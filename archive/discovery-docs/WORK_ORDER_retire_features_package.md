# WORK ORDER — Retire `features/`: one package below the sink registry

**STATUS:** OPEN — drafted 2026-08-06, not started
**Depends-on:** `WORK_ORDER_egress_sink_unification.md` (must land first, including its fix round)
**Closes:** the split-registry trap (`LES-020`), structurally rather than by error message
**Authority:** drafted by Claude Code; the user ratified *deferring* it to its own round (2026-08-06)
and has **not** ratified the move itself. D1/D2 below are proposals.

## Why this exists

The egress round moved the sink contract and registry into `src/aximprint/sinks/` and left the
`jsonl` sink in the sibling `features/` package. The registry is therefore **populated by import**,
and `import aximprint.sinks` yields a *silently incomplete* one — `get_sink("jsonl")` raises
`KeyError` until something has also imported `aximprint.features`.

This is not hypothetical. It fired once already, as a real `KeyError` in
`scripts/verify_determinism.py`, and was fixed by adding an import — a patch, not a shape. The
egress fix round improves the *error message* so the next caller is told why. That is a mitigation.
This round removes the condition.

**A registry whose registrants all live beneath it is complete on one import.** That is the whole
proposal.

## Decisions this draft makes

| # | Decision | Choice | Rejected, and why |
|---|---|---|---|
| D1 | Where `jsonl.py` goes | **`src/aximprint/sinks/jsonl.py`.** It is a sink; it implements the contract in `sinks/base.py`. | Leaving it in `features/` and lazy-importing from a `name -> "module:Class"` table in `sinks/` — it works, but hardcodes a downstream module path *upstream*, which is the dependency the layering exists to prevent. |
| D2 | Where `vocabulary.py` goes | **`src/aximprint/sinks/vocabulary.py`.** `LES-014` already calls the categorical index map "run-level state a **sink** legitimately holds", and only `jsonl.py` and the CLI's `--vocabulary` loader touch it. | Keeping it in a surviving `features/` package — then `sinks → features` and the split is merely inverted, with the registry still incomplete on one import. Moving it to `windowing/` — it is a *persistence-boundary* encoding concern, not a feature-computation one, and `windowing` must not acquire a downward dependency on how its outputs are written. |

**D2 is the one to challenge if either is wrong.** "Feature vocabulary" and "how a sink encodes
categorical columns" are arguably two things wearing one name, and a reviewer who thinks the index
map belongs with feature *semantics* rather than with feature *persistence* should say so before the
move rather than after — moving it twice is worse than moving it once.

## Build

1. **Move, do not rewrite.** `features/jsonl.py` → `sinks/jsonl.py`, `features/vocabulary.py` →
   `sinks/vocabulary.py`. Contents change only where an import path or a docstring names the old
   location. This is a mechanical diff and should read like one.
2. **Delete the `features/` package.** No shim, no re-export module (`LES-001` — a compatibility
   shim for an incubating contract is exactly the ceremony that rule forbids, and the egress round
   already set the precedent by deleting `features/base.py` outright).
3. **`sinks/__init__.py` registers every sink** — store, evidence, jsonl — so one import yields a
   complete registry. Then **delete the caveat** its docstring currently carries, and the "sinks
   register on import of the package that defines them" clause the fix round added to the `get_sink`
   error. A mitigation kept after its condition is gone is a false warning that trains readers to
   ignore the real ones.
4. **Update every importer**: `cli.py` (`load_pinned_vocabulary`), `scripts/verify_determinism.py`
   (which no longer needs its extra `import aximprint.features`), and the tests.
5. **Rename the test files to match** — `test_feature_sink.py` → `test_jsonl_sink.py`,
   `test_feature_vocabulary.py` → `test_vocabulary.py` — or state why not. A test file named after a
   package that no longer exists is drift the next reader has to resolve.
6. **`scripts/inject_lessons.py`**: drop `features` from `_MODULES`, and re-scope every
   `LES-NNN` whose `Scope:` names a `features/` path. `LES-011`, `LES-014` and `LES-016`/`LES-017`
   all point into that tree. A lesson that no longer reaches the file it is about is worse than no
   lesson, because the hook still reports success.
7. **`CONVENTIONS.md` §3 and CLAUDE.md's layer table** — collapse the two-layer description (added by
   the egress round) back to one: `sinks` reads windowing outputs, and nothing but the CLI imports
   it.

## Boundaries — do NOT do these

- **Do not change what any sink writes.** Byte-identical output is the acceptance criterion.
- **Do not change the sink contract**, the registry semantics, or any `open`/`write_window`/`close`
  signature. If this round touches behavior, it has stopped being a move.
- **Do not fold `store`/`evidence` sinks into one module** while reorganizing — they are separate
  files for the same reason `jsonl` is.
- No new runtime dependency; no schema versioning (`LES-001`); no capture contents (`LES-003`).

## Validation

- **The null test, again, and it is the whole point:** feature archive byte-identical, belief store
  row-identical, evidence log identical, against a baseline captured **before** any edit (`LES-018`).
  The egress round's baseline procedure and reference digests are in
  `WORK_ORDER_egress_sink_unification.md`; re-capture rather than reuse, since the fix round has
  since changed the store (`runs` table, transaction) — capture fresh, then compare.
- `./scripts/verify_tier.sh` green on all four steps; determinism digest unmoved.
- **The trap is actually gone:** a test asserting that a bare `import aximprint.sinks` (in a fresh
  interpreter, with nothing else imported) yields `sorted(_REGISTRY) == ["evidence", "jsonl",
  "store"]`. Without this the move is unverified — and this test is the reason the round exists, so
  it should fail if step 3 is skipped.
- `python scripts/inject_lessons.py <each moved file>` prints the lessons that apply to it. Check
  this by running it, not by reading `_MODULES`.
- Full suite green with no test weakened; list every test that moved and why.

## Findings to report

1. **Was D2 right?** After the move, does `vocabulary.py` read as a natural neighbour of `jsonl.py`,
   or as a feature-semantics module misfiled under persistence? Say which, plainly — it is cheap to
   re-home now and expensive once a second sink consumes it.
2. **Does anything still import a registrant for its side effect** rather than for a name? Those are
   the remaining places the trap could regrow.
3. **What did the mechanical diff turn out not to be?** Every line that changed for a reason other
   than a moved import path is a place the move was hiding something — list them.
