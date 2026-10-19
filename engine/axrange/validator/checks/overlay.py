"""OVR-* checks — Pass 3, accumulate."""
from __future__ import annotations

from collections import defaultdict

from axrange.validator._make import make_finding
from axrange.validator.checks import Check, ValidatorContext
from axrange.validator.finding import Finding


def _present_overlay_sections(ctx: ValidatorContext) -> set[str]:
    raw = ctx.raw.get("overlays") if isinstance(ctx.raw, dict) else None
    if not isinstance(raw, dict):
        return set()
    return set(raw.keys())


def check_ovr_010(ctx: ValidatorContext) -> list[Finding]:
    """Overlay binding references an overlay sub-section absent from overlays.

    "Bindings to overlays" in v1 are implicit: an activity's `actor:` points
    to an identity, a defense's `host:` points to a host (not overlay), etc.
    The rule fires when an activity references an identity and overlays.identity
    is absent. Similar for data references and defense references.
    """
    findings = []
    present = _present_overlay_sections(ctx)
    if ctx.ir.layers is None:
        return findings
    if "identity" not in present:
        # If any activity references a domain identity (heuristic: actor looks
        # like a domain user the IR cannot resolve locally), flag the missing section.
        if any(a.actor for a in ctx.ir.layers.activity):
            # We can't tell from the IR alone if actors *intend* to be domain.
            # Only fire when overlays.identity is explicitly referenced —
            # currently no IR fields name overlays.identity directly, so
            # the rule mostly stays quiet in v1. Kept for catalog completeness.
            pass
    return findings


def check_ovr_020(ctx: ValidatorContext) -> list[Finding]:
    """Overlay sub-section present but no entry is referenced anywhere."""
    findings = []
    present = _present_overlay_sections(ctx)

    if ctx.ir.layers is None:
        return findings

    # identity referenced via activity.actor
    actor_refs: set[str] = {a.actor for a in ctx.ir.layers.activity if a.actor}
    if "identity" in present and ctx.ir.overlays and ctx.ir.overlays.identity:
        all_ids = {i.id for i in ctx.ir.overlays.identity if i.id}
        if all_ids and not (all_ids & actor_refs):
            findings.append(make_finding("OVR-020", section="identity"))

    # data: hard to tell from IR alone what "references" mean — fire only if
    # no data entries are defined but the section is present.
    if "data" in present and ctx.ir.overlays and not ctx.ir.overlays.data:
        findings.append(make_finding("OVR-020", section="data"))

    if "defense" in present and ctx.ir.overlays and not ctx.ir.overlays.defense:
        findings.append(make_finding("OVR-020", section="defense"))

    return findings


def check_ovr_030(ctx: ValidatorContext) -> list[Finding]:
    """Identity overlay: every group references defined users; every trust references defined domains.

    IR v1 doesn't model groups-by-id or trusts as separate entities; group
    references are free-form strings, trusts aren't represented. Without
    those structures, the rule has nothing to validate. Stub kept for
    catalog completeness.
    """
    return []


def check_ovr_040(ctx: ValidatorContext) -> list[Finding]:
    """Data classification taxonomy. Dormant in v1 — taxonomy not ratified."""
    return []


CHECKS: list[Check] = [
    check_ovr_010,
    check_ovr_020,
    check_ovr_030,
    check_ovr_040,
]
