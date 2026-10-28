# Session State — the cross-session handoff

<!-- REPLACED at every handoff, never appended (`/handoff`); checked against the repo at every
start (`/catchup`). Keep it under 8 KB and about NOW. A stale handoff is worse than none,
because it will be believed. -->

**Last updated:** 2026-10-28 · **Branch:** `wip/api-discovery-runs`, five commits past
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
- **Bind:** `axrange serve` listens on `0.0.0.0:8080` (console reachable from other
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
- **The ten-day run: none in flight.** Three attempts under imprint `corp-apcn-10d` ended
  without a bundle: `disc-ef9ad362` and `disc-4a1314cc` failed at a one-second leaf over
  the page cap (fixed in `10edded` and `86cfe69`: `PAGE_ROWS` 20,000 and a floor leaf that
  overflows is re-read wider up to `LEAF_ROWS_MAX`); `disc-0a6a9379` was stopped by hand on
  2026-10-28 11:44 UTC at the user's request (the serve process of the day had no cancel
  route yet; its record read `failed, exit -15`). Everything the runs wrote is gone:
  `~/.axrange/discovery/` holds nothing now (belief stores, the IR store `ir.duckdb` and
  the saved record were removed), `catalog/twins/` has no `corp-apcn-*` bundle, and the
  API's run list is empty after the restart. The source `src-2ae78e66` in
  `~/.axrange/discovery.db` is kept. Measured: ~25 min of ingest per day of index, so a
  ten-day read is ~4–5 h.
- **Live:** `axrange serve` was restarted 2026-10-28 11:44 UTC on the current code
  (progress and cancel live) by `~/.axrange/restart-after-run.sh`, which has exited;
  same bind `0.0.0.0:8080`, same env, SPA from `ui/dist` (built from `1dc0e76`).
- **Known leak, test-only:** engine tests that capture a run's job without running it
  (`background=jobs.append`, the "busy" 409 tests) leave an empty
  `/tmp/axrange-discovery-<id>-*` scratch dir each; 33 were removed by hand on 2026-10-28.
- **dev45 environment:** unverified since 2026-10-05 (`corp-twin-3dae3188` and its tap/tfc);
  do not destroy anything there without asking.

## Next steps (ordered)

1. Start the ten-day run again from the console (source `corp-apcn-reader`, last 10 days,
   imprint `corp-apcn-10d`) and watch the progress bar; on completion decide whether
   `catalog/twins/corp-apcn-10d/` is committed. A one-hour run (`disc-f6aa0f01`,
   2026-10-28 13:42, six minutes) installed `catalog/twins/corp-apcn-reader-20261028/`,
   untracked: commit or remove it. Run records now persist in `~/.axrange/discovery.db`
   (table `runs`); the three from before persistence landed are saved as
   `~/.axrange/discovery/disc-*.json`. The Imprints page has a bin per bundle and the
   runs table a bin/× per run.
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

- The second discovery stage is `bake` (2026-10-28; `aximprint materialize` is kept as an
  alias). IR schema `1.5` adds `ir_pit.name`, a renamable label beside the unchanging id;
  a `1.4` store gains the column on open. `aximprint pit list|rename`,
  `GET/PATCH /api/discovery/pits`. The id was NOT made numeric: it is the identity every
  cast sidecar, drift report and the recompile seam key on, and it must mean the same thing
  outside the store it came from.
- A short capture can leave a role with no synthesized persona (its hosts dialled nothing);
  the cast now DROPS that role's traffic assignment with a caveat instead of leaving it
  naming the catalog persona, which `install-cast` refused after the whole ingest had run
  (`disc-7b100b4e`, one hour of CorpAPCN, `monitoring-collector`). `cli._drop_unsynthesized_roles`.
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
