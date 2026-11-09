"""A target's lab: the O2 components standing beside every twin on it
(`docs/design/targets-and-lab.md` §2.1).

A **component** is one lab member: an `id` (unique within the target; what a
plan names), a closed `role` (`sensor | telemetry | attack | defence`), a
`type` closed per role (the product), a `reach` (sensor: `vmid`,
`transport_ip`, `capture_iface`; the rest: `url`), an optional `health`
probe, `managed` (`external` only in v1: axRange never provisions,
reconfigures or destroys one) and, for a sensor, `protected`.

The lab is declared per target in the targets file. Its sensors are the
engine's EXTERNAL sensors: `register_lab` keys them by the target's
`adapter_config`, and `axrange.platform.load_config` folds them into the
loaded config's `external_sensors` (and the teardown guards), so every
consumer -- tap resolution, `Inventory.external_sensor_facts`, the protect
guards -- reads one merged list without knowing where an entry came from.

One-release fallback: `EXTERNAL_SENSORS` in adapter.conf is still read; a
sensor id declared in BOTH places is a `ConfigError` naming it, which
`axrange serve --targets` reports at start.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from axrange.platform.protocols import ConfigError

#: The closed role set, in the deck's order (Sensors, Telemetry, Attack,
#: Defence) -- the console groups by it.
ROLES: tuple[str, ...] = ("sensor", "telemetry", "attack", "defence")

#: The closed type set per role. An unknown type is refused by name at serve
#: start, like an unknown `platform`.
TYPES_BY_ROLE: dict[str, tuple[str, ...]] = {
    "sensor": ("zeek", "suricata", "tcpdump", "packetxpress"),
    "telemetry": ("grafana", "prometheus", "loki"),
    "attack": ("caldera",),
    "defence": ("wazuh",),
}

#: `managed:` values. v1 knows only operator-managed components.
MANAGED: tuple[str, ...] = ("external",)

#: The default HTTP health path per `url` type, used when a component
#: declares no `health:` (step 2's probe reads it).
DEFAULT_HEALTH_HTTP: dict[str, str] = {
    "grafana": "/api/health",
    "prometheus": "/-/ready",
    "loki": "/ready",
    "caldera": "/api/v2/health",
    "wazuh": "/",
}

_COMPONENT_KEYS = {"id", "role", "type", "reach", "health", "managed", "protected"}
_SENSOR_REACH_KEYS = {"vmid", "transport_ip", "capture_iface"}
_URL_REACH_KEYS = {"url"}
_HEALTH_KEYS = {"http"}


@dataclass(frozen=True)
class LabSensor:
    """A lab sensor as the engine's external-sensor seam reads it
    (`ExternalSensorSpec`: id, vmid, transport_ip, type, capture_iface)."""

    id: str
    vmid: int
    transport_ip: str
    type: str
    capture_iface: str = "gre-cap"


@dataclass(frozen=True)
class LabComponent:
    id: str
    role: str
    type: str
    #: As declared (sensor: vmid/transport_ip/capture_iface; others: url).
    #: Never a credential -- the lab block has none, and `GET /api/targets`
    #: publishes this verbatim.
    reach: dict[str, Any] = field(default_factory=dict, hash=False)
    #: The declared probe (`{"http": "/path"}`), or None for the type default.
    health: Optional[dict[str, Any]] = field(default=None, hash=False)
    managed: str = "external"
    protected: bool = False

    @property
    def url(self) -> Optional[str]:
        u = self.reach.get("url")
        return str(u) if u else None

    @property
    def health_http(self) -> Optional[str]:
        """The HTTP path the health probe GETs on `url`: the declared one,
        else the type default, else None (no probe -> status unknown)."""
        if self.health and self.health.get("http"):
            return str(self.health["http"])
        return DEFAULT_HEALTH_HTTP.get(self.type)

    def as_sensor(self) -> LabSensor:
        if self.role != "sensor":
            raise ValueError(f"lab component {self.id!r} is a {self.role}, not a sensor")
        return LabSensor(
            id=self.id, vmid=int(self.reach["vmid"]),
            transport_ip=str(self.reach["transport_ip"]), type=self.type,
            capture_iface=str(self.reach.get("capture_iface") or "gre-cap"),
        )


@dataclass(frozen=True)
class Lab:
    components: tuple[LabComponent, ...] = ()

    @property
    def sensors(self) -> tuple[LabComponent, ...]:
        return tuple(c for c in self.components if c.role == "sensor")

    def component(self, cid: str) -> Optional[LabComponent]:
        return next((c for c in self.components if c.id == cid), None)


def parse_lab(raw: Any, *, where: str) -> Lab:
    """Parse a target entry's `lab:` value. Raises `ConfigError` naming the
    offending path (`<where>.lab.components[i].<key>`) and value."""
    if raw is None:
        return Lab()
    if not isinstance(raw, dict):
        raise ConfigError(f"{where}.lab must be a mapping with a 'components:' list")
    unknown = sorted(set(raw) - {"components"})
    if unknown:
        raise ConfigError(f"{where}.lab: unknown key(s) {unknown}")
    comps = raw.get("components") or []
    if not isinstance(comps, list):
        raise ConfigError(f"{where}.lab.components must be a list")
    out: list[LabComponent] = []
    seen: set[str] = set()
    for i, c in enumerate(comps):
        at = f"{where}.lab.components[{i}]"
        out.append(_parse_component(c, at=at, seen=seen))
    return Lab(components=tuple(out))


def _parse_component(c: Any, *, at: str, seen: set[str]) -> LabComponent:
    if not isinstance(c, dict):
        raise ConfigError(f"{at} must be a mapping")
    unknown = sorted(set(c) - _COMPONENT_KEYS)
    if unknown:
        raise ConfigError(f"{at}: unknown key(s) {unknown}")
    cid = c.get("id")
    if not isinstance(cid, str) or not cid.strip():
        raise ConfigError(f"{at}.id must be a non-empty string")
    cid = cid.strip()
    at = f"{at} ({cid})"
    if cid in seen:
        raise ConfigError(f"{at}: duplicate component id {cid!r} in this lab")
    seen.add(cid)
    role = c.get("role")
    if role not in ROLES:
        raise ConfigError(
            f"{at}.role: unknown role {role!r} -- one of {', '.join(ROLES)}"
        )
    ctype = c.get("type")
    if ctype not in TYPES_BY_ROLE[role]:
        raise ConfigError(
            f"{at}.type: unknown {role} type {ctype!r} -- one of "
            f"{', '.join(TYPES_BY_ROLE[role])}"
        )
    managed = c.get("managed", "external")
    if managed not in MANAGED:
        raise ConfigError(
            f"{at}.managed: {managed!r} is not supported -- v1 components are "
            f"'external' only (axRange never provisions or destroys one)"
        )
    protected = c.get("protected", False)
    if not isinstance(protected, bool):
        raise ConfigError(f"{at}.protected must be true or false")
    if protected and role != "sensor":
        raise ConfigError(f"{at}.protected applies to sensors only")
    reach = c.get("reach")
    if not isinstance(reach, dict):
        raise ConfigError(f"{at}.reach must be a mapping")
    reach = dict(reach)
    if role == "sensor":
        unknown = sorted(set(reach) - _SENSOR_REACH_KEYS)
        if unknown:
            raise ConfigError(f"{at}.reach: unknown key(s) {unknown} for a sensor")
        missing = [k for k in ("vmid", "transport_ip") if k not in reach]
        if missing:
            raise ConfigError(f"{at}.reach: missing {', '.join(missing)}")
        if isinstance(reach["vmid"], bool) or not isinstance(reach["vmid"], int):
            raise ConfigError(f"{at}.reach.vmid must be an integer, got {reach['vmid']!r}")
        tip = reach["transport_ip"]
        if not isinstance(tip, str) or not tip.strip():
            raise ConfigError(
                f"{at}.reach.transport_ip must not be blank -- a blank remote would "
                "attach a gretap to nowhere"
            )
        reach["transport_ip"] = tip.strip()
        reach.setdefault("capture_iface", "gre-cap")
        if not isinstance(reach["capture_iface"], str) or not reach["capture_iface"].strip():
            raise ConfigError(f"{at}.reach.capture_iface must be an interface name")
        if c.get("health") is not None:
            raise ConfigError(
                f"{at}.health: a sensor's health comes from its tap metrics or "
                "transport reachability, not an HTTP probe"
            )
        health = None
    else:
        unknown = sorted(set(reach) - _URL_REACH_KEYS)
        if unknown:
            raise ConfigError(f"{at}.reach: unknown key(s) {unknown} for a {role}")
        url = reach.get("url")
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            raise ConfigError(f"{at}.reach.url must be an http(s) URL, got {url!r}")
        _refuse_userinfo(url, at=f"{at}.reach.url")
        health = c.get("health")
        if health is not None:
            if not isinstance(health, dict) or set(health) - _HEALTH_KEYS:
                raise ConfigError(f"{at}.health must be {{http: /path}}")
            hp = health.get("http")
            if not isinstance(hp, str) or not hp.startswith("/"):
                raise ConfigError(f"{at}.health.http must be a path starting with '/'")
            health = dict(health)
    return LabComponent(
        id=cid, role=role, type=ctype, reach=reach, health=health,
        managed=managed, protected=protected,
    )


def _refuse_userinfo(url: str, *, at: str) -> None:
    """`GET /api/targets` publishes `reach` verbatim, so a URL carrying
    `user:pass@` would leak a credential: refused at load."""
    from urllib.parse import urlsplit

    if urlsplit(url).username is not None:
        raise ConfigError(
            f"{at}: must not carry credentials (user@ / user:pass@) -- the lab "
            "block is published on GET /api/targets"
        )


# ---- the registry `load_config` merges from ---------------------------------

#: adapter_config key -> the lab sensors of the target that names it. Filled
#: by `register_lab` (serve start, CLI start with AXRANGE_TARGETS); read by
#: `merge_lab_sensors`.
_LAB_SENSORS: dict[Optional[str], tuple[LabComponent, ...]] = {}


def _key(adapter_config: Any) -> Optional[str]:
    if adapter_config is None or adapter_config == "":
        return None
    return str(Path(str(adapter_config)).expanduser().resolve())


def register_lab(adapter_config: Any, lab: Lab) -> None:
    """Record `lab`'s sensors for the target reached through
    `adapter_config` (None: the default search path). Replaces any earlier
    registration for that key."""
    sensors = lab.sensors
    if sensors:
        _LAB_SENSORS[_key(adapter_config)] = sensors
    else:
        _LAB_SENSORS.pop(_key(adapter_config), None)


def clear_registry() -> None:
    _LAB_SENSORS.clear()


def lab_sensors_for(adapter_config: Any) -> tuple[LabComponent, ...]:
    return _LAB_SENSORS.get(_key(adapter_config), ())


def merge_lab_sensors(cfg: Any, adapter_config: Any) -> Any:
    """Fold the registered lab sensors for `adapter_config` into a loaded
    config: append to `external_sensors`; every lab sensor's vmid joins
    `protected_external_vmids` (`managed: external` -- axRange never destroys
    one), and a `protected: true` sensor's id joins
    `protected_external_names` (what `PROTECTED_EXTERNAL_NAMES` was). A
    sensor id also declared in adapter.conf's `EXTERNAL_SENSORS` is a
    `ConfigError` naming it. Mutates and returns `cfg`."""
    sensors = lab_sensors_for(adapter_config)
    if not sensors:
        return cfg
    existing = getattr(cfg, "external_sensors", None)
    if existing is None:
        raise ConfigError(
            f"the {getattr(cfg, 'platform', '?')} adapter config carries no "
            "external sensors, so the target's lab sensors "
            f"({', '.join(s.id for s in sensors)}) cannot be used on it"
        )
    conf_ids = {str(getattr(x, "id", "")) for x in existing}
    both = [s.id for s in sensors if s.id in conf_ids]
    if both:
        raise ConfigError(
            f"sensor id(s) {', '.join(repr(x) for x in both)} declared in both "
            f"EXTERNAL_SENSORS ({adapter_config or 'adapter.conf'}) and the "
            "target's lab -- remove it from EXTERNAL_SENSORS (the lab is "
            "authoritative; adapter.conf's list is read for one release only)"
        )
    names = getattr(cfg, "protected_external_names", None)
    vmids = getattr(cfg, "protected_external_vmids", None)
    for s in sensors:
        ls = s.as_sensor()
        existing.append(ls)
        if vmids is not None and ls.vmid not in vmids:
            vmids.append(ls.vmid)
        if s.protected and names is not None and ls.id not in names:
            names.append(ls.id)
    return cfg


def load_labs_from_targets_file(path: Any) -> list[tuple[Optional[str], Lab]]:
    """`(adapter_config, lab)` per entry of a targets file, for a process
    (the CLI) that needs the labs without the rest of the target machinery.
    Raises `ConfigError` on a malformed lab block."""
    from ruamel.yaml import YAML

    from axrange.ir.yaml_util import YamlLoadError, load_yaml_raw

    p = Path(str(path)).expanduser()
    if not p.is_file():
        raise ConfigError(f"targets file {p} (AXRANGE_TARGETS) does not exist")
    try:
        raw = load_yaml_raw(p, loader=YAML(typ="safe"))
    except YamlLoadError as e:
        raise ConfigError(f"targets file {p}: {e}") from e
    entries = raw.get("targets") if isinstance(raw, dict) else None
    out: list[tuple[Optional[str], Lab]] = []
    for i, e in enumerate(entries or []):
        if not isinstance(e, dict):
            continue
        name = e.get("name") or f"targets[{i}]"
        lab = parse_lab(e.get("lab"), where=f"targets file {p}: {name}")
        ac = e.get("adapter_config")
        out.append((str(Path(ac).expanduser()) if ac else None, lab))
    return out
