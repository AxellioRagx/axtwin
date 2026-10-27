"""Discovery sources (`api/Spec.md` A7): a saved way to REACH a sensor's query API, and
the session that opens it for one probe or one run.

A source is `{name, plugin, endpoint, access, secrets, defaults}`:

  * `endpoint`  -- the plugin's base URL **as seen from wherever the access path lands**:
                   `http://127.0.0.1:9090` for an axquery on an appliance's loopback that
                   an SSH tunnel reaches; `https://sensor.example:9090` for a direct one.
  * `access`    -- `transport` (`direct` | `ssh_tunnel`) and `auth` (`none` | `basic` |
                   `api_key`), plus the SSH login for a tunnel: host, port, user and the
                   PATH of a private key on this machine. Never key material.
  * `secrets`   -- the password or the API key. Stored in the store file (mode 0600,
                   outside the checkout), **never returned by a read** -- a record says
                   `has_password` / `has_api_key` and nothing more -- and never placed in
                   `argv`: they reach the one `aximprint` subprocess through its
                   environment (`AXIMPRINT_SOURCE_USER` / `_PASSWORD` / `_API_KEY`).
  * `defaults`  -- run options the console pre-fills (`network`, `vantage`, `window`,
                   `days`, `sensors`); a run request may override any of them.

The store is one SQLite file, `AXRANGE_DISCOVERY_DB` (default `~/.axrange/discovery.db`,
beside the planner state DB -- live operator data lives with the deployment DB, never in
the checkout). It is NOT a table in `state.db`: the planner's schema and migrations are
its own, and a source outlives any deploy.

`open_access()` is the session: for `ssh_tunnel` it starts `ssh -N -L` to the endpoint's
host:port through the SSH host, waits for the local forward to accept, and hands back the
rewritten base URL plus the credential environment; for `direct` it hands back the
endpoint as is. The tunnel lives exactly as long as the `with` block -- the whole chain
for a run, one `/api/info` for a probe.
"""
from __future__ import annotations

import contextlib
import json
import os
import secrets as _secrets
import socket
import sqlite3
import subprocess
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

SETTING_DISCOVERY_DB = "AXRANGE_DISCOVERY_DB"
SETTING_DISCOVERY_TIMEOUT = "AXRANGE_DISCOVERY_TIMEOUT_S"
#: Seconds one stage of a source-driven run may take. The ingest of a multi-day window
#: over a tunnel is HOURS, so `recompile`'s 120 s (sized for one cast) cannot apply here.
DEFAULT_DISCOVERY_TIMEOUT_S = 6 * 3600.0

TRANSPORTS = ("direct", "ssh_tunnel")
AUTHS = ("none", "basic", "api_key")
SECRET_KEYS = ("password", "api_key")

#: The environment names the aximprint HTTP transport reads
#: (`aximprint.ingest.axquery.sources`). Spelled here too because the engine never
#: imports `aximprint`; `engine/tests/test_api_discovery_sources.py` pins the pair.
ENV_USER = "AXIMPRINT_SOURCE_USER"
ENV_PASSWORD = "AXIMPRINT_SOURCE_PASSWORD"
ENV_API_KEY = "AXIMPRINT_SOURCE_API_KEY"


def default_discovery_db_path() -> str:
    return os.environ.get(SETTING_DISCOVERY_DB) or str(Path.home() / ".axrange" / "discovery.db")


class SourceError(ValueError):
    """A source document is malformed; the message names the field and the accepted set."""


class SourceNotFound(KeyError):
    pass


class SourceConflict(Exception):
    """A name already in use, or a record something else still depends on."""


class AccessError(Exception):
    """The access path could not be opened (tunnel refused, key missing, ...)."""


# ---------------------------------------------------------------------------
# the shapes
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SshSpec:
    host: str
    user: str
    key_path: str
    port: int = 22

    def to_json(self) -> dict[str, Any]:
        return {"host": self.host, "user": self.user, "key_path": self.key_path, "port": self.port}


