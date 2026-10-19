"""runner — the batch loop behind `xtract pcap`
(`docs/WORK_ORDER_runner_pcap.md` WO-6).

Walks a `windows.plan()` across `--out`, calling `extract.extract_window()`
once per window and recording every outcome to `manifest.py` — resumably,
serially, and refusing to run when the manifest on disk was written by a
different invocation. This module owns *when* and *how many times*
`extract_window` is called; it never touches `requests` or pcap bytes
itself, and never recomputes a window boundary (`windows.py` owns that).

Three behaviors this module exists to get right (work order §0, "most
likely to be got wrong"):

  * **`--end` resolution** (`resolve_end`) is the **union max** of `last`
    across the requested streams, via `catalog.stream_range` — never the
    intersection (which would silently discard real data from a
    longer-running stream) and never the wall clock (which would generate a
    tail of empty windows when capture lags).
  * **The collision guard** (`check_collision`, read-only; `ensure_manifest_header`,
    the write side): two runs with different `--streams`/`--filter`/
    `--interval` into the same `--out` would produce identically-named
    files. `check_collision` refuses to run when the manifest header on disk
    disagrees, unless the caller explicitly overrides — checked before
    `plan_resume`, in both the real-run and `--dry-run` paths, so `--dry-run`
    never reports resume counts computed against a foreign manifest.
  * **Resume** (`plan_resume`): a window is done if its `.pcap` file exists
    (non-empty) or the manifest's *latest* record for it is `"empty"` — but
    only when that record's `end` also matches this plan's `end` for the
    same `start`; a same-start/different-end record means the window was
    clipped by an earlier, smaller `--end` and is re-attempted
    (`WindowDecision.reextract=True`). A `"failed"` record is retried.
    `.part` files are never a resume signal.
"""

from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass
from pathlib import Path

from aximprint.acquire import catalog, manifest
from aximprint.acquire.errors import XtractError
from aximprint.acquire.extract import Empty, Failed, Outcome, Written, extract_window
from aximprint.acquire.timeutil import Duration, Instant, format_instant
from aximprint.acquire.windows import Window

__all__ = [
    "AllStreamsEmptyError",
    "EndResolution",
    "ManifestCollisionError",
    "RunSummary",
    "RunnerError",
    "WindowDecision",
    "check_collision",
    "cleanup_stale_part_files",
    "ctid_for",
    "ensure_manifest_header",
    "execute",
    "plan_resume",
    "resolve_end",
    "run_id_for",
]


class RunnerError(XtractError):
    """Base class for every error this module raises."""


class AllStreamsEmptyError(RunnerError):
    """`resolve_end` found no data on any requested stream — nothing to
    resolve `--end` from. Raised instead of silently planning zero windows
    and reporting success (work order §0.3).
    """


class ManifestCollisionError(RunnerError):
    """The manifest header on disk disagrees with this invocation's
    `--streams`/`--filter`/`--interval` (work order §0.2, the collision
    guard). Refusing to run — the alternative is silently returning a mix of
    two different extracts' output under one set of `<startTs>.pcap` names.
    """


# --- Phase 1: --end resolution ------------------------------------------------


@dataclass(frozen=True)
class EndResolution:
    """The result of resolving an omitted `--end`.

    `per_stream_last` records every requested stream's `stream_range().last`
    (`None` for a stream with no stored data) so a caller can report it
    verbatim; `end` is `max()` over the non-`None` values (the union, never
    the intersection). `warning` is set when the requested streams' `last`
    values span more than one `interval` — the operator should know the tail
    of the run only covers some of them — and is `None` when they agree
    closely enough, or only one stream had data at all.
    """

    end: Instant
    per_stream_last: dict[int, Instant | None]
    warning: str | None


