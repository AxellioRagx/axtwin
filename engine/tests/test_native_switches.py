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


# ---------------------------------------------------------------------------
# Adapter (mocked): names, OVS commands, REST attach, teardown
# ---------------------------------------------------------------------------
from types import SimpleNamespace

from axrange.adapter_proxmox import naming
from axrange.adapter_proxmox.switches import SwitchOps, vlan_port_args
from axrange.planner.executor import AdapterError


def test_new_names_fit_ifnamsiz_and_match_only_their_own_predicate():
    names = {
        "switch": naming.switch_bridge_for("switch-test-1a2b3c4d", "access-sw"),
        "patch": naming.patch_port_for("switch-test-1a2b3c4d", "core#Po1|sw#Po1", "a"),
        "span": naming.span_mirror_for("switch-test-1a2b3c4d", "sw:binding-0"),
    }
    own = {"switch": naming.is_te_switch_bridge, "patch": naming.is_te_patch_port,
           "span": naming.is_tap_span_mirror}
    preds = {n: getattr(naming, n) for n in dir(naming)
             if n.startswith("is_") and n not in ("is_te", "is_tap", "is_o2", "is_tfc", "is_exr",
                                                   "is_protected_external", "is_te_vmid",
                                                   "is_managed_guest_handle")}
    for kind, name in names.items():
        assert len(name) <= 15, name
        hits = sorted(p for p, fn in preds.items() if fn(name))
        assert hits == [own[kind].__name__], (name, hits)
    assert naming.is_te(names["switch"]) and naming.is_te(names["patch"])
    assert naming.is_tap(names["span"]) and not naming.is_te(names["span"])


class _Run:
    def __init__(self, list_br: str = ""):
        self.cmds: list[str] = []
        self.list_br = list_br

    def __call__(self, cmd, **kw):
        self.cmds.append(cmd)
        if cmd.startswith("ovs-vsctl list-br"):
            return self.list_br
        return ""


def _ctx(plan_id="switch-test-1a2b3c4d"):
    return SimpleNamespace(plan_id=plan_id, cluster=SimpleNamespace(default_node="dev45"))


def _op(name, scope):
    return SimpleNamespace(op_name=name, scope=scope, timeout_s=60)


def test_create_switch_is_check_then_act_and_reasserts():
    run = _Run()
    ops = SwitchOps(run)
    out1 = ops.create_switch(_op("create-switch", {"switch": "access-sw"}),
                             {"switch_id": "access-sw", "stp": "off"}, _ctx())
    out2 = ops.create_switch(_op("create-switch", {"switch": "access-sw"}),
                             {"switch_id": "access-sw", "stp": "off"}, _ctx())
    assert out1 == out2 == {"bridge_name": naming.switch_bridge_for("switch-test-1a2b3c4d", "access-sw")}
    assert run.cmds[0] == run.cmds[1]  # the same idempotent command on re-run
    cmd = run.cmds[0]
    assert "--may-exist add-br tesw_" in cmd and "fail_mode=standalone" in cmd
    assert "external_ids:axrange-tier=te" in cmd and "external_ids:axrange-switch=access-sw" in cmd


def test_create_switch_refuses_rstp():
    with pytest.raises(AdapterError, match="phase 3"):
        SwitchOps(_Run()).create_switch(_op("create-switch", {"switch": "s"}),
                                        {"switch_id": "s", "stp": "rstp"}, _ctx())


