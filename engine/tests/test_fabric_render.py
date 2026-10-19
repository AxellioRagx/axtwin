"""Renderer tests for the fabric slice (docs/design/04-networking-capture.md).

render_routing(fabric_ctx) is pure: a per-router context dict -> VyOS `set` lines.
"""
from __future__ import annotations

from axrange.adapter_proxmox.fabric_render import render_routing


def _spine() -> dict:
    return {
        "router_os": "vyos", "region": "dc", "igp": "isis", "role": "spine",
        "loopback": "10.255.0.1/32", "router_id": "10.255.0.1",
        "isis_net": "49.0001.0000.0000.0001.00",
        "interfaces": [
            {"eth": "eth1", "net": "s1l1", "p2p": True, "ospf_area": None, "vni": None},
            {"eth": "eth2", "net": "s1l2", "p2p": True, "ospf_area": None, "vni": None},
        ],
        "bgp": {
            "asn": 65000, "rr_server": True,
            "rr_client_loopbacks": ["10.255.0.3", "10.255.0.4"],
            "rr_spine_loopbacks": [], "evpn": True, "vtep": False,
            "ebgp_peers": [], "originate": [],
        },
        "redistribute": [], "bfd": False,
    }


def _leaf() -> dict:
    return {
        "router_os": "vyos", "region": "dc", "igp": "isis", "role": "leaf",
        "loopback": "10.255.0.3/32", "router_id": "10.255.0.3",
        "isis_net": "49.0001.0000.0000.0003.00",
        "interfaces": [
            {"eth": "eth1", "net": "s1l1", "p2p": True, "ospf_area": None, "vni": None},
            {"eth": "eth2", "net": "app-l2", "p2p": False, "ospf_area": None, "vni": 11010},
        ],
        "bgp": {
            "asn": 65000, "rr_server": False, "rr_client_loopbacks": [],
            "rr_spine_loopbacks": ["10.255.0.1", "10.255.0.2"],
            "evpn": True, "vtep": True, "ebgp_peers": [], "originate": [],
        },
        "redistribute": [], "bfd": False,
    }


def _border() -> dict:
    return {
        "router_os": "vyos", "region": "edge", "igp": "both", "role": "border",
        "loopback": "10.255.0.9/32", "router_id": "10.255.0.9",
        "isis_net": "49.0001.0000.0000.0009.00",
        "interfaces": [
            {"eth": "eth1", "net": "l2b", "p2p": True, "ospf_area": None, "vni": None},
            {"eth": "eth2", "net": "campus0", "p2p": True, "ospf_area": 0, "vni": None},
            {"eth": "eth3", "net": "bext", "p2p": True, "ospf_area": None, "vni": None},
        ],
        "bgp": {
            "asn": 65000, "rr_server": False, "rr_client_loopbacks": [],
            "rr_spine_loopbacks": [], "evpn": False, "vtep": False,
            "ebgp_peers": [{"neighbor": "10.30.1.2", "remote_asn": 65001}],
            "originate": ["10.255.0.0/24"],
        },
        "redistribute": [], "bfd": False,
    }


def _provider() -> dict:
    return {
        "router_os": "vyos", "region": "wan", "igp": "none", "role": "provider",
        "loopback": "100.64.0.1/32", "router_id": "100.64.0.1", "isis_net": None,
        "interfaces": [
            {"eth": "eth1", "net": "bext", "p2p": True, "ospf_area": None, "vni": None},
        ],
        "bgp": {
            "asn": 65001, "rr_server": False, "rr_client_loopbacks": [],
            "rr_spine_loopbacks": [], "evpn": False, "vtep": False,
            "ebgp_peers": [{"neighbor": "10.30.1.1", "remote_asn": 65000}],
            "originate": ["100.64.1.0/24"],
        },
        "redistribute": [], "bfd": False,
    }


def test_spine_isis_and_rr_server():
    s = set(render_routing(_spine()))
    assert "set interfaces dummy dum0 address '10.255.0.1/32'" in s
    assert "set protocols isis net 49.0001.0000.0000.0001.00" in s
    assert "set protocols isis level level-2" in s
    assert "set protocols isis interface dum0 passive" in s
    assert "set protocols isis interface eth1 network point-to-point" in s
    assert "set protocols isis interface eth2 circuit-type level-2-only" in s
    assert "set protocols bgp system-as 65000" in s
    assert "set protocols bgp peer-group LEAVES remote-as 65000" in s
    assert "set protocols bgp peer-group LEAVES update-source dum0" in s
    assert "set protocols bgp peer-group LEAVES address-family l2vpn-evpn route-reflector-client" in s
    assert "set protocols bgp neighbor 10.255.0.3 peer-group LEAVES" in s
    assert "set protocols bgp neighbor 10.255.0.4 peer-group LEAVES" in s
    # a spine is not an RR client and runs no OSPF / redistribution
    assert not any("peer-group SPINES" in x for x in s)
    assert not any("protocols ospf" in x for x in s)
    assert not any("axr-isis-to-ospf" in x for x in s)


