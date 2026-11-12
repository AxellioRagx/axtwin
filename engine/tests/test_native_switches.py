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
                {"name": "ws", "role": "corp-workstation", "image": "img@v1",
                 "requires": ["linux"], "count": 2, "net": "office"},
                {"name": "fs", "role": "file-server", "image": "img@v1",
                 "requires": ["linux"], "nets": [{"segment": "servers", "ip": "10.71.20.10"}]},
                {"name": "sw", "kind": "switch", "role": "l2-switch"},
                {"name": "gw", "kind": "router", "image": "img@v1", "requires": ["linux"],
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
