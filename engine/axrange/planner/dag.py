"""Plan, OpInstance, BarrierInstance, edge representation, topological sort.

The graph is stored as `predecessors: dict[op_id, frozenset[op_id]]` — a node
knows who it depends on, not who depends on it. Successor lookup is computed
on demand. Cycle detection runs at DAG-build time (Pass-5, see
plan_builder.py) and at executor start as defense-in-depth.

Op identity (`op_id`) is deterministic from (plan_id, op-type name, scope).
Same IR + same plan_id => identical op_ids, every run. This is the substrate
that lets the state store recognize prior attempts (PLANNER.md §4.2).
"""
from __future__ import annotations

import hashlib
import heapq
from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator

from axrange.ops.registry import (
    Barrier,
    CleanupStrategy,
    IdempotencyClass,
    OpDefinition,
    OpKind,
)


class PlanValidationError(Exception):
    """Raised by Plan.validate() on cycle / unreachable / type / placement bugs."""


# ---------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------

# Scope is a small dict of stringly-keyed scopes the op acts on. Examples:
#   clone-vm[h-fin]           -> {"host": "h-fin"}
#   attach-vm-to-vnet[h-fin@corp] -> {"host": "h-fin", "net": "corp"}
#   attach-gre-tunnel[corp]   -> {"net": "corp", "sensor": "default"}
#   create-sdn-zone[ransom]   -> {"scenario": "ransom"}
#   network-ready[corp]       -> {"net": "corp"}
#
# Keeping scope as a dict (not a structural type) means the op catalog can grow
# scope dimensions (e.g. domain, sensor) without changing this module.

Scope = dict[str, str]


def _scope_repr(scope: Scope) -> str:
    """Canonical bracket-string for an op instance, used in op_id and display."""
    if not scope:
        return "[]"
    # Compound scopes like attach-vm-to-vnet[h-fin@corp] use @ between values.
    # Stable order: keys sorted alphabetically.
    parts = [scope[k] for k in sorted(scope.keys())]
    return "[" + "@".join(parts) + "]"


def _op_id(plan_id: str, op_name: str, scope: Scope) -> str:
    """Deterministic op_id of the form `<plan_id>:<op-name><scope>`.

    PLANNER.md §2.4 specifies `<plan-id>:<op-type>:<scope>`. We use the
    bracket-string form for scope so the id reads cleanly:
    `01HG...:clone-vm[h-fin]`.
    """
    return f"{plan_id}:{op_name}{_scope_repr(scope)}"


# ---------------------------------------------------------------------------
# Op + Barrier instances
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OpInstance:
    """One application of an OpDefinition to a specific IR scope.

    `inputs_resolved` is populated by Pass-1 emission with everything we know
    pre-execution (IR fields + manifest fields + planner-derived values like
    target_node, sensor_transport_ip). Inputs that come from upstream-op outputs
    (e.g. vmid from clone-vm) are filled by the executor at run time from the
    state store, not here.
    """

    op_id: str
    op_name: str           # OpDefinition.name (e.g. "clone-vm")
    op_catalog_id: str     # OpDefinition.id (e.g. "OP-HOST-010")
    kind: OpKind
    scope: Scope
    inputs_resolved: dict[str, Any] = field(default_factory=dict)
    inputs_from_outputs: dict[str, tuple[str, str]] = field(default_factory=dict)
    # name-of-input -> (producer-op_id, name-of-producer-output)
    cleanup_strategy: CleanupStrategy | None = None
    idempotency_class: IdempotencyClass | None = None
    resource_categories: tuple[str, ...] = ()
    timeout_s: int | None = None
    # Planner-internal bookkeeping that ONE OTHER PASS reads at build time
    # (never the adapter, never persisted) -- e.g. `configure-router`'s
    # `served_hosts` (`pass1_emit.py`), consumed only by `pass3_ordering.
    # py`'s `_add_router_egress_edges` to compute ordering EDGES, which are
    # never re-derived from state. Deliberately kept OUT of `inputs_resolved`
    # (`state.op_to_row` only ever serializes THAT dict, never this one) --
    # docs/WORK_ORDER_twin_incremental_update.md M1: a plan-wide roster
    # field like `served_hosts` changes every time ANY host is added/removed
    # anywhere that shares the router's net, even though the router's OWN
    # configured behavior never actually changes -- persisting it made
    # `plandiff.diff_plan` see a spurious Mutated field on EVERY unrelated
    # add/remove, hard-refusing every `--update` a router-bearing scenario
    # could ever run (measured: 100% of shipped twin-configs/archetypes).
    # Migrating an EXISTING persisted input into this dict (rather than
    # introducing a brand-new one) needs one more step: register the key in
    # `plandiff._RETIRED_PERSISTED_INPUT_KEYS`, or every pre-migration
    # deploy's persisted row (which still carries the old key in
    # `inputs_resolved_json`) permanently hard-refuses its first `--update`
    # as a false Mutated field (code review, SHOULD-FIX 4).
    planner_hints: dict[str, Any] = field(default_factory=dict)

    def render(self) -> str:
        return f"{self.op_name}{_scope_repr(self.scope)}"


