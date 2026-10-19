"""Pass 3: add ordering edges from `after:` clauses + barrier predecessor sets.

This pass turns the catalog's textual scope-substitution patterns into real
edges between op instances. The patterns (docs/OP_CATALOG.md §5):

  [$var]       — bound scope variable resolved from the consumer's scope
                 (e.g. `network-ready[$net]` on consumer `attach-vm-to-vnet`
                 binds `$net` to the consumer's net-scope value).

  [<x>@<y>]    — compound. Same resolution per component.

  [*]          — every instance of that op type in the plan.

  [$var@*]     — every instance of that op type whose scope includes
                 `$var = consumer's $var`. E.g. `attach-vm-to-vnet[$host@*]`
                 on `start-vm[$host]` = every NIC-attach for this host.

A predecessor pattern that resolves to zero existing ops is silently dropped
— the catalog encodes conditional predecessors (e.g. `install-ovs-mirror[$host]`
for non-tapped hosts) by simply not emitting the producer. Missing-producer
patterns are not an error.

Special-case: the OP_CATALOG §5.3 "conditional predecessor" rule on `start-vm`
for tapped hosts (`install-ovs-mirror[$host]` and `attach-gre-tunnel[$net]`
gate `start-vm`). The catalog records this as the mirror ops' contribution,
not as a static edge in start-vm. We honor that by adding two edges per
emitted `install-ovs-mirror`.

Barrier wiring (§5.7) runs through the same resolver, against each barrier's
`predecessors` tuple in the registry.
"""
from __future__ import annotations

import re

from axrange.ops.registry import get_barrier, get_op
from axrange.planner.dag import BarrierInstance, OpInstance, Plan, _op_id


_PATTERN_RE = re.compile(
    r"""
    ^(?P<name>[a-z][a-z0-9-]*)
    (?:\[(?P<scope>.*?)\])?$
    """,
    re.VERBOSE,
)


def add_ordering_edges(plan: Plan) -> None:
    """Add edges from catalog `after:` clauses + barrier predecessor sets."""
    _add_op_after_edges(plan)
    _add_barrier_predecessor_edges(plan)
    _add_mirror_conditional_edges(plan)
    _add_router_egress_edges(plan)
    _add_forwarder_edges(plan)


#: The in-guest recipe ops a host's network path must be up for (apt, pip,
#: an agent download) -- the same list `_add_router_egress_edges` gates.
_RECIPE_OPS = ("provision-app", "run-script", "install-defense-agent", "install-agent")


def _plan_topology(plan: Plan) -> tuple[dict[str, set[str]], set[str], set[str]]:
    """`(host -> its segments, router hosts, the routers' segments)`, read off
    the plan's `attach-vm-to-vnet` (the `__wan__` pseudo-net excluded) and
    `configure-router` ops."""
    host_nets: dict[str, set[str]] = {}
    for op in plan.ops.values():
        if op.op_name != "attach-vm-to-vnet":
            continue
        host, net = op.scope.get("host"), op.scope.get("net")
        if host is None or net is None or net == "__wan__":
            continue
        host_nets.setdefault(host, set()).add(net)
    routers = {
        op.scope["host"] for op in plan.ops.values()
        if op.op_name == "configure-router" and op.scope.get("host")
    }
    router_nets: set[str] = set()
    for r in routers:
        router_nets |= host_nets.get(r, set())
    return host_nets, routers, router_nets


