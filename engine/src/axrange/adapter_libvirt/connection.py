"""LibvirtConnection -- the thin seam between the adapter and the python
`libvirt` bindings.

Every libvirt call the adapter makes goes through ONE object with the method
set below, so (a) `import libvirt` happens lazily, on first use, and only in
the real implementation -- the engine, the factory and the whole test suite
import this package on a box with no libvirt installed; (b) the suite
exercises every op against a fake that implements the same method set over
in-memory domains/networks/pools (`tests/test_adapter_libvirt.py`).

The domain / network / pool / volume OBJECTS handed back keep libvirt's own
method names (`name()`, `UUIDString()`, `isActive()`, `create()`, `destroy()`,
`undefineFlags()`, `XMLDesc()`, `interfaceAddresses()`, `setAutostart()`,
`update()`, `createXML()`, `createXMLFrom()`, `refresh()`, `info()`,
`resize()`, `delete()`, `path()`), so the real wrapper passes them through
untouched and a fake is a plain class with those names. The conn-level
LOOKUPS are wrapped because libvirt signals "absent" by raising
`libvirtError`, and check-then-act wants `None`.

Integer flag constants are spelled here (from `libvirt.h`) rather than read
off the module, for the same lazy-import reason.
"""
from __future__ import annotations

import json
import sys
from typing import Any, Callable, Optional, Protocol, runtime_checkable

from axrange.adapter_libvirt.config import uri_with_keyfile
from axrange.planner.executor import AdapterError, AdapterTimeout

__all__ = [
    "Connection",
    "LibvirtConnection",
    "VIR_ERR_NO_DOMAIN",
    "VIR_ERR_NO_NETWORK",
    "VIR_ERR_NO_STORAGE_POOL",
    "VIR_ERR_NO_STORAGE_VOL",
    "libvirt_call",
    "VIR_DOMAIN_INTERFACE_ADDRESSES_SRC_AGENT",
    "VIR_DOMAIN_INTERFACE_ADDRESSES_SRC_LEASE",
    "VIR_DOMAIN_UNDEFINE_MANAGED_SAVE",
    "VIR_DOMAIN_UNDEFINE_NVRAM",
    "VIR_DOMAIN_UNDEFINE_SNAPSHOTS_METADATA",
    "VIR_DOMAIN_XML_INACTIVE",
    "VIR_NETWORK_SECTION_PORTGROUP",
    "VIR_NETWORK_UPDATE_AFFECT_CONFIG",
    "VIR_NETWORK_UPDATE_AFFECT_LIVE",
    "VIR_NETWORK_UPDATE_COMMAND_ADD_LAST",
    "VIR_NETWORK_UPDATE_COMMAND_DELETE",
]

# libvirt.h -- virDomainXMLFlags / virDomainInterfaceAddressesSource /
# virDomainUndefineFlagsValues / virNetworkUpdateCommand / virNetworkUpdateSection
VIR_DOMAIN_XML_INACTIVE = 2
VIR_DOMAIN_INTERFACE_ADDRESSES_SRC_LEASE = 0
VIR_DOMAIN_INTERFACE_ADDRESSES_SRC_AGENT = 1
VIR_DOMAIN_UNDEFINE_MANAGED_SAVE = 1
VIR_DOMAIN_UNDEFINE_SNAPSHOTS_METADATA = 2
VIR_DOMAIN_UNDEFINE_NVRAM = 4
VIR_NETWORK_UPDATE_COMMAND_DELETE = 2
VIR_NETWORK_UPDATE_COMMAND_ADD_LAST = 3
VIR_NETWORK_SECTION_PORTGROUP = 9
VIR_NETWORK_UPDATE_AFFECT_LIVE = 1
VIR_NETWORK_UPDATE_AFFECT_CONFIG = 2

#: virErrorNumber values that mean "that object is not there" -- the only
#: errors a check-then-act lookup may turn into `None`. Any other
#: `libvirtError` (a dropped qemu+ssh connection, a permission failure) is NOT
#: absence and must surface, or a teardown would report a live domain gone.
VIR_ERR_NO_DOMAIN = 42
VIR_ERR_NO_NETWORK = 43
VIR_ERR_NO_STORAGE_POOL = 49
VIR_ERR_NO_STORAGE_VOL = 50
_NOT_FOUND_CODES = frozenset({
    VIR_ERR_NO_DOMAIN, VIR_ERR_NO_NETWORK, VIR_ERR_NO_STORAGE_POOL, VIR_ERR_NO_STORAGE_VOL,
})

