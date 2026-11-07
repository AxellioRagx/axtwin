# edit-canvas — the twin design canvas

## What it is

The network-design surface of the console: a pan/zoom canvas on which a twin
is drawn as segments and the hosts and routers attached to them, first to
**view** a discovered or authored twin (C2) and then to **edit** it (C3) —
place hosts on segments, add segments, duplicate sections, annotate — writing
the result back as a twin-config. It serves the **view & customize** step of
the loop.

**2026-11-07:** the twin model and this canvas's rendering are redesigned in
`docs/design/topology-layers.md` (a device tree with `kind: switch` and
`links[]`, provenance, three change kinds, members, subtree duplicate, a
dagre layout). That document supersedes the "no link objects" premise of the
next paragraph and the "Rendering rules" below; both stay here as the record
of what is built until track 5 lands.

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
  shrinks by it. **2026-11-06 (canvas review):** the state pill reads every
  tier (`te completed · tfc failed · tap completed`, red when one failed,
  with a "Re-apply <tier> from baseline" exit); the document's word is
  *applied · unpersisted* or *unapplied changes*; an Apply or Reset in
  flight shows its tier and elapsed time with a Cancel; the exits are
  grouped history | working copy | save-as behind separators, and the
  layout group (unsaved moves, Save layout, Discard) floats at the canvas's
  bottom edge (`layoutBar`), so the bar stays one row with "layout: 1
  unsaved change" showing. A note on the strip fades after 8 s; an error
  stays until dismissed.
