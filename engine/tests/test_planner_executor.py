"""Executor tests with a fake adapter and an in-memory state store.

What we check:
  - Happy path: every op succeeds; outputs flow producer -> consumer.
  - Retry: an op that fails twice then succeeds on attempt 3 is recorded as
    3 attempts in op_attempts, status=completed.
  - Halt-subtree: a terminal failure marks transitive successors as
    halted-due-to-upstream.
  - Barriers: complete with no adapter call when their preds are completed.
"""
from __future__ import annotations

import json
import tempfile
import threading
from pathlib import Path

import pytest

from axrange.ir.parser import parse_ir
from axrange.ops.registry import CleanupStrategy, IdempotencyClass, OpKind
from axrange.planner import Executor, build_plan
from axrange.planner.cluster import ClusterContext
from axrange.planner.dag import OpInstance, Plan
from axrange.planner.executor import AdapterError, RetryConfig
from axrange.planner.state import OpStatus, StateStore


FIXTURES = Path(__file__).parent / "fixtures"
SLICE_SCN = FIXTURES / "ir" / "valid_slice" / "scenario.yaml"


class _FakeAdapter:
    """Adapter that returns canned outputs per op type. Idempotent (no
    side effects)."""

    OUTPUTS = {
        "create-sdn-zone": {"zone_id": "tetest1"},
        "create-ovs-bridge": {"bridge_name": "tebr_corp"},
        "create-vnet": {"vnet_id": "tevcorp"},
        "apply-sdn-config": {"applied": True},
        "clone-vm": {"vmid": 100123},
        "configure-vm-hardware": {"configured": True},
        "configure-cloud-init": {"cloud_init_attached": True},
        "attach-vm-to-vnet": {"nic_attached": True},
        "start-vm": {"started": True},
        "wait-guest-agent": {"agent_ready": True},
        "wait-os-ready": {"os_ready": True},
        # A3 Stage A default-agent sweep (ON BY DEFAULT, 2026-07-11) — also
        # config-class, routed through this same fake.
        "install-defense-agent": {"agent_installed": True},
        "install-agent": {"agent_installed": True},
    }

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def run(self, op, resolved_inputs, ctx):
        self.calls.append((op.op_name, dict(resolved_inputs)))
        return dict(self.OUTPUTS.get(op.op_name, {}))


class _FlakyAdapter(_FakeAdapter):
    """Fails N times on the named op, then succeeds."""

    def __init__(self, flaky_op: str, fail_times: int):
        super().__init__()
        self.flaky_op = flaky_op
        self.fail_times = fail_times
        self._seen = 0

    def run(self, op, resolved_inputs, ctx):
        self.calls.append((op.op_name, dict(resolved_inputs)))
        if op.op_name == self.flaky_op:
            self._seen += 1
            if self._seen <= self.fail_times:
                raise AdapterError(
                    f"flaky: {op.op_name} failure #{self._seen}",
                    error_class="test-flaky",
                )
        return dict(self.OUTPUTS.get(op.op_name, {}))


class _HardFailAdapter(_FakeAdapter):
    """Always fails on the named op."""

    def __init__(self, hard_fail_op: str):
        super().__init__()
        self.hard_fail_op = hard_fail_op

    def run(self, op, resolved_inputs, ctx):
        self.calls.append((op.op_name, dict(resolved_inputs)))
        if op.op_name == self.hard_fail_op:
            raise AdapterError(
                f"hard fail: {op.op_name}", error_class="test-hard"
            )
        return dict(self.OUTPUTS.get(op.op_name, {}))


@pytest.fixture
def slice_plan(manifests):
    parsed = parse_ir(path=str(SLICE_SCN))
    ctx = ClusterContext.single_node("pxtest1", sensor_transport_ip="10.255.255.10")
    return build_plan(parsed.ir, manifests, ctx, raw_ir=parsed.raw), ctx