def resolve_end(client, stream_ids: list[int], interval: Duration) -> EndResolution:
    """Resolve an omitted `--end` to the union max of `last` across
    `stream_ids`, via `catalog.stream_range` — one call per stream id.

    Raises `AllStreamsEmptyError` if every requested stream has no stored
    data at all, rather than returning an `Instant` that would plan zero
    windows and let the caller report success on nothing.
    """
    per_stream_last: dict[int, Instant | None] = {}
    for sid in stream_ids:
        rng = catalog.stream_range(client, sid)
        per_stream_last[sid] = rng.last

    present = {sid: last for sid, last in per_stream_last.items() if last is not None}
    if not present:
        raise AllStreamsEmptyError(
            "none of the requested streams ("
            + ", ".join(str(sid) for sid in stream_ids)
            + ") have any stored data; there is nothing to resolve --end from"
        )

    end = max(present.values())
    warning = None
    if len(present) > 1:
        earliest_last = min(present.values())
        if (end - earliest_last) > interval:
            lagging = ", ".join(
                f"stream {sid}={format_instant(last)}"
                for sid, last in sorted(present.items())
                if (end - last) > interval
            )
            warning = (
                "requested streams' last-data timestamps differ by more than one "
                f"--interval (latest is {format_instant(end)}); the tail of this run will "
                f"only cover streams whose data reaches that far — lagging: {lagging}"
            )
    return EndResolution(end=end, per_stream_last=per_stream_last, warning=warning)


# --- Phase 2/3: the collision guard --------------------------------------------


def check_collision(
    out_dir: os.PathLike | str,
    *,
    streams: str,
    filter: str | None,
    interval: Duration,
    override: bool,
) -> list[str]:
    """Read-only: compare `streams`/`filter`/`interval` against the manifest
    header already on disk at `out_dir`, if any. Never writes — a fresh
    `--out` (no header yet) is not a mismatch, and this returns `[]` without
    creating one; that's `ensure_manifest_header`'s job, called separately
    only on the path that actually runs (never under `--dry-run`), so this
    check can safely run first in *every* path, including `--dry-run`, which
    must never quietly report resume counts computed against a foreign
    manifest.

    Returns the list of disagreeing field descriptions (empty when there is
    no header yet, or every compared field matches). Raises
    `ManifestCollisionError` naming every disagreeing field when there's a
    mismatch and `override` is not set; when `override` is set, returns the
    mismatch list instead of raising, so the caller can still warn loudly
    while proceeding.

    `filter` is compared as `filter or None` on both sides, so an absent
    filter (`None`) and an explicit empty one (`""`) are never treated as a
    mismatch.
    """
    existing = manifest.read_manifest(out_dir)
    if existing.header is None:
        return []

    normalized_filter = filter or None
    mismatches: list[str] = []
    if existing.header.streams != streams:
        mismatches.append(f"streams: manifest has {existing.header.streams!r}, this run requested {streams!r}")
    if existing.header.filter != normalized_filter:
        mismatches.append(f"filter: manifest has {existing.header.filter!r}, this run requested {normalized_filter!r}")
    if existing.header.interval_ns != int(interval):
        mismatches.append(
            f"interval: manifest has {existing.header.interval_ns}ns, this run requested {int(interval)}ns"
        )

    if mismatches and not override:
        raise ManifestCollisionError(
            f"manifest at {manifest.manifest_path(out_dir)} was written by a different invocation ("
            + "; ".join(mismatches)
            + "); refusing to run — two runs with different --streams/--filter/--interval into the "
              "same --out produce identically-named files, and resuming here would silently mix them. "
              "Pass an explicit override if this is what you mean."
        )
    return mismatches


def ensure_manifest_header(
    out_dir: os.PathLike | str,
    *,
    streams: str,
    filter: str | None,
    interval: Duration,
    tool_version: str,
) -> manifest.ManifestHeader:
    """Write-side counterpart of `check_collision`: idempotently ensure
    `<out_dir>/manifest.jsonl` has a header, without comparing or refusing —
    callers must call `check_collision` first and handle its refusal (a
    function named `check_*` that also writes as a side effect is a
    check-then-act smell; this split keeps the read-only check safe to call
    from `--dry-run` too).

    A fresh `--out` (no header yet) writes one derived from
    `streams`/`filter`/`interval`/`tool_version` and returns it. An existing
    header is returned unchanged and never rewritten — it's the identity of
    what the files already on disk actually correspond to, not a running log
    of every invocation that has touched this `--out`.
    """
    existing = manifest.read_manifest(out_dir)
    if existing.header is not None:
        return existing.header

    header = manifest.ManifestHeader(
        streams=streams, filter=(filter or None), interval_ns=int(interval), tool_version=tool_version
    )
    manifest.write_header(out_dir, header)
    return header


