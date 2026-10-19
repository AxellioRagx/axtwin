"""Sequential executor for the first slice (docs/ADAPTER.md §6).

What the slice executor does:

  - Walks Plan.topological_order() one op at a time (no concurrency).
  - For each op:
      - Barriers: mark completed in one tx; no adapter call.
      - Actions/waits: resolve inputs from prior outputs, call adapter.run(),
        record attempt, retry up to max_attempts with exponential backoff on
        AdapterError. cleanup_strategy `escalate` is treated as `redo`
        for the slice (no escalate ops are in scope — see ADAPTER.md §6).
  - On terminal failure of any op:
      - Default halt-scope=subtree: mark every transitive successor
        `halted-due-to-upstream` and skip them.
      - Independent subtrees continue.
  - Final result: a DeployResult with per-op summary; the state store carries
    the authoritative per-attempt detail.

Cross-process correctness locks (docs/PLANNER.md §8.4, ratified 2026-06-03):
before running an op the executor acquires DB-backed locks on the op's
"correctness" resource categories (`ovs:<node>`, `vm:<vmid>`, `gretap:<sensor>`)
and releases them when the op reaches a terminal state. This serializes those
resources across *concurrent deploy processes* — the per-process semaphores of
§8.1–§8.3 cannot. The executor stays internally **sequential**; a parallel
intra-deploy executor + tuned throughput semaphores are a separate later slice.

What the slice executor does NOT do (deferred to later slices,
docs/PLANNER.md §8, §9):

  - Per-process throughput semaphores (`proxmox-cluster`, `proxmox-node:*`,
    `ansible-pool`, the global cap) — these are tuned numbers, not correctness.
  - Parallel intra-deploy execution.
  - Plan-diff / re-planning.
  - Escalate walk-back path.
"""
from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from axrange.ops.registry import IdempotencyClass
from axrange.planner.cluster import ClusterContext
from axrange.planner.dag import BarrierInstance, OpInstance, Plan
from axrange.planner.locks import (
    LockResolutionError,
    acquire_blocking,
    correctness_lock_keys,
)
from axrange.planner.state import (
    DEFAULT_LEASE_TTL_S,
    AttemptResult,
    DeployStatus,
    OpStatus,
    StateStore,
    barrier_to_row,
    op_to_row,
)


# ---------------------------------------------------------------------------
# Adapter protocol
# ---------------------------------------------------------------------------


class AdapterError(Exception):
    """Raised by an adapter when an op attempt fails. Retried unless terminal."""

    def __init__(self, message: str, *, error_class: str = "adapter-error") -> None:
        super().__init__(message)
        self.error_class = error_class


class AdapterTimeout(AdapterError):
    """Raised when a wait op's poll loop exhausts its per-attempt timeout."""

    def __init__(self, message: str) -> None:
        super().__init__(message, error_class="adapter-timeout")


@dataclass
class ExecutionContext:
    """Per-op context handed to the adapter at run time."""

    plan_id: str
    deploy_id: str
    cluster: ClusterContext
    placement: dict[str, str]
    # Per-attempt timeout in seconds (from the op's `timeout` spec).
    timeout_s: int | None = None
    # Resource-ownership tier tag (`docs/CONVENTIONS.md` §2), copied from
    # `Plan.tier` — the adapter's naming calls (`adapter_proxmox/naming.py`)
    # key off this to emit `te_*` vs `o2_*` name shapes. Default `"te"`.
    tier: str = "te"


class Adapter(Protocol):
    """The one interface docs/ADAPTER.md §2.1 says the planner sees.

    Implementations internally route to REST or SSH backends based on op type
    (see axrange.adapter_proxmox.adapter.ProxmoxAdapter).
    """

    def run(
        self,
        op: OpInstance,
        resolved_inputs: dict[str, Any],
        ctx: ExecutionContext,
    ) -> dict[str, Any]:
        """Execute one attempt of `op` with all inputs resolved.

        Returns the op's typed outputs (matching OpDefinition.outputs).
        Raises AdapterError on failure (the executor decides whether to retry).
        """


# ---------------------------------------------------------------------------
# Outcome + Result types
# ---------------------------------------------------------------------------


@dataclass
class OpAttemptOutcome:
    op_id: str
    final_status: OpStatus
    attempts: int
    error_message: str | None = None
    outputs: dict[str, Any] | None = None


