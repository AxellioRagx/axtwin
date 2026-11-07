# Session State — the cross-session handoff

<!-- REPLACED at every handoff, never appended (`/handoff`); checked against the repo at every
start (`/catchup`). Keep it under 8 KB and about NOW. A stale handoff is worse than none,
because it will be believed. -->

**Last updated:** 2026-11-07 · **Branch:** `wip/design-platforms-canvas`, **not pushed** (the
user pushes). Commits since origin's `f24648d`: `42c00de` track 4 (bus plane), `5df0eb2` the
brief, `f55d2db` the design doc, `e169f32` track 2, `0d2e3cc` track 3, `ccc680b` track 4 (A2
kinds), then `ed3f183` track 5a, then `08f70c1` 5b, then `5e9f5ed` 5c, then 5d (this commit). Rule: commit each track as it lands, staged by path.

## What this session did (the topology-layers build, `docs/design/topology-layers.md`)

The user confirmed §10 and §11 and asked for tracks 2–6 autonomously, with three answers:
restart the :8080 serve as needed; a **converted copy** of corp-twin for the eye check (never
corp-twin itself); a dev45 deploy of a small switch fixture **after tearing down canvas-test**.

- **Track 2 (`e169f32`)** — twin config: `kind: switch` (imageless = no VM, imaged = a VM with one
  L2 NIC per carried segment), `links[]`, `members[] {index, role}`, `source` / `confidence` /
  `vantage` on segments, nodes, links; `twin.yaml` `vantage`; IR `3 → 4` with `links[]` and §3.6;
  STR-210/220, REF-270/280, NET-120/130/140 (114 → 121); the planner skips an imageless switch;
  the topology DTO carries `links` and provenance. `catalog/twins/switch-test` is the fixture.
  **Live:** canvas-test destroyed; `switch-test-5c614b0e` deployed (48 ops, four VMs, no VM for
  `access-sw`, instance 02 `role=jump-host` from its member override), tap + tfc applied; the
  sink's `gre-cap` RX climbed 264537 → 264640 and dev45's tcpdump showed `10.71.10.2 →
  10.71.20.10:445` through the mirror. **Left up** for the user (teardown in its README).
- **Track 3 (`0d2e3cc`)** — `axr-assertions/2`: `device` / `link` / `tap` adds, `device_kind` and
  `group` overrides, link overrides, `confirm` / `doubt`; a `/1` file parses and re-emits as `/2`;
  the schema file is `axr-assertions-2.schema.json` in both repos; the cast writes `source` on
  every segment and node, emits asserted links and taps, splits members by `group`, makes an
  asserted device a switch; the engine's reader, DTOs and the console mirrors follow.
- **Track 4 (`ccc680b`)** — A2 kinds `traffic` / `taps` / `topology` / `inert`; `members[].role` is
  traffic keyed by instance id; provenance-only te edits inert; the Checks badges read
  live · traffic / live · taps / re-plan.
