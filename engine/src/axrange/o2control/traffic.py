"""axrange.o2control.traffic -- the Traffic-tier playout (`docs/
WORK_ORDER_traffic_tier.md`, `docs/design/02-twin-model.md`).

Binds a **traffic profile** (persona -> twin-host-role assignments) onto an
ALREADY-DEPLOYED twin (`te`-tier deploy) and drives each assignment's persona
via the persona drivers (`axrange.personas.drivers`), exactly the way A2's
tap-plan playout (`axrange.o2control.taps`) binds a tap-plan onto a deployed
twin's tapped interfaces. Its own `tfc`-tier lifecycle: independently
apply/status/destroy, cascaded by a `te` destroy.

Why this module exists as a THIRD standalone playout, modeled closely on
`taps.py` (LES-037 -- reuse the shared build/execute/release machinery,
don't reimplement it):

  * **Binds to something ALREADY DEPLOYED**, like a tap-plan -- there is
    nothing to EMIT from an IR walk (`pass1_emit`/`build_plan` never see
    this). So this module composes a `Plan` BY HAND out of `OpInstance`s,
    reusing `axrange.ops.registry` for the one new op's catalog metadata
    (`cleanup_strategy`/`idempotency_class`/`resource_categories`/`timeout`),
    exactly as `taps.py`'s `_make_mir_op` does for the three OP-MIR ops it
    composes.
  * **Runs through the SAME engine executor** (`axrange.api.service.
    run_deploy_executor` / `release_deploy_resources`) every other deploy
    lifecycle uses -- no second "build plan, run executor, release
    resources" implementation. Teardown reuses `planner.teardown.
    destroy_deploy` verbatim, same as `taps.py`/`control.py`.
  * **Uses `tier="tfc"`** (unlike `taps.py`, which is forced to reuse
    `tier="te"` because `naming._VALID_TIERS` has no separate `"tap"` deploy
    tier -- see that module's docstring). `tfc` IS a real, first-class
    deploy tier already: `docs/WORK_ORDER_tier_split.md` added it to
    `_VALID_TIERS`/`TIER_SHAPE_CHECKS`/the name-shape producers/the six-tier
    teardown protect matrix, all unit/suite-verified. So this module's
    destroy discovery is simpler than A2's -- no `plan_id`-prefix +
    op-name-subset structural discriminator is needed; `deploys.tier ==
    "tfc"` alone is an honest, unambiguous signal (`naming.TIER_SHAPE_CHECKS`
    already refuses every foreign-tier name shape a `tfc` deploy's own ops
    could ever produce).

New op catalog entry (additive, non-version-locked-schema-breaking, the same
posture every other slice's OP_CATALOG growth has used -- OP-MIR-005,
OP-HOST-100, OP-OVL-070, ...): **`OP-TFC-010 drive-persona`** -- config-class
(QGA channel), `cleanup_strategy=REDO`. Its adapter-side implementation is
NOT `axrange.config_exec.ConfigBackend` (that would create a REAL import
cycle: `axrange.personas.drivers.*` already imports `axrange.config_exec.
render` for the shared `systemd_daemon_start_block` primitive, so
`config_exec` importing `axrange.personas` back would be circular). Instead
this module defines its OWN small `Adapter`-protocol-shaped backend,
`PersonaBackend` (below), that lives here in `o2control` -- which already
legitimately imports BOTH the engine (`planner`, `adapter_proxmox`) AND
`axrange.personas` (engine vocabulary, consumed downward, `docs/
design/02-twin-model.md`) -- so there is no cycle. `run_deploy_executor`
takes `config_backend` as a plain duck-typed `Adapter` (`.run(op, inputs,
ctx)`), so handing it a `PersonaBackend` instead of the real
`ConfigBackend` is exactly the seam it was built for.

STABLE op_id per persona instance (`{"instance": instance_id}` scope, no
content hash), force-reset to `pending` on every apply -- the SAME reasoning
`taps.py`'s `install-ovs-mirror` uses (LES-057): the driver's `start()` is
idempotent-CONVERGENT (re-render + re-launch via `systemd_daemon_start_block`,
which itself `stop`s the old unit first), not "run once and never touch
again", so a content-hashed op_id would give dedup instead of the re-assertion
a changed profile (a persona swapped, a peer re-resolved, a seed changed)
actually needs.

Boundaries (LES-025): this module imports the engine (`planner`,
`adapter_proxmox`, `ops.registry`) and `axrange.personas` -- both DOWNWARD --
plus its sibling `axrange.o2control.taps` is NOT imported (no dependency
between the two playouts). The engine never imports `axrange.o2control`.
No sensors, no taps: this module never touches OP-MIR/OP-NET/OP-HOST ops or
the standing O2 sensor inventory -- only the target twin's published
`hosts[]` roster (`axrange.planner.inventory.build_twin_inventory`) and the
persona catalog/drivers.
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Optional

from axrange.ops.registry import get_op
from axrange.personas import registry as persona_registry
from axrange.personas.drivers import PersonaDriverError, get_driver
from axrange.personas.schema import RESERVED_PEER_ROLES, Persona
from axrange.planner.dag import OpInstance, Plan, _op_id
from axrange.planner.inventory import HostInfo, InventoryError, TwinInventory
from axrange.planner.state import OpStatus

# The one new OP_CATALOG entry this module drives (see module docstring).
_DRIVE_PERSONA_OP = "drive-persona"
_ASSIGN_ROLE_OP = "assign-role"

# Driver-specific extras threaded through an op's `inputs_resolved` and
# fanned back out as `**kwargs` to `PersonaDriver.start()` -- present only
# for the drive kinds that need them (replay/real-service); absent (never a
# bare `None`-valued key) for scripted, which needs none of these.
_DRIVER_EXTRA_KEYS = (
    "template_pcap_path", "orig_ip", "node_ip", "iface",
    "service_name", "process_match",
)


class TrafficPlanError(Exception):
    """A traffic-profile playout failure with an HTTP-ish status hint -- the
    tfc-tier counterpart to `axrange.o2control.taps.TapPlanError` (same
    shape, an independent class since this is its own tier/playout, not an
    extension of A2)."""

    def __init__(self, message: str, *, status: int = 400, detail: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        # Structured companion to `message` for an API caller (the per-host
        # apply's `refusals`, the host-edit revert list). `None` everywhere
        # else, which is what every pre-existing raise site produces.
        self.detail = detail


# ---------------------------------------------------------------------------
# Traffic-profile spec (`docs/WORK_ORDER_traffic_tier.md` SS2,
# `docs/design/02-twin-model.md`) -- a named doc, loaded like a tap-plan:
#
#   traffic-profile:
#     twin: <te-deploy-id>
#     assignments:
#       - role: corp-workstation        # OR node: ws01
#         persona: corp-workstation
#         intensity: 1.0                # optional, informational (persona.knobs override -- follow-on)
#         schedule: 24x7                # optional -- overrides the persona's own cadence-derived
#                                        # emit gate (2026-07-25, docs/WORK_ORDER_traffic_tier.md);
#                                        # "24x7"/"24/7"/"always" forces every emit ALWAYS-on,
#                                        # "business-hours"/"business_hours" forces the business-hours
#                                        # gate; unset (the default) leaves the persona's own
#                                        # cadence-derived gate untouched (see _normalize_schedule)
#         seed: 42                      # optional
#     replay:
#       - template: /path/to/historian.pcap
#         onto_role: historian
#         orig_ip: 10.10.0.50           # required by the replay driver
#         iface: eth0                   # optional, default eth0
#
# Exactly one of `role` / `node` is required per assignment (parse-time
# validated, same posture as `taps.py`'s exactly-one-addressing-mode check).
# ---------------------------------------------------------------------------
@dataclass
class AssignmentSpec:
    role: Optional[str] = None
    node: Optional[str] = None
    persona: str = ""
    intensity: Optional[float] = None
    # Already NORMALIZED by `parse_traffic_profile` (via `_normalize_schedule`)
    # to one of `"24x7"` / `"business-hours"` / `None` -- an on-demand emit-
    # gate override for a test/demo run, wired through to the scripted
    # driver's render (2026-07-25, docs/WORK_ORDER_traffic_tier.md). `None`
    # (unset) leaves the persona's own cadence-derived gate untouched. A
    # directly-constructed `AssignmentSpec` (tests, or a future non-parse
    # caller) is expected to already carry a normalized value or `None` --
    # only `parse_traffic_profile`'s YAML-loading path fail-loud-validates
    # the raw string.
    schedule: Optional[str] = None
    seed: Any = None


@dataclass
class RoleAssignmentSpec:
    """One `roles[]` entry -- the tfc-owned host->role map
    (`docs/WORK_ORDER_role_persona_tier.md` §4.4).

    Addressed by EXACTLY ONE of `node:` (a literal twin host id) or
    `selector:` (matched against the twin's published host facts). Addressing
    by `role:` is deliberately NOT offered: this block is what DEFINES a
    host's role, so selecting hosts by role here would be circular -- and
    silently self-referential in a way that depends on evaluation order.
    """

    node: Optional[str] = None
    selector: Optional[dict[str, Any]] = None
    role: str = ""


@dataclass
class ReplayBindingSpec:
    template: str = ""
    onto_role: str = ""
    orig_ip: Optional[str] = None
    iface: str = "eth0"


@dataclass
class TrafficProfileSpec:
    # OPTIONAL and named `target` since 2026-08-04
    # (`docs/WORK_ORDER_role_persona_tier.md` §4.3) -- exact parity with
    # `taps.TapPlanSpec.target`, which got the same treatment 2026-07-25
    # (`docs/WORK_ORDER_tap_target_resolution.md`). It accepts EITHER an
    # already-minted `te` deploy id OR a scenario/twin-config NAME (resolved
    # to that scenario's newest live `te` deploy at apply time), and may be
    # omitted entirely in favour of a `--target`/API-body override -- which
    # is what makes a traffic profile a checked-in, reusable library file
    # instead of one welded to a single minted deploy id (LES-072).
    # The legacy `twin:` key is still accepted as an alias by
    # `parse_traffic_profile`.
    target: Optional[str] = None
    # Applied BEFORE `assignments` resolve, so an `assignments[].role` may
    # reference a role this very profile assigns (§4.4 sequencing).
    roles: list[RoleAssignmentSpec] = field(default_factory=list)
    assignments: list[AssignmentSpec] = field(default_factory=list)
    replay: list[ReplayBindingSpec] = field(default_factory=list)


def role_entry_label(r: "RoleAssignmentSpec") -> str:
    """Human-readable stand-in for a `roles[]` entry's addressing target --
    the `roles:` counterpart to `assignment_label` (LES-037: one helper per
    vocabulary, reused by CLI rendering, never re-derived per call site)."""
    if r.node is not None:
        return f"node:{r.node}"
    return f"selector:{r.selector}"


def assignment_label(a: AssignmentSpec) -> str:
    """Human-readable string standing in for an assignment's addressing
    target -- mirrors `taps.py.binding_label`'s role (LES-037: one helper,
    reused by CLI rendering, not re-derived per call site)."""
    if a.node is not None:
        return f"node:{a.node}"
    return f"role:{a.role}"


# Normalized-key -> canonical token. Keys are matched AFTER lower-casing +
# collapsing underscores to hyphens (`_normalize_schedule`), so this table
# only needs to enumerate the hyphenated/slashed spellings.
_SCHEDULE_ALIASES = {
    "24x7": "24x7",
    "24/7": "24x7",
    "always": "24x7",
    "business-hours": "business-hours",
}


def _normalize_schedule(raw: Any, *, context: str) -> Optional[str]:
    """Normalize an assignment's `schedule:` override to one of `"24x7"` /
    `"business-hours"` / `None` -- the ONE place this spelling-normalization
    happens (2026-07-25, docs/WORK_ORDER_traffic_tier.md). Accepts case-
    insensitive `24x7`/`24/7`/`always` and `business-hours`/`business_hours`;
    `None`/unset passes through unchanged (falls through to the persona's own
    cadence-derived gate, byte-identical to pre-2026-07-25 behavior -- the
    critical backward-compat case).

    Fail-loud (LES-001) on anything else: an unrecognized value is almost
    certainly a typo (`bussiness-hours`, `always-on`, ...), and silently
    ignoring it would produce a green "applied" traffic profile that quietly
    still gates on cadence -- exactly the kind of surprise this override
    exists to prevent. Caught here at PARSE time (`parse_traffic_profile`),
    before any deploy, rather than deferred to resolve/apply time."""
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise TrafficPlanError(
            f"{context}: 'schedule' must be a string, got {raw!r}", status=400,
        )
    key = raw.strip().lower().replace("_", "-")
    normalized = _SCHEDULE_ALIASES.get(key)
    if normalized is None:
        raise TrafficPlanError(
            f"{context}: unknown schedule override {raw!r} -- expected "
            "'24x7' (or '24/7'/'always') or 'business-hours' (or "
            "'business_hours'), or omit 'schedule' entirely for the "
            "persona's own cadence-derived gate",
            status=400,
        )
    return normalized


def parse_traffic_profile(raw: dict[str, Any]) -> TrafficProfileSpec:
    """Sanity-check an already-loaded traffic-profile dict."""
    if not isinstance(raw, dict):
        raise TrafficPlanError("traffic-profile file root must be a mapping", status=400)
    body = raw.get("traffic-profile")
    if body is None:
        raise TrafficPlanError(
            "traffic-profile file must have a top-level 'traffic-profile:' key",
            status=400,
        )
    if not isinstance(body, dict):
        raise TrafficPlanError("'traffic-profile:' must be a mapping", status=400)

    # `target:` is the canonical key; `twin:` is the pre-2026-08-04 spelling,
    # still accepted so every existing profile keeps parsing. Both are now
    # OPTIONAL -- a profile with neither is valid and relies on a
    # `--target`/API-body override at apply time. A present-but-malformed
    # value is still a parse error, never silently coerced to "absent"
    # (same posture as `taps.parse_tap_plan`).
    if "target" in body and "twin" in body:
        raise TrafficPlanError(
            "traffic-profile declares both 'target:' and its legacy alias "
            "'twin:' -- keep one (prefer 'target:')",
            status=400,
        )
    target = body.get("target", body.get("twin"))
    if target is not None and (not isinstance(target, str) or not target):
        raise TrafficPlanError(
            "traffic-profile.target, if given, must be a non-empty string "
            "(a te deploy id, or a scenario/twin-config name)",
            status=400,
        )

    roles_raw = body.get("roles") or []
    if not isinstance(roles_raw, list):
        raise TrafficPlanError("traffic-profile.roles must be a list", status=400)
    # `roles_from:` -- share ONE role map across several profiles of the same
    # twin (`docs/WORK_ORDER_role_persona_tier.md` §3.1). Without it, every
    # profile that varies only its traffic VOLUME would have to repeat the
    # whole host->role map, which is the single real argument for splitting
    # roles into their own tier; this closes it for a fraction of that cost.
    #
    # Deliberately NOT the IR's `scenario.includes:` machinery: that lives in
    # `parse_ir` with an IR-shaped mergeable-paths table (`layers.infra.nodes`
    # &c.) and never ran for a profile at all -- `load_traffic_profile` calls
    # `load_yaml`, which loads exactly one file. This is a scoped, one-key
    # loader for the one block that needs sharing, resolved by
    # `load_traffic_profile` (which knows the referring file's directory);
    # `parse_traffic_profile` alone cannot resolve a relative path and says so.
    if body.get("roles_from") is not None and roles_raw:
        raise TrafficPlanError(
            "traffic-profile declares both 'roles:' and 'roles_from:' -- keep "
            "one (inline map, or a shared file)",
            status=400,
        )

    assignments_raw = body.get("assignments") or []
    if not isinstance(assignments_raw, list):
        raise TrafficPlanError("traffic-profile.assignments must be a list", status=400)
    replay_raw = body.get("replay") or []
    if not isinstance(replay_raw, list):
        raise TrafficPlanError("traffic-profile.replay must be a list", status=400)
    if not roles_raw and not assignments_raw and not replay_raw:
        raise TrafficPlanError(
            "traffic-profile must declare at least one roles[], assignments[] "
            "or replay[] entry",
            status=400,
        )

    _ROLE_SELECTOR_KEYS = {"net", "kind"}
    roles: list[RoleAssignmentSpec] = []
    for i, r in enumerate(roles_raw):
        if not isinstance(r, dict):
            raise TrafficPlanError(f"roles[{i}] must be a mapping", status=400)
        node, selector = r.get("node"), r.get("selector")
        if sum(x is not None for x in (node, selector)) != 1:
            raise TrafficPlanError(
                f"roles[{i}]: exactly one of 'node' / 'selector' is required "
                "(addressing by 'role' is not offered here -- this block is "
                "what defines roles)",
                status=400,
            )
        if selector is not None:
            if not isinstance(selector, dict) or not selector:
                raise TrafficPlanError(
                    f"roles[{i}]: 'selector' must be a non-empty mapping", status=400,
                )
            unknown = sorted(set(selector) - _ROLE_SELECTOR_KEYS)
            if unknown:
                raise TrafficPlanError(
                    f"roles[{i}]: unknown selector key(s) {unknown} -- "
                    f"expected any of {sorted(_ROLE_SELECTOR_KEYS)}",
                    status=400,
                )
        role = r.get("role")
        if not role or not isinstance(role, str):
            raise TrafficPlanError(
                f"roles[{i}]: 'role' (the semantic role to assign) is required",
                status=400,
            )
        roles.append(RoleAssignmentSpec(node=node, selector=selector, role=role))

    assignments: list[AssignmentSpec] = []
    for i, a in enumerate(assignments_raw):
        if not isinstance(a, dict):
            raise TrafficPlanError(f"assignments[{i}] must be a mapping", status=400)
        role, node = a.get("role"), a.get("node")
        if sum(x is not None for x in (role, node)) != 1:
            raise TrafficPlanError(
                f"assignments[{i}]: exactly one of 'role' / 'node' is required",
                status=400,
            )
        persona = a.get("persona")
        if not persona or not isinstance(persona, str):
            raise TrafficPlanError(
                f"assignments[{i}]: 'persona' (a persona catalog id) is required",
                status=400,
            )
        intensity = a.get("intensity")
        if intensity is not None:
            try:
                intensity = float(intensity)
            except (TypeError, ValueError) as e:
                raise TrafficPlanError(
                    f"assignments[{i}]: 'intensity' must be a number, got {intensity!r}",
                    status=400,
                ) from e
        schedule = _normalize_schedule(a.get("schedule"), context=f"assignments[{i}]")
        assignments.append(
            AssignmentSpec(
                role=role, node=node, persona=persona, intensity=intensity,
                schedule=schedule, seed=a.get("seed"),
            )
        )

    replay: list[ReplayBindingSpec] = []
    for i, r in enumerate(replay_raw):
        if not isinstance(r, dict):
            raise TrafficPlanError(f"replay[{i}] must be a mapping", status=400)
        template = r.get("template")
        onto_role = r.get("onto_role")
        if not template or not isinstance(template, str):
            raise TrafficPlanError(
                f"replay[{i}]: 'template' (a host-side pcap path) is required",
                status=400,
            )
        if not onto_role or not isinstance(onto_role, str):
            raise TrafficPlanError(
                f"replay[{i}]: 'onto_role' (a role present in the twin) is required",
                status=400,
            )
        orig_ip = r.get("orig_ip")
        if orig_ip is not None and not isinstance(orig_ip, str):
            raise TrafficPlanError(f"replay[{i}]: 'orig_ip' must be a string", status=400)
        iface = r.get("iface") or "eth0"
        if not isinstance(iface, str):
            raise TrafficPlanError(f"replay[{i}]: 'iface' must be a string", status=400)
        replay.append(
            ReplayBindingSpec(
                template=template, onto_role=onto_role, orig_ip=orig_ip, iface=iface,
            )
        )

    return TrafficProfileSpec(
        target=target, roles=roles, assignments=assignments, replay=replay,
    )


def load_traffic_profile(path: str | Path) -> TrafficProfileSpec:
    """Load + sanity-check a traffic-profile YAML file.

    Resolves `roles_from:` (a path RELATIVE to this file) before parsing --
    the one piece of profile loading that needs to know where the referring
    file lives, which is why it happens here and not in the pure
    `parse_traffic_profile`. One level only, deliberately: a shared role map
    that itself chains to another is a config-graph, not a shared constant.
    """
    from axrange.ir.parser import IncludeError, StructuralError, load_yaml

    p = Path(path)
    try:
        raw = load_yaml(p)
    except (IncludeError, StructuralError) as e:
        raise TrafficPlanError(f"{p}: {e}", status=400) from e
    if raw is None:
        raw = {}

    body = raw.get("traffic-profile") if isinstance(raw, dict) else None
    roles_from = body.get("roles_from") if isinstance(body, dict) else None
    if roles_from is not None:
        # Checked HERE, not only in `parse_traffic_profile`: this function
        # resolves `roles_from:` into `roles:` and strips the key, so by the
        # time parse runs it can never see both. (parse keeps its own copy of
        # the check for the API path, which hands it a raw dict directly.)
        if body.get("roles"):
            raise TrafficPlanError(
                f"{p}: traffic-profile declares both 'roles:' and "
                "'roles_from:' -- keep one (inline map, or a shared file)",
                status=400,
            )
        if not isinstance(roles_from, str) or not roles_from:
            raise TrafficPlanError(
                f"{p}: traffic-profile.roles_from must be a non-empty string "
                "(a path to a YAML file, relative to this profile)",
                status=400,
            )
        shared_path = (p.parent / roles_from).resolve()
        try:
            shared = load_yaml(shared_path)
        except (IncludeError, StructuralError) as e:
            raise TrafficPlanError(
                f"{p}: roles_from {roles_from!r}: {e}", status=400,
            ) from e
        shared_body = (shared or {}).get("traffic-profile") if isinstance(shared, dict) else None
        shared_roles = (shared_body or {}).get("roles") if isinstance(shared_body, dict) else None
        if shared_roles is None:
            raise TrafficPlanError(
                f"{p}: roles_from {roles_from!r} has no 'traffic-profile.roles:' "
                "block -- a shared role map is a traffic-profile file carrying "
                "only that block",
                status=400,
            )
        if (shared_body or {}).get("roles_from") is not None:
            raise TrafficPlanError(
                f"{p}: roles_from {roles_from!r} itself declares roles_from: -- "
                "shared role maps do not chain (one level only)",
                status=400,
            )
        body = dict(body or {})
        body.pop("roles_from", None)
        body["roles"] = shared_roles
        raw = dict(raw)
        raw["traffic-profile"] = body

    return parse_traffic_profile(raw)


def effective_roles_for_twin(store: Any, twin_deploy_id: str) -> dict[str, str]:
    """host_id -> tfc-assigned role for a twin, read back from the standing
    `tfc` deploy's persisted `assign-role` ops
    (`docs/WORK_ORDER_role_persona_tier.md` §4.2).

    This is the `o2control`-side half of the inventory's role precedence:
    `axrange.planner.inventory.build_twin_inventory` takes the map as a
    parameter and never looks it up itself, because the engine must not import
    `o2control` (LES-025). Knowing that a twin's roles live in a deploy named
    `tfc-<twin>` is tier knowledge, and tier knowledge belongs here.

    Filters POSITIVELY to `status == COMPLETED` (LES-113): a pending, failed
    or de-scoped (`destroyed`) row must not pin a role. Returns `{}` for a twin
    with no tfc deploy at all -- the overwhelmingly common case, and exactly
    the pre-2026-08-04 behavior."""
    from axrange.planner.opview import op_fields

    row = store.find_deploy_for_plan(f"tfc-{twin_deploy_id}", exclude_destroyed=True)
    if row is None:
        return {}
    out: dict[str, str] = {}
    for op in store.ops_for_deploy(row.deploy_id):
        if op.op_name != _ASSIGN_ROLE_OP or op.status != OpStatus.COMPLETED:
            continue
        _op_name, scope, inp, _out = op_fields(op)
        host_id = scope.get("host") or inp.get("host_id")
        role = inp.get("role")
        if host_id and role:
            out[str(host_id)] = str(role)
    return out


def resolve_traffic_target(store: Any, target: str) -> str:
    """Resolve a traffic-profile target to a CONCRETE `te` deploy id.

    Delegates to `axrange.o2control.taps.resolve_target_deploy` rather than
    reimplementing the lookup (LES-037): "an already-minted deploy id passes
    through; otherwise the newest live non-tap-playout `te` deploy of the
    scenario by that name" is one rule, and two copies of it would drift the
    moment either tier's notion of "live" changed. Only the raised error type
    is adapted -- a `tfc` caller must not surface a `TapPlanError`.
    """
    from axrange.o2control.taps import TapPlanError, resolve_target_deploy

    try:
        return resolve_target_deploy(store, target)
    except TapPlanError as e:
        raise TrafficPlanError(
            f"traffic-profile target {target!r} is neither a deploy id nor a "
            "live te deploy of a scenario by that name",
            status=e.status,
        ) from e


def resolve_traffic_profile(
    spec: Optional[TrafficProfileSpec], spec_path: Optional[str | Path]
) -> TrafficProfileSpec:
    """One spec wins, in order: an already-parsed `spec`, or a `spec_path` to
    load. Mirrors `taps.resolve_tap_plan`'s shape."""
    if spec is not None:
        return spec
    if spec_path is not None:
        return load_traffic_profile(spec_path)
    raise TrafficPlanError("apply_traffic_profile needs spec= or spec_path=", status=400)


