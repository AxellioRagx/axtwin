"""Validator checks for traffic lines (`SPEC.md` "Validator rules", TGN-0xx;
rule text in `axrange.validator.catalog`).

`check_lines` takes a parsed tfc profile, the twin view, the personas and
apps, and the generator image the target's platform resolves (None: the
platform has none), and returns findings. Errors refuse planning.
"""
from __future__ import annotations

import ipaddress
from typing import Any, Optional

from axrange.trafficgen.addresses import AllocationError, Allocator, allocate, group_segment
from axrange.trafficgen.twin import TwinView
from axrange.validator._make import make_finding
from axrange.validator.finding import Finding

#: Header and framing overhead added to an app's payload bytes for the Mbps estimate.
_WIRE_OVERHEAD = 1.15


def app_bytes(app: Any) -> int:
    """Payload bytes one connection of `app` carries, with tunable defaults:
    every send/reply step (padded size when larger) and every recv; for a
    replayed capture, the capture file's size (an upper bound)."""
    if app.source_kind == "pcap":
        return app.pcap.stat().st_size if app.pcap else 0
    vals = {k: t.default for k, t in app.tunables.items()}
    vals.update(server_name="x" * 24, server_ip="255.255.255.255", client_ip="255.255.255.255")
    total = 0
    for st in app.program:
        n = 0
        if "send" in st or "reply" in st:
            n = len(st.get("send") or st.get("reply")) // 2
        elif "send_text" in st or "reply_text" in st:
            n = len((st.get("send_text") or st.get("reply_text")).format(**vals).encode())
        elif "recv" in st:
            n = int(str(st["recv"]).format(**vals))
        if "size" in st:
            n = max(n, int(str(st["size"]).format(**vals)))
        total += n
    return total


def mix_shares(persona: Any) -> list[tuple[str, float]]:
    total = sum(e.weight for e in persona.mix if e.weight > 0) or 1.0
    return [(e.app, max(e.weight, 0.0) / total) for e in persona.mix]


def estimate_mbps(lines: list[Any], personas: dict[str, Any], apps: dict[str, Any]) -> float:
    bits = 0.0
    for ln in lines:
        p = personas.get(ln.mix)
        if p is None or not p.mix:
            continue
        for app_id, share in mix_shares(p):
            a = apps.get(app_id)
            if a is not None:
                bits += ln.rate_cps * share * app_bytes(a) * 8 * _WIRE_OVERHEAD
    return bits / 1e6


