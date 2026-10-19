"""Unit tests for `axrange.planner.plandiff` (`docs/
WORK_ORDER_twin_incremental_update.md`).

Pure, no DB/cluster: `diff_plan`/`barriers_to_reevaluate`/`ops_to_reexecute`
take a `Plan` + persisted `OpRow`s directly. Covers work order §6 criteria 1,
5, 8 (the `plandiff`-level ones) plus the code-review MUST-FIX 1 extension of
§3.3 past barriers to non-barrier aggregate ops; §6.2/§6.3's pinned-plan_id/
zero-adapter-call guarantees, and MUST-FIX 1's two measured failure shapes
against the REAL te-emittable catalog, are covered end-to-end in
`tests/test_twin_incremental_update.py`.
"""
from __future__ import annotations

import pytest

from axrange.ops.registry import CleanupStrategy, IdempotencyClass, OpKind, get_op
from axrange.planner.dag import BarrierInstance, OpInstance, Plan, _op_id
from axrange.planner.plandiff import (
    ABSENT,
    REAPPLY_OP_NAME,
    content_digest,
    diff_plan,
    barriers_to_reevaluate,
    ops_to_reexecute,
    reapply_ineligibility_reason,
    reapplyable_ops,
    render_value,
    _ops_with_bare_star_after,
)
from axrange.planner.state import OpRow, OpStatus, op_to_row


PLAN_ID = "twin-abc12345"


def _op(op_name: str, scope: dict, inputs: dict | None = None) -> OpInstance:
    return OpInstance(
        op_id=_op_id(PLAN_ID, op_name, scope),
        op_name=op_name,
        op_catalog_id="OP-TEST-000",
        kind=OpKind.ACTION,
        scope=dict(scope),
        inputs_resolved=dict(inputs or {}),
        inputs_from_outputs={},
        cleanup_strategy=CleanupStrategy.REDO,
        idempotency_class=IdempotencyClass.INFRA,
        resource_categories=(),
        timeout_s=None,
    )


def _barrier(barrier_name: str, scope: dict) -> BarrierInstance:
    return BarrierInstance(
        op_id=_op_id(PLAN_ID, barrier_name, scope),
        barrier_name=barrier_name,
        barrier_catalog_id="OP-BAR-000",
        scope=dict(scope),
    )


def _plan(*nodes) -> Plan:
    plan = Plan(
        plan_id=PLAN_ID, scenario_id="twin", scenario_version=1, ir_hash="h",
    )
    for n in nodes:
        if isinstance(n, BarrierInstance):
            plan.add_barrier(n)
        else:
            plan.add_op(n)
    plan.freeze()
    return plan


def _persisted_row(op: OpInstance, *, status: OpStatus = OpStatus.COMPLETED) -> OpRow:
    """A persisted row whose `inputs_resolved_json` matches `op` exactly
    (the "this op hasn't changed" baseline) -- tests mutate a COPY of `op`
    with different inputs to exercise the Mutated path."""
    row = op_to_row(op, deploy_id="d1")
    row.status = status
    return row


# ---------------------------------------------------------------------------
# diff_plan — the four-plus-one classification
# ---------------------------------------------------------------------------
def test_unchanged_when_id_present_completed_and_inputs_match():
    op_a = _op("clone-vm", {"host": "h-a"}, {"template_vmid": 610})
    new_plan = _plan(op_a)
    persisted = [_persisted_row(op_a)]

    diff = diff_plan(new_plan, persisted)

    assert diff.unchanged == {op_a.op_id}
    assert diff.added == frozenset()
    assert diff.removed == frozenset()
    assert diff.mutated == frozenset()
    assert diff.retry == frozenset()


def test_added_when_op_id_has_no_persisted_row():
    op_a = _op("clone-vm", {"host": "h-a"})
    op_b = _op("clone-vm", {"host": "h-b"})  # new node this update adds
    new_plan = _plan(op_a, op_b)
    persisted = [_persisted_row(op_a)]

    diff = diff_plan(new_plan, persisted)

    assert diff.added == {op_b.op_id}
    assert diff.unchanged == {op_a.op_id}
    assert diff.removed == frozenset()