def forwarder_layers(plan: Plan) -> tuple[dict[str, int], dict[str, int]]:
    """`(forwarder -> BFS layer, forwarder -> component id)` for the plan's
    FORWARDERS: non-router hosts with NICs on two or more segments (an L2
    bridge VM, an L3 `kind: host` router stand-in -- docs/
    WORK_ORDER_corp_twin.md §10 P0). Derived from the plan's own
    `attach-vm-to-vnet` rows (the WAN pseudo-net excluded) and its
    `configure-router` hosts, so the pass needs no planner hint.

    Layer = BFS distance from the routers' segments: a forwarder with a leg
    on a router segment is layer 0, one reached only through a layer-0
    forwarder is layer 1, and so on (unreachable: no entry in the first map).
    Component = connected component of forwarders joined by a shared
    NON-router segment -- a router segment does not join two forwarders,
    because the router routes between its legs rather than bridging them."""
    return _forwarder_layers(*_plan_topology(plan))


def _forwarder_layers(
    host_nets: dict[str, set[str]], routers: set[str], router_nets: set[str],
) -> tuple[dict[str, int], dict[str, int]]:
    """`forwarder_layers` over an already-read `_plan_topology`."""
    forwarders = {
        h: nets for h, nets in host_nets.items() if h not in routers and len(nets) >= 2
    }
    if not forwarders:
        return {}, {}

    layer: dict[str, int] = {}
    reached = set(router_nets)
    frontier_depth = 0
    while True:
        new = sorted(
            f for f, nets in forwarders.items() if f not in layer and nets & reached
        )
        if not new:
            break
        for f in new:
            layer[f] = frontier_depth
        for f in new:
            reached |= forwarders[f]
        frontier_depth += 1

    comp: dict[str, int] = {}
    next_cid = 0
    for start in sorted(forwarders):
        if start in comp:
            continue
        cid, next_cid = next_cid, next_cid + 1
        stack = [start]
        while stack:
            f = stack.pop()
            if f in comp:
                continue
            comp[f] = cid
            shared = forwarders[f] - router_nets
            stack.extend(
                g for g, nets in forwarders.items() if g not in comp and nets & shared
            )
    return layer, comp


def _addressed_hosts(plan: Plan) -> set[str]:
    """Hosts with at least one ADDRESSED leg (a static or DHCP `ip` in their
    `configure-cloud-init`'s `ip_config`), plus every host with no
    `configure-cloud-init` at all (its legs are unknown here, so it is never
    assumed address-less)."""
    seen: set[str] = set()
    addressed: set[str] = set()
    for op in plan.ops.values():
        if op.op_name != "configure-cloud-init" or op.scope.get("host") is None:
            continue
        host = op.scope["host"]
        seen.add(host)
        legs = op.inputs_resolved.get("ip_config") or {}
        if any((leg or {}).get("ip") for leg in legs.values()):
            addressed.add(host)
    for op in plan.ops.values():
        host = op.scope.get("host")
        if host is not None and host not in seen:
            addressed.add(host)
    return addressed


