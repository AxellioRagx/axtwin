"""The deploy gate — `DEPLOY_MIN_OCCUPANCY` over a cast twin-config's
`observation:` blocks (`docs/WORK_ORDER_deploy_gate.md` §9).

Split out of `tests/test_twinconfig_expand.py` (which the work order names)
purely for size: the gate is one feature of the expander with its own
fixture, its own error table and its own recovery helper, and burying ~35
cases in the 500-line general expander module would make neither greppable.
`test_twinconfig_expand.py` keeps the general expansion behavior; this file
keeps everything the gate owns.

The core assertion is `test_gating_table_at_each_threshold`: the exact node
ids, segments and conduits `tests/fixtures/twin-configs/gate-cast` yields at
0.0 / 0.5 / 0.8 / 0.9 / 1.0. Everything else in here exists to explain a
failure of that one.
"""
from __future__ import annotations

import copy

import pytest

from axrange.twinconfig.errors import TwinConfigParamError, TwinConfigShapeError
from axrange.twinconfig.expand import (
    expand_twin_config_raw,
    expand_twin_config_raw_with_gate,
)
from axrange.twinconfig.gate import (
    DEFAULT_MIN_OCCUPANCY,
    DEFAULT_MIN_ROLE_CONFIDENCE,
    WARNING_DETAIL_CAP,
    confirm_deploy_gate,
    deploy_gate_report,
    normalize_threshold,
)
from axrange.twinconfig.registry import load_twin_config_raw

_FIXT_DIR = "tests/fixtures/twin-configs"


def _cast() -> dict:
    """A fresh, mutable copy of the fixture cast config."""
    raw, _path = load_twin_config_raw("gate-cast", catalog_dir=_FIXT_DIR)
    return copy.deepcopy(raw)


def _expand(cfg: dict, threshold: str | None = None, **overrides):
    params = dict(overrides)
    if threshold is not None:
        params["DEPLOY_MIN_OCCUPANCY"] = threshold
    return expand_twin_config_raw_with_gate(cfg, param_overrides=params or None)


def _node_ids(ir: dict) -> list[str]:
    return [n["id"] for n in ir["layers"]["infra"]["nodes"]]


def _net_ids(ir: dict) -> list[str]:
    return [n["id"] for n in ir["layers"]["infra"]["networks"]]


def _obs_config(observation: dict | None, **node_extra) -> dict:
    """A three-node config whose SECOND node (`h`) carries `observation`.

    `anchor` is a blockless host that keeps `lan` occupied whatever happens to
    `h` -- a ROUTER leg does not make a segment occupied (a router follows its
    segments), so a router + one gated host would empty the segment and error
    with "no segments left". Tests that want exactly that drop `anchor`.
    """
    node = {"name": "h", "image": "img@v1", "requires": ["linux"], "net": "lan"}
    node.update(node_extra)
    if observation is not None:
        node["observation"] = observation
    return {
        "twin_config": {
            "id": "unit-obs",
            "segments": [{"name": "lan", "cidr": "10.99.0.0/24"}],
            "nodes": [
                {"name": "gw", "kind": "router", "image": "img@v1", "net": "lan"},
                node,
                {"name": "anchor", "image": "img@v1", "requires": ["linux"], "net": "lan"},
            ],
        }
    }


# ===========================================================================
# §3.3 -- observation-block validation, one case per rule, each naming the
# entity (a pydantic list index is not an actionable message).
# ===========================================================================
def test_windows_total_must_be_at_least_one():
    cfg = _obs_config({"windows_seen": 0, "windows_total": 0, "occupancy": 0.0})
    with pytest.raises(TwinConfigShapeError, match="windows_total 0 must be >= 1"):
        expand_twin_config_raw(cfg)


@pytest.mark.parametrize("seen", [-1, 12])
def test_windows_seen_must_be_within_windows_total(seen):
    cfg = _obs_config({"windows_seen": seen, "windows_total": 11, "occupancy": 0.5})
    with pytest.raises(TwinConfigShapeError, match="windows_seen"):
        expand_twin_config_raw(cfg)


def test_occupancy_outside_zero_to_one_is_rejected():
    cfg = _obs_config({"windows_seen": 11, "windows_total": 11, "occupancy": 1.4})
    with pytest.raises(TwinConfigShapeError, match=r"occupancy 1.4 must be in \[0.0, 1.0\]"):
        expand_twin_config_raw(cfg)


def test_occupancy_disagreeing_with_the_window_counts_is_a_hard_error():
    """§3.2: the counts are the carried occupancy's CHECKSUM. A producer bug
    must surface at expansion, not silently select the wrong node set."""
    cfg = _obs_config({"windows_seen": 2, "windows_total": 11, "occupancy": 0.9})
    with pytest.raises(TwinConfigShapeError, match="disagrees with"):
        expand_twin_config_raw(cfg)


def test_occupancy_within_the_rounding_tolerance_is_accepted():
    """A cast writes 6 decimals; 2/11 is 0.18181818... -- that must not be a
    checksum failure."""
    cfg = _obs_config({"windows_seen": 2, "windows_total": 11, "occupancy": 0.181818})
    ir, _params, _report = _expand(cfg, "0.1")
    assert "h" in _node_ids(ir)


def test_role_confidence_outside_zero_to_one_is_rejected():
    cfg = _obs_config({
        "windows_seen": 11, "windows_total": 11, "occupancy": 1.0, "role_confidence": 1.5,
    })
    with pytest.raises(TwinConfigShapeError, match="role_confidence"):
        expand_twin_config_raw(cfg)


def test_absent_role_confidence_is_fine_and_is_never_defaulted():
    """Segments and role-less hosts legitimately carry none: absent means
    NOT APPLICABLE, not 0.0 (the only place it reads as 0.0 is the optional
    DEPLOY_MIN_ROLE_CONFIDENCE comparison, which is off by default)."""
    cfg = _obs_config({"windows_seen": 11, "windows_total": 11, "occupancy": 1.0})
    ir, _params, report = _expand(cfg)
    assert "h" in _node_ids(ir)
    assert report.active is True


