# Work Order — WO-1: `timeutil` — instants, durations, and the string boundary

> **Genre:** work order (see `docs/CONVENTIONS.md` §3) — an execution ticket for the `/dev` Pipeline. Concrete files, concrete gates, time-bounded. Mark it **DONE** in place when its run lands; don't delete it.
>
> **Status:** ✅ DONE (CHANGELOG 2026-08-04 — "`xtract` iteration-1 build complete")
> **Authority for the decisions behind it:** CHANGELOG 2026-08-04 — `xtract` design ratified; iteration-1 scope and work orders

## 1. Goal

`xtract/timeutil.py` owns every conversion between a human/wire timestamp and the internal
representation, and **no `float` appears anywhere in the module or its callers**. After this lands,
`1728484411.293105240` survives a parse/format round trip byte-for-byte, and any future truncation
bug has exactly one file to be in.

**Not a goal:** timezone handling, calendar arithmetic, or human-readable date parsing
(`2026-08-04T12:00:00Z`). Epoch in, epoch out. If wall-clock date input is wanted later it is a
separate work order — see Boundaries.

## 2. Build — phases, in order

### Phase 0 — Minimal package skeleton

This is the first work order that writes code, so it lays the floor the others stand on. Keep it
minimal — full packaging is WO-7.

- **Files:** `xtract/__init__.py`, `tests/__init__.py`, a `pyproject.toml` sufficient for
  `pip install -e .` and `pytest` collection
- **Behavior:** `import xtract` works from a clean venv; `pytest` collects `tests/`. Runtime
  dependencies: none yet (`requests` arrives with WO-2). No CLI entry point yet.
- **Tests:** `pytest` runs green on an empty suite — proving the harness works before anything
  depends on it.

### Phase 1 — The instant type

- **Files:** `xtract/timeutil.py` (new), `tests/test_timeutil.py` (new)
- **Behavior:**
  - Internal representation is **`int` nanoseconds since the unix epoch**. Provide
    `parse_instant(s: str) -> Instant` and `format_instant(i: Instant) -> str`.
  - `parse_instant` accepts `"1728484411"` and `"1728484411.293105240"`, and any fractional length
    1–9 digits (`"1728484411.5"` → `1728484411_500000000` ns). Reject: empty, non-numeric,
    negative, more than 9 fractional digits, whitespace, `1e9`-style exponents, leading `+`.
  - **Precision is preserved, not normalized.** The parsed instant remembers whether its source had
    a fractional part; `format_instant` re-emits integer seconds for an integer-sourced instant and
    `%d.%09d` otherwise. `format_instant(parse_instant(s)) == s` for every accepted `s` whose
    fractional part is either absent or exactly 9 digits.
  - Arithmetic on instants yields instants and stays exact; adding a sub-second duration to an
    integer-sourced instant produces a fractional-precision instant.
- **Tests:**
  - Round-trip property over a table of representative strings, including `"1728484411.000000000"`
    (which must *not* collapse to `"1728484411"`).
  - `test_no_float_anywhere` — a source-level assertion that `timeutil.py` contains no `float(`
    call and no `/` division on time values. Crude, but it is the invariant the module exists for.
  - Every rejection case above raises a typed error naming the offending input.

### Phase 2 — Durations

- **Files:** `xtract/timeutil.py`, `tests/test_timeutil.py`
- **Behavior:**
  - `parse_duration(s: str) -> Duration` (also `int` ns) accepting `ns|us|ms|s|m|h|d` suffixes:
    `"30s"`, `"5m"`, `"1h"`, `"500ms"`, `"100us"`. A bare integer means seconds.
  - Reject zero, negative, and unsuffixed floats (`"1.5"` — ambiguous; `"1500ms"` is unambiguous).
  - Sub-second durations are accepted at parse time even though Q2 may cap what the appliance
    honors. The cap, if any, is enforced at the call site with a message that names Q2 — not by
    silently refusing here.
- **Tests:** each unit; rejection cases; `parse_duration("1000ms") == parse_duration("1s")`.

### Phase 3 — The unresolved unit boundary (Q1)

- **Files:** `xtract/timeutil.py`, `tests/test_timeutil.py`
- **Behavior:**
  - `instant_from_fs_field(v: int) -> Instant` converts the `time_start`/`time_end` integers
    returned by `GET /axapi/fs/files`. **Q1 is unresolved**, so this function takes the unit from a
    single module-level constant with a `# Q1:` comment pointing at `docs/OPEN_QUESTIONS.md`.
  - It also carries a magnitude sanity check: a value that would place the instant outside roughly
    1990–2100 raises rather than returning a nonsense timestamp. A ms/ns mix-up is 6 orders of
    magnitude and must fail loudly on the first record, not produce a plausible-looking wrong range.
- **Tests:** the sanity check fires for values that are plainly the wrong unit; correct-unit values
  pass through exactly.

## 3. Boundaries — explicitly OUT of scope

- No HTTP. This module has no I/O and imports nothing outside the stdlib — that is what makes it
  exhaustively testable, and every later work order depends on that.
- No ISO-8601 / human date input. Tempting and genuinely useful; it belongs in its own work order
  once the epoch path is proven.
- Do **not** resolve Q1 by picking whichever unit makes a sample response look reasonable. One
  constant, one comment, one probe.
- Anything that would trip the risk gate — surface it as `DECISION NEEDED:` instead of doing it.

## 4. Validation

1. **Unit:** `pytest tests/test_timeutil.py` green; this module should approach full branch coverage
   — it is pure and small, so anything less is a gap, not a trade-off.
2. **Hermetic tier:** `scripts/verify_tier.sh` clean
3. **Live:** none
4. **Docs:** CHANGELOG entry (Authority-marked), `doc-stats --check` clean, `LES-NNN` for anything
   durable this surfaces

## 5. Depends on / sequence

- **Depends on:** nothing (may run in parallel with WO-0).
- **Unblocks:** WO-3 (`catalog`), WO-4 (`windows`), WO-5 (`extract`). Every one of them handles time,
  and none of them should implement its own conversion.
