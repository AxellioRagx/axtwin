# edit-canvas — the twin design canvas

## What it is

The network-design surface of the console: a pan/zoom canvas on which a twin
is drawn as segments and the hosts and routers attached to them, first to
**view** a discovered or authored twin (C2) and then to **edit** it (C3) —
place hosts on segments, add segments, duplicate sections, annotate — writing
the result back as a twin-config. It serves the **view & customize** step of
the loop.

It is a canvas over axRange's own model, not an emulator's. A twin has no link
objects: a node attaches to one or more L2 segments (`net` / `nets[]`), and
the API already serves that as a bipartite host↔segment graph. The editing
gesture is therefore "drop a host onto a segment", never "wire interface to
interface". Exporters to emulator formats (containerlab, GNS3, CORE) are a
separate, later feature over the expanded IR and are not this canvas.

## Seam

A consumer of the control-plane API only (`ui/Spec.md`); it defines no
endpoint and holds no orchestration logic. Mounted as `/canvas/:id` in the
console's **Canvas** area (`ui/src/areas/canvas/`: the top-menu entry whose
drop-down lists the catalog's twins, the `/canvas` designs page, and
`/canvas/new`, which writes a blank or copied twin-config through A3 and opens
it); `/twins/:id/canvas` reaches the same page from a twin's page.

| Reads | Route |
|---|---|
| authored twin graph | `GET /api/scenarios/{id}/topology` (`TopologyDTO`: nodes typed `host\|router\|switch\|network`, edges host→network with `ip`) |
| cast twin graph with observed evidence | `GET /api/twins/{id}/topology` (`TopologyExportDTO`: weighted `src/dst/proto/bytes/flows/evidence_class` edges, census) |
| palette | `GET /api/twin-configs` images and node groups, `GET /api/personas` |
| positions | the layout document (`axr-layout`), extended with `host:<name>` pins beside the existing `site:` and `segment:` pins |

| Writes | Route |
|---|---|
| undeployed config: Save / Save-As | A3 `POST /api/twin-configs`, `PUT /api/twin-configs/{id}` |
| deployed twin: working copy, Apply / Reset / Persist | A2 `GET\|PUT /api/deploys/{id}/config`, `config/apply`, `config/reset` |
| every mutation, before Apply or Save is enabled | `POST /api/validate` |
| positions and annotations | `PUT` of the layout document, ETag-guarded, on an explicit **Save layout** (or with a document Save / Persist) -- never a write per drag |

The twin config never carries presentation. Positions, free text and shapes
live only in the layout side-car (the pattern containerlab's TopoViewer uses
with its annotations file); shapes are stored as raw positioned SVG snippets
there, the way GNS3 stores drawings.

**Stack:** `@xyflow/react` (React Flow, MIT) is the single dependency added to
`ui/`; it supplies pan/zoom, selection, drag, connection handles, a minimap
and DOM-rendered custom nodes. Segments render as compound (group) nodes,
hosts as chips with role badge and evidence ring, routers as nodes spanning
the segments they join, observed traffic as arcs with `log(bytes)` width. The
frozen console's pure `o2/console/src/layout/` and `model/` modules are lifted
for deterministic initial placement and the evidence/zoom model; its `views/`
and `edit/` (which edit imprint assertions, not the twin config) are not.
React Flow ships neither auto-layout nor undo/redo: initial layout comes from
the lifted tiered engine, undo/redo from a command stack over the working copy.

**Modes.** Edit mode when the twin is undeployed or the working copy is open;
view mode, canvas locked except annotations, while its deploy is running —
the same two-mode discipline CORE's GUI uses.

**Two kinds of edit, classified by the API, shown by the canvas.** An
attribute edit (persona, intensity, schedule, cadence, tap binding, a role
change that keeps the image) applies live through the tier's playout; a
topology edit (segment, node, attachment, count, image) requires a re-plan.
The canvas does not decide this: it reads the per-change classification A2
reports and shows each pending edit's blast radius — *live* or *re-plan* —
before Apply, and Apply is enabled only when every pending change is live.
A working copy with a topology change offers Persist / Save-As and a
"redeploy from baseline" action instead.

