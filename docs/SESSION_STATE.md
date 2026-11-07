# Session State — the cross-session handoff

<!-- REPLACED at every handoff, never appended (`/handoff`); checked against the repo at every
start (`/catchup`). Keep it under 8 KB and about NOW. A stale handoff is worse than none,
because it will be believed. -->

**Last updated:** 2026-11-07 · **Branch:** `wip/design-platforms-canvas`, three commits past
origin's `f24648d` (track 4 `42c00de`, the brief `5df0eb2`, this one), **not pushed** (the user
pushes). Rule: commit each track as it lands, staged by path; push only when asked.

## What this session did

**Topology layers, step 1 (design doc only).** From the user's brief of 2026-11-07
(`docs/design/topology-layers.brief.md`) wrote **`docs/design/topology-layers.md`** in the
house style of `imprint-twin-split.md`: vocabulary, the device tree (`kind: switch`, `links[]`,
`source` / `confidence` / `vantage`, `members[]`, `twin.yaml` `vantage`), provenance and the
overlay, three change kinds per tier, groups and members, subtree select / duplicate / attach,
canvas rendering (dagre device tree, icons, hover, inspector by tier, folding, hues), taps.
§9 **Deltas** (D1-D14, each with file and line), §10 the **draft** diff to `IR_SCHEMA.md`
(`3 → 4`, `links[]`, §3.6 provenance, `members[]`) and `VALIDATOR.md` (STR-210/220, REF-270/280,
NET-120/130/140, 114 → 121) -- **not applied**; §11 the brief's four open decisions verbatim
with a recommendation each; §12 the build order with sizes.

SPEC files touched only to point at the design doc: `ui/src/features/edit-canvas/SPEC.md`
("What it is" and the "Rendering rules" heading), `api/Spec.md` (A2 and the edit-model table).

**STOP point: nothing in code starts until the user confirms the §10 contract changes and
answers §11.** The deltas that change the brief's own wording: `kind: switch` already exists in
the Scenario IR, the new scope is the twin config (D1); corp-twin's switches are VMs with
images and must keep deploying as such (D2); "two segments with one CIDR" is NET-010 with the
`isolated` anchor exemption corp-twin relies on (D3); A2 already carries `via: [tfc|tap]`, so
track 4 is a rename plus the members rule (D6); member traffic overrides belong to the tfc
profile's `assignments[] {node: <instance>}`, `members[]` carries `role` only (D7); a vantage is
`<appliance>:<streams>`, not a place, so a discovered tap is proposed only when mappable (D8).

## Standing on dev45 — left up for the user

te `canvas-test-f919dae1` + tap `…-1a070fcc` (→ XPress-SW-A) + tfc `…-bb83ca92`. Teardown:
`axrange te destroy canvas-test-f919dae1 --allow-destroy --db ~/.axrange/state.db`. The :8080
serve (pid 1125381, `none` mode) runs track 4's `ui/dist`; no engine or ui code changed here.

## Current state

- **Tests:** untouched this session: ui typecheck clean, 283 tests in 20 files; engine green at
  its last run (2026-11-06 14:43).
- Untracked, the user's call: `catalog/twins/{test2,test-twin-2,corp-apcn-reader-20261028,mylab,
  canvas-test-exp1}`, `catalog/twins/canvas-test/generated/`, `ui/dist.old/`.

## Issues left (unchanged from track 4)

- Two corp-twin segments share one CIDR (`mx-vlan20` / `vlan20`): the lifted graph keys hosts by
  CIDR, so one bus shows the count and no icons. The design keys segments by name (D3).
- A leg over a lane's port region is picked before the lane; press the label to reorder.
- Folded sites and opened fans are per page, not saved. The canvas cannot author `install:`.
- Small, older: a tile says "1 host" for a `count: 2` group; `POST /api/validate` 400 for a
  leg-less router mid-edit; the bar wraps below ~1500 px.
- Yours to decide: the serve's auth mode; A8 provenance; the Checks tab; the untracked catalog
  folders; merge to `master`; a server-side undo stack.

## Next steps

0. The user: confirm or amend §10 (IR `3 → 4`, seven validator rules) and §11 (the seam as
   `axr-assertions/2`; imageless switch = vnets only, with an image = VM from `links[]`; member
   overrides by index in the config, by entity key in the assertion). Push when asked.
1. Track 2 (IR + twin config + expander + validator; M, ~2 sessions), then 3 (cast + seam v2;
   L), 4 (A2 three kinds; S), 5 (canvas on dagre; XL, land as 5a-5d), 6 (eyes; S). Track 2
   blocks the rest; 5a can start on 2 alone.
2. After that: `install:` / `requires` / `resources` on a palette group; the small items above.

## Gotchas

- React Flow drops an edge whose `sourceHandle` names a handle of the wrong type; a handle
  that fills its node swallows the node's drag (`nodrag`).
- A `pointerdown` whose default is prevented suppresses the compat `mousedown` d3-drag needs.
- With every lane's rank settled, pinning ONE lane's rank clashes: a reorder re-ranks the site.
- `rf.screenToFlowPosition` SNAPS when snapToGrid is on: hit-tests pass `{ snapToGrid: false }`.
- Any node a React Flow selection lifts covers what it overlaps: the site tint is
  `selectable: false`. `ugrep` treats `model/graph.ts` as binary: grep it with `-a`.
- With a working document the page opens the DESIGN view; tests wanting the cast view seed no
  document and a tree.
- Write policy and `ConfigExits` resolve ONCE at `create_app`. Engine tests `-n auto -p
  no:cacheprovider`; `ui/`: `npm ci` only; never build into a served `ui/dist`; stage by path,
  never `git add -A`. The contract files (`docs/contracts/*`) change only with the user's
  confirmation and a version bump in the same commit as the code.

## Environment

One venv at `.venv/`, node 22 for `ui/`, live state in `~/.axrange/`. Playwright 1.61 at
`/home/rocky/fx-cap/mgmtui/node_modules/playwright` + `~/.cache/ms-playwright/chromium-1228`.
dev45 API 10.80.76.108:8006, SSH root (default key); XPress-SW-A `fxadmin@10.80.72.43`.
