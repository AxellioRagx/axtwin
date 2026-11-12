"""Pass 4: placement — pick a Proxmox node per scenario host node.

The slice does trivial single-node placement: every host lands on
`ctx.default_node`. The full algorithm (PLANNER.md §3.4 — least-loaded among
nodes satisfying the union of IR `requires` and binding `requires_host_caps`)
is deferred to a later slice; the surface here keeps the same input/output so
swapping in the real algorithm is a one-function change.
"""
from __future__ import annotations

from axrange.ir.schema import IR
from axrange.planner.cluster import ClusterContext
from axrange.planner.dag import Plan


class PlacementError(Exception):
    pass


def place_hosts(plan: Plan, ir: IR, ctx: ClusterContext) -> None:
    """Populate `plan.placement` with `{host_id: proxmox_node_name}` for every host."""
    if not ctx.default_node:
        raise PlacementError(
            "ClusterContext.default_node is empty — slice placement needs one node"
        )
    if ir.layers is None or ir.layers.infra is None:
        return
    for node in ir.layers.infra.nodes:
        if node.id is None:
            continue
        plan.placement[node.id] = ctx.default_node
    _check_native_switch_single_node(plan, ir, ctx)


def _check_native_switch_single_node(plan: Plan, ir: IR, ctx: ClusterContext) -> None:
    """CLU-080 (docs/design/native-switches.md §3): every native switch and
    every VM attached to one sit on ONE node -- v1 has no cross-node switch.
    Trivially true while placement is single-node; this keeps it true when
    placement learns least-loaded."""
    from axrange.switchmodel import resolve_switches

    model = resolve_switches(ir, ctx.platform_caps)
    if not model.has_native:
        return
    members = list(model.native) + sorted({vm for (vm, _net) in model.leg_port})
    nodes = {m: plan.placement.get(m) for m in members}
    distinct = sorted({n for n in nodes.values() if n})
    if len(distinct) > 1:
        first = members[0]
        odd = [m for m in members if nodes[m] != nodes[first]]
        raise PlacementError(
            f"CLU-080: native switches are single-node in v1, but {odd[0]!r} is placed on "
            f"{nodes[odd[0]]!r} and {first!r} on {nodes[first]!r}"
        )
