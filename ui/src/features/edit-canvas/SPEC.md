# edit-canvas — the twin design canvas

## What it is

The network-design surface of the console: a pan/zoom canvas on which a twin
is drawn as a device tree -- routers, switches (real or implied) and the hosts
under them, a segment drawn as the switch that carries it -- first to
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

### Segments drawn as switches (2026-11-11, topology-layers §9 D15)

A UI-only change: the model, schema, validator, engine and deploy mapping are
untouched; a segment stays the L2 primitive a host's `net` names. The drawing
is one uniform tree, router → switch (real | implied) → hosts (Rendering rules
1–9 below).

**Drop rules** (palette drop, or an armed item clicked; `Canvas.tsx`
`placeTarget` / `placeOnDevice`, `edit/switches.ts`):

- on an **implied switch**: the host joins that segment (a peer of its hosts);
- on a **real switch** of one segment: that segment; of several: the native
  VLAN of its uplink trunk (one to a router first), else its most-populated
  segment (`switchSegment`); the host's inspector then offers the **VLAN
  picker** (`hostSwitch`, a `moveTo` re-addressed in the chosen segment);
  a switch that carries nothing refuses, with the reason;
- on a **router**: a new segment, the next free /24 in the router's site
  (`newSegmentOnRouter`, `edit/allocate.ts`), `zone_type: vlan`, the router's
  leg on it and the host on it -- one command, one undo step; it draws under
  a new implied switch;
- a router item lands on the pane as before (it joins segments by legs);
- a host dragged onto another segment's switch moves there; a tap chip
  dragged onto a real switch becomes a segment tap on its segment.

**Insert switch** (an implied switch's segment panel, or its context menu;
`insertSwitch`): a `kind: switch` node `sw-<segment>`, imageless, `role:
l2-switch`, `source: authored`; a trunk from the router with a leg on the
segment to it (`native` and only VLAN the segment, free ports at both ends);
an access link from it to each single-instance host whose first leg is the
segment. A `count > 1` group cannot be named by a link (§5; the expander
refuses it), so it stays on its `net:` and draws under the new switch, which
now carries its segment; the panel says so. The implied switch is gone. One
command through the stack (Undo restores the text byte for byte), validated
through `POST /api/validate` like any edit. Refused for a segment a switch
already carries, and for one with no router leg and no single host.

**Inspector.** An implied switch's panel is the segment's, with the note that
it is implied and the Insert switch button; a carried segment's panel names
its switch. A switch's panel lists the segments it carries under *Segments
(VLANs)*, each a click to the segment's panel (a carried segment has no node
of its own to click).

- [x] Implied switch for a switch-less segment (canvas-test, datacenter, the
      tree fixtures), no `segment` node type anywhere, hosts as nodes of their
      own under real switches (switch-test, corp-shaped) — `canvas.tree.test.tsx`,
      `canvas.orthogonal.test.ts`.
- [x] A `/30` stays a router–router line — `canvas.orthogonal.test.ts` (routers).
- [x] Boundary tap: one chip on the router; segment tap on its switch; a tap
      dropped on a real switch — `canvas.inspector.test.tsx`, `canvas.tapdrag.test.tsx`,
      `canvas.tree.test.tsx` (`boundaryRouter`).
- [x] Each drop rule, the VLAN picker, Insert switch through the command stack
      (Undo / Redo byte-equal) into a config the canvas's checks accept —
      `canvas.switches.test.ts`, `canvas.switches.page.test.tsx`. The real
      engine validator (`POST /api/validate`, :8080, 2026-11-11) returned PASS,
      0 errors, 0 warnings on canvas-test after Insert switch on `servers`, on
      `office`, and after a host dropped on `gw`.
- [x] Pins from epoch 6 are not applied — `canvas.place.test.tsx`,
      `canvas.tree.test.tsx` (epoch 7 pins).
- [x] No edge crosses a node and every structural edge is routed, folded and
      fully open, on canvas-test, datacenter, switch-test, corp-enterprise,
      corp-twin (also with 10.180 and every host fold open) and
      `pit-395a61f04e-1780405200` — `canvas.orthogonal.test.ts`.
