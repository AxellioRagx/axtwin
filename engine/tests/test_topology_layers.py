"""The device tree, track 2 (`docs/design/topology-layers.md` §2, §3, §5,
§10; Authority: user, 2026-11-07): `kind: switch` on a twin-config group,
`links[]`, `members[]`, the provenance fields, the expander's mapping of
them onto the IR, the seven validator rules (STR-210/220, REF-270/280,
NET-120/130/140), the planner's imageless-switch rule, the topology DTO's
links and the twin manifest's `vantage`.
"""
from __future__ import annotations

import textwrap

import pytest

from axrange.ir.parser import parse_ir
from axrange.planner import build_plan
from axrange.planner.cluster import ClusterContext
from axrange.twinconfig.errors import TwinConfigShapeError
from axrange.twinconfig.expand import expand_twin_config_raw
from axrange.validator.engine import validate_full, validate_structural


# ---------------------------------------------------------------------------
# Twin config -> IR
# ---------------------------------------------------------------------------
def _tree_config(**overrides) -> dict:
    """canvas-test's shape with one imageless switch between the router and
    the workstations, a `down` access link, and a member override."""
    base = {
        "twin_config": {
            "id": "unit-tree",
            "segments": [
                {"name": "office", "cidr": "10.71.10.0/24"},
                {"name": "servers", "cidr": "10.71.20.0/24"},
            ],
            "nodes": [
                {"name": "ws", "role": "corp-workstation", "image": "img@v1",
                 "requires": ["linux"], "count": 2, "net": "office",
                 "members": [{"index": 2, "role": "jump-host", "source": "authored"}]},
                {"name": "fs", "role": "file-server", "image": "img@v1",
                 "requires": ["linux"], "nets": [{"segment": "servers", "ip": "10.71.20.10"}]},
                {"name": "sw", "kind": "switch", "role": "l2-switch", "source": "authored"},
                {"name": "gw", "kind": "router", "image": "img@v1", "requires": ["linux"],
                 "nets": [{"segment": "office"}, {"segment": "servers"}]},
            ],
            "links": [
                {"a": {"device": "gw", "port": "eth1"}, "b": {"device": "sw", "port": "SFP1"},
                 "mode": "trunk", "native": "office", "vlans": ["office"], "source": "authored"},
                {"a": {"device": "sw", "port": "P24"}, "b": {"device": "fs", "port": "eth0"},
                 "mode": "access", "vlans": ["servers"], "state": "down"},
            ],
        }
    }
    base["twin_config"].update(overrides)
    return base


def _nodes(ir: dict) -> dict[str, dict]:
    return {n["id"]: n for n in ir["layers"]["infra"]["nodes"]}


def test_imageless_switch_expands_with_l2_legs_from_its_up_links():
    ir, _ = expand_twin_config_raw(_tree_config())
    sw = _nodes(ir)["sw"]
    assert sw["kind"] == "switch"
    assert "image" not in sw  # an imageless switch emits no image key at all
    # the trunk (up) carries office; the down access link carries nothing
    assert sw["nets"] == [{"net": "office"}]
    assert sw["source"] == "authored"
    assert ir["scenario_schema_version"] == 4


def test_links_reach_the_ir_in_authored_order_with_their_fields():
    ir, _ = expand_twin_config_raw(_tree_config())
    links = ir["layers"]["infra"]["links"]
    assert [l["a"]["device"] for l in links] == ["gw", "sw"]
    assert links[0] == {
        "a": {"device": "gw", "port": "eth1"}, "b": {"device": "sw", "port": "SFP1"},
        "mode": "trunk", "native": "office", "vlans": ["office"], "source": "authored",
    }
    assert links[1]["state"] == "down" and "source" not in links[1]


def test_member_override_lands_on_the_instance_and_the_group_keeps_its_role():
    ir, _ = expand_twin_config_raw(_tree_config())
    nodes = _nodes(ir)
    assert nodes["ws01"]["role"] == "corp-workstation" and "source" not in nodes["ws01"]
    assert nodes["ws02"]["role"] == "jump-host" and nodes["ws02"]["source"] == "authored"


