"""Planner: validated IR -> DAG of ops -> sequential executor over SQLite state.

First slice (docs/ADAPTER.md §6): 16 infra ops, sequential execution, minimal
state store. The cross-process correctness-lock layer (per-scenario lease +
`resource_locks`, PLANNER §6.2/§8.4) landed 2026-06-03. Still deferred behind
future CHANGELOG entries: per-process throughput semaphores, the parallel
intra-deploy executor, plan-diff/re-planning, and escalate walk-back.

Public surface:

  build_plan(ir, manifests, plan_id=None) -> Plan
      Run passes 1-4 + DAG validation. Returns an executable Plan.

  Executor(adapter, store, ...)
      Sequential executor. Walks the DAG topologically, calls adapter.run()
      per op, persists per-attempt state, halts the affected subtree on
      terminal failure.

  StateStore(db_path)
      SQLite-backed deploys/ops/op_attempts. The status surface and
      resume substrate.
"""
from __future__ import annotations

from axrange.planner.dag import (
    BarrierInstance,
    Edge,
    OpInstance,
    Plan,
    PlanValidationError,
)
from axrange.planner.executor import (
    DeployHalted,
    DeployResult,
    Executor,
    OpAttemptOutcome,
)
from axrange.planner.locks import (
    CORRECTNESS_PREFIXES,
    Heartbeat,
    acquire_blocking,
    correctness_lock_keys,
)
from axrange.planner.plan_builder import PlanBuildError, build_plan
from axrange.planner.state import (
    DeployRow,
    LeaseResult,
    LeaseRow,
    OpRow,
    ResourceLockRow,
    StateStore,
)
from axrange.planner.teardown import (
    TeardownAction,
    TeardownBackend,
    TeardownError,
    TeardownResult,
    destroy_deploy,
    destroy_ops_scoped,
)

__all__ = [
    "CORRECTNESS_PREFIXES",
    "BarrierInstance",
    "DeployHalted",
    "DeployResult",
    "DeployRow",
    "Edge",
    "Executor",
    "Heartbeat",
    "LeaseResult",
    "LeaseRow",
    "OpAttemptOutcome",
    "OpInstance",
    "OpRow",
    "Plan",
    "PlanBuildError",
    "PlanValidationError",
    "ResourceLockRow",
    "StateStore",
    "TeardownAction",
    "TeardownBackend",
    "TeardownError",
    "TeardownResult",
    "acquire_blocking",
    "build_plan",
    "correctness_lock_keys",
    "destroy_deploy",
    "destroy_ops_scoped",
]
