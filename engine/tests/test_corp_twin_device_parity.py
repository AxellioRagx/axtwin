"""corp-twin device parity (docs/WORK_ORDER_corp_twin.md §10 P0-P2, §12):
the twin-config enablers (L2 legs, `isolated:` extension segments, per-CIDR
allocation, gateway inheritance), the planner's forwarder handling (first-leg
gateway, positional ipconfig placeholders, served-hosts closure, forwarder
ordering edges), `te egress` (the live containment read-back), and content
guards on corp-twin / corp-twin-devlab (NIC ceiling, recipe port tables vs
leg order, one router).

The byte-identity of every OTHER shipped plan under these engine changes is
pinned elsewhere (`test_twinconfig_catalog.py`'s plan_id pins) and was
checked op-by-op/edge-by-edge against HEAD when this landed (work order §10).
"""
from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from axrange.twinconfig.errors import TwinConfigShapeError
from axrange.twinconfig.expand import expand_twin_config_raw

REPO = Path(__file__).resolve().parents[1]
CATALOG = REPO.parent / "catalog"  # twin content lives at the project root
CORP = CATALOG / "twins" / "corp-twin"
DEVLAB = CATALOG / "twins" / "corp-twin-devlab"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _tc(segments, nodes, **extra):
    return {"twin_config": {"id": "t", "segments": segments, "nodes": nodes, **extra}}


def _nodes_by_id(ir):
    return {n["id"]: n for n in ir["layers"]["infra"]["nodes"]}


def _nets_by_id(ir):
    return {n["id"]: n for n in ir["layers"]["infra"]["networks"]}


def _fabric_config():
    """A router, an L2 bridge VM, and a host on the bridge's isolated access
    segment -- the smallest corp-twin shape."""
    return _tc(
        [
            {"name": "mx-lan", "cidr": "10.9.0.0/24"},
            {"name": "lan", "cidr": "10.9.0.0/24", "isolated": True},
        ],
        [
            {"name": "gw", "kind": "router", "image": "debian-12-cloud@v1",
             "nets": [{"segment": "mx-lan"}]},
            {"name": "sw", "image": "debian-12-cloud@v1",
             "nets": [{"segment": "mx-lan", "ip": "none"}, {"segment": "lan", "l2": True}],
             "install": [{"script": "scripts/sw.sh"}]},
            {"name": "h", "image": "debian-12-cloud@v1", "net": "lan", "count": 2,
             "install": [{"pkg": "curl"}]},
            {"name": "h-anchor", "image": "debian-12-cloud@v1", "net": "mx-lan"},
        ],
    )


# ---------------------------------------------------------------------------
# P0: expansion
# ---------------------------------------------------------------------------
def test_l2_legs_carry_no_ip_and_are_never_allocated():
    ir, _ = expand_twin_config_raw(_fabric_config())
    nodes = _nodes_by_id(ir)
    assert nodes["sw"]["nets"] == [{"net": "mx-lan"}, {"net": "lan"}]
    # gw takes .1 (lone router leg); the L2 legs consume nothing, so the
    # hosts get .2/.3/.4 from ONE pool shared by the anchor and its extension.
    assert nodes["gw"]["nets"] == [{"net": "mx-lan", "ip": "10.9.0.1"}]
    ips = [nodes[h]["nets"][0]["ip"] for h in ("h01", "h02", "h-anchor")]
    assert ips == ["10.9.0.2", "10.9.0.3", "10.9.0.4"]


def test_isolated_extension_inherits_the_anchor_gateway_and_is_flagged():
    ir, _ = expand_twin_config_raw(_fabric_config())
    nets = _nets_by_id(ir)
    assert nets["lan"] == {"id": "lan", "cidr": "10.9.0.0/24", "gateway": "10.9.0.1",
                           "isolated": True}
    assert nets["mx-lan"] == {"id": "mx-lan", "cidr": "10.9.0.0/24", "gateway": "10.9.0.1"}


def test_an_explicit_gateway_on_the_anchor_reaches_the_extension():
    cfg = _tc(
        [{"name": "a", "cidr": "10.8.0.0/25", "gateway": "10.8.0.1"},
         {"name": "b", "cidr": "10.8.0.0/25", "isolated": True}],
        [{"name": "x", "image": "i", "nets": [{"segment": "a", "ip": "10.8.0.2"},
                                                {"segment": "b", "ip": "10.8.0.3"}]},
         {"name": "y", "image": "i", "net": "b"}],
    )
    ir, _ = expand_twin_config_raw(cfg)
    assert _nets_by_id(ir)["b"]["gateway"] == "10.8.0.1"
    # pinned .2/.3 and the gateway .1 are all reserved in the shared pool
    assert _nodes_by_id(ir)["y"]["nets"][0]["ip"] == "10.8.0.4"


@pytest.mark.parametrize("segments, match", [
    ([{"name": "a", "cidr": "10.1.0.0/24", "isolated": True}], "found 0"),
    ([{"name": "a", "cidr": "10.1.0.0/24"}, {"name": "b", "cidr": "10.1.0.0/25",
      "isolated": True}], "found 0"),
    ([{"name": "a", "cidr": "10.1.0.0/24"}, {"name": "a2", "cidr": "10.1.0.0/24"},
      {"name": "b", "cidr": "10.1.0.0/24", "isolated": True}], "found 2"),
    ([{"name": "a", "cidr": "10.1.0.0/24"}, {"name": "b", "cidr": "10.1.0.0/24",
      "isolated": False}], "only takes `true`"),
])
def test_isolated_must_extend_exactly_one_anchor(segments, match):
    cfg = _tc(segments, [{"name": "h", "image": "i", "net": segments[0]["name"]}])
    with pytest.raises(TwinConfigShapeError, match=match):
        expand_twin_config_raw(cfg)


def test_l2_leg_with_an_address_is_refused():
    cfg = _tc([{"name": "a", "cidr": "10.1.0.0/24"}, {"name": "b", "cidr": "10.2.0.0/24"}],
              [{"name": "s", "image": "i",
                "nets": [{"segment": "a", "l2": True, "ip": "10.1.0.9"}, {"segment": "b"}]}])
    with pytest.raises(TwinConfigShapeError, match="carries no address"):
        expand_twin_config_raw(cfg)


def test_a_router_leg_cannot_be_l2():
    cfg = _tc([{"name": "a", "cidr": "10.1.0.0/24"}],
              [{"name": "r", "kind": "router", "image": "i",
                "nets": [{"segment": "a", "ip": "none"}]}])
    with pytest.raises(TwinConfigShapeError, match="cannot be L2"):
        expand_twin_config_raw(cfg)


def test_reserved_addresses_are_never_allocated_and_never_pinned():
    segs = [{"name": "a", "cidr": "10.1.0.0/29", "reserved": ["10.1.0.1", "10.1.0.3"]},
            {"name": "b", "cidr": "10.1.0.0/29", "isolated": True}]
    ir, _ = expand_twin_config_raw(_tc(segs, [
        {"name": "h", "image": "i", "net": "a", "count": 2},
        {"name": "e", "image": "i", "net": "b"}]))
    nodes = _nodes_by_id(ir)
    # .1/.3 skipped on the anchor AND its extension (one pool per CIDR)
    assert [nodes[n]["nets"][0]["ip"] for n in ("h01", "h02", "e")] == \
        ["10.1.0.2", "10.1.0.4", "10.1.0.5"]
    assert "reserved" not in _nets_by_id(ir)["a"]  # never reaches the IR
    with pytest.raises(TwinConfigShapeError, match="`reserved:` claims"):
        expand_twin_config_raw(_tc(segs, [
            {"name": "x", "image": "i", "nets": [{"segment": "b", "ip": "10.1.0.3"}]}]))
    with pytest.raises(TwinConfigShapeError, match="outside its cidr"):
        expand_twin_config_raw(_tc([{"name": "a", "cidr": "10.1.0.0/29",
                                     "reserved": ["10.2.0.1"]}],
                                   [{"name": "h", "image": "i", "net": "a"}]))


