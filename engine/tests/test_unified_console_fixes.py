"""Unified Console backend -- code-review fix round 1
(`docs/reference/ui-consolidation/06-code-review.md`). One test (or more)
per finding; each asserts behaviour the pre-fix code did not have.

Same posture as `tests/test_unified_console.py` (whose seeding helpers are
reused): temp StateStores, a fake persona backend, no cluster, never
`~/.axrange/state.db`.
"""
from __future__ import annotations

import json

import pytest

from axrange.api import ApiSettings, create_app
from axrange.api.settings import ApiSettingsError
from axrange.o2control import traffic
from axrange.o2control.traffic import (
    AssignmentSpec,
    HostPersonaAssignment,
    TrafficPlanError,
    TrafficProfileSpec,
    apply_host_personas,
    apply_traffic_profile,
    plan_host_personas,
    resolve_traffic_bindings,
)
from axrange.personas import registry as persona_registry
from axrange.personas.schema import Persona
from axrange.planner import StateStore
from axrange.planner.state import OpStatus
from tests.test_unified_console import (
    PERSONAS,
    TWIN,
    TWIN_ID,
    FakeBackend,
    _GA,
    _adapter_conf,
    _api,
    _full_apply,
    _seed,
    _seed_full,
    _seed_tap,
    _tfc_id,
)

try:
    from starlette.testclient import TestClient
except ImportError:  # pragma: no cover
    pytest.skip("starlette/httpx not installed", allow_module_level=True)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("DEFAULT_NODE", "PROXMOX_API_URL", "PROXMOX_API_TOKEN", "AXRANGE_TARGETS",
              "AXRANGE_PERSONAS_DIR", "AXRANGE_PROTECTED_DEPLOYS", "OVS_SSH_HOST",
              "OVS_SSH_USER", "OVS_SSH_KEY"):
        monkeypatch.delenv(k, raising=False)


@pytest.fixture
def overlay():
    with persona_registry.overlay(PERSONAS):
        yield


def _host_apply(db, host, personas, backend, **kw):
    return apply_host_personas(
        db_path=db, target=TWIN, host_id=host,
        assignments=[HostPersonaAssignment(persona=p) for p in personas],
        target_node="pxtest1", persona_backend=backend, **kw,
    )


def _op(db, instance_or_host):
    store = StateStore(db)
    try:
        for op in store.ops_for_deploy(_tfc_id(db)):
            scope = json.loads(op.scope_json)
            if instance_or_host in (scope.get("instance"), scope.get("host")):
                return op
    finally:
        store.close()
    raise AssertionError(instance_or_host)


# ---------------------------------------------------------------------------
# M1 -- a non-default target must never reach the default cluster
# ---------------------------------------------------------------------------
def _targets_file(tmp_path, body: str) -> str:
    tf = tmp_path / "targets.yaml"
    tf.write_text("targets:\n" + body)
    return str(tf)


def test_m1_adapter_config_required_when_more_than_one_target(tmp_path):
    from axrange.api.targets import load_targets_file

    tf = _targets_file(tmp_path, f"  - {{name: a, db: {tmp_path}/a.db}}\n"
                                 f"  - {{name: b, db: {tmp_path}/b.db}}\n")
    with pytest.raises(ApiSettingsError, match="adapter_config' is required"):
        load_targets_file(tf)


def test_m1_identity_env_var_refuses_a_targets_file(tmp_path, monkeypatch):
    conf = _adapter_conf(tmp_path / "a.conf", "nodea")
    tf = _targets_file(tmp_path, f"  - {{name: a, db: {tmp_path}/a.db, adapter_config: {conf}}}\n")
    monkeypatch.setenv("DEFAULT_NODE", "dev45")
    with pytest.raises(ApiSettingsError, match="DEFAULT_NODE"):
        create_app(ApiSettings(targets_file=tf))


def test_m1_two_targets_on_one_api_url_are_refused(tmp_path):
    a = _adapter_conf(tmp_path / "a.conf", "nodea")
    b = _adapter_conf(tmp_path / "b.conf", "nodeb")  # same default URL
    tf = _targets_file(tmp_path, f"  - {{name: a, db: {tmp_path}/a.db, adapter_config: {a}}}\n"
                                 f"  - {{name: b, db: {tmp_path}/b.db, adapter_config: {b}}}\n")
    with pytest.raises(ApiSettingsError, match="same Proxmox API"):
        create_app(ApiSettings(targets_file=tf))


