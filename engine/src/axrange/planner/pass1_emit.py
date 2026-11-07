"""Pass 1: walk the IR and emit op instances + synthetic barriers for the
slice subset (docs/ADAPTER.md §6 — 16 ops).

Emission rules (per docs/OP_CATALOG.md §5.1–5.3, §5.7):

  Per scenario:
    - 1 create-sdn-zone[$scenario]
    - 1 apply-sdn-config[$scenario]

  Per network:
    - 1 create-ovs-bridge[$net]
    - 1 create-vnet[$net]
    - 1 network-ready[$net] barrier

  Per host (kind: host only — routers/switches out of slice):
    - 1 clone-vm[$host]
    - 1 configure-vm-hardware[$host]
    - 1 configure-cloud-init[$host]
    - 1 attach-vm-to-vnet[$host@$net] per attached net
    - 1 start-vm[$host]
    - 1 wait-guest-agent[$host]
    - 1 wait-os-ready[$host]
    - 1 host-ready[$host] barrier

A te deploy is fully tap-agnostic (`docs/WORK_ORDER_tap_eligibility.md`,
Authority: user, 2026-07-28): it emits NO OP-MIR op at all — no
attach-gre-tunnel / install-ovs-mirror / verify-capture, and (as of this
change) no record-tap-intent bookkeeping either — and no mirror-ready
barrier. `attach-gre-tunnel`/`install-ovs-mirror`/`verify-capture` stay in
the catalog, emitted exclusively by the A2 tap playout
(`axrange.o2control.taps`) against an already-deployed te's PUBLISHED
TOPOLOGY (`hosts[].nets[]`), never against a tap-intent subset this pass
used to record.

Inputs that are known statically (IR fields + image binding fields +
planner-derived values like target_node) land in `inputs_resolved`. Inputs that
depend on a prior op's output (e.g. vmid from clone-vm) are recorded in
`inputs_from_outputs` and resolved by the executor at run time.

Out of scope for this pass: apps, overlays, activity, identity, the router
static-routes op, defense sensors. Pass-2 (data-flow) and Pass-3 (ordering)
build on whatever this pass emits.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import logging

from axrange.ir.capabilities import install_action_kind
from axrange.ir.schema import IR, NetRef, Network, Node, Resources
from axrange.manifest.loader import ManifestRegistry
from axrange.manifest.schema import BindingVersion
from axrange.overlays.default_agents import (
    DEFAULT_DEFENSE_TOOL,
    EXPORTER_TOOL_ALIASES,
    default_defense_entry,
    default_exporter_entry,
    hosts_with_explicit_defense_tool,
    resolve_default_kinds,
)
from axrange.ops.registry import (
    CleanupStrategy,
    IdempotencyClass,
    OpKind,
    get_barrier,
    get_op,
)
from axrange.planner.cluster import ClusterContext
from axrange.planner.dag import BarrierInstance, OpInstance, Plan, _op_id
from axrange.planner.egress import EgressDeclarationError, resolve_egress_policy


class EmissionError(Exception):
    """Raised when Pass-1 can't make sense of the IR.

    Pass-1 expects a validated IR (validate_full passing). Findings like a
    node referencing a missing net would already be REF-class errors. This
    exception covers slice-specific limitations (e.g. node.kind != host)
    and is the surface the CLI converts to user-facing messages.
    """


# Catalog ID lookup so we don't repeat strings in the emit logic.
_CAT = {
    "create-sdn-zone": "OP-NET-010",
    "create-ovs-bridge": "OP-NET-020",
    "create-vnet": "OP-NET-030",
    "apply-sdn-config": "OP-NET-040",
    "create-subnet": "OP-NET-050",
    "clone-vm": "OP-HOST-010",
    "configure-vm-hardware": "OP-HOST-020",
    "configure-cloud-init": "OP-HOST-030",
    "attach-vm-to-vnet": "OP-HOST-040",
    "configure-router": "OP-HOST-090",
    "start-vm": "OP-HOST-060",
    "wait-guest-agent": "OP-HOST-070",
    "wait-os-ready": "OP-HOST-080",
    "attach-gre-tunnel": "OP-MIR-010",
    "install-ovs-mirror": "OP-MIR-020",
    "network-ready": "OP-BAR-010",
    "host-ready": "OP-BAR-020",
    "mirror-ready": "OP-BAR-030",
    "provision-app": "OP-APP-010",
    "run-script": "OP-APP-030",
}

# Install-action kinds the config slice executes today (docs/SLICE_CONFIG.md):
# pkg / pkg@version -> provision-app; script -> run-script. config / role /
# container are deferred (rejected at emit time with a clear message).
_PKG_KINDS = {"pkg", "pkg@version"}


def _parse_image_ref(image: str | None) -> tuple[str | None, str | None]:
    """`<image-id>[@<version>]` -> (image-id, version|None)."""
    if not image:
        return None, None
    if "@" in image:
        head, _, tail = image.partition("@")
        return head, tail
    return image, None


def _binding_for_node(
    node: Node, manifests: ManifestRegistry
) -> tuple[str | None, BindingVersion | None]:
    image_id, version_id = _parse_image_ref(node.image)
    if image_id is None:
        return None, None
    binding = manifests.get_binding(image_id, "proxmox")
    if binding is None:
        return image_id, None
    if version_id is None:
        # Pick "current"-equivalent: the manifest exists, but pinning isn't
        # enforced here (REPRO-* covers it). Fall back to first version.
        if binding.versions:
            return image_id, binding.versions[0]
        return image_id, None
    return image_id, binding.get_version(version_id)


_log = logging.getLogger(__name__)


def _is_router(node: Node) -> bool:
    """True for `kind: router` nodes (router slice Phase 2)."""
    return node.kind is not None and str(node.kind).lower().split(".")[-1] == "router"


# Uplink bridge a router's WAN NIC attaches to for internet/egress. The host's
# internet-facing bridge; configurable later, vmbr0 by convention.
ROUTER_WAN_BRIDGE = "vmbr0"


def _node_nets(node: Node) -> list[tuple[str, str | None]]:
    """Return [(net_id, ip|None), ...] for the IR node, handling both
    single-homed (`net:`) and multi-homed (`nets:`) forms.
    """
    if node.nets:
        return [
            (n.net or "", n.ip)
            for n in node.nets
            if n.net
        ]
    if node.net:
        return [(node.net, None)]
    return []


def _is_forwarder(node: Node) -> bool:
    """A non-router node with legs on two or more segments: an L2 bridge VM
    or an L3 `kind: host` router stand-in (docs/WORK_ORDER_corp_twin.md §10
    P0). The planner cannot tell a forwarding host from a merely dual-homed
    one, and does not need to: every use of this is an ORDERING/roster
    closure, which is safe (only ever later) for a host that forwards
    nothing. Shipped multi-homed hosts (corp-twin's device VMs and its 9K
    pair) are exactly such nodes; a plan without one is unaffected.

    Pass 3 has its own copy of this predicate over the PLAN
    (`pass3_ordering.forwarder_layers`: a non-router host with two or more
    non-WAN `attach-vm-to-vnet` rows) because it sees ops, not IR nodes --
    the same set of hosts, read from the other side of emission."""
    return not _is_router(node) and len({nid for nid, _ in _node_nets(node)}) >= 2


def _nets_reached_through_forwarders(seed: set[str], nodes: list[Node]) -> set[str]:
    """`seed` plus every segment reachable from it across forwarder nodes
    (`_is_forwarder`) -- never across another router.

    Deliberately NOT pass 3's `forwarder_layers` walk: that one refuses to
    let a router segment join two forwarders (it orders forwarders into
    layers and components), while this one only needs "which segments can a
    host reach this router's NAT from" -- a plain transitive closure, where
    a router segment in `seed` is exactly where the walk should start."""
    reached = set(seed)
    forwarders = [
        {nid for nid, _ in _node_nets(n)} for n in nodes if _is_forwarder(n)
    ]
    changed = True
    while changed:
        changed = False
        for nets in forwarders:
            if nets & reached and not nets <= reached:
                reached |= nets
                changed = True
    return reached


def _network_by_id(ir: IR, net_id: str) -> Network | None:
    if ir.layers is None or ir.layers.infra is None:
        return None
    for n in ir.layers.infra.networks:
        if n.id == net_id:
            return n
    return None


# `_is_vyos_node`/`_node_nets_with_nic` REMOVED here (`docs/
# WORK_ORDER_tap_eligibility.md`, Authority: user, 2026-07-28) -- both
# existed solely to serve the now-removed tap resolver ("the tap resolver
# and the mirror op share" a nic_index formula, their own docstrings said).
# The host-attach loop below computes `is_vyos`/nic_index inline; nothing
# else needed this pair.


def _wan_nic_index(node: Node, is_vyos: bool) -> int:
    """The router's WAN NIC index -- VyOS puts it at net0/eth0 (the baked
    mgmt iface `vyos-ssh-set` needs pre-LAN-attach), every other router
    appends it LAST, after all LAN legs. Fix round S2 (`docs/
    WORK_ORDER_tap_scopes.md`): extracted so its two callers (the WAN
    `attach-vm-to-vnet` emission, and formerly the M3 record-tap-intent WAN
    entry -- retired, `docs/WORK_ORDER_tap_eligibility.md`) could never
    independently drift on this formula -- they used to each inline
    `0 if is_vyos else len(_node_nets(node))` separately."""
    return 0 if is_vyos else len(_node_nets(node))


def _resources_dict(node: Node) -> dict[str, Any]:
    """Pull (cpu, ram, disk) from node.resources, with None tolerated."""
    if node.resources is None:
        return {"cpu": None, "ram": None, "disk": None}
    return {
        "cpu": node.resources.cpu,
        "ram": node.resources.ram,
        "disk": node.resources.disk,
    }


def _zone_type(networks: list[Network]) -> str:
    """Simple vs. VLAN: if any network sets vlan, the zone is VLAN-typed."""
    for n in networks:
        if n.vlan is not None:
            return "vlan"
    return "simple"


def _bridge_name(net_id: str) -> str:
    """Deterministic OVS bridge name. Slice convention: `vmbr-scn-<net-id>`.

    The full plan-id isn't included because OVS bridge names have a length
    cap (~15 chars on older kernels); we trim aggressively but stay readable.
    """
    cleaned = net_id.replace("_", "-")
    name = f"vbr-{cleaned}"
    return name[:15]


def _vnet_id(net_id: str) -> str:
    """Deterministic Proxmox vnet id. Convention: `vn-<net-id>` (trimmed)."""
    cleaned = net_id.replace("_", "-")
    return f"vn-{cleaned}"[:8]


# Dynamic tap resolution (node-tap/leg-tap/segment-tap -> a resolved
# tapped-interface set feeding `record-tap-intent`) REMOVED here
# (`docs/WORK_ORDER_tap_eligibility.md`, Authority: user, 2026-07-28) --
# `TapInterface`/`_resolve_tapped_interfaces`/`_net_refs_for`/
# `_net_is_tapped`/`_resolve_all_tapped_interfaces` are all gone. The twin
# substrate no longer resolves or records anything tap-related at emit
# time; the A2 tap playout resolves eligibility itself, against the
# published topology, at bind time (`axrange.planner.tappoints`).


# ---------------------------------------------------------------------------
# Op emission
# ---------------------------------------------------------------------------


def _make_op(
    plan_id: str,
    op_name: str,
    scope: dict[str, str],
    *,
    inputs_resolved: dict[str, Any] | None = None,
    inputs_from_outputs: dict[str, tuple[str, str]] | None = None,
    planner_hints: dict[str, Any] | None = None,
) -> OpInstance:
    op_def = get_op(op_name)
    if op_def is None:
        raise EmissionError(f"unknown op name: {op_name}")
    return OpInstance(
        op_id=_op_id(plan_id, op_name, scope),
        op_name=op_name,
        op_catalog_id=op_def.id,
        kind=op_def.kind,
        scope=dict(scope),
        inputs_resolved=inputs_resolved or {},
        inputs_from_outputs=inputs_from_outputs or {},
        cleanup_strategy=op_def.cleanup_strategy,
        idempotency_class=op_def.idempotency_class,
        resource_categories=op_def.resource_categories,
        timeout_s=_parse_timeout(op_def.timeout),
        # Planner-internal only -- `state.op_to_row` never serializes this
        # (see `OpInstance.planner_hints`'s docstring, dag.py). Distinct from
        # `inputs_resolved`: a caller that needs the value at EXECUTION time
        # (the adapter/config_exec) must use `inputs_resolved`, not this.
        planner_hints=planner_hints or {},
    )


def _make_barrier(plan_id: str, name: str, scope: dict[str, str]) -> BarrierInstance:
    b_def = get_barrier(name)
    if b_def is None:
        raise EmissionError(f"unknown barrier name: {name}")
    return BarrierInstance(
        op_id=_op_id(plan_id, name, scope),
        barrier_name=name,
        barrier_catalog_id=b_def.id,
        scope=dict(scope),
    )


def _parse_timeout(spec: str | None) -> int | None:
    """Convert OpDefinition.timeout (e.g. "60s", "120s") to integer seconds."""
    if spec is None:
        return None
    s = spec.strip().lower()
    if s.endswith("ms"):
        return max(1, int(float(s[:-2]) / 1000))
    if s.endswith("s"):
        return int(float(s[:-1]))
    if s.endswith("m"):
        return int(float(s[:-1]) * 60)
    return int(s)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def emit_ops(
    plan: Plan,
    ir: IR,
    manifests: ManifestRegistry,
    ctx: ClusterContext,
    scenario_dir: str | Path | None = None,
) -> None:
    """Walk the slice subset of the IR and populate Plan.ops + Plan.barriers.

    Mutates `plan` in place. Pass-2 and Pass-3 then add edges. `scenario_dir`
    is the directory of the scenario file — needed to read `{script:}` action
    files at emit time (the config backend can't reach the scenario bundle, and
    the guest is isolated). Only required when an app has a `script:` action.
    """
    if ir.scenario is None or ir.scenario.id is None:
        raise EmissionError("IR scenario.id is required for op emission")
    if ir.layers is None or ir.layers.infra is None:
        raise EmissionError("IR layers.infra is required for op emission")

    scenario_id = ir.scenario.id
    networks = list(ir.layers.infra.networks)
    nodes = list(ir.layers.infra.nodes)

    # ---- Slice gating ------------------------------------------------------
    # `host`, `router` and -- since the device tree (`docs/design/
    # topology-layers.md` §2.5, 2026-11-07) -- `switch`. A switch WITH an
    # image is a VM like a host (one L2 NIC per segment its `nets[]` list,
    # which the twin-config expander derives from its links; the ordering
    # pass already treats a multi-legged non-router as a forwarder). A
    # switch WITHOUT an image deploys as nothing: its segments' vnets exist
    # on their own, so it leaves the node set here and emits no op. It stays
    # in the IR for the topology routes and the validator.
    for n in nodes:
        k = str(n.kind).lower().split(".")[-1] if n.kind is not None else "host"
        if k not in ("host", "router", "switch"):
            raise EmissionError(
                f"node {n.id!r} kind={n.kind!r}: only kind=host, kind=router and "
                "kind=switch are supported"
            )
    imageless_switches = [
        n.id for n in nodes
        if n.kind is not None and str(n.kind).lower().split(".")[-1] == "switch"
        and not n.image
    ]
    if imageless_switches:
        _log.info(
            "pass1: %d imageless switch(es) emit no VM (their segments' vnets "
            "carry them): %s", len(imageless_switches), imageless_switches,
        )
        nodes = [n for n in nodes if n.id not in imageless_switches]
    # apps + activity: config-class provisioning (OP-APP, docs/SLICE_CONFIG.md)
    # and the inline-steps activity engine (OP-ACT, docs/SLICE_ACTIVITY.md) are
    # both supported — emitted after the per-host ops below.
    # overlays: `data` (seed-dataset), `defense` host-agents (install-defense-agent)
    # + network sensors (deploy-network-sensor), and `identity` *domain users*
    # (create-domain-user, W2b — docs/SLICE_WINDOWS_AD.md §6.2) are supported —
    # emitted after the per-host ops below in _emit_overlays. Local (no-domain)
    # users and join-host-to-domain remain follow-ons handled/rejected there.

    # ---- Sensor-agnostic te (2026-07-13, docs/WORK_ORDER_sensor_agnostic_te.md;
    # fully tap-agnostic 2026-07-28, docs/WORK_ORDER_tap_eligibility.md) --
    # A te deploy no longer wires taps to a sensor at all — no attach-gre-tunnel /
    # install-ovs-mirror / verify-capture, and no dependency on
    # ClusterContext.sensor_transport_ip (retired from adapter.conf). It no
    # longer resolves or records ANY tap-related state either — binding a tap
    # to a standing O2 sensor and wiring the actual mirror/gretap/heartbeat is
    # entirely the A2 tap playout's job (`axrange tap deploy`), which resolves
    # eligibility itself against the twin's published topology, post-deploy.
    # `primary_sensor_id` survives only as a scope/op_id label for the
    # still-supported legacy `deploy-network-sensor` overlay op (OP-OVL-060,
    # SUPERSEDED/back-compat) — it is never adapter.conf-sourced
    # (ClusterContext.sensor_id defaults to the bare string "default", so this
    # is always a real string, never conditioned on tap state, D1 fix-through:
    # `_emit_overlays` below no longer receives an `any_tapped`-gated `None`).
    sensors_decl = ir.sensors or []
    primary_sensor_id = ctx.sensor_id
    if sensors_decl:
        primaries = [s.id for s in sensors_decl if s.primary and s.id]
        if len(primaries) > 1:
            raise EmissionError(f"more than one primary sensor declared: {primaries}")
        if primaries:
            primary_sensor_id = primaries[0]
        elif len(sensors_decl) == 1 and sensors_decl[0].id:
            primary_sensor_id = sensors_decl[0].id
        else:
            raise EmissionError(
                "sensors: declared with multiple entries but none marked primary: true"
            )
    # tap_host/tap_net -> sensor bindings REMOVED here (`docs/
    # WORK_ORDER_tap_eligibility.md` D6) -- the `Binding.tap_host`/`tap_net`/
    # `sensor` fields are gone from IR_SCHEMA; a te records no tap-intent to
    # carry a sensor-preference hint on any more.

    # ---- Sensors design Phase B: expand declared sensors to in-scenario VMs --
    # Each `sensors:` entry becomes a synthetic host VM on an isolated transport
    # vnet the PVE host can reach (a host-held gateway via create-subnet). The
    # synthetic node + net flow through the SAME network/host emission loops, so
    # a sensor reuses the proven lifecycle. docs/design/06-taps-sensors.md.
    #
    # TIER-GATED (2026-07-13 review fix F1, docs/WORK_ORDER_sensor_agnostic_te.md):
    # this is the mechanism `axrange.o2control.control.stand_up_surface` uses to
    # materialize the STANDING O2 sensors (`build_plan_and_ctx(tier="o2")`,
    # `manifest.py` — "pass1_emit's own sensors-design Phase B auto-synthesis").
    # It must NOT also fire for an ordinary `tier="te"` deploy: since the
    # attach-gre-tunnel/verify-capture removal above, a te-emitted sensor VM
    # would receive ZERO mirrored traffic and get NO heartbeat to catch it
    # (silent-blind, LES-001/004). So this whole block — and the deploy-sensor
    # loop below — is now `tier == "o2"`-only; a te with `sensors:` emits NONE
    # of it (REF-250, below, surfaces this to the author as a WARNING, not
    # silently). `is_o2_surface_tier` doubles as the deploy-sensor gate later.
    is_o2_surface_tier = plan.tier == "o2"
    from axrange.sensors import default_image_for, resolve_type

    sensor_types: dict[str, str] = {}
    if sensors_decl and is_o2_surface_tier:
        node_ids = {n.id for n in nodes if n.id}
        # The transport network: a declared `transport: true` net, else synthesize.
        transport_net = next((n for n in networks if n.id and n.transport), None)
        if transport_net is None:
            pre_vlan = _zone_type(networks) == "vlan"
            t_vlan = (
                (max([n.vlan for n in networks if n.vlan] or [3999]) + 1)
                if pre_vlan else None
            )
            # egress=True so the sensor can `apt-get install` its tool (the base
            # image doesn't bake tcpdump/suricata/zeek) + ship artifacts out-of-
            # band. SNAT is materialized by create-subnet — Simple-zone only (PVE
            # rejects exitnodes on VLAN zones; a VLAN-zone sensor needs a pre-baked
            # tool or a router egress, a documented follow-on).
            transport_net = Network(
                id="axr-sensor-transport", cidr="10.255.250.0/24",
                gateway="10.255.250.1", vlan=t_vlan, transport=True,
                egress=(not pre_vlan), dns=["1.1.1.1", "8.8.8.8"],
            )
            networks.append(transport_net)
        net_base = (
            (transport_net.cidr or "10.255.250.0/24").split("/")[0].rsplit(".", 1)[0]
        )
        for i, s in enumerate(sensors_decl):
            sid = s.id
            if not sid:
                raise EmissionError("sensors[] entry without id")
            if sid in node_ids:
                raise EmissionError(
                    f"sensor id {sid!r} collides with a node id — sensor ids must "
                    "be distinct from node ids (they share the VM namespace)"
                )
            ip = f"{net_base}.{10 + i}"
            sensor_types[sid] = resolve_type(s.type)
            # The synthetic sensor VM lives only in the plan (not the IR), so
            # place it here — pass4 only places `ir.layers.infra.nodes`.
            plan.placement[sid] = ctx.default_node
            nodes.append(
                Node(
                    id=sid,
                    kind="host",
                    image=s.image or default_image_for(s.type),
                    requires=["linux"],
                    nets=[NetRef(net=transport_net.id, ip=ip)],
                    resources=s.resources or Resources(cpu=2, ram="2Gi", disk="20Gi"),
                )
            )

    plan_id = plan.plan_id

    # ---- Networking: SDN-native (OP_CATALOG §5.1; SDN-native 2026-06-03) ----
    # Zone type is auto-derived (OP-NET-010): VLAN when any network declares a
    # `vlan`, else Simple. The OVS-bridge cardinality follows:
    #   - VLAN  -> ONE OVS underlay bridge per zone; networks are VLAN-vnets.
    #   - Simple -> one bridge per network (the vnet materializes the bridge).
    # create-ovs-bridge runs BEFORE create-sdn-zone so a VLAN zone can
    # reference an already-existing underlay bridge.
    zone_type = _zone_type(networks)
    is_vlan = zone_type == "vlan"
    scenario_scope = {"scenario": scenario_id}

    # create-ovs-bridge — VLAN underlay (once per zone) here; per network in the
    # loop below for Simple.
    if is_vlan:
        plan.add_op(
            _make_op(
                plan_id,
                "create-ovs-bridge",
                scenario_scope,
                inputs_resolved={"net_id": None, "zone_type": zone_type},
            )
        )

    # create-sdn-zone — after the bridge(s). VLAN zone references the underlay.
    zone_from_outputs: dict[str, tuple[str, str]] = {}
    if is_vlan:
        zone_from_outputs["bridge_name"] = (
            _op_id(plan_id, "create-ovs-bridge", scenario_scope),
            "bridge_name",
        )
    plan.add_op(
        _make_op(
            plan_id,
            "create-sdn-zone",
            scenario_scope,
            inputs_resolved={"scenario_id": scenario_id, "zone_type": zone_type},
            inputs_from_outputs=zone_from_outputs,
        )
    )
    plan.add_op(
        _make_op(
            plan_id,
            "apply-sdn-config",
            scenario_scope,
            inputs_from_outputs={
                "zone_id": (
                    _op_id(plan_id, "create-sdn-zone", scenario_scope),
                    "zone_id",
                ),
            },
        )
    )

    # mirror-ready[$sensor] is NOT emitted by the te planner any more (2026-07-13,
    # docs/WORK_ORDER_sensor_agnostic_te.md): its only predecessors
    # (attach-gre-tunnel[*]/install-ovs-mirror[*]/verify-capture[*]) are never
    # emitted here either, so a te-side instance would be permanently vacuous
    # (zero predecessors, auto-completes) — worse than not emitting it. Its
    # catalog-declared consumers (start-activity, the legacy
    # deploy-network-sensor) simply resolve that `after:` pattern to zero ops,
    # which is a documented no-op (pass3_ordering, "not an error"). The A2 tap
    # playout composes its OWN mirror-ready-independent plan (no barriers at
    # all — it operates on an already-deployed te, docs/design/06-taps-sensors.md).

    # fabric-ready[$scenario] — only when >=1 router declares fabric routing
    # (fabric slice). Gates apps/activities that need cross-fabric reachability.
    if any(_is_router(n) and getattr(n, "routing", None) is not None for n in nodes):
        plan.add_barrier(_make_barrier(plan_id, "fabric-ready", {"scenario": scenario_id}))

    # ---- Per-network ops ---------------------------------------------------
    for net in networks:
        if net.id is None:
            raise EmissionError("network entry without id")
        net_id = net.id
        net_scope = {"net": net_id}

        # Simple zone: one OVS bridge per network (the vnet materializes it).
        if not is_vlan:
            plan.add_op(
                _make_op(
                    plan_id,
                    "create-ovs-bridge",
                    net_scope,
                    inputs_resolved={"net_id": net_id, "zone_type": zone_type},
                )
            )
        plan.add_op(
            _make_op(
                plan_id,
                "create-vnet",
                net_scope,
                inputs_resolved={
                    "net_id": net_id,
                    "cidr": net.cidr,
                    "vlan": net.vlan,
                    "gateway": net.gateway,
                    "dns": list(net.dns or []),
                    "dhcp": (
                        {"enabled": net.dhcp.enabled, "range": net.dhcp.range}
                        if net.dhcp is not None
                        else None
                    ),
                },
                inputs_from_outputs={
                    "zone_id": (
                        _op_id(plan_id, "create-sdn-zone", scenario_scope),
                        "zone_id",
                    ),
                },
            )
        )

        # create-subnet[$net] — opt-in SDN subnet + gateway + SNAT for
        # internet/apt egress (router slice Phase 1, docs/SLICE_ROUTER.md).
        # Only emitted when the network sets `egress: true` and has a gateway,
        # so existing scenarios are unchanged. Ordered after create-vnet[$net]
        # (registry) and gated before apply-sdn-config (registry after[*]).
        # A `transport: true` sensor vnet ALSO needs a subnet+gateway so the PVE
        # host holds an address on it and can route the keyed gretap to the sensor
        # VM's transport IP (sensors design §6) — but WITHOUT SNAT (it carries
        # mirrored capture, not egress traffic).
        if (net.egress or net.transport) and net.gateway:
            plan.add_op(
                _make_op(
                    plan_id,
                    "create-subnet",
                    net_scope,
                    inputs_resolved={
                        "net_id": net_id,
                        "cidr": net.cidr,
                        "gateway": net.gateway,
                        "snat": bool(net.egress),
                    },
                )
            )

        # network-ready[$net] barrier
        plan.add_barrier(_make_barrier(plan_id, "network-ready", net_scope))

        # attach-gre-tunnel is NOT emitted here any more (2026-07-13,
        # docs/WORK_ORDER_sensor_agnostic_te.md) — a te wires no mirror/gretap of
        # its own. The A2 tap playout composes it directly against a standing O2
        # sensor once the te is deployed (axrange.o2control.taps._compose_ops).

    # ---- Per-host ops ------------------------------------------------------
    # The DECLARED listen set per host, joined from `layers.apps[].listen` +
    # `bindings[]` ONCE here rather than per node (docs/
    # WORK_ORDER_protocol_faithful_services.md S1, D2(b), Authority: user).
    declared_listen = _declared_listen_by_host(ir)
    # The twin's containment posture (GAP-101), resolved ONCE for the whole
    # twin and attached to EVERY router below -- the same `fabric:`-block
    # shape `build_fabric_inputs` already uses. `None` when the IR declares no
    # `egress:` at all, in which case no router gets the input and the adapter
    # keeps today's permissive masquerade (LES-213: a config that never opted
    # in must be byte-identical, which is what keeps every shipped twin
    # config's `plan_id` pinned).
    try:
        egress_policy = resolve_egress_policy(ir)
    except EgressDeclarationError as exc:
        # Re-raised as this pass's own error so every existing CLI/API
        # handler (`except EmissionError`) covers it unchanged.
        raise EmissionError(str(exc)) from exc
    egress_routers = 0
    for node in nodes:
        if node.id is None:
            raise EmissionError("node entry without id")
        host_id = node.id
        host_scope = {"host": host_id}

        # Look up the binding to source hardware + cloud_init fields.
        image_id, binding_v = _binding_for_node(node, manifests)
        target_node = ctx.default_node

        # clone-vm
        clone_inputs: dict[str, Any] = {
            "node_id": host_id,
            "target_node": target_node,
            "clone_mode": (
                binding_v.clone_mode if binding_v and binding_v.clone_mode else "linked"
            ),
            "template_vmid": (
                binding_v.storage.template_vmid
                if binding_v and binding_v.storage
                else None
            ),
            "image_id": image_id,
            # Additive (docs/WORK_ORDER_traffic_tier.md Part 1, GAP-027): the
            # twin inventory's `HostInfo.role` sources from HERE (an
            # unconditional op, fired for every cloned host regardless of
            # tap status -- LES-095) rather than a tap-gated op, so an
            # untapped twin still publishes a real persona-assignable
            # roster. `None` for a node with no `role:` declared -- never
            # fabricated (same posture as `os`/`image_id`).
            "node_role": node.role,
            # Additive (2026-07-27): `HostInfo.kind` sources from HERE so the
            # inventory can tell `tappoints` which hosts are routers -- LOAD-
            # BEARING for `perimeter` scope (every router port on a target
            # net) and `o2control/taps.py`'s `_is_router_host`/
            # `_gateway_ip_for_net` (fix round G3 split it from the
            # STRUCTURAL, leg-count-only `_is_route_ambiguous` that
            # `_select_verify_injector` uses), NOT for excluding anything from
            # `segment` (that exclusion was superseded 2026-07-27, `docs/
            # WORK_ORDER_tap_scopes.md`/`docs/design/06-taps-sensors.md` --
            # under rx-only, excluding a router transit leg from `segment`
            # is a COVERAGE HOLE, not a de-dup; do not reinstate it).
            # Unconditional like `node_role`. A deploy predating this field
            # has it absent (LES-096) -- `inventory.build_twin_inventory`
            # back-fills "router" from the presence of a `configure-router`/
            # `configure-routing` op for those. `None` for a node with no
            # `kind:` declared.
            "node_kind": node.kind,
        }
        # The DECLARED listen set (`App.listen`, wired 2026-09-05 -- D2(b),
        # Authority: user), carried here for the SAME reason `node_role` and
        # `node_kind` are: `clone-vm` is the one op emitted for EVERY host
        # regardless of apps, taps or overlays, so an inventory read off it
        # covers the whole roster (LES-095).
        #
        # **Emitted ONLY when non-empty, and that is load-bearing.** An
        # unconditional `"node_listen": None` would appear on the NEW side of
        # `plandiff._diff_fields` for every host of every standing twin,
        # whose persisted rows have no such key -- ABSENT vs None is a
        # difference, so every clone-vm would classify Mutated and
        # `te deploy --update` would hard-refuse on 100% of already-deployed
        # content (LES-185's exact failure shape, one build later). Keyed off
        # content nothing ships today, the key simply never appears and every
        # existing deploy diffs clean.
        #
        # The cost, accepted: CHANGING a declared listen set on a standing
        # twin makes that host's clone-vm Mutated, so `--update` refuses it
        # (`--reapply` covers `run-script` content only). That is the honest
        # answer -- the work order's §7.2 already calls "a brand-new protocol
        # on an existing host" a redeploy -- but it does mean the declared set
        # is a deploy-time fact, not a runtime-swappable one like `role` (see
        # OP-TFC-020's `assign-role`, which exists precisely because roles
        # needed to change without a re-clone).
        node_listen = declared_listen.get(host_id)
        if node_listen:
            clone_inputs["node_listen"] = node_listen
        plan.add_op(_make_op(plan_id, "clone-vm", host_scope, inputs_resolved=clone_inputs))
        clone_id = _op_id(plan_id, "clone-vm", host_scope)

        # configure-vm-hardware
        hw = binding_v.hardware if binding_v else None
        res = _resources_dict(node)
        hw_inputs: dict[str, Any] = {
            **res,
            "machine": hw.machine if hw else None,
            "bios": hw.bios if hw else None,
            "agent": hw.agent if hw else None,
        }
        plan.add_op(
            _make_op(
                plan_id,
                "configure-vm-hardware",
                host_scope,
                inputs_resolved=hw_inputs,
                inputs_from_outputs={"vmid": (clone_id, "vmid")},
            )
        )

        # configure-cloud-init
        ci = binding_v.cloud_init if binding_v else None
        is_router = _is_router(node)
        ip_config: dict[str, dict[str, Any]] = {}
        legs = _node_nets(node)
        # `ipconfigN` is POSITIONAL (the adapter numbers `ip_config` entries in
        # insertion order, `rest._build_ipconfig_body`), so an address-less leg
        # (an L2 bridge port, docs/WORK_ORDER_corp_twin.md §10 P0) that sits
        # BEFORE an addressed one keeps a `{"ip": None}` placeholder -- dropping
        # it would shift every later leg's address onto the wrong NIC. A
        # trailing address-less leg is still omitted, exactly as before, so no
        # shipped plan changes (none has an address-less leg ahead of an
        # addressed one).
        last_ip_idx = max((i for i, (_n, ip) in enumerate(legs) if ip is not None), default=-1)
        # A multi-homed HOST takes a default route on its FIRST addressed leg
        # whose segment has a gateway, and on no other -- two `gw=` on one guest
        # is two default routes, and which one wins is a boot-order accident
        # (the 9K pair: default via hq-mx on vlan935, never via its own VIP on
        # vlan953). Single-homed hosts and routers are unchanged.
        gateway_taken = False
        for idx, (net_id, ip) in enumerate(legs):
            if ip is None:
                if idx < last_ip_idx:
                    ip_config[net_id] = {"ip": None}
                continue
            net = _network_by_id(ir, net_id)
            gw = None
            if ip == "dhcp":
                # A DHCP leg takes its route from the lease; `gateway` here is
                # informational (`rest._build_ipconfig_body` ignores it), so it
                # keeps the segment's value and never claims the one static
                # default route a later addressed leg may need.
                if not is_router and net is not None:
                    gw = net.gateway
            elif not is_router and net is not None and net.gateway and not gateway_taken:
                gw = net.gateway
                gateway_taken = len(legs) > 1
            ip_config[net_id] = {
                "ip": ip,
                "cidr": net.cidr if net else None,
                # A router IS its segments' gateway, so its own LAN NICs take no
                # gateway (its default route comes from the WAN NIC, below).
                "gateway": gw,
            }
        if is_router:
            # WAN NIC: DHCP off the uplink bridge — gives the router its default
            # route + internet, which it masquerades for the LAN (Phase 2).
            ip_config["__wan__"] = {"ip": "dhcp", "bridge": ROUTER_WAN_BRIDGE}
        is_vyos = is_router and image_id is not None and "vyos" in str(image_id).lower()
        # VyOS (agent:false, no cloud-init in the generic ISO) is configured via
        # SSH `set` commands post-boot (vyos-ssh-set, see configure-router below).
        # Its template self-boots eth0=DHCP + SSH + the operator key, so it needs
        # no cloud-init op at all. Every other node still gets cicustom user-data.
        if not is_vyos:
            ci_inputs: dict[str, Any] = {
                "node_id": host_id,
                "hostname": host_id,
                "ip_config": ip_config,
                "ci_user": (ci.user if ci and ci.user else None),
                "sysprep_required": (ci.sysprep_required if ci else False) or False,
                # docs/WORK_ORDER_access_facts.md Part A: an explicit binding
                # `cloud_init.password` (an EXTRA field -- not part of the
                # closed IMAGE_MANIFEST §3.5 cloud_init schema; ManifestBase's
                # extra="allow" passes it through verbatim) wins over the
                # deploy-wide default the adapter falls back to. `None` when
                # unset, exactly like `ci_user` above.
                "password": (getattr(ci, "password", None) if ci else None),
                # W1.3: Windows hosts get a cloudbase-init <powershell> first-boot
                # snippet instead of Linux cloud-config (docs/SLICE_WINDOWS_AD.md §5).
                "os_family": _os_family(image_id, manifests) or "linux",
                # Linux routers enable forwarding/NAT via configure-router (QGA).
                "router_os": ("linux" if is_router else None),
            }
            plan.add_op(
                _make_op(
                    plan_id,
                    "configure-cloud-init",
                    host_scope,
                    inputs_resolved=ci_inputs,
                    inputs_from_outputs={"vmid": (clone_id, "vmid")},
                )
            )

        # attach-vm-to-vnet (one per (host, net) pair). VyOS routers put the WAN
        # on net0/eth0 — the template's baked DHCP mgmt iface that makes the
        # router SSH-reachable for vyos-ssh-set — so their LAN NICs shift to
        # net1.. (eth1..). Every other node (incl. Debian routers) keeps LANs on
        # net0.. and appends the WAN last. netN->ethN is deterministic on the
        # VyOS template (hw-id stripped), so eth(i+1) is LAN net i.
        lan_base = 1 if is_vyos else 0
        for i, (net_id, ip) in enumerate(_node_nets(node)):
            nic_index = i + lan_base
            net_scope = {"net": net_id}
            attach_scope = {"host": host_id, "net": net_id}
            plan.add_op(
                _make_op(
                    plan_id,
                    "attach-vm-to-vnet",
                    attach_scope,
                    inputs_resolved={
                        "net_id": net_id,
                        "ip": ip,
                        "nic_index": nic_index,
                        "target_node": target_node,
                        # No NIC-level VLAN tag under SDN-native: the VM attaches
                        # to the SDN vnet's bridge, which already carries the tag
                        # (VLAN zone). Tagging the NIC too would double-tag.
                        "vlan": None,
                    },
                    inputs_from_outputs={
                        "vmid": (clone_id, "vmid"),
                        "vnet_id": (
                            _op_id(plan_id, "create-vnet", net_scope),
                            "vnet_id",
                        ),
                    },
                )
            )

        # Router WAN NIC: attach to the uplink bridge at the next NIC index
        # (router slice Phase 2). Not an SDN vnet — bridges straight to vmbr0.
        if _is_router(node):
            wan_index = _wan_nic_index(node, is_vyos)
            plan.add_op(
                _make_op(
                    plan_id,
                    "attach-vm-to-vnet",
                    {"host": host_id, "net": "__wan__"},
                    inputs_resolved={
                        "net_id": "__wan__",
                        "ip": None,
                        "nic_index": wan_index,
                        "vnet_id": ROUTER_WAN_BRIDGE,
                        "target_node": target_node,
                        "vlan": None,
                    },
                    inputs_from_outputs={"vmid": (clone_id, "vmid")},
                )
            )

        # record-tap-intent emission REMOVED here (`docs/
        # WORK_ORDER_tap_eligibility.md`, Authority: user, 2026-07-28) -- a
        # te deploy no longer resolves or records ANY tap-related state.
        # `perimeter: wan` no longer needs a WAN-eligibility special case
        # here either (the M3 unblock this replaced): the router's WAN leg
        # is already recorded, unconditionally, by the `attach-vm-to-vnet`
        # emission above (`net_id: "__wan__"`) -- the A2 tap playout's
        # `axrange.planner.tappoints` resolves `perimeter`/`perimeter: wan`
        # straight from that published topology, no separate tap-intent
        # record needed.

        # start-vm
        plan.add_op(
            _make_op(
                plan_id,
                "start-vm",
                host_scope,
                inputs_from_outputs={"vmid": (clone_id, "vmid")},
            )
        )

        # wait-guest-agent + wait-os-ready — only when the image runs the guest
        # agent (binding hardware.agent). VyOS (agent:false) has no QGA, so these
        # are skipped (its config is applied at boot via cloud-init).
        agent_enabled = not (hw is not None and hw.agent is False)
        if agent_enabled:
            plan.add_op(
                _make_op(
                    plan_id,
                    "wait-guest-agent",
                    host_scope,
                    inputs_from_outputs={"vmid": (clone_id, "vmid")},
                )
            )
            first_boot_budget = (
                None
                if binding_v is None or manifests is None
                else _first_boot_budget(node, manifests, image_id)
            )
            nets_attached = _node_nets(node)
            mgmt_ip = nets_attached[0][1] if nets_attached else None
            os_family = _os_family(image_id, manifests) or "linux"
            osready_inputs: dict[str, Any] = {
                "mgmt_ip": mgmt_ip,
                "os_family": os_family,
                "first_boot_budget": first_boot_budget,
            }
            # Windows has no DHCP on isolated scenario SDN nets and the template
            # ships no cloudbase-init to consume PVE's NoCloud ipconfigN — so the
            # adapter pins the static IP over QGA once the guest is specialized
            # (docs/SLICE_WINDOWS_AD.md §5). Thread the mgmt net's ip/prefix/gw.
            if os_family == "windows":
                # Rename the clone to the node id (the first-boot path doesn't —
                # no cloudbase-init), so an AD join lands under the friendly name.
                osready_inputs["hostname"] = host_id
                if mgmt_ip and mgmt_ip != "dhcp":
                    mgmt_net_id = nets_attached[0][0]
                    mgmt_net = _network_by_id(ir, mgmt_net_id)
                    prefix = 24
                    if mgmt_net and mgmt_net.cidr and "/" in str(mgmt_net.cidr):
                        prefix = int(str(mgmt_net.cidr).split("/", 1)[1])
                    osready_inputs["static_ip"] = mgmt_ip
                    osready_inputs["static_prefix"] = prefix
                    if mgmt_net and mgmt_net.gateway:
                        osready_inputs["static_gateway"] = mgmt_net.gateway
            plan.add_op(
                _make_op(
                    plan_id,
                    "wait-os-ready",
                    host_scope,
                    inputs_resolved=osready_inputs,
                    inputs_from_outputs={"vmid": (clone_id, "vmid")},
                )
            )

        # host-ready barrier
        plan.add_barrier(_make_barrier(plan_id, "host-ready", host_scope))

        # configure-router (router slice Phase 2): once the router VM is up, bring
        # up forwarding + masquerade LAN->WAN so its LAN segments get internet/apt
        # egress + inter-segment routing. Two delivery paths by router OS:
        #   - Debian/nftables over the QGA channel (router_os=linux).
        #   - VyOS (agent:false, no QGA): SSH `set` commands via vbash
        #     script-template (vyos-ssh-set). The adapter discovers the router's
        #     WAN/mgmt IP (eth0 DHCP) via arp-scan on the host, then SSHes in with
        #     the operator key (baked into the template) and applies LAN interface
        #     addresses + source-NAT + static routes. eth(i+1) is LAN net i.
        if _is_router(node):
            cr_inputs: dict[str, Any] = {"node_id": host_id}
            # Hosts that route their egress through this router (they share one of
            # its LAN nets). Their provision-app/run-script (apt/pip over the
            # router's NAT) must not run until configure-router has brought up
            # forwarding + masquerade — pass-3 adds those ordering edges. (Fixes
            # the apt-before-NAT race, CLAUDE.md Next Actions #4.)
            #
            # PLANNER-INTERNAL, not a real op input (docs/
            # WORK_ORDER_twin_incremental_update.md M1): this is a PLAN-WIDE
            # roster -- "every non-router node sharing one of my LAN nets" --
            # that changes every time ANY host anywhere on this router's nets
            # is added/removed, even though `configure-router`'s own
            # cluster-side behavior never actually changes. pass3_ordering.py
            # is the SOLE consumer (an ordering-edge computation at plan-BUILD
            # time); no adapter handler ever reads it (`op_configure_router`
            # only touches `inp["vmid"]`/`inp.get("router_os")`/the VyOS
            # fields). Carried via `planner_hints` (never persisted into
            # `inputs_resolved_json`, `OpInstance.planner_hints`'s docstring)
            # so `plandiff.diff_plan` never sees it as a false Mutated field
            # on an unrelated add/remove -- it used to live in `cr_inputs`
            # ("served_hosts"), which made EVERY incremental update against a
            # router-bearing scenario hard-refuse (measured: 100% of shipped
            # twin-configs/archetypes).
            # CLOSED through forwarders (docs/WORK_ORDER_corp_twin.md §10 P0): a
            # host behind an L2 bridge VM or an L3 `kind: host` forwarder (the
            # 9K pair) reaches this router's NAT through them, so it has the
            # same apt-before-NAT race as a host on the router's own leg.
            # Identical to the direct roster when a plan has no forwarder.
            _router_nets = _nets_reached_through_forwarders(
                {nid for nid, _ip in _node_nets(node)}, nodes,
            )
            served_hosts = [
                n.id for n in nodes
                if n.id and n.id != host_id and not _is_router(n)
                and _router_nets.intersection({nid for nid, _ in _node_nets(n)})
            ]
            if is_vyos:
                lan_ifaces: list[dict[str, str]] = []
                for i, (net_id, ip) in enumerate(_node_nets(node)):
                    if ip is None:
                        continue
                    net = _network_by_id(ir, net_id)
                    # Fabric VTEP leaf: a `vni` (L2VNI) leg is bridged by
                    # configure-routing (VXLAN), so it must NOT get an L3 address
                    # here — an iface can't be both L3-addressed and a bridge member.
                    if (
                        node.routing is not None
                        and net is not None
                        and getattr(net, "vni", None) is not None
                    ):
                        continue
                    prefix = "24"
                    if net and net.cidr and "/" in str(net.cidr):
                        prefix = str(net.cidr).split("/", 1)[1]
                    lan_ifaces.append(
                        {"eth": f"eth{i + 1}", "address": f"{ip}/{prefix}"}
                    )
                static_routes = [
                    {"to": r.to, "via": r.via}
                    for r in (node.static_routes or [])
                    if r.to and r.via
                ]
                cr_inputs.update(
                    {
                        "router_os": "vyos",
                        "wan_eth": "eth0",
                        "wan_bridge": ROUTER_WAN_BRIDGE,
                        "lan_ifaces": lan_ifaces,
                        "nameservers": ["8.8.8.8", "1.1.1.1"],
                        "static_routes": static_routes,
                    }
                )
            # The twin's containment posture (GAP-101) -- attached to EVERY
            # router, because the declaration is twin-level: one router left
            # permissive re-opens the real internet for everything behind it,
            # which is the breach GAP-101 records. The key is emitted ONLY
            # when the twin declared one (LES-224: an unconditional new key
            # retro-classifies every standing deploy's op as Mutated, and
            # every shipped twin config's `plan_id` is pinned).
            if egress_policy is not None:
                if is_vyos:
                    # Refuse at PLAN time what the adapter would refuse
                    # mid-deploy (`rest._configure_vyos_router_ssh`): VyOS's
                    # firewall syntax differs across 1.3/1.4, so there is no
                    # vbash branch for the containment half. Silently skipping
                    # it would leave the twin believing it is closed while this
                    # router forwards to the real WAN (LES-001).
                    raise EmissionError(
                        f"node {host_id!r}: the twin declares an `egress:` policy "
                        "but this router is VyOS, for which the egress phase "
                        "policy is not implemented (docs/WORK_ORDER_protocol_"
                        "engines.md §E1 — the Debian/nftables branch is the "
                        "shipped shape). Use a Debian router on a twin that "
                        "measures, or drop the `egress:` declaration."
                    )
                cr_inputs["egress"] = dict(egress_policy)
                egress_routers += 1
            plan.add_op(
                _make_op(
                    plan_id,
                    "configure-router",
                    host_scope,
                    inputs_resolved=cr_inputs,
                    inputs_from_outputs={"vmid": (clone_id, "vmid")},
                    planner_hints={"served_hosts": served_hosts},
                )
            )

            # configure-routing (fabric slice): dynamic routing + overlay on this
            # router from its `routing:` intent. Gated after configure-router (base
            # interfaces + WAN/NAT up); reuses configure-router's discovered wan_ip
            # (no second arp-scan). VyOS-only.
            if node.routing is not None:
                from axrange.planner.fabric import build_fabric_inputs

                fabric_ctx = build_fabric_inputs(node, nodes, networks, ir.fabric)
                cr_op_id = _op_id(plan_id, "configure-router", host_scope)
                plan.add_op(
                    _make_op(
                        plan_id,
                        "configure-routing",
                        host_scope,
                        inputs_resolved={
                            "node_id": host_id,
                            "router_os": "vyos",
                            "fabric": fabric_ctx,
                        },
                        inputs_from_outputs={
                            "vmid": (clone_id, "vmid"),
                            "wan_ip": (cr_op_id, "wan_ip"),
                            "wan_gw": (cr_op_id, "wan_gw"),  # tenant-VRF egress leak
                        },
                    )
                )

    # A declared posture that reached no router is a FALSE AFFORDANCE
    # (LES-172): the twin config says "contained", nothing enforces it, and
    # the deploy succeeds. Refuse at plan time instead -- containment that
    # depends on an op nobody emitted is exactly GAP-101's failure mode.
    if egress_policy is not None and egress_routers == 0:
        raise EmissionError(
            "the twin declares an `egress:` policy but has no `kind: router` "
            "node to apply it to — the phase is enforced ON THE ROUTER "
            "(docs/WORK_ORDER_protocol_engines.md §E1), so a routerless twin "
            "cannot be contained by it. Add a router, or drop the declaration."
        )

    # ---- deploy-sensor (sensors design Phase B; OP-MIR-050) ----------------
    # For each declared sensor VM (synthesized into `nodes` above, so its host
    # lifecycle is already emitted), provision the typed tool + the in-guest
    # flow-based gretap decap over QGA, after the sensor's host-ready barrier.
    # Tier-gated in lockstep with the synthesis block above (review fix F1) --
    # `is_o2_surface_tier` is False on every `tier="te"` deploy, so this loop
    # is a no-op there (and `sensors_decl` never got a corresponding clone-vm
    # emitted either, so a dangling inputs_from_outputs reference is not
    # possible even if this guard were ever removed by mistake).
    for s in (sensors_decl if is_o2_surface_tier else []):
        sid = s.id
        if not sid:
            continue
        clone_id = _op_id(plan_id, "clone-vm", {"host": sid})
        config_content = None
        ship = None
        if isinstance(s.export, dict):
            config_content = s.export.get("rules") or s.export.get("config")
            ship = s.export.get("ship")
        # A first-class `rules:` file (IDS ruleset for a suricata-/zeek-sensor) is
        # read at emit time and takes precedence over inline export rules — the
        # in-scenario IDS blue-team content (docs/SLICE_OVERLAY.md defense).
        if s.rules:
            config_content, _ = _read_script(scenario_dir, s.rules, f"sensor:{sid}")
        plan.add_op(
            _make_op(
                plan_id,
                "deploy-sensor",
                {"host": sid},
                inputs_resolved={
                    "sensor_id": sid,
                    "sensor_type": sensor_types.get(sid, resolve_type(s.type)),
                    "capture_iface": "gre-cap",
                    "config_content": config_content,
                    "ship": ship,
                },
                inputs_from_outputs={"vmid": (clone_id, "vmid")},
            )
        )

    # verify-capture is NOT emitted here any more (2026-07-13,
    # docs/WORK_ORDER_sensor_agnostic_te.md) — a te has no gretap/mirror of its
    # own to heartbeat. The A2 tap playout emits its OWN verify-capture (against
    # the standing O2 sensor it just bound) once the te is deployed.

    # ---- Per-app ops (config-class; docs/SLICE_CONFIG.md) ------------------
    _emit_apps(plan, ir, manifests, nodes, scenario_dir)

    # `layers.activity:` is NOT emitted here any more (2026-07-24, the Exercise-
    # tier scope-shift, docs/WORK_ORDER_exercise_tier.md,
    # docs/design/02-twin-model.md): a te deploy stands up only the
    # environment -- attack playout (the OP-ACT inline-steps trio this used to
    # emit) is re-homed to the standalone, replayable Exercise (`exr`) tier
    # playout (`axrange.o2control.exercise`), which composes the SAME
    # deploy-behavior-agent/start-activity/wait-actor-ready ops by hand against
    # an already-deployed twin (mirrors the sensor-agnostic-te precedent: the
    # ops stay in the catalog, only the te-side EMISSION trigger moves).
    # `layers.activity:` left on a scenario's IR (the shipped archetypes still
    # author it) validates with a WARNING (REF-260), not an error.

    # ---- Per-overlay ops (OP-OVL; docs/SLICE_OVERLAY.md) -------------------
    # `primary_sensor_id` passed UNCONDITIONALLY (`docs/
    # WORK_ORDER_tap_eligibility.md`, Authority: user, 2026-07-28) -- it used
    # to be gated on `any_tapped` (now-removed tap resolution); `ClusterContext.
    # sensor_id` defaults to the string `"default"`, so this was never actually
    # `None` on the positive path, and the negative path ("no tapped hosts") is
    # no longer a thing a te-emission-time check can even ask -- capture
    # eligibility is entirely the tap tier's business now. See `_emit_overlays`'s
    # own docstring/signature for the resulting simplification.
    _emit_overlays(plan, ir, nodes, manifests, scenario_dir, primary_sensor_id)


def _emit_apps(
    plan: Plan,
    ir: IR,
    manifests: ManifestRegistry,
    nodes: list[Node],
    scenario_dir: str | Path | None,
) -> None:
    """Per app: emit provision-app for its pkg/pkg@version actions and run-script
    for a script: action, both scoped {app,host} (so the catalog's
    `after host-ready[$host]` / `provision-app[$app@*]` patterns resolve), with
    vmid sourced from the host's clone-vm (docs/OP_CATALOG.md OP-APP-010/030,
    docs/SLICE_CONFIG.md)."""
    apps = list(ir.layers.apps or []) if ir.layers else []
    if not apps:
        return
    plan_id = plan.plan_id
    bindings = list(ir.bindings or [])
    node_by_id = {n.id: n for n in nodes}

    for app in apps:
        app_id = app.id
        if app_id is None:
            raise EmissionError("app entry without id")
        app_hosts = [b.host for b in bindings if b.app == app_id and b.host]
        if not app_hosts:
            raise EmissionError(
                f"app {app_id!r} has no app->host binding (add a bindings[] entry)"
            )
        # A multi-bound app: this loop takes the FIRST binding and has always
        # silently dropped the rest, so a second `{app: X, host: Y}` entry
        # provisions nothing on Y while looking like it does. That is a real,
        # PRE-EXISTING defect (LES-001's family) and it is LIVE IN SHIPPED
        # CONTENT -- `target-environments/enterprise-multisite/scenario.yaml`
        # binds `db-engine` to db-prod+db-dw, `etl-runner` to etl01+etl02 and
        # `branch-file` to br1-fs+br2-fs, each with an author comment saying
        # the two-host binding is deliberate; `db-dw`, `etl02` and `br2-fs`
        # have therefore never actually been provisioned. Fixing THAT (fan the
        # app out over every binding, or split the content into one app per
        # host) changes the op set of a shipped scenario, so it is its own
        # decision -- recorded, not taken here.
        #
        # What is refused here is only the case `App.listen` newly makes
        # WRONG rather than merely incomplete (review S4): the declared listen
        # set is joined over EVERY binding (`_declared_listen_by_host`), so a
        # multi-bound app that declares ports would publish them for a host
        # that installs nothing -- the twin's published inventory claiming a
        # service it was never asked to stand up, which the scorecard then
        # reports against the observed set. Narrow on purpose: it closes the
        # hole this build opens without silently re-shaping content this build
        # is not about (LES-185 -- and this refusal was FOUND by shipped
        # content, not by a fixture).
        if len(app_hosts) > 1 and app.listen:
            raise EmissionError(
                f"app {app_id!r} declares listen: and is bound to "
                f"{len(app_hosts)} hosts "
                f"({', '.join(sorted(str(h) for h in app_hosts))}) -- only the "
                "FIRST binding is ever provisioned, so the declared ports "
                "would be published for hosts that install nothing; declare "
                "one app entry per host"
            )
        host_id = app_hosts[0]
        node = node_by_id.get(host_id)
        if node is None:
            raise EmissionError(
                f"app {app_id!r} bound to unknown host {host_id!r}"
            )
        image_id, _ = _binding_for_node(node, manifests)
        clone_id = _op_id(plan_id, "clone-vm", {"host": host_id})
        app_scope = {"app": app_id, "host": host_id}

        install_actions, script_paths = _classify_install(app_id, app.install or [])
        os_family = _os_family(image_id, manifests) or "linux"

        # W2a: a `role: adds` action makes this app's host an AD domain controller.
        # The forest FQDN comes from the app's provides:[{domain}], threaded into
        # provision-app and the wait-domain-ready / domain-ready it triggers.
        role_adds = any(
            isinstance(a, dict) and a.get("role") == "adds" for a in install_actions
        )
        domain_fqdn = _provides_domain(app) if role_adds else None
        if role_adds and not domain_fqdn:
            raise EmissionError(
                f"app {app_id!r} has `role: adds` but declares no domain in provides: "
                "— add provides: [{domain: <fqdn>}] (the AD forest FQDN); "
                "docs/SLICE_WINDOWS_AD.md §6.1"
            )

        if install_actions:
            prov_inputs: dict[str, Any] = {
                "app_id": app_id,
                "host_id": host_id,
                "install_actions": install_actions,
                "env": dict(app.env or {}),
                "package_manager": _package_manager(image_id, manifests),
                # W1.4: Windows hosts install via choco/PowerShell, not Ansible.
                "os_family": os_family,
            }
            if domain_fqdn:
                prov_inputs["domain_fqdn"] = domain_fqdn
            plan.add_op(
                _make_op(
                    plan_id,
                    "provision-app",
                    app_scope,
                    inputs_resolved=prov_inputs,
                    inputs_from_outputs={"vmid": (clone_id, "vmid")},
                )
            )
            if role_adds:
                # wait-domain-ready polls the DC over QGA after the promotion; it
                # carries dc_app so its `after provision-app[$dc_app@*]` resolves,
                # and domain so the domain-ready[$fqdn] barrier (which it feeds)
                # resolves (docs/SLICE_WINDOWS_AD.md §6.1).
                plan.add_op(
                    _make_op(
                        plan_id,
                        "wait-domain-ready",
                        {"domain": domain_fqdn, "dc_app": app_id},
                        inputs_resolved={
                            "domain_fqdn": domain_fqdn,
                            "dc_host_id": host_id,
                            "os_family": os_family,
                        },
                        inputs_from_outputs={"dc_vmid": (clone_id, "vmid")},
                    )
                )
                plan.add_barrier(
                    _make_barrier(plan_id, "domain-ready", {"domain": domain_fqdn})
                )

        if script_paths:
            if len(script_paths) > 1:
                raise EmissionError(
                    f"app {app_id!r}: {len(script_paths)} script actions; the config "
                    "slice supports one script: per app (multiple is a fast-follow)"
                )
            content, name = _read_script(scenario_dir, script_paths[0], app_id)
            plan.add_op(
                _make_op(
                    plan_id,
                    "run-script",
                    app_scope,
                    inputs_resolved={
                        "app_id": app_id,
                        "host_id": host_id,
                        "script_name": f"{app_id}-{name}",
                        "script_content": content,
                        # W1.4: Windows scripts run via PowerShell (-File), not a shebang.
                        "os_family": _os_family(image_id, manifests) or "linux",
                    },
                    inputs_from_outputs={"vmid": (clone_id, "vmid")},
                )
            )



def _emit_overlays(
    plan: Plan,
    ir: IR,
    nodes: list[Node],
    manifests: ManifestRegistry,
    scenario_dir: str | Path | None,
    capture_sensor_id: str,
) -> None:
    """Per overlay: emit the OP-OVL materialization ops (docs/SLICE_OVERLAY.md).
    Supported: `overlays.data` (seed-dataset), `overlays.defense` *host agents*
    (install-defense-agent, `installed_on:`) and *network sensors*
    (deploy-network-sensor, `observes:` — network-sensor Suricata, Option C).
    Identity is a follow-on. Overlays configure existing nodes / standing infra;
    `capture_sensor_id` is the scenario's primary sensor id (`ClusterContext.
    sensor_id`, default `"default"`, or `sensors:`'s resolved primary) --
    ALWAYS a real string now (`docs/WORK_ORDER_tap_eligibility.md`,
    Authority: user, 2026-07-28: no longer gated on `any_tapped`, a
    now-removed te-emission-time concept -- see the caller). Used only as a
    scope/op_id label for `deploy-network-sensor`; never adapter.conf-sourced."""
    plan_id = plan.plan_id
    node_ids = {n.id for n in nodes}
    node_by_id = {n.id: n for n in nodes}

    # Host -> domain joins (explicit `node.joins:`, W2 §6.3 — supersedes the
    # ratified I1=infer). Driven by node fields, so emitted regardless of an
    # overlays block: per joining Windows node, join-host-to-domain gated on
    # domain-ready[$domain] + host-ready[$host], threading the domain's DC IP
    # (the host points its DNS there to locate the domain).
    for node in nodes:
        domain = node.joins
        if not domain:
            continue
        host_id = node.id
        image_id, _ = _binding_for_node(node, manifests)
        os_family = _os_family(image_id, manifests) or "linux"
        if os_family != "windows":
            raise EmissionError(
                f"node {host_id!r} has joins: {domain!r} but is os_family={os_family!r}"
                " — domain join is Windows-only (Linux/realmd join is a follow-on);"
                " docs/SLICE_WINDOWS_AD.md §6.3"
            )
        dc_host = _dc_host_for_domain(ir, domain)
        if dc_host is None:
            raise EmissionError(
                f"node {host_id!r} joins: {domain!r} but no domain controller for that"
                f" domain in the scenario — add an app with role: adds +"
                f" provides:[{{domain: {domain}}}] bound to a host (§6.1)"
            )
        dc_node = node_by_id.get(dc_host)
        dc_ip = next((ip for _net, ip in _node_nets(dc_node) if ip), None) if dc_node else None
        if not dc_ip:
            raise EmissionError(
                f"node {host_id!r} joins: {domain!r}: the DC {dc_host!r} needs a static"
                " IP (the joining host uses it as DNS to locate the domain)"
            )
        clone_id = _op_id(plan_id, "clone-vm", {"host": host_id})
        plan.add_op(
            _make_op(
                plan_id,
                "join-host-to-domain",
                {"host": host_id, "domain": domain},
                inputs_resolved={
                    "host_id": host_id,
                    "domain_fqdn": domain,
                    "dc_ip": dc_ip,
                    "os_family": "windows",
                },
                inputs_from_outputs={"vmid": (clone_id, "vmid")},
            )
        )

    # ---- Default-agent set: baked-in defense + exporter on every TE host ---
    # node (A3 Stage A, docs/design/08-exercise-measurement.md). ON BY
    # DEFAULT (user decision, 2026-07-11): fires unless `scenario.
    # default_agents: false` (the scenario-level escape hatch) or the node
    # opts out (`node.default_agents_off`). Independent of any hand-authored
    # overlays.defense entry -- both can coexist on one host UNLESS that
    # entry already covers the SAME tool kind on this host (dedup, mandatory
    # fix 2026-07-11 -- two install-defense-agent ops would both write
    # /var/ossec/etc/ossec.conf), in which case the default is skipped for
    # that kind only. Iterates the DECLARED IR nodes only (ir.layers.infra.
    # nodes), not the `nodes` param -- that also carries synthesized sensor/
    # router-injected nodes which are not default-agent targets in Stage A.
    default_agents_flag = ir.scenario.default_agents if ir.scenario is not None else None
    if default_agents_flag is not False and ir.layers is not None:
        explicit_wazuh_hosts = hosts_with_explicit_defense_tool(ir, DEFAULT_DEFENSE_TOOL)
        explicit_exporter_hosts = hosts_with_explicit_defense_tool(ir, *EXPORTER_TOOL_ALIASES)
        for node in ir.layers.infra.nodes:
            host_id = node.id
            if host_id is None:
                continue
            if str(node.kind or "host").lower().split(".")[-1] != "host":
                continue  # routers/switches are not a default-agent target
            image_id, _ = _binding_for_node(node, manifests)
            os_family = _os_family(image_id, manifests) or "linux"
            clone_id = _op_id(plan_id, "clone-vm", {"host": host_id})
            for kind in resolve_default_kinds(node):
                if kind == "defense":
                    if host_id in explicit_wazuh_hosts:
                        continue  # already covered by a hand-authored entry
                    d_entry = default_defense_entry(host_id)
                    # If host_id is a domain controller (role:adds), scope with
                    # `domain` so the op's after=domain-ready[$domain] edge
                    # resolves -- host-side AD auditing must run post-promotion
                    # (the promotion reboot wipes it otherwise). Mirrors the
                    # hand-authored overlays.defense installed_on: path below.
                    dc_domain = _domain_provided_by_host(ir, host_id)
                    d_scope = {"defense": d_entry["id"], "host": host_id}
                    if dc_domain:
                        d_scope["domain"] = dc_domain
                    plan.add_op(
                        _make_op(
                            plan_id,
                            "install-defense-agent",
                            d_scope,
                            inputs_resolved={
                                "defense_id": d_entry["id"],
                                "host_id": host_id,
                                "defense_entry": d_entry,
                                "config_content": None,
                                "os_family": os_family,
                            },
                            inputs_from_outputs={"vmid": (clone_id, "vmid")},
                        )
                    )
                elif kind == "exporter":
                    if host_id in explicit_exporter_hosts:
                        continue  # already covered by a hand-authored entry
                    e_entry = default_exporter_entry(host_id)
                    plan.add_op(
                        _make_op(
                            plan_id,
                            "install-agent",
                            {"agent": e_entry["id"], "host": host_id, "kind": "exporter"},
                            inputs_resolved={
                                "agent_id": e_entry["id"],
                                "agent_kind": "exporter",
                                "host_id": host_id,
                                "agent_entry": e_entry,
                                "os_family": os_family,
                            },
                            inputs_from_outputs={"vmid": (clone_id, "vmid")},
                        )
                    )

    overlays = ir.overlays
    if overlays is None:
        return
    scenario_seed = ir.scenario.seed if ir.scenario else None

    for entry in list(overlays.data or []):
        did = entry.id
        if did is None:
            raise EmissionError("overlays.data entry without id")
        host_id = entry.host
        if host_id is None:
            raise EmissionError(
                f"data overlay {did!r} has no host (overlays configure existing "
                "nodes; set host: to a defined node)"
            )
        if host_id not in node_ids:
            raise EmissionError(
                f"data overlay {did!r} targets unknown host {host_id!r}"
            )
        if not entry.path:
            raise EmissionError(f"data overlay {did!r} has no path")

        clone_id = _op_id(plan_id, "clone-vm", {"host": host_id})
        plan.add_op(
            _make_op(
                plan_id,
                "seed-dataset",
                {"data": did, "host": host_id},
                inputs_resolved={
                    "data_id": did,
                    "host_id": host_id,
                    "data_entry": entry.model_dump(),
                    "seed": scenario_seed,
                },
                inputs_from_outputs={"vmid": (clone_id, "vmid")},
            )
        )

    # ---- Defense overlay: host agents + network sensors --------------------
    for entry in list(overlays.defense or []):
        did = entry.id
        if did is None:
            raise EmissionError("overlays.defense entry without id")
        hosts = list(entry.installed_on or [])
        if not entry.observes and not hosts:
            raise EmissionError(
                f"defense overlay {did!r}: needs `installed_on:` (a host agent) or "
                "`observes:` (a network sensor)"
            )
        # config file (optional) read at emit time, like run-script's script:.
        # For a host agent it's the agent config; for a sensor it's the ruleset.
        config_content = None
        if entry.config:
            config_content, _ = _read_script(scenario_dir, entry.config, did)

        # Network sensor (observes:) -> deploy-network-sensor on the network sensor
        # (Option C, docs/SLICE_OVERLAY.md §4b). It reads a mirror of the sensor's
        # aggregated feed, so it gates on mirror-ready[$sensor] and needs capture.
        # The network sensor (vmid/iface) resolves at deploy from adapter config, so the
        # plan stays network-sensor-agnostic.
        #
        # The former "no tapped hosts feed a sensor in this scenario" guard is
        # REMOVED here (`docs/WORK_ORDER_tap_eligibility.md`, Authority: user,
        # 2026-07-28): `capture_sensor_id` is always a real string now (a te
        # deploy has no concept of "tapped hosts" left to check against --
        # capture is entirely the tap tier's business, resolved post-deploy).
        if entry.observes:
            plan.add_op(
                _make_op(
                    plan_id,
                    "deploy-network-sensor",
                    {"defense": did, "sensor": capture_sensor_id},
                    inputs_resolved={
                        "defense_id": did,
                        "tool": entry.tool,
                        "defense_entry": entry.model_dump(),
                        "config_content": config_content,
                    },
                )
            )

        # Host agent(s) (installed_on:) -> install-defense-agent per host.
        for host_id in hosts:
            if host_id not in node_ids:
                raise EmissionError(
                    f"defense overlay {did!r} installed_on unknown host {host_id!r}"
                )
            node = node_by_id[host_id]
            image_id, _ = _binding_for_node(node, manifests)
            os_family = _os_family(image_id, manifests) or "linux"
            # If the host is a domain controller (runs a role:adds app providing a
            # domain), scope the op with `domain` so its registry `after`
            # domain-ready[$domain] resolves — host-side AD auditing must be applied
            # post-promotion (Windows AD). Non-DC hosts carry no domain (the pattern
            # drops). docs/SLICE_WINDOWS_AD.md (host-side detection).
            dc_domain = _domain_provided_by_host(ir, host_id)
            scope = {"defense": did, "host": host_id}
            if dc_domain:
                scope["domain"] = dc_domain
            clone_id = _op_id(plan_id, "clone-vm", {"host": host_id})
            plan.add_op(
                _make_op(
                    plan_id,
                    "install-defense-agent",
                    scope,
                    inputs_resolved={
                        "defense_id": did,
                        "host_id": host_id,
                        "defense_entry": entry.model_dump(),
                        "config_content": config_content,
                        "os_family": os_family,
                    },
                    inputs_from_outputs={"vmid": (clone_id, "vmid")},
                )
            )

    # `overlays.adversary:` (Sandcat/Caldera agent plant) is NOT emitted here any
    # more (2026-07-24, the Exercise-tier scope-shift, docs/
    # WORK_ORDER_exercise_tier.md, docs/design/02-twin-model.md):
    # attack/adversary is re-homed to the standalone, replayable Exercise
    # (`exr`) tier playout (`axrange.o2control.exercise`), which composes the
    # SAME `install-agent` (agent_kind=adversary, OP-OVL-070) op by hand
    # against an already-deployed twin -- mirrors the sensor-agnostic-te
    # precedent (the op stays in the catalog; only the te-side EMISSION
    # trigger moves). The default-agent-set sweep above (agent_kind=exporter)
    # is UNAFFECTED -- the Prometheus exporter is baseline instrumentation,
    # not attack, and stays a te-side default. `overlays.adversary:` left on a
    # scenario's IR (the shipped archetypes still author it) validates with a
    # WARNING (REF-260), not an error.

    # ---- Identity overlay: domain + local users (W2b, docs/SLICE_WINDOWS_AD.md §6.2/§6.3) ----
    # One create-domain-user per non-external identity. A `domain:` identity runs
    # New-ADUser on that domain's DC (the role:adds host) over QGA after
    # domain-ready[$domain] (W2a). A no-`domain:` identity is a *local* user, bound
    # to an explicit `host:` (parallels node.joins, §6.3): New-LocalUser on Windows
    # / useradd on Linux, after host-ready[$host]. `external:` identities are op-less
    # (decision I2). join-host-to-domain is emitted from node.joins above.
    for entry in list(overlays.identity or []):
        iid = entry.id
        if iid is None:
            raise EmissionError("overlays.identity entry without id")
        if entry.external:
            continue  # exists outside the range's identity system (decision I2)
        domain = entry.domain
        if domain:
            # ---- domain user: created on the domain's DC after domain-ready ----
            dc_host = _dc_host_for_domain(ir, domain)
            if dc_host is None:
                raise EmissionError(
                    f"identity {iid!r} domain {domain!r} has no domain controller in the "
                    f"scenario — add an app with `role: adds` + provides:[{{domain: {domain}}}]"
                    " bound to a host (docs/SLICE_WINDOWS_AD.md §6.1)"
                )
            if dc_host not in node_ids:
                raise EmissionError(
                    f"identity {iid!r}: DC host {dc_host!r} for domain {domain!r} is not a node"
                )
            clone_id = _op_id(plan_id, "clone-vm", {"host": dc_host})
            plan.add_op(
                _make_op(
                    plan_id,
                    "create-domain-user",
                    {"identity": iid, "host": dc_host, "domain": domain},
                    inputs_resolved={
                        "identity_id": iid,
                        "domain_fqdn": domain,
                        "target_host_id": dc_host,
                        "groups": list(entry.groups or []),
                        "privileges": list(entry.privileges or []),
                        # Vulnerable-AD / SPN-OU modeling (§9) — make the account
                        # an exploitable target. None when unset (lab-default pw).
                        "password": entry.password,
                        "spn": entry.spn,
                        "ou": entry.ou,
                        "asrep_roastable": bool(entry.asrep_roastable),
                        # Dangerous-ACL / delegation weakening (BloodHound edges).
                        "acl": [dict(g) for g in (entry.acl or [])],
                        "delegation": entry.delegation,
                        "delegation_to": list(entry.delegation_to or []),
                        # the DC is always Windows; create-domain-user runs New-ADUser.
                        "os_family": "windows",
                    },
                    inputs_from_outputs={"target_vmid": (clone_id, "vmid")},
                )
            )
        else:
            # ---- local user: created on an explicit host: after host-ready ----
            host = entry.host
            if not host:
                raise EmissionError(
                    f"identity {iid!r} has no domain: and no host: — a local user needs "
                    "an explicit host: naming the node it is created on "
                    "(docs/SLICE_WINDOWS_AD.md §6.2/§6.3)"
                )
            if host not in node_ids:
                raise EmissionError(
                    f"identity {iid!r}: host {host!r} is not a node"
                )
            node = node_by_id[host]
            image_id, _ = _binding_for_node(node, manifests)
            os_family = _os_family(image_id, manifests) or "linux"
            clone_id = _op_id(plan_id, "clone-vm", {"host": host})
            plan.add_op(
                _make_op(
                    plan_id,
                    "create-domain-user",
                    {"identity": iid, "host": host},
                    inputs_resolved={
                        "identity_id": iid,
                        # no domain_fqdn -> the backend takes the local-user path
                        "target_host_id": host,
                        "groups": list(entry.groups or []),
                        "privileges": list(entry.privileges or []),
                        "os_family": os_family,
                    },
                    inputs_from_outputs={"target_vmid": (clone_id, "vmid")},
                )
            )