- [x] Eyes (headless shots, 2026-11-11): no segment boxes on canvas-test,
      switch-test, datacenter, corp-twin (folded and 10.180 open), the pit
      twin and corp-tree; folded widths against the pill plane: canvas-test
      580 → 408, switch-test 580 → 408, datacenter 1562 → 1158, corp-twin
      2100 → 1740, pit 1332 → 972 (narrower everywhere; one rank taller, the
      hosts' own). corp-twin with 10.180 open is 2451 → 2540 (+3.6 %).

### The device tree checklist (topology-layers, tracks 5a–5d, 2026-11-07)

Each line names where it is proven: a Vitest file, or the eye check's shot in
the session scratchpad (`t5-*`, `t5b-*`, `t5c-*`, `t5d-*`, `t6-*`).

- [x] `kind: switch`, `links[]`, `members[]`, `source` read from the twin config
      and carried through the document pipeline — `canvas.tree.test.tsx`.
- [x] The tree: routers at rank 0, switches by depth, each pill under its deepest
      carrier, hosts on the pill, the `L2` / `L3` / `FW` badge — `canvas.tree.test.tsx`;
      switch-test, corp-tree (`t5-*.png`).
- [x] Links drawn between devices, dashed when down / blocked / unconfirmed,
      clickable to the link row — `canvas.tree.test.tsx`; `t5-switch-test.png`.
- [x] One hue per router down its branch; folds per carrier on the Fan-out;
      the 120-box budget — `canvas.tree.test.tsx`; datacenter, corp-twin (`t6-*.png`).
- [x] The provenance overlay (`O`): dots off, badges and borders on — `canvas.tree.test.tsx`.
- [x] Plane epoch 6: absolute pins; a pill drags free — `canvas.tree.test.tsx`.
- [x] The inspector by tier: Identity & placement / Traffic / Taps / Members /
      Provenance — `canvas.inspector.test.tsx`; `t5b-switch-node.png`.
- [x] The link panel edits ports, mode, native, VLANs, state — `canvas.inspector.test.tsx`.
- [x] Taps as chips on pills and devices; the tap panel; Add / Remove on a segment;
      a plan created on a twin without one; a segment tap moved by drag —
      `canvas.inspector.test.tsx`; `t5b-switch-tap.png`.
- [x] Confirm / mark unconfirmed / assert through the seam as `confirm` / `doubt` /
      `device_kind` / `link` / `tap` records keyed by the cast roster; blocked with the
      reason otherwise — `canvas.inspector.test.tsx`.
- [x] The Checks tab's Provenance summary and its canvas filter —
      `canvas.inspector.test.tsx`; `t5b-corp-tree-checks.png`.
- [x] Members: the `×N·` dot, chips on double-click, the member panel, Detach, Move,
      chip drag between groups, the split form, merge — `canvas.members.test.tsx`;
      `t5c-members.png`.
- [x] Subtree select (Alt-click, menu), its count, Ctrl+D parked copy with `-2` names
      and the next free `/24`, Duplicate onto a device, attach device-device and
      device-host, a parked device dropped on a device — `canvas.subtree.test.tsx`;
      `t5d-*.png`.
- [x] The engine side: IR 4, the seven rules, the planner skipping an imageless
      switch, A2's three kinds, the seam v2 — `engine/tests/test_topology_layers.py`,
      `discovery/tests/test_synth_topology_layers.py`; the dev45 deploy of switch-test.
- [x] Built 2026-11-08 (rules 11 and 12 above): multi-select member rows, the split
      recorded as an assertion, the chips' zoom threshold — `canvas.members.test.tsx`,
      `canvas.inspector.test.tsx`; the floating copy, drop on the canvas, re-allocation
      under another site, the marquee as a subtree — `canvas.subtree.test.tsx`, eye on
      corp-tree (`E1-*.png`, nothing saved).
- [ ] Not built: the copy's pills carried along with its pinned root; the planner's L2
      port table for an imaged switch is the expander's and not drawn.

### Review round 3 checklist (2026-11-07, `docs/design/canvas-review-2026-11-05.md` R3-1..R3-10)

Shots in the session scratchpad (`A-*`, `B-*`, `C-*`).

Track A — provenance and validation:

- [x] R3-1: the cast writes `source` + `confidence` + `vantage` on every segment, node
      group and tap binding (the gateway placeholder: `unconfirmed` + `vantage`); operator
      taps re-applied after the cast are `asserted` — `discovery/tests/test_synth_compiler.py`.
      A missing `source` reads `discovered` on a twin cast from an imprint and `authored`
      on a hand-authored one; the overlay toggle (rail and `O`) exists only on a cast twin —
      `canvas.round3.test.tsx`, `canvas.tree.test.tsx`. Eye: corp-apcn-reader-20261028 (cast
      2026-10-28, before the cast wrote `source`) reads discovered 135 / authored 0 with the
      overlay on (`A-apcn.png`); switch-test has no overlay button.
- [x] R3-3: a pass with warnings reads `valid` — `canvas.round3.test.tsx`. The IPv6 /64 on
      corp-apcn-reader-20261028 is the canvas's own CV-012 error (the engine's validator
      takes IPv6); the Checks tab now counts it apart from the API line and labels it
      `error`, and its message says the cast drops IPv6 segments (it has since the twin was
      cast) — that twin still reads `invalid` until the segment is removed or it is re-cast.
- [x] R3-7: a switch is a `switch` node (`.react-flow__node-switch`) — `canvas.round3.test.tsx`;
      one on switch-test's DOM.