def test_host_end_link_attaches_the_host_to_the_link_segment():
    cfg = _tree_config()
    # an UP access link from the switch to the file server on `office`: the
    # file server gains an addressed leg on office beside its servers leg.
    cfg["twin_config"]["links"][1] = {
        "a": {"device": "sw", "port": "P24"}, "b": {"device": "fs", "port": "eth1"},
        "mode": "access", "vlans": ["office"],
    }
    ir, _ = expand_twin_config_raw(cfg)
    fs = _nodes(ir)["fs"]
    assert [leg["net"] for leg in fs["nets"]] == ["servers", "office"]
    assert fs["nets"][1]["ip"].startswith("10.71.10.")


def test_imaged_switch_keeps_its_image_and_declared_l2_legs_first():
    cfg = _tree_config()
    cfg["twin_config"]["nodes"][2] = {
        "name": "sw", "kind": "switch", "image": "img@v1", "requires": ["linux"],
        "nets": [{"segment": "servers"}],
    }
    ir, _ = expand_twin_config_raw(cfg)
    sw = _nodes(ir)["sw"]
    assert sw["image"] == "img@v1"
    # a switch leg with no address is L2 (no ip key); link-carried segments follow
    assert sw["nets"] == [{"net": "servers"}, {"net": "office"}]


def test_config_without_device_tree_fields_still_declares_schema_1():
    cfg = _tree_config(links=[])
    cfg["twin_config"]["nodes"] = [n for n in cfg["twin_config"]["nodes"] if n["name"] != "sw"]
    cfg["twin_config"]["nodes"][0].pop("members")
    ir, _ = expand_twin_config_raw(cfg)
    assert ir["scenario_schema_version"] == 1
    assert "links" not in ir["layers"]["infra"]
    assert not any("source" in n for n in ir["layers"]["infra"]["nodes"])


@pytest.mark.parametrize("mutate, needle", [
    (lambda tc: tc["nodes"][0].update(image=None), "needs an `image:`"),
    (lambda tc: tc["nodes"][0].update(kind="bridge"), "kind 'bridge' is not one of"),
    (lambda tc: tc["nodes"][0].update(source="guessed"), "source 'guessed' is not one of"),
    (lambda tc: tc["nodes"][0].update(source="authored", confidence=0.5), "confidence is a cast's measure"),
    (lambda tc: tc["nodes"][0].update(source="discovered", confidence=1.5), "outside 0.0..1.0"),
    (lambda tc: tc["nodes"][0]["members"].append({"index": 3, "role": "x"}), "index 3 is outside 1..2"),
    (lambda tc: tc["nodes"][0]["members"].append({"index": 2, "role": "x"}), "index 2 is overridden twice"),
    (lambda tc: tc["links"][0].update(mode="hub"), "mode 'hub' is not one of"),
    (lambda tc: tc["links"][0].update(state="flapping"), "state 'flapping' is not one of"),
    (lambda tc: tc["links"][1].update(native="office"), "`native:` is a trunk's field"),
    (lambda tc: tc["links"][1].update(vlans=["office", "servers"]), "exactly one segment"),
    (lambda tc: tc["links"][0].update(vlans=["nowhere"]), "unknown segment 'nowhere'"),
    (lambda tc: tc["links"][0].update(b={"device": "gw", "port": "eth2"}), "both ends are 'gw'"),
    (lambda tc: tc["links"][0].update(b={"device": "nobody", "port": "p1"}), "not an emitted node"),
    (lambda tc: tc["links"][0].update(b={"device": "ws", "port": "p1"}), "count > 1 group"),
    (lambda tc: tc["links"][1].update(a={"device": "sw", "port": "SFP1"}), "already used by"),
])
def test_shape_errors_name_the_problem(mutate, needle):
    cfg = _tree_config()
    mutate(cfg["twin_config"])
    with pytest.raises(TwinConfigShapeError, match=needle):
        expand_twin_config_raw(cfg)


