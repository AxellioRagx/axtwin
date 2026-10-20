"""Pass 2: add data-flow edges from each op's `inputs_from_outputs` map.

The producer is already known by op_id at emission time (Pass 1 wrote it).
Pass 2 is therefore mechanical: walk every op, walk its
`inputs_from_outputs`, emit one Edge per entry with kind="dataflow".

Per PLANNER.md §3.2, data-flow edges may bypass barriers — they go directly
between producer and consumer, even if a barrier sits between them in the
ordering DAG. Pass 3 adds the barrier-mediated ordering edges separately.
"""
from __future__ import annotations

from axrange.planner.dag import Plan, PlanValidationError


def add_dataflow_edges(plan: Plan) -> None:
    """Emit one edge per `inputs_from_outputs` entry on every op."""
    for op in plan.ops.values():
        for input_name, (producer_op_id, _producer_output) in op.inputs_from_outputs.items():
            producer = plan.get(producer_op_id)
            if producer is None:
                # Conditional producers (e.g. install-ovs-mirror for a non-
                # tapped host) shouldn't appear here because the consumer is
                # only emitted alongside its producer. If we land here, the
                # plan is internally inconsistent.
                raise PlanValidationError(
                    f"dataflow edge: op {op.op_id} reads input '{input_name}' "
                    f"from producer {producer_op_id} which is not in the plan"
                )
            plan.add_edge(producer_op_id, op.op_id, kind="dataflow")
