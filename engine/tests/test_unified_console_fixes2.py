"""Unified Console backend -- fix round 2 (live verification Part A).

1. several state stores per target; 2. reads never write; 3. plan/validate
for twins and imprints; 4. twin parameters in the catalog + parameterized
imprints; 5. unrecorded VMs on /api/range; 6. a cached health answer keeps
its original time everywhere.

Temp stores only, no cluster, never `~/.axrange/state.db`.
"""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from axrange.api import ApiSettings, create_app
from axrange.api.settings import ApiSettingsError
from axrange.planner import StateStore
from tests.test_unified_console import (
    TWIN,
    _adapter_conf,
    _api,
    _seed,
    _seed_full,
)

try:
    from starlette.testclient import TestClient
except ImportError:  # pragma: no cover
    pytest.skip("starlette/httpx not installed", allow_module_level=True)

TWIN_B = "tw1-bbbb2222"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("DEFAULT_NODE", "PROXMOX_API_URL", "PROXMOX_API_TOKEN", "AXRANGE_TARGETS",
              "AXRANGE_PERSONAS_DIR", "AXRANGE_PROTECTED_DEPLOYS", "AXRANGE_EXTRA_DBS",
              "OVS_SSH_HOST", "OVS_SSH_USER", "OVS_SSH_KEY"):
        monkeypatch.delenv(k, raising=False)


def _two_stores(tmp_path, **kw):
    """The implicit target with a primary store (twin A + its tfc) and an
    extra store (twin B, same cluster)."""
    extra = tmp_path / "other" / "svc.state.db"
    extra.parent.mkdir()
    _seed(str(extra), deploy_id=TWIN_B)  # an extra store must exist at startup
    c, db, backend = _api(tmp_path, extra_dbs=str(extra), **kw)
    _seed_full(db, backend)
    return c, db, str(extra), backend


def _file_state(path: str) -> tuple:
    p = Path(path)
    siblings = sorted(x.name for x in p.parent.iterdir())
    return p.read_bytes(), siblings


# ---------------------------------------------------------------------------
# 1. several stores per target
# ---------------------------------------------------------------------------
def test_range_and_deploys_read_every_store_of_the_target(tmp_path):
    c, db, extra, _b = _two_stores(
        tmp_path, adapter_conf=_adapter_conf(tmp_path / "a.conf", "pxtest1"),
    )
    (tgt,) = c.get("/api/range", params={"live": "false"}).json()["targets"]
    assert tgt["name"] == "pxtest1"
    by_id = {t["deploy_id"]: t for t in tgt["twins"]}
    assert set(by_id) == {TWIN, TWIN_B}
    assert by_id[TWIN]["store"] == "state.db"
    assert by_id[TWIN_B]["store"] == "svc.state.db"
    assert by_id[TWIN]["siblings"]["tfc"][0]["store"] == "state.db"
    assert [s["label"] for s in tgt["stores"]] == ["state.db", "svc.state.db"]
    assert all(s["ok"] for s in tgt["stores"])
    listed = {d["deploy_id"]: d["store"] for d in c.get("/api/deploys").json()["deploys"]}
    assert listed[TWIN_B] == "svc.state.db" and listed[TWIN] == "state.db"
    (t,) = c.get("/api/targets").json()["targets"]
    assert {s["path"]: s["primary"] for s in t["stores"]} == {db: True, extra: False}


def test_deploy_scoped_reads_and_writes_go_to_the_store_that_holds_it(tmp_path):
    c, db, extra, _b = _two_stores(tmp_path)
    detail = c.get(f"/api/deploys/{TWIN_B}")
    assert detail.status_code == 200 and detail.json()["deploy"]["store"] == "svc.state.db"
    hosts = c.get(f"/api/deploys/{TWIN_B}/hosts").json()
    assert hosts["store"] == "svc.state.db" and len(hosts["hosts"]) == 6
    before_primary = Path(db).read_bytes()
    r = c.put(f"/api/deploys/{TWIN_B}/hosts/srv01/personas",
              json={"assignments": [{"persona": "t-server"}], "target_node": "pxtest1"})
    assert r.status_code == 200, r.text
    # The tfc deploy (and its lease) landed in the extra store; the primary
    # store did not move.
    st = StateStore(extra, readonly=True)
    try:
        assert st.find_deploy_for_plan(f"tfc-{TWIN_B}") is not None
    finally:
        st.close()
    assert Path(db).read_bytes() == before_primary
    assert c.get("/api/tfc", params={"target": TWIN_B}).json()["deploy_id"] == r.json()["tfc_deploy_id"]


