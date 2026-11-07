"""NET-* checks — Pass 3, accumulate."""
from __future__ import annotations

import ipaddress
from collections import Counter
from typing import Optional

from axrange.validator._make import make_finding
from axrange.validator.checks import Check, ValidatorContext, node_kind
from axrange.validator.finding import Finding


def _parse_network(cidr: str) -> Optional[ipaddress._BaseNetwork]:
    try:
        return ipaddress.ip_network(cidr, strict=False)
    except (ValueError, TypeError):
        return None


def _parse_address(ip: str) -> Optional[ipaddress._BaseAddress]:
    try:
        return ipaddress.ip_address(ip)
    except (ValueError, TypeError):
        return None


def check_net_010(ctx: ValidatorContext) -> list[Finding]:
    """No two networks have overlapping CIDRs (excluding isolated: true)."""
    findings = []
    if ctx.ir.layers is None:
        return findings
    nets = []
    for net in ctx.ir.layers.infra.networks:
        if net.isolated:
            continue
        parsed = _parse_network(net.cidr or "")
        if parsed is None:
            continue  # NET-020 reports it
        nets.append((net.id, parsed))
    for i, (a_id, a) in enumerate(nets):
        for b_id, b in nets[i + 1 :]:
            if a.overlaps(b):
                findings.append(
                    make_finding(
                        "NET-010",
                        a=a_id,
                        b=b_id,
                        cidr_a=str(a),
                        cidr_b=str(b),
                    )
                )
    return findings


def check_net_020(ctx: ValidatorContext) -> list[Finding]:
    """Every network.cidr is a valid IPv4/IPv6 CIDR."""
    findings = []
    if ctx.ir.layers is None:
        return findings
    for i, net in enumerate(ctx.ir.layers.infra.networks):
        if net.cidr is None:
            findings.append(
                make_finding(
                    "NET-020",
                    net_id=net.id or f"<index {i}>",
                    cidr="<missing>",
                    location=f"layers.infra.networks[{i}]",
                )
            )
            continue
        if _parse_network(net.cidr) is None:
            findings.append(
                make_finding(
                    "NET-020",
                    net_id=net.id or f"<index {i}>",
                    cidr=net.cidr,
                    location=f"layers.infra.networks[{i}]",
                )
            )
    return findings


def check_net_030(ctx: ValidatorContext) -> list[Finding]:
    """Multi-homed node's nets[] entries reference distinct networks."""
    findings = []
    if ctx.ir.layers is None:
        return findings
    for i, node in enumerate(ctx.ir.layers.infra.nodes):
        if not node.nets:
            continue
        net_ids = [n.net for n in node.nets if n.net]
        seen: set[str] = set()
        for n_id in net_ids:
            if n_id in seen:
                findings.append(
                    make_finding(
                        "NET-030",
                        node_id=node.id or f"<index {i}>",
                        net_id=n_id,
                        location=f"layers.infra.nodes[{i}].nets",
                    )
                )
            seen.add(n_id)
    return findings


def check_net_040(ctx: ValidatorContext) -> list[Finding]:
    """Each nets[] entry's IP (if specified) is within the corresponding CIDR."""
    findings = []
    if ctx.ir.layers is None:
        return findings
    nets_by_id = {n.id: n for n in ctx.ir.layers.infra.networks if n.id}
    for i, node in enumerate(ctx.ir.layers.infra.nodes):
        if not node.nets:
            continue
        for j, entry in enumerate(node.nets):
            if entry.net is None or entry.ip is None:
                continue
            net = nets_by_id.get(entry.net)
            if net is None or net.cidr is None:
                continue
            net_obj = _parse_network(net.cidr)
            ip_obj = _parse_address(entry.ip)
            if net_obj is None or ip_obj is None:
                continue
            if ip_obj not in net_obj:
                findings.append(
                    make_finding(
                        "NET-040",
                        node_id=node.id or f"<index {i}>",
                        ip=entry.ip,
                        net_id=entry.net,
                        cidr=net.cidr,
                        location=f"layers.infra.nodes[{i}].nets[{j}]",
                    )
                )
    return findings


