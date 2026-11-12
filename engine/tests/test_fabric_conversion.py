"""Whole-fabric conversion of bridge VMs to native switches
(`docs/design/native-switches.md` §12 S5; `axrange/twinconfig/fabric.py`):
the port-table reader, the plan on corp-twin (the twin it exists for), the
converted twin validating and planning on a native-switch platform at three
deploy gates, the refusals, and `POST /api/fabric-conversion`."""
from __future__ import annotations

import io
import json
from collections import Counter
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from axrange.ir.parser import parse_ir
from axrange.switchmodel import NATIVE_SWITCH_CAP
from axrange.twinconfig.expand import expand_twin_config_raw
from axrange.twinconfig.fabric import (
    FabricConversionError,
    apply_plan,
    bridge_recipe,
    plan_fabric_conversion,
    read_port_tables,
)

ROOT = Path(__file__).resolve().parents[2]
CORP = ROOT / "catalog" / "twins" / "corp-twin"
UI_FIXTURE = ROOT / "ui" / "test" / "fixtures" / "corp-twin-fabric-plan.json"
CAPS = frozenset({NATIVE_SWITCH_CAP})


def _corp_files() -> dict[str, str]:
    files = {"te.yaml": (CORP / "te.yaml").read_text(),
             "scripts/serve-l2switch.sh": (CORP / "scripts/serve-l2switch.sh").read_text()}
    for p in sorted((CORP / "tap").glob("*.yaml")):
        files[f"tap/{p.name}"] = p.read_text()
    return files


def _corp_plan():
    f = _corp_files()
    taps = {k: v for k, v in f.items() if k.startswith("tap/")}
    return plan_fabric_conversion(f["te.yaml"], f["scripts/serve-l2switch.sh"], taps)


# ---- the port table ----------------------------------------------------------

def test_reads_every_corp_twin_port_table():
    tables = read_port_tables((CORP / "scripts/serve-l2switch.sh").read_text())
    assert set(tables) == {"mx-sw", "ms120", "sgs-5240", "axeprodsw", "cos-sw-r32e01", "labswitch",
                           "hwlab2960s", "sw-3850", "8325h-l", "8325h-r"}
    ms = tables["ms120"]
    assert ms.prio == 8192
    by_leg = {p.leg: p for p in ms.ports}
    assert by_leg["vlan20"].mgmt and by_leg["vlan20"].vid == 20
    assert by_leg["sfp26"].mode == "trunk" and by_leg["sfp26"].native == 1 and by_leg["sfp26"].cost == 200
    assert by_leg["sfp26"].vids == (20, 32, 40, 48, 52, 64, 192, 880, 1010, 935)
    assert by_leg["p17-hwlab2960s"].down


def test_a_bad_port_spec_is_refused():
    with pytest.raises(FabricConversionError, match="neither access: nor trunk:"):
        read_port_tables("x) cat <<'EOT'\nbridge prio=1\neth0 hybrid:3\nEOT\n")


# ---- the corp-twin plan --------------------------------------------------------

def test_corp_twin_plan_shape():
    plan = _corp_plan()
    assert len(plan.bridges) == 10
    assert all(sw["role"] == "l2-switch" and sw["stp"] is None for sw in plan.switches)
    ms = next(sw for sw in plan.switches if sw["name"] == "ms120")
    assert ms["nets"] == [{"segment": "mx-vlan20", "ip": "10.180.16.15"}]   # S2: kept, NSW-080
    assert set(plan.remove_segments) == {
        "sfp25", "sfp26", "t-ms120-sgs5240", "t-ms120-axeprod", "t-ms120-cos", "t-ms120-labsw",
        "t-ms120-3850", "vlan1", "vlan20", "vlan32", "p17-hwlab2960s", "vlan40", "vlan48",
        "vlan52", "vlan64", "vlan192", "x3850-9k01", "x3850-9k02", "x9k01-8325l",
        "x9k02-8325r", "isl-8325"}
    links = {(lk["a"]["device"], lk["a"]["port"]): lk for lk in plan.links}
    sfp25, sfp26 = links[("mx-sw", "sfp25")], links[("mx-sw", "sfp26")]
    assert sfp25["mode"] == "trunk" and sfp25["native"] == "mx-vlan1" and sfp25["state"] is None
    assert "mx-vlan935" in sfp25["vlans"] and len(sfp25["vlans"]) == 10
    assert sfp26["state"] == "blocked"                       # the higher-cost twin STP blocked
    assert links[("ms120", "p17-hwlab2960s")]["state"] == "down"
    assert links[("mx-sw", "mx-vlan20")]["b"] == {"device": "hq-mx", "port": "eth3"}
    # 9k02 keeps its pinned VRRP addresses on the survivors
    nets = {x["node"]: x["nets"] for x in plan.node_nets}
    assert nets["9k02"] == [{"segment": "mx-vlan935", "ip": "10.180.93.3"},
                            {"segment": "vlan953", "ip": "10.180.34.3"}]
    assert [p["rule_id"] for p in plan.problems] == ["NSW-080"]


