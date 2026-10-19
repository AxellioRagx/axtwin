# Work Order — WO-4: `windows` — the pure window planner

> **Genre:** work order (see `docs/CONVENTIONS.md` §3) — an execution ticket for the `/dev` Pipeline. Concrete files, concrete gates, time-bounded. Mark it **DONE** in place when its run lands; don't delete it.
>
> **Status:** ✅ DONE (CHANGELOG 2026-08-04 — "`xtract` iteration-1 build complete") — ⚠️ **the
> `end_time` encoding remains provisional (`Q4_END_TIME_INCLUSIVE`) until Q4 closes by the live
> probe; per this work order's own Phase 2, that's expected — closing Q4 is a one-line change to
> the constant, not a reopening of this work order.**
> **Authority for the decisions behind it:** CHANGELOG 2026-08-04 — `xtract` design ratified; iteration-1 scope and work orders

## 1. Goal

`xtract/windows.py` turns `(start, end, interval)` into the exact list of extraction windows, with
no I/O and no dependency on anything but `timeutil`. Because it is pure, it can be tested
exhaustively — and every boundary bug in the tool is a bug in this file, which is precisely why it
is isolated. After this lands, `xtract pcap --dry-run` can print a full plan without credentials.

**Not a goal:** fetching anything, resolving an omitted `--end` (that needs the catalog — WO-6), or
deciding which windows to skip on resume (WO-6).

## 2. Build — phases, in order

### Phase 1 — Window generation

- **Files:** `xtract/windows.py` (new), `tests/test_windows.py` (new)
- **Behavior:**
  - `plan(start: Instant, end: Instant, interval: Duration) -> list[Window]`.
  - Windows are **half-open `[t, t+interval)`** and tile the range without overlap or gap.
  - The final window is **clipped to `end`** — a 10-second range with a 3-second interval yields
    `[0,3) [3,6) [6,9) [9,10)`, four windows, the last one short. It is never dropped and never
    extended past `end`.
  - `start == end` yields **zero** windows, not one empty one. `end < start` is an error naming both
    values. `interval > (end - start)` yields exactly one clipped window.
  - Each `Window` carries `start`, `end`, and its index/total, so callers can report progress
    without recomputing.
- **Tests:** exact tiling for evenly- and unevenly-divisible ranges; the clipped final window; the
  three edge cases above; a property test that windows are contiguous, non-overlapping, and their
  union is exactly `[start, end)`; sub-second intervals produce exact boundaries with no drift
  across thousands of windows (this is the test that would catch a `float` creeping in).

### Phase 2 — Wire encoding and Q4

- **Files:** `xtract/windows.py`, `tests/test_windows.py`
- **Behavior:**
  - `Window.wire_params()` produces the `start_time`/`end_time` **strings** for the request.
  - **Q4 is unresolved.** If `end_time` proves inclusive, adjacent windows would both capture the
    boundary packet; the fix is to send `end − 1 ns`. That adjustment lives behind **one named
    constant** in this module with a `# Q4:` comment pointing at `docs/OPEN_QUESTIONS.md`. No other
    file may compensate for boundary semantics.
  - Formatting delegates to `timeutil.format_instant` — precision is preserved, never normalized.
- **Tests:** the encoding flips correctly when the constant flips; window `start` strings are exactly
  what `timeutil` produces (this is the resume key and the output filename, so it must be exact).

### Phase 3 — Guardrails

- **Files:** `xtract/windows.py`, `tests/test_windows.py`
- **Behavior:**
  - `plan()` refuses to materialize an unreasonable number of windows. `--start <beginning of
    storage> --interval 1s` is millions of requests against a live capture appliance and is an easy
    typo, not an intent. Past a ceiling, raise a typed error stating the count, the interval that
    would produce it, and the interval that would keep it under the ceiling.
  - The ceiling is a parameter so WO-6 can expose `--yes` to raise it deliberately.
- **Tests:** the ceiling fires with an actionable message; `--yes`-equivalent override permits it.

## 3. Boundaries — explicitly OUT of scope

- **No I/O of any kind** — no HTTP, no filesystem, no clock reads. `plan()` must be deterministic
  given its arguments. This is the property that makes the module exhaustively testable, and it is
  worth more than any convenience that would break it.
- No resume logic, no manifest, no filename construction (WO-6 owns the output contract).
- No resolution of an omitted `--end` — that requires the catalog and belongs in WO-6.
- Anything that would trip the risk gate — surface it as `DECISION NEEDED:` instead of doing it.

## 4. Validation

1. **Unit:** `pytest tests/test_windows.py` green. Like `timeutil`, this module is pure and small —
   aim for full branch coverage; a gap here is a boundary bug waiting to happen in production.
2. **Hermetic tier:** `scripts/verify_tier.sh` clean
3. **Live:** none — and that is the point of the module.
4. **Docs:** CHANGELOG entry (Authority-marked), `doc-stats --check` clean, `LES-NNN` for anything
   durable

## 5. Depends on / sequence

- **Depends on:** WO-1 (`timeutil`). Independent of the client — may run in parallel with WO-2/WO-3.
- **Unblocks:** WO-5, WO-6, and `--dry-run`.