def _store_in(tmp: Path) -> StateStore:
    return StateStore(tmp / "state.db")


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_happy_path_runs_every_op(slice_plan):
    plan, ctx = slice_plan
    with tempfile.TemporaryDirectory() as tmp:
        store = _store_in(Path(tmp))
        adapter = _FakeAdapter()
        store.create_deploy(
            deploy_id="d1",
            scenario_id=plan.scenario_id,
            scenario_version=plan.scenario_version,
            scenario_schema_version=1,
            plan_id=plan.plan_id,
            ir_hash=plan.ir_hash,
            placement=plan.placement,
        )
        ex = Executor(plan, store, adapter, ctx, config_backend=adapter, deploy_id="d1",
                      sleep_fn=lambda _: None)
        ex.persist_plan()
        result = ex.run()
        store.close()

        assert result.succeeded
        # Every action/wait op should have made exactly one adapter call.
        # A3 Stage A default-agent sweep (ON BY DEFAULT, 2026-07-11) adds
        # install-defense-agent + install-agent (2 hosts each) to the slice.
        # Full-topology tap eligibility (2026-07-28, docs/WORK_ORDER_tap_
        # eligibility.md D1/D4): the te records NO tap/mirror-related op at
        # all -- no record-tap-intent (retired along with attach-gre-tunnel/
        # install-ovs-mirror/verify-capture before it, A2's job now).
        called = {n for n, _ in adapter.calls}
        assert called == {
            "create-sdn-zone", "create-ovs-bridge", "create-vnet",
            "apply-sdn-config", "clone-vm", "configure-vm-hardware",
            "configure-cloud-init", "attach-vm-to-vnet", "start-vm",
            "wait-guest-agent", "wait-os-ready",
            "install-defense-agent", "install-agent",
        }


def test_dataflow_inputs_resolve_from_prior_outputs(slice_plan):
    plan, ctx = slice_plan
    with tempfile.TemporaryDirectory() as tmp:
        store = _store_in(Path(tmp))
        adapter = _FakeAdapter()
        store.create_deploy(
            deploy_id="d2",
            scenario_id=plan.scenario_id,
            scenario_version=plan.scenario_version,
            scenario_schema_version=1,
            plan_id=plan.plan_id,
            ir_hash=plan.ir_hash,
            placement=plan.placement,
        )
        ex = Executor(plan, store, adapter, ctx, config_backend=adapter, deploy_id="d2",
                      sleep_fn=lambda _: None)
        ex.persist_plan()
        ex.run()
        store.close()

        # configure-vm-hardware must have received vmid from clone-vm.
        hw_calls = [
            inp for n, inp in adapter.calls if n == "configure-vm-hardware"
        ]
        assert all(inp.get("vmid") == 100123 for inp in hw_calls)
        # attach-vm-to-vnet must have received vnet_id from create-vnet.
        attach_calls = [
            inp for n, inp in adapter.calls if n == "attach-vm-to-vnet"
        ]
        assert all(inp.get("vnet_id") == "tevcorp" for inp in attach_calls)


# ---------------------------------------------------------------------------
# Retry semantics
# ---------------------------------------------------------------------------


def test_op_retries_on_adapter_error_then_succeeds(slice_plan):
    plan, ctx = slice_plan
    with tempfile.TemporaryDirectory() as tmp:
        store = _store_in(Path(tmp))
        # Fail clone-vm twice then succeed; default max_attempts=3.
        adapter = _FlakyAdapter("clone-vm", fail_times=2)
        store.create_deploy(
            deploy_id="d3",
            scenario_id=plan.scenario_id,
            scenario_version=plan.scenario_version,
            scenario_schema_version=1,
            plan_id=plan.plan_id,
            ir_hash=plan.ir_hash,
            placement=plan.placement,
        )
        ex = Executor(
            plan, store, adapter, ctx, config_backend=adapter, deploy_id="d3",
            retry=RetryConfig(max_attempts=3, initial_backoff_s=0.001, cap_s=0.001, jitter_pct=0.0),
            sleep_fn=lambda _: None,
        )
        ex.persist_plan()
        result = ex.run()

        # First clone-vm executed (the one in topological order first) saw
        # 3 attempts; the second clone-vm ran once because the fake adapter's
        # counter advanced past its fail budget.
        clone_a = [op for op in result.per_op.values()
                   if op.op_id.endswith("clone-vm[h-a]") or op.op_id.endswith("clone-vm[h-b]")]
        attempts = sum(op.attempts for op in clone_a)
        assert attempts >= 4, f"expected >=4 total clone attempts; got {attempts}"
        store.close()