# ---------------------------------------------------------------------------
# Result shapes
# ---------------------------------------------------------------------------
@dataclass
class AssignmentResult:
    target: str          # assignment_label(a) -- "role:X" or "node:X"
    persona: str
    hosts: list[str] = field(default_factory=list)  # resolved host_id(s)


@dataclass
class TrafficPlanStatus:
    deploy_id: Optional[str]
    twin: str
    # "not-applied" | a DeployStatus value ("running"/"completed"/"failed"/...)
    status: str
    assignments: list[AssignmentResult] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Resolution against the twin's published inventory -- NEVER twin internals
# (`docs/design/02-twin-model.md`): everything here is derived from
# `axrange.planner.inventory.build_twin_inventory`'s `TwinInventory.hosts[]`.
# ---------------------------------------------------------------------------
@dataclass
class ResolvedAssignment:
    instance_id: str
    host_id: str
    vmid: int
    persona: Persona
    peers: dict[str, str] = field(default_factory=dict)
    # `peer_scope: all` fan-out sets (2026-07-28) -- role -> every twin host
    # IP carrying it, minus this instance's own host. Empty for every
    # persona that uses the default `peer_scope: one`, which is why the
    # `peers` map above is unchanged and every pre-2026-07-28 binding
    # resolves identically. See `_resolve_peer_sets`.
    peer_sets: dict[str, list[str]] = field(default_factory=dict)
    seed: Any = None
    # Already-normalized "24x7" / "business-hours" / None -- see
    # `AssignmentSpec.schedule` / `_normalize_schedule`.
    schedule: Optional[str] = None
    extra: dict[str, Any] = field(default_factory=dict)
    # The spec entry this resolved from, purely for status/label rendering.
    source: str = ""


@dataclass
class ResolvedRole:
    """One resolved host->role assignment, ready for an `assign-role` op."""

    host_id: str
    role: str
    source: str = ""  # the spec entry's label, for status/warning rendering


@dataclass
class TrafficPlanResolution:
    target_status: str  # the target twin deploy's own DeployStatus value
    twin: TwinInventory
    roles: list[ResolvedRole] = field(default_factory=list)
    resolved: list[ResolvedAssignment] = field(default_factory=list)
    assignment_results: list[AssignmentResult] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _hosts_by_role(
    twin: TwinInventory, *, overrides: Optional[dict[str, str]] = None,
) -> dict[str, list[HostInfo]]:
    """host-role index. `overrides` (host_id -> role) is this profile's own
    `roles:` block: a tfc-assigned role WINS over the twin's published
    baseline for that host, which is the whole point of making role
    tfc-owned (`docs/WORK_ORDER_role_persona_tier.md`). A host the overrides
    don't mention keeps its baseline role."""
    overrides = overrides or {}
    out: dict[str, list[HostInfo]] = {}
    for h in twin.hosts:
        role = overrides.get(h.host_id, h.role)
        if role:
            out.setdefault(role, []).append(h)
    for hosts in out.values():
        hosts.sort(key=lambda h: h.host_id)
    return out


def _host_matches_role_selector(h: HostInfo, selector: dict[str, Any]) -> bool:
    """`{net, kind}` matched against a published host. Both are real
    inventory facts (`HostInfo.nets[]`/`HostInfo.kind`); the parse-time key
    allowlist keeps this total, so an unmatched key can never silently mean
    "matches everything"."""
    net = selector.get("net")
    if net is not None and not any(n.net_id == net for n in h.nets):
        return False
    kind = selector.get("kind")
    if kind is not None and (h.kind or "host") != kind:
        return False
    return True


def _resolve_roles(
    spec: TrafficProfileSpec, twin: TwinInventory, host_by_id: dict[str, HostInfo],
) -> tuple[list[ResolvedRole], list[str]]:
    """Resolve `roles[]` into concrete host->role assignments.

    Fail-loud on an unresolvable `node:` (a typo'd host id must not silently
    assign nothing, LES-001); a `selector:` that matches no host is a WARNING,
    since a selector legitimately describes a shape a given twin may not have
    (the same posture `assignments[].role` takes for an absent role).

    A host named twice by the same profile is an error: two roles on one host
    is not a merge, it is an authoring mistake, and picking either silently
    would make the result depend on entry order."""
    resolved: list[ResolvedRole] = []
    warnings: list[str] = []
    claimed: dict[str, str] = {}  # host_id -> the entry label that claimed it

    for r in spec.roles:
        label = role_entry_label(r)
        if r.node is not None:
            host = host_by_id.get(r.node)
            if host is None:
                raise TrafficPlanError(
                    f"roles entry {label!r}: node {r.node!r} is not a host in "
                    f"target twin {spec.target!r}",
                    status=400,
                )
            matched = [host]
        else:
            matched = [
                h for h in sorted(twin.hosts, key=lambda h: h.host_id)
                if _host_matches_role_selector(h, r.selector or {})
            ]
            if not matched:
                warnings.append(
                    f"roles entry {label} matched no host in target twin "
                    f"{spec.target!r}; no role assigned"
                )
                continue

        for h in matched:
            prior = claimed.get(h.host_id)
            if prior is not None:
                raise TrafficPlanError(
                    f"host {h.host_id!r} is assigned a role twice in this "
                    f"profile ({prior} and {label}) -- a host has exactly one "
                    "role; drop or narrow one of the entries",
                    status=400,
                )
            claimed[h.host_id] = label
            resolved.append(ResolvedRole(host_id=h.host_id, role=r.role, source=label))

    return resolved, warnings


def _host_ip(h: HostInfo) -> Optional[str]:
    # M2 audit (`docs/WORK_ORDER_tap_scopes.md`): a router's `HostNet(
    # external=True)` WAN leg is now RECORDED in `h.nets` -- but its `ip` is
    # always `None` (pass1_emit never resolves the DHCP-assigned WAN address
    # at plan time), so the `if net.ip:` guard already excludes it here with
    # no code change needed; documented rather than silently relying on it.
    for net in h.nets:
        if net.ip:
            return net.ip
    return None


def _require_vmid(h: HostInfo) -> int:
    if h.vmid is None:
        raise TrafficPlanError(
            f"host {h.host_id!r} has no recorded vmid (the twin deploy may be "
            "incomplete -- a persona can't be driven onto a host that was "
            "never actually cloned)",
            status=400,
        )
    return h.vmid


def _resolve_peers(
    persona: Persona, hosts_by_role: dict[str, list[HostInfo]], *, index: int = 0,
    warnings: Optional[list[str]] = None, exclude_host_id: str = "",
) -> dict[str, str]:
    """Resolve every non-reserved `emits[].peer_role` to a concrete peer IP
    by finding the twin host(s) with that role.

    **Fan-out for hub roles** (twin fidelity -- should-fix, code review):
    when more than one host shares a peer_role (e.g. two domain
    controllers), the candidates are `host_id`-sorted (deterministic
    ordering, `_hosts_by_role`) and picked **round-robin by `index`** --
    `candidates[index % len(candidates)]` -- not always the same one. `index`
    is this persona INSTANCE's position among its assignment's resolved
    hosts (`resolve_traffic_bindings`'s enumerate loop), so e.g. 25
    `corp-workstation`s spread their DC traffic across however many DCs the
    twin has, round-robin, instead of every instance hammering a single
    deterministic-first one. Deterministic either way: the SAME (persona,
    host ordering, index) always resolves to the SAME peer, so a replay is
    identical.

    Reserved tokens (`egress`/`internet`) need no resolution -- they
    describe traffic leaving the twin's own role vocabulary entirely, so no
    peer IP is threaded for them; the guest client (`personas.drivers.
    scripted.render_scripted_client`) already logs-and-skips an emit with no
    resolved peer, gracefully.

    Fail-loud (LES-098/LES-001): a peer_role that ISN'T reserved and HARD
    (not `optional:`) and has NO matching host in the twin is a real
    authoring error -- silently skipping it would produce a green "applied"
    traffic profile whose persona never actually emits the traffic it was
    declared for.

    **Optional peers resolve-if-present** (2026-07-25 refinement,
    docs/WORK_ORDER_traffic_tier.md): an `emit.optional` peer_role with no
    matching host in the twin is a legitimate topology (a DNS resolver or
    print server the twin simply doesn't carry, e.g. `corp-workstation`
    onto `corp-enterprise`, whose DNS is on the DC) -- skip it with a
    warning (mirrors how the reserved `egress`/`internet` tokens are
    already skipped, just louder) rather than failing the whole bind. A
    HARD (non-optional) unresolvable peer still fails loud, unchanged.

    **Excludes the instance's own host** (`exclude_host_id`, 2026-08-14) --
    the same rule `_resolve_peer_sets` has always had for `peer_scope: all`
    (LES-170), applied here to the single-peer path where it was missing. A
    persona whose `peer_role` is a role the SENDER ALSO HOLDS -- any mesh, and
    `domain-controller`'s DC-to-DC replication emit -- otherwise resolves to
    its own IP and talks to itself over loopback. That traffic is real enough
    to look healthy (the guest logs `sent ... fails=0`) and never crosses a
    tap point, so it is invisible to every sensor: a silent, total capture
    failure that neither the guest log nor `tfc status` reports. Found live on
    dev45 2026-08-13.

    Filtering happens BEFORE the empty-candidates check, so "the only holder of
    this role is me" falls through to the existing optional/hard handling
    rather than being papered over with a self-peer. That is what finally makes
    `domain-controller`'s own authored intent work: its replication emit is
    already `optional: true` with a comment saying inter-DC traffic "only
    exists when the twin has MORE THAN ONE domain controller" -- true, and
    previously defeated by self-resolution on every single-DC twin."""
    peers: dict[str, str] = {}
    for emit in persona.emits:
        role = emit.peer_role
        if not role or role in RESERVED_PEER_ROLES:
            continue
        all_holders = hosts_by_role.get(role) or []
        candidates = [h for h in all_holders if h.host_id != exclude_host_id]
        if not candidates and all_holders:
            # The role exists, but this instance is its only holder. Not an
            # authoring error (a one-DC twin is a normal topology) -- but not
            # drivable either, so skip with a warning instead of self-peering.
            # The guest client already logs-and-skips an emit with no peer.
            if warnings is not None:
                warnings.append(
                    f"persona {persona.id!r}: peer_role {role!r} resolves only "
                    f"to this instance's own host {exclude_host_id!r} -- "
                    "skipping (a host does not talk to itself, and loopback "
                    "traffic would never cross a tap point)"
                )
            continue
        if not candidates:
            if emit.optional:
                if warnings is not None:
                    warnings.append(
                        f"persona {persona.id!r}: optional peer_role {role!r} "
                        "has no host in the target twin -- skipping (this "
                        "emit will not be driven)"
                    )
                continue
            raise TrafficPlanError(
                f"persona {persona.id!r} emits to peer_role {role!r}, which "
                "has no host in the target twin (add a node with that role, "
                "or mark the emit optional: true if the persona should "
                "tolerate its absence)",
                status=400,
            )
        target = candidates[index % len(candidates)]
        ip = _host_ip(target)
        if not ip:
            raise TrafficPlanError(
                f"persona {persona.id!r} emits to peer_role {role!r} "
                f"(resolved to host {target.host_id!r}), but that host "
                "has no recorded IP in the twin inventory",
                status=400,
            )
        peers[role] = ip
    return peers


