# Session State — the cross-session handoff

<!-- REPLACED at every handoff, never appended (`/handoff`); checked against the repo at every
start (`/catchup`). Keep it under 8 KB and about NOW. A stale handoff is worse than none,
because it will be believed. -->

**Last updated:** 2026-11-05 (evening) · **Branch:** `wip/design-platforms-canvas`, four commits
past `origin` (`git rev-list --count wip/api-discovery-runs..HEAD` says 31). Remote `origin`
exists; **never push**, the user pushes. Rule: **commit each track as it lands, staged by path.**

## What this session did (2026-11-05, on this host)

Pulled the other host's design commit `fb8a4e0` (the local handoff-only commit was dropped in
the rebase; its one fact is under "Current state"), then built the four tracks of next-step 0,
one commit each, ui gate + engine tests after each:

1. `ed733d0` **A8 in the engine** — `api/auth.py` holds the policy (`AXRANGE_AUTH = none |
   proxy | token`, comma-combinable; `none` stands alone; `token` without a token and an
   unknown mode are `ApiSettingsError` at `create_app`, so `axrange serve` exits 2 naming the
   key). `_require_write_token` in `api/app.py` is the dispatcher; the identity lands on
   `request.state.user` / `auth_mode`. `GET /api/whoami` → `{user, can_write, mode,
   signin_url}` (`signin_url` = `AXRANGE_SIGNIN_URL`, carried here because there is no other
   served config object). The proxy check is on the TCP peer: `serve.py` runs uvicorn with
   `proxy_headers=False`. The `none` warning now prints on every bind. `tests/test_api_auth.py`
   (16); two `test_api.py` tests updated because the policy is resolved once, not read live.
   `docs/OPERATIONS.md` §2/§5 and `api/Spec.md` A8 say so.
2. `1e06e66` **the console holds no credential** — Write-token field, `setWriteToken` and
   the `auth` flag gone; `credentials: "include"`; `whoami()` on load (a 404 from an older
   server = assume nothing); the bar shows the name, a read-only pill and a Sign in link;
   `ErrorLine` says "sign in required" (+ link, with this page appended when the URL ends in
   `rd=`) on 401 and "read-only for this account" on 403. C1.6 rewritten (five cases).
3. `1cb35d4` **the canvas is the page** — canvas routes render into `main.canvas-main` (the
   frame is the window), no sub-bar, no heading, no card; the page's title, pills,
   Design/Evidence, layout group and exits render INTO the bar via `areas/shell/topbar.tsx`
   (`TopbarPortal`); messages float as `.cv-strip`; the 220/1fr/320 grid is gone.
4. `83aa536` **rail + inspector** — `features/edit-canvas/chrome.tsx` + `icons.tsx`: 40 px
   icon rail (V/H select-marquee or pan, +/−/F, L, T light-up, / find; edit: + palette,
   attach, snap, grid, align, annotate) with one popover at a time; the inspector top-right
   (Selection / Hosts / Notes / **Checks** — a fourth tab I added for validation + pending
   changes, wearing the validation word as its badge; not in the design, flag it); placement
   warnings as a dismissible strip bottom-left; the blocked reason and the API's refusal on
   the message strip.

**Step 5 (node level of detail) was NOT started**: mockups first, as asked. They are at
https://claude.ai/artifact/9FrccaEjWLRPobc5xAhDoQ (two zooms of one twin, the glyph set per
`model/roles.ts`, the rules assumed, four open questions: threshold / rail toggle, chip
width, router badge when zoomed out, ghosts in the glyph row).

## Current state

- **Tests at HEAD:** engine 4180 passed / 19 skipped / 2 failed (the same pre-existing two:
  the untracked `catalog/twins/{test2,test-twin-2,corp-apcn-reader-20261028}` folders without
  a manifest); the API suites re-run after track 4, green. ui: typecheck clean, **162 tests**,
  build clean; `ui/dist` rebuilt from HEAD (`assets/index-LH7v8HmD.js`).