def check_net_050(ctx: ValidatorContext) -> list[Finding]:
    """No two networks share the same VLAN ID."""
    findings = []
    if ctx.ir.layers is None:
        return findings
    vlan_to_nets: dict[int, list[str]] = {}
    for net in ctx.ir.layers.infra.networks:
        if net.vlan is None or net.id is None:
            continue
        vlan_to_nets.setdefault(net.vlan, []).append(net.id)
    for vlan, ids in vlan_to_nets.items():
        if len(ids) > 1:
            # Emit a single finding per duplicate-vlan group (pairwise alt OK).
            findings.append(
                make_finding(
                    "NET-050",
                    a=ids[0],
                    b=", ".join(ids[1:]),
                    vlan=vlan,
                )
            )
    return findings


def check_net_060(ctx: ValidatorContext) -> list[Finding]:
    """Cross-scenario VLAN collision. Dormant in single-scenario mode."""
    return []


def check_net_070(ctx: ValidatorContext) -> list[Finding]:
    """Defense observes: a network the defense host can't see, and tap_from is not external-mirror."""
    findings = []
    if ctx.ir.overlays is None or ctx.ir.overlays.defense is None:
        return findings
    if ctx.ir.layers is None:
        return findings
    nodes_by_id = {n.id: n for n in ctx.ir.layers.infra.nodes if n.id}
    for i, d in enumerate(ctx.ir.overlays.defense):
        if not d.observes:
            continue
        if d.tap_from == "external-mirror":
            continue
        if d.host is None or d.host not in nodes_by_id:
            continue
        host = nodes_by_id[d.host]
        host_nets: set[str] = set()
        if host.net:
            host_nets.add(host.net)
        if host.nets:
            host_nets.update(n.net for n in host.nets if n.net)
        for j, o in enumerate(d.observes):
            if o.net is not None and o.net not in host_nets:
                findings.append(
                    make_finding(
                        "NET-070",
                        defense_id=d.id or f"<index {i}>",
                        host_id=d.host,
                        net_id=o.net,
                        location=f"overlays.defense[{i}].observes[{j}]",
                    )
                )
    return findings


def check_net_080(ctx: ValidatorContext) -> list[Finding]:
    """Router has at least one net[].

    Phase 2 (docs/SLICE_ROUTER.md): a router with 1 LAN net is a valid egress/NAT
    gateway (its WAN uplink is the adapter-injected other side); 2+ LAN nets make
    it an inter-segment router. So the floor is 1, not 2.
    """
    findings = []
    if ctx.ir.layers is None:
        return findings
    for i, node in enumerate(ctx.ir.layers.infra.nodes):
        kind = node_kind(node)
        if kind != "router":
            continue
        n = (1 if node.net else 0) + len(node.nets or [])
        if n < 1:
            findings.append(
                make_finding(
                    "NET-080",
                    node_id=node.id or f"<index {i}>",
                    n=n,
                    location=f"layers.infra.nodes[{i}]",
                )
            )
    return findings


def check_net_090(ctx: ValidatorContext) -> list[Finding]:
    """Switch has ≥ 1 net entry and bridges within a single broadcast domain."""
    findings = []
    if ctx.ir.layers is None:
        return findings
    for i, node in enumerate(ctx.ir.layers.infra.nodes):
        kind = node_kind(node)
        if kind != "switch":
            continue
        # Switches: validate at least one net is declared. The "single broadcast
        # domain" semantic is structural — multi-homed routers route, switches
        # bridge — checked here by ensuring a switch has nets but no IP per entry.
        n = len(node.nets or [])
        if n == 0 and node.net is None:
            findings.append(
                make_finding(
                    "NET-090",
                    node_id=node.id or f"<index {i}>",
                    detail="switch has no nets attached",
                    location=f"layers.infra.nodes[{i}]",
                )
            )
        if node.nets:
            for j, entry in enumerate(node.nets):
                if entry.ip is not None:
                    findings.append(
                        make_finding(
                            "NET-090",
                            node_id=node.id or f"<index {i}>",
                            detail=f"switch nets[{j}] should not declare an IP",
                            location=f"layers.infra.nodes[{i}].nets[{j}]",
                        )
                    )
    return findings