def test_link_switches_builds_a_trunk_veth_pair_and_asserts_state():
    run = _Run()
    a = naming.switch_bridge_for("p", "core")
    b = naming.switch_bridge_for("p", "sw")
    out = SwitchOps(run).link_switches(
        _op("link-switches", {"link": "core#Po1|sw#Po1"}),
        {"link_key": "core#Po1|sw#Po1", "bridge_a": a, "bridge_b": b, "mode": "trunk",
         "vlan_tags": [10, 20], "native_tag": None, "state": "down"}, _ctx("p"))
    pa, pb = out["port_a"], out["port_b"]
    assert naming.is_te_patch_port(pa) and naming.is_te_patch_port(pb) and pa != pb
    pair = run.cmds[0]
    # a kernel veth pair, not an OVS patch pair (R6: OVS does not mirror on patch ports)
    assert f"ip link add {pa} type veth peer name {pb}" in pair and "type=patch" not in pair
    assert f'get interface {pa} type 2>/dev/null)" = patch ] && ovs-vsctl del-port {pa}' in pair
    assert f"add-port {a} {pa}" in pair and f"add-port {b} {pb}" in pair
    assert f"ethtool -K {pa} gso off tso off gro off" in pair
    assert f"set port {pa} vlan_mode=trunk trunks=10,20" in pair and f"clear port {pb} tag" in pair
    assert run.cmds[1] == f"ovs-ofctl mod-port {a} {pa} down"
    assert run.cmds[2] == f"ovs-ofctl mod-port {b} {pb} down"


def test_vlan_port_args_native_and_access():
    assert vlan_port_args("x", mode="trunk", vlan_tags=[20], native_tag=10) == \
        " -- set port x vlan_mode=native-untagged tag=10 trunks=10,20"
    assert vlan_port_args("x", mode="access", vlan_tags=[10], native_tag=None) == \
        " -- set port x vlan_mode=access tag=10 -- clear port x trunks"


def test_set_port_state_blocked_on_the_vm_tap():
    run = _Run()
    br = naming.switch_bridge_for("p", "sw")
    out = SwitchOps(run).set_port_state(
        _op("set-port-state", {"switch": "sw", "port": "P2"}),
        {"bridge_name": br, "vmid": 600123, "nic_index": 0, "state": "blocked"}, _ctx("p"))
    assert out == {"state_applied": "blocked", "ovs_port": "tap600123i0"}
    assert run.cmds == [" && ".join(
        f"ovs-ofctl mod-port {br} tap600123i0 {a}" for a in ("up", "no-receive", "no-forward", "no-flood"))]


def test_switch_ops_refuse_a_foreign_bridge_name():
    with pytest.raises(AdapterError, match="non-tesw"):
        SwitchOps(_Run()).set_port_state(
            _op("set-port-state", {}),
            {"bridge_name": "vmbr0", "vmid": 1, "nic_index": 0, "state": "down"}, _ctx())


def test_delete_switch_absent_removed_and_survivor():
    br = naming.switch_bridge_for("p", "sw")
    assert SwitchOps(_Run(list_br="vmbr0\n")).delete_switch(br) == "absent"

    link_port = naming.patch_port_for("p", "a#1|b#1", "a")

    class _Gone(_Run):
        def __call__(self, cmd, **kw):
            self.cmds.append(cmd)
            if cmd.startswith("ovs-vsctl list-br"):
                return f"vmbr0\n{br}\n" if len(self.cmds) == 1 else "vmbr0\n"
            if cmd.startswith("ovs-vsctl list-ports"):
                return f"tap1i0\n{link_port}\n"
            return ""
    gone = _Gone()
    assert SwitchOps(gone).delete_switch(br) == "removed"
    assert gone.cmds[1] == f"ovs-vsctl list-ports {br}"
    assert gone.cmds[2] == f"ip link del {link_port} 2>/dev/null || true"   # the veth, not the VM tap
    assert gone.cmds[3] == f"ovs-vsctl --if-exists clear bridge {br} mirrors -- --if-exists del-br {br}"
    with pytest.raises(AdapterError, match="survived"):
        SwitchOps(_Run(list_br=f"{br}\n")).delete_switch(br)