@dataclass
class DeployResult:
    deploy_id: str
    plan_id: str
    final_status: DeployStatus
    per_op: dict[str, OpAttemptOutcome] = field(default_factory=dict)

    @property
    def succeeded(self) -> bool:
        return self.final_status == DeployStatus.COMPLETED


class DeployHalted(Exception):
    """Raised when the deploy ends with at least one failed op.

    The executor still returns a DeployResult so the CLI can render the tree;
    this exception is for callers that want a hard signal. The CLI catches
    it and exits non-zero.
    """

    def __init__(self, result: DeployResult) -> None:
        super().__init__(f"deploy {result.deploy_id} halted")
        self.result = result


# ---------------------------------------------------------------------------
# Retry config
# ---------------------------------------------------------------------------


@dataclass
class RetryConfig:
    """PLANNER.md §5.1 defaults: 3 attempts; exp 5s -> 15s -> 45s (3x; ±10% jitter)."""

    max_attempts: int = 3
    initial_backoff_s: float = 5.0
    multiplier: float = 3.0
    cap_s: float = 45.0
    jitter_pct: float = 0.10

    def delay_for(self, attempt_no: int) -> float:
        """attempt_no is 1-indexed; delay BEFORE the attempt. Attempt 1 has no delay."""
        if attempt_no <= 1:
            return 0.0
        # Geometric, capped, with ±jitter_pct.
        base = min(
            self.initial_backoff_s * (self.multiplier ** (attempt_no - 2)),
            self.cap_s,
        )
        jitter = base * self.jitter_pct
        return max(0.0, base + random.uniform(-jitter, jitter))


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------


