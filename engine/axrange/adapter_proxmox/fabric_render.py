"""VyOS fabric (dynamic-routing / overlay) config renderer.

Pure string generation: a per-router fabric *context dict* (built by the planner
in axrange.planner.fabric — it carries the resolved topology so this module needs
no IR/Proxmox knowledge) → a flat list of VyOS `set …` lines. The adapter wraps
the list in the `vbash -s` script-template envelope and pushes it over SSH.

Templates are ported from dtDataCenter's `orchestrate.py` (live-verified on
pxtwin1); see docs/design/04-networking-capture.md. Route-map names use the `axr-`
namespace; the tag-9998/9999 loop-prevention scheme is preserved.

Covers: loopback, IS-IS L2, OSPFv2 multi-area, iBGP + route-reflector (incl. the
l2vpn-evpn address-family on the peer-groups), eBGP, border mutual-redistribution
route-maps, VXLAN data-plane (interfaces/bridges + advertise-all-vni on VTEP
leaves, P-d), and BFD on IGP neighbors (P-e).
"""
from __future__ import annotations

from typing import Any


def _ip(addr: str | None) -> str | None:
    """Strip a /mask from an address (loopback peering / VTEP source want a bare IP)."""
    if not addr:
        return None
    return addr.split("/")[0]


def _render_loopback(fabric: dict[str, Any]) -> list[str]:
    lo = fabric.get("loopback")
    if not lo:
        return []
    return [f"set interfaces dummy dum0 address '{lo}'"]


def _render_isis(fabric: dict[str, Any]) -> list[str]:
    net = fabric.get("isis_net")
    if not net:
        return []
    out = [
        f"set protocols isis net {net}",
        "set protocols isis level level-2",
        "set protocols isis log-adjacency-changes",
    ]
    if fabric.get("loopback"):
        out.append("set protocols isis interface dum0 passive")
    # Enable IS-IS only on router↔router (p2p) legs.
    for itf in fabric.get("interfaces", []):
        if not itf.get("p2p"):
            continue
        eth = itf["eth"]
        out.append(f"set protocols isis interface {eth} network point-to-point")
        out.append(f"set protocols isis interface {eth} circuit-type level-2-only")
    return out


def _render_ospf(fabric: dict[str, Any]) -> list[str]:
    rid = fabric.get("router_id")
    out: list[str] = []
    if rid:
        out.append(f"set protocols ospf parameters router-id {rid}")
    out.append("set protocols ospf log-adjacency-changes")
    if fabric.get("loopback"):
        loopback_area = "1" if fabric.get("role") == "area" else "0"
        out.append(f"set protocols ospf interface dum0 area {loopback_area}")
        out.append("set protocols ospf interface dum0 passive enable")
    for itf in fabric.get("interfaces", []):
        area = itf.get("ospf_area")
        if area is None:
            continue
        eth = itf["eth"]
        out.append(f"set protocols ospf interface {eth} area {area}")
        if itf.get("p2p"):
            out.append(f"set protocols ospf interface {eth} network point-to-point")
    return out