@dataclass(frozen=True)
class BarrierInstance:
    """A synthetic milestone. No execution; pure ordering point."""

    op_id: str
    barrier_name: str       # Barrier.name (e.g. "network-ready")
    barrier_catalog_id: str
    scope: Scope

    def render(self) -> str:
        return f"{self.barrier_name}{_scope_repr(self.scope)}"


# ---------------------------------------------------------------------------
# Edges + Plan
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Edge:
    """A directed edge from `src_op_id` to `dst_op_id`.

    `kind` records what produced the edge for debug/inspection:
      - "dataflow": Pass-2 (output-of-X feeds input-of-Y)
      - "ordering": Pass-3 (after: clause or IR binding)
      - "barrier-pred": predecessor edge into a barrier
      - "barrier-succ": consumer edge from a barrier
    """

    src_op_id: str
    dst_op_id: str
    kind: str


@dataclass
class Plan:
    """A validated, executable deploy plan.

    Built by `build_plan`. Iterated by `Executor` in topological order.
    """

    plan_id: str
    scenario_id: str
    scenario_version: int | None
    ir_hash: str
    # Resource-ownership tier tag (`docs/CONVENTIONS.md` §2; `docs/WORK_ORDER_
    # resource_tags.md`) this plan's resources are named under. `"te"` (the
    # default — a target-environment deploy) for every normal scenario;
    # `axrange.o2control` is the only caller that builds a `tier="o2"` plan
    # (the standing O2 surface, `docs/design/05-o2-surface.md`). Flows
    # into `ExecutionContext.tier` at run time so the adapter's naming calls
    # (`adapter_proxmox/naming.py`) emit the right tier's name shapes.
    tier: str = "te"
    ops: dict[str, OpInstance] = field(default_factory=dict)
    barriers: dict[str, BarrierInstance] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)
    # placement: scenario node-id -> proxmox node name
    placement: dict[str, str] = field(default_factory=dict)
    # predecessors[op_id] = frozenset of immediate predecessor op_ids
    _predecessors: dict[str, frozenset[str]] = field(default_factory=dict)
    _successors: dict[str, frozenset[str]] = field(default_factory=dict)

    # -- Mutation (used by passes during build) --

    def add_op(self, op: OpInstance) -> None:
        if op.op_id in self.ops or op.op_id in self.barriers:
            raise PlanValidationError(f"duplicate op_id: {op.op_id}")
        self.ops[op.op_id] = op

    def add_barrier(self, b: BarrierInstance) -> None:
        if b.op_id in self.ops or b.op_id in self.barriers:
            raise PlanValidationError(f"duplicate op_id: {b.op_id}")
        self.barriers[b.op_id] = b

    def add_edge(self, src: str, dst: str, kind: str) -> None:
        self.edges.append(Edge(src_op_id=src, dst_op_id=dst, kind=kind))

    # -- Read-side --

    def all_nodes(self) -> dict[str, OpInstance | BarrierInstance]:
        merged: dict[str, OpInstance | BarrierInstance] = {}
        merged.update(self.ops)
        merged.update(self.barriers)
        return merged

    def predecessors(self, op_id: str) -> frozenset[str]:
        return self._predecessors.get(op_id, frozenset())

    def successors(self, op_id: str) -> frozenset[str]:
        return self._successors.get(op_id, frozenset())

    def get(self, op_id: str) -> OpInstance | BarrierInstance | None:
        return self.ops.get(op_id) or self.barriers.get(op_id)

    # -- Index build + validate --

    def freeze(self) -> None:
        """Build the predecessor/successor index from `edges`. Idempotent."""
        preds: dict[str, set[str]] = {nid: set() for nid in self.all_nodes()}
        succs: dict[str, set[str]] = {nid: set() for nid in self.all_nodes()}
        nodes = preds  # alias for membership check
        for e in self.edges:
            if e.src_op_id not in nodes:
                raise PlanValidationError(
                    f"edge {e.src_op_id} -> {e.dst_op_id}: src not in plan"
                )
            if e.dst_op_id not in nodes:
                raise PlanValidationError(
                    f"edge {e.src_op_id} -> {e.dst_op_id}: dst not in plan"
                )
            preds[e.dst_op_id].add(e.src_op_id)
            succs[e.src_op_id].add(e.dst_op_id)
        self._predecessors = {k: frozenset(v) for k, v in preds.items()}
        self._successors = {k: frozenset(v) for k, v in succs.items()}

    def topological_order(self) -> list[str]:
        """Kahn's algorithm. Returns op_ids in dependency-satisfying order.

        Raises PlanValidationError if a cycle is present.
        """
        if not self._predecessors and (self.ops or self.barriers):
            self.freeze()
        in_degree: dict[str, int] = {
            nid: len(self._predecessors.get(nid, frozenset()))
            for nid in self.all_nodes()
        }
        # Deterministic order: same plan, same IR -> same topo sort. A
        # min-heap keyed on op_id extracts the same "smallest ready node
        # first" sequence as re-sorting the whole ready set on every pop
        # (what this used to do), but each pop/push is O(log n) instead of
        # O(n log n) — the frontier no longer gets fully re-sorted per op.
        ready: list[str] = [nid for nid, d in in_degree.items() if d == 0]
        heapq.heapify(ready)
        ordered: list[str] = []
        while ready:
            cur = heapq.heappop(ready)
            ordered.append(cur)
            for s in self._successors.get(cur, frozenset()):
                in_degree[s] -= 1
                if in_degree[s] == 0:
                    heapq.heappush(ready, s)
        if len(ordered) != len(in_degree):
            unresolved = [nid for nid, d in in_degree.items() if d > 0]
            raise PlanValidationError(
                f"cycle detected: {len(unresolved)} ops not reachable in topo "
                f"order (e.g. {unresolved[:3]})"
            )
        return ordered

    def descendants(self, op_id: str) -> set[str]:
        """Transitive successors of op_id (excluding op_id itself)."""
        out: set[str] = set()
        stack = list(self._successors.get(op_id, frozenset()))
        while stack:
            cur = stack.pop()
            if cur in out:
                continue
            out.add(cur)
            stack.extend(self._successors.get(cur, frozenset()))
        return out

    def render_tree(self) -> str:
        """A simple indented tree view, like `axrange plan` prints."""
        lines: list[str] = []
        order = self.topological_order()
        # Compute one-line summary per op.
        for op_id in order:
            node = self.get(op_id)
            if node is None:
                continue
            preds = self.predecessors(op_id)
            tag = "barrier" if isinstance(node, BarrierInstance) else node.kind.value
            lines.append(
                f"  {node.render():35s} [{tag:7s}]"
                + (f"  after: {len(preds)}" if preds else "")
            )
        return "\n".join(lines)