def test_rest_attach_vm_to_switch_port_nic_string():
    from axrange.adapter_proxmox.rest import RestBackend
    calls = []
    backend = RestBackend.__new__(RestBackend)
    backend.client = SimpleNamespace(request=lambda *a, **kw: calls.append((a, kw)))
    br = naming.switch_bridge_for("p", "sw")
    out = backend.op_attach_vm_to_switch_port(
        _op("attach-vm-to-switch-port", {"host": "ws01", "net": "office"}),
        {"vmid": 600123, "nic_index": 0, "bridge_name": br, "vlan_tag": 10, "link_down": True},
        _ctx("p"))
    assert out == {"nic_attached": True, "ovs_port": "tap600123i0"}
    (method, path), kw = calls[0]
    assert (method, path) == ("PUT", "/nodes/dev45/qemu/600123/config")
    nic = kw["body"]["net0"]
    assert nic.startswith("virtio=") and f",bridge={br},tag=10,firewall=0,link_down=1" in nic


def test_destroy_removes_the_plans_switch_bridges_only(tmp_path):
    import json as _json
    from axrange.planner.state import OpRow, OpStatus, StateStore
    from axrange.planner.teardown import destroy_deploy

    plan_id = "switch-test-1a2b3c4d"
    good = naming.switch_bridge_for(plan_id, "access-sw")
    foreign = naming.switch_bridge_for("other-plan", "access-sw")
    store = StateStore(str(tmp_path / "s.db"))
    store.create_deploy(deploy_id="d1", scenario_id="switch-test", scenario_version=1,
                        scenario_schema_version=4, plan_id=plan_id, ir_hash="h",
                        placement={"access-sw": "dev45"})

    def row(op_id, scope, out):
        return OpRow(op_id=op_id, deploy_id="d1", op_name="create-switch", op_catalog_id="OP-NET-060",
                     kind="action", scope_json=_json.dumps(scope), inputs_resolved_json="{}",
                     inputs_from_outputs_json="{}", outputs_json=_json.dumps(out),
                     cleanup_strategy=None, idempotency_class=None, status=OpStatus.COMPLETED,
                     started_at=1.0, completed_at=2.0)
    store.insert_ops("d1", [row("a", {"switch": "access-sw"}, {"bridge_name": good}),
                            row("b", {"switch": "core"}, {"bridge_name": foreign})])

    calls = []

    class _Backend:
        def __getattr__(self, name):
            def rec(*a):
                calls.append((name, a))
                return "removed"
            return rec
    try:
        result = destroy_deploy(store, _Backend(), "d1")
    finally:
        store.close()
    assert ("delete_switch", ("dev45", good)) in calls
    assert not any(a and a[-1] == foreign for _n, a in calls)
    assert any(x.kind == "switch" and x.status == "guard-violation" for x in result.actions)


# ---------------------------------------------------------------------------
# Inventory: switches[].ports[] and switch legs
# ---------------------------------------------------------------------------
class _FakeAdapter:
    def __init__(self):
        self._vmid = 600100

    def run(self, op, inputs, ctx):
        if op.op_name == "clone-vm":
            self._vmid += 1
            return {"vmid": self._vmid}
        if op.op_name == "create-switch":
            return {"bridge_name": naming.switch_bridge_for(ctx.plan_id, inputs["switch_id"])}
        if op.op_name == "link-switches":
            return {"port_a": naming.patch_port_for(ctx.plan_id, inputs["link_key"], "a"),
                    "port_b": naming.patch_port_for(ctx.plan_id, inputs["link_key"], "b")}
        if op.op_name in ("create-ovs-bridge",):
            return {"bridge_name": "tebr_x"}
        if op.op_name == "create-vnet":
            return {"vnet_id": f"vn-{inputs['net_id']}"[:8]}
        return {}


def _inventory(tmp_path, cfg):
    from axrange.planner import Executor, StateStore
    from axrange.planner.inventory import build_twin_inventory
    plan = _plan(cfg)
    store = StateStore(str(tmp_path / "inv.db"))
    store.create_deploy(deploy_id="d1", scenario_id=plan.scenario_id, scenario_version=1,
                        scenario_schema_version=4, plan_id=plan.plan_id, ir_hash=plan.ir_hash,
                        placement=plan.placement, tier="te")
    adapter = _FakeAdapter()
    ctx = ClusterContext.single_node("px1", platform_caps=CAPS)
    ex = Executor(plan, store, adapter, ctx, config_backend=adapter, deploy_id="d1",
                  sleep_fn=lambda _: None)
    ex.persist_plan()
    assert ex.run().succeeded
    try:
        return build_twin_inventory(store, "d1")
    finally:
        store.close()


