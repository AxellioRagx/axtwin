"""Conformance — capability probes + the one-host deploy smoke-test.

docs/IMAGE_BUILD.md §4: a build is correct iff the built image satisfies its
manifest. Two layers, **both required**:

  §4.1 Capability probes (minimal): one probe per `provides:` keyword, run
       inside the booted build VM via the guest agent before seal.
  §4.2 axrange smoke-test: after templatization, deploy a throwaway one-host
       (untapped) scenario against the freshly-built template **through the
       real planner + adapter path** and confirm clone/boot/agent/cloud-init/
       teardown. Catches integration regressions the probes miss.

A failure in either layer fails the build (docs/IMAGE_BUILD.md §4.3).
"""
from __future__ import annotations

import secrets
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from axrange.imagebuild.bake import probe_command
from axrange.imagebuild.bake_windows import windows_probe_command
from axrange.imagebuild.errors import ImageBuildError
from axrange.imagebuild.proxmox_build import BuildProxmox


def _noop(_msg: str) -> None:  # pragma: no cover - trivial
    pass


# ---------------------------------------------------------------------------
# §4.1 Capability probes
# ---------------------------------------------------------------------------


@dataclass
class ProbeResult:
    capability: str
    command: str | None
    exit_code: int | None
    ok: bool
    detail: str = ""


@dataclass
class ProbeReport:
    results: list[ProbeResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(r.ok for r in self.results)

    @property
    def failures(self) -> list[ProbeResult]:
        return [r for r in self.results if not r.ok]


def run_probes(
    bp: BuildProxmox,
    vmid: int,
    provides: list[str],
    overrides: dict[str, str] | None = None,
    *,
    os_family: str = "linux",
    log: Callable[[str], None] = _noop,
) -> ProbeReport:
    """Run one probe per `provides:` keyword inside the booted build VM.

    The probe command + the in-guest shell are OS-family-specific: Linux probes
    resolve from `bake._DEFAULT_PROBES` and run via `sh -c`; Windows probes
    resolve from `bake_windows._WINDOWS_PROBES` and run via `powershell -Command`
    (docs/SLICE_WINDOWS_AD.md §4). A keyword with neither a built-in default
    probe nor a binding override is a conformance gap (the claim cannot be
    verified) → recorded as a failure.
    """
    overrides = overrides or {}
    is_windows = os_family == "windows"
    report = ProbeReport()
    for cap in provides:
        cmd = (
            windows_probe_command(cap, overrides)
            if is_windows
            else probe_command(cap, overrides)
        )
        if cmd is None:
            report.results.append(
                ProbeResult(
                    capability=cap,
                    command=None,
                    exit_code=None,
                    ok=False,
                    detail="no probe defined (add one to the binding's build.probes)",
                )
            )
            log(f"probe {cap}: NO PROBE DEFINED -> fail")
            continue
        argv = (
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd]
            if is_windows
            else ["sh", "-c", cmd]
        )
        res = bp.guest_exec(vmid, argv, timeout_s=30)
        ok = res.exit_code == 0
        report.results.append(
            ProbeResult(
                capability=cap,
                command=cmd,
                exit_code=res.exit_code,
                ok=ok,
                detail="" if ok else (res.stderr or res.stdout).strip()[:160],
            )
        )
        log(f"probe {cap}: exit={res.exit_code} {'ok' if ok else 'FAIL'}")
    return report


# ---------------------------------------------------------------------------
# §4.2 Smoke-test
# ---------------------------------------------------------------------------


@dataclass
class SmokeReport:
    ok: bool
    deploy_id: str
    ops_ok: int = 0
    ops_total: int = 0
    detail: str = ""


def build_smoke_scenario_yaml(
    image: str, version: str, os_family: str = "linux", *, salt: int | str | None = None
) -> str:
    """A throwaway one-host, single-network scenario referencing the
    just-built template. No mirror ops emit at all any more, for ANY te
    plan (`docs/WORK_ORDER_tap_eligibility.md`, Authority: user,
    2026-07-28) → no sensor needed (docs/IMAGE_BUILD.md §4.2).

    `salt` (typically the build VMID) is appended to the scenario id so two
    concurrent builds of the SAME image don't collide on one deterministic
    smoke-scenario id (finding #7, code-review 2026-07-07) — `plan_id` and
    every SDN/VM name the smoke deploy creates derive from the scenario id, so
    a collision means the second build's smoke-test clobbers/adopts the
    first's live objects mid-deploy. `salt=None` (the default) reproduces the
    original unsalted id, kept for callers (and tests) that don't need
    uniqueness."""
    ref = f"{image}@{version}" if version else image
    sanitized = image.replace("_", "-")[:24]
    scenario_id = f"imgsmoke-{sanitized}"
    if salt is not None:
        scenario_id = f"{scenario_id}-{salt}"
    return (
        "scenario_schema_version: 1\n"
        "\n"
        "scenario:\n"
        f"  id: {scenario_id}\n"
        "  version: 1\n"
        "  description: |\n"
        "    Throwaway one-host smoke-test for image-build conformance\n"
        f"    (docs/IMAGE_BUILD.md §4.2). Built template: {ref}.\n"
        "\n"
        "layers:\n"
        "  infra:\n"
        "    networks:\n"
        "      - id: smoke-net\n"
        "        cidr: 10.123.0.0/24\n"
        "        vlan: 4000\n"
        "        gateway: 10.123.0.1\n"
        "    nodes:\n"
        "      - id: smoke-h\n"
        "        kind: host\n"
        "        nets:\n"
        "          - { net: smoke-net, ip: 10.123.0.10 }\n"
        f"        image: {ref}\n"
        f"        requires: [{os_family}]\n"
        "        resources: { cpu: 1, ram: 1Gi, disk: 10Gi }\n"
        "  apps: []\n"
        "  activity: []\n"
        "\n"
        "bindings: []\n"
    )


