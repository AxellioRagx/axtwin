"""Compose a surface manifest into the engine-shaped IR dict `axrange
o2control` runs through the standard `build_plan_and_ctx` pipeline
(`docs/design/05-o2-surface.md`: "compose a 'surface deploy'
spec (sensors as nodes + transport subnet)").

The composition is deliberately thin: the engine's own sensors-design
Phase B emission (`axrange.planner.pass1_emit.emit_ops`, `docs/
design/06-taps-sensors.md`) already knows how to expand a `sensors:` block
into synthetic host nodes + an auto-provisioned transport vnet — that is
the "sensors as nodes + transport subnet" step, and o2control does not
reimplement it (LES-025/LES-037: reuse the engine, don't re-orchestrate
it). This module's only job is to wrap the surface manifest's `sensors:`
(+ an optional `transport:` override) in the minimal Scenario-IR envelope
`axrange.ir.parser.parse_ir`/`IR.model_validate` expects: a `scenario:`
header and an (empty, unless overridden) `layers.infra`.
"""
from __future__ import annotations

from typing import Any

from axrange.o2control.manifest import SurfaceManifest, SurfaceSensorSpec


def _sensor_dict(s: SurfaceSensorSpec) -> dict[str, Any]:
    d: dict[str, Any] = {"id": s.id, "primary": s.primary}
    if s.type:
        d["type"] = s.type
    if s.image:
        d["image"] = s.image
    if s.resources is not None:
        d["resources"] = s.resources
    if s.export is not None:
        d["export"] = s.export
    if s.rules:
        d["rules"] = s.rules
    if s.captures:
        d["captures"] = s.captures
    d.update(s.extra)
    return d


def compose_surface_ir(manifest: SurfaceManifest) -> dict[str, Any]:
    """Build the raw (pre-Pydantic) IR dict for a surface manifest.

    `layers.infra.networks`/`nodes` start EMPTY — no host of its own, no
    taps (A2's tap playout wires a deployed TE's taps to this surface's
    sensors later; the surface deploy itself carries none). If the manifest
    supplies a `transport:` override, it's emitted as one explicit
    `transport: true` network so pass1_emit's auto-synthesis step (which
    only fires when NO network is already marked `transport: true`) picks
    it up instead of minting its own defaults.
    """
    networks: list[dict[str, Any]] = []
    if manifest.transport:
        net = dict(manifest.transport)
        net.setdefault("id", "o2-transport")
        net["transport"] = True
        net.setdefault("egress", True)
        networks.append(net)

    return {
        "scenario_schema_version": 1,
        "scenario": {"id": manifest.surface_id, "version": 1},
        "layers": {
            "infra": {"networks": networks, "nodes": []},
            "apps": [],
            "activity": [],
        },
        "sensors": [_sensor_dict(s) for s in manifest.sensors],
        "bindings": [],
        "overlays": {},
    }
