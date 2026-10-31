"""LibvirtInventory -- the `Inventory` seam over one libvirt connection.

`adapters/libvirt/SPEC.md` Seam table: `listAllDomains` filtered by the
tier-tagged name, `interfaceAddresses` (agent source, lease fallback) for
guest -> IP, `getVersion` / `getInfo` / `getHostname` for host facts,
external sensors from config. The `guest_*` reads NEVER raise (an `info`
render prints what it knows when the host is down); the others raise
`AdapterError` so a health read can tell "unreachable" from "nothing there"
(`axrange.platform.protocols.Inventory`).
"""
from __future__ import annotations

import threading
from typing import Any, Callable, Optional

from axrange.adapter_libvirt import naming
from axrange.adapter_libvirt.connection import (
    VIR_DOMAIN_INTERFACE_ADDRESSES_SRC_AGENT,
    VIR_DOMAIN_INTERFACE_ADDRESSES_SRC_LEASE,
    LibvirtConnection,
)
from axrange.adapter_libvirt.guest import LibvirtGuestExec
from axrange.planner.executor import AdapterError, AdapterTimeout

__all__ = ["LibvirtInventory", "first_ipv4"]

_INFO_TIMEOUT_S = 5
_VIR_IP_ADDR_TYPE_IPV4 = 0


def first_ipv4(addresses: Any) -> str | None:
    """First non-loopback IPv4 in a `virDomainInterfaceAddresses` result
    (`{ifname: {"hwaddr": .., "addrs": [{"addr", "prefix", "type"}]}}`)."""
    if not isinstance(addresses, dict):
        return None
    for ifname, iface in addresses.items():
        if str(ifname).lower() in ("lo", "loopback") or not isinstance(iface, dict):
            continue
        for a in iface.get("addrs") or []:
            if not isinstance(a, dict) or a.get("type") != _VIR_IP_ADDR_TYPE_IPV4:
                continue
            ip = str(a.get("addr") or "")
            if ip and not ip.startswith("127."):
                return ip
    return None


def _bounded(fn: Callable[[], Any], timeout_s: float, what: str) -> Any:
    """`fn()` bounded by `timeout_s`: libvirt's python calls take no timeout,
    so an unreachable host would hang a health read. Runs `fn` on a daemon
    thread; past the budget `AdapterTimeout` is raised and the call is left to
    die with its connection."""
    box: dict[str, Any] = {}

    def run() -> None:
        try:
            box["ok"] = fn()
        except BaseException as e:  # noqa: BLE001 -- handed to the caller's thread
            box["err"] = e

    t = threading.Thread(target=run, daemon=True, name=f"libvirt-{what}")
    t.start()
    t.join(timeout_s)
    if t.is_alive():
        raise AdapterTimeout(f"libvirt {what}: no answer in {timeout_s}s")
    if "err" in box:
        raise box["err"]
    return box.get("ok")


def _is_tier_tagged(name: str) -> bool:
    return any(pred(name) for pred in naming.tier_predicates().values()) or naming.is_img(name)


class LibvirtInventory:
    def __init__(self, cfg: Any, *, client: Any = None) -> None:
        self.cfg = cfg
        self.client = client or LibvirtConnection(cfg)

    def is_configured(self) -> bool:
        return bool(getattr(self.cfg, "libvirt_uri", ""))

    # ---- best-effort reads (never raise) --------------------------------
    def _domain(self, guest: Any) -> Any | None:
        try:
            return self.client.domain_by_uuid(str(guest))
        except (AdapterError, AdapterTimeout, OSError):
            return None

    def guest_address(self, guest: int | str, *, host: Optional[str] = None) -> Optional[str]:
        dom = self._domain(guest)
        if dom is None:
            return None
        for src in (VIR_DOMAIN_INTERFACE_ADDRESSES_SRC_AGENT, VIR_DOMAIN_INTERFACE_ADDRESSES_SRC_LEASE):
            try:
                ip = first_ipv4(dom.interfaceAddresses(src, 0))
            except Exception:  # noqa: BLE001 -- libvirtError / agent down / stopped guest
                ip = None
            if ip:
                return ip
        return None

    def guest_host(self, guest: int | str) -> Optional[str]:
        return self.cfg.default_node if self._domain(guest) is not None else None

    # ---- raising reads --------------------------------------------------
    def platform_version(self, *, timeout_s: int = _INFO_TIMEOUT_S) -> dict[str, Any]:
        if not self.is_configured():
            raise AdapterError("adapter.conf has no LIBVIRT_URI")
        try:
            return _bounded(self.client.host_facts, timeout_s, "host facts")
        except AdapterError:
            raise
        except Exception as e:  # noqa: BLE001 -- libvirtError at the raw layer
            raise AdapterError(f"libvirt host facts: {e}", error_class="libvirt-transport") from e

    def live_guests(self, *, timeout_s: int = _INFO_TIMEOUT_S) -> list[dict[str, Any]]:
        """Every tier-tagged domain: `name`, `vmid` (the UUID handle -- the
        row key `api/service.py` reads), `node`, `status`."""
        if not self.is_configured():
            raise AdapterError("adapter.conf has no LIBVIRT_URI")
        try:
            doms = _bounded(self.client.all_domains, timeout_s, "listAllDomains")
        except AdapterError:
            raise
        except Exception as e:  # noqa: BLE001
            raise AdapterError(f"libvirt listAllDomains: {e}", error_class="libvirt-transport") from e
        rows: list[dict[str, Any]] = []
        for d in doms:
            name = d.name()
            if not _is_tier_tagged(name):
                continue
            rows.append({
                "name": name,
                "vmid": d.UUIDString(),
                "node": self.cfg.default_node,
                "status": "running" if d.isActive() else "stopped",
                "type": "qemu",
            })
        return rows

    def ping_guest(self, guest: int | str, *, host: Optional[str] = None, timeout_s: int = _INFO_TIMEOUT_S) -> None:
        LibvirtGuestExec(self.client, host or self.cfg.default_node).ping_once(guest, timeout_s=timeout_s)

    def external_sensor_facts(self, *, only_ids: Optional[set[str]] = None) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for ext in getattr(self.cfg, "external_sensors", None) or []:
            if only_ids is not None and ext.id not in only_ids:
                continue
            method = "rdp" if "windows" in (ext.type or "").lower() else "ssh"
            user = "Administrator" if method == "rdp" else "axrange"
            resolved_name = resolved_ip = None
            try:
                dom = self.client.domain_by_name(ext.id)
            except (AdapterError, AdapterTimeout, OSError):
                dom = None
            if dom is not None:
                resolved_name = dom.name()
                resolved_ip = self.guest_address(dom.UUIDString())
            access_ip = resolved_ip or ext.transport_ip
            out.append({
                "id": ext.id, "type": ext.type, "vmid": ext.vmid,
                "transport_ip": ext.transport_ip, "capture_iface": ext.capture_iface,
                "resolved_name": resolved_name, "resolved_ip": resolved_ip,
                "access_user": user, "access_password": self.cfg.default_password,
                "access_hint": f"rdp {access_ip}  (user={user})" if method == "rdp" else f"ssh {user}@{access_ip}",
            })
        return out