@pytest.mark.parametrize("missing", ["windows_seen", "windows_total", "occupancy"])
def test_a_partial_block_is_an_error_naming_the_entity(missing):
    block = {"windows_seen": 8, "windows_total": 11, "occupancy": 0.727273}
    block.pop(missing)
    cfg = _obs_config(block)
    with pytest.raises(TwinConfigShapeError) as e:
        expand_twin_config_raw(cfg)
    assert "node 'h'" in str(e.value)
    assert missing in str(e.value)


def test_unknown_key_inside_the_block_is_rejected_by_the_closed_vocabulary():
    cfg = _obs_config({
        "windows_seen": 11, "windows_total": 11, "occupancy": 1.0, "windows": 11,
    })
    with pytest.raises(TwinConfigShapeError):
        expand_twin_config_raw(cfg)


def test_segment_block_is_validated_too_and_names_the_segment():
    cfg = _obs_config(None)
    cfg["twin_config"]["segments"][0]["observation"] = {
        "windows_seen": 5, "windows_total": 0, "occupancy": 0.0,
    }
    with pytest.raises(TwinConfigShapeError, match="segment 'lan'"):
        expand_twin_config_raw(cfg)


# ===========================================================================
# §4.2 -- the predicate and its boundary. A float flip here silently changes
# the node set, therefore `ir_hash`, therefore `plan_id` (LES-126).
# ===========================================================================
def test_boundary_eight_of_ten_is_in_at_exactly_the_threshold():
    cfg = _obs_config({"windows_seen": 8, "windows_total": 10, "occupancy": 0.8})
    ir, _params, _report = _expand(cfg, "0.8")
    assert _node_ids(ir) == ["gw", "h", "anchor"]


def test_boundary_eight_of_ten_is_out_just_above_the_threshold():
    cfg = _obs_config({"windows_seen": 8, "windows_total": 10, "occupancy": 0.8})
    ir, _params, _report = _expand(cfg, "0.81")
    assert _node_ids(ir) == ["gw", "anchor"]


def test_a_node_with_no_block_is_never_gated_out_even_at_a_1_0_threshold():
    """§3.4 -- what every shipped `twin-configs/` config depends on."""
    cfg = _obs_config(None)
    ir, _params, report = _expand(cfg, "1.0")
    assert _node_ids(ir) == ["gw", "h", "anchor"]
    assert report.active is False


def test_a_router_is_never_gated_out_and_the_exemption_is_printed():
    cfg = _obs_config(None)
    cfg["twin_config"]["nodes"][0]["observation"] = {
        "windows_seen": 1, "windows_total": 11, "occupancy": 0.090909,
    }
    ir, _params, report = _expand(cfg, "0.8")
    assert "gw" in _node_ids(ir)
    assert [r.name for r in report.kept_routers] == ["gw"]
    assert "routers are never gated" in report.kept_routers[0].line()


# ===========================================================================
# §9.2 -- THE GATING TABLE. The core assertion of this whole build slice.
# ===========================================================================
_GATING_TABLE = {
    # threshold: (node ids, segment ids, surviving conduits)
    "0.0": (
        ["gw", "always-on", "edge-host", "core-strong", "core-mid",
         "worker01", "worker02", "worker03", "bridge-host", "ghost-host"],
        ["core", "edge", "thin", "ghost"],
        ["core->edge", "core->thin", "core->ghost"],
    ),
    "0.5": (
        ["gw", "always-on", "edge-host", "core-strong", "core-mid",
         "worker01", "worker02", "worker03"],
        ["core", "edge"],
        ["core->edge"],
    ),
    # 8/11 -- the `count: 3` group drops as ONE unit here.
    "0.8": (
        ["gw", "always-on", "edge-host", "core-strong", "core-mid"],
        ["core", "edge"], ["core->edge"],
    ),
    # 9/11 = 0.818182 -- `core-mid` goes.
    "0.9": (["gw", "always-on", "edge-host", "core-strong"], ["core", "edge"], ["core->edge"]),
    # 11/11 = 1.0 is still IN at a 1.0 threshold (the epsilon).
    "1.0": (["gw", "always-on", "edge-host", "core-strong"], ["core", "edge"], ["core->edge"]),
}


@pytest.mark.parametrize("threshold", sorted(_GATING_TABLE))
def test_gating_table_at_each_threshold(threshold):
    expected_nodes, expected_nets, expected_conduits = _GATING_TABLE[threshold]
    ir, _params, report = _expand(_cast(), threshold)
    assert _node_ids(ir) == expected_nodes
    assert _net_ids(ir) == expected_nets
    dropped = {g.name for g in report.dropped_conduits}
    all_conduits = {"core->edge", "core->thin", "core->ghost"}
    assert sorted(all_conduits - dropped) == sorted(expected_conduits)


def test_a_count_group_gates_as_one_unit():
    """The cast keeps no per-host identity inside a `count: N` group, so the
    block carries its weakest member's numbers and the gate keeps or drops
    all N (2026-09-04 ruling, Authority: user)."""
    ir_in, _p, _r = _expand(_cast(), "0.7")
    ir_out, _p, report = _expand(_cast(), "0.75")
    assert {"worker01", "worker02", "worker03"} <= set(_node_ids(ir_in))
    assert not [n for n in _node_ids(ir_out) if n.startswith("worker")]
    row = next(g for g in report.gated_nodes if g.name == "worker")
    assert row.count == 3
    assert "count: 3" in row.line()


def test_an_emptied_segment_is_dropped_with_a_reason_and_takes_its_conduit():
    _ir, _params, report = _expand(_cast(), "0.5")
    thin = next(g for g in report.gated_segments if g.name == "thin")
    assert thin.reason == "every host with a leg on it was gated out"
    conduit = next(g for g in report.dropped_conduits if g.name == "core->thin")
    assert "not emitted" in conduit.reason


