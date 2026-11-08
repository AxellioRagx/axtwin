"""Tests for the control-plane API (`axrange/api/`, docs/design/11-api-ui.md).

Covers, against a Starlette TestClient + the REAL engine (validator/planner/
state store), with a FAKE adapter for the deploy path (no Proxmox):

  - health / scenarios / topology / node-detail (read DTOs)
  - validate + plan (pure; validation-failure → 422; missing node → 400)
  - async deploy via an injected fake runner: deploy_id returned, op-tree
    readable from state.db, live status overlay on /topology
  - 409 on a concurrent deploy of the same scenario (lease)
  - idempotent re-deploy of a completed scenario (no-op)
  - write-token auth (401 without token when configured)
  - run → not_implemented; destroy → 400 (no allow) / 404 / 409 (lease) / 200
    (teardown via an injected fake backend); pcaps stub
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from axrange.api import ApiSettings, create_app
from axrange.planner import Executor, StateStore
from tests.test_planner_executor import _FakeAdapter, _HardFailAdapter
from tests.test_o2control import _TierAwareFakeAdapter, _TierAwareFakeTeardown

try:
    from starlette.testclient import TestClient
except ImportError:  # pragma: no cover
    pytest.skip("starlette/httpx not installed", allow_module_level=True)


FIXT_IR = Path(__file__).parent / "fixtures" / "ir"
FIXT_ROOT = Path(__file__).parent / "fixtures"


def _settings(tmp_path: Path, *, adapter_config: str = "/nonexistent.conf") -> ApiSettings:
    return ApiSettings(
        db_path=str(tmp_path / "state.db"),
        scenarios_dir=str(FIXT_IR.resolve()),
        images_root=str(FIXT_ROOT.resolve()),
        adapter_config=adapter_config,
    )


def _fake_runner(adapter_factory=_FakeAdapter):
    """Build a synchronous deploy runner that drives the executor with a fake."""

    def runner(settings, plan, cfg, ctx, deploy_id):
        store = StateStore(settings.db_path)
        try:
            adapter = adapter_factory()
            ex = Executor(
                plan, store, adapter, ctx, deploy_id=deploy_id,
                config_backend=adapter,
            )
            ex.persist_plan()
            ex.run()
        except Exception:  # noqa: BLE001
            pass
        finally:
            store.release_resource_locks_for_deploy(deploy_id)
            store.release_lease(plan.scenario_id, deploy_id)
            store.close()

    return runner


def _client(settings, *, runner=None, teardown=None, background=None):
    app = create_app(settings)
    if runner is not None:
        app.state.deploy_runner = runner
        app.state.deploy_background = lambda job: job()  # synchronous
    if teardown is not None:
        app.state.teardown_factory = lambda _settings: teardown
    if background is not None:
        # Independent of `runner` (docs/WORK_ORDER_twin_incremental_update.md
        # tests): synchronous execution against the DEFAULT `_proxmox_runner`
        # (not a test-injected fake) — needed to exercise `create_deploy`'s
        # `functools.partial(_proxmox_runner, post_run=...)` wiring, which
        # only applies when `runner is None` (a caller-injected runner is
        # responsible for its own tail).
        app.state.deploy_background = background
    return TestClient(app)


# ---------------------------------------------------------------------------
# Read endpoints
# ---------------------------------------------------------------------------
def test_health(tmp_path):
    c = _client(_settings(tmp_path))
    # Reads never create a store (fix round 2): absent -> state_db false.
    r = c.get("/api/health")
    assert r.status_code == 200 and r.json()["state_db"] is False
    assert not (tmp_path / "state.db").exists()
    StateStore(str(tmp_path / "state.db")).close()
    r = c.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["state_db"] is True
    assert "version" in body


def test_list_scenarios(tmp_path):
    c = _client(_settings(tmp_path))
    r = c.get("/api/scenarios")
    assert r.status_code == 200
    ids = {s["id"] for s in r.json()["scenarios"]}
    assert "valid_slice" in ids


def test_topology(tmp_path):
    c = _client(_settings(tmp_path))
    r = c.get("/api/scenarios/valid_slice/topology")
    assert r.status_code == 200
    t = r.json()
    types = {n["id"]: n["type"] for n in t["nodes"]}
    assert types["corp"] == "network"
    assert types["h-a"] == "host"
    assert types["h-b"] == "host"
    pairs = {(e["source"], e["target"]) for e in t["edges"]}
    assert ("h-a", "corp") in pairs
    assert ("h-b", "corp") in pairs
    # no deploy yet → no status overlay
    assert all(n.get("status") is None for n in t["nodes"])
    # a network node carries the segment's `egress` (the config's uplink marker
    # the canvas reads); the fixture declares none
    nets = [n for n in t["nodes"] if n["type"] == "network"]
    assert nets and all("egress" in n and n["egress"] is None for n in nets)
    assert all(n["egress"] is None for n in t["nodes"] if n["type"] != "network")


def test_topology_unknown_scenario(tmp_path):
    c = _client(_settings(tmp_path))
    r = c.get("/api/scenarios/does-not-exist/topology")
    assert r.status_code == 404


def test_node_detail(tmp_path):
    c = _client(_settings(tmp_path))
    r = c.get("/api/scenarios/valid_slice/nodes/h-a")
    assert r.status_code == 200
    j = r.json()
    assert j["id"] == "h-a"
    assert j["kind"] == "host"
    assert any(e["target"] == "corp" for e in j["networks"])


def test_node_detail_unknown_node(tmp_path):
    c = _client(_settings(tmp_path))
    r = c.get("/api/scenarios/valid_slice/nodes/nope")
    assert r.status_code == 404


def test_node_detail_live_marker(tmp_path):
    c = _client(_settings(tmp_path))
    r = c.get("/api/scenarios/valid_slice/nodes/h-a?live=true")
    assert r.status_code == 200
    assert r.json()["live"] is not None
    assert r.json()["live"]["available"] is False


# ---------------------------------------------------------------------------
# validate / plan
# ---------------------------------------------------------------------------
def test_validate_pass(tmp_path):
    c = _client(_settings(tmp_path))
    r = c.post("/api/validate", json={"scenario": "valid_slice"})
    assert r.status_code == 200
    j = r.json()
    assert j["disposition"] in ("PASS", "PASS_WITH_WARNINGS")
    assert j["errors"] == 0
    assert 1 in j["passes_run"]


def test_validate_missing_scenario_field(tmp_path):
    c = _client(_settings(tmp_path))
    r = c.post("/api/validate", json={})
    assert r.status_code == 400


def test_validate_unknown_scenario(tmp_path):
    c = _client(_settings(tmp_path))
    r = c.post("/api/validate", json={"scenario": "nope"})
    assert r.status_code == 404


def test_plan_requires_node(tmp_path):
    c = _client(_settings(tmp_path))
    r = c.post("/api/plan", json={"scenario": "valid_slice"})
    assert r.status_code == 400
    assert "DEFAULT_NODE" in r.json()["error"]


def test_plan_ok(tmp_path):
    c = _client(_settings(tmp_path))
    r = c.post("/api/plan", json={
        "scenario": "valid_slice", "target_node": "pxtest1",
        "sensor_ip": "10.255.255.10",
    })
    assert r.status_code == 200
    j = r.json()
    assert j["scenario_id"] == "slice-demo"
    assert j["ops"] > 0
    assert j["barriers"] > 0
    assert j["tree"]


# ---------------------------------------------------------------------------
# deploy (async, fake adapter)
# ---------------------------------------------------------------------------
def test_deploy_lifecycle(tmp_path):
    c = _client(_settings(tmp_path), runner=_fake_runner())
    r = c.post("/api/deploys", json={
        "scenario": "valid_slice", "target_node": "pxtest1",
        "sensor_ip": "10.255.255.10",
    })
    assert r.status_code == 200
    did = r.json()["deploy_id"]
    assert r.json()["detached"] is True

    # readback
    r = c.get(f"/api/deploys/{did}")
    assert r.status_code == 200
    j = r.json()
    assert j["deploy"]["status"] == "completed"
    assert j["counts"].get("completed", 0) > 0
    assert all(op["status"] == "completed" for op in j["ops"])

    # appears in the list
    r = c.get("/api/deploys")
    assert did in {d["deploy_id"] for d in r.json()["deploys"]}

    # /topology live status overlay shows nodes completed
    r = c.get(f"/api/scenarios/valid_slice/topology?deploy_id={did}")
    statuses = {n["id"]: n.get("status") for n in r.json()["nodes"] if n["type"] != "network"}
    assert statuses.get("h-a") == "completed"


def test_deploy_latest_alias(tmp_path):
    c = _client(_settings(tmp_path), runner=_fake_runner())
    c.post("/api/deploys", json={
        "scenario": "valid_slice", "target_node": "pxtest1", "sensor_ip": "10.255.255.10",
    })
    r = c.get("/api/deploys/latest")
    assert r.status_code == 200
    assert r.json()["deploy"]["status"] == "completed"


# ---------------------------------------------------------------------------
# `GET /api/deploys` status filter (`docs/WORK_ORDER_console_selector_and_
# stale_deploys.md`, defect 2) -- a torn-down deploy's cluster resources no
# longer exist, so the DEFAULT (no `status` given) excludes `destroyed`: an
# unfiltered Console shows only live deploys. `?status=destroyed`/`?status=
# all` keep the full history reachable, same rows `list-deploys` shows. This
# is the "one a future refactor will silently break" the work order calls
# out -- pinned here explicitly, not just at the `deploys_matching` unit
# level (`tests/test_planner_state.py`).
# ---------------------------------------------------------------------------
def test_list_deploys_default_excludes_destroyed(tmp_path):
    from axrange.planner.state import DeployStatus
    from tests.test_teardown import seed_deploy

    settings = _settings(tmp_path)
    seed_deploy(settings.db_path, deploy_id="dep-live", scenario_id="sc-a")
    seed_deploy(settings.db_path, deploy_id="dep-gone", scenario_id="sc-b")
    store = StateStore(settings.db_path)
    store.update_deploy_status("dep-live", DeployStatus.COMPLETED)
    store.mark_destroyed("dep-gone")
    store.close()

    c = _client(settings)
    r = c.get("/api/deploys")
    assert r.status_code == 200
    assert {d["deploy_id"] for d in r.json()["deploys"]} == {"dep-live"}


def test_list_deploys_status_all_includes_destroyed(tmp_path):
    from axrange.planner.state import DeployStatus
    from tests.test_teardown import seed_deploy

    settings = _settings(tmp_path)
    seed_deploy(settings.db_path, deploy_id="dep-live", scenario_id="sc-a")
    seed_deploy(settings.db_path, deploy_id="dep-gone", scenario_id="sc-b")
    store = StateStore(settings.db_path)
    store.update_deploy_status("dep-live", DeployStatus.COMPLETED)
    store.mark_destroyed("dep-gone")
    store.close()

    c = _client(settings)
    r = c.get("/api/deploys", params={"status": "all"})
    assert r.status_code == 200
    assert {d["deploy_id"] for d in r.json()["deploys"]} == {"dep-live", "dep-gone"}


def test_list_deploys_status_destroyed_reaches_history(tmp_path):
    from axrange.planner.state import DeployStatus
    from tests.test_teardown import seed_deploy

    settings = _settings(tmp_path)
    seed_deploy(settings.db_path, deploy_id="dep-live", scenario_id="sc-a")
    seed_deploy(settings.db_path, deploy_id="dep-gone", scenario_id="sc-b")
    store = StateStore(settings.db_path)
    store.update_deploy_status("dep-live", DeployStatus.COMPLETED)
    store.mark_destroyed("dep-gone")
    store.close()

    c = _client(settings)
    r = c.get("/api/deploys", params={"status": "destroyed"})
    assert r.status_code == 200
    assert {d["deploy_id"] for d in r.json()["deploys"]} == {"dep-gone"}


def test_list_deploys_status_filters_to_exactly_one_value(tmp_path):
    from axrange.planner.state import DeployStatus
    from tests.test_teardown import seed_deploy

    settings = _settings(tmp_path)
    seed_deploy(settings.db_path, deploy_id="dep-completed", scenario_id="sc-a")
    seed_deploy(settings.db_path, deploy_id="dep-failed", scenario_id="sc-b")
    seed_deploy(settings.db_path, deploy_id="dep-destroyed", scenario_id="sc-c")
    store = StateStore(settings.db_path)
    store.update_deploy_status("dep-completed", DeployStatus.COMPLETED)
    store.update_deploy_status("dep-failed", DeployStatus.FAILED)
    store.mark_destroyed("dep-destroyed")
    store.close()

    c = _client(settings)
    r = c.get("/api/deploys", params={"status": "failed"})
    assert r.status_code == 200
    assert {d["deploy_id"] for d in r.json()["deploys"]} == {"dep-failed"}


def test_list_deploys_unknown_status_is_400(tmp_path):
    c = _client(_settings(tmp_path))
    r = c.get("/api/deploys", params={"status": "bogus"})
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# Twin inventory (`docs/WORK_ORDER_tier_split.md` review SHOULD-FIX #10) --
# an API peer of `axrange te inventory`, not a second implementation (both
# call `axrange.planner.inventory.build_twin_inventory`).
# ---------------------------------------------------------------------------
def test_deploy_inventory(tmp_path):
    c = _client(_settings(tmp_path), runner=_fake_runner())
    r = c.post("/api/deploys", json={
        "scenario": "valid_slice", "target_node": "pxtest1",
        "sensor_ip": "10.255.255.10",
    })
    did = r.json()["deploy_id"]

    r = c.get(f"/api/deploys/{did}/inventory")
    assert r.status_code == 200
    j = r.json()
    assert j["deploy_id"] == did
    assert j["deploy_status"] == "completed"
    host_ids = {h["host_id"] for h in j["hosts"]}
    assert host_ids == {"h-a", "h-b"}
    for h in j["hosts"]:
        assert h["vmid"] is not None
        assert h["vm_name"] is not None
        # `images_root` is configured in `_settings` -> the manifest lookup
        # resolves (debian-12-cloud -> linux), not a fabricated default.
        assert h["os"] == "linux"
        assert h["role"] is None
    # `tap_points` is REMOVED from the published inventory DTO entirely
    # (`docs/WORK_ORDER_tap_eligibility.md` D5, Authority: user, 2026-07-28)
    # -- a twin publishes only `hosts[].nets[]`; a tap plan derives its own
    # candidate points from that, internally, at apply time.
    assert "tap_points" not in j
    # `net_bridge`/`vlan_underlay` are shipped through the API too (review
    # MUST-FIX/SHOULD-FIX, second round): same data, same shape as `te
    # inventory --json`'s `TwinInventory.to_dict()` -- no repeat of the
    # `node`/`vm_name` mismatch the review caught.
    assert "corp" in j["net_bridge"]


def test_deploy_inventory_latest_alias(tmp_path):
    c = _client(_settings(tmp_path), runner=_fake_runner())
    c.post("/api/deploys", json={
        "scenario": "valid_slice", "target_node": "pxtest1", "sensor_ip": "10.255.255.10",
    })
    r = c.get("/api/deploys/latest/inventory")
    assert r.status_code == 200
    assert r.json()["deploy_status"] == "completed"


def test_deploy_inventory_unknown_deploy_404(tmp_path):
    c = _client(_settings(tmp_path))
    r = c.get("/api/deploys/does-not-exist/inventory")
    assert r.status_code == 404


def test_deploy_inventory_marks_router_wan_leg_external(tmp_path):
    """`docs/WORK_ORDER_tap_scopes.md` M2 -- a router's synthetic WAN uplink
    (`net_id == "__wan__"`) is now RECORDED in `hosts[].nets[]` (instead of
    silently discarded), marked `external=True`; the API DTO
    (`HostNetDTO.external`) carries the flag through, not just the engine-
    side `TwinInventory` dataclass. An ordinary twin segment leg stays
    `external=False`."""
    ir_text = """
scenario_schema_version: 1
scenario: { id: wan-inventory, version: 1 }
layers:
  infra:
    networks:
      - { id: corp, cidr: 10.20.0.0/24, vlan: 20 }
    nodes:
      - { id: h-a, kind: host, net: corp, image: debian-12-cloud@v1, requires: [linux], resources: {cpu:1,ram:1Gi,disk:10Gi} }
      - id: r-edge
        kind: router
        nets: [{ net: corp, ip: 10.20.0.1 }]
        image: debian-12-cloud@v1
        requires: [linux]
        resources: {cpu:1,ram:1Gi,disk:10Gi}
  apps: []
  activity: []
