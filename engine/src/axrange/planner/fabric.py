"""Planner-side fabric context builder (docs/design/04-networking-capture.md).

Resolves a `kind: router` node's `routing:` intent plus the surrounding topology
(net→eth mapping, router↔router detection, per-net OSPF area / VNI, RR peer
loopbacks) into the flat per-router context dict consumed by
`axrange.adapter_proxmox.fabric_render.render_routing`. Kept on the planner side
so the adapter renderer needs no IR knowledge (boundary per CLAUDE.md).
"""
from __future__ import annotations

from typing import Any

from axrange.ir.schema import Network, Node


def _kind(node: Node) -> str | None:
    if node.kind is None:
        return None
    return str(node.kind).lower().split(".")[-1]


def _legs(node: Node) -> list[tuple[str, str | None]]:
    """[(net_id, ip|None), ...] in NIC order; eth0=WAN so nets[i] -> eth{i+1}."""
    if node.nets:
        return [(n.net or "", n.ip) for n in node.nets if n.net]
    if node.net:
        return [(node.net, None)]
    return []


def _net_ids(node: Node) -> set[str]:
    return {nid for nid, _ in _legs(node)}


def _strip_mask(addr: str | None) -> str | None:
    if not addr:
        return None
    return addr.split("/")[0]


def build_fabric_inputs(
    node: Node,
    nodes: list[Node],
    networks: list[Network],
    fabric: Any | None,
) -> dict[str, Any]:
    """Build the per-router fabric context for configure-routing.inputs_resolved['fabric']."""
    r = node.routing
    assert r is not None  # caller gates on node.routing is not None

    regions = fabric.regions if fabric else None
    tenant = fabric.tenant if fabric else None

    igp = None
    for reg in (regions or []):
        if reg.id == r.region:
            igp = (reg.igp or "").lower() or None
            break

    net_by_id = {n.id: n for n in networks if n.id}

    # router↔router (p2p) detection: a net whose every attached node is a router.
    kinds_on_net: dict[str, list[str]] = {}
    for n in nodes:
        for nid in _net_ids(n):
            kinds_on_net.setdefault(nid, []).append(_kind(n) or "")

    def _p2p(nid: str) -> bool:
        ks = kinds_on_net.get(nid, [])
        return len(ks) >= 2 and all(k == "router" for k in ks)

    # router IPs per net, for BFD peer (neighbor) resolution on p2p legs.
    router_ips_on_net: dict[str, list[tuple[str | None, str]]] = {}
    for n in nodes:
        if _kind(n) != "router":
            continue
        for nid, ip in _legs(n):
            if ip:
                router_ips_on_net.setdefault(nid, []).append((n.id, ip))

    interfaces: list[dict[str, Any]] = []
    for i, (nid, _ip) in enumerate(_legs(node)):
        net = net_by_id.get(nid)
        peer_ip = None
        for oid, oip in router_ips_on_net.get(nid, []):
            if oid != node.id:
                peer_ip = _strip_mask(oip)
                break
        cidr = getattr(net, "cidr", None) if net else None
        prefix = str(cidr).split("/", 1)[1] if cidr and "/" in str(cidr) else None
        interfaces.append({
            "eth": f"eth{i + 1}",
            "net": nid,
            "p2p": _p2p(nid),
            "ospf_area": getattr(net, "ospf_area", None) if net else None,
            "vni": getattr(net, "vni", None) if net else None,
            "gateway": getattr(net, "gateway", None) if net else None,   # anycast SVI (IRB)
            "prefix": prefix,
            "peer_ip": peer_ip,
        })

    loop_ip = _strip_mask(r.loopback)
    if r.bgp and r.bgp.router_id:
        router_id = r.bgp.router_id
    elif r.ospf and r.ospf.router_id:
        router_id = r.ospf.router_id
    else:
        router_id = loop_ip

    ctx: dict[str, Any] = {
        "router_os": "vyos",
        "region": r.region,
        "igp": igp,
        "role": r.role,
        "loopback": r.loopback,
        "router_id": router_id,
        "isis_net": r.isis.net if r.isis else None,
        "interfaces": interfaces,
        "redistribute": list(r.redistribute or []),
        "bfd": bool(r.bfd),
    }
    if tenant is not None:
        ctx["tenant"] = {
            "name": tenant.name,
            "l3vni": tenant.l3vni,
            "table": tenant.table,
        }

    if r.bgp:
        node_by_id = {n.id: n for n in nodes if n.id}

        def _lo(nid: str | None) -> str | None:
            nn = node_by_id.get(nid) if nid else None
            rt = getattr(nn, "routing", None) if nn else None
            return _strip_mask(rt.loopback) if rt and rt.loopback else None

        rr_client_loopbacks: list[str] = []
        if r.bgp.rr_server:
            # clients = routers whose rr_clients_of names THIS node.
            for n in nodes:
                rt = getattr(n, "routing", None)
                if rt and rt.bgp and node.id in (rt.bgp.rr_clients_of or []):
                    lo = _lo(n.id)
                    if lo:
                        rr_client_loopbacks.append(lo)
        rr_spine_loopbacks = [x for x in (_lo(s) for s in (r.bgp.rr_clients_of or [])) if x]

        ctx["bgp"] = {
            "asn": r.bgp.asn,
            "rr_server": bool(r.bgp.rr_server),
            "rr_client_loopbacks": rr_client_loopbacks,
            "rr_spine_loopbacks": rr_spine_loopbacks,
            "evpn": bool(r.bgp.evpn),
            "vtep": bool(r.bgp.vtep),
            "ebgp_peers": [
                {"neighbor": p.neighbor, "remote_asn": p.remote_asn}
                for p in (r.bgp.ebgp_peers or [])
            ],
            "originate": list(r.bgp.originate or []),
        }

    return ctx
