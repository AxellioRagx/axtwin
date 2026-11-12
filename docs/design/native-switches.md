# Native switches and SPAN taps

**Status:** decisions D1–D12 signed off by the user on 2026-11-12, in the task
brief "native switches + SPAN taps". This document records them. Phase 0, the
design and contract diff, is done; Phases 1–2 are being built on
`wip/design-platforms-canvas`, which the user chose to continue on rather than
open a second `wip/*` branch. Until Phase 1 lands, the contract edits it names
(§10) stay in the working tree, uncommitted: `docs/contracts/README.md` requires
a contract change to ship in the same commit as its code and tests.

**Loop step:** **deploy**, plus the **capture gate**. A switch drawn on the
canvas becomes a switch on the node, and a SPAN session on its port feeds the
sensor whose climbing counters are the acceptance test. **Customize** follows in
Phase 2, where the inspector edits the span binding.

**Partly reverses:** `topology-layers.md` §2.5 ("an imageless switch deploys as
nothing") and §9 D14, for adapters that declare `native-switch`. It also
reverses `ADAPTER.md` §8.3 in one narrow respect: the adapter again owns OVS
bridges outside Proxmox SDN, but only for native switches (§4.1). Segments no
native switch carries stay SDN vnets, unchanged.

## 1. The problem

Today each segment is one flat SDN vnet, so the deployed L2 is one switch per
VLAN. The consequences:

- the switch tree is drawn but not emulated;
- a link in `state: down` or `blocked` cuts nothing;
- a `port` tap resolves only to a VM interface (`planner/tappoints.py`), so
  nothing can tap a switch.

corp-twin works around this with bridge VMs (`kind: host, role: l2-switch`,
`scripts/serve-l2switch.sh`) and "isolated" partner segments that share a CIDR
(`topology-layers.md` §9 D2, D3, D14). The workaround is what gives the canvas
the dangling `mx-vlan20` and the switch drawn under a switch.

**The mapping this design rests on:** a Proxmox vnet is a VLAN, one broadcast
domain; it is not a switch. Switches are never mapped to vnets. A segment stays a
segment (CIDR, addressing, VLAN ID), and a native switch carries segments.

## 2. Model

### 2.1 What a switch resolves to (D4)

A `kind: switch` node resolves to one of three things. The **planner** decides,
because it is the first stage that knows the target platform. The twin-config
expander stays platform-agnostic, and the IR records only what the author wrote.

| the node | adapter declares `native-switch` | resolves to |
|---|---|---|
| `kind: switch`, `image:` set | either | **VM**, as today |
| `kind: switch`, no `image:` | yes | **native**: an adapter-owned switch (§4) |
| `kind: switch`, no `image:` | no | **logical**: deploys as nothing, as today; NSW-020 warns |

Discovery casts emit imageless switches, so they deploy native by default.
corp-twin's bridge VMs are `kind: host` and are not touched (D11).

### 2.2 New field: `stp` (D6)

`stp: rstp` is an optional field on a `kind: switch` node, in the twin config
and in the IR. It is absent by default, which means STP is off. It is admitted on
any switch but takes effect only on a native one; on a VM or logical switch it
warns (NSW-070). It is **Phase 3**: Phase 1 parses it and refuses `rstp` on a
native switch with "RSTP is phase 3" (NSW-070, error until then).

### 2.3 Segment hosting (D1)

A segment is **switch-hosted** when at least one native switch carries it: the
segment is named in the `vlans[]` / `native` of an `up` link with a native-switch
end, or in that switch's own L2 leg. Every other segment is **SDN-hosted** and is
planned exactly as today (one vnet; the zone type derived as today).

- A segment is SDN-hosted or switch-hosted, never both (NSW-030). There is no
  "half": every VM leg on a switch-hosted segment attaches to a switch port.
- A twin with no native switch emits byte-identical ops (§11, the golden
  check). A twin where every segment is switch-hosted still gets its SDN zone
  only if some segment is SDN-hosted. The zone, its underlay and
  `apply-sdn-config` are emitted only when the SDN set is non-empty.
- A switch-hosted segment needs a VLAN ID for OVS tagging. It uses the segment's
  `vlan:` when it is set; otherwise the planner assigns one deterministically,
  the lowest free ID from 2 upward in segment order. Tags are local to the
  switches of one twin, so CLU-050's cross-scenario clash check does not apply
  to them.

### 2.4 Ports and links (D2, D5, D6)

Every port on a native switch comes from one of two sources:

1. **An explicit `links[]` entry** naming the switch. These always win (D5).
2. **An automatic access port** (D5). A host or router leg that says only `net:`
   or a `nets[]` entry, on a switch-hosted segment, and that no link already
   attaches, gets an access port on that segment's **deepest carrier**. That is
   the native switch farthest from the router along `up` links that carries the
   segment: the same rule the canvas uses to hang hosts (`topology-layers.md`
   §7.1 rule 1). The port is named `<instance>.<nic_index>` (for example
   `corp-workstation01.0`). Port names are unique per switch (NET-120), and no
   authored name contains a host name followed by a dot and a digit, so these
   cannot collide. If two carriers tie at the deepest depth, NSW-040 refuses
   and asks for an explicit link.

| link | on the native switch it becomes |
|---|---|
| `access`, far end a VM (host or router) | the VM's NIC for that segment, plugged into the bridge with `tag=<vlan>` |
| `trunk`, far end a VM | the VM's NIC, `trunks=<vlans>`, with `tag=<native>` and `vlan_mode=native-untagged` when `native` is set. The guest has to handle the tags. In v1, NSW-060 refuses a VM-end trunk that carries anything beyond its native VLAN; see §12 S3 |
| `access` or `trunk`, both ends native switches | an OVS patch-port pair, one port on each bridge, each with the same tag or trunk set |
| far end a VM switch or a logical switch | refused (NSW-050): v1 does not bridge native switches to VM switches |

`state` on a link, on the native-switch side (D6):

| `state` | effect |
|---|---|
| `up` (default) | forwarding |
| `down` | the port is admin-down. On a VM port this is the NIC's `link_down=1` in the VM config: persistent, and the guest sees carrier loss. On a patch port it is the OpenFlow port flag `down` on both ends, re-asserted by `create-switch` on every run |
| `blocked` | the port exists but does not forward. With `stp` off, it carries the OpenFlow flags `no-forward,no-flood,no-receive`. With `stp: rstp` (Phase 3), STP decides and the authored value is only advisory |

The OpenFlow flags do not survive a port being re-created: Proxmox removes and
re-adds a VM's tap on every boot. So every OVS fact that is tied to a VM port
(a `blocked` flag, a span selection) is re-asserted by the VM's post-start hook.
This is the mechanism `install-ovs-mirror` already relies on (`OP_CATALOG.md`
§5.3), extended in §6.3.

### 2.5 What a native switch does not do in v1

- **No address of its own.** Running an SVI or management address would put an
  IP on the Proxmox node. NSW-080 warns, and the address is not deployed (§12
  S2).
- **No routing.** An `l3-switch` resolved native forwards at L2 only. Its
  gateway role stays with the twin's routers (§12 S2).
- **No cross-node switches** (D3, Phase 3).
- **No firewall guarantee** (D9, §8).

## 3. Placement (D3)

v1 is single-node: every native switch of a twin, and every VM attached to one,
lands on one Proxmox node. Placement today is already one node per plan
(`planner/pass4_placement.py` places everything on `ctx.default_node`), so the
pin holds by construction. The rule still has to be written down and enforced
for the day placement learns least-loaded (`PLANNER.md` §3.4):

- Pass 4 places every switch-attached VM on the node it places the twin's native
  switches on. It records that as `placement["switch:<name>"]`, the same way it
  records hosts.
- CLU-080 (new, planner-side) refuses the plan when that node cannot satisfy
  every switch-attached VM's `host:*` requirements, or when a VM's recorded
  prior placement is on another node. The message names the VM and both nodes.

## 4. Proxmox mapping (D2)

### 4.1 Bridge ownership

There is one OVS bridge per native switch, created with `ovs-vsctl` over the SSH
backend. It is **not** created as an SDN object, and it is **not** written into
the node's `/etc/network/interfaces`:

- Through the REST node-network API, the bridge would need an `ifreload -a` to
  commit. `ifreload` briefly detaches running VMs' taps on that node (ADAPTER
  §8.1, the 2026-05-29 finding), which disturbs every other twin on the node.
