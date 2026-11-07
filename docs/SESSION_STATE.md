# Session State — the cross-session handoff

<!-- REPLACED at every handoff, never appended (`/handoff`); checked against the repo at every
start (`/catchup`). Keep it under 8 KB and about NOW. A stale handoff is worse than none,
because it will be believed. -->

**Last updated:** 2026-11-07 · **Branch:** `wip/design-platforms-canvas`, **not pushed** (the
user pushes). Commits since origin's `f24648d`: `42c00de` bus plane, `5df0eb2` the brief,
`f55d2db` the design doc, `e169f32` track 2, `0d2e3cc` track 3, `ccc680b` track 4, `ed3f183`
5a, `08f70c1` 5b, `5e9f5ed` 5c, `663d77e` 5d, then track 6 (this commit). Rule: commit each
track as it lands, staged by path; never `git add -A`; never push.

## What this session did: the topology-layers build, complete

`docs/design/topology-layers.md` (§10 and §11 confirmed by the user) built as tracks 2–6,
autonomously, with the user's three answers: restart the :8080 serve as needed; a **converted
copy** of corp-twin for the eye check (never corp-twin itself); a dev45 deploy of a small switch
fixture **after tearing down canvas-test**.

- **2** twin config `kind: switch` / `links[]` / `members[]` / provenance, IR 3→4, seven validator
  rules (114→121), the planner skips an imageless switch; `catalog/twins/switch-test`. **Live:**
  canvas-test destroyed; `switch-test-5c614b0e` deployed on dev45 (no VM for `access-sw`), tap +
  tfc applied, the sink's `gre-cap` RX climbed and the mirror showed 10.71.10.2 → 10.71.20.10:445.
  **Left up** for the user (teardown in its README).
- **3** `axr-assertions/2` (device / link / tap adds, `device_kind` / `group` overrides, link
  overrides, confirm / doubt); the cast writes `source` everywhere; the engine's reader and
  console mirrors follow.
- **4** A2 kinds `traffic` / `taps` / `topology` / `inert`; the Checks badges.
- **5a** the dagre device tree, hues, folds, the overlay, plane epoch 6 (`canvas.tree.test.tsx`).
- **5b** the inspector by tier, the tap plan editor (`edit/tap.ts`), confirm / doubt / assert
  through the seam, the Checks Provenance summary; NET-090 lets a switch leg carry a management
  address; a re-select after a reload opens again (`canvas.inspector.test.tsx`).
- **5c** `edit/members.ts`: split / merge / detach / move-one; the `×N·` dot, member chips, the
  member panel (`canvas.members.test.tsx`).
- **5d** `edit/subtree.ts`: subtree select, Ctrl+D parked copy with `-2` names and the next free
  `/24`, Duplicate onto a device, attach device-device / device-host, a parked device dropped on
  a device (`canvas.subtree.test.tsx`).
- **6** the eye check on the final build (`scratchpad/t6-*.png`: canvas-test, datacenter,
  corp-enterprise, corp-twin, the largest cast twin, plus `t5*`); the acceptance checklist in
  `ui/src/features/edit-canvas/SPEC.md` ("The device tree checklist"), each line with its proof.

## Current state

- **Tests:** engine 4244 passed, 3 environmental failures (the user's untracked catalog folders
  `corp-apcn-reader-20261028` without `twin.yaml`, `test2` / `test-twin-2` with no nodes);
  discovery 2471 passed; ui typecheck clean, 23 files / 346 tests.
- **Serve:** :8080 runs the 5d `ui/dist` (restarted six times this session; env in the
  scratchpad's `serve.env`; the recipe in memory).
- Untracked, the user's call: the catalog folders above, `catalog/twins/corp-tree` (keep: the
  eye fixture, 12 switches / 13 links), `ui/dist.old/`.

## Not built (the SPEC's checklist names them)

Multi-select member rows; the split recorded as an assertion keyed by entity keys; a zoom
threshold opening the chips; the floating copy under the cursor; drop on empty canvas;
site-prefix re-allocation under another site; the marquee as a subtree.

## Issues left

- On a **deployed** twin the engine's expander refuses a parked switch (no nets, no carrying
  link) outright, so the A2 sync reverts a Ctrl+D copy with the reason in the strip; Duplicate
  onto a device works there. Relaxing the expander to let NET-140 carry it is a contract call.
- The down access link to a host inside a pill routes around the pill; a link to a host could end
  at the pill's edge instead.
- Two corp-twin segments share one CIDR (`mx-vlan20` / `vlan20`): the lifted graph keys hosts by
  CIDR, so one pill shows the count and no icons (delta D3: key by name).
- Folded carriers, open member chips and the overlay are per page / per user, not saved. The
  canvas cannot author `install:`.
- Small, older: a tile says "1 host" for a `count: 2` group; the bar wraps below ~1500 px.
- Yours to decide: the serve's auth mode (`none` on 0.0.0.0); merge to `master`; a server-side
  undo stack; whether corp-twin itself converts (D2 says only by the operator's hand).

## Gotchas

- `cd` inside a Bash call does not stick between calls here: use absolute paths.
- React Flow drops an edge whose `sourceHandle` names a handle of the wrong type; a handle
  that fills its node swallows the node's drag (`nodrag`). Its `onSelectionChange` echoes a
  programmatic selection back: the canvas ignores the echo while a subtree is selected.
- `rf.screenToFlowPosition` SNAPS when snapToGrid is on: hit-tests pass `{ snapToGrid: false }`.
- `ugrep` treats `model/graph.ts` as binary: grep it with `-a`.
- With a working document the page opens the DESIGN view; tests wanting the cast view seed no
  document and a tree. The page validates every edit: a test reads the newest `validate` write.
- A one-line flow link (`- { a: {...}, ... }`) is re-serialised whole by `setLinkField`; the
  YAML splicer cannot address a nested flow map on its own.
- Write policy and `ConfigExits` resolve ONCE at `create_app`. Engine tests `-n auto -p
  no:cacheprovider`; `ui/`: `npm ci` (the one `npm install` was dagre's); never build into a
  served `ui/dist` (build to `dist.new`, swap); stage by path, never `git add -A`.
- The contract files (`docs/contracts/*`) change only with the user's confirmation and a version
  bump in the same commit as the code (done for IR 4 / assertions 2).

## Environment

One venv at `.venv/`, node 22 for `ui/`, live state in `~/.axrange/`. Playwright 1.61 at
`/home/rocky/fx-cap/mgmtui/node_modules/playwright` + `~/.cache/ms-playwright/chromium-1228`.
dev45 API 10.80.76.108:8006, SSH root (default key); XPress-SW-A `fxadmin@10.80.72.43`.
