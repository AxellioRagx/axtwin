"""The twin's EGRESS POSTURE — both directions (GAP-101,
`docs/WORK_ORDER_protocol_engines.md` §E1).

Two small jobs, kept in one module because they are the two ends of one fact:

  * `resolve_egress_policy(ir)` — the AUTHORING path. Turns the IR's
    top-level `egress:` block (`axrange.ir.schema.Egress`, authored as
    `twin_config.egress:` and expanded there) into the `egress` input
    `OP-HOST-090 configure-router` already accepts. Resolves the sink
    address from the twin's own `role: sink` node so it is never typed
    twice, and FAILS LOUD at plan time when it cannot.
  * `egress_posture_from_ops(ops)` — the READ-BACK path. Recovers what a
    deployed twin actually reached from its `configure-router` op row
    (`egress_phase`, the phase the router really entered — never the one
    that was asked for), so `te info`/`te inventory` and their API peers can
    answer "is this twin contained?" without an ssh to the router.

WHY THE SINK IS RESOLVED AND NOT RE-TYPED
-----------------------------------------
The twin already states the sink's address once, on the `role: sink` node
(`twins/svc-engines/te.yaml`: `nets: [{segment: ext, ip:
10.66.30.10}]`). A second hand-written copy in the egress block is a fact
that can drift, and a `measure` phase pointed at a stale address is a twin
that believes it is contained while every external destination black-holes —
the neighbouring failure to GAP-101's own (a twin that believed it was
contained while it talked to the real internet). So: resolve, allow an
explicit override for the cases resolution cannot see, and refuse to guess.

WHY THE FAILURE IS AT PLAN TIME
-------------------------------
`te plan` is cluster-free. An unresolvable sink discovered by the adapter
mid-deploy has already cloned and started the twin's VMs; discovered here it
costs nothing and names the fix. The adapter re-validates anyway
(`egress_render.normalize_egress`) — that is the load-bearing gate, this is
the early one.

BOUNDARY NOTE
-------------
This module calls `adapter_proxmox.egress_render.normalize_egress` for the
vocabulary (phase/mode names, the `sink_ip` requirement, CIDR parsing).
That function is pure dict-in/dict-out validation with no Proxmox, IR or
planner knowledge (read its module docstring) and is the SAME normalizer
`scripts/gen_egress_policy.py` calls. Re-implementing the phase vocabulary
here instead would put a second, drift-prone copy of a containment rule set
in the tree — precisely the thing `egress_render`'s docstring says is "how a
twin ends up talking to the real internet while a test says it cannot"
(LES-025: reuse downward, don't reimplement).
"""
from __future__ import annotations

from typing import Any, Optional

from axrange.ir.schema import IR, Node

#: The node `role:` a sink advertises itself with. The twin's own vocabulary
#: (`twins/svc-engines/te.yaml`), not a new one.
SINK_ROLE = "sink"


class EgressDeclarationError(Exception):
    """A twin declared `egress:` that cannot be turned into a policy.

    Raised at PLAN time (pass 1), never at deploy time. `pass1_emit` wraps it
    in its own `EmissionError` so the CLI's existing handling covers it.
    """


def _node_role(node: Node) -> str:
    return str(node.role or "").strip().lower()


def _node_ips(node: Node) -> list[tuple[str, str]]:
    """[(net_id, ip), ...] for every leg of this node that HAS an address."""
    out: list[tuple[str, str]] = []
    for ref in node.nets or []:
        if ref.net and ref.ip:
            out.append((str(ref.net), str(ref.ip)))
    return out


def _nodes(ir: IR) -> list[Node]:
    if ir.layers is None or ir.layers.infra is None:
        return []
    return list(ir.layers.infra.nodes)