def test_ip_null_warns_that_it_means_allocate(caplog):
    cfg = _tc([{"name": "a", "cidr": "10.1.0.0/24"}, {"name": "b", "cidr": "10.2.0.0/24"}],
              [{"name": "s", "image": "i", "nets": [{"segment": "a", "ip": None},
                                                     {"segment": "b", "l2": True}]}])
    with caplog.at_level("WARNING", logger="axrange.twinconfig.expand"):
        ir, _ = expand_twin_config_raw(cfg)
    assert _nodes_by_id(ir)["s"]["nets"] == [{"net": "a", "ip": "10.1.0.1"}, {"net": "b"}]
    assert "write `l2: true`" in caplog.text and "'s'" in caplog.text


def test_a_forwarder_keeps_a_segment_whose_hosts_were_all_gated_out():
    """G14: an asserted VLAN whose every observed host was transient survives
    the default gate once a switch VM carries it -- the forwarder is a
    non-router occupant, so the empty-segment cascade does not fire."""
    obs_low = {"windows_seen": 1, "windows_total": 10, "occupancy": 0.1}
    base = _fabric_config()["twin_config"]
    base["nodes"][2]["observation"] = obs_low          # the hosts on `lan`
    base["nodes"][3]["observation"] = {"windows_seen": 9, "windows_total": 9,
                                       "occupancy": 1.0}
    ir, _ = expand_twin_config_raw({"twin_config": base})
    assert "lan" in _nets_by_id(ir)
    assert not any(n.startswith("h0") for n in _nodes_by_id(ir))
    # ... and without the switch, the cascade drops it exactly as before.
    base["nodes"] = [n for n in base["nodes"] if n["name"] != "sw"]
    ir2, _ = expand_twin_config_raw({"twin_config": base})
    assert "lan" not in _nets_by_id(ir2)


# ---------------------------------------------------------------------------
# P0: planner
# ---------------------------------------------------------------------------
def _plan(ir, manifests, scenario_dir):
    from axrange.ir.parser import parse_ir
    from axrange.planner import build_plan
    from axrange.planner.cluster import ClusterContext

    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "x.yaml"
        with p.open("w") as f:
            YAML(typ="safe").dump(ir, f)
        parsed = parse_ir(path=str(p))
        return build_plan(parsed.ir, manifests, ClusterContext.single_node("pxtest1"),
                          raw_ir=parsed.raw, scenario_dir=str(scenario_dir))


@pytest.fixture
def fabric_plan(manifests, tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "sw.sh").write_text("#!/bin/sh\ntrue\n")
    cfg = _fabric_config()
    # a second-hop L3 forwarder behind the switch, with a host behind it
    tc = cfg["twin_config"]
    tc["segments"] += [{"name": "far", "cidr": "10.7.0.0/24", "gateway": "10.7.0.1"}]
    tc["nodes"] += [
        {"name": "l3", "image": "debian-12-cloud@v1",
         "nets": [{"segment": "lan", "ip": "10.9.0.20"}, {"segment": "far", "ip": "10.7.0.2"}],
         "install": [{"script": "scripts/sw.sh"}]},
        {"name": "far-h", "image": "debian-12-cloud@v1", "net": "far",
         "install": [{"pkg": "curl"}]},
    ]
    tc["nodes"][0]["install"] = [{"script": "scripts/sw.sh"}]  # a recipe on the router
    ir, _ = expand_twin_config_raw(cfg)
    return _plan(ir, manifests, tmp_path)


def _ops(plan, name, host):
    return [op for op in plan.ops.values() if op.op_name == name and op.scope.get("host") == host]


def test_multi_homed_host_gets_one_default_route_on_its_first_gatewayed_leg(fabric_plan):
    (ci,) = _ops(fabric_plan, "configure-cloud-init", "l3")
    assert ci.inputs_resolved["ip_config"] == {
        "lan": {"ip": "10.9.0.20", "cidr": "10.9.0.0/24", "gateway": "10.9.0.1"},
        "far": {"ip": "10.7.0.2", "cidr": "10.7.0.0/24", "gateway": None},
    }


def test_l2_bridge_gets_no_ipconfig_at_all(fabric_plan):
    (ci,) = _ops(fabric_plan, "configure-cloud-init", "sw")
    assert ci.inputs_resolved["ip_config"] == {}


def test_leading_address_less_leg_keeps_a_positional_placeholder(manifests, tmp_path):
    cfg = _tc([{"name": "a", "cidr": "10.1.0.0/24"}, {"name": "b", "cidr": "10.2.0.0/24"}],
              [{"name": "r", "kind": "router", "image": "debian-12-cloud@v1",
                "nets": [{"segment": "a"}, {"segment": "b"}]},
               {"name": "s", "image": "debian-12-cloud@v1",
                "nets": [{"segment": "a", "ip": "none"}, {"segment": "b", "ip": "10.2.0.9"}]}])
    ir, _ = expand_twin_config_raw(cfg)
    plan = _plan(ir, manifests, tmp_path)
    (ci,) = _ops(plan, "configure-cloud-init", "s")
    ipc = ci.inputs_resolved["ip_config"]
    assert list(ipc) == ["a", "b"] and ipc["a"] == {"ip": None}

    from axrange.adapter_proxmox.rest import _build_ipconfig_body
    # net1 (b) carries the address; net0 (the L2 port) gets no ipconfig key.
    assert _build_ipconfig_body(ipc) == {"ipconfig1": "ip=10.2.0.9/24,gw=10.2.0.1"}


def test_router_served_hosts_close_through_forwarders(fabric_plan):
    (cr,) = [op for op in fabric_plan.ops.values() if op.op_name == "configure-router"]
    assert set(cr.planner_hints["served_hosts"]) == {"sw", "h01", "h02", "h-anchor",
                                                     "l3", "far-h"}


def test_forwarder_layers_and_ordering_edges(fabric_plan):
    from axrange.planner.pass3_ordering import forwarder_layers

    layer, comp = forwarder_layers(fabric_plan)
    assert layer == {"sw": 0, "l3": 1}
    assert comp["sw"] == comp["l3"]

    preds: dict[str, set[str]] = {}
    for e in fabric_plan.edges:
        preds.setdefault(e.dst_op_id, set()).add(e.src_op_id)
    (sw_rs,) = _ops(fabric_plan, "run-script", "sw")
    (l3_rs,) = _ops(fabric_plan, "run-script", "l3")
    (gw_rs,) = _ops(fabric_plan, "run-script", "gw")
    # a nearer forwarder gates a farther one, never the reverse
    assert sw_rs.op_id in preds[l3_rs.op_id]
    assert l3_rs.op_id not in preds.get(sw_rs.op_id, set())
    # a host behind the fabric waits on every forwarder AND the router's recipe
    (far_pa,) = _ops(fabric_plan, "provision-app", "far-h")
    assert {sw_rs.op_id, l3_rs.op_id, gw_rs.op_id} <= preds[far_pa.op_id]
    # a host on the router's own segment is not behind the fabric
    assert not _ops(fabric_plan, "provision-app", "h-anchor")


def _assert_acyclic(plan):
    import graphlib

    g = graphlib.TopologicalSorter()
    for e in plan.edges:
        g.add(e.dst_op_id, e.src_op_id)
    g.prepare()  # raises CycleError


def test_fabric_plan_is_acyclic(fabric_plan):
    _assert_acyclic(fabric_plan)


