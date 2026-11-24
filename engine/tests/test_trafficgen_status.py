"""trafficgen B6 (`engine/src/axrange/trafficgen/SPEC.md`): per-line counters
in the tfc status the API serves, and the live rate change endpoint, with the
generators faked behind the persona-backend seam."""
from __future__ import annotations

import json

import pytest

from axrange.planner import StateStore
from tests.test_api_edit_model import AUTH, _client, env  # noqa: F401 -- env is a fixture
from tests.test_trafficgen_driver import LINE, _composed
from tests.test_traffic_tier import _op_row


class _GenBackend:
    """Answers the generator controller's status and records update-traffic."""

    def __init__(self):
        self.updates = []

    def generator_status(self, node, vmid):
        return {"lines": {LINE: {"state": "running", "opened": 120, "established": 118, "errors": 0,
                                 "bytes": 4_200_000, "open": 9, "cps_now": 30.1}}}

    def run(self, op, inp, ctx):
        self.updates.append((op.op_name, inp["generator"], inp["line"], inp["mult"]))
        return {"updated": True}


def _seed(db):
    plan, ids = _composed()
    store = StateStore(db)
    te, tfc = "three-tier-dc-manual-abc123", "tfc-three-tier-dc-manual-abc123-0001"
    store.create_deploy(deploy_id=te, scenario_id="three-tier-dc-manual", scenario_version=1,
                        scenario_schema_version=1, plan_id="p", ir_hash="h", placement={}, tier="te")
    store.create_deploy(deploy_id=tfc, scenario_id=f"tfc-{te}", scenario_version=None,
                        scenario_schema_version=1, plan_id=f"tfc-{te}", ir_hash="", placement={}, tier="tfc")
    rows = []
    for i in ids:
        op = plan.ops[i]
        rows.append(_op_row(tfc, op.op_id, op.op_name, op.op_catalog_id, op.scope, inputs=op.inputs_resolved,
                            outputs={"vmid": 4242} if op.op_name == "clone-vm" else {"ok": True}))
    store.insert_ops(tfc, rows)
    from axrange.planner.state import DeployStatus
    store.update_deploy_status(tfc, DeployStatus.COMPLETED)
    store.close()
    return te, tfc


def test_the_tfc_status_carries_live_line_counters(env):  # noqa: F811
    settings, _ = env
    te, tfc = _seed(settings.db_path)
    c, _, _ = _client(settings, persona_backend=_GenBackend())
    j = c.get("/api/tfc", params={"deploy_id": tfc}).json()
    row = j["lines"][LINE]
    assert row["generator"] == "gen-internet--rack2-lan" and row["established"] == 118
    assert row["rate_cps"] == 60.0 and row["mult"] == 2.0          # the recorded multiplier from compose
    assert j["lines"]["rack1-web--rack3-db--sql-oltp"]["generator"] == "gen-rack1-lan--rack3-lan"


def test_post_traffic_line_changes_the_rate_live(env):  # noqa: F811
    settings, _ = env
    te, tfc = _seed(settings.db_path)
    be = _GenBackend()
    c, _, _ = _client(settings, persona_backend=be)
    r = c.post(f"/api/deploys/{tfc}/traffic-lines/{LINE}", json={"rate_cps": 45}, headers=AUTH)
    assert r.status_code == 200, r.text
    assert r.json()["mult"] == pytest.approx(1.5)
    assert be.updates == [("update-traffic", "gen-internet--rack2-lan", LINE, 1.5)]
    assert c.get("/api/tfc", params={"deploy_id": tfc}).json()["lines"][LINE]["rate_cps"] == 45.0
    over = c.post(f"/api/deploys/{tfc}/traffic-lines/{LINE}", json={"rate_cps": 500}, headers=AUTH)
    assert over.status_code == 409 and "over budget" in json.dumps(over.json())
    nope = c.post(f"/api/deploys/{tfc}/traffic-lines/nope", json={"rate_cps": 5}, headers=AUTH)
    assert nope.status_code == 404
    assert c.post(f"/api/deploys/{tfc}/traffic-lines/{LINE}", json={"rate_cps": 5}).status_code in (401, 403)


def test_a_profile_without_lines_has_an_empty_lines_map(env):  # noqa: F811
    settings, _ = env
    store = StateStore(settings.db_path)
    store.create_deploy(deploy_id="tfc-x-1", scenario_id="tfc-x", scenario_version=None, scenario_schema_version=1,
                        plan_id="tfc-x", ir_hash="", placement={}, tier="tfc")
    store.close()
    c, _, _ = _client(settings, persona_backend=_GenBackend())
    assert c.get("/api/tfc", params={"deploy_id": "tfc-x-1"}).json()["lines"] == {}
