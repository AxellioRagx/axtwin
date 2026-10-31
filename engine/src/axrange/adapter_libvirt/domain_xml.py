"""Domain XML -- render and edit the libvirt domain document.

The adapter never hand-concatenates XML into a live domain: `clone-vm`
renders a whole document from a `DomainSpec` (the default template below or
the image binding's `domain_xml_template`), and every later op reads the
persistent XML (`XMLDesc(INACTIVE)`), edits it with ElementTree and
re-defines it. Each editor is a pure `str -> str` so the suite asserts on
the XML it produced.

What every axRange domain carries (`adapters/libvirt/SPEC.md`,
`configure-vm-hardware` row): vcpu, memory, machine type, firmware, virtio
disk / NIC / rng / balloon, and the `org.qemu.guest_agent.0` virtio channel
the guest channel (`guest.py`) speaks over. A `<metadata>` block records the
plan/op/tier so a human can tell an axRange domain apart in `virsh`.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from string import Template
from typing import Any, Optional

from axrange.planner.executor import AdapterError

__all__ = [
    "AXRANGE_XMLNS",
    "DEFAULT_DOMAIN_TEMPLATE",
    "DomainSpec",
    "add_cdrom",
    "disk_sources",
    "ensure_agent_channel",
    "interfaces",
    "machine_of",
    "metadata_of",
    "parse_mem_kib",
    "parse_size_bytes",
    "render_domain",
    "set_hardware",
    "set_interface",
]

AXRANGE_XMLNS = "urn:axrange:deploy"
AGENT_CHANNEL = "org.qemu.guest_agent.0"
ET.register_namespace("axrange", AXRANGE_XMLNS)

#: `$name`-style template (`string.Template`), so an XML document with no `$`
#: needs no escaping. The binding's `domain_xml_template` receives the same
#: fields: name, uuid, memory_kib, vcpu, machine, disk_path, disk_bus,
#: disk_dev, plan_id, op_id, tier, firmware_attr.
DEFAULT_DOMAIN_TEMPLATE = """<domain type='kvm'>
  <name>$name</name>
  <uuid>$uuid</uuid>
  <metadata>
    <axrange:deploy xmlns:axrange='urn:axrange:deploy' plan='$plan_id' op='$op_id' tier='$tier'/>
  </metadata>
  <memory unit='KiB'>$memory_kib</memory>
  <currentMemory unit='KiB'>$memory_kib</currentMemory>
  <vcpu placement='static'>$vcpu</vcpu>
  <os$firmware_attr>
    <type arch='x86_64' machine='$machine'>hvm</type>
    <boot dev='hd'/>
  </os>
  <features>
    <acpi/>
    <apic/>
  </features>
  <cpu mode='host-passthrough' check='none'/>
  <clock offset='utc'/>
  <on_poweroff>destroy</on_poweroff>
  <on_reboot>restart</on_reboot>
  <on_crash>destroy</on_crash>
  <devices>
    <disk type='file' device='disk'>
      <driver name='qemu' type='qcow2' discard='unmap'/>
      <source file='$disk_path'/>
      <target dev='$disk_dev' bus='$disk_bus'/>
    </disk>
    <controller type='scsi' model='virtio-scsi'/>
    <serial type='pty'>
      <target port='0'/>
    </serial>
    <console type='pty'>
      <target type='serial' port='0'/>
    </console>
    <channel type='unix'>
      <source mode='bind'/>
      <target type='virtio' name='org.qemu.guest_agent.0'/>
    </channel>
    <rng model='virtio'>
      <backend model='random'>/dev/urandom</backend>
    </rng>
    <graphics type='vnc' port='-1' autoport='yes' listen='127.0.0.1'/>
    <video>
      <model type='virtio'/>
    </video>
    <memballoon model='virtio'/>
  </devices>