def _resolve_sink_ip(ir: IR, named: Optional[str]) -> str:
    """The sink's address, from the twin's own declaration.

    `named` (the block's `sink:`) picks a node by id; otherwise the twin's
    unique `role: sink` node is used. Every ambiguity is an ERROR naming the
    explicit `sink_ip:` escape hatch — guessing which of two sinks (or which
    of a multi-homed sink's legs) a `measure` phase should point at is
    exactly the silent-wrong-answer this module exists to prevent.
    """
    nodes = _nodes(ir)
    if named:
        matches = [n for n in nodes if str(n.id or "") == str(named)]
        if not matches:
            raise EgressDeclarationError(
                f"egress `sink: {named}` names no node in this twin "
                f"(nodes: {sorted(str(n.id) for n in nodes if n.id)})"
            )
        candidates = matches
        subject = f"node {named!r}"
    else:
        candidates = [n for n in nodes if _node_role(n) == SINK_ROLE]
        subject = f"the twin's `role: {SINK_ROLE}` node"
        if not candidates:
            raise EgressDeclarationError(
                "egress policy needs a sink address and this twin declares no "
                f"`role: {SINK_ROLE}` node — give one node `role: {SINK_ROLE}` "
                "(the node that impersonates the external world), or name it "
                "with `egress.sink:`, or state `egress.sink_ip:` explicitly. "
                "A `measure` phase pointed at nothing contains the twin by "
                "black-holing it, which is not the same as containing it."
            )
        if len(candidates) > 1:
            names = sorted(str(n.id) for n in candidates if n.id)
            raise EgressDeclarationError(
                f"egress policy is ambiguous: {len(candidates)} nodes declare "
                f"`role: {SINK_ROLE}` ({names}) — name one with `egress.sink:` "
                "or state `egress.sink_ip:` explicitly"
            )

    node = candidates[0]
    ips = _node_ips(node)
    if not ips:
        raise EgressDeclarationError(
            f"egress policy cannot resolve a sink address: {subject} "
            f"({node.id!r}) declares no IP on any of its legs — pin one "
            "(`nets: [{segment: ..., ip: ...}]`) or state `egress.sink_ip:` "
            "explicitly. The router needs a literal next hop; a DHCP address "
            "is not known at plan time."
        )
    if len(ips) > 1:
        raise EgressDeclarationError(
            f"egress policy is ambiguous: {subject} ({node.id!r}) is "
            f"multi-homed with addresses {[ip for _net, ip in ips]} — state "
            "`egress.sink_ip:` explicitly so the router's next hop is the "
            "leg you mean"
        )
    return ips[0][1]


def resolve_egress_policy(ir: IR) -> Optional[dict[str, Any]]:
    """The IR's `egress:` block -> `configure-router`'s `egress` op input.

    Returns `None` when the twin declares NO `egress:` block at all — the
    op input is then omitted entirely and the router keeps today's permissive
    masquerade (`rest._apply_egress_policy` returns `None` and touches
    nothing). "Unspecified" must NOT mean "closed": every shipped twin config
    apt-installs over that masquerade, and this function silently closing
    them would break all of them (LES-213 — a new rule must not change a
    config that never opted in).

    A DECLARED block with no `phase:` is `measure` — default CLOSED, applied
    by `normalize_egress` below, which is the one place that default lives.
    """
    from axrange.adapter_proxmox.egress_render import (  # boundary note in module docstring
        EgressRenderError,
        normalize_egress,
    )

    block = ir.egress
    if block is None:
        return None

    sink_ip = str(block.sink_ip or "").strip() or _resolve_sink_ip(ir, block.sink)
    spec: dict[str, Any] = {"phase": block.phase, "sink_ip": sink_ip}
    # `mode`/`cidrs` only when stated: `normalize_egress` owns their defaults,
    # and an unconditional key here would put `mode: default` into every
    # declaring twin's op inputs whether or not it asked for one.
    if block.mode is not None:
        spec["mode"] = block.mode
    if block.cidrs is not None:
        spec["cidrs"] = list(block.cidrs)

    try:
        return normalize_egress(spec)
    except EgressRenderError as exc:
        # Same vocabulary error the adapter would raise mid-deploy, raised
        # here instead — before a single VM is cloned.
        raise EgressDeclarationError(f"twin `egress:` block is invalid: {exc}") from exc


