"""Best-effort live-resolution helpers -- VM display name / management IP /
vmid-by-name / node-by-vmid over the Proxmox REST API.

Used by `info` renders (`docs/WORK_ORDER_access_facts.md` Parts B/C/D): the
o2-services endpoint resolution, the `EXTERNAL_SENSORS` display, and a `te
info` host's access-fact IP fallback. Every helper here degrades to `None`
on ANY adapter failure (unreachable cluster, missing guest agent, a vmid
that's gone, `adapter.conf` left unconfigured) -- info rendering must
still print whatever it already knows (declared/persisted facts), never
crash because the cluster it's asking about is unreachable.

Every `client.request(...)` call below is wrapped in
`except (AdapterError, AdapterTimeout, OSError)` -- not just the first two.
`RestClient.request` only wraps urllib's CONNECT-phase failures
(`URLError`/`HTTPError`) into `AdapterError`; a READ-phase failure during
`resp.read()` against a reachable-but-hung host (`socket.timeout`,
`TimeoutError`, `ConnectionResetError` -- all bare `OSError` subclasses)
propagates unwrapped and would otherwise crash `te info`/`o2 info`/`tap
info` (code review SHOULD-FIX 1, 2026-07-25).

A short default timeout (`_INFO_TIMEOUT_S`) keeps an `info` command
responsive against a down/unreachable cluster -- these are best-effort
reads for a human waiting on a terminal, not a deploy-time op that can
afford the adapter's normal 60s REST timeout.
"""
from __future__ import annotations

from typing import Any

from axrange.adapter_proxmox.config import AdapterConfig
from axrange.adapter_proxmox.rest import RestClient, vm_status
from axrange.planner.executor import AdapterError, AdapterTimeout

_INFO_TIMEOUT_S = 5


def resolve_vm_name(
    cfg: AdapterConfig,
    vmid: int,
    *,
    node: str | None = None,
    client: RestClient | None = None,
) -> str | None:
    """The VM's live PVE display name, or `None` on any failure (unreachable
    cluster, vmid absent, `adapter.conf` not configured)."""
    node = node or cfg.default_node
    if not node or not cfg.proxmox_api_url:
        return None
    cl = client or RestClient(cfg)
    try:
        status = vm_status(cl, node, vmid, timeout_s=_INFO_TIMEOUT_S)
    except (AdapterError, AdapterTimeout, OSError):
        return None
    if not status:
        return None
    name = status.get("name")
    return name if isinstance(name, str) else None


def resolve_vm_ip(
    cfg: AdapterConfig,
    vmid: int,
    *,
    node: str | None = None,
    client: RestClient | None = None,
) -> str | None:
    """Best-effort live management IP.

    Tries qemu-guest-agent `network-get-interfaces` first (works for any
    running guest with QGA reachable), then falls back to the VM's static
    `ipconfigN` (works for a stopped guest, or one with no agent -- e.g. a
    VyOS router). Returns `None` -- NEVER raises -- on any failure.
    """
    node = node or cfg.default_node
    if not node or not cfg.proxmox_api_url:
        return None
    cl = client or RestClient(cfg)
    try:
        result = cl.request(
            "GET", f"/nodes/{node}/qemu/{vmid}/agent/network-get-interfaces",
            timeout_s=_INFO_TIMEOUT_S,
        )
        ip = _first_nonloopback_ipv4(result)
        if ip:
            return ip
    except (AdapterError, AdapterTimeout, OSError):
        pass
    try:
        body = cl.request(
            "GET", f"/nodes/{node}/qemu/{vmid}/config", timeout_s=_INFO_TIMEOUT_S
        )
    except (AdapterError, AdapterTimeout, OSError):
        return None
    return _static_ip_from_config(body)


def resolve_vmid_by_name(
    cfg: AdapterConfig,
    name: str,
    *,
    client: RestClient | None = None,
) -> tuple[int, str] | None:
    """Best-effort `(vmid, node)` for a VM's live PVE display name, via
    `/cluster/resources`. `None` on any failure or no match -- a
    services-VM/external-sensor declared by NAME (no vmid) resolves through
    this before its IP can be looked up."""
    if not name or not cfg.proxmox_api_url:
        return None
    for r in _cluster_vms(cfg, client):
        if r.get("name") != name:
            continue
        if r.get("vmid") is None:
            continue
        try:
            return int(r["vmid"]), str(r.get("node") or cfg.default_node)
        except (TypeError, ValueError):
            continue
    return None


