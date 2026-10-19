"""Unified Console backend (`docs/reference/ui-consolidation/00-design-ruling.md`
§3.1-§3.3; contract `04-api-contract.md`).

No live cluster and never `~/.axrange/state.db`: every test seeds a temp
StateStore with hand-written op rows (the `tests/test_traffic_tier.py` idiom)
and drives the REAL executor through a fake persona backend. Personas are
test-local (a per-context overlay, or a twin folder's `personas/`), so no
catalog persona's peer graph leaks into these assertions.

The keystone is `test_host_apply_leaves_every_other_host_untouched`: a
per-host apply on a multi-host tfc deploy must not change one byte of any
other host's op rows (status, timestamps, attempts, inputs, outputs) and
must stop none of their instances.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from axrange.o2control.traffic import (
    AssignmentSpec,
    HostPersonaAssignment,
    TrafficPlanError,
    TrafficProfileSpec,
    apply_host_personas,
    apply_traffic_profile,
    plan_host_personas,
)
from axrange.personas import registry as persona_registry
from axrange.personas.schema import Emit, Persona
from axrange.planner import StateStore
from axrange.planner.state import OpRow, OpStatus

try:
    from starlette.testclient import TestClient
except ImportError:  # pragma: no cover
    pytest.skip("starlette/httpx not installed", allow_module_level=True)

from axrange.api import ApiSettings, create_app

TWIN = "tw1-aaaa1111"
TWIN_ID = "tw1"


# ---------------------------------------------------------------------------
# seeding
# ---------------------------------------------------------------------------
def _op_row(deploy_id, op_id, op_name, scope, *, inputs=None, outputs=None,
            status=OpStatus.COMPLETED, catalog="OP-HOST-010"):
    return OpRow(
        op_id=op_id, deploy_id=deploy_id, op_name=op_name, op_catalog_id=catalog,
        kind="action", scope_json=json.dumps(scope, sort_keys=True),
        inputs_resolved_json=json.dumps(inputs or {}, sort_keys=True, default=str),
        inputs_from_outputs_json="{}",
        outputs_json=json.dumps(outputs, sort_keys=True) if outputs is not None else None,
        cleanup_strategy="redo", idempotency_class="infra",
        status=status, started_at=1.0, completed_at=2.0,
    )


HOSTS = [
    {"host_id": "ws01", "role": "client", "vmid": 201, "ip": "10.9.0.21"},
    {"host_id": "ws02", "role": "client", "vmid": 202, "ip": "10.9.0.22"},
    {"host_id": "ws03", "role": "client", "vmid": 203, "ip": "10.9.0.23"},
    {"host_id": "srv01", "role": "t-server", "vmid": 211, "ip": "10.9.0.11"},
    {"host_id": "srv02", "role": "t-server", "vmid": 212, "ip": "10.9.0.12"},
    {"host_id": "win01", "role": "winbox", "vmid": 221, "ip": "10.9.0.31", "windows": True},
]


def _seed(db: str, *, deploy_id=TWIN, scenario_id=TWIN_ID, hosts=HOSTS, node="pxtest1",
          router_egress: bool = False) -> None:
    store = StateStore(db)
    try:
        store.create_deploy(
            deploy_id=deploy_id, scenario_id=scenario_id, scenario_version=1,
            scenario_schema_version=1, plan_id=f"{scenario_id}-plan", ir_hash="h",
            placement={h["host_id"]: node for h in hosts}, tier="te",
        )
        rows = []
        for h in hosts:
            hid = h["host_id"]
            rows.append(_op_row(deploy_id, f"op-clone-{hid}", "clone-vm", {"host": hid},
                                inputs={"target_node": node, "node_role": h.get("role")},
                                outputs={"vmid": h["vmid"]}))
            rows.append(_op_row(deploy_id, f"op-attach-{hid}", "attach-vm-to-vnet",
                                {"host": hid, "net": "lan"},
                                inputs={"net_id": "lan", "ip": h["ip"], "nic_index": 0}))
            if h.get("windows"):
                rows.append(_op_row(deploy_id, f"op-osready-{hid}", "wait-os-ready",
                                    {"host": hid}, inputs={"os_family": "windows"}))
        if router_egress:
            rows.append(_op_row(deploy_id, "op-clone-rtr", "clone-vm", {"host": "rtr"},
                                inputs={"target_node": node, "node_kind": "router"},
                                outputs={"vmid": 299}))
            rows.append(_op_row(deploy_id, "op-cfg-rtr", "configure-router", {"host": "rtr"},
                                inputs={"egress": {"phase": "measure"}},
                                outputs={"egress_phase": "measure"}))
        store.insert_ops(deploy_id, rows)
    finally:
        store.close()


# Test-local personas: a client that needs a `t-server` peer, an alternative
# with no peers, the server, and a Windows one (for the OS refusal).
PERSONAS = {
    "t-client": Persona(id="t-client", os_family="linux", drive="scripted",
                        emits=(Emit(proto="smb", ports=(445,), peer_role="t-server"),)),
    "t-alt": Persona(id="t-alt", os_family="linux", drive="scripted",
                     emits=(Emit(proto="https", ports=(443,), peer_role="internet"),)),
    "t-server": Persona(id="t-server", os_family="linux", drive="scripted", emits=()),
    "t-win": Persona(id="t-win", os_family="windows", drive="scripted", emits=()),
}

_PERSONA_YAML = {
    "t-client": "persona:\n  id: t-client\n  os_family: linux\n  drive: scripted\n"
                "  emits:\n    - { proto: smb, ports: [445], peer_role: t-server }\n",
    "t-alt": "persona:\n  id: t-alt\n  os_family: linux\n  drive: scripted\n"
             "  emits:\n    - { proto: https, ports: [443], peer_role: internet }\n",
    "t-server": "persona:\n  id: t-server\n  os_family: linux\n  drive: scripted\n  emits: []\n",
    "t-win": "persona:\n  id: t-win\n  os_family: windows\n  drive: scripted\n  emits: []\n",
}


class FakeBackend:
    """drive-persona recorder + the `stop_instance`/`heartbeat_instance`
    test seams `traffic.py` looks for (no GuestAgent, no cluster)."""

    def __init__(self):
        self.calls: list[dict] = []
        self.stopped: list[str] = []
        self.beats: list[str] = []
        self.running: set[str] = set()

    def run(self, op, inputs, ctx):
        if op.op_name == "assign-role":
            return {"assigned": True}
        self.calls.append(dict(inputs))
        self.running.add(inputs["instance_id"])
        return {"started": True}

    def stop_instance(self, vmid, drive, instance_id):
        self.stopped.append(instance_id)
        self.running.discard(instance_id)

    def heartbeat_instance(self, vmid, drive, instance_id):
        self.beats.append(instance_id)
        return {"alive": instance_id in self.running, "unit": f"axr-persona-{instance_id}"}


FULL_PROFILE = TrafficProfileSpec(
    target=TWIN,
    assignments=[
        AssignmentSpec(role="client", persona="t-client"),
        AssignmentSpec(node="srv01", persona="t-server"),
        AssignmentSpec(node="srv02", persona="t-server"),
    ],
)


@pytest.fixture
def overlay():
    with persona_registry.overlay(PERSONAS):
        yield


@pytest.fixture(autouse=True)
def _no_adapter_env(monkeypatch):
    for k in ("DEFAULT_NODE", "PROXMOX_API_URL", "PROXMOX_API_TOKEN", "AXRANGE_TARGETS",
              "AXRANGE_PERSONAS_DIR"):
        monkeypatch.delenv(k, raising=False)


def _full_apply(db, backend, **kw):
    return apply_traffic_profile(
        spec=FULL_PROFILE, db_path=db, target_node="pxtest1", persona_backend=backend, **kw,
    )


def _snapshot(db: str) -> dict:
    """Every op row and attempt, as raw sqlite tuples (bytes-level compare)."""
    con = sqlite3.connect(db)
    try:
        ops = {
            (r[0], r[1]): r for r in con.execute("SELECT deploy_id, op_id, * FROM ops")
        }
        attempts = {r for r in con.execute("SELECT * FROM op_attempts")}
        deploys = {r[0]: r for r in con.execute("SELECT deploy_id, * FROM deploys")}
        leases = {r for r in con.execute("SELECT * FROM leases")}
    finally:
        con.close()
    return {"ops": ops, "attempts": attempts, "deploys": deploys, "leases": leases}


def _rows_by_host(db: str, deploy_id: str) -> dict[str, list[tuple]]:
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    try:
        out: dict[str, list[tuple]] = {}
        for r in con.execute("SELECT * FROM ops WHERE deploy_id = ?", (deploy_id,)):
            inp = json.loads(r["inputs_resolved_json"] or "{}")
            scope = json.loads(r["scope_json"] or "{}")
            host = inp.get("host_id") or scope.get("host")
            attempts = tuple(con.execute(
                "SELECT * FROM op_attempts WHERE deploy_id = ? AND op_id = ?",
                (deploy_id, r["op_id"]),
            ).fetchall())
            out.setdefault(host, []).append((tuple(r), tuple(tuple(a) for a in attempts)))
        return {h: sorted(v) for h, v in out.items()}
    finally:
        con.close()


def _tfc_id(db: str) -> str:
    store = StateStore(db)
    try:
        return store.find_deploy_for_plan(f"tfc-{TWIN}").deploy_id
    finally:
        store.close()


# ---------------------------------------------------------------------------
# §3.3 -- the scalpel, engine level
# ---------------------------------------------------------------------------
def test_host_apply_leaves_every_other_host_untouched(tmp_path, overlay):
    db = str(tmp_path / "state.db")
    _seed(db)
    backend = FakeBackend()
    st = _full_apply(db, backend)
    assert st.status == "completed"
    tfc = _tfc_id(db)
    before = _rows_by_host(db, tfc)
    assert set(before) == {"ws01", "ws02", "ws03", "srv01", "srv02"}
    running_before = set(backend.running)
    calls_before = len(backend.calls)

    res = apply_host_personas(
        db_path=db, target=TWIN, host_id="ws01",
        assignments=[HostPersonaAssignment(persona="t-alt")],
        target_node="pxtest1", persona_backend=backend,
    )
    assert res.status == "completed", res.warnings
    assert res.tfc_deploy_id == tfc

    after = _rows_by_host(db, tfc)
    for host in ("ws02", "ws03", "srv01", "srv02"):
        # Byte-identical rows AND attempts: status, started/completed_at,
        # inputs, outputs, attempt history -- nothing on another host moved.
        assert after[host] == before[host], host
    # Only ws01's old instance was stopped; only ws01's new one was driven.
    assert backend.stopped == ["ws01-t-client"]
    assert [c["instance_id"] for c in backend.calls[calls_before:]] == ["ws01-t-alt"]
    assert backend.running == (running_before - {"ws01-t-client"}) | {"ws01-t-alt"}

    store = StateStore(db)
    try:
        by_id = {op.op_id: op for op in store.ops_for_deploy(tfc)}
        old = next(o for o in by_id.values() if '"ws01-t-client"' in o.scope_json)
        new = next(o for o in by_id.values() if '"ws01-t-alt"' in o.scope_json)
        assert old.status == OpStatus.DESTROYED
        assert json.loads(old.outputs_json)["descoped_by"] == "host-edit"
        assert new.status == OpStatus.COMPLETED
        assert json.loads(new.inputs_resolved_json)["origin"] == "host-edit"
        hb = json.loads(new.outputs_json)["heartbeat"]
        assert hb["state"] == "alive" and hb["alive"] is True and hb["checked_at"]
        assert store.get_deploy(tfc).status.value == "completed"
        assert store.get_lease(f"tfc-{TWIN}") is None  # released
    finally:
        store.close()
    # One post-STOP heartbeat asserting the old unit is dead (M2), then one
    # post-START heartbeat for the new one.
    assert backend.beats == ["ws01-t-client", "ws01-t-alt"]


def test_host_apply_first_apply_creates_the_tfc_deploy(tmp_path, overlay):
    db = str(tmp_path / "state.db")
    _seed(db)
    backend = FakeBackend()
    res = apply_host_personas(
        db_path=db, target=TWIN_ID, host_id="srv01",  # a twin NAME resolves
        assignments=[HostPersonaAssignment(persona="t-server", schedule="24/7")],
        target_node="pxtest1", persona_backend=backend,
    )
    assert res.status == "completed"
    assert res.plan.creates_tfc_deploy is True
    store = StateStore(db)
    try:
        d = store.get_deploy(res.tfc_deploy_id)
        assert d.tier == "tfc" and d.plan_id == f"tfc-{TWIN}"
        (op,) = store.ops_for_deploy(res.tfc_deploy_id)
        assert json.loads(op.inputs_resolved_json)["schedule"] == "24x7"
    finally:
        store.close()


def test_host_apply_role_change_lists_and_optionally_redrives_dependents(tmp_path, overlay):
    db = str(tmp_path / "state.db")
    _seed(db)
    backend = FakeBackend()
    _full_apply(db, backend)
    tfc = _tfc_id(db)
    # Round-robin: ws01 -> srv01, ws02 -> srv02, ws03 -> srv01.
    store = StateStore(db)
    try:
        plan = plan_host_personas(store, TWIN, "srv01", [], role="retired")
    finally:
        store.close()
    assert plan.ok and plan.role_changed and plan.role_new == "retired"
    deps = {d["instance_id"]: d for d in plan.dependents}
    assert set(deps) == {"ws01-t-client", "ws03-t-client"}
    assert deps["ws01-t-client"]["stale_peers"]["t-server"] == {
        "recorded": "10.9.0.11", "new": "10.9.0.12",
    }
    assert [s["instance_id"] for s in plan.stop] == ["srv01-t-server"]

    before = _rows_by_host(db, tfc)
    res = apply_host_personas(
        db_path=db, target=TWIN, host_id="srv01", assignments=[], role="retired",
        update_dependents=True, target_node="pxtest1", persona_backend=backend,
    )
    assert res.status == "completed", res.warnings
    after = _rows_by_host(db, tfc)
    for host in ("ws02", "srv02"):  # not dependents -> untouched
        assert after[host] == before[host], host
    store = StateStore(db)
    try:
        ops = {json.loads(o.scope_json).get("instance") or json.loads(o.scope_json).get("host"): o
               for o in store.ops_for_deploy(tfc)}
        for iid in ("ws01-t-client", "ws03-t-client"):
            inp = json.loads(ops[iid].inputs_resolved_json)
            assert inp["peers"] == {"t-server": "10.9.0.12"}
            assert "origin" not in inp  # dependents are re-driven, not host-edited
        role_row = ops["srv01"]
        assert role_row.op_name == "assign-role"
        assert json.loads(role_row.inputs_resolved_json) == {
            "host_id": "srv01", "role": "retired", "origin": "host-edit",
        }
    finally:
        store.close()
    assert "ws02-t-client" not in [c["instance_id"] for c in backend.calls[-3:]]


def test_host_apply_os_mismatch_is_refused(tmp_path, overlay):
    db = str(tmp_path / "state.db")
    _seed(db)
    with pytest.raises(TrafficPlanError) as ei:
        apply_host_personas(
            db_path=db, target=TWIN, host_id="win01",
            assignments=[HostPersonaAssignment(persona="t-alt")],
            target_node="pxtest1", persona_backend=FakeBackend(),
        )
    assert ei.value.status == 400
    assert ei.value.detail["refusals"][0]["code"] == "os-mismatch"
    store = StateStore(db)
    try:
        assert store.find_deploy_for_plan(f"tfc-{TWIN}") is None  # nothing written
    finally:
        store.close()


class _GA:
    """Fake QGA for the router's egress engine: `--show` + `--healthcheck`."""

    def __init__(self, phase):
        self.phase = phase

    def exec(self, vmid, argv, timeout_s=300):
        from types import SimpleNamespace

        return SimpleNamespace(exit_code=0, stdout=f"phase={self.phase} since=x\n", stderr="")


