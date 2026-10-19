"""State-store unit tests (axrange.planner.state)."""
from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

from axrange.planner.state import (
    AttemptResult,
    DeployStatus,
    OpStatus,
    StateStore,
)


@pytest.fixture
def store():
    with tempfile.TemporaryDirectory() as tmp:
        s = StateStore(Path(tmp) / "state.db")
        yield s
        s.close()


def test_create_and_get_deploy(store):
    row = store.create_deploy(
        deploy_id="d1",
        scenario_id="slice-demo",
        scenario_version=1,
        scenario_schema_version=1,
        plan_id="slice-demo-abcdef12",
        ir_hash="deadbeef",
        placement={"h-a": "px1", "h-b": "px1"},
    )
    assert row.status == DeployStatus.RUNNING
    fetched = store.get_deploy("d1")
    assert fetched is not None
    assert fetched.scenario_id == "slice-demo"
    assert json.loads(fetched.placement_json) == {"h-a": "px1", "h-b": "px1"}


def test_complete_op_is_atomic_with_outputs(store):
    from axrange.planner.state import OpRow
    store.create_deploy(
        deploy_id="d1", scenario_id="s", scenario_version=1,
        scenario_schema_version=1, plan_id="p", ir_hash="h",
        placement={},
    )
    op = OpRow(
        op_id="d1:clone-vm[h]",
        deploy_id="d1",
        op_name="clone-vm",
        op_catalog_id="OP-HOST-010",
        kind="action",
        scope_json='{"host":"h"}',
        inputs_resolved_json='{}',
        inputs_from_outputs_json='{}',
        outputs_json=None,
        cleanup_strategy="redo",
        idempotency_class="infra",
        status=OpStatus.PENDING,
        started_at=None,
        completed_at=None,
    )
    store.insert_ops("d1", [op])
    store.set_op_running("d1", "d1:clone-vm[h]")
    store.complete_op("d1", "d1:clone-vm[h]", outputs={"vmid": 12345})

    row = store.get_op("d1", "d1:clone-vm[h]")
    assert row is not None
    assert row.status == OpStatus.COMPLETED
    assert row.completed_at is not None
    assert json.loads(row.outputs_json) == {"vmid": 12345}


def test_attempts_record_in_order(store):
    from axrange.planner.state import OpRow
    store.create_deploy(
        deploy_id="d1", scenario_id="s", scenario_version=1,
        scenario_schema_version=1, plan_id="p", ir_hash="h", placement={},
    )
    op = OpRow(
        op_id="d1:start-vm[h]", deploy_id="d1", op_name="start-vm",
        op_catalog_id="OP-HOST-060", kind="action",
        scope_json='{"host":"h"}', inputs_resolved_json='{}',
        inputs_from_outputs_json='{}', outputs_json=None,
        cleanup_strategy="redo", idempotency_class="infra",
        status=OpStatus.PENDING, started_at=None, completed_at=None,
    )
    store.insert_ops("d1", [op])

    a1 = store.start_attempt("d1", "d1:start-vm[h]")
    store.end_attempt(a1, AttemptResult.ERROR,
                      error_message="boom", error_class="test")
    a2 = store.start_attempt("d1", "d1:start-vm[h]")
    store.end_attempt(a2, AttemptResult.SUCCESS)

    rows = store.attempts_for_op("d1", "d1:start-vm[h]")
    assert [r.attempt_id for r in rows] == [a1, a2]
    assert rows[0].result == AttemptResult.ERROR
    assert rows[0].error_class == "test"
    assert rows[1].result == AttemptResult.SUCCESS


def test_update_deploy_status_sets_completed_at(store):
    store.create_deploy(
        deploy_id="d1", scenario_id="s", scenario_version=1,
        scenario_schema_version=1, plan_id="p", ir_hash="h", placement={},
    )
    store.update_deploy_status("d1", DeployStatus.COMPLETED)
    row = store.get_deploy("d1")
    assert row.status == DeployStatus.COMPLETED
    assert row.completed_at is not None


def test_find_deploy_for_plan_returns_most_recent(store):
    """`find_deploy_for_plan` is the resume substrate: it surfaces the most
    recent deploy for a given plan_id so the CLI can pick it up."""
    import time as _t
    store.create_deploy(
        deploy_id="d1", scenario_id="s", scenario_version=1,
        scenario_schema_version=1, plan_id="plan-x", ir_hash="h", placement={},
    )
    _t.sleep(0.01)
    store.create_deploy(
        deploy_id="d2", scenario_id="s", scenario_version=1,
        scenario_schema_version=1, plan_id="plan-x", ir_hash="h", placement={},
    )
    found = store.find_deploy_for_plan("plan-x")
    assert found is not None
    assert found.deploy_id == "d2"  # most recent
    # Different plan_id returns None.
    assert store.find_deploy_for_plan("plan-y") is None


