# Session State — the cross-session handoff

<!-- REPLACED at every handoff, never appended (`/handoff`); checked against the repo at every
start (`/catchup`). Keep it under 8 KB and about NOW. A stale handoff is worse than none,
because it will be believed. -->

**Last updated:** 2026-11-06 21:20 · **Branch:** `wip/design-platforms-canvas`, one commit
past origin's `f24648d` (track 4), **not pushed** (the user pushes).
Rule: commit each track as it lands, staged by path; push only when asked.

## What this session did

The user's briefs of 2026-11-06: tracks 1+2 (`917e871`), track 3 (`f24648d`, pushed), and
**track 4, segments as buses** (this commit; `ui/src/features/edit-canvas/SPEC.md`
"Rendering rules" rewritten, rules 1-6 with a numbered manual checklist, every line **seen**):

- **Premise corrected:** corp-twin's twelve switch-class nodes are not promoted from cast
  adjacency (no cast); they carry `nets[]` with `l2: true` entries in the config, so they
  are multi-homed hosts (rule 4). corp-enterprise has ONE router; the multi-router eyes
  check is `datacenter` (gw, fab-r1, fab-r2). Track 3's device rule is reverted
  (`authored.ts`, `graph.ts` `RouterLegs` has no `kind`, `roles.ts` `SWITCH_ROLES`).
- 1: a segment is a BUS lane (`canvas/bus.ts` constants, `placement.ts`, `SegmentNode`):
  label left, count badge right, hue dots at the left end, hosts as 30 px icons on the line
  wrapping past 10, the port region at the right end where drops land; sites are vertical
  stacks in rows near a window's shape (`chooseSitesPerRow`). `+N more` is a lane.
