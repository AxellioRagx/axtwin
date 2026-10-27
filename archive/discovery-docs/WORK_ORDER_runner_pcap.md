# Work Order — WO-6: `runner` and the `xtract pcap` command

> **Genre:** work order (see `docs/CONVENTIONS.md` §3) — an execution ticket for the `/dev` Pipeline. Concrete files, concrete gates, time-bounded. Mark it **DONE** in place when its run lands; don't delete it.
>
> **Status:** ✅ **DONE.** Built and mock-tested (`runner.py`, `xtract pcap` — CHANGELOG 2026-08-04
> "`xtract` iteration-1 build complete"); the live-acceptance gate (§4.3 below) is now satisfied —
> Q2 and Q4 were resolved by the 2026-08-05 live probe (`docs/WORK_ORDER_live_probe.md`, DONE) and
> `xtract pcap` extracted a real 60-second window from CorpAPCN (2,866,041 bytes, 7,058 packets,
> valid nanosecond-resolution classic pcap, exit 0, ~3.2s — CHANGELOG 2026-08-05 "Live verification
> against CorpAPCN"). The boundary check this section's §4.3 calls out ("adjacent windows do not
> duplicate a boundary packet") was verified after applying the Q4 fix: two adjacent 30s windows
> shared no packet. **Not run as part of this DONE:** the full §4.3 ~10-window interrupt-and-resume
> scenario against a live `--out` directory — see `docs/WORK_ORDER_packaging_runbook.md` (WO-7) for
> exactly what was and wasn't exercised live.
> **Authority for the decisions behind it:** CHANGELOG 2026-08-04 — `xtract` design ratified; iteration-1 scope and work orders

## 1. Goal

**This delivers the user's second task.** `xtract pcap --streams <spec> --start <ts> [--end <ts>]
--interval <dur>` walks the range in fixed steps and produces one pcap per window in `--out`, named
`<startTs>.pcap`. It resumes after interruption without redoing completed work, records what
happened to every window including the empty ones, and refuses to silently produce wrong output.

**Not a goal:** parallel extraction, a job-file format, or scheduling. Serial is the deliberate
default against a live capture appliance; a job file is a separate work order once the flag-driven
path is proven.

## 2. Build — phases, in order

### Phase 1 — `--end` resolution

- **Files:** `xtract/runner.py` (new), `tests/test_runner.py` (new)
- **Behavior:**
  - `--end` omitted → resolve to the **latest stored data**: the **union max** of `last` across the
    streams in the spec, via `catalog.stream_range` (WO-3).
  - Union, not intersection: intersection-min silently discards real data from the streams that ran
    longer, whereas extra windows past a quieter stream's end simply return 418, which is already a
    recorded outcome.
  - **Warn** when the requested streams' `last` values differ by more than one interval — the
    operator should know the tail of the run only covers some streams.
  - Wall clock is **not** used. Capture lag would generate a tail of empty windows.
  - If every requested stream is empty, fail with a clear message rather than planning zero windows
    and reporting success.
- **Tests:** union max across differing stream ends; the divergence warning fires; explicit `--end`
  bypasses all catalog calls (so `--dry-run --end` needs no credentials); all-empty → typed error.

### Phase 2 — The manifest

- **Files:** `xtract/manifest.py` (new), `tests/test_manifest.py` (new)
- **Behavior:**
  - `<out>/manifest.jsonl`, one JSON record per window: start/end strings, outcome
    (`written`/`empty`/`failed`), bytes, `X-Task-GUID`, duration, attempt count, timestamp.
  - A header record stores the run parameters: `streams`, `filter`, `interval`, tool version.
  - **The manifest is load-bearing, not diagnostic.** Because the filename carries only `startTs`,
    it is the *only* record that a 418 window was attempted. Without it, every empty window on a
    sparse capture is re-requested on every resume and the run never converges.
  - Appended and flushed per window, so a `kill -9` loses at most one record.
  - Never contains credentials, hostnames, or capture bytes.
- **Tests:** records round-trip; a truncated final line (simulating a hard kill) is tolerated on
  read; no credential material appears in the output.

### Phase 3 — Resume and the collision guard

- **Files:** `xtract/runner.py`, `tests/test_runner.py`
- **Behavior:**
  - A window is skipped when a completed `<startTs>.pcap` exists **or** the manifest records it as
    `empty`. `failed` windows are retried on resume.
  - **Collision guard:** if the manifest header's `streams`/`filter`/`interval` disagree with the
    current invocation, **refuse to run** and name the mismatch. Two runs with different
    `--streams` into one `--out` produce identical filenames; without this guard the second run
    would skip every window as "already done" and quietly return the first run's data. Loud failure
    beats a silently wrong answer. An explicit override flag exists for the operator who means it.
  - `.part` files are ignored for resume purposes and cleaned up at startup.
- **Tests:** interrupted run resumes without refetching completed windows; empty windows are not
  retried; failed windows are; the collision guard fires on each of the three mismatches and the
  override bypasses it; a `.part` never satisfies resume.

### Phase 4 — The batch loop and the command

- **Files:** `xtract/runner.py`, `xtract/cli.py`, `tests/test_cli_pcap.py` (new)
- **Behavior:**
  - `xtract pcap --streams <spec> --start <ts> [--end <ts>] --interval <dur> [--filter <bpf>]
    [--out DIR] [--dry-run] [--yes] [--fail-fast]`.
  - **Serial by default.** No parallelism flag in this work order.
  - Per-window `ctid` — stable and derived from the run ID plus window index, so a resumed run
    reuses it and appliance-side tasks stay correlatable.
  - `--dry-run` prints the full window plan (count, first, last, total duration, resolved `--end`,
    how many would be skipped by resume) and **makes no `/axapi/pcap` call**. With an explicit
    `--end` it requires no credentials at all.
  - A window count above the WO-4 ceiling requires `--yes`; the error states the count and suggests
    an interval that would stay under it.
  - Default is to continue past a failed window and report at the end; `--fail-fast` stops at the
    first failure.
  - SIGINT: stop cleanly, abort the in-flight task (WO-5), flush the manifest, print how to resume.
  - Final summary: written / empty / failed counts, total bytes, elapsed. Exit codes: `0` all
    windows accounted for (**empty windows are success**), `1` one or more failed, `2` usage,
    `3` auth, `4` transport/server.
- **Tests:** end-to-end against a mocked client — N windows produce N correctly-named files; a mixed
  run of written/empty/failed yields the right summary and exit code; `--dry-run` issues zero pcap
  calls; the count ceiling requires `--yes`; SIGINT leaves a resumable state.

## 3. Boundaries — explicitly OUT of scope

- **No parallelism.** Concurrent extracts against a live capture appliance is a decision with an
  operational blast radius, not a default. Separate work order if wanted.
- No job-file format. Flags first; a job file earns its own work order once this path is proven.
- No `archive=` / `servers=`, no async transport, no scheduling/cron wrapper.
- No merging, splitting, or reordering of pcaps — a multi-stream extract already returns one merged,
  time-ordered file from the appliance.
- Anything that would trip the risk gate — surface it as `DECISION NEEDED:` instead of doing it.

## 4. Validation

1. **Unit:** `pytest tests/test_runner.py tests/test_manifest.py tests/test_cli_pcap.py` green,
   HTTP mocked, `tmp_path` for output. No sockets.
2. **Hermetic tier:** `scripts/verify_tier.sh` clean
3. **Live (gated):** a ~10-window run against CorpAPCN over a range known to hold data. Evidence:
   every window is either a readable pcap or a manifest `empty` record; the run is interrupted and
   resumed, and the resumed run refetches nothing already complete; **adjacent windows do not
   duplicate a boundary packet** (this is the Q4 acceptance check).
4. **Docs:** CHANGELOG entry (Authority-marked), `doc-stats --check` clean, `LES-NNN` for anything
   durable, `GAP-NNN` for anything left brittle

## 5. Depends on / sequence

- **Depends on:** WO-3 (`catalog`, for `--end`), WO-4 (`windows`), WO-5 (`extract`). Q2 and Q4 for
  DONE status.
- **Unblocks:** WO-7 — and this is the point at which the tool does what it was asked to do.
