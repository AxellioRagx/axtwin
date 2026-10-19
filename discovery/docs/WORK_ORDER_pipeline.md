# Work Order — H-4: the concurrent extract → Zeek → reap pipeline

> **Genre:** work order (see `docs/CONVENTIONS.md` §3) — an execution ticket for the `/dev` Pipeline. Concrete files, concrete gates, time-bounded. Mark it **DONE** in place when its run lands; don't delete it.
>
> **Status:** ✅ **DONE.** Built and landed in commit `1ec6131` (CHANGELOG 2026-08-07 "H-4: the
> concurrent extract → Zeek → reap pipeline"); live-verified against the real appliance and the pinned
> Zeek image the same day. That live run also found `docs/VENDOR_API_DEFECTS.md` §S2 (CHANGELOG
> 2026-08-07 "`GET /axapi/pcap` returns 200 with an error body under concurrency"), which changed
> `DEFAULT_EXTRACT_CONCURRENCY` from this work order's assumed 8 down to 1. `GAP-013` (drift check
> runs once, at end-of-run) is the one item this work order's own §3 Phase 4 called for that landed
> narrower than specified — see that gap for why.
> **Authority for the decisions behind it:** `docs/PLAN_metadata_harvester.md` §5, §6

## 1. Goal

`harvest` walks a time range end to end: plan windows → extract pcaps concurrently → run Zeek over
each → delete the pcap once Zeek has succeeded with it → report. It runs for days against a disk far
smaller than the data crossing it, survives failures without losing coverage, and stops cleanly on
SIGINT without orphaning containers.

This is the work order where H-1, H-2 and H-3 become a tool. After it lands, the only thing between
`harvest` and a real archive is the Parquet converter (H-5) and the manifest (H-6).

**Not a goal:** Parquet, the archive layout, or any manifest (H-5/H-6). Full resume semantics (H-7)
— though see §3 Phase 5, which handles the orphans H-3's reconciliation surfaces. No daemon mode.

## 2. What already exists, and the contracts it hands you

Read these modules before writing anything; the pipeline is mostly the wiring between them.

- **`xtract/timespec.py`** — `resolve_start(expr, *, now)`, `resolve_end(expr, *, start, now)`.
  `now` is injected, never read from the clock inside the module: **resolve it once at startup and
  thread it through**, so a single run cannot resolve `now` twice and get two answers.
- **`xtract/windows.py`** — `plan(start, end, interval, *, max_windows=)` → `list[Window]`, each
  with `.wire_params()`. Half-open `[t, t+interval)`, final window clipped. The Q4 boundary encoding
  lives here and only here.
- **`xtract/harvest/spool.py`** — `Spool(root, cap_bytes, *, estimator=, stall_timeout=)`;
  `admit(window_id, *, timeout=) -> Reservation` (**blocks — this is the backpressure**),
  `commit(window_id, actual_bytes=None)` (defaults to a real `stat()`), `fail(window_id)`,
  `release(window_id, *, delete=True)`, `resident_windows()`, `reconciled_windows`, `verify()`.
  State machine: `RESERVED → RESIDENT → released`. `release()` is legal **only** on `RESIDENT`;
  `fail()` only on `RESERVED`. That asymmetry is what makes "delete only after success" structural.
- **`xtract/harvest/zeek.py`** — `ZeekRunner` protocol with `run(pcap, out_dir) -> ZeekResult` and
  `probe() -> ProbeResult`. `FakeZeekRunner` (production code — **all your tests use it**) and
  `PodmanZeekRunner(image, ...)`. `DEFAULT_ZEEK_IMAGE` is the live-verified pin.
  **A failed run is a value, never an exception** — check `ZeekResult.ok`, never `try/except`.
  `run()` requires a **fresh** `out_dir` and returns a failed result if one already has logs.
- **`xtract/client.py`, `xtract/extract.py`, `xtract/runner.py`** — the existing, live-verified
  extraction path. Reuse `extract_window`; do not write new HTTP code.

## 3. Build — phases, in order

