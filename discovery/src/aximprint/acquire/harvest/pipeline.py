"""pipeline — the concurrent extract -> Zeek -> reap pipeline (H-4,
`docs/WORK_ORDER_pipeline.md`).

This is the wiring between three already-built, independently-tested pieces — it makes almost no
decisions of its own that those modules haven't already settled:

  * `aximprint.acquire.extract.extract_window` (reused unchanged — no new HTTP code) turns one `Window` into a
    `Written`/`Empty`/`Failed` outcome.
  * `aximprint.acquire.harvest.spool.Spool` is the disk governor: `admit()` blocks for backpressure,
    `commit()`/`fail()` land or abandon a reservation, `release(delete=True)` is the **only** thing
    that ever deletes a pcap.
  * `aximprint.acquire.harvest.zeek.ZeekRunner` (`FakeZeekRunner`/`PodmanZeekRunner`) turns one pcap into a
    directory of Zeek logs, as a value (`ZeekResult`, never an exception, even on failure).

**The one invariant this module exists to protect:** a pcap is deleted only after Zeek has
succeeded with it, AND only if this pipeline owns it. `spool.release(window_id, delete=True)` is
called from exactly one place below (`_process_zeek_item`), only when `ZeekResult.ok`, and only
for an `owned` `_ZeekWorkItem` (`docs/WORK_ORDER_harvest_from_pcaps.md`, D3) — a failed Zeek run
leaves an owned pcap RESIDENT, never released, so the window is retried on a future run via Phase
5's orphan re-drive; a `--from-pcaps`-supplied pcap is never released at all, success or failure,
because it was never admitted into any `Spool` to begin with (see `run_harvest_from_pcaps`).

`harvest --from-pcaps SRC` swaps the extract stage for a directory listing: run the Zeek stage
alone over pcaps already on disk, never touching the appliance. Its own top-level entry point
(`plan_from_pcaps`/`run_harvest_from_pcaps`, at the bottom of this module) rather than a branch in
`run_harvest`, since almost everything above this point — `Spool`, credentials, the window-plan
grammar — is inapplicable to it (D4, `docs/WORK_ORDER_harvest_from_pcaps.md`).

Runtime shape (`docs/PLAN_metadata_harvester.md` §5):

```
   plan_harvest ──► N extract workers ──► pcap spool ──► M zeek workers ──► release
                    (ThreadPoolExecutor)   (disk cap)     (persistent threads + queue.Queue)
                          ▲                                     │
                          └──────────── backpressure ───────────┘
```

Extract workers run in a `concurrent.futures.ThreadPoolExecutor`, but **windows are submitted one
at a time from a sliding window of at most `extract_concurrency` in-flight futures — never all of
`plan.windows` upfront.** A pool's workers dequeue eagerly: a worker freed by one task's completion
starts the next *already-queued* task immediately, before the submitting thread has reacted to
that task's outcome — so a design that submits everything upfront and relies on `Future.cancel()`
after a SIGINT/fatal-error/`--fail-fast` trigger cannot reliably "stop admitting new work" (the
freed worker can already be running window N+1 by the time this thread decides to stop). Submitting
incrementally, only when this thread itself chooses to, closes that race — see `run_harvest`'s
`_submit_next` for the mechanism. Zeek workers are plain, persistent `threading.Thread`s consuming
a `queue.Queue` — a pool shape that suits a long-lived consumer better than a `Future`-per-item
executor, since work keeps arriving from the extract side for the run's whole duration.

Two functions split Phase 1's preflight checks (deliberately, so each is independently testable):
`plan_harvest()` owns "the window plan must be computable" (pure `--start`/`--end` expression
grammar plus `windows.plan`, needing the client only when `--end` is omitted); `preflight()` owns
the other four ("`zeek_runner.probe()` must be `ok`", "the spool root must be writable", "the zeek
root must be writable", "credentials must resolve" — the last checked with one cheap,
already-existing call, `catalog.list_streams(client)`, rather than this module inventing a
parallel credential-resolution path). `run_harvest()` calls both, in that order, before creating
the `Spool` or starting a single worker — "preflight before any extraction" is structural here,
not a convention. `run_harvest_from_pcaps` shares the zeek-probe and zeek-root-writable checks
(`_check_zeek_probe`/`_check_zeek_root_writable`) directly, since it has no `HarvestConfig`/
`Spool`/client for a shared `preflight()` call to make sense against.
"""

from __future__ import annotations

import queue
import threading
import time
import uuid
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from aximprint.acquire import (
    __version__,
    catalog,
    runner,
    streamspec,
    timespec,
    windows,
)
from aximprint.acquire.errors import XtractError
from aximprint.acquire.extract import (
    PCAP_MAGICS,
    Empty,
    Failed,
    Written,
    extract_window,
    leading_magic,
)
from aximprint.acquire.harvest.spool import (
    SizeEstimator,
    Spool,
    SpoolError,
    validate_window_id,
)
from aximprint.acquire.harvest.zeek import ZeekResult, ZeekRunner
from aximprint.acquire.timeutil import NS_PER_SECOND, Duration, Instant
from aximprint.acquire.windows import Window
from aximprint.vantage import Vantage, vantage_id, write_vantage

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = [
    "DEFAULT_EXTRACT_CONCURRENCY",
    "DEFAULT_RETAINED_FAILURE_FRACTION",
    "DEFAULT_STALL_TIMEOUT_S",
    "DEFAULT_ZEEK_WORKERS",
    "FromPcapsAccountingError",
    "FromPcapsConfig",
    "FromPcapsPlan",
    "FromPcapsSourceError",
    "HarvestConfig",
    "HarvestPlan",
    "PipelineError",
    "PlanNotComputableError",
    "PreflightError",
    "RetainedFailureLimitExceededError",
    "RunReport",
    "SpoolDriftError",
    "SpoolRootNotWritableError",
    "ZeekProbeFailedError",
    "ZeekRootNotWritableError",
    "check_drift",
    "check_from_pcaps_accounting",
    "plan_from_pcaps",
    "plan_harvest",
    "preflight",
    "run_harvest",
    "run_harvest_from_pcaps",
    "seed_bytes_for",
]


class PipelineError(XtractError):
    """Base class for every error this module raises directly.

    Every OTHER error a lower-level module can raise during a run (`AuthenticationError`,
    `UnauthorizedError`, `runner.AllStreamsEmptyError`, `catalog.CatalogError`,
    `aximprint.acquire.harvest.spool.SpoolError` and its subtypes, ...) is let through **unwrapped** —
    reusing the existing typed-error hierarchy (`LES-041`) so the CLI's existing exit-code table
    classifies it exactly like every other command in this same CLI (`streams`, `pcap`) already
    does, instead of this module inventing a second, parallel hierarchy a caller would have to
    special-case.
    """


class PreflightError(PipelineError):
    """A Phase-1 precondition failed before any extraction began."""


class ZeekProbeFailedError(PreflightError):
    """`zeek_runner.probe()` reported `ok=False` — discovered now, not an hour into a run."""


class SpoolRootNotWritableError(PreflightError):
    """The configured spool root cannot be created or written to."""


class ZeekRootNotWritableError(PreflightError):
    """The configured Zeek output root (`<out>/zeek`) cannot be created or written to.

    `run_harvest` used to reach an unwritable `<out>/zeek` only via a bare, unguarded
    `zeek_root.mkdir(parents=True, exist_ok=True)` — an unwritable `--out` surfaced as a raw
    `OSError`, mapped by `cli.py`'s last-resort handler to a generic "unexpected error" and exit
    `4`, rather than the same clear, Phase-1, no-work-attempted-yet diagnosis
    `SpoolRootNotWritableError` already gives its own root. Checked from both `preflight()` (for
    `run_harvest`) and `run_harvest_from_pcaps`, which has no spool root to check at all but still
    needs this one.
    """


class PlanNotComputableError(PreflightError):
    """`--streams`/`--start`/`--end`/`--interval` do not resolve to a window plan.

    Wraps a malformed `--streams` spec, a `timespec` expression-grammar failure, or a
    `windows.plan` failure (bad range/interval, or the window-count ceiling without `--yes`) —
    all genuinely usage-class mistakes. `runner.AllStreamsEmptyError` and `catalog.CatalogError`
    (raised only when `--end` is omitted and resolving it from stored data goes wrong) are
    deliberately **not** wrapped here — they propagate unwrapped, matching `xtract pcap`'s own
    existing exit-code treatment of the same failure (transport-class, not usage-class).
    """


class SpoolDriftError(PipelineError):
    """`spool.verify()`'s accounted-vs-on-disk totals disagree once a run has fully drained.

    Checked once, at the very end of `run_harvest` (see `check_drift`'s docstring for why not
    mid-run) — "fail loud on drift rather than silently exceeding the cap for the rest of a
    multi-day run" (work order §3 Phase 4).
    """


