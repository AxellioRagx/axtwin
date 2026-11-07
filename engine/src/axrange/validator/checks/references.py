"""REF-* checks — Pass 2, halt on first error."""
from __future__ import annotations

from collections import defaultdict

from axrange.ir.capabilities import split_behavior_ref, split_image_ref
from axrange.ir.schema import IR
from axrange.validator._make import make_finding
from axrange.validator.checks import Check, ValidatorContext
from axrange.validator.finding import Finding


def _node_ids(ir: IR) -> set[str]:
    if ir.layers is None:
        return set()
    return {n.id for n in ir.layers.infra.nodes if n.id}


def _net_ids(ir: IR) -> set[str]:
    if ir.layers is None:
        return set()
    return {n.id for n in ir.layers.infra.networks if n.id}


def _app_ids(ir: IR) -> set[str]:
    if ir.layers is None:
        return set()
    return {a.id for a in ir.layers.apps if a.id}


def _activity_ids(ir: IR) -> set[str]:
    if ir.layers is None:
        return set()
    return {a.id for a in ir.layers.activity if a.id}


def _identity_ids(ir: IR) -> set[str]:
    if ir.overlays is None or ir.overlays.identity is None:
        return set()
    return {i.id for i in ir.overlays.identity if i.id}


def _bindings_iter(ctx: ValidatorContext):
    raw = ctx.raw.get("bindings") if isinstance(ctx.raw, dict) else None
    if not isinstance(raw, list):
        return
    for i, b in enumerate(raw):
        if isinstance(b, dict):
            yield i, b


# --- App↔host bindings ---


def check_ref_010(ctx: ValidatorContext) -> list[Finding]:
    apps = _app_ids(ctx.ir)
    findings = []
    for i, b in _bindings_iter(ctx):
        if "app" in b and "host" in b:
            if b["app"] not in apps:
                findings.append(
                    make_finding(
                        "REF-010", index=i, app_id=b["app"], location=f"bindings[{i}]"
                    )
                )
    return findings


def check_ref_020(ctx: ValidatorContext) -> list[Finding]:
    nodes = _node_ids(ctx.ir)
    findings = []
    for i, b in _bindings_iter(ctx):
        if "app" in b and "host" in b:
            if b["host"] not in nodes:
                findings.append(
                    make_finding(
                        "REF-020", index=i, host_id=b["host"], location=f"bindings[{i}]"
                    )
                )
    return findings


# --- Activity→host bindings ---


def check_ref_030(ctx: ValidatorContext) -> list[Finding]:
    acts = _activity_ids(ctx.ir)
    findings = []
    for i, b in _bindings_iter(ctx):
        if "activity" in b and "host" in b:
            if b["activity"] not in acts:
                findings.append(
                    make_finding(
                        "REF-030", index=i, activity_id=b["activity"], location=f"bindings[{i}]"
                    )
                )
    return findings


def check_ref_040(ctx: ValidatorContext) -> list[Finding]:
    nodes = _node_ids(ctx.ir)
    findings = []
    for i, b in _bindings_iter(ctx):
        if "activity" in b and "host" in b:
            if b["host"] not in nodes:
                findings.append(
                    make_finding(
                        "REF-040", index=i, host_id=b["host"], location=f"bindings[{i}]"
                    )
                )
    return findings


# --- Activity→targets bindings ---


def check_ref_050(ctx: ValidatorContext) -> list[Finding]:
    acts = _activity_ids(ctx.ir)
    findings = []
    for i, b in _bindings_iter(ctx):
        if "activity" in b and "targets" in b:
            if b["activity"] not in acts:
                findings.append(
                    make_finding(
                        "REF-050", index=i, activity_id=b["activity"], location=f"bindings[{i}]"
                    )
                )
    return findings