def test_host_apply_containment_gate_refuses_uncontained(tmp_path, overlay):
    db = str(tmp_path / "state.db")
    _seed(db, router_egress=True)
    with pytest.raises(TrafficPlanError) as ei:
        apply_host_personas(
            db_path=db, target=TWIN, host_id="srv01",
            assignments=[HostPersonaAssignment(persona="t-server")],
            target_node="pxtest1", persona_backend=FakeBackend(),
            containment_ga=_GA("provision"),
        )
    assert ei.value.status == 409
    assert ei.value.detail["refusals"][0]["code"] == "containment"
    # A contained router passes; the override passes with a warning.
    res = apply_host_personas(
        db_path=db, target=TWIN, host_id="srv01",
        assignments=[HostPersonaAssignment(persona="t-server")],
        target_node="pxtest1", persona_backend=FakeBackend(),
        containment_ga=_GA("measure"),
    )
    assert res.status == "completed"


def test_host_apply_contends_with_a_full_apply_lease(tmp_path, overlay):
    db = str(tmp_path / "state.db")
    _seed(db)
    store = StateStore(db)
    try:
        assert store.acquire_lease(f"tfc-{TWIN}", "someone-else").acquired
    finally:
        store.close()
    with pytest.raises(TrafficPlanError) as ei:
        apply_host_personas(
            db_path=db, target=TWIN, host_id="srv01",
            assignments=[HostPersonaAssignment(persona="t-server")],
            target_node="pxtest1", persona_backend=FakeBackend(),
        )
    assert ei.value.status == 409


