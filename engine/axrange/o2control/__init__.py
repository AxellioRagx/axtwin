"""axrange.o2control — the O2 control component (A1, `docs/design/05-o2-surface.md`).

Stands up / inspects / tears down the standing O2 surface (sensors +
transport vnet) by reusing the standalone engine (planner + adapter +
state store) as a spec executor, tagged with the `o2` resource-ownership
tier (`docs/CONVENTIONS.md` §2). An engine *client*, not a second
orchestrator — see `control.py`'s module docstring for the reuse map.

Public surface (consumed by `axrange/cli/main.py` and `axrange/api/`, both
thin transports over this module — `docs/CONVENTIONS.md` code-structure
rule):

    stand_up_surface(...)   -> SurfaceStatus   # axrange o2 up
    surface_status(...)     -> SurfaceStatus   # axrange o2 status
    tear_down_surface(...)  -> TeardownResult  # axrange o2 down

`taps.py` (A2, `docs/design/06-taps-sensors.md`) adds the tap-plan playout —
a separate, independently apply/destroy/redeployable `tap_`-tier lifecycle
that binds a deployed TE's tapped interfaces to A1's standing sensors:

    apply_tap_plan(...)     -> TapPlanStatus       # axrange o2 tap apply
    destroy_taps(...)       -> TeardownResult       # axrange o2 tap destroy

`traffic.py` (the Traffic tier, `docs/WORK_ORDER_traffic_tier.md`) adds a
THIRD playout — a separate, independently apply/status/destroy `tfc`-tier
lifecycle that binds a traffic profile (persona -> twin-host-role
assignments) onto a deployed twin, driven by the persona drivers
(`axrange.personas.drivers`):

    apply_traffic_profile(...)      -> TrafficPlanStatus  # axrange tfc deploy
    destroy_traffic_profile(...)    -> TeardownResult      # axrange tfc destroy
    traffic_status(...)             -> TrafficPlanStatus   # axrange tfc status

`exercise.py` (the Exercise tier, `docs/WORK_ORDER_exercise_tier.md`) adds a
FOURTH, EPHEMERAL playout — an independent `exr`-tier one-shot run (attack
playout: internal `activity` and/or a Sandcat/Caldera adversary plant) onto
a deployed twin, reusing the SAME OP-ACT/OP-OVL-070 ops a `te` deploy used to
emit itself (re-homed, not reinvented — see that module's docstring):

    apply_exercise(...)                   -> ExerciseStatus  # axrange exr deploy
    destroy_exercise(...)                 -> TeardownResult   # axrange exr destroy
    cascade_destroy_exercises_for_te(...) -> list[TeardownResult]  # a te destroy's cascade
    exercise_status(...)                  -> ExerciseStatus  # axrange exr status

`cascade.py` owns the ORDER a twin (`te`) destroy tears those three sibling
playouts down in — `exr` -> `tfc` -> `tap`, all BEFORE the twin's own sweep,
since each binds into the twin's live VMs (LES-171). Both transports call
this one helper rather than re-implementing the sequence (LES-037):

    cascade_destroy_siblings_for_te(...) -> list[SiblingCascade]

Boundary (LES-025): this package imports the engine downward only
(`planner`, `adapter_proxmox`, `ir`, `manifest`, `sensors`, `personas`); the
engine never imports `axrange.o2control`.
"""
from __future__ import annotations