def smoke_test(
    build_cfg,
    images_root: str | Path,
    image: str,
    version: str,
    *,
    bp: BuildProxmox,
    os_family: str = "linux",
    log: Callable[[str], None] = _noop,
    teardown: bool = True,
    build_vmid: int | None = None,
) -> SmokeReport:
    """Deploy the throwaway scenario through the real planner+adapter, confirm
    it completes, then tear the clone + throwaway bridge down (best-effort).

    `build_vmid` (the just-templatized VM's id, passed by `builder.build_image`)
    salts the scenario id so two concurrent builds of the same image get
    distinct smoke deploys (finding #7, code-review 2026-07-07) — see
    `build_smoke_scenario_yaml`.

    Imports the planner/adapter inline (like cli/main.py) so a build that skips
    the smoke-test pays none of that import cost."""
    from axrange.adapter_proxmox import ProxmoxAdapter
    from axrange.ir.parser import parse_ir
    from axrange.manifest.loader import load_registry
    from axrange.planner import Executor, StateStore, build_plan
    from axrange.planner.cluster import ClusterContext
    from axrange.validator.engine import validate_full

    tmpdir = Path(tempfile.mkdtemp(prefix="axr-imgsmoke-"))
    scenario_path = tmpdir / "scenario.yaml"
    scenario_path.write_text(
        build_smoke_scenario_yaml(image, version, os_family, salt=build_vmid)
    )
    db_path = str(tmpdir / "smoke-state.db")

    manifests = load_registry(root=images_root)

    result = validate_full(str(scenario_path), manifests=manifests)
    if result.exit_code != 0:
        return SmokeReport(
            ok=False,
            deploy_id="",
            detail=f"smoke scenario failed validation ({len(result.errors)} error(s))",
        )

    # The clone must land on the node that now holds the freshly-built template
    # (single-node lab assumption: build node == deploy node). The smoke deploy
    # is untapped, and a te is sensor-agnostic anyway (2026-07-13,
    # docs/WORK_ORDER_sensor_agnostic_te.md) — no sensor identity to thread.
    ctx = ClusterContext.single_node(build_cfg.node)
    parse_result = parse_ir(path=str(scenario_path))
    plan = build_plan(parse_result.ir, manifests, ctx, raw_ir=parse_result.raw)

    store = StateStore(db_path)
    deploy_id = f"imgsmoke-{secrets.token_hex(3)}"
    store.create_deploy(
        deploy_id=deploy_id,
        scenario_id=plan.scenario_id,
        scenario_version=plan.scenario_version,
        scenario_schema_version=1,
        plan_id=plan.plan_id,
        ir_hash=plan.ir_hash,
        placement=plan.placement,
    )
    adapter = ProxmoxAdapter(build_cfg.deploy)
    executor = Executor(plan, store, adapter, ctx, deploy_id=deploy_id)
    executor.persist_plan()
    try:
        run_result = executor.run()
    finally:
        store.close()

    ops_total = len(run_result.per_op)
    ops_ok = sum(
        1 for o in run_result.per_op.values() if o.final_status.value == "completed"
    )

    if teardown:
        _smoke_teardown(bp, plan, "smoke-net", log)

    ok = run_result.succeeded
    return SmokeReport(
        ok=ok,
        deploy_id=deploy_id,
        ops_ok=ops_ok,
        ops_total=ops_total,
        detail="" if ok else f"deploy {run_result.final_status.value}",
    )


def _smoke_teardown(bp, plan, net_id, log) -> None:
    """Destroy the cloned VM(s) + the throwaway SDN objects. Best-effort: a
    teardown failure never fails the build, but it is logged so leaks are
    visible.

    SDN-native (2026-06-03): the deploy creates an SDN zone + vnet and, for a
    VLAN zone (the smoke scenario uses `vlan: 4000`), a backing OVS underlay
    bridge named `underlay_bridge_for_plan(plan_id)` — NOT the pre-SDN-native
    per-net `bridge_name_for_net(net_id)`. Deleting the old name left the zone,
    vnet and underlay bridge behind (live-verified leak, dev45 2026-06-08).
    """
    from axrange.adapter_proxmox.naming import (
        underlay_bridge_for_plan,
        vmid_for_op,
        vnet_id_for_net,
        zone_id_for_plan,
    )

    for op in plan.ops.values():
        if op.op_name != "clone-vm":
            continue
        vmid = vmid_for_op(plan.plan_id, op.op_id)
        try:
            if bp.vm_exists(vmid):
                bp.destroy_vm(vmid)
                log(f"smoke teardown: destroyed vm {vmid}")
        except Exception as e:  # noqa: BLE001
            log(f"smoke teardown: vm {vmid} destroy failed: {e}")

    zone = zone_id_for_plan(plan.plan_id)
    vnet = vnet_id_for_net(net_id, plan_id=plan.plan_id)
    try:
        bp.delete_sdn(zone, vnet)
        log(f"smoke teardown: removed sdn vnet {vnet} + zone {zone}")
    except Exception as e:  # noqa: BLE001
        log(f"smoke teardown: sdn {zone}/{vnet} removal failed: {e}")

    bridge = underlay_bridge_for_plan(plan.plan_id)
    try:
        bp.delete_bridge(bridge)
        log(f"smoke teardown: removed bridge {bridge}")
    except Exception as e:  # noqa: BLE001
        log(f"smoke teardown: bridge {bridge} removal failed: {e}")
