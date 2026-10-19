"""axrange.o2control.taps -- the tap config playout (A2, `docs/design/06-taps-sensors.md`).

Binds a deployed TE's interfaces to standing O2 sensors (A1's inventory),
realized by directly composing OP-MIR ops (`attach-gre-tunnel` /
`install-ovs-mirror` / `verify-capture`) against the TE's ALREADY-DEPLOYED
bridges/vmids/topology and running them through the SAME engine executor
(LES-037) the rest of the system uses. Its own `tap_`-tier lifecycle
(`docs/design/06-taps-sensors.md`) -- independently apply/destroy/redeployable,
never touching the target TE's or the standing O2 surface's own resources.

**The te substrate is fully tap-agnostic** (`docs/WORK_ORDER_tap_eligibility.md`,
Authority: user, 2026-07-28 -- supersedes the sensor-agnostic-te framing
below): the te planner emits NO OP-MIR op at all, not even declarative
tap-intent bookkeeping (`record-tap-intent`/OP-MIR-005 is RETIRED from the
catalog). This module's `_scan_te_inventory` discovers the TE's ELIGIBLE
interfaces from `axrange.planner.inventory.build_twin_inventory`'s published
`hosts[].nets[]` -- the twin's FULL attachment topology -- instead of any
tap-gated subset. ANY port a binding names is eligible; eligibility, scope,
and target are entirely THIS tier's authority now. A2 remains the SOLE
installer of the three real OP-MIR ops above.

(Historical: sensor-agnostic te, 2026-07-13,
docs/WORK_ORDER_sensor_agnostic_te.md, first moved mirror/gretap/heartbeat
INSTALLATION out of the te planner and into this module, while the te still
recorded a `tap:`-resolved subset for this module to discover from. The
2026-07-28 change above removed that recorded subset too -- the te now
carries no tap-related state whatsoever.)

Why this does NOT go through `axrange.planner.build_plan` / `pass1_emit`
(unlike a normal scenario deploy, or A1's `stand_up_surface`): those build a
plan from an IR that describes resources to CREATE. A tap-plan binds
resources that ALREADY EXIST (the TE's bridges/VMs, the O2 sensors' transport
IPs) -- there is nothing to emit from an IR walk. So this module composes a
`Plan` BY HAND out of `OpInstance`s (reusing `axrange.ops.registry` for each
op's catalog metadata, exactly as `pass1_emit._make_op` does, but without
depending on that module's private helpers -- this file must stay import-clean
of `planner/pass1_emit.py`, which a concurrent thread is editing), then runs
it through `axrange.planner.Executor` via the SAME `run_deploy_executor` /
`release_deploy_resources` helpers `axrange.o2control.control.stand_up_surface`
uses (`axrange.api.service`) -- no second "build plan, run executor, release
resources" implementation (LES-037). Teardown reuses `planner.teardown.
destroy_deploy` verbatim, same as `axrange.o2control.control.tear_down_surface`.

Boundaries (LES-025): this module imports the engine (`planner`,
`adapter_proxmox`, `ops.registry`) -- downward only -- plus its sibling
`axrange.o2control.control` (read-only: `surface_status`, `SensorInventoryEntry`)
for the A1 sensor inventory. It does NOT modify `control.py`. The engine never
imports `o2control`.

Design notes (why the pieces are shaped this way):

  * **`install-ovs-mirror` uses a STABLE op_id (`{host}`, no content hash) and
    is FORCE-RESET to `pending` on every apply -- not content-addressed.**
    `attach-gre-tunnel[$net@$sensor]` and `verify-capture[$net@$sensor@$host]`
    are naturally content-addressed by scope (a binding on the SAME net+sensor
    always yields the SAME op_id, and an unchanged pair is correctly a no-op
    skip). `install-ovs-mirror[$host]` is different: its adapter implementation
    REGENERATES THE WHOLE mirror hook script from the `tapped_nics` input on
    every call (LES-011: the *installer* must always supply the complete
    desired state -- append-not-replace is a caller obligation, not something
    the adapter does for you). An EARLIER version of this module content-hashed
    `install-ovs-mirror`'s scope (`{host, gen}`) to get "unchanged -> skip,
    changed -> fresh op_id" for free -- but that gives DEDUP, not
    RE-ASSERTION: a narrow/revert (apply corp->[px1,suri1], then apply
    corp->[px1] again) recomputes the SAME desired content as an EARLIER,
    already-`completed` op_id, so the executor SKIPS it -- the cluster's tc
    mirred config, mutated by the INTERVENING apply, never gets reverted, and
    still silently copies traffic to the "removed" sensor (a real
    traffic-mis-routing / data-exposure bug, not just an orphaned resource).
    So `install-ovs-mirror` now uses the plain `{host}` scope and
    `apply_tap_plan` unconditionally force-resets every op_id in the freshly
    composed Plan (`StateStore.force_reset_ops`) before running the executor
    -- Model C's check-then-act still makes an unchanged host a cluster no-op
    (the adapter rewrites the identical script/asserts the identical tc
    state), and now also makes a narrow/revert correct (the adapter rewrites
    the ACTUAL current desired content, not a stale cached one).

  * **A drop-diff on every apply, and a de-scope pass on every destroy.**
    Dropping a binding (corp: [px1, suri1] -> corp: [px1]) must ALSO tear
    down suri1's now-unused gretap -- `apply_tap_plan` diffs this tap-plan's
    PRIOR (net, sensor) pairs (read back from its own persisted
    `attach-gre-tunnel` ops) against the newly resolved ones and deletes the
    dropped pairs' gretaps *after* the mirror re-assertion above lands (so the
    TE host's tc config stops naming them before they're removed). The mirror
    side of the same problem -- `destroy_taps` fully removing a tap-plan --
    is `_revert_mirrors_to_te_original`: a best-effort de-scope pass that
    re-asserts every affected host's install-ovs-mirror back to EMPTY
    tapped_nics BEFORE the gretaps are deleted (reuses `_compose_ops` with an
    EMPTY binding set -- there is no TE-original wiring to fall back to any
    more, sensor-agnostic te, 2026-07-13). Both are best-effort: a TE
    whose VM is already gone (e.g. this runs as part of `cascade_destroy_for_te`
    AFTER the TE's own destroy) can't be reverted, and that failure must not
    block the actual gretap teardown that follows.

    **The drop-diff deletes gretap DEVICES, not (net, sensor) PAIRS
    (2026-07-25 fix, dev45 live diagnosis -- a real data-loss bug).**
    `gre_port_for_bridge_sensor(bridge, sensor)` (`naming.py`) keys the
    netdev on the underlay BRIDGE, not the net -- and in a VLAN zone every
    vnet shares ONE underlay bridge, so the device is really per
    (underlay-bridge, sensor), shared across every net routed to that
    sensor. A dropped (net, sensor) pair can therefore alias the SAME device
    a still-needed (net', sensor) pair uses; deleting by pair alone silently
    killed a live gretap the instant a re-shard apply finished (verify-
    capture and the apply status both stayed green -- LES-001 false-green).
    `_delete_dropped_gretaps` now diffs at the device level: a dropped
    pair's device is deleted only if no needed pair maps to it, otherwise
    it's kept and loudly logged as such.

  * **A separate deploy per target TE, not folded into the TE's own deploy.**
    `plan_id = f"tap-{target_te_deploy_id}"` (deterministic -- ONE tap-plan
    deploy per target TE, `StateStore.find_deploy_for_plan` resumes/re-points
    it exactly like a normal `axrange deploy` resumes a TE). This keeps
    `destroy taps` naturally scoped to ONLY the tap-plan's own ops (no `te_`
    ops ever land in this deploy's `ops` table, so `planner.teardown.
    destroy_deploy` can't touch the TE's VMs/vnets/zone even in principle --
    it only ever sees `attach-gre-tunnel`/`install-ovs-mirror`/`verify-capture`
    rows -- and `destroy_taps` also STRUCTURALLY re-checks that its recorded
    op names are a subset of exactly those three, in addition to the `tap-`
    plan_id-prefix signal, so a real TE deploy coincidentally named `tap-*`
    can never be swept by `o2 tap destroy`). `tier="te"` is stamped on this
    deploy (not `"tap"` -- `tap` is a NAME-shape tag on individual resources,
    `adapter_proxmox/naming.py`'s `_VALID_TIERS` has no `"tap"` deploy-level
    tier and `planner/teardown.py` unconditionally calls
    `underlay_bridge_for_plan(plan_id, tier=own_tier)` on any destroy, which
    would raise on an invalid tier).

    **`docs/WORK_ORDER_tap_points.md` §2's open design call, decided: `tap`
    stays a RESOURCE name-shape tag, NOT a `_VALID_TIERS` deploy-level tier,
    in this build.** `tier_split` deliberately left this open (`naming.py`
    already carries a `tap` row in `TIER_PREDICATES` -- the "which foreign
    tiers exist to refuse" table -- for exactly this future promotion;
    `TIER_SHAPE_CHECKS`, the "what does THIS tier's own resource look like"
    table, deliberately has NO `tap` row, since `deploys.tier` is always one
    of `_VALID_TIERS` and never `"tap"` today -- corrected here, code
    review, from an earlier draft that wrongly said `tap` was already in
    BOTH tables). Reasoning for deferring it here, not doing it:

      1. **Scope discipline.** This work order is "capture wiring only"
         (tap-point vocabulary + resolution + decoupling). Promoting `tap`
         to a real deploy tier means threading it through `_VALID_TIERS`,
         `TIER_SHAPE_CHECKS`, `underlay_bridge_for_plan`, and — per LES-097 —
         re-verifying the FULL six/seven-tier teardown protect matrix
         end-to-end. That is real, separate work with its own blast-radius
         review, not a side effect of adding binding vocabulary.
      2. **No behavior gap it would close.** The `te -> tap` cascade
         exception in `planner/teardown.py`'s `_protected()` is LOAD-BEARING
         TODAY (LES-094) precisely because this deploy is `tier="te"` — and
         it already works, live-verified end-to-end (`docs/design/
         06-taps-sensors.md`'s dev45 run). Nothing in THIS order's
         deliverable (tap-point resolution, selector matching, tap_point/
         selector addressing) needs a new deploy tier to function — a
         tap-point-addressed binding resolves to the exact same
         `(host, net, nic) -> {sensor_id}` map a `tap:`-addressed one does,
         composed through the exact same `_compose_ops`.
      3. **Fail-closed is unaffected either way (LES-098).** `tap` is NOT
         added to `naming._VALID_TIERS` here, so `destroy_deploy`'s tier
         guard (`TeardownError` on an unrecognized tier) is exercised
         EXACTLY as before for any deploy that might carry `tier="tap"` by
         mistake — see `tests/test_teardown.py`'s defensive coverage added
         alongside this decision.

      A future order that DOES want `tap` as a first-class deploy tier
      (e.g. so `list-deploys --tier tap` lists genuine rows instead of
      relying on the `tap-`-prefix + op-name-subset structural discriminator,
      `is_tap_plan_deploy`) should treat that as its own scoped change, re-
      running the full teardown-matrix verification `tier_split` did for
      `tfc`/`exr`.

  * **A `te` destroy cascades to its standalone tap-plan** (`docs/design/
    06-taps-sensors.md`). Because the tap-plan is its OWN deploy
    (previous bullet), a plain `axrange destroy <te>` can't discover it
    structurally -- so the CLI `destroy` verb and `DELETE /api/deploys/{id}`
    (both in `axrange.cli.main` / `axrange.api.service`, NOT this module) call
    `cascade_destroy_for_te` right after the TE's own teardown, which looks up
    `tap-<te_deploy_id>` via `StateStore.find_deploy_for_plan` and destroys it
    too if present -- best-effort, never blocking or overriding the TE's own
    destroy result.
"""
from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterable, Optional

from axrange.adapter_proxmox.naming import gre_port_for_bridge_sensor

if TYPE_CHECKING:
    from axrange.adapter_proxmox.config import ExternalSensor
from axrange.ops.registry import get_op
from axrange.planner.dag import OpInstance, Plan, _op_id
from axrange.planner.inventory import InventoryError, TwinInventory
from axrange.planner.state import OpStatus
from axrange.planner.tappoints import (
    TAP_SCOPES,
    TapIndex,
    TapPoint,
    TapPointResolutionError,
    TapPointSelector,
    TapPointShapeError,
    build_tap_index,
    derive_default_tap_points,
    direction_for_scope,
    has_criteria,
    resolve_tap_point,
    scope_error_detail,
    segment_attached_hosts,
    select_tap_points,
)

# NOTE: `axrange.o2control.control` is imported lazily inside functions below
# (matching this package's existing style, `control.py`'s own docstring) to
# keep import-time side effects minimal and avoid import cycles with
# `axrange.o2control.__init__`.

# The op names a tap-plan deploy is EVER allowed to carry -- the structural
# half of `destroy_taps`'s guard (S4: the `tap-` plan_id prefix alone is a
# naming convention a real TE scenario could coincidentally collide with;
# this is immune to naming).
_TAP_OP_NAMES = frozenset({"attach-gre-tunnel", "install-ovs-mirror", "verify-capture"})


def _is_tap_shaped_op_names(op_names: set[str]) -> bool:
    """The structural half of the tap-plan discriminator (S4), shared by
    `destroy_taps`'s guard AND the read-path discovery helpers below
    (`is_tap_plan_deploy`/`latest_tap_plan_deploy`) -- NON-EMPTY and a
    subset of `_TAP_OP_NAMES`. `set() <= _TAP_OP_NAMES` is vacuously True,
    so the emptiness check matters: a zero-op deploy (e.g. a genuine `te`
    scenario named `tap-*` that hasn't had any ops recorded yet, or any
    other deploy shape with no ops on file) must never structurally pass as
    tap-shaped merely because the empty set is a subset of everything."""
    return bool(op_names) and op_names <= _TAP_OP_NAMES


