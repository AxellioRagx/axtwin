"""Unit tests for `axrange.twinconfig.expand` — the twin-config -> Scenario
IR expander (docs/WORK_ORDER_twin_configs.md §2).

Uses small, hand-built twin-config dicts (not the shipped `twin-configs/`
catalog — that's covered end-to-end by `test_twinconfig_catalog.py`) so
each behavior is exercised in isolation.

No `tap_points:`/tap-point materialization coverage any more (`docs/
WORK_ORDER_tap_eligibility.md` D3, Authority: user, 2026-07-28) -- the
named/labeled tap-point catalog + `_apply_tap_points` are retired outright,
not relocated; a `tap_points:` key now fails loudly at expansion time
instead (LES-111). See `test_twinconfig_tap_materialize.py` for what
replaced the deploy-round-trip coverage this file used to link to.
"""
from __future__ import annotations

import pytest

from axrange.twinconfig.errors import TwinConfigParamError, TwinConfigShapeError
from axrange.twinconfig.expand import expand_twin_config_raw


def _flat_config(**overrides) -> dict:
    base = {
        "twin_config": {
            "id": "unit-flat",
            "params": {"N": 2},
            "segments": [{"name": "lan", "cidr": "10.99.0.0/24"}],
            "nodes": [
                {"name": "gw", "kind": "router", "image": "img@v1",
                 "requires": ["linux"], "net": "lan"},
                {"name": "ws", "image": "img@v1", "requires": ["linux"],
                 "count": "${N:2}", "net": "lan"},
            ],
        }
    }
    base["twin_config"].update(overrides)
    return base


def test_flat_expand_shape():
    ir, params = expand_twin_config_raw(_flat_config())
    assert params["N"] == 2
    assert ir["scenario"]["id"] == "unit-flat"
    net = ir["layers"]["infra"]["networks"][0]
    assert net["id"] == "lan"
    assert "tap" not in net  # no tap-related content anywhere (D1/D3)
    node_ids = {n["id"] for n in ir["layers"]["infra"]["nodes"]}
    assert node_ids == {"gw", "ws01", "ws02"}
    # router's lone leg on a LAN segment auto-gets the gateway address.
    gw = next(n for n in ir["layers"]["infra"]["nodes"] if n["id"] == "gw")
    assert gw["nets"][0]["ip"] == net["gateway"]


def test_count_param_override_changes_fanout():
    ir, params = expand_twin_config_raw(_flat_config(), param_overrides={"N": "5"})
    assert params["N"] == 5
    node_ids = {n["id"] for n in ir["layers"]["infra"]["nodes"]}
    assert node_ids == {"gw", "ws01", "ws02", "ws03", "ws04", "ws05"}


def test_count_1_uses_bare_role_as_id():
    cfg = _flat_config()
    cfg["twin_config"]["nodes"][1]["count"] = 1
    ir, _ = expand_twin_config_raw(cfg)
    node_ids = {n["id"] for n in ir["layers"]["infra"]["nodes"]}
    assert "ws" in node_ids


def test_missing_param_no_default_raises():
    cfg = _flat_config()
    cfg["twin_config"]["nodes"][1]["count"] = "${UNDECLARED}"
    with pytest.raises(TwinConfigParamError):
        expand_twin_config_raw(cfg)


def test_simple_zone_when_no_segment_wants_vlan():
    ir, _ = expand_twin_config_raw(_flat_config())
    net = ir["layers"]["infra"]["networks"][0]
    assert "vlan" not in net


def test_vlan_zone_assigns_distinct_tags_to_every_segment():
    cfg = _flat_config()
    cfg["twin_config"]["segments"] = [
        {"name": "a", "cidr": "10.1.0.0/24", "zone_type": "vlan"},
        {"name": "b", "cidr": "10.2.0.0/24"},  # "simple" by itself, but the
                                                # SCENARIO is vlan (a wants it)
    ]
    cfg["twin_config"]["nodes"] = [
        {"name": "gw", "kind": "router", "image": "img@v1", "requires": ["linux"],
         "nets": [{"segment": "a"}, {"segment": "b"}]},
    ]
    ir, _ = expand_twin_config_raw(cfg)
    nets = {n["id"]: n for n in ir["layers"]["infra"]["networks"]}
    assert "vlan" in nets["a"] and "vlan" in nets["b"]
    assert nets["a"]["vlan"] != nets["b"]["vlan"]


