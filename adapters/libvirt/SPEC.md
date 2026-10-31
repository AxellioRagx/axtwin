# libvirt adapter — native QEMU/KVM on a Linux host

## What it is

A deploy platform that casts a twin onto any Linux host with KVM, driven
through libvirt rather than a cluster product: domains from qcow2 templates,
Linux bridges for segments, the guest agent for in-guest control, and the same
`tc`-mirror-into-gretap capture plane as Proxmox. It serves the **deploy** step
of the loop for the cases a Proxmox cluster does not cover — a developer
laptop, a CI host, a single appliance-adjacent box, a demo.

It is libvirt, not `qemu-system` driven directly: libvirt already owns the
things the engine's platform seams need (storage pools and backing-file
clones, defined networks, the agent channel, deterministic domain naming and
listing, remote hosts over `qemu+ssh://`). Driving QEMU by hand would rebuild
those for no feature the loop asks for.

## Seam

Implements `axrange.planner.executor.Adapter` as `LibvirtAdapter`, plus the
four platform seams in `docs/ARCHITECTURE.md` §13:

| Seam | libvirt implementation |
|---|---|
| `Adapter.run` | routes each op to a **libvirt** backend (python `libvirt` bindings over a connection URI) or a **host** backend (commands on the KVM host: local subprocess when the URI is local, SSH otherwise) |
| guest exec | `virDomain.qemuAgentCommand` — the same QGA JSON the Proxmox adapter speaks today, so the exec / file-in / file-out wire format and the base64 in-guest convention are unchanged |
| inventory | `listAllDomains` filtered by tier-tagged name, `interfaceAddresses` (agent source) for VM → IP, `getVersion` / `getInfo` for host facts |
| resource naming | the tier-tagged name shapes from `CONVENTIONS.md` §2 unchanged; the Proxmox-only VMID is replaced by a deterministic domain UUID derived from `{plan_id, op_id}` (UUID5), which keeps check-then-act and the `img` protect guard structural |
| teardown | sweep domains, networks and bridges by tier-tag shape with the same protect guard for `o2` and `img` |

Selected by a target's `platform: libvirt` (`api/Spec.md` A4). Registration is
the platform factory, not a registry: `adapter_for(target.platform)` with the
closed set `{proxmox, libvirt}` (VMware joins it when built); an unknown
platform fails the deploy by name.

### Op → libvirt mapping

The op catalog is unchanged; this table is the libvirt column beside
`docs/contracts/ADAPTER.md` §3. An op marked *logical* records its outputs and
performs no platform call, the pattern ADAPTER.md §8 already used for SDN.

