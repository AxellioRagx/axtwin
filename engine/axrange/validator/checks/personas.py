"""PSN-* checks — persona-catalog validation (docs/WORK_ORDER_persona_library.md
§2, `docs/design/07-traffic-personas.md`).

**Dependency direction (code-review fix, 2026-07-24):** the validator reads
the persona registry, never the other way around — the SAME direction
`check_ref_210` (this package's `references.py`) already uses for the
sibling `sensors/` registry (`from axrange.sensors import SENSOR_TYPES`).
`axrange.personas` exposes only `schema` + `registry` (+ `drivers`, which
this module never imports); it has zero `axrange.validator` imports. This
module is the one and only place persona *content* gets checked against
Finding/Severity — `axrange.personas` itself stays a pure leaf.

**Shape note (the documented escape hatch, `docs/WORK_ORDER_persona_library.md`
review):** every other `checks/*.py` module's functions are `Check =
Callable[[ValidatorContext], list[Finding]]` — one Scenario IR in, findings
out. PSN doesn't fit that shape: a persona is catalog content, not Scenario
IR content (no `bindings:`/node in any Scenario IR references a persona yet
— that binding is `twin_configs`/the Traffic tier's job), so there is no
`ctx.ir` to extract a persona (or a role set) from. Forcing a fake
`ValidatorContext` just to satisfy the `Check` signature would be a worse
fit than being honest about it: the functions below take a `Persona`
directly. `CHECKS` is therefore an intentionally empty list (like CLUSTER
has NO `checks/cluster.py` at all — pass 4 is genuinely unimplemented
there); PSN's checks ARE implemented and directly callable
(`validate_persona`/`validate_catalog`), just never wired into
`engine.py`'s `PASS_1/2/3_CHECKS` lists, matching the catalog-side pass_=4
placement `catalog.py`'s PSN block documents.
"""
from __future__ import annotations

from typing import Any

from axrange.ir.durations import DurationParseError, parse_duration
from axrange.validator._make import make_finding
from axrange.validator.checks import Check
from axrange.validator.finding import Finding

# No Check-shaped functions are wired into any pass — see the module
# docstring's shape note. Kept (empty) so this module still exports the
# `CHECKS` name every `checks/*.py` sibling does, for anyone grepping.
CHECKS: list[Check] = []


def _is_well_formed_cadence(cadence: str) -> bool:
    from axrange.personas.schema import CADENCE_KINDS

    if cadence in CADENCE_KINDS:
        return True
    if ":" not in cadence:
        return False
    kind, _, rest = cadence.partition(":")
    if kind not in ("cyclic", "burst"):
        return False
    try:
        parse_duration(rest)
    except DurationParseError:
        return False
    return True


