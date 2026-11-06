# Session State — the cross-session handoff

<!-- REPLACED at every handoff, never appended (`/handoff`); checked against the repo at every
start (`/catchup`). Keep it under 8 KB and about NOW. A stale handoff is worse than none,
because it will be believed. -->

**Last updated:** 2026-11-06 00:45 · **Branch:** `wip/design-platforms-canvas`, six commits
past `c5d6856` (39f02a6..c7ca5e0), **pushed** to origin on 2026-11-06 at the user's request.
Rule: commit each track as it lands, staged by path; push only when asked.

## What this session did

The 2026-11-05 design built end to end (A8 auth; canvas = the page; rail + inspector; node
level of detail; the basics; routers place without a segment — all pushed), then, on the
user's "fix them all autonomously" and "design a twin from scratch and deploy it on dev45":

1. `39f02a6` **quiet legs, Space to pan, three-octet short names, narrow compact tiles** —
   one dashed `legs:` edge per router and folded `+N more` group; a leg's label only on
   hover/selection; hold Space to pan (trackpads); `.80.20.4`; narrow tiles wrap their name.
2. `39998ca` a hovered chip's card rises above its neighbours.
3. `98dbfb7` **a router's leg is never pinned**: `attach` and placing on a segment gave the
   router the next free host address; the engine then had nothing at the gateway
   (network+1). Found designing the twin; the deploy shows `gw` at .1 on both segments.
4. `a89fef7` **discovery: the cast keeps IPv6 segments out of the twin** (`_segment_of`,
   one caveat naming them; a ULA /64 passed `is_provisionable` and axRange refuses non-IPv4
   CIDRs — corp-apcn-reader-20261028 could not be saved). Discovery suite green.
5. `391eadb` **`catalog/twins/canvas-test`** — the first twin designed on the canvas through
   the browser (Playwright drove the real UI: new design, palette, inspector, Save), with
   bundle-local scripted personas and a boundary tap to XPress-SW-A; README has the sequence,
   the capture proof and the teardown.

**The serve** (:8080, pid 1029254, `none` mode) was restarted 00:17 with
`AXRANGE_IMAGES_ROOT=/home/rocky/axr-product/deploy-dev45`: without an images root every
design fails REF-120 and the console cannot Save. Killed by pid, not pattern (a pattern also
matched a shell wrapper the first time).

## Standing on dev45 — left up for the user

- te `canvas-test-f919dae1` (48 ops, 4 VMs: gw router, corp-workstation01/02, file-server),
  tap `tap-canvas-test-f919dae1-1a070fcc` (office↔servers boundary → XPress-SW-A), tfc
  `tfc-canvas-test-f919dae1-bb83ca92` (office-workstation, file-share). DB `~/.axrange/state.db`.
- **Capture proven**: the sink's `gre-cap` RX counter 21 → 233 in minutes, and `tcpdump` on
  dev45 of the GRE to 10.255.251.21 shows `10.70.10.2/.3 → 10.70.20.10:445/80` SYNs and
  the server's RSTs (nothing listens: scripted personas, no `install:` from the canvas).
  Teardown: `axrange te destroy canvas-test-f919dae1 --allow-destroy --db ~/.axrange/state.db`.

## Current state

- **Tests:** ui typecheck clean, **179 tests**, build clean, `ui/dist` live; discovery full
  suite green after the cast change; engine untouched this session (last: 4180 passed, the
  two pre-existing failures from the untracked `catalog/twins/{test2,test-twin-2,
  corp-apcn-reader-20261028}` folders). Untracked also: `catalog/twins/mylab` (not mine).
- **Seen with eyes** (Playwright chromium, 1622 × 980): every twin's fit view, compact and
  full, the dark theme, a dense Checks tab, the Router item live, Delete, double-click, the
  pane menu, the deployed twin's working-copy view with its exits. Recipe in memory.

## Issues left (the user's list, worked)

- **Open, small:** a design tile says "1 host" for a group with `count: 2` (groups, not
  instances); the top bar wraps below ~1500 px; compact tiles keep their full-detail height
  (corp-twin's 42-host tiles stand empty at fit zoom 0.5); the canvas cannot author a
  node's `install:` / `requires` / `resources` (palette group items drop them), so a
  canvas-designed file server runs no smbd; `POST /api/validate` answers 400 (not a
  finding) for a router between placement and its first leg.
- **Yours to decide:** the serve's auth mode (`none` on 0.0.0.0 accepts every write; set the
  token or `AXRANGE_AUTH=proxy`); where A8 provenance lands (`request.state.user` is unused);
  the Checks tab (an addition to the three-tab design); the untracked catalog folders and the
  two engine failures they cause; merge to `master`; the mockup's Router/LOD follow-ups.
- **Confirmed fine:** a leg-less router is a canvas warning and the engine's STR-070 refuses
  it at Save; dark theme; dense Checks at 460 px.

## Next steps

0. Re-cast corp-apcn-reader-20261028 so the IPv6 segment leaves it.
1. The small open items above, then the libvirt path (blocked by the confirm-first `vmid` →
   guest-handle bump and `planner/pass1_emit.py:149 ROUTER_WAN_BRIDGE="vmbr0"`), then live
   Splunk/Elastic against a Corelight-fed index.

## Gotchas

- Write policy resolved ONCE at `create_app`; tests flipping `settings.api_token` build a
  second app. `TestClient(app, client=("10.0.0.5", 1))` sets the peer for the CIDR check.
- Chip/tile constants are hashed into the plane stamp and a packing-rule change bumps its
  epoch (`canvas/plane.ts`, now 2): either drops every saved layout. Compact header text is
  screen-constant via `--inv` (1/zoom ≤ 2.5).
- Canvas tests: the palette is the rail's `+` popover, find/traffic/annotate are popovers,
  the inspector shows ONE tab; helpers `openPalette()`, `item()`, `ann()`, `tab()`. Every
  edit must leave the document free of ERRORS (warnings pass), so a "not yet" rule is a
  warning. jsdom's zoom is 1; pin a level with `rail-detail`.
- `tap`/`tfc deploy` take `--twin <id> --db`, no `--images`; `te inventory` wants the deploy
  id. Library endpoint personas `require` roles (DC, DNS, web-tier): a small twin needs
  bundle-local ones in `<bundle>/personas/`.
- Stage by path, never `git add -A`. Engine tests `-n auto -p no:cacheprovider`; `ui/`: `npm
  ci` only.

## Environment

One venv at `.venv/`, node 22 for `ui/`, live state in `~/.axrange/`. Playwright 1.61 at
`/home/rocky/fx-cap/mgmtui/node_modules/playwright` + `~/.cache/ms-playwright/chromium-1228`.
dev45 API 10.80.76.108:8006, SSH root (default key); XPress-SW-A `fxadmin@10.80.72.43`.
