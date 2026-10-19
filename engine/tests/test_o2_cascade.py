"""Tests for `axrange.o2control.cascade` — the ONE owner of the order a twin
(`te`) destroy tears its sibling-tier deploys down in.

The ordering is the whole point of the module: every sibling tier (`exr`,
`tfc`, `tap`) binds into the twin's LIVE VMs, so all three must run BEFORE
the twin's own sweep, in reverse-of-create order (LES-171). Because the
sibling teardowns' in-guest steps are best-effort (they warn rather than
fail), a wrong order does NOT fail a destroy — it reports success under a
wall of warnings — so it can only be caught by a test that asserts the
sequence directly.

No live cluster: the three cascade helpers are monkeypatched on their owning
modules (which is also a regression check that this module resolves them
LAZILY — a module-level `from … import` would bind past the patch).
"""
from __future__ import annotations

import pytest

import axrange.o2control.exercise as exr_mod
import axrange.o2control.taps as tap_mod
import axrange.o2control.traffic as tfc_mod
from axrange.o2control.cascade import (
    CASCADE_LABELS,
    CASCADE_ORDER,
    cascade_destroy_siblings_for_te,
)


class _Result:
    """Stand-in for a `TeardownResult` — only `removed`/`errors` are read by
    the callers that render these."""

    def __init__(self, removed=0, errors=()):
        self.removed = removed
        self.errors = list(errors)


def _patch(monkeypatch, *, exr=None, tfc=None, tap=None):
    """Patch the three cascade helpers on their OWNING modules (the same seam
    the sibling-isolation tests in test_exercise.py/test_api.py use)."""
    monkeypatch.setattr(
        exr_mod, "cascade_destroy_exercises_for_te", exr or (lambda **_k: []),
    )
    monkeypatch.setattr(
        tfc_mod, "cascade_destroy_tfc_for_te", tfc or (lambda **_k: None),
    )
    monkeypatch.setattr(
        tap_mod, "cascade_destroy_for_te", tap or (lambda **_k: None),
    )


def test_cascade_order_is_exr_then_tfc_then_tap(monkeypatch):
    """THE regression test for the inverted-order bug: the siblings run in
    reverse-of-create order, and the returned entries report that order."""
    seen: list[str] = []

    def _rec(tier, ret):
        def _fn(**_kwargs):
            seen.append(tier)
            return ret
        return _fn

    _patch(
        monkeypatch,
        exr=_rec("exr", [_Result()]),
        tfc=_rec("tfc", _Result()),
        tap=_rec("tap", _Result()),
    )

    out = cascade_destroy_siblings_for_te(db_path="/nonexistent.db", te_deploy_id="te-x")

    assert seen == ["exr", "tfc", "tap"]
    assert seen == list(CASCADE_ORDER)
    assert [c.tier for c in out] == list(CASCADE_ORDER)
    assert [c.label for c in out] == [CASCADE_LABELS[t] for t in CASCADE_ORDER]
    assert all(c.error is None for c in out)


def test_every_tier_in_cascade_order_has_a_label():
    """A tier added to the order without a label would `KeyError` mid-sweep,
    after some siblings are already torn down."""
    assert set(CASCADE_ORDER) == set(CASCADE_LABELS)


def test_unexpected_failure_in_one_sibling_does_not_skip_the_others(monkeypatch):
    """LES-117, re-asserted at the new shared seam: an UNEXPECTED exception in
    one sibling is captured on that entry and every LATER sibling still runs
    (before, the caller's own `try/except` per block did this; the ordering
    refactor must not lose it)."""
    seen: list[str] = []

    def _boom(**_kwargs):
        seen.append("exr")
        raise RuntimeError("simulated DB hiccup in the exercise cascade")

    def _rec(tier, ret):
        def _fn(**_kwargs):
            seen.append(tier)
            return ret
        return _fn

    _patch(monkeypatch, exr=_boom, tfc=_rec("tfc", _Result()), tap=_rec("tap", _Result()))

    out = cascade_destroy_siblings_for_te(db_path="/nonexistent.db", te_deploy_id="te-x")

    assert seen == ["exr", "tfc", "tap"]  # the crash did NOT skip the rest
    by_tier = {c.tier: c for c in out}
    assert "simulated DB hiccup" in by_tier["exr"].error
    assert by_tier["exr"].results == []
    assert by_tier["tfc"].error is None and by_tier["tap"].error is None


@pytest.mark.parametrize(
    "tier,ret,expected_len",
    [
        # `exercise` returns a LIST (a twin can have several ephemeral runs);
        # `traffic`/`tap` return a single result or None ("never bound" — not
        # an error worth surfacing on a twin destroy).
        ("exr", [], 0),
        ("exr", [_Result(), _Result()], 2),
        ("tfc", None, 0),
        ("tfc", _Result(), 1),
        ("tap", None, 0),
        ("tap", _Result(), 1),
    ],
)
def test_return_shapes_are_normalized_to_a_list(monkeypatch, tier, ret, expected_len):
    _patch(monkeypatch, **{tier: lambda **_k: ret})
    out = cascade_destroy_siblings_for_te(db_path="/nonexistent.db", te_deploy_id="te-x")
    entry = next(c for c in out if c.tier == tier)
    assert entry.error is None
    assert len(entry.results) == expected_len