def test_full_apply_refuses_to_silently_revert_a_host_edit(tmp_path, overlay):
    db = str(tmp_path / "state.db")
    _seed(db)
    backend = FakeBackend()
    _full_apply(db, backend)
    apply_host_personas(
        db_path=db, target=TWIN, host_id="ws01",
        assignments=[HostPersonaAssignment(persona="t-alt")],
        target_node="pxtest1", persona_backend=backend,
    )
    with pytest.raises(TrafficPlanError) as ei:
        _full_apply(db, backend)
    assert ei.value.status == 409
    (edit,) = ei.value.detail["host_edits"]
    assert edit["host_id"] == "ws01"
    assert edit["current"] == ["t-alt"] and edit["profile"] == ["t-client"]

    st = _full_apply(db, backend, allow_revert_host_edits=True)
    assert st.status == "completed"
    assert any("host edit on ws01 reverted" in w for w in st.warnings)
    assert "ws01-t-alt" in backend.stopped
    # Reverted: no host edit left, so a further plain apply is not refused.
    assert _full_apply(db, backend).status == "completed"


def test_profile_export_round_trips_the_running_state(tmp_path, overlay):
    from axrange.o2control.traffic import export_traffic_profile, parse_traffic_profile
    from ruamel.yaml import YAML

    db = str(tmp_path / "state.db")
    _seed(db)
    backend = FakeBackend()
    _full_apply(db, backend)
    apply_host_personas(
        db_path=db, target=TWIN, host_id="ws01",
        assignments=[HostPersonaAssignment(persona="t-alt")],
        target_node="pxtest1", persona_backend=backend,
    )
    store = StateStore(db)
    try:
        doc = export_traffic_profile(store, _tfc_id(db), target=TWIN_ID)
    finally:
        store.close()
    assert doc["host_edits"] == ["ws01"] and doc["instances"] == 5
    spec = parse_traffic_profile(YAML(typ="safe").load(doc["yaml"]))
    assert spec.target == TWIN_ID
    assert {(a.node, a.persona) for a in spec.assignments} == {
        ("ws01", "t-alt"), ("ws02", "t-client"), ("ws03", "t-client"),
        ("srv01", "t-server"), ("srv02", "t-server"),
    }