def test_switch_with_nothing_to_stand_on_is_a_shape_error():
    cfg = _tree_config(links=[])
    with pytest.raises(TwinConfigShapeError, match="switch with no 'net:', 'nets:' and no link"):
        expand_twin_config_raw(cfg)


def test_link_to_a_gated_out_device_is_dropped_with_a_reason():
    cfg = _tree_config()
    cfg["twin_config"]["nodes"][1]["observation"] = {
        "windows_seen": 1, "windows_total": 9, "occupancy": 1 / 9,
    }
    warnings: list[str] = []
    ir, _ = expand_twin_config_raw(cfg, warnings=warnings)
    assert "fs" not in _nodes(ir)
    assert [l["b"]["device"] for l in ir["layers"]["infra"]["links"]] == ["sw"]
    assert any("link 'sw#P24 <-> fs#eth0'" in w and "not emitted" in w for w in warnings)


def test_router_leg_on_a_switch_is_still_refused():
    cfg = _tree_config()
    cfg["twin_config"]["nodes"][3]["nets"] = [{"segment": "office", "l2": True}, {"segment": "servers"}]
    with pytest.raises(TwinConfigShapeError, match="kind: router` leg"):
        expand_twin_config_raw(cfg)


# ---------------------------------------------------------------------------
# The validator's seven rules on the IR
# ---------------------------------------------------------------------------
_IR = textwrap.dedent("""
    scenario_schema_version: 4
    scenario: {{ id: m, version: 1 }}
    layers:
      infra:
        networks:
          - {{ id: office, cidr: 10.71.10.0/24 }}
          - {{ id: servers, cidr: 10.71.20.0/24 }}
          {extra_nets}
        nodes:
          - {{ id: gw, kind: router, nets: [{{ net: office, ip: 10.71.10.1 }}], image: vyos@1.5, requires: [linux] }}
          - {{ id: ws, kind: host, net: office, image: win11-finance@v4, requires: [windows] {ws_extra} }}
          - {{ id: sw, kind: switch, nets: [{{ net: office }}] {sw_extra} }}
          {extra_nodes}
        links:
          {links}
      apps: []
      activity: []
    bindings: []
""").strip()

_LINK_OK = "- { a: { device: gw, port: eth1 }, b: { device: sw, port: SFP1 }, mode: trunk, native: office, vlans: [office] }"


def _ir(links: str = _LINK_OK, *, extra_nets: str = "", extra_nodes: str = "",
        ws_extra: str = "", sw_extra: str = "") -> str:
    return _IR.format(links=links, extra_nets=extra_nets, extra_nodes=extra_nodes,
                      ws_extra=ws_extra, sw_extra=sw_extra)


def _ids(result) -> set[str]:
    return {f.rule_id for f in result.findings}


def _msgs(result, rule: str) -> list[str]:
    return [f.message for f in result.findings if f.rule_id == rule]


def test_schema_version_4_is_recognized_and_a_clean_tree_validates(manifests):
    result = validate_full(text=_ir(), manifests=manifests)
    assert {"STR-020", "STR-210", "STR-220", "REF-270", "REF-280", "NET-120", "NET-130", "NET-140"} \
        .isdisjoint(_ids(result)), result.findings


