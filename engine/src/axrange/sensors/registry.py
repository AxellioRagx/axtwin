"""The sensor-type catalog (docs/design/06-taps-sensors.md).

Each `type` maps to the tool it runs and the artifact it produces. The list is
**open**: new analyzers (arkime, ntopng, custom) are added here without touching
the IR schema, validator, or planner. v1 seeds the three the brief names.

Phase A: validation only (`is_known_type`). Phase B/C add per-type base images +
provisioning recipes + export wiring (the `recipe`/`image` fields are reserved
here so the registry is the one place that grows).
"""
from __future__ import annotations

from dataclasses import dataclass


# The generic image ref (id@version) a sensor clones when its type declares no
# more specific `default_image` (tcpdump is in Debian main → apt-installs at
# deploy time onto the plain generic image).
_GENERIC_IMAGE = "debian-12-cloud@v1"


@dataclass(frozen=True)
class SensorType:
    """One sensor type. `recipe`/`default_image` are reserved for Phase B."""

    name: str
    tool: str            # the capture/analysis program
    artifact: str        # what it emits (for the export/telemetry plane)
    description: str
    # The image ref (id@version) a sensor of this type clones when it declares no
    # `image:`. tcpdump is in Debian main → the generic image (apt-installs at
    # deploy). suricata lives in backports its scenario VM can't reach, so it has
    # a PRE-BAKED image (`axrange image build suricata-sensor`); deploy-sensor then
    # just starts the already-present tool (Phase C, docs/design/06-taps-sensors.md).
    default_image: str = _GENERIC_IMAGE
    recipe: str | None = None                # reserved: provisioning recipe id


# The default for a sensor with no explicit `type:` — the former standing capture appliance.
DEFAULT_SENSOR_TYPE = "tcpdump-sensor"


SENSOR_TYPES: dict[str, SensorType] = {
    "tcpdump-sensor": SensorType(
        name="tcpdump-sensor",
        tool="tcpdump",
        artifact="pcap",
        description="Full-fidelity packet capture to rotating pcaps (the former "
        "standing capture appliance; reuses o2/tcpdump-sensor/'s flow-gretap + "
        "tcpdump + watchdog stack).",
    ),
    "suricata-sensor": SensorType(
        name="suricata-sensor",
        tool="suricata",
        artifact="eve.json",
        description="Suricata IDS alerts (the former OP-OVL network-sensor "
        "network sensor; eve.json/fast.log, shippable to Loki).",
        default_image="suricata-sensor@v1",   # pre-baked (build it first)
    ),
    "zeek-sensor": SensorType(
        name="zeek-sensor",
        tool="zeek",
        artifact="logs",
        description="Zeek connection/protocol logs (conn/dns/http/...).",
    ),
}


def is_known_type(t: str | None) -> bool:
    """A sensor `type` is valid if it is known, or absent (defaults)."""
    return t is None or t in SENSOR_TYPES


def resolve_type(t: str | None) -> str:
    """The effective type for a sensor: explicit, or the default."""
    return t or DEFAULT_SENSOR_TYPE


def default_image_for(t: str | None) -> str:
    """The image ref a sensor of type `t` clones when it declares no `image:`."""
    st = SENSOR_TYPES.get(resolve_type(t))
    return st.default_image if st else _GENERIC_IMAGE
