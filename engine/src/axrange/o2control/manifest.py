"""The O2 surface manifest — the small standalone spec `axrange o2 up`
composes into an engine deploy (`docs/design/05-o2-surface.md`).

Reuses the ratified `sensors:` IR shape verbatim (same fields a scenario
author already writes under `axrange/ir/schema.py`'s `Sensor` model) plus an
optional `transport:` addressing stanza (a subset of `Network` fields). No
new schema is introduced — this module is just a loader/light-validator for
a document shaped like:

    surface:
      id: o2-surface            # optional; scenario id the composed IR gets
    transport:                  # optional; overrides the auto-synthesized
      cidr: 10.255.250.0/24     # sensor-transport network pass1_emit would
      egress: true              # otherwise create on its own (same defaults)
      dns: [1.1.1.1, 8.8.8.8]
    sensors:
      - id: o2-sensor
        type: tcpdump-sensor
        primary: true
    services:                   # optional (docs/WORK_ORDER_access_facts.md
      vmid: 102                 # Part B item 5) -- overrides adapter.conf's
      name: o2-services         # O2_SERVICES_VMID/_NAME/O2_<SVC>_PORT for
      ports:                    # THIS surface's `o2 info`/`o2 deploy` echo
        grafana: 3000

`compose.py` turns a `SurfaceManifest` into the actual composed IR dict fed
to the engine; this module only loads + sanity-checks the standalone file.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from axrange.sensors import DEFAULT_SENSOR_TYPE, is_known_type

if TYPE_CHECKING:
    from axrange.platform.protocols import PlatformConfig


class SurfaceManifestError(Exception):
    """Raised on a structurally invalid or unresolvable surface manifest."""


@dataclass
class SurfaceSensorSpec:
    """One `sensors:` entry — mirrors `axrange.ir.schema.Sensor`'s fields
    (a deliberate subset; anything else in the manifest passes through
    verbatim via `extra` so `compose.py` doesn't have to duplicate every
    field the IR's `Sensor` model already knows about)."""

    id: str
    type: str | None = None
    primary: bool = False
    image: str | None = None
    resources: dict[str, Any] | None = None
    export: dict[str, Any] | None = None
    rules: str | None = None
    captures: list[str] | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class SurfaceServicesSpec:
    """Optional `services:` stanza (`docs/WORK_ORDER_access_facts.md` Part B
    item 5) -- overrides adapter.conf's `O2_SERVICES_VMID`/`_NAME`/
    `O2_<SVC>_PORT` for THIS surface's `o2 info <manifest.yaml>` /
    `o2 deploy` echo. adapter.conf remains the resolution source for the
    STANDING surface's bare `o2 info` (item 5's own hedge)."""

    vmid: int | None = None
    name: str | None = None
    ports: dict[str, int] = field(default_factory=dict)


@dataclass
class SurfaceManifest:
    """The parsed, sanity-checked surface spec `compose.py` consumes."""

    surface_id: str = "o2-surface"
    # Optional overrides for the transport network `compose.py` emits
    # (a subset of `axrange.ir.schema.Network` fields: cidr/gateway/dns/
    # vlan/egress/id). `None` ⇒ let pass1_emit's own sensors-design Phase B
    # auto-synthesis (docs/design/06-taps-sensors.md) pick the defaults.
    transport: dict[str, Any] | None = None
    sensors: list[SurfaceSensorSpec] = field(default_factory=list)
    # Optional o2-services register override (Part B item 5); `None` ⇒ no
    # manifest-level override, adapter.conf's O2_SERVICES_* resolves alone.
    services: SurfaceServicesSpec | None = None


def default_surface_manifest() -> SurfaceManifest:
    """The manifest `axrange o2 up` uses when `--surface` is omitted: one
    primary `tcpdump-sensor`, the minimal useful standing surface."""
    return SurfaceManifest(
        surface_id="o2-surface",
        sensors=[
            SurfaceSensorSpec(id="o2-sensor", type=DEFAULT_SENSOR_TYPE, primary=True)
        ],
    )


def _sensor_from_dict(raw: dict[str, Any], *, index: int) -> SurfaceSensorSpec:
    if not isinstance(raw, dict):
        raise SurfaceManifestError(f"sensors[{index}] must be a mapping")
    sid = raw.get("id")
    if not sid or not isinstance(sid, str):
        raise SurfaceManifestError(f"sensors[{index}] missing a string 'id'")
    sensor_type = raw.get("type")
    if not is_known_type(sensor_type):
        raise SurfaceManifestError(
            f"sensors[{index}] ({sid!r}): unknown sensor type {sensor_type!r}"
        )
    known = {"id", "type", "primary", "image", "resources", "export", "rules", "captures"}
    return SurfaceSensorSpec(
        id=sid,
        type=sensor_type,
        primary=bool(raw.get("primary", False)),
        image=raw.get("image"),
        resources=raw.get("resources"),
        export=raw.get("export"),
        rules=raw.get("rules"),
        captures=raw.get("captures"),
        extra={k: v for k, v in raw.items() if k not in known},
    )


def parse_surface_manifest(raw: dict[str, Any]) -> SurfaceManifest:
    """Sanity-check an already-loaded manifest dict (used directly by tests;
    `load_surface_manifest` is the file-reading entry point)."""
    if not isinstance(raw, dict):
        raise SurfaceManifestError("surface manifest root must be a mapping")

    surface = raw.get("surface") or {}
    surface_id = (surface.get("id") if isinstance(surface, dict) else None) or "o2-surface"

    transport = raw.get("transport")
    if transport is not None and not isinstance(transport, dict):
        raise SurfaceManifestError("transport: must be a mapping")

    sensors_raw = raw.get("sensors") or []
    if not isinstance(sensors_raw, list) or not sensors_raw:
        raise SurfaceManifestError(
            "surface manifest must declare at least one sensors[] entry"
        )
    sensors = [_sensor_from_dict(s, index=i) for i, s in enumerate(sensors_raw)]

    ids = [s.id for s in sensors]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise SurfaceManifestError(f"duplicate sensor id(s) in sensors[]: {sorted(dupes)}")

    services = _services_from_dict(raw.get("services"))

    return SurfaceManifest(
        surface_id=surface_id, transport=transport, sensors=sensors, services=services
    )


def _services_from_dict(raw: Any) -> "SurfaceServicesSpec | None":
    """Parse the optional `services:` stanza (Part B item 5). `None` if the
    key is absent; a `SurfaceServicesSpec` (possibly with everything left at
    its default) otherwise."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise SurfaceManifestError("services: must be a mapping")
    vmid = raw.get("vmid")
    if vmid is not None:
        try:
            vmid = int(vmid)
        except (TypeError, ValueError) as e:
            raise SurfaceManifestError(f"services.vmid must be an int, got {vmid!r}") from e
    name = raw.get("name")
    if name is not None and not isinstance(name, str):
        raise SurfaceManifestError("services.name must be a string")
    ports_raw = raw.get("ports") or {}
    if not isinstance(ports_raw, dict):
        raise SurfaceManifestError("services.ports must be a mapping")
    ports: dict[str, int] = {}
    for svc, port in ports_raw.items():
        try:
            ports[str(svc)] = int(port)
        except (TypeError, ValueError) as e:
            raise SurfaceManifestError(
                f"services.ports[{svc!r}] must be an int, got {port!r}"
            ) from e
    return SurfaceServicesSpec(vmid=vmid, name=name, ports=ports)


def load_surface_manifest(path: str | Path) -> SurfaceManifest:
    """Load + sanity-check a surface manifest YAML file. Raises
    `SurfaceManifestError` on a structural problem (missing/malformed file,
    no sensors, unknown sensor type, duplicate ids)."""
    from axrange.ir.parser import IncludeError, StructuralError, load_yaml

    p = Path(path)
    try:
        raw = load_yaml(p)
    except (IncludeError, StructuralError) as e:
        raise SurfaceManifestError(f"{p}: {e}") from e
    if raw is None:
        raw = {}
    return parse_surface_manifest(raw)


def resolve_manifest_endpoints(cfg: "PlatformConfig", manifest: SurfaceManifest) -> dict[str, str]:
    """The five O2 endpoint URLs for `manifest`, per
    `docs/WORK_ORDER_access_facts.md` Part B item 5.

    adapter.conf stays the resolution source for the STANDING surface's bare
    `o2 info` (`cfg.resolve_o2_endpoints()` alone); this is only for
    `o2 info <manifest.yaml>` / the `o2 deploy` echo, where a manifest's own
    `services:` stanza (if present) OVERRIDES adapter.conf's declared
    services VM for that one resolution -- an explicit `O2_<SVC>_URL` in
    adapter.conf still wins over either (unchanged per-service precedence,
    `AdapterConfig.resolve_o2_endpoints`'s own docstring).

    Kept a thin CLI-facing helper here (not a CLI function itself, per
    CLAUDE.md "keep o2control thin... the CLI stays render-only") so
    `cli/main.py` never has to know `AdapterConfig`'s resolution internals.
    """
    if manifest.services is None:
        return cfg.resolve_o2_endpoints()
    return cfg.resolve_o2_endpoints(
        services_vmid=manifest.services.vmid,
        services_name=manifest.services.name,
        service_ports=manifest.services.ports or None,
    )