@pytest.mark.parametrize("link, detail", [
    ("- { a: { device: gw, port: eth1 }, b: { device: sw, port: SFP1 }, mode: hub, vlans: [office] }", "mode 'hub'"),
    ("- { a: { device: gw, port: eth1 }, b: { device: sw, port: SFP1 }, mode: trunk, vlans: [office], state: flapping }", "state 'flapping'"),
    ("- { a: { device: gw, port: eth1 }, b: { device: sw, port: SFP1 }, mode: access, native: office, vlans: [office] }", "native: on a non-trunk"),
    ("- { a: { device: gw, port: eth1 }, b: { device: sw, port: SFP1 }, mode: access, vlans: [office, servers] }", "exactly one network"),
    ("- { a: { device: gw, port: eth1 }, b: { device: sw }, mode: trunk, vlans: [office] }", "b.port missing"),
    ("- { a: { device: gw, port: eth1 }, b: { device: sw, port: SFP1 }, mode: trunk, vlans: [] }", "non-empty list"),
])
def test_str_210_names_the_malformed_field(link, detail):
    result = validate_structural(text=_ir(link))
    assert "STR-210" in _ids(result)
    assert any(detail in m for m in _msgs(result, "STR-210")), _msgs(result, "STR-210")


@pytest.mark.parametrize("kw, detail", [
    ({"ws_extra": ", source: guessed"}, "source 'guessed'"),
    ({"ws_extra": ", source: authored, confidence: 0.4"}, "only discovered / unconfirmed"),
    ({"ws_extra": ", source: discovered, confidence: 7"}, "not a number in 0.0..1.0"),
    ({"extra_nets": "- { id: lab, cidr: 10.71.30.0/24, source: maybe }"}, "source 'maybe'"),
])
def test_str_220_provenance_shape(kw, detail):
    result = validate_structural(text=_ir(**kw))
    assert "STR-220" in _ids(result)
    assert any(detail in m for m in _msgs(result, "STR-220")), _msgs(result, "STR-220")


def test_str_220_accepts_a_cast_verdict_with_confidence():
    result = validate_structural(text=_ir(ws_extra=", source: unconfirmed, confidence: 0.3, vantage: 'X:1-4'"))
    assert "STR-220" not in _ids(result)


def test_ref_270_unknown_device():
    link = "- { a: { device: gw, port: eth1 }, b: { device: ghost, port: SFP1 }, mode: trunk, vlans: [office] }"
    result = validate_structural(text=_ir(link))
    assert "REF-270" in _ids(result)
    assert "links[0].b.device 'ghost'" in _msgs(result, "REF-270")[0]


def test_ref_280_unknown_network_in_vlans_and_native():
    link = "- { a: { device: gw, port: eth1 }, b: { device: sw, port: SFP1 }, mode: trunk, native: nowhere, vlans: [office, elsewhere] }"
    result = validate_structural(text=_ir(link))
    msgs = _msgs(result, "REF-280")
    assert len(msgs) == 2 and "'nowhere'" in msgs[0] and "'elsewhere'" in msgs[1]


def test_net_120_port_reused_on_one_device(manifests):
    links = (
        _LINK_OK + "\n      "
        "- { a: { device: sw, port: SFP1 }, b: { device: ws, port: eth0 }, mode: access, vlans: [office] }"
    )
    result = validate_full(text=_ir(links), manifests=manifests)
    assert "NET-120" in _ids(result)
    assert "'sw' port 'SFP1'" in _msgs(result, "NET-120")[0]


def test_net_130_both_ends_one_device(manifests):
    link = "- { a: { device: sw, port: P1 }, b: { device: sw, port: P2 }, mode: trunk, vlans: [office] }"
    result = validate_full(text=_ir(link), manifests=manifests)
    assert "NET-130" in _ids(result)
    assert "both ends are device 'sw'" in _msgs(result, "NET-130")[0]


def test_net_130_isolated_network_needs_exactly_one_anchor(manifests):
    orphan = "- { id: far, cidr: 10.71.40.0/24, isolated: true }"
    result = validate_full(text=_ir(extra_nets=orphan), manifests=manifests)
    assert any("'far' is isolated" in m and "found 0" in m for m in _msgs(result, "NET-130"))
    anchored = "- { id: far, cidr: 10.71.10.0/24, isolated: true }"
    result = validate_full(text=_ir(extra_nets=anchored), manifests=manifests)
    assert "NET-130" not in _ids(result)


