"""Twin inventory — the published binding contract instrumentation tiers
consume (`docs/design/02-twin-model.md`, `docs/WORK_ORDER_tier_split.md`
"keystone"; consolidated review fix pass, 2026-07-23).

A twin (`te`-tier deploy) publishes a stable inventory so the Traffic (`tfc`)
and Tap (`tap`) tiers can bind against it without reaching into twin
internals:

  * **`hosts[]`** — the persona-assignable roster (`role`, `vm_name`,
    `nets[]`, `os`), consumed by the future Traffic tier AND, as of
    `docs/WORK_ORDER_tap_eligibility.md` (D1/D5, Authority: user,
    2026-07-28), the Tap tier too: `hosts[].nets[]` (the twin's FULL
    attachment topology) is now the ONLY thing this module publishes about
    a twin's ports. There is no more `tap_points[]`.

**`tap_points[]` is REMOVED** (`docs/WORK_ORDER_tap_eligibility.md` D5) --
the twin substrate is tap-agnostic: it carries no candidate-capture-point
catalog of its own any more (the earlier `TapPointInfo` dataclass, sourced
from the now-retired `OP-MIR-005 record-tap-intent`, is gone). The Tap
tier's `axrange.o2control.taps` resolves ALL tap-point semantics --
eligibility, scope, target -- from `hosts[].nets[]` alone, via `axrange.
planner.tappoints.derive_default_tap_points`/`resolve_tap_point` (INTERNAL
resolution machinery, never published from here).

This module GENERALIZES the derivation style
`axrange.o2control.taps._scan_te_inventory` already uses (read `scope`/
`inputs_resolved`/`outputs` off each recorded op row) but lives in the
ENGINE, not `o2control` (LES-025: the engine never imports `o2control`; only
`o2control` — and the future `tfc`/A2-successor tiers — import the engine).
`_scan_te_inventory` itself is re-expressed as a thin adapter over
`build_twin_inventory` (review SHOULD-FIX #8) so there is exactly ONE
derivation, not two that can drift.

No adapter/cluster call: everything here is read back from `StateStore`
(plus, optionally, a `ManifestRegistry` for the `os` lookup below — still no
cluster call, just a local file read).

**Source-of-facts design (review MUST-FIX #2 — read this before touching
`role`/`segment`/`ip`/`os`):** an EARLIER revision of this module sourced
`role`/`segment`/`ip`/`os` from `record-tap-intent` (OP-MIR-005), additively
threaded there alongside `tapped_nics`. That was WRONG and has been reverted:
`record-tap-intent` only ever runs for a host with >=1 tapped NIC, so an
UNTAPPED twin (no `tap:` anywhere in the IR) would publish an all-`None`
roster, and editing a *tap* declaration would silently change what the
*Traffic* tier could bind to — re-coupling the two tiers `docs/
design/02-twin-model.md` exists to keep independent. Every fact below
is now sourced from an **unconditional** op instead:

  * `nets[]` (segment + ip per attached leg) ← `attach-vm-to-vnet`, which
    fires for **every** (host, net) pair regardless of tap status. Carries
    **all legs** of a multi-homed host (a router's several NICs, a
    dual-homed server) rather than picking just the first — a multi-homed
    host is exactly what the twin model cares about representing faithfully.
    The router's synthetic `__wan__` leg is now RECORDED too (`docs/
    WORK_ORDER_tap_scopes.md` M2 — an earlier revision excluded it entirely,
    "not a twin segment"), marked `HostNet.external=True` so a consumer that
    means "twin segments only" can still filter it out explicitly instead of
    relying on it never appearing.
  * `os` ← `clone-vm`'s recorded `image_id` input, re-resolved through a
    `ManifestRegistry` lookup (the SAME `_os_family()` helper `pass1_emit.py`
    itself calls when it renders `wait-os-ready`'s `os_family` input — reused,
    not reimplemented, so the two call sites can't independently drift). If
    no `manifests` registry is passed to `build_twin_inventory` (or the image
    is unknown to it), `os` is published as `None` — **never fabricated**.
    This deliberately does NOT read `wait-os-ready`'s already-persisted
    `os_family` input: that value already has pass1_emit's own `or "linux"`
    fallback baked in (right for an op input that needs a concrete runtime
    value; wrong for a published fact that drives `persona.os_family`
    binding — publishing a fabricated "linux" for an unknown image would be
    exactly the kind of silent-success this contract must not produce).
  * `role` ← `clone-vm`'s recorded `node_role` input (the IR's `node.role`
    field, `docs/WORK_ORDER_traffic_tier.md` Part 1 -- **resolves GAP-027**).
    `clone-vm` is unconditional (fires for every cloned host regardless of
    tap status, LES-095), so an untapped twin still publishes a real
    persona-assignable roster. `None` when the node declares no `role:`
    (never fabricated -- same posture as `os`/`image_id`). An EARLIER
    revision hung this on a now-retired tap-gated op instead (the same
    coupling mistake the "Source-of-facts design" note above explains);
    `_coarse_host_role()` (an even earlier `node.kind`-derived stand-in,
    `"host"` for nearly every node) was removed outright too, a
    constant-almost-everywhere value being a null signal masquerading as a
    populated field, worse than an honest `None`.
  * `listen` ← `clone-vm`'s recorded `node_listen` input (the IR's
    `App.listen`, joined through `bindings[]` -- `docs/
    WORK_ORDER_protocol_faithful_services.md` S1, D2(b), Authority: user).
    The twin's **declared** listen set: what it says each host serves, as
    distinct from what a capture OBSERVED (the fidelity scorecard's
    reference side) and from what an in-guest `ss -lnH -tu` finds bound (its
    row 4). Same posture as `role`/`os`/`kind`: `None` when the host
    declares none -- never an empty list, never fabricated. Carried on
    `clone-vm` for the same reason `node_role` is (unconditional per host,
    LES-095), and emitted there ONLY when non-empty so no standing deploy's
    op rows gain a key `plandiff` would read as Mutated (see
    `pass1_emit`'s own comment).

**Coverage:** `hosts[]` includes EVERY host the twin cloned (from
`clone-vm`, unconditional) — not just tapped ones; `nets[]`/`os` are
populated for every host as of this fix (both sourced from unconditional
ops), a strict improvement over the pre-fix tap-gated behavior.

**Deploy/host completeness (review SHOULD-FIX #7):** `inputs_resolved_json`
is persisted at PLAN-INSERT time (`Executor.persist_plan`), before any op
actually runs — so a deploy that failed at, say, op 3 still has every
host's plan-time facts sitting in `state.db`, and this module would
otherwise publish a full roster with `vmid=None` for a host that was never
actually cloned. `HostInfo.vm_status` (the recorded `clone-vm` op's own
`OpStatus`, e.g. `"completed"`/`"pending"`/`"failed"`/`"halted"`) and
`TwinInventory.deploy_status` (the deploy's own status at read time) let a
consumer (the Traffic tier, in particular) tell "genuinely deployed" apart
from "planned but never actually stood up" before binding a persona to a
host that doesn't exist on the cluster — a LES-001-class silent-success trap
otherwise.

**Resume-staleness caveat (review documentation item #14):** the executor's
Model-C resume path skips re-running an op whose `op_id` is already
`completed` — and an op's `op_id` is derived from its **scope**, not its
resolved inputs' content. So a deploy created under an OLDER `pass1_emit.py`
(before some future field gets added to `attach-vm-to-vnet`/`clone-vm`'s
`inputs_resolved`) keeps that OLDER row's shape frozen in `state.db` until
the scenario is redeployed with `--fresh` — a resume does NOT retroactively
backfill a newly-added field into an already-`completed` op's persisted
inputs. Every read path in this module uses `.get(...)` defensively for
exactly this reason: an absent key must degrade to `None`/empty, never
raise, so a pre-existing deploy publishes a partial (not broken) inventory
until it is next fully (re)deployed.

**Tap-point id/location namespace -- RETIRED (`docs/WORK_ORDER_tap_eligibility.md`
D5):** this module used to ALSO publish `tap_points[].id`, a mechanically-derived,
redeploy-stable `host:net:nic` interface id sourced from `record-tap-intent`.
Both the field and the op are gone; `axrange.planner.tappoints`'s semantic
grouping/resolution layer now derives its own `host:net:nic` ids directly
from `hosts[].nets[]` (`derive_default_tap_points`, internal, never
published from here) instead of joining against a published catalog.

**`net_bridge`/`vlan_underlay` (review MUST-FIX, second round; historical --
predates D5's `tap_points[]` removal, kept for the reasoning trail):**
`TwinInventory` publishes the FULL `net_id -> bridge` map for every network
this deploy declared (plus the raw VLAN underlay name, if any) -- STILL
true post-D5, and unrelated to the `tap_points[]` removal above (`net_bridge`
was never keyed off `tap_points` at all after this fix; see below). The
first cut of this fix only resolved `bridge` on already-tapped `tap_points`,
which read as equivalent to the original inline scan
`o2control.taps._scan_te_inventory` used to do (same field shapes) but
actually NARROWED the key domain: the original scan's `net_bridge` covered
every network regardless of tap status (a Simple zone's per-net
`create-ovs-bridge` op fires unconditionally), so a caller could ask for ANY
net's bridge, not just a tapped one. `_scan_te_inventory`'s adapter reshape
(below) republishes this full map so `_bridge_for_net`'s callers keep
resolving correctly for any net a tap-plan binding names, not just ones an
earlier apply happened to also bind.

**Reuse note (review SHOULD-FIX #1):** this module used to hand-roll its
own `_json_obj`/scope-inputs-outputs unpacking — exactly what `planner.
opview.op_fields` already normalizes (for both `OpRow` and `OpInstance`) so
every op-derived read path in the codebase shares one duck-typing helper
(LES-037). Reused now, not reimplemented.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from axrange.adapter_proxmox import naming
from axrange.adapter_proxmox.naming import vm_name_for_host
from axrange.planner.egress import EgressPosture, egress_posture_from_ops
from axrange.planner.opview import op_fields
from axrange.planner.state import StateStore


def _os_family_lookup(image_id: str | None, manifests: Any) -> str | None:
    """Reuse (not reimplement, LES-037) `pass1_emit._os_family` — see the
    module docstring's `os` bullet for why this is a fresh re-lookup, not a
    read of `wait-os-ready`'s already-fallback-fabricated `os_family`
    input."""
    if manifests is None or image_id is None:
        return None
    from axrange.planner.pass1_emit import _os_family

    return _os_family(image_id, manifests)


class InventoryError(Exception):
    """Raised when the requested deploy doesn't exist."""