def check_ref_060(ctx: ValidatorContext) -> list[Finding]:
    """Targets resolve to a defined host, app, or actor (identity)."""
    nodes = _node_ids(ctx.ir)
    apps = _app_ids(ctx.ir)
    actors = _identity_ids(ctx.ir)
    findings = []
    for i, b in _bindings_iter(ctx):
        if "activity" in b and "targets" in b:
            targets = b.get("targets") or []
            if not isinstance(targets, list):
                continue
            for t in targets:
                if not isinstance(t, str):
                    continue
                if t not in nodes and t not in apps and t not in actors:
                    findings.append(
                        make_finding(
                            "REF-060",
                            index=i,
                            target_id=t,
                            location=f"bindings[{i}].targets",
                        )
                    )
    return findings


# --- Defense, identity, data references ---


def check_ref_070(ctx: ValidatorContext) -> list[Finding]:
    """Defense host: references a defined host."""
    nodes = _node_ids(ctx.ir)
    findings = []
    if ctx.ir.overlays is None or ctx.ir.overlays.defense is None:
        return findings
    for i, d in enumerate(ctx.ir.overlays.defense):
        if d.host is not None and d.host not in nodes:
            findings.append(
                make_finding(
                    "REF-070",
                    index=i,
                    host_id=d.host,
                    location=f"overlays.defense[{i}]",
                )
            )
    return findings


def check_ref_080(ctx: ValidatorContext) -> list[Finding]:
    """Defense observes: nets resolve."""
    nets = _net_ids(ctx.ir)
    findings = []
    if ctx.ir.overlays is None or ctx.ir.overlays.defense is None:
        return findings
    for i, d in enumerate(ctx.ir.overlays.defense):
        if not d.observes:
            continue
        for j, o in enumerate(d.observes):
            if o.net is not None and o.net not in nets:
                findings.append(
                    make_finding(
                        "REF-080",
                        index=i,
                        j=j,
                        net_id=o.net,
                        location=f"overlays.defense[{i}].observes[{j}]",
                    )
                )
    return findings


def check_ref_090(ctx: ValidatorContext) -> list[Finding]:
    """Identity overlay: id present (basic shape; the spec mostly handles
    groups/trust refs in OVR-030)."""
    findings = []
    if ctx.ir.overlays is None or ctx.ir.overlays.identity is None:
        return findings
    for i, ident in enumerate(ctx.ir.overlays.identity):
        if not ident.id:
            findings.append(
                make_finding(
                    "REF-090", index=i, id="<missing>", location=f"overlays.identity[{i}]"
                )
            )
    return findings


def check_ref_100(ctx: ValidatorContext) -> list[Finding]:
    """Data overlay host: references a defined host."""
    nodes = _node_ids(ctx.ir)
    findings = []
    if ctx.ir.overlays is None or ctx.ir.overlays.data is None:
        return findings
    for i, d in enumerate(ctx.ir.overlays.data):
        if d.host is not None and d.host not in nodes:
            findings.append(
                make_finding(
                    "REF-100", index=i, host_id=d.host, location=f"overlays.data[{i}]"
                )
            )
    return findings


def check_ref_110(ctx: ValidatorContext) -> list[Finding]:
    """Every net:/nets: ref on a node resolves to a defined network."""
    nets = _net_ids(ctx.ir)
    findings = []
    if ctx.ir.layers is None:
        return findings
    for i, node in enumerate(ctx.ir.layers.infra.nodes):
        if node.net is not None and node.net not in nets:
            findings.append(
                make_finding(
                    "REF-110",
                    node_id=node.id or f"<index {i}>",
                    net_id=node.net,
                    location=f"layers.infra.nodes[{i}]",
                )
            )
        if node.nets:
            for j, n in enumerate(node.nets):
                if n.net is not None and n.net not in nets:
                    findings.append(
                        make_finding(
                            "REF-110",
                            node_id=node.id or f"<index {i}>",
                            net_id=n.net,
                            location=f"layers.infra.nodes[{i}].nets[{j}]",
                        )
                    )
    return findings


