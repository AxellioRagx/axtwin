"""Native switches (`docs/design/native-switches.md`, D1-D12 and §12 S1-S3
signed off by the user, 2026-11-12): the twin-config `stp` field and member
link ends, the switch resolution model, the NSW validator rules, op emission,
the adapter ops (mocked), the inventory's `switches[]`.
"""
from __future__ import annotations

import copy

import pytest

from axrange.twinconfig.errors import TwinConfigShapeError
from axrange.twinconfig.expand import expand_twin_config_raw


def _cfg(**overrides) -> dict:
    """switch-test's shape: a native access switch carrying office, a group
    of two workstations wired to it by instance id, a file server on an
    SDN-hosted servers segment, one router trunked to the switch."""
    base = {
        "twin_config": {
            "id": "unit-nsw",
            "segments": [
                {"name": "office", "cidr": "10.71.10.0/24", "zone_type": "vlan"},
                {"name": "servers", "cidr": "10.71.20.0/24", "zone_type": "vlan"},
            ],
            "nodes": [
                {"name": "ws", "role": "corp-workstation", "image": "debian-12-cloud@v1",
                 "requires": ["linux"], "count": 2, "net": "office"},
                {"name": "fs", "role": "file-server", "image": "debian-12-cloud@v1",
                 "requires": ["linux"], "nets": [{"segment": "servers", "ip": "10.71.20.10"}]},
                {"name": "sw", "kind": "switch", "role": "l2-switch"},
                {"name": "gw", "kind": "router", "image": "debian-12-cloud@v1", "requires": ["linux"],
                 "nets": [{"segment": "office"}, {"segment": "servers"}]},
            ],
            "links": [
                {"a": {"device": "gw", "port": "eth1"}, "b": {"device": "sw", "port": "SFP1"},
                 "mode": "trunk", "native": "office", "vlans": ["office"]},
                {"a": {"device": "sw", "port": "P1"}, "b": {"device": "ws01", "port": "eth0"},
                 "mode": "access", "vlans": ["office"]},
                {"a": {"device": "sw", "port": "P2"}, "b": {"device": "ws02", "port": "eth0"},
                 "mode": "access", "vlans": ["office"]},
            ],
        }
    }
    base["twin_config"].update(copy.deepcopy(overrides))
    return base


def _nodes(ir: dict) -> dict[str, dict]:
    return {n["id"]: n for n in ir["layers"]["infra"]["nodes"]}


# ---------------------------------------------------------------------------
# Twin config: member link ends (S1 a) and `stp`
# ---------------------------------------------------------------------------
def test_a_link_may_name_a_group_member_by_instance_id():
    ir, _ = expand_twin_config_raw(_cfg())
    links = ir["layers"]["infra"]["links"]
    assert [l["b"]["device"] for l in links[1:]] == ["ws01", "ws02"]
    nodes = _nodes(ir)
    # the members keep their single office leg (the link names the segment
    # they already declare) and their allocated addresses
    assert [n["net"] for n in nodes["ws01"]["nets"]] == ["office"]
    assert nodes["ws01"]["nets"][0]["ip"] != nodes["ws02"]["nets"][0]["ip"]


def test_a_link_to_the_group_name_itself_is_still_refused_and_names_a_member():
    cfg = _cfg()
    cfg["twin_config"]["links"][1]["b"]["device"] = "ws"
    with pytest.raises(TwinConfigShapeError, match=r"count > 1 group.*ws01"):
        expand_twin_config_raw(cfg)


def test_a_member_link_carrying_a_new_segment_adds_a_leg_to_that_member_only():
    cfg = _cfg()
    cfg["twin_config"]["links"][1]["vlans"] = ["servers"]
    ir, _ = expand_twin_config_raw(cfg)
    nodes = _nodes(ir)
    assert [n["net"] for n in nodes["ws01"]["nets"]] == ["office", "servers"]
    assert [n["net"] for n in nodes["ws02"]["nets"]] == ["office"]


def test_a_link_naming_a_member_beyond_the_count_is_unknown():
    cfg = _cfg()
    cfg["twin_config"]["links"][1]["b"]["device"] = "ws03"
    with pytest.raises(TwinConfigShapeError, match="not an emitted node"):
        expand_twin_config_raw(cfg)


def test_stp_reaches_the_ir_and_declares_schema_5():
    cfg = _cfg()
    cfg["twin_config"]["nodes"][2]["stp"] = "rstp"
    ir, _ = expand_twin_config_raw(cfg)
    assert _nodes(ir)["sw"]["stp"] == "rstp"
    assert ir["scenario_schema_version"] == 5


def test_no_stp_keeps_schema_4_and_no_key():
    ir, _ = expand_twin_config_raw(_cfg())
    assert "stp" not in _nodes(ir)["sw"]
    assert ir["scenario_schema_version"] == 4


