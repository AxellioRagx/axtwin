"""The device tree in the cast (`docs/design/topology-layers.md` §2, §3, §5, §8; track 3,
2026-11-07): `source` on every segment and node, `axr-assertions/2`'s device, link, tap,
confirm and doubt records applied after the cast, member splits by `group`, and the audit of
what could not be keyed.

Synthetic events only (`LES-003`); the harness is `test_synth_compiler.py`'s.
"""
from aximprint.operator_assertions import parse_assertions
from aximprint.synth.compiler import GATEWAY_NAME, compile_twin, to_yaml
from tests.test_synth_compiler import _TWO_SEGMENTS, _pit, _tc

_SW = "mac:e0:cb:bc:11:22:33"
_DC = "address:10.0.1.20"          # the domain controller, one host in its own group
_FS = "address:10.0.2.30"          # the file server in the second segment
_SEG1, _SEG2 = "10.0.1.0/24", "10.0.2.0/24"


def _assertions(**blocks):
    return parse_assertions({
        "schema": "axr-assertions/2", "imprint": "t",
        "as_of": "2026-09-04T18:00:00Z", "source": "operator: raghu", **blocks},
        path="fixture.yaml")


def _named(out):
    return {n["name"]: n for n in _tc(out)["nodes"]}


def _by_key(out, key):
    """The node group an entity key compiled into, through the sidecar's roster."""
    fields = out["provenance"]["fields"]
    for path, row in fields.items():
        if path.endswith(".count") and key in (row.get("members") or []):
            idx = int(path.split("[")[1].split("]")[0])
            return _tc(out)["nodes"][idx]
    raise AssertionError(f"{key} is in no group's roster")


# -- source on everything ----------------------------------------------------------------

def test_every_node_and_segment_carries_a_source_and_a_discovered_one_says_how_firmly(tmp_path):
    out = compile_twin(_pit(tmp_path, _TWO_SEGMENTS))
    tc = _tc(out)
    assert all(n.get("source") for n in tc["nodes"])
    assert all(s.get("source") for s in tc["segments"])
    dc = _by_key(out, _DC)
    assert dc["source"] == "discovered"
    assert dc["confidence"] == dc["observation"]["occupancy"]
    assert "vantage" not in dc  # this PIT records no vantage, so none is fabricated
    seg = next(s for s in tc["segments"] if s["cidr"] == _SEG1)
    assert seg["source"] == "discovered" and 0 < seg["confidence"] <= 1
    gw = _named(out)[GATEWAY_NAME]
    assert gw["source"] == "unconfirmed" and "confidence" not in gw


def test_the_cast_is_byte_identical_across_two_compiles_with_source(tmp_path):
    one = to_yaml(compile_twin(_pit(tmp_path, _TWO_SEGMENTS, "a.duckdb"))["twin_config"])
    two = to_yaml(compile_twin(_pit(tmp_path, _TWO_SEGMENTS, "b.duckdb"))["twin_config"])
    assert one == two


def test_a_version_1_assertions_file_still_casts(tmp_path):
    aset = parse_assertions({
        "schema": "axr-assertions/1", "imprint": "t", "as_of": "2026-09-04T18:00:00Z",
        "source": "operator: raghu",
        "override": [{"key": _DC, "field": "role", "value": "web-tier", "note": "n"}]})
    out = compile_twin(_pit(tmp_path, _TWO_SEGMENTS), assertions=aset)
    assert _by_key(out, _DC)["role"] == "web-tier"
    assert out["provenance"]["population"]["assertions"]["schema"] == "axr-assertions/2"


# -- confirm / doubt ---------------------------------------------------------------------

def test_confirm_pins_a_discovered_group_as_asserted_and_doubt_marks_it_unconfirmed(tmp_path):
    out = compile_twin(_pit(tmp_path, _TWO_SEGMENTS), assertions=_assertions(
        confirm=[{"key": _DC, "note": "seen"}, {"key": _SEG1, "note": "seen"}],
        doubt=[{"key": _FS, "note": "weak"}]))
    assert _by_key(out, _DC)["source"] == "asserted"
    assert _by_key(out, _DC)["role"] == "domain-controller", "a confirm changes no field"
    assert _by_key(out, _FS)["source"] == "unconfirmed"
    seg = next(s for s in _tc(out)["segments"] if s["cidr"] == _SEG1)
    assert seg["source"] == "asserted" and "confidence" not in seg
    audit = out["provenance"]["population"]["assertions"]
    assert audit["confirmed"] == [_SEG1, _DC] and audit["doubted"] == [_FS]
    assert audit["unmatched"] == []