**Two layers, one rule.** The canvas edits the twin-config (the cast). When
an operator is correcting discovery rather than designing — a host's role,
two segments that are one VLAN — the edit is offered a **promote to
assertion** action that writes it through the imprint's assertions seam,
re-casts, and re-opens the working copy; the canvas never writes assertions
directly and never edits the imprint in place. A twin designed from scratch
starts from the blank twin-config `/canvas/new` writes -- one empty segment
and no nodes, since the engine refuses a config with no segment at all -- and
has no imprint and no promote action.

## Feature set

The interaction set EVE-NG and GNS3 operators expect, reimplemented at the
concept level over React Flow (**RF** = built-in; **RF+** = a custom node or
edge on RF primitives; **app** = application code; **API** = needs a seam).
The two incumbents' editing primitives are table stakes; what reads as
sleek is undo/redo, a side panel instead of modals, and state visible on the
canvas — the first two are what neither incumbent documents.

| Area | Items | Tag |
|---|---|---|
| Palette & placement | searchable, categorised palette of images, personas and node groups (this twin's own; other twins' groups under a folded "From other twins"), with an availability cue per image; ONE placement rule (2026-11-06, the GNS3 pattern): a click arms the item, the next click on a segment places it (a router: a segment for its first leg or an empty spot for none), Esc disarms; drag-drop onto a segment; a segment is added at once, a persona binds to the selected node group at once; count field for N copies with `{name}-{n}` naming | app over RF `onDrop` |
| | snap-to-grid with Alt bypass, grid toggle, align selection H/V, helper lines while dragging | RF, align/helpers app |
| | layout menu: preset (the lifted tiered engine), hierarchical, force, radial | app |
| Attaching (the twin's "linking") | drag a host onto a segment or from a host's handle to a segment bus; sticky attach mode with Esc; address auto-allocated and shown as an editable end label; second leg on a router or multi-homed host | RF handles + app state machine |
| | per-attachment label modes (all / selected / hidden); straight, bezier and orthogonal styles with curvature; colour, width, dash; state colours | RF, RF+ for labels and state |
| | right-click on an attachment: capture, suspend, style, detach; impairment badge once the IR has an impairment concept | app; impairment API |
| Node visuals | SVG icon sets and custom upload; resize; run-state tint and badges (locked, capturing, dirty); label style and position; hover card with address, role, evidence ring | RF+ |
| | segments as compound nodes with colour and transparency; tag-driven auto-sizing zones (site, VLAN); z-order | RF groups, smart zones app |
| Annotations | inline text with a typography dialog; rect, ellipse, line, Bézier with rotation; image or wallpaper at low z; per-item lock; hide-all; stored as positioned SVG in the layout side-car | app node types over RF resizer |
| Ergonomics | marquee and Shift/Ctrl multi-select, delete, select-all; Ctrl+C/V/D and Ctrl+Z/Y over the command stack; state-dependent right-click menus on node, attachment, canvas, selection; persistent property side panel, never a modal; find node and centre; zoom-to-fit, mini-map, zoom controls; dark/light theme; lock-all synced from the deploy state; viewport and positions restored from the side-car | RF for select/zoom/theme/lock; command stack, menus, panel, find app |
| Run-time | per-node and twin-wide power actions with live progress; click-to-open console in a tabbed terminal with pop-out; **traffic overlay**: a filter over the observed edges (`proto`, port, bytes, peer) makes matching attachments glow in a chosen colour for a timeout, rendered from `TopologyExportDTO` evidence with no backend change; live counters at both ends of an attachment; PNG/SVG export | overlay RF+; console, power API |
| Configurability | per-user console, theme, grid and label defaults; twin properties (description, params, containment claim) in the panel; template forms with `{name}-{n}` and interface patterns; per-node overrides; whole-twin text export beside the layout side-car | app |

Phase 1 (read-only, **built 2026-10-30**) covers the RF rows and the traffic
overlay. Phase 2 (**built 2026-10-30**) adds the palette (searchable,
categorised, N copies as `{name}-{n}`, drag-drop and click-to-place,
snap-to-grid with Alt bypass, grid toggle, align H/V), attaching (drop onto
a segment, a handle drag to the segment bus, the sticky attach mode with
Esc, addresses allocated by the lifted `allocate.ts`, a second leg, labels,
right-click detach), the command stack (Ctrl+Z/Y; undo restores the
pre-edit text byte-for-byte because every edit is a line splice over the
YAML text -- `edit/yaml.ts`, no YAML library is in the lock), the side
panel (never a modal: node, segment, attachment, annotation and twin
properties, persona / intensity / schedule through the traffic profile,
validation state, the pending changes with their *live* / *re-plan*
classification), the exits (Save / Save-As; Apply only when every pending
change is live, Reset, Persist, redeploy from baseline), promote to
assertion (a cast twin's role change or segment merge through
`PUT /api/imprints/{pit}/assertions` and a re-cast), and annotations (text,
rect, ellipse, line, per-item lock, hide-all). Not in Phase 2: the layout
menu's hierarchical / force / radial presets, helper lines while dragging,
per-attachment styles, icon sets and node resize, Ctrl+C/V/D, PNG/SVG
export, the run-time row. **2026-10-31:** every mutation is validated by
`POST /api/validate {document}` (debounced; Save, Save-As, Persist and Apply
open only on a clean answer for the files on screen, the findings shown with
rule and location; the canvas's structural check stays the instant hint and
the working-copy PUT's refusal the backstop), and annotations live in the
layout side-car's `annotations` list (positioned SVG snippets with lock and
hidden flags, written with the pins, a set an earlier build kept in
localStorage migrated once; the server refuses script,
foreignObject, `on*=` and href by name, and the canvas escapes every typed
character that could spell one). Promote-to-assertion is keyed by the cast
roster (`GET /api/twins/{id}/cast`): a twin-config node name is a cast
*group* name (`role-band-segment-rung`) and a segment's `cidr` the deployed,
possibly relocated network, and the assertions seam resolves neither; the
roster's `members` (each group's observed entity keys, off the sidecar's
`count` row) and `observed_cidr` (the relocation's replaced value) are what
the records carry, one override per member. A node or segment the roster
cannot key -- hand-added, synthetic, a router, a cast older than the row --
shows the promote action disabled with that reason. Fault injection and
attachment impairments wait for an IR concept. **The layout side-car is a
draft** (user rule 2026-10-31, `edit/useLayoutDraft.ts`): every drag, align,
placement and annotation change lands on the canvas and counts as unsaved
(the header says how many); the document is written only by **Save layout**
-- one PUT of every pin and annotation as they stand, the etag as the
precondition -- or alongside a document Save / Persist; **Discard** returns
to the layout as last loaded or saved; a 412 keeps the draft, takes the
fresh etag and says another console saved meanwhile; leaving the page with
unsaved moves asks first. An earlier build wrote the side-car on every drag.

## Config

None of its own. The telemetry base URL and everything else come from
`ui/Spec.md`'s single served config; the canvas reads what the API enumerates
and names no image, persona or platform in code.

## Acceptance

`ui/Spec.md`'s C2 and C3 tests are the acceptance; this feature adds no
criteria of its own beyond them.

- **Automated (Vitest, the node gate):** C2 — the authored and cast graphs
  render with every segment, host and router present and positions stable
  across two renders of the same layout document. C3.1–C3.6 as written in
  `ui/Spec.md`: add node and segment, duplicate a section onto a segment with
  segment-appropriate addressing (the lifted `allocate.ts`), Save-As from both
  states, invalid edits blocked with the validator's reason, dirty marker on a
  deployed twin, Reset to baseline across two open consoles. Undo returns the
  working copy byte-equivalent to the pre-edit document. A persona change on
  a deployed twin shows *live* and Apply is enabled; adding a segment shows
  *re-plan* and Apply is disabled while Persist and Save-As stay enabled;
  promote-to-assertion on a cast twin re-opens a working copy whose
  baseline carries the change.
- **Live (eyes):** the largest cast twin in the catalog renders and pans at
  interactive rate; the element budgets below are measured, not assumed.

## Routers

A router joins segments rather than living on one (user, 2026-11-05), so it
places without a segment: the palette's **Router** item (image: what the
catalog's routers use, this twin's first; otherwise `router`, to be picked in
the inspector) lands on the pane with a click or a drop, or with a selected
or dropped-on segment as its first leg; the pane menu offers *Place Router
here* while one is armed. A group copied from a router stays a router
(`PaletteItem.nodeKind`). Its legs are the attach gesture or the node
panel's *Attach to segment*. An unattached router is a **warning** (CV-024,
`validate.ts`), not an error, because every edit must leave the document
free of errors and an error here would make a router impossible to place.