#: `virDomainQemuAgentCommand` timeout sentinels (libvirt-qemu.h):
#: BLOCK = -2, DEFAULT = -1, NOWAIT = 0.
_AGENT_TIMEOUT_DEFAULT = -1


def libvirt_call(op: str, fn: Callable[..., Any], *args: Any) -> Any:
    """Run one mutating libvirt OBJECT method (`dom.create`, `net.update`,
    `pool.createXML`, `vol.resize`, ...), mapping a raw `libvirtError` to
    `AdapterError(error_class="libvirt-<op>")` so the executor classifies it
    like every other adapter failure. Lazy: with no `libvirt` module imported
    no `libvirtError` can exist, so nothing is imported here."""
    try:
        return fn(*args)
    except Exception as e:
        lv = sys.modules.get("libvirt")
        err = getattr(lv, "libvirtError", None)
        if isinstance(err, type) and isinstance(e, err):
            raise AdapterError(f"libvirt {op}: {e}", error_class=f"libvirt-{op}") from e
        raise


@runtime_checkable
class Connection(Protocol):
    """What the adapter, inventory, guest channel and teardown ask of a
    libvirt connection. The real `LibvirtConnection` and the test fake both
    satisfy it structurally."""

    def domain_by_uuid(self, uuid: str) -> Any | None: ...
    def domain_by_name(self, name: str) -> Any | None: ...
    def all_domains(self) -> list[Any]: ...
    def define_domain(self, xml: str) -> Any: ...
    def network_by_name(self, name: str) -> Any | None: ...
    def all_networks(self) -> list[Any]: ...
    def define_network(self, xml: str) -> Any: ...
    def pool_by_name(self, name: str) -> Any | None: ...
    def volume_by_name(self, pool_name: str, vol_name: str) -> Any | None: ...
    def pool_path(self, pool_name: str) -> str: ...
    def agent_command(self, domain: Any, command: dict[str, Any], timeout_s: int | None) -> dict[str, Any]: ...
    def host_facts(self) -> dict[str, Any]: ...