def test_a_mark_on_nothing_in_the_pit_is_reported_not_fatal(tmp_path):
    out = compile_twin(_pit(tmp_path, _TWO_SEGMENTS), assertions=_assertions(
        doubt=[{"key": "address:10.0.9.9", "note": "n"}]))
    assert {"kind": "doubt", "key": "address:10.0.9.9"} in \
        out["provenance"]["population"]["assertions"]["unmatched"]


# -- devices -----------------------------------------------------------------------------

def test_an_overridden_device_kind_makes_a_discovered_host_a_switch_with_an_l2_port(tmp_path):
    out = compile_twin(_pit(tmp_path, _TWO_SEGMENTS), assertions=_assertions(
        override=[{"key": _DC, "field": "device_kind", "value": "switch", "note": "n"}]))
    node = _by_key(out, _DC)
    assert node["kind"] == "switch"
    assert node["nets"] == [{"segment": "seg-10-0-1-0", "l2": True}] and "net" not in node
    assert "image" not in node, "no asserted image: the switch deploys as its vnets only"
    assert node["source"] == "asserted"
    idx = _tc(out)["nodes"].index(node)
    assert out["provenance"]["fields"][f"twin_config.nodes[{idx}].kind"]["evidence_class"] == "asserted"


def test_an_added_device_enters_the_twin_as_a_switch_on_its_segment(tmp_path):
    out = compile_twin(_pit(tmp_path, _TWO_SEGMENTS), assertions=_assertions(
        add=[{"kind": "device", "key": _SW, "device_kind": "switch", "role": "l2-switch",
              "segment": _SEG1, "note": "n"}]))
    node = _by_key(out, _SW)
    assert node["kind"] == "switch" and node["role"] == "l2-switch"
    assert node["nets"] == [{"segment": "seg-10-0-1-0", "l2": True}]
    assert "image" not in node and "observation" not in node
    assert node["source"] == "asserted"
    assert out["provenance"]["population"]["assertions"]["added_devices"] == [_SW]


def test_an_added_router_keeps_its_image_and_its_addressed_leg(tmp_path):
    out = compile_twin(_pit(tmp_path, _TWO_SEGMENTS), assertions=_assertions(
        add=[{"kind": "device", "key": _SW, "device_kind": "router", "image": "vyos@1.5",
              "segment": _SEG1, "note": "n"}]))
    node = _by_key(out, _SW)
    assert node["kind"] == "router" and node["image"] == "vyos@1.5" and node["net"] == "seg-10-0-1-0"


# -- links and taps ----------------------------------------------------------------------

def _wired():
    return _assertions(
        add=[
            {"kind": "device", "key": _SW, "device_kind": "switch", "segment": _SEG1, "note": "n"},
            {"kind": "link", "key": f"{_SW}#SFP25|{_DC}#eth0", "mode": "trunk",
             "native": _SEG1, "vlans": [_SEG1, _SEG2], "note": "n"},
            {"kind": "tap", "key": f"segment:{_SEG2}", "sensors": ["XPress-SW-A"], "note": "n"},
            {"kind": "tap", "key": f"port:{_SW}#SFP25", "sensors": ["o2-suricata"], "note": "n"},
        ],
        override=[{"key": f"{_SW}#SFP25|{_DC}#eth0", "field": "state", "value": "up", "note": "n"}])


def test_an_asserted_link_lands_with_its_ends_and_segments_resolved_by_name(tmp_path):
    out = compile_twin(_pit(tmp_path, _TWO_SEGMENTS), assertions=_wired())
    links = _tc(out)["links"]
    assert len(links) == 1
    link = links[0]
    sw, dc = _by_key(out, _SW)["name"], _by_key(out, _DC)["name"]
    assert {link["a"]["device"], link["b"]["device"]} == {sw, dc}
    assert link["mode"] == "trunk" and link["native"] == "seg-10-0-1-0"
    assert link["vlans"] == ["seg-10-0-1-0", "seg-10-0-2-0"]
    assert link["state"] == "up", "the override on the link's state applied (the add set none)"
    assert link["source"] == "asserted"
    assert out["provenance"]["fields"]["twin_config.links[0]"]["evidence_class"] == "asserted"
    assert out["provenance"]["population"]["assertions"]["unmatched"] == []