def test_forwarders_in_one_layer_never_wait_on_each_other(manifests, tmp_path):
    """Two switches side by side (the 8325h VSX pair, joined by an ISL) share
    a layer: an edge either way would be arbitrary, both ways a cycle."""
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "sw.sh").write_text("#!/bin/sh\ntrue\n")
    sw = {"install": [{"script": "scripts/sw.sh"}], "image": "debian-12-cloud@v1"}
    cfg = _tc(
        [{"name": "up", "cidr": "10.3.0.0/24"},
         {"name": "isl", "cidr": "10.3.0.0/24", "isolated": True}],
        [{"name": "r", "kind": "router", "image": "debian-12-cloud@v1",
          "nets": [{"segment": "up"}]},
         {"name": "left", **sw, "nets": [{"segment": "up", "ip": "none"},
                                         {"segment": "isl", "ip": "none"}]},
         {"name": "right", **sw, "nets": [{"segment": "up", "ip": "none"},
                                          {"segment": "isl", "ip": "none"}]}],
    )
    ir, _ = expand_twin_config_raw(cfg)
    plan = _plan(ir, manifests, tmp_path)
    from axrange.planner.pass3_ordering import forwarder_layers

    layer, _comp = forwarder_layers(plan)
    assert layer == {"left": 0, "right": 0}
    rs = {op.op_id for op in plan.ops.values() if op.op_name == "run-script"}
    assert not [e for e in plan.edges if e.src_op_id in rs and e.dst_op_id in rs]
    _assert_acyclic(plan)


def test_a_fabric_that_reaches_no_router_orders_no_host(manifests, tmp_path):
    """S7: a forwarder component with no layered member leads nowhere, so a
    host on it gets no edge from the forwarder pass."""
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "sw.sh").write_text("#!/bin/sh\ntrue\n")
    cfg = _tc(
        [{"name": "up", "cidr": "10.3.0.0/24"}, {"name": "x", "cidr": "10.4.0.0/24"},
         {"name": "y", "cidr": "10.5.0.0/24"}],
        [{"name": "r", "kind": "router", "image": "debian-12-cloud@v1",
          "nets": [{"segment": "up"}]},
         {"name": "f", "image": "debian-12-cloud@v1", "install": [{"script": "scripts/sw.sh"}],
          "nets": [{"segment": "x", "l2": True}, {"segment": "y", "l2": True}]},
         {"name": "hx", "image": "debian-12-cloud@v1", "net": "x",
          "install": [{"pkg": "curl"}]}],
    )
    ir, _ = expand_twin_config_raw(cfg)
    plan = _plan(ir, manifests, tmp_path)
    from axrange.planner.pass3_ordering import forwarder_layers

    layer, comp = forwarder_layers(plan)
    assert layer == {} and comp == {"f": 0}
    (f_rs,) = _ops(plan, "run-script", "f")
    (hx_pa,) = _ops(plan, "provision-app", "hx")
    assert not [e for e in plan.edges
                if e.src_op_id == f_rs.op_id and e.dst_op_id == hx_pa.op_id]


def test_a_dhcp_leg_never_claims_the_static_default_route(manifests, tmp_path):
    cfg = _tc([{"name": "a", "cidr": "10.1.0.0/24", "gateway": "10.1.0.1"},
               {"name": "b", "cidr": "10.2.0.0/24", "gateway": "10.2.0.1"}],
              [{"name": "s", "image": "debian-12-cloud@v1",
                "nets": [{"segment": "a", "ip": "dhcp"}, {"segment": "b", "ip": "10.2.0.9"}]}])
    ir, _ = expand_twin_config_raw(cfg)
    (ci,) = _ops(_plan(ir, manifests, tmp_path), "configure-cloud-init", "s")
    assert ci.inputs_resolved["ip_config"]["b"]["gateway"] == "10.2.0.1"


# ---------------------------------------------------------------------------
# te egress: the live containment read-back
# ---------------------------------------------------------------------------
class _Res:
    def __init__(self, rc, out=""):
        self.exit_code, self.stdout, self.stderr = rc, out, ""


class _FakeGA:
    """Answers the egress engine the way `egress_render`'s script does."""

    def __init__(self, phase="provision", healthy=True, flip_ok=True):
        self.phase, self.healthy, self.flip_ok, self.calls = phase, healthy, flip_ok, []

    def exec(self, vmid, argv, timeout_s=300):
        self.calls.append((vmid, list(argv)))
        arg = argv[-1]
        if arg == "--show":
            return _Res(0, f"phase={self.phase} since=2026-09-26T00:00:00Z "
                           "sink=10.250.234.66 mode=default\n")
        if arg == "--healthcheck":
            return _Res(0 if self.healthy else 1, f"phase={self.phase}\nok   wan drop on ens27\n")
        if not self.flip_ok:
            return _Res(1, "axr-egress: measure: sink not reachable\n")
        self.phase = arg
        return _Res(0, f"AXR_EGRESS_OK phase={arg}\n")


def _row(deploy_id, op_id, name, scope, *, inputs=None, outputs=None):
    from axrange.planner.state import OpRow, OpStatus

    return OpRow(
        op_id=op_id, deploy_id=deploy_id, op_name=name, op_catalog_id="OP-X",
        kind="action", scope_json=json.dumps(scope),
        inputs_resolved_json=json.dumps(inputs or {}), inputs_from_outputs_json="{}",
        outputs_json=json.dumps(outputs) if outputs is not None else None,
        cleanup_strategy="redo", idempotency_class="infra",
        status=OpStatus.COMPLETED, started_at=0.0, completed_at=0.0,
    )


@pytest.fixture
def egress_db(tmp_path):
    from axrange.planner import StateStore

    db = tmp_path / "state.db"
    store = StateStore(str(db))
    store.create_deploy(deploy_id="corp-twin-abc123", scenario_id="corp-twin",
                        scenario_version=1, scenario_schema_version=1,
                        plan_id="corp-twin-abc123", ir_hash="h", placement={})
    store.insert_ops("corp-twin-abc123", [
        _row("corp-twin-abc123", "op-clone", "clone-vm", {"host": "hq-mx"},
             outputs={"vmid": 4242}),
        _row("corp-twin-abc123", "op-cr", "configure-router", {"host": "hq-mx"},
             inputs={"egress": {"phase": "provision", "sink_ip": "10.250.234.66"}},
             outputs={"configured": True, "egress_phase": "provision"}),
    ])
    store.close()
    return db


def _posture(db):
    from axrange.planner import StateStore
    from axrange.planner.egress import egress_posture_from_ops

    store = StateStore(str(db))
    try:
        return egress_posture_from_ops(store.ops_for_deploy("corp-twin-abc123"))
    finally:
        store.close()


def test_te_egress_flip_records_the_live_phase_where_the_gate_reads_it(egress_db, capsys):
    from axrange.cli import main as climain
    from axrange.cli.main import _build_parser as build_parser

    assert _posture(egress_db).contained is False
    ga = _FakeGA("provision")
    args = build_parser().parse_args(
        ["te", "egress", "corp-twin", "--phase", "measure", "--db", str(egress_db)])
    assert climain.cmd_te_egress(args, ga=ga) == 0
    out = capsys.readouterr().out
    assert "recorded egress_phase=measure -- CONTAINED" in out
    assert [c[1][-1] for c in ga.calls] == ["measure", "--show", "--healthcheck"]
    assert all(c[0] == 4242 for c in ga.calls)
    posture = _posture(egress_db)
    assert posture.contained and posture.phase_applied == "measure"
    assert posture.phase_declared == "provision"