def _declared_listen_by_host(ir: IR) -> dict[str, list[dict[str, Any]]]:
    """`host_id -> [{port, proto}, ...]` -- the twin's DECLARED listen set,
    joined from `layers.apps[].listen` through `bindings[]`
    (`docs/WORK_ORDER_protocol_faithful_services.md` S1; `App.listen`'s first
    consumer, D2(b), Authority: user).

    **One host per LISTEN-DECLARING app is the contract** -- `_emit_apps`
    refuses a multi-bound app that declares `listen:` (review S4), precisely
    so this fan-out can never publish a declared port for a host the emitter
    silently skipped provisioning. Multi-bound apps WITHOUT `listen:` still
    exist in shipped content and still lose every binding after the first;
    that is a pre-existing defect recorded separately, and it cannot reach
    this map because an app with no `listen:` contributes nothing to it.
    The reverse direction is allowed and normal: several APPS may be bound to
    one host, so entries are merged in declaration order and de-duplicated on
    `(port, proto)`. A malformed entry (no port) is SKIPPED rather
    than raising: the IR model's fields are
    `Optional` (every field in that version-locked module is), a
    twin-config-authored set is already shape-checked at its own layer
    (`twinconfig.expand._check_listen`), and a hand-authored IR half-entry
    must not take a whole deploy down over an informational fact. `proto`
    defaults to `tcp` -- the transport the overwhelming majority of declared
    ports use, and the one an unqualified port number means everywhere else
    in this codebase (`fidelity.probe.method_for_port`).
    """
    out: dict[str, list[dict[str, Any]]] = {}
    if ir.layers is None or not ir.layers.apps:
        return out
    listen_by_app: dict[str, list[dict[str, Any]]] = {}
    for app in ir.layers.apps:
        if app.id is None or not app.listen:
            continue
        entries: list[dict[str, Any]] = []
        for li in app.listen:
            if li.port is None:
                continue
            try:
                port = int(li.port)
            except (TypeError, ValueError):
                continue
            entries.append({"port": port, "proto": str(li.proto or "tcp").lower()})
        if entries:
            listen_by_app[app.id] = entries
    if not listen_by_app:
        return out
    for b in (ir.bindings or []):
        if not b.app or not b.host:
            continue
        for entry in listen_by_app.get(b.app, []):
            bucket = out.setdefault(b.host, [])
            if entry not in bucket:
                bucket.append(entry)
    return out


