"""Catalog integrity tests: counts match what was implemented, every
rule referenced by a check has a definition, every rule has a check."""
from __future__ import annotations

from axrange.validator import RULES
from axrange.validator.catalog import list_rules
from axrange.validator.engine import PASS_1_CHECKS, PASS_2_CHECKS, PASS_3_CHECKS
from axrange.validator.finding import RuleCategory


def test_catalog_total_count() -> None:
    # 83 IR-side + 7 CLU = 90 (REF-200 per-tap sensor routing 2026-06-03; STR-190
    # tap: shape + NET-110 tapped-bridge-capturable, dynamic-tap slice 2026-06-19;
    # REF-210 typed-sensor rule, sensors design 2026-06-23) + 8 FAB (fabric slice,
    # 2026-06-29) + 2 ACT (schedule executor -- ACT-060 interval shape, ACT-070
    # seed-recommended, 2026-07-10) = 100 + 2 REF (REF-220/REF-230, overlays.adversary
    # -- A3 Stage A, 2026-07-11) = 102 + 1 REF (REF-240, adversary target kind:host
    # check, code-review fix, 2026-07-11) = 103 + 1 REF (REF-250, sensor-agnostic-te
    # code-review fix F1, WARNING that sensors: isn't stood up by a te deploy,
    # 2026-07-13) = 104 + 8 PSN (PSN-010..080, the persona-catalog rule set,
    # docs/WORK_ORDER_persona_library.md, 2026-07-24 -- registered at pass_=4,
    # like CLU: catalog-side, run by axrange.validator.checks.personas, never
    # wired into engine.py's PASS_1/2/3_CHECKS) = 112 + 1 PSN-090
    # (peer_role-in-requires[] satisfiability, code-review fix splitting
    # PSN-050's presence-only check from the dangling/satisfiability concern,
    # 2026-07-24) = 113 + 1 REF (REF-260, the Exercise-tier scope-shift --
    # WARNING that a te deploy no longer runs layers.activity:/
    # overlays.adversary:, docs/WORK_ORDER_exercise_tier.md, 2026-07-24) = 114
    # - STR-190 - NET-110 - REF-200 + STR-200 (the twin substrate becomes
    # tap-agnostic, docs/WORK_ORDER_tap_eligibility.md, Authority: user,
    # 2026-07-28 -- eligibility moves entirely into the tap plan) = 112
    # + 2 PSN (PSN-100 transport enum, PSN-110 peer_scope enum -- the
    # high-volume persona wave's two new emit fields, 2026-07-28) = 114.
    # The catalog comment in catalog.py records the divergence from the spec's
    # headline number.
    assert len(RULES) == 114


def test_no_duplicate_rule_ids() -> None:
    ids = [r.id for r in RULES.values()]
    assert len(ids) == len(set(ids)), "Duplicate rule IDs in catalog"


def test_every_rule_has_a_pass() -> None:
    for r in RULES.values():
        assert r.pass_ in (1, 2, 3, 4), f"{r.id}: invalid pass {r.pass_}"


def test_pass_assignments_match_categories() -> None:
    """STR/INC = Pass 1, REF = Pass 2, semantic = Pass 3, CLU/PERSONA = Pass 4
    (PERSONA is catalog-side like CLU — run by
    axrange.validator.checks.personas, not engine.py's PASS_1/2/3_CHECKS,
    docs/WORK_ORDER_persona_library.md)."""
    p1_cats = {RuleCategory.STRUCTURAL, RuleCategory.INCLUDE_CONSISTENCY}
    p2_cats = {RuleCategory.REFERENCE_INTEGRITY}
    p3_cats = {
        RuleCategory.CAPABILITY,
        RuleCategory.LIFECYCLE,
        RuleCategory.NETWORK_SANITY,
        RuleCategory.OVERLAY,
        RuleCategory.ACTIVITY,
        RuleCategory.DEFENSE,
        RuleCategory.FABRIC,
    }
    p4_cats = {RuleCategory.CLUSTER, RuleCategory.PERSONA}
    for r in RULES.values():
        if r.category in p1_cats:
            assert r.pass_ == 1, f"{r.id} should be pass 1"
        elif r.category in p2_cats:
            assert r.pass_ == 2, f"{r.id} should be pass 2"
        elif r.category in p3_cats:
            assert r.pass_ == 3, f"{r.id} should be pass 3"
        elif r.category in p4_cats:
            assert r.pass_ == 4, f"{r.id} should be pass 4"


def test_pass_1_check_count_matches_catalog() -> None:
    expected = len(list_rules(pass_=1))
    assert len(PASS_1_CHECKS) == expected, (
        f"Pass 1 has {len(PASS_1_CHECKS)} checks, expected {expected} rules"
    )


def test_pass_2_check_count_matches_catalog() -> None:
    expected = len(list_rules(pass_=2))
    assert len(PASS_2_CHECKS) == expected


def test_pass_3_check_count_matches_catalog() -> None:
    expected = len(list_rules(pass_=3))
    assert len(PASS_3_CHECKS) == expected


def test_rptoe_rules_have_warnable_severity() -> None:
    """Only `info` or `warning` severities can be promoted (errors are ceiling)."""
    for r in RULES.values():
        if r.reproducible_promotes_to_error:
            assert r.severity.value in ("info", "warning"), (
                f"{r.id} rptoe but severity is {r.severity}"
            )