@dataclass(frozen=True)
class Access:
    transport: str = "direct"
    auth: str = "none"
    user: Optional[str] = None
    ssh: Optional[SshSpec] = None

    def to_json(self) -> dict[str, Any]:
        return {
            "transport": self.transport, "auth": self.auth, "user": self.user,
            "ssh": self.ssh.to_json() if self.ssh else None,
        }


def _port(value: Any, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not (1 <= value <= 65535):
        raise SourceError(f"{what} must be an integer port 1..65535, got {value!r}")
    return value


def _text(value: Any, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SourceError(f"{what} must be a non-empty string")
    return value.strip()


def validate_endpoint(url: Any) -> str:
    """An ABSOLUTE http(s) base URL with a host -- the same refusal the axquery locator
    makes, so a bare `host:port` is refused here, at save time, not at run time."""
    text = _text(url, "endpoint").rstrip("/")
    parsed = urllib.parse.urlparse(text)
    if parsed.scheme not in ("http", "https") or not parsed.netloc or not parsed.hostname:
        raise SourceError(
            f"endpoint must be an absolute http(s) base URL (e.g. 'http://127.0.0.1:9090'), got {text!r}"
        )
    if parsed.query or parsed.fragment:
        raise SourceError("endpoint must not carry a query string or fragment")
    return text


def validate_access(obj: Any) -> Access:
    if obj is None:
        return Access()
    if not isinstance(obj, dict):
        raise SourceError("access must be an object")
    transport = obj.get("transport") or "direct"
    auth = obj.get("auth") or "none"
    if transport not in TRANSPORTS:
        raise SourceError(f"access.transport must be one of {list(TRANSPORTS)}, got {transport!r}")
    if auth not in AUTHS:
        raise SourceError(f"access.auth must be one of {list(AUTHS)}, got {auth!r}")
    user = obj.get("user")
    if auth == "basic":
        user = _text(user, "access.user (required for basic auth)")
    elif user not in (None, ""):
        raise SourceError("access.user is only used with basic auth")
    else:
        user = None
    ssh: Optional[SshSpec] = None
    if transport == "ssh_tunnel":
        raw = obj.get("ssh")
        if not isinstance(raw, dict):
            raise SourceError("access.ssh {host, user, key_path, port?} is required for an ssh_tunnel")
        ssh = SshSpec(
            host=_text(raw.get("host"), "access.ssh.host"),
            user=_text(raw.get("user"), "access.ssh.user"),
            key_path=_text(raw.get("key_path"), "access.ssh.key_path"),
            port=_port(raw.get("port", 22), "access.ssh.port"),
        )
    elif obj.get("ssh") not in (None, {}):
        raise SourceError("access.ssh is only used with the ssh_tunnel transport")
    return Access(transport=transport, auth=auth, user=user, ssh=ssh)


def validate_defaults(obj: Any) -> dict[str, Any]:
    if obj is None:
        return {}
    if not isinstance(obj, dict):
        raise SourceError("defaults must be an object")
    out: dict[str, Any] = {}
    for key, value in obj.items():
        if value in (None, ""):
            continue
        if key in ("network", "vantage"):
            out[key] = _text(value, f"defaults.{key}")
        elif key in ("window", "subbuckets"):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise SourceError(f"defaults.{key} must be a positive integer")
            out[key] = value
        elif key == "days":
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
                raise SourceError("defaults.days must be a positive number")
            out[key] = value
        elif key == "sensors":
            if not isinstance(value, list) or not all(isinstance(s, str) and s.strip() for s in value):
                raise SourceError("defaults.sensors must be a list of non-empty names")
            out[key] = [s.strip() for s in value]
        else:
            raise SourceError(
                f"unknown defaults key {key!r} (allowed: days, network, vantage, window, subbuckets, sensors)"
            )
    return out


def secrets_for(access: Access, secrets: dict[str, Any]) -> dict[str, str]:
    """Keep only the secret the chosen auth mode uses; refuse a missing one by name."""
    out: dict[str, str] = {}
    if access.auth == "basic":
        pw = secrets.get("password")
        if not isinstance(pw, str) or not pw:
            raise SourceError("basic auth needs a password")
        out["password"] = pw
    elif access.auth == "api_key":
        key = secrets.get("api_key")
        if not isinstance(key, str) or not key:
            raise SourceError("api_key auth needs an api_key")
        out["api_key"] = key
    return out


@dataclass
class SourceRecord:
    id: str
    name: str
    plugin: str
    endpoint: str
    access: Access
    defaults: dict[str, Any]
    has_password: bool
    has_api_key: bool
    created_at: float
    updated_at: float
    last_probe: Optional[dict[str, Any]] = None


# ---------------------------------------------------------------------------
# the store
# ---------------------------------------------------------------------------
_SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  plugin TEXT NOT NULL,
  endpoint TEXT NOT NULL,
  access_json TEXT NOT NULL,
  secrets_json TEXT NOT NULL,
  defaults_json TEXT NOT NULL,
  last_probe_json TEXT,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
)
"""


class SourceStore:
    """One SQLite file, lock-guarded, shared by every request thread of one app."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        existed = self.path.exists()
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(self.path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._db:
            self._db.execute(_SCHEMA)
        if not existed:
            # Created by us: no one else's mode to respect.
            os.chmod(self.path, 0o600)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # -- reads --------------------------------------------------------------
    def list(self) -> list[SourceRecord]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM sources ORDER BY name").fetchall()
        return [self._record(r) for r in rows]

    def get(self, source_id: str) -> SourceRecord:
        with self._lock:
            row = self._db.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
        if row is None:
            raise SourceNotFound(source_id)
        return self._record(row)

    def secrets(self, source_id: str) -> dict[str, str]:
        """The one read that returns secret VALUES -- for `open_access`, never a DTO."""
        with self._lock:
            row = self._db.execute(
                "SELECT secrets_json FROM sources WHERE id = ?", (source_id,)
            ).fetchone()
        if row is None:
            raise SourceNotFound(source_id)
        return dict(json.loads(row["secrets_json"]))

    # -- writes -------------------------------------------------------------
    def create(self, *, name: str, plugin: str, endpoint: str, access: Access,
               secrets: dict[str, Any], defaults: dict[str, Any]) -> SourceRecord:
        name = _text(name, "name")
        kept = secrets_for(access, secrets)
        now = time.time()
        with self._lock:
            while True:
                source_id = f"src-{_secrets.token_hex(4)}"
                if self._db.execute("SELECT 1 FROM sources WHERE id = ?", (source_id,)).fetchone() is None:
                    break
            try:
                with self._db:
                    self._db.execute(
                        "INSERT INTO sources VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (source_id, name, plugin, endpoint, json.dumps(access.to_json()),
                         json.dumps(kept), json.dumps(defaults), None, now, now),
                    )
            except sqlite3.IntegrityError as e:
                raise SourceConflict(f"a discovery source named {name!r} already exists") from e
        return self.get(source_id)

    def update(self, source_id: str, *, name: Optional[str] = None, plugin: Optional[str] = None,
               endpoint: Optional[str] = None, access: Optional[Access] = None,
               secrets: Optional[dict[str, Any]] = None,
               defaults: Optional[dict[str, Any]] = None) -> SourceRecord:
        """Partial update. `secrets` merges: a key absent keeps the stored value, `None`
        or `""` clears it, a string replaces it -- then the result is filtered to what
        the (new) auth mode uses, so switching from basic to api_key drops the password."""
        current = self.get(source_id)
        with self._lock:
            row = self._db.execute(
                "SELECT secrets_json FROM sources WHERE id = ?", (source_id,)
            ).fetchone()
            stored = dict(json.loads(row["secrets_json"]))
        merged = dict(stored)
        for key, value in (secrets or {}).items():
            if key not in SECRET_KEYS:
                raise SourceError(f"unknown secret {key!r} (allowed: {', '.join(SECRET_KEYS)})")
            if value in (None, ""):
                merged.pop(key, None)
            else:
                merged[key] = value
        new_access = access if access is not None else current.access
        kept = secrets_for(new_access, merged)
        new_name = _text(name, "name") if name is not None else current.name
        now = time.time()
        with self._lock:
            try:
                with self._db:
                    self._db.execute(
                        "UPDATE sources SET name=?, plugin=?, endpoint=?, access_json=?, "
                        "secrets_json=?, defaults_json=?, updated_at=? WHERE id=?",
                        (new_name, plugin if plugin is not None else current.plugin,
                         endpoint if endpoint is not None else current.endpoint,
                         json.dumps(new_access.to_json()), json.dumps(kept),
                         json.dumps(defaults if defaults is not None else current.defaults),
                         now, source_id),
                    )
            except sqlite3.IntegrityError as e:
                raise SourceConflict(f"a discovery source named {new_name!r} already exists") from e
        return self.get(source_id)

    def delete(self, source_id: str) -> None:
        with self._lock, self._db:
            cur = self._db.execute("DELETE FROM sources WHERE id = ?", (source_id,))
        if cur.rowcount == 0:
            raise SourceNotFound(source_id)

    def record_probe(self, source_id: str, probe: dict[str, Any]) -> None:
        with self._lock, self._db:
            cur = self._db.execute(
                "UPDATE sources SET last_probe_json = ? WHERE id = ?",
                (json.dumps(probe), source_id),
            )
        if cur.rowcount == 0:
            raise SourceNotFound(source_id)

    # -- row -> record ------------------------------------------------------
    @staticmethod
    def _record(row: sqlite3.Row) -> SourceRecord:
        secrets = json.loads(row["secrets_json"])
        access = validate_access(json.loads(row["access_json"]))
        return SourceRecord(
            id=row["id"], name=row["name"], plugin=row["plugin"], endpoint=row["endpoint"],
            access=access, defaults=dict(json.loads(row["defaults_json"])),
            has_password=bool(secrets.get("password")), has_api_key=bool(secrets.get("api_key")),
            created_at=float(row["created_at"]), updated_at=float(row["updated_at"]),
            last_probe=json.loads(row["last_probe_json"]) if row["last_probe_json"] else None,
        )


# ---------------------------------------------------------------------------
# opening the access path
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class AccessSpec:
    endpoint: str
    access: Access
    secrets: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class OpenedAccess:
    #: The base URL the plugin should talk to from THIS machine.
    base_url: str
    #: Extra environment for the aximprint subprocess (the credential, if any).
    env: dict[str, str]
    #: Human line for the run record: "direct" or "ssh user@host:port -> 127.0.0.1:NNN".
    via: str


def env_for(access: Access, secrets: dict[str, str]) -> dict[str, str]:
    if access.auth == "basic":
        return {ENV_USER: access.user or "", ENV_PASSWORD: secrets.get("password", "")}
    if access.auth == "api_key":
        return {ENV_API_KEY: secrets.get("api_key", "")}
    return {}


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def endpoint_target(endpoint: str) -> tuple[str, int]:
    """host, port of the endpoint -- what the tunnel forwards TO, on the SSH host's side."""
    parsed = urllib.parse.urlparse(endpoint)
    host = parsed.hostname or ""
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return host, port


def rewrite_endpoint(endpoint: str, local_port: int) -> str:
    parsed = urllib.parse.urlparse(endpoint)
    return urllib.parse.urlunparse(parsed._replace(netloc=f"127.0.0.1:{local_port}"))


class SshTunnel:
    """`ssh -N -L 127.0.0.1:<free>:<host>:<port> user@sshhost`, held open as a context.

    BatchMode: a key that needs a passphrase or a host not in `known_hosts` FAILS (with
    ssh's own words) rather than hanging on a prompt nobody can answer. The forward
    failing is fatal (`ExitOnForwardFailure`), and keep-alives let a long ingest survive
    a quiet link.
    """

    def __init__(self, ssh: SshSpec, target: tuple[str, int], *, connect_timeout_s: float = 20.0) -> None:
        self.ssh = ssh
        self.target = target
        self.connect_timeout_s = connect_timeout_s
        self.local_port: Optional[int] = None
        self._proc: Optional[subprocess.Popen[str]] = None

    def argv(self, local_port: int) -> list[str]:
        host, port = self.target
        return [
            "ssh", "-N",
            "-o", "BatchMode=yes",
            "-o", "ExitOnForwardFailure=yes",
            "-o", "ServerAliveInterval=30",
            "-o", "ServerAliveCountMax=3",
            "-o", f"ConnectTimeout={int(self.connect_timeout_s)}",
            "-p", str(self.ssh.port),
            "-i", self.ssh.key_path,
            "-L", f"127.0.0.1:{local_port}:{host}:{port}",
            f"{self.ssh.user}@{self.ssh.host}",
        ]

    def __enter__(self) -> "SshTunnel":
        key = Path(self.ssh.key_path).expanduser()
        if not key.is_file():
            raise AccessError(f"ssh key {self.ssh.key_path!r} is not a file on this machine")
        self.local_port = free_port()
        try:
            self._proc = subprocess.Popen(  # noqa: S603 -- argv list, no shell
                self.argv(self.local_port), stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
            )
        except OSError as e:
            raise AccessError(f"cannot start ssh: {e}") from e
        deadline = time.monotonic() + self.connect_timeout_s + 5.0
        while time.monotonic() < deadline:
            if self._proc.poll() is not None:
                err = (self._proc.stderr.read() if self._proc.stderr else "").strip()
                raise AccessError(
                    f"ssh tunnel to {self.ssh.user}@{self.ssh.host}:{self.ssh.port} exited "
                    f"({self._proc.returncode}) before the forward came up: {err or '<no stderr>'}"
                )
            with contextlib.suppress(OSError):
                with socket.create_connection(("127.0.0.1", self.local_port), timeout=0.5):
                    return self
            time.sleep(0.2)
        self.__exit__(None, None, None)
        raise AccessError(
            f"ssh tunnel to {self.ssh.user}@{self.ssh.host}:{self.ssh.port} did not accept on "
            f"127.0.0.1:{self.local_port} within {self.connect_timeout_s:g}s"
        )

    def __exit__(self, *exc: Any) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        if proc.stderr:
            proc.stderr.close()


#: `tunnel_factory(ssh_spec, (host, port))` -> a context manager exposing `local_port`
#: once entered. Tests inject one that forwards nowhere; the default is `SshTunnel`.
TunnelFactory = Callable[[SshSpec, tuple[str, int]], Any]


@contextlib.contextmanager
def open_access(spec: AccessSpec, *, tunnel_factory: Optional[TunnelFactory] = None) -> Iterator[OpenedAccess]:
    env = env_for(spec.access, spec.secrets)
    if spec.access.transport == "direct":
        yield OpenedAccess(base_url=spec.endpoint, env=env, via="direct")
        return
    assert spec.access.ssh is not None  # validate_access guarantees it
    factory = tunnel_factory or SshTunnel
    target = endpoint_target(spec.endpoint)
    with factory(spec.access.ssh, target) as tunnel:
        port = int(tunnel.local_port)
        yield OpenedAccess(
            base_url=rewrite_endpoint(spec.endpoint, port), env=env,
            via=f"ssh {spec.access.ssh.user}@{spec.access.ssh.host}:{spec.access.ssh.port} "
                f"-> {target[0]}:{target[1]} via 127.0.0.1:{port}",
        )


def window_from_probe(probe: dict[str, Any], days: float) -> tuple[float, float]:
    """`[last_ts + 1 - days*86400, last_ts + 1)`, clamped to the source's own span.
    `last_ts` is inclusive on the sensor side (`aximprint`'s axquery plugin says so), so
    the half-open upper bound is one past it."""
    last = probe.get("last_ts")
    first = probe.get("first_ts")
    if not isinstance(last, (int, float)):
        raise AccessError("the source reported no last_ts, so 'the last N days' has no anchor; "
                          "pass ts_from/ts_to instead")
    ts_to = float(last) + 1.0
    ts_from = ts_to - float(days) * 86400.0
    if isinstance(first, (int, float)) and ts_from < float(first):
        ts_from = float(first)
    return ts_from, ts_to
