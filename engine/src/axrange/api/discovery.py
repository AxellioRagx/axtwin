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
import re
import secrets
import shutil
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Protocol

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
STATUS_CANCELLED = "cancelled"
ACTIVE_STATUSES = (STATUS_QUEUED, STATUS_RUNNING)

#: Seconds between SIGTERM and SIGKILL when a run is cancelled.
CANCEL_GRACE_S = 5.0

#: What `aximprint ingest` prints as each window closes: `window <start>: ...`. The start
#: plus the window length is the point in source time the ingest has reached -- a window
#: closes only when an event past its end has arrived.
_WINDOW_LINE = re.compile(r"^window (\d+(?:\.\d+)?):")


class DiscoveryError(Exception):
    """A stage failed. The message is what the run record carries as `error`."""


class DiscoveryUnavailableError(DiscoveryError):
    """The binary cannot be run at all (unset, missing, not executable)."""


class DiscoveryCancelled(DiscoveryError):
    """The operator cancelled the run; the stage's process was killed on purpose."""


def progress_from_line(line: str, window_seconds: float) -> Optional[float]:
    """The source-time position a `window <start>:` line proves the ingest has passed."""
    m = _WINDOW_LINE.match(line)
    if not m:
        return None
    return float(m.group(1)) + float(window_seconds)


