# Session State — the cross-session handoff

<!-- REPLACED at every handoff, never appended (`/handoff`); checked against the repo at every
start (`/catchup`). Keep it under 8 KB and about NOW. A stale handoff is worse than none,
because it will be believed. -->

**Last updated:** 2026-11-08 · **Branch:** `wip/design-platforms-canvas`. Pushed to origin up to
`c0d38a8`; this session's commits after it are **local, not pushed** (push only when the user
asks). Rule: commit each piece as it lands, staged by path; never `git add -A`.

## What this session did: the canvas follow-ups after review round 3

Round 3 itself (`205140c` A, `d279d3e` B, `4b8ce61` C) is done and in the review doc
(https://claude.ai/artifact/PR6PeoXpMPjcfMikVX69qP). Then, asked to finish every open canvas item:

- **`2fe0b04`** the tree reads the graph directly: shown segments `graph.segments`, unplaced
  `graph.unplacedLocal`, role bands `canvas/bands.ts`, east-west pairs `canvas/pairs.ts` (merged
  on the boxes drawn, so an arc's key now matches the `arcKey(anchor, anchor)` the selection and
  overlay compute; folded pairs used to miss). `layout/` level0, level1, arcs, columns, infra,
  sites, tiers, trunk, scale → `archive/ui-level0-engine/` (README). `layout/` keeps geometry,
  pins, matrix (the list view).
- **`28ecfbf`** no orphan segments: bridging hosts (a host with legs on two segments, corp-twin's
  `role: l2-switch` hosts) rank what they reach under their pill, to a fixpoint; a dashed
  `via <host>` edge when the host is folded away; what nothing reaches sits under a dashed
  *Not carried* box (`chrome:uncarried`). corp-twin: 22 such segments, all now reached.
- **`9c853f1`** members (§5): multi-select rows + bulk role / persona (one undo step); *Split and
  assert* on a cast twin (override `field: group` per member; instance i = i-th of the roster's
  sorted keys; refused when the roster size is not the count); chips open past zoom 1.6. The
  promote now re-opens a deployed working copy with the files after the edit (it read them
  stale).
- **`de8df00`** duplicate (§6): Ctrl+D / the menu carry a floating copy; a press drops it on a
  device (trunk) or the canvas (parked, root pinned there), under another site's pill it
  re-allocates in that prefix; Enter parks in place, Esc cancels; a marquee of a switch and
  devices below it is that switch's subtree.
- SPEC: rules 11 / 12 and a "Canvas follow-ups (2026-11-08)" checklist, each line with its proof.

## Current state

- **Tests:** ui typecheck clean, 26 files / 409. Engine and discovery untouched this session
  (last: engine 4245 passed + 3 environmental failures, run from `engine/`; discovery 2472).
- **Serve:** :8080 unchanged (pid 1419935); it serves the rebuilt `ui/dist` (no restart needed:
  no engine change).
- **Eye checks** (headless, scratchpad): corp-twin `D2-*` (every item on a line), corp-tree
  `E1-*` (carry, device target, parked drop; nothing saved, only a validate sent), the six round-3
  twins `G-*` (no page errors). dev45 untouched.
- Untracked, the user's: the catalog folders, `ui/dist.old/`; mine: `ui/dist.prev/` (the
  previous build, safe to delete).

## Not done / honest gaps

- R3-6's 30 s freeze still does not reproduce (fold opens 70–165 ms up to 175 nodes on
  corp-twin; numbers in the SPEC).
- A dropped copy's pills follow the tree's placement, not its pinned root (they can land far
  from it).
- *Split and assert* maps instances to the roster's sorted keys by position: nothing in the cast
  records which instance is which entity, so that is a convention, stated in the code.
- corp-apcn-reader-20261028 still reads invalid (its IPv6 /64, CV-012); re-cast or remove the
  segment, or decide whether the engine should refuse non-IPv4 too (a contract call).
- Console, not canvas: the imprint JSON editor drops `confirm` / `doubt` on save; Twins list
  "Deploy" links to the twin page (no console deploy action).
- Yours to decide: push these commits; the serve's auth mode (`none` on 0.0.0.0); merge.

## Gotchas

- `cd` does not persist between Bash calls, and a failed `cd` leaves you in the checkout: never
  chain a destructive git command after a `cd` without `&&`.
- A subagent worktree is created from an old base here (39fa362): make one by hand
  (`git worktree add --detach <path> HEAD`) and point the agent at it.
- Positions settle per session in the design view; a fold or Fan-out change drops them so the
  tree re-ranks; a document edit keeps them.
- React Flow drops an edge whose `sourceHandle` names a handle of the wrong type; its
  `onSelectionChange` echoes a programmatic selection (the marquee-as-subtree runs on
  `onSelectionEnd` for that reason). `rf.screenToFlowPosition` snaps unless
  `{ snapToGrid: false }`. `ugrep` reads `model/graph.ts` as binary: `grep -a`.
- Playwright: off-viewport tree nodes refuse `.click()`; use `el.evaluate((e) => e.click())`.
- `ui/`: `npm ci` only; never build into the served `ui/dist` (build `dist.new`, swap). The
  contract files change only with the user's confirmation and a version bump.

## Environment

One venv at `.venv/`, node 22 for `ui/`, live state in `~/.axrange/`. Playwright 1.61 at
`/home/rocky/fx-cap/mgmtui/node_modules/playwright` + `~/.cache/ms-playwright/chromium-1228`
(scripts in this session's scratchpad: `shot.cjs`, `perf.cjs`, `carry.cjs`, `folds.cjs`). dev45 API
10.80.76.108:8006, SSH root; XPress-SW-A `fxadmin@10.80.72.43`.
