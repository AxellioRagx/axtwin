"""axrange.o2control.exercise -- the Exercise-tier (`exr`) playout
(`docs/WORK_ORDER_exercise_tier.md`, `docs/design/08-exercise-measurement.md`).

Runs an **attack playout** (internal `activity` and/or a Sandcat/Caldera
adversary plant) + records **defense-tuning** notes + threads a
**measurement** run label against an ALREADY-DEPLOYED twin (a `te`-tier
deploy) -- the fourth standalone playout, modeled closely on
`axrange.o2control.taps`/`axrange.o2control.traffic` (LES-037 -- reuse the
shared build/execute/release machinery, don't reimplement it):

  * **Binds to something ALREADY DEPLOYED**, like a tap-plan/traffic-profile
    -- there is nothing to EMIT from an IR walk (`pass1_emit`/`build_plan`
    never see this; see that module's Exercise-tier scope-shift note). This
    module composes a `Plan` BY HAND out of `OpInstance`s, reusing
    `axrange.ops.registry` for catalog metadata, exactly as `taps.py`'s
    `_make_mir_op` / `traffic.py`'s `_make_tfc_op` do.
  * **Runs through the SAME engine executor** (`axrange.api.service.
    run_deploy_executor` / `release_deploy_resources`) every other deploy
    lifecycle uses -- no second "build plan, run executor, release
    resources" implementation. Teardown reuses `planner.teardown.
    destroy_deploy` verbatim, same as `taps.py`/`traffic.py`.
  * **Uses `tier="exr"`** -- a real, first-class deploy tier already
    (`docs/WORK_ORDER_tier_split.md` added it to `_VALID_TIERS`/
    `TIER_SHAPE_CHECKS`/the name-shape producers/the six-tier teardown
    protect matrix, all unit/suite-verified), so this module's destroy
    discovery is a plain `deploys.tier == "exr"` filter, no structural
    discriminator needed (mirrors `traffic.py`'s `tfc` posture, not
    `taps.py`'s `tap`-reuses-`"te"` workaround).

NO NEW OP_CATALOG ENTRY. Unlike A2 (`OP-MIR-005`) or the Traffic tier
(`OP-TFC-010`), this playout reuses ops that ALREADY EXIST and already have a
config-class execution backend: `deploy-behavior-agent` / `start-activity` /
`wait-actor-ready` (OP-ACT-010/020/030, the inline-steps activity engine) and
`install-agent` (OP-OVL-070, agent_kind=adversary -- the Sandcat plant).
Their adapter-side implementation is `axrange.config_exec.ConfigBackend`
UNCHANGED (every one of these ops already reads `vmid`/`host_id`/... straight
out of `resolved_inputs` and calls `ctx.cluster.default_node` -- no
`inputs_from_outputs`-only wiring assumption to work around, unlike A2's
verify-capture or the Traffic tier's persona drivers), so this module reuses
`ConfigBackend` directly, exactly the way `taps.py` already does (`taps.py`
line ~1608). No new backend class is needed here (contrast `traffic.py`'s
`PersonaBackend`, which exists ONLY to dodge a real import cycle that does
not apply to this module).

**This is the te->exr re-homing the sensor-agnostic-te work order set the
precedent for**: `axrange.planner.pass1_emit` no longer emits the OP-ACT trio
or `overlays.adversary`'s `install-agent` for a `te`-tier deploy at all (see
that module's Exercise-tier scope-shift comments) -- the ops themselves stay
in the catalog untouched; only the EMISSION TRIGGER moves, from a `te`
deploy's own IR walk to this module's hand-composed plan against an
already-deployed twin. A scenario's `layers.activity:`/`overlays.adversary:`
blocks still parse/validate (WARNING REF-260, not an error) so the shipped
archetypes (`ad-domain-lab`, `purple-team-range`, `soc-detection-lab`) keep
validating -- that content is now **exr-consumed**, hand-copied into an
exercise-spec file's `attack.activity`/`attack.adversary` block (this module
does NOT re-parse the twin's original scenario IR -- see the module-level
"Exercise spec" comment below for why).

**EPHEMERAL, unlike `tfc`/`tap` (standing, converge-in-place)**: every
`apply_exercise()` call mints a BRAND NEW `plan_id`/`deploy_id`
(`exr-<twin>-<token>`) -- there is no `find_deploy_for_plan` resume/reuse.
This is deliberate: an exercise RUN is a one-shot execution against the twin
(fire the attack, measure, `exr destroy`, tune defense, run again = REPLAY),
not a converging desired-state binding like a tap-plan or traffic-profile.
**Two runs against the SAME twin can coexist ONLY on DISJOINT target
hosts** (narrowed, code review S2): the in-guest agent handle a run installs
-- the jittered activity scheduler's path, or the `axr-sandcat.service` unit
-- is host-scoped, not deploy-scoped, so two runs sharing a host would
reconverge/stop each other's agent with no way to tell them apart.
`apply_exercise` refuses (`ExercisePlanError`, fail-loud) a new run whose
target host set overlaps an existing NON-DESTROYED exr run's hosts for the
same twin (`_check_no_live_host_overlap`) -- destroy the prior run first,
which is exactly the replay loop. Beyond that overlap guard there is no
twin-scoped mutex; each run's own lease is keyed on ITS OWN fresh plan_id
(same as a `te` deploy's own scenario-id lease), matching the "run with
traffic on or off, replay identically" freedom the design brief calls for.

**Replay determinism**: `playout_hash()` hashes the composed plan's
(op_name, scope, inputs_resolved) tuples (order-independent, plan_id-
independent) and is persisted onto the deploy row's existing `ir_hash` column
(no state.db schema change -- `ir_hash` already exists for every tier; tfc/tap
just leave it `""`). Re-running `apply_exercise()` with the SAME spec+seed
against the SAME twin produces the SAME hash (mints a NEW deploy_id, since
each run is its own ephemeral execution) -- that equality IS the "identical
playout" assertion the work order calls for.

Boundaries (LES-025): this module imports the engine (`planner`,
`adapter_proxmox`, `ops.registry`, `config_exec`) and `axrange.ir.schema`
(reuses `Activity`/`Schedule`/`ScheduleInterval`/`AdversaryEntry` -- the SAME
shapes a scenario's `layers.activity:`/`overlays.adversary:` already use, so
an operator can copy-paste those blocks straight into an exercise-spec file)
-- both DOWNWARD, exactly like `traffic.py` reusing `axrange.personas`. No
sibling playout (`taps`/`traffic`) is imported (no dependency between
playouts). The engine never imports `axrange.o2control`.

**Exercise spec** -- a named YAML doc loaded like a tap-plan/traffic-profile
(NOT a reference into the twin's original scenario IR -- the twin's own
`layers.activity:`/`overlays.adversary:` content is inert now that `te` no
longer emits it, but an operator can literally copy those blocks into an
exercise-spec's `attack.activity`/`attack.adversary`, since both reuse the
identical IR pydantic shapes):

    exercise:
      twin: <te-deploy-id>
      attack:
        activity:                     # optional -- internal/automated mode (OP-ACT)
          id: ad-kill-chain
          host: attacker              # a host_id in the twin's published inventory
          seed: 42
          schedule: { ... }           # optional, same shape as IR Activity.schedule
          inline: { steps: [ ... ] }  # same shape as IR Activity.inline
        adversary:                    # optional -- external/manual Caldera mode (Sandcat plant)
          id: red-sandcat
          targets: [ws01, ws02]       # host_ids in the twin's published inventory
          server: http://caldera.o2.lab:8888
          group: red                  # optional
      defense: [ { note: "..." }, ... ]   # optional, INFORMATIONAL ONLY (§ "Defense
                                           # tuning" below) -- manual playout, no ops
      measurement: { run_label: ..., techniques: [...] }  # optional, INFORMATIONAL
      seed: 7                         # optional top-level FALLBACK: folded into
                                       # attack.activity.seed at parse time when the
                                       # latter is unset (fail-loud if both are set
                                       # to DIFFERENT values, or if no attack.activity
                                       # exists at all -- see
                                       # `_apply_top_level_seed_fallback`). Whichever
                                       # value wins is the one `playout_hash` reflects.

At least one of `attack.activity` / `attack.adversary` is required.

**Defense tuning is manual, by design** (`docs/design/08-exercise-measurement.md`:
"tuning manual for now") -- `defense:` is carried through into
`ExerciseStatus`/`ExerciseInfo` purely as an operator-facing note (what was
tuned before this run), never compiled into an op. Do not add automation here
without a fresh design decision.

**Measurement reuses A4, it does not reimplement it** (`docs/
design/08-exercise-measurement.md`): the activity runner ALREADY mints its own
`RUN_ID` per fire and ships `[axr-truth]` markers to Loki
(`config_exec/render.py::render_activity_runner`) -- nothing here needs to
inject a run-id into that path. `measurement:` here is a coarser, informational
label an operator attaches to THIS EXR DEPLOY (technique labels + a human
run_label) surfaced via `ExerciseStatus.measurement` alongside this deploy's
own `started_at`/`completed_at` window (`exercise_measurement_window()`) --
enough for an operator (or a future automated scorer, `axrange.measurement`,
explicitly out of scope here per the A4 brief's own "follow-on") to scope a
Loki query by technique + host + time window. This module never queries Loki
itself (`axrange.measurement` stays a leaf the engine/o2control does not
import at runtime, LES-025 -- consistent with A4's own "engine never imports
it" stance)."""
from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Optional

