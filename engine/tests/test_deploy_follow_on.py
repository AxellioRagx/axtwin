"""The console's Deploy stands the whole twin up: te, then tap, then tfc
(the CLI's sequence), each tier's outcome on the te row's `follow_on`
(2026-11-09, item 3 of the full-cycle test). A tier the twin has no plan
for is skipped; one that fails is failed, and the next tier still runs.
"Start tap" / "Start tfc" stand one tier up on a te that has none."""
from __future__ import annotations

from tests.test_api_edit_model import (  # noqa: F401 -- the shared fixture
    AUTH, MANIFEST, TWIN, _client, _seed_sensors, env,
)


def _deploy_twin(c, **body):
    r = c.post(f"/api/twins/{TWIN}/deploy", json={"target_node": "pxtest1", **body}, headers=AUTH)
    assert r.status_code == 200, r.text
    return r.json()["deploy_id"]


def _row(c, did):
    (row,) = [d for d in c.get("/api/deploys").json()["deploys"] if d["deploy_id"] == did]
    return row


def _imprints(settings, catalog):
    settings.imprints_dir = str(catalog)


def test_deploy_runs_te_then_tap_then_tfc_and_records_each(env):
    settings, catalog = env
    _imprints(settings, catalog)
    _seed_sensors(settings)
    c, backend, adapter = _client(settings)
    did = _deploy_twin(c, follow_on=["tfc", "tap"])
    row = _row(c, did)
    assert row["status"] == "completed"
    fo = row["follow_on"]
    assert fo["tap"]["status"] == "completed" and fo["tap"]["deploy_id"]
    assert fo["tfc"]["status"] == "completed" and fo["tfc"]["deploy_id"]
    assert [x[1]["sensor_id"] for x in adapter.calls if x[0] == "attach-gre-tunnel"] == ["pktxpress-1"]
    assert backend.calls, "the tfc playout assigned personas"
    # The detail read carries it too.
    assert c.get(f"/api/deploys/{did}").json()["deploy"]["follow_on"]["tfc"]["status"] == "completed"
    # te only, as before, without follow_on.
    assert c.post("/api/twins/x/deploy", json={"follow_on": ["exr"]}, headers=AUTH).status_code == 400


def test_a_twin_with_no_taps_skips_tap_and_still_stands_tfc_up(env):
    settings, catalog = env
    _imprints(settings, catalog)
    (catalog / TWIN / "tap" / "default.yaml").unlink()
    (catalog / TWIN / "twin.yaml").write_text(MANIFEST.replace("    tap:\n      default: tap/default.yaml\n", ""))
    c, _, adapter = _client(settings)
    did = _deploy_twin(c, follow_on=["tap", "tfc"])
    fo = _row(c, did)["follow_on"]
    assert fo["tap"] == {**fo["tap"], "status": "skipped", "reason": "the twin has no taps"}
    assert fo["tfc"]["status"] == "completed"
    assert not [x for x in adapter.calls if x[0] == "attach-gre-tunnel"]


def test_a_failing_tap_does_not_block_tfc(env):
    settings, catalog = env
    _imprints(settings, catalog)
    # A cast's plan: the binding names no sensor -- the tap playout refuses it.
    (catalog / TWIN / "tap" / "default.yaml").write_text(
        "tap-plan:\n  target: edit-twin\n  bindings:\n    - tap: corp\n      mode: exclusive\n")
    c, _, _ = _client(settings)
    did = _deploy_twin(c, follow_on=["tap", "tfc"])
    fo = _row(c, did)["follow_on"]
    assert fo["tap"]["status"] == "failed" and "sensors" in fo["tap"]["reason"]
    assert fo["tfc"]["status"] == "completed"


def test_start_tier_stands_one_tier_up_and_refuses_a_standing_one(env):
    settings, catalog = env
    _imprints(settings, catalog)
    _seed_sensors(settings)
    c, _, adapter = _client(settings)
    did = _deploy_twin(c)  # te only
    assert _row(c, did)["follow_on"] == {}
    r = c.post(f"/api/deploys/{did}/tiers/tap", json={"target_node": "pxtest1"}, headers=AUTH)
    assert r.status_code == 202, r.text
    assert _row(c, did)["follow_on"]["tap"]["status"] == "completed"
    assert [x[1]["sensor_id"] for x in adapter.calls if x[0] == "attach-gre-tunnel"] == ["pktxpress-1"]
    r = c.post(f"/api/deploys/{did}/tiers/tap", headers=AUTH)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "tier-standing"
    assert c.post(f"/api/deploys/{did}/tiers/exr", headers=AUTH).status_code == 400
    r = c.post(f"/api/deploys/{did}/tiers/tfc", json={"target_node": "pxtest1"}, headers=AUTH)
    assert r.status_code == 202 and _row(c, did)["follow_on"]["tfc"]["status"] == "completed"


