"""SSH backend for the Proxmox adapter.

SSH backend (docs/ADAPTER.md §3): the two mirror-plane ops that have no
Proxmox API surface today. **Re-mechanized to `tc`/`ip link` for SDN-native
capture (Option 1, CHANGELOG 2026-06-04):** PVE SDN materializes every vnet
as a *Linux* bridge holding the VM taps, so the old per-VM `ovs-vsctl`
port-mirror had no OVS port to bind (capture was silently dead). We now mirror
**per-tap with `tc`** on the tap, into a keyed gretap netdev — capture no
longer depends on the bridge being an OVS bridge.

  attach-gre-tunnel    `ip link add <dev> type gretap remote <sensor> key <k>`
                       — a keyed gretap netdev per (bridge, sensor); the mirror
                       target the per-tap `tc` rules point at.
  install-ovs-mirror   write the post-start/pre-stop hook (tc clsact + matchall
                       mirred on the VM tap → the gretap), register it on the
                       VM with `qm set <vmid> -hookscript ...`. (Op id/name kept
                       stable; a rename is a separate catalog change.)

We shell out to `ssh` and `scp` rather than depend on paramiko — keeps the
install footprint to "what every operator already has" and uses the
operator's existing key material exactly as they'd expect.

Historical note: ADAPTER.md §3 originally classed `wait-os-ready` as a
third "probe" path (orchestrator-side SSH/WinRM to the guest). The slice
moved it to a Proxmox qemu-guest-agent exec via REST (rest.py:
op_wait_os_ready) because the orchestrator can't reach isolated SDN
networks. The orchestrator-side SSH probe has been removed from this
module; its body is no longer referenced anywhere.
"""
from __future__ import annotations

import shlex
import subprocess
from pathlib import Path
from typing import Any

from axrange.adapter_proxmox.config import AdapterConfig
from axrange.adapter_proxmox.naming import (
    gre_key_for_bridge_sensor,
    gre_port_for_bridge_sensor,
)
from axrange.planner.dag import OpInstance
from axrange.planner.executor import AdapterError, AdapterTimeout, ExecutionContext


# gretap netdev MTU. The mirror carries full scenario Ethernet frames (up to
# 1500 B of L3 payload), so the gretap must accept 1500. The ENCAPSULATED frame
# is 20 (outer IPv4) + 8 (GRE + key) + 14 (inner Ethernet) [+ 4 per 802.1Q tag]
# + 1500 = 1542 / 1546 B -- larger than a 1500-MTU transport vnet, which is what
# every transport on dev45 is. `_GRETAP_PMTU_OPTS` makes the tunnel fragment the
# outer packet instead of dropping it (docs/ARCHITECTURE.md §3 MTU math).
_GRETAP_MTU = 1500

# Outer-IP DF policy for the mirror gretap: never set DF on the outer header and
# never copy it from the inner one, so an oversized encapsulation is FRAGMENTED
# on the transport (the sensor's kernel reassembles before GRE decap) rather
# than dropped. With the kernel default (`pmtudisc`, outer DF set) a mirrored
# full-size frame is lost two ways, both silent -- a mirror has no sender to
# receive the ICMP frag-needed:
#   * inner IPv4 with DF (every TCP bulk segment): `tnl_update_pmtu` -> -E2BIG
#     -> counted as the gretap's `tx_errors`;
#   * anything else (802.1Q-tagged trunk frames, non-DF IP): outer DF packet >
#     transport MTU -> `ip_fragment` FragFails -> the gretap's `tx_dropped`.
# Measured corp-twin live run 3 (dev45, 2026-09-28): ~30% of frames on wan1-isp
# (tx_errors) and ~17% on sfp25 (tx_dropped); the sum of tx_dropped across all
# gretaps equalled the host's `IpFragFails` delta. Reproduced in a netns rig:
# 150/150 full-size mirrored frames lost without these flags, 0 lost with them.
# `ignore-df` requires `nopmtudisc` (the kernel rejects it alongside outer DF).
_GRETAP_PMTU_OPTS = "nopmtudisc ignore-df"


# ---------------------------------------------------------------------------
# SSH helper
# ---------------------------------------------------------------------------


_SSH_OPTS = [
    "-o", "BatchMode=yes",
    "-o", "StrictHostKeyChecking=accept-new",
    "-o", "ConnectTimeout=10",
]


def _ssh_cmd(cfg: AdapterConfig, *, host_override: str | None = None,
             user_override: str | None = None) -> list[str]:
    cmd = ["ssh", *list(_SSH_OPTS)]
    if cfg.ovs_ssh_key:
        cmd.extend(["-i", cfg.ovs_ssh_key])
    user = user_override or cfg.ovs_ssh_user
    host = host_override or cfg.ovs_ssh_host
    if not host:
        raise AdapterError(
            "SSH host not set; populate OVS_SSH_HOST in adapter.conf",
            error_class="adapter-config",
        )
    cmd.append(f"{user}@{host}")
    return cmd


