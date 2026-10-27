# Session State — the cross-session handoff

<!-- REPLACED at every handoff, never appended (`/handoff`); checked against the repo at every
start (`/catchup`). Keep it under 8 KB and about NOW. A stale handoff is worse than none,
because it will be believed. -->

**Last updated:** 2026-10-27 · **Branch:** `wip/api-discovery-runs`, at `master`'s tip
(`614d0ed`); no remote configured, nothing to push.

## What this session did

Caught up from the two inherited handoffs and found both stale: they described the
pre-consolidation repos (branches, workspaces and files that no longer exist here). Retired
them and the handoff archive, moved `engine/docs/` and `discovery/docs/` verbatim to
`archive/engine-docs/` and `archive/discovery-docs/`, moved the nine version-locked contract
specs to `docs/contracts/`, and wrote this root `docs/` set (README, ARCHITECTURE, CONVENTIONS,
OPERATIONS, this file). Root `Claude.md`, `catalog/README.md` and four doc-reading tests were
re-pointed.

## Current state

- **Uncommitted, two groups.** (1) The docs restructure above (about 230 renames, 4 deletes,
  6 new files, 2 edits). (2) Seam **A1, discovery runs**, built 2026-10-20 and untouched since:
  `engine/src/axrange/api/discovery.py` (new), `engine/tests/test_api_discovery.py` (new),
  `discovery/tests/test_ingest_plugins.py` (new), plus edits to `api/app.py`, `api/dtos.py`,
  `api/service.py`, `api/Spec.md`, `aximprint/cli.py` and every `ingest/*` plugin
  (`config_schema`, `list_plugins`, the `aximprint plugins` verb).
- **Tests:** both full suites pass after the restructure (`engine`: 3916 passed, 18 skipped;
  `discovery`: 2237 passed, 1 skipped). Four tests read doc files and were re-pointed:
  `test_ir_schema.py` at `docs/contracts/`, `test_zeek_parquet_source.py` and
  `test_fidelity_probe.py` at the archive, and `test_pipeline_scripts.py` (the retired lessons
  and doc-stats tooling) is skipped at module level.
- **dev45 environment:** unverified since 2026-10-05. Last known: `corp-twin-3dae3188` standing
  with its tap and `caprate-max` traffic, capture-to-disk off on XPress-SW-A, the `:8080`
  console down. `~/.axrange/state.db` was touched 2026-10-26, so something ran against it.
  Do not destroy anything there without asking.

## Work in progress

- **A1** is complete per `api/Spec.md` and its tests; it has not been reviewed or committed.
- **This restructure** is staged for review. `engine/.claude/`, `discovery/.claude/` and the
  doc-tooling scripts in both `scripts/` dirs (`doc-stats.py`, `doc-budget.py`,
  `inject_lessons.py`, `hooks.py`, `claude-export.sh`, `claude-import.sh`) still point at
  the moved `docs/` trees and are dead as they stand.

## Next steps (ordered)

1. Review and commit the docs restructure (one commit: `docs: retire the package ledgers ...`).
2. Review and commit A1 (`api: discovery runs ...`), then decide whether `wip/api-discovery-runs`
   merges to `master` now.
3. Critical path item 2 (root `Claude.md`): attack-inject plus confirm as one action — add the
   O2 read-back to `POST /api/exr` (seam A3 in `api/Spec.md`).
4. Decide the fate of the dead per-package pipeline config and doc tooling (above): delete, or
   re-point at the root set.

## Open questions / decisions pending the user

- The standing dev45 twin: keep as the live-verify bed, or destroy (order `tfc -> tap -> te`,
  ids above)?
- Delete `engine/.claude/`, `discovery/.claude/` and the doc-tooling scripts, or keep?
- `aximprint ingest --source` help text and many code docstrings cite `docs/<name>` paths that
  now resolve under the archive (`docs/README.md` has the mapping). Leave, or sweep?

## Gotchas

- The host-global hooks activate on `docs/WORKFLOW.md` or `.claude-workflow.env` at the repo
  root. Neither exists; do not add them.
- The two `tests/` trees must be run separately (conftest name clash).
- Everything operational that still bites is in `docs/OPERATIONS.md` §6.

## Environment

One venv at `.venv/` with both packages editable. `AXRANGE_API_TOKEN` is held by the user only.
Live-deploy state is `~/.axrange/` (`state.db`, `serve-8080.log`). `config/adapter.conf` is
gitignored and operator-local.
