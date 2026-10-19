# Conventions — aximprint

Workflow imported from the axrange project family, with two deliberate deltas (§1). When in doubt,
axrange house style applies.

## 1. Deltas from axrange (ratified, user 2026-08-02)

1. **No authority split between surfaces.** axrange and its predecessors held a hard separation:
   design authority on desktop chat, coding in Claude Code sessions. aximprint does **not**. Design
   and code may happen in either surface; a decision is ratified by the user regardless of where it
   was discussed. Design-of-record docs still capture ratified decisions — the *docs* carry
   authority, not the surface.
2. **Schemas are incubating — no versioning yet.** The event model, feature shapes, plugin
   contract, and belief-store shapes carry **no version fields and no bump ceremony**. Experimental
   changes are made freely. The axrange house rule "version-locked schema → confirm + bump" is
   **suspended** for aximprint until the user ratifies a v1 for a given schema; from that moment,
   that schema (only) becomes version-locked and DESIGN_BRIEF §11 activates for it. Do not force
   experimental changes into version bumps before then.

   **This clause fired for the first time on 2026-08-14**, and it said in advance exactly what
   would happen: the user ratified the **network IR** (`docs/SPEC_network_ir.md`, schema `1.0`),
   §11 became binding **for it only**, and every other shape named above stayed exactly as it was.
   Read *"that schema (only)"* as the operative words. `tests/test_ir_schema.py` enforces the
   scope mechanically — a `SCHEMA_VERSION` outside `aximprint.ir` fails the suite.

## 2. Doc taxonomy (as axrange)

- `DESIGN_BRIEF_*.md` — design of record; ratified direction. Supersedes-in-direction notes when
  re-framing earlier work.
- `WORK_ORDER_*.md` — a bounded buildable unit: Goal / Build / Boundaries / Validation /
  Depends-on. STATUS header (OPEN / IN-PROGRESS / LANDED). Not required for early exploratory work;
  introduce them when the build arc stabilizes.
- `DISCUSSION_*.md` — captured design discussion; not yet ratified; may graduate into a brief.
- `CHANGELOG.md` — every landed change; note `Supersedes:` when re-homing ratified behavior.
- `OPEN_QUESTIONS.md` — numbered, referenced from code/docs as Q<n>, closed with a pointer to the
  resolution.
- `LESSONS.md` — LES-NNN entries: hard-won operational lessons (the axrange LES pattern). Record
  the failure mode, not just the fix.
