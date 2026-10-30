"""libvirt image bindings -- `images/bindings/libvirt/<image>.yaml`.

`adapters/libvirt/SPEC.md`: "the binding carries the template volume name,
the domain XML template, and the hardware defaults"; the manifest's
platform-independent half is untouched. The document shape is the same
`binding_schema_version` / `binding:` wrapper the Proxmox bindings use and
is read through the same loader (`axrange.manifest.loader.load_registry`,
which walks every `images/bindings/<platform>/`); the per-version keys are
this platform's:

```yaml
binding_schema_version: 1
binding:
  image: debian-12-cloud
  platform: libvirt
  versions:
    - id: v1
      storage:
        type: volume               # a volume in LIBVIRT_STORAGE_POOL
        volume: img-debian-12-cloud.qcow2   # the imported `img-*` template (qcow2)
        pool: default              # optional; defaults to LIBVIRT_STORAGE_POOL
      clone_mode: linked           # linked = qcow2 overlay on the template; full = copy
      cloud_init: { supported: true, user: debian, sysprep_required: false }
      hardware:
        machine: pc                # libvirt machine type (pc = i440fx, q35)
        bios: seabios              # seabios | ovmf
        nic_model: virtio
        disk_bus: virtio           # virtio | scsi | sata
        agent: true
        vcpu: 2                    # defaults until configure-vm-hardware runs
        memory: 2Gi
      domain_xml_template: |       # optional; `$name`-style placeholders, see domain_xml.py
        <domain type='kvm'> ... </domain>
```

A template volume is the same qcow2 the Proxmox `img-*` build produces,
imported into the pool (`virsh vol-create-as <pool> <volume> <size> --format
qcow2` + `virsh vol-upload`, or copy the file into a dir pool and `virsh
pool-refresh`). A libvirt-native `axrange image build` is a follow-up.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from axrange.planner.executor import AdapterError

__all__ = ["LibvirtBinding", "LibvirtBindings"]


@dataclass
class LibvirtBinding:
    image: str
    version: str
    template_volume: str
    pool: Optional[str] = None
    clone_mode: str = "linked"
    cloud_init_user: Optional[str] = None
    machine: str = "pc"
    firmware: str = "seabios"
    nic_model: str = "virtio"
    disk_bus: str = "virtio"
    agent: bool = True
    vcpu: int = 2
    memory: str = "2Gi"
    domain_xml_template: Optional[str] = None
    raw: dict[str, Any] = field(default_factory=dict)


def _get(obj: Any, key: str, default: Any = None) -> Any:
    """Attribute or extra-field read on a pydantic model with `extra='allow'`
    (the manifest schema is permissive on purpose: `manifest/schema.py`)."""
    if obj is None:
        return default
    v = getattr(obj, key, None)
    if v is None:
        extra = getattr(obj, "model_extra", None) or {}
        v = extra.get(key)
    return default if v is None else v


class LibvirtBindings:
    """Resolve `(image_id, version)` to a `LibvirtBinding` from a bindings
    directory. Loaded once, lazily; an absent directory is an empty set (the
    failure is reported at `clone-vm`, naming the image and the directory)."""

    def __init__(self, bindings_dir: str | Path | None) -> None:
        self.dir = Path(bindings_dir).expanduser() if bindings_dir else None
        self._registry: Any = None

    def _load(self) -> Any:
        if self._registry is None:
            from axrange.manifest.loader import ManifestRegistry, load_registry

            if self.dir is None or not self.dir.is_dir():
                self._registry = ManifestRegistry()
            else:
                self._registry = load_registry(binding_dirs={"libvirt": self.dir})
        return self._registry

    def resolve(self, image_id: str, version: str | None = None) -> LibvirtBinding:
        reg = self._load()
        doc = reg.get_binding(image_id, "libvirt")
        if doc is None:
            raise AdapterError(
                f"no libvirt image binding for {image_id!r} "
                f"(looked in {self.dir or '<LIBVIRT_BINDINGS_DIR unset>'}); add "
                f"images/bindings/libvirt/{image_id}.yaml or pass `template_volume`",
                error_class="binding-missing",
            )
        if version:
            ver = doc.get_version(version)
            if ver is None:
                raise AdapterError(
                    f"libvirt binding {image_id!r} has no version {version!r}",
                    error_class="binding-missing",
                )
        else:
            if not doc.versions:
                raise AdapterError(f"libvirt binding {image_id!r} lists no versions", error_class="binding-invalid")
            ver = doc.versions[0]
        storage = _get(ver, "storage")
        volume = _get(storage, "volume")
        if not volume:
            raise AdapterError(
                f"libvirt binding {image_id!r}/{_get(ver, 'id')}: storage.volume (the "
                "template volume name in the pool) is missing",
                error_class="binding-invalid",
            )
        hw = _get(ver, "hardware")
        ci = _get(ver, "cloud_init")
        return LibvirtBinding(
            image=image_id,
            version=str(_get(ver, "id", "")),
            template_volume=str(volume),
            pool=_get(storage, "pool"),
            clone_mode=str(_get(ver, "clone_mode", "linked") or "linked"),
            cloud_init_user=_get(ci, "user"),
            machine=str(_get(hw, "machine", "pc") or "pc"),
            firmware=str(_get(hw, "bios", "seabios") or "seabios"),
            nic_model=str(_get(hw, "nic_model", "virtio") or "virtio"),
            disk_bus=str(_get(hw, "disk_bus", "virtio") or "virtio"),
            agent=bool(_get(hw, "agent", True)),
            vcpu=int(_get(hw, "vcpu", 2) or 2),
            memory=str(_get(hw, "memory", "2Gi") or "2Gi"),
            domain_xml_template=_get(ver, "domain_xml_template"),
            raw=ver.model_dump() if hasattr(ver, "model_dump") else {},
        )
