"""Discovery sources (`api/Spec.md` A7): `/api/discovery/sources[...]`, the probe, and a
source-driven run.

Everything runs against a fake backend and a fake tunnel: the suite never runs
`aximprint` or `ssh`. The store is a temp SQLite file, so the one thing that IS real
here is persistence: a saved source survives a second `create_app` over the same path,
and a secret never appears in any read.
"""
from __future__ import annotations

import json
import os
import sqlite3
import stat
from pathlib import Path

import pytest

from axrange.api import ApiSettings, create_app
from axrange.api import discovery as disc
from axrange.api import discovery_sources as ds

try:
    from starlette.testclient import TestClient
except ImportError:  # pragma: no cover
    pytest.skip("starlette not installed", allow_module_level=True)

FIXT_IR = Path(__file__).parent / "fixtures" / "ir"
FIXT_ROOT = Path(__file__).parent / "fixtures"

HTTP_SCHEMA = {
    "type": "object",
    "properties": {
        "source": {"type": "string", "description": "an axquery instance"},
        "ts_from": {"type": "number"}, "ts_to": {"type": "number"},
        "vantage": {"type": "string"}, "network": {"type": "string"},
        "window": {"type": "integer"}, "subbuckets": {"type": "integer"},
    },
    "required": ["source"],
    "additionalProperties": False,
}
PATH_SCHEMA = {**HTTP_SCHEMA, "properties": {"source": {"type": "string"}}}

SPAN = {"first_ts": 1788723036.0, "last_ts": 1791315036.0}


def _settings(tmp_path: Path, **over) -> ApiSettings:
    (tmp_path / "imprints").mkdir(exist_ok=True)
    (tmp_path / "disco").mkdir(exist_ok=True)
    kw = dict(
        db_path=str(tmp_path / "state.db"),
        scenarios_dir=str(FIXT_IR.resolve()),
        images_root=str(FIXT_ROOT.resolve()),
        adapter_config="/nonexistent.conf",
        aximprint_bin="/usr/bin/false-aximprint",
        aximprint_store=str(tmp_path / "disco" / "ir.duckdb"),
        imprints_dir=str(tmp_path / "imprints"),
        discovery_db=str(tmp_path / "axrange-home" / "discovery.db"),
    )
    kw.update(over)
    return ApiSettings(**kw)


class _FakeDiscovery:
    def __init__(self, *, probe=None, run=None):
        self.plugins = [
            {"name": "axquery", "source_kind": "http", "config_schema": HTTP_SCHEMA},
            {"name": "zeek", "source_kind": "path", "config_schema": PATH_SCHEMA},
        ]
        self.probes: list[tuple[str, str, dict]] = []
        self.requests: list[disc.DiscoveryRunRequest] = []
        self._probe = probe
        self._run = run
        # The imprints the fake IR store holds: `{pit_id: name}`.
        self.pits: dict[str, str] = {}

    def list_plugins(self):
        return list(self.plugins)

    def list_pits(self, store):
        return [{"pit_id": p, "name": n} for p, n in self.pits.items()]

    def probe(self, plugin, source, *, env):
        self.probes.append((plugin, source, dict(env)))
        if self._probe is not None:
            return self._probe(plugin, source, env)
        return {"supported": True, "reachable": True, "error": None, **SPAN,
                "vantage": "axellio-hq/corp-apcn-rdr1:12", "extra": {"chunks": 69}}

    def run(self, request, *, on_stage, on_progress=None, handle=None):
        self.requests.append(request)
        if self._run is not None:
            return self._run(request, on_stage)
        for s in disc.STAGES:
            on_stage(s)
        return disc.DiscoveryRunResult(pit_id="pit-fake", stages=list(disc.STAGES))


class _FakeTunnel:
    """Forwards nowhere; records that it was opened and closed, in order."""

    log: list[str] = []

    def __init__(self, ssh, target):
        self.ssh, self.target = ssh, target
        self.local_port = 45678

    def __enter__(self):
        _FakeTunnel.log.append(f"open {self.ssh.user}@{self.ssh.host}:{self.ssh.port} -> {self.target}")
        return self

    def __exit__(self, *exc):
        _FakeTunnel.log.append("close")


def _client(settings, *, backend=None, background="sync", tunnel=_FakeTunnel):
    app = create_app(settings)
    app.state.discovery_backend = backend
    app.state.discovery_tunnel = tunnel
    if background == "sync":
        app.state.discovery_background = lambda job: job()
    elif background is not None:
        app.state.discovery_background = background
    return TestClient(app, raise_server_exceptions=False)


def _tunnel_source(**over):
    body = {
        "name": "corp-apcn-reader", "plugin": "axquery", "endpoint": "http://127.0.0.1:9090/",
        "access": {"transport": "ssh_tunnel", "auth": "none",
                   "ssh": {"host": "reader.example", "user": "fxadmin", "key_path": "~/.ssh/id_ed25519"}},
        "defaults": {"days": 10, "network": "corp"},
    }
    body.update(over)
    return body


@pytest.fixture(autouse=True)
def _clear_tunnel_log():
    _FakeTunnel.log = []
    yield


# ---------------------------------------------------------------------------
# A7.1 -- create / read / update / delete, no secret ever read back
# ---------------------------------------------------------------------------
def test_create_lists_and_reads_back_without_the_secret(tmp_path):
    c = _client(_settings(tmp_path), backend=_FakeDiscovery())
    r = c.post("/api/discovery/sources", json=_tunnel_source(
        access={"transport": "direct", "auth": "basic", "user": "ro"}, password="s3cret",
        endpoint="https://sensor.example:9443"))
    assert r.status_code == 201, r.text
    doc = r.json()
    assert doc["id"].startswith("src-") and doc["name"] == "corp-apcn-reader"
    assert doc["endpoint"] == "https://sensor.example:9443"  # trailing slash trimmed
    assert doc["access"] == {"transport": "direct", "auth": "basic", "user": "ro", "ssh": None, "tls_verify": True}
    assert doc["has_password"] is True and doc["has_api_key"] is False
    assert "s3cret" not in r.text
    listed = c.get("/api/discovery/sources").json()["sources"]
    assert [s["id"] for s in listed] == [doc["id"]]
    assert "s3cret" not in json.dumps(listed)
    assert c.get(f"/api/discovery/sources/{doc['id']}").json() == doc


def test_the_store_is_a_0600_file_outside_the_checkout_and_persists(tmp_path):
    settings = _settings(tmp_path)
    c = _client(settings, backend=_FakeDiscovery())
    sid = c.post("/api/discovery/sources", json=_tunnel_source()).json()["id"]
    path = Path(settings.discovery_db)
    assert path.is_file()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    # A second app over the same file sees the record.
    c2 = _client(settings, backend=_FakeDiscovery())
    assert c2.get(f"/api/discovery/sources/{sid}").status_code == 200


def test_the_secret_is_in_the_file_but_only_for_the_runner(tmp_path):
    settings = _settings(tmp_path)
    c = _client(settings, backend=_FakeDiscovery())
    sid = c.post("/api/discovery/sources", json=_tunnel_source(
        access={"transport": "direct", "auth": "api_key"}, api_key="K-1")).json()["id"]
    store = ds.SourceStore(settings.discovery_db)
    assert store.secrets(sid) == {"api_key": "K-1"}
    assert store.get(sid).has_api_key is True


