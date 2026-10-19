"""FAB-* rule tests (fabric slice, docs/design/04-networking-capture.md)."""
from __future__ import annotations

import textwrap

from axrange.validator.engine import validate_full


def _ids(result) -> set[str]:
    return {f.rule_id for f in result.findings}


def _base() -> str:
    """A minimal VALID spine-leaf fabric (1 spine + 1 leaf + an EVPN access net)."""
    return textwrap.dedent("""
        scenario_schema_version: 1
        scenario: { id: fab, version: 1 }
        fabric:
          regions:
            - { id: dc, igp: isis }
        layers:
          infra:
            networks:
              - { id: s1l1, cidr: 10.0.1.0/30, vlan: 11 }
              - { id: app-l2, cidr: 10.10.10.0/24, vlan: 110, vni: 11010 }
            nodes:
              - id: spine-1
                kind: router
                image: vyos@1.5
                requires: [linux]
                nets: [ { net: s1l1, ip: 10.0.1.1 } ]
                routing:
                  region: dc
                  role: spine
                  loopback: 10.255.0.1/32
                  isis: { net: "49.0001.0000.0000.0001.00" }
                  bgp: { asn: 65000, rr_server: true, evpn: true }
              - id: leaf-1
                kind: router
                image: vyos@1.5
                requires: [linux]
                nets: [ { net: s1l1, ip: 10.0.1.2 }, { net: app-l2, ip: 10.10.10.1 } ]
                routing:
                  region: dc
                  role: leaf
                  loopback: 10.255.0.3/32
                  isis: { net: "49.0001.0000.0000.0003.00" }
                  bgp: { asn: 65000, rr_clients_of: [spine-1], evpn: true, vtep: true }
              - id: app-host
                kind: host
                net: app-l2
                image: ubuntu-server-22@v5
                requires: [linux]
          apps: []
        bindings: []
    """).strip()


def test_fabric_base_is_clean(manifests):
    result = validate_full(text=_base(), manifests=manifests)
    fab = {i for i in _ids(result) if i.startswith("FAB")}
    assert not fab, f"valid fabric should raise no FAB findings, got {fab}"
    assert result.disposition.value == "PASS"


def test_fab_010_routing_on_host(manifests):
    src = _base().replace(
        "        net: app-l2\n        image: ubuntu-server-22@v5\n        requires: [linux]",
        "        net: app-l2\n        image: ubuntu-server-22@v5\n        requires: [linux]\n        routing: { region: dc, role: leaf }",
    )
    assert "FAB-010" in _ids(validate_full(text=src, manifests=manifests))


def test_fab_020_bad_region(manifests):
    src = _base().replace("region: dc\n          role: leaf", "region: nope\n          role: leaf")
    assert "FAB-020" in _ids(validate_full(text=src, manifests=manifests))


def test_fab_030_bad_role(manifests):
    src = _base().replace("role: leaf\n          loopback: 10.255.0.3/32",
                          "role: wizard\n          loopback: 10.255.0.3/32")
    assert "FAB-030" in _ids(validate_full(text=src, manifests=manifests))


def test_fab_040_bad_rr_client(manifests):
    src = _base().replace("rr_clients_of: [spine-1]", "rr_clients_of: [ghost]")
    assert "FAB-040" in _ids(validate_full(text=src, manifests=manifests))


def test_fab_050_isis_region_without_net(manifests):
    src = _base().replace('\n          isis: { net: "49.0001.0000.0000.0003.00" }', "")
    assert "FAB-050" in _ids(validate_full(text=src, manifests=manifests))


def test_fab_060_bad_vni(manifests):
    src = _base().replace("vni: 11010", "vni: 99999999")
    assert "FAB-060" in _ids(validate_full(text=src, manifests=manifests))


def test_fab_070_bgp_without_loopback(manifests):
    src = _base().replace("\n          loopback: 10.255.0.3/32", "")
    assert "FAB-070" in _ids(validate_full(text=src, manifests=manifests))


def test_fab_080_non_vyos_fabric_router(manifests):
    src = _base().replace(
        "image: vyos@1.5\n        requires: [linux]\n        nets: [ { net: s1l1, ip: 10.0.1.2 }",
        "image: debian-12-cloud@v1\n        requires: [linux]\n        nets: [ { net: s1l1, ip: 10.0.1.2 }",
    )
    assert "FAB-080" in _ids(validate_full(text=src, manifests=manifests))
