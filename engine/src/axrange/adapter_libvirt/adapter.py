"""LibvirtAdapter -- `axrange.planner.executor.Adapter` for a KVM host driven
through libvirt (`adapters/libvirt/SPEC.md`).

One interface, two backends, the same shape as the Proxmox adapter:

  libvirt  (`connection.LibvirtConnection`) -- networks, domains, volumes,
           the guest agent channel.
  host     (`host.HostBackend`)             -- the NoCloud seed ISO, the keyed
           gretap, the per-tap `tc` mirror hook; local subprocess or SSH per
           the URI.

Op -> call (the SPEC's mapping table, implemented):

| op | what happens |
|---|---|
| create-sdn-zone | logical: `zone_id` from naming, no call |
| create-ovs-bridge | Simple zone: logical (`bridge_name` = the segment's network name). VLAN zone: define+start the plan's underlay network (`tebr_<6hex>`, OVS-backed -- needs `LIBVIRT_NETWORK_BACKEND=ovs`) |
| create-vnet | Simple zone: define+start one isolated network per segment (`tev<5hex>`, bridge of the same name). VLAN zone: a tagged `<portgroup>` named `tev<5hex>` on the underlay network. A DHCP block is refused (not implemented) |
| create-subnet | logical (addressing is cloud-init's); `snat` / DHCP refused (not implemented) |
| apply-sdn-config | every network of this plan active + autostart |
| clone-vm | qcow2 overlay volume in the pool with the template volume as backing file (`linked`; `full` = `createXMLFrom`), `defineXML` of the binding's / default domain document, UUID = `naming.guest_handle_for_op` |
| configure-vm-hardware | re-define with vcpu / memory / machine / firmware / agent channel; grow the overlay (`vol.resize`) |
| configure-cloud-init | user-data + meta-data + network-config as a `cidata` ISO built on the host (genisoimage / xorriso / mkisofs, probed) and attached as a read-only CD-ROM |
| attach-vm-to-vnet | `<interface type='network'>` on the segment network (or the underlay + portgroup), deterministic MAC and tap name |
| start-vm | `create()` unless active |
| wait-guest-agent | `guest-ping` polled to the first-boot budget |
| wait-os-ready | `cloud-init status --wait` (Linux) / the os-ready marker (Windows) over the agent |
| configure-router / configure-routing | unchanged guest-side scripts (Debian over the agent, VyOS over SSH) |
| attach-gre-tunnel | `ip link add ... type gretap` + DF policy + MTU + deep-FIFO qdisc + offload off on the host |
| install-ovs-mirror | per-guest hook under `/etc/libvirt/hooks/qemu.d/` with the per-tap `tc` mirror; applied in-band when the domain is running |

Every op is check-then-act on the deterministic name / UUID (an existing
domain or network of ours is adopted; a foreign domain at our UUID is
refused by name).
"""
from __future__ import annotations

import shlex
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Optional

from axrange.adapter_libvirt import naming
from axrange.adapter_libvirt.bindings import LibvirtBinding, LibvirtBindings
from axrange.adapter_libvirt.config import LibvirtConfig
from axrange.adapter_libvirt.connection import (
    VIR_DOMAIN_XML_INACTIVE,
    VIR_NETWORK_SECTION_PORTGROUP,
    VIR_NETWORK_UPDATE_AFFECT_CONFIG,
    VIR_NETWORK_UPDATE_AFFECT_LIVE,
    VIR_NETWORK_UPDATE_COMMAND_ADD_LAST,
    LibvirtConnection,
    libvirt_call,
)
from axrange.adapter_libvirt.domain_xml import (
    DomainSpec,
    add_cdrom,
    interfaces,
    metadata_of,
    parse_mem_kib,
    parse_size_bytes,
    render_domain,
    set_hardware,
    set_interface,
)
from axrange.adapter_libvirt.guest import LibvirtGuestExec
from axrange.adapter_libvirt.host import HostBackend
# Guest-side renders and host command builders shared with the Proxmox
# adapter: all pure (string in, string out) -- see each module's note.
from axrange.adapter_proxmox.rest import (
    ROUTER_NAT_SCRIPT,
    WINDOWS_OSREADY_MARKER,
    _render_userdata,
    _render_windows_userdata,
)
from axrange.adapter_proxmox.ssh import (
    SshBackend,
    _tc_mirror_cmd,
    apply_vyos_sets,
    gretap_add_cmd,
    gretap_heal_cmd,
    gretap_offload_cmd,
    gretap_qdisc_cmd,
    gretap_show_cmd,
    gretap_up_cmd,
    run_ssh_guest,
)
from axrange.planner.dag import OpInstance
from axrange.planner.executor import AdapterError, AdapterTimeout, ExecutionContext

__all__ = ["LibvirtAdapter", "LIBVIRT_OPS", "HOST_OPS", "GUEST_OPS", "LOGICAL_OPS"]

LOGICAL_OPS = frozenset({"create-sdn-zone", "create-subnet"})
LIBVIRT_OPS = frozenset({
    "create-ovs-bridge", "create-vnet", "apply-sdn-config", "clone-vm",
    "configure-vm-hardware", "configure-cloud-init", "attach-vm-to-vnet",
    "start-vm", "wait-guest-agent",
})
GUEST_OPS = frozenset({"wait-os-ready", "configure-router", "configure-routing"})
HOST_OPS = frozenset({"attach-gre-tunnel", "install-ovs-mirror"})


def _network_xml(name: str, backend: str) -> str:
    """An isolated segment network. `linux`: libvirt creates and owns a Linux
    bridge of the same name (no `<forward>` = no uplink, no DHCP -- the twin
    addresses itself). `ovs`: a pre-created Open vSwitch bridge, so VLAN
    portgroups can tag ports."""
    if backend == "ovs":
        return (
            f"<network><name>{name}</name><forward mode='bridge'/>"
            f"<bridge name='{name}'/><virtualport type='openvswitch'/></network>"
        )
    return f"<network><name>{name}</name><bridge name='{name}' stp='off' delay='0'/></network>"