</domain>
"""


@dataclass
class DomainSpec:
    name: str
    uuid: str
    disk_path: str
    plan_id: str = ""
    op_id: str = ""
    tier: str = "te"
    vcpu: int = 2
    memory_kib: int = 2 * 1024 * 1024
    machine: str = "pc"
    firmware: str = "seabios"      # seabios | ovmf
    disk_bus: str = "virtio"       # virtio | scsi | sata
    pool: str = ""                 # the storage pool holding the overlay (recorded in metadata)
    nic_model: str = ""            # the binding's NIC model (recorded in metadata; attach-vm-to-vnet reads it)
    ci_user: str = ""              # the binding's cloud-init user (recorded in metadata; configure-cloud-init reads it)


def _disk_dev_for(bus: str) -> str:
    return {"virtio": "vda", "scsi": "sda", "sata": "sda", "ide": "hda"}.get(bus, "vda")


def _firmware_attr(firmware: str | None) -> str:
    return " firmware='efi'" if (firmware or "").lower() in ("ovmf", "efi", "uefi") else ""


def render_domain(spec: DomainSpec, template: str | None = None) -> str:
    """The persistent domain XML for a fresh clone. `template` overrides the
    default document (an image binding's `domain_xml_template`); whichever
    is used, the agent channel and the axRange metadata are asserted after
    rendering so a binding cannot silently drop them."""
    fields = {
        "name": spec.name,
        "uuid": spec.uuid,
        "memory_kib": int(spec.memory_kib),
        "vcpu": int(spec.vcpu),
        "machine": spec.machine or "pc",
        "disk_path": spec.disk_path,
        "disk_bus": spec.disk_bus or "virtio",
        "disk_dev": _disk_dev_for(spec.disk_bus or "virtio"),
        "plan_id": spec.plan_id,
        "op_id": spec.op_id,
        "tier": spec.tier,
        "firmware_attr": _firmware_attr(spec.firmware),
    }
    try:
        xml = Template(template or DEFAULT_DOMAIN_TEMPLATE).substitute(fields)
    except (KeyError, ValueError) as e:
        raise AdapterError(
            f"domain XML template: unknown placeholder {e} (known: {sorted(fields)})",
            error_class="binding-invalid",
        ) from e
    root = _parse(xml)
    _ensure_metadata(root, spec)
    ensure_agent_channel(root)
    return _dump(root)


# ---------------------------------------------------------------------------
# editors
# ---------------------------------------------------------------------------


def _parse(xml: str) -> ET.Element:
    try:
        return ET.fromstring(xml)
    except ET.ParseError as e:
        raise AdapterError(f"domain XML does not parse: {e}", error_class="libvirt-xml") from e


def _dump(root: ET.Element) -> str:
    return ET.tostring(root, encoding="unicode")


def _devices(root: ET.Element) -> ET.Element:
    dev = root.find("devices")
    if dev is None:
        dev = ET.SubElement(root, "devices")
    return dev


def _ensure_metadata(root: ET.Element, spec: DomainSpec) -> None:
    meta = root.find("metadata")
    if meta is None:
        meta = ET.SubElement(root, "metadata")
    tag = f"{{{AXRANGE_XMLNS}}}deploy"
    el = meta.find(tag)
    if el is None:
        el = ET.SubElement(meta, tag)
    el.set("plan", spec.plan_id)
    el.set("op", spec.op_id)
    el.set("tier", spec.tier)
    if spec.pool:
        el.set("pool", spec.pool)
    if spec.nic_model:
        el.set("nic_model", spec.nic_model)
    if spec.ci_user:
        el.set("ci_user", spec.ci_user)


def ensure_agent_channel(root: ET.Element) -> bool:
    """Assert the `org.qemu.guest_agent.0` virtio channel; True if added."""
    dev = _devices(root)
    for ch in dev.findall("channel"):
        t = ch.find("target")
        if t is not None and t.get("name") == AGENT_CHANNEL:
            return False
    ch = ET.SubElement(dev, "channel", {"type": "unix"})
    ET.SubElement(ch, "source", {"mode": "bind"})
    ET.SubElement(ch, "target", {"type": "virtio", "name": AGENT_CHANNEL})
    return True


def set_hardware(
    xml: str,
    *,
    vcpu: Optional[int] = None,
    memory_kib: Optional[int] = None,
    machine: Optional[str] = None,
    firmware: Optional[str] = None,
    agent: Optional[bool] = None,
) -> str:
    """`configure-vm-hardware`: vcpu / memory / machine / firmware, and the
    agent channel asserted when `agent` is not False. Untouched fields keep
    the document's values."""
    root = _parse(xml)
    if vcpu is not None:
        el = root.find("vcpu")
        if el is None:
            el = ET.SubElement(root, "vcpu", {"placement": "static"})
        el.text = str(int(vcpu))
    if memory_kib is not None:
        for tag in ("memory", "currentMemory"):
            el = root.find(tag)
            if el is None:
                el = ET.SubElement(root, tag)
            el.set("unit", "KiB")
            el.text = str(int(memory_kib))
    os_el = root.find("os")
    if os_el is None:
        os_el = ET.SubElement(root, "os")
    if machine:
        t = os_el.find("type")
        if t is None:
            t = ET.SubElement(os_el, "type", {"arch": "x86_64"})
            t.text = "hvm"
        t.set("machine", machine)
    if firmware:
        if _firmware_attr(firmware):
            os_el.set("firmware", "efi")
        else:
            os_el.attrib.pop("firmware", None)
            for loader in os_el.findall("loader"):
                os_el.remove(loader)
    if agent is not False:
        ensure_agent_channel(root)
    return _dump(root)


def set_interface(
    xml: str,
    nic_index: int,
    *,
    mac: str,
    network: Optional[str] = None,
    portgroup: Optional[str] = None,
    tap: Optional[str] = None,
    model: str = "virtio",
) -> str:
    """`attach-vm-to-vnet`: the `<interface type='network'>` at position
    `nic_index` (replaced if present, appended -- with placeholders for any
    gap -- if not). `portgroup` selects a VLAN-zone portgroup on the
    underlay network; `tap` pins the host-side netdev name."""
    if not network:
        raise AdapterError("set_interface: network missing", error_class="adapter-input")
    root = _parse(xml)
    dev = _devices(root)
    ifaces = dev.findall("interface")
    new = ET.Element("interface", {"type": "network"})
    ET.SubElement(new, "mac", {"address": mac})
    src = {"network": network}
    if portgroup:
        src["portgroup"] = portgroup
    ET.SubElement(new, "source", src)
    if tap:
        ET.SubElement(new, "target", {"dev": tap})
    ET.SubElement(new, "model", {"type": model})
    if nic_index < len(ifaces):
        old = ifaces[nic_index]
        pos = list(dev).index(old)
        dev.remove(old)
        dev.insert(pos, new)
    else:
        # Append after the last interface (or the disks) so NIC order in the
        # document is the nic_index order libvirt enumerates.
        anchor = ifaces[-1] if ifaces else (dev.findall("disk") or [None])[-1]
        pos = list(dev).index(anchor) + 1 if anchor is not None else len(list(dev))
        dev.insert(pos, new)
    return _dump(root)


def interfaces(xml: str) -> list[dict[str, Any]]:
    """Every `<interface>` in document order: `{index, mac, tap, network,
    portgroup, bridge}`."""
    root = _parse(xml)
    out: list[dict[str, Any]] = []
    for i, it in enumerate(_devices(root).findall("interface")):
        mac = it.find("mac")
        src = it.find("source")
        tgt = it.find("target")
        out.append({
            "index": i,
            "mac": mac.get("address") if mac is not None else None,
            "tap": tgt.get("dev") if tgt is not None else None,
            "network": src.get("network") if src is not None else None,
            "portgroup": src.get("portgroup") if src is not None else None,
            "bridge": src.get("bridge") if src is not None else None,
        })
    return out


def _free_cdrom_dev(devices: ET.Element, q35: bool) -> str:
    """First `<target dev>` of the bus family (`sd?` on q35's SATA, `hd?` on
    i440fx's IDE) no existing disk already uses: a sata/scsi boot disk is
    `sda` (`_disk_dev_for`), so a fixed `sda` would collide."""
    taken = {t.get("dev") for d in devices.findall("disk") if (t := d.find("target")) is not None}
    prefix = "sd" if q35 else "hd"
    # i440fx keeps `hdc` (cloud images find cidata on /dev/sr0) as its first choice.
    letters = "abcdefghijklmnopqrstuvwxyz"
    if not q35:
        letters = "cdab" + letters[4:]
    for ch in letters:
        if f"{prefix}{ch}" not in taken:
            return f"{prefix}{ch}"
    raise AdapterError("no free sd?/hd? target for the cloud-init CD-ROM", error_class="libvirt-xml")


def add_cdrom(xml: str, iso_path: str) -> str:
    """`configure-cloud-init`: attach (or re-point) the NoCloud seed ISO as a
    read-only CD-ROM. Bus follows the machine type: SATA on q35 (no IDE
    controller there), IDE on i440fx (`pc`) where the cloud images find the
    cidata on /dev/sr0 (the Proxmox binding's own note)."""
    root = _parse(xml)
    dev = _devices(root)
    for d in dev.findall("disk"):
        if d.get("device") == "cdrom":
            src = d.find("source")
            if src is None:
                src = ET.SubElement(d, "source")
            src.set("file", iso_path)
            return _dump(root)
    q35 = "q35" in (machine_of(xml) or "")
    cd = ET.Element("disk", {"type": "file", "device": "cdrom"})
    ET.SubElement(cd, "driver", {"name": "qemu", "type": "raw"})
    ET.SubElement(cd, "source", {"file": iso_path})
    ET.SubElement(cd, "target", {"dev": _free_cdrom_dev(dev, q35), "bus": "sata" if q35 else "ide"})
    ET.SubElement(cd, "readonly")
    disks = dev.findall("disk")
    anchor = disks[-1] if disks else None
    pos = list(dev).index(anchor) + 1 if anchor is not None else 0
    dev.insert(pos, cd)
    return _dump(root)


def disk_sources(xml: str) -> list[str]:
    """Every `<disk><source file=...>` path, disks and CD-ROMs alike."""
    root = _parse(xml)
    out = []
    for d in _devices(root).findall("disk"):
        src = d.find("source")
        if src is not None and src.get("file"):
            out.append(src.get("file"))
    return out


def metadata_of(xml: str) -> dict[str, str]:
    """The axRange `<metadata>` attributes (`plan`, `op`, `tier`, `pool`,
    `nic_model`, `ci_user`), or
    `{}` for a domain axRange did not define."""
    root = _parse(xml)
    el = root.find(f"./metadata/{{{AXRANGE_XMLNS}}}deploy")
    return dict(el.attrib) if el is not None else {}


def machine_of(xml: str) -> str | None:
    root = _parse(xml)
    t = root.find("./os/type")
    return t.get("machine") if t is not None else None


# ---------------------------------------------------------------------------
# sizes
# ---------------------------------------------------------------------------

_SIZE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([KMGT]i?)?B?\s*$", re.IGNORECASE)
_MULT = {"": 1, "K": 1024, "M": 1024 ** 2, "G": 1024 ** 3, "T": 1024 ** 4}


def parse_size_bytes(spec: str) -> int:
    """IR size strings (`4Gi`, `512Mi`, `10G`, `8192` = MiB) to bytes; binary
    units throughout, as the manifest's `resources.ram`/`disk` convention."""
    m = _SIZE_RE.match(str(spec))
    if not m:
        raise AdapterError(f"size {spec!r} is not a valid size (e.g. 4Gi, 10G, 512Mi)", error_class="adapter-input")
    number = float(m.group(1))
    unit = (m.group(2) or "").upper().rstrip("I")
    if not unit:
        return int(number * _MULT["M"])  # a bare number is MiB (the Proxmox convention)
    return int(number * _MULT[unit])


def parse_mem_kib(spec: str) -> int:
    return max(1, parse_size_bytes(spec) // 1024)
