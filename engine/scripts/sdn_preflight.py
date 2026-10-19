#!/usr/bin/env python3
"""Print the exact Proxmox REST calls the SDN-native OP-NET ops will make for a
scenario — WITHOUT touching the cluster.

Item A (SDN-native networking, `docs/ADAPTER.md` §3/§8) replaced the direct-
bridge bypass with real `/cluster/sdn` definitions. Before a live `axrange
deploy` on `pxtwin1`, run this to eyeball the precise zone/vnet/bridge payloads
and their order — the "resolve by building" REST details the CHANGELOG flags as
live-verify-pending.

It drives the *real* `RestBackend` OP-NET methods (the source of truth for the
calls) against a recording client that records instead of sending, threading
each op's outputs into the next exactly as the executor would. SSH (the apply-
time tap re-assert) is stubbed.

Usage:
  python scripts/sdn_preflight.py tests/fixtures/ir/valid_slice/scenario.yaml
  python scripts/sdn_preflight.py <scenario.yaml> --node pxtwin1 --sensor-ip 10.255.255.10
  python scripts/sdn_preflight.py <scenario.yaml> --images <images-root>

Shows, per OP-NET op: GET (check-then-act probe), then the POST/PUT it issues.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from axrange.ir.parser import parse_ir  # noqa: E402
from axrange.manifest.loader import load_registry  # noqa: E402
from axrange.planner import build_plan  # noqa: E402
from axrange.planner.cluster import ClusterContext  # noqa: E402
from axrange.planner.executor import AdapterError, ExecutionContext  # noqa: E402

_OP_NET = ("create-ovs-bridge", "create-sdn-zone", "create-vnet", "apply-sdn-config")


class _RecordingClient:
    """Records requests; GET reports 'absent' so check-then-act proceeds to the
    create call (the path a fresh deploy takes)."""

    def __init__(self):
        self.calls: list[tuple[str, str, dict]] = []

    def request(self, method, path, *, params=None, body=None):
        self.calls.append((method, path, dict(body or {})))
        if method == "GET":
            raise AdapterError("preflight: treat as absent",
                               error_class="preflight")
        return {}

    def wait_task(self, node, upid, *, timeout_s=60):
        return None


def _resolve(op, outputs):
    """Merge static inputs with values threaded from prior ops' outputs."""
    merged = dict(op.inputs_resolved)
    for name, (producer_id, out_name) in op.inputs_from_outputs.items():
        merged[name] = outputs.get(producer_id, {}).get(out_name)
    return merged


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scenario")
    ap.add_argument("--images", default=None,
                    help="images root (default: scenario's directory)")
    ap.add_argument("--node", default="pxnode", help="target Proxmox node name")
    ap.add_argument("--sensor-ip", default="10.255.255.10",
                    help="primary sensor transport-vnet IP")
    args = ap.parse_args()

    scenario = Path(args.scenario)
    images_root = Path(args.images) if args.images else scenario.parent
    manifests = load_registry(root=images_root)
    parsed = parse_ir(path=str(scenario))
    ctx_cluster = ClusterContext.single_node(args.node, sensor_transport_ip=args.sensor_ip)
    plan = build_plan(parsed.ir, manifests, ctx_cluster, raw_ir=parsed.raw)

    # Stub the apply-time SSH tap re-assert.
    import axrange.adapter_proxmox.ssh as ssh_mod
    ssh_mod.run_ssh = lambda cfg, cmd, **kw: ""  # type: ignore[assignment]

    from axrange.adapter_proxmox.config import AdapterConfig
    from axrange.adapter_proxmox.rest import RestBackend

    client = _RecordingClient()
    backend = RestBackend(AdapterConfig(), client=client)

    zone_type = "vlan" if any(
        getattr(n, "vlan", None) is not None
        for n in parsed.ir.layers.infra.networks
    ) else "simple"
    print(f"# SDN preflight for {scenario}")
    print(f"# node={args.node}  zone_type={zone_type}  sensor_ip={args.sensor_ip}")
    print("# Calls below are recorded, NOT sent. GET = check-then-act probe.\n")

    exec_ctx = ExecutionContext(
        plan_id=plan.plan_id, deploy_id="preflight",
        cluster=ctx_cluster, placement=dict(plan.placement),
    )
    outputs: dict[str, dict] = {}
    method_map = {
        "create-ovs-bridge": backend.op_create_ovs_bridge,
        "create-sdn-zone": backend.op_create_sdn_zone,
        "create-vnet": backend.op_create_vnet,
        "apply-sdn-config": backend.op_apply_sdn_config,
    }

    for op_id in plan.topological_order():
        op = plan.ops.get(op_id)
        if op is None or op.op_name not in _OP_NET:
            continue
        before = len(client.calls)
        resolved = _resolve(op, outputs)
        try:
            outputs[op_id] = method_map[op.op_name](op, resolved, exec_ctx) or {}
        except AdapterError as e:
            print(f"  ! {op.render()}: {e}")
            continue
        print(f"  {op.render()}")
        for method, path, body in client.calls[before:]:
            tag = "  (probe)" if method == "GET" else ""
            line = f"      {method:4s} {path}{tag}"
            if body:
                line += "\n           body: " + ", ".join(
                    f"{k}={v}" for k, v in body.items())
            print(line)
        print()
    print("# Done. Review the zone type, the bridge the VLAN zone references, "
          "and the vnet tags.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