class TapPlanError(Exception):
    """A tap-playout failure with an HTTP-ish status hint -- the tap-plan
    counterpart to `axrange.o2control.control.O2ControlError` (same shape,
    kept as an independent class since A2 is a separate concern from A1's
    surface stand-up, `docs/design/06-taps-sensors.md` -- "add alongside", not
    "extend", `control.py`)."""

    def __init__(self, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


# ---------------------------------------------------------------------------
# Tap-plan spec (F3, `docs/design/06-taps-sensors.md`) -- a standalone binding
# matrix keyed to a TE deploy + resolved against A1's sensor inventory.
#
#   tap-plan:
#     target: <te-deploy-id-or-scenario-name>   # OPTIONAL (docs/WORK_ORDER_
#                                                # tap_target_resolution.md) --
#                                                # omit entirely for a
#                                                # PREDEFINED/library plan and
#                                                # supply --target at apply
#                                                # time instead. Either a
#                                                # literal deploy id or a
#                                                # scenario/twin-config NAME
#                                                # (resolved to its newest
#                                                # live `te` deploy).
#     bindings:
#       - tap: corp-net            # a tapped NETWORK id -> fan-out to N sensors
#         sensors: [pktxpress-1, suricata-1]
#       - tap: dmz-host-h1         # a tapped HOST id -> exclusive to one sensor
#         sensors: [pktxpress-2]
#
# **Tap-point addressing (`docs/WORK_ORDER_tap_points.md`, evolves the above,
# does not replace it):** a binding may address its target through the
# semantic tap-point vocabulary instead of a raw net/host id --
#
#   bindings:
#     - tap_point: corp-lan             # a named tap point's `id`
#       sensors: [sensor-a]
#     - selector: { scope: boundary }   # matches every point the selector hits
#       sensors: [sensor-b]
#       mode: exclusive                 # fanout (default) | exclusive
#
# Exactly ONE of `tap` / `tap_point` / `selector` is required per binding
# (parse-time validated). `mode` is vocabulary only -- it does not change
# the underlying mechanism (`docs/design/06-taps-sensors.md`: a `sensors:`
# list of length 1 already IS exclusive, length N already IS fan-out); it
# exists so a tap-plan author can say what they mean and have it checked
# (`mode: exclusive` with >1 sensor is a parse-time error).
# ---------------------------------------------------------------------------
@dataclass
class TapBindingSpec:
    tap: Optional[str] = None
    tap_point: Optional[str] = None
    selector: Optional[dict[str, Any]] = None
    sensors: list[str] = field(default_factory=list)
    mode: str = "fanout"


@dataclass
class TapPlanSpec:
    # OPTIONAL (`docs/WORK_ORDER_tap_target_resolution.md`) -- a tap-plan can
    # now be PREDEFINED (no welded-in deploy id) and either name a scenario
    # (resolved to its newest live `te` deploy at apply time, `resolve_target_
    # deploy`) or omit `target:` entirely and rely on a `--target` CLI/API
    # override. `None` here means "no target declared in the plan itself" --
    # every APPLY/PLAN caller must still arrive at a concrete deploy id
    # before composing ops (`resolve_target_deploy`); only `tap validate`
    # tolerates `None` all the way through (semantics-only mode).
    target: Optional[str] = None
    bindings: list[TapBindingSpec] = field(default_factory=list)


def binding_label(
    b: TapBindingSpec, *, direction: Optional[str] = None, hint: bool = False,
) -> str:
    """A human-readable string standing in for whichever addressing mode a
    binding used -- `TapBindingResult.tap`/`TapBindingDTO.tap` predate
    tap-point/selector addressing and stay a single string for backward
    compatibility, so this is what's shown there (and in CLI rendering of a
    raw, unresolved `TapBindingSpec`) instead of a bare net/host id. Public
    (no leading underscore) so `axrange.cli.main` reuses it rather than
    re-deriving the same "which addressing mode did this binding use"
    logic (LES-037).

    `direction` (`docs/WORK_ORDER_tap_scopes.md` M6) -- appended as
    `" [rx]"`/`" [both]"` when the caller has a RESOLVED direction in hand
    (e.g. `TapPlanResolution.binding_directions`, populated once a binding
    is resolved against a live twin).

    `hint=True` (fix round F4, `docs/WORK_ORDER_tap_scopes.md` -- OFF by
    default) -- ONLY when no resolved `direction` was given, fall back to a
    best-effort STATIC hint for a `selector: {scope: ...}` binding (direction
    is a pure function of `scope` alone, `direction_for_scope` -- no live
    twin needed). This must stay opt-in: `TapPlanStatus`/`TapPlanStatusDTO`'s
    `tap`/`TapBindingDTO.tap` are machine-readable CONTRACT fields that
    predate tap-point addressing (`docs/WORK_ORDER_tap_points.md`) and must
    stay UNDECORATED regardless of scope -- an earlier, unconditional
    version of this hint leaked into them (and, when a caller ALSO appended
    its own resolved-direction suffix on top, double-rendered as `[rx] ...
    [rx]`). The ONE caller that wants the static hint (`axrange tap
    validate`'s pre-resolution, no-live-twin-yet render) opts in explicitly;
    every other caller (including every `TapPlanStatus`/`TapBindingDTO`
    construction) leaves it off and stays undecorated unless it ALSO passes
    a real resolved `direction`. Every caller that wants direction shown
    calls THIS function for it -- never re-derives the scope->direction
    mapping itself (LES-037)."""
    if direction is None and hint and b.selector is not None:
        scope = b.selector.get("scope")
        if isinstance(scope, str) and scope in TAP_SCOPES:
            direction = direction_for_scope(scope)
    suffix = f" [{direction}]" if direction else ""
    if b.tap is not None:
        return f"{b.tap}{suffix}"
    if b.tap_point is not None:
        return f"tap_point:{b.tap_point}{suffix}"
    return f"selector:{json.dumps(b.selector, sort_keys=True)}{suffix}"


def _binding_dedup_key(b: TapBindingSpec) -> tuple[str, str]:
    """The identity a duplicate-binding check dedupes on -- one binding per
    DISTINCT addressing target, regardless of which of the three addressing
    modes it used (mirrors `binding_label`'s precedence: `tap` >
    `tap_point` > `selector`)."""
    if b.tap is not None:
        return ("tap", b.tap)
    if b.tap_point is not None:
        return ("tap_point", b.tap_point)
    return ("selector", json.dumps(b.selector, sort_keys=True))


# `docs/WORK_ORDER_tap_scopes.md` M3 -- `wan` is the author-facing alias for
# the `__wan__` sentinel `axrange.planner.inventory`/`pass1_emit.py` actually
# store/emit (`attach-vm-to-vnet`'s `net_id`). The sentinel stays the value
# in `state.db` (renaming it there would mean migrating state, brief §5); the
# alias is resolved HERE, at the tap-plan boundary, so `tappoints.py` and
# every op-composition path downstream only ever see `"__wan__"`.
_WAN_ALIAS = "wan"
_WAN_SENTINEL = "__wan__"


def _resolve_wan_alias(value: Any) -> Any:
    """`wan` -> `__wan__`, wherever it appears as a net id: a bare string
    (`perimeter`/`segment` targets, or a legacy `tap:` net addressing), a
    `{host, net}` dict (`port` targets), or a 2-sequence (`boundary`
    targets). Anything else (including `None`) passes through unchanged.

    Caveat (nit, `docs/WORK_ORDER_tap_scopes.md` fix round): `"wan"` is
    reserved as the alias -- a twin that names one of its OWN segments
    literally `wan` cannot be addressed by that name through this function
    (it will always rewrite to the `__wan__` sentinel instead). Not fixed
    here; documented so a future author doesn't rediscover it as a
    mystery."""
    if value == _WAN_ALIAS:
        return _WAN_SENTINEL
    if isinstance(value, dict):
        if value.get("net") == _WAN_ALIAS:
            value = dict(value)
            value["net"] = _WAN_SENTINEL
        return value
    if isinstance(value, (list, tuple)):
        return type(value)(_WAN_SENTINEL if x == _WAN_ALIAS else x for x in value)
    return value


# `_binding_plausibly_targets_wan`/the zero-interface "redeploy with --fresh"
# WAN hint it fed REMOVED here (`docs/WORK_ORDER_tap_eligibility.md`,
# Authority: user, 2026-07-28) -- that LES-096 staleness was specific to
# `record-tap-intent`'s ADDITIVE `__wan__` entry, now retired along with the
# op itself. `_tapped_iface_set` sources the WAN leg from `attach-vm-to-vnet`
# directly (unconditional, and has been since before this change), so a WAN
# binding resolves identically on ANY deployed twin with a router -- no
# `--fresh` redeploy caveat left to hint at.


def _selector_from_dict(raw: dict[str, Any]) -> TapPointSelector:
    """`{id, scope, target, segment}` (the parse-time allowed selector keys,
    `_SELECTOR_KEYS` below) -> `TapPointSelector` -- THE one conversion
    point every selector-bearing binding goes through, whether at parse
    time (`parse_tap_plan`, a FILE/API-loaded tap-plan) or at resolve time
    (`_resolve_binding_targets`, a hand-built `TapBindingSpec` that
    bypassed `parse_tap_plan` entirely) -- so a malformed selector is
    refused IDENTICALLY either way (M2, `docs/WORK_ORDER_tap_points.md`
    code review).

    `segment` is a convenience alias for `target` (brief §4.1's own
    "target/segment" phrasing), used only when `target` itself is absent.
    Validates (raising `TapPlanError`, not a bare `ValueError`/`TypeError`
    an untrusted dict could otherwise trigger deep inside `TapPointSelector`
    consumers):

      * `scope`, if given, must be one of `TAP_SCOPES` -- an unrecognized
        scope would otherwise silently match NOTHING (`matches_selector`
        never sees a real `TapPoint` with that scope), which reads exactly
        like "the twin has none of these" instead of "you typo'd the
        selector" (SHOULD-FIX, code review).

    **No `labels` key** (`docs/WORK_ORDER_tap_eligibility.md` D3, Authority:
    user, 2026-07-28) -- the tap-plan grammar no longer accepts
    `selector: {labels: [...]}`; `_SELECTOR_KEYS` (below) drops it, so a
    `labels:` key in a FILE/API-loaded selector is now caught by
    `parse_tap_plan`'s existing "unknown key" check, loudly, not silently
    ignored. `TapPointSelector.labels`/`matches_selector`'s label-matching
    itself stays generic in `axrange.planner.tappoints` (a hand-built
    `TapPointSelector` in engine-side code may still set it) -- only THIS
    module's author-facing grammar stops accepting it, because there is no
    longer any declarative source (`derive_default_tap_points` never
    assigns `labels`) for an author to select against by that axis."""
    scope = raw.get("scope")
    if scope is not None and scope not in TAP_SCOPES:
        # `docs/WORK_ORDER_tap_scopes.md` M6: a RETIRED scope (`leg`/`host`)
        # gets its specific replacement guidance (`scope_error_detail`),
        # never a bare "unknown scope" that reads identically to a genuine
        # typo. Nit fix (fix round, taps.py:403 finding): "selector.scope:
        # ..." reads cleanly; the earlier "selector.{detail}" ran the two
        # words together ("selector.unknown scope").
        raise TapPlanError(f"selector.scope: {scope_error_detail(scope)}", status=400)
    target = raw.get("target", raw.get("segment"))
    target = _resolve_wan_alias(target)
    if isinstance(target, list):
        target = tuple(target)
    return TapPointSelector(id=raw.get("id"), scope=scope, target=target)


def parse_tap_plan(raw: dict[str, Any]) -> TapPlanSpec:
    """Sanity-check an already-loaded tap-plan dict (used directly by tests;
    `load_tap_plan` is the file-reading entry point)."""
    if not isinstance(raw, dict):
        raise TapPlanError("tap-plan file root must be a mapping", status=400)
    body = raw.get("tap-plan")
    if body is None:
        raise TapPlanError(
            "tap-plan file must have a top-level 'tap-plan:' key", status=400
        )
    if not isinstance(body, dict):
        raise TapPlanError("'tap-plan:' must be a mapping", status=400)

    # OPTIONAL (`docs/WORK_ORDER_tap_target_resolution.md`) -- a tap-plan can
    # now be PREDEFINED with no `target:` at all, relying entirely on a
    # `--target`/API-body override at apply time. If a `target:` IS given,
    # it must still be a non-empty string (a present-but-malformed target is
    # still a parse-time error, not silently coerced to "absent").
    target = body.get("target")
    if target is not None and (not isinstance(target, str) or not target):
        raise TapPlanError(
            "tap-plan.target, if given, must be a non-empty string", status=400
        )

    bindings_raw = body.get("bindings") or []
    if not isinstance(bindings_raw, list) or not bindings_raw:
        raise TapPlanError("tap-plan.bindings must be a non-empty list", status=400)

    # `docs/WORK_ORDER_tap_points.md`: a binding addresses its target via
    # EXACTLY one of the three vocabularies below -- the legacy raw
    # net/host `tap:` id, an auto-derived `tap_point:` id, or a `selector:`
    # (matched against `id`/`scope`/`target`/`segment`, `TapPointSelector`
    # -- NO `labels`, `docs/WORK_ORDER_tap_eligibility.md` D3: removed from
    # this grammar, there is no declarative source to select a label
    # against any more). `_SELECTOR_KEYS` is the `selector:` mapping's own
    # key allowlist.
    _SELECTOR_KEYS = {"id", "scope", "target", "segment"}
    bindings: list[TapBindingSpec] = []
    for i, b in enumerate(bindings_raw):
        if not isinstance(b, dict):
            raise TapPlanError(f"bindings[{i}] must be a mapping", status=400)
        tap = b.get("tap")
        tap_point = b.get("tap_point")
        selector_raw = b.get("selector")
        addr_present = [x is not None for x in (tap, tap_point, selector_raw)]
        if sum(addr_present) != 1:
            raise TapPlanError(
                f"bindings[{i}]: exactly one of 'tap' / 'tap_point' / "
                "'selector' is required", status=400,
            )
        if tap is not None and (not isinstance(tap, str) or not tap):
            raise TapPlanError(f"bindings[{i}]: 'tap' must be a non-empty string", status=400)
        if tap_point is not None and (not isinstance(tap_point, str) or not tap_point):
            raise TapPlanError(
                f"bindings[{i}]: 'tap_point' must be a non-empty string", status=400
            )
        selector: Optional[dict[str, Any]] = None
        if selector_raw is not None:
            if not isinstance(selector_raw, dict) or not selector_raw:
                raise TapPlanError(
                    f"bindings[{i}]: 'selector' must be a non-empty mapping", status=400
                )
            unknown = set(selector_raw) - _SELECTOR_KEYS
            if unknown:
                raise TapPlanError(
                    f"bindings[{i}]: selector has unknown key(s) "
                    f"{sorted(unknown)} (allowed: {sorted(_SELECTOR_KEYS)})",
                    status=400,
                )
            # M2 (code review): a selector with every key present but
            # DEGENERATE (`{id: null}`, `{scope: null, target: null}` -- a
            # YAML null for every key) would otherwise convert to an
            # all-unset `TapPointSelector`, which `matches_selector` accepts
            # vacuously for EVERY point -- "match everything" must never be
            # an emergent default (M1's sibling gap). `_selector_from_dict`
            # also validates `scope` shape here (raises `TapPlanError`), so
            # a malformed selector is refused at PARSE time, not just
            # resolve time.
            try:
                parsed_selector = _selector_from_dict(selector_raw)
            except TapPlanError as e:
                raise TapPlanError(f"bindings[{i}]: {e.message}", status=e.status) from e
            if not has_criteria(parsed_selector):
                raise TapPlanError(
                    f"bindings[{i}]: selector has no EFFECTIVE criterion "
                    "(id/scope/target/segment all unset or empty) -- 'match "
                    "every tap point' must never be an emergent default; "
                    "specify at least one", status=400,
                )
            selector = dict(selector_raw)

        mode = b.get("mode", "fanout")
        if mode not in ("fanout", "exclusive"):
            raise TapPlanError(
                f"bindings[{i}]: 'mode' must be 'fanout' or 'exclusive', "
                f"got {mode!r}", status=400,
            )

        sensors = b.get("sensors")
        if (
            not isinstance(sensors, list)
            or not sensors
            or not all(isinstance(s, str) and s for s in sensors)
        ):
            label = tap or tap_point or "selector"
            raise TapPlanError(
                f"bindings[{i}] ({label!r}): 'sensors' must be a non-empty "
                "list of sensor-id strings", status=400,
            )
        if mode == "exclusive" and len(sensors) != 1:
            raise TapPlanError(
                f"bindings[{i}]: mode=exclusive requires exactly 1 sensor, "
                f"got {len(sensors)}", status=400,
            )
        bindings.append(
            TapBindingSpec(
                tap=tap, tap_point=tap_point, selector=selector,
                sensors=list(sensors), mode=mode,
            )
        )

    keys_seen = [_binding_dedup_key(b) for b in bindings]
    dupes = sorted({k[1] for k in keys_seen if keys_seen.count(k) > 1})
    if dupes:
        raise TapPlanError(
            f"duplicate tap(s) in bindings[] -- express fan-out as one binding "
            f"with multiple sensors[], not repeated entries: {dupes}", status=400,
        )
    return TapPlanSpec(target=target, bindings=bindings)


def load_tap_plan(path: str | Path) -> TapPlanSpec:
    """Load + sanity-check a tap-plan YAML file. Raises `TapPlanError` on a
    structural problem (missing/malformed file, no target, no bindings)."""
    from axrange.ir.parser import IncludeError, StructuralError, load_yaml

    p = Path(path)
    try:
        raw = load_yaml(p)
    except (IncludeError, StructuralError) as e:
        raise TapPlanError(f"{p}: {e}", status=400) from e
    if raw is None:
        raw = {}
    return parse_tap_plan(raw)


def resolve_tap_plan(
    spec: Optional[TapPlanSpec], spec_path: Optional[str | Path]
) -> TapPlanSpec:
    """One spec wins, in order: an already-parsed `spec` object, or a
    `spec_path` to load. Mirrors `control.resolve_surface_manifest`'s shape."""
    if spec is not None:
        return spec
    if spec_path is not None:
        return load_tap_plan(spec_path)
    raise TapPlanError("apply_tap_plan needs spec= or spec_path=", status=400)


# ---------------------------------------------------------------------------
# Result shapes
# ---------------------------------------------------------------------------
@dataclass
class TapBindingResult:
    tap: str
    sensors: list[str] = field(default_factory=list)
    # The resolved mirror direction this binding produced -- usually "rx" or
    # "both", `None` if unresolved (`docs/WORK_ORDER_tap_scopes.md` M6).
    # NIT: can also be the joined form "both+rx" (`_resolve_bindings`'
    # `binding_directions[i]`) for the rare scope-less `selector:` that
    # matches points of DIFFERING scopes -- not a bare enum of exactly two
    # values. `tap` ABOVE no longer embeds it (fix round F4 -- opt-in
    # `hint=` only); this field is the one place a `--json` consumer reads
    # it as a real, undecorated value.
    direction: Optional[str] = None


@dataclass
class TapPlanStatus:
    deploy_id: Optional[str]
    target: str
    # "not-applied" | a DeployStatus value ("running"/"completed"/"failed"/...)
    status: str
    bindings: list[TapBindingResult] = field(default_factory=list)
    # Non-fatal apply-time findings -- e.g. "capture UNVERIFIED for external
    # sensor ..." (code review, follow-on to the config-external-sensor
    # feature: LES-001 forbids a SILENT "green deploy, no capture" outcome,
    # so a binding this apply composed with NO verify-capture heartbeat
    # surfaces here, not just as a `continue` inside `_compose_ops`). Always
    # populated by `apply_tap_plan`; empty for a plain `tap_status` read (a
    # persisted-state read has no compose pass to generate them from).
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# `axrange tap info` shapes (work order §3.2) -- richer than `TapPlanStatus`:
# per-(host,net,nic) bindings with the resolved GRE device name + destination
# sensor transport IP, not just a tap->sensors summary.
# ---------------------------------------------------------------------------
@dataclass
class TapBindingInfo:
    host: str
    net_id: str
    nic_index: int
    sensors: list[str] = field(default_factory=list)
    # sensor_id -> resolved gretap device name (per-(bridge,sensor), naming.py)
    gre_ports: dict[str, Optional[str]] = field(default_factory=dict)
    # sensor_id -> the sensor's standing transport IP (A1 inventory)
    sensor_transport_ips: dict[str, Optional[str]] = field(default_factory=dict)
    # sensor_id -> False for a config-declared EXTERNAL sensor (no A1 deploy_id,
    # `merge_external_sensors`'s `deploy_id=""` marker) -- these get NO
    # verify-capture heartbeat (axRange never QGA-reads an operator-managed
    # sink), so `tap info` renders an explicit "UNVERIFIED" line per sensor
    # rather than silently looking identical to a heartbeat-confirmed one.
    # True (the default) for a standing A1 sensor or an id not found at all.
    capture_verified: dict[str, bool] = field(default_factory=dict)
    # sensor_id -> mirror direction ("rx"/"both", `docs/
    # WORK_ORDER_tap_scopes.md` M4/M5) -- already unioned per (port, sensor)
    # by resolution; lets `tap info`/the API surface WHY a given (port,
    # sensor) pair mirrors one or both directions.
    directions: dict[str, str] = field(default_factory=dict)


@dataclass
class TapInfo:
    target: str
    target_status: str          # the target TE deploy's own status
    deploy_id: Optional[str]    # this tap-plan playout's OWN deploy id, if applied
    # "not-applied" | a DeployStatus value ("running"/"completed"/"failed"/...)
    status: str
    bindings: list[TapBindingInfo] = field(default_factory=list)
    fanout_note: str = (
        "Fan-out to N sensors duplicates the mirrored frame N times on the "
        "transport wire (one gretap per (net, sensor) pair) -- a capacity "
        "note, not a limitation."
    )
    # Non-fatal findings, e.g. an external-sensor id collision with a
    # standing A1 sensor (`merge_external_sensors`) -- per-sensor capture
    # verification status lives on each `TapBindingInfo.capture_verified`
    # instead, since that one IS per-(binding, sensor).
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# TE inventory -- everything this module needs to know about the ALREADY
# DEPLOYED target TE, read purely from `state.db` (the same "derive from
# persisted ops, not from re-parsing the scenario" posture `control.
# _inventory_from_ops` uses for A1's sensor inventory). No IR, no manifest.
# ---------------------------------------------------------------------------
def _json_obj(raw: Optional[str]) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        d = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return d if isinstance(d, dict) else {}


@dataclass
class _TeInventory:
    # host_id -> vmid (from clone-vm outputs)
    host_vmid: dict[str, int]
    # net_id -> OVS/vnet bridge name (from create-ovs-bridge outputs; a Simple
    # zone keys these per-net, a VLAN zone shares one underlay -- see below)
    net_bridge: dict[str, str]
    vlan_underlay: Optional[str]
    # host_id -> (nic_index, net_id) pairs for EVERY interface the te
    # published (`docs/WORK_ORDER_tap_eligibility.md` D1/D4, Authority:
    # user, 2026-07-28: read from `TwinInventory.hosts[].nets[]` -- the
    # twin's FULL attachment topology, sourced from `attach-vm-to-vnet`,
    # unconditional -- NOT a tap-gated subset. `record-tap-intent`, the
    # former source, is retired; there is no more "tapped" vs. "attached"
    # distinction -- any published interface is eligible. WAN uplinks are
    # INCLUDED (no special case needed: `HostNet.external=True` legs are
    # already carried in the source topology unconditionally). NO sensor is
    # attached here: the te wires no mirror of its own any more, so there
    # is nothing to preserve as a union baseline in `_compose_ops` -- every
    # apply's desired tapped_nics comes ENTIRELY from THIS tap-plan's own
    # bindings. Field name kept (`tappable`, not e.g. `attached`) -- purely
    # internal to this module, and every call site already reads it as
    # "the set a tap plan may bind against."
    tappable: dict[str, list[tuple[int, str]]]
    # host_ids the te's own deploy proved QGA-capable Linux (a `wait-os-ready`
    # op with os_family=linux) -- a preference signal for picking a new
    # verify-capture injector, not a hard requirement. Derived from the host
    # lifecycle (no te-side verify-capture exists to read any more).
    qga_hosts: set[str]
    # host_id -> the FULL set of things that host is attached to (ALL
    # `attach-vm-to-vnet` legs, INCLUDING a router's synthetic `__wan__`
    # uplink -- `docs/WORK_ORDER_tap_scopes.md` M2 now RECORDS it, see
    # `planner.inventory.HostNet.external`), NOT just its tapped ones. This
    # is the "is this host multi-homed" signal `_select_verify_injector`
    # uses: a host on >=2 legs is, in practice, the router a boundary tap
    # point resolves onto (only a router sits on more than one twin segment
    # -- and now, ALWAYS, since a router also always has the WAN leg) --
    # and ANY multi-homed host is an unsafe sentinel injector regardless of
    # role, since a route-less broadcast picks its egress NIC via the
    # DEFAULT ROUTE, which need not be the segment being verified.
    # DELIBERATE DECISION (M2 audit): the WAN leg is kept here, not filtered
    # -- it makes the "unsafe injector" signal MORE accurate (a router with
    # only ONE LAN leg is still multi-homed once its WAN leg counts, so it's
    # still correctly excluded as an injector), not less. Defaulted to `{}`
    # (unlike every OTHER field on this dataclass) so pre-existing direct
    # `_TeInventory(...)` constructions in the test suite -- built before this
    # field existed -- keep compiling unchanged; every REAL caller goes
    # through `_scan_te_inventory`, which always populates it.
    host_nets: dict[str, set[str]] = field(default_factory=dict)
    # host_id -> the raw `os_family` `wait-os-ready` recorded for that host
    # (2026-07-24, Windows verify-capture sentinel): unlike `qga_hosts` (a
    # LINUX-only membership set, a soft injector preference), this publishes
    # the ACTUAL value for every host that ran `wait-os-ready` -- Windows
    # included -- so `_compose_ops` can pick the right sentinel RENDERER
    # (`render_capture_sentinel` vs `render_capture_sentinel_windows`) for
    # whichever host `_select_verify_injector` lands on. Defaulted to `{}`
    # like `host_nets` above, for the same pre-existing-test-construction
    # reason.
    host_os_family: dict[str, str] = field(default_factory=dict)
    # (host_id, net_id) -> that leg's IP (`attach-vm-to-vnet`'s recorded `ip`
    # input, the SAME source `planner.inventory.HostNet.ip` reads) -- used to
    # resolve a segment's GATEWAY address (the multi-homed router's IP on
    # that net) for a Windows sentinel's unicast ping target (`.NET
    # Ping.Send` has no reliable broadcast, unlike POSIX `ping -b`). Defaulted
    # to `{}` like `host_nets`/`host_os_family` above.
    host_net_ip: dict[tuple[str, str], str] = field(default_factory=dict)
    # host_id -> `HostInfo.kind` (normalized "router"/"host"/... -- fix round
    # S9, `docs/WORK_ORDER_tap_scopes.md`: `_is_router_host` keys off THIS
    # when known, leg-count only as a FALLBACK (an earlier docstring
    # claiming "neither role/kind survives into `_TeInventory`" went stale
    # the moment `HostInfo.kind` was published, M2). `_is_route_ambiguous`
    # (fix round G3) deliberately does NOT consult this field at all --
    # see its own docstring. Defaulted to `{}` like
    # every other post-hoc field above -- LES-096: a deploy predating
    # `node_kind`/the `configure-router` back-fill may still have this
    # absent for a given host, so the leg-count fallback stays load-bearing,
    # not just a compatibility shim.
    host_kind: dict[str, str] = field(default_factory=dict)


def _bridge_for_net(inv: _TeInventory, net_id: str) -> Optional[str]:
    """`__wan__` needs NO special case here (fix round F3,
    `docs/WORK_ORDER_tap_scopes.md`; supersedes an earlier `_WAN_BRIDGE =
    "vmbr0"` local constant, DELETED) -- `planner.inventory.build_twin_
    inventory` now records the WAN attach op's own `vnet_id` input
    (`ROUTER_WAN_BRIDGE`, whatever the adapter's uplink bridge actually is)
    directly into `net_bridge["__wan__"]`, data-driven, correct on a
    cluster whose uplink bridge isn't literally `vmbr0`. A hardcoded
    constant here would ALSO have been a THIRD copy of that value
    (`pass1_emit.ROUTER_WAN_BRIDGE` + the adapter's own "vmbr0" fallback,
    `adapter_proxmox/rest.py`) that a real deploy's actual value could
    silently drift from."""
    return inv.net_bridge.get(net_id) or inv.vlan_underlay


def _gre_naming_bridge(
    inv: _TeInventory, net_id: str, plan_id: str,
) -> Optional[str]:
    """The bridge-name STRING fed into `gre_port_for_bridge_sensor`/
    `gre_key_for_bridge_sensor` for `net_id` -- `_bridge_for_net`'s value,
    BYTE-IDENTICAL for a twin-scoped bridge (`tebr_*`/`tev*`, already
    unique per twin -- `underlay_bridge_for_plan`/per-twin vnets), but
    discriminated with THIS tap-plan's own `plan_id` for the node-SHARED
    external/WAN uplink bridge (fix round G2, `docs/
    WORK_ORDER_tap_scopes.md`, final round).

    Why: `gre_port_for_bridge_sensor`/`gre_key_for_bridge_sensor`
    (`adapter_proxmox/naming.py`) are pure functions of `(bridge, sensor)`.
    Every bridge that had EVER entered that naming function before F3 was
    twin-unique BY CONSTRUCTION -- `vmbr0` (or whatever the cluster's real
    uplink bridge is) is the FIRST node-SHARED bridge to enter it. Two
    twins on one node, each binding `perimeter: wan` to the same sensor,
    would otherwise resolve to the IDENTICAL gretap device/key -- capture
    still works (merged per sensor by design), but `_delete_dropped_
    gretaps`'s device-level dedup (LES-118) only ever compares within ONE
    plan's own needed set, and `destroy_taps`/`ProxmoxTeardown.
    delete_gretap` deletes BY NAME -- so tearing down twin A's tap plan
    would silently kill twin B's WAN capture too. Restoring "every bridge
    feeding this naming function is twin-unique" here (rather than adding
    refcounting/ownership tracking at delete time) keeps the resource
    genuinely PER-TWIN, the same posture `tebr_*`/`tev*` already have.

    CRITICAL, do not "simplify": a twin-scoped bridge's returned string is
    UNCHANGED from before this fix -- discriminating it too would RENAME
    the gretap device on an ALREADY-DEPLOYED twin and orphan its live
    capture. `plan_id` (not `deploy_id`) is the discriminator: it is the
    STABLE `f"tap-{target}"` identity a tap-plan resumes under (`docs/design/
    06-taps-sensors.md`), so a re-apply of the SAME tap-plan
    still resolves the SAME device -- a fresh `deploy_id` per apply would
    not."""
    bridge = _bridge_for_net(inv, net_id)
    if bridge is None:
        return None
    if net_id == _WAN_SENTINEL:
        return f"{bridge}|{plan_id}"
    return bridge


def _scan_te_inventory(
    store: Any, te_deploy_id: str, *, twin: Optional[TwinInventory] = None,
) -> _TeInventory:
    """Thin adapter over `axrange.planner.inventory.build_twin_inventory`
    (review SHOULD-FIX #8, `docs/WORK_ORDER_tier_split.md`) — ONE derivation
    of "what does this TE's state.db say", not two that can silently drift
    apart. Reshapes the engine's tier-agnostic `TwinInventory` into this
    module's own `_TeInventory` shape; every CALLER of `_scan_te_inventory`
    is unchanged (same field names/types on `_TeInventory`), so this is a
    pure internal refactor.

    `twin=` (`docs/WORK_ORDER_tap_points.md`): pass an ALREADY-BUILT
    `TwinInventory` to skip the internal `build_twin_inventory` call --
    `resolve_tap_plan_bindings` needs the full `TwinInventory` anyway (tap-
    point derivation, below) and builds it exactly once, handing it to both
    this function and `derive_default_tap_points` rather than re-scanning
    `state.db` twice for the same deploy. Every EXISTING caller (none of
    which passes `twin=`) is byte-for-byte unchanged (LES-099: this widens
    the function's input, it does not narrow what it publishes).

    `host_vmid`/`tappable` are read straight off `TwinInventory`.
    `net_bridge`/`vlan_underlay` are read straight off `TwinInventory` TOO
    now (review MUST-FIX, second round) — an earlier version of this adapter
    reconstructed `net_bridge` NARROWLY from `tap_points` alone (only nets
    with a tap point) and hard-set `vlan_underlay=None`, which read as
    equivalent to the original inline scan (same field shapes) but actually
    SHRANK the key domain: the original scan's `net_bridge` covered every
    declared network regardless of tap status. That narrowing was invisible
    on the APPLY path (every net `_bridge_for_net` is queried for there
    always has a tap point, by construction) but bit on the DROP path
    (`_delete_dropped_gretaps`, below): a net_id from a PRIOR apply's
    recorded ops that no longer has a tap point in the CURRENT scan (a
    resumed redeploy with an edited `tap:`) would fail to resolve a bridge
    and silently orphan a gretap. `build_twin_inventory` now publishes the
    FULL map (+ the VLAN underlay) itself, so this reshape just republishes
    it unchanged — restoring the original key domain exactly, not
    reconstructing a narrower approximation of it. `qga_hosts` stays its own
    small residual scan: it's an A2-only "prefer this host as the
    verify-capture injector" signal, outside the published twin contract,
    not worth growing `build_twin_inventory` for.
    """
    if twin is None:
        from axrange.planner.inventory import build_twin_inventory

        twin = build_twin_inventory(store, te_deploy_id)

    host_vmid: dict[str, int] = {
        h.host_id: h.vmid for h in twin.hosts if h.vmid is not None
    }
    # Full segment-attachment set per host (ALL `attach-vm-to-vnet` legs,
    # `HostInfo.nets` -- not just tapped ones), the route-ambiguity signal
    # `_select_verify_injector` needs via `_is_route_ambiguous` (fix round
    # G3 -- leg-count only, deliberately NOT `kind`-based; LES-099:
    # republish the FULL key domain `twin.hosts` already covers, don't
    # narrow it to the tapped subset).
    host_nets: dict[str, set[str]] = {
        h.host_id: {n.net_id for n in h.nets} for h in twin.hosts
    }
    # `HostInfo.kind` (fix round S9) -- the load-bearing signal for
    # `_is_router_host` now (fix round G3 split this from the leg-count-only
    # `_is_route_ambiguous`), leg-count staying only a FALLBACK for a host
    # whose `kind` is unresolved (LES-096: a deploy predating `node_kind`/
    # the `configure-router` back-fill).
    host_kind: dict[str, str] = {
        h.host_id: h.kind for h in twin.hosts if h.kind
    }
    # `docs/WORK_ORDER_tap_eligibility.md` D1/D4 (Authority: user, 2026-07-28):
    # sourced from `twin.hosts[].nets[]` -- the FULL attachment topology --
    # not `twin.tap_points` (removed; `record-tap-intent`, the op that used
    # to populate it, is retired). Any published interface is eligible, WAN
    # uplinks INCLUDED (no special-case needed: `HostNet.external=True` legs
    # are already carried in `h.nets` unconditionally -- this is exactly what
    # closes the `docs/WORK_ORDER_tap_scopes.md` M3 WAN-eligibility special
    # case pass1_emit used to need, and its LES-096 "redeploy with --fresh"
    # caveat along with it: `attach-vm-to-vnet` is unconditional, so there is
    # no additive-input-never-backfills staleness left to worry about here).
    # Same "same rule `tappoints._tapped_ifaces` applies" DROP-not-coerce
    # discipline for a missing `nic_index` this dataclass has always used.
    tappable: dict[str, list[tuple[int, str]]] = {}
    for h in twin.hosts:
        for n in h.nets:
            if n.nic_index is None:
                continue
            entries = tappable.setdefault(h.host_id, [])
            pair = (n.nic_index, n.net_id)
            if pair not in entries:
                entries.append(pair)
    for entries in tappable.values():
        entries.sort()

    # (host, net) -> that leg's IP -- straight off `HostNet.ip` (same source
    # `attach-vm-to-vnet` populates `host_nets` from, above), needed to
    # resolve a segment's gateway address for a Windows sentinel's unicast
    # ping target (`_gateway_ip_for_net`, below).
    host_net_ip: dict[tuple[str, str], str] = {
        (h.host_id, n.net_id): n.ip for h in twin.hosts for n in h.nets if n.ip
    }

    qga_hosts: set[str] = set()
    host_os_family: dict[str, str] = {}
    for op in store.ops_for_deploy(te_deploy_id):
        if op.op_name != "wait-os-ready":
            continue
        scope = _json_obj(op.scope_json)
        inp = _json_obj(op.inputs_resolved_json)
        host = scope.get("host")
        osf = str(inp.get("os_family") or "").lower()
        if not host:
            continue
        if osf == "linux":
            qga_hosts.add(str(host))
        if osf:
            host_os_family[str(host)] = osf

    return _TeInventory(
        host_vmid=host_vmid, net_bridge=dict(twin.net_bridge),
        vlan_underlay=twin.vlan_underlay, tappable=tappable, qga_hosts=qga_hosts,
        host_nets=host_nets, host_os_family=host_os_family,
        host_net_ip=host_net_ip, host_kind=host_kind,
    )


def _tapped_iface_set(inv: _TeInventory) -> set[tuple[str, str, int]]:
    """The TE's ELIGIBLE interface set, as `(host_id, net_id, nic_index)`
    triples -- the twin's FULL attachment topology (`docs/
    WORK_ORDER_tap_eligibility.md` D1, Authority: user, 2026-07-28; no more
    `tap:`-resolved subset -- `record-tap-intent`/OP-MIR-005 is retired). A
    tap-plan binding may reference ANY identifier that resolves into this
    set."""
    out: set[tuple[str, str, int]] = set()
    for host, entries in inv.tappable.items():
        for nic, net_id in entries:
            out.add((host, net_id, nic))
    return out


def _sensor_vmid(store: Any, entry: Any) -> Optional[int]:
    """The O2 sensor VM's vmid, read from ITS OWN standing deploy (A1) --
    needed so `verify-capture` can read the sensor over QGA (`sensor_vmid`,
    OP_CATALOG par.5.3 / config_exec._verify_capture)."""
    for op in store.ops_for_deploy(entry.deploy_id):
        if op.op_name != "clone-vm":
            continue
        scope = _json_obj(op.scope_json)
        if scope.get("host") != entry.sensor_id:
            continue
        out = _json_obj(op.outputs_json)
        vmid = out.get("vmid")
        if vmid is not None:
            try:
                return int(vmid)
            except (TypeError, ValueError):
                return None
    return None


# ---------------------------------------------------------------------------
# Binding resolution -- `tap:` resolves to either a tapped NETWORK (fan-out
# across every host tapped on it -- segment semantics) or a tapped HOST
# (exclusive to that host's tapped NICs -- host semantics), per
# `docs/design/06-taps-sensors.md`'s host/leg/segment tap locations.
# ---------------------------------------------------------------------------
def _resolve_tap_target(
    ifaces: set[tuple[str, str, int]], tap: str
) -> list[tuple[str, str, int]]:
    tap = _resolve_wan_alias(tap)
    net_hits = sorted(i for i in ifaces if i[1] == tap)
    if net_hits:
        return net_hits
    # Fix round G7 (`docs/WORK_ORDER_tap_scopes.md`): since M3, a router's
    # synthetic `__wan__` uplink is one more entry in `_tapped_iface_set`
    # alongside its ordinary LAN legs -- so a legacy `tap: <router-host>`
    # binding (this branch; the caller already asked for `wan`/`__wan__`
    # explicitly above if that's what they wanted) would otherwise silently
    # pick up the WAN leg too, changing what an EXISTING host-addressed
    # binding captures with no warning at all. Host addressing keeps its
    # pre-M3 meaning -- "this host's own (LAN) interfaces" -- and excludes
    # the WAN leg; capturing the uplink is the new `perimeter` scope's job
    # (or the explicit `tap: wan`/`__wan__` net-hit branch above).
    host_hits = sorted(
        i for i in ifaces if i[0] == tap and i[1] != _WAN_SENTINEL
    )
    if host_hits:
        return host_hits
    return []


def _union_direction(a: Optional[str], b: Optional[str]) -> str:
    """`"both"` wins over `"rx"` (`docs/design/06-taps-sensors.md`) --
    the ONE place two directions for the SAME (port[, sensor]) are combined,
    reused at every granularity that needs it (within one selector's several
    matched points, and across several bindings, `_resolve_bindings` below).
    `None` (nothing yet) loses to any real direction."""
    return "both" if "both" in (a, b) else (b if a is None else a)


def _resolve_binding_targets(
    b: TapBindingSpec,
    ifaces: set[tuple[str, str, int]],
    points: Optional[list[TapPoint]],
    twin: Optional[TwinInventory],
    index: Optional[TapIndex],
    *,
    warnings: Optional[list[str]] = None,
) -> tuple[dict[tuple[str, str, int], str], list[TapPoint]]:
    """`{(host, net, nic): direction}` -- every interface ONE binding
    addresses, regardless of which of the three addressing modes it used
    (`docs/WORK_ORDER_tap_points.md`), PLUS the `TapPoint`s actually matched
    (`[]` for a legacy `tap:` binding, which has no `TapPoint` concept) --
    the caller (`_resolve_bindings`) needs both: the per-target direction
    for sensor binding (`docs/WORK_ORDER_tap_scopes.md` M4), and the matched
    points for the M5 segment-coverage warning + M3 exclusivity bookkeeping.

    Direction (M4) is `direction_for_scope(point.scope)` for a `tap_point:`/
    `selector:` binding -- unioned across every matched point that names the
    SAME interface (`_union_direction`; a `target`-only selector with no
    `scope:` given can legitimately match points of different scopes, e.g.
    both a `port` point and a `boundary` point naming the same net). A
    legacy `tap:` binding has no `TapPoint`/scope to read, so its direction
    is inferred from WHICH branch of `_resolve_tap_target` matched: a net-id
    hit is `segment`-like (rx-only, potentially many ports on that net), a
    host-id hit is `port`-like (tx & rx, one binding naming one host's own
    interfaces).

    **S8 promotion (`docs/WORK_ORDER_tap_scopes.md`, brief §3):** brief §3's
    rule is "rx-only whenever the set contains >=2 ports; tx & rx when the
    set is a single port" -- but `direction_for_scope` alone applies §4's
    per-scope CONSTANT, so a `segment`/`boundary` point (or a legacy
    net-id-matched `tap:`) that happens to resolve to exactly ONE port would
    otherwise lose tx entirely, strictly WORSE than the duplication rx-only
    exists to avoid (a lone port cannot self-duplicate with itself). Any
    `"rx"` result that resolves to exactly one port is promoted to `"both"`
    here and logged into `warnings` (opt-in per caller, LES-089 posture) --
    this is a genuine per-RESOLUTION decision (the same scope can resolve to
    one port on one twin and several on another), not a scope-level
    constant, so it lives at resolution time, not in `direction_for_scope`
    itself.

    `tap:` stays the original, mechanical net/host lookup
    (`_resolve_tap_target`, unchanged, alias-resolved); `tap_point:`/
    `selector:` resolve through `points`/`twin`/`index` (all three required
    for those two modes -- `resolve_tap_plan_bindings` always supplies them;
    a caller that invokes `_resolve_bindings` directly with only `tap:`
    bindings, as every PRE-EXISTING test does, never needs them).

    M1 (code review): a binding with NONE of `tap`/`tap_point`/a selector
    carrying an EFFECTIVE criterion raises HERE -- at the RESOLUTION layer,
    not just `parse_tap_plan`'s parse-time check -- so a `TapBindingSpec`
    built directly (bypassing `parse_tap_plan` entirely, e.g. a caller that
    doesn't route through file/API parsing) can never silently resolve as
    "match every tap point in the twin.\""""
    if b.tap is not None:
        targets = _resolve_tap_target(ifaces, b.tap)
        if not targets:
            return {}, []
        resolved_tap = _resolve_wan_alias(b.tap)
        is_net_match = any(t[1] == resolved_tap for t in targets)
        # Fix round F2 (`docs/WORK_ORDER_tap_scopes.md`): `__wan__` is a net
        # id, so a plain net-id match here would otherwise infer `"rx"`
        # (ordinary segment semantics) -- but EVERY port on the WAN net is a
        # router uplink, i.e. `perimeter` semantics (`both`), on every
        # addressing path, not just `tap_point:`/`selector:`. Without this,
        # `tap: wan` silently captured only outbound-to-internet traffic
        # with no signal the inbound half was ever missing.
        if resolved_tap == _WAN_SENTINEL:
            direction = "both"
        else:
            direction = "rx" if is_net_match else "both"
            if direction == "rx" and len(targets) == 1:
                # S8 promotion (see docstring) -- a legacy net-id `tap:`
                # that happens to resolve to exactly one port.
                direction = "both"
                if warnings is not None:
                    warnings.append(
                        f"tap {b.tap!r} resolved to a single port -- "
                        "direction promoted from rx to both (a lone port "
                        "cannot self-duplicate, docs/design/"
                        "06-taps-sensors.md)"
                    )
        return {t: direction for t in targets}, []

    selector: Optional[TapPointSelector] = None
    if b.tap_point is None:
        selector = _selector_from_dict(b.selector or {})
        if not has_criteria(selector):
            raise TapPlanError(
                f"binding {binding_label(b)!r} has no addressing key (tap / "
                "tap_point / a non-empty selector) -- 'match every tap "
                "point' must never be an emergent default; every binding "
                "must name what it targets", status=400,
            )

    if points is None or twin is None or index is None:
        raise TapPlanError(
            "internal error: a tap_point/selector binding needs a resolved "
            "twin inventory (points=/twin=/index= missing)", status=500,
        )

    if b.tap_point is not None:
        matched = [p for p in points if p.id == b.tap_point]
        if len(matched) > 1:
            # SHOULD-FIX (code review): derived ids are unique WITHIN a
            # scope but not ACROSS scopes -- a net literally named
            # `corp--dmz` collides with a derived boundary id, a net named
            # `rtr-legs` with a derived host id. Refuse the ambiguity
            # rather than silently unioning both matches' interfaces into
            # one binding.
            raise TapPlanError(
                f"tap_point {b.tap_point!r} is ambiguous: it matches "
                f"{len(matched)} derived tap points across scopes "
                f"{sorted({p.scope for p in matched})} -- tap-point ids "
                "must be unique across scopes; rename one", status=400,
            )
    else:
        matched = select_tap_points(points, selector)  # type: ignore[arg-type]

    out: dict[tuple[str, str, int], str] = {}
    for p in matched:
        direction = direction_for_scope(p.scope)
        refs = resolve_tap_point(p, twin, index=index)
        if direction == "rx" and len(refs) == 1:
            # S8 promotion (see docstring) -- this specific POINT'S
            # resolution (not the whole binding, which may match several
            # points) is a single-port set.
            direction = "both"
            if warnings is not None:
                warnings.append(
                    f"tap point {p.id!r} ({p.scope}) resolved to a single "
                    "port -- direction promoted from rx to both (a lone "
                    "port cannot self-duplicate, docs/design/"
                    "06-taps-sensors.md)"
                )
        for ref in refs:
            t = ref.as_tuple()
            out[t] = _union_direction(out.get(t), direction)
    return out, matched


def _resolve_bindings(
    spec: TapPlanSpec,
    ifaces: set[tuple[str, str, int]],
    sensor_by_id: dict[str, Any],
    *,
    points: Optional[list[TapPoint]] = None,
    twin: Optional[TwinInventory] = None,
    index: Optional[TapIndex] = None,
    warnings: Optional[list[str]] = None,
    binding_directions: Optional[dict[int, str]] = None,
) -> dict[tuple[str, str, int], dict[str, str]]:
    """`(host, net, nic) -> {sensor_id: direction}` -- every tapped interface
    this tap-plan binds, the FULL set of sensors it fans out to (1 exclusive
    entry = exclusive, N = fan-out), AND the mirror direction each (port,
    sensor) pair needs (`"rx"` or `"both"`, `docs/WORK_ORDER_tap_scopes.md`
    M4). Validates every `tap:`/`tap_point:`/`selector:` + `sensors:`
    reference along the way.

    **Direction is UNIONED per (port, sensor)** across every binding that
    claims it -- `"both"` wins over `"rx"` (`_union_direction`): a port can
    legitimately appear in two bindings aimed at the SAME sensor with
    different directions (`segment: X` plus `port: {h, X}`), and the result
    must be ONE mirror carrying the union, never whichever binding happened
    to compose last (LES-034's "never a last-wins map for a fan-out key,"
    applied to direction the same way it already applied to the sensor
    set).

    `points`/`twin`/`index` (`docs/WORK_ORDER_tap_points.md`): the derived
    tap-point catalog, the twin inventory it was derived from, and the
    precomputed `TapIndex` (Perf) -- needed ONLY when a binding addresses
    via `tap_point:`/`selector:` -- every PRE-EXISTING `tap:`-only call
    site (this module's own legacy tests) omits them and is unaffected.

    `warnings` (M5): a `segment`-scope match (whether via a legacy net-level
    `tap:` hit, a `tap_point:` naming one, or a `selector:` matching one)
    that covers only a SUBSET of the net's attached hosts appends an
    explicit coverage warning here (an operator binding `it` must not
    silently believe they have "the segment" when 1 of 3 attached hosts is
    actually tapped) -- opt-in per caller, same posture as the external-
    sensor capture-UNVERIFIED warning (LES-089)."""
    out: dict[tuple[str, str, int], dict[str, str]] = {}
    # M3 (code review): track every binding's OWN claimed targets so an
    # `exclusive` binding's claim can be checked against every OTHER
    # binding's claims once all are resolved -- "exclusive" must be an
    # ENFORCED invariant, not a description.
    claims: list[tuple[TapBindingSpec, list[tuple[str, str, int]]]] = []

    for i, b in enumerate(spec.bindings):
        target_directions, matched_points = _resolve_binding_targets(
            b, ifaces, points, twin, index, warnings=warnings,
        )
        targets = sorted(target_directions)
        if not targets:
            # `docs/WORK_ORDER_tap_eligibility.md`: the former WAN-specific
            # "redeploy with --fresh" hint (fix round F5, `docs/
            # WORK_ORDER_tap_scopes.md`) is REMOVED -- that staleness was
            # specific to `record-tap-intent`'s additive `__wan__` entry,
            # now retired along with the op. A zero-interface resolution
            # here means the named net/host/tap-point genuinely doesn't
            # exist in this twin's topology, full stop.
            if b.tap is not None:
                raise TapPlanError(
                    f"tap {b.tap!r} is not a network or host in "
                    f"target TE {spec.target!r}", status=400,
                )
            raise TapPlanError(
                f"binding {binding_label(b)!r} resolved to zero "
                f"interfaces in target TE {spec.target!r}", status=400,
            )

        if binding_directions is not None:
            # `docs/WORK_ORDER_tap_scopes.md` M6 -- a summary direction for
            # THIS binding, shown by `binding_label`/the CLI renderers. A
            # binding almost always resolves to exactly one direction (its
            # scope determines it); the rare scope-less `selector:` that
            # matches points of DIFFERING scopes shows the sorted, joined
            # set rather than silently picking one.
            binding_directions[i] = "+".join(sorted(set(target_directions.values())))

        # M5: segment-coverage warning. A legacy `tap:` binding counts as
        # "segment" addressing when it matched via NET id (the same
        # `_resolve_tap_target` net_hits branch, not the host_hits
        # fallback) -- re-derived here from whether `b.tap` (alias-resolved)
        # itself equals one of the resolved targets' net_ids (cheap, no
        # extra state).
        if twin is not None and warnings is not None:
            seg_net_ids: set[str] = {p.target for p in matched_points if p.scope == "segment"}
            if b.tap is not None:
                target_net_ids = {t[1] for t in targets}
                resolved_tap = _resolve_wan_alias(b.tap)
                if resolved_tap in target_net_ids:
                    seg_net_ids.add(resolved_tap)
            for net_id in sorted(seg_net_ids):
                attached = segment_attached_hosts(twin, net_id, index=index)
                covered = {t[0] for t in targets if t[1] == net_id}
                missing = sorted(h for h in attached if h not in covered)
                if missing:
                    warnings.append(
                        f"tap point {net_id!r} (segment) covers "
                        f"{len(attached) - len(missing)} of {len(attached)} "
                        f"attached legs -- host(s) {', '.join(missing)} "
                        "are not tapped"
                    )

        sensors: list[str] = []
        for sid in b.sensors:
            sensor = sensor_by_id.get(sid)
            if sensor is None:
                raise TapPlanError(
                    f"binding {binding_label(b)!r}: unknown sensor {sid!r} "
                    "-- not in the standing O2 inventory (axrange o2 status)",
                    status=400,
                )
            if not sensor.transport_ip:
                raise TapPlanError(
                    f"binding {binding_label(b)!r}: sensor {sid!r} has no "
                    "transport IP yet (is the O2 surface fully up?)", status=400,
                )
            sensors.append(sid)
        for t in targets:
            direction = target_directions[t]
            entry = out.setdefault(t, {})
            for sid in sensors:
                entry[sid] = _union_direction(entry.get(sid), direction)
        claims.append((b, targets))

    # M3 enforcement: an `exclusive` binding's claimed interfaces must not
    # be claimed by ANY other binding (whether that other one is itself
    # exclusive or fanout) -- `mode` was previously a label only (parse-time
    # `len(sensors) == 1` check, nothing more), so a second binding could
    # silently fan an "exclusive" point out to an extra sensor.
    for b, targets in claims:
        if b.mode != "exclusive":
            continue
        target_set = set(targets)
        for other, other_targets in claims:
            if other is b:
                continue
            overlap = sorted(target_set & set(other_targets))
            if overlap:
                raise TapPlanError(
                    f"binding {binding_label(b)!r} is mode=exclusive but "
                    f"interface(s) {overlap} are ALSO claimed by binding "
                    f"{binding_label(other)!r} -- exclusive means no other "
                    "binding may target the same interface(s)", status=400,
                )
    return out


# ---------------------------------------------------------------------------
# Config-declared EXTERNAL sensors as a tap target (follow-on to the
# XPress-SW protection change) -- an operator-managed, hand-provisioned
# sensor (`adapter_proxmox.config.ExternalSensor`) that a tap binding can
# name WITHOUT it ever appearing in A1's standing O2 inventory. axRange never
# provisions or reaches into one of these -- see `ExternalSensor`'s
# docstring for the manual runbook. `deploy_id=""` is the marker used
# throughout this module to mean "not an A1 standing deploy, no QGA read is
# possible or permitted here" -- `_compose_ops`'s verify-capture step checks
# it explicitly (below) so this feature can never grow an implicit QGA/SSH
# reach into an externally-managed node.
# ---------------------------------------------------------------------------
def _external_to_inventory_entry(ext: "ExternalSensor") -> Any:
    """Map one config-declared `ExternalSensor` to a `SensorInventoryEntry`
    so it flows through the SAME resolution/composition path as an A1
    standing sensor, unchanged."""
    from axrange.o2control.control import SensorInventoryEntry

    return SensorInventoryEntry(
        sensor_id=ext.id, type=ext.type, transport_ip=ext.transport_ip,
        deploy_id="",
    )


def merge_external_sensors(
    sensor_by_id: dict[str, Any], external_sensors: Iterable["ExternalSensor"],
    *, warnings: Optional[list[str]] = None,
) -> dict[str, Any]:
    """`sensor_by_id` (built from A1's standing inventory) plus every
    config-declared external sensor, A1 winning on an id collision (a
    standing sensor is the authoritative source of truth for its own id if
    an operator's adapter.conf ever names the same id). Shared by every
    resolution/display path that builds a `sensor_by_id` map -- `resolve_tap_plan_bindings`
    below AND the CLI's own direct `tap info` reads (LES-069: rewire every
    consumer, not just the one under active test) -- so `tap validate`/`tap
    plan`/`tap info`/`tap apply` all resolve a config-external sensor
    identically; a naming/apply inconsistency between them is exactly the
    trap this helper exists to close.

    Code review (LES-001 family): A1-wins-on-collision is a SILENT swallow
    on its own -- an external sensor whose `id` happens to match a standing
    A1 sensor gets quietly dropped, and a binding that meant to reach the
    external sink instead wires to A1's (different) transport_ip; capture at
    the intended sink is then simply empty, with nothing in the result to
    explain why. When `warnings` is given, a collision appends a message
    there so the caller can surface it (same posture as `_compose_ops`'s
    `warnings=` -- opt-in per caller, the collision-resolution itself is
    unconditional either way)."""
    out = dict(sensor_by_id)
    for ext in external_sensors:
        if ext.id in out and warnings is not None:
            warnings.append(
                f"external sensor {ext.id!r} shadowed by a standing O2 sensor "
                "of the same id — using the standing sensor's transport_ip; "
                "rename the external declaration if this is unintended"
            )
        out.setdefault(ext.id, _external_to_inventory_entry(ext))
    return out


# ---------------------------------------------------------------------------
# Target resolution (`docs/WORK_ORDER_tap_target_resolution.md`) -- lets a
# tap-plan's `target:` (or a `--target`/API-body override) be EITHER an
# already-minted deploy id OR a scenario/twin-config NAME, resolved to the
# newest LIVE `te` deploy of that scenario. This is what makes a tap-plan
# PREDEFINABLE (reusable across redeploys of the same scenario) instead of
# welded to one deploy instance. Resolution happens BEFORE `plan_id =
# f"tap-{target}"` is ever computed -- everything downstream (the tap-plan's
# OWN deploy_id, `find_deploy_for_plan`, `_scan_te_inventory`, etc.) uses the
# CONCRETE resolved deploy id, never the raw name.
# ---------------------------------------------------------------------------
def resolve_target_deploy(store: Any, target: str) -> str:
    """`target` is EITHER an already-minted deploy id (checked first --
    `store.get_deploy` hit wins outright, no further interpretation) OR a
    scenario_id, in which case this resolves to the newest LIVE (non-
    destroyed) `te` deploy of that scenario, filtered by `scenario_id ==
    target` and re-checked against `is_tap_plan_deploy` (LES-082: a tap-plan
    playout deploy is ALSO `tier="te"`, so the structural op-name
    discriminator -- not a `plan_id`-prefix filter -- is what tells the two
    apart here). `deploys_matching` orders newest-first by `updated_at`
    (LES-084), so the first surviving candidate IS the newest live match.

    Code review (MUST-FIX): this does NOT ALSO pass `exclude_plan_id_prefix
    ="tap-"` to `deploys_matching` (unlike `latest_tap_plan_deploy`'s own
    query, which wants the OPPOSITE thing -- "find a tap-plan playout" --
    and so filters TO that prefix, `plan_id_prefix="tap-"`). Filtering OUT
    `tap-%` here would strip every plan_id `derive_plan_id` mints for a
    scenario literally NAMED `tap-*` (`tap-<name>-<hash>`) BEFORE the
    `scenario_id == target` check even runs -- a genuine, live `te` twin
    whose scenario is named e.g. `tap-decoy` would 404 by name, and the
    structural `is_tap_plan_deploy` check below would become unreachable
    dead code (nothing with a `tap-` plan_id would ever reach it). Filter to
    `scenario_id == target` FIRST, on the FULL unfiltered `tier="te"`
    candidate set, and let the structural check alone decide "is this
    actually a tap-plan playout" -- mirrors `latest_tap_plan_deploy`'s own
    pattern (prefix-filter to find candidates worth op-reading, structural
    check to confirm), just inverted since this function is looking for the
    opposite thing.

    Raises `TapPlanError` (404) if `target` resolves as NEITHER a deploy id
    NOR a live `te` deploy of a scenario by that name."""
    hit = store.get_deploy(target)
    if hit is not None:
        return target

    for row in store.deploys_matching(tier="te", exclude_destroyed=True):
        if row.scenario_id != target:
            continue
        if is_tap_plan_deploy(store, row):
            continue
        return row.deploy_id

    raise TapPlanError(
        f"tap-plan target {target!r} is neither a deploy id nor a live te "
        "deploy of a scenario by that name", status=404,
    )


def resolve_live_target_deploy(store: Any, target: str) -> Any:
    """`resolve_target_deploy` PLUS the "live, active twin" assertion
    `axrange tap validate`'s target-PRESENT mode requires (work order §6):
    the resolved deploy must exist, be tier="te", and must NOT be
    destroyed. `resolve_target_deploy`'s own scenario-NAME path already
    guarantees this (its `deploys_matching` call excludes destroyed rows and
    filters to tier="te") -- but its literal-deploy-id path does not (a
    caller can hand it an id for a destroyed or non-te deploy and it will
    happily return that id unchanged, by design: "it's a deploy id; use
    it"). This function re-checks explicitly so a caller that needs "must
    actually be live" (unlike `apply_tap_plan`/`tap plan`, which tolerate
    binding against a non-completed-but-existing te deploy) gets a hard,
    clearly-worded failure instead of silently proceeding.

    Returns the resolved `DeployRow` (not just its id) so a caller can also
    read `.status`/`.tier` without a second `store.get_deploy` round trip.
    Raises `TapPlanError` (400) on a resolved-but-not-live result -- a
    DIFFERENT failure mode than `resolve_target_deploy`'s own 404 ("doesn't
    resolve to anything at all").

    Code review (SHOULD-FIX): also rejects a resolved deploy that IS a
    tap-plan playout (`is_tap_plan_deploy`) -- a tap-plan playout is ITSELF
    `tier="te"` (LES-082, its own module docstring), so a literal `--target
    <tap-plan-playout-deploy-id>` would otherwise sail past every check
    above (it exists, is tier=te, isn't destroyed) and only fail much later
    and confusingly, deep in bindings resolution ("target TE has no tapped
    interfaces to bind"). Caught here with a clear, actionable message
    instead."""
    from axrange.planner.state import DeployStatus

    concrete = resolve_target_deploy(store, target)
    row = store.get_deploy(concrete)
    if row is None or (row.tier or "te") != "te" or row.status == DeployStatus.DESTROYED:
        raise TapPlanError(
            f"tap-plan target {target!r} resolved to deploy {concrete!r}, "
            "which is not a live, active te deploy (must exist, be tier=te, "
            "and must not be destroyed)", status=400,
        )
    if is_tap_plan_deploy(store, row):
        raise TapPlanError(
            f"target {target!r} resolved to deploy {concrete!r}, which is a "
            "tap-plan playout, not a twin -- pass the twin's deploy id or "
            "scenario name instead", status=400,
        )
    return row


# ---------------------------------------------------------------------------
# Bindings resolution against LIVE state, WITHOUT composing/executing
# anything -- shared by `apply_tap_plan` (which goes on to compose+run) and
# the read-only `axrange tap validate`/`tap plan`/`tap info` entry points
# (`docs/WORK_ORDER_cli_lifecycle.md` §3.2/§3.3). One place reads "TE
# inventory + sensor inventory + validated bindings" (LES-037 -- no second
# copy of this sequence).
# ---------------------------------------------------------------------------
@dataclass
class TapPlanResolution:
    target_status: str  # the target TE deploy's DeployStatus value
    inv: "_TeInventory"
    sensor_by_id: dict[str, Any]
    # `(host, net, nic) -> {sensor_id: direction}` (`docs/
    # WORK_ORDER_tap_scopes.md` M4) -- direction already unioned per
    # (port, sensor) by `_resolve_bindings`.
    iface_new_sensors: dict[tuple[str, str, int], dict[str, str]]
    # Non-fatal resolve-time findings (e.g. an external-sensor id collision
    # with a standing A1 sensor, `merge_external_sensors`) -- folded into
    # `apply_tap_plan`'s own `TapPlanStatus.warnings` by its caller.
    warnings: list[str] = field(default_factory=list)
    # `docs/WORK_ORDER_tap_points.md`: the tap-point catalog `tap_point:`/
    # `selector:` bindings resolved against (`derive_default_tap_points`) and
    # the full `TwinInventory` it was derived from -- kept here so a future
    # CLI/API surface (e.g. listing a twin's candidate tap points) can reuse
    # this SAME resolve pass rather than re-deriving it. Not currently read
    # by `apply_tap_plan`/`compose_tap_ops` (both stay purely
    # `iface_new_sensors`-driven, unchanged).
    points: list[TapPoint] = field(default_factory=list)
    twin: Optional[TwinInventory] = None
    # binding index (position in `spec.bindings`) -> resolved direction
    # summary (`docs/WORK_ORDER_tap_scopes.md` M6) -- read by `binding_label`
    # callers (`axrange.cli.main`) so every renderer shows a binding's
    # resolved direction without re-deriving it (LES-037).
    binding_directions: dict[int, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Capacity guardrail (`docs/WORK_ORDER_tap_eligibility.md` §7, Authority:
# user, 2026-07-28): full-topology eligibility (D1) makes "bind every port
# in the twin to one sensor" trivial to author, and EVERY (port, sensor)
# pair this resolves to is a DISTINCT GRE-mirrored copy sharing the same
# transport wire (fan-out literally duplicates each frame once per sensor
# it's routed to, `docs/design/06-taps-sensors.md`) -- a plan that resolves
# an unusually large or heavily fanned-out binding set can quietly approach
# that wire's real capacity with no signal an operator would otherwise see
# before applying. SOFT, NON-BLOCKING (the brief's own words: "name the
# duplication; proceed anyway. Not a hard cap.") -- folded into the same
# `warnings` list every other resolve-time advisory uses, so it surfaces
# identically at `tap plan`/`tap validate`(live)/`tap deploy`/`tap info`,
# every caller of `resolve_tap_plan_bindings` (below), with no per-caller
# wiring needed. Thresholds are a reasonable-default heuristic, not a
# ratified number -- adjust freely if real usage says otherwise.
_CAPACITY_WARN_PORTS = 50   # distinct (host, net, nic) interfaces bound
_CAPACITY_WARN_PAIRS = 100  # distinct (port, sensor) mirrored copies produced


def _capacity_warning(
    iface_new_sensors: dict[tuple[str, str, int], dict[str, str]],
) -> Optional[str]:
    """`None` under both thresholds; otherwise a message NAMING the actual
    port count / mirrored-copy count / heaviest single-port fan-out (the
    brief's "name the duplication") rather than a bare "this plan is big.\""""
    port_count = len(iface_new_sensors)
    if port_count == 0:
        return None
    pair_count = sum(len(sensors) for sensors in iface_new_sensors.values())
    if port_count <= _CAPACITY_WARN_PORTS and pair_count <= _CAPACITY_WARN_PAIRS:
        return None
    max_fanout = max(len(sensors) for sensors in iface_new_sensors.values())
    return (
        f"capacity: this plan binds {port_count} port(s), producing "
        f"{pair_count} mirrored (port, sensor) cop{'y' if pair_count == 1 else 'ies'} "
        f"on the transport wire (heaviest single-port fan-out: {max_fanout}x) "
        "-- each fan-out duplicates every frame once per sensor it's routed "
        "to; not a hard cap, but check transport-wire capacity before applying"
    )


def resolve_tap_plan_bindings(
    spec: TapPlanSpec, store: Any, *, db_path: str,
    external_sensors: Iterable["ExternalSensor"] = (),
) -> TapPlanResolution:
    """Resolve `spec`'s bindings against `store` (an ALREADY-OPEN
    `StateStore` -- the caller owns its lifecycle): the target TE must
    exist and be tier="te"; it must have ANY published interface at all
    (`hosts[].nets[]` -- effectively always true for a genuinely deployed
    twin, `docs/WORK_ORDER_tap_eligibility.md` D1); every named sensor must
    be standing (A1 inventory, read via `control.surface_status(db_path=...)` -- a second
    DB connection, which SQLite handles fine for concurrent readers) OR a
    config-declared `external_sensors` entry (`adapter_proxmox.config.
    ExternalSensor` -- an operator-provisioned sink like `XPress-SW`,
    `merge_external_sensors` above; A1 wins on an id collision).

    Every caller should pass its loaded `AdapterConfig.external_sensors`
    here (LES-069) -- `apply_tap_plan` AND the read-only `axrange tap
    validate`/`tap plan`/`tap info` entry points, so a tap-plan naming a
    config-external sensor resolves IDENTICALLY whether it's about to be
    applied or merely inspected.

    Raises `TapPlanError` on any structural problem. A caller that wants a
    softer "degrade to a warning" behavior (the offline half of `axrange tap
    validate`, work order §3.3) catches it itself -- this function always
    reports the true resolve outcome, never silently degrades.
    """
    from axrange.o2control.control import surface_status
    from axrange.planner.inventory import build_twin_inventory

    te = store.get_deploy(spec.target)
    if te is None:
        raise TapPlanError(f"target TE deploy not found: {spec.target!r}", status=404)
    if (te.tier or "te") != "te":
        raise TapPlanError(
            f"target {spec.target!r} is tier={te.tier!r}; a tap-plan target "
            "must be a target-environment ('te') deploy", status=400,
        )
    if is_tap_plan_deploy(store, te):
        # SHOULD-FIX (code review): a tap-plan playout is ITSELF `tier="te"`
        # (LES-082), so a literal `--target <tap-plan-playout-deploy-id>`
        # would otherwise sail past the tier check above and fail much
        # later and confusingly (e.g. "target TE has no tapped interfaces
        # to bind"). Every caller of this function (`apply_tap_plan`, `tap
        # plan`, `tap validate`'s live-resolution path) gets this guard for
        # free -- `resolve_live_target_deploy` carries the SAME guard for
        # its own, slightly earlier resolve step.
        raise TapPlanError(
            f"target {spec.target!r} is a tap-plan playout, not a twin -- "
            "pass the twin's deploy id or scenario name instead", status=400,
        )

    # Built ONCE here and threaded into BOTH `_scan_te_inventory` (so it
    # doesn't re-scan `state.db` a second time) and `derive_default_tap_points`
    # (`docs/WORK_ORDER_tap_points.md`) -- one derivation of "what does this
    # TE's state.db say" feeding every consumer, not two that could drift
    # apart (LES-099's own lesson, applied here too). `index` (Perf, code
    # review) is likewise built ONCE and threaded into both
    # `derive_default_tap_points` and every `resolve_tap_point`/
    # `segment_attached_hosts` call `_resolve_bindings` makes, instead of
    # each recomputing `_tapped_ifaces`/`_host_legs` from `twin`
    # independently on every matched point.
    #
    # Both calls are wrapped (code review, LES-098 posture): `te.tier` is
    # already pre-checked above so `build_twin_inventory`'s own `InventoryError`
    # can't fire today, and `derive_default_tap_points` only ever builds
    # well-formed `TapPoint`s from ALREADY-PUBLISHED, ALREADY-VALIDATED twin
    # topology so `TapPointShapeError` can't fire today either -- but
    # `TapPointShapeError` stays a real, reachable failure mode for a
    # hypothetical future author-declared tap-point source (there is none
    # today, `docs/WORK_ORDER_tap_eligibility.md` D3 closed that door), and a
    # malformed point there would otherwise escape this function as a bare,
    # uncaught exception (an API 500 / CLI traceback) instead of the
    # `TapPlanError`(400) every other resolve-time problem in this function
    # produces.
    try:
        twin = build_twin_inventory(store, spec.target)
        index = build_tap_index(twin)
        inv = _scan_te_inventory(store, spec.target, twin=twin)
        ifaces = _tapped_iface_set(inv)
        if not ifaces:
            raise TapPlanError(
                f"target TE {spec.target!r} has no interfaces to bind "
                "(the twin published an empty topology -- was it actually "
                "deployed?)", status=400,
            )
        points = derive_default_tap_points(twin, index=index)
    except (InventoryError, TapPointShapeError) as e:
        raise TapPlanError(str(e), status=400) from e

    surface = surface_status(db_path=db_path)
    sensor_by_id = {s.sensor_id: s for s in surface.sensors}
    warnings: list[str] = []
    sensor_by_id = merge_external_sensors(sensor_by_id, external_sensors, warnings=warnings)
    binding_directions: dict[int, str] = {}
    try:
        iface_new_sensors = _resolve_bindings(
            spec, ifaces, sensor_by_id, points=points, twin=twin, index=index,
            warnings=warnings, binding_directions=binding_directions,
        )
    except TapPointResolutionError as e:
        # M4 (code review): a `boundary` point whose joining host(s) exist
        # but have no tapped leg -- an ACTIONABLE resolve failure, not the
        # generic "internal error"/500 `_resolve_binding_targets` itself
        # raises for a genuinely missing points=/twin=/index=. Surfaced as
        # a plain 400 `TapPlanError` so it reaches the operator the same
        # way every other resolve-time binding problem does.
        raise TapPlanError(str(e), status=400) from e

    capacity_warning = _capacity_warning(iface_new_sensors)
    if capacity_warning is not None:
        warnings.append(capacity_warning)

    return TapPlanResolution(
        target_status=te.status.value, inv=inv, sensor_by_id=sensor_by_id,
        iface_new_sensors=iface_new_sensors, warnings=warnings,
        points=points, twin=twin, binding_directions=binding_directions,
    )


def _needed_pairs(
    iface_new_sensors: dict[tuple[str, str, int], dict[str, str]]
) -> set[tuple[str, str]]:
    """Every distinct `(net_id, sensor_id)` pair the CURRENT binding set
    needs a keyed gretap for -- direction plays no part here (a gretap
    DEVICE has no direction, only the per-NIC `tc` filter chained onto it
    does, M5), so iterating the sensor dict's KEYS is enough. Shared between
    `_compose_ops` (what to build) and `apply_tap_plan` (the drop-diff
    against what this tap-plan's PRIOR apply built -- M1/S2)."""
    return {
        (net_id, sid)
        for (_host, net_id, _nic), sids in iface_new_sensors.items()
        for sid in sids
    }


def _gretap_device_for_pair(
    inv: _TeInventory, net_id: str, sensor_id: str, plan_id: str,
) -> Optional[str]:
    """The keyed gretap netdev name a `(net_id, sensor_id)` pair maps onto --
    `gre_port_for_bridge_sensor(bridge, sensor_id)` (`naming.py`), keyed on
    the underlying OVS BRIDGE (via `_gre_naming_bridge`, fix round G2 --
    twin-discriminated for the shared WAN uplink bridge, byte-identical for
    an ordinary twin-scoped one), not the net itself. `None` if the net's
    bridge can't be resolved (same "can't even name the device" case
    `_delete_dropped_gretaps` already handles per-pair).

    Load-bearing (2026-07-25, dev45 live diagnosis -- capture-plane
    data-loss bug): in a VLAN zone every vnet sharing that zone shares ONE
    underlay bridge (`_bridge_for_net`), so this function can return the
    SAME device for two DIFFERENT `(net_id, sensor_id)` pairs. That's what
    makes the device-set (not pair-set) diff in `_delete_dropped_gretaps`
    necessary -- see its docstring."""
    bridge = _gre_naming_bridge(inv, net_id, plan_id)
    if not bridge:
        return None
    return gre_port_for_bridge_sensor(bridge, sensor_id)


def _is_router_host(inv: _TeInventory, host: str) -> bool:
    """True for the ROUTER -- keyed off `HostInfo.kind` when known (fix
    round S9, `docs/WORK_ORDER_tap_scopes.md`: `kind` is now published,
    superseding an earlier note that claimed neither `role` nor `kind`
    survived into `_TeInventory`), falling back to a >=2-leg count
    (`inv.host_nets`, ALL `attach-vm-to-vnet` legs -- not just tapped ones,
    and now INCLUDING the WAN leg, M2) for a host whose `kind` this
    particular deploy never recorded (LES-096: a deploy predating
    `node_kind`/the `configure-router` back-fill).

    A SEMANTIC question -- "is this specific host THE router" -- used by
    `_gateway_ip_for_net` to find "the segment's gateway address." Fix
    round G3 (`docs/WORK_ORDER_tap_scopes.md`, final round) split this
    predicate away from the STRUCTURAL "is a route-less broadcast from
    this host ambiguous" question (`_is_route_ambiguous`, below) --
    `_select_verify_injector` wants the latter, not this one; see that
    function's docstring for the regression re-keying both onto `kind`
    caused."""
    kind = inv.host_kind.get(host)
    if kind is not None:
        return kind == "router"
    return len(inv.host_nets.get(host, ())) >= 2


def _is_route_ambiguous(inv: _TeInventory, host: str) -> bool:
    """True iff `host` has >=2 attached legs (ALL `attach-vm-to-vnet` legs,
    `inv.host_nets` -- not just tapped ones, INCLUDING the WAN leg, M2).

    A STRUCTURAL question -- fix round G3 (`docs/WORK_ORDER_tap_scopes.md`,
    final round): "is a route-less broadcast/self-originated sentinel from
    THIS host's egress NIC ambiguous" (the kernel's default-route pick
    could differ from the one segment being verified) -- deliberately NOT
    `kind`-based. A PRIOR revision of this fix round re-keyed the shared
    `_is_multihomed` predicate onto `kind == "router"` (fix round S9), which
    made `_select_verify_injector` silently answer "is this the router"
    instead of "is this host route-ambiguous": a dual-homed `kind: host`
    node (fully expressible in the IR, if not in any shipped archetype
    today) would fall into the `safe` bucket and get a broadcast injection
    with `needs_warning=False` -- a silent false-negative heartbeat,
    LES-001 class. `_select_verify_injector` uses THIS predicate now; only
    `_gateway_ip_for_net` (which genuinely wants "the router," a semantic
    question) uses `_is_router_host`."""
    return len(inv.host_nets.get(host, ())) >= 2


def _gateway_ip_for_net(inv: _TeInventory, net_id: str) -> Optional[str]:
    """The segment's gateway address on `net_id` -- the ROUTER's
    (`_is_router_host`) recorded IP on that leg (`inv.host_net_ip`). `None`
    if no router is attached to this net at all (a flat, router-less
    segment) -- the caller decides what that means for its own injector
    (the Linux sentinel tolerates it, keeping its broadcast default; a
    Windows sentinel cannot, and must fail loud instead, 2026-07-24
    Windows verify-capture sentinel)."""
    for host, nets in inv.host_nets.items():
        if net_id in nets and _is_router_host(inv, host):
            ip = inv.host_net_ip.get((host, net_id))
            if ip:
                return ip
    return None


def _unicast_leaf_target(
    inv: _TeInventory, net_id: str, *, exclude: set[str],
) -> Optional[str]:
    """Any OTHER host's known IP on `net_id`, excluding `exclude` (typically
    the injector itself) -- `inv.host_net_ip` is sourced UNCONDITIONALLY
    (`attach-vm-to-vnet`, LES-095 posture), so this covers every attached
    host, tapped or not. Used (fix round F1, `docs/WORK_ORDER_tap_scopes.md`)
    as the on-segment unicast ping target when the ROUTER ITSELF is forced
    to be the injector (an rx-only pair with no safe single-homed
    candidate): a unicast address directly attached to `net_id` forces the
    kernel's LOCAL route table to pick the correct NIC deterministically
    (unlike broadcast, which resolves via the ambiguous DEFAULT route on a
    multi-homed host).

    G4 correction (`docs/WORK_ORDER_tap_scopes.md`, final round): an
    EARLIER revision of this docstring claimed the destination "doesn't
    need to be REAL or REACHABLE" -- FALSE for unicast IPv4. With no ARP
    reply for the target address, the kernel queues and drops the echo:
    only the ARP request itself (carrying no token payload) ever reaches
    the wire, and the sensor-side `frame contains <hex>` confirm never
    matches. Broadcast never had this dependency (every host on the
    segment answers it at L2, no ARP resolution needed) -- unicast trades
    that away for a deterministic egress NIC, so the target host had
    better actually be UP. Prefer a host in `inv.qga_hosts` (an EXISTING
    liveness signal: it proved itself alive and Linux-capable via a real
    `wait-os-ready`) before falling back to the alphabetically-first known
    IP, which could belong to a powered-off/never-booted host with a
    merely-PLANNED address. `None` if no other host is attached to this
    net at all (a router-only segment/boundary edge)."""
    candidates_with_ip = sorted(
        (host, ip) for (host, n), ip in inv.host_net_ip.items()
        if n == net_id and host not in exclude and ip
    )
    if not candidates_with_ip:
        return None
    for host, ip in candidates_with_ip:
        if host in inv.qga_hosts:
            return ip
    return candidates_with_ip[0][1]


def _select_verify_injector(
    inv: _TeInventory, net_id: str, candidates: set[str],
    candidate_directions: dict[str, str],
) -> tuple[str, bool, Optional[str]]:
    """Pick the verify-capture sentinel injector for `net_id` among
    `candidates` (the hosts THIS tap-plan bound to the sensor on that net),
    and a UNICAST target IP to aim the sentinel at (`None` -> the Linux
    sentinel's broadcast default; REQUIRED for a Windows injector).

    `candidate_directions` (fix round F1, `docs/WORK_ORDER_tap_scopes.md`)
    is `{host: direction}` for THIS (net, sensor) pair -- direction now
    determines which injection mechanism is safe:

      * **A safe (single-homed) candidate exists** -- inject from it
        directly, REGARDLESS of direction: the mirror sits on that host's
        OWN leg, so its rx side always captures what it sends, whether the
        mirror is rx-only or both. Broadcast (Linux) / the segment's
        gateway (Windows, `_gateway_ip_for_net`) both still work here --
        unaffected by rx-only.
      * **Every bound candidate is multi-homed (the router) AND at least
        one of them mirrors `"both"` directions** (`perimeter`, or a
        `segment`/`boundary` pair unioned up to `both` by another binding,
        `docs/WORK_ORDER_tap_scopes.md` M4) -- the PRE-rx-only bystander-
        leaf mechanism still works: a bystander leaf's broadcast/gateway-
        unicast floods the segment's L2 domain, landing on the router's
        mirrored port as egress/tx, which IS mirrored when direction is
        `"both"`.
      * **Every bound candidate is multi-homed AND `"rx"`-only** (the
        `boundary` case under rx-only, F1's regression) -- the bystander-
        leaf mechanism is now UNAVAILABLE (egress isn't mirrored at all),
        so inject FROM THE ROUTER ITSELF instead: its own rx side captures
        what IT sends, exactly like the safe-candidate case above, just
        with the router as its own injector. The sentinel needs a UNICAST
        target on `net_id` (`_unicast_leaf_target`) rather than broadcast
        -- broadcast on a MULTI-HOMED host resolves via the DEFAULT ROUTE
        (the dev45-diagnosed bug this function's docstring used to
        describe as the reason to avoid the router entirely), while a
        unicast address directly attached to `net_id` forces the kernel's
        LOCAL route table to pick the correct NIC deterministically. If no
        other host is attached to `net_id` at all, there is no such target
        -- fall back to broadcast from the router anyway, but this is
        genuinely unreliable and the caller MUST warn (LES-001).

    Returns `(injector_host_id, needs_warning, sentinel_target_ip)` --
    `needs_warning=True` means the heartbeat is structurally unreliable
    (LES-001: never silently accepted as an equivalent green check).

    Fix round G3 (`docs/WORK_ORDER_tap_scopes.md`, final round): this
    function uses `_is_route_ambiguous` (leg-count only), NEVER
    `_is_router_host` (`kind`-based) -- the question here is structural
    ("is a route-less broadcast from this host ambiguous"), not semantic
    ("is this specific host the router"). A dual-homed `kind: host` node is
    JUST as route-ambiguous as a router; treating it as `safe` (an earlier
    revision's `kind`-based re-keying did exactly this) would silently
    broadcast-inject from it with `needs_warning=False` -- a false-negative
    heartbeat.
    """
    safe = sorted(h for h in candidates if not _is_route_ambiguous(inv, h))
    if safe:
        injector = next((h for h in safe if h in inv.qga_hosts), safe[0])
        return injector, False, None

    # Every bound candidate is route-ambiguous (multi-homed -- the router,
    # possibly several).
    multihomed_candidates = sorted(candidates)
    any_both = any(
        candidate_directions.get(h) == "both" for h in multihomed_candidates
    )
    if any_both:
        # >=1 candidate mirrors BOTH directions -- the PRE-rx-only
        # bystander-leaf/broadcast mechanism still applies to it (egress IS
        # mirrored). Look inventory-wide for a bystander leaf attached to
        # the SAME net, excluding the multi-homed candidate(s) themselves.
        leaf_on_net = sorted(
            h for h, nets in inv.host_nets.items()
            if net_id in nets and not _is_route_ambiguous(inv, h) and h not in candidates
        )
        if leaf_on_net:
            injector = next(
                (h for h in leaf_on_net if h in inv.qga_hosts), leaf_on_net[0],
            )
            return injector, False, None
        # No leaf exists on this net at all -- structurally true for
        # `__wan__` (fix round S7, `docs/WORK_ORDER_tap_scopes.md`): only a
        # router EVER attaches to the WAN uplink, so `leaf_on_net` can never
        # be non-empty there, unlike an internal segment where it's a genuine
        # (if unlikely) topology gap. Fall back to a candidate (the router)
        # -- but the "may egress the wrong NIC" warning below is backwards
        # for `__wan__` specifically: the router's DEFAULT route IS the
        # mirrored WAN uplink by definition, so broadcasting from it is
        # exactly as reliable as targeting it directly, not a fallback of
        # last resort. Every OTHER net still warns: this heartbeat CAN
        # false-negative there even though the mirror itself works.
        injector = next(
            (h for h in multihomed_candidates if h in inv.qga_hosts),
            multihomed_candidates[0],
        )
        return injector, net_id != _WAN_SENTINEL, None

    # Every bound candidate is multi-homed AND rx-only (F1: the `boundary`
    # case) -- inject FROM the router itself, targeting a unicast address
    # on `net_id` so its own outbound (rx-captured) send traverses the
    # mirrored NIC deterministically.
    injector = next(
        (h for h in multihomed_candidates if h in inv.qga_hosts),
        multihomed_candidates[0],
    )
    target = _unicast_leaf_target(inv, net_id, exclude={injector})
    if target:
        return injector, False, target
    # No other host attached to `net_id` at all (a router-only segment/
    # boundary edge) -- no unicast target to force the correct NIC. Fall
    # back to broadcast from the router; structurally unreliable, warn.
    return injector, True, None


# ---------------------------------------------------------------------------
# Op composition -- attach-gre-tunnel / install-ovs-mirror / verify-capture,
# built by hand (see module docstring for why) but using the SAME catalog
# metadata `pass1_emit._make_op` uses, so cleanup_strategy/idempotency_class/
# resource_categories/timeout are never re-derived or drifted from the
# catalog (`axrange.ops.registry` stays the single source of truth).
# ---------------------------------------------------------------------------
def _op_timeout_s(spec: Optional[str]) -> Optional[int]:
    if not spec or not spec.endswith("s"):
        return None
    try:
        return int(spec[:-1])
    except ValueError:
        return None


def _make_mir_op(
    plan_id: str,
    op_name: str,
    scope: dict[str, str],
    inputs_resolved: dict[str, Any],
) -> OpInstance:
    # TODO(N1): duplicates pass1_emit._make_op's catalog->OpInstance
    # construction (a drift hazard if the two ever diverge). Kept
    # independent deliberately while pass1_emit.py is under concurrent
    # edit (A3); once that lands, factor this + pass1_emit._make_op into one
    # shared helper (e.g. axrange.ops.registry or a small planner util).
    op_def = get_op(op_name)
    if op_def is None:  # pragma: no cover -- defensive; catalog names are fixed
        raise TapPlanError(f"unknown op name: {op_name}", status=500)
    return OpInstance(
        op_id=_op_id(plan_id, op_name, scope),
        op_name=op_name,
        op_catalog_id=op_def.id,
        kind=op_def.kind,
        scope=dict(scope),
        inputs_resolved=inputs_resolved,
        inputs_from_outputs={},
        cleanup_strategy=op_def.cleanup_strategy,
        idempotency_class=op_def.idempotency_class,
        resource_categories=op_def.resource_categories,
        timeout_s=_op_timeout_s(op_def.timeout),
    )


def _compose_ops(
    plan_id: str,
    inv: _TeInventory,
    sensor_by_id: dict[str, Any],
    iface_new_sensors: dict[tuple[str, str, int], dict[str, str]],
    store: Any,
    target_node: str,
    *,
    prior_hosts: Optional[set[str]] = None,
    warnings: Optional[list[str]] = None,
) -> Plan:
    """Compose the OP-MIR playout for `iface_new_sensors` (this apply's
    resolved bindings). `prior_hosts` (M1/S2) widens the affected-host set
    with hosts a PRIOR apply of this SAME tap-plan touched but the CURRENT
    bindings no longer reference -- so a dropped/narrowed host still gets an
    `install-ovs-mirror` op composed (with EMPTY desired content -- no
    TE-original wiring exists to fall back to), actively reverting it rather
    than leaving it at its last-applied state.
    """
    plan = Plan(
        plan_id=plan_id, scenario_id=plan_id, scenario_version=None,
        ir_hash="", tier="te",
    )

    needed_pairs = _needed_pairs(iface_new_sensors)

    # 1. attach-gre-tunnel[$net@$sensor] -- one keyed gretap per distinct
    #    (net, sensor) pair this tap-plan needs (per-tap sensor routing,
    #    OP_CATALOG par.5.3; distinct GRE key per (bridge, sensor) is naming.py's
    #    job, `gre_key_for_bridge_sensor`, applied by the adapter itself).
    gre_ops: dict[tuple[str, str], OpInstance] = {}
    for net_id, sensor_id in sorted(needed_pairs):
        bridge = _gre_naming_bridge(inv, net_id, plan_id)
        if not bridge:
            raise TapPlanError(
                f"no recorded OVS bridge for network {net_id!r} in target TE "
                "(the TE deploy may be incomplete)", status=400,
            )
        sensor = sensor_by_id[sensor_id]
        op = _make_mir_op(
            plan_id, "attach-gre-tunnel", {"net": net_id, "sensor": sensor_id},
            {
                "bridge_name": bridge,
                "net_id": net_id,
                "sensor_id": sensor_id,
                "sensor_transport_ip": sensor.transport_ip,
                "target_node": target_node,
            },
        )
        plan.add_op(op)
        gre_ops[(net_id, sensor_id)] = op

    # 2. install-ovs-mirror[$host] -- STABLE scope (no content hash, see the
    #    module docstring's M1 note). Per affected host (current bindings UNION
    #    prior_hosts), the FULL desired tapped_nics -- a dropped binding is
    #    simply absent, which is exactly the revert -- so the regenerated hook
    #    script never keeps a binding this apply removed (LES-011 -- the caller
    #    always supplies the complete desired state). The baseline starts EMPTY
    #    (not the TE's own tapped_nics): a sensor-agnostic te (2026-07-13,
    #    docs/WORK_ORDER_sensor_agnostic_te.md) wires no mirror of its own, so
    #    there is nothing TE-original to preserve -- this tap-plan's bindings
    #    are the ENTIRE desired state.
    affected_hosts = sorted(
        {h for (h, _n, _i) in iface_new_sensors} | set(prior_hosts or ())
    )
    # (nic, net_id, sensor_id, direction) -- direction is per (nic, sensor)
    # (`docs/WORK_ORDER_tap_scopes.md` M4/M5), already unioned by
    # `_resolve_bindings` for any (port, sensor) pair two bindings both
    # claim.
    host_desired: dict[str, list[tuple[int, str, str, str]]] = {}
    for host in affected_hosts:
        desired: list[tuple[int, str, str, str]] = []
        seen: set[tuple[int, str, str]] = set()
        for (h, net_id, nic), sid_directions in sorted(iface_new_sensors.items()):
            if h != host:
                continue
            for sid in sorted(sid_directions):
                triple = (nic, net_id, sid)
                if triple not in seen:
                    desired.append((nic, net_id, sid, sid_directions[sid]))
                    seen.add(triple)
        host_desired[host] = sorted(desired)

    mirror_ops: dict[str, OpInstance] = {}
    for host in affected_hosts:
        vmid = inv.host_vmid.get(host)
        if vmid is None and not host_desired.get(host):
            # A host present ONLY as a `prior_hosts` de-scope target, whose VM no
            # longer exists -- `te deploy --update --allow-destroy` swept it, so
            # `build_twin_inventory` no longer publishes it. There is nothing to
            # revert: the mirror lived in that VM's hookscript + tc rules and was
            # deleted along with the VM. Composing the de-scope anyway is not
            # merely useless, it FAILS the whole re-apply -- `qm set <vmid>
            # --hookscript ...` -> "Configuration file ... does not exist" (dev45,
            # 2026-08-27: 73 removed hosts, 73 failed install-ovs-mirror ops) --
            # which is exactly the re-apply the update's own dangling-sibling
            # warning instructs the operator to run.
            #
            # Narrow by construction: a host with DESIRED mirrors and no vmid is
            # still the hard error below, because that is a host we are being
            # asked to actively tap and cannot.
            if warnings is not None:
                warnings.append(
                    f"host {host!r} is gone from the target TE (swept by an "
                    "incremental update); skipping its mirror de-scope -- the "
                    "mirror went with the VM"
                )
            continue
        if vmid is None:
            raise TapPlanError(
                f"no recorded vmid for host {host!r} in target TE (the TE "
                "deploy may be incomplete)", status=400,
            )
        desired = host_desired[host]
        tapped_nics_meta: list[dict[str, Any]] = []
        resolved: dict[str, Any] = {"node_id": target_node, "vmid": vmid}
        for nic, net_id, sensor_id, direction in desired:
            bridge = _gre_naming_bridge(inv, net_id, plan_id)
            key = f"gre_port__{nic}__{sensor_id}"
            tapped_nics_meta.append(
                {
                    "nic_index": nic,
                    "net_id": net_id,
                    "sensor_id": sensor_id,
                    "gre_port_input": key,
                    # Per-gretap direction (`docs/WORK_ORDER_tap_scopes.md`
                    # M5) -- `"rx"` or `"both"`, consumed by
                    # `adapter_proxmox/ssh.py`'s `install-ovs-mirror` render
                    # to decide whether THIS destination is chained onto the
                    # egress filter too, or ingress-only.
                    "direction": direction,
                }
            )
            resolved[key] = (
                gre_port_for_bridge_sensor(bridge, sensor_id) if bridge else None
            )
        resolved["tapped_nics"] = tapped_nics_meta
        op = _make_mir_op(
            plan_id, "install-ovs-mirror", {"host": host}, resolved,
        )
        plan.add_op(op)
        mirror_ops[host] = op
        for nic, net_id, sensor_id, _direction in desired:
            gop = gre_ops.get((net_id, sensor_id))
            if gop is not None:
                plan.add_edge(gop.op_id, op.op_id, "ordering")

    # 3. verify-capture[$net@$sensor@$host] -- one heartbeat per NEW (net,
    #    sensor) pair. `_select_verify_injector` picks the injector AND a
    #    unicast sentinel target (`docs/WORK_ORDER_tap_scopes.md` fix round
    #    F1) -- see its own docstring for the full direction-aware case
    #    split (safe single-homed candidate / bystander-leaf-under-`both` /
    #    forced-router-under-`rx`).
    pair_candidates: dict[tuple[str, str], set[str]] = {}
    pair_candidate_directions: dict[tuple[str, str], dict[str, str]] = {}
    for (host, net_id, _nic), sid_directions in iface_new_sensors.items():
        for sid, direction in sid_directions.items():
            pair_candidates.setdefault((net_id, sid), set()).add(host)
            pair_candidate_directions.setdefault((net_id, sid), {})[host] = direction

    for net_id, sensor_id in sorted(needed_pairs):
        sensor = sensor_by_id[sensor_id]
        if not sensor.deploy_id:
            # A config-declared EXTERNAL sensor (`merge_external_sensors`
            # marks it with `deploy_id=""`) -- axRange must NEVER build a
            # QGA/SSH read into an operator-managed, hand-provisioned node
            # (the explicit constraint on this feature; confirmed live --
            # XPress-SW has QGA guest-exec DISABLED, so a QGA verify-capture
            # is not just unwanted but IMPOSSIBLE). `verify-capture` always
            # reads the sensor over QGA (`_verify_capture` in
            # `config_exec/backend.py` fails loud with no `sensor_vmid`), so
            # there is no vmid to give it here and none should be sought --
            # simply skip composing a heartbeat for this pair. The
            # attach-gre-tunnel + install-ovs-mirror ops above still wire the
            # gretap fully; the operator verifies capture on their own side.
            #
            # Code review (LES-001 trap): skipping the heartbeat must NEVER
            # be SILENT -- a `tap apply` that reports "completed" with zero
            # proof a frame reached the sink is exactly the "green deploy, no
            # capture" failure LES-001 forbids. Surface it into the caller's
            # `warnings` collector (only `apply_tap_plan` passes one; a dry
            # `tap plan`/de-scope-revert compose call passes none and simply
            # doesn't collect it -- the skip itself is unconditional either
            # way, only the surfacing is opt-in per caller).
            if warnings is not None:
                warnings.append(
                    f"capture UNVERIFIED for external sensor {sensor_id!r} on "
                    f"net {net_id!r} — operator-managed sink, no "
                    "verify-capture heartbeat; confirm capture out-of-band "
                    "(packetXpress / tcpdump on gre-cap)"
                )
            continue
        candidates = pair_candidates.get((net_id, sensor_id)) or set()
        if not candidates:
            continue  # pragma: no cover -- defensive; needed_pairs is derived from this map
        candidate_directions = pair_candidate_directions.get((net_id, sensor_id)) or {}
        injector, unreliable, sentinel_target_ip = _select_verify_injector(
            inv, net_id, candidates, candidate_directions,
        )
        # Computed here (fix round G8, `docs/WORK_ORDER_tap_scopes.md`, final
        # round) rather than only later at op-resolution time (below) --
        # the unreliable-injector warning text needs to know it too, since
        # BOTH `_select_verify_injector` unreliable paths return
        # `sentinel_target_ip=None`, and a Windows injector NEVER falls back
        # to broadcast (unlike Linux) -- it falls back to `gateway_ip`,
        # which for a forced-router injector IS ITS OWN leg address, i.e. a
        # self-ping, not a broadcast. Reused verbatim below (not recomputed).
        injector_os_family = inv.host_os_family.get(injector, "linux")
        if unreliable and warnings is not None:
            # LES-001: a heartbeat with a structurally-unreliable injector
            # must never regress to a silent skip -- surface it exactly like
            # the external-sensor "capture UNVERIFIED" case above. Two root
            # causes land here now (fix round F1): no bystander leaf exists
            # for a `both`-direction multi-homed pair, OR (rx-only) no OTHER
            # host is attached to `net_id` at all to target a unicast
            # sentinel at.
            if injector_os_family == "windows":
                # G8: a Windows sentinel never broadcasts (`_verify_capture`
                # ALWAYS unicasts) -- with no `sentinel_target_ip` resolved,
                # it falls back to `gateway_ip`, the forced-router injector's
                # OWN address on `net_id` -- a self-ping, not "egressing the
                # wrong NIC."
                warnings.append(
                    f"verify-capture heartbeat for net {net_id!r} / sensor "
                    f"{sensor_id!r} could not find a reliable injector/"
                    f"target -- falling back to {injector!r} (multi-homed, "
                    "likely the router whose leg the mirror sits on), a "
                    "Windows injector with no other host attached to this "
                    "net to unicast against, so it targets its OWN "
                    "interface address -- effectively a self-ping that may "
                    "never register as new traffic on the wire, so this "
                    "heartbeat can FALSE-NEGATIVE even though the mirror "
                    "itself is working -- confirm capture out-of-band if "
                    "it fails"
                )
            else:
                warnings.append(
                    f"verify-capture heartbeat for net {net_id!r} / sensor "
                    f"{sensor_id!r} could not find a reliable injector/target -- "
                    f"falling back to {injector!r} (multi-homed, likely the "
                    "router whose leg the mirror sits on) with a broadcast "
                    "sentinel, which may egress the WRONG NIC (its default-"
                    "route interface) and never cross the mirrored wire, so "
                    "this heartbeat can FALSE-NEGATIVE even though the mirror "
                    "itself is working -- confirm capture out-of-band if it "
                    "fails"
                )
        # Fix round G1 (`docs/WORK_ORDER_tap_scopes.md`, final round):
        # the ORIGINAL S7 guard scoped this to `__wan__` alone, reasoning
        # that "a Linux router elsewhere with no recorded os_family is a
        # rarer, less certain signal." That reasoning no longer holds after
        # F1: for EVERY rx-only `boundary` pair, the router is now the
        # injector BY CONSTRUCTION (candidates are router-only there), not
        # just a rare WAN-specific fallback -- so a VyOS-routed `boundary`
        # twin (VyOS has `agent: false`, so `pass1_emit.py` never emits
        # `wait-guest-agent`/`wait-os-ready` for it, and no `os_family` is
        # ever recorded) hits this EXACT same "QGA-less forced injector"
        # failure mode on an ordinary internal boundary, not just on
        # `perimeter: wan`. Widen the guard to the forced-router-injector
        # case generally, not just the WAN net id.
        # `_is_route_ambiguous` (fix round G3), not `_is_router_host` -- this
        # re-derives the SAME structural question `_select_verify_injector`
        # used to decide "no safe candidate existed," so it must use the
        # identical predicate.
        forced_router_injector = injector in candidates and _is_route_ambiguous(inv, injector)
        if (
            (net_id == _WAN_SENTINEL or forced_router_injector)
            and injector not in inv.host_os_family
        ):
            # Fix round S7 (`docs/WORK_ORDER_tap_scopes.md`): the injector
            # never ran `wait-os-ready` at all (no os_family on file), which
            # is almost certainly a QGA-less image (VyOS, `agent: false`).
            # Composing a verify-capture op against it would fail HARD at
            # EXECUTION time (no guest agent to read/write through), taking
            # down the WHOLE `tap deploy` for a heartbeat this specific host
            # structurally cannot run. Skip composing it instead (LES-089
            # posture: surface "capture UNVERIFIED," never silently) -- the
            # gretap/mirror ops above still wire the actual capture path
            # fully. Scoped to the FORCED-injector case (this pair's
            # injector IS the router with no safer candidate, `net_id ==
            # "__wan__"` or a rx-only `boundary`-shaped pair) -- a Linux
            # SAFE (single-homed) injector elsewhere with no recorded
            # os_family stays a much rarer, less certain signal; this keeps
            # the guard from silently swallowing a genuine bug on an
            # ordinary segment tap that DOES have QGA.
            if warnings is not None:
                warnings.append(
                    f"verify-capture heartbeat for net {net_id!r} / sensor "
                    f"{sensor_id!r} SKIPPED -- injector {injector!r} has no "
                    "recorded guest-agent (likely a QGA-less router image, "
                    "e.g. VyOS) and cannot run the sentinel; the mirror "
                    "itself is still wired, confirm capture out-of-band"
                )
            continue
        vmid = inv.host_vmid.get(injector)
        if vmid is None:
            continue
        bridge = _gre_naming_bridge(inv, net_id, plan_id)
        # The injector's real OS family (2026-07-24, Windows verify-capture
        # sentinel) -- `config_exec._verify_capture` picks the sentinel
        # RENDERER off this (`render_capture_sentinel` vs
        # `render_capture_sentinel_windows`), unrelated to `inv.qga_hosts`
        # (a LINUX-only preference signal `_select_verify_injector` already
        # used above). Default `"linux"` when unresolved (matches
        # `pass1_emit`'s own `wait-os-ready` fallback, `_os_family_lookup`'s
        # docstring) -- this is an op-input driving a real runtime branch,
        # not a published fact, so a fabricated fallback here is the RIGHT
        # posture (unlike `HostInfo.os`, which must never fabricate).
        # (Already computed above, right after `_select_verify_injector`,
        # fix round G8 -- the unreliable-injector warning needed it too;
        # reused verbatim, not recomputed.)
        # `gateway_ip` (the router's OWN IP on `net_id`, `_gateway_ip_for_net`)
        # still serves its ORIGINAL role -- the Windows-mandatory unicast
        # target for the "safe"/"bystander-leaf" injector cases, where the
        # injector is NOT the router itself. `sentinel_target_ip` (from
        # `_select_verify_injector`, fix round F1) is the NEW target for the
        # forced-router-injects-itself case (a DIFFERENT address -- some
        # OTHER host's, since the router can't usefully target its own IP).
        # Effective target: whichever one this injector selection actually
        # produced; `sentinel_target_ip` wins when both are somehow present
        # (it is the more SPECIFIC of the two -- computed for THIS exact
        # injector, not just "some router on this net").
        gateway_ip = _gateway_ip_for_net(inv, net_id)
        effective_target_ip = sentinel_target_ip or gateway_ip
        if injector_os_family == "windows" and not effective_target_ip:
            # Fail LOUD at compose time (same posture as the "no recorded
            # bridge"/"no recorded vmid" raises above) -- a Windows sentinel
            # structurally cannot fall back to broadcast (unlike Linux's
            # `ping -b`), so an unresolvable target means this heartbeat
            # can NEVER be rendered, not just "unreliable" (which still gets
            # a real attempt). The segment needs either a multi-homed
            # router leg (a gateway IP to target) or another attached host
            # (a unicast target for a forced-router injector).
            raise TapPlanError(
                f"verify-capture[{net_id}@{sensor_id}]: injector {injector!r} "
                "is Windows but no unicast target IP could be resolved for "
                f"net {net_id!r} -- a Windows sentinel cannot fall back to "
                "broadcast the way the Linux sentinel does (docs/design/"
                "06-taps-sensors.md)", status=400,
            )
        resolved: dict[str, Any] = {
            "vmid": vmid,
            "injector_host_id": injector,
            "net_id": net_id,
            "sensor_id": sensor_id,
            "bridge_name": bridge,
            "gre_port": (
                gre_port_for_bridge_sensor(bridge, sensor_id) if bridge else None
            ),
            # O2 sensors are always in-scenario-style VMs (A1) decapping on
            # gre-cap (sensors design Phase B, OP_CATALOG par.5.3) -- read them
            # over QGA, not the legacy SENSOR_SSH_* config-iface path.
            "sensor_iface": "gre-cap",
            "os_family": injector_os_family,
        }
        if gateway_ip:
            # Threaded regardless of OS (harmless for Linux -- `config_exec.
            # _verify_capture`'s Linux branch ignores it unless
            # `sentinel_target_ip` is ALSO absent, LES-025 "optional, not
            # required" posture); REQUIRED for Windows when
            # `sentinel_target_ip` is absent too, enforced by the raise above.
            resolved["gateway_ip"] = gateway_ip
        if sentinel_target_ip:
            # Fix round F1 (`docs/WORK_ORDER_tap_scopes.md`) -- the on-segment
            # unicast target for a FORCED-router injector under an rx-only
            # pair (`_select_verify_injector`). `config_exec/backend.py`'s
            # `_verify_capture` prefers this over `gateway_ip` for BOTH OS
            # families: a router injecting itself needs a DIFFERENT
            # address than its own `gateway_ip` (which IS its own leg).
            resolved["sentinel_target_ip"] = sentinel_target_ip
        svmid = _sensor_vmid(store, sensor)
        if svmid is not None:
            resolved["sensor_vmid"] = svmid
        vop = _make_mir_op(
            plan_id, "verify-capture",
            {"net": net_id, "sensor": sensor_id, "host": injector}, resolved,
        )
        plan.add_op(vop)
        gop = gre_ops.get((net_id, sensor_id))
        if gop is not None:
            plan.add_edge(gop.op_id, vop.op_id, "ordering")
        # Order after EVERY candidate's install-ovs-mirror, not just the
        # chosen injector's -- for a boundary tap the injector is now a
        # bystander LEAF that has no mirror op of its own at all (its own
        # leg isn't tapped); the op that actually matters here is the
        # ROUTER's (one of `candidates`), whose mirror must be applied
        # before the heartbeat reads for it. `candidates | {injector}`
        # covers both: every actually-mirrored host, plus the injector
        # itself on the (unchanged) segment-tap path where they coincide.
        for host_id in sorted(candidates | {injector}):
            mop = mirror_ops.get(host_id)
            if mop is not None:
                plan.add_edge(mop.op_id, vop.op_id, "ordering")

    plan.freeze()
    return plan


def compose_tap_ops(
    resolution: TapPlanResolution, *, target: str, store: Any, target_node: str,
) -> Plan:
    """Compose the OP-MIR playout for an ALREADY-RESOLVED tap-plan binding,
    WITHOUT running it -- `axrange tap plan`'s entry point (a thin PUBLIC
    wrapper over `_compose_ops`, this module's own op-composition core, so
    the CLI reads a public symbol rather than a private one). `plan_id` uses
    the SAME deterministic `f"tap-{target}"` convention `apply_tap_plan`
    uses, so `tap plan`'s rendered op tree matches what `tap deploy` would
    actually persist."""
    plan_id = f"tap-{target}"
    return _compose_ops(
        plan_id, resolution.inv, resolution.sensor_by_id,
        resolution.iface_new_sensors, store, target_node,
    )


def _delete_dropped_gretaps(
    inv: _TeInventory,
    dropped_pairs: set[tuple[str, str]],
    *,
    needed_pairs: set[tuple[str, str]],
    teardown_backend: Any,
    adapter_config: Optional[str],
    log: Callable[[str], None],
    plan_id: str,
    store: Any,
    deploy_id: str,
) -> None:
    """M1/S2's other half: a binding this apply DROPPED (present in the
    tap-plan's PRIOR attach-gre-tunnel ops, absent from the current needed
    set) leaves an unused gretap netdev unless we explicitly remove it --
    the mirror re-assertion above already stopped POINTING at it, so this is
    now a plain resource-leak cleanup, not a mis-routing risk. Best-effort:
    called only after a successful executor run (the mirror side landed).

    VLAN-zone gretap ALIASING (2026-07-25, dev45 live diagnosis -- a real
    capture-plane DATA-LOSS bug, not just a leak): the keyed gretap netdev
    is named `gre_port_for_bridge_sensor(bridge, sensor)` -- keyed on the
    (underlay BRIDGE, sensor) pair, not (net, sensor). In a VLAN zone every
    vnet in that zone shares ONE underlay bridge (`_bridge_for_net`), so the
    gretap device is effectively per-(underlay-bridge, sensor), SHARED
    across every net bound to that sensor. A drop-diff computed at (net,
    sensor) granularity can therefore compute a `dropped_pairs` entry whose
    DEVICE is the exact same one a still-NEEDED (net', sensor) pair uses
    (proven live: a re-shard swap left `dropped_pairs` mapping onto the
    SAME two devices the swapped-to bindings still needed -- both live
    gretaps were deleted the instant apply finished, silently killing
    capture while verify-capture and the apply status both stayed green,
    LES-001 false-green). So this now diffs at the DEVICE level, not the
    pair level: a dropped pair's device is deleted ONLY if no needed pair
    maps to that same device; otherwise it's KEPT, loudly logged (LES-102)
    so the de-scope stays legible.

    S3 (code review, GAP-045): also marks each dropped pair's OWN
    `attach-gre-tunnel[$net@$sensor]` op row `destroyed` (`StateStore.
    mark_op_destroyed`) -- mirrors `traffic.py`'s `_stop_dropped_instances`.
    `force_reset_ops` only resets op_ids IN the new plan, so a dropped
    pair's op_id is simply never touched again; without this, M2's
    COMPLETED-only filter in `_bindings_summary_from_ops`/
    `tap_info_from_deploy` would otherwise keep reporting a de-scoped
    sensor as live forever (the same over-report class as GAP-044, on the
    sensor-fan-out axis instead of direction). Marked for EVERY dropped
    pair regardless of whether its gretap DEVICE was actually deleted or
    kept (VLAN-zone aliasing, above) -- those are separate facts: "is this
    (net, sensor) BINDING still part of the current desired state" (no,
    unconditionally, for everything in `dropped_pairs`) vs. "is the shared
    physical DEVICE still needed by some OTHER pair" (maybe). Per LES-113,
    this is a point-in-time annotation for a status READ, not a durable
    record a later apply's `force_reset_ops`/`reset_incomplete_ops` must
    preserve -- correctness rests on the pair's absence from
    `needed_pairs`, not on this flag surviving.
    """
    if not dropped_pairs:
        return
    for net_id, sensor_id in sorted(dropped_pairs):
        store.mark_op_destroyed(
            deploy_id,
            _op_id(plan_id, "attach-gre-tunnel", {"net": net_id, "sensor": sensor_id}),
        )
    needed_devices = {
        dev for dev in (
            _gretap_device_for_pair(inv, net_id, sensor_id, plan_id)
            for net_id, sensor_id in needed_pairs
        )
        if dev is not None
    }
    backend = teardown_backend
    if backend is None:
        from axrange.adapter_proxmox import ProxmoxTeardown, load_config

        backend = ProxmoxTeardown(load_config(adapter_config))
    for net_id, sensor_id in sorted(dropped_pairs):
        bridge = _gre_naming_bridge(inv, net_id, plan_id)
        if not bridge:
            # LOUD, not a silent skip (review MUST-FIX, second round; LES-001
            # class): an unresolvable bridge for a net_id this apply is
            # ACTIVELY trying to drop means the corresponding gretap netdev
            # can't be named, so it CANNOT be deleted -- an orphaned gretap
            # left running on the cluster with no signal anything is wrong.
            # `net_bridge`'s key domain now covers every network this TE
            # ever declared (`build_twin_inventory`), so this branch should
            # be unreachable in practice; if it ever fires, it means a real
            # net_id/bridge mismatch that needs an operator's attention.
            log(
                f"  ! gretap for dropped binding {net_id}@{sensor_id}: "
                f"could not resolve a bridge for net_id={net_id!r} -- "
                "SKIPPED, likely an orphaned gretap netdev on the cluster"
            )
            continue
        dev = gre_port_for_bridge_sensor(bridge, sensor_id)
        if dev in needed_devices:
            # VLAN-zone aliasing: this dropped pair's device is STILL
            # needed by a retained/needed pair sharing the same underlay
            # bridge + sensor -- deleting it would kill live capture for
            # that other pair. Kept, not deleted; logged loudly (LES-102)
            # so this de-scope decision is visible, not a silent no-op.
            log(
                f"  = gretap {dev}: KEPT (dropped binding {net_id}@{sensor_id} "
                "shares this device with a still-needed pair on the same "
                "underlay bridge+sensor -- VLAN-zone aliasing, not deleted)"
            )
            continue
        try:
            backend.delete_gretap(dev)
            log(f"  - gretap {dev}: removed (dropped binding {net_id}@{sensor_id})")
        except Exception as e:  # noqa: BLE001 -- best-effort; an orphaned
            # gretap is a resource leak, not the mis-routing bug M1 fixes --
            # a delete failure here must never fail the apply that just
            # corrected the actual traffic path.
            log(f"  ! gretap {dev}: cleanup failed: {e}")


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------
def apply_tap_plan(
    *,
    spec: Optional[TapPlanSpec] = None,
    spec_path: Optional[str | Path] = None,
    db_path: str,
    adapter_config: Optional[str] = None,
    target_node: Optional[str] = None,
    target: Optional[str] = None,
    adapter: Any = None,
    config_backend: Any = None,
    teardown_backend: Any = None,
) -> TapPlanStatus:
    """Apply a tap-plan: bind the target TE's tapped interfaces to standing
    O2 sensors and run the composed OP-MIR ops through the engine executor
    (`run_deploy_executor`, `axrange.api.service` -- LES-037 reuse).

    `target` (`docs/WORK_ORDER_tap_target_resolution.md`) is the CLI
    `--target`/API-body override -- it wins over the loaded plan's OWN
    `target:` when both are given ("effective target" = `target` if set,
    else `resolved.target`, else neither -> a hard error). The effective
    target is then resolved (`resolve_target_deploy`: an already-minted
    deploy id, OR a scenario/twin-config NAME resolved to the newest LIVE
    `te` deploy of that scenario) BEFORE anything else -- no `StateStore`
    write, lease, or plan_id computation happens until a concrete deploy id
    is in hand, so a missing/unresolvable target fails clean with NO state
    written.

    Idempotent AND correct on narrow/revert (M1): one tap-plan deploy per
    target TE (`plan_id = f"tap-{target}"` is deterministic, resumed via
    `StateStore.find_deploy_for_plan`). Every apply force-resets the composed
    plan's `install-ovs-mirror`/`verify-capture`/`attach-gre-tunnel` op_ids to
    `pending` (`StateStore.force_reset_ops`) before running the executor --
    an unchanged desired state is then a cluster NO-OP (Model C
    check-then-act), and a narrowed/reverted one is CORRECTLY re-asserted
    (not silently skipped because an identically-shaped op_id happened to
    already be `completed` from an earlier apply). A binding this apply
    DROPPED also gets its now-unused gretap deleted, after the mirror
    re-assertion lands (`_delete_dropped_gretaps`).

    `adapter`/`config_backend`/`teardown_backend` are test seams (inject
    fakes, no Proxmox); production builds the real
    `ProxmoxAdapter`/`ConfigBackend`/`ProxmoxTeardown`, same as
    `axrange.o2control.control.stand_up_surface` / `tear_down_surface`.
    """
    from axrange.adapter_proxmox import load_config
    from axrange.api.service import release_deploy_resources, run_deploy_executor
    from axrange.planner import StateStore
    from axrange.planner.cluster import ClusterContext
    from axrange.planner.locks import Heartbeat
    from axrange.planner.state import DeployStatus, default_process_info

    resolved = resolve_tap_plan(spec, spec_path)

    effective_target = target or resolved.target
    if not effective_target:
        raise TapPlanError(
            "no tap-plan target: set target: in the plan or pass --target",
            status=400,
        )

    store = StateStore(db_path)
    try:
        # Resolve the effective target to a CONCRETE deploy id (an already-
        # minted id passes through unchanged; a scenario NAME resolves to
        # its newest live `te` deploy) BEFORE anything else this function
        # does -- `plan_id = f"tap-{resolved.target}"` below, and every
        # StateStore write that follows, must key off the concrete id, not
        # a possibly-reusable name (`docs/WORK_ORDER_tap_target_resolution.md`).
        concrete_target = resolve_target_deploy(store, effective_target)
        if concrete_target != resolved.target:
            # Defensive: no live bug today (TapPlanSpec has only target+bindings,
            # both threaded), but `replace()` is immune to a future added field
            # -- the exact class that hand-listing let bite the sibling
            # TrafficProfileSpec rebuild when W1 added `roles` (LES-037, W1
            # dev45 live-verify 2026-08-04).
            resolved = replace(resolved, target=concrete_target)

        # cfg loaded FIRST (moved ahead of resolve_tap_plan_bindings) so its
        # config-declared external_sensors can be threaded into resolution --
        # LES-069, the same allowlist `tap validate`/`tap plan`/`tap info`
        # resolve against, so `tap apply` can never accept a config-external
        # sensor name those read-only entry points would reject. The
        # DEFAULT_NODE-missing check stays AFTER resolve_tap_plan_bindings
        # (unchanged from before this reordering) so an unknown-target 404
        # is still reported before a config-completeness 400 -- moving cfg
        # loading earlier must not reshuffle that error-priority order.
        cfg = load_config(adapter_config)
        if target_node:
            cfg.default_node = target_node

        # Resolve the target TE + standing sensor inventory + validated
        # bindings in ONE shared place (`resolve_tap_plan_bindings`) --
        # reused verbatim by `axrange tap validate`/`tap plan`/`tap info`
        # (`docs/WORK_ORDER_cli_lifecycle.md` §3.2/§3.3, LES-037).
        resolution = resolve_tap_plan_bindings(
            resolved, store, db_path=db_path, external_sensors=cfg.external_sensors,
        )
        inv = resolution.inv
        sensor_by_id = resolution.sensor_by_id
        iface_new_sensors = resolution.iface_new_sensors
        # Carry forward any resolve-time findings (e.g. an external-sensor id
        # collision, `merge_external_sensors`) into this apply's own warnings
        # -- `_compose_ops` below appends its OWN (verify-capture-skip)
        # findings onto the SAME list.
        warnings: list[str] = list(resolution.warnings)

        if not cfg.default_node:
            raise TapPlanError(
                "DEFAULT_NODE missing; set it in adapter.conf, or override "
                "via target_node", status=400,
            )
        # No adapter.conf sensor identity to thread any more (2026-07-13,
        # docs/WORK_ORDER_sensor_agnostic_te.md) — `_compose_ops` resolves
        # `sensor_transport_ip` explicitly per op from A1's inventory
        # (`sensor.transport_ip`, above); `ClusterContext.sensor_ip()`'s fallback
        # is defensive/unreachable via this path.
        ctx = ClusterContext.single_node(cfg.default_node)

        plan_id = f"tap-{resolved.target}"

        # M1/S2 drop-diff: read this tap-plan's OWN prior apply (if any) back
        # from state.db BEFORE composing, so a host/pair no longer in the
        # CURRENT bindings still gets actively reverted/removed rather than
        # silently left at its last-applied state. `exclude_destroyed=True`:
        # a `destroyed` tap-plan deploy is terminal (`axrange tap destroy`
        # already swept its gretaps/mirrors) -- treated as no-match, so a
        # fresh deploy_id is minted below with an EMPTY prior_hosts/
        # prior_pairs (correct: there is nothing left to diff against, the
        # prior apply's resources are already gone) instead of reviving the
        # destroyed row in place (code review finding,
        # docs/WORK_ORDER_cli_lifecycle.md).
        existing = store.find_deploy_for_plan(plan_id, exclude_destroyed=True)
        prior_hosts: set[str] = set()
        prior_pairs: set[tuple[str, str]] = set()
        # Every PRIOR apply's `verify-capture` op_ids (LES-112, 2026-07-24
        # orphaned-injector fix, below): `verify-capture`'s scope includes
        # the INJECTOR host (`{net, sensor, host}`), so a re-apply that picks
        # a DIFFERENT injector for the SAME (net, sensor) pair (e.g.
        # `_select_verify_injector` moving from a router `gw` to a bystander
        # leaf) mints a brand-new op_id -- the OLD one is simply absent from
        # the freshly composed `plan` below, `force_reset_ops` never touches
        # it (it only resets op_ids the NEW plan actually contains), and it
        # would otherwise sit forever as a stale `pending`/`failed` row for
        # this deploy_id with no bookkeeping ever retiring it.
        prior_verify_op_ids: set[str] = set()
        if existing is not None:
            for op in store.ops_for_deploy(existing.deploy_id):
                op_scope = _json_obj(op.scope_json)
                if op.op_name == "install-ovs-mirror":
                    h = op_scope.get("host")
                    if h:
                        prior_hosts.add(str(h))
                elif op.op_name == "attach-gre-tunnel":
                    n_ = op_scope.get("net")
                    s_ = op_scope.get("sensor")
                    if n_ and s_:
                        prior_pairs.add((str(n_), str(s_)))
                elif op.op_name == "verify-capture":
                    prior_verify_op_ids.add(op.op_id)

        # `warnings` was seeded above with `resolution.warnings` (a resolve-
        # time finding like an external-sensor id collision); `_compose_ops`
        # appends its OWN (verify-capture-skip) findings onto the same list.
        plan = _compose_ops(
            plan_id, inv, sensor_by_id, iface_new_sensors, store, cfg.default_node,
            prior_hosts=prior_hosts, warnings=warnings,
        )
        needed_pairs = _needed_pairs(iface_new_sensors)
        dropped_pairs = prior_pairs - needed_pairs

        deploy_id = (
            existing.deploy_id if existing is not None
            else f"{plan_id}-{secrets.token_hex(4)}"
        )

        lease = store.acquire_lease(
            plan_id, deploy_id, process_info=default_process_info()
        )
        if not lease.acquired:
            raise TapPlanError(
                f"another tap-plan operation for target {resolved.target!r} "
                f"is in progress (deploy_id={lease.held_by_deploy_id})",
                status=409,
            )

        heartbeat = Heartbeat(str(db_path), deploy_id=deploy_id, scenario_id=plan_id)
        try:
            if existing is not None:
                store.reset_incomplete_ops(deploy_id)
                store.update_deploy_status(deploy_id, DeployStatus.RUNNING)
            else:
                store.create_deploy(
                    deploy_id=deploy_id, scenario_id=plan_id, scenario_version=None,
                    scenario_schema_version=1, plan_id=plan_id, ir_hash="",
                    placement={}, tier="te",
                )

            real_adapter = adapter
            real_config_backend = config_backend
            if real_adapter is None or real_config_backend is None:
                from axrange.adapter_proxmox import ProxmoxAdapter
                from axrange.config_exec import ConfigBackend

                real_adapter = real_adapter or ProxmoxAdapter(cfg)
                real_config_backend = real_config_backend or ConfigBackend(cfg)

            # M1: force EVERY op in the freshly composed plan back to pending
            # -- including one already `completed` from an earlier apply --
            # so a narrow/revert is genuinely RE-ASSERTED against the
            # cluster, not skipped because its op_id happens to already be
            # marked done. Safe to call before `run_deploy_executor` (which
            # `persist_plan()`s next): `insert_ops` is `INSERT OR IGNORE`, so
            # it never clobbers the `pending` status this just set on an
            # existing row, and a brand-new op_id isn't affected either way
            # (nothing to reset; `persist_plan` inserts it `pending` anyway).
            current_op_ids = {op.op_id for op in plan.ops.values()}
            store.force_reset_ops(deploy_id, list(current_op_ids))

            # LES-112 orphaned-injector de-scope (2026-07-24): a PRIOR
            # apply's `verify-capture` op_id absent from the CURRENT plan
            # means either its (net, sensor) pair was dropped entirely, or
            # `_select_verify_injector` picked a DIFFERENT injector for the
            # SAME pair this time -- either way the old op_id is no longer
            # part of this tap-plan's desired state and would otherwise sit
            # forever as a stale `pending`/`failed` row (bookkeeping only,
            # no cluster resource to tear down for a heartbeat -- unlike
            # `_delete_dropped_gretaps` below, this is safe to do
            # unconditionally, not gated on `result.succeeded`).
            for stale_op_id in sorted(prior_verify_op_ids - current_op_ids):
                store.mark_op_destroyed(deploy_id, stale_op_id)

            result = run_deploy_executor(
                store, real_adapter, real_config_backend, plan, ctx, deploy_id,
                heartbeat,
            )
            status = "completed" if result.succeeded else "failed"
            if result.succeeded and dropped_pairs:
                # M6 (code review): `log=warnings.append`, not `lambda _m:
                # None` -- the LATTER silently swallowed BOTH the
                # unresolvable-bridge warning (an orphaned gretap netdev
                # left on the cluster with no signal, LES-001 class) and any
                # `gretap ... cleanup failed: {e}` line on every real apply.
                # `warnings` is already threaded into this function's
                # `TapPlanStatus.warnings` below (no extra plumbing needed);
                # this also surfaces this step's ROUTINE "gretap X: removed"
                # confirmations (not just its failures) -- an acceptable,
                # honest trade against ever silently dropping the failure
                # case again.
                _delete_dropped_gretaps(
                    inv, dropped_pairs, needed_pairs=needed_pairs,
                    teardown_backend=teardown_backend,
                    adapter_config=adapter_config, log=warnings.append,
                    plan_id=plan_id, store=store, deploy_id=deploy_id,
                )
        finally:
            release_deploy_resources(store, plan_id, deploy_id, heartbeat)

        return TapPlanStatus(
            deploy_id=deploy_id, target=resolved.target, status=status,
            bindings=[
                # `binding_label(b)` (not raw `b.tap`, which is `None` for a
                # tap_point/selector-addressed binding, `docs/
                # WORK_ORDER_tap_points.md`) -- `TapBindingResult.tap`/
                # `TapBindingDTO.tap` predate tap-point addressing and stay a
                # single, UNDECORATED string for backward compatibility (no
                # `[rx]`/`[both]` suffix -- that belongs in a human-facing
                # CLI RENDER, not this machine-readable field). The resolved
                # direction (`docs/WORK_ORDER_tap_scopes.md` M6) is instead a
                # real, separate `direction` field -- "a field, not only a
                # rendered string" -- populated from
                # `resolution.binding_directions[i]`.
                TapBindingResult(
                    tap=binding_label(b), sensors=list(b.sensors),
                    direction=resolution.binding_directions.get(i),
                )
                for i, b in enumerate(resolved.bindings)
            ],
            warnings=warnings,
        )
    finally:
        store.close()


# ---------------------------------------------------------------------------
# Destroy -- `tap_`-scoped ONLY. Because a tap-plan's ops live under their OWN
# deploy_id (never the target TE's), `planner.teardown.destroy_deploy`
# structurally cannot see or touch the TE's clone-vm/create-vnet/create-
# sdn-zone rows (or an O2 sensor's own deploy) when run against this
# deploy_id -- no extra guard code needed beyond reusing it (LES-037).
# ---------------------------------------------------------------------------
def _revert_mirrors_to_te_original(
    store: Any,
    tap_deploy_id: str,
    te_deploy_id: str,
    plan_id: str,
    *,
    adapter: Any,
    default_node: str,
    log: Callable[[str], None],
) -> None:
    """S2 de-scope pass: reassert every host this tap-plan ever touched back to
    EMPTY tapped_nics (an empty binding set — sensor-agnostic te, 2026-07-13,
    docs/WORK_ORDER_sensor_agnostic_te.md: there is no TE-original mirror wiring
    to fall back to any more, since a te never installs one of its own) BEFORE
    the gretaps are deleted below -- reuses `_compose_ops` exactly as a narrow-
    to-nothing apply would. Runs the composed `install-ovs-mirror` ops
    directly against the adapter (bypassing the executor/state-store
    tracking -- this is a teardown-time action, like `destroy_deploy`'s own
    `backend.*` calls, not a normal deploy op). The caller wraps this in a
    broad try/except: if the TE (or its VM) is already gone -- e.g. this
    runs via `cascade_destroy_for_te` AFTER the TE's own destroy already
    removed the VM -- a failure here is EXPECTED and must never block the
    actual gretap teardown that follows.
    """
    from axrange.planner.cluster import ClusterContext
    from axrange.planner.executor import ExecutionContext

    inv = _scan_te_inventory(store, te_deploy_id)
    affected_hosts = {
        h
        for op in store.ops_for_deploy(tap_deploy_id)
        if op.op_name == "install-ovs-mirror"
        for h in [_json_obj(op.scope_json).get("host")]
        if h
    }
    if not affected_hosts:
        return

    revert_plan = _compose_ops(
        plan_id, inv, {}, {}, store, default_node, prior_hosts=affected_hosts,
    )
    cluster = ClusterContext.single_node(default_node)
    ctx = ExecutionContext(
        plan_id=plan_id, deploy_id=tap_deploy_id, cluster=cluster,
        placement={}, timeout_s=None, tier="te",
    )
    for op in revert_plan.ops.values():
        if op.op_name != "install-ovs-mirror":
            continue
        host = op.scope.get("host")
        try:
            adapter.run(op, dict(op.inputs_resolved), ctx)
            log(f"  - mirror-descope host={host}: reverted to empty (no TE-original to fall back to)")
        except Exception as e:  # noqa: BLE001 -- best-effort (see docstring)
            log(f"  ! mirror-descope host={host}: {e}")


def destroy_taps(
    *,
    db_path: str,
    target: Optional[str] = None,
    deploy_id: Optional[str] = None,
    adapter_config: Optional[str] = None,
    node: Optional[str] = None,
    teardown_backend: Any = None,
    adapter: Any = None,
    log: Callable[[str], None] = lambda _m: None,
) -> Any:
    """Tear down a tap-plan's `tap_`-tier resources: first a best-effort
    de-scope pass reverting every affected host's install-ovs-mirror back to
    EMPTY tapped_nics (S2, `_revert_mirrors_to_te_original` — no TE-original
    wiring exists to fall back to, sensor-agnostic te, 2026-07-13),
    THEN the engine's OWN teardown (`planner.destroy_deploy`) against the
    tap-plan's deploy for the gretaps -- reused verbatim, not reimplemented.
    Leaves the target TE and the O2 sensors completely untouched (they are
    different `deploy_id`s the tap-plan's own `ops` table never references).

    Returns the `planner.teardown.TeardownResult` unchanged (the CLI/API
    surfaces map it, same as `axrange destroy` / `axrange o2 down` do).
    """
    from axrange.adapter_proxmox import load_config
    from axrange.planner import StateStore, TeardownError, destroy_deploy
    from axrange.planner.locks import Heartbeat
    from axrange.planner.state import default_process_info

    store = StateStore(db_path)
    try:
        did = deploy_id
        if did is None:
            if target is None:
                raise TapPlanError(
                    "destroy taps needs target= or deploy_id=", status=400
                )
            # Resolve a scenario/twin-config NAME to the concrete te deploy id
            # the `plan_id` was keyed off -- `apply_tap_plan` resolves the same
            # way BEFORE building `plan_id = f"tap-{...}"`, so a name-addressed
            # destroy could never find a plan created by name. Symmetric to the
            # tfc-tier fix (W1 dev45 live-verify, 2026-08-04); this gap predates
            # W1 (`docs/WORK_ORDER_tap_target_resolution.md`, 2026-07-25, added
            # name resolution to apply but not destroy). A concrete id passes
            # through unchanged (the cascade passes the concrete `te_deploy_id`,
            # so it is byte-identical to before); falls back to the raw target
            # so a stale name still gives the clear "no deploy found" error.
            try:
                resolved_target = resolve_target_deploy(store, target)
            except TapPlanError:
                resolved_target = target
            existing = store.find_deploy_for_plan(f"tap-{resolved_target}")
            if existing is None:
                raise TapPlanError(
                    f"no tap-plan deploy found for target {target!r}", status=404
                )
            did = existing.deploy_id

        d = store.get_deploy(did)
        if d is None:
            raise TapPlanError(f"deploy not found: {did}", status=404)

        # S4: STRUCTURAL guard, not just the plan_id-prefix naming
        # convention -- a tap-plan deploy is tier="te", so a scenario
        # literally named `tap-*` would ALSO produce a `te` deploy whose
        # plan_id starts with `tap-`; refuse unless this deploy's recorded
        # op names are a NON-EMPTY subset of exactly the three OP-MIR ops a
        # tap-plan can ever carry (`_is_tap_shaped_op_names` -- `set() <=
        # _TAP_OP_NAMES` is vacuously True, so a zero-op `tap-*`-named te
        # must not pass on the subset check alone). The prefix check stays
        # as a cheap secondary signal (both must hold).
        op_names = {op.op_name for op in store.ops_for_deploy(did)}
        is_tap_shaped = _is_tap_shaped_op_names(op_names)
        if not (d.plan_id.startswith("tap-") and is_tap_shaped):
            raise TapPlanError(
                f"refusing to tear down {did}: not a tap-plan deploy "
                f"(plan_id={d.plan_id!r}, op_names={sorted(op_names)}) -- "
                "axrange o2 tap destroy only tears down tap-playout deploys "
                "(attach-gre-tunnel/install-ovs-mirror/verify-capture ops "
                "only); use axrange destroy for a target-environment deploy",
                status=400,
            )

        cfg = load_config(adapter_config)
        if node:
            cfg.default_node = node

        backend = teardown_backend
        if backend is None:
            from axrange.adapter_proxmox import ProxmoxTeardown

            backend = ProxmoxTeardown(cfg)

        real_adapter = adapter
        if real_adapter is None:
            from axrange.adapter_proxmox import ProxmoxAdapter

            real_adapter = ProxmoxAdapter(cfg)

        lease = store.acquire_lease(
            d.scenario_id, did, process_info=default_process_info()
        )
        if not lease.acquired:
            raise TapPlanError(
                f"another operation on tap-plan {d.scenario_id!r} is in "
                f"progress (deploy_id={lease.held_by_deploy_id})", status=409,
            )

        heartbeat = Heartbeat(str(db_path), deploy_id=did, scenario_id=d.scenario_id)
        heartbeat.start()
        try:
            # S2 de-scope pass -- best-effort, see
            # `_revert_mirrors_to_te_original`'s docstring for why a failure
            # here (e.g. the TE/VM is already gone) must not block the
            # gretap teardown below.
            te_deploy_id = d.plan_id[len("tap-"):]
            try:
                _revert_mirrors_to_te_original(
                    store, did, te_deploy_id, d.plan_id,
                    adapter=real_adapter, default_node=cfg.default_node, log=log,
                )
            except Exception as e:  # noqa: BLE001
                log(f"  ! tap-plan de-scope pass failed: {e}")

            try:
                result = destroy_deploy(
                    store, backend, did, node=node, log=log,
                    protected_names=cfg.protected_external_names,
                    protected_vmids=cfg.protected_external_vmids,
                )
            except TeardownError as e:
                raise TapPlanError(str(e), status=404) from e
        finally:
            heartbeat.stop()
            try:
                store.release_lease(d.scenario_id, did)
            except Exception:  # noqa: BLE001
                pass
        return result
    finally:
        store.close()


# ---------------------------------------------------------------------------
# S3 -- a `te` destroy cascades to its standalone tap-plan (`docs/design/
# 06-taps-sensors.md`). Called by `axrange.cli.main.cmd_destroy`
# and `axrange.api.service.destroy_deploy` right after the TE's OWN teardown
# -- NOT by this module's own destroy path (a tap-plan destroying itself has
# nothing to cascade to).
# ---------------------------------------------------------------------------
def cascade_destroy_for_te(
    *,
    db_path: str,
    te_deploy_id: str,
    adapter_config: Optional[str] = None,
    node: Optional[str] = None,
    teardown_backend: Any = None,
    adapter: Any = None,
    log: Callable[[str], None] = lambda _m: None,
) -> Optional[Any]:
    """If `te_deploy_id` has a standalone tap-plan bound to it, tear it down
    too. Returns the tap-plan's `TeardownResult`, or `None` if no tap-plan
    was ever applied to this TE (the common case for most TEs -- NOT an
    error worth surfacing on a TE destroy). Any OTHER failure during the
    cascade is logged and swallowed: the TE's own destroy result is what the
    caller must act on; a stuck tap-plan cascade is finished by a later
    `axrange o2 tap destroy` retry (idempotent) or the next `axrange destroy`
    re-run on the TE (this cascade is itself idempotent -- `destroy_deploy`
    is check-then-act).
    """
    try:
        return destroy_taps(
            db_path=db_path, target=te_deploy_id, adapter_config=adapter_config,
            node=node, teardown_backend=teardown_backend, adapter=adapter, log=log,
        )
    except TapPlanError as e:
        if e.status == 404:
            return None
        log(f"  ! tap-plan cascade for {te_deploy_id}: {e.message}")
        return None


# ---------------------------------------------------------------------------
# `axrange tap status` / `axrange tap info` (work order §3.1/§3.2, previously
# deferred verbs) -- read-only, derived purely from `state.db` (§3.6's
# discovery posture: an unbounded, filter-scoped query, not a capped scan --
# LES-053).
# ---------------------------------------------------------------------------
def is_tap_plan_deploy(store: Any, row: Any) -> bool:
    """The FULL structural test for "is this deploy row a tap-plan
    playout" -- the SAME discriminator `destroy_taps`'s S4 guard uses
    (`plan_id` prefix `tap-` AND a non-empty op-name subset of
    `_TAP_OP_NAMES`), not the naming convention alone. A genuine `te`
    scenario literally named `tap-foo` produces a `tier="te"` row whose
    `plan_id` ALSO starts with `tap-` -- the op-name check is what tells
    the two apart on a READ/discovery path (`list-deploys --tier tap`,
    `axrange tap status`/`info`'s bare-arg default), which used to trust
    the prefix alone (code review finding, `docs/WORK_ORDER_cli_lifecycle.md`).
    Needs `store` (an already-open StateStore) to read the deploy's ops."""
    if (row.tier or "te") != "te" or not row.plan_id.startswith("tap-"):
        return False
    op_names = {op.op_name for op in store.ops_for_deploy(row.deploy_id)}
    return _is_tap_shaped_op_names(op_names)


def latest_tap_plan_deploy(store: Any, *, exclude_destroyed: bool = False) -> Any:
    """Most recent GENUINE tap-plan playout deploy -- `axrange tap status`/
    `info`/`destroy`'s bare-arg default. `plan_id` prefix `tap-` is only the
    first-pass filter (`StateStore.deploys_matching`'s unbounded,
    filter-scoped query, LES-053 -- not a capped recent-N scan); a genuine
    `te` scenario literally named `tap-foo` also matches that prefix, so
    every prefix-matched candidate is walked, most-recent-first, until one
    passes the FULL structural check (`is_tap_plan_deploy`).

    `exclude_destroyed=True` -- `tap destroy`'s bare-default ONLY (code
    review finding, docs/WORK_ORDER_cli_lifecycle.md): `mark_destroyed`
    bumps `updated_at`, so a just-destroyed tap-plan deploy sorts as the
    newest -- without this, a bare `tap destroy` right after destroying an
    OLDER tap-plan would resolve to that tombstone (a safe but misleading
    no-op "DESTROYED" while a genuinely live, newer tap-plan deploy goes
    untouched). `tap status`/`tap info`'s bare-default deliberately leaves
    this `False` -- they WANT the tombstone on top."""
    for row in store.deploys_matching(
        tier="te", plan_id_prefix="tap-", exclude_destroyed=exclude_destroyed,
    ):
        if is_tap_plan_deploy(store, row):
            return row
    return None


def _bindings_summary_from_ops(ops: Any) -> list[TapBindingResult]:
    """Group a tap-plan deploy's persisted `attach-gre-tunnel[$net@$sensor]`
    ops into `tap -> [sensors]` (net-level, matching `TapPlanStatus`'s
    existing shape -- `apply_tap_plan`'s own return value).

    `direction` (fix round G5, `docs/WORK_ORDER_tap_scopes.md`, final
    round) -- `attach-gre-tunnel` itself carries no direction (that lives
    on `install-ovs-mirror`'s persisted `tapped_nics`, the SAME source
    `tap_info_from_deploy` already reads back from, `or "both"` for a
    pre-M5-persisted entry, LES-096), so this does a second pass over the
    SAME `ops` to derive it -- unioned across every (host, nic, sensor)
    entry recorded for this net_id, since `_bindings_summary_from_ops` is
    NET-level (unlike `TapBindingInfo`'s per-(host,net,nic) granularity) --
    more than one direction can legitimately appear for the same net_id
    (`"both+rx"`, mirroring `TapPlanResolution.binding_directions`).

    Filters BOTH loops to `status == COMPLETED` (M2, code review, LES-113
    scope widening -- GAP-044's write-back fix made this mandatory, not
    optional: `set_op_running` now stamps the CURRENT plan's inputs onto a
    row the instant an op starts an attempt, even on the `LockResolutionError`/
    lock-timeout paths where the adapter is never actually called
    (`attempts=0`). Unfiltered, a never-applied set of inputs would render
    as "what's live" -- the exact GAP-044 over-report, through a new door.
    Trade accepted deliberately, same as `traffic.py`'s
    `_assignments_summary_from_ops`: mid-apply (right after
    `force_reset_ops`, before the executor reaches an op) and after a failed
    apply, `tap status` reports FEWER bindings than the desired plan -- the
    honest answer; the deploy-level `status` field already carries the
    running/failed signal for the whole plan."""
    ops_list = list(ops)
    m: dict[str, set[str]] = {}
    for op in ops_list:
        if op.op_name != "attach-gre-tunnel" or op.status != OpStatus.COMPLETED:
            continue
        scope = _json_obj(op.scope_json)
        net_id, sid = scope.get("net"), scope.get("sensor")
        if net_id and sid:
            m.setdefault(str(net_id), set()).add(str(sid))
    net_directions: dict[str, set[str]] = {}
    for op in ops_list:
        if op.op_name != "install-ovs-mirror" or op.status != OpStatus.COMPLETED:
            continue
        inp = _json_obj(op.inputs_resolved_json)
        for e in inp.get("tapped_nics") or []:
            if not isinstance(e, dict):
                continue
            net_id = e.get("net_id")
            if not net_id:
                continue
            net_directions.setdefault(str(net_id), set()).add(
                str(e.get("direction") or "both")
            )
    return [
        TapBindingResult(
            tap=k, sensors=sorted(v),
            direction=(
                "+".join(sorted(net_directions[k])) if k in net_directions else None
            ),
        )
        for k, v in sorted(m.items())
    ]


def tap_status(
    *, db_path: str, target: Optional[str] = None, deploy_id: Optional[str] = None,
) -> TapPlanStatus:
    """Read-only: an applied tap-plan's status + net-level bindings, derived
    purely from `state.db` (`axrange tap status`, previously deferred).
    Mirrors `control.surface_status`'s shape/posture."""
    from axrange.planner import StateStore

    store = StateStore(db_path)
    try:
        did = deploy_id
        resolved_target = target
        if did is not None:
            # The positional [ID] may be a tap deploy id (`tap-<twin>-<hash>`)
            # OR a TWIN/target id. A twin id has a non-`tap-` plan_id, so using
            # it directly reads tap bindings off the te deploy (none) -- the
            # `bindings: (none)` bug. Resolve a non-tap id to its applied tap
            # deploy via find_deploy_for_plan, IDENTICALLY to `tap_info` and the
            # fixed `cmd_tap_info`, so `tap status`/`tap info` behave the same on
            # the same argument.
            d0 = store.get_deploy(did)
            if not (d0 is not None and d0.plan_id.startswith("tap-")):
                existing = store.find_deploy_for_plan(f"tap-{did}")
                resolved_target = did
                did = existing.deploy_id if existing is not None else None
        elif target is not None:
            existing = store.find_deploy_for_plan(f"tap-{target}")
            did = existing.deploy_id if existing is not None else None
        else:
            row = latest_tap_plan_deploy(store)
            did = row.deploy_id if row is not None else None

        if did is None:
            return TapPlanStatus(deploy_id=None, target=resolved_target or "", status="not-applied")
        d = store.get_deploy(did)
        if d is None:
            return TapPlanStatus(deploy_id=None, target=resolved_target or "", status="not-applied")
        te_id = resolved_target or (
            d.plan_id[len("tap-"):] if d.plan_id.startswith("tap-") else ""
        )
        bindings = _bindings_summary_from_ops(store.ops_for_deploy(did))
        return TapPlanStatus(deploy_id=did, target=te_id, status=d.status.value, bindings=bindings)
    finally:
        store.close()


def tap_info(
    *, db_path: str, target: Optional[str] = None, deploy_id: Optional[str] = None,
    external_sensors: "Iterable[Any]" = (),
) -> TapInfo:
    """Read-only: the RICH per-binding "config + access facts" view of an
    applied tap-plan (the `TapInfo` shape `axrange tap info` renders), derived
    purely from `state.db` + the standing A1 surface + config-external sensors.
    The verbose peer of `tap_status` above; both are called by the CLI verb and
    the `GET /api/o2/taps` endpoint (LES-037 -- one resolver, two callers).

    `deploy_id` may be EITHER a tap deploy id (`tap-<twin>-<hash>`) OR a
    twin/target id; a non-tap id is resolved to its applied tap deploy via
    `find_deploy_for_plan(f"tap-{id}")`, the same disambiguation `tap status
    --target` uses and `cmd_tap_info` was fixed to apply (2026-07-26)."""
    from axrange.o2control.control import surface_status
    from axrange.planner import StateStore

    store = StateStore(db_path)
    try:
        did = deploy_id
        resolved_target = target
        if did is not None:
            d0 = store.get_deploy(did)
            if not (d0 is not None and d0.plan_id.startswith("tap-")):
                existing = store.find_deploy_for_plan(f"tap-{did}")
                if existing is not None:
                    resolved_target = did
                    did = existing.deploy_id
        elif target is not None:
            existing = store.find_deploy_for_plan(f"tap-{target}")
            did = existing.deploy_id if existing is not None else None
        else:
            row = latest_tap_plan_deploy(store)
            did = row.deploy_id if row is not None else None

        if did is None:
            return TapInfo(
                target=resolved_target or "", target_status="unknown",
                deploy_id=None, status="not-applied",
            )
        d = store.get_deploy(did)
        if d is None:
            return TapInfo(
                target=resolved_target or "", target_status="unknown",
                deploy_id=None, status="not-applied",
            )
        tgt = resolved_target or (
            d.plan_id[len("tap-"):] if d.plan_id.startswith("tap-") else "?"
        )
        te = store.get_deploy(tgt)
        surface = surface_status(db_path=db_path)
        sensor_by_id = {s.sensor_id: s for s in surface.sensors}
        warnings: list[str] = []
        sensor_by_id = merge_external_sensors(
            sensor_by_id, external_sensors, warnings=warnings,
        )
        return tap_info_from_deploy(
            store, did, target=tgt,
            target_status=te.status.value if te else "unknown",
            sensor_by_id=sensor_by_id, warnings=warnings,
        )
    finally:
        store.close()


def _tap_binding_infos(
    iface_sensors: dict[tuple[str, str, int], dict[str, str]],
    inv: "_TeInventory",
    sensor_by_id: dict[str, Any],
    plan_id: str,
) -> list[TapBindingInfo]:
    out: list[TapBindingInfo] = []
    for (host, net_id, nic), sid_directions in sorted(iface_sensors.items()):
        bridge = _gre_naming_bridge(inv, net_id, plan_id)
        b = TapBindingInfo(
            host=host, net_id=net_id, nic_index=nic, sensors=sorted(sid_directions),
        )
        for sid in sorted(sid_directions):
            b.gre_ports[sid] = gre_port_for_bridge_sensor(bridge, sid) if bridge else None
            sensor = sensor_by_id.get(sid)
            b.sensor_transport_ips[sid] = sensor.transport_ip if sensor else None
            # A config-external sensor (`deploy_id == ""`) never gets a
            # verify-capture heartbeat -- flag it here rather than leaving
            # it looking identical to a heartbeat-confirmed A1 sensor.
            b.capture_verified[sid] = not (sensor is not None and sensor.deploy_id == "")
            b.directions[sid] = sid_directions[sid]
        out.append(b)
    return out


def tap_info_from_resolution(
    resolution: TapPlanResolution, *, target: str,
) -> TapInfo:
    """The "resolved config, not yet applied" tier of `axrange tap info
    <spec>` (work order §3.2's two-tier behavior) -- built from the SAME
    resolution `axrange tap validate`/`tap plan` use, no second read of
    live state."""
    # Fix round G2 (`docs/WORK_ORDER_tap_scopes.md`, final round): the SAME
    # `plan_id = f"tap-{target}"` convention `compose_tap_ops`/`apply_tap_
    # plan` use, so this PREVIEW shows the identical (possibly twin-
    # discriminated, for `perimeter: wan`) gretap device name a real apply
    # would actually create.
    plan_id = f"tap-{target}"
    return TapInfo(
        target=target, target_status=resolution.target_status, deploy_id=None,
        status="not-applied",
        bindings=_tap_binding_infos(
            resolution.iface_new_sensors, resolution.inv, resolution.sensor_by_id,
            plan_id,
        ),
        warnings=list(resolution.warnings),
    )


def tap_info_from_deploy(
    store: Any, tap_deploy_id: str, *, target: str, target_status: str,
    sensor_by_id: dict[str, Any], warnings: Optional[list[str]] = None,
) -> TapInfo:
    """The "config + access facts" tier of `axrange tap info` (a live
    tap-plan deploy, given by id or the bare-arg default) -- reconstructs
    bindings from the persisted `install-ovs-mirror[$host]` ops' own
    `tapped_nics` metadata (which already carries the RESOLVED gre_port
    string per `(nic, sensor)`, `_compose_ops`'s `resolved[key]` -- no need
    to recompute via `gre_port_for_bridge_sensor` a second time).

    Filters to `status == COMPLETED` (M2, code review, same trade as
    `_bindings_summary_from_ops` above -- a `pending`/`failed` row's
    `tapped_nics` may be either stale (not yet re-run) or never-applied
    (`LockResolutionError`/lock-timeout, `attempts=0`); neither belongs in
    a "what's live" view."""
    bindings_map: dict[tuple[str, str, int], TapBindingInfo] = {}
    for op in store.ops_for_deploy(tap_deploy_id):
        if op.op_name != "install-ovs-mirror" or op.status != OpStatus.COMPLETED:
            continue
        scope = _json_obj(op.scope_json)
        host = scope.get("host")
        inp = _json_obj(op.inputs_resolved_json)
        for e in inp.get("tapped_nics") or []:
            if not isinstance(e, dict) or host is None:
                continue
            nic, net_id, sid = e.get("nic_index"), e.get("net_id"), e.get("sensor_id")
            if nic is None or not net_id or not sid:
                continue
            key = (str(host), str(net_id), int(nic))
            b = bindings_map.setdefault(
                key, TapBindingInfo(host=str(host), net_id=str(net_id), nic_index=int(nic))
            )
            if str(sid) not in b.sensors:
                b.sensors.append(str(sid))
            gre_key = e.get("gre_port_input")
            b.gre_ports[str(sid)] = inp.get(gre_key) if gre_key else None
            sensor = sensor_by_id.get(str(sid))
            b.sensor_transport_ips[str(sid)] = sensor.transport_ip if sensor else None
            b.capture_verified[str(sid)] = not (sensor is not None and sensor.deploy_id == "")
            # `direction` (`docs/WORK_ORDER_tap_scopes.md` M5/M6) -- persisted
            # per-entry on `tapped_nics`; defaults to `"both"` for a
            # pre-M5-persisted op the SAME way `ssh.py`'s adapter-side
            # `_resolve_nic_gretaps` does (LES-096: never silently narrow an
            # already-completed op's behavior).
            b.directions[str(sid)] = str(e.get("direction") or "both")

    d = store.get_deploy(tap_deploy_id)
    status = d.status.value if d is not None else "not-applied"
    bindings = [bindings_map[k] for k in sorted(bindings_map)]
    for b in bindings:
        b.sensors = sorted(b.sensors)
    return TapInfo(
        target=target, target_status=target_status, deploy_id=tap_deploy_id,
        status=status, bindings=bindings, warnings=list(warnings or []),
    )
