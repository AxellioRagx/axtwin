"""A cast bundle in a `twins/<id>/` folder (`docs/WORK_ORDER_twin_consolidation.md`
Phase 2/3): `te.yaml` via the manifest, machine-written files under
`generated/` -- ONE layout rule (`axrange.twinconfig.bundle`) every imprint,
Console-file, recompile and fidelity reader shares, beside the legacy
aximprint layout (`<id>.yaml` + id-stemmed sidecars at the root).

Each API test builds the SAME content in both layouts from the shipped
`mini-cast-prov` fixture and asserts the two answer alike, so a reader that
still looks at the legacy name inside a twin folder is caught by a
difference rather than by a hand-typed expectation.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from axrange.api import ApiSettings, create_app
from axrange.twinconfig.bundle import (
    bundle_artifact_path,
    bundle_config_path,
    has_cast_artifacts,
    is_twin_folder,
)
from axrange.twinconfig.errors import TwinManifestError

try:
    from starlette.testclient import TestClient
except ImportError:  # pragma: no cover
    pytest.skip("starlette/httpx not installed", allow_module_level=True)

REPO_ROOT = Path(__file__).resolve().parent.parent
CATALOG = REPO_ROOT.parent / "catalog"  # twin content lives at the project root
FIXT_ROOT = REPO_ROOT / "tests" / "fixtures"
MINI = FIXT_ROOT / "cast-bundles" / "mini-cast-prov"
ID = "mini-cast-prov"

_MANIFEST = f"""\
twin:
  id: {ID}
  containment: uncontained
  tiers:
    te:
      default: te.yaml