def test_corp_twin_taps_become_one_span_and_retargeted_segments():
    taps = {t.path: t for t in _corp_plan().taps}
    d = taps["tap/default.yaml"]
    assert d.retarget == [{"index": 2, "target": "mx-vlan20"}, {"index": 3, "target": "mx-vlan1"}]
    assert d.remove == [4, 5, 6, 7, 8, 9, 10, 11]
    (span,) = d.add_spans
    assert span["switch"] == "ms120" and span["sensors"] == ["XPress-SW-A"]
    assert [s["port"] for s in span["sources"]] == [
        "sfp25", "sfp26", "t-ms120-sgs5240", "t-ms120-axeprod", "t-ms120-cos", "t-ms120-labsw",
        "t-ms120-3850", "p17-hwlab2960s"]
    assert taps["tap/fanout-suricata.yaml"].add_spans[0]["mode"] == "fanout"
    up = taps["tap/uplink-span.yaml"]
    assert up.add_spans == [{"switch": "ms120", "sources": [{"port": "sfp25", "dir": "both"}],
                             "sensors": ["XPress-SW-A"], "mode": None}]


@pytest.mark.parametrize("gate", [None, "0.9", "0.5"])
def test_converted_corp_twin_validates_and_plans_native(gate):
    from axrange.manifest.loader import load_registry
    from axrange.planner import build_plan
    from axrange.planner.cluster import ClusterContext
    from axrange.validator.engine import validate_full

    plan = _corp_plan()
    raw = apply_plan(YAML(typ="safe").load((CORP / "te.yaml").read_text()), plan)
    ir_dict, _ = expand_twin_config_raw(
        raw, param_overrides={"DEPLOY_MIN_OCCUPANCY": gate} if gate else None)
    buf = io.StringIO()
    YAML(typ="safe").dump(ir_dict, buf)
    manifests = load_registry(root=Path(__file__).parent / "fixtures")
    result = validate_full(None, text=buf.getvalue(), manifests=manifests, platform_caps=CAPS)
    nsw_errors = [f for f in result.errors if f.rule_id.startswith(("NSW", "CLU-080"))]
    assert nsw_errors == []
    parsed = parse_ir(text=buf.getvalue())
    p = build_plan(parsed.ir, manifests, ClusterContext.single_node("px1", platform_caps=CAPS),
                   raw_ir=parsed.raw, scenario_dir=CORP)
    ops = Counter(op.op_name for op in p.ops.values())
    assert ops["create-switch"] == 10 and ops["link-switches"] == 9
    assert ops["set-port-state"] == 0      # sfp26 is a switch link: link-switches blocks it


def test_corp_twin_files_are_untouched_by_planning():
    before = _corp_files()
    _corp_plan()
    assert _corp_files() == before


def test_ui_fixture_is_the_current_plan():
    """The canvas test (`ui/test/canvas.fabric.test.ts`) applies this plan to
    corp-twin's files; it must be what the engine answers today."""
    current = json.loads(json.dumps(_corp_plan().as_dict()))
    assert json.loads(UI_FIXTURE.read_text()) == current, (
        "ui/test/fixtures/corp-twin-fabric-plan.json is stale: regenerate it from "
        "plan_fabric_conversion (tests/test_fabric_conversion.py::_corp_plan)")


# ---- refusals ------------------------------------------------------------------

def test_leg_order_must_match_the_table():
    f = _corp_files()
    te = f["te.yaml"].replace(
        "        - { segment: t-ms120-sgs5240, l2: true }\n        - { segment: vlan64, l2: true }",
        "        - { segment: vlan64, l2: true }\n        - { segment: t-ms120-sgs5240, l2: true }")
    assert te != f["te.yaml"]
    with pytest.raises(FabricConversionError, match="order is load-bearing"):
        plan_fabric_conversion(te, f["scripts/serve-l2switch.sh"])


def test_a_twin_without_bridge_vms_is_refused():
    te = (ROOT / "catalog/twins/switch-test/te.yaml").read_text()
    assert bridge_recipe(YAML(typ="safe").load(te)) is None
    with pytest.raises(FabricConversionError, match="no bridge VM"):
        plan_fabric_conversion(te, "")


def test_a_vlan_with_two_anchors_is_refused():
    f = _corp_files()
    te = f["te.yaml"].replace(
        "    - { name: vlan64, cidr: 10.180.64.0/20, zone_type: simple, isolated: true }",
        "    - { name: vlan64, cidr: 10.180.64.0/20, zone_type: simple }")
    with pytest.raises(FabricConversionError, match="VLAN 64 .* exactly one"):
        plan_fabric_conversion(te, f["scripts/serve-l2switch.sh"])


# ---- the route -----------------------------------------------------------------

def test_route_answers_the_plan_and_refuses_a_twin_without_bridges(tmp_path):
    from axrange.api import create_app
    from tests.test_api import _settings

    try:
        from starlette.testclient import TestClient
    except ImportError:  # pragma: no cover
        pytest.skip("no web stack")
    c = TestClient(create_app(_settings(tmp_path)))
    files = _corp_files()
    r = c.post("/api/fabric-conversion", json={"document": {"id": "corp-twin", "files": files}})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["recipe"] == "scripts/serve-l2switch.sh" and len(body["bridges"]) == 10
    files.pop("scripts/serve-l2switch.sh")
    r = c.post("/api/fabric-conversion", json={"document": {"id": "corp-twin", "files": files}})
    assert r.status_code == 422 and "lacks" in r.text
    st = {"te.yaml": (ROOT / "catalog/twins/switch-test/te.yaml").read_text()}
    r = c.post("/api/fabric-conversion", json={"document": {"id": "switch-test", "files": st}})
    assert r.status_code == 422 and "no bridge VM" in r.text