def _resolve_peer_sets(
    persona: Persona,
    hosts_by_role: dict[str, list[HostInfo]],
    *,
    exclude_host_id: str = "",
) -> dict[str, list[str]]:
    """Resolve every `peer_scope: all` emit's role to the FULL list of twin
    host IPs carrying it (2026-07-28, the high-volume persona wave).

    `_resolve_peers` above deliberately resolves one peer per role — the
    right model for a workstation talking to its DC. It is the wrong model
    for a persona whose defining behavior is talking to EVERYTHING: an
    authorized subnet-sweeping scanner, or a peer-to-peer update mesh whose
    flow count is meant to grow with the square of the twin. Those emits set
    `peer_scope: all` and are fanned out here instead.

    Complementary, not a replacement: `_resolve_peers` still runs for these
    emits too, so the existing fail-loud on a hard unresolvable role
    (LES-098/LES-001) is unchanged, and its single peer remains the driver's
    fallback when no set was threaded.

    **Excludes the instance's own host** (`exclude_host_id`): in a mesh
    persona every host shares the role, so an unfiltered fan-out would have
    each node talk to itself — self-directed traffic that never crosses a
    tap point, inflating the message count without producing a single
    capturable frame.

    IP-less hosts are skipped silently: the primary `_resolve_peers` pass
    already fail-louds when the role resolves to nothing usable, so a second
    error here would only duplicate it.
    """
    out: dict[str, list[str]] = {}
    for emit in persona.emits:
        role = emit.peer_role
        if emit.peer_scope != "all" or not role or role in RESERVED_PEER_ROLES:
            continue
        if role in out:
            continue
        ips: list[str] = []
        for h in hosts_by_role.get(role) or []:
            if exclude_host_id and h.host_id == exclude_host_id:
                continue
            ip = _host_ip(h)
            if ip:
                ips.append(ip)
        if ips:
            out[role] = ips
    return out


def _check_requires(persona: Persona, hosts_by_role: dict[str, list[HostInfo]]) -> None:
    """Fail-loud (LES-098/LES-001): every role a persona `requires[]` must be
    present in the target twin, or the assignment is nonsensical (e.g. a
    `corp-workstation` persona requiring `domain-controller` bound onto a
    twin with no DC)."""
    missing = [r for r in persona.requires if not hosts_by_role.get(r)]
    if missing:
        raise TrafficPlanError(
            f"persona {persona.id!r} requires role(s) {missing} which the "
            "target twin has no host for",
            status=400,
        )


# ---------------------------------------------------------------------------
# Persona os_family vs the host's OS (corp-twin live run 3, 2026-09-28).
#
# Every persona driver renders and launches for `persona.os_family`, never for
# the host's actual OS: a `linux` persona on a Windows host execs `sh`/`python3`
# over QGA and fails with the opaque `Agent error: Failed to execute child
# process (No such file or directory)`, once per instance, at apply time. The
# binding is decidable before anything runs -- the host's image names its OS --
# so it is refused at resolve (live) and at `tfc validate` (static, from the
# twin's te.yaml + the image manifests).
# ---------------------------------------------------------------------------
def persona_os_mismatch(persona: Persona, host_id: str, host_os: Optional[str]) -> Optional[str]:
    """The refusal message when `persona` cannot run on a host whose OS
    family is `host_os`; `None` when it can, or when `host_os` is unknown
    (never guessed -- an unknown OS is not evidence of a mismatch)."""
    if not host_os:
        return None
    want = (persona.os_family or "").lower()
    have = host_os.lower()
    if want == have:
        return None
    return (
        f"persona {persona.id!r} is os_family={want!r} but host {host_id!r} "
        f"runs {have!r} -- its {persona.drive!r} driver would launch a "
        f"{want} client over QGA and fail on every attempt; bind a persona "
        f"declared os_family: {have} (or fix this persona's os_family)"
    )


def _recorded_windows_hosts(store: Any, deploy_id: str) -> set[str]:
    """Hosts the twin's own plan rendered as Windows (`wait-os-ready`'s
    `os_family` input). Only `"windows"` is trusted: pass1_emit's fallback for
    an image it could not resolve is `"linux"`, so a recorded `linux` may be
    fabricated but a recorded `windows` never is. Used only when the
    inventory's manifest-derived `os` is unavailable (no `--images`)."""
    from axrange.planner.opview import op_fields

    out: set[str] = set()
    for op in store.ops_for_deploy(deploy_id):
        op_name, scope, inp, _out = op_fields(op)
        if op_name != "wait-os-ready" or scope.get("host") is None:
            continue
        if str(inp.get("os_family") or "").lower() == "windows":
            out.add(str(scope["host"]))
    return out


@dataclass(frozen=True)
class StaticHost:
    """One would-be twin host as its twin config declares it -- enough to
    check a profile offline: id (after `count:` fan-out), declared role,
    image id."""

    host_id: str
    role: Optional[str]
    image: Optional[str]


def static_hosts_from_twin_config(te_path: str | Path) -> list[StaticHost]:
    """Every host id a twin config's `nodes[]` can produce, with its declared
    role and image -- read from the raw file, with NO param substitution and
    NO deploy gate, so it is a superset of any gated deploy (a `node:` the
    gate removed is still found here; whether it is deployed is the live
    resolve's question, not this one's). A node whose `image:` is still an
    unsubstituted `${...}` param publishes `image=None`."""
    from axrange.ir.yaml_util import load_yaml_raw
    from axrange.twinconfig.expand import instance_ids
    from axrange.twinconfig.registry import _yaml_loader

    raw = load_yaml_raw(Path(te_path), loader=_yaml_loader()) or {}
    tc = raw.get("twin_config") if isinstance(raw, dict) else None
    nodes = (tc or {}).get("nodes") or []
    out: list[StaticHost] = []
    for n in nodes:
        if not isinstance(n, dict) or not n.get("name"):
            continue
        try:
            count = max(1, int(n.get("count", 1)))
        except (TypeError, ValueError):
            count = 1
        image = n.get("image")
        image = str(image) if image and "${" not in str(image) else None
        role = n.get("role")
        for hid in instance_ids(str(n["name"]), count):
            out.append(StaticHost(host_id=hid, role=str(role) if role else None, image=image))
    return out


def check_persona_os_static(
    spec: TrafficProfileSpec,
    hosts: list[StaticHost],
    os_family_of: Callable[[str], Optional[str]],
) -> tuple[list[str], list[str]]:
    """Offline counterpart of the resolve-time os check: every
    `assignments[]` entry's persona against the OS of each host it can bind
    to, per `hosts` (see `static_hosts_from_twin_config`) and
    `os_family_of(image_id)` (an image-manifest lookup). Returns
    `(errors, notes)`. A `node:` absent from `hosts`, an unknown persona, a
    host whose image OS is unknown: skipped (the live resolve reports the
    first two; the third is noted). `roles:` `node:` overrides are applied
    before `role:` assignments match; `selector:` role entries need live
    host facts, so they are noted and left to the live resolve."""
    errors: list[str] = []
    notes: list[str] = []
    by_id = {h.host_id: h for h in hosts}
    role_of = {h.host_id: h.role for h in hosts}
    if any(r.selector is not None for r in spec.roles):
        notes.append(
            "roles: has selector: entries -- hosts they re-role are checked "
            "by the live resolve only"
        )
    for r in spec.roles:
        if r.node is not None and r.node in role_of:
            role_of[r.node] = r.role
    unknown_images: set[str] = set()
    for a in spec.assignments:
        persona = persona_registry.get(a.persona)
        if persona is None:
            continue
        if a.node is not None:
            targets = [by_id[a.node]] if a.node in by_id else []
        else:
            targets = [h for h in hosts if role_of.get(h.host_id) == a.role]
        for h in targets:
            host_os = os_family_of(h.image) if h.image else None
            if host_os is None:
                if h.image:
                    unknown_images.add(h.image)
                continue
            msg = persona_os_mismatch(persona, h.host_id, host_os)
            if msg:
                errors.append(f"assignment {assignment_label(a)!r}: {msg}")
    for img in sorted(unknown_images):
        notes.append(f"image {img!r} has no os family in the manifests -- its hosts were not os-checked")
    return errors, notes