def test_te_egress_show_after_a_hand_flip_is_what_unblocks_the_gate(egress_db):
    from axrange.cli import main as climain
    from axrange.cli.main import _build_parser as build_parser

    args = build_parser().parse_args(["te", "egress", "--show", "corp-twin-abc123",
                                      "--db", str(egress_db)])
    assert climain.cmd_te_egress(args, ga=_FakeGA("measure")) == 0
    assert _posture(egress_db).contained


def test_te_egress_records_an_unasserted_measure_as_not_contained(egress_db, capsys):
    from axrange.cli import main as climain
    from axrange.cli.main import _build_parser as build_parser

    args = build_parser().parse_args(["te", "egress", "--show", "corp-twin",
                                      "--db", str(egress_db)])
    assert climain.cmd_te_egress(args, ga=_FakeGA("measure", healthy=False)) == 1
    assert "measure-unverified" in capsys.readouterr().out
    posture = _posture(egress_db)
    assert not posture.contained and posture.phase_applied == "measure-unverified"


def test_te_egress_failed_flip_fails_loud_and_records_the_read_back(egress_db, capsys):
    from axrange.cli import main as climain
    from axrange.cli.main import _build_parser as build_parser

    args = build_parser().parse_args(["te", "egress", "--phase", "measure", "corp-twin",
                                      "--db", str(egress_db)])
    assert climain.cmd_te_egress(args, ga=_FakeGA("provision", flip_ok=False)) == 1
    err = capsys.readouterr().err
    assert "failed" in err and "egress_phase=provision" in err
    assert _posture(egress_db).phase_applied == "provision"


def test_te_egress_refuses_a_twin_with_no_posture(tmp_path, capsys):
    from axrange.cli import main as climain
    from axrange.cli.main import _build_parser as build_parser
    from axrange.planner import StateStore

    db = tmp_path / "s.db"
    store = StateStore(str(db))
    store.create_deploy(deploy_id="d1", scenario_id="x", scenario_version=1,
                        scenario_schema_version=1, plan_id="d1", ir_hash="h", placement={})
    store.insert_ops("d1", [_row("d1", "op-cr", "configure-router", {"host": "r"})])
    store.close()
    args = build_parser().parse_args(["te", "egress", "--show", "d1", "--db", str(db)])
    assert climain.cmd_te_egress(args, ga=_FakeGA()) == 1
    assert "declares no containment" in capsys.readouterr().err


def test_gate_passes_after_te_egress_without_allow_uncontained(egress_db, monkeypatch, capsys):
    """End to end on a real state DB: provision -> the tap gate refuses; te
    egress --phase measure -> the SAME gate lets the deploy through."""
    from axrange.cli import main as climain
    from axrange.cli.main import _build_parser as build_parser
    from axrange.o2control import taps

    def _applied(**kw):
        raise taps.TapPlanError("APPLY-REACHED", status=400)

    monkeypatch.setattr(taps, "apply_tap_plan", _applied)
    router = _FakeGA("provision")  # ONE router: the gate and te egress both read it
    monkeypatch.setattr(climain, "_egress_guest_agent", lambda args: router)
    argv = ["tap", "deploy", str(CORP / "tap" / "default.yaml"), "--db", str(egress_db)]
    assert climain.main(argv) == 2
    assert "te egress corp-twin-abc123 --phase measure" in capsys.readouterr().err

    args = build_parser().parse_args(["te", "egress", "corp-twin", "--phase", "measure",
                                      "--db", str(egress_db)])
    assert climain.cmd_te_egress(args, ga=router) == 0
    capsys.readouterr()
    assert climain.main(argv) == 2
    err = capsys.readouterr().err
    assert "APPLY-REACHED" in err and "refusing" not in err


class _BrokenGA(_FakeGA):
    """A router whose `fail_on` exec raises (QGA timeout / transport error)
    or exits non-zero (`show_rc`)."""

    def __init__(self, *, fail_on=None, show_rc=0, **kw):
        super().__init__(**kw)
        self.fail_on, self.show_rc = fail_on, show_rc

    def exec(self, vmid, argv, timeout_s=300):
        if argv[-1] == self.fail_on:
            self.calls.append((vmid, list(argv)))
            raise TimeoutError("guest-exec-status: no answer in 300s")
        if argv[-1] == "--show" and self.show_rc:
            self.calls.append((vmid, list(argv)))
            return _Res(self.show_rc, "axr-egress: state file unreadable\n")
        return super().exec(vmid, argv, timeout_s)


@pytest.fixture
def measured_db(egress_db):
    """egress_db after an earlier successful `te egress`: recorded `measure`."""
    from axrange.planner import StateStore

    store = StateStore(str(egress_db))
    assert store.merge_op_outputs("corp-twin-abc123", "op-cr", {"egress_phase": "measure"})
    store.close()
    assert _posture(egress_db).contained
    return egress_db


def _live_record(db):
    from axrange.planner import StateStore
    from axrange.planner.opview import op_fields

    store = StateStore(str(db))
    try:
        for op in store.ops_for_deploy("corp-twin-abc123"):
            name, _s, _i, out = op_fields(op)
            if name == "configure-router":
                return out
    finally:
        store.close()


@pytest.mark.parametrize("ga_kw", [
    {"show_rc": 3},                       # --show exits non-zero
    {"fail_on": "--show"},                # --show exec raises
    {"fail_on": "--healthcheck"},         # --healthcheck exec raises
], ids=["show-exit", "show-raises", "health-raises"])
def test_a_failed_show_never_leaves_a_stale_measure(measured_db, capsys, ga_kw):
    from axrange.cli import main as climain
    from axrange.cli.main import _build_parser as build_parser

    args = build_parser().parse_args(["te", "egress", "--show", "corp-twin",
                                      "--db", str(measured_db)])
    assert climain.cmd_te_egress(args, ga=_BrokenGA(phase="measure", **ga_kw)) == 1
    err = capsys.readouterr().err
    assert "unknown-unverified" in err
    assert not _posture(measured_db).contained
    out = _live_record(measured_db)
    assert out["egress_phase"] == "unknown-unverified"
    assert out["egress_live"]["error"] and out["configured"] is True


def test_a_flip_that_raises_mid_exec_is_recorded_not_contained(measured_db, capsys):
    from axrange.cli import main as climain
    from axrange.cli.main import _build_parser as build_parser

    args = build_parser().parse_args(["te", "egress", "--phase", "provision", "corp-twin",
                                      "--db", str(measured_db)])
    ga = _BrokenGA(phase="measure", fail_on="provision")
    assert climain.cmd_te_egress(args, ga=ga) == 1
    assert "TimeoutError" in capsys.readouterr().err
    assert _live_record(measured_db)["egress_phase"] == "unknown-unverified"
    assert not _posture(measured_db).contained


def test_the_row_is_invalidated_before_the_guest_is_touched(measured_db):
    """Whatever the guest does, the record already says NOT contained while
    the exec runs -- a crash mid-flip cannot leave `measure` behind."""
    from axrange.o2control.containment import egress_live
    from axrange.planner import StateStore

    seen = []

    class _Peek(_FakeGA):
        def exec(self, vmid, argv, timeout_s=300):
            seen.append((argv[-1], _live_record(measured_db)["egress_phase"]))
            return super().exec(vmid, argv, timeout_s)

    store = StateStore(str(measured_db))
    try:
        egress_live(store, "corp-twin-abc123", "provision", ga=_Peek("measure"))
    finally:
        store.close()
    assert seen[0] == ("provision", "provision-pending-unverified")
    assert _live_record(measured_db)["egress_phase"] == "provision"


