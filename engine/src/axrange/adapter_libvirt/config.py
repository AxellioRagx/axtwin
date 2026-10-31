"""LibvirtConfig -- `adapter.conf` for a libvirt target.

`adapters/libvirt/SPEC.md` "Config": the same file shape the Proxmox loader
reads (`KEY=value`, `#` comments, quotes, inline comments -- the grammar is
`adapter_proxmox.config._read_conf_file`, shared, not copied), with the
libvirt key set below. Unlike the Proxmox loader this one FAILS LOUD on a
key it does not know and on a missing required key, before any op runs
(`docs/CONVENTIONS.md` §4 "fail loud, fail closed"): pointing a libvirt
target at a Proxmox `adapter.conf` (or mistyping a key) is refused by name,
never silently half-loaded.

| Key | Required | Purpose |
|---|---|---|
| `LIBVIRT_URI` | yes | `qemu:///system`, `qemu+ssh://user@host/system`, ... |
| `LIBVIRT_STORAGE_POOL` | yes | pool holding the `img-*` template volumes and the per-deploy overlays + seed ISOs |
| `LIBVIRT_NETWORK_BACKEND` | no | `linux` (default) or `ovs`; a VLAN zone needs `ovs` (libvirt applies `<vlan>` tags to Open vSwitch ports only) |
| `HOST_SSH_HOST`, `HOST_SSH_USER`, `HOST_SSH_KEY` | when the URI is remote | the host backend's SSH (key PATH) for the seed ISO, gretap and `tc` work. `HOST_SSH_HOST` defaults to the URI's host and must equal it when both are set (one machine, not two); `HOST_SSH_KEY` is also handed to libvirt's own ssh transport as `?keyfile=` when a `qemu+ssh` URI carries none |
| `GUEST_SSH_PUBKEY_FILE` | yes | public key cloud-init injects into every Linux guest |
| `DEFAULT_NODE` | yes | the host's name as placement records it |
| `MIRROR_GRETAP_TXQUEUELEN` | no | deep-FIFO depth on each mirror gretap (default 20000; 0 leaves the host default) |
| `LIBVIRT_BINDINGS_DIR` | no | where `images/bindings/libvirt/<image>.yaml` live, for `clone-vm`'s template lookup when the planner did not resolve `template_volume` itself (see `bindings.py`) |
| `DEFAULT_PASSWORD`, `PROTECTED_EXTERNAL_NAMES`, `PROTECTED_EXTERNAL_VMIDS`, `EXTERNAL_SENSORS`, `LOKI_URL`, `NETWORK_SENSOR_VMID` | no | unchanged from the Proxmox file (the engine's `PlatformConfig` slice) |
| `PLATFORM` | no | `libvirt` -- the neutral key `axrange.platform.load_config` reads to pick this loader; refused here if it says anything else |

Precedence: `overrides` > environment (known keys only, except `PLATFORM`,
which is file / overrides only -- an env value would re-aim every target of a
multi-target serve at once; `platform/factory.py`) > file > defaults.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

# The conf-file grammar, the default search path and the EXTERNAL_SENSORS
# parser are platform-neutral helpers that happen to live in the Proxmox
# loader today; they are reused, not duplicated (the orchestrator's "lift
# what is pure" rule). `ExternalSensor` is the `ExternalSensorSpec` value
# type the engine reads (`vmid` on it is a version-locked contract field,
# `adapters/proxmox/SPEC.md` Notes).
from axrange.adapter_proxmox.config import (
    ExternalSensor,
    _parse_external_sensors,
    _read_conf_file,
    default_adapter_conf_paths,
)
from axrange.platform.protocols import ConfigError

__all__ = [
    "ConfigError",
    "ExternalSensor",
    "KNOWN_KEYS",
    "LibvirtConfig",
    "NETWORK_BACKENDS",
    "example_conf_text",
    "load_config",
    "uri_is_remote",
    "uri_with_keyfile",
]

NETWORK_BACKENDS = ("linux", "ovs")

#: Every key this loader accepts. Anything else in the file / overrides is a
#: `ConfigError` naming the key.
KNOWN_KEYS: frozenset[str] = frozenset({
    "PLATFORM",
    "LIBVIRT_URI",
    "LIBVIRT_STORAGE_POOL",
    "LIBVIRT_NETWORK_BACKEND",
    "LIBVIRT_BINDINGS_DIR",
    "HOST_SSH_HOST",
    "HOST_SSH_USER",
    "HOST_SSH_KEY",
    "GUEST_SSH_PUBKEY_FILE",
    "DEFAULT_NODE",
    "DEFAULT_PASSWORD",
    "MIRROR_GRETAP_TXQUEUELEN",
    "PROTECTED_EXTERNAL_NAMES",
    "PROTECTED_EXTERNAL_VMIDS",
    "EXTERNAL_SENSORS",
    "LOKI_URL",
    "NETWORK_SENSOR_VMID",
})

_REQUIRED_KEYS = ("LIBVIRT_URI", "LIBVIRT_STORAGE_POOL", "GUEST_SSH_PUBKEY_FILE", "DEFAULT_NODE")
_REMOTE_KEYS = ("HOST_SSH_HOST", "HOST_SSH_USER", "HOST_SSH_KEY")

_URI_SCHEMES = ("qemu", "qemu+ssh", "qemu+tcp", "qemu+tls", "qemu+unix", "test")


def uri_is_remote(uri: str) -> bool:
    """True when the libvirt URI names a host (`qemu+ssh://user@host/system`,
    `qemu+tcp://host/system`); `qemu:///system`, `qemu+unix:///system` and
    `test:///default` are local. Raises `ConfigError` on a scheme libvirt's
    QEMU driver does not speak."""
    parsed = urlparse(uri.strip())
    if parsed.scheme not in _URI_SCHEMES:
        raise ConfigError(
            f"LIBVIRT_URI {uri!r}: scheme {parsed.scheme or '(none)'!r} is not one of "
            f"{', '.join(_URI_SCHEMES)}"
        )
    return bool(parsed.hostname)


@dataclass
class LibvirtConfig:
    """The loaded `adapter.conf` of a libvirt target. Satisfies
    `axrange.platform.protocols.PlatformConfig` structurally (the engine
    reads `default_node`, `default_password`, `external_sensors`,
    `protected_external_*`); everything else stays behind the seam."""

    #: `PlatformConfig.platform`: the factory selects the adapter from it.
    platform: str = "libvirt"
    libvirt_uri: str = ""
    storage_pool: str = ""
    network_backend: str = "linux"

    host_ssh_host: str = ""
    host_ssh_user: str = ""
    host_ssh_key: str = ""

    guest_ssh_pubkey_file: str = ""
    default_node: str = ""
    default_password: str = "Axr4nge-Lab!"
    mirror_gretap_txqueuelen: int = 20000
    bindings_dir: str = ""

    protected_external_names: list[str] = field(default_factory=list)
    protected_external_vmids: list[int] = field(default_factory=list)
    external_sensors: list[ExternalSensor] = field(default_factory=list)
    # The engine's `PlatformConfig` slice `config_exec/backend.py` reads:
    # the activity truth shipper's Loki (empty: no shipping) and the
    # appliance-adjacent network sensor by platform identity (contract:
    # `adapters/proxmox/SPEC.md` Notes -- a VMID int; `0` is "none", and a
    # libvirt sensor would need a UUID handle here before deploy-network-
    # sensor can run on this platform).
    loki_url: str = ""
    network_sensor_vmid: int = 0

    # ---- the neutral facts `GET /api/targets` reports (`PlatformConfig`) ----
    @property
    def endpoint(self) -> str:
        return self.libvirt_uri or ""

    @property
    def verify_tls(self) -> bool:
        return self.libvirt_uri.startswith("qemu+tls://")

    @property
    def has_token(self) -> bool:
        return False  # libvirt authenticates by URI transport (SSH/TLS), not a token

    @property
    def has_ssh_key(self) -> bool:
        return bool(self.host_ssh_key)

    @property
    def is_remote(self) -> bool:
        """Whether the host backend must SSH (the URI names a host)."""
        return bool(self.libvirt_uri) and uri_is_remote(self.libvirt_uri)

    def expand_paths(self) -> "LibvirtConfig":
        for attr in ("host_ssh_key", "guest_ssh_pubkey_file", "bindings_dir"):
            v = getattr(self, attr)
            if v:
                setattr(self, attr, str(Path(v).expanduser()))
        return self


def uri_with_keyfile(uri: str, key_path: str) -> str:
    """`uri` with `?keyfile=<key_path>` added for a `qemu+ssh` URI that names
    no keyfile of its own, so libvirt's ssh transport uses the same operator
    key as the host backend. Any other URI, or an empty key, is returned
    unchanged."""
    parsed = urlparse(uri)
    if parsed.scheme != "qemu+ssh" or not key_path:
        return uri
    if any(part.split("=", 1)[0] == "keyfile" for part in parsed.query.split("&") if part):
        return uri
    extra = f"keyfile={quote(key_path, safe='/')}"
    return parsed._replace(query=f"{parsed.query}&{extra}" if parsed.query else extra).geturl()


def _validate(cfg: LibvirtConfig) -> None:
    missing = [
        k for k, v in (
            ("LIBVIRT_URI", cfg.libvirt_uri),
            ("LIBVIRT_STORAGE_POOL", cfg.storage_pool),
            ("GUEST_SSH_PUBKEY_FILE", cfg.guest_ssh_pubkey_file),
            ("DEFAULT_NODE", cfg.default_node),
        ) if not v
    ]
    if missing:
        raise ConfigError(
            f"libvirt adapter.conf: missing required key(s): {', '.join(missing)}"
        )
    if cfg.network_backend not in NETWORK_BACKENDS:
        raise ConfigError(
            f"LIBVIRT_NETWORK_BACKEND must be one of {NETWORK_BACKENDS}, "
            f"got {cfg.network_backend!r}"
        )
    if cfg.is_remote:  # raises ConfigError itself on a bad scheme
        uri_host = urlparse(cfg.libvirt_uri.strip()).hostname or ""
        if not cfg.host_ssh_host:
            cfg.host_ssh_host = uri_host
        elif cfg.host_ssh_host.lower() != uri_host.lower():
            raise ConfigError(
                f"HOST_SSH_HOST {cfg.host_ssh_host!r} is not the host of LIBVIRT_URI "
                f"{cfg.libvirt_uri!r} ({uri_host!r}): the host backend and libvirt must "
                "reach the same machine"
            )
        missing = [
            k for k, v in (
                ("HOST_SSH_HOST", cfg.host_ssh_host),
                ("HOST_SSH_USER", cfg.host_ssh_user),
                ("HOST_SSH_KEY", cfg.host_ssh_key),
            ) if not v
        ]
        if missing:
            raise ConfigError(
                f"LIBVIRT_URI {cfg.libvirt_uri!r} is remote; the host backend needs "
                f"{', '.join(missing)}"
            )


def load_config(
    config_path: str | Path | None = None,
    *,
    overrides: dict[str, Any] | None = None,
) -> LibvirtConfig:
    """Load a libvirt `LibvirtConfig`. Raises `ConfigError` (the one type every
    platform's loader raises) for an unknown key, a missing required key, a
    bad backend/URI, or a malformed `EXTERNAL_SENSORS`."""
    paths: list[Path] = [Path(config_path)] if config_path is not None else default_adapter_conf_paths()
    file_data: dict[str, str] = {}
    source: Path | None = None
    for p in paths:
        if p.is_file():
            file_data = _read_conf_file(p)
            source = p
            break

    unknown = sorted(k for k in file_data if k not in KNOWN_KEYS)
    if unknown:
        raise ConfigError(
            f"libvirt adapter.conf {source}: unknown key(s) {', '.join(unknown)} "
            f"(known: {', '.join(sorted(KNOWN_KEYS))})"
        )
    if overrides:
        bad = sorted(k for k in overrides if k not in KNOWN_KEYS)
        if bad:
            raise ConfigError(f"libvirt adapter config overrides: unknown key(s) {', '.join(bad)}")

    def get(key: str, default: str = "") -> str:
        if overrides and key in overrides and overrides[key] is not None:
            return str(overrides[key])
        env_val = os.environ.get(key) if key != "PLATFORM" else None   # never from the environment
        if env_val is not None:
            return env_val
        return file_data.get(key, default)

    try:
        qlen = int(get("MIRROR_GRETAP_TXQUEUELEN", "20000") or "20000")
    except ValueError as e:
        raise ConfigError(f"MIRROR_GRETAP_TXQUEUELEN must be an int: {e}") from e
    qlen = max(qlen, 0)

    protected_names = [n.strip() for n in get("PROTECTED_EXTERNAL_NAMES", "").split(",") if n.strip()]
    try:
        protected_vmids = [int(v.strip()) for v in get("PROTECTED_EXTERNAL_VMIDS", "").split(",") if v.strip()]
    except ValueError as e:
        raise ConfigError(f"PROTECTED_EXTERNAL_VMIDS must be a comma list of ints: {e}") from e
    external_sensors = _parse_external_sensors(get("EXTERNAL_SENSORS", ""))
    declared = get("PLATFORM", "libvirt")
    if declared != "libvirt":
        raise ConfigError(
            f"libvirt adapter.conf {source or ''}: PLATFORM={declared!r} is not this "
            "loader's platform (libvirt)"
        )
    try:
        sensor_vmid = int(get("NETWORK_SENSOR_VMID", "0") or "0")
    except ValueError as e:
        raise ConfigError(f"NETWORK_SENSOR_VMID must be an int: {e}") from e
    for ext in external_sensors:
        if ext.id not in protected_names:
            protected_names.append(ext.id)
        if ext.vmid not in protected_vmids:
            protected_vmids.append(ext.vmid)

    cfg = LibvirtConfig(
        libvirt_uri=get("LIBVIRT_URI", "").strip(),
        storage_pool=get("LIBVIRT_STORAGE_POOL", "").strip(),
        network_backend=(get("LIBVIRT_NETWORK_BACKEND", "linux") or "linux").strip().lower(),
        host_ssh_host=get("HOST_SSH_HOST", "").strip(),
        host_ssh_user=get("HOST_SSH_USER", "").strip(),
        host_ssh_key=get("HOST_SSH_KEY", "").strip(),
        guest_ssh_pubkey_file=get("GUEST_SSH_PUBKEY_FILE", "").strip(),
        default_node=get("DEFAULT_NODE", "").strip(),
        default_password=get("DEFAULT_PASSWORD", "Axr4nge-Lab!"),
        mirror_gretap_txqueuelen=qlen,
        bindings_dir=get("LIBVIRT_BINDINGS_DIR", "").strip(),
        loki_url=get("LOKI_URL", "").strip(),
        network_sensor_vmid=sensor_vmid,
        protected_external_names=protected_names,
        protected_external_vmids=protected_vmids,
        external_sensors=external_sensors,
    ).expand_paths()
    _validate(cfg)
    return cfg


def example_conf_text() -> str:
    """`config/adapter.conf.libvirt.example`'s contents."""
    return (
        "# axRange libvirt adapter configuration (adapters/libvirt/SPEC.md)\n"
        "# Copy to the path your target's `adapter_config:` names and fill in the values.\n"
        "# Same file shape as the Proxmox adapter.conf; UNKNOWN KEYS ARE REFUSED, so do\n"
        "# not point a libvirt target at a Proxmox file. Env vars of the same names\n"
        "# override this file (except PLATFORM, which only the file or an override sets).\n"
        "\n"
        "# The deploy platform this file configures: selects the adapter (and the\n"
        "# image bindings under images/bindings/<platform>/) for every command that\n"
        "# loads it. Required to be `libvirt` here; a file without it is read as a\n"
        "# Proxmox one.\n"
        "PLATFORM=libvirt\n"
        "\n"
        "# --- libvirt backend ---\n"
        "# Local host: qemu:///system. Remote host: qemu+ssh://user@host/system\n"
        "# (then HOST_SSH_* below are required for the host backend; HOST_SSH_HOST must\n"
        "# be the URI's host and defaults to it).\n"
        "LIBVIRT_URI=qemu:///system\n"
        "# Storage pool holding the img-* template volumes; per-deploy qcow2 overlays\n"
        "# and NoCloud seed ISOs are created in the same pool.\n"
        "LIBVIRT_STORAGE_POOL=default\n"
        "# linux (default): isolated libvirt networks on Linux bridges, one per segment.\n"
        "# ovs: Open vSwitch bridges; REQUIRED for a VLAN zone (libvirt only applies\n"
        "# <vlan> tags to OVS ports).\n"
        "# LIBVIRT_NETWORK_BACKEND=linux\n"
        "\n"
        "# --- host backend (seed ISO build, gretap + tc mirror) ---\n"
        "# Local URI: commands run as this process (needs sudo-less `ip`/`tc`/\n"
        "# `xorriso` rights, e.g. run axrange as root or via a sudo-capable user).\n"
        "# Remote URI: SSH with this key PATH (never key material). The key is also\n"
        "# passed to libvirt's own ssh transport as `?keyfile=<path>` when the\n"
        "# qemu+ssh URI has no keyfile parameter of its own.\n"
        "# HOST_SSH_HOST=kvm1.lab\n"
        "# HOST_SSH_USER=root\n"
        "# HOST_SSH_KEY=~/.ssh/kvm1_ed25519\n"
        "\n"
        "# --- guest cloud-init ---\n"
        "# Public key injected into every Linux guest's cloud-init user (required).\n"
        "GUEST_SSH_PUBKEY_FILE=~/.ssh/kvm1_ed25519.pub\n"
        "\n"
        "# --- placement ---\n"
        "# The host's name as the planner records placement (one host in v1).\n"
        "DEFAULT_NODE=kvm1\n"
        "\n"
        "# --- image bindings ---\n"
        "# Directory of images/bindings/libvirt/<image>.yaml (template volume name,\n"
        "# hardware defaults, optional domain XML template). Needed by clone-vm when\n"
        "# the plan does not carry `template_volume` itself.\n"
        "# LIBVIRT_BINDINGS_DIR=/path/to/images/bindings/libvirt\n"
        "\n"
        "# --- optional, unchanged from the Proxmox file ---\n"
        "# DEFAULT_PASSWORD=Axr4nge-Lab!\n"
        "# MIRROR_GRETAP_TXQUEUELEN=20000\n"
        "# PROTECTED_EXTERNAL_NAMES=XPress-SW\n"
        "# PROTECTED_EXTERNAL_VMIDS=250\n"
        "# EXTERNAL_SENSORS=[{\"id\":\"XPress-SW\",\"vmid\":250,\"transport_ip\":\"10.255.250.20\"}]\n"
        "# Loki for the activity truth shipper (empty: no shipping).\n"
        "# LOKI_URL=http://10.0.0.90:3100\n"
        "# The appliance-adjacent network sensor, by platform identity (a VMID today;\n"
        "# 0 = none -- deploy-network-sensor needs a libvirt handle before it can run here).\n"
        "# NETWORK_SENSOR_VMID=0\n"
    )
