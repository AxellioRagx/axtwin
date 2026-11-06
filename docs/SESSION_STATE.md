# Session State — the cross-session handoff

<!-- REPLACED at every handoff, never appended (`/handoff`); checked against the repo at every
start (`/catchup`). Keep it under 8 KB and about NOW. A stale handoff is worse than none,
because it will be believed. -->

**Last updated:** 2026-11-06 15:45 · **Branch:** `wip/design-platforms-canvas`, one commit
past origin's `7522956` (track 3), **not pushed** (the user pushes).
Rule: commit each track as it lands, staged by path; push only when asked.

## What this session did

The 2026-11-05 canvas review (Claude Doc `PR6PeoXpMPjcfMikVX69qP`), two rounds, then the
user's briefs of 2026-11-06: tracks 1+2 (pushed, `f8ef1fd`..`7522956`) and **track 3,
edge routing on large twins** (this commit; `ui/src/features/edit-canvas/SPEC.md`
"Rendering rules" rules 5-9 with a numbered manual checklist, every line **seen**):

- **Premise corrected:** corp-twin is authored (no cast); `ms120` has ten `nets[]`, so every
  leg it fanned was a config attachment, and no code path ever drew inferred adjacency as a
  leg. The "adjacency observed, not attached" device, dashed legs and the inspector's
  *attach to these segments* are NOT built -- no observed adjacency reaches the canvas.
- 5: `kind: host` legs are `net`/`nets[]` only; a host of a `DEVICE_ROLES` role
  (`model/roles.ts`: l2-switch, l3-switch, router, firewall; the API enumerates no roles)
  on >1 segment is drawn router-style (`RouterLegs.kind: "host"`, `RouterData.device`,
  `authored.ts` `devices`), its legs `from: "host"`, no `hasRouterLeg`. A router's node
  is now its drawn box (`DEVICE_W`, **plane epoch 4**), not a span over its segments; the
  band packs rows by it, slides a router along its span to share a row, keeps feet clear.
- 6: orthogonal legs: router icon bottom → the tile's per-router top handle, A* over a
  channel grid a margin outside every box (`canvas/route.ts`), wired over the live boxes
  (`canvas/wiring.ts`, deferred memo, `RoutesContext`); `legs.ts` polyline helpers; the pane
  hit-tests `data-pts`.
- 7: fan collapse per site past N (`canvas/fan.ts`; rail **Fan-out** 2/4/8/all, default 4,
  `axr-canvas-fan`); the overflow `legs:` edge goes through the fan; selected router or a
  click on the tint expands; N=all draws everything.
- 8: one `×N` badge style, inside the chip (rungs too). 9: labels icon per mode.

**Seen live** (:8080, `scratchpad/t3.cjs`, shots `t3-*.png`): corp-twin 54 edges routed, 0
tile crossings in every state, 3 fans, 12 devices (7 on row 0, 7 rows); hq-mx selected → 51 legs;
canvas-test unchanged (gw y 24, 2 legs); pit-395a61f04e-1780405200 one fan of 40.

**The serve** was restarted 15:40 (pid 1115719, `none` mode, env in the scratchpad's
`serve.env`; no engine change this track) after `ui/dist` was rebuilt beside and swapped.

## Standing on dev45 — left up for the user

te `canvas-test-f919dae1` + tap `…-1a070fcc` (→ XPress-SW-A) + tfc `…-bb83ca92`; the working
copy is clean. Teardown: `axrange te destroy canvas-test-f919dae1 --allow-destroy --db
~/.axrange/state.db`.

## Current state

- **Tests:** engine full suite green (14:43, `-n auto`, exit 0); ui typecheck clean,
  **277 tests in 20 files** (`canvas.routing.test.tsx` new: the leg geometry is checked
  over the computed layout); `ui/dist` live.
- Untracked: `catalog/twins/{test2,test-twin-2,corp-apcn-reader-20261028,mylab,canvas-test-exp1}`,
  `catalog/twins/canvas-test/generated/` (the reviewer's layout, plane 2).

## Issues left

- Devices whose spans centre on the same x slide along the span or take rows below;
  corp-twin's band is seven rows. A long router name ellipsizes at its box (the title and
  card carry it whole).
- Rule 1's config marker is segment-level `egress: true`; corp-twin (sink-segment uplinks)
  draws no Internet. A router-level `uplink:` key would be new IR scope: the user's call.
- A leg is picked only over the pane or a site's tint. Folded sites and opened fans are per
  page, not saved. The canvas cannot author `install:`.
- **Open, small (older):** a tile says "1 host" for a `count: 2` group; compact tiles keep
  full height; `POST /api/validate` 400 for a leg-less router mid-edit; the bar wraps below
  ~1500 px.
- **Yours to decide:** the serve's auth mode (`none` on 0.0.0.0); A8 provenance; the Checks
  tab; the untracked catalog folders; merge to `master`; a server-side undo stack; an
  observed-adjacency source for the "not attached" device rendering.

## Next steps

0. Push the commit. Re-cast corp-apcn-reader-20261028 (IPv6 segment).
1. `install:` / `requires` / `resources` on a palette group and in the node panel.
2. The small items above, then the libvirt path and live Splunk/Elastic.

## Gotchas

- A flex item with `overflow: hidden` may SHRINK and clip its own text (`min-height` 0):
  give it `flex: none`. React Flow sets a node's `pointer-events` inline (no CSS override
  without `!important`); the router node is sized to what it draws instead.
- A router's `from'` port (icon bottom + margin) must not land inside another box: the band
  keeps feet clear (`placement.ts` `under`), else that leg falls back to a straight line.
- `rf.screenToFlowPosition` SNAPS to the grid when snapToGrid is on: a hit-test passes
  `{ snapToGrid: false }`.
- A memo keyed on the `edit` props runs on every selection; key on the field you read.
- Any node a React Flow selection lifts covers what it overlaps: the site tint is
  `selectable: false`.
- `ugrep` treats `model/graph.ts` as binary: grep it with `-a`.
- With a working document the page opens the DESIGN view; a cast twin's evidence is the
  *evidence* view button. Tests wanting the cast view seed no document and a tree.
- Pane click in jsdom: pointer-down + pointer-up; jsdom draws no edge (no measurement):
  test leg geometry on the placement + `wire`, not the DOM.
- Write policy and `ConfigExits` resolve ONCE at `create_app`. Engine tests `-n auto -p
  no:cacheprovider`; `ui/`: `npm ci` only; never build into a served `ui/dist`; stage by
  path, never `git add -A`.

## Environment

One venv at `.venv/`, node 22 for `ui/`, live state in `~/.axrange/`. Playwright 1.61 at
`/home/rocky/fx-cap/mgmtui/node_modules/playwright` + `~/.cache/ms-playwright/chromium-1228`.
dev45 API 10.80.76.108:8006, SSH root (default key); XPress-SW-A `fxadmin@10.80.72.43`.