def cleanup_stale_part_files(out_dir: os.PathLike | str) -> int:
    """Remove every `*.pcap.part` left in `out_dir` from a prior hard kill.

    `.part` files are never a resume signal (`extract.py` always truncates
    and overwrites a stale one anyway); cleaning them up here just keeps a
    resumed run's directory listing honest. Returns the count removed.
    """
    out_path = Path(out_dir)
    if not out_path.exists():
        return 0
    removed = 0
    for part in out_path.glob("*.pcap.part"):
        part.unlink(missing_ok=True)
        removed += 1
    return removed


# --- Phase 3: resume ------------------------------------------------------------


@dataclass(frozen=True)
class WindowDecision:
    """Whether `window` should be skipped on this run, and why.

    `reextract` is `True` for a window that is *not* skipped (`skip=False`)
    specifically because an earlier, completed record for the same `start`
    covers a strictly different `end` than this run's plan — the clipped-final-
    window case (a previous run's smaller `--end` clipped it short, or its
    `end` otherwise disagrees). `execute()` surfaces this distinctly from an
    ordinary first-time attempt, so an operator can see a re-extraction
    happened rather than reading it as just another "written".
    """

    window: Window
    skip: bool
    reason: str | None
    reextract: bool = False


def plan_resume(out_dir: os.PathLike | str, windows: list[Window]) -> list[WindowDecision]:
    """Decide, for every window in `windows`, whether it's already done.

    A window is skipped when its `<startTs>.pcap` file already exists on
    disk **and is non-empty** (a zero-byte file — e.g. a stray `touch` — is
    never a completed extraction; `extract_window` itself never produces
    one), or the manifest's *latest* record for that start is `"empty"`.
    `"failed"` is retried (that's the point of resume); a window absent from
    the manifest entirely is attempted for the first time. "Latest" is the
    last-appended record for a given `start` — a window that failed and was
    then successfully retried in an earlier run must not be re-skipped as
    failed nor re-attempted as done-via-a-stale-empty-record.

    Neither of those checks alone is sufficient, though: `out/<startTs>.pcap`
    names only the window's *start*, so a window clipped by a smaller `--end`
    on an earlier run is indistinguishable, by filename alone, from the
    full-length window a later, larger `--end` now plans for that same start
    — `check_collision` can't catch this either, since `streams`/`filter`/
    `interval` all still match. The fix: compare the manifest record's own
    `end` field (already recorded, previously simply unread here) against
    this plan's **logical** end (`format_instant(window.end)`) for the same
    `start` — not the wire-encoded `end_time`, which carries the Q22
    inclusive-end transport adjustment and would make a Q22 constant flip look
    like every prior window was clipped. A same-start/different-end
    record — whether `"written"` (a `.pcap` on disk) or `"empty"` (416/418
    can legitimately flip to real data once `--end` grows) — means the window
    is *not* done; it is attempted again (`reextract=True`), and the new,
    full-length result overwrites the old, clipped one (`out/<startTs>.pcap`
    is a superset, so nothing already captured is lost).
    """
    data = manifest.read_manifest(out_dir)
    latest_by_start: dict[str, manifest.WindowRecord] = {}
    for record in data.records:
        latest_by_start[record.start] = record

    out_path = Path(out_dir)
    decisions: list[WindowDecision] = []
    for window in windows:
        wire = window.wire_params()
        start_str = wire["start_time"]
        # Compared against the manifest's recorded *logical* end
        # (`format_instant(window.end)`, matching `execute()`'s write side),
        # never the wire-encoded `end_time` — the wire encoding is a Q22
        # transport quirk (inclusive-end minus 1ns), and comparing against it
        # here would make a Q22 constant flip look like every prior window was
        # clipped, forcing a full re-extract for a reason that has nothing to
        # do with the requested range actually changing.
        end_str = format_instant(window.end)
        final_path = out_path / f"{start_str}.pcap"
        record = latest_by_start.get(start_str)
        clipped = record is not None and record.end != end_str and record.outcome in ("written", "empty")

        if final_path.exists() and final_path.stat().st_size > 0:
            if clipped:
                decisions.append(WindowDecision(window, False, None, reextract=True))
            else:
                decisions.append(WindowDecision(window, True, "pcap file already present"))
            continue
        if record is not None and record.outcome == "empty":
            if clipped:
                decisions.append(WindowDecision(window, False, None, reextract=True))
            else:
                decisions.append(WindowDecision(window, True, "recorded empty in the manifest"))
            continue
        decisions.append(WindowDecision(window, False, None))
    return decisions


