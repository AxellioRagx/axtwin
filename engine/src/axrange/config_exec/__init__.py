"""Config-class execution backend (docs/SLICE_CONFIG.md, ratified 2026-06-02).

Runs `config`-class ops (first: `OP-APP-010 provision-app`) as Ansible playbooks
delivered into the guest over the qemu-guest-agent exec channel. The executor
routes `config`-class ops here by `idempotency_class`; `infra` ops stay on
`adapter_proxmox`. The Ansible render is platform-agnostic; the QGA delivery is
the Proxmox adapter's channel (reused), so a future platform adapter swaps the
channel and keeps the render.

Public surface:

  ConfigBackend       — implements axrange.planner.Adapter for config ops.
  render_playbook     — IR install actions -> Ansible playbook (YAML).
  ConfigRenderError   — unsupported install-action kind.
"""
from __future__ import annotations

from axrange.config_exec.backend import ConfigBackend
from axrange.config_exec.render import ConfigRenderError, render_playbook

__all__ = ["ConfigBackend", "ConfigRenderError", "render_playbook"]
