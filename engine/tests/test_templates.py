"""Canvas templates (`docs/design/canvas-add-panel.md` §5, D3): the format,
its expansion, the shipped built-in set and the read API."""
from __future__ import annotations

import shutil

import pytest
from ruamel.yaml import YAML

from axrange.api import service
from axrange.api import templates as T
from axrange.paths import CATALOG_ROOT

from tests.test_api import _settings
from tests.test_api_edit_model import _client, env  # noqa: F401 -- env is a fixture

try:
    from starlette.testclient import TestClient  # noqa: F401
except ImportError:  # pragma: no cover
    pytest.skip("starlette/httpx not installed", allow_module_level=True)

BUILTINS = sorted(p.name for p in (CATALOG_ROOT / "templates").iterdir() if (p / "template.yaml").is_file())
SECTION_5_1 = {"office-segment", "datacenter-rack", "dmz", "ad-domain", "branch-office", "three-tier-web", "ot-cell", "internet-edge"}


def _profiles() -> dict:
    raw = YAML(typ="safe").load((CATALOG_ROOT / "image-profiles.yaml").read_text())
    return {k: (v or {}).get("image") for k, v in raw["profiles"].items()}


def _builtin(tid: str) -> dict:
    return T._load_file(CATALOG_ROOT / "templates" / tid / "template.yaml")


def _validate(tmp_path, files):
    s = _settings(tmp_path)
    (tmp_path / "twins").mkdir(exist_ok=True)
    s.twin_configs_dir = str(tmp_path / "twins")
    return service._validate_twin_document(s, "tpl-check", files, raise_on_fail=False)


def test_the_section_5_1_set_ships_and_is_builtin():
    assert set(BUILTINS) == SECTION_5_1
    for tid in BUILTINS:
        tpl = _builtin(tid)
        assert tpl["id"] == tid and tpl.get("builtin") is True
        assert tpl.get("label") and tpl.get("description") and tpl.get("ports")


@pytest.mark.parametrize("tid", BUILTINS)
def test_every_builtin_expands_with_defaults_into_a_valid_twin(tid, tmp_path):
    files = T.template_document(_builtin(tid), "tpl-check", None, _profiles())
    r = _validate(tmp_path, files)
    errors = [f"{f.rule_id}: {f.message}" for f in r.findings if f.severity == "error"]
    assert r.disposition != "FAIL", errors
    # Every port names a device the expansion holds.
    x = T.expand(_builtin(tid), None, _profiles())
    names = {n["name"] for n in x["nodes"]}
    assert all(p["device"] in names for p in x["ports"]), x["ports"]


@pytest.mark.parametrize("tid,given", [("office-segment", {"windows_share": 100, "workstations": 7}), ("datacenter-rack", {"app": 0, "prefix": "r2"}), ("three-tier-web", {"web": 5, "prefix": "web2"})])
def test_builtins_stay_valid_with_other_settings(tid, given, tmp_path):
    r = _validate(tmp_path, T.template_document(_builtin(tid), "tpl-check", given, _profiles()))
    assert r.disposition != "FAIL", [f.message for f in r.findings if f.severity == "error"]


def test_expressions_and_counts():
    tpl = _builtin("office-segment")
    x = T.expand(tpl, {"workstations": 7, "windows_share": 100, "prefix": "hq"}, _profiles())
    by = {n["name"]: n for n in x["nodes"]}
    assert by["hq-ws"]["count"] == 7 and by["hq-ws"]["image"] == "win11-finance@v4"
    assert "hq-lx" not in by  # a count of 0 drops the node ...
    assert [a["node"] for a in x["assignments"]] == ["hq-ws"]  # ... and its assignment
    assert x["ports"] == [{"name": "uplink", "device": "hq-sw"}]
    assert x["layout"] == {"hq-sw": {"x": 0, "y": 0}}
    assert "profile" not in by["hq-printer"]
    # Out-of-range settings clamp; 80% of 21 rounds half up.
    x = T.expand(tpl, {"workstations": 21, "windows_share": 250})
    assert {n["name"]: n.get("count") for n in x["nodes"]}["office-ws"] == 21
    assert T.evaluate("round(21 * 80 / 100)", {}) == 17
    assert T.evaluate("max(a, 2) - min(1, 3)", {"a": 5}) == 4
    assert T.substitute("{p}-sw-{n + 1}", {"p": "x", "n": 1}) == "x-sw-2"
    with pytest.raises(T.TemplateError):
        T.evaluate("nope + 1", {})
    with pytest.raises(T.TemplateError):
        T.evaluate("1 / 0", {})


