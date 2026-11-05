# Session State — the cross-session handoff

<!-- REPLACED at every handoff, never appended (`/handoff`); checked against the repo at every
start (`/catchup`). Keep it under 8 KB and about NOW. A stale handoff is worse than none,
because it will be believed. -->

**Last updated:** 2026-11-05 (night) · **Branch:** `wip/design-platforms-canvas` = `origin`
(`6821f32`, pushed on request). Rule: **commit each track as it lands, staged by path; push
only when asked.**

## What this session did (2026-11-05, on this host)

Built the 2026-11-05 design (`docs/design/ui-shell-and-auth.md`) end to end, one commit per
track, ui gate after each, engine suites after the engine track; then looked at it:

1. `ed733d0` **A8 auth modes** — `api/auth.py`, `AXRANGE_AUTH = none | proxy | token`
   resolved once at `create_app`; `_require_write_token` dispatches, `request.state.user`;
   `GET /api/whoami` → `{user, can_write, mode, signin_url}`; proxy trust is the TCP peer
   (uvicorn `proxy_headers=False`); the `none` warning on every bind. `tests/test_api_auth.py`.
2. `1e06e66` **console holds no credential** — `whoami` on load, name + read-only pill + Sign
   in link; 401 → "sign in required" (+ `signin_url`), 403 → "read-only for this account".
3. `1cb35d4` **the canvas is the page** — full window under the one bar; title, pills and
   exits portal into the bar (`areas/shell/topbar.tsx`); messages float as `.cv-strip`.
4. `83aa536` **rail + inspector** (`chrome.tsx`, `icons.tsx`) — icon rail with popovers
   (find, traffic, palette, annotate); inspector Selection / Hosts / Notes / **Checks** (a
   fourth tab, not in the design); placement warnings as a dismissible strip.
5. `46742f2` **node level of detail** (mockups: https://claude.ai/artifact/9FrccaEjWLRPobc5xAhDoQ;
   user: 0.6 with a rail toggle) — `model/detail.ts`: expand ≥ 0.6, collapse < 0.5; D cycles
   auto/full/compact; compact = glyph tiles, chips hidden, arc labels off; full = glyph in
   the evidence ring + short name; routers and the chrome are icon nodes.
6. `edfb66a` **the basics** — Delete, double-click a tile to zoom, pane right-click menu
   (select mode pans with the middle button only). Then the router / chrome icons under
   their handles, the inspector 460 px on Checks and drag-resizable, chrome labels short.
7. `eb0b01b` **what the first look found** (Playwright, see below) — compact names were
   12.5 px (`.mono` overrides), the count clipped (now a number pill; CIDR and gw badge go at
   compact), chrome names fit, attach handles only on hover / attaching / target, the
   inspector stops short of the mini-map, chips 13.5 px.
8. `6821f32` **routers place without a segment** (user: a router joins segments) — palette
   Router item, lands on the pane or with a selected segment as its first leg; a copied `gw`
   stays a router; CV-024 (unattached) is a warning, an error blocked the placement.

Specs follow each track: `api/Spec.md` A8, `ui/Spec.md` Seam/Config/C1.6, the canvas
`SPEC.md` ("Shell and chrome" bullets marked built, "Routers"), `docs/OPERATIONS.md` §2/§5.

## Current state

- **Tests at HEAD:** engine 4180 passed / 19 skipped / 2 failed (pre-existing: the untracked
  `catalog/twins/{test2,test-twin-2,corp-apcn-reader-20261028}` folders without a manifest);
  ui typecheck clean, **179 tests**, build clean; `ui/dist` rebuilt from HEAD.
- **Working tree clean** apart from the three untracked catalog folders. No agents running.
- **:8080 serve restarted on request 2026-11-05 21:27 in `none` mode** (pid 1008702; same
  command and `AXRANGE_*` env minus `AXRANGE_API_TOKEN`; log `~/.axrange/serve-8080.log`).
  It serves `ui/dist` live. Every write on that bind is accepted from anyone: set the token
  or `AXRANGE_AUTH=proxy` (+ `AXRANGE_TRUSTED_PROXY_CIDRS`) before exposing further.
- **Seen in a browser:** Playwright 1.61 at `/home/rocky/fx-cap/mgmtui/node_modules/playwright`
  with `~/.cache/ms-playwright/chromium-1228` (1622 × 980; the rail's zoom buttons zoom,
  `mouse.wheel` does not). Looked at: corp-apcn-reader compact at fit zoom 0.44 and full at
  1.57, corp-twin, corp-enterprise, the palette, the pane menu, the Hosts tab. Not yet:
  dark theme, view mode on a deployed twin, a dense Checks tab, the new Router item live.
- Existing cast bundles predate the `members` row: promote shows disabled until re-cast.

## Next steps (ordered)

0. Open from the look: single-host 156 px tiles ellipsize their name at compact; site labels
   sit on the tiles' top edge; corp-twin still fits at zoom 0.5 (its 42-host segments make
   tall tiles that are empty when compact). Fixed: the bar is one row at 1622 px (wraps
   below ~1500); a site's tile columns follow the plane's shape (`chooseSiteCols`, plane
   epoch 2, so pins saved before today are not applied): corp-apcn-reader fits at 0.70 in
   full detail, corpapcn-golden at 1.05. Then eyes on the unseen list above.
1. The Canvas area against the largest cast twin (element budgets are measured, not
   assumed); create one blank design from `/canvas/new`; re-cast one imprint and exercise
   promote end to end; place a Router and attach it, live.
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
  sets the peer the proxy CIDR check sees; `"testclient"` is never trusted.
- Chip/tile constants are hashed into the plane stamp (`canvas/plane.ts`): changing one
  drops every saved layout. Sizes are tuned inside the boxes; compact header text is
  screen-constant via `--inv` (1/zoom ≤ 2.5), the glyph row capped by `glyphCapacity`.
- Canvas tests: the palette is the rail's `+` popover (`rail-add`), find/traffic/annotate are
  popovers, the inspector shows ONE tab (a node click switches to Selection); helpers
  `openPalette()`, `item()`, `ann()`, `tab()` in `canvas.edit.test.tsx`. `validation-state` is
  the Checks badge. jsdom's zoom is 1: tests see full detail; pin a level with `rail-detail`.
  Every edit must leave the document free of ERRORS (warnings pass): a blocking rule blocks
  the gesture, so a "not yet" condition is a warning.
- Stage by path, never `git add -A`. `test_api_console.py::_MIRRORED_DTO_FIELDS` pins console
  DTO field sets by hand. Engine tests with `-n auto -p no:cacheprovider`; the two Python
  `tests/` trees separately. `ui/`: `npm ci` only; `@xyflow/react` pinned 12.12.0.

## Environment

Unchanged from 2026-10-28: one venv at `.venv/`, node 22 for `ui/`, live state in `~/.axrange/`.
Podman + `docker.io/zeek/zeek:8.0` on this box; no libvirt, no docker, no Splunk/Elastic.