def test_removed_when_persisted_row_absent_from_new_plan():
    op_a = _op("clone-vm", {"host": "h-a"})
    op_b = _op("clone-vm", {"host": "h-b"})  # will be removed
    new_plan = _plan(op_a)  # h-b dropped from the edited IR
    persisted = [_persisted_row(op_a), _persisted_row(op_b)]

    diff = diff_plan(new_plan, persisted)

    assert diff.removed == {op_b.op_id}
    assert diff.unchanged == {op_a.op_id}
    assert diff.added == frozenset()


def test_already_destroyed_row_is_never_reclassified_removed_again():
    """M2 (code review): a row a PRIOR update's removal sweep already marked
    `destroyed` must be invisible to a LATER diff entirely -- it is already
    swept and belongs in no bucket. Left in, it would be re-classified
    `removed` on EVERY subsequent diff forever (work order §6 criterion
    (c): "re-run the same update -> clean no-op" would never actually
    become clean)."""
    op_a = _op("clone-vm", {"host": "h-a"})
    op_b = _op("clone-vm", {"host": "h-b"})  # already swept by a PRIOR update
    new_plan = _plan(op_a)  # h-b still absent -- an identical re-apply
    persisted = [
        _persisted_row(op_a),
        _persisted_row(op_b, status=OpStatus.DESTROYED),
    ]

    diff = diff_plan(new_plan, persisted)

    assert diff.removed == frozenset()
    assert diff.added == frozenset()
    assert diff.unchanged == {op_a.op_id}
    assert diff.retry == frozenset()
    assert diff.is_noop is True  # the clean-no-op property §6(c) requires


def test_mutated_when_completed_row_inputs_differ_and_reports_old_new():
    old_op = _op("configure-vm-hardware", {"host": "h-a"}, {"ram": "1Gi", "cpu": 1})
    new_op = _op("configure-vm-hardware", {"host": "h-a"}, {"ram": "4Gi", "cpu": 1})
    assert old_op.op_id == new_op.op_id  # same scope -> same id, by construction
    new_plan = _plan(new_op)
    persisted = [_persisted_row(old_op)]

    diff = diff_plan(new_plan, persisted)

    assert diff.mutated == {new_op.op_id}
    assert diff.unchanged == frozenset()
    assert new_op.op_id in diff.mutations
    fields = diff.mutations[new_op.op_id]
    assert fields == {"ram": ("1Gi", "4Gi")}


def test_mutated_reports_a_field_that_appeared_or_disappeared_as_absent():
    old_op = _op("clone-vm", {"host": "h-a"}, {"image_id": "debian-12-cloud@v1"})
    new_op = _op(
        "clone-vm", {"host": "h-a"},
        {"image_id": "debian-12-cloud@v1", "template_vmid": 620},
    )
    new_plan = _plan(new_op)
    persisted = [_persisted_row(old_op)]

    diff = diff_plan(new_plan, persisted)

    assert diff.mutated == {new_op.op_id}
    assert diff.mutations[new_op.op_id] == {"template_vmid": (ABSENT, 620)}
    assert render_value(ABSENT) == "<unset>"
    assert render_value(620) == "620"