def _portgroup_xml(name: str, vlan: int) -> str:
    return f"<portgroup name='{name}'><vlan><tag id='{int(vlan)}'/></vlan></portgroup>"


def _portgroups_of(net_xml: str) -> list[str]:
    try:
        root = ET.fromstring(net_xml)
    except ET.ParseError:
        return []
    return [pg.get("name") or "" for pg in root.findall("portgroup")]


_HOOK_TEMPLATE = """#!/bin/bash
# axRange per-tap tc mirror hook (libvirt qemu.d) -- generated by
# axrange/adapter_libvirt/adapter.py; the same clsact + matchall mirred rules
# the Proxmox hook installs (docs/ARCHITECTURE.md §3, Option 1).
# Domain: {name} ({uuid})
# Plan:   {plan_id}
# Mirrors (nic:tap:gretap:direction): {comment}
# libvirt runs every script in /etc/libvirt/hooks/qemu.d/ as:
#   <script> <domain-name> <phase> <sub-op> <extra>
[ "$1" = "{name}" ] || exit 0
set -e
case "$2" in
  started)
{started}
    ;;
  stopped|release)
{stopped}
    ;;
esac
exit 0
"""


def _group_by_tap(entries: list[tuple[int, str, str, str]]) -> dict[str, list[tuple[str, str]]]:
    """`(nic, tap, gretap, direction)` entries -> `{tap: [(gretap, direction)]}`,
    in first-seen tap order."""
    by_tap: dict[str, list[tuple[str, str]]] = {}
    for _nic, tap, gretap, direction in entries:
        by_tap.setdefault(tap, []).append((gretap, direction))
    return by_tap


def _ethtool_off_cmd(tap: str) -> str:
    """Segmentation offloads off on a tap, so the mirror sees real frames."""
    return f"ethtool -K {shlex.quote(tap)} gso off tso off gro off >/dev/null 2>&1 || true"


def _hook_script(name: str, uuid: str, plan_id: str, entries: list[tuple[int, str, str, str]]) -> str:
    """`entries` = `(nic_index, tap, gretap, direction)`; an empty list renders
    a hook whose `started` branch installs nothing (the revert shape)."""
    started: list[str] = []
    stopped: list[str] = []
    for tap, gretaps in _group_by_tap(entries).items():
        q = shlex.quote(tap)
        started.append("    " + _ethtool_off_cmd(tap))
        started.append("    " + _tc_mirror_cmd(tap, gretaps))
        started.append(f"    tc filter show dev {q} ingress | grep -q mirred")
        stopped.append(f"    tc qdisc del dev {q} clsact 2>/dev/null || true")
    comment = ", ".join(f"{n}:{t}->{g}({d})" for n, t, g, d in entries) or "(none)"
    return _HOOK_TEMPLATE.format(
        name=name, uuid=uuid, plan_id=plan_id, comment=comment,
        started="\n".join(started) or "    :",
        stopped="\n".join(stopped) or "    :",
    )


_DEFAULT_DNS = ("1.1.1.1",)


def _render_network_config(
    ip_config: dict[str, Any], plan_id: str, host_id: str, dns_by_net: Optional[dict[str, list[str]]] = None,
) -> str:
    """cloud-init network-config v2 from the planner's `ip_config`
    (`net_id -> {ip, cidr, gateway} | "dhcp"`), one `ethernets` entry per
    NIC matched BY MAC (`naming.mac_for_nic`, the same MAC
    `attach-vm-to-vnet` pins), indexed by position like Proxmox's
    `ipconfigN` (an address-less leg keeps its index). `dns_by_net` carries
    each segment's create-vnet `dns`; a segment without one keeps 1.1.1.1."""
    lines = ["version: 2", "ethernets:"]
    for i, (net, net_cfg) in enumerate(ip_config.items()):
        ip = net_cfg.get("ip") if isinstance(net_cfg, dict) else net_cfg
        if not ip:
            continue
        mac = naming.mac_for_nic(plan_id, host_id, i)
        lines += [f"  nic{i}:", f"    match: {{macaddress: \"{mac}\"}}"]
        if ip == "dhcp":
            lines.append("    dhcp4: true")
            continue
        prefix = "24"
        cidr = net_cfg.get("cidr") if isinstance(net_cfg, dict) else None
        if isinstance(cidr, str) and "/" in cidr:
            prefix = cidr.split("/", 1)[1]
        lines.append(f"    addresses: [{ip}/{prefix}]")
        gw = net_cfg.get("gateway") if isinstance(net_cfg, dict) else None
        if gw:
            lines.append(f"    routes: [{{to: 0.0.0.0/0, via: {gw}}}]")
        dns = (dns_by_net or {}).get(net) or list(_DEFAULT_DNS)
        lines.append(f"    nameservers: {{addresses: [{', '.join(dns)}], search: [axrange.local]}}")
    if len(lines) == 2:
        lines.append("  {}")
    return "\n".join(lines) + "\n"


