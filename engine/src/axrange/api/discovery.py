"""Discovery runs (`api/Spec.md` A1): the ingest -> materialize -> cast -> install
chain as ONE control-plane action, over `aximprint`'s CLI.

The engine never imports `aximprint` (root `CLAUDE.md`, the one-way boundary),
so this module reaches the discovery package the same way `recompile.py`
does: the executable named by `AXRANGE_AXIMPRINT_BIN`, run as a subprocess.
`DiscoveryBackend` is the seam a test (or, later, an in-process backend once
the API lives outside `engine/`) drops into; `SubprocessDiscovery` is the one
real implementation.

Where things land, all derived from settings the operator already sets:

  * the belief store of a run:  `<dir of AXRANGE_AXIMPRINT_STORE>/<imprint_id>.belief.duckdb`
  * the PIT:                    `AXRANGE_AXIMPRINT_STORE` (the IR store `apply` casts from)
  * the installed imprint:      `<AXRANGE_IMPRINTS_DIR>/<imprint_id>/` -- the legacy
                                cast-bundle layout `aximprint install-cast` writes and
                                `api/imprints.py` already reads.

A run is server-side state in memory (`DiscoveryRuns`): it survives for the
life of the `axrange serve` process, which is the life of the console session
that started it. Persisting runs is not needed until something reads them
after a restart.
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Protocol

from axrange.api.recompile import (
    CAST_VERB,
    DEFAULT_TIMEOUT_S,
    INSTALL_VERB,
    SETTING_BIN,
    SETTING_STORE,
    clip,
    stderr_tail,
)

SETTING_IMPRINTS_DIR = "AXRANGE_IMPRINTS_DIR"

#: The four stages, in order; a run record names which have completed.
STAGES = ("ingest", "materialize", "cast", "install")

STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"


class DiscoveryError(Exception):
    """A stage failed. The message is what the run record carries as `error`."""


class DiscoveryUnavailableError(DiscoveryError):
    """The binary cannot be run at all (unset, missing, not executable)."""


@dataclass
class DiscoveryRunRequest:
    plugin: str
    config: dict[str, Any]
    imprint_id: str
    belief_store: Path
    ir_store: Path
    scratch_dir: Path
    catalog_root: Path
    sensors: list[str] = field(default_factory=list)
    #: Extra environment for every `aximprint` subprocess of this run -- the credential
    #: a stored discovery source carries (`discovery_sources.env_for`). Never `argv`.
    env: dict[str, str] = field(default_factory=dict)


@dataclass
class DiscoveryRunResult:
    pit_id: str
    stages: list[str]


class DiscoveryBackend(Protocol):
    def list_plugins(self) -> list[dict[str, Any]]: ...

    def probe(self, plugin: str, source: str, *, env: dict[str, str]) -> dict[str, Any]: ...

    def run(self, request: DiscoveryRunRequest, *, on_stage) -> DiscoveryRunResult: ...


# ---------------------------------------------------------------------------
# config validation -- the plugin's declared schema, applied here
# ---------------------------------------------------------------------------
_JSON_TYPES = {
    "string": (str,),
    "number": (int, float),
    "integer": (int,),
    "boolean": (bool,),
    "object": (dict,),
    "array": (list,),
}


def check_config(schema: dict[str, Any], config: Any) -> list[str]:
    """The subset of JSON schema a plugin's `config_schema` uses -- `type`,
    `properties`, `required`, `additionalProperties` -- checked without a
    `jsonschema` dependency. Returns every problem, by key, so a caller sees
    the whole refusal at once rather than one key per round trip."""
    problems: list[str] = []
    if not isinstance(config, dict):
        return ["config must be an object"]
    props = schema.get("properties") or {}
    for key in schema.get("required") or []:
        if key not in config:
            problems.append(f"missing required key {key!r}")
    if schema.get("additionalProperties") is False:
        for key in sorted(set(config) - set(props)):
            problems.append(f"unknown key {key!r} (allowed: {', '.join(sorted(props))})")
    for key, spec in props.items():
        if key not in config:
            continue
        want = spec.get("type")
        if want in _JSON_TYPES:
            ok = _JSON_TYPES[want]
            value = config[key]
            # bool is an int in Python; a schema that says integer/number does
            # not mean True.
            if not isinstance(value, ok) or (isinstance(value, bool) and want != "boolean"):
                problems.append(f"{key!r} must be {want}, got {type(value).__name__}")
    return problems


# ---------------------------------------------------------------------------
# the subprocess backend
# ---------------------------------------------------------------------------
class SubprocessDiscovery:
    """Run `aximprint` four times. Nothing here knows what an imprint IS."""

    def __init__(self, binary: str, *, timeout_s: float = DEFAULT_TIMEOUT_S) -> None:
        self.binary = binary
        self.timeout_s = timeout_s

    # -- plugins ----------------------------------------------------------
    def list_plugins(self) -> list[dict[str, Any]]:
        proc = self._run([self.binary, "plugins"], stage="plugins")
        try:
            doc = json.loads(proc.stdout or "")
            plugins = doc["plugins"]
            assert isinstance(plugins, list)
        except (ValueError, KeyError, AssertionError) as e:
            raise DiscoveryError(
                f"`{self.binary} plugins` did not print a plugin listing: "
                f"{clip(proc.stdout or '', limit=300, keep='head')!r}"
            ) from e
        return plugins

    # -- probe --------------------------------------------------------------
    def probe(self, plugin: str, source: str, *, env: dict[str, str]) -> dict[str, Any]:
        """`aximprint probe`: reachability + span, no events. Exit 2 is the plugin saying
        it cannot probe at all (a path plugin); anything else non-zero is a real failure."""
        argv = [self.binary, "probe", "--plugin", plugin, "--source", source]
        proc = self._run(argv, stage="probe", env=env, ok_codes=(0, 2))
        try:
            doc = json.loads(proc.stdout or "")
            assert isinstance(doc, dict)
        except (ValueError, AssertionError) as e:
            raise DiscoveryError(
                f"`{self.binary} probe` did not print an answer: "
                f"{clip(proc.stdout or '', limit=300, keep='head')!r}"
            ) from e
        if proc.returncode == 2 or not doc.get("supported"):
            raise DiscoveryError(f"plugin {plugin!r} cannot probe a source (no network transport)")
        return doc

    # -- the chain ---------------------------------------------------------
    def build_ingest_argv(self, r: DiscoveryRunRequest) -> list[str]:
        cfg = r.config
        argv = [
            self.binary, "ingest",
            "--plugin", r.plugin,
            "--source", str(cfg["source"]),
            "--store", str(r.belief_store),
        ]
        if cfg.get("ts_from") is not None:
            argv += ["--from", str(cfg["ts_from"])]
        if cfg.get("ts_to") is not None:
            argv += ["--to", str(cfg["ts_to"])]
        if cfg.get("vantage"):
            argv += ["--vantage", str(cfg["vantage"])]
        if cfg.get("network"):
            argv += ["--network", str(cfg["network"])]
        if cfg.get("window") is not None:
            argv += ["--window", str(cfg["window"])]
        if cfg.get("subbuckets") is not None:
            argv += ["--subbuckets", str(cfg["subbuckets"])]
        return argv

    def build_materialize_argv(self, r: DiscoveryRunRequest) -> list[str]:
        # `--replace`: the IR store is this API's own working store and a PIT id
        # is deterministic over (source, as_of), so re-discovering the same
        # capture under a new imprint id must not be refused by the store's
        # "something may have compared against it" guard -- nothing here
        # compares PITs.
        return [
            self.binary, "materialize",
            "--store", str(r.belief_store),
            "--out", str(r.ir_store),
            "--replace",
        ]

    def build_cast_argv(self, r: DiscoveryRunRequest, pit_id: str) -> list[str]:
        return [
            self.binary, CAST_VERB,
            "--store", str(r.ir_store),
            "--pit", pit_id,
            "--out", str(r.scratch_dir),
            "--imprint-id", r.imprint_id,
        ]

    def build_install_argv(self, r: DiscoveryRunRequest) -> list[str]:
        argv = [
            self.binary, INSTALL_VERB,
            str(r.scratch_dir), str(r.catalog_root), r.imprint_id,
        ]
        if r.sensors:
            argv += ["--sensors", *r.sensors]
        return argv

    def run(self, r: DiscoveryRunRequest, *, on_stage) -> DiscoveryRunResult:
        done: list[str] = []

        def _did(stage: str) -> None:
            done.append(stage)
            on_stage(stage)

        self._run(self.build_ingest_argv(r), stage="ingest", env=r.env)
        _did("ingest")

        proc = self._run(self.build_materialize_argv(r), stage="materialize", env=r.env)
        try:
            pit_id = str(json.loads(proc.stdout or "")["pit_id"])
        except (ValueError, KeyError, TypeError) as e:
            raise DiscoveryError(
                "materialize printed no `pit_id`: "
                f"{clip(proc.stdout or '', limit=300, keep='head')!r}"
            ) from e
        _did("materialize")

        self._run(self.build_cast_argv(r, pit_id), stage=CAST_VERB, env=r.env)
        _did("cast")

        self._run(self.build_install_argv(r), stage=INSTALL_VERB, env=r.env)
        _did("install")
        return DiscoveryRunResult(pit_id=pit_id, stages=done)

    def _run(self, argv: list[str], *, stage: str, env: Optional[dict[str, str]] = None,
             ok_codes: tuple[int, ...] = (0,)) -> subprocess.CompletedProcess:
        try:
            proc = subprocess.run(  # noqa: S603 -- argv list, no shell
                argv, capture_output=True, text=True, timeout=self.timeout_s,
                env={**os.environ, **env} if env else None,
            )
        except OSError as e:
            raise DiscoveryUnavailableError(
                f"cannot run {self.binary!r}: {e} (set {SETTING_BIN} to the aximprint "
                "executable)"
            ) from e
        except subprocess.TimeoutExpired as e:
            raise DiscoveryError(
                f"{stage} timed out after {self.timeout_s:g}s: "
                f"{stderr_tail(e.stderr if isinstance(e.stderr, str) else '') or '<no stderr>'}"
            ) from e
        if proc.returncode not in ok_codes:
            raise DiscoveryError(
                f"{stage} failed (exit {proc.returncode}): "
                f"{stderr_tail(clip(proc.stderr or '')) or '<no stderr>'}"
            )
        return proc


# ---------------------------------------------------------------------------
# the run table
# ---------------------------------------------------------------------------
@dataclass
class DiscoveryRun:
    id: str
    plugin: str
    imprint_id: str
    config: dict[str, Any]
    status: str = STATUS_QUEUED
    stages_done: list[str] = field(default_factory=list)
    pit_id: Optional[str] = None
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    # A7: set when the run came from a stored discovery source.
    source_id: Optional[str] = None
    started_at: Optional[float] = None
    ts_from: Optional[float] = None
    ts_to: Optional[float] = None
    #: How the source was reached ("direct", "ssh ... via 127.0.0.1:NNN"); no secret.
    via: Optional[str] = None


class DiscoveryRuns:
    """In-memory, lock-guarded, one per `create_app`."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._runs: dict[str, DiscoveryRun] = {}

    def new(self, plugin: str, imprint_id: str, config: dict[str, Any],
            *, source_id: Optional[str] = None) -> DiscoveryRun:
        with self._lock:
            while True:
                run_id = f"disc-{secrets.token_hex(4)}"
                if run_id not in self._runs:
                    break
            run = DiscoveryRun(id=run_id, plugin=plugin, imprint_id=imprint_id,
                               config=dict(config), source_id=source_id)
            self._runs[run_id] = run
            return run

    def get(self, run_id: str) -> Optional[DiscoveryRun]:
        with self._lock:
            return self._runs.get(run_id)

    def list(self) -> list[DiscoveryRun]:
        """Newest first; snapshots, so a caller never holds a live record."""
        with self._lock:
            ids = sorted(self._runs, key=lambda i: self._runs[i].created_at, reverse=True)
        return [r for r in (self.snapshot(i) for i in ids) if r is not None]

    def active_for_source(self, source_id: str) -> list[str]:
        with self._lock:
            return [r.id for r in self._runs.values()
                    if r.source_id == source_id and r.status in (STATUS_QUEUED, STATUS_RUNNING)]

    def active_imprint_ids(self) -> set[str]:
        """Imprint ids a queued/running run is about to write -- a second POST
        for one of them is refused before two chains race on one bundle dir."""
        with self._lock:
            return {r.imprint_id for r in self._runs.values()
                    if r.status in (STATUS_QUEUED, STATUS_RUNNING)}

    def snapshot(self, run_id: str) -> Optional[DiscoveryRun]:
        with self._lock:
            r = self._runs.get(run_id)
            if r is None:
                return None
            return DiscoveryRun(**{**r.__dict__, "stages_done": list(r.stages_done),
                                   "config": dict(r.config)})

    def update(self, run_id: str, **fields: Any) -> None:
        with self._lock:
            r = self._runs[run_id]
            for k, v in fields.items():
                setattr(r, k, v)

    def stage_done(self, run_id: str, stage: str) -> None:
        with self._lock:
            self._runs[run_id].stages_done.append(stage)


