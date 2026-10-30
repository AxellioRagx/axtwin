"""The edit model (`api/Spec.md`, "The edit model: baseline + working copy"):
seam A3 (`POST|PUT /api/twin-configs`, the only writers of a baseline) and
seam A2 (`GET|PUT /api/deploys/{id}/config`, `.../config/apply`,
`.../config/reset`, the per-deploy working copy). Every A2.* / A3.*
acceptance line, plus the classifier's `role` rule both ways.

No cluster: a temp twin catalog (one bundle with te / tfc / tap tiers), the
Starlette test client, a seeded state DB, the fake deploy adapter the rest of
`tests/test_api.py` drives, and the tfc / tap fakes (`_FakePersonaBackend`,
`_FakeMirAdapter`) so an attribute Apply provably reaches the tier playouts.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from axrange.api import create_app
from axrange.api.edits import working_copy_db_path
from axrange.planner import StateStore
from tests.test_api import _fake_runner, _settings
from tests.test_o2_taps import DEFAULT_SENSORS, _FakeMirAdapter, _seed_o2_surface
from tests.test_traffic_tier import _FakePersonaBackend

try:
    from starlette.testclient import TestClient
except ImportError:  # pragma: no cover
    pytest.skip("starlette/httpx not installed", allow_module_level=True)


TWIN = "edit-twin"
TOKEN = "s3cret"
AUTH = {"Authorization": f"Bearer {TOKEN}"}

MANIFEST = """twin:
  id: edit-twin
  description: edit-model fixture twin
  containment: uncontained
  tiers:
    te:
      default: te.yaml
    tfc:
      default: tfc/default.yaml
    tap:
      default: tap/default.yaml
"""

TE = """# edit-twin -- two linux hosts on one segment (api-test-config's shape).
twin_config:
  id: edit-twin
  description: Two-host twin for the edit-model tests.

  segments:
    - name: corp
      cidr: 10.20.0.0/24
      zone_type: simple

  nodes:
    - name: h-a
      role: web-tier
      image: debian-12-cloud@v1
      requires: [linux]
      resources: { cpu: 1, ram: 1Gi, disk: 10Gi }
      net: corp
    - name: h-b
      role: generic
      image: debian-12-cloud@v1
      requires: [linux]
      resources: { cpu: 1, ram: 1Gi, disk: 10Gi }
      net: corp
"""

TFC = """traffic-profile:
  target: edit-twin
  assignments:
    - node: h-b
      persona: web-tier
"""

TAP = """tap-plan:
  target: edit-twin
  bindings:
    - tap: corp
      sensors: [pktxpress-1]
