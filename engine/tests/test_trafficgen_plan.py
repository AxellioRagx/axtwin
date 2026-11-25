"""trafficgen B3 (`engine/src/axrange/trafficgen/SPEC.md`; T3): the planner on
a fixture of `three-tier-dc-manual` with the deploy gate's profile.

The golden file is the reviewed plan; regenerate it only on purpose:
    .venv/bin/python -c "import tests.test_trafficgen_plan as t; t.regenerate()"
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from axrange.o2control.traffic import load_traffic_profile, parse_traffic_profile
from axrange.trafficgen.plan import TrafficGenPlanError, plan_profile
from axrange.trafficgen.twin import TwinView

FIX = Path(__file__).parent / "fixtures" / "trafficgen" / "three-tier-dc-manual"
GOLDEN = FIX / "expected-plan.json"


def _plan(mutate=None):
    from ruamel.yaml import YAML

    raw = YAML(typ="safe").load((FIX / "tfc" / "default.yaml").read_text())
    if mutate:
        mutate(raw["traffic-profile"])
    return plan_profile(parse_traffic_profile(raw), TwinView.from_te_path(FIX / "te.yaml"))


def regenerate() -> None:
    GOLDEN.write_text(_plan().to_json() + "\n")


def test_the_plan_matches_the_reviewed_golden_file():
    assert _plan().to_json() + "\n" == GOLDEN.read_text()


def test_the_plan_is_deterministic():
    assert _plan().to_json() == _plan().to_json()


def test_the_gate_profile_plans_two_single_pair_generators():
    p = _plan()
    assert [g.host_id for g in p.generators] == ["gen-rack1-lan--rack3-lan", "gen-internet--rack2-lan"]
    g1, g2 = p.generators
    assert (g1.cores, g1.memory_mib) == (2, 2253)
    c, s = g1.legs
    assert (c.index, c.role, c.segment, c.default_gw) == (0, "client", "rack1-lan", "10.200.0.1")
    assert (s.index, s.role, s.segment, s.default_gw) == (1, "server", "rack3-lan", "10.200.2.1")
    assert len(c.addresses) == 20 and s.addresses == ("10.200.2.5",)
    line = g1.document["lines"][0]
    assert line["clients"] == ["10.200.0.11", "10.200.0.30"] and line["servers"] == ["10.200.2.5", "10.200.2.5"]
    assert [(t["app"], t["cps"]) for t in line["templates"]] == [("sql-query", 20.0)]
    web = g2.document["lines"][0]
    assert [(t["app"], t["cps"]) for t in web["templates"]] == [("http-browse", 21.0), ("tls-session", 9.0)]
    assert web["templates"][1]["pcap"]["name"] == "tls-session--capture.pcap"
    assert p.routes == []


def test_leg_addresses_are_free_and_distinct_from_the_instances():
    p = _plan()
    twin = TwinView.from_te_path(FIX / "te.yaml")
    leg_ips = [leg.ip for g in p.generators for leg in g.legs]
    inst_ips = {i.ip for v in p.instances.values() for i in v}
    assert len(set(leg_ips)) == len(leg_ips)
    assert not set(leg_ips) & (inst_ips | twin.real_ips())


def test_templates_are_rendered_to_hex_with_the_peer_name():
    p = _plan()
    http = p.generators[1].document["lines"][0]["templates"][0]["program"]
    req = bytes.fromhex(http[0]["send"]).decode()
    assert req.startswith("GET /index.html HTTP/1.1\r\nHost: rack2-web\r\n")
    assert http[2]["size"] == 8192 and "fill" in http[2]
    sql = p.generators[0].document["lines"][0]["templates"][0]["program"]
    assert sql[5] == {"delay_ms": 20}


def test_namespace_addresses_are_what_each_leg_answers_for():
    d = _plan().generators[1].document
    assert len(d["namespaces"]["0"]) == 200 and len(d["namespaces"]["1"]) == 5
    assert d["trex"]["ports"][0] == {"leg": 0, "ip": "10.250.0.3", "default_gw": "10.250.0.1"}


def test_a_same_segment_line_routes_leg_to_leg():
    def m(b):
        b["emulated"].append({"group": "rack1-db", "count": 2})
        b["lines"] = [{"from": "rack1-web", "to": "rack1-db", "mix": "sql-oltp", "rate_cps": 5}]
    g = _plan(m).generators[0]
    c, s = g.legs
    assert g.host_id == "gen-rack1-lan--rack1-lan"
    assert (c.default_gw, s.default_gw) == (s.ip, c.ip)


def test_lines_sharing_a_segment_pair_share_a_generator():
    def m(b):
        b["emulated"].append({"group": "rack3-web", "count": 2})
        b["lines"].append({"from": "rack1-web", "to": "rack3-web", "mix": "web-browse", "rate_cps": 5})
    p = _plan(m)
    assert len(p.generators) == 2
    assert p.generators[0].lines == ("rack1-web--rack3-db--sql-oltp", "rack1-web--rack3-web--web-browse")
    assert len(p.generators[0].legs[1].addresses) == 3


def test_a_group_on_two_generators_is_refused():
    def m(b):
        b["lines"].append({"from": "rack1-web", "to": "rack2-web", "mix": "web-browse", "rate_cps": 5})
    with pytest.raises(TrafficGenPlanError, match="two generators"):
        _plan(m)


def test_a_profile_without_lines_plans_nothing():
    spec = parse_traffic_profile({"traffic-profile": {"assignments": [{"role": "web-tier", "persona": "web-tier"}]}})
    p = plan_profile(spec, TwinView.from_te_path(FIX / "te.yaml"))
    assert (p.instances, p.generators, p.routes) == ({}, [], [])