def _render_bgp(fabric: dict[str, Any]) -> list[str]:
    bgp = fabric.get("bgp")
    if not bgp:
        return []
    asn = bgp.get("asn")
    rid = fabric.get("router_id")
    out = [
        f"set protocols bgp system-as {asn}",
        f"set protocols bgp parameters router-id {rid}",
        "set protocols bgp parameters log-neighbor-changes",
    ]
    evpn = bool(bgp.get("evpn"))
    # iBGP route-reflector SERVER (spine): peer-group LEAVES + per-client neighbor.
    if bgp.get("rr_server"):
        out += [
            f"set protocols bgp peer-group LEAVES remote-as {asn}",
            "set protocols bgp peer-group LEAVES update-source dum0",
        ]
        if evpn:
            out += [
                "set protocols bgp peer-group LEAVES address-family l2vpn-evpn",
                "set protocols bgp peer-group LEAVES address-family l2vpn-evpn route-reflector-client",
            ]
        for lo in bgp.get("rr_client_loopbacks", []):
            out.append(f"set protocols bgp neighbor {lo} peer-group LEAVES")
    # iBGP route-reflector CLIENT (leaf): peer-group SPINES + per-spine neighbor.
    if bgp.get("rr_spine_loopbacks"):
        out += [
            f"set protocols bgp peer-group SPINES remote-as {asn}",
            "set protocols bgp peer-group SPINES update-source dum0",
        ]
        if evpn:
            out.append("set protocols bgp peer-group SPINES address-family l2vpn-evpn")
        for lo in bgp["rr_spine_loopbacks"]:
            out.append(f"set protocols bgp neighbor {lo} peer-group SPINES")
    # eBGP peers + originated networks.
    for p in bgp.get("ebgp_peers", []):
        out += [
            f"set protocols bgp neighbor {p['neighbor']} remote-as {p['remote_asn']}",
            f"set protocols bgp neighbor {p['neighbor']} address-family ipv4-unicast",
        ]
    for pfx in bgp.get("originate", []):
        out.append(f"set protocols bgp address-family ipv4-unicast network {pfx}")
    # Border: pull the IGP + connected routes into eBGP (advertise the fabric out).
    # IGP-aware: only redistribute the IGP(s) this border actually runs.
    if fabric.get("role") == "border" and bgp.get("ebgp_peers"):
        igp = (fabric.get("igp") or "").lower()
        out.append("set protocols bgp address-family ipv4-unicast redistribute connected")
        if igp in ("ospf", "both"):
            out.append("set protocols bgp address-family ipv4-unicast redistribute ospf")
        if igp in ("isis", "both"):
            out.append("set protocols bgp address-family ipv4-unicast redistribute isis")
    return out


def _render_redistribution(fabric: dict[str, Any]) -> list[str]:
    """Border mutual OSPF↔IS-IS + BGP→IGP redistribution with tag loop-prevention.

    IGP-aware: the full mutual OSPF↔IS-IS maps are emitted only on a border that
    runs **both** IGPs (`igp: both`); a single-IGP border (an IS-IS-only DC edge
    or an OSPF-only campus edge) gets only that IGP's BGP→IGP redistribution —
    emitting the other protocol's `redistribute` would be rejected by VyOS.

    tag 9999 = IS-IS→OSPF marker; tag 9998 = BGP→IGP marker. Deny-tagged rules
    (rule 5) run ahead of the permit rules so a border never re-imports its own
    redistributed routes back through the other protocol.
    """
    igp = (fabric.get("igp") or "").lower()
    has_isis = igp in ("isis", "both")
    has_ospf = igp in ("ospf", "both")
    out: list[str] = []
    if not (has_isis or has_ospf):
        return out
    # mutual OSPF<->IS-IS maps only when the border bridges both IGPs
    if has_isis and has_ospf:
        out += [
            "set policy route-map axr-isis-to-ospf rule 5 action deny",
            "set policy route-map axr-isis-to-ospf rule 5 match tag 9998",
            "set policy route-map axr-isis-to-ospf rule 10 action permit",
            "set policy route-map axr-isis-to-ospf rule 10 set tag 9999",
            "set policy route-map axr-ospf-to-isis rule 5 action deny",
            "set policy route-map axr-ospf-to-isis rule 5 match tag 9998",
            "set policy route-map axr-ospf-to-isis rule 10 action deny",
            "set policy route-map axr-ospf-to-isis rule 10 match tag 9999",
            "set policy route-map axr-ospf-to-isis rule 20 action permit",
        ]
    # BGP->IGP marker map (whenever any IGP is present)
    out += [
        "set policy route-map axr-bgp-to-igp rule 10 action permit",
        "set policy route-map axr-bgp-to-igp rule 10 set tag 9998",
    ]
    if has_ospf:
        out.append("set protocols ospf redistribute connected")
        out.append("set protocols ospf redistribute bgp route-map axr-bgp-to-igp")
        if has_isis:
            out.append("set protocols ospf redistribute isis route-map axr-isis-to-ospf")
    if has_isis:
        out.append("set protocols isis redistribute ipv4 connected level-2")
        out.append("set protocols isis redistribute ipv4 bgp level-2 route-map axr-bgp-to-igp")
        if has_ospf:
            out.append("set protocols isis redistribute ipv4 ospf level-2 route-map axr-ospf-to-isis")
    return out


