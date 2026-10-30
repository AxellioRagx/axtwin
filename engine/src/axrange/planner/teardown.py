"""`axrange destroy` — reverse-of-create teardown driven by recorded state.

The teardown path axRange never built (CLAUDE.md flags it: no `destroy` verb;
OVS bridges persist in `/etc/network/interfaces`). This is the engine half of
`docs/design/11-api-ui.md`: a state-driven teardown ROUTINE (the brief's lean
over formal decommission ops), exposed by the `axrange destroy` CLI verb and the
`DELETE /api/deploys/{id}` endpoint.

Design (the §8 "resolve at build" choices, settled here):

  * **Driven by the deploy's recorded state**, not by re-running the planner.
    Every infra op recorded the *identity* of what it created in its
    `outputs_json` (`clone-vm` → vmid, `create-vnet` → vnet_id, `create-sdn-zone`
    → zone_id, `create-ovs-bridge` → bridge_name, `attach-gre-tunnel` → gre_port).
    We read those back from `ops` — faithful to what was actually built, and
    immune to a since-edited scenario file / adapter.conf.

  * **Touches ONLY THIS deploy's OWN tier's resources** (`deploy.tier`, `te`
    by default; `o2` for the standing surface deploy `axrange.o2control`
    stands up — `docs/design/05-o2-surface.md`; `tfc`/`exr` reserved
    ahead of the Traffic/Exercise tiers — `docs/design/02-twin-model.md`,
    `docs/WORK_ORDER_tier_split.md`). Every name is re-checked against
    the deterministic-naming invariants (`adapter_proxmox/naming.py`) before
    it is touched — the hard guard the brief calls for. `img_*` golden
    templates (and any operator-declared protected-external name) are
    refused UNCONDITIONALLY (a `guard-violation`, no tier's destroy ever
    sweeps one); every OTHER foreign tier's resources are refused UNLESS
    this destroy's own deploy IS that tier (so a `te`/`tap` `axrange destroy`
    can never touch the standing surface or a `tfc`/`exr` deploy's
    resources, while `axrange o2 destroy` — this exact function, reused
    verbatim — CAN tear down its own `o2_*` resources) — see `_protected()`'s
    six-tier matrix below. The OVS-bridge step is additionally gated to
    *exactly* `underlay_bridge_for_plan(plan_id, tier=deploy.tier)` for THIS
    deploy, so a bare `vmbr0` / an operator bridge / another scenario's
    bridge can never be removed (the real blast-radius step, §8 step 5).

  * **Reverse dependency order** (§8): VMs → mirror snippets → gretaps → SDN
    vnets → SDN zone → cluster SDN apply → OVS underlay bridge → state transition
    + lease/lock release.

  * **Idempotent / resume-safe.** Each backend op is check-then-act ("already
    gone" == success). A partial teardown (a VM that wouldn't stop) is finished
    by re-running. The deploy is marked `destroyed` ONLY on a clean sweep, so a
    re-run retries whatever failed and only then flips the terminal state.

This module is the planner-side ORCHESTRATOR — it owns the ordering, the
name-guards, and the state transition; it knows nothing about Proxmox. The
Proxmox-specific reverse operations live behind the `TeardownBackend` protocol
(`axrange.platform.protocols.Teardown`; the Proxmox one is
`axrange.adapter_proxmox.teardown.ProxmoxTeardown`, handed in by the platform
factory), the same boundary the executor keeps with the `Adapter` protocol.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from axrange.platform.protocols import Teardown

from axrange.planner.state import DeployRow, OpRow, StateStore
from axrange.platform import default_namer

# The naming seam (`axrange.platform.protocols.ResourceNamer`, ARCHITECTURE
# §13) of the default platform: the tier-shape predicates below are the
# engine's own vocabulary (`docs/CONVENTIONS.md` §2) and the one platform
# identity check (`is_te_vmid` -> `is_managed_guest_handle`) is the
# platform's. No target is in hand here (the deploy row is), so the default
# platform's namer is the one `api/Spec.md` A4 says a target without
# `platform:` means.
naming = default_namer()
is_img = naming.is_img
is_protected_external = naming.is_protected_external
is_tap_gretap = naming.is_tap_gretap
is_te_vmid = naming.is_managed_guest_handle
underlay_bridge_for_plan = naming.underlay_bridge_for_plan


# ---------------------------------------------------------------------------
# Name guards — the deterministic-naming invariants (adapter_proxmox/naming.py).
# A name that fails its guard is NOT touched; it is recorded as a guard
# violation and blocks the clean sweep so the anomaly surfaces.
#
# These used to be a local re-derivation of the same regex/range shapes
# (code-review finding #5, 2026-07-07) — `adapter_proxmox/naming.py` now
# exposes tier-tagged `is_te_*`/`is_tap_*` predicates (retagged from the
# earlier `is_axr_*` set, docs/WORK_ORDER_resource_tags.md) as the single
# source of truth; this module just aliases them so the call sites below
# read the same as before.
#
# Tier-aware since A1 (`docs/design/05-o2-surface.md`): `destroy_deploy`
# is reused verbatim by BOTH a `te`-tier `axrange destroy` AND `o2control`'s
# `axrange o2 down` against its own `o2`-tier deploy — the positive shape
# checks must match whichever tier the DEPLOY BEING TORN DOWN was built under
# (`deploy.tier`), not always `te`. `vmid_ok` stays tier-agnostic: the
# 100000-999999 VMID range is shared by every engine-managed VM regardless of
# tier (`is_te_vmid`'s docstring); a resource's tier is determined by its NAME
# shape, checked separately by the protect guard below.
# ---------------------------------------------------------------------------
_vmid_ok = is_te_vmid

# One tier table, owned by `adapter_proxmox/naming.py` (`docs/WORK_ORDER_
# tier_split.md` review, MUST-FIX #5): `TIER_PREDICATES` (which foreign tiers
# exist, for `_protected()`'s refusal loop) and `TIER_SHAPE_CHECKS`
# (vnet_ok/zone_ok/vm_name_ok per DEPLOY tier, for the positive gate below)
# both come from there now, instead of two hand-maintained tables in this
# file that could silently drift apart (a tier registered in one but not the
# other is exactly the "no predicate owns this name, so nothing refuses it"
# failure mode a blast-radius guard must never have). This module still owns
# the CASCADE policy (`_CASCADE_ALLOWED_FOREIGN`, below) — whether a tier is
# ALLOWED to touch a foreign tier's resources is a teardown decision, not a
# naming-shape fact.
_TIER_SHAPE_PREDICATES = naming.tier_predicates()


def _shape_checks_for_tier(tier: str) -> tuple[
    Callable[[str], bool], Callable[[str], bool], Callable[[str], bool],
    Callable[[str], bool],
]:
    """(vnet_ok, zone_ok, gretap_ok, vm_name_ok) for the deploy's OWN tier.

    `vnet_ok`/`zone_ok`/`vm_name_ok` come from `naming.tier_shape_checks()`
    (the single source of truth, MUST-FIX #5 above) — `vm_name_ok` (code-
    review N1, A1 follow-up) is the symmetric positive gate the vnet/zone/
    gretap steps already had and the VM step lacked: it defends the REVERSE
    direction from `_protected()` (an `o2`-tier destroy accidentally
    touching a `te-*`-named VM) — unreachable today since an `o2` plan only
    ever records `o2-*` names, but cheap defense-in-depth to keep the VM
    step's guard shape symmetric with every other resource kind.

    `gretap_ok` is DERIVED from `_CASCADE_ALLOWED_FOREIGN`, not hard-coded to
    `is_tap_gretap` for every tier (review MUST-FIX #6): `_protected()`
    already refuses a `tap_*`-shaped name for any tier that ISN'T allowed to
    cascade into `tap` (today, every tier except `te`), so a positive
    `is_tap_gretap` slot for e.g. `tfc`/`exr`/`o2` was DEAD CODE that read
    like a permission it never actually granted (`_protected` fires first
    and refuses it regardless). Deriving it from the same cascade table both
    halves already agree on keeps the two readable as ONE policy instead of
    two that happen to agree today by coincidence.

    Fails CLOSED on an unrecognized tier (LES-088; review MUST-FIX #4) INSTEAD
    OF silently falling through to the `te` tuple for anything not explicitly
    `"o2"`/`"tfc"`/`"exr"` (the OLD behavior — a trap for the very next tier
    wired into `deploys.tier` before it's registered in `naming.
    TIER_SHAPE_CHECKS`, e.g. `tap_points` promoting `"tap"` to a real
    deploy tier: it would silently borrow `te`'s shapes and pass the positive
    gate). An all-`False` tuple only closes the DANGEROUS half of that trap
    (no more borrowed shapes) — by itself it does NOT stop
    `underlay_bridge_for_plan(deploy.plan_id, tier=own_tier)` (step 7, below
    in `destroy_deploy`) from still raising a bare `ValueError` for that same
    unrecognized tier, since that call doesn't go through this function at
    all. THAT crash is what `destroy_deploy` actually prevents (review
    MUST-FIX #4, second round): it validates `own_tier` against `naming.
    TIER_SHAPE_CHECKS` and raises the typed `TeardownError` BEFORE calling
    this function or touching a single resource — so in practice this
    function is never invoked with a tier `destroy_deploy` hasn't already
    validated. The all-`False` fallback here remains as a defense-in-depth
    backstop for any OTHER caller of `_shape_checks_for_tier` this module
    gains later, not the primary fix. This does NOT promote `"tap"` to a
    deploy tier — `docs/WORK_ORDER_tap_points.md` deliberately decided
    AGAINST that promotion (see `axrange.o2control.taps`'s module docstring
    for the reasoning); `tap` stays a resource name-shape tag only."""
    base = naming.tier_shape_checks().get(tier)
    if base is None:
        _always_false: Callable[[str], bool] = lambda _n: False  # noqa: E731
        return _always_false, _always_false, _always_false, _always_false
    vnet_ok, zone_ok, vm_name_ok = base
    gretap_ok = (
        is_tap_gretap
        if "tap" in _CASCADE_ALLOWED_FOREIGN.get(tier, frozenset())
        else (lambda _n: False)
    )
    return vnet_ok, zone_ok, gretap_ok, vm_name_ok


# ---------------------------------------------------------------------------
# Six-tier cascade/protect matrix (`docs/design/02-twin-model.md`,
# `docs/WORK_ORDER_tier_split.md`): keyed by a deploy's OWN tier, the set of
# FOREIGN tiers that tier is allowed to sweep anyway (beyond its own shapes,
# which `_shape_checks_for_tier` already lets through positively).
#
# `te` -> `tap` is the ONLY entry, and it is LOAD-BEARING, not vestigial
# defense-in-depth (review MUST-FIX #3 — a prior comment here understated
# this and a future reader could delete it as "redundant"): a `te` deploy's
# OWN ops table indeed records no `tap_*` resources any more (sensor-agnostic
# te moved every real OP-MIR op out), but `axrange.o2control.taps` stamps its
# tap-plan playout deploy `tier="te"` TOO — `adapter_proxmox/naming.py`'s
# `_VALID_TIERS` has no separate `"tap"` deploy-level tier (see
# `taps.py`'s own docstring), so a tap-plan deploy's `ops` table is `tier=
# "te"` throughout while every row in it is `attach-gre-tunnel`/
# `install-ovs-mirror`/`verify-capture` producing `tapg_*`/`tapm-*` names.
# WITHOUT this exception, `_protected()` would refuse every one of those
# names as a foreign-tier violation the moment `tap-<te>`'s OWN `te`-tiered
# destroy tried to sweep them — i.e. `axrange tap destroy` /
# `axrange.o2control.taps.cascade_destroy_for_te` (which calls this exact
# `destroy_deploy` against the tap-plan's deploy) would turn into a
# guard-violation storm on every apply, leaving live `tc` mirrors + gretaps
# running on the cluster with the CLI reporting a "clean" but actually-
# nothing-swept destroy. Do not remove this entry without re-plumbing how
# the tap-plan deploy is tiered.
#
# No tier is ever allowed to sweep `o2`/`img` (unconditional, see
# `_protected` below) or another instrumentation/exercise tier's resources —
# `tfc` and `exr` are deliberately self-only per the design brief's
# dependency table (§2.1).
# ---------------------------------------------------------------------------
_CASCADE_ALLOWED_FOREIGN: dict[str, frozenset[str]] = {
    "te": frozenset({"tap"}),
}


# ---------------------------------------------------------------------------
# Backend protocol — the Proxmox-specific reverse operations.
# ---------------------------------------------------------------------------
# The reverse-of-create protocol is the platform seam `Teardown`
# (`axrange.platform.protocols`); this is its planner-side name, kept for the
# orchestrator's callers and the suite's fake backends.
TeardownBackend = Teardown


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------
@dataclass
class TeardownAction:
    """One resource the teardown considered, and what happened to it."""

    kind: str          # vm | mirror | gretap | vnet | zone | sdn-apply | ovs-bridge
    target: str        # the resource id (vmid / dev / vnet_id / ...)
    status: str        # removed | absent | guard-violation | error
    detail: str | None = None


@dataclass
class TeardownResult:
    deploy_id: str
    scenario_id: str
    plan_id: str
    actions: list[TeardownAction] = field(default_factory=list)
    destroyed: bool = False   # True iff a clean sweep transitioned the deploy

    @property
    def removed(self) -> int:
        return sum(1 for a in self.actions if a.status == "removed")

    @property
    def errors(self) -> list[TeardownAction]:
        return [a for a in self.actions if a.status in ("error", "guard-violation")]


class TeardownError(Exception):
    """Raised for an orchestration-level refusal (e.g. unknown deploy)."""


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------
def _outputs(outputs_json: str | None) -> dict[str, Any]:
    if not outputs_json:
        return {}
    try:
        d = json.loads(outputs_json)
    except (ValueError, TypeError):
        return {}
    return d if isinstance(d, dict) else {}


def _scope(scope_json: str | None) -> dict[str, Any]:
    if not scope_json:
        return {}
    try:
        d = json.loads(scope_json)
    except (ValueError, TypeError):
        return {}
    return d if isinstance(d, dict) else {}


# The op names `_sweep`'s resource-grouping loop (below) actually turns into
# a TEARDOWN ACTION -- exported so a caller that renders a removed op set
# without running the sweep itself (`cli.main`'s `--update` diff printer,
# `docs/WORK_ORDER_twin_incremental_update.md` code review S8) can tell an
# operator which removed ops genuinely revert something on the cluster vs.
# which ones (`provision-app`, `install-defense-agent`, `seed-dataset`,
# barriers, ...) have NO teardown counterpart at all -- their in-guest/
# state-only effect simply stays behind. Kept in sync by hand with the
# if/elif chain in `_sweep`; `test_sweepable_op_names_matches_the_sweep_chain`
# (tests/test_teardown.py) pins the correspondence so the two can't quietly
# drift apart.
SWEEPABLE_OP_NAMES: frozenset[str] = frozenset({
    "clone-vm", "attach-gre-tunnel", "create-vnet", "create-sdn-zone",
    "create-ovs-bridge",
})


def _sweep(
    deploy: DeployRow,
    ops: list[OpRow],
    backend: TeardownBackend,
    *,
    node: str | None,
    log: Callable[[str], None],
    protected_names: Iterable[str],
    protected_vmids: Iterable[int],
) -> TeardownResult:
    """The reverse-of-create resource sweep (steps 1-7: VM -> mirror ->
    gretap -> vnet -> zone -> sdn-apply -> ovs-bridge), scoped to exactly
    `ops` — every guard/gate/ordering rule identical regardless of who's
    calling. Shared by `destroy_deploy` (the WHOLE deploy's ops) and
    `destroy_ops_scoped` (an `--update`'s REMOVED subset only, `docs/
    WORK_ORDER_twin_incremental_update.md` §3.2/§4.2) so there is ONE sweep
    implementation, not two (LES-037) — the caller decides what follows a
    clean sweep (the deploy-wide state transition + lock/lease release, vs.
    a scoped per-op `destroyed` mark with locks/lease left to the `--update`
    run holding them for its ENTIRE duration).

    `node` overrides the PVE node used for node-scoped REST calls (VM destroy,
    OVS-bridge removal); it defaults to the deploy's recorded placement.

    `protected_names` is the operator's extra allowlist of operator-managed
    external nodes (`adapter.conf` `PROTECTED_EXTERNAL_NAMES`, parsed by
    `adapter_proxmox.config.load_config`) that must NEVER be destroyed by ANY
    tier, on top of the built-in `naming.PROTECTED_EXTERNAL_NAMES` (e.g.
    `XPress-SW`, the packetXpress sink/capture box) — see `_protected()`
    below. Every caller of this function should thread its loaded
    `AdapterConfig.protected_external_names` through here.

    `protected_vmids` is the review-hardening belt to `protected_names`'
    suspenders (code review, follow-on to the XPress-SW protection change):
    the name guard can only fire once `backend.vm_name()` resolves a display
    name, and a resolution failure (returns `None`) previously fell through
    to the ordinary vmid-range check — a fail-OPEN gap that could destroy a
    protected node if its name lookup ever flaked. `protected_vmids` (e.g.
    `AdapterConfig.protected_external_vmids`, auto-populated from every
    declared `external_sensors[].vmid`) is checked FIRST in the VM loop,
    before any name resolution is even attempted, so it can never be
    unprotected by a naming hiccup.

    Returns a `TeardownResult` whose `.destroyed` is always `False` (never
    set here — only a caller that actually transitions the DEPLOY sets it);
    inspect `.errors` for "was this sweep clean."
    """
    # This deploy's OWN tier (`docs/CONVENTIONS.md` §2) — selects which shape
    # a "this deploy's resource" positive match looks like. `te` (the default
    # for every normal scenario deploy) unless this is the standing O2
    # surface deploy `axrange.o2control` stood up (`tier="o2"`, torn down by
    # `axrange o2 down` reusing this exact function — see the module-level
    # comment above `_shape_checks_for_tier`).
    #
    # VALIDATED HERE, BEFORE ANYTHING ELSE RUNS (review MUST-FIX #4, second
    # round: `_shape_checks_for_tier`'s all-`False` fallback closed the
    # DANGEROUS half — no more borrowed `te` shapes — but an unrecognized
    # tier could still reach `underlay_bridge_for_plan(..., tier=own_tier)`
    # below (step 7) and raise a bare `ValueError` MID-SWEEP: after VMs/
    # gretaps/vnets/zones are already destroyed, and BEFORE the caller's own
    # lease release runs — leaving a held lease that then blocks the very
    # retry that would fix it. `taps.py`'s `except TeardownError` and the
    # CLI/API handlers don't catch a bare `ValueError` either, so it would
    # surface as an API 500 / CLI traceback. Failing loud with the module's
    # OWN typed error, HERE, before a single resource is touched, means:
    # nothing has been swept yet (nothing to half-clean-up), no lease was
    # ever released (nothing to leak), and every caller's existing `except
    # TeardownError` handling already covers it — for BOTH callers of this
    # function (LES-098: fail closed at every later use of `own_tier`, not
    # just the first check).
    own_tier = deploy.tier or "te"
    if own_tier not in naming.tier_shape_checks():
        raise TeardownError(
            f"deploy {deploy.deploy_id} has unrecognized tier {own_tier!r}; "
            "refusing to sweep"
        )
    _vnet_ok, _zone_ok, _gretap_ok, _vm_name_ok = _shape_checks_for_tier(own_tier)

    result = TeardownResult(
        deploy_id=deploy.deploy_id,
        scenario_id=deploy.scenario_id,
        plan_id=deploy.plan_id,
    )

    try:
        placement: dict[str, str] = json.loads(deploy.placement_json or "{}")
    except (ValueError, TypeError):
        placement = {}
    primary_node = node or (next(iter(placement.values()), None))

    # Group the created-resource identities out of the recorded op outputs.
    # SWEEPABLE_OP_NAMES (module-level, exported, above the if/elif chain
    # below) is the SAME set as this loop's op_names -- kept in sync by
    # hand; `test_sweepable_op_names_matches_the_sweep_chain` in
    # tests/test_teardown.py pins the correspondence.
    vms: list[tuple[int, str]] = []          # (vmid, node)
    gretaps: list[str] = []
    vnets: list[str] = []
    zones: list[str] = []
    bridges: list[str] = []
    for op in ops:
        out = _outputs(op.outputs_json)
        if op.op_name == "clone-vm" and out.get("vmid") is not None:
            host = _scope(op.scope_json).get("host")
            vm_node = (placement.get(host) if host else None) or primary_node or ""
            vms.append((int(out["vmid"]), vm_node))
        elif op.op_name == "attach-gre-tunnel" and out.get("gre_port"):
            gretaps.append(str(out["gre_port"]))
        elif op.op_name == "create-vnet" and out.get("vnet_id"):
            vnets.append(str(out["vnet_id"]))
        elif op.op_name == "create-sdn-zone" and out.get("zone_id"):
            zones.append(str(out["zone_id"]))
        elif op.op_name == "create-ovs-bridge" and out.get("bridge_name"):
            bridges.append(str(out["bridge_name"]))

    # De-dup while preserving discovery order.
    def _uniq(seq):
        seen: set = set()
        out = []
        for x in seq:
            if x not in seen:
                seen.add(x)
                out.append(x)
        return out

    gretaps, vnets, zones, bridges = (
        _uniq(gretaps), _uniq(vnets), _uniq(zones), _uniq(bridges),
    )
    seen_vm: set[int] = set()
    vms = [(v, n) for (v, n) in vms if not (v in seen_vm or seen_vm.add(v))]

    def _run(kind: str, target: str, fn: Callable[[], str]) -> None:
        try:
            status = fn()
        except Exception as e:  # noqa: BLE001 — record + continue (best-effort sweep)
            result.actions.append(TeardownAction(kind, target, "error", str(e)[:300]))
            log(f"  ! {kind} {target}: ERROR {str(e)[:160]}")
            return
        result.actions.append(TeardownAction(kind, target, status))
        log(f"  - {kind} {target}: {status}")

    def _record_guard(kind: str, target: str, detail: str) -> None:
        """Single append+log point for every guard-violation action (code
        review, follow-on to the XPress-SW protection change): `_guard`
        below, the protected-external branch, and the o2/img branch all
        recorded the same `TeardownAction(..., "guard-violation", ...)` +
        log-line shape independently — one helper, one place to get the
        wording right."""
        result.actions.append(TeardownAction(kind, target, "guard-violation", detail))
        log(f"  ! {kind} {target}: GUARD VIOLATION — {detail}")

    def _guard(kind: str, target: str) -> None:
        _record_guard(kind, target, "name is not a te/tap-deterministic name; refusing to touch")

    def _protected(kind: str, target: str) -> bool:
        """Hard protect guard (docs/WORK_ORDER_resource_tags.md §3, refined
        by `docs/design/05-o2-surface.md`, generalized to six tiers by
        `docs/design/02-twin-model.md` / `docs/WORK_ORDER_tier_split.md`):
        a destroy must NEVER remove a FOREIGN tier's resource — even if a
        positive same-tier shape-match above ever had a bug that let one
        slip through. Checked BEFORE the positive `_xxx_ok` checks so it
        fires independently of them (defense-in-depth).

        Two names are refused UNCONDITIONALLY, regardless of `own_tier`
        (checked first, in this order):

          1. A protected-EXTERNAL name (`is_protected_external` — e.g. the
             packetXpress `XPress-SW` sink/capture box, hand-created outside
             axRange's deploy lifecycle entirely): it outlives every tier, so
             even an `o2`/`tfc`/`exr` destroy of ITS OWN deploy must refuse
             it.
          2. `img_*` (a golden template): no tier's destroy, including its
             own, ever sweeps one — `img` is never a deploy tier at all.

        Every OTHER tier is refused unless it is `own_tier` itself, or an
        explicitly-allowed CASCADE target of `own_tier`
        (`_CASCADE_ALLOWED_FOREIGN` above — today just `te` -> `tap`, kept
        for defense-in-depth even though a `te` deploy's own ops table no
        longer records real `tap_*` resources post-sensor-agnostic-te). This
        is what makes the brief's §2.1 matrix true structurally: an `exr`
        destroy can never touch `tfc`/`tap`/`te`/`o2`/`img` (and vice-versa
        for every other tier), because the ONLY tier tag that ever passes
        this loop unrefused is `own_tier` (plus `te`'s one cascade
        exception)."""
        if is_protected_external(target, protected_names):
            _record_guard(kind, target,
                          "name is a protected external/sink node; refusing to touch")
            return True
        if is_img(target):
            _record_guard(kind, target,
                          "name matches a protected img_ resource; refusing to touch")
            return True
        allowed_foreign = _CASCADE_ALLOWED_FOREIGN.get(own_tier, frozenset())
        for tier_tag, shape_pred in _TIER_SHAPE_PREDICATES.items():
            if tier_tag == own_tier or tier_tag in allowed_foreign:
                continue
            if shape_pred(target):
                _record_guard(
                    kind, target,
                    f"name matches a protected {tier_tag}_ resource; refusing to touch",
                )
                return True
        return False

    # 1. VMs — stop + destroy (purge). Also drops the hookscript registration.
    #    Resolve the VM's actual PVE name FIRST (the Model-C name assertion
    #    this path previously lacked) and run it through the same protect
    #    guard as every other resource kind: once the O2 layer stands up
    #    VMs (`o2_*` names, docs/design/05-o2-surface.md), a te destroy must
    #    never sweep one even if it somehow ended up recorded against this
    #    deploy's ops. The te-VMID range check stays the secondary guard.
    #    Also a positive `_vm_name_ok` gate (code-review N1) mirroring the
    #    vnet/zone/gretap steps below — the reverse direction from
    #    `_protected` (an `o2` destroy refusing a `te-*`-named VM), applied
    #    only when the name resolved (a missing/unavailable name can't
    #    disprove ownership, same fallback posture `_protected` above uses).
    #
    #    VMID-based protection is checked FIRST, before any name resolution
    #    is even attempted (review hardening, follow-on to the XPress-SW
    #    protection change): the name guard above can only fire once
    #    `backend.vm_name()` resolves — a resolution failure (returns `None`)
    #    previously fell straight through to the vmid-range check, a fail-
    #    OPEN gap that could destroy a protected node on a naming hiccup.
    #    `protected_vmids` (e.g. `AdapterConfig.protected_external_vmids`) is
    #    resolution-independent: it can never be unprotected by a flaky
    #    lookup.
    _protected_vmid_set = set(protected_vmids)
    guarded_vmids: set[int] = set()
    for vmid, vm_node in vms:
        if vmid in _protected_vmid_set:
            _record_guard("vm", str(vmid),
                          "vmid is a protected external/sink node; refusing to touch")
            guarded_vmids.add(vmid)
            continue
        resolved_name = backend.vm_name(vm_node, vmid) if vm_node else None
        if resolved_name and _protected("vm", resolved_name):
            guarded_vmids.add(vmid)
            continue
        if resolved_name and not _vm_name_ok(resolved_name):
            _guard("vm", resolved_name)
            guarded_vmids.add(vmid)
            continue
        if not _vmid_ok(vmid):
            _guard("vm", str(vmid))
            continue
        if not vm_node:
            result.actions.append(TeardownAction(
                "vm", str(vmid), "error", "no PVE node resolved (empty placement)"))
            log(f"  ! vm {vmid}: ERROR no PVE node resolved")
            continue
        _run("vm", str(vmid), lambda v=vmid, n=vm_node: backend.destroy_vm(n, v))

    # 2. Mirror plane — the per-VM hook + cloud-init snippets (keyed by vmid).
    #    The VM destroy above already removed the hookscript *registration*; the
    #    snippet FILES persist in /var/lib/vz/snippets, so remove them here.
    #    Skip anything the VM step's protect guard already refused.
    for vmid, vm_node in vms:
        if vmid in guarded_vmids:
            continue
        if not _vmid_ok(vmid) or not vm_node:
            continue
        _run("mirror", str(vmid),
             lambda v=vmid, n=vm_node: backend.delete_mirror_snippets(n, v))

    # 3. Mirror plane — keyed gretap netdevs.
    for dev in gretaps:
        if _protected("gretap", dev):
            continue
        if not _gretap_ok(dev):
            _guard("gretap", dev)
            continue
        _run("gretap", dev, lambda d=dev: backend.delete_gretap(d))

    # 4. SDN vnets (must go before the zone — a zone with vnets won't delete).
    for vnet_id in vnets:
        if _protected("vnet", vnet_id):
            continue
        if not _vnet_ok(vnet_id):
            _guard("vnet", vnet_id)
            continue
        _run("vnet", vnet_id, lambda v=vnet_id: backend.delete_vnet(v))

    # 5. SDN zone.
    for zone_id in zones:
        if _protected("zone", zone_id):
            continue
        if not _zone_ok(zone_id):
            _guard("zone", zone_id)
            continue
        _run("zone", zone_id, lambda z=zone_id: backend.delete_sdn_zone(z))

    # 6. Commit the SDN removals cluster-wide (only if we removed any SDN object).
    if vnets or zones:
        _run("sdn-apply", "/cluster/sdn", backend.apply_sdn)

    # 7. OVS underlay bridge — the real blast-radius step. Gate HARD: only the
    #    name this plan's deterministic naming would produce for its VLAN-zone
    #    underlay. A Simple-zone `create-ovs-bridge` returns a `tev*` vnet name
    #    (already removed by the vnet step) — it is NOT a node-network bridge, so
    #    it never matches here and is correctly left alone.
    expected_underlay = underlay_bridge_for_plan(deploy.plan_id, tier=own_tier)
    for bridge_name in bridges:
        if _protected("ovs-bridge", bridge_name):
            continue
        if bridge_name != expected_underlay:
            # Not this plan's VLAN underlay (a Simple-zone vnet name, or anything
            # unexpected). Record it as skipped without touching the node network.
            result.actions.append(
                TeardownAction("ovs-bridge", bridge_name, "absent",
                               "not the VLAN underlay (SDN-managed or non-bridge); skipped")
            )
            continue
        if not primary_node:
            result.actions.append(TeardownAction(
                "ovs-bridge", bridge_name, "error", "no PVE node resolved"))
            log(f"  ! ovs-bridge {bridge_name}: ERROR no PVE node resolved")
            continue
        _run("ovs-bridge", bridge_name,
             lambda b=bridge_name, n=primary_node: backend.delete_ovs_bridge(n, b))

    return result


def destroy_deploy(
    store: StateStore,
    backend: TeardownBackend,
    deploy_id: str,
    *,
    node: str | None = None,
    log: Callable[[str], None] = lambda _m: None,
    protected_names: Iterable[str] = (),
    protected_vmids: Iterable[int] = (),
) -> TeardownResult:
    """Tear down every `te`/`tap`-tier resource of `deploy_id`, reverse order.

    The caller (CLI / API) is responsible for the `--allow-destroy` gate —
    this routine assumes consent and just does the work. See `_sweep`'s
    docstring for `node`/`protected_names`/`protected_vmids`.

    Returns a `TeardownResult`. The deploy is transitioned to `destroyed` only
    when no action errored or hit a guard violation (clean sweep); otherwise the
    deploy keeps its prior status so a re-run finishes the teardown.
    """
    deploy = store.get_deploy(deploy_id)
    if deploy is None:
        raise TeardownError(f"deploy not found: {deploy_id}")

    ops = store.ops_for_deploy(deploy_id)
    result = _sweep(
        deploy, ops, backend, node=node, log=log,
        protected_names=protected_names, protected_vmids=protected_vmids,
    )

    # State transition — only on a clean sweep.
    clean = not result.errors
    if clean:
        store.mark_destroyed(deploy_id)
        result.destroyed = True
        log(f"  deploy {deploy_id} -> destroyed")
    else:
        log(
            f"  deploy {deploy_id}: {len(result.errors)} resource(s) not cleared; "
            "left un-transitioned so a re-run finishes the teardown"
        )
    # Resource locks (`ovs:<node>` etc) are shared across *different*
    # scenarios' deploys (PLANNER §8.4) — releasing one while its resource
    # (e.g. the OVS underlay bridge) still exists would let a concurrent,
    # unrelated deploy start touching that same node before this teardown's
    # retry finishes clearing it: a real correctness race, not just a lease
    # concern. So only release them on a clean sweep (code-review finding #7,
    # 2026-07-07); on a partial failure they stay held until TTL expiry or a
    # later clean re-run releases them explicitly.
    if clean:
        store.release_resource_locks_for_deploy(deploy_id)
    # The lease, in contrast, is single-writer-per-*scenario* (not shared
    # across scenarios) and the CLI/API re-acquires it before every
    # destroy_deploy call (including retries) — so it always stays
    # unconditional: a destroy must never leave a held lease behind blocking
    # its own retry (§8 step 6).
    store.release_lease(deploy.scenario_id, deploy_id)
    return result


def destroy_ops_scoped(
    store: StateStore,
    backend: TeardownBackend,
    deploy_id: str,
    op_ids: Iterable[str],
    *,
    node: str | None = None,
    log: Callable[[str], None] = lambda _m: None,
    protected_names: Iterable[str] = (),
    protected_vmids: Iterable[int] = (),
) -> TeardownResult:
    """Tear down ONLY the resources recorded under `op_ids` for `deploy_id`
    — the REMOVED half of an `axrange te deploy --update`
    (`docs/WORK_ORDER_twin_incremental_update.md` §3.2/§4.2), reusing the
    exact same reverse-order sweep, `_protected()` guard, and idempotent
    check-then-act handlers `destroy_deploy` uses for a whole-deploy
    teardown (LES-037 — reuse, don't fork).

    Call this BEFORE `deploy.placement_json` is overwritten with the
    edited IR's placement: a removed host has no placement entry in a
    freshly-built plan (it isn't in the new IR at all), so its `vm_node`
    resolution here depends on the placement recorded by the ORIGINAL
    apply still being on the deploy row.

    Unlike `destroy_deploy`, this does NOT transition `deploy.status` (the
    deploy as a whole is still standing — only some of its resources are
    going away) and does NOT release the scenario lease or any resource
    locks: the `--update` caller holds those for the WHOLE run — the added-
    ops executor pass AND this removal sweep together (work order §4.4) —
    and releases them itself exactly once at the end, via the same
    `release_deploy_resources` path every other deploy already uses.

    On a clean sweep (no error/guard-violation), every id in `op_ids` — not
    only ones with an associated resource action (a removed BARRIER has
    none) — is marked `destroyed` (`StateStore.mark_ops_destroyed`),
    mirroring what `mark_destroyed` does for every op at full-deploy-
    teardown granularity. On a partial failure, nothing is marked, so a
    retried `--update` finishes the sweep — the same idempotent-resume
    posture `destroy_deploy` has.

    `result.destroyed` stays `False` regardless of outcome (see `_sweep`'s
    docstring: only a caller that transitions the whole DEPLOY sets it) —
    check `result.errors` for "did this scoped sweep succeed."
    """
    deploy = store.get_deploy(deploy_id)
    if deploy is None:
        raise TeardownError(f"deploy not found: {deploy_id}")

    wanted = set(op_ids)
    ops = [op for op in store.ops_for_deploy(deploy_id) if op.op_id in wanted]

    result = _sweep(
        deploy, ops, backend, node=node, log=log,
        protected_names=protected_names, protected_vmids=protected_vmids,
    )

    clean = not result.errors
    if clean:
        n = store.mark_ops_destroyed(deploy_id, wanted)
        log(f"  {n} op row(s) for deploy {deploy_id} -> destroyed (scoped)")
    else:
        log(
            f"  deploy {deploy_id}: {len(result.errors)} resource(s) not "
            "cleared in this scoped sweep; left un-marked so a retried "
            "--update finishes it"
        )
    return result