def test_op_terminal_failure_halts_subtree(slice_plan):
    plan, ctx = slice_plan
    with tempfile.TemporaryDirectory() as tmp:
        store = _store_in(Path(tmp))
        adapter = _HardFailAdapter("clone-vm")
        store.create_deploy(
            deploy_id="d4",
            scenario_id=plan.scenario_id,
            scenario_version=plan.scenario_version,
            scenario_schema_version=1,
            plan_id=plan.plan_id,
            ir_hash=plan.ir_hash,
            placement=plan.placement,
        )
        ex = Executor(
            plan, store, adapter, ctx, config_backend=adapter, deploy_id="d4",
            retry=RetryConfig(max_attempts=2, initial_backoff_s=0.001, cap_s=0.001, jitter_pct=0.0),
            sleep_fn=lambda _: None,
        )
        ex.persist_plan()
        result = ex.run()

        # Both clone-vm ops failed (HardFail hits every clone-vm call).
        failed = [
            op for op in result.per_op.values()
            if op.final_status == OpStatus.FAILED
        ]
        assert len(failed) >= 2

        # Every host-side op downstream of clone is halted.
        halted = [
            op for op in result.per_op.values()
            if op.final_status == OpStatus.HALTED
        ]
        assert len(halted) > 0
        # The network-side ops (no host scope) should still complete.
        net_completed = [
            op for op in result.per_op.values()
            if any(name in op.op_id for name in (
                "create-sdn-zone", "create-ovs-bridge[slice-demo]",
                "create-vnet[corp]", "apply-sdn-config",
                "network-ready[corp]",
            ))
            and op.final_status == OpStatus.COMPLETED
        ]
        assert len(net_completed) >= 5
        store.close()


# ---------------------------------------------------------------------------
# Barriers
# ---------------------------------------------------------------------------


def test_barriers_make_no_adapter_calls(slice_plan):
    plan, ctx = slice_plan
    with tempfile.TemporaryDirectory() as tmp:
        store = _store_in(Path(tmp))
        adapter = _FakeAdapter()
        store.create_deploy(
            deploy_id="d5",
            scenario_id=plan.scenario_id,
            scenario_version=plan.scenario_version,
            scenario_schema_version=1,
            plan_id=plan.plan_id,
            ir_hash=plan.ir_hash,
            placement=plan.placement,
        )
        ex = Executor(plan, store, adapter, ctx, config_backend=adapter, deploy_id="d5",
                      sleep_fn=lambda _: None)
        ex.persist_plan()
        ex.run()
        store.close()

        # Barriers are kind="barrier"; they should not appear in adapter.calls.
        called_names = {n for n, _ in adapter.calls}
        for n in ("network-ready", "host-ready", "mirror-ready"):
            assert n not in called_names


# ---------------------------------------------------------------------------
# Resume semantics
# ---------------------------------------------------------------------------


def test_resume_skips_completed_ops(slice_plan):
    """On a second Executor.run() against the same deploy_id, ops already
    marked completed in the state DB are not re-dispatched to the adapter."""
    plan, ctx = slice_plan
    with tempfile.TemporaryDirectory() as tmp:
        store = _store_in(Path(tmp))
        adapter1 = _FakeAdapter()
        store.create_deploy(
            deploy_id="resume-1",
            scenario_id=plan.scenario_id,
            scenario_version=plan.scenario_version,
            scenario_schema_version=1,
            plan_id=plan.plan_id,
            ir_hash=plan.ir_hash,
            placement=plan.placement,
        )
        ex1 = Executor(plan, store, adapter1, ctx, config_backend=adapter1, deploy_id="resume-1",
                       sleep_fn=lambda _: None)
        ex1.persist_plan()
        result1 = ex1.run()
        assert result1.succeeded
        call_count_1 = len(adapter1.calls)
        assert call_count_1 > 0

        # Second run with a fresh adapter against the same store.
        adapter2 = _FakeAdapter()
        ex2 = Executor(plan, store, adapter2, ctx, config_backend=adapter2, deploy_id="resume-1",
                       sleep_fn=lambda _: None)
        ex2.persist_plan()  # INSERT OR IGNORE keeps existing rows
        result2 = ex2.run()
        store.close()

        # Every op already completed; adapter2 should not be called at all.
        assert adapter2.calls == []
        assert result2.succeeded
        # Every per_op outcome should report attempts=0 (no work done).
        for outcome in result2.per_op.values():
            assert outcome.attempts == 0
            assert outcome.final_status == OpStatus.COMPLETED


