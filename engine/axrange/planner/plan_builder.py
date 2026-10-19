"""Top-level plan builder: validated IR -> Plan.

Runs the four passes in order, then DAG validation. The result is an
executable Plan the executor walks.

  Pass 1: emit_ops (pass1_emit.py)
  Pass 2: add_dataflow_edges (pass2_dataflow.py)
  Pass 3: add_ordering_edges (pass3_ordering.py)
  Pass 4: place_hosts (pass4_placement.py)
  Pass 5: validate_plan (validation.py)
"""
from __future__ import annotations

from typing import Any

from axrange.ir.schema import IR
from axrange.manifest.loader import ManifestRegistry
from axrange.planner.cluster import ClusterContext
from axrange.planner.dag import Plan, derive_plan_id, hash_ir
from axrange.planner.pass1_emit import EmissionError, emit_ops
from axrange.planner.pass2_dataflow import add_dataflow_edges
from axrange.planner.pass3_ordering import add_ordering_edges
from axrange.planner.pass4_placement import PlacementError, place_hosts
from axrange.planner.validation import validate_plan


class PlanBuildError(Exception):
    """Wraps EmissionError / PlacementError / PlanValidationError with a uniform
    surface for the CLI. Use `cause` to introspect the original."""

    def __init__(self, msg: str, *, cause: Exception | None = None) -> None:
        super().__init__(msg)
        self.cause = cause


def build_plan(
    ir: IR,
    manifests: ManifestRegistry,
    ctx: ClusterContext,
    *,
    plan_id: str | None = None,
    raw_ir: dict[str, Any] | None = None,
    scenario_dir: str | None = None,
    tier: str = "te",
) -> Plan:
    """Build and validate a Plan from a validated IR + manifests + cluster ctx.

    `raw_ir` is the post-include-splice dict the parser returned; if omitted,
    we hash an empty dict (which means plan_ids won't deduplicate properly
    across runs — fine for tests, the CLI passes `raw_ir`).

    `scenario_dir` is the scenario file's directory, needed only to read
    `{script:}` install-action files at emit time (the CLI passes it).

    `tier` is the resource-ownership tier tag (`docs/CONVENTIONS.md` §2) this
    plan's resources are named under — `"te"` for every normal scenario
    (default, unchanged behavior); `axrange.o2control` passes `"o2"` when
    composing the standing surface deploy (`docs/design/05-o2-surface.md`).
    Stamped onto `Plan.tier`; the executor reads it at run time to build
    each op's `ExecutionContext.tier`.
    """
    if ir.scenario is None or ir.scenario.id is None:
        raise PlanBuildError("IR scenario.id is required")

    scenario_id = ir.scenario.id
    scenario_version = ir.scenario.version
    ir_hash = hash_ir(raw_ir or {})
    pid = plan_id or derive_plan_id(scenario_id, ir_hash, tier=tier)

    plan = Plan(
        plan_id=pid,
        scenario_id=scenario_id,
        scenario_version=scenario_version,
        ir_hash=ir_hash,
        tier=tier,
    )

    try:
        emit_ops(plan, ir, manifests, ctx, scenario_dir)
        add_dataflow_edges(plan)
        add_ordering_edges(plan)
        place_hosts(plan, ir, ctx)
        validate_plan(plan)
    except (EmissionError, PlacementError) as exc:
        raise PlanBuildError(str(exc), cause=exc) from exc
    return plan
