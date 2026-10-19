"""Unified Console backend -- fix round 4 (live write-path run): the host-edit
job history survives re-drives (D2), and a whole-profile apply marks earlier
host edits superseded (D3). Temp stores, fake backend, no cluster."""
from __future__ import annotations

import pytest

from axrange.personas import registry as persona_registry
from tests.test_unified_console import PERSONAS, TWIN, _api, _seed_full, _tfc_id

try:
    from starlette.testclient import TestClient  # noqa: F401
except ImportError:  # pragma: no cover
    pytest.skip("starlette/httpx not installed", allow_module_level=True)

_PROFILE = {"target": TWIN, "target_node": "pxtest1", "allow_revert_host_edits": True,
            "assignments": [{"role": "client", "persona": "t-client"},
                            {"node": "srv01", "persona": "t-server"},
                            {"node": "srv02", "persona": "t-server"}]}


def _put(c, host, personas):
    r = c.put(f"/api/deploys/{TWIN}/hosts/{host}/personas",
              json={"assignments": [{"persona": p} for p in personas], "target_node": "pxtest1"})
    assert r.status_code == 200, r.text
    return r.json()


def test_job_history_survives_redrives_and_whole_profile_supersedes(tmp_path):
    c, db, backend = _api(tmp_path)
    _seed_full(db, backend)
    j1 = _put(c, "ws01", ["t-alt"])           # edit
    j2 = _put(c, "ws01", [])                  # stop-only edit
    with persona_registry.overlay(PERSONAS):
        r = c.post("/api/tfc", json=_PROFILE)  # whole-profile revert: re-drives ws01
    assert r.status_code == 200, r.text
    j3 = _put(c, "ws02", ["t-alt"])           # another edit

    tfc = _tfc_id(db)
    jobs = c.get(f"/api/deploys/{tfc}").json()["host_edits"]
    assert [j["fingerprint"] for j in jobs] == [
        j3["plan"]["fingerprint"], j2["plan"]["fingerprint"], j1["plan"]["fingerprint"],
    ]  # every job, newest first
    by_fp = {j["fingerprint"]: j for j in jobs}
    assert by_fp[j1["plan"]["fingerprint"]]["superseded"] is True
    assert by_fp[j2["plan"]["fingerprint"]]["superseded"] is True
    assert "superseded" not in by_fp[j3["plan"]["fingerprint"]]

    hosts = {h["host_id"]: h for h in c.get(f"/api/deploys/{TWIN}/hosts").json()["hosts"]}
    ws01 = hosts["ws01"]
    assert all(p["origin"] == "profile" for p in ws01["personas"] if p["status"] == "completed")
    assert ws01["host_edit"]["superseded"] is True and ws01["host_edit"]["superseded_at"]
    assert ws01["host_edit"]["fingerprint"] == j2["plan"]["fingerprint"]
    assert hosts["ws02"]["host_edit"].get("superseded") is None
