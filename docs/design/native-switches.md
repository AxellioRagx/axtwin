# Native switches and SPAN taps

**Status:** decisions D1–D12 signed off by the user on 2026-11-12, in the task
brief "native switches + SPAN taps"; §12 S1–S3 decided the same day (S1: a
link may name a group member by instance id; S2 and S3 as proposed). Phase 0
(design and contract diff) is done. Phase 1 (Proxmox single-node native
switches) is built on `wip/design-platforms-canvas`, which the user chose to
continue on rather than open a second `wip/*` branch; its contract edits (§10)
landed with its code (`docs/contracts/README.md`). Phase 2 (SPAN on native
switches, the canvas editor) is built and live-verified on dev45 (§11.2).
R5 and Phase 3 (RSTP, the libvirt adapter) are built on the user's go,
2026-11-12; Phase 3's cross-node switches and the corp-twin conversion offer
are stopped for decisions (§12).

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
warns (NSW-070). On a native switch (Phase 3) `create-switch` sets the
bridge's `rstp_enable=true`; VM ports are edge ports by OVS's own
auto-edge detection and forward within seconds, switch links run the
protocol. A loop of native switches over `up` links must run RSTP on every
switch in it (NSW-090): without it the second link would be a broadcast storm
on the node.

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
   §7.1 rule 1); depth is the distance from a router over `up` links. The port
   is named `<instance>.<leg>`, the leg being the network's position in the
   node's `nets[]` (for example `corp-workstation01.0`); a collision with an
   authored port name is refused (NSW-050). If two carriers tie at the deepest
   depth, NSW-040 refuses and asks for an explicit link.

A link end may name a group member by its instance id (`corp-workstation01`),
so each member of a `count > 1` group can be wired to its own port (§12 S1).
The group name itself is still refused as a link end.

| link | on the native switch it becomes |
|---|---|
| `access`, far end a VM (host or router) | the VM's NIC for that segment, plugged into the bridge with `tag=<vlan>` |
| `trunk`, far end a VM | the VM's NIC, `trunks=<vlans>`, with `tag=<native>` and `vlan_mode=native-untagged` when `native` is set. The guest has to handle the tags. In v1, NSW-060 refuses a VM-end trunk that carries anything beyond its native VLAN; see §12 S3 |
| `access` or `trunk`, both ends native switches | a kernel veth pair, one end a port on each bridge, each with the same tag or trunk set (an OVS patch pair until R6, 2026-11-12) |
| far end a switch with an image | a VM port like any other: that switch is a VM |
| far end a logical switch | refused (NSW-050): there is nothing deployed to plug into |

`state` on a link, on the native-switch side (D6):