bindings: []
"""
    ir_path = tmp_path / "wan-inventory.yaml"
    ir_path.write_text(ir_text)

    c = _client(_settings(tmp_path), runner=_fake_runner())
    r = c.post("/api/deploys", json={
        "scenario": str(ir_path), "target_node": "pxtest1",
        "sensor_ip": "10.255.255.10",
    })
    assert r.status_code == 200, r.text
    did = r.json()["deploy_id"]

    r = c.get(f"/api/deploys/{did}/inventory")
    assert r.status_code == 200, r.text
    j = r.json()
    router = next(h for h in j["hosts"] if h["host_id"] == "r-edge")
    wan_nets = [n for n in router["nets"] if n["net_id"] == "__wan__"]
    assert len(wan_nets) == 1
    assert wan_nets[0]["external"] is True
    corp_nets = [n for n in router["nets"] if n["net_id"] == "corp"]
    assert corp_nets and corp_nets[0]["external"] is False


def test_deploy_idempotent_when_completed(tmp_path):
    settings = _settings(tmp_path)
    c = _client(settings, runner=_fake_runner())
    body = {"scenario": "valid_slice", "target_node": "pxtest1", "sensor_ip": "10.255.255.10"}
    r1 = c.post("/api/deploys", json=body)
    did1 = r1.json()["deploy_id"]
    # second deploy of the unchanged (completed) scenario → same id, no-op
    r2 = c.post("/api/deploys", json=body)
    assert r2.status_code == 200
    assert r2.json()["deploy_id"] == did1
    assert r2.json()["status"] == "completed"
    assert r2.json()["detached"] is False


def test_deploy_fresh_reruns_completed_plan(tmp_path):
    """`fresh: true` regression (LES-007 / finding #1, 2026-07-07): once the
    `ops` PK became deploy-scoped (`deploy_id`, `op_id`), a `--fresh`/`fresh`
    redeploy mints a NEW `deploy_id` that owns its OWN op rows — so the
    executor actually re-runs every op, instead of the old plan-scoped resume
    check silently skipping them all ("N ok" having done nothing new)."""
    settings = _settings(tmp_path)
    fake = _fake_runner()
    c = _client(settings, runner=fake)
    body = {"scenario": "valid_slice", "target_node": "pxtest1", "sensor_ip": "10.255.255.10"}

    r1 = c.post("/api/deploys", json=body)
    did1 = r1.json()["deploy_id"]
    j1 = c.get(f"/api/deploys/{did1}").json()
    assert j1["deploy"]["status"] == "completed"
    n_ops_1 = len(j1["ops"])
    assert n_ops_1 > 0

    # A fresh redeploy of the SAME (unchanged, completed) scenario must NOT
    # take the idempotent no-op branch — it gets a brand-new deploy_id and
    # actually runs the plan again.
    r2 = c.post("/api/deploys", json={**body, "fresh": True})
    assert r2.status_code == 200
    did2 = r2.json()["deploy_id"]
    assert did2 != did1
    assert r2.json()["detached"] is True

    j2 = c.get(f"/api/deploys/{did2}").json()
    assert j2["deploy"]["status"] == "completed"
    # Every op under the NEW deploy actually ran (recorded completed), not
    # skipped — the regression this test guards against.
    assert len(j2["ops"]) == n_ops_1
    assert all(op["status"] == "completed" for op in j2["ops"])

    # The prior deploy's own rows are untouched (composite PK: each deploy_id
    # owns its own op rows — a fresh deploy never disturbs the old one).
    j1_again = c.get(f"/api/deploys/{did1}").json()
    assert j1_again["deploy"]["status"] == "completed"
    assert len(j1_again["ops"]) == n_ops_1


def test_deploy_409_on_concurrent_lease(tmp_path):
    """A held lease (a deploy 'in progress') refuses a second deploy → 409."""
    settings = _settings(tmp_path)

    # A runner that does NOT release the lease, simulating an in-flight deploy.
    def stuck_runner(settings, plan, cfg, ctx, deploy_id):
        store = StateStore(settings.db_path)
        adapter = _FakeAdapter()
        ex = Executor(plan, store, adapter, ctx, deploy_id=deploy_id, config_backend=adapter)
        ex.persist_plan()
        # leave the deploy 'running' and the lease HELD (no release)
        store.close()

    c = _client(settings, runner=stuck_runner)
    body = {"scenario": "valid_slice", "target_node": "pxtest1", "sensor_ip": "10.255.255.10"}
    r1 = c.post("/api/deploys", json=body)
    assert r1.status_code == 200
    # the plan persisted as 'running' with the lease held; a fresh attempt 409s
    r2 = c.post("/api/deploys", json={**body, "fresh": True})
    assert r2.status_code == 409
    assert "in progress" in r2.json()["error"]


def test_deploy_validation_failure_422(tmp_path):
    """A scenario that fails validation is refused before planning (422)."""
    invalid_dir = FIXT_IR / "invalid"
    cand = next(iter(invalid_dir.glob("*.yaml")), None)
    if cand is None:
        pytest.skip("no invalid scenario fixture")
    settings = ApiSettings(
        db_path=str(tmp_path / "state.db"),
        scenarios_dir=str(invalid_dir.resolve()),
        images_root=str(FIXT_ROOT.resolve()),
        adapter_config="/nonexistent.conf",
    )
    c = _client(settings, runner=_fake_runner())
    sid = cand.stem  # flat <id>.yaml → id is the stem
    r = c.post("/api/deploys", json={"scenario": sid, "target_node": "pxtest1"})
    # Validation must fail before any adapter call → 422 (or 400 if it's a plan
    # build error / 404 if the manifest can't resolve); never 200.
    assert r.status_code in (422, 400, 404)
    assert r.status_code != 200

    # And the pure validate endpoint reports it as a FAIL with findings.
    rv = c.post("/api/validate", json={"scenario": sid})
    assert rv.status_code == 200
    assert rv.json()["disposition"] == "FAIL"
    assert rv.json()["errors"] >= 1


@pytest.mark.slow
def test_deploy_records_failure(tmp_path):
    """A hard adapter failure leaves the op tree with a failed op in state.db."""
    c = _client(_settings(tmp_path), runner=_fake_runner(
        adapter_factory=lambda: _HardFailAdapter("start-vm")
    ))
    r = c.post("/api/deploys", json={
        "scenario": "valid_slice", "target_node": "pxtest1", "sensor_ip": "10.255.255.10",
    })
    did = r.json()["deploy_id"]
    j = c.get(f"/api/deploys/{did}").json()
    statuses = {op["op_name"]: op["status"] for op in j["ops"]}
    # the failing op is recorded failed; downstream halted
    assert any(s == "failed" for s in statuses.values())


# ---------------------------------------------------------------------------
# Incremental update (`docs/WORK_ORDER_twin_incremental_update.md`) --
# `create_deploy`'s `update`/`allow_destroy` request fields, same refusal
# semantics as the CLI (`resolve_deploy_plan` is the SHARED helper,
# LES-037). These tests deliberately do NOT use `_client(..., runner=...)`
# (the `runner=` test seam skips the `--update` tail-wiring by design, since
# a caller injecting its own runner owns its own tail) -- instead they
# monkeypatch `ProxmoxAdapter`/`ConfigBackend`/`ProxmoxTeardown` directly
# (matching `tests/test_cli.py`'s idiom) and drive the REAL default
# `_proxmox_runner`, synchronously, to prove the `functools.partial(
# _proxmox_runner, post_run=apply_update_tail)` wiring actually runs the
# tail inside the same request/response cycle.
# ---------------------------------------------------------------------------
@pytest.fixture
def update_fake_env(monkeypatch):
    import axrange.adapter_proxmox as adapter_mod
    import axrange.config_exec as config_exec_mod
    from tests.test_teardown import _RecordTeardown
    from tests.test_twin_incremental_update import _NamingAwareFakeAdapter

    fake = _NamingAwareFakeAdapter()
    monkeypatch.setattr(adapter_mod, "ProxmoxAdapter", lambda cfg: fake)
    monkeypatch.setattr(config_exec_mod, "ConfigBackend", lambda cfg: fake)
    backend = _RecordTeardown()
    monkeypatch.setattr(adapter_mod, "ProxmoxTeardown", lambda cfg: backend)
    return fake, backend


def _update_client(tmp_path) -> "TestClient":
    return _client(_settings(tmp_path), background=lambda job: job())


def test_api_update_add_node_pins_plan_id(tmp_path, update_fake_env):
    from tests.test_twin_incremental_update import ADD_SCENARIO, _base_scenario

    c = _update_client(tmp_path)
    base = tmp_path / "base.yaml"
    base.write_text(_base_scenario())
    r1 = c.post("/api/deploys", json={"scenario": str(base), "target_node": "pxtest1"})
    assert r1.status_code == 200
    did = r1.json()["deploy_id"]
    j1 = c.get(f"/api/deploys/{did}").json()
    assert j1["deploy"]["status"] == "completed"
    plan_id_before = j1["plan_id"]

    add = tmp_path / "add.yaml"
    add.write_text(ADD_SCENARIO)
    r2 = c.post("/api/deploys", json={
        "scenario": str(add), "target_node": "pxtest1", "update": True,
    })
    assert r2.status_code == 200
    assert r2.json()["deploy_id"] == did  # same deploy, not a new one

    j2 = c.get(f"/api/deploys/{did}").json()
    assert j2["plan_id"] == plan_id_before  # THE safety property
    assert j2["deploy"]["status"] == "completed"
    names = {op["op_name"] for op in j2["ops"]}
    assert "clone-vm" in names
    hosts_seen = {
        op["op_id"].rsplit("[", 1)[-1].rstrip("]")
        for op in j2["ops"] if op["op_name"] == "clone-vm"
    }
    assert hosts_seen == {"h-a", "h-b", "h-dmz", "h-new"}


def test_api_update_mutation_refuses_with_409_and_field_detail(tmp_path, update_fake_env):
    from tests.test_twin_incremental_update import _base_scenario

    c = _update_client(tmp_path)
    base = tmp_path / "base.yaml"
    base.write_text(_base_scenario(ha_ram="1Gi"))
    r1 = c.post("/api/deploys", json={"scenario": str(base), "target_node": "pxtest1"})
    assert r1.status_code == 200
    did = r1.json()["deploy_id"]

    mutated = tmp_path / "mutate.yaml"
    mutated.write_text(_base_scenario(ha_ram="4Gi"))
    r2 = c.post("/api/deploys", json={
        "scenario": str(mutated), "target_node": "pxtest1", "update": True,
    })
    assert r2.status_code == 409
    body = r2.json()
    assert "configure-vm-hardware[h-a]" in body["error"]
    assert "mutated" in body["error"]
    mutations = body["detail"]["mutations"]
    ram_op = next(k for k in mutations if "configure-vm-hardware[h-a]" in k)
    assert mutations[ram_op]["ram"] == ["'1Gi'", "'4Gi'"]

    # Nothing executed: the standing deploy is untouched.
    j = c.get(f"/api/deploys/{did}").json()
    assert j["deploy"]["status"] == "completed"


# ---------------------------------------------------------------------------
# `--reapply`'s API peer (`docs/WORK_ORDER_responder_op_row_drift.md` option
# A): the SAME `resolve_deploy_plan` carve-out, reached through the
# `CreateDeployRequest.reapply` field -- proving the two surfaces share one
# implementation rather than the CLI having its own (LES-037).
# ---------------------------------------------------------------------------
def test_api_update_reapply_reexecutes_a_script_content_only_change(
    tmp_path, update_fake_env,
):
    import json as _json

    from axrange.planner import StateStore
    from axrange.planner.dag import _op_id
    from tests.test_twin_incremental_update import (
        _NEW_SCRIPT, _OLD_SCRIPT, _script_scenario,
    )

    fake, _teardown = update_fake_env
    c = _update_client(tmp_path)
    (tmp_path / "setup.sh").write_text(_OLD_SCRIPT)
    scn = tmp_path / "script.yaml"
    scn.write_text(_script_scenario())

    r1 = c.post("/api/deploys", json={"scenario": str(scn), "target_node": "pxtest1"})
    assert r1.status_code == 200
    did = r1.json()["deploy_id"]

    # Content-only edit: the scenario file is untouched, only the script is.
    (tmp_path / "setup.sh").write_text(_NEW_SCRIPT)

    # Without `reapply`, the SAME refusal the CLI gives (409), and the
    # refused-value rendering is fingerprinted, not the whole script.
    r2 = c.post("/api/deploys", json={
        "scenario": str(scn), "target_node": "pxtest1", "update": True,
    })
    assert r2.status_code == 409
    body = r2.json()
    assert "run-script[responder@h-a]" in body["error"]
    assert "new-port-9090" not in body["error"]
    assert "sha256:" in _json.dumps(body["detail"])

    calls_before = len(fake.calls)
    r3 = c.post("/api/deploys", json={
        "scenario": str(scn), "target_node": "pxtest1",
        "update": True, "reapply": True,
    })
    assert r3.status_code == 200
    assert r3.json()["deploy_id"] == did  # same deploy, re-applied in place

    new_calls = [name for name, _i in fake.calls[calls_before:]]
    assert new_calls == ["run-script"]

    store = StateStore(str(tmp_path / "state.db"))
    plan_id = store.get_deploy(did).plan_id
    script_op = _op_id(plan_id, "run-script", {"app": "responder", "host": "h-a"})
    row = store.get_op(did, script_op)
    assert _json.loads(row.inputs_resolved_json)["script_content"] == _NEW_SCRIPT
    store.close()


def test_api_reapply_without_update_is_a_400(tmp_path, update_fake_env):
    from tests.test_twin_incremental_update import _OLD_SCRIPT, _script_scenario

    c = _update_client(tmp_path)
    (tmp_path / "setup.sh").write_text(_OLD_SCRIPT)
    scn = tmp_path / "script.yaml"
    scn.write_text(_script_scenario())

    r = c.post("/api/deploys", json={
        "scenario": str(scn), "target_node": "pxtest1", "reapply": True,
    })
    assert r.status_code == 400
    assert "--reapply" in r.json()["error"]


def test_resolve_deploy_plan_wraps_a_rebuild_failure_as_apierror(tmp_path):
    """S9 (code review): `resolve_deploy_plan`'s two internal rebuild call
    sites (the `--update` pin, and the same-generation resume heal) must
    translate a `PlanBuildError` into an `ApiError` -- mirrors `build_plan_
    and_ctx`'s own catch for the CALLER's first `build_plan` call. Before
    the fix, a bare `PlanBuildError` propagated uncaught from either
    rebuild: an API 500 instead of a 4xx, and an unhandled CLI traceback
    instead of `cmd_deploy`'s existing `except ApiError`.

    Forces the REBUILD branch specifically (not the caller's own first
    build) by giving `resolve_deploy_plan` a standing deploy whose `plan_id`
    DIFFERS from the freshly-built plan's -- exactly the shape a prior
    `--update` leaves behind (§3.4)."""
    from axrange.api.service import ApiError, resolve_deploy_plan
    from axrange.planner import StateStore
    from axrange.planner.plan_builder import PlanBuildError

    db = str(tmp_path / "s.db")
    store = StateStore(db)
    try:
        store.create_deploy(
            deploy_id="d1", scenario_id="sc", scenario_version=1,
            scenario_schema_version=1, plan_id="pinned-plan-id", ir_hash="h-old",
            placement={},
        )

        import axrange.planner as planner_mod

        def _boom(*_a, **_kw):
            raise PlanBuildError("simulated failure")

        # `_rebuild_plan_at` does `from axrange.planner import build_plan`
        # INSIDE the function body, resolved fresh on each call -- patching
        # the attribute on `axrange.planner` (not a pre-bound local name)
        # is what makes this monkeypatch actually take effect.
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(planner_mod, "build_plan", _boom)

            class _FakePlan:
                plan_id = "fresh-plan-id"  # differs from the standing row's
                scenario_id = "sc"
                ir_hash = "h-new"

            with pytest.raises(ApiError) as exc_info:
                resolve_deploy_plan(
                    store, _FakePlan(), ir=object(), raw={}, manifests=object(),
                    ctx=object(), scenario_dir=str(tmp_path), tier="te",
                    fresh=False, update=True, allow_destroy=False,
                )
        assert exc_info.value.status == 400
        assert "plan build failed" in exc_info.value.message
    finally:
        store.close()


def test_api_update_and_fresh_together_400(tmp_path, update_fake_env):
    from tests.test_twin_incremental_update import _base_scenario

    c = _update_client(tmp_path)
    base = tmp_path / "base.yaml"
    base.write_text(_base_scenario())
    c.post("/api/deploys", json={"scenario": str(base), "target_node": "pxtest1"})

    r = c.post("/api/deploys", json={
        "scenario": str(base), "target_node": "pxtest1", "update": True, "fresh": True,
    })
    assert r.status_code == 400
    assert "mutually exclusive" in r.json()["error"]


def test_api_update_removal_without_allow_destroy_refuses_409(tmp_path, update_fake_env):
    from tests.test_twin_incremental_update import REMOVE_SCENARIO, _base_scenario

    c = _update_client(tmp_path)
    base = tmp_path / "base.yaml"
    base.write_text(_base_scenario())
    c.post("/api/deploys", json={"scenario": str(base), "target_node": "pxtest1"})

    remove = tmp_path / "remove.yaml"
    remove.write_text(REMOVE_SCENARIO)
    r = c.post("/api/deploys", json={
        "scenario": str(remove), "target_node": "pxtest1", "update": True,
    })
    assert r.status_code == 409
    assert "allow_destroy" in r.json()["error"] or "--allow-destroy" in r.json()["error"]


def test_api_update_removal_with_allow_destroy_sweeps_and_advances_generation(
    tmp_path, update_fake_env,
):
    _fake, backend = update_fake_env
    from tests.test_twin_incremental_update import REMOVE_SCENARIO, _base_scenario

    c = _update_client(tmp_path)
    base = tmp_path / "base.yaml"
    base.write_text(_base_scenario())
    r1 = c.post("/api/deploys", json={"scenario": str(base), "target_node": "pxtest1"})
    did = r1.json()["deploy_id"]

    remove = tmp_path / "remove.yaml"
    remove.write_text(REMOVE_SCENARIO)
    r2 = c.post("/api/deploys", json={
        "scenario": str(remove), "target_node": "pxtest1", "update": True,
        "allow_destroy": True,
    })
    assert r2.status_code == 200
    assert r2.json()["deploy_id"] == did

    # The removal sweep (post_run/apply_update_tail) actually ran inside
    # this synchronous request -- the teardown backend saw the vm/vnet
    # calls the removed hosts/network needed.
    assert any(kind == "vm" for kind, _ in backend.calls)


def test_api_update_removal_with_protect_guard_violation_sets_failed_status(
    tmp_path, update_fake_env,
):
    """M3 (code review): a non-clean removal sweep (here, a protect-guard
    refusal) must downgrade the deploy's PERSISTED status away from the
    COMPLETED the executor's own successful added-ops pass already wrote --
    a --update whose removal half is still standing is not a green
    deploy. `_proxmox_runner` consumes `apply_update_tail`'s returned
    `UpdateTailResult` and marks the deploy FAILED when `not tail.clean`."""
    from axrange.adapter_proxmox import naming
    from axrange.planner.dag import _op_id
    from tests.test_twin_incremental_update import REMOVE_SCENARIO, _base_scenario

    _fake, backend = update_fake_env

    c = _update_client(tmp_path)
    base = tmp_path / "base.yaml"
    base.write_text(_base_scenario())
    r1 = c.post("/api/deploys", json={"scenario": str(base), "target_node": "pxtest1"})
    did = r1.json()["deploy_id"]
    plan_id = c.get(f"/api/deploys/{did}").json()["plan_id"]

    hb_op_id = _op_id(plan_id, "clone-vm", {"host": "h-b"})
    hb_vmid = naming.vmid_for_op(plan_id, hb_op_id)
    # h-b's PVE-resolved name will look like a standing o2_ resource.
    backend.vm_names[hb_vmid] = "o2-sensor-vm"

    remove = tmp_path / "remove.yaml"
    remove.write_text(REMOVE_SCENARIO)
    r2 = c.post("/api/deploys", json={
        "scenario": str(remove), "target_node": "pxtest1", "update": True,
        "allow_destroy": True,
    })
    assert r2.status_code == 200  # create_deploy itself always answers 200/"running"

    j2 = c.get(f"/api/deploys/{did}").json()
    assert j2["deploy"]["status"] == "failed"
    assert ("vm", str(hb_vmid)) not in backend.calls


def test_api_allow_destroy_without_update_400(tmp_path, update_fake_env):
    """S5 (code review): `allow_destroy` only means anything WITH `update`
    -- must 400, not be silently ignored."""
    from tests.test_twin_incremental_update import _base_scenario

    c = _update_client(tmp_path)
    base = tmp_path / "base.yaml"
    base.write_text(_base_scenario())

    r = c.post("/api/deploys", json={
        "scenario": str(base), "target_node": "pxtest1", "allow_destroy": True,
    })
    assert r.status_code == 400
    assert "--allow-destroy" in r.json()["error"]
    assert "--update" in r.json()["error"]


def test_api_update_refuses_ambiguously_with_two_standing_deploys(tmp_path, update_fake_env):
    """M5 (code review): two standing `te` deploys of one scenario_id (the
    LES-126 edit-without-`--update` path) must refuse an `--update` rather
    than silently binding to whichever `deploys_matching(..., limit=1)`
    happens to rank newest."""
    from tests.test_twin_incremental_update import _base_scenario

    c = _update_client(tmp_path)
    base = tmp_path / "base.yaml"
    base.write_text(_base_scenario(ha_ram="1Gi"))
    r1 = c.post("/api/deploys", json={"scenario": str(base), "target_node": "pxtest1"})
    did1 = r1.json()["deploy_id"]

    edit2 = tmp_path / "edit2.yaml"
    edit2.write_text(_base_scenario(ha_ram="2Gi"))
    r2 = c.post("/api/deploys", json={"scenario": str(edit2), "target_node": "pxtest1"})
    did2 = r2.json()["deploy_id"]
    assert did2 != did1

    edit3 = tmp_path / "edit3.yaml"
    edit3.write_text(_base_scenario(ha_ram="3Gi"))
    r3 = c.post("/api/deploys", json={
        "scenario": str(edit3), "target_node": "pxtest1", "update": True,
    })
    assert r3.status_code == 409
    body = r3.json()
    assert "2 standing te deploys" in body["error"]
    assert set(body["detail"]["deploy_ids"]) == {did1, did2}


# ---------------------------------------------------------------------------
# run / destroy / pcaps / targets
# ---------------------------------------------------------------------------
def test_run_not_implemented(tmp_path):
    c = _client(_settings(tmp_path), runner=_fake_runner())
    did = c.post("/api/deploys", json={
        "scenario": "valid_slice", "target_node": "pxtest1", "sensor_ip": "10.255.255.10",
    }).json()["deploy_id"]
    r = c.post(f"/api/deploys/{did}/run")
    assert r.status_code == 200
    assert r.json()["status"] == "not_implemented"


def test_run_unknown_deploy(tmp_path):
    c = _client(_settings(tmp_path))
    assert c.post("/api/deploys/nope/run").status_code == 404


def test_destroy_requires_allow(tmp_path):
    c = _client(_settings(tmp_path), runner=_fake_runner())
    did = c.post("/api/deploys", json={
        "scenario": "valid_slice", "target_node": "pxtest1", "sensor_ip": "10.255.255.10",
    }).json()["deploy_id"]
    r = c.request("DELETE", f"/api/deploys/{did}", json={"allow_destroy": False})
    assert r.status_code == 400


def test_destroy_teardown(tmp_path):
    """DELETE with allow_destroy tears the deploy down (injected fake teardown),
    returns the action report, and transitions the deploy to `destroyed`."""
    from tests.test_teardown import _RecordTeardown, seed_deploy

    settings = _settings(tmp_path)
    info = seed_deploy(settings.db_path, deploy_id="sc-deadbeef")
    fake = _RecordTeardown()
    c = _client(settings, teardown=fake)

    r = c.request("DELETE", f"/api/deploys/{info['deploy_id']}",
                  json={"allow_destroy": True})
    assert r.status_code == 200
    body = r.json()
    assert body["destroyed"] is True
    assert body["errors"] == 0
    assert body["removed"] >= 6
    # the deploy now reads `destroyed`
    j = c.get(f"/api/deploys/{info['deploy_id']}").json()
    assert j["deploy"]["status"] == "destroyed"


def test_destroy_heartbeats_the_lease(tmp_path, monkeypatch):
    """Finding #2 (2026-07-07): destroy wraps the teardown call in a
    `Heartbeat`, same as the deploy path, so a long sweep can't outrun the
    lease's fixed TTL. Assert one is started and stopped around the call."""
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

    monkeypatch.setattr(locks_mod, "Heartbeat", _FakeHeartbeat)

    settings = _settings(tmp_path)
    info = seed_deploy(settings.db_path, deploy_id="sc-heartbeat")
    c = _client(settings, teardown=_RecordTeardown())

    r = c.request("DELETE", f"/api/deploys/{info['deploy_id']}",
                  json={"allow_destroy": True})
    assert r.status_code == 200
    assert calls == {"started": 1, "stopped": 1}
    assert seen["deploy_id"] == info["deploy_id"]
    assert seen["scenario_id"] == info["scenario_id"]


def test_run_deploy_executor_wires_heartbeat_lost_event(tmp_path, monkeypatch):
    """Regression guard for code-review finding #2 (2026-07-07) going dead a
    second time: `run_deploy_executor` is the SOLE `Executor(...)`
    construction for BOTH the CLI (`cli.main.cmd_deploy`) and API deploy
    paths, so wiring the split-brain abort here covers both surfaces. Spy on
    `Executor` and assert it's constructed with the heartbeat's actual `lost`
    Event (non-None) rather than the abort silently staying unwired."""
    import threading

    import axrange.planner as planner_mod
    from axrange.api.service import run_deploy_executor

    captured: dict[str, object] = {}

    class _SpyExecutor:
        def __init__(self, plan, store, adapter, ctx, *, deploy_id,
                     config_backend=None, lost_event=None):
            captured["lost_event"] = lost_event

        def persist_plan(self):
            pass

        def run(self):
            return "ok"

    monkeypatch.setattr(planner_mod, "Executor", _SpyExecutor)

    class _FakeHeartbeat:
        def __init__(self):
            self.lost = threading.Event()
            self.started = False

        def start(self):
            self.started = True

    heartbeat = _FakeHeartbeat()
    store = StateStore(str(tmp_path / "state.db"))
    try:
        result = run_deploy_executor(
            store, None, None, None, None, "d1", heartbeat,
        )
    finally:
        store.close()

    assert result == "ok"
    assert heartbeat.started is True
    assert captured["lost_event"] is not None
    assert captured["lost_event"] is heartbeat.lost


def test_destroy_allow_via_query_param(tmp_path):
    """Confirmation may ride a query param instead of the body — the path that
    survives a body-stripping DELETE proxy (Grafana's datasource proxy). No body
    at all, `?allow_destroy=true`, still tears down."""
    from tests.test_teardown import _RecordTeardown, seed_deploy

    settings = _settings(tmp_path)
    info = seed_deploy(settings.db_path, deploy_id="sc-queryparam")
    c = _client(settings, teardown=_RecordTeardown())

    r = c.request("DELETE", f"/api/deploys/{info['deploy_id']}?allow_destroy=true")
    assert r.status_code == 200
    assert r.json()["destroyed"] is True


def test_destroy_no_confirmation_anywhere_400(tmp_path):
    """Neither query param nor body → still refused with 400 (the guard holds)."""
    from tests.test_teardown import _RecordTeardown, seed_deploy

    settings = _settings(tmp_path)
    info = seed_deploy(settings.db_path, deploy_id="sc-noconfirm")
    c = _client(settings, teardown=_RecordTeardown())

    r = c.request("DELETE", f"/api/deploys/{info['deploy_id']}")
    assert r.status_code == 400


def test_destroy_409_when_scenario_deploy_in_flight(tmp_path):
    """A held lease for the scenario refuses a destroy → 409 (no mid-deploy
    teardown)."""
    from tests.test_teardown import _RecordTeardown, seed_deploy

    settings = _settings(tmp_path)
    info = seed_deploy(settings.db_path, deploy_id="sc-inflight")
    # Simulate a concurrent deploy holding the scenario lease.
    store = StateStore(settings.db_path)
    store.acquire_lease(info["scenario_id"], "other-deploy")
    store.close()

    c = _client(settings, teardown=_RecordTeardown())
    r = c.request("DELETE", f"/api/deploys/{info['deploy_id']}",
                  json={"allow_destroy": True})
    assert r.status_code == 409


def test_destroy_unknown_deploy(tmp_path):
    c = _client(_settings(tmp_path))
    r = c.request("DELETE", "/api/deploys/nope", json={"allow_destroy": True})
    assert r.status_code == 404


def test_pcaps_stub(tmp_path):
    c = _client(_settings(tmp_path))
    r = c.get("/api/pcaps")
    assert r.status_code == 200
    assert r.json()["implemented"] is False


def test_targets_empty_without_config(tmp_path):
    c = _client(_settings(tmp_path))
    r = c.get("/api/targets")
    assert r.status_code == 200
    # no adapter.conf → empty targets (no crash)
    assert "targets" in r.json()


# ---------------------------------------------------------------------------
# auth
# ---------------------------------------------------------------------------
def test_write_requires_token_when_configured(tmp_path):
    settings = _settings(tmp_path)
    settings.api_token = "s3cret"
    c = _client(settings, runner=_fake_runner())
    body = {"scenario": "valid_slice", "target_node": "pxtest1", "sensor_ip": "10.255.255.10"}
    # no token → 401
    assert c.post("/api/deploys", json=body).status_code == 401
    # wrong token → 401
    assert c.post("/api/deploys", json=body,
                  headers={"Authorization": "Bearer nope"}).status_code == 401
    # right token → 200
    r = c.post("/api/deploys", json=body, headers={"Authorization": "Bearer s3cret"})
    assert r.status_code == 200


def test_reads_open_without_token(tmp_path):
    settings = _settings(tmp_path)
    settings.api_token = "s3cret"
    c = _client(settings)
    # reads stay open even with a token configured (trusted-bind posture)
    assert c.get("/api/health").status_code == 200
    assert c.get("/api/scenarios").status_code == 200


def test_write_token_constant_time_compare(tmp_path):
    """Finding #4 (2026-07-07): the token check uses `secrets.compare_digest`,
    not `!=`. Exercise the dependency directly — the exact comparison
    mechanism isn't observable through the HTTP outcome (both a fast `!=` and
    a constant-time compare produce the same 401/pass), so this asserts the
    accept/reject *decision* is unchanged across several near-miss tokens,
    including ones a length/prefix-revealing timing side-channel would help
    an attacker find (shorter, and a right-length-wrong-content guess)."""
    from fastapi import HTTPException

    from axrange.api.app import _require_write_token

    from types import SimpleNamespace

    settings = _settings(tmp_path)
    settings.api_token = "s3cret"
    dep = _require_write_token(settings)  # the A8 dispatcher, in token mode

    def _request(authorization):
        headers = {} if authorization is None else {"authorization": authorization}
        return SimpleNamespace(client=SimpleNamespace(host="10.0.0.5"), headers=headers, state=SimpleNamespace())

    dep(_request("Bearer s3cret"))  # correct -> no exception

    for bad in (None, "", "Bearer", "Bearer wrong", "Bearer s3cre", "Bearer s3crets"):
        with pytest.raises(HTTPException) as exc_info:
            dep(_request(bad))
        assert exc_info.value.status_code == 401


# ---------------------------------------------------------------------------
# O2 surface (A1, docs/design/05-o2-surface.md) — POST/GET/DELETE
# /api/o2/surface, a thin DTO wrapper over axrange.o2control. Uses the SAME
# tier-aware fake adapter/teardown as tests/test_o2control.py so the created
# resource names are genuinely o2-shaped, not canned te-shaped strings.
# ---------------------------------------------------------------------------
def _o2_client(settings):
    app = create_app(settings)
    adapter = _TierAwareFakeAdapter()
    app.state.o2_adapter = adapter
    app.state.o2_config_backend = adapter
    return TestClient(app), adapter


def test_o2_surface_status_not_deployed(tmp_path):
    c = _client(_settings(tmp_path))
    r = c.get("/api/o2/surface")
    assert r.status_code == 200
    assert r.json() == {"deploy_id": None, "status": "not-deployed", "sensors": []}


def test_o2_surface_up_and_status(tmp_path):
    c, adapter = _o2_client(_settings(tmp_path))
    r = c.post("/api/o2/surface", json={"target_node": "pxtest1"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "completed"
    assert body["deploy_id"]
    assert body["sensors"] == [{
        "sensor_id": "o2-sensor", "type": "tcpdump-sensor",
        "transport_ip": "10.255.250.10", "deploy_id": body["deploy_id"],
    }]

    r2 = c.get("/api/o2/surface")
    assert r2.status_code == 200
    assert r2.json() == body

    # idempotent re-up: same deploy_id, no new adapter calls.
    calls_before = len(adapter.calls)
    r3 = c.post("/api/o2/surface", json={"target_node": "pxtest1"})
    assert r3.json()["deploy_id"] == body["deploy_id"]
    assert len(adapter.calls) == calls_before


def test_o2_surface_status_refuses_a_te_tier_deploy_id(tmp_path):
    """`GET /api/o2/surface?deploy_id=<te-id>` must 400, not silently render a
    twin as an O2 surface with `sensors: []` -- completes the tier guard that
    `DELETE /api/o2/surface` already enforces (`o2_surface_down`) on the read
    path (`o2_surface_status`)."""
    settings = _settings(tmp_path)
    c = _client(settings)

    store = StateStore(settings.db_path)
    try:
        store.create_deploy(
            deploy_id="te-dep-1", scenario_id="sc", scenario_version=1,
            scenario_schema_version=1, plan_id="plan-1", ir_hash="h",
            placement={}, tier="te",
        )
    finally:
        store.close()

    r = c.get("/api/o2/surface", params={"deploy_id": "te-dep-1"})
    assert r.status_code == 400
    assert "not an O2 surface" in r.json()["error"]


def test_o2_surface_down_requires_allow(tmp_path):
    c, _adapter = _o2_client(_settings(tmp_path))
    c.post("/api/o2/surface", json={"target_node": "pxtest1"})
    r = c.request("DELETE", "/api/o2/surface")
    assert r.status_code == 400


def test_o2_surface_down_removes_only_the_surface(tmp_path, manifests):
    """The keystone API case: `DELETE /api/o2/surface` tears down the o2
    deploy via a genuine tier-aware sweep, leaving an unrelated `te`-tier
    deploy in the same state.db untouched (`teardown._protected()`)."""
    from axrange.o2control.compose import compose_surface_ir
    from axrange.o2control.manifest import default_surface_manifest
    from axrange.ir.parser import parse_ir
    from axrange.planner import build_plan
    from axrange.planner.cluster import ClusterContext
    from axrange.planner.state import DeployStatus

    settings = _settings(tmp_path)
    c, adapter = _o2_client(settings)
    up = c.post("/api/o2/surface", json={"target_node": "pxtest1"}).json()

    # Seed an unrelated te-tier deploy directly in the same state.db.
    store = StateStore(settings.db_path)
    try:
        store.create_deploy(
            deploy_id="te-unrelated-1", scenario_id="unrelated", scenario_version=1,
            scenario_schema_version=1, plan_id="plan-unrelated", ir_hash="h",
            placement={}, tier="te",
        )
    finally:
        store.close()

    raw = compose_surface_ir(default_surface_manifest())
    parsed = parse_ir(text=_yaml_dump(raw))
    ctx = ClusterContext.single_node("pxtest1")
    plan = build_plan(parsed.ir, manifests, ctx, raw_ir=parsed.raw, tier="o2")

    app2 = create_app(settings)
    teardown_backend = _TierAwareFakeTeardown(adapter, plan)
    app2.state.o2_teardown_backend = teardown_backend
    c2 = TestClient(app2)

    r = c2.request("DELETE", "/api/o2/surface", params={"allow_destroy": "true"})
    assert r.status_code == 200
    body = r.json()
    assert body["destroyed"] is True
    assert body["deploy_id"] == up["deploy_id"]

    # The unrelated te deploy is untouched.
    store = StateStore(settings.db_path)
    try:
        te = store.get_deploy("te-unrelated-1")
    finally:
        store.close()
    assert te.status == DeployStatus.RUNNING


def _yaml_dump(raw: dict) -> str:
    import io

    from ruamel.yaml import YAML

    buf = io.StringIO()
    YAML(typ="safe").dump(raw, buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# O2 taps (A2, docs/design/06-taps-sensors.md) -- POST/DELETE /api/taps, a
# thin DTO wrapper over axrange.o2control.taps. Reuses the seeding helpers
# and fakes from tests/test_o2_taps.py (same idiom as the O2-surface section
# above reusing tests/test_o2control.py's fakes).
# ---------------------------------------------------------------------------
def _tap_client(settings, adapter=None):
    from tests.test_o2_taps import _FakeMirAdapter

    app = create_app(settings)
    adapter = adapter or _FakeMirAdapter()
    app.state.tap_adapter = adapter
    app.state.tap_config_backend = adapter
    return TestClient(app), adapter


def test_o2_tap_apply_and_get_via_status_shape(tmp_path):
    from tests.test_o2_taps import DEFAULT_SENSORS, TE_DEPLOY, _seed_o2_surface, _seed_te

    settings = _settings(tmp_path)
    store = StateStore(settings.db_path)
    try:
        _seed_te(store)
        _seed_o2_surface(store, sensors=DEFAULT_SENSORS)
    finally:
        store.close()

    c, adapter = _tap_client(settings)
    r = c.post(
        "/api/taps",
        json={
            "target": TE_DEPLOY,
            "bindings": [{"tap": "corp", "sensors": ["pktxpress-1", "suricata-1"]}],
            "target_node": "pxtest1",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "completed"
    assert body["target"] == TE_DEPLOY
    # `docs/WORK_ORDER_tap_points.md`: `TapBindingDTO` gained `tap_point`/
    # `selector`/`mode` -- always `None`/`None`/"fanout" on a RESPONSE built
    # from `TapBindingResult` (a status summary, not a request echo).
    # `direction` (`docs/WORK_ORDER_tap_scopes.md` M4/M6): `tap: corp`
    # resolved via the NET-id branch (`_resolve_tap_target`'s net_hits),
    # segment-like semantics -- but `_seed_te`'s `corp` has only ONE tapped
    # host (h1), so fix round S8 promotes rx to "both" (a lone port cannot
    # self-duplicate).
    assert body["bindings"] == [
        {
            "tap": "corp", "tap_point": None, "selector": None,
            "sensors": ["pktxpress-1", "suricata-1"], "mode": "fanout",
            "direction": "both",
        }
    ]

    gre_calls = [c2 for c2 in adapter.calls if c2[0] == "attach-gre-tunnel"]
    assert len(gre_calls) == 2

    # Idempotent re-apply: same deploy_id, and the reasserted mirror content
    # is unchanged (a cluster no-op) even though a new adapter call DID
    # happen -- install-ovs-mirror is force-reset on every apply so a
    # narrow/revert is correctly re-asserted, not silently skipped (M1,
    # docs/design/06-taps-sensors.md; see tests/test_o2_taps.py for the
    # focused narrow-revert regression test).
    mirror_calls_before = [c2 for c2 in adapter.calls if c2[0] == "install-ovs-mirror"]
    r2 = c.post(
        "/api/taps",
        json={
            "target": TE_DEPLOY,
            "bindings": [{"tap": "corp", "sensors": ["pktxpress-1", "suricata-1"]}],
            "target_node": "pxtest1",
        },
    )
    assert r2.json()["deploy_id"] == body["deploy_id"]
    mirror_calls_after = [c2 for c2 in adapter.calls if c2[0] == "install-ovs-mirror"]
    assert len(mirror_calls_after) == len(mirror_calls_before) + 1
    assert mirror_calls_after[-1][1]["tapped_nics"] == mirror_calls_before[-1][1]["tapped_nics"]


def _seed_and_apply_tap(settings, tmp_path):
    """Seed te + surface and apply one tap plan; return the client + TE id."""
    from tests.test_o2_taps import DEFAULT_SENSORS, TE_DEPLOY, _seed_o2_surface, _seed_te

    store = StateStore(settings.db_path)
    try:
        _seed_te(store)
        _seed_o2_surface(store, sensors=DEFAULT_SENSORS)
    finally:
        store.close()
    c, _ = _tap_client(settings)
    r = c.post(
        "/api/taps",
        json={
            "target": TE_DEPLOY,
            "bindings": [{"tap": "corp", "sensors": ["pktxpress-1", "suricata-1"]}],
            "target_node": "pxtest1",
        },
    )
    assert r.status_code == 200
    return c, TE_DEPLOY


def test_o2_tap_get_concise_and_verbose_by_twin_id(tmp_path):
    """GET /api/taps read peer: `?target=<twin>` resolves the applied tap
    deploy; concise returns the status shape, `?verbose=true` returns the rich
    TapInfoDTO (per-binding sensors + fanout_note)."""
    settings = _settings(tmp_path)
    c, te = _seed_and_apply_tap(settings, tmp_path)

    # concise (default) -- TapPlanStatusDTO shape
    rc = c.get("/api/taps", params={"target": te})
    assert rc.status_code == 200
    b = rc.json()
    assert b["target"] == te
    assert b["status"] == "completed"
    assert b["bindings"] and b["bindings"][0]["tap"] == "corp"
    assert "fanout_note" not in b          # concise shape has no fanout_note

    # verbose -- TapInfoDTO shape
    rv = c.get("/api/taps", params={"target": te, "verbose": "true"})
    assert rv.status_code == 200
    v = rv.json()
    assert v["target"] == te
    assert v["status"] == "completed"
    assert v["fanout_note"]                # verbose carries the capacity note
    assert v["secrets_revealed"] is False
    assert v["bindings"]
    binding = v["bindings"][0]
    assert {"host", "net_id", "nic_index", "sensors"} <= set(binding)
    assert set(binding["sensors"]) == {"pktxpress-1", "suricata-1"}


def test_o2_tap_get_verbose_masks_external_sensor_password(tmp_path):
    """Verbose view carries resolved external-sensor access facts, MASKED by
    default; `?secrets=true` requires the bearer token when one is configured
    (LES-134), and reveals the real password only then."""
    conf = tmp_path / "adapter.conf"
    conf.write_text(
        'EXTERNAL_SENSORS=[{"id":"XPress-SW","vmid":250,'
        '"transport_ip":"10.255.250.20"}]\n'
    )
    settings = _settings(tmp_path, adapter_config=str(conf))
    # Apply the tap FIRST (a write, open in `none` mode), then a second app
    # over the same store WITH the token: the write policy is resolved once,
    # at `create_app` (A8), so it gates only the `?secrets=true` read below.
    _c0, te = _seed_and_apply_tap(settings, tmp_path)
    settings.api_token = "s3cret"
    c, _ = _tap_client(settings)

    # masked by default
    rv = c.get("/api/taps", params={"target": te, "verbose": "true"})
    assert rv.status_code == 200
    ext = rv.json()["external_sensors"]
    assert ext and ext[0]["id"] == "XPress-SW"
    assert ext[0]["access_password"] == "********"

    # ?secrets=true without a token -> 401
    r401 = c.get("/api/taps", params={"target": te, "verbose": "true", "secrets": "true"})
    assert r401.status_code == 401

    # ?secrets=true WITH the token -> revealed
    rok = c.get(
        "/api/taps",
        params={"target": te, "verbose": "true", "secrets": "true"},
        headers={"Authorization": "Bearer s3cret"},
    )
    assert rok.status_code == 200
    body = rok.json()
    assert body["secrets_revealed"] is True
    assert body["external_sensors"][0]["access_password"] != "********"


def test_o2_tap_apply_unknown_target_is_404(tmp_path):
    from tests.test_o2_taps import DEFAULT_SENSORS, _seed_o2_surface

    settings = _settings(tmp_path)
    store = StateStore(settings.db_path)
    try:
        _seed_o2_surface(store, sensors=DEFAULT_SENSORS)
    finally:
        store.close()

    c, _adapter = _tap_client(settings)
    r = c.post(
        "/api/taps",
        json={
            "target": "no-such-te",
            "bindings": [{"tap": "corp", "sensors": ["pktxpress-1"]}],
        },
    )
    assert r.status_code == 404


def test_o2_tap_apply_no_target_in_body_is_400(tmp_path):
    """`docs/WORK_ORDER_tap_target_resolution.md` §"API parity": `target`
    is optional on the request body, but the deploy path still requires an
    EFFECTIVE one -- 400s if absent, same posture as the CLI's `tap deploy`
    with no `target:`/`--target` anywhere."""
    settings = _settings(tmp_path)
    c, _adapter = _tap_client(settings)
    r = c.post(
        "/api/taps",
        json={"bindings": [{"tap": "corp", "sensors": ["pktxpress-1"]}]},
    )
    assert r.status_code == 400
    assert "no tap-plan target" in r.json()["error"]


def test_o2_tap_apply_resolves_scenario_name_in_body_target(tmp_path):
    """The request body's `target` can be a scenario NAME, not just a
    literal deploy id -- resolved to the newest live `te` deploy of that
    scenario (`resolve_target_deploy`), exactly like the CLI."""
    from tests.test_o2_taps import DEFAULT_SENSORS, TE_DEPLOY, _seed_o2_surface, _seed_te

    settings = _settings(tmp_path)
    store = StateStore(settings.db_path)
    try:
        _seed_te(store)  # scenario_id="te-scn"
        _seed_o2_surface(store, sensors=DEFAULT_SENSORS)
    finally:
        store.close()

    c, _adapter = _tap_client(settings)
    r = c.post(
        "/api/taps",
        json={
            "target": "te-scn",
            "bindings": [{"tap": "corp", "sensors": ["pktxpress-1"]}],
            "target_node": "pxtest1",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "completed"
    assert body["target"] == TE_DEPLOY  # resolved to the CONCRETE deploy id


# ---------------------------------------------------------------------------
# Tap-point addressing over HTTP (`docs/WORK_ORDER_tap_points.md`).
# ---------------------------------------------------------------------------
def test_o2_tap_apply_via_tap_point_over_http(tmp_path, manifests):
    """`tap_point:`/`selector:` addressing works end-to-end through the
    actual DTO/JSON boundary, not just the internal `TapBindingSpec` path
    the o2_taps-module-level tests exercise."""
    from tests.test_o2_taps import _seed_tp_te

    db, te_id = _seed_tp_te(tmp_path, manifests)
    assert db == str(tmp_path / "state.db")  # _seed_tp_te and _settings must agree
    settings = _settings(tmp_path)
    c, adapter = _tap_client(settings)
    r = c.post(
        "/api/taps",
        json={
            "target": te_id,
            "bindings": [
                {
                    "selector": {"scope": "boundary"}, "sensors": ["pktxpress-1"],
                    "mode": "exclusive",
                },
            ],
            "target_node": "px1",
        },
    )
    assert r.status_code == 200
    assert r.json()["status"] == "completed"
    mirror_calls = [c2 for c2 in adapter.calls if c2[0] == "install-ovs-mirror"]
    assert len(mirror_calls) == 1  # only the boundary-joining host (r-edge)


def test_o2_tap_apply_with_no_addressing_field_is_rejected_not_select_everything(
    tmp_path, manifests,
):
    """M1 FIXED (`docs/WORK_ORDER_tap_points.md` code review):
    `o2_tap_apply` now routes the request through `parse_tap_plan` (the
    exact same validation a file-loaded tap-plan goes through, LES-037)
    instead of building `TapBindingSpec` directly from the DTO -- a binding
    with NONE of `tap`/`tap_point`/`selector` set is now a 400, not a
    silent 200 that binds EVERY derived tap point in the twin to whatever
    sensors were given."""
    from tests.test_o2_taps import _seed_tp_te

    db, te_id = _seed_tp_te(tmp_path, manifests)
    assert db == str(tmp_path / "state.db")  # _seed_tp_te and _settings must agree
    settings = _settings(tmp_path)
    c, adapter = _tap_client(settings)
    r = c.post(
        "/api/taps",
        json={
            "target": te_id,
            # Deliberately no "tap"/"tap_point"/"selector" key at all.
            "bindings": [{"sensors": ["pktxpress-1"]}],
            "target_node": "px1",
        },
    )
    assert r.status_code == 400, (
        f"expected a 400 rejecting the unaddressed binding, got "
        f"{r.status_code}: {r.text}"
    )
    assert "exactly one of" in r.text
    # And NOTHING was bound -- the fake adapter never even ran.
    assert adapter.calls == []


def test_o2_tap_destroy_requires_allow(tmp_path):
    from tests.test_o2_taps import DEFAULT_SENSORS, TE_DEPLOY, _seed_o2_surface, _seed_te

    settings = _settings(tmp_path)
    store = StateStore(settings.db_path)
    try:
        _seed_te(store)
        _seed_o2_surface(store, sensors=DEFAULT_SENSORS)
    finally:
        store.close()

    c, _adapter = _tap_client(settings)
    c.post(
        "/api/taps",
        json={
            "target": TE_DEPLOY,
            "bindings": [{"tap": "corp", "sensors": ["pktxpress-1"]}],
            "target_node": "pxtest1",
        },
    )
    r = c.request("DELETE", "/api/taps", params={"target": TE_DEPLOY})
    assert r.status_code == 400


def test_o2_tap_destroy_removes_only_tap_resources(tmp_path):
    """The keystone API case: `DELETE /api/taps` tears down only the
    tap-plan's own gretaps, leaving the target TE and the O2 surface deploy
    rows in the same state.db untouched."""
    from tests.test_o2_taps import (
        DEFAULT_SENSORS, O2_DEPLOY, TE_DEPLOY, _FakeMirAdapter, _FakeTeardown,
        _seed_o2_surface, _seed_te,
    )

    settings = _settings(tmp_path)
    store = StateStore(settings.db_path)
    try:
        _seed_te(store)
        _seed_o2_surface(store, sensors=DEFAULT_SENSORS)
    finally:
        store.close()

    c, _adapter = _tap_client(settings)
    up = c.post(
        "/api/taps",
        json={
            "target": TE_DEPLOY,
            "bindings": [{"tap": "corp", "sensors": ["pktxpress-1"]}],
            "target_node": "pxtest1",
        },
    ).json()

    app2 = create_app(settings)
    teardown_backend = _FakeTeardown()
    app2.state.tap_teardown_backend = teardown_backend
    app2.state.tap_adapter = _FakeMirAdapter()
    c2 = TestClient(app2)

    r = c2.request(
        "DELETE", "/api/taps",
        params={"allow_destroy": "true", "target": TE_DEPLOY},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["destroyed"] is True
    assert body["deploy_id"] == up["deploy_id"]
    assert not any(k in ("vm", "vnet", "zone") for k, _t in teardown_backend.calls)

    store = StateStore(settings.db_path)
    try:
        te = store.get_deploy(TE_DEPLOY)
        o2 = store.get_deploy(O2_DEPLOY)
    finally:
        store.close()
    assert te.status.value != "destroyed"
    assert o2.status.value != "destroyed"


def test_destroy_deploy_cascades_to_standalone_tap_plan(tmp_path):
    """S3: `DELETE /api/deploys/{te_id}` also sweeps a standalone tap-plan
    bound to that TE (docs/design/06-taps-sensors.md), via
    `axrange.o2control.taps.cascade_destroy_for_te`."""
    from tests.test_o2_taps import (
        DEFAULT_SENSORS, TE_DEPLOY, _FakeMirAdapter, _seed_o2_surface, _seed_te,
    )

    settings = _settings(tmp_path)
    store = StateStore(settings.db_path)
    try:
        _seed_te(store, deploy_id=TE_DEPLOY)
        _seed_o2_surface(store, sensors=DEFAULT_SENSORS)
    finally:
        store.close()

    tap_c, _tap_adapter = _tap_client(settings)
    up = tap_c.post(
        "/api/taps",
        json={
            "target": TE_DEPLOY,
            "bindings": [{"tap": "corp", "sensors": ["pktxpress-1"]}],
            "target_node": "pxtest1",
        },
    ).json()
    assert up["status"] == "completed"

    app2 = create_app(settings)
    app2.state.teardown_factory = lambda _s: _NoopTeTeardown()
    app2.state.tap_adapter = _FakeMirAdapter()
    c2 = TestClient(app2)

    r = c2.request(
        "DELETE", f"/api/deploys/{TE_DEPLOY}", params={"allow_destroy": "true"}
    )
    assert r.status_code == 200

    store = StateStore(settings.db_path)
    try:
        tap_dep = store.get_deploy(up["deploy_id"])
    finally:
        store.close()
    assert tap_dep.status.value == "destroyed"


def test_destroy_deploy_cascades_before_sweeping_the_twins_own_vms(tmp_path):
    """LES-171, service-layer counterpart to the CLI test in test_cli.py: the
    sibling cascades must run BEFORE the twin's own sweep, since each binds
    into the twin's LIVE VMs (the tap tier's de-scope re-asserts a per-VM
    hookscript; the traffic tier's persona stop is a QGA call into the guest).
    Those steps are best-effort, so a wrong order does not fail the destroy --
    only the ORDER is assertable."""
    from tests.test_o2_taps import (
        DEFAULT_SENSORS, TE_DEPLOY, _FakeMirAdapter, _seed_o2_surface, _seed_te,
    )

    settings = _settings(tmp_path)
    store = StateStore(settings.db_path)
    try:
        _seed_te(store, deploy_id=TE_DEPLOY)
        _seed_o2_surface(store, sensors=DEFAULT_SENSORS)
    finally:
        store.close()

    tap_c, _tap_adapter = _tap_client(settings)
    up = tap_c.post(
        "/api/taps",
        json={
            "target": TE_DEPLOY,
            "bindings": [{"tap": "corp", "sensors": ["pktxpress-1"]}],
            "target_node": "pxtest1",
        },
    ).json()
    assert up["status"] == "completed"

    class _RecordingTeardown(_NoopTeTeardown):
        """Same no-op backend, but records an ordered call trace. The SAME
        instance serves both the cascade and the twin's own sweep, so this is
        one timeline across the two phases."""

        def __init__(self):
            self.calls: list[str] = []

        def destroy_vm(self, node, vmid):
            self.calls.append("vm")
            return "removed"

        def delete_gretap(self, dev):
            self.calls.append("gretap")
            return "removed"

    backend = _RecordingTeardown()
    app2 = create_app(settings)
    app2.state.teardown_factory = lambda _s: backend
    app2.state.tap_adapter = _FakeMirAdapter()
    c2 = TestClient(app2)

    r = c2.request(
        "DELETE", f"/api/deploys/{TE_DEPLOY}", params={"allow_destroy": "true"}
    )
    assert r.status_code == 200
    assert r.json()["cascade_errors"] == []
    assert "gretap" in backend.calls and "vm" in backend.calls
    assert backend.calls.index("gretap") < backend.calls.index("vm"), (
        "tap cascade must complete before the twin's VMs are purged; "
        f"got call order {backend.calls}"
    )


class _NoopTeTeardown:
    """A minimal `TeardownBackend` for the seeded TE (which carries no
    create-vnet/create-sdn-zone/create-ovs-bridge outputs -- `_seed_te` in
    tests/test_o2_taps.py only records clone-vm/install-ovs-mirror/
    verify-capture), so the TE's own `DELETE /api/deploys/{id}` sweep has
    nothing real to remove -- the point of this test is the CASCADE, not the
    TE's own teardown mechanics (covered elsewhere)."""

    def vm_name(self, node, vmid):
        return None

    def destroy_vm(self, node, vmid):
        return "removed"

    def delete_mirror_snippets(self, node, vmid):
        return "removed"

    def delete_gretap(self, dev):
        return "removed"

    def delete_vnet(self, vnet_id):
        return "removed"

    def delete_sdn_zone(self, zone_id):
        return "removed"

    def apply_sdn(self):
        return "removed"

    def delete_ovs_bridge(self, node, b):
        return "removed"


# ---------------------------------------------------------------------------
# tfc (the Traffic tier, docs/WORK_ORDER_traffic_tier.md) -- POST/GET/DELETE
# /api/tfc, a thin DTO wrapper over axrange.o2control.traffic. Reuses the
# seeding helpers/fakes from tests/test_traffic_tier.py.
# ---------------------------------------------------------------------------
def _tfc_client(settings, persona_backend=None):
    from tests.test_traffic_tier import _FakePersonaBackend

    app = create_app(settings)
    persona_backend = persona_backend or _FakePersonaBackend()
    app.state.tfc_persona_backend = persona_backend
    return TestClient(app), persona_backend


def test_tfc_apply_and_get_via_status_shape(tmp_path):
    from tests.test_traffic_tier import TWIN_DEPLOY, _seed_twin

    settings = _settings(tmp_path)
    store = StateStore(settings.db_path)
    try:
        _seed_twin(store, deploy_id=TWIN_DEPLOY, hosts=[
            {"host_id": "ws01", "role": "corp-workstation", "vmid": 100001, "ip": "10.0.0.21"},
            {"host_id": "ws02", "role": "corp-workstation", "vmid": 100002, "ip": "10.0.0.22"},
            {"host_id": "dc01", "role": "domain-controller", "vmid": 100003, "ip": "10.0.0.5"},
        ])
    finally:
        store.close()

    c, backend = _tfc_client(settings)
    r = c.post(
        "/api/tfc",
        json={
            "twin": TWIN_DEPLOY,
            "assignments": [{"node": "dc01", "persona": "domain-controller"}],
            "target_node": "pxtest1",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "completed"
    assert body["twin"] == TWIN_DEPLOY
    assert len(backend.calls) == 1

    r2 = c.get("/api/tfc", params={"target": TWIN_DEPLOY})
    assert r2.status_code == 200
    assert r2.json()["deploy_id"] == body["deploy_id"]

    # Idempotent re-apply: same deploy_id, force-reset re-asserts (a second
    # backend call happened, byte-identical content).
    r3 = c.post(
        "/api/tfc",
        json={
            "twin": TWIN_DEPLOY,
            "assignments": [{"node": "dc01", "persona": "domain-controller"}],
            "target_node": "pxtest1",
        },
    )
    assert r3.json()["deploy_id"] == body["deploy_id"]
    assert len(backend.calls) == 2
    assert backend.calls[0] == backend.calls[1]


def test_tfc_apply_unknown_target_is_404(tmp_path):
    settings = _settings(tmp_path)
    c, _backend = _tfc_client(settings)
    r = c.post(
        "/api/tfc",
        json={
            "twin": "no-such-twin",
            "assignments": [{"node": "dc01", "persona": "domain-controller"}],
        },
    )
    assert r.status_code == 404


def test_tfc_destroy_requires_allow(tmp_path):
    from tests.test_traffic_tier import TWIN_DEPLOY, _seed_twin

    settings = _settings(tmp_path)
    store = StateStore(settings.db_path)
    try:
        _seed_twin(store, deploy_id=TWIN_DEPLOY, hosts=[
            {"host_id": "dc01", "role": "domain-controller", "vmid": 100003, "ip": "10.0.0.5"},
            {"host_id": "ws01", "role": "corp-workstation", "vmid": 100001, "ip": "10.0.0.21"},
        ])
    finally:
        store.close()

    c, _backend = _tfc_client(settings)
    c.post(
        "/api/tfc",
        json={
            "twin": TWIN_DEPLOY,
            "assignments": [{"node": "dc01", "persona": "domain-controller"}],
            "target_node": "pxtest1",
        },
    )
    r = c.request("DELETE", "/api/tfc", params={"target": TWIN_DEPLOY})
    assert r.status_code == 400


def test_tfc_destroy_removes_only_tfc_resources(tmp_path):
    """The keystone API case: `DELETE /api/tfc` tears down only the traffic
    profile's own deploy, leaving the target twin's deploy row untouched."""
    from tests.test_traffic_tier import TWIN_DEPLOY, _FakePersonaBackend, _seed_twin

    settings = _settings(tmp_path)
    store = StateStore(settings.db_path)
    try:
        _seed_twin(store, deploy_id=TWIN_DEPLOY, hosts=[
            {"host_id": "dc01", "role": "domain-controller", "vmid": 100003, "ip": "10.0.0.5"},
            {"host_id": "ws01", "role": "corp-workstation", "vmid": 100001, "ip": "10.0.0.21"},
        ])
    finally:
        store.close()

    c, _backend = _tfc_client(settings)
    up = c.post(
        "/api/tfc",
        json={
            "twin": TWIN_DEPLOY,
            "assignments": [{"node": "dc01", "persona": "domain-controller"}],
            "target_node": "pxtest1",
        },
    ).json()

    app2 = create_app(settings)
    app2.state.tfc_persona_backend = _FakePersonaBackend()
    c2 = TestClient(app2)
    r = c2.request(
        "DELETE", "/api/tfc", params={"allow_destroy": "true", "target": TWIN_DEPLOY},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["destroyed"] is True
    assert body["deploy_id"] == up["deploy_id"]

    store = StateStore(settings.db_path)
    try:
        twin = store.get_deploy(TWIN_DEPLOY)
    finally:
        store.close()
    assert twin.status.value != "destroyed"


def test_destroy_deploy_cascades_to_standalone_traffic_profile(tmp_path):
    """S3-style cascade (`docs/WORK_ORDER_traffic_tier.md`): `DELETE
    /api/deploys/{twin_id}` also sweeps a standalone traffic-profile deploy
    bound to that twin, via `axrange.o2control.traffic.
    cascade_destroy_tfc_for_te`."""
    from tests.test_traffic_tier import TWIN_DEPLOY, _FakePersonaBackend, _seed_twin

    settings = _settings(tmp_path)
    store = StateStore(settings.db_path)
    try:
        _seed_twin(store, deploy_id=TWIN_DEPLOY, hosts=[
            {"host_id": "dc01", "role": "domain-controller", "vmid": 100003, "ip": "10.0.0.5"},
            {"host_id": "ws01", "role": "corp-workstation", "vmid": 100001, "ip": "10.0.0.21"},
        ])
    finally:
        store.close()

    tfc_c, _tfc_backend = _tfc_client(settings)
    up = tfc_c.post(
        "/api/tfc",
        json={
            "twin": TWIN_DEPLOY,
            "assignments": [{"node": "dc01", "persona": "domain-controller"}],
            "target_node": "pxtest1",
        },
    ).json()
    assert up["status"] == "completed"

    app2 = create_app(settings)
    app2.state.teardown_factory = lambda _s: _NoopTeTeardown()
    app2.state.tfc_persona_backend = _FakePersonaBackend()
    c2 = TestClient(app2)

    r = c2.request(
        "DELETE", f"/api/deploys/{TWIN_DEPLOY}", params={"allow_destroy": "true"}
    )
    assert r.status_code == 200

    store = StateStore(settings.db_path)
    try:
        tfc_dep = store.get_deploy(up["deploy_id"])
    finally:
        store.close()
    assert tfc_dep.status.value == "destroyed"


def test_destroying_a_twin_brings_down_its_standing_tfc_and_tap_and_releases_the_sensor_binding(tmp_path):
    """The console's Destroy (2026-11-08): `DELETE /api/deploys/{te}` on a twin
    with a traffic profile AND a tap plan standing removes all three, the tap's
    gretaps to its sensor among what comes down, and the response says so per
    tier, children first, the twin last."""
    from tests.test_o2_taps import DEFAULT_SENSORS, TE_DEPLOY, _FakeMirAdapter, _op_row, _seed_o2_surface
    from tests.test_traffic_tier import _FakePersonaBackend, _seed_twin

    settings = _settings(tmp_path)
    store = StateStore(settings.db_path)
    try:
        _seed_twin(store, deploy_id=TE_DEPLOY, hosts=[
            {"host_id": "dc01", "role": "domain-controller", "vmid": 100003, "ip": "10.0.0.5", "net_id": "corp"},
            {"host_id": "ws01", "role": "corp-workstation", "vmid": 100001, "ip": "10.0.0.21", "net_id": "corp"},
        ])
        store.insert_ops(TE_DEPLOY, [_op_row(TE_DEPLOY, "op-br-corp", "create-ovs-bridge", "OP-NET-020",
                                             {"net": "corp"}, outputs={"bridge_name": "br-corp"})])
        _seed_o2_surface(store, sensors=DEFAULT_SENSORS)
    finally:
        store.close()

    tfc_c, _ = _tfc_client(settings)
    tfc = tfc_c.post("/api/tfc", json={
        "twin": TE_DEPLOY, "assignments": [{"node": "dc01", "persona": "domain-controller"}],
        "target_node": "pxtest1",
    }).json()
    assert tfc.get("status") == "completed", tfc
    tap_c, _ = _tap_client(settings)
    tap = tap_c.post("/api/taps", json={
        "target": TE_DEPLOY, "bindings": [{"tap": "corp", "sensors": ["pktxpress-1"]}],
        "target_node": "pxtest1",
    }).json()
    assert tap.get("status") == "completed", tap

    store = StateStore(settings.db_path)
    try:
        gre_before = [o for o in store.ops_for_deploy(tap["deploy_id"]) if o.op_name.startswith("attach-gre")]
    finally:
        store.close()
    assert gre_before, "the tap plan bound the sensor with a gretap"

    app2 = create_app(settings)
    app2.state.teardown_factory = lambda _s: _NoopTeTeardown()
    app2.state.tap_adapter = _FakeMirAdapter()
    app2.state.tfc_persona_backend = _FakePersonaBackend()
    r = TestClient(app2).request("DELETE", f"/api/deploys/{TE_DEPLOY}", params={"allow_destroy": "true"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["cascade_errors"] == []

    store = StateStore(settings.db_path)
    try:
        for did in (TE_DEPLOY, tfc["deploy_id"], tap["deploy_id"]):
            assert store.get_deploy(did).status.value == "destroyed", did
        gre_after = [o for o in store.ops_for_deploy(tap["deploy_id"]) if o.op_name.startswith("attach-gre")]
    finally:
        store.close()
    assert gre_after and all(o.status.value == "destroyed" for o in gre_after), "the sensor binding is released"

    tiers = [(t["tier"], t["deploy_id"], t["destroyed"]) for t in body["tiers"]]
    assert tiers == [("exr", None, False), ("tfc", tfc["deploy_id"], True),
                     ("tap", tap["deploy_id"], True), ("te", TE_DEPLOY, True)]
    tap_row = next(t for t in body["tiers"] if t["tier"] == "tap")
    assert any(x.startswith("gretap ") for x in tap_row["released"]), tap_row


# ---------------------------------------------------------------------------
# exr (the Exercise tier, docs/WORK_ORDER_exercise_tier.md) -- POST/GET/DELETE
# /api/exr, a thin DTO wrapper over axrange.o2control.exercise. Reuses the
# seeding helpers/fakes from tests/test_exercise.py.
# ---------------------------------------------------------------------------
def _exr_client(settings, config_backend=None):
    from tests.test_exercise import _FakeConfigBackend

    app = create_app(settings)
    config_backend = config_backend or _FakeConfigBackend()
    app.state.exr_config_backend = config_backend
    return TestClient(app), config_backend


_EXR_ACTIVITY_BODY = {
    "id": "ad-kill-chain", "host": "attacker",
    "inline_steps": [{"tool": "nmap", "args": "-sV", "target": "10.0.0.0/24"}],
}


def test_exr_apply_and_get_via_status_shape(tmp_path):
    from tests.test_exercise import TWIN_DEPLOY, _seed_twin

    settings = _settings(tmp_path)
    store = StateStore(settings.db_path)
    try:
        _seed_twin(store, deploy_id=TWIN_DEPLOY)
    finally:
        store.close()

    c, backend = _exr_client(settings)
    r = c.post(
        "/api/exr",
        json={"twin": TWIN_DEPLOY, "activity": _EXR_ACTIVITY_BODY, "target_node": "pxtest1"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "completed"
    assert body["twin"] == TWIN_DEPLOY
    assert body["activity_host"] == "attacker"
    assert body["playout_hash"]

    r2 = c.get("/api/exr", params={"deploy_id": body["deploy_id"]})
    assert r2.status_code == 200
    assert r2.json()["deploy_id"] == body["deploy_id"]

    # A second, same-host apply while run1 is still live is refused (S2, code
    # review -- the in-guest agent handle is host-scoped, not deploy-scoped).
    r_conflict = c.post(
        "/api/exr",
        json={"twin": TWIN_DEPLOY, "activity": _EXR_ACTIVITY_BODY, "target_node": "pxtest1"},
    )
    assert r_conflict.status_code == 409

    # The real replay loop: destroy run1 first, THEN re-apply -- a FRESH,
    # independent deploy_id (ephemeral, unlike tfc's converge-in-place) that
    # hashes IDENTICALLY to run1 -- the replay assertion.
    d = c.request(
        "DELETE", "/api/exr",
        params={"allow_destroy": "true", "deploy_id": body["deploy_id"]},
    )
    assert d.status_code == 200

    r3 = c.post(
        "/api/exr",
        json={"twin": TWIN_DEPLOY, "activity": _EXR_ACTIVITY_BODY, "target_node": "pxtest1"},
    )
    body3 = r3.json()
    assert body3["deploy_id"] != body["deploy_id"]
    assert body3["playout_hash"] == body["playout_hash"]


def test_exr_apply_unknown_twin_is_404(tmp_path):
    settings = _settings(tmp_path)
    c, _backend = _exr_client(settings)
    r = c.post(
        "/api/exr",
        json={"twin": "no-such-twin", "activity": _EXR_ACTIVITY_BODY},
    )
    assert r.status_code == 404


def test_exr_apply_requires_at_least_one_attack_mode(tmp_path):
    from tests.test_exercise import TWIN_DEPLOY, _seed_twin

    settings = _settings(tmp_path)
    store = StateStore(settings.db_path)
    try:
        _seed_twin(store, deploy_id=TWIN_DEPLOY)
    finally:
        store.close()

    c, _backend = _exr_client(settings)
    r = c.post("/api/exr", json={"twin": TWIN_DEPLOY})
    assert r.status_code == 400


def test_exr_destroy_requires_allow(tmp_path):
    from tests.test_exercise import TWIN_DEPLOY, _seed_twin

    settings = _settings(tmp_path)
    store = StateStore(settings.db_path)
    try:
        _seed_twin(store, deploy_id=TWIN_DEPLOY)
    finally:
        store.close()

    c, _backend = _exr_client(settings)
    c.post(
        "/api/exr",
        json={"twin": TWIN_DEPLOY, "activity": _EXR_ACTIVITY_BODY, "target_node": "pxtest1"},
    )
    r = c.request("DELETE", "/api/exr", params={"target": TWIN_DEPLOY})
    assert r.status_code == 400


def test_exr_destroy_removes_only_exr_resources(tmp_path):
    """The keystone API case: `DELETE /api/exr` tears down only this
    exercise run's own deploy, leaving the target twin's deploy row
    untouched."""
    from tests.test_exercise import TWIN_DEPLOY, _seed_twin

    settings = _settings(tmp_path)
    store = StateStore(settings.db_path)
    try:
        _seed_twin(store, deploy_id=TWIN_DEPLOY)
    finally:
        store.close()

    c, _backend = _exr_client(settings)
    up = c.post(
        "/api/exr",
        json={"twin": TWIN_DEPLOY, "activity": _EXR_ACTIVITY_BODY, "target_node": "pxtest1"},
    ).json()

    r = c.request(
        "DELETE", "/api/exr",
        params={"allow_destroy": "true", "deploy_id": up["deploy_id"]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["destroyed"] is True
    assert body["deploy_id"] == up["deploy_id"]

    store = StateStore(settings.db_path)
    try:
        twin = store.get_deploy(TWIN_DEPLOY)
        exr = store.get_deploy(up["deploy_id"])
    finally:
        store.close()
    assert twin.status.value != "destroyed"
    assert exr.status.value == "destroyed"


def test_destroy_deploy_cascades_to_every_exercise_run(tmp_path):
    """S3-style cascade (`docs/WORK_ORDER_exercise_tier.md`): `DELETE
    /api/deploys/{twin_id}` also sweeps EVERY exercise run bound to that
    twin, via `axrange.o2control.exercise.cascade_destroy_exercises_for_te`."""
    from tests.test_exercise import TWIN_DEPLOY, _seed_twin

    settings = _settings(tmp_path)
    store = StateStore(settings.db_path)
    try:
        _seed_twin(store, deploy_id=TWIN_DEPLOY)
    finally:
        store.close()

    exr_c, _backend = _exr_client(settings)
    up = exr_c.post(
        "/api/exr",
        json={"twin": TWIN_DEPLOY, "activity": _EXR_ACTIVITY_BODY, "target_node": "pxtest1"},
    ).json()
    assert up["status"] == "completed"

    app2 = create_app(settings)
    app2.state.teardown_factory = lambda _s: _NoopTeTeardown()
    c2 = TestClient(app2)

    r = c2.request(
        "DELETE", f"/api/deploys/{TWIN_DEPLOY}", params={"allow_destroy": "true"}
    )
    assert r.status_code == 200

    store = StateStore(settings.db_path)
    try:
        exr_dep = store.get_deploy(up["deploy_id"])
    finally:
        store.close()
    assert exr_dep.status.value == "destroyed"


def test_destroy_deploy_exr_cascade_survives_an_unrelated_sibling_cascade_crash(
    tmp_path, monkeypatch,
):
    """S3 (code review, service-layer counterpart to the CLI test in
    test_exercise.py): an UNEXPECTED exception in the tap-plan cascade must
    not propagate and skip the exercise cascade or the TE's own
    `DestroyResponse` -- `DELETE /api/deploys/{id}` must still return 200,
    not a 500 from an unrelated sibling's bug."""
    import axrange.o2control.taps as taps_mod
    from tests.test_exercise import TWIN_DEPLOY, _FakeConfigBackend, _seed_twin

    settings = _settings(tmp_path)
    store = StateStore(settings.db_path)
    try:
        _seed_twin(store, deploy_id=TWIN_DEPLOY)
    finally:
        store.close()

    exr_c, _backend = _exr_client(settings)
    up = exr_c.post(
        "/api/exr",
        json={"twin": TWIN_DEPLOY, "activity": _EXR_ACTIVITY_BODY, "target_node": "pxtest1"},
    ).json()
    assert up["status"] == "completed"

    def _boom(**_kwargs):
        raise RuntimeError("simulated DB hiccup in the tap-plan cascade")

    monkeypatch.setattr(taps_mod, "cascade_destroy_for_te", _boom)

    app2 = create_app(settings)
    app2.state.teardown_factory = lambda _s: _NoopTeTeardown()
    c2 = TestClient(app2)

    r = c2.request(
        "DELETE", f"/api/deploys/{TWIN_DEPLOY}", params={"allow_destroy": "true"}
    )
    assert r.status_code == 200  # the twin's own destroy still succeeds
    body = r.json()
    # The crash is SURFACED to a programmatic caller (no silent failure) --
    # not just logged where only an operator watching CLI stdout would see it.
    assert len(body["cascade_errors"]) == 1
    assert "tap-plan cascade" in body["cascade_errors"][0]
    assert "simulated DB hiccup" in body["cascade_errors"][0]

    store = StateStore(settings.db_path)
    try:
        exr_dep = store.get_deploy(up["deploy_id"])
    finally:
        store.close()
    assert exr_dep.status.value == "destroyed"  # unaffected sibling still ran


# ---------------------------------------------------------------------------
# Access facts (`docs/WORK_ORDER_access_facts.md`) -- `GET /api/deploys/{id}
# /access`, an API peer of `axrange te info`'s access block. Reuses the SAME
# `valid_slice` fixture + `_fake_runner()` `test_deploy_lifecycle` etc. use.
# ---------------------------------------------------------------------------
def test_deploy_access_masks_password_by_default(tmp_path):
    c = _client(_settings(tmp_path), runner=_fake_runner())
    r = c.post("/api/deploys", json={
        "scenario": "valid_slice", "target_node": "pxtest1",
    })
    did = r.json()["deploy_id"]

    r = c.get(f"/api/deploys/{did}/access")
    assert r.status_code == 200
    j = r.json()
    assert j["deploy_id"] == did
    assert j["status"] == "completed"
    assert j["secrets_revealed"] is False
    host_ids = {h["host_id"] for h in j["hosts"]}
    assert host_ids == {"h-a", "h-b"}
    for h in j["hosts"]:
        # IP/hint are real facts (never secret); password is masked.
        assert h["ip"] == h["ips"]["corp"]
        assert h["password"] == "********"
        assert h["hint"].startswith("ssh ")


def test_deploy_access_reveals_with_secrets_query_when_no_token_configured(tmp_path):
    """No `AXRANGE_API_TOKEN` configured -> `?secrets=true` needs no auth at
    all (open-writes/open-reads dev posture, same as every other endpoint)."""
    c = _client(_settings(tmp_path), runner=_fake_runner())
    r = c.post("/api/deploys", json={
        "scenario": "valid_slice", "target_node": "pxtest1",
    })
    did = r.json()["deploy_id"]

    r = c.get(f"/api/deploys/{did}/access", params={"secrets": "true"})
    assert r.status_code == 200
    j = r.json()
    assert j["secrets_revealed"] is True
    for h in j["hosts"]:
        assert h["password"] == "Axr4nge-Lab!"


def test_deploy_access_secrets_requires_token_when_configured(tmp_path):
    settings = _settings(tmp_path)
    settings.api_token = "s3cret"
    c = _client(settings, runner=_fake_runner())
    r = c.post(
        "/api/deploys", json={"scenario": "valid_slice", "target_node": "pxtest1"},
        headers={"Authorization": "Bearer s3cret"},
    )
    did = r.json()["deploy_id"]

    # masked read stays open even with a token configured (trusted-bind
    # posture, matching every other GET) -- no header needed.
    r = c.get(f"/api/deploys/{did}/access")
    assert r.status_code == 200
    assert r.json()["hosts"][0]["password"] == "********"

    # ?secrets=true without the token -> 401, never a leaked password.
    r = c.get(f"/api/deploys/{did}/access", params={"secrets": "true"})
    assert r.status_code == 401

    # ?secrets=true WITH the right token -> revealed.
    r = c.get(
        f"/api/deploys/{did}/access", params={"secrets": "true"},
        headers={"Authorization": "Bearer s3cret"},
    )
    assert r.status_code == 200
    assert r.json()["hosts"][0]["password"] == "Axr4nge-Lab!"


def test_deploy_access_unknown_deploy_404(tmp_path):
    c = _client(_settings(tmp_path))
    r = c.get("/api/deploys/does-not-exist/access")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# O2 endpoint resolution (`docs/WORK_ORDER_access_facts.md` Part B/C) --
# `GET /api/o2/info`, an API peer of `axrange o2 info`'s endpoint block.
# ---------------------------------------------------------------------------
def test_o2_info_degrades_gracefully_without_config(tmp_path):
    c = _client(_settings(tmp_path))
    r = c.get("/api/o2/info")
    assert r.status_code == 200
    j = r.json()
    assert j["secrets_revealed"] is False
    assert j["external_sensors"] == []
    for name in ("grafana", "loki", "prometheus", "caldera", "wazuh"):
        assert j["endpoints"][name] == (
            "(not configured — set O2_SERVICES_VMID or a per-service "
            "O2_<SVC>_URL in adapter.conf)"
        )


def test_o2_info_secrets_requires_token_when_configured(tmp_path):
    settings = _settings(tmp_path)
    settings.api_token = "s3cret"
    c = _client(settings)
    assert c.get("/api/o2/info", params={"secrets": "true"}).status_code == 401
    r = c.get(
        "/api/o2/info", params={"secrets": "true"},
        headers={"Authorization": "Bearer s3cret"},
    )
    assert r.status_code == 200
    assert r.json()["secrets_revealed"] is True


# ---------------------------------------------------------------------------
# Twin configs (`docs/WORK_ORDER_twin_configs.md`) -- `GET /api/twin-configs`
# / `GET /api/twin-configs/{config_id}`, API peers of `axrange twin-config
# list|show`. Exercised against the REAL shipped `twin-configs/` catalog
# (no `AXRANGE_TWIN_CONFIGS_DIR` override needed), same as
# `tests/test_twinconfig_cli.py`.
# ---------------------------------------------------------------------------
def test_twin_configs_list(tmp_path):
    c = _client(_settings(tmp_path))
    r = c.get("/api/twin-configs")
    assert r.status_code == 200
    ids = {e["id"] for e in r.json()["twin_configs"]}
    for expected in ("sme-flat", "corp-enterprise", "datacenter", "ics-ot", "dmz-perimeter"):
        assert expected in ids


def test_twin_config_detail(tmp_path):
    c = _client(_settings(tmp_path))
    r = c.get("/api/twin-configs/sme-flat")
    assert r.status_code == 200
    j = r.json()
    assert j["id"] == "sme-flat"
    assert j["segments"]
    assert j["nodes"]
    # `tap_points` is REMOVED from `TwinConfig`/its DTO entirely (`docs/
    # WORK_ORDER_tap_eligibility.md` D3, Authority: user, 2026-07-28) -- a
    # twin config authors no capture intent at all any more.
    assert "tap_points" not in j
    param_names = {p["name"] for p in j["params"]}
    assert "N_WORKSTATIONS" in param_names


def test_twin_config_detail_unknown_404(tmp_path):
    c = _client(_settings(tmp_path))
    r = c.get("/api/twin-configs/does-not-exist")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Imprints (aximprint-integration Track C, `docs/WORK_ORDER_
# aximprint_console.md`) -- `GET /api/twins` / `GET /api/twins/{id}` /
# `GET /api/twins/{id}/provenance`. Exercised against the REAL shipped
# `axrange/api/fixtures/imprints/` fixture catalog (no `AXRANGE_IMPRINTS_DIR`
# override needed), same "test the shipped content" posture as
# `test_twin_configs_list` above.
# ---------------------------------------------------------------------------
def test_imprints_list(tmp_path):
    c = _client(_settings(tmp_path))
    r = c.get("/api/twins")
    assert r.status_code == 200
    by_id = {e["id"]: e for e in r.json()["imprints"]}
    assert "corp-enterprise" in by_id
    e = by_id["corp-enterprise"]
    assert e["hosts"] == 42
    assert e["segments"] == 4
    assert e["as_of"] == ""  # the fixture ships no topology export
    assert e["mean_confidence"] == pytest.approx(0.8825)
    assert e["confidence_source"] == "canonical sidecar (4/4 fields)"
    # Review round 3: when the definition last changed, ISO 8601 UTC (the Twins list's column).
    import datetime as _dt
    assert _dt.datetime.fromisoformat(e["modified_at"]).tzinfo is not None


def test_twin_modified_at_ignores_the_layout_sidecar(tmp_path):
    """Moving a box writes `generated/layout.json`; that is not a change to the twin."""
    import os

    from axrange.api.service import _twin_modified_at

    twin = tmp_path / "t"
    (twin / "generated").mkdir(parents=True)
    (twin / "te.yaml").write_text("x: 1\n")
    (twin / "generated" / "layout.yaml").write_text("pins: {}\n")
    os.utime(twin / "te.yaml", (1_700_000_000, 1_700_000_000))
    os.utime(twin / "generated" / "layout.yaml", (1_800_000_000, 1_800_000_000))
    assert _twin_modified_at(tmp_path, "t") == "2023-11-14T22:13:20+00:00"
    assert _twin_modified_at(tmp_path, "missing") is None


def test_imprint_detail_of_the_packaged_fixture(tmp_path):
    """The packaged fixture carries a canonical provenance file (its
    normalized shape retired 2026-10-29): services and personas are counted
    from the bundle (none declared, none shipped), the mean is over the
    records that carry a confidence, and the vocabulary stat is null."""
    c = _client(_settings(tmp_path))
    r = c.get("/api/twins/corp-enterprise")
    assert r.status_code == 200
    j = r.json()
    assert j["id"] == "corp-enterprise"
    assert j["provenance"] == "canonical"
    assert j["hosts"] == 42
    assert j["segments"] == 4
    assert j["services"] == 0
    assert j["personas"] == 0
    assert j["mean_confidence"] == pytest.approx(0.8825)
    assert j["confidence_source"] == "canonical sidecar (4/4 fields)"
    assert j["out_of_vocab_pct"] is None

    # NOTE (2026-08-25 review S1): no `sum(host_count) == hosts` assertion --
    # that's not a general invariant (a multi-homed non-router host counts
    # once in `hosts` but on every segment it touches in `host_count`); see
    # `tests/test_imprints_reader.py::
    # test_host_counts_by_segment_membership_can_exceed_total_for_a_multihomed_host`
    # for that case in isolation. `corp-enterprise` merely happens to have no
    # multi-homed hosts.
    segs = {s["name"]: s for s in j["topology_segments"]}
    assert segs["corp-lan"]["host_count"] == 26
    assert segs["corp-lan"]["cidr"]
    assert segs["corp-lan"]["confidence"] is None  # no shape carries one
    assert segs["dmz"]["host_count"] == 3
    assert segs["db"]["host_count"] == 5
    assert segs["mgmt"]["host_count"] == 8


def test_imprint_detail_unknown_404(tmp_path):
    c = _client(_settings(tmp_path))
    r = c.get("/api/twins/does-not-exist")
    assert r.status_code == 404


def test_imprint_provenance_rows(tmp_path):
    c = _client(_settings(tmp_path))
    r = c.get("/api/twins/corp-enterprise/provenance")
    assert r.status_code == 200
    j = r.json()
    assert j["imprint_id"] == "corp-enterprise"
    assert j["provenance"] == "canonical"
    by_field = {row["field"]: row for row in j["rows"]}
    assert "twin_config.segments[0].cidr" in by_field
    row = by_field["twin_config.segments[0].cidr"]
    assert row["evidence_class"] == "observed"
    assert row["confidence"] == pytest.approx(0.91)
    assert row["signal"]
    assert row["evidence"]
    signals = [row["signal"] for row in j["rows"]]
    assert any("sql01" in sig for sig in signals)
    assert any("perimeter:wan" in sig for sig in signals)


def test_imprint_provenance_unknown_404(tmp_path):
    c = _client(_settings(tmp_path))
    r = c.get("/api/twins/does-not-exist/provenance")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Imprints -- fail-loud coercion (M1) + id cross-check (M2), 2026-08-25
# review. A malformed/inconsistent fixture must come back as a clean 400
# (`ApiError`/`ImprintDataError`), never an unhandled 500 -- exercised here
# over real HTTP, against a dedicated broken fixture dir (never the shipped
# `corp-enterprise` one).
# ---------------------------------------------------------------------------
def _imprint_settings(tmp_path: Path, imprints_dir: Path) -> "ApiSettings":
    s = _settings(tmp_path)
    s.imprints_dir = str(imprints_dir)
    return s


#: A minimal canonical provenance file.
_CANONICAL_PROVENANCE = {
    "schema": "axr-cast-provenance/1", "pit_id": "pit-1", "code_fingerprint": "f00",
    "fields": {"twin_config.nodes[0].net": {"evidence_class": "observed", "confidence": 0.5}},
}


def _write_broken_imprint(root: Path, dirname: str, *, tc_id: str = "") -> Path:
    """A minimal, otherwise-valid, internally-consistent (dir name == tc.id)
    imprint fixture with a canonical provenance file -- `tc_id` breaks the
    M2 cross-check, so each test corrupts exactly one thing."""
    d = root / dirname
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{dirname}.yaml").write_text(
        "twin_config:\n"
        f"  id: {tc_id or dirname}\n"
        "  segments: [{ name: lan, cidr: 10.5.0.0/24 }]\n"
        "  nodes:\n"
        "    - { name: h1, image: debian-12-cloud@v1, requires: [linux], net: lan }\n"
    )
    (d / f"{dirname}.provenance.json").write_text(json.dumps(_CANONICAL_PROVENANCE))
    return d


def test_imprint_detail_id_mismatch_is_400_not_404_or_500(tmp_path):
    fixtures = tmp_path / "imprints"
    _write_broken_imprint(fixtures, "dirname-a", tc_id="content-id-b")
    c = _client(_imprint_settings(tmp_path, fixtures))
    r = c.get("/api/twins/dirname-a")
    assert r.status_code == 400
    assert "dirname-a" in r.json()["error"]


def test_imprints_list_skips_an_id_mismatched_entry(tmp_path):
    fixtures = tmp_path / "imprints"
    _write_broken_imprint(fixtures, "good")
    _write_broken_imprint(fixtures, "bad-dir", tc_id="something-else")
    c = _client(_imprint_settings(tmp_path, fixtures))
    r = c.get("/api/twins")
    assert r.status_code == 200
    assert [e["id"] for e in r.json()["imprints"]] == ["good"]


# ---------------------------------------------------------------------------
# Deploy from a twin-config (`docs/WORK_ORDER_twin_configs.md` §2's `te
# deploy --config` CLI parity) -- `POST /api/deploys` with `config`/`params`
# instead of `scenario`. Uses a dedicated fixture catalog (`tests/fixtures/
# twin-configs/`) mirroring `valid_slice`'s two-host shape, so the existing
# fake-adapter/manifest fixtures drive it end to end with no cluster.
# ---------------------------------------------------------------------------
_TC_FIXT_DIR = Path(__file__).parent / "fixtures" / "twin-configs"


def _config_settings(tmp_path: Path) -> "ApiSettings":
    s = _settings(tmp_path)
    s.twin_configs_dir = str(_TC_FIXT_DIR.resolve())
    return s


def test_create_deploy_from_config_resolves_and_expands(tmp_path):
    c = _client(_config_settings(tmp_path), runner=_fake_runner())
    r = c.post("/api/deploys", json={
        "config": "api-test-config", "target_node": "pxtest1",
    })
    assert r.status_code == 200
    body = r.json()
    did = body["deploy_id"]
    assert body["detached"] is True
    # A clean config (no unknown persona ids etc.) yields an empty list, not
    # a missing key (code review: CLI/API config-warnings parity).
    assert body["config_warnings"] == []

    r = c.get(f"/api/deploys/{did}")
    assert r.status_code == 200
    j = r.json()
    assert j["deploy"]["status"] == "completed"
    assert j["deploy"]["scenario_id"] == "api-test-config"
    assert all(op["status"] == "completed" for op in j["ops"])


# `test_create_deploy_from_config_surfaces_expansion_warnings` REMOVED
# (2026-08-04, `docs/WORK_ORDER_role_persona_tier.md`): its producer (the
# twin-config unknown-persona warning) is gone with the `persona:` field.
# The `config_warnings` CHANNEL itself is still pinned, by
# `test_create_deploy_scenario_path_config_warnings_is_always_empty` below.


def test_create_deploy_scenario_path_config_warnings_is_always_empty(tmp_path):
    c = _client(_settings(tmp_path), runner=_fake_runner())
    r = c.post("/api/deploys", json={
        "scenario": "valid_slice", "target_node": "pxtest1",
    })
    assert r.status_code == 200
    assert r.json()["config_warnings"] == []


def test_create_deploy_from_config_with_param_override(tmp_path):
    """`params` fans `h-b` out to 2 instances (`count: "${N_WORKSTATIONS:1}"`)
    -- same `--param N_WORKSTATIONS=2` override the CLI supports."""
    c = _client(_config_settings(tmp_path), runner=_fake_runner())
    r = c.post("/api/deploys", json={
        "config": "api-test-config", "target_node": "pxtest1",
        "params": {"N_WORKSTATIONS": "2"},
    })
    assert r.status_code == 200
    did = r.json()["deploy_id"]

    r = c.get(f"/api/deploys/{did}/inventory")
    assert r.status_code == 200
    host_ids = {h["host_id"] for h in r.json()["hosts"]}
    assert host_ids == {"h-a", "h-b01", "h-b02"}


def test_create_deploy_scenario_and_config_together_is_400(tmp_path):
    c = _client(_config_settings(tmp_path), runner=_fake_runner())
    r = c.post("/api/deploys", json={
        "scenario": "valid_slice", "config": "api-test-config",
        "target_node": "pxtest1",
    })
    assert r.status_code == 400


def test_create_deploy_neither_scenario_nor_config_is_400(tmp_path):
    c = _client(_config_settings(tmp_path), runner=_fake_runner())
    r = c.post("/api/deploys", json={"target_node": "pxtest1"})
    assert r.status_code == 400


def test_create_deploy_params_without_config_is_400(tmp_path):
    c = _client(_config_settings(tmp_path), runner=_fake_runner())
    r = c.post("/api/deploys", json={
        "scenario": "valid_slice", "target_node": "pxtest1",
        "params": {"X": "1"},
    })
    assert r.status_code == 400


def test_create_deploy_unknown_config_is_400(tmp_path):
    c = _client(_config_settings(tmp_path), runner=_fake_runner())
    r = c.post("/api/deploys", json={
        "config": "does-not-exist", "target_node": "pxtest1",
    })
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# Imprints -- "deploy as twin" (aximprint-integration Track C slice 2,
# `docs/WORK_ORDER_aximprint_console.md` PART A). `POST /api/twins/{id}/
# plan` (pure preview) + `POST /api/twins/{id}/deploy` (the real thing,
# reusing the SAME async lease-guarded `/api/deploys` machinery a `config`
# deploy uses -- exercised directly via `runner=_fake_runner()`, i.e. NO
# real cluster call, matching every other deploy test in this file). Uses
# the REAL shipped `axrange/api/fixtures/imprints/corp-enterprise` fixture
# (same posture as the slice-1 read tests above) plus one dedicated broken
# fixture for the validation-failure path.
# ---------------------------------------------------------------------------
def test_imprint_plan_success(tmp_path):
    """Pure preview: validates + plans the shipped imprint, no state.db
    write, no lease, no execution -- what makes PART A unit-testable with
    no cluster and what the Console calls before offering "Deploy as twin"."""
    c = _client(_settings(tmp_path))
    r = c.post("/api/twins/corp-enterprise/plan", json={"target_node": "pxtest1"})
    assert r.status_code == 200
    j = r.json()
    assert j["scenario_id"] == "corp-enterprise"
    assert j["ops"] > 0
    assert j["tree"]

    # PURE: no deploy row was created by planning alone.
    assert c.get("/api/deploys").json()["deploys"] == []


def test_imprint_plan_defaults_body_to_empty(tmp_path):
    """The request body is optional -- omitting it entirely must not 422 on
    request-shape grounds (only a genuinely missing DEFAULT_NODE, the same
    failure an omitted `target_node` produces on every other plan/deploy
    endpoint, may still 400)."""
    c = _client(_settings(tmp_path))
    r = c.post("/api/twins/corp-enterprise/plan")
    assert r.status_code == 400
    assert "DEFAULT_NODE" in r.json()["error"]


def test_imprint_plan_unknown_is_404(tmp_path):
    c = _client(_settings(tmp_path))
    r = c.post("/api/twins/does-not-exist/plan", json={"target_node": "pxtest1"})
    assert r.status_code == 404


def test_imprint_plan_reaches_the_planner_for_a_cast_shaped_bundle(tmp_path):
    """R7 (GAP-082) through the deploy pre-check: `_resolve_imprint_scenario_
    path` runs the summary first, so a bundle with a CANONICAL sidecar whose
    export bakes another imprint id used to 400 there before planning -- which
    is every real cast bundle. It now plans; the ADDRESSING guard is the only
    id check left on this path (next test)."""
    import shutil

    from axrange.api.imprints import default_fixtures_dir

    root = tmp_path / "imprints"
    shutil.copytree(default_fixtures_dir() / "corp-enterprise", root / "corp-enterprise")
    d = root / "corp-enterprise"
    (d / "corp-enterprise.provenance.json").write_text(json.dumps({
        "schema": "axr-cast-provenance/1", "pit_id": "pit-ce-1", "fields": {},
    }))
    (d / "corp-enterprise.topology.json").write_text(json.dumps({
        "imprint_id": "observed-ce", "pit_id": "pit-ce-1", "as_of": 1767225600,
        "windows": 3, "segments": [], "nodes": [], "edges": [], "census": {},
    }))
    c = _client(_imprint_settings(tmp_path, root))
    assert c.get("/api/twins/corp-enterprise").status_code == 200
    r = c.post("/api/twins/corp-enterprise/plan", json={"target_node": "pxtest1"})
    assert r.status_code == 200, r.text
    assert r.json()["scenario_id"] == "corp-enterprise"


def test_imprint_plan_still_refuses_a_bundle_whose_twin_config_names_another_id(tmp_path):
    """The addressing guard R7 keeps: the id "deploy as twin" would deploy
    under must be the id the bundle is addressed by (LES-195)."""
    fixtures = tmp_path / "imprints"
    _write_broken_imprint(fixtures, "dirname-a", tc_id="content-id-b")
    c = _client(_imprint_settings(tmp_path, fixtures))
    r = c.post("/api/twins/dirname-a/plan", json={"target_node": "pxtest1"})
    assert r.status_code == 400
    assert "twin_config.id" in r.json()["error"]


def _write_unresolvable_image_imprint(root: Path, dirname: str) -> Path:
    """A twin_config that parses fine (schema-valid) but expands to an IR
    that fails `validate_full` (an unresolvable `image:` -- REF-120) --
    proves the plan/deploy actions surface a validation failure as a clean
    4xx (this module's own established 422 "validation failed; refusing to
    plan" convention -- see `test_deploy_validation_failure_422` above),
    never a 500/traceback, exactly like an ordinary bad `config:` twin-config
    already does through the identical `_validated_plan_for_path` call."""
    d = root / dirname
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{dirname}.yaml").write_text(
        "twin_config:\n"
        f"  id: {dirname}\n"
        "  segments: [{ name: lan, cidr: 10.5.0.0/24 }]\n"
        "  nodes:\n"
        "    - { name: h1, image: totally-bogus-image@v1, requires: [linux], net: lan }\n"
    )
    (d / f"{dirname}.provenance.json").write_text(json.dumps(_CANONICAL_PROVENANCE))
    return d


def test_imprint_plan_validation_failure_is_clean_4xx_not_500(tmp_path):
    fixtures = tmp_path / "imprints"
    _write_unresolvable_image_imprint(fixtures, "bad-ir")
    c = _client(_imprint_settings(tmp_path, fixtures))
    r = c.post("/api/twins/bad-ir/plan", json={"target_node": "pxtest1"})
    assert r.status_code not in (500,)
    assert r.status_code == 422
    assert any(f["rule_id"] == "REF-120" for f in r.json()["detail"])


def test_imprint_deploy_validation_failure_is_clean_4xx_not_500(tmp_path):
    fixtures = tmp_path / "imprints"
    _write_unresolvable_image_imprint(fixtures, "bad-ir")
    c = _client(_imprint_settings(tmp_path, fixtures), runner=_fake_runner())
    r = c.post("/api/twins/bad-ir/deploy", json={"target_node": "pxtest1"})
    assert r.status_code not in (500,)
    assert r.status_code == 422


def test_imprint_deploy_wires_to_the_existing_deploy_entry(tmp_path):
    """"Deploy as twin" hands off to the SAME `create_deploy` an ordinary
    `config`/`scenario` deploy uses -- proven here by asserting against the
    injected fake runner/executor's real effect (op rows in state.db,
    completed status), never a real cluster call (the fake adapter stands
    in, exactly like every other deploy test in this file)."""
    c = _client(_settings(tmp_path), runner=_fake_runner())
    r = c.post("/api/twins/corp-enterprise/deploy", json={"target_node": "pxtest1"})
    assert r.status_code == 200
    body = r.json()
    did = body["deploy_id"]
    assert body["detached"] is True
    assert body["config_warnings"] == []

    r2 = c.get(f"/api/deploys/{did}")
    assert r2.status_code == 200
    j = r2.json()
    assert j["deploy"]["status"] == "completed"
    assert j["deploy"]["scenario_id"] == "corp-enterprise"
    assert len(j["ops"]) > 0
    assert all(op["status"] == "completed" for op in j["ops"])

    # The deploy's inventory reflects the imprint's REAL structural content
    # (26 corp-workstation clones, one router, etc.) -- not a stub.
    r3 = c.get(f"/api/deploys/{did}/inventory")
    assert r3.status_code == 200
    assert len(r3.json()["hosts"]) > 0


def test_imprint_deploy_unknown_is_404(tmp_path):
    c = _client(_settings(tmp_path), runner=_fake_runner())
    r = c.post("/api/twins/does-not-exist/deploy", json={"target_node": "pxtest1"})
    assert r.status_code == 404


def test_imprint_deploy_requires_write_token_when_configured(tmp_path):
    settings = _settings(tmp_path)
    settings.api_token = "s3cret"
    c = _client(settings, runner=_fake_runner())
    body = {"target_node": "pxtest1"}
    assert c.post("/api/twins/corp-enterprise/deploy", json=body).status_code == 401
    assert c.post(
        "/api/twins/corp-enterprise/deploy", json=body,
        headers={"Authorization": "Bearer nope"},
    ).status_code == 401
    r = c.post(
        "/api/twins/corp-enterprise/deploy", json=body,
        headers={"Authorization": "Bearer s3cret"},
    )
    assert r.status_code == 200


def test_imprint_plan_is_open_without_token(tmp_path):
    """`/plan` is a pure preview -- same open-read posture as `/api/plan`,
    never gated behind the write token."""
    settings = _settings(tmp_path)
    settings.api_token = "s3cret"
    c = _client(settings)
    r = c.post("/api/twins/corp-enterprise/plan", json={"target_node": "pxtest1"})
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# Twin configs -- `GET /api/scenarios/{id}/topology` resolves a TWIN-CONFIG
# id for its base graph (`service._resolve_read_ir_path`'s twin-configs
# fallback, `docs/WORK_ORDER_console_selector_and_stale_deploys.md` defect
# 1 / LES-196's generalization note). Every twin this platform deploys today
# comes from a twin config, not a raw scenario, so the Console's picker
# (which now lists twin configs alongside scenarios) needs this to actually
# be USABLE, not just non-empty: picking one and viewing its topology used
# to 404 the base graph. Tried BEFORE the imprints catalog in the fallback
# chain, mirroring `scenario`/`config`/`imprint`'s ordering elsewhere.
# ---------------------------------------------------------------------------
def test_topology_resolves_twin_config_id_for_base_graph(tmp_path):
    c = _client(_config_settings(tmp_path))
    r = c.get("/api/scenarios/api-test-config/topology")
    assert r.status_code == 200
    j = r.json()
    assert j["scenario_id"] == "api-test-config"
    types = {n["type"] for n in j["nodes"]}
    assert "network" in types
    assert "host" in types
    ids = {n["id"] for n in j["nodes"]}
    assert {"corp", "h-a", "h-b"} <= ids
    # Pure read: resolving the twin config for the canvas created no deploy row.
    assert c.get("/api/deploys").json()["deploys"] == []


def test_node_detail_resolves_twin_config_id(tmp_path):
    """The same fallback rides `_parse_ir_only`, so node-detail works for a
    twin-config-sourced deploy too (both callers fixed by one change)."""
    c = _client(_config_settings(tmp_path))
    r = c.get("/api/scenarios/api-test-config/nodes/h-a")
    assert r.status_code == 200
    assert r.json()["id"] == "h-a"


def test_topology_scenario_resolution_unaffected_by_a_configured_twin_configs_dir(tmp_path):
    """The scenarios catalog is tried FIRST, and only falls through to the
    twin-configs catalog on a scenario 404 -- having `twin_configs_dir` ALSO
    configured (as `_config_settings` does) must not change how an ordinary
    scenario id resolves."""
    settings = _config_settings(tmp_path)
    c = _client(settings)
    r = c.get("/api/scenarios/valid_slice/topology")
    assert r.status_code == 200
    # The IR's OWN declared `scenario.id` ("slice-demo"), not the catalog
    # filename ref used to resolve it -- same field `test_topology` doesn't
    # happen to assert, but `topology()` prefers it whenever the IR sets one.
    assert r.json()["scenario_id"] == "slice-demo"


# ---------------------------------------------------------------------------
# Imprints -- `GET /api/scenarios/{id}/topology` resolves an IMPRINT id for
# its base graph (`service._resolve_read_ir_path`'s imprints fallback), so the
# plugin's "deploy as twin" -> Topology handoff overlays a real canvas, not a
# blank one (code-review SHOULD-FIX 1 / LES-196). An imprint id is NOT in the
# scenarios catalog, so this is exactly the id that used to 404 here.
#
# M1 (code review, `docs/WORK_ORDER_console_selector_and_stale_deploys.md`):
# `corp-enterprise` exists in BOTH the shipped twin-configs catalog (33
# nodes, `domain-controller`) AND the imprints fixture catalog (43 nodes,
# `dc01`) -- these two tests now use an ISOLATED `twin_configs_dir`
# (`_config_settings`, which has no `corp-enterprise` at all) so the
# chain-fallback unambiguously reaches the imprint, and assert the
# imprint-EXCLUSIVE node id `dc01` (a shape neither branch could produce by
# accident), not merely "some graph with hosts and networks came back" --
# the assertion `test_topology_resolves_imprint_id_for_base_graph` used to
# make, which is a shape BOTH branches share and so stayed green even while
# silently resolving the wrong catalog against the real, non-isolated
# shipped one.
# ---------------------------------------------------------------------------
def test_topology_resolves_imprint_id_for_base_graph(tmp_path):
    c = _client(_config_settings(tmp_path))
    r = c.get("/api/scenarios/corp-enterprise/topology")
    assert r.status_code == 200
    j = r.json()
    assert j["scenario_id"] == "corp-enterprise"
    ids = {n["id"] for n in j["nodes"]}
    assert "dc01" in ids  # imprint-exclusive; the shipped twin-config has no such node
    assert "domain-controller" not in ids  # the shipped twin-config's node, absent here
    types = {n["type"] for n in j["nodes"]}
    assert "network" in types
    assert "host" in types
    # Pure read: resolving the imprint for the canvas created no deploy row.
    assert c.get("/api/deploys").json()["deploys"] == []


def test_topology_imprint_fallback_still_404s_a_truly_unknown_id(tmp_path):
    """The fallback must not swallow a genuine miss -- an id in neither the
    scenarios NOR the imprints catalog re-raises the original scenarios 404
    (guards `_resolve_read_ir_path`'s `raise scenario_miss from None`)."""
    c = _client(_config_settings(tmp_path))
    assert c.get("/api/scenarios/no-such-thing-anywhere/topology").status_code == 404


def test_node_detail_resolves_imprint_id(tmp_path):
    """The same fallback rides `_parse_ir_only`, so node-detail works for an
    imprint-sourced deploy too (both callers fixed by one change)."""
    c = _client(_config_settings(tmp_path))
    r = c.get("/api/scenarios/corp-enterprise/nodes/dc01")
    assert r.status_code == 200
    assert r.json()["id"] == "dc01"


# ---------------------------------------------------------------------------
# `?source=` disambiguator (M1, code review) -- against the REAL shipped
# catalog (default `_settings`, where `corp-enterprise` genuinely collides
# across twin-configs and imprints), proving the actual bug + fix: the
# id-only chain silently always resolved the twin-config (tried first) even
# when a caller meant the imprint -- the wrong graph rendering with NO error
# at all, worse than a 404. `?source=` lets a caller that KNOWS which
# catalog an id came from (`TwinTopologyScreen`'s `sourceKind`) pin it.
# ---------------------------------------------------------------------------
def test_topology_source_unset_keeps_the_original_chain_order(tmp_path):
    """Unchanged back-compat behavior: no `source` -> twin-config wins the
    collision (tried before imprint), exactly as before this fix."""
    c = _client(_settings(tmp_path))
    j = c.get("/api/scenarios/corp-enterprise/topology").json()
    ids = {n["id"] for n in j["nodes"]}
    assert "domain-controller" in ids
    assert "dc01" not in ids


def test_topology_source_twin_config_pins_the_twin_config_explicitly(tmp_path):
    c = _client(_settings(tmp_path))
    j = c.get(
        "/api/scenarios/corp-enterprise/topology", params={"source": "twin-config"},
    ).json()
    ids = {n["id"] for n in j["nodes"]}
    assert "domain-controller" in ids
    assert "dc01" not in ids


def test_topology_source_imprint_bypasses_the_twin_config_and_reaches_the_imprint(tmp_path):
    """The actual bug: WITHOUT this param, this exact request silently
    rendered the twin-config's 33-node graph instead."""
    c = _client(_settings(tmp_path))
    r = c.get(
        "/api/scenarios/corp-enterprise/topology", params={"source": "imprint"},
    )
    assert r.status_code == 200
    ids = {n["id"] for n in r.json()["nodes"]}
    assert "dc01" in ids
    assert "domain-controller" not in ids


def test_node_detail_source_imprint_bypasses_the_twin_config(tmp_path):
    c = _client(_settings(tmp_path))
    r = c.get(
        "/api/scenarios/corp-enterprise/nodes/dc01", params={"source": "imprint"},
    )
    assert r.status_code == 200
    assert r.json()["id"] == "dc01"
    # The SAME id resolved against the twin-config (no `dc01` there) 404s --
    # proves `source` genuinely constrains the catalog rather than widening it.
    r2 = c.get(
        "/api/scenarios/corp-enterprise/nodes/dc01", params={"source": "twin-config"},
    )
    assert r2.status_code == 404


def test_topology_unknown_source_is_400(tmp_path):
    c = _client(_settings(tmp_path))
    r = c.get("/api/scenarios/corp-enterprise/topology", params={"source": "bogus"})
    assert r.status_code == 400


def test_topology_source_scenario_does_not_reach_twin_configs_or_imprints(tmp_path):
    """Pinning `source=scenario` on an id that is ONLY a twin-config/imprint
    (not in the scenarios catalog at all) 404s rather than silently
    widening to the chain."""
    c = _client(_settings(tmp_path))
    r = c.get("/api/scenarios/corp-enterprise/topology", params={"source": "scenario"})
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# S8 (code review): the twin-config/imprint catalog fallbacks must apply the
# SAME path-traversal guard `ApiSettings.scenario_path` already enforces for
# the scenarios catalog -- `_resolve_read_ir_path`'s new branches called
# `materialize_twin_config` directly with the raw ref, with no guard at all.
# ---------------------------------------------------------------------------
def test_topology_twin_config_source_refuses_path_traversal(tmp_path):
    c = _client(_config_settings(tmp_path))
    for bad in ("../../etc/passwd", "a/b", "a\\b", ".."):
        r = c.get(
            f"/api/scenarios/{bad}/topology", params={"source": "twin-config"},
        )
        assert r.status_code == 404, bad


def test_topology_imprint_source_refuses_path_traversal(tmp_path):
    c = _client(_settings(tmp_path))
    for bad in ("../../etc/passwd", "a/b", "a\\b", ".."):
        r = c.get(f"/api/scenarios/{bad}/topology", params={"source": "imprint"})
        assert r.status_code == 404, bad


# ---------------------------------------------------------------------------
# S2 (code review): `materialize_twin_config` never deletes its own
# tempfile -- `_parse_ir_only` must clean up any tempfile IT created
# (twin-config/imprint sourced), and must NEVER touch a real scenario
# catalog file.
# ---------------------------------------------------------------------------
def test_topology_twin_config_read_does_not_leak_a_tempfile(tmp_path, monkeypatch):
    """`materialize_twin_config`'s tempfile must not survive the read.

    Scoped to a PER-TEST temp root rather than the shared `/tmp`: the
    producer (`axrange/twinconfig/expand.py`, `NamedTemporaryFile(dir=None)`)
    resolves its directory through `tempfile.gettempdir()`, so pointing
    `tempfile.tempdir` at `tmp_path` moves BOTH the file under test and the
    glob that looks for it. Globbing the shared `/tmp` made this test fail
    whenever ANY other process (a parallel agent, another pytest run) had a
    `twin-config-*.yaml` in flight -- a leak in someone else's process is not
    this assertion's subject, and ~1700 unrelated leftovers there make the
    read equally slow and equally wrong.
    """
    import glob
    import os
    import tempfile

    tmp_root = tmp_path / "tmpdir"
    tmp_root.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_root))
    assert tempfile.gettempdir() == str(tmp_root)

    before = set(glob.glob(os.path.join(tempfile.gettempdir(), "twin-config-*")))
    c = _client(_config_settings(tmp_path))
    assert c.get("/api/scenarios/api-test-config/topology").status_code == 200
    after = set(glob.glob(os.path.join(tempfile.gettempdir(), "twin-config-*")))
    assert after - before == set()

    # Non-vacuousness proof. `tempfile.tempdir` is only a useful isolation if
    # the producer actually resolves through it -- otherwise this test would
    # be watching an empty directory and passing for the wrong reason. One
    # direct call to the materializer (whose contract IS to leave the file
    # behind for its caller to clean up) must land under this root.
    from axrange.twinconfig.expand import materialize_twin_config

    leaked = materialize_twin_config(
        "api-test-config", catalog_dir=str(_TC_FIXT_DIR.resolve()),
    )
    try:
        assert leaked.parent == tmp_root, (leaked, tmp_root)
        assert leaked.name.startswith("twin-config-api-test-config-")
    finally:
        leaked.unlink(missing_ok=True)


def test_topology_scenario_read_never_deletes_the_real_catalog_file(tmp_path):
    """The `is_temp=False` path (a real scenario file) must never be
    unlinked -- a regression here would delete shipped/fixture content on
    the next `GET .../topology`."""
    c = _client(_settings(tmp_path))
    scenario_file = FIXT_IR / "valid_slice" / "scenario.yaml"
    assert scenario_file.is_file()
    assert c.get("/api/scenarios/valid_slice/topology").status_code == 200
    assert scenario_file.is_file()  # still there


# ---------------------------------------------------------------------------
# Topology export (`docs/CHANGELOG.md` 2026-08-27; `axrange/api/
# topology_export.py`) -- the aximprint cast-topology-export artifact,
# `<id>.topology.json` shipped BESIDE the twin-config/imprint it casts from
# (`install_cast_as_twin.py`). `GET /api/scenarios/{id}/topology-export`.
# ---------------------------------------------------------------------------
def _cast_config_with_export(tmp_path, config_id="cast-twin", network="cast-net"):
    """A minimal twin-config + a co-located `<id>.topology.json` sibling,
    mirroring `install_cast_as_twin.py`'s real output shape (verified
    against the actual `twin-configs-cast/corpapcn-twin/corpapcn-twin.
    topology.json` on disk)."""
    cfg_dir = tmp_path / config_id
    cfg_dir.mkdir()
    (cfg_dir / f"{config_id}.yaml").write_text(
        f"twin_config:\n  id: {config_id}\n"
        "  description: '[cast pit-test-1] a test cast twin'\n"
        "  segments: []\n  nodes: []\n"
    )
    export = {
        "imprint_id": "pit-test-1", "pit_id": "pit-test-1", "as_of": 1700000000.0,
        "network": network, "vantage": "tap-a", "windows": 4, "schema_version": "1.3",
        "segments": [
            {"cidr": "10.1.0.0/24", "name": "seg-a", "entities": 2,
             "coverage_source": "observed"},
        ],
        "nodes": [
            {"id": "address:10.1.0.5", "address": "10.1.0.5", "locality": "local",
             "segment": "seg-a", "segment_evidence_class": "observed",
             "role": "web-server", "role_confidence": 0.9,
             "role_evidence_class": "observed", "windows_seen": 3,
             "listen_ports": ["443/tcp"], "dial_ports": []},
            {"id": "address:10.1.0.9", "address": "10.1.0.9", "locality": "local",
             "segment": "seg-a", "segment_evidence_class": "observed",
             "role": None, "role_confidence": None, "role_evidence_class": None,
             "windows_seen": 1, "listen_ports": [], "dial_ports": ["53/udp"]},
        ],
        "edges": [
            {"src": "address:10.1.0.9", "dst": "address:10.1.0.5", "proto": "tcp",
             "bytes": 4096, "flows": 2, "evidence_class": "observed"},
        ],
        "census": {"entities_total": 2, "with_role": 1, "placed": 2, "local": 2},
    }
    (cfg_dir / f"{config_id}.topology.json").write_text(json.dumps(export))
    return cfg_dir


def test_topology_export_reads_the_cast_sibling_file(tmp_path):
    _cast_config_with_export(tmp_path, "cast-twin")
    settings = _settings(tmp_path)
    settings.twin_configs_dir = str(tmp_path)
    c = _client(settings)

    r = c.get("/api/scenarios/cast-twin/topology-export")
    assert r.status_code == 200
    j = r.json()
    assert j["pit_id"] == "pit-test-1"
    assert j["network"] == "cast-net"
    assert j["census"] == {"entities_total": 2, "with_role": 1, "placed": 2, "local": 2}
    assert len(j["segments"]) == 1
    assert len(j["nodes"]) == 2
    role_none = next(n for n in j["nodes"] if n["id"] == "address:10.1.0.9")
    assert role_none["role"] is None  # census.with_role < entities_total -- not hidden
    assert len(j["edges"]) == 1
    assert j["edges"][0]["evidence_class"] == "observed"
    assert j["edges"][0]["bytes"] == 4096


def test_topology_export_404s_when_no_sibling_file_exists(tmp_path):
    """The ordinary case for almost every twin-config today (none of the
    shipped catalog, and `api-test-config`, have ever been cast)."""
    c = _client(_config_settings(tmp_path))
    r = c.get("/api/scenarios/api-test-config/topology-export")
    assert r.status_code == 404


def test_topology_export_404s_for_unknown_id(tmp_path):
    c = _client(_config_settings(tmp_path))
    r = c.get("/api/scenarios/does-not-exist-anywhere/topology-export")
    assert r.status_code == 404


def test_topology_export_refuses_path_traversal(tmp_path):
    c = _client(_config_settings(tmp_path))
    for bad in ("../../etc/passwd", "a/b", ".."):
        r = c.get(f"/api/scenarios/{bad}/topology-export")
        assert r.status_code == 404, bad


def test_topology_export_malformed_json_is_400(tmp_path):
    cfg_dir = tmp_path / "broken-twin"
    cfg_dir.mkdir()
    (cfg_dir / "broken-twin.yaml").write_text(
        "twin_config:\n  id: broken-twin\n  segments: []\n  nodes: []\n"
    )
    (cfg_dir / "broken-twin.topology.json").write_text("{not valid json")
    settings = _settings(tmp_path)
    settings.twin_configs_dir = str(tmp_path)
    c = _client(settings)
    r = c.get("/api/scenarios/broken-twin/topology-export")
    assert r.status_code == 400


def test_topology_export_source_scenario_is_400(tmp_path):
    """A raw scenario was never cast from a capture -- `source=scenario`
    is not a valid export source at all (unlike `/topology`, where it
    IS valid)."""
    c = _client(_settings(tmp_path))
    r = c.get(
        "/api/scenarios/valid_slice/topology-export", params={"source": "scenario"},
    )
    assert r.status_code == 400


def test_topology_export_source_twin_config_pins_the_catalog(tmp_path):
    _cast_config_with_export(tmp_path, "cast-twin")
    settings = _settings(tmp_path)
    settings.twin_configs_dir = str(tmp_path)
    c = _client(settings)
    r = c.get(
        "/api/scenarios/cast-twin/topology-export", params={"source": "twin-config"},
    )
    assert r.status_code == 200
    # Pinned to imprints instead -- this id lives only in twin_configs_dir,
    # so it 404s rather than falling back.
    r2 = c.get(
        "/api/scenarios/cast-twin/topology-export", params={"source": "imprint"},
    )
    assert r2.status_code == 404


# ---------------------------------------------------------------------------
# Imprints -- the generic `/api/deploys` dispatcher also accepts `imprint`
# (the field `service.create_deploy` gained so `imprint_deploy` can be a
# thin pass-through -- `docs/WORK_ORDER_aximprint_console.md` PART A's hard
# "no second executor wiring" constraint). Covers the three-way mutual-
# exclusion refusals the dispatcher gained alongside `scenario`/`config`.
# ---------------------------------------------------------------------------
def test_create_deploy_from_imprint_via_generic_endpoint(tmp_path):
    c = _client(_settings(tmp_path), runner=_fake_runner())
    r = c.post("/api/deploys", json={
        "imprint": "corp-enterprise", "target_node": "pxtest1",
    })
    assert r.status_code == 200
    did = r.json()["deploy_id"]
    assert c.get(f"/api/deploys/{did}").json()["deploy"]["status"] == "completed"


def test_create_deploy_scenario_and_imprint_together_is_400(tmp_path):
    c = _client(_settings(tmp_path), runner=_fake_runner())
    r = c.post("/api/deploys", json={
        "scenario": "valid_slice", "imprint": "corp-enterprise",
        "target_node": "pxtest1",
    })
    assert r.status_code == 400


def test_create_deploy_config_and_imprint_together_is_400(tmp_path):
    c = _client(_config_settings(tmp_path), runner=_fake_runner())
    r = c.post("/api/deploys", json={
        "config": "api-test-config", "imprint": "corp-enterprise",
        "target_node": "pxtest1",
    })
    assert r.status_code == 400


def test_create_deploy_params_without_config_or_imprint_is_400(tmp_path):
    c = _client(_settings(tmp_path), runner=_fake_runner())
    r = c.post("/api/deploys", json={
        "scenario": "valid_slice", "target_node": "pxtest1",
        "params": {"X": "1"},
    })
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# The deploy gate is the `config_warnings` channel's producer again
# (`docs/WORK_ORDER_deploy_gate.md` §4.5) -- the role/persona sweep removed
# the previous one (the unknown-persona check) in 2026-08-04, leaving the
# field plumbed but never populated. `tests/fixtures/twin-configs/gate-cast`
# is the fixture cast config carrying `observation:` blocks.
# ---------------------------------------------------------------------------
def test_create_deploy_from_config_surfaces_deploy_gate_warnings(tmp_path):
    c = _client(_config_settings(tmp_path), runner=_fake_runner())
    r = c.post("/api/deploys", json={
        "config": "gate-cast", "target_node": "pxtest1",
        "params": {"DEPLOY_MIN_OCCUPANCY": "0.9"},
    })
    assert r.status_code == 200, r.text
    warnings = r.json()["config_warnings"]
    assert any("deploy gate: effective DEPLOY_MIN_OCCUPANCY=0.9" in w for w in warnings), warnings
    assert any("gated out node 'core-mid'" in w for w in warnings), warnings

    # ...and the gate actually selected: the deployed twin holds only the
    # hosts above the bar, not the ten the cast describes -- and the router
    # came with only its surviving legs.
    did = r.json()["deploy_id"]
    inv = c.get(f"/api/deploys/{did}/inventory")
    assert inv.status_code == 200
    assert {h["host_id"] for h in inv.json()["hosts"]} == {
        "gw", "always-on", "edge-host", "core-strong",
    }


def _write_gated_imprint(root: Path, dirname: str) -> Path:
    """An imprint whose drafted twin_config carries `observation:` blocks --
    i.e. what an emit-all imprint compiler actually hands back. `h-thin` is
    below the 0.8 default bar, so a plan of this imprint previews FEWER
    nodes than the imprint describes."""
    d = root / dirname
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{dirname}.yaml").write_text(
        "twin_config:\n"
        f"  id: {dirname}\n"
        "  params: { DEPLOY_MIN_OCCUPANCY: 0.8 }\n"
        "  segments: [{ name: lan, cidr: 10.5.0.0/24 }]\n"
        "  nodes:\n"
        "    - { name: h-solid, image: debian-12-cloud@v1, requires: [linux], net: lan,\n"
        "        observation: { windows_seen: 11, windows_total: 11, occupancy: 1.0 } }\n"
        "    - { name: h-thin, image: debian-12-cloud@v1, requires: [linux], net: lan,\n"
        "        observation: { windows_seen: 1, windows_total: 11, occupancy: 0.090909 } }\n"
    )
    (d / f"{dirname}.provenance.json").write_text(json.dumps(_CANONICAL_PROVENANCE))
    return d


def test_imprint_plan_surfaces_deploy_gate_warnings(tmp_path):
    """A PREVIEW that quietly reports a gated-down op count is the exact
    silent-skip this project refuses (LES-089): the plan path expands a
    twin_config, so it runs the deploy gate, so it must carry the gate's
    warnings the way `create_deploy` already does."""
    fixtures = tmp_path / "imprints"
    _write_gated_imprint(fixtures, "gated-imprint")
    c = _client(_imprint_settings(tmp_path, fixtures))
    r = c.post("/api/twins/gated-imprint/plan", json={"target_node": "pxtest1"})
    assert r.status_code == 200, r.text
    warnings = r.json()["config_warnings"]
    assert any("effective DEPLOY_MIN_OCCUPANCY=0.8" in w for w in warnings), warnings
    assert any("gated out node 'h-thin'" in w for w in warnings), warnings


def test_plain_scenario_plan_carries_an_empty_warnings_list(tmp_path):
    """The field is additive: a scenario-sourced plan has no expansion step,
    so it reports `[]` -- present, not missing."""
    c = _client(_settings(tmp_path))
    r = c.post("/api/plan", json={"scenario": "valid_slice", "target_node": "pxtest1"})
    assert r.status_code == 200, r.text
    assert r.json()["config_warnings"] == []