def _render_vxlan(fabric: dict[str, Any]) -> list[str]:
    """VXLAN data-plane on a VTEP leaf: one vxlan+bridge per attached `vni` net.

    The EVPN *control-plane* (l2vpn-evpn AF on the iBGP peer-groups) is rendered
    in _render_bgp; this adds the *data-plane* encap. The access leg becomes an
    L2 bridge member (it gets no L3 address — the planner skips configure-router
    addressing on vni legs). `advertise-all-vni` is emitted once if any VNI was
    bridged. NB: do not combine `parameters external` with an explicit vni —
    VyOS rolling rejects it (dtDataCenter render_vyos_vxlan).
    """
    bgp = fabric.get("bgp") or {}
    if fabric.get("role") != "leaf" or not bgp.get("vtep"):
        return []
    loop = _ip(fabric.get("loopback"))
    if not loop:
        return []
    out: list[str] = []
    bridged = False
    for itf in fabric.get("interfaces", []):
        vni = itf.get("vni")
        if vni is None:
            continue
        eth = itf["eth"]
        out += [
            f"set interfaces vxlan vxlan{vni} source-address {loop}",
            f"set interfaces vxlan vxlan{vni} vni {vni}",
            f"set interfaces bridge br{vni} description 'EVPN VNI {vni}'",
            f"set interfaces bridge br{vni} member interface {eth}",
            f"set interfaces bridge br{vni} member interface vxlan{vni}",
        ]
        bridged = True
    if bridged and bgp.get("evpn"):
        out.append("set protocols bgp address-family l2vpn-evpn advertise-all-vni")
    return out


def _render_bfd(fabric: dict[str, Any]) -> list[str]:
    """Attach BFD (sub-second failure detection) to the IGP on each p2p leg.

    Gated on `routing.bfd: true`. 300/300/3 (rx/tx ms, multiplier) ~ sub-second
    vs the multi-second IGP hold timers. Attached to OSPF on ospf_area legs, else
    IS-IS on p2p legs in an isis/both region (dtDataCenter scenario-bfd pattern).
    """
    if not fabric.get("bfd"):
        return []
    igp = (fabric.get("igp") or "").lower()
    out: list[str] = []
    for itf in fabric.get("interfaces", []):
        peer = itf.get("peer_ip")
        if not peer or not itf.get("p2p"):
            continue
        eth = itf["eth"]
        out += [
            f"set protocols bfd peer {peer} interval receive 300",
            f"set protocols bfd peer {peer} interval transmit 300",
            f"set protocols bfd peer {peer} interval multiplier 3",
        ]
        if itf.get("ospf_area") is not None:
            out.append(f"set protocols ospf interface {eth} bfd")
        elif igp in ("isis", "both"):
            out.append(f"set protocols isis interface {eth} bfd")
    return out


def _anycast_mac(vni: int) -> str:
    """Deterministic locally-administered anycast-gateway MAC per L2VNI — the same
    on every leaf bridging that VNI, so the distributed gateway is consistent."""
    b = int(vni) & 0xFFFFFF
    return f"02:00:{(b >> 16) & 0xFF:02x}:{(b >> 8) & 0xFF:02x}:{b & 0xFF:02x}:01"


