"""Native switches on Proxmox (`docs/design/native-switches.md` §4, §5;
`docs/contracts/ADAPTER.md` §3, §8.4). One adapter-owned, VLAN-aware OVS
bridge per native switch, created with `ovs-vsctl` over SSH and living only
in OVSDB: not an SDN object, not in `/etc/network/interfaces`, so creating it
needs no `ifreload` and disturbs no running VM on the node.

Every command here is built from values this module or `naming.py` produced
(hex names, integer VLAN ids, `tap<vmid>i<n>`) -- never from an author's free
text (a link's port name is a label in the model, not an OVS name), so none
of them can carry shell metacharacters. Every op is check-then-act (ADAPTER
§4): `--may-exist` creates, and the attributes are re-asserted on every run,
so an adopted bridge or port that drifted is healed in place.
"""
from __future__ import annotations

import re
from typing import Any, Callable, Iterable, Optional

from axrange.adapter_proxmox.naming import (
    is_te_patch_port,
    is_te_switch_bridge,
    patch_port_for,
    switch_bridge_for,
)
from axrange.planner.executor import AdapterError

#: OpenFlow port-config actions per link state (`ovs-ofctl mod-port`), in the
#: order they are applied. `up` restores every flag, so a state change in
#: either direction converges.
PORT_STATE_ACTIONS: dict[str, tuple[str, ...]] = {
    "up": ("up", "receive", "forward", "flood"),
    "down": ("down",),
    "blocked": ("up", "no-receive", "no-forward", "no-flood"),
}


def vm_ovs_port(vmid: int, nic_index: int) -> str:
    """The OVS port qemu-server plugs a VM NIC in as (`firewall=0`, so the
    tap itself, not a `fwln…` veth: design §8)."""
    return f"tap{int(vmid)}i{int(nic_index)}"


def _ints(values: Iterable[Any]) -> list[int]:
    return [int(v) for v in values]


def create_bridge_cmd(bridge: str, *, plan_id_tag: str, switch_id_tag: str) -> str:
    _check_bridge(bridge)
    return (
        f"ovs-vsctl --may-exist add-br {bridge}"
        f" -- set bridge {bridge} fail_mode=standalone mcast_snooping_enable=false"
        f" external_ids:axrange-tier=te external_ids:axrange-plan={plan_id_tag}"
        f" external_ids:axrange-switch={switch_id_tag}"
        f" && ip link set {bridge} up"
    )


def vlan_port_args(port: str, *, mode: str, vlan_tags: list[int], native_tag: Optional[int]) -> str:
    """`ovs-vsctl` arguments asserting a port's VLAN role, clearing whatever
    the other role left behind."""
    if mode == "access":
        if len(vlan_tags) != 1:
            raise AdapterError(f"access port {port}: one VLAN expected, got {vlan_tags}",
                               error_class="adapter-input")
        return (f" -- set port {port} vlan_mode=access tag={int(vlan_tags[0])}"
                f" -- clear port {port} trunks")
    trunks = ",".join(str(t) for t in sorted(set(_ints(vlan_tags) + ([native_tag] if native_tag else []))))
    if native_tag:
        return f" -- set port {port} vlan_mode=native-untagged tag={int(native_tag)} trunks={trunks}"
    return (f" -- set port {port} vlan_mode=trunk trunks={trunks}"
            f" -- clear port {port} tag")


def patch_pair_cmd(bridge_a: str, port_a: str, bridge_b: str, port_b: str, *,
                   mode: str, vlan_tags: list[int], native_tag: Optional[int]) -> str:
    for b in (bridge_a, bridge_b):
        _check_bridge(b)
    for p in (port_a, port_b):
        if not is_te_patch_port(p):
            raise AdapterError(f"refusing non-tepp patch port {p!r}", error_class="adapter-input")
    vlan_a = vlan_port_args(port_a, mode=mode, vlan_tags=vlan_tags, native_tag=native_tag)
    vlan_b = vlan_port_args(port_b, mode=mode, vlan_tags=vlan_tags, native_tag=native_tag)
    return (
        f"ovs-vsctl --may-exist add-port {bridge_a} {port_a}"
        f" -- set interface {port_a} type=patch options:peer={port_b}"
        f" -- --may-exist add-port {bridge_b} {port_b}"
        f" -- set interface {port_b} type=patch options:peer={port_a}"
        f"{vlan_a}{vlan_b}"
    )