## Shell and chrome

Decided 2026-11-05 after measuring the live canvas against GNS3's web UI
(`docs/design/ui-shell-and-auth.md` has the measurements). The canvas is the
page, not a card in one:

- **Frame.** `/canvas/:id` opts out of `.main`'s 1100 px column. The React
  Flow container is fixed to the viewport under the top bar; no subbar, no
  page heading, no card border or padding; the dot grid runs edge to edge;
  `fitView` on load. **Built 2026-11-05**: `AppFrame` renders a canvas
  route into `main.canvas-main` (the frame is exactly the window) and the
  page is `position: absolute; inset: 0`; a saved viewport is restored,
  otherwise `fitView`.
- **Top bar.** One bar carries the twin name, the origin pill (cast /
  authored), the state pill (read-only / dirty) and the exits (Save layout,
  Discard; in edit mode Apply / Reset / Persist / Save-As). Nothing
  twin-specific sits below it. **Built 2026-11-05**: `CanvasPage` renders
  them into the bar's middle through `areas/shell/topbar.tsx`
  (`TopbarPortal`); the title is the twin id, linking its summary page.
  Load / save errors and notes float as a strip over the drawing's top edge
  (`.cv-strip`). The plane meets a window's shape: a site's tile columns are
  the engine's count at most and otherwise chosen so the one row of sites
  comes nearest `PLANE_ASPECT_TARGET` 1.8 (`placement.ts` `chooseSiteCols`;
  the chip-sized tiles are twice the engine's width, and at three across a
  five-site cast was 4.5 : 1 with a fitView in the middle third of the
  window). Plane epoch 2: pins saved before 2026-11-05 are read but not
  applied, and the page says so. The bar is one row at 1622 px on a cast twin's long id
  (measured headless): one pill carries origin and mode, Undo / Redo are
  glyph buttons, Save-as is an 8 em field, the title ellipsizes past 240 px;
  below about 1500 px the exits wrap to a second row and the drawing
  shrinks by it.