def test_overlay_is_context_local_and_never_mutates_the_registry():
    assert persona_registry.get("t-client") is None
    with persona_registry.overlay(PERSONAS):
        assert persona_registry.get("t-client") is PERSONAS["t-client"]
        assert "t-client" in persona_registry.all_ids()
    assert persona_registry.get("t-client") is None
    assert "t-client" not in persona_registry.PERSONAS


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
def _twin_folder(root: Path, twin_id: str, personas: dict[str, str], *, cluster=None) -> Path:
    d = root / twin_id
    (d / "personas").mkdir(parents=True, exist_ok=True)
    (d / "te.yaml").write_text(f"twin_config:\n  id: {twin_id}\n")
    cl = f"  cluster: {cluster}\n" if cluster else ""
    (d / "twin.yaml").write_text(
        f"twin:\n  id: {twin_id}\n{cl}  containment: uncontained\n"
        "  tiers:\n    te: { default: te.yaml }\n"
    )
    for pid, text in personas.items():
        (d / "personas" / f"{pid}.yaml").write_text(text)
    return d


def _api(tmp_path, *, adapter_conf: str | None = None, **settings_kw):
    db = str(tmp_path / "state.db")
    twins = tmp_path / "twins"
    _twin_folder(twins, TWIN_ID, _PERSONA_YAML, cluster="pxtest1")
    settings = ApiSettings(
        db_path=db, adapter_config=adapter_conf or "/nonexistent.conf",
        twin_configs_dir=str(twins), **settings_kw,
    )
    app = create_app(settings)
    backend = FakeBackend()
    app.state.tfc_persona_backend = backend
    app.state.tfc_host_background = lambda job: job()  # synchronous
    return TestClient(app), db, backend


