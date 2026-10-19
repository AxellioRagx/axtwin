# WORK ORDER — Egress: one sink registry, both output paths through it

**STATUS:** LANDED (2026-08-06 — see CHANGELOG "Egress: one sink registry, both output paths
through it")
**Depends-on:** `WORK_ORDER_streaming_zeek_ingest.md` (LANDED), `WORK_ORDER_feature_vector_sink.md`
(LANDED), `SPEC_zeek_metadata_archive.md` (LANDED)
**Authority:** drafted by Claude Code at user request (2026-08-05) — the *goal* is ratified ("do the
egress half now"); the choices in "Decisions this draft makes" are proposals. Overrule any of them
and say so rather than discovering it mid-build.

## Goal

The ingest half is now plugin-capable at both the transport and semantic layers. The egress half is
**half-built**: `features/base.py` has a sink contract and registry, and `cmd_features` uses it —
but `cmd_ingest` bypasses it entirely and hardcodes `BeliefStore` + `EvidenceLog`. So a second
output format costs nothing on one path and a code change on the other, and the two verbs carry a
duplicated window loop that has already drifted once (only `cmd_features` stamps a run into its
output; `cmd_ingest` stamps nothing).

Close that: **one registry, every writer behind it, one driver.**

## The layering crux — settle this first

`features/base.py` currently owns the sink contract. Moving `BeliefStore` behind it would make
`store` import `features`, and `features` is declared as a layer that *reads windowing outputs and
is imported only by the CLI* (CONVENTIONS §3). A `store → features` edge inverts that.

The contract is not a *feature* concept at all — it is "something that consumes closed windows".
Three artifacts already consume them (the belief store, the evidence log, the feature archive) and
only one lives in `features/`.

**D1 (proposed): move the contract and registry to a new neutral module `src/aximprint/sinks/`**,
importing only `windowing` outputs. `features/jsonl.py` and the new store/evidence sinks all import
*down* into it. `features/base.py` becomes a re-export shim or disappears; prefer it disappearing —
`LES-001` says shapes change freely here, and a compatibility shim for an incubating contract is
exactly the ceremony that rule forbids.

Rejected: leaving it in `features/` and letting `store` import it (inverts a ratified layer rule for
convenience); duplicating a second registry in `store/` (two registries is the thing
`features/base.py` explicitly says this codebase will not have).

## Decisions this draft makes

| # | Decision | Choice | Rejected, and why |
|---|---|---|---|
| D2 | Where assertions come from | **The driver computes `extract_assertions(win)` once and passes them to every sink**, widening the contract to `write_window(win, assertions)`. | Each sink calling `extract_assertions` itself — it is per-window work that would run N times for N sinks, and it makes every sink import `learners`. Putting assertions on `WindowResult` — `windowing` would have to import `learners`, which is circular. |
| D3 | CLI surface | **Keep `ingest` and `features` as verbs**, both thin wrappers over one shared driver, and add repeatable `--sink NAME=DEST`. `ingest` defaults to `--sink store=<path>`, `features` to `--sink jsonl=<dir>`. | Collapsing to one verb — it breaks every existing invocation, including `verify_tier.sh` and the docs, to save one line. Do that later if the verbs turn out to be redundant. |
| D4 | Sink-specific options (`--vocabulary`) | **Keep them on the verb that owns them**, passed through to the sink that declares it wants them; a sink that does not declare it never sees it. | Threading `vocabulary` through the base `open()` for every sink — it is already the one jsonl-specific parameter in a contract that is otherwise generic, and adding a second would set the pattern. |
| D5 | The `evidence` writer | **Becomes a registered sink of its own** (`evidence=<path>`), not a flag on the store sink. | `--evidence-log` as a store option — the evidence log is the *source of truth* (§7) and the store is a projection of it; making one an option of the other inverts that relationship in the CLI. |

**D2's real cost, stated plainly:** `write_window(win, assertions)` widens a contract every existing
sink implements. That is free during incubation (`LES-001`) but it is still a two-sided change
(`LES-005`) — update `JsonlSink` in the same commit, and check no caller passes positionally in a
way that silently reorders.

## Build

1. **`src/aximprint/sinks/base.py`** — move `WindowSink` (renamed from `FeatureSink`; it no longer
   writes only features) + `register` / `get_sink`. Import direction: `sinks → windowing`, and
   nothing but the CLI imports `sinks`. Keep the existing contract discipline verbatim — the
   `close()` summary's **required keys are subscripted, not `.get`**-ed, and that reasoning is
   already written down in `features/base.py`; carry the comment, not just the code.
2. **`StoreSink`** wrapping `BeliefStore`, and **`EvidenceSink`** wrapping `EvidenceLog`. Both
   report a `close()` summary in the same shape every other sink does. `vocabulary` is `{}` for
   both — **present and empty, never absent**, which the contract already requires and which is the
   `LES-006` distinction in miniature.
3. **One driver.** Extract the duplicated window loop from `cmd_ingest` / `cmd_features` into a
   single function: build the plugin, open every sink, run `wm.process` then `wm.finalize`, compute
   assertions once per window, fan out, close every sink, print one summary. Both verbs call it.
4. **Fan-out must not hide a partial failure.** If sink 3 of 4 raises on `write_window`, the run
   has already written two archives that no longer describe the same run. Decide and state what
   happens — the default should be **fail loud and name which sinks had already written**, never a
   silent `continue`. Whatever you choose, the summary must make it visible.
5. **`--sink NAME=DEST`, repeatable**, with the old flags kept as aliases (`--store`, `--out`,
   `--evidence-log`) so `verify_tier.sh` and every documented invocation keep working. An unknown
   sink name fails loud with the registry's available names — `get_sink` already does this.
6. **Stamp every path.** `cmd_features` passes `run_meta`; `cmd_ingest` passes nothing, so a belief
   store cannot say which build or source produced it. §10's first reconciliation invariant is "the
   same aximprint build on both sides", and a store with no run stamp cannot satisfy it. Give the
   store sink the same `run_meta` and persist it.

## Boundaries — do NOT do these

- **Do not change what any sink writes**, beyond the assertions question below. This is a plumbing
  change; the artifacts must come out byte-identical (see Validation).
- **Do not add `assertions.jsonl` to the feature archive in this round.** It is a real gap — §5.4
  lists deterministic assertions as artifact #4 and the archive has four files, none of them
  assertions, so the archive is missing a specified artifact while the store path has it. **Record
  it as a finding and open it as a question**; adding it changes the archive's bytes and belongs
  with a decision about whether the archive is meant to be the store's equal.
- **No new runtime dependency** (`duckdb`, `pyyaml` only).
- **No schema versioning** on the sink contract or the store tables (`LES-001`).
- **Do not touch `windowing/`, `ingest/`, or `synth/`.** Every one of `Q7`–`Q15` stays where it is.
- **No capture contents anywhere** (`LES-003`); synthetic fixtures only.

## Validation

- `./scripts/verify_tier.sh` passes, all four steps, **unchanged** — including the archive
  equivalence step and the `--vocabulary` pinned re-run.
- **The null test, and it is the acceptance criterion:** for the seeded synthetic capture, the
  feature archive and the belief store produced *through the new driver* must be **byte-identical**
  (archive) and **row-identical** (store) to what the current code produces. This is plumbing; any
  difference is a defect unless you can name it. Capture the current outputs first, on the current
  build, before editing `src/` (`LES-018`).
- **Determinism holds:** `verify_determinism.py`, both passes byte-identical. The recorded synthetic
  feature-archive digest is `99b97b747058e6ec` — it must not move.
- **Multi-sink actually works:** one run writing store + evidence + jsonl together produces all
  three, and each is identical to what a single-sink run of the same kind produces.
- **Partial-failure behavior is tested**, not just chosen: a sink that raises mid-run must produce
  the stated outcome, and the test must assert what the operator is told.
- Existing tests pass unchanged except where a renamed symbol legitimately moved — list those.

## Findings to report (record, do not fix)

1. **Is the feature archive supposed to be the belief store's equal?** It is missing §5.4 artifact
   #4 entirely. Say whether that is a gap or a deliberate split, because the answer decides whether
   `assertions.jsonl` is a follow-up or a non-goal.
2. **What run metadata does the belief store actually need** to satisfy §10's "same build on both
   sides"? Report what you stamped and what a reconciler would still be missing.
3. **Does any sink hold run-level state** the way `JsonlSink` holds its `VocabularyObserver`? If so,
   is its bound stated and enforced at insert (`LES-014`)?
4. **Does the `close()` summary contract still fit** now that a store sink reports through it —
   specifically, does `rows` mean the same thing for a DuckDB projection as for a JSONL file, or is
   it a field whose name no longer answers its own question (`LES-015`)?

## Outcome (2026-08-06)

**Delivered.** `src/aximprint/sinks/{base,store_sink,evidence_sink}.py` (the `WindowSink` contract +
registry, moved per D1; `StoreSink` and `EvidenceSink`), `src/aximprint/provenance.py`
(`build_stamp()`, shared by `StoreSink` and `JsonlSink`), `cli._pipeline` as the one driver behind
both `ingest`/`features`, repeatable `--sink NAME=DEST` with `--store`/`--evidence-log`/`--out` kept
as aliases, and a `runs` table in the belief store. `features/base.py` is gone, no shim. Suite
**189 → 252 tests**, all passing.

**Determinism: unchanged, and that is the proof.** `verify_determinism.py` (synthetic seeded
capture): all three invariants hold, both passes byte-identical. The feature-archive digest recorded
before touching `src/` (`LES-018`) — `99b97b747058e6ec` — is the same digest the landed code
produces; independently re-confirmed after the fix round. `./scripts/verify_tier.sh` green on all
four steps, unchanged from the metadata-archive round.

**The null test, and it held:** feature archive byte-identical; all five pre-existing belief-store
tables (`node_features`, `edge_features`, `assertions`, `identity_bindings`, `window_health`)
row-identical to the pre-round baseline; evidence log identical. A multi-sink run (store + evidence +
jsonl together) matches three separate single-sink runs of the same kinds, file for file and table
for table.

**Findings, answered** (full text in CHANGELOG "Egress: one sink registry, both output paths through
it," 2026-08-06):

1. **The feature archive is not the belief store's equal, and that is now a decided gap, not a
   silent one.** §5.4 artifact #4 (deterministic assertions) has no file in the archive; `Q16` opened
   to decide whether it is a follow-up or a non-goal.
2. **The store now stamps what §10 needs** (`run_meta` + `build_stamp()`, once per clean close) —
   but a reconciler still cannot attribute a specific `window_health` row to the run that wrote it,
   because `runs` records a window *count*, not the `window_start` values. `Q17` opened.
3. **No sink besides `JsonlSink` holds run-level state.** `StoreSink`/`EvidenceSink` hold only a
   connection/handle, bounded independently of the contract; both report `vocabulary: {}`.
4. **`rows` does not mean the same thing for every sink.** A fresh JSONL archive makes "written this
   run" and "in the file" the same number; a DuckDB table or an append-only log accumulates across
   runs, so `rows` there is "this run's insert count," never the destination's total. Recorded, not
   renamed — a rename is a decision for every caller of the summary, not this round's to make alone.

**Four real defects, reproduced and fixed in the same round** (full detail in the CHANGELOG entry,
not repeated here): a repeated `--sink`/alias destination silently doubled the belief store, because
DuckDB permits — rather than refuses — a second same-configuration connection to an already-open file
(`M1`); a sink-specific option (`--vocabulary`) that reached no open sink used to print a false
success message (`M2`); the option router was `inspect.signature`-based and could fail silently on a
decorator or a rename, replaced by a declared `accepts` (`M3`); the fan-out's three failure surfaces
(open/write/close) were only handled at one of them, fixed by a `close()`/`abort()` split
(`M4`/`M5`); and, pre-existing and surfaced by the tester rather than introduced here,
`BeliefStore.write_window` crashed on an empty or edge-less window for want of an empty-list guard
**and** a transaction (`T1`/`S4`) — both were needed, neither alone was enough.

**One trap this round could only mitigate, not remove.** The registry is populated by import:
`import aximprint.sinks` alone registers `store`/`evidence` but not `jsonl` (declared in the sibling
`aximprint.features`), so a caller that imports only `sinks` sees a registry that looks complete and
is not — this already fired once, as a real `KeyError` in an earlier draft of
`scripts/verify_determinism.py`. The fix shipped here is a better error message naming where the
missing sink actually lives (`S10`); `docs/WORK_ORDER_retire_features_package.md` is drafted and
queued to remove the condition structurally, by moving every registrant beneath `sinks/`. It is not
ratified and has not started — see that work order's own D2 for the part flagged for challenge before
it is built.