def port_state_cmd(bridge: str, port: str, state: str) -> str:
    _check_bridge(bridge)
    actions = PORT_STATE_ACTIONS.get(state)
    if actions is None:
        raise AdapterError(f"unknown port state {state!r}", error_class="adapter-input")
    return " && ".join(f"ovs-ofctl mod-port {bridge} {port} {a}" for a in actions)


def delete_bridge_cmd(bridge: str) -> str:
    """Remove every mirror on the bridge, then the bridge (its patch ports and
    any leftover VM tap ports go with it)."""
    _check_bridge(bridge)
    return (f"ovs-vsctl --if-exists clear bridge {bridge} mirrors"
            f" -- --if-exists del-br {bridge}")


def _check_bridge(bridge: str) -> None:
    if not is_te_switch_bridge(bridge):
        raise AdapterError(f"refusing non-tesw switch bridge {bridge!r}",
                           error_class="adapter-input")


class SwitchOps:
    """The SSH-side native-switch ops. `run` is `run_ssh` bound to the
    adapter config (injected so the tests mock one callable)."""

    def __init__(self, run: Callable[..., str]) -> None:
        self.run = run

    def bridge_exists(self, bridge: str) -> bool:
        out = self.run(f"ovs-vsctl list-br", timeout_s=15)
        return bridge in out.split()

    def create_switch(self, op: Any, inp: dict[str, Any], ctx: Any) -> dict[str, Any]:
        switch_id = inp.get("switch_id") or op.scope.get("switch")
        if not switch_id:
            raise AdapterError("create-switch: switch_id missing", error_class="adapter-input")
        if (inp.get("stp") or "off") != "off":
            raise AdapterError("create-switch: RSTP is phase 3", error_class="adapter-unsupported")
        bridge = switch_bridge_for(ctx.plan_id, str(switch_id))
        # Check-then-act: `--may-exist` adopts a bridge a prior attempt made,
        # and the `set` re-asserts its attributes either way.
        self.run(create_bridge_cmd(bridge, plan_id_tag=_tag(ctx.plan_id),
                                   switch_id_tag=_tag(str(switch_id))),
                 timeout_s=op.timeout_s or 60)
        return {"bridge_name": bridge}

    def link_switches(self, op: Any, inp: dict[str, Any], ctx: Any) -> dict[str, Any]:
        key = inp.get("link_key") or op.scope.get("link")
        bridge_a, bridge_b = inp.get("bridge_a"), inp.get("bridge_b")
        if not (key and bridge_a and bridge_b):
            raise AdapterError("link-switches: link_key / bridge_a / bridge_b missing",
                               error_class="adapter-input")
        port_a = patch_port_for(ctx.plan_id, str(key), "a")
        port_b = patch_port_for(ctx.plan_id, str(key), "b")
        native = inp.get("native_tag")
        self.run(patch_pair_cmd(bridge_a, port_a, bridge_b, port_b,
                                mode=str(inp.get("mode") or "trunk"),
                                vlan_tags=_ints(inp.get("vlan_tags") or []),
                                native_tag=int(native) if native else None),
                 timeout_s=op.timeout_s or 60)
        state = str(inp.get("state") or "up")
        for bridge, port in ((bridge_a, port_a), (bridge_b, port_b)):
            self.run(port_state_cmd(bridge, port, state), timeout_s=op.timeout_s or 60)
        return {"port_a": port_a, "port_b": port_b}

    def set_port_state(self, op: Any, inp: dict[str, Any], ctx: Any) -> dict[str, Any]:
        bridge = inp.get("bridge_name")
        state = str(inp.get("state") or "")
        if inp.get("vmid") is None or inp.get("nic_index") is None or not bridge:
            raise AdapterError("set-port-state: vmid / nic_index / bridge_name missing",
                               error_class="adapter-input")
        port = vm_ovs_port(int(inp["vmid"]), int(inp["nic_index"]))
        self.run(port_state_cmd(bridge, port, state), timeout_s=op.timeout_s or 60)
        return {"state_applied": state, "ovs_port": port}

    def delete_switch(self, bridge: str) -> str:
        if not self.bridge_exists(bridge):
            return "absent"
        self.run(delete_bridge_cmd(bridge), timeout_s=60)
        if self.bridge_exists(bridge):
            raise AdapterError(f"switch bridge {bridge} survived del-br",
                               error_class="teardown-incomplete")
        return "removed"


def _tag(value: str) -> str:
    """An OVSDB external_ids value: a label for an operator reading
    `ovs-vsctl list bridge`, never parsed back, so anything outside
    `[A-Za-z0-9._-]` becomes `_` (no quoting to get wrong)."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", value)[:64] or "_"