def test_latest_deploys_orders_by_updated_at_not_started_at(store, monkeypatch):
    """Code review finding (docs/WORK_ORDER_cli_lifecycle.md, Part A): a
    resumed/revived deploy keeps its ORIGINAL `started_at` (only
    `updated_at` moves) -- ordering by `started_at` alone ranks it BEHIND
    an unrelated deploy that merely started more recently, so a bare
    "latest deploy" default silently acts on the wrong row. Live repro
    (dev45): bare `te destroy` printed DESTROYED against a stale deploy
    while the just-resumed one stayed live."""
    counter = iter(range(1, 10_000))
    monkeypatch.setattr(
        "axrange.planner.state.time.time", lambda: 1_700_000_000.0 + next(counter)
    )

    # An OLDER-started deploy...
    store.create_deploy(
        deploy_id="a-old", scenario_id="a", scenario_version=1,
        scenario_schema_version=1, plan_id="plan-a", ir_hash="h", placement={},
    )
    # ...then a genuinely NEWER-started, UNRELATED deploy.
    store.create_deploy(
        deploy_id="b-unrelated", scenario_id="b", scenario_version=1,
        scenario_schema_version=1, plan_id="plan-b", ir_hash="h", placement={},
    )
    # `a-old` gets RESUMED (its `updated_at` moves; `started_at` stays put)
    # -- e.g. `axrange te deploy` picking it back up.
    store.update_deploy_status("a-old", DeployStatus.RUNNING)

    latest = store.latest_deploys(limit=1)
    assert latest[0].deploy_id == "a-old"

    all_rows = store.latest_deploys(limit=20)
    assert [r.deploy_id for r in all_rows] == ["a-old", "b-unrelated"]


def test_latest_deploy_for_tier_orders_by_updated_at(store, monkeypatch):
    """Same ordering fix, tier-filtered query (`o2 status`/`info`/`destroy`'s
    bare-arg default is built on this)."""
    counter = iter(range(1, 10_000))
    monkeypatch.setattr(
        "axrange.planner.state.time.time", lambda: 1_700_000_000.0 + next(counter)
    )

    store.create_deploy(
        deploy_id="o2-old", scenario_id="o2-surface", scenario_version=1,
        scenario_schema_version=1, plan_id="plan-o2-old", ir_hash="h",
        placement={}, tier="o2",
    )
    store.create_deploy(
        deploy_id="o2-unrelated-newer", scenario_id="o2-surface-2", scenario_version=1,
        scenario_schema_version=1, plan_id="plan-o2-new", ir_hash="h",
        placement={}, tier="o2",
    )
    store.update_deploy_status("o2-old", DeployStatus.RUNNING)

    found = store.latest_deploy_for_tier("o2")
    assert found is not None
    assert found.deploy_id == "o2-old"


def test_deploys_matching_default_orders_by_updated_at(store, monkeypatch):
    """Same ordering fix, the general-purpose filtered listing (`te`'s
    bare-arg default + `list-deploys --tier` are built on this)."""
    counter = iter(range(1, 10_000))
    monkeypatch.setattr(
        "axrange.planner.state.time.time", lambda: 1_700_000_000.0 + next(counter)
    )

    store.create_deploy(
        deploy_id="te-old", scenario_id="te-a", scenario_version=1,
        scenario_schema_version=1, plan_id="plan-te-a", ir_hash="h",
        placement={}, tier="te",
    )
    store.create_deploy(
        deploy_id="te-unrelated-newer", scenario_id="te-b", scenario_version=1,
        scenario_schema_version=1, plan_id="plan-te-b", ir_hash="h",
        placement={}, tier="te",
    )
    store.update_deploy_status("te-old", DeployStatus.RUNNING)

    rows = store.deploys_matching(tier="te", limit=1)
    assert len(rows) == 1
    assert rows[0].deploy_id == "te-old"


def test_deploys_matching_status_filters_to_exactly_one_value(store):
    """`docs/WORK_ORDER_console_selector_and_stale_deploys.md`'s explicit
    `?status=` reach-into-history filter on `GET /api/deploys` is built on
    this: `status=` narrows to exactly that one status, independent of
    `exclude_destroyed` (which stays the separate "give me the live ones"
    shorthand this method already had)."""
    store.create_deploy(
        deploy_id="d-completed", scenario_id="s", scenario_version=1,
        scenario_schema_version=1, plan_id="plan-a", ir_hash="h", placement={},
    )
    store.update_deploy_status("d-completed", DeployStatus.COMPLETED)
    store.create_deploy(
        deploy_id="d-failed", scenario_id="s", scenario_version=1,
        scenario_schema_version=1, plan_id="plan-b", ir_hash="h", placement={},
    )
    store.update_deploy_status("d-failed", DeployStatus.FAILED)

    rows = store.deploys_matching(status="failed")
    assert [r.deploy_id for r in rows] == ["d-failed"]


def test_deploys_matching_status_filter_happens_before_limit(store, monkeypatch):
    """The bug this guards against: a Python-side post-filter (fetch the N
    most recent rows, THEN drop non-matching statuses) silently
    under-returns once enough newer rows of a DIFFERENT status crowd the
    page. Three deploys are destroyed strictly AFTER the one live deploy
    (each teardown bumps `updated_at`, so they rank newest), so a
    `limit=1` post-filter would find an empty page even though a live
    deploy exists -- the fix filters IN SQL before `LIMIT` (`docs/
    WORK_ORDER_console_selector_and_stale_deploys.md`)."""
    counter = iter(range(1, 10_000))
    monkeypatch.setattr(
        "axrange.planner.state.time.time", lambda: 1_700_000_000.0 + next(counter)
    )

    store.create_deploy(
        deploy_id="d-live", scenario_id="s", scenario_version=1,
        scenario_schema_version=1, plan_id="plan-live", ir_hash="h", placement={},
    )
    store.update_deploy_status("d-live", DeployStatus.COMPLETED)
    for i in range(3):
        did = f"d-gone-{i}"
        store.create_deploy(
            deploy_id=did, scenario_id="s", scenario_version=1,
            scenario_schema_version=1, plan_id=f"plan-gone-{i}", ir_hash="h",
            placement={},
        )
        store.mark_destroyed(did)

    rows = store.deploys_matching(status="completed", limit=1)
    assert [r.deploy_id for r in rows] == ["d-live"]

    rows = store.deploys_matching(exclude_destroyed=True, limit=1)
    assert [r.deploy_id for r in rows] == ["d-live"]