class RunHandle:
    """The one thing a cancel can reach: whichever subprocess the run is in right now.

    `cancel()` marks the handle and kills the attached process GROUP (every stage is
    started in its own session, so a plugin's own children die with it); a stage that
    attaches after the mark is killed on attach. SIGTERM first, SIGKILL after
    `CANCEL_GRACE_S` if the process is still there.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._proc: Optional[subprocess.Popen] = None
        self.cancelled = False

    def attach(self, proc: subprocess.Popen) -> None:
        with self._lock:
            self._proc = proc
            if self.cancelled:
                _kill_group(proc)

    def detach(self) -> None:
        with self._lock:
            self._proc = None

    def cancel(self) -> None:
        with self._lock:
            self.cancelled = True
            proc = self._proc
        if proc is not None:
            _kill_group(proc)


def _kill_group(proc: subprocess.Popen) -> None:
    def _sig(sig: int) -> None:
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError, OSError):
            pass

    _sig(signal.SIGTERM)

    def _escalate() -> None:
        if proc.poll() is None:
            _sig(signal.SIGKILL)

    threading.Timer(CANCEL_GRACE_S, _escalate).start()


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

    def run(self, request: DiscoveryRunRequest, *, on_stage,
            on_progress: Optional[Callable[[float], None]] = None,
            handle: Optional[RunHandle] = None) -> DiscoveryRunResult: ...


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

    def run(self, r: DiscoveryRunRequest, *, on_stage,
            on_progress: Optional[Callable[[float], None]] = None,
            handle: Optional[RunHandle] = None) -> DiscoveryRunResult:
        done: list[str] = []

        def _did(stage: str) -> None:
            done.append(stage)
            on_stage(stage)

        window_seconds = float(r.config.get("window") or 3600)

        def _ingest_line(line: str) -> None:
            if on_progress is None:
                return
            ts = progress_from_line(line, window_seconds)
            if ts is not None:
                on_progress(ts)

        self._run(self.build_ingest_argv(r), stage="ingest", env=r.env,
                  on_line=_ingest_line, handle=handle)
        _did("ingest")

        proc = self._run(self.build_materialize_argv(r), stage="materialize", env=r.env,
                         handle=handle)
        try:
            pit_id = str(json.loads(proc.stdout or "")["pit_id"])
        except (ValueError, KeyError, TypeError) as e:
            raise DiscoveryError(
                "materialize printed no `pit_id`: "
                f"{clip(proc.stdout or '', limit=300, keep='head')!r}"
            ) from e
        _did("materialize")

        self._run(self.build_cast_argv(r, pit_id), stage=CAST_VERB, env=r.env, handle=handle)
        _did("cast")

        self._run(self.build_install_argv(r), stage=INSTALL_VERB, env=r.env, handle=handle)
        _did("install")
        return DiscoveryRunResult(pit_id=pit_id, stages=done)

    def _run(self, argv: list[str], *, stage: str, env: Optional[dict[str, str]] = None,
             ok_codes: tuple[int, ...] = (0,),
             on_line: Optional[Callable[[str], None]] = None,
             handle: Optional[RunHandle] = None) -> subprocess.CompletedProcess:
        """One stage, streamed: stdout is read line by line as it arrives (`on_line` is
        how ingest progress reaches the run record), stderr is collected for the error
        tail, the process runs in its own session so a cancel or a timeout kills it and
        everything it started. `PYTHONUNBUFFERED` because a Python child block-buffers a
        pipe, and a progress line that arrives at exit is no progress at all."""
        full_env = {**os.environ, **(env or {}), "PYTHONUNBUFFERED": "1"}
        try:
            proc = subprocess.Popen(  # noqa: S603 -- argv list, no shell
                argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                env=full_env, start_new_session=True,
            )
        except OSError as e:
            raise DiscoveryUnavailableError(
                f"cannot run {self.binary!r}: {e} (set {SETTING_BIN} to the aximprint "
                "executable)"
            ) from e
        if handle is not None:
            handle.attach(proc)
        err_chunks: list[str] = []

        def _drain_stderr() -> None:
            assert proc.stderr is not None
            for chunk in proc.stderr:
                err_chunks.append(chunk)
                # Keep the tail only; a plugin's stderr over a multi-day read is unbounded.
                if len(err_chunks) > 400:
                    del err_chunks[:200]

        timed_out = threading.Event()

        def _on_timeout() -> None:
            timed_out.set()
            _kill_group(proc)

        timer = threading.Timer(self.timeout_s, _on_timeout)
        timer.daemon = True
        reader = threading.Thread(target=_drain_stderr, name=f"{stage}-stderr", daemon=True)
        timer.start()
        reader.start()
        out_lines: list[str] = []
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                out_lines.append(line)
                if on_line is not None:
                    on_line(line.rstrip("\n"))
            proc.wait()
        finally:
            timer.cancel()
            reader.join()
            if handle is not None:
                handle.detach()
        stdout = "".join(out_lines)
        stderr = "".join(err_chunks)
        if handle is not None and handle.cancelled:
            raise DiscoveryCancelled(f"{stage} cancelled by the operator")
        if timed_out.is_set():
            raise DiscoveryError(
                f"{stage} timed out after {self.timeout_s:g}s: "
                f"{stderr_tail(stderr) or '<no stderr>'}"
            )
        if proc.returncode not in ok_codes:
            raise DiscoveryError(
                f"{stage} failed (exit {proc.returncode}): "
                f"{stderr_tail(clip(stderr)) or '<no stderr>'}"
            )
        return subprocess.CompletedProcess(argv, proc.returncode, stdout, stderr)


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
    #: Source time the ingest has reached (`progress_from_line`), and when it said so.
    progress_ts: Optional[float] = None
    progress_at: Optional[float] = None
    #: When a cancel was asked for; the status turns `cancelled` once the stage is dead.
    cancel_requested_at: Optional[float] = None


class DiscoveryRuns:
    """In-memory, lock-guarded, one per `create_app`."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._runs: dict[str, DiscoveryRun] = {}
        self._handles: dict[str, RunHandle] = {}

    def handle_for(self, run_id: str) -> RunHandle:
        """The run's cancel handle, made on first ask so a cancel that lands before the
        job thread has started still reaches the first stage."""
        with self._lock:
            h = self._handles.get(run_id)
            if h is None:
                h = self._handles[run_id] = RunHandle()
            return h

    def cancel(self, run_id: str) -> bool:
        """Ask a queued/running run to stop. False when it is not active."""
        with self._lock:
            r = self._runs.get(run_id)
            if r is None or r.status not in ACTIVE_STATUSES:
                return False
            if r.cancel_requested_at is None:
                r.cancel_requested_at = time.time()
            h = self._handles.get(run_id)
            if h is None:
                h = self._handles[run_id] = RunHandle()
        h.cancel()
        return True

    def delete(self, run_id: str) -> bool:
        """Forget a finished run's record. False when unknown; a queued/running run is
        refused (`ValueError`) -- its job thread still writes to the record, and the
        operator's way to stop it is `cancel`."""
        with self._lock:
            r = self._runs.get(run_id)
            if r is None:
                return False
            if r.status in ACTIVE_STATUSES:
                raise ValueError(f"discovery run {run_id!r} is {r.status}; cancel it first")
            del self._runs[run_id]
            self._handles.pop(run_id, None)
            return True

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
        _discard_stale_belief_store(request)
        result = backend.run(request, on_stage=lambda s: runs.stage_done(run_id, s),
                             on_progress=_progress_sink(runs, run_id),
                             handle=runs.handle_for(run_id))
    except DiscoveryCancelled as e:
        _discard_partial_bundle(request)
        _discard_stale_belief_store(request)
        runs.update(run_id, status=STATUS_CANCELLED, error=str(e), finished_at=time.time())
        return
    except Exception as e:  # noqa: BLE001 -- the record IS the error channel
        _discard_partial_bundle(request)
        runs.update(run_id, status=STATUS_FAILED, error=clip(str(e), limit=2000),
                    finished_at=time.time())
        return
    finally:
        shutil.rmtree(request.scratch_dir, ignore_errors=True)
    runs.update(run_id, status=STATUS_COMPLETED, pit_id=result.pit_id,
                finished_at=time.time())


def _progress_sink(runs: "DiscoveryRuns", run_id: str) -> Callable[[float], None]:
    def _sink(ts: float) -> None:
        runs.update(run_id, progress_ts=ts, progress_at=time.time())
    return _sink


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
            _discard_stale_belief_store(request)
            result = backend.run(request, on_stage=lambda s: runs.stage_done(run_id, s),
                                 on_progress=_progress_sink(runs, run_id),
                                 handle=runs.handle_for(run_id))
    except DiscoveryCancelled as e:
        _discard_partial_bundle(request)
        _discard_stale_belief_store(request)
        runs.update(run_id, status=STATUS_CANCELLED, error=str(e), finished_at=time.time())
        return
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


def _discard_stale_belief_store(request: DiscoveryRunRequest) -> None:
    """The belief store under this imprint id is this run's to write. One that already
    exists can only be what an earlier failed or cancelled run left (a completed run's
    bundle would have refused this run at POST), and ingesting on top of it would merge
    two reads into one store. Removed, with DuckDB's `-wal`."""
    for suffix in ("", ".wal"):
        p = Path(f"{request.belief_store}{suffix}")
        if p.is_file():
            try:
                p.unlink()
            except OSError:
                pass


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
