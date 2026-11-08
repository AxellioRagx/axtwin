# Session State — the cross-session handoff

<!-- REPLACED at every handoff, never appended (`/handoff`); checked against the repo at every
start (`/catchup`). Keep it under 8 KB and about NOW. A stale handoff is worse than none,
because it will be believed. -->

**Last updated:** 2026-11-08 (evening) · **Branch:** `wip/design-platforms-canvas`. Origin is
at `ffd18c7`; the eight commits below are LOCAL, not pushed (push only when the user asks).
Rule: commit each piece as it lands, staged by path; never `git add -A`.

## What this session did

The user's re-test of 06:50 left seven items (R4); one commit each (4 + 5 as one):

- **`51d9bc3`** R4-3 duplicate: a subtree is the device, deeper devices over `up` links, the
  segments they carry, the hosts ON them, links among that set. switch-test's access-sw no
  longer drags file-server (behind the down P24) into its copy.
- **`28b9c40`** the canvas stamps `source: authored` on everything it creates or changes
  (`edit/provenance.ts`, one diff pass in `useEditor.apply`; asserted kept; discard not
  stamped; promote adopts the re-cast baseline's marks).
- **`a54aa02`** R4-4: the cast ALREADY wrote source/confidence/vantage; the tree route had no
  fields and the topology route dropped a segment's. Both now carry them (engine DTOs,
  `twin_tree.py`, `service.topology`, `api/Spec.md`, UI types).
- **`d7f9f92`** R4-2b + R4-8: a segment only a bridging host reaches joins the router's rail
  with its site (dashed `via <host>`); rails are one row, site then CIDR, `+N more` last; an
  open site is a tile with its pills in a row below; fold open/close refits the view.
- **`b0a6dc9`** R4-10: Esc folds member chips, chips sit below the label, override dot.
- **`eb281a8`** R4-7: one timestamp style, `ui/src/model/time.ts` (local + zone, UTC tooltip).
- **`4c41738`** SPEC checklist "Review round 4" with what the eye saw; plus this handoff and a
  topology-layers §6 sentence (the R4-3 rule) in the last commit.

## Current state

- **Tests:** ui typecheck clean, 29 files / 441. Engine: `test_twin_tree`, `test_api`,
  `test_topology_layers`, `test_api_console` 680 passed, 2 skipped; discovery
  `test_synth_topology_layers` 13 passed. Full engine suite not re-run.
- **Serve:** :8080 restarted 2026-11-08 evening (pid 1524004, `none` mode, same env, from the
  saved `/proc/<pid>/environ` minus the token) on the rebuilt `ui/dist`.
- **Eye checks** (headless, scratchpad `R4-*`): switch-test duplicate → drop on gw (exactly
  three copies + one authored trunk) then **Reset** (working copy equals baseline);
  corp-twin collapsed, `+41 more` open, 10.180 open and closed; corp-apcn-reader; a fresh
  cast `r4-scratch` (provenance in the tree and the overlay), **deleted**. dev45 untouched
  beyond switch-test's working copy.
- Untracked, the user's: the catalog folders, `ui/dist.old/`; mine: `ui/dist.prev/` (the
  previous build, safe to delete).

## Not done / honest gaps

- An open 24-segment site (corp-twin 10.180) is one ~3,400 px row as specified; its fit lands
  near 17 % zoom. Wrapping the row is a design call, not made.
- The Deploy panel has still never run a real deploy (plan only). *Split and assert* is
  proven in jsdom only (no live cast has member keys).
- corp-apcn-reader-20261028 still invalid (IPv6 /64, CV-012): re-cast, remove, or rule on
  non-IPv4 in the engine — the user's call. Fresh casts drop the segment.
- Yours to decide: the serve's auth mode (`none` on 0.0.0.0); push; merge.

## Gotchas

- `cd` does not persist between Bash calls, and a failed `cd` leaves you in the checkout: never
  chain a destructive git command after a `cd` without `&&`.
- A subagent worktree is created from an old base here (39fa362): make one by hand.
- corp-twin has segments that SHARE a CIDR (10.180.34.0/25 ×4, 10.180.93.0/24 ×2): never map
  a host to its pill by CIDR; `ExtraLeg.home` carries the segment name.
- A UI-only change goes live by swapping `ui/dist` (the serve reads it per request); an engine
  change needs the restart.
- React Flow drops an edge whose `sourceHandle` names a handle of the wrong type; its
  `onSelectionChange` echoes a programmatic selection. `rf.screenToFlowPosition` snaps unless
  `{ snapToGrid: false }`. `ugrep` reads `model/graph.ts` as binary: `grep -a`.
- Importing a helper from another `*.test.tsx` re-runs that file's tests: define it locally.
- Playwright: off-viewport tree nodes refuse `.click()`; use `el.evaluate((e) => e.click())`.
  A canvas button that disables itself after acting can time out the click AFTER it acted:
  check the serve log before retrying.
- `ui/`: `npm ci` only; never build into the served `ui/dist` (build `dist.new`, swap). The
  contract files change only with the user's confirmation and a version bump.

## Environment

One venv at `.venv/`, node 22 for `ui/`, live state in `~/.axrange/`. Playwright 1.61 at
`/home/rocky/fx-cap/mgmtui/node_modules/playwright` + `~/.cache/ms-playwright/chromium-1228`
(scripts in this session's scratchpad: `shot.cjs`, `dup.cjs`, `reset.cjs`, `members.cjs`,
`dates.cjs`, `stress.cjs`). dev45 API 10.80.76.108:8006, SSH root.