def test_retired_persisted_input_key_dropped_from_new_plan_is_not_a_mutation():
    """M1 migration clause (`docs/WORK_ORDER_twin_incremental_update.md`):
    `served_hosts` used to be persisted as part of `configure-router`'s
    `inputs_resolved` -- a deploy created BEFORE the fix that moved it to
    `planner_hints` (dag.py) still has the key baked into its persisted
    `inputs_resolved_json`. A freshly-built plan's op never carries the key
    in `inputs_resolved` AT ALL any more (it lives in `planner_hints`,
    which `_new_inputs` never even looks at). That must NOT be seen as a
    dropped/mutated field -- the whole point of the fix is that this exact
    comparison must come back clean, or every router-bearing scenario's
    FIRST `--update` after upgrading still hard-refuses."""
    old_op = _op(
        "configure-router", {"host": "gw"},
        {"node_id": "gw", "served_hosts": ["h-a", "h-b"]},
    )
    new_op = _op("configure-router", {"host": "gw"}, {"node_id": "gw"})
    assert old_op.op_id == new_op.op_id  # same scope -> same id, by construction
    new_plan = _plan(new_op)
    persisted = [_persisted_row(old_op)]

    diff = diff_plan(new_plan, persisted)

    assert diff.unchanged == {new_op.op_id}
    assert diff.mutated == frozenset()
    assert diff.mutations == {}


def test_retired_persisted_input_key_present_on_both_sides_is_compared_normally():
    """The exclusion is ASYMMETRIC (SHOULD-FIX 4, code review), NOT an
    unconditional both-sides skip: only the migration direction --
    persisted on the OLD row, absent from the freshly-built new one -- is
    tolerated (the test above). A key present on BOTH sides (e.g.
    re-introduced for a genuinely different purpose, or a bug that starts
    persisting it again) is compared NORMALLY and CAN surface as Mutated --
    an unconditional both-sides skip would make that re-introduction
    invisible forever, defeating the whole point of keeping this set at
    all."""
    old_op = _op(
        "configure-router", {"host": "gw"},
        {"node_id": "gw", "served_hosts": ["h-a"]},
    )
    new_op = _op(
        "configure-router", {"host": "gw"},
        {"node_id": "gw", "served_hosts": ["h-a", "h-b"]},
    )
    new_plan = _plan(new_op)
    persisted = [_persisted_row(old_op)]

    diff = diff_plan(new_plan, persisted)

    assert diff.mutated == {new_op.op_id}
    assert diff.unchanged == frozenset()
    assert diff.mutations[new_op.op_id] == {
        "served_hosts": (["h-a"], ["h-a", "h-b"])
    }


@pytest.mark.parametrize("status", [OpStatus.FAILED, OpStatus.HALTED, OpStatus.PENDING])
def test_present_but_not_completed_is_retry_not_unchanged(status):
    """Work order §6 criterion 1: an op present but failed/halted/stuck-
    pending must be classified for retry, NOT folded into 'unchanged' --
    today's resume path (`reset_incomplete_ops`) still needs to touch it."""
    op_a = _op("clone-vm", {"host": "h-a"}, {"template_vmid": 610})
    new_plan = _plan(op_a)
    persisted = [_persisted_row(op_a, status=status)]

    diff = diff_plan(new_plan, persisted)

    assert diff.retry == {op_a.op_id}
    assert op_a.op_id not in diff.unchanged
    assert op_a.op_id not in diff.mutated  # not completed -> never checked for mutation


def test_present_but_not_completed_with_changed_inputs_is_still_retry_not_mutated():
    """A row that never finished is not dangerous to redo -- changed inputs
    on a `failed`/`pending` op must NOT trip the mutation refusal (that is
    reserved for a `completed` row whose real-world effect already applied
    under the OLD inputs)."""
    old_op = _op("clone-vm", {"host": "h-a"}, {"template_vmid": 610})
    new_op = _op("clone-vm", {"host": "h-a"}, {"template_vmid": 620})
    new_plan = _plan(new_op)
    persisted = [_persisted_row(old_op, status=OpStatus.FAILED)]

    diff = diff_plan(new_plan, persisted)

    assert diff.retry == {new_op.op_id}
    assert diff.mutated == frozenset()
    assert diff.mutations == {}