def check_ref_120(ctx: ValidatorContext) -> list[Finding]:
    """Every image: ref resolves to a manifest entry.

    Skipped only when `ctx.skip_unknown_image_lifecycle` is set — the flag
    `validate_structural` (Pass 1+2 only: the canvas on every keystroke,
    dev/test shape-only checks) sets on its context, because that entry
    point intentionally runs with an empty `ManifestRegistry()` before
    manifests are available. This is NOT a blanket "registry is empty ⇒
    skip" guard: `validate_full` (the `axrange validate`/`deploy` path) can
    also be handed an empty registry — e.g. a wrong `--images` path — and in
    that case the rule must still fail loudly, since a genuinely-unloaded
    image set means every `image:` ref is truly unresolvable and deploy
    would otherwise fail cryptically much later (`clone-vm: template_vmid
    missing`).
    """
    findings = []
    if ctx.ir.layers is None:
        return findings
    if ctx.skip_unknown_image_lifecycle:
        return findings
    for i, node in enumerate(ctx.ir.layers.infra.nodes):
        if not node.image:
            continue
        image_id, version = split_image_ref(node.image)
        m = ctx.manifests.get_manifest(image_id)
        if m is None:
            # Tolerate missing manifests in dev/test by emitting a non-fatal
            # info-level passthrough? Per spec REF-120 is an error. We'll honor it.
            findings.append(
                make_finding(
                    "REF-120",
                    node_id=node.id or f"<index {i}>",
                    image_ref=node.image,
                    location=f"layers.infra.nodes[{i}].image",
                )
            )
            continue
        if version is not None and m.get_version(version) is None:
            findings.append(
                make_finding(
                    "REF-120",
                    node_id=node.id or f"<index {i}>",
                    image_ref=node.image,
                    location=f"layers.infra.nodes[{i}].image",
                )
            )
    return findings


def check_ref_130(ctx: ValidatorContext) -> list[Finding]:
    """Image ref without version pinning (warning; rptoe)."""
    findings = []
    if ctx.ir.layers is None:
        return findings
    for i, node in enumerate(ctx.ir.layers.infra.nodes):
        if not node.image:
            continue
        image_id, version = split_image_ref(node.image)
        if version is not None:
            continue
        m = ctx.manifests.get_manifest(image_id)
        if m is None:
            continue  # REF-120 already fired
        current = m.current_version()
        findings.append(
            make_finding(
                "REF-130",
                reproducible=ctx.reproducible,
                node_id=node.id or f"<index {i}>",
                image_ref=node.image,
                resolved=current.id if current else "<no current>",
                location=f"layers.infra.nodes[{i}].image",
            )
        )
    return findings


def check_ref_140(ctx: ValidatorContext) -> list[Finding]:
    """Every behavior library ref resolves.

    v1 has no behavior library registry yet; the rule errors only when an
    activity declares a `behavior:` field at all and we have no way to
    confirm. To avoid emitting false errors against scenarios that reference
    valid library behaviors, the registry-absent case is currently a no-op.
    When the library registry lands, this check expands accordingly.
    """
    return []


def check_ref_150(ctx: ValidatorContext) -> list[Finding]:
    """Behavior ref without version pinning (warning; rptoe)."""
    findings = []
    if ctx.ir.layers is None:
        return findings
    for i, act in enumerate(ctx.ir.layers.activity):
        if not act.behavior:
            continue
        _, version = split_behavior_ref(act.behavior)
        if version is None:
            findings.append(
                make_finding(
                    "REF-150",
                    reproducible=ctx.reproducible,
                    activity_id=act.id or f"<index {i}>",
                    behavior_ref=act.behavior,
                    location=f"layers.activity[{i}]",
                )
            )
    return findings


