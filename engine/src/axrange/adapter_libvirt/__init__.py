"""libvirt adapter -- native QEMU/KVM on a Linux host, driven through libvirt
(`adapters/libvirt/SPEC.md`). One interface, two backends, mirroring
`axrange.adapter_proxmox`:

  LibvirtAdapter     -- `axrange.planner.executor.Adapter`; routes each op to
                        the libvirt backend (a connection) or the host backend
                        (local subprocess / SSH).
  LibvirtGuestExec   -- the `GuestExec` seam over `virDomainQemuAgentCommand`.
  LibvirtInventory   -- the `Inventory` seam.
  naming             -- the `ResourceNamer` seam (a module).
  LibvirtTeardown    -- the `Teardown` seam.
  LibvirtConfig      -- `adapter.conf` for a libvirt target; `load_config`.

Placement: a sibling of `adapter_proxmox/` under `engine/src/axrange/` until
the relocation of adapter code under `adapters/<platform>/` is decided for
both. The engine imports this package ONLY from `axrange.platform.factory`
(`tests/test_platform_layering.py`). `import libvirt` is lazy
(`connection.py`): nothing here needs the bindings until a connection opens.
"""
from __future__ import annotations

from axrange.adapter_libvirt.adapter import LibvirtAdapter
from axrange.adapter_libvirt.config import ConfigError, ExternalSensor, LibvirtConfig, load_config
from axrange.adapter_libvirt.connection import LibvirtConnection
from axrange.adapter_libvirt.guest import LibvirtGuestExec
from axrange.adapter_libvirt.inventory import LibvirtInventory
from axrange.adapter_libvirt.teardown import LibvirtTeardown
from axrange.planner.executor import AdapterError, AdapterTimeout

__all__ = [
    "AdapterError",
    "AdapterTimeout",
    "ConfigError",
    "ExternalSensor",
    "LibvirtAdapter",
    "LibvirtConfig",
    "LibvirtConnection",
    "LibvirtGuestExec",
    "LibvirtInventory",
    "LibvirtTeardown",
    "load_config",
]