def test_a_segment_gated_by_its_own_block_reports_the_occupancy():
    _ir, _params, report = _expand(_cast(), "0.5")
    ghost = next(g for g in report.gated_segments if g.name == "ghost")
    assert "0.181818" in ghost.reason and "DEPLOY_MIN_OCCUPANCY=0.5" in ghost.reason


def test_a_segment_gated_out_from_under_an_emitted_node_is_a_hard_error():
    """§4.3 step 3 / seam §10.5: the node would have a leg into nothing. A
    cast inconsistency, not an operator choice -- so it is refused, not
    silently repaired."""
    cfg = _cast()
    for seg in cfg["twin_config"]["segments"]:
        if seg["name"] == "core":
            seg["observation"] = {
                "windows_seen": 1, "windows_total": 11, "occupancy": 0.090909,
            }
    with pytest.raises(TwinConfigShapeError) as e:
        _expand(cfg, "0.5")
    msg = str(e.value)
    assert "segment 'core'" in msg and "still have a leg on it" in msg
    assert "always-on" in msg  # names the node(s), not just the segment


def test_everything_gated_out_errors_naming_the_deploy_gate():
    cfg = _obs_config({"windows_seen": 1, "windows_total": 11, "occupancy": 0.090909})
    # Keep ONLY the observed host: no anchor, and the router's own leg does
    # not hold a segment up.
    cfg["twin_config"]["nodes"] = [cfg["twin_config"]["nodes"][1]]
    with pytest.raises(TwinConfigShapeError, match="deploy gate"):
        _expand(cfg, "0.8")


def test_a_blockless_config_never_loses_an_empty_segment():
    """The empty-segment cascade is scoped to a config where the gate
    actually ran. With no `observation:` data there is nothing to cascade
    FROM, and dropping a deliberately-empty authored segment would change
    that config's `ir_hash` -- orphaning any standing deploy (LES-126)."""
    cfg = _obs_config(None)
    cfg["twin_config"]["segments"].append({"name": "spare", "cidr": "10.98.0.0/24"})
    ir, _params, report = _expand(cfg, "1.0")
    assert "spare" in _net_ids(ir)
    assert report.active is False


# ===========================================================================
# §4.3 step 6 (D2) -- identity/data references are AUTHORED facts.
# ===========================================================================
def test_data_reference_to_a_gated_out_node_is_a_hard_error_with_the_remedy():
    cfg = _cast()
    cfg["twin_config"]["data"][0]["on_role"] = "worker02"
    with pytest.raises(TwinConfigShapeError) as e:
        _expand(cfg, "0.8")
    msg = str(e.value)
    assert "data 'crown-jewels'" in msg          # the entry
    assert "worker02" in msg and "'worker' spec" in msg  # the node AND its spec
    assert "0.727273" in msg                     # its occupancy, in prose
    assert "DEPLOY_MIN_OCCUPANCY=0.8" in msg     # the effective threshold
    # The remedy carries the EXACT value, not the 6-digit prose rendering:
    # the fixture's occupancy is a full-precision 8/11, and 0.727273 sits
    # BELOW it (the gate's epsilon is 1e-9, the checksum's tolerance 1e-6).
    assert "--param DEPLOY_MIN_OCCUPANCY=0.7272727272727273 (or lower)" in msg


def test_the_printed_remedy_actually_works_when_pasted_back():
    """A remedy that still refuses when re-run is worse than no remedy. The
    `%g` rendering (`0.727273`) is exactly that: below the value it names."""
    cfg = _cast()
    cfg["twin_config"]["data"][0]["on_role"] = "worker02"
    with pytest.raises(TwinConfigShapeError) as e:
        _expand(cfg, "0.8")
    remedy = str(e.value).split("--param DEPLOY_MIN_OCCUPANCY=")[1].split(" ")[0]
    ir, _params, _report = _expand(cfg, remedy)
    assert "worker02" in _node_ids(ir)
    # ...and the rounded form the message uses in PROSE would not have.
    with pytest.raises(TwinConfigShapeError):
        _expand(cfg, "0.727273")


def test_identity_reference_to_a_gated_out_node_is_a_hard_error():
    """The identity half of D2, asserted to the SAME four-part standard as
    the data half above (entry, host, occupancy, effective threshold,
    remedy). They share `_gated_reference_message` today -- pin both, so a
    later per-kind branch cannot quietly drop half the message from the arm
    that carries a local account."""
    cfg = _cast()
    cfg["twin_config"]["identity"]["users"][0]["host"] = "bridge-host"
    with pytest.raises(TwinConfigShapeError) as e:
        _expand(cfg, "0.5")
    msg = str(e.value)
    assert "identity user 'localadmin'" in msg              # the entry
    assert "bridge-host" in msg                             # the host
    assert "0.090909" in msg and "1/11 windows" in msg      # its occupancy
    assert "DEPLOY_MIN_OCCUPANCY=0.5" in msg                # the threshold
    assert "--param DEPLOY_MIN_OCCUPANCY=0.090909" in msg   # the remedy


# `test_the_printed_remedy_actually_re_admits_the_node` REMOVED -- subsumed by
# `test_the_printed_remedy_actually_works_when_pasted_back` above, which makes
# the same assertion AND pins the failure it was written for (the `%g`-rounded
# form does NOT re-admit the node, so a remedy printed in prose precision is
# advice that does not work).


def test_identity_and_data_pointing_at_emitted_nodes_expand_normally():
    ir, _params, _report = _expand(_cast(), "0.8")
    assert ir["overlays"]["identity"][0]["host"] == "always-on"
    assert ir["overlays"]["data"][0]["host"] == "core-strong"


# ===========================================================================
# §4.3 step 1 / §9.3 -- `when:` (authored intent) runs FIRST.
# ===========================================================================
def test_when_excluded_entry_with_an_invalid_block_is_never_checked():
    cfg = _obs_config(
        {"windows_seen": 99, "windows_total": 0, "occupancy": 5.0},  # nonsense
        when="EXTRA",
    )
    cfg["twin_config"]["params"] = {"EXTRA": False}
    ir, _params, report = _expand(cfg, "0.8")
    assert _node_ids(ir) == ["gw", "anchor"]
    # The gate never ran: the only block in the config belongs to an entry
    # `when:` already excluded.
    assert report.active is False