def test_m1_shared_db_is_caught_across_relative_and_absolute_spellings(tmp_path, monkeypatch):
    from axrange.api.targets import load_targets_file

    monkeypatch.chdir(tmp_path)
    tf = _targets_file(tmp_path, f"  - {{name: a, db: ./s.db, adapter_config: /x/a.conf}}\n"
                                 f"  - {{name: b, db: {tmp_path}/s.db, adapter_config: /x/b.conf}}\n")
    with pytest.raises(ApiSettingsError, match="own state store"):
        load_targets_file(tf)


# ---------------------------------------------------------------------------
# M2 -- the detached job's outcome is persisted; an unconfirmed stop is kept
# ---------------------------------------------------------------------------
class _StopFails(FakeBackend):
    def stop_instance(self, vmid, drive, instance_id):
        raise RuntimeError("QGA timeout")


class _StopLies(FakeBackend):
    """The stop 'succeeds' but the unit keeps running."""

    def stop_instance(self, vmid, drive, instance_id):
        self.stopped.append(instance_id)


@pytest.mark.parametrize("backend_cls, why", [
    (_StopFails, "QGA timeout"), (_StopLies, "post-stop heartbeat reads alive"),
])
def test_m2_an_unconfirmed_stop_keeps_the_row_and_is_recorded(tmp_path, overlay, backend_cls, why):
    db = str(tmp_path / "state.db")
    _seed(db)
    backend = backend_cls()
    _full_apply(db, backend)
    res = _host_apply(db, "ws01", ["t-alt"], backend)
    assert res.status == "failed"
    old = _op(db, "ws01-t-client")
    assert old.status == OpStatus.COMPLETED  # NOT retired: it may still run
    outs = json.loads(old.outputs_json)
    assert why in outs["stop_error"]["message"]
    job = outs["host_edit"]
    assert job["status"] == "failed" and job["finished_at"]
    assert any(d["level"] == "error" and "NOT stopped" in d["message"] for d in job["diagnostics"])


def test_m2_detached_diagnostics_reach_hosts_and_the_tfc_deploy_detail(tmp_path):
    c, db, _b = _api(tmp_path)
    backend = _StopFails()
    _seed(db)
    with persona_registry.overlay(PERSONAS):
        _full_apply(db, backend)
    c.app.state.tfc_persona_backend = backend
    jobs = []
    c.app.state.tfc_host_background = jobs.append  # detached: runs after the PUT returns
    r = c.put(f"/api/deploys/{TWIN}/hosts/ws01/personas",
              json={"assignments": [{"persona": "t-alt"}], "target_node": "pxtest1"})
    assert r.status_code == 200 and r.json()["status"] == "running"
    jobs[0]()
    tfc = r.json()["tfc_deploy_id"]
    detail = c.get(f"/api/deploys/{tfc}").json()
    (job,) = detail["host_edits"]
    assert job["status"] == "failed" and job["fingerprint"] == r.json()["plan"]["fingerprint"]
    assert job["plan"]["stop"][0]["instance_id"] == "ws01-t-client"
    hosts = c.get(f"/api/deploys/{TWIN}/hosts").json()
    ws01 = next(h for h in hosts["hosts"] if h["host_id"] == "ws01")
    assert ws01["host_edit"]["job_id"] == job["job_id"]
    kept = next(p for p in ws01["personas"] if p["instance_id"] == "ws01-t-client")
    assert kept["status"] == "completed" and "QGA timeout" in kept["stop_error"]["message"]
    ws02 = next(h for h in hosts["hosts"] if h["host_id"] == "ws02")
    assert ws02["host_edit"] is None


def test_m2_background_that_cannot_start_releases_the_lease(tmp_path, overlay):
    db = str(tmp_path / "state.db")
    _seed(db)
    backend = FakeBackend()
    _full_apply(db, backend)

    def _broken(job):
        raise RuntimeError("can't start new thread")

    with pytest.raises(TrafficPlanError) as ei:
        _host_apply(db, "ws01", ["t-alt"], backend, background=_broken)
    assert ei.value.status == 500
    store = StateStore(db)
    try:
        assert store.get_lease(f"tfc-{TWIN}") is None
    finally:
        store.close()
    job = json.loads(_op(db, "ws01-t-alt").outputs_json)["host_edit"]
    assert job["status"] == "failed" and "could not be started" in job["diagnostics"][-1]["message"]