def test_inventory_publishes_switch_ports_with_far_ends(tmp_path):
    inv = _inventory(tmp_path, _cfg())
    assert [s.name for s in inv.switches] == ["sw"]
    sw = inv.switches[0]
    assert sw.bridge and naming.is_te_switch_bridge(sw.bridge) and sw.pve_node == "px1"
    ports = {p.port: p for p in sw.ports}
    assert set(ports) == {"SFP1", "P1", "P2"}
    assert ports["P1"].far == {"kind": "host", "device": "ws01", "port": "eth0", "leg": 0}
    assert ports["SFP1"].mode == "trunk" and ports["SFP1"].native == "office"
    hosts = {h.host_id: h for h in inv.hosts}
    office = next(n for n in hosts["ws01"].nets if n.net_id == "office")
    assert (office.switch, office.port, office.nic_index) == ("sw", "P1", 0)
    servers = next(n for n in hosts["fs"].nets if n.net_id == "servers")
    assert servers.switch is None
    # a switch-hosted network has no SDN bridge
    assert "office" not in inv.net_bridge and "servers" in inv.net_bridge
    d = inv.to_dict()
    assert d["switches"][0]["ports"][0]["port"] == "SFP1"


def test_inventory_of_a_twin_without_native_switches_has_none(tmp_path):
    cfg = _cfg()
    cfg["twin_config"]["nodes"][2]["image"] = "debian-12-cloud@v1"
    cfg["twin_config"]["nodes"][2]["requires"] = ["linux"]
    inv = _inventory(tmp_path, cfg)
    assert inv.switches == [] and inv.to_dict()["switches"] == []


# ---------------------------------------------------------------------------
# The shipped fixtures (catalog/twins/switch-test, switch-trunk-test)
# ---------------------------------------------------------------------------
_TWINS = Path(__file__).resolve().parents[2] / "catalog" / "twins"


def _bundle(name: str) -> dict:
    return YAML(typ="safe").load((_TWINS / name / "te.yaml").read_text())


def test_switch_test_fixture_is_one_native_switch_with_member_ports():
    cfg = _bundle("switch-test")
    m = _model(cfg)
    assert m.native == ["access-sw"] and m.problems == []
    assert {p.port: p.far_device for p in m.ports["access-sw"]} == {
        "SFP1": "gw", "P1": "corp-workstation01", "P2": "corp-workstation02"}
    assert set(m.carriers) == {"office"}
    plan = _plan(cfg)
    assert set(_ops(plan, "create-vnet")) == {"create-vnet[servers]"}
    assert len(_ops(plan, "attach-vm-to-switch-port")) == 3
    v = _validate(cfg, CAPS)
    assert not [f for f in v.findings if f.severity.value == "error"], v.findings


def test_switch_trunk_test_fixture_has_no_sdn_and_one_patch_trunk():
    cfg = _bundle("switch-trunk-test")
    m = _model(cfg)
    assert m.native == ["core", "access-sw"] and m.problems == []
    assert m.depth == {"core": 1, "access-sw": 2}
    assert [l.key for l in m.switch_links] == ["access-sw#Po1|core#Po1"]
    plan = _plan(cfg)
    for name in ("create-sdn-zone", "apply-sdn-config", "create-vnet"):
        assert not _ops(plan, name), name
    lk = _ops(plan, "link-switches")["link-switches[access-sw#Po1|core#Po1]"]
    assert lk.inputs_resolved["vlan_tags"] == [10, 20]
    assert {o.inputs_resolved["switch_id"] for o in _ops(plan, "attach-vm-to-switch-port").values()} \
        == {"core", "access-sw"}


