# Session State — the cross-session handoff

<!-- REPLACED at every handoff, never appended (`/handoff`); checked against the repo at every
start (`/catchup`). Keep it under 8 KB and about NOW. A stale handoff is worse than none,
because it will be believed. -->

**Last updated:** 2026-11-09 (Destroy pushed) · **Branch:** `wip/design-platforms-canvas`,
pushed to origin with the Destroy commit `9cc56e4`. `wip/api-discovery-runs` and `master` stay
at `32de0dd` (not merged forward this time). Rule: commit each piece as it lands, staged by
path; never `git add -A`; push only when the user asks.

## What this session did

Earlier today: R5-1 (cast provenance on the twin-config read, conduits sourced), R5-2 (every
site on the rail, an open site pages by the budget, one count), pushed and fast-forwarded into
`wip/api-discovery-runs` and `master` (`32de0dd`).

Then the gap "the console can deploy a twin but not bring one down", one commit:

- **Vocabulary** in `ui/Spec.md` and `docs/CONVENTIONS.md` §1: Deploy / Destroy / Redeploy ·
  Apply · Persist / Delete (a twin config) / Remove (a design element); never "undeploy".
- **Engine:** `DELETE /api/deploys/{id}` ALREADY cascaded to the exr/tfc/tap children and the
  tap teardown removes its gretaps and mirrors (the sensor binding). New: the response lists
  `tiers` (per tier, children first, te last, `released` = the tap's gretap/mirror actions).
  Test: a te with a standing tfc AND tap → all three destroyed, the attach-gre ops destroyed.
- **UI:** `areas/shell/destroy.tsx` (one confirm, `useDestroy` with a poll that ticks tiers
  off, `DestroyControl`), wired into the Twins row (Deploy ↔ Destroy…, state-aware), the twin
  page (Deploy panel → Destroy…; Delete twin on a deployed twin says "destroy the deploy first"
  with Destroy…), and the canvas bar (Destroy… in the exits, dirty-copy checkbox + Persist
  first, progress `destroying… te · N s`, pill ticks, back to undeployed edit mode via the
  page's data reload, no page reload). Per-tier Stop/Restart left out: `/api/tfc` and
  `/api/taps` have apply and destroy only (noted in `ui/Spec.md`).

## Current state

- **Tests:** ui typecheck clean, 31 files / 484 (new `destroy.test.tsx`). Engine `test_api`,
  `test_o2_cascade`, `test_o2_taps`, `test_traffic_tier` 415 passed (full suite not re-run this
  round; it had 3 failures from the untracked catalog folders, unrelated).
- **Serve:** :8080 restarted for the engine change (pid 1594970, `none` mode, env from the live
  `/proc/<pid>/environ` minus the token, scratchpad `restart.sh serve-r6.env`), on the rebuilt
  `ui/dist`.
- **Live (dev45):** canvas-test deployed from the Twins list (plan `canvas-test-0bf75c29`, 4
  VMs, `canvas-test-9d2ad59b`, completed in ~270 s), destroyed from the canvas bar in ~27 s;
  the canvas came back `twin config · edit · undeployed` with Save / Save as; the Twins row
  reads undeployed with Deploy; `/api/deploys` lists no canvas-test row. Only the te stood (the
  console's Deploy stands no tfc/tap), so the cascade is proven by the engine test, not live.
  switch-test untouched: te/tfc/tap completed, no working copy. Shots `R6-*` in the scratchpad.
- Untracked, the user's: the catalog folders, `ui/dist.old/`; mine: `ui/dist.prev/`.

## Not done / honest gaps

- A live destroy with tfc and tap standing has not been run (the console has no tfc/tap
  deploy action; switch-test has them but is to stay deployed).
- `DELETE /api/deploys/{id}` is synchronous: the progress ticks from a poll of `/api/deploys`,
  so on a te-only destroy it shows `te` until the answer arrives.
- The Deploy panel's own deploy is te only; tfc and tap still follow from the CLI.
- corp-apcn-reader-20261028 still invalid (IPv6 /64, CV-012): the user's call.
- Yours to decide: the serve's auth mode (`none` on 0.0.0.0); whether to fast-forward
  `wip/api-discovery-runs` and `master` to the Destroy commit.

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
