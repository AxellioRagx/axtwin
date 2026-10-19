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
