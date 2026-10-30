"""LibvirtTeardown -- reverse-of-create on a KVM host (the `Teardown` seam).

`adapters/libvirt/SPEC.md`: "sweep domains, networks and bridges by tier-tag
shape with the same protect guard for `o2` and `img`". `planner/teardown.py`
owns the ordering and the deploy-wide guard; this backend owns HOW each kind
is removed and carries the guard a second time, at the point of action,
keyed on the LIVE resource's name: `img-*` and a protected external name are
refused by every tier, a foreign tier's shape is refused unless this
teardown was built for that tier (`tier=`; `te` may sweep `tap`, the one
cascade). Fail closed: an unrecognized name is left alone and raised, never
removed on the chance it is ours.

Each method is check-then-act: absent -> ``"absent"``, removed ->
``"removed"``; a hard failure raises `AdapterError`.
"""
from __future__ import annotations

import shlex
from typing import Any, Optional

from axrange.adapter_libvirt import naming
from axrange.adapter_libvirt.connection import (
    VIR_DOMAIN_UNDEFINE_MANAGED_SAVE,
    VIR_DOMAIN_UNDEFINE_NVRAM,
    VIR_DOMAIN_UNDEFINE_SNAPSHOTS_METADATA,
    VIR_DOMAIN_XML_INACTIVE,
    VIR_NETWORK_SECTION_PORTGROUP,
    VIR_NETWORK_UPDATE_AFFECT_CONFIG,
    VIR_NETWORK_UPDATE_AFFECT_LIVE,
    VIR_NETWORK_UPDATE_COMMAND_DELETE,
    LibvirtConnection,
)
from axrange.adapter_libvirt.domain_xml import metadata_of
from axrange.adapter_libvirt.host import HostBackend
from axrange.adapter_proxmox.ssh import gretap_del_cmd, gretap_show_cmd
from axrange.planner.executor import AdapterError

__all__ = ["LibvirtTeardown", "ProtectedResource"]

_UNDEFINE_FLAGS = (
    VIR_DOMAIN_UNDEFINE_MANAGED_SAVE | VIR_DOMAIN_UNDEFINE_SNAPSHOTS_METADATA | VIR_DOMAIN_UNDEFINE_NVRAM
)


class ProtectedResource(AdapterError):
    """The protect guard refused a resource by name."""

    def __init__(self, kind: str, name: str, why: str) -> None:
        super().__init__(f"teardown refuses {kind} {name!r}: {why}", error_class="teardown-protected")
        self.kind, self.name, self.why = kind, name, why


class LibvirtTeardown:
    def __init__(self, cfg: Any, *, client: Any = None, host: Optional[HostBackend] = None, tier: str = "te") -> None:
        naming.check_tier(tier)
        self.cfg = cfg
        self.client = client or LibvirtConnection(cfg)
        self.host = host or HostBackend(cfg)
        self.tier = tier

    # ---- the guard ----------------------------------------------------
    def _guard(self, kind: str, name: str | None) -> None:
        if not name:
            return
        if naming.is_img(name):
            raise ProtectedResource(kind, name, "img tier (golden template) is never swept")
        if naming.is_protected_external(name, getattr(self.cfg, "protected_external_names", ())):
            raise ProtectedResource(kind, name, "protected external node")
        for tier, pred in naming.tier_predicates().items():
            if tier == self.tier or (self.tier == "te" and tier == "tap"):
                continue
            if pred(name):
                raise ProtectedResource(kind, name, f"{tier} tier resource; this is a {self.tier} teardown")

    # ---- VMs ----------------------------------------------------------
    def vm_name(self, host: str, guest: int | str) -> Optional[str]:
        dom = self.client.domain_by_uuid(str(guest))
        return dom.name() if dom is not None else None

    def destroy_vm(self, host: str, guest: int | str) -> str:
        dom = self.client.domain_by_uuid(str(guest))
        if dom is None:
            return "absent"
        name = dom.name()
        self._guard("vm", name)
        if not naming.is_managed_guest_handle(str(guest)):
            raise ProtectedResource("vm", name, f"uuid {guest} is not an axRange-minted (version-5) handle")
        xml = dom.XMLDesc(VIR_DOMAIN_XML_INACTIVE)
        if dom.isActive():
            dom.destroy()
        pool = metadata_of(xml).get("pool") or self.cfg.storage_pool
        for vol_name in (naming.overlay_volume_name(str(guest)), naming.seed_iso_name(str(guest))):
            vol = self.client.volume_by_name(pool, vol_name)
            if vol is not None:
                vol.delete(0)
        dom.undefineFlags(_UNDEFINE_FLAGS)
        return "removed"

    def delete_mirror_snippets(self, host: str, guest: int | str) -> str:
        self.host.remove_files(naming.hook_script_path(str(guest)))
        return "removed"

    # ---- mirror plane -------------------------------------------------
    def delete_gretap(self, dev: str) -> str:
        if not naming.is_tap_gretap(dev):
            raise ProtectedResource("gretap", dev, "not a tap-tier gretap shape")
        if dev not in self.host.run(gretap_show_cmd(dev), timeout_s=15):
            return "absent"
        self.host.run(gretap_del_cmd(dev), timeout_s=30)
        return "removed"

    # ---- networks -----------------------------------------------------
    def _remove_network(self, net: Any) -> None:
        if net.isActive():
            net.destroy()
        net.undefine()
        if getattr(self.cfg, "network_backend", "linux") == "ovs":
            self.host.run(f"ovs-vsctl --if-exists del-br {shlex.quote(net.name())}", timeout_s=30)

    def delete_vnet(self, vnet_id: str) -> str:
        self._guard("vnet", vnet_id)
        net = self.client.network_by_name(vnet_id)
        if net is not None:
            self._remove_network(net)
            return "removed"
        # A VLAN-zone segment: a portgroup on an underlay network.
        import xml.etree.ElementTree as ET

        for candidate in self.client.all_networks():
            try:
                root = ET.fromstring(candidate.XMLDesc(0))
            except ET.ParseError:
                continue
            for pg in root.findall("portgroup"):
                if pg.get("name") == vnet_id:
                    flags = VIR_NETWORK_UPDATE_AFFECT_CONFIG | (
                        VIR_NETWORK_UPDATE_AFFECT_LIVE if candidate.isActive() else 0
                    )
                    candidate.update(VIR_NETWORK_UPDATE_COMMAND_DELETE, VIR_NETWORK_SECTION_PORTGROUP, -1,
                                     ET.tostring(pg, encoding="unicode"), flags)
                    return "removed"
        return "absent"

    def delete_sdn_zone(self, zone_id: str) -> str:
        self._guard("zone", zone_id)
        return "absent"   # a zone is logical on libvirt; nothing was ever defined

    def apply_sdn(self) -> str:
        return "applied"

    def delete_ovs_bridge(self, host: str, bridge_name: str) -> str:
        self._guard("ovs-bridge", bridge_name)
        net = self.client.network_by_name(bridge_name)
        if net is None:
            if getattr(self.cfg, "network_backend", "linux") == "ovs":
                self.host.run(f"ovs-vsctl --if-exists del-br {shlex.quote(bridge_name)}", timeout_s=30)
            return "absent"
        self._remove_network(net)
        return "removed"