# ---------------------------------------------------------------------------
# M3 -- lease first; plan and revert guard run under it
# ---------------------------------------------------------------------------
def _lease_held(db) -> bool:
    store = StateStore(db)
    try:
        return store.get_lease(f"tfc-{TWIN}") is not None
    finally:
        store.close()


def test_m3_host_plan_is_computed_under_the_lease(tmp_path, overlay, monkeypatch):
    db = str(tmp_path / "state.db")
    _seed(db)
    backend = FakeBackend()
    _full_apply(db, backend)
    seen = []
    real = traffic.plan_host_personas

    def _spy(*a, **k):
        seen.append(_lease_held(db))
        return real(*a, **k)

    monkeypatch.setattr(traffic, "plan_host_personas", _spy)
    _host_apply(db, "ws01", ["t-alt"], backend)
    assert seen == [True]


def test_m3_whole_profile_revert_guard_runs_under_the_lease(tmp_path, overlay, monkeypatch):
    db = str(tmp_path / "state.db")
    _seed(db)
    backend = FakeBackend()
    _full_apply(db, backend)
    seen = []
    real = traffic.host_edit_reverts

    def _spy(*a, **k):
        seen.append(_lease_held(db))
        return real(*a, **k)

    monkeypatch.setattr(traffic, "host_edit_reverts", _spy)
    _full_apply(db, backend)
    assert seen == [True]


# ---------------------------------------------------------------------------
# M4 -- the previewed plan is the executed plan
# ---------------------------------------------------------------------------
def test_m4_stale_fingerprint_is_409_with_the_fresh_plan(tmp_path):
    c, db, backend = _api(tmp_path)
    _seed_full(db, backend)
    body = {"assignments": [{"persona": "t-alt"}], "target_node": "pxtest1"}
    preview = c.post(f"/api/deploys/{TWIN}/hosts/ws01/personas/plan", json=body).json()
    assert len(preview["fingerprint"]) == 32
    # A pure re-preview is stable.
    again = c.post(f"/api/deploys/{TWIN}/hosts/ws01/personas/plan", json=body).json()
    assert again["fingerprint"] == preview["fingerprint"]
    # Another host edit lands between preview and confirm.
    assert c.put(f"/api/deploys/{TWIN}/hosts/ws02/personas",
                 json={"assignments": [], "target_node": "pxtest1"}).status_code == 200
    r = c.put(f"/api/deploys/{TWIN}/hosts/ws01/personas",
              json={**body, "expect_plan": preview["fingerprint"]})
    assert r.status_code == 409
    fresh = r.json()["detail"]
    assert fresh["code"] == "plan-changed"
    assert fresh["plan"]["fingerprint"] != preview["fingerprint"]
    ok = c.put(f"/api/deploys/{TWIN}/hosts/ws01/personas",
               json={**body, "expect_plan": fresh["plan"]["fingerprint"]})
    assert ok.status_code == 200
    assert ok.json()["plan"]["fingerprint"] == fresh["plan"]["fingerprint"]


def test_m4_catalog_deploy_pins_the_previewed_plan_id(tmp_path):
    from tests.test_api import _client, _fake_runner, _settings

    c = _client(_settings(tmp_path), runner=_fake_runner())
    base = {"scenario": "valid_slice", "target_node": "pxtest1"}
    planned = c.post("/api/plan", json=base).json()
    bad = c.post("/api/deploys", json={**base, "expect_plan_id": "nope"})
    assert bad.status_code == 409 and bad.json()["detail"]["code"] == "plan-changed"
    assert bad.json()["detail"]["plan_id"] == planned["plan_id"]
    ok = c.post("/api/deploys", json={**base, "expect_plan_id": planned["plan_id"]})
    assert ok.status_code == 200 and ok.json()["plan_id"] == planned["plan_id"]


# ---------------------------------------------------------------------------
# SHOULD-FIX
# ---------------------------------------------------------------------------
def test_s1_stop_only_edit_skips_the_containment_gate(tmp_path, overlay):
    db = str(tmp_path / "state.db")
    _seed(db, router_egress=True)
    backend = FakeBackend()
    _host_apply(db, "srv01", ["t-server"], backend, containment_ga=_GA("measure"))

    class _Boom:
        def exec(self, *a, **k):
            raise AssertionError("the gate must not read the router for a stop-only edit")

    res = _host_apply(db, "srv01", [], backend, containment_ga=_Boom())
    assert res.status == "completed"
    assert "srv01-t-server" in backend.stopped