def _classify_install(app_id: str, actions: list[Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """Split an app's install actions into provision-app action dicts and
    run-script paths. provision-app carries `pkg` / `pkg@version` and `role: adds`
    (W2a — AD DC promotion, docs/SLICE_WINDOWS_AD.md §6.1). Other `role:` values,
    plus config / container, are deferred (a clear EmissionError)."""
    install_actions: list[dict[str, Any]] = []
    script_paths: list[str] = []
    for a in actions:
        if a.pkg is not None:
            d: dict[str, Any] = {"pkg": a.pkg}
            if a.version is not None:
                d["version"] = a.version
            install_actions.append(d)
        elif a.script is not None:
            script_paths.append(a.script)
        elif a.role is not None:
            if a.role != "adds":
                raise EmissionError(
                    f"app {app_id!r}: role={a.role!r} not supported (only `role: adds`"
                    " — AD domain-controller promotion — is implemented; other roles"
                    " are a fast-follow — docs/SLICE_WINDOWS_AD.md §6)"
                )
            install_actions.append({"role": "adds"})
        else:
            kind = (
                "config" if a.config is not None
                else "container" if a.container is not None
                else "<empty>"
            )
            raise EmissionError(
                f"app {app_id!r}: install action kind {kind!r} not supported by the "
                "config slice (pkg / pkg@version / script / role:adds; config / "
                "container are a fast-follow — docs/SLICE_CONFIG.md)"
            )
    return install_actions, script_paths


def _provides_domain(app: Any) -> str | None:
    """The AD forest FQDN a `role: adds` app declares via provides:[{domain: …}]
    (IR_SCHEMA §4). None if absent — the emitter turns that into an error."""
    for p in (app.provides or []):
        if isinstance(p, dict) and p.get("domain"):
            return str(p["domain"])
    return None


def _dc_host_for_domain(ir: IR, domain: str) -> str | None:
    """The host running the `role: adds` app that provides `domain` — i.e. the
    domain controller (W2b). Resolved via the apps' provides + the app->host
    binding; None if the scenario has no DC for that domain."""
    apps = list(ir.layers.apps or []) if ir.layers else []
    bindings = list(ir.bindings or [])
    for app in apps:
        if app.id is None:
            continue
        if not any(a.role == "adds" for a in (app.install or [])):
            continue
        if _provides_domain(app) != domain:
            continue
        return next((b.host for b in bindings if b.app == app.id and b.host), None)
    return None


def _domain_provided_by_host(ir: IR, host_id: str) -> str | None:
    """The AD domain a host serves as DC, if any — it runs a `role: adds` app
    (bound to it) that provides a domain. Reverse of `_dc_host_for_domain`; used to
    gate host-side AD auditing on domain-ready (post-promotion)."""
    apps = {a.id: a for a in (ir.layers.apps or []) if a.id} if ir.layers else {}
    for b in (ir.bindings or []):
        if b.host != host_id or not b.app:
            continue
        app = apps.get(b.app)
        if app and any(a.role == "adds" for a in (app.install or [])):
            return _provides_domain(app)
    return None


def _read_script(
    scenario_dir: str | Path | None, rel_path: str, app_id: str
) -> tuple[str, str]:
    """Read a `{script:}` action's file relative to the scenario dir at emit
    time (the config backend can't reach the scenario bundle). Returns
    (content, basename)."""
    if scenario_dir is None:
        raise EmissionError(
            f"app {app_id!r} has a script: action but build_plan was called without "
            "scenario_dir — the script file can't be located (the CLI passes it; "
            "pass scenario_dir= when planning programmatically)"
        )
    p = Path(scenario_dir) / rel_path
    try:
        return p.read_text(encoding="utf-8"), Path(rel_path).name
    except OSError as e:
        raise EmissionError(
            f"app {app_id!r}: cannot read script {rel_path!r} (resolved {p}): {e}"
        )


def _package_manager(image_id: str | None, manifests: ManifestRegistry) -> str:
    """First package manager from the image's install_support.pkg (default apt)."""
    if image_id is None:
        return "apt"
    m = manifests.get_manifest(image_id)
    if m is None:
        return "apt"
    cur = m.current_version()
    if (
        cur is not None
        and cur.install_support is not None
        and isinstance(cur.install_support.pkg, list)
        and cur.install_support.pkg
    ):
        return str(cur.install_support.pkg[0])
    return "apt"


def _first_boot_budget(
    node: Node, manifests: ManifestRegistry, image_id: str | None
) -> int | None:
    if image_id is None:
        return None
    m = manifests.get_manifest(image_id)
    if m is None or m.image is None:
        return None
    image_ref = node.image or ""
    _, version_id = _parse_image_ref(image_ref)
    if version_id:
        v = m.get_version(version_id)
        if v is not None and v.first_boot_budget is not None:
            return v.first_boot_budget
    cur = m.current_version()
    return cur.first_boot_budget if cur else None


def _os_family(image_id: str | None, manifests: ManifestRegistry) -> str | None:
    if image_id is None:
        return None
    m = manifests.get_manifest(image_id)
    if m is None or m.image is None or m.image.os is None:
        return None
    return m.image.os.family