### Phase 1 — Configuration and preflight

- **Files:** `xtract/harvest/pipeline.py` (new), `tests/test_pipeline.py` (new)
- **Behavior:**
  - A frozen `HarvestConfig`: streams spec, start/end expressions, interval, out root, spool cap,
    extract concurrency, Zeek worker count, Zeek image, filter, `--yes`.
  - **Preflight before any extraction**, and fail fast with one clear message: `zeek_runner.probe()`
    must be `ok`; the spool root must be writable; credentials must resolve; the window plan must be
    computable. Discovering a missing Zeek image after an hour of extraction is the failure this
    prevents.
  - **Seed the spool's estimator from reality, not the module default.** `SizeEstimator`'s 64 MiB
    default is a deliberately generic placeholder. Measured: a 300 s window on **one** stream ran
    45.7 MB. Scale the seed by the number of streams in the spec (`streamspec.expand`) and by the
    interval relative to 300 s. Under-estimating overshoots the cap; over-estimating only costs
    throughput — err high.
- **Tests:** each preflight failure is distinct and fires before any extraction; the seed scales
  with stream count and interval.

### Phase 2 — The extract stage

- **Files:** `xtract/harvest/pipeline.py`, `tests/test_pipeline.py`
- **Behavior:**
  - N concurrent extract workers. **Measured: extraction is latency-bound (~4 s fixed per request)
    and parallelizes near-linearly — 1 → 0.8 MB/s, 8 → 50.3 MB/s aggregate.** Default concurrency 8.
  - Each worker: `spool.admit(window_id)` (blocks under pressure) → `extract_window(...)` →
    `spool.commit(...)` on success → hand off to the Zeek queue.
  - **`Empty` (418, no data) and `Failed` must be distinguished.** An empty window is a normal
    outcome on this appliance (`LES-035`), not a failure: release its reservation with `fail()`
    (nothing landed) and record it. A `Failed` window is recorded for retry and does **not** stop
    the run unless `--fail-fast`.
  - `window_id` is the window's `start_time` wire string — the same key the pcap filename uses.
    It must pass the spool's id validation.
- **Tests:** admit → commit → handoff ordering; an empty window releases its reservation and does
  not enqueue Zeek work; a failed extraction does not leave a leaked reservation; backpressure
  actually blocks the extract worker when the spool is full (assert it, deterministically).

### Phase 3 — The Zeek stage and the reaper

- **Files:** `xtract/harvest/pipeline.py`, `tests/test_pipeline.py`
- **Behavior:**
  - M Zeek workers consuming committed pcaps. Each writes to a **fresh** per-window log directory
    under the out root — the cold tier. Never reuse a directory; the runner rejects it.
  - **The invariant this whole work order exists to protect:** on `ZeekResult.ok`, and only then,
    `spool.release(window_id, delete=True)`. On a failed result the pcap **stays resident** so the
    window can be retried, and the failure is recorded. `FakeZeekRunner(returncode=…)` exists
    precisely so this is testable — **write that test**.
  - Record `ZeekResult.unrecognized` per window. A Zeek upgrade that starts emitting a new log type
    must be visible in the run report, not silent.
  - Zeek worker count defaults conservatively (this host has 32 cores; Zeek did 2.87 MB in 0.83 s).
    Do not let the Zeek pool starve the extract pool of CPU, and do not size it so large that the
    spool drains faster than it fills — the run should settle at whichever stage is genuinely slowest.
- **Tests:** a successful Zeek run releases and deletes; **a failed Zeek run does NOT release, and
  the pcap is still on disk**; a timeout is treated as failure, not success; `unrecognized` reaches
  the report; a quiet window (`FakeZeekRunner(stems=())`) is handled without a `KeyError` — per
  `LES-035` that is the common case, and any `result.logs["conn"]` would break on it.

### Phase 4 — Shutdown, interruption and the run report