@pytest.mark.parametrize("over, needle", [
    ({"plugin": "nope"}, "unknown discovery plugin"),
    ({"plugin": "zeek"}, "reads a path, not an endpoint"),
    ({"endpoint": "10.0.0.1:9090"}, "absolute http(s) base URL"),
    ({"access": {"transport": "carrier-pigeon"}}, "access.transport must be one of"),
    ({"access": {"transport": "direct", "auth": "basic"}}, "access.user (required for basic auth)"),
    ({"access": {"transport": "direct", "auth": "basic", "user": "ro"}}, "basic auth needs a password"),
    ({"access": {"transport": "direct", "auth": "api_key"}}, "api_key auth needs an api_key"),
    ({"access": {"transport": "ssh_tunnel", "auth": "none"}}, "access.ssh {host, user, key_path, port?} is required"),
    ({"access": {"transport": "ssh_tunnel", "auth": "none",
                 "ssh": {"host": "h", "user": "u", "key_path": "k", "port": 70000}}}, "access.ssh.port must be an integer port"),
    ({"defaults": {"days": -1}}, "defaults.days must be a positive number"),
    ({"defaults": {"colour": "blue"}}, "unknown defaults key 'colour'"),
])
def test_a_malformed_source_is_refused_by_name_before_anything_is_written(tmp_path, over, needle):
    c = _client(_settings(tmp_path), backend=_FakeDiscovery())
    r = c.post("/api/discovery/sources", json=_tunnel_source(**over))
    assert r.status_code == 400, r.text
    assert needle in r.text
    assert c.get("/api/discovery/sources").json() == {"sources": []}


def test_a_duplicate_name_is_a_409(tmp_path):
    c = _client(_settings(tmp_path), backend=_FakeDiscovery())
    assert c.post("/api/discovery/sources", json=_tunnel_source()).status_code == 201
    r = c.post("/api/discovery/sources", json=_tunnel_source())
    assert r.status_code == 409 and "already exists" in r.text


def test_update_keeps_an_unsent_secret_clears_an_empty_one_and_drops_the_unused_one(tmp_path):
    settings = _settings(tmp_path)
    c = _client(settings, backend=_FakeDiscovery())
    sid = c.post("/api/discovery/sources", json=_tunnel_source(
        access={"transport": "direct", "auth": "basic", "user": "ro"}, password="pw1")).json()["id"]
    store = ds.SourceStore(settings.discovery_db)
    # Rename only: the password stays.
    r = c.put(f"/api/discovery/sources/{sid}", json={"name": "renamed"})
    assert r.status_code == 200 and r.json()["name"] == "renamed" and r.json()["has_password"]
    assert store.secrets(sid) == {"password": "pw1"}
    # Switch to api_key: the password is dropped, the key kept.
    r = c.put(f"/api/discovery/sources/{sid}", json={
        "access": {"transport": "direct", "auth": "api_key"}, "api_key": "K"})
    assert r.status_code == 200, r.text
    assert r.json()["has_password"] is False and r.json()["has_api_key"] is True
    assert store.secrets(sid) == {"api_key": "K"}
    # Clearing the key under api_key auth is refused: the mode needs one.
    r = c.put(f"/api/discovery/sources/{sid}", json={"api_key": ""})
    assert r.status_code == 400 and "api_key auth needs an api_key" in r.text
    # Switching to none and clearing is fine.
    r = c.put(f"/api/discovery/sources/{sid}", json={
        "access": {"transport": "direct", "auth": "none"}, "api_key": ""})
    assert r.status_code == 200 and r.json()["has_api_key"] is False
    assert store.secrets(sid) == {}


def test_delete_removes_and_404s_after(tmp_path):
    c = _client(_settings(tmp_path), backend=_FakeDiscovery())
    sid = c.post("/api/discovery/sources", json=_tunnel_source()).json()["id"]
    assert c.delete(f"/api/discovery/sources/{sid}").status_code == 204
    assert c.get(f"/api/discovery/sources/{sid}").status_code == 404
    assert c.delete(f"/api/discovery/sources/{sid}").status_code == 404
    assert c.put(f"/api/discovery/sources/{sid}", json={"name": "x"}).status_code == 404


def test_writes_carry_the_token_reads_do_not(tmp_path):
    c = _client(_settings(tmp_path, api_token="T"), backend=_FakeDiscovery())
    assert c.post("/api/discovery/sources", json=_tunnel_source()).status_code == 401
    assert c.get("/api/discovery/sources").status_code == 200
    h = {"Authorization": "Bearer T"}
    sid = c.post("/api/discovery/sources", json=_tunnel_source(), headers=h).json()["id"]
    assert c.post(f"/api/discovery/sources/{sid}/probe").status_code == 401
    assert c.post(f"/api/discovery/sources/{sid}/runs", json={"imprint_id": "x"}).status_code == 401
    assert c.delete(f"/api/discovery/sources/{sid}").status_code == 401


def test_no_sources_is_an_empty_list_and_no_file_is_created_by_a_read(tmp_path):
    settings = _settings(tmp_path)
    c = _client(settings, backend=_FakeDiscovery())
    assert c.get("/api/discovery/sources").json() == {"sources": []}
    # The store file now exists (opened on first use) but holds nothing.
    assert sqlite3.connect(settings.discovery_db).execute("SELECT count(*) FROM sources").fetchone()[0] == 0


# ---------------------------------------------------------------------------
# A7.2 -- the probe
# ---------------------------------------------------------------------------
def test_probe_opens_the_tunnel_rewrites_the_endpoint_and_remembers_the_answer(tmp_path):
    be = _FakeDiscovery()
    c = _client(_settings(tmp_path), backend=be)
    sid = c.post("/api/discovery/sources", json=_tunnel_source()).json()["id"]
    r = c.post(f"/api/discovery/sources/{sid}/probe")
    assert r.status_code == 200, r.text
    doc = r.json()
    assert doc["reachable"] is True and doc["error"] is None
    assert (doc["first_ts"], doc["last_ts"]) == (SPAN["first_ts"], SPAN["last_ts"])
    assert doc["vantage"] == "axellio-hq/corp-apcn-rdr1:12" and doc["extra"] == {"chunks": 69}
    assert doc["via"] == "ssh fxadmin@reader.example:22 -> 127.0.0.1:9090 via 127.0.0.1:45678"
    # The plugin was asked through the tunnel's local end, with no credential env.
    assert be.probes == [("axquery", "axquery:http://127.0.0.1:45678", {})]
    assert _FakeTunnel.log == ["open fxadmin@reader.example:22 -> ('127.0.0.1', 9090)", "close"]
    # Remembered on the record.
    rec = c.get(f"/api/discovery/sources/{sid}").json()
    assert rec["last_probe"]["reachable"] is True and rec["last_probe"]["probed_at"] == doc["probed_at"]


def test_probe_direct_with_basic_auth_passes_the_credential_in_env_only(tmp_path):
    be = _FakeDiscovery()
    c = _client(_settings(tmp_path), backend=be)
    sid = c.post("/api/discovery/sources", json=_tunnel_source(
        endpoint="https://sensor.example:9443",
        access={"transport": "direct", "auth": "basic", "user": "ro"}, password="pw")).json()["id"]
    r = c.post(f"/api/discovery/sources/{sid}/probe")
    assert r.status_code == 200 and r.json()["via"] == "direct"
    assert be.probes == [("axquery", "axquery:https://sensor.example:9443",
                          {ds.ENV_USER: "ro", ds.ENV_PASSWORD: "pw"})]
    assert "pw" not in r.text and _FakeTunnel.log == []