def test_transit_segment_between_two_routers_gets_no_gateway_and_distinct_ips():
    cfg = {
        "twin_config": {
            "id": "unit-transit",
            "segments": [{"name": "p2p", "cidr": "10.5.0.0/30"}],
            "nodes": [
                {"name": "r1", "kind": "router", "image": "img@v1",
                 "requires": ["linux"], "net": "p2p"},
                {"name": "r2", "kind": "router", "image": "img@v1",
                 "requires": ["linux"], "net": "p2p"},
            ],
        }
    }
    ir, _ = expand_twin_config_raw(cfg)
    net = ir["layers"]["infra"]["networks"][0]
    assert "gateway" not in net
    ips = [n["nets"][0]["ip"] for n in ir["layers"]["infra"]["nodes"]]
    assert len(set(ips)) == 2  # r1 and r2 got DISTINCT addresses, no collision


def test_when_gate_excludes_node_and_segment_by_default():
    cfg = _flat_config()
    cfg["twin_config"]["params"]["FABRIC"] = False
    cfg["twin_config"]["segments"].append(
        {"name": "extra", "cidr": "10.9.0.0/30", "when": "FABRIC"}
    )
    cfg["twin_config"]["nodes"].append(
        {"name": "extra-node", "image": "img@v1", "requires": ["linux"],
         "net": "extra", "when": "FABRIC"}
    )
    ir, _ = expand_twin_config_raw(cfg)
    net_ids = {n["id"] for n in ir["layers"]["infra"]["networks"]}
    node_ids = {n["id"] for n in ir["layers"]["infra"]["nodes"]}
    assert "extra" not in net_ids
    assert "extra-node" not in node_ids


def test_when_gate_includes_when_param_truthy():
    cfg = _flat_config()
    cfg["twin_config"]["params"]["FABRIC"] = False
    cfg["twin_config"]["segments"].append(
        {"name": "extra", "cidr": "10.9.0.0/30", "when": "FABRIC"}
    )
    cfg["twin_config"]["nodes"].append(
        {"name": "extra-node", "image": "img@v1", "requires": ["linux"],
         "net": "extra", "when": "FABRIC"}
    )
    ir, _ = expand_twin_config_raw(cfg, param_overrides={"FABRIC": "true"})
    net_ids = {n["id"] for n in ir["layers"]["infra"]["networks"]}
    node_ids = {n["id"] for n in ir["layers"]["infra"]["nodes"]}
    assert "extra" in net_ids
    assert "extra-node" in node_ids


def test_when_gate_unknown_param_is_a_shape_error():
    cfg = _flat_config()
    cfg["twin_config"]["nodes"][0]["when"] = "TYPO_PARAM"
    with pytest.raises(TwinConfigShapeError):
        expand_twin_config_raw(cfg)


def test_install_generates_app_and_binding():
    cfg = _flat_config()  # count=2 -> one App + one Binding PER fanned instance
    cfg["twin_config"]["nodes"][1]["install"] = [{"pkg": "nginx"}]
    ir, _ = expand_twin_config_raw(cfg)
    apps = ir["layers"]["apps"]
    assert len(apps) == 2
    assert {a["id"] for a in apps} == {"ws01-app", "ws02-app"}
    assert all(a["install"] == [{"pkg": "nginx"}] for a in apps)
    bindings = ir["bindings"]
    assert {(b["app"], b["host"]) for b in bindings} == {
        ("ws01-app", "ws01"), ("ws02-app", "ws02"),
    }


# ---------------------------------------------------------------------------
# `tap_points:` is REMOVED (`docs/WORK_ORDER_tap_eligibility.md` D3,
# Authority: user, 2026-07-28) -- a twin config authors no tap-related
# content at all. A leftover `tap_points:` key from stale/copy-pasted
# content must fail LOUDLY at expansion time, never be silently absorbed
# by `TwinConfig`'s permissive `extra="allow"` schema and vanish the
# author's capture intent with no signal (LES-111) -- the same posture
# `STR-200` enforces on the IR side for a leftover `tap:` field.
# ---------------------------------------------------------------------------
def test_tap_points_key_is_rejected_loudly():
    cfg = _flat_config()
    cfg["twin_config"]["tap_points"] = [
        {"id": "lan", "scope": "segment", "target": "lan"}
    ]
    with pytest.raises(TwinConfigShapeError, match="tap_points"):
        expand_twin_config_raw(cfg)