def test_an_asserted_tap_binds_a_segment_or_a_port_with_its_sensors(tmp_path):
    out = compile_twin(_pit(tmp_path, _TWO_SEGMENTS), assertions=_wired())
    bindings = out["tap_plan"]["tap-plan"]["bindings"]
    named = [b for b in bindings if b.get("sensors")]
    assert len(named) == 2
    seg_tap = next(b for b in named if b["selector"]["scope"] == "segment")
    assert seg_tap["selector"]["target"] == "seg-10-0-2-0" and seg_tap["sensors"] == ["XPress-SW-A"]
    port_tap = next(b for b in named if b["selector"]["scope"] == "port")
    assert port_tap["selector"]["target"] == _by_key(out, _SW)["name"]
    assert port_tap["selector"]["segment"] == "seg-10-0-1-0", "the trunk's native, carried by the port"
    assert port_tap["sensors"] == ["o2-suricata"]


def test_a_link_to_a_group_of_several_or_to_an_unknown_device_is_reported(tmp_path):
    fs_group_key = "address:10.0.1.31"   # one of the three file servers collapsed into one group
    out = compile_twin(_pit(tmp_path, _TWO_SEGMENTS), assertions=_assertions(add=[
        {"kind": "link", "key": f"{_DC}#eth0|{fs_group_key}#eth0", "mode": "access",
         "vlans": [_SEG1], "note": "n"},
        {"kind": "link", "key": f"{_DC}#eth1|mac:00:00:00:00:00:01#P1", "mode": "access",
         "vlans": [_SEG1], "note": "n"},
    ]))
    assert "links" not in _tc(out)
    reasons = {r["key"]: r["reason"] for r in out["provenance"]["population"]["assertions"]["ineffective"]}
    assert any("a link names one device" in v for v in reasons.values())
    assert any("not a twin node" in v for v in reasons.values())


def test_a_tap_on_a_port_no_link_carries_is_reported(tmp_path):
    out = compile_twin(_pit(tmp_path, _TWO_SEGMENTS), assertions=_assertions(add=[
        {"kind": "tap", "key": f"port:{_DC}#eth9", "note": "n"}]))
    assert not any(b.get("sensors") for b in out["tap_plan"]["tap-plan"]["bindings"])
    assert any(r["kind"] == "add:tap" and "on no link" in r["reason"]
               for r in out["provenance"]["population"]["assertions"]["ineffective"])


# -- member split ------------------------------------------------------------------------

def test_a_group_override_splits_a_member_into_its_own_named_group(tmp_path):
    out = compile_twin(_pit(tmp_path, _TWO_SEGMENTS), assertions=_assertions(override=[
        {"key": "address:10.0.1.31", "field": "group", "value": "backup-server", "note": "n"}]))
    node = _by_key(out, "address:10.0.1.31")
    assert node["name"] == "backup-server" and "count" not in node
    assert node["role"] == "file-server", "the split keeps the role"
    assert node["source"] == "asserted"
    others = [n for n in _tc(out)["nodes"] if n.get("role") == "file-server"
              and n["name"] != "backup-server" and n.get("net") == "seg-10-0-1-0"]
    assert sum(n.get("count", 1) for n in others) == 2, "the other two stay together"


# -- review R5-1: nothing the cast emits goes without a source ---------------------------

def _unsourced(out):
    tc = _tc(out)
    rows = [("segment", e) for e in tc["segments"]] + [("node", e) for e in tc["nodes"]]
    rows += [("conduit", e) for e in tc.get("conduits") or []]
    rows += [("link", e) for e in tc.get("links") or []]
    rows += [("tap", e) for e in out["tap_plan"]["tap-plan"]["bindings"]]
    return [(kind, e) for kind, e in rows if not e.get("source")]


def test_no_node_segment_conduit_link_or_tap_of_a_cast_lacks_a_source(tmp_path):
    out = compile_twin(_pit(tmp_path, _TWO_SEGMENTS), assertions=_wired())
    tc = _tc(out)
    assert tc["conduits"] and tc["links"], "the fixture exercises every kind"
    assert _unsourced(out) == []
    plain = compile_twin(_pit(tmp_path, _TWO_SEGMENTS, "plain.duckdb"))
    assert _unsourced(plain) == []
    assert all(c["source"] == "discovered" and "confidence" not in c
               for c in _tc(plain)["conduits"]), "no score exists for a conduit; none is invented"


def test_a_cast_with_one_asserted_conduit_marks_that_conduit_asserted(tmp_path):
    out = compile_twin(_pit(tmp_path, _TWO_SEGMENTS), assertions=_assertions(
        add=[{"kind": "conduit", "key": f"{_SEG1}|{_SEG2}", "note": "n"}]))
    (conduit,) = [c for c in _tc(out)["conduits"]
                  if {c["from"], c["to"]} == {"seg-10-0-1-0", "seg-10-0-2-0"}]
    assert conduit["source"] == "asserted" and "vantage" not in conduit
    assert _unsourced(out) == []
