# Session State — the cross-session handoff

<!-- REPLACED at every handoff, never appended (`/handoff`); checked against the repo at every
start (`/catchup`). Keep it under 8 KB and about NOW. A stale handoff is worse than none,
because it will be believed. -->

**Last updated:** 2026-11-06 05:55 · **Branch:** `wip/design-platforms-canvas`, seven
commits past origin's `8cc405a` (65dd86b..HEAD, this handoff the seventh), **not pushed** (the user pushes).
Rule: commit each track as it lands, staged by path; push only when asked.

## What this session did

The canvas review a Claude-desktop browser session wrote on 2026-11-05 (Claude Doc
`PR6PeoXpMPjcfMikVX69qP`: an edit → Save-As → Apply round trip on the deployed `canvas-test`,
ten bugs by severity, six UX confusions) was worked in full. Decisions the user made first:
Reset discards first and replays best-effort; the undo stack lives in the browser per working
copy; placement is arm-then-click only; the bar regroup, a cancel for long exits, per-change
discard and restoring canvas-test's committed tfc were all in scope.

1. `65dd86b` **api (A2):** Reset deletes the working copy BEFORE replaying the baseline's
   tiers; a failed replay is `replay_failed` + a warning, not a 500 (a baseline persona that
   could not play had wedged the copy). `{tiers: [...]}` replays named baseline tiers with
   or without a copy. `GET .../config` carries `exit` while an Apply/Reset runs; `POST
   .../config/cancel` sets a flag the executor polls between ops (`409 cancelled`, the tier
   left `attempted`); a second exit is `409 busy` at once. `edits.ConfigExits` on
   `app.state`, in-process. Spec: `api/Spec.md` A2. Engine suite green (+2 tests).
2. `710f68c` **ui, the deployed twin's exits:** per-tier pill (te/tfc/tap, red when failed,
   "Re-apply <tier> from baseline"); *applied · unpersisted* vs *unapplied changes*;
   progress with Cancel; a 409 shown at once as `busy:` / `cancelled:`; Reset's failed
   replay reported; Undo survives a reload (localStorage per deploy + `updated_at`); Discard
   per pending change in Checks; the layout group floats at the canvas's bottom
   (`layoutBar`); exits grouped history | working copy | save-as; notes fade after 8 s.
3. `8ae7dde` **one placement rule:** click arms, click a segment places (a router: a segment
   or an empty spot), Esc disarms; other twins' groups under a folded "From other twins".
4. `e8b7791` **positions:** site/segment positions settle for the session (`settled` ref in
   `CanvasPage`), a box never lands under a pinned one (`freeSlot`, the site grows), the
   view fits once (`useNodesInitialized`), legs at `EDGE_Z` 3 above the boxes. Test
   fixtures moved to `ui/test/helpers/canvasFixtures.tsx`.
5. `3a7d104` rename re-keys the selection; Save-As carries the layout draft; the next /24
   after the highest in use; hover card on a 350 ms dwell, closes on press, flips at the
   edge; no evidence row on authored chips; the twin page shows the standing deploy per
   tier and no discovery facts for an authored twin.
6. `1ed813c` the pill's tier words compact (`te ✓ · tfc ✗ failed`) so the bar is one row.

**Seen live** (:8080, Playwright, 1622 × 980, `scratchpad/review.cjs`): bar 46 px one row;
a collapsed inspector opens on a tab click (the review's item did not reproduce); the card;
"add a leg on…" registers a keyboard pick (the review's failure was its automation); legs
z 4 over segments z 1; a real Reset on canvas-test showed "resetting… replaying tfc · N s
Cancel" for 15 s while `GET .../config` stayed ~14 ms and the page answered in ~11 ms — the
review's 90 s freeze came from its failing baseline persona, now a reported replay failure.

**The serve** (pid 1075721, `none` mode, images root) was restarted 05:43 for the cancel
route; `ui/dist` is rebuilt beside and swapped (`vite build --outDir dist.new`). Memory has
the anchored `pgrep` (an unanchored one killed the bash wrapper, not the serve).

## Standing on dev45 — left up for the user

te `canvas-test-f919dae1` + tap `…-1a070fcc` (→ XPress-SW-A) + tfc `…-bb83ca92`; the working
copy is now clean (the live Reset above replayed the committed scripted tfc). The reviewer's
uncommitted edit (library `file-server` persona in `tfc/default.yaml`) was reverted;
`catalog/twins/canvas-test/generated/` (layout.json, .history) is untracked, as is
`canvas-test-exp1` from the review's Save-As. Teardown: `axrange te destroy
canvas-test-f919dae1 --allow-destroy --db ~/.axrange/state.db`.

## Current state

- **Tests:** engine full suite green (background run, exit 0; `test_api_edit_model.py` 44);
  ui typecheck clean, **227 tests in 17 files**; `ui/dist` live.
- Untracked: `catalog/twins/{test2,test-twin-2,corp-apcn-reader-20261028,mylab,canvas-test-exp1}`.

## Issues left

- From the review, not done: the canvas cannot author `install:` (so a designed file server
  runs no smbd — the review's root cause); a cancel reaches only the process running the
  playout; the executor's cancel lands between ops (one host's drive finishes first).
- **Open, small (older):** a tile says "1 host" for a `count: 2` group; compact tiles keep
  full height; `POST /api/validate` 400 for a leg-less router mid-edit; the bar wraps below
  ~1500 px.
- **Yours to decide:** the serve's auth mode (`none` on 0.0.0.0); A8 provenance; the Checks
  tab; the untracked catalog folders; merge to `master`; whether the undo stack should move
  server-side (chosen browser-side for now).

## Next steps

0. Push the seven commits. Re-cast corp-apcn-reader-20261028 (IPv6 segment).
1. `install:` / `requires` / `resources` on a palette group and in the node panel, so a
   canvas-designed server runs its daemon.
2. The small items above, then the libvirt path and live Splunk/Elastic.

## Gotchas

- Write policy resolved ONCE at `create_app`; `ConfigExits` too — tests that flip settings
  build a second app. A cancel test needs a persona backend that blocks (`_Blocking…`).
- Pane click in jsdom: React Flow's selection mode dispatches it from pointer-down +
  pointer-up (`clickPane()` in `canvasFixtures`), `fireEvent.click` does nothing.
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