def test_find_deploy_for_plan_orders_by_updated_at(store, monkeypatch):
    """Two deploys can share a `plan_id` (a `--fresh` re-deploy mints a NEW
    deploy_id but the deterministic plan_id, derived from IR content alone,
    stays the same). The resume substrate must pick whichever one was most
    recently ACTIVE, not whichever merely started later."""
    counter = iter(range(1, 10_000))
    monkeypatch.setattr(
        "axrange.planner.state.time.time", lambda: 1_700_000_000.0 + next(counter)
    )

    store.create_deploy(
        deploy_id="d-old", scenario_id="s", scenario_version=1,
        scenario_schema_version=1, plan_id="plan-shared", ir_hash="h", placement={},
    )
    store.create_deploy(
        deploy_id="d-newer-started", scenario_id="s", scenario_version=1,
        scenario_schema_version=1, plan_id="plan-shared", ir_hash="h", placement={},
    )
    store.update_deploy_status("d-old", DeployStatus.RUNNING)

    found = store.find_deploy_for_plan("plan-shared")
    assert found is not None
    assert found.deploy_id == "d-old"


def test_find_deploy_for_plan_exclude_destroyed_treats_top_match_as_no_match(store):
    """Part B (docs/WORK_ORDER_cli_lifecycle.md): a `destroyed` deploy is
    terminal -- `exclude_destroyed=True` (the deploy-creation resume
    decision) must treat it as no-match so the caller mints a fresh
    deploy_id, exactly as `--fresh` would. The default (`exclude_destroyed=
    False`, teardown-lookup callers like `destroy_taps`) must keep seeing
    it -- finding an already-destroyed deploy there is the expected
    idempotent-no-op path, not a revival."""
    store.create_deploy(
        deploy_id="d1", scenario_id="s", scenario_version=1,
        scenario_schema_version=1, plan_id="plan-x", ir_hash="h", placement={},
    )
    store.mark_destroyed("d1")

    assert store.find_deploy_for_plan("plan-x").deploy_id == "d1"
    assert store.find_deploy_for_plan("plan-x", exclude_destroyed=True) is None


def test_find_deploy_for_plan_exclude_destroyed_does_not_reach_past_to_older_row(
    store, monkeypatch,
):
    """A destroyed TOP match means "mint a fresh deploy_id", not "reach
    back further into history" -- even if an OLDER row for the same
    plan_id is still `completed`/non-destroyed, `exclude_destroyed=True`
    must not resurface it."""
    counter = iter(range(1, 10_000))
    monkeypatch.setattr(
        "axrange.planner.state.time.time", lambda: 1_700_000_000.0 + next(counter)
    )

    store.create_deploy(
        deploy_id="d-completed-old", scenario_id="s", scenario_version=1,
        scenario_schema_version=1, plan_id="plan-y", ir_hash="h", placement={},
    )
    store.update_deploy_status("d-completed-old", DeployStatus.COMPLETED)

    store.create_deploy(
        deploy_id="d-destroyed-new", scenario_id="s", scenario_version=1,
        scenario_schema_version=1, plan_id="plan-y", ir_hash="h", placement={},
    )
    store.mark_destroyed("d-destroyed-new")

    # Without exclude_destroyed: the most-recent row (destroyed) wins, as always.
    assert store.find_deploy_for_plan("plan-y").deploy_id == "d-destroyed-new"
    # With it: no-match, NOT a fall-back to the older completed row.
    assert store.find_deploy_for_plan("plan-y", exclude_destroyed=True) is None


def test_create_deploy_defaults_to_te_tier(store):
    row = store.create_deploy(
        deploy_id="d1", scenario_id="s", scenario_version=1,
        scenario_schema_version=1, plan_id="plan-x", ir_hash="h", placement={},
    )
    assert row.tier == "te"
    assert store.get_deploy("d1").tier == "te"