def test_barrier_present_in_both_never_mutates():
    from axrange.planner.state import barrier_to_row

    b = _barrier("network-ready", {"net": "corp"})
    new_plan = _plan(b)
    persisted = [barrier_to_row(b, deploy_id="d1")]
    persisted[0].status = OpStatus.COMPLETED

    diff = diff_plan(new_plan, persisted)

    assert diff.unchanged == {b.op_id}
    assert diff.mutated == frozenset()


def test_is_noop_true_only_when_nothing_needs_doing():
    op_a = _op("clone-vm", {"host": "h-a"})
    new_plan = _plan(op_a)
    diff = diff_plan(new_plan, [_persisted_row(op_a)])
    assert diff.is_noop is True

    diff2 = diff_plan(_plan(op_a, _op("clone-vm", {"host": "h-b"})), [_persisted_row(op_a)])
    assert diff2.is_noop is False


# ---------------------------------------------------------------------------
# op_names / scopes -- (S7/S8, code review): every added/removed id must
# carry a resolvable (op_name, scope), whether its node is still in the new
# plan (added) or only in the persisted rows (removed). A newly-ADDED
# BARRIER exercises the one shape `opview.op_fields` can't duck-type
# directly (no `op_name` attribute) -- `diff_plan` must not crash on it.
# ---------------------------------------------------------------------------
def test_op_names_and_scopes_cover_added_and_removed_ops():
    op_a = _op("clone-vm", {"host": "h-a"})
    op_new = _op("clone-vm", {"host": "h-new"})
    op_gone = _op("clone-vm", {"host": "h-gone"})
    new_plan = _plan(op_a, op_new)  # h-gone dropped, h-new added
    persisted = [_persisted_row(op_a), _persisted_row(op_gone)]

    diff = diff_plan(new_plan, persisted)

    assert diff.added == {op_new.op_id}
    assert diff.removed == {op_gone.op_id}
    assert diff.op_names[op_new.op_id] == "clone-vm"
    assert diff.scopes[op_new.op_id] == {"host": "h-new"}
    assert diff.op_names[op_gone.op_id] == "clone-vm"
    assert diff.scopes[op_gone.op_id] == {"host": "h-gone"}


def test_op_names_and_scopes_handle_a_newly_added_barrier():
    """A `BarrierInstance` has no `op_name` attribute (it's `barrier_name`)
    -- `opview.op_fields` only duck-types `OpInstance`/`OpRow`, so `diff_
    plan` must branch around it locally rather than crash when a brand new
    barrier (e.g. a new segment's `network-ready`) lands in `added`."""
    host_c = _op("attach-vm-to-vnet", {"host": "h-c", "net": "dmz"})
    new_barrier = _barrier("network-ready", {"net": "dmz"})
    new_plan = _plan(host_c, new_barrier)
    persisted: list = []

    diff = diff_plan(new_plan, persisted)

    assert new_barrier.op_id in diff.added
    assert diff.op_names[new_barrier.op_id] == "network-ready"
    assert diff.scopes[new_barrier.op_id] == {"net": "dmz"}


# ---------------------------------------------------------------------------
# barriers_to_reevaluate — §3.3
# ---------------------------------------------------------------------------
def test_barrier_gaining_a_new_predecessor_is_flagged_for_reeval():
    host_a = _op("attach-vm-to-vnet", {"host": "h-a", "net": "corp"})
    host_c = _op("attach-vm-to-vnet", {"host": "h-c", "net": "corp"})  # newly added
    barrier = _barrier("network-ready", {"net": "corp"})
    plan = _plan(host_a, host_c, barrier)
    plan.add_edge(host_a.op_id, barrier.op_id, kind="barrier-pred")
    plan.add_edge(host_c.op_id, barrier.op_id, kind="barrier-pred")
    plan.freeze()

    added = {host_c.op_id}  # barrier itself is Unchanged-by-id; only host_c is new
    stale = barriers_to_reevaluate(plan, added)

    assert barrier.op_id in stale