def test_probe_reports_a_dead_tunnel_as_unreachable_with_sshs_reason(tmp_path):
    class _Dead(_FakeTunnel):
        def __enter__(self):
            raise ds.AccessError("ssh tunnel to fxadmin@reader.example:22 exited (255): Permission denied")

    be = _FakeDiscovery()
    c = _client(_settings(tmp_path), backend=be, tunnel=_Dead)
    sid = c.post("/api/discovery/sources", json=_tunnel_source()).json()["id"]
    r = c.post(f"/api/discovery/sources/{sid}/probe")
    assert r.status_code == 200
    assert r.json()["reachable"] is False and "Permission denied" in r.json()["error"]
    assert be.probes == []


def test_probe_passes_the_plugins_own_unreachable_answer_through(tmp_path):
    be = _FakeDiscovery(probe=lambda p, s, e: {
        "supported": True, "reachable": False, "error": "axquery unreachable at http://127.0.0.1:45678: refused",
        "first_ts": None, "last_ts": None, "vantage": None, "extra": {}})
    c = _client(_settings(tmp_path), backend=be)
    sid = c.post("/api/discovery/sources", json=_tunnel_source()).json()["id"]
    doc = c.post(f"/api/discovery/sources/{sid}/probe").json()
    assert doc["reachable"] is False and "refused" in doc["error"]
    assert _FakeTunnel.log == ["open fxadmin@reader.example:22 -> ('127.0.0.1', 9090)", "close"]


def test_probe_of_an_unknown_source_is_404_and_unconfigured_discovery_is_503(tmp_path):
    c = _client(_settings(tmp_path), backend=_FakeDiscovery())
    assert c.post("/api/discovery/sources/src-nope/probe").status_code == 404
    c2 = _client(_settings(tmp_path, aximprint_bin=""), backend=None)
    # The store is real; the backend is the real (unconfigured) one -> 503 naming the key.
    sid = None
    r = c2.get("/api/discovery/sources")
    assert r.status_code == 200
    r = c2.post("/api/discovery/sources", json=_tunnel_source())
    assert r.status_code == 503 and disc.SETTING_BIN in r.text
    assert sid is None


# ---------------------------------------------------------------------------
# A7.3 -- a source-driven run
# ---------------------------------------------------------------------------
def test_run_last_n_days_resolves_bounds_from_the_span_and_runs_the_chain_over_the_tunnel(tmp_path):
    be = _FakeDiscovery()
    settings = _settings(tmp_path)
    c = _client(settings, backend=be)
    sid = c.post("/api/discovery/sources", json=_tunnel_source()).json()["id"]
    r = c.post(f"/api/discovery/sources/{sid}/runs", json={"imprint_id": "corp-10d", "days": 10})
    assert r.status_code == 202, r.text
    run = r.json()
    assert run["status"] == "completed" and run["source_id"] == sid
    assert run["stages_done"] == list(disc.STAGES) and run["pit_id"] == "pit-fake"
    assert run["ts_to"] == SPAN["last_ts"] + 1.0
    assert run["ts_from"] == run["ts_to"] - 10 * 86400
    assert run["via"].startswith("ssh fxadmin@reader.example:22")
    assert run["started_at"] and run["finished_at"]
    # The chain saw the tunnel's local end, the defaults, and the bounds; the tunnel
    # outlived the whole chain.
    req = be.requests[0]
    # The source's legacy `network: corp` is ignored: a new imprint's network id is
    # derived from the run's own id.
    assert req.config == {"source": "axquery:http://127.0.0.1:45678",
                          "network": "net-" + run["id"].split("-", 1)[1],
                          "ts_from": run["ts_from"], "ts_to": run["ts_to"]}
    assert req.env == {} and req.sensors == []
    assert req.belief_store == Path(settings.aximprint_store).parent / "corp-10d.belief.duckdb"
    assert _FakeTunnel.log == ["open fxadmin@reader.example:22 -> ('127.0.0.1', 9090)", "close"]
    assert be.probes[0][1] == "axquery:http://127.0.0.1:45678"
    # Listed, newest first, and readable by id.
    assert [x["id"] for x in c.get("/api/discovery/runs").json()["runs"]] == [run["id"]]
    assert c.get(f"/api/discovery/runs/{run['id']}").json()["status"] == "completed"


def test_run_explicit_bounds_skip_the_probe_and_override_the_defaults(tmp_path):
    be = _FakeDiscovery()
    c = _client(_settings(tmp_path), backend=be)
    sid = c.post("/api/discovery/sources", json=_tunnel_source(
        access={"transport": "direct", "auth": "api_key"}, api_key="K",
        endpoint="http://sensor.example:9090",
        defaults={"days": 10, "network": "corp", "window": 3600, "sensors": ["s1"]})).json()["id"]
    r = c.post(f"/api/discovery/sources/{sid}/runs", json={
        "imprint_id": "corp-slice", "ts_from": 100.0, "ts_to": 200.0, "network": "lab",
        "sensors": [], "vantage": "tap-1"})
    assert r.status_code == 202, r.text
    assert r.json()["status"] == "completed" and r.json()["via"] == "direct"
    assert be.probes == []
    req = be.requests[0]
    # network/vantage in the request are deprecated and ignored for source runs.
    assert req.config == {"source": "axquery:http://sensor.example:9090",
                          "network": "net-" + r.json()["id"].split("-", 1)[1],
                          "window": 3600, "ts_from": 100.0, "ts_to": 200.0}
    assert req.env == {ds.ENV_API_KEY: "K"} and req.sensors == []


def test_days_is_clamped_to_the_sources_first_ts(tmp_path):
    be = _FakeDiscovery()
    c = _client(_settings(tmp_path), backend=be)
    sid = c.post("/api/discovery/sources", json=_tunnel_source()).json()["id"]
    run = c.post(f"/api/discovery/sources/{sid}/runs", json={"imprint_id": "all", "days": 365}).json()
    assert run["ts_from"] == SPAN["first_ts"] and run["ts_to"] == SPAN["last_ts"] + 1.0


@pytest.mark.parametrize("body, needle", [
    ({"imprint_id": "x", "days": 5, "ts_from": 1.0, "ts_to": 2.0}, "not both"),
    ({"imprint_id": "x", "ts_from": 5.0, "ts_to": 2.0}, "ts_to > ts_from"),
    ({"imprint_id": "x", "ts_from": 1.0}, "both be set"),
    ({"imprint_id": "../x", "days": 1}, "not a usable imprint name"),
])
def test_a_bad_run_request_is_refused_before_the_tunnel_opens(tmp_path, body, needle):
    be = _FakeDiscovery()
    c = _client(_settings(tmp_path), backend=be)
    sid = c.post("/api/discovery/sources", json=_tunnel_source()).json()["id"]
    r = c.post(f"/api/discovery/sources/{sid}/runs", json=body)
    assert r.status_code == 400, r.text
    assert needle in r.text
    assert _FakeTunnel.log == [] and be.requests == []


def test_no_days_anywhere_is_refused(tmp_path):
    c = _client(_settings(tmp_path), backend=_FakeDiscovery())
    sid = c.post("/api/discovery/sources", json=_tunnel_source(defaults={})).json()["id"]
    r = c.post(f"/api/discovery/sources/{sid}/runs", json={"imprint_id": "x"})
    assert r.status_code == 400 and "no default days" in r.text