@dataclass
class HostNet:
    """One attached leg (`attach-vm-to-vnet`'s recorded `{net_id, ip,
    nic_index}` for this host) — a multi-homed host gets one entry per leg.

    `external` (`docs/WORK_ORDER_tap_scopes.md` M2) — `True` for a router's
    synthetic WAN uplink (`net_id == "__wan__"`), `False` for an ordinary
    twin segment. A TYPED marker, not name-sniffing `__wan__` at each call
    site (`docs/design/06-taps-sensors.md`): every consumer that means
    "twin segments only" filters on this field instead. An EARLIER revision
    dropped the WAN leg here entirely (`build_twin_inventory`'s
    `attach-vm-to-vnet` handling used to `continue` on `net_id == "__wan__"`)
    — now RECORDED, marked `external=True`, so `perimeter: wan` (`tappoints.
    py`) has data to resolve against; every pre-existing consumer that
    assumed "no `__wan__` entry ever appears here" must filter on `external`
    explicitly (audited, `docs/WORK_ORDER_tap_scopes.md` M2) rather than
    relying on the old drop to do it implicitly."""

    net_id: str
    ip: Optional[str] = None
    nic_index: Optional[int] = None
    external: bool = False


@dataclass
class HostListen:
    """One DECLARED listen `{port, proto}` on a host (`docs/
    WORK_ORDER_protocol_faithful_services.md` S1, D2(b), Authority: user,
    2026-09-04) -- what the twin SAYS this host serves.

    Sourced from `clone-vm`'s `node_listen` input (the IR's `App.listen`,
    joined through `bindings[]` by `pass1_emit._declared_listen_by_host`).
    A host with no declaration publishes `HostInfo.listen = None`, never an
    empty list -- see that field's own comment.
    Declared, never measured: nothing here asserts the port is bound. The
    fidelity scorecard's row 4 (in-guest `ss -lnH -tu`) and row 5 (a
    protocol-aware probe) are the two channels that measure that, and they
    report this set ALONGSIDE the observed one, never instead of it."""

    port: int
    proto: str = "tcp"