class RetainedFailureLimitExceededError(PipelineError):
    """Too many bytes are RESIDENT-but-never-releasable because Zeek failed on them.

    A failed Zeek run correctly leaves its pcap resident for a future run's retry (the module
    docstring's "one invariant") — but nothing bounded how many of those accumulate *within one
    run*, and a systematic downstream failure (Zeek itself broken, a bad image, ...) means every
    single one fails and none is ever released. Measured 2026-08-07: a live run hit 3 retained
    failures totalling 79.6 MB against a 60 MB cap, leaving the spool permanently over its
    ceiling with nothing able to release the space for the rest of that run. Raised as soon as
    retained failure bytes exceed `HarvestConfig.retained_failure_fraction` of `spool_cap_bytes`,
    so this surfaces immediately as a loud, diagnosable error instead of silently wedging a
    multi-day run — see `_RunStats.check_retained_failure_limit`.
    """


# --- concurrency defaults, measured not guessed (docs/PLAN_metadata_harvester.md §5,
# docs/WORK_ORDER_pipeline.md §3) ---------------------------------------------------------------

# Superseded 2026-08-07 by a live-run measurement: the appliance serves exactly one extract at a
# time. A concurrent second (or third, ...) request to `GET /axapi/pcap` gets a `200 OK` whose
# body is `b"Error starting listen thread, shutting down thread"` repeated to fill the response —
# no other signal distinguishes it from a real capture (see `extract.py`'s `PCAP_MAGICS` check,
# added because of exactly this). The earlier "8 -> 50.3 MB/s, near-linear" note below was that
# error text counted as payload; measured *with* validation (`extract.py` rejecting the
# non-magic body):
#
#   concurrency 1:  1 valid, 0 corrupt
#   concurrency 2:  1 valid, 1 CORRUPT
#   concurrency 4:  1 valid, 3 CORRUPT
#   concurrency 8:  1 valid, 7 CORRUPT
#
# Real throughput is ~13.9 MB/s and does not parallelize — concurrency > 1 does not speed
# extraction, it manufactures corrupt output at a rate matching (concurrency - 1) / concurrency.
# The flag itself (`--concurrency`) stays, in case a future appliance/firmware genuinely serves
# concurrent extracts; anyone raising this default must re-measure with validation first (the
# table above, not the throughput number alone — a naive throughput measurement is exactly what
# produced the wrong "50.3 MB/s, scales linearly" conclusion the first time).
DEFAULT_EXTRACT_CONCURRENCY = 1

# Zeek workers default conservatively relative to this host's 32 cores: one Zeek run processed
# 2.87 MB in 0.83s (~3.5 MB/s single-worker), so 4 concurrent gives Zeek a nominal throughput
# (~14 MB/s) comfortably above the appliance's own measured extraction ceiling (~13.9 MB/s,
# single-extract-at-a-time — see `DEFAULT_EXTRACT_CONCURRENCY`'s 2026-08-07 note) — meaning Zeek
# is not expected to be the bottleneck under this default. Left at 4 rather than re-tuned down:
# extra Zeek headroom is harmless (workers simply idle waiting on the single-extract-at-a-time
# upstream), and `--zeek-workers` remains the knob if a future appliance changes the extraction
# ceiling.
DEFAULT_ZEEK_WORKERS = 4

# A failed Zeek run retains its pcap for retry (see module docstring's "one invariant") — but
# nothing bounds how many accumulate within a single run. Measured 2026-08-07: a live run hit 3
# retained failures totalling 79.6 MB against a 60 MB cap, leaving the spool permanently over its
# ceiling with nothing able to release them (a failed window is not retried again mid-run — only
# a future run's Phase-5 orphan re-drive attempts it). Default: abort the run as soon as retained
# failure bytes exceed half the cap, so a systematic downstream failure (Zeek itself broken, a bad
# image, ...) surfaces immediately as a loud, diagnosable error instead of silently wedging a
# multi-day run over its ceiling for its entire remaining duration.
DEFAULT_RETAINED_FAILURE_FRACTION = 0.5

# A resumed run's inherited RESIDENT orphans (Phase 5) keep `_entries` non-empty forever unless
# something is genuinely in flight to release them — `Spool`'s own `stall_timeout` guard exists
# for exactly this. 30 minutes is generous headroom beyond one worst-case single-window Zeek
# timeout (`zeek.DEFAULT_TIMEOUT_S` = 900s = 15 min): anything genuinely stuck for twice that is a
# structural problem worth surfacing loudly, not a normal multi-day-run slow patch.
DEFAULT_STALL_TIMEOUT_S = 1800.0

# Measured 2026-08-06: a 300s window on ONE stream ran 45.7 MB (`docs/PLAN_metadata_harvester.md`
# §5). The spool estimator's seed is scaled from this baseline by stream count and by the
# requested interval relative to 300s — see `seed_bytes_for`.
_BASELINE_WINDOW_S = 300
_BASELINE_BYTES_PER_STREAM = 45_700_000

_ZEEK_SENTINEL = object()