def check_net_100(ctx: ValidatorContext) -> list[Finding]:
    """Orphan network (no nodes attached)."""
    findings = []
    if ctx.ir.layers is None:
        return findings
    used_nets: set[str] = set()
    for node in ctx.ir.layers.infra.nodes:
        if node.net:
            used_nets.add(node.net)
        if node.nets:
            used_nets.update(n.net for n in node.nets if n.net)
    for i, net in enumerate(ctx.ir.layers.infra.networks):
        if net.id and net.id not in used_nets:
            findings.append(
                make_finding(
                    "NET-100",
                    net_id=net.id,
                    location=f"layers.infra.networks[{i}]",
                )
            )
    return findings


# check_net_110 "Tapped segment has at least one node attached" RETIRED
# 2026-07-28 (`docs/WORK_ORDER_tap_eligibility.md`, Authority: user) --
# `networks[].tap` no longer exists (NET-110 retired alongside it,
# catalog.py). See that file's comment for the full rationale.


def check_net_120(ctx: ValidatorContext) -> list[Finding]:
    """No device uses one port name on two links (both ends counted)."""
    findings: list[Finding] = []
    if ctx.ir.layers is None:
        return findings
    seen: dict[tuple[str, str], int] = {}
    for i, link in enumerate(ctx.ir.layers.infra.links):
        for end in (link.a, link.b):
            if end is None or not end.device or not end.port:
                continue
            key = (end.device, end.port)
            if key in seen:
                findings.append(make_finding(
                    "NET-120", device=end.device, port=end.port, first=seen[key], second=i,
                    location=f"layers.infra.links[{i}]",
                ))
            else:
                seen[key] = i
    return findings


def check_net_130(ctx: ValidatorContext) -> list[Finding]:
    """A link's two ends are different devices; an `isolated: true` network
    shares its CIDR with exactly one non-isolated network (its anchor) --
    the twin-config expander's rule, repeated on the IR (NET-010 states the
    overlap rule for the non-isolated set)."""
    findings: list[Finding] = []
    if ctx.ir.layers is None:
        return findings
    infra = ctx.ir.layers.infra
    for i, link in enumerate(infra.links):
        if link.a is not None and link.b is not None and link.a.device and link.a.device == link.b.device:
            findings.append(make_finding(
                "NET-130", detail=f"links[{i}]: both ends are device '{link.a.device}'",
                location=f"layers.infra.links[{i}]",
            ))
    plain = []
    for net in infra.networks:
        if net.isolated:
            continue
        parsed = _parse_network(net.cidr or "")
        if parsed is not None:
            plain.append((net.id, parsed))
    for i, net in enumerate(infra.networks):
        if not net.isolated:
            continue
        parsed = _parse_network(net.cidr or "")
        if parsed is None:
            continue  # NET-020
        anchors = [nid for nid, p in plain if p == parsed]
        if len(anchors) != 1:
            findings.append(make_finding(
                "NET-130",
                detail=(
                    f"network '{net.id}' is isolated with cidr {parsed}, which must equal "
                    f"exactly one non-isolated network's (found {len(anchors)}"
                    f"{': ' + ', '.join(str(a) for a in anchors) if anchors else ''})"
                ),
                location=f"layers.infra.networks[{i}]",
            ))
    return findings


def check_net_140(ctx: ValidatorContext) -> list[Finding]:
    """A `kind: switch` node that no `links[]` entry names is unwired: a
    parked device, or the pre-device-tree `nets:` spelling. A warning (the
    canvas blocks Apply and Save on a parked one). A switch with no `nets:`
    at all never reaches this pass: STR-070 halts on it."""
    findings: list[Finding] = []
    if ctx.ir.layers is None:
        return findings
    infra = ctx.ir.layers.infra
    linked = set()
    for link in infra.links:
        for end in (link.a, link.b):
            if end is not None and end.device:
                linked.add(end.device)
    for i, node in enumerate(infra.nodes):
        if node_kind(node) != "switch":
            continue
        if node.id not in linked:
            findings.append(make_finding(
                "NET-140", node_id=node.id or f"<index {i}>",
                location=f"layers.infra.nodes[{i}]",
            ))
    return findings


CHECKS: list[Check] = [
    check_net_010,
    check_net_020,
    check_net_030,
    check_net_040,
    check_net_050,
    check_net_060,
    check_net_070,
    check_net_080,
    check_net_090,
    check_net_100,
    check_net_120,
    check_net_130,
    check_net_140,
]