- **Genres inherited from the `xtract`→`acquire` merge (2026-08-10), landed alongside the five above:**
  `AXAPI_CONTRACT.md` is a **formal spec** — a version-locked transcription of a wire contract this
  project's code must satisfy; correcting it is either fixing a transcription error (edit freely) or
  re-pinning to a new vendor firmware/API revision (that's a decision, not a doc edit).
  `PLAN_metadata_harvester.md` is a **plan** — precedes work orders, less binding than a
  `DESIGN_BRIEF_*`, more structured than a `DISCUSSION_*`. `RUNBOOK_CORPAPCN.md` is a **one-off
  verification runbook** — named by subject rather than by prefix, same footing as
  `SESSION_STATE.md` and `PIPELINE_MAP.md`. (This clause used to say "outside this five-(now
  eight-)genre list"; the count was already unreconcilable against the bullets it claimed to
  count, and a running total in prose that nothing checks is `LES-093`'s own failure mode. Dropped
  rather than corrected — the list is the list.) `VENDOR_API_DEFECTS.md` is a defect report (documentation vs. measured
  appliance behavior) and, with `AXAPI_CONTRACT.md`, is one of the two **sendable, vendor-facing**
  documents in this repo — coherence matters more for these two than for anything else here, since an
  outside reader (the vendor) may see them. `PRODUCT_GAPS.md` (`GAP-NNN`) is a living register of
  known deficiencies, distinct from `OPEN_QUESTIONS.md`: a `GAP` is a shipped shortfall, a `Q` is a
  design question not yet settled. **A document landed from that merge whose own header cites
  "`docs/CONVENTIONS.md` §3" for this genre list is citing its position in `xtract`'s own
  `CONVENTIONS.md`, not this file's** — here, doc taxonomy is §2 (this section) and engineering rules
  are §3; the citation was not rewritten on landing (`docs/WORK_ORDER_merge_xtract_acquire.md` step
  10 moved these documents' *content*, not every internal self-reference — see `LES-062`).
- **`PIPELINE_MAP.md` is a *state map*** — declared 2026-08-14 by the user, the first genre this
  project added rather than inherited. One screen answering the question the other genres answer
  only in pieces: **at a glance, how far along is each layer, and what does it hand the next one?**
  Named by subject, same footing as `SESSION_STATE.md` and `RUNBOOK_CORPAPCN.md`. It is **not**
  design of record (that is `DESIGN_BRIEF_*`) and **not** a decision record (that is
  `CHANGELOG.md`) — it holds no authority of its own, and a conflict between the map and the
  document that governs a claim is resolved against that document, never against the map.
  Two obligations come with the genre, both consequences of being derived:
  **(a)** every volatile claim on it names the source that governs it — a state map that asserts
  a number without saying who owns it is a second source of truth wearing a summary's clothes;
  **(b)** it carries its own **drift table** rather than a promise, because `doc-stats.py` reads
  only `CLAUDE.md` and `README.md` and so **nothing in a state map is machine-policed** — the
  discipline has to be written down where the reader is, or it is not a discipline (`LES-093`).

## 3. Engineering house rules (as axrange)

- **Fail loud, fail closed.** Guards are positive gates; an unrecognized shape is rejected, not
  passed through. Assertions over silent defaults.
- **Determinism as a feature.** Seeded randomness everywhere it exists; byte-stable outputs given
  the same inputs and seed. Window close is a pure function (brief §5.4); the synthesizer is a
  deterministic compiler (brief §9).
- **`acquire` must not import `events`** (stated ahead of the code, 2026-08-10 — DESIGN_BRIEF §0.1,
  CHANGELOG same date). The acquisition package merging in from `../xapi-pcap-extract` owns the
  capture path: appliance extraction, the pinned Zeek runner, the spool. It produces **sensor
  artifacts on disk** and nothing else; `ingest` assigns meaning to them; the CLI wires the two and
  **neither imports the other**. This is the rule that replaced "aximprint owns no capture path" when
  the repos merged — the old guarantee was an accident of packaging, this one is checkable. It is
  also what keeps Xpress a drop-in later: a new `acquire` path plus a new parser table, with nothing
  between them renegotiated. **Landed 2026-08-10 (the merge build, step 4): now a real test, not
  just a stated rule** — an AST walk over `acquire`'s imports, checked in both directions
  (`acquire`→nothing downstream; nothing downstream→`acquire` either, since the CLI is the only
  legitimate wiring point) and mutation-proven (deliberately violating the rule fails the test). A
  convention nobody checks is `LES-014`'s "a bound stated in a comment is not a bound" — this one is
  now checked.
- **Layering imports downward only.** `ingest` plugins import the event model; `windowing` imports
  events; `learners` import windowing outputs; **the output-sink layer is two packages, not one**
  (`WORK_ORDER_egress_sink_unification.md`, 2026-08-06) — `sinks` (the `WindowSink` contract +
  registry, plus the `store`/`evidence` writers) and `features` (the `jsonl` writer and its
  categorical-vocabulary machinery). Both import windowing outputs and events, and both are imported
  by nothing but the CLI; `sinks/store_sink.py` additionally imports `store` (the sanctioned "writer
  imports store" direction, not its reverse) and `features/jsonl.py` imports *down* into `sinks` for
  the contract. `store` is imported by all writers; `synth` reads the store. No upward imports.
  **Two packages sit BELOW every layer** and may be imported from any of them without creating an
  upward edge, because neither imports anything from `aximprint`: `provenance` (the build
  fingerprint) and **`catalog`** (2026-08-14, `docs/WORK_ORDER_synth_v1.md` phase 2 — role rules
  and the twin content each role implies, read by `learners` for inference and by `synth` for
  compilation; the alternative was `learners` importing `synth` or the reverse, upward either way).
  They differ in one respect that matters to the `acquire` guard above: `provenance` is exempt from
  it, `catalog` is **forbidden** — not on cycle grounds, which would exempt both, but on meaning.
  `catalog` holds twin content (axRange image ids, persona names, sizings), and `acquire` producing
  sensor artifacts while knowing what a twin role is would satisfy the letter of the layering rule
  and defeat the reason it exists.
  **`operator_assertions` joined them 2026-09-05** (`docs/WORK_ORDER_reference_ingest.md` S2) — a
  top-level MODULE holding one thing, the `axr-assertions/1` document that axRange's Console writes
  and `cast --assertions` reads. It sits below every layer for the ordinary reason (it imports
  nothing from `aximprint`) and exists as a separate module for a specific one: **two consumers on
  opposite branches need the same parse** — `ingest/reference/` maps the document into belief-store
  rows, and `synth/compiler.py` resolves it against a PIT — so a module holding only the document
  keeps them from inventing a sideways edge. **Forbidden for `acquire`** on `catalog`'s reasoning,
  not on cycles: it declares the closed sets of overridable twin fields, axRange's SDN zone types
  and a `resources` shape mirroring `catalog/roles.yaml`, and that is twin content.
- **Reference ingest is a SECOND plugin kind, with its own registry** (2026-09-05,
  `src/aximprint/ingest/reference/`): non-sensor, snapshot-shaped readers of out-of-band documents
  (the operator assertions file today, an LDIF directory export next). Kept out of
  `ingest/base.py::_REGISTRY` deliberately — that registry answers *"which sensors can this build
  read"*, which is what `--plugin` and the doc-stats `plugins` counter both mean, and a reader that
  has never seen a packet does not belong in the answer. Same "no credential, no bind, no network
  reach" posture as the rest of the project: `read()` takes a local path and nothing else.
- **`ir` sits between `store` and `synth`** (2026-08-14, `docs/SPEC_network_ir.md`): it reads the
  belief store and is read by the twin compiler, the drift comparator, and any other consumer of a
  documented schema. Forbidden for `acquire` on the same meaning-not-cycles grounds as `catalog` —
  and note that `ir/schema.py` imports nothing at all today, so a cycle-based check would wave both
  of them through. **It is also the one package permitted to declare a schema version**
  (DESIGN_BRIEF §0.2 as amended); `tests/test_ir_schema.py` fails if a `SCHEMA_VERSION` appears
  anywhere else.
  **Split-registry caveat:** sinks register into `sinks._REGISTRY` by import side effect — `store`
  and `evidence` register on `import aximprint.sinks` alone, but `jsonl` is declared in
  `aximprint.features` and registers only once that package is also imported somewhere in the
  process, so a bare `import aximprint.sinks` yields a registry that looks complete and is not
  (`LES-020`). The CLI imports both; anything else that wants the full registry must too, until
  `docs/WORK_ORDER_retire_features_package.md` (drafted, not ratified) closes the split
  structurally.
- **Content vs code.** Catalogs and templates are data files, not code (axrange
  `target-environments/` pattern).
- **Provenance everywhere.** Every assertion carries evidence class, confidence, source, and time
  bounds. Imputed never masquerades as observed.
- **Forensic completeness.** **Ratified 2026-08-12 by the user; supersedes the former "Privacy" rule**
  (CHANGELOG 2026-08-12). aximprint **collects everything it observes** and refuses no field on
  disclosure grounds — no redaction, no privacy-motivated skips, no "we took the narrow set." It is
  an evidence collector recording facts; **the disclosure decision belongs to the consumer,
  `axrange`**, which knows what its output is for and who will read it. The engineering reason, not
  just the policy one: a field withheld at collection time is indistinguishable downstream from a
  field that was never observed, so collection-time redaction directly attacks the provenance claim
  in the bullet above. Two things this does **not** license, because neither is a disclosure
  judgment: (a) **cardinality bounds stay** — `MAX_KEYS`, `SERVICE_MAX_KEYS`, `TOP_PEERS` are store
  economics (`LES-017`, `Q9`, where `bound_dns` alone was 93% of the store), and truncation is always
  disclosed; (b) **run data still does not go in the repo** — see `LES-003`, now a repo-hygiene rule
  with no privacy framing, the same reason a 9 GB pcap does not get committed.

## 4. Pipeline route

The axrange subagent route (code-writer → tester → code-reviewer → doc-writer → gated live-verify)
is available but **optional** during incubation; use judgment. Live-verify gates return when work
orders do (validation against real Zeek logs, and later against an axrange-deployed twin for the
round-trip self-test).

**Imported and wired 2026-08-03** (CHANGELOG). Entry point `/dev <task>`; agents in
`.claude/agents/`; full model in `docs/WORKFLOW.md` "Pipeline mode". Two adaptations from axrange
worth knowing: the risk gate is **inverted on schemas** (changing an incubating schema is free —
*ratifying* one needs the user, §1.2 above and `LES-001`), and the live tier is **real capture
data** rather than a cluster — human-gated on **cost and blast radius** (a live production appliance,
multi-GB transfers), not privacy, which §3 retired 2026-08-12. The cluster-free middle tier
(`scripts/verify_tier.sh`) enforces the determinism invariants from brief §5.4 and §9 — three
checks as of 2026-08-03: window close, synth compiler, and the emitted feature archive — which the
unit suite structurally cannot catch.

## 5. Naming

- Project: `aximprint`. Package: `aximprint`. CLI: `aximprint`.
- Components stay role-shaped and boring: the daemon logic is the **learner**, the DB is the
  **belief store**, the config generator is the **synthesizer** (`aximprint synth`). The metaphor
  (imprint / cast) lives in prose and UX, not identifiers.
- **Ruling on the `acquire`/`windowing` filename collision (2026-08-10, `docs/WORK_ORDER_merge_xtract_acquire.md`
  "Findings to report"):** `acquire/windows.py` (extraction time windows — how a harvest run tiles a
  time range into pcap-sized chunks) and `windowing/windows.py` (feature time windows — the belief
  store's own hourly/sub-bucket windows) are the same filename meaning two unrelated things, one
  package tree; likewise `acquire/manifest.py` (the `xtract pcap` resume ledger) against the feature
  archive's `manifest.json` (`features/vocabulary.py`'s categorical-vocabulary artifact). **Do not
  rename either side to disambiguate.** Paths already disambiguate correctly (`src/aximprint/acquire/windows.py`
  vs. `src/aximprint/windowing/windows.py` are never confused by an import, a test collection path,
  or a human reading a traceback), and `D1` of the merge work order forbade reorganizing during the
  move — a rename now would be exactly the "unreviewable diff" `D1`/`D2` were written to avoid, for a
  collision that a full path already resolves. **The actual failure this collision caused was in
  `docs/LESSONS.md`'s `Scope:` field**, which matches on bare filenames as well as full paths (`scripts/inject_lessons.py`) —
  so a Scope token written as bare `windows.py` or `manifest.py` genuinely does collide, silently
  injecting the wrong lesson under a filename that resolves to two different files. The rule that
  follows: **`docs/LESSONS.md` `Scope:` tokens must be path fragments (`acquire/windows.py`,
  `acquire/manifest.py`), not bare filenames, wherever the same name exists in more than one place in
  the tree** — this is checkable today with the existing tool (`python3 scripts/inject_lessons.py
  <path>`, run against a file from each colliding location) and is the fix this round actually
  applied (`LES-062`, `LES-063`), not a module rename.