- **Left rail.** A floating vertical icon rail (`<Panel position="top-left">`,
  ~40 px, translucent): select, pan, zoom in/out, fit, lock-all, light-up,
  find; tooltip with name and shortcut on hover. Edit mode adds **+**, which
  opens `Palette.tsx` as a popover, never a permanent sidebar; the
  `220px 1fr 320px` grid in `canvas.css` goes. **Built 2026-11-05**
  (`chrome.tsx`, `icons.tsx`): the rail's view tools (V/H select or pan —
  select drags a marquee, pan drags the plane; +/−/F zoom; L lock; T
  light-up; / find) and, in edit mode, the "Edit tools" group (+ palette,
  attach, snap, grid, align H/V, annotate). Find, traffic, the palette and
  the annotation tools are popovers beside the rail, one open at a time,
  Esc closes. React Flow's `<Controls>` is gone; the mini-map sits
  bottom-right in the rail's treatment.
- **Right inspector.** `SidePanel.tsx` becomes one floating, collapsible,
  tabbed card (~280 px):
  *Selection* (the clicked node's address, role, image, persona, evidence —
  where C3's property editing lives), *Hosts* (filterable list, click to
  centre), *Notes*. Collapsed to a tab strip until a node is clicked. The
  run-time warnings become a dismissible status strip. **Built 2026-11-05**:
  `Inspector` in `chrome.tsx`, 300 px, `top-right`; a node click opens
  *Selection* (in read-only mode the graph's facts for the host, router or
  segment; in edit mode `SidePanel.tsx`'s panels). A fourth tab, *Checks*,
  holds what the old panel also carried — the validation state and, on a
  deployed twin, the pending changes — and wears the validation word
  (`valid` / `invalid` / `validating`) as its badge so the gate reads at a
  glance. The blocked-edit reason and the API's refusal of the document
  moved to the canvas's message strip. The placement warnings (segments
  behind "+N more", pairs not drawn) are the dismissible strip bottom-left;
  the page's notes (authored twin, cast omissions, layout on another plane,
  annotation count) are the *Notes* tab. The inspector's width is per tab
  (300 px; Checks 460 px, whose rule ids, locations and change paths wrap
  into noise narrower) until the operator drags its left edge; a double-click
  on the edge restores the tab's width; clamped to 240 px … 60 % of the window.
