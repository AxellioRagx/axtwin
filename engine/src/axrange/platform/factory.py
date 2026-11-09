"""The closed platform factory — `load_config` / `adapter_for_config(cfg)`.

A deploy target names its `platform` (`api/Spec.md` A4) and this module
turns that name into one `PlatformAdapters` bundle of the five seams
(`protocols.py`). The platform TRAVELS WITH THE LOADED CONFIG: `load_config`
reads the adapter conf's neutral `PLATFORM=` key (`proxmox` when absent),
dispatches to that platform's loader and the loaded object carries
`cfg.platform`; `adapter_for_config(cfg)` reads it back. A target's own
`platform:` (the targets file, `ApiSettings.platform`) is a cross-check —
when both are present they must agree — never a second argument threaded
through every call site. It is a CLOSED SET, not a registry
(`api/Spec.md` "Not being built"; `docs/ARCHITECTURE.md` §13): no
decorators, no entry points, no self-registration. The set is `proxmox`
and `libvirt` (`adapters/libvirt/SPEC.md`, built 2026-10-30); VMware joins
when built; an unknown name is refused by name, never defaulted.

This is the ONE module under `axrange/` (beside the genuinely Proxmox-only
image-build and render modules) that imports `axrange.adapter_proxmox`,
and the ONLY one that imports `axrange.adapter_libvirt`
(`tests/test_platform_layering.py`). Every platform name is looked up on
its module AT CALL TIME, not bound at import: the suite's seams monkeypatch
`axrange.adapter_proxmox.ProxmoxAdapter` / `ProxmoxTeardown` /
`load_config` and `adapter_proxmox.resolve.resolve_vm_ip`, and those must
keep intercepting the engine's real call paths.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Optional

from axrange.platform.protocols import (
    ConfigError,
    GuestExec,
    Inventory,
    ResourceNamer,
    Teardown,
)

if TYPE_CHECKING:  # the ops seam lives in the planner; see protocols.py
    from axrange.planner.executor import Adapter

__all__ = [
    "DEFAULT_PLATFORM",
    "PLATFORMS",
    "PlatformAdapters",
    "UnknownPlatformError",
    "adapter_for",
    "adapter_for_config",
    "check_platform",
    "default_namer",
    "load_config",
    "namer_for",
    "platform_of",
]

#: The one neutral key every platform's `adapter.conf` may carry; the value
#: is a name from the closed set below. Read by `load_config` here (file and
#: `overrides` only, never the environment: an env `PLATFORM` would re-aim
#: every target of a multi-target serve at once).
PLATFORM_KEY = "PLATFORM"


class UnknownPlatformError(ValueError):
    """`platform` is not in the closed set. Carries the offending name."""

    def __init__(self, platform: Any) -> None:
        self.platform = platform
        super().__init__(
            f"unknown deploy platform {platform!r}; one of {sorted(PLATFORMS)}"
        )


class PlatformAdapters:
    """One target's five seams, built lazily on first use so a destroy never
    constructs an ops adapter and an `info` never opens a teardown.

    `client` is the platform's own connection object (Proxmox: a `RestClient`)
    — a TEST SEAM carried for the engine modules that accept an injected
    `rest_client` and hand it to an injected guest-agent factory; the engine
    never calls anything on it.
    """

    def __init__(
        self,
        platform: str,
        config: Any,
        builders: "_Builders",
        *,
        client: Any = None,
    ) -> None:
        self.platform = platform
        self.config = config
        self._b = builders
        self._client = client
        self._adapter: Optional["Adapter"] = None
        self._inventory: Optional[Inventory] = None
        self._teardown: Optional[Teardown] = None

    @property
    def client(self) -> Any:
        if self._client is None:
            self._client = self._b.client(self.config)
        return self._client

    @property
    def adapter(self) -> "Adapter":
        if self._adapter is None:
            self._adapter = self._b.adapter(self.config)
        return self._adapter

    @property
    def inventory(self) -> Inventory:
        if self._inventory is None:
            # Shares the bundle's connection (one HTTP path per process, the
            # same client the guest channel uses).
            self._inventory = self._b.inventory(self.config, self.client)
        return self._inventory

    @property
    def namer(self) -> ResourceNamer:
        return self._b.namer()

    @property
    def teardown(self) -> Teardown:
        if self._teardown is None:
            self._teardown = self._b.teardown(self.config)
        return self._teardown

    def guest_exec(self, host: str, *, os_family: str = "linux") -> GuestExec:
        """A guest-control channel on `host` for guests of `os_family`."""
        return self._b.guest_exec(self.config, self.client, host, os_family)


class _Builders:
    """How one platform builds each seam. Plain callables, filled in per
    platform below; nothing registers itself."""

    def __init__(
        self,
        *,
        load_config: Callable[..., Any],
        client: Callable[[Any], Any],
        adapter: Callable[[Any], "Adapter"],
        inventory: Callable[[Any, Any], Inventory],
        namer: Callable[[], ResourceNamer],
        teardown: Callable[[Any], Teardown],
        guest_exec: Callable[[Any, Any, str, str], GuestExec],
    ) -> None:
        self.load_config = load_config
        self.client = client
        self.adapter = adapter
        self.inventory = inventory
        self.namer = namer
        self.teardown = teardown
        self.guest_exec = guest_exec


# ---------------------------------------------------------------------------
# proxmox — `adapters/proxmox/SPEC.md`; code in `axrange.adapter_proxmox`.
# ---------------------------------------------------------------------------
def _px_load_config(path: Any = None, *, overrides: Optional[dict[str, Any]] = None) -> Any:
    import axrange.adapter_proxmox as px

    # `overrides=` only when given: the suite's `load_config` seams take a
    # bare path.
    if overrides is None:
        return px.load_config(path)
    return px.load_config(path, overrides=overrides)


def _px_client(cfg: Any) -> Any:
    from axrange.adapter_proxmox import rest as px_rest

    return px_rest.RestClient(cfg)


def _px_adapter(cfg: Any) -> "Adapter":
    import axrange.adapter_proxmox as px

    return px.ProxmoxAdapter(cfg)


def _px_inventory(cfg: Any, client: Any) -> Inventory:
    from axrange.adapter_proxmox import resolve as px_resolve

    return px_resolve.ProxmoxInventory(cfg, client=client)


def _px_namer() -> ResourceNamer:
    from axrange.adapter_proxmox import naming as px_naming

    return px_naming


def _px_teardown(cfg: Any) -> Teardown:
    import axrange.adapter_proxmox as px

    return px.ProxmoxTeardown(cfg)


def _px_guest_exec(cfg: Any, client: Any, host: str, os_family: str) -> GuestExec:
    from axrange.adapter_proxmox import guest_agent as px_ga

    return px_ga.GuestAgent(client, host, os_family=os_family)


_PROXMOX = _Builders(
    load_config=_px_load_config,
    client=_px_client,
    adapter=_px_adapter,
    inventory=_px_inventory,
    namer=_px_namer,
    teardown=_px_teardown,
    guest_exec=_px_guest_exec,
)


# ---------------------------------------------------------------------------
# libvirt -- `adapters/libvirt/SPEC.md`; code in `axrange.adapter_libvirt`.
# `client` is the lazily-opened `LibvirtConnection` the inventory and the
# guest channel share (one connection per bundle, as the Proxmox bundle
# shares one `RestClient`).
# ---------------------------------------------------------------------------
def _lv_load_config(path: Any = None, *, overrides: Optional[dict[str, Any]] = None) -> Any:
    import axrange.adapter_libvirt as lv

    if overrides is None:
        return lv.load_config(path)
    return lv.load_config(path, overrides=overrides)


def _lv_client(cfg: Any) -> Any:
    from axrange.adapter_libvirt import connection as lv_conn

    return lv_conn.LibvirtConnection(cfg)


def _lv_adapter(cfg: Any) -> "Adapter":
    import axrange.adapter_libvirt as lv

    return lv.LibvirtAdapter(cfg)


def _lv_inventory(cfg: Any, client: Any) -> Inventory:
    from axrange.adapter_libvirt import inventory as lv_inventory

    return lv_inventory.LibvirtInventory(cfg, client=client)


def _lv_namer() -> ResourceNamer:
    from axrange.adapter_libvirt import naming as lv_naming

    return lv_naming


def _lv_teardown(cfg: Any) -> Teardown:
    import axrange.adapter_libvirt as lv

    return lv.LibvirtTeardown(cfg)


def _lv_guest_exec(cfg: Any, client: Any, host: str, os_family: str) -> GuestExec:
    from axrange.adapter_libvirt import guest as lv_guest

    return lv_guest.LibvirtGuestExec(client, host, os_family=os_family)


_LIBVIRT = _Builders(
    load_config=_lv_load_config,
    client=_lv_client,
    adapter=_lv_adapter,
    inventory=_lv_inventory,
    namer=_lv_namer,
    teardown=_lv_teardown,
    guest_exec=_lv_guest_exec,
)


# ---------------------------------------------------------------------------
# The closed set. ONE place: `PLATFORMS` is derived from it, nothing else
# lists platform names. VMware is added here when `adapters/vmware/` has
# code (its SPEC is the prerequisite list).
# ---------------------------------------------------------------------------
_BUILDERS: dict[str, _Builders] = {
    "proxmox": _PROXMOX,
    "libvirt": _LIBVIRT,
}

PLATFORMS: frozenset[str] = frozenset(_BUILDERS)
DEFAULT_PLATFORM = "proxmox"  # `api/Spec.md` A4: a target with no `platform:`
assert DEFAULT_PLATFORM in _BUILDERS


def check_platform(platform: Optional[str]) -> str:
    """The canonical platform name for a `platform` value — `DEFAULT_PLATFORM`
    for `None` (absent: a targets-file `platform:` with no value parses to
    None, a conf file without `PLATFORM=`), the name itself when it is in
    the closed set, `UnknownPlatformError` (naming it) otherwise. An empty
    or blank string is NOT absent: it is refused by name like any other
    unknown value. Used at targets-file load (serve start), on any target
    write, and by `load_config`."""
    if platform is None:
        return DEFAULT_PLATFORM
    if not isinstance(platform, str) or platform not in _BUILDERS:
        raise UnknownPlatformError(platform)
    return platform


def _builders(platform: str) -> _Builders:
    try:
        return _BUILDERS[platform]
    except (KeyError, TypeError):
        raise UnknownPlatformError(platform) from None


def _declared_platform_in(config_path: Any, overrides: Optional[dict[str, Any]]) -> Optional[str]:
    """The `PLATFORM=` value of the conf file `load_config` will read (the
    given path, else the first hit on the default search path), `overrides`
    winning; `None` when neither names one. The file grammar and the search
    path are the neutral helpers every loader shares (they live in the
    Proxmox loader today — the factory is allowed to reach them)."""
    if overrides and overrides.get(PLATFORM_KEY) is not None:
        return str(overrides[PLATFORM_KEY])
    from pathlib import Path

    from axrange.adapter_proxmox.config import _read_conf_file, default_adapter_conf_paths

    paths = [Path(config_path)] if config_path is not None else default_adapter_conf_paths()
    for p in paths:
        if p.is_file():
            # `PLATFORM=` with no value is "" here, which `check_platform`
            # refuses by name; only a missing key is "absent".
            return _read_conf_file(p).get(PLATFORM_KEY)
    return None


def load_config(
    config_path: Any = None,
    *,
    platform: Optional[str] = None,
    overrides: Optional[dict[str, Any]] = None,
) -> Any:
    """Load an adapter config (`config/adapter.conf` by default search path)
    through its platform's own loader, and stamp the platform on it.

    Which platform: the file's `PLATFORM=` key (`DEFAULT_PLATFORM` when the
    file has none), refused by name when unknown. `platform`, when given
    (a target's declared `platform:`), must equal the file's key if the file
    has one — a mismatch is a `ConfigError` naming both, so a libvirt conf
    behind a target declared `proxmox` fails before any op runs — and
    selects the loader when the file is silent. Raises `ConfigError` on a
    bad file, `UnknownPlatformError` on a bad platform.
    """
    declared = _declared_platform_in(config_path, overrides)
    if declared is not None:
        declared = check_platform(declared)
    if platform is not None:
        platform = check_platform(platform)
        if declared is not None and declared != platform:
            raise ConfigError(
                f"adapter config {config_path or '(default search path)'} says "
                f"{PLATFORM_KEY}={declared} but the target declares platform "
                f"{platform!r}; make them agree"
            )
    chosen = platform or declared or DEFAULT_PLATFORM
    cfg = _builders(chosen).load_config(config_path, overrides=overrides)
    stamped = getattr(cfg, "platform", None)
    if stamped is None:
        # A loader that does not carry `platform` (a test's fake config):
        # stamp it so `adapter_for_config` still works.
        try:
            cfg.platform = chosen
        except (AttributeError, TypeError):
            pass
    elif stamped != chosen:
        raise ConfigError(
            f"{chosen} adapter config loader returned a config for platform "
            f"{stamped!r}"
        )
    # The target's lab sensors (`docs/design/targets-and-lab.md` §2.1),
    # registered per adapter config at serve/CLI start: one merged
    # `external_sensors` list for every consumer.
    from axrange.platform.lab import merge_lab_sensors

    return merge_lab_sensors(cfg, config_path)


def platform_of(cfg: Any) -> str:
    """The platform a loaded config belongs to: `cfg.platform`, stamped by
    `load_config` / each adapter's config type, refused by name when unknown.
    A config object that carries no `platform` at all (the suite's bare
    `SimpleNamespace` / `object()` stand-ins) is "absent", the same as a conf
    file without `PLATFORM=`: the default platform, exactly as
    `check_platform(None)`."""
    return check_platform(getattr(cfg, "platform", None))


def adapter_for_config(cfg: Any, *, client: Any = None) -> PlatformAdapters:
    """The five seams for a loaded config, keyed by its own `cfg.platform`.
    This is how every engine call site selects the adapter; `adapter_for`
    below is the explicit form for a caller that already holds the name."""
    return adapter_for(platform_of(cfg), cfg, client=client)


def adapter_for(platform: str, cfg: Any, *, client: Any = None) -> PlatformAdapters:
    """The five seams for `platform` over its loaded config `cfg`. Fails by
    name on a platform outside the closed set, and with a `ConfigError` when
    `cfg` carries a different `platform` than asked for."""
    builders = _builders(platform)
    carried = getattr(cfg, "platform", None)
    if carried is not None and carried != platform:
        raise ConfigError(
            f"adapter_for({platform!r}) given a config for platform {carried!r}"
        )
    return PlatformAdapters(platform, cfg, builders, client=client)


def namer_for(platform: str) -> ResourceNamer:
    """`platform`'s `ResourceNamer` alone — naming needs no config."""
    return _builders(platform).namer()


def default_namer() -> ResourceNamer:
    """The default platform's namer, for engine modules that derive names
    before any target is in hand (`planner/inventory.py`, `planner/
    teardown.py`, `o2control/taps.py`)."""
    return namer_for(DEFAULT_PLATFORM)