| `state` | effect |
|---|---|
| `up` (default) | forwarding |
| `down` | the port is admin-down. On a VM port this is the NIC's `link_down=1`, written by `attach-vm-to-switch-port` itself: persistent in the VM config, and the guest sees carrier loss. On a switch-link port it is the OpenFlow port flag `down` on both ends, asserted by `link-switches` on every run |
| `blocked` | the port exists but does not forward: the OpenFlow flags `no-receive,no-forward,no-flood`. On a switch-link port `link-switches` asserts them; on a VM port `set-port-state` does, after `start-vm` (the tap exists only once the VM runs). With `stp: rstp` on both ends of a switch link (or on a VM port's switch), STP decides and the authored value is advisory: no flags are set and no `set-port-state` is emitted |

The OpenFlow flags do not survive a port being re-created: Proxmox removes and
re-adds a VM's tap when the VM stops and starts (a guest reboot keeps it). So
`set-port-state` also writes a **fragment** that the VM's start hook runs
(R5, built 2026-11-12): Proxmox allows one hookscript per VM and the `tc`
mirror already owns it, so every axRange hook runs
`/var/lib/vz/snippets/axrange-ovs-<vmid>-*.sh` on `post-start`, and an op
writing a fragment registers an empty axRange hook on a VM that has none,
regenerates an older axRange hook from its own recorded mirror pairs, and
refuses a foreign hookscript. On libvirt the fragments sit under
`/etc/libvirt/hooks/axrange-ovs/` and the domain's qemu hook runs them.
`down` needs none: `link_down` lives in the VM config. A switch link is a
veth pair, which a VM restart does not touch.

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

- Pass 4 places every native switch under its own node id in `placement`, as
  it already places every IR node, beside the VMs attached to it.
- CLU-080 (new, planner-side, `pass4_placement.py`) refuses the plan when a
  native switch and a VM attached to one land on different nodes; the message
  names both. Checking a VM's recorded prior placement against it is part of
  the same rule once placement can move a VM (it cannot today).

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
`external_ids:axrange-plan=<plan_id>` and `external_ids:axrange-switch=<name>`
(labels for an operator; anything outside `[A-Za-z0-9._-]` becomes `_`).
Its name, its fail mode (`standalone`: it learns MACs without a controller) and
`mcast_snooping_enable=false` are set on create and re-asserted on adopt.

ifupdown2 ignoring an undeclared OVS bridge, and a bridge surviving an SDN apply
of another twin, are both checked live in Phase 1 (§12 R1).

### 4.2 Names (`adapter_proxmox/naming.py`)

| resource | producer | shape | length | tier |
|---|---|---|---|---|
| switch bridge | `switch_bridge_for(plan_id, switch)` | `tesw_<8hex>` | 13 | `te` |
| switch-link port (a veth end) | `patch_port_for(plan_id, link_key, end)` | `tepp_<8hex>` | 13 | `te` |
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
2. removes each `tesw_*` bridge recorded by the deploy's `create-switch` ops,
   only when it is exactly the name `switch_bridge_for(plan_id, switch)`
   produces (anything else is a guard violation); it clears the bridge's
   mirrors and deletes its link veths, then `del-br` takes any leftover VM taps;
3. confirms that `ovs-vsctl list-br` no longer shows the bridge. A survivor is
   a teardown failure, not a warning.

## 5. Ops (D2, D6, `OP_CATALOG.md`)

Every op that touches a node's OVS holds the existing `ovs:$target_node` lock
category. These are new ops, so the existing ops keep their signatures.

| ID | name | per | after | backend |
|---|---|---|---|---|
| OP-NET-060 | `create-switch` | native switch | — | SSH |
| OP-NET-070 | `link-switches` | switch↔switch link (any state; it asserts the state) | `create-switch` of both ends (data flow) | SSH |
| OP-HOST-110 | `attach-vm-to-switch-port` | host@net on a switch-hosted segment (writes `link_down` for `down`) | `configure-cloud-init[$host]`, `create-switch[$switch]` (data flow), every `link-switches` | REST |
| OP-NET-080 | `set-port-state` | VM port with `state: blocked` | `start-vm[$host]` | SSH (OpenFlow flags) |
| OP-MIR-060 | `install-span` | span session (tap tier, Phase 2) | `attach-gre-tunnel` of its bridge and sensor | SSH |

- A switch-hosted net gets no `create-vnet` and no `network-ready` barrier;
  its attaches wait on the switch ops directly (above).
- `attach-vm-to-switch-port` replaces `attach-vm-to-vnet` for that leg. It
  publishes the same `hosts[].nets[]` record (with `nic_index`), plus `switch`
  and `port`, so tap resolution and the inventory see one shape.
- `start-vm` keeps `attach-*[$host@*]` as its predecessors, whichever form each
  one takes.
- Idempotency (ADAPTER §4): every op is safe to re-run and corrects drift in
  place (the gretap-qdisc pattern), never by recreating:
  - `create-switch`: `--may-exist add-br`, then `set bridge …` re-asserts its
    attributes;
  - `link-switches`: `--may-exist add-port` on both ends, then the peers, VLAN
    role and state flags are re-asserted;
  - `attach-vm-to-switch-port`: the same NIC string PUT again (deterministic
    MAC), as `attach-vm-to-vnet` does;
  - `set-port-state`: `ovs-ofctl mod-port` flags are absolute;
  - `install-span`: a mirror with that name is replaced with the planned
    selection (Phase 2).

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

Re-applying a plan whose session changed replaces the mirror in place (same
name, `tapsm_<8hex>` of the tap plan's id and the session key
`<switch>|<sensor>|<vlans>`); a session the new plan no longer has gets a
removing `install-span`, which also takes its gretap off the switch and
deletes it when no other session of that (switch, sensor) remains. `axrange
tap destroy` (and the cascade from a twin destroy) removes every span mirror
before the gretaps are swept.

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
| `segment` | every VM port on the segment, across all its carriers, `rx`. Inter-switch link ports are excluded, so each frame is mirrored once, where it enters the fabric. VM ports are access ports in v1 (NSW-060), so no `select_vlan` is needed |
| `perimeter` | every router port on the segment, `both` |
| `boundary` | the joining router's port on each of the two segments, `rx` |

One mirror is created per `(switch, binding)`. A scope that spans several
carriers yields one mirror on each, each with its own gretap.

Mirror selections name OVSDB Port rows. A VM stop/start removes and re-adds its
tap, and OVS drops a deleted port from every mirror. `install-span` therefore
writes one fragment per selected VM (`axrange-ovs-<vm>-span-<mirror>.sh`) that
re-adds the re-created tap to the mirror on `post-start` (R5), deletes the
fragments of VMs a re-apply dropped, and a removed session deletes all of its
own. The VM's handle for each port travels in the op's `port_owners`.

### 6.4 `verify-capture`

`verify-capture` (OP-MIR-040) takes a span session the way it takes a bridge:
the sentinel is injected by a VM on a source (`rx`/`both`) port of the mirror,
a single-homed one first, and the sensor is read over QGA. For an external sink
(`XPress-SW-A`) it is skipped with the existing "capture UNVERIFIED" warning. Capture there is confirmed on the appliance: the gre-cap RX counter,
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
    status: completed
    ports:
      - port: SFP1
        mode: trunk
        native: office
        vlans: [office]
        state: up
        source: link            # link | auto
        far: { kind: router, device: gw, port: eth1, leg: 0 }
      - port: corp-workstation01.0
        mode: access
        vlans: [office]
        state: up
        source: auto
        far: { kind: host, device: corp-workstation01, leg: 0 }
```

`hosts[].nets[]` keeps its shape and gains `switch` and `port` on switch-hosted
legs. `net_bridge` keeps mapping only SDN-hosted segments. A switch-hosted
segment's bridges are found through `switches[]`, because one segment can sit
on several bridges.

## 8. Proxmox firewall on native-switch ports (D9)

Native-switch bridges are outside the SDN view: they are not in the SDN GUI, and
IPAM does not see them.

**Finding (from the Proxmox source of `qemu-server` and `pve-firewall`; the
`firewall=0` half confirmed live on dev45, 2026-11-12):** pve-firewall acts on a VM NIC only when the NIC
carries `firewall=1`. On an OVS bridge, `firewall=1` makes `qemu-server` insert a
per-NIC Linux bridge (`fwbr<vmid>i<n>`) with a veth pair. The OVS port is then
`fwln<vmid>o<n>`, not the tap, and the VLAN tag sits on that port. axRange never
sets `firewall=1`, so its VM NICs have none, and the tap is the OVS port.

`attach-vm-to-switch-port` writes `firewall=0` explicitly, so the port the
mirror, `blocked` and `link_down` act on is always the tap, even when a template
default enables the firewall.

Live (dev45, PVE 9.2.2, switch-test): `qm config` shows
`net0: virtio=…,bridge=tesw_647ae73f,firewall=0,tag=10`; `ovs-vsctl show` lists
`tap<vmid>i0` ports with `tag: 10` directly on the bridge; no `fw*` interface
exists on the node. pve-firewall is `disabled/running` cluster-wide on dev45,
so the `firewall=1` path (the `fwbr`/`fwln` insertion) could not be exercised
there; it stays a source-reading finding. v1 makes no firewall guarantee on a native
switch, and the cluster's datacenter firewall does not apply to traffic between
two ports of one native switch.

## 9. Validator rules (`validator/checks`, `VALIDATOR.md`)

These are Pass 3 rules (`NSW`, a new category) unless noted. "Native" is the
resolution of §2.1. The validator gains the adapter's capability set as an input
(`validate_full(..., platform_caps=...)`). Without it (a bare `axrange
validate`) the NSW rules do not fire; the planner's Pass 1 resolves the same
model (`axrange.switchmodel`) with the target's capabilities and refuses any
NSW error. There is no NSW-010: the resolution itself is not a violation, and
NSW-020 reports the logical case.

| ID | sev | rule |
|---|---|---|
| NSW-020 | warning | A `kind: switch` without an image on a platform without `native-switch` is logical: it deploys as nothing, and its `state` and taps have no effect |
| NSW-030 | error | A segment is SDN-hosted or switch-hosted, never both: a switch-hosted segment may not set `egress: true` or `transport: true`, which need an SDN vnet (every VM leg on it gets a switch port, explicit or automatic, so no leg can attach outside one) |
| NSW-040 | error | A leg that gets an automatic port has exactly one deepest carrier. Otherwise: "ambiguous carrier for <host> on <segment>: <sw1>, <sw2>; add an explicit link" |
| NSW-050 | error | Every `vlans[]` / `native` on an up native-switch port is a segment the switch carries; a link to a VM names a segment the VM has a leg on; one VM NIC is plugged by at most one link; an automatic port name does not collide with an authored one; a native switch links only to VMs (including imaged switches) and native switches. Span sources naming unknown ports are refused at `tap plan` (Phase 2) |
| NSW-060 | error | A link with a VM end carries exactly one segment the VM has a leg on (access, or trunk with only `native`). v1 does no in-guest VLAN tagging (§12 S3) |
| NSW-070 | warning | `stp: rstp` on a VM or logical switch: it has no effect (on a native switch it is OVS RSTP, Phase 3) |
| NSW-090 | error | A loop of native switches over `up` links (parallel links count) runs `stp: rstp` on every switch in it; a `down` link breaks the loop. Added 2026-11-12 with RSTP: without it a looped twin deploys into a broadcast storm |
| NSW-080 | warning | An addressed leg on a native switch: the address is not deployed (§2.5) |
| CLU-080 | error | Planner-side (Pass 4): every native switch, and every VM attached to one, is placeable on one Proxmox node (§3) |

## 10. Contract diff

Edited in place in the working tree and committed with the Phase 1 code
(`docs/contracts/README.md`, the change rule):

| file | change |
|---|---|
| `IR_SCHEMA.md` | `scenario_schema_version` 4→5 (additive): `stp` on a `kind: switch` node; §3.2 adds the native/logical/VM resolution and drops "an imageless switch deploys as nothing" as the only behaviour. §3.3 says a link `state` acts on a native switch's port. Declaring `5` is optional; the expander declares it only for a config that sets `stp` |
| `VALIDATOR.md` | STR-020 adds `5`; a new NSW category (NSW-020…080); CLU-080; the counts paragraph |
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

### 11.1 Phase 1 live run (dev45, PVE 9.2.2, 2026-11-12)

State DB `~/.axrange/state.db`; logs beside it (`switch-test-native-*.log`,
`switch-trunk-test-te-*.log`).

| check | deploy | reading |
|---|---|---|
| switch-test deploys | `switch-test-c5f9de7d` | 47 ops COMPLETED; `tesw_647ae73f` with `tap<vmid>i0` ports, tag 10; `servers` stays an SDN vnet |
| workstations reach `file-server` through `gw` | same | ws01 10.71.10.2 and ws02 10.71.10.3 → 10.71.20.10: 3/3, ~0.5 ms |
| `state: down` cuts that host | `switch-test-bb995d07` (P2 down, scratch copy) | ws02 `net0 … link_down=1`, carrier 0, "Network is unreachable"; ws01 on the same switch 3/3 to gw and file-server |
| two-switch trunk | `switch-trunk-test-8307e956` | 44 ops, no SDN zone; `tepp_ad831698`↔`tepp_5dae7406` trunks [10, 20]; ws01, ws02 ↔ file-server across VLANs through gw on core: 3/3 each way |
| destroy leaves nothing | all three | each switch "removed"; `ovs-vsctl list-br` empty, 0 mirrors, no `tepp_` or VM taps on the node |

SPAN capture (`XPress-SW-A` counters, the `rx`-only reading) is Phase 2.

### 11.2 Phase 2 live run (dev45, 2026-11-12)

Logs beside the state DB (`switch-test-span-*.log`, `switch-test-span-p1rx-*`,
`switch-trunk-test-span-*`).

| check | reading |
|---|---|
| switch-test span on access-sw SFP1 `both` → XPress-SW-A | `tapsm_3f790adf` selects `gw`'s office tap both ways, output `tapg_b3c494` (trunk port); GRE key 0xb3c494d5 carries ws01's echo requests AND file-server's replies; sink `gre-cap` RX 499,807 → 499,850 (+43) for 20 pings |
| tagged SPAN output (R3) | 98-byte frames arrive as 110-byte keyed-GRE payloads (+4 for the 802.1Q tag); the sink counts them |
| `P1 rx` sees only ws01's transmit | re-applied in place (same mirror, src = ws01's tap only): ws01→fs 5 requests, ws01's 5 replies to fs, ws01's ARP; nothing from ws02, nothing addressed to ws01 |
| destroy with a span | cascade "span mirror …: removed", gretap gone with it, switch removed; node: no bridges, mirrors, gretaps |
| span on a trunk port while links were OVS patch pairs, switch-trunk-test Po1 `both` | **captured nothing**: mirror `tx_packets=0` while ws01↔fs crossed Po1 (3/3 pings). `ofproto/trace` of a real gw→fs frame: `push_vlan(20),2,1,pop_vlan,5`, never the gretap (dp port 8). See R6 |
| span on trunk Po1 `both`, links as veth pairs (`switch-trunk-test-7efc5447`) | mirror `tx_packets=28`; ws01→fs crosses Po1 twice and every frame is mirrored: `vlan 10` and `vlan 20`, requests and replies, 5 each |
| `tx` on P3 (file-server) for frames from the other switch | exactly the 5 echo requests that reached fs from core; its replies (P3 `rx`) not mirrored |
| destroy, veth links | span mirror removed, both switches removed; node: no bridges, `tepp_` veths, mirrors or gretaps |

### 11.3 R5 and Phase 3 live runs (dev45, 2026-11-12)

| check | reading |
|---|---|
| R5: span and `blocked` survive a VM stop/start (`switch-test-4ed6ac9a`, P2 `blocked`, `P1 rx` span) | before: mirror src `tap661338i0`, ws02 `NO_RECV NO_FLOOD NO_FWD`, fragments `axrange-ovs-650112-blocked-0.sh`, `axrange-ovs-661338-span-tapsm_4e5e7e08.sh`, both hooks with the runner; after `qm stop`: mirror empty, flags gone; after `qm start`: mirror src `tap661338i0` again (new port UUID), flags back; ws01→fs 4/4 with its 4 requests mirrored, ws02 0/2 |
| R5 destroy | no bridges, mirrors, fragments or hooks left |
| RSTP on a loop (`switch-trunk-test-157f2f0d`, a second core↔access trunk, `stp: rstp` on both) | access-sw root; core Po1 Root/Forwarding, Po2 Alternate/Discarding; agent installs crossed the loop; ws01→fs 4/4 |
| RSTP failover | Po1's veth down: Po2 Root/Forwarding, 4/4; Po1 back: roles restored, 4/4 |
| RSTP destroy | both switches, their veths, no mirrors or fragments left |

## 12. Risks and the items not signed off

**Risks**, each checked live in Phase 1 before it is relied on:

- **R1, bridge persistence.** Verified on dev45 (PVE 9.2.2, 2026-11-12):
  `create-switch` finished before the same deploy's `apply-sdn-config` (a
  cluster SDN apply, which reloads node networking), and `tesw_647ae73f`
  survived it with its three ports and tags. It does not survive a node
  reboot; a redeploy recreates it.
- **R2, IFNAMSIZ.** Every new name is 14 characters or fewer by construction
  (§4.2). The risk is a future producer, which the round-trip test guards.
- **R3, tagged SPAN output.** Verified on dev45 (2026-11-12): the mirrored
  frames carry their 802.1Q tag (a 98-byte ICMP frame arrives as a 110-byte
  keyed-GRE payload: 8 bytes of GRE with key, 4 of tag) and `XPress-SW-A`'s
  `gre-cap` RX counts them. The in-guest flow decap of an O2 sensor is not yet
  exercised with tagged frames.
- **R4, placement pinning.** Trivially true today (one node per plan). CLU-080
  is what keeps it true when placement grows.
- **R5, port re-creation.** Resolved 2026-11-12 (§2.4, §6.3): the VM's start
  hook re-applies OVS fragments. Verified on dev45 by a `qm stop` / `qm start`
  of both workstations (§11.3).

- **R6, OVS does not mirror on patch ports (found live, resolved 2026-11-12).** A frame
  crossing an OVS patch port is translated into the peer bridge within the
  same datapath flow, and the mirror selection does not carry across: a mirror
  selecting a patch port never fires on real traffic (only on a synthetic
  `ofproto/trace` that starts on the patch port), and by the same mechanism a
  `tx` / `both` selection on a VM port misses frames that arrived from another
  switch (inferred from the trace, not measured). Single-switch spans and every
  `rx` selection on a VM port are unaffected (the VM's tap is the real ingress).
  **Resolved** (the user's go, 2026-11-12, changing D2's "OVS patch-port
  pair"): a switch link is a kernel **veth pair**, one end a port on each
  bridge, so each bridge sees a real ingress port; teardown deletes the veths
  before `del-br`. Live readings in §11.2.

**Not covered by D1–D12; decided by the user on 2026-11-12** (S1: option (a);
S2 and S3 as proposed below):

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

**Phase 3 items stopped for the user (2026-11-12):**

- **S4, cross-node switches and MTU (D3's deferral).** dev45 is a standalone
  node (no corosync cluster), and the planner places every plan on one node
  (`pass4_placement.py`, PLANNER §3.4's least-loaded placement is deferred).
  Cross-node tunnels need multi-node placement first -- new scope -- and a
  second node to verify on.
- **S5, the corp-twin conversion offer (D11).** Two decisions D11 leaves open:
  (a) the unit -- converting one bridge VM leaves native switches trunked to
  the remaining bridge VMs, which NSW-060 refuses (no in-guest tagging), so
  only a whole-fabric conversion yields a valid twin; (b) the trunk VLAN sets
  live only in `catalog/twins/corp-twin/scripts/serve-l2switch.sh`'s port table
  (`<leg> access:<vid>` / `trunk:<native>:<vids>`, flags `cost=`, `down`,
  `mgmt`), which the canvas does not load: the conversion needs an engine-side
  reader behind an API route, and the `mgmt` SVI (ms120) and the L3 9K pair
  (VRRP) stay VMs or lose their addresses (S2).
- **libvirt native switches** are built (D10) but not live-verified: no
  libvirt host is configured.