def test_a_row_that_cannot_be_written_fails_loud():
    from axrange.o2control import containment

    class _Store:
        def ops_for_deploy(self, did):
            return [_row(did, "op-clone", "clone-vm", {"host": "r"}, outputs={"vmid": 7}),
                    _row(did, "op-cr", "configure-router", {"host": "r"},
                         inputs={"egress": {"phase": "measure"}}, outputs={})]

        def merge_op_outputs(self, did, op_id, patch):
            return False

    with pytest.raises(containment.ContainmentError, match="could not record"):
        containment.egress_live(_Store(), "d", "show", ga=_FakeGA("measure"))


def test_merge_refuses_a_corrupt_outputs_record(egress_db):
    import sqlite3

    from axrange.planner import StateStore

    con = sqlite3.connect(str(egress_db))
    con.execute("UPDATE ops SET outputs_json = '{not json' WHERE op_id = 'op-cr'")
    con.commit()
    con.close()
    store = StateStore(str(egress_db))
    try:
        with pytest.raises(ValueError, match="corrupt outputs_json"):
            store.merge_op_outputs("corp-twin-abc123", "op-cr", {"egress_phase": "measure"})
    finally:
        store.close()


def _tap_argv(db, *extra):
    return ["tap", "deploy", str(CORP / "tap" / "default.yaml"), "--db", str(db), *extra]


@pytest.fixture
def tap_apply(monkeypatch):
    from axrange.o2control import taps

    def _applied(**kw):
        raise taps.TapPlanError("APPLY-REACHED", status=400)

    monkeypatch.setattr(taps, "apply_tap_plan", _applied)


def test_gate_refuses_a_recorded_measure_the_router_no_longer_holds(
        measured_db, monkeypatch, capsys, tap_apply):
    """The record says measure; the router (flipped by hand) says provision.
    The gate reads the router, not the record."""
    from axrange.cli import main as climain

    monkeypatch.setattr(climain, "_egress_guest_agent", lambda args: _FakeGA("provision"))
    assert climain.main(_tap_argv(measured_db)) == 2
    err = capsys.readouterr().err
    assert "refusing" in err and "LIVE egress phase reads 'provision'" in err
    assert "APPLY-REACHED" not in err
    assert _live_record(measured_db)["egress_phase"] == "provision"


@pytest.mark.parametrize("allow", [False, True])
def test_gate_refuses_an_unreachable_router(measured_db, monkeypatch, capsys, tap_apply, allow):
    from axrange.cli import main as climain

    monkeypatch.setattr(climain, "_egress_guest_agent",
                        lambda args: _BrokenGA(phase="measure", fail_on="--show"))
    extra = ["--allow-uncontained"] if allow else []
    assert climain.main(_tap_argv(measured_db, *extra)) == 2
    err = capsys.readouterr().err
    assert "could not be read" in err and "TimeoutError" in err
    if allow:
        assert "[warn]" in err and "APPLY-REACHED" in err
    else:
        assert "refusing" in err and "APPLY-REACHED" not in err
    assert not _posture(measured_db).contained


def test_gate_refuses_when_no_guest_agent_can_be_built(measured_db, monkeypatch, capsys,
                                                       tap_apply):
    from axrange.adapter_proxmox import ConfigError
    from axrange.cli import main as climain

    def _no_cfg(args):
        raise ConfigError("adapter.conf: no such file")

    monkeypatch.setattr(climain, "_egress_guest_agent", _no_cfg)
    assert climain.main(_tap_argv(measured_db)) == 2
    assert "adapter.conf: no such file" in capsys.readouterr().err


def test_gate_passes_on_a_live_asserted_measure_and_te_info_shows_the_read(
        measured_db, monkeypatch, capsys, tap_apply):
    from axrange.cli import main as climain

    router = _FakeGA("measure")
    monkeypatch.setattr(climain, "_egress_guest_agent", lambda args: router)
    assert climain.main(_tap_argv(measured_db)) == 2
    err = capsys.readouterr().err
    assert "APPLY-REACHED" in err and "refusing" not in err
    assert [c[1][-1] for c in router.calls] == ["--show", "--healthcheck"]
    posture = _posture(measured_db)
    assert posture.contained and posture.live_read_at
    climain._write_egress_line(posture.to_dict(), indent="")
    assert f"(live read {posture.live_read_at})" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# content guards: corp-twin and corp-twin-devlab
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module", params=["corp-twin", "corp-twin-devlab"])
def twin_ir(request):
    from axrange.twinconfig.expand import expand_twin_config

    ir, _ = expand_twin_config(request.param)
    return request.param, ir


def test_the_vrrp_vips_are_no_nodes_address(twin_ir):
    """10.180.93.254 (reserved on mx-vlan935) and 10.180.34.1 (vlan953's
    gateway) float between the 9K pair; no leg may carry either."""
    _name, ir = twin_ir
    ips = {leg.get("ip") for n in ir["layers"]["infra"]["nodes"] for leg in n["nets"]}
    assert not ips & {"10.180.93.254", "10.180.34.1"}


def test_every_vm_fits_the_pve_nic_ceiling(twin_ir):
    """net0..net31: a router's legs + its engine-added WAN NIC, any other
    VM's legs. hq-mx is the widest (~27 at the default gate), mx-sw next."""
    _name, ir = twin_ir
    for n in ir["layers"]["infra"]["nodes"]:
        nics = len(n.get("nets") or []) + (1 if n.get("kind") == "router" else 0)
        assert nics <= 32, (n["id"], nics)


def test_one_router_one_wan_one_egress_carrier(twin_ir):
    _name, ir = twin_ir
    routers = [n["id"] for n in ir["layers"]["infra"]["nodes"] if n.get("kind") == "router"]
    assert routers == ["hq-mx"]
    assert ir["egress"]["phase"] in ("provision", "measure")


def _script_tables(script: Path) -> dict[str, list[str]]:
    """{device: [leg segment, ...]} parsed from serve-l2switch.sh's heredocs."""
    text = script.read_text()
    tables: dict[str, list[str]] = {}
    for m in re.finditer(r"^\s*([\w-]+)\) cat <<'EOT'\n(.*?)^EOT$", text, re.S | re.M):
        rows = [ln.split()[0] for ln in m.group(2).splitlines()
                if ln.strip() and not ln.startswith("bridge")]
        tables[m.group(1)] = rows
    return tables


def test_l2switch_port_tables_match_every_switch_vms_leg_order(twin_ir):
    """The recipe maps NICs to ports BY POSITION -- a table that drifts from
    the twin's leg order bridges the wrong segments together."""
    _name, ir = twin_ir
    tables = _script_tables(CORP / "scripts" / "serve-l2switch.sh")
    switches = {n["id"]: [leg["net"] for leg in n["nets"]]
                for n in ir["layers"]["infra"]["nodes"] if n.get("role") == "l2-switch"}
    assert switches, "no switch VMs"
    assert set(switches) == set(tables)
    for dev, legs in switches.items():
        assert tables[dev] == legs, dev


def _run_neutraliser(root: Path, nic: str = "ens18") -> str:
    """Run serve-l2switch.sh's embedded python (the `EOPY` heredoc) against a
    fake root, exactly as the recipe runs it on a guest."""
    import os
    import subprocess
    import sys

    text = (CORP / "scripts" / "serve-l2switch.sh").read_text()
    code = re.search(r"<<'EOPY'\n(.*?)^EOPY$", text, re.S | re.M).group(1)
    mark = re.search(r'^AXR_IFACE_MARK="([^"]+)"$', text, re.M).group(1)
    env = dict(os.environ, AXR_L2_ROOT=str(root), AXR_IFACE_MARK=mark)
    r = subprocess.run([sys.executable, "-", nic, "52:54:00:aa:bb:cc"], input=code,
                       env=env, capture_output=True, text=True, check=True)
    return r.stdout.strip()