def _l3vni_rmac(loopback: str, l3vni: int) -> str:
    """Deterministic, UNIQUE-PER-LEAF locally-administered MAC for the L3VNI bridge
    (`br<l3vni>`). FRR derives the L3VNI **Router-MAC** from this bridge's MAC and stamps
    it into every EVPN Type-5 route the leaf originates. Linked-clone leaves otherwise
    share an identical *auto-derived* bridge MAC, so each leaf REJECTS its peers' Type-5
    routes with `DENIED due to: self mac` (FRR's RMAC loop check) — cross-leaf
    symmetric-IRB routing then silently fails while Type-2/3 (no RMAC check) work. This
    was the real root cause of GAP-003 (live-verified on dev45 2026-07-01). Keying the
    MAC off the per-leaf loopback makes each leaf's Router-MAC unique. (Distinct from the
    L2VNI `_anycast_mac`, which is intentionally *shared* across leaves.)

    Assumes router loopbacks differ within their last three octets (the standard fabric
    convention, e.g. `10.255.0.0/24`)."""
    o = [int(x) & 0xFF for x in (_ip(loopback) or "0.0.0.0").split(".")]
    v = int(l3vni)
    return f"02:{(v >> 8) & 0xFF:02x}:{v & 0xFF:02x}:{o[1]:02x}:{o[2]:02x}:{o[3]:02x}"