def test_resume_after_partial_failure_only_retries_failed(slice_plan):
    """Op marked failed + downstream marked halted in run 1; run 2 with a
    healthy adapter completes the deploy by re-running only the failed
    subtree."""
    plan, ctx = slice_plan
    with tempfile.TemporaryDirectory() as tmp:
        store = _store_in(Path(tmp))

        # Run 1: hard-fail wait-os-ready (late in the topo, so most ops
        # complete and only a small subtree halts).
        adapter1 = _HardFailAdapter("wait-os-ready")
        store.create_deploy(
            deploy_id="resume-2",
            scenario_id=plan.scenario_id,
            scenario_version=plan.scenario_version,
            scenario_schema_version=1,
            plan_id=plan.plan_id,
            ir_hash=plan.ir_hash,
            placement=plan.placement,
        )
        ex1 = Executor(plan, store, adapter1, ctx, config_backend=adapter1, deploy_id="resume-2",
                       retry=RetryConfig(max_attempts=1, initial_backoff_s=0.001,
                                         cap_s=0.001, jitter_pct=0.0),
                       sleep_fn=lambda _: None)
        ex1.persist_plan()
        result1 = ex1.run()
        assert not result1.succeeded
        # At least one op failed.
        failed_ops_1 = [
            o for o in result1.per_op.values()
            if o.final_status == OpStatus.FAILED
        ]
        assert len(failed_ops_1) >= 1
        completed_count_1 = sum(
            1 for o in result1.per_op.values()
            if o.final_status == OpStatus.COMPLETED
        )

        # CLI's resume path: reset non-completed ops back to pending.
        n_reset = store.reset_incomplete_ops("resume-2")
        assert n_reset >= 1

        # Run 2: healthy adapter.
        adapter2 = _FakeAdapter()
        ex2 = Executor(plan, store, adapter2, ctx, config_backend=adapter2, deploy_id="resume-2",
                       sleep_fn=lambda _: None)
        ex2.persist_plan()
        result2 = ex2.run()
        store.close()

        assert result2.succeeded
        # Adapter2 should NOT have re-run any op that was already completed
        # in run 1 — those count as zero in result1.per_op[*].attempts but
        # the call count of adapter2 is the more direct check.
        called_op_names_run2 = [n for n, _ in adapter2.calls]
        # Number of distinct ops adapter2 ran <= total non-completed ops
        # from run 1. (Strict equality not asserted because retry could
        # re-trigger via wait ops; just verify the savings.)
        assert len(called_op_names_run2) < (
            len(plan.ops)  # total action/wait ops
        ), (
            f"resume should skip prior completed ops; adapter2 ran "
            f"{len(called_op_names_run2)} ops vs {len(plan.ops)} total"
        )
        # Specifically: a completed-on-run-1 op like create-sdn-zone must
        # not have been re-called.
        if completed_count_1 > 0:
            assert "create-sdn-zone" not in called_op_names_run2


# ---------------------------------------------------------------------------
# GAP-044 / LES-157 — a re-applied deploy's persisted op INPUTS must catch up
# to the current in-memory plan the moment an op actually executes, not stay
# frozen at whatever the deploy's first-ever apply wrote.
# ---------------------------------------------------------------------------


def _op_id_for(plan, op_name: str, host: str) -> str:
    return next(
        op.op_id for op in plan.ops.values()
        if op.op_name == op_name and op.scope.get("host") == host
    )