- **Working tree clean** apart from the three untracked catalog folders. No agents running.
- **The `axrange serve` on :8080 was restarted on the user's request (2026-11-05 21:27, pid
  1008702) in `none` mode**: same command and `AXRANGE_*` environment as pid 828079 minus
  `AXRANGE_API_TOKEN` (saved as `serve.env` in this session's scratchpad; log
  `~/.axrange/serve-8080.log`, which carries the loud `AXRANGE_AUTH=none` warning). Verified:
  58 paths incl. `/api/whoami` → `{anonymous, true, none}`; a twin-config's `document` comes
  with no header; the served bundle is `index-LH7v8HmD.js`. The CLI's bearer is now ignored
  (nothing to check); set the token again, or `AXRANGE_AUTH=proxy`, before exposing further.
- Not looked at in a browser this session (no browser tool here): the chrome is verified by
  jsdom tests and the build only. First thing to do with eyes: `/canvas/<twin>` at 1622 px.
- **Existing cast bundles predate the `members` row**: promote shows disabled until re-cast.

## Next steps (ordered)

0. Eyes on the new shell at :8080 (after the serve restart decision above), then the step-5
   mockup review: answer the four questions, then build node LOD (`useViewport().zoom`,
   hysteresis 0.5/0.6, tile keeps its layout size, chips icon + short name, router icon
   nodes, edge labels hidden zoomed out). Basics still open from the design: Delete key,
   double-click a segment to zoom, right-click on the pane (node menus exist).
1. Eyes on the Canvas area against the largest cast twin (element budgets are measured, not
   assumed); create one blank design from `/canvas/new`; re-cast one imprint and exercise
   promote end to end.
2. Libvirt end to end is blocked by the confirm-first `vmid` → guest-handle bump (the
   `int(vmid)` readers in `config_exec/backend.py`, `planner/*`, `o2control/containment.py`,
   `api/service.py`) and by `planner/pass1_emit.py:149 ROUTER_WAN_BRIDGE="vmbr0"`.
3. Then the live libvirt two-host slice (`config/adapter.conf.libvirt.example`).
4. Live Splunk/Elastic against a Corelight-fed index (fixtures are authored, not recorded).

## Decisions pending the user

- **A8 provenance:** no write records a user today, so `request.state.user` is set and unused;
  decide where it lands (the layout side-car? the twin-config bundle?) before proxy mode goes
  live. **The Checks tab** (above) is an addition to the three-tab design.
- **Confirm-first contract bumps:** neutral op ids; `vmid`/`pve_node` → guest handle + `host`;
  `zone_type` → an L2 mode; `ADAPTER.md` + `PLATFORM.md`; a neutral router-uplink marker.
- **libvirt NAT/DHCP:** keep refusing `snat`/`dhcp`, or implement `<forward mode='nat'>`?
- **Layering moves held:** `ingest/siem/http.py` → `ingest/http.py`; hoisting the Proxmox-only
  flows out of `adapter_proxmox/rest.py`. **Edit model:** Persist of a never-applied working
  copy marks the deploy clean? keep `inert`?
- Merge of this branch and `wip/api-discovery-runs` to `master`; fate of the untracked folders.
  TRex ASTF as a "crowd" traffic generator: parked 2026-11-05, nothing in the repo.

## Gotchas

- The write policy is resolved ONCE at `create_app`; a test that flips `settings.api_token`
  after building the app must build a second app (see `test_api.py`'s masked-password test).
- `TestClient(app, client=("10.0.0.5", 1))` sets the peer the proxy CIDR check sees; the
  default `"testclient"` is not an IP and is never trusted.
- Canvas tests: the palette is the rail's `+` popover (`rail-add`), find/traffic/annotate are
  popovers too, the inspector shows ONE tab (a node click switches to Selection); helpers
  `openPalette()`, `item()`, `ann()`, `tab()` in `canvas.edit.test.tsx`.
  `data-testid="validation-state"` is the Checks tab's badge; the word inside the tab is
  `validation-word`. In jsdom React Flow never marks a chip `selected`; a drop and a drag are
  inert: test canvas state machines as hooks.
- Stage by path, never `git add -A`. `test_api_console.py::_MIRRORED_DTO_FIELDS` pins console
  DTO field sets by hand. Run the two Python `tests/` trees separately; engine with `-n auto
  -p no:cacheprovider`. `ui/`: `npm ci` only; `@xyflow/react` pinned 12.12.0.

## Environment

Unchanged from 2026-10-28: one venv at `.venv/`, node 22 for `ui/`, live state in `~/.axrange/`.
Podman + `docker.io/zeek/zeek:8.0` on this box; no libvirt, no docker, no Splunk/Elastic.