def test_barrier_with_unchanged_predecessors_is_not_flagged():
    host_a = _op("attach-vm-to-vnet", {"host": "h-a", "net": "corp"})
    host_b = _op("attach-vm-to-vnet", {"host": "h-b", "net": "corp"})
    barrier = _barrier("network-ready", {"net": "corp"})
    plan = _plan(host_a, host_b, barrier)
    plan.add_edge(host_a.op_id, barrier.op_id, kind="barrier-pred")
    plan.add_edge(host_b.op_id, barrier.op_id, kind="barrier-pred")
    plan.freeze()

    # Nothing was added anywhere in this update.
    stale = barriers_to_reevaluate(plan, added=set())

    assert stale == frozenset()


def test_brand_new_barrier_is_not_in_the_reeval_set():
    """A barrier that is ITSELF newly added is handled by the ordinary
    Added-op execution path, not the reeval force-reset -- it has no
    persisted row to stale-skip in the first place."""
    host_c = _op("attach-vm-to-vnet", {"host": "h-c", "net": "dmz"})
    new_barrier = _barrier("network-ready", {"net": "dmz"})  # a whole new segment
    plan = _plan(host_c, new_barrier)
    plan.add_edge(host_c.op_id, new_barrier.op_id, kind="barrier-pred")
    plan.freeze()

    added = {host_c.op_id, new_barrier.op_id}
    stale = barriers_to_reevaluate(plan, added)

    assert new_barrier.op_id not in stale


def test_barrier_losing_a_predecessor_is_not_flagged():
    """Removing a predecessor only makes an already-`completed` barrier's
    satisfied condition MORE true, not less -- no reeval needed (module
    docstring: we can't see the old edges anyway, only detect gains)."""
    host_a = _op("attach-vm-to-vnet", {"host": "h-a", "net": "corp"})
    barrier = _barrier("network-ready", {"net": "corp"})
    # h-b's attach op is gone from the new plan (host removed); only h-a remains.
    plan = _plan(host_a, barrier)
    plan.add_edge(host_a.op_id, barrier.op_id, kind="barrier-pred")
    plan.freeze()

    stale = barriers_to_reevaluate(plan, added=set())

    assert stale == frozenset()


# ---------------------------------------------------------------------------
# ops_to_reexecute — §3.3 extended past barriers to non-barrier AGGREGATE
# ops (MUST-FIX 1, code review). `_ops_with_bare_star_after` is the
# mechanism that DETERMINES the set (mechanically, from the catalog's own
# `after:` shape, never hand-picked op names) -- pinned first below, then
# `ops_to_reexecute` itself against synthetic Plans exercising both a
# positive case (the bare-star shape) and a negative case (the far more
# common `[$var@*]` shape, which must NOT be swept in).
# ---------------------------------------------------------------------------
def test_bare_star_after_pattern_ops_matches_expected_catalog_set():
    """Locks WHICH op types the generalized mechanism covers today. Derived
    mechanically from the catalog's own `after:` clauses (`_has_bare_star_
    after`'s `pattern.endswith("[*]")` test), not hand-maintained -- if a
    FUTURE catalog op grows this shape, this test's failure is the intended
    signal to examine it (a genuine whole-set aggregate the same way
    apply-sdn-config is? or does it need its own exclusion?), not a silent,
    unreviewed scope change to what a `--update` force-re-executes.

    `create-sdn-zone`/`apply-sdn-config` are both emitted by the `te`
    planner (the ops this fix actually needed to reach); `verify-capture`
    is catalog-present but never `te`-emitted any more (`docs/
    WORK_ORDER_tap_eligibility.md` -- composed only by the A2 tap playout)
    -- included here because the mechanism is meant to generalize from the
    catalog SHAPE, not from "which ops a te `--update` happens to touch
    today"."""
    assert _ops_with_bare_star_after() == {
        "create-sdn-zone", "apply-sdn-config", "verify-capture",
    }


