"""LIF-* checks — Pass 3, accumulate."""
from __future__ import annotations

from axrange.ir.capabilities import split_image_ref
from axrange.manifest.schema import ImageStatus
from axrange.validator._make import make_finding
from axrange.validator.checks import Check, ValidatorContext
from axrange.validator.checks.capability import _resolve_image_version
from axrange.validator.finding import Finding


def check_lif_010(ctx: ValidatorContext) -> list[Finding]:
    """No node references a withdrawn image version."""
    findings = []
    if ctx.ir.layers is None:
        return findings
    for i, node in enumerate(ctx.ir.layers.infra.nodes):
        if not node.image:
            continue
        _, v = _resolve_image_version(ctx, node.image)
        if v is None:
            continue
        if v.status == ImageStatus.WITHDRAWN.value:
            findings.append(
                make_finding(
                    "LIF-010",
                    node_id=node.id or f"<index {i}>",
                    image_ref=node.image,
                    replacement=v.replacement or "<none specified>",
                    location=f"layers.infra.nodes[{i}].image",
                )
            )
    return findings


def check_lif_020(ctx: ValidatorContext) -> list[Finding]:
    """Node references a deprecated image version (warning)."""
    findings = []
    if ctx.ir.layers is None:
        return findings
    for i, node in enumerate(ctx.ir.layers.infra.nodes):
        if not node.image:
            continue
        _, v = _resolve_image_version(ctx, node.image)
        if v is None:
            continue
        if v.status == ImageStatus.DEPRECATED.value:
            hint = f" Replacement: '{v.replacement}'." if v.replacement else ""
            findings.append(
                make_finding(
                    "LIF-020",
                    node_id=node.id or f"<index {i}>",
                    image_ref=node.image,
                    replacement_hint=hint,
                    location=f"layers.infra.nodes[{i}].image",
                )
            )
    return findings


def check_lif_030(ctx: ValidatorContext) -> list[Finding]:
    """Activity references a deprecated behavior version (warning; rptoe).

    No behavior library registry in v1 — passthrough no-op until registry lands.
    """
    return []


def check_lif_040(ctx: ValidatorContext) -> list[Finding]:
    """Activity references a withdrawn behavior version."""
    return []


def check_lif_050(ctx: ValidatorContext) -> list[Finding]:
    """Pinned-but-deprecated: a pinned ref's lifecycle changed since authoring.

    For images: when a node uses a pinned `image@version` and that version's
    current status is `deprecated`, emit a heads-up. (LIF-020 already covers
    this, so LIF-050 is redundant in practice unless we add behavior-library
    pinning later. Stub kept for catalog completeness.)
    """
    return []


def check_lif_060(ctx: ValidatorContext) -> list[Finding]:
    """reproducible: true and missing top-level seed:."""
    if ctx.ir.scenario is None:
        return []
    if not ctx.reproducible:
        return []
    if ctx.ir.scenario.seed is None:
        return [make_finding("LIF-060", reproducible=ctx.reproducible)]
    return []


CHECKS: list[Check] = [
    check_lif_010,
    check_lif_020,
    check_lif_030,
    check_lif_040,
    check_lif_050,
    check_lif_060,
]