from axrange.o2control.cascade import (
    CASCADE_LABELS,
    CASCADE_ORDER,
    SiblingCascade,
    cascade_destroy_siblings_for_te,
)
from axrange.o2control.compose import compose_surface_ir
from axrange.o2control.control import (
    TIER,
    O2ControlError,
    SensorInventoryEntry,
    SurfaceStatus,
    compose_and_build_plan,
    plan_surface,
    resolve_surface_manifest,
    stand_up_surface,
    surface_status,
    tear_down_surface,
    validate_surface,
)
from axrange.o2control.manifest import (
    SurfaceManifest,
    SurfaceManifestError,
    SurfaceSensorSpec,
    default_surface_manifest,
    load_surface_manifest,
    parse_surface_manifest,
)
from axrange.o2control.taps import (
    TapBindingInfo,
    TapBindingResult,
    TapBindingSpec,
    TapInfo,
    TapPlanError,
    TapPlanResolution,
    TapPlanSpec,
    TapPlanStatus,
    apply_tap_plan,
    binding_label,
    cascade_destroy_for_te,
    compose_tap_ops,
    destroy_taps,
    is_tap_plan_deploy,
    latest_tap_plan_deploy,
    load_tap_plan,
    merge_external_sensors,
    parse_tap_plan,
    resolve_tap_plan,
    resolve_tap_plan_bindings,
    tap_info_from_deploy,
    tap_info_from_resolution,
    tap_status,
)
from axrange.o2control.traffic import (
    AssignmentInfo,
    AssignmentResult,
    AssignmentSpec,
    PersonaBackend,
    ReplayBindingSpec,
    ResolvedAssignment,
    TrafficInfo,
    TrafficPlanError,
    TrafficPlanResolution,
    TrafficPlanStatus,
    TrafficProfileSpec,
    apply_traffic_profile,
    assignment_label,
    cascade_destroy_tfc_for_te,
    compose_traffic_ops,
    destroy_traffic_profile,
    latest_traffic_deploy,
    load_traffic_profile,
    parse_traffic_profile,
    resolve_traffic_bindings,
    resolve_traffic_profile,
    traffic_info_from_deploy,
    traffic_info_from_resolution,
    traffic_status,
)
from axrange.o2control.exercise import (
    ActivityAttackSpec,
    AdversaryAttackSpec,
    ExercisePlanError,
    ExerciseResolution,
    ExerciseSpec,
    ExerciseStatus,
    apply_exercise,
    cascade_destroy_exercises_for_te,
    compose_exercise_ops,
    destroy_exercise,
    exercise_measurement_window,
    exercise_status,
    latest_exercise_deploy,
    load_exercise_spec,
    parse_exercise_spec,
    playout_hash,
    resolve_exercise_bindings,
    resolve_exercise_spec,
)

__all__ = [
    "TIER",
    "CASCADE_LABELS",
    "CASCADE_ORDER",
    "SiblingCascade",
    "cascade_destroy_siblings_for_te",
    "O2ControlError",
    "SensorInventoryEntry",
    "SurfaceStatus",
    "SurfaceManifest",
    "SurfaceManifestError",
    "SurfaceSensorSpec",
    "compose_and_build_plan",
    "compose_surface_ir",
    "default_surface_manifest",
    "load_surface_manifest",
    "parse_surface_manifest",
    "plan_surface",
    "resolve_surface_manifest",
    "stand_up_surface",
    "surface_status",
    "tear_down_surface",
    "validate_surface",
    "TapBindingInfo",
    "TapBindingResult",
    "TapBindingSpec",
    "TapInfo",
    "TapPlanError",
    "TapPlanResolution",
    "TapPlanSpec",
    "TapPlanStatus",
    "apply_tap_plan",
    "binding_label",
    "cascade_destroy_for_te",
    "compose_tap_ops",
    "destroy_taps",
    "is_tap_plan_deploy",
    "latest_tap_plan_deploy",
    "load_tap_plan",
    "merge_external_sensors",
    "parse_tap_plan",
    "resolve_tap_plan",
    "resolve_tap_plan_bindings",
    "tap_info_from_deploy",
    "tap_info_from_resolution",
    "tap_status",
    "AssignmentInfo",
    "AssignmentResult",
    "AssignmentSpec",
    "PersonaBackend",
    "ReplayBindingSpec",
    "ResolvedAssignment",
    "TrafficInfo",
    "TrafficPlanError",
    "TrafficPlanResolution",
    "TrafficPlanStatus",
    "TrafficProfileSpec",
    "apply_traffic_profile",
    "assignment_label",
    "cascade_destroy_tfc_for_te",
    "compose_traffic_ops",
    "destroy_traffic_profile",
    "latest_traffic_deploy",
    "load_traffic_profile",
    "parse_traffic_profile",
    "resolve_traffic_bindings",
    "resolve_traffic_profile",
    "traffic_info_from_deploy",
    "traffic_info_from_resolution",
    "traffic_status",
    "ActivityAttackSpec",
    "AdversaryAttackSpec",
    "ExercisePlanError",
    "ExerciseResolution",
    "ExerciseSpec",
    "ExerciseStatus",
    "apply_exercise",
    "cascade_destroy_exercises_for_te",
    "compose_exercise_ops",
    "destroy_exercise",
    "exercise_measurement_window",
    "exercise_status",
    "latest_exercise_deploy",
    "load_exercise_spec",
    "parse_exercise_spec",
    "playout_hash",
    "resolve_exercise_bindings",
    "resolve_exercise_spec",
]