def test_ops_to_reexecute_flags_aggregate_op_gaining_an_added_predecessor():
    """The core MUST-FIX 1 mechanism: `apply-sdn-config`'s own literal
    inputs (just `zone_id`) never change when a NEW `create-vnet` lands
    elsewhere in the plan, so `diff_plan`'s field comparison alone would
    leave it stale-`completed` forever. `ops_to_reexecute` catches it via
    the catalog's bare-star `after:` shape, exactly like
    `barriers_to_reevaluate` catches a barrier gaining a new predecessor."""
    zone = _op("create-sdn-zone", {"scenario": "s1"}, {"scenario_id": "s1"})
    apply_cfg = _op("apply-sdn-config", {"scenario": "s1"})
    new_vnet = _op("create-vnet", {"net": "eng"})  # the newly ADDED network
    plan = _plan(zone, apply_cfg, new_vnet)
    plan.add_edge(zone.op_id, apply_cfg.op_id, kind="ordering")
    plan.add_edge(new_vnet.op_id, apply_cfg.op_id, kind="ordering")
    plan.freeze()

    added = {new_vnet.op_id}
    stale = ops_to_reexecute(plan, added)

    assert apply_cfg.op_id in stale
    assert zone.op_id not in stale  # its OWN predecessor set never changed


def test_ops_to_reexecute_flags_create_sdn_zone_too_not_just_apply_sdn_config():
    """The mechanism must not be a hand-picked special-case for
    `apply-sdn-config` alone: `create-sdn-zone`'s `after=("create-ovs-
    bridge[*]",)` is the SAME bare-star shape (Simple-zone twins emit one
    `create-ovs-bridge[$net]` per network, so a newly-added network's
    bridge genuinely IS a new predecessor of the plan's single
    `create-sdn-zone`)."""
    new_bridge = _op("create-ovs-bridge", {"net": "eng"})  # newly ADDED network's bridge
    zone = _op("create-sdn-zone", {"scenario": "s1"}, {"scenario_id": "s1"})
    plan = _plan(new_bridge, zone)
    plan.add_edge(new_bridge.op_id, zone.op_id, kind="ordering")
    plan.freeze()

    added = {new_bridge.op_id}
    stale = ops_to_reexecute(plan, added)

    assert zone.op_id in stale


def test_ops_to_reexecute_does_not_flag_a_bound_star_tail_op():
    """The `[$var@*]` shape (e.g. `start-vm[$host]`'s `attach-vm-to-vnet
    [$host@*]`) ties the fan-in to the SAME entity as the consumer -- pure
    sequencing, not a cross-entity commit -- and critically covers
    non-idempotent ops (`run-script`, `CleanupStrategy.ESCALATE`) that must
    NEVER be silently force-re-executed just because an unrelated peer op
    landed elsewhere in the plan. `ops_to_reexecute` must not flag it even
    though its predecessor set literally gained an Added member -- this is
    the boundary that keeps the mechanism from over-applying."""
    attach_a = _op("attach-vm-to-vnet", {"host": "h-a", "net": "corp"})
    attach_b = _op("attach-vm-to-vnet", {"host": "h-a", "net": "dmz"})  # ADDED: new NIC
    start = _op("start-vm", {"host": "h-a"})
    plan = _plan(attach_a, attach_b, start)
    plan.add_edge(attach_a.op_id, start.op_id, kind="ordering")
    plan.add_edge(attach_b.op_id, start.op_id, kind="ordering")
    plan.freeze()

    added = {attach_b.op_id}
    stale = ops_to_reexecute(plan, added)

    assert start.op_id not in stale