def test_reapply_of_a_force_reset_op_persists_the_new_inputs(slice_plan):
    """A stable-scope op (`configure-cloud-init[h-a]`, same op_id no matter
    what its content is -- the SAME pattern the now-retired `record-tap-
    intent`/A2-only `install-ovs-mirror` follow, `docs/design/
    06-taps-sensors.md`) is force-reset back to PENDING after its owning
    plan's content changes (the binding's cloud-init user changed). Looked
    up dynamically (not a hardcoded plan-hash-prefixed literal, LES-072's
    staleness trap applied to a TEST id, not just a tap-plan's `target:`) --
    `plan.plan_id` is ir_hash-derived, so a literal `"slice-demo-<hash>:..."`
    silently drifts stale the moment the fixture IR's CONTENT changes for
    ANY reason, tap-related or not (hit for real during this file's own
    2026-07-28 migration). Re-running it must persist the NEW inputs, not
    the ones its first-ever apply wrote -- the GAP-044 regression."""
    plan, ctx = slice_plan
    op_id = _op_id_for(plan, "configure-cloud-init", "h-a")

    with tempfile.TemporaryDirectory() as tmp:
        store = _store_in(Path(tmp))
        store.create_deploy(
            deploy_id="reapply-1",
            scenario_id=plan.scenario_id,
            scenario_version=plan.scenario_version,
            scenario_schema_version=1,
            plan_id=plan.plan_id,
            ir_hash=plan.ir_hash,
            placement=plan.placement,
        )

        # First apply, with the op's ORIGINAL inputs (no ci_user override).
        adapter1 = _FakeAdapter()
        ex1 = Executor(plan, store, adapter1, ctx, config_backend=adapter1,
                       deploy_id="reapply-1", sleep_fn=lambda _: None)
        ex1.persist_plan()
        result1 = ex1.run()
        assert result1.succeeded

        row1 = store.get_op("reapply-1", op_id)
        original_ci_user = json.loads(row1.inputs_resolved_json)["ci_user"]
        assert original_ci_user != "newadmin"

        # A DIFFERENT op (already completed, NOT force-reset) -- the
        # complement: it must retain the inputs it genuinely ran with.
        untouched_op_id = _op_id_for(plan, "configure-cloud-init", "h-b")
        untouched_before = store.get_op("reapply-1", untouched_op_id).inputs_resolved_json

        # The plan's content changes -- the binding now names a ci_user for
        # h-a -- mutating the SAME in-memory OpInstance the way a second
        # `build_plan` call against a changed IR would produce a fresh one
        # with new content (op_id is scope-derived, not inputs-derived --
        # LES-030 -- so the id here is unchanged, exactly why a stable-scope
        # op needs force-reset to re-run at all).
        plan.ops[op_id].inputs_resolved["ci_user"] = "newadmin"
        store.force_reset_ops("reapply-1", [op_id])
        assert store.get_op("reapply-1", op_id).status == OpStatus.PENDING

        # Re-apply: only the force-reset op re-runs.
        adapter2 = _FakeAdapter()
        ex2 = Executor(plan, store, adapter2, ctx, config_backend=adapter2,
                       deploy_id="reapply-1", sleep_fn=lambda _: None)
        ex2.persist_plan()
        result2 = ex2.run()
        assert result2.succeeded

        # The adapter itself was already called with the NEW value (the wire
        # was never wrong) -- confirm that, then confirm the STORE now
        # agrees.
        [resolved] = [
            inputs for name, inputs in adapter2.calls
            if name == "configure-cloud-init" and inputs.get("node_id") == "h-a"
        ]
        assert resolved["ci_user"] == "newadmin"

        row2 = store.get_op("reapply-1", op_id)
        assert json.loads(row2.inputs_resolved_json)["ci_user"] == "newadmin", (
            "persisted inputs_resolved_json must reflect the RE-APPLIED "
            "plan, not the deploy's first-ever apply (GAP-044)"
        )

        # Complement: the op that was NEVER force-reset (a legitimate resume
        # skip) must retain the inputs it genuinely ran with, untouched.
        untouched_after = store.get_op("reapply-1", untouched_op_id).inputs_resolved_json
        assert untouched_after == untouched_before
        store.close()


def test_reapplied_op_that_ultimately_fails_still_persists_the_new_attempted_inputs(
    slice_plan,
):
    """Complement to the happy-path re-apply test above: an op that is
    force-reset to PENDING and then FAILS (adapter error, retries exhausted)
    must still have its persisted `inputs_resolved_json` reflect what it was
    ACTUALLY attempted with on this re-apply, not the stale value from a
    prior successful apply. `set_op_running` (called before any attempt, in
    `_run_one`) is what makes this honest — the inputs refresh does not wait
    on the op's eventual success/failure."""
    plan, ctx = slice_plan
    op_id = _op_id_for(plan, "configure-cloud-init", "h-a")

    with tempfile.TemporaryDirectory() as tmp:
        store = _store_in(Path(tmp))
        store.create_deploy(
            deploy_id="reapply-fail-1",
            scenario_id=plan.scenario_id,
            scenario_version=plan.scenario_version,
            scenario_schema_version=1,
            plan_id=plan.plan_id,
            ir_hash=plan.ir_hash,
            placement=plan.placement,
        )

        adapter1 = _FakeAdapter()
        ex1 = Executor(plan, store, adapter1, ctx, config_backend=adapter1,
                       deploy_id="reapply-fail-1", sleep_fn=lambda _: None)
        ex1.persist_plan()
        result1 = ex1.run()
        assert result1.succeeded

        row1 = store.get_op("reapply-fail-1", op_id)
        assert json.loads(row1.inputs_resolved_json)["ci_user"] != "newadmin"

        # New content bound for this op, and it is force-reset to re-run --
        # but this time the adapter call for it will hard-fail.
        plan.ops[op_id].inputs_resolved["ci_user"] = "newadmin"
        store.force_reset_ops("reapply-fail-1", [op_id])
        assert store.get_op("reapply-fail-1", op_id).status == OpStatus.PENDING

        adapter2 = _HardFailAdapter(hard_fail_op="configure-cloud-init")
        ex2 = Executor(plan, store, adapter2, ctx, config_backend=adapter2,
                       deploy_id="reapply-fail-1", sleep_fn=lambda _: None,
                       retry=RetryConfig(max_attempts=2, initial_backoff_s=0))
        ex2.persist_plan()
        result2 = ex2.run()
        assert not result2.succeeded

        row2 = store.get_op("reapply-fail-1", op_id)
        assert row2.status == OpStatus.FAILED
        assert json.loads(row2.inputs_resolved_json)["ci_user"] == "newadmin", (
            "a FAILED op must honestly record what it was ATTEMPTED with on "
            "this re-apply, not a prior successful apply's stale inputs"
        )
        store.close()