- **Level of detail.** Switched on `useViewport().zoom`. Below ~0.6 a segment
  is a tile with CIDR, host count and up to ~6 role glyphs, and edge byte
  labels are hidden; above it the tile expands to host chips (icon + short
  name; address and role on hover; detail in the inspector). Routers and the
  gateway are icon nodes. **Built 2026-11-05** (`model/detail.ts`, user
  decision: 0.6 with a rail toggle): expand at zoom ≥ 0.6, collapse below
  0.5, hold between (hysteresis); the rail's **Node detail** button (D)
  cycles auto → full → compact. Compact: the chips and rungs are React Flow
  `hidden` nodes (state keeps them, so a selection survives a zoom), the
  tile's header grows and a glyph row (`segment-glyphs`, up to six role
  glyphs by count, a `+N` fold, a ghost glyph) sits beneath it, arc labels
  are off, routers and the chrome drop their sub-line. Full: a chip is the
  role glyph inside the evidence ring plus the short name (`shortName`: a
  `hostname:` key's first label, an `address:` key's `.c.d`, a design node's
  group name); the address and role ride as `data-address` / `data-role` and
  on the hover card. A tile keeps its layout size at both levels, so no pin
  moves (the chip and tile constants are in the plane stamp: changing them
  drops every saved layout, so sizes are tuned inside the boxes). A compact
  tile's header text keeps a constant screen size through `--inv` (1/zoom,
  capped at 2.5, written to the shell on every viewport change without a
  render); the CIDR gives way first; the glyph row (54 × 32 canvas px per
  glyph) shows as many as the tile holds under the header
  (`glyphCapacity`), the rest fold into `+N`. A full chip's label is 15 px
  canvas (9 px on screen at 0.6). Role glyphs are `RoleIcon` in
  `icons.tsx`, keyed by the badge label.
  A router keeps its `DEVICE_W × DEVICE_H` slot (the band's spacing and the
  saved pins depend on it); the icon sits on the slot's bottom edge, where the
  legs' source handle is, with the name and leg count above it. The Internet
  pill and the WAN edge keep their slots (widened to 300 / 360 px; they are
  centred over the content and pin nothing): the icon box is the slot's
  full-height centre, so the trunk's handles touch it, and a short label
  hangs to its right (`Gateway gw01` / `Egress (inferred)`, `12 crossing ·
  1.2 GB`, `41 non-local`), ellipsized at the slot's edge with the full
  wording in the node's title.
- **Surface.** One accent for selection, one for lit traffic; UI text
  12–13 px; mini-map styled like the rail. **Started 2026-11-05**: the
  armed state and selection share `--accent`, routers lose the ink border,
  no in-chip text under 11 px, every floating box shares one translucent
  treatment (`--float-bg`, `--float-shadow`); the tiles' own sizes wait for
  the level-of-detail step.
- **Basics now, not Phase 2.** Marquee select, Delete, Esc, double-click a
  segment to zoom to it, right-click menus. Undo/redo waits for the command
  stack. **Built 2026-11-05**: the marquee came with the rail's select tool;
  Delete / Backspace removes the selection (nodes, segments, a leg; an
  annotation even in view mode; never from inside a field, never while the
  deploy locks the canvas); double-click a segment or a site fits the view to
  it (`maxZoom` 1.5, so the tile expands past the detail threshold; the shell
  records `data-zoom-target`); right-click on the empty pane opens a menu
  (Zoom to fit, Lock/Unlock all, Find a node, and in edit mode Add a segment
  and Annotate here at the pointer). Node, leg and segment menus as before.

Order: frame + surface, then rail + inspector, then level of detail (mockups
first) — all three built 2026-11-05, the basics the same evening.

## Notes

- Prerequisites are the A3 then A2 seams and the `host:` pin kind in the
  layout document. A canvas with nothing to save into repeats the frozen
  console's fate; read-only C2 may land first, editing waits for A3.
- Element budgets: the frozen console's tile/band/matrix caps stay as the
  guard against DOM-node blow-up; beyond them the observed census falls back
  to the matrix view, not to a denser canvas.
- Do not import emulator UI code: EVE-NG's UI is proprietary, GNS3's is
  GPLv3; both are incompatible with this repository's license. Their UX ideas
  (palette, interface picker, drawings, edit/run lock) are reimplemented, not
  copied.
- The old console's edit path writes imprint assertions; this feature writes
  twin-configs. Keep the two apart.
