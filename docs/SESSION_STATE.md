# Session State — the cross-session handoff

<!-- REPLACED at every handoff, never appended (`/handoff`); checked against the repo at every
start (`/catchup`). Keep it under 8 KB and about NOW. A stale handoff is worse than none,
because it will be believed. -->

**Last updated:** 2026-11-08 · **Branch:** `wip/design-platforms-canvas`. Pushed to origin
(through the five fixes below; push only when the user asks). Rule: commit each piece as it lands, staged by path; never `git add -A`.

## What this session did

Earlier today (pushed): the canvas follow-ups after review round 3 — the tree reads the graph
directly (`2fe0b04`), no orphan segments (`28ecfbf`), members (`9c853f1`), duplicate (`de8df00`).
Round 3's review doc: https://claude.ai/artifact/PR6PeoXpMPjcfMikVX69qP.

Then, asked to fix five open items (pushed):

- **`211de33`** imprint JSON editor keeps `confirm` / `doubt` through a save (it sent only
  add / override / suppress, erasing the marks).
- **`89ce070`** deploy from the console: the twin page's Deploy panel (Twins list "Deploy" lands
  there) — `POST /api/twins/{id}/plan` (pure), then *Deploy this plan* → a confirmation naming
  the VM count and target → `POST /api/twins/{id}/deploy` with `expect_plan_id`. A standing
  deploy or a read-only caller gets the reason. **Never run for real**: tested against the fake
  API; on the serve only the plan was clicked (corp-tree: 147 VMs, 1,264 ops).
- **`4f65174`** a copy dropped on the canvas keeps the original's shape (`copyPositions`: every
  copied device and pill pinned at its original's position shifted by the root's offset).
- **`a4b3e6b`** *Split and assert* asks which observed hosts the split instances are (the cast
  ties no instance to a host; it used to take the i-th sorted key); the count-mismatch refusal
  is gone.
- **`769df72`** R3-6 found: not a main-thread freeze (no long task over 92 ms through load,
  folds, zoom, pan, marquee, drag) but a request flood on open — the twin-config listing (4 s
  alone, 8.8 s under load), a read of every other twin's config, the 5 s validate beside them;
  first clicks waited ~10 s. The palette's catalog now loads on first open; the engine's
  listing memoises each file's head (LES-283 key) and skips YAML without `twin_config`
  (repeat listing 3.25 s → 0.07 s). corp-twin quiet in 4.3 s, was 10.7 s.
- SPEC: "Canvas follow-ups (2026-11-08)" gained the four lines; rule 11 says the new split.

## Current state

- **Tests:** ui typecheck clean, 27 files / 431. Engine: the 50 catalog-touching test files
  2811 passed + the 3 known environmental failures (the user's untracked catalog folders:
  `test_default_catalog_dir_exists_and_has_the_expected_configs`,
  `test_load_twin_config_every_shipped_config_parses`,
  `test_every_migrated_twin_folder_has_a_valid_manifest`). Full engine suite not re-run.
- **Serve:** :8080 restarted 2026-11-08 (pid 1509220, `none` mode, same env) on the rebuilt
  `ui/dist`; the listing memo is live (first listing 3.4 s, repeat 0.06 s).
- **Eye checks** (headless, scratchpad): `I1-*` (corp-tree carry → parked drop: the copy's pills
  at the original's offset; only a validate sent), `J1/J2` (Deploy panel, plan only),
  `stress.cjs` / `net.cjs` (the R3-6 numbers). dev45 untouched.
- Untracked, the user's: the catalog folders, `ui/dist.old/`; mine: `ui/dist.prev/` (the
  previous build, safe to delete).

## Not done / honest gaps

- No live cast carries group member keys (all five casts predate the field), so on the serve
  *Split and assert* shows "no observed members"; the new step is proven in jsdom only.
- The first catalog listing after a serve restart still costs ~3 s (cold parse); only repeats
  are cheap. Validate of corp-twin is ~2 s per edit, server side.
- corp-apcn-reader-20261028 still reads invalid (its IPv6 /64, CV-012); re-cast or remove the
  segment, or decide whether the engine should refuse non-IPv4 too (a contract call).
- Yours to decide: the serve's auth mode (`none` on 0.0.0.0); merge.

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
- CanvasPage's edit-props memo omits `pins`: read them through `pinsRef` in a handler.
- Importing a helper from another `*.test.tsx` re-runs that file's tests: define it locally.
- Playwright: off-viewport tree nodes refuse `.click()`; use `el.evaluate((e) => e.click())`.
- `ui/`: `npm ci` only; never build into the served `ui/dist` (build `dist.new`, swap). The
  contract files change only with the user's confirmation and a version bump.

## Environment

One venv at `.venv/`, node 22 for `ui/`, live state in `~/.axrange/`. Playwright 1.61 at
`/home/rocky/fx-cap/mgmtui/node_modules/playwright` + `~/.cache/ms-playwright/chromium-1228`
(scripts in this session's scratchpad: `shot.cjs`, `perf.cjs`, `carry2.cjs`, `stress.cjs`,
`net.cjs`, `deploy.cjs`). dev45 API 10.80.76.108:8006, SSH root; XPress-SW-A `fxadmin@10.80.72.43`.