@pytest.mark.parametrize("node_index, stp, needle", [
    (0, "rstp", "a `kind: switch` field"),
    (2, "mstp", "not one of off, rstp"),
])
def test_stp_shape_errors(node_index, stp, needle):
    cfg = _cfg()
    cfg["twin_config"]["nodes"][node_index]["stp"] = stp
    with pytest.raises(TwinConfigShapeError, match=needle):
        expand_twin_config_raw(cfg)


# ---------------------------------------------------------------------------
# The switch model (axrange.switchmodel)
# ---------------------------------------------------------------------------
import tempfile
from pathlib import Path

from ruamel.yaml import YAML

from axrange.ir.parser import parse_ir
from axrange.switchmodel import NATIVE_SWITCH_CAP, resolve_switches

CAPS = frozenset({NATIVE_SWITCH_CAP})


def _ir(cfg: dict):
    ir_dict, _ = expand_twin_config_raw(cfg)
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "ir.yaml"
        with p.open("w") as f:
            YAML(typ="safe").dump(ir_dict, f)
        return parse_ir(path=str(p))


def _model(cfg: dict, caps=CAPS):
    return resolve_switches(_ir(cfg).ir, caps)


def _rules(model) -> list[str]:
    return [p.rule_id for p in model.problems]


def _two_switch_cfg() -> dict:
    """core <-> access over a trunk; office hosts on access, servers on core."""
    cfg = _cfg()
    tc = cfg["twin_config"]
    tc["nodes"].append({"name": "core", "kind": "switch", "role": "l3-switch"})
    tc["links"] = [
        {"a": {"device": "gw", "port": "eth1"}, "b": {"device": "core", "port": "Te1"},
         "mode": "trunk", "native": "office", "vlans": ["office"]},
        {"a": {"device": "gw", "port": "eth2"}, "b": {"device": "core", "port": "Te2"},
         "mode": "access", "vlans": ["servers"]},
        {"a": {"device": "core", "port": "Po1"}, "b": {"device": "sw", "port": "Po1"},
         "mode": "trunk", "vlans": ["office", "servers"]},
    ]
    return cfg


def test_resolution_follows_image_and_capability():
    cfg = _cfg()
    cfg["twin_config"]["nodes"].append(
        {"name": "vsw", "kind": "switch", "image": "debian-12-cloud@v1", "requires": ["linux"], "net": "servers"})
    m = _model(cfg)
    assert m.resolution == {"sw": "native", "vsw": "vm"}
    assert m.native == ["sw"] and m.has_native
    logical = _model(cfg, caps=frozenset())
    assert logical.resolution["sw"] == "logical" and not logical.has_native
    assert "NSW-020" in _rules(logical)
    # unknown platform (bare validate): logical, and no NSW-020 noise
    assert "NSW-020" not in _rules(_model(cfg, caps=None))


def test_carriers_tags_and_explicit_member_ports():
    m = _model(_cfg())
    assert m.carriers == {"office": ["sw"]}          # servers stays SDN-hosted
    assert m.is_switch_hosted("office") and not m.is_switch_hosted("servers")
    assert m.vlan_tag == {"office": 10}  # the expander's zone_type: vlan allocation
    ports = {p.port: p for p in m.ports["sw"]}
    assert set(ports) == {"SFP1", "P1", "P2"}
    assert ports["P1"].far_device == "ws01" and ports["P1"].far_leg == 0
    assert ports["SFP1"].far_kind == "router" and ports["SFP1"].mode == "trunk"
    assert m.leg_port[("gw", "office")].port == "SFP1"
    assert m.problems == []


def test_a_network_without_a_vlan_gets_the_lowest_free_tag():
    parsed = _ir(_cfg())
    parsed.ir.layers.infra.networks[0].vlan = None  # office: no VLAN in the IR
    parsed.ir.layers.infra.networks[1].vlan = 2     # servers holds 2 (SDN-hosted)
    assert resolve_switches(parsed.ir, CAPS).vlan_tag == {"office": 3}


def test_unlinked_legs_get_auto_ports_on_the_deepest_carrier():
    cfg = _two_switch_cfg()
    m = _model(cfg)
    assert m.depth == {"sw": 2, "core": 1}
    assert m.carriers == {"office": ["sw", "core"], "servers": ["sw", "core"]}
    # ws01/ws02 have no link of their own here: auto ports on access (deeper)
    auto = {p.port: p for p in m.ports["sw"] if p.source == "auto"}
    assert set(auto) == {"ws01.0", "ws02.0", "fs.0"}
    # fs (servers) also lands on the deepest carrier of servers
    assert m.leg_port[("fs", "servers")].switch == "sw"
    assert m.leg_port[("gw", "servers")].port == "Te2"
    assert [l.key for l in m.switch_links] == ["core#Po1|sw#Po1"]
    assert m.problems == []