def test_latest_deploy_for_tier_finds_o2_behind_many_newer_te_rows(store, monkeypatch):
    """Code-review S1 (`axrange.o2control` A1 follow-up): the O2 surface is
    the OLDEST persistent deploy row by design (it survives every `te`
    destroy) — `latest_deploy_for_tier` must find it via a direct `tier`
    query, not a `started_at DESC LIMIT N` scan a pile of newer `te` rows
    could bury it behind.

    Ticks a fake clock so every row gets a strictly increasing `started_at`
    (real `time.time()` calls in a tight loop can collide at its float
    resolution, making the "most recent" assertions flaky)."""
    counter = iter(range(1, 10_000))
    monkeypatch.setattr(
        "axrange.planner.state.time.time", lambda: 1_700_000_000.0 + next(counter)
    )

    store.create_deploy(
        deploy_id="o2-surface-1", scenario_id="o2-surface", scenario_version=1,
        scenario_schema_version=1, plan_id="plan-o2", ir_hash="h", placement={},
        tier="o2",
    )
    # A pile of newer `te` deploys — more than any plausible client-side cap.
    for i in range(250):
        store.create_deploy(
            deploy_id=f"te-{i}", scenario_id="unrelated", scenario_version=1,
            scenario_schema_version=1, plan_id=f"plan-te-{i}", ir_hash="h",
            placement={}, tier="te",
        )

    found = store.latest_deploy_for_tier("o2")
    assert found is not None
    assert found.deploy_id == "o2-surface-1"

    # And the reverse query still returns the most recent `te` row.
    found_te = store.latest_deploy_for_tier("te")
    assert found_te is not None
    assert found_te.deploy_id == "te-249"

    # An unrepresented tier returns None.
    assert store.latest_deploy_for_tier("img") is None


def test_insert_ops_is_idempotent(store):
    """Re-inserting the same op rows is a no-op — required for resume.
    Existing rows keep their status (don't get reset to PENDING)."""
    from axrange.planner.state import OpRow
    store.create_deploy(
        deploy_id="d1", scenario_id="s", scenario_version=1,
        scenario_schema_version=1, plan_id="p", ir_hash="h", placement={},
    )
    op = OpRow(
        op_id="d1:start-vm[h]", deploy_id="d1", op_name="start-vm",
        op_catalog_id="OP-HOST-060", kind="action",
        scope_json='{"host":"h"}', inputs_resolved_json='{}',
        inputs_from_outputs_json='{}', outputs_json=None,
        cleanup_strategy="redo", idempotency_class="infra",
        status=OpStatus.PENDING, started_at=None, completed_at=None,
    )
    store.insert_ops("d1", [op])
    store.complete_op("d1", "d1:start-vm[h]", outputs={"started": True})

    # Re-insert the same op as PENDING; the existing COMPLETED row must win.
    store.insert_ops("d1", [op])
    row = store.get_op("d1", "d1:start-vm[h]")
    assert row.status == OpStatus.COMPLETED
    import json
    assert json.loads(row.outputs_json) == {"started": True}


def test_set_op_running_refreshes_persisted_inputs_on_reapply(store):
    """GAP-044/LES-157 — `insert_ops`'s `INSERT OR IGNORE` freezes
    `inputs_resolved_json`/`inputs_from_outputs_json` at the FIRST apply. A
    re-applied deploy (same deploy_id, an op forced back to PENDING by a
    caller like `force_reset_ops`) must have its persisted inputs catch up
    to the NEW plan the moment the op actually starts an attempt, via
    `set_op_running`'s optional inputs-refresh kwargs — not stay frozen at
    whatever the first-ever `insert_ops` wrote."""
    from axrange.planner.state import OpRow

    store.create_deploy(
        deploy_id="d1", scenario_id="s", scenario_version=1,
        scenario_schema_version=1, plan_id="p", ir_hash="h", placement={},
    )
    op = OpRow(
        op_id="d1:install-ovs-mirror[h]", deploy_id="d1",
        op_name="install-ovs-mirror", op_catalog_id="OP-MIR-020",
        kind="action", scope_json='{"host":"h"}',
        inputs_resolved_json=json.dumps({"sensor_id": "XPress-SW"}),
        inputs_from_outputs_json="{}", outputs_json=None,
        cleanup_strategy="redo", idempotency_class="infra",
        status=OpStatus.PENDING, started_at=None, completed_at=None,
    )
    store.insert_ops("d1", [op])

    # First apply: op starts + completes with the ORIGINAL inputs.
    store.set_op_running(
        "d1", op.op_id,
        inputs_resolved_json=json.dumps({"sensor_id": "XPress-SW"}),
        inputs_from_outputs_json="{}",
    )
    store.complete_op("d1", op.op_id, outputs={"ok": True})
    row = store.get_op("d1", op.op_id)
    assert json.loads(row.inputs_resolved_json) == {"sensor_id": "XPress-SW"}

    # Re-insert (a resume/re-apply's insert_ops call) is a no-op over the
    # completed row -- the stale-forever bug, absent the fix.
    store.insert_ops("d1", [op])
    row = store.get_op("d1", op.op_id)
    assert json.loads(row.inputs_resolved_json) == {"sensor_id": "XPress-SW"}

    # The plan CHANGED (a new sensor added) and the caller force-resets this
    # op back to pending so it re-runs (mirrors `taps.py`'s force_reset_ops
    # for `install-ovs-mirror`).
    store.force_reset_ops("d1", [op.op_id])
    assert store.get_op("d1", op.op_id).status == OpStatus.PENDING
    # Still stale until the op actually executes again.
    assert json.loads(store.get_op("d1", op.op_id).inputs_resolved_json) == {
        "sensor_id": "XPress-SW"
    }

    new_inputs = {"sensor_id": "XPress-SW", "direction": "rx"}
    store.set_op_running(
        "d1", op.op_id,
        inputs_resolved_json=json.dumps(new_inputs),
        inputs_from_outputs_json="{}",
    )
    row = store.get_op("d1", op.op_id)
    # Inputs refreshed the instant the op STARTS, even before it completes --
    # a failed attempt still honestly records what it was attempted with.
    assert json.loads(row.inputs_resolved_json) == new_inputs
    assert row.status == OpStatus.RUNNING

    store.complete_op("d1", op.op_id, outputs={"ok": True})
    row = store.get_op("d1", op.op_id)
    assert json.loads(row.inputs_resolved_json) == new_inputs