def resolve_vm_node(
    cfg: AdapterConfig,
    vmid: int,
    *,
    client: RestClient | None = None,
) -> str | None:
    """The PVE node a VM id currently lives on, via `/cluster/resources`.
    `None` on any failure or no match -- the caller picks its own fallback
    (typically `DEFAULT_NODE`)."""
    if not cfg.proxmox_api_url:
        return None
    for r in _cluster_vms(cfg, client):
        try:
            if int(r.get("vmid")) == int(vmid) and r.get("node"):
                return str(r["node"])
        except (TypeError, ValueError):
            continue
    return None


def _cluster_vms(cfg: AdapterConfig, client: RestClient | None) -> list[dict[str, Any]]:
    """`/cluster/resources?type=vm`'s dict rows; `[]` on any failure."""
    try:
        return cluster_vm_resources(cfg, client=client)
    except (AdapterError, AdapterTimeout, OSError):
        return []


def cluster_vm_resources(
    cfg: AdapterConfig, *, client: RestClient | None = None,
    timeout_s: int = _INFO_TIMEOUT_S,
) -> list[dict[str, Any]]:
    """`/cluster/resources?type=vm`'s dict rows, RAISING on failure.

    The one caller that must tell "the sweep failed" from "the sweep found
    nothing" (a health read: an unreachable cluster is `unknown`, never
    "every VM absent") uses this; every best-effort `info` helper above
    goes through `_cluster_vms`, which swallows the error into `[]`.
    Raises `AdapterError` (also for an unconfigured `adapter.conf` and for
    a non-list answer)."""
    if not cfg.proxmox_api_url:
        raise AdapterError("adapter.conf has no PROXMOX_API_URL")
    cl = client or RestClient(cfg)
    resources = cl.request(
        "GET", "/cluster/resources", params={"type": "vm"}, timeout_s=timeout_s,
    )
    if not isinstance(resources, list):
        raise AdapterError(
            f"/cluster/resources answered {type(resources).__name__}, not a list"
        )
    return [r for r in resources if isinstance(r, dict)]


def qga_ping(
    cfg: AdapterConfig, vmid: int, *, node: str | None = None,
    client: RestClient | None = None, timeout_s: int = _INFO_TIMEOUT_S,
) -> None:
    """One qemu-guest-agent ping with a SHORT timeout (a health read; the
    deploy-time `GuestAgent.ping_once` uses the adapter's 60 s default).
    RAISES on no answer (see `cluster_vm_resources`)."""
    node = node or cfg.default_node
    if not node or not cfg.proxmox_api_url:
        raise AdapterError("adapter.conf has no PROXMOX_API_URL / node")
    from axrange.adapter_proxmox.guest_agent import GuestAgent

    # The ONE ping implementation (`GuestAgent.ping_once`, S10), with a
    # short timeout.
    GuestAgent(client or RestClient(cfg), node).ping_once(vmid, timeout_s=timeout_s)


def cluster_version(
    cfg: AdapterConfig, *, client: RestClient | None = None,
    timeout_s: int = _INFO_TIMEOUT_S,
) -> dict[str, Any]:
    """`GET /version` -- the cheapest authenticated read, used as a
    reachability probe. RAISES on any failure (see `cluster_vm_resources`)."""
    if not cfg.proxmox_api_url:
        raise AdapterError("adapter.conf has no PROXMOX_API_URL")
    cl = client or RestClient(cfg)
    out = cl.request("GET", "/version", timeout_s=timeout_s)
    return out if isinstance(out, dict) else {"raw": out}