def test_net_140_parked_switch_warns_only(manifests):
    parked = "- { id: lonely, kind: switch, nets: [{ net: office }] }"
    result = validate_full(text=_ir(extra_nodes=parked), manifests=manifests)
    found = [f for f in result.findings if f.rule_id == "NET-140"]
    assert len(found) == 1 and found[0].severity.value == "warning"
    assert "'lonely'" in found[0].message


# ---------------------------------------------------------------------------
# The planner: an imageless switch emits no VM; an imaged one is a VM
# ---------------------------------------------------------------------------
def _plan(text: str, manifests):
    parsed = parse_ir(text=text)
    ctx = ClusterContext.single_node("pxtest1", sensor_transport_ip="10.255.255.10")
    plan = build_plan(parsed.ir, manifests, ctx, raw_ir=parsed.raw)
    plan.freeze()
    return plan


def _clone_hosts(plan) -> set[str]:
    return {op.scope.get("host") for op in plan.ops.values() if op.op_name == "clone-vm"}


def test_imageless_switch_emits_no_clone_vm(manifests):
    plan = _plan(_ir(), manifests)
    assert _clone_hosts(plan) == {"gw", "ws"}
    assert not any("sw" in op.op_id.split(":") for op in plan.ops.values())


def test_imaged_switch_is_cloned_like_a_host(manifests):
    plan = _plan(_ir(sw_extra=", image: win11-finance@v4, requires: [windows]"), manifests)
    assert _clone_hosts(plan) == {"gw", "ws", "sw"}
    clone = next(op for op in plan.ops.values()
                 if op.op_name == "clone-vm" and op.scope.get("host") == "sw")
    assert clone.inputs_resolved["node_kind"] == "switch"


# ---------------------------------------------------------------------------
# The topology DTO carries links and provenance; the manifest carries vantage
# ---------------------------------------------------------------------------
def test_topology_dto_lists_links_and_node_provenance(tmp_path, manifests):
    from axrange.api.dtos import TopologyDTO, TopologyLinkDTO, TopologyNodeDTO

    # the DTO shape itself: a link row and a node's provenance fields
    link = TopologyLinkDTO(a="gw", a_port="eth1", b="sw", b_port="SFP1", mode="trunk",
                           vlans=["office"], source="discovered", confidence=0.6)
    node = TopologyNodeDTO(id="sw", type="switch", role="l2-switch", source="unconfirmed")
    dto = TopologyDTO(scenario_id="m", nodes=[node], edges=[], links=[link])
    dumped = dto.model_dump()
    assert dumped["links"][0]["state"] == "up" and dumped["links"][0]["native"] is None
    assert dumped["nodes"][0]["source"] == "unconfirmed"


def test_manifest_vantage_is_optional_and_a_tuple(tmp_path):
    from axrange.twinconfig.manifest import load_twin_manifest

    d = tmp_path / "vt"
    d.mkdir()
    (d / "te.yaml").write_text(
        "twin_config:\n  id: vt\n  segments: [{name: a, cidr: 10.9.0.0/24}]\n"
        "  nodes: [{name: h, image: img@v1, requires: [linux], net: a}]\n"
    )
    (d / "twin.yaml").write_text(
        "twin:\n  id: vt\n  containment: uncontained\n"
        "  vantage: ['XPress-SW-A:1-4', 'XPress-SW-A:5-8']\n"
        "  tiers: {te: {default: te.yaml}}\n"
    )
    m = load_twin_manifest(d).manifest
    assert m.vantage == ("XPress-SW-A:1-4", "XPress-SW-A:5-8")
    (d / "twin.yaml").write_text(
        "twin:\n  id: vt\n  containment: uncontained\n  tiers: {te: {default: te.yaml}}\n"
    )
    assert load_twin_manifest(d).manifest.vantage == ()