def validate_persona(persona: Any, *, available_roles: frozenset[str] | None = None) -> list[Finding]:
    """Run every PSN-0xx check against one `axrange.personas.schema.Persona`.

    `available_roles` is the optional twin role-set PSN-080 checks
    `requires[]` against — omitted (`None`) for context-free catalog
    validation, where PSN-080 is a no-op (mirrors CLU-060's planner-side/
    cluster-context dependency). PSN-090 (peer_role declared in
    `requires[]`) needs no twin at all and always runs.
    """
    from axrange.personas.schema import (
        DRIVE_KINDS,
        KNOWN_PROTOS,
        OS_FAMILIES,
        PEER_SCOPES,
        RESERVED_PEER_ROLES,
        TRANSPORTS,
        VOLUME_LEVELS,
    )

    findings: list[Finding] = []
    pid = persona.id or "<unknown>"

    # PSN-010: id present and non-empty.
    if not persona.id:
        findings.append(make_finding("PSN-010"))

    # PSN-020: os_family enum.
    if persona.os_family not in OS_FAMILIES:
        findings.append(
            make_finding("PSN-020", persona_id=pid, os_family=persona.os_family)
        )

    # PSN-030: drive enum.
    if persona.drive not in DRIVE_KINDS:
        findings.append(make_finding("PSN-030", persona_id=pid, drive=persona.drive))

    for i, emit in enumerate(persona.emits):
        # PSN-040: known proto.
        if emit.proto not in KNOWN_PROTOS:
            findings.append(
                make_finding("PSN-040", persona_id=pid, index=i, proto=emit.proto)
            )
        # PSN-050: peer_role is set at all (presence only).
        if not emit.peer_role or not emit.peer_role.strip():
            findings.append(make_finding("PSN-050", persona_id=pid, index=i))
        # PSN-090: a SET, HARD (non-optional) peer_role must be declared in
        # requires[] (unless it's one of the reserved external-egress
        # tokens). Presence (PSN-050) and satisfiability (PSN-090) are
        # deliberately separate findings — an empty peer_role shouldn't also
        # spuriously fire PSN-090 (there's nothing to check requires[]
        # membership against). An `optional: true` emit (2026-07-25
        # refinement, docs/WORK_ORDER_traffic_tier.md) is a peer the persona
        # talks to only IF PRESENT — it need NOT be declared in requires[],
        # so it's exempt here exactly like a reserved token.
        elif (
            emit.peer_role not in RESERVED_PEER_ROLES
            and not emit.optional
            and emit.peer_role not in persona.requires
        ):
            findings.append(
                make_finding(
                    "PSN-090", persona_id=pid, index=i, peer_role=emit.peer_role
                )
            )
        # PSN-060: well-formed cadence.
        if not _is_well_formed_cadence(emit.cadence):
            findings.append(
                make_finding("PSN-060", persona_id=pid, index=i, cadence=emit.cadence)
            )
        # PSN-070: volume enum.
        if emit.volume not in VOLUME_LEVELS:
            findings.append(
                make_finding("PSN-070", persona_id=pid, index=i, volume=emit.volume)
            )
        # PSN-100: transport enum. Only an EXPLICIT value can be wrong — an
        # omitted one is derived from proto by `default_transport()` and is
        # always in-enum, so the empty string is checked as valid here rather
        # than resolving it first (resolving would make the rule unable to
        # distinguish "authored badly" from "not authored").
        if emit.transport and emit.transport not in TRANSPORTS:
            findings.append(
                make_finding(
                    "PSN-100", persona_id=pid, index=i, transport=emit.transport
                )
            )
        # PSN-110: peer_scope enum.
        if emit.peer_scope not in PEER_SCOPES:
            findings.append(
                make_finding(
                    "PSN-110", persona_id=pid, index=i, peer_scope=emit.peer_scope
                )
            )

    # PSN-080: requires[] satisfiable against a supplied twin role set.
    # A no-op (skip entirely) when no role set is supplied — a persona is
    # twin-agnostic by design, so this only fires once something (the future
    # Traffic tier) actually binds a persona to a twin and passes its roster.
    if available_roles is not None and persona.requires:
        missing = sorted(
            r for r in persona.requires
            if r not in available_roles and r not in RESERVED_PEER_ROLES
        )
        if missing:
            findings.append(
                make_finding(
                    "PSN-080",
                    persona_id=pid,
                    missing=missing,
                    roles=sorted(available_roles),
                )
            )

    return findings


def validate_catalog(
    personas: dict[str, Any] | None = None,
    *,
    available_roles: frozenset[str] | None = None,
) -> dict[str, list[Finding]]:
    """Validate every persona in `personas` (defaults to the built-in
    registry — `from axrange.personas.registry import PERSONAS`, the SAME
    validator-reads-registry direction `check_ref_210` uses for
    `axrange.sensors`). Returns `{persona_id: [Finding, ...]}` — an empty
    list for a clean persona, so `all(not f for f in
    validate_catalog().values())` is the "every catalog persona validates"
    assertion."""
    if personas is None:
        from axrange.personas.registry import PERSONAS as personas  # noqa: PLC0415

    return {
        pid: validate_persona(p, available_roles=available_roles)
        for pid, p in personas.items()
    }