# ---------------------------------------------------------------------------
# Cross-process correctness locks (PLANNER §8.4)
# ---------------------------------------------------------------------------


class _LockSnoopAdapter(_FakeAdapter):
    """Records which resource locks are held (and by whom) at the moment each
    op runs — so a test can assert an op holds its own correctness locks."""

    def __init__(self, store, deploy_id):
        super().__init__()
        self._store = store
        self._deploy_id = deploy_id
        self.held_during: dict[str, set[str]] = {}

    def run(self, op, resolved_inputs, ctx):
        self.held_during[op.op_name] = {
            r.resource_key
            for r in self._store.held_resource_locks(self._deploy_id)
            if r.op_id == op.op_id
        }
        return super().run(op, resolved_inputs, ctx)


def test_op_holds_its_correctness_locks_and_releases_them(slice_plan):
    plan, ctx = slice_plan
    with tempfile.TemporaryDirectory() as tmp:
        store = _store_in(Path(tmp))
        store.create_deploy(
            deploy_id="lk1", scenario_id=plan.scenario_id,
            scenario_version=plan.scenario_version, scenario_schema_version=1,
            plan_id=plan.plan_id, ir_hash=plan.ir_hash, placement=plan.placement,
        )
        adapter = _LockSnoopAdapter(store, "lk1")
        ex = Executor(plan, store, adapter, ctx, config_backend=adapter, deploy_id="lk1",
                      sleep_fn=lambda _: None)
        ex.persist_plan()
        result = ex.run()
        assert result.succeeded

        # vm-mutating op held vm:<vmid> (vmid=100123 from the fake clone).
        assert "vm:100123" in adapter.held_during["configure-vm-hardware"]
        # clone-vm uses only throughput categories -> no correctness lock held.
        assert adapter.held_during["clone-vm"] == set()

        # Every lock released once the deploy finished.
        assert store.held_resource_locks() == []
        store.close()


def test_op_fails_when_a_correctness_lock_is_unavailable(slice_plan):
    """A foreign deploy squats on vm:100123 with a long TTL; the executor's
    configure-vm-hardware op blocks, times out, and fails -> its subtree halts.
    Independent network-side ops still complete."""
    plan, ctx = slice_plan
    with tempfile.TemporaryDirectory() as tmp:
        store = _store_in(Path(tmp))
        store.create_deploy(
            deploy_id="lk2", scenario_id=plan.scenario_id,
            scenario_version=plan.scenario_version, scenario_schema_version=1,
            plan_id=plan.plan_id, ir_hash=plan.ir_hash, placement=plan.placement,
        )
        # Foreign holder of the vmid the fake clone will mint.
        store.acquire_resource_locks(
            ["vm:100123"], "other-deploy", "other-op", ttl_s=10_000.0
        )
        adapter = _FakeAdapter()
        ex = Executor(
            plan, store, adapter, ctx, config_backend=adapter, deploy_id="lk2",
            sleep_fn=lambda _: None,
            lock_poll_s=1.0, lock_timeout_s=3.0,
        )
        ex.persist_plan()
        result = ex.run()
        store.close()

        assert not result.succeeded
        # configure-vm-hardware never reached the adapter (blocked on the lock).
        assert "configure-vm-hardware" not in {n for n, _ in adapter.calls}
        # At least one op failed with the lock-timeout message.
        lock_fails = [
            o for o in result.per_op.values()
            if o.error_message and "resource lock acquisition timed out" in o.error_message
        ]
        assert lock_fails
        # Network-side ops (no vm: dependency) still completed.
        assert any(
            "create-sdn-zone" in o.op_id and o.final_status == OpStatus.COMPLETED
            for o in result.per_op.values()
        )


def test_resource_locks_can_be_disabled(slice_plan):
    """With resource_locks=False the executor takes no DB locks (the unit-test
    / sole-writer path)."""
    plan, ctx = slice_plan
    with tempfile.TemporaryDirectory() as tmp:
        store = _store_in(Path(tmp))
        store.create_deploy(
            deploy_id="lk3", scenario_id=plan.scenario_id,
            scenario_version=plan.scenario_version, scenario_schema_version=1,
            plan_id=plan.plan_id, ir_hash=plan.ir_hash, placement=plan.placement,
        )
        # Even a foreign squatter on vm:100123 is ignored when locks are off.
        store.acquire_resource_locks(["vm:100123"], "other", "op", ttl_s=10_000.0)
        adapter = _FakeAdapter()
        ex = Executor(plan, store, adapter, ctx, config_backend=adapter, deploy_id="lk3",
                      sleep_fn=lambda _: None, resource_locks=False)
        ex.persist_plan()
        result = ex.run()
        store.close()
        assert result.succeeded
        assert "configure-vm-hardware" in {n for n, _ in adapter.calls}