- **Files:** `xtract/harvest/pipeline.py`, `tests/test_pipeline.py`
- **Behavior:**
  - SIGINT: stop admitting, let in-flight Zeek runs finish or abort cleanly, **do not orphan
    containers**, flush the report, and print how to resume.
  - A `RunReport`: windows planned / extracted / empty / failed / zeeked / released, bytes moved,
    elapsed, peak spool usage, and the union of `unrecognized` stems. **Empty windows are success.**
  - Exit codes consistent with `xtract`'s existing table (`0` all accounted for, `1` one or more
    failed, `2` usage, `3` auth, `4` transport, `130` SIGINT).
  - Periodically assert `spool.verify()` — accounted vs on-disk — and fail loud on drift rather than
    silently exceeding the cap for the rest of a multi-day run.
- **Tests:** SIGINT mid-run leaves a resumable state and no leaked reservations; the report counts
  reconcile with what the fakes did; `verify()` drift is detected.

### Phase 5 — Inherited orphans

- **Files:** `xtract/harvest/pipeline.py`, `tests/test_pipeline.py`
- **Behavior:** at startup the spool reconciles pre-existing pcaps and reports them via
  `reconciled_windows`. **Decide and implement one explicit policy** — re-drive them through Zeek
  (they were extracted but never processed), or reap them. Re-driving is almost certainly right,
  since extraction is the expensive half. Whichever is chosen, do not leave them charged against
  the cap with nothing able to release them — that was the hang H-3's review found.
- **Tests:** a spool seeded with orphan pcaps drains them; the cap is not permanently consumed.

### Phase 6 — A minimal CLI

- **Files:** `xtract/cli.py` (add a `harvest` subcommand), `tests/test_cli_harvest.py` (new)
- **Behavior:** `xtract harvest --streams SPEC --start EXPR [--end EXPR] --interval DUR
  [--out DIR] [--spool-cap SIZE] [--concurrency N] [--zeek-workers M] [--zeek-image REF]
  [--filter BPF] [--dry-run] [--yes] [--fail-fast]`. `--dry-run` prints the plan (window count,
  span, estimated bytes, spool headroom) and **makes no request and runs no container**.
  Reuse the existing `client_factory` injection seam so tests never touch the network.
- **Tests:** `--dry-run` issues zero extractions and zero Zeek runs; flags map onto `HarvestConfig`;
  exit codes per case.

## 4. Boundaries — explicitly OUT of scope

- No Parquet, no archive layout, no manifest (H-5/H-6). This work order's output is the cold tier:
  pcaps (transient) and Zeek log directories (durable).
- No full resume semantics beyond Phase 5's orphan handling (H-7).
- No daemon/scheduling mode, no continuous tailing of a moving `--end`.
- **No new HTTP code.** Reuse `extract_window` and the existing client.
- **No per-row Python anywhere.** Not applicable yet, but do not start.
- Anything that would trip the risk gate — surface it as `DECISION NEEDED:` instead of doing it.

## 5. Validation

1. **Unit:** `pytest tests/test_pipeline.py tests/test_cli_harvest.py` green, with `FakeZeekRunner`
   and a mocked client. **No test may open a socket or invoke podman** (`tests/conftest.py` enforces
   the socket half). Concurrency tests must be deterministic — events and barriers, never sleeps.
2. **Hermetic tier:** `bash scripts/verify_tier.sh` clean (620 tests currently pass).
3. **Live (gated, orchestrator-run):** a short real run — a handful of 300 s windows on one stream,
   through the real appliance and the real pinned Zeek image, with a spool cap small enough that
   backpressure actually engages. Evidence: every window either has Zeek logs or a recorded failure;
   the spool never exceeds its cap; pcaps are deleted after success and retained after failure.
4. **Docs:** CHANGELOG entry (Authority-marked), `doc-stats --check` clean, `LES-NNN` for anything
   durable, `GAP-NNN` for anything left unproven.

## 6. Depends on / sequence

- **Depends on:** H-1 (`timespec`), H-2 (`zeek`), H-3 (`spool`), and the existing `xtract` extraction
  path — all landed.
- **Unblocks:** H-5 (the converter consumes the Zeek log directories this produces).