- [x] R3-9: the blocked banner clears on the next change (an edit, Undo, Redo); the
      working-copy poll's failure is its own line, cleared by the next good poll; the
      "N local entities in no segment" count is a Notes line, not a box —
      `canvas.round3.test.tsx`; Evidence on corp-apcn-reader-20261028 shows 111 in Notes
      and no box (`A-evidence-notes.png`).

Track B — the hierarchical device tree (brief "Canvas rendering"):

- [x] Ranks: edge routers (an uplink, else the router with most segments) → other routers
      by hops → switches by hops (a router-less component rooted at its most-linked switch)
      → pills → hosts. With switches, a segment is a label (on its trunk and on its host
      leaf) and a router whose branch reaches a pill by a trunk draws no leg of its own to it;
      with none, router → pills → hosts. A segment only routers are on (`fab-link`) is a
      router-to-router link labelled with it — `canvas.tree.test.tsx`,
      `canvas.orthogonal.test.ts`; datacenter shows `fab-r1 —fab-link— fab-r2` (`B1-datacenter.png`).
- [x] R3-4: orthogonal edges routed through the gutters between ranks (`canvas/orthogonal.ts`:
      down from the source, horizontal only in a gutter, one lane per parent, down a long
      edge's reserved dagre column, into the target from above); a dragged end draws a
      smoothstep until Re-layout. The geometry test runs `placeTree` over canvas-test,
      datacenter, switch-test, corp-enterprise and corp-twin, folded and fully open, and
      asserts every structural edge is routed, axis-aligned and crosses no box —
      `canvas.orthogonal.test.ts`. Eye: switch-test's gw→office no longer crosses the trunk;
      corp-enterprise's fan is orthogonal (`B2-*.png`).
- [x] R3-5: a hue per router only with two or more routers; a one-router twin is neutral
      (`HUE_NEUTRAL`) — `canvas.orthogonal.test.ts`; canvas-test, switch-test, corp-enterprise,
      corp-twin neutral, datacenter three hues.
- [x] Folding, folded by default: per carrier, segments of one site (two-octet prefix) fold
      into one site pill (`10.20 · 11 segments`), the carrier shows `fan` items and `+N more`,
      an opened fold leaves a `fold` tile to close it; a pill shows eight host icons and `+N`;
      `×N` folds a group; a click opens in place and the tree re-ranks (the session's settled
      positions are dropped on a fold change, kept on a document edit) — `canvas.test.tsx`,
      `canvas.orthogonal.test.ts`, `canvas.round3.test.tsx`; corp-twin and
      corp-apcn-reader-20261028 open folded.
- [x] R3-8: a double-click anywhere on a `×N` icon opens and closes its member chips (each
      selectable, draggable onto another group: track 5c) — `canvas.members.test.tsx`.
- [x] Positions: side-car pins override dagre; Re-layout (beside Save layout) drops them as
      one unsaved change — `canvas.round3.test.tsx`.
- [x] Archived the bus plane's geometry (`archive/ui-bus-plane/`: `wiring.ts`, `route.ts`,
      `layout/plane.ts`, `layout/viewport.ts`). `layout/level0.ts` / `level1.ts` stay: the tree
      reads what they decide is drawn and discards their positions.
- [x] R3-6, measured on the :8080 build (headless chromium, 1622×980): first paint was
      ~7 s on every twin (datacenter 6.5 s, corp-twin 7.1 s) because the page's load listed
      and read EVERY twin-config for the palette before drawing; the palette now loads after
      the drawing — corp-twin 3.0 s, pit-395a61f04e-1780405200 2.0 s. Pan holds 61 fps (worst
      frame 17 ms) folded; corp-twin with every fold open (168 nodes) re-renders in ~250 ms
      and each zoom step settles in ~300 ms. The 30 s freeze did not reproduce: the per-render
      A* wiring of the bus plane (`route.ts`, now archived) was already disabled at 5a.

Track C — duplicate subtree and console polish:

- [x] R3-2: a switch no router reaches (a parked duplicate, a switch just placed — `edit/subtree.ts`
      `parkedDevices`; a down link carries nothing) holds the document HERE: no API validate,
      no working-copy sync, a `not attached` badge, a strip line and every exit (Save, Apply,
      Persist, Save as) disabled with the reason; it validates and syncs again once attached.
      The copy is one undo step; taps never copy; a parked copy keeps what its original's
      uplink carried as its own ports, so the attach tool trunks exactly those. A new box that
      would land on a settled one moves right into free space — `canvas.subtree.test.tsx`,
      `canvas.orthogonal.test.ts`. Eye, switch-test (deployed): Duplicate on access-sw parks
      `access-sw-2` with its `office-2` beside the original, the bar says "held here", Apply is
      disabled with the reason, NO write reaches the working copy; Undo removes it (`C-dup2.png`).
- [x] Twins list: twin (name over id), origin (`authored` / `cast from <imprint>` linked),
      per-tier deploy state (the canvas pill's words), hosts, segments, last changed (the API's
      new `modified_at`: the newest config-file mtime, the layout side-car excluded), Canvas ·
      Deploy · Delete…; no Apply sentence, no "As of: unknown", no "Confidence: none"; Delete
      lives on the detail pages — `imprints.page.test.tsx`, `engine/tests/test_api.py`; `C-twins.png`.