def test_resume_loads_completed_op_outputs_for_downstream(slice_plan):
    """Outputs of completed ops should be available to downstream consumers
    via the in-process cache after _prime_completed_outputs."""
    plan, ctx = slice_plan
    with tempfile.TemporaryDirectory() as tmp:
        store = _store_in(Path(tmp))
        adapter = _FakeAdapter()
        store.create_deploy(
            deploy_id="resume-3",
            scenario_id=plan.scenario_id,
            scenario_version=plan.scenario_version,
            scenario_schema_version=1,
            plan_id=plan.plan_id,
            ir_hash=plan.ir_hash,
            placement=plan.placement,
        )
        ex1 = Executor(plan, store, adapter, ctx, config_backend=adapter, deploy_id="resume-3",
                       sleep_fn=lambda _: None)
        ex1.persist_plan()
        ex1.run()

        # Inspect a second executor's _outputs after priming.
        adapter2 = _FakeAdapter()
        ex2 = Executor(plan, store, adapter2, ctx, config_backend=adapter2, deploy_id="resume-3",
                       sleep_fn=lambda _: None)
        ex2._prime_completed_outputs()
        store.close()

        # Should have loaded outputs for clone-vm (which produced vmid=100123).
        clone_outputs = [
            outputs for op_id, outputs in ex2._outputs.items()
            if "clone-vm" in op_id
        ]
        assert len(clone_outputs) >= 1
        assert all(o.get("vmid") == 100123 for o in clone_outputs)


# ---------------------------------------------------------------------------
# CRITICAL regression: composite (deploy_id, op_id) PK (code-review finding
# #1, 2026-07-07). Two deploys of the SAME plan (identical op_ids — the
# `--fresh` shape when the state DB isn't cleared) must each actually run
# every op, not alias onto a prior deploy's `completed` rows.
# ---------------------------------------------------------------------------


def test_second_deploy_of_same_plan_actually_runs_every_op(slice_plan):
    plan, ctx = slice_plan
    with tempfile.TemporaryDirectory() as tmp:
        store = _store_in(Path(tmp))

        adapter_a = _FakeAdapter()
        store.create_deploy(
            deploy_id="dep-a", scenario_id=plan.scenario_id,
            scenario_version=plan.scenario_version, scenario_schema_version=1,
            plan_id=plan.plan_id, ir_hash=plan.ir_hash, placement=plan.placement,
        )
        ex_a = Executor(plan, store, adapter_a, ctx, config_backend=adapter_a,
                        deploy_id="dep-a", sleep_fn=lambda _: None)
        ex_a.persist_plan()
        result_a = ex_a.run()
        assert result_a.succeeded
        assert len(adapter_a.calls) > 0

        # Same plan (identical op_ids) under a NEW deploy_id, state DB intact
        # — exactly the `--fresh` shape.
        adapter_b = _FakeAdapter()
        store.create_deploy(
            deploy_id="dep-b", scenario_id=plan.scenario_id,
            scenario_version=plan.scenario_version, scenario_schema_version=1,
            plan_id=plan.plan_id, ir_hash=plan.ir_hash, placement=plan.placement,
        )
        ex_b = Executor(plan, store, adapter_b, ctx, config_backend=adapter_b,
                        deploy_id="dep-b", sleep_fn=lambda _: None)
        ex_b.persist_plan()
        result_b = ex_b.run()
        store.close()

        assert result_b.succeeded
        # The critical assertion: deploy B ran every op itself — it did not
        # alias deploy A's already-`completed` rows and skip the work.
        assert {n for n, _ in adapter_b.calls} == {n for n, _ in adapter_a.calls}
        assert len(adapter_b.calls) == len(adapter_a.calls) > 0


# ---------------------------------------------------------------------------
# MEDIUM regression: lost lease/lock renewal aborts the deploy (finding #2).
# ---------------------------------------------------------------------------


