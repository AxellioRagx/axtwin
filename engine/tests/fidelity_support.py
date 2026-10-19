"""Shared, non-collected helpers for the fidelity-scorecard tests.

Builds a REAL `StateStore` for the `mini-cast` bundle fixture by running the
actual `materialize_twin_config` -> `parse_ir` -> `build_plan` -> `Executor`
path against a fake adapter -- the same idiom `tests/test_twin_inventory.py`
uses. Two reasons it is not a hand-built row fixture:

  * `ir_hash` has to come from the SAME expansion `confirm_deploy_gate` will
    re-run, or the deploy-gate confirmation this whole feature rests on is
    never actually exercised (LES-214: test a confirm-by-hash helper against
    the persisted artifact, never the producer's own in-memory dict);
  * row 3 reads `provision-app`/`run-script` inputs back out of persisted
    op rows, so the serialization path must be the real one (LES-068).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from axrange.ir.parser import parse_ir
from axrange.manifest.loader import load_registry
from axrange.planner import Executor, StateStore, build_plan
from axrange.planner.cluster import ClusterContext
from axrange.twinconfig import expand_twin_config, materialize_twin_config

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "tests" / "fixtures"
CAST_BUNDLES = FIXTURES / "cast-bundles"
MINI_CAST = CAST_BUNDLES / "mini-cast"
# The same content with a CANONICAL provenance sidecar beside it, so the
# two differ in exactly one thing: whether evidence classes are read from
# the producer or re-derived (M4).
MINI_CAST_PROV = CAST_BUNDLES / "mini-cast-prov"
REAL_CAST = REPO_ROOT / "twins" / "corpapcn-twin"


class FakeAdapter:
    """Canned per-op outputs, one distinct vmid per host."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self._next_vmid = 900100

    def run(self, op, resolved_inputs, ctx):
        self.calls.append((op.op_name, dict(resolved_inputs)))
        if op.op_name == "clone-vm":
            self._next_vmid += 1
            return {"vmid": self._next_vmid}
        if op.op_name == "create-ovs-bridge":
            net = op.scope.get("net") or op.scope.get("scenario")
            return {"bridge_name": f"tebr_{net}"}
        if op.op_name == "create-vnet":
            return {"vnet_id": f"tev_{op.scope.get('net')}"}
        return {}


def build_mini_deploy(
    tmp_path: Path, *, deploy_id: str = "mini-cast-fid1",
    param_overrides: Optional[dict[str, str]] = None,
    config_id: str = "mini-cast",
) -> tuple[StateStore, str]:
    """Expand + deploy the `mini-cast` fixture bundle into a fresh state
    store, through the real planner/executor."""
    manifests = load_registry(root=FIXTURES)
    ir_dict, _params = expand_twin_config(
        config_id, catalog_dir=str(CAST_BUNDLES), param_overrides=param_overrides,
    )
    scenario_path = materialize_twin_config(
        config_id, catalog_dir=str(CAST_BUNDLES),
        param_overrides=param_overrides,
        out_path=tmp_path / f"{config_id}.ir.yaml",
    )
    parsed = parse_ir(path=scenario_path)
    ctx = ClusterContext.single_node("px1")
    plan = build_plan(
        parsed.ir, manifests, ctx, raw_ir=ir_dict,
        # `install: [{script: ...}]` resolves against the BUNDLE dir, which is
        # where the cast's own `scripts/` live.
        scenario_dir=str(CAST_BUNDLES / config_id),
    )
    store = StateStore(str(tmp_path / "state.db"))
    store.create_deploy(
        deploy_id=deploy_id, scenario_id=plan.scenario_id,
        scenario_version=plan.scenario_version, scenario_schema_version=1,
        plan_id=plan.plan_id, ir_hash=plan.ir_hash, placement=plan.placement,
        tier="te",
    )
    adapter = FakeAdapter()
    ex = Executor(
        plan, store, adapter, ctx, config_backend=adapter,
        deploy_id=deploy_id, sleep_fn=lambda _: None,
    )
    ex.persist_plan()
    result = ex.run()
    assert result.succeeded, result
    return store, deploy_id


def mini_inputs(store: StateStore, deploy_id: str, **kwargs: Any):
    """`(reference, reproduced, gate)` for a mini-cast deploy."""
    from axrange.fidelity import (
        build_reproduced, load_reference_bundle, resolve_gate_info,
    )

    rep = build_reproduced(store, deploy_id, **kwargs)
    ref = load_reference_bundle(MINI_CAST)
    gate = resolve_gate_info(
        config_id=rep.scenario_id, ir_hash=rep.ir_hash,
        catalog_dir=[str(CAST_BUNDLES)],
    )
    return ref, rep, gate


class FakeGuestAgent:
    """A `GuestAgent` stand-in for the probe runner.

    Every backend seam the runner can reach is faked HERE, so no test ever
    resolves a gitignored `adapter.conf` through the process cwd or reaches a
    cluster (LES-208).
    """

    def __init__(self, *, results=None, python3: bool = True, listens: str = "") -> None:
        self.results = results or {}
        self.python3 = python3
        self.listens = listens
        self.written: list[tuple[int, str, str]] = []
        self.execs: list[tuple[int, list[str]]] = []
        self.removed: list[int] = []

    # -- GuestAgent surface ----------------------------------------------
    def write_file(self, vmid, path, content, mode=None):
        self.written.append((int(vmid), str(path), content))

    def exec(self, vmid, argv, timeout_s=None):
        self.execs.append((int(vmid), list(argv)))
        joined = " ".join(argv)
        if "command -v python3" in joined:
            return _Res(0 if self.python3 else 1, "/usr/bin/python3" if self.python3 else "", "")
        if argv[0] == "python3":
            import json as _json

            from axrange.fidelity.probe import BEGIN_MARKER, END_MARKER

            payload = {"results": [
                {"key": k, "status": v[0], "detail": v[1]}
                for k, v in sorted(self.results.items())
            ]}
            return _Res(0, f"{BEGIN_MARKER}\n{_json.dumps(payload)}\n{END_MARKER}\n", "")
        if "rm -f" in joined:
            self.removed.append(int(vmid))
            return _Res(0, "", "")
        if "ss -lnH" in joined:
            return _Res(0, self.listens, "")
        return _Res(0, "", "")


class _Res:
    def __init__(self, exit_code, stdout, stderr):
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr

    @property
    def ok(self):
        return self.exit_code == 0