- A bridge that exists only in OVSDB survives `ifreload` and SDN applies,
  because ifupdown2 manages only the interfaces it declares. It is lost on a
  node reboot, after which the twin is redeployed: `create-switch` is
  check-then-act and recreates it.

The bridge carries `external_ids:axrange-tier=te`,
`external_ids:axrange-deploy=<deploy_id>` and `external_ids:axrange-switch=<name>`.
Its name, its fail mode (`standalone`: it learns MACs without a controller) and
`mcast_snooping_enable=false` are set on create and re-asserted on adopt.

ifupdown2 ignoring an undeclared OVS bridge, and a bridge surviving an SDN apply
of another twin, are both checked live in Phase 1 (§12 R1).

### 4.2 Names (`adapter_proxmox/naming.py`)

| resource | producer | shape | length | tier |
|---|---|---|---|---|
| switch bridge | `switch_bridge_for(plan_id, switch)` | `tesw_<8hex>` | 13 | `te` |
| patch port | `patch_port_for(plan_id, link_key, end)` | `tepp_<8hex>` | 13 | `te` |
| span mirror (OVSDB name, not an ifname) | `span_mirror_for(plan_id, binding_key)` | `tapsm_<8hex>` | 14 | `tap` |
| span gretap | existing `gre_port_for_bridge_sensor(bridge, sensor)` | `tapg_<6hex>` | 11 | `tap` |