def test_set_op_running_without_inputs_kwargs_leaves_them_untouched(store):
    """The one-arg call shape (no inputs refresh requested) must not
    clobber existing inputs -- backward compatible with every pre-fix
    caller/test that calls `set_op_running(deploy_id, op_id)` alone."""
    from axrange.planner.state import OpRow

    store.create_deploy(
        deploy_id="d1", scenario_id="s", scenario_version=1,
        scenario_schema_version=1, plan_id="p", ir_hash="h", placement={},
    )
    op = OpRow(
        op_id="d1:clone-vm[h]", deploy_id="d1", op_name="clone-vm",
        op_catalog_id="OP-HOST-010", kind="action",
        scope_json='{"host":"h"}',
        inputs_resolved_json=json.dumps({"template": "610"}),
        inputs_from_outputs_json="{}", outputs_json=None,
        cleanup_strategy="redo", idempotency_class="infra",
        status=OpStatus.PENDING, started_at=None, completed_at=None,
    )
    store.insert_ops("d1", [op])
    store.set_op_running("d1", op.op_id)
    row = store.get_op("d1", op.op_id)
    assert json.loads(row.inputs_resolved_json) == {"template": "610"}
    assert row.status == OpStatus.RUNNING


def test_set_op_running_rejects_a_lone_inputs_kwarg(store):
    """S2, code review: `inputs_resolved_json`/`inputs_from_outputs_json`
    must be supplied together or not at all. Passing only one used to
    silently wipe the OTHER column to `"{}"` (an `or "{}"` fallback) --
    manufacturing a confident "no wiring" answer out of an omission
    (LES-096's shape). Both one-sided call shapes must raise, and neither
    may touch the row (the row's original `inputs_from_outputs_json` must
    survive the rejected call untouched)."""
    from axrange.planner.state import OpRow

    store.create_deploy(
        deploy_id="d1", scenario_id="s", scenario_version=1,
        scenario_schema_version=1, plan_id="p", ir_hash="h", placement={},
    )
    op = OpRow(
        op_id="d1:clone-vm[h]", deploy_id="d1", op_name="clone-vm",
        op_catalog_id="OP-HOST-010", kind="action",
        scope_json='{"host":"h"}',
        inputs_resolved_json=json.dumps({"template": "610"}),
        inputs_from_outputs_json=json.dumps({"ip": ["prior-op", "ip"]}),
        outputs_json=None,
        cleanup_strategy="redo", idempotency_class="infra",
        status=OpStatus.PENDING, started_at=None, completed_at=None,
    )
    store.insert_ops("d1", [op])

    with pytest.raises(ValueError):
        store.set_op_running(
            "d1", op.op_id, inputs_resolved_json=json.dumps({"template": "999"}),
        )
    with pytest.raises(ValueError):
        store.set_op_running(
            "d1", op.op_id,
            inputs_from_outputs_json=json.dumps({"ip": ["other-op", "ip"]}),
        )

    # Neither rejected call touched the row -- no partial/wiped write.
    row = store.get_op("d1", op.op_id)
    assert json.loads(row.inputs_resolved_json) == {"template": "610"}
    assert json.loads(row.inputs_from_outputs_json) == {"ip": ["prior-op", "ip"]}
    assert row.status == OpStatus.PENDING  # not even the status flip landed


def test_insert_ops_never_upserts_inputs_for_a_legitimately_skipped_op(store):
    """Guards against the WRONG fix for GAP-044: a blanket upsert of
    `inputs_resolved_json`/`inputs_from_outputs_json` in `insert_ops` itself
    (e.g. `INSERT ... ON CONFLICT DO UPDATE SET inputs_resolved_json =
    excluded....`) would look plausible -- `insert_ops` IS where the stale
    values get written -- but it is an equal and opposite lie to the bug it
    fixes: `insert_ops` is called with EVERY op in the CURRENT in-memory plan
    on every apply, including ops the executor legitimately SKIPS as
    already-COMPLETED (a resume). If the new plan's content for one of those
    (unexecuted) ops differs from what actually ran, a blanket upsert would
    silently claim the new content is live even though nothing re-executed
    it. Only `set_op_running` -- called exactly when an op ACTUALLY starts
    an attempt -- may refresh the persisted inputs; `insert_ops` must keep
    `INSERT OR IGNORE` semantics for inputs on an existing row, completed or
    not."""
    from axrange.planner.state import OpRow

    store.create_deploy(
        deploy_id="d1", scenario_id="s", scenario_version=1,
        scenario_schema_version=1, plan_id="p", ir_hash="h", placement={},
    )
    op_v1 = OpRow(
        op_id="d1:start-vm[h]", deploy_id="d1", op_name="start-vm",
        op_catalog_id="OP-HOST-060", kind="action",
        scope_json='{"host":"h"}',
        inputs_resolved_json=json.dumps({"memory": 2048}),
        inputs_from_outputs_json="{}", outputs_json=None,
        cleanup_strategy="redo", idempotency_class="infra",
        status=OpStatus.PENDING, started_at=None, completed_at=None,
    )
    store.insert_ops("d1", [op_v1])
    store.complete_op("d1", op_v1.op_id, outputs={"started": True})

    # A re-apply's fresh in-memory plan resolved DIFFERENT content for this
    # op (e.g. the IR's memory sizing changed) but the executor never force-
    # resets it -- it is still COMPLETED, a legitimate resume-skip, so it
    # never runs `set_op_running` again. `insert_ops` gets called anyway
    # (persist_plan writes every op in the plan, not just the ones about to
    # run).
    op_v2 = OpRow(
        op_id="d1:start-vm[h]", deploy_id="d1", op_name="start-vm",
        op_catalog_id="OP-HOST-060", kind="action",
        scope_json='{"host":"h"}',
        inputs_resolved_json=json.dumps({"memory": 4096}),
        inputs_from_outputs_json="{}", outputs_json=None,
        cleanup_strategy="redo", idempotency_class="infra",
        status=OpStatus.PENDING, started_at=None, completed_at=None,
    )
    store.insert_ops("d1", [op_v2])

    row = store.get_op("d1", op_v1.op_id)
    assert row.status == OpStatus.COMPLETED
    assert json.loads(row.inputs_resolved_json) == {"memory": 2048}, (
        "insert_ops must NEVER touch inputs_resolved_json for an existing "
        "row -- a skipped op's persisted inputs must stay exactly what it "
        "actually ran with, not whatever a later unexecuted plan resolved"
    )


