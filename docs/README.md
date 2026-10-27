# docs — the live documentation set

Everything in this folder describes the project **as it is now**. History is git.
The pre-consolidation ledgers are under `archive/` and are reference only.

| File | What it is | Change rule |
|---|---|---|
| `SESSION_STATE.md` | The cross-session handoff: branch and push state, what is in flight, ordered next steps, open decisions, the gotchas that bite now. | **Replaced** at every handoff (`/handoff`), never appended; checked against the repo at every start (`/catchup`). Keep it under 8 KB. |
| `ARCHITECTURE.md` | The design of record for both packages, the content catalog and the control-plane API. | Present tense, no dates, no build status, no counts. Edit in place when the design changes. |
| `CONVENTIONS.md` | What things are called, the resource tier tags, the layering rules, the engineering and documentation rules. | Edit in place. |
| `OPERATIONS.md` | How to install, configure, run the loop, run discovery, serve the API, and what bites in a real run. | Edit in place; a gotcha stays only while it still bites. |
| `contracts/` | The version-locked specs implementations must satisfy, moved verbatim from the package docs. | A change bumps the schema version and needs the user first. See its README. |

Per-feature specs live **beside their code** as `SPEC.md` (start from the root
`Spec.template.md`). The two seam specs at the root, `api/Spec.md` and
`ui/Spec.md`, own the API and console contracts.

## Rules

These follow root `Claude.md` "Scope discipline", rule 3.

- Docs match reality; they do not accumulate. Git history is the changelog.
- No narrative CHANGELOG, no `WORK_ORDER_*` files, no `LESSONS` store, no
  doc-counter commit hooks. The former machinery is in the archive and stays there.
- Do **not** create `docs/WORKFLOW.md` or a root `.claude-workflow.env`. The
  host-global pipeline hooks (`~/.claude/settings.json`) treat either file as the
  onboarding marker and start enforcing the retired machinery (CHANGELOG
  authority lines, doc-stats counters, lessons injection) on every edit and commit.
- A design change is an edit to `ARCHITECTURE.md` or `CONVENTIONS.md`, not a
  new file.

## The archive

`archive/engine-docs/` (formerly `engine/docs/`) and `archive/discovery-docs/`
(formerly `discovery/docs/`) hold the former ledgers verbatim: CHANGELOGs,
LESSONS, PRODUCT_GAPS, OPEN_QUESTIONS, every WORK_ORDER and SLICE, the numbered
design docs, journals, runbooks, reference notes, reports and the vendor PDFs.
The two full pre-consolidation orientation files are
`archive/CLAUDE.engine.full.md` and `archive/CLAUDE.discovery.full.md`.

Citations elsewhere in the tree of the form `docs/<name>` (code docstrings,
catalog YAML comments, CLI help strings, scripts) predate this layout. Resolve
them like this:

| Citation | Resolves to |
|---|---|
| `docs/IR_SCHEMA.md`, `PLANNER.md`, `OP_CATALOG.md`, `VALIDATOR.md`, `ADAPTER.md`, `IMAGE_MANIFEST.md`, `IMAGE_BUILD.md` | `docs/contracts/<same name>` |
| `docs/SPEC_network_ir.md`, `docs/AXAPI_CONTRACT.md` | `docs/contracts/<same name>` |
| any other `docs/<name>` cited from `engine/`, `catalog/`, `config/` or `o2/` | `archive/engine-docs/<name>` |
| any other `docs/<name>` cited from `discovery/`, or written as "aximprint `docs/...`" | `archive/discovery-docs/<name>` |