# --- Phase 4: ctid derivation and the batch loop -------------------------------


def run_id_for(streams: str, filter: str | None, interval: Duration) -> str:
    """A stable run identity derived from the parameters the collision guard
    already requires to match across a resumed invocation — so a resume
    (necessarily the same `streams`/`filter`/`interval`, or the guard would
    have refused) always recomputes the same id without persisting one
    anywhere. Not derived from `--start`/`--end`: a resolved `--end` can
    legitimately grow between runs on a still-capturing stream without that
    being a new run.
    """
    payload = f"{streams}\n{filter or ''}\n{int(interval)}".encode()
    return hashlib.sha256(payload).hexdigest()[:16]


def ctid_for(run_id: str, window: Window) -> str:
    """Per-window `ctid`: stable across a resumed run (same `run_id`, same
    `window.index`), so an appliance-side task from an earlier attempt stays
    correlatable with this one.
    """
    return f"xtract-{run_id}-{window.index}"


@dataclass(frozen=True)
class RunSummary:
    """The final accounting `cmd_pcap` reports and picks an exit code from.

    `failed_windows` carries the `start_time` wire string of every `"failed"`
    outcome, for a caller that wants to name them without re-reading the
    manifest. `interrupted` is set only on a `KeyboardInterrupt` during a
    window's extraction (SIGINT) — a `--fail-fast` stop is a normal,
    non-interrupted completion of the loop.

    `reextracted` counts windows successfully rewritten because
    `plan_resume` flagged them `reextract=True` (a prior, clipped-by-a-
    smaller-`--end` record) — a subset of `written`, reported separately so
    an operator can see the re-extraction happened rather than reading it as
    an ordinary first-time "written".
    """

    written: int
    reextracted: int
    empty: int
    failed: int
    skipped: int
    total_bytes: int
    elapsed_s: float
    interrupted: bool
    failed_windows: list[str]


def _outcome_to_record(
    outcome: Outcome, *, start: str, end: str, duration_s: float, attempt: int, timestamp: str
) -> manifest.WindowRecord:
    if isinstance(outcome, Written):
        return manifest.WindowRecord(
            start=start,
            end=end,
            outcome="written",
            bytes=outcome.bytes,
            task_guid=outcome.task_guid,
            duration_s=duration_s,
            attempt=attempt,
            timestamp=timestamp,
        )
    if isinstance(outcome, Empty):
        return manifest.WindowRecord(
            start=start,
            end=end,
            outcome="empty",
            bytes=0,
            task_guid=None,
            duration_s=duration_s,
            attempt=attempt,
            timestamp=timestamp,
            detail=outcome.reason,
        )
    if isinstance(outcome, Failed):
        return manifest.WindowRecord(
            start=start,
            end=end,
            outcome="failed",
            bytes=0,
            task_guid=None,
            duration_s=duration_s,
            attempt=attempt,
            timestamp=timestamp,
            detail=outcome.detail,
        )
    raise TypeError(f"unknown Outcome type: {outcome!r}")  # pragma: no cover -- exhaustive today


def _utc_timestamp() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()


