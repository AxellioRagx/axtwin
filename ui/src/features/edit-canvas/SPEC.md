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

Decided 2026-11-07 (user, `docs/design/topology-layers.md` §7) and **built
as track 5a the same day**; the bus plane of track 4 (2026-11-06) is gone
from the code and lives in git (`42c00de`). The canvas is the **device
tree**: a layered layout (`@dagrejs/dagre`, `canvas/tree.ts`) under the
device that carries each segment, read top to bottom.

**The model.** A segment is the L2 primitive (one vnet). A host is on the
segments its `net` / `nets[]` name. A router has one leg per segment it
serves. A `kind: switch` is a device wired by `links[]` (trunk or access,
`native`, `vlans[]`, `state`); a switch carries the segments its links and
its own L2 ports name. Every segment, node and link carries `source`
(`discovered` / `asserted` / `authored` / `unconfirmed`) and a discovered
one its `confidence`.

1. **Ranks.** The Internet (drawn only on evidence of non-local peers or a
   configured uplink) over the gateway routers; routers at rank 0; switches
   by depth along their up links from a router (`treeDepths`); each segment
   a **pill** under its carrier -- the deepest switch whose link or port
   carries it (`carriersOf`), else the router with a leg on it; the hosts as
   icons on the pill. No switches (canvas-test, datacenter) degrades to
   router → pills → hosts. There is no site tint.
2. **Icons only.** Router, switch (`DeviceNode`: the switch glyph with the
   `L2` / `L3` / `FW` badge), host (the role glyph in the evidence ring, the
   `×N` badge inside it), the `+N more` fold pill, and the provenance mark.
   Routers and switches keep a short name above the icon; a pill keeps its
   name and CIDR at the left and its host-count badge at the right; the
   rail's **Labels** toggle captions host icons (all / selected / none).
3. **Hover** is the facts: a host's name, address, role, segments,
   provenance; a device's kind, image, ports, segments, provenance; a
   link's port pair, mode, VLANs and state while selected.
4. **One hue per router** (`canvas/hues.ts`, eight then grey) on its ring,
   on every device its links reach (`hueOfDevice`), on the links and the
   carry branch below it, on each pill's border and the port dot where a
   leg lands; a pill carried by no device wears its first router's hue.
5. **Edges.** A `links[]` entry is a `link` edge between two devices (or
   from a device to a host's icon), orthogonal, in the branch's hue, dashed
   when `down`, `blocked` or `unconfirmed`, clickable (the inspector's link
   row). A switch carrying a pill is a `carry` edge labelled with the
   segment. A router's legs are the config's `nets[]`: one `leg` edge per
   pill, straight from box to box, re-homed by drag as before; a leg to a
   folded segment goes to the fold, once. A multi-homed host sits on its
   first segment's pill with a `stub` to each other one. Observed flows stay
   evidence: arcs under Light-up or for the selection.
6. **Folding.** A carrier shows at most **Fan-out** pills (the rail's
   button; 2 / 4 / 8 / all, default 4) and a dashed `+N more` pill for the
   rest (`overflow:carrier:<device>`); a click expands that carrier in
   place (`expanded` carries `carrier:<device>`). Segments no device carries
   fold under `none`. A `×N` group folds its instances; a segment over the
   band budget folds its role bands. The box budget is 120 drawn boxes after
   folding (`TREE_BUDGET_BOXES`); past it, the list and the matrix.
7. **Provenance overlay** (rail button, `O`): off, a dot marks an asserted
   (accent) or authored (green) item; on, every item wears its state's badge
   (`D` discovered with its confidence, `A` asserted, `+` authored, `?`
   unconfirmed) and border (accent / green / dashed amber). Kept per user.
8. **Positions.** dagre's, then the layout side-car's pins override them:
   `host:<name>` (a host, a router or a switch) and `segment:<cidr>` are
   absolute top-lefts on this plane -- **plane epoch 6**; pins saved on the
   bus plane are read but not applied, and the page says so. A pill drags
   free; a host icon dropped on another pill moves there; "Add a segment"
   lands the new pill under the selection's carrier at dagre's next free
   slot.
9. **Taps** (topology-layers §8, **built 2026-11-07, track 5b**): every
   binding of the tap plan is an amber `tap` chip on the top edge of what it
   names -- a `segment` tap on its pill, a `boundary` tap on both pills, a
   `port` tap on the device (or, for a host's port, on the pill the port is
   on, at the left). A click opens the tap's panel; a segment tap drags onto
   another pill and moves there (`moveTap`, a `taps` change). The tap plan
   is `tap/*.yaml` read and spliced by `edit/tap.ts`; "Add tap" on a segment
   with no plan creates `tap/default.yaml` and wires `twin.yaml`'s tier.
10. **The inspector by tier** (§7.4, built with 9): a node's panel is
   *Identity & placement · topology* (name, kind, role, image, count,
   attachments, links and ports), *Traffic · tfc, live* (persona, intensity,
   schedule), *Taps · tap, live* (port taps on a device), *Members* (a ×N
   group: one row per instance, its `members[]` role override and its
   tfc persona by instance id) and *Provenance* (source and confidence;
   **Confirm** / **Mark unconfirmed** through the assertions seam as
   `confirm` / `doubt` marks keyed by the cast roster -- a node's observed
   members, a segment's observed CIDR, a link's two ports on those keys;
   **Assert: a switch** as a `device_kind` override; a link's and a tap's
   panels assert them as `link` / `tap` adds; every action is blocked with
   the reason on a twin that was not cast or on a subject the roster cannot
   key). A segment's panel carries its taps (rows, sensors, Add / Remove)
   and provenance; the link panel edits ports, mode, native, the segments
   carried and state. The *Checks* tab's **Provenance** summary counts the
   sources over segments, nodes and links; a row filters the canvas (the
   rest dims, `cv-dim`).

11. **Members** (§5, **built 2026-11-07, track 5c**, `edit/members.ts`): a
   `×N` badge with role overrides wears a dot (`×5·`); its double-click opens
   the member chips under the icon (`01 … NN`, an overridden one outlined);
   a chip's click opens the **member panel** (the instance id, its role
   override, its own persona / intensity / schedule by instance id, *Detach
   as its own group*, *Move to…* a group of the same image and segments); a
   chip dragged onto another such group moves that one member there (a
   detach, then a merge of the `×1`). The Members block picks members and
   **splits** them off as a named group (the original keeps the rest,
   re-indexed; overrides and instance bindings follow; the group binding
   copies); the node panel **merges** the group into another of the same
   image and segments (the count grows, a differing role becomes a member
   override, bindings re-key, links naming the group re-point). All four are
   topology changes. Not built: multi-select rows, the split recorded as an
   assertion keyed by the members' entity keys (§5), a zoom threshold that
   opens the chips.