def test_tap_points_empty_list_is_still_rejected():
    """The mere PRESENCE of the key is the problem, regardless of value --
    an empty list is not treated as "nothing to reject" (mirrors STR-200's
    own "any value" posture on the IR side)."""
    cfg = _flat_config()
    cfg["twin_config"]["tap_points"] = []
    with pytest.raises(TwinConfigShapeError, match="tap_points"):
        expand_twin_config_raw(cfg)


def test_no_tap_points_key_expands_cleanly():
    """A config with no `tap_points:` at all (the only valid shape now)
    expands without ever mentioning tap in the resulting IR."""
    ir, _ = expand_twin_config_raw(_flat_config())
    for net in ir["layers"]["infra"]["networks"]:
        assert "tap" not in net
    for node in ir["layers"]["infra"]["nodes"]:
        assert "tap" not in node
        for leg in node.get("nets", []):
            assert "tap" not in leg


# ---------------------------------------------------------------------------
# Inert-field sweep (2026-08-04, Authority: user) -- five keys traced to ZERO
# consumers were removed from the twin-config vocabulary. Each must now fail
# LOUDLY rather than be silently absorbed by `TCBase`'s `extra="allow"`
# (LES-111), and each message must name where the intent belongs instead.
# ---------------------------------------------------------------------------
def test_removed_segment_purpose_is_rejected():
    cfg = _flat_config()
    cfg["twin_config"]["segments"][0]["purpose"] = "the flat network"
    with pytest.raises(TwinConfigShapeError, match=r"segments\[0\]\.purpose"):
        expand_twin_config_raw(cfg)


def test_removed_node_identity_role_is_rejected():
    cfg = _flat_config()
    cfg["twin_config"]["nodes"][1]["identity_role"] = "workstation"
    with pytest.raises(TwinConfigShapeError, match=r"nodes\[1\]\.identity_role"):
        expand_twin_config_raw(cfg)


def test_removed_conduit_policy_is_rejected():
    cfg = _flat_config()
    cfg["twin_config"]["conduits"] = [{"from": "lan", "to": "lan", "policy": "allow"}]
    with pytest.raises(TwinConfigShapeError, match=r"conduits\[0\]\.policy"):
        expand_twin_config_raw(cfg)


@pytest.mark.parametrize("key", ["groups", "ous"])
def test_removed_top_level_identity_lists_are_rejected(key):
    cfg = _flat_config()
    cfg["twin_config"]["identity"] = {"domain": "corp.axr.lab", key: ["Domain Admins"]}
    with pytest.raises(TwinConfigShapeError, match=rf"identity\.{key}"):
        expand_twin_config_raw(cfg)


def test_per_user_groups_and_ou_are_still_wired():
    """The REMOVAL is scoped to the top-level, whole-domain lists. A user's
    OWN `groups:`/`ou:` are real, wired fields (`_build_ir` reads them into
    `overlays.identity`) and must keep working -- the exact distinction the
    sweep could most easily have got wrong."""
    cfg = _flat_config()
    cfg["twin_config"]["identity"] = {
        "domain": "corp.axr.lab",
        "users": [{"id": "alice", "groups": ["Domain Admins"], "ou": "OU=Staff"}],
    }
    ir, _ = expand_twin_config_raw(cfg)
    alice = ir["overlays"]["identity"][0]
    assert alice["groups"] == ["Domain Admins"]
    assert alice["ou"] == "OU=Staff"


def test_removed_key_is_rejected_regardless_of_value():
    """Mere PRESENCE is the problem -- a falsy value is not "nothing to
    reject" (same posture as the `tap_points: []` case above)."""
    cfg = _flat_config()
    cfg["twin_config"]["segments"][0]["purpose"] = ""
    with pytest.raises(TwinConfigShapeError, match=r"segments\[0\]\.purpose"):
        expand_twin_config_raw(cfg)


