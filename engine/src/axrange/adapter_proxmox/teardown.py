"""ProxmoxTeardown — the Proxmox-specific reverse-of-create operations.

Implements `axrange.planner.teardown.TeardownBackend`: the concrete REST/SSH
calls that undo each infra op, in the same one-interface spirit as
`ProxmoxAdapter`. The planner-side orchestrator (`planner/teardown.py`) owns the
ordering, the name-guards, and the state transition; this owns *how* a VM / vnet
/ zone / gretap / OVS bridge is removed on Proxmox.

Every method is **check-then-act** (Model C, mirrored from the create side):
a resource that is already gone returns ``"absent"``; one we removed returns
``"removed"``. SDN object deletions are *staged* (`DELETE /cluster/sdn/...`) and
committed by `apply_sdn` (`PUT /cluster/sdn`) — the same two-step the create side
uses. Reuses `RestClient` + `run_ssh` so there's one HTTP/SSH code path.
"""
from __future__ import annotations

import shlex
import urllib.parse

from axrange.adapter_proxmox.config import AdapterConfig
from axrange.adapter_proxmox.rest import RestClient
from axrange.adapter_proxmox.ssh import gretap_del_cmd, gretap_show_cmd, run_ssh
from axrange.planner.executor import AdapterError, AdapterTimeout