def test_two_carriers_at_one_depth_are_ambiguous():
    cfg = _two_switch_cfg()
    tc = cfg["twin_config"]
    # make access a sibling of core under gw instead of below it
    tc["links"][2] = {"a": {"device": "gw", "port": "eth3"}, "b": {"device": "sw", "port": "Up"},
                      "mode": "trunk", "vlans": ["office", "servers"]}
    m = _model(cfg)
    assert "NSW-040" in _rules(m)
    assert any("Ambiguous carrier for 'ws01' on 'office'" in p.message for p in m.problems)


def test_a_vm_trunk_beyond_its_native_is_refused():
    cfg = _cfg()
    cfg["twin_config"]["links"][0]["vlans"] = ["office", "servers"]
    assert "NSW-060" in _rules(_model(cfg))


def test_a_link_to_a_logical_switch_is_refused():
    cfg = _cfg()
    tc = cfg["twin_config"]
    tc["nodes"].append({"name": "dumb", "kind": "switch"})
    tc["links"].append({"a": {"device": "sw", "port": "P9"}, "b": {"device": "dumb", "port": "1"},
                        "mode": "access", "vlans": ["office"]})
    # both imageless -> both native: a patch link, fine
    assert _model(cfg).problems == []
    tc["nodes"][-1]["image"] = "debian-12-cloud@v1"
    tc["nodes"][-1]["requires"] = ["linux"]
    m = _model(cfg)  # an imaged switch is a VM: an ordinary VM port
    assert m.port("sw", "P9").far_kind == "switch" and m.problems == []


def test_egress_on_a_switch_hosted_network_is_refused():
    parsed = _ir(_cfg())
    parsed.ir.layers.infra.networks[0].egress = True
    m = resolve_switches(parsed.ir, CAPS)
    assert "NSW-030" in _rules(m)


def test_stp_and_svi_problems():
    cfg = _cfg()
    sw = cfg["twin_config"]["nodes"][2]
    sw["stp"] = "rstp"
    sw["nets"] = [{"segment": "office", "ip": "10.71.10.250"}]
    probs = {p.rule_id: p.severity for p in _model(cfg).problems}
    assert probs == {"NSW-070": "error", "NSW-080": "warning"}
    probs = {p.rule_id: p.severity for p in _model(cfg, caps=frozenset()).problems}
    assert probs["NSW-070"] == "warning" and "NSW-080" not in probs


def test_a_down_member_port_keeps_its_nic_and_state():
    cfg = _cfg()
    cfg["twin_config"]["links"][1]["state"] = "down"
    p = _model(cfg).port("sw", "P1")
    assert p.state == "down" and p.far_leg == 0  # ws01 keeps `net: office`


def test_no_native_switch_gives_an_empty_model():
    cfg = _cfg()
    cfg["twin_config"]["nodes"][2]["image"] = "debian-12-cloud@v1"
    cfg["twin_config"]["nodes"][2]["requires"] = ["linux"]
    m = _model(cfg)
    assert not m.has_native and m.carriers == {} and m.ports == {}


# ---------------------------------------------------------------------------
# Validator (NSW)
# ---------------------------------------------------------------------------
from axrange.validator.engine import validate_full


def _validate(cfg: dict, caps):
    from axrange.manifest.loader import load_registry
    manifests = load_registry(root=Path(__file__).parent / "fixtures")
    return validate_full(_ir(cfg), manifests=manifests, platform_caps=caps)


def test_nsw_rules_fire_only_with_a_known_platform():
    cfg = _cfg()
    cfg["twin_config"]["links"][0]["vlans"] = ["office", "servers"]
    assert not [f for f in _validate(cfg, None).findings if f.rule_id.startswith("NSW")]
    ids = [f.rule_id for f in _validate(cfg, CAPS).findings]
    assert "NSW-060" in ids


def test_nsw_070_warning_half_is_a_warning_finding():
    cfg = _cfg()
    cfg["twin_config"]["nodes"][2]["stp"] = "rstp"
    f = [f for f in _validate(cfg, frozenset()).findings if f.rule_id == "NSW-070"]
    assert f and f[0].severity.value == "warning"


# ---------------------------------------------------------------------------
# Op emission
# ---------------------------------------------------------------------------
from axrange.planner import build_plan
from axrange.planner.cluster import ClusterContext


def _plan(cfg: dict, caps=CAPS):
    from axrange.manifest.loader import load_registry
    manifests = load_registry(root=Path(__file__).parent / "fixtures")
    parsed = _ir(cfg)
    ctx = ClusterContext.single_node("px1", platform_caps=caps)
    return build_plan(parsed.ir, manifests, ctx, raw_ir=parsed.raw)


def _ops(plan, name):
    return {op.op_id.split(":", 1)[1]: op for op in plan.ops.values() if op.op_name == name}