def check_ref_160(ctx: ValidatorContext) -> list[Finding]:
    """Activity actor: resolves to an overlay identity or implicit local user.

    "Implicit local user on the activity's bound host" — we treat any
    non-empty actor as resolvable unless overlays.identity is present and the
    actor is missing AND no Activity→host binding exists (then there is no
    host on which to materialize the local user).
    """
    findings: list[Finding] = []
    if ctx.ir.layers is None:
        return findings
    identities = _identity_ids(ctx.ir)
    nodes = _node_ids(ctx.ir)
    # Map activity_id → list of (binding-host) it's placed on.
    act_hosts: dict[str, list[str]] = defaultdict(list)
    for _, b in _bindings_iter(ctx):
        if "activity" in b and "host" in b:
            act_hosts[b["activity"]].append(b["host"])
    for i, act in enumerate(ctx.ir.layers.activity):
        if not act.actor:
            findings.append(
                make_finding(
                    "REF-160",
                    activity_id=act.id or f"<index {i}>",
                    actor="<missing>",
                    location=f"layers.activity[{i}]",
                )
            )
            continue
        if act.actor in identities:
            continue
        hosts = act_hosts.get(act.id or "", []) if act.id else []
        # Need at least one bound host to materialize an implicit local user.
        if not hosts or not any(h in nodes for h in hosts):
            findings.append(
                make_finding(
                    "REF-160",
                    activity_id=act.id or f"<index {i}>",
                    actor=act.actor,
                    location=f"layers.activity[{i}]",
                )
            )
    return findings


def check_ref_170(ctx: ValidatorContext) -> list[Finding]:
    """Activity references actor missing from a present overlays.identity (warn)."""
    findings: list[Finding] = []
    if ctx.ir.layers is None:
        return findings
    if ctx.ir.overlays is None or ctx.ir.overlays.identity is None:
        return findings  # rule only fires when identity overlay is present
    identities = _identity_ids(ctx.ir)
    for i, act in enumerate(ctx.ir.layers.activity):
        if not act.actor:
            continue
        if act.actor not in identities:
            findings.append(
                make_finding(
                    "REF-170",
                    activity_id=act.id or f"<index {i}>",
                    actor=act.actor,
                    location=f"layers.activity[{i}]",
                )
            )
    return findings


def check_ref_180(ctx: ValidatorContext) -> list[Finding]:
    """Every alias is unique across the whole IR."""
    findings: list[Finding] = []
    if ctx.ir.layers is None:
        return findings
    by_alias: dict[str, list[str]] = defaultdict(list)
    for i, node in enumerate(ctx.ir.layers.infra.nodes):
        for alias in node.aliases or []:
            by_alias[alias].append(f"nodes[{i}]({node.id})")
    for alias, where in by_alias.items():
        if len(where) > 1:
            findings.append(
                make_finding("REF-180", alias=alias, where=where)
            )
    return findings


# check_ref_200 "tap→sensor binding references a defined sensor and a
# tapped host/network" RETIRED 2026-07-28
# (`docs/WORK_ORDER_tap_eligibility.md`, Authority: user) -- the
# `tap_host`/`tap_net`/`sensor` binding kind it validated (and the
# `_tapped_node_ids`/`_sensor_ids` helpers it alone used) is removed from
# IR_SCHEMA (D6). See catalog.py's comment for the full rationale; REF-200
# is never reused. (Code review, `_sensor_ids` follow-up: confirmed zero
# call sites tree-wide before removal.)


def check_ref_190(ctx: ValidatorContext) -> list[Finding]:
    """Phase refs (dormant in v1)."""
    findings: list[Finding] = []
    for i, b in _bindings_iter(ctx):
        conds = b.get("conditions") or {}
        phases = conds.get("only_in_phase")
        if phases:
            # Dormant: no phases defined in IR v1. We do NOT fire the rule
            # (per spec it's reserved). When phases ratify, activate this.
            pass
    return findings