class ProxmoxTeardown:
    """Reverse operations for `axrange destroy`. One instance per teardown."""

    def __init__(self, cfg: AdapterConfig, *, client: RestClient | None = None) -> None:
        self.cfg = cfg
        self.client = client or RestClient(cfg)

    # ---- VMs -------------------------------------------------------------
    def vm_name(self, node: str, vmid: int) -> str | None:
        """The VM's current PVE display name, for the orchestrator's protect
        guard (`planner/teardown.py`, docs/WORK_ORDER_resource_tags.md §3) —
        the same `status/current` field `op_clone_vm`'s name-guard reads
        (rest.py). ``None`` if the VM is absent or the field is unavailable;
        the orchestrator falls back to the vmid-range guard in that case."""
        status = self._vm_status(node, vmid)
        if status is None:
            return None
        name = status.get("name")
        return name if isinstance(name, str) else None

    def destroy_vm(self, node: str, vmid: int) -> str:
        """Stop (if running) then `DELETE …?purge=1` the VM. Purge removes its
        disks + any references (incl. the hookscript line). Absent VM → no-op."""
        status = self._vm_status(node, vmid)
        if status is None:
            return "absent"
        if status.get("status") == "running":
            try:
                upid = self.client.request(
                    "POST", f"/nodes/{node}/qemu/{vmid}/status/stop"
                )
                if isinstance(upid, str) and upid.startswith("UPID:"):
                    self.client.wait_task(node, upid, timeout_s=120)
            except (AdapterError, AdapterTimeout):
                # A force-stop race or an already-stopping VM; the DELETE below
                # with purge handles a stopped VM regardless.
                pass
        upid = self.client.request(
            "DELETE",
            f"/nodes/{node}/qemu/{vmid}",
            params={"purge": 1, "destroy-unreferenced-disks": 1},
        )
        if isinstance(upid, str) and upid.startswith("UPID:"):
            self.client.wait_task(node, upid, timeout_s=300)
        return "removed"

    def delete_mirror_snippets(self, node: str, vmid: int) -> str:
        """Remove the per-VM snippet FILES the mirror + cloud-init ops scp'd
        (`axrange-mirror-<vmid>.sh`, `axr-userdata-<vmid>.yaml`). The VM destroy
        already dropped the hookscript registration; these are the orphaned
        files. `rm -f` is idempotent (no error if already gone)."""
        run_ssh(
            self.cfg,
            "rm -f "
            f"/var/lib/vz/snippets/axrange-mirror-{vmid}.sh "
            f"/var/lib/vz/snippets/axr-userdata-{vmid}.yaml",
            timeout_s=20,
        )
        return "removed"

    # ---- Mirror plane (gretap netdev) ------------------------------------
    def delete_gretap(self, dev: str) -> str:
        """`ip link del <dev>` the keyed gretap netdev. Absent → no-op. The
        netdev isn't persistent across a node reboot anyway (§8 step 3)."""
        show = run_ssh(self.cfg, gretap_show_cmd(dev), timeout_s=15)
        if dev not in show:
            return "absent"
        run_ssh(self.cfg, gretap_del_cmd(dev), timeout_s=30)
        return "removed"

    # ---- SDN (staged; committed by apply_sdn) ----------------------------
    def delete_vnet(self, vnet_id: str) -> str:
        if not self._vnet_exists(vnet_id):
            return "absent"
        # Delete the vnet's subnets FIRST — PVE refuses to remove a vnet that
        # still has a subnet (400 "Parameter verification failed"). Egress /
        # sensor-transport vnets carry a create-subnet; capture vnets don't.
        try:
            subs = self.client.request(
                "GET", f"/cluster/sdn/vnets/{vnet_id}/subnets"
            ) or []
            for s in subs if isinstance(subs, list) else []:
                sid = s.get("subnet") or s.get("id") if isinstance(s, dict) else None
                if sid:
                    self._delete_tolerant(
                        f"/cluster/sdn/vnets/{vnet_id}/subnets/"
                        f"{urllib.parse.quote(str(sid), safe='')}"
                    )
        except AdapterError:
            pass
        self._delete_tolerant(f"/cluster/sdn/vnets/{vnet_id}")
        return "removed"

    def delete_sdn_zone(self, zone_id: str) -> str:
        if not self._zone_exists(zone_id):
            return "absent"
        self._delete_tolerant(f"/cluster/sdn/zones/{zone_id}")
        return "removed"

    def apply_sdn(self) -> str:
        """Commit the staged SDN deletions cluster-wide (`PUT /cluster/sdn`,
        async). Mirrors op_apply_sdn_config's apply-and-wait."""
        upid = self.client.request("PUT", "/cluster/sdn")
        if isinstance(upid, str) and upid.startswith("UPID:"):
            node = (
                upid.split(":", 2)[1]
                if upid.count(":") >= 2
                else self.cfg.default_node
            )
            try:
                self.client.wait_task(node, upid, timeout_s=180)
            except AdapterTimeout:
                pass
        return "applied"

    # ---- OVS underlay bridge ---------------------------------------------
    def delete_ovs_bridge(self, node: str, bridge_name: str) -> str:
        """Remove the VLAN-zone underlay OVS bridge from the node network and
        commit it (`DELETE /nodes/<node>/network/<iface>` + `PUT …/network`).
        This is what clears the bridge from `/etc/network/interfaces` — the
        persistence CLAUDE.md flags. Absent → no-op."""
        if not self._bridge_exists(node, bridge_name):
            return "absent"
        self._delete_tolerant(f"/nodes/{node}/network/{bridge_name}")
        # Commit the node network change (async PUT, same as the create side).
        try:
            upid = self.client.request("PUT", f"/nodes/{node}/network")
            if isinstance(upid, str) and upid.startswith("UPID:"):
                self.client.wait_task(node, upid, timeout_s=120)
        except (AdapterError, AdapterTimeout):
            pass
        return "removed"

    # ---- helpers ---------------------------------------------------------
    def _delete_tolerant(self, path: str) -> None:
        """DELETE, tolerating a 'does not exist'/404 race (idempotent teardown)."""
        try:
            self.client.request("DELETE", path)
        except AdapterError as e:
            msg = str(e).lower()
            if "does not exist" in msg or "not found" in msg or "http 404" in msg:
                return
            raise

    def _vm_status(self, node: str, vmid: int):
        try:
            data = self.client.request(
                "GET", f"/nodes/{node}/qemu/{vmid}/status/current"
            )
        except AdapterError:
            return None
        return data if isinstance(data, dict) else None

    def _vnet_exists(self, vnet_id: str) -> bool:
        try:
            return bool(self.client.request("GET", f"/cluster/sdn/vnets/{vnet_id}"))
        except AdapterError:
            return False

    def _zone_exists(self, zone_id: str) -> bool:
        try:
            return bool(self.client.request("GET", f"/cluster/sdn/zones/{zone_id}"))
        except AdapterError:
            return False

    def _bridge_exists(self, node: str, bridge_name: str) -> bool:
        try:
            return bool(
                self.client.request("GET", f"/nodes/{node}/network/{bridge_name}")
            )
        except AdapterError:
            return False
