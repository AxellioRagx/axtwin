"""Proxmox adapter — one interface, two backends (docs/ADAPTER.md §2).

Public surface:

  ProxmoxAdapter      — implements axrange.planner.Adapter; routes ops to
                        the rest or ssh backend per docs/ADAPTER.md §3.
  AdapterConfig       — connection config loaded from adapter.conf / env.
  AdapterError        — common failure type (mirrors planner.AdapterError).

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
from axrange.adapter_proxmox.teardown import ProxmoxTeardown
from axrange.planner.executor import AdapterError, AdapterTimeout

__all__ = [
    "AdapterConfig",
    "AdapterError",
    "AdapterTimeout",
    "ConfigError",
    "ExternalSensor",
    "ProxmoxAdapter",
    "ProxmoxTeardown",
    "load_config",
]