All of these fit IFNAMSIZ. `tesw_` and `tepp_` are disjoint from `tebr_` at the
third character, and from `te<6hex>` and `tev<5hex>` by both length and the
underscore. `tapsm_` is disjoint from `tapm-<vmid>` and `tapg_` at the fourth
character. The hex is `sha256(...)[:8]`, the hashing convention of
`_short_plan_id`. The `naming.py` round-trip test gains the new producers and
predicates. VM ports keep Proxmox's own names (`tap<vmid>i<n>`); the adapter
does not rename them.

### 4.3 Teardown

`planner/teardown.py` gains `is_te_switch_bridge` and `is_te_patch_port` as
positive matches, and `is_tap_span_mirror` for the tap tier. The protect guard is
unchanged. A `te` destroy:

1. removes every `tapsm_*` mirror whose bridge is one of the deploy's `tesw_*`
   bridges (a cascade to `tap`, as today);
2. removes the deploy's `tesw_*` bridges, matched by name **and**
   `external_ids:axrange-deploy`; `del-br` takes their patch ports and any
   leftover VM taps with them;
3. confirms that `ovs-vsctl list-br` shows none of them and that `list Mirror`
   shows no `tapsm_*` on the node. A survivor is a teardown failure, not a
   warning.

## 5. Ops (D2, D6, `OP_CATALOG.md`)

Every op that touches a node's OVS holds the existing `ovs:$target_node` lock
category. These are new ops, so the existing ops keep their signatures.

| ID | name | per | after | backend |
|---|---|---|---|---|
| OP-NET-060 | `create-switch` | native switch | — | SSH |
| OP-NET-070 | `link-switches` | switch↔switch link | `create-switch[a]`, `create-switch[b]` | SSH |
| OP-HOST-110 | `attach-vm-to-switch-port` | host@net on a switch-hosted segment | `configure-cloud-init[$host]`, `create-switch[$switch]` | REST (NIC) + SSH (verify) |
| OP-NET-080 | `set-port-state` | port with `state` other than `up` | the op that made the port | REST (`link_down`) / SSH (OpenFlow flags) |
| OP-MIR-060 | `install-span` | span session (tap tier) | `attach-gre-tunnel[$bridge@$sensor]` | SSH |

- `network-ready[$net]` for a switch-hosted net depends on every
  `create-switch` and `link-switches` that carries it, in place of
  `create-vnet` and `apply-sdn-config`.
- `attach-vm-to-switch-port` replaces `attach-vm-to-vnet` for that leg. It
  publishes the same `hosts[].nets[]` record (with `nic_index`), plus `switch`
  and `port`, so tap resolution and the inventory see one shape.
- `start-vm` keeps `attach-*[$host@*]` as its predecessors, whichever form each
  one takes.
- Idempotency (ADAPTER §4): every op checks before it acts:
  - `create-switch`: `br-exists`, then adopt;
  - `link-switches`: patch peers present and pointing at each other;
  - `attach-vm-to-switch-port`: the NIC string matches, compared field by field
    as `attach-vm-to-vnet` does today;
  - `set-port-state`: the flag or `link_down` already matches;
  - `install-span`: a mirror with that name and selection exists.

  An adopted resource whose attributes drifted is corrected in place (the
  gretap-qdisc pattern), not recreated.