def test_ops_to_reexecute_does_not_flag_a_non_idempotent_op_like_run_script():
    """Same boundary, made concrete against the catalog op the review
    specifically called out as dangerous to blindly redo: `run-script` is
    `CleanupStrategy.ESCALATE` (opaque, non-idempotent) and reaches other
    `provision-app` instances only via the `[$app@*]` bound-star shape, so
    it must never appear in `ops_to_reexecute`'s output regardless of what
    gets added elsewhere in the plan."""
    provision_a = _op("provision-app", {"app": "nmap", "host": "h-a"})
    provision_b = _op("provision-app", {"app": "nmap", "host": "h-b"})  # ADDED
    script = _op("run-script", {"app": "nmap", "host": "h-a"})
    plan = _plan(provision_a, provision_b, script)
    plan.add_edge(provision_a.op_id, script.op_id, kind="ordering")
    plan.add_edge(provision_b.op_id, script.op_id, kind="ordering")
    plan.freeze()

    added = {provision_b.op_id}
    stale = ops_to_reexecute(plan, added)

    assert script.op_id not in stale


def test_ops_to_reexecute_empty_when_nothing_added():
    apply_cfg = _op("apply-sdn-config", {"scenario": "s1"})
    plan = _plan(apply_cfg)
    plan.freeze()

    assert ops_to_reexecute(plan, added=set()) == frozenset()


def test_ops_to_reexecute_excludes_an_op_that_is_itself_newly_added():
    """A brand-new aggregate op (e.g. a whole new scenario-scoped plan) is
    handled by the ordinary Added-op execution path, not the reeval
    force-reset -- it has no persisted row to stale-skip in the first
    place (mirrors `test_brand_new_barrier_is_not_in_the_reeval_set`)."""
    new_vnet = _op("create-vnet", {"net": "eng"})
    new_apply = _op("apply-sdn-config", {"scenario": "s2"})
    plan = _plan(new_vnet, new_apply)
    plan.add_edge(new_vnet.op_id, new_apply.op_id, kind="ordering")
    plan.freeze()

    added = {new_vnet.op_id, new_apply.op_id}
    stale = ops_to_reexecute(plan, added)

    assert new_apply.op_id not in stale


# ---------------------------------------------------------------------------
# reapplyable_ops / reapply_ineligibility_reason -- the content-only re-apply
# predicate (`docs/WORK_ORDER_responder_op_row_drift.md` option A). The
# NARROWNESS is the entire safety argument (§7.3), so each rejection shape
# gets its own pin.
# ---------------------------------------------------------------------------
def test_reapply_op_name_matches_the_documented_catalog_id():
    """`REAPPLY_OP_NAME` matches by op NAME (`run-script`) -- the same idiom
    `_ops_with_bare_star_after` above already uses -- rather than by the
    catalog id (`OP-APP-030`) that CLAUDE.md calls the documented stable
    handle ("never renumber an OP-<CAT>-<NNN>"). Code review (2026-09-02)
    flagged that mismatch; this test is the chosen mitigation (`plandiff.
    py`'s own comment on `REAPPLY_OP_NAME` explains the id-vs-name
    tradeoff): if `run-script` is ever renamed OR renumbered in the
    catalog, this fails loudly here instead of the reapply predicate
    silently no longer matching the op it was built for."""
    op_def = get_op(REAPPLY_OP_NAME)
    assert op_def is not None
    assert op_def.name == REAPPLY_OP_NAME
    assert op_def.id == "OP-APP-030"


def _script_op(content: str, *, name: str = "web-setup.sh") -> OpInstance:
    return _op(
        "run-script", {"app": "web", "host": "h-a"},
        {"app_id": "web", "host_id": "h-a", "script_name": name,
         "script_content": content, "os_family": "linux"},
    )


def test_reapplyable_ops_matches_a_script_content_only_mutation():
    old_op = _script_op("#!/bin/sh\necho old\n")
    new_op = _script_op("#!/bin/sh\necho new\n")
    new_plan = _plan(new_op)

    diff = diff_plan(new_plan, [_persisted_row(old_op)])

    assert diff.mutated == {new_op.op_id}
    assert set(diff.mutations[new_op.op_id]) == {"script_content"}
    assert reapplyable_ops(new_plan, diff) == {new_op.op_id}


