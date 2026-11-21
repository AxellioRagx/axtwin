"""The canvas Add panel's read side (`docs/design/canvas-add-panel.md` §4.2,
§4.3; D1, D2): persona display metadata (`category`, `label`, `blurb`) with
its fallback, and `GET /api/image-profiles`."""
from __future__ import annotations

import pytest

from axrange.personas.registry import PERSONAS
from axrange.personas.schema import PERSONA_CATEGORIES, load_persona

from tests.test_api_edit_model import _client, env  # noqa: F401 -- env is a fixture

try:
    from starlette.testclient import TestClient  # noqa: F401
except ImportError:  # pragma: no cover
    pytest.skip("starlette/httpx not installed", allow_module_level=True)


def _p(**extra):
    return {"persona": {"id": "x-role", "os_family": "linux", "drive": "scripted", "emits": [], **extra}}


def test_metadata_loads_when_present():
    p = load_persona(_p(category="business", label="File server", blurb=" Serves shares. "))
    assert (p.category, p.label, p.blurb) == ("business", "File server", "Serves shares.")
    assert p.effective_category() == "business"


@pytest.mark.parametrize("pid,want", [("x-role", "other"), ("loadgen-l9", "traffic"), ("xseg-a-to-b", "traffic")])
def test_missing_metadata_still_loads_with_the_fallback(pid, want):
    raw = _p()
    raw["persona"]["id"] = pid
    p = load_persona(raw)
    assert (p.category, p.label, p.blurb) == ("", "", "")
    assert p.effective_category() == want


def test_a_non_string_label_is_a_shape_error():
    from axrange.personas.schema import PersonaSchemaError

    with pytest.raises(PersonaSchemaError):
        load_persona(_p(label=["a"]))


def test_every_catalog_persona_names_a_known_category_label_and_blurb():
    for pid, p in PERSONAS.items():
        assert p.category in PERSONA_CATEGORIES, pid
        assert p.label and p.blurb, pid
        if pid.startswith(("loadgen-", "xseg-")):
            assert p.category == "traffic", pid


def test_get_personas_serves_the_metadata_and_the_fallback(env, tmp_path):  # noqa: F811
    settings, catalog = env
    c, _, _ = _client(settings)
    j = c.get("/api/personas").json()
    by = {p["id"]: p for p in j["personas"]}
    fs = by["file-server"]
    assert fs["category"] == "business" and fs["label"] == "File server" and fs["blurb"]
    # A twin-local persona with no metadata: the fallback rule, the id as its label.
    d = catalog / "edit-twin" / "personas"
    d.mkdir(parents=True, exist_ok=True)
    (d / "loadgen-local.yaml").write_text("persona:\n  id: loadgen-local\n  os_family: linux\n  drive: scripted\n  emits: []\n")
    (d / "plain-local.yaml").write_text("persona:\n  id: plain-local\n  os_family: linux\n  drive: scripted\n  emits: []\n")
    j = c.get("/api/personas", params={"twin": "edit-twin"}).json()
    by = {p["id"]: p for p in j["personas"]}
    assert (by["loadgen-local"]["category"], by["loadgen-local"]["label"], by["loadgen-local"]["blurb"]) == ("traffic", "loadgen-local", "")
    assert by["plain-local"]["category"] == "other"


def test_image_profiles_read_the_file_beside_the_catalog(env):  # noqa: F811
    settings, catalog = env
    (catalog.parent / "image-profiles.yaml").write_text(
        "profiles:\n"
        "  linux-server: { image: debian-12-cloud@v1 }\n"
        "  linux-client: { image: null }\n"
        "  router: {}\n"
    )
    c, _, _ = _client(settings)
    r = c.get("/api/image-profiles")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["profiles"] == [
        {"name": "linux-client", "image": None, "platforms": {}},
        {"name": "linux-server", "image": "debian-12-cloud@v1", "platforms": {}},
        {"name": "router", "image": None, "platforms": {}},
    ]
    assert any("linux-client" in w for w in j["warnings"])


def test_image_profiles_without_a_file_are_empty_with_a_warning(env):  # noqa: F811
    settings, _ = env
    c, _, _ = _client(settings)
    j = c.get("/api/image-profiles").json()
    assert j["profiles"] == [] and j["warnings"]


def test_a_malformed_image_profiles_file_is_a_500_naming_it(env):  # noqa: F811
    settings, catalog = env
    (catalog.parent / "image-profiles.yaml").write_text("profiles: [a, b]\n")
    c, _, _ = _client(settings)
    r = c.get("/api/image-profiles")
    assert r.status_code == 500 and "image-profiles.yaml" in r.text


def test_image_profiles_is_read_only(env):  # noqa: F811
    settings, _ = env
    c, _, _ = _client(settings)
    assert c.post("/api/image-profiles", json={}).status_code == 405
    assert c.put("/api/image-profiles", json={}).status_code == 405


def test_the_shipped_profiles_name_images_the_catalog_twins_use():
    """Every image in catalog/image-profiles.yaml is one a catalog twin deploys."""
    import glob

    from ruamel.yaml import YAML

    from axrange.paths import CATALOG_ROOT

    used = set()
    for f in glob.glob(str(CATALOG_ROOT / "twins" / "*" / "te.yaml")):
        used |= {line.split("image:", 1)[1].strip() for line in open(f) if "image:" in line and "#" not in line.split("image:")[0]}
    raw = YAML(typ="safe").load((CATALOG_ROOT / "image-profiles.yaml").read_text())
    for name, body in raw["profiles"].items():
        img = (body or {}).get("image")
        if img:
            assert img in used, (name, img)