def check_ref_210(ctx: ValidatorContext) -> list[Finding]:
    """A sensor's `type` is a known sensor type, and its `captures`/`transport`
    reference defined sources (sensors design, 2026-06-23,
    docs/design/06-taps-sensors.md)."""
    from axrange.sensors import SENSOR_TYPES, is_known_type

    ir = ctx.ir
    findings: list[Finding] = []
    nets = _net_ids(ir)
    node_ids = {n.id for n in ir.layers.infra.nodes if n.id} if ir.layers else set()
    transport_nets = (
        {n.id for n in ir.layers.infra.networks if n.id and n.transport}
        if ir.layers else set()
    )
    for i, s in enumerate(ir.sensors or []):
        if not is_known_type(s.type):  # None ⇒ default tcpdump-sensor (ok)
            findings.append(make_finding(
                "REF-210", index=i, location=f"sensors[{i}]",
                detail=(f"unknown sensor type '{s.type}'; known types: "
                        f"{sorted(SENSOR_TYPES)}")))
        for c in (s.captures or []):
            if c not in nets and c not in node_ids:
                findings.append(make_finding(
                    "REF-210", index=i, location=f"sensors[{i}]",
                    detail=f"captures: '{c}' is not a defined network or node"))
        if s.transport is not None:
            if s.transport not in nets:
                findings.append(make_finding(
                    "REF-210", index=i, location=f"sensors[{i}]",
                    detail=f"transport: '{s.transport}' is not a defined network"))
            elif s.transport not in transport_nets:
                findings.append(make_finding(
                    "REF-210", index=i, location=f"sensors[{i}]",
                    detail=(f"transport: '{s.transport}' must be a network with "
                            "transport: true")))
    return findings


def check_ref_220(ctx: ValidatorContext) -> list[Finding]:
    """Adversary overlay: id present (mirrors REF-090 for identity). A3 Stage A."""
    findings = []
    if ctx.ir.overlays is None or ctx.ir.overlays.adversary is None:
        return findings
    for i, adv in enumerate(ctx.ir.overlays.adversary):
        if not adv.id:
            findings.append(
                make_finding(
                    "REF-220", index=i, location=f"overlays.adversary[{i}]"
                )
            )
    return findings


def check_ref_230(ctx: ValidatorContext) -> list[Finding]:
    """Adversary overlay targets: every target (fan-out) references a defined
    host (LES-034 -- check every binding, not just the first). A3 Stage A."""
    nodes = _node_ids(ctx.ir)
    findings = []
    if ctx.ir.overlays is None or ctx.ir.overlays.adversary is None:
        return findings
    for i, adv in enumerate(ctx.ir.overlays.adversary):
        for j, t in enumerate(adv.targets or []):
            if t not in nodes:
                findings.append(
                    make_finding(
                        "REF-230", index=i, j=j, host_id=t,
                        location=f"overlays.adversary[{i}].targets[{j}]",
                    )
                )
    return findings


def check_ref_240(ctx: ValidatorContext) -> list[Finding]:
    """Adversary overlay targets: every target (fan-out) is a kind: host node
    (LES-034 -- check every binding). Sibling to REF-230 (existence) -- a
    router/switch target has no QGA and would hang at deploy, not fail at
    validate, without this."""
    findings = []
    if ctx.ir.overlays is None or ctx.ir.overlays.adversary is None:
        return findings
    if ctx.ir.layers is None:
        return findings
    node_kind = {n.id: n.kind for n in ctx.ir.layers.infra.nodes if n.id}
    for i, adv in enumerate(ctx.ir.overlays.adversary):
        for j, t in enumerate(adv.targets or []):
            if t not in node_kind:
                continue  # REF-230 already covers an unresolved target
            kind = str(node_kind[t] or "host").lower().split(".")[-1]
            if kind != "host":
                findings.append(
                    make_finding(
                        "REF-240", index=i, j=j, host_id=t, kind=kind,
                        location=f"overlays.adversary[{i}].targets[{j}]",
                    )
                )
    return findings