from axrange.ir.schema import Activity as IRActivity
from axrange.ir.schema import AdversaryEntry as IRAdversaryEntry
from axrange.ops.registry import get_op
from axrange.planner.dag import OpInstance, Plan, _op_id
from axrange.planner.inventory import HostInfo, InventoryError, TwinInventory

_DEPLOY_BEHAVIOR_OP = "deploy-behavior-agent"
_START_ACTIVITY_OP = "start-activity"
_WAIT_ACTOR_OP = "wait-actor-ready"
_INSTALL_AGENT_OP = "install-agent"


class ExercisePlanError(Exception):
    """An exercise-spec/playout failure with an HTTP-ish status hint -- the
    `exr`-tier counterpart to `TapPlanError`/`TrafficPlanError` (same shape,
    an independent class since this is its own tier/playout)."""

    def __init__(self, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


# ---------------------------------------------------------------------------
# Exercise spec parsing (see module docstring for the full shape)
# ---------------------------------------------------------------------------
@dataclass
class ActivityAttackSpec:
    host: str
    activity: IRActivity  # id/inline/schedule/seed -- behavior/overrides rejected


@dataclass
class AdversaryAttackSpec:
    entry: IRAdversaryEntry  # id/targets/server/group/adversary/techniques


@dataclass
class ExerciseSpec:
    twin: str
    activity: Optional[ActivityAttackSpec] = None
    adversary: Optional[AdversaryAttackSpec] = None
    defense_notes: list[dict[str, Any]] = field(default_factory=list)
    measurement: dict[str, Any] = field(default_factory=dict)
    seed: Any = None


def _parse_activity_attack(raw: dict[str, Any]) -> ActivityAttackSpec:
    if not isinstance(raw, dict):
        raise ExercisePlanError("attack.activity must be a mapping", status=400)
    host = raw.get("host")
    if not host or not isinstance(host, str):
        raise ExercisePlanError(
            "attack.activity.host (a host_id in the twin's published "
            "inventory) is required",
            status=400,
        )
    ir_fields = {k: v for k, v in raw.items() if k != "host"}
    try:
        act = IRActivity.model_validate(ir_fields)
    except Exception as e:  # noqa: BLE001 -- pydantic ValidationError, reported plainly
        raise ExercisePlanError(f"attack.activity: {e}", status=400) from e
    if not act.id:
        raise ExercisePlanError("attack.activity.id is required", status=400)
    if act.behavior is not None:
        raise ExercisePlanError(
            f"attack.activity {act.id!r}: library `behavior:` refs are not "
            "supported (inline-steps engine only -- docs/SLICE_ACTIVITY.md §3)",
            status=400,
        )
    if act.overrides is not None:
        raise ExercisePlanError(
            f"attack.activity {act.id!r}: `overrides:` requires the behavior "
            "library (deferred -- docs/SLICE_ACTIVITY.md §3)",
            status=400,
        )
    if act.inline is None or not act.inline.steps:
        raise ExercisePlanError(
            f"attack.activity {act.id!r}: the inline-steps engine requires a "
            "non-empty `inline.steps`",
            status=400,
        )
    if act.schedule is not None:
        sched = act.schedule
        interval = sched.interval
        if interval is not None:
            if not interval.min or not interval.max:
                raise ExercisePlanError(
                    f"attack.activity {act.id!r}: schedule.interval requires "
                    "both min and max",
                    status=400,
                )
            if sched.once:
                raise ExercisePlanError(
                    f"attack.activity {act.id!r}: schedule.interval is "
                    "mutually exclusive with schedule.once: true",
                    status=400,
                )
    return ActivityAttackSpec(host=host, activity=act)


def _parse_adversary_attack(raw: dict[str, Any]) -> AdversaryAttackSpec:
    if not isinstance(raw, dict):
        raise ExercisePlanError("attack.adversary must be a mapping", status=400)
    try:
        entry = IRAdversaryEntry.model_validate(raw)
    except Exception as e:  # noqa: BLE001 -- pydantic ValidationError, reported plainly
        raise ExercisePlanError(f"attack.adversary: {e}", status=400) from e
    if not entry.id:
        raise ExercisePlanError("attack.adversary.id is required", status=400)
    if not entry.targets:
        raise ExercisePlanError(
            f"attack.adversary {entry.id!r}: targets (host_ids to plant the "
            "Sandcat agent on) is required",
            status=400,
        )
    if not entry.server:
        raise ExercisePlanError(
            f"attack.adversary {entry.id!r}: server (the Caldera server this "
            "agent beacons to) is required",
            status=400,
        )
    return AdversaryAttackSpec(entry=entry)


def parse_exercise_spec(raw: dict[str, Any]) -> ExerciseSpec:
    """Sanity-check an already-loaded exercise-spec dict."""
    if not isinstance(raw, dict):
        raise ExercisePlanError("exercise-spec file root must be a mapping", status=400)
    body = raw.get("exercise")
    if body is None:
        raise ExercisePlanError(
            "exercise-spec file must have a top-level 'exercise:' key", status=400,
        )
    if not isinstance(body, dict):
        raise ExercisePlanError("'exercise:' must be a mapping", status=400)

    # OPTIONAL since 2026-08-04 (docs/WORK_ORDER_corp_enterprise_bundle.md §4,
    # Authority: user) -- parity with tfc/tap: `twin:` may be an already-minted
    # te deploy id OR a scenario/twin-config NAME (resolved to its newest live
    # te deploy at apply time), and may be omitted in favour of a
    # `--target`/API-body override, so a checked-in exr spec is a reusable
    # library file, not one welded to a dead deploy id (LES-072). A
    # present-but-malformed value is still a parse error, never coerced to
    # "absent".
    twin = body.get("twin")
    if twin is not None and (not isinstance(twin, str) or not twin):
        raise ExercisePlanError(
            "exercise.twin, if given, must be a non-empty string "
            "(a te deploy id, or a scenario/twin-config name)", status=400,
        )

    attack = body.get("attack") or {}
    if not isinstance(attack, dict):
        raise ExercisePlanError("exercise.attack must be a mapping", status=400)
    activity_raw = attack.get("activity")
    adversary_raw = attack.get("adversary")
    if not activity_raw and not adversary_raw:
        raise ExercisePlanError(
            "exercise.attack must declare at least one of `activity:` / "
            "`adversary:`",
            status=400,
        )
    activity = _parse_activity_attack(activity_raw) if activity_raw else None
    adversary = _parse_adversary_attack(adversary_raw) if adversary_raw else None

    defense_notes = body.get("defense") or []
    if not isinstance(defense_notes, list):
        raise ExercisePlanError("exercise.defense must be a list", status=400)
    measurement = body.get("measurement") or {}
    if not isinstance(measurement, dict):
        raise ExercisePlanError("exercise.measurement must be a mapping", status=400)

    top_seed = body.get("seed")
    _apply_top_level_seed_fallback(top_seed, activity)

    return ExerciseSpec(
        twin=twin, activity=activity, adversary=adversary,
        defense_notes=list(defense_notes), measurement=dict(measurement),
        seed=top_seed,
    )


def _apply_top_level_seed_fallback(
    top_seed: Any, activity: Optional["ActivityAttackSpec"],
) -> None:
    """S1 (code review, `docs/WORK_ORDER_exercise_tier.md`): the top-level
    `exercise.seed` must never be a SILENT no-op -- in the one tier whose
    contract is "same spec+seed -> identical playout", an ignored seed field
    would make `playout_hash` LIE about run-equality for an operator who set
    only the top-level field (LES-001/LES-004; LES-115 candidate).

    Resolution, in order:
      - No top-level `seed`: no-op, unchanged behavior.
      - `attack.activity` present, its own `seed` unset: FOLD the top-level
        value in as `activity.activity.seed` (mutated in place, pydantic
        `frozen=False`) -- from here on EVERY downstream reader
        (`compose_exercise_ops`'s `_schedule_to_dict`/`act.seed` threading,
        hence `playout_hash`) sees the SAME single effective seed, with NO
        separate hashing path to keep in sync.
      - `attack.activity` present, its own `seed` ALREADY set to a DIFFERENT
        value: fail loud -- two explicit, conflicting seeds is a real
        authoring error, not a silently-resolved precedence question.
      - No `attack.activity` at all (adversary-only): fail loud -- a
        top-level `seed` that cannot possibly reach any op must never be
        silently accepted (there is nothing else in the exr spec that draws
        from a seed today)."""
    if top_seed is None:
        return
    if activity is None:
        raise ExercisePlanError(
            "exercise.seed is set but there is no attack.activity to apply "
            "it to (only attack.activity.seed threads into a seeded run) -- "
            "drop exercise.seed or add attack.activity",
            status=400,
        )
    act_seed = activity.activity.seed
    if act_seed is None:
        activity.activity.seed = top_seed
    elif act_seed != top_seed:
        raise ExercisePlanError(
            f"exercise.seed ({top_seed!r}) and attack.activity.seed "
            f"({act_seed!r}) are both set to DIFFERENT values -- set only "
            "one (attack.activity.seed is the one that actually threads "
            "into the ops)",
            status=400,
        )


def load_exercise_spec(path: str | Path) -> ExerciseSpec:
    """Load + sanity-check an exercise-spec YAML file."""
    from axrange.ir.parser import IncludeError, StructuralError, load_yaml

    p = Path(path)
    try:
        raw = load_yaml(p)
    except (IncludeError, StructuralError) as e:
        raise ExercisePlanError(f"{p}: {e}", status=400) from e
    if raw is None:
        raw = {}
    return parse_exercise_spec(raw)


def resolve_exercise_target(store: Any, target: str) -> str:
    """Resolve an exercise's `twin:`/`--target` to a CONCRETE te deploy id.

    Delegates to `taps.resolve_target_deploy` rather than reimplementing the
    lookup (LES-037) -- "an already-minted deploy id passes through; otherwise
    the newest live non-tap-playout `te` deploy of the scenario by that name."
    Only the raised error type is adapted: an exr caller must not surface a
    `TapPlanError`. Mirrors `traffic.resolve_traffic_target` exactly."""
    from axrange.o2control.taps import TapPlanError, resolve_target_deploy

    try:
        return resolve_target_deploy(store, target)
    except TapPlanError as e:
        raise ExercisePlanError(
            f"exercise target {target!r} is neither a deploy id nor a live te "
            "deploy of a scenario by that name", status=e.status,
        ) from e


def resolve_exercise_spec(
    spec: Optional[ExerciseSpec], spec_path: Optional[str | Path],
) -> ExerciseSpec:
    """One spec wins, in order: an already-parsed `spec`, or a `spec_path` to
    load. Mirrors `taps.resolve_tap_plan`/`traffic.resolve_traffic_profile`."""
    if spec is not None:
        return spec
    if spec_path is not None:
        return load_exercise_spec(spec_path)
    raise ExercisePlanError("apply_exercise needs spec= or spec_path=", status=400)


# ---------------------------------------------------------------------------
# Resolution against the twin's published inventory -- NEVER twin internals
# (`docs/design/02-twin-model.md`).
# ---------------------------------------------------------------------------
@dataclass
class ResolvedActivityAttack:
    host_id: str
    vmid: int
    os_family: str
    activity: IRActivity


@dataclass
class ResolvedAdversaryTarget:
    host_id: str
    vmid: int
    os_family: str


@dataclass
class ExerciseResolution:
    target_status: str  # the target twin deploy's own DeployStatus value
    twin: TwinInventory
    activity: Optional[ResolvedActivityAttack] = None
    adversary_entry: Optional[IRAdversaryEntry] = None
    adversary_targets: list[ResolvedAdversaryTarget] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _require_vmid(h: HostInfo) -> int:
    if h.vmid is None:
        raise ExercisePlanError(
            f"host {h.host_id!r} has no recorded vmid (the twin deploy may be "
            "incomplete)",
            status=400,
        )
    return h.vmid


def resolve_exercise_bindings(spec: ExerciseSpec, store: Any, *, manifests: Any = None) -> ExerciseResolution:
    """Resolve `spec`'s attack targets against `store` (an ALREADY-OPEN
    `StateStore` -- the caller owns its lifecycle): the target twin must
    exist and be tier="te"; every `host:`/`targets:` entry must resolve to a
    host in its published inventory. Raises `ExercisePlanError` on any
    structural problem (mirrors `taps.resolve_tap_plan_bindings`'s /
    `traffic.resolve_traffic_bindings`'s fail-loud contract, LES-098/LES-001)."""
    from axrange.planner.inventory import build_twin_inventory

    te = store.get_deploy(spec.twin)
    if te is None:
        raise ExercisePlanError(f"target twin deploy not found: {spec.twin!r}", status=404)
    if (te.tier or "te") != "te":
        raise ExercisePlanError(
            f"target {spec.twin!r} is tier={te.tier!r}; an exercise's twin "
            "must be a target-environment ('te') deploy",
            status=400,
        )

    try:
        twin = build_twin_inventory(store, spec.twin, manifests=manifests)
    except InventoryError as e:
        raise ExercisePlanError(str(e), status=400) from e

    host_by_id = {h.host_id: h for h in twin.hosts}
    warnings: list[str] = []

    resolved_activity: Optional[ResolvedActivityAttack] = None
    if spec.activity is not None:
        host = host_by_id.get(spec.activity.host)
        if host is None:
            raise ExercisePlanError(
                f"attack.activity host {spec.activity.host!r} is not a host "
                f"in target twin {spec.twin!r}",
                status=400,
            )
        vmid = _require_vmid(host)
        resolved_activity = ResolvedActivityAttack(
            host_id=host.host_id, vmid=vmid, os_family=host.os or "linux",
            activity=spec.activity.activity,
        )

    resolved_targets: list[ResolvedAdversaryTarget] = []
    adversary_entry: Optional[IRAdversaryEntry] = None
    if spec.adversary is not None:
        adversary_entry = spec.adversary.entry
        for host_id in adversary_entry.targets or []:
            host = host_by_id.get(host_id)
            if host is None:
                raise ExercisePlanError(
                    f"attack.adversary {adversary_entry.id!r}: target "
                    f"{host_id!r} is not a host in target twin {spec.twin!r}",
                    status=400,
                )
            vmid = _require_vmid(host)
            resolved_targets.append(
                ResolvedAdversaryTarget(
                    host_id=host.host_id, vmid=vmid, os_family=host.os or "linux",
                )
            )

    return ExerciseResolution(
        target_status=te.status.value, twin=twin, activity=resolved_activity,
        adversary_entry=adversary_entry, adversary_targets=resolved_targets,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Op composition -- hand-built OpInstances reusing the EXISTING OP-ACT/
# OP-OVL-070 catalog metadata (no new OP_CATALOG entry -- see module
# docstring).
# ---------------------------------------------------------------------------
def _op_timeout_s(spec: Optional[str]) -> Optional[int]:
    if not spec or not spec.endswith("s"):
        return None
    try:
        return int(spec[:-1])
    except ValueError:
        return None


def _make_op(plan_id: str, op_name: str, scope: dict[str, str], inputs_resolved: dict[str, Any]) -> OpInstance:
    op_def = get_op(op_name)
    if op_def is None:  # pragma: no cover -- defensive; catalog names are fixed
        raise ExercisePlanError(f"unknown op name: {op_name}", status=500)
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


def _schedule_to_dict(activity: IRActivity) -> Optional[dict[str, Any]]:
    """Same resolved shape `pass1_emit._emit_activity` used to thread into
    the ops' inputs -- ported here since that function no longer exists
    (deleted with the te-side emission, not left as dead code)."""
    sched = activity.schedule
    if sched is None:
        return None
    interval = sched.interval
    return {
        "once": sched.once,
        "days": list(sched.days) if sched.days else None,
        "start": sched.start,
        "end": sched.end,
        "interval": (
            {"min": interval.min, "max": interval.max} if interval is not None else None
        ),
    }


def compose_exercise_ops(resolution: ExerciseResolution, plan_id: str) -> Plan:
    """Compose the exercise playout for an ALREADY-RESOLVED spec, WITHOUT
    running it -- also the `exr plan`/`exr validate` entry points' shared
    core."""
    plan = Plan(
        plan_id=plan_id, scenario_id=plan_id, scenario_version=None,
        ir_hash="", tier="exr",
    )

    if resolution.activity is not None:
        ra = resolution.activity
        act = ra.activity
        act_id = act.id or "activity"
        host_id = ra.host_id
        scope = {"act": act_id, "host": host_id}
        base_inputs: dict[str, Any] = {
            "activity_id": act_id, "host_id": host_id, "vmid": ra.vmid,
        }
        schedule_dict = _schedule_to_dict(act)
        if schedule_dict is not None:
            base_inputs["schedule"] = schedule_dict
        if act.seed is not None:
            base_inputs["seed"] = act.seed

        deploy_op = _make_op(
            plan_id, _DEPLOY_BEHAVIOR_OP, scope,
            {**base_inputs, "inline_steps": [dict(s) for s in (act.inline.steps or [])]},
        )
        plan.add_op(deploy_op)
        start_op = _make_op(plan_id, _START_ACTIVITY_OP, scope, dict(base_inputs))
        plan.add_op(start_op)
        plan.add_edge(deploy_op.op_id, start_op.op_id, kind="ordering")
        wait_op = _make_op(plan_id, _WAIT_ACTOR_OP, scope, dict(base_inputs))
        plan.add_op(wait_op)
        plan.add_edge(start_op.op_id, wait_op.op_id, kind="ordering")

    if resolution.adversary_entry is not None:
        entry = resolution.adversary_entry
        # `targets` is dropped from the per-op `agent_entry` copy (nice-to-
        # have, code review): the renderer (`render_sandcat_agent`/
        # `render_windows_sandcat_agent`) never reads it -- only `id`/
        # `group` -- and the FULL fan-out list embedded in every op made
        # `playout_hash` order-sensitive to `attack.adversary.targets`
        # (JSON preserves list order), so reordering an identical target
        # set changed the hash for an identical effective playout. Each
        # op's own `host_id` already carries the specific target this op is
        # about; the shared entry's `targets` is redundant here.
        entry_dict = entry.model_dump(exclude={"targets"})
        for target in resolution.adversary_targets:
            scope = {"agent": entry.id, "host": target.host_id}
            plan.add_op(
                _make_op(
                    plan_id, _INSTALL_AGENT_OP, scope,
                    {
                        "agent_id": entry.id,
                        "agent_kind": "adversary",
                        "host_id": target.host_id,
                        "vmid": target.vmid,
                        "agent_entry": dict(entry_dict),
                        "server": entry.server,
                        "os_family": target.os_family,
                    },
                )
            )

    plan.freeze()
    return plan


def playout_hash(plan: Plan) -> str:
    """Order-independent, plan_id-independent digest of a composed
    exercise's (op_name, scope, inputs_resolved) tuples -- the "replay
    produced an identical playout" assertion the work order calls for. Two
    `apply_exercise()` calls with the SAME spec+seed against the SAME twin
    mint DIFFERENT deploy_ids (ephemeral, see module docstring) but hash
    identically; a changed spec (different steps/seed/targets) hashes
    differently. Deliberately independent of op_id (which embeds plan_id,
    hence would differ run-to-run even for an identical spec)."""
    items = [
        {"op_name": op.op_name, "scope": dict(sorted(op.scope.items())), "inputs": op.inputs_resolved}
        for op in plan.ops.values()
    ]
    items.sort(key=lambda d: json.dumps(d, sort_keys=True, default=str))
    blob = json.dumps(items, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Result shapes
# ---------------------------------------------------------------------------
@dataclass
class ExerciseStatus:
    deploy_id: Optional[str]
    twin: str
    status: str  # "not-applied" | a DeployStatus value
    playout_hash: Optional[str] = None
    activity_host: Optional[str] = None
    adversary_targets: list[str] = field(default_factory=list)
    defense_notes: list[dict[str, Any]] = field(default_factory=list)
    measurement: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# S2 (code review): same-host coexistence guard. Two exr runs on DISJOINT
# hosts are fine (each run's in-guest agent handle -- the jittered activity
# scheduler path, or the `axr-sandcat.service` unit -- is host-scoped, not
# deploy-scoped, but two DIFFERENT hosts never share one). Two runs that
# BOTH target the SAME host collide on that host-global handle: a second
# apply reconverges the first run's agent (same scheduler path / same
# systemd unit name), and destroying either one stops BOTH -- there is no
# way to disambiguate "whose agent is this" once installed. Rather than
# deploy-scope the in-guest unit/process names (more invasive to the shared
# `config_exec` installer that `install-agent`/`deploy-behavior-agent` also
# serve for `te`/`tfc`), this REFUSES the overlap up front: a new exr run
# whose target host set intersects an existing NON-DESTROYED exr run's host
# set (same twin) is a fail-loud `ExercisePlanError`, not a silent collision.
# This preserves disjoint-host concurrency (the module's actual coexistence
# claim, narrowed from the earlier "any two runs coexist") while making the
# no-collision invariant explicit.
# ---------------------------------------------------------------------------
def _resolution_target_hosts(resolution: "ExerciseResolution") -> set[str]:
    hosts: set[str] = set()
    if resolution.activity is not None:
        hosts.add(resolution.activity.host_id)
    hosts.update(t.host_id for t in resolution.adversary_targets)
    return hosts


def _persisted_exercise_deploy_hosts(store: Any, deploy_id: str) -> set[str]:
    """Every host id a (possibly not-yet-run) exr deploy's OWN composed ops
    target -- `Executor.persist_plan()` writes every op row (as `pending`)
    BEFORE running any of them, so this is accurate even for a run that
    hasn't started executing yet, not just a completed one."""
    from axrange.planner.opview import op_fields

    hosts: set[str] = set()
    for op in store.ops_for_deploy(deploy_id):
        if op.op_name not in (_DEPLOY_BEHAVIOR_OP, _INSTALL_AGENT_OP):
            continue
        _name, _scope, inp, _out = op_fields(op)
        host_id = inp.get("host_id")
        if host_id:
            hosts.add(host_id)
    return hosts


def _check_no_live_host_overlap(store: Any, twin: str, new_hosts: set[str]) -> None:
    """Fail loud (LES-098/LES-001) if `new_hosts` intersects any existing
    NON-DESTROYED exr run's target hosts for the same twin -- see the
    section banner above for why this is the chosen fix over deploy-scoping
    the in-guest names."""
    if not new_hosts:
        return
    for d in store.deploys_matching(
        tier="exr", plan_id_prefix=f"exr-{twin}-", exclude_destroyed=True,
    ):
        overlap = new_hosts & _persisted_exercise_deploy_hosts(store, d.deploy_id)
        if overlap:
            raise ExercisePlanError(
                f"target host(s) {sorted(overlap)} already carry a live "
                f"exercise run ({d.deploy_id!r}) against twin {twin!r} -- "
                "two exr runs may not target the SAME host at once (the "
                "in-guest agent handle is host-scoped); destroy the prior "
                "run first (`axrange exr destroy`), then replay",
                status=409,
            )


# ---------------------------------------------------------------------------
# Apply -- ALWAYS a fresh deploy_id/plan_id (ephemeral, see module docstring
# -- no `find_deploy_for_plan` resume, unlike `taps`/`traffic`).
# ---------------------------------------------------------------------------
def apply_exercise(
    *,
    spec: Optional[ExerciseSpec] = None,
    spec_path: Optional[str | Path] = None,
    db_path: str,
    adapter_config: Optional[str] = None,
    target_node: Optional[str] = None,
    target: Optional[str] = None,
    config_backend: Any = None,
) -> ExerciseStatus:
    """Run an exercise: compose the attack playout's ops and run them through
    the engine executor (`run_deploy_executor` -- LES-037 reuse). Every call
    mints a brand-new `deploy_id` (`exr-<twin>-<token>`) -- an exercise run is
    a one-shot, ephemeral execution, not a converging desired-state binding
    (contrast `taps`/`traffic`'s "one deploy per target, re-apply converges"
    posture). `config_backend` is a test seam (inject a fake, no real QGA/
    Proxmox); production builds the real `axrange.config_exec.ConfigBackend`.
    """
    from axrange.adapter_proxmox import load_config
    from axrange.planner.entry import release_deploy_resources, run_deploy_executor
    from axrange.planner import StateStore
    from axrange.planner.cluster import ClusterContext
    from axrange.planner.locks import Heartbeat
    from axrange.planner.state import DeployStatus, default_process_info

    resolved_spec = resolve_exercise_spec(spec, spec_path)

    # `target` (the twin this exercise binds ONTO) overrides the spec's own
    # `twin:`; precedence override > spec > error. Unrelated to `target_node`
    # (a PVE cluster node) despite the spelling -- same split tfc uses.
    effective_target = target or resolved_spec.twin
    if not effective_target:
        raise ExercisePlanError(
            "no exercise target: set twin: in the spec or pass --target", status=400,
        )

    store = StateStore(db_path)
    try:
        # Resolve a scenario/twin NAME to a CONCRETE te deploy id BEFORE
        # anything else -- `plan_id = f"exr-{...}-<token>"` and the cascade
        # that finds this run (`exr-<te_deploy_id>-*`) must both key off the
        # concrete id, never a reusable name (LES-072; a name-authored run
        # would otherwise be a cascade-orphan). `replace()` carries every
        # field (LES-173).
        concrete_target = resolve_exercise_target(store, effective_target)
        if concrete_target != resolved_spec.twin:
            resolved_spec = replace(resolved_spec, twin=concrete_target)

        cfg = load_config(adapter_config)
        if target_node:
            cfg.default_node = target_node

        resolution = resolve_exercise_bindings(resolved_spec, store)
        if not cfg.default_node:
            raise ExercisePlanError(
                "DEFAULT_NODE missing; set it in adapter.conf, or override "
                "via target_node",
                status=400,
            )
        _check_no_live_host_overlap(
            store, resolved_spec.twin, _resolution_target_hosts(resolution),
        )
        ctx = ClusterContext.single_node(cfg.default_node)

        plan_id = f"exr-{resolved_spec.twin}-{secrets.token_hex(4)}"
        plan = compose_exercise_ops(resolution, plan_id)
        p_hash = playout_hash(plan)
        deploy_id = plan_id

        lease = store.acquire_lease(plan_id, deploy_id, process_info=default_process_info())
        if not lease.acquired:  # pragma: no cover -- plan_id is fresh per call
            raise ExercisePlanError(
                f"another exercise operation for {plan_id!r} is in progress "
                f"(deploy_id={lease.held_by_deploy_id})",
                status=409,
            )

        heartbeat = Heartbeat(str(db_path), deploy_id=deploy_id, scenario_id=plan_id)
        try:
            store.create_deploy(
                deploy_id=deploy_id, scenario_id=plan_id, scenario_version=None,
                scenario_schema_version=1, plan_id=plan_id, ir_hash=p_hash,
                placement={}, tier="exr",
            )

            backend = config_backend
            if backend is None:
                from axrange.config_exec import ConfigBackend

                backend = ConfigBackend(cfg)

            # `backend` fills BOTH the `adapter=` (infra) and `config_backend=`
            # slots -- valid only because every op this module composes is
            # config-class (deploy-behavior-agent/start-activity/
            # wait-actor-ready/install-agent), so `Executor._backend_for`
            # always routes to `config_backend` and the `adapter=` slot is
            # never actually invoked (mirrors `traffic.py`'s identical note).
            result = run_deploy_executor(
                store, backend, backend, plan, ctx, deploy_id, heartbeat,
            )
            status = "completed" if result.succeeded else "failed"
        finally:
            release_deploy_resources(store, plan_id, deploy_id, heartbeat)

        return ExerciseStatus(
            deploy_id=deploy_id, twin=resolved_spec.twin, status=status,
            playout_hash=p_hash,
            activity_host=(resolution.activity.host_id if resolution.activity else None),
            adversary_targets=[t.host_id for t in resolution.adversary_targets],
            defense_notes=list(resolved_spec.defense_notes),
            measurement=dict(resolved_spec.measurement),
            warnings=list(resolution.warnings),
        )
    finally:
        store.close()


# ---------------------------------------------------------------------------
# Destroy -- `exr_`-scoped ONLY. `naming.TIER_SHAPE_CHECKS`/`planner.
# teardown._protected()` already refuse every foreign-tier name an `exr`
# deploy's own ops could ever produce, so this deploy's `destroy_deploy` call
# structurally cannot touch the target twin's (or any other tier's)
# resources -- it only ever sees this deploy's OWN `deploy-behavior-agent`/
# `start-activity`/`wait-actor-ready`/`install-agent` rows, none of which
# create a Proxmox resource (no clone-vm/create-vnet/...), so the sweep is a
# clean no-op resource-wise; the ACTUAL cleanup work (stopping whatever this
# run started in-guest) happens in the best-effort pre-pass below.
# ---------------------------------------------------------------------------
def _stop_exercise_processes(
    store: Any, deploy_id: str, *, cfg: Any, ga_factory: Any = None,
    rest_client: Any = None, log: Callable[[str], None],
) -> None:
    """Best-effort: stop whatever persistent in-guest process this exercise
    deploy started, reading it back from the persisted ops (the SAME "derive
    purely from state.db" posture `taps.py`/`traffic.py`'s de-scope passes
    use). A ONE-SHOT activity needs no stop (the runner already finished on
    its own); only a JITTERED one (`schedule.interval` set) leaves a
    persistent scheduler process behind, so that's the only
    deploy-behavior-agent case that does anything here. An adversary plant
    (Sandcat) is always a persistent daemon/process, so every install-agent
    (agent_kind=adversary) row gets a best-effort stop. A failure here must
    never block the deploy-state transition that follows (mirrors
    `traffic.py::_stop_instances`)."""
    from axrange.adapter_proxmox.guest_agent import GuestAgent
    from axrange.adapter_proxmox.rest import RestClient
    from axrange.config_exec.render import activity_paths
    from axrange.planner.opview import op_fields

    factory = ga_factory or GuestAgent
    client = rest_client
    if client is None:
        client = RestClient(cfg)

    for op in store.ops_for_deploy(deploy_id):
        _op_name, _scope, inp, _out = op_fields(op)
        vmid = inp.get("vmid")
        if vmid is None:
            continue
        try:
            if op.op_name == _DEPLOY_BEHAVIOR_OP:
                schedule = inp.get("schedule") if isinstance(inp.get("schedule"), dict) else None
                if not schedule or not schedule.get("interval"):
                    continue  # one-shot: the runner already finished, nothing to stop
                activity_id = inp.get("activity_id") or "unknown"
                paths = activity_paths(str(activity_id))
                ga = factory(client, cfg.default_node)
                ga.exec(int(vmid), ["pkill", "-f", paths["scheduler"]], timeout_s=30)
                log(f"  - activity {activity_id}: jittered scheduler stopped")
            elif op.op_name == _INSTALL_AGENT_OP and inp.get("agent_kind") == "adversary":
                agent_id = inp.get("agent_id") or "unknown"
                os_family = (inp.get("os_family") or "linux").lower()
                ga = factory(client, cfg.default_node, os_family=os_family)
                if os_family == "windows":
                    ga.exec(
                        int(vmid),
                        [
                            "powershell", "-NoProfile", "-Command",
                            "Get-Process | Where-Object { $_.Path -eq "
                            "'C:\\axrange\\adversary\\splunkd.exe' } | "
                            "Stop-Process -Force -ErrorAction SilentlyContinue",
                        ],
                        timeout_s=30,
                    )
                else:
                    # `disable --now` (not a bare `stop`): the unit was
                    # installed `enable --now` (render_sandcat_agent), so a
                    # plain `stop` leaves it re-armed on the NEXT boot -- a
                    # twin reboot after `exr destroy` would silently
                    # resurrect a supposedly-torn-down Sandcat agent
                    # (nice-to-have, code review).
                    ga.exec(
                        int(vmid), ["systemctl", "disable", "--now", "axr-sandcat.service"],
                        timeout_s=30,
                    )
                log(f"  - adversary agent {agent_id}: stopped")
        except Exception as e:  # noqa: BLE001 -- best-effort, see docstring
            log(f"  ! stop failed for {op.op_name} ({op.op_id}): {e}")


def destroy_exercise(
    *,
    db_path: str,
    deploy_id: Optional[str] = None,
    adapter_config: Optional[str] = None,
    node: Optional[str] = None,
    teardown_backend: Any = None,
    ga_factory: Any = None,
    log: Callable[[str], None] = lambda _m: None,
) -> Any:
    """Tear down an exercise deploy: first a best-effort pass stopping
    whatever persistent process it started (`_stop_exercise_processes`), THEN
    the engine's OWN teardown (`planner.destroy_deploy`) against this deploy
    -- reused verbatim, not reimplemented (mirrors `traffic.
    destroy_traffic_profile`). Leaves the target twin (and every other tier)
    completely untouched: this deploy's `ops` table carries only
    config-class rows that create no Proxmox resource.

    Returns the `planner.teardown.TeardownResult` unchanged (the CLI/API
    surfaces map it, same as `axrange tap destroy` / `axrange tfc destroy`).
    """
    from axrange.adapter_proxmox import load_config
    from axrange.planner import StateStore, TeardownError, destroy_deploy
    from axrange.planner.locks import Heartbeat
    from axrange.planner.state import default_process_info

    if not deploy_id:
        raise ExercisePlanError("destroy needs deploy_id=", status=400)

    store = StateStore(db_path)
    try:
        d = store.get_deploy(deploy_id)
        if d is None:
            raise ExercisePlanError(f"deploy not found: {deploy_id}", status=404)
        if (d.tier or "te") != "exr":
            raise ExercisePlanError(
                f"refusing to tear down {deploy_id}: tier={d.tier!r}, not "
                "'exr' -- axrange exr destroy only tears down exercise deploys",
                status=400,
            )

        cfg = load_config(adapter_config)
        if node:
            cfg.default_node = node

        backend = teardown_backend
        if backend is None:
            from axrange.adapter_proxmox import ProxmoxTeardown

            backend = ProxmoxTeardown(cfg)

        lease = store.acquire_lease(d.scenario_id, deploy_id, process_info=default_process_info())
        if not lease.acquired:
            raise ExercisePlanError(
                f"another operation on exercise {d.scenario_id!r} is in "
                f"progress (deploy_id={lease.held_by_deploy_id})",
                status=409,
            )

        heartbeat = Heartbeat(str(db_path), deploy_id=deploy_id, scenario_id=d.scenario_id)
        heartbeat.start()
        try:
            try:
                _stop_exercise_processes(
                    store, deploy_id, cfg=cfg, ga_factory=ga_factory, log=log,
                )
            except Exception as e:  # noqa: BLE001 -- best-effort, see docstring
                log(f"  ! exercise stop pass failed: {e}")

            try:
                result = destroy_deploy(
                    store, backend, deploy_id, node=node, log=log,
                    protected_names=cfg.protected_external_names,
                    protected_vmids=cfg.protected_external_vmids,
                )
            except TeardownError as e:
                raise ExercisePlanError(str(e), status=404) from e
        finally:
            heartbeat.stop()
            try:
                store.release_lease(d.scenario_id, deploy_id)
            except Exception:  # noqa: BLE001
                pass
        return result
    finally:
        store.close()


def cascade_destroy_exercises_for_te(
    *,
    db_path: str,
    te_deploy_id: str,
    adapter_config: Optional[str] = None,
    node: Optional[str] = None,
    teardown_backend: Any = None,
    ga_factory: Any = None,
    log: Callable[[str], None] = lambda _m: None,
) -> list[Any]:
    """A twin destroy cascades EVERY exercise deploy bound to it (§2.1's
    dependency table: "Twin ... takes down its tfc + tap + exr") -- unlike
    `taps`/`traffic` (at most one standing artifact per twin), an `exr`
    twin can have MULTIPLE independent runs still around (ephemeral, never
    auto-reaped -- see module docstring), so this discovers every
    NON-destroyed `exr` deploy whose plan_id starts with `exr-<te_deploy_id>-`
    and destroys each. Best-effort per deploy: one failure is logged and does
    not block the others or the twin's own DestroyResponse. Returns the list
    of `TeardownResult`s actually attempted (empty if none bound)."""
    from axrange.planner import StateStore

    store = StateStore(db_path)
    try:
        candidates = store.deploys_matching(
            tier="exr", plan_id_prefix=f"exr-{te_deploy_id}-",
            exclude_destroyed=True,
        )
        deploy_ids = [d.deploy_id for d in candidates]
    finally:
        store.close()

    results: list[Any] = []
    for did in deploy_ids:
        try:
            results.append(
                destroy_exercise(
                    db_path=db_path, deploy_id=did, adapter_config=adapter_config,
                    node=node, teardown_backend=teardown_backend,
                    ga_factory=ga_factory, log=log,
                )
            )
        except ExercisePlanError as e:
            log(f"  ! exercise cascade for {did}: {e.message}")
    return results


# ---------------------------------------------------------------------------
# Read-only status/info (`axrange exr status`/`info`) -- derived purely from
# `state.db`, mirroring `taps.tap_status`/`traffic.traffic_status`'s posture.
# ---------------------------------------------------------------------------
def latest_exercise_deploy(store: Any, *, twin: Optional[str] = None, exclude_destroyed: bool = False) -> Any:
    rows = store.deploys_matching(
        tier="exr", plan_id_prefix=(f"exr-{twin}-" if twin else None),
        exclude_destroyed=exclude_destroyed,
    )
    return rows[0] if rows else None


def _twin_from_plan_id(plan_id: str) -> str:
    """`exr-<twin>-<8hexToken>` -- strip the fixed `exr-` prefix and the
    trailing `-<token>` this module's own `secrets.token_hex(4)` always
    appends (8 hex chars, `apply_exercise`'s `plan_id` construction)."""
    body = plan_id[len("exr-"):] if plan_id.startswith("exr-") else plan_id
    if len(body) > 9 and body[-9] == "-":
        return body[:-9]
    return body  # pragma: no cover -- defensive; every exr plan_id has the suffix


def exercise_status(
    *, db_path: str, deploy_id: Optional[str] = None, twin: Optional[str] = None,
) -> ExerciseStatus:
    """Read-only: an exercise run's status + playout hash + attack targets,
    derived purely from `state.db`."""
    from axrange.planner.opview import op_fields
    from axrange.planner.state import OpStatus
    from axrange.planner import StateStore

    store = StateStore(db_path)
    try:
        did = deploy_id
        if did is None:
            row = latest_exercise_deploy(store, twin=twin)
            did = row.deploy_id if row is not None else None
        if did is None:
            return ExerciseStatus(deploy_id=None, twin=twin or "", status="not-applied")
        d = store.get_deploy(did)
        if d is None:
            return ExerciseStatus(deploy_id=None, twin=twin or "", status="not-applied")

        activity_host = None
        adversary_targets: list[str] = []
        for op in store.ops_for_deploy(did):
            if op.status != OpStatus.COMPLETED:
                continue
            _op_name, _scope, inp, _out = op_fields(op)
            if op.op_name == _DEPLOY_BEHAVIOR_OP:
                activity_host = inp.get("host_id")
            elif op.op_name == _INSTALL_AGENT_OP and inp.get("agent_kind") == "adversary":
                if inp.get("host_id"):
                    adversary_targets.append(inp["host_id"])

        return ExerciseStatus(
            deploy_id=did, twin=_twin_from_plan_id(d.plan_id), status=d.status.value,
            playout_hash=d.ir_hash or None, activity_host=activity_host,
            adversary_targets=sorted(set(adversary_targets)),
        )
    finally:
        store.close()


def exercise_measurement_window(store: Any, deploy_id: str) -> dict[str, Any]:
    """The Δ-window an operator (or a future automated scorer) can scope a
    Loki truth/detection query by -- this exr deploy's own
    `started_at`/`completed_at` (`docs/design/08-exercise-measurement.md`).
    Purely descriptive; this module never queries Loki itself (see module
    docstring)."""
    d = store.get_deploy(deploy_id)
    if d is None:
        raise ExercisePlanError(f"deploy not found: {deploy_id}", status=404)
    return {
        "deploy_id": deploy_id, "started_at": d.started_at,
        "completed_at": d.completed_at, "playout_hash": d.ir_hash or None,
    }
