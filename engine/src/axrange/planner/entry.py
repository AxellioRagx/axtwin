"""The deploy entry points every surface shares: build a plan for a validated
scenario, run it through the executor, release what it held.

Factored out of `axrange.api.service` (2026-10-20, `api/Spec.md` Notes) because
the o2control playouts (`control`, `taps`, `traffic`, `exercise`) and the CLI
import these, and a domain layer must not depend on the API layer. The API's
`service` module keeps thin re-exports that translate `PlanBuildError` into its
HTTP-shaped `ApiError`; nothing else in this module knows the API exists.

The sequence is ONE implementation on purpose (LES-037): load adapter config ->
ClusterContext -> parse IR -> `build_plan`, then `Executor(...)` construction +
persist + heartbeat + run, then heartbeat-stop + best-effort lock/lease release.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


class PlanBuildError(Exception):
    """`build_plan_and_ctx` failed. `status` is an HTTP-ish hint (always 400
    here: a missing/misconfigured node or a plan-build error) so the API's
    wrapper can map it without inspecting the message -- the same shape
    `axrange.o2control.O2ControlError` uses."""

    def __init__(self, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


@dataclass
class PlanBuildResult:
    plan: Any
    cfg: Any
    ctx: Any
    ir: Any
    raw: Any


def build_plan_and_ctx(scenario_path: Path, manifests: Any, *,
                       adapter_config: Optional[str],
                       target_node: Optional[str] = None,
                       tier: str = "te",
                       scenario_dir: Optional[str] = None) -> PlanBuildResult:
    """Load adapter config, build the ClusterContext, and build a Plan for an
    ALREADY-VALIDATED scenario at `scenario_path`. Raises `PlanBuildError` on
    failure (400 for a missing/misconfigured node or a plan-build error); both
    `_build_plan_and_ctx` below and `cli.main._load_planning_inputs` catch and
    translate it for their surface (HTTP status vs. stderr + SystemExit).

    `tier` is the resource-ownership tier tag (`docs/CONVENTIONS.md` §2)
    threaded into `build_plan`; default `"te"` (unchanged behavior for the
    CLI/API). `axrange.o2control` is the one caller that passes `"o2"` when
    it composes the standing surface deploy (`docs/design/05-o2-surface.md`)
    — it still writes its composed spec to a real file so this same
    load-config/build-ClusterContext/parse-IR/build-Plan sequence is reused
    verbatim (LES-037), rather than re-implementing it against an in-memory IR.

    No `sensor_ip`/sensor identity is threaded here any more (2026-07-13,
    docs/WORK_ORDER_sensor_agnostic_te.md) — a `te_` plan is sensor-agnostic
    (no tap→sensor wiring at plan time; that is entirely the A2 tap playout's
    job, `axrange o2 tap apply`, against A1's standing sensor inventory).
    """
    from axrange.ir.parser import parse_ir
    from axrange.platform import load_config
    from axrange.planner import build_plan
    from axrange.planner.cluster import ClusterContext

    cfg = load_config(adapter_config)
    if target_node:
        cfg.default_node = target_node
    if not cfg.default_node:
        # Worded to read sensibly from both callers: the API's `target_node`
        # request field and the CLI's `--node` flag both set this.
        raise PlanBuildError(
            "DEFAULT_NODE missing; set it in adapter.conf, or override via "
            "target_node (API) / --node (CLI)",
        )

    ctx = ClusterContext.single_node(cfg.default_node)

    parse_result = parse_ir(path=str(scenario_path))
    try:
        plan = build_plan(
            parse_result.ir, manifests, ctx, raw_ir=parse_result.raw,
            scenario_dir=scenario_dir or str(scenario_path.parent), tier=tier,
        )
    except Exception as e:  # noqa: BLE001
        raise PlanBuildError(f"plan build failed: {e}")
    return PlanBuildResult(
        plan=plan, cfg=cfg, ctx=ctx, ir=parse_result.ir, raw=parse_result.raw
    )


def run_deploy_executor(store, adapter, config_backend, plan, ctx, deploy_id: str,
                        heartbeat) -> Any:
    """Build the `Executor`, persist the plan, start the heartbeat, and run.

    Returns the executor's result; propagates any exception `executor.run()`
    raises (callers decide whether to swallow it or let it surface).
    """
    from axrange.planner import Executor

    executor = Executor(
        plan, store, adapter, ctx, deploy_id=deploy_id,
        config_backend=config_backend,
        # Wire the split-brain guard (code-review finding #2, 2026-07-07):
        # without this, `heartbeat.lost` is set but never polled and the
        # abort is dead. This is the SOLE `Executor(...)` construction for
        # both the CLI and API deploy paths (`cli.main.cmd_deploy` routes
        # through this function too), so wiring it here covers both.
        lost_event=heartbeat.lost,
    )
    executor.persist_plan()
    heartbeat.start()
    return executor.run()


def release_deploy_resources(store, scenario_id: str, deploy_id: str, heartbeat) -> None:
    """Stop the heartbeat, then best-effort release the deploy's resource
    locks and scenario lease. Each release is independently guarded so one
    failing (e.g. a DB hiccup) never blocks the others or leaves the
    connection unclosed — the caller still calls `store.close()` itself.
    """
    heartbeat.stop()
    try:
        store.release_resource_locks_for_deploy(deploy_id)
    except Exception:  # noqa: BLE001
        pass
    try:
        store.release_lease(scenario_id, deploy_id)
    except Exception:  # noqa: BLE001
        pass
