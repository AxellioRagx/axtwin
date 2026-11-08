# Session State — the cross-session handoff

<!-- REPLACED at every handoff, never appended (`/handoff`); checked against the repo at every
start (`/catchup`). Keep it under 8 KB and about NOW. A stale handoff is worse than none,
because it will be believed. -->

**Last updated:** 2026-11-08 (evening, R5) · **Branch:** `wip/design-platforms-canvas`. Pushed to origin
through the R5 items below, and fast-forwarded into `wip/api-discovery-runs` and `master`
(all three at the same commit, 2026-11-08; push only when the user asks). Rule: commit each piece as it lands, staged by path; never `git add -A`.

## What this session did

The user's re-test of 08:00 verified R4 and left two items plus a count mismatch (R5):

- **`f370f95`** R5-1: the cast's provenance was in the YAML all along (111 `source:` lines in a
  fresh cast); the re-test read it through `GET /api/twin-configs/{id}`, whose segment and node
  summary rows had no fields for it. They carry `source`/`confidence`/`vantage` now. What the
  cast really left bare: **conduits**. Each now gets `discovered` + the PIT's vantage, or
  `asserted` (also when observed). No confidence, because nothing scores a pair. Schema +
  expander accept/check the three fields on a conduit.
- **`482f3dd`** R5-2: every site pill a router serves is always on its rail; Fan-out (default 4)
  budgets loose pills only; `+N more` holds only loose segments. An open site pages its row by
  the budget with its own `+N more` (`site:<c>|<p>#2`, ...; closing the site closes its pages).
  The fold refit (`canvas/fit.ts`) frames router + row, never below 50 %, left edge when too
  wide. `+N more`, `fold N back` and the strip are one count (was 54 vs 43); closed sites get
  their own strip line. `Placement.rows` publishes each rail / row.
- **`968f587`** SPEC checklist "Review round 5" with what the eye saw; topology-layers §7.6.

## Current state

- **Tests:** ui typecheck clean, 30 files / 462 (new `canvas.foldbudget.test.tsx`, over the real
  corp-twin bundle). Discovery full suite 2474 passed. Engine full suite 4249 passed, 3 failed:
  `test_twin_bundle_layout` / `test_twinconfig_registry` x2 scan the real catalog and trip on
  the **untracked** folders (`canvas-test-exp1`, `mylab`, `corp-apcn-reader-20261028` with no
  `twin.yaml`), not on this change.
- **Serve:** :8080 restarted twice this round (engine change, then the rebuilt `ui/dist`): now
  pid 1587831, `none` mode, env from the live `/proc/<pid>/environ` minus the token. Script:
  scratchpad `restart.sh <envfile>` (env in `serve-r5.env`).
- **Eye checks** (headless, scratchpad `R5-*`, `r5fold.cjs`): corp-twin collapsed (4 sites, 4
  loose, `+7 more`, 73 %), 10.180 once (4 + `+20 more`, 65 %), twice (8 + `+16 more`, 50 %, left
  edge), closed, carrier open (`fold 7 back`); fresh cast `r5-probe` through the API: no row
  without a source, conduits and taps all `discovered`, overlay on; **deleted**. switch-test has
  no working copy; dev45 untouched.
- Untracked, the user's: the catalog folders, `ui/dist.old/`; mine: `ui/dist.prev/` (the
  previous build, safe to delete).

## Not done / honest gaps

- A second page of corp-twin's 10.180 lands at the 50 % floor framed from the left; the newest
  segments are a pan away (as the brief specified). The rail's right end can sit off-screen
  after a site opens, because the refit frames the router + row, not the whole rail.
- Conduit `confidence` is absent by design (no per-pair score exists); say so if asked.
- The Deploy panel has still never run a real deploy (plan only). *Split and assert* proven
  in jsdom only.
- corp-apcn-reader-20261028 still invalid (IPv6 /64, CV-012): the user's call.
- Yours to decide: the serve's auth mode (`none` on 0.0.0.0).

## Gotchas

- `cd` does not persist between Bash calls, and a failed `cd` leaves you in the checkout: never
  chain a destructive git command after a `cd` without `&&`.
- A subagent worktree is created from an old base here (39fa362): make one by hand.
- corp-twin has segments that SHARE a CIDR (10.180.34.0/25 ×4, 10.180.93.0/24 ×2): never map
  a host to its pill by CIDR; `ExtraLeg.home` carries the segment name.
- `test/helpers/canvasFixtures.tsx` imports `imprints.page.test`, so every file using it re-runs
  those tests under its own name (not a new failure).
- The engine's catalog-scanning tests fail while untracked twin folders sit in `catalog/twins`.
- A UI-only change goes live by swapping `ui/dist` (the serve reads it per request); an engine
  change needs the restart. `restart.sh` stops the serve FIRST: pass the env file.
- React Flow drops an edge whose `sourceHandle` names a handle of the wrong type; its
  `onSelectionChange` echoes a programmatic selection. `rf.screenToFlowPosition` snaps unless
  `{ snapToGrid: false }`. `ugrep` reads `model/graph.ts` as binary: `grep -a`.
- Playwright: off-viewport tree nodes refuse `.click()`; use `el.evaluate((e) => e.click())`.
- `ui/`: `npm ci` only; never build into the served `ui/dist` (build `dist.new`, swap). The
  contract files change only with the user's confirmation and a version bump.

## Environment

One venv at `.venv/`, node 22 for `ui/`, live state in `~/.axrange/`. Playwright 1.61 at
`/home/rocky/fx-cap/mgmtui/node_modules/playwright` + `~/.cache/ms-playwright/chromium-1228`
(scripts in this session's scratchpad: `shot.cjs`, `r5fold.cjs`, `stress.cjs`, `restart.sh`).
dev45 API 10.80.76.108:8006, SSH root.