def test_reset_incomplete_ops_preserves_completed(store):
    """reset_incomplete_ops touches FAILED / HALTED / RUNNING / PENDING rows
    only — COMPLETED rows stay completed (their outputs feed downstream)."""
    from axrange.planner.state import OpRow

    store.create_deploy(
        deploy_id="d1", scenario_id="s", scenario_version=1,
        scenario_schema_version=1, plan_id="p", ir_hash="h", placement={},
    )
    rows = [
        OpRow(op_id=f"d1:op-{name}", deploy_id="d1", op_name=name,
              op_catalog_id="X", kind="action",
              scope_json="{}", inputs_resolved_json="{}",
              inputs_from_outputs_json="{}", outputs_json=None,
              cleanup_strategy="redo", idempotency_class="infra",
              status=status, started_at=None, completed_at=None)
        for name, status in [
            ("ok",      OpStatus.COMPLETED),
            ("failed",  OpStatus.FAILED),
            ("halted",  OpStatus.HALTED),
            ("running", OpStatus.RUNNING),
            ("pending", OpStatus.PENDING),
        ]
    ]
    store.insert_ops("d1", rows)
    store.complete_op("d1", "d1:op-ok", outputs={"v": 1})  # preserve outputs_json

    n_reset = store.reset_incomplete_ops("d1")
    # All non-completed rows reset; COMPLETED row left alone.
    assert n_reset == 4
    assert store.get_op("d1", "d1:op-ok").status == OpStatus.COMPLETED
    for n in ("failed", "halted", "running", "pending"):
        assert store.get_op("d1", f"d1:op-{n}").status == OpStatus.PENDING


def test_reset_incomplete_ops_preserves_destroyed(store):
    """M2/S1 (`docs/WORK_ORDER_twin_incremental_update.md` code review): a
    `destroyed` row (an `--update`'s removal sweep, or an ordinary `axrange
    destroy`) must NOT be resurrected to `pending` -- resume must never
    silently bring back something that was deliberately torn down."""
    from axrange.planner.state import OpRow

    store.create_deploy(
        deploy_id="d1", scenario_id="s", scenario_version=1,
        scenario_schema_version=1, plan_id="p", ir_hash="h", placement={},
    )
    rows = [
        OpRow(op_id=f"d1:op-{name}", deploy_id="d1", op_name=name,
              op_catalog_id="X", kind="action",
              scope_json="{}", inputs_resolved_json="{}",
              inputs_from_outputs_json="{}", outputs_json=None,
              cleanup_strategy="redo", idempotency_class="infra",
              status=status, started_at=None, completed_at=None)
        for name, status in [
            ("ok",        OpStatus.COMPLETED),
            ("destroyed", OpStatus.DESTROYED),
            ("failed",    OpStatus.FAILED),
        ]
    ]
    store.insert_ops("d1", rows)

    n_reset = store.reset_incomplete_ops("d1")

    # Only the FAILED row was reset -- COMPLETED and DESTROYED both untouched.
    assert n_reset == 1
    assert store.get_op("d1", "d1:op-ok").status == OpStatus.COMPLETED
    assert store.get_op("d1", "d1:op-destroyed").status == OpStatus.DESTROYED
    assert store.get_op("d1", "d1:op-failed").status == OpStatus.PENDING


# ---------------------------------------------------------------------------
# Composite PK (deploy_id, op_id) — CRITICAL regression (code-review finding
# #1, 2026-07-07). `op_id` is deterministic from the plan alone, so two
# deploys of the SAME plan (e.g. a `--fresh` redeploy without clearing the
# state DB) produce IDENTICAL op_id strings. Before this fix `ops` was keyed
# by `op_id` ALONE, so the second deploy's `insert_ops` silently aliased
# onto the first deploy's rows (`INSERT OR IGNORE`) and owned zero rows of
# its own.
# ---------------------------------------------------------------------------


