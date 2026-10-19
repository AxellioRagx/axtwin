"""FAB-* checks — Pass 3, accumulate.

Fabric slice (docs/design/04-networking-capture.md): validate the `fabric:` block and
per-router `routing:` intent for the VyOS dynamic-routing/overlay renderer.
"""
from __future__ import annotations

from axrange.validator._make import make_finding
from axrange.validator.checks import Check, ValidatorContext, node_kind
from axrange.validator.finding import Finding

_ROLES = {"spine", "leaf", "border", "abr", "area", "provider", "edge"}
_VNI_MAX = 16_777_215  # 2**24 - 1

# Alias kept for in-module readability at existing call sites.
_kind = node_kind


def _router_ids(ctx: ValidatorContext) -> set[str]:
    if ctx.ir.layers is None:
        return set()
    return {n.id for n in ctx.ir.layers.infra.nodes if n.id and _kind(n) == "router"}


def _region_igp(ctx: ValidatorContext, region_id: str | None) -> str | None:
    if region_id is None or ctx.ir.fabric is None or ctx.ir.fabric.regions is None:
        return None
    for r in ctx.ir.fabric.regions:
        if r.id == region_id:
            return (r.igp or "").lower() or None
    return None


def check_fab_010(ctx: ValidatorContext) -> list[Finding]:
    """A node with routing: must be kind: router."""
    findings: list[Finding] = []
    if ctx.ir.layers is None:
        return findings
    for i, node in enumerate(ctx.ir.layers.infra.nodes):
        if node.routing is None:
            continue
        if _kind(node) != "router":
            findings.append(make_finding(
                "FAB-010", node_id=node.id or f"<index {i}>", kind=_kind(node) or "<unset>",
                location=f"layers.infra.nodes[{i}].routing"))
    return findings


def check_fab_020(ctx: ValidatorContext) -> list[Finding]:
    """routing.region is set and resolves to a fabric.regions[].id."""
    findings: list[Finding] = []
    if ctx.ir.layers is None:
        return findings
    regions = set()
    if ctx.ir.fabric is not None and ctx.ir.fabric.regions is not None:
        regions = {r.id for r in ctx.ir.fabric.regions if r.id}
    for i, node in enumerate(ctx.ir.layers.infra.nodes):
        if node.routing is None:
            continue
        region = node.routing.region
        if not region or region not in regions:
            findings.append(make_finding(
                "FAB-020", node_id=node.id or f"<index {i}>", region=region or "<missing>",
                location=f"layers.infra.nodes[{i}].routing.region"))
    return findings


def check_fab_030(ctx: ValidatorContext) -> list[Finding]:
    """routing.role is in the supported set."""
    findings: list[Finding] = []
    if ctx.ir.layers is None:
        return findings
    for i, node in enumerate(ctx.ir.layers.infra.nodes):
        if node.routing is None:
            continue
        role = node.routing.role
        if role is None or role not in _ROLES:
            findings.append(make_finding(
                "FAB-030", node_id=node.id or f"<index {i}>", role=role or "<missing>",
                location=f"layers.infra.nodes[{i}].routing.role"))
    return findings


def check_fab_040(ctx: ValidatorContext) -> list[Finding]:
    """bgp.rr_clients_of entries resolve to defined router nodes."""
    findings: list[Finding] = []
    if ctx.ir.layers is None:
        return findings
    routers = _router_ids(ctx)
    for i, node in enumerate(ctx.ir.layers.infra.nodes):
        if node.routing is None or node.routing.bgp is None:
            continue
        for ref in (node.routing.bgp.rr_clients_of or []):
            if ref not in routers:
                findings.append(make_finding(
                    "FAB-040", node_id=node.id or f"<index {i}>", ref=ref,
                    location=f"layers.infra.nodes[{i}].routing.bgp.rr_clients_of"))
    return findings


def check_fab_050(ctx: ValidatorContext) -> list[Finding]:
    """A router in an isis/both region declares routing.isis.net."""
    findings: list[Finding] = []
    if ctx.ir.layers is None:
        return findings
    for i, node in enumerate(ctx.ir.layers.infra.nodes):
        if node.routing is None:
            continue
        if _region_igp(ctx, node.routing.region) in ("isis", "both"):
            net = node.routing.isis.net if node.routing.isis else None
            if not net:
                findings.append(make_finding(
                    "FAB-050", node_id=node.id or f"<index {i}>",
                    region=node.routing.region or "<?>",
                    location=f"layers.infra.nodes[{i}].routing.isis"))
    return findings


def check_fab_060(ctx: ValidatorContext) -> list[Finding]:
    """networks[].vni is a valid VNI and the network has nodes attached to bridge it."""
    findings: list[Finding] = []
    if ctx.ir.layers is None:
        return findings
    attached: set[str] = set()
    for node in ctx.ir.layers.infra.nodes:
        if node.net:
            attached.add(node.net)
        for nr in (node.nets or []):
            if nr.net:
                attached.add(nr.net)
    for i, net in enumerate(ctx.ir.layers.infra.networks):
        if net.vni is None:
            continue
        if not isinstance(net.vni, int) or not (1 <= net.vni <= _VNI_MAX):
            findings.append(make_finding(
                "FAB-060", net_id=net.id or f"<index {i}>",
                detail=f"invalid vni {net.vni!r} (expect int 1..{_VNI_MAX})",
                location=f"layers.infra.networks[{i}].vni"))
        elif net.id not in attached:
            findings.append(make_finding(
                "FAB-060", net_id=net.id or f"<index {i}>",
                detail="no nodes attached to bridge the VNI",
                location=f"layers.infra.networks[{i}]"))
    return findings


def check_fab_070(ctx: ValidatorContext) -> list[Finding]:
    """loopback should be set when bgp is present (router-id/update-source/VTEP source)."""
    findings: list[Finding] = []
    if ctx.ir.layers is None:
        return findings
    for i, node in enumerate(ctx.ir.layers.infra.nodes):
        r = node.routing
        if r is None or r.bgp is None:
            continue
        if not r.loopback:
            findings.append(make_finding(
                "FAB-070", node_id=node.id or f"<index {i}>",
                location=f"layers.infra.nodes[{i}].routing"))
    return findings


def check_fab_080(ctx: ValidatorContext) -> list[Finding]:
    """Fabric routers must use a VyOS image (this slice is VyOS-only)."""
    findings: list[Finding] = []
    if ctx.ir.layers is None:
        return findings
    for i, node in enumerate(ctx.ir.layers.infra.nodes):
        if node.routing is None:
            continue
        img = (node.image or "").lower()
        if not img.startswith("vyos"):
            findings.append(make_finding(
                "FAB-080", node_id=node.id or f"<index {i}>", image=node.image or "<missing>",
                location=f"layers.infra.nodes[{i}].image"))
    return findings


CHECKS: list[Check] = [
    check_fab_010,
    check_fab_020,
    check_fab_030,
    check_fab_040,
    check_fab_050,
    check_fab_060,
    check_fab_070,
    check_fab_080,
]