# A cloud-init-rendered ENI (Debian cloud image), plus the end-of-line-comment
# form an earlier apply of the recipe wrote (which ifupdown refuses to parse).
_ENI = """\
auto lo
iface lo inet loopback

auto ens18
iface ens18 inet dhcp
    dns-nameservers 10.0.0.1
    mtu 1500

auto ens19
iface ens19 inet static
    address 10.1.1.5/24
"""


@pytest.mark.parametrize("legacy", [False, True])
def test_l2switch_ifupdown_rewrite_is_parseable_and_idempotent(tmp_path, legacy):
    """ifupdown has no end-of-line comments: the rewritten stanza must be a
    bare `iface <nic> <fam> manual` (marker on its own line before it), the
    port's options commented out, other interfaces untouched; re-run = no-op."""
    eni = tmp_path / "etc" / "network" / "interfaces"
    eni.parent.mkdir(parents=True)
    body = _ENI
    if legacy:
        body = body.replace("iface ens18 inet dhcp",
                            "iface ens18 inet manual  # axr-l2switch: bridge port")
    eni.write_text(body)

    assert _run_neutraliser(tmp_path) == str(eni)
    out = eni.read_text()
    lines = out.splitlines()
    for ln in lines:
        toks = ln.split()
        if toks[:1] == ["iface"]:
            assert len(toks) == 4, ln          # iface <name> <family> <method>
    i = lines.index("iface ens18 inet manual")
    assert lines[i - 1] == "# axr-l2switch: bridge port"
    assert lines[i - 2] == "auto ens18"
    assert "#axr-l2switch# dns-nameservers 10.0.0.1" in lines
    assert "iface ens19 inet static" in lines and "    address 10.1.1.5/24" in lines
    assert out.count("# axr-l2switch: bridge port") == 1

    assert _run_neutraliser(tmp_path) == ""    # second apply: nothing to rewrite
    assert eni.read_text() == out


def test_l2switch_validates_a_rewritten_config_with_its_own_parser():
    text = (CORP / "scripts" / "serve-l2switch.sh").read_text()
    assert "ifquery -a --list" in text and "netplan generate" in text
    assert re.search(r"axr_check_port_config \$fixed", text)


_NETPLAN = """\
network:
  version: 2
  ethernets:
    eth0:
      match:
        macaddress: 52:54:00:aa:bb:cc
      set-name: eth0
      addresses: [10.180.16.15/20]
      gateway4: 10.180.16.1
      nameservers: {addresses: [1.1.1.1]}
    eth1:
      match:
        macaddress: 52:54:00:00:00:01
      dhcp4: true
"""


def test_l2switch_netplan_rewrite_is_address_less_optional_and_idempotent(tmp_path):
    """The netplan port entry (matched by MAC) loses every address/route/DNS
    key, DHCP/RA/link-local go off and it becomes `optional` (boot never waits
    on an address-less port); the other NIC is untouched; re-run = no-op."""
    yaml = pytest.importorskip("yaml")
    np = tmp_path / "etc" / "netplan" / "50-cloud-init.yaml"
    np.parent.mkdir(parents=True)
    np.write_text(_NETPLAN)

    assert _run_neutraliser(tmp_path, nic="eth0") == str(np)
    eths = yaml.safe_load(np.read_text())["network"]["ethernets"]
    port = eths["eth0"]
    for k in ("addresses", "gateway4", "gateway6", "routes", "nameservers"):
        assert k not in port, k
    assert port["dhcp4"] is False and port["dhcp6"] is False
    assert port["accept-ra"] is False and port["link-local"] == []
    assert port["optional"] is True
    assert port["set-name"] == "eth0"                 # the rename survives
    assert eths["eth1"] == {"match": {"macaddress": "52:54:00:00:00:01"}, "dhcp4": True}

    out = np.read_text()
    assert _run_neutraliser(tmp_path, nic="eth0") == ""
    assert np.read_text() == out


def test_l2switch_loads_the_rewrite_into_networkd_before_any_port_is_touched():
    """Live run 1 (2026-09-26): networkd kept its boot-time .network in memory
    and re-added the MS120 mgmt address to eth0 on the carrier-up that
    enslaving causes. Every config rewrite must precede ONE fail-loud
    `networkctl reload`, which must precede every flush and enslave."""
    text = (CORP / "scripts" / "serve-l2switch.sh").read_text()
    body = text[text.index("# ---- the bridge: STP ON"):]
    reload_call = re.search(r"^axr_reload_network_daemon$", body, re.M)
    assert reload_call, "the reload is not called at top level"
    at = reload_call.start()
    rewrites = [m.start() for m in re.finditer(r'axr_neutralise_port_config "\$nic"', body)]
    assert rewrites and max(rewrites) < at
    for touch in ('ip -4 addr flush dev "$nic"', 'ip link set "$nic" master "$BR"'):
        assert body.index(touch) > at, touch
    fn = re.search(r"^axr_reload_network_daemon\(\) \{\n(.*?)^\}$", text, re.S | re.M).group(1)
    assert re.search(r"networkctl reload \|\|\s*\n\s*die ", fn), "reload must fail loud"
    assert "|| true" not in fn.split("networkctl reload")[1].split("\n")[0]
    # Unconditional (an earlier apply may have rewritten the file and died
    # before reloading): not gated on this apply having changed anything.
    assert "fixed" not in fn


def test_vrrp_recipe_addresses_match_the_twin(twin_ir):
    """serve-vrrp-l3.sh hard-codes the peer legs and both VIPs; the twin pins
    the legs and reserves/gateways the VIPs. Drift = a peer ping to nothing."""
    _name, ir = twin_ir
    text = (CORP / "scripts" / "serve-vrrp-l3.sh").read_text()
    legs = {n["id"]: [leg["ip"] for leg in n["nets"]]
            for n in ir["layers"]["infra"]["nodes"] if n["id"] in ("9k01", "9k02")}
    for dev, peer in (("9k01", "9k02"), ("9k02", "9k01")):
        m = re.search(rf"^\s*{dev}\) PRIO=\d+; PEER_935=(\S+); PEER_953=(\S+) ;;", text, re.M)
        assert m, dev
        assert [m.group(1), m.group(2)] == legs[peer], dev
    assert "V935_VIP=10.180.93.254/24" in text and "V953_VIP=10.180.34.1/25" in text
    assert _nets_by_id(ir)["vlan953"]["gateway"] == "10.180.34.1"


def test_devlab_shares_corp_twins_recipes_and_fabric():
    y = YAML(typ="safe")
    corp = y.load((CORP / "te.yaml").read_text())["twin_config"]
    lab = y.load((DEVLAB / "te.yaml").read_text())["twin_config"]
    dev = lambda tc: {n["name"]: n["nets"] for n in tc["nodes"]  # noqa: E731
                      if n.get("role") in ("l2-switch", "l3-switch")}
    assert dev(corp) == dev(lab)
    for n in lab["nodes"]:
        for a in n.get("install") or []:
            if "script" in a:
                assert (DEVLAB / a["script"]).is_file(), a["script"]


def test_devlab_tap_and_tfc_name_only_what_it_emits():
    from axrange.twinconfig.expand import expand_twin_config

    ir, _ = expand_twin_config("corp-twin-devlab")
    ids = {n["id"] for n in ir["layers"]["infra"]["nodes"]}
    nets = {n["id"] for n in ir["layers"]["infra"]["networks"]}
    y = YAML(typ="safe")
    for a in y.load((DEVLAB / "tfc" / "default.yaml").read_text())["traffic-profile"]["assignments"]:
        assert a["node"] in ids
    for b in y.load((DEVLAB / "tap" / "default.yaml").read_text())["tap-plan"]["bindings"]:
        assert b["selector"]["target"] in nets