def test_when_kept_entry_is_then_subject_to_the_occupancy_gate():
    cfg = _obs_config(
        {"windows_seen": 2, "windows_total": 11, "occupancy": 0.181818}, when="EXTRA",
    )
    cfg["twin_config"]["params"] = {"EXTRA": True}
    ir, _params, report = _expand(cfg, "0.8")
    assert _node_ids(ir) == ["gw", "anchor"]
    assert [g.name for g in report.gated_nodes] == ["h"]


# ===========================================================================
# §4.4 -- the optional second gate (D3), off by default.
# ===========================================================================
def test_role_confidence_gate_is_off_by_default():
    ir, _params, _report = _expand(_cast(), "0.0")
    # `core-strong` and `always-on` carry no role_confidence at all and are
    # emitted anyway -- at the 0.0 default this clause can never exclude.
    assert {"core-strong", "always-on"} <= set(_node_ids(ir))


def _cast_without_overlays() -> dict:
    """The fixture minus its `identity:`/`data:` entries. Turning the
    role-confidence gate on removes every host carrying NO `role_confidence`
    -- including `core-strong`, which the fixture's crown-jewel dataset sits
    on -- and that reference is (correctly) a hard error. These tests are
    about the predicate, not about the reference check."""
    cfg = _cast()
    cfg["twin_config"].pop("identity", None)
    cfg["twin_config"].pop("data", None)
    return cfg


def test_role_confidence_gate_excludes_below_threshold_hosts():
    ir, _params, report = _expand(
        _cast_without_overlays(), "0.0", DEPLOY_MIN_ROLE_CONFIDENCE="0.5",
    )
    ids = set(_node_ids(ir))
    assert "core-mid" in ids            # 0.92
    assert "worker01" not in ids        # 0.40
    row = next(g for g in report.gated_nodes if g.name == "worker")
    assert "role_confidence 0.4" in row.reason
    assert row.metric == "role_confidence"


def test_role_confidence_gate_treats_absent_as_zero_and_says_so():
    _ir, _params, report = _expand(
        _cast_without_overlays(), "0.0", DEPLOY_MIN_ROLE_CONFIDENCE="0.5",
    )
    row = next(g for g in report.gated_nodes if g.name == "core-strong")
    assert "absent, compared as 0" in row.reason


@pytest.mark.parametrize("threshold,expected_in", [("0.4", True), ("0.41", False)])
def test_role_confidence_boundary_is_inclusive_at_equality(threshold, expected_in):
    """The second clause carries the same epsilon as the first, and it is
    load-bearing for more than the node set: the error text tells an operator
    to re-run at `--param DEPLOY_MIN_ROLE_CONFIDENCE=<the node's own value>`,
    which is only truthful while equality is IN. `worker` carries exactly
    0.4."""
    ir, _params, _report = _expand(
        _cast_without_overlays(), "0.0", DEPLOY_MIN_ROLE_CONFIDENCE=threshold,
    )
    assert ("worker01" in _node_ids(ir)) is expected_in


def test_role_confidence_never_gates_a_segment():
    """A segment has no ROLE. Comparing its (always absent) role_confidence
    as 0.0 gated out every observed segment the moment the second threshold
    was raised -- and then hard-errored for gating a segment out from under
    its own emitted nodes."""
    ir, _params, report = _expand(
        _cast_without_overlays(), "0.0", DEPLOY_MIN_ROLE_CONFIDENCE="0.5",
    )
    assert "core" in _net_ids(ir)  # carries an 11/11 block and no role_confidence
    # Other segments DO leave here -- but by the empty-segment CASCADE (their
    # only hosts fell to the role clause), never by a role_confidence verdict
    # of their own.
    assert report.gated_segments  # the cascade did fire
    assert all(g.metric is None for g in report.gated_segments)


def test_the_remedy_names_the_threshold_that_actually_removed_the_node():
    """Suggesting `--param DEPLOY_MIN_OCCUPANCY=...` to an operator whose host
    was removed by the ROLE-CONFIDENCE clause is an actively wrong
    instruction, not merely an unhelpful one."""
    cfg = _cast()
    cfg["twin_config"]["data"][0]["on_role"] = "worker01"
    with pytest.raises(TwinConfigShapeError) as e:
        _expand(cfg, "0.0", DEPLOY_MIN_ROLE_CONFIDENCE="0.5")
    assert "--param DEPLOY_MIN_ROLE_CONFIDENCE=0.4 (or lower)" in str(e.value)


# ===========================================================================
# §4.5 -- never silently skip (LES-089).
# ===========================================================================
def test_warnings_summary_prints_even_with_nothing_gated_out():
    """"0 gated out at 0.8" is a meaningful answer and its absence is not."""
    warnings: list[str] = []
    expand_twin_config_raw(
        _cast(), param_overrides={"DEPLOY_MIN_OCCUPANCY": "0.0"}, warnings=warnings,
    )
    assert any("DEPLOY_MIN_OCCUPANCY=0" in w for w in warnings)
    assert any(
        "0 node spec(s) (0 node(s)) and 0 segment(s) removed (0 gated, 0 emptied)" in w
        for w in warnings
    )


def test_warnings_carry_one_line_per_gated_entity():
    warnings: list[str] = []
    expand_twin_config_raw(
        _cast(), param_overrides={"DEPLOY_MIN_OCCUPANCY": "0.8"}, warnings=warnings,
    )
    joined = "\n".join(warnings)
    for expected in (
        "gated out node 'worker'", "gated out node 'bridge-host'",
        "gated out segment 'thin'", "dropped conduit 'core->thin'",
        "kept router 'gw'",
    ):
        assert expected in joined, joined
    assert all(w.startswith("deploy gate: ") for w in warnings)