def check_lines(
    spec: Any,
    twin: TwinView,
    personas: dict[str, Any],
    apps: dict[str, Any],
    *,
    generator_image: Optional[str],
) -> list[Finding]:
    out: list[Finding] = []
    # TGN-060: an app mix never plays on a VM.
    for i, a in enumerate(spec.assignments):
        p = personas.get(a.persona)
        if p is not None and p.mix:
            out.append(make_finding("TGN-060", index=i, persona_id=a.persona))
    if not spec.lines and not spec.emulated:
        return out

    if spec.lines and not generator_image:
        out.append(make_finding("TGN-070"))

    # TGN-030: every name resolves.
    emulated = {e.group: e for e in spec.emulated}
    for i, e in enumerate(spec.emulated):
        if e.external:
            if e.group in twin.groups:
                out.append(make_finding("TGN-030", where=f"emulated[{i}].group", name=e.group,
                                        why="an external group may not reuse a te group's name"))
            if e.segment not in twin.segments:
                out.append(make_finding("TGN-030", where=f"emulated[{i}].segment", name=e.segment,
                                        why="no such segment in the twin"))
        else:
            g = twin.groups.get(e.group)
            if g is None:
                out.append(make_finding("TGN-030", where=f"emulated[{i}].group", name=e.group,
                                        why="no te node group of that name (or mark it external: true)"))
            elif g.kind != "host" or len(g.segments) != 1:
                out.append(make_finding("TGN-030", where=f"emulated[{i}].group", name=e.group,
                                        why="only a single-segment host group can have emulated instances"))
    for i, ln in enumerate(spec.lines):
        for end, name in (("from", ln.from_group), ("to", ln.to_group)):
            if name not in emulated and name not in twin.groups:
                out.append(make_finding("TGN-030", where=f"lines[{i}].{end}", name=name,
                                        why="neither a te node group nor an emulated[] group"))
        p = personas.get(ln.mix)
        if p is None or not p.mix or p.drive != "trex-astf":
            out.append(make_finding("TGN-030", where=f"lines[{i}].mix", name=ln.mix,
                                    why="not an app mix (a trex-astf persona with mix:)"))
            p = None
        # TGN-010: TRex sends only from emulated instances.
        if ln.from_group not in emulated and ln.from_group in twin.groups:
            out.append(make_finding("TGN-010", index=i, group=ln.from_group))
        # TGN-020: no line to a real server in this phase.
        if ln.to_group not in emulated and ln.to_group in twin.groups:
            bad = [a for a, _ in mix_shares(p) if not getattr(apps.get(a), "real_server_ok", False)] if p else []
            out.append(make_finding(
                "TGN-020", index=i, group=ln.to_group,
                apps=", ".join(bad) if bad else "none (all are real_server_ok)",
            ))

    # TGN-040: the budget.
    if spec.lines and spec.budget is not None:
        cps = sum(ln.rate_cps for ln in spec.lines)
        if cps > spec.budget.cps:
            out.append(make_finding("TGN-040", what="connections/s", planned=f"{cps:g}", budget=f"{spec.budget.cps:g}"))
        mbps = estimate_mbps(spec.lines, personas, apps)
        if mbps > spec.budget.mbps:
            out.append(make_finding("TGN-040", what="Mbps (estimated)", planned=f"{mbps:.1f}", budget=f"{spec.budget.mbps:g}"))

    # TGN-050: addresses -- never a real one, and the segment has room.
    bad_start: set[str] = set()
    for e in spec.emulated:
        seg = twin.segments.get(group_segment(twin, e) or "")
        if e.start and seg is not None and ipaddress.IPv4Address(e.start) not in seg.cidr:
            bad_start.add(e.group)
            out.append(make_finding("TGN-050", group=e.group,
                                    detail=f"start {e.start} is outside {seg.name} ({seg.cidr})"))
    resolvable = [e for e in spec.emulated
                  if e.group not in bad_start and group_segment(twin, e) in twin.segments
                  and (e.external or (e.group in twin.groups and len(twin.groups[e.group].segments) == 1))]
    try:
        placed = allocate(twin, resolvable, Allocator(twin))
    except AllocationError as ex:
        out.append(make_finding("TGN-050", group=ex.group, detail=str(ex)))
    else:
        real = twin.real_ips()
        for g, insts in placed.items():
            clash = sorted(i.ip for i in insts if i.ip in real)
            if clash:
                out.append(make_finding("TGN-050", group=g, detail=f"{', '.join(clash)} already belong to the twin"))
    return out


class LinesContextError(ValueError):
    """The profile has lines but no twin to check them against."""


def check_profile_file(spec: Any, spec_path: Any, *, platform: Optional[str]) -> list[Finding]:
    """`check_lines` for a profile file inside a twin folder
    (`twins/<id>/tfc/<name>.yaml`): the twin's te file (read-only), the
    catalog personas plus the twin's own, the installed apps, and the
    generator image for `platform`. A profile with nothing trafficgen-shaped
    returns []; one with lines or emulated[] outside a twin folder raises
    `LinesContextError`."""
    from axrange.personas.registry import PERSONAS, twin_overlay
    from axrange.trafficgen.apps import list_apps
    from axrange.trafficgen.image import generator_image
    from axrange.twinconfig import TwinConfigError
    from axrange.twinconfig.manifest import load_twin_manifest, twin_dir_for_file

    personas = dict(PERSONAS)
    twin_dir = twin_dir_for_file(spec_path)
    if twin_dir is not None:
        personas.update(twin_overlay(twin_dir / "personas"))
    if not spec.lines and not spec.emulated and not any(
        getattr(personas.get(a.persona), "mix", ()) for a in spec.assignments
    ):
        return []
    if twin_dir is None:
        raise LinesContextError(
            f"{spec_path}: emulated[]/lines[] need the profile inside a twin folder (twins/<id>/tfc/)")
    try:
        te_path = load_twin_manifest(twin_dir).te_path
    except TwinConfigError as e:
        raise LinesContextError(f"{twin_dir}: {e}") from e
    return check_lines(spec, TwinView.from_te_path(te_path), personas, list_apps()[0],
                       generator_image=generator_image(platform))
