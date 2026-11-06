# Session State — the cross-session handoff

<!-- REPLACED at every handoff, never appended (`/handoff`); checked against the repo at every
start (`/catchup`). Keep it under 8 KB and about NOW. A stale handoff is worse than none,
because it will be believed. -->

**Last updated:** 2026-11-06 13:50 · **Branch:** `wip/design-platforms-canvas`, three
commits past origin's `8c7312d` (f8ef1fd, 917e871, this handoff), **not pushed** (the user pushes).
Rule: commit each track as it lands, staged by path; push only when asked.

## What this session did

The 2026-11-05 canvas review (Claude Doc `PR6PeoXpMPjcfMikVX69qP`) was worked in two rounds,
then the user's two-track brief of 2026-11-06 (rendering rules; the round-2 bugs):

- Round 1, pushed as `65dd86b`..`4c8a401`; round 2 (R2-1..R2-4) pushed as `ac93980` + `8c7312d`:
  the command stack restored by content (`stackFits`), a site's box the union of its
  children's, legs border-to-border (`canvas/legs.ts`), legs without pointer events and the
  pane hit-testing them (`legAt`, not grid-snapped).
- `f8ef1fd` **track 2 record:** `docs/design/canvas-review-2026-11-05.md` (the brief named it;
  it did not exist) -- each R2 item, the fix, R2-5 checked by hand (Cancel mid-replay 409 in
  4 s; the attach select takes a keyboard pick).
- `917e871` **track 1, rendering rules** (`ui/src/features/edit-canvas/SPEC.md` "Shell and
  chrome" > "Rendering rules", with a numbered manual checklist, every line **seen**):
  1. no WAN node: a router with a leg on an `egress: true` segment (or the one router of a
     cast twin with crossing evidence) wears a WAN badge; crossing figure on its hover card
     and in the inspector; the Internet node only on non-local evidence or a configured
     uplink, trunk to each gateway; a twin with neither starts at its router. The topology
     route's network nodes carry `egress` (dtos/service, `api/Spec.md`); plane epoch 3.
  2. legs always; observed-flow arcs off until lit by the overlay or touching the selection
     (`canvas/evidence.ts`, filtered in `Canvas.tsx`).
  3. the site group a borderless tint with a small label and a fold chevron (segments and
     chips `hidden`), not selectable; routers at z 2.
  4. host chips are ring + `×N`; rail **Labels** (N) all / selected / none, default selected,
     in `localStorage` `axr-canvas-labels`; a bare chip is sized to its ring.
  Also fixed on the way: the annotation nodes memo was keyed on the whole edit props, so every
  selection rebuilt the node array and dropped `selected` (a selected chip never drew so).

**Seen live** (:8080, `scratchpad/t1.cjs`, `t1b.cjs`, shots `t1-*.png`): canvas-test starts at
`gw` (y 24), no Internet/WAN, bare chips, tint without border, fold/unfold, labels cycle;
corp-twin `hq-mx` topmost, 18 legs, 0 arcs; corpapcn-twin evidence view: `Internet 596
non-local`, one router with the badge, 0 arcs on open, 1 on a segment click, Esc clears, the
inspector reads `715 crossing edges · 41,065,082 bytes · 3,725 flows`, Light up `tcp` lit 11
arcs. canvas-test's saved pins are from plane 2 and no longer apply (the page says so).

**The serve** was restarted 13:40 (pid 1099306, `none` mode, images root, env in the session
scratchpad's `serve.env`) for the `egress` field; `ui/dist` rebuilt beside and swapped.

## Standing on dev45 — left up for the user

te `canvas-test-f919dae1` + tap `…-1a070fcc` (→ XPress-SW-A) + tfc `…-bb83ca92`; the working
copy is clean (the last live Reset replayed the committed tfc). Teardown: `axrange te destroy
canvas-test-f919dae1 --allow-destroy --db ~/.axrange/state.db`.

## Current state

- **Tests:** engine full suite green (13:30, `-n auto`, exit 0; `test_api.py` asserts the
  `egress` key); ui typecheck clean, **258 tests in 19 files** (`canvas.rendering.test.tsx`,
  `canvas.round2.test.tsx` new this day); `ui/dist` live.
- Untracked: `catalog/twins/{test2,test-twin-2,corp-apcn-reader-20261028,mylab,canvas-test-exp1}`,
  `catalog/twins/canvas-test/generated/` (the reviewer's layout, plane 2).

## Issues left

- Rule 1's config marker is segment-level `egress: true` (the IR's only uplink fact); the
  twin-level `egress:` block (containment phase) is not read, so corp-twin -- whose uplinks
  are sink segments -- draws no Internet. A router-level `uplink:` key would be new IR scope:
  the user's call.
- A leg is picked only over the pane or a site's tint; one wholly inside other boxes is
  selectable from the node panel only. Folded sites are per page, not saved.
- From the review, not done: the canvas cannot author `install:` (the reviewer's file server
  ran no smbd); a cancel reaches only the process running the playout, between ops.
- **Open, small (older):** a tile says "1 host" for a `count: 2` group; compact tiles keep
  full height; `POST /api/validate` 400 for a leg-less router mid-edit; the bar wraps below
  ~1500 px.
- **Yours to decide:** the serve's auth mode (`none` on 0.0.0.0); A8 provenance; the Checks
  tab; the untracked catalog folders; merge to `master`; a server-side undo stack.

## Next steps

0. Push the three commits. Re-cast corp-apcn-reader-20261028 (IPv6 segment).
1. `install:` / `requires` / `resources` on a palette group and in the node panel.
2. The small items above, then the libvirt path and live Splunk/Elastic.

## Gotchas

- `rf.screenToFlowPosition` SNAPS to the grid when snapToGrid is on: a hit-test passes
  `{ snapToGrid: false }`. A router node spans the segments it joins: clip against its icon.
- A memo keyed on the `edit` props runs on every selection; key on the field you read.
- Any node a React Flow selection lifts (`elevateNodesOnSelect`) covers what it overlaps:
  the site tint is `selectable: false` for that reason.
- `ugrep` treats `model/graph.ts` as binary (a non-UTF-8 glyph): grep it with `-a`.
- The cast view vs the design view: with a working document the page opens the DESIGN view
  (the text); a cast twin's evidence (Internet, arcs) is the *evidence* view button. Tests
  that want the cast view seed no document for that twin and a tree with its routers.
- Pane click in jsdom: pointer-down + pointer-up (`clickPane()`); the hover card has a
  350 ms dwell; the fake API's timestamps come from `stamp()`.
- Write policy and `ConfigExits` resolve ONCE at `create_app`. Engine tests `-n auto -p
  no:cacheprovider`; `ui/`: `npm ci` only; never build into a served `ui/dist`; stage by
  path, never `git add -A`.

## Environment

One venv at `.venv/`, node 22 for `ui/`, live state in `~/.axrange/`. Playwright 1.61 at
`/home/rocky/fx-cap/mgmtui/node_modules/playwright` + `~/.cache/ms-playwright/chromium-1228`.
dev45 API 10.80.76.108:8006, SSH root (default key); XPress-SW-A `fxadmin@10.80.72.43`.
