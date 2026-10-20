"""Pass 5 (DAG-level validation): cycle + reachability + type + placement.

PLANNER.md §3.5 lists four checks. We implement all four; for the slice the
type check is reduced (every typed input is either statically resolved or
comes from a recorded producer) and placement satisfiability is trivial
(one node).
"""
from __future__ import annotations

from axrange.planner.dag import Plan, PlanValidationError


def validate_plan(plan: Plan) -> None:
    """Run all four DAG-level checks. Raises PlanValidationError on first failure."""
    plan.freeze()
    _check_cycles_and_reachability(plan)
    _check_type_consistency(plan)
    _check_placement(plan)


def _check_cycles_and_reachability(plan: Plan) -> None:
    """Topological sort catches cycles; reachability is implicit in the
    successor index (every emitted node is reachable from *some* root unless
    its preds vanished). We assert no orphan edges."""
    order = plan.topological_order()
    if len(order) != len(plan.all_nodes()):
        unreached = set(plan.all_nodes()) - set(order)
        raise PlanValidationError(
            f"reachability: {len(unreached)} ops not visited by topo order "
            f"(possible cycle/disconnect): {sorted(unreached)[:3]}"
        )


def _check_type_consistency(plan: Plan) -> None:
    """For every `inputs_from_outputs` entry, confirm the producer exists.
    Strict type-name matching is reserved for a richer typed-input model in a
    later slice — here we just confirm the producer is present and is an
    OpInstance (barriers produce no outputs)."""
    for op in plan.ops.values():
        for input_name, (producer_op_id, _output_name) in op.inputs_from_outputs.items():
            producer = plan.get(producer_op_id)
            if producer is None:
                raise PlanValidationError(
                    f"type: op {op.op_id} reads '{input_name}' from missing "
                    f"producer {producer_op_id}"
                )
            if producer_op_id in plan.barriers:
                raise PlanValidationError(
                    f"type: op {op.op_id} reads '{input_name}' from barrier "
                    f"{producer_op_id} (barriers have no outputs)"
                )


def _check_placement(plan: Plan) -> None:
    """For the slice, every emitted host op must have a placement entry.
    A future slice replaces this with the union-cap satisfiability check."""
    host_ops = (
        "clone-vm",
        "configure-vm-hardware",
        "configure-cloud-init",
        "attach-vm-to-vnet",
        "start-vm",
        "wait-guest-agent",
        "wait-os-ready",
        "install-ovs-mirror",
    )
    for op in plan.ops.values():
        if op.op_name not in host_ops:
            continue
        host_id = op.scope.get("host")
        if host_id is None:
            continue
        if host_id not in plan.placement:
            raise PlanValidationError(
                f"placement: op {op.op_id} references host {host_id!r} "
                f"with no placement entry"
            )