@dataclass
class HostInfo:
    host_id: str                     # the twin's own node id (e.g. "ws01")
    vm_name: Optional[str] = None    # deterministic VM display name (vm_name_for_host)
    vmid: Optional[int] = None
    pve_node: Optional[str] = None   # physical PVE node from deploy placement
    vm_status: Optional[str] = None  # this host's recorded clone-vm op status
    # EFFECTIVE role: a tfc-assigned role (`role_overrides`, see
    # `build_twin_inventory`) if one exists for this host, else the twin's own
    # baseline. This is what every consumer should bind against.
    role: Optional[str] = None
    # The twin's OWN baseline (`clone-vm`'s recorded `node_role`), kept
    # alongside so a display can say "file-server (tfc; baseline: ws)" and an
    # operator is never confused about why a host answers to a role its twin
    # config didn't declare. Equal to `role` when no override applies.
    role_baseline: Optional[str] = None
    # The IR node's `kind:` (normalized: `host`/`router`/...). Sourced from
    # clone-vm's `node_kind` input; back-filled to "router" for a host that has
    # a `configure-router`/`configure-routing` op (so a deploy predating the
    # `node_kind` record still identifies its routers). Consumed by
    # `tappoints` (`_is_router_kind`) to find router ports for `perimeter`
    # scope (`docs/WORK_ORDER_tap_scopes.md` M1) -- NOT to exclude anything
    # from `segment` any more (that exclusion was superseded 2026-07-27,
    # brief §3.1: under rx-only it was a coverage hole, not a de-dup).
    # `None` when unknown (never fabricated).
    kind: Optional[str] = None
    nets: list[HostNet] = field(default_factory=list)   # ALL attached legs
    os: Optional[str] = None         # os family via image_id -> manifest lookup; None if unresolved
    # The DECLARED listen set, or `None` when this host declares none --
    # never `[]`. `None` is the ONE honest answer for "this twin makes no
    # claim about what this host serves": a twin config that declares nothing
    # for the host, and equally a deploy predating the field or resumed past
    # an already-`completed` clone-vm (LES-096). There is no second, distinct
    # "claims to serve nothing" state to represent -- an empty `listen: []`
    # is refused at the author instead (`twinconfig.expand._check_listen`),
    # so it can never arrive here. Consumers test truthiness.
    listen: Optional[list[HostListen]] = None
    # NOTE (review SHOULD-FIX #2, second round): a `node` back-compat alias
    # for `vm_name` used to live here (a property + a `to_dict()` extra key).
    # DROPPED, not kept: this module is brand-new with zero shipped
    # consumers, and the alias had already produced a shape MISMATCH between
    # `te inventory --json` (which had it) and `GET /api/deploys/{id}/
    # inventory` (whose DTO never did) for the exact same data — the kind of
    # inconsistency a published contract should never launch with. `vm_name`
    # is the one, honest field name everywhere now (CLI, API DTO, dataclass).


