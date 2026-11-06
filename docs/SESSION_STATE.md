# Session State — the cross-session handoff

<!-- REPLACED at every handoff, never appended (`/handoff`); checked against the repo at every
start (`/catchup`). Keep it under 8 KB and about NOW. A stale handoff is worse than none,
because it will be believed. -->

**Last updated:** 2026-11-06 12:50 · **Branch:** `wip/design-platforms-canvas`, two
commits past origin's `4c8a401` (ac93980 + this handoff), **not pushed** (the user pushes).
Rule: commit each track as it lands, staged by path; push only when asked.

## What this session did

The canvas review a Claude-desktop browser session wrote on 2026-11-05 (Claude Doc
`PR6PeoXpMPjcfMikVX69qP`: an edit → Save-As → Apply round trip on the deployed `canvas-test`,
ten bugs by severity, six UX confusions) was worked in full. Decisions the user made first:
Reset discards first and replays best-effort; the undo stack lives in the browser per working
copy; placement is arm-then-click only; the bar regroup, a cancel for long exits, per-change
discard and restoring canvas-test's committed tfc were all in scope.

1. `65dd86b` **api (A2):** Reset deletes the working copy BEFORE replaying the baseline's
   tiers; a failed replay is `replay_failed` + a warning, not a 500. `{tiers: [...]}` replays
   named tiers. `GET .../config` carries `exit` while an Apply/Reset runs; `POST
   .../config/cancel` sets a flag polled between ops (`409 cancelled`); a second exit is
   `409 busy`. `edits.ConfigExits` on `app.state`, in-process. Spec: `api/Spec.md` A2.
2. `710f68c` **ui, the deployed twin's exits:** per-tier pill, red when failed, "Re-apply
   <tier> from baseline"; *applied · unpersisted* vs *unapplied changes*; progress with
   Cancel; 409s shown at once; Discard per pending change in Checks; the layout group floats
   at the canvas's bottom (`layoutBar`); exits grouped; notes fade after 8 s.
3. `8ae7dde` one placement rule: click arms, click a segment places, Esc disarms; other
   twins' groups under a folded "From other twins".
4. `e8b7791` positions settle for the session (`settled` ref), a box never lands under a
   pinned one (`freeSlot`), the view fits once, legs at `EDGE_Z` 3. Fixtures in
   `ui/test/helpers/canvasFixtures.tsx`.
5. `3a7d104` rename re-keys the selection; Save-As carries the layout draft; the next /24
   after the highest; hover card on a 350 ms dwell; no evidence row on authored chips; the
   twin page shows the standing deploy per tier.
6. `1ed813c` the pill's tier words compact (`te ✓ · tfc ✗ failed`) so the bar is one row.
7. `ac93980` **round 2** (the reviewer re-tested the pushed build the same day, "Round 2" in
   the same doc; everything else verified fixed): the command stack is stored per document
   (`axr-canvas-undo:<deploy id>` or `twin:<id>`) and restored when it still FITS the text
   loaded (`stackFits`), undeployed twins included -- the `updated_at` key could not be made
   to fail here (two live repros held), so the content check replaces it; a site's box is
   the union of its children's boxes (a segment dragged out no longer leaves a hole); a leg
   is a straight line border-to-border towards its segment's centre (`canvas/legs.ts`, a
   router's box is its icon), overlapping boxes centre-to-centre; legs take NO pointer events
   and the pane hit-tests them (`Canvas.tsx` `legAt`, only over the pane or a site: a chip,
   segment or router wins) for hover label, click and menu. Spec: `SPEC.md` Positions.

**Round 1 seen live** (:8080, Playwright, `scratchpad/review.cjs`): bar one row at 46 px; a
tab click opens the collapsed inspector and "add a leg on…" takes a keyboard pick (neither
review item reproduced); a real Reset showed progress for 15 s with `GET .../config` at
~14 ms -- the review's 90 s freeze was its failing baseline persona, now a reported failure.
**Round 2 seen live** (`scratchpad/r2*.cjs`): Undo survived a reload after a palette click
and after a drag-drop place + Save layout (both before the fix -- the reviewer's R2-1 did
not reproduce; the content check is the safer key anyway); `office` dragged up out of
`10.70` stays inside the grown site; the gw legs leave the icon's border; a leg over the
site hovers / selects / menus; Apply (persona change) then Cancel: 409 cancelled in 4 s,
tfc `attempted`, pill red, Reset replayed the baseline (`reverted tfc`). R2-4's "lower part
of a chip selects the segment" did not reproduce by element hit-test at zoom 2 or 1 -- the
likeliest cause was the leg's 14 px hit zone over the chip, gone now.