def _scp_cmd(cfg: AdapterConfig, *, host_override: str | None = None,
             user_override: str | None = None) -> list[str]:
    cmd = ["scp", *list(_SSH_OPTS)]
    if cfg.ovs_ssh_key:
        cmd.extend(["-i", cfg.ovs_ssh_key])
    return cmd  # caller appends src + dest


def run_ssh(
    cfg: AdapterConfig,
    remote_cmd: str,
    *,
    timeout_s: int = 60,
    host_override: str | None = None,
    user_override: str | None = None,
) -> str:
    """Run `remote_cmd` on the Proxmox node via SSH. Returns stdout text.

    Raises AdapterError on non-zero exit; AdapterTimeout on subprocess timeout.
    """
    argv = _ssh_cmd(cfg, host_override=host_override, user_override=user_override)
    argv.append(remote_cmd)
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        raise AdapterTimeout(
            f"ssh {host_override or cfg.ovs_ssh_host}: timeout after {timeout_s}s"
        ) from e
    if proc.returncode != 0:
        raise AdapterError(
            f"ssh {host_override or cfg.ovs_ssh_host} {remote_cmd!r}: "
            f"exit {proc.returncode}; stderr={proc.stderr.strip()[:200]}",
            error_class="ssh-cmd-failed",
        )
    return proc.stdout


def scp_to_remote(
    cfg: AdapterConfig,
    local_content: str,
    remote_path: str,
    *,
    timeout_s: int = 30,
) -> None:
    """Write `local_content` to a temp file and scp it to `remote_path`."""
    import tempfile

    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".tmp") as tf:
        tf.write(local_content)
        tf.flush()
        local_path = tf.name
    try:
        argv = _scp_cmd(cfg)
        argv.append(local_path)
        argv.append(f"{cfg.ovs_ssh_user}@{cfg.ovs_ssh_host}:{remote_path}")
        try:
            proc = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired as e:
            raise AdapterTimeout(
                f"scp to {cfg.ovs_ssh_host}: timeout after {timeout_s}s"
            ) from e
        if proc.returncode != 0:
            raise AdapterError(
                f"scp to {cfg.ovs_ssh_host}:{remote_path}: "
                f"exit {proc.returncode}; stderr={proc.stderr.strip()[:200]}",
                error_class="ssh-scp-failed",
            )
    finally:
        Path(local_path).unlink(missing_ok=True)


_SSH_FAIL_MARKERS = (
    "permission denied",
    "connection refused",
    "connection timed out",
    "could not resolve",
    "no route to host",
    "connection closed by",
    "host key verification failed",
)


def ssh_connect_failed(rc: int, out: str) -> bool:
    """True if the SSH *transport* failed (auth / connect / DNS), as opposed to the
    remote command returning non-zero. rc 255 is ssh's canonical connection-failure
    code; the markers catch the common stderr lines.

    VyOS config handlers must call this BEFORE the config-error-marker scan: a failed
    SSH (e.g. an unauthorized key — `Permission denied`) produces output that matches
    none of the VyOS config-error markers, so without this check the op would mistake
    a totally-unconfigured router for a benign 'no configuration changes' success.
    """
    low = (out or "").lower()
    return rc == 255 or any(m in low for m in _SSH_FAIL_MARKERS)


_VYOS_ERROR_MARKERS = (
    "set failed",
    "commit failed",
    "is not valid",
    "invalid value",
    "configuration error",
    "aborted",
)


def apply_vyos_sets(
    cfg: AdapterConfig,
    wan_ip: str,
    ssh_user: str,
    sets: list[str],
    *,
    op_label: str,
    timeout_s: int = 180,
) -> str:
    """Apply a flat list of VyOS `set ...` commands via the vbash
    `script-template` envelope (`configure` -> sets -> `commit`/`save`/`exit`).

    Shared by `configure-router` (rest.py `_configure_vyos_router_ssh`) and
    `configure-routing` (`SshBackend._configure_routing` below) — both built
    the identical envelope + LES-005 error-detection order independently
    (code-review finding #4, 2026-07-07). Gate order matters: check the SSH
    *transport* result first (`ssh_connect_failed`), THEN scan for VyOS
    config-error markers — `commit` returns non-zero on an idempotent "No
    configuration changes" re-run, so exit code alone can't distinguish a
    benign no-op from a real failure, and a marker scan alone would mistake an
    auth/connect failure for success (LES-005).

    Returns the combined stdout+stderr on success. Raises AdapterError with
    `op_label` as context: `error_class="router-ssh-failed"` for an SSH
    transport failure, `error_class="router-config-failed"` for a VyOS apply
    error.
    """
    script = (
        "source /opt/vyatta/etc/functions/script-template\n"
        "configure\n"
        + "\n".join(sets)
        + "\ncommit\nsave\nexit\n"
    )
    rc, out = run_ssh_guest(
        cfg, wan_ip, ssh_user, "vbash -s", stdin_text=script, timeout_s=timeout_s,
    )
    if ssh_connect_failed(rc, out):
        raise AdapterError(
            f"{op_label}: SSH to {wan_ip} as {ssh_user!r} failed (rc={rc}) — "
            f"router unreachable or key not authorized: {out[:200]}",
            error_class="router-ssh-failed",
        )
    low = out.lower()
    if any(m in low for m in _VYOS_ERROR_MARKERS):
        raise AdapterError(
            f"{op_label}: ip={wan_ip} apply failed: {out[:400]}",
            error_class="router-config-failed",
        )
    return out


