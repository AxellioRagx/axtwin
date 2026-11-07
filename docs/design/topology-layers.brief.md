# axtwin topology layers — design brief for the canvas and the twin model

Nov 7, 2026 · @ragx

A twin should resemble the real network's topology and generate protocol-correct traffic from discovered roles. Discovery from a SPAN or external tap sees hosts, VLAN tags and one gateway; it cannot see the switch tree, redundant pairs or anything behind a down port. The canvas must therefore show what discovery found, let the operator fill the gaps by hand, keep those edits across re-casts, and keep topology, traffic and taps independently editable and applicable. This brief is the input for `docs/design/topology-layers.md` in the repo.

## Why

The real corp network (operator's diagrams, 2026-11-07) is a device tree: Comcast ISP → MX85 firewall (L3 gateway for ten VLANs, dual WAN) → MS120 core switch (SFP25 forwarding, SFP26 STP-blocked) → six access switches on trunk ports (SGS-5240, AxeProdSW, cos-sw-r32e01, labswitch, hwlab2960s on a down port, sw-3850) → a 9K01/9K02 vPC L3 ToR pair with HSRP → an eng-8325h VSX pair carrying the servers/ESX segment. The capture comes from an external tap on the ISP link plus a SPAN port on the MS120.

What that vantage yields: the gateway, the core switch, every host as a MAC with VLAN tag arriving on an MS120 port, and the access switches as MACs on those ports. What it cannot yield: the switch tree below the core, trunk definitions, the vPC and VSX pairs, HSRP, WAN2, anything behind the down port. The cast of this capture (`corp-twin`) therefore has one router, twelve `kind: host, role: l2-switch` nodes with no attachments, and 118 node groups; drawing the switches' observed adjacency as routing (track 3) produced thirteen fake routers and 54 crossing legs.

The intent: discover what the capture can show, edit the topology by hand to fill the gaps, keep those edits across re-casts, and keep roles, personas, traffic profiles and taps editable and applicable independently of topology — which is what the layered deploy (te / tfc / tap) exists for.

## Model

Segments stay the L2 primitive the engine deploys (one Proxmox SDN vnet each); hosts stay attached to segments by `net` / `nets[]`. Two additions make the infrastructure layer representable:

| Addition | Shape | Notes |
| --- | --- | --- |
| `kind: switch` | a node kind beside `router` and `host`; `role` in the switch class (`l2-switch`, `l3-switch`, read from the API) | an `l2-switch` host today becomes a `switch`; it is a device, never a gateway |
| `links[]` | `{a: {device, port}, b: {device, port}, mode: trunk\|access, native?, vlans[]?, state?: up\|down\|blocked}` | device-to-device wiring; ports are free text (`P14`, `SFP25`, `Po48`); `vlans[]` names segments carried on a trunk |
| `source` on nodes, links, segments, taps | `discovered \| asserted \| authored`; discovered carries `confidence` and `vantage` | see Provenance |
| `members[]` on a node group | per-member overrides by index (`persona`, `intensity`, `schedule`, `role`) | see Groups and members |
| `vantage` on the containment claim | which tap points the capture came from | a cast states what it could not have seen |

A segment whose only members are routers (a `/30` such as `fab-link`) is a router-to-router link and renders as one. A host with two legs is multi-homed and renders between its two segments. Nothing in the IR carries presentation; positions and overlay state live in the layout side-car.

Deploy mapping: a `switch` with trunks deploys as the vnets its trunks carry (no VM unless the operator gives it an image); a link's `vlans[]` must name segments that exist; a `down` or `blocked` link deploys nothing. The validator refuses a trunk naming an unknown segment, a port used twice on one device, and two segments with one CIDR.

## Provenance

Every node, link, segment, tap and member override carries where it came from, and the edit survives a re-cast only if it is recorded through the assertions seam.

| State | Meaning | Survives re-cast | Rendering (overlay on) |
| --- | --- | --- | --- |
| discovered | the cast produced it from the capture; carries `confidence` and `vantage` | replaced by the next cast | solid |
| asserted | an operator correction to something discovered (role, merge, trunk, placement), written as an `add / override / suppress` assertion | yes, re-applied after cast | accent border + badge |
| authored | something discovery never saw (a ToR pair, WAN2, a tap on a server segment) | yes, stored with the twin config | accent border + badge |
| unconfirmed | observed once or weakly (a down port, an STP-blocked link, adjacency with no traffic) | re-derived | dashed |

The assertions seam today accepts host roles and segment merges; it must also accept device kind, links and trunks, member splits, and tap placement. A re-cast applies assertions after casting and reports any it could not key (the roster changed).

**The overlay** is a rail toggle, off by default. Off: normal rendering, a small dot on authored or asserted items. On: the four states render as above, taps light up, and the Checks tab adds a Provenance summary (`discovered 38 · asserted 4 · authored 7 · unconfirmed 3`) whose rows filter the canvas. The inspector's Provenance block shows what discovery saw (`observed on 7 VLANs via MS120 P14; no trunk defined`) and offers confirm, assert (`attach as trunk, native 1, VLANs …`) and mark-unconfirmed.

## Three tiers, three change kinds

A2's live / re-plan binary becomes three classifications, one per deploy tier, each applied on its own. A working copy may carry all three at once; Apply pushes what is live, Persist keeps the rest for the next redeploy.

| Change | Examples | Tier | Applies |
| --- | --- | --- | --- |
| topology | segment, switch, link, trunk, host placement, count, image, split, merge, duplicate | te | re-plan |
| traffic | role (keeping the image), persona binding, intensity, schedule, per-member overrides, the profile as a whole | tfc | live, replays the tfc tier |
| taps | add, move, remove a tap point; selector; sensor target | tap | live, replays the tap tier |

Defaults, not locks: moving a host keeps its persona; changing a role offers the role's default persona and asks before overriding an explicit binding; deleting a segment that carries a tap asks what to do with the tap. Everything on the traffic and tap side is editable from the inspector, including authoring a persona on a host discovery never saw. The Checks tab shows the three kinds with three badges.

## Groups and members

A node group is `name + count` and the engine expands it into instances (`corp-workstation01`, `02`, …). Editing the group edits all instances; that stays the default. To address one instance:

- **Member overrides.** `members: [{index: 2, persona: backup-server}, {index: 4, role: jump-host}]`; anything not overridden inherits from the group. Override by index maps onto the instance the engine already names. Persona, intensity and schedule overrides are traffic changes (live); a role override keeping the image is live, with a new image a re-plan.
- **Members tab** in the inspector for any ×N group: one row per instance (name, address, persona, intensity, schedule, role), inline-editable; multi-select rows to change several at once.
- **Split**: pick members (or `2 / 3`), name the new group, choose which keeps the original name; the copy starts with the original's role and persona and both stay on the segment. Topology change. Recorded as an assertion ("these two are a separate role") so a re-cast proposes the split rather than ×5 again.
- **Merge**: drop one group onto another of the same image and segment; conflicting fields become member overrides, nothing is lost. Topology change.
- **Detach one**: split off member *n* as its own ×1 group.

On the canvas the ×N badge expands (double-click, or past a zoom threshold) into member chips under the group, each clickable for the member inspector and draggable onto another group (a merge of one). Collapsed, a group with overrides shows `×5 ·` with a dot.

## Subtree select, duplicate, attach

Generalises C3's "duplicate a section onto a segment" to any subtree onto any attachment point.

1. **Select a subtree.** Alt-click or right-click → *Select subtree* on a device selects it and everything below it in the tree: segments, host groups, member overrides, links. Marquee works too. The bar shows the count (`1 switch · 2 segments · 14 hosts`).
2. **Duplicate** (Ctrl+D, or right-click). A floating copy follows the cursor. Names follow `{name}-{n}` (`labswitch-2`, `lab-10-20-193-2`). Every copied segment is re-allocated the next free CIDR in its site (the lifted `allocate.ts`) and its hosts get addresses in it; two segments with one CIDR is a validator error. Roles, personas, counts and member overrides copy. **Taps do not copy** (decided 2026-11-07): a tap is a sensor binding and is placed deliberately. The whole copy is `authored`.
3. **Drop on an attachment point.** On a switch or router: a new `links[]` entry on the next free port, mode trunk with the original's native VLAN. On empty canvas: parked with a "not attached" badge; the validator blocks Apply and Save until attached. Dropping under a different site re-allocates in that site's prefix and moves the copy under its rail.
4. **Attach** (rail tool or drag from a handle) to add a link from the new device to a server, a pair, or another switch: a device target writes `links[]` with free ports on both ends (access for a host, trunk for a device); a host joining one of the copied segments gets a second `nets[]` leg. Ports and mode are editable in the inspector's link row.

Duplicate and attach are topology changes (re-plan); Undo removes the duplicate as one step.

## Canvas rendering

The canvas is a hierarchical device tree with orthogonal, non-overlapping branches, read top to bottom; a layered (Sugiyama) layout with `@dagrejs/dagre` (MIT) replaces the lifted `o2/console` tiered engine, which is archived.

**Ranks.** Edge (routers with an uplink; else the router with most segments) → core / distribution (switches by link count) → access → ToR / leaf pairs → hosts. Segments are not a rank: they are labels on links (trunk VLAN sets, access VLAN) and on host leaves. A config with no switches (canvas-test, datacenter) degrades to router → segment pills → hosts, which is the same tree.

**Icons only.** Five glyphs — router, switch, server, host, tap — plus the ×N badge, the provenance mark (overlay on), and a down/blocked dash on a link. No names, ports or addresses on the drawing by default. Routers keep a short name (few, landmarks). A discovered device with no name gets a derived one (`switch-on-ms120-p14`) shown only in hover and inspector. A labels toggle in the rail: *all / selected (default) / none*.

**Hover** = the facts, read-only: name, role, address or MAC, segments or VLANs, provenance + vantage + confidence; for a link, the port pair, mode and state.

**Inspector on click** = the facts, editable, grouped by tier: Identity & placement (name, kind, role, image, count, links and ports — topology); Traffic (persona, intensity, schedule — tfc); Taps (link, selector, sensor target — tap); Members (for ×N); Provenance (what discovery saw, confirm / assert / unconfirm).

**One hue per router** (8-hue palette, grey beyond) on its icon ring and every branch below it; a site rail in that hue when a router serves more than N segments in one site (N default 4, rail pref); selecting the router expands the fan.

**Folding.** Each site shows a budget of pills and a dashed `+N more` pill; each segment shows a budget of host icons and a count; the ×N badge folds instances by role. Click a fold to expand in place; the tree re-ranks around it. Fit-to-window of the fully expanded tree stays available; the folded view is the default. Width is bounded by the sum of visible pills.

**Evidence vs structure.** Attachments and links are always drawn; observed flows and observed adjacency are drawn only under Light-up or for the selection, dashed. Structure never comes from evidence.

**Positions** in the layout side-car are overrides on top of dagre; *Re-layout* resets; positions survive mutations, a new node is placed into free space.

## Taps on the canvas

A tap is a node that sits on a link or a segment, drawn as an amber chip at the point where the SPAN or external tap is (the ISP uplink, the MS120 SPAN, an authored tap on the servers segment). It is draggable to another link or segment (a tap change, live), has selector and sensor target in the inspector, and carries provenance like everything else: the vantage the capture came from is a discovered tap; one the operator adds is authored. Discovery proposes a tap placement from the containment claim's vantage. Taps are never duplicated with a subtree.

## Open decisions and build order

Open, for the user to confirm before code:

- [ ] `kind: switch` and `links[]` in the IR is new scope: schema version bump in `docs/contracts/IR_SCHEMA.md` and `VALIDATOR.md`; needs sign-off.
- [ ] Assertions seam extended to devices, links, member splits and tap placement: contract change, needs sign-off.
- [ ] Does a `switch` with an image deploy as a VM (e.g. a VyOS/Linux bridge) or only as its vnets? Default proposed: vnets only unless an image is given.
- [ ] Member override by index vs by instance name: index proposed (stable under re-cast if the roster order is stable; verify).

Build order, one commit per track:

1. Design doc `docs/design/topology-layers.md` from this brief; SPEC updates (edit-canvas "Rendering rules" rewritten, `api/Spec.md` A2 three change kinds, assertions seam).
2. IR: `kind: switch`, `links[]`, `source` / `confidence` / `vantage`, `members[]`, containment `vantage`; validator rules; schema version bump.
3. Cast: emit switches and observed adjacency as `unconfirmed`, taps from vantage; assertions re-applied after cast; roster keys for members.
4. A2: three change kinds; tap tier classification and replay.
5. Canvas: dagre device tree, icons, hover, inspector by tier, folding, router hues, overlay, Members tab, split/merge/detach, subtree select/duplicate/attach, taps.
6. Verify by eye on canvas-test, datacenter, corp-enterprise, corp-twin and the largest cast twin; acceptance checklist in the SPEC.
