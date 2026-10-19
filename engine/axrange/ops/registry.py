"""Op catalog v1 (docs/OP_CATALOG.md): 40 op types as typed definitions.

34 action/wait + 6 barriers = 40 (OP-NET-050 create-subnet added 2026-06-09 for
router slice Phase 1 — SDN subnet+SNAT egress; OP-HOST-090 configure-router added
2026-06-10 for Phase 2 — router node egress/routing; OP-MIR-040 verify-capture added
2026-06-19 for the dynamic-tap capture heartbeat; OP-MIR-050 deploy-sensor added
2026-06-23 for the sensors slice; OP-HOST-100 configure-routing + OP-BAR-060
fabric-ready added 2026-06-29 for the fabric slice; OP-OVL-070 install-agent added
2026-07-11 for A3 Stage A -- default/declared exporter + adversary agents,
docs/design/08-exercise-measurement.md; OP-TFC-010 drive-persona added
2026-07-24 for the Traffic tier, docs/WORK_ORDER_traffic_tier.md -- composed
by hand by the standalone `tfc` playout (axrange.o2control.traffic), never
emitted by the te planner either; OP-MIR-005 record-tap-intent added
2026-07-13 for the sensor-agnostic `te_` substrate then REMOVED 2026-07-28
(docs/WORK_ORDER_tap_eligibility.md, Authority: user) -- the twin substrate
became fully tap-agnostic: a `te` deploy emits NO OP-MIR op at all any more,
not even declarative bookkeeping. attach-gre-tunnel/install-ovs-mirror/
verify-capture stay in the catalog, emitted ONLY by the A2 tap playout
(never the te planner) against the twin's published topology, not a
recorded tap-intent subset. Op count 40 -> 39 with this removal);
OP-TFC-020 assign-role added 2026-08-04 for the role/persona decoupling
(docs/WORK_ORDER_role_persona_tier.md, Authority: user) -- the SECOND
Traffic-tier op, likewise composed by hand by the `tfc` playout and never
emitted by the te planner, taking op count 39 -> 40.
Illustrative `implementation:` refs are omitted (gated on the
Proxmox adapter spec, OPEN_QUESTIONS Q5). The registry serves two purposes:

  1. Gives the validator a concrete op set for any rules that need to check
     against it (of the 98 v1 catalog rules, the registry is referenced by
     reproducibility checks and is a load-bearing input for the planner).
  2. Documents every op's signature in code so adapter API design can be
     prototyped against real types.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class OpKind(str, Enum):
    ACTION = "action"
    WAIT = "wait"
    BARRIER = "barrier"


class CleanupStrategy(str, Enum):
    REDO = "redo"
    ESCALATE = "escalate"


class IdempotencyClass(str, Enum):
    INFRA = "infra"
    CONFIG = "config"


@dataclass(frozen=True)
class OpDefinition:
    """One op type. Field-presence-by-kind matches OP_CATALOG.md §3."""

    id: str
    name: str
    kind: OpKind
    description: str
    inputs: dict[str, str] = field(default_factory=dict)
    outputs: dict[str, str] = field(default_factory=dict)
    after: tuple[str, ...] = ()
    cleanup_strategy: CleanupStrategy | None = None  # None for wait ops
    idempotency_class: IdempotencyClass | None = None  # None for wait ops
    resource_categories: tuple[str, ...] = ()
    timeout: str | None = None  # spec-side; per-attempt
    redo_on: tuple[str, ...] = ()


@dataclass(frozen=True)
class Barrier:
    """Synthetic milestone. No execution; pure ordering construct."""

    id: str
    name: str
    description: str
    scope: str  # what each instance is scoped to (e.g. "net-id", "host-id")
    predecessors: tuple[str, ...]
    consumed_by: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# OP-NET — Network creation (4 ops)
# ---------------------------------------------------------------------------

_OPS: list[OpDefinition] = [
    OpDefinition(
        id="OP-NET-010",
        name="create-sdn-zone",
        kind=OpKind.ACTION,
        description=(
            "Create the per-scenario Proxmox SDN Zone, type auto-derived "
            "(Simple when no network declares a VLAN; VLAN otherwise). A VLAN "
            "zone references the pre-created OVS underlay bridge, so this runs "
            "AFTER create-ovs-bridge (SDN-native, 2026-06-03)."
        ),
        # bridge_name is the VLAN underlay (absent/ignored for Simple zones).
        inputs={
            "scenario_id": "scenario-id:str",
            "zone_type": "sdn-zone-type:str",
            "bridge_name": "ovs-bridge-name:str",
        },
        outputs={"zone_id": "sdn-zone-id:str"},
        after=("create-ovs-bridge[*]",),
        cleanup_strategy=CleanupStrategy.REDO,
        idempotency_class=IdempotencyClass.INFRA,
        resource_categories=("proxmox-cluster",),
        timeout="60s",
        redo_on=(),
    ),
    OpDefinition(
        id="OP-NET-020",
        name="create-ovs-bridge",
        kind=OpKind.ACTION,
        description=(
            "Create the OVS bridge(s) for the scenario. VLAN zone: ONE underlay "
            "bridge per zone (emitted once). Simple zone: one per network (the "
            "vnet materializes it). MUST be OVS where mirroring is needed — "
            "port mirroring depends on it. Runs first, before the SDN zone."
        ),
        inputs={"net_id": "net-id:str", "zone_type": "sdn-zone-type:str"},
        outputs={"bridge_name": "ovs-bridge-name:str"},
        after=(),
        cleanup_strategy=CleanupStrategy.REDO,
        idempotency_class=IdempotencyClass.INFRA,
        resource_categories=("proxmox-cluster",),
        timeout="60s",
        redo_on=(),
    ),
    OpDefinition(
        id="OP-NET-030",
        name="create-vnet",
        kind=OpKind.ACTION,
        description=(
            "Create one SDN vnet inside the scenario zone — VLAN-tagged on the "
            "underlay for a VLAN zone, or the network's own bridge for a Simple "
            "zone. PVE materializes a bridge named after the vnet that VMs attach to."
        ),
        inputs={
            "zone_id": "sdn-zone-id:str",
            "net_id": "net-id:str",
            "cidr": "cidr:str",
            "vlan": "vlan-tag:int",
            "gateway": "ip:str",
            "dns": "ip-list:list",
            "dhcp": "dhcp-config:obj",
        },
        outputs={"vnet_id": "vnet-id:str"},
        after=("create-sdn-zone[$scenario]",),
        cleanup_strategy=CleanupStrategy.REDO,
        idempotency_class=IdempotencyClass.INFRA,
        resource_categories=("proxmox-cluster",),
        timeout="60s",
        redo_on=(
            "infra.networks[*].vlan",
            "infra.networks[*].cidr",
            "infra.networks[*].gateway",
            "infra.networks[*].dns",
            "infra.networks[*].dhcp",
        ),
    ),
    OpDefinition(
        id="OP-NET-040",
        name="apply-sdn-config",
        kind=OpKind.ACTION,
        description=(
            "Commit all staged SDN definitions with one cluster-wide SDN reload."
        ),
        inputs={"zone_id": "sdn-zone-id:str"},
        outputs={"applied": "bool"},
        after=(
            "create-sdn-zone[$scenario]",
            "create-ovs-bridge[*]",
            "create-vnet[*]",
            "create-subnet[*]",
        ),
        cleanup_strategy=CleanupStrategy.REDO,
        idempotency_class=IdempotencyClass.INFRA,
        resource_categories=("proxmox-cluster",),
        timeout="120s",
        redo_on=(),
    ),
    OpDefinition(
        id="OP-NET-050",
        name="create-subnet",
        kind=OpKind.ACTION,
        description=(
            "Define a PVE SDN subnet on the network's vnet with a gateway and "
            "source-NAT (SNAT) — gives the network a real L3 gateway and "
            "internet/apt egress. Emitted per network that opts in with "
            "`egress: true` (and declares a `gateway`). SDN-native egress; "
            "router/firewall slice Phase 1 (docs/SLICE_ROUTER.md)."
        ),
        inputs={
            "net_id": "net-id:str",
            "cidr": "cidr:str",
            "gateway": "ip:str",
            "snat": "bool",
        },
        outputs={"subnet_id": "subnet-id:str", "vnet_id": "vnet-id:str"},
        after=("create-vnet[$net]",),
        cleanup_strategy=CleanupStrategy.REDO,
        idempotency_class=IdempotencyClass.INFRA,
        resource_categories=("proxmox-cluster",),
        timeout="60s",
        redo_on=(
            "infra.networks[*].cidr",
            "infra.networks[*].gateway",
            "infra.networks[*].egress",
        ),
    ),
    # -------------------------------------------------------------------
    # OP-HOST — Host lifecycle (8 ops)
    # -------------------------------------------------------------------
    OpDefinition(
        id="OP-HOST-010",
        name="clone-vm",
        kind=OpKind.ACTION,
        description=(
            "Clone the bound template into a new VM on the target node."
        ),
        inputs={
            "template_vmid": "vmid:int",
            "clone_mode": "clone-mode:str",
            "target_node": "node-name:str",
            "node_id": "node-id:str",
        },
        outputs={"vmid": "vmid:int"},
        after=(),
        cleanup_strategy=CleanupStrategy.REDO,
        idempotency_class=IdempotencyClass.INFRA,
        resource_categories=("proxmox-cluster", "proxmox-node:$target_node"),
        timeout="600s",
        redo_on=("nodes[*].image", "nodes[*].requires"),
    ),
    OpDefinition(
        id="OP-HOST-020",
        name="configure-vm-hardware",
        kind=OpKind.ACTION,
        description="Set CPU/RAM/disk and machine/BIOS/agent flags on the cloned VM.",
        inputs={
            "vmid": "vmid:int",
            "cpu": "int",
            "ram": "mem-size:str",
            "disk": "disk-size:str",
            "machine": "machine-type:str",
            "bios": "bios-type:str",
            "agent": "bool",
        },
        outputs={"configured": "bool"},
        after=("clone-vm[$host]",),
        cleanup_strategy=CleanupStrategy.REDO,
        idempotency_class=IdempotencyClass.INFRA,
        resource_categories=("proxmox-cluster", "vm:$vmid"),
        timeout="120s",
        redo_on=("nodes[*].resources",),
    ),
    OpDefinition(
        id="OP-HOST-030",
        name="configure-cloud-init",
        kind=OpKind.ACTION,
        description=(
            "Render and attach the per-host cloud-init / sysprep payload as "
            "the cicustom drive."
        ),
        inputs={
            "vmid": "vmid:int",
            "node_id": "node-id:str",
            "ip_config": "ip-config:obj",
            "sysprep_required": "bool",
            "hostname": "str",
        },
        outputs={"cloud_init_attached": "bool"},
        after=("configure-vm-hardware[$host]",),
        cleanup_strategy=CleanupStrategy.REDO,
        idempotency_class=IdempotencyClass.INFRA,
        resource_categories=("proxmox-cluster", "vm:$vmid"),
        timeout="120s",
        redo_on=("nodes[*].net", "nodes[*].nets"),
    ),
    OpDefinition(
        id="OP-HOST-040",
        name="attach-vm-to-vnet",
        kind=OpKind.ACTION,
        description=(
            "Attach one VM NIC to one scenario vnet. Per (host, network) — "
            "multi-homed nodes emit several."
        ),
        inputs={
            "vmid": "vmid:int",
            "vnet_id": "vnet-id:str",
            "net_id": "net-id:str",
            "ip": "ip:str",
            "nic_index": "int",
        },
        outputs={"nic_attached": "bool"},
        after=("configure-cloud-init[$host]", "network-ready[$net]"),
        cleanup_strategy=CleanupStrategy.REDO,
        idempotency_class=IdempotencyClass.INFRA,
        resource_categories=("proxmox-cluster", "vm:$vmid", "ovs:$target_node"),
        timeout="60s",
        redo_on=("nodes[*].net", "nodes[*].nets"),
    ),
    OpDefinition(
        id="OP-HOST-050",
        name="configure-static-routes",
        kind=OpKind.ACTION,
        description=(
            "Apply router static routes (IR static_routes) into the guest "
            "via Ansible. config-class."
        ),
        inputs={
            "host_id": "node-id:str",
            "mgmt_ip": "ip:str",
            "static_routes": "route-list:list",
        },
        outputs={"routes_applied": "bool"},
        after=("wait-os-ready[$host]",),
        cleanup_strategy=CleanupStrategy.REDO,
        idempotency_class=IdempotencyClass.CONFIG,
        resource_categories=("ansible-pool", "vm:$vmid"),
        timeout="300s",
        redo_on=("nodes[*].static_routes",),
    ),
    OpDefinition(
        id="OP-HOST-060",
        name="start-vm",
        kind=OpKind.ACTION,
        description=(
            "Power on the VM. Proxmox post-start hook installed by OP-MIR "
            "fires here for tapped hosts."
        ),
        inputs={"vmid": "vmid:int"},
        outputs={"started": "bool"},
        after=("attach-vm-to-vnet[$host@*]",),
        cleanup_strategy=CleanupStrategy.REDO,
        idempotency_class=IdempotencyClass.INFRA,
        resource_categories=("proxmox-cluster", "vm:$vmid"),
        timeout="120s",
        redo_on=(),
    ),
    OpDefinition(
        id="OP-HOST-070",
        name="wait-guest-agent",
        kind=OpKind.WAIT,
        description=(
            "Poll the QEMU guest agent until it responds. Skipped when "
            "hardware.agent: false."
        ),
        inputs={"vmid": "vmid:int"},
        outputs={"agent_ready": "bool"},
        after=("start-vm[$host]",),
        resource_categories=("vm:$vmid",),
        timeout="180s",
    ),
    OpDefinition(
        id="OP-HOST-080",
        name="wait-os-ready",
        kind=OpKind.WAIT,
        description=(
            "Poll SSH (Linux) or WinRM (Windows) reachability bounded by "
            "first_boot_budget."
        ),
        inputs={
            "vmid": "vmid:int",
            "mgmt_ip": "ip:str",
            "os_family": "os-family:str",
            "first_boot_budget": "int",
        },
        outputs={"os_ready": "bool"},
        after=("wait-guest-agent[$host]",),
        resource_categories=("vm:$vmid",),
        timeout="600s",
    ),
    OpDefinition(
        id="OP-HOST-090",
        name="configure-router",
        kind=OpKind.ACTION,
        description=(
            "Configure a `kind: router` node (router slice Phase 2, "
            "docs/SLICE_ROUTER.md): hot-add a WAN NIC on the uplink bridge, "
            "bring it up via DHCP, enable IPv4 forwarding, and masquerade "
            "LAN->WAN so the router's LAN segments get internet/apt egress + "
            "inter-segment routing. Delivered in-guest over the QGA channel "
            "(image-agnostic: Debian/nftables today, VyOS-swappable). Default "
            "forward policy is permissive — per-rule firewall/segmentation "
            "policy needs an IR extension (follow-on). OPTIONAL `egress` input "
            "(E1, docs/WORK_ORDER_protocol_engines.md): the router's egress "
            "PHASE — `provision` (real WAN masquerade, so apt works) or "
            "`measure` (no real egress; every external destination is "
            "policy-routed to the twin's sink). Omitting `egress` entirely "
            "keeps today's permissive behavior; omitting only its `phase` is "
            "`measure` — default CLOSED. Debian/nftables only: a VyOS router "
            "carrying an `egress` policy fails loud."
        ),
        # `egress` is OPTIONAL and, like configure-routing's `fabric`, a context
        # dict: {phase, sink_ip, mode: default|cidrs, cidrs: [...]}.
        inputs={
            "vmid": "vmid:int",
            "node_id": "node-id:str",
            "egress": "egress-policy:dict",
        },
        # wan_ip/wan_iface/lan_ifaces are returned by the VyOS path and consumed by
        # configure-routing (fabric slice) via inputs_from_outputs — no second arp-scan.
        # `egress_phase` is returned ONLY when an `egress` policy was applied; it is
        # the phase the router actually reached (never the one that was asked for),
        # which is what a capture window gets labelled with (§6).
        outputs={"configured": "bool", "wan_ip": "ip:str", "wan_gw": "ip:str",
                 "wan_iface": "str", "lan_ifaces": "list",
                 "egress_phase": "egress-phase:str"},
        after=("host-ready[$host]",),
        cleanup_strategy=CleanupStrategy.REDO,
        # INFRA, not CONFIG: routed to the REST/QGA backend (Model-C check-then-act,
        # idempotent NAT rule), not the Ansible config_exec pool.
        idempotency_class=IdempotencyClass.INFRA,
        resource_categories=("vm:$vmid",),
        timeout="300s",
    ),
    OpDefinition(
        id="OP-HOST-100",
        name="configure-routing",
        kind=OpKind.ACTION,
        description=(
            "Configure dynamic routing + overlay (OSPF / IS-IS / iBGP / eBGP / "
            "BGP-EVPN / VXLAN) on a `kind: router` node from its `routing:` intent "
            "(fabric slice, docs/design/04-networking-capture.md). VyOS-only: rendered to "
            "`set protocols ...` lines and delivered over the vbash SSH channel. "
            "Runs after configure-router (base interfaces + WAN/NAT up) and reuses "
            "its discovered wan_ip — no second arp-scan."
        ),
        inputs={
            "vmid": "vmid:int",
            "node_id": "node-id:str",
            "router_os": "str",
            "fabric": "fabric-ctx:dict",
            "wan_ip": "ip:str",
        },
        outputs={"routing_configured": "bool"},
        after=("host-ready[$host]", "configure-router[$host]"),
        cleanup_strategy=CleanupStrategy.REDO,
        # INFRA: VyOS-SSH Model-C, routed to the ProxmoxAdapter (not the Ansible
        # config_exec pool). Per-VM correctness lock comes free from resource_categories.
        idempotency_class=IdempotencyClass.INFRA,
        resource_categories=("vm:$vmid",),
        timeout="300s",
        redo_on=("nodes[*].routing", "fabric"),
    ),
    # -------------------------------------------------------------------
    # OP-MIR — Mirror plumbing (3 ops)
    # -------------------------------------------------------------------
    # OP-MIR-005 record-tap-intent REMOVED (docs/WORK_ORDER_tap_eligibility.md,
    # Authority: user, 2026-07-28) -- the twin substrate is now tap-agnostic:
    # a `te` deploy emits NO OP-MIR op at all (not even declarative
    # bookkeeping). The A2 tap playout (`axrange.o2control.taps`) discovers
    # the tappable set from the twin's PUBLISHED TOPOLOGY
    # (`axrange.planner.inventory.build_twin_inventory`'s unconditional
    # `hosts[].nets[]`, sourced from `attach-vm-to-vnet`) instead of this
    # op's `tapped_nics` output. Op count 40 -> 39; the id is retired and
    # never reused (docs/OP_CATALOG.md).
    OpDefinition(
        id="OP-MIR-010",
        name="attach-gre-tunnel",
        kind=OpKind.ACTION,
        description=(
            "Create a keyed gretap netdev per (bridge, sensor) that the per-tap "
            "tc mirror targets, tunneling mirrored frames to the sensor's "
            "transport-vnet IP (Option 1, tc/gretap — CHANGELOG 2026-06-04)."
        ),
        inputs={
            "bridge_name": "ovs-bridge-name:str",
            "net_id": "net-id:str",
            "sensor_id": "sensor-id:str",
            "sensor_transport_ip": "ip:str",
            "target_node": "node-name:str",
        },
        outputs={"gre_port": "ovs-port-name:str"},
        after=("network-ready[$net]",),
        cleanup_strategy=CleanupStrategy.REDO,
        idempotency_class=IdempotencyClass.INFRA,
        # Per-(bridge, sensor): one keyed tunnel per distinct target sensor
        # (per-tap sensor routing, 2026-06-03). gretap lock is per target sensor.
        resource_categories=("proxmox-cluster", "ovs:$target_node", "gretap:$sensor_id"),
        timeout="60s",
        redo_on=(),
    ),
    OpDefinition(
        id="OP-MIR-020",
        name="install-ovs-mirror",
        kind=OpKind.ACTION,
        description=(
            "Install the Proxmox post-start/pre-stop hook on a mirrored VM. "
            "Hook rebuilds the per-tap tc mirror (clsact + mirred -> gretap) "
            "on every boot (Option 1 — CHANGELOG 2026-06-04). Never emitted by "
            "the te planner (docs/WORK_ORDER_sensor_agnostic_te.md, "
            "docs/WORK_ORDER_tap_eligibility.md) -- composed by hand by the A2 "
            "tap playout (axrange.o2control.taps._compose_ops) against an "
            "already-deployed twin's tap-plan-resolved bindings; `tapped_nics` "
            "is the per-host list of NIC indices to mirror, entirely tap-plan- "
            "sourced (not an IR-declared tap type); `gre_port` is the gretap "
            "the host's tunnel feeds (its first mirrored NIC's sensor)."
        ),
        inputs={
            "vmid": "vmid:int",
            "node_id": "node-id:str",
            "bridge_name": "ovs-bridge-name:str",
            "gre_port": "ovs-port-name:str",
            "tapped_nics": "nic-index-list:list",
        },
        outputs={"hook_installed": "bool"},
        after=("configure-cloud-init[$host]", "attach-gre-tunnel[$net]"),
        cleanup_strategy=CleanupStrategy.REDO,
        idempotency_class=IdempotencyClass.INFRA,
        resource_categories=("proxmox-cluster", "vm:$vmid"),
        timeout="60s",
        # A2-composed only, driven by a tap plan's resolved bindings -- no IR
        # path re-derives this any more (`docs/WORK_ORDER_tap_eligibility.md`,
        # matching attach-gre-tunnel/verify-capture's own `redo_on=()`, below).
        redo_on=(),
    ),
    OpDefinition(
        id="OP-MIR-040",
        name="verify-capture",
        kind=OpKind.ACTION,
        description=(
            "Deploy-time capture heartbeat (docs/design/06-taps-sensors.md). "
            "One per (bridge, sensor) with >=1 tap: inject a uniquely-keyed sentinel "
            "frame (ICMP echo whose id/payload encodes deploy_id+bridge) from a "
            "tapped, QGA-capable guest on the bridge (the injector), then confirm "
            "it at the sensor by reading the sensor's NEWEST capture over the mgmt NIC "
            "(adapter SSH + a tcpdump/tshark sentinel filter) within a timeout. "
            "Feeds mirror-ready[$sensor], upgrading it from 'mirror commands issued' "
            "to 'a sentinel provably traversed tap -> gretap -> sensor' — closing "
            "the silent-zero-capture failure mode (CHANGELOG 2026-06-03). "
            "EMISSION CONTEXT (2026-07-13, docs/WORK_ORDER_sensor_agnostic_te.md; "
            "2026-07-28, docs/WORK_ORDER_tap_eligibility.md): the te planner no "
            "longer emits this op (a te deploy stands up only its own "
            "components) — it is emitted exclusively by the A2 tap playout "
            "(axrange.o2control.taps._compose_ops) against an already-deployed "
            "te's tap-plan-resolved bindings (discovered from the twin's "
            "published attach-vm-to-vnet topology, not a te-side tap op) and a "
            "standing O2 sensor."
        ),
        inputs={
            "vmid": "vmid:int",          # the injector's vmid
            "injector_host_id": "node-id:str",
            "net_id": "net-id:str",
            "sensor_id": "sensor-id:str",
            "bridge_name": "ovs-bridge-name:str",
            "gre_port": "ovs-port-name:str",
            # In-scenario sensor (sensors design Phase B): read the sensor VM over
            # QGA instead of SENSOR_SSH_*. Absent ⇒ the standing-appliance SSH read path.
            "sensor_vmid": "vmid:int",
            "sensor_iface": "iface:str",
            "sensor_ready": "bool",
        },
        outputs={"capture_verified": "bool"},
        # Gate on the specific tunnel + ALL mirror installs (so every tap on the
        # bridge is live before we inject) + the injector being OS-ready. Routed
        # CONFIG so the in-guest sentinel injection rides the QGA channel (like
        # wait-actor-ready); the sensor read is done via the adapter SSH backend.
        # It holds only vm:$vmid (the injector), never the ansible-pool slot.
        after=(
            "attach-gre-tunnel[$net@$sensor]",
            "install-ovs-mirror[*]",
            "host-ready[$host]",
        ),
        cleanup_strategy=CleanupStrategy.REDO,
        idempotency_class=IdempotencyClass.CONFIG,
        resource_categories=("vm:$vmid",),
        timeout="120s",
        redo_on=(),
    ),
    OpDefinition(
        id="OP-MIR-050",
        name="deploy-sensor",
        kind=OpKind.ACTION,
        description=(
            "Provision a typed capture sensor inside its in-scenario VM over QGA "
            "(sensors design Phase B, docs/design/06-taps-sensors.md): bring up "
            "the in-guest flow-based gretap decap iface (`type gretap external`, the "
            "receiver of the per-tap keyed gretaps), then install + start the tool "
            "the `type` selects — tcpdump-sensor (rotating pcaps), suricata-sensor "
            "(eve.json IDS), zeek-sensor (conn/dns/http logs) — reading that decap "
            "iface. Idempotent: re-running replaces config + restarts. Config-class "
            "(delivered over the QGA exec channel, like provision-app)."
        ),
        inputs={
            "vmid": "vmid:int",
            "sensor_id": "sensor-id:str",
            "sensor_type": "sensor-type:str",
            "capture_iface": "iface:str",
            "config_content": "str",
            "ship": "str",          # export.ship target (e.g. "loki"); Phase C
        },
        outputs={"sensor_ready": "bool"},
        after=("host-ready[$host]",),
        cleanup_strategy=CleanupStrategy.REDO,
        idempotency_class=IdempotencyClass.CONFIG,
        resource_categories=("ansible-pool", "vm:$vmid"),
        timeout="900s",
        redo_on=("sensors[*].type", "sensors[*].export"),
    ),
    # -------------------------------------------------------------------
    # OP-APP — App install dispatch (3 ops)
    # -------------------------------------------------------------------
    OpDefinition(
        id="OP-APP-010",
        name="provision-app",
        kind=OpKind.ACTION,
        description=(
            "Converge one app's pkg / pkg@version / config / role install "
            "actions onto its host via pull-mode Ansible at first boot."
        ),
        inputs={
            "app_id": "app-id:str",
            "host_id": "node-id:str",
            "mgmt_ip": "ip:str",
            "install_actions": "install-action-list:list",
            "env": "env-map:obj",
        },
        outputs={"provisioned": "bool"},
        after=("host-ready[$host]", "provision-app[$dep]"),
        cleanup_strategy=CleanupStrategy.REDO,
        idempotency_class=IdempotencyClass.CONFIG,
        resource_categories=("ansible-pool", "vm:$vmid"),
        timeout="900s",
        redo_on=("apps[*].install", "apps[*].env"),
    ),
    OpDefinition(
        id="OP-APP-020",
        name="run-container",
        kind=OpKind.ACTION,
        description=(
            "Start an OCI container on the host for a {container:} install "
            "action. Deterministic name; check-then-act on entry."
        ),
        inputs={
            "app_id": "app-id:str",
            "host_id": "node-id:str",
            "mgmt_ip": "ip:str",
            "oci_ref": "oci-ref:str",
            "env": "env-map:obj",
        },
        outputs={"container_running": "bool"},
        after=("host-ready[$host]", "provision-app[$app]"),
        cleanup_strategy=CleanupStrategy.REDO,
        idempotency_class=IdempotencyClass.CONFIG,
        resource_categories=("ansible-pool", "vm:$vmid"),
        timeout="600s",
        redo_on=("apps[*].install",),
    ),
    OpDefinition(
        id="OP-APP-030",
        name="run-script",
        kind=OpKind.ACTION,
        description=(
            "Execute a {script:} install action once at first boot. Opaque, "
            "non-idempotent: cleanup_strategy is escalate."
        ),
        inputs={
            "app_id": "app-id:str",
            "host_id": "node-id:str",
            "mgmt_ip": "ip:str",
            "script_path": "path:str",
        },
        outputs={"script_ran": "bool"},
        # provision-app / run-container are {app,host}-scoped, so reference them
        # by `$app@*` (any instance with this app) rather than `$app` (which the
        # exact-set matcher reads as app-only). See pass3_ordering scope rules.
        after=("host-ready[$host]", "provision-app[$app@*]", "run-container[$app@*]"),
        cleanup_strategy=CleanupStrategy.ESCALATE,
        idempotency_class=IdempotencyClass.CONFIG,
        resource_categories=("ansible-pool", "vm:$vmid"),
        timeout="600s",
        redo_on=("apps[*].install",),
    ),
    # -------------------------------------------------------------------
    # OP-OVL — Overlay materialization (6 ops)
    # -------------------------------------------------------------------
    OpDefinition(
        id="OP-OVL-010",
        name="wait-domain-ready",
        kind=OpKind.WAIT,
        description=(
            "Poll a domain controller until AD/LDAP/Kerberos answer. Gates "
            "all domain-scoped identity ops."
        ),
        inputs={
            "domain_fqdn": "domain-fqdn:str",
            "dc_host_id": "node-id:str",
            "dc_mgmt_ip": "ip:str",
        },
        outputs={"domain_serving": "bool"},
        # `$dc_app@*` (star-tail), not `$dc_app`: provision-app is {app,host}-scoped,
        # so the exact-set matcher never resolves the single-scope form (same latent
        # fix as the OP-ACT cross-op patterns). Routed CONFIG so the executor sends
        # the in-guest Get-ADDomain poll to the ConfigBackend over QGA (like
        # wait-actor-ready); it holds only vm:$dc_vmid, never the ansible-pool slot.
        after=("provision-app[$dc_app@*]",),
        idempotency_class=IdempotencyClass.CONFIG,
        resource_categories=("vm:$dc_vmid",),
        timeout="600s",
    ),
    OpDefinition(
        id="OP-OVL-020",
        name="create-domain-user",
        kind=OpKind.ACTION,
        description=(
            "Create one identity in its AD domain (or local user if no "
            "domain:). Not emitted for external: true identities."
        ),
        inputs={
            "identity_id": "identity-id:str",
            "domain_fqdn": "domain-fqdn:str",
            "target_host_id": "node-id:str",
            "groups": "str-list:list",
            "privileges": "str-list:list",
        },
        outputs={"user_created": "bool"},
        # Conditional `after` (OP_CATALOG §5.5): domain users carry `domain` in
        # scope and gate on domain-ready[$domain] (host-ready[$host=DC] also
        # resolves — a redundant-but-valid predecessor); local (no-`domain:`)
        # users carry only `host` and gate on host-ready[$host]. _resolve_pattern
        # drops a pattern whose scope var is absent, so each entry selects itself.
        after=("domain-ready[$domain]", "host-ready[$host]"),
        cleanup_strategy=CleanupStrategy.REDO,
        idempotency_class=IdempotencyClass.CONFIG,
        resource_categories=("ansible-pool", "vm:$target_vmid"),
        timeout="300s",
        redo_on=(
            "overlays.identity[*].domain",
            "overlays.identity[*].host",
            "overlays.identity[*].groups",
            "overlays.identity[*].privileges",
        ),
    ),
    OpDefinition(
        id="OP-OVL-030",
        name="join-host-to-domain",
        kind=OpKind.ACTION,
        description=(
            "Join a host to an AD domain so domain identities can authenticate "
            "on it. Emitted from the EXPLICIT `node.joins: <fqdn>` IR field (W2; "
            "supersedes the I1=infer activity-placement inference). Points the "
            "host's DNS at the DC, runs Add-Computer with the domain admin "
            "credential, and reboots."
        ),
        inputs={
            "host_id": "node-id:str",
            "domain_fqdn": "domain-fqdn:str",
            "mgmt_ip": "ip:str",
            "dc_ip": "ip:str",          # the DC's IP — the host's DNS for the join
        },
        outputs={"host_joined": "bool"},
        after=("domain-ready[$domain]", "host-ready[$host]"),
        cleanup_strategy=CleanupStrategy.REDO,
        idempotency_class=IdempotencyClass.CONFIG,
        resource_categories=("ansible-pool", "vm:$vmid"),
        timeout="600s",
        redo_on=("nodes[*].joins",),
    ),
    OpDefinition(
        id="OP-OVL-040",
        name="seed-dataset",
        kind=OpKind.ACTION,
        description=(
            "Materialize synthetic data at path on the host. Deterministic "
            "from scenario seed when reproducible."
        ),
        inputs={
            "data_id": "data-id:str",
            "host_id": "node-id:str",
            "path": "path:str",
            "classification": "str",
            "synthetic_size": "size:str",
            "seed": "int",
        },
        outputs={"dataset_seeded": "bool"},
        # Gate on the host's apps (star-tail, like OP-ACT): a dataset may land in
        # a directory an app creates (a web root, a share), so it must not race
        # provision-app/run-script. host-ready is single-scope so `[$host]` resolves.
        after=(
            "host-ready[$host]",
            "provision-app[$host@*]",
            "run-script[$host@*]",
        ),
        cleanup_strategy=CleanupStrategy.REDO,
        idempotency_class=IdempotencyClass.CONFIG,
        resource_categories=("ansible-pool", "vm:$vmid"),
        timeout="600s",
        redo_on=(
            "overlays.data[*].path",
            "overlays.data[*].synthetic_size",
            "overlays.data[*].classification",
        ),
    ),
    OpDefinition(
        id="OP-OVL-050",
        name="install-defense-agent",
        kind=OpKind.ACTION,
        description=(
            "Install a host-agent defense tool (EDR etc.) on one installed_on host."
        ),
        inputs={
            "defense_id": "defense-id:str",
            "tool": "str",
            "host_id": "node-id:str",
            "config": "path:str",
            "ruleset": "str",
        },
        outputs={"agent_installed": "bool"},
        # Gate on the host's apps (star-tail): the agent binary is installed by an
        # app, so the overlay's config/enable must run after it. host-ready is
        # single-scope so `[$host]` resolves. `domain-ready[$domain]` resolves ONLY
        # for a DC-hosted agent (the planner adds `domain` to its scope) — host-side
        # AD auditing on a DC must run post-promotion so it isn't reset by the
        # promotion reboot / Default DC Policy (conditional, like create-domain-user).
        after=(
            "host-ready[$host]",
            "provision-app[$host@*]",
            "run-script[$host@*]",
            "domain-ready[$domain]",
        ),
        cleanup_strategy=CleanupStrategy.REDO,
        idempotency_class=IdempotencyClass.CONFIG,
        resource_categories=("ansible-pool", "vm:$vmid"),
        timeout="600s",
        redo_on=(
            "overlays.defense[*].installed_on",
            "overlays.defense[*].config",
            "overlays.defense[*].ruleset",
        ),
    ),
    OpDefinition(
        id="OP-OVL-060",
        name="deploy-network-sensor",
        kind=OpKind.ACTION,
        description=(
            "Bring up a network-mode defense tool observing one or more "
            "nets. tap_from: bridge reuses the sensor's OVS mirror. "
            "SUPERSEDED (sensors design, docs/design/06-taps-sensors.md) by the "
            "in-scenario `suricata-sensor` type — declare a sensor in `sensors:` "
            "with `captures:` instead of an overlays.defense `observes:` network sensor. "
            "Kept for back-compat with existing defense overlays; not extended."
        ),
        inputs={
            "defense_id": "defense-id:str",
            "tool": "str",
            "sensor_host_id": "node-id:str",
            "observes": "observe-list:list",
            "tap_from": "tap-from:str",
            "ruleset": "str",
            "config": "path:str",
        },
        outputs={"sensor_live": "bool"},
        # Option C (network-sensor Suricata, docs/SLICE_OVERLAY.md §4b): the sensor
        # reads a mirror of the sensor's already-aggregated capture feed, so it gates
        # on the capture path being live (`mirror-ready[$sensor]`) — NOT on a scenario
        # sensor node or the per-net capture ops (the A/B `host-ready`/
        # `attach-gre-tunnel`/`install-ovs-mirror` predecessors are dropped). It does
        # no host OVS work (the network sensor + mirror are standing infra), so it holds
        # only the ansible-pool throughput slot — no `ovs:`/`vm:` categories.
        after=("mirror-ready[$sensor]",),
        cleanup_strategy=CleanupStrategy.REDO,
        idempotency_class=IdempotencyClass.CONFIG,
        resource_categories=("ansible-pool",),
        timeout="600s",
        redo_on=(
            "overlays.defense[*].observes",
            "overlays.defense[*].tap_from",
            "overlays.defense[*].ruleset",
        ),
    ),
    OpDefinition(
        id="OP-OVL-070",
        name="install-agent",
        kind=OpKind.ACTION,
        description=(
            "Install a non-defense agent (Prometheus node-exporter, or the "
            "Sandcat/Caldera adversary agent) on one host, config-class over "
            "QGA. A3 Stage A (docs/design/08-exercise-measurement.md) -- "
            "mirrors install-defense-agent's render/backend pattern for the "
            "exporter/adversary agent kinds so install-defense-agent itself "
            "stays defense-only. `agent_kind` selects the render branch; "
            "adversary additionally requires `server` (the Caldera server the "
            "agent beacons to -- the O2 server is Stage B, not yet stood up, "
            "so the plan carries it as an explicit input, not an adapter-"
            "resolved default). agent_kind=exporter is emitted from the "
            "default-agent-set sweep when `scenario.default_agents: true` -- "
            "a te-deploy-time default, unaffected by the note below. "
            "EMISSION CONTEXT for agent_kind=adversary (2026-07-24, "
            "docs/WORK_ORDER_exercise_tier.md, the Exercise-tier scope-shift): "
            "the te planner no longer emits this op for a declared "
            "`overlays.adversary[].targets:` entry -- it is emitted "
            "exclusively by the standalone Exercise (`exr`) tier playout "
            "(`axrange.o2control.exercise.compose_exercise_ops`) against an "
            "already-deployed twin's explicit `attack.adversary.targets:`, "
            "mirroring OP-MIR-040 verify-capture's identical A2 emission-"
            "context move."
        ),
        inputs={
            "agent_id": "agent-id:str",
            "agent_kind": "str",  # "exporter" | "adversary"
            "host_id": "node-id:str",
            "agent_entry": "obj",
            "server": "str",      # adversary only
        },
        outputs={"agent_installed": "bool"},
        # Same star-tail gating as install-defense-agent (host-ready plus the
        # host's apps -- the exporter/Sandcat binary may itself be fetched
        # in-op, but must not race an app that's still converging).
        after=(
            "host-ready[$host]",
            "provision-app[$host@*]",
            "run-script[$host@*]",
        ),
        cleanup_strategy=CleanupStrategy.REDO,
        idempotency_class=IdempotencyClass.CONFIG,
        resource_categories=("ansible-pool", "vm:$vmid"),
        timeout="600s",
        redo_on=(
            "scenario.default_agents",
            "nodes[*].default_agents_off",
            "overlays.adversary[*].targets",
            "overlays.adversary[*].server",
            "overlays.adversary[*].group",
        ),
    ),
    # -------------------------------------------------------------------
    # OP-ACT — Activity actor (3 ops)
    # -------------------------------------------------------------------
    OpDefinition(
        id="OP-ACT-010",
        name="deploy-behavior-agent",
        kind=OpKind.ACTION,
        description=(
            "Install the behavior-runner and resolved behavior definition "
            "onto the activity's bound host. Idempotent Ansible. "
            "EMISSION CONTEXT (2026-07-24, docs/WORK_ORDER_exercise_tier.md, "
            "the Exercise-tier scope-shift): the te planner no longer emits "
            "this op (nor start-activity/wait-actor-ready, OP-ACT-020/030) "
            "for a declared `layers.activity:` -- a te deploy stands up only "
            "the environment. The OP-ACT trio is emitted exclusively by the "
            "standalone Exercise (`exr`) tier playout (`axrange.o2control."
            "exercise.compose_exercise_ops`) against an already-deployed "
            "twin's `attack.activity`, mirroring OP-MIR-040 verify-capture's "
            "identical A2 emission-context move."
        ),
        inputs={
            "activity_id": "activity-id:str",
            "host_id": "node-id:str",
            "mgmt_ip": "ip:str",
            "actor_resolved": "resolved-actor:obj",
            "behavior_def": "behavior-def:obj",
            "targets": "node-id-list:list",
            "intensity": "float",
            "schedule": "schedule:obj",
        },
        outputs={"agent_deployed": "bool"},
        # `provision-app`/`run-script` are {app,host}-scoped, so reference them by
        # `$host@*` (any app on this host) — not `$target_app`, which the exact-set
        # matcher reads as app-only and never resolves (see pass3_ordering scope
        # rules; same fix as run-script's `provision-app[$app@*]`). This gates the
        # activity on the host's tooling installs (e.g. nmap) so a recon step never
        # runs before its tools exist. The domain-identity predecessors stay for
        # forward-compat: they resolve to zero ops (no `$actor`/`$domain` in scope)
        # and drop in the inline-steps MVP (local/external actors only).
        after=(
            "host-ready[$host]",
            "create-domain-user[$actor]",
            "join-host-to-domain[$host@$domain]",
            "provision-app[$host@*]",
            "run-script[$host@*]",
        ),
        cleanup_strategy=CleanupStrategy.REDO,
        idempotency_class=IdempotencyClass.CONFIG,
        resource_categories=("ansible-pool", "vm:$vmid"),
        timeout="600s",
        redo_on=(
            "activity[*].behavior",
            "activity[*].overrides",
            "activity[*].inline",
            "activity[*].actor",
        ),
    ),
    OpDefinition(
        id="OP-ACT-020",
        name="start-activity",
        kind=OpKind.ACTION,
        description=(
            "Start the deployed behavior agent's generation process. Gated "
            "on mirror-ready so capture is live before traffic generation."
        ),
        inputs={
            "activity_id": "activity-id:str",
            "host_id": "node-id:str",
            "mgmt_ip": "ip:str",
        },
        outputs={"activity_started": "bool"},
        # `[$act@*]` (not `[$act]`): deploy-behavior-agent is multi-scope
        # ({act,host}), which the exact-set matcher won't match on `$act` alone.
        after=("deploy-behavior-agent[$act@*]", "mirror-ready[$sensor]"),
        cleanup_strategy=CleanupStrategy.REDO,
        idempotency_class=IdempotencyClass.CONFIG,
        resource_categories=("ansible-pool", "vm:$vmid"),
        timeout="120s",
        redo_on=(),
    ),
    OpDefinition(
        id="OP-ACT-030",
        name="wait-actor-ready",
        kind=OpKind.WAIT,
        description=(
            "Poll the behavior agent until it reports healthy and registered."
        ),
        inputs={
            "activity_id": "activity-id:str",
            "host_id": "node-id:str",
            "mgmt_ip": "ip:str",
        },
        outputs={"actor_ready": "bool"},
        # `[$act@*]`: start-activity is multi-scope ({act,host,sensor}).
        after=("start-activity[$act@*]",),
        # CONFIG-class so the executor routes this wait op to the ConfigBackend,
        # which polls the runner's completion marker over the QGA channel (the
        # executor calls backend.run() for wait ops too — no separate poll loop).
        idempotency_class=IdempotencyClass.CONFIG,
        resource_categories=("vm:$vmid",),
        timeout="180s",
    ),
    # -------------------------------------------------------------------
    # OP-TFC — Traffic-tier persona driving (1 op; added 2026-07-24 for the
    # Traffic tier, docs/WORK_ORDER_traffic_tier.md). NOT emitted by the te
    # planner (pass1_emit never builds one) -- composed by hand, exactly like
    # OP-MIR-010/020/040 are, by the standalone `tfc` playout
    # (axrange.o2control.traffic._compose_ops), which binds a traffic
    # profile onto an ALREADY-DEPLOYED twin's hosts. Adapter-side execution
    # is that module's own `PersonaBackend` (config_exec.ConfigBackend would
    # create an import cycle with axrange.personas -- see traffic.py's module
    # docstring), reached the same way every other config-class op reaches
    # its ConfigBackend: routed by idempotency_class=CONFIG.
    # -------------------------------------------------------------------
    OpDefinition(
        id="OP-TFC-010",
        name="drive-persona",
        kind=OpKind.ACTION,
        description=(
            "(Re)start one persona instance on an already-deployed twin host "
            "via its driver (scripted/replay/real-service/command, axrange.personas."
            "drivers). Stable per-instance scope (no content hash); the "
            "Traffic-tier playout force-resets this op to pending on every "
            "apply so a changed profile is re-asserted, not silently skipped "
            "(LES-057, mirrors install-ovs-mirror's own re-assert posture)."
        ),
        inputs={
            "persona_id": "persona-id:str",
            "drive": "drive-kind:str",
            "instance_id": "instance-id:str",
            "host_id": "node-id:str",
            "vmid": "vmid:int",
            "peers": "peer-map:obj",
            "seed": "seed:any",
        },
        outputs={"started": "bool"},
        after=(),
        cleanup_strategy=CleanupStrategy.REDO,
        idempotency_class=IdempotencyClass.CONFIG,
        resource_categories=("ansible-pool", "vm:$vmid"),
        timeout="120s",
    ),
    OpDefinition(
        id="OP-TFC-020",
        name="assign-role",
        kind=OpKind.ACTION,
        description=(
            "Record the tfc-owned SEMANTIC ROLE of one already-deployed twin "
            "host (docs/WORK_ORDER_role_persona_tier.md, Authority: user, "
            "2026-08-04). Pure metadata: NO adapter call, nothing on the "
            "wire, no guest exec -- the twin's published inventory prefers "
            "this over clone-vm's recorded `node_role` baseline, which is "
            "what makes a role re-assignable without re-cloning the node "
            "(the role used to live INSIDE clone-vm's persisted inputs, and "
            "that is precisely why changing one needed a redeploy). "
            "Empty `resource_categories` because it touches no cluster "
            "resource at all; the Traffic-tier playout force-resets it to "
            "pending on every apply so a changed role map is re-asserted, "
            "not silently skipped (LES-057, same posture as drive-persona "
            "above). A host may carry a role while running NO persona -- a "
            "peer target (`emits[].peer_role`) need not emit anything "
            "itself -- which is why this is its own per-host op rather than "
            "an input threaded onto drive-persona."
        ),
        inputs={
            "host_id": "node-id:str",
            "role": "role:str",
        },
        outputs={"assigned": "bool"},
        after=(),
        cleanup_strategy=CleanupStrategy.REDO,
        idempotency_class=IdempotencyClass.CONFIG,
        resource_categories=(),
        timeout="10s",
    ),
]


_BARRIERS: list[Barrier] = [
    Barrier(
        id="OP-BAR-010",
        name="network-ready",
        scope="net-id",
        description=(
            "Network's vnet/bridge exist and SDN config is applied — the "
            "network is usable."
        ),
        predecessors=("apply-sdn-config[$scenario]",),
        consumed_by=("attach-vm-to-vnet", "attach-gre-tunnel"),
    ),
    Barrier(
        id="OP-BAR-020",
        name="host-ready",
        scope="host-id",
        description=(
            "Host is cloned, configured, attached, started; OS is reachable; "
            "for tapped hosts, mirror hook installed; for routers, routes applied."
        ),
        predecessors=(
            # start-vm anchors the barrier to the VM actually starting. For agent
            # nodes this is redundant (wait-os-ready already follows start-vm), but
            # agent:false nodes (e.g. VyOS routers, no QGA) skip the wait-* ops, so
            # without this the barrier would have no predecessors and fire before
            # the VM ever started — configure-router then ran against a dead VM.
            "start-vm[$host]",
            "wait-os-ready[$host]",
            "configure-static-routes[$host]",
            "install-ovs-mirror[$host]",
        ),
        consumed_by=(
            "provision-app",
            "run-container",
            "run-script",
            "create-domain-user",
            "join-host-to-domain",
            "seed-dataset",
            "install-defense-agent",
            "deploy-behavior-agent",
        ),
    ),
    Barrier(
        id="OP-BAR-030",
        name="mirror-ready",
        scope="sensor-id",
        description=(
            "Capture path is PROVEN live: every GRE tunnel attached, every "
            "tapped host's mirror hook installed, and (dynamic-tap heartbeat, "
            "docs/design/06-taps-sensors.md) a sentinel frame confirmed at "
            "the sensor for each (bridge, sensor) with a tap. Upgraded from 'mirror "
            "commands issued' to 'a frame provably reached the sensor'."
        ),
        # `verify-capture[*]` (added 2026-06-19): every per-(bridge,sensor) heartbeat
        # must pass before activities can generate traffic (start-activity gates on
        # mirror-ready). verify-capture itself depends on install-ovs-mirror[*] +
        # attach-gre-tunnel, so this barrier transitively still waits on those.
        predecessors=(
            "attach-gre-tunnel[*]",
            "install-ovs-mirror[*]",
            "verify-capture[*]",
        ),
        consumed_by=("start-activity",),
    ),
    Barrier(
        id="OP-BAR-040",
        name="domain-ready",
        scope="domain-fqdn",
        description="Domain controller's AD directory services answer (LDAP + Kerberos).",
        # `$domain@*` (star-tail): wait-domain-ready is {domain,dc_app}-scoped, so the
        # single-scope form never resolves (same fix as the after edge above).
        predecessors=("wait-domain-ready[$domain@*]",),
        consumed_by=("create-domain-user", "join-host-to-domain"),
    ),
    Barrier(
        id="OP-BAR-050",
        name="overlay-ready",
        scope="kind:id",
        description=(
            "A specific overlay entry is fully materialized. Activities can "
            "depend on precise overlays without serializing on all of them."
        ),
        predecessors=(
            "seed-dataset[$id]",
            "create-domain-user[$id]",
            "install-defense-agent[$id]",
            "deploy-network-sensor[$id]",
        ),
        consumed_by=("deploy-behavior-agent",),
    ),
    Barrier(
        id="OP-BAR-060",
        name="fabric-ready",
        scope="scenario-id",
        description=(
            "All fabric routers have their dynamic-routing/overlay config applied "
            "(configure-routing converged). Apps/activities that need cross-fabric "
            "reachability gate on this. Emitted only when >=1 router declares "
            "`routing:` (fabric slice, docs/design/04-networking-capture.md)."
        ),
        predecessors=("configure-routing[*]",),
        consumed_by=(
            "provision-app",
            "run-container",
            "run-script",
            "deploy-behavior-agent",
            "start-activity",
        ),
    ),
]


# Public registry: dict by id and by name for both ops and barriers.
REGISTRY: dict[str, OpDefinition | Barrier] = {}
for _op in _OPS:
    REGISTRY[_op.id] = _op
    REGISTRY[_op.name] = _op
for _b in _BARRIERS:
    REGISTRY[_b.id] = _b
    REGISTRY[_b.name] = _b


def get_op(key: str) -> OpDefinition | None:
    """Look up by id or name. Returns None for barriers."""
    v = REGISTRY.get(key)
    return v if isinstance(v, OpDefinition) else None


def get_barrier(key: str) -> Barrier | None:
    v = REGISTRY.get(key)
    return v if isinstance(v, Barrier) else None


def list_actions_and_waits() -> list[OpDefinition]:
    return list(_OPS)


def list_barriers() -> list[Barrier]:
    return list(_BARRIERS)


# Self-check: catalog totals match the spec. 34 action/wait (OP-MIR-005
# record-tap-intent retired, docs/WORK_ORDER_tap_eligibility.md, 2026-07-28
# -- op count 40 -> 39; OP-TFC-020 assign-role added,
# docs/WORK_ORDER_role_persona_tier.md, 2026-08-04 -- 39 -> 40) + 6 barriers.
assert len(_OPS) == 34, f"OP_CATALOG action/wait count must be 34 (got {len(_OPS)})"
assert len(_BARRIERS) == 6, f"OP_CATALOG barrier count must be 6 (got {len(_BARRIERS)})"