def seed_bytes_for(streams: str, interval: Duration) -> int:
    """Seed `SizeEstimator` from reality, not `spool.py`'s generic 64 MiB placeholder.

    Scales the measured single-stream, 300s-window baseline (45.7 MB) by the number of streams
    the spec expands to and by `interval` relative to 300s, rounding up (ceiling division): under-
    estimating overshoots the cap, over-estimating only costs throughput, so erring high is the
    requirement here, not a preference.
    """
    stream_count = len(streamspec.expand(streams))
    numerator = _BASELINE_BYTES_PER_STREAM * stream_count * int(interval)
    denominator = _BASELINE_WINDOW_S * NS_PER_SECOND
    seed = -(-numerator // denominator)  # ceiling division, integers only
    return max(seed, 1)


@dataclass(frozen=True)
class HarvestConfig:
    """Everything `harvest` needs, resolved from CLI flags but not yet from the clock or the
    catalog — `start_expr`/`end_expr` are still `timespec` expressions (`"now"`, `"30 days ago"`,
    an absolute epoch), resolved once by `plan_harvest` against a single `now` the caller supplies.
    """

    streams: str
    start_expr: str
    end_expr: str | None
    interval: Duration
    out_dir: Path
    spool_cap_bytes: int
    filter: str | None = None
    extract_concurrency: int = DEFAULT_EXTRACT_CONCURRENCY
    zeek_workers: int = DEFAULT_ZEEK_WORKERS
    yes: bool = False
    fail_fast: bool = False
    admit_timeout_s: float | None = None
    stall_timeout_s: float | None = DEFAULT_STALL_TIMEOUT_S
    retained_failure_fraction: float = DEFAULT_RETAINED_FAILURE_FRACTION


@dataclass(frozen=True)
class HarvestPlan:
    """The read-only result of resolving `HarvestConfig` against a `now` and (if `--end` was
    omitted) the catalog: the exact windows to extract, plus the estimator seed for the spool
    this run will construct. Computing this makes no request against `/axapi/pcap` and runs no
    Zeek container — safe to compute for `--dry-run`.
    """

    start: Instant
    end: Instant
    windows: list[Window]
    seed_bytes: int


def _spool_root(out_dir: Path) -> Path:
    return out_dir / "spool"


def _zeek_root(out_dir: Path) -> Path:
    return out_dir / "zeek"


def _record_vantage(zeek_root: Path, record: Vantage) -> None:
    """Write the vantage manifest beside the cold tier (`aximprint.vantage`).

    **Written before the first window is extracted, not after the last one succeeds.** An
    interrupted harvest still produced real Zeek logs, and those logs came from a real tap; a
    manifest written only on a clean finish would leave exactly the long, interrupted runs this is
    for describing themselves as "unknown vantage". Overwriting on a resume is correct for the same
    reason — appending windows to a tree does not change which appliance they came from.

    `<zeek_root>/vantage.json`, i.e. beside the per-window directories rather than inside one:
    `zeek_root` is what `aximprint ingest --source` is pointed at, and a manifest one level up from
    that would be invisible to the reader (`ingest/zeek/sources.py`).

    This is a **sensor artifact on disk**, which is exactly what `acquire` is for — writing it
    creates no dependency on the discovery pipeline. `aximprint.vantage` sits below every layer and
    imports nothing from `aximprint`, the same footing as `provenance.py`
    (`tests/test_acquire_layering.py` classifies it).
    """
    write_vantage(zeek_root, record)


def plan_harvest(config: HarvestConfig, *, client, now: Instant) -> HarvestPlan:
    """Resolve `--streams`/`--start`/`--end`/`--interval` into a `HarvestPlan`.

    `client` is only used when `config.end_expr is None` (resolving `--end` to the union max of
    stored data, via `runner.resolve_end` — reused unchanged, no new HTTP code); pass `client=None`
    when `--end` is explicit (the `--dry-run`-with-explicit-`--end` case needs no credentials at
    all, same as `xtract pcap`).
    """
    try:
        streamspec.validate(config.streams)
    except streamspec.StreamSpecError as exc:
        raise PlanNotComputableError(f"invalid --streams spec: {exc}") from exc

    try:
        start = timespec.resolve_start(config.start_expr, now=now)
    except timespec.TimeSpecError as exc:
        raise PlanNotComputableError(f"cannot resolve --start: {exc}") from exc

    if config.end_expr is not None:
        try:
            end = timespec.resolve_end(config.end_expr, start=start, now=now)
        except timespec.TimeSpecError as exc:
            raise PlanNotComputableError(f"cannot resolve --end: {exc}") from exc
    else:
        if client is None:
            raise PlanNotComputableError(
                "no --end given and no client available to resolve it from stored data"
            )
        stream_ids = streamspec.expand(config.streams)
        # AllStreamsEmptyError/CatalogError propagate unwrapped — see PlanNotComputableError's
        # docstring for why these two specifically are not folded into it.
        resolved = runner.resolve_end(client, stream_ids, config.interval)
        end = resolved.end

    try:
        plan_windows = windows.plan(start, end, config.interval, max_windows=windows.max_windows_for(config.yes))
    except windows.WindowsError as exc:
        raise PlanNotComputableError(f"cannot compute window plan: {exc}") from exc

    seed = seed_bytes_for(config.streams, config.interval)
    return HarvestPlan(start=start, end=end, windows=plan_windows, seed_bytes=seed)


def _check_writable(root: Path, error_cls: type[PreflightError], label: str) -> None:
    """Shared shape behind `_check_spool_root_writable`/`_check_zeek_root_writable`: create
    `root` if missing, write and remove one throwaway probe file, and raise `error_cls` naming
    `label` (`"spool root"`/`"zeek root"`) on any `OSError` — never surface a raw `OSError` from
    this class of check (`unexpected error`/exit `4` in `cli.py`) when a typed, Phase-1
    `PreflightError` says the same thing more usefully.
    """
    try:
        root.mkdir(parents=True, exist_ok=True)
        probe_path = root / f".xtract-writable-probe-{uuid.uuid4().hex}"
        probe_path.write_bytes(b"")
        probe_path.unlink()
    except OSError as exc:
        raise error_cls(f"{label} {root} is not writable: {exc}") from exc


def _check_spool_root_writable(out_dir: Path) -> None:
    _check_writable(_spool_root(out_dir), SpoolRootNotWritableError, "spool root")


def _check_zeek_root_writable(out_dir: Path) -> None:
    _check_writable(_zeek_root(out_dir), ZeekRootNotWritableError, "zeek root")


def _check_zeek_probe(zeek_runner: ZeekRunner) -> None:
    """`zeek_runner.probe()` must be `ok` — the one Phase-1 precondition `run_harvest_from_pcaps`
    (`docs/WORK_ORDER_harvest_from_pcaps.md`) shares with `preflight()` below: extracted so the
    check (and its error) is expressed exactly once and called by both, rather than a `--from-pcaps`
    run reimplementing it against a `HarvestConfig`/`client`/spool it does not have.
    """
    probe = zeek_runner.probe()
    if not probe.ok:
        raise ZeekProbeFailedError(f"zeek runner failed its startup probe: {probe.detail}")


def preflight(config: HarvestConfig, *, client, zeek_runner: ZeekRunner) -> None:
    """Phase 1's four checks (see module docstring): `zeek_runner.probe()`, spool-root
    writability, zeek-root writability, and credentials — in that order, so whichever fails first
    is reported without paying for the checks after it. Raises before creating a `Spool` or
    starting a single worker; `run_harvest` calls this before either exists, and never needs to
    `mkdir` the zeek root itself afterward — this already did.
    """
    _check_zeek_probe(zeek_runner)

    _check_spool_root_writable(config.out_dir)
    _check_zeek_root_writable(config.out_dir)

    # "Credentials must resolve": one cheap, already-existing, real call — reused rather than this
    # module inventing a parallel credential-resolution path. Any exception it raises
    # (AuthenticationError, UnauthorizedError, CatalogError, ...) propagates unwrapped, so the
    # CLI's existing exit-code table classifies it exactly like every other xtract command already
    # does with the same exception types.
    catalog.list_streams(client)


def check_drift(spool: Spool) -> None:
    """Compare `spool.verify()`'s accounted-vs-on-disk totals and raise `SpoolDriftError` if they
    disagree.

    Called exactly once, at the very end of `run_harvest`, **not** on a fixed interval mid-run:
    `Spool.verify()`'s own docstring documents that the two numbers are *expected* to disagree
    transiently while any entry is RESERVED (an estimate with no file landed yet, or a file still
    mid-write) — which is true for essentially the entire duration of a concurrent run. By the time
    every extract worker and every Zeek worker has finished, no RESERVED entries remain (every
    window is either fully released or RESIDENT because Zeek failed and is awaiting retry), so a
    disagreement at that point is a genuine, structural drift — not the ordinary transient case —
    and is exactly what this is for.
    """
    accounted, on_disk = spool.verify()
    if accounted != on_disk:
        raise SpoolDriftError(
            f"spool accounting drift in {spool.root}: {accounted} bytes accounted for vs "
            f"{on_disk} bytes actually on disk — the bookkeeping and the filesystem disagree"
        )


# --- run report and thread-safe accumulation -----------------------------------------------------


@dataclass
class RunReport:
    """The final accounting `cmd_harvest` reports and picks an exit code from
    (work order §3 Phase 4).

    `extracted`/`empty`/`zeeked`/`released` are all successful outcomes — **an empty window is
    success** (`LES-035`), never counted in `extract_failed`. `failed` (a property, not a stored
    field) is the sum of `extract_failed` and `zeek_failed`: either kind means the window did not
    complete this run (a failed Zeek run's pcap stays resident, retried on a future run's Phase-5
    re-drive — `zeek_failed_windows` names them). `unrecognized_stems` is the union, across every
    Zeek run this call made (successes and failures both), of log stems outside
    `zeek.KNOWN_ZEEK_STEMS` — a Zeek upgrade producing a new log type must be visible here, not
    silently generated-and-ignored.
    """

    planned: int
    extracted: int
    empty: int
    extract_failed: int
    zeeked: int
    zeek_failed: int
    released: int
    reconciled_orphans: int
    bytes_moved: int
    elapsed_s: float
    peak_spool_bytes: int
    unrecognized_stems: tuple[str, ...]
    interrupted: bool
    failed_windows: tuple[str, ...]
    zeek_failed_windows: tuple[str, ...]
    retained_failed_bytes: int

    @property
    def failed(self) -> int:
        return self.extract_failed + self.zeek_failed


class _RunStats:
    """Thread-safe accumulation shared by every extract and Zeek worker — one lock, every mutation
    under it, same discipline `spool.py` uses for its own accounting.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.extracted = 0
        self.empty = 0
        self.extract_failed = 0
        self.zeeked = 0
        self.zeek_failed = 0
        self.released = 0
        self.reconciled_orphans = 0
        self.bytes_moved = 0
        self.peak_spool_bytes = 0
        self.retained_failed_bytes = 0
        self._unrecognized: set[str] = set()
        self._failed_windows: list[str] = []
        self._zeek_failed_windows: list[str] = []
        # Set at most once, by whichever Zeek worker first pushes `retained_failed_bytes` past
        # `HarvestConfig.retained_failure_fraction` of the cap — see
        # `check_retained_failure_limit`. Read (via `zeek_fatal_snapshot`) only after every Zeek
        # worker thread has been joined, so no lock is needed at that read site.
        self.zeek_fatal: RetainedFailureLimitExceededError | None = None
        # Set at most once, by whichever Zeek worker thread first lets an exception escape
        # `_process_zeek_item` (`_zeek_worker_loop`'s own exception guard) — same "first one wins,
        # later ones are no-ops" discipline as `zeek_fatal`. Read (via `worker_fatal_snapshot`)
        # only after every Zeek worker thread has been joined, so no lock is needed at that read
        # site. A worker thread that dies silently on an uncaught exception must not be able to
        # leave its window (or the run's overall exit code) looking like an ordinary success.
        self.worker_fatal: BaseException | None = None

    def record_extracted(self, nbytes: int) -> None:
        with self._lock:
            self.extracted += 1
            self.bytes_moved += nbytes

    def record_empty(self) -> None:
        with self._lock:
            self.empty += 1

    def record_extract_failed(self, window_id: str) -> None:
        with self._lock:
            self.extract_failed += 1
            self._failed_windows.append(window_id)

    def record_zeek_outcome(self, window_id: str, result: ZeekResult, *, failed_bytes: int = 0) -> None:
        """`failed_bytes` (the pcap's real on-disk size — ignored when `result.ok`) is what
        `check_retained_failure_limit` compares against the cap; the caller measures it, this
        method only accumulates it, same division of responsibility as `spool.commit()`'s own
        caller-measures/spool-accumulates split.

        Does **not** touch `released` — a successful Zeek run does not imply a release (an
        `owned=False` `--from-pcaps` item never calls `spool.release()` at all, see
        `_process_zeek_item`); call `record_released()` at the actual `spool.release()` call site
        instead, measured there rather than inferred here from `result.ok`.
        """
        with self._lock:
            if result.unrecognized:
                self._unrecognized.update(result.unrecognized)
            if result.ok:
                self.zeeked += 1
            else:
                self.zeek_failed += 1
                self._zeek_failed_windows.append(window_id)
                self.retained_failed_bytes += failed_bytes

    def record_released(self) -> None:
        """Counts one actual `spool.release()` call — see `record_zeek_outcome`'s docstring for
        why this is a separate method rather than folded into it: `released` must mean "space was
        actually reclaimed", not "Zeek succeeded", and the two are no longer the same claim once
        an `owned=False` item can succeed without ever being released.
        """
        with self._lock:
            self.released += 1

    def check_retained_failure_limit(
        self, config: HarvestConfig
    ) -> RetainedFailureLimitExceededError | None:
        """Called after every `record_zeek_outcome` failure: if `retained_failed_bytes` now
        exceeds `config.retained_failure_fraction` of `config.spool_cap_bytes`, builds (and
        remembers, via `zeek_fatal` — first failure past the threshold wins, later ones are
        no-ops) a `RetainedFailureLimitExceededError` and returns it; returns `None` otherwise,
        including when the threshold was already tripped by an earlier call. The caller
        (`_process_zeek_item`) uses a non-`None` return to signal `stop_requested` — this method
        itself never touches threading events, only accounting, same as every other `_RunStats`
        method.
        """
        with self._lock:
            threshold = int(config.spool_cap_bytes * config.retained_failure_fraction)
            if self.retained_failed_bytes <= threshold or self.zeek_fatal is not None:
                return None
            exc = RetainedFailureLimitExceededError(
                f"retained Zeek-failure bytes ({self.retained_failed_bytes}) exceed "
                f"{config.retained_failure_fraction:.0%} of the spool cap "
                f"({config.spool_cap_bytes} bytes, threshold {threshold} bytes) across "
                f"{self.zeek_failed} failed window(s) {tuple(self._zeek_failed_windows)!r} — "
                "aborting the run rather than silently wedging it over its ceiling with nothing "
                "able to release the retained space"
            )
            self.zeek_fatal = exc
            return exc

    def zeek_fatal_snapshot(self) -> RetainedFailureLimitExceededError | None:
        with self._lock:
            return self.zeek_fatal

    def record_worker_fatal(self, exc: BaseException) -> None:
        """Record an exception a Zeek worker thread let escape while processing one item — first
        one wins (later ones are no-ops), same discipline as `zeek_fatal`/
        `check_retained_failure_limit`. Called from `_zeek_worker_loop`'s own exception guard;
        `run_harvest`/`run_harvest_from_pcaps` re-raise this, unwrapped, once every Zeek worker
        thread has been joined (`worker_fatal_snapshot`).
        """
        with self._lock:
            if self.worker_fatal is None:
                self.worker_fatal = exc

    def worker_fatal_snapshot(self) -> BaseException | None:
        with self._lock:
            return self.worker_fatal

    def observe_spool_usage(self, usage: int) -> None:
        with self._lock:
            self.peak_spool_bytes = max(self.peak_spool_bytes, usage)

    def to_report(self, *, planned: int, elapsed_s: float, interrupted: bool) -> RunReport:
        with self._lock:
            return RunReport(
                planned=planned,
                extracted=self.extracted,
                empty=self.empty,
                extract_failed=self.extract_failed,
                zeeked=self.zeeked,
                zeek_failed=self.zeek_failed,
                released=self.released,
                reconciled_orphans=self.reconciled_orphans,
                bytes_moved=self.bytes_moved,
                elapsed_s=elapsed_s,
                peak_spool_bytes=self.peak_spool_bytes,
                unrecognized_stems=tuple(sorted(self._unrecognized)),
                interrupted=interrupted,
                # Sorted, matching `unrecognized_stems` above — both lists are otherwise built in
                # whichever order concurrent worker threads happened to finish in, which is not
                # deterministic run to run over identical input (CONVENTIONS §3).
                failed_windows=tuple(sorted(self._failed_windows)),
                zeek_failed_windows=tuple(sorted(self._zeek_failed_windows)),
                retained_failed_bytes=self.retained_failed_bytes,
            )


# --- the zeek stage ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _ZeekWorkItem:
    window_id: str
    pcap_path: Path
    # D3, `docs/WORK_ORDER_harvest_from_pcaps.md`: a **per-entry** predicate, not a run-level flag
    # — a resumed run can have Phase-5 orphans it owns sitting in the same queue shape a future
    # mixed run might feed alongside pcaps it does not (H-7). `True` for `_extract_one` and
    # `run_harvest`'s own orphan re-drive loop; `False` only for `run_harvest_from_pcaps`'s items:
    # a `--from-pcaps`-supplied pcap this pipeline never admitted into any `Spool` and must never
    # delete. See `_process_zeek_item` for what this gates.
    #
    # Deliberately REQUIRED, no default: this field gates a delete permission (CONVENTIONS §3,
    # "fail loud, fail closed" — a permission check must never fail open). A default of `True`
    # would make "forgot to consider ownership at a new call site" silently equivalent to "this
    # pipeline owns it and may delete it", the exact class of mistake this field exists to make
    # impossible; every one of the three call sites must now say which it means, or the
    # constructor raises `TypeError` rather than guessing.
    owned: bool


def _fresh_zeek_out_dir(zeek_root: Path, window_id: str) -> Path:
    """A genuinely fresh directory for `zeek_runner.run()` — `zeek.py`'s own contract rejects a
    directory that already has `*.log` files in it (module docstring: "each Zeek run needs a
    fresh output directory"). Naming stays stable (`<zeek_root>/<window_id>/`, easy for a human to
    find) across retries: any stale `*.log` left by a previous failed attempt — this run's own
    retry-via-Phase-5-orphan-redrive on a later run, or (in principle) a second attempt within one
    process — is cleared first rather than treated as a permanently poisoned path. Discarding a
    stale partial log set is correct here: Zeek has no incremental-resume mode, so a retry always
    reprocesses the whole pcap and produces a full, superseding log set anyway.
    """
    out_dir = zeek_root / window_id
    # Belt-and-suspenders against a validation bug in `plan_from_pcaps` (or a future call site
    # that forgets to validate), same role as `Spool._path_for`'s assertion — this function also
    # deletes files (the stale-`*.log` sweep below), and since
    # `docs/WORK_ORDER_harvest_from_pcaps.md` its `window_id` can come from a filename stem in a
    # user-controlled directory.
    #
    # Compared RESOLVED, which `_path_for` does not need to do and this does: `_path_for`
    # interpolates a suffix (`f"{window_id}.pcap"`), so `..` becomes the ordinary filename
    # `"...pcap"` and can never be the final component. Here the bare `window_id` IS the final
    # component, and `(zeek_root / "..").parent == zeek_root` is **True** — pathlib does not
    # normalize `..`. An unresolved parent check would therefore admit the one traversal that
    # actually reaches the unlink loop, while looking like it defended against it.
    assert out_dir.resolve().parent == zeek_root.resolve(), f"constructed path escaped zeek root: {out_dir!r}"
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in out_dir.glob("*.log"):
        stale.unlink(missing_ok=True)
    return out_dir


def _process_zeek_item(
    item: _ZeekWorkItem,
    zeek_runner: ZeekRunner,
    spool: Spool | None,
    zeek_root: Path,
    stats: _RunStats,
    on_event: Callable[[str, str, str], None] | None,
    config: HarvestConfig | None,
    stop_requested: threading.Event,
) -> None:
    """The real contract on `spool`/`config` is narrower than their types suggest: **an
    `item.owned=False` item never dereferences either**, by construction — every read of `spool`
    or `config` below is reached exclusively through an `if item.owned:` branch. That is what lets
    `run_harvest_from_pcaps` pass `spool=None`/`config=None` (there is no `Spool`/`HarvestConfig`
    in that mode at all), and it is *also* what will let a future mixed run (H-7) pass a REAL,
    populated `Spool` alongside `owned=False` items without those items ever touching it —
    `owned=False` and `spool=None` happen to travel together today only because `--from-pcaps` is
    the sole producer of `owned=False` items, not because this function assumes the pairing. See
    `tests/acquire/test_pipeline.py`'s `test_process_zeek_item_owned_false_never_calls_release_
    even_when_the_spool_has_a_real_entry` for exactly that mixed case, exercised directly. An
    `owned=True` item (`_extract_one`, `run_harvest`'s own Phase-5 re-drive loop) is unaffected by
    any of this: same calls, same arguments, as before this function grew the ownership check.
    """
    out_dir = _fresh_zeek_out_dir(zeek_root, item.window_id)
    result = zeek_runner.run(item.pcap_path, out_dir)

    if result.ok:
        stats.record_zeek_outcome(item.window_id, result)
        if item.owned:
            # THE invariant this module exists to protect: delete only after Zeek has succeeded
            # — AND only for a pcap this pipeline itself admitted (D3,
            # docs/WORK_ORDER_harvest_from_pcaps.md). A `--from-pcaps`-supplied pcap is never
            # admitted into any `Spool`'s accounting in the first place (see
            # `run_harvest_from_pcaps`) — this is not "release with delete=False", it is not
            # calling release() AT ALL, so a `--from-pcaps` run's correctness never depends on
            # `spool` tracking that pcap right; there is no `spool` to get it wrong.
            spool.release(item.window_id, delete=True)
            # Measured at the actual release, never inferred from `result.ok` — a Zeek success
            # does not by itself mean anything was reclaimed (see `record_zeek_outcome`'s own
            # docstring for the bug this replaced: `released` used to increment unconditionally
            # alongside `zeeked`, so a real `--from-pcaps` run reported "released" for pcaps that
            # were never touched).
            stats.record_released()
            event, detail = "zeeked", ""
        else:
            # Same successful Zeek outcome, but nothing was released — the distinct event name
            # is what lets `cli.py` print "source pcap retained" instead of "released" and
            # actually be telling the truth.
            event, detail = "zeeked_kept", ""
    else:
        # Never release() on failure for an owned pcap — it stays RESIDENT, still counted
        # against the cap, so a future run's Phase-5 orphan re-drive retries it. A timeout is a
        # failure here too (`ZeekResult.ok` is `returncode == 0` only — see `zeek.py`), never
        # treated as success. A `--from-pcaps` pcap that fails Zeek needed no release to begin
        # with (never admitted) — it simply stays exactly where the operator put it, exactly as
        # untouched as one whose Zeek run succeeded.
        if item.owned:
            # `failed_bytes` is measured here (the file is still resident, whatever `commit()`
            # last recorded as its real size) rather than trusted from anywhere else — same
            # caller-measures discipline `spool.commit()` itself uses. Retained-failure
            # accounting is an owned-pcap concept too (it bounds how much of *this run's own
            # spool cap* is stuck unreleasable) — a `--from-pcaps` failure has no cap to bound.
            failed_bytes = item.pcap_path.stat().st_size if item.pcap_path.exists() else 0
            stats.record_zeek_outcome(item.window_id, result, failed_bytes=failed_bytes)
            if stats.check_retained_failure_limit(config) is not None:
                # Stop admitting new extract work immediately — every further extraction only
                # risks producing another retained failure once accounting has already crossed
                # the threshold. The exception itself is raised later, from `run_harvest`, once
                # every Zeek worker has drained (see `zeek_fatal_snapshot`).
                stop_requested.set()
        else:
            stats.record_zeek_outcome(item.window_id, result)
        event, detail = "zeek_failed", result.stderr_tail

    if spool is not None:
        stats.observe_spool_usage(spool.usage())
    if on_event is not None:
        on_event(item.window_id, event, detail)


def _zeek_worker_loop(
    work_queue: queue.Queue,
    zeek_runner: ZeekRunner,
    spool: Spool | None,
    zeek_root: Path,
    stats: _RunStats,
    on_event: Callable[[str, str, str], None] | None,
    config: HarvestConfig | None,
    stop_requested: threading.Event,
    *,
    drain_on_stop: bool = False,
) -> None:
    """`drain_on_stop` (only ever `True` for `run_harvest_from_pcaps`, ruling A of
    `docs/WORK_ORDER_harvest_from_pcaps.md`): once `stop_requested` is set, a dequeued item is
    drained (`task_done()`, never run through Zeek) instead of processed. `run_harvest`'s own Zeek
    queue must always fully drain every already-queued item regardless of `stop_requested` — those
    pcaps are disk-resident and cap-consuming, and draining them (not skipping them) is how their
    space gets reclaimed (see the module docstring) — so `drain_on_stop` stays `False`, the
    default, for every one of ITS threads. `--from-pcaps` has no cap and nothing resident, so that
    justification does not transfer: `--fail-fast`/a SIGINT there should actually stop the run
    (`run_harvest_from_pcaps` sets `stop_requested` for both).

    Any exception `_process_zeek_item` lets escape is caught here, recorded via
    `stats.record_worker_fatal` (mirroring `zeek_fatal`/`zeek_fatal_snapshot`), and the loop keeps
    consuming the queue — an uncaught exception must not silently kill a worker thread and leave
    its window (and, worst case, this thread's whole remaining share of the queue) unaccounted for
    while the run still reports success.
    """
    while True:
        item = work_queue.get()
        try:
            if item is _ZEEK_SENTINEL:
                return
            if drain_on_stop and stop_requested.is_set():
                continue
            try:
                _process_zeek_item(item, zeek_runner, spool, zeek_root, stats, on_event, config, stop_requested)
            except BaseException as exc:  # noqa: BLE001 -- recorded, re-raised once every worker has drained
                stats.record_worker_fatal(exc)
        finally:
            work_queue.task_done()


# --- the extract stage -------------------------------------------------------------------------


def _ctid_for(window_id: str) -> str:
    return f"xtract-harvest-{window_id}"


def _extract_one(
    client,
    window: Window,
    config: HarvestConfig,
    spool: Spool,
    zeek_queue: queue.Queue,
    stats: _RunStats,
    on_event: Callable[[str, str, str], None] | None,
    stop_requested: threading.Event,
) -> None:
    """One extract worker's full handling of one window: `admit` (blocks — this is the
    backpressure) -> `extract_window` -> `commit`/`fail` -> hand off to the Zeek queue.

    Any exception from `admit()` itself (no reservation exists yet, nothing to clean up)
    propagates directly. Any exception from `extract_window` — `KeyboardInterrupt`,
    `AuthenticationError`/`UnauthorizedError` (re-raised there after retry/re-login is exhausted),
    or anything else — frees the reservation via `fail()` first (the extraction produced nothing,
    so `fail()`, never `release()`) and then re-raises, so the caller sees it via `Future.result()`.
    """
    wire = window.wire_params()
    window_id = wire["start_time"]
    ctid = _ctid_for(window_id)

    reservation = spool.admit(window_id, timeout=config.admit_timeout_s)
    stats.observe_spool_usage(spool.usage())

    try:
        outcome = extract_window(client, window, config.streams, config.filter, spool.root, ctid)
    except BaseException:
        spool.fail(window_id)
        raise

    if isinstance(outcome, Written):
        spool.commit(window_id)  # stats the real file at reservation.path
        stats.observe_spool_usage(spool.usage())
        stats.record_extracted(outcome.bytes)
        zeek_queue.put(_ZeekWorkItem(window_id=window_id, pcap_path=reservation.path, owned=True))
        if on_event is not None:
            on_event(window_id, "extracted", "")
    elif isinstance(outcome, Empty):
        # LES-035's sibling here: 418/zero-byte is a normal outcome. Nothing landed, so the
        # reservation is abandoned via fail() — release() is only legal on a RESIDENT entry.
        spool.fail(window_id)
        stats.observe_spool_usage(spool.usage())
        stats.record_empty()
        if on_event is not None:
            on_event(window_id, "empty", outcome.reason)
    else:
        assert isinstance(outcome, Failed)
        spool.fail(window_id)
        stats.observe_spool_usage(spool.usage())
        stats.record_extract_failed(window_id)
        if on_event is not None:
            on_event(window_id, "extract_failed", outcome.detail)
        if config.fail_fast:
            stop_requested.set()


def run_harvest(
    config: HarvestConfig,
    *,
    plan: HarvestPlan,
    client,
    zeek_runner: ZeekRunner,
    on_event: Callable[[str, str, str], None] | None = None,
) -> RunReport:
    """Run the full pipeline for `plan` (from `plan_harvest`) and return a `RunReport`.

    Preflight (`preflight()`) runs first, unconditionally, before a `Spool` exists or a single
    worker starts. Then, before this run's own extraction begins, every pcap `Spool` reconciled
    from a prior interrupted run (`spool.reconciled_windows` — Phase 5's "inherited orphans") is
    queued for Zeek: re-driven, not reaped, since extraction is the expensive half already paid
    for and Zeek is what never got to run on them.

    On `KeyboardInterrupt` (SIGINT): stops admitting new extract work (the sliding-window
    submission loop below simply never submits another window), lets everything already
    dispatched (extraction in flight, and the whole Zeek queue, including anything already
    committed) finish naturally, then returns a `RunReport` with `interrupted=True` — never
    raises. `--fail-fast` on a `Failed` extraction
    behaves the same way structurally (`stop_requested`, checked after each completed extraction)
    but is reported as an ordinary completed run (`interrupted=False`, `failed > 0`) rather than an
    interruption. Any other exception surfacing from a worker (an unrecoverable auth failure, a
    `SpoolError` indicating the spool itself is misconfigured or stuck) triggers the same graceful
    drain and is then re-raised, unwrapped, once draining is complete.
    """
    preflight(config, client=client, zeek_runner=zeek_runner)

    spool_root = _spool_root(config.out_dir)
    zeek_root = _zeek_root(config.out_dir)
    # No `zeek_root.mkdir(...)` here — `preflight()`'s own `_check_zeek_root_writable` already
    # created (and probed the writability of) `zeek_root`, before this function had a `Spool` or a
    # worker of its own.

    # The one place this pipeline knows BOTH halves of the tap identity — which appliance
    # (`client.host`) and which streams (`config.streams`). Before this, both were discarded at the
    # disk boundary and every PIT downstream described itself by filesystem path alone
    # (`docs/WORK_ORDER_visibility_and_vantage.md` D1/D2, measured: `ir_pit.source` was
    # `/tmp/aximprint-zeek-test`, which says nothing about where the tap sat).
    _record_vantage(zeek_root, Vantage(
        vantage=vantage_id(client.host, config.streams),
        appliance=client.host, streams=config.streams, produced_by="harvest",
        planned_from=plan.start.ns / NS_PER_SECOND, planned_to=plan.end.ns / NS_PER_SECOND,
        tool_version=__version__))

    estimator = SizeEstimator(seed_bytes=plan.seed_bytes)
    spool = Spool(spool_root, config.spool_cap_bytes, estimator=estimator, stall_timeout=config.stall_timeout_s)

    stats = _RunStats()
    stats.reconciled_orphans = len(spool.reconciled_windows)

    # Hoisted above the Zeek threads' own construction (rather than declared where the extract
    # side first uses it, further down) — the Zeek side needs the same event too, to stop the
    # extract loop admitting further work the moment `check_retained_failure_limit` trips (see
    # `_process_zeek_item`).
    stop_requested = threading.Event()

    zeek_queue: queue.Queue = queue.Queue()
    zeek_threads = [
        threading.Thread(
            target=_zeek_worker_loop,
            args=(zeek_queue, zeek_runner, spool, zeek_root, stats, on_event, config, stop_requested),
            name=f"xtract-harvest-zeek-{i}",
        )
        for i in range(config.zeek_workers)
    ]
    for t in zeek_threads:
        t.start()

    # Phase 5: re-drive every window this spool inherited from a prior run before this run's own
    # extraction gets going — see the docstring above.
    for window_id in spool.reconciled_windows:
        zeek_queue.put(_ZeekWorkItem(window_id=window_id, pcap_path=spool.root / f"{window_id}.pcap", owned=True))

    interrupted = False
    fatal: BaseException | None = None
    loop_start = time.monotonic()

    try:
        with ThreadPoolExecutor(
            max_workers=config.extract_concurrency, thread_name_prefix="xtract-harvest-extract"
        ) as extract_pool:
            # A sliding window of at most `extract_concurrency` in-flight futures, submitted
            # incrementally from this thread only — **not** all of `plan.windows` submitted
            # upfront. `ThreadPoolExecutor` workers dequeue eagerly: a worker freed by one task's
            # completion starts the *next already-queued* task immediately, racing ahead of
            # whatever this thread does with that task's result — so a design that submits
            # everything upfront and relies on `Future.cancel()` after the fact cannot reliably
            # "stop admitting new work" (a freed worker can already be running window N+1 before
            # this thread has even seen window N's outcome). Submitting one window at a time, only
            # after checking `stop_requested`/interrupted/fatal here, closes that race: nothing
            # starts unless this thread itself decides to start it.
            windows_iter = iter(plan.windows)
            in_flight: dict[Future, Window] = {}

            def _submit_next() -> bool:
                if stop_requested.is_set():
                    return False
                try:
                    window = next(windows_iter)
                except StopIteration:
                    return False
                fut = extract_pool.submit(
                    _extract_one, client, window, config, spool, zeek_queue, stats, on_event, stop_requested
                )
                in_flight[fut] = window
                return True

            for _ in range(config.extract_concurrency):
                if not _submit_next():
                    break

            try:
                while in_flight:
                    done, _pending = wait(in_flight.keys(), return_when=FIRST_COMPLETED)
                    for fut in done:
                        del in_flight[fut]
                        try:
                            fut.result()
                        except KeyboardInterrupt:
                            interrupted = True
                        except BaseException as exc:  # noqa: BLE001 -- propagated once, after a graceful drain
                            fatal = exc
                    if interrupted or fatal is not None or stop_requested.is_set():
                        continue  # keep waiting for whatever's left in flight; submit nothing more
                    while len(in_flight) < config.extract_concurrency:
                        if not _submit_next():
                            break
            except KeyboardInterrupt:
                # The realistic real-SIGINT path: raised from `wait()`'s own blocking call in the
                # main thread, rather than from a specific future's result() (see module
                # docstring's "measured, not guessed" note on why worker threads themselves are
                # not where a real SIGINT is delivered). Nothing more is submitted; the `with`
                # block's own exit still waits for whatever was already in flight to finish.
                interrupted = True
            # Exiting the `with` block waits for every already-running extract task to finish
            # (never force-killed — "no orphan containers" applies to the Zeek side, but the same
            # discipline is kept here for consistency and because a killed extraction leaves a
            # `.part` file `extract.py` itself must clean up, not this module).
    finally:
        # Always drain the Zeek queue, even after a fatal extract-side error: every pcap already
        # committed has real disk space reserved against it and deserves its Zeek pass (and, on
        # success, its release) rather than being abandoned RESIDENT until a future run's Phase-5
        # reconciliation happens to pick it up.
        for _ in zeek_threads:
            zeek_queue.put(_ZEEK_SENTINEL)
        for t in zeek_threads:
            t.join()

    elapsed_s = time.monotonic() - loop_start

    if fatal is None:
        # Checked only after every Zeek worker thread has been joined (above) — whichever thread
        # (if any) called `check_retained_failure_limit` and tripped the threshold recorded it in
        # `stats.zeek_fatal`.
        fatal = stats.zeek_fatal_snapshot()
    if fatal is None:
        # Same "checked only after every Zeek worker thread has joined" timing — a worker that let
        # an exception escape `_process_zeek_item` (`_zeek_worker_loop`'s own exception guard)
        # recorded it here instead of dying silently with the run still reporting success.
        fatal = stats.worker_fatal_snapshot()

    if fatal is not None:
        raise fatal

    check_drift(spool)

    return stats.to_report(planned=len(plan.windows), elapsed_s=elapsed_s, interrupted=interrupted)


# --- `harvest --from-pcaps`: the Zeek stage over pcaps already on disk (`docs/WORK_ORDER_
# harvest_from_pcaps.md`) --------------------------------------------------------------------
#
# `--from-pcaps SRC` swaps out the entire extract stage for a directory listing: `SRC`'s own
# `*.pcap` files ARE the plan. Deliberately its own top-level entry point rather than a branch
# inside `run_harvest` — that function's preflight (credentials, via `catalog.list_streams`),
# `Spool` construction (disk-cap backpressure over a directory this mode never writes a single
# byte into), and window-plan resolution (`--streams`/`--start`/`--end`/`--interval`) are ALL
# inapplicable here (D4 in the work order): branching around every one of them inside one
# function would be harder to read and easier to get wrong than a second, smaller function that
# shares only the pieces genuinely common to both modes (`_ZeekWorkItem`, `_process_zeek_item`,
# `_zeek_worker_loop`, `_fresh_zeek_out_dir`, `_check_zeek_probe`/`_check_zeek_root_writable`,
# `RunReport`).
#
# No `Spool` is ever constructed for this mode — not even an unused one rooted at `<out>/spool`.
# Every discovered pcap is fed to Zeek as an `owned=False` `_ZeekWorkItem` (see that dataclass and
# `_process_zeek_item`), so `spool.release()` is never called for it at all, regardless of the
# Zeek outcome — a `--from-pcaps` run's correctness does not depend on `spool` accounting for
# these pcaps correctly, because there is no `spool` for it to get wrong.
#
# **Why not a real, `commit()`-populated `Spool` marked non-owning, instead of `None`** — the
# decisive argument, recorded here so H-7 (mixed owned/not-owned runs against one `--out`) does
# not have to re-derive it. `Spool.admit()` BLOCKS for backpressure against `cap_bytes` — that is
# its whole job. A `--from-pcaps` run that admitted each supplied pcap (to reuse `Spool`'s
# accounting) would be admitting real bytes against `--spool-cap`'s default (10 GB, D4: rejected
# as a flag here, so unavailable to raise) with **nothing ever calling `release()`** to free room
# for the next one (D3: never released, owned or not). 9.45 GB of supplied pcaps — the exact
# 2026-08-11 live-run figure this work order exists because of — would admit fine; the next
# window would block in `admit()` until `stall_timeout` (30 minutes, `DEFAULT_STALL_TIMEOUT_S`)
# gives up and raises `SpoolTimeoutError`. Not a near-miss: a real, silent, half-hour hang before
# the failure even surfaces. `spool=None` sidesteps the entire class of bug by construction —
# there is no cap to block against because there is no `Spool`.


def _looks_epoch_shaped(window_id: str) -> bool:
    """A best-effort, LOCAL heuristic only (ruling C, `docs/WORK_ORDER_harvest_from_pcaps.md`) —
    `acquire` must not import `ingest` (CONVENTIONS §3 layering), so this cannot ask the real
    ingest-side parser what shape it expects; it can only approximate what `pcap`'s own output
    (`<start_epoch>.pcap`) actually looks like, matching `timeutil.format_instant`'s two output
    forms: a bare integer, or `<seconds>.<9-digit-fraction>`. False positives/negatives here
    change only `plan_from_pcaps`'s plan-time note — never plan or run correctness: every
    `validate_window_id`-safe stem is accepted and Zeek'd regardless of what this returns.
    """
    if window_id.isdigit():
        return True
    whole, sep, frac = window_id.partition(".")
    return bool(sep) and whole.isdigit() and frac.isdigit()


class FromPcapsSourceError(PreflightError):
    """`--from-pcaps SRC` does not resolve to a runnable set of windows: `SRC` is empty/unset,
    not a directory, contains zero `*.pcap` files (CONVENTIONS §3 — an empty source is a usage
    error, not a zero-window success), contains more `*.pcap` files than
    `windows.max_windows_for(False)` without `FromPcapsConfig.yes`, or contains an entry matching
    `*.pcap` that is not a safe, non-empty, real pcap/pcapng file: a directory (`Spool._reconcile`'s
    own `is_file()` guard, reused here — but REJECTED, not silently skipped, since this is
    untrusted input, not already-known-good managed state), a zero-byte file (`runner.plan_resume`'s
    own rule for "never a completed capture", reused here), a filename stem that fails
    `spool.validate_window_id`, or a body whose leading 4 bytes are not in `extract.PCAP_MAGICS`
    (the work order's own "the one permitted inspection" — this project reads `source`, never
    parses pcap framing beyond that one whitelist check).

    Rejected here rather than passed through (CONVENTIONS §3, "an unrecognized shape is rejected,
    not passed through") — `SRC` is the first place this project has ever pointed
    `validate_window_id`/`PCAP_MAGICS` at a user-controlled directory rather than input this tool
    generated or fetched itself (build steps 2 and 4, `docs/WORK_ORDER_harvest_from_pcaps.md`).
    """


class FromPcapsAccountingError(PipelineError):
    """`zeeked + zeek_failed != planned` once every Zeek worker thread has been joined, and
    nothing (a SIGINT, or `--fail-fast` tripping) stopped the run early on purpose — the
    from-pcaps sibling of `check_drift`: no `Spool` exists in this mode to cross-check disk usage
    against, so this is the structural completeness check instead. A worker thread that raised
    and died is already caught loud by `_RunStats.worker_fatal` (`_zeek_worker_loop`'s own
    exception guard); this is the belt-and-suspenders check for anything else that could leave a
    planned window unaccounted for without itself raising.
    """


@dataclass(frozen=True)
class FromPcapsConfig:
    """Everything `harvest --from-pcaps` needs — the counterpart to `HarvestConfig` for this mode,
    deliberately a SEPARATE type rather than `HarvestConfig` with some fields left at a placeholder:
    most of `HarvestConfig`'s fields (`streams`, `start_expr`, `end_expr`, `interval`,
    `spool_cap_bytes`, `extract_concurrency`) describe the extraction stage this mode skips
    entirely (D4 in the work order) — inventing values for them just to satisfy `HarvestConfig`'s
    constructor would make a `--from-pcaps` run's config carry fields that lie about what the run
    actually does.

    `source` is `str | Path`, not `Path` — deliberately: `plan_from_pcaps` needs to see whether an
    empty *string* was ever supplied at all (must-fix 3), and `Path("")` already normalizes to
    `Path(".")`, silently discarding exactly that information before this dataclass would ever see
    it if the caller had already wrapped it. A caller may still pass a real `Path` directly (every
    `str | Path` value is falsy only when it's `None` or `""` — a real, non-empty `Path` is always
    truthy, so the check below never misfires against one).

    `fail_fast` (ruling A: implemented, not a documentation-only accept) stops the run on the
    first `zeek_failed` — unlike `run_harvest`, whose Zeek queue must always fully drain to
    reclaim disk-resident, cap-consuming pcaps regardless of `--fail-fast` (see
    `_zeek_worker_loop`'s own docstring): `--from-pcaps` has no cap and nothing resident, so that
    justification does not transfer, and stopping early here is safe and meaningful.
    """

    source: str | Path
    out_dir: Path
    zeek_workers: int = DEFAULT_ZEEK_WORKERS
    fail_fast: bool = False
    yes: bool = False


@dataclass(frozen=True)
class FromPcapsPlan:
    """The read-only result of resolving `FromPcapsConfig.source` into windows — the from-pcaps
    counterpart to `HarvestPlan`. Computing this touches the filesystem (one directory listing,
    plus a 4-byte read per candidate file) but makes no network call and runs no Zeek container —
    safe to compute for `--dry-run`.

    `non_epoch_shaped_window_ids` (ruling C) names every accepted `window_id` that does not look
    like `pcap`'s own `<start_epoch>` naming (`_looks_epoch_shaped`) — informational only, never a
    reason to reject: `cli.py` prints a note when this is non-empty (see
    `docs/WORK_ORDER_harvest_from_pcaps.md`'s scope boundary — an operator who cut a pcap
    externally and points `--from-pcaps` at the result generally will NOT have epoch-shaped
    filenames, and that is an accepted, intended use, not a mistake to flag as an error).
    """

    windows: tuple[tuple[str, Path], ...]
    non_epoch_shaped_window_ids: tuple[str, ...] = ()


def plan_from_pcaps(config: FromPcapsConfig) -> FromPcapsPlan:
    """Resolve `config.source` into the exact `(window_id, path)` pairs `run_harvest_from_pcaps`
    will run Zeek over: `source/*.pcap`, sorted for a deterministic processing order (CONVENTIONS
    §3 — `Path.glob`'s own order is filesystem-dependent, never relied on directly).

    Every `window_id` (the filename stem) is validated exactly as `Spool.admit` would validate one
    it generated itself (`spool.validate_window_id`) — build step 2 of the work order: `pcap`'s own
    output is already `<start_epoch>.pcap`, exactly the spool's `<window_id>.pcap` shape, but that
    is asserted here rather than relied on, because `source` is a user-controlled directory that
    need not have been populated by this tool at all. A `window_id` need not itself LOOK
    epoch-shaped to be accepted (ruling C — see `FromPcapsPlan.non_epoch_shaped_window_ids`); only
    `validate_window_id`'s safety rejections are fatal here.

    Every candidate file is also validated as a genuine, non-empty pcap/pcapng capture (build step
    4): not a directory (or other non-regular-file) matching `*.pcap`, not zero bytes, and its
    leading 4 bytes are one of `extract.PCAP_MAGICS` — reusing the three guards this package
    already has for exactly these three shapes (`Spool._reconcile`'s `is_file()` check,
    `runner.plan_resume`'s zero-byte rule, `extract.py`'s magic whitelist) rather than assuming a
    user-controlled directory only ever holds what `pcap` itself would have written there.

    Raises `FromPcapsSourceError` if `source` is empty/unset, not a directory, contains zero
    `*.pcap` files, contains more than `windows.max_windows_for(False)` without `config.yes` (a
    directory can hold more pcaps than the window ceiling — the one place `--yes` still matters in
    this mode), or contains an entry that fails any of the checks above.
    """
    if not config.source:
        raise FromPcapsSourceError(
            "--from-pcaps source must not be empty — an empty/unset value would otherwise "
            "silently resolve to the current directory (Path('') normalizes to '.'), which is "
            "never the intended target"
        )
    source = Path(config.source)
    if not source.is_dir():
        raise FromPcapsSourceError(f"--from-pcaps source is not a directory: {source}")

    found = sorted(source.glob("*.pcap"))
    if not found:
        raise FromPcapsSourceError(
            f"--from-pcaps source contains no *.pcap files: {source} — an empty source is a "
            "usage error, not a zero-window run"
        )

    max_windows = windows.max_windows_for(config.yes)
    if len(found) > max_windows:
        raise FromPcapsSourceError(
            f"--from-pcaps source {source} contains {len(found)} pcap files, exceeding the "
            f"default ceiling of {max_windows} — pass --yes to proceed deliberately"
        )

    resolved: list[tuple[str, Path]] = []
    non_epoch_shaped: list[str] = []
    for path in found:
        window_id = path.stem
        try:
            validate_window_id(window_id)
        except SpoolError as exc:
            raise FromPcapsSourceError(
                f"--from-pcaps source contains a file whose name is not a safe window id: "
                f"{path.name!r} in {source} ({exc})"
            ) from exc

        if not path.is_file():
            raise FromPcapsSourceError(
                f"--from-pcaps source contains a non-file entry matching *.pcap: {path.name!r} "
                f"in {source} (a directory, or another non-regular-file, cannot be a capture)"
            )
        size = path.stat().st_size
        if size == 0:
            raise FromPcapsSourceError(
                f"--from-pcaps source contains a zero-byte file: {path.name!r} in {source} — "
                "never a completed capture (same rule runner.plan_resume applies to pcap's own "
                "output)"
            )
        magic = leading_magic(path)
        if magic not in PCAP_MAGICS:
            raise FromPcapsSourceError(
                f"--from-pcaps source contains a file that is not a recognized pcap/pcapng "
                f"capture: {path.name!r} in {source} (leading bytes {magic.hex()!r}) — refusing "
                "rather than handing it to Zeek"
            )

        if not _looks_epoch_shaped(window_id):
            non_epoch_shaped.append(window_id)
        resolved.append((window_id, path))

    return FromPcapsPlan(windows=tuple(resolved), non_epoch_shaped_window_ids=tuple(non_epoch_shaped))


def check_from_pcaps_accounting(report: RunReport, *, stopped_early: bool) -> None:
    """Raise `FromPcapsAccountingError` if `report.zeeked + report.zeek_failed != report.planned`
    and `stopped_early` is `False`. `stopped_early` covers both a SIGINT and `--fail-fast` tripping
    (both set the same `stop_requested` event `run_harvest_from_pcaps` passes here as
    `stop_requested.is_set()`) — either legitimately leaves later-queued windows un-processed via
    `_zeek_worker_loop`'s `drain_on_stop`, so a deficit in that case is expected, not a bug. If
    nothing stopped the run early, every planned window must have been either zeeked or
    zeek_failed by the time every worker thread has joined; a deficit then means one vanished
    without being accounted for.
    """
    if stopped_early:
        return
    completed = report.zeeked + report.zeek_failed
    if completed != report.planned:
        raise FromPcapsAccountingError(
            f"from-pcaps accounting mismatch: planned={report.planned} but only {completed} "
            f"window(s) were accounted for (zeeked={report.zeeked}, zeek_failed={report.zeek_failed}) "
            f"— {report.planned - completed} window(s) vanished without being accounted for"
        )


def run_harvest_from_pcaps(
    config: FromPcapsConfig,
    *,
    plan: FromPcapsPlan,
    zeek_runner: ZeekRunner,
    on_event: Callable[[str, str, str], None] | None = None,
) -> RunReport:
    """Run the Zeek stage over `plan.windows` — `harvest --from-pcaps`'s whole job. No extraction,
    no `Spool`, no credentials, no window-plan resolution: `plan_from_pcaps` already reduced
    `config.source` to the exact set of `(window_id, path)` pairs to Zeek.

    Every produced `_ZeekWorkItem` carries `owned=False`, so `_process_zeek_item` never calls
    `spool.release()` for it — `config.source`'s own pcaps are read-only input to this run, on
    both a successful AND a failed Zeek outcome (D3 of the work order: never deleted, matching the
    scope boundary's "no pcap slicing" — this project reads `source`, it never writes to it).

    Returns the same `RunReport` shape `run_harvest` does, so `cli.py` has one summary/exit-code
    path for both modes: `extracted`/`empty`/`extract_failed`/`reconciled_orphans`/`bytes_moved`/
    `peak_spool_bytes`/`retained_failed_bytes` are always `0` (no extraction and no `Spool` exist
    in this mode); `zeeked`/`zeek_failed`/`released`/`unrecognized_stems`/`zeek_failed_windows`
    reflect the real Zeek outcomes over `plan.windows` (`released` counts only pcaps this mode
    actually deletes — none, ever, since every item here is `owned=False`); `failed_windows` is
    always empty (nothing here is an *extraction* failure).

    `config.fail_fast` (ruling A) sets `stop_requested` on the first `zeek_failed`, detected by
    wrapping `on_event` rather than touching `_process_zeek_item`: every Zeek worker thread runs
    with `drain_on_stop=True` (see `_zeek_worker_loop`), so once set, any item a worker dequeues
    afterward is drained without being run through Zeek at all.

    On `KeyboardInterrupt`: every window was already queued for Zeek before this thread can ever
    observe the interrupt (there is no incremental "admit the next window" loop to stop, unlike
    the extract side — `plan.windows` is fully known upfront). Setting `stop_requested` here uses
    the SAME `drain_on_stop` mechanism `--fail-fast` does, so un-started queued items are drained
    rather than run to completion regardless — a SIGINT on a long `--from-pcaps` run actually
    stops it, rather than merely changing the eventual exit code once everything already queued
    has finished anyway.
    """
    _check_zeek_probe(zeek_runner)
    _check_zeek_root_writable(config.out_dir)
    zeek_root = _zeek_root(config.out_dir)

    # A manifest with `vantage=None` — and that is the point, not an omission. This mode has no
    # appliance and no stream spec (D2 of `docs/WORK_ORDER_visibility_and_vantage.md` names it as
    # the case tier 2 exists for), so nothing here can name the tap; inventing an id would make two
    # unrelated pcap directories compare as one vantage, which is the false-accept D1 closes. The
    # manifest is still written, because "harvested from these pcaps by this build, tap unknown" is
    # strictly more than the nothing that was recorded before, and because its presence is what
    # lets `aximprint ingest --vantage` be checked against a recorded answer rather than assumed.
    _record_vantage(zeek_root, Vantage(
        vantage=None, appliance=None, streams=None, produced_by="harvest --from-pcaps",
        pcap_source=str(config.source), tool_version=__version__))

    stats = _RunStats()
    stop_requested = threading.Event()

    def _on_event(window_id: str, event: str, detail: str) -> None:
        if config.fail_fast and event == "zeek_failed":
            stop_requested.set()
        if on_event is not None:
            on_event(window_id, event, detail)

    zeek_queue: queue.Queue = queue.Queue()
    zeek_threads = [
        threading.Thread(
            target=_zeek_worker_loop,
            args=(zeek_queue, zeek_runner, None, zeek_root, stats, _on_event, None, stop_requested),
            kwargs={"drain_on_stop": True},
            name=f"xtract-harvest-from-pcaps-zeek-{i}",
        )
        for i in range(config.zeek_workers)
    ]
    for t in zeek_threads:
        t.start()

    for window_id, path in plan.windows:
        zeek_queue.put(_ZeekWorkItem(window_id=window_id, pcap_path=path, owned=False))
    for _ in zeek_threads:
        zeek_queue.put(_ZEEK_SENTINEL)

    interrupted = False
    loop_start = time.monotonic()
    try:
        for t in zeek_threads:
            t.join()
    except KeyboardInterrupt:
        interrupted = True
        # Same mechanism as --fail-fast above: stop draining-and-processing, only drain. Every
        # item still sitting in the queue (real work, and the sentinels behind it, both already
        # enqueued) gets skipped by `drain_on_stop` rather than run through Zeek.
        stop_requested.set()
        for t in zeek_threads:
            t.join()

    elapsed_s = time.monotonic() - loop_start

    fatal = stats.worker_fatal_snapshot()
    if fatal is not None:
        raise fatal

    report = stats.to_report(planned=len(plan.windows), elapsed_s=elapsed_s, interrupted=interrupted)
    check_from_pcaps_accounting(report, stopped_early=stop_requested.is_set())
    return report