- 2: routers in a row above (a site is at least as wide as its routers' row), one hue each
  (`canvas/hues.ts`, 8 then grey) on ring, legs and bus dots; a leg is a drop down a port
  column (`wiring.ts`: one column per source per site; `route.ts` gained `arrive: "up"`),
  a port dot where it meets the line; the fan (trunk + rail) kept, in the hue.
- 3: a switch-class host is an icon on its bus with the switch glyph and an `L2`/`L3`/`FW`
  badge; `canvas/evidence.ts` `observedSegments` (cast roster members + export edges) feeds
  the inspector's *observed on N segments … Attach…* row, which writes `nets[]`.
- 4: a multi-homed host sits on the LOWEST of its buses in its first segment's site, with a
  `stub:` edge (dashed, hollow dot) to each other bus; `ms120` has nine.
- 5: the site tint as before. 6: drop/click on a lane attaches (`segmentAt`); a router's
  drop dragged onto another bus re-homes it (`Canvas.tsx` `legDrag`, pointer capture on
  the shell, rubber line; `document.ts` `rehome`); "Add a segment [in this site]" uses the
  bus asked beside / the selection; a bus dragged is RE-RANKED, its site's lanes pinned as
  `segment:<cidr>` = `{ x: 0, y: rank }` in one layout change (`reorderPins`,
  `useLayoutDraft.onPinMany`, `placement.ts` `orderByRank`; the page settles ranks).
  **Plane epoch 5.** Fits cap at zoom 1.5.

**Seen live** (:8080, `scratchpad/t4.cjs`, shots `t4-*.png`, the probe checking every
leg's polyline against every host icon, bus label and badge): corp-twin 5 sites / 26 lanes,
ONE router, 70 icons, 8 `L2` switches, 35 edges (3 fans, 17 rail drops, 1 leg, 14 stubs),
0 crossings in the collapsed, selected (18 legs + 14 stubs) and Fan-out: all states;
corp-enterprise 1 router, 4 drops; datacenter 3 hues side by side, `fab-link` with two dots,
re-home by drag (fab-r1: fab-link → edge, dirty, Ctrl+Z back), reorder by drag (edge →
rank 1, one layout change); canvas-test gw above two buses, two drops; the pit twin one
fan of 40, 45 icons.

**The serve** was restarted 21:05 (pid 1125381, `none` mode, env in the scratchpad's
`serve.env`; no engine change this track) after `ui/dist` was rebuilt beside and swapped.

## Standing on dev45 — left up for the user

te `canvas-test-f919dae1` + tap `…-1a070fcc` (→ XPress-SW-A) + tfc `…-bb83ca92`; the working
copy is clean. Teardown: `axrange te destroy canvas-test-f919dae1 --allow-destroy --db
~/.axrange/state.db`.

## Current state

- **Tests:** ui typecheck clean, **283 tests in 20 files** (`canvas.bus.test.tsx` replaces
  `canvas.routing.test.tsx`; the geometry is checked over the computed layout); engine
  untouched since its last green run (14:43); `ui/dist` live.
- Untracked: `catalog/twins/{test2,test-twin-2,corp-apcn-reader-20261028,mylab,canvas-test-exp1}`,
  `catalog/twins/canvas-test/generated/` (the reviewer's layout, plane 2).

## Issues left

- Two corp-twin segments share ONE CIDR (`mx-vlan20` / `vlan20`, 10.180.16.0/20; also
  vlan1, vlan64 pairs): the lifted graph keys hosts by CIDR, so both buses show the count
  and one shows the icons (the tiles did the same). A fan trunk's badge sits at the
  polyline's midpoint, on a long trunk over another site.
- A leg over a lane's port region is picked before the lane (hit-test within 8 screen px):
  a press there starts a re-home drag, not a lane drag; press the label to reorder.
- Rule 1's config marker is segment-level `egress: true`; corp-twin draws no Internet. A
  router-level `uplink:` key would be new IR scope: the user's call.
- Folded sites and opened fans are per page, not saved. The canvas cannot author `install:`.
- **Open, small (older):** a tile says "1 host" for a `count: 2` group; `POST /api/validate`
  400 for a leg-less router mid-edit; the bar wraps below ~1500 px.
- **Yours to decide:** the serve's auth mode (`none` on 0.0.0.0); A8 provenance; the Checks
  tab; the untracked catalog folders; merge to `master`; a server-side undo stack.

## Next steps

0. Push the commit. Re-cast corp-apcn-reader-20261028 (IPv6 segment).
1. `install:` / `requires` / `resources` on a palette group and in the node panel.
2. The small items above, then the libvirt path and live Splunk/Elastic.

## Gotchas

- React Flow drops an edge whose `sourceHandle` names a handle of the wrong type (a stub
  from a host's `top` TARGET handle never rendered; jsdom draws no edge, so only the live
  probe saw it). A handle that fills its node swallows the node's drag (`nodrag`): the bus
  handle grows to the lane only while the shell is `connecting` (`onConnectStart/End`).
- A `pointerdown` whose default is prevented suppresses the compat `mousedown`, which is
  what d3-drag listens to: the shell's capture handler prevents only over a leg.
- With every lane's rank settled, pinning ONE lane's rank clashes: a reorder re-ranks the
  whole site (`reorderPins`).
- A flex item with `overflow: hidden` may SHRINK and clip its own text: give it `flex: none`.
- `rf.screenToFlowPosition` SNAPS to the grid when snapToGrid is on: a hit-test passes
  `{ snapToGrid: false }`.
- A memo keyed on the `edit` props runs on every selection; key on the field you read.
- Any node a React Flow selection lifts covers what it overlaps: the site tint is
  `selectable: false`.
- `ugrep` treats `model/graph.ts` as binary: grep it with `-a`.
- With a working document the page opens the DESIGN view; a cast twin's evidence is the
  *evidence* view button. Tests wanting the cast view seed no document and a tree.
- Pane click in jsdom: pointer-down + pointer-up; test leg geometry on the placement +
  `wire`, not the DOM.
- Write policy and `ConfigExits` resolve ONCE at `create_app`. Engine tests `-n auto -p
  no:cacheprovider`; `ui/`: `npm ci` only; never build into a served `ui/dist`; stage by
  path, never `git add -A`.

## Environment

One venv at `.venv/`, node 22 for `ui/`, live state in `~/.axrange/`. Playwright 1.61 at
`/home/rocky/fx-cap/mgmtui/node_modules/playwright` + `~/.cache/ms-playwright/chromium-1228`.
dev45 API 10.80.76.108:8006, SSH root (default key); XPress-SW-A `fxadmin@10.80.72.43`.