def test_corp_twin_devices_plan_with_the_vrrp_and_fabric_shape(manifests):
    """The shipped twin (fixture images): VLAN953 behind the 9K pair, the
    pair's default route via hq-mx on VLAN935, the plan acyclic."""
    from axrange.twinconfig.expand import expand_twin_config

    ir, _ = expand_twin_config("corp-twin-devlab")
    nets = _nets_by_id(ir)
    assert nets["vlan953"]["gateway"] == "10.180.34.1"
    assert nets["x3850-9k01"]["gateway"] == "10.180.93.1"
    assert "hq-mx" not in {n["id"] for n in ir["layers"]["infra"]["nodes"]
                           if any(leg["net"] == "vlan953" for leg in n["nets"])}
    plan = _plan(ir, manifests, DEVLAB)
    (ci,) = _ops(plan, "configure-cloud-init", "9k01")
    ipc = ci.inputs_resolved["ip_config"]
    assert ipc["x3850-9k01"]["gateway"] == "10.180.93.1"
    assert ipc["x9k01-8325l"]["gateway"] is None
    _assert_acyclic(plan)

    # SF1 ordering: the bridges build in parallel; the 9K pair waits on all
    # of them (its VRRP needs both legs' L2 paths up, whichever layer).
    from axrange.planner.pass3_ordering import _addressed_hosts, forwarder_layers

    _layer, comp = forwarder_layers(plan)
    addressed = _addressed_hosts(plan)
    bridges = sorted(f for f in comp if f not in addressed)
    assert {"8325h-l", "8325h-r", "sw-3850"} <= set(bridges)
    preds: dict[str, set[str]] = {}
    for e in plan.edges:
        preds.setdefault(e.dst_op_id, set()).add(e.src_op_id)
    rs = {f: {op.op_id for op in plan.ops.values()
              if op.op_name == "run-script" and op.scope.get("host") == f} for f in comp}
    all_fwd_recipes = set().union(*rs.values())
    for b in bridges:
        for dst in rs[b]:
            assert not preds.get(dst, set()) & all_fwd_recipes, b
    for pair in ("9k01", "9k02"):
        assert pair in addressed
        need = set().union(*(rs[b] for b in bridges if comp[b] == comp[pair]))
        assert {"8325h-l", "8325h-r", "sw-3850"} <= {b for b in bridges
                                                      if comp[b] == comp[pair]}
        for dst in rs[pair]:
            assert need <= preds[dst], pair


def test_address_less_forwarders_never_wait_and_addressed_ones_wait_on_them(
    manifests, tmp_path,
):
    """SF1: two bridges in series (layers 0, 1) and an L3 forwarder behind
    them (layer 2), plus a second L3 forwarder at layer 3. Bridges get no
    forwarder predecessor; each L3 forwarder waits on BOTH bridges, and the
    farther L3 one on the nearer one too. Acyclic."""
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "sw.sh").write_text("#!/bin/sh\ntrue\n")
    sw = {"install": [{"script": "scripts/sw.sh"}], "image": "debian-12-cloud@v1"}
    cfg = _tc(
        [{"name": "up", "cidr": "10.3.0.0/24"},
         {"name": "a", "cidr": "10.3.0.0/24", "isolated": True},
         {"name": "b", "cidr": "10.3.0.0/24", "isolated": True},
         {"name": "c", "cidr": "10.6.0.0/24"},
         {"name": "d", "cidr": "10.8.0.0/24"}],
        [{"name": "r", "kind": "router", "image": "debian-12-cloud@v1",
          "nets": [{"segment": "up"}]},
         {"name": "br0", **sw, "nets": [{"segment": "up", "l2": True},
                                        {"segment": "a", "l2": True}]},
         {"name": "br1", **sw, "nets": [{"segment": "a", "l2": True},
                                        {"segment": "b", "l2": True}]},
         {"name": "l3a", **sw, "nets": [{"segment": "b", "ip": "10.3.0.20"},
                                        {"segment": "c", "ip": "10.6.0.2"}]},
         {"name": "l3b", **sw, "nets": [{"segment": "c", "ip": "10.6.0.3"},
                                        {"segment": "d", "ip": "10.8.0.2"}]}],
    )
    ir, _ = expand_twin_config_raw(cfg)
    plan = _plan(ir, manifests, tmp_path)
    from axrange.planner.pass3_ordering import forwarder_layers

    layer, _comp = forwarder_layers(plan)
    assert layer == {"br0": 0, "br1": 1, "l3a": 2, "l3b": 3}
    (b0,) = _ops(plan, "run-script", "br0")
    (b1,) = _ops(plan, "run-script", "br1")
    (a,) = _ops(plan, "run-script", "l3a")
    (b,) = _ops(plan, "run-script", "l3b")
    preds: dict[str, set[str]] = {}
    for e in plan.edges:
        preds.setdefault(e.dst_op_id, set()).add(e.src_op_id)
    fwd = {b0.op_id, b1.op_id, a.op_id, b.op_id}
    assert not preds.get(b0.op_id, set()) & fwd
    assert not preds.get(b1.op_id, set()) & fwd      # was: br1 after br0
    assert preds[a.op_id] & fwd == {b0.op_id, b1.op_id}
    assert preds[b.op_id] & fwd == {b0.op_id, b1.op_id, a.op_id}
    _assert_acyclic(plan)


# ---------------------------------------------------------------------------
# fix round 2: containment read, node resolution, pinned-IP uniqueness
# ---------------------------------------------------------------------------
class _SplitGA(_FakeGA):
    """`--show` says one phase, the healthcheck (a separate exec) resolved
    and asserted another -- a flip landed between the two reads."""

    def exec(self, vmid, argv, timeout_s=300):
        if argv[-1] == "--healthcheck":
            self.calls.append((vmid, list(argv)))
            return _Res(0, "phase=provision\nok   provision asserted\n")
        return super().exec(vmid, argv, timeout_s)


def test_a_healthcheck_of_another_phase_never_verifies_measure(egress_db, capsys):
    from axrange.cli import main as climain
    from axrange.cli.main import _build_parser as build_parser

    args = build_parser().parse_args(["te", "egress", "--show", "corp-twin",
                                      "--db", str(egress_db)])
    assert climain.cmd_te_egress(args, ga=_SplitGA("measure")) == 0
    assert "measure-unverified" in capsys.readouterr().out
    posture = _posture(egress_db)
    assert not posture.contained and posture.phase_applied == "measure-unverified"
    assert _live_record(egress_db)["egress_live"]["health_phase"] == "provision"


def test_one_budget_spans_every_exec(egress_db, monkeypatch):
    """SF4: `timeout_s` is the whole call's budget -- each exec gets what is
    left, and a budget spent before the healthcheck fails closed."""
    from axrange.o2control import containment
    from axrange.o2control.containment import ContainmentError, egress_live
    from axrange.planner import StateStore

    clock = [1000.0]
    monkeypatch.setattr(containment._time, "monotonic", lambda: clock[0])
    seen = []

    class _SlowGA(_FakeGA):
        def exec(self, vmid, argv, timeout_s=300):
            seen.append((argv[-1], timeout_s))
            clock[0] += 60.0  # the --show alone spends the whole 60
            return super().exec(vmid, argv, timeout_s)

    store = StateStore(str(egress_db))
    try:
        with pytest.raises(ContainmentError, match="budget ran out before `--healthcheck`"):
            egress_live(store, "corp-twin-abc123", "show", ga=_SlowGA("measure"),
                        timeout_s=60)
    finally:
        store.close()
    assert seen == [("--show", 60)]
    assert not _posture(egress_db).contained

    clock[0], seen[:] = 1000.0, []

    class _QuickGA(_FakeGA):
        def exec(self, vmid, argv, timeout_s=300):
            seen.append((argv[-1], timeout_s))
            clock[0] += 20.0
            return super().exec(vmid, argv, timeout_s)

    store = StateStore(str(egress_db))
    try:
        res = egress_live(store, "corp-twin-abc123", "show", ga=_QuickGA("measure"),
                          timeout_s=60)
    finally:
        store.close()
    assert res.contained and seen == [("--show", 60), ("--healthcheck", 40)]


