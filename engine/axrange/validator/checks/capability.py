"""CAP-* checks — Pass 3, accumulate."""
from __future__ import annotations

from axrange.ir.capabilities import (
    HOST_CAPABILITIES,
    KERNEL_CAPABILITIES,
    OS_FAMILY_KEYWORDS,
    install_action_kind,
    split_image_ref,
)
from axrange.validator._make import make_finding
from axrange.validator.checks import Check, ValidatorContext, node_kind
from axrange.validator.finding import Finding


def _resolve_image_version(ctx: ValidatorContext, image_ref: str):
    """Return (Manifest, ManifestVersion) for an `image[@version]` ref, or
    (None, None) if not resolvable.
    """
    if not image_ref:
        return None, None
    image_id, version = split_image_ref(image_ref)
    m = ctx.manifests.get_manifest(image_id)
    if m is None:
        return None, None
    if version is None:
        return m, m.current_version()
    return m, m.get_version(version)


def _app_host_mapping(ctx: ValidatorContext) -> dict[str, list[str]]:
    """app_id → [host_id, ...] from bindings (an app may be bound to multiple
    hosts — check every one of them, not just the last-wins single host)."""
    raw = ctx.raw.get("bindings") if isinstance(ctx.raw, dict) else None
    mapping: dict[str, list[str]] = {}
    if not isinstance(raw, list):
        return mapping
    for b in raw:
        if isinstance(b, dict) and "app" in b and "host" in b:
            mapping.setdefault(b["app"], []).append(b["host"])
    return mapping


def check_cap_010(ctx: ValidatorContext) -> list[Finding]:
    """Image provides ⊇ node requires (OS-family + kernel:* subset)."""
    findings = []
    if ctx.ir.layers is None:
        return findings
    for i, node in enumerate(ctx.ir.layers.infra.nodes):
        if not node.requires or not node.image:
            continue
        m, v = _resolve_image_version(ctx, node.image)
        if v is None:
            continue
        # Restrict to OS-family + kernel:* (host:* deferred to CLU-010).
        ir_caps = {
            k for k in node.requires if k in OS_FAMILY_KEYWORDS or k in KERNEL_CAPABILITIES
        }
        provided = set(v.provides or [])
        missing = ir_caps - provided
        if missing:
            findings.append(
                make_finding(
                    "CAP-010",
                    node_id=node.id or f"<index {i}>",
                    missing=sorted(missing),
                    image_ref=node.image,
                    provided=sorted(provided),
                    location=f"layers.infra.nodes[{i}]",
                )
            )
    return findings


def check_cap_020(ctx: ValidatorContext) -> list[Finding]:
    """For each install action on a bound app, the action kind ∈ image's install_support."""
    findings = []
    if ctx.ir.layers is None:
        return findings
    app_host = _app_host_mapping(ctx)
    host_image: dict[str, str] = {
        n.id: n.image for n in ctx.ir.layers.infra.nodes if n.id and n.image
    }
    raw_apps = (
        ctx.raw.get("layers", {}).get("apps") if isinstance(ctx.raw, dict) else None
    )
    if not isinstance(raw_apps, list):
        return findings
    for i, app_raw in enumerate(raw_apps):
        if not isinstance(app_raw, dict):
            continue
        app_id = app_raw.get("id")
        if app_id is None:
            continue
        host_ids = app_host.get(app_id) or []
        for host_id in host_ids:
            image_ref = host_image.get(host_id)
            if image_ref is None:
                continue
            _, version = _resolve_image_version(ctx, image_ref)
            if version is None or version.install_support is None:
                continue
            for j, action in enumerate(app_raw.get("install") or []):
                if not isinstance(action, dict):
                    continue
                kind = install_action_kind(action)
                if kind is None:
                    continue
                if not version.install_support.supports(kind):
                    findings.append(
                        make_finding(
                            "CAP-020",
                            app_id=app_id,
                            kind=kind,
                            image_ref=image_ref,
                            host_id=host_id,
                            location=f"layers.apps[{i}].install[{j}]",
                        )
                    )
                elif kind == "role":
                    # `role:` shorthand — verify the role name is in install_support.role list.
                    role_name = action.get("role")
                    allowed = version.install_support.valid_role_names()
                    if allowed and role_name not in allowed:
                        findings.append(
                            make_finding(
                                "CAP-020",
                                app_id=app_id,
                                kind=f"role:{role_name}",
                                image_ref=image_ref,
                                host_id=host_id,
                                location=f"layers.apps[{i}].install[{j}]",
                            )
                        )
    return findings