def test_s3_deploy_status_covers_every_live_row(tmp_path, overlay):
    db = str(tmp_path / "state.db")
    _seed(db)
    backend = FakeBackend()
    _full_apply(db, backend)
    tfc = _tfc_id(db)
    store = StateStore(db)
    try:
        store.fail_op(tfc, _op(db, "srv02-t-server").op_id)
    finally:
        store.close()
    res = _host_apply(db, "ws01", ["t-alt"], backend)
    assert res.status == "completed"  # the edit itself worked...
    store = StateStore(db)
    try:
        # ...but srv02's row is still failed, so the deploy is not "completed".
        assert store.get_deploy(tfc).status.value == "failed"
    finally:
        store.close()


def _collision_hosts():
    return [
        {"host_id": "h", "role": "a", "vmid": 301, "ip": "10.9.1.1"},
        {"host_id": "h-t", "role": "b", "vmid": 302, "ip": "10.9.1.2"},
        {"host_id": "srv01", "role": "t-server", "vmid": 211, "ip": "10.9.0.11"},
    ]


COLLIDE = dict(PERSONAS, server=Persona(id="server", os_family="linux", drive="scripted"))


def test_s4_resolver_refuses_an_instance_id_collision(tmp_path):
    db = str(tmp_path / "state.db")
    _seed(db, hosts=_collision_hosts())
    spec = TrafficProfileSpec(target=TWIN, assignments=[
        AssignmentSpec(node="h", persona="t-server"),    # h-t-server
        AssignmentSpec(node="h-t", persona="server"),    # h-t-server
    ])
    store = StateStore(db)
    try:
        with persona_registry.overlay(COLLIDE), pytest.raises(TrafficPlanError, match="instance-collision"):
            resolve_traffic_bindings(spec, store)
    finally:
        store.close()


def test_s4_host_plan_refuses_an_instance_id_collision(tmp_path):
    db = str(tmp_path / "state.db")
    _seed(db, hosts=_collision_hosts())
    with persona_registry.overlay(COLLIDE):
        apply_traffic_profile(
            spec=TrafficProfileSpec(target=TWIN, assignments=[
                AssignmentSpec(node="h-t", persona="server")]),
            db_path=db, target_node="pxtest1", persona_backend=FakeBackend(),
        )
        store = StateStore(db)
        try:
            plan = plan_host_personas(store, TWIN, "h", [HostPersonaAssignment("t-server")])
        finally:
            store.close()
    assert [r["code"] for r in plan.refusals] == ["instance-collision"]


def test_s5_markers_retire_after_a_whole_profile_apply(tmp_path, overlay):
    db = str(tmp_path / "state.db")
    _seed(db)
    backend = FakeBackend()
    _full_apply(db, backend)
    _host_apply(db, "ws01", ["t-alt"], backend)      # ws01-t-client retired as host-edit

    def profile(ws01_persona):
        return TrafficProfileSpec(target=TWIN, assignments=[
            AssignmentSpec(node="ws01", persona=ws01_persona),
            AssignmentSpec(node="ws02", persona="t-client"),
            AssignmentSpec(node="ws03", persona="t-client"),
            AssignmentSpec(node="srv01", persona="t-server"),
            AssignmentSpec(node="srv02", persona="t-server"),
        ])

    kw = dict(db_path=db, target_node="pxtest1", persona_backend=backend)
    assert apply_traffic_profile(spec=profile("t-server"), allow_revert_host_edits=True,
                                 **kw).status == "completed"
    # The host edit was superseded: a further profile change needs no flag.
    assert apply_traffic_profile(spec=profile("t-alt"), **kw).status == "completed"


def test_s6_failed_rows_absent_from_the_new_set_are_stopped_and_retired(tmp_path, overlay):
    db = str(tmp_path / "state.db")
    _seed(db)
    backend = FakeBackend()
    _full_apply(db, backend)
    tfc = _tfc_id(db)
    store = StateStore(db)
    try:
        store.fail_op(tfc, _op(db, "ws01-t-client").op_id)
        store.fail_op(tfc, _op(db, "ws03-t-client").op_id)
    finally:
        store.close()
    _host_apply(db, "ws01", ["t-alt"], backend)      # host-scoped
    assert _op(db, "ws01-t-client").status == OpStatus.DESTROYED
    assert "ws01-t-client" in backend.stopped
    assert _op(db, "ws03-t-client").status == OpStatus.FAILED  # other host: untouched
    # Whole-profile: a failed row the profile no longer names is retired too.
    apply_traffic_profile(
        spec=TrafficProfileSpec(target=TWIN, assignments=[
            AssignmentSpec(node="srv01", persona="t-server")]),
        db_path=db, target_node="pxtest1", persona_backend=backend,
        allow_revert_host_edits=True,
    )
    assert _op(db, "ws03-t-client").status == OpStatus.DESTROYED