# ---------------------------------------------------------------------------
# SPAN (Phase 2): parse, resolve (span bindings + D8), compose, adapter
# ---------------------------------------------------------------------------
from axrange.o2control import spans
from axrange.o2control.taps import TapBindingSpec, TapPlanError, _compose_ops, parse_tap_plan


def test_span_binding_parses_and_normalizes():
    spec = parse_tap_plan({"tap-plan": {"target": "t", "bindings": [
        {"span": {"switch": "sw", "sources": [{"port": "SFP1"}, {"port": "P1", "dir": "rx"},
                                              {"port": "Po1", "dir": "tx", "vlans": ["b", "a"]}]},
         "sensors": ["s1"], "mode": "exclusive"}]}})
    b = spec.bindings[0]
    assert b.span == {"switch": "sw", "sources": [
        {"port": "SFP1", "dir": "both"}, {"port": "P1", "dir": "rx"},
        {"port": "Po1", "dir": "tx", "vlans": ["a", "b"]}]}


@pytest.mark.parametrize("span, needle", [
    ({"switch": "sw"}, "sources must be a non-empty list"),
    ({"switch": "sw", "sources": [{"port": "P1", "dir": "out"}]}, "dir 'out'"),
    ({"switch": "sw", "sources": [{"port": "P1"}, {"port": "P1"}]}, "listed twice"),
    ({"switch": "sw", "sources": [{"port": "P1", "vlan": [1]}]}, "unknown key"),
])
def test_span_shape_errors(span, needle):
    with pytest.raises(TapPlanError, match=needle):
        parse_tap_plan({"tap-plan": {"bindings": [{"span": span, "sensors": ["s"]}]}})


def test_span_and_selector_together_is_refused():
    with pytest.raises(TapPlanError, match="exactly one of"):
        parse_tap_plan({"tap-plan": {"bindings": [
            {"span": {"switch": "sw", "sources": [{"port": "P1"}]},
             "selector": {"scope": "segment"}, "sensors": ["s"]}]}})


def _span(switch, *sources, sensors=("s1",)):
    return TapBindingSpec(span=spans.parse_span({"switch": switch, "sources": list(sources)}, "b"),
                          sensors=list(sensors))


def _ok(_b, _sid):
    return None


def _ovs(inv, switch, port):
    sw = next(s for s in inv.switches if s.name == switch)
    return next(p for p in sw.ports if p.port == port).ovs_port


def test_span_resolves_to_ovs_ports_with_switch_directions(tmp_path):
    inv = _inventory(tmp_path, _cfg())
    sdn, sessions = spans.resolve_sessions(
        [(0, _span("sw", {"port": "SFP1"}, {"port": "P1", "dir": "rx"}, {"port": "P2", "dir": "tx"}))],
        {}, inv, validate_sensor=_ok)
    assert sdn == {}
    (sess,) = sessions
    assert sess.switch == "sw" and sess.vlans == ()
    sfp1, p1, p2 = (_ovs(inv, "sw", p) for p in ("SFP1", "P1", "P2"))
    assert set(sess.src) == {sfp1, p1} and set(sess.dst) == {sfp1, p2}
    assert sfp1.startswith("tap") and p1.endswith("i0")


def test_d8_compiles_switch_hosted_interfaces_and_keeps_sdn_ones(tmp_path):
    inv = _inventory(tmp_path, _cfg())
    ws01 = next(h for h in inv.hosts if h.host_id == "ws01")
    fs = next(h for h in inv.hosts if h.host_id == "fs")
    office = next(n for n in ws01.nets if n.net_id == "office")
    servers = next(n for n in fs.nets if n.net_id == "servers")
    ifaces = {("ws01", "office", office.nic_index): {"s1": "rx"},
              ("fs", "servers", servers.nic_index): {"s1": "rx"}}
    sdn, sessions = spans.resolve_sessions([], ifaces, inv, validate_sensor=_ok)
    assert sdn == {("fs", "servers", servers.nic_index): {"s1": "rx"}}   # tc path, unchanged
    (sess,) = sessions
    assert set(sess.src) == {_ovs(inv, "sw", "P1")} and sess.dst == {}  # rx -> select_src_port
    assert sess.injectors == [("ws01", "office")]