def test_seams_and_scope_are_threaded_to_each_sibling(monkeypatch):
    """Each tier's test seam (`adapter`/`persona_backend`/`ga_factory`) and the
    shared scope args must actually reach its helper — a dropped kwarg would
    silently fall back to a real Proxmox client on a live destroy."""
    got: dict[str, dict] = {}

    def _rec(tier):
        def _fn(**kwargs):
            got[tier] = kwargs
            return None
        return _fn

    _patch(monkeypatch, exr=_rec("exr"), tfc=_rec("tfc"), tap=_rec("tap"))

    cascade_destroy_siblings_for_te(
        db_path="/db", te_deploy_id="te-x", adapter_config="/a.conf", node="pxtest1",
        teardown_backend="BACKEND", adapter="ADAPTER", persona_backend="PERSONA",
        ga_factory="GA",
    )

    for tier in CASCADE_ORDER:
        assert got[tier]["db_path"] == "/db"
        assert got[tier]["te_deploy_id"] == "te-x"
        assert got[tier]["adapter_config"] == "/a.conf"
        assert got[tier]["node"] == "pxtest1"
        assert got[tier]["teardown_backend"] == "BACKEND"
    assert got["tap"]["adapter"] == "ADAPTER"
    assert got["tfc"]["persona_backend"] == "PERSONA"
    assert got["exr"]["ga_factory"] == "GA"


# ---------------------------------------------------------------------------
# `siblings_for_te` — the read-only counterpart (code review S3, `docs/
# WORK_ORDER_twin_incremental_update.md`): finds standing sibling deploys
# without tearing anything down. A real `StateStore`, not mocked -- this is
# a pure lookup over the naming convention, exactly like `find_deploy_for_
# plan`/`deploys_matching` themselves are tested against a real DB.
# ---------------------------------------------------------------------------
from axrange.o2control.cascade import siblings_for_te  # noqa: E402
from axrange.planner import StateStore  # noqa: E402


def test_siblings_for_te_finds_one_of_each_tier(tmp_path):
    db = str(tmp_path / "s.db")
    store = StateStore(db)
    try:
        te_id = "te-x"
        store.create_deploy(
            deploy_id=te_id, scenario_id="sc", scenario_version=1,
            scenario_schema_version=1, plan_id="plan-te-x", ir_hash="h",
            placement={}, tier="te",
        )
        store.create_deploy(
            deploy_id=f"tap-{te_id}", scenario_id=f"tap-{te_id}", scenario_version=None,
            scenario_schema_version=None, plan_id=f"tap-{te_id}", ir_hash="", placement={},
            tier="te",
        )
        store.create_deploy(
            deploy_id=f"tfc-{te_id}", scenario_id=f"tfc-{te_id}", scenario_version=None,
            scenario_schema_version=None, plan_id=f"tfc-{te_id}", ir_hash="", placement={},
            tier="tfc",
        )
        exr_id = f"exr-{te_id}-abc123"
        store.create_deploy(
            deploy_id=exr_id, scenario_id=exr_id, scenario_version=None,
            scenario_schema_version=None, plan_id=exr_id, ir_hash="", placement={},
            tier="exr",
        )

        out = siblings_for_te(store, te_id)

        assert set(out) == set(CASCADE_ORDER)
        assert [d.deploy_id for d in out["tap"]] == [f"tap-{te_id}"]
        assert [d.deploy_id for d in out["tfc"]] == [f"tfc-{te_id}"]
        assert [d.deploy_id for d in out["exr"]] == [exr_id]
    finally:
        store.close()


def test_siblings_for_te_returns_empty_lists_when_nothing_bound(tmp_path):
    db = str(tmp_path / "s.db")
    store = StateStore(db)
    try:
        store.create_deploy(
            deploy_id="te-lonely", scenario_id="sc", scenario_version=1,
            scenario_schema_version=1, plan_id="plan-lonely", ir_hash="h",
            placement={}, tier="te",
        )
        out = siblings_for_te(store, "te-lonely")
        assert out == {"exr": [], "tfc": [], "tap": []}
    finally:
        store.close()


def test_siblings_for_te_excludes_destroyed_siblings(tmp_path):
    db = str(tmp_path / "s.db")
    store = StateStore(db)
    try:
        te_id = "te-y"
        store.create_deploy(
            deploy_id=te_id, scenario_id="sc", scenario_version=1,
            scenario_schema_version=1, plan_id="plan-te-y", ir_hash="h",
            placement={}, tier="te",
        )
        store.create_deploy(
            deploy_id=f"tap-{te_id}", scenario_id=f"tap-{te_id}", scenario_version=None,
            scenario_schema_version=None, plan_id=f"tap-{te_id}", ir_hash="", placement={},
            tier="te",
        )
        store.mark_destroyed(f"tap-{te_id}")

        out = siblings_for_te(store, te_id)

        assert out["tap"] == []
    finally:
        store.close()


def test_siblings_for_te_multiple_exercise_runs_all_returned(tmp_path):
    """`exr` is the ONE tier that can have several concurrent standing
    deploys bound to the same twin -- `siblings_for_te` must return all of
    them, not just the newest (mirrors `cascade_destroy_exercises_for_te`'s
    own list-returning shape)."""
    db = str(tmp_path / "s.db")
    store = StateStore(db)
    try:
        te_id = "te-z"
        store.create_deploy(
            deploy_id=te_id, scenario_id="sc", scenario_version=1,
            scenario_schema_version=1, plan_id="plan-te-z", ir_hash="h",
            placement={}, tier="te",
        )
        for token in ("run1", "run2"):
            run_id = f"exr-{te_id}-{token}"
            store.create_deploy(
                deploy_id=run_id, scenario_id=run_id, scenario_version=None,
                scenario_schema_version=None, plan_id=run_id, ir_hash="", placement={},
                tier="exr",
            )
        out = siblings_for_te(store, te_id)
        assert {d.deploy_id for d in out["exr"]} == {
            f"exr-{te_id}-run1", f"exr-{te_id}-run2",
        }
    finally:
        store.close()