"""


def _legacy(root: Path) -> Path:
    d = root / ID
    shutil.copytree(MINI, d)
    return d


def _twin_folder(root: Path, *, manifest: str = _MANIFEST) -> Path:
    """`mini-cast-prov` filed into the twin layout -- the same bytes, moved."""
    d = root / ID
    (d / "generated").mkdir(parents=True)
    shutil.copy(MINI / f"{ID}.yaml", d / "te.yaml")
    for kind in ("topology", "provenance"):
        shutil.copy(MINI / f"{ID}.{kind}.json", d / "generated" / f"{ID}.{kind}.json")
    shutil.copytree(MINI / "scripts", d / "scripts")
    (d / "twin.yaml").write_text(manifest)
    return d


def _client(root: Path, tmp_path: Path) -> TestClient:
    return TestClient(create_app(ApiSettings(
        db_path=str(tmp_path / "state.db"), adapter_config="/nonexistent.conf",
        imprints_dir=str(root), images_root=str(FIXT_ROOT),
    )), raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# The helper itself
# ---------------------------------------------------------------------------
def test_the_layout_rule_on_both_layouts(tmp_path):
    legacy = _legacy(tmp_path / "a")
    twin = _twin_folder(tmp_path / "b")
    assert not is_twin_folder(legacy) and is_twin_folder(twin)
    assert bundle_config_path(legacy, ID) == legacy / f"{ID}.yaml"
    assert bundle_config_path(twin, ID) == twin / "te.yaml"
    assert bundle_artifact_path(legacy, "layout.json") == legacy / "layout.json"
    assert bundle_artifact_path(twin, "layout.json") == twin / "generated" / "layout.json"
    assert has_cast_artifacts(legacy, ID) and has_cast_artifacts(twin, ID)


def test_a_hand_authored_twin_folder_carries_no_cast_artifacts():
    """What keeps `twins/sme-flat` from being scored as its own reference."""
    assert not has_cast_artifacts(CATALOG / "twins" / "sme-flat", "sme-flat")
    assert has_cast_artifacts(CATALOG / "twins" / "corpapcn-golden", "corpapcn-golden")


def test_a_broken_manifest_fails_loud_rather_than_falling_back_to_a_sibling(tmp_path):
    twin = _twin_folder(tmp_path, manifest=_MANIFEST.replace(f"id: {ID}", "id: someone-else"))
    with pytest.raises(TwinManifestError):
        bundle_config_path(twin, ID)


# ---------------------------------------------------------------------------
# The imprint API reads a twin folder exactly as it reads the legacy bundle
# ---------------------------------------------------------------------------
def _read_all(c: TestClient) -> dict:
    out = {"list": c.get("/api/twins").json()}
    for route in ("", "/cast", "/provenance", "/topology"):
        r = c.get(f"/api/twins/{ID}{route}")
        assert r.status_code == 200, (route, r.text[:300])
        out[route] = r.json()
    return out


def _scrub(doc, root: Path):
    """Drop the one thing that legitimately differs -- absolute paths."""
    text = json.dumps(doc, sort_keys=True).replace(str(root), "<root>")
    for legacy, twin in ((f"{ID}/{ID}.provenance.json", f"{ID}/generated/{ID}.provenance.json"),
                         (f"{ID}/{ID}.topology.json", f"{ID}/generated/{ID}.topology.json")):
        text = text.replace(twin, legacy)
    return json.loads(text)


def _pop_apply_capability(doc):
    """Remove every `can_apply`/`apply_blocked_reason` pair (in place) and
    return them as `[(can_apply, reason)]`."""
    out = []

    def walk(x):
        if isinstance(x, dict):
            if "can_apply" in x:
                out.append((x.pop("can_apply"), x.pop("apply_blocked_reason", None) or ""))
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk(doc)
    return out


def test_every_read_route_answers_the_same_for_both_layouts(tmp_path):
    a, b = tmp_path / "legacy", tmp_path / "twins"
    _legacy(a)
    _twin_folder(b)
    got_a = _scrub(_read_all(_client(a, tmp_path / "ca")), a)
    got_b = _scrub(_read_all(_client(b, tmp_path / "cb")), b)
    # Apply capability is the ONE legitimate difference (fix round 1): a
    # twins/ folder cannot be recompiled in place, and the read says why.
    why_a, why_b = _pop_apply_capability(got_a), _pop_apply_capability(got_b)
    assert all(not ok for ok, _w in why_a + why_b)
    assert all("twins/ folder" in w for _ok, w in why_b)
    assert got_a == got_b
    assert got_b["/provenance"]["provenance"] == "canonical"  # the file WAS found
    assert got_b["/cast"]["has_topology_export"] is True
    assert got_b["/cast"]["has_provenance"] is True


def test_the_listing_over_a_twins_root_skips_shared_assets_silently(tmp_path):
    root = tmp_path / "twins"
    _twin_folder(root)
    (root / "_shared" / "scripts").mkdir(parents=True)
    body = _client(root, tmp_path).get("/api/twins").json()
    assert [e["id"] for e in body["imprints"]] == [ID]
    assert body["skipped"] == []


def test_a_twin_folder_plans_through_the_api_with_its_scripts(tmp_path, monkeypatch):
    """The relative `scripts/` installs resolve beside `te.yaml` (LES-275)."""
    import tempfile

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    root = tmp_path / "twins"
    _twin_folder(root)
    r = _client(root, tmp_path).post(f"/api/twins/{ID}/plan", json={"target_node": "pxtest1"})
    assert r.status_code == 200, r.text[:400]
    assert r.json()["ops"] > 0


# ---------------------------------------------------------------------------
# Writers put machine-written files where the readers look
# ---------------------------------------------------------------------------
def test_a_layout_pin_lands_in_generated_and_reads_back(tmp_path):
    root = tmp_path / "twins"
    d = _twin_folder(root)
    shutil.rmtree(d / "generated")  # a hand-authored twin's first pin: no generated/ yet
    c = _client(root, tmp_path)
    body = {"schema": "axr-layout/1", "imprint": ID, "pins": {"site:10.5": {"x": 1, "y": 2}}}
    put = c.put(f"/api/twins/{ID}/layout", json=body)
    assert put.status_code == 200, put.text[:300]
    assert (d / "generated" / "layout.json").is_file()
    assert not (d / "layout.json").exists()
    got = c.get(f"/api/twins/{ID}/layout").json()
    assert got["exists"] is True and got["pins"] == body["pins"]


@pytest.mark.parametrize("pins", [
    {"host:address:10.80.20.4": {"x": 12, "y": 8}},
    {"host:dc01": {"x": 0, "y": 0}, "segment:10.80.20.0/24": {"x": 1, "y": 2}, "site:10.80": {"x": 3, "y": 4}},
])
def test_a_host_pin_is_accepted_beside_site_and_segment_pins(tmp_path, pins):
    """The design canvas (`ui/src/features/edit-canvas/SPEC.md`) pins a host
    chip as `host:<name>`; the three kinds round-trip together."""
    root = tmp_path / "twins"
    _twin_folder(root)
    c = _client(root, tmp_path)
    body = {"schema": "axr-layout/1", "imprint": ID, "pins": pins}
    put = c.put(f"/api/twins/{ID}/layout", json=body)
    assert put.status_code == 200, put.text[:300]
    assert c.get(f"/api/twins/{ID}/layout").json()["pins"] == pins


@pytest.mark.parametrize("bad_key,expect", [
    ("cluster:a", "accepted"),            # an unknown pin kind
    ("hostname:dc01", "hosts are NEVER"),  # an entity key is not a pin
    ("host:", "host:<name>"),              # the kind without a name
])
def test_an_unknown_or_entity_key_pin_kind_is_still_refused(tmp_path, bad_key, expect):
    root = tmp_path / "twins"
    d = _twin_folder(root)
    c = _client(root, tmp_path)
    body = {"schema": "axr-layout/1", "imprint": ID, "pins": {bad_key: {"x": 1, "y": 2}}}
    r = c.put(f"/api/twins/{ID}/layout", json=body)
    assert r.status_code == 400, r.text[:300]
    assert expect in r.json()["error"]
    assert not (d / "generated" / "layout.json").exists()


def test_the_assertions_document_stays_at_the_twin_root(tmp_path):
    """Operator content, not machine output: beside `te.yaml`."""
    root = tmp_path / "twins"
    d = _twin_folder(root)
    doc = {"schema": "axr-assertions/1", "imprint": ID, "as_of": "2026-09-26T00:00:00Z",
           "source": "operator: tester", "add": [], "override": [],
           "suppress": [{"key": "address:10.5.0.9", "note": "out of scope"}]}
    r = _client(root, tmp_path).put(f"/api/twins/{ID}/assertions", json=doc)
    assert r.status_code == 200, r.text[:300]
    assert (d / f"{ID}.assertions.yaml").is_file()


def test_apply_refuses_a_twin_folder_before_it_saves_anything(tmp_path):
    """`aximprint install-cast` writes the legacy layout; an apply into a twin
    folder would report success and change nothing a reader sees (D4)."""
    root = tmp_path / "twins"
    d = _twin_folder(root)
    before = sorted(str(p.relative_to(d)) for p in d.rglob("*"))
    doc = {"schema": "axr-assertions/1", "imprint": ID, "as_of": "2026-09-26T00:00:00Z",
           "source": "operator: tester", "add": [], "override": [], "suppress": []}
    r = _client(root, tmp_path).post(f"/api/twins/{ID}/apply", json={"document": doc})
    assert r.status_code == 422, r.text[:300]
    assert "D4" in r.json()["error"]
    assert sorted(str(p.relative_to(d)) for p in d.rglob("*")) == before


def test_the_bundle_fingerprint_does_not_depend_on_the_layout(tmp_path):
    from axrange.api.recompile import bundle_fingerprint, history_dir

    legacy = _legacy(tmp_path / "a")
    twin = _twin_folder(tmp_path / "b")
    assert bundle_fingerprint(legacy, ID) == bundle_fingerprint(twin, ID) is not None
    assert history_dir(twin) == twin / "generated" / ".history"


def test_the_tap_plan_sensors_come_from_the_manifests_tap_tier(tmp_path):
    from axrange.api.recompile import sensors_from_tap_plan

    d = _twin_folder(tmp_path, manifest=_MANIFEST + "    tap:\n      default: tap/cast.yaml\n")
    assert sensors_from_tap_plan(d) == ((), [])  # declared file absent -> manifest refuses first
    (d / "tap").mkdir()
    (d / "tap" / "cast.yaml").write_text(
        f"tap-plan:\n  target: {ID}\n  bindings:\n"
        "    - selector: { scope: segment, target: lan }\n      sensors: [XPress-SW-A]\n"
    )
    assert sensors_from_tap_plan(d) == (("XPress-SW-A",), [])


# ---------------------------------------------------------------------------
# Fidelity: the default reference root is twins/, casts only
# ---------------------------------------------------------------------------
def test_the_default_reference_resolves_a_cast_twin_and_not_a_hand_authored_one():
    from axrange.fidelity.reference import default_reference_root, resolve_bundle_dir

    assert default_reference_root() == CATALOG / "twins"
    assert resolve_bundle_dir(bundle_id="corpapcn-twin") == CATALOG / "twins" / "corpapcn-twin"
    assert resolve_bundle_dir(bundle_id="sme-flat") is None


def test_a_scorecard_is_written_under_generated_in_a_twin_folder(tmp_path):
    from axrange.fidelity import scorecard_output_path

    twin = _twin_folder(tmp_path / "b")
    legacy = _legacy(tmp_path / "a")
    assert scorecard_output_path(twin, ID, "d-1") == twin / "generated" / f"{ID}.scorecard.d-1.json"
    assert scorecard_output_path(legacy, ID, "d-1") == legacy / f"{ID}.scorecard.d-1.json"


def test_the_real_cast_twin_loads_as_a_reference_bundle():
    from axrange.fidelity.reference import load_reference_bundle

    b = load_reference_bundle(CATALOG / "twins" / "corpapcn-golden")
    assert b.provenance_sidecar == "present"
    assert not any("no corpapcn-golden.topology.json" in w for w in b.warnings), b.warnings


# ---------------------------------------------------------------------------
# The new spellings keep the old ones as aliases
# ---------------------------------------------------------------------------
def test_twins_dir_and_the_old_twin_configs_dir_flag_are_one_option():
    from axrange.cli.main import _build_parser

    p = _build_parser()
    for flag in ("--twins-dir", "--twin-configs-dir"):
        assert p.parse_args(["twin-config", "list", flag, "x"]).twin_configs_dir == "x"
        assert p.parse_args(["te", "plan", "--config", "a", flag, "x"]).twin_configs_dir == "x"
        assert p.parse_args(["serve", flag, "x"]).twin_configs_dir == "x"


def test_axrange_twins_dir_wins_over_the_old_env_var(monkeypatch):
    monkeypatch.setenv("AXRANGE_TWIN_CONFIGS_DIR", "old")
    assert ApiSettings().twin_configs_dir == "old"
    monkeypatch.setenv("AXRANGE_TWINS_DIR", "new")
    assert ApiSettings().twin_configs_dir == "new"


def test_every_migrated_twin_folder_has_a_valid_manifest():
    """Phase 2's migration: each folder loads, and its id is its folder."""
    from axrange.twinconfig.manifest import load_twin_manifest

    folders = [d for d in sorted((CATALOG / "twins").iterdir())
               if d.is_dir() and not d.name.startswith(("_", "."))]
    assert {d.name for d in folders} >= {
        "corp-enterprise", "datacenter", "dmz-perimeter", "ics-ot", "sme-flat",
        "svc-engines", "svc-verify", "corpapcn-golden", "corpapcn-twin",
        "pit-395a61f04e-1780405200",
    }
    for d in folders:
        assert load_twin_manifest(d).id == d.name
    # The dated maxrate record is archived, not a twin (D6).
    assert not os.path.exists(CATALOG / "twin-configs" / "corp-enterprise-maxrate")