- [x] Imprint page: "Cast into a twin" a primary button at the top; assertions as a list with
      their note and origin; the JSON under Advanced — `imprints.page.test.tsx`; `C-imprint.png`.
- [x] Discovery run form: vantage, window, subbuckets defaulted from the source (its defaults,
      else its last probe) under Advanced; the run starts without opening it —
      `run.flow.test.tsx`; `C-source.png`.
- [x] One name+id presentation (`EntityName`), one date style (`When`: `YYYY-MM-DD HH:mm`
      local, UTC in the title), rows and buttons for navigation (`LinkRow`, `.linkbutton`).
      Census bounds on the source probe and the run window keep their seconds-precise UTC.

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
device that carries each segment, read top to bottom. **2026-11-11 (user,
topology-layers §9 D15):** a segment is no longer a box ("pill"); it is drawn
as the switch that carries it, real or implied. Rules 1, 2, 4, 5, 6, 8 and 9
below are the current ones.

**The model.** A segment is the L2 primitive (one vnet). A host is on the
segments its `net` / `nets[]` name. A router has one leg per segment it
serves. A `kind: switch` is a device wired by `links[]` (trunk or access,
`native`, `vlans[]`, `state`); a switch carries the segments its links and
its own L2 ports name. Every segment, node and link carries `source`
(`discovered` / `asserted` / `authored` / `unconfirmed`) and a discovered
one its `confidence`.

1. **Ranks: router → switch (real | implied) → hosts.** The Internet (drawn
   only on evidence of non-local peers or a configured uplink) over the
   gateway routers; routers at rank 0; switches by depth along their up links
   from a router (`treeDepths`); every host a node of its own one rank under
   its switch. A segment is drawn as a switch, never a box:
   (a) a **real carrier** -- the deepest `kind: switch` whose up access or
   trunk link, or own L2 port, carries it (`carriersOf`) -- stands for it: the
   segment's hosts hang directly off that switch, the segment shows as the
   VLAN label on their access edges, on hover and in the inspector, and
   `anchorByName[segment]` is the switch's node id;
   (b) no switch carries it: an **implied switch**, node id `vsw:<segment>`
   (`ID.segment`), type `impliedSwitch`, data the segment's
   (`kind: "segment"`, `implied: true`), under the router with a leg on it.
   It is presentation only and never written to the twin config;
   (c) only routers are on it (a `/30`, `fab-link`): one router-to-router
   `seglink`, as before;
   (d) a multi-homed host is a child of its first segment's switch and
   draws between it and the switch of each further segment (a `stub` from
   that switch, which also ranks above it).
   Each host's home segment is its first leg's NAME (`TreeStructure.homes`),
   so two segments on one CIDR (§9 D3) never share a host. No switches
   (canvas-test, datacenter) is the same tree: router → implied switches →
   hosts. There is no site tint.
2. **Icons only.** Router, switch (`DeviceNode`: the switch glyph with the
   `L2` / `L3` / `FW` badge), **implied switch** (`ImpliedSwitchNode`: the
   switch glyph with a dashed outline, an `implied` mark at its left and the
   host count at its corner; the segment's name above, its CIDR under the
   name when labels are on), host (the role glyph in the evidence ring, the
   `×N` badge inside it), the `+N more` fold, a switch's `+N` host fold, and
   the provenance mark. Routers and switches keep a short name above the
   icon; the rail's **Labels** toggle captions host icons (all / selected /
   none). Compact detail keeps the host icons (they are the leaves) and
   drops their captions.
3. **Hover** is the facts: a host's name, address, role, segments,
   provenance; a device's kind, image, ports, segments, provenance; an
   implied switch's segment name, CIDR, zone type, hosts, routers and
   provenance; a link's port pair, mode, VLANs and state while selected.