| Op | libvirt | Backend |
|---|---|---|
| `create-sdn-zone` | logical; `zone_id` from naming | — |
| `create-ovs-bridge` | define + start an isolated libvirt network backed by a Linux (or OVS) bridge named by `naming` | libvirt |
| `create-vnet` | isolated, logical L2 segments only (no uplink): Simple-zone analogue: one network per segment; VLAN-zone analogue: a tagged portgroup on the zone's underlay network (needs `LIBVIRT_NETWORK_BACKEND=ovs` -- libvirt applies `<vlan>` tags to Open vSwitch ports only, so a VLAN zone on the `linux` backend fails loud at `create-ovs-bridge`). **NAT and DHCP refused:** a `dhcp.enabled` block raises `adapter-unsupported` (not implemented; decision held). The segment's `dns` is carried into the guests' cloud-init network-config | libvirt |
| `create-subnet` | *logical* (addressing is cloud-init's). **NAT refused:** the planner's `snat` (`bool(net.egress)`) raises `adapter-unsupported` rather than casting a segment with no egress (not implemented; decision held) | — |
| `apply-sdn-config` | `net-start` / autostart of the networks defined above | libvirt |
| `clone-vm` | qcow2 overlay in the storage pool with the template volume as backing file; `defineXML` from the binding's domain template | libvirt |
| `configure-vm-hardware` | domain XML: vcpu, memory, machine, firmware, virtio devices, the `org.qemu.guest_agent.0` channel | libvirt |
| `configure-cloud-init` | the already-rendered user-data / meta-data as a NoCloud seed ISO attached as a CD-ROM | host + libvirt |
| `attach-vm-to-vnet` | `<interface type='network'>` on the segment's network / portgroup | libvirt |
| `start-vm` | `create()` | libvirt |
| `wait-guest-agent` | `qemuAgentCommand({"execute":"guest-ping"})` polled to the image's `first_boot_budget` | libvirt |
| `wait-os-ready` | unchanged probe from the orchestrator | probe |
| `configure-router`, `configure-routing` | unchanged (guest exec / SSH into the guest) | guest |
| `attach-gre-tunnel` | the same `ip link … type gretap` + deep-FIFO qdisc on the KVM host | host |
| `install-ovs-mirror` | the same per-tap `tc` mirror hook; the tap name comes from the domain's interface target | host |

Multi-host is a later placement concern: a target may name one URI in v1;
`qemu+ssh://` URIs make a second host a placement decision rather than an
adapter change, and inter-host segment stitching is out of scope.

## Config

`adapter.conf` for a libvirt target (same file shape, read by the same loader,
keys fail loud when unrecognized):

| Key | Required | Purpose |
|---|---|---|
| `LIBVIRT_URI` | yes | `qemu:///system` or `qemu+ssh://user@host/system` |
| `LIBVIRT_STORAGE_POOL` | yes | pool holding `img-*` template volumes and per-deploy overlays |
| `LIBVIRT_NETWORK_BACKEND` | no | `linux` (default) or `ovs` |
| `HOST_SSH_HOST`, `HOST_SSH_USER`, `HOST_SSH_KEY` | when the URI is remote | the host backend's SSH (key *path*) for gretap / `tc` work; `HOST_SSH_HOST` defaults to, and must equal, the URI's host; the key is also passed to libvirt's ssh transport as `?keyfile=` when the URI has none |
| `GUEST_SSH_PUBKEY_FILE` | yes | unchanged |
| `DEFAULT_NODE` | yes | the host's name as placement records it |
| `MIRROR_GRETAP_TXQUEUELEN` | no | unchanged |
| `LIBVIRT_BINDINGS_DIR` | no | the `images/bindings/libvirt/` directory `clone-vm` resolves a template volume from by the op's `image_id`, until the planner binds images per platform (`docs/ARCHITECTURE.md` §4); a plan that carries `template_volume` needs no directory |

Image bindings: `images/bindings/libvirt/<image>.yaml` with the manifest's
platform-independent half untouched; the binding carries the template volume
name, the domain XML template, and the hardware defaults (the key set is
documented in `adapter_libvirt/bindings.py`; the manifest loader walks every
`images/bindings/<platform>/` directory). A template is the
same qcow2 the Proxmox `img-*` build produces, imported into the pool; a
libvirt-native `axrange image build` path is a follow-up, not a prerequisite.

## Acceptance

The capture-flow gate in root `Claude.md`, in its first-slice form from
`docs/contracts/ADAPTER.md` §6: two tapped Linux hosts on one segment deploy
to a single KVM host, `ping`/`iperf` between them, and pcaps land on the
sensor with climbing counters.

- **Automated:** adapter tests beside the code run every op in the table
  against a fake libvirt connection (recorded XML and agent exchanges), assert
  the deterministic UUID/name derivation, check-then-act adoption of an
  existing domain or network, the logical ops' outputs, and that the teardown
  guard refuses `o2` and `img` shapes. The planner's existing fake-adapter
  suite must pass unchanged — the DAG does not know the platform.
- **Live:** the two-host slice on a laptop (`qemu:///system`) and on a remote
  host (`qemu+ssh://`), then a `corp-enterprise` cast with Windows guests.

## Notes

- **Prerequisite:** the four platform seams exist as protocols the engine
  imports (`docs/ARCHITECTURE.md` §13). Building this adapter before that
  means duplicating the Proxmox guest-agent, inventory and teardown code paths
  at their call sites; do not.
- The capture plane is unchanged by design: a Linux bridge plus a tap-level
  `tc` mirror is exactly what PVE SDN materializes, so this platform is the
  simpler case, not a new one.
- Windows guests need virtio drivers in the template, as on Proxmox.
- VMware follows the same seams with vSphere in place of libvirt and VMware
  Tools guest operations in place of QGA; nothing here is libvirt-only except
  the backend column.
- **Open decision (held): the router WAN uplink.** `planner/pass1_emit.py`
  `ROUTER_WAN_BRIDGE = "vmbr0"` is a Proxmox bridge name, so every router twin
  fails on libvirt (no such network). The fix is a neutral uplink marker from
  the planner that each adapter maps to its own uplink, plus a libvirt config
  key naming the WAN network; neither exists yet.
- Teardown fails closed: every removal needs a positive match of the
  teardown's own tier shape (vm / vnet / zone / bridge), so libvirt's own
  `default` network and hand-made domains are never swept.
- `apply-sdn-config` touches only the networks this deploy's adapter instance
  defined or adopted (plus the plan's underlay), never a tier-shape sweep of
  the host.
- Not a reason to add a plugin registry: the factory is a closed set and stays
  one until a third platform makes the set open.