def label_for_op_id(plan: Plan, op_id: str) -> str:
    """`clone-vm[h-c]`-style label for `op_id`, whether or not it is still
    present IN `plan` -- a REMOVED op_id (`docs/
    WORK_ORDER_twin_incremental_update.md`, an `--update`'s diff) has no
    OpInstance/BarrierInstance left to call `.render()` on, but `op_id` is
    always `f"{plan_id}:{op_name}{scope}"` (`_op_id`, above), so stripping
    the known `plan_id:` prefix recovers the exact same label `.render()`
    would have produced. Kept as a free function so a CALLER that already
    knows an id is a legitimate former member of this plan -- API/CLI/
    o2control diff renderers, review S4 -- gets ONE shared implementation
    instead of each re-deriving the op_id grammar (a genuinely bogus id
    that never belonged to this plan_id at all just falls through to
    returning `op_id` verbatim, unlike `.render()`, which has no fallback
    at all -- callers here are trusted to pass a real former member)."""
    node = plan.get(op_id)
    if node is not None:
        return node.render()
    prefix = f"{plan.plan_id}:"
    return op_id[len(prefix):] if op_id.startswith(prefix) else op_id


# ---------------------------------------------------------------------------
# Plan-id helpers
# ---------------------------------------------------------------------------


def derive_plan_id(scenario_id: str, ir_hash: str, *, tier: str = "te") -> str:
    """Deterministic plan id from (scenario_id, ir_hash[:8][, tier]).

    Per PLANNER.md §4.2, deterministic naming is the substrate for Model C
    idempotency. We don't need the full hash; the prefix gives ~16 bits of
    uniqueness across re-plans of the same scenario, and the scenario_id makes
    the id human-readable.

    `tier` (code-review N2, A1 follow-up, `docs/CONVENTIONS.md` §2) folds the
    resource-ownership tier into the id for every NON-`te` tier, so a future
    tier can never alias a `te` plan_id that happens to reuse the same
    scenario_id + ir_hash (extremely unlikely today — a composed o2-surface
    scenario_id/shape is already distinctive — but cheap insurance,
    `StateStore.find_deploy_for_plan` keys on plan_id alone). `te` plan_ids
    are UNCHANGED (no suffix) to keep every existing plan_id — and every
    test/fixture that pins one — stable.
    """
    short = ir_hash[:8] if ir_hash else "00000000"
    if tier == "te":
        return f"{scenario_id}-{short}"
    return f"{scenario_id}-{short}-{tier}"


def hash_ir(raw_ir: dict[str, Any]) -> str:
    """SHA-256 of the canonicalized raw IR dict. Used for plan_id derivation."""
    import json

    canonical = json.dumps(raw_ir, sort_keys=True, default=str).encode()
    return hashlib.sha256(canonical).hexdigest()
