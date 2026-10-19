*SDN-native scenario networking, routers and fabric routing, and the tap/gretap capture
mechanics that mirror traffic out of a deployed twin.*

This document has two halves: how scenario networking is built, and how traffic capture works
on top of it. Which tier *owns* taps and sensors (standing sensors, tap-point vocabulary, tap
plans) is covered in [06-taps-sensors.md](./06-taps-sensors.md) and
[05-o2-surface.md](./05-o2-surface.md); this document describes only the underlying mechanism.

## Networking

### SDN-native, one zone per twin

Each deployed twin gets its own Proxmox SDN Zone. `OP-NET` ops define `/cluster/sdn` objects,
committed cluster-wide with a single apply — the zone, its bridge(s), and its vnets are all
staged and then applied together, so the operator-visible surface is the ordinary `/cluster/sdn`
tree, not a host-local bypass.

**Zone type is auto-derived from the IR**, not authored directly:

- **Simple zone** — used when no network in the scenario declares a VLAN tag. Each network gets
  its **own** OVS bridge; the vnet *is* the bridge.
- **VLAN zone** — used when any network declares a VLAN. **One** underlay OVS bridge backs the
  whole zone, and each network is a VLAN-tagged vnet on that shared underlay — this is the model
  that mirrors a real datacenter's VLAN structure.

Proxmox SDN materializes every vnet — Simple or VLAN — as a Linux (VLAN-aware) bridge that holds
the VM taps; a VLAN zone's per-network vnets sit on top of the single OVS underlay via an
internal link port. The capture mechanism (below) works identically regardless of which zone
type is in play, because it mirrors at the VM's own tap interface rather than depending on the
bridge being an OVS bridge.

### Routers: two OS branches

A `kind: router` node is multi-homed across its LAN segments, with a WAN NIC the adapter injects
onto the platform's uplink bridge. Two router operating systems are supported, each with its own
configuration-delivery path:

- **Debian + nftables** — configuration is delivered over REST/QGA, the same channel every other
  Linux host uses. Forwarding and LAN→WAN masquerade are installed as a small reboot-persistent
  service (using tools already present on the base image) rather than a one-shot runtime
  command, so a router surviving a first-boot reboot doesn't silently lose its NAT state.
- **VyOS** — the generic VyOS ISO ships neither cloud-init nor a guest agent, so config delivery
  rides **SSH** instead: the adapter discovers the router's WAN/management IP from its NIC's MAC
  address via `arp-scan` (no QGA to ask), then pipes a flat sequence of VyOS `set` commands
  through `vbash` over SSH. The router's template has its hardware ID stripped pre-clone so every
  clone enumerates its NICs in the same deterministic order.

### Egress and inter-segment routing

A network can declare SNAT egress directly at the SDN layer (a subnet + gateway + `snat`) with no
router VM at all — but this path is **Simple-zone only**: Proxmox SDN's VLAN zones reject
`exitnodes` and materialize no gateway or SNAT rule, so a VLAN-zoned scenario needs a real router
node for egress. A router node provides both LAN→WAN masquerade and inter-segment routing between
the twin's own VLAN segments — the general-purpose path that works regardless of zone type.

### Fabric: dynamic routing and overlay