def run_ssh_guest(
    cfg: AdapterConfig,
    host: str,
    user: str,
    remote_cmd: str,
    *,
    stdin_text: str | None = None,
    timeout_s: int = 60,
) -> tuple[int, str]:
    """SSH from the orchestrator directly to a *guest* (not the PVE host) using
    the operator key, returning (exit_code, combined stdout+stderr).

    Used by the VyOS router config path (vyos-ssh-set): the orchestrator reaches
    the router on its WAN/mgmt IP and pipes a vbash `set`-command script in over
    stdin. Unlike `run_ssh` (PVE host; raises on non-zero), this returns the exit
    code so the caller can do marker-based error detection — VyOS `commit` exits
    non-zero on "No configuration changes to commit" (a benign idempotent re-run),
    so exit code alone is not a reliable failure signal.
    """
    cmd = ["ssh", *list(_SSH_OPTS)]
    if cfg.ovs_ssh_key:
        cmd.extend(["-i", cfg.ovs_ssh_key])
    cmd.append(f"{user}@{host}")
    cmd.append(remote_cmd)
    try:
        proc = subprocess.run(
            cmd,
            input=stdin_text,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        raise AdapterTimeout(
            f"ssh {user}@{host}: timeout after {timeout_s}s"
        ) from e
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def discover_guest_ip_by_mac(
    cfg: AdapterConfig,
    mac: str,
    *,
    bridge: str = "vmbr0",
    attempts: int = 12,
    delay_s: int = 5,
) -> str:
    """Resolve a guest's DHCP IP from its NIC MAC via `arp-scan` on the PVE host.

    VyOS routers have `agent: false` (no QGA to report their IP), and the
    orchestrator is typically routed to the scenario uplink (can't ARP it
    directly) — but the PVE host sits on the uplink L2. So we run arp-scan over
    the existing host-SSH channel and poll until the MAC appears (the router
    needs time to boot + DHCP after start-vm). `arp-scan` must be installed on
    the host (documented prerequisite, alongside openvswitch).
    """
    import time as _time

    mac_l = mac.strip().lower()
    last = ""
    for i in range(max(1, attempts)):
        out = run_ssh(
            cfg,
            f"arp-scan -q -I {shlex.quote(bridge)} --localnet 2>/dev/null || true",
            timeout_s=30,
        )
        last = out
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[1].strip().lower() == mac_l:
                return parts[0].strip()
        if i < attempts - 1:
            _time.sleep(delay_s)
    raise AdapterError(
        f"could not resolve guest IP for MAC {mac!r} on {bridge} via arp-scan "
        f"after {attempts} attempts (router not up / no DHCP lease?); "
        f"last scan:\n{last[:300]}",
        error_class="router-ip-discovery-failed",
    )


# ---------------------------------------------------------------------------
# Hook script content
# ---------------------------------------------------------------------------


# The per-tap `tc` mirror lives in a Proxmox VM hook (docs/ARCHITECTURE.md §3):
# the VM tap (`tap<vmid>i0`) is recreated on every boot, so a mirror referencing
# the old tap captures nothing after a reboot. The hook rebuilds the rules on
# every `post-start` and tears them down on `pre-stop` — stateless, self-healing
# from the plan. The keyed gretap netdev it mirrors INTO is created once per
# (bridge, sensor) at deploy time by attach-gre-tunnel; the hook only manages the
# tap-side clsact qdisc + filters.
#
# Per-gretap DIRECTION (`docs/WORK_ORDER_tap_scopes.md` M5): every gretap still
# gets the ingress (rx) filter -- a multi-port scope's whole point is exactly-
# once capture at the RX side. The egress (tx) filter chains ONLY the
# `"both"`-direction gretaps (a single-port scope, `port`/`perimeter`), and is
# OMITTED ENTIRELY when a NIC has no `"both"` destination at all -- installing
# an empty-action `tc filter` is not just wasted, it is invalid `tc` syntax.
_HOOK_SCRIPT_TEMPLATE = """#!/bin/bash
# axRange per-tap tc mirror hook (Option 1 — CHANGELOG 2026-06-04;
# per-gretap direction — docs/WORK_ORDER_tap_scopes.md M5).
# Generated by axrange/adapter_proxmox/ssh.py.
# Plan:   {plan_id}
# Mirrors (nic_index:gretap:direction), set by the resolved tapped-interface
# set (dynamic taps, docs/design/06-taps-sensors.md): {pairs_comment}
#
# PVE SDN places the VM tap on a Linux vnet bridge, so we mirror AT THE TAP with
# tc (clsact + matchall mirred) into the keyed gretap netdev, not with an OVS
# port-mirror. Mirroring at the tap captures east-west traffic between VMs on
# one vnet too (the tap sits ahead of bridge switching). A port/segment/
# perimeter/boundary tap may select several NICs, each into its own
# (net,sensor) gretap — the loop below handles 1..N tapped NICs uniformly.
set -e
vmid="$1"
phase="$2"
# Space-separated "nicindex:gretap:direction" triples for this VM's tapped NICs.
pairs="{pairs}"

nics=$(for pair in $pairs; do echo "${{pair%%:*}}"; done | sort -u)
case "$phase" in
  post-start)
    # Per unique tapped NIC: reset the clsact qdisc, then add ONE matchall
    # filter per direction whose action list CHAINS one `mirred mirror` per
    # gretap (`...mirror dev g1 pipe action mirred ...mirror dev g2`). A NIC
    # that fans out to several gretaps (multi-sensor capture) must mirror into
    # ALL of them; two SEPARATE matchall filters don't both fire — matchall
    # matches and stops filter traversal, so only the first runs (the C4
    # fan-out finding, 2026-06-23). Chained actions on ONE filter all run.
    for nic in $nics; do
      vmnic="tap${{vmid}}i${{nic}}"
      # Segmentation offload OFF before mirroring. A tap netdev with GSO/TSO on carries
      # super-frames of up to 64 KB; `mirred` copies them verbatim into a 1500-MTU gretap, which
      # cannot encapsulate them and drops them as tx errors -- silently, because a mirror is
      # fire-and-forget. Measured 2026-08-27: doing this on the source ports removed the drops on
      # 9 of 11 gretaps, and the tunnel's own offload (asserted in `attach-gre-tunnel`) removed
      # the rest. Both halves are needed; neither is sufficient.
      #
      # In the HOOK, not one-shot at deploy: the tap netdev is recreated on every VM boot, so a
      # one-time ethtool would be lost at the first reboot exactly like the mirror itself.
      ethtool -K "$vmnic" gso off tso off gro off >/dev/null 2>&1 || true
      tc qdisc del dev "$vmnic" clsact 2>/dev/null || true
      tc qdisc add dev "$vmnic" clsact
      ingress_acts=""
      egress_acts=""
      for pair in $pairs; do
        pnic="${{pair%%:*}}"
        rest="${{pair#*:}}"
        g="${{rest%%:*}}"
        dir="${{rest##*:}}"
        [ "$pnic" = "$nic" ] || continue
        if [ -z "$ingress_acts" ]; then ingress_acts="action mirred egress mirror dev $g";
        else ingress_acts="$ingress_acts pipe action mirred egress mirror dev $g"; fi
        if [ "$dir" = "both" ]; then
          if [ -z "$egress_acts" ]; then egress_acts="action mirred egress mirror dev $g";
          else egress_acts="$egress_acts pipe action mirred egress mirror dev $g"; fi
        fi
      done
      tc filter add dev "$vmnic" ingress matchall $ingress_acts
      if [ -n "$egress_acts" ]; then
        tc filter add dev "$vmnic" egress matchall $egress_acts
      fi
      # Fail loud, not silent (the 2026-06-03 silent-zero-capture finding).
      tc filter show dev "$vmnic" ingress | grep -q mirred
    done
    ;;
  pre-stop)
    for nic in $nics; do
      tc qdisc del dev "tap${{vmid}}i${{nic}}" clsact 2>/dev/null || true
    done
    ;;
esac
exit 0
"""


def _hook_script(plan_id: str, nic_gretaps: list[tuple[int, str, str]]) -> str:
    pairs = " ".join(f"{nic}:{gretap}:{direction}" for nic, gretap, direction in nic_gretaps)
    pairs_comment = ", ".join(
        f"{nic}->{gretap}({direction})" for nic, gretap, direction in nic_gretaps
    )
    return _HOOK_SCRIPT_TEMPLATE.format(
        plan_id=plan_id, pairs=pairs, pairs_comment=pairs_comment
    )


def _tc_mirror_cmd(vmnic: str, gretaps: list[tuple[str, str]]) -> str:
    """The clsact + mirred install applied to a live tap, identical to what the
    post-start hook installs. Used for the in-band path (VM already running on a
    re-deploy, so the hook won't fire). Resets the clsact qdisc ONCE, then adds
    ONE matchall filter per direction whose action list CHAINS one `mirred mirror`
    per gretap — so a NIC that fans out to several gretaps (multi-sensor capture)
    mirrors into ALL of them. Two separate matchall filters would NOT both fire
    (matchall matches and stops filter traversal — only the first runs; the C4
    fan-out finding, 2026-06-23). Idempotent: drops any prior clsact first.

    `gretaps` is `[(gretap, direction), ...]` (`docs/WORK_ORDER_tap_scopes.md`
    M5) -- ingress chains ALL of them; egress chains only the `"both"`-
    direction ones, and the egress `tc filter add` is skipped entirely when
    none qualify (an empty-action filter is invalid `tc` syntax, not just
    wasted).
    """
    q_nic = shlex.quote(vmnic)
    ingress_acts = " pipe ".join(
        f"action mirred egress mirror dev {shlex.quote(g)}" for g, _d in gretaps
    )
    both_gretaps = [g for g, d in gretaps if d == "both"]
    cmds = [
        f"tc qdisc del dev {q_nic} clsact 2>/dev/null || true",
        f"tc qdisc add dev {q_nic} clsact",
        f"tc filter add dev {q_nic} ingress matchall {ingress_acts}",
    ]
    if both_gretaps:
        egress_acts = " pipe ".join(
            f"action mirred egress mirror dev {shlex.quote(g)}" for g in both_gretaps
        )
        cmds.append(f"tc filter add dev {q_nic} egress matchall {egress_acts}")
    return " && ".join(cmds)


# ---------------------------------------------------------------------------
# Backend dispatcher
# ---------------------------------------------------------------------------


class SshBackend:
    """The two SSH-routed ops + tiny shared SSH helpers."""

    def __init__(self, cfg: AdapterConfig) -> None:
        self.cfg = cfg

    def dispatch(
        self,
        op: OpInstance,
        resolved_inputs: dict[str, Any],
        ctx: ExecutionContext,
    ) -> dict[str, Any]:
        if op.op_name == "attach-gre-tunnel":
            return self._attach_gre_tunnel(op, resolved_inputs, ctx)
        if op.op_name == "install-ovs-mirror":
            return self._install_ovs_mirror(op, resolved_inputs, ctx)
        if op.op_name == "configure-routing":
            return self._configure_routing(op, resolved_inputs, ctx)
        raise AdapterError(
            f"SSH backend has no implementation for {op.op_name!r}",
            error_class="adapter-unsupported",
        )

    def _configure_routing(
        self, op: OpInstance, inp: dict[str, Any], ctx: ExecutionContext
    ) -> dict[str, Any]:
        """Apply VyOS dynamic-routing/overlay config (fabric slice, OP-HOST-100).

        Runs after configure-router, reusing its discovered `wan_ip` (no second
        arp-scan). Renders the per-router fabric context to `set protocols ...`
        lines (fabric_render.render_routing) and applies them through VyOS's
        vbash script-template — the same envelope + marker-based error check as
        configure-router. Idempotent: `commit` is a no-op on unchanged config.
        """
        from axrange.adapter_proxmox.fabric_render import render_routing

        node_id = inp.get("node_id")
        wan_ip = inp.get("wan_ip")
        fabric = dict(inp.get("fabric") or {})
        ssh_user = inp.get("ci_user") or "vyos"
        if not wan_ip:
            raise AdapterError(
                f"configure-routing: node={node_id} missing wan_ip "
                "(configure-router must run first and return it)",
                error_class="router-config-failed",
            )
        # The WAN gateway (discovered by configure-router) drives the tenant-VRF
        # egress leak in the IRB renderer; a runtime value, injected into the ctx.
        wan_gw = inp.get("wan_gw")
        if wan_gw:
            fabric["wan_gw"] = wan_gw
        sets = render_routing(fabric)
        if not sets:
            return {"routing_configured": True, "lines": 0}
        # Shared envelope + LES-005 error-detection order with configure-router
        # (finding #4, 2026-07-07) — see `apply_vyos_sets` docstring.
        apply_vyos_sets(
            self.cfg, wan_ip, ssh_user, sets,
            op_label=f"configure-routing: node={node_id}",
            timeout_s=op.timeout_s or 240,
        )
        return {
            "routing_configured": True,
            "router_os": "vyos",
            "wan_ip": wan_ip,
            "lines": len(sets),
        }

    def _attach_gre_tunnel(
        self, op: OpInstance, inp: dict[str, Any], ctx: ExecutionContext
    ) -> dict[str, Any]:
        bridge = inp.get("bridge_name")
        sensor_id = inp.get("sensor_id") or ctx.cluster.sensor_id
        sensor_ip = inp.get("sensor_transport_ip") or ctx.cluster.sensor_ip(sensor_id)
        if not bridge:
            raise AdapterError(
                "attach-gre-tunnel: bridge_name missing",
                error_class="adapter-input",
            )
        if not sensor_ip:
            raise AdapterError(
                "attach-gre-tunnel: sensor_transport_ip missing",
                error_class="adapter-input",
            )
        # Option 1 (CHANGELOG 2026-06-04): the mirror target is a standalone
        # keyed gretap *netdev* created here at deploy time, NOT an OVS GRE port.
        # The per-tap `tc` mirror installed by install-ovs-mirror points at it.
        # Naming + key are per-(bridge, sensor) (item B keying, carried over
        # unchanged): a bridge fans one tunnel out per distinct sensor, many
        # bridges converge on one sensor, and the distinct key stops unkeyed
        # tunnels colliding at the datapath. Each sensor terminates on a flow-based
        # gretap (`type gretap external`) that decaps any key into one merged
        # stream (docs/ARCHITECTURE.md §4, o2/tcpdump-sensor/README.md).
        dev = gre_port_for_bridge_sensor(bridge, sensor_id)
        key = gre_key_for_bridge_sensor(bridge, sensor_id)

        # Check-then-act (Model C): adopt an existing netdev rather than tearing
        # it down and recreating — a recreate would briefly drop a live capture
        # and orphan the tc filters that reference it. `ip -o link show <dev>`
        # exits non-zero when absent (remote_ip/key drift is picked up by a
        # `--fresh` redeploy). Note: an `ip link` netdev is NOT persistent across
        # a node reboot (unlike the old OVSDB GRE port); a node reboot is healed
        # by a resume-safe re-deploy, which re-runs this op.
        show = run_ssh(
            self.cfg,
            f"ip -o link show {shlex.quote(dev)} 2>/dev/null || true",
            timeout_s=15,
        )
        if dev not in show:
            add_cmd = (
                f"ip link add {shlex.quote(dev)} type gretap "
                f"remote {shlex.quote(sensor_ip)} key {key} {_GRETAP_PMTU_OPTS}"
            )
            run_ssh(self.cfg, add_cmd, timeout_s=op.timeout_s or 60)
        else:
            # Adopted netdev: assert the DF policy in place (`ip link set ...
            # type gretap` is a changelink -- iproute2 re-reads remote/key, the
            # ifindex the tc mirred filters point at is unchanged, idempotent).
            # This is what heals a gretap built before `_GRETAP_PMTU_OPTS`
            # existed on a plain tap re-apply. Fatal on failure: without it the
            # tunnel silently drops every full-size frame.
            run_ssh(
                self.cfg,
                f"ip link set {shlex.quote(dev)} type gretap {_GRETAP_PMTU_OPTS}",
                timeout_s=op.timeout_s or 60,
            )
        # Idempotently assert MTU + up, whether freshly created or adopted, so a
        # partially-built tunnel from a prior attempt heals on re-run.
        run_ssh(
            self.cfg,
            f"ip link set {shlex.quote(dev)} mtu {_GRETAP_MTU} up",
            timeout_s=op.timeout_s or 60,
        )
        # Replace the node-default qdisc with a deep FIFO (2026-08-13, dev45
        # max-capture-rate run). A new netdev inherits the node default, which
        # on PVE 9.x is `fq_codel` -- an AQM that DROPS to hold latency down.
        # That is right for a congested uplink and wrong for a mirror: the
        # copy is fire-and-forget, so a drop is simply lost evidence, and there
        # is no sender to back off. Measured: 9,349 frames lost in a 90s window
        # at ~2.38 Gbit/s with fq_codel, zero after this change.
        #
        # Asserted on EVERY run, not only on create, and deliberately so --
        # `attach-gre-tunnel` adopts a pre-existing netdev (above), so a gretap
        # built before this change would otherwise keep fq_codel forever. This
        # is also what removes the manual "re-assert after every tap deploy"
        # step the live run needed.
        #
        # `qlen` is an int from config, so it cannot inject into the command;
        # 0 is the operator opt-out (leave the node default alone).
        qlen = getattr(self.cfg, "mirror_gretap_txqueuelen", 0)
        if qlen:
            run_ssh(
                self.cfg,
                f"ip link set {shlex.quote(dev)} txqueuelen {int(qlen)} && "
                f"tc qdisc replace dev {shlex.quote(dev)} root "
                f"pfifo limit {int(qlen)}",
                timeout_s=op.timeout_s or 60,
            )
        # Segmentation offload OFF on the gretap, asserted every run for the same reason the
        # qdisc is (an adopted pre-existing netdev must heal too).
        #
        # A gretap is created with `mtu 1500` and `gso_max_size 65536`. The kernel honours the
        # latter and hands it super-frames the tunnel cannot encapsulate, so they are counted as
        # tx errors and dropped -- silently, since a mirror is fire-and-forget and there is no
        # sender to notice. `docs/CHANGELOG.md`'s max-capture-rate entry recorded a handful of
        # such drops and could not explain them ("most likely occasional oversized/GSO frames
        # ... Unconfirmed").
        #
        # MEASURED 2026-08-27 on a 123-VM twin at ~64 kpps with realistic per-flow sizes:
        # ~1.1M frames dropped across 26 gretaps, two of them losing 14% and 33% while carrying
        # only 1,698 and 195 pps -- proof the loss was frame SIZE, not rate. Disabling offload on
        # the tapped VM ports alone fixed 9 of 11; the gretap's own `gso_max_size` was the
        # dominant half. With both off: 2,448,400 frames in 30s, ZERO dropped.
        #
        # Failure here is not fatal: a kernel or device that rejects the request should not fail
        # an otherwise good tunnel, and the drops it prevents are a fidelity loss, not an outage.
        run_ssh(
            self.cfg,
            f"ethtool -K {shlex.quote(dev)} gso off tso off gro off || true",
            timeout_s=op.timeout_s or 60,
        )
        return {"gre_port": dev}

    def _install_ovs_mirror(
        self, op: OpInstance, inp: dict[str, Any], ctx: ExecutionContext
    ) -> dict[str, Any]:
        vmid = int(inp["vmid"])
        # Build the per-NIC (nic_index, gretap) mirror set from the resolved
        # tapped-interface list (dynamic taps, docs/design/06-taps-sensors.md):
        # a host tap selects all NICs, a leg tap one, a segment tap each attached
        # NIC — each into ITS net's gretap. `tapped_nics` carries the per-NIC
        # gre_port input key (`gre_port__<n>`), resolved into `inp` by the planner.
        nic_gretaps = self._resolve_nic_gretaps(inp)
        if not nic_gretaps:
            # EMPTY tapped_nics is a REVERT, not an error. On a NARROWING
            # re-apply, the tap playout composes an install-ovs-mirror[$host] op
            # with EMPTY desired content to actively CLEAR a host dropped from
            # the tapped set (o2control/taps.py::_compose_ops, "with EMPTY
            # desired content ... actively reverting it"; the docstring's
            # "re-asserts every affected host's install-ovs-mirror back to EMPTY
            # tapped_nics"). Raising here failed every dropped host and broke the
            # whole re-apply (e.g. corp-enterprise segment->boundary, 2026-07-26).
            # Assert the no-mirror desired state instead of erroring.
            return self._clear_ovs_mirror(op, vmid, ctx)

        # Deterministic hook script name; one per VM so we can replace cleanly
        # under Model C.
        snippet_name = f"axrange-mirror-{vmid}.sh"
        # Default Proxmox local-storage snippet path. Operator can override
        # snippets storage via the binding; v1 slice assumes `local`.
        remote_path = f"/var/lib/vz/snippets/{snippet_name}"

        # Render the hook (handles 1..N tapped NICs).
        script = _hook_script(ctx.plan_id, nic_gretaps)

        # Idempotent: scp overwrites, ensure exec bit, register on VM.
        # First make sure the snippets directory exists.
        run_ssh(
            self.cfg,
            "mkdir -p /var/lib/vz/snippets",
            timeout_s=10,
        )
        scp_to_remote(self.cfg, script, remote_path, timeout_s=20)
        run_ssh(self.cfg, f"chmod 0755 {shlex.quote(remote_path)}", timeout_s=10)

        # Register the hookscript on the VM. `qm set` is idempotent.
        register_cmd = (
            f"qm set {vmid} --hookscript local:snippets/{snippet_name}"
        )
        run_ssh(self.cfg, register_cmd, timeout_s=op.timeout_s or 60)

        # Model C check-then-act for the mirror itself. The hook fires on
        # post-start; if the VM is currently STOPPED, leave mirror creation to
        # the hook on next boot. If the VM is already RUNNING — typical on a
        # re-deploy where Model C adopts an existing VM and start-vm is a no-op —
        # the hook will never fire on its own, so assert each tc mirror here
        # in-band against the live tap iface (the same rules the hook installs).
        # Surfaced during the 2026-05-29 real-cluster bring-up: re-deploys
        # silently lost mirror coverage and pcaps stopped flowing.
        status_out = run_ssh(
            self.cfg,
            f"qm status {vmid} 2>/dev/null | awk '{{print $2}}'",
            timeout_s=15,
        ).strip()
        if status_out == "running":
            # Group (gretap, direction) pairs per NIC so a fan-out NIC
            # (multi-sensor) gets ALL its mirrors in one clsact reset — not
            # one-per-gretap (which would clobber).
            by_nic: dict[int, list[tuple[str, str]]] = {}
            for nic_index, gretap, direction in nic_gretaps:
                by_nic.setdefault(nic_index, []).append((gretap, direction))
            for nic_index, gretaps in by_nic.items():
                run_ssh(
                    self.cfg,
                    _tc_mirror_cmd(f"tap{vmid}i{nic_index}", gretaps),
                    timeout_s=op.timeout_s or 60,
                )
            # Fix round S1 (`docs/WORK_ORDER_tap_scopes.md`): a NIC narrowed
            # OUT of the desired set (e.g. a host moves from nic1 to nic0,
            # or a tap-plan re-shard drops one of a host's several tapped
            # NICs -- the new `port`/`perimeter` vocabulary makes same-host
            # NIC re-shards first-class, not a corner case) otherwise keeps
            # its STALE clsact/mirror alive until the VM's next reboot
            # re-runs the (now-narrower) post-start hook -- the sibling of
            # LES-112/LES-118's "surviving state still references something
            # no longer wanted," just at the NIC level instead of the
            # gretap-device level. Enumerate every LIVE `tap<vmid>i*` iface
            # (the SAME technique `_clear_ovs_mirror`'s empty-set path
            # already uses below) and reset clsact on any NOT in `by_nic` --
            # idempotent (`|| true` on a NIC that has no clsact).
            live_ifaces = run_ssh(
                self.cfg,
                f"ip -o link show 2>/dev/null | grep -oE 'tap{vmid}i[0-9]+' | sort -u",
                timeout_s=15,
            ).split()
            desired_ifaces = {f"tap{vmid}i{nic_index}" for nic_index in by_nic}
            cleared_ifaces: list[str] = []
            for ifc in live_ifaces:
                if ifc in desired_ifaces:
                    continue
                run_ssh(
                    self.cfg,
                    f"tc qdisc del dev {shlex.quote(ifc)} clsact 2>/dev/null || true",
                    timeout_s=op.timeout_s or 30,
                )
                cleared_ifaces.append(ifc)
            if cleared_ifaces:
                # Fix round G6 (`docs/WORK_ORDER_tap_scopes.md`): a destructive
                # sweep against LIVE capture must be legible after the fact
                # (LES-102) -- `_clear_ovs_mirror`'s empty-set path already
                # signals `mirror_cleared`; this narrowing sweep previously
                # returned a flat `{"hook_installed": True}` with no record of
                # which NIC(s) it just reset clsact on.
                return {"hook_installed": True, "cleared_ifaces": cleared_ifaces}

        return {"hook_installed": True}

    def _clear_ovs_mirror(
        self, op: OpInstance, vmid: int, ctx: ExecutionContext
    ) -> dict[str, Any]:
        """Revert a VM to NO mirror (empty desired `tapped_nics`) -- the drop
        side of a narrowing tap re-apply. Writes+registers an EMPTY (no-op) hook
        so a reboot reinstates nothing, and clears any live tc mirror on the VM's
        tap ifaces now. Idempotent: safe on a VM that already has no mirror."""
        snippet_name = f"axrange-mirror-{vmid}.sh"
        remote_path = f"/var/lib/vz/snippets/{snippet_name}"
        # An empty pairs set renders a valid hook whose post-start loop is a
        # no-op (see `_HOOK_SCRIPT_TEMPLATE` -- `nics` is empty, the loop body
        # never runs), so a reboot installs no mirror for this VM.
        script = _hook_script(ctx.plan_id, [])
        run_ssh(self.cfg, "mkdir -p /var/lib/vz/snippets", timeout_s=10)
        scp_to_remote(self.cfg, script, remote_path, timeout_s=20)
        run_ssh(self.cfg, f"chmod 0755 {shlex.quote(remote_path)}", timeout_s=10)
        run_ssh(
            self.cfg,
            f"qm set {vmid} --hookscript local:snippets/{snippet_name}",
            timeout_s=op.timeout_s or 60,
        )
        # Clear the live tc mirror (VM running only -- a stopped VM has no tap
        # ifaces; the empty hook covers its next boot). Reset clsact on every
        # tap iface this VM currently owns; idempotent (`|| true` on a NIC that
        # has no clsact).
        status_out = run_ssh(
            self.cfg,
            f"qm status {vmid} 2>/dev/null | awk '{{print $2}}'",
            timeout_s=15,
        ).strip()
        if status_out == "running":
            ifaces = run_ssh(
                self.cfg,
                f"ip -o link show 2>/dev/null | grep -oE 'tap{vmid}i[0-9]+' | sort -u",
                timeout_s=15,
            ).split()
            for ifc in ifaces:
                run_ssh(
                    self.cfg,
                    f"tc qdisc del dev {shlex.quote(ifc)} clsact 2>/dev/null || true",
                    timeout_s=op.timeout_s or 30,
                )
        return {"hook_installed": True, "mirror_cleared": True}

    @staticmethod
    def _resolve_nic_gretaps(inp: dict[str, Any]) -> list[tuple[int, str, str]]:
        """The ordered (nic_index, gretap, direction) mirror set for this VM.

        Reads the planner's `tapped_nics` metadata (each entry names a per-NIC
        gre_port input key, resolved into `inp` from attach-gre-tunnel). A named
        per-NIC key absent from `inp` is a planner wiring bug and raises (fail
        loud) rather than silently mirroring that NIC to the wrong tunnel. Falls
        back to the legacy single-NIC form (nic 0 -> `gre_port`) only for
        plans/tests that don't carry `tapped_nics`, preserving the pre-dynamic-tap
        path.

        `direction` (`docs/WORK_ORDER_tap_scopes.md` M5) defaults to `"both"`
        when an entry carries none -- the SAFE default (never a silent
        narrowing): a `tapped_nics` entry from before this field existed (a
        resumed deploy's already-persisted op input, LES-096) must keep
        mirroring both directions exactly as it always did, not drop to
        rx-only because the field happens to be absent."""
        tapped = inp.get("tapped_nics")
        out: list[tuple[int, str, str]] = []
        if isinstance(tapped, list) and tapped:
            for entry in tapped:
                if not isinstance(entry, dict):
                    continue
                nic = int(entry.get("nic_index", 0))
                key = entry.get("gre_port_input") or "gre_port"
                gretap = inp.get(key)
                if not gretap:
                    raise AdapterError(
                        f"install-ovs-mirror: tapped NIC {nic} names gretap input "
                        f"{key!r}, but it is absent from the resolved inputs -- "
                        f"the planner did not wire its gretap, which would "
                        f"silently mirror this NIC to the wrong tunnel. Resolved "
                        f"keys: {sorted(inp)}",
                        error_class="mirror-wiring-missing",
                    )
                direction = entry.get("direction") or "both"
                out.append((nic, str(gretap), str(direction)))
            return out
        # Legacy single-NIC path -- always both directions (the pre-M5 shape).
        gretap = inp.get("gre_port")
        if gretap:
            out.append((0, str(gretap), "both"))
        return out
