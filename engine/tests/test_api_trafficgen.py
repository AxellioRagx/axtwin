"""trafficgen through the API (canvas task item 2, 2026-11-24): `POST /api/tfc`
carries `budget` / `emulated` / `lines` into the same parser and apply path as
a tfc file; the twin document round-trips a profile that has them; Check runs
the TGN rules and the generator planner on it."""
from __future__ import annotations

from pathlib import Path

import pytest

from axrange.api import service
from tests.test_api_edit_model import AUTH, _client, _settings, _write_catalog  # noqa: F401

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "catalog" / "twins" / "three-tier-dc-manual"
TWIN = "three-tier-dc-manual"


def _files() -> dict[str, str]:
    return {rel: (SRC / rel).read_text(encoding="utf-8")
            for rel in ("twin.yaml", "te.yaml", "tfc/default.yaml", "tap/default.yaml")}


GATE = (SRC / "tfc" / "trafficgen.yaml").read_text(encoding="utf-8")


@pytest.fixture
def tg_env(tmp_path):
    catalog = tmp_path / "twins"
    catalog.mkdir()
    _write_catalog(catalog, TWIN, _files())
    settings = _settings(tmp_path)
    settings.twin_configs_dir = str(catalog)
    settings.api_token = "s3cret"
    return settings, catalog


def _validate(c, files):
    r = c.post("/api/validate", json={"document": {"id": TWIN, "files": files}})
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------------------
# POST /api/tfc
# ---------------------------------------------------------------------------
def test_post_tfc_carries_budget_emulated_and_lines_into_the_apply(tg_env, monkeypatch):
    settings, _ = tg_env
    seen = {}

    def fake_apply(**kw):
        seen.update(kw)
        from axrange.o2control.traffic import TrafficPlanStatus
        return TrafficPlanStatus(deploy_id="tfc-x-1", twin="x", status="completed", assignments=[])

    monkeypatch.setattr("axrange.o2control.traffic.apply_traffic_profile", fake_apply)
    monkeypatch.setattr(service, "_load_manifests", lambda root: "MANIFESTS")
    c, _, _ = _client(settings)
    body = {
        "target": "three-tier-dc-manual-0123abcd",
        "budget": {"cps": 100, "mbps": 60},
        "emulated": [{"group": "rack1-web", "count": 4},
                     {"group": "internet-users", "external": True, "segment": "internet",
                      "role": "internet-user", "count": 200}],
        "lines": [{"from": "internet-users", "to": "rack1-web", "mix": "web-browse", "rate_cps": 30},
                  {"id": "x", "from": "rack1-web", "to": "internet-users", "mix": "web-browse",
                   "rate_cps": 5, "max_concurrent": 7}],
    }
    r = c.post("/api/tfc", json=body, headers=AUTH)
    assert r.status_code == 200, r.text
    spec = seen["spec"]
    assert spec.budget.cps == 100 and spec.budget.mbps == 60
    assert [(e.group, e.count, e.external) for e in spec.emulated] == [
        ("rack1-web", 4, False), ("internet-users", 200, True)]
    assert [(ln.id, ln.from_group, ln.to_group, ln.rate_cps, ln.max_concurrent) for ln in spec.lines] == [
        ("internet-users--rack1-web--web-browse", "internet-users", "rack1-web", 30.0, 300),
        ("x", "rack1-web", "internet-users", 5.0, 7)]
    assert seen["manifests"] == "MANIFESTS"   # the generator template is findable from the API


def test_post_tfc_refuses_a_bad_trafficgen_block_like_the_file_parser(tg_env, monkeypatch):
    settings, _ = tg_env
    monkeypatch.setattr("axrange.o2control.traffic.apply_traffic_profile",
                        lambda **kw: pytest.fail("must not apply"))
    c, _, _ = _client(settings)
    line = {"from": "a", "to": "b", "mix": "web-browse", "rate_cps": 3}
    # lines without a budget: the parser's own rule
    r = c.post("/api/tfc", json={"target": "t", "emulated": [{"group": "a", "count": 1}], "lines": [line]},
               headers=AUTH)
    assert r.status_code == 400 and "budget" in r.text
    # an unknown key inside a block: refused before the parser
    r = c.post("/api/tfc", json={"target": "t", "budget": {"cps": 1, "mbps": 1},
                                 "lines": [{**line, "colour": "red"}]}, headers=AUTH)
    assert r.status_code == 422
    # an external group without its segment: the parser's own rule
    r = c.post("/api/tfc", json={"target": "t", "emulated": [{"group": "u", "count": 2, "external": True}]},
               headers=AUTH)
    assert r.status_code == 400 and "segment" in r.text


# ---------------------------------------------------------------------------
# The twin document
# ---------------------------------------------------------------------------
def test_the_document_round_trips_a_tfc_profile_with_lines(tg_env):
    settings, catalog = tg_env
    c, _, _ = _client(settings)
    files = _files()
    files["tfc/default.yaml"] = GATE
    assert _validate(c, files)["errors"] == 0
    r = c.put(f"/api/twin-configs/{TWIN}", json={"files": files}, headers=AUTH)
    assert r.status_code == 200, r.text
    got = c.get(f"/api/twin-configs/{TWIN}", headers=AUTH).json()["document"]["files"]
    assert got["tfc/default.yaml"] == GATE
    assert (catalog / TWIN / "tfc" / "default.yaml").read_text() == GATE
    assert got["te.yaml"] == files["te.yaml"]   # te is never written by a traffic edit
    # Save As under a new id carries it too.
    clone = {k: v.replace("three-tier-dc-manual", "three-tier-dc-tg") for k, v in files.items()}
    r = c.post("/api/twin-configs", json={"id": "three-tier-dc-tg", "files": clone}, headers=AUTH)
    assert r.status_code == 201, r.text
    assert "emulated:" in (catalog / "three-tier-dc-tg" / "tfc" / "default.yaml").read_text()


def test_check_reports_tgn_findings_and_the_two_generator_refusal(tg_env):
    settings, catalog = tg_env
    c, _, _ = _client(settings)
    files = _files()
    # TGN-010: a line from a group with no emulated instances.
    files["tfc/default.yaml"] = GATE.replace("    - { group: rack1-web, count: 20 }\n", "")
    j = _validate(c, files)
    assert j["disposition"] == "FAIL"
    tgn = [f for f in j["findings"] if f["rule_id"].startswith("TGN-")]
    assert [f["rule_id"] for f in tgn] == ["TGN-010"] and tgn[0]["location"] == "tfc/default.yaml"
    assert "lines[0]" in tgn[0]["message"]
    # Save refuses it and leaves the file as it was.
    r = c.put(f"/api/twin-configs/{TWIN}", json={"files": files}, headers=AUTH)
    assert r.status_code == 400 and "TGN-010" in r.text
    # rack1-web serving internet users AND calling rack3-db: two generators (the planner limit).
    two = GATE.replace(
        "    - { from: internet-users, to: rack2-web, mix: web-browse, rate_cps: 30 }",
        "    - { from: internet-users, to: rack1-web, mix: web-browse, rate_cps: 30 }")
    files["tfc/default.yaml"] = two
    j = _validate(c, files)
    (f,) = [f for f in j["findings"] if f["severity"] == "error"]
    assert f["rule_id"] == "TIER-LOAD" and "two generators" in f["message"]
    assert "rack1-web--rack3-db--sql-oltp" in f["message"] and "internet-users--rack1-web--web-browse" in f["message"]