def test_lost_event_aborts_run_between_ops(slice_plan):
    """A `lost_event` set before run() starts must be observed at the very
    first op — nothing gets dispatched, and every op ends up either HALTED
    (aborted) or, if it was already completed in a prior run, COMPLETED."""
    plan, ctx = slice_plan
    with tempfile.TemporaryDirectory() as tmp:
        store = _store_in(Path(tmp))
        adapter = _FakeAdapter()
        store.create_deploy(
            deploy_id="lost-1", scenario_id=plan.scenario_id,
            scenario_version=plan.scenario_version, scenario_schema_version=1,
            plan_id=plan.plan_id, ir_hash=plan.ir_hash, placement=plan.placement,
        )
        lost = threading.Event()
        lost.set()  # already lost before run() even starts
        ex = Executor(plan, store, adapter, ctx, config_backend=adapter,
                      deploy_id="lost-1", sleep_fn=lambda _: None, lost_event=lost)
        ex.persist_plan()
        result = ex.run()
        store.close()

        assert not result.succeeded
        assert adapter.calls == []  # never dispatched a single op
        assert all(
            o.final_status == OpStatus.HALTED for o in result.per_op.values()
        )
        assert all(
            o.error_message and "lease/lock renewal lost" in o.error_message
            for o in result.per_op.values()
        )


def test_lost_event_set_mid_run_stops_further_dispatch(slice_plan):
    """`lost_event` set by the FakeAdapter itself partway through a run
    (simulating a heartbeat firing it concurrently) stops all further
    adapter calls from that point on."""
    plan, ctx = slice_plan
    with tempfile.TemporaryDirectory() as tmp:
        store = _store_in(Path(tmp))
        lost = threading.Event()

        class _TriggerAfterN(_FakeAdapter):
            def __init__(self, n):
                super().__init__()
                self.n = n

            def run(self, op, resolved_inputs, ctx):
                if len(self.calls) >= self.n:
                    lost.set()
                return super().run(op, resolved_inputs, ctx)

        adapter = _TriggerAfterN(2)
        store.create_deploy(
            deploy_id="lost-2", scenario_id=plan.scenario_id,
            scenario_version=plan.scenario_version, scenario_schema_version=1,
            plan_id=plan.plan_id, ir_hash=plan.ir_hash, placement=plan.placement,
        )
        ex = Executor(plan, store, adapter, ctx, config_backend=adapter,
                      deploy_id="lost-2", sleep_fn=lambda _: None, lost_event=lost)
        ex.persist_plan()
        result = ex.run()
        store.close()

        assert not result.succeeded
        # Stopped well short of running every op in the plan.
        assert 0 < len(adapter.calls) < len(plan.ops)
        assert any(
            o.final_status == OpStatus.HALTED
            and o.error_message and "lease/lock renewal lost" in o.error_message
            for o in result.per_op.values()
        )


# ---------------------------------------------------------------------------
# MEDIUM regression: an unresolvable correctness-lock $var fails the op
# cleanly instead of crashing the executor (finding #3).
# ---------------------------------------------------------------------------


def test_op_with_unresolvable_lock_var_fails_cleanly_no_crash():
    op = OpInstance(
        op_id="p:bad-op[x]",
        op_name="bad-op",
        op_catalog_id="OP-X-999",
        kind=OpKind.ACTION,
        scope={},                            # no `vmid` anywhere in scope...
        inputs_resolved={},                  # ...or resolved inputs...
        resource_categories=("vm:$vmid",),   # ...but the category needs $vmid.
        cleanup_strategy=CleanupStrategy.REDO,
        idempotency_class=IdempotencyClass.INFRA,
    )
    plan = Plan(
        plan_id="p", scenario_id="s", scenario_version=1, ir_hash="h",
        ops={op.op_id: op},
    )
    plan.freeze()
    ctx = ClusterContext.single_node("node-1")

    with tempfile.TemporaryDirectory() as tmp:
        store = _store_in(Path(tmp))
        store.create_deploy(
            deploy_id="lockbad", scenario_id="s", scenario_version=1,
            scenario_schema_version=1, plan_id="p", ir_hash="h", placement={},
        )
        adapter = _FakeAdapter()
        ex = Executor(plan, store, adapter, ctx, config_backend=adapter,
                      deploy_id="lockbad", sleep_fn=lambda _: None)
        ex.persist_plan()
        result = ex.run()  # must NOT raise LockResolutionError

        assert not result.succeeded
        outcome = result.per_op[op.op_id]
        assert outcome.final_status == OpStatus.FAILED
        assert "cannot resolve" in (outcome.error_message or "")
        assert adapter.calls == []  # failed before ever reaching the adapter
        # The op reached a terminal DB state, not stuck `running`.
        row = store.get_op("lockbad", op.op_id)
        assert row.status == OpStatus.FAILED
        store.close()