def test_s7_removing_the_last_holder_of_a_hard_role_is_refused(tmp_path, overlay):
    db = str(tmp_path / "state.db")
    _seed(db)
    backend = FakeBackend()
    _full_apply(db, backend)
    assert _host_apply(db, "srv02", [], backend, role="retired").status == "completed"
    store = StateStore(db)
    try:
        plan = plan_host_personas(store, TWIN, "srv01", [], role="retired")
        assert [r["code"] for r in plan.refusals] == ["last-holder"]
        assert "NO host holds role 't-server'" in plan.refusals[0]["message"]
        forced = plan_host_personas(store, TWIN, "srv01", [], role="retired",
                                    allow_role_orphaning=True)
    finally:
        store.close()
    assert forced.ok
    assert all(d["no_new_holder"] for d in forced.dependents)
    assert {d["instance_id"] for d in forced.dependents} == {
        "ws01-t-client", "ws02-t-client", "ws03-t-client"}


def test_s8_host_health_reads_are_capped_to_one_at_a_time(tmp_path):
    c, db, backend = _api(tmp_path, adapter_conf=_adapter_conf(tmp_path / "a.conf", "pxtest1"))
    _seed_full(db, backend)
    c.app.state.health_sweep = lambda cfg: []
    c.app.state.health_qga_ping = lambda cfg, vmid, node: None
    caches = c.app.state.live_caches
    caches.host_wait_s = 0.05
    assert caches.host_gate.acquire()  # another host read in flight
    try:
        r = c.get(f"/api/deploys/{TWIN}/health", params={"host": "ws01"})
        assert r.status_code == 429
    finally:
        caches.host_gate.release()
    assert c.get(f"/api/deploys/{TWIN}/health", params={"host": "ws01"}).status_code == 200


def test_odd_recorded_rows_do_not_500_hosts(tmp_path):
    c, db, backend = _api(tmp_path)
    _seed_full(db, backend)
    tap_id = _seed_tap(db)
    store = StateStore(db)
    try:
        tfc = _tfc_id(db)
        op = _op(db, "ws02-t-client")
        inp = json.loads(op.inputs_resolved_json)
        inp["peers"] = {"t-server": None}
        store._conn.execute("UPDATE ops SET inputs_resolved_json=? WHERE deploy_id=? AND op_id=?",
                            (json.dumps(inp), tfc, op.op_id))
        store._conn.commit()
    finally:
        store.close()
    r = c.get(f"/api/deploys/{TWIN}/hosts")
    assert r.status_code == 200, r.text
    ws02 = next(h for h in r.json()["hosts"] if h["host_id"] == "ws02")
    assert ws02["personas"][0]["peers"] == {"t-server": None}
    assert tap_id


def test_hosts_carries_the_recorded_seed(tmp_path):
    c, db, backend = _api(tmp_path)
    _seed(db)
    with persona_registry.overlay(PERSONAS):
        apply_traffic_profile(
            spec=TrafficProfileSpec(target=TWIN, assignments=[
                AssignmentSpec(node="srv01", persona="t-server", seed=42)]),
            db_path=db, target_node="pxtest1", persona_backend=backend,
        )
    srv01 = next(h for h in c.get(f"/api/deploys/{TWIN}/hosts").json()["hosts"]
                 if h["host_id"] == "srv01")
    assert srv01["personas"][0]["seed"] == 42