# ---------------------------------------------------------------------------
# Read-back: what phase did this deploy's router actually reach?
# ---------------------------------------------------------------------------


class EgressPosture:
    """A deployed twin's containment posture, read off its `configure-router`
    op row(s) — the answer to "is this twin contained?".

    `phase_applied` is the router's OWN report (`configure-router`'s
    `egress_phase` output: the phase it actually reached, never the one that
    was asked for). `phase_declared` is what the twin asked for. They differ
    exactly when the op has not run yet (declared set, applied `None`) — a
    planned-but-not-yet-applied containment is not a containment, and the two
    fields are kept apart so no surface can round one into the other.

    A plain class rather than a dataclass only because `to_dict()` is the
    published shape; see `TwinInventory.to_dict`/`te info --json`.
    """

    __slots__ = ("router_host", "phase_declared", "phase_applied", "sink_ip", "mode",
                 "live_read_at")

    def __init__(
        self,
        *,
        router_host: Optional[str] = None,
        phase_declared: Optional[str] = None,
        phase_applied: Optional[str] = None,
        sink_ip: Optional[str] = None,
        mode: Optional[str] = None,
        live_read_at: Optional[str] = None,
    ) -> None:
        self.router_host = router_host
        self.phase_declared = phase_declared
        self.phase_applied = phase_applied
        self.sink_ip = sink_ip
        self.mode = mode
        # When `phase_applied` came from a LIVE read (`axrange te egress`, or
        # the tap/tfc containment gate) rather than configure-router's own
        # report: that read's UTC timestamp. None = never read live.
        self.live_read_at = live_read_at

    @property
    def contained(self) -> bool:
        """True ONLY when a router has actually REPORTED `measure`.

        Deliberately reads `phase_applied`, never the declaration: the whole
        of GAP-101 is a twin that was believed contained because someone
        meant to contain it.
        """
        return self.phase_applied == "measure"

    def to_dict(self) -> dict[str, Any]:
        return {
            "router_host": self.router_host,
            "phase_declared": self.phase_declared,
            "phase_applied": self.phase_applied,
            "sink_ip": self.sink_ip,
            "mode": self.mode,
            "contained": self.contained,
            "live_read_at": self.live_read_at,
        }

    def __eq__(self, other: object) -> bool:  # tests/readability
        if not isinstance(other, EgressPosture):
            return NotImplemented
        return self.to_dict() == other.to_dict()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"EgressPosture({self.to_dict()})"


def egress_posture_from_ops(ops: Any) -> Optional[EgressPosture]:
    """Scan recorded (or planned) ops for the twin's egress posture.

    `None` when no `configure-router` op carries an `egress` input at all —
    i.e. the twin declares no posture, which is the state every twin
    deployed before GAP-101 is in. Never fabricated: an undeclared twin
    publishes no posture rather than a reassuring `provision`.

    The FIRST declaring router wins (the block is twin-level, so every router
    carries the same policy by construction); its own `egress_phase` output
    is the applied phase. `.get(...)` throughout, LES-096: a row predating
    the field simply has no key.
    """
    from axrange.planner.opview import op_fields

    for op in ops:
        name, scope, inputs, outputs = op_fields(op)
        if name != "configure-router":
            continue
        declared = inputs.get("egress")
        if not isinstance(declared, dict):
            continue
        applied = outputs.get("egress_phase")
        live = outputs.get("egress_live")
        read_at = live.get("read_at") if isinstance(live, dict) else None
        return EgressPosture(
            router_host=(str(scope["host"]) if scope.get("host") else None),
            phase_declared=(str(declared["phase"]) if declared.get("phase") else None),
            phase_applied=(str(applied) if applied else None),
            sink_ip=(str(declared["sink_ip"]) if declared.get("sink_ip") else None),
            mode=(str(declared["mode"]) if declared.get("mode") else None),
            live_read_at=(str(read_at) if read_at else None),
        )
    return None
