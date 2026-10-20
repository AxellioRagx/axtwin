"""ProxmoxAdapter — the single interface the planner sees.

Routes each op type to one of three execution paths per docs/ADAPTER.md §3:

  REST   (Proxmox API) — SDN zone/vnet/bridge + apply; VM clone/config/
                         start/agent-ping.
  SSH    (tc/ip link on the Proxmox node) — keyed gretap netdev; per-tap
                         tc mirror hook install.
  PROBE  (orchestrator-side reachability check) — wait-os-ready only.
"""
from __future__ import annotations

from typing import Any

from axrange.adapter_proxmox.config import AdapterConfig
from axrange.adapter_proxmox.rest import RestBackend
from axrange.adapter_proxmox.ssh import SshBackend
from axrange.planner.dag import OpInstance
from axrange.planner.executor import AdapterError, ExecutionContext


# ADAPTER.md §3 originally classed `wait-os-ready` as a third "probe" path
# (orchestrator-side SSH/WinRM to the guest). In practice the orchestrator
# cannot reach the scenario's isolated SDN networks; the slice pivots
# wait-os-ready to a Proxmox qemu-guest-agent exec via REST. See
# rest.py:op_wait_os_ready and CHANGELOG 2026-05-29.
_REST_OPS = {
    "create-sdn-zone",
    "create-ovs-bridge",
    "create-vnet",
    "create-subnet",
    "apply-sdn-config",
    "clone-vm",
    "configure-vm-hardware",
    "configure-cloud-init",
    "attach-vm-to-vnet",
    "start-vm",
    "wait-guest-agent",
    "wait-os-ready",
    "configure-router",
}

_SSH_OPS = {
    "attach-gre-tunnel",
    "install-ovs-mirror",
    "configure-routing",
}

_PROBE_OPS: set[str] = set()  # reserved for future ops the slice doesn't need


class ProxmoxAdapter:
    """Implements axrange.planner.Adapter.

    Constructed once per deploy; reused across every op call. Backends share
    one HTTP and one SSH client respectively (connection reuse is a slice
    performance refinement, not a correctness requirement — see ADAPTER.md §7).
    """

    def __init__(
        self,
        config: AdapterConfig,
        *,
        rest: RestBackend | None = None,
        ssh: SshBackend | None = None,
    ) -> None:
        self.config = config
        self.rest = rest or RestBackend(config)
        self.ssh = ssh or SshBackend(config)

    # The Adapter Protocol method.
    def run(
        self,
        op: OpInstance,
        resolved_inputs: dict[str, Any],
        ctx: ExecutionContext,
    ) -> dict[str, Any]:
        name = op.op_name
        # OP-MIR-005 record-tap-intent's dispatch branch REMOVED here
        # (docs/WORK_ORDER_tap_eligibility.md, Authority: user, 2026-07-28) --
        # the op itself is retired from the catalog; a `te` deploy no longer
        # emits any OP-MIR op at all.
        if name in _REST_OPS:
            return self.rest.dispatch(op, resolved_inputs, ctx)
        if name in _SSH_OPS:
            return self.ssh.dispatch(op, resolved_inputs, ctx)
        raise AdapterError(
            f"adapter: no backend for op {name!r}; "
            "slice supports REST/SSH/probe only",
            error_class="adapter-unsupported",
        )