def test_a_deploy_in_two_stores_is_refused_naming_both(tmp_path):
    c, db, extra, _b = _two_stores(tmp_path)
    _seed(extra, deploy_id=TWIN)  # the same id, recorded twice
    r = c.get(f"/api/deploys/{TWIN}")
    assert r.status_code == 409
    assert r.json()["detail"] == {"code": "ambiguous-store", "stores": ["state.db", "svc.state.db"]}
    assert c.get(f"/api/deploys/{TWIN}/hosts").status_code == 409
    (tgt,) = c.get("/api/range", params={"live": "false"}).json()["targets"]
    assert any(TWIN in w and "two stores" in w for w in tgt["warnings"])


def test_a_missing_extra_store_is_reported_and_never_created(tmp_path):
    missing = tmp_path / "nope" / "gone.db"
    # At startup: refused, by name (fix round 3, S2).
    with pytest.raises(ApiSettingsError, match="does not exist"):
        _api(tmp_path, extra_dbs=str(missing))
    assert not missing.exists() and not missing.parent.exists()
    # Vanishing AFTER startup: reported per store, never created.
    missing.parent.mkdir()
    _seed(str(missing), deploy_id=TWIN_B)
    c, db, _b = _api(tmp_path, extra_dbs=str(missing))
    _seed(db)
    missing.unlink()
    for sfx in ("-wal", "-shm"):
        Path(str(missing) + sfx).unlink(missing_ok=True)
    missing.parent.rmdir()
    (tgt,) = c.get("/api/range", params={"live": "false"}).json()["targets"]
    gone = next(s for s in tgt["stores"] if s["label"] == "gone.db")
    assert gone["ok"] is False and "no state store" in gone["reason"]
    assert not missing.exists() and not missing.parent.exists()


def test_targets_file_extra_dbs_and_store_ownership(tmp_path):
    from axrange.api.targets import load_targets_file

    tf = tmp_path / "t.yaml"
    tf.write_text(f"targets:\n  - {{name: a, db: {tmp_path}/a.db, extra_dbs: [{tmp_path}/b.db]}}\n")
    (t,) = load_targets_file(tf)
    assert t.stores == (str(tmp_path / "a.db"), str(tmp_path / "b.db"))
    tf.write_text(
        "targets:\n"
        f"  - {{name: a, db: {tmp_path}/a.db, adapter_config: /x/a, extra_dbs: [{tmp_path}/b.db]}}\n"
        f"  - {{name: b, db: {tmp_path}/b.db, adapter_config: /x/b}}\n"
    )
    with pytest.raises(ApiSettingsError, match="own state store|already listed"):
        load_targets_file(tf)
    tf.write_text(f"targets:\n  - {{name: a, db: {tmp_path}/a.db, extra_dbs: [{tmp_path}/a.db]}}\n")
    with pytest.raises(ApiSettingsError, match="already listed"):
        load_targets_file(tf)


# ---------------------------------------------------------------------------
# 2. reads never write
# ---------------------------------------------------------------------------
_READS = [
    ("GET", "/api/range", {"params": {"live": "false"}}),
    ("GET", "/api/deploys", {}),
    ("GET", f"/api/deploys/{TWIN}", {}),
    ("GET", f"/api/deploys/{TWIN}/inventory", {}),
    ("GET", f"/api/deploys/{TWIN}/hosts", {}),
    ("GET", f"/api/deploys/{TWIN}/tfc/profile", {}),
    ("GET", "/api/tfc", {"params": {"target": TWIN}}),
    ("GET", "/api/health", {}),
    ("POST", f"/api/deploys/{TWIN}/hosts/ws01/personas/plan",
     {"json": {"assignments": [{"persona": "t-alt"}]}}),
]