def execute_run(
    runs: DiscoveryRuns, run_id: str, backend: DiscoveryBackend,
    request: DiscoveryRunRequest,
) -> None:
    """The job a background thread runs. Every outcome lands on the record;
    nothing escapes (a thread with no handler would print a traceback and
    leave the run `running` forever).

    A failed install may have left a half-written bundle under the imprint
    id; it is removed, because this run was refused at POST unless that
    directory did NOT exist, so whatever is there is this run's own.
    """
    runs.update(run_id, status=STATUS_RUNNING, started_at=time.time())
    try:
        result = backend.run(request, on_stage=lambda s: runs.stage_done(run_id, s))
    except Exception as e:  # noqa: BLE001 -- the record IS the error channel
        _discard_partial_bundle(request)
        runs.update(run_id, status=STATUS_FAILED, error=clip(str(e), limit=2000),
                    finished_at=time.time())
        return
    finally:
        shutil.rmtree(request.scratch_dir, ignore_errors=True)
    runs.update(run_id, status=STATUS_COMPLETED, pit_id=result.pit_id,
                finished_at=time.time())


def execute_source_run(
    runs: DiscoveryRuns, run_id: str, backend: DiscoveryBackend,
    request: DiscoveryRunRequest, *, access_spec: Any, lookback_days: Optional[float],
    tunnel_factory: Any = None,
) -> None:
    """`execute_run` for a run that reaches its source through a stored access path
    (A7): open the path (a tunnel, for the whole chain), rewrite `config.source` to the
    base URL this machine can reach, carry the credential in the subprocess environment,
    turn "the last N days" into bounds from the source's own span, then run the chain.
    Every failure -- the tunnel, the probe, a stage -- lands on the record."""
    from axrange.api import discovery_sources as ds

    runs.update(run_id, status=STATUS_RUNNING, started_at=time.time())
    try:
        with ds.open_access(access_spec, tunnel_factory=tunnel_factory) as opened:
            request.config["source"] = f"{request.plugin}:{opened.base_url}"
            request.env = dict(opened.env)
            runs.update(run_id, via=opened.via)
            if lookback_days is not None:
                probe = backend.probe(request.plugin, request.config["source"], env=request.env)
                if not probe.get("reachable"):
                    raise DiscoveryError(
                        f"source not reachable: {probe.get('error') or 'no reason given'}")
                ts_from, ts_to = ds.window_from_probe(probe, lookback_days)
                request.config["ts_from"] = ts_from
                request.config["ts_to"] = ts_to
            runs.update(run_id, config=dict(request.config),
                        ts_from=request.config.get("ts_from"), ts_to=request.config.get("ts_to"))
            result = backend.run(request, on_stage=lambda s: runs.stage_done(run_id, s))
    except Exception as e:  # noqa: BLE001 -- the record IS the error channel
        _discard_partial_bundle(request)
        runs.update(run_id, status=STATUS_FAILED, error=clip(str(e), limit=2000),
                    finished_at=time.time())
        return
    finally:
        shutil.rmtree(request.scratch_dir, ignore_errors=True)
    runs.update(run_id, status=STATUS_COMPLETED, pit_id=result.pit_id,
                finished_at=time.time())


def _discard_partial_bundle(request: DiscoveryRunRequest) -> None:
    d = request.catalog_root / request.imprint_id
    if d.is_dir() and not d.is_symlink():
        shutil.rmtree(d, ignore_errors=True)


def unset_settings(settings: Any) -> Optional[str]:
    """The first discovery setting a run cannot start without, named so the
    refusal tells the operator which key to set (`recompile`'s posture)."""
    if not settings.aximprint_bin:
        return SETTING_BIN
    if not settings.aximprint_store:
        return SETTING_STORE
    if not settings.imprints_dir:
        # Unset means the package's own fixtures directory, which a run must
        # never write into.
        return SETTING_IMPRINTS_DIR
    return None
