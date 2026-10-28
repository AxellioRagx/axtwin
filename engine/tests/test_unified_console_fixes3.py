"""Unified Console backend -- fix round 3 (focused review of the round-2
commit). M1 (no `immutable` reads) is pinned in
`tests/test_unified_console_fixes2.py::test_a_readonly_reader_survives_a_writer_committing_and_closing_under_it`;
everything else is here. Temp stores only, no cluster.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from axrange.api import ApiSettings, create_app
from axrange.api.settings import ApiSettingsError
from axrange.planner import StateStore
from tests.test_unified_console import TWIN, _adapter_conf, _api, _seed

try:
    from starlette.testclient import TestClient
except ImportError:  # pragma: no cover
    pytest.skip("starlette/httpx not installed", allow_module_level=True)

_TC = Path(__file__).parent / "fixtures" / "twin-configs"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("DEFAULT_NODE", "PROXMOX_API_URL", "PROXMOX_API_TOKEN", "AXRANGE_TARGETS",
              "AXRANGE_PROTECTED_DEPLOYS", "AXRANGE_EXTRA_DBS", "EXTERNAL_SENSORS"):
        monkeypatch.delenv(k, raising=False)


def _deploy_client(tmp_path, *, extra: Path, **kw):
    from tests.test_api import _client, _fake_runner, _settings

    s = _settings(tmp_path)
    s.twin_configs_dir = str(_TC.resolve())
    s.extra_dbs = str(extra)
    for k, v in kw.items():
        setattr(s, k, v)
    return _client(s, runner=_fake_runner()), s.db_path


def _standing_twin_in(extra: Path, tmp_path) -> str:
    """Deploy `api-test-config` into `extra` (the fake runner) -- the standing
    twin recorded in a NON-primary store of the same cluster."""
    from tests.test_api import _client, _fake_runner, _settings

    s = _settings(tmp_path / "seed")
    (tmp_path / "seed").mkdir()
    s.db_path = str(extra)
    s.twin_configs_dir = str(_TC.resolve())
    r = _client(s, runner=_fake_runner()).post(
        "/api/deploys", json={"config": "api-test-config", "target_node": "pxtest1"})
    assert r.status_code == 200, r.text
    return r.json()["deploy_id"]


def _rows(db: str) -> list[str]:
    if not Path(db).exists():
        return []
    st = StateStore(db, readonly=True)
    try:
        return [d.deploy_id for d in st.deploys_matching()]
    finally:
        st.close()


# ---------------------------------------------------------------------------
# M2 -- a deploy of a twin standing in another store runs THERE
# ---------------------------------------------------------------------------
def test_m2_a_fresh_post_is_routed_to_the_store_holding_the_standing_twin(tmp_path):
    extra = tmp_path / "svc.state.db"
    standing = _standing_twin_in(extra, tmp_path)
    c, primary = _deploy_client(tmp_path, extra=extra)
    r = c.post("/api/deploys", json={"config": "api-test-config", "target_node": "pxtest1"})
    assert r.status_code == 200, r.text
    assert r.json()["deploy_id"] == standing          # resumed/no-op, not minted
    assert _rows(primary) == []                       # never a second row
    assert _rows(str(extra)) == [standing]


def test_m2_protection_is_honoured_in_the_owning_store(tmp_path):
    extra = tmp_path / "svc.state.db"
    standing = _standing_twin_in(extra, tmp_path)
    st = StateStore(str(extra))
    try:
        from axrange.planner.state import DeployStatus

        st.update_deploy_status(standing, DeployStatus.FAILED)  # a resume would run
    finally:
        st.close()
    c, primary = _deploy_client(tmp_path, extra=extra, protected_deploys=standing)
    body = {"config": "api-test-config", "target_node": "pxtest1"}
    r = c.post("/api/deploys", json=body)
    assert r.status_code == 423 and r.json()["detail"]["deploy_id"] == standing
    ok = c.post("/api/deploys", params={"override_protection": standing}, json=body)
    assert ok.status_code == 200 and ok.json()["deploy_id"] == standing
    assert _rows(primary) == []


def test_m2_a_scenario_standing_in_two_stores_is_refused(tmp_path):
    extra = tmp_path / "svc.state.db"
    _standing_twin_in(extra, tmp_path)
    c, primary = _deploy_client(tmp_path, extra=extra)
    # The same scenario also standing in the primary.
    _seed(primary, deploy_id="api-test-config-00000000", scenario_id="api-test-config")
    r = c.post("/api/deploys", json={"config": "api-test-config", "target_node": "pxtest1"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "ambiguous-store"


def test_m2_run_finds_a_deploy_in_an_extra_store(tmp_path):
    extra = tmp_path / "svc.state.db"
    standing = _standing_twin_in(extra, tmp_path)
    c, primary = _deploy_client(tmp_path, extra=extra)
    r = c.post(f"/api/deploys/{standing}/run")
    assert r.status_code == 200 and r.json()["status"] == "not_implemented"
    assert not Path(primary).exists()  # a POST that only reads creates nothing


# ---------------------------------------------------------------------------
# S1 / S2 -- broken or duplicate stores
# ---------------------------------------------------------------------------
def test_s1_an_unreadable_store_fails_startup_by_name(tmp_path):
    bad = tmp_path / "bad.db"
    bad.write_bytes(b"this is not sqlite at all" * 100)
    with pytest.raises(ApiSettingsError, match="bad.db"):
        _api(tmp_path, extra_dbs=str(bad))


def test_s1_a_store_broken_after_startup_is_skipped_with_a_warning(tmp_path):
    extra = tmp_path / "x" / "extra.db"
    extra.parent.mkdir()
    _seed(str(extra), deploy_id="tw1-bbbb2222")
    c, db, _b = _api(tmp_path, extra_dbs=str(extra))
    _seed(db)
    extra.write_bytes(b"garbage" * 1000)
    for sfx in ("-wal", "-shm"):
        Path(str(extra) + sfx).unlink(missing_ok=True)
    r = c.get("/api/deploys")
    assert r.status_code == 200
    assert [d["deploy_id"] for d in r.json()["deploys"]] == [TWIN]
    assert any("extra.db" in w and "skipped" in w for w in r.json()["warnings"])
    assert c.get(f"/api/deploys/{TWIN}").status_code == 200  # locate skips it too


def test_s2_implicit_extra_paths_are_resolved_before_comparing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    StateStore(str(tmp_path / "state.db")).close()
    s = ApiSettings(db_path=str(tmp_path / "state.db"), extra_dbs="./state.db")
    app = create_app(s)
    (t,) = app.state.settings.targets
    assert t.extra_dbs == ()  # the primary spelled relatively is not a second store


# ---------------------------------------------------------------------------
# S3 -- recorded vmids; S4 -- params strings; S5 -- empty files
# ---------------------------------------------------------------------------
def test_s3_clone_vm_formula_vmids_and_external_sensors_are_not_unrecorded(tmp_path):
    from axrange.adapter_proxmox.naming import vmid_for_op
    from axrange.planner.state import OpRow, OpStatus

    conf = tmp_path / "a.conf"
    conf.write_text(
        "PROXMOX_API_URL=https://10.0.0.1:8006/api2/json\nDEFAULT_NODE=pxtest1\n"
        'EXTERNAL_SENSORS=[{"id": "s1", "vmid": 777, "transport_ip": "10.9.9.9", '
        '"type": "tcpdump", "capture_iface": "eth1"}]\n'
    )
    c, db, _b = _api(tmp_path, adapter_conf=str(conf))
    _seed(db)
    # A clone that FAILED after creating its VM: no outputs.vmid recorded.
    st = StateStore(db)
    try:
        st.insert_ops(TWIN, [OpRow(
            op_id="op-clone-lost", deploy_id=TWIN, op_name="clone-vm",
            op_catalog_id="OP-HOST-010", kind="action", scope_json='{"host": "lost"}',
            inputs_resolved_json="{}", inputs_from_outputs_json="{}", outputs_json=None,
            cleanup_strategy="redo", idempotency_class="infra",
            status=OpStatus.FAILED, started_at=1.0, completed_at=2.0,
        )])
    finally:
        st.close()
    lost = vmid_for_op("tw1-plan", "op-clone-lost")
    c.app.state.range_probe = lambda cfg: {"version": "9.2"}
    c.app.state.health_sweep = lambda cfg: [
        {"vmid": lost, "name": "te-aaaaaa-lost", "status": "stopped", "node": "pxtest1"},
        {"vmid": 777, "name": "o2-sensor", "status": "running", "node": "pxtest1"},
        {"vmid": 990, "name": "te-cafe01-ghost", "status": "running", "node": "pxtest1"},
    ]
    un = c.get("/api/range").json()["targets"][0]["unrecorded"]
    assert [v["vmid"] for v in un["vms"]] == [990]
    assert un["state"] == "known" and "no attached store records" in un["reason"]


def test_s3_a_broken_store_makes_unrecorded_partial(tmp_path):
    extra = tmp_path / "x" / "extra.db"
    extra.parent.mkdir()
    _seed(str(extra), deploy_id="tw1-bbbb2222")
    c, db, _b = _api(tmp_path, extra_dbs=str(extra),
                     adapter_conf=_adapter_conf(tmp_path / "a.conf", "pxtest1"))
    _seed(db)
    extra.write_bytes(b"garbage" * 1000)
    c.app.state.range_probe = lambda cfg: {"version": "9.2"}
    c.app.state.health_sweep = lambda cfg: [
        {"vmid": 990, "name": "te-cafe01-ghost", "status": "running", "node": "pxtest1"}]
    un = c.get("/api/range").json()["targets"][0]["unrecorded"]
    assert un["state"] == "partial" and "extra.db" in un["reason"]


def test_s4_plan_params_must_be_strings(tmp_path):
    from tests.test_api import _client, _settings

    s = _settings(tmp_path)
    s.twin_configs_dir = str(_TC.resolve())
    c = _client(s)
    r = c.post("/api/plan", json={"config": "api-test-config", "params": {"N_WORKSTATIONS": 3},
                                  "target_node": "pxtest1"})
    assert r.status_code == 400 and "N_WORKSTATIONS" in r.json()["error"]


@pytest.mark.parametrize("kind", ["zero-bytes", "empty-schema"])
def test_s5_an_empty_store_file_reads_as_empty(tmp_path, kind):
    extra = tmp_path / "empty.db"
    if kind == "zero-bytes":
        extra.write_bytes(b"")
    else:
        sqlite3.connect(extra).execute("PRAGMA user_version=0").connection.close()
        con = sqlite3.connect(extra)
        con.execute("CREATE TABLE unrelated (x)")
        con.execute("DROP TABLE unrelated")
        con.commit()
        con.close()
    before = extra.read_bytes()
    c, db, _b = _api(tmp_path, extra_dbs=str(extra))
    _seed(db)
    r = c.get("/api/deploys")
    assert r.status_code == 200 and [d["deploy_id"] for d in r.json()["deploys"]] == [TWIN]
    assert extra.read_bytes() == before


# ---------------------------------------------------------------------------
# Notes: nested params, 0-default counts, unique labels
# ---------------------------------------------------------------------------
def test_describe_params_reads_only_the_top_level_block():
    from axrange.twinconfig.params import declared_params_from_text, describe_params

    text = (
        "twin_config:\n  id: x\n"
        "  nodes:\n    - name: a\n      params:\n        NOT_ME: 1\n"
        "  params:\n    # the real one\n    N: 2\n"
    )
    declared = declared_params_from_text(text)
    assert declared == {"N": 2}
    assert describe_params(text, declared) == [
        {"name": "N", "default": 2, "description": "the real one", "declared": True}]
    assert declared_params_from_text("twin_config:\n  nodes:\n    - params:\n        X: 1\n") == {}


def test_a_param_defaulting_to_zero_counts_zero_hosts_everywhere(tmp_path):
    root = tmp_path / "twins"
    d = root / "zt"
    d.mkdir(parents=True)
    (d / "te.yaml").write_text(
        "twin_config:\n  id: zt\n  params:\n    N_X: 0\n"
        "  segments: [{name: lan, cidr: 10.1.0.0/24}]\n"
        "  nodes:\n"
        "    - {name: ws, image: debian-12-cloud@v1, net: lan, count: '${N_X:0}'}\n"
        "    - {name: db, image: debian-12-cloud@v1, net: lan}\n"
    )
    (d / "twin.yaml").write_text(
        "twin:\n  id: zt\n  containment: uncontained\n  tiers:\n    te: { default: te.yaml }\n")
    c = TestClient(create_app(ApiSettings(db_path=str(tmp_path / "s.db"), imprints_dir=str(root))))
    summary = c.get("/api/twins/zt").json()
    cast = c.get("/api/twins/zt/cast").json()
    assert summary["hosts"] == cast["hosts"] == 1


def test_store_labels_are_unique_even_when_names_and_parents_collide():
    from axrange.api.targets import Target, store_labels

    t = Target(name="a", adapter_config=None, db_path="/r/a/x/state.db",
               extra_dbs=("/r/b/x/state.db", "/r/c/y/other.db"))
    labels = store_labels(t)
    assert len(set(labels.values())) == 3
    assert labels["/r/c/y/other.db"] == "other.db"
    assert labels["/r/a/x/state.db"] == "a/x/state.db"
