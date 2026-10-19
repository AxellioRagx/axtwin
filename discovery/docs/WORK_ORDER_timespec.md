# Work Order — H-1: `timespec` — relative and absolute time expressions

> **Genre:** work order (see `docs/CONVENTIONS.md` §3) — an execution ticket for the `/dev` Pipeline. Concrete files, concrete gates, time-bounded. Mark it **DONE** in place when its run lands; don't delete it.
>
> **Status:** ✅ **DONE.** Built and landed in commit `62013a0` (CHANGELOG 2026-08-06 "harvest
> foundations: `timespec`, containerized Zeek runner, disk governor").
> **Authority for the decisions behind it:** `docs/PLAN_metadata_harvester.md` §1, §6

## 1. Goal

`harvest` can be told *"start 30 days back from now, end 2 days from the start"* and resolve that to
two exact `Instant`s, with the same nanosecond discipline the rest of the codebase keeps. After this
lands, no other module parses a time expression, and the accepted grammar is small, explicit, and
documented in one place.

**Not a goal:** natural-language date parsing, calendar arithmetic (months, "last Tuesday",
DST-aware day boundaries), timezones, or a dependency on `dateutil`. Epoch in, epoch out — a "day"
is exactly 86400 seconds.

## 2. Build — phases, in order

### Phase 1 — Human durations

- **Files:** `xtract/timespec.py` (new), `tests/test_timespec.py` (new)
- **Behavior:**
  - `parse_human_duration(s) -> Duration` accepting both the compact form `timeutil.parse_duration`
    already takes (`30d`, `500ms`) **and** a spelled-out form: `30 days`, `1 day`, `2 hours`,
    `90 minutes`, `45 seconds`. Singular and plural both accepted; case-insensitive.
  - Delegate to `timeutil.parse_duration` wherever the compact form applies rather than
    reimplementing unit arithmetic — `NS_PER_SECOND` and the unit table live there and stay there.
  - Reject anything else with a typed error naming the input and listing the accepted forms.
    Specifically reject: bare floats (`1.5 days` — ambiguous at ns precision; `36 hours` is
    unambiguous), negative values, zero, and mixed forms (`1 day 2 hours`).
- **Tests:** each spelled-out unit, singular and plural; case-insensitivity; equivalence with the
  compact form (`parse_human_duration("30 days") == parse_duration("30d")`); every rejection.

### Phase 2 — The expression grammar

- **Files:** `xtract/timespec.py`, `tests/test_timespec.py`
- **Behavior:** a deliberately small, closed grammar. Anything outside it is an error listing what
  *is* accepted — no guessing, no partial matches.

  | Form | Meaning | Valid for |
  |---|---|---|
  | `1785934195`, `1785934195.5` | absolute epoch (via `timeutil.parse_instant`) | `--start`, `--end` |
  | `now` | the injected reference clock | `--start`, `--end` |
  | `<duration> back from now`, `<duration> ago` | `now - duration` | `--start`, `--end` |
  | `<duration> from the start`, `<duration> after the start` | `start + duration` | `--end` **only** |

  - `resolve_start(expr, *, now) -> Instant`
  - `resolve_end(expr, *, start, now) -> Instant` — and `None` when `expr` is `None`, meaning
    "latest stored data", which `harvest` resolves from the catalog, not here.
  - **`<duration> from the start` used for `--start` is an error naming the circularity**, not a
    silent fallback.
  - Whitespace is normalized (collapse runs, strip); `the` is optional (`2 days from start`).
- **Tests:** every row of the table; `from the start` rejected for `--start`; unknown phrasing
  produces an error listing accepted forms; `None` end returns `None`.

### Phase 3 — Purity and the clock seam

- **Files:** `xtract/timespec.py`, `tests/test_timespec.py`
- **Behavior:**
  - **No module in `xtract/` reads the clock.** `now` is a required keyword argument, never
    defaulted to `time.time()` inside this module. The CLI layer supplies it, once, at startup —
    so a single run cannot resolve `now` twice and get two different answers, and every test is
    deterministic without monkeypatching time.
  - `now` is an `Instant`, not a float — the caller converts at the boundary.
  - Arithmetic is exact integer nanoseconds throughout; no `float` in this module. Add the same
    AST-based `no float / no true-division` source check `tests/test_timeutil.py` already uses
    (`LES-045`).
- **Tests:** the source-level float check; resolving the same expression twice with the same `now`
  is identical; `resolve_start("now", now=X).ns == X.ns`.

## 3. Boundaries — explicitly OUT of scope

- No CLI wiring. `harvest`'s argument parser does not exist yet; H-1 is the library it will call.
- No calendar semantics. A "day" is 86400 s. If DST- or timezone-correct day boundaries are ever
  wanted, that is a separate work order with a real dependency decision attached.
- No clock reads, no I/O, no imports outside the stdlib and `xtract.timeutil` — the same purity rule
  that makes `timeutil` and `windows` exhaustively testable.
- Anything that would trip the risk gate — surface it as `DECISION NEEDED:` instead of doing it.

## 4. Validation

1. **Unit:** `pytest tests/test_timespec.py` green. Pure and small — aim for full branch coverage.
2. **Hermetic tier:** `bash scripts/verify_tier.sh` clean (394 tests currently pass; keep them green).
3. **Live:** none.
4. **Docs:** CHANGELOG entry (Authority-marked), `doc-stats --check` clean, `LES-NNN` for anything
   durable.

## 5. Depends on / sequence

- **Depends on:** `xtract/timeutil.py` (already landed).
- **Unblocks:** the `harvest` CLI, and H-4's planner.