# `TapPointInfo` REMOVED (`docs/WORK_ORDER_tap_eligibility.md` D5) -- the
# twin substrate publishes no candidate-capture-point catalog of its own;
# see the module docstring's "Tap-point id/location namespace" note.


@dataclass
class TwinInventory:
    deploy_id: str
    deploy_status: Optional[str] = None
    hosts: list[HostInfo] = field(default_factory=list)
    # Full `net_id -> bridge` map (review MUST-FIX, second round) — EVERY
    # network this deploy declared (a Simple zone's per-net
    # `create-ovs-bridge` already covers all of them; a VLAN zone's single
    # scenario-scoped underlay is back-filled for all of them too). Restores
    # `o2control.taps._scan_te_inventory`'s ORIGINAL key domain — see that
    # module's docstring for why narrowing this was a real bug (an
    # orphaned-gretap silent-continue on a tap-plan's drop path).
    net_bridge: dict[str, str] = field(default_factory=dict)
    vlan_underlay: Optional[str] = None
    # The twin's CONTAINMENT POSTURE (GAP-101) -- `None` when the twin
    # declares none (every twin deployed before the field existed), never
    # fabricated, same posture as `role`/`os`/`listen` above. Derived by
    # `axrange.planner.egress.egress_posture_from_ops` from the SAME op rows
    # this function already scans, so `te inventory` and `GET /api/deploys/
    # {id}/inventory` answer "is this twin contained?" without an ssh to the
    # router -- the question nobody could answer during the 2026-09-21 live
    # run, which is how the breach went unnoticed.
    egress: Optional[EgressPosture] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "deploy_id": self.deploy_id,
            "deploy_status": self.deploy_status,
            "hosts": [asdict(h) for h in self.hosts],
            "net_bridge": dict(self.net_bridge),
            "vlan_underlay": self.vlan_underlay,
            "egress": self.egress.to_dict() if self.egress is not None else None,
        }