def check_ref_250(ctx: ValidatorContext) -> list[Finding]:
    """A declared `sensors:` block is not stood up by a te deploy (sensor-agnostic
    te, 2026-07-13, docs/WORK_ORDER_sensor_agnostic_te.md, review fix F1). WARNING,
    not an error -- the shipped target-environments that still author `sensors:`
    (ad-domain-lab, purple-team-range, etc.) must keep validating; capture is
    `axrange o2 up` + `axrange o2 tap apply` after deploy, not this block."""
    findings: list[Finding] = []
    sensors = ctx.ir.sensors or []
    if not sensors:
        return findings
    findings.append(
        make_finding("REF-250", count=len(sensors), location="sensors")
    )
    return findings


def check_ref_260(ctx: ValidatorContext) -> list[Finding]:
    """`layers.activity:` / `overlays.adversary:` are declared, but a te
    deploy no longer runs them (Exercise-tier scope-shift, 2026-07-24,
    docs/WORK_ORDER_exercise_tier.md, docs/design/02-twin-model.md).
    WARNING, not an error -- mirrors REF-250's posture exactly (same
    reasoning: the shipped target-environments that still author
    `activity:`/`overlays.adversary:` -- ad-domain-lab, purple-team-range,
    soc-detection-lab -- must keep validating; that content is now
    `axrange exr deploy`-consumed, not te-emitted)."""
    findings: list[Finding] = []
    activities = list(ctx.ir.layers.activity or []) if ctx.ir.layers else []
    adversary = (
        list(ctx.ir.overlays.adversary or []) if ctx.ir.overlays else []
    )
    if not activities and not adversary:
        return findings
    findings.append(
        make_finding(
            "REF-260",
            count=len(activities) + len(adversary),
            activity_count=len(activities),
            adversary_count=len(adversary),
            location="layers.activity/overlays.adversary",
        )
    )
    return findings


def _link_ends(ctx: ValidatorContext):
    for i, link in enumerate(ctx.ir.layers.infra.links):
        for side, end in (("a", link.a), ("b", link.b)):
            yield i, side, end


def check_ref_270(ctx: ValidatorContext) -> list[Finding]:
    """Every `links[].a.device` / `.b.device` names a defined node (any kind)."""
    findings: list[Finding] = []
    if ctx.ir.layers is None:
        return findings
    node_ids = {n.id for n in ctx.ir.layers.infra.nodes if n.id}
    for i, side, end in _link_ends(ctx):
        dev = end.device if end is not None else None
        if dev and dev not in node_ids:
            findings.append(make_finding(
                "REF-270", index=i, side=side, device=dev,
                location=f"layers.infra.links[{i}].{side}.device",
            ))
    return findings


def check_ref_280(ctx: ValidatorContext) -> list[Finding]:
    """Every `links[].vlans[]` entry and `links[].native` names a defined network."""
    findings: list[Finding] = []
    if ctx.ir.layers is None:
        return findings
    net_ids = {n.id for n in ctx.ir.layers.infra.networks if n.id}
    for i, link in enumerate(ctx.ir.layers.infra.links):
        refs = [("native", link.native)] if link.native else []
        refs += [(f"vlans[{j}]", v) for j, v in enumerate(link.vlans or [])]
        for field_name, net in refs:
            if isinstance(net, str) and net and net not in net_ids:
                findings.append(make_finding(
                    "REF-280", index=i, field=field_name, net_id=net,
                    location=f"layers.infra.links[{i}].{field_name}",
                ))
    return findings


CHECKS: list[Check] = [
    check_ref_010,
    check_ref_020,
    check_ref_030,
    check_ref_040,
    check_ref_050,
    check_ref_060,
    check_ref_070,
    check_ref_080,
    check_ref_090,
    check_ref_100,
    check_ref_110,
    check_ref_120,
    check_ref_130,
    check_ref_140,
    check_ref_150,
    check_ref_160,
    check_ref_170,
    check_ref_180,
    check_ref_190,
    # REF-200 RETIRED (docs/WORK_ORDER_tap_eligibility.md) -- see this
    # file's comment above `check_ref_190`; never reused.
    check_ref_210,
    check_ref_220,
    check_ref_230,
    check_ref_240,
    check_ref_250,
    check_ref_260,
    check_ref_270,
    check_ref_280,
]