def test_two_deploys_of_same_plan_hold_independent_op_rows(store):
    from axrange.planner.state import OpRow

    store.create_deploy(
        deploy_id="d1", scenario_id="s", scenario_version=1,
        scenario_schema_version=1, plan_id="p", ir_hash="h", placement={},
    )
    store.create_deploy(
        deploy_id="d2", scenario_id="s", scenario_version=1,
        scenario_schema_version=1, plan_id="p", ir_hash="h", placement={},
    )
    op_id = "p:clone-vm[h]"  # identical op_id under both deploys (same plan)

    def _mk(deploy_id: str) -> OpRow:
        return OpRow(
            op_id=op_id, deploy_id=deploy_id, op_name="clone-vm",
            op_catalog_id="OP-HOST-010", kind="action",
            scope_json='{"host":"h"}', inputs_resolved_json="{}",
            inputs_from_outputs_json="{}", outputs_json=None,
            cleanup_strategy="redo", idempotency_class="infra",
            status=OpStatus.PENDING, started_at=None, completed_at=None,
        )

    store.insert_ops("d1", [_mk("d1")])
    store.insert_ops("d2", [_mk("d2")])

    # Completing d1's op must NOT be visible under d2 — independent rows.
    store.complete_op("d1", op_id, outputs={"vmid": 1})
    assert store.get_op("d1", op_id).status == OpStatus.COMPLETED
    assert store.get_op("d2", op_id).status == OpStatus.PENDING
    assert len(store.ops_for_deploy("d1")) == 1
    assert len(store.ops_for_deploy("d2")) == 1

    # d2's op-attempt history is independent too (deploy_id-scoped table).
    a1 = store.start_attempt("d2", op_id)
    store.end_attempt(a1, AttemptResult.SUCCESS)
    assert len(store.attempts_for_op("d1", op_id)) == 0
    assert len(store.attempts_for_op("d2", op_id)) == 1


def test_opening_pre_v2_schema_db_fails_loudly(tmp_path):
    """A state.db written under the pre-composite-PK schema (`op_id` alone
    as PRIMARY KEY) must refuse to open rather than silently keep operating
    against the stale schema — `CREATE TABLE IF NOT EXISTS` is a no-op
    against an existing table, so it would never repair one."""
    import sqlite3

    from axrange.planner.state import StateStoreSchemaError

    db_path = tmp_path / "state.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE deploys (deploy_id TEXT PRIMARY KEY)")
    conn.execute("CREATE TABLE ops (op_id TEXT PRIMARY KEY, deploy_id TEXT)")
    conn.commit()
    conn.close()

    with pytest.raises(StateStoreSchemaError):
        StateStore(db_path)


def test_fresh_db_schema_creation_is_atomic_to_a_concurrent_opener(tmp_path):
    """Regression for the 2026-07-07 schema-guard race: two processes racing
    to create a fresh SHARED state.db must never let a concurrent opener
    observe the `ops` table committed while `user_version` still reads 0 —
    that used to trip a false `StateStoreSchemaError` on a legitimately-fresh
    DB. `_SCHEMA` now stamps `PRAGMA user_version` inside the SAME explicit
    transaction as the table/index DDL, so a concurrent reader either sees
    NOTHING (version 0, no tables) or EVERYTHING (all tables + the stamped
    version) — never the partial state in between."""
    import threading

    from axrange.planner.state import _SCHEMA, StateStore

    db_path = tmp_path / "state.db"

    pause = threading.Event()
    resume = threading.Event()
    hit_count = 0

    conn = sqlite3.connect(str(db_path), isolation_level=None, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")

    def progress_handler() -> int:
        nonlocal hit_count
        hit_count += 1
        # Pause partway through the DDL script, well after the first
        # CREATE TABLE has run but before COMMIT.
        if hit_count == 5:
            pause.set()
            resume.wait(timeout=5)
        return 0

    conn.set_progress_handler(progress_handler, 1)

    observations: list[tuple[object, int]] = []

    def run_script() -> None:
        conn.executescript(_SCHEMA)

    t = threading.Thread(target=run_script)
    t.start()
    assert pause.wait(timeout=5), "progress handler never paused mid-script"

    # A second connection opened while the first is mid-transaction must see
    # a fully-fresh DB (no ops table, version 0) -- never the partial state.
    conn2 = sqlite3.connect(str(db_path))
    conn2.execute("PRAGMA busy_timeout=200")
    has_ops = conn2.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ops'"
    ).fetchone()
    version = conn2.execute("PRAGMA user_version").fetchone()[0]
    observations.append((has_ops, version))
    conn2.close()

    resume.set()
    t.join(timeout=5)
    conn.close()

    (has_ops, version) = observations[0]
    assert has_ops is None, "concurrent opener saw a partially-created schema"
    assert version == 0, "concurrent opener saw a partial user_version stamp"

    # And after the writer commits, StateStore opens cleanly against the now-
    # fully-materialized DB (no false StateStoreSchemaError).
    store = StateStore(db_path)
    store.close()