def build_twin_inventory(
    store: StateStore, deploy_id: str, *, manifests: Any = None,
    role_overrides: Optional[dict[str, str]] = None,
) -> TwinInventory:
    """Assemble `deploy_id`'s twin inventory from its recorded ops.

    Composes `hosts[]` from `clone-vm` (vmid + status + image_id, universal —
    every cloned host) + `deploy.placement_json` (pve_node) +
    `attach-vm-to-vnet` (all attached legs) + an optional `manifests`
    registry (`os`, via image_id lookup — `None` if omitted/unresolved).
    Pure state-store derivation (plus an optional local manifest-registry
    read) — no cluster call, safe against a still-`running` or `completed`
    deploy alike. Publishes NO tap-related state (`docs/
    WORK_ORDER_tap_eligibility.md` D5) — `hosts[].nets[]` (this module's own
    unconditional `attach-vm-to-vnet` scan) is the twin's entire published
    topology; `axrange.planner.tappoints` derives every tap-point semantic
    from it at bind time, never from a field this function emits.

    **`role_overrides` (host_id -> role, `docs/WORK_ORDER_role_persona_tier.md`
    §4.2).** A host's semantic role became `tfc`-OWNED and runtime-swappable on
    2026-08-04: this map wins over `clone-vm`'s recorded `node_role` baseline,
    which is what lets a role change without re-cloning the node (the role used
    to live INSIDE `clone-vm`'s persisted inputs -- precisely why changing one
    needed a redeploy). `HostInfo.role` is the EFFECTIVE value;
    `HostInfo.role_baseline` keeps the twin's own.

    The map is passed IN rather than looked up here on purpose: this module is
    the ENGINE, and the engine never imports `o2control` (LES-025). Resolving
    "which tfc deploy belongs to this twin, and what roles did it record" is
    `o2control.traffic.effective_roles_for_twin`'s job; callers that want the
    effective roster (the CLI's `te inventory`/`te info`, the API's inventory
    endpoint) thread it through. Omitting it yields the pre-2026-08-04
    behavior exactly -- baseline only.
    """
    deploy = store.get_deploy(deploy_id)
    if deploy is None:
        raise InventoryError(f"deploy not found: {deploy_id}")

    # Validate the deploy's OWN tier BEFORE it reaches `vm_name_for_host`
    # below, which would otherwise raise a bare, untyped `ValueError`
    # (`adapter_proxmox.naming._check_tier`) for an unrecognized tier --
    # the same crash class `planner/teardown.py`'s `destroy_deploy` closes
    # for its own `underlay_bridge_for_plan` call (review MUST-FIX #4,
    # second round). Routed through this module's OWN typed error so every
    # caller's existing `except InventoryError` handling covers it, instead
    # of a raw traceback surfacing at the CLI/API layer.
    own_tier = deploy.tier or "te"
    if own_tier not in naming.TIER_SHAPE_CHECKS:
        raise InventoryError(
            f"deploy {deploy_id} has unrecognized tier {own_tier!r}; refusing to read"
        )

    try:
        placement: dict[str, str] = json.loads(deploy.placement_json or "{}")
    except (ValueError, TypeError):
        placement = {}

    host_vmid: dict[str, int] = {}
    host_status: dict[str, str] = {}
    host_image_id: dict[str, str] = {}
    host_role: dict[str, str] = {}
    host_kind: dict[str, str] = {}
    host_listen: dict[str, list[HostListen]] = {}
    host_nets: dict[str, list[HostNet]] = {}
    net_bridge: dict[str, str] = {}
    vlan_underlay: Optional[str] = None
    all_net_ids: set[str] = set()   # EVERY declared network
    seen_hosts: dict[str, None] = {}  # insertion-ordered set

    rows = list(store.ops_for_deploy(deploy_id))

    # Hosts an incremental update has SWEPT (`te deploy --update --allow-destroy`
    # tombstones their op rows `destroyed`, exactly as `plandiff.diff_plan`'s own
    # tombstone exclusion expects). Their VMs are GONE from the cluster, so they
    # are not part of the twin's published binding contract and must not reach a
    # consumer: the tap playout resolves a `segment` scope against these hosts and
    # composes a real `install-ovs-mirror` against the deleted vmid, which fails
    # hard (`qm set <vmid> --hookscript ...` -> "Configuration file ... does not
    # exist") and takes the WHOLE re-apply down -- which is precisely what the
    # update's own "still references removed host(s) -- re-apply it" advice tells
    # an operator to do. Measured on dev45 after a 73-host removal: 73 failed
    # install-ovs-mirror ops, inventory reporting 196 hosts for a 123-host twin.
    # Computed in its own pass so a SUBORDINATE op (attach-vm-to-vnet, ...) can
    # never re-add a swept host below.
    swept_hosts: set[str] = set()
    for op in rows:
        op_name, scope, _inp, _out = op_fields(op)
        if op_name == "clone-vm" and scope.get("host") is not None:
            status = getattr(op, "status", None)
            if (getattr(status, "value", str(status)) or "") == "destroyed":
                swept_hosts.add(str(scope["host"]))

    for op in rows:
        op_name, scope, inp, out = op_fields(op)
        host = scope.get("host")
        if host is not None and str(host) in swept_hosts:
            continue

        if op_name == "clone-vm" and host is not None:
            host = str(host)
            seen_hosts.setdefault(host, None)
            status = getattr(op, "status", None)
            if status is not None:
                host_status[host] = getattr(status, "value", str(status))
            vmid = out.get("vmid")
            if vmid is not None:
                try:
                    host_vmid[host] = int(vmid)
                except (TypeError, ValueError):
                    pass
            image_id = inp.get("image_id")
            if image_id:
                host_image_id[host] = str(image_id)
            # LES-096: `.get(...)` defensively -- a deploy resumed from
            # before this field existed simply has no `node_role` key here;
            # `role` degrades to `None` for that host until a `--fresh`
            # redeploy, never raises.
            role = inp.get("node_role")
            if role:
                host_role[host] = str(role)
            # `node_kind` (added alongside `node_role`) -- absent on a deploy
            # predating the field (LES-096), so the router-op back-fill below
            # is the load-bearing signal for those. Normalized like
            # `pass1_emit._is_router` (`kind.router`/`Router` -> `router`).
            kind = inp.get("node_kind")
            if kind:
                host_kind[host] = str(kind).lower().split(".")[-1]
            # `node_listen` (2026-09-05) -- `.get(...)` defensively for the
            # same LES-096 reason as `node_role`/`node_kind` above: the key is
            # emitted only by a plan built after the field existed AND only
            # for a host whose IR actually declares a listen set, so absence
            # is the norm and must degrade to `None` (a host that declares
            # nothing), never raise and never fabricate an empty set.
            declared = inp.get("node_listen")
            if isinstance(declared, list):
                parsed: list[HostListen] = []
                for entry in declared:
                    if not isinstance(entry, dict) or entry.get("port") is None:
                        continue
                    try:
                        port = int(entry["port"])
                    except (TypeError, ValueError):
                        continue
                    parsed.append(
                        HostListen(port=port, proto=str(entry.get("proto") or "tcp").lower())
                    )
                if parsed:
                    host_listen[host] = parsed

        elif op_name in ("configure-router", "configure-routing") and host is not None:
            # A `configure-router`/`configure-routing` op is emitted ONLY for a
            # `kind: router` node (pass1_emit), and fires for every deploy
            # regardless of when it was created -- so it identifies routers even
            # on a twin whose clone-vm predates `node_kind` (the live corp-
            # enterprise twin). Consumed by `tappoints` -- LOAD-BEARING for
            # `perimeter` scope (every router port on a target net) and
            # `o2control/taps.py`'s `_is_router_host`/`_gateway_ip_for_net`
            # (fix round G3 split it from the STRUCTURAL, leg-count-only
            # `_is_route_ambiguous` `_select_verify_injector` uses), NOT to
            # exclude router legs from `segment` (superseded
            # 2026-07-27, `docs/design/06-taps-sensors.md` -- under
            # rx-only that exclusion is a coverage hole, not a de-dup; do
            # not reinstate it).
            host_kind[str(host)] = "router"

        elif op_name == "attach-vm-to-vnet" and host is not None:
            host = str(host)
            seen_hosts.setdefault(host, None)
            net_id = scope.get("net") or inp.get("net_id")
            if not net_id:
                continue
            # `docs/WORK_ORDER_tap_scopes.md` M2: RECORD the router's WAN
            # uplink instead of discarding it (an earlier revision `continue`d
            # here) -- marked `external=True` so `perimeter: wan` has data to
            # resolve against, while every consumer that means "twin segments
            # only" filters on the flag instead of re-deriving the sentinel
            # check itself (`HostNet`'s own docstring).
            is_external = str(net_id) == "__wan__"
            ip = inp.get("ip")
            nic_index = inp.get("nic_index")
            try:
                nic_index = int(nic_index) if nic_index is not None else None
            except (TypeError, ValueError):
                nic_index = None
            host_nets.setdefault(host, []).append(
                HostNet(
                    net_id=str(net_id), ip=ip, nic_index=nic_index,
                    external=is_external,
                )
            )
            if is_external:
                # Fix round F3 (`docs/WORK_ORDER_tap_scopes.md`): the WAN
                # attach op's OWN resolved input already names its real
                # bridge (`vnet_id`, a plain resolved value for this op --
                # `pass1_emit.py` sets it to `ROUTER_WAN_BRIDGE`, never a
                # `create-vnet` output like an ordinary net's attach does).
                # Record it directly rather than letting `__wan__` fall into
                # the VLAN-underlay backfill below, which would publish the
                # SCENARIO's underlay bridge for it -- wrong, and exactly the
                # LES-118 aliasing shape (`gre_port_for_bridge_sensor` would
                # then name the same device the segment taps use). NOT added
                # to `all_net_ids` (the VLAN-underlay backfill loop, below,
                # only ever iterates that set) -- this is the one thing that
                # makes the backfill loop safe to leave unconditional.
                vnet_id = inp.get("vnet_id")
                if vnet_id:
                    net_bridge.setdefault(str(net_id), str(vnet_id))
            else:
                all_net_ids.add(str(net_id))

        elif op_name == "create-ovs-bridge":
            bridge = out.get("bridge_name")
            if bridge:
                if "net" in scope:
                    net_bridge[str(scope["net"])] = str(bridge)
                    all_net_ids.add(str(scope["net"]))
                elif "scenario" in scope:
                    vlan_underlay = str(bridge)

        # `record-tap-intent` branch REMOVED here (docs/WORK_ORDER_tap_eligibility.md
        # D4/D5, Authority: user, 2026-07-28) -- the op is retired; a te
        # deploy records no tap-related state for this scan to read.

    # VLAN zone: every network shares the one underlay bridge (matches
    # pass1_emit's VLAN create-ovs-bridge scope) -- fill in EVERY declared
    # net_id (review MUST-FIX, second round: an earlier revision narrowed
    # this to only nets with a tap point, silently shrinking `net_bridge`'s
    # key domain versus the original scan, which ALSO had a Simple-zone
    # per-net op covering every network unconditionally).
    if vlan_underlay:
        for net_id in all_net_ids:
            net_bridge.setdefault(net_id, vlan_underlay)

    hosts = [
        HostInfo(
            host_id=host_id,
            vm_name=vm_name_for_host(deploy.plan_id, host_id, tier=deploy.tier or "te"),
            vmid=host_vmid.get(host_id),
            pve_node=placement.get(host_id),
            vm_status=host_status.get(host_id),
            role=(role_overrides or {}).get(host_id) or host_role.get(host_id),
            role_baseline=host_role.get(host_id),
            kind=host_kind.get(host_id),
            nets=host_nets.get(host_id, []),
            os=_os_family_lookup(host_image_id.get(host_id), manifests),
            listen=host_listen.get(host_id),   # None when undeclared, never []
        )
        for host_id in seen_hosts
    ]
    hosts.sort(key=lambda h: h.host_id)  # deterministic order, review SHOULD-FIX #11

    return TwinInventory(
        deploy_id=deploy_id,
        deploy_status=getattr(deploy.status, "value", str(deploy.status)),
        hosts=hosts,
        net_bridge=net_bridge,
        vlan_underlay=vlan_underlay,
        # ONE derivation, shared with `te info`'s own read (LES-037) -- both
        # surfaces call `egress_posture_from_ops` over the same `rows`, so
        # they can never disagree about whether a twin is contained.
        egress=egress_posture_from_ops(rows),
    )