def test_a_blockless_config_produces_no_gate_warnings_at_all():
    warnings: list[str] = []
    expand_twin_config_raw(_obs_config(None), warnings=warnings)
    assert warnings == []


def test_detail_lines_are_capped_with_a_tail_pointing_at_twin_config_show():
    cfg = _obs_config(None)
    cfg["twin_config"]["nodes"].extend(
        {"name": f"thin{i:03d}", "image": "img@v1", "net": "lan",
         "observation": {"windows_seen": 1, "windows_total": 11, "occupancy": 0.090909}}
        for i in range(WARNING_DETAIL_CAP + 10)
    )
    warnings: list[str] = []
    _ir, _params = expand_twin_config_raw(
        cfg, param_overrides={"DEPLOY_MIN_OCCUPANCY": "0.8"}, warnings=warnings,
    )
    details = [w for w in warnings if "gated out node" in w]
    assert len(details) == WARNING_DETAIL_CAP
    assert warnings[-1].endswith(
        "... and 10 more; run 'axrange twin-config show unit-obs' for the full table"
    )


def test_twin_config_show_section_is_uncapped():
    report = deploy_gate_report(
        "gate-cast", catalog_dir=_FIXT_DIR,
        param_overrides={"DEPLOY_MIN_OCCUPANCY": "0.8"},
    )
    # 3 gated nodes + 2 segments + 2 conduits + 2 pruned router legs + 1 kept
    # router.
    assert len(report.detail_lines()) == 10
    assert len(report.warning_lines(cap=2)) == len(report.summary_lines()) + 3


# ===========================================================================
# §6 / D1 (c) -- threshold recovery via `scenario.labels`, confirmed against
# the recorded `ir_hash`.
# ===========================================================================
def test_labels_are_emitted_only_when_a_block_exists():
    ir_with, _p, _r = _expand(_cast(), "0.8")
    assert ir_with["scenario"]["labels"] == {
        "deploy_min_occupancy": "0.8", "deploy_min_role_confidence": "0",
    }
    ir_without, _p, _r = _expand(_obs_config(None), "0.8")
    assert "labels" not in ir_without["scenario"]


# `test_labels_merge_into_an_existing_map_rather_than_replacing_it` REMOVED
# (review S4): it re-implemented `_build_ir`'s merge in the test body and then
# asserted its own arithmetic -- it could not have failed if the expander
# stopped merging. No twin-config field can set `scenario.labels`, so the
# merge branch is defensive-only and has nothing real to drive it; the code
# comment at the emission site carries the intent instead.


@pytest.mark.parametrize("value,expected", [
    (0.8, "0.8"), (0.0, "0"), (1.0, "1"), (0.5, "0.5"),
    (0.727273, "0.727273"), (0.1 + 0.7, "0.8"),
])
def test_normalize_threshold_is_stable_bytes(value, expected):
    """The label values join `ir_hash`: `str(0.1 + 0.7)` is
    '0.7999999999999999', which would mint a different `plan_id` for the same
    intended bar."""
    assert normalize_threshold(value) == expected


def test_the_threshold_joins_ir_hash():
    from axrange.planner.dag import hash_ir

    ir_a, _p, _r = _expand(_cast(), "0.8")
    ir_b, _p, _r = _expand(_cast(), "0.9")
    assert hash_ir(ir_a) != hash_ir(ir_b)


def test_confirm_deploy_gate_confirms_the_threshold_actually_used():
    from axrange.planner.dag import hash_ir

    ir, _params, _report = _expand(_cast(), "0.5")
    record = confirm_deploy_gate(
        "gate-cast", hash_ir(ir), catalog_dir=_FIXT_DIR,
        param_overrides={"DEPLOY_MIN_OCCUPANCY": "0.5"},
    )
    assert record is not None
    assert record.min_occupancy == 0.5
    assert record.gate_active is True
    assert record.emitted_nodes == 8
    assert {g.name for g in record.gated_out} == {
        "bridge-host", "ghost-host",            # nodes below the bar
        "thin", "ghost",                        # their segments, cascaded
        "core->thin", "core->ghost",            # conduits into them
        "gw:thin", "gw:ghost",                  # the router legs that followed
    }


def test_confirm_deploy_gate_unconfirms_the_wrong_threshold():
    from axrange.planner.dag import hash_ir

    ir, _params, _report = _expand(_cast(), "0.5")
    assert confirm_deploy_gate(
        "gate-cast", hash_ir(ir), catalog_dir=_FIXT_DIR,
        param_overrides={"DEPLOY_MIN_OCCUPANCY": "0.8"},
    ) is None


def test_confirm_deploy_gate_unconfirms_a_drifted_config(tmp_path):
    """A config edited since deploy can no longer reproduce the recorded
    `ir_hash` -- the caller must report UNCONFIRMED, never a number nobody
    can stand behind."""
    import shutil
    from pathlib import Path

    from axrange.planner.dag import hash_ir
    from ruamel.yaml import YAML

    src = Path(_FIXT_DIR) / "gate-cast"
    dst = tmp_path / "gate-cast"
    shutil.copytree(src, dst)
    ir, _params, _report = _expand(_cast(), "0.8")
    recorded = hash_ir(ir)
    assert confirm_deploy_gate(
        "gate-cast", recorded, catalog_dir=tmp_path,
        param_overrides={"DEPLOY_MIN_OCCUPANCY": "0.8"},
    ) is not None

    y = YAML()
    doc = y.load((dst / "gate-cast.yaml").read_text())
    doc["twin_config"]["nodes"].append(
        {"name": "added-later", "image": "debian-12-cloud@v1", "net": "core"}
    )
    with (dst / "gate-cast.yaml").open("w") as f:
        y.dump(doc, f)
    assert confirm_deploy_gate(
        "gate-cast", recorded, catalog_dir=tmp_path,
        param_overrides={"DEPLOY_MIN_OCCUPANCY": "0.8"},
    ) is None


def test_confirm_deploy_gate_returns_none_for_a_config_that_is_gone():
    assert confirm_deploy_gate("no-such-config", "deadbeef", catalog_dir=_FIXT_DIR) is None


