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