On top of the basic router/egress model, a twin can opt into a fuller routing fabric — **VyOS
routers only**; a Debian/nftables router only ever gets the basic router model above (LAN→WAN
NAT + inter-segment routing), never dynamic routing or overlay. The fabric is declared with a
top-level `fabric:` block (naming regions and each region's IGP — OSPF, IS-IS, or none) plus a
per-router `routing:` stanza (region, role — spine / leaf / border / abr / area / provider /
edge — loopback, and IS-IS/OSPF/BGP parameters). The renderer compiles this into VyOS
configuration covering IS-IS or OSPF underlay routing, iBGP with route reflection, eBGP peering
and prefix origination/redistribution at borders, and BGP-EVPN/VXLAN overlay for leaves acting as
VTEPs. A border role also installs mutual OSPF↔IS-IS and BGP↔IGP redistribution with tagged
route-maps to prevent redistribution loops between the two IGPs.

An optional `fabric.tenant:` block turns on **symmetric IRB** — each VTEP leaf gets a tenant VRF
bound to an L3VNI, an anycast distributed gateway (the same gateway IP and a deterministic
shared anycast MAC on every leaf's SVI for a given L2 segment), and per-VRF BGP advertising the
gateway subnets as EVPN Type-5 routes. Hosts route through their segment's anycast gateway
regardless of which leaf they're attached to; each leaf independently routes east-west traffic
over the L3VNI. Because linked clones can otherwise derive an identical bridge MAC across
leaves, the anycast-gateway renderer keys each leaf's L3VNI router-MAC off its own loopback
address, giving every leaf a unique router-MAC so EVPN Type-5 routes aren't mistaken for
loopbacks of the advertising leaf's own routes.

**Current limit:** a tenant VRF has no route out to the internet — egress from an EVPN-VXLAN
overlay's L3VNI isn't implemented. The natural fix is either a border-originated default route
leaked into the tenant VRF or an explicit BGP route-target leak; neither is built, so a fabric
tenant's hosts can route to each other over the overlay but not to the twin's WAN egress.

## Capture mechanics

### Per-tap `tc` mirror → keyed gretap → sensor

Capture is a per-tap, not per-bridge, mechanism. For every interface marked for capture, a `tc`
mirror is attached directly to the VM's own tap interface (`tap<vmid>i<n>`) — wherever Proxmox
SDN happens to place that tap's bridge. Mirroring at the tap, ahead of bridge switching, means
east-west traffic between two VMs on the *same* segment is captured too, not just traffic that
crosses a bridge boundary.

A tap's `tc` mirror can be installed in one direction only (`ingress` — the frames the VM *sent*)
or in both directions; which one is used for a given tap is never authored directly, it's derived
from whatever tap point addresses that port. Mirroring both directions on every port of a
multi-port set delivers each frame to the sensor more than once (once from the sender's port,
again from the receiver's), so a set of two-or-more ports mirrors one direction only, while a
single-port tap keeps both. See [06-taps-sensors.md](./06-taps-sensors.md) for the full
scope-to-direction table.

The mirrored frames are sent to a **keyed gretap netdev** — a GRE tunnel interface, one per
(bridge, sensor) pair, whose remote endpoint is a sensor's address on a dedicated, **isolated**
transport network. That transport network carries GRE-encapsulated mirror traffic only; scenario
hosts have no route to it and no way to reach or discover the sensor. At the sensor end, a
single **flow-based gretap** (a decap that isn't tied to any one key) terminates all the keyed
tunnels aimed at it into one merged capture stream, so multiple tapped bridges — and even
multiple twins — can share one sensor without their tunnels colliding. The GRE key is scoped per
(bridge, sensor) pair specifically so that concurrent tunnels to the same sensor never clash on
the wire.

Because a VM's tap interface is recreated on every boot, the mirror itself is not a one-time
deploy-time side effect: it is installed as a Proxmox VM hook script that runs on every
`post-start` (rebuilding the `tc` rule) and `pre-stop` (tearing it down), so the mirror
self-heals across reboots, snapshot restores, and pauses without needing a re-deploy. The keyed
gretap netdev itself *is* a one-time, deploy-time side effect — created once per (bridge, sensor)
pair — but it does not survive a Proxmox **node** reboot, since it isn't backed by any persistent
database the way an OVS port would be; a resume-safe re-deploy recreates it.

Two-plus tapped bridges and per-tap sensor routing both fall out of the same per-(bridge, sensor)
scoping: a bridge with taps split across two different sensors simply gets two keyed tunnels
instead of one, and the port name plus GRE key are always derived from the (bridge, sensor)
pair rather than the bridge alone.

### The capture heartbeat: `verify-capture`

A mirror rule binding successfully is not the same thing as a frame actually reaching the
sensor — a mirror can bind cleanly and still capture nothing if, for instance, the underlying
bridge type silently doesn't support the mirror mechanism a given implementation assumed.
`verify-capture` closes that gap by making capture liveness a structural, checked property
rather than an assumption: it injects a uniquely-keyed sentinel frame from a capture-capable
guest on the tapped bridge, then confirms that exact sentinel arrived at the sensor by reading
the sensor's live capture over its management channel, within a timeout. A capture claim that
can't produce a confirmed sentinel fails loudly rather than reporting success on a mirror that is
silently capturing nothing.

### MTU

A full-size mirrored frame encapsulates to 1542 B (outer IPv4 20 + GRE with key 8 + inner
Ethernet 14 + 1500), 1546 B with an 802.1Q tag. Scenario-side MTU is never lowered to absorb this —
shrinking it can itself change attacker/OS behavior (some tooling probes path MTU). Instead the
mirror gretaps are built `nopmtudisc ignore-df`: the outer header never carries DF, so an
encapsulation larger than the transport MTU is fragmented and the sensor's kernel reassembles it
before decap, rather than the tunnel dropping it silently (a mirror has no sender to hear the
ICMP). Raising the transport vnet and sensor NIC to ≥ 1546 would avoid the fragmentation cost at
high rates; that is not implemented yet (GAP-112).

### Isolation properties

The transport vnet that carries mirror traffic is deliberately not a scenario network: scenario
hosts have no interface on it and no route to it, so the sensor is invisible from inside a
deployed twin, and there is no way for a sensor's own encapsulated copies to loop back into a
scenario capture. It also generalizes cleanly to a cloud adapter later — the same shape maps
onto AWS VPC Traffic Mirroring or Azure vTAP target subnets without a design change.

## Cloud-init delivery

First-boot configuration (hostname, the sudo'd user from the image binding, SSH authorized keys,
and a `runcmd` that starts the guest agent) is delivered through a `cicustom` snippet: rendered
per host and copied to the snippets directory Proxmox reads at first boot. Setting `ciuser` /
`sshkeys` directly on the VM config without going through `cicustom` skips the `runcmd`, and the
`runcmd` is what closes the guest-agent bootstrap loop for any template that doesn't already
auto-start the agent on its own.

**Exception — VyOS routers.** The generic VyOS ISO supports neither cloud-init nor a guest
agent, so a VyOS router's image binding declares cloud-init unsupported; its configuration is
delivered entirely over SSH `vbash` instead (see "Routers" above).

## See also

- [03-engine.md](./03-engine.md) — the adapter's REST/SSH transport split and the op catalog
  categories (`OP-NET`, `OP-HOST`, `OP-MIR`) that implement everything above.
- [06-taps-sensors.md](./06-taps-sensors.md) — which tier declares taps, how tap points are
  named and selected, and how a tap plan binds them to a standing sensor.
- [05-o2-surface.md](./05-o2-surface.md) — where standing sensors live and how the surface layer
  that owns them is stood up.
- Formal contracts: `docs/ADAPTER.md` (transport split, op→backend mapping), `docs/OP_CATALOG.md`
  (`OP-NET`/`OP-HOST`/`OP-MIR` signatures).
