"""DEF-* checks — Pass 3, accumulate."""
from __future__ import annotations

from axrange.ir.capabilities import TAP_FROM_MODES, split_image_ref
from axrange.validator._make import make_finding
from axrange.validator.checks import Check, ValidatorContext
from axrange.validator.checks.capability import _resolve_image_version
from axrange.validator.finding import Finding


def _defense_install_kind(tool: str | None) -> str | None:
    """The install kind `install-defense-agent` needs from the host image.

    Ratified by the Defense overlay increment (docs/SLICE_OVERLAY.md §4b,
    CHANGELOG 2026-06-15): host agents are **script/config-class** — the agent
    binary comes from an app, and the overlay drops the config + enables the
    service via a config script delivered over the QGA channel (exactly how
    `run-script` works). So the image needs `script` support, not `role`
    (the earlier `role` placeholder predated the implementation).
    """
    return "script"


def check_def_010(ctx: ValidatorContext) -> list[Finding]:
    """Defense installed_on host's image supports the install kind."""
    findings = []
    if ctx.ir.overlays is None or ctx.ir.overlays.defense is None:
        return findings
    if ctx.ir.layers is None:
        return findings
    host_image = {
        n.id: n.image for n in ctx.ir.layers.infra.nodes if n.id and n.image
    }
    for i, d in enumerate(ctx.ir.overlays.defense):
        if not d.installed_on:
            continue
        kind = _defense_install_kind(d.tool)
        if kind is None:
            continue
        for host_id in d.installed_on:
            image_ref = host_image.get(host_id)
            if image_ref is None:
                continue
            _, v = _resolve_image_version(ctx, image_ref)
            if v is None or v.install_support is None:
                continue
            if not v.install_support.supports(kind):
                findings.append(
                    make_finding(
                        "DEF-010",
                        defense_id=d.id or f"<index {i}>",
                        host_id=host_id,
                        kind=kind,
                        location=f"overlays.defense[{i}].installed_on",
                    )
                )
    return findings


def check_def_020(ctx: ValidatorContext) -> list[Finding]:
    """Defense with observes: has tap_from in the closed set."""
    findings = []
    if ctx.ir.overlays is None or ctx.ir.overlays.defense is None:
        return findings
    for i, d in enumerate(ctx.ir.overlays.defense):
        if not d.observes:
            continue
        if d.tap_from is None:
            findings.append(
                make_finding(
                    "DEF-020",
                    defense_id=d.id or f"<index {i}>",
                    tap_from="<missing>",
                    location=f"overlays.defense[{i}]",
                )
            )
        elif d.tap_from not in TAP_FROM_MODES:
            findings.append(
                make_finding(
                    "DEF-020",
                    defense_id=d.id or f"<index {i}>",
                    tap_from=d.tap_from,
                    location=f"overlays.defense[{i}]",
                )
            )
    return findings


def check_def_030(ctx: ValidatorContext) -> list[Finding]:
    """Defense tap_from: per-vm requires the observed network has nodes."""
    findings = []
    if ctx.ir.overlays is None or ctx.ir.overlays.defense is None:
        return findings
    if ctx.ir.layers is None:
        return findings
    # net_id → has any node?
    net_has_nodes: dict[str, bool] = {n.id: False for n in ctx.ir.layers.infra.networks if n.id}
    for node in ctx.ir.layers.infra.nodes:
        if node.net:
            net_has_nodes[node.net] = True
        if node.nets:
            for n in node.nets:
                if n.net:
                    net_has_nodes[n.net] = True
    for i, d in enumerate(ctx.ir.overlays.defense):
        if d.tap_from != "per-vm" or not d.observes:
            continue
        for j, o in enumerate(d.observes):
            if o.net and not net_has_nodes.get(o.net, False):
                findings.append(
                    make_finding(
                        "DEF-030",
                        defense_id=d.id or f"<index {i}>",
                        net_id=o.net,
                        location=f"overlays.defense[{i}].observes[{j}]",
                    )
                )
    return findings


def check_def_040(ctx: ValidatorContext) -> list[Finding]:
    """Defense tap_from: external-mirror flagged for operator review."""
    findings = []
    if ctx.ir.overlays is None or ctx.ir.overlays.defense is None:
        return findings
    for i, d in enumerate(ctx.ir.overlays.defense):
        if d.tap_from == "external-mirror":
            findings.append(
                make_finding(
                    "DEF-040",
                    defense_id=d.id or f"<index {i}>",
                    location=f"overlays.defense[{i}]",
                )
            )
    return findings


def check_def_050(ctx: ValidatorContext) -> list[Finding]:
    """Defense with observes: AND installed_on: — must be consistent.

    Consistency check: the tool's host (`host:`, used for the observe role)
    must be one of the `installed_on:` hosts. Otherwise the operator has split
    the tool across two hosts, which is meaningful only if `tap_from:` is
    `external-mirror` (operator-wired).
    """
    findings = []
    if ctx.ir.overlays is None or ctx.ir.overlays.defense is None:
        return findings
    for i, d in enumerate(ctx.ir.overlays.defense):
        if not d.observes or not d.installed_on or d.host is None:
            continue
        if d.tap_from == "external-mirror":
            continue  # operator wires across hosts intentionally
        if d.host not in d.installed_on:
            findings.append(
                make_finding(
                    "DEF-050",
                    defense_id=d.id or f"<index {i}>",
                    detail=(
                        f"host '{d.host}' (where the observe-mode tool runs) "
                        f"is not among installed_on: {d.installed_on}"
                    ),
                    location=f"overlays.defense[{i}]",
                )
            )
    return findings


CHECKS: list[Check] = [
    check_def_010,
    check_def_020,
    check_def_030,
    check_def_040,
    check_def_050,
]