**The serve** (pid 1075721, `none` mode, images root) was restarted 05:43 for the cancel
route; `ui/dist` was rebuilt beside and swapped again at 12:40 (`vite build --outDir
dist.new`; `ui/dist.old` is the previous build, deletable). Memory has
the anchored `pgrep` (an unanchored one killed the bash wrapper, not the serve).

## Standing on dev45 — left up for the user

te `canvas-test-f919dae1` + tap `…-1a070fcc` (→ XPress-SW-A) + tfc `…-bb83ca92`; the working
copy is now clean (the live Reset above replayed the committed scripted tfc). The reviewer's
uncommitted edit (library `file-server` persona in `tfc/default.yaml`) was reverted;
`catalog/twins/canvas-test/generated/` (layout.json, .history) is untracked, as is
`canvas-test-exp1` from the review's Save-As. Teardown: `axrange te destroy
canvas-test-f919dae1 --allow-destroy --db ~/.axrange/state.db`.

## Current state

- **Tests:** engine full suite green (05:40 run, exit 0; untouched since); ui typecheck
  clean, **242 tests in 18 files** (`canvas.round2.test.tsx` new); `ui/dist` live.
- Untracked: `catalog/twins/{test2,test-twin-2,corp-apcn-reader-20261028,mylab,canvas-test-exp1}`.

## Issues left

- From the review, not done: the canvas cannot author `install:` (so a designed file server
  runs no smbd — the review's root cause); a cancel reaches only the process running the
  playout; the executor's cancel lands between ops (one host's drive finishes first). A leg
  is picked only over the pane or a site's background (never over a chip/segment/router):
  a leg wholly inside other boxes is selectable from the node panel only.
- **Open, small (older):** a tile says "1 host" for a `count: 2` group; compact tiles keep
  full height; `POST /api/validate` 400 for a leg-less router mid-edit; the bar wraps below
  ~1500 px.
- **Yours to decide:** the serve's auth mode (`none` on 0.0.0.0); A8 provenance; the Checks
  tab; the untracked catalog folders; merge to `master`; whether the undo stack should move
  server-side (chosen browser-side for now).

## Next steps

0. Push the two commits. Re-cast corp-apcn-reader-20261028 (IPv6 segment).
1. `install:` / `requires` / `resources` on a palette group and in the node panel, so a
   canvas-designed server runs its daemon.
2. The small items above, then the libvirt path and live Splunk/Elastic.

## Gotchas

- Write policy resolved ONCE at `create_app`; `ConfigExits` too — tests that flip settings
  build a second app. A cancel test needs a persona backend that blocks (`_Blocking…`).
- Pane click in jsdom: React Flow's selection mode dispatches it from pointer-down +
  pointer-up (`clickPane()` in `canvasFixtures`), `fireEvent.click` does nothing.
- `rf.screenToFlowPosition` SNAPS to the grid when snapToGrid is on: a hit-test must pass
  `{ snapToGrid: false }` (the leg pick missed by up to half a grid cell before).
- A router node spans the segments it joins (an invisible box): clip against its icon.
- The hover card has a 350 ms dwell: tests `findByTestId` it. The fake API's timestamps
  come from `stamp()` (monotonic): `applied.at === updated_at` means "applied".
- Chip/tile constants are hashed into the plane stamp (epoch 2). Every edit must leave the
  document free of ERRORS (warnings pass). jsdom's zoom is 1; pin a level with `rail-detail`.
- `tap`/`tfc deploy` take `--twin <id> --db`, no `--images`. Stage by path, never `git add
  -A`. Engine tests `-n auto -p no:cacheprovider`; `ui/`: `npm ci` only; never build into
  a served `ui/dist`.

## Environment

One venv at `.venv/`, node 22 for `ui/`, live state in `~/.axrange/`. Playwright 1.61 at
`/home/rocky/fx-cap/mgmtui/node_modules/playwright` + `~/.cache/ms-playwright/chromium-1228`.
dev45 API 10.80.76.108:8006, SSH root (default key); XPress-SW-A `fxadmin@10.80.72.43`.
