"""Compose a generator plan into tfc-tier ops (`SPEC.md`; T3 §1-2).

Hand-composed like the tfc and tap tiers: the te deploy's networks already
exist, so each generator VM reuses the catalog's host ops with literal inputs
read from the te deploy's recorded outputs (a switch segment's bridge and VLAN
tag, an SDN segment's vnet id), then runs the five generator ops over the
guest agent. Ops and edges are deterministic in the plan id and the generator
plan.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from axrange.ops.registry import get_op
from axrange.planner.dag import OpInstance, Plan, _op_id

GENERATOR_INFRA_OPS = ("clone-vm", "configure-vm-hardware", "configure-cloud-init",
                       "attach-vm-to-switch-port", "attach-vm-to-vnet", "start-vm", "wait-guest-agent")
GENERATOR_CONFIG_OPS = ("provision-generator", "load-traffic-profile", "start-traffic")


class ComposeError(ValueError):
    """The te deploy does not record what a generator needs (a leg's network)."""


def _timeout(spec: Optional[str]) -> Optional[int]:
    return int(spec[:-1]) if spec and spec.endswith("s") and spec[:-1].isdigit() else None


def _op(plan_id: str, name: str, scope: dict[str, str], inputs: dict[str, Any],
        from_outputs: Optional[dict[str, list[str]]] = None) -> OpInstance:
    d = get_op(name)
    if d is None:  # pragma: no cover -- catalog names are fixed
        raise ComposeError(f"unknown op {name}")
    return OpInstance(
        op_id=_op_id(plan_id, name, scope), op_name=name, op_catalog_id=d.id, kind=d.kind,
        scope=dict(scope), inputs_resolved=inputs, inputs_from_outputs=dict(from_outputs or {}),
        cleanup_strategy=d.cleanup_strategy, idempotency_class=d.idempotency_class,
        resource_categories=d.resource_categories, timeout_s=_timeout(d.timeout),
    )


def te_segment_attach(te_ops: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """`{segment: attach spec}` from the te deploy's recorded op rows (dicts
    with `op_name`, `scope`, `inputs`, `outputs`): a switch segment ->
    `{"op": "attach-vm-to-switch-port", "bridge_name", "vlan_tag", "switch_id"}`,
    an SDN segment -> `{"op": "attach-vm-to-vnet", "vnet_id", "vlan"}`."""
    bridges = {r["scope"].get("switch"): (r.get("outputs") or {}).get("bridge_name")
               for r in te_ops if r["op_name"] == "create-switch"}
    vnets = {r["scope"].get("net"): (r.get("outputs") or {}).get("vnet_id")
             for r in te_ops if r["op_name"] == "create-vnet"}
    out: dict[str, dict[str, Any]] = {}
    for r in te_ops:
        inp = r.get("inputs") or {}
        seg = inp.get("net_id")
        if not seg or seg in out:
            continue
        if r["op_name"] == "attach-vm-to-switch-port" and inp.get("mode", "access") == "access":
            br = bridges.get(inp.get("switch_id"))
            if br:
                out[seg] = {"op": "attach-vm-to-switch-port", "bridge_name": br,
                            "vlan_tag": inp.get("vlan_tag"), "switch_id": inp.get("switch_id")}
        elif r["op_name"] == "attach-vm-to-vnet":
            vn = vnets.get(seg)
            if vn:
                out[seg] = {"op": "attach-vm-to-vnet", "vnet_id": vn, "vlan": inp.get("vlan")}
    for seg in vnets:
        if seg and seg not in out and vnets[seg]:
            out[seg] = {"op": "attach-vm-to-vnet", "vnet_id": vnets[seg], "vlan": None}
    return out


def compose_generator_ops(
    plan: Plan, tg_plan: Any, *, attach: dict[str, dict[str, Any]], binding: dict[str, Any],
    target_node: str, mults: Optional[dict[str, float]] = None,
) -> list[str]:
    """Add every generator's ops and edges to `plan` (not frozen). `binding`:
    `{"image_id", "template_vmid", "clone_mode", "machine", "bios", "ci_user"}`.
    Returns the added op ids."""
    mults = mults or {}
    pid = plan.plan_id
    added: list[str] = []

    def add(op: OpInstance, after: list[str]) -> str:
        plan.add_op(op)
        for k, (src, _) in op.inputs_from_outputs.items():
            plan.add_edge(src, op.op_id, kind="dataflow")
        for a in after:
            plan.add_edge(a, op.op_id, kind="ordering")
        added.append(op.op_id)
        return op.op_id

    for g in tg_plan.generators:
        h = g.host_id
        for leg in g.legs:
            if leg.segment not in attach:
                raise ComposeError(f"{h}: the te deploy records no network for segment {leg.segment!r}")
        clone = add(_op(pid, "clone-vm", {"host": h}, {
            "node_id": h, "node_kind": "host", "node_role": "traffic-generator", "image_id": binding["image_id"],
            "template_vmid": binding["template_vmid"], "clone_mode": binding.get("clone_mode") or "linked",
            "target_node": target_node,
        }), [])
        vmid = {"vmid": [clone, "vmid"]}
        hw = add(_op(pid, "configure-vm-hardware", {"host": h}, {
            "agent": True, "bios": binding.get("bios") or "seabios", "machine": binding.get("machine") or "pc",
            "cpu": g.cores, "ram": f"{g.memory_mib}M", "disk": None,
        }, vmid), [])
        ci = add(_op(pid, "configure-cloud-init", {"host": h}, {
            "ci_user": binding.get("ci_user") or "debian", "hostname": h, "ip_config": {}, "node_id": h,
            "os_family": "linux", "password": None, "router_os": None, "sysprep_required": False,
        }, vmid), [hw])
        attaches = []
        for leg in g.legs:
            a = attach[leg.segment]
            scope = {"host": h, "net": leg.segment}
            if a["op"] == "attach-vm-to-switch-port":
                inputs = {"ip": leg.ip, "link_down": False, "mode": "access", "net_id": leg.segment,
                          "nic_index": leg.index, "port": f"{h}.{leg.index}", "switch_id": a["switch_id"],
                          "target_node": target_node, "vlan_tag": a["vlan_tag"], "bridge_name": a["bridge_name"]}
            else:
                inputs = {"ip": leg.ip, "net_id": leg.segment, "nic_index": leg.index, "target_node": target_node,
                          "vlan": a.get("vlan"), "vnet_id": a["vnet_id"]}
            attaches.append(add(_op(pid, a["op"], scope, inputs, vmid), [ci]))
        start = add(_op(pid, "start-vm", {"host": h}, {}, vmid), attaches)
        ga = add(_op(pid, "wait-guest-agent", {"host": h}, {}, vmid), [start])
        files = []
        for ln in g.document["lines"]:
            for t in ln["templates"]:
                if "pcap" in t:
                    files.append({"app": t["app"], **t["pcap"]})
        gscope = {"generator": h}
        prov = add(_op(pid, "provision-generator", gscope, {
            "generator": h, "legs": {str(leg.index): leg.segment for leg in g.legs},
            "document": g.document, "document_sha256": g.document_sha256(), "files": files,
        }, vmid), [ga])
        load = add(_op(pid, "load-traffic-profile", gscope, {"generator": h, "lines": list(g.lines),
                                                           "document_sha256": g.document_sha256()}, vmid), [prov])
        add(_op(pid, "start-traffic", gscope, {
            "generator": h, "lines": list(g.lines),
            "mults": {lid: float(mults.get(lid, 1.0)) for lid in g.lines},
            "document_sha256": g.document_sha256(),
        }, vmid), [load])
    return added


def describe(tg_plan: Any) -> str:
    return json.dumps([{"generator": g.host_id, "legs": [(l.role, l.segment, l.ip) for l in g.legs],
                        "lines": list(g.lines)} for g in tg_plan.generators])