def execute(
    client,
    *,
    out_dir: os.PathLike | str,
    windows: list[Window],
    decisions: list[WindowDecision],
    streams: str,
    filter: str | None,
    run_id: str,
    fail_fast: bool,
    on_window=None,
) -> RunSummary:
    """Run every non-skipped window in `windows` (aligned 1:1 with
    `decisions`, from `plan_resume`), serially, in order.

    Cleans up stale `.part` files first (`cleanup_stale_part_files`). Each
    window's outcome is appended to the manifest immediately
    (`manifest.append_window_record`, itself `fsync`ed) before moving to the
    next window, so a `kill -9` loses at most the one record in flight.

    On `KeyboardInterrupt` from `extract_window` (SIGINT): the in-flight
    task has already been aborted server-side and its `.part` cleaned up by
    `extract_window` itself. This loop stops cleanly — no traceback, no
    re-raise — and returns a `RunSummary` with `interrupted=True`;
    `cmd_pcap` is what prints the resume instructions.

    `on_window(window, event, detail)` is an optional progress callback,
    `event` one of `"skip"`/`"written"`/`"reextracted"`/`"empty"`/`"failed"`/
    `"interrupted"` — `"reextracted"` is a `Written` outcome for a window
    `plan_resume` flagged `reextract=True` (see `WindowDecision`), reported
    distinctly from an ordinary `"written"`.
    """
    cleanup_stale_part_files(out_dir)

    existing = manifest.read_manifest(out_dir)
    attempt_counts: dict[str, int] = {}
    for record in existing.records:
        attempt_counts[record.start] = attempt_counts.get(record.start, 0) + 1

    written = reextracted = empty = failed = skipped = 0
    total_bytes = 0
    failed_windows: list[str] = []
    interrupted = False
    loop_start = time.monotonic()

    # `strict=True`: `windows` and `decisions` must be the exact same length
    # (`decisions` comes from `plan_resume(out_dir, windows)`) — a caller bug
    # that passes mismatched lengths must raise loudly here, not silently
    # truncate to the shorter one and run an incomplete plan.
    for window, decision in zip(windows, decisions, strict=True):
        wire = window.wire_params()
        start_str = wire["start_time"]

        if decision.skip:
            skipped += 1
            if on_window is not None:
                on_window(window, "skip", decision.reason or "")
            continue

        ctid = ctid_for(run_id, window)
        attempt = attempt_counts.get(start_str, 0) + 1
        t0 = time.monotonic()
        try:
            outcome = extract_window(client, window, streams, filter, out_dir, ctid)
        except KeyboardInterrupt:
            interrupted = True
            if on_window is not None:
                on_window(window, "interrupted", "")
            break
        duration_s = time.monotonic() - t0
        attempt_counts[start_str] = attempt

        record = _outcome_to_record(
            outcome,
            start=start_str,
            # Logical end (`format_instant(window.end)`), not the wire
            # `end_time` (`wire["end_time"]`, Q22-encoded end - 1ns) — see
            # `plan_resume`'s matching comment. Keeps the manifest's resume
            # key decoupled from a transport quirk that may change again.
            end=format_instant(window.end),
            duration_s=duration_s,
            attempt=attempt,
            timestamp=_utc_timestamp(),
        )
        manifest.append_window_record(out_dir, record)

        if isinstance(outcome, Written):
            written += 1
            total_bytes += outcome.bytes
            if decision.reextract:
                reextracted += 1
                event, detail = "reextracted", ""
            else:
                event, detail = "written", ""
        elif isinstance(outcome, Empty):
            empty += 1
            event, detail = "empty", outcome.reason
        else:
            failed += 1
            failed_windows.append(start_str)
            event, detail = "failed", outcome.detail

        if on_window is not None:
            on_window(window, event, detail)

        if isinstance(outcome, Failed) and fail_fast:
            break

    elapsed_s = time.monotonic() - loop_start
    return RunSummary(
        written=written,
        reextracted=reextracted,
        empty=empty,
        failed=failed,
        skipped=skipped,
        total_bytes=total_bytes,
        elapsed_s=elapsed_s,
        interrupted=interrupted,
        failed_windows=failed_windows,
    )