# ---------------------------------------------------------------------------
# `docs/WORK_ORDER_twin_incremental_update.md` §4.1 — scenario-scoped
# discovery + §3.4's generation bookkeeping.
# ---------------------------------------------------------------------------
def test_deploys_matching_scenario_id_finds_standing_deploy_by_a_pinned_plan_id(store):
    """The exact scenario `--update` exists to fix: a standing deploy whose
    `plan_id` no longer equals `derive_plan_id(scenario_id, ir_hash)` (it
    was pinned by a prior update) is still found by `scenario_id` alone."""
    store.create_deploy(
        deploy_id="corp-abc123", scenario_id="corp-enterprise", scenario_version=1,
        scenario_schema_version=1, plan_id="corp-enterprise-abc123", ir_hash="h-original",
        placement={"h-a": "pxtest1"},
    )
    # Simulate a prior --update: ir_hash advanced, plan_id stayed pinned.
    store.update_deploy_generation(
        "corp-abc123", ir_hash="h-edited", placement={"h-a": "pxtest1", "h-c": "pxtest1"},
    )

    # A fresh `derive_plan_id(scenario_id, "h-edited")` would NOT equal the
    # pinned plan_id -- find_deploy_for_plan on that fresh id would miss.
    assert store.find_deploy_for_plan("corp-enterprise-deadbeef") is None

    rows = store.deploys_matching(
        scenario_id="corp-enterprise", tier="te", exclude_destroyed=True, limit=1,
    )
    assert len(rows) == 1
    assert rows[0].deploy_id == "corp-abc123"
    assert rows[0].plan_id == "corp-enterprise-abc123"  # still pinned
    assert rows[0].ir_hash == "h-edited"  # current generation


def test_deploys_matching_scenario_id_excludes_other_scenarios(store):
    store.create_deploy(
        deploy_id="a-1", scenario_id="scenario-a", scenario_version=1,
        scenario_schema_version=1, plan_id="plan-a", ir_hash="h", placement={},
    )
    store.create_deploy(
        deploy_id="b-1", scenario_id="scenario-b", scenario_version=1,
        scenario_schema_version=1, plan_id="plan-b", ir_hash="h", placement={},
    )
    rows = store.deploys_matching(scenario_id="scenario-a")
    assert [r.deploy_id for r in rows] == ["a-1"]


def test_deploys_matching_scenario_id_excludes_destroyed_when_asked(store):
    store.create_deploy(
        deploy_id="d1", scenario_id="s", scenario_version=1,
        scenario_schema_version=1, plan_id="plan-x", ir_hash="h", placement={},
    )
    store.mark_destroyed("d1")
    assert store.deploys_matching(scenario_id="s", exclude_destroyed=True) == []
    assert len(store.deploys_matching(scenario_id="s")) == 1


def test_update_deploy_generation_leaves_plan_id_untouched(store):
    row = store.create_deploy(
        deploy_id="d1", scenario_id="s", scenario_version=1,
        scenario_schema_version=1, plan_id="plan-pinned", ir_hash="h-old",
        placement={"h-a": "pxtest1"},
    )
    store.update_deploy_generation(
        "d1", ir_hash="h-new", placement={"h-a": "pxtest1", "h-b": "pxtest1"},
    )
    fetched = store.get_deploy("d1")
    assert fetched.plan_id == "plan-pinned" == row.plan_id
    assert fetched.ir_hash == "h-new"
    assert json.loads(fetched.placement_json) == {"h-a": "pxtest1", "h-b": "pxtest1"}


def test_update_deploy_generation_does_not_touch_status(store):
    store.create_deploy(
        deploy_id="d1", scenario_id="s", scenario_version=1,
        scenario_schema_version=1, plan_id="p", ir_hash="h", placement={},
    )
    store.update_deploy_status("d1", DeployStatus.COMPLETED)
    store.update_deploy_generation("d1", ir_hash="h2", placement={})
    assert store.get_deploy("d1").status == DeployStatus.COMPLETED


# ---------------------------------------------------------------------------
# `mark_ops_destroyed` — the scoped-teardown batch marker
# ---------------------------------------------------------------------------
def test_mark_ops_destroyed_marks_only_the_given_ids(store):
    from axrange.planner.state import OpRow

    store.create_deploy(
        deploy_id="d1", scenario_id="s", scenario_version=1,
        scenario_schema_version=1, plan_id="p", ir_hash="h", placement={},
    )
    ops = [
        OpRow(
            op_id=f"p:clone-vm[h-{i}]", deploy_id="d1", op_name="clone-vm",
            op_catalog_id="OP-HOST-010", kind="action",
            scope_json=f'{{"host":"h-{i}"}}', inputs_resolved_json="{}",
            inputs_from_outputs_json="{}", outputs_json=None,
            cleanup_strategy="redo", idempotency_class="infra",
            status=OpStatus.COMPLETED, started_at=1.0, completed_at=2.0,
        )
        for i in ("a", "b", "c")
    ]
    store.insert_ops("d1", ops)

    n = store.mark_ops_destroyed("d1", ["p:clone-vm[h-a]", "p:clone-vm[h-c]"])

    assert n == 2
    assert store.get_op("d1", "p:clone-vm[h-a]").status == OpStatus.DESTROYED
    assert store.get_op("d1", "p:clone-vm[h-c]").status == OpStatus.DESTROYED
    assert store.get_op("d1", "p:clone-vm[h-b]").status == OpStatus.COMPLETED


def test_mark_ops_destroyed_empty_list_is_a_noop(store):
    store.create_deploy(
        deploy_id="d1", scenario_id="s", scenario_version=1,
        scenario_schema_version=1, plan_id="p", ir_hash="h", placement={},
    )
    assert store.mark_ops_destroyed("d1", []) == 0