def external_sensor_facts(
    cfg: AdapterConfig, *, only_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Resolve each declared `EXTERNAL_SENSORS` entry's live name + management
    IP (`docs/WORK_ORDER_access_facts.md` Part C), best-effort. Shared by the
    CLI (`cli.main.cmd_o2_info`/`cmd_tap_info`, thin `_external_sensor_facts`
    wrapper there) and the API (`axrange.api.service.o2_info`) so both
    surfaces show the IDENTICAL resolved shape -- one derivation, not two
    (LES-037).

    `only_ids`, when given, restricts resolution to those declared ids --
    `tap info`/a future tap-scoped API read pass the sensor ids actually
    BOUND in a plan (efficiency trim: resolving every declared entry
    regardless of whether it's used bounds each entry's own 5s best-effort
    stall against a hung cluster to only the ones that matter). `None`
    (unfiltered) is the surface-wide inventory view, where every declared
    sensor is in scope.

    axRange never provisions/reaches into one of these nodes -- `resolved_*`
    fields are `None` on ANY resolution failure (no crash, no partial
    inference); the declared fields (`vmid`/`transport_ip`/`type`/
    `capture_iface`) always come through regardless.
    """
    out: list[dict[str, Any]] = []
    for ext in cfg.external_sensors:
        if only_ids is not None and ext.id not in only_ids:
            continue
        method = "rdp" if "windows" in (ext.type or "").lower() else "ssh"
        user = "Administrator" if method == "rdp" else "axrange"
        resolved_name = resolve_vm_name(cfg, ext.vmid)
        resolved_ip = resolve_vm_ip(cfg, ext.vmid)
        access_ip = resolved_ip or ext.transport_ip
        hint = (
            f"rdp {access_ip}  (user={user})" if method == "rdp"
            else f"ssh {user}@{access_ip}"
        )
        out.append({
            "id": ext.id,
            "type": ext.type,
            "vmid": ext.vmid,
            "transport_ip": ext.transport_ip,
            "capture_iface": ext.capture_iface,
            "resolved_name": resolved_name,
            "resolved_ip": resolved_ip,
            "access_user": user,
            "access_password": cfg.default_password,
            "access_hint": hint,
        })
    return out


def _first_nonloopback_ipv4(result: Any) -> str | None:
    ifaces = result.get("result") if isinstance(result, dict) else None
    if not isinstance(ifaces, list):
        return None
    for iface in ifaces:
        if not isinstance(iface, dict):
            continue
        if (iface.get("name") or "").lower() in ("lo", "loopback"):
            continue
        for addr in iface.get("ip-addresses") or []:
            if not isinstance(addr, dict):
                continue
            if addr.get("ip-address-type") != "ipv4":
                continue
            ip = addr.get("ip-address")
            if ip and not str(ip).startswith("127."):
                return str(ip)
    return None


def _static_ip_from_config(body: Any) -> str | None:
    if not isinstance(body, dict):
        return None
    for key, val in body.items():
        if not (isinstance(key, str) and key.startswith("ipconfig") and isinstance(val, str)):
            continue
        for part in val.split(","):
            if part.startswith("ip=") and part != "ip=dhcp":
                ip = part[len("ip="):].split("/")[0]
                if ip:
                    return ip
    return None


class ProxmoxInventory:
    """`axrange.platform.protocols.Inventory` over this module's helpers —
    the inventory seam the factory hands the engine (`adapters/proxmox/
    SPEC.md` Seam table). Each method is a thin call to the module-level
    function of the same meaning, looked up at call time so the suite's
    `resolve_mod.resolve_vm_ip` seam keeps intercepting. `client` is the
    shared `RestClient` when the caller already has one (one HTTP path per
    process); `None` lets each read build the short-timeout default."""

    def __init__(self, cfg: AdapterConfig, *, client: RestClient | None = None) -> None:
        self.cfg = cfg
        self.client = client

    def is_configured(self) -> bool:
        return bool(self.cfg.proxmox_api_url)

    def guest_address(self, guest: int | str, *, host: str | None = None) -> str | None:
        return resolve_vm_ip(self.cfg, int(guest), node=host, client=self.client)

    def guest_host(self, guest: int | str) -> str | None:
        return resolve_vm_node(self.cfg, int(guest), client=self.client)

    def platform_version(self, *, timeout_s: int = _INFO_TIMEOUT_S) -> dict[str, Any]:
        return cluster_version(self.cfg, client=self.client, timeout_s=timeout_s)

    def live_guests(self, *, timeout_s: int = _INFO_TIMEOUT_S) -> list[dict[str, Any]]:
        return cluster_vm_resources(self.cfg, client=self.client, timeout_s=timeout_s)

    def ping_guest(
        self, guest: int | str, *, host: str | None = None, timeout_s: int = _INFO_TIMEOUT_S,
    ) -> None:
        qga_ping(self.cfg, int(guest), node=host, client=self.client, timeout_s=timeout_s)

    def external_sensor_facts(self, *, only_ids: set[str] | None = None) -> list[dict[str, Any]]:
        return external_sensor_facts(self.cfg, only_ids=only_ids)