def _render_irb(fabric: dict[str, Any]) -> list[str]:
    """Symmetric IRB (L3VNI/IRB follow-on): an anycast distributed gateway on each
    L2VNI bridge + a tenant VRF bound to an L3VNI, with per-VRF BGP advertising the
    gateway subnets as EVPN Type-5 — so hosts on different EVPN segments are
    L3-routed to each other across the fabric.

    Only on VTEP leaves when `fabric.tenant` is set and the L2VNI net carries a
    `gateway`. Inter-segment routing uses an explicit RD + shared route-target on the
    VRF's l2vpn-evpn so peers import the Type-5 routes, and a UNIQUE per-leaf Router-MAC
    on `br<l3vni>` (see `_l3vni_rmac`) so peers don't reject the Type-5 as "self mac".
    Internet egress (tenant VRF → WAN) is a follow-on — NOT emitted (the simple static
    leak is invalid VyOS syntax, Finding C).

    **Live-verified end-to-end on dev45 2026-07-01 (FRR 10.6.1) — cross-leaf `ha->hb`
    routes (4/4 ping, ttl 62 through the L3VNI).** The final blocker (GAP-003) was
    **duplicate L3VNI Router-MACs**: linked-clone leaves auto-derived an identical
    `br<l3vni>` MAC, so each leaf's Type-5 carried the same RMAC and every peer denied it
    (`DENIED due to: self mac`). Fixed by `_l3vni_rmac` (per-leaf MAC keyed off the
    loopback). The RT binding + Type-5 origination were already correct; the earlier
    "operational RT empty" / "RR doesn't reflect Type-5" readings were misdiagnoses
    (a grep artifact on a partially-applied config, and the RMAC-self drop happening on
    the *receiving* leaf, not the RR).
    """
    bgp = fabric.get("bgp") or {}
    tenant = fabric.get("tenant")
    if fabric.get("role") != "leaf" or not bgp.get("vtep") or not tenant:
        return []
    loop = _ip(fabric.get("loopback"))
    name = tenant.get("name")
    l3vni = tenant.get("l3vni")
    table = tenant.get("table")
    if not (loop and name and l3vni and table):
        return []
    # tenant VRF + its L3VNI transit (the routed VXLAN). The explicit per-leaf `mac` on
    # br<l3vni> is REQUIRED: it becomes the leaf's EVPN Type-5 Router-MAC, and must be
    # unique per leaf or peers reject the Type-5 as "self mac" (GAP-003, see _l3vni_rmac).
    out = [
        f"set vrf name {name} table {table}",
        f"set vrf name {name} vni {l3vni}",
        f"set interfaces vxlan vxlan{l3vni} source-address {loop}",
        f"set interfaces vxlan vxlan{l3vni} vni {l3vni}",
        f"set interfaces bridge br{l3vni} member interface vxlan{l3vni}",
        f"set interfaces bridge br{l3vni} mac {_l3vni_rmac(loop, l3vni)}",
        f"set interfaces bridge br{l3vni} vrf {name}",
    ]
    # anycast distributed gateway: an SVI on each L2VNI bridge, enslaved to the VRF
    bridged = False
    for itf in fabric.get("interfaces", []):
        vni = itf.get("vni")
        gw = itf.get("gateway")
        prefix = itf.get("prefix")
        if vni is None or not gw or not prefix:
            continue
        out += [
            f"set interfaces bridge br{vni} address {gw}/{prefix}",
            f"set interfaces bridge br{vni} mac {_anycast_mac(vni)}",
            f"set interfaces bridge br{vni} vrf {name}",
        ]
        bridged = True
    # per-VRF BGP: advertise the connected SVI subnets into EVPN as Type-5, with an
    # explicit RD (per-leaf, from the loopback router-id) + a shared route-target so
    # PEER leaves IMPORT the Type-5 into their tenant VRF.
    #
    # VyOS form is `route-target both <asn>:<l3vni>` (NOT `route-target vpn both ...` —
    # VyOS 1.5 rejects the `vpn` token: "route-target [vpn] is not valid"). It commits
    # cleanly and maps to FRR `route-target import/export <asn>:<l3vni>` under
    # `router bgp <asn> vrf <name> / address-family l2vpn evpn`.
    #
    # GAP-003 RESOLVED (live-verified dev45 2026-07-01, FRR 10.6.1): cross-leaf `ha->hb`
    # routes end-to-end. The final blocker was NOT the RT/RR at all — the RT binds, the
    # leaf originates the Type-5, and the RR reflects it. It was that linked-clone leaves
    # shared an identical L3VNI Router-MAC, so each leaf DENIED its peers' Type-5 with
    # `DENIED due to: self mac`. The unique per-leaf `br<l3vni>` MAC above (`_l3vni_rmac`)
    # fixes it. See docs/PRODUCT_GAPS.md GAP-003 for the full diagnostic trail.
    if bridged:
        asn = bgp.get("asn")
        rid = fabric.get("router_id")
        out += [
            f"set vrf name {name} protocols bgp system-as {asn}",
            f"set vrf name {name} protocols bgp address-family ipv4-unicast redistribute connected",
            f"set vrf name {name} protocols bgp address-family l2vpn-evpn advertise ipv4 unicast",
            f"set vrf name {name} protocols bgp address-family l2vpn-evpn rd {rid}:{l3vni}",
            f"set vrf name {name} protocols bgp address-family l2vpn-evpn route-target both {asn}:{l3vni}",
        ]
    # NB: tenant-VRF internet egress (apt/pip) is a follow-on, NOT emitted here. The
    # simple per-leaf static leak `route 0.0.0.0/0 next-hop <gw> next-hop-vrf default`
    # is INVALID VyOS syntax (live-verify dev45 2026-06-30 — Finding C: "[next-hop-vrf]
    # is not valid / Set failed"). Proper VRF egress needs a border-originated default
    # into the tenant VRF (Type-5) or BGP route-target leaking — docs/design/04-networking-capture.md.
    return out


def render_routing(fabric: dict[str, Any]) -> list[str]:
    """Render the full VyOS routing config for one router from its fabric context."""
    igp = (fabric.get("igp") or "").lower()
    out: list[str] = []
    out += _render_loopback(fabric)
    if igp in ("isis", "both"):
        out += _render_isis(fabric)
    if igp in ("ospf", "both"):
        out += _render_ospf(fabric)
    out += _render_bgp(fabric)
    if fabric.get("role") == "border":
        out += _render_redistribution(fabric)
    out += _render_vxlan(fabric)
    out += _render_irb(fabric)
    out += _render_bfd(fabric)
    return out