def test_shipped_switch_test_fixture_expands_validates_and_plans(manifests):
    """`catalog/twins/switch-test`, the dev45 fixture: canvas-test's shape
    plus an imageless switch -- the deploy is canvas-test's (four VMs)."""
    from pathlib import Path

    from ruamel.yaml import YAML

    te = Path(__file__).resolve().parents[2] / "catalog" / "twins" / "switch-test" / "te.yaml"
    raw = YAML(typ="safe").load(te.read_text())
    ir, _ = expand_twin_config_raw(raw)
    nodes = _nodes(ir)
    assert nodes["access-sw"]["nets"] == [{"net": "office"}] and "image" not in nodes["access-sw"]
    assert nodes["corp-workstation02"]["role"] == "jump-host"
    assert ir["scenario_schema_version"] == 4


# ---------------------------------------------------------------------------
# The assertions seam, version 2, as the API's writer reads it
# ---------------------------------------------------------------------------
def test_console_files_reads_a_version_1_document_and_writes_version_2():
    from axrange.api.console_files import ACCEPTED_SCHEMAS, validate_assertions

    assert ACCEPTED_SCHEMAS == ("axr-assertions/1", "axr-assertions/2")
    doc, warnings = validate_assertions({
        "schema": "axr-assertions/1", "imprint": "t", "as_of": "2026-09-04T18:00:00Z",
        "source": "operator: raghu",
        "add": [{"kind": "host", "key": "address:10.9.0.4", "segment": "10.9.0.0/24", "note": "n"}],
    }, "t", where="test")
    assert doc["schema"] == "axr-assertions/2" and doc["confirm"] == [] and doc["doubt"] == []
    assert not warnings


def test_console_files_validates_the_device_tree_records():
    import pytest as _pytest

    from axrange.api.console_files import ConsoleFileShapeError, validate_assertions

    def put(**blocks):
        return validate_assertions({
            "schema": "axr-assertions/2", "imprint": "t", "as_of": "2026-09-04T18:00:00Z",
            "source": "operator: raghu", **blocks}, "t", where="test")[0]

    link_key = "address:10.9.0.4#eth0|mac:aa:bb:cc:dd:ee:01#SFP25"  # the sorted, canonical form
    doc = put(
        add=[
            {"kind": "device", "key": "mac:aa:bb:cc:dd:ee:01", "device_kind": "switch",
             "segment": "10.9.0.0/24", "note": "n"},
            {"kind": "link", "key": "address:10.9.0.4#eth0|mac:aa:bb:cc:dd:ee:01#SFP25",
             "mode": "trunk", "vlans": ["10.9.1.0/24", "10.9.0.0/24"], "note": "n"},
            {"kind": "tap", "key": "port:mac:aa:bb:cc:dd:ee:01#SFP25", "sensors": ["x", "x"],
             "note": "n"},
        ],
        override=[
            {"key": link_key, "field": "state", "value": "down", "note": "n"},
            {"key": "address:10.9.0.4", "field": "group", "value": "backup", "note": "n"},
        ],
        confirm=[{"key": "10.9.0.0/24", "note": "n"}],
        doubt=[{"key": link_key, "note": "n"}],
    )
    link = next(r for r in doc["add"] if r["kind"] == "link")
    assert link["key"] == link_key, "ends sorted into the canonical order"
    assert link["vlans"] == ["10.9.0.0/24", "10.9.1.0/24"]
    tap = next(r for r in doc["add"] if r["kind"] == "tap")
    assert tap["sensors"] == ["x"]
    assert doc["override"][0]["kind"] == "link"
    assert doc["confirm"][0]["kind"] == "segment" and doc["doubt"][0]["kind"] == "link"

    with _pytest.raises(ConsoleFileShapeError, match="device_kind"):
        put(add=[{"kind": "device", "key": "mac:aa:bb:cc:dd:ee:01", "segment": "10.9.0.0/24", "note": "n"}])
    with _pytest.raises(ConsoleFileShapeError, match="exactly one segment"):
        put(add=[{"kind": "link", "key": link_key, "mode": "access",
                  "vlans": ["10.9.0.0/24", "10.9.1.0/24"], "note": "n"}])
    with _pytest.raises(ConsoleFileShapeError, match="DIFFERENT devices"):
        put(add=[{"kind": "link", "key": "address:10.9.0.4#eth0|address:10.9.0.4#eth1",
                  "mode": "trunk", "vlans": ["10.9.0.0/24"], "note": "n"}])
    with _pytest.raises(ConsoleFileShapeError, match="both `confirm` and `doubt`"):
        put(confirm=[{"key": "address:10.9.0.4", "note": "a"}],
            doubt=[{"key": "address:10.9.0.4", "note": "b"}])
    with _pytest.raises(ConsoleFileShapeError, match="not one of"):
        put(add=[], **{"schema": "axr-assertions/9"})