# ---------------------------------------------------------------------------
# NEW -- server-side protected deploys
# ---------------------------------------------------------------------------
def test_protected_twin_refuses_every_write_route_without_the_override(tmp_path):
    c, db, backend = _api(tmp_path, protected_deploys=TWIN)
    _seed_full(db, backend)
    tap_id = _seed_tap(db)
    tfc = _tfc_id(db)
    locked = [
        ("DELETE", f"/api/deploys/{TWIN}", {"params": {"allow_destroy": "true"}}),
        ("DELETE", f"/api/deploys/{tfc}", {"params": {"allow_destroy": "true"}}),
        ("PUT", f"/api/deploys/{TWIN}/hosts/ws01/personas",
         {"json": {"assignments": [], "target_node": "pxtest1"}}),
        ("POST", "/api/tfc", {"json": {"target": TWIN_ID, "assignments": [
            {"node": "srv01", "persona": "t-server"}], "target_node": "pxtest1"}}),
        ("DELETE", "/api/tfc", {"params": {"allow_destroy": "true", "deploy_id": tfc}}),
        ("POST", "/api/taps", {"json": {"target": TWIN, "bindings": []}}),
        ("DELETE", "/api/taps", {"params": {"allow_destroy": "true", "deploy_id": tap_id}}),
        ("POST", "/api/exr", {"json": {"target": TWIN}}),
        ("DELETE", "/api/exr", {"params": {"allow_destroy": "true", "target": TWIN}}),
    ]
    for method, url, kw in locked:
        r = c.request(method, url, **kw)
        assert r.status_code == 423, (method, url, r.status_code, r.text)
        assert r.json()["detail"] == {"code": "protected", "deploy_id": TWIN}
    # Naming the deploy lets the write through to its own (non-423) answer.
    r = c.put(f"/api/deploys/{TWIN}/hosts/ws01/personas",
              params={"override_protection": TWIN},
              json={"assignments": [{"persona": "t-client"}], "target_node": "pxtest1"})
    assert r.status_code == 200, r.text
    wrong = c.put(f"/api/deploys/{TWIN}/hosts/ws01/personas",
                  params={"override_protection": "something-else"},
                  json={"assignments": [], "target_node": "pxtest1"})
    assert wrong.status_code == 423
    # Reads say so.
    twin = c.get("/api/range", params={"live": "false"}).json()["targets"][0]["twins"][0]
    assert twin["protected"] is True
    assert c.get(f"/api/deploys/{TWIN}/hosts").json()["protected"] is True


def test_protected_deploys_per_target_from_the_targets_file(tmp_path):
    from axrange.api.targets import load_targets_file

    tf = _targets_file(tmp_path, f"  - {{name: a, db: {tmp_path}/a.db, "
                                 f"protected_deploys: [{TWIN}]}}\n")
    (t,) = load_targets_file(tf)
    assert t.protected_deploys == (TWIN,)


# ---------------------------------------------------------------------------
# NEW -- POST /api/tfc loads the twin's local personas; imprints can_apply
# ---------------------------------------------------------------------------
def test_tfc_post_resolves_twin_local_personas_without_touching_the_registry(tmp_path):
    c, db, backend = _api(tmp_path)
    _seed(db)
    r = c.post("/api/tfc", json={"target": TWIN, "target_node": "pxtest1", "assignments": [
        {"node": "srv01", "persona": "t-server"}]})
    assert r.status_code == 200, r.text
    assert persona_registry.get("t-server") is None


def test_tfc_post_with_catalog_personas_is_unchanged(tmp_path):
    """No twin-local persona named: the persisted inputs are byte-identical
    to an apply made with no twin personas folder at all."""
    import shutil

    def run(root, with_personas):
        c, db, _backend = _api(root)
        if not with_personas:
            shutil.rmtree(root / "twins" / TWIN_ID / "personas")
        _seed(db)
        r = c.post("/api/tfc", json={"target": TWIN, "target_node": "pxtest1",
                                     "assignments": [{"node": "srv01", "persona": "dmz-web"}]})
        assert r.status_code == 200, r.text
        store = StateStore(db)
        try:
            return [(o.op_id, o.inputs_resolved_json) for o in store.ops_for_deploy(_tfc_id(db))]
        finally:
            store.close()

    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    assert run(tmp_path / "a", True) == run(tmp_path / "b", False)


def test_imprints_expose_can_apply_and_why_not(tmp_path):
    from axrange.api import recompile as rc

    app = create_app(ApiSettings(db_path=str(tmp_path / "s.db")))
    c = TestClient(app)
    items = c.get("/api/imprints").json()["imprints"]
    assert items
    for it in items:
        assert it["can_apply"] is False
        assert rc.SETTING_BIN in it["apply_blocked_reason"]
    one = c.get(f"/api/imprints/{items[0]['id']}").json()
    assert one["can_apply"] is False and rc.SETTING_BIN in one["apply_blocked_reason"]

    app2 = create_app(ApiSettings(db_path=str(tmp_path / "s.db"), aximprint_bin="/bin/true",
                                  aximprint_store="/tmp/x.duckdb"))
    items2 = TestClient(app2).get("/api/imprints").json()["imprints"]
    for it in items2:
        assert it["can_apply"] is (it["apply_blocked_reason"] is None)
        if not it["can_apply"]:
            assert rc.SETTING_BIN not in it["apply_blocked_reason"]
