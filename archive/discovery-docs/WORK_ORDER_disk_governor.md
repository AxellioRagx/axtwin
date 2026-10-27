# Work Order — H-3: `spool` — the pcap spool and its disk governor

> **Genre:** work order (see `docs/CONVENTIONS.md` §3) — an execution ticket for the `/dev` Pipeline. Concrete files, concrete gates, time-bounded. Mark it **DONE** in place when its run lands; don't delete it.
>
> **Status:** ✅ **DONE.** Built and landed in commit `62013a0` (CHANGELOG 2026-08-06 "harvest
> foundations: `timespec`, containerized Zeek runner, disk governor"); reviewed and hardened the same
> day (CHANGELOG 2026-08-06 "Code review and fix pass over H-1/H-2/H-3"). `GAP-015` (no cross-process
> lock on the spool root) and the deferred `df`-based free-space check (§4) remain open.
> **Authority for the decisions behind it:** `docs/PLAN_metadata_harvester.md` §5, §6

## 1. Goal

`harvest` can run for days against a disk far smaller than the data passing through it, without
ever exceeding a configured ceiling and without deadlocking. After this lands, the pipeline has a
component that admits work only when there is room, accounts for what actually landed, and releases
space when Zeek is finished with a pcap.

This is load-bearing, not a convenience: a 30-day, all-stream harvest moves ~12.9 TB through a
168 GB disk. The governor is what makes that possible at all.

**Not a goal:** extraction, Zeek, conversion, or the pipeline wiring that uses this (H-4).

## 2. The hard part, stated up front

**A pcap's size is not known before it is extracted.** The API streams a body of unannounced length.
So this cannot be a classic reserve-exact-then-commit allocator. The design must be:

1. **Admission** on an *estimate*, with the estimate improving as real sizes are observed.
2. **Accounting** on the *actual* size once the file has landed.
3. **Overshoot is possible and must be handled**, not assumed away — a window that turns out 5×
   larger than estimated can push usage past the cap. The correct response is to stop admitting new
   work until the reaper drains, never to delete a pcap that has not been processed.

A design that quietly assumed sizes were predictable would fail exactly on the busy windows that
matter most.

## 3. Build — phases, in order

### Phase 1 — Accounting

- **Files:** `xtract/harvest/spool.py` (new), `tests/test_spool.py` (new)
- **Behavior:**
  - `Spool(root: Path, cap_bytes: int, *, estimator=...)` tracking resident bytes for pcaps it owns.
  - On startup, **reconcile against the filesystem**: sum what is actually in `root` rather than
    starting from zero. A resumed run that assumed an empty spool would blow straight past the cap.
  - `usage()`, `available()`, `cap_bytes` — all exact integers, no floats.
  - Entries are tracked with a state: `reserved` → `resident` → `released`. A file that never
    arrives (failed extraction) releases its reservation; a reservation that leaks is a bug the
    tests must catch.
- **Tests:** reconciliation finds pre-existing files; usage tracks add/remove exactly; a failed
  reservation releases; `usage()` never goes negative.

### Phase 2 — Admission and backpressure

- **Files:** `xtract/harvest/spool.py`, `tests/test_spool.py`
- **Behavior:**
  - `admit(window_id, *, timeout=None) -> Reservation` blocks until estimated room exists, then
    returns a reservation. **This is the backpressure**: the extract pool blocks here, and the
    pipeline naturally settles at whichever stage is slowest.
  - Thread-safe. The extract pool and the reaper run concurrently, so every mutation is under one
    lock and the condition variable is notified on release.
  - **Deadlock guards, both fatal and both explicit:**
    - If a *single* estimated window cannot fit in an empty spool, that is a configuration error —
      fail loud at admission with a message naming the estimate and the cap. Blocking forever is
      the failure mode this prevents.
    - If nothing is resident and nothing is in flight and the caller still cannot be admitted, that
      is the same error surfaced from a different direction. Raise, never wait.
  - `timeout` on `admit` so a stalled reaper surfaces as an error rather than a hang.
- **Tests:** a full spool blocks and then admits after a release (use a real thread, assert ordering,
  keep it fast); an oversized single window raises rather than blocking; timeout fires; concurrent
  admit/release from several threads keeps `usage()` exact (stress with N threads, assert the
  invariant at the end).

### Phase 3 — The estimator

- **Files:** `xtract/harvest/spool.py`, `tests/test_spool.py`
- **Behavior:**
  - Estimate the next window's size from observed sizes — a simple rolling statistic, seeded with a
    configurable default for the first windows when nothing has been observed.
  - Deliberately conservative: estimate high. Under-estimating causes overshoot past the cap;
    over-estimating only costs throughput. **State that asymmetry in the code**, because a future
    tuning pass will be tempted to use the mean and it should have to argue past a comment.
  - Feed the actual size back on `commit`.
- **Tests:** the estimator adapts to observed sizes; the seed applies when nothing is observed; a
  sequence of large windows raises the estimate rather than repeatedly overshooting.

### Phase 4 — Release and reap

- **Files:** `xtract/harvest/spool.py`, `tests/test_spool.py`
- **Behavior:**
  - `release(window_id, *, delete=True)` — used when Zeek has *succeeded* on a pcap. Deletes the
    file, frees the accounting, wakes a waiter.
  - **A pcap is only deleted after its consumer reports success.** A failed Zeek run keeps its pcap
    so the window can be retried; that is what makes the pipeline resumable rather than lossy.
  - A `retain` mode that frees accounting without deleting, for debugging a bad window.
  - Deleting a file that is already gone is not an error (idempotent reap — a resumed run may find
    the reaper already ran).
- **Tests:** release deletes and frees; a retained release frees without deleting; double release is
  idempotent; a failed window's pcap survives.

## 4. Boundaries — explicitly OUT of scope

- No extraction, no Zeek, no conversion, no CLI. H-4 wires these together.
- **No eviction of unprocessed pcaps under pressure.** Dropping work to stay under a cap produces
  silent gaps in the metadata that nothing downstream can distinguish from a quiet network. The cap
  is enforced by *not admitting*, never by discarding admitted work.
- No `df`-based free-space checking in this work order — the cap is the configured ceiling, not the
  disk's true free space. (Honoring actual free space too is a reasonable later addition; note it as
  a `GAP-NNN` rather than building it.)
- Anything that would trip the risk gate — surface it as `DECISION NEEDED:` instead of doing it.

## 5. Validation

1. **Unit:** `pytest tests/test_spool.py` green, `tmp_path` for the spool root. Concurrency tests
   must be deterministic — use events/barriers, not sleeps, and keep the suite fast.
2. **Hermetic tier:** `bash scripts/verify_tier.sh` clean (394 tests currently pass).
3. **Live:** none.
4. **Docs:** CHANGELOG entry (Authority-marked), `doc-stats --check` clean, `LES-NNN` for anything
   durable, `GAP-NNN` for the deferred free-space check.

## 6. Depends on / sequence

- **Depends on:** nothing in-tree.
- **Unblocks:** H-4 (the concurrent pipeline), which is mostly the wiring of this to H-2.