# ---------------------------------------------------------------------------
# A2: three change kinds (track 4) -- the members and provenance rules
# ---------------------------------------------------------------------------
_TE_GROUP = textwrap.dedent("""
    twin_config:
      id: t
      segments: [{ name: office, cidr: 10.71.10.0/24 }]
      nodes:
        - name: ws
          role: corp-workstation
          image: img@v1
          count: 3
          net: office
          source: discovered
          confidence: 0.9
    """).lstrip()


def test_a_members_role_override_is_a_traffic_change_keyed_by_instance():
    from axrange.api import edits

    after = _TE_GROUP.replace("      net: office\n",
                              "      net: office\n      members:\n"
                              "        - { index: 2, role: jump-host }\n")
    assert after != _TE_GROUP
    ch = edits.classify_te("te.yaml", _TE_GROUP, after)
    assert ch.kind == edits.KIND_TRAFFIC and ch.via == ["tfc"]
    assert ch.roles == {"ws02": "jump-host"}
    # removing the override hands the instance the group's role back
    back = edits.classify_te("te.yaml", after, _TE_GROUP)
    assert back.kind == edits.KIND_TRAFFIC and back.roles == {"ws02": "corp-workstation"}


def test_a_members_override_with_a_new_image_is_topology():
    from axrange.api import edits

    after = (_TE_GROUP.replace("image: img@v1", "image: img@v2")
             .replace("      net: office\n",
                      "      net: office\n      members:\n        - { index: 1, role: x }\n"))
    assert edits.classify_te("te.yaml", _TE_GROUP, after).kind == edits.KIND_TOPOLOGY


def test_a_provenance_only_edit_is_inert_and_a_link_edit_is_topology():
    from axrange.api import edits

    prov = _TE_GROUP.replace("confidence: 0.9", "confidence: 0.4").replace(
        "source: discovered", "source: unconfirmed")
    ch = edits.classify_te("te.yaml", _TE_GROUP, prov)
    assert ch.kind == edits.KIND_INERT and "provenance" in ch.reason
    linked = _TE_GROUP + "      links:\n        - { a: { device: ws, port: p }, b: { device: gw, port: q }, mode: access, vlans: [office] }\n"
    assert edits.classify_te("te.yaml", _TE_GROUP, linked).kind == edits.KIND_TOPOLOGY


def test_tiers_to_apply_reads_the_two_live_kinds():
    from axrange.api import edits

    changes = [
        edits.PendingChange(path="tfc/default.yaml", op="modified", kind=edits.KIND_TRAFFIC, via=["tfc"]),
        edits.PendingChange(path="tap/default.yaml", op="modified", kind=edits.KIND_TAPS, via=["tap"]),
        edits.PendingChange(path="README.md", op="modified", kind=edits.KIND_INERT),
    ]
    assert edits.tiers_to_apply(changes) == ["tfc", "tap"]
    assert edits.live_kind(["tfc", "tap"]) == edits.KIND_TRAFFIC and edits.live_kind(["tap"]) == edits.KIND_TAPS