def test_removed_key_message_names_the_replacement():
    """A rejection that doesn't say where the intent belongs just moves the
    confusion (the whole reason these fields were a trap)."""
    cfg = _flat_config()
    cfg["twin_config"]["nodes"][0]["identity_role"] = "gw"
    with pytest.raises(TwinConfigShapeError, match="identity.users"):
        expand_twin_config_raw(cfg)


def test_conduit_with_no_joining_router_is_a_shape_error():
    cfg = _flat_config()
    cfg["twin_config"]["segments"].append({"name": "other", "cidr": "10.7.0.0/24"})
    cfg["twin_config"]["conduits"] = [{"from": "lan", "to": "other", "policy": "allow"}]
    with pytest.raises(TwinConfigShapeError):
        expand_twin_config_raw(cfg)


def test_duplicate_node_id_after_fanout_is_a_shape_error():
    cfg = _flat_config()
    cfg["twin_config"]["nodes"].append(
        {"name": "ws01", "image": "img@v1", "requires": ["linux"], "net": "lan"}
    )
    with pytest.raises(TwinConfigShapeError):
        expand_twin_config_raw(cfg)


def test_node_missing_net_and_nets_is_a_shape_error():
    cfg = _flat_config()
    del cfg["twin_config"]["nodes"][1]["net"]
    with pytest.raises(TwinConfigShapeError):
        expand_twin_config_raw(cfg)


def test_no_segments_after_gating_is_a_shape_error():
    cfg = _flat_config()
    cfg["twin_config"]["segments"][0]["when"] = "GATE"
    cfg["twin_config"]["params"]["GATE"] = False
    with pytest.raises(TwinConfigShapeError):
        expand_twin_config_raw(cfg)


def test_missing_twin_config_key_is_a_shape_error():
    with pytest.raises(TwinConfigShapeError):
        expand_twin_config_raw({"not_twin_config": {}})


def test_identity_and_data_overlays_emitted():
    cfg = _flat_config()
    cfg["twin_config"]["identity"] = {
        "domain": "corp.axr.lab",
        "users": [{"id": "alice", "groups": ["Domain Users"]}],
    }
    cfg["twin_config"]["data"] = [
        {"dataset": "pii", "on_role": "ws01", "crown_jewel": True}
    ]
    ir, _ = expand_twin_config_raw(cfg)
    assert ir["overlays"]["identity"][0]["id"] == "alice"
    assert ir["overlays"]["identity"][0]["domain"] == "corp.axr.lab"
    data = ir["overlays"]["data"][0]
    assert data["host"] == "ws01"
    assert data["path"]  # auto-defaulted
    assert data["crown_jewel"] is True


def test_fabric_block_gated_by_fabric_when():
    cfg = _flat_config()
    cfg["twin_config"]["params"]["FABRIC"] = False
    cfg["twin_config"]["fabric"] = {"regions": [{"id": "r1", "igp": "ospf"}]}
    cfg["twin_config"]["fabric_when"] = "FABRIC"
    ir, _ = expand_twin_config_raw(cfg)
    assert "fabric" not in ir
    ir2, _ = expand_twin_config_raw(cfg, param_overrides={"FABRIC": "true"})
    assert ir2["fabric"]["regions"][0]["id"] == "r1"


# ---------------------------------------------------------------------------
# Adversarial param-edge coverage (tester deepening, docs/WORK_ORDER_
# twin_configs.md §4): an undeclared --param name is accepted (params.py's
# "not a closed allow-list" contract), and a --param VALUE that itself looks
# like a `${...}` token must NOT be re-substituted -- one substitution pass
# only, never an injection/double-eval surface.
# ---------------------------------------------------------------------------
def test_undeclared_param_override_is_accepted_even_if_unreferenced():
    cfg = _flat_config()
    ir, params = expand_twin_config_raw(cfg, param_overrides={"TOTALLY_UNUSED": "xyz"})
    assert params["TOTALLY_UNUSED"] == "xyz"
    assert ir["scenario"]["id"] == "unit-flat"  # expansion proceeded normally


