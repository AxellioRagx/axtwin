# Topology layers: the device tree, provenance, and the canvas

**Status:** decided 2026-11-07 (user, `docs/design/topology-layers.brief.md`),
to be built on `wip/design-platforms-canvas` in the order of §12. §10 and
§11 were signed off by the user the same day; §10 is applied to the contract
files as of track 2 and the tracks of §12 are being built in order.
**Supersedes:** the "Rendering rules" of `ui/src/features/edit-canvas/SPEC.md`
(tracks 1-4, 2026-11-06) and that spec's premise that a twin has no link
objects; `api/Spec.md` A2's two-way *live* / *re-plan* classification as the
console shows it.

## 1. Vocabulary

| word | means | lives in | identity |
|---|---|---|---|
| **device** | a node of kind `router` or `switch`: something with ports that other things plug into | `twin_config.nodes[]` | `name` |
| **switch** | a device that forwards at L2 (`l2-switch`) or L2 and L3 (`l3-switch`); never a gateway | `nodes[] kind: switch` | `name` |
| **link** | one wire between two device ports, or between a device port and a host | `twin_config.links[]` | `(a.device, a.port)` and `(b.device, b.port)` |
| **trunk** | a link carrying several segments as 802.1Q VLANs, with one native | `links[] mode: trunk, vlans[], native` | |
| **access link** | a link carrying one segment untagged | `links[] mode: access` | |
| **port** | free text naming a device's interface (`P14`, `SFP25`, `Po48`); unique on its device | `links[].a.port`, `.b.port` | |
| **segment** | the L2 primitive the engine deploys, one Proxmox SDN vnet; unchanged | `twin_config.segments[]` | `name`; `cidr` |
| **attachment** | a host's membership of a segment, with or without an address | `nodes[].net` / `nets[]` | |
| **host group** | one `nodes[]` entry with `count: N`, expanded to `N` instances | `nodes[]` | `name`; instances `{name}01…` |
| **member** | one instance of a group, addressed by its index `1…N` | `nodes[].members[]` | `index` |
| **member override** | a field one member carries instead of its group's | `members[] {index, role}` and the tfc profile's `assignments[] {node: <instance>}` | |
| **source** | where a thing came from: `discovered`, `asserted`, `authored`, `unconfirmed` | every node, link, segment, tap and member override | |
| **vantage** | the tap point a capture came from, `<appliance>:<streams>` | the PIT (`ir_pit.vantage`), copied into `twin.yaml` at cast | |
| **assertion** | an operator claim about the network, re-applied after every cast | the imprint's `axr-assertions` file | the entity key (`address:` / `hostname:` / `mac:`, a CIDR, a port pair) |
| **tap** | a sensor binding at a point in the twin: a segment, a port or a link | the tap plan (`tap/*.yaml`) | the selector |
| **tier** | one of the three deploy layers: `te` (topology), `tfc` (traffic), `tap` | the bundle's tier files | |
| **change kind** | which tier a pending edit touches: `topology`, `traffic`, `taps` | A2's per-change classification | |
| **subtree** | a device and everything below it in the tree: its links, the segments they carry, the host groups on them, their overrides | the canvas selection | |
| **overlay** | the rail toggle that renders provenance | the canvas, per page | |

## 2. The device tree

### 2.1 Why

A SPAN or external tap sees hosts, VLAN tags and one gateway. It cannot see
the switch tree, trunk definitions, redundant pairs, HSRP, a second WAN or
anything behind a down port. The real corp network (the operator's
diagrams, 2026-11-07) is ISP → MX85 → MS120 core → six access switches → a
9K vPC pair → an 8325 VSX pair; the cast of its capture is one router and
twelve switch-class nodes, and drawing their observed adjacency as routing
(track 3) produced thirteen fake routers and 54 crossing legs. The model
therefore represents the infrastructure layer as what it is, a tree of
devices wired by links, and marks every piece with where it came from.

### 2.2 Nodes: `kind: switch`

