"""Tests for `axrange.tapscopes` -- the shared tap-scope vocabulary leaf
module (fix round S5, `docs/WORK_ORDER_tap_scopes.md`).

Originally extracted so `axrange.planner.tappoints` (the engine) and
`axrange.twinconfig.expand` (a content-authoring layer that must never
import the engine, LES-025) could read the SAME scope set, direction rule,
and retired-scope wording -- an earlier revision duplicated all three
independently in both modules and they had already drifted. `expand.py`'s
own consumption is RETIRED as of `docs/WORK_ORDER_tap_eligibility.md` D3
(Authority: user, 2026-07-28) -- its `tap_points[]`/`_apply_tap_points`
materialization (the only place it read this vocabulary) is gone, so it no
longer imports this module at all; see `tapscopes.py`'s own docstring for
the full reasoning on why the module is kept as a leaf regardless. These
tests now assert `axrange.planner.tappoints`'s re-export SHARING (same
object identity / same values), not just "produces an equal string today."
"""
from __future__ import annotations

import pytest

from axrange import tapscopes


def test_tap_scopes_are_exactly_the_four_current_scopes():
    assert tapscopes.TAP_SCOPES == ("port", "segment", "perimeter", "boundary")


@pytest.mark.parametrize(
    "scope,expected",
    [("port", "both"), ("perimeter", "both"), ("segment", "rx"), ("boundary", "rx")],
)
def test_direction_for_scope(scope, expected):
    assert tapscopes.direction_for_scope(scope) == expected


def test_direction_for_scope_rejects_unknown_scope():
    with pytest.raises(ValueError, match="unknown tap-point scope"):
        tapscopes.direction_for_scope("host")


def test_scope_error_detail_retired_scopes():
    assert "renamed to 'port'" in tapscopes.scope_error_detail("leg")
    assert "dropped" in tapscopes.scope_error_detail("host")


def test_scope_error_detail_generic_unknown_scope():
    detail = tapscopes.scope_error_detail("direction")
    assert "unknown scope" in detail
    assert "direction" in detail


def test_planner_tappoints_reexports_the_same_objects():
    """`axrange.planner.tappoints` re-exports `tapscopes`'s vocabulary
    unchanged (so every pre-existing `from axrange.planner.tappoints import
    TAP_SCOPES` call site keeps working) -- verified by IDENTITY, not just
    equal values, to prove it's a re-export, not a second definition."""
    from axrange.planner import tappoints

    assert tappoints.TAP_SCOPES is tapscopes.TAP_SCOPES
    assert tappoints.direction_for_scope is tapscopes.direction_for_scope
    assert tappoints.scope_error_detail is tapscopes.scope_error_detail


# `test_twinconfig_expand_uses_the_same_shared_vocabulary` and
# `test_engine_and_content_layer_produce_identical_retired_scope_wording`
# REMOVED here (`docs/WORK_ORDER_tap_eligibility.md` D3, Authority: user,
# 2026-07-28) -- `axrange.twinconfig.expand` no longer imports `tapscopes`
# at all (its `tap_points[]` materialization, the only consumer, is
# retired), so there is nothing left to assert sharing against. The
# remaining test below still proves `axrange.o2control.taps` (via its
# `axrange.planner.tappoints` re-export) uses the SAME retired-scope
# wording `tapscopes.scope_error_detail` defines.
def test_taps_module_uses_the_shared_retired_scope_wording():
    from axrange.o2control.taps import scope_error_detail as taps_detail

    for scope in ("leg", "host", "direction"):
        assert taps_detail(scope) == tapscopes.scope_error_detail(scope)