def _seed_full(db, backend):
    _seed(db)
    with persona_registry.overlay(PERSONAS):
        _full_apply(db, backend)


def test_api_plan_is_pure(tmp_path):
    c, db, backend = _api(tmp_path)
    _seed_full(db, backend)
    before = _snapshot(db)
    r = c.post(f"/api/deploys/{TWIN}/hosts/ws01/personas/plan",
               json={"assignments": [{"persona": "t-alt"}]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert [s["instance_id"] for s in body["start"]] == ["ws01-t-alt"]
    assert [s["instance_id"] for s in body["stop"]] == ["ws01-t-client"]
    assert body["unchanged"] == {"hosts": 4, "instances": 4}
    # A refused plan is still a 200 carrying the refusal.
    r2 = c.post(f"/api/deploys/{TWIN}/hosts/win01/personas/plan",
                json={"assignments": [{"persona": "t-client"}]})
    assert r2.status_code == 200 and r2.json()["refusals"][0]["code"] == "os-mismatch"
    assert _snapshot(db) == before  # no state write of any kind
    assert persona_registry.get("t-alt") is None  # overlay did not leak


def test_api_plan_before_any_tfc_writes_nothing(tmp_path):
    c, db, _backend = _api(tmp_path)
    _seed(db)
    before = _snapshot(db)
    r = c.post(f"/api/deploys/{TWIN}/hosts/srv01/personas/plan",
               json={"assignments": [{"persona": "t-server"}]})
    assert r.status_code == 200 and r.json()["creates_tfc_deploy"] is True
    assert _snapshot(db) == before


def test_api_put_applies_one_host(tmp_path):
    c, db, backend = _api(tmp_path, api_token="tok")
    _seed_full(db, backend)
    tfc = _tfc_id(db)
    before = _rows_by_host(db, tfc)
    body = {"assignments": [{"persona": "t-alt"}], "target_node": "pxtest1"}
    assert c.put(f"/api/deploys/{TWIN}/hosts/ws01/personas", json=body).status_code == 401
    r = c.put(f"/api/deploys/{TWIN}/hosts/ws01/personas", json=body,
              headers={"Authorization": "Bearer tok"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["tfc_deploy_id"] == tfc and out["plan"]["ok"] is True
    after = _rows_by_host(db, tfc)
    for h in ("ws02", "ws03", "srv01", "srv02"):
        assert after[h] == before[h]
    hosts = c.get(f"/api/deploys/{TWIN}/hosts").json()
    ws01 = next(h for h in hosts["hosts"] if h["host_id"] == "ws01")
    live = [p for p in ws01["personas"] if p["status"] == "completed"]
    assert [(p["persona"], p["origin"]) for p in live] == [("t-alt", "host-edit")]
    assert live[0]["heartbeat"]["state"] == "alive"


def test_api_put_os_mismatch_is_400_with_refusals(tmp_path):
    c, db, backend = _api(tmp_path)
    _seed_full(db, backend)
    r = c.put(f"/api/deploys/{TWIN}/hosts/win01/personas",
              json={"assignments": [{"persona": "t-client"}], "target_node": "pxtest1"})
    assert r.status_code == 400
    assert r.json()["detail"]["refusals"][0]["code"] == "os-mismatch"
    # The Windows persona IS accepted on the Windows host.
    ok = c.put(f"/api/deploys/{TWIN}/hosts/win01/personas",
               json={"assignments": [{"persona": "t-win"}], "target_node": "pxtest1"})
    assert ok.status_code == 200, ok.text


def test_api_put_lease_contention_is_409(tmp_path):
    c, db, backend = _api(tmp_path)
    _seed_full(db, backend)
    store = StateStore(db)
    try:
        assert store.acquire_lease(f"tfc-{TWIN}", "a-full-apply").acquired
    finally:
        store.close()
    r = c.put(f"/api/deploys/{TWIN}/hosts/ws01/personas",
              json={"assignments": [{"persona": "t-alt"}], "target_node": "pxtest1"})
    assert r.status_code == 409


def test_api_put_containment_refusal(tmp_path):
    c, db, _backend = _api(tmp_path)
    _seed(db, router_egress=True)
    c.app.state.containment_ga = _GA("provision")
    r = c.put(f"/api/deploys/{TWIN}/hosts/srv01/personas",
              json={"assignments": [{"persona": "t-server"}], "target_node": "pxtest1"})
    assert r.status_code == 409
    assert r.json()["detail"]["refusals"][0]["code"] == "containment"
    r2 = c.put(f"/api/deploys/{TWIN}/hosts/srv01/personas",
               json={"assignments": [{"persona": "t-server"}], "target_node": "pxtest1",
                     "allow_uncontained": True})
    assert r2.status_code == 200, r2.text


def test_api_null_assignments_keep_the_running_set(tmp_path):
    c, db, backend = _api(tmp_path)
    _seed_full(db, backend)
    r = c.post(f"/api/deploys/{TWIN}/hosts/ws01/personas/plan", json={"role": "client"})
    assert r.status_code == 200
    assert [s["instance_id"] for s in r.json()["start"]] == ["ws01-t-client"]
    assert r.json()["stop"] == []


def test_api_full_tfc_apply_revert_needs_the_flag(tmp_path, monkeypatch):
    c, db, backend = _api(tmp_path)
    _seed_full(db, backend)
    assert c.put(f"/api/deploys/{TWIN}/hosts/ws01/personas",
                 json={"assignments": [{"persona": "t-alt"}],
                       "target_node": "pxtest1"}).status_code == 200
    # POST /api/tfc now loads the twin folder's personas per request (fix
    # round 1, GAP-105): no process-wide registry change is needed.
    profile = {"target": TWIN, "target_node": "pxtest1", "assignments": [
        {"role": "client", "persona": "t-client"},
        {"node": "srv01", "persona": "t-server"}, {"node": "srv02", "persona": "t-server"},
    ]}
    r = c.post("/api/tfc", json=profile)
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["host_edits"][0]["host_id"] == "ws01"
    r2 = c.post("/api/tfc", json={**profile, "allow_revert_host_edits": True})
    assert r2.status_code == 200, r2.text
    assert any("reverted" in w for w in r2.json()["warnings"])


def test_api_profile_export(tmp_path):
    c, db, backend = _api(tmp_path)
    _seed_full(db, backend)
    r = c.get(f"/api/deploys/{TWIN}/tfc/profile")
    assert r.status_code == 200 and r.json()["instances"] == 5
    y = c.get(f"/api/deploys/{TWIN}/tfc/profile", params={"format": "yaml"})
    assert y.status_code == 200 and y.text.startswith("# Exported")
    assert y.headers["content-type"].startswith("text/yaml")


def test_api_personas_overlay_does_not_leak_between_twins(tmp_path):
    c, _db, _b = _api(tmp_path)
    twins = tmp_path / "twins"
    _twin_folder(twins, "tw2", {"only-in-tw2": (
        "persona:\n  id: only-in-tw2\n  os_family: linux\n  drive: scripted\n  emits: []\n")})
    a = c.get("/api/personas", params={"twin": TWIN_ID}).json()
    b = c.get("/api/personas", params={"twin": "tw2"}).json()
    ids_a = {p["id"]: p["source"] for p in a["personas"]}
    ids_b = {p["id"]: p["source"] for p in b["personas"]}
    assert ids_a["t-client"] == "twin" and "only-in-tw2" not in ids_a
    assert ids_b["only-in-tw2"] == "twin" and "t-client" not in ids_b
    assert "domain-controller" in ids_a and ids_a["domain-controller"] == "catalog"
    client = next(p for p in a["personas"] if p["id"] == "t-client")
    assert client["summary"] == "tcp/445 -> t-server"
    assert persona_registry.get("only-in-tw2") is None
    assert c.get("/api/personas", params={"twin": "nope"}).status_code == 404


def _seed_tap(db, te=TWIN):
    store = StateStore(db)
    try:
        tap_id = f"tap-{te}-0000beef"
        store.create_deploy(
            deploy_id=tap_id, scenario_id=f"tap-{te}", scenario_version=None,
            scenario_schema_version=1, plan_id=f"tap-{te}", ir_hash="",
            placement={}, tier="te",
        )
        store.insert_ops(tap_id, [_op_row(
            tap_id, "op-mir-ws01", "install-ovs-mirror", {"host": "ws01"},
            inputs={"tapped_nics": [{"nic_index": 0, "net_id": "lan", "sensor_id": "s1",
                                     "direction": "both"}]},
        )])
        return tap_id
    finally:
        store.close()


def test_api_range_groups_siblings_and_shows_tap_as_tap(tmp_path):
    c, db, backend = _api(tmp_path)
    _seed_full(db, backend)
    tap_id = _seed_tap(db)
    r = c.get("/api/range", params={"live": "false"})
    assert r.status_code == 200, r.text
    (target,) = r.json()["targets"]
    assert target["name"] == "default" and target["reachability"]["state"] == "unknown"
    (twin,) = target["twins"]  # the tap row (stored as te) is NOT a twin
    assert twin["deploy_id"] == TWIN and twin["twin_id"] == TWIN_ID
    assert twin["host_count"] == 6 and twin["persona_instance_count"] == 5
    assert [s["deploy_id"] for s in twin["siblings"]["tap"]] == [tap_id]
    assert twin["siblings"]["tap"][0]["tier"] == "tap"
    assert twin["siblings"]["tfc"][0]["tier"] == "tfc"
    assert twin["siblings"]["exr"] == []
    # DeploySummaryDTO grew tier/plan_id/target, and the list shows tap as tap.
    listed = {d["deploy_id"]: d for d in c.get("/api/deploys").json()["deploys"]}
    assert listed[tap_id]["tier"] == "tap" and listed[TWIN]["tier"] == "te"
    assert listed[TWIN]["plan_id"] == f"{TWIN_ID}-plan" and listed[TWIN]["target"] == "default"
    hosts = c.get(f"/api/deploys/{tap_id}/hosts").json()  # a sibling id -> its twin
    assert hosts["deploy_id"] == TWIN
    ws01 = next(h for h in hosts["hosts"] if h["host_id"] == "ws01")
    assert ws01["taps"][0]["sensors"] == ["s1"]


def _adapter_conf(path: Path, node: str, url: str = "https://10.0.0.1:8006/api2/json") -> str:
    path.write_text(f"PROXMOX_API_URL={url}\nDEFAULT_NODE={node}\n")
    return str(path)


def test_targets_default_is_named_after_default_node(tmp_path):
    conf = _adapter_conf(tmp_path / "adapter.conf", "nodeq")
    c, _db, _b = _api(tmp_path, adapter_conf=conf)
    (t,) = c.get("/api/targets").json()["targets"]
    assert t["name"] == "nodeq" and t["default"] is True and t["node"] == "nodeq"
    assert t["nodes"] == ["nodeq"]


def test_targets_file_serves_two_clusters(tmp_path):
    db_a, db_b = str(tmp_path / "a.db"), str(tmp_path / "b.db")
    _seed(db_a, node="nodea")
    _seed(db_b, deploy_id="tw1-bbbb2222", node="nodeb")
    conf_a = _adapter_conf(tmp_path / "a.conf", "nodea")
    conf_b = _adapter_conf(tmp_path / "b.conf", "nodeb", url="https://10.0.0.2:8006/api2/json")
    tf = tmp_path / "targets.yaml"
    tf.write_text(
        "targets:\n"
        f"  - {{name: alpha, adapter_config: {conf_a}, db: {db_a}}}\n"
        f"  - {{name: beta, adapter_config: {conf_b}, db: {db_b}, nodes: [nodex]}}\n"
    )
    app = create_app(ApiSettings(targets_file=str(tf), twin_configs_dir=str(tmp_path)))
    calls = []

    def probe(cfg):
        calls.append(cfg.default_node)
        if cfg.default_node == "nodeb":
            raise OSError("no route to host")
        return {"version": "9.2"}

    app.state.range_probe = probe
    c = TestClient(app)
    names = [t["name"] for t in c.get("/api/targets").json()["targets"]]
    assert names == ["alpha", "beta"]
    rng = {t["name"]: t for t in c.get("/api/range").json()["targets"]}
    assert rng["alpha"]["reachability"]["state"] == "reachable"
    assert rng["alpha"]["reachability"]["checked_at"]
    assert rng["beta"]["reachability"]["state"] == "unreachable"
    assert [t["deploy_id"] for t in rng["alpha"]["twins"]] == [TWIN]
    assert [t["cluster"] for t in rng["beta"]["twins"]] == ["beta"]
    # Placement names nodeb, but beta declares nodes [nodex]: surfaced.
    assert "nodeb" in rng["beta"]["twins"][0]["warnings"][0]
    assert rng["alpha"]["twins"][0]["warnings"] == []
    # ?target= routes a deploy-scoped read to that target's store.
    assert c.get("/api/deploys/tw1-bbbb2222").status_code == 404
    r = c.get("/api/deploys/tw1-bbbb2222", params={"target": "beta"})
    assert r.status_code == 200 and r.json()["deploy"]["target"] == "beta"
    bad = c.get(f"/api/deploys/{TWIN}", params={"target": "gamma"})
    assert bad.status_code == 404 and bad.json()["detail"]["targets"] == ["alpha", "beta"]


def test_targets_file_refuses_a_shared_db(tmp_path):
    from axrange.api.settings import ApiSettingsError
    from axrange.api.targets import load_targets_file

    tf = tmp_path / "t.yaml"
    tf.write_text("targets:\n  - {name: a, db: /x/s.db, adapter_config: /x/a.conf}\n"
                  "  - {name: b, db: /x/s.db, adapter_config: /x/b.conf}\n")
    with pytest.raises(ApiSettingsError, match="own state store"):
        load_targets_file(tf)


def test_health_sweep_and_host_detail(tmp_path):
    c, db, backend = _api(tmp_path, adapter_conf=_adapter_conf(tmp_path / "a.conf", "pxtest1"))
    _seed_full(db, backend)
    sweeps = []

    def _sweep(cfg):
        sweeps.append(1)
        return [
            {"vmid": 201, "status": "running", "node": "pxtest1"},
            {"vmid": 202, "status": "stopped", "node": "pxtest1"},
        ]

    c.app.state.health_sweep = _sweep
    pings = []
    c.app.state.health_qga_ping = lambda cfg, vmid, node: pings.append((vmid, node))
    body = c.get(f"/api/deploys/{TWIN}/health", params={"host": "ws01"}).json()
    power = {h["host_id"]: h["power"] for h in body["hosts"]}
    assert power["ws01"]["state"] == "running" and power["ws01"]["checked_at"]
    assert power["ws02"]["state"] == "stopped"
    assert power["ws03"]["state"] == "absent"
    assert pings == [(201, "pxtest1")]  # ONE host's QGA, never a fan-out
    (p,) = body["host_detail"]["personas"]
    assert p["instance_id"] == "ws01-t-client" and p["state"] == "alive"
    # S8: a second read inside the min interval is served from cache -- no
    # second sweep, no second guest read -- with its ORIGINAL checked_at.
    again = c.get(f"/api/deploys/{TWIN}/health", params={"host": "ws01"}).json()
    assert len(sweeps) == 1 and pings == [(201, "pxtest1")]
    assert again["host_detail"]["qga"]["checked_at"] == body["host_detail"]["qga"]["checked_at"]
    from axrange.api import service as _svc

    c.app.state.live_caches = _svc.LiveCaches()

    # A failed sweep is `unknown` with the reason -- never "stopped"/"absent".
    def boom(cfg):
        raise OSError("timed out")

    c.app.state.health_sweep = boom
    body = c.get(f"/api/deploys/{TWIN}/health").json()
    assert body["cluster"]["state"] == "unknown" and "timed out" in body["cluster"]["reason"]
    assert {h["power"]["state"] for h in body["hosts"]} == {"unknown"}
    assert body["host_detail"] is None


def test_bare_tfc_status_ignores_destroyed_and_twin_name_resolves(tmp_path):
    from axrange.o2control.traffic import traffic_status

    db = str(tmp_path / "state.db")
    _seed(db)
    with persona_registry.overlay(PERSONAS):
        _full_apply(db, FakeBackend())
    tfc = _tfc_id(db)
    by_name = traffic_status(db_path=db, target=TWIN_ID)
    assert by_name.deploy_id == tfc
    store = StateStore(db)
    try:
        store.mark_destroyed(tfc)
    finally:
        store.close()
    assert traffic_status(db_path=db).status == "not-applied"


def test_latest_topology_overlay_is_scenario_scoped(tmp_path):
    from axrange.api import service

    db = str(tmp_path / "state.db")
    _seed(db)
    with persona_registry.overlay(PERSONAS):
        _full_apply(db, FakeBackend())  # the tfc row is now the newest of any tier
    settings = ApiSettings(db_path=db)
    store = StateStore(db)
    try:
        assert service._latest_live_te_for(store, TWIN_ID) == TWIN
        assert service._latest_live_te_for(store, f"tfc-{TWIN}") is None
    finally:
        store.close()
    assert service._node_status_overlay(settings, TWIN_ID, "latest")["ws01"] == "completed"


def test_twin_config_catalog_carries_cluster(tmp_path):
    c, _db, _b = _api(tmp_path)
    items = {t["id"]: t for t in c.get("/api/twin-configs").json()["twin_configs"]}
    assert items[TWIN_ID]["cluster"] == "pxtest1"


# ---------------------------------------------------------------------------
# CLI parity: `axrange tfc set-host`
# ---------------------------------------------------------------------------
def test_cli_set_host_plan_uses_twin_personas_and_writes_nothing(tmp_path, capsys):
    from axrange.cli.main import main

    db = str(tmp_path / "state.db")
    twins = tmp_path / "twins"
    _twin_folder(twins, TWIN_ID, _PERSONA_YAML)
    _seed(db)
    with persona_registry.overlay(PERSONAS):
        _full_apply(db, FakeBackend())
    before = _snapshot(db)
    rc = main(["tfc", "set-host", TWIN_ID, "ws01", "--persona", "t-alt", "--plan",
               "--db", db, "--twins-dir", str(twins), "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert [s["instance_id"] for s in out["start"]] == ["ws01-t-alt"]
    assert [s["instance_id"] for s in out["stop"]] == ["ws01-t-client"]
    assert _snapshot(db) == before
    assert persona_registry.get("t-alt") is None

    rc = main(["tfc", "set-host", TWIN_ID, "win01", "--persona", "t-client",
               "--db", db, "--twins-dir", str(twins)])
    assert rc == 2  # refused before anything runs
    assert "os-mismatch" in capsys.readouterr().out
    assert _snapshot(db) == before


def test_cli_set_host_needs_an_explicit_persona_set(tmp_path, capsys):
    from axrange.cli.main import main

    rc = main(["tfc", "set-host", TWIN, "ws01", "--db", str(tmp_path / "x.db")])
    assert rc == 2
    assert "--no-personas" in capsys.readouterr().err


def test_detached_host_apply_holds_the_lease_until_its_job_finishes(tmp_path, overlay):
    """LES-243's other half: the host edit TAKES the `tfc-<te>` lease and
    keeps it across the async gap (PUT returns, job not yet run), so a
    whole-profile apply in that window is refused -- and it is released after."""
    db = str(tmp_path / "state.db")
    _seed(db)
    backend = FakeBackend()
    _full_apply(db, backend)
    jobs = []
    res = apply_host_personas(
        db_path=db, target=TWIN, host_id="ws01",
        assignments=[HostPersonaAssignment(persona="t-alt")],
        target_node="pxtest1", persona_backend=backend, background=jobs.append,
    )
    assert res.status == "running" and res.detached is True
    with pytest.raises(TrafficPlanError) as ei:
        _full_apply(db, backend)
    assert ei.value.status == 409
    jobs[0]()
    store = StateStore(db)
    try:
        assert store.get_lease(f"tfc-{TWIN}") is None
        assert store.get_deploy(res.tfc_deploy_id).status.value == "completed"
    finally:
        store.close()
