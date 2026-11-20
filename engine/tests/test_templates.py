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