def test_reapplyable_ops_rejects_script_content_plus_another_field():
    """Renaming the script file changes `script_name` too -- two differing
    fields, so the predicate must decline even though ONE of them is the
    covered field."""
    old_op = _script_op("#!/bin/sh\necho old\n", name="web-setup.sh")
    new_op = _script_op("#!/bin/sh\necho new\n", name="web-boot.sh")
    new_plan = _plan(new_op)

    diff = diff_plan(new_plan, [_persisted_row(old_op)])

    assert set(diff.mutations[new_op.op_id]) == {"script_content", "script_name"}
    assert reapplyable_ops(new_plan, diff) == frozenset()
    reason = reapply_ineligibility_reason(
        "run-script", diff.mutations[new_op.op_id]
    )
    assert reason is not None and "script_name" in reason


def test_reapplyable_ops_rejects_a_non_content_field_alone():
    old_op = _script_op("#!/bin/sh\n:\n", name="web-setup.sh")
    new_op = _script_op("#!/bin/sh\n:\n", name="web-boot.sh")
    new_plan = _plan(new_op)

    diff = diff_plan(new_plan, [_persisted_row(old_op)])

    assert set(diff.mutations[new_op.op_id]) == {"script_name"}
    assert reapplyable_ops(new_plan, diff) == frozenset()


def test_reapplyable_ops_rejects_a_provision_app_change_as_out_of_scope():
    """`provision-app` is the `pkg:` sibling with the same `redo_on`
    trigger and is DELIBERATELY out of scope for this slice -- the refusal
    reason must say so, not merely decline."""
    old_op = _op(
        "provision-app", {"app": "web", "host": "h-a"},
        {"app_id": "web", "install_actions": [{"pkg": "nginx"}]},
    )
    new_op = _op(
        "provision-app", {"app": "web", "host": "h-a"},
        {"app_id": "web", "install_actions": [{"pkg": "htop"}]},
    )
    new_plan = _plan(new_op)

    diff = diff_plan(new_plan, [_persisted_row(old_op)])

    assert diff.mutated == {new_op.op_id}
    assert reapplyable_ops(new_plan, diff) == frozenset()
    reason = reapply_ineligibility_reason("provision-app", ["install_actions"])
    assert reason is not None
    assert "provision-app" in reason and "out of scope" in reason


def test_reapplyable_ops_is_empty_when_nothing_mutated():
    op_a = _script_op("#!/bin/sh\n:\n")
    new_plan = _plan(op_a)

    diff = diff_plan(new_plan, [_persisted_row(op_a)])

    assert diff.mutated == frozenset()
    assert reapplyable_ops(new_plan, diff) == frozenset()


def test_reapply_ineligibility_reason_is_none_only_for_the_exact_shape():
    assert reapply_ineligibility_reason("run-script", ["script_content"]) is None
    assert reapply_ineligibility_reason("run-script", []) is not None
    assert reapply_ineligibility_reason("run-script", ["script_content", "vmid"]) is not None
    assert reapply_ineligibility_reason("seed-dataset", ["script_content"]) is not None


def test_content_digest_never_reproduces_the_content_and_is_stable():
    """The re-apply plan prints a FINGERPRINT, never the script (work order
    option A: "never the full content")."""
    secret = "#!/bin/sh\necho SUPERSECRET-TOKEN\n"
    d = content_digest(secret)

    assert "SUPERSECRET-TOKEN" not in d
    assert d.startswith("sha256:")
    assert str(len(secret.encode())) in d          # byte count is disclosed
    assert d == content_digest(secret)             # stable
    assert d != content_digest(secret + " ")       # and content-sensitive
    assert content_digest(ABSENT) == "<unset>"


def test_render_value_elides_only_when_a_max_len_is_given():
    long = "x" * 500

    assert render_value(long) == repr(long)        # unchanged default
    assert render_value("1Gi", max_len=160) == "'1Gi'"
    elided = render_value(long, max_len=160)
    assert "xxxx" not in elided and "sha256:" in elided
