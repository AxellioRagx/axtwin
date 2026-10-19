"""The O2 control component's core logic (A1, `docs/design/05-o2-surface.md`).

Stands up / inspects / tears down the standing O2 surface by REUSING the
engine as a spec executor — not a second orchestrator (§1 of the brief). This
module is the ONLY place `axrange.cli`/`axrange.api` need to import for `axrange
o2 up|status|down` / `POST|GET|DELETE /api/o2/surface`; both surfaces stay
thin (`docs/CONVENTIONS.md` code-structure rule, mirroring `config_exec`).

Boundaries (LES-025): this module imports the engine (`planner`,
`adapter_proxmox`, `ir`, `manifest`, `sensors`) — downward only. The engine
never imports `o2control`.

Reuse (LES-037): the compose→plan→run sequencing below is built ENTIRELY out
of the same primitives the CLI/API deploy paths use — `build_plan_and_ctx`,
`run_deploy_executor`, `release_deploy_resources` (`axrange.api.service`),
`StateStore.create_deploy`/`find_deploy_for_plan`/`acquire_lease`,
`locks.Heartbeat`, and `planner.destroy_deploy` — nothing here re-implements
"build plan, run executor, release resources" or "reverse-of-create
teardown"; it only supplies a different SPEC (composed in-memory) and a
different TIER (`"o2"`) than a normal scenario deploy does.
"""
from __future__ import annotations

import secrets
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from axrange.o2control.compose import compose_surface_ir
from axrange.o2control.manifest import (
    SurfaceManifest,
    SurfaceManifestError,
    default_surface_manifest,
    load_surface_manifest,
)
from axrange.planner.opview import op_fields
from axrange.sensors import DEFAULT_SENSOR_TYPE

# The tier every o2control-created deploy carries (`docs/CONVENTIONS.md` §2).
TIER = "o2"