def test_confirm_deploy_gate_confirms_a_blockless_config_as_gate_inactive():
    """A config with no observation data still confirms (its `ir_hash`
    reproduces) -- and says `gate_active: False` rather than reporting a
    threshold that was never applied to anything."""
    from axrange.planner.dag import hash_ir
    from axrange.twinconfig.expand import expand_twin_config

    ir, _params = expand_twin_config("sme-flat")
    record = confirm_deploy_gate("sme-flat", hash_ir(ir))
    assert record is not None and record.gate_active is False


# ===========================================================================
# §9.7 -- param typing. `coerce_scalar("abc")` yields the STRING "abc", which
# must fail loudly rather than compare falsy.
# ===========================================================================
@pytest.mark.parametrize("bad", ["abc", "1.5", "true", "-0.1"])
def test_a_bad_threshold_override_raises(bad):
    with pytest.raises(TwinConfigParamError):
        _expand(_cast(), bad)


@pytest.mark.parametrize("bad", ["abc", "true"])
def test_a_bad_threshold_override_raises_without_a_declared_default(bad):
    """The fixture DECLARES `DEPLOY_MIN_OCCUPANCY: 0.8`, so `_coerce_like`
    catches a bad override there. A config that does NOT declare it reaches
    `coerce_scalar` instead, which happily yields a str/bool -- this is the
    path `gate._threshold` has to catch itself."""
    with pytest.raises(TwinConfigParamError):
        _expand(_obs_config(None), bad)


def test_an_override_coerces_to_float_against_the_declared_default():
    _ir, params, report = _expand(_cast(), "0.5")
    assert params["DEPLOY_MIN_OCCUPANCY"] == 0.5
    assert report.thresholds.min_occupancy == 0.5


def test_an_undeclared_unoverridden_threshold_falls_back_to_the_builtin():
    _ir, _params, report = _expand(_obs_config(None))
    assert report.thresholds.min_occupancy == DEFAULT_MIN_OCCUPANCY == 0.8
    assert report.thresholds.min_role_confidence == DEFAULT_MIN_ROLE_CONFIDENCE == 0.0


def test_a_bad_threshold_raises_even_when_no_block_would_be_gated():
    """The threshold is resolved unconditionally: a typo'd `--param` must not
    be silently ignored just because the config happens to carry no
    observation data."""
    with pytest.raises(TwinConfigParamError):
        expand_twin_config_raw(
            _obs_config(None), param_overrides={"DEPLOY_MIN_ROLE_CONFIDENCE": "later"},
        )


# ===========================================================================
# M1 -- "a router leg is derived plumbing, not observation evidence: a router
# FOLLOWS its segments" (ruling, 2026-09-04). The first cut counted router
# legs, which refused every real cast: the compiler's single `gw` legs every
# segment, so nothing could ever be empty and every below-bar segment tripped
# the disagreement error.
# ===========================================================================
def test_a_gated_segment_takes_the_routers_leg_with_it():
    ir, _params, report = _expand(_cast(), "0.8")
    gw = next(n for n in ir["layers"]["infra"]["nodes"] if n["id"] == "gw")
    # `gw` legs all four segments in the fixture, exactly like the real cast.
    assert [leg["net"] for leg in gw["nets"]] == ["core", "edge"]
    assert {g.name for g in report.gated_segments} == {"thin", "ghost"}
    # ...and the whole expansion SUCCEEDED, which is the actual regression.
    assert _net_ids(ir) == ["core", "edge"]


def test_pruned_router_legs_are_reported_not_silent():
    warnings: list[str] = []
    expand_twin_config_raw(
        _cast(), param_overrides={"DEPLOY_MIN_OCCUPANCY": "0.8"}, warnings=warnings,
    )
    joined = "\n".join(warnings)
    assert "pruned router-leg 'gw:thin'" in joined
    assert "pruned router-leg 'gw:ghost'" in joined
    assert "router leg(s) pruned" in joined  # and in the counts line


def _cast_with_router_only_edge() -> dict:
    """The fixture with `edge` reduced to a router-only segment -- the shape
    of a router<->router transit link or a WAN uplink."""
    cfg = _cast()
    cfg["twin_config"]["nodes"] = [
        n for n in cfg["twin_config"]["nodes"] if n["name"] != "edge-host"
    ]
    return cfg


@pytest.mark.parametrize("threshold", ["0.0", "0.8", "1.0"])
def test_a_router_only_segment_survives_the_emptiness_cascade(threshold):
    """A segment whose only legs are routers has no host occupant BY
    CONSTRUCTION, so the gate cannot have emptied it. Dropping it took its
    routers with it as "stranded" and turned a threshold change into the
    topology break the router exemption exists to prevent (§3.4)."""
    ir, _params, report = _expand(_cast_with_router_only_edge(), threshold)
    assert "edge" in _net_ids(ir)
    gw = next(n for n in ir["layers"]["infra"]["nodes"] if n["id"] == "gw")
    assert "edge" in [leg["net"] for leg in gw["nets"]]
    assert "edge" not in {g.name for g in report.gated_segments}


def test_a_router_only_segment_leaves_only_via_its_own_block():
    """...and when it does, its router legs are pruned exactly as before:
    routers still follow their segments."""
    cfg = _cast_with_router_only_edge()
    for seg in cfg["twin_config"]["segments"]:
        if seg["name"] == "edge":
            seg["observation"] = {
                "windows_seen": 2, "windows_total": 11, "occupancy": 0.181818,
            }
    ir, _params, report = _expand(cfg, "0.5")
    assert "edge" not in _net_ids(ir)
    edge = next(g for g in report.gated_segments if g.name == "edge")
    assert edge.metric == "occupancy"        # its OWN block, not the cascade
    gw = next(n for n in ir["layers"]["infra"]["nodes"] if n["id"] == "gw")
    assert "edge" not in [leg["net"] for leg in gw["nets"]]
    assert "gw:edge" in {g.name for g in report.pruned_router_legs}