- **Track 5a (`ed3f183`)** — the canvas is the device tree: `canvas/tree.ts` (dagre), `DeviceNode`,
  `LinkEdge`, the provenance overlay (rail `O`), pills under their carriers, per-carrier folds on
  the Fan-out, plane epoch 6 (absolute `segment:` pins), the reader/validator/document pipeline
  carrying switches, links, members and source; `canvas.bus.test.tsx` replaced by
  `canvas.tree.test.tsx`; the bus placement and the rank helpers are gone (git has them).
  `@dagrejs/dagre 3.1.3` added (`npm install` worked; the lock changed by 16 lines).
  **Seen** on :8080 (`scratchpad/t5-*.png`): switch-test, canvas-test, corp-enterprise, datacenter
  (three hues, one fold), corp-twin (two folds), **corp-tree** (corp-twin converted by a script:
  12 switches, 13 trunk links; an untracked fixture under `catalog/twins/corp-tree`) and the
  largest cast twin. The serve runs the rebuilt `ui/dist` (restarted three times this session;
  env in the scratchpad's `serve.env`).

## Not built yet (tracks 5b–5d, 6)

- **5b (done, this commit)**: the inspector by tier (Identity & placement / Traffic / Taps /
  Members / Provenance); confirm / mark-unconfirmed / assert through the seam (`promote` now
  carries `add` / `confirm` / `doubt`); the Checks tab's Provenance summary and filter; taps as
  chips (`edit/tap.ts`; a segment tap drags between pills); the link panel editable; NET-090's
  "no IP on a switch leg" half retired (a management SVI is allowed); a canvas fix: a re-select
  of the same node after a reload opened nothing (`lastSel` compared against a stale key).
  `ui/test/canvas.inspector.test.tsx` (22). Seen: `t5b-*.png`.
- **5c (done, this commit)**: `edit/members.ts` (split / merge / detach / move-one, every binding
  and override re-keyed), the `×N·` dot, member chips on double-click, the member panel, chip
  drag onto another group, the Members block's split form, the node panel's merge; a one-line
  flow link re-serialises whole when a field changes (`setLinkField`). Not built: multi-select
  rows, the split recorded as an assertion, a zoom threshold for the chips.
  `ui/test/canvas.members.test.tsx` (22). Seen: `t5c-members.png`.
- **5d (done, this commit)**: `edit/subtree.ts` (subtree of a device, duplicate with `-2` names
  and the next free `/24` per segment, attach device-device / device-host with free ports), the
  subtree panel, Alt-click / menu Select subtree, Ctrl+D parked copy, Duplicate onto a device,
  the attach tool's second click on a node, a parked device dropped on a device. Known: on a
  deployed twin the expander refuses a parked switch, so the A2 sync reverts a Ctrl+D copy
  with the reason (duplicate onto a device there). `ui/test/canvas.subtree.test.tsx` (23).
- **6**: the acceptance checklist is in the SPEC; the eye check above covers 5a only.

## Current state

- **Tests:** engine 4230 passed (3 environmental failures: the user's untracked catalog folders
  `corp-apcn-reader-20261028` without `twin.yaml`, `test2` / `test-twin-2` with no nodes);
  discovery 2471 passed; ui typecheck clean, 23 files / 346 tests.
- Untracked, the user's call: the catalog folders above, `catalog/twins/corp-tree` (keep: the eye
  fixture), `ui/dist.old/`.

## Issues left

- The down access link to a host inside a pill routes around the pill (smoothstep from the switch
  to the chip); fine, but a link to a host could end at the pill's edge instead.
- Two corp-twin segments share one CIDR (`mx-vlan20` / `vlan20`): the lifted graph keys hosts by
  CIDR, so one pill shows the count and no icons. The design says key by name (delta D3).
- Folded carriers and the overlay are per page / per user, not saved. The canvas cannot author
  `install:`.
- Small, older: a tile says "1 host" for a `count: 2` group; the bar wraps below ~1500 px.
- Yours to decide: the serve's auth mode (`none` on 0.0.0.0); merge to `master`; a server-side
  undo stack; whether corp-twin itself converts (D2 says only by the operator's hand).

## Gotchas

- `cd` inside a Bash call does not stick between calls here: use absolute paths.
- React Flow drops an edge whose `sourceHandle` names a handle of the wrong type; a handle
  that fills its node swallows the node's drag (`nodrag`).
- `rf.screenToFlowPosition` SNAPS when snapToGrid is on: hit-tests pass `{ snapToGrid: false }`.
- `ugrep` treats `model/graph.ts` as binary: grep it with `-a`.
- With a working document the page opens the DESIGN view; tests wanting the cast view seed no
  document and a tree.
- Write policy and `ConfigExits` resolve ONCE at `create_app`. Engine tests `-n auto -p
  no:cacheprovider`; `ui/`: `npm ci` (the one `npm install` was dagre's); never build into a
  served `ui/dist` (build to `dist.new`, swap); stage by path, never `git add -A`.
- The contract files (`docs/contracts/*`) change only with the user's confirmation and a version
  bump in the same commit as the code (done for IR 4 / assertions 2).

## Environment

One venv at `.venv/`, node 22 for `ui/`, live state in `~/.axrange/`. Playwright 1.61 at
`/home/rocky/fx-cap/mgmtui/node_modules/playwright` + `~/.cache/ms-playwright/chromium-1228`.
dev45 API 10.80.76.108:8006, SSH root (default key); XPress-SW-A `fxadmin@10.80.72.43`.