def test_param_override_value_containing_template_syntax_is_not_double_substituted():
    # SITE has no declared default (string-typed by nature) -- its inline
    # `${SITE:hq}` default is used only when SITE isn't overridden, so
    # _coerce_like's "like is a str/anything else -> keep as string" branch
    # applies to the override and no int-coercion gets in the way.
    cfg = _flat_config()
    cfg["twin_config"]["nodes"][0]["name"] = "${SITE:hq}-gw"
    # Override SITE with a literal string that itself LOOKS like a nested
    # token. If the substitution engine ran a second pass over the already-
    # resolved override value, "INJECTED" would get resolved too; it must
    # not -- the override value is used verbatim, exactly once.
    ir, params = expand_twin_config_raw(
        cfg, param_overrides={"SITE": "${INJECTED:should-not-resolve}"},
    )
    assert params["SITE"] == "${INJECTED:should-not-resolve}"
    node_ids = {n["id"] for n in ir["layers"]["infra"]["nodes"]}
    # The node NAME (the id stem since the role/persona split) became the
    # literal override text concatenated with "-gw" --
    # NOT further substituted (no attempt was ever made to resolve
    # "INJECTED", which has no declared default and would otherwise raise
    # TwinConfigParamError).
    assert "${INJECTED:should-not-resolve}-gw" in node_ids


def test_surviving_unresolved_token_fails_validation_not_silently(manifests):
    """N2 confirmation (docs/WORK_ORDER_twin_configs.md review): a `${...}`
    token whose NAME doesn't match the substitution engine's token charset
    (hyphens aren't a valid param-name character) is never recognized as a
    token at all, so `substitute()` leaves it as a literal string -- single-
    pass substitution is correct, injection-safe behavior, not a bug. Confirm
    the SAFETY NET still catches it: a literal, unresolved `${...}` string
    used as an image id fails the validator (REF-120), it does not silently
    pass through as if it were a real image."""
    from ruamel.yaml import YAML

    from axrange.validator.engine import validate_full

    cfg = _flat_config()
    cfg["twin_config"]["nodes"][0]["image"] = "${bad-name}"  # hyphen -> not a token
    ir, _params = expand_twin_config_raw(cfg)
    gw = next(n for n in ir["layers"]["infra"]["nodes"] if n["id"] == "gw")
    assert gw["image"] == "${bad-name}"  # survived, literally, into the IR

    import io

    buf = io.StringIO()
    YAML(typ="safe").dump(ir, buf)
    result = validate_full(text=buf.getvalue(), manifests=manifests)
    assert any(f.rule_id == "REF-120" for f in result.errors), (
        "a literal unresolved '${bad-name}' image id must fail REF-120, not "
        f"pass silently: {result.errors}"
    )


# ---------------------------------------------------------------------------
# M1/S3 (code review): count coercion must raise a typed TwinConfigShapeError,
# never a bare ValueError/TypeError, and count < 1 must be a loud error, not
# a silent floor-to-1.
# ---------------------------------------------------------------------------
def test_count_non_numeric_raises_typed_shape_error():
    cfg = _flat_config()
    cfg["twin_config"]["nodes"][1]["count"] = "notanumber"
    with pytest.raises(TwinConfigShapeError):
        expand_twin_config_raw(cfg)


def test_count_from_undeclared_param_non_numeric_raises_typed_shape_error():
    # An UNDECLARED count param overridden with a non-numeric string --
    # declared int params are guarded earlier by params._coerce_like, an
    # undeclared one isn't, so this exercises the SAME "int(spec.count)"
    # coercion boundary from a different angle (review M1).
    cfg = _flat_config()
    cfg["twin_config"]["nodes"][1]["count"] = "${UNDECLARED_COUNT}"
    with pytest.raises(TwinConfigShapeError):
        expand_twin_config_raw(cfg, param_overrides={"UNDECLARED_COUNT": "notanumber"})


@pytest.mark.parametrize("bad_count", [0, -1, -5])
def test_count_below_one_raises_instead_of_silently_flooring(bad_count):
    cfg = _flat_config()
    cfg["twin_config"]["nodes"][1]["count"] = bad_count
    with pytest.raises(TwinConfigShapeError):
        expand_twin_config_raw(cfg)