class LibvirtAdapter:
    """Implements `axrange.planner.executor.Adapter`. One instance per deploy;
    the connection opens lazily on the first libvirt-backed op."""

    def __init__(
        self,
        config: LibvirtConfig,
        *,
        conn: Any = None,
        host: Optional[HostBackend] = None,
        bindings: Optional[LibvirtBindings] = None,
        guest_exec_factory: Optional[Callable[..., Any]] = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config
        self.conn = conn or LibvirtConnection(config)
        self.host = host or HostBackend(config)
        self.bindings = bindings or LibvirtBindings(getattr(config, "bindings_dir", "") or None)
        self._guest_exec_factory = guest_exec_factory
        self._sleep = sleep
        # What THIS deploy defined (one adapter instance per deploy): the
        # networks `apply-sdn-config` may touch, and each segment's `dns`
        # for the network-config. Another plan's networks are never listed.
        self._plan_networks: set[str] = set()
        self._net_dns: dict[str, list[str]] = {}

    # ---- the Adapter protocol -----------------------------------------
    def run(self, op: OpInstance, resolved_inputs: dict[str, Any], ctx: ExecutionContext) -> dict[str, Any]:
        fn = _OP_TO_METHOD.get(op.op_name)
        if fn is None:
            raise AdapterError(
                f"libvirt adapter: no backend for op {op.op_name!r}",
                error_class="adapter-unsupported",
            )
        return fn(self, op, resolved_inputs, ctx)

    # ---- helpers -------------------------------------------------------
    def _guest(self, ctx: ExecutionContext, *, os_family: str = "linux") -> Any:
        if self._guest_exec_factory is not None:
            return self._guest_exec_factory(self.conn, ctx.cluster.default_node, os_family=os_family)
        return LibvirtGuestExec(self.conn, ctx.cluster.default_node, os_family=os_family, sleep=self._sleep)

    def _domain(self, guest: Any) -> Any:
        dom = self.conn.domain_by_uuid(str(guest))
        if dom is None:
            raise AdapterError(f"domain {guest} is not defined on the host", error_class="domain-absent")
        return dom

    @staticmethod
    def _uuid(inp: dict[str, Any]) -> str:
        v = inp.get("vmid")
        if not v:
            raise AdapterError("vmid (the guest handle) missing", error_class="adapter-input")
        return str(v)

    def _ensure_network(self, name: str, xml: str, *, timeout_s: int = 60) -> Any:
        """Check-then-act: adopt a defined network of this name, else define it;
        active + autostart either way. `ovs` backend: the bridge first."""
        if self.config.network_backend == "ovs":
            self.host.run(f"ovs-vsctl --may-exist add-br {shlex.quote(name)}", timeout_s=timeout_s)
        net = self.conn.network_by_name(name)
        if net is None:
            net = self.conn.define_network(xml)
        if not net.isActive():
            libvirt_call("network-create", net.create)
        net.setAutostart(True)
        self._plan_networks.add(name)
        return net

    def _underlay(self, ctx: ExecutionContext) -> str:
        return naming.underlay_bridge_for_plan(ctx.plan_id, tier=ctx.tier)

    def _guest_pubkey(self) -> str:
        """The operator public key cloud-init injects. Unreadable (or never
        configured) is an error: a guest cast without it is unreachable."""
        path = self.config.guest_ssh_pubkey_file
        if not path:
            raise AdapterError("GUEST_SSH_PUBKEY_FILE is not set; refusing to cast a guest with no key",
                               error_class="adapter-config")
        try:
            return Path(path).expanduser().read_text().strip()
        except OSError as e:
            raise AdapterError(
                f"GUEST_SSH_PUBKEY_FILE {path!r} is unreadable ({e}); refusing to cast a guest with no key",
                error_class="adapter-config",
            ) from e

    def _pool_of(self, dom_xml: str) -> str:
        return metadata_of(dom_xml).get("pool") or self.config.storage_pool

    # ---- OP-NET --------------------------------------------------------
    @staticmethod
    def _refuse_nat_dhcp(op_name: str, inp: dict[str, Any]) -> None:
        """NAT (`snat`) and libvirt-served DHCP are not implemented here
        (adapters/libvirt/SPEC.md, `create-subnet` / `create-vnet` rows: the
        decision is held); a plan that asks for either fails loud rather than
        casting a segment with no egress / no lease server."""
        dhcp = inp.get("dhcp")
        if inp.get("snat"):
            raise AdapterError(
                f"{op_name}: snat (egress NAT for a segment) is not implemented on the libvirt "
                f"platform -- adapters/libvirt/SPEC.md op table, `{op_name}` row (NAT and DHCP refused)",
                error_class="adapter-unsupported",
            )
        if isinstance(dhcp, dict) and dhcp.get("enabled"):
            raise AdapterError(
                f"{op_name}: a dhcp block (libvirt-served leases) is not implemented on the libvirt "
                f"platform -- adapters/libvirt/SPEC.md op table, `{op_name}` row (NAT and DHCP refused)",
                error_class="adapter-unsupported",
            )

    def op_create_sdn_zone(self, op: OpInstance, inp: dict[str, Any], ctx: ExecutionContext) -> dict[str, Any]:
        zone_type = (inp.get("zone_type") or "simple").lower()
        return {"zone_id": naming.zone_id_for_plan(ctx.plan_id, tier=ctx.tier), "zone_type": zone_type}

    def op_create_subnet(self, op: OpInstance, inp: dict[str, Any], ctx: ExecutionContext) -> dict[str, Any]:
        net_id = inp.get("net_id") or op.scope.get("net")
        cidr = inp.get("cidr")
        if not net_id or not cidr:
            raise AdapterError("create-subnet: net_id/cidr missing", error_class="adapter-input")
        self._refuse_nat_dhcp("create-subnet", inp)
        return {"subnet_id": cidr, "vnet_id": naming.vnet_id_for_net(net_id, plan_id=ctx.plan_id, tier=ctx.tier)}

    def op_create_ovs_bridge(self, op: OpInstance, inp: dict[str, Any], ctx: ExecutionContext) -> dict[str, Any]:
        zone_type = (inp.get("zone_type") or "simple").lower()
        if zone_type != "vlan":
            net_id = inp.get("net_id") or op.scope.get("net")
            if not net_id:
                raise AdapterError("create-ovs-bridge: net_id missing for Simple zone", error_class="adapter-input")
            return {"bridge_name": naming.vnet_id_for_net(net_id, plan_id=ctx.plan_id, tier=ctx.tier)}
        if self.config.network_backend != "ovs":
            raise AdapterError(
                "create-ovs-bridge: a VLAN zone needs LIBVIRT_NETWORK_BACKEND=ovs -- libvirt "
                "applies <vlan> tags to Open vSwitch ports only, a Linux bridge network "
                "cannot carry tagged portgroups",
                error_class="adapter-config",
            )
        name = self._underlay(ctx)
        self._ensure_network(name, _network_xml(name, "ovs"), timeout_s=op.timeout_s or 60)
        return {"bridge_name": name}

    def op_create_vnet(self, op: OpInstance, inp: dict[str, Any], ctx: ExecutionContext) -> dict[str, Any]:
        net_id = inp.get("net_id") or op.scope.get("net")
        if not net_id:
            raise AdapterError("create-vnet: net_id missing", error_class="adapter-input")
        self._refuse_nat_dhcp("create-vnet", inp)
        if inp.get("dns"):
            self._net_dns[str(net_id)] = [str(d) for d in inp["dns"]]
        vnet_id = naming.vnet_id_for_net(net_id, plan_id=ctx.plan_id, tier=ctx.tier)
        vlan = inp.get("vlan")
        if vlan is None:
            self._ensure_network(vnet_id, _network_xml(vnet_id, self.config.network_backend),
                                 timeout_s=op.timeout_s or 60)
            return {"vnet_id": vnet_id}
        underlay = self._underlay(ctx)
        net = self.conn.network_by_name(underlay)
        if net is None:
            raise AdapterError(
                f"create-vnet: VLAN segment {net_id!r} needs the underlay network {underlay!r} "
                "(create-ovs-bridge for the VLAN zone has not run)",
                error_class="adapter-input",
            )
        if vnet_id in _portgroups_of(net.XMLDesc(0)):
            return {"vnet_id": vnet_id}
        flags = VIR_NETWORK_UPDATE_AFFECT_CONFIG | (VIR_NETWORK_UPDATE_AFFECT_LIVE if net.isActive() else 0)
        libvirt_call("network-update", net.update, VIR_NETWORK_UPDATE_COMMAND_ADD_LAST,
                     VIR_NETWORK_SECTION_PORTGROUP, -1, _portgroup_xml(vnet_id, int(vlan)), flags)
        return {"vnet_id": vnet_id}

    def op_apply_sdn_config(self, op: OpInstance, inp: dict[str, Any], ctx: ExecutionContext) -> dict[str, Any]:
        underlay = self._underlay(ctx)
        # This deploy's networks only: the underlay plus the segment networks
        # create-vnet / create-ovs-bridge defined or adopted on this instance --
        # never a tier-shape sweep, which would also start (and autostart)
        # another same-tier plan's networks on a shared host.
        mine = self._plan_networks | {underlay}
        touched = 0
        for net in self.conn.all_networks():
            name = net.name()
            if name not in mine:
                continue
            if not net.isActive():
                libvirt_call("network-create", net.create)
            net.setAutostart(True)
            touched += 1
        return {"applied": True, "networks": touched}

    # ---- OP-HOST -------------------------------------------------------
    def _template_for(self, inp: dict[str, Any]) -> tuple[str, str, LibvirtBinding | None]:
        """(pool, template volume name, binding) for a clone: an explicit
        `template_volume` input wins (a platform-aware plan), else the libvirt
        binding of the op's `image_id`."""
        explicit = inp.get("template_volume")
        if explicit:
            return (inp.get("template_pool") or self.config.storage_pool, str(explicit), None)
        image_id = inp.get("image_id")
        if not image_id:
            raise AdapterError(
                "clone-vm: neither template_volume nor image_id in the inputs -- "
                "the libvirt adapter cannot pick a template",
                error_class="adapter-input",
            )
        b = self.bindings.resolve(str(image_id), inp.get("image_version"))
        return (b.pool or self.config.storage_pool, b.template_volume, b)

    def op_clone_vm(self, op: OpInstance, inp: dict[str, Any], ctx: ExecutionContext) -> dict[str, Any]:
        host_id = op.scope.get("host") or inp.get("node_id") or ""
        guest = naming.guest_handle_for_op(ctx.plan_id, op.op_id)
        name = naming.vm_name_for_host(ctx.plan_id, host_id, tier=ctx.tier)

        existing = self.conn.domain_by_uuid(guest)
        if existing is not None:
            existing_name = existing.name()
            if existing_name and existing_name != name:
                raise AdapterError(
                    f"clone-vm: domain uuid={guest} already exists with name {existing_name!r}, "
                    f"expected {name!r} for host {host_id!r} (plan={ctx.plan_id!r}) -- refusing "
                    "to adopt a domain axRange did not create at this deterministic UUID.",
                    error_class="vmid-collision-foreign",
                )
            return {"vmid": guest}
        by_name = self.conn.domain_by_name(name)
        if by_name is not None:
            raise AdapterError(
                f"clone-vm: a domain named {name!r} exists with uuid {by_name.UUIDString()}, "
                f"not the plan's {guest} -- a stale or hand-made domain; remove it first",
                error_class="vmid-collision-foreign",
            )

        pool_name, template_volume, binding = self._template_for(inp)
        pool = self.conn.pool_by_name(pool_name)
        if pool is None:
            raise AdapterError(f"clone-vm: storage pool {pool_name!r} is not defined on the host", error_class="adapter-config")
        tpl = self.conn.volume_by_name(pool_name, template_volume)
        if tpl is None:
            raise AdapterError(
                f"clone-vm: template volume {template_volume!r} is not in pool {pool_name!r} "
                "(import the img-* qcow2 into the pool: adapter_libvirt/bindings.py)",
                error_class="template-missing",
            )
        clone_mode = (inp.get("clone_mode") or (binding.clone_mode if binding else "linked") or "linked").lower()
        overlay_name = naming.overlay_volume_name(guest)
        vol = self.conn.volume_by_name(pool_name, overlay_name)
        if vol is None:
            capacity = int(tpl.info()[1])
            backing = (
                f"<backingStore><path>{tpl.path()}</path><format type='qcow2'/></backingStore>"
                if clone_mode == "linked" else ""
            )
            vol_xml = (
                f"<volume type='file'><name>{overlay_name}</name>"
                f"<capacity unit='bytes'>{capacity}</capacity>"
                f"<target><format type='qcow2'/></target>{backing}</volume>"
            )
            vol = (
                libvirt_call("vol-create", pool.createXML, vol_xml, 0) if clone_mode == "linked"
                else libvirt_call("vol-create", pool.createXMLFrom, vol_xml, tpl, 0)
            )

        spec = DomainSpec(
            name=name, uuid=guest, disk_path=vol.path(), plan_id=ctx.plan_id, op_id=op.op_id,
            tier=ctx.tier, pool=pool_name,
            vcpu=binding.vcpu if binding else 2,
            memory_kib=parse_mem_kib(binding.memory) if binding else 2 * 1024 * 1024,
            machine=binding.machine if binding else "pc",
            firmware=binding.firmware if binding else "seabios",
            disk_bus=binding.disk_bus if binding else "virtio",
            nic_model=binding.nic_model if binding else "",
            ci_user=(binding.cloud_init_user or "") if binding else "",
        )
        self.conn.define_domain(render_domain(spec, binding.domain_xml_template if binding else None))
        return {"vmid": guest}

    def op_configure_vm_hardware(self, op: OpInstance, inp: dict[str, Any], ctx: ExecutionContext) -> dict[str, Any]:
        guest = self._uuid(inp)
        dom = self._domain(guest)
        xml = dom.XMLDesc(VIR_DOMAIN_XML_INACTIVE)
        new_xml = set_hardware(
            xml,
            vcpu=int(inp["cpu"]) if inp.get("cpu") is not None else None,
            memory_kib=parse_mem_kib(str(inp["ram"])) if inp.get("ram") is not None else None,
            machine=inp.get("machine") or None,
            firmware=inp.get("bios") or None,
            agent=inp.get("agent"),
        )
        if new_xml != xml:
            self.conn.define_domain(new_xml)
        if inp.get("disk"):
            wanted = parse_size_bytes(str(inp["disk"]))
            vol = self.conn.volume_by_name(self._pool_of(xml), naming.overlay_volume_name(guest))
            if vol is not None and int(vol.info()[1]) < wanted:
                libvirt_call("vol-resize", vol.resize, wanted, 0)   # a smaller/equal request is the Model-C no-op
        return {"configured": True}

    def op_configure_cloud_init(self, op: OpInstance, inp: dict[str, Any], ctx: ExecutionContext) -> dict[str, Any]:
        guest = self._uuid(inp)
        dom = self._domain(guest)
        host_id = op.scope.get("host") or inp.get("node_id") or ""
        hostname = inp.get("hostname") or host_id or f"host-{guest[:8]}"
        password = inp.get("password") or self.config.default_password
        meta = f"instance-id: {guest}\nlocal-hostname: {hostname}\n"
        files: dict[str, str] = {"meta-data": meta}
        if (inp.get("os_family") or "linux").lower() == "windows":
            files["user-data"] = _render_windows_userdata(hostname=hostname, admin_password=password)
        else:
            pubkey = self._guest_pubkey()
            # planner's `ci_user` (the manifest's cloud_init.user) > the libvirt
            # binding's, recorded on the domain at clone-vm > `axrange`.
            ci_user = inp.get("ci_user") or metadata_of(dom.XMLDesc(VIR_DOMAIN_XML_INACTIVE)).get("ci_user") or "axrange"
            files["user-data"] = _render_userdata(
                hostname=hostname, user=ci_user, ssh_pubkey=pubkey, password=password,
            )
        files["network-config"] = _render_network_config(
            inp.get("ip_config") or {}, ctx.plan_id, host_id, self._net_dns,
        )

        xml = dom.XMLDesc(VIR_DOMAIN_XML_INACTIVE)
        pool_name = self._pool_of(xml)
        iso_path = f"{self.conn.pool_path(pool_name).rstrip('/')}/{naming.seed_iso_name(guest)}"
        tool = self.host.build_seed_iso(iso_path, files, timeout_s=op.timeout_s or 60)
        pool = self.conn.pool_by_name(pool_name)
        if pool is not None:
            try:
                pool.refresh(0)
            except Exception:  # noqa: BLE001 -- the ISO is on disk; a refresh race is cosmetic
                pass
        self.conn.define_domain(add_cdrom(xml, iso_path))
        return {"cloud_init_attached": True, "seed_iso": iso_path, "iso_tool": tool}

    def op_attach_vm_to_vnet(self, op: OpInstance, inp: dict[str, Any], ctx: ExecutionContext) -> dict[str, Any]:
        guest = self._uuid(inp)
        vnet_id = inp.get("vnet_id")
        if not vnet_id:
            raise AdapterError("attach-vm-to-vnet: vnet_id missing", error_class="adapter-input")
        nic_index = int(inp.get("nic_index", 0))
        host_id = op.scope.get("host") or inp.get("node_id") or ""
        dom = self._domain(guest)
        if self.conn.network_by_name(vnet_id) is not None:
            network, portgroup = vnet_id, None
        else:
            underlay = self._underlay(ctx)
            net = self.conn.network_by_name(underlay)
            if net is None or vnet_id not in _portgroups_of(net.XMLDesc(0)):
                raise AdapterError(
                    f"attach-vm-to-vnet: {vnet_id!r} is neither a network nor a portgroup of "
                    f"{underlay!r} on the host",
                    error_class="adapter-input",
                )
            network, portgroup = underlay, vnet_id
        xml = dom.XMLDesc(VIR_DOMAIN_XML_INACTIVE)
        new_xml = set_interface(
            xml, nic_index,
            mac=naming.mac_for_nic(ctx.plan_id, host_id, nic_index),
            network=network, portgroup=portgroup,
            tap=naming.tap_name_for_nic(guest, nic_index),
            model=metadata_of(xml).get("nic_model") or "virtio",   # the binding's, recorded at clone-vm
        )
        self.conn.define_domain(new_xml)
        return {"nic_attached": True}

    def op_start_vm(self, op: OpInstance, inp: dict[str, Any], ctx: ExecutionContext) -> dict[str, Any]:
        dom = self._domain(self._uuid(inp))
        if not dom.isActive():
            libvirt_call("domain-create", dom.create)
        return {"started": True}

    def op_wait_guest_agent(self, op: OpInstance, inp: dict[str, Any], ctx: ExecutionContext) -> dict[str, Any]:
        guest = self._uuid(inp)
        budget = int(inp.get("first_boot_budget") or op.timeout_s or 180)
        self._guest(ctx).wait_ready(guest, timeout_s=budget)
        return {"agent_ready": True}

    def op_wait_os_ready(self, op: OpInstance, inp: dict[str, Any], ctx: ExecutionContext) -> dict[str, Any]:
        guest = self._uuid(inp)
        budget = int(inp.get("first_boot_budget") or op.timeout_s or 600)
        os_family = (inp.get("os_family") or "linux").lower()
        ga = self._guest(ctx, os_family=os_family)
        ga.wait_ready(guest, timeout_s=budget)
        if os_family == "windows":
            deadline = time.time() + budget
            while True:
                res = ga.shell_exec(
                    guest, f"if (Test-Path '{WINDOWS_OSREADY_MARKER}') {{ exit 0 }} else {{ exit 1 }}", timeout_s=60,
                )
                if res.ok:
                    self._apply_windows_hostname(ga, guest, inp)
                    self._apply_windows_static_ip(ga, guest, inp)
                    return {"os_ready": True}
                if time.time() >= deadline:
                    break
                self._sleep(10)
            raise AdapterError(
                f"wait-os-ready: {guest} (windows) marker {WINDOWS_OSREADY_MARKER} absent after {budget}s",
                error_class="windows-os-not-ready",
            )
        res = ga.exec(guest, ["cloud-init", "status", "--wait"], timeout_s=budget)
        if res.exit_code in (0, 2):
            return {"os_ready": True}
        raise AdapterError(
            f"wait-os-ready: {guest} cloud-init exited with code {res.exit_code}; "
            f"out-data={(res.stdout or res.stderr)[:200]}",
            error_class="cloud-init-failed",
        )

    # Windows post-marker steps -- the same renders the Proxmox adapter drives
    # (`axrange.config_exec.render`, engine-side), over this guest channel.
    def _windows_boot_id(self, ga: Any, guest: str) -> str:
        try:
            res = ga.shell_exec(guest, "(Get-CimInstance Win32_OperatingSystem).LastBootUpTime.Ticks", timeout_s=60)
            return res.stdout.strip() if res.ok else ""
        except (AdapterError, AdapterTimeout):
            return ""

    def _wait_windows_reboot(self, ga: Any, guest: str, prev: str, *, timeout_s: int = 600) -> None:
        deadline = time.time() + timeout_s
        saw_down = False
        while time.time() < deadline:
            cur = self._windows_boot_id(ga, guest)
            if not cur:
                saw_down = True
            elif prev and cur != prev:
                return
            elif not prev and saw_down:
                return
            self._sleep(10)
        raise AdapterTimeout(f"wait-os-ready: {guest} did not reboot back in {timeout_s}s")

    def _apply_windows_hostname(self, ga: Any, guest: str, inp: dict[str, Any]) -> None:
        hostname = inp.get("hostname")
        if not hostname:
            return
        from axrange.config_exec.render import ConfigRenderError, render_windows_set_hostname

        try:
            script = render_windows_set_hostname(str(hostname))
        except ConfigRenderError:
            return
        prev = self._windows_boot_id(ga, guest)
        res = ga.shell_exec(guest, script, timeout_s=120)
        if not res.ok:
            raise AdapterError(
                f"wait-os-ready: {guest} hostname rename exited {res.exit_code}: {(res.stdout or res.stderr)[:200]}",
                error_class="windows-hostname-failed",
            )
        if "AXR_RENAMED yes" not in (res.stdout or ""):
            return
        try:
            ga.shell_exec(guest, "Restart-Computer -Force", timeout_s=30)
        except (AdapterError, AdapterTimeout):
            pass
        self._wait_windows_reboot(ga, guest, prev)

    def _apply_windows_static_ip(self, ga: Any, guest: str, inp: dict[str, Any]) -> None:
        static_ip = inp.get("static_ip")
        if not static_ip:
            return
        from axrange.config_exec.render import ConfigRenderError, render_windows_static_ip

        try:
            script = render_windows_static_ip(
                str(static_ip), prefix=int(inp.get("static_prefix") or 24), gateway=inp.get("static_gateway"),
            )
        except ConfigRenderError as e:
            raise AdapterError(f"wait-os-ready: {guest} static-ip render failed: {e}", error_class="windows-static-ip-failed")
        res = ga.shell_exec(guest, script, timeout_s=120)
        if not res.ok:
            raise AdapterError(
                f"wait-os-ready: {guest} static-ip apply exited {res.exit_code}: {(res.stdout or res.stderr)[:200]}",
                error_class="windows-static-ip-failed",
            )

    # ---- routers (guest side, unchanged) -------------------------------
    def op_configure_router(self, op: OpInstance, inp: dict[str, Any], ctx: ExecutionContext) -> dict[str, Any]:
        guest = self._uuid(inp)
        if inp.get("router_os") == "vyos":
            return self._configure_vyos_router(op, inp, ctx)
        ga = self._guest(ctx)
        ga.wait_ready(guest, timeout_s=op.timeout_s or 300)
        res = ga.exec(guest, ["/bin/bash", "-c", ROUTER_NAT_SCRIPT], timeout_s=op.timeout_s or 300)
        out = (res.stdout or "") + (res.stderr or "")
        if res.exit_code != 0 or "AXR_ROUTER_OK" not in out:
            raise AdapterError(
                f"configure-router: {guest} setup failed (exit {res.exit_code}): {out[:300]}",
                error_class="router-config-failed",
            )
        result: dict[str, Any] = {"configured": True}
        phase = self._apply_egress_policy(guest, inp.get("egress"), ga, op)
        if phase is not None:
            result["egress_phase"] = phase
        return result

    def _apply_egress_policy(self, guest: str, egress: Any, ga: Any, op: OpInstance) -> str | None:
        from axrange.adapter_proxmox.egress_render import (  # guest-side render, pure
            EGRESS_INSTALLER,
            EGRESS_OK_MARKER,
            EgressRenderError,
            normalize_egress,
            render_install,
        )

        try:
            ectx = normalize_egress(egress)
        except EgressRenderError as exc:
            raise AdapterError(f"configure-router: {guest} egress policy is malformed: {exc}", error_class="egress-policy-invalid") from exc
        if ectx is None:
            return None
        ga.write_file(guest, EGRESS_INSTALLER, render_install(ectx), mode="0755")
        res = ga.exec(guest, ["/bin/sh", EGRESS_INSTALLER], timeout_s=op.timeout_s or 300)
        out = (res.stdout or "") + (res.stderr or "")
        if res.exit_code != 0 or f"{EGRESS_OK_MARKER} phase={ectx['phase']}" not in out:
            raise AdapterError(
                f"configure-router: {guest} egress phase {ectx['phase']!r} did not apply (exit {res.exit_code}): {out[-300:]}",
                error_class="egress-phase-failed",
            )
        return str(ectx["phase"])

    def _vyos_ssh_cfg(self) -> Any:
        """`ssh.apply_vyos_sets` / `run_ssh_guest` read one field of the
        Proxmox config -- the operator key PATH; here it is `HOST_SSH_KEY`.
        Both take the whole `AdapterConfig` (no positional key parameter), so
        this one-field stand-in stays until they are hoisted out of
        `adapter_proxmox/ssh.py` (held, SPEC Notes)."""
        return SimpleNamespace(ovs_ssh_key=self.config.host_ssh_key)

    def _discover_ip_by_mac(self, mac: str, bridge: str, *, attempts: int = 12, delay_s: int = 5) -> str:
        """`arp-scan` on the KVM host for a guest with no agent (VyOS)."""
        mac_l = mac.strip().lower()
        last = ""
        for i in range(max(1, attempts)):
            out = self.host.run(f"arp-scan -q -I {shlex.quote(bridge)} --localnet 2>/dev/null || true", timeout_s=30)
            last = out
            for line in out.splitlines():
                parts = line.split()
                if len(parts) >= 2 and parts[1].strip().lower() == mac_l:
                    return parts[0].strip()
            if i < attempts - 1:
                self._sleep(delay_s)
        raise AdapterError(
            f"could not resolve guest IP for MAC {mac!r} on {bridge} via arp-scan after {attempts} attempts; last scan:\n{last[:300]}",
            error_class="router-ip-discovery-failed",
        )

    def _configure_vyos_router(self, op: OpInstance, inp: dict[str, Any], ctx: ExecutionContext) -> dict[str, Any]:
        guest = self._uuid(inp)
        if inp.get("egress") is not None:
            raise AdapterError(
                f"configure-router(vyos): {guest} egress phase policy is not implemented for VyOS",
                error_class="egress-phase-unsupported-os",
            )
        dom = self._domain(guest)
        nics = interfaces(dom.XMLDesc(VIR_DOMAIN_XML_INACTIVE))
        mac = nics[0]["mac"] if nics else None
        if not mac:
            raise AdapterError(f"configure-router(vyos): {guest} has no NIC 0 MAC", error_class="router-config-failed")
        wan_bridge = inp.get("wan_bridge") or "virbr0"
        wan_ip = self._discover_ip_by_mac(mac, wan_bridge)
        ssh_user = inp.get("ci_user") or "vyos"
        wan_eth = inp.get("wan_eth") or "eth0"
        sets: list[str] = []
        for lif in inp.get("lan_ifaces") or []:
            if lif.get("eth") and lif.get("address"):
                sets.append(f"set interfaces ethernet {lif['eth']} address '{lif['address']}'")
        sets.append(f"set nat source rule 100 outbound-interface name '{wan_eth}'")
        sets.append("set nat source rule 100 translation address 'masquerade'")
        for ns in inp.get("nameservers") or []:
            sets.append(f"set system name-server '{ns}'")
        for r in inp.get("static_routes") or []:
            if r.get("to") and r.get("via"):
                sets.append(f"set protocols static route {r['to']} next-hop {r['via']}")
        apply_vyos_sets(self._vyos_ssh_cfg(), wan_ip, ssh_user, sets,
                        op_label=f"configure-router(vyos): {guest}", timeout_s=op.timeout_s or 180)
        wan_gw = ""
        try:
            _rc, gw_out = run_ssh_guest(self._vyos_ssh_cfg(), wan_ip, ssh_user, "ip -4 route show default", timeout_s=20)
            toks = gw_out.split()
            if "via" in toks:
                wan_gw = toks[toks.index("via") + 1]
        except Exception:  # noqa: BLE001 -- best-effort, as on Proxmox
            wan_gw = ""
        return {
            "configured": True, "router_os": "vyos", "wan_ip": wan_ip, "wan_gw": wan_gw,
            "wan_iface": wan_eth, "lan_ifaces": [lif.get("eth") for lif in inp.get("lan_ifaces") or []],
        }

    def op_configure_routing(self, op: OpInstance, inp: dict[str, Any], ctx: ExecutionContext) -> dict[str, Any]:
        from axrange.adapter_proxmox.fabric_render import render_routing  # pure VyOS render

        node_id = inp.get("node_id")
        wan_ip = inp.get("wan_ip")
        if not wan_ip:
            raise AdapterError(
                f"configure-routing: node={node_id} missing wan_ip (configure-router must run first and return it)",
                error_class="router-config-failed",
            )
        fabric = dict(inp.get("fabric") or {})
        if inp.get("wan_gw"):
            fabric["wan_gw"] = inp["wan_gw"]
        sets = render_routing(fabric)
        if not sets:
            return {"routing_configured": True, "lines": 0}
        apply_vyos_sets(self._vyos_ssh_cfg(), wan_ip, inp.get("ci_user") or "vyos", sets,
                        op_label=f"configure-routing: node={node_id}", timeout_s=op.timeout_s or 240)
        return {"routing_configured": True, "router_os": "vyos", "wan_ip": wan_ip, "lines": len(sets)}

    # ---- OP-MIR (host backend) ----------------------------------------
    def op_attach_gre_tunnel(self, op: OpInstance, inp: dict[str, Any], ctx: ExecutionContext) -> dict[str, Any]:
        bridge = inp.get("bridge_name")
        sensor_id = inp.get("sensor_id") or ctx.cluster.sensor_id
        sensor_ip = inp.get("sensor_transport_ip") or ctx.cluster.sensor_ip(sensor_id)
        if not bridge:
            raise AdapterError("attach-gre-tunnel: bridge_name missing", error_class="adapter-input")
        if not sensor_ip:
            raise AdapterError("attach-gre-tunnel: sensor_transport_ip missing", error_class="adapter-input")
        dev = naming.gre_port_for_bridge_sensor(bridge, sensor_id)
        key = naming.gre_key_for_bridge_sensor(bridge, sensor_id)
        t = op.timeout_s or 60
        if dev not in self.host.run(gretap_show_cmd(dev), timeout_s=15):
            self.host.run(gretap_add_cmd(dev, sensor_ip, key), timeout_s=t)
        else:
            self.host.run(gretap_heal_cmd(dev), timeout_s=t)
        self.host.run(gretap_up_cmd(dev), timeout_s=t)
        qlen = int(getattr(self.config, "mirror_gretap_txqueuelen", 0) or 0)
        if qlen:
            self.host.run(gretap_qdisc_cmd(dev, qlen), timeout_s=t)
        self.host.run(gretap_offload_cmd(dev), timeout_s=t)
        return {"gre_port": dev}

    def op_install_ovs_mirror(self, op: OpInstance, inp: dict[str, Any], ctx: ExecutionContext) -> dict[str, Any]:
        guest = self._uuid(inp)
        dom = self._domain(guest)
        name = dom.name()
        xml = dom.XMLDesc(VIR_DOMAIN_XML_INACTIVE)
        taps = {n["index"]: (n["tap"] or naming.tap_name_for_nic(guest, n["index"])) for n in interfaces(xml)}
        nic_gretaps = SshBackend._resolve_nic_gretaps(inp)   # the planner's `tapped_nics` reader, pure
        entries = [(nic, taps.get(nic) or naming.tap_name_for_nic(guest, nic), g, d) for nic, g, d in nic_gretaps]
        self.host.put_file(_hook_script(name, guest, ctx.plan_id, entries), naming.hook_script_path(guest),
                           mode="0755", timeout_s=20)
        result: dict[str, Any] = {"hook_installed": True}
        if not entries:
            result["mirror_cleared"] = True
        if not dom.isActive():
            return result
        by_tap = _group_by_tap(entries)
        t = op.timeout_s or 60
        for tap, gretaps in by_tap.items():
            self.host.run(_ethtool_off_cmd(tap), timeout_s=t)
            self.host.run(_tc_mirror_cmd(tap, gretaps), timeout_s=t)
        cleared: list[str] = []
        for tap in taps.values():
            if tap in by_tap:
                continue
            self.host.run(f"tc qdisc del dev {shlex.quote(tap)} clsact 2>/dev/null || true", timeout_s=30)
            cleared.append(tap)
        if cleared and entries:
            result["cleared_ifaces"] = cleared
        return result


_OP_TO_METHOD: dict[str, Any] = {
    "create-sdn-zone": LibvirtAdapter.op_create_sdn_zone,
    "create-subnet": LibvirtAdapter.op_create_subnet,
    "create-ovs-bridge": LibvirtAdapter.op_create_ovs_bridge,
    "create-vnet": LibvirtAdapter.op_create_vnet,
    "apply-sdn-config": LibvirtAdapter.op_apply_sdn_config,
    "clone-vm": LibvirtAdapter.op_clone_vm,
    "configure-vm-hardware": LibvirtAdapter.op_configure_vm_hardware,
    "configure-cloud-init": LibvirtAdapter.op_configure_cloud_init,
    "attach-vm-to-vnet": LibvirtAdapter.op_attach_vm_to_vnet,
    "start-vm": LibvirtAdapter.op_start_vm,
    "wait-guest-agent": LibvirtAdapter.op_wait_guest_agent,
    "wait-os-ready": LibvirtAdapter.op_wait_os_ready,
    "configure-router": LibvirtAdapter.op_configure_router,
    "configure-routing": LibvirtAdapter.op_configure_routing,
    "attach-gre-tunnel": LibvirtAdapter.op_attach_gre_tunnel,
    "install-ovs-mirror": LibvirtAdapter.op_install_ovs_mirror,
}
assert set(_OP_TO_METHOD) == LOGICAL_OPS | LIBVIRT_OPS | GUEST_OPS | HOST_OPS