`kind` admits `switch` beside `host` and `router`. A switch's `role` is in
the switch class (`l2-switch`, `l3-switch`); the class is a property of
`kind`, not of the role string, so the canvas never guesses from a name.
A switch is a device: it has ports, links hang off it, and it is never a
gateway. A `kind: host` whose role is `l2-switch` today (corp-twin's twelve)
is offered conversion to `kind: switch` (§9 D2); nothing converts on its own.

A switch may carry `image`, `requires` and `resources` like any node. With an
image it deploys as a VM (corp-twin's bridge VMs are this); without one it
deploys as nothing but the vnets its links carry (§2.5). A switch's own
`nets[]` legs are L2 ports unless given an address: a management SVI (the
MS120's) or an L3 switch's VRRP leg (the 9k pair's) is addressed, and NET-090
no longer refuses it (track 5a's eye check on corp-tree found the refusal).

### 2.3 Links

```yaml
twin_config:
  links:
    - a: { device: ms120, port: P14 }
      b: { device: sgs-5240, port: Po48 }
      mode: trunk           # trunk | access
      native: vlan1         # optional, a segment name
      vlans: [vlan20, vlan64, vlan935]   # trunk: the segments carried
      state: up             # optional: up | down | blocked (default up)
      source: discovered    # see §3
      confidence: 0.6       # discovered only
      vantage: XPress-SW-A:1-4
```

| field | required | notes |
|---|---|---|
| `a`, `b` | yes | `{device, port}`; `device` names a node of any kind; `port` is free text, unique on that device |
| `mode` | yes | `trunk` carries `vlans[]` tagged with `native` untagged; `access` carries exactly one segment, named in `vlans[]` |
| `native` | no | a segment name; trunk only |
| `vlans` | trunk: yes; access: exactly one | segment names that exist |
| `state` | no | `down` and `blocked` deploy nothing and draw dashed |
| `source`, `confidence`, `vantage` | see §3 | |

A link between two devices is wiring. A link whose one end is a host is an
access attachment spelled as a link; it is equivalent to the host's
`net` / `nets[]` entry and the expander treats it as one (the host keeps its
address on the segment). A segment whose only members are routers (a `/30`
such as datacenter's `fab-link`) is a router-to-router link and draws as one
line between the two routers. A host with two attachments is multi-homed and
draws between its segments.

Ports and links carry no presentation. Positions and overlay state live in
the layout side-car (`axr-layout`), as before.

### 2.4 Segments and attachments stay

Segments stay the L2 primitive; hosts stay attached by `net` / `nets[]`;
addresses are allocated per segment as today. A trunk's `vlans[]` names
segments that exist; the segment, not the trunk, is what the engine deploys.
corp-twin's "each VLAN is two segments with one subnet" (an `isolated: true`
access segment behind the bridge VM and its anchor) stays valid under the
rule it already has (§9 D3).

### 2.5 Deploy mapping

| thing | deploys as |
|---|---|
| segment | one SDN vnet, as today |
| `switch` without `image` | nothing: the vnets its links carry already exist; the switch is topology the operator can see and edit |
| `switch` with `image` | a VM with one bridge-port NIC per segment its links carry (what `scripts/serve-l2switch.sh` does for corp-twin today, generated from `links[]` instead of a hand-kept port table) |
| trunk / access link | nothing of its own; it is the reason a switch VM has a NIC on a segment |
| link `state: down` or `blocked` | nothing; the port is drawn dashed and the segments it alone carries become orphans the validator warns about |
| `router` | as today |

The validator (§10.2) refuses a trunk naming an unknown segment, a port used
twice on one device, a link whose two ends are one device, and two segments
with one CIDR unless one is the other's `isolated` extension.

## 3. Provenance

### 3.1 `source` on everything

Every node, link, segment, tap and member override carries `source`.

| `source` | meaning | survives re-cast | drawn (overlay on) |
|---|---|---|---|
| `discovered` | the cast produced it from the capture; carries `confidence` (0…1) and `vantage` | no: the next cast replaces it | solid |
| `asserted` | an operator correction of something discovered (a role, a merge, a trunk, a placement), recorded as an `add` / `override` / `suppress` assertion | yes: re-applied after the cast | accent border, badge |
| `authored` | something discovery never saw (a ToR pair, WAN2, a tap on a server segment), stored in the twin config | yes: it is the twin's | accent border, badge |
| `unconfirmed` | the cast's own verdict on weak evidence: adjacency seen once, a port that never carried traffic, an STP-blocked link | re-derived by every cast; an assertion may pin it either way | dashed |

`confidence` and `vantage` are written only by the cast (an assertion's
confidence is 1.0 by convention and never authored, as the seam already
rules). A hand-authored twin has `source: authored` on everything and no
vantage; the catalog shows it as *authored* as it does today.

The cast's provenance side-car (`<id>.provenance.json`, one record per
field) stays the record of **which evidence** produced a value; `source` in
the config is the one-word summary of it that the overlay, the validator and
the diff read without the side-car. The two must agree: a cast writes both
from the same facts, and the inspector's Provenance block reads the side-car
for the detail (`observed on 7 VLANs via MS120 P14; no trunk defined`).

### 3.2 The assertions seam, extended

The seam (`axr-assertions/1`) takes `add` of `conduit | host | segment`,
`override` of a host's `image | resources | role | segment` and a segment's
`name | zone_type`, and `suppress` of a host. It must also take:

| record | key | content |
|---|---|---|
| `add` `kind: device` | the device's host entity key (`mac:` for a switch seen as a MAC on a port; `hostname:` when named) | `device_kind: switch \| router` (required), `role`, `image`, `resources`, `segment` |
| `override` `field: device_kind` on a host | the host's entity key | `switch \| router \| host` (the last turns a device back into a host) |
| `add` `kind: link` | `<hostKeyA>#<port>|<hostKeyB>#<port>`, the two ends sorted | `mode` (required), `native` and `vlans[]` as CIDRs (the seam's segment key), `state` |
| `override` on a link | the link key | `mode`, `native`, `vlans`, `state` |
| `override` `field: group` on a host | the **member's** entity key, one record per member | the name of the group it joins: a member split |
| `add` `kind: tap` | `segment:<cidr>` or `port:<hostKey>#<port>` | `sensors[]` |
| `confirm` / `doubt` | a host key, a segment CIDR or a link key, with a note | pins a discovered value as `asserted`, or marks it `unconfirmed` |

A re-cast applies assertions after casting and reports every record it could
not key (the roster changed: the MAC is gone, the CIDR moved) under the
sidecar's `population.assertions.ineffective` and `unmatched`, which the cast
response republishes as caveats. Member records are keyed by entity key,
never by index, so a member that changes position in the roster keeps its
override (§11 decision 4). A `device` is a host entity, so its other fields
reach the node the way a host add's do; a switch with no asserted image
deploys as nothing (§2.5). A `link` end must resolve to a group of one; a
link naming a `count > 1` group is reported, and the remedy is a `group`
split first. A `port` tap resolves its segment through the asserted link that
carries the port.

This is the seam's version 2 (`axr-assertions/2`, §11 decision 2, built in
track 3): a superset of version 1, so a version 1 file parses unchanged and is
re-emitted as version 2 on its first save. The published schema moved to
`axr-assertions-2.schema.json` in both repos.

The cast writes `source` on every segment and node it emits (§3.1): an
operator's confirm or doubt mark wins; an asserted group is `asserted`; the
synthetic gateway and sink are `unconfirmed`; a group on the transient rung
(occupancy below 0.5) or with no measurable presence is `unconfirmed`;
everything else is `discovered` with its occupancy as `confidence` and the
PIT's vantage.

### 3.3 The overlay

A rail toggle, **off by default**.

- Off: normal rendering; a small dot marks an `asserted` or `authored` item.
- On: the four states draw as in §3.1; taps light up; the Checks tab adds a
  *Provenance* summary (`discovered 38 · asserted 4 · authored 7 ·
  unconfirmed 3`) whose rows filter the canvas to that state.
- The inspector's *Provenance* block shows what discovery saw and offers
  **confirm**, **assert** (`attach as trunk, native 1, VLANs …`) and **mark
  unconfirmed**; each writes an assertion through the seam and re-casts, as
  promote-to-assertion does today.

## 4. Three tiers, three change kinds

A2 classifies every pending change by the tier file it touches. The console
shows three kinds instead of a *live* / *re-plan* pair, and Apply pushes each
kind through its own tier.

| kind | examples | tier | applies by |
|---|---|---|---|
| **topology** | a segment, switch, link, trunk, host placement, count, image, split, merge, duplicate, attach | `te` | re-plan (refused by Apply as `requires-replan`; Persist or Save-As keeps it for the next redeploy) |
| **traffic** | a role keeping its image, a persona binding, intensity, schedule, a member's persona / intensity / schedule / role, the profile as a whole | `tfc` | live: the tfc playout replays |
| **taps** | add, move or remove a tap; its selector; its sensor target | `tap` | live: the tap playout replays |

A working copy may carry all three; Apply pushes what is live and leaves the
topology changes pending. The Checks tab shows the pending changes under
three badges.

Defaults, not locks: moving a host keeps its persona; changing a role offers
the role's default persona and asks before overriding an explicit binding;
deleting a segment that carries a tap asks what to do with the tap. Everything
on the traffic and tap side is editable from the inspector, including a
persona on a host discovery never saw.

A member's traffic override lives in the tfc profile (`assignments[] {node:
<instance id>, persona, intensity, schedule}`, which exists today); a member's
`role` lives on the group in `te.yaml` (`members[] {index, role}`). A2
classifies the first as traffic by its file and the second as traffic when the
member keeps its image (the existing `role` rule, extended to members), else
topology.

## 5. Groups and members

A node group is `name + count`; the engine expands it to instances
`{name}01 … {name}NN`. Editing the group edits every instance and stays the
default. To address one instance:

- **Member overrides.** `members: [{index: 2, role: jump-host}]` on the group
  for the te-tier field; `assignments: [{node: corp-workstation02, persona:
  backup-server}]` in the tfc profile for the traffic fields. Anything not
  overridden inherits from the group. The inspector presents both as one row.
- **Members tab**, for any `×N` group: one row per instance (name, address,
  persona, intensity, schedule, role), inline-editable; multi-select rows to
  change several at once.
- **Split.** Pick members (or `2 / 3`), name the new group, choose which keeps
  the original name; the copy starts with the original's role and persona and
  both stay on the segment. A topology change. Recorded as an assertion keyed
  by the members' entity keys ("these two are a separate role"), so a re-cast
  proposes the split instead of `×5` again.
- **Merge.** Drop one group onto another of the same image and segment;
  conflicting fields become member overrides, nothing is lost. A topology
  change.
- **Detach one.** Split off member *n* as its own `×1` group.

On the canvas the `×N` badge expands (double-click, or past a zoom threshold)
into member chips under the group, each clickable for the member inspector
and draggable onto another group (a merge of one). Collapsed, a group with
overrides shows `×5 ·` with a dot.

## 6. Subtree select, duplicate, attach

C3's "duplicate a section onto a segment" generalises to any subtree onto any
attachment point.

1. **Select a subtree.** Alt-click or right-click → *Select subtree* on a
   device selects it and everything below it: links, the segments they carry,
   the host groups on them, their member overrides. Only an `up` link extends
   it (R4-3, 2026-11-08): what hangs off a down or blocked port is not below
   the device, a host on a segment outside the set is not in it, and a copied
   link never carries an original segment. Marquee works too. The bar
   shows the count (`1 switch · 2 segments · 14 hosts`).
2. **Duplicate** (Ctrl+D, or right-click). A floating copy follows the
   cursor. Names follow `{name}-{n}` (`labswitch-2`, `lab-10-20-193-2`). Every
   copied segment takes the next free CIDR in its site (`edit/allocate.ts`)
   and its hosts get addresses in it. Roles, personas, counts and member
   overrides copy. **Taps never copy**: a tap is a sensor binding, placed
   deliberately. The whole copy is `source: authored`.
3. **Drop on an attachment point.** On a switch or router: a new `links[]`
   entry on the next free port, `mode: trunk` with the original's native VLAN.
   On empty canvas: parked with a *not attached* badge; the validator blocks
   Apply and Save until it is attached. Dropped under another site it
   re-allocates in that site's prefix and moves under that site's rail.
4. **Attach** (rail tool, or a drag from a handle) adds a link from the new
   device to a host, a pair or another switch: a device target writes
   `links[]` with free ports at both ends (`access` for a host, `trunk` for a
   device); a host joining a copied segment gets a second `nets[]` leg. Ports
   and mode are editable in the inspector's link row.

Duplicate and attach are topology changes; Undo removes the duplicate as one
step.

## 7. Canvas rendering

### 7.1 Layout: a device tree

The canvas is a hierarchical device tree with orthogonal, non-overlapping
branches, read top to bottom. A layered (Sugiyama) layout from
`@dagrejs/dagre` (MIT; a new `ui/` dependency, added with `npm ci` semantics)
replaces the lifted `o2/console` tiered engine
(`ui/src/features/edit-canvas/layout/`) and track 4's bus placement
(`canvas/bus.ts`, `wiring.ts`, `fan.ts`, `route.ts`), which are archived.

**Ranks**, top to bottom: edge (routers with an uplink, read from a segment's
`egress: true`; else the router with the most segments) → core / distribution
(switches by link count) → access → ToR / leaf pairs → hosts. Segments are not
a rank and never a box: a segment is drawn as the switch that carries it
(2026-11-11, §9 D15), one uniform tree everywhere, router → switch (real or
implied) → hosts:

1. **A real carrier.** A `kind: switch` whose access or trunk link (up), or
   own L2 port, carries the segment (the deepest such switch): the segment's
   hosts hang directly off that switch as nodes of their own. The segment
   appears only as the VLAN label (and, on a switch of two or more segments,
   a per-segment hue) on the access edges, plus in hover and the inspector.
2. **No switch carries it** (`gw → servers → file-server`): an **implied
   switch**, node id `vsw:<segment>`, drawn as the switch glyph with a dashed
   outline and an "implied" mark; its hover and inspector show the segment's
   name, CIDR and zone type. It is presentation only and never written to the
   twin config; the inspector's *Insert switch* writes a real one.
3. **Only routers are on it** (a `/30`, datacenter's `fab-link`): unchanged,
   one router-to-router line labelled with it.
4. **A multi-homed host** draws between the switches (real or implied) of its
   segments: a child of its first segment's switch, with a stub from each
   other one.

A config with no switches (canvas-test, datacenter) is the same tree: router
→ implied switches → hosts.

### 7.2 Icons only

Five glyphs: router, switch, server, host, tap; the implied switch is the
switch glyph with a dashed outline and an "implied" mark (§7.1 rule 2); plus
the `×N` badge, the provenance mark (overlay on) and a down / blocked dash on
a link. No names,
ports or addresses on the drawing by default. Routers keep a short name (few,
landmarks). A discovered device with no name gets a derived one
(`switch-on-ms120-p14`) shown only on hover and in the inspector. A rail
toggle for labels: *all / selected (default) / none*.

### 7.3 Hover

The facts, read-only: name, role, address or MAC, segments or VLANs,
provenance with vantage and confidence; for a link, the port pair, mode and
state.

### 7.4 Inspector on click

The facts, editable, grouped by tier:

| block | fields | tier |
|---|---|---|
| Identity & placement | name, kind, role, image, count, links and ports | topology |
| Traffic | persona, intensity, schedule | traffic |
| Taps | link or segment, selector, sensor target | taps |
| Members | the `×N` rows of §5 | both |
| Provenance | what discovery saw; confirm / assert / mark unconfirmed | assertion |

### 7.5 One hue per router

The k-th router wears the k-th hue of an eight-hue palette (grey beyond) on
its icon ring and on every branch below it. A site rail in that hue appears
when a router serves more than N segments in one site (N default 4, a rail
preference); selecting the router expands the fan.

### 7.6 Folding

Folding is per switch (2026-11-11), real or implied. Each switch shows a
budget of host icons (8) and a `+N` host fold; the `×N` badge folds instances
by role and role bands collapse per switch. Every site a router serves is
always a site fold on its rail; the budget (the rail's Fan-out, default 4)
counts the loose implied switches, and a dashed `+N more` holds only those
beyond it. An open site shows a budget of its implied switches per page on its
own row, then its own `+N more` for the next page; the refit frames the router
and that row, never below 50 % zoom (R5-2, 2026-11-08). Real switches are
devices, ranked by their links, and are not folded into site folds. A click on
a fold expands it in place and the tree re-ranks around it. Fit-to-window of
the fully expanded tree stays available; the folded view is the default.
Width is bounded by the sum of visible switches and their host rows.

### 7.7 Evidence versus structure

Attachments and links are always drawn. Observed flows and observed adjacency
draw only under Light-up or for the selection, dashed. Structure never comes
from evidence: an observed adjacency becomes a link only through the
inspector's assert action.

### 7.8 Positions

Positions in the layout side-car are overrides on top of dagre; *Re-layout*
resets them; positions survive mutations and a new node lands in free space.
This is plane epoch 7 (2026-11-11, segments drawn as switches): pins saved on
an older plane (the pill plane, epoch 6, and the bus plane before it) are read
but not applied, and the page says so.

## 8. Taps on the canvas

A tap is a node on a link or a segment, drawn as an amber chip where the SPAN
or external tap sits (the ISP uplink, the MS120 SPAN, an authored tap on the
servers segment). It drags to another link or segment (a taps change, live),
carries selector and sensor target in the inspector, and carries provenance
like everything else: a tap the capture came from is `discovered`; one the
operator adds is `authored`. Discovery proposes a tap placement from the
vantage when the vantage names a point the twin has (§9 D8). Taps never
duplicate with a subtree.

A tap on a link is a `port` selector on one end of the link in the tap plan
(`scope: port`); a tap on a segment is `scope: segment`, as today. No new
tap-plan scope is needed.

Where the chip sits (2026-11-11, §7.1): a `scope: segment` chip rides the
segment's switch, real or implied; a `scope: boundary` chip is ONE chip on the
router that forms the boundary (the router with legs on both segments), not
one per segment; a `scope: port` chip rides the device, or, for a host's port,
the host's switch.

## 9. Deltas: where the brief and the code or contracts disagree

| # | the brief says | the code or contract says | resolution in this design |
|---|---|---|---|
| D1 | `kind: switch` in the IR is new scope | The Scenario IR already has it: `docs/contracts/IR_SCHEMA.md:180` (`kind: host \| router \| switch`), `:254` (a switch bridges `nets: [<id>, …]`, a string list); the validator admits it (`VALIDATOR.md:166` STR-080, `:262` CAP-060, `:295` NET-090). What admits only `host \| router` is the **twin config**, the document the cast writes and the canvas edits: `engine/src/axrange/twinconfig/schema.py:240`, and its expander sends every bridge to a `kind: host` device VM (`twinconfig/expand.py:213-217`). The topology DTO already passes any kind through (`api/service.py:853`). | The new scope is `kind: switch` in the twin config and its expansion to the IR, plus `links[]`, `source` and `members[]` in both. The IR's string-list `nets:` form for a switch is superseded by `links[]` (§10.1). |
| D2 | an `l2-switch` host today becomes a `switch`; a switch deploys as vnets only unless given an image | corp-twin's twelve switch-class nodes are `kind: host, role: l2-switch` **with** an image and `l2: true` legs (`catalog/twins/corp-twin/te.yaml:379-397`), running `scripts/serve-l2switch.sh` from a hand-kept port table whose leg order is load-bearing (`te.yaml:367-373`); its trunks are segments (`sfp25`, `t-ms120-*`, `te.yaml:132`). | Conversion is offered, never automatic: a converted switch keeps its image and so keeps deploying as a VM; its `l2: true` legs stay valid (a switch port with no address) and `links[]` is added beside them. corp-twin deploys byte-identically until the operator converts it. |
| D3 | the validator refuses two segments with one CIDR | The twin config allows exactly that for an `isolated: true` access segment behind a bridge VM and its anchor (`twinconfig/schema.py:134-143`; the NET-010 exemption, `VALIDATOR.md:287`), and corp-twin uses it for every VLAN (`te.yaml:115` and `:152`, `mx-vlan20` / `vlan20`, both 10.180.16.0/20). The canvas keys hosts by CIDR, which is why one of the pair shows no icons (`SESSION_STATE.md`, issues). | The rule is "two segments with one CIDR unless one is the other's `isolated` extension", which NET-010 already is; a duplicate's re-allocated CIDR (§6.2) is refused by the same rule. The canvas keys segments by name. |
| D4 | the assertions seam accepts host roles and segment merges | True: `add` of `conduit \| host \| segment` (`discovery/src/aximprint/operator_assertions.py:71`), `override` of `image \| resources \| role \| segment` on a host and `name \| zone_type` on a segment (`:94`), `suppress` of a host; keys are entity keys and CIDRs (`:112`). The schema is a published contract (`discovery/src/aximprint/schemas/axr-assertions-1.schema.json`) with a byte-identical copy in the engine (`engine/src/axrange/api/schemas/axr-assertions-1.schema.json`, checked by `api/console_files.py`). | The device, link, member, tap, confirm and doubt records of §3.2 are `axr-assertions/2`: a seam version bump, a parser change, the engine's copy, and the console's promote path. §11 decision 2. |
| D5 | `source` on nodes, links, segments, taps | Provenance today is a per-field **side-car** (`<id>.provenance.json`, written by `discovery/src/aximprint/synth/compiler.py:1631-1643`, one record per `twin_config.nodes[i].<field>`), not a field of the node; the config itself says nothing about where a value came from. | Both: the side-car stays the per-field record, `source` is its one-word summary in the config (§3.1). A cast writes both; a hand edit writes `source` only. |
| D6 | A2's live / re-plan binary becomes three change kinds | A2 already classifies into `attribute \| topology \| inert` with `via: [tfc]` or `[tap]` naming the tier (`engine/src/axrange/api/edits.py:63-65`, `:806`, `:820`), and Apply already replays the tfc and tap tiers (`api/service.py:6892-6901`). The binary is the **console's** reading of `kind`. What is not classified today: any `te.yaml` diff other than a node's `role` with its image unchanged is topology (`edits.py:700-750`), so a `members[]` role edit would be topology. | Track 4 is a rename on the wire (`attribute` → `traffic` / `taps` by `via`), the members rule in `classify_te`, and the console's three badges. Smaller than the brief implies. |
| D7 | `members[] {index, persona, intensity, schedule, role}` on the node group | The bundle's tier split **is** the edit-type split (`api/Spec.md:343-349`): persona, intensity and schedule live in the tfc profile, which already addresses an instance by name (`catalog/twins/corp-twin/tfc/default.yaml:37`, `assignments: [{node: corp-workstation-…01, persona, schedule}]`). Putting them on the te-tier group would make a persona edit a `te.yaml` diff. | `members[]` on the group carries `role` only; the traffic fields stay tfc assignments keyed by instance name; the Members tab writes both and shows one row (§4, §5). |
| D8 | discovery proposes a tap placement from the containment claim's vantage; a tap sits on a link | The PIT's `vantage` is `<appliance>:<streams>` (`docs/contracts/SPEC_network_ir.md:399`), the tap's identity, not a place in the network; the tap plan's selector scopes are `port \| segment \| perimeter \| boundary` (`engine/src/axrange/tapscopes.py:30`), and a link does not exist to select. | A proposal is made only when the vantage can be mapped to a segment or device port the twin has (the operator's source record carries that map; absent, the cast emits no tap and says so). A tap "on a link" is a `port` selector on one end (§8). |
| D9 | `vantage` on the containment claim | `twin.yaml`'s `containment: contained` is a claim about egress, checked by `twinconfig/manifest.py` against `te.yaml`'s `egress:` block (`twinconfig/schema.py:405`); vantage lives on the PIT (`ir_pit.vantage` / `vantages`, `SPEC_network_ir.md:399-402`). | `twin.yaml` gains `vantage: [<appliance>:<streams>, …]` beside `containment`, copied from the PIT at cast, so a twin states what its capture could and could not have seen without the store. |
| D10 | the switch class is `l2-switch`, `l3-switch`, read from the API | The API enumerates no role vocabulary (`ui/src/features/edit-canvas/SPEC.md:398-399`); the canvas keeps its own set and it includes `router` and `firewall` (`ui/src/features/edit-canvas/model/roles.ts:101`). | The class comes from `kind` (§2.2); `role` stays free text and the canvas's set goes. No new API route. |
| D11 | the canvas edits attachments, never wires interface to interface | `SPEC.md:12-17` ("a twin has no link objects … never wire interface to interface") and the rendering rules (`SPEC.md:359-363`, "there are no link objects"). | Superseded. The attach gesture gains the device-to-device form (§6.4); the drop-on-segment gesture stays for hosts. The SPEC points here. |
| D12 | the cast of corp-twin has twelve switch nodes with no attachments | corp-twin has no cast; its switches carry `nets[]` with `l2: true` entries (`te.yaml:385-397`), already recorded in `SPEC.md:349-356`. | Premise correction only; the design is unchanged by it. |
| D13 | schema version bump in `IR_SCHEMA.md` and `VALIDATOR.md` | `VALIDATOR.md:159` still states STR-020's set as `{1, 2}` while the code recognises `{1, 2, 3}` (`engine/src/axrange/validator/checks/structural.py:59-61`) and `IR_SCHEMA.md:90-105` records `3`. Both specs say a change "adds a CHANGELOG entry" (`IR_SCHEMA.md:5`, `VALIDATOR.md:5`), which root `CLAUDE.md` rule 3 forbids. | The draft diff (§10) bumps to `4`, corrects the STR-020 row, and names this document as the record in place of a changelog entry. |
| D14 | a `down` or `blocked` link deploys nothing | corp-twin deploys `sfp26` as a segment and lets kernel STP in the bridge VM block it; `P17` is admin-down inside the VM (`te.yaml:367-373`). | Equivalent only for an imageless switch. A switch with an image keeps its script's behaviour; the generated port table marks the port down or blocked. |
| D15 | (2026-11-11, user) segments were drawn as "pills" holding their hosts | `canvas/tree.ts` placed every segment's hosts in a pill box; on simple twins (switch-test) `access-sw` sat above an "office" box, a "servers" box hung off `gw`, and tap chips sat on both | **Decided 2026-11-11:** segments stay in the model and are drawn as switches (§7.1 rules 1–4): a real carrier stands for its segments, a switch-less segment is an implied switch (`vsw:<segment>`, presentation only), a `/30` stays a router–router line. Folding is per switch (§7.6), taps ride switches and a boundary tap the boundary router (§8), plane epoch 7 (§7.8). UI only: no model, schema, validator, engine, deploy-mapping or contract change. |

Not a delta: the uplink marker is the segment-level `egress: true`
(`api/Spec.md:32`, `IR_SCHEMA.md:163`), which §7.1 reads; no router-level
`uplink:` key is needed.

## 10. Draft contract diff (not applied)

Both files are contracts (`docs/contracts/README.md`, "Change rule"): the
user confirms first, the version bumps, code and tests change in the same
commit. The text below is the proposed wording; nothing here is in effect.

### 10.1 `docs/contracts/IR_SCHEMA.md`

```diff
 **Status:** ratified for v1. Changes to this spec must bump
-`scenario_schema_version` and add a CHANGELOG entry.
+`scenario_schema_version` and name the design document that records them
+(git history is the changelog; root `CLAUDE.md` rule 3).
```

§1.5, after the `2→3` note:

```diff
+> **`scenario_schema_version` widened 3→4 (Authority: user, 2026-11-07,
+> `docs/design/topology-layers.md`).** Additive. `layers.infra` gains
+> `links[]` (§3.3); every network, node and link may carry `source`,
+> `confidence` and `vantage` (§3.6); a node may carry `members[]` (§3.2).
+> A `kind: switch` node is wired by `links[]`; its string-list `nets:`
+> form (§3.2, "Switches with `kind: switch`") is accepted and read as one
+> access link per listed network. Declaring `4` is optional, as `2` and
+> `3` are; STR-020 recognises `{1, 2, 3, 4}`.
```

§3.2, the node table (`members[]` is a **twin-config** field: the expander
applies it before the IR exists, so the IR carries the overridden `role` on
the instance and no `members[]`; the twin-config shape is enforced in
`twinconfig/expand.py` like `listen:`):

```diff
 | `role` | no | string | … |
+| `source`, `confidence`, `vantage` | no | | **Additive, `4`.** Provenance, §3.6. |
+| `image` on `kind: switch` | no | string | **`4`.** A switch may omit `image:`; it then deploys as nothing and the planner emits no VM for it. |
```

§3.2, replacing the "Switches with `kind: switch` are rare" paragraph:

```diff
-Switches with `kind: switch` are rare in v1; Proxmox SDN handles inter-vnet
-switching natively. When present, a switch references the networks it
-bridges via `nets: [<id>, ...]` (string-only list, no per-net IP).
+A `kind: switch` node is a device wired by `links[]` (§3.3). It carries no
+address. With an `image` it is deployed as a VM with one L2 NIC per network
+its links carry; without one it is deployed as nothing: the networks its
+links carry exist on their own, and the switch is topology for the operator.
+The older `nets: [<id>, ...]` string list is read as one access link per
+network, `a: {device: <switch>, port: eth<i>}`, and emitted back unchanged.
```

New §3.3 (the old 3.3 is absent from the file; 3.4 and 3.5 keep their numbers):

```diff
+### 3.3 Links
+
+```yaml
+infra:
+  links:
+    - a: { device: ms120, port: P14 }
+      b: { device: sgs-5240, port: Po48 }
+      mode: trunk                    # trunk | access
+      native: vlan1                  # optional; a network id
+      vlans: [vlan20, vlan64]        # network ids; trunk: 1+, access: exactly 1
+      state: up                      # optional; up | down | blocked
+      source: discovered             # §3.6
+      confidence: 0.6
+      vantage: XPress-SW-A:1-4
+```
+
+| Field | Required | Type | Notes |
+|---|---|---|---|
+| `a`, `b` | yes | obj | `{device: <node id>, port: <string>}`; `port` unique per device (NET-120); `a.device != b.device` (NET-130). |
+| `mode` | yes | enum | `trunk` \| `access`. |
+| `native` | no | string | Network id; trunk only (STR-210). |
+| `vlans` | yes | list[string] | Network ids (REF-280); an access link lists exactly one. |
+| `state` | no | enum | `up` (default) \| `down` \| `blocked`. The planner emits nothing for a link that is not `up`. |
+| `source`, `confidence`, `vantage` | no | | §3.6. |
+
+A link whose one end is a `kind: host` node is that host's attachment to the
+link's one network, equivalent to a `net:` / `nets[]` entry; the host keeps
+its address there.
```

New §3.6:

```diff
+### 3.6 Provenance fields
+
+| Field | On | Type | Notes |
+|---|---|---|---|
+| `source` | network, node, link, `members[]` entry | enum | `discovered` (a cast wrote it from a capture) \| `asserted` (an operator assertion re-applied by the cast) \| `authored` (written by hand or by the console) \| `unconfirmed` (a cast's verdict on weak evidence, re-derived each cast). Absent means `authored`. |
+| `confidence` | the same, `source: discovered` or `unconfirmed` only | float | `0.0`–`1.0` (STR-220). |
+| `vantage` | the same | string | `<appliance>:<streams>`, the PIT's `ir_pit.vantage` (`SPEC_network_ir.md`). |
+
+These describe where a value came from, never how to draw it: the layout
+side-car carries presentation. The cast's provenance side-car remains the
+per-field record; `source` summarises it.
```

### 10.2 `docs/contracts/VALIDATOR.md`

```diff
 **Status:** ratified for v1. Changes to rule IDs, severities, or pass
-assignments require a CHANGELOG entry. Adding new rules is a CHANGELOG-able
-addition; …
+assignments are recorded in the design document that makes them (git
+history is the changelog). Adding new rules is such an addition; …
```

§5.1, STR-020's row and two new rows:

```diff
-| `STR-020` | error | `scenario_schema_version` present and recognized (`{1, 2}` — widened 2026-07-24, …) |
+| `STR-020` | error | `scenario_schema_version` present and recognized (`{1, 2, 3, 4}`: widened 2026-07-24, 2026-07-28 and 2026-11-07, `docs/design/topology-layers.md`) |
+| `STR-210` | error | Every `links[]` entry has `a` and `b` as `{device, port}` with non-blank strings, `mode` ∈ {`trunk`, `access`}, `state` ∈ {`up`, `down`, `blocked`} if present, `native` only with `mode: trunk`, and `vlans` a non-empty list (exactly one entry under `access`) |
+| `STR-220` | error | Every `source` on a network, node or link is ∈ {`discovered`, `asserted`, `authored`, `unconfirmed`}; `confidence` is a float in `0.0`–`1.0` and present only with `discovered` or `unconfirmed` (a `members[]` index outside `1…count`, or repeated, is a twin-config shape error at expansion) |
```

§5.3, two new rows:

```diff
+| `REF-270` | error | Every `links[].a.device` / `.b.device` resolves to a defined node (any kind) | — |
+| `REF-280` | error | Every `links[].vlans[]` entry and `links[].native` resolves to a defined network | — |
```

§5.6, three new rows:

```diff
+| `NET-120` | error | No device uses one `port` name on two links (`a` and `b` ends counted together) |
+| `NET-130` | error | A link's two ends are different devices; a network marked `isolated: true` shares its CIDR with exactly one non-isolated network (its anchor), and no two non-isolated networks share one CIDR (NET-010 states the overlap rule; this states the anchor rule the twin-config expander enforces today) |
+| `NET-140` | warning | A `kind: switch` node is named by no `links[]` entry: a parked device (the canvas blocks Apply and Save on it) or the pre-device-tree `nets:` spelling; a switch with no `nets:` at all is halted by STR-070 first |
```

§5, the count paragraph:

```diff
-96 IR-side rules across 10 categories (… = 114 total). The per-category
-counts are: STR 20, INC 5, REF 25, …, NET 10, …
+103 IR-side rules across 10 categories (plus 7 cluster rules executed by the
+planner, plus 11 catalog-side persona rules = 121 total). The per-category
+counts are: STR 22, INC 5, REF 27, CAP 6, LIF 6, NET 13, OVR 4, ACT 7, DEF 5,
+FAB 8 (= 103 IR-side), plus CLU 7, plus PSN 11. (… +STR 2 (`STR-210`
+links shape, `STR-220` provenance and members shape) +REF 2 (`REF-270`,
+`REF-280` link references) +NET 3 (`NET-120` port reuse, `NET-130` link ends
+and the isolated anchor, `NET-140` orphan switch) / 114→121 with the device
+tree, `docs/design/topology-layers.md`, 2026-11-07.)
```

Twin-config shapes (`kind: switch` on a group, `links[]` and `members[]`
under `twin_config`, `source` on a segment or group) are enforced where the
author writes them, in `twinconfig/expand.py`, the way `listen:` is
(`IR_SCHEMA.md:362`); the IR rules above fire on the expanded IR.

## 11. Needs sign-off

The brief's open decisions, verbatim, each with a recommendation.

1. **"`kind: switch` and `links[]` in the IR is new scope: schema version
   bump in `docs/contracts/IR_SCHEMA.md` and `VALIDATOR.md`; needs
   sign-off."**
   Recommend **yes, as §10 drafts it**: `scenario_schema_version` `3 → 4`,
   additive, every shipped IR unchanged; `kind: switch` already exists in the
   IR and gains `links[]` as its wiring (D1); the twin config gains `kind:
   switch`, `links[]`, `members[]` and `source`. Seven new validator rules.

2. **"Assertions seam extended to devices, links, member splits and tap
   placement: contract change, needs sign-off."**
   Recommend **yes, as `axr-assertions/2`** (§3.2): new `add` / `override`
   kinds `device`, `link`, `tap`, a `members` override keyed by member entity
   key, and `confirm` / `doubt`. The parser, the published schema file, the
   engine's vendored copy (`api/schemas/`) and the console's promote path change together;
   version 1 files keep parsing. This is the largest piece of track 3.

3. **"Does a `switch` with an image deploy as a VM (e.g. a VyOS/Linux bridge)
   or only as its vnets? Default proposed: vnets only unless an image is
   given."**
   Recommend **the default as proposed**, with one addition (D2): a switch
   with an image deploys as a VM whose port table is generated from `links[]`,
   which is what corp-twin's bridge VMs need to keep deploying as they do
   when they are converted. An imageless switch deploys nothing and a `down`
   or `blocked` link on it deploys nothing.

4. **"Member override by index vs by instance name: index proposed (stable
   under re-cast if the roster order is stable; verify)."**
   Verified: the expander mints `{name}{index:02d}`
   (`twinconfig/expand.py:376`), and the cast's roster lists a group's
   members as its **sorted entity keys** (`compiler.py:1635-1643`), so
   instance `02` is the second key. Index and name are the same ordinal; both
   move when a host enters or leaves the group between captures. Recommend
   **index in the config** (no string parsing, and the tfc tier keeps
   addressing by instance name, which is the same thing), **and the entity key
   in the assertion** that records a member edit, so a re-cast re-derives the
   index from the key and the override follows the host, not the slot (§3.2).

## 12. Build order

One commit per track, its suite green, staged by path.

| track | scope | touches | size |
|---|---|---|---|
| 1 | this document; SPEC pointers; `SESSION_STATE.md` | docs | done |
| 2 | IR: `kind: switch` on a twin-config group, `links[]`, `source` / `confidence` / `vantage`, `members[]`, `twin.yaml` `vantage`; the expander's switch-to-vnets mapping and port-table generation; the seven validator rules; the `3 → 4` bump; the contract files as §10 | `twinconfig/schema.py`, `expand.py`, `manifest.py`, `ir/`, `validator/checks/`, tests; `IR_SCHEMA.md`, `VALIDATOR.md` | **M**: about two sessions, ~900 lines with tests |
| 3 | cast: switch-class roles emitted as `kind: switch`, observed adjacency as `unconfirmed` links, a tap from a mappable vantage; `axr-assertions/2` (parser, schema file, engine copy); assertions re-applied after cast with the unkeyed report; member records keyed by entity key | `aximprint/synth/compiler.py`, `operator_assertions.py`, `schemas/`, `api/console_files.py`, `api/service.py` cast route, tests | **L**: three to four sessions; the seam bump is most of it |
| 4 | A2: `traffic` / `taps` on the wire by `via`; `members[].role` classified in `classify_te`; the console's three badges and the Checks tab | `api/edits.py`, `api/service.py`, `api/Spec.md`; `ui/` Checks tab | **S**: one session, ~300 lines |
| 5 | canvas: dagre device tree, five icons, hover, inspector by tier, folding, router hues, the overlay, Members tab, split / merge / detach, subtree select / duplicate / attach, taps; plane epoch | `ui/src/features/edit-canvas/` (replaces `layout/` and the bus modules), `package.json` + lock | **XL**: five to eight sessions, ~3-4k lines; land as 5a tree + icons + hues + folding, 5b inspector + overlay + taps, 5c members, 5d subtree duplicate / attach |
| 6 | verify by eye on canvas-test, datacenter, corp-enterprise, corp-twin and the largest cast twin; the acceptance checklist into the SPEC | docs, screenshots in the scratchpad | **S**: one session |

Track 2 blocks 3, 4 and 5. Track 5a can start on 2 alone; 5b needs 3; 5c needs 4.