def test_an_existing_imprint_or_active_run_is_a_409(tmp_path):
    settings = _settings(tmp_path)
    be = _FakeDiscovery()
    be.pits["pit-1"] = "taken"
    started: list = []
    c = _client(settings, backend=be, background=started.append)
    sid = c.post("/api/discovery/sources", json=_tunnel_source()).json()["id"]
    assert c.post(f"/api/discovery/sources/{sid}/runs", json={"imprint_id": "taken", "days": 1}).status_code == 409
    assert c.post(f"/api/discovery/sources/{sid}/runs", json={"imprint_id": "busy", "days": 1}).status_code == 202
    assert c.post(f"/api/discovery/sources/{sid}/runs", json={"imprint_id": "busy", "days": 1}).status_code == 409
    # A source with a run in progress cannot be deleted.
    r = c.delete(f"/api/discovery/sources/{sid}")
    assert r.status_code == 409 and "run in progress" in r.text
    assert len(started) == 1


def test_a_missing_ssh_key_is_refused_at_post_time(tmp_path):
    c = _client(_settings(tmp_path), backend=_FakeDiscovery())
    sid = c.post("/api/discovery/sources", json=_tunnel_source(
        access={"transport": "ssh_tunnel", "auth": "none",
                "ssh": {"host": "h", "user": "u", "key_path": str(tmp_path / "no-such-key")}})).json()["id"]
    r = c.post(f"/api/discovery/sources/{sid}/runs", json={"imprint_id": "x", "days": 1})
    assert r.status_code == 400 and "is not a file on the server" in r.text


def test_a_dead_tunnel_fails_the_run_on_the_record_and_leaves_no_bundle(tmp_path):
    class _Dead(_FakeTunnel):
        def __enter__(self):
            raise ds.AccessError("ssh tunnel exited (255): Connection timed out")

    be = _FakeDiscovery()
    settings = _settings(tmp_path)
    c = _client(settings, backend=be, tunnel=_Dead)
    sid = c.post("/api/discovery/sources", json=_tunnel_source()).json()["id"]
    run = c.post(f"/api/discovery/sources/{sid}/runs", json={"imprint_id": "dead", "days": 1}).json()
    assert run["status"] == "failed" and "Connection timed out" in run["error"]
    assert be.requests == [] and not (Path(settings.imprints_dir) / "dead").exists()


def test_an_unreachable_probe_fails_the_run_before_ingest(tmp_path):
    be = _FakeDiscovery(probe=lambda p, s, e: {"supported": True, "reachable": False,
                                              "error": "axquery unreachable: refused"})
    c = _client(_settings(tmp_path), backend=be)
    sid = c.post("/api/discovery/sources", json=_tunnel_source()).json()["id"]
    run = c.post(f"/api/discovery/sources/{sid}/runs", json={"imprint_id": "x", "days": 1}).json()
    assert run["status"] == "failed" and "source not reachable" in run["error"]
    assert be.requests == [] and _FakeTunnel.log[-1] == "close"


def test_a_failed_stage_closes_the_tunnel_and_lands_nothing(tmp_path):
    def boom(request, on_stage):
        on_stage("ingest")
        raise disc.DiscoveryError("bake failed (exit 1): no events")

    be = _FakeDiscovery(run=boom)
    settings = _settings(tmp_path)
    c = _client(settings, backend=be)
    sid = c.post("/api/discovery/sources", json=_tunnel_source()).json()["id"]
    run = c.post(f"/api/discovery/sources/{sid}/runs", json={"imprint_id": "half", "days": 1}).json()
    assert run["status"] == "failed" and run["stages_done"] == ["ingest"]
    assert "bake failed" in run["error"]
    assert not (Path(settings.imprints_dir) / "half").exists()
    assert _FakeTunnel.log == ["open fxadmin@reader.example:22 -> ('127.0.0.1', 9090)", "close"]


# ---------------------------------------------------------------------------
# the module itself: env names, the tunnel argv, the window arithmetic
# ---------------------------------------------------------------------------
def test_env_names_match_the_aximprint_transport():
    # `discovery/src/aximprint/ingest/axquery/sources.py` spells the same three.
    assert (ds.ENV_USER, ds.ENV_PASSWORD, ds.ENV_API_KEY) == (
        "AXIMPRINT_SOURCE_USER", "AXIMPRINT_SOURCE_PASSWORD", "AXIMPRINT_SOURCE_API_KEY")


def test_tunnel_argv_is_batch_mode_with_the_forward_and_never_a_secret():
    t = ds.SshTunnel(ds.SshSpec(host="reader", user="fx", key_path="/k", port=2222), ("127.0.0.1", 9090))
    argv = t.argv(40000)
    assert argv[:2] == ["ssh", "-N"]
    assert "BatchMode=yes" in argv and "ExitOnForwardFailure=yes" in argv
    assert argv[argv.index("-L") + 1] == "127.0.0.1:40000:127.0.0.1:9090"
    assert argv[argv.index("-p") + 1] == "2222" and argv[argv.index("-i") + 1] == "/k"
    assert argv[-1] == "fx@reader"


def test_a_real_tunnel_with_a_missing_key_fails_without_running_ssh(tmp_path):
    t = ds.SshTunnel(ds.SshSpec(host="reader", user="fx", key_path=str(tmp_path / "nokey")), ("127.0.0.1", 1))
    with pytest.raises(ds.AccessError, match="is not a file"):
        t.__enter__()


def test_rewrite_endpoint_keeps_scheme_and_path():
    assert ds.rewrite_endpoint("https://sensor.example:9443/axq", 5) == "https://127.0.0.1:5/axq"
    assert ds.endpoint_target("https://sensor.example/axq") == ("sensor.example", 443)
    assert ds.endpoint_target("http://127.0.0.1:9090") == ("127.0.0.1", 9090)


def test_window_from_probe_needs_a_last_ts():
    with pytest.raises(ds.AccessError, match="no last_ts"):
        ds.window_from_probe({"first_ts": 1.0}, 1)
    assert ds.window_from_probe({"first_ts": 0.0, "last_ts": 99.0}, 1) == (0.0, 100.0)
    assert ds.window_from_probe({"last_ts": 1_000_000.0}, 1) == (1_000_000.0 + 1 - 86400, 1_000_001.0)


# ---------------------------------------------------------------------------
# A7.2a -- the probe of an UNSAVED config (the editor's "Test connection")
# ---------------------------------------------------------------------------
PROBE = "/api/discovery/sources/probe"
RESET_AT_LOCAL = {
    "supported": True, "reachable": False,
    "error": "axquery /api/info unreachable at http://127.0.0.1:45678: [Errno 104] Connection reset by peer",
    "first_ts": None, "last_ts": None, "vantage": None, "extra": {}}