class LibvirtConnection:
    """The real connection: `libvirt.open(cfg.libvirt_uri)` on first use.

    `opener` / `error_type` are test seams (a fake raw connection and the
    exception class it raises for "absent"); production leaves both unset
    and the python `libvirt` module supplies them.
    """

    def __init__(
        self,
        cfg: Any,
        *,
        opener: Optional[Callable[[str], Any]] = None,
        error_type: Optional[type] = None,
        agent_command: Optional[Callable[[Any, str, int, int], str]] = None,
    ) -> None:
        self.cfg = cfg
        self._raw: Any = None
        self._opener = opener
        self._error_type = error_type
        self._agent_command = agent_command

    # ---- lazy open ----------------------------------------------------
    @property
    def raw(self) -> Any:
        if self._raw is None:
            uri = getattr(self.cfg, "libvirt_uri", "") or ""
            if not uri:
                raise AdapterError(
                    "LIBVIRT_URI is not set; populate adapter.conf", error_class="adapter-config",
                )
            # libvirt's own ssh transport uses the operator key too (HOST_SSH_KEY)
            uri = uri_with_keyfile(uri, getattr(self.cfg, "host_ssh_key", "") or "")
            if self._opener is not None:
                self._raw = self._opener(uri)
            else:
                lv = self._libvirt()
                try:
                    self._raw = lv.open(uri)
                except lv.libvirtError as e:
                    raise AdapterError(
                        f"libvirt open {uri!r}: {e}", error_class="libvirt-transport",
                    ) from e
        return self._raw

    @staticmethod
    def _libvirt() -> Any:
        try:
            import libvirt  # lazy: the bindings are a host-side package
        except ImportError as e:  # pragma: no cover - exercised on a real host only
            raise AdapterError(
                "the python libvirt bindings are not installed "
                "(dnf install python3-libvirt / apt install python3-libvirt, "
                "or pip install libvirt-python into the venv)",
                error_class="adapter-config",
            ) from e
        return libvirt

    @property
    def _err(self) -> type:
        if self._error_type is None:
            self._error_type = self._libvirt().libvirtError
        return self._error_type

    def _absent(self, fn: Callable[[], Any]) -> Any | None:
        """`fn()`, or None when libvirt says the object is not there (a
        not-found error code). Every other libvirt failure is a transport /
        driver problem and raises -- never "absent"."""
        try:
            return fn()
        except self._err as e:
            code = e.get_error_code() if hasattr(e, "get_error_code") else None
            if code in _NOT_FOUND_CODES:
                return None
            raise AdapterError(f"libvirt lookup: {e}", error_class="libvirt-transport") from e

    # ---- domains ------------------------------------------------------
    def domain_by_uuid(self, uuid: str) -> Any | None:
        return self._absent(lambda: self.raw.lookupByUUIDString(str(uuid)))

    def domain_by_name(self, name: str) -> Any | None:
        return self._absent(lambda: self.raw.lookupByName(name))

    def all_domains(self) -> list[Any]:
        return list(self.raw.listAllDomains(0))

    def define_domain(self, xml: str) -> Any:
        try:
            return self.raw.defineXML(xml)
        except self._err as e:
            raise AdapterError(f"libvirt defineXML: {e}", error_class="libvirt-define") from e

    # ---- networks -----------------------------------------------------
    def network_by_name(self, name: str) -> Any | None:
        return self._absent(lambda: self.raw.networkLookupByName(name))

    def all_networks(self) -> list[Any]:
        return list(self.raw.listAllNetworks(0))

    def define_network(self, xml: str) -> Any:
        try:
            return self.raw.networkDefineXML(xml)
        except self._err as e:
            raise AdapterError(f"libvirt networkDefineXML: {e}", error_class="libvirt-define") from e

    # ---- storage ------------------------------------------------------
    def pool_by_name(self, name: str) -> Any | None:
        return self._absent(lambda: self.raw.storagePoolLookupByName(name))

    def volume_by_name(self, pool_name: str, vol_name: str) -> Any | None:
        pool = self.pool_by_name(pool_name)
        if pool is None:
            return None
        return self._absent(lambda: pool.storageVolLookupByName(vol_name))

    def pool_path(self, pool_name: str) -> str:
        pool = self.pool_by_name(pool_name)
        if pool is None:
            raise AdapterError(
                f"libvirt storage pool {pool_name!r} is not defined on the host",
                error_class="adapter-config",
            )
        return pool_target_path(pool.XMLDesc(0))

    # ---- guest agent --------------------------------------------------
    def agent_command(self, domain: Any, command: dict[str, Any], timeout_s: int | None) -> dict[str, Any]:
        """`virDomainQemuAgentCommand` with the QGA JSON; the parsed reply."""
        payload = json.dumps(command)
        timeout = int(timeout_s) if timeout_s else _AGENT_TIMEOUT_DEFAULT
        if self._agent_command is not None:
            out = self._agent_command(domain, payload, timeout, 0)
        else:
            try:
                import libvirt_qemu  # lazy, beside `libvirt`
            except ImportError as e:  # pragma: no cover
                raise AdapterError(
                    "python libvirt bindings lack `libvirt_qemu` (guest agent commands)",
                    error_class="adapter-config",
                ) from e
            try:
                out = libvirt_qemu.qemuAgentCommand(domain, payload, timeout, 0)
            except self._err as e:
                msg = str(e)
                cls = "qga-timeout" if "timed out" in msg.lower() or "timeout" in msg.lower() else "qga-unreachable"
                if cls == "qga-timeout":
                    raise AdapterTimeout(f"guest agent {command.get('execute')}: {msg}") from e
                raise AdapterError(f"guest agent {command.get('execute')}: {msg}", error_class=cls) from e
        try:
            parsed = json.loads(out) if isinstance(out, (str, bytes)) else out
        except ValueError as e:
            raise AdapterError(f"guest agent returned malformed JSON: {out!r}", error_class="qga-json") from e
        return parsed if isinstance(parsed, dict) else {"return": parsed}

    # ---- host facts ---------------------------------------------------
    def host_facts(self) -> dict[str, Any]:
        raw = self.raw
        info = raw.getInfo()
        return {
            "hostname": raw.getHostname(),
            "hypervisor_version": raw.getVersion(),
            "libvirt_version": raw.getLibVersion(),
            "uri": getattr(self.cfg, "libvirt_uri", ""),
            "model": info[0] if info else None,
            "memory_mib": info[1] if info else None,
            "cpus": info[2] if info else None,
        }


def pool_target_path(pool_xml: str) -> str:
    """`<pool><target><path>` of a pool's XML."""
    import xml.etree.ElementTree as ET

    root = ET.fromstring(pool_xml)
    el = root.find("./target/path")
    if el is None or not (el.text or "").strip():
        raise AdapterError("libvirt pool XML carries no <target><path>", error_class="adapter-config")
    return el.text.strip()