4. **One hue per router** (`canvas/hues.ts`, eight then grey) on its ring,
   on every device its links reach (`hueOfDevice`), on the links, the
   implied switches and the access edges below it. On a real switch of two
   or more segments each segment's access edges wear their own hue
   (`hueOf(k)` by the segment's place on that switch).
5. **Edges.** A `links[]` entry is a `link` edge between two devices (or
   from a device to a host), orthogonal, in the branch's hue, dashed when
   `down`, `blocked` or `unconfirmed`, clickable (the inspector's link row).
   A host's **access edge** is a `leg` from its switch (real or implied),
   id `leg:<host>><segment>` (an attachment: click and Delete detach it);
   on a real switch the first access edge of each segment keeps its VLAN
   label on (`data.vlan`); a host an up link already wires to the switch
   draws only that link. A router's legs are the config's `nets[]`: one
   `leg` edge per implied switch it serves (to a carrier outside its own
   branch, to that switch; none to a segment its own link reaches); a leg to
   a folded segment goes to the fold, once. Observed flows stay evidence:
   arcs under Light-up or for the selection.
6. **Folding, per switch.** A switch (real or implied) shows at most
   `TREE_HOST_BUDGET` (8) host icons and a `+N` host fold last on its row
   (`hostfold:<switch node id>`, expand key `hosts:<switch node id>`); its
   role bands and the `×N` badges are per switch too. Each host row is one
   block centred under its switch, packed left to right in its rank clear of
   every other box and of the columns dagre reserved for longer edges
   (`packHostRows`). The implied switches of one site (two-octet prefix)
   under a carrier fold into a site fold, and **every site fold is always on
   the carrier's rail**. Real switches are devices ranked by their links and
   are not folded. The **Fan-out** (the rail's button; 2 / 4 / 8 / all,
   default 4) budgets the loose (site-less) implied switches: past it a
   dashed `+N more`
   (`overflow:carrier:<device>`) holds only the loose segments beyond it; a
   click expands that carrier in place (`expanded` carries
   `carrier:<device>`) and its tile reads `fold N back`, the same N. An open
   site shows Fan-out of its segments on its own row under the rail, then
   its own `+N more` (`overflow:more:site:<c>|<p>`), which opens the next
   page (`site:<c>|<p>#2`, `#3`, ...); closing the site closes its pages. A
   fold's refit frames the router plus the row that changed, padded, never
   below 50 % zoom; a row too wide for that is framed from its left edge.
   The status strip counts the segments behind `+N more` folds and, apart,
   those in closed site folds (R5-2). Segments no device carries
   fold under `none`. A `×N` group folds its instances; a segment over the
   band budget folds its role bands. The box budget is 120 drawn boxes after
   folding (`TREE_BUDGET_BOXES`; a switch's host row counts as one box);
   past it, the list and the matrix.
7. **Provenance overlay** (rail button, `O`): off, a dot marks an asserted
   (accent) or authored (green) item; on, every item wears its state's badge
   (`D` discovered with its confidence, `A` asserted, `+` authored, `?`
   unconfirmed) and border (accent / green / dashed amber). Kept per user.
8. **Positions.** dagre's, then the layout side-car's pins override them:
   `host:<name>` (a host, a router or a switch) and `segment:<cidr>` (an
   implied switch) are absolute top-lefts on this plane -- **plane epoch 7**
   (2026-11-11); pins saved on the pill plane (epoch 6) are read but not
   applied, and the page says so. A session's settled position is never
   kept under a key two nodes share (two segments on one CIDR). An implied
   switch drags free; a host dropped on another segment's switch (implied,
   or real by the drop rule below) moves there; "Add a segment" lands the
   new implied switch under the selection's carrier at dagre's next free
   slot.
9. **Taps** (topology-layers §8, **built 2026-11-07, track 5b**; placement
   2026-11-11): every binding of the tap plan is an amber `tap` chip beside
   the icon of what it names -- a `segment` tap on the segment's switch (real
   or implied), a `boundary` tap ONE chip on the router with a leg on both
   its segments (`boundaryRouter`; when no one router forms it, a chip on
   each side's switch), a `port` tap on the device (or, for a host's port,
   on the host's switch, at its top-left). A click opens the tap's panel; a
   segment tap drags onto another implied switch, or a real switch (a
   segment tap on its segment), and moves there (`moveTap` / `retargetTap`,
   a `taps` change). The tap plan
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
   topology changes. **Built 2026-11-08**: multi-select rows (Shift-click a
   range, the header box for all; two or more picked get a bulk row that sets
   their role override or persona at once, one undo step); *Split and assert*
   on a cast twin, an override `field: group` per observed host the operator
   names in a second step, as many as the instances split off (the cast ties
   no instance to a host, so nothing is guessed); past zoom
   1.6 (`MEMBERS_ZOOM`) every `×N` group shows its chips, a double-click still
   closes one.

12. **Subtree select, duplicate, attach** (§6, **built 2026-11-07, track
   5d**, `edit/subtree.ts`): Alt-click a switch or router (or the menu's
   *Select subtree*) selects it and everything below it -- the deeper
   devices, the segments whose deepest carrier is in the subtree, the host
   groups on them or wired to them, the links among them; the panel counts
   it (`1 switch · 2 segments · 6 hosts`). **Ctrl+D** (or the menu) copies
   the subtree parked: names `-2`, `-3`…; every copied segment takes the
   next free `/24` in its two-octet prefix (`edit/allocate.ts`) and its
   hosts addresses in it; roles, images, counts, member overrides and
   traffic bindings copy; taps never do; the copy is selected. The subtree
   panel's *Duplicate onto…* a device writes the copy's uplink as a trunk
   from the device's next free port, with the original uplink's native and
   VLANs mapped. **Attach**: the rail tool's second click on a device or a
   host (one end a device) writes one `links[]` entry -- a trunk carrying
   what the deeper device carries, or an access link on the host's first
   segment -- with free ports at both ends; a parked device dragged onto
   another device attaches the same way. A parked switch is CV-048 on the
   canvas and NET-140 in the engine, but the expander refuses it outright,
   so on a **deployed** twin the working copy's sync reverts a parked copy
   with the reason in the strip: duplicate onto a device there. **Built
   2026-11-08**: Ctrl+D (or the menu) carries a floating copy under the cursor
   that says where it would land; a press drops it on a switch or router (a
   trunk) or on the canvas (parked, its root pinned where it landed); dropped
   under a pill or site fold of another two-octet prefix every copied segment
   re-allocates there (`duplicateSubtree`'s `prefix`); Enter parks in place,
   Esc cancels; a marquee that takes a switch and only devices below it is
   that switch's subtree (`marqueeSubtree`). Not built: the copy's pills follow
   the tree's placement, not the pinned root.

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
**5d (`t5d-subtree.png`, `t5d-dup.png`, `t5d-dup-deployed.png`):**
corp-tree's `labswitch` subtree selected and counted, Ctrl+D's parked
`labswitch-2` with its two pills at the top right and the panel reading
"none (parked)", and the deployed switch-test refusing the parked copy with
the expander's reason. `ui/test/canvas.subtree.test.tsx` (23): depths,
carriage, the subtree of a switch / of core / of a host, free ports,
duplicate onto a device and parked, the refusals, attach device-device and
device-host, Alt-click + Ctrl+D on the page, the panel's duplicate onto a
device, the attach tool between a device and a host and between devices.

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

### Canvas follow-ups (2026-11-08)

- [x] The lifted level-0 / level-1 engine is archived (`archive/ui-level0-engine/`): the tree
      reads `graph.segments`, `graph.unplacedLocal`, `canvas/bands.ts` (role bands) and
      `canvas/pairs.ts` (east-west pairs, merged on the boxes they are drawn between, keyed as
      the selection keys them). Shown segments and bands checked identical on the 17 catalog
      twins with a `te.yaml` before the move — `canvas.pairs.test.ts`.
- [x] No orphan segments: a host with legs on two segments bridges them; what nothing reaches
      sits under a dashed *Not carried* box (superseded for the bridged ones by R4-2b below: a
      bridged segment joins the carrier's rail, it no longer ranks under the host's pill) —
      `canvas.orthogonal.test.ts` (corp-twin folded and open: no box without an incoming edge);
      eye `D2-corp-twin.png`.
- [x] R3-6 re-measured on this build: first paint corp-twin 3.0 s, pit-395a61f04e-1780405200
      2.0 s, corp-tree 2.7 s; pan 61 fps; on corp-twin each fold opens in 70–165 ms up to 175
      nodes, a host fold in under 190 ms. The 30 s freeze still does not reproduce.
- [x] R3-6 found (later 2026-11-08): no main-thread long task over 92 ms through load, every
      fold, zoom, pan, marquee, drag and the overlay, but the page sent a dozen slow requests
      while drawing — the twin-config listing (4 s alone, 8.8 s under load), a read of every
      other twin's config, and the 5 s validate beside them — so the first clicks waited ~10 s.
      The palette's other-twin items now load when the palette first opens; the engine's
      catalog listing memoises each file's head (LES-283 key) and skips YAML that never spells
      `twin_config` (repeat listing 3.25 s → 0.07 s). corp-twin goes quiet in 4.3 s, was 10.7 s
      — `canvas.edit.test.tsx` (R3-6), `engine/tests/test_twinconfig_registry.py`.
- [x] A copy dropped on the canvas keeps the original's shape: every copied device and pill is
      pinned at its original's position shifted by the root's offset (`copyPositions`) —
      `canvas.subtree.test.tsx`; eye `I1-3-dropped.png` (corp-tree, nothing saved).
- [x] *Split and assert* asks which observed hosts the split instances are (the cast ties no
      instance to a host); the override goes on the hosts named, as many as split off; the
      count-mismatch refusal is gone — `canvas.inspector.test.tsx`. No live cast carries member
      keys yet (older casts), so on the serve the button shows its reason.

### Review round 4 checklist (2026-11-08, R4-2b / R4-3 / R4-4 / R4-7 / R4-8 / R4-10)

Seen on the rebuilt build at :8080 (headless, shots `R4-*` in the session scratchpad).

- [x] R4-3: a subtree is the device, the deeper devices its `up` links reach, the segments
      they carry, the hosts ON those segments and the links among that set; a down or blocked
      link never extends it, a host on a segment outside it is never copied, a copied link (or
      the new trunk) never carries an original segment — `canvas.subtree.test.tsx`. Eye:
      switch-test, access-sw → Duplicate → drop on gw: exactly `access-sw-2`, `office-2`,
      `corp-workstation-2` and one `gw eth2 → access-sw-2 SFP1` trunk carrying `office-2`;
      `servers` still holds file-server alone. Reset afterwards: the working copy equals the
      baseline (`R4-dup-dropped.png`).
- [x] Provenance on what the canvas writes: one pass at the editor's apply stamps every new or
      changed segment, node, link, member override and tap binding `source: authored` and drops
      `confidence` / `vantage`; `asserted` keeps its mark; a discard back to the baseline is not
      stamped; a promote adopts the re-cast baseline's marks — `canvas.provenance.test.ts`. Eye:
      the switch-test working copy after the duplicate carries `source: authored` on the three
      copies and on the attach link.
- [x] R4-4: the cast already wrote `source` / `confidence` / `vantage` (all 89 node groups and
      44 segments of a fresh cast; only the synthetic sink and gateway lack a confidence, by
      §3.2), but `GET /api/twins/{id}/tree` had no field for them and the scenario topology
      route dropped a segment's. Both now carry them — `engine/tests/test_twin_tree.py`;
      `discovery/tests/test_synth_topology_layers.py` keeps the cast side. Eye: a fresh cast of
      pit-094b299eb4-1791313200 into `r4-scratch` — tree: segments 40 discovered / 4
      unconfirmed, nodes 71 / 17, `gw` unconfirmed; the overlay draws them
      (`R4-scratch.png`); deleted afterwards.
- [x] R4-2b: a segment no device carries but a bridging host reaches sits on the carrier's
      rail with its site, dashed from the router `via <host>`; each rail is one row, site then
      CIDR, the `+N more` last and a leaf — `canvas.tree.test.tsx` (rail), `canvas.orthogonal`
      (bridged). Eye: corp-twin collapsed — hq-mx, then 10.100 · 10.101 · 10.120 · 10.121 ·
      `+41 more of 54 segments` in one row; nothing chains down the left; opened, 10.180 (24
      segments), 10.192 (2) and 10.199 (7) are site tiles on the same row (`R4-corp-*.png`).
- [x] R4-8: an open site is one tile on the rail, its segments in their own row below it (the
      router's legs drawn from the tile), never chained through a pill; opening or closing a
      fold refits the view to the tile and its row, or back to the rail — `canvas.tree.test.tsx`.
      Eye: corp-twin, 10.180 opened: 24 pills in one row under the tile; closed, the view
      returns to the rail. A 24-segment row is ~3,400 px wide, so its fit sits near 17 % zoom
      (superseded by R5-2: the row pages by the budget, the refit never goes below 50 %).
- [x] R4-10: double-click (or the zoom threshold) opens one chip per instance below the group's
      label; a chip opens the member panel and drags onto another group; Esc folds every open
      strip; an overridden member's chip wears the dot — `canvas.members.test.tsx`. Eye:
      switch-test `corp-workstation` ×2: chips 01, 02•; Esc closes them (`R4-members3-crop.png`).
- [x] R4-7: one timestamp style, `model/time.ts`: local time with the zone, to the minute
      (`2026-10-06 14:00 CDT`), the exact UTC instant in the tooltip; the pages' `When` and the
      Discovery pages' `formatTs` / `formatIso` go through it — `time.test.ts`. Eye (the
      headless browser runs in UTC): Imprints, a source page, Twins and Designs all read
      `2026-10-06 19:00 UTC`-style. The canvas shows no timestamp of its own.
- [ ] corp-apcn-reader-20261028 still reads invalid (its IPv6 /64, CV-012); fresh casts drop
      the segment. Re-cast, remove it, or rule on non-IPv4 in the engine: the user's call.

### Review round 5 checklist (2026-11-08, re-test of 08:00: R5-1 / R5-2)

Seen on the rebuilt build at :8080 (headless, viewport 1622 × 980, shots `R5-*` in the session
scratchpad).

- [x] R5-1: the re-test read the cast through `GET /api/twin-configs/{id}`, whose segment and
      node summary rows had no provenance fields; the YAML already carried them. The rows now
      carry `source` / `confidence` / `vantage`, and the cast writes `source` on every conduit
      (`discovered` with the PIT's vantage, or `asserted`; no confidence: nothing scores a
      cross-segment pair) — `discovery/tests/test_synth_topology_layers.py` (no node, segment,
      conduit, link or tap without a source; one asserted conduit is `asserted`),
      `engine/tests/test_twin_tree.py` (the detail route). Eye: a fresh cast of
      pit-094b299eb4-1791313200 into `r5-probe` through the API — 44 segments (40 discovered /
      4 unconfirmed) and 89 nodes (71 / 18), none without a source; 43 of each kind carry a
      confidence (only the synthetic sink and `gw` do not); 47 conduits and 43 tap bindings
      all `discovered`. The overlay marks 14 discovered and 10 unconfirmed drawn items. Deleted
      afterwards. The UI's default-by-origin stays the fallback for old twins.
- [x] R5-2 collapsed: every site pill on the rail, loose pills up to the budget, `+N more` holds
      only loose segments — `canvas.foldbudget.test.tsx` over the real corp-twin bundle. Eye:
      corp-twin — 10.100 · 10.101 · `10.120 · 10 segments` · 10.121 · 10.155 · `10.180 · 24` ·
      `10.192 · 2` · `10.199 · 7` · `+7 more` in one row at 73 % zoom (`R5-corp-twin-collapsed`).
- [x] R5-2 expanded: 10.180 once — a row of 4 under its tile and `+20 more`, the refit on the
      router and the row at 65 %; twice — 8 and `+16 more`, at the 50 % floor framed from the
      row's left edge (the rest is a pan away); closed — back on the rail at 62 %. Switches stay
      host leaves on their segment (`R5-corp-twin-open-*`).
- [x] R5-2 one count: `+7 more`, the strip's `7 segments behind "+N more" tiles` and the opened
      tile's `fold 7 back` agree (were 54 vs 43); with 10.180 open the strip reads 27 = 7 + 20.
      Closed sites have their own line (`43 segments in closed site folds`). Eye on the fresh
      cast too: `10.20 · 11`, `10.80 · 20`, `192.168 · 2`, `+7 more`; 10.80 pages 4 at a time.
- [x] switch-test left reset (no working copy); dev45 not touched.

### Full-cycle test checklist (2026-11-09: cast → edit → plan/deploy → live tap apply → destroy)

Five defects from the full-cycle run on switch-test, fixed and re-run by eye on the rebuilt build
at :8080 (headless, 1622 × 980; shots `C*`, `D*`, `M*`, `X*` in the session scratchpad).

- [x] 1 — no taps is a valid plan: the last Remove tap drops `tap/default.yaml` and the manifest's
      `tiers.tap` (one undo puts both back); the loader reads `bindings: []` as no taps; Apply of
      a copy with no taps left tears the tap tier down. `canvas.inspector.test.tsx`,
      `test_api_edit_model.py` (`..._no_taps_left_tears_the_tap_tier_down`), `test_o2_taps.py`.
      A deploy of a twin with no taps stands te and tfc up and shows tap `—`
      (`test_deploy_follow_on.py`).
- [x] 2 — a cast lands as a twin folder (`twin.yaml`, `te.yaml`, `tfc/default.yaml`,
      `tap/default.yaml`, `generated/`); a legacy bundle still renders, with a Notes line.
      Eye: pit-094b299eb4-1791313200 cast into `cycle-scratch-1109` through the API in ~4 s —
      the three current files, 43 bindings `source: discovered`; the canvas draws the chips on
      the opening fold's pills (4 of 43: the rest sit on folded sites), all `discovered`.
- [x] 4 — Add tap waits for a sensor; the field offers the target's EXTERNAL_SENSORS
      (`Tcpdump-SW-A`, `XPress-SW-A`, `o2-suricata` on dev45). Eye on the scratch cast: Checks
      "43 taps need a sensor" (a TIER-LOAD warning, Save open); the row dims every host and leaves
      the taps bright; "Set on all 43" with XPress-SW-A clears it; Save wrote 43
      `sensors: [XPress-SW-A]`. Found and fixed on the way: the set re-stamped all 43 taps
      `authored` — a sensor is a lab fact, not a re-placement; now they stay `discovered`
      (`canvas.provenance.test.ts`). Scratch twin deleted (204).
- [x] 3 — Deploy from the Twins page stands te → tap → tfc. Eye on switch-test: plan
      `switch-test-aac3bb66` (4 VMs), deploy `switch-test-3743f618`; the twin page, never
      reloaded, read `te running · tap queued · tfc queued` → 225 s `te completed · tap running`
      → 231 s `tap completed · tfc running` → 240 s `tfc completed`. Canvas pill `te ✓ · tap ✓ ·
      tfc ✓`; `/api/taps` mirrors office and servers (the boundary). Start tap / Start tfc:
      `canvas.exits.test.tsx`, `test_deploy_follow_on.py` (not clicked live: both tiers stood).
- [x] 5 — the office⇄servers boundary chip dragged onto the servers pill: one `taps` change
      (`live · taps`), the chip redrawn on servers as `segment`; Apply in 12 s; `/api/taps` then
      lists the servers mirror only. Found and fixed on the way: the drag-stop returned at the
      pin-key check before the tap branch (a tap has no pin), which was the snap-back. Inspector
      Scope / Target: `canvas.inspector.test.tsx`. Drop on a router = its two segments'
      boundary; on a link = a port on its VM end (unit-tested; not dragged live).
- [x] Destroy from the canvas bar with the applied-unpersisted copy acknowledged: `tfc · tap · te`
      down in ~31 s (the first live destroy with all three tiers standing), the canvas back to
      `twin config · edit · undeployed`, the Twins row Deploy again; `/api/deploys` empty, the
      follow-on record gone. The catalog's switch-test is unchanged (the move lived in the copy).
      Nothing deployed on dev45.
