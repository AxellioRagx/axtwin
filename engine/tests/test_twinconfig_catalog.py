"""Catalog-level validation for the twin-config starter set
(`docs/WORK_ORDER_twin_configs.md` §4): each of the five shipped
`twin-configs/*.yaml` expands to a Scenario IR that validates through all
four passes, both at default params and at a non-default param set; the
declared `tap_points[]` materialize (checked in
`test_twinconfig_tap_materialize.py`'s deploy round trip); persona defaults
are present on node roles.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from ruamel.yaml import YAML

from axrange.twinconfig.expand import expand_twin_config
from axrange.twinconfig.registry import load_twin_config

# (config_id, non-default param overrides) -- exercises count/domain/OT
# protocol/replication/fabric knobs across the five starter configs.
_NON_DEFAULT_PARAMS = {
    "sme-flat": {"N_WORKSTATIONS": "7"},
    "corp-enterprise": {"N_WORKSTATIONS": "2", "DOMAIN": "test.example.lab"},
    "datacenter": {"N_APP": "5", "REPLICATION": "true", "FABRIC": "true"},
    "ics-ot": {"N_PLC": "4", "OT_PROTOCOL": "dnp3"},
    "dmz-perimeter": {"N_DMZ_WEB": "1"},
}


@pytest.mark.parametrize("config_id", sorted(_NON_DEFAULT_PARAMS))
def test_validates_at_default_params(config_id, manifests):
    ir_dict, params = expand_twin_config(config_id)
    result = _validate_with_manifests(ir_dict, manifests)
    assert not result.errors, [(f.rule_id, f.message) for f in result.errors]


@pytest.mark.parametrize("config_id", sorted(_NON_DEFAULT_PARAMS))
def test_validates_at_non_default_params(config_id, manifests):
    overrides = _NON_DEFAULT_PARAMS[config_id]
    ir_dict, params = expand_twin_config(config_id, param_overrides=overrides)
    for key, raw in overrides.items():
        # confirm the override actually took (not silently ignored)
        assert str(params[key]).lower() == raw.lower() or params[key] == raw
    result = _validate_with_manifests(ir_dict, manifests)
    assert not result.errors, [(f.rule_id, f.message) for f in result.errors]


def _validate_with_manifests(ir_dict, manifests):
    from axrange.validator.engine import validate_full

    y = YAML(typ="safe")
    import io

    buf = io.StringIO()
    y.dump(ir_dict, buf)
    return validate_full(text=buf.getvalue(), manifests=manifests)


def test_datacenter_fabric_off_by_default_has_no_fabric_block():
    ir_dict, params = expand_twin_config("datacenter")
    assert params["FABRIC"] is False
    assert "fabric" not in ir_dict
    net_ids = {n["id"] for n in ir_dict["layers"]["infra"]["networks"]}
    assert "fab-link" not in net_ids


def test_datacenter_fabric_on_adds_fabric_block_and_validates(manifests):
    ir_dict, params = expand_twin_config("datacenter", param_overrides={"FABRIC": "true"})
    assert params["FABRIC"] is True
    assert ir_dict["fabric"]["regions"][0]["igp"] == "ospf"
    result = _validate_with_manifests(ir_dict, manifests)
    assert not result.errors, [(f.rule_id, f.message) for f in result.errors]


# ---------------------------------------------------------------------------
# "The expander must not bypass validation" (tester deepening, docs/
# WORK_ORDER_twin_configs.md §3 boundary): expansion never imports/calls the
# validator itself (LES-025 -- see expand.py's module docstring), so a
# twin-config that expands SHAPE-cleanly (no TwinConfigShapeError/
# TapPointMaterializationError) to a semantically-invalid IR must still be
# caught by the real, unchanged four-pass validator downstream -- not waved
# through as if expansion implied validity.
# ---------------------------------------------------------------------------
def test_expanded_ir_with_a_real_capability_mismatch_is_rejected_by_the_validator(manifests):
    from axrange.twinconfig.expand import expand_twin_config_raw

    cfg = {
        "twin_config": {
            "id": "broken-caps",
            "segments": [{"name": "lan", "cidr": "10.50.0.0/24"}],
            "nodes": [
                {"name": "gw", "kind": "router", "image": "debian-12-cloud@v1",
                 "requires": ["linux"], "net": "lan"},
                # requires "windows" but points at a LINUX image -- a real
                # CAP-010/CAP-030 capability mismatch the validator must
                # catch; expansion itself has no opinion on capabilities.
                {"name": "badnode", "image": "debian-12-cloud@v1",
                 "requires": ["windows"], "net": "lan"},
            ],
        }
    }
    ir_dict, _params = expand_twin_config_raw(cfg)
    # Expansion itself raised nothing -- it produced a shape-valid IR dict.
    assert ir_dict["layers"]["infra"]["nodes"]

    result = _validate_with_manifests(ir_dict, manifests)
    rule_ids = {f.rule_id for f in result.errors}
    assert "CAP-010" in rule_ids or "CAP-030" in rule_ids, (
        "expander bypassed validation -- a real capability mismatch in the "
        f"expanded IR produced no validator error at all: {result.errors}"
    )


# ---------------------------------------------------------------------------
# W1's central safety property (`docs/WORK_ORDER_role_persona_tier.md` §7.3).
# ---------------------------------------------------------------------------
_PLAN_IDS_BEFORE_THE_ROLE_SPLIT = {
    "sme-flat": "sme-flat-d6ab9fe2",
    "corp-enterprise": "corp-enterprise-86423faa",
    "datacenter": "datacenter-09528ee1",
    "ics-ot": "ics-ot-34fd6ef5",
    "dmz-perimeter": "dmz-perimeter-dcf875e2",
}

# The standing CAST config (`twins/corpapcn-twin/`), added by the
# deploy gate (`docs/WORK_ORDER_deploy_gate.md` §5, blocking): it carries no
# `observation:` block either, so the gate must leave it byte-identical too.
# Not cosmetic -- the standing `corpapcn-twin-0e4f7d58` (138 VMs, dev45) is
# the fidelity scorecard's first subject, and a `plan_id` change orphans it.
_CAST_CATALOG_DIR = str(Path(__file__).resolve().parents[2] / "catalog" / "twins")
_CAST_PLAN_IDS = {"corpapcn-twin": "corpapcn-twin-b6aaa3c5"}

_ALL_PINNED = {
    **{cid: (None, pid) for cid, pid in _PLAN_IDS_BEFORE_THE_ROLE_SPLIT.items()},
    **{cid: (_CAST_CATALOG_DIR, pid) for cid, pid in _CAST_PLAN_IDS.items()},
}


@pytest.mark.parametrize("config_id", sorted(_ALL_PINNED))
def test_shipped_config_plan_id_survived_the_name_role_split(config_id, manifests):
    """`role:` -> `name:` + optional `role:` must leave every node id -- and
    therefore the expanded IR, its `ir_hash`, and the derived `plan_id` --
    byte-identical. The deploy gate (2026-09-04) must too: it is structurally
    a no-op for a config carrying no `observation:` block, which is all six
    of these (LES-185 -- a green hand-fixture suite is not evidence about
    shipped content; `tests/test_twinconfig_gate.py`'s fixture cannot prove
    this).

    This is not a cosmetic pin. `plan_id` keys VMIDs, VM names, the SDN zone,
    vnets and the underlay bridge (`naming.vmid_for_op` hashes
    f"{plan_id}:{op_id}"), and a standing deploy is found by it (LES-126). A
    change here means every existing deploy of these configs is orphaned and
    every VM would be renumbered on redeploy -- so it must fail loudly rather
    than be discovered on a cluster.

    Values captured from the pre-split tree on 2026-08-04, before any W1 edit;
    the cast's on 2026-09-04, before any deploy-gate edit.
    """
    from axrange.planner.dag import derive_plan_id, hash_ir

    catalog_dir, expected = _ALL_PINNED[config_id]
    ir_dict, _params = expand_twin_config(config_id, catalog_dir=catalog_dir)
    plan_id = derive_plan_id(ir_dict["scenario"]["id"], hash_ir(ir_dict))
    assert plan_id == expected


@pytest.mark.parametrize("config_id", sorted(_ALL_PINNED))
def test_shipped_config_emits_no_deploy_gate_labels(config_id):
    """`scenario.labels` is emitted ONLY when the gate actually ran (>=1
    `observation:` block). That condition is precisely what keeps the pin
    above green -- assert the mechanism, not just its consequence, so a
    regression says WHY the hash moved.
    """
    catalog_dir, _expected = _ALL_PINNED[config_id]
    ir_dict, _params = expand_twin_config(config_id, catalog_dir=catalog_dir)
    assert "labels" not in ir_dict["scenario"]


# The HAND-AUTHORED configs only. The cast (`twin-configs-cast/`) is
# deliberately excluded from the two `listen:` assertions below: the
# aximprint half of `docs/WORK_ORDER_protocol_faithful_services.md` is adding
# a declared `listen:` to the cast BY DESIGN, and a test that forbids it
# would read as "this is a regression" when it is the feature landing. The
# `plan_id` pin above still covers the cast -- which is the point: when the
# cast starts declaring listens its hash MOVES, and re-pinning it is a
# deliberate act with a real consequence (every standing deploy of it is
# orphaned, LES-126), not something to discover afterwards.
_HAND_AUTHORED_PINNED = sorted(_PLAN_IDS_BEFORE_THE_ROLE_SPLIT)


@pytest.mark.parametrize("config_id", _HAND_AUTHORED_PINNED)
def test_hand_authored_config_declares_no_listen_set(config_id):
    """The MECHANISM behind the pin above surviving the declared-listen set
    (`docs/WORK_ORDER_protocol_faithful_services.md` S1, D2(b)): no
    hand-authored config authors `listen:`, so no `App.listen` is emitted, so
    their expanded IR -- and therefore `ir_hash`/`plan_id` -- cannot move.
    Asserted as its own statement rather than left implicit in the hash, so a
    regression says WHY the hash moved (LES-213's three-assertion rule).

    It also pins the second, sharper property: with no `listen:` anywhere,
    `pass1_emit` puts NO `node_listen` key on any `clone-vm`, which is what
    keeps `te deploy --update` diffing clean against every standing deploy of
    these configs (an unconditional key would classify every clone-vm as
    Mutated -- LES-185).
    """
    catalog_dir, _expected = _ALL_PINNED[config_id]
    ir_dict, _params = expand_twin_config(config_id, catalog_dir=catalog_dir)
    for app in ir_dict["layers"].get("apps") or []:
        assert "listen" not in app, (
            f"{config_id}: {app.get('id')} declares listen -- if this is "
            "intentional, the `plan_id` pin above must be re-pinned "
            "DELIBERATELY (it orphans every standing deploy of this config)"
        )


@pytest.mark.parametrize("config_id", _HAND_AUTHORED_PINNED)
def test_hand_authored_config_emits_no_node_listen_input(config_id, manifests):
    """The consequence of the above, measured on the PLAN rather than the IR
    -- the level `plandiff` actually compares."""
    import tempfile

    from axrange.ir.parser import parse_ir
    from axrange.planner import build_plan
    from axrange.planner.cluster import ClusterContext
    from axrange.twinconfig.expand import materialize_twin_config
    from axrange.twinconfig.registry import load_twin_config_raw

    catalog_dir, _expected = _ALL_PINNED[config_id]
    # `install: [{script: ...}]` resolves against the CONFIG's own directory,
    # not the tempdir its expansion lands in -- exactly what `cli.main.
    # _scenario_dir` threads for a `--config` deploy. The cast's responder
    # scripts live there.
    _raw, config_path = load_twin_config_raw(config_id, catalog_dir=catalog_dir)

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / f"{config_id}.yaml"
        materialize_twin_config(config_id, catalog_dir=catalog_dir, out_path=out)
        parsed = parse_ir(path=str(out))
        plan = build_plan(
            parsed.ir, manifests, ClusterContext.single_node("pxtest1"),
            raw_ir=parsed.raw, scenario_dir=str(Path(config_path).parent),
        )
    for op in plan.ops.values():
        if op.op_name == "clone-vm":
            assert "node_listen" not in op.inputs_resolved, (
                f"{op.scope} carries node_listen -- see the sibling test's "
                "message about re-pinning deliberately"
            )


@pytest.mark.parametrize("config_id", sorted(_ALL_PINNED))
def test_shipped_config_is_unaffected_by_a_deploy_gate_threshold(config_id):
    """A threshold has nothing to bite on in a blockless config: expansion at
    the strictest possible bar must be byte-identical to the default."""
    from axrange.planner.dag import hash_ir

    catalog_dir, _expected = _ALL_PINNED[config_id]
    default_ir, _params = expand_twin_config(config_id, catalog_dir=catalog_dir)
    strict_ir, _params = expand_twin_config(
        config_id, catalog_dir=catalog_dir,
        param_overrides={"DEPLOY_MIN_OCCUPANCY": "1.0"},
    )
    assert hash_ir(strict_ir) == hash_ir(default_ir)