def test_leaf_rr_client():
    s = set(render_routing(_leaf()))
    assert "set protocols bgp peer-group SPINES remote-as 65000" in s
    assert "set protocols bgp peer-group SPINES address-family l2vpn-evpn" in s
    assert "set protocols bgp neighbor 10.255.0.1 peer-group SPINES" in s
    assert "set protocols bgp neighbor 10.255.0.2 peer-group SPINES" in s
    # IS-IS only on the p2p uplink (eth1), not the access leg (eth2)
    assert "set protocols isis interface eth1 network point-to-point" in s
    assert not any("isis interface eth2" in x for x in s)


def test_border_redistribution_and_multiarea():
    s = set(render_routing(_border()))
    # both IGPs present
    assert "set protocols isis net 49.0001.0000.0000.0009.00" in s
    assert "set protocols ospf interface eth2 area 0" in s
    assert "set protocols ospf interface eth2 network point-to-point" in s
    # eBGP + advertise the fabric
    assert "set protocols bgp neighbor 10.30.1.2 remote-as 65001" in s
    assert "set protocols bgp address-family ipv4-unicast network 10.255.0.0/24" in s
    assert "set protocols bgp address-family ipv4-unicast redistribute isis" in s
    # mutual redistribution route-maps with tag loop-prevention
    assert "set policy route-map axr-isis-to-ospf rule 10 set tag 9999" in s
    assert "set policy route-map axr-bgp-to-igp rule 10 set tag 9998" in s
    assert "set protocols ospf redistribute isis route-map axr-isis-to-ospf" in s
    assert "set protocols isis redistribute ipv4 ospf level-2 route-map axr-ospf-to-isis" in s


def test_provider_ebgp_only():
    s = set(render_routing(_provider()))
    assert "set protocols bgp system-as 65001" in s
    assert "set protocols bgp neighbor 10.30.1.1 remote-as 65000" in s
    assert "set protocols bgp address-family ipv4-unicast network 100.64.1.0/24" in s
    assert "set interfaces dummy dum0 address '100.64.0.1/32'" in s
    # igp none, role provider -> no IGP, no redistribution, no RR, no VXLAN
    assert not any("protocols isis" in x for x in s)
    assert not any("protocols ospf" in x for x in s)
    assert not any("peer-group" in x for x in s)
    assert not any("axr-" in x for x in s)
    assert not any("interfaces vxlan" in x for x in s)


def test_leaf_vxlan_dataplane():
    """P-d: a VTEP leaf bridges each `vni` access leg + advertises all VNIs."""
    s = set(render_routing(_leaf()))
    assert "set interfaces vxlan vxlan11010 source-address 10.255.0.3" in s
    assert "set interfaces vxlan vxlan11010 vni 11010" in s
    assert "set interfaces bridge br11010 member interface eth2" in s
    assert "set interfaces bridge br11010 member interface vxlan11010" in s
    assert "set protocols bgp address-family l2vpn-evpn advertise-all-vni" in s


def test_spine_is_not_a_vtep():
    # spines relay EVPN but are not VTEPs -> no VXLAN interfaces
    s = set(render_routing(_spine()))
    assert not any("interfaces vxlan" in x for x in s)


def _bfd_spine() -> dict:
    f = _spine()
    f["bfd"] = True
    f["interfaces"] = [
        {"eth": "eth1", "net": "s1l1", "p2p": True, "ospf_area": None, "vni": None, "peer_ip": "10.0.1.2"},
        {"eth": "eth2", "net": "s1l2", "p2p": True, "ospf_area": None, "vni": None, "peer_ip": "10.0.1.6"},
    ]
    return f


def test_bfd_on_isis_neighbors():
    """P-e: bfd:true attaches BFD to each p2p IGP neighbor."""
    s = set(render_routing(_bfd_spine()))
    assert "set protocols bfd peer 10.0.1.2 interval multiplier 3" in s
    assert "set protocols bfd peer 10.0.1.6 interval transmit 300" in s
    assert "set protocols isis interface eth1 bfd" in s
    assert "set protocols isis interface eth2 bfd" in s


def test_bfd_off_by_default():
    s = set(render_routing(_spine()))
    assert not any("protocols bfd" in x for x in s)


def _ospf_border() -> dict:
    return {
        "router_os": "vyos", "region": "campus", "igp": "ospf", "role": "border",
        "loopback": "10.255.0.20/32", "router_id": "10.255.0.20", "isis_net": None,
        "interfaces": [
            {"eth": "eth1", "net": "c-a1", "p2p": True, "ospf_area": 0, "vni": None, "peer_ip": "10.30.1.2"},
            {"eth": "eth2", "net": "wan", "p2p": True, "ospf_area": None, "vni": None, "peer_ip": "10.255.255.1"},
        ],
        "bgp": {
            "asn": 65030, "rr_server": False, "rr_client_loopbacks": [],
            "rr_spine_loopbacks": [], "evpn": False, "vtep": False,
            "ebgp_peers": [{"neighbor": "10.255.255.1", "remote_asn": 65010}],
            "originate": [],
        },
        "redistribute": [], "bfd": False,
    }


