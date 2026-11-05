# Console shell, canvas chrome, and where auth lives

**Status:** decided 2026-11-05 (user) on `wip/design-platforms-canvas`. Not
built. Lands on `ui/` (the shell and `features/edit-canvas`) and the API auth
dependency; no engine seam beyond one setting and one read route.
**Supersedes:** the "Write token" field in the console shell
(`ui/src/areas/shell/AppFrame.tsx`) and the console-held bearer token
(`ui/src/api/client.ts`); the fixed three-column edit layout in
`features/edit-canvas/canvas.css`; `ui/Spec.md` C1.6 as written.

Two decisions, taken together because both are about what the console is
*not* responsible for: it is not a page with a canvas in it, and it is not a
credential holder.

## 1. The canvas is the page

Measured on 2026-11-05 against `http://10.80.72.52:8080/canvas/<twin>` in a
1622 px window, next to GNS3 2.2's web UI on the same desk:

- `.main` is `max-width: 1100px; margin: 0 auto; padding: 20px 16px 40px`
  (`ui/src/theme/app.css`). The React Flow surface (`.canvas-shell`,
  `height: calc(100vh - 170px)`) sits inside that column, inside a bordered
  card, under the top nav, the subbar (Designs / New design / `<twin>`), the
  page heading (`Twin <id> · canvas`) and the status pills. A third of the
  window on each side is empty margin; the drawing gets ~60 % of the width.
- In edit mode `.canvas-page.editing` is a CSS grid
  `220px 1fr 320px` **inside** that column (`canvas.css`), so the palette
  (`Palette.tsx`) and the side panel (`SidePanel.tsx`) are full-height
  bordered boxes and the drawing is left with ~540 px. Both panels are
  scrolling lists of text rows — a file listing on the left, a log on the
  right — not tools.
- GNS3's canvas is `position: fixed; inset: 0` under one 36 px toolbar.
  Everything else — an 11-icon tool rail, a tabbed node list, the mini-map —
  floats over the drawing, translucent, collapsible, labelled on hover.
- Our toolbar is a card of labelled text inputs; our notes are five sentences
  of prose in a bordered box; segment tiles are tables of 8 px IP/role rows.
  At 205 hosts / 40 segments nothing is legible until zoomed 4×.

The gap is layout, not drawing. Decisions:

| | Decision |
|---|---|
| **Frame** | Canvas routes (`/canvas/:id`) opt out of `.main`. The React Flow container is `position: fixed; top: <navbar>; left: 0; right: 0; bottom: 0`. No subbar, no page heading, no card border, no inner padding; the dot grid runs edge to edge. `fitView` on load. |
| **Top bar** | One bar. Left: product + areas (as now). Centre: twin name, the origin pill (*cast · observed evidence* / *authored*), the state pill (*read-only* / *dirty*). Right: Save layout, Discard, and in edit mode Apply / Reset / Persist / Save-As. Nothing twin-specific lives below the bar. |
| **Left rail** | A floating vertical icon rail (~40 px, translucent, 8 px from the edge; a React Flow `<Panel position="top-left">`): select, pan, zoom in/out, fit, lock-all, light-up (traffic), find. Icon only; tooltip with name and shortcut on hover. In edit mode the rail gains **+**, which opens today's `Palette.tsx` as a popover (search + categorised chips, the arm/place state machine unchanged), never a permanent sidebar. |
| **Right inspector** | `SidePanel.tsx` becomes one floating, collapsible card (~280 px, `top-right`), tabbed: **Selection** (the clicked segment/host: address, role, image, persona, evidence — the property fields it already renders), **Hosts** (filterable list, click to centre — GNS3's "Map topology"), **Notes** (what the bottom prose box says today). Collapsed to a tab strip by default; opens on node click. The run-time warnings (segments behind "+N more", pairs not drawn, layout on another plane) become a dismissible status strip, not a panel. |
| **Node level of detail** | Two levels, switched on `useViewport().zoom`. Zoomed out (< ~0.6): a segment is a rounded tile with CIDR, host count and up to ~6 role glyphs; no rows. Zoomed in: the tile expands to host chips, each *icon + short name*; address and role in a hover card; full detail in the inspector. Routers and the gateway are icon nodes, not full-width text bars. Edge byte labels hidden below ~0.6. |
| **Surface** | One accent for selection/active, one for *lit* traffic; today orange, blue, grey and white borders compete. UI text 12–13 px everywhere; the tiles' 7–9 px goes. Mini-map keeps the rail's translucent treatment. |
| **Basics** | Marquee select, Delete, Esc clears selection, double-click a segment to zoom to it, right-click menus on node / attachment / canvas. Ctrl+Z/Y arrive with the command stack (Phase 2). |

Order: frame + surface first (a day, no model change, changes the first
impression completely); then rail + inspector (the inspector is where C3's
property editing lives, so it is groundwork, not polish); then node LOD, which
is the real design work and deserves a mockup round before code.

## 2. The console holds no credential; auth is a deployment mode of the API

Today every write needs `AXRANGE_API_TOKEN` as a bearer, and the console asks
the operator to type it into a field in the top bar for the life of the tab.
That is a shared password with API-key ergonomics: you must know the secret
to do anything, it is typed in plain view, and it names nobody.

The production shapes are known: the SPA will either be embedded in a larger
UI that has its own auth, or sit behind a reverse proxy (Caddy / nginx /
oauth2-proxy / Authelia) that terminates auth. In development there is
neither. So the console never authenticates; the API chooses, per deployment,
whom to trust.

**API: one setting, three modes** (`AXRANGE_AUTH`, comma-combinable):

| mode | writes are accepted when | notes |
|---|---|---|
| `none` | always | **dev default** when `AXRANGE_API_TOKEN` is unset. Loud startup warning that writes are unauthenticated. |
| `proxy` | the request carries an identity header (`X-Forwarded-User`; `X-Auth-Request-Email` also read) **and** arrives from a trusted upstream (`AXRANGE_TRUSTED_PROXY_CIDRS`, default loopback only) | missing header → 401. The username is recorded wherever a write already records provenance. Without the CIDR check anyone who can reach the bind sets the header, so the check is not optional. |
| `token` | `Authorization: Bearer <AXRANGE_API_TOKEN>` | the existing check, unchanged, for the CLI, automation and a directly exposed engine. Setting `AXRANGE_API_TOKEN` implies this mode as it does today. |

Plus `GET /api/whoami` → `{user, can_write, mode}` (`anonymous`, `true`,
`none` in dev), so the console can show the Save affordances and a name in the
bar without probing.

**Console:**

- Delete the Write token field, `setWriteToken` / `getWriteToken` and the
  `auth` flag in `client.ts`; writes are same-origin `fetch` with
  `credentials: "include"` so a proxy's session cookie rides along.
- Attempt the write, then react: `401` → "sign in required" with a link to
  the configured sign-in URL (`AXRANGE_SIGNIN_URL`, served in the one config
  object; e.g. `/oauth2/start?rd=<here>`); `403` → "read-only: your account
  can't write here". The console never decides ahead of time what is allowed.
- `GET /api/whoami` on load sets the read-only pill and the name.

**Guardrails kept:** CORS stays same-origin / explicit allowlist so a stray
page cannot drive writes in `none` mode on a lab network; reads stay open as
today; the ETag-guarded PUT semantics are untouched.

**Not being built:** a login page, a session store, cookies minted by the
API, user accounts or RBAC in axtwin. GNS3 2.2 trusts the network by default
and GNS3 3 grew accounts + JWT + RBAC inside the server; we take the first and
delegate the second to the proxy or the host app, which already have it.

## Where this lands

- `api/Spec.md` — seam **A8** (auth modes + `whoami`); Config paragraph.
- `ui/Spec.md` — Seam (credential rule), Config (`signin_url`), C1.6 rewritten,
  shell paragraph.
- `ui/src/features/edit-canvas/SPEC.md` — "Shell and chrome".
- `docs/OPERATIONS.md` — `AXRANGE_AUTH` beside `AXRANGE_API_TOKEN` once built.