def test_native_switch_emits_switch_ops_and_moves_office_off_sdn():
    plan = _plan(_cfg())
    assert set(_ops(plan, "create-switch")) == {"create-switch[sw]"}
    vnets = _ops(plan, "create-vnet")
    assert set(vnets) == {"create-vnet[servers]"}       # office is switch-hosted
    attach = _ops(plan, "attach-vm-to-switch-port")
    assert set(attach) == {
        "attach-vm-to-switch-port[ws01@office]", "attach-vm-to-switch-port[ws02@office]",
        "attach-vm-to-switch-port[gw@office]",
    }
    ws01 = attach["attach-vm-to-switch-port[ws01@office]"]
    assert ws01.inputs_resolved["port"] == "P1" and ws01.inputs_resolved["vlan_tag"] == 10
    assert ws01.inputs_resolved["link_down"] is False
    assert ws01.inputs_from_outputs["bridge_name"][0].endswith("create-switch[sw]")
    # the vnet attach remains for SDN-hosted legs only
    assert set(_ops(plan, "attach-vm-to-vnet")) == {
        "attach-vm-to-vnet[fs@servers]", "attach-vm-to-vnet[gw@servers]", "attach-vm-to-vnet[gw@__wan__]",
    }
    assert "network-ready[office]" not in {b.split(":", 1)[1] for b in plan.barriers}


def test_without_the_capability_the_switch_is_logical_and_the_plan_is_sdn_only():
    plan = _plan(_cfg(), caps=frozenset())
    assert not _ops(plan, "create-switch") and not _ops(plan, "attach-vm-to-switch-port")
    assert set(_ops(plan, "create-vnet")) == {"create-vnet[office]", "create-vnet[servers]"}


def test_all_networks_switch_hosted_emits_no_sdn_zone():
    cfg = _cfg()
    tc = cfg["twin_config"]
    tc["links"].append({"a": {"device": "sw", "port": "P3"}, "b": {"device": "fs", "port": "eth0"},
                        "mode": "access", "vlans": ["servers"]})
    tc["links"][0]["vlans"] = ["office"]
    tc["links"].append({"a": {"device": "gw", "port": "eth2"}, "b": {"device": "sw", "port": "P4"},
                        "mode": "access", "vlans": ["servers"]})
    plan = _plan(cfg)
    for name in ("create-sdn-zone", "apply-sdn-config", "create-vnet", "create-ovs-bridge"):
        assert not _ops(plan, name), name
    assert "attach-vm-to-switch-port[fs@servers]" in _ops(plan, "attach-vm-to-switch-port")


def test_two_switch_trunk_emits_a_patch_link_ordered_before_every_port():
    plan = _plan(_two_switch_cfg())
    links = _ops(plan, "link-switches")
    assert set(links) == {"link-switches[core#Po1|sw#Po1]"}
    lk = links["link-switches[core#Po1|sw#Po1]"]
    assert lk.inputs_resolved["mode"] == "trunk" and lk.inputs_resolved["vlan_tags"] == [10, 20]
    assert lk.inputs_resolved["state"] == "up"
    for op in _ops(plan, "attach-vm-to-switch-port").values():
        assert lk.op_id in plan.predecessors(op.op_id)


def test_blocked_vm_port_emits_set_port_state_after_start_vm():
    cfg = _cfg()
    cfg["twin_config"]["links"][2]["state"] = "blocked"
    plan = _plan(cfg)
    sps = _ops(plan, "set-port-state")
    assert set(sps) == {"set-port-state[P2@sw]"}
    start = _ops(plan, "start-vm")["start-vm[ws02]"]
    assert start.op_id in plan.predecessors(sps["set-port-state[P2@sw]"].op_id)


def test_down_vm_port_is_link_down_on_the_attach():
    cfg = _cfg()
    cfg["twin_config"]["links"][1]["state"] = "down"
    plan = _plan(cfg)
    assert _ops(plan, "attach-vm-to-switch-port")["attach-vm-to-switch-port[ws01@office]"] \
        .inputs_resolved["link_down"] is True
    assert not _ops(plan, "set-port-state")


def test_start_vm_waits_on_its_switch_port_attach():
    plan = _plan(_cfg())
    start = _ops(plan, "start-vm")["start-vm[ws01]"]
    att = _ops(plan, "attach-vm-to-switch-port")["attach-vm-to-switch-port[ws01@office]"]
    assert att.op_id in plan.predecessors(start.op_id)


def test_a_native_switch_error_refuses_the_plan():
    from axrange.planner.plan_builder import PlanBuildError
    cfg = _cfg()
    cfg["twin_config"]["links"][0]["vlans"] = ["office", "servers"]
    with pytest.raises(PlanBuildError, match="NSW-060"):
        _plan(cfg)