def test_shape_errors_are_named():
    base = {"id": "t1", "params": [], "fragment": {"nodes": [{"name": "a", "image": "x"}]}}
    T.check_shape(base)
    for bad in ({**base, "id": "../x"}, {**base, "params": [{"name": "a b"}]}, {**base, "params": [{"name": "a", "type": "colour"}]}, {**base, "fragment": {}}):
        with pytest.raises(T.TemplateError):
            T.check_shape(bad)


@pytest.fixture
def cl(env):  # noqa: F811
    settings, catalog = env
    shutil.copytree(CATALOG_ROOT / "templates", catalog.parent / "templates")
    c, _, _ = _client(settings)
    return c, catalog


def test_get_templates_lists_the_builtins_first(cl):
    c, _ = cl
    j = c.get("/api/templates").json()
    assert {t["id"] for t in j["templates"]} == SECTION_5_1
    t = next(t for t in j["templates"] if t["id"] == "office-segment")
    assert t["builtin"] is True and t["label"] == "Office segment"
    assert [p["name"] for p in t["params"]] == ["workstations", "windows_share", "prefix", "subnet"]
    assert t["fragment"]["nodes"][0]["name"] == "{prefix}-sw"  # as written: settings not applied


def test_get_one_template_and_path_safety(cl):
    c, _ = cl
    r = c.get("/api/templates/dmz")
    assert r.status_code == 200 and r.json()["id"] == "dmz"
    for bad in ("nope", "%2e%2e", "dmz%2F..%2Fx", "a b"):
        assert c.get(f"/api/templates/{bad}").status_code == 404, bad
    # The guard itself (a bare "." or ".." never reaches the route: the client normalises it).
    from axrange.api.service import ApiError

    for bad in (".", "..", "a/b", "../dmz"):
        with pytest.raises(ApiError):
            T.read_template(None, bad)  # refused before any path is built


def test_a_broken_template_file_is_a_warning_not_a_failed_listing(cl):
    c, catalog = cl
    d = catalog.parent / "templates" / "broken"
    d.mkdir()
    (d / "template.yaml").write_text("template: {id: broken, fragment: {}}\n")
    j = c.get("/api/templates").json()
    assert "broken" not in {t["id"] for t in j["templates"]}
    assert any("broken" in w for w in j["warnings"])
    assert c.get("/api/templates/broken").status_code == 500


# ---------------------------------------------------------------------------
# Writes (§5.3): POST / PUT / DELETE
# ---------------------------------------------------------------------------
from tests.test_api_edit_model import AUTH  # noqa: E402


def _saved(tid: str = "my-lan", **over) -> dict:
    tpl = {
        "id": tid, "label": "My LAN", "description": "saved from a test", "icon": "office",
        "params": [{"name": "users", "label": "users: N", "type": "int", "default": 3, "min": 1},
                   {"name": "prefix", "label": "Name prefix", "type": "name", "default": "lab"},
                   {"name": "subnet", "label": "Subnet", "type": "cidr", "default": "auto"}],
        "ports": [{"name": "uplink", "device": "{prefix}-gw"}],
        "fragment": {
            "segments": [{"name": "{prefix}-lan", "cidr": "10.0.0.0/24", "zone_type": "vlan"}],
            "nodes": [{"name": "{prefix}-gw", "kind": "router", "role": "gw", "image": "debian-12-cloud@v1", "net": "{prefix}-lan"},
                      {"name": "{prefix}-ws", "role": "generic-endpoint", "image": "debian-12-cloud@v1", "count": "{users}", "net": "{prefix}-lan"}],
            "assignments": [{"node": "{prefix}-ws", "persona": "generic-endpoint"}],
        },
        "layout": {"{prefix}-gw": {"x": 0, "y": 0}},
    }
    tpl.update(over)
    return tpl


