# Session State — the cross-session handoff

<!-- REPLACED at every handoff, never appended (`/handoff`); checked against the repo at every
start (`/catchup`). Keep it under 8 KB and about NOW. A stale handoff is worse than none,
because it will be believed. -->

**Last updated:** 2026-11-07 · **Branch:** `wip/design-platforms-canvas`, **pushed** to origin
on the user's request (in sync with `origin/wip/design-platforms-canvas`). This session:
`205140c` track A, `d279d3e` track B, `4b8ce61` track C, then this handoff. Before it: the
topology-layers build (`42c00de` … `1551658`). Rule: commit each track as it lands, staged by
path; never `git add -A`; push only when the user asks.

## What this session did: review round 3

Input: the review doc's "Round 3" table (R3-1..R3-10) and "Console-wide" list
(https://claude.ai/artifact/PR6PeoXpMPjcfMikVX69qP; the local export
`docs/design/canvas-review-2026-11-05.md` predates Round 3) and the brief's "Canvas rendering"
(https://claude.ai/artifact/WTJm68Y7EXndjqWW1VfiiX). A "Round 3 — fixed" row now sits under every
R3 row in the review doc, plus a line under Console-wide. Every item's proof is in
`ui/src/features/edit-canvas/SPEC.md` → "Review round 3 checklist".

- **A (205140c)** provenance and validation: the cast's tap bindings carry `source: discovered`
  + confidence + vantage (segments and groups already did), asserted taps `asserted`, the gw
  placeholder `unconfirmed` + vantage; the canvas reads a missing `source` as discovered on a
  cast twin, authored on an authored one, and shows the overlay toggle only on cast twins
  (corp-apcn-reader-20261028: 135 discovered / 0 authored). Checks counts canvas errors apart
  from the API line; pass-with-warnings reads valid. A `switch` node type. Banners clear on the
  next change; the unplaced count moved to Notes.
- **B (d279d3e)** the device tree: ranks edge router → routers → switches → pills → hosts; a
  router draws no leg to a pill its branch reaches by a trunk; router-only segments
  (`fab-link`) are router-to-router links; orthogonal edges on dagre's ranks
  (`canvas/orthogonal.ts`) with a geometry test over five catalog twins; neutral hue on one
  router; per-site folds, `+N more`, an 8-icon host budget per pill, all folded by default and
  opened in place; ×N double-click anywhere opens member chips; Re-layout; the palette loads
  after the drawing (first paint ~7 s → 2–3 s). Archived `canvas/wiring.ts`, `canvas/route.ts`,
  `layout/plane.ts`, `layout/viewport.ts` to `archive/ui-bus-plane/` (README there).
- **C (4b8ce61)** a switch no router reaches (`edit/subtree.ts` `parkedDevices`) holds the
  document on the page: no validate, no sync, every exit blocked with the reason, one undo step,
  taps never copy; a parked copy keeps its original's uplink segments so the attach tool trunks
  them; new boxes avoid settled ones. Console pages (built by a subagent in a worktree, reviewed
  and merged): Twins list columns + `modified_at` on `GET /api/twins` (additive engine field),
  Imprint page assertions list / Advanced JSON / primary Cast, run form Advanced with
  probe-derived defaults, `EntityName` / `When` / `LinkRow` in `areas/shell/parts.tsx`.

## Current state

- **Tests:** ui typecheck clean, 25 files / 395; engine (run from `engine/`, its tests read
  `tests/fixtures` relative to it) 4245 passed, 3 environmental failures (the untracked catalog
  folders, as before); discovery 2472.
- **Serve:** :8080 restarted once this session (pid 1419935, same env from the old pid's
  `/proc/environ`, saved as `serve.env.live` in this session's scratchpad) for `modified_at`;
  it serves the final `ui/dist`.
- **dev45:** untouched. On switch-test only the working copy was read; the eye check's
  Duplicate was held locally (no write) and undone.
- Untracked, the user's: the catalog folders, `ui/dist.old/`.

## Not done / honest gaps

- `layout/level0.ts` / `level1.ts` (and their site/column/tier/arc modules) still decide WHAT the
  tree draws (shown segments, role-band collapse, top-K arcs, unplaced); their geometry is
  unused. Replacing them with a direct graph read is what's left before the whole lifted
  o2/console engine can be archived.
- R3-3 on corp-apcn-reader-20261028 still reads invalid: its IPv6 /64 is a real canvas error
  (CV-012); the engine's validator accepts IPv6 (`validator/catalog.py:394`). Re-cast or
  remove the segment. Making the engine refuse non-IPv4 too is a contract call.
- R3-6's 30 s freeze did not reproduce on this build; measured numbers are in the SPEC.
- Segments no router or switch carries (corp-twin's `vlan192`, two site folds) sit one rank
  down with no edge: honest, but they read as orphans.
- Twins list "Deploy" links to the twin page's deploy section (the console has no deploy
  action); the imprint JSON editor still drops `confirm` / `doubt` on save (older bug).
- From the SPEC's earlier list, still not built: multi-select member rows, the split as an
  assertion, a zoom threshold for chips, the floating copy under the cursor, drop on empty
  canvas, site re-allocation under another site, the marquee as a subtree.
- Yours to decide: the serve's auth mode (`none` on 0.0.0.0); merge to `master`.

## Gotchas

- `cd` does not persist between Bash calls, and a failed `cd` leaves you in the checkout:
  never chain a destructive git command after a `cd` without `&&` (this session a
  `git reset --hard` meant for a removed worktree ran in the checkout; HEAD was unchanged and only
  my own uncommitted edits were lost and redone).
- A subagent worktree is created from an old base here (39fa362): make one by hand
  (`git worktree add --detach <path> HEAD`) and point the agent at it.
- Positions settle per session in the design view; a fold or Fan-out change drops them so the
  tree re-ranks; a document edit keeps them.
- React Flow drops an edge whose `sourceHandle` names a handle of the wrong type; its
  `onSelectionChange` echoes a programmatic selection. `rf.screenToFlowPosition` snaps unless
  `{ snapToGrid: false }`. `ugrep` reads `model/graph.ts` as binary: `grep -a`.
- `ui/`: `npm ci` only; never build into the served `ui/dist` (build `dist.new`, swap). The
  contract files change only with the user's confirmation and a version bump.

## Environment

One venv at `.venv/`, node 22 for `ui/`, live state in `~/.axrange/`. Playwright 1.61 at
`/home/rocky/fx-cap/mgmtui/node_modules/playwright` + `~/.cache/ms-playwright/chromium-1228`
(shots: `shot.cjs`, timings: `perf.cjs` in this session's scratchpad). dev45 API
10.80.76.108:8006, SSH root; XPress-SW-A `fxadmin@10.80.72.43`.