def test_a_destroyed_deploy_is_refused(egress_db, capsys):
    from axrange.cli import main as climain
    from axrange.cli.main import _build_parser as build_parser
    from axrange.planner import StateStore

    store = StateStore(str(egress_db))
    store.mark_destroyed("corp-twin-abc123")
    store.close()
    ga = _FakeGA("measure")
    args = build_parser().parse_args(["te", "egress", "--show", "corp-twin-abc123",
                                      "--db", str(egress_db)])
    assert climain.cmd_te_egress(args, ga=ga) == 1
    assert "is destroyed" in capsys.readouterr().err
    assert ga.calls == []


def test_te_egress_json_prints_the_read_back_on_a_failed_flip(egress_db, capsys):
    from axrange.cli import main as climain
    from axrange.cli.main import _build_parser as build_parser

    args = build_parser().parse_args(["te", "egress", "--phase", "measure", "corp-twin",
                                      "--json", "--db", str(egress_db)])
    assert climain.cmd_te_egress(args, ga=_FakeGA("provision", flip_ok=False)) == 1
    cap = capsys.readouterr()
    doc = json.loads(cap.out)
    assert doc["recorded"] == "provision" and doc["contained"] is False
    assert "failed" in doc["error"] and "failed" in cap.err


class _Rest:
    """A recording RestClient: `/cluster/resources` answers `resources`."""

    def __init__(self, resources):
        self.resources, self.calls = resources, []

    def request(self, method, path, **kw):
        self.calls.append((method, path))
        if path == "/cluster/resources":
            if isinstance(self.resources, Exception):
                raise self.resources
            return self.resources
        if path.endswith("/agent/exec"):
            return {"pid": 7}
        if path.endswith("/agent/exec-status"):
            return {"exited": 1, "exitcode": 0, "out-data": "phase=measure\n"}
        raise AssertionError(path)


@pytest.mark.parametrize("resources, node", [
    ([{"vmid": 1, "node": "a"}, {"vmid": 4242, "node": "pve-b", "name": "hq-mx"}], "pve-b"),
    ([{"vmid": 1, "node": "a"}], "dflt"),                  # not found -> fallback
    (OSError("cluster down"), "dflt"),                      # lookup unavailable
])
def test_the_egress_guest_agent_execs_on_the_routers_own_node(monkeypatch, resources, node):
    """SF2: a router placed off DEFAULT_NODE must be read on ITS node -- the
    default node answers 'no such VM', which the gate reads as NOT contained."""
    from axrange.adapter_proxmox import config as apcfg
    from axrange.adapter_proxmox import rest as aprest
    from axrange.cli import main as climain

    cfg = apcfg.AdapterConfig(proxmox_api_url="https://pve:8006", default_node="dflt")
    monkeypatch.setattr("axrange.adapter_proxmox.load_config", lambda _p=None: cfg)
    rest = _Rest(resources)
    monkeypatch.setattr(aprest, "RestClient", lambda _cfg: rest)
    ga = climain._egress_guest_agent(climain._build_parser().parse_args(
        ["te", "egress", "--show"]))
    res = ga.exec(4242, ["/usr/local/sbin/axr-egress-phase.sh", "--show"], timeout_s=30)
    assert res.exit_code == 0
    execs = [p for _m, p in rest.calls if "/agent/" in p]
    assert execs and all(p.startswith(f"/nodes/{node}/qemu/4242/") for p in execs)
    ga.exec(4242, ["x"], timeout_s=30)  # resolved once, then cached
    assert sum(p == "/cluster/resources" for _m, p in rest.calls) == 1


def test_two_legs_pinning_one_address_in_a_cidr_are_refused():
    """SF5: the anchor and its `isolated:` extension share a CIDR (one
    broadcast domain across the bridge): one pinned address, two NICs."""
    segs = [{"name": "a", "cidr": "10.1.0.0/24"},
            {"name": "b", "cidr": "10.1.0.0/24", "isolated": True}]
    with pytest.raises(TwinConfigShapeError, match="both pin 10.1.0.9"):
        expand_twin_config_raw(_tc(segs, [
            {"name": "x", "image": "i", "nets": [{"segment": "a", "ip": "10.1.0.9"}]},
            {"name": "y", "image": "i", "nets": [{"segment": "b", "ip": "10.1.0.9"}]}]))
    with pytest.raises(TwinConfigShapeError, match="both pin"):
        expand_twin_config_raw(_tc(segs[:1], [
            {"name": "x", "image": "i", "count": 2,
             "nets": [{"segment": "a", "ip": "10.1.0.9"}]}]))
    # a router pinned to its segment's own `gateway:` is the same address, legitimately
    expand_twin_config_raw(_tc(
        [{"name": "a", "cidr": "10.1.0.0/24", "gateway": "10.1.0.1"}],
        [{"name": "r", "kind": "router", "image": "i",
          "nets": [{"segment": "a", "ip": "10.1.0.1"}]}]))


def test_l2_false_with_ip_none_is_a_contradiction():
    cfg = _tc([{"name": "a", "cidr": "10.1.0.0/24"}],
              [{"name": "s", "image": "i", "nets": [{"segment": "a", "ip": "none",
                                                     "l2": False}]}])
    with pytest.raises(TwinConfigShapeError, match="`l2: false` with `ip: none`"):
        expand_twin_config_raw(cfg)


@pytest.mark.parametrize("stem, gate", [("default", None), ("gate90", "0.9")])
def test_corp_twin_tap_and_tfc_name_only_what_their_gate_emits(stem, gate):
    """tfc/tap `<stem>.yaml` is applied against a deploy at `gate`: a `node:`
    that is not a host is a hard error at apply, a binding on a missing
    segment a resolve-time one. The 0.9 variant exists because dev45 cannot
    hold the default 0.8 expansion (live, 2026-09-28)."""
    from axrange.twinconfig.expand import expand_twin_config

    overrides = {"DEPLOY_MIN_OCCUPANCY": gate} if gate else None
    ir, params = expand_twin_config("corp-twin", param_overrides=overrides)
    assert float(params["DEPLOY_MIN_OCCUPANCY"]) == float(gate or 0.8)
    ids = {n["id"]: n for n in ir["layers"]["infra"]["nodes"]}
    nets = {n["id"] for n in ir["layers"]["infra"]["networks"]}
    roles = {n.get("role") for n in ids.values()}
    y = YAML(typ="safe")
    tfc = y.load((CORP / "tfc" / f"{stem}.yaml").read_text())["traffic-profile"]["assignments"]
    for a in tfc:
        assert (a["node"] in ids) if "node" in a else (a["role"] in roles), a
    for b in y.load((CORP / "tap" / f"{stem}.yaml").read_text())["tap-plan"]["bindings"]:
        assert b["selector"]["target"] in nets, b
    if stem == "gate90":
        # Same mix intent: every persona the default drives is still driven.
        default = y.load((CORP / "tfc" / "default.yaml").read_text())["traffic-profile"]["assignments"]
        assert {a["persona"] for a in tfc} == {a["persona"] for a in default}


def test_corp_twin_gate90_variants_are_declared():
    from axrange.twinconfig.manifest import load_twin_manifest

    m = load_twin_manifest(CORP)
    assert m.tier_path("tfc", "gate90").name == "gate90.yaml"
    assert m.tier_path("tap", "gate90").name == "gate90.yaml"
