# Session State — the cross-session handoff

<!-- REPLACED at every handoff, never appended (`/handoff`); checked against the repo at every
start (`/catchup`). Keep it under 8 KB and about NOW. A stale handoff is worse than none,
because it will be believed. -->

**Last updated:** 2026-11-05 (late) · **Branch:** `wip/design-platforms-canvas` = `origin`
(`cc73511`, everything pushed on the user's request). Rule: **commit each track as it lands,
staged by path; push only when asked.**

## What this session did (2026-11-05, on this host)

Built the 2026-11-05 design (`docs/design/ui-shell-and-auth.md`) end to end, one commit per
track, ui gate (typecheck + vitest) after each, engine suites after the engine track:

1. `ed733d0` **A8 auth modes** — `api/auth.py` resolves `AXRANGE_AUTH = none | proxy | token`
   once at `create_app` (`none` stands alone; `token` without a token or an unknown mode is
   an `ApiSettingsError`, `axrange serve` exits 2); `_require_write_token` dispatches and
   leaves `request.state.user` / `auth_mode`; `GET /api/whoami` → `{user, can_write, mode,
   signin_url}`; proxy trust is the TCP peer (`AXRANGE_TRUSTED_PROXY_CIDRS`, uvicorn
   `proxy_headers=False`); the `none` warning prints on every bind. `tests/test_api_auth.py`.
2. `1e06e66` **console holds no credential** — token field and plumbing gone,
   `credentials: "include"`, `whoami` on load (404 = assume nothing), name + read-only pill +
   Sign in link in the bar; 401 → "sign in required" (+ served `signin_url`), 403 →
   "read-only for this account". C1.6 rewritten.
3. `1cb35d4` **the canvas is the page** — canvas routes fill the window under the one bar
   (`main.canvas-main`); title, pills, Design/Evidence, layout group and exits portal into
   the bar (`areas/shell/topbar.tsx`); messages float as `.cv-strip`; no sub-bar, grid or card.
4. `83aa536` **rail + inspector** (`features/edit-canvas/chrome.tsx`, `icons.tsx`) — 40 px icon
   rail (V/H select-marquee or pan, +/−/F, L, D detail, T light-up, / find; edit: + palette,
   attach, snap, grid, align, annotate), one popover at a time; inspector top-right with
   Selection / Hosts / Notes / **Checks** (a fourth tab, not in the design: validation +
   pending changes, the validation word as its badge); placement warnings as a dismissible
   strip; blocked reason and the API's refusal on the message strip.
5. `46742f2` **node level of detail** after the mockup round
   (https://claude.ai/artifact/9FrccaEjWLRPobc5xAhDoQ; user: 0.6 with a rail toggle) —
   `model/detail.ts`: expand ≥ 0.6, collapse < 0.5, hold between; D cycles auto/full/compact;
   compact = chips hidden, glyph row on the tile (`RoleIcon`), arc labels off; full = glyph in
   the evidence ring + short name, address/role as `data-*` and on the hover card; routers
   and the chrome are icon nodes.
6. `edfb66a` **the basics** — Delete/Backspace removes the selection (not from a field, not
   when locked; annotations even in view mode); double-click a segment/site fits to it
   (`data-zoom-target`); right-click the pane: Zoom to fit, Lock all, Find, and in edit mode
   Add a segment / Annotate here. Select mode pans with the middle button only.
7. `cc73511` a router keeps its `DEVICE_W × DEVICE_H` slot (pins and band spacing depend on
   it) with the icon on the slot's bottom edge, so the legs meet the icon.

Specs updated in step: `api/Spec.md` A8, `ui/Spec.md` Seam/Config/C1.6, the canvas `SPEC.md`
"Shell and chrome" (each bullet marked built), `docs/OPERATIONS.md` §2/§5.

## Current state

- **Tests at HEAD:** engine 4180 passed / 19 skipped / 2 failed (pre-existing: the untracked
  `catalog/twins/{test2,test-twin-2,corp-apcn-reader-20261028}` folders without a manifest);
  ui typecheck clean, **174 tests**, build clean; `ui/dist` rebuilt from HEAD.
- **Working tree clean** apart from the three untracked catalog folders. No agents running.
- **:8080 serve restarted on request 2026-11-05 21:27 in `none` mode** (pid 1008702; same
  command and `AXRANGE_*` env as before minus `AXRANGE_API_TOKEN`; log
  `~/.axrange/serve-8080.log`). It serves `ui/dist` live, so every track above is what :8080
  shows. Every write on that bind is accepted from anyone; set the token or `AXRANGE_AUTH=
  proxy` (+ `AXRANGE_TRUSTED_PROXY_CIDRS`) before exposing further. Restart only on request.
- **Nothing of this session was seen in a browser** (no browser tool here): jsdom tests and
  the build only. First thing: `/canvas/<twin>` at 1622 px, zoom across 0.5/0.6, D, Delete,
  double-click a tile, right-click the pane, the inspector tabs.
- Existing cast bundles predate the `members` row: promote shows disabled until re-cast.

## Next steps (ordered)

0. Eyes on the shell (above); fix what the first look finds. Likely candidates: glyph-row
   and chip sizes (tuned from the mockup, not a screen). The chrome labels are short now
   (full wording in the title) in 300/360 px slots; the inspector is 460 px on Checks and
   drag-resizable on its left edge (double-click resets).
1. The Canvas area against the largest cast twin (element budgets are measured, not
   assumed); create one blank design from `/canvas/new`; re-cast one imprint and exercise
   promote end to end.
2. Libvirt end to end is blocked by the confirm-first `vmid` → guest-handle bump (the
   `int(vmid)` readers in `config_exec/backend.py`, `planner/*`, `o2control/containment.py`,
   `api/service.py`) and by `planner/pass1_emit.py:149 ROUTER_WAN_BRIDGE="vmbr0"`.
3. Then the live libvirt two-host slice (`config/adapter.conf.libvirt.example`).
4. Live Splunk/Elastic against a Corelight-fed index (fixtures are authored, not recorded).

## Decisions pending the user

- **A8 provenance:** no write records a user yet; `request.state.user` is set and unused.
  Decide where it lands before proxy mode goes live. **The Checks tab** is an addition.
- **Confirm-first contract bumps:** neutral op ids; `vmid`/`pve_node` → guest handle + `host`;
  `zone_type` → an L2 mode; `ADAPTER.md` + `PLATFORM.md`; a neutral router-uplink marker.
- **libvirt NAT/DHCP:** keep refusing `snat`/`dhcp`, or implement `<forward mode='nat'>`?
- **Layering moves held:** `ingest/siem/http.py` → `ingest/http.py`; hoisting the Proxmox-only
  flows out of `adapter_proxmox/rest.py`. **Edit model:** Persist of a never-applied working
  copy marks the deploy clean? keep `inert`?
- Merge of this branch and `wip/api-discovery-runs` to `master`; fate of the untracked folders.
  TRex ASTF as a "crowd" traffic generator: parked 2026-11-05.

## Gotchas

- The write policy is resolved ONCE at `create_app`; a test that flips `settings.api_token`
  after building the app must build a second app. `TestClient(app, client=("10.0.0.5", 1))`
  sets the peer the proxy CIDR check sees; the default `"testclient"` is never trusted.
- Canvas tests: the palette is the rail's `+` popover (`rail-add`), find/traffic/annotate are
  popovers, the inspector shows ONE tab (a node click switches to Selection); helpers
  `openPalette()`, `item()`, `ann()`, `tab()` in `canvas.edit.test.tsx`. `validation-state` is
  the Checks tab's badge. jsdom's zoom is 1, so tests see full detail; pin a level with
  `rail-detail`. React Flow never marks a chip `selected` in jsdom; drops and drags are inert.
- Stage by path, never `git add -A`. `test_api_console.py::_MIRRORED_DTO_FIELDS` pins console
  DTO field sets by hand. Engine tests with `-n auto -p no:cacheprovider`; the two Python
  `tests/` trees separately. `ui/`: `npm ci` only; `@xyflow/react` pinned 12.12.0.

## Environment

Unchanged from 2026-10-28: one venv at `.venv/`, node 22 for `ui/`, live state in `~/.axrange/`.
Podman + `docker.io/zeek/zeek:8.0` on this box; no libvirt, no docker, no Splunk/Elastic.
