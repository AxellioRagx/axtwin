"""Tests for `axrange/cli/main.py` (code-review, 2026-07-07).

Focused coverage for the cli-side changes in that review:

  - `_DEFAULT_DB_PATH` shares its resolution with the API's
    `axrange.api.settings.default_db_path()` (finding #7) instead of an
    independent recompute.
  - `cmd_destroy` heartbeats the scenario lease for the life of the teardown
    (finding #2), same as the deploy path already does.
  - `cmd_deploy` delegates plan-build to the shared
    `axrange.api.service.build_plan_and_ctx` (finding #3) and its executor
    block to the shared `run_deploy_executor`/`release_deploy_resources`
    (also finding #3) — exercised end-to-end via a real `cmd_deploy` run
    against a fake adapter (no Proxmox).
  - `--fresh` (finding #1 / LES-007): a `cmd_deploy --fresh` redeploy of a
    completed plan mints a new deploy_id and actually re-runs every op.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from axrange.api.settings import default_db_path
from axrange.cli import main as climain

FIXT_IR = Path(__file__).parent / "fixtures" / "ir"
FIXT_ROOT = Path(__file__).parent / "fixtures"


def test_default_db_path_is_shared_with_api():
    """Finding #7 (2026-07-07): one function resolves AXRANGE_DB /
    ~/.axrange/state.db, not two independently-recomputed copies."""
    assert climain._DEFAULT_DB_PATH == default_db_path()


def test_cli_importable_without_fastapi(monkeypatch):
    """The CLI must run without the optional `api` extra (fastapi/uvicorn)
    installed (`pyproject.toml`): `axrange.api.settings` (finding #7's shared
    helper) must not drag in `axrange.api.app`'s FastAPI import merely by
    importing the `axrange.api` package."""
    import sys

    for name in list(sys.modules):
        if name == "fastapi" or name.startswith("fastapi."):
            monkeypatch.delitem(sys.modules, name, raising=False)

    class _BlockFastapi:
        def find_module(self, name, path=None):
            if name == "fastapi" or name.startswith("fastapi."):
                raise ImportError("fastapi blocked for this test")
            return None

    blocker = _BlockFastapi()
    sys.meta_path.insert(0, blocker)
    try:
        import importlib

        import axrange.api as api_pkg

        importlib.reload(api_pkg)  # re-run __init__ under the block
        from axrange.api.settings import default_db_path as _dbp

        _dbp()  # no ImportError
    finally:
        sys.meta_path.remove(blocker)


def test_cmd_destroy_heartbeats_the_lease(tmp_path, monkeypatch):
    """Finding #2 (2026-07-07): `cmd_destroy` wraps the teardown call in a
    `Heartbeat`, the same fix `api.service.destroy_deploy` got, so a long
    sweep can't outrun the scenario lease's fixed TTL."""
    import axrange.adapter_proxmox as adapter_mod
    import axrange.planner.locks as locks_mod
    from tests.test_teardown import _RecordTeardown, seed_deploy

    calls: dict[str, int] = {"started": 0, "stopped": 0}
    seen: dict[str, str] = {}

    class _FakeHeartbeat:
        def __init__(self, db_path, *, deploy_id, scenario_id, **kw):
            seen["deploy_id"] = deploy_id
            seen["scenario_id"] = scenario_id

        def start(self):
            calls["started"] += 1

        def stop(self):
            calls["stopped"] += 1

    fake_backend = _RecordTeardown()
    monkeypatch.setattr(locks_mod, "Heartbeat", _FakeHeartbeat)
    monkeypatch.setattr(adapter_mod, "ProxmoxTeardown", lambda cfg: fake_backend)

    db_path = str(tmp_path / "state.db")
    info = seed_deploy(db_path, deploy_id="sc-cli-heartbeat")

    args = argparse.Namespace(
        deploy_id=info["deploy_id"],
        db=db_path,
        adapter_config="/nonexistent.conf",
        node=None,
        allow_destroy=True,
    )
    rc = climain.cmd_destroy(args)

    assert rc == 0
    assert calls == {"started": 1, "stopped": 1}
    assert seen["deploy_id"] == info["deploy_id"]
    assert seen["scenario_id"] == info["scenario_id"]
    assert len(fake_backend.calls) >= 6


def _deploy_args(*, db: str, fresh: bool) -> argparse.Namespace:
    return argparse.Namespace(
        command="deploy",
        scenario=str(FIXT_IR / "valid_slice" / "scenario.yaml"),
        images=str(FIXT_ROOT),
        adapter_config="/nonexistent.conf",
        sensor_ip="10.255.255.10",
        node="pxtest1",
        db=db,
        dry_run=False,
        fresh=fresh,
    )


def test_cmd_deploy_fresh_reruns_completed_plan(tmp_path, monkeypatch, capsys):
    """Finding #1 / LES-007 regression, verified through the real `cmd_deploy`
    CLI path (not just the API's): a `--fresh` redeploy of an already-
    completed plan gets a brand-new deploy_id whose op rows all actually run
    (not silently skipped)."""
    import axrange.adapter_proxmox as adapter_mod
    import axrange.config_exec as config_exec_mod
    from axrange.planner import StateStore
    from tests.test_planner_executor import _FakeAdapter

    monkeypatch.setattr(adapter_mod, "ProxmoxAdapter", lambda cfg: _FakeAdapter())
    monkeypatch.setattr(config_exec_mod, "ConfigBackend", lambda cfg: _FakeAdapter())

    db_path = str(tmp_path / "state.db")

    rc1 = climain.cmd_deploy(_deploy_args(db=db_path, fresh=False))
    assert rc1 == 0

    store = StateStore(db_path)
    did1 = store.latest_deploys(limit=1)[0].deploy_id
    n_ops_1 = len(store.ops_for_deploy(did1))
    store.close()
    assert n_ops_1 > 0

    rc2 = climain.cmd_deploy(_deploy_args(db=db_path, fresh=True))
    assert rc2 == 0

    store = StateStore(db_path)
    did2 = store.latest_deploys(limit=1)[0].deploy_id
    assert did2 != did1
    ops2 = store.ops_for_deploy(did2)
    assert len(ops2) == n_ops_1
    assert all(op.status.value == "completed" for op in ops2)
    # the prior deploy's own rows are untouched (composite PK)
    ops1 = store.ops_for_deploy(did1)
    assert len(ops1) == n_ops_1
    store.close()


def test_cmd_deploy_heartbeats_the_lease(tmp_path, monkeypatch):
    """Finding #3: `cmd_deploy`'s executor block runs through the shared
    `run_deploy_executor`, which still starts the same `Heartbeat` the
    unrefactored block did."""
    import axrange.adapter_proxmox as adapter_mod
    import axrange.config_exec as config_exec_mod
    import axrange.planner.locks as locks_mod
    from tests.test_planner_executor import _FakeAdapter

    import threading

    calls: dict[str, int] = {"started": 0, "stopped": 0}

    class _FakeHeartbeat:
        def __init__(self, db_path, *, deploy_id, scenario_id, **kw):
            self.lost = threading.Event()

        def start(self):
            calls["started"] += 1

        def stop(self):
            calls["stopped"] += 1

    monkeypatch.setattr(locks_mod, "Heartbeat", _FakeHeartbeat)
    monkeypatch.setattr(adapter_mod, "ProxmoxAdapter", lambda cfg: _FakeAdapter())
    monkeypatch.setattr(config_exec_mod, "ConfigBackend", lambda cfg: _FakeAdapter())

    db_path = str(tmp_path / "state.db")
    rc = climain.cmd_deploy(_deploy_args(db=db_path, fresh=False))

    assert rc == 0
    assert calls == {"started": 1, "stopped": 1}


def test_cmd_destroy_cascades_to_standalone_tap_plan(tmp_path, monkeypatch):
    """S3: `axrange destroy <te>` also sweeps a standalone tap-plan bound to
    it (docs/design/06-taps-sensors.md), via
    `axrange.o2control.taps.cascade_destroy_for_te` -- exercised through the
    real CLI verb, not just the helper function directly."""
    import axrange.adapter_proxmox as adapter_mod
    from axrange.adapter_proxmox import naming as n
    from axrange.o2control.taps import TapBindingSpec, TapPlanSpec, apply_tap_plan
    from axrange.planner import StateStore
    from tests.test_o2_taps import (
        DEFAULT_SENSORS, TE_DEPLOY, _FakeMirAdapter, _FakeTeardown,
        _seed_o2_surface, _seed_te,
    )

    db_path = str(tmp_path / "state.db")
    store = StateStore(db_path)
    try:
        _seed_te(store)
        _seed_o2_surface(store, sensors=DEFAULT_SENSORS)
    finally:
        store.close()

    apply_adapter = _FakeMirAdapter()
    tap_status = apply_tap_plan(
        spec=TapPlanSpec(
            target=TE_DEPLOY,
            bindings=[TapBindingSpec(tap="corp", sensors=["pktxpress-1"])],
        ),
        db_path=db_path, adapter=apply_adapter, config_backend=apply_adapter,
        target_node="pxtest1",
    )
    assert tap_status.status == "completed"

    fake_backend = _FakeTeardown()
    fake_adapter = _FakeMirAdapter()
    monkeypatch.setattr(adapter_mod, "ProxmoxTeardown", lambda cfg: fake_backend)
    monkeypatch.setattr(adapter_mod, "ProxmoxAdapter", lambda cfg: fake_adapter)

    args = argparse.Namespace(
        deploy_id=TE_DEPLOY, db=db_path, adapter_config="/nonexistent.conf",
        node=None, allow_destroy=True,
    )
    rc = climain.cmd_destroy(args)
    assert rc == 0

    store = StateStore(db_path)
    try:
        te = store.get_deploy(TE_DEPLOY)
        tap_dep = store.get_deploy(tap_status.deploy_id)
    finally:
        store.close()
    assert te.status.value == "destroyed"
    assert tap_dep.status.value == "destroyed"
    assert ("gretap", n.gre_port_for_bridge_sensor("br-corp", "pktxpress-1")) in fake_backend.calls


def test_cmd_destroy_cascades_before_sweeping_the_twins_own_vms(tmp_path, monkeypatch):
    """LES-171: the sibling cascades must run BEFORE the twin's own sweep.

    Every sibling tier binds into the twin's LIVE VMs -- the tap tier's
    de-scope re-asserts a per-VM mirror hookscript (`qm set <vmid>
    --hookscript`), the traffic tier's persona stop is a QGA `agent/exec`
    into the guest -- so a cascade sequenced after the VM purge can only ever
    fail with "Configuration file ... does not exist". Because both of those
    loops are best-effort (they warn and do NOT count into
    `TeardownResult.errors`), the inverted order still reported
    `Result: DESTROYED`; only the ORDER itself is assertable, which is what
    this test does. Live-repro'd on dev45 2026-07-29 (32-host
    `corp-enterprise`: 30 `mirror-descope` + 25 `persona stop failed`
    warnings under a green destroy).

    Both the tap cascade and the twin's own sweep are handed the SAME
    `_FakeTeardown`, so its `calls` list is a single ordered trace across the
    two phases."""
    import axrange.adapter_proxmox as adapter_mod
    from axrange.adapter_proxmox import naming as n
    from axrange.o2control.taps import TapBindingSpec, TapPlanSpec, apply_tap_plan
    from axrange.planner import StateStore
    from tests.test_o2_taps import (
        DEFAULT_SENSORS, TE_DEPLOY, _FakeMirAdapter, _FakeTeardown,
        _seed_o2_surface, _seed_te,
    )

    db_path = str(tmp_path / "state.db")
    store = StateStore(db_path)
    try:
        _seed_te(store)
        _seed_o2_surface(store, sensors=DEFAULT_SENSORS)
    finally:
        store.close()

    apply_adapter = _FakeMirAdapter()
    tap_status = apply_tap_plan(
        spec=TapPlanSpec(
            target=TE_DEPLOY,
            bindings=[TapBindingSpec(tap="corp", sensors=["pktxpress-1"])],
        ),
        db_path=db_path, adapter=apply_adapter, config_backend=apply_adapter,
        target_node="pxtest1",
    )
    assert tap_status.status == "completed"

    fake_backend = _FakeTeardown()
    fake_adapter = _FakeMirAdapter()
    monkeypatch.setattr(adapter_mod, "ProxmoxTeardown", lambda cfg: fake_backend)
    monkeypatch.setattr(adapter_mod, "ProxmoxAdapter", lambda cfg: fake_adapter)

    args = argparse.Namespace(
        deploy_id=TE_DEPLOY, db=db_path, adapter_config="/nonexistent.conf",
        node=None, allow_destroy=True,
    )
    assert climain.cmd_destroy(args) == 0

    kinds = [kind for kind, _target in fake_backend.calls]
    gretap_at = kinds.index("gretap")          # the tap cascade
    first_vm_at = kinds.index("vm")            # the twin's own sweep
    assert gretap_at < first_vm_at, (
        "tap cascade must complete before the twin's VMs are purged; "
        f"got call order {fake_backend.calls}"
    )
    # And it really was the tap tier's gretap, not a te-owned one.
    assert fake_backend.calls[gretap_at] == (
        "gretap", n.gre_port_for_bridge_sensor("br-corp", "pktxpress-1"),
    )