# ---------------------------------------------------------------------------
# trafficgen (canvas task item 6): generators before taps, and taps that see them
# ---------------------------------------------------------------------------
LINES_TFC = """traffic-profile:
  target: edit-twin
  budget: { cps: 100, mbps: 60 }
  emulated:
    - { group: h-a, count: 2 }
    - { group: h-b, count: 1 }
  lines:
    - { from: h-a, to: h-b, mix: web-browse, rate_cps: 5 }
"""


def _record_replays(monkeypatch):
    from axrange.api import dtos, service

    calls = []

    def fake_tfc(settings, te, path, **kw):
        calls.append("tfc")
        return dtos.TrafficPlanStatusDTO(deploy_id=f"tfc-{te.deploy_id}", twin=te.scenario_id, status="completed")

    def fake_tap(settings, te, path, **kw):
        calls.append("tap")
        return type("T", (), {"deploy_id": f"tap-{te.deploy_id}"})()

    monkeypatch.setattr(service, "_replay_tfc", fake_tfc)
    monkeypatch.setattr(service, "_replay_tap", fake_tap)
    return calls


def test_a_profile_with_lines_runs_tfc_before_tap(env, monkeypatch):
    settings, catalog = env
    _imprints(settings, catalog)
    calls = _record_replays(monkeypatch)
    c, _, _ = _client(settings)
    _deploy_twin(c, follow_on=["tap", "tfc"])
    assert calls == ["tap", "tfc"]          # no lines: as before
    calls.clear()
    (catalog / TWIN / "tfc" / "default.yaml").write_text(LINES_TFC)
    did = _deploy_twin(c, follow_on=["tap", "tfc"])
    assert calls == ["tfc", "tap"]          # generators first, so the spans name their legs
    fo = _row(c, did)["follow_on"]
    assert fo["tfc"]["status"] == "completed" and fo["tap"]["status"] == "completed"


def test_with_generator_legs_adds_the_tfc_generators_to_the_tap_inventory(tmp_path):
    """The standing tfc deploy's generators join the twin's hosts (their legs
    become tap interfaces), and a generator leg on a native switch becomes a
    port of that switch with its OVS port, so the switch's SPAN names it."""
    from axrange.o2control.taps import _with_generator_legs
    from axrange.planner import StateStore
    from axrange.planner.inventory import SwitchInfo, TwinInventory
    from axrange.planner.state import DeployStatus
    from tests.test_traffic_tier import _op_row
    from tests.test_trafficgen_driver import _composed

    db = str(tmp_path / "s.db")
    plan, ids = _composed()
    store = StateStore(db)
    te = "three-tier-dc-manual-abc12345"
    tfc = f"tfc-{te}"
    store.create_deploy(deploy_id=te, scenario_id="three-tier-dc-manual", scenario_version=1,
                        scenario_schema_version=1, plan_id="p", ir_hash="h", placement={}, tier="te")
    twin = TwinInventory(deploy_id=te, switches=[SwitchInfo(name="rack2-tor", bridge="tesw-x")])
    assert _with_generator_legs(store, te, twin) is twin       # no tfc deploy: unchanged
    store.create_deploy(deploy_id=tfc, scenario_id=f"tfc-{te}", scenario_version=None,
                        scenario_schema_version=1, plan_id=f"tfc-{te}", ir_hash="", placement={}, tier="tfc")
    rows = []
    for i in ids:
        op = plan.ops[i]
        out = {"vmid": 4242} if op.op_name == "clone-vm" else {"ok": True}
        if op.op_name == "attach-vm-to-switch-port":
            out = {"ovs_port": f"tap4242i{op.inputs_resolved['nic_index']}"}
        rows.append(_op_row(tfc, op.op_id, op.op_name, op.op_catalog_id, op.scope,
                            inputs=op.inputs_resolved, outputs=out))
    store.insert_ops(tfc, rows)
    store.update_deploy_status(tfc, DeployStatus.COMPLETED)
    out = _with_generator_legs(store, te, twin)
    gens = {h.host_id: sorted((n.net_id, n.nic_index) for n in h.nets) for h in out.hosts}
    assert gens == {"gen-internet--rack2-lan": [("internet", 0), ("rack2-lan", 1)],
                    "gen-rack1-lan--rack3-lan": [("rack1-lan", 0), ("rack3-lan", 1)]}
    (sw,) = out.switches
    assert [(p.port, p.ovs_port, p.native) for p in sw.ports] == [("gen-internet--rack2-lan.1", "tap4242i1", "rack2-lan")]
    store.close()
