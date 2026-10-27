# Session State — the cross-session handoff

<!-- REPLACED at every handoff, never appended (`/handoff`); checked against the repo at every
start (`/catchup`). Keep it under 8 KB and about NOW. A stale handoff is worse than none,
because it will be believed. -->

**Last updated:** 2026-10-27 · **Branch:** `wip/api-discovery-runs`, five commits past
`master` (`614d0ed`); no remote configured, nothing to push (never push).

## What this session did

1. Retired the stale per-package handoffs and docs trees into `archive/`, wrote this root
   `docs/` set, re-pointed references (commit `a60f496`).
2. Committed seam **A1, discovery runs** (`008d3be`).
3. Built seam **A7, discovery sources** (`39f8349`): a saved sensor endpoint + access
   (direct / SSH tunnel × none / basic / API key) in `~/.axrange/discovery.db`, probe,
   source-driven runs, `GET /api/discovery/runs`. `aximprint` plugins publish `source_kind`
   and `probe()`; `aximprint probe` verb; the axquery transport sends the environment
   credential. 43 engine + 14 discovery tests.
4. Started the operator console in **`ui/`** (React 18 + Vite 8 + TS + Vitest; stack
   recorded in `ui/Spec.md`): Discovery sources (list, editor with the access choice,
   detail with Test connection + Run form drawn from the plugin schema), Runs (list, polled
   detail), Imprint summary. 30 Vitest tests (C1.1–C1.4). Built to `ui/dist`.
5. Ran the loop for real against **corp-apcn-reader** (see below).

## Current state

- **Tests:** engine 3959 passed / 18 skipped; discovery 2251 passed / 1 skipped; `ui`
  typecheck clean, 30 passed. The frozen `o2/console` suite has 6 files broken by the
  2026-10-20 layout moves (`test/fixtures/realBundles.ts` line 26 and
  `test/helpers/jsonschema.ts` open `twins/` and `axrange/api/schemas/`, now
  `catalog/twins/` and `engine/src/axrange/api/schemas/`); not fixed, it is frozen.
- **Live:** `axrange serve` is running on `0.0.0.0:8080` (console reachable from other
  hosts at `http://10.80.72.52:8080/`; firewalld is inactive on this node) with `--spa-dir ui/dist`,
  `AXRANGE_IMPRINTS_DIR=catalog/twins`, `AXRANGE_AXIMPRINT_STORE=~/.axrange/discovery/ir.duckdb`,
  `AXRANGE_DISCOVERY_TIMEOUT_S=172800`, token from `~/.axrange/api-token` (0600, generated
  this session; the user's own `AXRANGE_API_TOKEN` was not used). Log: `~/.axrange/serve-8080.log`.
- **The corp-apcn-reader source** is `src-2ae78e66` in `~/.axrange/discovery.db`: axquery at
  `http://127.0.0.1:9090` on the appliance's loopback, reached by SSH tunnel as
  `fxadmin@corp-apcn-reader.swlab.axellio.dom` (10.80.65.8) with `~/.ssh/id_ed25519`, no API
  auth (`--auth-disabled`). The appliance's axquery holds 30 days / 69 chunks of
  `axellio-hq/corp-apcn-rdr1` (12 streams), 2026-09-06 19:30 → 2026-10-06 19:30 UTC. It is
  **not** the two 20-hour-old test instances on `:26244`/`:26756` (a 2023 golden dataset),
  and nothing was started or written on the appliance.
- **The ten-day run.** The first attempt (`disc-ef9ad362`, started 2026-10-27 16:03 UTC)
  **failed after 15 min** in `ingest`: the one-second leaf `[1790496071, 1790496072)` held
  5,148 rows against a 5,000-row page cap (4,641 of them one host's port sweep starting in
  the next second; the server's `before` is inclusive at second granularity). `PAGE_ROWS` in
  `discovery/src/aximprint/ingest/axquery/sources.py` was raised 5,000 → 20,000 with the
  measurement recorded in its comment, and the one-hour window re-cast to the same 249 hosts /
  42 segments at both values. The second attempt **`disc-4a1314cc`** (imprint `corp-apcn-10d`,
  window 2026-09-26 19:30 → 2026-10-06 19:30 UTC, `network=corp-apcn`) started 2026-10-27
  20:55 UTC; a one-hour slice takes ~4 min end to end, so expect the order of a day. It lands
  in `catalog/twins/corp-apcn-10d/` (inside the checkout, uncommitted) with the belief store at
  `~/.axrange/discovery/corp-apcn-10d.belief.duckdb`. Watch it at
  `http://10.80.72.52:8080/discovery/runs/disc-4a1314cc` or
  `curl :8080/api/discovery/runs/disc-4a1314cc`. If `axrange serve` is restarted the run
  record is lost (in-memory) and the subprocess is orphaned: check `pgrep -af "aximprint ingest"`
  first. A second over the 20,000 cap anywhere in the ten days fails the run the same way.
- **dev45 environment:** unverified since 2026-10-05 (`corp-twin-3dae3188` and its tap/tfc);
  do not destroy anything there without asking.

## Next steps (ordered)

1. When `disc-4a1314cc` completes: open `/imprints/corp-apcn-10d`, review the bundle, decide
   whether `catalog/twins/corp-apcn-10d/` is committed. If it failed, the record's `error`
   has the stage and aximprint's stderr tail.
2. Review the uncommitted UI + docs commit (this handoff is part of it) and the three
   commits above; decide whether `wip/api-discovery-runs` merges to `master`.
3. Critical path item 2 (root `Claude.md`): attack-inject plus confirm as one action on
   `POST /api/exr` (api seam A3's neighbour); then UI T0 (the v0 loop tracer) now that a stack
   and shell exist.
4. Fate of the dead per-package pipeline config and doc tooling (`engine/.claude/`,
   `discovery/.claude/`, `scripts/doc-*.py`, `hooks.py`, `claude-export/import.sh`).

## Open questions / decisions pending the user

- Commit `catalog/twins/corp-apcn-10d/` once it lands, or keep discovery output out of the
  catalog (point `AXRANGE_IMPRINTS_DIR` elsewhere)?
- The standing dev45 twin: keep as the live-verify bed, or destroy?
- Fix the six fixture paths in the frozen `o2/console` tests, or leave frozen as is?

## Gotchas

- The host-global hooks activate on `docs/WORKFLOW.md` or `.claude-workflow.env` at the repo
  root. Neither exists; do not add them.
- The two Python `tests/` trees must be run separately (conftest name clash). `ui` tests are
  `cd ui && npm run test`.
- `npm install` in `ui/` hit an npm arborist bug (`edgesOut` of null); `ui/package-lock.json`
  is the console's resolved set and `npm ci` works. Use `npm ci`.
- Killing `axrange serve` with `pkill -f "axrange serve"` from a wrapper shell whose own
  command line contains that string kills the wrapper too; match the python process.
- Everything operational that still bites is in `docs/OPERATIONS.md` §6, including the three
  new ones (long runs, BatchMode ssh, imprints dir inside the checkout).

## Environment

One venv at `.venv/` with both packages editable; node 22.23 / npm 10.9 for `ui/`.
Live state is `~/.axrange/` (`state.db`, `discovery.db`, `discovery/`, `api-token`,
`serve-8080.log`). `config/adapter.conf` is gitignored and operator-local.
