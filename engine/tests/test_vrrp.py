"""First-hop redundancy, design D5 (`docs/design/canvas-add-panel.md` §8,
Authority: user, 2026-11-21): a segment's `vrrp:` block expands into the IR's
`networks[].vrrp` with the VIP as the network's gateway (reserved, never a
leg's address), NET-150 checks the group's shape, the planner hands each VyOS
member's `configure-router` a `vrrp` input (absent when there is none, so no
shipped plan changes), and the VyOS renderer writes `high-availability vrrp`."""
from __future__ import annotations

import copy
import io
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from axrange.adapter_proxmox.fabric_render import render_vrrp
from axrange.ir.parser import parse_ir
from axrange.twinconfig.errors import TwinConfigShapeError
from axrange.twinconfig.expand import expand_twin_config_raw

FIXTURES = Path(__file__).parent / "fixtures"

RACK = {
    "twin_config": {
        "id": "vrrp-rack",
        "segments": [
            {"name": "rack1-srv", "cidr": "10.200.0.0/24", "zone_type": "vlan",
             "vrrp": {"vip": "10.200.0.1", "members": ["agg-1", "agg-2"]}},
        ],
        "nodes": [
            {"name": "agg-1", "kind": "router", "role": "l3-switch", "image": "vyos@1.5",
             "nets": [{"segment": "rack1-srv", "ip": "10.200.0.2"}]},
            {"name": "agg-2", "kind": "router", "role": "l3-switch", "image": "vyos@1.5",
             "nets": [{"segment": "rack1-srv"}]},
            {"name": "web", "image": "debian-12-cloud@v1", "count": 2, "net": "rack1-srv"},
        ],
    }
}


def _ir(raw: dict) -> dict:
    ir, _ = expand_twin_config_raw(copy.deepcopy(raw))
    return ir


def _text(ir: dict) -> str:
    buf = io.StringIO()
    YAML(typ="safe").dump(ir, buf)
    return buf.getvalue()


def _net(ir: dict, name: str) -> dict:
    return next(n for n in ir["layers"]["infra"]["networks"] if n["id"] == name)


def _legs(ir: dict, node: str) -> list[dict]:
    return next(n for n in ir["layers"]["infra"]["nodes"] if n["id"] == node)["nets"]


def _validate(ir: dict):
    from axrange.manifest.loader import load_registry
    from axrange.validator.engine import validate_full

    return validate_full(None, text=_text(ir), manifests=load_registry(root=FIXTURES))


def _plan(ir: dict):
    from axrange.manifest.loader import load_registry
    from axrange.planner import build_plan
    from axrange.planner.cluster import ClusterContext

    parsed = parse_ir(text=_text(ir))
    return build_plan(parsed.ir, load_registry(root=FIXTURES), ClusterContext.single_node("px1"), raw_ir=parsed.raw)


# ---- expansion -----------------------------------------------------------------

def test_the_vip_is_the_gateway_and_no_legs_address():
    ir = _ir(RACK)
    net = _net(ir, "rack1-srv")
    assert net["gateway"] == "10.200.0.1"
    assert net["vrrp"] == {"vip": "10.200.0.1", "vrid": 1, "members": ["agg-1", "agg-2"],
                           "priority": {"agg-1": 200, "agg-2": 100}}
    addresses = [leg.get("ip") for n in ir["layers"]["infra"]["nodes"] for leg in n["nets"]]
    assert "10.200.0.1" not in addresses
    # The unpinned member gets a plain address (not the gateway), the pinned one keeps .2.
    assert _legs(ir, "agg-1")[0]["ip"] == "10.200.0.2"
    assert _legs(ir, "agg-2")[0]["ip"] not in (None, "10.200.0.1", "10.200.0.2")


def test_a_twin_without_vrrp_expands_byte_identically():
    raw = copy.deepcopy(RACK)
    del raw["twin_config"]["segments"][0]["vrrp"]
    assert "vrrp" not in _net(_ir(raw), "rack1-srv")


def test_authored_vrid_and_priority_are_kept():
    raw = copy.deepcopy(RACK)
    raw["twin_config"]["segments"][0]["vrrp"].update({"vrid": 7, "priority": {"agg-2": 250}})
    assert _net(_ir(raw), "rack1-srv")["vrrp"]["vrid"] == 7
    assert _net(_ir(raw), "rack1-srv")["vrrp"]["priority"] == {"agg-1": 200, "agg-2": 250}