def test_a_non_router_node_legging_a_gated_segment_still_hard_errors():
    """The disagreement check survives the router rule -- it just no longer
    fires on a router's own leg."""
    cfg = _cast()
    for seg in cfg["twin_config"]["segments"]:
        if seg["name"] == "core":
            seg["observation"] = {
                "windows_seen": 1, "windows_total": 11, "occupancy": 0.090909,
            }
    with pytest.raises(TwinConfigShapeError) as e:
        _expand(cfg, "0.5")
    msg = str(e.value)
    assert "segment 'core'" in msg and "still have a leg on it" in msg
    assert "always-on" in msg      # a HOST, named
    assert "'gw'" not in msg       # never the router


def test_a_router_stranded_of_every_leg_is_dropped_and_reported():
    cfg = {
        "twin_config": {
            "id": "stranded",
            "segments": [
                {"name": "a", "cidr": "10.70.0.0/24"},
                {"name": "b", "cidr": "10.70.1.0/24"},
            ],
            "nodes": [
                # Legs ONLY `a`, which is about to lose its single host.
                {"name": "gw", "kind": "router", "image": "img@v1", "net": "a"},
                {"name": "thin-host", "image": "img@v1", "net": "a",
                 "observation": {
                     "windows_seen": 1, "windows_total": 11, "occupancy": 0.090909}},
                {"name": "b-host", "image": "img@v1", "net": "b",
                 "observation": {
                     "windows_seen": 11, "windows_total": 11, "occupancy": 1.0}},
            ],
        }
    }
    ir, _params, report = _expand(cfg, "0.8")
    assert _node_ids(ir) == ["b-host"]
    assert _net_ids(ir) == ["b"]
    stranded = next(g for g in report.gated_nodes if g.name == "gw")
    assert stranded.reason == "every segment it had a leg on was gated out"


# ===========================================================================
# M3 -- a gated spec's WOULD-BE ids can belong to a different, EMITTED spec.
# ===========================================================================
def test_an_emitted_node_id_is_not_treated_as_gated_out():
    """A gated-out `count: 3` `worker` would have produced `worker01`; an
    emitted, blockless spec literally NAMED `worker01` owns that id. Refusing
    a `data:` reference to it would be a false refusal -- and its remedy
    ("lower the threshold") would not even fix it."""
    cfg = _cast()
    cfg["twin_config"]["nodes"].append({
        "name": "worker01", "role": "worker", "image": "debian-12-cloud@v1",
        "requires": ["linux"], "net": "core",
    })
    cfg["twin_config"]["data"][0]["on_role"] = "worker01"
    ir, _params, report = _expand(cfg, "0.8")          # must not raise
    assert "worker01" in _node_ids(ir)
    assert ir["overlays"]["data"][0]["host"] == "worker01"
    assert "worker" in {g.name for g in report.gated_nodes}


# ===========================================================================
# S6 -- `role_confidence` on a SEGMENT is not applicable; parsing it and then
# ignoring it is the false-affordance shape (LES-172).
# ===========================================================================
def test_a_segment_block_carrying_role_confidence_is_rejected():
    cfg = _obs_config(None)
    cfg["twin_config"]["segments"][0]["observation"] = {
        "windows_seen": 11, "windows_total": 11, "occupancy": 1.0,
        "role_confidence": 0.9,
    }
    with pytest.raises(TwinConfigShapeError) as e:
        expand_twin_config_raw(cfg)
    msg = str(e.value)
    assert "segment 'lan'" in msg and "role_confidence" in msg
    assert "not applicable" in msg


# ===========================================================================
# S5 -- an int-typed declared default must not poison a float override.
# ===========================================================================
@pytest.mark.parametrize("name,override", [
    ("DEPLOY_MIN_OCCUPANCY", "0.5"),
    ("DEPLOY_MIN_ROLE_CONFIDENCE", "0.9"),
])
def test_an_int_declared_threshold_default_still_accepts_a_float_override(name, override):
    """`params._coerce_like` types an override against the declared default,
    so `DEPLOY_MIN_ROLE_CONFIDENCE: 0` (the natural way to write "off") used
    to make `--param DEPLOY_MIN_ROLE_CONFIDENCE=0.9` fail as "not a valid
    int". Fixed for the two gate params only -- `N_WORKSTATIONS: 25` must
    keep rejecting `=2.5`."""
    cfg = _obs_config({"windows_seen": 11, "windows_total": 11, "occupancy": 1.0})
    cfg["twin_config"]["params"] = {"DEPLOY_MIN_OCCUPANCY": 1, "DEPLOY_MIN_ROLE_CONFIDENCE": 0}
    _ir, params, report = expand_twin_config_raw_with_gate(
        cfg, param_overrides={name: override},
    )
    assert params[name] == float(override)
    assert getattr(report.thresholds, name.replace("DEPLOY_MIN_", "min_").lower()) == float(
        override
    )


def test_an_int_declared_non_threshold_param_still_rejects_a_float_override():
    """The narrow fix must NOT widen into `_coerce_like`."""
    cfg = _obs_config(None)
    cfg["twin_config"]["params"] = {"N_WORKSTATIONS": 25}
    with pytest.raises(TwinConfigParamError, match="not a valid int"):
        expand_twin_config_raw(cfg, param_overrides={"N_WORKSTATIONS": "2.5"})