- **Positions.** The view fits ONCE, when the first nodes are measured (or
  the session's viewport is restored), never on an edit; F / Zoom to fit is
  the explicit way. In the design view a site's or segment's computed
  position settles for the session (`CanvasPage` `settled`): a mutation
  packs only what has no position yet, so adding a node never shuffles its
  neighbours. A box without a pin never lands under a pinned one: it takes
  the nearest free spot (`placement.ts` `freeSlot`) and the site grows to
  hold it. **Round 2 (2026-11-06):** a site's box is the union of its packed
  box and every child's box with its padding, so a segment dragged out in
  any direction takes the site's edge with it rather than leaving a hole.
  Legs draw above the boxes they cross (`EDGE_Z` 3, over site 0 / segment 1
  / chip 2) but take NO pointer events (`LEG_CLASS` in `canvas.css`): a drag
  or a click on a box under a leg reaches the box. A leg is a straight line
  from the border of the node it leaves towards the centre of its segment,
  clipped at both borders (`canvas/legs.ts` `clipLine`; a router's box is its
  34 px icon, not its span), so it points at its target from whichever side
  the segment was dragged to; overlapping boxes get the centre-to-centre
  line. The pane finds the leg under the pointer (`Canvas.tsx` `legAt`,
  nearest within 8 screen px, only over the pane or a site's background: a
  chip, segment or router wins): hover shows its label, click selects it,
  right-click opens its menu. The command stack is stored per document
  (`axr-canvas-undo:<deploy id>` or `twin:<id>`) and restored only when it
  still fits the text loaded (`stackFits`: the last command's after-text,
  or the next redo's before-text, is what the server holds).
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

### Rendering rules

**Superseded by `docs/design/topology-layers.md` §7 (2026-11-07); this is
the built state of track 4 until track 5 replaces it.**

Decided 2026-11-06 (user), rewritten the same day for **track 4** after the
measurement on `/canvas/corp-twin`: the config has ONE router (`hq-mx`), no
segment attached to more than one router, yet the canvas drew thirteen
router-class nodes and 54 legs, and segments like `sfp25` appeared to hang off
two routers. The twelve extra were `kind: host, role: l2-switch` promoted by
track 3's device rule. A switch that is IN a segment is a member of it, not a
gateway to it: that rule is reverted. One correction to the brief's premise:
those switches are not promoted from cast adjacency -- corp-twin has no cast
-- they carry `nets[]` in the config (`{ segment, l2: true }` entries), so
under the model below they are multi-homed hosts (rule 4), and that is how
they draw. (Tracks 1-3's rules -- the gateway badge instead of a WAN node,
structure always and evidence on demand, the demoted site group, the labels
toggle, the fan collapse, one badge style -- are kept where they still apply
and restated below; git has their earlier wording.)

**The model, restated.** A segment is the L2 primitive (one Proxmox SDN
vnet). A host is on exactly the segments its `net` / `nets[]` name. A router
has one port per segment it serves. There are no link objects. The picture
makes "one segment, one router port" literally true: one bus per segment,
one drop per router port, nothing inferred.

1. **Segment = a horizontal bus.** A single line (`canvas/bus.ts`,
   `SegmentNode`): its name and CIDR at the left end, a host-count badge at
   the right end, no enclosing box. The hosts sit ON the bus as icons -- the
   role glyph in the evidence ring, the `×N` badge inside the icon's bounds
   -- their name, address and role on the hover card and in the inspector
   (the rail's **Labels** toggle still captions them under the line: all /
   selected / none). The bus grows with its host count and the icons wrap to
   a row above past the width budget (`BUS_HOSTS_PER_ROW` 10). The lane's
   box (the host rows over the line) is the drop target of every attach
   gesture; its right end, left of the badge, is the PORT REGION where no
   host sits and every drop lands. Compact (zoomed out), the icons give way
   to a glyph row. The placement is `canvas/placement.ts`; the lifted engine
   still decides the site groups, each group's budgeted set (`+N more` is a
   lane at the bottom of the site) and the arcs.
2. **Routers above, one hue each, one vertical drop per port.** The routers
   sit in a row above the sites (packed by their drawn boxes; a site's lanes
   are at least as wide as the row of routers serving it, so they share one
   row). The k-th router of the twin wears the k-th hue of an eight-hue
   palette (`canvas/hues.ts`, grey past eight) on its icon ring, on its
   legs, and as a small dot at the left end of every bus it serves. A leg
   leaves the icon's bottom and drops onto the bus's line in the site's port
   region -- one drop column per source into the site (`canvas/wiring.ts`),
   by the sources' x, so drops never cross -- routed orthogonally through
   the gutters (`canvas/route.ts`), never through a host row, a label or a
   badge; it ends in a port dot on the line. Past N buses in one site (the
   rail's **Fan-out** button, 2 / 4 / 8 / all, default 4, `axr-canvas-fan`)
   the router draws ONE trunk to the site's top edge, badged `N segments`,
   and a rail down the port column to each bus (`canvas/fan.ts`), in its
   hue; selecting the router, or a click on the site's tint, expands the
   fan. The gateway badge (rule 1 of 2026-11-06) and the Internet node's
   rule are unchanged.
3. **Switches are members, not gateways.** A `kind: host` whose role is in
   the switch class (`model/roles.ts` `SWITCH_ROLES`: `l2-switch`,
   `l3-switch`, `router`, `firewall` -- the API enumerates no role
   vocabulary, so the set is the canvas's) is a host on its bus with the
   switch glyph and a role badge (`L2` / `L3` / `FW`). Observed adjacency is
   EVIDENCE only, never a structural leg: (a) the inspector's node panel
   reads *observed on N segments: <list> -- Attach…* for a cast twin's
   switch, from the cast roster's members and observed networks and the
   export's edges (`canvas/evidence.ts` `observedSegments`), and Attach
   writes those segments into its `nets[]` (a topology change, a re-plan);
   (b) under Light-up or on selection its observed flows draw as the dashed
   arcs every host's do. An authored twin has nothing observed and shows no
   row.
4. **A multi-homed host** (`nets[]` > 1) is one icon, placed on the LOWEST
   of its buses in its first segment's site, with a stub from its icon to
   each other bus it is on (`stub:` edges, dashed, a hollow port dot) -- the
   only legitimate "two paths" rendering. corp-twin's `ms120` sits on
   `vlan20` with nine stubs; its hover card lists every segment.
5. **The site group** is a faint band behind its buses with a small label
   and the collapse caret; no border, no title bar (`.cv-site`, not a
   selection target; the caret folds it to its label). The sites fill rows
   near a window's shape (`chooseSitesPerRow`).
6. **Edit gestures follow the metaphor.** A palette item dropped or
   click-placed ON a bus attaches there (`segmentAt` reads the lane's box).
   A router's drop dragged from one bus to another re-homes that leg
   (`Canvas.tsx` `legDrag`: the shell catches the press over a leg by the
   pane's hit-test, follows the pointer with a rubber line in the router's
   hue, highlights the bus under it, and on release `rehome` in
   `edit/document.ts` rewrites that `nets[]` entry with no address; Esc
   cancels). **Add segment** inserts the new bus under the selected site
   (the site of the bus asked beside, else of the selection, else of the
   first segment: the next free /24 under that prefix). A bus dragged within
   its site is reordered, never dropped free: its `segment:<cidr>` pin in
   the layout side-car is its y RANK in the site (`{ x: 0, y: rank }`,
   `Canvas.tsx` `rankAt`, `placement.ts` `orderByRank`); `host:` and
   `site:` pins stay absolute top-lefts. In the design view a bus's rank
   settles for the session like a site's position, so a new segment lands
   at the end of its site. Plane epoch 5: pins saved on the tile plane are
   read but not applied, and the page says so.

Acceptance (Vitest, `ui/test/canvas.bus.test.tsx`; the geometry is checked
over the computed layout, since jsdom draws no edge): a corp-twin-shaped
design (one router into two sites, two `l2-switch` hosts on several buses, a
workstation on two) renders exactly one router node, `leg` edges only from
routers to their `nets[]`, every switch an icon on a bus with zero legs, no
bus with two drops from one router, every bus's dots exactly the routers the
config puts on it; a three-router design wears three hues and a shared bus's
dots name both routers in those hues; canvas-test's shape puts `gw` above two
buses with two drops landing in the port region; with every leg drawn and
with the fans collapsed, no run crosses a host row, a label or a badge, every
drop leaves the router's bottom and lands on the line from above, two sources
into one site take different columns; the multi-homed hosts sit on their
lowest bus with the right stubs; `observedSegments` and the inspector's
Attach…; `rehome`, `rankAt` and `orderByRank`; Add segment under the selected
bus's site; the attach gesture; the badge and the labels icon; the Hosts
list. The grid router's arrival from below is tested on its own.

**Manual checklist, track 4, 2026-11-06** (the running build on :8080,
Playwright 1622 × 980, `scratchpad/t4.cjs`, the probe re-running the
host-row / label / badge crossing check over every drawn leg's `data-pts`;
read by eye on the shots):

1. `/canvas/corp-twin` (design view): 5 sites in two rows, 26 lanes, ONE
   router node (`hq-mx`, red, 32 legs), 70 icons, 8 switch-class icons with
   the `L2` badge, each a multi-homed host on its lowest bus; 35 drawn edges
   (3 fans badged `10 segments · 4 behind +N more` / `11 · 5` / `10 · 4`,
   17 rail drops, 1 leg, 14 stubs), all routed, zero crossings of any host
   row, label or badge; a red dot at the left end of every bus `hq-mx`
   serves, none on the switches' buses; `hq-mx` selected expands to 18 legs
   + 14 stubs, zero crossings; Fan-out: all the same. `ms120`'s card lists
   its ten segments; its panel says a switch is a member of its segments and
   lists the ten attachments; no observed-adjacency row (no cast). **Seen.**
2. `/canvas/corp-enterprise`: ONE router (`gw`, red) above four buses, four
   drops, four red dots; the fit stops at zoom 1.5. (The brief called it
   "several routers": its config has one; the multi-router check is item 3.)
   **Seen.**
3. `/canvas/datacenter` (three routers): `fab-r1` blue, `fab-r2` green, `gw`
   red, side by side above the one site; `fab-link`'s left end carries the
   blue and the green dot, every other bus the red one; `gw`'s five legs
   collapse to one trunk and a red rail with a port dot on each of its
   buses; the two single legs land with their own dots. **Seen.**
4. `/canvas/canvas-test`: `gw` above two buses with two red drops, each
   landing in the port region; the workstation's `×2` inside its icon; the
   fit at 1.5, not 2.5. **Seen.**
5. `/canvas/pit-395a61f04e-1780405200` (the largest cast twin, design
   view, 40 segments, six drawn): one site, one fan `40 segments · 34
   behind +N more`, the rail down to each lane and the `+34 more` lane; 45
   icons; zero crossings. **Seen.**
6. datacenter, rule 6 by hand: `fab-r1`'s drop dragged from `fab-link` onto
   `edge` re-homes it (the rubber line in blue, `edge` highlighted, the
   document dirty, Ctrl+Z puts it back); the first bus dragged below the
   second takes rank 1 and the layout bar counts one unsaved change.
   **Seen** -- see the handoff for the probe's record.
7. Known, left: two corp-twin segments with ONE CIDR (`mx-vlan20` and
   `vlan20`, both 10.180.16.0/20) share one host list, keyed by CIDR as the
   lifted graph keys them, so the second bus shows the count and no icons
   -- as the tiles did; a fan trunk's badge sits at its polyline's midpoint,
   which on a long trunk is over another site.

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