@pytest.mark.parametrize("change, words", [
    ({"vip": "10.9.0.1"}, "not a host address"),
    ({"vip": "10.200.0.0"}, "not a host address"),
    ({"vip": "nope"}, "not an IP address"),
    ({"vip": "10.200.0.2"}, "a VIP is no node's address"),
    ({"priority": {"web": 9}}, "not members"),
])
def test_expansion_refuses_a_bad_vip(change, words):
    raw = copy.deepcopy(RACK)
    raw["twin_config"]["segments"][0]["vrrp"].update(change)
    with pytest.raises(TwinConfigShapeError, match=words) as e:
        _ir(raw)
    assert e.value.rule_id == "TC-VRRP"


def test_a_gateway_other_than_the_vip_is_refused():
    raw = copy.deepcopy(RACK)
    raw["twin_config"]["segments"][0]["gateway"] = "10.200.0.3"
    with pytest.raises(TwinConfigShapeError, match="the VIP IS the gateway"):
        _ir(raw)


# ---- NET-150 ---------------------------------------------------------------------

def test_net_150_passes_a_well_formed_group():
    assert [f for f in _validate(_ir(RACK)).findings if f.rule_id == "NET-150"] == []


@pytest.mark.parametrize("mutate, words", [
    (lambda tc: tc["segments"][0]["vrrp"].update({"members": ["agg-1"]}), "two or more members"),
    (lambda tc: tc["segments"][0]["vrrp"].update({"members": ["agg-1", "ghost"]}), "'ghost' is not a node"),
    (lambda tc: tc["segments"][0]["vrrp"].update({"members": ["agg-1", "web01"]}), "not a router"),
    (lambda tc: tc["nodes"][1].update({"image": "debian-12-cloud@v1"}), "VyOS only"),
    (lambda tc: tc["segments"][0]["vrrp"].update({"vrid": 300}), "outside 1..255"),
])
def test_net_150_names_each_problem(mutate, words):
    raw = copy.deepcopy(RACK)
    mutate(raw["twin_config"])
    found = [f.message for f in _validate(_ir(raw)).findings if f.rule_id == "NET-150"]
    assert any(words in m for m in found), found


def test_net_150_member_without_a_leg():
    raw = copy.deepcopy(RACK)
    raw["twin_config"]["segments"].append({"name": "other", "cidr": "10.201.0.0/24"})
    raw["twin_config"]["nodes"][1]["nets"] = [{"segment": "other"}]
    found = [f.message for f in _validate(_ir(raw)).findings if f.rule_id == "NET-150"]
    assert any("'agg-2' has no leg on it" in m for m in found), found


# ---- planner and renderer ----------------------------------------------------------

def _router_op(plan, node: str):
    return next(op for op in plan.ops.values() if op.op_name == "configure-router" and node in op.op_id)


def test_each_vyos_member_gets_its_vrrp_group():
    plan = _plan(_ir(RACK))
    for node, prio in (("agg-1", 200), ("agg-2", 100)):
        inp = _router_op(plan, node).inputs_resolved
        assert inp["vrrp"] == [{"eth": "eth1", "group": "axr1", "vrid": 1, "address": "10.200.0.1/24", "priority": prio}]


def test_a_router_in_no_group_gets_no_vrrp_key():
    raw = copy.deepcopy(RACK)
    del raw["twin_config"]["segments"][0]["vrrp"]
    plan = _plan(_ir(raw))
    assert "vrrp" not in _router_op(plan, "agg-1").inputs_resolved


def test_a_debian_member_is_refused_at_plan_time():
    from axrange.planner.plan_builder import PlanBuildError as EmissionError  # the builder wraps pass 1

    raw = copy.deepcopy(RACK)
    raw["twin_config"]["nodes"][1]["image"] = "debian-12-cloud@v1"
    with pytest.raises(EmissionError, match="VRRP is rendered on VyOS only"):
        _plan(_ir(raw))


def test_render_vrrp_writes_vyos_high_availability():
    assert render_vrrp([{"eth": "eth2", "group": "axr3", "vrid": 3, "address": "10.200.1.1/24", "priority": 200}]) == [
        "set high-availability vrrp group axr3 interface 'eth2'",
        "set high-availability vrrp group axr3 vrid '3'",
        "set high-availability vrrp group axr3 address '10.200.1.1/24'",
        "set high-availability vrrp group axr3 priority '200'",
    ]
    assert render_vrrp(None) == []