Acceptance (Vitest, `ui/test/canvas.tree.test.tsx`; the inspector and the
taps in `ui/test/canvas.inspector.test.tsx`: the tap plan reader and
splices, `links[]` / `members[]` edits, the five blocks, a member's role,
the link panel's state / mode / vlans, tap chips and the segment panel's
Add / Remove, the plan created on a twin without one, the Checks summary
and its filter, and on a cast twin the confirm / doubt / assert-switch /
assert-link / assert-tap records the seam receives): the reader carries
`kind: switch`, `links[]`, `members[]` and `source`; `treeDepths`,
`carriersOf`, `linkSegments`, `pillSize`; on the switch-test shape one
router, one device with the `L2` badge, two pills (office carried by the
switch, servers by the router), the trunk link gw→switch in hue 0, the down
access link to the file server dashed and `unconfirmed`, two legs, one carry
edge, no arcs; pins absolute; on a corp shape the switches chain by depth,
each pill under its deepest carrier, the fold per carrier at fan 2 with its
`legs:` edge and its expansion, the branch in hue 0; the budget fallback past
120 boxes; on the page the device, its badge, the pills' carriers, the `×N`
badge, the overlay's dots and badges (`O` toggles), the link's panel on
click.

**Seen (2026-11-07, :8080, Playwright 1622 × 980, `scratchpad/shot.cjs`,
shots `t5-*.png`):** switch-test as above with the trunk orthogonal in red
and the down link dashed; canvas-test and corp-enterprise as router → pills →
hosts; datacenter's three hues with `fab-link` under both fabric routers and
gw's fifth segment folded; corp-twin (unconverted) as hq-mx over its first
four pills and two folds; **corp-tree** (corp-twin converted: 12 switches, 13
trunk links, an untracked fixture) as hq-mx and mx-sw over ms120 over the
six access switches, the 9k pair and the 8325 pair chained below, 28 pills
drawn and 26 folded; the largest cast twin as one router, four pills and a
`+36 more` fold. **5b (`t5b-*.png`):** switch-test's switch panel in its
blocks, its boundary tap as a chip on both pills with the tap panel on
click, corp-tree's Checks tab with the Provenance rows and the `asserted`
filter dimming the whole canvas. **5c (`t5c-members.png`):** switch-test's
`×2·` badge opened into chips `01 02`, the overridden `02` outlined, and its
member panel with the role `jump-host` and *Detach as its own group*.
`ui/test/canvas.members.test.tsx` (22): split / merge / detach / move-one
over the document, the badge dot, the chips, the member panel's Detach,
the chip drag onto another group, the split form and the merge select.

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