def test_span_on_a_trunk_patch_port_with_vlan_narrowing(tmp_path):
    inv = _inventory(tmp_path, _bundle("switch-trunk-test"))
    po1 = _ovs(inv, "access-sw", "Po1")
    assert naming.is_te_patch_port(po1)
    _, sessions = spans.resolve_sessions(
        [(0, _span("access-sw", {"port": "Po1", "dir": "tx", "vlans": ["servers"]}))],
        {}, inv, validate_sensor=_ok)
    (sess,) = sessions
    assert sess.dst == {po1: "Po1"} and sess.src == {}
    assert sess.vlans == ("servers",) and sess.vlan_tags == (20,)


@pytest.mark.parametrize("binding, needle", [
    (lambda: _span("nope", {"port": "P1"}), "not a native switch"),
    (lambda: _span("sw", {"port": "P9"}), "not on 'sw'"),
    (lambda: _span("sw", {"port": "P1", "vlans": ["servers"]}), "does not carry"),
])
def test_span_resolution_errors(tmp_path, binding, needle):
    inv = _inventory(tmp_path, _cfg())
    with pytest.raises(spans.SpanError, match=needle):
        spans.resolve_sessions([(0, binding())], {}, inv, validate_sensor=_ok)


def test_compose_span_ops_gretap_mirror_and_descope(tmp_path):
    inv = _inventory(tmp_path, _cfg())
    _, sessions = spans.resolve_sessions(
        [(0, _span("sw", {"port": "SFP1"}))], {}, inv, validate_sensor=_ok)
    ext = SimpleNamespace(transport_ip="10.255.251.21", deploy_id="")
    warnings: list[str] = []
    plan = _compose_ops(
        "tap-d1", SimpleNamespace(), {"s1": ext}, {}, None, "px1",
        warnings=warnings, span_sessions=sessions,
        prior_spans=[{"switch": "sw", "sensor": "s1", "mirror_key": "sw|s1|office",
                      "bridge_name": sessions[0].bridge, "gre_port": "tapg_abcdef"}],
    )
    by = {}
    for op in plan.ops.values():
        by.setdefault(op.op_name, []).append(op)
    (gre,) = by["attach-gre-tunnel"]
    assert gre.inputs_resolved["bridge_name"] == sessions[0].bridge
    add, rem = sorted(by["install-span"], key=lambda o: o.inputs_resolved["remove"])
    assert add.inputs_resolved["src_ports"] == add.inputs_resolved["dst_ports"] == [_ovs(inv, "sw", "SFP1")]
    assert add.inputs_resolved["gre_port"] == naming.gre_port_for_bridge_sensor(sessions[0].bridge, "s1")
    assert rem.inputs_resolved["remove"] is True and rem.inputs_resolved["delete_gre"] is False
    assert "verify-capture" not in by
    assert any("UNVERIFIED" in w and "span" in w for w in warnings)


def test_adapter_install_span_replaces_the_named_mirror():
    run = _Run(list_br="")
    br = naming.switch_bridge_for("p", "sw")
    gre = naming.gre_port_for_bridge_sensor(br, "s1")
    out = SwitchOps(run).install_span(
        _op("install-span", {}),
        {"bridge_name": br, "mirror_key": "sw|s1|", "gre_port": gre,
         "src_ports": ["tap1i0", "tap2i0"], "dst_ports": ["tap1i0"], "vlan_tags": [10]},
        _ctx("tap-d1"))
    name = naming.span_mirror_for("tap-d1", "sw|s1|")
    assert out == {"mirror_installed": True, "mirror_name": name}
    assert run.cmds[0].startswith(f"ovs-vsctl --may-exist add-port {br} {gre}")
    assert f"find mirror name={name}" in run.cmds[1]
    create = run.cmds[2]
    assert f"create mirror name={name} output_port=@g" in create
    assert "select_src_port=@p0,@p1" in create and "select_dst_port=@p0" in create
    assert "select_vlan=10" in create and create.endswith(f"-- add bridge {br} mirrors @m")


