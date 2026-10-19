"""STR-* checks — Pass 1, halt on first error.

Pass-1 also surfaces parser-time findings (YAML failed to parse, includes
couldn't resolve, duplicate IDs across files). Those are handled in
engine.py before the structural checks below run.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from axrange.ir.capabilities import (
    INSTALL_ACTION_KEYS,
    NODE_KINDS,
    OS_FAMILY_KEYWORDS,
    OVERLAY_SECTIONS,
    ALL_CAPABILITIES,
    install_action_kind,
)
from axrange.validator._make import make_finding
from axrange.validator.checks import Check, ValidatorContext, node_kind
from axrange.validator.finding import Finding


def check_str_010(ctx: ValidatorContext) -> list[Finding]:
    """YAML parses. Mostly handled by parser; flag any STR-010 from parser."""
    return [
        make_finding("STR-010", reason=str(p.message), location=p.file)
        for p in ctx.parse_findings
        if p.rule_id == "STR-010"
    ]


def check_str_020(ctx: ValidatorContext) -> list[Finding]:
    """scenario_schema_version present and recognized.

    Recognized set widened from `{1}` to `{1, 2}` (Authority: user,
    2026-07-24, `docs/WORK_ORDER_traffic_tier.md` Part 1 -- the IR gains an
    optional, additive `node.role` field). Backward-compatible: every
    existing `scenario_schema_version: 1` IR (all shipped `target-
    environments/`/`twin-configs/` content, every internal composer) keeps
    validating completely unchanged -- `role:` needs no version bump to be
    used, exactly like every other additive IR field this project has
    shipped (`default_agents`, `routing:`, `schedule.interval`, ...). `2` is
    accepted so an author who wants to explicitly mark an IR as "authored
    with role: in mind" may, without being rejected.

    Widened again `{1, 2}` -> `{1, 2, 3}` (Authority: user, 2026-07-28,
    `docs/WORK_ORDER_tap_eligibility.md` §4.3 -- the twin substrate becomes
    tap-agnostic; `tap:` is REMOVED from the IR, a genuinely BREAKING
    change unlike `1->2`'s additive one, hence a version bump at all). Still
    backward-compatible in the sense that matters here: STR-200 (the
    leftover-`tap:` reject) fires on ANY declared version, not just `3` --
    declaring `3` is optional, a way to mark an IR as "authored post-
    removal," same non-enforced-marker posture as `2`. `3` must be
    RECOGNIZED (not rejected by this rule) so an author who follows the
    shipped docs' own instruction to stamp `scenario_schema_version: 3` on a
    post-removal twin doesn't get a spurious STR-020 error."""
    v = ctx.ir.scenario_schema_version
    if v is None:
        return [make_finding("STR-020", value="<missing>")]
    if v not in (1, 2, 3):
        return [make_finding("STR-020", value=v)]
    return []


def check_str_030(ctx: ValidatorContext) -> list[Finding]:
    """scenario.version present and non-negative int."""
    if ctx.ir.scenario is None:
        return []  # STR-040 will catch
    v = ctx.ir.scenario.version
    if v is None:
        return [make_finding("STR-030", value="<missing>")]
    if not isinstance(v, int) or isinstance(v, bool) or v < 0:
        return [make_finding("STR-030", value=repr(v))]
    return []


def check_str_040(ctx: ValidatorContext) -> list[Finding]:
    """scenario: header present with id + version (required fields)."""
    findings: list[Finding] = []
    if ctx.ir.scenario is None:
        return [make_finding("STR-040", missing="['scenario' block]")]
    s = ctx.ir.scenario
    missing = []
    if s.id is None:
        missing.append("id")
    if s.version is None:
        missing.append("version")
    if missing:
        findings.append(make_finding("STR-040", missing=missing))
    # Also surface parser-injected STR-040s (Pydantic shape failures, etc.)
    findings.extend(
        make_finding("STR-040", missing=p.message, location=p.file)
        for p in ctx.parse_findings
        if p.rule_id == "STR-040"
    )
    return findings


def check_str_050(ctx: ValidatorContext) -> list[Finding]:
    """layers: present with infra + apps."""
    missing: list[str] = []
    if ctx.ir.layers is None:
        return [make_finding("STR-050", missing="['layers' block]")]
    # `infra` defaults to an empty Infra() — distinguish present-vs-missing via the raw dict.
    raw_layers = ctx.raw.get("layers") if isinstance(ctx.raw, dict) else None
    if not isinstance(raw_layers, dict):
        return [make_finding("STR-050", missing="['layers' block]")]
    if "infra" not in raw_layers:
        missing.append("infra")
    if "apps" not in raw_layers:
        missing.append("apps")
    if missing:
        return [make_finding("STR-050", missing=missing)]
    return []


def check_str_060(ctx: ValidatorContext) -> list[Finding]:
    """bindings: present (may be empty list)."""
    if not isinstance(ctx.raw, dict):
        return []
    if "bindings" not in ctx.raw:
        return [make_finding("STR-060")]
    if ctx.ir.bindings is None and ctx.raw.get("bindings") is not None:
        # Present but couldn't parse as a list — surface as STR-060.
        return [make_finding("STR-060")]
    return []


def check_str_070(ctx: ValidatorContext) -> list[Finding]:
    """Every node: exactly one of net: or nets:."""
    findings: list[Finding] = []
    if ctx.ir.layers is None:
        return findings
    for i, node in enumerate(ctx.ir.layers.infra.nodes):
        has_net = node.net is not None
        has_nets = node.nets is not None and len(node.nets) > 0
        node_id = node.id or f"<index {i}>"
        loc = f"layers.infra.nodes[{i}]"
        if has_net and has_nets:
            findings.append(
                make_finding("STR-070", node_id=node_id, state="both", location=loc)
            )
        elif not has_net and not has_nets:
            findings.append(
                make_finding("STR-070", node_id=node_id, state="neither", location=loc)
            )
    return findings


def check_str_080(ctx: ValidatorContext) -> list[Finding]:
    """Node kind: ∈ {host, router, switch}."""
    findings: list[Finding] = []
    if ctx.ir.layers is None:
        return findings
    for i, node in enumerate(ctx.ir.layers.infra.nodes):
        # NodeKind enum or string both acceptable.
        kind_str = node_kind(node)
        if kind_str not in NODE_KINDS:
            findings.append(
                make_finding(
                    "STR-080",
                    node_id=node.id or f"<index {i}>",
                    kind=kind_str or "<missing>",
                    location=f"layers.infra.nodes[{i}]",
                )
            )
    return findings


def check_str_090(ctx: ValidatorContext) -> list[Finding]:
    """Every node ID unique within layers.infra.nodes."""
    findings: list[Finding] = []
    if ctx.ir.layers is None:
        return findings
    counts = Counter(n.id for n in ctx.ir.layers.infra.nodes if n.id)
    for node_id, count in counts.items():
        if count > 1:
            findings.append(make_finding("STR-090", node_id=node_id, count=count))
    return findings


def check_str_095(ctx: ValidatorContext) -> list[Finding]:
    """Every network ID unique."""
    findings: list[Finding] = []
    if ctx.ir.layers is None:
        return findings
    counts = Counter(n.id for n in ctx.ir.layers.infra.networks if n.id)
    for net_id, count in counts.items():
        if count > 1:
            findings.append(make_finding("STR-095", net_id=net_id, count=count))
    return findings


def _binding_kind(b_raw: dict) -> str | None:
    """Classify a binding entry. Returns canonical kind or None if invalid.

    The former tap→sensor kind (`tap_host`/`tap_net`/`sensor`) is REMOVED
    (`docs/WORK_ORDER_tap_eligibility.md` D6) -- a binding carrying those
    keys now matches no recognized kind and falls through to `None`, so
    STR-100 rejects it with no dedicated rule needed (REF-200, which used to
    validate this kind's references, retires alongside it)."""
    keys = set(k for k in b_raw if k != "conditions")
    if keys == {"app", "host"}:
        return "app-host"
    if keys == {"activity", "host"}:
        return "activity-host"
    if keys == {"activity", "targets"}:
        return "activity-targets"
    return None


def check_str_100(ctx: ValidatorContext) -> list[Finding]:
    """Every binding has a recognized kind."""
    findings: list[Finding] = []
    raw_bindings = ctx.raw.get("bindings") if isinstance(ctx.raw, dict) else None
    if not isinstance(raw_bindings, list):
        return findings
    for i, b in enumerate(raw_bindings):
        if not isinstance(b, dict):
            findings.append(
                make_finding("STR-100", index=i, keys=type(b).__name__, location=f"bindings[{i}]")
            )
            continue
        if _binding_kind(b) is None:
            findings.append(
                make_finding("STR-100", index=i, keys=sorted(b.keys()), location=f"bindings[{i}]")
            )
    return findings


def check_str_110(ctx: ValidatorContext) -> list[Finding]:
    """Every requires: keyword is in the closed 16-keyword set."""
    findings: list[Finding] = []
    if ctx.ir.layers is None:
        return findings
    for i, node in enumerate(ctx.ir.layers.infra.nodes):
        if not node.requires:
            continue
        unknown = [k for k in node.requires if k not in ALL_CAPABILITIES]
        if unknown:
            findings.append(
                make_finding(
                    "STR-110",
                    node_id=node.id or f"<index {i}>",
                    unknown=unknown,
                    location=f"layers.infra.nodes[{i}]",
                )
            )
    return findings


def check_str_120(ctx: ValidatorContext) -> list[Finding]:
    """Exactly one OS-family keyword per node (when requires: present)."""
    findings: list[Finding] = []
    if ctx.ir.layers is None:
        return findings
    for i, node in enumerate(ctx.ir.layers.infra.nodes):
        if not node.requires:
            continue
        os_family = [k for k in node.requires if k in OS_FAMILY_KEYWORDS]
        if len(os_family) != 1:
            findings.append(
                make_finding(
                    "STR-120",
                    node_id=node.id or f"<index {i}>",
                    found=os_family or "<none>",
                    location=f"layers.infra.nodes[{i}]",
                )
            )
    return findings


def check_str_130(ctx: ValidatorContext) -> list[Finding]:
    """Every install: action kind is in the closed 6-kind set."""
    findings: list[Finding] = []
    if ctx.ir.layers is None:
        return findings
    raw_apps = (
        ctx.raw.get("layers", {}).get("apps") if isinstance(ctx.raw, dict) else None
    )
    if not isinstance(raw_apps, list):
        return findings
    for i, app_raw in enumerate(raw_apps):
        if not isinstance(app_raw, dict):
            continue
        app_id = app_raw.get("id", f"<index {i}>")
        install = app_raw.get("install") or []
        if not isinstance(install, list):
            continue
        for j, action in enumerate(install):
            if not isinstance(action, dict):
                findings.append(
                    make_finding(
                        "STR-130",
                        app_id=app_id,
                        index=j,
                        keys=type(action).__name__,
                        location=f"layers.apps[{i}].install[{j}]",
                    )
                )
                continue
            keys = set(action.keys())
            # Exactly one of the five action keys (pkg/config/role/container/
            # script) may appear per install action. Two or more (e.g.
            # {pkg: nginx, script: setup.sh}) is a malformed action even
            # though install_action_kind() would resolve one of them.
            action_keys_present = keys & INSTALL_ACTION_KEYS
            if len(action_keys_present) > 1:
                findings.append(
                    make_finding(
                        "STR-130",
                        app_id=app_id,
                        index=j,
                        keys=sorted(keys),
                        location=f"layers.apps[{i}].install[{j}]",
                    )
                )
                continue
            # Only `pkg` may co-occur with `version`. Other action keys must
            # appear alone. Stray keys are STR-130.
            kind = install_action_kind(action)
            if kind is None:
                findings.append(
                    make_finding(
                        "STR-130",
                        app_id=app_id,
                        index=j,
                        keys=sorted(keys),
                        location=f"layers.apps[{i}].install[{j}]",
                    )
                )
                continue
            # If keys contain something unrelated to the chosen kind, flag.
            allowed = {kind.replace("@version", "")} | {"version"}
            extras = keys - allowed - INSTALL_ACTION_KEYS - {"pkg"}
            if extras:
                findings.append(
                    make_finding(
                        "STR-130",
                        app_id=app_id,
                        index=j,
                        keys=sorted(keys),
                        location=f"layers.apps[{i}].install[{j}]",
                    )
                )
    return findings


def check_str_140(ctx: ValidatorContext) -> list[Finding]:
    """overlays: sub-sections in {identity, data, defense}."""
    findings: list[Finding] = []
    raw_overlays = ctx.raw.get("overlays") if isinstance(ctx.raw, dict) else None
    if not isinstance(raw_overlays, dict):
        return findings
    for section in raw_overlays.keys():
        if section not in OVERLAY_SECTIONS:
            findings.append(
                make_finding("STR-140", section=section, location=f"overlays.{section}")
            )
    return findings


def check_str_150(ctx: ValidatorContext) -> list[Finding]:
    """scenario.seed: is string or int."""
    if ctx.ir.scenario is None or ctx.ir.scenario.seed is None:
        return []
    seed = ctx.ir.scenario.seed
    if isinstance(seed, bool) or not isinstance(seed, (int, str)):
        return [make_finding("STR-150", type=type(seed).__name__)]
    return []


def check_str_160(ctx: ValidatorContext) -> list[Finding]:
    """scenario.reproducible: is bool."""
    raw_scenario = ctx.raw.get("scenario") if isinstance(ctx.raw, dict) else None
    if not isinstance(raw_scenario, dict) or "reproducible" not in raw_scenario:
        return []
    v = raw_scenario["reproducible"]
    if not isinstance(v, bool):
        return [make_finding("STR-160", type=type(v).__name__)]
    return []


def check_str_170(ctx: ValidatorContext) -> list[Finding]:
    """Info: alias-resolved references. Surfaced by reference checks elsewhere;
    no standalone check fires here. Kept as a placeholder so the rule's
    presence is verifiable.
    """
    return []


def check_str_200(ctx: ValidatorContext) -> list[Finding]:
    """A `tap:` field (node / leg / network) is REJECTED loudly
    (`docs/WORK_ORDER_tap_eligibility.md`, Authority: user, 2026-07-28) --
    `tap:` is REMOVED from `IR_SCHEMA` entirely; the twin substrate carries
    NO tap-related content any more (capture eligibility/scope/target are
    authored entirely in a standalone tap plan, resolved against the twin's
    published topology). A leftover `tap:` (stale content, a copy-pasted
    archetype, hand-edited YAML) must not be silently absorbed by Pydantic's
    `extra="allow"` and vanish an author's capture intent with no signal
    (LES-111). Supersedes `STR-190` (retired -- that rule only checked a
    PRESENT `tap:`'s boolean-ness; this one rejects its mere PRESENCE,
    regardless of value). Inspects the raw dict, same posture STR-190 used,
    so the location is precise."""
    findings: list[Finding] = []
    if not isinstance(ctx.raw, dict):
        return findings
    layers = ctx.raw.get("layers")
    infra = layers.get("infra") if isinstance(layers, dict) else None
    if not isinstance(infra, dict):
        return findings

    for i, net in enumerate(infra.get("networks") or []):
        if isinstance(net, dict) and "tap" in net:
            findings.append(make_finding(
                "STR-200", location=f"layers.infra.networks[{i}].tap"))

    for i, node in enumerate(infra.get("nodes") or []):
        if not isinstance(node, dict):
            continue
        if "tap" in node:
            findings.append(make_finding(
                "STR-200", location=f"layers.infra.nodes[{i}].tap"))
        for j, leg in enumerate(node.get("nets") or []):
            if isinstance(leg, dict) and "tap" in leg:
                findings.append(make_finding(
                    "STR-200", location=f"layers.infra.nodes[{i}].nets[{j}].tap"))
    return findings


def check_str_180(ctx: ValidatorContext) -> list[Finding]:
    """Info: unknown top-level keys."""
    findings: list[Finding] = []
    if not isinstance(ctx.raw, dict):
        return findings
    known = {
        "scenario_schema_version",
        "scenario",
        "layers",
        "bindings",
        "sensors",
        "overlays",
        "fabric",
        "egress",   # GAP-101: the twin's containment posture (ir.schema.Egress)
    }
    for key in ctx.raw.keys():
        if key not in known:
            findings.append(make_finding("STR-180", key=key))
    return findings


CHECKS: list[Check] = [
    check_str_010,
    check_str_020,
    check_str_030,
    check_str_040,
    check_str_050,
    check_str_060,
    check_str_070,
    check_str_080,
    check_str_090,
    check_str_095,
    check_str_100,
    check_str_110,
    check_str_120,
    check_str_130,
    check_str_140,
    check_str_150,
    check_str_160,
    check_str_170,
    check_str_180,
    # STR-190 RETIRED (docs/WORK_ORDER_tap_eligibility.md) -- superseded by
    # STR-200 below; the id is never reused (docs/VALIDATOR.md §3.3).
    check_str_200,
]
