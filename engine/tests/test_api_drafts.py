"""Drafts (`api/Spec.md` A3d): `/api/drafts` keeps a document that FAILS
validation, outside the twin catalog, never deployable."""
from __future__ import annotations

import pytest

from tests.test_api_edit_model import (
    AUTH, TE, TWIN, _client, _doc, _edited, env,  # noqa: F401 -- env is a fixture
)

try:
    from starlette.testclient import TestClient  # noqa: F401
except ImportError:  # pragma: no cover
    pytest.skip("starlette/httpx not installed", allow_module_level=True)

DRAFT = "broken-twin"
CHECK = {"disposition": "FAIL", "errors": 1, "warnings": 0, "checked_at": "2026-11-20T10:00:00Z"}


def _bad_files(c, draft_id: str = DRAFT) -> dict[str, str]:
    files = {rel: text.replace("edit-twin", draft_id) for rel, text in _doc(c).items()}
    files["te.yaml"] = files["te.yaml"].replace("debian-12-cloud@v1", "no-such-image@v9")
    return files


@pytest.fixture
def cl(env, tmp_path):  # noqa: F811
    settings, catalog = env
    settings.drafts_dir = str(tmp_path / "drafts")
    c, _, _ = _client(settings)
    return c, settings, catalog


def _post(c, **kw):
    body = {"id": DRAFT, "files": _bad_files(c), "origin": TWIN, "last_check": CHECK}
    body.update(kw)
    return c.post("/api/drafts", json=body, headers=AUTH)


def test_a_failing_document_is_stored_and_reads_back(cl):
    c, settings, _ = cl
    files = _bad_files(c)
    # The same document is refused as a twin ...
    r = c.post("/api/twin-configs", json={"id": DRAFT, "files": files}, headers=AUTH)
    assert r.status_code in (400, 422)
    # ... and kept as a draft.
    r = _post(c)
    assert r.status_code == 201, r.text
    j = r.json()
    assert j["id"] == DRAFT and j["draft"] is True and j["origin"] == TWIN
    assert j["last_check"] == CHECK and j["document"]["files"] == files
    assert j["description"] and j["created_at"] and j["updated_at"]
    assert ".draft.json" not in j["document"]["files"]
    # Open read: summary only; token read: document.
    r = c.get(f"/api/drafts/{DRAFT}")
    assert r.status_code == 200 and r.json()["document"] is None
    r = c.get(f"/api/drafts/{DRAFT}", headers=AUTH)
    assert r.json()["document"]["files"] == files and r.json()["origin"] == TWIN
    assert [d["id"] for d in c.get("/api/drafts").json()["drafts"]] == [DRAFT]
    assert c.get("/api/drafts/nope").status_code == 404


def test_a_draft_is_never_deployable(cl):
    c, _, _ = cl
    assert _post(c).status_code == 201
    for r in (
        c.post(f"/api/twins/{DRAFT}/plan", json={"target_node": "pxtest1"}),
        c.post(f"/api/twins/{DRAFT}/deploy", json={"target_node": "pxtest1"}, headers=AUTH),
        c.post("/api/deploys", json={"config": DRAFT, "target_node": "pxtest1"}, headers=AUTH),
    ):
        assert r.status_code == 409, r.text
        assert r.json()["detail"]["code"] == "draft", r.text


def test_a_draft_is_in_no_twin_listing(cl):
    c, _, catalog = cl
    assert _post(c).status_code == 201
    assert DRAFT not in {e["id"] for e in c.get("/api/twin-configs").json()["twin_configs"]}
    body = c.get("/api/twins").json()
    assert DRAFT not in {e["id"] for e in body.get("imprints", body.get("twins", []))}
    assert not (catalog / DRAFT).exists()


def test_put_twin_config_still_refuses_an_invalid_document(cl):
    c, _, _ = cl
    bad = _edited(_doc(c), **{"te.yaml": TE.replace("debian-12-cloud@v1", "no-such-image@v9")})
    r = c.put(f"/api/twin-configs/{TWIN}", json={"files": bad}, headers=AUTH)
    assert r.status_code == 422, r.text


def test_put_delete_collision_and_identity(cl):
    c, settings, _ = cl
    assert _post(c).status_code == 201
    # 409: a draft with that id, or a twin with that id.
    assert _post(c).status_code == 409
    assert _post(c, id=TWIN, files=_doc(c)).status_code == 409
    # Save-As onto a draft id is refused.
    r = c.post("/api/twin-configs", json={"id": DRAFT, "files": _doc(c)}, headers=AUTH)
    assert r.status_code == 409
    # 400: identity mismatch, unsafe origin, escaping path.
    assert _post(c, id="other").status_code == 400
    assert _post(c, id="o2", files=_bad_files(c, "o2"), origin="../x").status_code == 400
    assert _post(c, id="o3", files={"../e.yaml": "x: 1\n"}).status_code == 400
    # PUT: overwrite, keep created_at/origin, clear last_check with null.
    before = c.get(f"/api/drafts/{DRAFT}", headers=AUTH).json()
    files = _bad_files(c)
    files["README.md"] = "changed\n"
    files.pop("tap/default.yaml")
    r = c.put(f"/api/drafts/{DRAFT}", json={"files": files, "last_check": None}, headers=AUTH)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["document"]["files"] == files and j["origin"] == TWIN
    assert j["last_check"] is None and j["created_at"] == before["created_at"]
    r = c.put(f"/api/drafts/{DRAFT}", json={"files": files, "origin": "elsewhere"}, headers=AUTH)
    assert r.json()["origin"] == "elsewhere"
    assert c.put(f"/api/drafts/{DRAFT}", json={"id": "x", "files": files}, headers=AUTH).status_code == 400
    assert c.put("/api/drafts/nope", json={"files": files}, headers=AUTH).status_code == 404
    # DELETE
    assert c.delete(f"/api/drafts/{DRAFT}", headers=AUTH).status_code == 204
    assert c.delete(f"/api/drafts/{DRAFT}", headers=AUTH).status_code == 404
    assert c.get(f"/api/drafts/{DRAFT}").status_code == 404


def test_drafts_dir_may_not_be_a_twin_search_dir(env):
    settings, catalog = env
    settings.drafts_dir = str(catalog)
    c, _, _ = _client(settings)
    assert c.get("/api/drafts").status_code in (500, 503)
    # ... and the twin catalog's own paths are unaffected.
    assert c.get("/api/twin-configs").status_code == 200


def test_default_drafts_dir_sits_beside_the_catalog(env):
    settings, catalog = env
    settings.drafts_dir = ""
    c, _, _ = _client(settings)
    assert _post(c).status_code == 201
    assert (catalog.parent / "drafts" / DRAFT / ".draft.json").is_file()


def test_draft_writes_are_token_gated(cl):
    c, _, _ = cl
    body = {"id": DRAFT, "files": _bad_files(c)}
    assert c.post("/api/drafts", json=body).status_code == 401
    assert _post(c).status_code == 201
    assert c.put(f"/api/drafts/{DRAFT}", json=body).status_code == 401
    assert c.delete(f"/api/drafts/{DRAFT}").status_code == 401