def _add_forwarder_edges(plan: Plan) -> None:
    """A host behind a forwarder cannot provision until the forwarder's own
    recipe has built the path (`serve-l2switch.sh` enslaving its bridge ports,
    `serve-vrrp-l3.sh` bringing up forwarding + the VIP). Two rules, both
    between RECIPE ops only (`_RECIPE_OPS`):

      * an ADDRESS-LESS forwarder (every leg L2 -- `configure-cloud-init`'s
        `ip_config` carries no address) waits on no forwarder: it only
        enslaves its own NICs, so the L2 fabric builds in parallel. A
        forwarder with an addressed leg waits on every address-less
        forwarder of its component, and on every addressed one in a strictly
        LOWER BFS layer (nearer the router) -- acyclic by construction:
        address-less forwarders are sources only, and every
        addressed->addressed edge goes from a lower layer to a higher one;
      * any other non-router host with a NIC on a non-router segment a
        forwarder of that component touches waits on EVERY forwarder of the
        component, and on the recipes of every router the component hangs
        off -- a sink, so it cannot close a cycle either (a router's own
        recipes wait only on its configure-router).

    Deliberately coarse (a host waits on the whole component, not only its
    path): the fabric is one small set of device VMs, and a host whose
    upstream is half-built fails in a way that reads like a broken recipe.
    A component with NO layered member (no forwarder of it reaches a router
    segment, even through another forwarder) gets no host rule: nothing it
    carries leads to a router, so there is no provisioning path for its
    hosts to wait on -- and its members' recipes would only add latency.
    Its forwarders still order by the first rule (addressed after
    address-less; none is layered, so no addressed->addressed edge).

    A plan with no forwarder (every shipped plan before corp-twin's device
    VMs) gets no edge from this pass."""
    host_nets, routers, router_nets = _plan_topology(plan)
    layer, comp = _forwarder_layers(host_nets, routers, router_nets)
    if not comp:
        return
    recipes: dict[str, list[str]] = {}
    for op in plan.ops.values():
        if op.op_name in _RECIPE_OPS and op.scope.get("host") is not None:
            recipes.setdefault(op.scope["host"], []).append(op.op_id)
    for ops in recipes.values():
        ops.sort()

    # forwarder -> forwarder. An address-less forwarder (every leg L2: a
    # bridge VM) needs no upstream to apply -- it only enslaves its own NICs
    # -- so it is never a destination here, and the whole L2 fabric builds in
    # parallel. A forwarder WITH an addressed leg (the 9K pair, a switch's
    # mgmt SVI) waits on EVERY address-less forwarder of its component (the
    # path its first packet takes is somewhere in that fabric) and on every
    # addressed one strictly nearer the router. Acyclic: address-less
    # forwarders are sources only, addressed->addressed edges go up a layer.
    addressed = _addressed_hosts(plan)
    for f in sorted(comp):
        if f not in addressed:
            continue
        for g in sorted(comp):
            if g == f or comp[g] != comp[f]:
                continue
            if g in addressed and (
                f not in layer or g not in layer or layer[g] >= layer[f]
            ):
                continue
            for src in recipes.get(g, []):
                for dst in recipes.get(f, []):
                    plan.add_edge(src, dst, kind="ordering")

    # hosts behind the fabric -> every forwarder of the component; only for
    # components that reach a router (at least one layered member).
    layered = {cid for f, cid in comp.items() if f in layer}
    comp_nets: dict[int, set[str]] = {}
    for f, cid in comp.items():
        if cid not in layered:
            continue
        comp_nets.setdefault(cid, set()).update(host_nets.get(f, set()) - router_nets)
    comp_members: dict[int, list[str]] = {}
    for f, cid in sorted(comp.items()):
        comp_members.setdefault(cid, []).append(f)
    # The routers a component hangs off (a router sharing a segment with any
    # of its forwarders). A host behind the fabric ALSO waits on their
    # recipes: a Debian router's static route to a segment behind an L3
    # forwarder is a recipe on the router (corp-twin's `serve-hq-mx-routes.
    # sh`), and without it the return path of that host's first apt does
    # not exist yet.
    comp_routers: dict[int, list[str]] = {}
    for cid, members in comp_members.items():
        member_nets = set().union(*(host_nets.get(f, set()) for f in members))
        comp_routers[cid] = sorted(r for r in routers if host_nets.get(r, set()) & member_nets)
    for host in sorted(host_nets):
        if host in comp or host in routers or host not in recipes:
            continue
        behind = host_nets[host] - router_nets
        for cid, nets in sorted(comp_nets.items()):
            if not behind & nets:
                continue
            for f in [*comp_members[cid], *comp_routers[cid]]:
                for src in recipes.get(f, []):
                    for dst in recipes[host]:
                        plan.add_edge(src, dst, kind="ordering")