def _checkpointed_store(tmp_path):
    c, db, backend = _api(tmp_path)
    _seed_full(db, backend)
    con = __import__("sqlite3").connect(db)
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    con.close()
    for sfx in ("-wal", "-shm"):
        Path(db + sfx).unlink(missing_ok=True)
    return c, db


def test_a_batch_of_reads_leaves_the_store_byte_identical(tmp_path):
    c, db = _checkpointed_store(tmp_path)
    before = Path(db).read_bytes()
    for method, url, kw in _READS:
        r = c.request(method, url, **kw)
        assert r.status_code == 200, (url, r.status_code, r.text[:200])
    # Same bytes. SQLite may leave -wal/-shm beside a WAL-mode db for a
    # `mode=ro` reader (the accepted price of NOT using `immutable`,
    # LES-359) -- but a reader never writes a frame into the WAL.
    assert Path(db).read_bytes() == before
    wal = Path(db + "-wal")
    assert not wal.exists() or wal.stat().st_size == 0


def test_a_readonly_reader_survives_a_writer_committing_and_closing_under_it(tmp_path):
    """LES-359: with `immutable=1` a reader that opened when there was no
    -wal read torn pages once a writer committed and closed (checkpointed)
    mid-scan. `mode=ro` holds a read snapshot: the scan finishes intact."""
    import sqlite3

    db = str(tmp_path / "s.db")
    StateStore(db).close()
    w = sqlite3.connect(db, isolation_level=None)
    w.execute("BEGIN")
    for i in range(3000):
        w.execute(
            "INSERT INTO deploys (deploy_id, scenario_id, plan_id, ir_hash, status, "
            "placement_json, started_at, updated_at) VALUES (?, 's', 'p', 'h', 'completed', ?, 1, 1)",
            (f"d{i:05d}", json.dumps({"x": "a" * 200})),
        )
    w.execute("COMMIT")
    w.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    w.close()
    for sfx in ("-wal", "-shm"):
        Path(db + sfx).unlink(missing_ok=True)

    reader = StateStore(db, readonly=True)
    try:
        cur = reader._conn.execute("SELECT deploy_id, placement_json FROM deploys ORDER BY deploy_id")
        first = cur.fetchmany(50)
        # A separate writer rewrites every row, commits and CLOSES (which
        # checkpoints the WAL back into the main file) while the scan is open.
        w = sqlite3.connect(db, isolation_level=None)
        w.execute("PRAGMA journal_mode=WAL")
        w.execute("BEGIN")
        w.execute("UPDATE deploys SET placement_json = ?", (json.dumps({"y": "b" * 900}),))
        w.execute("DELETE FROM deploys WHERE deploy_id >= 'd02000'")
        w.execute("COMMIT")
        w.close()
        rest = cur.fetchall()
    finally:
        reader.close()
    rows = first + rest
    assert len(rows) == 3000  # the reader's snapshot, whole
    assert all(json.loads(p) == {"x": "a" * 200} for _d, p in rows)