def test_ospf_only_border_emits_no_isis():
    """IGP-aware redistribution: an OSPF-only campus border must not emit IS-IS."""
    s = set(render_routing(_ospf_border()))
    assert "set protocols ospf redistribute bgp route-map axr-bgp-to-igp" in s
    assert "set protocols bgp address-family ipv4-unicast redistribute ospf" in s
    assert not any("protocols isis" in x for x in s)
    assert not any("redistribute isis" in x for x in s)
    assert not any("axr-isis-to-ospf" in x for x in s)


def _irb_leaf() -> dict:
    f = _leaf()
    f["tenant"] = {"name": "OPTRA", "l3vni": 10999, "table": 1000}
    f["interfaces"] = [
        {"eth": "eth1", "net": "s1l1", "p2p": True, "ospf_area": None, "vni": None,
         "gateway": None, "prefix": "30", "peer_ip": "10.0.1.1"},
        {"eth": "eth2", "net": "app-l2", "p2p": False, "ospf_area": None, "vni": 11010,
         "gateway": "10.10.10.254", "prefix": "24", "peer_ip": None},
    ]
    return f


def test_irb_symmetric_gateway():
    """L3VNI/IRB: VTEP leaf gets a tenant VRF + L3VNI + anycast SVI + per-VRF Type-5."""
    s = set(render_routing(_irb_leaf()))
    assert "set vrf name OPTRA table 1000" in s
    assert "set vrf name OPTRA vni 10999" in s
    assert "set interfaces vxlan vxlan10999 vni 10999" in s
    assert "set interfaces bridge br10999 vrf OPTRA" in s
    # GAP-003 (dev45 live-verify): UNIQUE per-leaf L3VNI Router-MAC keyed off the loopback
    # (10.255.0.3) so peers don't reject the Type-5 as "self mac". 02:<vni>:<lo[1:4]>.
    assert "set interfaces bridge br10999 mac 02:2a:f7:ff:00:03" in s
    # anycast distributed gateway on the L2VNI bridge (SHARED anycast MAC, distinct from the RMAC)
    assert "set interfaces bridge br11010 address 10.10.10.254/24" in s
    assert "set interfaces bridge br11010 vrf OPTRA" in s
    assert any(x.startswith("set interfaces bridge br11010 mac 02:00:") for x in s)
    # per-VRF EVPN Type-5 advertisement of the SVI subnets
    assert "set vrf name OPTRA protocols bgp address-family l2vpn-evpn advertise ipv4 unicast" in s
    # Finding D (dev45 live-verify): explicit RD (per-leaf) + shared RT so peers IMPORT
    # the Type-5 into the VRF. The VyOS-valid form is `route-target both` (the `vpn`
    # token is rejected by VyOS 1.5); it maps to FRR import/export. _irb_leaf:
    # router_id 10.255.0.3, asn 65010, l3vni 10999.
    assert "set vrf name OPTRA protocols bgp address-family l2vpn-evpn rd 10.255.0.3:10999" in s
    assert "set vrf name OPTRA protocols bgp address-family l2vpn-evpn route-target both 65000:10999" in s
    # the INVALID `vpn both` form must never be emitted
    assert not any("route-target vpn both" in x for x in s)


def test_irb_absent_without_tenant():
    s = set(render_routing(_leaf()))
    assert not any("set vrf name" in x for x in s)


def test_l3vni_rmac_unique_per_leaf():
    """GAP-003: the L3VNI Router-MAC must be UNIQUE per leaf (keyed off the loopback),
    or peers reject the Type-5 as "self mac". Distinct from the SHARED anycast MAC."""
    from axrange.adapter_proxmox.fabric_render import _l3vni_rmac, _anycast_mac
    m3 = _l3vni_rmac("10.255.0.3", 10999)
    m4 = _l3vni_rmac("10.255.0.4", 10999)
    assert m3 == "02:2a:f7:ff:00:03" and m4 == "02:2a:f7:ff:00:04"
    assert m3 != m4  # different leaves -> different RMAC (the whole point)
    # both are locally-administered unicast MACs
    for m in (m3, m4):
        assert int(m.split(":")[0], 16) & 0x03 == 0x02
    # and NOT the shared anycast MAC (which is intentionally identical across leaves)
    assert m3 != _anycast_mac(10999)


def test_irb_no_invalid_egress_leak():
    """Finding C (dev45 live-verify): the next-hop-vrf static leak is invalid VyOS and
    must NOT be emitted — even when wan_gw is present (egress is a follow-on)."""
    f = _irb_leaf()
    f["wan_gw"] = "10.0.0.1"
    s = set(render_routing(f))
    assert not any("next-hop-vrf" in x for x in s)
    assert not any("static route 0.0.0.0/0" in x for x in s)