def resolve_traffic_bindings(
    spec: TrafficProfileSpec, store: Any, *, manifests: Any = None,
) -> TrafficPlanResolution:
    """Resolve `spec`'s assignments + replay bindings against `store` (an
    ALREADY-OPEN `StateStore` -- the caller owns its lifecycle): the target
    twin must exist and be tier="te"; every `role:`/`node:` must resolve to
    >=1 host in its published inventory; every persona must be catalog-known
    with its `requires[]` satisfied and every non-reserved `emits[].peer_role`
    resolvable. Raises `TrafficPlanError` on any structural problem -- this
    function always reports the true resolve outcome, never silently
    degrades (mirrors `taps.resolve_tap_plan_bindings`'s contract).

    **Multiple persona instances per host (E0, 2026-09-21,
    `docs/WORK_ORDER_protocol_engines.md` §3.1/§5).** One host may now be
    bound to several DIFFERENT personas at once -- the protocol-engine round
    needs exactly that (a `browser` instance for the web stack, a `command`
    instance for Kerberos/LDAP/SMB, a `scripted` one for the leftovers, on
    one Linux client). The same persona bound onto one host twice is still
    refused, loudly and by name: those two share an `instance_id` and would
    fight over one systemd unit / in-guest directory / op scope.

    **Role is unaffected by persona count.** A host's role resolves exactly
    as `docs/WORK_ORDER_role_persona_tier.md` W1 specifies -- this profile's
    own `roles:` block first, then the twin's `clone-vm` baseline
    (`HostInfo.role_baseline`), then none -- and `assignments[]` never names
    a role for a host, it CONSUMES one. So "which of a host's two personas
    names its role?" is not a question this code has to answer: neither
    does. Two `roles:` entries that DISAGREE about one host are refused by
    `_resolve_roles` (a loud error, never an entry-order-dependent pick),
    which is the only place a disagreement can arise."""
    from axrange.planner.inventory import build_twin_inventory

    if not spec.target:
        raise TrafficPlanError(
            "no traffic-profile target: set target: in the profile or pass "
            "--target (a te deploy id, or a scenario/twin-config name)",
            status=400,
        )
    te = store.get_deploy(spec.target)
    if te is None:
        raise TrafficPlanError(f"target twin deploy not found: {spec.target!r}", status=404)
    if (te.tier or "te") != "te":
        raise TrafficPlanError(
            f"target {spec.target!r} is tier={te.tier!r}; a traffic profile's "
            "twin must be a target-environment ('te') deploy",
            status=400,
        )

    try:
        twin = build_twin_inventory(store, spec.target, manifests=manifests)
    except InventoryError as e:
        raise TrafficPlanError(str(e), status=400) from e

    host_by_id = {h.host_id: h for h in twin.hosts}
    # The OS each host runs, for the persona os_family check below: the
    # manifest-derived inventory fact when `manifests` resolves the image,
    # else the twin plan's own recorded `windows` (`_recorded_windows_hosts`).
    recorded_windows = _recorded_windows_hosts(store, spec.target)

    def _host_os(h: HostInfo) -> Optional[str]:
        return h.os or ("windows" if h.host_id in recorded_windows else None)

    os_errors: list[str] = []

    # ---- roles FIRST (`docs/WORK_ORDER_role_persona_tier.md` §4.4) --------
    # The role map is resolved and applied BEFORE any assignment resolves,
    # so `assignments[].role` may reference a role THIS profile assigns.
    # Reversing these two would silently drop every such assignment -- the
    # ordering is load-bearing, not stylistic.
    resolved_roles, role_warnings = _resolve_roles(spec, twin, host_by_id)
    effective_role = {rr.host_id: rr.role for rr in resolved_roles}
    hosts_by_role = _hosts_by_role(twin, overrides=effective_role)

    resolved: list[ResolvedAssignment] = []
    results: list[AssignmentResult] = []
    warnings: list[str] = list(role_warnings)
    # E0 (`docs/WORK_ORDER_protocol_engines.md` §5): a host may carry MANY
    # persona instances -- `browser` for the web, `command` for Kerberos/SMB,
    # `scripted` for the leftovers, all at once. That is the whole point of
    # the protocol-engine round (one engine per protocol family), and the
    # identity model already supports it: `instance_id` is per-(host,
    # persona), and the unit name, the in-guest paths and the op scope
    # `{"instance": ...}` all key off it.
    #
    # What is still refused is the genuinely broken case: the SAME persona
    # bound onto one host TWICE (a `node:` entry and a `role:` entry that
    # both select it). Those two collide on `instance_id` -- one systemd
    # unit, one in-guest directory, one op scope -- so they would silently
    # fight instead of adding traffic.
    claimed_instances: dict[tuple[str, str], str] = {}  # (host, persona) -> label
    # S4: instance_id -> (host, persona) that minted it. `f"{host}-{persona}"`
    # is not injective (host `a` + `b-c` vs host `a-b` + `c`); two bindings
    # minting one id would share a unit and an op row, silently.
    minted: dict[str, tuple[str, str]] = {}

    def _mint(iid: str, host_id: str, what: str) -> None:
        prior = minted.get(iid)
        if prior is not None and prior != (host_id, what):
            raise TrafficPlanError(
                f"instance-collision: {what!r} on host {host_id!r} and {prior[1]!r} on "
                f"host {prior[0]!r} both mint instance id {iid!r} -- they would share "
                "one systemd unit and one op row; rename a host or a persona",
                status=400,
            )
        minted[iid] = (host_id, what)
    # host_id -> every label bound onto it, in resolve order. Only `replay[]`
    # still demands whole-host exclusivity (see its loop below).
    claims_by_host: dict[str, list[str]] = {}

    for a in spec.assignments:
        persona = persona_registry.get(a.persona)
        if persona is None:
            raise TrafficPlanError(
                f"assignment {assignment_label(a)!r}: unknown persona "
                f"{a.persona!r} (not in the persona catalog, "
                "catalog/personas/) -- check for a typo",
                status=400,
            )
        _check_requires(persona, hosts_by_role)

        if a.node is not None:
            host = host_by_id.get(a.node)
            if host is None:
                raise TrafficPlanError(
                    f"assignment {assignment_label(a)!r}: node {a.node!r} is "
                    f"not a host in target twin {spec.target!r}",
                    status=400,
                )
            hosts = [host]
        else:
            hosts = hosts_by_role.get(a.role) or []
            if not hosts:
                raise TrafficPlanError(
                    f"assignment {assignment_label(a)!r}: role {a.role!r} "
                    f"matches no host in target twin {spec.target!r}",
                    status=400,
                )

        host_ids: list[str] = []
        # `enumerate` gives each instance its position among THIS
        # assignment's resolved hosts -- `_resolve_peers`'s `index=` fans a
        # hub-role peer (e.g. 2 domain controllers) out round-robin across
        # instances instead of every one hammering the same deterministic-
        # first host (should-fix, code review, twin fidelity).
        for idx, h in enumerate(hosts):
            instance_id = f"{h.host_id}-{persona.id}"
            prior = claimed_instances.get((h.host_id, persona.id))
            if prior is not None:
                raise TrafficPlanError(
                    f"host {h.host_id!r} is claimed by both {prior!r} and "
                    f"{assignment_label(a)!r} for the SAME persona "
                    f"{persona.id!r} -- both resolve to instance id "
                    f"{instance_id!r}, so they would share one systemd unit, "
                    "one in-guest directory and one op scope, and silently "
                    "fight instead of adding traffic. A host may carry many "
                    "DISTINCT personas but only ONE instance of each: narrow "
                    "whichever entry also selects this host (a `node:` entry "
                    "and a `role:` entry naming it is the usual cause)",
                    status=400,
                )
            os_msg = persona_os_mismatch(persona, h.host_id, _host_os(h))
            if os_msg:
                # Collected, not raised on the first: one refusal naming every
                # mis-bound host is what an operator fixing a profile needs.
                os_errors.append(f"assignment {assignment_label(a)!r}: {os_msg}")
            claimed_instances[(h.host_id, persona.id)] = assignment_label(a)
            _mint(instance_id, h.host_id, persona.id)
            claims_by_host.setdefault(h.host_id, []).append(assignment_label(a))
            vmid = _require_vmid(h)
            peers = _resolve_peers(
                persona, hosts_by_role, index=idx, warnings=warnings,
                exclude_host_id=h.host_id,
            )
            peer_sets = _resolve_peer_sets(
                persona, hosts_by_role, exclude_host_id=h.host_id,
            )
            resolved.append(
                ResolvedAssignment(
                    instance_id=instance_id, host_id=h.host_id, vmid=vmid,
                    persona=persona, peers=peers, peer_sets=peer_sets, seed=a.seed,
                    schedule=a.schedule, source=assignment_label(a),
                )
            )
            host_ids.append(h.host_id)
        results.append(
            AssignmentResult(target=assignment_label(a), persona=persona.id, hosts=host_ids)
        )

    if os_errors:
        raise TrafficPlanError(
            f"{len(os_errors)} persona binding(s) cannot run on their host's "
            "OS:\n  " + "\n  ".join(os_errors),
            status=400,
        )

    for r in spec.replay:
        hosts = hosts_by_role.get(r.onto_role) or []
        if not hosts:
            raise TrafficPlanError(
                f"replay[{r.template!r}]: onto_role {r.onto_role!r} matches "
                f"no host in target twin {spec.target!r}",
                status=400,
            )
        for h in hosts:
            # E0 keeps `replay[]`'s WHOLE-HOST exclusivity deliberately (work
            # order §5 E0: "a whole pcap should not share a host"). A replay
            # rewrites and re-emits a captured capture of an entire device's
            # NIC; another binding emitting alongside it would corrupt the
            # very trace being replayed, and neither side could be told apart
            # at the sensor. Persona assignments lost this refusal; replay
            # bindings keep it, against BOTH personas and other replays.
            prior_labels = claims_by_host.get(h.host_id)
            if prior_labels:
                raise TrafficPlanError(
                    f"host {h.host_id!r} is claimed by both "
                    f"{prior_labels[0]!r} and a replay binding onto role "
                    f"{r.onto_role!r} -- a replay binding takes the WHOLE "
                    "host (it re-emits a captured device's entire NIC), so it "
                    "cannot share one with any other binding, unlike persona "
                    "assignments which may now stack",
                    status=400,
                )
            node_ip = _host_ip(h)
            if not node_ip:
                raise TrafficPlanError(
                    f"replay[{r.template!r}]: host {h.host_id!r} (role "
                    f"{r.onto_role!r}) has no recorded IP in the twin inventory",
                    status=400,
                )
            if not r.orig_ip:
                raise TrafficPlanError(
                    f"replay[{r.template!r}]: 'orig_ip' is required (the "
                    "template's original device address the replay driver "
                    "remaps to the twin host's IP)",
                    status=400,
                )
            claims_by_host.setdefault(h.host_id, []).append(f"replay:{r.template}")
            vmid = _require_vmid(h)
            instance_slug = Path(r.template).stem
            instance_id = f"{h.host_id}-replay-{instance_slug}"
            _mint(instance_id, h.host_id, f"replay:{instance_slug}")
            synthetic = Persona(
                id=f"replay:{instance_slug}", os_family=h.os or "linux",
                drive="replay",
            )
            resolved.append(
                ResolvedAssignment(
                    instance_id=instance_id, host_id=h.host_id, vmid=vmid,
                    persona=synthetic, peers={}, seed=None,
                    extra={
                        "template_pcap_path": r.template, "orig_ip": r.orig_ip,
                        "node_ip": node_ip, "iface": r.iface,
                    },
                    source=f"replay:{r.template}",
                )
            )
            results.append(
                AssignmentResult(
                    target=f"replay:{r.template}", persona=synthetic.id,
                    hosts=[h.host_id],
                )
            )

    return TrafficPlanResolution(
        roles=resolved_roles,
        target_status=te.status.value, twin=twin, resolved=resolved,
        assignment_results=results, warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Op composition -- one `drive-persona` OpInstance per resolved assignment,
# built by hand (see module docstring for why) using the SAME catalog
# metadata `pass1_emit._make_op`/`taps._make_mir_op` use.
# ---------------------------------------------------------------------------
def _op_timeout_s(spec: Optional[str]) -> Optional[int]:
    if not spec or not spec.endswith("s"):
        return None
    try:
        return int(spec[:-1])
    except ValueError:
        return None


def _make_tfc_op(
    plan_id: str, scope: dict[str, str], inputs_resolved: dict[str, Any],
    *, op_name: str = _DRIVE_PERSONA_OP,
) -> OpInstance:
    """Build one tfc-tier OpInstance. `op_name` defaults to drive-persona so
    every pre-2026-08-04 call site is unchanged; `assign-role` passes its own
    (`docs/WORK_ORDER_role_persona_tier.md`)."""
    op_def = get_op(op_name)
    if op_def is None:  # pragma: no cover -- defensive; catalog names are fixed
        raise TrafficPlanError(f"unknown op name: {op_name}", status=500)
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


def compose_traffic_ops(
    resolution: TrafficPlanResolution, plan_id: str, *,
    origins: Optional[dict[str, str]] = None,
) -> Plan:
    """Compose the drive-persona playout for an ALREADY-RESOLVED traffic
    profile, WITHOUT running it -- also the `tfc plan`/`tfc validate` entry
    points' shared core.

    `origins` (host_id -> marker) stamps `origin: <marker>` into the inputs
    of every op on those hosts -- the per-host apply passes `{host:
    "host-edit"}` so a later whole-profile apply can tell it would revert a
    host edit (`host_edit_reverts`). `None` (every whole-profile caller)
    adds no key, so a profile's persisted op inputs are byte-identical to
    before this parameter existed. The key is not in the op_id (scope-only)."""
    origins = origins or {}
    plan = Plan(
        plan_id=plan_id, scenario_id=plan_id, scenario_version=None,
        ir_hash="", tier="tfc",
    )
    # `assign-role` first: it publishes the host->role map the twin inventory
    # then prefers over its clone-vm baseline. No DAG edge is needed between
    # the two op kinds -- role RESOLUTION already happened in
    # `resolve_traffic_bindings` (before any assignment resolved), so a
    # drive-persona op's peers are already correct by the time either runs.
    # The ordering here is for a readable plan tree, not correctness.
    for rr in resolution.roles:
        role_inputs: dict[str, Any] = {"host_id": rr.host_id, "role": rr.role}
        if rr.host_id in origins:
            role_inputs["origin"] = origins[rr.host_id]
        plan.add_op(_make_tfc_op(
            plan_id, {"host": rr.host_id}, role_inputs, op_name=_ASSIGN_ROLE_OP,
        ))
    for ra in resolution.resolved:
        scope = {"instance": ra.instance_id}
        inputs: dict[str, Any] = {
            "persona_id": ra.persona.id,
            "drive": ra.persona.drive,
            "os_family": ra.persona.os_family,
            "instance_id": ra.instance_id,
            "host_id": ra.host_id,
            "vmid": ra.vmid,
            "peers": dict(ra.peers),
            "seed": ra.seed,
            "schedule": ra.schedule,
        }
        # Omitted entirely (never a bare empty-dict key) when no emit uses
        # `peer_scope: all`, so a pre-2026-07-28 persona's persisted op
        # inputs -- and therefore its `ir_hash`/replay identity -- are
        # byte-identical to before this field existed.
        if ra.peer_sets:
            inputs["peer_sets"] = {k: list(v) for k, v in ra.peer_sets.items()}
        for k in _DRIVER_EXTRA_KEYS:
            if k in ra.extra:
                inputs[k] = ra.extra[k]
        if ra.host_id in origins:
            inputs["origin"] = origins[ra.host_id]
        plan.add_op(_make_tfc_op(plan_id, scope, inputs))
    plan.freeze()
    return plan


# ---------------------------------------------------------------------------
# PersonaBackend -- the `Adapter`-protocol-shaped (`.run(op, inputs, ctx)`)
# execution backend for `drive-persona`, standing in for BOTH `adapter=` and
# `config_backend=` when this module's ops are run through the SAME engine
# `Executor` every other lifecycle uses (`run_deploy_executor`). See the
# module docstring for why this can't just be `axrange.config_exec.
# ConfigBackend` (an import-cycle boundary reason, not a laziness one).
# ---------------------------------------------------------------------------
class PersonaBackend:
    """Drives one persona instance via `axrange.personas.drivers.get_driver`
    over the platform's guest-exec seam (`axrange.platform`, ARCHITECTURE
    §13) -- the SAME connection `axrange.config_exec.ConfigBackend` uses, no
    second guest-control client. `guest_agent_factory` (test seam) is called
    `(client, host, os_family=...)` with the platform's connection."""

    def __init__(self, config: Any, *, rest_client: Any = None, guest_agent_factory: Any = None) -> None:
        from axrange.platform import DEFAULT_PLATFORM, adapter_for

        self.config = config
        self._adapters = adapter_for(DEFAULT_PLATFORM, config, client=rest_client)
        self._client = self._adapters.client
        self._ga_factory = guest_agent_factory or (
            lambda _client, node, *, os_family="linux": self._adapters.guest_exec(node, os_family=os_family)
        )

    def run(self, op: Any, resolved_inputs: dict[str, Any], ctx: Any) -> dict[str, Any]:
        from axrange.planner.executor import AdapterError

        if op.op_name == _ASSIGN_ROLE_OP:
            return self._assign_role(resolved_inputs)
        if op.op_name != _DRIVE_PERSONA_OP:
            raise AdapterError(
                f"persona backend has no implementation for {op.op_name!r}",
                error_class="tfc-unsupported",
            )
        return self._drive_persona(resolved_inputs, ctx)

    def _credentials(self) -> dict[str, Any]:
        """The credential material a persona driver may need in the guest
        (E5's real clients: smbclient/ldapsearch/kinit/rpcclient/xfreerdp/
        snmpget all authenticate). One lab-wide value today -- adapter.conf's
        `DEFAULT_PASSWORD` -- which is the same source `config_exec.backend`
        uses for every identity it creates, so a persona logs in as an
        identity the twin actually provisioned."""
        return {"default_password": getattr(self.config, "default_password", "")}

    def _assign_role(self, inp: dict[str, Any]) -> dict[str, Any]:
        """`assign-role` (OP-TFC-020) touches NOTHING -- no adapter call, no
        guest exec, nothing on the wire. Its entire effect is the persisted op
        row itself, which `axrange.planner.inventory` reads back to prefer a
        tfc-assigned role over the twin's clone-vm baseline
        (`docs/WORK_ORDER_role_persona_tier.md` §4.2).

        It still validates its inputs rather than blindly returning success: a
        row recording `host_id=None` would publish a role nobody can look up,
        which is the silent-success shape LES-001 exists to prevent."""
        from axrange.planner.executor import AdapterError

        host_id, role = inp.get("host_id"), inp.get("role")
        if not host_id or not role:
            raise AdapterError(
                f"assign-role needs both host_id and role (got host_id="
                f"{host_id!r}, role={role!r})",
                error_class="tfc-assign-role-invalid",
            )
        return {"assigned": True}

    def _drive_persona(self, inp: dict[str, Any], ctx: Any) -> dict[str, Any]:
        from axrange.planner.executor import AdapterError

        drive = inp.get("drive")
        persona_id = inp.get("persona_id") or "unknown"
        if not drive:
            raise AdapterError(
                f"drive-persona[{inp.get('instance_id')}]: no 'drive' recorded "
                "for this instance", error_class="tfc-input",
            )
        # A replay assignment's persona is synthetic (never in the shared
        # catalog -- see `resolve_traffic_bindings`'s replay branch), so a
        # minimal stand-in with the same id/os_family/drive is enough here;
        # no driver ever reads anything else off `Persona` at run time beyond
        # `emits`/`requires`/`knobs` (scripted/real-service only).
        persona = persona_registry.get(persona_id) or Persona(
            id=persona_id, os_family="linux", drive=drive,
        )
        ga = self._ga_factory(self._client, ctx.cluster.default_node, os_family=persona.os_family)
        extra = {k: inp[k] for k in _DRIVER_EXTRA_KEYS if inp.get(k) is not None}
        try:
            driver = get_driver(drive)
            result = driver.start(
                ga, inp["vmid"], persona, instance_id=inp["instance_id"],
                peers=inp.get("peers") or {}, seed=inp.get("seed"),
                schedule=inp.get("schedule"),
                # Part of the core peer-resolution contract (like `peers`),
                # not a per-drive extra -- the replay/real-service drivers
                # absorb it via their `**kwargs`, exactly as they already do
                # for `schedule`.
                peer_sets=inp.get("peer_sets") or {},
                # Credentials are passed as a RUNTIME kwarg, never as an op
                # input (E5, docs/WORK_ORDER_protocol_engines.md): resolved
                # here from `AdapterConfig.default_password` -- exactly where
                # `config_exec.backend` resolves it for `create-identity`/
                # `join-domain` -- so no secret is ever written into
                # `state.db`, into a plan hash, or into a rendered client.
                # Drivers that do not authenticate absorb it via `**kwargs`.
                credentials=self._credentials(), **extra,
            )
        except PersonaDriverError as e:
            raise AdapterError(str(e), error_class="tfc-driver-failed") from e
        return {"started": bool(result.get("started") or result.get("asserted") or False)}


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------
def apply_traffic_profile(
    *,
    spec: Optional[TrafficProfileSpec] = None,
    spec_path: Optional[str | Path] = None,
    db_path: str,
    adapter_config: Optional[str] = None,
    target_node: Optional[str] = None,
    target: Optional[str] = None,
    persona_backend: Any = None,
    manifests: Any = None,
    allow_revert_host_edits: bool = False,
) -> TrafficPlanStatus:
    """Apply a traffic profile: bind personas onto the target twin's hosts
    and run the composed drive-persona ops through the engine executor
    (`run_deploy_executor` -- LES-037 reuse).

    Idempotent: one traffic-profile deploy per target twin (`plan_id =
    f"tfc-{twin}"`, deterministic, resumed via `StateStore.
    find_deploy_for_plan`). Every apply force-resets every composed op's
    op_id to `pending` before running the executor (mirrors `taps.
    apply_tap_plan`'s M1 fix, LES-057) -- an unchanged profile re-launches
    each persona instance with byte-identical rendered content (a cluster
    no-op in effect); a changed one (new persona, re-resolved peer, dropped
    assignment) is genuinely re-asserted, never silently skipped because an
    identically-scoped op_id happened to already be `completed`.

    **Re-asserting and de-scoping are two SEPARATE obligations (LES-112,
    MUST-FIX, code review)** -- force-reset above only re-runs ops the NEW
    plan still contains; it says nothing about ones the new plan DROPPED. A
    narrowed re-apply (an assignment removed) or a persona SWAP on one host
    (`instance_id` is `f"{host_id}-{persona_id}"`, so swapping the persona
    changes the systemd unit name) would otherwise leave the dropped/old
    instance's unit running forever -- for a swap, running ALONGSIDE the new
    one, both emitting simultaneously. `_stop_dropped_instances` (called
    after a successful run, below) closes this: every previously-`completed`
    instance NOT in this apply's new resolved set is stopped via its driver
    and its op row marked `destroyed`, mirroring `taps.py`'s drop-diff
    pattern for gretaps.

    `target` overrides the profile's own `target:`/`twin:` (the `--target`
    CLI flag / API-body field, `docs/WORK_ORDER_role_persona_tier.md` §4.3).
    Precedence is override > profile > error. The effective value is
    resolved to a CONCRETE deploy id before any state write, because
    `plan_id` and every StateStore row below key off it -- a reusable
    scenario NAME must never reach the state store (the same ordering
    `taps.apply_tap_plan` establishes).

    NOTE `target_node` (a PVE cluster NODE, overriding `DEFAULT_NODE`) and
    `target` (the twin this profile binds ONTO) are unrelated despite the
    similar spelling; both already existed under those names on their
    respective sides, so neither is renamed here.

    `persona_backend` is a test seam (inject a fake, no real QGA/Proxmox);
    production builds the real `PersonaBackend`.

    `manifests` (optional `ManifestRegistry`, the CLI's `--images`) gives the
    inventory each host's manifest OS for the persona os_family check in
    `resolve_traffic_bindings`; without it that check still refuses a
    non-Windows persona on a host the twin plan recorded as Windows.

    **Host edits are never reverted silently** (Unified Console ruling
    §3.3): when the standing tfc deploy carries rows a per-host apply wrote
    (`origin: host-edit`) and this profile would change that host's persona
    set or role, the apply is refused 409 naming each host, unless
    `allow_revert_host_edits` (CLI `--revert-host-edits`). With no host
    edits recorded -- every deploy before this feature -- nothing changes.
    """
    from axrange.platform import load_config
    from axrange.planner.entry import release_deploy_resources, run_deploy_executor
    from axrange.planner import StateStore
    from axrange.planner.cluster import ClusterContext
    from axrange.planner.locks import Heartbeat
    from axrange.planner.state import DeployStatus, default_process_info

    resolved_spec = resolve_traffic_profile(spec, spec_path)

    effective_target = target or resolved_spec.target
    if not effective_target:
        raise TrafficPlanError(
            "no traffic-profile target: set target: in the profile or pass "
            "--target",
            status=400,
        )

    store = StateStore(db_path)
    try:
        # Resolve to a CONCRETE deploy id BEFORE anything else: `plan_id =
        # f"tfc-{...}"` and every StateStore write below must key off the
        # minted id, never a reusable scenario name (LES-072; mirrors
        # `taps.apply_tap_plan`'s identical ordering).
        concrete_target = resolve_traffic_target(store, effective_target)
        if concrete_target != resolved_spec.target:
            # `replace()` copies EVERY field and overrides only `target`.
            # Hand-listing the fields here dropped `roles` -- the W1 field
            # added to this dataclass after this rebuild site was first
            # copied from taps.py -- so a NAME target (proof 2's whole point)
            # silently discarded the profile's role map before
            # `resolve_traffic_bindings` ever saw it (W1 dev45 live-verify,
            # 2026-08-04; invisible to the unit suite because a literal
            # deploy-id target skips this branch entirely). `replace` is
            # immune to the next field added, closing the bug class, not just
            # this instance (LES-037).
            resolved_spec = replace(resolved_spec, target=concrete_target)

        cfg = load_config(adapter_config)
        if target_node:
            cfg.default_node = target_node

        resolution = resolve_traffic_bindings(resolved_spec, store, manifests=manifests)
        if not cfg.default_node:
            raise TrafficPlanError(
                "DEFAULT_NODE missing; set it in adapter.conf, or override "
                "via target_node", status=400,
            )
        ctx = ClusterContext.single_node(cfg.default_node)

        plan_id = f"tfc-{resolved_spec.target}"
        plan = compose_traffic_ops(resolution, plan_id)

        existing = store.find_deploy_for_plan(plan_id, exclude_destroyed=True)
        deploy_id = (
            existing.deploy_id if existing is not None
            else f"{plan_id}-{secrets.token_hex(4)}"
        )

        lease = store.acquire_lease(plan_id, deploy_id, process_info=default_process_info())
        if not lease.acquired:
            raise TrafficPlanError(
                f"another traffic-profile operation for twin "
                f"{resolved_spec.target!r} is in progress "
                f"(deploy_id={lease.held_by_deploy_id})", status=409,
            )

        # The revert guard runs UNDER the lease (code review M3): a host edit
        # finishing between an unlocked check and the lease would otherwise
        # be reverted silently.
        revert_warnings: list[str] = []
        try:
            reverts = (
                host_edit_reverts(store, existing.deploy_id, resolution)
                if existing is not None else []
            )
        except BaseException:
            store.release_lease(plan_id, deploy_id)
            raise
        if reverts:
            if not allow_revert_host_edits:
                store.release_lease(plan_id, deploy_id)
                hosts = ", ".join(r["host_id"] for r in reverts)
                raise TrafficPlanError(
                    f"this profile would revert {len(reverts)} host edit(s) made "
                    f"with a per-host persona apply on twin {resolved_spec.target!r} "
                    f"({hosts}) -- export the running state first "
                    "(GET /api/deploys/<id>/tfc/profile) or confirm the revert "
                    "(--revert-host-edits / allow_revert_host_edits: true)",
                    status=409, detail={"host_edits": reverts},
                )
            for r in reverts:
                revert_warnings.append(
                    f"host edit on {r['host_id']} reverted to the profile "
                    f"(was {r['current']}, role {r['role_current']!r}; now "
                    f"{r['profile']}, role {r['role_profile']!r})"
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
                    placement={}, tier="tfc",
                )

            backend = persona_backend or PersonaBackend(cfg)

            # Force EVERY op back to pending, including one already
            # `completed` from an earlier apply, so a changed assignment is
            # genuinely re-driven (LES-057, mirrors taps.py's M1 fix).
            store.force_reset_ops(deploy_id, [op.op_id for op in plan.ops.values()])

            # `backend` fills BOTH the `adapter=` (infra) and `config_backend=`
            # slots -- valid only because every `drive-persona` op is
            # config-class (idempotency_class=CONFIG), so `Executor._backend_for`
            # always routes to `config_backend` and the `adapter=` slot is
            # never actually invoked. A future tfc op that ISN'T config-class
            # would hit `PersonaBackend.run`'s fail-loud "no implementation
            # for {op_name}" the moment it's dispatched as `adapter=` -- a
            # deliberate trip-wire, not a silent gap (code review).
            import time as _time

            # Host-edit job records survive the re-drive (fix round 4, D2).
            jobs_before = _snapshot_jobs(store, deploy_id) if existing is not None else {}
            apply_started = _time.time()
            try:
                result = run_deploy_executor(
                    store, backend, backend, plan, ctx, deploy_id, heartbeat,
                )
            finally:
                _restore_jobs(store, deploy_id, jobs_before)
            status = "completed" if result.succeeded else "failed"
            warnings: list[str] = list(resolution.warnings) + revert_warnings
            if result.succeeded:
                # MUST-FIX (code review, LES-112): stop + de-scope every
                # instance THIS apply's new plan no longer contains -- see
                # this function's own docstring for the persona-swap /
                # narrowing-removal bugs this closes. Best-effort logged
                # into `warnings` (LES-102: never `/dev/null` a log
                # collector) rather than silently swallowed.
                live_instance_ids = {ra.instance_id for ra in resolution.resolved}
                _stop_dropped_instances(
                    store, deploy_id, live_instance_ids, cfg=cfg,
                    persona_backend=backend, log=warnings.append,
                )
                # Same obligation for the ROLE map: a host dropped from
                # `roles:` must revert to its twin baseline, which only
                # happens once its `assign-role` row stops being `completed`
                # (`docs/WORK_ORDER_role_persona_tier.md` §4.4; the GAP-045
                # failure shape).
                _retire_dropped_roles(
                    store, deploy_id, {rr.host_id for rr in resolution.roles},
                    log=warnings.append,
                )
                # S5: a successful whole-profile apply supersedes every host
                # edit it did not keep -- their retired rows stop counting as
                # host edits, so the next profile change needs no flag.
                _supersede_host_edit_markers(store, deploy_id)
                _supersede_jobs(store, deploy_id, apply_started)  # D3
        finally:
            release_deploy_resources(store, plan_id, deploy_id, heartbeat)

        return TrafficPlanStatus(
            deploy_id=deploy_id, twin=resolved_spec.target, status=status,
            assignments=resolution.assignment_results,
            warnings=warnings,
        )
    finally:
        store.close()


# ---------------------------------------------------------------------------
# Per-host persona apply -- the scalpel (`docs/reference/ui-consolidation/
# 00-design-ruling.md` §3.3). Change ONE host's personas (and optionally its
# role) on a standing twin without re-driving, stopping or even touching the
# row of any other host's persona:
#
#   * same lease as a whole-profile apply (`tfc-<te>`), so the two serialize;
#   * desired set = the standing tfc deploy's COMPLETED rows with only this
#     host's entries replaced -- resolved through the unchanged
#     `resolve_traffic_bindings`, so duplicate / requires / peer / OS checks
#     run exactly as for a profile;
#   * the executor is handed ONLY this host's ops (plus opted-in dependents),
#     and only those are force-reset: every other row keeps its status,
#     timestamps and attempts because nothing ever writes to it (NOT even
#     `reset_incomplete_ops`, which would flip another host's failed row);
#   * de-scope is restricted to this host (`_stop_dropped_instances(host_ids=)`);
#   * rows it writes carry `origin: host-edit`, so a later whole-profile apply
#     that would revert them refuses unless told (`host_edit_reverts`).
# ---------------------------------------------------------------------------
HOST_EDIT_ORIGIN = "host-edit"


@dataclass
class HostPersonaAssignment:
    """One persona requested on the edited host. `schedule` is RAW here and
    normalized by `plan_host_personas` (the same `_normalize_schedule` a
    profile file goes through). `intensity` is accepted for parity with a
    profile entry and, like there, never persisted."""

    persona: str
    schedule: Optional[str] = None
    seed: Any = None
    intensity: Optional[float] = None


@dataclass
class HostEditPlan:
    te_deploy_id: str
    host_id: str
    tfc_deploy_id: Optional[str]
    creates_tfc_deploy: bool
    role_current: Optional[str] = None
    role_baseline: Optional[str] = None
    role_new: Optional[str] = None
    role_changed: bool = False
    start: list[dict[str, Any]] = field(default_factory=list)
    stop: list[dict[str, Any]] = field(default_factory=list)
    unchanged_hosts: int = 0
    unchanged_instances: int = 0
    dependents: list[dict[str, Any]] = field(default_factory=list)
    update_dependents: bool = False
    refusals: list[dict[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # sha256 over the plan's decisions AND the tfc rows it was computed from
    # (`_plan_fingerprint`). A PUT carrying `expect_plan` is refused 409 when
    # the plan computed under the lease has another one (code review M4).
    fingerprint: str = ""
    # Engine-internal: what `apply_host_personas` runs.
    resolution: Optional[TrafficPlanResolution] = None
    role_action: Optional[str] = None  # "set" | "clear" | None (keep)

    @property
    def ok(self) -> bool:
        return not self.refusals

    def to_dict(self) -> dict[str, Any]:
        return {
            "deploy_id": self.te_deploy_id,
            "host_id": self.host_id,
            "tfc_deploy_id": self.tfc_deploy_id,
            "creates_tfc_deploy": self.creates_tfc_deploy,
            "ok": self.ok,
            "role": {
                "current": self.role_current, "baseline": self.role_baseline,
                "new": self.role_new, "changed": self.role_changed,
            },
            "start": [dict(x) for x in self.start],
            "stop": [dict(x) for x in self.stop],
            "unchanged": {"hosts": self.unchanged_hosts, "instances": self.unchanged_instances},
            "dependents": [dict(x) for x in self.dependents],
            "will_update_dependents": bool(self.update_dependents and self.dependents),
            "refusals": [dict(x) for x in self.refusals],
            "warnings": list(self.warnings),
            "fingerprint": self.fingerprint,
        }


@dataclass
class HostEditResult:
    te_deploy_id: str
    host_id: str
    tfc_deploy_id: str
    status: str          # "running" (detached) | "completed" | "failed"
    detached: bool
    plan: HostEditPlan
    warnings: list[str] = field(default_factory=list)


def _recorded_tfc_state(
    store: Any, tfc_deploy_id: Optional[str],
) -> tuple[dict[str, tuple[str, str, dict[str, Any]]], dict[str, list[tuple[str, dict[str, Any]]]]]:
    """The standing tfc deploy's COMPLETED rows (LES-113's positive filter):
    `host -> (role, op_id, inputs)` for `assign-role`, and `host -> [(op_id,
    inputs)]` for `drive-persona`. Both empty without a tfc deploy."""
    from axrange.planner.opview import op_fields

    roles: dict[str, tuple[str, str, dict[str, Any]]] = {}
    drives: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    if not tfc_deploy_id:
        return roles, drives
    for op in store.ops_for_deploy(tfc_deploy_id):
        if op.status != OpStatus.COMPLETED:
            continue
        _name, scope, inp, _out = op_fields(op)
        if op.op_name == _ASSIGN_ROLE_OP:
            h = scope.get("host") or inp.get("host_id")
            if h and inp.get("role"):
                roles[str(h)] = (str(inp["role"]), op.op_id, inp)
        elif op.op_name == _DRIVE_PERSONA_OP:
            h = inp.get("host_id")
            if h:
                drives.setdefault(str(h), []).append((op.op_id, inp))
    for rows in drives.values():
        rows.sort(key=lambda r: str(r[1].get("instance_id") or ""))
    return roles, drives


def twin_persona_overlay(
    twin_id: Optional[str], roots: Any = None,
) -> tuple[dict[str, Persona], list[str], bool]:
    """`(overlay, warnings, twin_found)` -- a twin folder's `personas/` as a
    per-request overlay (`personas.registry.twin_overlay`), the ONE helper
    the CLI (`tfc set-host`) and the API share (S10). `roots`: the twins
    catalog search path (`twinconfig.registry.search_dirs` input)."""
    from axrange.personas.registry import PersonaRegistryError, twin_overlay
    from axrange.twinconfig import TwinConfigError
    from axrange.twinconfig.manifest import find_twin_dir, load_twin_manifest
    from axrange.twinconfig.registry import search_dirs

    if not twin_id or "/" in twin_id or "\\" in twin_id or twin_id in (".", ".."):
        return {}, [], False
    try:
        loaded = load_twin_manifest(find_twin_dir(twin_id, search_dirs(roots), warnings=[]))
    except (TwinConfigError, OSError):
        return {}, [f"no twin folder for {twin_id!r}: catalog personas only"], False
    try:
        return twin_overlay(loaded.personas_dir), [], True
    except PersonaRegistryError as e:
        return {}, [f"twin {twin_id!r} personas failed to load: {e}"], True


def current_host_assignments(store: Any, te_id: str, host_id: str) -> list[HostPersonaAssignment]:
    """The host's RUNNING persona set (completed `drive-persona` rows of the
    twin's standing tfc deploy) as assignments -- what a role-only edit
    keeps. `[]` when the host runs none."""
    tfc = store.find_deploy_for_plan(f"tfc-{te_id}", exclude_destroyed=True)
    _roles, drives = _recorded_tfc_state(store, tfc.deploy_id if tfc else None)
    return [
        HostPersonaAssignment(
            persona=str(inp.get("persona_id")), schedule=inp.get("schedule"),
            seed=inp.get("seed"),
        )
        for _op, inp in drives.get(host_id, [])
        if inp.get("drive") != "replay"
    ]


def _refusal_code(message: str) -> str:
    if "instance-collision" in message:
        return "instance-collision"
    if "cannot run on their host's OS" in message:
        return "os-mismatch"
    if "unknown persona" in message:
        return "unknown-persona"
    return "resolve"


def _stale_dependents(
    drives: dict[str, list[tuple[str, dict[str, Any]]]], host_id: str,
    twin: TwinInventory, old_roles: dict[str, str], new_roles: dict[str, str],
) -> list[dict[str, Any]]:
    """Other hosts' running instances whose recorded peer picks a role change
    invalidates: a recorded single peer that no longer holds the role, a
    peer role that gains its first holder, or a `peer_scope: all` set whose
    membership changes. Only roles whose holder list actually changes are
    looked at, so a persona swap (no role change) has no dependents."""
    old_hbr = _hosts_by_role(twin, overrides=old_roles)
    new_hbr = _hosts_by_role(twin, overrides=new_roles)
    changed = {
        r for r in set(old_hbr) | set(new_hbr)
        if [h.host_id for h in old_hbr.get(r, [])] != [h.host_id for h in new_hbr.get(r, [])]
    }
    out: list[dict[str, Any]] = []
    if not changed:
        return out
    for h, rows in sorted(drives.items()):
        if h == host_id:
            continue
        for op_id, inp in rows:
            if inp.get("drive") == "replay":
                continue
            persona = persona_registry.get(str(inp.get("persona_id") or ""))
            if persona is None:
                continue
            rec_peers = inp.get("peers") or {}
            rec_sets = inp.get("peer_sets") or {}
            stale: dict[str, Any] = {}
            for emit in persona.emits:
                role = emit.peer_role
                if not role or role in RESERVED_PEER_ROLES or role not in changed or role in stale:
                    continue
                ips = [
                    ip for ip in (_host_ip(x) for x in new_hbr.get(role, []) if x.host_id != h)
                    if ip
                ]
                if emit.peer_scope == "all" and sorted(rec_sets.get(role) or []) != sorted(ips):
                    stale[role] = {"recorded": sorted(rec_sets.get(role) or []), "new": sorted(ips)}
                    continue
                rec = rec_peers.get(role)
                if rec is None and ips:
                    stale[role] = {"recorded": None, "new": ips[0]}
                elif rec is not None and rec not in ips:
                    stale[role] = {"recorded": rec, "new": ips[0] if ips else None}
                    if not ips:
                        # S7: say it plainly -- a re-drive has no one to pick.
                        stale[role]["no_new_holder"] = True
            if stale:
                out.append({
                    "host_id": h, "instance_id": inp.get("instance_id"),
                    "persona": inp.get("persona_id"), "op_id": op_id,
                    "stale_peers": stale,
                    "no_new_holder": any(v.get("no_new_holder") for v in stale.values()),
                    "schedule": inp.get("schedule"), "seed": inp.get("seed"),
                })
    return out


def _inputs_key(inp: dict[str, Any]) -> tuple:
    import json as _json

    return (
        inp.get("persona_id"), inp.get("schedule"), _json.dumps(inp.get("seed"), default=str),
        _json.dumps(inp.get("peers") or {}, sort_keys=True),
        _json.dumps(inp.get("peer_sets") or {}, sort_keys=True),
    )


def plan_host_personas(
    store: Any, target: str, host_id: str,
    assignments: list[HostPersonaAssignment], *,
    role: Optional[str] = None, update_dependents: bool = False, manifests: Any = None,
    allow_role_orphaning: bool = False,
) -> HostEditPlan:
    """PURE preview of a per-host persona apply -- reads `store`, writes
    nothing (the API's `.../personas/plan` and `tfc set-host --plan`).

    `assignments` is the host's COMPLETE desired persona set (`[]` stops
    them all). `role`: `None` keeps the host's role, `""` drops its tfc
    override (back to the twin baseline), a string sets it. A role change
    lists the other hosts whose recorded peer picks it invalidates
    (`dependents`); they are re-driven only with `update_dependents`.

    A not-found target/host raises `TrafficPlanError` (404); a request the
    resolver refuses (OS mismatch, unknown persona, duplicate, missing
    requires/peer) comes back as `refusals` so a preview can show it."""
    from axrange.planner.inventory import build_twin_inventory

    te_id = resolve_traffic_target(store, target)
    te = store.get_deploy(te_id)
    if te is None or (te.tier or "te") != "te":
        raise TrafficPlanError(
            f"target {target!r} is not a target-environment ('te') deploy", status=400,
        )
    try:
        twin = build_twin_inventory(store, te_id, manifests=manifests)
    except InventoryError as e:
        raise TrafficPlanError(str(e), status=400) from e
    host_by_id = {h.host_id: h for h in twin.hosts}
    host = host_by_id.get(host_id)
    if host is None:
        raise TrafficPlanError(
            f"host {host_id!r} is not a host of twin deploy {te_id!r}", status=404,
        )

    tfc = store.find_deploy_for_plan(f"tfc-{te_id}", exclude_destroyed=True)
    roles_rec, drives_rec = _recorded_tfc_state(store, tfc.deploy_id if tfc else None)
    plan = HostEditPlan(
        te_deploy_id=te_id, host_id=host_id,
        tfc_deploy_id=tfc.deploy_id if tfc else None, creates_tfc_deploy=tfc is None,
        role_baseline=host.role_baseline, update_dependents=update_dependents,
    )

    # ---- role map: recorded tfc roles, this host's entry replaced ---------
    old_roles = {h: r for h, (r, _op, _inp) in roles_rec.items() if h in host_by_id}
    for h in sorted(set(roles_rec) - set(host_by_id)):
        plan.warnings.append(
            f"recorded tfc role for {h!r} names a host the twin no longer has -- ignored"
        )
    new_roles = dict(old_roles)
    if role is not None:
        if role == "":
            new_roles.pop(host_id, None)
            plan.role_action = "clear" if host_id in old_roles else None
        else:
            new_roles[host_id] = role
            plan.role_action = "set"
    plan.role_current = old_roles.get(host_id) or host.role_baseline
    plan.role_new = new_roles.get(host_id) or host.role_baseline
    plan.role_changed = plan.role_new != plan.role_current

    # ---- the host's own rows ----------------------------------------------
    on_host = {str(inp.get("instance_id")): (op_id, inp) for op_id, inp in drives_rec.get(host_id, [])}
    # Every NON-destroyed row (any status) -- a failed/pending instance absent
    # from the new set is stopped and retired too (S6, LES-113).
    all_rows = _live_drive_rows(store, tfc.deploy_id if tfc else None)
    live_on_host = {
        str(inp.get("instance_id")): (op_id, inp)
        for op_id, inp, _st in all_rows if inp.get("host_id") == host_id
    }
    plan.fingerprint = _plan_fingerprint(plan, store, tfc)
    if any(inp.get("drive") == "replay" for _op, inp in live_on_host.values()):
        plan.refusals.append({
            "code": "replay-host",
            "message": f"host {host_id!r} carries a pcap replay binding, which takes "
                       "the whole host; change it through the full traffic profile",
        })
        plan.fingerprint = _plan_fingerprint(plan, store, tfc)
        return plan

    specs: list[AssignmentSpec] = []
    for i, a in enumerate(assignments):
        if not a.persona or not isinstance(a.persona, str):
            raise TrafficPlanError(f"assignments[{i}]: 'persona' is required", status=400)
        specs.append(AssignmentSpec(
            node=host_id, persona=a.persona, intensity=a.intensity,
            schedule=_normalize_schedule(a.schedule, context=f"assignments[{i}]"),
            seed=a.seed,
        ))
    # S4: `instance_id` is `f"{host}-{persona}"`, so host `a` + persona `b-c`
    # and host `a-b` + persona `c` mint the SAME id -- one unit, one op row.
    owner = {str(inp.get("instance_id")): str(inp.get("host_id")) for _o, inp, _s in all_rows}
    for sp in specs:
        iid = f"{host_id}-{sp.persona}"
        if owner.get(iid) not in (None, host_id):
            plan.refusals.append({
                "code": "instance-collision",
                "message": f"persona {sp.persona!r} on {host_id!r} would mint instance id "
                           f"{iid!r}, which host {owner[iid]!r} already uses -- the two "
                           "would share one unit and one op row",
            })
    if plan.refusals:
        plan.fingerprint = _plan_fingerprint(plan, store, tfc)
        return plan

    plan.dependents = _stale_dependents(drives_rec, host_id, twin, old_roles, new_roles)
    orphaned = _orphaned_hard_roles(drives_rec, host_id, twin, old_roles, new_roles)
    if orphaned and not allow_role_orphaning:
        for role_name, needers in sorted(orphaned.items()):
            plan.refusals.append({
                "code": "last-holder",
                "message": f"after this change NO host holds role {role_name!r}, which "
                           f"{len(needers)} running persona instance(s) need hard "
                           f"({', '.join(needers[:5])}{' …' if len(needers) > 5 else ''}); "
                           "they would keep talking to an address that no longer has "
                           "the role -- pass allow_role_orphaning to do it anyway",
            })
    elif orphaned:
        for role_name in sorted(orphaned):
            plan.warnings.append(
                f"role {role_name!r} is left with no holder (allow_role_orphaning)"
            )
    driven = {host_id}
    if update_dependents:
        for d in plan.dependents:
            specs.append(AssignmentSpec(
                node=d["host_id"], persona=str(d["persona"]),
                schedule=d.get("schedule"), seed=d.get("seed"),
            ))
            driven.add(d["host_id"])

    spec = TrafficProfileSpec(
        target=te_id,
        roles=[RoleAssignmentSpec(node=h, role=r) for h, r in sorted(new_roles.items())],
        assignments=specs,
    )
    if plan.refusals:
        plan.fingerprint = _plan_fingerprint(plan, store, tfc)
        return plan
    try:
        full = resolve_traffic_bindings(spec, store, manifests=manifests)
    except TrafficPlanError as e:
        plan.refusals.append({"code": _refusal_code(e.message), "message": e.message})
        plan.fingerprint = _plan_fingerprint(plan, store, tfc)
        return plan
    plan.warnings.extend(full.warnings)

    # Only this host's role op (when set), only the driven hosts' instances:
    # the role map above was handed to the resolver so peers/requires see the
    # EFFECTIVE roles, but every other host's assign-role row stays untouched.
    plan.resolution = TrafficPlanResolution(
        target_status=full.target_status, twin=full.twin,
        roles=[rr for rr in full.roles if rr.host_id == host_id and plan.role_action == "set"],
        resolved=[ra for ra in full.resolved if ra.host_id in driven],
        assignment_results=full.assignment_results, warnings=list(full.warnings),
    )

    dep_ids = {str(d["instance_id"]) for d in plan.dependents}
    new_ids: set[str] = set()
    for ra in sorted(plan.resolution.resolved, key=lambda r: (r.host_id != host_id, r.instance_id)):
        if ra.host_id == host_id:
            new_ids.add(ra.instance_id)
            rec = on_host.get(ra.instance_id)
            if rec is None:
                reason = "new"
            else:
                probe = {
                    "persona_id": ra.persona.id, "schedule": ra.schedule, "seed": ra.seed,
                    "peers": ra.peers, "peer_sets": ra.peer_sets,
                }
                reason = "changed" if _inputs_key(probe) != _inputs_key(rec[1]) else "re-assert"
        else:
            reason = "dependent" if ra.instance_id in dep_ids else "re-assert"
        plan.start.append({
            "host_id": ra.host_id, "instance_id": ra.instance_id, "persona": ra.persona.id,
            "os_family": ra.persona.os_family, "reason": reason, "peers": dict(ra.peers),
            "schedule": ra.schedule,
        })
    for iid, (op_id, inp) in sorted(live_on_host.items()):
        if iid not in new_ids:
            plan.stop.append({
                "host_id": host_id, "instance_id": iid,
                "persona": inp.get("persona_id"), "op_id": op_id,
            })
    others = [h for h in drives_rec if h not in driven]
    plan.unchanged_hosts = len(others)
    plan.unchanged_instances = sum(len(drives_rec[h]) for h in others)
    plan.fingerprint = _plan_fingerprint(plan, store, tfc)
    return plan


def _live_drive_rows(store: Any, tfc_deploy_id: Optional[str]) -> list[tuple[str, dict[str, Any], Any]]:
    """Every NON-destroyed `drive-persona` row as `(op_id, inputs, status)`."""
    from axrange.planner.opview import op_fields

    if not tfc_deploy_id:
        return []
    out = []
    for op in store.ops_for_deploy(tfc_deploy_id):
        if op.op_name != _DRIVE_PERSONA_OP or op.status == OpStatus.DESTROYED:
            continue
        _n, _s, inp, _o = op_fields(op)
        out.append((op.op_id, inp, op.status))
    return out


def _plan_fingerprint(plan: HostEditPlan, store: Any, tfc: Any) -> str:
    """sha256 over what the plan DECIDES (role, start, stop, dependents,
    refusals, the tfc deploy it lands in) AND the recorded tfc rows it was
    computed from (op id, status, completed_at, inputs) -- so a whole-profile
    apply or another host edit between preview and PUT changes it, and a
    heartbeat or a health read does not (M4). Warnings are excluded."""
    import hashlib
    import json as _json

    rows = []
    if tfc is not None:
        for op in store.ops_for_deploy(tfc.deploy_id):
            rows.append([op.op_id, op.status.value, op.completed_at, op.inputs_resolved_json])
    d = plan.to_dict()
    material = {
        k: d[k] for k in (
            "deploy_id", "host_id", "tfc_deploy_id", "creates_tfc_deploy", "role",
            "start", "stop", "dependents", "will_update_dependents", "refusals",
        )
    }
    material["rows"] = rows
    blob = _json.dumps(material, sort_keys=True, default=str).encode()
    return hashlib.sha256(blob).hexdigest()[:32]


def _orphaned_hard_roles(
    drives: dict[str, list[tuple[str, dict[str, Any]]]], host_id: str,
    twin: TwinInventory, old_roles: dict[str, str], new_roles: dict[str, str],
) -> dict[str, list[str]]:
    """S7: roles the change leaves with NO holder that running instances on
    other hosts need hard (`requires[]`, or a non-optional emit's
    `peer_role`) -> those instance ids."""
    old_hbr = _hosts_by_role(twin, overrides=old_roles)
    new_hbr = _hosts_by_role(twin, overrides=new_roles)
    gone = {r for r in old_hbr if old_hbr.get(r) and not new_hbr.get(r)}
    out: dict[str, list[str]] = {}
    if not gone:
        return out
    for h, rows in sorted(drives.items()):
        if h == host_id:
            continue
        for _op, inp in rows:
            persona = persona_registry.get(str(inp.get("persona_id") or ""))
            if persona is None:
                continue
            needs = set(persona.requires) | {
                e.peer_role for e in persona.emits
                if e.peer_role and not e.optional and e.peer_role not in RESERVED_PEER_ROLES
            }
            for r in sorted(needs & gone):
                out.setdefault(r, []).append(str(inp.get("instance_id")))
    return out


def _instance_driver(inp: dict[str, Any], adapters: Any, cfg: Any, *, node: Optional[str] = None):
    """(driver, guest agent, persona) for a persisted `drive-persona` input --
    `os_family` recovered onto BOTH the placeholder persona and the agent
    (see `_stop_instances` for why both). `adapters` is the target's
    `PlatformAdapters` (the guest-exec seam)."""
    drive = inp.get("drive")
    driver = get_driver(drive)
    osf = (inp.get("os_family") or "linux").lower()
    persona = persona_registry.get(inp.get("persona_id")) or Persona(
        id=inp.get("persona_id") or "unknown", os_family=osf, drive=drive,
    )
    ga = adapters.guest_exec(node or cfg.default_node, os_family=persona.os_family)
    return driver, ga, persona


def _heartbeat_record(fn: Callable[[], Any]) -> dict[str, Any]:
    """One driver heartbeat as a timestamped record. A failure to READ is
    `unknown` with the reason -- never `alive: false` (a heartbeat that could
    not be taken says nothing about the unit)."""
    import time as _time

    now = _time.time()
    try:
        res = fn()
    except Exception as e:  # noqa: BLE001 -- recorded, never raised
        return {"alive": None, "state": "unknown", "checked_at": now,
                "reason": f"{type(e).__name__}: {e}"}
    alive = res.get("alive") if isinstance(res, dict) else None
    if alive is None:
        return {"alive": None, "state": "unknown", "checked_at": now,
                "reason": "the driver returned no liveness answer"}
    rec: dict[str, Any] = {
        "alive": bool(alive), "state": "alive" if alive else "dead",
        "checked_at": now, "reason": res.get("note"),
    }
    if res.get("unit"):
        rec["unit"] = res["unit"]
    return rec


def heartbeat_instances(
    inps: list[dict[str, Any]], *, cfg: Any, persona_backend: Any = None,
    node: Optional[str] = None,
) -> dict[str, dict[str, Any]]:
    """`instance_id -> heartbeat record` for persisted `drive-persona`
    inputs, via each instance's driver `heartbeat()` over QGA. Test seam: a
    backend with `heartbeat_instance(vmid, drive, instance_id)`. A foreign
    backend that has neither is reported `unknown`, never guessed."""
    out: dict[str, dict[str, Any]] = {}
    if persona_backend is not None and hasattr(persona_backend, "heartbeat_instance"):
        for inp in inps:
            iid = str(inp.get("instance_id") or "")
            out[iid] = _heartbeat_record(
                lambda inp=inp, iid=iid: persona_backend.heartbeat_instance(
                    inp.get("vmid"), inp.get("drive"), iid,
                )
            )
        return out
    if persona_backend is not None and not isinstance(persona_backend, PersonaBackend):
        import time as _time

        for inp in inps:
            out[str(inp.get("instance_id") or "")] = {
                "alive": None, "state": "unknown", "checked_at": _time.time(),
                "reason": "this persona backend cannot read a heartbeat",
            }
        return out
    from axrange.platform import DEFAULT_PLATFORM, adapter_for

    adapters = adapter_for(DEFAULT_PLATFORM, cfg)
    for inp in inps:
        iid = str(inp.get("instance_id") or "")

        def _one(inp: dict[str, Any] = inp, iid: str = iid) -> Any:
            driver, ga, persona = _instance_driver(inp, adapters, cfg, node=node)
            return driver.heartbeat(ga, int(inp["vmid"]), persona, instance_id=iid)

        out[iid] = _heartbeat_record(_one)
    return out


def apply_host_personas(
    *,
    db_path: str,
    target: str,
    host_id: str,
    assignments: list[HostPersonaAssignment],
    role: Optional[str] = None,
    update_dependents: bool = False,
    adapter_config: Optional[str] = None,
    target_node: Optional[str] = None,
    persona_backend: Any = None,
    manifests: Any = None,
    allow_uncontained: bool = False,
    allow_role_orphaning: bool = False,
    expect_plan: Optional[str] = None,
    containment_ga: Any = None,
    background: Optional[Callable[[Callable[[], None]], None]] = None,
    log: Callable[[str], None] = lambda _m: None,
) -> HostEditResult:
    """Apply ONE host's persona set (and optionally its role) on a standing
    twin -- see the section comment above for the guarantees.

    Synchronously, in this order (code review M3: the LEASE comes first, so
    the plan that runs is the plan computed while nobody else can write):
    `tfc-<te>` lease (409 when a whole-profile apply or another host edit
    holds it) -> plan under the lease (refusal -> 400 with
    `detail.refusals`; `expect_plan` mismatch -> 409 with the fresh plan,
    M4) -> containment gate, only when something STARTS (S1; 409 unless
    `allow_uncontained`) -> create the tfc deploy when the twin has none ->
    force-reset exactly the ops about to run -> a `running` job record on
    the rows it will touch. Then the executor run -- inline (CLI) or handed
    to `background` (the API, which returns at once and is polled).

    Everything the run learns is PERSISTED (M2): the job record
    (`outputs.host_edit` on every row it touched -- status, the executed
    plan and its fingerprint, diagnostics), per-row `stop_error` when a stop
    raised or the post-stop heartbeat is not `dead` (the row is then NOT
    retired), and the post-start heartbeat. Afterwards the tfc deploy's
    status is recomputed over ALL its non-destroyed rows (S3)."""
    import time as _time

    from axrange.o2control.containment import NodeResolvingGuestAgent, containment_verdict
    from axrange.platform import load_config
    from axrange.planner import StateStore
    from axrange.planner.state import DeployStatus, default_process_info

    store = StateStore(db_path)
    try:
        te_id = resolve_traffic_target(store, target)
        plan_id = f"tfc-{te_id}"
        existing = store.find_deploy_for_plan(plan_id, exclude_destroyed=True)
        deploy_id = (
            existing.deploy_id if existing is not None
            else f"{plan_id}-{secrets.token_hex(4)}"
        )
        lease = store.acquire_lease(plan_id, deploy_id, process_info=default_process_info())
        if not lease.acquired:
            raise TrafficPlanError(
                f"another traffic-profile operation for twin {te_id!r} is in "
                f"progress (deploy_id={lease.held_by_deploy_id})", status=409,
                detail={"held_by_deploy_id": lease.held_by_deploy_id},
            )
        try:
            plan = plan_host_personas(
                store, te_id, host_id, assignments, role=role,
                update_dependents=update_dependents, manifests=manifests,
                allow_role_orphaning=allow_role_orphaning,
            )
            if expect_plan is not None and expect_plan != plan.fingerprint:
                raise TrafficPlanError(
                    f"the plan for {host_id!r} changed since it was previewed (another "
                    "apply landed in between) -- review the fresh plan and confirm again",
                    status=409, detail={"code": "plan-changed", "plan": plan.to_dict()},
                )
            if not plan.ok:
                raise TrafficPlanError(
                    f"per-host persona apply on {host_id!r} refused: "
                    + "; ".join(r["message"] for r in plan.refusals),
                    status=400, detail={"refusals": plan.refusals, "plan": plan.to_dict()},
                )
            assert plan.resolution is not None
            cfg = load_config(adapter_config)
            if target_node:
                cfg.default_node = target_node
            if not cfg.default_node:
                raise TrafficPlanError(
                    "DEFAULT_NODE missing; set it in adapter.conf, or override via "
                    "target_node", status=400,
                )
            diagnostics: list[dict[str, str]] = [
                {"level": "warning", "message": w} for w in plan.warnings
            ]
            if plan.start:
                # S1: a stop-only edit reduces egress; it is never gated.
                verdict = containment_verdict(
                    store, te_id, ga=containment_ga,
                    ga_factory=lambda: NodeResolvingGuestAgent(cfg, cfg.default_node),
                    timeout_s=60,
                )
                if verdict is not None and not verdict.contained:
                    msg = (f"twin {te_id} is NOT contained: {verdict.why}; real internet "
                           f"egress is possible. Contain it first (`axrange te egress {te_id} "
                           "--phase measure`), or pass allow_uncontained")
                    if not allow_uncontained:
                        raise TrafficPlanError(
                            msg, status=409,
                            detail={"refusals": [{"code": "containment", "message": msg}]},
                        )
                    diagnostics.append({
                        "level": "warning",
                        "message": f"proceeding uncontained (allow_uncontained): {verdict.why}",
                    })

            sub_plan = compose_traffic_ops(
                plan.resolution, plan_id, origins={host_id: HOST_EDIT_ORIGIN},
            )
            if existing is not None:
                store.update_deploy_status(deploy_id, DeployStatus.RUNNING)
            else:
                store.create_deploy(
                    deploy_id=deploy_id, scenario_id=plan_id, scenario_version=None,
                    scenario_schema_version=1, plan_id=plan_id, ir_hash="",
                    placement={}, tier="tfc",
                )
            plan.tfc_deploy_id = deploy_id
            # EXACTLY the ops about to run -- never `reset_incomplete_ops`,
            # which would flip another host's failed/halted row to pending.
            store.force_reset_ops(deploy_id, [op.op_id for op in sub_plan.ops.values()])
            # Rows exist from here on, so the job record has anchors even if
            # the run crashes before the executor persists anything.
            from axrange.planner.state import op_to_row

            store.insert_ops(deploy_id, [op_to_row(op, deploy_id) for op in sub_plan.ops.values()])
            job = {
                "job_id": secrets.token_hex(6), "host_id": host_id, "status": "running",
                "started_at": _time.time(), "finished_at": None,
                "fingerprint": plan.fingerprint, "plan": plan.to_dict(),
                "diagnostics": list(diagnostics),
            }
            anchors = _job_anchors(store, deploy_id, sub_plan, plan)
            _record_job(store, deploy_id, anchors, job)
        except BaseException:
            store.release_lease(plan_id, deploy_id)
            raise
    finally:
        store.close()

    overlay_now = persona_registry.current_overlay()
    outcome: dict[str, Any] = {"status": "running"}

    def _job() -> None:
        # A thread does not inherit the request's persona overlay: re-enter it.
        with persona_registry.overlay(overlay_now):
            outcome["status"] = _run_host_edit(
                db_path=db_path, deploy_id=deploy_id, plan_id=plan_id,
                sub_plan=sub_plan, plan=plan, cfg=cfg, job=job, anchors=anchors,
                persona_backend=persona_backend, log=log,
            )

    if background is not None:
        try:
            background(_job)
        except BaseException as e:
            # The job never started: release the lease and say so in the
            # store, rather than leaking the lease until it goes stale.
            _abandon_job(db_path, deploy_id, plan_id, anchors, job,
                         f"the background job could not be started: {type(e).__name__}: {e}")
            raise TrafficPlanError(
                f"per-host apply on {host_id!r} could not start: {e}", status=500,
            ) from e
        return HostEditResult(
            te_deploy_id=te_id, host_id=host_id, tfc_deploy_id=deploy_id,
            status=outcome["status"], detached=outcome["status"] == "running",
            plan=plan, warnings=[d["message"] for d in diagnostics],
        )
    _job()
    return HostEditResult(
        te_deploy_id=te_id, host_id=host_id, tfc_deploy_id=deploy_id,
        status=outcome["status"], detached=False, plan=plan,
        warnings=[d["message"] for d in job["diagnostics"]],
    )


def _job_anchors(store: Any, deploy_id: str, sub_plan: Plan, plan: HostEditPlan) -> list[str]:
    """The op rows a host-edit job record is written onto: every op it runs,
    every row it stops, and the host's role row."""
    ids = [op.op_id for op in sub_plan.ops.values()]
    ids += [str(s["op_id"]) for s in plan.stop if s.get("op_id")]
    if plan.role_action == "clear":
        from axrange.planner.opview import op_fields

        for op in store.ops_for_deploy(deploy_id):
            if op.op_name == _ASSIGN_ROLE_OP:
                _n, scope, inp, _o = op_fields(op)
                if (scope.get("host") or inp.get("host_id")) == plan.host_id:
                    ids.append(op.op_id)
    return list(dict.fromkeys(ids))


def job_records(outs: Any) -> list[dict[str, Any]]:
    """Every host-edit job record on one op row's outputs: the current
    `host_edit` plus the append-only `host_edit_history` (fix round 4)."""
    if not isinstance(outs, dict):
        return []
    out = [j for j in (outs.get("host_edit_history") or []) if isinstance(j, dict)]
    cur = outs.get("host_edit")
    if isinstance(cur, dict):
        out.append(cur)
    return [j for j in out if j.get("job_id")]


def _better_job(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """Of two copies of one job, the more final: superseded > finished >
    running."""
    rank = lambda j: (bool(j.get("superseded")), bool(j.get("finished_at")))  # noqa: E731
    return b if rank(b) > rank(a) else a


def _merge_jobs(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for group in groups:
        for j in group:
            cur = by_id.get(j["job_id"])
            by_id[j["job_id"]] = j if cur is None else _better_job(cur, j)
    return sorted(by_id.values(), key=lambda j: j.get("started_at") or 0)


def _write_jobs(store: Any, deploy_id: str, op_id: str, jobs: list[dict[str, Any]]) -> None:
    if not jobs:
        return
    try:
        store.merge_op_outputs(deploy_id, op_id, {
            "host_edit": jobs[-1], "host_edit_history": jobs,
        })
    except ValueError:  # a corrupt outputs record is refused, never coerced
        pass


def _record_job(store: Any, deploy_id: str, anchors: list[str], job: dict[str, Any]) -> None:
    """Write `job` onto each anchor row -- as `host_edit` AND into the row's
    append-only `host_edit_history`, keeping every earlier job there."""
    from axrange.planner.opview import op_fields

    for op_id in anchors:
        row = store.get_op(deploy_id, op_id)
        prior = job_records(op_fields(row)[3]) if row is not None else []
        _write_jobs(store, deploy_id, op_id, _merge_jobs(prior, [job]))


def _snapshot_jobs(store: Any, deploy_id: str) -> dict[str, list[dict[str, Any]]]:
    """op_id -> its job records, taken BEFORE an executor run: `complete_op`
    replaces a re-driven row's outputs wholesale, which would erase them
    (fix round 4, D2). `_restore_jobs` puts them back after the run."""
    from axrange.planner.opview import op_fields

    out: dict[str, list[dict[str, Any]]] = {}
    for op in store.ops_for_deploy(deploy_id):
        jobs = job_records(op_fields(op)[3])
        if jobs:
            out[op.op_id] = jobs
    return out


def _restore_jobs(store: Any, deploy_id: str, snap: dict[str, list[dict[str, Any]]]) -> None:
    from axrange.planner.opview import op_fields

    for op_id, jobs in snap.items():
        row = store.get_op(deploy_id, op_id)
        if row is None:
            continue
        current = job_records(op_fields(row)[3])
        merged = _merge_jobs(jobs, current)
        if [j.get("job_id") for j in merged] != [j.get("job_id") for j in current] or (
            merged != current
        ):
            _write_jobs(store, deploy_id, op_id, merged)


def _supersede_jobs(store: Any, deploy_id: str, at: float) -> None:
    """D3: a successful WHOLE-profile apply supersedes every host-edit job
    before it -- each record keeps its history but says `superseded: true`
    with the apply's time, so `/hosts` never shows it as the live state."""
    from axrange.planner.opview import op_fields

    for op in store.ops_for_deploy(deploy_id):
        jobs = job_records(op_fields(op)[3])
        if not jobs:
            continue
        changed = False
        for j in jobs:
            if not j.get("superseded") and (j.get("started_at") or 0) <= at:
                j["superseded"] = True
                j["superseded_at"] = at
                changed = True
        if changed:
            _write_jobs(store, deploy_id, op.op_id, _merge_jobs(jobs))


def _abandon_job(db_path: str, deploy_id: str, plan_id: str, anchors: list[str],
                 job: dict[str, Any], why: str) -> None:
    import time as _time

    from axrange.planner import StateStore
    from axrange.planner.state import DeployStatus

    store = StateStore(db_path)
    try:
        job.update(status="failed", finished_at=_time.time())
        job["diagnostics"] = list(job["diagnostics"]) + [{"level": "error", "message": why}]
        _record_job(store, deploy_id, anchors, job)
        _recompute_tfc_status(store, deploy_id, fallback=DeployStatus.FAILED)
        store.release_lease(plan_id, deploy_id)
    finally:
        store.close()


def _recompute_tfc_status(store: Any, deploy_id: str, *, fallback: Any = None) -> Any:
    """S3: the tfc deploy's status over ALL its non-destroyed rows -- the
    executor only saw a host edit's sub-plan, so its verdict says nothing
    about the other hosts' rows. `completed` iff every live row is."""
    from axrange.planner.state import DeployStatus

    live = [op.status for op in store.ops_for_deploy(deploy_id)
            if op.status != OpStatus.DESTROYED]
    if live and all(st == OpStatus.COMPLETED for st in live):
        status = DeployStatus.COMPLETED
    elif live:
        status = DeployStatus.FAILED
    else:
        status = fallback or DeployStatus.COMPLETED
    store.update_deploy_status(deploy_id, status)
    return status


def _run_host_edit(
    *, db_path: str, deploy_id: str, plan_id: str, sub_plan: Plan, plan: HostEditPlan,
    cfg: Any, job: dict[str, Any], anchors: list[str], persona_backend: Any,
    log: Callable[[str], None],
) -> str:
    """The executor half of `apply_host_personas` (its own StateStore: it may
    run on a worker thread). Always releases the lease and always writes the
    final job record; an unexpected crash is recorded as an `error`
    diagnostic, never only logged (M2)."""
    import time as _time

    from axrange.planner.entry import release_deploy_resources, run_deploy_executor
    from axrange.planner import StateStore
    from axrange.planner.cluster import ClusterContext
    from axrange.planner.locks import Heartbeat

    host_id = plan.host_id
    diags: list[dict[str, str]] = list(job["diagnostics"])

    def _note(level: str, message: str) -> None:
        diags.append({"level": level, "message": message})
        log(f"per-host apply {deploy_id}/{host_id}: {message}")

    store = StateStore(db_path)
    heartbeat = Heartbeat(str(db_path), deploy_id=deploy_id, scenario_id=plan_id)
    status = "failed"
    try:
        try:
            backend = persona_backend or PersonaBackend(cfg)
            ctx = ClusterContext.single_node(cfg.default_node)
            jobs_before = _snapshot_jobs(store, deploy_id)
            try:
                result = run_deploy_executor(
                    store, backend, backend, sub_plan, ctx, deploy_id, heartbeat,
                )
            finally:
                _restore_jobs(store, deploy_id, jobs_before)  # D2
            if not result.succeeded:
                _note("error", "one or more persona ops failed (see the op rows' attempts)")
            else:
                assert plan.resolution is not None
                live = {ra.instance_id for ra in plan.resolution.resolved if ra.host_id == host_id}
                descoped = _stop_dropped_instances(
                    store, deploy_id, live, cfg=cfg, persona_backend=backend,
                    log=lambda m: None, host_ids={host_id},
                    mark={"descoped_by": HOST_EDIT_ORIGIN}, verify_stop=True,
                )
                for iid in descoped["retired"]:
                    _note("info", f"stopped {iid}")
                for iid, why in sorted(descoped["kept"].items()):
                    _note("error", f"{iid} was NOT stopped: {why}")
                if plan.role_action == "clear":
                    _retire_dropped_roles(
                        store, deploy_id, set(), host_ids={host_id},
                        mark={"descoped_by": HOST_EDIT_ORIGIN},
                        log=lambda m: _note("info", m.strip()),
                    )
            # "Started" is not "running": one heartbeat per instance this run
            # actually started, recorded on its own row.
            started = []
            for op in sub_plan.ops.values():
                if op.op_name != _DRIVE_PERSONA_OP:
                    continue
                row = store.get_op(deploy_id, op.op_id)
                if row is not None and row.status == OpStatus.COMPLETED:
                    started.append((op.op_id, dict(op.inputs_resolved)))
            beats = heartbeat_instances(
                [inp for _id, inp in started], cfg=cfg, persona_backend=backend,
            )
            for op_id, inp in started:
                iid = str(inp.get("instance_id"))
                rec = beats.get(iid)
                if rec is not None:
                    store.merge_op_outputs(deploy_id, op_id, {"heartbeat": rec})
                    if rec.get("state") != "alive":
                        _note("warning", f"{iid} started but its heartbeat reads "
                                         f"{rec.get('state')}"
                                         + (f" ({rec['reason']})" if rec.get("reason") else ""))
            status = "completed" if result.succeeded and not any(
                d["level"] == "error" for d in diags
            ) else "failed"
        except Exception as e:  # noqa: BLE001 -- recorded, never left `running`
            status = "failed"
            _note("error", f"per-host apply crashed: {type(e).__name__}: {e}")
        try:
            _recompute_tfc_status(store, deploy_id)
        except Exception as e:  # noqa: BLE001
            _note("error", f"could not recompute the tfc deploy status: {e}")
        job.update(status=status, finished_at=_time.time(), diagnostics=diags)
        try:
            _record_job(store, deploy_id, anchors, job)
        except Exception as e:  # noqa: BLE001 -- last resort: the log
            log(f"per-host apply {deploy_id}/{host_id}: could not record the job: {e}")
    finally:
        release_deploy_resources(store, plan_id, deploy_id, heartbeat)
        store.close()
    return status


def host_edit_jobs(store: Any, tfc_deploy_id: str) -> list[dict[str, Any]]:
    """Every host-edit job record on a tfc deploy (deduplicated by
    `job_id`, newest first) -- what `GET /api/deploys/{tfc}` exposes."""
    from axrange.planner.opview import op_fields

    groups = [job_records(op_fields(op)[3]) for op in store.ops_for_deploy(tfc_deploy_id)]
    # One copy per job; the most final copy wins (superseded > finished >
    # running) -- a row the run did not finish on keeps the `running` copy.
    return list(reversed(_merge_jobs(*groups)))


# ---------------------------------------------------------------------------
# Host-edit bookkeeping for the WHOLE-profile apply + the profile export.
# ---------------------------------------------------------------------------
#: What a host edit's retired-row marker becomes once a whole-profile apply
#: has superseded it (S5) -- anything but `host-edit` no longer counts.
HOST_EDIT_SUPERSEDED = "host-edit-superseded"


def _supersede_host_edit_markers(store: Any, tfc_deploy_id: str) -> None:
    from axrange.planner.opview import op_fields

    for op in store.ops_for_deploy(tfc_deploy_id):
        if op.status != OpStatus.DESTROYED:
            continue
        _n, _s, _i, outs = op_fields(op)
        if (outs or {}).get("descoped_by") == HOST_EDIT_ORIGIN:
            store.merge_op_outputs(tfc_deploy_id, op.op_id, {"descoped_by": HOST_EDIT_SUPERSEDED})


def host_edited_hosts(store: Any, tfc_deploy_id: str) -> set[str]:
    """Hosts carrying a per-host edit: a live row stamped `origin: host-edit`,
    or a row a host edit retired (`outputs.descoped_by: host-edit`)."""
    from axrange.planner.opview import op_fields

    out: set[str] = set()
    for op in store.ops_for_deploy(tfc_deploy_id):
        if op.op_name not in (_DRIVE_PERSONA_OP, _ASSIGN_ROLE_OP):
            continue
        _n, scope, inp, outs = op_fields(op)
        h = inp.get("host_id") or scope.get("host")
        if not h:
            continue
        if op.status == OpStatus.COMPLETED and inp.get("origin") == HOST_EDIT_ORIGIN:
            out.add(str(h))
        elif op.status == OpStatus.DESTROYED and (outs or {}).get("descoped_by") == HOST_EDIT_ORIGIN:
            out.add(str(h))
    return out


def host_edit_reverts(
    store: Any, tfc_deploy_id: str, resolution: TrafficPlanResolution,
) -> list[dict[str, Any]]:
    """The host edits a whole-profile `resolution` would undo: for each
    host-edited host, its running persona set (persona, schedule, seed) or
    its tfc role differs from what the profile resolves for it."""
    import json as _json

    edited = host_edited_hosts(store, tfc_deploy_id)
    if not edited:
        return []
    roles_rec, drives_rec = _recorded_tfc_state(store, tfc_deploy_id)

    def _entry(persona: Any, schedule: Any, seed: Any) -> dict[str, Any]:
        return {"persona": persona, "schedule": schedule, "seed": seed}

    def _key(e: dict[str, Any]) -> str:
        return _json.dumps(e, sort_keys=True, default=str)

    out: list[dict[str, Any]] = []
    for h in sorted(edited):
        cur = sorted(
            (_entry(inp.get("persona_id"), inp.get("schedule"), inp.get("seed"))
             for _op, inp in drives_rec.get(h, [])), key=_key,
        )
        new = sorted(
            (_entry(ra.persona.id, ra.schedule, ra.seed)
             for ra in resolution.resolved if ra.host_id == h), key=_key,
        )
        role_cur = roles_rec[h][0] if h in roles_rec else None
        role_new = next((rr.role for rr in resolution.roles if rr.host_id == h), None)
        if [_key(e) for e in cur] != [_key(e) for e in new] or role_cur != role_new:
            out.append({
                "host_id": h,
                "current": [e["persona"] for e in cur], "profile": [e["persona"] for e in new],
                "current_detail": cur, "profile_detail": new,
                "role_current": role_cur, "role_profile": role_new,
            })
    return out


def export_traffic_profile(
    store: Any, tfc_deploy_id: str, *, target: str,
) -> dict[str, Any]:
    """The tfc deploy's RUNNING state (completed rows) as a traffic-profile
    document -- `{"yaml", "instances", "host_edits", "notes"}`. Every entry
    is `node:`-addressed (the running state is per host; re-deriving role
    groupings would be a guess). `target` should be the twin NAME so the
    file is reusable (LES-072). Replay bindings cannot be expressed per
    node and are listed in `notes` instead."""
    import io

    from ruamel.yaml import YAML

    roles_rec, drives_rec = _recorded_tfc_state(store, tfc_deploy_id)
    roles = [{"node": h, "role": r} for h, (r, _op, _inp) in sorted(roles_rec.items())]
    assignments: list[dict[str, Any]] = []
    notes: list[str] = []
    count = 0
    for h in sorted(drives_rec):
        for _op, inp in drives_rec[h]:
            count += 1
            if inp.get("drive") == "replay":
                notes.append(
                    f"replay binding {inp.get('template_pcap_path')!r} on {h} "
                    f"(orig_ip {inp.get('orig_ip')}) is not exported: a replay "
                    "binds by onto_role, not by node"
                )
                continue
            entry: dict[str, Any] = {"node": h, "persona": inp.get("persona_id")}
            if inp.get("schedule"):
                entry["schedule"] = inp["schedule"]
            if inp.get("seed") is not None:
                entry["seed"] = inp["seed"]
            assignments.append(entry)
    body: dict[str, Any] = {"target": target}
    if roles:
        body["roles"] = roles
    body["assignments"] = assignments
    # Round-trip dumper: keeps `target`, `roles`, `assignments` in the order
    # a hand-written profile has them (the safe dumper sorts keys).
    y = YAML()
    y.default_flow_style = False
    buf = io.StringIO()
    y.dump({"traffic-profile": body}, buf)
    header = (
        f"# Exported from tfc deploy {tfc_deploy_id} (running state, completed rows).\n"
        "# Save deliberately to twins/<id>/tfc/ -- applying it as-is re-asserts every host.\n"
    )
    return {
        "yaml": header + buf.getvalue(),
        "instances": count,
        "host_edits": sorted(host_edited_hosts(store, tfc_deploy_id)),
        "notes": notes,
    }


# ---------------------------------------------------------------------------
# Destroy -- `tfc_`-scoped ONLY. `naming.TIER_SHAPE_CHECKS`/`planner.
# teardown._protected()` already refuse every foreign-tier name a `tfc`
# deploy's own ops could ever produce, so this deploy's `destroy_deploy`
# call structurally cannot touch the target twin's (or any other tier's)
# resources -- it only ever sees this deploy's OWN `drive-persona` rows
# (which create no Proxmox resource at all: no clone-vm/create-vnet/
# create-sdn-zone/gretap, so the sweep is a clean no-op resource-wise; the
# ACTUAL work of stopping each persona instance happens in the pre-pass
# below, exactly the shape `taps.py`'s S2 de-scope pass takes for mirrors).
# ---------------------------------------------------------------------------
def _stop_instances(
    inps: list[dict[str, Any]], *, cfg: Any, persona_backend: Any,
    log: Callable[[str], None],
) -> dict[str, str]:
    """Stop a list of ALREADY-EXTRACTED persisted `drive-persona` inputs
    (vmid/drive/instance_id/persona_id each) via their drivers. Best-effort:
    a failure logs, never raises -- shared by BOTH the full de-scope pass on
    destroy (`_stop_persisted_instances`) and the drop-diff pass on a
    narrowing apply (`_stop_dropped_instances`), factored out here (code
    review, LES-037) rather than duplicated across the two call sites.

    **`os_family` must be recovered and threaded through BOTH the placeholder
    `Persona` AND the `GuestAgent`** (review fix, 2026-07-25 Windows persona
    port): a Windows persona instance's `ScriptedDriver.stop` dispatches on
    `persona.os_family` to reach `stop_scheduled_task` (`ga.shell_exec`,
    PowerShell) -- a `GuestAgent` built with the default `os_family="linux"`
    still renders `sh -lc` for that call regardless of what the placeholder
    `Persona` says, so BOTH must carry the recovered value or the "stop"
    silently no-ops against a Windows guest (LES-001: reports `stopped` while
    the Scheduled Task keeps running and survives reboots). Mirrors
    `exercise.py`'s `os_family` persist (`compose_exercise_ops`) + read-back
    (`_run_exercise_op`) shape for the sibling `exr` tier.

    Returns `instance_id -> error` for every stop that RAISED (still logged
    too), so a caller that must not retire a row whose stop failed -- the
    per-host apply (code review M2) -- can tell."""
    failed: dict[str, str] = {}
    if persona_backend is not None and hasattr(persona_backend, "stop_instance"):
        # Test seam: a fake backend can implement `stop_instance(vmid,
        # drive, instance_id)` directly, without a real GuestAgent.
        for inp in inps:
            instance_id = inp.get("instance_id")
            try:
                persona_backend.stop_instance(
                    inp.get("vmid"), inp.get("drive"), instance_id,
                )
                log(f"  - persona {instance_id}: stopped")
            except Exception as e:  # noqa: BLE001 -- best-effort, see docstring
                log(f"  ! persona {instance_id}: stop failed: {e}")
                failed[str(instance_id)] = f"{type(e).__name__}: {e}"
        return failed

    from axrange.platform import DEFAULT_PLATFORM, adapter_for

    adapters = adapter_for(DEFAULT_PLATFORM, cfg)
    for inp in inps:
        instance_id = inp.get("instance_id")
        vmid = inp.get("vmid")
        drive = inp.get("drive")
        if not instance_id or vmid is None or not drive:
            continue
        try:
            # One construction of (driver, agent, persona) shared with the
            # heartbeat read (`_instance_driver`, LES-037).
            driver, ga, persona = _instance_driver(inp, adapters, cfg)
            driver.stop(ga, int(vmid), persona, instance_id=instance_id)
            log(f"  - persona {instance_id}: stopped")
        except Exception as e:  # noqa: BLE001 -- best-effort, see docstring
            log(f"  ! persona {instance_id}: stop failed: {e}")
            failed[str(instance_id)] = f"{type(e).__name__}: {e}"
    return failed


def _stop_persisted_instances(
    store: Any, deploy_id: str, *, cfg: Any, persona_backend: Any,
    log: Callable[[str], None],
) -> None:
    """Best-effort: stop EVERY persona instance this tfc deploy ever drove
    (used at full destroy time), reading them back from the persisted
    `drive-persona` ops (the SAME "derive purely from state.db" posture
    `taps.py`'s de-scope pass and `axrange.planner.inventory` both use). A
    failure here (e.g. the twin's VM is already gone -- exactly what
    happens when this runs via the S3 cascade AFTER the twin's own destroy)
    must never block the deploy-state transition that follows."""
    from axrange.planner.opview import op_fields

    inps = []
    for op in store.ops_for_deploy(deploy_id):
        if op.op_name != _DRIVE_PERSONA_OP:
            continue
        _op_name, _scope, inp, _out = op_fields(op)
        inps.append(inp)
    _stop_instances(inps, cfg=cfg, persona_backend=persona_backend, log=log)


def _stop_dropped_instances(
    store: Any, deploy_id: str, live_instance_ids: set[str], *, cfg: Any,
    persona_backend: Any, log: Callable[[str], None],
    host_ids: Optional[set[str]] = None, mark: Optional[dict[str, Any]] = None,
    verify_stop: bool = False,
) -> dict[str, Any]:
    """MUST-FIX (code review, LES-112): the apply-time counterpart to
    `_stop_persisted_instances`'s destroy-time de-scope pass -- stopping a
    persona is a SEPARATE obligation from re-asserting one, and force-reset
    (LES-057) only re-runs ops that are IN THE NEW PLAN. Without this, an
    instance the new profile no longer contains just keeps running forever
    (only a full `tfc destroy` would ever stop it) -- and worse, a
    PERSONA SWAP on the same host (`instance_id` is `f"{host_id}-
    {persona_id}"`, so changing the persona changes the systemd unit NAME)
    leaves the OLD persona's unit running ALONGSIDE the newly-started one --
    both emitting simultaneously, silently corrupting the very profile just
    applied.

    Reads back every previously-`completed` `drive-persona` op NOT in
    `live_instance_ids` (this apply's new resolved set), stops each via its
    driver, and marks its op row `destroyed` (`StateStore.
    mark_op_destroyed`) so `_assignments_summary_from_ops`/`
    traffic_info_from_deploy` -- which filter to `status == COMPLETED` --
    stop reporting it as an active assignment. Best-effort per instance,
    same posture as `_stop_persisted_instances`; called only after a
    successful executor run (mirrors `taps.py`'s `_delete_dropped_gretaps`:
    the new state must land first).

    `host_ids` (the per-host apply, `apply_host_personas`) restricts the
    de-scope to instances ON those hosts: a host edit's live set names only
    its own host's instances, so an unrestricted pass would stop every other
    persona on the twin. `mark` is merged into each dropped row's outputs
    before it is retired (`{"descoped_by": "host-edit"}`), which is how a
    later whole-profile apply knows a removal was a host edit. `None` for
    both is the whole-profile behaviour.

    **Non-completed rows too (S6, LES-113):** a `failed`/`pending`/`halted`
    row absent from the new plan is a half-started (or never-started) unit
    that nothing would ever stop or retire otherwise; it is stopped
    best-effort and retired like a completed one.

    `verify_stop` (the per-host apply, code review M2): a row is retired ONLY
    when its stop did not raise AND a post-stop heartbeat reads `dead`;
    otherwise it stays as it was and gets `outputs.stop_error` -- the unit
    may still be running, and saying "stopped" would be LES-001's lie.

    Returns `{"retired": [instance ids], "kept": {instance id: reason}}`."""
    import time as _time

    from axrange.planner.opview import op_fields

    dropped: list[tuple[str, dict[str, Any]]] = []  # (op_id, inputs_resolved)
    for op in store.ops_for_deploy(deploy_id):
        if op.op_name != _DRIVE_PERSONA_OP or op.status == OpStatus.DESTROYED:
            continue
        _op_name, scope, inp, _out = op_fields(op)
        if host_ids is not None and inp.get("host_id") not in host_ids:
            continue
        instance_id = scope.get("instance") or inp.get("instance_id")
        if instance_id and instance_id not in live_instance_ids:
            dropped.append((op.op_id, inp))
    out: dict[str, Any] = {"retired": [], "kept": {}}
    if not dropped:
        return out
    failed = _stop_instances(
        [inp for _op_id, inp in dropped], cfg=cfg, persona_backend=persona_backend, log=log,
    )
    beats: dict[str, dict[str, Any]] = {}
    if verify_stop:
        beats = heartbeat_instances(
            [inp for _op, inp in dropped if str(inp.get("instance_id")) not in failed],
            cfg=cfg, persona_backend=persona_backend,
        )
    for op_id, inp in dropped:
        iid = str(inp.get("instance_id") or "")
        if verify_stop:
            beat = beats.get(iid)
            why = failed.get(iid)
            if why is None and (beat or {}).get("state") != "dead":
                why = (f"post-stop heartbeat reads {(beat or {}).get('state', 'unknown')}"
                       + (f" ({beat.get('reason')})" if beat and beat.get("reason") else ""))
            if why is not None:
                try:
                    store.merge_op_outputs(deploy_id, op_id, {"stop_error": {
                        "message": why, "at": _time.time(), "heartbeat": beat,
                    }})
                except ValueError as e:
                    log(f"  ! could not record the stop failure on {op_id}: {e}")
                log(f"  ! persona {iid}: NOT retired -- {why}")
                out["kept"][iid] = why
                continue
        if mark:
            try:
                store.merge_op_outputs(deploy_id, op_id, dict(mark))
            except ValueError as e:  # a corrupt outputs record: never coerced
                log(f"  ! could not mark {op_id} {mark}: {e}")
        store.mark_op_destroyed(deploy_id, op_id)
        out["retired"].append(iid)
    return out


def _retire_dropped_roles(
    store: Any, deploy_id: str, live_host_ids: set[str], *, log: Callable[[str], None],
    host_ids: Optional[set[str]] = None, mark: Optional[dict[str, Any]] = None,
) -> None:
    """The `assign-role` counterpart to `_stop_dropped_instances`.

    A host dropped from this apply's `roles:` block must REVERT to its twin
    baseline, which means its `assign-role` op row has to stop being
    `completed` -- `axrange.planner.inventory` filters positively to
    `status == COMPLETED` when it prefers a tfc role over the baseline
    (LES-113), so a row left `completed` would pin a stale role on that host
    forever. This is the GAP-045 failure shape (a de-scoped binding reported
    live forever because its op row was never retired), pre-empted here
    rather than rediscovered.

    Unconditional, like `_delete_dropped_gretaps`' own mark-destroyed pass:
    there is no cluster-side state to reconcile first (this op touches
    nothing), so retiring the row IS the whole de-scope.

    `host_ids`/`mark`: the per-host apply's `role: ""` retires only its own
    host's row, marked `descoped_by: host-edit` (one loop, S10)."""
    from axrange.planner.opview import op_fields

    dropped: list[tuple[str, str]] = []  # (op_id, host_id)
    for op in store.ops_for_deploy(deploy_id):
        if op.op_name != _ASSIGN_ROLE_OP or op.status != OpStatus.COMPLETED:
            continue
        _op_name, scope, inp, _out = op_fields(op)
        host_id = scope.get("host") or inp.get("host_id")
        if host_ids is not None and host_id not in host_ids:
            continue
        if host_id and host_id not in live_host_ids:
            dropped.append((op.op_id, host_id))
    for op_id, host_id in dropped:
        if mark:
            store.merge_op_outputs(deploy_id, op_id, dict(mark))
        store.mark_op_destroyed(deploy_id, op_id)
        log(f"  role de-scoped: {host_id} reverts to its twin baseline role")


def destroy_traffic_profile(
    *,
    db_path: str,
    target: Optional[str] = None,
    deploy_id: Optional[str] = None,
    adapter_config: Optional[str] = None,
    node: Optional[str] = None,
    teardown_backend: Any = None,
    persona_backend: Any = None,
    log: Callable[[str], None] = lambda _m: None,
) -> Any:
    """Tear down a traffic-profile deploy: first a best-effort pass stopping
    every persisted persona instance (`_stop_persisted_instances`), THEN the
    engine's OWN teardown (`planner.destroy_deploy`) against this deploy --
    reused verbatim, not reimplemented. Leaves the target twin (and every
    other tier) completely untouched: this deploy's `ops` table carries only
    `drive-persona` rows, none of which name a Proxmox resource the teardown
    orchestrator's blast-radius guard would even recognize.

    Returns the `planner.teardown.TeardownResult` unchanged (the CLI/API
    surfaces map it, same as `axrange tap destroy` / `axrange o2 destroy`).
    """
    from axrange.planner import StateStore, TeardownError, destroy_deploy
    from axrange.planner.locks import Heartbeat
    from axrange.planner.state import default_process_info
    from axrange.platform import DEFAULT_PLATFORM, adapter_for, load_config

    store = StateStore(db_path)
    try:
        did = deploy_id
        if did is None:
            if target is None:
                raise TrafficPlanError(
                    "destroy needs target= or deploy_id=", status=400
                )
            # Resolve a scenario/twin-config NAME to the concrete te deploy id
            # the `plan_id` was actually keyed off -- `apply_traffic_profile`
            # resolves identically BEFORE building `plan_id = f"tfc-{...}"`, so
            # a destroy that skipped this step could never find a deploy that
            # was created BY name (proof 2's whole point). W1 dev45 live-verify,
            # 2026-08-04: the name-targeting work extended the APPLY path but not
            # the symmetric destroy on either tier (the identical gap lives in
            # `taps.destroy_taps`). A concrete id passes through unchanged, so
            # the cascade path (which passes the concrete `te_deploy_id`) is
            # byte-identical to before. Falls back to the raw `target` if
            # resolution fails, so a name that no longer resolves to a live twin
            # still yields the clear "no deploy found" error below, not a
            # resolver error.
            try:
                resolved_target = resolve_traffic_target(store, target)
            except TrafficPlanError:
                resolved_target = target
            existing = store.find_deploy_for_plan(f"tfc-{resolved_target}")
            if existing is None:
                raise TrafficPlanError(
                    f"no traffic-profile deploy found for target {target!r}",
                    status=404,
                )
            did = existing.deploy_id

        d = store.get_deploy(did)
        if d is None:
            raise TrafficPlanError(f"deploy not found: {did}", status=404)
        if (d.tier or "te") != "tfc":
            raise TrafficPlanError(
                f"refusing to tear down {did}: tier={d.tier!r}, not 'tfc' -- "
                "axrange tfc destroy only tears down traffic-profile deploys",
                status=400,
            )

        cfg = load_config(adapter_config)
        if node:
            cfg.default_node = node

        backend = teardown_backend
        if backend is None:
            backend = adapter_for(DEFAULT_PLATFORM, cfg).teardown

        lease = store.acquire_lease(d.scenario_id, did, process_info=default_process_info())
        if not lease.acquired:
            raise TrafficPlanError(
                f"another operation on traffic profile {d.scenario_id!r} is "
                f"in progress (deploy_id={lease.held_by_deploy_id})", status=409,
            )

        heartbeat = Heartbeat(str(db_path), deploy_id=did, scenario_id=d.scenario_id)
        heartbeat.start()
        try:
            try:
                _stop_persisted_instances(
                    store, did, cfg=cfg, persona_backend=persona_backend, log=log,
                )
            except Exception as e:  # noqa: BLE001 -- best-effort, see docstring
                log(f"  ! traffic-profile stop pass failed: {e}")

            try:
                result = destroy_deploy(
                    store, backend, did, node=node, log=log,
                    protected_names=cfg.protected_external_names,
                    protected_vmids=cfg.protected_external_vmids,
                )
            except TeardownError as e:
                raise TrafficPlanError(str(e), status=404) from e
        finally:
            heartbeat.stop()
            try:
                store.release_lease(d.scenario_id, did)
            except Exception:  # noqa: BLE001
                pass
        return result
    finally:
        store.close()


def cascade_destroy_tfc_for_te(
    *,
    db_path: str,
    te_deploy_id: str,
    adapter_config: Optional[str] = None,
    node: Optional[str] = None,
    teardown_backend: Any = None,
    persona_backend: Any = None,
    log: Callable[[str], None] = lambda _m: None,
) -> Optional[Any]:
    """If `te_deploy_id` has a standalone traffic-profile deploy bound to
    it, tear it down too. Returns the traffic-profile's `TeardownResult`, or
    `None` if no traffic profile was ever applied to this twin -- NOT an
    error worth surfacing on a twin destroy. Any OTHER failure is logged and
    swallowed, same posture as `taps.cascade_destroy_for_te`."""
    try:
        return destroy_traffic_profile(
            db_path=db_path, target=te_deploy_id, adapter_config=adapter_config,
            node=node, teardown_backend=teardown_backend,
            persona_backend=persona_backend, log=log,
        )
    except TrafficPlanError as e:
        if e.status == 404:
            return None
        log(f"  ! traffic-profile cascade for {te_deploy_id}: {e.message}")
        return None


# ---------------------------------------------------------------------------
# Read-only status (`axrange tfc status`/`info`) -- derived purely from
# `state.db`, mirroring `taps.tap_status`'s posture.
# ---------------------------------------------------------------------------
def latest_traffic_deploy(store: Any, *, exclude_destroyed: bool = False) -> Any:
    """Most recent traffic-profile deploy -- `tfc`'s deploy tier IS a real,
    unambiguous `_VALID_TIERS` entry (unlike `tap`), so this is a plain
    tier-filtered query (`StateStore.deploys_matching`, LES-053's unbounded
    filter-scoped read), no plan_id-prefix/op-name-subset discriminator
    needed."""
    rows = store.deploys_matching(tier="tfc", exclude_destroyed=exclude_destroyed)
    return rows[0] if rows else None


def _assignments_summary_from_ops(ops: Any) -> list[AssignmentResult]:
    """Group a tfc deploy's persisted `drive-persona` ops into `persona ->
    [hosts]`. Filters to `status == COMPLETED` (code review, MUST-FIX
    corollary): a dropped instance's op row is marked `destroyed` by
    `_stop_dropped_instances` on a narrowing apply, and one that never
    confirmed running (`pending`/`failed`/`halted`) was never actually
    live either -- neither belongs in a "what's currently active" summary."""
    from axrange.planner.opview import op_fields

    by_persona: dict[str, list[str]] = {}
    for op in ops:
        if op.op_name != _DRIVE_PERSONA_OP or op.status != OpStatus.COMPLETED:
            continue
        _op_name, _scope, inp, _out = op_fields(op)
        persona_id = inp.get("persona_id") or "unknown"
        # `host_id` is its own recorded input (not parsed back out of
        # `instance_id`, which is a plain `f"{host_id}-{persona_id}"`
        # concatenation -- unsafe to split on "-" when a persona id itself
        # contains a hyphen, e.g. "dmz-web"/"corp-workstation").
        host_id = inp.get("host_id") or ""
        by_persona.setdefault(persona_id, []).append(host_id)
    return [
        AssignmentResult(target=f"persona:{pid}", persona=pid, hosts=sorted(hosts))
        for pid, hosts in sorted(by_persona.items())
    ]


def traffic_status(
    *, db_path: str, target: Optional[str] = None, deploy_id: Optional[str] = None,
) -> TrafficPlanStatus:
    """Read-only: an applied traffic profile's status + per-persona host
    roster, derived purely from `state.db` (mirrors `taps.tap_status`)."""
    from axrange.planner import StateStore

    store = StateStore(db_path)
    try:
        did = deploy_id
        if did is None and target is not None:
            # A twin NAME resolves to its newest live te deploy first
            # (Unified Console ruling §3.2): `plan_id` is keyed off the
            # minted id, so `tfc-corp-twin` never matched anything. A
            # concrete id passes through unchanged; an unresolvable name
            # falls back to the raw value and reports "not-applied".
            try:
                concrete = resolve_traffic_target(store, target)
            except TrafficPlanError:
                concrete = target
            existing = store.find_deploy_for_plan(f"tfc-{concrete}")
            did = existing.deploy_id if existing is not None else None
        elif did is None:
            # The bare default is "what is standing now": a destroyed tfc
            # deploy is history, not the answer (ruling §3.2 bug fix; the
            # same LES-084 posture `GET /api/deploys` already takes).
            row = latest_traffic_deploy(store, exclude_destroyed=True)
            did = row.deploy_id if row is not None else None

        if did is None:
            return TrafficPlanStatus(deploy_id=None, twin=target or "", status="not-applied")
        d = store.get_deploy(did)
        if d is None:
            return TrafficPlanStatus(deploy_id=None, twin=target or "", status="not-applied")
        twin_id = d.plan_id[len("tfc-"):] if d.plan_id.startswith("tfc-") else (target or "")
        assignments = _assignments_summary_from_ops(store.ops_for_deploy(did))
        return TrafficPlanStatus(
            deploy_id=did, twin=twin_id, status=d.status.value, assignments=assignments,
        )
    finally:
        store.close()


# ---------------------------------------------------------------------------
# `axrange tfc info` (work order §3's grid parity with `tap info`) --
# richer than `TrafficPlanStatus`: per-instance vmid + resolved peers, and a
# "resolved but not yet applied" tier when given a profile file instead of a
# deploy id (mirrors `taps.tap_info_from_resolution`/`tap_info_from_deploy`).
# ---------------------------------------------------------------------------
@dataclass
class AssignmentInfo:
    instance_id: str
    host_id: str
    persona: str
    vmid: Optional[int] = None
    peers: dict[str, str] = field(default_factory=dict)


@dataclass
class TrafficInfo:
    target: str
    target_status: str          # the target twin deploy's own status
    deploy_id: Optional[str]    # this traffic-profile playout's OWN deploy id, if applied
    status: str                 # "not-applied" | a DeployStatus value
    assignments: list[AssignmentInfo] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def traffic_info_from_resolution(
    resolution: TrafficPlanResolution, *, target: str,
) -> TrafficInfo:
    """The "resolved config, not yet applied" tier -- built from the SAME
    resolution `tfc validate`/`tfc plan` use, no second read of live state."""
    return TrafficInfo(
        target=target, target_status=resolution.target_status, deploy_id=None,
        status="not-applied",
        assignments=[
            AssignmentInfo(
                instance_id=ra.instance_id, host_id=ra.host_id, persona=ra.persona.id,
                vmid=ra.vmid, peers=dict(ra.peers),
            )
            for ra in resolution.resolved
        ],
        warnings=list(resolution.warnings),
    )


def traffic_info_from_deploy(
    store: Any, deploy_id: str, *, target: str, target_status: str,
) -> TrafficInfo:
    """The "config + access facts" tier -- reconstructs assignments from the
    persisted `drive-persona` ops' own resolved inputs (vmid/peers already
    resolved at apply time, no recompute). Filters to `status == COMPLETED`,
    same reasoning as `_assignments_summary_from_ops` (a dropped/never-
    confirmed instance must not show up here either)."""
    from axrange.planner.opview import op_fields

    assignments: list[AssignmentInfo] = []
    for op in store.ops_for_deploy(deploy_id):
        if op.op_name != _DRIVE_PERSONA_OP or op.status != OpStatus.COMPLETED:
            continue
        _op_name, _scope, inp, _out = op_fields(op)
        assignments.append(
            AssignmentInfo(
                instance_id=inp.get("instance_id") or "", host_id=inp.get("host_id") or "",
                persona=inp.get("persona_id") or "unknown", vmid=inp.get("vmid"),
                peers=dict(inp.get("peers") or {}),
            )
        )
    assignments.sort(key=lambda a: a.instance_id)
    d = store.get_deploy(deploy_id)
    status = d.status.value if d is not None else "not-applied"
    return TrafficInfo(
        target=target, target_status=target_status, deploy_id=deploy_id,
        status=status, assignments=assignments,
    )