class O2ControlError(Exception):
    """A control-layer failure with an HTTP-ish status hint, mirroring
    `axrange.api.service.ApiError` so `axrange/api/`'s thin wrapper can map
    it the same way (see `docs/design/05-o2-surface.md`)."""

    def __init__(self, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


# ---------------------------------------------------------------------------
# Inventory / status shapes — the A2/A3 discovery source (§2.3 of the brief).
# ---------------------------------------------------------------------------
@dataclass
class SensorInventoryEntry:
    """One standing sensor: `{sensor_id -> type, transport_ip, deploy_id}`
    (work order §3)."""

    sensor_id: str
    type: str
    transport_ip: Optional[str]
    deploy_id: str


@dataclass
class SurfaceStatus:
    deploy_id: Optional[str]
    # "not-deployed" | a DeployStatus value ("running"/"completed"/"failed"/...)
    status: str
    sensors: list[SensorInventoryEntry] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Inventory derivation — reads either the in-memory Plan (right after a
# stand-up) or the persisted OpRows (a later `status`/`down` call, in a
# fresh process with no Plan in hand) via the SAME extraction logic, since
# both carry the same (op_name, scope, inputs_resolved) shape.
# ---------------------------------------------------------------------------
def _inventory_from_ops(ops: Iterable[Any], deploy_id: str) -> list[SensorInventoryEntry]:
    """`deploy-sensor[$host]` carries `sensor_id`/`sensor_type` directly
    (OP-MIR-050's inputs, `pass1_emit.py`); `attach-vm-to-vnet[$host@$net]`
    carries the planner-assigned transport `ip` for that same host. Neither
    op needs the original surface manifest to interpret — this makes
    `surface_status`/`tear_down_surface` work from `state.db` alone, without
    re-composing anything.

    Uses `axrange.planner.opview.op_fields` (`docs/WORK_ORDER_cli_lifecycle.md`
    §3.2) for the OpInstance/OpRow duck-typing this function pioneered —
    generalized there so `axrange.cli.main`'s `te`/`tap` `info` extractors
    reuse the SAME normalization instead of a second copy (LES-037)."""
    types: dict[str, str] = {}
    ips: dict[str, str] = {}
    for op in ops:
        name, scope, inputs, _outputs = op_fields(op)
        if name == "deploy-sensor":
            sid = inputs.get("sensor_id") or scope.get("host")
            if sid:
                types[str(sid)] = str(inputs.get("sensor_type") or DEFAULT_SENSOR_TYPE)
        elif name == "attach-vm-to-vnet":
            sid = scope.get("host")
            ip = inputs.get("ip")
            if sid and ip:
                ips[str(sid)] = str(ip)
    return [
        SensorInventoryEntry(
            sensor_id=sid, type=types[sid], transport_ip=ips.get(sid), deploy_id=deploy_id,
        )
        for sid in sorted(types)
    ]


# ---------------------------------------------------------------------------
# Manifest resolution
# ---------------------------------------------------------------------------
def resolve_surface_manifest(
    manifest: Optional[SurfaceManifest] = None,
    surface_path: Optional[str | Path] = None,
) -> SurfaceManifest:
    """One manifest wins, in order: an already-parsed `manifest` object, a
    `surface_path` to load, or the built-in single-tcpdump-sensor default
    (`axrange o2 up` with no `--surface`, work order §3)."""
    if manifest is not None:
        return manifest
    if surface_path is not None:
        try:
            return load_surface_manifest(surface_path)
        except SurfaceManifestError as e:
            raise O2ControlError(str(e), status=400) from e
    return default_surface_manifest()


def compose_and_build_plan(
    *,
    manifest: Optional[SurfaceManifest] = None,
    surface_path: Optional[str | Path] = None,
    images_root: str | Path,
    adapter_config: Optional[str] = None,
    target_node: Optional[str] = None,
) -> tuple[dict[str, Any], Any]:
    """Compose a surface manifest -> IR -> `Plan` (tier="o2"), WITHOUT
    creating or persisting a deploy. Shared by `stand_up_surface` (which
    goes on to persist+run it) and the read-only `axrange o2 plan` entry
    point (`plan_surface`, below) — LES-037: one place composes+builds, not
    two. Returns `(raw_ir, PlanBuildResult)`.
    """
    from axrange.api.service import ApiError, build_plan_and_ctx
    from axrange.manifest.loader import ManifestError, load_registry

    resolved_manifest = resolve_surface_manifest(manifest, surface_path)
    raw_ir = compose_surface_ir(resolved_manifest)
    scenario_path = _write_temp_scenario(raw_ir)
    try:
        try:
            manifests = load_registry(root=Path(images_root))
        except ManifestError as e:
            raise O2ControlError(f"manifest load failure: {e}", status=400) from e

        try:
            r = build_plan_and_ctx(
                scenario_path, manifests, adapter_config=adapter_config,
                target_node=target_node, tier=TIER,
            )
        except ApiError as e:
            raise O2ControlError(e.message, status=e.status) from e
        return raw_ir, r
    finally:
        scenario_path.unlink(missing_ok=True)


def validate_surface(
    *,
    manifest: Optional[SurfaceManifest] = None,
    surface_path: Optional[str | Path] = None,
    images_root: str | Path,
) -> Any:
    """Run the standard validator (Passes 1-3) on the composed surface IR --
    NO plan build, NO adapter config, NO deploy. `axrange o2 validate`'s
    entry point (work order §3.1: "validator on composed IR" -- an entry
    point over machinery that already exists, `compose_surface_ir` +
    `validate_full`)."""
    from axrange.manifest.loader import ManifestError, load_registry
    from axrange.validator.engine import validate_full

    resolved_manifest = resolve_surface_manifest(manifest, surface_path)
    raw_ir = compose_surface_ir(resolved_manifest)
    scenario_path = _write_temp_scenario(raw_ir)
    try:
        try:
            manifests = load_registry(root=Path(images_root))
        except ManifestError as e:
            raise O2ControlError(f"manifest load failure: {e}", status=400) from e
        return validate_full(str(scenario_path), manifests=manifests)
    finally:
        scenario_path.unlink(missing_ok=True)


def plan_surface(
    *,
    manifest: Optional[SurfaceManifest] = None,
    surface_path: Optional[str | Path] = None,
    images_root: str | Path,
    adapter_config: Optional[str] = None,
    target_node: Optional[str] = None,
) -> Any:
    """Dry `build_plan_and_ctx` (tier="o2") on the composed surface IR -- no
    deploy record, no executor run. `axrange o2 plan`'s entry point. Returns
    the `Plan` (same object `axrange te plan` renders via
    `plan.render_tree()`)."""
    _raw_ir, r = compose_and_build_plan(
        manifest=manifest, surface_path=surface_path, images_root=images_root,
        adapter_config=adapter_config, target_node=target_node,
    )
    return r.plan


def _write_temp_scenario(raw_ir: dict[str, Any]) -> Path:
    """Serialize a composed IR dict to a throwaway YAML file so the surface
    deploy can flow through `build_plan_and_ctx` (a `scenario_path: Path`
    API) unchanged (LES-037) — reused verbatim rather than adding a second,
    in-memory-only entry point to that shared helper."""
    from ruamel.yaml import YAML

    fd = tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", prefix="o2-surface-", delete=False
    )
    try:
        YAML(typ="safe").dump(raw_ir, fd)
    finally:
        fd.close()
    return Path(fd.name)


# ---------------------------------------------------------------------------
# Stand up
# ---------------------------------------------------------------------------
def stand_up_surface(
    *,
    manifest: Optional[SurfaceManifest] = None,
    surface_path: Optional[str | Path] = None,
    db_path: str,
    images_root: str | Path,
    adapter_config: Optional[str] = None,
    target_node: Optional[str] = None,
    adapter: Any = None,
    config_backend: Any = None,
) -> SurfaceStatus:
    """Compose the surface spec, run it through the engine tagged
    `tier="o2"`, and return the resulting inventory.

    Idempotent (work order §5 "idempotent re-up (Model-C adopt)"): the
    composed IR's `scenario.id` + content hash into the SAME deterministic
    `plan_id` on every call with an unchanged manifest, so
    `StateStore.find_deploy_for_plan` resumes the prior standing deploy
    exactly the way `axrange deploy` resumes a TE — no extra bookkeeping
    needed here.

    `adapter`/`config_backend` are test seams (inject fakes, no Proxmox);
    production builds the real `ProxmoxAdapter`/`ConfigBackend` from the
    resolved adapter config, same as `cli.main.cmd_deploy`/
    `api.service._proxmox_runner`.
    """
    from axrange.api.service import release_deploy_resources, run_deploy_executor
    from axrange.planner import StateStore
    from axrange.planner.locks import Heartbeat
    from axrange.planner.state import DeployStatus, default_process_info

    _raw_ir, r = compose_and_build_plan(
        manifest=manifest, surface_path=surface_path, images_root=images_root,
        adapter_config=adapter_config, target_node=target_node,
    )
    plan = r.plan

    store = StateStore(db_path)
    try:
        # `exclude_destroyed=True`: a `destroyed` surface deploy is terminal
        # (its resources are gone) and must never be silently revived in
        # place -- treated as no-match, so a fresh deploy_id is minted below
        # (code review finding, docs/WORK_ORDER_cli_lifecycle.md; mirrors
        # `cli.main.cmd_deploy`'s te path -- `axrange o2 deploy` has no
        # `--fresh` flag of its own, so this is the ONLY way a re-deploy
        # after `axrange o2 destroy` gets a new deploy_id).
        existing = store.find_deploy_for_plan(plan.plan_id, exclude_destroyed=True)
        if existing is not None and existing.status == DeployStatus.COMPLETED:
            inv = _inventory_from_ops(store.ops_for_deploy(existing.deploy_id), existing.deploy_id)
            return SurfaceStatus(deploy_id=existing.deploy_id, status="completed", sensors=inv)

        deploy_id = existing.deploy_id if existing is not None else (
            f"{plan.scenario_id}-{secrets.token_hex(4)}"
        )

        lease = store.acquire_lease(
            plan.scenario_id, deploy_id, process_info=default_process_info()
        )
        if not lease.acquired:
            raise O2ControlError(
                f"another o2 surface operation for '{plan.scenario_id}' is in "
                f"progress (deploy_id={lease.held_by_deploy_id})", status=409,
            )

        heartbeat = Heartbeat(
            str(db_path), deploy_id=deploy_id, scenario_id=plan.scenario_id
        )
        try:
            if existing is not None:
                store.reset_incomplete_ops(deploy_id)
                store.update_deploy_status(deploy_id, DeployStatus.RUNNING)
            else:
                store.create_deploy(
                    deploy_id=deploy_id,
                    scenario_id=plan.scenario_id,
                    scenario_version=plan.scenario_version,
                    scenario_schema_version=1,
                    plan_id=plan.plan_id,
                    ir_hash=plan.ir_hash,
                    placement=plan.placement,
                    tier=TIER,
                )

            real_adapter = adapter
            real_config_backend = config_backend
            if real_adapter is None or real_config_backend is None:
                from axrange.adapter_proxmox import ProxmoxAdapter
                from axrange.config_exec import ConfigBackend

                real_adapter = real_adapter or ProxmoxAdapter(r.cfg)
                real_config_backend = real_config_backend or ConfigBackend(r.cfg)

            result = run_deploy_executor(
                store, real_adapter, real_config_backend, plan, r.ctx, deploy_id, heartbeat
            )
            status = "completed" if result.succeeded else "failed"
            inv = _inventory_from_ops(plan.ops.values(), deploy_id)
            return SurfaceStatus(deploy_id=deploy_id, status=status, sensors=inv)
        finally:
            release_deploy_resources(store, plan.scenario_id, deploy_id, heartbeat)
    finally:
        store.close()


# ---------------------------------------------------------------------------
# Status / inventory (§2.3 — the A2/A3 discovery source)
# ---------------------------------------------------------------------------
def _latest_o2_deploy_id(store: Any) -> Optional[str]:
    """The most recently started `o2`-tiered deploy, if any. A1 stands up
    ONE surface deploy at a time (multiple standing surfaces aren't a
    designed concept yet — `docs/design/05-o2-surface.md`), so
    "most recent o2-tier deploy" is an unambiguous "the surface" for now.

    Uses `StateStore.latest_deploy_for_tier` (a direct `tier`-filtered query,
    index-backed) rather than scanning `latest_deploys(limit=N)` client-side:
    the O2 surface is BY DESIGN the OLDEST persistent deploy row (it survives
    every `te` destroy) while `te` rows accumulate monotonically on a busy
    cluster — a capped scan can blow past N newer `te` rows and never reach
    the surface's row, silently reporting "not-deployed" (code-review S1).
    """
    row = store.latest_deploy_for_tier(TIER)
    return row.deploy_id if row is not None else None


def surface_status(*, db_path: str, deploy_id: Optional[str] = None) -> SurfaceStatus:
    """Read-only: the standing surface's inventory, derived purely from
    `state.db` (no manifest needed) — the discovery source A2 (tap playout)
    and A3 (attack/defense playout) query."""
    from axrange.planner import StateStore

    store = StateStore(db_path)
    try:
        did = deploy_id or _latest_o2_deploy_id(store)
        if did is None:
            return SurfaceStatus(deploy_id=None, status="not-deployed", sensors=[])
        d = store.get_deploy(did)
        if d is None:
            return SurfaceStatus(deploy_id=None, status="not-deployed", sensors=[])
        if d.tier != TIER:
            raise O2ControlError(
                f"{did} is a {d.tier!r} deploy, not an O2 surface (tier '{TIER}') "
                "— `axrange o2 info`/`status` only inspect o2-tier surface deploys; "
                "use `axrange te info`/`status` for a target-environment deploy",
                status=400,
            )
        inv = _inventory_from_ops(store.ops_for_deploy(did), did)
        return SurfaceStatus(deploy_id=did, status=d.status.value, sensors=inv)
    finally:
        store.close()


# ---------------------------------------------------------------------------
# Tear down
# ---------------------------------------------------------------------------
def tear_down_surface(
    *,
    db_path: str,
    deploy_id: Optional[str] = None,
    adapter_config: Optional[str] = None,
    node: Optional[str] = None,
    teardown_backend: Any = None,
    log: Callable[[str], None] = lambda _m: None,
) -> Any:
    """Tear down the standing O2 surface deploy by running the engine's
    OWN teardown (`planner.destroy_deploy`) against it — reused verbatim,
    not reimplemented (LES-037). Refuses to touch anything that is not
    `tier="o2"` (a hard guard: `axrange o2 down` must never be pointable at
    a `te`/`tap` deploy, and vice versa — `axrange destroy` never touches an
    `o2` deploy, by `teardown._protected()`'s cross-tier guard).

    Returns the `planner.teardown.TeardownResult` unchanged (the CLI/API
    surfaces map it, same as `axrange destroy` does today).
    """
    from axrange.planner import StateStore, TeardownError, destroy_deploy
    from axrange.planner.locks import Heartbeat
    from axrange.planner.state import default_process_info

    store = StateStore(db_path)
    try:
        did = deploy_id or _latest_o2_deploy_id(store)
        if did is None:
            raise O2ControlError("no standing O2 surface deploy found", status=404)
        d = store.get_deploy(did)
        if d is None:
            raise O2ControlError(f"deploy not found: {did}", status=404)
        if d.tier != TIER:
            raise O2ControlError(
                f"refusing to tear down {did}: tier={d.tier!r} (expected "
                f"'{TIER}') — axrange o2 down only tears down o2-tier surface "
                "deploys; use axrange destroy for a target-environment deploy",
                status=400,
            )

        from axrange.adapter_proxmox import load_config

        cfg = load_config(adapter_config)

        backend = teardown_backend
        if backend is None:
            from axrange.adapter_proxmox import ProxmoxTeardown

            backend = ProxmoxTeardown(cfg)

        lease = store.acquire_lease(
            d.scenario_id, did, process_info=default_process_info()
        )
        if not lease.acquired:
            raise O2ControlError(
                f"another operation on surface '{d.scenario_id}' is in "
                f"progress (deploy_id={lease.held_by_deploy_id})", status=409,
            )

        heartbeat = Heartbeat(str(db_path), deploy_id=did, scenario_id=d.scenario_id)
        heartbeat.start()
        try:
            try:
                result = destroy_deploy(
                    store, backend, did, node=node, log=log,
                    protected_names=cfg.protected_external_names,
                    protected_vmids=cfg.protected_external_vmids,
                )
            except TeardownError as e:
                raise O2ControlError(str(e), status=404) from e
        finally:
            heartbeat.stop()
            try:
                store.release_lease(d.scenario_id, did)
            except Exception:  # noqa: BLE001
                pass
        return result
    finally:
        store.close()