def _add_router_egress_edges(plan: Plan) -> None:
    """A host behind a router can't provision (apt/pip over the router's NAT)
    until `configure-router` has brought up forwarding + masquerade. For each
    `configure-router`, gate its `served_hosts`' `provision-app` / `run-script`
    ops on it. Encoded here (not in a static `after:`) because the router→host
    relationship is per-scenario topology, threaded into configure-router's
    `served_hosts` input at emit time. Fixes the apt-before-NAT race
    (CLAUDE.md Immediate Next Actions #4; CHANGELOG 2026-06-24).

    `install-defense-agent` / `install-agent` (OP-OVL-050/070) join this list
    for the same reason (A3 Stage A default-on, code-review 2026-07-11): the
    default exporter's `apt-get install prometheus-node-exporter` — and any
    future defense-tool apt-install — races the router's NAT exactly like
    provision-app's `apt` did. Gates BOTH the baked-in default sweep and any
    hand-authored overlays.defense/overlays.adversary entry on the same host.

    `served_hosts` lives in `cr.planner_hints`, NOT `cr.inputs_resolved`
    (docs/WORK_ORDER_twin_incremental_update.md M1): it is a plan-wide
    roster this pass is the SOLE consumer of, never read by the adapter, so
    it must never be persisted as an op input (see `OpInstance.
    planner_hints`'s docstring in `dag.py`)."""
    for cr in [op for op in plan.ops.values() if op.op_name == "configure-router"]:
        served = set(cr.planner_hints.get("served_hosts") or [])
        # The ROUTER ITSELF is gated too, and it is not in `served_hosts` (that
        # roster is deliberately "every non-router node on my LAN nets"). A
        # router node that carries its own in-guest script has exactly the same
        # race as every host behind it — more sharply, in fact: E1's egress
        # phase payload (`scripts/gen_egress_policy.py`, docs/SLICE_ROUTER.md
        # §9) asserts a working LAN→WAN masquerade before it reports
        # `provision`, and that masquerade IS what `configure-router` installs.
        # No shipped config puts an app on a router today, so this adds no edge
        # to any existing plan; it stops the first one that does from failing in
        # a way that reads like a broken script.
        own_host = cr.scope.get("host")
        if not served and not own_host:
            continue
        for op in plan.ops.values():
            if (
                op.op_name in _RECIPE_OPS
                and (
                    op.scope.get("host") in served
                    or (own_host is not None and op.scope.get("host") == own_host)
                )
            ):
                plan.add_edge(cr.op_id, op.op_id, kind="ordering")


def _add_op_after_edges(plan: Plan) -> None:
    """For every op instance, resolve each `after:` pattern against the plan."""
    for consumer in list(plan.ops.values()):
        op_def = get_op(consumer.op_name)
        if op_def is None:
            continue
        for pattern in op_def.after:
            for producer_id in _resolve_pattern(pattern, consumer.scope, plan):
                if producer_id == consumer.op_id:
                    continue
                plan.add_edge(producer_id, consumer.op_id, kind="ordering")


def _add_barrier_predecessor_edges(plan: Plan) -> None:
    """For every barrier instance, resolve its predecessor set against the plan."""
    for barrier in list(plan.barriers.values()):
        b_def = get_barrier(barrier.barrier_name)
        if b_def is None:
            continue
        for pattern in b_def.predecessors:
            for producer_id in _resolve_pattern(pattern, barrier.scope, plan):
                plan.add_edge(producer_id, barrier.op_id, kind="barrier-pred")