@pytest.fixture
def wc(env, tmp_path):  # noqa: F811
    settings, catalog = env
    shutil.copytree(CATALOG_ROOT / "templates", catalog.parent / "templates")
    (catalog.parent / "image-profiles.yaml").write_text((CATALOG_ROOT / "image-profiles.yaml").read_text())
    settings.images_root = _settings(tmp_path).images_root
    c, _, _ = _client(settings)
    return c, catalog


def test_post_get_put_delete_a_saved_template(wc):
    c, catalog = wc
    r = c.post("/api/templates", json={"template": _saved()}, headers=AUTH)
    assert r.status_code == 201, r.text
    assert r.json()["builtin"] is False and r.json()["label"] == "My LAN"
    assert (catalog.parent / "templates" / "my-lan" / "template.yaml").is_file()
    assert "my-lan" in {t["id"] for t in c.get("/api/templates").json()["templates"]}
    # Taken: 409, never overwritten.
    assert c.post("/api/templates", json={"template": _saved(label="other")}, headers=AUTH).status_code == 409
    r = c.put("/api/templates/my-lan", json={"template": _saved(label="Renamed")}, headers=AUTH)
    assert r.status_code == 200 and c.get("/api/templates/my-lan").json()["label"] == "Renamed"
    # A body id other than the path's is refused.
    assert c.put("/api/templates/my-lan", json={"template": _saved("x")}, headers=AUTH).status_code == 400
    assert c.delete("/api/templates/my-lan", headers=AUTH).status_code == 204
    assert c.get("/api/templates/my-lan").status_code == 404
    assert c.delete("/api/templates/my-lan", headers=AUTH).status_code == 404


def test_writes_need_the_write_token(wc):
    c, _ = wc
    assert c.post("/api/templates", json={"template": _saved()}).status_code == 401
    assert c.put("/api/templates/dmz", json={"template": _saved("dmz")}).status_code == 401
    assert c.delete("/api/templates/dmz").status_code == 401


def test_builtins_are_read_only(wc):
    c, catalog = wc
    r = c.put("/api/templates/dmz", json={"template": _saved("dmz")}, headers=AUTH)
    assert r.status_code == 403 and r.json()["detail"]["code"] == "builtin"
    assert c.delete("/api/templates/dmz", headers=AUTH).status_code == 403
    assert (catalog.parent / "templates" / "dmz" / "template.yaml").is_file()
    # A saved template cannot claim to be built in, nor take a built-in's id.
    assert c.post("/api/templates", json={"template": _saved(builtin=True)}, headers=AUTH).status_code == 400
    assert c.post("/api/templates", json={"template": _saved("dmz")}, headers=AUTH).status_code == 409


def test_validated_on_save(wc):
    c, catalog = wc
    bad = _saved()
    bad["fragment"]["nodes"][1]["net"] = "no-such-segment"
    r = c.post("/api/templates", json={"template": bad}, headers=AUTH)
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["code"] == "invalid"
    assert not (catalog.parent / "templates" / "my-lan").exists()  # nothing written
    unknown_image = _saved()
    unknown_image["fragment"]["nodes"][0]["image"] = "no-such-image@v9"
    assert c.post("/api/templates", json={"template": unknown_image}, headers=AUTH).status_code == 422
    # A bad expression is refused before the validator runs.
    expr = _saved()
    expr["fragment"]["nodes"][1]["count"] = "{nope + 1}"
    assert c.post("/api/templates", json={"template": expr}, headers=AUTH).status_code == 422


@pytest.mark.parametrize("tid", ["../evil", "a/b", ".", "..", "x y", "", "-lead"])
def test_path_safety_on_write(wc, tid):
    c, catalog = wc
    r = c.post("/api/templates", json={"template": _saved(tid)}, headers=AUTH)
    assert r.status_code == 400, (tid, r.text)
    assert not (catalog.parent / "evil").exists()


def test_the_catalog_files_can_live_elsewhere(env, tmp_path):  # noqa: F811
    """AXRANGE_TEMPLATES_DIR / AXRANGE_IMAGE_PROFILES: a twin catalog in another place."""
    settings, _ = env
    settings.templates_dir = str(CATALOG_ROOT / "templates")
    settings.image_profiles = str(CATALOG_ROOT / "image-profiles.yaml")
    c, _, _ = _client(settings)
    assert {t["id"] for t in c.get("/api/templates").json()["templates"]} == SECTION_5_1
    assert len(c.get("/api/image-profiles").json()["profiles"]) == 5
