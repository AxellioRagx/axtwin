"""`GET /api/twins/{id}/tree` -- a cast bundle as a hierarchy.

Segment -> node group -> the group's intent (role, listen, install,
observation) and what the bundle's traffic profile gives it (personas, with
their emitted traffic). Everything is READ from the bundle's own files:

  * structure from the twin config (`imprints._load_twin_config`, the cached
    parse every other bundle route uses);
  * personas from the traffic profile (`tfc-cast.yaml` / `tfc.yaml` at the
    root of a legacy bundle, the manifest's `tfc.default` in a `twins/<id>/`
    folder), joined to groups the way the tfc tier resolves them: a `node:`
    assignment names an INSTANCE id (`expand.instance_ids`), a `role:`
    assignment addresses every group carrying that role;
  * each persona's traffic from the bundle's `personas/` folder first and
    the catalog second (`personas.registry.twin_overlay` precedence);
  * tap bindings from the tap plan, by segment.

A missing profile or tap plan is an ordinary bundle (the tree says which file
it read); a broken one is a warning, never a refusal -- the twin config alone
is the tree. Nothing here is fabricated: a group with no assignment has no
personas, a persona neither folder nor catalog defines is `missing: true`.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from axrange.api import dtos
from axrange.api.imprints import (
    ImprintDataError,
    _host_counts_by_segment,
    _imprint_dir,
    _load_twin_config,
    _node_segments,
    _resolved_count,
    check_imprint_addressable,
)
from axrange.twinconfig.schema import TwinConfig, TwinConfigNode

TFC_NAMES = ("tfc-cast.yaml", "tfc.yaml")
TAP_NAMES = ("tap-cast.yaml", "tap.yaml")


def load_twin_tree(imprint_id: str, *, fixtures_dir: Optional[str | Path] = None) -> dtos.TwinTreeDTO:
    """The tree for one bundle. Raises `ImprintNotFoundError` / `ImprintDataError`
    exactly as `load_imprint_summary` does for the same bundle."""
    dir_ = _imprint_dir(fixtures_dir, imprint_id)
    check_imprint_addressable(imprint_id, fixtures_dir=fixtures_dir)
    tc = _load_twin_config(dir_, imprint_id)
    warnings: list[str] = []

    by_segment, total_hosts = _host_counts_by_segment(tc, imprint_id=imprint_id)
    groups = [_group(node, tc, imprint_id=imprint_id) for node in tc.nodes]

    tfc_rel, assignments = _traffic_assignments(dir_, imprint_id, warnings)
    personas = _persona_docs(dir_, warnings)
    _bind_personas(groups, assignments, personas, warnings)

    tap_rel, taps_by_segment = _tap_bindings(dir_, imprint_id, warnings)

    segments = []
    for seg in tc.segments:
        members = [g for g in groups if g.kind != "router" and seg.name in g.segments]
        segments.append(dtos.TreeSegmentDTO(
            name=seg.name,
            cidr=seg.cidr,
            zone_type=seg.zone_type,
            hosts=by_segment.get(seg.name, 0),
            roles=_role_counts(members),
            observation=_observation(seg.observation),
            taps=taps_by_segment.get(seg.name, []),
            nodes=sorted(members, key=lambda g: (-g.count, g.name)),
            **_provenance(seg),
        ))
    segments.sort(key=lambda s: (-s.hosts, s.name))

    return dtos.TwinTreeDTO(
        id=imprint_id,
        hosts=total_hosts,
        segments=segments,
        routers=[g for g in groups if g.kind == "router"],
        traffic_profile=tfc_rel,
        tap_plan=tap_rel,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Groups
# ---------------------------------------------------------------------------
def _group(node: TwinConfigNode, tc: TwinConfig, *, imprint_id: str) -> dtos.TreeNodeDTO:
    from axrange.twinconfig.expand import instance_ids

    count = _count(node, tc, imprint_id=imprint_id)
    return dtos.TreeNodeDTO(
        name=node.name,
        kind=node.kind,
        role=node.role,
        image=node.image,
        count=count,
        instances=instance_ids(node.name, count),
        segments=_segments_with_links(node, tc),
        requires=list(node.requires or []),
        resources=dict(node.resources) if node.resources else None,
        listen=[dtos.TreeListenDTO(port=entry.port, proto=entry.proto) for entry in (node.listen or [])],
        install=[_install_label(step) for step in (node.install or [])],
        joins=node.joins,
        observation=_observation(node.observation),
        **_provenance(node),
    )


def _provenance(entry: Any) -> dict[str, Any]:
    """The entry's `source` / `confidence` / `vantage` as the twin config carries them (R4-4):
    the cast writes all three, a hand-authored config usually none, and the tree passes on
    exactly what is there."""
    return {
        "source": getattr(entry, "source", None),
        "confidence": float(entry.confidence) if getattr(entry, "confidence", None) is not None else None,
        "vantage": getattr(entry, "vantage", None),
    }


def _segments_with_links(node: TwinConfigNode, tc: TwinConfig) -> list[str]:
    """The node's declared segments plus every segment an `up` link of the
    twin carries to it (a `kind: switch` wired only by `links[]` sits in
    the segments its trunks carry -- docs/design/topology-layers.md §2.3)."""
    segs = list(_node_segments(node))
    for link in tc.links:
        if (link.state or "up") != "up":
            continue
        if node.name not in (link.a.device, link.b.device):
            continue
        for seg in ([link.native] if link.native else []) + list(link.vlans):
            if seg not in segs:
                segs.append(seg)
    return segs


def _count(node: TwinConfigNode, tc: TwinConfig, *, imprint_id: str) -> int:
    """The same resolution `_host_counts_by_segment` applies, for one node."""
    if node.count in (None, ""):
        return 1
    try:
        return int(_resolved_count(node.count, tc))
    except (TypeError, ValueError) as e:
        raise ImprintDataError(
            f"imprint {imprint_id!r}: node {node.name!r} has a non-numeric count: {node.count!r}"
        ) from e


def _install_label(step: Any) -> str:
    """`{pkg: nginx}` -> `pkg nginx`; a multi-key step lists each pair."""
    if isinstance(step, dict):
        return ", ".join(f"{k} {v}" for k, v in step.items())
    return str(step)


def _observation(raw: Any) -> Optional[dtos.TreeObservationDTO]:
    if raw is None:
        return None
    return dtos.TreeObservationDTO(
        windows_seen=raw.windows_seen,
        windows_total=raw.windows_total,
        occupancy=raw.occupancy,
        role_confidence=raw.role_confidence,
    )


def _role_counts(members: list[dtos.TreeNodeDTO]) -> list[dtos.TreeRoleCountDTO]:
    counts: dict[Optional[str], int] = {}
    for g in members:
        counts[g.role] = counts.get(g.role, 0) + g.count
    return [
        dtos.TreeRoleCountDTO(role=role, hosts=n)
        for role, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0] or ""))
    ]


# ---------------------------------------------------------------------------
# Traffic profile -> personas
# ---------------------------------------------------------------------------
def _tier_file(dir_: Path, tier: str, root_names: tuple[str, ...]) -> Optional[Path]:
    """The bundle's file for a tier: a `twins/<id>/` folder's manifest default,
    else the first legacy root name present. None when the bundle ships none."""
    from axrange.twinconfig.bundle import is_twin_folder
    from axrange.twinconfig.errors import TwinConfigError
    from axrange.twinconfig.manifest import load_twin_manifest

    if is_twin_folder(dir_):
        try:
            files = load_twin_manifest(dir_).tier(tier)
        except TwinConfigError:
            return None  # the addressing guard already refused a broken manifest
        return dir_ / files.default if files and files.default else None
    for name in root_names:
        if (dir_ / name).is_file():
            return dir_ / name
    return None


def _traffic_assignments(dir_: Path, imprint_id: str, warnings: list[str]) -> tuple[Optional[str], list[Any]]:
    from axrange.o2control.traffic import TrafficPlanError, load_traffic_profile

    path = _tier_file(dir_, "tfc", TFC_NAMES)
    if path is None:
        return None, []
    rel = str(path.relative_to(dir_))
    try:
        return rel, list(load_traffic_profile(path).assignments)
    except TrafficPlanError as e:
        warnings.append(f"twin {imprint_id!r}: traffic profile {rel} not read ({e}); no personas shown")
        return rel, []


def _persona_docs(dir_: Path, warnings: list[str]) -> dict[str, Any]:
    """Persona id -> document: the bundle's `personas/` folder ahead of the
    catalog, the precedence the tfc tier applies."""
    from axrange.personas.registry import PERSONAS, PersonaRegistryError, twin_overlay

    docs: dict[str, Any] = dict(PERSONAS)
    folder = dir_ / "personas"
    if folder.is_dir():
        try:
            docs.update(twin_overlay(folder))
        except PersonaRegistryError as e:
            warnings.append(f"the bundle's personas/ folder failed to load ({e}); catalog personas only")
    return docs


def _bind_personas(
    groups: list[dtos.TreeNodeDTO], assignments: list[Any], docs: dict[str, Any], warnings: list[str],
) -> None:
    by_instance = {inst: g for g in groups for inst in g.instances}
    by_name = {g.name: g for g in groups}
    # (group name, persona id, bound_by) -> the DTO being accumulated
    bound: dict[tuple[str, str, str], dtos.TreePersonaDTO] = {}

    def give(g: dtos.TreeNodeDTO, a: Any, *, bound_by: str, instances: int) -> None:
        key = (g.name, a.persona, bound_by)
        row = bound.get(key)
        if row is None:
            row = _persona_row(a, docs, bound_by=bound_by)
            bound[key] = row
            g.personas.append(row)
        row.instances = min(g.count, row.instances + instances)

    for a in assignments:
        if a.node is not None:
            g = by_instance.get(a.node) or by_name.get(a.node)
            if g is None:
                warnings.append(f"traffic profile assigns {a.persona!r} to node {a.node!r}, which the twin config does not declare")
                continue
            give(g, a, bound_by="node", instances=1)
        elif a.role is not None:
            targets = [g for g in groups if g.role == a.role]
            if not targets:
                warnings.append(f"traffic profile assigns {a.persona!r} to role {a.role!r}, which no node carries")
            for g in targets:
                give(g, a, bound_by="role", instances=g.count)
    for g in groups:
        g.personas.sort(key=lambda p: (-p.instances, p.id))


def _persona_row(a: Any, docs: dict[str, Any], *, bound_by: str) -> dtos.TreePersonaDTO:
    doc = docs.get(a.persona)
    return dtos.TreePersonaDTO(
        id=a.persona,
        bound_by=bound_by,
        instances=0,
        schedule=a.schedule,
        seed=a.seed,
        intensity=a.intensity,
        os_family=doc.os_family if doc else None,
        drive=doc.drive if doc else None,
        missing=doc is None,
        emits=[
            dtos.PersonaEmitDTO(
                proto=e.proto, ports=list(e.ports), peer_role=e.peer_role,
                transport=e.effective_transport(), cadence=e.cadence,
                volume=e.volume, optional=e.optional, peer_scope=e.peer_scope,
            )
            for e in (doc.emits if doc else ())
        ],
    )


# ---------------------------------------------------------------------------
# Tap plan -> per-segment bindings
# ---------------------------------------------------------------------------
def _tap_bindings(dir_: Path, imprint_id: str, warnings: list[str]) -> tuple[Optional[str], dict[str, list[dtos.TreeTapDTO]]]:
    from axrange.o2control.taps import TapPlanError, load_tap_plan

    path = _tier_file(dir_, "tap", TAP_NAMES)
    if path is None:
        return None, {}
    rel = str(path.relative_to(dir_))
    try:
        plan = load_tap_plan(path)
    except TapPlanError as e:
        warnings.append(f"twin {imprint_id!r}: tap plan {rel} not read ({e}); no taps shown")
        return rel, {}
    out: dict[str, list[dtos.TreeTapDTO]] = {}
    for b in plan.bindings:
        sel = b.selector or {}
        if sel.get("scope") == "segment" and isinstance(sel.get("target"), str):
            out.setdefault(sel["target"], []).append(dtos.TreeTapDTO(sensors=list(b.sensors), mode=b.mode))
    return rel, out