def _add_mirror_conditional_edges(plan: Plan) -> None:
    """OP_CATALOG §5.3 says tapped hosts' `start-vm` gains
    `install-ovs-mirror[$host]` and `attach-gre-tunnel[$net]` as predecessors.

    We encode it here, not in start-vm's static `after:`, because start-vm's
    catalog definition stays tap-agnostic.
    """
    for mirror_op in [
        op for op in plan.ops.values() if op.op_name == "install-ovs-mirror"
    ]:
        host_id = mirror_op.scope.get("host")
        if host_id is None:
            continue
        # The host's start-vm op
        start_id = _op_id(plan.plan_id, "start-vm", {"host": host_id})
        if start_id in plan.ops:
            plan.add_edge(mirror_op.op_id, start_id, kind="ordering")

    # For each tapped host, also gate its start-vm on attach-gre-tunnel of
    # its first net (the bridge it's tapped from). We re-derive the net from
    # the install-ovs-mirror op's gre_port input, which references
    # attach-gre-tunnel by op_id.
    for mirror_op in [
        op for op in plan.ops.values() if op.op_name == "install-ovs-mirror"
    ]:
        host_id = mirror_op.scope.get("host")
        if host_id is None:
            continue
        gre_ref = mirror_op.inputs_from_outputs.get("gre_port")
        if gre_ref is None:
            continue
        gre_op_id, _ = gre_ref
        if gre_op_id not in plan.ops:
            continue
        start_id = _op_id(plan.plan_id, "start-vm", {"host": host_id})
        if start_id in plan.ops:
            plan.add_edge(gre_op_id, start_id, kind="ordering")


# ---------------------------------------------------------------------------
# Pattern resolution
# ---------------------------------------------------------------------------


def _resolve_pattern(
    pattern: str, consumer_scope: dict[str, str], plan: Plan
) -> list[str]:
    """Return existing op_ids in `plan` matching `pattern` from `consumer_scope`.

    Pattern grammar (docs/OP_CATALOG.md §5 scope-pattern convention):

      "<op-or-barrier-name>"                              ; bare name (rare)
      "<op-or-barrier-name>[<scope-spec>]"                ; the normal case

    scope-spec:
      ""                            → match any single scope
      "$var"                        → resolve $var from consumer_scope
      "$var1@$var2"                 → compound; resolve each side
      "value"                       → literal (rare)
      "*"                           → every instance of that op type
      "$var@*"                      → every instance with matching $var
    """
    m = _PATTERN_RE.match(pattern.strip())
    if not m:
        return []
    name = m.group("name")
    scope_spec = m.group("scope") or ""

    # Find all candidate ops/barriers in the plan with this name.
    candidates: list[OpInstance | BarrierInstance] = []
    for op in plan.ops.values():
        if op.op_name == name:
            candidates.append(op)
    for b in plan.barriers.values():
        if b.barrier_name == name:
            candidates.append(b)
    if not candidates:
        return []

    # Star-only: every candidate matches.
    if scope_spec == "*":
        return [c.op_id for c in candidates]

    # Bound-var or compound: tokenize, resolve each component.
    tokens = scope_spec.split("@") if scope_spec else []
    # Star-tail like "$host@*"
    has_star_tail = bool(tokens) and tokens[-1] == "*"
    bound_components: list[str | None] = []
    for tok in tokens:
        if tok == "*":
            bound_components.append(None)
        elif tok.startswith("$"):
            # Substitute from consumer_scope. The map is keyed by short names
            # (`host`, `net`, `scenario`, `domain`, `sensor`, `app`, ...).
            var = tok[1:]
            val = consumer_scope.get(var)
            if val is None:
                # Pattern references a scope var the consumer doesn't carry.
                # Treat as no match (consistent with conditional-producer rules).
                return []
            bound_components.append(val)
        else:
            bound_components.append(tok)

    out: list[str] = []
    for c in candidates:
        scope_values = _scope_value_list(c.scope)
        if not has_star_tail:
            # Exact match on bound components in arbitrary order — the scope
            # is a dict, not positional. So compare sets.
            bound_set = {b for b in bound_components if b is not None}
            scope_set = set(scope_values)
            if bound_set == scope_set:
                out.append(c.op_id)
        else:
            # Star-tail: every non-star bound component must appear in scope.
            required = {b for b in bound_components if b is not None and b != "*"}
            if required.issubset(set(scope_values)):
                out.append(c.op_id)
    return out


def _scope_value_list(scope: dict[str, str]) -> list[str]:
    """Return scope values in canonical (alphabetic-key) order."""
    return [scope[k] for k in sorted(scope.keys())]