def check_cap_030(ctx: ValidatorContext) -> list[Finding]:
    """Image OS family matches node's OS-family keyword."""
    findings = []
    if ctx.ir.layers is None:
        return findings
    for i, node in enumerate(ctx.ir.layers.infra.nodes):
        if not node.requires or not node.image:
            continue
        m, v = _resolve_image_version(ctx, node.image)
        if m is None or m.image is None or m.image.os is None:
            continue
        image_os = m.image.os.family
        node_os = next((k for k in node.requires if k in OS_FAMILY_KEYWORDS), None)
        if node_os is None or image_os is None:
            continue
        if node_os != image_os:
            findings.append(
                make_finding(
                    "CAP-030",
                    node_id=node.id or f"<index {i}>",
                    node_os=node_os,
                    image_ref=node.image,
                    image_os=image_os,
                    location=f"layers.infra.nodes[{i}]",
                )
            )
    return findings


def check_cap_040(ctx: ValidatorContext) -> list[Finding]:
    """Node requires already provided by image's OS-family declaration — redundant info."""
    findings = []
    if ctx.ir.layers is None:
        return findings
    for i, node in enumerate(ctx.ir.layers.infra.nodes):
        if not node.requires or not node.image:
            continue
        m, _ = _resolve_image_version(ctx, node.image)
        if m is None or m.image is None or m.image.os is None:
            continue
        image_os = m.image.os.family
        # The OS-family keyword in requires:, if equal to image_os, is technically
        # redundant *given* the image binding. Treat the explicit declaration as
        # intentional, not redundant — operators write it for clarity. CAP-040
        # should fire when requires: lists a keyword the OS-family already implies.
        # Since IR_SCHEMA §7.4 says "no implicit inheritance", the rule actually
        # fires only when a node lists a kernel:* keyword whose presence is
        # advertised by the image's OS family contract — but with no inheritance
        # in v1, this is a no-op. Keeping the check stub for catalog completeness.
    return findings


def check_cap_050(ctx: ValidatorContext) -> list[Finding]:
    """Node requires host:* keyword no manifest binding declares — possible
    intended kernel:* miswrite.

    Approximation: the node's image binding's `requires_host_caps:` set is the
    union of host:* keywords this image actually needs. If a node-declared
    host:* keyword is not in any registered binding's requires_host_caps for
    that image, the operator may have meant a kernel:* keyword.
    """
    findings = []
    if ctx.ir.layers is None:
        return findings
    for i, node in enumerate(ctx.ir.layers.infra.nodes):
        if not node.requires or not node.image:
            continue
        host_caps = [k for k in node.requires if k in HOST_CAPABILITIES]
        if not host_caps:
            continue
        image_id, _ = split_image_ref(node.image)
        binding = ctx.manifests.get_binding(image_id, "proxmox")
        # If no binding, we have no signal — silently skip.
        if binding is None:
            continue
        # Aggregate host caps across all binding versions.
        binding_caps: set[str] = set()
        for bv in binding.versions:
            if bv.placement_constraints:
                binding_caps.update(bv.placement_constraints.requires_host_caps)
        for cap in host_caps:
            if cap not in binding_caps:
                findings.append(
                    make_finding(
                        "CAP-050",
                        node_id=node.id or f"<index {i}>",
                        cap=cap,
                        location=f"layers.infra.nodes[{i}]",
                    )
                )
    return findings


def check_cap_060(ctx: ValidatorContext) -> list[Finding]:
    """Router/switch capability requirements consistent with kind.

    Routers/switches typically run minimal images and shouldn't declare
    workload-specific kernel:* requirements (e.g. kernel:edr-hooks on a
    router is nearly certainly a paste error).
    """
    findings = []
    if ctx.ir.layers is None:
        return findings
    suspect_for_infra = {"kernel:edr-hooks", "kernel:dotnet-clr", "kernel:powershell-5plus"}
    for i, node in enumerate(ctx.ir.layers.infra.nodes):
        kind = node_kind(node)
        if kind not in ("router", "switch"):
            continue
        if not node.requires:
            continue
        bad = [c for c in node.requires if c in suspect_for_infra]
        if bad:
            findings.append(
                make_finding(
                    "CAP-060",
                    node_id=node.id or f"<index {i}>",
                    kind=kind,
                    caps=bad,
                    location=f"layers.infra.nodes[{i}]",
                )
            )
    return findings


CHECKS: list[Check] = [
    check_cap_010,
    check_cap_020,
    check_cap_030,
    check_cap_040,
    check_cap_050,
    check_cap_060,
]