class Executor:
    """Sequential executor over a Plan.

    Constructed once per deploy. `run()` is one-shot.
    """

    def __init__(
        self,
        plan: Plan,
        store: StateStore,
        adapter: Adapter,
        cluster: ClusterContext,
        *,
        deploy_id: str,
        config_backend: Adapter | None = None,
        retry: RetryConfig | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
        resource_locks: bool = True,
        lock_ttl_s: float = DEFAULT_LEASE_TTL_S,
        lock_poll_s: float = 1.0,
        lock_timeout_s: float = 600.0,
        lost_event: threading.Event | None = None,
    ) -> None:
        self.plan = plan
        self.store = store
        self.adapter = adapter
        # config-class ops route here (axrange.config_exec.ConfigBackend); infra
        # ops stay on `adapter`. Routed by op.idempotency_class in _backend_for.
        self.config_backend = config_backend
        self.cluster = cluster
        self.deploy_id = deploy_id
        self.retry = retry or RetryConfig()
        self._sleep = sleep_fn
        # Cross-process correctness locks (PLANNER §8.4). On by default; a
        # caller that knows it is the sole writer (or a unit test) can disable.
        # A deploy's lease + lock heartbeat is driven by the CLI (locks.Heartbeat).
        self._resource_locks = resource_locks
        self._lock_ttl_s = lock_ttl_s
        self._lock_poll_s = lock_poll_s
        self._lock_timeout_s = lock_timeout_s
        # Set by a `locks.Heartbeat` (constructed by the caller — CLI/API,
        # not this module) when a lease/lock renewal is lost, i.e. another
        # process may have taken over as the scenario's writer. Polled
        # between ops (not mid-op) so this process stops issuing further
        # adapter calls rather than continuing as a phantom writer.
        self._lost_event = lost_event
        # Outputs collected in-process so wait/action ops downstream don't have
        # to round-trip SQLite. The state store remains authoritative.
        self._outputs: dict[str, dict[str, Any]] = {}
        # op_ids already `completed` as of the start of this run() — computed
        # once in `_prime_completed_outputs` so the main loop doesn't issue a
        # `get_op` round-trip per op just to check status (dedup: this is now
        # the one source of truth for "already done", replacing a second
        # per-op DB read that used to shadow it).
        self._completed_ids: set[str] = set()

    # --- Setup --------------------------------------------------------------

    def persist_plan(self) -> None:
        """Insert one row per op + barrier with status=pending. Idempotent only
        on first call; rerunning would conflict on the PK. Callers wrap in a
        try/except for resume semantics in a later slice."""
        ir = self.plan
        op_rows = [op_to_row(op, self.deploy_id) for op in ir.ops.values()]
        b_rows = [barrier_to_row(b, self.deploy_id) for b in ir.barriers.values()]
        self.store.insert_ops(self.deploy_id, op_rows + b_rows)

    # --- Run ----------------------------------------------------------------

    def run(self) -> DeployResult:
        """Execute the plan to completion or terminal failure.

        Resume-safe: any op already marked `completed` in the state store is
        skipped (its outputs are loaded into the in-process cache for
        downstream consumers, and it's recorded in the result tree as
        already-done). Non-completed ops were reset to `pending` by the
        caller via `StateStore.reset_incomplete_ops` before this method ran.
        """
        result = DeployResult(
            deploy_id=self.deploy_id,
            plan_id=self.plan.plan_id,
            final_status=DeployStatus.RUNNING,
        )
        halted: set[str] = set()
        order = self.plan.topological_order()

        # Pre-load already-completed ops so downstream consumers don't
        # round-trip the DB inside `_lookup_output`, and so the resume
        # short-circuit below is an in-process set lookup, not a query.
        self._prime_completed_outputs()

        lost_for_lock_loss = False
        for idx, op_id in enumerate(order):
            if self._lost_event is not None and self._lost_event.is_set():
                # Lease/lock renewal was lost (locks.Heartbeat) — another
                # process may now be the scenario's writer. Stop issuing
                # adapter calls; halt everything not yet done rather than
                # continue as a phantom writer racing the new holder.
                lost_for_lock_loss = True
                self._abort_remaining(order[idx:], result)
                break

            if op_id in halted:
                self._halt_one(op_id, result)
                continue

            # Resume short-circuit: if this op completed in a prior run,
            # record it as already-done and skip the adapter call.
            if op_id in self._completed_ids:
                outputs = self._outputs.get(op_id, {})
                result.per_op[op_id] = OpAttemptOutcome(
                    op_id=op_id,
                    final_status=OpStatus.COMPLETED,
                    attempts=0,
                    outputs=outputs,
                )
                continue

            node = self.plan.get(op_id)
            if node is None:
                continue

            if isinstance(node, BarrierInstance):
                self._complete_barrier(node, result)
                continue

            assert isinstance(node, OpInstance)
            outcome = self._run_one(node)
            result.per_op[op_id] = outcome
            if outcome.final_status == OpStatus.FAILED:
                # Halt every transitive successor.
                for desc_id in self.plan.descendants(op_id):
                    halted.add(desc_id)

        any_failed = lost_for_lock_loss or any(
            o.final_status == OpStatus.FAILED for o in result.per_op.values()
        )
        result.final_status = (
            DeployStatus.FAILED if any_failed else DeployStatus.COMPLETED
        )
        self.store.update_deploy_status(self.deploy_id, result.final_status)
        return result

    def _prime_completed_outputs(self) -> None:
        """Pull outputs for every already-completed op in this deploy into
        the in-process cache, and record which op_ids are completed. Avoids
        per-op DB lookups during input resolution AND during the main loop's
        resume short-circuit."""
        import json

        for row in self.store.ops_for_deploy(self.deploy_id):
            if row.status != OpStatus.COMPLETED:
                continue
            self._completed_ids.add(row.op_id)
            if not row.outputs_json:
                continue
            try:
                data = json.loads(row.outputs_json)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict):
                self._outputs[row.op_id] = data

    def _abort_remaining(self, remaining_ids: list[str], result: DeployResult) -> None:
        """Mark every not-yet-visited op HALTED (already-completed ones stay
        COMPLETED) without touching the adapter or the cluster — used when
        `_lost_event` fires mid-run (see `run()`)."""
        for op_id in remaining_ids:
            if op_id in result.per_op:
                continue
            if op_id in self._completed_ids:
                result.per_op[op_id] = OpAttemptOutcome(
                    op_id=op_id,
                    final_status=OpStatus.COMPLETED,
                    attempts=0,
                    outputs=self._outputs.get(op_id, {}),
                )
                continue
            self.store.halt_op(self.deploy_id, op_id)
            result.per_op[op_id] = OpAttemptOutcome(
                op_id=op_id,
                final_status=OpStatus.HALTED,
                attempts=0,
                error_message=(
                    "deploy aborted: lease/lock renewal lost "
                    "(possible split-brain writer)"
                ),
            )

    # --- Per-op execution ---------------------------------------------------

    def _complete_barrier(
        self, barrier: BarrierInstance, result: DeployResult
    ) -> None:
        """Barriers carry no execution — they succeed when their predecessors
        succeed. Since we visit in topological order, when we reach a barrier
        all its preds have already finished (or were halted).
        """
        preds = self.plan.predecessors(barrier.op_id)
        any_halted = any(
            result.per_op.get(p) and result.per_op[p].final_status != OpStatus.COMPLETED
            for p in preds
        )
        if any_halted:
            self.store.halt_op(self.deploy_id, barrier.op_id)
            result.per_op[barrier.op_id] = OpAttemptOutcome(
                op_id=barrier.op_id,
                final_status=OpStatus.HALTED,
                attempts=0,
            )
        else:
            self.store.complete_op(self.deploy_id, barrier.op_id, outputs={})
            result.per_op[barrier.op_id] = OpAttemptOutcome(
                op_id=barrier.op_id,
                final_status=OpStatus.COMPLETED,
                attempts=0,
                outputs={},
            )

    def _halt_one(self, op_id: str, result: DeployResult) -> None:
        self.store.halt_op(self.deploy_id, op_id)
        result.per_op[op_id] = OpAttemptOutcome(
            op_id=op_id,
            final_status=OpStatus.HALTED,
            attempts=0,
        )

    def _backend_for(self, op: OpInstance) -> Adapter:
        """Route ops to a backend by idempotency class: `config` -> the config
        backend (axrange.config_exec), everything else -> the infra adapter."""
        if op.idempotency_class == IdempotencyClass.CONFIG:
            if self.config_backend is None:
                raise AdapterError(
                    f"op {op.op_id} is config-class but no config backend is "
                    "wired into the executor",
                    error_class="executor-no-config-backend",
                )
            return self.config_backend
        return self.adapter

    def _lock_keys(self, op: OpInstance, resolved: dict[str, Any]) -> list[str]:
        """Correctness lock keys this op must hold while running, or [] when
        resource locking is disabled. Throughput categories are not locked
        here (PLANNER §8.4)."""
        if not self._resource_locks:
            return []
        return correctness_lock_keys(op, resolved, self.cluster)

    def _run_one(self, op: OpInstance) -> OpAttemptOutcome:
        # Refresh the persisted inputs at the moment this op actually starts
        # an attempt (GAP-044, LES-157): `insert_ops`'s `INSERT OR IGNORE`
        # only ever writes `inputs_resolved_json`/`inputs_from_outputs_json`
        # on the FIRST apply, so a re-applied deploy's op rows would
        # otherwise keep the first apply's inputs forever even though `op`
        # is freshly composed from the CURRENT plan. Reuses `op_to_row` (S1,
        # code review, LES-025 reuse direction) rather than re-implementing
        # its serialization -- guarantees a re-run's row is genuinely
        # byte-identical to what a fresh `insert_ops` would write, not just
        # coincidentally so, and stays correct if `op_to_row` ever changes.
        fresh_row = op_to_row(op, self.deploy_id)
        self.store.set_op_running(
            self.deploy_id,
            op.op_id,
            inputs_resolved_json=fresh_row.inputs_resolved_json,
            inputs_from_outputs_json=fresh_row.inputs_from_outputs_json,
        )
        resolved = self._resolve_inputs(op)
        ctx = ExecutionContext(
            plan_id=self.plan.plan_id,
            deploy_id=self.deploy_id,
            cluster=self.cluster,
            placement=dict(self.plan.placement),
            timeout_s=op.timeout_s,
            tier=self.plan.tier,
        )

        # Cross-process correctness locks (PLANNER §8.4): hold for the whole op
        # (across retries), release on any terminal outcome. All-or-nothing
        # acquisition in a global key order makes this deadlock-free.
        #
        # Key resolution (`correctness_lock_keys`) can raise LockResolutionError
        # when an op's category references a `$var` we can't substitute — a
        # planner/catalog bug, not a transient failure. That used to happen
        # BEFORE this try/finally, so it propagated out of run() uncaught,
        # crashing the whole deploy with the op stuck `running`. Resolve it
        # inside its own try so a bad op fails cleanly (terminal FAILED) and
        # the rest of the deploy proceeds/halts normally.
        try:
            keys = self._lock_keys(op, resolved)
        except LockResolutionError as e:
            self.store.fail_op(self.deploy_id, op.op_id)
            return OpAttemptOutcome(
                op_id=op.op_id,
                final_status=OpStatus.FAILED,
                attempts=0,
                error_message=str(e),
            )

        if keys and not acquire_blocking(
            self.store,
            keys,
            self.deploy_id,
            op.op_id,
            ttl_s=self._lock_ttl_s,
            poll_s=self._lock_poll_s,
            timeout_s=self._lock_timeout_s,
            sleep_fn=self._sleep,
        ):
            self.store.fail_op(self.deploy_id, op.op_id)
            return OpAttemptOutcome(
                op_id=op.op_id,
                final_status=OpStatus.FAILED,
                attempts=0,
                error_message=(
                    "resource lock acquisition timed out: " + ", ".join(keys)
                ),
            )
        try:
            return self._run_attempts(op, resolved, ctx)
        finally:
            if keys:
                self.store.release_resource_locks_for_op(op.op_id)

    def _run_attempts(
        self, op: OpInstance, resolved: dict[str, Any], ctx: ExecutionContext
    ) -> OpAttemptOutcome:
        last_error: str | None = None
        backend = self._backend_for(op)
        for attempt_no in range(1, self.retry.max_attempts + 1):
            delay = self.retry.delay_for(attempt_no)
            if delay > 0:
                self._sleep(delay)
            attempt_id = self.store.start_attempt(self.deploy_id, op.op_id)
            try:
                outputs = backend.run(op, resolved, ctx)
            except AdapterError as e:
                err_class = e.error_class
                err_msg = str(e)
                self.store.end_attempt(
                    attempt_id,
                    AttemptResult.ERROR,
                    error_message=err_msg,
                    error_class=err_class,
                )
                last_error = err_msg
                # Continue retry loop.
                continue
            except Exception as e:  # noqa: BLE001
                # Adapter raised something other than AdapterError — treat as
                # error_class=internal for the state-store grep view; do not
                # retry an unknown failure (likely a bug).
                self.store.end_attempt(
                    attempt_id,
                    AttemptResult.ERROR,
                    error_message=f"{type(e).__name__}: {e}",
                    error_class="internal",
                )
                self.store.fail_op(self.deploy_id, op.op_id)
                return OpAttemptOutcome(
                    op_id=op.op_id,
                    final_status=OpStatus.FAILED,
                    attempts=attempt_no,
                    error_message=f"{type(e).__name__}: {e}",
                )
            # Success.
            self.store.end_attempt(attempt_id, AttemptResult.SUCCESS)
            self.store.complete_op(self.deploy_id, op.op_id, outputs=outputs or {})
            self._outputs[op.op_id] = outputs or {}
            return OpAttemptOutcome(
                op_id=op.op_id,
                final_status=OpStatus.COMPLETED,
                attempts=attempt_no,
                outputs=outputs or {},
            )

        # Exhausted retries.
        self.store.fail_op(self.deploy_id, op.op_id)
        return OpAttemptOutcome(
            op_id=op.op_id,
            final_status=OpStatus.FAILED,
            attempts=self.retry.max_attempts,
            error_message=last_error,
        )

    # --- Input resolution ---------------------------------------------------

    def _resolve_inputs(self, op: OpInstance) -> dict[str, Any]:
        """Merge `inputs_resolved` with values fetched from prior ops' outputs.

        In-process outputs (`self._outputs`) are checked first; the state store
        is the fallback when the executor is later extended to resume from
        prior runs.
        """
        merged: dict[str, Any] = dict(op.inputs_resolved)
        for input_name, (producer_op_id, output_name) in op.inputs_from_outputs.items():
            value = self._lookup_output(producer_op_id, output_name)
            merged[input_name] = value
        return merged

    def _lookup_output(self, producer_op_id: str, output_name: str) -> Any:
        if producer_op_id in self._outputs:
            return self._outputs[producer_op_id].get(output_name)
        # Resume path: pull from state store.
        row = self.store.get_op(self.deploy_id, producer_op_id)
        if row is None or row.outputs_json is None:
            return None
        import json

        try:
            data = json.loads(row.outputs_json)
        except json.JSONDecodeError:
            return None
        return data.get(output_name) if isinstance(data, dict) else None