# ---------------------------------------------------------------------------
# The four persona-warning tests that lived here are GONE with the
# `nodes[].persona` field they exercised (2026-08-04,
# `docs/WORK_ORDER_role_persona_tier.md`). The `warnings` out-parameter they
# drove is KEPT -- it is real plumbing with real consumers (`cli.main`
# prints it; the API returns it as `CreateDeployResponse.config_warnings`) --
# it simply has no producer today. `test_warnings_out_param_still_accepted`
# below pins that the channel survives, so re-adding a producer later needs
# no signature change.
# ---------------------------------------------------------------------------
def test_warnings_out_param_still_accepted_and_empty():
    cfg = _flat_config()
    warnings: list[str] = []
    ir, params = expand_twin_config_raw(cfg, warnings=warnings)
    assert warnings == []
    assert ir["scenario"]["id"] == "unit-flat"


def test_expand_without_warnings_out_param_is_a_plain_2_tuple():
    ir, params = expand_twin_config_raw(_flat_config())
    assert ir["scenario"]["id"] == "unit-flat"


# ---------------------------------------------------------------------------
# W1 -- name/role split + closed vocabulary
# (`docs/WORK_ORDER_role_persona_tier.md` §4.5/§4.6, Authority: user).
# ---------------------------------------------------------------------------
def test_removed_node_persona_is_rejected_with_the_profile_remedy():
    cfg = _flat_config()
    cfg["twin_config"]["nodes"][1]["persona"] = "corp-workstation"
    with pytest.raises(TwinConfigShapeError, match="tfc-\\*.yaml"):
        expand_twin_config_raw(cfg)


def test_unknown_key_is_rejected_by_the_closed_vocabulary():
    """`extra="forbid"` (2026-08-04, Authority: user) -- a typo'd key used to
    be silently absorbed, which is how five inert fields survived unnoticed."""
    cfg = _flat_config()
    cfg["twin_config"]["nodes"][1]["imagge"] = "typo"
    with pytest.raises(TwinConfigShapeError):
        expand_twin_config_raw(cfg)


def test_unsubstituted_load_still_parses_under_forbid():
    """`registry.load_twin_config` (behind `twin-config show`) loads a config
    BEFORE param substitution, so `count: "${N:25}"` is still a raw string at
    model-validate time. `forbid` must not break that path."""
    from axrange.twinconfig.registry import load_twin_config

    tc = load_twin_config("corp-enterprise")
    assert tc.id == "corp-enterprise"


def test_node_name_is_the_id_stem_and_role_is_independent():
    cfg = _flat_config()
    cfg["twin_config"]["nodes"][1]["name"] = "ws"
    cfg["twin_config"]["nodes"][1]["role"] = "corp-workstation"
    ir, _ = expand_twin_config_raw(cfg)
    nodes = {n["id"]: n for n in ir["layers"]["infra"]["nodes"]}
    assert {"ws01", "ws02"} <= set(nodes)
    assert nodes["ws01"]["role"] == "corp-workstation"


def test_node_without_role_omits_the_key_entirely():
    cfg = _flat_config()
    cfg["twin_config"]["nodes"][1].pop("role", None)
    ir, _ = expand_twin_config_raw(cfg)
    ws = [n for n in ir["layers"]["infra"]["nodes"] if n["id"].startswith("ws")]
    assert ws and all("role" not in n for n in ws)


def test_default_agents_absent_unless_the_config_states_it():
    """The IR treats an ABSENT `scenario.default_agents` as ON (user decision,
    2026-07-11). Emitting `None` instead of omitting the key would change every
    existing config's materialized IR — and `ir_hash`/`plan_id` key VMIDs, VM
    names and standing-deploy lookup (LES-126), so that is a live-cluster
    hazard, not a cosmetic diff."""
    ir, _ = expand_twin_config_raw(_flat_config())
    assert "default_agents" not in ir["scenario"]


def test_default_agents_passes_through_when_set():
    """A twin config previously had NO way to reach `scenario.default_agents`:
    `expand` emitted a fixed scenario block and `TCBase` forbids extras, so a
    config could not opt out of the default-agent sweep at all."""
    for value in (False, True):
        cfg = _flat_config()
        cfg["twin_config"]["default_agents"] = value
        ir, _ = expand_twin_config_raw(cfg)
        assert ir["scenario"]["default_agents"] is value