def test_reads_work_on_a_read_only_store_file(tmp_path):
    c, db = _checkpointed_store(tmp_path)
    os.chmod(db, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    try:
        for method, url, kw in _READS:
            r = c.request(method, url, **kw)
            assert r.status_code == 200, (url, r.status_code, r.text[:200])
    finally:
        os.chmod(db, stat.S_IRUSR | stat.S_IWUSR)


def test_a_writable_open_of_a_current_store_stamps_nothing(tmp_path):
    db = str(tmp_path / "s.db")
    StateStore(db).close()
    con = __import__("sqlite3").connect(db)
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    change_counter = lambda: Path(db).read_bytes()[24:28]  # noqa: E731
    before = change_counter()
    con.close()
    for _ in range(3):
        StateStore(db).close()
    con = __import__("sqlite3").connect(db)
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    con.close()
    assert change_counter() == before


def test_readonly_store_refuses_writes_loudly(tmp_path):
    db = str(tmp_path / "s.db")
    StateStore(db).close()
    st = StateStore(db, readonly=True)
    try:
        with pytest.raises(Exception, match="readonly|read-only|query_only"):
            st.acquire_lease("x", "y")
    finally:
        st.close()


# ---------------------------------------------------------------------------
# 3. plan / validate for twin configs and imprints
# ---------------------------------------------------------------------------
_TC = Path(__file__).parent / "fixtures" / "twin-configs"


def _plan_client(tmp_path, runner=None):
    from tests.test_api import _client, _fake_runner, _settings

    s = _settings(tmp_path)
    s.twin_configs_dir = str(_TC.resolve())
    return _client(s, runner=runner or _fake_runner())


def test_plan_accepts_a_twin_config_with_params_and_pins_the_deploy(tmp_path):
    c = _plan_client(tmp_path)
    body = {"config": "api-test-config", "params": {"N_WORKSTATIONS": "3"},
            "target_node": "pxtest1"}
    p = c.post("/api/plan", json=body)
    assert p.status_code == 200, p.text[:300]
    j = p.json()
    assert j["vm_count"] == 4  # h-a + 3 x h-b
    assert j["params"] == {"N_WORKSTATIONS": 3}
    d = c.post("/api/deploys", json={**body, "expect_plan_id": j["plan_id"]})
    assert d.status_code == 200 and d.json()["plan_id"] == j["plan_id"]
    default = c.post("/api/plan", json={"config": "api-test-config", "target_node": "pxtest1"}).json()
    assert default["vm_count"] == 2 and default["params"] == {"N_WORKSTATIONS": 1}
    assert default["plan_id"] != j["plan_id"]


def test_validate_accepts_a_twin_config(tmp_path):
    c = _plan_client(tmp_path)
    r = c.post("/api/validate", json={"config": "api-test-config"})
    assert r.status_code == 200, r.text[:300]
    assert r.json()["disposition"].lower() in ("pass", "pass_with_warnings")


def test_plan_and_validate_need_exactly_one_source(tmp_path):
    c = _plan_client(tmp_path)
    for url in ("/api/plan", "/api/validate"):
        assert c.post(url, json={}).status_code == 400
        assert c.post(url, json={"scenario": "valid_slice", "config": "api-test-config"}).status_code == 400
        assert c.post(url, json={"scenario": "valid_slice", "params": {"A": "1"}}).status_code == 400


def test_plan_writes_no_state(tmp_path):
    c = _plan_client(tmp_path)
    r = c.post("/api/plan", json={"config": "api-test-config", "target_node": "pxtest1"})
    assert r.status_code == 200
    v = c.post("/api/validate", json={"config": "api-test-config"})
    assert v.status_code == 200
    assert not (tmp_path / "state.db").exists()


# ---------------------------------------------------------------------------
# 4. twin parameters in the catalog; parameterized imprints are usable
# ---------------------------------------------------------------------------
def test_twin_config_detail_describes_its_params(tmp_path):
    twins = tmp_path / "twins"
    d = twins / "pt"
    d.mkdir(parents=True)
    (d / "te.yaml").write_text(
        "twin_config:\n  id: pt\n  params:\n"
        "    # How many workstations to stand up.\n"
        "    N_WS: 4\n"
        "    SITE: hq  # site tag -- informational\n"
        "              # (second line)\n"
        "  segments: [{name: lan, cidr: 10.1.0.0/24}]\n"
        "  nodes:\n"
        "    - {name: ws, image: debian-12-cloud@v1, net: lan, count: '${N_WS:4}'}\n"
        "    - {name: db, image: debian-12-cloud@v1, net: lan, count: '${N_DB:1}'}\n"
    )
    (d / "twin.yaml").write_text(
        "twin:\n  id: pt\n  containment: uncontained\n  tiers:\n    te: { default: te.yaml }\n"
    )
    c = TestClient(create_app(ApiSettings(db_path=str(tmp_path / "s.db"),
                                          twin_configs_dir=str(twins))))
    params = c.get("/api/twin-configs/pt").json()["params"]
    assert params == [
        {"name": "N_WS", "default": 4, "description": "How many workstations to stand up.",
         "declared": True},
        {"name": "SITE", "default": "hq",
         "description": "site tag -- informational (second line)", "declared": True},
        {"name": "N_DB", "default": 1, "description": None, "declared": False},
    ]
    (item,) = c.get("/api/twin-configs").json()["twin_configs"]
    assert item["parameterized"] is True and item["param_names"] == ["N_WS", "SITE", "N_DB"]


def test_a_parameterized_bundle_is_listed_not_skipped(tmp_path):
    root = Path(__file__).resolve().parents[2] / "catalog" / "twins"
    c = TestClient(create_app(ApiSettings(db_path=str(tmp_path / "s.db"),
                                          imprints_dir=str(root))))
    body = c.get("/api/twins").json()
    ids = {e["id"]: e for e in body["imprints"]}
    assert "corp-enterprise" in ids and ids["corp-enterprise"]["hosts"] >= 25
    assert all("non-numeric count" not in s["reason"] for s in body["skipped"])


# ---------------------------------------------------------------------------
# 5. unrecorded VMs; 6. cached checked_at
# ---------------------------------------------------------------------------
def test_range_lists_axrange_vms_no_store_records(tmp_path):
    c, db, extra, _b = _two_stores(tmp_path, adapter_conf=_adapter_conf(tmp_path / "a.conf", "pxtest1"))
    c.app.state.range_probe = lambda cfg: {"version": "9.2"}
    c.app.state.health_sweep = lambda cfg: [
        {"vmid": 201, "name": "te-aaaaaa-ws01", "status": "running", "node": "pxtest1"},  # recorded (A)
        {"vmid": 211, "name": "te-bbbbbb-srv01", "status": "running", "node": "pxtest1"},  # recorded (B)
        {"vmid": 990, "name": "te-cafe01-ghost", "status": "running", "node": "pxtest1"},
        {"vmid": 991, "name": "tfc-cafe01-x", "status": "stopped", "node": "pxtest1"},
        {"vmid": 992, "name": "XPress-SW-A", "status": "running", "node": "pxtest1"},  # not ours
        {"vmid": 993, "name": "img-debian-12", "status": "stopped", "node": "pxtest1",
         "template": 1},
    ]
    (tgt,) = c.get("/api/range").json()["targets"]
    un = tgt["unrecorded"]
    assert un["state"] == "known" and un["count"] == 2 and un["checked_at"]
    assert [v["vmid"] for v in un["vms"]] == [990, 991]
    assert un["vms"][0] == {"vmid": 990, "name": "te-cafe01-ghost", "status": "running",
                            "node": "pxtest1"}
    assert c.get("/api/range", params={"live": "false"}).json()["targets"][0]["unrecorded"] is None


def test_unrecorded_is_unknown_when_the_sweep_fails(tmp_path):
    c, db, _b = _api(tmp_path, adapter_conf=_adapter_conf(tmp_path / "a.conf", "pxtest1"))
    _seed(db)
    c.app.state.range_probe = lambda cfg: {"version": "9.2"}

    def boom(cfg):
        raise OSError("no route to host")

    c.app.state.health_sweep = boom
    un = c.get("/api/range").json()["targets"][0]["unrecorded"]
    assert un["state"] == "unknown" and "no route to host" in un["reason"] and un["count"] is None


def test_a_cached_health_answer_keeps_its_original_time_everywhere(tmp_path):
    c, db, backend = _api(tmp_path, adapter_conf=_adapter_conf(tmp_path / "a.conf", "pxtest1"))
    _seed_full(db, backend)
    c.app.state.health_sweep = lambda cfg: [{"vmid": 201, "status": "running", "node": "pxtest1"}]
    first = c.get(f"/api/deploys/{TWIN}/health").json()
    import time

    time.sleep(0.02)
    second = c.get(f"/api/deploys/{TWIN}/health").json()
    assert second["checked_at"] == first["checked_at"] == first["cluster"]["checked_at"]
    assert second["cluster"]["checked_at"] == first["cluster"]["checked_at"]