def test_adapter_install_span_refuses_foreign_ports_and_removes_cleanly():
    br = naming.switch_bridge_for("p", "sw")
    gre = naming.gre_port_for_bridge_sensor(br, "s1")
    with pytest.raises(AdapterError, match="refusing to mirror"):
        SwitchOps(_Run()).install_span(_op("install-span", {}), {
            "bridge_name": br, "mirror_key": "k", "gre_port": gre,
            "src_ports": ["vmbr0"], "dst_ports": []}, _ctx())
    run = _Run(list_br=f"{br}\n")
    out = SwitchOps(run).install_span(_op("install-span", {}), {
        "bridge_name": br, "mirror_key": "k", "gre_port": gre, "remove": True,
        "delete_gre": True}, _ctx())
    assert out["mirror_installed"] is False
    assert any(c == f"ovs-vsctl --if-exists del-port {br} {gre}" for c in run.cmds)
    assert any(c.startswith(f"ip link show {gre}") for c in run.cmds)


def test_tc_tx_direction_mirrors_egress_only():
    from axrange.adapter_proxmox.ssh import _hook_script, _tc_mirror_cmd
    cmd = _tc_mirror_cmd("tap1i0", [("tapg_aaaaaa", "tx")])
    assert "ingress" not in cmd.split("clsact", 2)[-1] and "egress matchall" in cmd
    hook = _hook_script("p", [(0, "tapg_aaaaaa", "tx")])
    assert '[ "$dir" != "tx" ]' in hook and '[ "$dir" != "rx" ]' in hook


def test_union_direction():
    from axrange.tapscopes import union_direction
    assert union_direction("rx", "tx") == "both"
    assert union_direction(None, "tx") == "tx"
    assert union_direction("rx", "rx") == "rx"
    assert union_direction("both", "rx") == "both"


# ---------------------------------------------------------------------------
# R5: OVS state re-asserted by the VM's hook after a stop/start
# ---------------------------------------------------------------------------
from axrange.adapter_proxmox import switches as swmod


def test_hook_template_runs_the_ovs_fragments():
    from axrange.adapter_proxmox.ssh import _hook_script
    hook = _hook_script("p", [])
    assert 'for f in /var/lib/vz/snippets/axrange-ovs-${vmid}-*.sh; do' in hook
    assert hook.rstrip().endswith("exit 0")


def test_span_fragment_readds_the_taps_and_skips_a_gone_mirror():
    frag = swmod.span_fragment("tapsm_0011aabb", ["tap5i0"], ["tap5i0", "tap5i1"])
    assert '[ "$1" = post-start ] || exit 0' in frag
    assert '[ -n "$(ovs-vsctl --if-exists get mirror tapsm_0011aabb _uuid 2>/dev/null)" ] || exit 0' in frag
    assert "ovs-vsctl -- --id=@p get port tap5i0 -- add mirror tapsm_0011aabb select_src_port @p" in frag
    assert "get port tap5i1 -- add mirror tapsm_0011aabb select_dst_port @p" in frag
    with pytest.raises(AdapterError):
        swmod.span_fragment("tapsm_0011aabb", ["vmbr0"], [])


class _Persist:
    def __init__(self):
        self.hooks: list[int] = []
        self.files: dict[str, str] = {}

    def ensure(self, vmid, plan_id):
        self.hooks.append(vmid)

    def write(self, path, content):
        self.files[path] = content


