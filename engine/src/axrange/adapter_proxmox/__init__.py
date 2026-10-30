"""Proxmox adapter — one interface, two backends (docs/ADAPTER.md §2).

Public surface:

  ProxmoxAdapter      — implements axrange.planner.Adapter; routes ops to
                        the rest or ssh backend per docs/ADAPTER.md §3.
  ProxmoxInventory    — the `Inventory` seam (resolve.py's live reads).
  ProxmoxTeardown     — the `Teardown` seam (reverse-of-create).
  AdapterConfig       — connection config loaded from adapter.conf / env.
  AdapterError        — common failure type (mirrors planner.AdapterError).

The engine does not import this package directly: it reaches these through
the protocols in `axrange.platform` (`docs/ARCHITECTURE.md` §13) and the
closed factory `axrange.platform.factory` is the one module that does
(`tests/test_platform_layering.py`). `guest_agent.GuestAgent` is the
`GuestExec` seam and `naming` the `ResourceNamer` seam, structurally.

The slice (docs/ADAPTER.md §6) implements 13 REST ops + 2 SSH ops + 1 probe
op (wait-os-ready, neither REST nor SSH-into-Proxmox: it SSHs to the guest).

REST backend (rest.py) uses urllib (no requests dependency). SSH backend
(ssh.py) shells out to `ssh` and `scp` rather than depending on paramiko —
keeps the install footprint minimal and uses the operator's existing key
material the way they'd expect.
"""
from __future__ import annotations

from axrange.adapter_proxmox.adapter import ProxmoxAdapter
from axrange.adapter_proxmox.config import (
    AdapterConfig,
    ConfigError,
    ExternalSensor,
    load_config,
)
from axrange.adapter_proxmox.resolve import ProxmoxInventory
from axrange.adapter_proxmox.teardown import ProxmoxTeardown
from axrange.planner.executor import AdapterError, AdapterTimeout

__all__ = [
    "AdapterConfig",
    "AdapterError",
    "AdapterTimeout",
    "ConfigError",
    "ExternalSensor",
    "ProxmoxAdapter",
    "ProxmoxInventory",
    "ProxmoxTeardown",
    "load_config",
]