## 6. SPAN taps (D7, D8)

### 6.1 The binding

The tap plan gains a fourth addressing form beside `tap`, `tap_point` and
`selector`. Exactly one of the four appears per binding:

```yaml
tap-plan:
  target: switch-test
  bindings:
    - span:
        switch: access-sw
        sources:
          - { port: SFP1, dir: both }                # the uplink, both directions
          - { port: corp-workstation01.0, dir: rx }  # an auto port, ws01's transmit
          - { port: Po1, dir: tx, vlans: [office] }  # a trunk, office only, toward the far end
      sensors: [XPress-SW-A]
      mode: exclusive
```

| field | rule |
|---|---|
| `switch` | a native switch of the target twin (refused at `tap plan` otherwise: a logical switch has no ports to mirror) |
| `sources[].port` | a port on that switch, explicit or automatic, as listed in the inventory's `switches[].ports[]` |
| `sources[].dir` | `rx` (frames the switch **receives** on the port: the far end's transmit) \| `tx` (frames the switch sends out of it) \| `both` |
| `sources[].vlans` | optional; segment names carried by that port. It narrows the session to those VLANs. Because an OVS mirror applies one `select_vlan` to the whole mirror, sources that disagree on `vlans` compile to one mirror per distinct set |

Directions follow switch-port convention, which is what a real SPAN session
means. A `P1 rx` span on ws01's port sees what ws01 sends.

### 6.2 Resolution

A span binding resolves to one OVS mirror on the switch's bridge:

- `select_src_port` = the `rx` and `both` ports;
- `select_dst_port` = the `tx` and `both` ports;
- `select_vlan` = the VLAN IDs, when narrowed;
- `output_port` = the keyed gretap toward the sensor.

`attach-gre-tunnel` creates the gretap unchanged; it is keyed per
`(bridge, sensor)`, so it comes out as `tesw_<…>` × sensor. `install-span` then
adds the gretap to the switch bridge as a port carrying every VLAN, so the
mirrored frames keep their 802.1Q tags as a real SPAN session with
encapsulation-replicate does (see R3). OVS mirrors each packet at most once per
mirror, so a frame that matches both a source port and a destination port is not
duplicated.

`dir: tx` also becomes a value of the general direction vocabulary
(`tapscopes.py`). `install-ovs-mirror`'s per-NIC `direction` admits `"tx"`,
which installs only the egress `mirred` filter. This addition is backward
compatible, because an absent `direction` still means `both`. Note the
viewpoint: the `tc` mirror's `rx` is ingress **on the VM's tap**, which is what
the VM transmits, the same frames as a switch port's `rx`. The two vocabularies
agree.

### 6.3 Existing scopes on switch-hosted segments (D8)

On an SDN-hosted segment, `port`, `segment`, `perimeter` and `boundary` keep
today's per-tap `tc` mirror, byte for byte. On a switch-hosted segment they
compile to an OVS mirror on the carrier switch or switches. The direction rule
does not change:

| scope | compiles to |
|---|---|
| `port` (one VM leg) | its switch port, `both` |
| `segment` | every **edge** port carrying the segment, across all its carriers, `rx`, with `select_vlan=<tag>` on trunk-edge ports. Edge ports are VM ports. Inter-switch patch ports are excluded, so each frame is mirrored once, where it enters the fabric |
| `perimeter` | every router port on the segment, `both` |
| `boundary` | the joining router's port on each of the two segments, `rx` |

One mirror is created per `(switch, binding)`. A scope that spans several
carriers yields one mirror on each, each with its own gretap.

Mirror selections name OVSDB Port rows. A VM boot removes and re-adds its tap,
and OVS drops a deleted port from every mirror. So `install-span` also writes the
mirror's selection by **port name** into the post-start hook that
`install-ovs-mirror` already installs, and the hook re-adds the re-created port
to the mirror. The hook gains an `ovs` stanza beside its `tc` one; the per-NIC
`tc` path is unchanged.

### 6.4 `verify-capture`

`verify-capture` (OP-MIR-040) takes a span session the way it takes a bridge:
it injects the sentinel from a VM on a source port of the mirror and reads the
sensor. For an external sink (`XPress-SW-A`) it is skipped with the existing
warning. Capture there is confirmed on the appliance: the gre-cap RX counter,
plus a GRE `tcpdump` on the node.

## 7. Inventory

`planner/inventory.py` publishes `switches[]` beside `hosts[]`:

```yaml
switches:
  - name: access-sw
    resolved: native            # native | logical | vm
    bridge: tesw_1a2b3c4d
    pve_node: dev45
    stp: off
    ports:
      - port: SFP1
        mode: trunk
        native: office
        vlans: [office]
        state: up
        source: link            # link | auto
        far: { kind: router, device: gw, nic_index: 0 }
      - port: corp-workstation01.0
        mode: access
        vlans: [office]
        state: up
        source: auto
        far: { kind: host, device: corp-workstation01, nic_index: 0 }
```

`hosts[].nets[]` keeps its shape and gains `switch` and `port` on switch-hosted
legs. `net_bridge` keeps mapping only SDN-hosted segments. A switch-hosted
segment's bridges are found through `switches[]`, because one segment can sit
on several bridges.

## 8. Proxmox firewall on native-switch ports (D9)

Native-switch bridges are outside the SDN view: they are not in the SDN GUI, and
IPAM does not see them.

**Finding (from the Proxmox source of `qemu-server` and `pve-firewall`; to be
confirmed live in Phase 1):** pve-firewall acts on a VM NIC only when the NIC
carries `firewall=1`. On an OVS bridge, `firewall=1` makes `qemu-server` insert a
per-NIC Linux bridge (`fwbr<vmid>i<n>`) with a veth pair. The OVS port is then
`fwln<vmid>o<n>`, not the tap, and the VLAN tag sits on that port. axRange never
sets `firewall=1`, so its VM NICs have none, and the tap is the OVS port.

`attach-vm-to-switch-port` writes `firewall=0` explicitly, so the port the
mirror, `blocked` and `link_down` act on is always the tap, even when a template
default enables the firewall. v1 makes no firewall guarantee on a native
switch, and the cluster's datacenter firewall does not apply to traffic between
two ports of one native switch.

## 9. Validator rules (`validator/checks`, `VALIDATOR.md`)

These are Pass 3 rules (`NSW`, a new category) unless noted. "Native" is the
resolution of §2.1. The validator gains the adapter's capability set as an input
(`validate_full(..., platform_caps=...)`). Without it (a bare `axrange
validate`), the rules that depend on resolution assume no capabilities and do not
fire, and the planner runs them again with the target known.

| ID | sev | rule |
|---|---|---|
| NSW-010 | error | A `kind: switch` without an image is resolved native only when the platform declares `native-switch` (a capability check, planner-side when no platform is given) |
| NSW-020 | warning | A `kind: switch` without an image on a platform without `native-switch` is logical: it deploys as nothing, and its `state` and taps have no effect |
| NSW-030 | error | A segment is SDN-hosted or switch-hosted, never both: no leg on a switch-hosted segment attaches outside a native switch (an `isolated` partner segment counts as its own segment) |
| NSW-040 | error | A leg that gets an automatic port has exactly one deepest carrier. Otherwise: "ambiguous carrier for <host> on <segment>: <sw1>, <sw2>; add an explicit link" |
| NSW-050 | error | Every port named in a link, a span source or `set-port-state` exists on its switch, and every `vlans[]` / `native` named on a native-switch port is a segment that port's link carries. A native switch links only to VMs and native switches |
| NSW-060 | error | A link with a VM end carries exactly one segment the VM has a leg on (access, or trunk with only `native`). v1 does no in-guest VLAN tagging (§12 S3) |
| NSW-070 | error | `stp: rstp` on a native switch: RSTP is Phase 3. A warning on a VM or logical switch: it has no effect |
| NSW-080 | warning | An addressed leg on a native switch: the address is not deployed (§2.5) |
| CLU-080 | error | Planner-side (Pass 4): every native switch, and every VM attached to one, is placeable on one Proxmox node (§3) |

## 10. Contract diff

Edited in place in the working tree and committed with the Phase 1 code
(`docs/contracts/README.md`, the change rule):

| file | change |
|---|---|
| `IR_SCHEMA.md` | `scenario_schema_version` 4→5 (additive): `stp` on a `kind: switch` node; §3.2 adds the native/logical/VM resolution and drops "an imageless switch deploys as nothing" as the only behaviour. §3.3 says a link `state` acts on a native switch's port. Declaring `5` is optional; the expander declares it only for a config that sets `stp` |
| `VALIDATOR.md` | STR-020 adds `5`; a new NSW category (NSW-010…080); CLU-080; the counts paragraph |
| `PLANNER.md` | §3.1 emits the switch ops; §3.4 switch placement and the single-node pin; §8.1 the `ovs:` category on the new ops |
| `OP_CATALOG.md` | OP-NET-060/070/080, OP-HOST-110, OP-MIR-060; `tx` in `install-ovs-mirror`'s `direction`; the summary table: 40 → 45 op types |
| `ADAPTER.md` | the new rows in §3; §4's name table; a §8.4 note that native-switch bridges are adapter-owned OVS outside SDN (this design), with SDN-native unchanged for every other segment; the `native-switch` capability |
| tap-plan contract | it lives in `o2control/taps.py`'s module text and `ARCHITECTURE.md` §8, not under `docs/contracts/`; both gain the `span` binding and `tx` (§6) when Phase 2 lands |

The platform seam (`platform/protocols.py`, `ARCHITECTURE.md` §13) gains
`capabilities: frozenset[str]` on the adapter bundle. Proxmox declares
`{"native-switch"}` once Phase 1 is live-verified; libvirt declares nothing
until D10 is implemented.

## 11. Acceptance

- **Golden.** `axrange plan` for canvas-test, datacenter, corp-twin and
  corp-enterprise is byte-identical to its output before this branch. The
  goldens are captured from `0f92b04` into the engine test fixtures before any
  code changes.
- **Engine and UI suites:** `cd engine && pytest -q -n auto`, then
  `cd ui && npm run typecheck && npm test`.
- **Live on dev45,** with `--images` and `--db` passed explicitly and logs
  kept with the state DB:
  1. switch-test deploys;
  2. the workstations reach `file-server` through `gw`;
  3. the span on `access-sw` (SFP1 `both`) makes `XPress-SW-A`'s gre-cap RX
     climb;
  4. a `rx` span on ws01's port sees only ws01's transmit;
  5. `state: down` on a port cuts that host;
  6. destroy leaves no `tesw_*`, `tepp_*` or `tapsm_*` on the node.

## 12. Risks and the items not signed off

**Risks**, each checked live in Phase 1 before it is relied on:

- **R1, bridge persistence.** An OVSDB-only bridge survives `ifreload -a`
  (another twin's SDN apply) on PVE 9.2. If it does not, the fallback is the
  REST node-network API, which costs the tap-detach disturbance of ADAPTER
  §8.1.
- **R2, IFNAMSIZ.** Every new name is 14 characters or fewer by construction
  (§4.2). The risk is a future producer, which the round-trip test guards.
- **R3, tagged SPAN output.** The mirror emits 802.1Q-tagged frames on trunk
  and access traffic alike, because OVS mirrors carry the internal VLAN. The
  external sink and the in-guest flow decap must accept tagged inner frames. If
  `XPress-SW-A` does not count them, the gretap is made an access port of a
  per-session VLAN. That costs the tag, and the change is local to
  `install-span`.
- **R4, placement pinning.** Trivially true today (one node per plan). CLU-080
  is what keeps it true when placement grows.
- **R5, port re-creation.** OpenFlow port flags and mirror port references
  are lost when a VM reboots; the post-start hook re-asserts them (§2.4,
  §6.3). This is verified by a VM restart in the live run.

**Not covered by D1–D12.** The engine work in Phase 1 does not depend on these,
but the fixture and the edge cases do:

- **S1, links to group members.** A link cannot name a `count > 1` group or one
  of its members today (`topology-layers.md` §3.2: the expander refuses it; the
  remedy is a split). So switch-test's "explicit access links to both
  workstations" cannot be written while `corp-workstation` is `count: 2`. The
  options:
  - (a) let a link end name an emitted instance (`corp-workstation01`), which
    is new twin-config scope;
  - (b) split the group into two ×1 nodes in the fixture;
  - (c) drop the explicit links and let D5's auto ports attach them.
- **S2, an addressed leg or `l3-switch` on a native switch.** This design
  warns and does not deploy the address (NSW-080) and forwards at L2 only. The
  alternative is to resolve such a switch as logical.
- **S3, a VM-end trunk carrying more than its native VLAN.** This needs
  in-guest 802.1Q subinterfaces, which `configure-router` does not do; NSW-060
  refuses it in v1.