# ===========================================================================
# S3 -- confirm against the hash the PLANNER would record (LES-187: hashing
# the expander's own dict on both sides is circular).
# ===========================================================================
def test_confirm_deploy_gate_confirms_the_hash_the_planner_records(tmp_path):
    """`build_plan` hashes `parse_ir(...).raw` -- the YAML round trip, not the
    expander's in-memory dict. `confirm_deploy_gate` hashes the latter, so the
    two must agree byte-for-byte (they do; ruamel emits the label values as
    quoted strings). Pin it: a serializer change that turned `"0.8"` into
    `0.8` would silently unconfirm every gated twin."""
    from axrange.ir.parser import parse_ir
    from axrange.planner.dag import hash_ir
    from axrange.twinconfig.expand import materialize_twin_config

    out = tmp_path / "materialized.yaml"
    materialize_twin_config(
        "gate-cast", catalog_dir=_FIXT_DIR,
        param_overrides={"DEPLOY_MIN_OCCUPANCY": "0.8"}, out_path=out,
    )
    recorded = hash_ir(parse_ir(str(out)).raw)
    assert parse_ir(str(out)).raw["scenario"]["labels"] == {
        "deploy_min_occupancy": "0.8", "deploy_min_role_confidence": "0",
    }
    record = confirm_deploy_gate(
        "gate-cast", recorded, catalog_dir=_FIXT_DIR,
        param_overrides={"DEPLOY_MIN_OCCUPANCY": "0.8"},
    )
    assert record is not None and record.min_occupancy == 0.8


# ===========================================================================
# §5 -- "a threshold change is add/remove only, never a mutation", i.e. the
# claim that `te deploy --update` can move the bar on a STANDING twin. The
# reviewer asked for it to be measured rather than asserted in prose: the
# plandiff machinery itself is exercised for real in
# `tests/test_twin_incremental_update.py`; this pins only the gate's own
# contribution to it.
# ===========================================================================
def _plan_at(threshold: str, manifests, tmp_path, *, plan_id: str):
    from axrange.ir.parser import parse_ir
    from axrange.planner import build_plan
    from axrange.planner.cluster import ClusterContext
    from axrange.twinconfig.expand import materialize_twin_config

    out = tmp_path / f"ir-{threshold}.yaml"
    materialize_twin_config(
        "gate-cast", catalog_dir=_FIXT_DIR,
        param_overrides={"DEPLOY_MIN_OCCUPANCY": threshold}, out_path=out,
    )
    parsed = parse_ir(path=str(out))
    # plan_id PINNED across both expansions, exactly as `--update` pins the
    # standing deploy's (a fresh one would renumber every VMID -- LES-126).
    plan = build_plan(
        parsed.ir, manifests, ClusterContext.single_node("pxtest1"),
        raw_ir=parsed.raw, plan_id=plan_id,
    )
    plan.freeze()
    return plan


def _persisted_rows(plan):
    from axrange.planner.dag import OpInstance
    from axrange.planner.state import barrier_to_row, op_to_row

    return [
        op_to_row(n, deploy_id="d") if isinstance(n, OpInstance)
        else barrier_to_row(n, deploy_id="d")
        for n in plan.all_nodes().values()
    ]


def test_a_threshold_change_is_adds_or_removes_only_never_a_mutation(manifests, tmp_path):
    """Work order §5: raising the bar on a standing twin is an `--update
    --allow-destroy` REMOVE, lowering it is an `--update` ADD, and neither
    trips `--update`'s hard mutation refusal. Measured under ONE pinned
    `plan_id`, which is what `--update` itself does."""
    from axrange.planner.plandiff import diff_plan

    pin = "gate-cast-0e4f7d58"
    standing = _plan_at("0.5", manifests, tmp_path, plan_id=pin)
    rows = _persisted_rows(standing)

    raised = diff_plan(_plan_at("0.8", manifests, tmp_path, plan_id=pin), rows)
    assert raised.mutated == frozenset(), raised.mutations
    assert raised.removed and not raised.added

    lowered = diff_plan(_plan_at("0.0", manifests, tmp_path, plan_id=pin), rows)
    assert lowered.mutated == frozenset(), lowered.mutations
    assert lowered.added and not lowered.removed


# ===========================================================================
# SHIPPED CONTENT (LES-185). A hand-built fixture cannot prove the cascade
# behaves on the topologies this repo actually ships: both of these failed
# before the "emptied BY the gate" rule, with a single annotated host that
# gated nothing out at all.
# ===========================================================================
def _shipped_with_one_annotation(config_id: str, node_name: str) -> dict:
    """A shipped twin-config with ONE fully-observed host annotated -- enough
    to switch the gate on (`report.active`) while gating nothing out."""
    raw, _path = load_twin_config_raw(config_id)
    cfg = copy.deepcopy(raw)
    for node in cfg["twin_config"]["nodes"]:
        if node["name"] == node_name:
            node["observation"] = {
                "windows_seen": 11, "windows_total": 11, "occupancy": 1.0,
            }
            break
    else:  # pragma: no cover -- a shipped-content rename would land here
        raise AssertionError(f"{config_id} has no node named {node_name!r}")
    return cfg


@pytest.mark.parametrize("threshold", ["0.0", "0.8", "1.0"])
def test_dmz_perimeter_keeps_its_internet_uplink_at_every_threshold(threshold):
    """`internet` is a WAN/uplink segment whose only leg is the `edge`
    router. It was dropped at EVERY threshold -- including 0.0 -- and `edge`
    lost its uplink."""
    ir, _params, report = _expand(
        _shipped_with_one_annotation("dmz-perimeter", "waf"), threshold,
    )
    assert _net_ids(ir) == ["internet", "dmz", "internal"]
    edge = next(n for n in ir["layers"]["infra"]["nodes"] if n["id"] == "edge")
    assert [leg["net"] for leg in edge["nets"]] == ["internet", "dmz", "internal"]
    assert report.active is True          # the gate really did run...
    assert not report.gated               # ...and correctly removed nothing


def test_datacenter_fabric_keeps_its_router_to_router_transit_link():
    """`fab-link` is the `>=2 router legs, no explicit gateway` TRANSIT case
    the IP allocator already models. Dropping it also swept `fab-r1`/`fab-r2`
    as stranded routers."""
    cfg = _shipped_with_one_annotation("datacenter", "web-tier")
    ir, _params, _report = expand_twin_config_raw_with_gate(
        cfg, param_overrides={"FABRIC": "true", "DEPLOY_MIN_OCCUPANCY": "0.8"},
    )
    assert "fab-link" in _net_ids(ir)
    assert {"fab-r1", "fab-r2"} <= set(_node_ids(ir))
