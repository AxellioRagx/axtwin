"""A4 Grafana coverage dashboard (o2/grafana/dashboards/attack-coverage.json).

No Grafana runtime here (that's the gated live-verify); this just checks the
JSON is well-formed and references the right datasource/labels the way the
rest of the o2/grafana/dashboards/*.json convention does (mirrors
suricata-alerts.json's shape).
"""
from __future__ import annotations

import json
from pathlib import Path

DASHBOARD = (
    Path(__file__).resolve().parents[2] / "o2" / "grafana" / "dashboards" / "attack-coverage.json"
)


def _load() -> dict:
    return json.loads(DASHBOARD.read_text())


def test_dashboard_file_exists():
    assert DASHBOARD.is_file()


def test_dashboard_is_valid_json():
    doc = _load()
    assert doc["title"]
    assert doc["uid"] == "axr-attack-coverage"


def test_dashboard_panels_use_loki_datasource():
    doc = _load()
    assert doc["panels"], "dashboard has no panels"
    for panel in doc["panels"]:
        assert panel["datasource"] == {"type": "loki", "uid": "axr-loki"}
        for target in panel.get("targets", []):
            assert target["datasource"] == {"type": "loki", "uid": "axr-loki"}


def test_dashboard_queries_reference_truth_and_detection_streams():
    doc = _load()
    exprs = [
        t["expr"]
        for p in doc["panels"]
        for t in p.get("targets", [])
    ]
    assert any('job="axr-truth"' in e for e in exprs), "no query reads the ground-truth stream"
    assert any('job="suricata"' in e for e in exprs), "no query reads the detection stream"


def test_dashboard_truth_queries_extract_technique_host_run_via_pattern():
    doc = _load()
    exprs = [
        t["expr"]
        for p in doc["panels"]
        for t in p.get("targets", [])
        if 'job="axr-truth"' in t["expr"]
    ]
    assert exprs
    for e in exprs:
        assert "| pattern" in e
        assert "<technique>" in e
        assert "<host>" in e
        assert "<run>" in e


def test_dashboard_has_run_id_and_activity_template_vars():
    doc = _load()
    names = {v["name"] for v in doc["templating"]["list"]}
    assert "run_id" in names
    assert "activity" in names


def test_dashboard_tagged_for_discovery():
    doc = _load()
    for tag in ("axrange", "measurement"):
        assert tag in doc["tags"]