def test_install_span_writes_one_fragment_per_vm_and_drops_stale_ones():
    br = naming.switch_bridge_for("p", "sw")
    gre = naming.gre_port_for_bridge_sensor(br, "s1")
    mirror = naming.span_mirror_for("tap-d1", "sw|s1|")
    stale = swmod.span_fragment_path(999, mirror)

    class _R(_Run):
        def __call__(self, cmd, **kw):
            self.cmds.append(cmd)
            return f"{stale}\n" if cmd.startswith("ls ") else ""
    run, keep = _R(), _Persist()
    SwitchOps(run, ensure_hook=keep.ensure, write_file=keep.write).install_span(
        _op("install-span", {}),
        {"bridge_name": br, "mirror_key": "sw|s1|", "gre_port": gre,
         "src_ports": ["tap7i0", naming.patch_port_for("p", "k", "a")], "dst_ports": ["tap8i1"]},
        _ctx("tap-d1"))
    assert keep.hooks == [7, 8]          # the veth end needs no fragment
    assert set(keep.files) == {swmod.span_fragment_path(7, mirror), swmod.span_fragment_path(8, mirror)}
    assert "select_dst_port" in keep.files[swmod.span_fragment_path(8, mirror)]
    assert f"rm -f {stale}" in run.cmds


def test_removing_a_span_deletes_its_fragments():
    br = naming.switch_bridge_for("p", "sw")
    mirror = naming.span_mirror_for("tap-d1", "k")
    run = _Run(list_br="")
    SwitchOps(run).install_span(_op("install-span", {}), {
        "bridge_name": br, "mirror_key": "k", "gre_port": naming.gre_port_for_bridge_sensor(br, "s"),
        "remove": True}, _ctx("tap-d1"))
    assert run.cmds[0] == f"rm -f /var/lib/vz/snippets/axrange-ovs-*-span-{mirror}.sh"


def test_blocked_port_writes_its_fragment():
    br = naming.switch_bridge_for("p", "sw")
    keep = _Persist()
    SwitchOps(_Run(), ensure_hook=keep.ensure, write_file=keep.write).set_port_state(
        _op("set-port-state", {}), {"bridge_name": br, "vmid": 600123, "nic_index": 1, "state": "blocked"},
        _ctx("p"))
    frag = keep.files[swmod.blocked_fragment_path(600123, 1)]
    assert keep.hooks == [600123]
    assert f"ovs-ofctl mod-port {br} tap600123i1 no-forward" in frag


@pytest.mark.parametrize("current, body, expect", [
    ("", "", "registered"),
    ("local:snippets/axrange-mirror-5.sh", "...\nfor f in /var/lib/vz/snippets/axrange-ovs-${vmid}-*.sh; do\n", "present"),
    ("local:snippets/axrange-mirror-5.sh", '#!/bin/bash\n# Plan:   tap-old\npairs="0:tapg_aaaaaa:rx"\nexit 0\n', "upgraded"),
])
def test_ensure_ovs_hook(monkeypatch, current, body, expect):
    from axrange.adapter_proxmox import ssh
    cmds, written = [], {}

    def fake_run(cfg, cmd, **kw):
        cmds.append(cmd)
        if cmd.startswith("qm config"):
            return current + "\n"
        if cmd.startswith("cat "):
            return body
        return ""
    monkeypatch.setattr(ssh, "run_ssh", fake_run)
    monkeypatch.setattr(ssh, "scp_to_remote", lambda cfg, content, path, **kw: written.__setitem__(path, content))
    assert ssh.ensure_ovs_hook(None, 5, "tap-new") == expect
    if expect == "registered":
        assert "qm set 5 --hookscript local:snippets/axrange-mirror-5.sh" in cmds
    if expect == "upgraded":
        hook = written["/var/lib/vz/snippets/axrange-mirror-5.sh"]
        assert 'pairs="0:tapg_aaaaaa:rx"' in hook and "# Plan:   tap-old" in hook
        assert "axrange-ovs-${vmid}" in hook
        assert not any(c.startswith("qm set") for c in cmds)


def test_ensure_ovs_hook_refuses_a_foreign_hook(monkeypatch):
    from axrange.adapter_proxmox import ssh
    monkeypatch.setattr(ssh, "run_ssh", lambda cfg, cmd, **kw: "local:snippets/someone-else.sh\n")
    with pytest.raises(AdapterError, match="foreign hook"):
        ssh.ensure_ovs_hook(None, 5, "p")