def test_probe_of_an_unsaved_config_answers_like_the_saved_one_and_writes_nothing(tmp_path):
    settings = _settings(tmp_path)
    be = _FakeDiscovery()
    c = _client(settings, backend=be)
    r = c.post(PROBE, json=_tunnel_source())
    assert r.status_code == 200, r.text
    doc = r.json()
    assert doc["source_id"] is None and doc["reachable"] is True and doc["stage"] is None
    assert doc["via"] == "ssh fxadmin@reader.example:22 -> 127.0.0.1:9090 via 127.0.0.1:45678"
    assert [s["stage"] for s in doc["steps"]] == list(ds.PROBE_STAGES)
    assert all(s["ok"] for s in doc["steps"]) and doc["probed_at"]
    assert be.probes == [("axquery", "axquery:http://127.0.0.1:45678", {})]
    assert _FakeTunnel.log == ["open fxadmin@reader.example:22 -> ('127.0.0.1', 9090)", "close"]
    # Nothing written: the sources table was never even opened, and lists empty.
    tables = {r[0] for r in sqlite3.connect(settings.discovery_db).execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "sources" not in tables
    assert c.get("/api/discovery/sources").json() == {"sources": []}


def test_probe_of_an_unsaved_config_leaves_saved_sources_untouched(tmp_path):
    c = _client(_settings(tmp_path), backend=_FakeDiscovery())
    sid = c.post("/api/discovery/sources", json=_tunnel_source()).json()["id"]
    before = c.get("/api/discovery/sources").json()
    assert c.post(PROBE, json=_tunnel_source(name="other")).status_code == 200
    assert c.get("/api/discovery/sources").json() == before
    assert c.get(f"/api/discovery/sources/{sid}").json()["last_probe"] is None


def test_an_unreachable_unsaved_config_names_the_stage_and_the_configured_endpoint(tmp_path):
    be = _FakeDiscovery(probe=lambda p, s, e: dict(RESET_AT_LOCAL))
    c = _client(_settings(tmp_path), backend=be)
    doc = c.post(PROBE, json=_tunnel_source()).json()
    assert doc["reachable"] is False and doc["stage"] == "api_http"
    assert doc["steps"] == [{"stage": s, "ok": s != "api_http"} for s in ds.PROBE_STAGES[:4]]
    # Worded against the endpoint the operator typed, not the tunnel's local end.
    assert "45678" not in doc["error"]
    assert "http://127.0.0.1:9090 (as seen from fxadmin@reader.example)" in doc["error"]
    assert "Connection reset by peer" in doc["error"]
    assert _FakeTunnel.log[-1] == "close"


def test_the_saved_probe_also_names_the_stage_and_the_configured_endpoint(tmp_path):
    be = _FakeDiscovery(probe=lambda p, s, e: dict(RESET_AT_LOCAL))
    c = _client(_settings(tmp_path), backend=be)
    sid = c.post("/api/discovery/sources", json=_tunnel_source()).json()["id"]
    doc = c.post(f"/api/discovery/sources/{sid}/probe").json()
    assert doc["source_id"] == sid and doc["stage"] == "api_http" and "45678" not in doc["error"]
    assert c.get(f"/api/discovery/sources/{sid}").json()["last_probe"]["stage"] == "api_http"


@pytest.mark.parametrize("message, stage", [
    ("ssh tunnel to fx@h:22 exited (255) before the forward came up: fx@h: Permission denied (publickey).", "ssh_auth"),
    ("ssh tunnel to fx@h:22 exited (255) before the forward came up: ssh: connect to host h port 22: Connection refused", "ssh_connect"),
    ("ssh tunnel to fx@h:22 exited (255) before the forward came up: Host key verification failed.", "ssh_auth"),
    ("ssh tunnel to fx@h:22 exited (255) before the forward came up: bind [127.0.0.1]:4000: Address already in use", "ssh_forward"),
])
def test_ssh_failures_are_sorted_into_their_stage(tmp_path, message, stage):
    err = message.split("came up: ", 1)[1]
    assert ds.classify_ssh_error(err) == stage

    class _Dead(_FakeTunnel):
        def __enter__(self):
            raise ds.AccessError(message, stage=ds.classify_ssh_error(err))

    be = _FakeDiscovery()
    doc = _client(_settings(tmp_path), backend=be, tunnel=_Dead).post(PROBE, json=_tunnel_source()).json()
    assert doc["reachable"] is False and doc["stage"] == stage and doc["error"] == message
    assert doc["steps"][-1] == {"stage": stage, "ok": False} and be.probes == []


@pytest.mark.parametrize("error, stage", [
    ("axquery /api/info unreachable at http://h:1: refused", "api_http"),
    ("axquery /api/info timed out after 60s — narrow the time window", "api_http"),
    ("axquery /api/info -> HTTP 401: http://h:1/api/info -- the server refused the credential", "api_http"),
    ("axquery /api/info returned non-JSON: b'<html>'", "plugin_census"),
    ("axquery /api/info returned an EMPTY body (http://h/api/info)", "plugin_census"),
])
def test_plugin_failures_are_sorted_into_http_or_census(tmp_path, error, stage):
    be = _FakeDiscovery(probe=lambda p, s, e: {"supported": True, "reachable": False, "error": error})
    doc = _client(_settings(tmp_path), backend=be).post(
        PROBE, json=_tunnel_source(endpoint="https://sensor.example:9443", access={"transport": "direct"})).json()
    assert doc["stage"] == stage and doc["error"] == error
    assert [s["stage"] for s in doc["steps"]] == list(ds.DIRECT_STAGES[: ds.DIRECT_STAGES.index(stage) + 1])


def test_a_crashed_plugin_is_a_census_failure_not_a_500(tmp_path):
    def boom(p, s, e):
        raise disc.DiscoveryError("probe failed (exit 1): Traceback ... http://127.0.0.1:45678")

    doc = _client(_settings(tmp_path), backend=_FakeDiscovery(probe=boom)).post(PROBE, json=_tunnel_source()).json()
    assert doc["stage"] == "plugin_census" and "127.0.0.1:9090" in doc["error"] and "45678" not in doc["error"]
    assert _FakeTunnel.log[-1] == "close"


@pytest.mark.parametrize("over, needle", [
    ({"plugin": "zeek"}, "reads a path, not an endpoint"),
    ({"endpoint": "10.0.0.1:9090"}, "absolute http(s) base URL"),
    ({"access": {"transport": "direct", "auth": "basic", "user": "ro"}}, "basic auth needs a password"),
    ({"access": {"transport": "ssh_tunnel", "auth": "none"}}, "access.ssh {host, user, key_path, port?} is required"),
    ({"name": "  "}, "name must be a non-empty string"),
])
def test_an_invalid_unsaved_config_is_refused_like_create_and_never_probed(tmp_path, over, needle):
    be = _FakeDiscovery()
    c = _client(_settings(tmp_path), backend=be)
    probed = c.post(PROBE, json=_tunnel_source(**over))
    created = c.post("/api/discovery/sources", json=_tunnel_source(**over))
    assert probed.status_code == created.status_code == 400
    assert probed.json() == created.json() and needle in probed.text
    assert be.probes == [] and _FakeTunnel.log == []


def test_a_malformed_body_is_the_same_422_as_create(tmp_path):
    c = _client(_settings(tmp_path), backend=_FakeDiscovery())
    body = {"plugin": "axquery"}  # no name, no endpoint
    p, cr = c.post(PROBE, json=body), c.post("/api/discovery/sources", json=body)
    assert p.status_code == cr.status_code == 422


def test_the_unsaved_probe_carries_the_write_token(tmp_path):
    c = _client(_settings(tmp_path, api_token="t0k"), backend=_FakeDiscovery())
    assert c.post(PROBE, json=_tunnel_source()).status_code == 401
    assert c.post(PROBE, json=_tunnel_source(), headers={"Authorization": "Bearer t0k"}).status_code == 200


def test_both_probe_routes_call_the_one_probe_function(tmp_path, monkeypatch):
    calls: list[tuple[str, str, str]] = []

    def spy(backend, plugin, spec, *, tunnel_factory=None, timeout_s):
        calls.append((plugin, spec.endpoint, spec.access.transport))
        return {"reachable": True, "error": None, "first_ts": None, "last_ts": None,
                "vantage": None, "extra": {}, "via": "direct", "stage": None, "steps": []}

    monkeypatch.setattr(ds, "probe_config", spy)
    c = _client(_settings(tmp_path, source_probe_timeout_s=7.0), backend=_FakeDiscovery())
    sid = c.post("/api/discovery/sources", json=_tunnel_source()).json()["id"]
    assert c.post(f"/api/discovery/sources/{sid}/probe").status_code == 200
    assert c.post(PROBE, json=_tunnel_source()).status_code == 200
    assert calls == [("axquery", "http://127.0.0.1:9090", "ssh_tunnel")] * 2


def test_secrets_reach_the_plugin_env_and_nowhere_else(tmp_path, caplog):
    import logging

    caplog.set_level(logging.DEBUG)
    be = _FakeDiscovery(probe=lambda p, s, e: dict(RESET_AT_LOCAL))
    c = _client(_settings(tmp_path), backend=be)
    for body, secret in (
        (_tunnel_source(access={"transport": "direct", "auth": "basic", "user": "ro"},
                        password="pw-SECRET-1", endpoint="https://s.example:9443"), "pw-SECRET-1"),
        (_tunnel_source(access={"transport": "ssh_tunnel", "auth": "api_key",
                                "ssh": {"host": "reader.example", "user": "fxadmin", "key_path": "~/.ssh/k"}},
                        api_key="key-SECRET-2"), "key-SECRET-2"),
    ):
        r = c.post(PROBE, json=body)
        assert r.status_code == 200 and secret not in r.text
    assert be.probes[0][2] == {ds.ENV_USER: "ro", ds.ENV_PASSWORD: "pw-SECRET-1"}
    assert be.probes[1][2] == {ds.ENV_API_KEY: "key-SECRET-2"}
    assert "SECRET" not in caplog.text


def test_secrets_from_fills_an_untyped_secret_from_the_saved_source(tmp_path):
    be = _FakeDiscovery()
    c = _client(_settings(tmp_path), backend=be)
    body = _tunnel_source(access={"transport": "direct", "auth": "basic", "user": "ro"},
                          endpoint="https://s.example:9443")
    sid = c.post("/api/discovery/sources", json={**body, "password": "stored-pw"}).json()["id"]
    # The edit form sends no password ("unchanged unless typed").
    assert c.post(PROBE, json=body).status_code == 400
    r = c.post(f"{PROBE}?secrets_from={sid}", json=body)
    assert r.status_code == 200 and "stored-pw" not in r.text
    assert be.probes[-1][2][ds.ENV_PASSWORD] == "stored-pw"
    # A typed one wins; an unknown id is a 404.
    c.post(f"{PROBE}?secrets_from={sid}", json={**body, "password": "typed"})
    assert be.probes[-1][2][ds.ENV_PASSWORD] == "typed"
    assert c.post(f"{PROBE}?secrets_from=src-nope", json=body).status_code == 404
    assert c.get(f"/api/discovery/sources/{sid}").json()["last_probe"] is None


class _Hung(_FakeTunnel):
    """A tunnel whose connect never comes up until something calls `kill()`."""

    def __init__(self, ssh, target):
        super().__init__(ssh, target)
        import threading

        self._stop = threading.Event()

    def __enter__(self):
        _FakeTunnel.log.append("connecting")
        self._stop.wait(10)
        raise ds.AccessError("stopped", stage="ssh_connect")

    def kill(self):
        _FakeTunnel.log.append("kill")
        self._stop.set()


def test_a_hung_ssh_connect_times_out_at_its_stage_and_the_tunnel_is_killed(tmp_path):
    be = _FakeDiscovery()
    c = _client(_settings(tmp_path, source_probe_timeout_s=0.3), backend=be, tunnel=_Hung)
    doc = c.post(PROBE, json=_tunnel_source()).json()
    assert doc["reachable"] is False and doc["stage"] == "ssh_connect"
    assert doc["error"] == "timed out after 0.3s at ssh_connect"
    assert doc["steps"] == [{"stage": "ssh_connect", "ok": False}]
    assert _FakeTunnel.log == ["connecting", "kill"] and be.probes == []


def test_a_hung_api_read_times_out_at_api_http_and_the_tunnel_is_closed(tmp_path):
    import threading

    released = threading.Event()

    class _Killable(_FakeTunnel):
        def kill(self):
            _FakeTunnel.log.append("kill")
            released.set()  # a killed tunnel fails the request in flight through it

    def hang(p, s, e):
        released.wait(10)
        return dict(RESET_AT_LOCAL)

    c = _client(_settings(tmp_path, source_probe_timeout_s=0.3), backend=_FakeDiscovery(probe=hang),
                tunnel=_Killable)
    doc = c.post(PROBE, json=_tunnel_source()).json()
    assert doc["stage"] == "api_http" and doc["error"].startswith("timed out after 0.3s at api_http")
    assert "http://127.0.0.1:9090" in doc["error"] and doc["via"]
    assert [s["ok"] for s in doc["steps"]] == [True, True, True, False]
    assert _FakeTunnel.log == ["open fxadmin@reader.example:22 -> ('127.0.0.1', 9090)", "kill", "close"]


def test_a_real_ssh_tunnel_is_terminated_by_the_deadline(tmp_path, monkeypatch):
    """The real `SshTunnel` against an `ssh` that never forwards: the deadline kills the
    process and nothing is left running."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake_ssh = bindir / "ssh"
    pidfile = tmp_path / "ssh.pid"
    fake_ssh.write_text(f"#!/bin/sh\necho $$ > {pidfile}\nexec sleep 30\n")
    fake_ssh.chmod(0o755)
    key = tmp_path / "id_test"
    key.write_text("not a real key")
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    spec = ds.AccessSpec(endpoint="http://127.0.0.1:9090", access=ds.validate_access(
        {"transport": "ssh_tunnel", "ssh": {"host": "h.example", "user": "fx", "key_path": str(key)}}))
    answer = ds.probe_config(_FakeDiscovery(), "axquery", spec, timeout_s=0.5)
    assert answer["stage"] == "ssh_connect" and answer["error"] == "timed out after 0.5s at ssh_connect"
    pid = int(pidfile.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_a_real_tunnel_whose_ssh_refuses_the_key_is_an_ssh_auth_failure(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "ssh").write_text("#!/bin/sh\necho 'fx@h: Permission denied (publickey).' >&2\nexit 255\n")
    (bindir / "ssh").chmod(0o755)
    key = tmp_path / "id_test"
    key.write_text("x")
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    spec = ds.AccessSpec(endpoint="http://127.0.0.1:9090", access=ds.validate_access(
        {"transport": "ssh_tunnel", "ssh": {"host": "h", "user": "fx", "key_path": str(key)}}))
    answer = ds.probe_config(_FakeDiscovery(), "axquery", spec, timeout_s=10)
    assert answer["stage"] == "ssh_auth" and "Permission denied" in answer["error"]


def test_create_and_update_store_a_probe_just_run_in_the_editor(tmp_path):
    c = _client(_settings(tmp_path), backend=_FakeDiscovery())
    probe = c.post(PROBE, json=_tunnel_source()).json()
    rec = c.post("/api/discovery/sources", json={**_tunnel_source(), "last_probe": probe}).json()
    assert rec["last_probe"]["reachable"] is True and rec["last_probe"]["probed_at"] == probe["probed_at"]
    assert "source_id" not in rec["last_probe"]
    failed = {**probe, "reachable": False, "stage": "api_http", "error": "refused"}
    upd = c.put(f"/api/discovery/sources/{rec['id']}", json={"last_probe": failed}).json()
    assert upd["last_probe"]["stage"] == "api_http"
    # Without one, an update leaves the stored probe as it was.
    assert c.put(f"/api/discovery/sources/{rec['id']}", json={"name": "renamed"}).json()["last_probe"]["stage"] == "api_http"


# ---------------------------------------------------------------------------
# access.tls_verify -- a sensor with a self-signed certificate
# ---------------------------------------------------------------------------
def _https_source(**access):
    return _tunnel_source(endpoint="https://10.80.72.40/axquery/api",
                          access={"transport": "direct", "auth": "none", **access})


def test_tls_verify_defaults_on_and_adds_nothing_to_the_env(tmp_path):
    be = _FakeDiscovery()
    c = _client(_settings(tmp_path), backend=be)
    rec = c.post("/api/discovery/sources", json=_https_source()).json()
    assert rec["access"]["tls_verify"] is True
    doc = c.post(PROBE, json=_https_source()).json()
    assert doc["via"] == "direct" and be.probes[-1][2] == {}


def test_tls_verify_off_reaches_the_plugin_env_on_probe_and_run_and_says_so(tmp_path):
    be = _FakeDiscovery()
    c = _client(_settings(tmp_path), backend=be)
    body = _https_source(auth="api_key", tls_verify=False)
    body["api_key"] = "k"
    doc = c.post(PROBE, json=body).json()
    assert doc["via"] == "direct, TLS certificate not verified"
    assert be.probes[-1][2] == {ds.ENV_API_KEY: "k", ds.ENV_TLS_VERIFY: "0"}
    sid = c.post("/api/discovery/sources", json=body).json()["id"]
    rec = c.get(f"/api/discovery/sources/{sid}").json()
    assert rec["access"]["tls_verify"] is False
    run = c.post(f"/api/discovery/sources/{sid}/runs", json={"name": "tls", "days": 1}).json()
    assert run["status"] == "completed", run
    assert be.requests[-1].env[ds.ENV_TLS_VERIFY] == "0"
    # Turned back on by an update.
    c.put(f"/api/discovery/sources/{sid}", json={"access": {"transport": "direct", "auth": "api_key"}})
    assert c.get(f"/api/discovery/sources/{sid}").json()["access"]["tls_verify"] is True


def test_tls_verify_must_be_a_boolean_and_old_records_verify(tmp_path):
    c = _client(_settings(tmp_path), backend=_FakeDiscovery())
    r = c.post(PROBE, json=_https_source(tls_verify="no"))
    assert r.status_code in (400, 422)
    assert ds.validate_access({"transport": "direct"}).tls_verify is True  # no key: a pre-field record
    assert ds.env_for(ds.validate_access({"tls_verify": False}), {}) == {ds.ENV_TLS_VERIFY: "0"}


def test_tls_env_name_matches_the_aximprint_transport():
    # `discovery/src/aximprint/ingest/siem/http.py` spells the same name.
    assert ds.ENV_TLS_VERIFY == "AXIMPRINT_SOURCE_TLS_VERIFY"


# ---------------------------------------------------------------------------
# run defaults are legacy on a source (2026-11-19); a run returns its options
# ---------------------------------------------------------------------------
def _no_defaults_source(**over):
    body = _tunnel_source(**over)
    body.pop("defaults")
    return body


def test_a_source_without_run_defaults_creates_and_updates(tmp_path):
    c = _client(_settings(tmp_path), backend=_FakeDiscovery())
    r = c.post("/api/discovery/sources", json=_no_defaults_source())
    assert r.status_code == 201, r.text
    assert r.json()["defaults"] == {}
    sid = r.json()["id"]
    body = _no_defaults_source(endpoint="http://127.0.0.1:9091")
    u = c.put(f"/api/discovery/sources/{sid}", json=body)
    assert u.status_code == 200 and u.json()["endpoint"] == "http://127.0.0.1:9091"
    assert u.json()["defaults"] == {}


def test_an_old_style_payload_with_run_defaults_is_still_accepted_and_kept(tmp_path):
    settings = _settings(tmp_path)
    c = _client(settings, backend=_FakeDiscovery())
    sid = c.post("/api/discovery/sources", json=_tunnel_source(
        defaults={"days": 3, "network": "corp-apcn", "sensors": ["XPress-SW-A"]})).json()["id"]
    # An update that leaves defaults out (the console's) keeps the stored ones.
    c.put(f"/api/discovery/sources/{sid}", json=_no_defaults_source(name="renamed"))
    # And a fresh app over the same store loads them.
    again = _client(settings, backend=_FakeDiscovery()).get(f"/api/discovery/sources/{sid}").json()
    assert again["name"] == "renamed"
    assert again["defaults"] == {"days": 3, "network": "corp-apcn", "sensors": ["XPress-SW-A"]}


def test_a_run_record_returns_the_options_it_ran_with(tmp_path):
    c = _client(_settings(tmp_path), backend=_FakeDiscovery())
    sid = c.post("/api/discovery/sources", json=_no_defaults_source()).json()["id"]
    run = c.post(f"/api/discovery/sources/{sid}/runs",
                 json={"name": "opt", "days": 1, "network": "corp", "vantage": "v:1", "window": 60}).json()
    assert run["options"] == {"network": "net-" + run["id"].split("-", 1)[1], "window": 60}
    listed = [r for r in c.get("/api/discovery/runs").json()["runs"] if r["id"] == run["id"]]
    assert listed[0]["options"] == run["options"] and listed[0]["source_id"] == sid


# ---------------------------------------------------------------------------
# "Add to an existing imprint": versions, lineage, the base never touched
# ---------------------------------------------------------------------------
class _StoreWritingDiscovery(_FakeDiscovery):
    """A fake whose ingest appends to the belief store file (so a copy is visible) and
    whose bake registers a PIT named after the run."""

    def __init__(self, *, fail_ingest=False, **kw):
        super().__init__(**kw)
        self.fail_ingest = fail_ingest
        self.stamped: list[tuple[str, str]] = []

    def run(self, request, *, on_stage, on_progress=None, handle=None):
        self.requests.append(request)
        with open(request.belief_store, "ab") as fh:
            fh.write(f"run {request.imprint_id}\n".encode())
        if self.fail_ingest:
            raise disc.DiscoveryError("ingest blew up")
        pid = f"pit-{request.imprint_id}"
        self.pits[pid] = request.imprint_id
        for s in disc.STAGES:
            on_stage(s)
        return disc.DiscoveryRunResult(pit_id=pid, stages=list(disc.STAGES))

    def stamp_network(self, store, network):
        self.stamped.append((store, network))

    def store_status(self, store):
        return {"last_run_vantage": "axellio-hq/rdr1:12"}


def _v1(tmp_path, **settings_over):
    be = _StoreWritingDiscovery()
    settings = _settings(tmp_path, **settings_over)
    c = _client(settings, backend=be)
    sid = c.post("/api/discovery/sources", json=_no_defaults_source()).json()["id"]
    run = c.post(f"/api/discovery/sources/{sid}/runs", json={
        "name": "corp", "ts_from": 0.0, "ts_to": 21600.0, "window": 900}).json()
    assert run["status"] == "completed", run
    return be, settings, c, sid, run


def _pits_dir(settings):
    return Path(settings.aximprint_store).parent / "pits"


def test_a_new_imprint_records_version_1_with_its_run_and_network(tmp_path):
    be, settings, c, sid, run = _v1(tmp_path)
    lin = c.get("/api/imprints/pit-corp").json()["lineage"]
    assert lin["version"] == 1 and lin["parent"] is None and lin["root"] == "pit-corp"
    assert lin["network"] == "net-" + run["id"].split("-", 1)[1]
    assert (lin["window_seconds"], lin["subbuckets"]) == (900, 12)
    assert lin["extendable"] is True and lin["next_name"] == "corp-v2"
    assert lin["runs"] == [{"run_id": run["id"], "source_id": sid, "plugin": "axquery",
                            "vantage": "axellio-hq/rdr1:12", "ts_from": 0.0, "ts_to": 21600.0}]


def test_add_copies_the_base_store_and_never_mutates_the_base(tmp_path):
    be, settings, c, sid, run1 = _v1(tmp_path)
    base_store = Path(settings.aximprint_store).parent / "corp.belief.duckdb"
    (Path(f"{base_store}.wal")).write_bytes(b"wal\n")
    before = base_store.read_bytes()
    a = _pits_dir(settings) / "pit-corp" / "pit-corp.assertions.yaml"
    a.write_text("schema: axr-assertions/1\n")
    run2 = c.post(f"/api/discovery/sources/{sid}/runs", json={
        "name": "corp-v2", "base_imprint": "pit-corp", "ts_from": 21600.0, "ts_to": 43200.0,
        "network": "typed", "vantage": "typed"}).json()
    assert run2["status"] == "completed", run2
    req = be.requests[-1]
    assert req.base_store == base_store and req.replace is False and req.stamp_network is None
    assert req.config["network"] == "net-" + run1["id"].split("-", 1)[1]  # inherited
    assert req.config["window"] == 900                                     # the base's
    # The base is byte-identical; the new store is a copy plus this run.
    assert base_store.read_bytes() == before
    new_store = base_store.parent / "corp-v2.belief.duckdb"
    assert new_store.read_bytes() == before + b"run corp-v2\n"
    assert Path(f"{new_store}.wal").read_bytes() == b"wal\n"
    lin = c.get("/api/imprints/corp-v2").json()["lineage"]
    assert lin["version"] == 2 and lin["parent"] == "pit-corp" and lin["root"] == "pit-corp"
    assert [r["run_id"] for r in lin["runs"]] == [run1["id"], run2["id"]]
    assert lin["next_name"] == "corp-v3"
    # Assertions carried as a copy, re-stemmed; the base's file is unchanged.
    carried = _pits_dir(settings) / "pit-corp-v2" / "pit-corp-v2.assertions.yaml"
    assert carried.read_text() == a.read_text() and not carried.is_symlink()
    # v1 is untouched: same lineage, still version 1.
    assert c.get("/api/imprints/pit-corp").json()["lineage"]["version"] == 1


def test_add_refuses_a_window_or_subbuckets_that_differ_from_the_base(tmp_path):
    be, settings, c, sid, _ = _v1(tmp_path)
    for body, needle in (({"window": 3600}, "window=3600 differs"),
                         ({"subbuckets": 6}, "subbuckets=6 differs")):
        r = c.post(f"/api/discovery/sources/{sid}/runs", json={
            "name": "corp-v2", "base_imprint": "pit-corp", "days": 1, **body})
        assert r.status_code == 400 and needle in r.text, r.text
    r = c.post(f"/api/discovery/sources/{sid}/runs", json={
        "name": "corp-v2", "base_imprint": "pit-corp", "days": 1, "window": 900, "subbuckets": 12})
    assert r.status_code == 202, r.text


def test_add_refuses_a_base_without_a_belief_store(tmp_path):
    be, settings, c, sid, _ = _v1(tmp_path)
    be.pits["pit-merged"] = "merged"           # no run record, no lineage
    r = c.post(f"/api/discovery/sources/{sid}/runs", json={
        "name": "m-v2", "base_imprint": "pit-merged", "days": 1})
    assert r.status_code == 409 and "cannot be extended" in r.text
    (Path(settings.aximprint_store).parent / "corp.belief.duckdb").unlink()
    r = c.post(f"/api/discovery/sources/{sid}/runs", json={
        "name": "corp-v2", "base_imprint": "pit-corp", "days": 1})
    assert r.status_code == 409 and "is gone" in r.text
    assert c.get("/api/imprints/pit-corp").json()["lineage"]["extendable"] is False


def test_a_failed_add_removes_only_its_partial_version(tmp_path):
    be, settings, c, sid, _ = _v1(tmp_path)
    base_store = Path(settings.aximprint_store).parent / "corp.belief.duckdb"
    before = base_store.read_bytes()
    be.fail_ingest = True
    run = c.post(f"/api/discovery/sources/{sid}/runs", json={
        "name": "corp-v2", "base_imprint": "pit-corp", "days": 1}).json()
    assert run["status"] == "failed" and "ingest blew up" in run["error"]
    assert base_store.read_bytes() == before
    assert not (base_store.parent / "corp-v2.belief.duckdb").exists()
    assert not (_pits_dir(settings) / "pit-corp-v2").exists()


def test_a_legacy_base_gets_a_derived_network_stamped_on_the_copy_only(tmp_path):
    be, settings, c, sid, run1 = _v1(tmp_path)
    # Make it legacy: no lineage file, a run record that declared no network.
    (_pits_dir(settings) / "pit-corp" / "lineage.json").unlink()
    runs = c.app.state.discovery_runs
    cfg = dict(runs.get(run1["id"]).config)
    cfg.pop("network")
    runs.update(run1["id"], config=cfg)
    lin = c.get("/api/imprints/pit-corp").json()["lineage"]
    assert lin["version"] == 1 and lin["network"] is None and lin["extendable"] is True
    run2 = c.post(f"/api/discovery/sources/{sid}/runs", json={
        "name": "corp-v2", "base_imprint": "corp", "days": 1}).json()
    assert run2["status"] == "completed", run2
    derived = "net-" + run1["id"].split("-", 1)[1]
    new_store = str(Path(settings.aximprint_store).parent / "corp-v2.belief.duckdb")
    assert be.stamped == [(new_store, derived)]
    assert be.requests[-1].config["network"] == derived


def test_a_save_as_copy_carries_no_lineage(tmp_path):
    be, settings, c, sid, _ = _v1(tmp_path)
    be.copy_pit = lambda store, pit, name: (be.pits.__setitem__("pit-copy", name)
                                            or {"pit_id": "pit-copy", "name": name})
    r = c.post("/api/imprints/pit-corp/copy", json={"name": "copy"})
    assert r.status_code in (200, 201), r.text
    assert r.json()["lineage"] is None
    assert not (_pits_dir(settings) / "pit-copy" / "lineage.json").exists()