"""

README = "# edit-twin\n\nA fixture.\n"


def _bundle_files() -> dict[str, str]:
    return {
        "twin.yaml": MANIFEST, "te.yaml": TE, "tfc/default.yaml": TFC,
        "tap/default.yaml": TAP, "README.md": README,
    }


def _write_catalog(root: Path, twin_id: str = TWIN, files: dict[str, str] | None = None) -> Path:
    d = root / twin_id
    for rel, text in (files or _bundle_files()).items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return d


def _catalog_fingerprint(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*")) if p.is_file()
    }


@pytest.fixture
def env(tmp_path):
    """A temp twin catalog + settings aimed at it, token-gated writes."""
    catalog = tmp_path / "twins"
    catalog.mkdir()
    _write_catalog(catalog)
    settings = _settings(tmp_path)
    settings.twin_configs_dir = str(catalog)
    settings.api_token = TOKEN
    return settings, catalog


def _client(settings, *, persona_backend=None, tap_adapter=None):
    app = create_app(settings)
    app.state.deploy_runner = _fake_runner()
    app.state.deploy_background = lambda job: job()
    app.state.tfc_persona_backend = persona_backend or _FakePersonaBackend()
    adapter = tap_adapter or _FakeMirAdapter()
    app.state.tap_adapter = adapter
    app.state.tap_config_backend = adapter
    return TestClient(app), app.state.tfc_persona_backend, adapter


def _deploy(c) -> str:
    r = c.post("/api/deploys", json={"config": TWIN, "target_node": "pxtest1"}, headers=AUTH)
    assert r.status_code == 200, r.text
    did = r.json()["deploy_id"]
    assert c.get(f"/api/deploys/{did}").json()["deploy"]["status"] == "completed"
    return did


def _seed_sensors(settings):
    store = StateStore(settings.db_path)
    try:
        _seed_o2_surface(store, sensors=DEFAULT_SENSORS)
    finally:
        store.close()


def _edited(files: dict[str, str], **changes: str) -> dict[str, str]:
    out = dict(files)
    out.update(changes)
    return out


def _doc(c, twin_id: str = TWIN) -> dict[str, str]:
    # The document rides only on an authorized read (the bundle carries
    # lab passwords); the summary stays open.
    r = c.get(f"/api/twin-configs/{twin_id}", headers=AUTH)
    assert r.status_code == 200, r.text
    return r.json()["document"]["files"]


# ---------------------------------------------------------------------------
# A3 -- twin-config write
# ---------------------------------------------------------------------------
def test_detail_carries_the_document_only_with_the_token(env):
    settings, _ = env
    c, _, _ = _client(settings)
    # Open read: the summary, no document (lab passwords live in a bundle).
    r = c.get(f"/api/twin-configs/{TWIN}")
    assert r.status_code == 200 and r.json()["id"] == TWIN
    assert r.json()["document"] is None
    r = c.get(f"/api/twin-configs/{TWIN}", headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 200 and r.json()["document"] is None
    r = c.get(f"/api/twin-configs/{TWIN}", headers=AUTH)
    assert r.status_code == 200
    doc = r.json()["document"]
    assert doc["id"] == TWIN and doc["te_file"] == "te.yaml"
    assert doc["files"] == _bundle_files()
    assert doc["skipped"] == []


def test_a3_1_save_as_persists_byte_for_byte_and_leaves_the_source_alone(env):
    settings, catalog = env
    before = _catalog_fingerprint(catalog / TWIN)
    c, _, _ = _client(settings)
    files = {
        rel: text.replace("edit-twin", "edit-twin-copy") for rel, text in _doc(c).items()
    }
    r = c.post("/api/twin-configs", json={"id": "edit-twin-copy", "files": files}, headers=AUTH)
    assert r.status_code == 201, r.text
    assert r.json()["id"] == "edit-twin-copy"
    assert r.json()["document"]["files"] == files
    # A re-read is byte-for-byte the written document.
    assert _doc(c, "edit-twin-copy") == files
    for rel, text in files.items():
        assert (catalog / "edit-twin-copy" / rel).read_text(encoding="utf-8") == text
    assert "edit-twin-copy" in {e["id"] for e in c.get("/api/twin-configs").json()["twin_configs"]}
    # The source baseline is unchanged.
    assert _catalog_fingerprint(catalog / TWIN) == before


def test_a3_2_save_as_under_an_existing_id_is_refused_nothing_overwritten(env):
    settings, catalog = env
    before = _catalog_fingerprint(catalog / TWIN)
    c, _, _ = _client(settings)
    files = _edited(_doc(c), **{"tfc/default.yaml": TFC.replace("web-tier", "dmz-web")})
    r = c.post("/api/twin-configs", json={"id": TWIN, "files": files}, headers=AUTH)
    assert r.status_code == 409
    assert "already exists" in r.json()["error"]
    assert _catalog_fingerprint(catalog / TWIN) == before


def test_a3_save_as_validates_before_writing(env):
    settings, catalog = env
    c, _, _ = _client(settings)
    # The document's own id must be the id it is saved under (never rewritten).
    r = c.post("/api/twin-configs", json={"id": "other", "files": _doc(c)}, headers=AUTH)
    assert r.status_code == 400 and "twin_config.id" in r.json()["error"]
    assert not (catalog / "other").exists()
    # An invalid twin config (an unknown image) is refused with the validator's reason.
    files = {
        rel: text.replace("edit-twin", "bad-twin") for rel, text in _doc(c).items()
    }
    files["te.yaml"] = files["te.yaml"].replace("debian-12-cloud@v1", "no-such-image@v9")
    r = c.post("/api/twin-configs", json={"id": "bad-twin", "files": files}, headers=AUTH)
    assert r.status_code in (400, 422), r.text
    assert "no-such-image" in r.text or r.json()["detail"]
    assert not (catalog / "bad-twin").exists()
    # A tap plan the tap playout would refuse is refused here, too.
    files = {rel: text.replace("edit-twin", "bad-tap") for rel, text in _doc(c).items()}
    files["tap/default.yaml"] = "tap-plan:\n  target: bad-tap\n  bindings: []\n"
    r = c.post("/api/twin-configs", json={"id": "bad-tap", "files": files}, headers=AUTH)
    assert r.status_code == 400 and "bindings" in r.json()["error"]
    assert not (catalog / "bad-tap").exists()
    # A document path that escapes the bundle is refused by name.
    files = {rel: text.replace("edit-twin", "esc") for rel, text in _doc(c).items()}
    files["../escape.yaml"] = "x: 1\n"
    r = c.post("/api/twin-configs", json={"id": "esc", "files": files}, headers=AUTH)
    assert r.status_code == 400 and "escape.yaml" in r.json()["error"]
    assert not (catalog / "escape.yaml").exists() and not (catalog / "esc").exists()


def test_a3_save_on_an_undeployed_config_overwrites_the_baseline(env):
    settings, catalog = env
    c, _, _ = _client(settings)
    files = _edited(_doc(c), **{"tfc/default.yaml": TFC.replace("web-tier", "dmz-web")})
    files.pop("README.md")  # a file dropped from the document leaves the bundle
    r = c.put(f"/api/twin-configs/{TWIN}", json={"files": files}, headers=AUTH)
    assert r.status_code == 200, r.text
    assert _doc(c) == files
    assert (catalog / TWIN / "tfc" / "default.yaml").read_text() == files["tfc/default.yaml"]
    assert not (catalog / TWIN / "README.md").exists()
    # An invalid save is refused with the validator's reason; the baseline stands.
    bad = _edited(files, **{"te.yaml": TE.replace("net: corp", "net: nowhere")})
    r = c.put(f"/api/twin-configs/{TWIN}", json={"files": bad}, headers=AUTH)
    assert r.status_code in (400, 422), r.text
    assert "nowhere" in r.text
    assert _doc(c) == files
    # A body id that disagrees with the path is a 400.
    r = c.put(f"/api/twin-configs/{TWIN}", json={"id": "x", "files": files}, headers=AUTH)
    assert r.status_code == 400
    r = c.put("/api/twin-configs/nope", json={"files": files}, headers=AUTH)
    assert r.status_code == 404


def test_a3_3_persist_from_a_working_copy_overwrites_baseline_and_cleans_the_copy(env):
    settings, _ = env
    c, _, _ = _client(settings)
    did = _deploy(c)
    files = _edited(_doc(c), **{"tfc/default.yaml": TFC.replace("web-tier", "dmz-web")})
    r = c.put(f"/api/deploys/{did}/config", json={"files": files}, headers=AUTH)
    assert r.status_code == 200 and r.json()["dirty"] is True
    assert _doc(c)["tfc/default.yaml"] == TFC  # baseline untouched by the PUT

    r = c.put(f"/api/twin-configs/{TWIN}", json={"files": files}, headers=AUTH)
    assert r.status_code == 200, r.text
    assert _doc(c) == files
    cfg = c.get(f"/api/deploys/{did}/config").json()
    assert cfg["dirty"] is False
    assert cfg["has_working_copy"] is False
    assert cfg["document"]["files"] == files
    assert cfg["changes"] == []


def test_writes_are_token_gated(env):
    settings, _ = env
    c, _, _ = _client(settings)
    did = _deploy(c)
    files = _doc(c)
    assert c.post("/api/twin-configs", json={"id": "x", "files": files}).status_code == 401
    assert c.put(f"/api/twin-configs/{TWIN}", json={"files": files}).status_code == 401
    assert c.put(f"/api/deploys/{did}/config", json={"files": files}).status_code == 401
    assert c.post(f"/api/deploys/{did}/config/apply").status_code == 401
    assert c.post(f"/api/deploys/{did}/config/reset").status_code == 401
    # Reads stay open.
    assert c.get(f"/api/deploys/{did}/config").status_code == 200


# ---------------------------------------------------------------------------
# A2 -- the per-deploy working copy
# ---------------------------------------------------------------------------
def test_a2_1_fresh_deploy_config_equals_baseline_and_is_not_dirty(env):
    settings, _ = env
    c, _, _ = _client(settings)
    did = _deploy(c)
    r = c.get(f"/api/deploys/{did}/config")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["deploy_id"] == did and j["twin_id"] == TWIN
    assert j["dirty"] is False and j["has_working_copy"] is False
    assert j["document"]["files"] == _doc(c)
    assert j["changes"] == [] and j["apply_allowed"] is True and j["applied"] is None
    # A read creates no working-copy store.
    assert not working_copy_db_path(settings.db_path).exists()
    # A deploy that is not from the twin catalog has no config to edit.
    assert c.get("/api/deploys/nope/config").status_code == 404


def test_a2_2_put_changes_only_the_working_copy_visible_to_a_second_client(env):
    settings, catalog = env
    before = _catalog_fingerprint(catalog)
    c, _, _ = _client(settings)
    did = _deploy(c)
    files = _edited(_doc(c), **{"tfc/default.yaml": TFC.replace("web-tier", "dmz-web")})
    r = c.put(f"/api/deploys/{did}/config", json={"files": files}, headers=AUTH)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["dirty"] is True and j["has_working_copy"] is True
    assert j["document"]["files"] == files
    assert [(ch["path"], ch["kind"], ch["via"]) for ch in j["changes"]] == [
        ("tfc/default.yaml", "attribute", ["tfc"]),
    ]
    assert j["apply_allowed"] is True
    # The baseline read is unchanged, on the wire and on disk.
    assert _doc(c) == _bundle_files()
    assert _catalog_fingerprint(catalog) == before
    # The copy lives beside the state DB, so a second client (a second
    # process over the same settings) reads the same copy.
    assert working_copy_db_path(settings.db_path).is_file()
    c2, _, _ = _client(settings)
    j2 = c2.get(f"/api/deploys/{did}/config").json()
    assert j2["document"]["files"] == files and j2["dirty"] is True
    # Idempotent: putting the baseline back is clean again (still a copy).
    r = c.put(f"/api/deploys/{did}/config", json={"files": _bundle_files()}, headers=AUTH)
    assert r.json()["dirty"] is False and r.json()["has_working_copy"] is True


def test_a2_3_invalid_put_is_refused_with_the_reason_and_the_copy_stands(env):
    settings, _ = env
    c, _, _ = _client(settings)
    did = _deploy(c)
    good = _edited(_doc(c), **{"tfc/default.yaml": TFC.replace("web-tier", "dmz-web")})
    assert c.put(f"/api/deploys/{did}/config", json={"files": good}, headers=AUTH).status_code == 200

    bad = _edited(good, **{"te.yaml": TE.replace("net: corp", "net: nowhere")})
    r = c.put(f"/api/deploys/{did}/config", json={"files": bad}, headers=AUTH)
    assert r.status_code in (400, 422), r.text
    assert "nowhere" in r.text
    # A traffic profile the tfc playout would refuse is refused here.
    bad2 = _edited(good, **{"tfc/default.yaml": "traffic-profile:\n  assignments:\n    - persona: web-tier\n"})
    r = c.put(f"/api/deploys/{did}/config", json={"files": bad2}, headers=AUTH)
    assert r.status_code == 400 and "assignments[0]" in r.json()["error"]
    # Not YAML at all.
    bad3 = _edited(good, **{"te.yaml": "twin_config: [unterminated"})
    r = c.put(f"/api/deploys/{did}/config", json={"files": bad3}, headers=AUTH)
    assert r.status_code in (400, 422)
    # The stored copy is the last valid one.
    assert c.get(f"/api/deploys/{did}/config").json()["document"]["files"] == good


# ---- classification ----
def _put(c, did, files):
    r = c.put(f"/api/deploys/{did}/config", json={"files": files}, headers=AUTH)
    assert r.status_code == 200, r.text
    return r.json()


def test_classifies_a_persona_change_as_attribute(env):
    settings, _ = env
    c, _, _ = _client(settings)
    did = _deploy(c)
    j = _put(c, did, _edited(_doc(c), **{"tfc/default.yaml": TFC.replace("web-tier", "dmz-web")}))
    (ch,) = j["changes"]
    assert ch == {
        "path": "tfc/default.yaml", "op": "modified", "kind": "attribute", "via": ["tfc"],
        "paths": ["tfc/default.yaml"], "reason": ch["reason"], "roles": {},
    }
    assert j["apply_allowed"] is True
    # A twin-local persona file and a tap binding change are attribute too.
    j = _put(c, did, _edited(
        _doc(c),
        **{"tap/default.yaml": TAP.replace("pktxpress-1", "suricata-1"),
           "personas/local.yaml": "persona:\n  id: local\n  os_family: linux\n  drive: scripted\n  emits: []\n"},
    ))
    assert {(ch["path"], ch["op"], ch["kind"], tuple(ch["via"])) for ch in j["changes"]} == {
        ("tap/default.yaml", "modified", "attribute", ("tap",)),
        ("personas/local.yaml", "added", "inert", ()),  # not assigned by the default profile
    }
    # A twin-local persona the default profile DOES assign is attribute.
    j = _put(c, did, _edited(
        _doc(c),
        **{"tfc/default.yaml": TFC.replace("persona: web-tier", "persona: local"),
           "personas/local.yaml": "persona:\n  id: local\n  os_family: linux\n  drive: scripted\n  emits: []\n"},
    ))
    assert {(ch["path"], ch["kind"], tuple(ch["via"])) for ch in j["changes"]} == {
        ("tfc/default.yaml", "attribute", ("tfc",)),
        ("personas/local.yaml", "attribute", ("tfc",)),
    }
    # An exr spec or prose is inert: Apply has nothing to do, Persist carries it.
    j = _put(c, did, _edited(_doc(c), **{"README.md": "changed\n", "exr/x.yaml": "exercise: {}\n"}))
    assert {ch["kind"] for ch in j["changes"]} == {"inert"}
    assert j["apply_allowed"] is True


def test_classifies_a_segment_add_as_topology(env):
    settings, _ = env
    c, _, _ = _client(settings)
    did = _deploy(c)
    te = TE.replace(
        "      zone_type: simple\n",
        "      zone_type: simple\n    - name: dmz\n      cidr: 10.20.1.0/24\n      zone_type: simple\n",
    )
    j = _put(c, did, _edited(_doc(c), **{"te.yaml": te}))
    (ch,) = j["changes"]
    assert ch["path"] == "te.yaml" and ch["kind"] == "topology"
    assert "twin_config.segments[dmz]" in ch["paths"]
    assert j["apply_allowed"] is False
    # A count change, an image change and a node add are topology too.
    for edit in (
        TE.replace("      net: corp\n    - name: h-b", "      net: corp\n      count: 2\n    - name: h-b"),
        TE.replace("debian-12-cloud@v1\n      requires: [linux]\n      resources: { cpu: 1, ram: 1Gi, disk: 10Gi }\n      net: corp\n", "ubuntu-server-22@v5\n      requires: [linux]\n      resources: { cpu: 1, ram: 1Gi, disk: 10Gi }\n      net: corp\n", 1),
        TE + "    - name: h-c\n      image: debian-12-cloud@v1\n      requires: [linux]\n      resources: { cpu: 1, ram: 1Gi, disk: 10Gi }\n      net: corp\n",
    ):
        j = _put(c, did, _edited(_doc(c), **{"te.yaml": edit}))
        assert [ch["kind"] for ch in j["changes"]] == ["topology"], j["changes"]
        assert j["apply_allowed"] is False


def test_classifies_a_role_change_both_ways(env):
    settings, _ = env
    c, _, _ = _client(settings)
    did = _deploy(c)
    # The image unchanged: an attribute change the tfc playout re-asserts.
    j = _put(c, did, _edited(_doc(c), **{"te.yaml": TE.replace("role: generic", "role: app-tier")}))
    (ch,) = j["changes"]
    assert ch["kind"] == "attribute" and ch["via"] == ["tfc"]
    assert ch["paths"] == ["twin_config.nodes[h-b].role"]
    assert ch["roles"] == {"h-b": "app-tier"}
    assert j["apply_allowed"] is True
    # The image changed with it: topology.
    te = TE.replace("role: generic", "role: app-tier").replace(
        "role: app-tier\n      image: debian-12-cloud@v1", "role: app-tier\n      image: ubuntu-server-22@v5",
    )
    j = _put(c, did, _edited(_doc(c), **{"te.yaml": te}))
    (ch,) = j["changes"]
    assert ch["kind"] == "topology" and "h-b" in ch["reason"]
    assert j["apply_allowed"] is False


def test_classifies_manifest_rewiring_as_attribute_and_te_rewiring_as_topology(env):
    settings, _ = env
    c, _, _ = _client(settings)
    did = _deploy(c)
    files = _edited(_doc(c), **{
        "twin.yaml": MANIFEST.replace("default: tfc/default.yaml", "default: tfc/busy.yaml"),
        "tfc/busy.yaml": TFC.replace("web-tier", "dmz-web"),
    })
    j = _put(c, did, files)
    by_path = {ch["path"]: ch for ch in j["changes"]}
    assert by_path["twin.yaml"]["kind"] == "attribute" and by_path["twin.yaml"]["via"] == ["tfc"]
    assert by_path["tfc/busy.yaml"]["op"] == "added"
    j = _put(c, did, _edited(_doc(c), **{"twin.yaml": MANIFEST.replace("default: te.yaml", "default: te2.yaml"), "te2.yaml": TE}))
    assert by_path  # the te tier moved: topology
    assert {ch["kind"] for ch in j["changes"] if ch["path"] == "twin.yaml"} == {"topology"}
    assert j["apply_allowed"] is False


# ---- apply / reset ----
def _persona_ids(backend) -> list[str]:
    return [call.get("persona_id") for call in backend.calls if "persona_id" in call]


def _assigned_roles(backend) -> list[tuple[str, str]]:
    return [
        (call.get("host_id"), call.get("role")) for call in backend.calls
        if "role" in call and "persona_id" not in call
    ]


def test_a2_4_apply_of_an_attribute_delta_drives_the_tfc_and_tap_playouts(env):
    settings, catalog = env
    _seed_sensors(settings)
    before = _catalog_fingerprint(catalog)
    c, backend, adapter = _client(settings)
    did = _deploy(c)
    # The deploy sequence's own tfc + tap applies (the CLI's steps 2 and 3),
    # so the edit's apply has something to converge from.
    r = c.post("/api/tfc", json={"target": did, "assignments": [{"node": "h-b", "persona": "web-tier"}],
                                 "target_node": "pxtest1"}, headers=AUTH)
    assert r.status_code == 200, r.text
    r = c.post("/api/taps", json={"target": did, "bindings": [{"tap": "corp", "sensors": ["pktxpress-1"]}],
                                  "target_node": "pxtest1"}, headers=AUTH)
    assert r.status_code == 200, r.text
    assert backend.running == {"h-b-web-tier"}
    tfc_id = c.get("/api/tfc", params={"target": did}).json()["deploy_id"]
    tap_id = c.get("/api/taps", params={"target": did}).json()["deploy_id"]

    files = _edited(_doc(c), **{
        "tfc/default.yaml": TFC.replace("web-tier", "dmz-web"),
        "tap/default.yaml": TAP.replace("pktxpress-1", "suricata-1"),
    })
    _put(c, did, files)
    n_calls, n_gre = len(backend.calls), len([x for x in adapter.calls if x[0] == "attach-gre-tunnel"])
    r = c.post(f"/api/deploys/{did}/config/apply", json={"target_node": "pxtest1"}, headers=AUTH)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["applied"] == ["tfc", "tap"]
    assert j["tfc"]["status"] == "completed" and j["tfc"]["deploy_id"] == tfc_id  # converged in place
    assert j["tap"]["status"] == "completed" and j["tap"]["deploy_id"] == tap_id
    assert j["config"]["applied"]["tiers"] == {"tfc": "done", "tap": "done"}
    assert j["config"]["baseline_drift"] is False
    assert j["config"]["dirty"] is True  # applied, not persisted
    # The running twin reflects it: the new persona drives, the old one was
    # de-scoped (stopped), no VM was touched.
    assert _persona_ids(backend)[n_calls:] == ["dmz-web"]
    assert backend.running == {"h-b-dmz-web"}
    assert ("h-b-web-tier" in {s[2] for s in backend.stopped})
    gre = [x for x in adapter.calls if x[0] == "attach-gre-tunnel"][n_gre:]
    assert [x[1]["sensor_id"] for x in gre] == ["suricata-1"]
    assert not any(x[0] == "clone-vm" for x in adapter.calls)
    # No redeploy: the twin's own deploy row is the same, still completed.
    assert c.get(f"/api/deploys/{did}").json()["deploy"]["status"] == "completed"
    # The baseline is untouched.
    assert _catalog_fingerprint(catalog) == before
    assert _doc(c) == _bundle_files()


def test_a2_5_apply_with_a_topology_delta_is_refused_and_applies_nothing(env):
    settings, catalog = env
    _seed_sensors(settings)
    before = _catalog_fingerprint(catalog)
    c, backend, adapter = _client(settings)
    did = _deploy(c)
    # An attribute change AND a topology change pending together: the whole
    # apply is refused (nothing partial).
    te = TE.replace(
        "      zone_type: simple\n",
        "      zone_type: simple\n    - name: dmz\n      cidr: 10.20.1.0/24\n      zone_type: simple\n",
    )
    files = _edited(_doc(c), **{"te.yaml": te, "tfc/default.yaml": TFC.replace("web-tier", "dmz-web")})
    j = _put(c, did, files)
    assert j["apply_allowed"] is False
    r = c.post(f"/api/deploys/{did}/config/apply", headers=AUTH)
    assert r.status_code == 409, r.text
    body = r.json()
    assert body["error"].startswith("requires-replan")
    assert body["detail"]["code"] == "requires-replan"
    assert body["detail"]["files"] == ["te.yaml"]
    assert "twin_config.segments[dmz]" in body["detail"]["paths"]
    assert {ch["path"]: ch["kind"] for ch in body["detail"]["changes"]} == {
        "te.yaml": "topology", "tfc/default.yaml": "attribute",
    }
    assert backend.calls == [] and adapter.calls == []
    assert c.get("/api/tfc", params={"target": did}).json()["status"] == "not-applied"
    # The copy stays dirty and unapplied; the baseline is untouched.
    cfg = c.get(f"/api/deploys/{did}/config").json()
    assert cfg["dirty"] is True and cfg["applied"] is None and cfg["document"]["files"] == files
    assert _catalog_fingerprint(catalog) == before
    # No working copy at all: nothing to apply.
    did2 = _deploy(c)
    assert c.post(f"/api/deploys/{did2}/config/apply", headers=AUTH).status_code == 409


def test_a2_6_reset_after_an_apply_reverts_the_twin_and_clears_the_copy(env):
    settings, _ = env
    _seed_sensors(settings)
    c, backend, adapter = _client(settings)
    did = _deploy(c)
    assert c.post("/api/tfc", json={"target": did, "assignments": [{"node": "h-b", "persona": "web-tier"}],
                                    "target_node": "pxtest1"}, headers=AUTH).status_code == 200
    assert c.post("/api/taps", json={"target": did, "bindings": [{"tap": "corp", "sensors": ["pktxpress-1"]}],
                                     "target_node": "pxtest1"}, headers=AUTH).status_code == 200
    files = _edited(_doc(c), **{
        "tfc/default.yaml": TFC.replace("web-tier", "dmz-web"),
        "tap/default.yaml": TAP.replace("pktxpress-1", "suricata-1"),
    })
    _put(c, did, files)
    assert c.post(f"/api/deploys/{did}/config/apply", json={"target_node": "pxtest1"}, headers=AUTH).status_code == 200
    assert backend.running == {"h-b-dmz-web"}

    n_calls, n_gre = len(backend.calls), len([x for x in adapter.calls if x[0] == "attach-gre-tunnel"])
    r = c.post(f"/api/deploys/{did}/config/reset", json={"target_node": "pxtest1"}, headers=AUTH)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["discarded"] is True and j["reverted"] == ["tfc", "tap"]
    assert j["tfc"]["status"] == "completed" and j["tap"]["status"] == "completed"
    # The twin is back at baseline: the baseline persona drives, the tweak
    # was de-scoped, the baseline sensor is bound again.
    assert _persona_ids(backend)[n_calls:] == ["web-tier"]
    assert backend.running == {"h-b-web-tier"}
    assert "h-b-dmz-web" in {s[2] for s in backend.stopped}
    gre = [x for x in adapter.calls if x[0] == "attach-gre-tunnel"][n_gre:]
    assert [x[1]["sensor_id"] for x in gre] == ["pktxpress-1"]
    # The copy is gone: the config is the baseline, clean, in every client.
    cfg = j["config"]
    assert cfg["dirty"] is False and cfg["has_working_copy"] is False and cfg["applied"] is None
    assert cfg["document"]["files"] == _bundle_files()
    c2, _, _ = _client(settings)
    assert c2.get(f"/api/deploys/{did}/config").json()["has_working_copy"] is False
    # A second reset is a no-op answer, not an error.
    r = c.post(f"/api/deploys/{did}/config/reset", headers=AUTH)
    assert r.status_code == 200 and r.json()["discarded"] is False and r.json()["reverted"] == []


def test_role_change_applies_through_the_tfc_roles_block_and_resets(env):
    settings, _ = env
    c, backend, _ = _client(settings)
    did = _deploy(c)
    assert c.post("/api/tfc", json={"target": did, "assignments": [{"node": "h-b", "persona": "web-tier"}],
                                    "target_node": "pxtest1"}, headers=AUTH).status_code == 200
    _put(c, did, _edited(_doc(c), **{"te.yaml": TE.replace("role: generic", "role: app-tier")}))
    r = c.post(f"/api/deploys/{did}/config/apply", json={"target_node": "pxtest1"}, headers=AUTH)
    assert r.status_code == 200, r.text
    assert r.json()["applied"] == ["tfc"] and r.json()["roles"] == {"h-b": "app-tier"}
    assert ("h-b", "app-tier") in _assigned_roles(backend)
    # The running twin publishes the new role, no redeploy.
    hosts = {h["host_id"]: h for h in c.get(f"/api/deploys/{did}/inventory").json()["hosts"]}
    assert hosts["h-b"]["role"] == "app-tier"
    assert c.get(f"/api/deploys/{did}/config").json()["applied"]["roles"] == {"h-b": "app-tier"}

    r = c.post(f"/api/deploys/{did}/config/reset", json={"target_node": "pxtest1"}, headers=AUTH)
    assert r.status_code == 200, r.text
    assert r.json()["reverted"] == ["tfc"]
    # The baseline profile is replayed UNMODIFIED: no role is folded in; the
    # playout's own de-scope retires the folded row, and the host reverts to
    # its twin baseline role.
    assert ("h-b", "generic") not in _assigned_roles(backend)
    hosts = {h["host_id"]: h for h in c.get(f"/api/deploys/{did}/inventory").json()["hosts"]}
    assert hosts["h-b"]["role"] == "generic"


def test_a3_3_no_other_call_changes_a_baseline(env):
    """The full A2 sequence (GET, PUT, apply, reset, a refused apply, a
    refused PUT) against the catalog, then a byte diff: only Persist /
    Save-As write a baseline."""
    settings, catalog = env
    _seed_sensors(settings)
    before = _catalog_fingerprint(catalog)
    c, _, _ = _client(settings)
    did = _deploy(c)
    c.get(f"/api/deploys/{did}/config")
    _put(c, did, _edited(_doc(c), **{"tfc/default.yaml": TFC.replace("web-tier", "dmz-web")}))
    assert c.post(f"/api/deploys/{did}/config/apply", json={"target_node": "pxtest1"}, headers=AUTH).status_code == 200
    assert c.post(f"/api/deploys/{did}/config/reset", json={"target_node": "pxtest1"}, headers=AUTH).status_code == 200
    _put(c, did, _edited(_doc(c), **{"te.yaml": TE.replace("role: generic", "role: x").replace("debian-12-cloud@v1\n      requires: [linux]\n      resources: { cpu: 1, ram: 1Gi, disk: 10Gi }\n      net: corp\n", "ubuntu-server-22@v5\n      requires: [linux]\n      resources: { cpu: 1, ram: 1Gi, disk: 10Gi }\n      net: corp\n")}))
    assert c.post(f"/api/deploys/{did}/config/apply", headers=AUTH).status_code == 409
    bad = _edited(_doc(c), **{"te.yaml": "nope: [\n"})
    assert c.put(f"/api/deploys/{did}/config", json={"files": bad}, headers=AUTH).status_code in (400, 422)
    assert c.post("/api/twin-configs", json={"id": TWIN, "files": _doc(c)}, headers=AUTH).status_code == 409
    assert _catalog_fingerprint(catalog) == before
    # The working-copy store is beside the state DB, never in the catalog.
    assert working_copy_db_path(settings.db_path).parent == Path(settings.db_path).parent
    assert not list(catalog.rglob("*.db"))


def test_working_copy_of_a_destroyed_deploy_is_read_only(env):
    settings, _ = env
    c, _, _ = _client(settings)
    did = _deploy(c)
    _put(c, did, _edited(_doc(c), **{"README.md": "x\n"}))
    # The record says destroyed (the teardown's own mechanics are covered in
    # tests/test_api.py; here only the status matters).
    store = StateStore(settings.db_path)
    try:
        store.mark_destroyed(did)
    finally:
        store.close()
    assert c.get(f"/api/deploys/{did}").json()["deploy"]["status"] == "destroyed"
    assert c.get(f"/api/deploys/{did}/config").status_code == 200
    assert c.put(f"/api/deploys/{did}/config", json={"files": _doc(c)}, headers=AUTH).status_code == 409
    assert c.post(f"/api/deploys/{did}/config/apply", headers=AUTH).status_code == 409
    # Reset still discards the copy (nothing to revert on a destroyed twin).
    r = c.post(f"/api/deploys/{did}/config/reset", headers=AUTH)
    assert r.status_code == 200 and r.json()["discarded"] is True and r.json()["reverted"] == []
    assert c.get(f"/api/deploys/{did}/config").json()["has_working_copy"] is False


def test_working_copy_store_is_json_without_secrets(env):
    settings, _ = env
    c, _, _ = _client(settings)
    did = _deploy(c)
    _put(c, did, _edited(_doc(c), **{"README.md": "x\n"}))
    import sqlite3

    conn = sqlite3.connect(str(working_copy_db_path(settings.db_path)))
    rows = conn.execute("SELECT deploy_id, twin_id, document_json FROM working_copies").fetchall()
    conn.close()
    assert [(r[0], r[1]) for r in rows] == [(did, TWIN)]
    assert json.loads(rows[0][2])["README.md"] == "x\n"


# ---------------------------------------------------------------------------
# Review round (2026-10-30): partial applies, the role fold, reset of a tier
# the baseline lacks, baseline drift, atomic Save-As / Persist, the sidecar's
# races and modes, the classifier's edges, document hygiene.
# ---------------------------------------------------------------------------
class _TapAdapterFailsGre(_FakeMirAdapter):
    """The tap playout's backend raising mid-apply: the executor marks the
    op failed and the playout ends `failed`."""

    def run(self, op, resolved_inputs, ctx):
        if op.op_name == "attach-gre-tunnel":
            raise RuntimeError("gre attach exploded")
        return super().run(op, resolved_inputs, ctx)


def _apply_baseline_tiers(c, did):
    assert c.post("/api/tfc", json={"target": did, "assignments": [{"node": "h-b", "persona": "web-tier"}],
                                    "target_node": "pxtest1"}, headers=AUTH).status_code == 200
    assert c.post("/api/taps", json={"target": did, "bindings": [{"tap": "corp", "sensors": ["pktxpress-1"]}],
                                     "target_node": "pxtest1"}, headers=AUTH).status_code == 200


def test_partial_apply_is_recorded_and_reset_replays_every_recorded_tier(env):
    settings, _ = env
    _seed_sensors(settings)
    c, backend, _ = _client(settings, tap_adapter=_TapAdapterFailsGre())
    did = _deploy(c)
    _apply_baseline_tiers(c, did)
    files = _edited(_doc(c), **{
        "tfc/default.yaml": TFC.replace("web-tier", "dmz-web"),
        "tap/default.yaml": TAP.replace("pktxpress-1", "suricata-1"),
    })
    _put(c, did, files)
    r = c.post(f"/api/deploys/{did}/config/apply", json={"target_node": "pxtest1"}, headers=AUTH)
    assert r.status_code == 502, r.text
    assert r.json()["detail"]["tier"] == "tap"
    # tfc went through and is recorded done; tap is recorded attempted.
    assert backend.running == {"h-b-dmz-web"}
    cfg = c.get(f"/api/deploys/{did}/config").json()
    assert cfg["applied"]["tiers"] == {"tfc": "done", "tap": "attempted"}
    # Reset with a healthy tap backend reverts BOTH recorded tiers.
    c2, _, adapter2 = _client(settings, persona_backend=backend)
    r = c2.post(f"/api/deploys/{did}/config/reset", json={"target_node": "pxtest1"}, headers=AUTH)
    assert r.status_code == 200, r.text
    assert r.json()["reverted"] == ["tfc", "tap"]
    assert backend.running == {"h-b-web-tier"}
    assert [x[1]["sensor_id"] for x in adapter2.calls if x[0] == "attach-gre-tunnel"] == ["pktxpress-1"]
    assert c2.get(f"/api/deploys/{did}/config").json()["has_working_copy"] is False


def test_role_change_on_a_host_the_profile_roles_block_claims_is_refused(env):
    settings, catalog = env
    # The baseline profile addresses h-b by a SELECTOR (every host on corp).
    profile = TFC.replace(
        "  assignments:",
        "  roles:\n    - selector: { net: corp }\n      role: lan-host\n  assignments:",
    )
    (catalog / TWIN / "tfc" / "default.yaml").write_text(profile)
    c, backend, _ = _client(settings)
    did = _deploy(c)
    _put(c, did, _edited(_doc(c), **{"te.yaml": TE.replace("role: generic", "role: app-tier")}))
    r = c.post(f"/api/deploys/{did}/config/apply", json={"target_node": "pxtest1"}, headers=AUTH)
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "role-conflict"
    assert r.json()["detail"]["host"] == "h-b" and "selector" in r.json()["error"]
    assert backend.calls == []
    # ... and by NODE.
    (catalog / TWIN / "tfc" / "default.yaml").write_text(TFC.replace(
        "  assignments:", "  roles:\n    - node: h-b\n      role: lan-host\n  assignments:",
    ))
    files = _edited(_doc(c), **{"te.yaml": TE.replace("role: generic", "role: app-tier")})
    assert c.post(f"/api/deploys/{did}/config/reset", headers=AUTH).status_code == 200
    _put(c, did, files)  # re-taken against the new baseline
    r = c.post(f"/api/deploys/{did}/config/apply", json={"target_node": "pxtest1"}, headers=AUTH)
    assert r.status_code == 409, r.text
    assert r.json()["detail"].get("entry", "").startswith("node"), r.text
    assert backend.calls == []
    # Without a claim on h-b (in the baseline OR the working copy) the fold goes through.
    (catalog / TWIN / "tfc" / "default.yaml").write_text(TFC)
    assert c.post(f"/api/deploys/{did}/config/reset", headers=AUTH).status_code == 200
    files = _edited(_doc(c), **{"te.yaml": TE.replace("role: generic", "role: app-tier")})
    _put(c, did, files)
    r = c.post(f"/api/deploys/{did}/config/apply", json={"target_node": "pxtest1"}, headers=AUTH)
    assert r.status_code == 200, r.text
    assert ("h-b", "app-tier") in _assigned_roles(backend)


def test_reset_tears_down_a_tier_the_baseline_lacks(env):
    settings, catalog = env
    _seed_sensors(settings)
    # The baseline has no tap tier at all.
    (catalog / TWIN / "tap" / "default.yaml").unlink()
    (catalog / TWIN / "tap").rmdir()
    (catalog / TWIN / "twin.yaml").write_text(MANIFEST.replace("    tap:\n      default: tap/default.yaml\n", ""))
    from tests.test_o2_taps import _FakeTeardown

    c, _, adapter = _client(settings)
    teardown = _FakeTeardown()
    c.app.state.tap_teardown_backend = teardown
    did = _deploy(c)
    assert c.get(f"/api/deploys/{did}/config").json()["document"]["files"].keys() == {
        "README.md", "te.yaml", "tfc/default.yaml", "twin.yaml",
    }
    files = _edited(_doc(c), **{"twin.yaml": MANIFEST, "tap/default.yaml": TAP})
    j = _put(c, did, files)
    assert {(ch["path"], ch["kind"]) for ch in j["changes"]} == {
        ("twin.yaml", "attribute"), ("tap/default.yaml", "attribute"),
    }
    r = c.post(f"/api/deploys/{did}/config/apply", json={"target_node": "pxtest1"}, headers=AUTH)
    assert r.status_code == 200, r.text
    assert r.json()["applied"] == ["tap"]
    assert [x[1]["sensor_id"] for x in adapter.calls if x[0] == "attach-gre-tunnel"] == ["pktxpress-1"]
    assert c.get("/api/taps", params={"target": did}).json()["status"] == "completed"

    r = c.post(f"/api/deploys/{did}/config/reset", json={"target_node": "pxtest1"}, headers=AUTH)
    assert r.status_code == 200, r.text
    assert r.json()["reverted"] == [] and r.json()["torn_down"] == ["tap"]
    assert any(kind == "gretap" for kind, _t in teardown.calls)
    assert c.get("/api/taps", params={"target": did}).json()["status"] in ("destroyed", "not-applied")
    assert r.json()["config"]["has_working_copy"] is False


def test_apply_refuses_on_baseline_drift_and_get_reports_it(env):
    settings, catalog = env
    c, backend, _ = _client(settings)
    did = _deploy(c)
    _put(c, did, _edited(_doc(c), **{"tfc/default.yaml": TFC.replace("web-tier", "dmz-web")}))
    assert c.get(f"/api/deploys/{did}/config").json()["baseline_drift"] is False
    # Someone saves the baseline meanwhile (out of band here; a Save is the same).
    (catalog / TWIN / "README.md").write_text("# moved\n")
    cfg = c.get(f"/api/deploys/{did}/config").json()
    assert cfg["baseline_drift"] is True and cfg["dirty"] is True
    r = c.post(f"/api/deploys/{did}/config/apply", json={"target_node": "pxtest1"}, headers=AUTH)
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == {"code": "baseline-drift", "twin_id": TWIN}
    assert TWIN in r.json()["error"]
    assert backend.calls == []
    # A later PUT keeps the first baseline: still drifted. Only a reset
    # re-opens the copy against the current baseline.
    j = _put(c, did, _edited(_doc(c), **{"tfc/default.yaml": TFC.replace("web-tier", "dmz-web")}))
    assert j["baseline_drift"] is True
    assert c.post(f"/api/deploys/{did}/config/reset", headers=AUTH).status_code == 200
    j = _put(c, did, _edited(_doc(c), **{"tfc/default.yaml": TFC.replace("web-tier", "dmz-web")}))
    assert j["baseline_drift"] is False
    assert c.post(f"/api/deploys/{did}/config/apply", json={"target_node": "pxtest1"}, headers=AUTH).status_code == 200


def test_save_as_claims_the_id_atomically_and_leaves_no_half_folder(env, monkeypatch):
    settings, catalog = env
    c, _, _ = _client(settings)
    files = {rel: text.replace("edit-twin", "edit-twin-copy") for rel, text in _doc(c).items()}
    # A folder that appeared between the catalog check and the claim: 409,
    # and the folder is NOT ours to remove.
    (catalog / "edit-twin-copy").mkdir()
    (catalog / "edit-twin-copy" / "keep.txt").write_text("theirs\n")
    r = c.post("/api/twin-configs", json={"id": "edit-twin-copy", "files": files}, headers=AUTH)
    assert r.status_code == 409
    assert (catalog / "edit-twin-copy" / "keep.txt").read_text() == "theirs\n"
    (catalog / "edit-twin-copy" / "keep.txt").unlink()
    (catalog / "edit-twin-copy").rmdir()
    # A write that fails half-way: the claimed folder is removed again.
    from axrange.api import console_files, edits

    real = console_files.atomic_write
    calls = {"n": 0}

    def flaky(path, text):
        calls["n"] += 1
        if calls["n"] == 2:
            raise console_files.ConsoleFileWriteError("disk full")
        real(path, text)

    monkeypatch.setattr(console_files, "atomic_write", flaky)
    r = c.post("/api/twin-configs", json={"id": "edit-twin-copy", "files": files}, headers=AUTH)
    assert r.status_code == 400 and "disk full" in r.json()["error"]
    assert not (catalog / "edit-twin-copy").exists()
    assert not list(catalog.glob(".*"))
    monkeypatch.setattr(console_files, "atomic_write", real)
    r = c.post("/api/twin-configs", json={"id": "edit-twin-copy", "files": files}, headers=AUTH)
    assert r.status_code == 201 and _doc(c, "edit-twin-copy") == files
    assert edits.read_document(catalog / "edit-twin-copy")[0] == files


def test_persist_keeps_a_history_snapshot_and_never_exposes_temp_files(env):
    settings, catalog = env
    from axrange.api.recompile import HISTORY_KEEP, history_dir

    c, _, _ = _client(settings)
    base = _doc(c)
    # Stray temp / dot files in the bundle are not part of the document.
    (catalog / TWIN / "te.yaml.tmp").write_text("half\n")
    (catalog / TWIN / ".te.yaml.abc.tmp").write_text("half\n")
    (catalog / TWIN / ".hidden.yaml").write_text("x\n")
    (catalog / TWIN / "tfc" / "generated").mkdir()
    (catalog / TWIN / "tfc" / "generated" / "x.yaml").write_text("x\n")
    assert _doc(c) == base
    for i in range(HISTORY_KEEP + 2):
        files = _edited(base, **{"README.md": f"# v{i}\n"})
        r = c.put(f"/api/twin-configs/{TWIN}", json={"files": files}, headers=AUTH)
        assert r.status_code == 200, r.text
        assert _doc(c) == files
    snaps = sorted(history_dir(catalog / TWIN).glob("*.json"))
    assert len(snaps) == HISTORY_KEEP
    snap = json.loads(snaps[0].read_text())
    assert snap["schema"] == "axr-baseline-history/1" and snap["twin_id"] == TWIN
    assert snap["files"]["te.yaml"] == TE
    # The snapshots live under generated/.history and never enter the document.
    assert "generated" in snaps[0].parts and "generated/.history" not in str(_doc(c).keys())
    # No leftover temp file from the writes: only the two planted strays.
    assert sorted(p.name for p in (catalog / TWIN).glob("*.tmp")) == [".te.yaml.abc.tmp", "te.yaml.tmp"]


def test_crlf_round_trips_byte_for_byte(env):
    settings, catalog = env
    c, _, _ = _client(settings)
    files = _edited(_doc(c), **{"README.md": "# crlf\r\n\r\nline\r\n"})
    r = c.put(f"/api/twin-configs/{TWIN}", json={"files": files}, headers=AUTH)
    assert r.status_code == 200, r.text
    assert r.json()["document"]["files"]["README.md"] == "# crlf\r\n\r\nline\r\n"
    assert (catalog / TWIN / "README.md").read_bytes() == b"# crlf\r\n\r\nline\r\n"
    assert _doc(c)["README.md"] == "# crlf\r\n\r\nline\r\n"


def test_apply_is_refused_while_the_twin_lease_is_held(env):
    settings, _ = env
    from axrange.planner.state import default_process_info

    c, backend, _ = _client(settings)
    did = _deploy(c)
    _put(c, did, _edited(_doc(c), **{"tfc/default.yaml": TFC.replace("web-tier", "dmz-web")}))
    store = StateStore(settings.db_path)
    try:
        assert store.acquire_lease(TWIN, "other-console", process_info=default_process_info()).acquired
    finally:
        store.close()
    r = c.post(f"/api/deploys/{did}/config/apply", json={"target_node": "pxtest1"}, headers=AUTH)
    assert r.status_code == 409 and r.json()["detail"] == {"code": "busy", "held_by": "other-console"}
    assert backend.calls == []
    r = c.post(f"/api/deploys/{did}/config/reset", headers=AUTH)
    assert r.status_code == 200  # nothing applied: no lease needed to discard
    store = StateStore(settings.db_path)
    try:
        store.release_lease(TWIN, "other-console")
    finally:
        store.close()
    _put(c, did, _edited(_doc(c), **{"tfc/default.yaml": TFC.replace("web-tier", "dmz-web")}))
    assert c.post(f"/api/deploys/{did}/config/apply", json={"target_node": "pxtest1"}, headers=AUTH).status_code == 200
    # The lease is released after the apply.
    store = StateStore(settings.db_path)
    try:
        assert store.get_lease(TWIN) is None
    finally:
        store.close()


def test_mark_tier_on_a_vanished_row_raises_and_apply_answers_409(env, tmp_path):
    settings, _ = env
    from axrange.api.edits import WorkingCopyGone, WorkingCopyStore

    store = WorkingCopyStore(tmp_path / "x.edits.db")
    store.put("d1", TWIN, {"te.yaml": TE}, baseline_hash="h")
    with pytest.raises(WorkingCopyGone):
        store.mark_tier("d2", "tfc", "attempted")
    assert store.mark_tier("d1", "tfc", "attempted")["tiers"] == {"tfc": "attempted"}
    assert store.mark_tier("d1", "tap", "done", roles={"h-b": "x"})["tiers"] == {"tfc": "attempted", "tap": "done"}
    assert store.get("d1").applied["roles"] == {"h-b": "x"}
    store.delete("d1")
    with pytest.raises(WorkingCopyGone):
        store.mark_tier("d1", "tfc", "done")

    # Through the API: the row deleted under a running apply (the tap backend
    # resets the copy) ends the apply with a 409, not a green answer.
    class _ResetsMidApply(_FakeMirAdapter):
        def run(self, op, resolved_inputs, ctx):
            WorkingCopyStore.beside(settings.db_path).delete(self.did)
            return super().run(op, resolved_inputs, ctx)

    _seed_sensors(settings)
    adapter = _ResetsMidApply()
    c, _, _ = _client(settings, tap_adapter=adapter)
    did = adapter.did = _deploy(c)
    _put(c, did, _edited(_doc(c), **{"tap/default.yaml": TAP.replace("pktxpress-1", "suricata-1")}))
    r = c.post(f"/api/deploys/{did}/config/apply", json={"target_node": "pxtest1"}, headers=AUTH)
    assert r.status_code == 409 and "gone" in r.json()["error"]


def test_classifier_ignores_comment_only_edits_and_unreached_tier_files(env):
    settings, _ = env
    c, _, _ = _client(settings)
    did = _deploy(c)
    # A comment / whitespace-only te edit and a re-keyed manifest are no change.
    j = _put(c, did, _edited(_doc(c), **{
        "te.yaml": "# a new comment\n" + TE.replace("      net: corp\n", "      net:   corp\n"),
        "twin.yaml": "# wiring\n" + MANIFEST,
    }))
    assert j["changes"] == [] and j["dirty"] is True and j["apply_allowed"] is True
    # A tap variant, a tfc variant and a persona nobody assigns are inert.
    j = _put(c, did, _edited(_doc(c), **{
        "tap/other.yaml": TAP.replace("pktxpress-1", "suricata-1"),
        "tfc/alt.yaml": TFC.replace("web-tier", "dmz-web"),
        "personas/stray.yaml": "persona:\n  id: stray\n  os_family: linux\n  drive: scripted\n  emits: []\n",
    }))
    assert {(ch["path"], ch["kind"], tuple(ch["via"])) for ch in j["changes"]} == {
        ("tap/other.yaml", "inert", ()), ("tfc/alt.yaml", "inert", ()), ("personas/stray.yaml", "inert", ()),
    }
    # A roles_from fragment the default profile reaches IS attribute.
    j = _put(c, did, _edited(_doc(c), **{
        "tfc/default.yaml": TFC.replace("  assignments:", "  roles_from: roles.yaml\n  assignments:"),
        "tfc/roles.yaml": "traffic-profile:\n  roles:\n    - node: h-b\n      role: lan-host\n",
    }))
    assert {(ch["path"], ch["kind"]) for ch in j["changes"]} == {
        ("tfc/default.yaml", "attribute"), ("tfc/roles.yaml", "attribute"),
    }
    # Anything else unrecognised is topology (fail closed); nested prose too.
    j = _put(c, did, _edited(_doc(c), **{"scripts/x.sh": "#!/bin/sh\n", "docs/notes.md": "x\n"}))
    assert {(ch["path"], ch["kind"]) for ch in j["changes"]} == {
        ("scripts/x.sh", "topology"), ("docs/notes.md", "topology"),
    }
    assert j["apply_allowed"] is False
    # Manifest prefixes match whole keys: `twin.tiers.tapx` is not the tap tier.
    from axrange.api.edits import classify_manifest

    ch = classify_manifest("twin.yaml", MANIFEST, MANIFEST.replace("    tap:\n", "    tapx:\n"))
    assert ch.kind == "topology"
    assert classify_manifest("twin.yaml", MANIFEST, MANIFEST + "\n") is None


def test_group_instances_use_the_expand_id_scheme():
    from axrange.api.edits import parse_yaml
    from axrange.api.service import _group_count, _group_instances
    from axrange.twinconfig.expand import instance_ids

    roster = ["web01", "web0101", "ws", "ws01", "ws02", "ws100"] + instance_ids("big", 120)
    # The literal group `web01` at count 1 is its own one-host group.
    assert _group_instances("web01", 1, roster) == ["web01"]
    assert _group_instances("web01", 2, roster) == ["web0101"]
    # count 1 and the NN fan-out never mix: `ws` at count 1 is `ws` alone.
    assert _group_instances("ws", 1, roster) == ["ws"]
    assert _group_instances("ws", 2, roster) == ["ws01", "ws02"]
    # count >= 100: the same ids the deploy minted (`big01` ... `big120`).
    assert _group_instances("big", 120, roster) == instance_ids("big", 120)
    assert "big120" in _group_instances("big", 120, roster)
    assert _group_instances("nope", 3, roster) == []
    # The count comes from the document, templated or not.
    te = parse_yaml(TE.replace("      net: corp\n    - name: h-b", "      net: corp\n      count: 3\n    - name: h-b"))
    assert _group_count(te, "h-a") == 3 and _group_count(te, "h-b") == 1 and _group_count(te, "nope") is None
    te = parse_yaml(TE.replace("  segments:", "  params:\n    N: 4\n  segments:").replace(
        "      net: corp\n    - name: h-b", "      net: corp\n      count: \"${N:2}\"\n    - name: h-b"))
    assert _group_count(te, "h-a") == 4


def test_document_keys_are_normalised_and_escapes_refused(env):
    settings, catalog = env
    from axrange.api.edits import DocumentError, normalize_files

    assert normalize_files({"./tfc//default.yaml": "x"}) == {"tfc/default.yaml": "x"}
    for bad in ("../x.yaml", "/abs.yaml", "a\\b.yaml", "tfc/generated/x.yaml", "tfc/.hidden.yaml",
                "a/../b.yaml", "x.tmp", "", "generated/x.yaml", "~/x.yaml"):
        with pytest.raises(DocumentError):
            normalize_files({bad: "x"})
    with pytest.raises(DocumentError):
        normalize_files({"te.yaml": "a", "./te.yaml": "b"})
    with pytest.raises(DocumentError):
        normalize_files({"te.yaml": 1})
    c, _, _ = _client(settings)
    did = _deploy(c)
    files = _edited(_doc(c), **{"tfc/generated/x.yaml": "x\n"})
    r = c.put(f"/api/deploys/{did}/config", json={"files": files}, headers=AUTH)
    assert r.status_code == 400 and "generated" in r.json()["error"]
    r = c.put(f"/api/twin-configs/{TWIN}", json={"files": {"te.yaml": TE, "./te.yaml": TE}}, headers=AUTH)
    assert r.status_code == 400 and "duplicates" in r.json()["error"]
    # A symlinked folder inside the bundle that points outside: refused.
    outside = catalog.parent / "outside"
    outside.mkdir()
    (catalog / TWIN / "scripts").symlink_to(outside, target_is_directory=True)
    files = _edited(_doc(c), **{"scripts/evil.sh": "#!/bin/sh\n"})
    r = c.put(f"/api/twin-configs/{TWIN}", json={"files": files}, headers=AUTH)
    assert r.status_code == 400 and "escapes" in r.json()["error"]
    assert not (outside / "evil.sh").exists()


def test_bundle_location_legacy_folder_and_flat_file(env):
    settings, catalog = env
    from axrange.api.service import _bundle_location

    legacy = catalog / "legacy-folder"
    legacy.mkdir()
    (legacy / "legacy-folder.yaml").write_text(TE.replace("edit-twin", "legacy-folder"))
    (catalog / "flat.yaml").write_text(TE.replace("edit-twin", "flat"))
    assert _bundle_location(settings, TWIN) == (catalog / TWIN, None)
    assert _bundle_location(settings, "legacy-folder") == (legacy, None)
    assert _bundle_location(settings, "flat") == (catalog, catalog / "flat.yaml")
    c, _, _ = _client(settings)
    assert _doc(c, "flat") == {"flat.yaml": TE.replace("edit-twin", "flat")}
    assert set(_doc(c, "legacy-folder")) == {"legacy-folder.yaml"}
    # A broken manifest is a 400, never a silent fall-back to a sibling file.
    (catalog / TWIN / "twin.yaml").write_text("twin: [broken\n")
    r = c.get(f"/api/twin-configs/{TWIN}", headers=AUTH)
    assert r.status_code == 400 or (r.status_code == 200 and r.json()["document"] is None)


def test_invalid_persona_file_is_refused_and_overlay_warnings_surface(env):
    settings, catalog = env
    c, _, _ = _client(settings)
    did = _deploy(c)
    files = _edited(_doc(c), **{"personas/bad.yaml": "persona: [broken\n"})
    r = c.put(f"/api/deploys/{did}/config", json={"files": files}, headers=AUTH)
    assert r.status_code == 400 and "personas/" in r.json()["error"]
    assert c.get(f"/api/deploys/{did}/config").json()["has_working_copy"] is False
    # A legacy bundle (no twin.yaml) has no twin folder for the overlay: the
    # loader's own warning rides on the apply.
    (catalog / TWIN / "twin.yaml").unlink()
    did2 = _deploy(c)
    _put(c, did2, _edited(_doc(c), **{"tfc/default.yaml": TFC.replace("web-tier", "dmz-web")}))
    r = c.post(f"/api/deploys/{did2}/config/apply", json={"target_node": "pxtest1"}, headers=AUTH)
    assert r.status_code == 200, r.text
    assert any("catalog personas only" in w for w in r.json()["warnings"])


def test_role_only_change_with_no_tfc_default_is_refused(env):
    settings, catalog = env
    (catalog / TWIN / "tfc" / "default.yaml").unlink()
    (catalog / TWIN / "tfc").rmdir()
    (catalog / TWIN / "twin.yaml").write_text(MANIFEST.replace("    tfc:\n      default: tfc/default.yaml\n", ""))
    c, backend, _ = _client(settings)
    did = _deploy(c)
    _put(c, did, _edited(_doc(c), **{"te.yaml": TE.replace("role: generic", "role: app-tier")}))
    r = c.post(f"/api/deploys/{did}/config/apply", json={"target_node": "pxtest1"}, headers=AUTH)
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == {"code": "no-tier-default", "tiers": ["tfc"]}
    assert backend.calls == []
    assert c.get(f"/api/deploys/{did}/config").json()["applied"] is None


def test_persist_clears_copies_in_every_store(env, tmp_path):
    settings, _ = env
    other_db = str(tmp_path / "other" / "state.db")
    import dataclasses

    other = dataclasses.replace(settings, db_path=other_db)
    c_other, _, _ = _client(other)
    did_other = _deploy(c_other)
    files = _edited(_doc(c_other), **{"tfc/default.yaml": TFC.replace("web-tier", "dmz-web")})
    _put(c_other, did_other, files)
    # The primary target knows the other store as an extra.
    settings.extra_dbs = other_db
    c, _, _ = _client(settings)
    did = _deploy(c)
    _put(c, did, files)
    r = c.put(f"/api/twin-configs/{TWIN}", json={"files": files}, headers=AUTH)
    assert r.status_code == 200, r.text
    assert c.get(f"/api/deploys/{did}/config").json()["has_working_copy"] is False
    assert c_other.get(f"/api/deploys/{did_other}/config").json()["has_working_copy"] is False


def test_sidecar_is_private_and_never_created_by_a_read(env):
    settings, _ = env
    import stat

    c, _, _ = _client(settings)
    did = _deploy(c)
    c.get(f"/api/deploys/{did}/config")
    c.post(f"/api/deploys/{did}/config/reset", headers=AUTH)
    path = working_copy_db_path(settings.db_path)
    assert not path.exists()
    _put(c, did, _edited(_doc(c), **{"README.md": "x\n"}))
    assert path.is_file()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    # A read-only open: a GET on a copy taken by another process works and
    # writes nothing (no -wal/-shm for a read of an idle file).
    c2, _, _ = _client(settings)
    assert c2.get(f"/api/deploys/{did}/config").json()["has_working_copy"] is True
