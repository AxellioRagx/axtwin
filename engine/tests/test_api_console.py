"""Tests for the Console canvas API additions (`docs/WORK_ORDER_console_canvas.md`
S1.1-S1.8; `docs/design/15-console-canvas.md` §10).

Covers, over a Starlette TestClient:

  - S1.1 `GET /api/twins/{id}/topology` -- the ALIAS (D1 (a)): asserted to
    return the SAME payload as the shipped `?source=imprint` route, which is
    the only way "no second reader" is actually a test rather than a claim.
  - S1.2 `GET .../cast`             -- nulls, not zeros, on an older bundle.
  - S1.3 `GET`/`PUT .../assertions` -- round-trip, etag guard, closed sets.
  - S1.4 `GET .../provenance`       -- canonical / unrecognized / absent.
  - S1.5 `POST .../apply`           -- save + recompile behind a FAKE backend.
  - S1.6 `GET`/`PUT .../layout`     -- pins; a host key is refused.
  - S1.7 `GET .../diff?since=`      -- apply-produced fingerprints only.
  - S1.8 catalog plumbing           -- tolerant listing + `--imprints-dir`.

and, from S2 (the SPA slice's two API-side needs):

  - the `suppressed_keys` authored-key join on `GET .../diff` (§12.6);
  - the SPA history fallback `_maybe_mount_spa` grew, so a deep Console route
    survives a reload/bookmark/paste (`docs/design/15-console-canvas.md` §7).

**Three catalogs, deliberately** (LES-185 -- a hand-written fixture cannot
prove this works on what ships):

  * `axrange/api/fixtures/imprints/`      the packaged NORMALIZED fixture;
  * `tests/fixtures/cast-bundles/`        `mini-cast` (no sidecar) beside
                                          `mini-cast-prov` (CANONICAL) -- the
                                          stable pair every write test copies
                                          into `tmp_path`;
  * `twins/` (was `twin-configs-cast/`)   the REAL bundles: `corpapcn-twin`
                                          (786/1437/32, no sidecar) and
                                          `corpapcn-golden` (`axr-topology/
                                          1.4`, 687 canonical fields).

Read-only assertions run against the real bundles directly. **Every write
test copies a bundle into `tmp_path` first** -- a test that wrote an
assertions file into `twins/` would mutate checked-in content.

The real bundles are `twins/<id>/` FOLDERS (`te.yaml`, `generated/<id>.*.json`
-- `axrange.twinconfig.bundle`); `_real_config`/`_real_artifact` resolve their
files by the same rule the readers use. `test_the_real_bundles_are_present`
fails -- rather than letting every `requires_real_*` test skip -- if they move.

The recompile subprocess is NEVER executed by this suite: `app.state.
recompile_backend` takes a fake, and the one test that pins the real argv
calls `SubprocessRecompile.build_argv` without running it.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from axrange.api import ApiSettings, create_app

try:
    from starlette.testclient import TestClient
except ImportError:  # pragma: no cover
    pytest.skip("starlette/httpx not installed", allow_module_level=True)


REPO_ROOT = Path(__file__).resolve().parent.parent
CATALOG = REPO_ROOT.parent / "catalog"  # twin content lives at the project root
CAST_FIXTURES = REPO_ROOT / "tests" / "fixtures" / "cast-bundles"
REAL_CAST_ROOT = CATALOG / "twins"
REAL_SIDECARLESS = REAL_CAST_ROOT / "corpapcn-twin"
REAL_GOLDEN = REAL_CAST_ROOT / "corpapcn-golden"

# The canonical-sidecar path is covered UNCONDITIONALLY by `mini-cast-prov`;
# these marks only gate the extra, real-bundle proofs, so losing the golden
# bundle can never silently remove canonical coverage.
requires_real_golden = pytest.mark.skipif(
    not REAL_GOLDEN.is_dir(), reason="twins/corpapcn-golden not present"
)
requires_real_sidecarless = pytest.mark.skipif(
    not REAL_SIDECARLESS.is_dir(), reason="twins/corpapcn-twin not present"
)


def _real_config(bundle: str) -> Path:
    """A real bundle's twin-config file (`twins/<id>/te.yaml`)."""
    from axrange.twinconfig.bundle import bundle_config_path

    return bundle_config_path(REAL_CAST_ROOT / bundle, bundle)


def _real_artifact(bundle_dir: Path, name: str) -> Path:
    """A bundle's machine-written file -- `generated/<name>` in a twin folder."""
    from axrange.twinconfig.bundle import bundle_artifact_path

    return bundle_artifact_path(bundle_dir, name)


def test_the_real_bundles_are_present():
    """The real-bundle tests below are `skipif`-guarded; this is not. A move
    of the real casts (it happened once: `twin-configs-cast/` -> `twins/`)
    must fail here, never turn those tests silently green."""
    for bundle in ("corpapcn-golden", "corpapcn-twin", "pit-395a61f04e-1780405200"):
        assert _real_config(bundle).is_file(), bundle
        assert _real_artifact(REAL_CAST_ROOT / bundle, f"{bundle}.topology.json").is_file()

_VALID_DOC = {
    "schema": "axr-assertions/2",
    "imprint": "mini-cast-prov",
    "as_of": "2026-09-05T12:00:00Z",
    "source": "operator: tester",
    "add": [
        {
            "kind": "segment", "key": "10.80.31.0/24", "name": "seg-10-80-31",
            "zone_type": "vlan", "note": "pruned by the cast; it is the OT lab",
        },
        {
            "kind": "host", "key": "address:10.80.20.5",
            "segment": "10.80.20.0/24", "role": "domain-controller",
            "listen": [{"port": 88, "proto": "tcp"}, {"port": 389, "proto": "tcp"}],
            "note": "the DC the vantage never saw answer",
        },
    ],
    "override": [
        {
            "key": "address:10.80.20.9", "field": "role", "value": "file-server",
            "note": "imputed dns-resolver is wrong; it serves SMB",
        },
    ],
    "suppress": [
        {"key": "address:203.0.113.9", "note": "out of scope: a cloud CDN edge"},
    ],
}


def _settings(imprints_dir: Path, tmp_path: Path, *, token: str = "") -> ApiSettings:
    return ApiSettings(
        db_path=str(tmp_path / "state.db"),
        adapter_config="/nonexistent.conf",
        imprints_dir=str(imprints_dir),
        api_token=token,
    )


def _client(settings: ApiSettings, *, backend=None) -> TestClient:
    app = create_app(settings)
    if backend is not None:
        app.state.recompile_backend = backend
    return TestClient(app)


def _copy_bundle(tmp_path: Path, bundle: str, src_root: Path = CAST_FIXTURES) -> Path:
    """A writable copy of a bundle catalog holding exactly `bundle` -- every
    write test runs here, never against checked-in content."""
    root = tmp_path / "imprints"
    root.mkdir(exist_ok=True)
    shutil.copytree(src_root / bundle, root / bundle)
    return root


def _doc(**overrides):
    d = json.loads(json.dumps(_VALID_DOC))
    d.update(overrides)
    return d


# ===========================================================================
# S1.1 -- GET /api/twins/{id}/topology (the alias, D1 (a))
# ===========================================================================
def test_imprint_topology_is_identical_to_the_shipped_export_route(tmp_path):
    """The alias must be the SAME payload from the SAME reader -- if these
    two ever diverge, a second reader has appeared (LES-025/LES-037)."""
    c = _client(_settings(CAST_FIXTURES, tmp_path))
    alias = c.get("/api/twins/mini-cast-prov/topology")
    shipped = c.get(
        "/api/scenarios/mini-cast-prov/topology-export", params={"source": "imprint"},
    )
    assert alias.status_code == 200 and shipped.status_code == 200
    assert alias.json() == shipped.json()


def test_imprint_topology_carries_the_fixture_export_content(tmp_path):
    c = _client(_settings(CAST_FIXTURES, tmp_path))
    j = c.get("/api/twins/mini-cast-prov/topology").json()
    assert j["pit_id"]
    assert j["nodes"] and j["segments"]
    assert j["census"]["entities_total"] >= len(j["nodes"])


@requires_real_sidecarless
def test_imprint_topology_on_the_real_corpapcn_bundle(tmp_path):
    """LES-185: the shipped 786/1437/32 bundle, through the real route."""
    c = _client(_settings(REAL_CAST_ROOT, tmp_path))
    r = c.get("/api/twins/corpapcn-twin/topology")
    assert r.status_code == 200
    j = r.json()
    assert len(j["nodes"]) == 786
    assert len(j["edges"]) == 1437
    assert len(j["segments"]) == 32
    assert j["census"] == {
        "entities_total": 786, "with_role": 356, "placed": 141, "local": 190,
    }


def test_imprint_topology_unknown_id_404(tmp_path):
    c = _client(_settings(CAST_FIXTURES, tmp_path))
    assert c.get("/api/twins/does-not-exist/topology").status_code == 404


@pytest.mark.parametrize(
    "route", ["topology", "cast", "assertions", "layout", "provenance"],
)
def test_console_reads_refuse_path_traversal(tmp_path, route):
    c = _client(_settings(CAST_FIXTURES, tmp_path))
    for bad in ("..", "%2e%2e"):
        r = c.get(f"/api/twins/{bad}/{route}")
        assert r.status_code == 404, (bad, route, r.status_code)


# ===========================================================================
# S1.2 -- GET /api/twins/{id}/cast
# ===========================================================================
def test_cast_summary_canonical_fixture(tmp_path):
    c = _client(_settings(CAST_FIXTURES, tmp_path))
    j = c.get("/api/twins/mini-cast-prov/cast").json()
    assert j["id"] == "mini-cast-prov"
    assert j["twin_config_id"] == "mini-cast-prov"
    assert j["pit_id"] == "pit-miniprov-0000000001"
    assert j["code_fingerprint"] == "fixture-0000000000"
    assert j["params"]["DEPLOY_MIN_OCCUPANCY"] == 0.8
    assert j["deploy_min_occupancy"] == 0.8
    assert j["deploy_min_occupancy_source"] == "declared"
    assert j["has_provenance"] is True
    assert j["provenance"] == "canonical"
    assert j["provenance_path"].endswith("mini-cast-prov.provenance.json")
    assert j["has_assertions"] is False and j["has_layout"] is False
    assert j["segments"] > 0 and j["node_specs"] > 0 and j["vms"] >= j["node_specs"]
    assert j["observed_nodes"] > 0


def test_cast_summary_reports_absent_as_null_never_zero(tmp_path):
    """The house rule, tested on a real absence: a bundle with no sidecar
    publishes `code_fingerprint: null` -- `0.0`/`""` would say something
    false (`docs/WORK_ORDER_console_canvas.md` S1.2)."""
    c = _client(_settings(CAST_FIXTURES, tmp_path))
    j = c.get("/api/twins/mini-cast/cast").json()
    assert j["code_fingerprint"] is None
    assert j["has_provenance"] is False
    assert j["provenance_schema"] is None
    assert j["provenance_path"] is None
    assert j["provenance"] == "absent"
    assert j["evidence_source"] == "re-derived from topology export"


@requires_real_sidecarless
def test_cast_summary_real_sidecarless_bundle_uses_the_engine_default_threshold(tmp_path):
    """`corpapcn-twin` declares no params at all, so the EFFECTIVE threshold
    is the engine's -- published with its source rather than as if the cast
    had asked for it."""
    c = _client(_settings(REAL_CAST_ROOT, tmp_path))
    j = c.get("/api/twins/corpapcn-twin/cast").json()
    assert j["params"] == {}
    assert j["deploy_min_occupancy"] == 0.8
    assert j["deploy_min_occupancy_source"] == "engine default"
    assert j["bundle_schema"] is None          # predates `axr-topology/1.4`
    assert j["topology_schema_version"] == "1.3"
    assert j["code_fingerprint"] is None
    assert j["observed_nodes"] == 786 and j["observed_edges"] == 1437
    assert j["vms"] == 138 and j["node_specs"] == 62 and j["segments"] == 27


@requires_real_golden
def test_cast_summary_real_golden_bundle_publishes_the_1_4_schema_and_fingerprint(tmp_path):
    c = _client(_settings(REAL_CAST_ROOT, tmp_path))
    j = c.get("/api/twins/corpapcn-golden/cast").json()
    # `axr-topology/1.5` since the 2026-09-06 recast that added
    # `cast_invocation` (R3). Pinned to the exact string on purpose: the
    # Console must refuse a MAJOR version it does not know rather than
    # render a partial graph (work order §9 seam 1).
    assert j["bundle_schema"] == "axr-topology/1.5"
    # Two DIFFERENT numbers on one bundle -- the network-IR version and the
    # bundle schema. Publishing one as the other would be a silent lie.
    assert j["topology_schema_version"] == "1.3"
    assert j["code_fingerprint"] and j["code_fingerprint"] != "absent"
    assert j["has_provenance"] is True
    assert j["deploy_min_occupancy_source"] == "declared"


def test_cast_summary_unknown_id_404(tmp_path):
    c = _client(_settings(CAST_FIXTURES, tmp_path))
    assert c.get("/api/twins/nope/cast").status_code == 404


# ===========================================================================
# S1.3 -- GET / PUT /api/twins/{id}/assertions
# ===========================================================================
def test_assertions_get_on_a_bundle_with_no_file_is_an_empty_200(tmp_path):
    c = _client(_settings(CAST_FIXTURES, tmp_path))
    r = c.get("/api/twins/mini-cast-prov/assertions")
    assert r.status_code == 200
    j = r.json()
    assert j["exists"] is False
    assert j["etag"] is None
    assert j["schema"] == "axr-assertions/2"
    assert j["imprint"] == "mini-cast-prov"
    assert j["add"] == [] and j["override"] == [] and j["suppress"] == []
    assert "ETag" not in r.headers


def test_assertions_put_round_trip_writes_the_file_and_sets_an_etag(tmp_path):
    root = _copy_bundle(tmp_path, "mini-cast-prov")
    c = _client(_settings(root, tmp_path))

    put = c.put("/api/twins/mini-cast-prov/assertions", json=_VALID_DOC)
    assert put.status_code == 200, put.text
    j = put.json()
    assert j["exists"] is True and j["etag"]
    assert put.headers["ETag"] == f'"{j["etag"]}"'
    assert len(j["add"]) == 2 and len(j["override"]) == 1 and len(j["suppress"]) == 1

    on_disk = root / "mini-cast-prov" / "mini-cast-prov.assertions.yaml"
    assert on_disk.is_file()

    get = c.get("/api/twins/mini-cast-prov/assertions")
    assert get.json()["etag"] == j["etag"]
    assert get.json()["add"] == j["add"]
    # The GET payload is exactly what PUT accepts (round-trip symmetry: the
    # read-only decorations are stripped server-side).
    again = c.put("/api/twins/mini-cast-prov/assertions", json=get.json())
    assert again.status_code == 200
    # ... and a no-op save leaves the file byte-identical (`14` §3's
    # determinism rule -- a churning file breaks no-op-apply determinism).
    assert again.json()["etag"] == j["etag"]


def test_a_clone_shaped_batch_re_saves_byte_identical(tmp_path):
    """Acceptance 2's BYTE half, for a batch (review S6).

    The Console proves a clone's document against the vendored `axr-assertions/2`
    schema and re-emits it in JSON -- it has no YAML writer, and the bytes on
    disk are the server's. This is the missing half: a clone-shaped document
    (one segment + 17 hosts carrying `dial`, `image`, `resources` and the long
    propagated `cloned from ...` note, plus two mirrored conduits) written
    through the real writer, read back, and re-saved -- byte-identical.

    Byte-identity is not cosmetic here: a churning file breaks the no-op-apply
    determinism rule `14` §3 states, and a batch is where it is most at risk,
    since every record carries the same long note and the same emitter runs 20
    times on one save.
    """
    root = _copy_bundle(tmp_path, "mini-cast-prov")
    c = _client(_settings(root, tmp_path))
    note = "cloned from 10.80.20.0/24: a second lab floor for the OT conduit rehearsal"
    clone = "10.80.31.0/24"
    add = [
        {
            "kind": "segment", "key": clone, "name": "seg-10-80-31-0",
            "zone_type": "vlan", "note": note,
        },
    ]
    for i in range(17):
        host = {
            "kind": "host", "key": f"address:10.80.31.{10 + i}", "segment": clone,
            "role": "corp-workstation" if i else "domain-controller",
            "listen": [{"port": 445, "proto": "tcp"}, {"port": 88, "proto": "tcp"}],
            "dial": [{"port": 53, "proto": "udp"}, {"port": 443, "proto": "tcp"}],
            "note": note,
        }
        if i == 0:
            host["image"] = "windows-server-2022"
            host["resources"] = {"cpu": 4, "ram": "8192", "disk": "80G"}
        add.append(host)
    add.append({"kind": "conduit", "key": f"10.20.193.0/24|{clone}", "note": note})
    add.append({"kind": "conduit", "key": f"{clone}|10.80.72.0/24", "note": note})

    doc = _doc(add=add, override=[], suppress=[])
    put = c.put("/api/twins/mini-cast-prov/assertions", json=doc)
    assert put.status_code == 200, put.text
    on_disk = root / "mini-cast-prov" / "mini-cast-prov.assertions.yaml"
    first = on_disk.read_bytes()
    assert len(put.json()["add"]) == 20

    # Read it back and save exactly that -- the round trip the Console makes.
    got = c.get("/api/twins/mini-cast-prov/assertions").json()
    assert got["add"] == put.json()["add"]
    again = c.put("/api/twins/mini-cast-prov/assertions", json=got)
    assert again.status_code == 200, again.text
    assert on_disk.read_bytes() == first
    assert again.json()["etag"] == put.json()["etag"]

    # The order the batch was staged in is the order on disk: segment, hosts in
    # address order, then the conduits by key. A reordering writer would still
    # be "byte-identical on re-save" and would still be wrong.
    assert [r["key"] for r in again.json()["add"]] == [r["key"] for r in add]
    # Every record kept its note, and the two that carry image/sizing kept them.
    assert {r["note"] for r in again.json()["add"]} == {note}
    first_host = next(r for r in again.json()["add"] if r["key"] == "address:10.80.31.10")
    assert first_host["image"] == "windows-server-2022"
    assert first_host["resources"] == {"cpu": 4, "ram": "8192", "disk": "80G"}
    assert first_host["dial"] == [{"port": 53, "proto": "udp"}, {"port": 443, "proto": "tcp"}]

    # The ENVELOPE never reaches the file: `etag` is a request field, and the
    # assertions schema does not describe it (reviewer's "one thing to check").
    text = on_disk.read_text()
    assert "etag" not in text
    from ruamel.yaml import YAML

    raw = YAML(typ="safe").load(text)
    assert "etag" not in raw
    assert set(raw) <= {"schema", "imprint", "as_of", "source", "add", "override", "suppress"}


def test_assertions_put_with_a_stale_etag_is_412_and_leaves_the_file_untouched(tmp_path):
    root = _copy_bundle(tmp_path, "mini-cast-prov")
    c = _client(_settings(root, tmp_path))
    first = c.put("/api/twins/mini-cast-prov/assertions", json=_VALID_DOC).json()
    path = root / "mini-cast-prov" / "mini-cast-prov.assertions.yaml"
    before = path.read_bytes()

    changed = _doc(source="operator: someone-else")
    changed["etag"] = "0" * 64
    r = c.put("/api/twins/mini-cast-prov/assertions", json=changed)
    assert r.status_code == 412
    # The refusal NAMES the on-disk etag so the caller can reload.
    assert first["etag"] in r.json()["error"]
    assert r.json()["detail"]["etag"] == first["etag"]
    assert path.read_bytes() == before  # never a silent merge


def test_assertions_put_with_no_etag_against_an_existing_file_is_refused(tmp_path):
    root = _copy_bundle(tmp_path, "mini-cast-prov")
    c = _client(_settings(root, tmp_path))
    c.put("/api/twins/mini-cast-prov/assertions", json=_VALID_DOC)
    r = c.put("/api/twins/mini-cast-prov/assertions", json=_doc(source="operator: x"))
    assert r.status_code == 412
    assert "If-Match" in r.json()["error"]


def test_assertions_etag_is_accepted_in_the_if_match_header_too(tmp_path):
    """Both channels work, so a proxy that strips `If-Match` cannot turn a
    guarded write into an unguarded one (work order §3)."""
    root = _copy_bundle(tmp_path, "mini-cast-prov")
    c = _client(_settings(root, tmp_path))
    etag = c.put("/api/twins/mini-cast-prov/assertions", json=_VALID_DOC).json()["etag"]
    r = c.put(
        "/api/twins/mini-cast-prov/assertions",
        json=_doc(source="operator: second"),
        headers={"If-Match": f'"{etag}"'},
    )
    assert r.status_code == 200
    assert r.json()["source"] == "operator: second"


def test_assertions_disagreeing_etags_are_refused_rather_than_guessed(tmp_path):
    root = _copy_bundle(tmp_path, "mini-cast-prov")
    c = _client(_settings(root, tmp_path))
    etag = c.put("/api/twins/mini-cast-prov/assertions", json=_VALID_DOC).json()["etag"]
    body = _doc(source="operator: second")
    body["etag"] = "f" * 64
    r = c.put(
        "/api/twins/mini-cast-prov/assertions", json=body,
        headers={"If-Match": f'"{etag}"'},
    )
    assert r.status_code == 400
    assert "disagree" in r.json()["error"]


@pytest.mark.parametrize(
    "mutate,expect",
    [
        # kind vocabulary
        (lambda d: d["add"].append(
            {"kind": "network", "key": "10.9.0.0/24", "name": "n", "note": "x"}), "'segment'"),
        # `route` refused BY NAME (`14` §3)
        (lambda d: d["override"].append(
            {"key": "address:10.0.0.1", "field": "route", "value": "x", "note": "n"}), "route"),
        # override field closed per kind
        (lambda d: d["override"].append(
            {"key": "address:10.0.0.1", "field": "hostname", "value": "x", "note": "n"}),
         "accepted"),
        # nothing is overridable on a conduit
        (lambda d: d["override"].append(
            {"key": "10.1.0.0/24|10.2.0.0/24", "field": "role", "value": "x", "note": "n"}),
         "conduit"),
        # suppress takes host keys only
        (lambda d: d["suppress"].append({"key": "10.9.0.0/24", "note": "n"}), "host"),
        # a segment add needs a name
        (lambda d: d["add"].append(
            {"kind": "segment", "key": "10.9.0.0/24", "note": "n"}), "name"),
        # a host add needs a segment
        (lambda d: d["add"].append(
            {"kind": "host", "key": "address:10.9.0.4", "note": "n"}), "segment"),
        # every record carries a note
        (lambda d: d["add"].append(
            {"kind": "host", "key": "address:10.9.0.5", "segment": "10.9.0.0/24"}), "note"),
        # `confidence` is implied 1.0 and never authored
        (lambda d: d["override"].append(
            {"key": "address:10.0.0.2", "field": "role", "value": "x",
             "note": "n", "confidence": 0.5}), "confidence"),
        # closed top level
        (lambda d: d.update({"overrides": []}), "unknown top-level"),
        # an unusable key shape
        (lambda d: d["add"].append(
            {"kind": "host", "key": "dc01", "segment": "10.9.0.0/24", "note": "n"}),
         "accepted"),
        # a listen port that is not a port
        (lambda d: d["add"].append(
            {"kind": "host", "key": "address:10.9.0.6", "segment": "10.9.0.0/24",
             "listen": [{"port": "eighty", "proto": "tcp"}], "note": "n"}), "port"),
    ],
)
def test_assertions_put_refuses_a_bad_record_naming_the_accepted_set(tmp_path, mutate, expect):
    root = _copy_bundle(tmp_path, "mini-cast-prov")
    c = _client(_settings(root, tmp_path))
    d = _doc()
    mutate(d)
    r = c.put("/api/twins/mini-cast-prov/assertions", json=d)
    assert r.status_code == 400, r.text
    assert expect in r.json()["error"], r.json()["error"]
    assert not (root / "mini-cast-prov" / "mini-cast-prov.assertions.yaml").exists()


def test_assertions_put_refuses_a_key_in_both_add_and_suppress(tmp_path):
    root = _copy_bundle(tmp_path, "mini-cast-prov")
    c = _client(_settings(root, tmp_path))
    d = _doc()
    d["suppress"].append({"key": "address:10.80.20.5", "note": "also suppressed"})
    r = c.put("/api/twins/mini-cast-prov/assertions", json=d)
    assert r.status_code == 400
    assert "both `add` and `suppress`" in r.json()["error"]


def test_assertions_put_refuses_a_key_field_in_both_add_and_override(tmp_path):
    root = _copy_bundle(tmp_path, "mini-cast-prov")
    c = _client(_settings(root, tmp_path))
    d = _doc()
    d["override"].append(
        {"key": "address:10.80.20.5", "field": "role", "value": "web", "note": "n"}
    )
    r = c.put("/api/twins/mini-cast-prov/assertions", json=d)
    assert r.status_code == 400
    assert "both `add` and `override`" in r.json()["error"]


def test_assertions_put_refuses_a_document_for_another_imprint(tmp_path):
    root = _copy_bundle(tmp_path, "mini-cast-prov")
    c = _client(_settings(root, tmp_path))
    r = c.put(
        "/api/twins/mini-cast-prov/assertions", json=_doc(imprint="somebody-else"),
    )
    assert r.status_code == 400
    assert "somebody-else" in r.json()["error"]


def test_assertions_put_refuses_a_foreign_schema(tmp_path):
    root = _copy_bundle(tmp_path, "mini-cast-prov")
    c = _client(_settings(root, tmp_path))
    r = c.put(
        "/api/twins/mini-cast-prov/assertions", json=_doc(schema="axr-assertions/9"),
    )
    assert r.status_code == 400
    assert "axr-assertions/2" in r.json()["error"], "the refusal names the accepted schemas"


def test_assertions_written_file_is_readable_yaml_in_the_authored_vocabulary(tmp_path):
    """The FILE is the cross-repo contract (aximprint reads it), so assert on
    the bytes, not only on the response DTO."""
    from ruamel.yaml import YAML

    root = _copy_bundle(tmp_path, "mini-cast-prov")
    c = _client(_settings(root, tmp_path))
    c.put("/api/twins/mini-cast-prov/assertions", json=_VALID_DOC)
    raw = YAML(typ="safe").load(
        (root / "mini-cast-prov" / "mini-cast-prov.assertions.yaml").read_text()
    )
    assert raw["schema"] == "axr-assertions/2"
    assert raw["imprint"] == "mini-cast-prov"
    assert raw["source"] == "operator: tester"
    assert raw["add"][0]["kind"] == "segment"
    assert raw["add"][1]["listen"] == [
        {"port": 88, "proto": "tcp"}, {"port": 389, "proto": "tcp"},
    ]
    assert raw["suppress"][0]["key"] == "address:203.0.113.9"
    # No transport decoration leaks into the file.
    assert "etag" not in raw and "imprint_id" not in raw


def test_assertions_put_requires_the_write_token(tmp_path):
    root = _copy_bundle(tmp_path, "mini-cast-prov")
    c = _client(_settings(root, tmp_path, token="s3cret"))
    assert c.put("/api/twins/mini-cast-prov/assertions", json=_VALID_DOC).status_code == 401
    ok = c.put(
        "/api/twins/mini-cast-prov/assertions", json=_VALID_DOC,
        headers={"Authorization": "Bearer s3cret"},
    )
    assert ok.status_code == 200
    # ... and the read stays open on a trusted bind, like every other read.
    assert c.get("/api/twins/mini-cast-prov/assertions").status_code == 200


def test_assertions_put_unknown_imprint_404(tmp_path):
    c = _client(_settings(CAST_FIXTURES, tmp_path))
    assert c.put("/api/twins/nope/assertions", json=_VALID_DOC).status_code == 404


# ===========================================================================
# S1.4 -- GET /api/twins/{id}/provenance (the two shapes + absent)
# ===========================================================================
def test_provenance_of_the_packaged_fixture_is_canonical(tmp_path):
    """The packaged fixture carries a canonical provenance file (the Track-C
    normalized shape it used to carry was retired 2026-10-29), read by the
    same adapter as every cast bundle."""
    c = _client(ApiSettings(db_path=str(tmp_path / "s.db"), adapter_config="/nonexistent.conf"))
    j = c.get("/api/twins/corp-enterprise/provenance").json()
    assert j["provenance"] == "canonical"
    assert j["sidecar_schema"] == "axr-cast-provenance/1"
    assert j["evidence_source"] == "sidecar fields"
    # The fixture ships no topology export; the fidelity reader says so.
    assert len(j["warnings"]) == 1 and "no corp-enterprise.topology.json" in j["warnings"][0]
    assert j["population"]["placed_hosts"] == 42
    assert j["code_fingerprint"] is None  # a hand-written fixture: no build
    row = next(r for r in j["rows"] if r["field"] == "twin_config.segments[0].cidr")
    assert row["evidence_class"] == "observed"
    assert row["confidence"] == pytest.approx(0.91)
    assert row["has_confidence"] is True
    assert row["evidence"] and row["signal"]
    assert row["overrides"] is None


def test_provenance_canonical_sidecar_folds_to_rows_with_the_six_classes(tmp_path):
    c = _client(_settings(CAST_FIXTURES, tmp_path))
    j = c.get("/api/twins/mini-cast-prov/provenance").json()
    assert j["provenance"] == "canonical"
    assert j["sidecar_schema"] == "axr-cast-provenance/1"
    assert j["evidence_source"] == "sidecar fields"
    assert j["code_fingerprint"] == "fixture-0000000000"
    assert j["population"]["role_less"] == ["address:10.1.0.13"]
    assert j["rows"], "a canonical sidecar's fields must produce rows"

    classes = {r["evidence_class"] for r in j["rows"]}
    six = {"asserted", "imported", "observed", "referenced", "imputed", "compiler"}
    assert classes <= six, classes
    assert {"asserted", "observed", "imputed", "compiler"} & classes

    # An override is VISIBLE, never an erasure (`14` §2's house rule).
    asserted = next(r for r in j["rows"] if r["evidence_class"] == "asserted")
    assert asserted["overrides"]["evidence_class"] == "imputed"
    assert asserted["overrides"]["value"] == "dns-resolver"
    assert asserted["confidence"] == pytest.approx(1.0)
    assert asserted["has_confidence"] is True

    # A `compiler` row has NO confidence by definition -- 0.0 for the shipped
    # plugin's `.toFixed(2)`, with `has_confidence: false` saying so.
    compiler = next(r for r in j["rows"] if r["evidence_class"] == "compiler")
    assert compiler["has_confidence"] is False
    assert compiler["confidence"] == 0.0
    assert compiler["source"]


def test_provenance_absent_sidecar_is_a_200_that_says_so(tmp_path):
    c = _client(_settings(CAST_FIXTURES, tmp_path))
    r = c.get("/api/twins/mini-cast/provenance")
    assert r.status_code == 200
    j = r.json()
    assert j["provenance"] == "absent"
    assert j["rows"] == []
    assert j["evidence_source"] == "re-derived from topology export"
    assert any("provenance sidecar absent" in w for w in j["warnings"])


@requires_real_golden
def test_provenance_real_golden_bundle_reads_every_field(tmp_path):
    c = _client(_settings(REAL_CAST_ROOT, tmp_path))
    j = c.get("/api/twins/corpapcn-golden/provenance").json()
    assert j["provenance"] == "canonical"
    assert len(j["rows"]) > 500          # 687 fields on the 2026-09-05 cast
    six = {"asserted", "imported", "observed", "referenced", "imputed", "compiler"}
    assert {r["evidence_class"] for r in j["rows"]} <= six
    assert j["population"], "the real sidecar carries a population block"
    assert j["code_fingerprint"]


@requires_real_sidecarless
def test_provenance_real_sidecarless_bundle_reports_absent(tmp_path):
    c = _client(_settings(REAL_CAST_ROOT, tmp_path))
    j = c.get("/api/twins/corpapcn-twin/provenance").json()
    assert j["provenance"] == "absent"
    assert j["rows"] == []


def test_provenance_malformed_sidecar_is_still_400(tmp_path):
    """The tolerance is about ABSENCE; a present-but-broken sidecar keeps its
    loud failure."""
    root = tmp_path / "imprints"
    (root / "broken").mkdir(parents=True)
    (root / "broken" / "broken.yaml").write_text(
        "twin_config:\n  id: broken\n  segments: [{ name: lan, cidr: 10.5.0.0/24 }]\n"
        "  nodes:\n    - { name: h1, image: debian-12-cloud@v1, requires: [linux], net: lan }\n"
    )
    (root / "broken" / "broken.provenance.json").write_text("{ not json")
    c = _client(_settings(root, tmp_path))
    assert c.get("/api/twins/broken/provenance").status_code == 400


# ===========================================================================
# S1.6 -- GET / PUT /api/twins/{id}/layout
# ===========================================================================
_LAYOUT = {
    "schema": "axr-layout/1",
    "imprint": "mini-cast-prov",
    "pins": {"site:10.80": {"x": 40, "y": 12.5}, "segment:10.80.31.0/24": {"x": 0, "y": 0}},
}


def test_layout_get_absent_then_put_round_trip(tmp_path):
    root = _copy_bundle(tmp_path, "mini-cast-prov")
    c = _client(_settings(root, tmp_path))

    first = c.get("/api/twins/mini-cast-prov/layout").json()
    assert first["exists"] is False and first["etag"] is None and first["pins"] == {}

    put = c.put("/api/twins/mini-cast-prov/layout", json=_LAYOUT)
    assert put.status_code == 200, put.text
    j = put.json()
    assert j["exists"] is True and j["etag"]
    assert j["pins"]["site:10.80"] == {"x": 40.0, "y": 12.5}
    assert (root / "mini-cast-prov" / "layout.json").is_file()

    got = c.get("/api/twins/mini-cast-prov/layout")
    assert got.json()["etag"] == j["etag"]
    assert got.headers["ETag"] == f'"{j["etag"]}"'


def test_layout_put_stale_etag_is_412(tmp_path):
    root = _copy_bundle(tmp_path, "mini-cast-prov")
    c = _client(_settings(root, tmp_path))
    c.put("/api/twins/mini-cast-prov/layout", json=_LAYOUT)
    body = dict(_LAYOUT, etag="0" * 64)
    r = c.put("/api/twins/mini-cast-prov/layout", json=body)
    assert r.status_code == 412


@pytest.mark.parametrize(
    "pins,expect",
    [
        ({"hostname:dc01": {"x": 1, "y": 2}}, "hosts are NEVER"),
        ({"address:10.0.0.1": {"x": 1, "y": 2}}, "hosts are NEVER"),
        ({"host:": {"x": 1, "y": 2}}, "host:<name>"),
        ({"host:a b": {"x": 1, "y": 2}}, "host:<name>"),
        ({"segment:not-a-cidr": {"x": 1, "y": 2}}, "CIDR"),
        ({"cluster:a": {"x": 1, "y": 2}}, "accepted"),
        ({"site:10.80": {"x": "left", "y": 2}}, "number"),
        ({"site:10.80": {"x": 1, "y": 2, "z": 3}}, "unknown field"),
    ],
)
def test_layout_put_refuses_a_bad_pin(tmp_path, pins, expect):
    root = _copy_bundle(tmp_path, "mini-cast-prov")
    c = _client(_settings(root, tmp_path))
    r = c.put("/api/twins/mini-cast-prov/layout", json=dict(_LAYOUT, pins=pins))
    assert r.status_code == 400, r.text
    assert expect in r.json()["error"]
    assert not (root / "mini-cast-prov" / "layout.json").exists()


# --- the PLANE stamp (R2, `o2/console/src/layout/plane.ts`; LES-289) -------
# A pin is an ABSOLUTE position on a plane the document does not otherwise
# identify, so a layout change re-points every stored pin without changing a
# byte of it. The stamp is the frame of reference; this writer round-trips it
# as an opaque token and never interprets it (the SPA owns what it MEANS).
_STAMP = "l0-1-a1b2c3d4"
_LAYOUT_STAMPED = dict(_LAYOUT, plane=_STAMP)


def test_layout_plane_stamp_round_trips_through_the_file(tmp_path):
    root = _copy_bundle(tmp_path, "mini-cast-prov")
    c = _client(_settings(root, tmp_path))

    put = c.put("/api/twins/mini-cast-prov/layout", json=_LAYOUT_STAMPED)
    assert put.status_code == 200, put.text
    assert put.json()["plane"] == _STAMP
    # It is in the FILE, not only in the response -- the next reader of the
    # bundle is what the stamp exists for.
    text = (root / "mini-cast-prov" / "layout.json").read_text()
    assert f'"plane": "{_STAMP}"' in text
    assert c.get("/api/twins/mini-cast-prov/layout").json()["plane"] == _STAMP


def test_layout_with_no_plane_is_LEGACY_and_is_not_invented(tmp_path):
    """A document written with no stamp stays unstamped: `plane: null` on the
    wire means "this file carries none" (a pin saved before the stamp
    existed), and writing a value we made up would erase that fact."""
    root = _copy_bundle(tmp_path, "mini-cast-prov")
    c = _client(_settings(root, tmp_path))

    put = c.put("/api/twins/mini-cast-prov/layout", json=_LAYOUT)
    assert put.status_code == 200, put.text
    assert put.json()["plane"] is None
    assert '"plane"' not in (root / "mini-cast-prov" / "layout.json").read_text()
    assert c.get("/api/twins/mini-cast-prov/layout").json()["plane"] is None


def test_layout_accepts_an_EXPLICIT_null_plane_as_absent(tmp_path):
    """`{"plane": null}` is a payload the Console actually sends: a partial
    re-place keeps the document's EXISTING stamp, which for a legacy file is
    absent, and `JSON.stringify` emits the key as null rather than dropping it.
    It means the same as omitting the key -- and must never put a literal
    `null` in the file, which would be a third state nothing reads."""
    root = _copy_bundle(tmp_path, "mini-cast-prov")
    c = _client(_settings(root, tmp_path))

    r = c.put("/api/twins/mini-cast-prov/layout", json=dict(_LAYOUT, plane=None))
    assert r.status_code == 200, r.text
    assert r.json()["plane"] is None
    assert '"plane"' not in (root / "mini-cast-prov" / "layout.json").read_text()
    assert c.get("/api/twins/mini-cast-prov/layout").json()["plane"] is None


def test_layout_stamped_payload_is_re_puttable_verbatim(tmp_path):
    """The GET → PUT round trip of LES-231, with the new field on it."""
    root = _copy_bundle(tmp_path, "mini-cast-prov")
    c = _client(_settings(root, tmp_path))
    c.put("/api/twins/mini-cast-prov/layout", json=_LAYOUT_STAMPED)
    got = c.get("/api/twins/mini-cast-prov/layout").json()
    before = (root / "mini-cast-prov" / "layout.json").read_text()

    again = c.put("/api/twins/mini-cast-prov/layout", json=got)
    assert again.status_code == 200, again.text
    assert (root / "mini-cast-prov" / "layout.json").read_text() == before


@pytest.mark.parametrize(
    "plane,expect",
    [
        ("", "OMIT the key"),
        ("   ", "OMIT the key"),
        ("l0 1 deadbeef", "opaque token"),          # whitespace is not in the set
        ("l0-1-<script>", "opaque token"),          # nor is anything markup-ish
        ("x" * 65, "opaque token"),                 # bounded
        (7, "must be a string"),
        ({"epoch": 1}, "must be a string"),
    ],
)
def test_layout_refuses_a_malformed_plane_stamp(tmp_path, plane, expect):
    root = _copy_bundle(tmp_path, "mini-cast-prov")
    c = _client(_settings(root, tmp_path))
    r = c.put("/api/twins/mini-cast-prov/layout", json=dict(_LAYOUT, plane=plane))
    assert r.status_code == 400, r.text
    assert expect in r.json()["error"]
    assert not (root / "mini-cast-prov" / "layout.json").exists()


def test_layout_put_requires_the_write_token(tmp_path):
    root = _copy_bundle(tmp_path, "mini-cast-prov")
    c = _client(_settings(root, tmp_path, token="s3cret"))
    assert c.put("/api/twins/mini-cast-prov/layout", json=_LAYOUT).status_code == 401
    assert c.put(
        "/api/twins/mini-cast-prov/layout", json=_LAYOUT,
        headers={"Authorization": "Bearer s3cret"},
    ).status_code == 200


def test_cast_summary_sees_the_two_operator_files_once_written(tmp_path):
    root = _copy_bundle(tmp_path, "mini-cast-prov")
    c = _client(_settings(root, tmp_path))
    assert c.get("/api/twins/mini-cast-prov/cast").json()["has_assertions"] is False
    c.put("/api/twins/mini-cast-prov/assertions", json=_VALID_DOC)
    c.put("/api/twins/mini-cast-prov/layout", json=_LAYOUT)
    j = c.get("/api/twins/mini-cast-prov/cast").json()
    assert j["has_assertions"] is True and j["has_layout"] is True


# ===========================================================================
# S1.5 -- POST /api/twins/{id}/apply (fake backend; no real subprocess)
# ===========================================================================
class _FakeRecompile:
    """Records the request and returns a canned success. The suite NEVER runs
    the real binary; `test_subprocess_recompile_builds_the_pinned_argv` is
    what pins the actual invocation."""

    def __init__(self, *, document=None, raise_=None) -> None:
        self.calls: list = []
        self._document = document if document is not None else {
            "imprint_id": "mini-cast-prov",
            "record_counts": {"add": 2, "override": 1, "suppress": 1},
        }
        self._raise = raise_

    def recompile(self, request):
        from axrange.api.recompile import RecompileResult

        self.calls.append(request)
        if self._raise is not None:
            raise self._raise
        return RecompileResult(
            argv=["fake", "cast"], returncode=0,
            stdout=json.dumps(self._document), stderr="",
            document=self._document,
        )


def _apply_settings(root: Path, tmp_path: Path, **kw) -> ApiSettings:
    s = _settings(root, tmp_path, **kw)
    s.aximprint_bin = "/usr/bin/false-aximprint"   # never executed (fake backend)
    s.aximprint_store = str(tmp_path / "ir.duckdb")
    return s


def test_apply_saves_then_recompiles_and_reports_both_fingerprints(tmp_path):
    root = _copy_bundle(tmp_path, "mini-cast-prov")
    fake = _FakeRecompile()
    c = _client(_apply_settings(root, tmp_path), backend=fake)

    r = c.post(
        "/api/twins/mini-cast-prov/apply",
        json={"document": _VALID_DOC},
    )
    assert r.status_code == 200, r.text
    j = r.json()
    # R4: the fingerprints are CONTENT fingerprints axRange computes over the
    # bundle's own files -- never the sidecar's `code_fingerprint`, which is
    # the compiler's build hash and is identical across casts that produced
    # different bundles (measured: one value for a 687-field and a 681-field
    # cast, which made `changed` permanently false).
    from axrange.api.recompile import bundle_fingerprint

    expected = bundle_fingerprint(root / "mini-cast-prov", "mini-cast-prov")
    assert j["fingerprint_before"] == expected
    assert j["fingerprint_after"] == expected     # the fake recompiles nothing
    assert j["fingerprint_before"] != "fixture-0000000000"
    assert j["changed"] is False
    # ... and BOTH build stamps are published, under names that say whose they
    # are (GAP-088). The sidecar's is the PIT row's; the invocation's is the
    # build that ran the cast. The fixture carries two DIFFERENT values on
    # purpose -- it used to carry one, which is why nothing in this suite
    # could see that the apply response was publishing the wrong one.
    assert j["pit_code_fingerprint_before"] == "fixture-0000000000"
    assert j["pit_code_fingerprint_after"] == "fixture-0000000000"
    assert j["cast_code_fingerprint_before"] == "fixture-castbuild-01"
    assert j["cast_code_fingerprint_after"] == "fixture-castbuild-01"
    assert j["compiler_changed"] is False and j["compiler_note"] is None
    # The renamed pair is GONE, not aliased: a consumer reading the old name
    # for "the compiler's build hash" was reading the PIT's, and a silent
    # wrong answer is worse than a missing key.
    assert "code_fingerprint_before" not in j and "code_fingerprint_after" not in j
    assert j["record_counts"] == {"add": 2, "override": 1, "suppress": 1}
    assert j["stdout_json"]["imprint_id"] == "mini-cast-prov"
    assert j["history_written"] is True
    # apply IS save: the file is on disk with an etag the SPA can reuse.
    assert j["assertions"]["exists"] is True and j["assertions"]["etag"]
    assert (root / "mini-cast-prov" / "mini-cast-prov.assertions.yaml").is_file()

    # R3: the backend got the bundle's own recorded invocation, the saved
    # file's path, a SCRATCH `--out` (never the bundle), and the catalog root
    # the install stage puts the result back into.
    (req,) = fake.calls
    assert req.pit_id == "pit-miniprov-0000000001"
    assert req.invocation.relocate == "10.200.0.0/16"
    assert req.invocation.max_count == 12
    assert req.invocation.chatter is False
    assert req.assertions_path.name == "mini-cast-prov.assertions.yaml"
    assert req.bundle_dir == root / "mini-cast-prov"
    assert req.catalog_root == root
    assert req.out_dir != root / "mini-cast-prov"
    # ... and the scratch directory does not outlive the request (LES-199).
    assert not req.out_dir.exists()


def test_apply_refuses_an_invalid_document_before_running_anything(tmp_path):
    root = _copy_bundle(tmp_path, "mini-cast-prov")
    fake = _FakeRecompile()
    c = _client(_apply_settings(root, tmp_path), backend=fake)
    bad = _doc()
    bad["add"].append({"kind": "network", "key": "10.9.0.0/24", "note": "n"})
    r = c.post("/api/twins/mini-cast-prov/apply", json={"document": bad})
    assert r.status_code == 400
    assert fake.calls == []      # never recompiled
    assert not (root / "mini-cast-prov" / "mini-cast-prov.assertions.yaml").exists()


def test_apply_with_a_stale_etag_is_412_and_does_not_recompile(tmp_path):
    root = _copy_bundle(tmp_path, "mini-cast-prov")
    fake = _FakeRecompile()
    c = _client(_apply_settings(root, tmp_path), backend=fake)
    c.put("/api/twins/mini-cast-prov/assertions", json=_VALID_DOC)
    r = c.post(
        "/api/twins/mini-cast-prov/apply",
        json={"document": _doc(source="operator: other"), "etag": "0" * 64},
    )
    assert r.status_code == 412
    assert fake.calls == []


def test_apply_exit_2_is_422_carrying_the_compilers_own_stderr_line(tmp_path):
    from axrange.api.recompile import RecompileResult, RecompileShapeError

    root = _copy_bundle(tmp_path, "mini-cast-prov")
    boom = RecompileShapeError(
        "assertions: add[1]: unknown field 'colour'",
        result=RecompileResult(
            argv=["fake"], returncode=2, stderr="assertions: add[1]: unknown field 'colour'",
        ),
    )
    c = _client(_apply_settings(root, tmp_path), backend=_FakeRecompile(raise_=boom))
    r = c.post("/api/twins/mini-cast-prov/apply", json={"document": _VALID_DOC})
    assert r.status_code == 422
    assert "unknown field 'colour'" in r.json()["error"]
    # A failed compile still leaves the operator's saved work on disk (`14`
    # §4: save and apply are two actions, and apply must not lose the save).
    assert (root / "mini-cast-prov" / "mini-cast-prov.assertions.yaml").is_file()


def test_apply_other_nonzero_exit_is_502_with_the_stderr_tail(tmp_path):
    from axrange.api.recompile import RecompileFailedError, RecompileResult

    root = _copy_bundle(tmp_path, "mini-cast-prov")
    boom = RecompileFailedError(
        "recompile failed (exit 1): duckdb: no such store",
        result=RecompileResult(argv=["fake"], returncode=1, stderr="duckdb: no such store"),
    )
    c = _client(_apply_settings(root, tmp_path), backend=_FakeRecompile(raise_=boom))
    r = c.post("/api/twins/mini-cast-prov/apply", json={"document": _VALID_DOC})
    assert r.status_code == 502
    assert "duckdb: no such store" in r.json()["error"]


@pytest.mark.parametrize(
    "unset,expect",
    [("aximprint_bin", "AXRANGE_AXIMPRINT_BIN"), ("aximprint_store", "AXRANGE_AXIMPRINT_STORE")],
)
def test_apply_without_its_settings_is_503_naming_the_key(tmp_path, unset, expect):
    root = _copy_bundle(tmp_path, "mini-cast-prov")
    settings = _apply_settings(root, tmp_path)
    setattr(settings, unset, "")
    c = _client(settings, backend=_FakeRecompile())
    r = c.post("/api/twins/mini-cast-prov/apply", json={"document": _VALID_DOC})
    assert r.status_code == 503
    assert expect in r.json()["error"]


def test_apply_requires_the_write_token(tmp_path):
    root = _copy_bundle(tmp_path, "mini-cast-prov")
    c = _client(_apply_settings(root, tmp_path, token="s3cret"), backend=_FakeRecompile())
    assert c.post(
        "/api/twins/mini-cast-prov/apply", json={"document": _VALID_DOC},
    ).status_code == 401
    assert c.post(
        "/api/twins/mini-cast-prov/apply", json={"document": _VALID_DOC},
        headers={"Authorization": "Bearer s3cret"},
    ).status_code == 200


def test_apply_without_a_document_is_400(tmp_path):
    root = _copy_bundle(tmp_path, "mini-cast-prov")
    c = _client(_apply_settings(root, tmp_path), backend=_FakeRecompile())
    r = c.post("/api/twins/mini-cast-prov/apply", json={})
    assert r.status_code == 400
    assert "document" in r.json()["error"]


def test_apply_on_a_SIDECARLESS_bundle_still_has_a_content_fingerprint(tmp_path):
    """R4's other consequence, and the reason it is worth having: a bundle
    with NO provenance sidecar has no `code_fingerprint` at all, so under the
    old keying it could never be snapshotted and `diff?since=` could never
    answer for it. A CONTENT fingerprint exists for every readable bundle, so
    the sidecar-less half of the catalog becomes diffable too."""
    root = _copy_bundle(tmp_path, "mini-cast")
    doc = _doc(imprint="mini-cast")
    c = _client(_apply_settings(root, tmp_path), backend=_FakeRecompile())
    r = c.post("/api/twins/mini-cast/apply", json={"document": doc})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["fingerprint_before"] and j["history_written"] is True
    # The PIT's build stamp is honestly absent (no sidecar to read it from),
    # and says so. This fixture's `cast_invocation` records no build hash
    # either, so the compiler verdict is the THIRD outcome -- `null`, never
    # `false` (LES-273/LES-333: "I could not tell" is not "no").
    assert j["pit_code_fingerprint_before"] is None
    assert j["pit_code_fingerprint_after"] is None
    assert j["cast_code_fingerprint_before"] is None
    assert j["compiler_changed"] is None
    assert "could not be determined" in j["compiler_note"]
    assert "records no `code_fingerprint`" in j["compiler_note"]


def test_apply_refuses_a_bundle_that_cannot_say_how_it_was_cast(tmp_path):
    """R3: a bundle with no `cast_invocation` block makes apply REFUSE by
    name. Guessing the cast's flags -- `--relocate` above all -- would
    silently compile a different twin into the same directory, and the
    operator would find out at deploy time."""
    root = _copy_bundle(tmp_path, "mini-cast-prov")
    export = root / "mini-cast-prov" / "mini-cast-prov.topology.json"
    raw = json.loads(export.read_text())
    del raw["cast_invocation"]
    export.write_text(json.dumps(raw, indent=2, sort_keys=True) + "\n")

    fake = _FakeRecompile()
    c = _client(_apply_settings(root, tmp_path), backend=fake)
    r = c.post("/api/twins/mini-cast-prov/apply", json={"document": _VALID_DOC})
    assert r.status_code == 422, r.text
    assert "cast_invocation" in r.json()["error"]
    assert "Recast" in r.json()["error"]
    assert fake.calls == []
    # S9: the save DID land, so the refusal hands back the new etag rather
    # than leaving the SPA holding a stale one.
    assert r.json()["detail"]["etag"]
    assert (root / "mini-cast-prov" / "mini-cast-prov.assertions.yaml").is_file()


# ---------------------------------------------------------------------------
# GAP-087 / GAP-088 -- a compiler upgrade that rides along on an apply, and
# the two build stamps that used to be published under one name
# ---------------------------------------------------------------------------
class _RecompileAsANewerCompiler:
    """What an UPGRADED aximprint does to a bundle: it recompiles, and the
    export it installs records a DIFFERENT `cast_invocation.code_fingerprint`
    than the one that cast it.

    Sits at the seam the real `SubprocessRecompile` occupies -- the external
    binary, not the logic under test (LES-242). `compare_compiler_builds` and
    the whole service wiring really run; only the compiler is a double."""

    def __init__(self, build: str, *, install_returncode: int = 0) -> None:
        self.build = build
        self.install_returncode = install_returncode

    def recompile(self, request):
        from axrange.api.recompile import RecompileResult

        export = request.bundle_dir / f"{request.imprint_id}.topology.json"
        raw = json.loads(export.read_text())
        # A recompile by the SAME build leaves the bundle byte-identical --
        # `14` §3's determinism rule, which this double has to honour or it
        # cannot stand in for the no-op case at all.
        if raw["cast_invocation"].get("code_fingerprint") != self.build:
            raw["cast_invocation"]["code_fingerprint"] = self.build
            export.write_text(json.dumps(raw, indent=2, sort_keys=True) + "\n")
        doc = {
            "imprint_id": request.imprint_id,
            "assertions": {"add": 0, "override": 0, "suppress": 1},
        }
        return RecompileResult(
            argv=["fake", "cast"], returncode=0, stdout=json.dumps(doc),
            install_argv=["fake", "install-cast"],
            install_returncode=self.install_returncode, document=doc,
        )


def test_apply_reports_a_compiler_change_as_its_own_outcome(tmp_path):
    """GAP-087, and GAP-088's symptom reproduced in the same run.

    A recompile re-runs the producer from the bundle's own `cast_invocation`,
    which pins the cast's FLAGS and not the compiler BUILD -- so an upgrade to
    the sibling repo between cast and apply lands on the operator's next edit.
    On the S6 acceptance proof that was 22 hosts added and 23 removed for one
    suppression, reported as "1 element changed".

    RULED: report it, do not refuse -- so the apply is still a 200 and the new
    bundle is still installed. What changes is that the response now SAYS the
    population may have moved for a reason that is not the edit."""
    root = _copy_bundle(tmp_path, "mini-cast-prov")
    c = _client(
        _apply_settings(root, tmp_path),
        backend=_RecompileAsANewerCompiler("fixture-castbuild-02"),
    )
    r = c.post("/api/twins/mini-cast-prov/apply", json={"document": _VALID_DOC})
    assert r.status_code == 200, r.text
    j = r.json()

    assert j["compiler_changed"] is True
    assert j["cast_code_fingerprint_before"] == "fixture-castbuild-01"
    assert j["cast_code_fingerprint_after"] == "fixture-castbuild-02"
    note = j["compiler_note"]
    assert "fixture-castbuild-01 -> fixture-castbuild-02" in note
    assert "may be the compiler's" in note
    # Published ONCE, on `compiler_note` -- not repeated in `warnings`, where
    # a client printing both would say it twice (fix round 1, finding 3).
    assert note not in j["warnings"]
    assert not any("compiler changed" in w for w in j["warnings"])

    # GAP-088: the pair an operator WOULD have reached for is identical across
    # exactly this apply -- it is the PIT row's stamp, and a re-cast does not
    # re-materialize the PIT. This assertion is the gap, stated as a test.
    assert j["pit_code_fingerprint_before"] == j["pit_code_fingerprint_after"]
    assert j["pit_code_fingerprint_before"] == "fixture-0000000000"


def test_a_recompile_that_lands_an_UNREADABLE_bundle_is_still_a_400(tmp_path):
    """The compiler-change read needs the post-apply BUNDLE, not only its
    summary, so the two now come off one load -- and both must stay inside
    the `ImprintDataError` guard.

    `cast_summary_from_bundle` raises that error itself on a malformed
    `params.DEPLOY_MIN_OCCUPANCY`, so hoisting it out of the `except` to get
    at the bundle object turns a legible 400 into an unhandled 500. Caught in
    review of this build's own first cut; pinned here because the structure
    that invites it is now permanent."""
    root = _copy_bundle(tmp_path, "mini-cast-prov")

    class _LandsGarbage:
        def recompile(self, request):
            from axrange.api.recompile import RecompileResult

            config = request.bundle_dir / f"{request.imprint_id}.yaml"
            config.write_text(
                config.read_text().replace(
                    "DEPLOY_MIN_OCCUPANCY: 0.8", 'DEPLOY_MIN_OCCUPANCY: "not-a-number"',
                ),
                encoding="utf-8",
            )
            return RecompileResult(argv=["fake", "cast"], returncode=0)

    c = _client(_apply_settings(root, tmp_path), backend=_LandsGarbage())
    r = c.post("/api/twins/mini-cast-prov/apply", json={"document": _VALID_DOC})
    assert r.status_code == 400, r.text
    assert "DEPLOY_MIN_OCCUPANCY" in r.json()["error"]
    # S9: and the etag of the file that IS on disk still travels with it.
    assert r.json()["detail"]["etag"]


def test_cast_publishes_the_CONTENT_fingerprint_an_apply_reports(tmp_path):
    """Fix round 1, finding 1: the Console stamps its drafts with the bundle
    it drew, and the only value that moves when a bundle is recompiled is
    R4's CONTENT fingerprint -- not `code_fingerprint`, which is the PIT
    row's stamp and does not move on a re-cast. `/cast` publishes it as
    `bundle_fingerprint`, equal to the apply's own `fingerprint_before` and
    then `fingerprint_after` (additive: `code_fingerprint` stays)."""
    root = _copy_bundle(tmp_path, "mini-cast-prov")
    c = _client(
        _apply_settings(root, tmp_path),
        backend=_RecompileAsANewerCompiler("fixture-castbuild-02"),
    )
    cast0 = c.get("/api/twins/mini-cast-prov/cast").json()
    assert cast0["bundle_fingerprint"]
    assert cast0["code_fingerprint"] == "fixture-0000000000"
    j = c.post(
        "/api/twins/mini-cast-prov/apply", json={"document": _VALID_DOC},
    ).json()
    assert j["fingerprint_before"] == cast0["bundle_fingerprint"]
    assert j["changed"] is True
    cast1 = c.get("/api/twins/mini-cast-prov/cast").json()
    assert cast1["bundle_fingerprint"] == j["fingerprint_after"]
    assert cast1["bundle_fingerprint"] != cast0["bundle_fingerprint"]
    # ...while the PIT stamp the drafts USED to key on never moved.
    assert cast1["code_fingerprint"] == cast0["code_fingerprint"]


def test_the_post_apply_bundle_is_read_INSIDE_the_apply_lock(tmp_path, monkeypatch):
    """Fix round 1, finding 4: `fingerprint_after` is computed under the
    per-imprint apply lock, so the post-apply bundle (the PIT stamp, the
    compiler build, the summary) must be too -- read after the release, a
    concurrent apply could finish in the gap and leak ITS stamps into this
    response. Pinned structurally: every bundle load an apply makes happens
    while the lock is held."""
    from axrange.api import imprints
    from axrange.api import recompile as rc

    held: list[bool] = []
    real = imprints.load_reference

    def spy(imprint_id, *a, **kw):
        held.append(rc.apply_in_progress(imprint_id))
        return real(imprint_id, *a, **kw)

    monkeypatch.setattr(imprints, "load_reference", spy)
    root = _copy_bundle(tmp_path, "mini-cast-prov")
    c = _client(
        _apply_settings(root, tmp_path),
        backend=_RecompileAsANewerCompiler("fixture-castbuild-02"),
    )
    r = c.post("/api/twins/mini-cast-prov/apply", json={"document": _VALID_DOC})
    assert r.status_code == 200, r.text
    assert len(held) == 2, held      # the pre- and the post-apply load
    assert all(held), held


def test_bools_are_not_counted_as_assertion_records(tmp_path):
    """Nit: `True` is an `int` to Python. On the explicit `record_counts`
    path a boolean member must not inflate N."""
    from axrange.api import recompile as rc

    req = _pinned_request(bundle_dir=tmp_path, catalog_root=tmp_path)
    result = rc.RecompileResult(
        argv=[], returncode=0, install_returncode=0,
        document={"record_counts": {"add": 2, "complete": True}},
    )
    rc.check_output_landed(req, result, before="fp", after="fp")
    (warning,) = result.warnings
    assert "your 2 assertion record(s)" in warning, warning


def test_a_compiler_that_did_not_change_says_so_without_a_note(tmp_path):
    """The ordinary outcome stays quiet: a verdict of `false` and no note, so
    the changed/unknown cases are the only ones that spend the operator's
    attention."""
    root = _copy_bundle(tmp_path, "mini-cast-prov")
    c = _client(
        _apply_settings(root, tmp_path),
        # The SAME build the fixture was cast by.
        backend=_RecompileAsANewerCompiler("fixture-castbuild-01"),
    )
    j = c.post(
        "/api/twins/mini-cast-prov/apply", json={"document": _VALID_DOC},
    ).json()
    assert j["compiler_changed"] is False
    assert j["compiler_note"] is None
    assert not any("compiler changed" in w for w in j["warnings"])


@pytest.mark.parametrize(
    "before,after,changed,expect_note",
    [
        ({"code_fingerprint": "a"}, {"code_fingerprint": "b"}, True, "a -> b"),
        ({"code_fingerprint": "a"}, {"code_fingerprint": "a"}, False, None),
        # The three ways a side can be unreadable -- each its own reason
        # (LES-235), and NONE of them `False` (LES-273/LES-333).
        (None, {"code_fingerprint": "b"}, None, "carries no `cast_invocation`"),
        ({"code_fingerprint": "a"}, {}, None, "records no `code_fingerprint`"),
        ({"code_fingerprint": "a"}, {"code_fingerprint": "  "}, None, "is empty"),
        # LES-235: a non-string is MALFORMED, its own reason -- never
        # str()-coerced into a "hash" that then compares for no reason.
        ({"code_fingerprint": "a"}, {"code_fingerprint": {"x": 1}}, None, "is malformed (a dict"),
        ({"code_fingerprint": True}, {"code_fingerprint": "a"}, None, "is malformed (a bool"),
        ({"code_fingerprint": "a"}, {"code_fingerprint": 5}, None, "is malformed (a int"),
    ],
)
def test_compare_compiler_builds_has_three_outcomes(before, after, changed, expect_note):
    """"I could not tell" is not "no". A build stamp that could not be READ
    must not render as a build that did not MOVE -- the same don't-know-vs-no
    rule the apply's diff branch carries (LES-273)."""
    from axrange.api.recompile import compare_compiler_builds

    got = compare_compiler_builds(before, after)
    assert got.changed is changed
    if expect_note is None:
        assert got.note is None
    else:
        assert expect_note in got.note
    if changed is None:
        # The unknown note must not be mistakable for the quiet outcome.
        assert "could not be determined" in got.note


def test_both_sides_of_the_compiler_comparison_are_read_by_ONE_rule():
    """A stamp coerced on one side and not the other would fabricate a
    "changed" out of `5` vs `"5"`. Both sides go through the same reader, so
    the only way to differ is to genuinely differ -- and a non-string stamp
    is not coerced at all (LES-235): it is malformed, so the verdict is the
    honest `None`, never a fabricated `True` or `False`."""
    from axrange.api.recompile import compare_compiler_builds

    got = compare_compiler_builds(
        {"code_fingerprint": 5}, {"code_fingerprint": "5"},
    )
    assert got.changed is None
    assert "malformed" in got.note


def test_an_undetermined_compiler_note_drops_the_population_clause_when_nothing_moved():
    """Fix round 1, finding 6: when the bundle's content fingerprint did not
    move, there is no population difference to attribute -- the unknown note
    keeps its reason but not the "may still be the compiler's" clause."""
    from axrange.api.recompile import compare_compiler_builds

    moved = compare_compiler_builds(None, {"code_fingerprint": "b"})
    assert "may still be the compiler's" in moved.note
    still = compare_compiler_builds(
        None, {"code_fingerprint": "b"}, bundle_changed=False,
    )
    assert still.changed is None
    assert "could not be determined" in still.note
    assert "carries no `cast_invocation`" in still.note
    assert "population difference" not in still.note


# ---- the real invocation (built, never run) -------------------------------
def _pinned_request(**kw):
    from axrange.api.recompile import CastInvocation, RecompileRequest

    inv = kw.pop("invocation", None) or CastInvocation(
        pit_id="pit-03de65ec67-1787823960", imprint_id="corpapcn-golden",
    )
    base = dict(
        imprint_id="corpapcn-golden",
        bundle_dir=Path("/bundles/corpapcn-golden"),
        catalog_root=Path("/bundles"),
        assertions_path=Path("/bundles/corpapcn-golden/corpapcn-golden.assertions.yaml"),
        store="/store/ir.duckdb",
        out_dir=Path("/scratch/axr-cast-corpapcn-golden"),
        invocation=inv,
    )
    base.update(kw)
    return RecompileRequest(**base)


def test_subprocess_recompile_builds_the_pinned_cast_argv(tmp_path):
    """Seam 4 (`docs/WORK_ORDER_console_canvas.md` §9), STAGE 1: the exact
    argument list aximprint's `cast` is contracted to accept.

    `--out` is a SCRATCH directory, not the bundle (R3: a cast writes generic
    stems, and the `<id>.*` names come from the install stage)."""
    from axrange.api.recompile import SubprocessRecompile

    argv = SubprocessRecompile("/opt/aximprint/bin/aximprint").build_argv(
        _pinned_request()
    )
    assert argv == [
        "/opt/aximprint/bin/aximprint", "cast",
        "--store", "/store/ir.duckdb",
        "--pit", "pit-03de65ec67-1787823960",
        "--out", "/scratch/axr-cast-corpapcn-golden",
        "--imprint-id", "corpapcn-golden",
        "--assertions", "/bundles/corpapcn-golden/corpapcn-golden.assertions.yaml",
    ]


def test_the_cast_argv_carries_every_recorded_flag_in_order(tmp_path):
    """R3: `--relocate` / `--max-count` / `--no-chatter` come from the
    bundle's own `cast_invocation`, in a PINNED position -- each of the three
    changes the twin's shape, so a re-cast without one compiles a different
    twin into the same directory."""
    from axrange.api.recompile import CastInvocation, SubprocessRecompile

    inv = CastInvocation(
        pit_id="pit-03de65ec67-1787823960", imprint_id="corpapcn-golden",
        relocate="10.200.0.0/16", max_count=12, chatter=False,
    )
    argv = SubprocessRecompile("/opt/aximprint/bin/aximprint").build_argv(
        _pinned_request(invocation=inv)
    )
    assert argv == [
        "/opt/aximprint/bin/aximprint", "cast",
        "--store", "/store/ir.duckdb",
        "--pit", "pit-03de65ec67-1787823960",
        "--out", "/scratch/axr-cast-corpapcn-golden",
        "--imprint-id", "corpapcn-golden",
        "--relocate", "10.200.0.0/16",
        "--max-count", "12",
        "--no-chatter",
        "--assertions", "/bundles/corpapcn-golden/corpapcn-golden.assertions.yaml",
    ]


def test_subprocess_recompile_builds_the_pinned_install_argv(tmp_path):
    """Seam 4, STAGE 2: `install-cast <scratch> <catalog-root> <id>
    [--sensors NAME ...]`.

    The sensor names are the one LAB fact the compiler cannot observe; they
    are recovered from the bundle's existing tap plan so an install-time fact
    is not silently lost to an edit."""
    from axrange.api.recompile import SubprocessRecompile

    impl = SubprocessRecompile("/opt/aximprint/bin/aximprint")
    assert impl.build_install_argv(_pinned_request()) == [
        "/opt/aximprint/bin/aximprint", "install-cast",
        "/scratch/axr-cast-corpapcn-golden",
        "/bundles",
        "corpapcn-golden",
    ]
    assert impl.build_install_argv(
        _pinned_request(sensors=("XPress-SW-A", "o2-suricata"))
    ) == [
        "/opt/aximprint/bin/aximprint", "install-cast",
        "/scratch/axr-cast-corpapcn-golden",
        "/bundles",
        "corpapcn-golden",
        "--sensors", "XPress-SW-A", "o2-suricata",
    ]


@requires_real_golden
def test_sensors_are_recovered_from_the_bundles_own_tap_plan(tmp_path):
    """LES-185: against the bundle that ships. `corpapcn-golden/tap-cast.yaml`
    binds every segment to `XPress-SW-A`, and that name must survive an
    apply."""
    from axrange.api.recompile import sensors_from_tap_plan

    assert sensors_from_tap_plan(REAL_GOLDEN) == (("XPress-SW-A",), [])


def test_a_missing_tap_plan_is_silent_and_every_other_empty_result_is_not(tmp_path):
    """N2: `()` used to mean five indistinguishable things -- no plan at all,
    `sensors: []`, a singular `sensor:` key, malformed YAML, and a different
    root key -- and only the FIRST is the ordinary state. The rest are an
    operator's install-time lab fact quietly not surviving their own edit,
    which produces a reinstalled tap plan axRange then refuses to apply."""
    from axrange.api.recompile import sensors_from_tap_plan

    plan = tmp_path / "tap-cast.yaml"

    # (a) no plan at all: silent, by design.
    assert sensors_from_tap_plan(tmp_path) == ((), [])

    # (b) malformed YAML.
    plan.write_text("{ not: valid: yaml\n")
    sensors, warnings = sensors_from_tap_plan(tmp_path)
    assert sensors == () and any("could not be read" in w for w in warnings)

    # (c) a different root key.
    plan.write_text("tap_plan:\n  bindings: []\n")
    sensors, warnings = sensors_from_tap_plan(tmp_path)
    assert sensors == () and any("no top-level `tap-plan:`" in w for w in warnings)

    # (d) an empty / absent `sensors:`.
    plan.write_text(
        "tap-plan:\n  target: x\n  bindings:\n"
        "  - selector: {scope: segment, target: a}\n    sensors: []\n"
    )
    sensors, warnings = sensors_from_tap_plan(tmp_path)
    assert sensors == () and any("names no sensors" in w for w in warnings)

    # (e) the SINGULAR key -- a plausible typo that silently binds nothing.
    plan.write_text(
        "tap-plan:\n  target: x\n  bindings:\n"
        "  - selector: {scope: segment, target: a}\n    sensor: XPress-SW-A\n"
    )
    sensors, warnings = sensors_from_tap_plan(tmp_path)
    assert sensors == () and any("spells its target(s) `sensor:`" in w for w in warnings)

    # ... and the healthy case warns about nothing.
    plan.write_text(
        "tap-plan:\n  target: x\n  bindings:\n"
        "  - selector: {scope: segment, target: a}\n    sensors: [B, A, B]\n"
    )
    assert sensors_from_tap_plan(tmp_path) == (("A", "B"), [])


def test_an_unrecoverable_sensor_name_reaches_the_apply_response(tmp_path):
    """The warning has to travel: a `sensors:` an apply could not carry
    forward is the operator's problem to see, not a log line in a subprocess
    wrapper."""
    root = _copy_bundle(tmp_path, "mini-cast-prov")
    (root / "mini-cast-prov" / "tap-cast.yaml").write_text(
        "tap-plan:\n  target: mini-cast-prov\n  bindings:\n"
        "  - selector: {scope: segment, target: a}\n    sensors: []\n"
    )
    c = _client(_apply_settings(root, tmp_path), backend=_FakeRecompile())
    j = c.post(
        "/api/twins/mini-cast-prov/apply", json={"document": _VALID_DOC},
    ).json()
    assert any("names no sensors" in w for w in j["warnings"]), j["warnings"]


@pytest.mark.parametrize("kind", ["missing", "directory", "not-executable"])
def test_an_unusable_binary_is_unavailable_not_a_500(tmp_path, kind):
    """M5: `FileNotFoundError` is only ONE of the ways an argv[0] can be
    unusable. A DIRECTORY raises `IsADirectoryError` and a file without `+x`
    raises `PermissionError`; both used to escape the handler as a 500.

    A binary that cannot run means NOTHING RAN -- a different thing from a
    compile that failed, and a different HTTP status (503 vs 502)."""
    import os as _os

    from axrange.api.recompile import (
        RecompileUnavailableError,
        SETTING_BIN,
        SubprocessRecompile,
    )

    if kind == "missing":
        binary = tmp_path / "no-such-binary"
    elif kind == "directory":
        binary = tmp_path / "a-directory"
        binary.mkdir()
    else:
        binary = tmp_path / "not-executable"
        binary.write_text("#!/bin/sh\n")
        _os.chmod(binary, 0o644)

    with pytest.raises(RecompileUnavailableError) as e:
        SubprocessRecompile(str(binary)).recompile(
            _pinned_request(bundle_dir=tmp_path, catalog_root=tmp_path)
        )
    assert SETTING_BIN in str(e.value)


def test_apply_lock_refuses_a_second_concurrent_apply(tmp_path):
    from axrange.api.recompile import ApplyInProgressError, imprint_apply_lock

    with imprint_apply_lock("mini-cast-prov"):
        with pytest.raises(ApplyInProgressError):
            with imprint_apply_lock("mini-cast-prov"):
                pass
        # A DIFFERENT imprint is unaffected -- the lock is per-imprint.
        with imprint_apply_lock("another"):
            pass
    # Released on exit.
    with imprint_apply_lock("mini-cast-prov"):
        pass


# ===========================================================================
# S1.7 -- GET /api/twins/{id}/diff?since=<fingerprint>
# ===========================================================================
def test_diff_unknown_fingerprint_is_404_stating_the_limit(tmp_path):
    c = _client(_settings(CAST_FIXTURES, tmp_path))
    r = c.get("/api/twins/mini-cast-prov/diff", params={"since": "deadbeef"})
    assert r.status_code == 404
    assert "apply" in r.json()["error"]


def test_diff_reports_added_removed_and_changed_against_an_apply_snapshot(tmp_path):
    """Drive the real snapshot path: apply writes `<bundle>/.history/
    <fingerprint>.json`, then the diff compares the CURRENT bundle against
    it. The snapshot is doctored afterwards so the three sections all have
    something to report without needing a real recompile."""
    from axrange.api import recompile as rc

    root = _copy_bundle(tmp_path, "mini-cast-prov")
    bundle = root / "mini-cast-prov"
    c = _client(_apply_settings(root, tmp_path), backend=_FakeRecompile())
    applied = c.post(
        "/api/twins/mini-cast-prov/apply", json={"document": _VALID_DOC},
    ).json()
    fp = applied["fingerprint_before"]
    assert applied["history_written"] is True

    snap_path = rc.history_dir(bundle) / f"{fp}.json"
    assert snap_path.is_file()
    snap = json.loads(snap_path.read_text())
    observed = snap["observed"]
    a_node = sorted(observed["nodes"])[0]
    observed["nodes"]["address:10.99.99.99"] = {   # present then, gone now -> removed
        "role": "ghost", "segment": None, "locality": "local", "windows_seen": 1,
    }
    observed["nodes"][a_node]["role"] = "something-else"     # -> changed
    removed_segment = sorted(observed["segments"])[0]
    del observed["segments"][removed_segment]                 # -> added (now present)
    snap_path.write_text(json.dumps(snap))

    r = c.get("/api/twins/mini-cast-prov/diff", params={"since": fp})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["since"] == fp
    assert j["source"] == "topology export"
    assert j["snapshot_schema"] == rc.SNAPSHOT_SCHEMA
    assert "address:10.99.99.99" in j["observed"]["nodes"]["removed"]
    changed = {e["key"]: e for e in j["observed"]["nodes"]["changed"]}
    assert a_node in changed
    assert changed[a_node]["fields"]["role"]["before"] == "something-else"
    assert removed_segment in j["observed"]["segments"]["added"]
    # The TWIN side is present and untouched -- present-and-empty, which is
    # a different answer from `null` ("I could not compare it").
    assert j["twin"] is not None
    assert j["twin"]["hosts"] == {"added": [], "removed": [], "changed": []}


def test_diff_of_an_unchanged_bundle_is_empty(tmp_path):
    from axrange.api import recompile as rc

    root = _copy_bundle(tmp_path, "mini-cast-prov")
    c = _client(_apply_settings(root, tmp_path), backend=_FakeRecompile())
    fp = c.post(
        "/api/twins/mini-cast-prov/apply", json={"document": _VALID_DOC},
    ).json()["fingerprint_before"]
    assert rc.list_history(root / "mini-cast-prov") == [fp]

    j = c.get("/api/twins/mini-cast-prov/diff", params={"since": fp}).json()
    empty = {"added": [], "removed": [], "changed": []}
    assert j["observed"] == {"nodes": empty, "segments": empty}
    assert j["twin"] == {"hosts": empty, "segments": empty, "conduits": empty}
    assert j["twin_config_changed"] is False


def test_diff_refuses_a_path_shaped_fingerprint(tmp_path):
    c = _client(_settings(CAST_FIXTURES, tmp_path))
    r = c.get("/api/twins/mini-cast-prov/diff", params={"since": "../../etc/passwd"})
    assert r.status_code == 400


def test_history_is_pruned_to_the_last_n(tmp_path):
    from axrange.api import recompile as rc

    d = tmp_path / "bundle"
    d.mkdir()
    for i in range(8):
        rc.write_history_snapshot(d, f"fp{i}", {"fingerprint": f"fp{i}"}, keep=5)
    assert len(rc.list_history(d)) == 5


# ===========================================================================
# S1.8 -- catalog plumbing (tolerant listing + `--imprints-dir`)
# ===========================================================================
def test_list_imprints_over_http_tolerates_a_sidecarless_bundle(tmp_path):
    c = _client(_settings(CAST_FIXTURES, tmp_path))
    by_id = {e["id"]: e for e in c.get("/api/twins").json()["imprints"]}
    assert {"mini-cast", "mini-cast-prov"} <= set(by_id)
    bare = by_id["mini-cast"]
    assert bare["has_provenance"] is False
    assert bare["confidence_source"] == "absent"
    # NON-NULL for the shipped plugin's `mean_confidence.toFixed(2)`.
    assert bare["mean_confidence"] == 0.0
    assert isinstance(bare["mean_confidence"], float)
    prov = by_id["mini-cast-prov"]
    assert prov["has_provenance"] is True
    # S1: a CANONICAL sidecar carries no `summary.mean_confidence`, but it
    # does carry per-field confidences -- publishing 0.0 said the
    # best-evidenced bundle in the catalog knows nothing. The mean is over
    # the fields that CARRY one, with the coverage in `confidence_source`.
    assert prov["mean_confidence"] > 0.0
    assert prov["confidence_source"].startswith("canonical sidecar (")
    assert "fields)" in prov["confidence_source"]


@requires_real_sidecarless
def test_list_imprints_over_http_sees_the_real_cast_bundles(tmp_path):
    """S1.8's headline: `AXRANGE_IMPRINTS_DIR=twin-configs-cast` used to list
    NOTHING (work order §2's measured fact)."""
    c = _client(_settings(REAL_CAST_ROOT, tmp_path))
    by_id = {e["id"]: e for e in c.get("/api/twins").json()["imprints"]}
    assert "corpapcn-twin" in by_id
    assert by_id["corpapcn-twin"]["has_provenance"] is False
    assert by_id["corpapcn-twin"]["hosts"] == 137
    assert by_id["corpapcn-twin"]["segments"] == 27


@requires_real_golden
def test_list_imprints_over_http_sees_a_canonical_sidecar_bundle(tmp_path):
    """LES-185, and S1's measured numbers: the golden sidecar carries 687
    fields of which 151 have a confidence (mean 0.708). The listing publishes
    that mean with its coverage, not a 0.0 that reads as "nothing known"."""
    c = _client(_settings(REAL_CAST_ROOT, tmp_path))
    by_id = {e["id"]: e for e in c.get("/api/twins").json()["imprints"]}
    golden = by_id["corpapcn-golden"]
    assert golden["has_provenance"] is True
    assert golden["confidence_source"] == "canonical sidecar (151/687 fields)"
    assert round(golden["mean_confidence"], 3) == 0.708


def test_list_imprints_publishes_what_it_SKIPPED_and_why(tmp_path):
    """S5: a tolerant listing that silently drops a bundle leaves an operator
    staring at an incomplete Console with no way to find out which one went
    missing. Every skip branch reports its reason."""
    root = _copy_bundle(tmp_path, "mini-cast-prov")
    # (a) a directory that is not a bundle at all
    (root / "not-a-bundle").mkdir()
    # (b) a bundle whose sidecar is corrupt -- content, not a degraded state
    shutil.copytree(CAST_FIXTURES / "mini-cast-prov", root / "broken-prov")
    (root / "broken-prov" / "mini-cast-prov.yaml").rename(
        root / "broken-prov" / "broken-prov.yaml"
    )
    (root / "broken-prov" / "mini-cast-prov.provenance.json").rename(
        root / "broken-prov" / "broken-prov.provenance.json"
    )
    (root / "broken-prov" / "broken-prov.provenance.json").write_text("{ truncated")

    c = _client(_settings(root, tmp_path))
    j = c.get("/api/twins").json()
    assert {e["id"] for e in j["imprints"]} == {"mini-cast-prov"}
    by_id = {e["id"]: e["reason"] for e in j["skipped"]}
    assert set(by_id) == {"not-a-bundle", "broken-prov"}
    assert "not an imprint bundle" in by_id["not-a-bundle"]
    assert "unreadable" in by_id["broken-prov"]


@requires_real_sidecarless
def test_imprint_detail_summarizes_the_real_sidecarless_bundle(tmp_path):
    """R7 (GAP-082) -- SUPERSEDES the S1 implementation call D3 (a)'s
    "`GET /{id}` stays strict" half (`docs/WORK_ORDER_console_canvas.md`
    §8.1) under the user's D3 ruling "tolerate on read" (`docs/
    WORK_ORDER_console_level0_rendering.md` §5). The non-null plugin contract
    D3 (a) protected still holds: every number is non-null, and what was not
    measured is SAID (`provenance`, `confidence_source`, `warnings`)
    rather than defaulted into a finding."""
    c = _client(_settings(REAL_CAST_ROOT, tmp_path))
    r = c.get("/api/twins/corpapcn-twin")
    assert r.status_code == 200, r.text
    j = r.json()
    listed = {e["id"]: e for e in c.get("/api/twins").json()["imprints"]}["corpapcn-twin"]
    assert j["id"] == "corpapcn-twin"
    assert (j["hosts"], j["segments"]) == (137, 27) == (listed["hosts"], listed["segments"])
    assert j["as_of"] == listed["as_of"] != ""
    assert j["windows"] == 11
    assert j["provenance"] == "absent" and j["confidence_source"] == "absent"
    # The migrated bundle declares no `listen:` and ships 24 persona documents.
    assert (j["services"], j["personas"], j["mean_confidence"], j["out_of_vocab_pct"]) == (
        0, 24, 0.0, None,
    )
    assert not any("NOT a measurement" in w for w in j["warnings"]), j["warnings"]
    # The export bakes its PIT id; it is NAMED, never refused (LES-222).
    assert any("'pit-63994c9543-1787823960'" in w for w in j["warnings"]), j["warnings"]


def test_serve_threads_the_imprints_dir(tmp_path, monkeypatch):
    """S1.8's flag, threaded exactly like `--twin-configs-dir`."""
    import axrange.api.serve as serve_mod

    captured = {}

    def _fake_create_app(settings):
        captured["settings"] = settings
        return object()

    monkeypatch.setattr(serve_mod, "create_app", _fake_create_app)
    # Stop before uvicorn: the import inside `serve()` is what we intercept.
    monkeypatch.setitem(
        __import__("sys").modules, "uvicorn",
        type("_U", (), {"run": staticmethod(lambda *a, **k: None)})(),
    )
    rc = serve_mod.serve(
        db_path=str(tmp_path / "s.db"), imprints_dir="twins",
        log=lambda _m: None,
    )
    assert rc == 0
    assert captured["settings"].imprints_dir == "twins"


def test_serve_imprints_dir_flag_reaches_serve(monkeypatch):
    """The CLI half: `axrange serve --imprints-dir X` passes X through."""
    from axrange.cli import main as climain

    seen = {}
    import axrange.api.serve as serve_mod

    def _fake_serve(**kwargs):
        seen.update(kwargs)
        return 0

    monkeypatch.setattr(serve_mod, "serve", _fake_serve)
    args = climain._build_parser().parse_args(
        ["serve", "--imprints-dir", "twins"]
    )
    assert climain.cmd_serve(args) == 0
    assert seen["imprints_dir"] == "twins"


def test_apply_accepts_the_etag_in_the_if_match_header(tmp_path):
    root = _copy_bundle(tmp_path, "mini-cast-prov")
    c = _client(_apply_settings(root, tmp_path), backend=_FakeRecompile())
    etag = c.put("/api/twins/mini-cast-prov/assertions", json=_VALID_DOC).json()["etag"]
    r = c.post(
        "/api/twins/mini-cast-prov/apply",
        json={"document": _doc(source="operator: second")},
        headers={"If-Match": f'"{etag}"'},
    )
    assert r.status_code == 200, r.text
    assert r.json()["assertions"]["source"] == "operator: second"


def test_apply_survives_a_history_snapshot_it_cannot_write(tmp_path):
    """The snapshot is BOOKKEEPING: failing to record it degrades the
    post-apply diff and must never fail the recompile the operator asked
    for -- reported in `warnings`, never swallowed."""
    root = _copy_bundle(tmp_path, "mini-cast-prov")
    # A FILE where the history directory has to go: `mkdir` cannot succeed.
    (root / "mini-cast-prov" / ".history").write_text("not a directory")
    c = _client(_apply_settings(root, tmp_path), backend=_FakeRecompile())
    r = c.post("/api/twins/mini-cast-prov/apply", json={"document": _VALID_DOC})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["history_written"] is False
    assert any("could not record a pre-apply bundle snapshot" in w for w in j["warnings"])
    # ... and the apply itself still did its job.
    assert (root / "mini-cast-prov" / "mini-cast-prov.assertions.yaml").is_file()


# ===========================================================================
# Test-stage extensions (2026-09-05). Everything below pins a behaviour the
# S1 build has but no test asserted, chosen from the paths that a) can only
# break silently, or b) LES-185 says a hand-written fixture cannot prove --
# so the real `twin-configs-cast/` bundles are the subject wherever they can
# be. Real bundles are read-only here; every write copies one to `tmp_path`.
# ===========================================================================

# ---------------------------------------------------------------------------
# Round-trip symmetry: what GET returns must be what PUT accepts, LITERALLY.
# The assertions half is pinned above; the LAYOUT half was not, and the two
# documents strip their read-only decorations through different code
# (`AssertionRecordDTO._compact` vs `validate_layout`'s `_READ_ONLY_KEYS`).
# ---------------------------------------------------------------------------
def test_layout_get_payload_is_re_puttable_verbatim(tmp_path):
    """The SPA's save is "PUT back what you read". A `path`/`exists`/
    `imprint_id` decoration riding along must be stripped server-side, and
    the etag in the body must satisfy the guard on its own."""
    root = _copy_bundle(tmp_path, "mini-cast-prov")
    c = _client(_settings(root, tmp_path))
    c.put("/api/twins/mini-cast-prov/layout", json=_LAYOUT)

    got = c.get("/api/twins/mini-cast-prov/layout").json()
    assert {"imprint_id", "path", "exists", "etag"} <= set(got)

    again = c.put("/api/twins/mini-cast-prov/layout", json=got)
    assert again.status_code == 200, again.text
    # A no-op save is byte-identical (same determinism rule as assertions).
    assert again.json()["etag"] == got["etag"]
    assert again.json()["pins"] == got["pins"]


def test_assertions_get_payload_of_an_EMPTY_document_is_re_puttable(tmp_path):
    """The first save of a bundle that has nothing asserted yet: GET returns
    `exists: false`, `etag: null` and three empty lists, and that payload
    must be accepted once `as_of`/`source` are filled in. A `null` etag in
    the body must read as "no etag", not as a supplied one."""
    root = _copy_bundle(tmp_path, "mini-cast-prov")
    c = _client(_settings(root, tmp_path))
    empty = c.get("/api/twins/mini-cast-prov/assertions").json()
    assert empty["etag"] is None and empty["exists"] is False

    empty["as_of"] = "2026-09-05T00:00:00Z"
    empty["source"] = "operator: tester"
    r = c.put("/api/twins/mini-cast-prov/assertions", json=empty)
    assert r.status_code == 200, r.text
    assert r.json()["exists"] is True and r.json()["etag"]


def test_assertions_record_round_trip_drops_no_authored_field(tmp_path):
    """`AssertionRecordDTO._compact` omits `None`s so a GET payload is
    re-PUT-able -- this pins the other side of that trade: no field the
    operator DID author may be dropped by the same compaction."""
    root = _copy_bundle(tmp_path, "mini-cast-prov")
    c = _client(_settings(root, tmp_path))
    first = c.put("/api/twins/mini-cast-prov/assertions", json=_VALID_DOC).json()

    host_add = next(r for r in first["add"] if r["kind"] == "host")
    assert host_add["segment"] == "10.80.20.0/24"
    assert host_add["role"] == "domain-controller"
    assert host_add["listen"] == [
        {"port": 88, "proto": "tcp"}, {"port": 389, "proto": "tcp"},
    ]
    assert host_add["note"]
    # ... and nothing it did NOT author appears as a null.
    assert "field" not in host_add and "value" not in host_add
    assert "zone_type" not in host_add

    again = c.put("/api/twins/mini-cast-prov/assertions", json=first)
    assert again.status_code == 200, again.text
    assert again.json()["add"] == first["add"]


def test_override_value_of_none_survives_the_null_dropping(tmp_path):
    """M4 (was an xfail): the null-drop's ONE exception is scoped IDENTICALLY
    on both sides.

    `console_files._records` exempts `value` on an override, because an
    override may legitimately clear a field; `dtos.AssertionRecordDTO.
    _compact` re-applied the drop with no such exception, so the file on disk
    was right and re-PUTting the literal GET payload -- the SPA's documented
    save -- was a 400 "an override record requires `value`"."""
    root = _copy_bundle(tmp_path, "mini-cast-prov")
    c = _client(_settings(root, tmp_path))
    d = _doc()
    d["override"] = [
        {"key": "address:10.0.0.7", "field": "role", "value": None,
         "note": "observed role is wrong and there is no right one"},
    ]
    r = c.put("/api/twins/mini-cast-prov/assertions", json=d)
    assert r.status_code == 200, r.text
    # The FILE is right (this half already holds) ...
    from ruamel.yaml import YAML

    raw = YAML(typ="safe").load(
        (root / "mini-cast-prov" / "mini-cast-prov.assertions.yaml").read_text()
    )
    assert "value" in raw["override"][0] and raw["override"][0]["value"] is None
    # ... and the payload must be re-PUT-able, which is the half that fails.
    got = c.get("/api/twins/mini-cast-prov/assertions").json()
    assert "value" in got["override"][0]
    assert c.put(
        "/api/twins/mini-cast-prov/assertions", json=got,
    ).status_code == 200


# ---------------------------------------------------------------------------
# Refusals: EVERY one names an accepted set. The table above checks a
# per-case substring; this sweeps the same table for the property the module
# docstring promises ("every refusal names the accepted set, so a rejected
# save tells the operator what IS allowed").
# ---------------------------------------------------------------------------
_BAD_DOCS = [
    ("unknown top-level key", lambda d: d.update({"nodes": []})),
    ("unknown add kind", lambda d: d["add"].append(
        {"kind": "vlan", "key": "10.9.0.0/24", "name": "n", "note": "x"})),
    ("unknown add field", lambda d: d["add"].append(
        {"kind": "segment", "key": "10.9.0.0/24", "name": "n", "note": "x",
         "colour": "blue"})),
    ("unknown suppress field", lambda d: d["suppress"].append(
        {"key": "address:10.9.0.9", "note": "x", "reason": "y"})),
    ("unusable key shape", lambda d: d["suppress"].append(
        {"key": "not-a-key", "note": "x"})),
    ("kind/key disagreement", lambda d: d["add"].append(
        {"kind": "host", "key": "10.9.0.0/24", "segment": "10.9.0.0/24", "note": "x"})),
    ("bad conduit key", lambda d: d["add"].append(
        {"kind": "conduit", "key": "10.9.0.0/24|nope", "note": "x"})),
    ("listen not a list", lambda d: d["add"].append(
        {"kind": "host", "key": "address:10.9.0.8", "segment": "10.9.0.0/24",
         "listen": {"port": 80}, "note": "x"})),
    ("listen entry unknown field", lambda d: d["add"].append(
        {"kind": "host", "key": "address:10.9.0.10", "segment": "10.9.0.0/24",
         "listen": [{"port": 80, "proto": "tcp", "svc": "http"}], "note": "x"})),
    ("resources not a mapping", lambda d: d["add"].append(
        {"kind": "host", "key": "address:10.9.0.11", "segment": "10.9.0.0/24",
         "resources": ["4cpu"], "note": "x"})),
    ("duplicate add key", lambda d: d["add"].append(dict(d["add"][0]))),
    ("duplicate suppress key", lambda d: d["suppress"].append(dict(d["suppress"][0]))),
    ("duplicate (key, field) override", lambda d: d["override"].append(
        dict(d["override"][0]))),
]


@pytest.mark.parametrize("label,mutate", _BAD_DOCS, ids=[c[0] for c in _BAD_DOCS])
def test_every_assertions_refusal_names_what_would_have_been_accepted(
    tmp_path, label, mutate,
):
    root = _copy_bundle(tmp_path, "mini-cast-prov")
    c = _client(_settings(root, tmp_path))
    d = _doc()
    mutate(d)
    r = c.put("/api/twins/mini-cast-prov/assertions", json=d)
    assert r.status_code == 400, (label, r.status_code, r.text)
    msg = r.json()["error"]
    # Either it lists the accepted set, or it says WHY this one thing is
    # refused by name (`route`) / is a duplicate. Never a bare "invalid".
    assert any(
        token in msg
        for token in (
            "accepted",      # "...; accepted: ['segment', 'host', 'conduit']"
            "carries",       # "a suppress record carries ['kind', 'key', 'note']"
            "but key",       # "declares kind 'host' but key '10.9.0.0/24' is a segment key"
            "must be", "requires", "twice", "one of", "refused",
        )
    ), (label, msg)
    assert not (
        root / "mini-cast-prov" / "mini-cast-prov.assertions.yaml"
    ).exists(), f"{label}: a refused document must write nothing"


@pytest.mark.parametrize("bad_key", ["hostname:dc01", "mac:00:11:22:33:44:55", "address:10.0.0.1"])
def test_layout_refuses_every_entity_key_shaped_pin_key(tmp_path, bad_key):
    """The three entity-key shapes stay in `_REFUSED_PIN_PREFIXES` -- a pin
    that silently did nothing is worse than a refusal (`15` §4). `host:` left
    that set for the design canvas (`ui/src/features/edit-canvas/SPEC.md`),
    which `test_twin_bundle_layout.py` covers."""
    root = _copy_bundle(tmp_path, "mini-cast-prov")
    c = _client(_settings(root, tmp_path))
    r = c.put(
        "/api/twins/mini-cast-prov/layout",
        json=dict(_LAYOUT, pins={bad_key: {"x": 1, "y": 2}}),
    )
    assert r.status_code == 400, r.text
    assert "hosts are NEVER" in r.json()["error"]


# ---------------------------------------------------------------------------
# Writes are atomic, and a REFUSED write leaves the existing file alone.
# ---------------------------------------------------------------------------
def _atomic_targets(root: Path):
    b = root / "mini-cast-prov"
    return b, b / "mini-cast-prov.assertions.yaml", b / "layout.json"


def test_atomic_write_failure_leaves_neither_a_partial_file_nor_a_temp_sibling(
    tmp_path, monkeypatch,
):
    """`_atomic_write` is a sibling-temp + `os.replace`. If the replace
    fails, the CURRENT file must survive byte-identical and no `.tmp` sibling
    may be left behind for the next reader (or the next `glob`) to find --
    a half-written assertions file is one the compiler would read as the
    operator's intent."""
    import os as _os

    from axrange.api import console_files as cf

    root = _copy_bundle(tmp_path, "mini-cast-prov")
    bundle, apath, _ = _atomic_targets(root)
    c = _client(_settings(root, tmp_path))
    first = c.put("/api/twins/mini-cast-prov/assertions", json=_VALID_DOC).json()
    before = apath.read_bytes()

    def _boom(src, dst):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(_os, "replace", _boom)
    d = _doc(etag=first["etag"])
    d["source"] = "operator: someone else"
    # M5: a filesystem refusal is a typed `ConsoleFileWriteError` -> 503, not
    # a raw OSError escaping the handler as a 500. 503 rather than 400
    # because a read-only bundle directory is the server's environment, not
    # the operator's document -- telling them their edit was malformed sends
    # them to fix the wrong thing.
    r = c.put("/api/twins/mini-cast-prov/assertions", json=d)
    assert r.status_code == 503, r.text
    assert "cannot write" in r.json()["error"]

    monkeypatch.undo()
    assert apath.read_bytes() == before, "the existing document was damaged"
    strays = [p.name for p in bundle.iterdir() if ".tmp" in p.name]
    assert strays == [], strays
    assert cf.compute_etag(apath.read_text()) == first["etag"]


def test_a_refused_save_leaves_an_EXISTING_assertions_file_byte_identical(tmp_path):
    """Validation runs before the etag guard and before any byte is written.
    The already-pinned case is a first save (nothing to damage); this is the
    one that matters in use -- a bad edit on top of a good file."""
    root = _copy_bundle(tmp_path, "mini-cast-prov")
    _, apath, _ = _atomic_targets(root)
    c = _client(_settings(root, tmp_path))
    good = c.put("/api/twins/mini-cast-prov/assertions", json=_VALID_DOC).json()
    before = apath.read_bytes()

    bad = _doc(etag=good["etag"])
    bad["add"].append({"kind": "nonsense", "key": "10.9.0.0/24", "note": "x"})
    r = c.put("/api/twins/mini-cast-prov/assertions", json=bad)
    assert r.status_code == 400
    assert apath.read_bytes() == before


def test_a_refused_save_leaves_an_EXISTING_layout_file_byte_identical(tmp_path):
    root = _copy_bundle(tmp_path, "mini-cast-prov")
    _, _, lpath = _atomic_targets(root)
    c = _client(_settings(root, tmp_path))
    good = c.put("/api/twins/mini-cast-prov/layout", json=_LAYOUT).json()
    before = lpath.read_bytes()

    r = c.put(
        "/api/twins/mini-cast-prov/layout",
        json=dict(_LAYOUT, etag=good["etag"], pins={"hostname:x": {"x": 1, "y": 1}}),
    )
    assert r.status_code == 400
    assert lpath.read_bytes() == before


def test_layout_disagreeing_etags_are_refused_rather_than_guessed(tmp_path):
    """The assertions half of this is pinned; the layout PUT carries its own
    copy of the same check, so it needs its own test or the copy can rot."""
    root = _copy_bundle(tmp_path, "mini-cast-prov")
    c = _client(_settings(root, tmp_path))
    etag = c.put("/api/twins/mini-cast-prov/layout", json=_LAYOUT).json()["etag"]
    r = c.put(
        "/api/twins/mini-cast-prov/layout",
        json=dict(_LAYOUT, etag=etag),
        headers={"If-Match": '"' + "0" * 64 + '"'},
    )
    assert r.status_code == 400
    assert "disagree" in r.json()["error"]


def test_etag_conflict_carries_the_on_disk_etag_so_a_client_can_reload(tmp_path):
    """412's whole point is recoverability: the response must name the etag
    the client should have had, or the SPA can only tell the operator to
    refresh and hope."""
    root = _copy_bundle(tmp_path, "mini-cast-prov")
    c = _client(_settings(root, tmp_path))
    good = c.put("/api/twins/mini-cast-prov/assertions", json=_VALID_DOC).json()
    r = c.put(
        "/api/twins/mini-cast-prov/assertions", json=_doc(etag="0" * 64),
    )
    assert r.status_code == 412
    body = r.json()
    assert good["etag"] in json.dumps(body), body


# ---------------------------------------------------------------------------
# `.history` -- WHICH five survive, not only how many.
# ---------------------------------------------------------------------------
def test_history_eviction_keeps_the_NEWEST_five_by_mtime(tmp_path):
    """`prune_history` sorts by mtime descending and unlinks the tail. The
    shipped test asserts the COUNT; this asserts the ORDER, which is the part
    that decides whether `diff?since=` can still answer for a recent apply.
    mtimes are set explicitly -- eight writes in a tight loop can land inside
    one filesystem timestamp tick, and a tie is resolved by glob order, which
    is arbitrary."""
    import os as _os

    from axrange.api import recompile as rc

    d = tmp_path / "bundle"
    d.mkdir()
    for i in range(8):
        p = rc.write_history_snapshot(d, f"fp{i}", {"fingerprint": f"fp{i}"}, keep=99)
        _os.utime(p, (1_700_000_000 + i * 10, 1_700_000_000 + i * 10))
    assert rc.list_history(d) == [f"fp{i}" for i in range(8)]

    rc.prune_history(d, keep=5)
    assert rc.list_history(d) == ["fp3", "fp4", "fp5", "fp6", "fp7"]


def test_history_snapshot_of_the_same_fingerprint_overwrites_rather_than_grows(tmp_path):
    """A snapshot is keyed by fingerprint, so re-applying under the SAME
    fingerprint replaces the file. Pinned because it is load-bearing for
    `diff?since=`: the answer is always against the most recent apply that
    carried that fingerprint, never an older one."""
    from axrange.api import recompile as rc

    d = tmp_path / "bundle"
    d.mkdir()
    rc.write_history_snapshot(d, "fpA", {"fingerprint": "fpA", "nodes": {"a": 1}})
    rc.write_history_snapshot(d, "fpA", {"fingerprint": "fpA", "nodes": {"b": 2}})
    assert rc.list_history(d) == ["fpA"]
    assert rc.load_history_snapshot(d, "fpA")["nodes"] == {"b": 2}


@pytest.mark.parametrize("bad", ["../escape", "a/b", "a\\b", "", "  ", ".."])
def test_history_refuses_every_path_shaped_fingerprint(tmp_path, bad):
    """`_safe_fingerprint` guards the read AND the write; only the read path
    had a test, and the write is the one that could plant a file outside the
    bundle."""
    from axrange.api import recompile as rc

    d = tmp_path / "bundle"
    d.mkdir()
    with pytest.raises(ValueError):
        rc.write_history_snapshot(d, bad, {})
    with pytest.raises(ValueError):
        rc.load_history_snapshot(d, bad)


def test_a_corrupt_snapshot_is_UNREADABLE_not_absent(tmp_path):
    """S3: a truncated snapshot must not 500 the diff endpoint (LES-110), and
    it must not report as ABSENT either -- it is listed by `list_history` as
    known, so "no apply produced that fingerprint" sends the operator looking
    for the wrong thing."""
    from axrange.api import recompile as rc

    d = tmp_path / "bundle"
    rc.history_dir(d).mkdir(parents=True)
    (rc.history_dir(d) / "fpX.json").write_text("{ truncated")
    (rc.history_dir(d) / "fpY.json").write_text('["not", "an", "object"]')
    for fp in ("fpX", "fpY"):
        with pytest.raises(rc.HistorySnapshotUnreadable):
            rc.load_history_snapshot(d, fp)
    # ... while a fingerprint nothing ever wrote is genuinely absent.
    assert rc.load_history_snapshot(d, "fpZ") is None
    assert rc.list_history(d) == ["fpX", "fpY"]


def test_the_history_snapshot_write_is_atomic(tmp_path):
    """S3: the snapshot goes through the same atomic writer the operator
    files use, so an interrupted apply cannot leave a half-written snapshot
    the next diff reports as corrupt."""
    import os as _os

    from axrange.api import recompile as rc

    d = tmp_path / "bundle"
    rc.write_history_snapshot(d, "fpA", {"fingerprint": "fpA", "nodes": {"a": 1}})
    before = (rc.history_dir(d) / "fpA.json").read_bytes()

    def _boom(src, dst):
        raise OSError(28, "No space left on device")

    from axrange.api.console_files import ConsoleFileWriteError

    real = _os.replace
    _os.replace = _boom
    try:
        with pytest.raises(ConsoleFileWriteError):
            rc.write_history_snapshot(d, "fpA", {"fingerprint": "fpA", "nodes": {"b": 2}})
    finally:
        _os.replace = real
    assert (rc.history_dir(d) / "fpA.json").read_bytes() == before
    assert [p.name for p in rc.history_dir(d).iterdir() if ".tmp" in p.name] == []


def test_diff_against_a_corrupt_snapshot_is_a_404_not_a_500(tmp_path):
    from axrange.api import recompile as rc

    root = _copy_bundle(tmp_path, "mini-cast-prov")
    rc.history_dir(root / "mini-cast-prov").mkdir(parents=True)
    (rc.history_dir(root / "mini-cast-prov") / "fpZ.json").write_text("{ nope")
    c = _client(_settings(root, tmp_path))
    r = c.get("/api/twins/mini-cast-prov/diff", params={"since": "fpZ"})
    assert r.status_code == 404, r.text
    # S3: the 404 says UNREADABLE, not "no such fingerprint" -- the file is
    # there and `list_history` lists it.
    assert "cannot be read" in r.json()["error"]


# ---------------------------------------------------------------------------
# The apply lock, over HTTP -- the shipped test exercises the context manager
# directly, which cannot tell you the ROUTE takes it.
# ---------------------------------------------------------------------------
def test_apply_over_http_is_409_while_the_imprint_lock_is_held(tmp_path):
    """Deterministic half: the lock is process-global and per-imprint, so
    holding it from the test thread proves the route consults it."""
    from axrange.api.recompile import imprint_apply_lock

    root = _copy_bundle(tmp_path, "mini-cast-prov")
    c = _client(_apply_settings(root, tmp_path), backend=_FakeRecompile())
    with imprint_apply_lock("mini-cast-prov"):
        r = c.post("/api/twins/mini-cast-prov/apply", json={"document": _VALID_DOC})
        assert r.status_code == 409, r.text
        assert "already running" in r.json()["error"]
        # A DIFFERENT imprint is unaffected (the lock is per-imprint, and a
        # global one would serialize the whole Console).
        assert not (root / "mini-cast-prov" / "mini-cast-prov.assertions.yaml").exists()
    # Released: the same call now gets past the guard.
    assert c.post(
        "/api/twins/mini-cast-prov/apply", json={"document": _VALID_DOC},
    ).status_code == 200


def test_two_genuinely_concurrent_applies_yield_one_200_and_one_409(tmp_path):
    """The real race, not a simulation: a backend that blocks inside
    `recompile` holds the lock while a second request arrives on another
    thread. Proves the lock spans the SUBPROCESS, not just the save."""
    import threading

    root = _copy_bundle(tmp_path, "mini-cast-prov")
    entered = threading.Event()
    release = threading.Event()

    class _Blocking(_FakeRecompile):
        def recompile(self, request):
            entered.set()
            assert release.wait(timeout=30), "second request never arrived"
            return super().recompile(request)

    settings = _apply_settings(root, tmp_path)
    c1 = _client(settings, backend=_Blocking())
    c2 = _client(_apply_settings(root, tmp_path), backend=_FakeRecompile())

    results: dict[str, int] = {}

    def _first():
        results["a"] = c1.post(
            "/api/twins/mini-cast-prov/apply", json={"document": _VALID_DOC},
        ).status_code

    t = threading.Thread(target=_first, daemon=True)
    t.start()
    assert entered.wait(timeout=30), "the first apply never reached the backend"
    try:
        r2 = c2.post("/api/twins/mini-cast-prov/apply", json={"document": _VALID_DOC})
        results["b"] = r2.status_code
    finally:
        release.set()
        t.join(timeout=30)

    assert sorted(results.values()) == [200, 409], results


# ---------------------------------------------------------------------------
# Recompile failure modes the shipped tests do not reach.
# ---------------------------------------------------------------------------
def test_apply_timeout_is_502_and_names_the_timeout(tmp_path):
    """A hung compiler must release the lock and report, not hang the SPA."""
    from axrange.api import recompile as rc

    class _Hangs:
        def recompile(self, request):
            raise rc.RecompileFailedError(
                "recompile timed out after 900s: aximprint cast",
                result=rc.RecompileResult(argv=["aximprint"], returncode=-1, stderr=""),
            )

    root = _copy_bundle(tmp_path, "mini-cast-prov")
    c = _client(_apply_settings(root, tmp_path), backend=_Hangs())
    r = c.post("/api/twins/mini-cast-prov/apply", json={"document": _VALID_DOC})
    assert r.status_code == 502, r.text
    assert "timed out" in r.json()["error"]
    # The lock was released despite the failure, so the operator can retry.
    from axrange.api.recompile import imprint_apply_lock

    with imprint_apply_lock("mini-cast-prov"):
        pass
    # ... and the SAVE survived the failed compile (`14` §4: work is never
    # lost to a compiler).
    assert (root / "mini-cast-prov" / "mini-cast-prov.assertions.yaml").is_file()


def test_apply_backend_unavailable_is_503_not_502(tmp_path):
    """"nothing was run" and "it ran and failed" are different operator
    situations, and the status code is the only place that distinction
    survives to the SPA."""
    from axrange.api import recompile as rc

    class _Missing:
        def recompile(self, request):
            raise rc.RecompileUnavailableError("recompiler not found: 'aximprint'")

    root = _copy_bundle(tmp_path, "mini-cast-prov")
    c = _client(_apply_settings(root, tmp_path), backend=_Missing())
    r = c.post("/api/twins/mini-cast-prov/apply", json={"document": _VALID_DOC})
    assert r.status_code == 503, r.text


@pytest.mark.parametrize(
    "before,after,expect",
    [
        ("fp-old", "fp-new", None),
        ("fp-same", "fp-same", "content fingerprint is unchanged"),
        ("fp-old", None, "nothing landed"),
        (None, "fp-new", None),
    ],
)
def test_the_landing_check_compares_the_content_fingerprint(
    tmp_path, before, after, expect,
):
    """R4: the landing check asks whether the bundle CHANGED, not whether a
    file exists.

    The file-existence version looked for `<out>/<id>.yaml` -- which on a
    real bundle is the PRE-EXISTING file the install stage overwrites, so the
    guard was silent whether or not anything landed (found live, 2026-09-05).
    An unchanged fingerprint is not automatically wrong -- `14` §3's
    determinism rule says a no-op save is a no-op recompile. WHICH reading
    applies is the second half of the message, and GAP-090's subject; see
    `test_the_unchanged_fingerprint_warning_says_WHICH_reading_applies` for
    that half. This one is about the comparison itself."""
    from axrange.api import recompile as rc

    req = _pinned_request(bundle_dir=tmp_path, catalog_root=tmp_path)
    result = rc.RecompileResult(argv=[], returncode=0)
    rc.check_output_landed(req, result, before=before, after=after)
    if expect is None:
        assert result.warnings == []
    else:
        assert any(expect in w for w in result.warnings), result.warnings


# ---------------------------------------------------------------------------
# GAP-090 -- the no-op apply warning said "either ... or ..." while the server
# was holding the evidence that decides it
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "document,install_rc,expect,forbid",
    [
        # Counted records + an install that returned 0: the determinism
        # reading, named, with N.
        (
            {"assertions": {"add": 2, "override": 1, "suppress": 1}}, 0,
            "your 4 assertion record(s) (add=2, override=1, suppress=1) "
            "compiled to the same bytes",
            "either this apply was a no-op",
        ),
        # An EMPTY assertions file is its own, even more specific case: there
        # was nothing for the recompile to change.
        (
            {"assertions": {"add": 0, "override": 0, "suppress": 0}}, 0,
            "holds no records at all", "either this apply was a no-op",
        ),
        # The genuinely-unknown cases KEEP the disjunction -- and say why the
        # server cannot pick, rather than leaving the operator to guess that
        # it could have.
        (
            {"assertions": {"add": 1}}, None,
            "the install stage did not report an exit code", None,
        ),
        (
            None, 0,
            "printed no record counts this apply could read", None,
        ),
    ],
)
def test_the_unchanged_fingerprint_warning_says_WHICH_reading_applies(
    tmp_path, document, install_rc, expect, forbid,
):
    """GAP-090: "either the assertions changed nothing **or** the recompile
    did not land" left the operator to pick between two readings the server
    could already tell apart -- it holds the record counts, the fingerprint
    pair and the install return code.

    The disjunction is kept for, and only for, the case that genuinely is
    one."""
    from axrange.api import recompile as rc

    req = _pinned_request(bundle_dir=tmp_path, catalog_root=tmp_path)
    result = rc.RecompileResult(
        argv=[], returncode=0, document=document, install_returncode=install_rc,
    )
    rc.check_output_landed(req, result, before="fp-same", after="fp-same")
    (warning,) = result.warnings
    assert "content fingerprint is unchanged (fp-same)" in warning
    assert expect in warning, warning
    if forbid is not None:
        assert forbid not in warning, warning


def test_the_no_op_apply_warning_reaches_the_HTTP_response_unambiguous(tmp_path):
    """GAP-090 end to end: the specific sentence is what a Console (or a
    `curl`) actually receives, not only what the helper returns."""
    root = _copy_bundle(tmp_path, "mini-cast-prov")
    c = _client(
        _apply_settings(root, tmp_path),
        # Recompiles to the SAME build, so nothing in the bundle moves: the
        # byte-identical case, with an install stage that reported success.
        backend=_RecompileAsANewerCompiler("fixture-castbuild-01"),
    )
    j = c.post(
        "/api/twins/mini-cast-prov/apply", json={"document": _VALID_DOC},
    ).json()
    assert j["changed"] is False
    (unchanged,) = [w for w in j["warnings"] if "fingerprint is unchanged" in w]
    assert "your 1 assertion record(s)" in unchanged
    assert "the recompile produced no change to the cast" in unchanged
    # Byte-identity does not prove each record took effect -- the sentence
    # must not claim it did (fix round 1, finding 5).
    assert "honored" not in unchanged
    # And with nothing moved, the unknown-compiler note (if any) must not
    # raise a population difference that does not exist (finding 6).
    assert j["compiler_changed"] is False
    assert "either this apply was a no-op" not in unchanged


# ---------------------------------------------------------------------------
# GAP-089 -- a parameter description that names a field is testable
# ---------------------------------------------------------------------------
def test_the_diff_since_description_names_the_field_that_actually_works(tmp_path):
    """GAP-089: the description read as "the apply response's
    `code_fingerprint_before`" -- a value that 404s, because it is an
    aximprint build stamp and names no history snapshot. Proven both ways in
    the S6 acceptance run's evidence.

    Both halves are asserted here, against the description the SERVER
    publishes: the field it names works, and the one it warns off does not."""
    from axrange.api import dtos
    from axrange.api.app import DIFF_SINCE_FIELD

    root = _copy_bundle(tmp_path, "mini-cast-prov")
    c = _client(_apply_settings(root, tmp_path), backend=_FakeRecompile())

    # 1. The description in the OpenAPI document names the constant, so the
    #    prose and the value this test drives cannot drift apart.
    spec = c.get("/openapi.json").json()
    param = next(
        p for p in spec["paths"]["/api/twins/{imprint_id}/diff"]["get"]["parameters"]
        if p["name"] == "since"
    )
    assert f"`{DIFF_SINCE_FIELD}`" in param["description"]
    # 2. ... and it is a real field of the response it says to read it from.
    assert DIFF_SINCE_FIELD in dtos.ImprintApplyResponse.model_fields

    applied = c.post(
        "/api/twins/mini-cast-prov/apply", json={"document": _VALID_DOC},
    ).json()
    # 3. The named field's value is diffable.
    named = c.get(
        "/api/twins/mini-cast-prov/diff", params={"since": applied[DIFF_SINCE_FIELD]},
    )
    assert named.status_code == 200, named.text

    # 4. ... and the value the OLD description named is not, which is the gap.
    #    (`pit_code_fingerprint_before` is the same value the field formerly
    #    spelled `code_fingerprint_before` carried.)
    stale = c.get(
        "/api/twins/mini-cast-prov/diff",
        params={"since": applied["pit_code_fingerprint_before"]},
    )
    assert stale.status_code == 404, stale.text
    for name in ("pit_code_fingerprint_", "cast_code_fingerprint_"):
        assert name in param["description"], param["description"]


def test_the_content_fingerprint_moves_when_any_canonical_file_moves(tmp_path):
    """R4's own property, and the one `code_fingerprint` did not have: the
    fingerprint is a function of the bundle's CONTENT, so editing any of the
    three canonical files changes it and editing nothing does not."""
    from axrange.api.recompile import bundle_fingerprint

    root = _copy_bundle(tmp_path, "mini-cast-prov")
    b = root / "mini-cast-prov"
    base = bundle_fingerprint(b, "mini-cast-prov")
    assert base and bundle_fingerprint(b, "mini-cast-prov") == base

    for name in (
        "mini-cast-prov.yaml",
        "mini-cast-prov.topology.json",
        "mini-cast-prov.provenance.json",
    ):
        path = b / name
        original = path.read_bytes()
        path.write_bytes(original + b"\n# touched\n")
        assert bundle_fingerprint(b, "mini-cast-prov") != base, name
        path.write_bytes(original)
        assert bundle_fingerprint(b, "mini-cast-prov") == base, name

    # A file VANISHING is a change too -- an absence is hashed as an absence,
    # never skipped (which would make "sidecar deleted" invisible).
    (b / "mini-cast-prov.provenance.json").unlink()
    assert bundle_fingerprint(b, "mini-cast-prov") != base
    # ... and a directory with none of the canonical files is not a bundle.
    assert bundle_fingerprint(tmp_path / "nowhere", "x") is None


def test_the_content_fingerprint_is_not_the_compilers_build_hash(tmp_path):
    """F3/R4, stated as a test: the two real bundles carry DIFFERENT content
    and the `code_fingerprint` cannot tell them apart, which is exactly why
    `changed` was permanently false and keep-5 history collapsed to keep-1."""
    from axrange.api.recompile import bundle_fingerprint

    root = _copy_bundle(tmp_path, "mini-cast-prov")
    shutil.copytree(CAST_FIXTURES / "mini-cast", root / "mini-cast")
    a = bundle_fingerprint(root / "mini-cast-prov", "mini-cast-prov")
    b = bundle_fingerprint(root / "mini-cast", "mini-cast")
    assert a and b and a != b
    assert a != "fixture-0000000000"


@pytest.mark.parametrize(
    "stdout,expect_doc,expect_warning",
    [
        ('{"imprint_id": "x", "nodes": 3}', True, None),
        ("", False, "printed nothing"),
        ("not json at all", False, "not JSON"),
        ('["a", "list"]', False, "not an object"),
    ],
)
def test_recompile_stdout_parsing_never_invents_a_shape(stdout, expect_doc, expect_warning):
    """The compiler "prints JSON on stdout" is a CONTRACT, and a contract the
    other repo can break. Every non-conforming shape must degrade to a
    warning + `document: None`, never to a fabricated record count."""
    from axrange.api import recompile as rc

    result = rc.RecompileResult(argv=[], returncode=0, stdout=stdout)
    rc._parse_stdout(result)
    assert (result.document is not None) is expect_doc
    if expect_warning:
        assert any(expect_warning in w for w in result.warnings), result.warnings
    else:
        assert result.warnings == []


def test_apply_reads_the_REAL_cast_stdout_shape(tmp_path):
    """F4, from the live run (2026-09-05): the real `aximprint cast` prints
    `{pit_id, imprint_id, out, assertions{path, add, override, suppress},
    segments, nodes, conduits, assignments, provenance_fields, caveats}` --
    there is NO `record_counts` key, so THIS is the primary path, not a
    fallback.

    `assertions` IS the record count of the file this apply just saved, which
    is what "record counts" means on this endpoint; the bundle's own counts
    are published beside it under their own name, because "how many segments
    the cast emitted" and "how many records the operator asserted" are two
    different questions; and `caveats[]` becomes warnings instead of being
    dropped on the floor."""
    from axrange.api import recompile as rc

    class _RealShapedStdout:
        def recompile(self, request):
            r = rc.RecompileResult(
                argv=["aximprint", "cast"], returncode=0,
                stdout=json.dumps({
                    "pit_id": "pit-1", "imprint_id": request.imprint_id,
                    "out": str(request.out_dir),
                    "assertions": {
                        "path": str(request.assertions_path),
                        "add": 0, "override": 0, "suppress": 1,
                    },
                    "segments": 33, "nodes": 87, "conduits": 31,
                    "assignments": 108, "provenance_fields": 681,
                    "caveats": ["a node group was capped at 12"],
                }),
            )
            rc._parse_stdout(r)
            return r

    root = _copy_bundle(tmp_path, "mini-cast-prov")
    c = _client(_apply_settings(root, tmp_path), backend=_RealShapedStdout())
    j = c.post(
        "/api/twins/mini-cast-prov/apply", json={"document": _VALID_DOC},
    ).json()
    assert j["record_counts"] == {"add": 0, "override": 0, "suppress": 1}
    assert "path" not in j["record_counts"]      # a string is not a count
    assert j["cast_counts"] == {
        "segments": 33, "nodes": 87, "conduits": 31,
        "assignments": 108, "provenance_fields": 681,
    }
    # The nested/str/list members are NOT counts and must not be coerced.
    assert "caveats" not in j["cast_counts"]
    assert "assertions" not in j["cast_counts"]
    assert "imprint_id" not in j["cast_counts"]
    # The compiler's own caveats reach the operator.
    assert any("capped at 12" in w for w in j["warnings"])
    # The compiler's own document survives verbatim beside the derived maps.
    assert j["stdout_json"]["assertions"]["suppress"] == 1


def test_an_explicit_record_counts_key_still_wins(tmp_path):
    """A producer that publishes `record_counts` outright is read first --
    the `assertions` block is the shape the CURRENT compiler prints, not a
    promise that no producer ever prints the other."""
    from axrange.api.recompile import record_counts_from

    assert record_counts_from({"record_counts": {"add": 3}}) == {"add": 3}
    assert record_counts_from({"assertions": {"path": "p", "add": 1}}) == {"add": 1}
    assert record_counts_from({}) == {}
    assert record_counts_from(None) == {}


def test_a_huge_stdout_document_is_capped_in_the_response(tmp_path):
    """S7/LES-206: a producer that prints megabytes must not put megabytes in
    a JSON response body. The elision is stated in the document, never
    silent."""
    from axrange.api import recompile as rc

    class _Chatty:
        def recompile(self, request):
            r = rc.RecompileResult(
                argv=["aximprint", "cast"], returncode=0,
                stdout=json.dumps({
                    "imprint_id": request.imprint_id, "nodes": 3,
                    "caveats": ["x" * 200] * 200,
                }),
            )
            rc._parse_stdout(r)
            return r

    root = _copy_bundle(tmp_path, "mini-cast-prov")
    c = _client(_apply_settings(root, tmp_path), backend=_Chatty())
    j = c.post(
        "/api/twins/mini-cast-prov/apply", json={"document": _VALID_DOC},
    ).json()
    assert "_elided" in j["stdout_json"]
    assert "caveats" not in j["stdout_json"]
    # ... and the DERIVED counts are unaffected by the cap.
    assert j["cast_counts"] == {"nodes": 3}


def test_a_huge_stderr_is_capped_in_the_error_detail(tmp_path):
    """The same cap on the failure path, which is where a compiler is most
    likely to print a wall of text (LES-206: elision threaded through EVERY
    surface, not just the first one built)."""
    from axrange.api import recompile as rc

    boom = rc.RecompileFailedError(
        "recompile failed",
        result=rc.RecompileResult(argv=["fake"], returncode=1, stderr="E\n" * 100_000),
    )
    root = _copy_bundle(tmp_path, "mini-cast-prov")
    c = _client(_apply_settings(root, tmp_path), backend=_FakeRecompile(raise_=boom))
    r = c.post("/api/twins/mini-cast-prov/apply", json={"document": _VALID_DOC})
    assert r.status_code == 502
    assert len(r.json()["detail"]["stderr"]) <= rc.MAX_STREAM_CHARS + 200
    assert len(r.json()["error"]) < 10_000
    # S9: the save landed, so the failure hands the new etag back.
    assert r.json()["detail"]["etag"]


# ---------------------------------------------------------------------------
# The provenance adapter: THREE shapes, six classes, `evidence_source` always
# stated -- and an unknown class WARNS rather than 500s (LES-110/LES-219).
# ---------------------------------------------------------------------------
_SIX = ("asserted", "imported", "observed", "referenced", "imputed", "compiler")


@pytest.mark.parametrize(
    "catalog,imprint,shape,source",
    [
        ("fixtures", "corp-enterprise", "canonical", "sidecar fields"),
        ("cast", "mini-cast-prov", "canonical", "sidecar fields"),
        ("cast", "mini-cast", "absent", "re-derived from topology export"),
    ],
)
def test_provenance_states_its_shape_and_evidence_source_for_all_three(
    tmp_path, catalog, imprint, shape, source,
):
    """One table for the whole S1.4 contract: whichever shape a bundle
    carries, the answer NAMES it and NAMES whether the producer supplied the
    classes -- never inferred from the presence of a warning (LES-219)."""
    if catalog == "fixtures":
        c = _client(ApiSettings(
            db_path=str(tmp_path / "s.db"), adapter_config="/nonexistent.conf",
        ))
    else:
        c = _client(_settings(CAST_FIXTURES, tmp_path))
    r = c.get(f"/api/twins/{imprint}/provenance")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["provenance"] == shape
    assert j["evidence_source"] == source
    assert {row["evidence_class"] for row in j["rows"]} <= set(_SIX)
    # Every row carries the pair, so a consumer never reads a bare float.
    for row in j["rows"]:
        assert isinstance(row["confidence"], float)
        assert isinstance(row["has_confidence"], bool)


def _doctor_canonical_class(root: Path, imprint: str, new_class: str) -> None:
    p = root / imprint / f"{imprint}.provenance.json"
    doc = json.loads(p.read_text())
    first = sorted(doc["fields"])[0]
    doc["fields"][first]["evidence_class"] = new_class
    p.write_text(json.dumps(doc))


def test_an_unknown_evidence_class_in_a_CANONICAL_sidecar_warns_and_is_published_as_is(
    tmp_path,
):
    """LES-110/LES-219: producer/consumer drift on the class vocabulary is a
    thing to REPORT, never to normalize away and never to 500 on. The
    six-class list is published in the warning so the reader can see which
    side moved."""
    root = _copy_bundle(tmp_path, "mini-cast-prov")
    _doctor_canonical_class(root, "mini-cast-prov", "hearsay")
    c = _client(_settings(root, tmp_path))
    r = c.get("/api/twins/mini-cast-prov/provenance")
    assert r.status_code == 200, r.text
    j = r.json()
    assert "hearsay" in {row["evidence_class"] for row in j["rows"]}
    warn = " ".join(j["warnings"])
    assert "hearsay" in warn
    assert "never rewritten" in warn
    for cls in _SIX:
        assert cls in warn, "the warning must publish the accepted vocabulary"


def test_a_canonical_row_with_a_non_object_overrides_is_a_400_not_a_500(tmp_path):
    root = _copy_bundle(tmp_path, "mini-cast-prov")
    p = root / "mini-cast-prov" / "mini-cast-prov.provenance.json"
    doc = json.loads(p.read_text())
    doc["fields"][sorted(doc["fields"])[0]]["overrides"] = "imputed"
    p.write_text(json.dumps(doc))
    c = _client(_settings(root, tmp_path))
    assert c.get("/api/twins/mini-cast-prov/provenance").status_code == 400


def test_a_canonical_row_that_is_not_an_object_is_a_400_not_a_500(tmp_path):
    root = _copy_bundle(tmp_path, "mini-cast-prov")
    p = root / "mini-cast-prov" / "mini-cast-prov.provenance.json"
    doc = json.loads(p.read_text())
    doc["fields"]["twin_config.bogus"] = "not an object"
    p.write_text(json.dumps(doc))
    c = _client(_settings(root, tmp_path))
    assert c.get("/api/twins/mini-cast-prov/provenance").status_code == 400


def test_canonical_overrides_read_the_legacy_class_spelling_too(tmp_path):
    """The sidecar spells the losing class `class` OR `evidence_class`
    depending on its vintage; the adapter reads both and publishes ONE. The
    `evidence_class` spelling is pinned by the shipped fixture; this is the
    legacy `class` half."""
    root = _copy_bundle(tmp_path, "mini-cast-prov")
    p = root / "mini-cast-prov" / "mini-cast-prov.provenance.json"
    doc = json.loads(p.read_text())
    key = sorted(doc["fields"])[0]
    doc["fields"][key] = {
        "evidence_class": "asserted", "confidence": 1.0, "signal": "operator",
        "source": "operator: tester",
        "overrides": {"class": "observed", "value": "old", "confidence": 0.7,
                      "drift": True},
    }
    p.write_text(json.dumps(doc))
    c = _client(_settings(root, tmp_path))
    row = next(
        r for r in c.get("/api/twins/mini-cast-prov/provenance").json()["rows"]
        if r["field"] == key
    )
    assert row["overrides"]["evidence_class"] == "observed"
    assert row["overrides"]["value"] == "old"
    assert row["overrides"]["drift"] is True


# ---------------------------------------------------------------------------
# The REAL bundles (LES-185). Read-only against the checked-in ones; the
# write round trip runs on a COPY.
# ---------------------------------------------------------------------------
@requires_real_golden
@requires_real_sidecarless
@pytest.mark.parametrize("imprint", ["corpapcn-golden", "corpapcn-twin"])
def test_topology_alias_matches_the_shipped_route_on_the_real_bundles(tmp_path, imprint):
    """D1 (a) says "no second reader". Proven on the bundles that ship, not
    only on the fixture -- the two differ by 700 nodes and a bundle schema,
    and an alias that diverged would do so on scale, not on the toy."""
    c = _client(_settings(REAL_CAST_ROOT, tmp_path))
    alias = c.get(f"/api/twins/{imprint}/topology")
    direct = c.get(f"/api/scenarios/{imprint}/topology-export", params={"source": "imprint"})
    assert alias.status_code == direct.status_code == 200, (alias.text, direct.text)
    assert alias.json() == direct.json()


@requires_real_golden
def test_cast_summary_on_the_real_golden_bundle_reports_the_whole_sheet(tmp_path):
    """The Console's header. Pinned against the shipped bundle so a change in
    what the compiler emits shows up here rather than as a blank panel."""
    c = _client(_settings(REAL_CAST_ROOT, tmp_path))
    j = c.get("/api/twins/corpapcn-golden/cast").json()
    assert j["id"] == "corpapcn-golden"
    assert j["twin_config_id"] == "corpapcn-golden"
    assert j["pit_id"] == "pit-03de65ec67-1787823960"
    # `axr-topology/1.5` since the 2026-09-06 recast that added
    # `cast_invocation` (R3). Pinned to the exact string on purpose: the
    # Console must refuse a MAJOR version it does not know rather than
    # render a partial graph (work order §9 seam 1).
    assert j["bundle_schema"] == "axr-topology/1.5"
    assert j["topology_schema_version"] == "1.3"
    assert j["code_fingerprint"]
    assert j["provenance"] == "canonical"
    assert j["provenance_schema"] == "axr-cast-provenance/1"
    assert j["has_provenance"] is True and j["has_topology_export"] is True
    assert j["has_assertions"] is False and j["has_layout"] is False
    assert j["observed_nodes"] == 786 and j["observed_segments"] == 32
    # The gate threshold the Console draws ghosts against.
    assert j["deploy_min_occupancy"] == pytest.approx(0.8)
    assert j["deploy_min_occupancy_source"] in ("declared", "engine default")


@requires_real_golden
@requires_real_sidecarless
@pytest.mark.parametrize(
    "imprint,shape,has_prov",
    [("corpapcn-golden", "canonical", True), ("corpapcn-twin", "absent", False)],
)
def test_the_two_real_bundles_are_the_two_sidecar_states(tmp_path, imprint, shape, has_prov):
    c = _client(_settings(REAL_CAST_ROOT, tmp_path))
    cast = c.get(f"/api/twins/{imprint}/cast").json()
    prov = c.get(f"/api/twins/{imprint}/provenance").json()
    assert cast["provenance"] == prov["provenance"] == shape
    assert cast["has_provenance"] is has_prov
    assert cast["evidence_source"] == prov["evidence_source"]
    if has_prov:
        assert prov["rows"] and prov["population"]
        # S1: the only warning a healthy canonical bundle carries is the
        # confidence-coverage line -- no sidecar-absent, no unknown class.
        assert all("carry no `confidence`" in w for w in prov["warnings"])
    else:
        assert prov["rows"] == []
        assert any("sidecar absent" in w for w in prov["warnings"])


@requires_real_golden
def test_the_real_golden_sidecars_classes_are_all_inside_the_six(tmp_path):
    """The vocabulary check that matters: if aximprint ever emits a seventh
    class, this is where axRange finds out -- as a warning, on the bundle
    that ships."""
    c = _client(_settings(REAL_CAST_ROOT, tmp_path))
    j = c.get("/api/twins/corpapcn-golden/provenance").json()
    seen = {r["evidence_class"] for r in j["rows"]}
    assert seen <= set(_SIX), seen
    assert not any("outside the six-class" in w for w in j["warnings"]), j["warnings"]

    # `compiler` rows carry no confidence BY DEFINITION, and the real sidecar
    # is where that claim is actually testable at scale.
    compilers = [r for r in j["rows"] if r["evidence_class"] == "compiler"]
    assert compilers
    assert all(r["has_confidence"] is False for r in compilers)
    assert all(r["confidence"] == 0.0 for r in compilers)

    # S1, and the correction it came from: `has_confidence: false` is NOT a
    # synonym for "compiler row". 250 of 333 `observed` rows and 1 of 69
    # `imputed` rows carry none too, and the endpoint says so in a warning
    # rather than leaving a consumer to average a 0.0 that means "absent".
    observed = [r for r in j["rows"] if r["evidence_class"] == "observed"]
    assert sum(1 for r in observed if not r["has_confidence"]) == 250
    (coverage,) = [w for w in j["warnings"] if "carry no `confidence`" in w]
    assert "536 of 687" in coverage
    assert "compiler: 285" in coverage
    assert "observed: 250" in coverage
    assert "imputed: 1" in coverage


@requires_real_golden
def test_a_full_edit_round_trip_on_a_COPY_of_the_real_golden_bundle(tmp_path):
    """LES-185's point, end to end: author an assertion against a REAL entity
    key taken from the real bundle's own topology export, save it, read it
    back, re-save the literal read, and confirm the FILE (the cross-repo
    contract) says what was authored.

    The copy is deliberate -- writing into `twins/` would mutate checked-in
    content.
    """
    root = _copy_bundle(tmp_path, "corpapcn-golden", src_root=REAL_CAST_ROOT)
    bundle = root / "corpapcn-golden"

    export = json.loads(_real_artifact(bundle, "corpapcn-golden.topology.json").read_text())
    host = next(
        n["id"] for n in export["nodes"]
        if n.get("locality") == "local" and n.get("segment")
    )
    segment = next(n["segment"] for n in export["nodes"] if n["id"] == host)
    assert host.startswith("address:") and "/" in segment

    doc = {
        "schema": "axr-assertions/2",
        "imprint": "corpapcn-golden",
        "as_of": "2026-09-05T00:00:00Z",
        "source": "operator: tester",
        "add": [],
        "override": [
            {"key": host, "field": "role", "value": "file-server",
             "note": "observed as generic; it serves SMB"},
        ],
        "suppress": [
            {"key": "address:203.0.113.9", "note": "out of scope: a CDN edge"},
        ],
    }
    c = _client(_settings(root, tmp_path))
    put = c.put("/api/twins/corpapcn-golden/assertions", json=doc)
    assert put.status_code == 200, put.text

    got = c.get("/api/twins/corpapcn-golden/assertions").json()
    assert got["override"][0]["key"] == host
    assert got["suppress"][0]["key"] == "address:203.0.113.9"

    again = c.put("/api/twins/corpapcn-golden/assertions", json=got)
    assert again.status_code == 200, again.text
    assert again.json()["etag"] == got["etag"]

    from ruamel.yaml import YAML

    raw = YAML(typ="safe").load(
        (bundle / "corpapcn-golden.assertions.yaml").read_text()
    )
    assert raw["imprint"] == "corpapcn-golden"
    assert raw["override"][0]["key"] == host
    assert raw["override"][0]["field"] == "role"
    # The cast summary now sees the operator file beside the real bundle.
    assert c.get("/api/twins/corpapcn-golden/cast").json()["has_assertions"] is True
    # ... and the CHECKED-IN bundle was never touched.
    assert not (REAL_GOLDEN / "corpapcn-golden.assertions.yaml").exists()
    assert segment  # used above; keeps the key derivation honest


@requires_real_sidecarless
def test_the_real_bundles_have_no_operator_files_and_reading_them_creates_none(tmp_path):
    """A GET must never materialize a file. Asserted against the checked-in
    bundles precisely because a regression here would show up as a dirty
    working tree, which is easy to explain away."""
    c = _client(_settings(REAL_CAST_ROOT, tmp_path))
    for imprint in ("corpapcn-twin",) + (("corpapcn-golden",) if REAL_GOLDEN.is_dir() else ()):
        a = c.get(f"/api/twins/{imprint}/assertions").json()
        lay = c.get(f"/api/twins/{imprint}/layout").json()
        assert a["exists"] is False and a["etag"] is None
        assert lay["exists"] is False and lay["etag"] is None
        assert not (REAL_CAST_ROOT / imprint / f"{imprint}.assertions.yaml").exists()
        assert not (REAL_CAST_ROOT / imprint / "layout.json").exists()


# ---------------------------------------------------------------------------
# The cross-repo file contract (the ONE thing a mocked test can still pin:
# what axRange WRITES, checked against the reader's documented key sets).
# ---------------------------------------------------------------------------
#
# These sets are transcribed from aximprint's `operator_assertions.py`
# (`_parse_suppress`/`_parse_override`/`_parse_add`) rather than imported --
# the no-import-in-either-direction rule (`docs/design/aximprint-integration/
# WORK_ORDERS.md` §0) means the contract has to be RESTATED on this side, and
# a restated contract is exactly the kind that drifts unnoticed.
_AXIMPRINT_SUPPRESS_KEYS = {"key", "note"}
_AXIMPRINT_OVERRIDE_KEYS = {"key", "field", "value", "note"}
_AXIMPRINT_ADD_KEYS = {
    "conduit": {"kind", "key", "note"},
    "host": {"kind", "key", "note", "dial", "listen", "image", "resources",
             "role", "segment"},
    "segment": {"kind", "key", "note", "name", "zone_type"},
}


# R1/F1 (was an xfail, proven live against the real compiler): the file
# carries NO `kind` outside `add`. aximprint's `_parse_override`/
# `_parse_suppress` are fail-closed on their key sets and answered the
# injected discriminator with `suppress[0]: unknown key(s) ['kind']`,
# exit 2 -- every file this API wrote with a non-empty override or
# suppress block was rejected by the compiler its own apply invokes.
def test_written_records_carry_only_keys_the_compiler_accepts(tmp_path):
    from ruamel.yaml import YAML

    root = _copy_bundle(tmp_path, "mini-cast-prov")
    c = _client(_settings(root, tmp_path))
    assert c.put(
        "/api/twins/mini-cast-prov/assertions", json=_VALID_DOC,
    ).status_code == 200
    raw = YAML(typ="safe").load(
        (root / "mini-cast-prov" / "mini-cast-prov.assertions.yaml").read_text()
    )
    for rec in raw["suppress"]:
        assert set(rec) <= _AXIMPRINT_SUPPRESS_KEYS, set(rec)
    for rec in raw["override"]:
        assert set(rec) <= _AXIMPRINT_OVERRIDE_KEYS, set(rec)
    for rec in raw["add"]:
        assert set(rec) <= _AXIMPRINT_ADD_KEYS[rec["kind"]], set(rec)


# R1/F1 (was an xfail): a segment key is parsed with `strict=True`, like
# the compiler's `_cidr` -- `10.80.31.5/24` is not the network address
# and would join against nothing.
def test_a_segment_key_that_is_not_a_network_address_is_refused(tmp_path):
    root = _copy_bundle(tmp_path, "mini-cast-prov")
    c = _client(_settings(root, tmp_path))
    d = _doc()
    d["add"] = [{"kind": "segment", "key": "10.80.31.5/24", "name": "n", "note": "x"}]
    d["override"] = []
    d["suppress"] = []
    r = c.put("/api/twins/mini-cast-prov/assertions", json=d)
    assert r.status_code == 400, r.text


# R1/F1 (was an xfail): the proto set is CLOSED to (icmp, tcp, udp),
# because the `"389/tcp"` strings these become are joined against Zeek's
# own proto column and anything else matches nothing.
def test_a_listen_proto_outside_the_wire_vocabulary_is_refused(tmp_path):
    root = _copy_bundle(tmp_path, "mini-cast-prov")
    c = _client(_settings(root, tmp_path))
    d = _doc()
    d["add"] = [{
        "kind": "host", "key": "address:10.9.0.4", "segment": "10.9.0.0/24",
        "listen": [{"port": 80, "proto": "HTTP"}], "note": "x",
    }]
    d["override"] = []
    d["suppress"] = []
    assert c.put(
        "/api/twins/mini-cast-prov/assertions", json=d,
    ).status_code == 400


# R1/F1 (was an xfail): `mac:` is a host entity key on both sides. This
# was the over-restrictive half of the drift -- not a broken file, but a
# Console that could not author an assertion the compiler would take.
def test_a_mac_host_key_is_accepted(tmp_path):
    root = _copy_bundle(tmp_path, "mini-cast-prov")
    c = _client(_settings(root, tmp_path))
    d = _doc()
    d["add"] = []
    d["override"] = []
    d["suppress"] = [{"key": "mac:00:11:22:33:44:55", "note": "a printer"}]
    assert c.put(
        "/api/twins/mini-cast-prov/assertions", json=d,
    ).status_code == 200


# R1/F1 (was an xfail): `zone_type`, `resources` keys, and conduit keys
# (canonicalized by sorting the halves; a self-conduit refused).
def test_the_remaining_closed_vocabularies_match_the_compilers(tmp_path):
    root = _copy_bundle(tmp_path, "mini-cast-prov")
    c = _client(_settings(root, tmp_path))

    def _put(add):
        return c.put(
            "/api/twins/mini-cast-prov/assertions",
            json=dict(_doc(), add=add, override=[], suppress=[]),
        )

    assert _put([{"kind": "segment", "key": "10.9.0.0/24", "name": "n",
                  "zone_type": "overlay", "note": "n"}]).status_code == 400
    assert _put([{"kind": "host", "key": "address:10.9.0.4",
                  "segment": "10.9.0.0/24", "resources": {"gpu": 1},
                  "note": "n"}]).status_code == 400
    assert _put([{"kind": "conduit", "key": "10.1.0.0/24|10.1.0.0/24",
                  "note": "n"}]).status_code == 400
    # ... and the canonical (sorted) spelling is what lands on disk.
    assert _put([{"kind": "conduit", "key": "10.2.0.0/24|10.1.0.0/24",
                  "note": "n"}]).status_code == 200
    got = c.get("/api/twins/mini-cast-prov/assertions").json()
    assert got["add"][0]["key"] == "10.1.0.0/24|10.2.0.0/24"


# ===========================================================================
# The 2026-09-06 fix round: R1-R4, M1-M5, the S-items. Everything below pins
# a behaviour a RULING fixed, and each one is a bug that was reachable over
# HTTP before it -- not a hypothetical.
# ===========================================================================

# ---------------------------------------------------------------------------
# M1 -- the catalog-id guard is POSITIVE, on every route including the writers
# ---------------------------------------------------------------------------
_WRITE_ROUTES = (
    ("put", "assertions", {"json": _VALID_DOC}),
    ("put", "layout", {"json": _LAYOUT}),
    ("post", "apply", {"json": {"document": _VALID_DOC}}),
)


@pytest.mark.parametrize("bad", ["%2e", "%2E", "%2e%2e", "%2e%2e%2f", "..", "."])
@pytest.mark.parametrize("verb,route,kw", _WRITE_ROUTES, ids=[r[1] for r in _WRITE_ROUTES])
def test_write_routes_refuse_a_dot_shaped_id(tmp_path, bad, verb, route, kw):
    """M1, and it was reachable: the old guard rejected `/`, `\\` and `..`
    but accepted a lone `.`, which arrives percent-encoded as `%2e` and
    resolves to the catalog ROOT -- `PUT /api/twins/%2e/layout` wrote
    `layout.json` into it. A deny-list of the traversal spellings somebody
    thought of is not an allow-list of the ids that are legal."""
    root = _copy_bundle(tmp_path, "mini-cast-prov")
    c = _client(_apply_settings(root, tmp_path), backend=_FakeRecompile())
    r = getattr(c, verb)(f"/api/twins/{bad}/{route}", **kw)
    # 404 from the guard, or 404/405 from the router for a spelling that URL
    # normalization collapses before it ever arrives (which is exactly why
    # the PERCENT-ENCODED `%2e` was the one nobody thought of). Never a 2xx,
    # and -- the assertion that matters -- never a byte written.
    assert r.status_code in (404, 405), (bad, route, r.status_code, r.text)
    # ... and nothing was written anywhere in the catalog.
    assert sorted(p.name for p in root.iterdir()) == ["mini-cast-prov"]
    assert not (root / "layout.json").exists()
    assert not (root / "mini-cast-prov" / "layout.json").exists()


@pytest.mark.parametrize(
    "value,ok",
    [
        ("mini-cast-prov", True), ("a", True), ("a.b_c-d", True),
        (".", False), ("..", False), (".hidden", False), ("", False),
        ("a/b", False), ("a\\b", False), ("../x", False), ("a b", False),
    ],
)
def test_the_catalog_id_predicate_is_shared_by_both_callers(value, ok):
    """One predicate, two callers (`imprints.resolve_imprint_dir` and
    `service._is_safe_catalog_ref`) -- two guards on one question is how the
    `%2e` case got through the one nobody updated."""
    from axrange.api.imprints import is_safe_catalog_id
    from axrange.api.service import _is_safe_catalog_ref

    assert is_safe_catalog_id(value) is ok
    assert _is_safe_catalog_ref(value) is ok


# ---------------------------------------------------------------------------
# M2 -- two concurrent PUTs are one 200 and one refusal, never a 500
# ---------------------------------------------------------------------------
def test_two_genuinely_concurrent_puts_yield_one_200_and_one_refusal(tmp_path):
    """M2, the real race: the routes are sync `def`, so Starlette runs them
    in a threadpool and two PUTs interleave for real.

    Before the fix, both read "no file", both passed the etag guard, and both
    wrote -- one silently winning against the last-writer-REFUSED promise --
    while the PID-keyed temp name they shared made one of them lose its file
    entirely (`FileNotFoundError` -> 500). Read + guard + write is now one
    critical section per file."""
    import threading

    root = _copy_bundle(tmp_path, "mini-cast-prov")
    apath = root / "mini-cast-prov" / "mini-cast-prov.assertions.yaml"
    barrier = threading.Barrier(2, timeout=30)
    results: list[int] = []
    lock = threading.Lock()

    # Both apps are built BEFORE the race, the way a running server is: one
    # `create_app`, then concurrent requests. Building them inside the
    # threads would race pydantic's own schema construction, which is a
    # different (and pre-existing) thing from the race under test.
    clients = [_client(_settings(root, tmp_path)) for _ in range(2)]

    def _put(i: int) -> None:
        barrier.wait()
        r = clients[i].put(
            "/api/twins/mini-cast-prov/assertions",
            json=_doc(source=f"operator: writer-{i}"),
        )
        with lock:
            results.append(r.status_code)

    threads = [
        threading.Thread(target=_put, args=(i,), daemon=True) for i in range(2)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert len(results) == 2, results
    assert 500 not in results, results
    assert sorted(results) == [200, 412], results
    # Exactly one document survived, and it is a complete one.
    assert apath.is_file()
    from ruamel.yaml import YAML

    raw = YAML(typ="safe").load(apath.read_text())
    assert raw["schema"] == "axr-assertions/2"
    assert [p.name for p in (root / "mini-cast-prov").iterdir() if ".tmp" in p.name] == []


def test_the_temp_name_is_unique_across_threads_not_per_pid(tmp_path):
    """The narrower half of M2, pinned on its own: `_atomic_write` must not
    derive its temp name from the PID -- two threads in one process share
    one, so the old name collided under exactly the concurrency above."""
    import threading

    from axrange.api import console_files as cf

    target = tmp_path / "doc.yaml"
    seen: list[str] = []
    real = cf.tempfile.mkstemp
    lock = threading.Lock()

    def _spy(**kw):
        fd, name = real(**kw)
        with lock:
            seen.append(name)
        return fd, name

    cf.tempfile.mkstemp = _spy
    try:
        threads = [
            threading.Thread(target=cf._atomic_write, args=(target, f"x{i}\n"))
            for i in range(8)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
    finally:
        cf.tempfile.mkstemp = real
    assert len(seen) == 8
    assert len(set(seen)) == 8, seen


# ---------------------------------------------------------------------------
# M3 -- design 14 §3's own example block, hand-authored, unquoted timestamp
# ---------------------------------------------------------------------------
# Copied from `docs/design/14-operator-assertions.md` §3's worked example,
# with ONE deviation, marked: the design's example omits `note` on its host
# `add` and on its `override`, and aximprint's `_require_note` -- the reader
# of record -- requires one on EVERY record in all three blocks. axRange
# therefore keeps requiring it and the design example is what needs fixing
# (reported to the orchestrator rather than silently relaxed here).
_DESIGN_EXAMPLE = """\
schema: axr-assertions/2
imprint: mini-cast-prov
as_of: 2026-09-04T18:00:00Z          # the snapshot's time bound
source: "operator: <name>"           # carried into every fact's `source`
add:
  - kind: segment
    key: "10.80.31.0/24"             # stable keys: a CIDR, address:<ip>, hostname:<name>
    name: seg-10-80-31
    zone_type: vlan
    note: "pruned by the cast (seen in 2 of 11 windows); it is the OT lab"
  - kind: host
    key: "address:10.80.20.5"
    segment: "10.80.20.0/24"        # an address alone names no segment
    role: domain-controller
    listen: [{port: 88, proto: tcp}, {port: 389, proto: tcp}, {port: 445, proto: tcp}]
    note: "the DC the vantage never saw answer"   # DEVIATION: the design omits this
  - kind: conduit
    key: "10.80.20.0/24|10.80.31.0/24"
    note: "the OT lab reaches the DC"
override:
  - key: "address:10.80.20.9"
    field: role
    value: domain-controller          # the store keeps the overridden `imputed: dns-resolver`
    note: "imputed dns-resolver is wrong"         # DEVIATION: the design omits this
suppress:
  - key: "address:203.0.113.9"
    note: "out of scope: a cloud CDN edge"
"""


def test_the_design_notes_own_example_file_reads_back(tmp_path):
    """M3: a hand-authored `as_of: 2026-09-04T18:00:00Z` -- UNQUOTED, exactly
    as the design note tells operators to write it -- is resolved to a
    `datetime` by ruamel (and by PyYAML on the compiler's side) before any
    reader sees a string, so an `isinstance(str)` check refused the one
    spelling the design documents.

    The whole example block is the fixture on purpose: a hand-written
    approximation would have kept passing (LES-185's argument, applied to a
    document instead of a bundle)."""
    root = _copy_bundle(tmp_path, "mini-cast-prov")
    path = root / "mini-cast-prov" / "mini-cast-prov.assertions.yaml"
    path.write_text(_DESIGN_EXAMPLE)

    c = _client(_settings(root, tmp_path))
    r = c.get("/api/twins/mini-cast-prov/assertions")
    assert r.status_code == 200, r.text
    j = r.json()
    # Coerced to the ONE ISO-8601 `Z` spelling the compiler canonicalizes to.
    assert j["as_of"] == "2026-09-04T18:00:00Z"
    assert len(j["add"]) == 3 and len(j["override"]) == 1 and len(j["suppress"]) == 1
    assert j["add"][2]["key"] == "10.80.20.0/24|10.80.31.0/24"
    # ... and the read payload is re-PUT-able, which is the round trip that
    # matters (the etag comes from the hand-authored bytes).
    assert c.put(
        "/api/twins/mini-cast-prov/assertions", json=j,
        headers={"If-Match": f'"{j["etag"]}"'},
    ).status_code == 200


@pytest.mark.parametrize(
    "authored,expect",
    [
        ("2026-09-04T18:00:00Z", "2026-09-04T18:00:00Z"),
        ('"2026-09-04T18:00:00Z"', "2026-09-04T18:00:00Z"),
        ("2026-09-04T18:00:00+02:00", "2026-09-04T16:00:00Z"),
        ("2026-09-04T18:00:00", "2026-09-04T18:00:00Z"),   # naive is UTC, said so
        ("2026-09-04", "2026-09-04T00:00:00Z"),            # ruamel yields a date
    ],
)
def test_every_as_of_spelling_a_yaml_loader_can_produce_is_accepted(
    tmp_path, authored, expect,
):
    """M3 generalized: a YAML loader retypes an unquoted timestamp to
    `datetime` and a bare date to `date`, so a reader that string-checks
    refuses documents its own writer would accept back."""
    root = _copy_bundle(tmp_path, "mini-cast-prov")
    path = root / "mini-cast-prov" / "mini-cast-prov.assertions.yaml"
    path.write_text(
        "schema: axr-assertions/2\n"
        "imprint: mini-cast-prov\n"
        f"as_of: {authored}\n"
        'source: "operator: tester"\n'
    )
    c = _client(_settings(root, tmp_path))
    r = c.get("/api/twins/mini-cast-prov/assertions")
    assert r.status_code == 200, r.text
    assert r.json()["as_of"] == expect


def test_an_as_of_that_is_not_a_timestamp_is_refused(tmp_path):
    root = _copy_bundle(tmp_path, "mini-cast-prov")
    c = _client(_settings(root, tmp_path))
    r = c.put(
        "/api/twins/mini-cast-prov/assertions", json=_doc(as_of="last tuesday"),
    )
    assert r.status_code == 400
    assert "ISO-8601" in r.json()["error"]


# ---------------------------------------------------------------------------
# R2 -- the vocabulary is an artifact, and the drift detector is a file
# ---------------------------------------------------------------------------
#: N5: resolved, not hardcoded. `AXIMPRINT_REPO` in the environment wins;
#: otherwise the sibling checkout beside this repo, which is the layout both
#: repos are developed in. Every skip names the path it looked at, so "the
#: detector is not armed" never reads as "the detector passed".
AXIMPRINT_REPO = Path(
    os.environ.get("AXIMPRINT_REPO") or (REPO_ROOT.parent / "aximprint-axrange")
)


def _published_schema() -> Path:
    from axrange.api.console_files import AXIMPRINT_SCHEMA_RELPATH

    return AXIMPRINT_REPO / AXIMPRINT_SCHEMA_RELPATH


def _vocabulary_of(path: Path) -> dict:
    """Every closed set the file declares, extracted the way
    `console_files` extracts them. This -- not the bytes -- is what must
    never silently differ between the two repos."""
    import json as _json

    doc = _json.loads(path.read_text())
    defs = doc["$defs"]
    by_ref = {"#/$defs/hostKey": "host", "#/$defs/cidr": "segment"}
    return {
        "schema": doc["properties"]["schema"]["const"],
        "doc_keys": sorted(doc["properties"]),
        "doc_required": sorted(doc["required"]),
        "source_pattern": doc["properties"]["source"]["pattern"],
        "host_key_pattern": defs["hostKey"]["pattern"],
        "add_kinds": sorted(
            b["properties"]["kind"]["const"] for b in defs["addRecord"]["oneOf"]
        ),
        "add_fields": {
            b["properties"]["kind"]["const"]: sorted(
                k for k in b["properties"] if k not in ("kind", "key", "note")
            )
            for b in defs["addRecord"]["oneOf"]
        },
        "add_required": {
            b["properties"]["kind"]["const"]: sorted(b["required"])
            for b in defs["addRecord"]["oneOf"]
        },
        "override_fields": {
            by_ref[b["properties"]["key"]["$ref"]]: sorted(b["properties"]["field"]["enum"])
            for b in defs["overrideRecord"]["oneOf"]
        },
        "override_keys": {
            by_ref[b["properties"]["key"]["$ref"]]: sorted(b["properties"])
            for b in defs["overrideRecord"]["oneOf"]
        },
        "override_required": {
            by_ref[b["properties"]["key"]["$ref"]]: sorted(b["required"])
            for b in defs["overrideRecord"]["oneOf"]
        },
        "suppress_keys": sorted(defs["suppressRecord"]["properties"]),
        "suppress_required": sorted(defs["suppressRecord"]["required"]),
        "zone_type": defs["zoneType"]["enum"],
        "proto": defs["portList"]["items"]["properties"]["proto"]["enum"],
        "port_keys": sorted(defs["portList"]["items"]["properties"]),
        "resources": sorted(defs["resources"]["propertyNames"]["enum"]),
        "evidence_class": defs["evidenceClass"]["enum"],
    }


def test_the_vendored_schemas_VOCABULARY_matches_aximprints_published_copy():
    """R2's whole point, and the assertion that must never be soft: the
    closed sets are a FILE, and the file says the same thing in both repos.

    HARD -- a differing set fails, because every one of the six vocabulary
    bugs the live run found was a hand-restated set that had silently
    diverged and was only discovered when the compiler refused a written
    file. Skips only while aximprint has not published its copy at all."""
    from axrange.api.console_files import SCHEMA_PATH

    published = _published_schema()
    if not published.is_file():
        pytest.skip(
            f"aximprint has not published {published} yet (looked under "
            f"{AXIMPRINT_REPO}; set AXIMPRINT_REPO to point elsewhere) -- the "
            f"vendored copy at {SCHEMA_PATH} is the contract until it does, "
            "and this drift detector arms itself the moment that file lands"
        )
    assert _vocabulary_of(SCHEMA_PATH) == _vocabulary_of(published), (
        f"{SCHEMA_PATH} and {published} declare DIFFERENT closed sets -- the "
        "axr-assertions/2 vocabulary is ONE artifact; re-vendor aximprint's "
        "canonical file (it declares itself canonical: edit it there and copy, "
        "never the other way round)"
    )


def test_the_vendored_schema_is_byte_identical_to_aximprints_published_copy():
    """The stronger property, held SOFT on purpose while the other repo's
    slice is in flight.

    aximprint's file declares itself canonical and axRange vendors it
    verbatim, so the two should be the same bytes. But prose and formatting
    move in that repo independently of the vocabulary, and a red suite for a
    reworded `$comment` teaches people to ignore this check -- which is
    exactly what would then hide a real divergence. So: a byte difference
    with an IDENTICAL vocabulary skips with both digests and a re-vendor
    instruction; a vocabulary difference is the hard failure above."""
    import hashlib as _hashlib

    from axrange.api.console_files import SCHEMA_PATH

    published = _published_schema()
    if not published.is_file():
        pytest.skip(
            f"aximprint has not published {published} yet (looked under "
            f"{AXIMPRINT_REPO}; set AXIMPRINT_REPO to point elsewhere)"
        )
    mine, theirs = SCHEMA_PATH.read_bytes(), published.read_bytes()
    if mine == theirs:
        return
    assert _vocabulary_of(SCHEMA_PATH) == _vocabulary_of(published), (
        "vocabulary drift -- see the hard test above"
    )
    pytest.skip(
        "the vendored copy and aximprint's published one differ in prose or "
        f"formatting only (vendored md5 {_hashlib.md5(mine).hexdigest()}, "
        f"published md5 {_hashlib.md5(theirs).hexdigest()}); the VOCABULARY is "
        f"identical. Re-vendor with: cp {published} {SCHEMA_PATH}"
    )


def test_every_closed_set_is_derived_from_the_schema_not_restated():
    """The sets `console_files` enforces ARE the schema's, read at import.

    Asserted against the schema DOCUMENT rather than against literals, so a
    set that stops being derived -- a hand-written constant creeping back in
    -- fails here instead of at a live round trip against the compiler,
    which is where all six of the original drifts were found."""
    import json as _json

    from axrange.api import console_files as cf

    doc = _json.loads(cf.SCHEMA_PATH.read_text())
    defs = doc["$defs"]

    assert cf.ASSERTIONS_SCHEMA == doc["properties"]["schema"]["const"]
    assert cf._DOC_KEYS == tuple(doc["properties"])
    assert cf.ADD_KINDS == ("conduit", "host", "segment")
    assert set(cf.ADD_KINDS) == {
        b["properties"]["kind"]["const"] for b in defs["addRecord"]["oneOf"]
    }
    for branch in defs["addRecord"]["oneOf"]:
        kind = branch["properties"]["kind"]["const"]
        assert cf.ADD_FIELDS[kind] == tuple(sorted(
            k for k in branch["properties"] if k not in ("kind", "key", "note")
        ))
    assert cf.ZONE_TYPES == tuple(defs["zoneType"]["enum"])
    assert cf.PORT_PROTOS == tuple(
        defs["portList"]["items"]["properties"]["proto"]["enum"]
    )
    assert cf._PORT_ENTRY_KEYS == tuple(defs["portList"]["items"]["properties"])
    assert cf.RESOURCE_KEYS == tuple(sorted(defs["resources"]["propertyNames"]["enum"]))
    assert cf._SUPPRESS_KEYS == tuple(defs["suppressRecord"]["properties"])

    by_ref = {"#/$defs/hostKey": "host", "#/$defs/cidr": "segment"}
    for branch in defs["overrideRecord"]["oneOf"]:
        kind = by_ref[branch["properties"]["key"]["$ref"]]
        assert cf.OVERRIDE_FIELDS[kind] == tuple(branch["properties"]["field"]["enum"])

    # `route` is refused BY NAME, and the schema carries the reason as prose
    # rather than as data -- so what IS checkable is that it is not among the
    # kinds the schema accepts.
    assert tuple(cf.REFUSED_ADD_KINDS) == ("route",)
    assert "route" not in cf.ADD_KINDS


def test_the_three_axrange_only_rules_are_declared_as_such():
    """The schema is aximprint's file and states the CONTRACT; these three
    are this endpoint's own policy on top of it, and each is in the code
    rather than in the shared artifact because putting axRange-only rules
    into a file the other repo owns is how a shared artifact stops being
    shared.

    None can produce a file the compiler refuses, which is R1's test."""
    import json as _json

    from axrange.api import console_files as cf

    doc = _json.loads(cf.SCHEMA_PATH.read_text())

    # (1) `imprint` is optional to the compiler, required here.
    assert "imprint" not in doc["required"]
    assert "imprint" in cf._DOC_REQUIRED
    assert set(doc["required"]) < set(cf._DOC_REQUIRED)

    # (2) the compiler requires only kind/key/note on an add; axRange also
    #     requires the field that makes the record resolvable at compile time.
    for branch in doc["$defs"]["addRecord"]["oneOf"]:
        kind = branch["properties"]["kind"]["const"]
        assert set(branch["required"]) == {"kind", "key", "note"}
        assert set(cf.ADD_REQUIRED[kind]) <= set(cf.ADD_FIELDS[kind])

    # (3) the source prefix literal satisfies the schema's own pattern.
    assert cf._SOURCE_RE.match(cf.SOURCE_PREFIX + "name")
    assert cf._SOURCE_RE.match(cf.SOURCE_PREFIX + " name")


def test_the_host_key_prefixes_are_read_out_of_the_schemas_own_pattern():
    """The one closed set the canonical schema expresses ONLY as a regex, so
    it is extracted from the alternation rather than restated -- and every
    extracted prefix must still match the pattern it came from."""
    from axrange.api import console_files as cf

    assert cf.HOST_KEY_PREFIXES == ("address:", "hostname:", "mac:")
    for prefix in cf.HOST_KEY_PREFIXES:
        assert cf._HOST_KEY_RE.match(prefix + "value"), prefix
    for bad in ("mmac:x", "ip:1.2.3.4", "10.0.0.0/24", "address:"):
        assert not cf._HOST_KEY_RE.match(bad), bad


def test_the_schema_is_a_wellformed_draft_2020_12_document():
    """It is a real JSON Schema, not a config file wearing one's name: the
    `$ref`s resolve and every `$def` it points at exists."""
    import json as _json

    from axrange.api.console_files import SCHEMA_PATH

    doc = _json.loads(SCHEMA_PATH.read_text())
    assert doc["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    defs = doc["$defs"]

    def _refs(node):
        if isinstance(node, dict):
            if isinstance(node.get("$ref"), str):
                yield node["$ref"]
            for v in node.values():
                yield from _refs(v)
        elif isinstance(node, list):
            for v in node:
                yield from _refs(v)

    for ref in _refs(doc):
        assert ref.startswith("#/$defs/"), ref
        assert ref[len("#/$defs/"):] in defs, ref


# ---------------------------------------------------------------------------
# R1 -- the remaining vocabulary parity the live run did not reach
# ---------------------------------------------------------------------------
def test_a_source_that_is_not_operator_prefixed_is_REFUSED_not_warned(tmp_path):
    """R1: the compiler is fail-closed on `source`, so a warning here would
    have written a file the recompile then rejects with exit 2 -- a save that
    looks like it worked and fails minutes later inside a subprocess."""
    root = _copy_bundle(tmp_path, "mini-cast-prov")
    c = _client(_settings(root, tmp_path))
    for bad in ("raghu", "operator:", "operator:   ", "author: raghu"):
        r = c.put("/api/twins/mini-cast-prov/assertions", json=_doc(source=bad))
        assert r.status_code == 400, (bad, r.text)
        assert "operator:" in r.json()["error"]
    assert not (root / "mini-cast-prov" / "mini-cast-prov.assertions.yaml").exists()


@pytest.mark.parametrize("spelling", ["operator: tester", "operator:tester"])
def test_both_source_spellings_are_accepted_and_kept_as_authored(tmp_path, spelling):
    """The compiler canonicalizes internally and accepts either, so
    reformatting a string the operator typed would churn the file for
    nothing (and break the no-op-save-is-byte-identical rule)."""
    from ruamel.yaml import YAML

    root = _copy_bundle(tmp_path, "mini-cast-prov")
    c = _client(_settings(root, tmp_path))
    assert c.put(
        "/api/twins/mini-cast-prov/assertions", json=_doc(source=spelling),
    ).status_code == 200
    raw = YAML(typ="safe").load(
        (root / "mini-cast-prov" / "mini-cast-prov.assertions.yaml").read_text()
    )
    assert raw["source"] == spelling


def test_a_segment_key_is_normalized_to_its_strict_network_form(tmp_path):
    """`10.80.31.0/24` is already canonical; a host-bit-bearing key is
    refused by name AND told the network form it meant (S12)."""
    root = _copy_bundle(tmp_path, "mini-cast-prov")
    c = _client(_settings(root, tmp_path))
    d = _doc(add=[{"kind": "segment", "key": "10.9.0.5/24", "name": "n", "note": "x"}],
             override=[], suppress=[])
    r = c.put("/api/twins/mini-cast-prov/assertions", json=d)
    assert r.status_code == 400
    assert "10.9.0.0/24" in r.json()["error"]      # names the form that works


def test_an_address_key_that_is_not_an_ip_is_WARNED_about_not_refused(tmp_path):
    """S12, resolved against R1: the compiler does NOT parse the value part
    of a host key, so refusing `address:not-an-ip` would refuse a document
    the reader of record accepts -- an over-restrictive writer, which is the
    same bug class as the `mac:` one. Warn instead: the operator is told it
    will join against nothing, and the file stays writable."""
    root = _copy_bundle(tmp_path, "mini-cast-prov")
    c = _client(_settings(root, tmp_path))
    d = _doc(add=[], override=[],
             suppress=[{"key": "address:dc01.corp.local", "note": "x"}])
    r = c.put("/api/twins/mini-cast-prov/assertions", json=d)
    assert r.status_code == 200, r.text
    assert any("join against nothing" in w for w in r.json()["warnings"])
    # ... and a real address warns about nothing.
    d2 = _doc(add=[], override=[], suppress=[{"key": "address:10.0.0.9", "note": "x"}],
              etag=r.json()["etag"])
    r2 = c.put("/api/twins/mini-cast-prov/assertions", json=d2)
    assert r2.status_code == 200 and r2.json()["warnings"] == []


@pytest.mark.parametrize("block", ["override", "suppress"])
def test_a_misdeclared_kind_is_refused_in_BOTH_blocks(tmp_path, block):
    """S13: `_validate_suppress` silently REWROTE a mis-declared `kind` where
    `_validate_override` refused it -- two blocks with two answers to one
    question. Both refuse now. (Moot on disk after R1 strips `kind`
    entirely, but the API body can still carry one as a discriminator, and a
    wrong one means the SPA and the server disagree about what the record
    is.)"""
    root = _copy_bundle(tmp_path, "mini-cast-prov")
    c = _client(_settings(root, tmp_path))
    rec = {"kind": "segment", "key": "address:10.0.0.9", "note": "x"}
    if block == "override":
        rec.update({"field": "role", "value": "web"})
    d = _doc(add=[], override=[], suppress=[])
    d[block] = [rec]
    r = c.put("/api/twins/mini-cast-prov/assertions", json=d)
    assert r.status_code == 400, r.text
    assert "but key" in r.json()["error"]


def test_the_written_file_keeps_the_canonical_key_order(tmp_path):
    """S11: the dump must not alphabetize, or the file stops diffing like the
    design's own example (`kind` sorted after `image`). The compiler sets
    `sort_keys=False` for the same reason."""
    root = _copy_bundle(tmp_path, "mini-cast-prov")
    c = _client(_settings(root, tmp_path))
    assert c.put(
        "/api/twins/mini-cast-prov/assertions", json=_VALID_DOC,
    ).status_code == 200
    text = (root / "mini-cast-prov" / "mini-cast-prov.assertions.yaml").read_text()
    assert text.startswith("schema: axr-assertions/2\n")
    assert text.index("imprint:") < text.index("as_of:") < text.index("source:")
    # Within a record: kind, key, the content fields sorted, then note last.
    host = text[text.index("- kind: host"):]
    assert host.index("key:") < host.index("listen:") < host.index("role:")
    assert host.index("role:") < host.index("segment:") < host.index("note:")


# ---------------------------------------------------------------------------
# S6 -- the diff covers conduits and the twin-config side
# ---------------------------------------------------------------------------
def test_the_diff_reports_conduits(tmp_path):
    """S1.7 names segments, nodes AND conduits; conduits are one of the three
    `add` kinds an operator can author, and adding one produced an empty
    diff because the snapshot did not carry them at all."""
    from axrange.api import recompile as rc

    root = _copy_bundle(tmp_path, "mini-cast-prov")
    bundle = root / "mini-cast-prov"
    c = _client(_apply_settings(root, tmp_path), backend=_FakeRecompile())
    fp = c.post(
        "/api/twins/mini-cast-prov/apply", json={"document": _VALID_DOC},
    ).json()["fingerprint_before"]

    snap_path = rc.history_dir(bundle) / f"{fp}.json"
    snap = json.loads(snap_path.read_text())
    assert "conduits" in snap["twin"], "the snapshot must carry conduits at all"
    snap["twin"]["conduits"]["10.99.0.0/24|10.99.1.0/24"] = {
        "from": "gone", "to": "also-gone",
    }
    snap_path.write_text(json.dumps(snap))

    j = c.get("/api/twins/mini-cast-prov/diff", params={"since": fp}).json()
    assert "10.99.0.0/24|10.99.1.0/24" in j["twin"]["conduits"]["removed"]
    assert j["twin"]["conduits"]["added"] == []


def test_the_diff_publishes_a_twin_config_change_the_topology_cannot_show(tmp_path):
    """S6: an edit that changes an image, a size or a role moves the twin
    config and not one observed node, so a topology-only diff answered
    "nothing changed" for a real change. The flag says otherwise, and a
    warning explains why there is nothing to highlight."""
    from axrange.api import recompile as rc

    root = _copy_bundle(tmp_path, "mini-cast-prov")
    bundle = root / "mini-cast-prov"
    c = _client(_apply_settings(root, tmp_path), backend=_FakeRecompile())
    fp = c.post(
        "/api/twins/mini-cast-prov/apply", json={"document": _VALID_DOC},
    ).json()["fingerprint_before"]

    unchanged = c.get("/api/twins/mini-cast-prov/diff", params={"since": fp}).json()
    assert unchanged["twin_config_changed"] is False

    # Move the twin config without touching a single observed entity.
    tc = bundle / "mini-cast-prov.yaml"
    tc.write_text(tc.read_text() + "\n# an operator changed an image\n")
    j = c.get("/api/twins/mini-cast-prov/diff", params={"since": fp}).json()
    assert j["twin_config_changed"] is True
    assert j["observed"]["nodes"] == {"added": [], "removed": [], "changed": []}
    assert j["twin"]["hosts"] == {"added": [], "removed": [], "changed": []}
    warned = " ".join(j["warnings"])
    assert "twin config changed" in warned
    # G2: it must NOT claim to know which KIND of change it was.
    assert "rather than the population" not in warned


# ---------------------------------------------------------------------------
# S8 / S9 -- one etag helper, and never handing back a stale one
# ---------------------------------------------------------------------------
def test_apply_refuses_disagreeing_etags_like_the_two_PUTs_do(tmp_path):
    """S8: apply silently PREFERRED the body when `If-Match` disagreed, while
    both PUTs refused -- the same request was guarded one way through one
    route and another way through the other. One helper now."""
    root = _copy_bundle(tmp_path, "mini-cast-prov")
    fake = _FakeRecompile()
    c = _client(_apply_settings(root, tmp_path), backend=fake)
    etag = c.put("/api/twins/mini-cast-prov/assertions", json=_VALID_DOC).json()["etag"]
    r = c.post(
        "/api/twins/mini-cast-prov/apply",
        json={"document": _doc(source="operator: second"), "etag": etag},
        headers={"If-Match": '"' + "0" * 64 + '"'},
    )
    assert r.status_code == 400
    assert "disagree" in r.json()["error"]
    assert fake.calls == []


@pytest.mark.parametrize(
    "raise_,status",
    [
        ("shape", 422),
        ("failed", 502),
        ("unavailable", 503),
    ],
)
def test_every_post_save_failure_hands_back_the_saved_etag(tmp_path, raise_, status):
    """S9: the save landed, so the file on disk MOVED. A failure that does
    not return the new etag leaves the SPA holding a stale one and refused on
    its next write, with a reload as its only recovery."""
    from axrange.api import recompile as rc

    boom = {
        "shape": rc.RecompileShapeError(
            "assertions: add[1]: unknown field 'colour'",
            result=rc.RecompileResult(argv=["fake"], returncode=2, stderr="nope"),
        ),
        "failed": rc.RecompileFailedError(
            "recompile failed (exit 1)",
            result=rc.RecompileResult(argv=["fake"], returncode=1, stderr="nope"),
        ),
        "unavailable": rc.RecompileUnavailableError("cannot run 'aximprint'"),
    }[raise_]

    root = _copy_bundle(tmp_path, "mini-cast-prov")
    c = _client(_apply_settings(root, tmp_path), backend=_FakeRecompile(raise_=boom))
    r = c.post("/api/twins/mini-cast-prov/apply", json={"document": _VALID_DOC})
    assert r.status_code == status, r.text
    etag = r.json()["detail"]["etag"]
    assert etag
    # ... and it is the etag a follow-up write is actually guarded by.
    assert c.put(
        "/api/twins/mini-cast-prov/assertions",
        json=_doc(source="operator: retry", etag=etag),
    ).status_code == 200


# ---------------------------------------------------------------------------
# S15 -- the timeout, and the proxy note that comes with a synchronous apply
# ---------------------------------------------------------------------------
def test_the_default_recompile_timeout_is_two_minutes():
    """S15: the real cast is 0.74 s for 786 nodes; 900 s was a number nobody
    had timed, and it bounds how long a hung compiler holds the per-imprint
    apply lock. The settings default and the module default are ONE value."""
    from axrange.api.recompile import DEFAULT_TIMEOUT_S
    from axrange.api.settings import ApiSettings

    assert DEFAULT_TIMEOUT_S == 120.0
    assert ApiSettings(db_path="x").aximprint_timeout_s == DEFAULT_TIMEOUT_S


def test_the_setting_key_strings_are_spelled_once(monkeypatch):
    """The refusal names these keys, so a second literal is a second place
    for the name in the message to stop matching the name in the
    environment (LES-105)."""
    from axrange.api import recompile as rc
    from axrange.api.settings import ApiSettings

    monkeypatch.setenv(rc.SETTING_BIN, "/opt/aximprint/bin/aximprint")
    monkeypatch.setenv(rc.SETTING_STORE, "/store/ir.duckdb")
    monkeypatch.setenv(rc.SETTING_TIMEOUT, "45")
    s = ApiSettings(db_path="x")
    assert s.aximprint_bin == "/opt/aximprint/bin/aximprint"
    assert s.aximprint_store == "/store/ir.duckdb"
    assert s.aximprint_timeout_s == 45.0


# ---------------------------------------------------------------------------
# R3 -- the cast_invocation block is VALIDATED before it becomes an argv
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "block,expect",
    [
        ({"pit_id": "pit-1; rm -rf /"}, "plain token"),
        ({"pit_id": 17}, "plain token"),
        ({"pit_id": "pit-1", "relocate": "not-a-cidr"}, "not a CIDR"),
        ({"pit_id": "pit-1", "max_count": 0}, "positive integer"),
        ({"pit_id": "pit-1", "max_count": "12"}, "positive integer"),
        ({"pit_id": "pit-1", "chatter": "yes"}, "boolean"),
    ],
)
def test_a_malformed_cast_invocation_is_refused_before_it_becomes_an_argv(
    tmp_path, block, expect,
):
    """The bundle is a file another repo wrote, and "trusted producer" is not
    the same as "validated input" -- a `subprocess.run` argument list is the
    wrong place to discover a shell-shaped pit id."""
    from axrange.api import recompile as rc

    root = _copy_bundle(tmp_path, "mini-cast-prov")
    bundle = root / "mini-cast-prov"
    export = bundle / "mini-cast-prov.topology.json"
    raw = json.loads(export.read_text())
    raw["cast_invocation"] = block
    export.write_text(json.dumps(raw, indent=2, sort_keys=True) + "\n")

    with pytest.raises(rc.RecompileInvocationError) as e:
        rc.read_cast_invocation(bundle, "mini-cast-prov")
    assert expect in str(e.value)


def test_the_cast_invocation_reads_the_fixtures_recorded_flags(tmp_path):
    from axrange.api import recompile as rc

    root = _copy_bundle(tmp_path, "mini-cast-prov")
    inv = rc.read_cast_invocation(root / "mini-cast-prov", "mini-cast-prov")
    assert inv.pit_id == "pit-miniprov-0000000001"
    assert inv.relocate == "10.200.0.0/16"
    assert inv.max_count == 12
    assert inv.chatter is False
    # GAP-088: the invocation's build stamp is a DIFFERENT value from the
    # sidecar's (`fixture-0000000000`) -- the fixture used to spell them the
    # same, which made the two indistinguishable to every test in this file.
    assert inv.code_fingerprint == "fixture-castbuild-01"


@requires_real_golden
def test_the_real_golden_bundle_records_its_own_cast_invocation(tmp_path):
    """LES-185, on the bundle that ships: `corpapcn-golden` was recast under
    `axr-topology/1.5` and carries the flags it was produced with.

    `--relocate 10.200.0.0/16` is the one that matters here -- a re-cast
    without it would reproduce the SOURCE network's CIDRs, colliding with the
    lab the twin deploys into, and an apply that guessed the flags would have
    done exactly that silently."""
    from axrange.api import recompile as rc

    inv = rc.read_cast_invocation(REAL_GOLDEN, "corpapcn-golden")
    assert inv.pit_id == "pit-03de65ec67-1787823960"
    assert inv.imprint_id == "corpapcn-golden"
    assert inv.relocate == "10.200.0.0/16"
    assert inv.max_count is None
    assert inv.chatter is True
    # R4: the invocation's `code_fingerprint` is the COMPILER's build hash and
    # is not this bundle's identity -- it does not even agree with the
    # sidecar's own copy across a recast, which is the whole finding.
    assert inv.code_fingerprint is not None
    assert rc.bundle_fingerprint(REAL_GOLDEN, "corpapcn-golden") != inv.code_fingerprint


@requires_real_golden
def test_the_two_code_fingerprints_in_a_REAL_bundle_are_different_values():
    """GAP-088's premise, measured on shipped content rather than a fixture.

    One field NAME, two questions: the sidecar's `code_fingerprint` is the
    PIT row's build stamp (whose run materialized the capture) and the
    export's `cast_invocation.code_fingerprint` is the build that ran the
    cast. The apply response published the first under a docstring
    describing the second for as long as nothing here compared them -- and a
    fixture that spelled both the same could never have caught it."""
    from axrange.api import recompile as rc
    from axrange.fidelity.reference import load_reference_bundle

    bundle = load_reference_bundle(REAL_GOLDEN, expected_id="corpapcn-golden")
    pit_stamp = bundle.code_fingerprint                      # the sidecar's
    cast_stamp, why = rc.cast_code_fingerprint(              # the invocation's
        bundle.cast_invocation, where="the bundle's export",
    )
    assert why is None, why
    assert pit_stamp and cast_stamp
    assert pit_stamp != cast_stamp, (
        "the shipped golden bundle is supposed to demonstrate that these are "
        "two different values; if they have converged, this test's premise "
        "(and GAP-088's) needs re-measuring, not deleting"
    )


# ---------------------------------------------------------------------------
# S4 -- each artifact is read ONCE per request
# ---------------------------------------------------------------------------
def _count_reads(monkeypatch, root: Path) -> dict[str, int]:
    """Count `read_text`/`read_bytes` per file name under `root`.

    A read count is the only way to assert "loaded once" without asserting
    about the shape of the call graph, which is what makes this survive a
    refactor that keeps the property."""
    import collections

    counts: collections.Counter = collections.Counter()
    real_text, real_bytes = Path.read_text, Path.read_bytes

    def _text(self, *a, **kw):
        if str(self).startswith(str(root)):
            counts[self.name] += 1
        return real_text(self, *a, **kw)

    def _bytes(self, *a, **kw):
        if str(self).startswith(str(root)):
            counts[self.name] += 1
        return real_bytes(self, *a, **kw)

    monkeypatch.setattr(Path, "read_text", _text)
    monkeypatch.setattr(Path, "read_bytes", _bytes)
    return counts


@pytest.mark.parametrize("route", ["cast", "provenance", "topology"])
def test_a_read_endpoint_loads_each_artifact_once(tmp_path, monkeypatch, route):
    """S4: `load_provenance` parsed the sidecar itself and then handed the
    whole bundle -- 732 KB of topology export included -- to a second parser
    for the same file, and `imprint_diff` loaded the bundle twice.

    One load per request, asserted by counting reads rather than by
    inspecting the call graph."""
    root = _copy_bundle(tmp_path, "mini-cast-prov")
    c = _client(_settings(root, tmp_path))
    if route == "cast":
        # `/cast` also publishes `bundle_fingerprint` (fix round 1, finding
        # 1): R4's content hash is over the files' RAW BYTES, which no parse
        # retains (`read_text` normalizes newlines), so it costs exactly one
        # `read_bytes` per canonical file ON TOP of the one parse. Counted
        # per method so the parse-once property stays pinned exactly.
        import collections

        from axrange.api.recompile import FINGERPRINT_FILES

        by: collections.Counter = collections.Counter()
        real_text, real_bytes = Path.read_text, Path.read_bytes

        def _text(self, *a, **kw):
            if str(self).startswith(str(root)):
                by[("text", self.name)] += 1
            return real_text(self, *a, **kw)

        def _bytes(self, *a, **kw):
            if str(self).startswith(str(root)):
                by[("bytes", self.name)] += 1
            return real_bytes(self, *a, **kw)

        monkeypatch.setattr(Path, "read_text", _text)
        monkeypatch.setattr(Path, "read_bytes", _bytes)
        assert c.get("/api/twins/mini-cast-prov/cast").status_code == 200
        hashed = {p.format(id="mini-cast-prov") for p in FINGERPRINT_FILES}
        for (method, name), n in by.items():
            assert n <= 1, (method, name, n, dict(by))
            if method == "bytes":
                assert name in hashed, (name, dict(by))
        return
    counts = _count_reads(monkeypatch, root)
    assert c.get(f"/api/twins/mini-cast-prov/{route}").status_code == 200
    for name, n in counts.items():
        assert n <= 1, (route, name, n, dict(counts))


def test_the_listing_reads_each_bundles_files_once(tmp_path, monkeypatch):
    """S4's other half: the strict reader ran first and, on its failure, a
    degraded reader re-read BOTH artifacts -- so the branch every real cast
    bundle takes (a canonical sidecar) paid for two full reads of each file,
    and `_as_of_from_export` built a 786-node object graph to reach one
    top-level float."""
    root = _copy_bundle(tmp_path, "mini-cast-prov")
    shutil.copytree(CAST_FIXTURES / "mini-cast", root / "mini-cast")
    c = _client(_settings(root, tmp_path))
    counts = _count_reads(monkeypatch, root)
    assert c.get("/api/twins").status_code == 200
    for name, n in counts.items():
        assert n <= 1, (name, n, dict(counts))


# ---------------------------------------------------------------------------
# GAP-082 review round 2 -- the twin config is parsed ONCE per file (SF1), and
# every export value is typed by one bounded rule and NAMED when wrong (SF2;
# items 5, 6 and 8).
# ---------------------------------------------------------------------------
def _count_twin_config_parses(monkeypatch, yaml_path: Path) -> list[str]:
    """Every PARSE of `yaml_path` -- the ~210 ms a parse of the real pit
    bundle's config costs -- by the imprint reader and by the twin-config
    registry (the expander and the fidelity reader load through it): the two
    `load_yaml_raw` call sites that read a twin config. Each entry is the
    module that parsed."""
    from axrange.api import imprints
    from axrange.twinconfig import registry

    target = yaml_path.resolve()
    parses: list[str] = []
    for module in (imprints, registry):
        real = module.load_yaml_raw

        def _spy(path, *a, _real=real, _name=module.__name__, **kw):
            if Path(path).resolve() == target:
                parses.append(_name)
            return _real(path, *a, **kw)

        monkeypatch.setattr(module, "load_yaml_raw", _spy)
    return parses


def _cold_twin_config_cache(monkeypatch) -> None:
    from collections import OrderedDict

    from axrange.api import imprints

    monkeypatch.setattr(imprints, "_TWIN_CONFIG_CACHE", OrderedDict())


def test_opening_the_console_parses_the_twin_config_once(tmp_path, monkeypatch):
    """SF1: the Console's open sequence -- the listing, then the summary,
    `/cast`, `/topology`, `/provenance`, `/assertions` and `/layout` --
    parsed the pit bundle's twin config EIGHT times (~2.5 s). Once now: the
    addressing guard's parse is cached per file, and `/cast` and
    `/provenance` hand it to the fidelity reader instead of letting it scan
    and parse the same file twice more."""
    root = _copy_bundle(tmp_path, "mini-cast-prov")
    _cold_twin_config_cache(monkeypatch)
    c = _client(_settings(root, tmp_path))
    parses = _count_twin_config_parses(monkeypatch, root / "mini-cast-prov" / "mini-cast-prov.yaml")
    assert c.get("/api/twins").status_code == 200
    for route in _CONSOLE_READ_ROUTES:
        r = c.get(f"/api/twins/mini-cast-prov{route}")
        assert r.status_code == 200, (route, r.text[:300])
    assert parses == ["axrange.api.imprints"], parses


def test_a_plan_preview_parses_the_twin_config_twice_cold_and_once_warm(tmp_path, monkeypatch):
    """SF1 on the plan path: the pre-check, the source-directory lookup and
    the expander each parsed the same file (three parses, ~2.2 s on the pit
    bundle). The source-directory lookup now stats the file the expander
    will read instead of parsing it, and the pre-check's parse is cached; the
    expander's own parse is the one that remains."""
    root = tmp_path / "imprints"
    root.mkdir()
    shutil.copytree(CAST_FIXTURES / "mini-cast-prov", root / "mini-cast-prov")
    _private_tmp(tmp_path, monkeypatch)
    _cold_twin_config_cache(monkeypatch)
    c = _client(_plan_settings(root, tmp_path))
    parses = _count_twin_config_parses(monkeypatch, root / "mini-cast-prov" / "mini-cast-prov.yaml")
    for expected in (
        ["axrange.api.imprints", "axrange.twinconfig.registry"],  # cold
        ["axrange.twinconfig.registry"],                          # warm
    ):
        parses.clear()
        r = c.post("/api/twins/mini-cast-prov/plan", json={"target_node": "pxtest1"})
        assert r.status_code == 200, r.text[:300]
        assert sorted(parses) == expected, parses


#: The smallest export with one of each nested object, so a value has
#: somewhere to be wrong.
_EXPORT_BASE = {
    "imprint_id": "mini-cast-prov", "as_of": 1767225600.0, "windows": 3,
    "segments": [{"cidr": "10.5.0.0/24", "name": "lan", "entities": 1}],
    "nodes": [{"id": "address:10.5.0.2", "address": "10.5.0.2",
               "segment": "10.5.0.0/24", "windows_seen": 3}],
    "edges": [{"src": "address:10.5.0.2", "dst": "address:10.6.0.3", "bytes": 10, "flows": 1}],
    "census": {"entities_total": 1},
}


def _with_export(tmp_path: Path, **values) -> Path:
    """`mini-cast-prov` whose export is `_EXPORT_BASE` with `values` set --
    keys are dotted paths (`segments.0.cidr`)."""
    root = _copy_bundle(tmp_path, "mini-cast-prov")
    doc = json.loads(json.dumps(_EXPORT_BASE))
    for where, value in values.items():
        *parents, last = where.split(".")
        target = doc
        for key in parents:
            target = target[int(key)] if key.isdigit() else target[key]
        target[int(last) if last.isdigit() else last] = value
    (root / "mini-cast-prov" / "mini-cast-prov.topology.json").write_text(json.dumps(doc))
    return root


_EVERY_PARSER = ("/topology", "/cast", "/provenance")


@pytest.mark.parametrize(("where", "value", "field", "routes"), [
    ("census", [1], "`census`", ("/topology",)),
    ("census.entities_total", 2**53, "`census.entities_total`", ("/topology",)),
    ("segments.0.cidr", ["10.5.0.0/24"], "`segments[0].cidr`", _EVERY_PARSER),
    ("segments.0.name", 7, "`segments[0].name`", _EVERY_PARSER),
    ("nodes.0.segment", {"cidr": "10.5.0.0/24"}, "`nodes[0].segment`", _EVERY_PARSER),
    ("nodes.0.id", 42, "`nodes[0].id`", _EVERY_PARSER),
    ("nodes.0.windows_seen", 2**53, "`nodes[0].windows_seen`", _EVERY_PARSER),
    ("edges.0.bytes", 2**53, "`edges[0].bytes`", ("/topology",)),
    # The rest of the class, not just the fields a bug named: `role` is a dict
    # KEY in `fidelity/rows.py`'s `_group_key`, so a list or object role is
    # the same unhashable-key 500 as `segment`/`cidr` were.
    ("nodes.0.role", ["domain-controller"], "`nodes[0].role`", _EVERY_PARSER),
    ("nodes.0.role_evidence_class", 5, "`nodes[0].role_evidence_class`", _EVERY_PARSER),
    ("nodes.0.segment_evidence_class", {"x": 1}, "`nodes[0].segment_evidence_class`",
     _EVERY_PARSER),
    ("nodes.0.locality", 7, "`nodes[0].locality`", _EVERY_PARSER),
    ("segments.0.entities", "many", "`segments[0].entities`", _EVERY_PARSER),
    ("segments.0.coverage_source", {"a": 1}, "`segments[0].coverage_source`", _EVERY_PARSER),
], ids=["census-list", "census-count-unsafe", "segment-cidr-list", "segment-name-number",
        "node-segment-object", "node-id-number", "node-windows-seen-unsafe", "edge-bytes-unsafe",
        "node-role-list", "node-role-evidence-class-number", "node-segment-evidence-class-object",
        "node-locality-number", "segment-entities-string", "segment-coverage-source-object"])
def test_a_mistyped_or_unpublishable_export_value_is_a_400_naming_it(tmp_path, where, value, field, routes):
    """A nested value of the wrong type -- or a whole number past 2**53 - 1,
    which a browser reads as a different number -- is refused NAMING the
    field by every route that parses it. A non-object `census` 500'd
    `/topology`; a list or object `cidr`/`segment` 500'd `/cast` and
    `/provenance` as an unhashable key; a numeric `cidr`/`name`/`id` came back
    `str()`-ed into a value the producer never wrote. The summary and the
    listing read top-level scalars only, and still answer 200."""
    root = _with_export(tmp_path, **{where: value})
    c = _client(_settings(root, tmp_path))
    for route in routes:
        r = c.get(f"/api/twins/mini-cast-prov{route}")
        assert r.status_code == 400, (route, r.status_code, r.text[:300])
        assert field in r.json()["error"], (route, r.json()["error"])
    assert c.get("/api/twins/mini-cast-prov").status_code == 200
    assert [e["id"] for e in c.get("/api/twins").json()["imprints"]] == ["mini-cast-prov"]


@pytest.mark.parametrize(("windows", "published"), [(2**53 - 1, 2**53 - 1), (2**53, None)],
                         ids=["max-safe", "two-to-the-53"])
def test_a_windows_a_browser_cannot_read_exactly_is_null_on_the_summary_and_a_400_elsewhere(
    tmp_path, windows, published,
):
    """SF2: the summary is the TOLERANT reader -- it publishes `null` and
    says why -- while `/topology` and `/cast`, which parse the export whole,
    refuse naming the field. The largest integer a browser reads exactly is
    published as is by all three."""
    root = _with_export(tmp_path, windows=windows)
    c = _client(_settings(root, tmp_path))
    s = c.get("/api/twins/mini-cast-prov").json()
    assert s["windows"] == published
    reasons = [w for w in s["warnings"] if "`windows`" in w]
    if published is None:
        assert len(reasons) == 1 and "is out of range" in reasons[0], s["warnings"]
        for route in ("/topology", "/cast"):
            r = c.get(f"/api/twins/mini-cast-prov{route}")
            assert r.status_code == 400 and "`windows`" in r.json()["error"], (route, r.text[:300])
    else:
        assert reasons == [], s["warnings"]
        assert c.get("/api/twins/mini-cast-prov/topology").json()["windows"] == published
        assert c.get("/api/twins/mini-cast-prov/cast").status_code == 200


def _count_bundle_loads(monkeypatch) -> list[str]:
    """Count the EXPENSIVE load -- `load_reference_bundle`, which parses all
    three artifacts and builds the node/edge/segment object graph. The cheap
    per-file reads (a content fingerprint, the `cast_invocation` block) are a
    different cost and are counted by `_count_reads`."""
    from axrange.fidelity import reference as ref

    calls: list[str] = []
    real = ref.load_reference_bundle

    def _spy(bundle_dir, **kw):
        calls.append(str(bundle_dir))
        return real(bundle_dir, **kw)

    monkeypatch.setattr(ref, "load_reference_bundle", _spy)
    return calls


def test_apply_loads_the_bundle_once_before_and_once_after(tmp_path, monkeypatch):
    """S4: `imprint_apply` built the bundle's object graph THREE times for
    one request (a cast summary before, a reference bundle for the snapshot,
    a cast summary after). TWO is the honest floor -- the whole point of the
    endpoint is to compare before with after."""
    root = _copy_bundle(tmp_path, "mini-cast-prov")
    c = _client(_apply_settings(root, tmp_path), backend=_FakeRecompile())
    calls = _count_bundle_loads(monkeypatch)
    assert c.post(
        "/api/twins/mini-cast-prov/apply", json={"document": _VALID_DOC},
    ).status_code == 200
    assert len(calls) == 2, calls


@pytest.mark.parametrize("route", ["cast", "provenance", "diff"])
def test_a_read_endpoint_builds_the_object_graph_once(tmp_path, monkeypatch, route):
    """The same property on the read side: `load_provenance` parsed the
    sidecar itself and then handed the bundle to a second parser for the same
    file, and `imprint_diff` loaded it twice."""
    root = _copy_bundle(tmp_path, "mini-cast-prov")
    c = _client(_apply_settings(root, tmp_path), backend=_FakeRecompile())
    params = {}
    if route == "diff":
        params["since"] = c.post(
            "/api/twins/mini-cast-prov/apply", json={"document": _VALID_DOC},
        ).json()["fingerprint_before"]
    calls = _count_bundle_loads(monkeypatch)
    assert c.get(
        f"/api/twins/mini-cast-prov/{route}", params=params,
    ).status_code == 200
    assert len(calls) == 1, (route, calls)


# ---------------------------------------------------------------------------
# The nits with a runtime surface
# ---------------------------------------------------------------------------
def test_a_console_endpoint_does_not_cite_the_scorecard_work_order(tmp_path):
    """`GET .../cast` is a Console endpoint, and the reader it borrows is the
    FIDELITY reader -- whose warnings cite a work order about scorecard rows.
    An operator on the canvas should be sent to the Console's own doc."""
    from axrange.fidelity.reference import SIDECAR_ABSENT_WARNING

    assert "WORK_ORDER_fidelity_scorecard" in SIDECAR_ABSENT_WARNING  # the source

    root = _copy_bundle(tmp_path, "mini-cast")
    c = _client(_settings(root, tmp_path))
    j = c.get("/api/twins/mini-cast/cast").json()
    assert j["warnings"], "a sidecar-less bundle must still say so"
    for w in j["warnings"]:
        assert "WORK_ORDER_fidelity_scorecard" not in w, w
        assert "WORK_ORDER_console_canvas" in w or "sidecar absent" in w, w
    # ... and the same rewrite on the provenance endpoint.
    for w in c.get("/api/twins/mini-cast/provenance").json()["warnings"]:
        assert "WORK_ORDER_fidelity_scorecard" not in w, w


def test_the_canonical_sidecar_predicate_has_exactly_one_spelling():
    """The nit, pinned: `is_canonical_provenance` was spelled three different
    ways in `imprints.py`, and three spellings of one question are three
    places for it to drift."""
    from axrange.api.imprints import is_canonical_provenance
    from axrange.fidelity.reference import CAST_PROVENANCE_SCHEMA

    assert is_canonical_provenance({"schema": CAST_PROVENANCE_SCHEMA}) is True
    assert is_canonical_provenance({"schema": "something/else"}) is False
    assert is_canonical_provenance({}) is False
    assert is_canonical_provenance(None) is False
    assert is_canonical_provenance("a string") is False

    src = Path(__import__("axrange.api.imprints", fromlist=["x"]).__file__).read_text()
    assert src.count(f'== {CAST_PROVENANCE_SCHEMA!r}') == 0, (
        "the schema literal is compared in more than one place again"
    )


def test_resolve_imprint_dir_is_the_one_guarded_path_into_a_bundle(tmp_path):
    """LES-164: the docstring used to claim `console_files`/`recompile`
    import this, which they do not -- they are handed an already-resolved
    directory, which is a STRONGER guarantee (they cannot bypass the guard
    because they never see an id). The claim now matches the code."""
    import inspect

    from axrange.api import console_files, imprints, recompile

    doc = inspect.getdoc(imprints.resolve_imprint_dir)
    assert "console_files" in doc and "recompile" in doc
    for mod in (console_files, recompile):
        src = Path(mod.__file__).read_text()
        # They may NAME it in prose; what they must not do is call it -- they
        # are handed an already-resolved directory, which is the stronger
        # guarantee (no id, so no way to bypass the guard).
        assert "resolve_imprint_dir(" not in src, mod.__name__
        assert "import resolve_imprint_dir" not in src, mod.__name__

    with pytest.raises(imprints.ImprintNotFoundError):
        imprints.resolve_imprint_dir(tmp_path, "..")
    with pytest.raises(imprints.ImprintNotFoundError):
        imprints.resolve_imprint_dir(tmp_path, ".")


# ---------------------------------------------------------------------------
# R3 -- the two stages, sequenced (no subprocess: `_run` is the seam)
# ---------------------------------------------------------------------------
class _FakeProc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def _staged(monkeypatch, *procs):
    """Drive `SubprocessRecompile` with canned process results, recording the
    argvs. Never spawns anything -- the suite must not depend on an
    aximprint binary being installed."""
    from axrange.api.recompile import SubprocessRecompile

    seen: list[list[str]] = []
    queue = list(procs)

    def _run(self, argv, *, result=None):
        seen.append(list(argv))
        return queue.pop(0)

    monkeypatch.setattr(SubprocessRecompile, "_run", _run)
    return seen


def test_the_install_stage_runs_after_the_cast_stage(tmp_path, monkeypatch):
    """R3: a cast writes GENERIC stems into `--out`, and the `<id>.*` names
    this API reads come from the install stage. A one-stage apply pointed at
    the bundle directory therefore reported success and changed nothing the
    API could see -- found live, 2026-09-05."""
    from axrange.api.recompile import CAST_VERB, INSTALL_VERB, SubprocessRecompile

    seen = _staged(
        monkeypatch,
        _FakeProc(stdout=json.dumps({"imprint_id": "corpapcn-golden", "nodes": 7})),
        _FakeProc(stdout="installed"),
    )
    result = SubprocessRecompile("/opt/aximprint/bin/aximprint").recompile(
        _pinned_request(sensors=("XPress-SW-A",))
    )
    assert [a[1] for a in seen] == [CAST_VERB, INSTALL_VERB]
    assert result.returncode == 0 and result.install_returncode == 0
    assert result.argv == seen[0] and result.install_argv == seen[1]
    assert result.document == {"imprint_id": "corpapcn-golden", "nodes": 7}
    assert seen[1][-3:] == ["corpapcn-golden", "--sensors", "XPress-SW-A"]


def test_a_failed_cast_never_reaches_the_install_stage(tmp_path, monkeypatch):
    """A shape error in the assertions file exits 2 on stage 1; installing
    the previous cast's leftovers on top of the bundle would be worse than
    doing nothing."""
    from axrange.api.recompile import RecompileShapeError, SubprocessRecompile

    seen = _staged(monkeypatch, _FakeProc(returncode=2, stderr="assertions: add[0]: boom"))
    with pytest.raises(RecompileShapeError) as e:
        SubprocessRecompile("/opt/aximprint/bin/aximprint").recompile(_pinned_request())
    assert len(seen) == 1
    assert "add[0]: boom" in str(e.value)


def test_a_failed_install_is_reported_with_its_own_stage_named(tmp_path, monkeypatch):
    """Two stages means two places to fail, and an operator reading one
    stderr blob needs to know which command produced it."""
    from axrange.api.recompile import (
        INSTALL_VERB,
        RecompileFailedError,
        SubprocessRecompile,
    )

    _staged(
        monkeypatch,
        _FakeProc(stdout=json.dumps({"nodes": 1})),
        _FakeProc(returncode=1, stderr="is not a host in target twin"),
    )
    with pytest.raises(RecompileFailedError) as e:
        SubprocessRecompile("/opt/aximprint/bin/aximprint").recompile(_pinned_request())
    assert INSTALL_VERB in str(e.value)
    assert "is not a host in target twin" in str(e.value)
    assert f"[{INSTALL_VERB}]" in e.value.result.combined_stderr


def test_both_stages_streams_are_bounded(tmp_path, monkeypatch):
    """LES-206 at the source, not only at the response: a compiler that dies
    printing megabytes must not put megabytes in a `RecompileResult`."""
    from axrange.api.recompile import MAX_STREAM_CHARS, SubprocessRecompile

    _staged(
        monkeypatch,
        _FakeProc(stdout=json.dumps({"nodes": 1}), stderr="W\n" * 100_000),
        _FakeProc(stdout="x" * 100_000, stderr="Y\n" * 100_000),
    )
    r = SubprocessRecompile("/opt/aximprint/bin/aximprint").recompile(_pinned_request())
    for stream in (r.stdout, r.stderr, r.install_stdout, r.install_stderr):
        assert len(stream) <= MAX_STREAM_CHARS + 200, len(stream)
    assert "elided" in r.stderr
    assert len(r.combined_stderr) <= MAX_STREAM_CHARS + 200


# ---------------------------------------------------------------------------
# `compiler` is SIDECAR-ONLY: never authorable, always publishable
# ---------------------------------------------------------------------------
def test_the_assertions_vocabulary_is_five_classes_and_excludes_compiler():
    """The schema's `$defs.evidenceClass` is DOCUMENTATION-ONLY (nothing
    `$ref`s it) and lists five: `compiler` is deliberately absent, because it
    labels a value the compiler INVENTED and must never be ranked against
    evidence -- least of all by an operator hand-writing it.

    axRange's own six-class order (`14` §2) ends in `compiler` and is the
    PROVENANCE vocabulary. Two vocabularies, one word apart, and the
    difference is which direction the fact travelled."""
    import json as _json

    from axrange.api.console_files import SCHEMA_PATH
    from axrange.api.imprints import EVIDENCE_CLASSES

    doc = _json.loads(SCHEMA_PATH.read_text())
    authorable = doc["$defs"]["evidenceClass"]["enum"]
    assert authorable == ["asserted", "imported", "observed", "referenced", "imputed"]
    assert "compiler" not in authorable
    # ... and it is documentation: no record `$ref`s it, so no key in the
    # document carries an evidence class at all.
    assert _json.dumps(doc).count("#/$defs/evidenceClass") == 0
    # The PROVENANCE vocabulary is the six, `compiler` included.
    assert set(authorable) < set(EVIDENCE_CLASSES)
    assert EVIDENCE_CLASSES[-1] == "compiler"


@pytest.mark.parametrize("block", ["add", "override", "suppress"])
@pytest.mark.parametrize("cls", ["compiler", "asserted", "observed"])
def test_no_assertions_record_may_carry_an_evidence_class(tmp_path, block, cls):
    """Structural, not a special case: `additionalProperties: false` on every
    record and no `evidence_class` property anywhere means an authored class
    -- `compiler` above all -- is refused as an unknown key, in every block,
    naming the accepted set."""
    root = _copy_bundle(tmp_path, "mini-cast-prov")
    c = _client(_settings(root, tmp_path))
    d = _doc()
    d[block][0]["evidence_class"] = cls
    r = c.put("/api/twins/mini-cast-prov/assertions", json=d)
    assert r.status_code == 400, r.text
    assert "evidence_class" in r.json()["error"]
    assert "accepted" in r.json()["error"]
    assert not (root / "mini-cast-prov" / "mini-cast-prov.assertions.yaml").exists()


def test_provenance_rows_still_publish_the_compiler_class(tmp_path):
    """The other half of the same rule, and the one a naive "refuse compiler"
    fix would break: `compiler` rows come from the SIDECAR, where they are
    the honest label for a catalog default, and the provenance endpoint must
    keep publishing them."""
    c = _client(_settings(CAST_FIXTURES, tmp_path))
    j = c.get("/api/twins/mini-cast-prov/provenance").json()
    classes = {r["evidence_class"] for r in j["rows"]}
    assert "compiler" in classes
    assert not any("outside the six-class" in w for w in j["warnings"])


# ---------------------------------------------------------------------------
# Every `required` list in the schema is actually enforced
# ---------------------------------------------------------------------------
def test_every_required_key_the_schema_declares_is_enforced(tmp_path):
    """A `required` list in a schema nobody validates against is a comment.
    This drops each declared key in turn and demands a refusal naming it."""
    import json as _json

    from axrange.api.console_files import SCHEMA_PATH

    doc = _json.loads(SCHEMA_PATH.read_text())
    root = _copy_bundle(tmp_path, "mini-cast-prov")
    c = _client(_settings(root, tmp_path))
    apath = root / "mini-cast-prov" / "mini-cast-prov.assertions.yaml"

    # (1) the document's own required keys (plus axRange's `imprint`).
    for key in sorted(set(doc["required"]) | {"imprint"}):
        d = _doc()
        del d[key]
        r = c.put("/api/twins/mini-cast-prov/assertions", json=d)
        assert r.status_code == 400, (key, r.text)
        assert key in r.json()["error"], (key, r.json()["error"])

    # (2) each add branch's required keys, on a record of that kind.
    samples = {
        "segment": {"kind": "segment", "key": "10.9.0.0/24", "name": "n", "note": "x"},
        "host": {"kind": "host", "key": "address:10.9.0.4",
                 "segment": "10.9.0.0/24", "note": "x"},
        "conduit": {"kind": "conduit", "key": "10.1.0.0/24|10.2.0.0/24", "note": "x"},
        # axr-assertions/2: the device tree's three kinds.
        "device": {"kind": "device", "key": "mac:aa:bb:cc:dd:ee:01", "device_kind": "switch",
                   "segment": "10.9.0.0/24", "note": "x"},
        "link": {"kind": "link", "key": "mac:aa:bb:cc:dd:ee:01#P1|address:10.9.0.4#eth0",
                 "mode": "access", "vlans": ["10.9.0.0/24"], "note": "x"},
        "tap": {"kind": "tap", "key": "segment:10.9.0.0/24", "sensors": ["s"], "note": "x"},
    }
    for branch in doc["$defs"]["addRecord"]["oneOf"]:
        kind = branch["properties"]["kind"]["const"]
        for key in branch["required"]:
            rec = dict(samples[kind])
            del rec[key]
            r = c.put(
                "/api/twins/mini-cast-prov/assertions",
                json=_doc(add=[rec], override=[], suppress=[]),
            )
            assert r.status_code == 400, (kind, key, r.text)

    # (3) the override and suppress branches.
    override = {"key": "address:10.9.0.4", "field": "role", "value": "web", "note": "x"}
    for key in doc["$defs"]["overrideRecord"]["oneOf"][0]["required"]:
        rec = dict(override)
        del rec[key]
        r = c.put(
            "/api/twins/mini-cast-prov/assertions",
            json=_doc(add=[], override=[rec], suppress=[]),
        )
        assert r.status_code == 400, (key, r.text)
    for key in doc["$defs"]["suppressRecord"]["required"]:
        rec = {"key": "address:10.9.0.4", "note": "x"}
        del rec[key]
        r = c.put(
            "/api/twins/mini-cast-prov/assertions",
            json=_doc(add=[], override=[], suppress=[rec]),
        )
        assert r.status_code == 400, (key, r.text)

    assert not apath.exists(), "not one of those refusals may have written"


# ---------------------------------------------------------------------------
# The six refusals the schema CANNOT express, named in its own `$comment`
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "label,mutate",
    [
        ("a CIDR with host bits", lambda d: d.update(add=[
            {"kind": "segment", "key": "10.80.31.5/24", "name": "n", "note": "x"}])),
        ("a self-conduit", lambda d: d.update(add=[
            {"kind": "conduit", "key": "10.1.0.0/24|10.1.0.0/24", "note": "x"}])),
        ("an unparseable as_of", lambda d: d.update(as_of="soon")),
        ("a duplicate merge key in one block", lambda d: d["suppress"].append(
            dict(d["suppress"][0]))),
        ("a key in both add and suppress", lambda d: d["suppress"].append(
            {"key": "address:10.80.20.5", "note": "also suppressed"})),
        ("a (key, field) in both add and override", lambda d: d["override"].append(
            {"key": "address:10.80.20.5", "field": "role", "value": "web", "note": "n"})),
    ],
    ids=lambda v: v if isinstance(v, str) else "",
)
def test_the_six_refusals_the_schema_cannot_express_are_code_checks(
    tmp_path, label, mutate,
):
    """The vendored schema's own `$comment` names six refusals no JSON Schema
    keyword reaches -- a non-strict CIDR, a self-conduit, an unparseable
    `as_of`, a duplicate merge key inside one block, a key in both `add` and
    `suppress`, and a `(key, field)` claimed by both an `add` record's own
    field and an `override`.

    They are the reason `console_files` validates rather than merely
    schema-checks, and they must match the compiler's own, which refuses all
    six. A consumer reading "schema-valid" as "parser-accepted" is exactly
    what the `$comment` exists to prevent."""
    root = _copy_bundle(tmp_path, "mini-cast-prov")
    c = _client(_settings(root, tmp_path))
    d = _doc()
    mutate(d)
    r = c.put("/api/twins/mini-cast-prov/assertions", json=d)
    assert r.status_code == 400, (label, r.text)
    assert not (root / "mini-cast-prov" / "mini-cast-prov.assertions.yaml").exists()


def test_the_schema_comment_still_names_exactly_those_nine(tmp_path):
    """If the other repo adds a seventh unexpressible refusal, this is where
    axRange finds out -- as a failing test naming the phrase it did not
    expect, rather than as a file the compiler rejects at apply time."""
    import json as _json

    from axrange.api.console_files import SCHEMA_PATH

    comment = _json.loads(SCHEMA_PATH.read_text())["$comment"]
    # Anchored on the COUNT and its enumeration markers rather than on each
    # item's wording: the other repo rewords this prose independently, and a
    # test that fails on a reworded clause teaches people to ignore it --
    # which is what would then hide a genuine seventh refusal.
    assert "NINE REFUSALS THIS SCHEMA CANNOT EXPRESS" in comment
    for marker in ("(1)", "(2)", "(3)", "(4)", "(5)", "(6)"):
        assert marker in comment, marker
    assert "(7)" not in comment, "a seventh unexpressible refusal appeared"


# ===========================================================================
# Round-2 test-stage findings (2026-09-06), from driving the REAL two-stage
# `aximprint cast` + `install-cast` through `POST .../apply` on a scratch
# copy of the re-installed `twin-configs-cast/corpapcn-golden/`.
#
# Both are LES-185's shape: every fixture in this file is small enough, or
# synthetic enough, that neither defect can appear in it.
# ===========================================================================
def test_a_real_sized_cast_stdout_still_yields_its_record_counts(tmp_path, monkeypatch):
    """G1 (was an xfail, measured live): a REAL-SIZED cast document survives
    the LES-206 output bound.

    The measured stdout was 8356 characters -- 4% over the 8000-char cap,
    almost all of it `caveats[]`. The bound was applied on the way IN and
    kept the TAIL, so the parser received a document with its opening brace
    removed, reported "the recompiler's stdout is not JSON" (it WAS JSON;
    axRange had truncated it) and returned empty counts -- blaming the
    producer for our own truncation.

    Driven through the REAL pipeline (a process result into
    `SubprocessRecompile.recompile`) rather than by calling `_parse_stdout`
    on a hand-clipped string, because the previous test did the latter and
    that is exactly why it could not see this."""
    from axrange.api import recompile as rc

    doc = {
        "pit_id": "pit-03de65ec67-1787823960", "imprint_id": "corpapcn-golden",
        "out": "/tmp/axr-cast-corpapcn-golden-xxxx",
        "assertions": {"path": "/x/a.yaml", "add": 0, "override": 0, "suppress": 1},
        "segments": 33, "nodes": 87, "conduits": 31, "assignments": 108,
        "provenance_fields": 681,
        # The real bundle's five caveats are ~7 kB on their own.
        "caveats": ["c" * 1600 for _ in range(5)],
    }
    raw = json.dumps(doc, indent=2)
    assert len(raw) > rc.MAX_STREAM_CHARS, "the premise: a real document overruns the cap"

    _staged(monkeypatch, _FakeProc(stdout=raw), _FakeProc(stdout="installed"))
    result = rc.SubprocessRecompile("/opt/aximprint/bin/aximprint").recompile(
        _pinned_request()
    )
    assert result.document is not None, result.warnings
    assert result.warnings == [], result.warnings
    assert rc.record_counts_from(result.document) == {
        "add": 0, "override": 0, "suppress": 1,
    }
    assert rc.cast_counts_from(result.document)["nodes"] == 87
    # ... and the REPUBLISHED stdout is still bounded, head-first, so what
    # survives is the start of a document rather than the end of one.
    assert len(result.stdout) <= rc.MAX_STREAM_CHARS + 200
    assert result.stdout.startswith("{")
    assert "later character(s) elided" in result.stdout


def test_the_output_bound_keeps_the_head_of_a_document_and_the_tail_of_a_log():
    """The DIRECTION is the defect, not the size. A log's useful line is the
    LAST one; a document truncated from the front stops being a document."""
    from axrange.api.recompile import MAX_STREAM_CHARS, clip

    text = "S" + ("x" * (MAX_STREAM_CHARS * 2)) + "E"
    assert clip(text, keep="head").startswith("S")
    assert "later character(s) elided" in clip(text, keep="head")
    assert clip(text, keep="tail").endswith("earlier character(s) elided]")
    assert clip(text, keep="tail").splitlines()[0].endswith("E")
    # Under the cap, both directions are the identity.
    assert clip("short", keep="head") == clip("short", keep="tail") == "short"


def test_a_real_sized_stdout_survives_the_whole_apply(tmp_path):
    """The same defect where an operator meets it: an over-cap document must
    still produce record counts, publish the compiler's caveats, and raise no
    false "not JSON" warning through `POST .../apply`."""
    from axrange.api import recompile as rc

    payload = {
        "pit_id": "pit-1", "imprint_id": "mini-cast-prov",
        "assertions": {"path": "/x/a.yaml", "add": 2, "override": 1, "suppress": 1},
        "segments": 4, "nodes": 9,
        "caveats": ["c" * 1600 for _ in range(5)],
    }
    assert len(json.dumps(payload)) > rc.MAX_STREAM_CHARS

    class _Big:
        def recompile(self, request):
            r = rc.RecompileResult(
                argv=["aximprint", "cast"], returncode=0,
                stdout=json.dumps(payload),
            )
            rc._parse_stdout(r)
            r.stdout = rc.clip(r.stdout, keep="head")
            return r

    root = _copy_bundle(tmp_path, "mini-cast-prov")
    c = _client(_apply_settings(root, tmp_path), backend=_Big())
    j = c.post(
        "/api/twins/mini-cast-prov/apply", json={"document": _VALID_DOC},
    ).json()
    assert j["record_counts"] == {"add": 2, "override": 1, "suppress": 1}
    assert j["cast_counts"] == {"segments": 4, "nodes": 9}
    assert not any("not JSON" in w for w in j["warnings"]), j["warnings"]
    assert any("caveat" in w for w in j["warnings"])


# G2 (was an xfail, measured live): the warning no longer claims WHICH kind
# of change it was -- and the snapshot now carries the twin side, so the
# change it could not see is visible in the first place (see the twin-side
# tests below).
def test_a_population_change_is_not_reported_as_a_field_edit(tmp_path):
    from axrange.api import recompile as rc

    root = _copy_bundle(tmp_path, "mini-cast-prov")
    bundle = root / "mini-cast-prov"
    c = _client(_apply_settings(root, tmp_path), backend=_FakeRecompile())
    fp = c.post(
        "/api/twins/mini-cast-prov/apply", json={"document": _VALID_DOC},
    ).json()["fingerprint_before"]

    # Stand in for the live result: the twin config moved (a host was
    # suppressed out of it) while the observed export is untouched.
    snap_path = rc.history_dir(bundle) / f"{fp}.json"
    snap = json.loads(snap_path.read_text())
    snap["twin_config_sha256"] = "0" * 64
    snap_path.write_text(json.dumps(snap))

    j = c.get("/api/twins/mini-cast-prov/diff", params={"since": fp}).json()
    warned = " ".join(j["warnings"])
    assert "twin config changed" in warned, warned
    # It must NOT claim to know WHICH kind of change it was.
    assert "rather than the population" not in warned, warned


# ===========================================================================
# Round 3 (2026-09-06): G1/G2 above, plus N1-N5 and the nits.
# ===========================================================================

# ---------------------------------------------------------------------------
# G2 -- a suppress is VISIBLE, because the snapshot carries both populations
# ---------------------------------------------------------------------------
def test_the_snapshot_carries_both_populations(tmp_path):
    """G2: the observed side is what the vantage saw; the twin side is what
    the cast emitted. An assertion moves the second and cannot move the
    first, so a snapshot holding only the first could not show a `suppress`
    -- the very first edit the Console's edit set leads with."""
    from axrange.api import recompile as rc
    from axrange.api.imprints import load_reference

    bundle = load_reference("mini-cast-prov", CAST_FIXTURES)
    snap = rc.snapshot_bundle(bundle, fingerprint="fp")

    assert snap["schema"] == rc.SNAPSHOT_SCHEMA
    assert set(snap["observed"]) == {"nodes", "segments"}
    assert set(snap["twin"]) == {"hosts", "segments", "conduits"}
    assert snap["observed"]["nodes"], "the fixture has an export"
    assert snap["twin"]["hosts"], "the fixture emits hosts"
    # The two are keyed differently ON PURPOSE and must not be confused: an
    # observed key is an entity key, a twin key is a node id.
    assert all(k.startswith(("address:", "hostname:", "mac:"))
               for k in snap["observed"]["nodes"])
    assert not any(k.startswith("address:") for k in snap["twin"]["hosts"])
    # The sidecar's own accounting rides along, counts and all.
    assert snap["population"]["emitted_hosts"] == 5
    assert snap["population"]["suppressed"] == []


@requires_real_golden
def test_the_snapshot_reads_the_REAL_sidecars_population_block(tmp_path):
    """LES-185/LES-228 together: `population.emitted_hosts` sounds like a key
    set and is a COUNT (156 on the golden bundle), which is exactly why the
    twin's host KEYS have to come from the twin config instead. Checked
    against the sidecar that ships rather than assumed from the name."""
    from axrange.api import recompile as rc
    from axrange.api.imprints import load_reference

    snap = rc.snapshot_bundle(
        load_reference("corpapcn-golden", REAL_CAST_ROOT), fingerprint="fp",
    )
    assert snap["population"]["emitted_hosts"] == 156
    assert snap["population"]["pit_entities"] == 786
    assert isinstance(snap["population"]["suppressed"], list)
    # The twin side is keyed and populated from the twin config, not from
    # that count -- and the two do NOT agree, which is the point. The
    # sidecar counts HOSTS (156); the twin config's node instances are 157,
    # the extra one being the synthetic gateway `gw`, a `kind: router`. A
    # reader that had taken `emitted_hosts` for the population size would
    # have been off by every router in every twin, silently (LES-228: does
    # the name mean what it implies AT THIS GRANULARITY).
    hosts = snap["twin"]["hosts"]
    assert len(hosts) == 157
    assert sum(1 for v in hosts.values() if v["kind"] == "host") == 156
    assert [k for k, v in hosts.items() if v["kind"] == "router"] == ["gw"]
    assert len(snap["observed"]["nodes"]) == 786


def test_a_suppress_shows_as_a_twin_side_removal(tmp_path):
    """The measured case, reproduced: a host leaves the twin config while
    every observed node stays. Before G2 this produced an EMPTY diff and a
    warning claiming the apply "moved a field rather than the population" --
    it had moved the population."""
    from axrange.api import recompile as rc

    root = _copy_bundle(tmp_path, "mini-cast-prov")
    bundle = root / "mini-cast-prov"
    c = _client(_apply_settings(root, tmp_path), backend=_FakeRecompile())
    fp = c.post(
        "/api/twins/mini-cast-prov/apply", json={"document": _VALID_DOC},
    ).json()["fingerprint_before"]

    # Stand in for the recompile: a host that WAS emitted no longer is,
    # while the observed side is byte-identical.
    snap_path = rc.history_dir(bundle) / f"{fp}.json"
    snap = json.loads(snap_path.read_text())
    gone = sorted(snap["twin"]["hosts"])[0]
    ghost = "suppressed-host-1"
    snap["twin"]["hosts"][ghost] = {
        "role": "generic-endpoint", "kind": "host", "segment": "s", "group": "g",
    }
    snap["population"]["suppressed"] = ["address:203.0.113.9"]
    snap_path.write_text(json.dumps(snap))

    j = c.get("/api/twins/mini-cast-prov/diff", params={"since": fp}).json()
    assert ghost in j["twin"]["hosts"]["removed"]
    assert gone not in j["twin"]["hosts"]["removed"]
    # The observed side is genuinely unchanged, and says so by being EMPTY
    # rather than absent.
    assert j["observed"]["nodes"] == {"added": [], "removed": [], "changed": []}
    # ... and with a real population move on the board, no warning claims
    # this was a field edit.
    assert not any("field-level edit" in w for w in j["warnings"]), j["warnings"]


def test_the_twin_side_reports_a_relocated_segment_as_a_CHANGED_field(tmp_path):
    """Twin segments are keyed by NAME with the cidr in the value, so a
    re-address shows as one changed field rather than as an add plus a
    remove -- which is what an operator means by "it moved"."""
    from axrange.api import recompile as rc

    root = _copy_bundle(tmp_path, "mini-cast-prov")
    bundle = root / "mini-cast-prov"
    c = _client(_apply_settings(root, tmp_path), backend=_FakeRecompile())
    fp = c.post(
        "/api/twins/mini-cast-prov/apply", json={"document": _VALID_DOC},
    ).json()["fingerprint_before"]

    snap_path = rc.history_dir(bundle) / f"{fp}.json"
    snap = json.loads(snap_path.read_text())
    name = sorted(snap["twin"]["segments"])[0]
    snap["twin"]["segments"][name] = {"cidr": "10.255.255.0/24"}
    snap_path.write_text(json.dumps(snap))

    j = c.get("/api/twins/mini-cast-prov/diff", params={"since": fp}).json()
    changed = {e["key"]: e for e in j["twin"]["segments"]["changed"]}
    assert name in changed
    assert changed[name]["fields"]["cidr"]["before"] == "10.255.255.0/24"
    assert j["twin"]["segments"]["added"] == []
    assert j["twin"]["segments"]["removed"] == []


# ---------------------------------------------------------------------------
# N3 -- an unreadable snapshot VERSION is unknown, never empty
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("version", [None, "axr-bundle-snapshot/2", "nonsense"])
def test_an_unknown_snapshot_version_is_omitted_not_reported_empty(tmp_path, version):
    """N3: a snapshot written by another axRange cannot be compared, and
    saying "nothing changed" about it is a claim rather than a measurement.
    `null` sides plus a warning; `{"added": [], ...}` would be a lie a
    consumer cannot distinguish from the truth."""
    from axrange.api import recompile as rc

    root = _copy_bundle(tmp_path, "mini-cast-prov")
    bundle = root / "mini-cast-prov"
    c = _client(_apply_settings(root, tmp_path), backend=_FakeRecompile())
    fp = c.post(
        "/api/twins/mini-cast-prov/apply", json={"document": _VALID_DOC},
    ).json()["fingerprint_before"]

    snap_path = rc.history_dir(bundle) / f"{fp}.json"
    snap = json.loads(snap_path.read_text())
    if version is None:
        del snap["schema"]
    else:
        snap["schema"] = version
    snap_path.write_text(json.dumps(snap))

    r = c.get("/api/twins/mini-cast-prov/diff", params={"since": fp})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["observed"] is None and j["twin"] is None
    assert j["snapshot_schema"] == version
    assert j["twin_config_changed"] is False
    assert any(rc.SNAPSHOT_SCHEMA in w for w in j["warnings"]), j["warnings"]


def test_a_missing_SECTION_is_omitted_with_its_own_warning(tmp_path):
    """The narrower half of N3: a snapshot of the right VERSION that is
    missing one side -- a shape only a hand edit or a partial write
    produces -- omits that side and keeps the other."""
    from axrange.api import recompile as rc

    root = _copy_bundle(tmp_path, "mini-cast-prov")
    bundle = root / "mini-cast-prov"
    c = _client(_apply_settings(root, tmp_path), backend=_FakeRecompile())
    fp = c.post(
        "/api/twins/mini-cast-prov/apply", json={"document": _VALID_DOC},
    ).json()["fingerprint_before"]

    snap_path = rc.history_dir(bundle) / f"{fp}.json"
    snap = json.loads(snap_path.read_text())
    del snap["twin"]
    snap_path.write_text(json.dumps(snap))

    j = c.get("/api/twins/mini-cast-prov/diff", params={"since": fp}).json()
    assert j["twin"] is None
    assert j["observed"] is not None
    assert any("`twin` side is absent" in w for w in j["warnings"]), j["warnings"]


def test_every_snapshot_this_build_writes_declares_its_version():
    """The version has to be WRITTEN, not merely read -- a snapshot recorded
    without one is the very artifact N3 exists to reject, and this build
    would then reject its own."""
    from axrange.api import recompile as rc
    from axrange.api.imprints import load_reference

    snap = rc.snapshot_bundle(
        load_reference("mini-cast-prov", CAST_FIXTURES), fingerprint="fp",
    )
    assert rc.snapshot_version(snap) == rc.SNAPSHOT_SCHEMA


# ---------------------------------------------------------------------------
# N1 -- the atomic writer does not re-narrow a git-checked-out file
# ---------------------------------------------------------------------------
def test_the_atomic_writer_creates_0644_and_preserves_an_existing_mode(tmp_path):
    """N1: `tempfile.mkstemp` makes 0600, and the atomic-write idiom carries
    the TEMP file's mode onto the target -- so every save left these two
    git-tracked documents owner-only, and re-narrowed a 0644 checkout on each
    subsequent write. They are documents a compiler, a reviewer and `git
    diff` all read; they are not secrets."""
    import os as _os
    import stat as _stat

    from axrange.api.console_files import DEFAULT_FILE_MODE, atomic_write

    target = tmp_path / "doc.yaml"
    atomic_write(target, "first\n")
    assert _stat.S_IMODE(target.stat().st_mode) == DEFAULT_FILE_MODE

    # An EXISTING mode is preserved, in both directions.
    for mode in (0o600, 0o664, 0o640):
        _os.chmod(target, mode)
        atomic_write(target, f"mode {mode:o}\n")
        assert _stat.S_IMODE(target.stat().st_mode) == mode, oct(mode)
        assert target.read_text() == f"mode {mode:o}\n"


def test_a_saved_assertions_file_is_readable_by_the_compilers_uid(tmp_path):
    """The same rule where it bites: `aximprint cast --assertions <path>` may
    run as another user, and a 0600 file it cannot read makes the apply fail
    with a permission error naming a file axRange just wrote."""
    import stat as _stat

    root = _copy_bundle(tmp_path, "mini-cast-prov")
    c = _client(_settings(root, tmp_path))
    assert c.put(
        "/api/twins/mini-cast-prov/assertions", json=_VALID_DOC,
    ).status_code == 200
    apath = root / "mini-cast-prov" / "mini-cast-prov.assertions.yaml"
    assert _stat.S_IMODE(apath.stat().st_mode) & 0o044, oct(apath.stat().st_mode)

    lpath = root / "mini-cast-prov" / "layout.json"
    assert c.put("/api/twins/mini-cast-prov/layout", json=_LAYOUT).status_code == 200
    assert _stat.S_IMODE(lpath.stat().st_mode) & 0o044, oct(lpath.stat().st_mode)


# ---------------------------------------------------------------------------
# N4 -- three etag placements, one answer
# ---------------------------------------------------------------------------
def test_apply_accepts_the_etag_inside_the_document_too(tmp_path):
    """N4: both PUTs accepted a document carrying its own `etag` -- which a
    GET payload does, and the SPA's save is "PUT what you read". Apply read
    only `req.etag`/`If-Match`, so the identical payload was guarded through
    one route and UNGUARDED through the other."""
    root = _copy_bundle(tmp_path, "mini-cast-prov")
    c = _client(_apply_settings(root, tmp_path), backend=_FakeRecompile())
    c.put("/api/twins/mini-cast-prov/assertions", json=_VALID_DOC)
    read = c.get("/api/twins/mini-cast-prov/assertions").json()
    assert read["etag"]

    # The literal GET payload as `document`, etag and all, with nothing
    # beside it -- and it is GUARDED, not ignored.
    r = c.post("/api/twins/mini-cast-prov/apply", json={"document": read})
    assert r.status_code == 200, r.text

    stale = dict(read, etag="0" * 64)
    r2 = c.post("/api/twins/mini-cast-prov/apply", json={"document": stale})
    assert r2.status_code == 412, r2.text


@pytest.mark.parametrize(
    "where",
    ["header vs body", "header vs document", "body vs document"],
)
def test_disagreeing_etags_are_refused_from_any_pair_of_places(tmp_path, where):
    root = _copy_bundle(tmp_path, "mini-cast-prov")
    c = _client(_apply_settings(root, tmp_path), backend=_FakeRecompile())
    etag = c.put("/api/twins/mini-cast-prov/assertions", json=_VALID_DOC).json()["etag"]
    other = "0" * 64

    doc = _doc(source="operator: second")
    body: dict = {"document": doc}
    headers: dict = {}
    if where == "header vs body":
        headers["If-Match"] = f'"{etag}"'
        body["etag"] = other
    elif where == "header vs document":
        headers["If-Match"] = f'"{etag}"'
        doc["etag"] = other
    else:
        body["etag"] = etag
        doc["etag"] = other

    r = c.post("/api/twins/mini-cast-prov/apply", json=body, headers=headers)
    assert r.status_code == 400, r.text
    assert "disagree" in r.json()["error"]
    # ... and all three placements AGREEING is accepted.
    ok = c.post(
        "/api/twins/mini-cast-prov/apply",
        json={"document": dict(_doc(source="operator: third"), etag=etag), "etag": etag},
        headers={"If-Match": f'"{etag}"'},
    )
    assert ok.status_code == 200, ok.text


# ---------------------------------------------------------------------------
# The remaining round-3 nits
# ---------------------------------------------------------------------------
def test_a_save_during_an_apply_is_409(tmp_path):
    """A plain save landing between an apply's own save and its recompile
    would make the apply report a document that is no longer on disk. 409
    (busy), not the etag guard's 412 (stale) -- nothing about the caller's
    etag is wrong."""
    from axrange.api.recompile import imprint_apply_lock

    root = _copy_bundle(tmp_path, "mini-cast-prov")
    c = _client(_settings(root, tmp_path))
    with imprint_apply_lock("mini-cast-prov"):
        r = c.put("/api/twins/mini-cast-prov/assertions", json=_VALID_DOC)
        assert r.status_code == 409, r.text
        assert "apply" in r.json()["error"]
        assert not (root / "mini-cast-prov" / "mini-cast-prov.assertions.yaml").exists()
        # A DIFFERENT imprint is unaffected -- the guard is per-imprint.
        assert c.put(
            "/api/twins/nope/assertions", json=_VALID_DOC,
        ).status_code == 404
    assert c.put(
        "/api/twins/mini-cast-prov/assertions", json=_VALID_DOC,
    ).status_code == 200


def test_a_failed_install_says_the_bundle_may_be_partially_installed(tmp_path, monkeypatch):
    """Two stages means two places to fail, and they have different blast
    radii: a failed `cast` changed nothing, a failed `install-cast` may have
    replaced some of the bundle's files and not others. An operator reading a
    502 needs to be told which."""
    root = _copy_bundle(tmp_path, "mini-cast-prov")

    from axrange.api import recompile as rc

    class _InstallFails:
        def recompile(self, request):
            result = rc.RecompileResult(
                argv=["aximprint", "cast"], returncode=0,
                stdout=json.dumps({"nodes": 1}),
            )
            rc._parse_stdout(result)
            rc.SubprocessRecompile._check(
                result, 1, "is not a host in target twin",
                stage=rc.INSTALL_VERB, partial=True,
            )

    c = _client(_apply_settings(root, tmp_path), backend=_InstallFails())
    r = c.post("/api/twins/mini-cast-prov/apply", json={"document": _VALID_DOC})
    assert r.status_code == 502, r.text
    assert "PARTIALLY INSTALLED" in r.json()["error"]
    # ... and a stage-1 failure says no such thing. (The first apply saved,
    # so this one carries the etag it produced.)
    etag = r.json()["detail"]["etag"]
    boom = rc.RecompileFailedError(
        "cast failed (exit 1): duckdb: no such store",
        result=rc.RecompileResult(argv=["fake"], returncode=1, stderr="x"),
    )
    c2 = _client(_apply_settings(root, tmp_path), backend=_FakeRecompile(raise_=boom))
    r2 = c2.post(
        "/api/twins/mini-cast-prov/apply",
        json={"document": _VALID_DOC, "etag": etag},
    )
    assert r2.status_code == 502, r2.text
    assert "PARTIALLY INSTALLED" not in r2.json()["error"]


def test_a_missing_schema_artifact_fails_at_startup_not_at_the_first_save():
    """`console_files` derives the `axr-assertions/2` closed sets from a
    vendored JSON file at import time. A mis-built wheel without it would
    otherwise surface as a 500 on an operator's first `PUT .../assertions`,
    hours after `axrange serve` reported ready -- so `create_app` touches the
    module."""
    import inspect

    from axrange.api import app as app_mod

    src = inspect.getsource(app_mod)
    assert "console_files" in src
    assert "_console_files.ASSERTIONS_SCHEMA" in src


def test_read_cast_invocation_does_not_re_read_an_already_loaded_export(tmp_path):
    """The nit, with the number that motivates it: the real export is 732 KB,
    and the apply path had already parsed it before asking for seven scalars
    out of it. Validation is identical either way -- the shortcut skips the
    READ, never the checks."""
    from axrange.api import recompile as rc
    from axrange.api.imprints import load_reference

    bundle = load_reference("mini-cast-prov", CAST_FIXTURES)
    assert isinstance(bundle.cast_invocation, dict)

    from_block = rc.read_cast_invocation(
        Path("/nonexistent"), "mini-cast-prov", block=bundle.cast_invocation,
    )
    from_disk = rc.read_cast_invocation(
        CAST_FIXTURES / "mini-cast-prov", "mini-cast-prov",
    )
    assert from_block == from_disk

    # The checks still run on the handed-in block.
    with pytest.raises(rc.RecompileInvocationError):
        rc.read_cast_invocation(
            Path("/nonexistent"), "x", block={"pit_id": "pit-1; rm -rf /"},
        )


def test_a_non_numeric_timeout_setting_is_a_typed_error(monkeypatch):
    """A bad env value should name the KEY, not show an operator a traceback
    whose only clue is the word `float`."""
    from axrange.api import recompile as rc
    from axrange.api.settings import ApiSettings, ApiSettingsError

    for bad in ("soon", "12s", "0", "-5"):
        monkeypatch.setenv(rc.SETTING_TIMEOUT, bad)
        with pytest.raises(ApiSettingsError) as e:
            ApiSettings(db_path="x")
        assert rc.SETTING_TIMEOUT in str(e.value), bad
    # Empty/unset falls back to the default rather than refusing.
    monkeypatch.setenv(rc.SETTING_TIMEOUT, "")
    assert ApiSettings(db_path="x").aximprint_timeout_s == rc.DEFAULT_TIMEOUT_S
    monkeypatch.delenv(rc.SETTING_TIMEOUT)
    assert ApiSettings(db_path="x").aximprint_timeout_s == rc.DEFAULT_TIMEOUT_S


def test_the_history_writer_uses_the_public_atomic_writer():
    """The nit: reaching across a module boundary for a `_`-private name is
    how two writers end up with one and a half of the same invariant."""
    import inspect

    from axrange.api import recompile as rc

    assert "_atomic_write" not in inspect.getsource(rc.write_history_snapshot)
    assert rc.atomic_write is not None


# ===========================================================================
# S2 -- the authored-key join (§12.6) and the SPA co-serve
# (`docs/WORK_ORDER_console_canvas.md` S2; the decision record for the join
# is `o2/console/README.md`)
# ===========================================================================
def _suppress_in_sidecar(bundle: Path, bundle_id: str, *keys: str) -> None:
    """Doctor a bundle's sidecar so the compiler reports `keys` as suppressed
    -- what a real recompile of a file carrying those `suppress` records
    writes back. The suite never runs the real compiler (LES-242's seam), so
    the producer's side of the change is staged here."""
    path = bundle / f"{bundle_id}.provenance.json"
    doc = json.loads(path.read_text())
    doc["population"]["suppressed"] = [
        {"key": k, "note": "n", "placed": True, "matched": True} for k in keys
    ]
    path.write_text(json.dumps(doc))


def test_the_diff_names_the_authored_key_a_suppression_used(tmp_path):
    """§12.6: the twin side is keyed by TWIN node ids, so a `suppress` the
    operator keyed on `address:10.1.0.14` shows up there as some
    `generic-endpoint-*` host disappearing -- and the canvas could not join
    the two from the diff alone. `suppressed_keys` is that join."""
    from axrange.api import recompile as rc

    root = _copy_bundle(tmp_path, "mini-cast-prov")
    bundle = root / "mini-cast-prov"
    c = _client(_apply_settings(root, tmp_path), backend=_FakeRecompile())
    fp = c.post(
        "/api/twins/mini-cast-prov/apply", json={"document": _VALID_DOC},
    ).json()["fingerprint_before"]
    assert rc.list_history(bundle) == [fp]

    _suppress_in_sidecar(bundle, "mini-cast-prov", "address:10.1.0.14")

    j = c.get("/api/twins/mini-cast-prov/diff", params={"since": fp}).json()
    assert j["suppressed_keys"] == {
        "added": ["address:10.1.0.14"], "removed": [], "changed": [],
    }


def test_the_diff_reports_an_UNsuppression_too(tmp_path):
    """Removing a `suppress` record brings a host back; the key moves to
    `removed`, so the canvas can retire the mark it was carrying."""
    root = _copy_bundle(tmp_path, "mini-cast-prov")
    bundle = root / "mini-cast-prov"
    _suppress_in_sidecar(bundle, "mini-cast-prov", "address:10.1.0.14")
    c = _client(_apply_settings(root, tmp_path), backend=_FakeRecompile())
    fp = c.post(
        "/api/twins/mini-cast-prov/apply", json={"document": _VALID_DOC},
    ).json()["fingerprint_before"]

    _suppress_in_sidecar(bundle, "mini-cast-prov")     # the record is gone
    j = c.get("/api/twins/mini-cast-prov/diff", params={"since": fp}).json()
    assert j["suppressed_keys"]["removed"] == ["address:10.1.0.14"]
    assert j["suppressed_keys"]["added"] == []


def test_an_unchanged_suppression_set_is_present_and_empty(tmp_path):
    """Present-and-empty ("compared, nothing moved"), never `null` ("could
    not compare") -- the same contract `observed`/`twin` carry."""
    root = _copy_bundle(tmp_path, "mini-cast-prov")
    c = _client(_apply_settings(root, tmp_path), backend=_FakeRecompile())
    fp = c.post(
        "/api/twins/mini-cast-prov/apply", json={"document": _VALID_DOC},
    ).json()["fingerprint_before"]
    j = c.get("/api/twins/mini-cast-prov/diff", params={"since": fp}).json()
    assert j["suppressed_keys"] == {"added": [], "removed": [], "changed": []}


def test_the_authored_key_join_is_NULL_when_a_side_has_no_accounting(tmp_path):
    """A bundle with no canonical sidecar cannot say "nothing is suppressed",
    and saying it anyway would let the canvas mark an edit applied on the
    strength of an absence (LES-240)."""
    from axrange.api import recompile as rc

    root = _copy_bundle(tmp_path, "mini-cast-prov")
    bundle = root / "mini-cast-prov"
    c = _client(_apply_settings(root, tmp_path), backend=_FakeRecompile())
    fp = c.post(
        "/api/twins/mini-cast-prov/apply", json={"document": _VALID_DOC},
    ).json()["fingerprint_before"]

    snap_path = rc.history_dir(bundle) / f"{fp}.json"
    snap = json.loads(snap_path.read_text())
    del snap["population"]                     # an older, pre-accounting snapshot
    snap_path.write_text(json.dumps(snap))

    j = c.get("/api/twins/mini-cast-prov/diff", params={"since": fp}).json()
    assert j["suppressed_keys"] is None
    warned = " ".join(j["warnings"])
    assert "population.suppressed" in warned
    assert "recorded snapshot" in warned
    # ... and the OTHER sides still compared: one absent input does not
    # blank the whole answer.
    assert j["twin"] is not None


def test_the_authored_key_join_names_the_CURRENT_bundle_when_it_is_the_gap(tmp_path):
    root = _copy_bundle(tmp_path, "mini-cast-prov")
    bundle = root / "mini-cast-prov"
    c = _client(_apply_settings(root, tmp_path), backend=_FakeRecompile())
    fp = c.post(
        "/api/twins/mini-cast-prov/apply", json={"document": _VALID_DOC},
    ).json()["fingerprint_before"]

    (bundle / "mini-cast-prov.provenance.json").unlink()
    j = c.get("/api/twins/mini-cast-prov/diff", params={"since": fp}).json()
    assert j["suppressed_keys"] is None
    assert "current bundle" in " ".join(j["warnings"])


def test_suppressed_key_delta_accepts_both_row_shapes():
    """`_population_of` reduces the sidecar's rows to a key list, but a
    snapshot written by hand (or by an older build) may carry the raw
    `{key, note, placed, matched}` rows. Both are the same key set."""
    from axrange.api import recompile as rc

    rows = {"population": {"suppressed": [{"key": "address:10.0.0.1", "matched": True}]}}
    strings = {"population": {"suppressed": ["address:10.0.0.1"]}}
    section, warnings = rc.suppressed_key_delta(strings, rows)
    assert section == {"added": [], "removed": [], "changed": []}
    assert warnings == []
    section, _ = rc.suppressed_key_delta({"population": {"suppressed": []}}, rows)
    assert section["added"] == ["address:10.0.0.1"]


# ---------------------------------------------------------------------------
# The SPA co-serve: `axrange serve --spa-dir` must survive a RELOAD on a deep
# client route, because the Console's URL is its zoom (`15` §7).
# ---------------------------------------------------------------------------
_INDEX_HTML = "<!doctype html><html><body><div id=\"root\"></div></body></html>"


def _spa_dir(tmp_path: Path) -> Path:
    d = tmp_path / "dist"
    (d / "assets").mkdir(parents=True)
    (d / "index.html").write_text(_INDEX_HTML)
    (d / "assets" / "app.js").write_text("console.log('spa');")
    return d


def _spa_client(tmp_path: Path) -> TestClient:
    s = _settings(CAST_FIXTURES, tmp_path)
    s.spa_dir = str(_spa_dir(tmp_path))
    return _client(s)


def test_the_spa_is_served_at_the_root(tmp_path):
    r = _spa_client(tmp_path).get("/")
    assert r.status_code == 200
    assert r.text == _INDEX_HTML


def test_a_deep_client_route_falls_back_to_index_html(tmp_path):
    """The bug this closes: `StaticFiles(html=True)` serves `/` and 404s
    everything else, so every Console URL worked when navigated to in-app and
    404'd on reload, bookmark or paste."""
    c = _spa_client(tmp_path)
    for path in (
        "/imprints/corpapcn-golden",
        # The CIDR route is the regression case: its last path segment is
        # FULL of dots, so any "a dot means an asset" heuristic 404s it.
        "/imprints/corpapcn-golden/segments/10.80.20.0-24",
        "/imprints/corpapcn-golden/segments/10.80.20.0-24/hosts/address:10.1.10.222",
        "/imprints/corpapcn-golden/matrix",
        "/imprints/corpapcn-golden?ghosts=1&class=imputed",
    ):
        r = c.get(path)
        assert r.status_code == 200, path
        assert r.text == _INDEX_HTML, path


def test_a_real_asset_is_still_served_as_itself(tmp_path):
    r = _spa_client(tmp_path).get("/assets/app.js")
    assert r.status_code == 200
    assert "console.log" in r.text


def test_a_MISSING_asset_stays_a_404_and_never_becomes_the_index(tmp_path):
    """Answering a stale/broken build with HTML and a 200 leaves the browser
    reporting a MIME-type error and the operator hunting the wrong bug."""
    r = _spa_client(tmp_path).get("/assets/does-not-exist.js")
    assert r.status_code == 404
    assert "<!doctype html>" not in r.text.lower()


def test_an_unknown_API_path_never_falls_back_to_the_spa(tmp_path):
    """A typo'd endpoint answered with the SPA's HTML and a 200 is a silent
    success: the caller parses HTML as JSON and reports something unrelated."""
    c = _spa_client(tmp_path)
    for path in ("/api/nonexistent", "/api/twins/mini-cast-prov/nonexistent"):
        r = c.get(path)
        assert r.status_code == 404, path
        assert "<div id=" not in r.text, path


def test_the_api_is_intact_beside_the_mounted_spa(tmp_path):
    c = _spa_client(tmp_path)
    assert c.get("/api/health").status_code == 200
    ids = {e["id"] for e in c.get("/api/twins").json()["imprints"]}
    assert "mini-cast-prov" in ids
    assert c.get("/api/twins/mini-cast-prov/cast").json()["segments"] > 0


def test_headless_is_unchanged_when_no_spa_dir_is_set(tmp_path):
    """The default posture (`docs/design/11-api-ui.md`): the API runs without
    a UI for the AI core / CI, and `/` is not a route."""
    c = _client(_settings(CAST_FIXTURES, tmp_path))
    assert c.get("/").status_code == 404
    assert c.get("/imprints/anything").status_code == 404


def test_a_spa_dir_that_is_not_a_directory_is_a_no_op(tmp_path):
    s = _settings(CAST_FIXTURES, tmp_path)
    s.spa_dir = str(tmp_path / "never-built")
    c = _client(s)
    assert c.get("/").status_code == 404
    assert c.get("/api/health").status_code == 200


def test_a_suppression_that_moved_NOTHING_else_is_still_movement(tmp_path):
    """A `suppress` naming a key no entity carries leaves both populations
    untouched. Warning the canvas that there is "nothing to highlight" while
    `suppressed_keys` names that key is the same silence G2 fixed for the
    twin side -- so the key set counts as movement."""
    root = _copy_bundle(tmp_path, "mini-cast-prov")
    bundle = root / "mini-cast-prov"
    c = _client(_apply_settings(root, tmp_path), backend=_FakeRecompile())
    fp = c.post(
        "/api/twins/mini-cast-prov/apply", json={"document": _VALID_DOC},
    ).json()["fingerprint_before"]

    # The sidecar records the suppression; the twin config's bytes move too
    # (the compiler rewrote it), but no host, segment or conduit changed.
    _suppress_in_sidecar(bundle, "mini-cast-prov", "hostname:never-observed")
    tc = bundle / "mini-cast-prov.yaml"
    tc.write_text(tc.read_text() + "\n# recompiled\n")

    j = c.get("/api/twins/mini-cast-prov/diff", params={"since": fp}).json()
    assert j["twin_config_changed"] is True
    assert j["suppressed_keys"]["added"] == ["hostname:never-observed"]
    assert "nothing for the canvas to highlight" not in " ".join(j["warnings"])


# ===========================================================================
# S2, tester pass -- the authored-key join at UNIT level (hand-built
# snapshots, no bundle, no apply), and the SPA fallback's edges.
#
# The API-level tests above drive `suppressed_key_delta` through a real
# bundle + a real apply, which is the right integration proof but reaches the
# function only through one snapshot shape. These pin the function's own
# contract on inputs a real producer can and does emit: raw rows, bare
# strings, duplicates, a missing accounting, and a `population` that is not a
# mapping at all.
# ===========================================================================
def test_suppressed_key_delta_reports_added_removed_and_never_changed():
    """`changed` is ALWAYS empty: a key is a member of a set, not a record
    with fields (the DTO's own note). Added/removed are the two answers."""
    from axrange.api import recompile as rc

    before = {"population": {"suppressed": ["address:10.0.0.1", "hostname:b"]}}
    after = {"population": {"suppressed": ["hostname:b", "mac:aa:bb:cc:dd:ee:ff"]}}
    section, warnings = rc.suppressed_key_delta(before, after)
    assert section == {
        "added": ["mac:aa:bb:cc:dd:ee:ff"],
        "removed": ["address:10.0.0.1"],
        "changed": [],
    }
    assert warnings == []


def test_suppressed_key_delta_is_a_SET_delta_sorted_and_deduped():
    """Two `suppress` records naming the same key (a hand-edited file the
    server accepts) must not report the key twice, and the order must not
    depend on the sidecar's row order -- the canvas diffs these against its
    own pending set."""
    from axrange.api import recompile as rc

    section, _ = rc.suppressed_key_delta(
        {"population": {"suppressed": []}},
        {"population": {"suppressed": ["hostname:z", "address:10.0.0.1",
                                       "hostname:z"]}},
    )
    assert section["added"] == ["address:10.0.0.1", "hostname:z"]


def test_suppressed_key_delta_skips_a_row_it_cannot_read_rather_than_failing():
    """A sidecar row that is neither a string nor `{key: str}` is not a key.
    Dropping it keeps the endpoint answering; raising would take the whole
    diff down over one malformed row a future producer added."""
    from axrange.api import recompile as rc

    section, _ = rc.suppressed_key_delta(
        {"population": {"suppressed": []}},
        {"population": {"suppressed": [
            {"key": "address:10.0.0.1"},      # the 2026-09 row shape
            "hostname:legacy",                # the older bare-string shape
            {"note": "no key at all"},        # unreadable
            42,                               # unreadable
            None,                             # unreadable
        ]}},
    )
    assert section["added"] == ["address:10.0.0.1", "hostname:legacy"]


@pytest.mark.parametrize(
    "population",
    [
        None,                                   # no `population` member
        "not-a-mapping",                        # a scalar where a map belongs
        [],                                     # a list where a map belongs
        {},                                     # a map with no `suppressed`
        {"suppressed": None},                   # present but null
        {"suppressed": "address:10.0.0.1"},     # a string, not a list
        {"suppressed": {"address:10.0.0.1": True}},   # a map, not a list
    ],
)
def test_an_unreadable_accounting_is_NULL_never_an_empty_set(population):
    """`null` is "could not compare"; `[]` would be the claim "nothing is
    suppressed", and the canvas marks edits applied off that difference
    (LES-240). Every shape that is not a list of rows is an absence."""
    from axrange.api import recompile as rc

    good = {"population": {"suppressed": ["address:10.0.0.1"]}}
    bad = {} if population is None else {"population": population}
    section, warnings = rc.suppressed_key_delta(bad, good)
    assert section is None and warnings
    section, warnings = rc.suppressed_key_delta(good, bad)
    assert section is None and warnings


def test_the_null_warning_names_WHICH_side_lacks_the_accounting():
    """Two very different operator actions -- "your snapshot predates this"
    vs "this bundle has no canonical sidecar" -- so the warning must say
    which, not just that the comparison failed."""
    from axrange.api import recompile as rc

    good = {"population": {"suppressed": []}}
    _, before_missing = rc.suppressed_key_delta({}, good)
    _, after_missing = rc.suppressed_key_delta(good, {})
    assert "recorded snapshot" in before_missing[0]
    assert "current bundle" in after_missing[0]
    # ... and when BOTH are missing it names the snapshot (the side an
    # operator can do nothing about) rather than saying nothing.
    _, both = rc.suppressed_key_delta({}, {})
    assert "recorded snapshot" in both[0]


def test_two_empty_accountings_compare_to_an_empty_section():
    """Present-and-empty on both sides is a real comparison with a real
    answer: nothing moved."""
    from axrange.api import recompile as rc

    empty = {"population": {"suppressed": []}}
    assert rc.suppressed_key_delta(empty, empty) == (
        {"added": [], "removed": [], "changed": []}, [],
    )


# ---------------------------------------------------------------------------
# The SPA history fallback, adversarially.
#
# `_SpaFiles` turns a 404 into `index.html`, which is exactly the behavior a
# path-traversal probe would love to hide behind: the answer looks identical
# whether the server refused the traversal or served the file. These pin that
# nothing outside `--spa-dir` is ever in the body, and that the fallback's
# three documented exclusions (an API path, an asset suffix, a missing
# `index.html`) hold on inputs a browser and a scanner both send.
# ---------------------------------------------------------------------------
_SECRET = "PROXMOX_PASSWORD=do-not-serve-me"


def _spa_dir_with_a_secret_sibling(tmp_path: Path) -> Path:
    """A built SPA whose PARENT directory holds a file that must never be
    served -- the shape of the real deployment, where `o2/console/dist` sits
    inside a repo that also holds `adapter.conf`."""
    d = _spa_dir(tmp_path)
    (d.parent / "adapter.conf").write_text(_SECRET)
    return d


def _traversal_client(tmp_path: Path) -> TestClient:
    s = _settings(CAST_FIXTURES, tmp_path)
    s.spa_dir = str(_spa_dir_with_a_secret_sibling(tmp_path))
    return _client(s)


@pytest.mark.parametrize(
    "path",
    [
        "/../adapter.conf",
        "/assets/../../adapter.conf",
        "/%2e%2e/adapter.conf",
        "/imprints/%2e%2e/%2e%2e/adapter.conf",
        "/assets/%2e%2e%2f%2e%2e%2fadapter.conf",
        "/....//adapter.conf",
        "/%252e%252e/adapter.conf",
    ],
)
def test_no_traversal_reaches_a_file_outside_the_spa_dir(tmp_path, path):
    """The fallback must not become a file-read primitive. Whatever the
    status, the one thing that may never appear is the sibling file's bytes."""
    r = _traversal_client(tmp_path).get(path)
    assert _SECRET not in r.text, path
    assert r.status_code in (200, 404), (path, r.status_code)
    if r.status_code == 200:
        # A refused traversal that falls back is the SPA's own index, and
        # nothing else -- not a directory listing, not another file.
        assert r.text == _INDEX_HTML, path


def test_a_dot_segment_that_stays_INSIDE_the_dir_still_serves_the_asset(tmp_path):
    """The mirror of the test above: `assets/../assets/app.js` normalizes to
    a path inside the root, and refusing it would break a legitimate (if
    ugly) relative URL a bundler can emit."""
    r = _traversal_client(tmp_path).get("/assets/%2e%2e%2fassets/app.js")
    assert r.status_code == 200
    assert "console.log" in r.text


def test_index_html_is_served_as_itself(tmp_path):
    r = _spa_client(tmp_path).get("/index.html")
    assert r.status_code == 200
    assert r.text == _INDEX_HTML


def test_a_missing_index_html_is_a_404_not_a_mystery(tmp_path):
    """The docstring's claim, pinned: a half-built or emptied `--spa-dir`
    reports as MISSING. A 200 with an empty body, or a 500, would send the
    operator looking at the router instead of at the build."""
    s = _settings(CAST_FIXTURES, tmp_path)
    d = tmp_path / "half-built"
    (d / "assets").mkdir(parents=True)
    (d / "assets" / "app.js").write_text("console.log('spa');")
    s.spa_dir = str(d)
    c = _client(s)
    for path in ("/", "/imprints/corpapcn-golden"):
        r = c.get(path)
        assert r.status_code == 404, path
    # ... and the API beside it is unaffected by the broken build.
    assert c.get("/api/health").status_code == 200


@pytest.mark.parametrize(
    "path",
    [
        "/assets/missing.JS",           # the suffix match is case-insensitive
        "/assets/missing.CSS",
        "/assets/missing.WOFF2",
        "/assets/missing.js?v=cachebust",   # a cache-busted asset URL
        "/vite.svg",                    # an asset at the root, not under /assets
        "/favicon.ico",
    ],
)
def test_an_asset_suffix_miss_stays_a_404_however_it_is_spelled(tmp_path, path):
    """The asset list is matched `.lower()`ed, and a query string is not part
    of the path -- both worth pinning, because either one silently answering
    a broken build with HTML and a 200 is the failure `_SPA_ASSET_SUFFIXES`
    exists to prevent."""
    r = _spa_client(tmp_path).get(path)
    assert r.status_code == 404, path
    assert "<div id=" not in r.text, path


def test_a_write_METHOD_on_a_client_route_is_never_answered_with_the_spa(tmp_path):
    """A POST that came back 200 text/html would tell a caller its write
    succeeded. Starlette's own 405 is the right answer and the fallback must
    not convert it -- the handler only rewrites a 404."""
    c = _spa_client(tmp_path)
    for method in ("post", "put", "delete", "patch"):
        r = getattr(c, method)("/imprints/corpapcn-golden")
        assert r.status_code == 405, method
        assert "<div id=" not in r.text, method


def test_HEAD_on_a_deep_route_answers_the_index_with_no_body(tmp_path):
    r = _spa_client(tmp_path).head("/imprints/corpapcn-golden/matrix")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert r.text == ""


def test_a_very_long_client_route_is_answered_not_crashed(tmp_path):
    """A 4 KB segment and a 1000-segment path both reach `_spa_wants_index`;
    neither may raise out of the mount."""
    c = _spa_client(tmp_path)
    assert c.get("/imprints/" + "a" * 4000).status_code == 200
    assert c.get("/imprints/y" + "/x" * 1000).status_code == 200


@pytest.mark.parametrize(
    "path", ["/api", "/api/", "/api/nonexistent", "/api/twins/x/nonexistent"],
)
def test_every_api_shaped_path_refuses_the_fallback(tmp_path, path):
    """`/api` bare and `/api/` trailing-slash reach the mount the same way an
    `/api/typo` does; all three must stay JSON 404s, never a 200 of HTML a
    caller would parse as JSON."""
    r = _spa_client(tmp_path).get(path)
    assert r.status_code == 404, path
    assert "<div id=" not in r.text, path


def test_the_fallback_never_hides_a_non_404(tmp_path):
    """Only a 404 is rewritten. A 405 (above) and any other status must
    propagate -- otherwise a real failure reads as a working page."""
    import inspect

    from axrange.api import app as app_mod

    src = inspect.getsource(app_mod._maybe_mount_spa)
    assert "exc.status_code != 404" in src


@pytest.mark.parametrize(
    "path,wants",
    [
        ("", True),                       # `/` -- the index itself
        ("imprints/corpapcn-golden", True),
        ("imprints/x/segments/10.80.20.0-24", True),   # dots in the LAST segment
        ("imprints/x/hosts/address:10.1.10.222", True),
        ("api", False),
        ("api/imprints", False),
        ("apiary/not-the-api", True),     # the prefix test is `api/`, not `api`
        ("assets/index-abc123.js", False),
        ("assets/index-abc123.JS", False),
        ("assets/style.css", False),
        ("robots.txt", False),
        ("imprints/x/hosts/hostname:report.css", False),  # the stated residual
    ],
)
def test_spa_wants_index_decides_by_prefix_and_suffix_only(path, wants):
    """The predicate itself, including the residual its own docstring names:
    an entity key ending in an asset suffix does NOT fall back. Pinned so the
    day it bites someone, the test says it was a known trade, not a bug."""
    from axrange.api.app import _spa_wants_index

    assert _spa_wants_index(path) is wants, path


# ---------------------------------------------------------------------------
# S2 review: the SPA's cache policy, the decoded-path exclusion, and the
# hand-written DTO mirror's field set
# ---------------------------------------------------------------------------
def test_index_html_is_revalidated_and_hashed_assets_are_immutable(tmp_path):
    """Vite emits `assets/index-<contenthash>.js`, so an asset URL changes
    whenever its bytes do -- those may be cached forever. `index.html` names
    the current hashes and must not be, or a browser holding yesterday's index
    asks for an asset the new build deleted and the Console boots to a blank
    page after every deploy."""
    c = _spa_client(tmp_path)
    for path in ("/", "/index.html", "/imprints/x/segments/10.80.20.0-24"):
        r = c.get(path)
        assert r.status_code == 200, path
        assert r.headers["Cache-Control"] == "no-cache", path
    asset = c.get("/assets/app.js")
    assert asset.status_code == 200
    assert asset.headers["Cache-Control"] == "public, max-age=31536000, immutable"
    # A 404 asserts no cache policy at all -- caching a miss would outlive the
    # rebuild that fixes it.
    assert "Cache-Control" not in c.get("/assets/does-not-exist.js").headers


def test_the_api_exclusion_runs_on_the_DECODED_path(tmp_path):
    """`/%61pi/...` is `/api/...` by the time the app routes it (LES-236: a
    guard tested only against its literal form is tested against the one input
    an attacker will not send). If the SPA fallback matched on the raw path, a
    percent-encoded API URL would answer with the Console's HTML and a 200."""
    r = _spa_client(tmp_path).get("/%61pi/nonexistent")
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/json")


def test_HEAD_on_a_deep_client_route_is_a_200_with_no_body(tmp_path):
    """A link checker, a proxy health probe and a browser preflight all send
    HEAD; answering 404 there would report the Console as broken."""
    r = _spa_client(tmp_path).head("/imprints/corpapcn-golden/segments/10.80.20.0-24")
    assert r.status_code == 200
    assert r.content == b""
    assert r.headers["content-type"].startswith("text/html")


#: The exact field set of every DTO the Console SPA mirrors BY HAND in
#: `o2/console/src/api/types.ts` (seam 1 of `docs/WORK_ORDER_console_canvas.md`
#: §9). Spelled as literals, deliberately: deriving them from the DTOs would
#: make this test agree with itself no matter what changed, which is the one
#: thing it must not do. Aliases are used where the wire name differs from the
#: python name (`schema_` -> `schema`).
_MIRRORED_DTO_FIELDS: dict[str, set[str]] = {
    "ImprintListDTO": {"imprints", "skipped"},
    "ImprintListItemDTO": {
        "apply_blocked_reason", "as_of", "can_apply", "cast_from", "confidence_source",
        "has_provenance", "has_topology_export", "hosts", "id", "mean_confidence",
        "segments",
    },
    "ImprintSkippedDTO": {"id", "reason"},
    "CastSummaryDTO": {
        "bundle_dir", "bundle_fingerprint", "bundle_schema", "census",
        "code_fingerprint", "conduit_roster", "conduits", "deploy_min_occupancy",
        "deploy_min_occupancy_source", "description", "evidence_source",
        "has_assertions", "has_layout", "has_provenance",
        "has_topology_export", "hosts", "id", "node_specs",
        "node_roster", "observed_edges", "observed_nodes", "observed_segments", "params",
        "pit_id", "provenance_path", "provenance_schema", "routers",
        "segment_roster", "segments", "provenance",
        "topology_schema_version", "twin_config_id", "vms", "warnings",
    },
    # R5 (GAP-079): one roster entry, under its WIRE name `from`.
    "ConduitDTO": {"from", "to"},
    # Unified Console: the node roster behind `node_specs` (declared-design map).
    "CastNodeDTO": {"count", "kind", "name", "node_ids", "role", "segments", "members"},
    # Console S5.1.a: one segment-roster entry (`CastSegment` in types.ts).
    "CastSegmentDTO": {"cidr", "name", "zone_type", "observed_cidr"},
    # R5, per router since the GAP-082 review (S5): `CastRouter` in types.ts.
    "RouterDTO": {"name", "segments"},
    # R7 (GAP-082) made the summary route serve cast bundles; its mirror
    # (`ImprintSummary`, `ImprintSegment`) was not pinned here before.
    "ImprintSummaryDTO": {
        "apply_blocked_reason", "as_of", "can_apply",
        "confidence_source", "days_observed", "hosts", "id",
        "mean_confidence", "out_of_vocab_pct", "personas", "pit_id", "segments",
        "services", "provenance", "store", "topology_segments",
        "warnings", "windows",
    },
    "ImprintSegmentDTO": {"cidr", "confidence", "host_count", "name"},
    # `confirm` / `doubt` (axr-assertions/2, docs/design/topology-layers.md §3.2).
    "AssertionsDocDTO": {
        "add", "as_of", "confirm", "doubt", "etag", "exists", "imprint", "imprint_id",
        "override", "path", "schema", "source", "suppress", "warnings",
    },
    # `plane` (R2): the layout-plane stamp the pins were authored against.
    "LayoutDocDTO": {
        "annotations", "etag", "exists", "imprint", "imprint_id", "path", "pins",
        "plane", "schema",
    },
    # GAP-087/GAP-088: `code_fingerprint_before/after` became
    # `pit_code_fingerprint_*` (what it always held), and the build stamp that
    # actually moves joined it as `cast_code_fingerprint_*` with a
    # three-valued `compiler_changed` + the server's `compiler_note`.
    "ImprintApplyResponse": {
        "argv", "assertions", "cast_code_fingerprint_after",
        "cast_code_fingerprint_before", "cast_counts", "changed",
        "compiler_changed", "compiler_note",
        "fingerprint_after", "fingerprint_before", "history_written",
        "imprint_id", "install_argv", "install_returncode",
        "pit_code_fingerprint_after", "pit_code_fingerprint_before",
        "record_counts", "returncode", "stderr", "stdout_json", "warnings",
    },
    "ImprintDiffDTO": {
        "current_fingerprint", "imprint_id", "observed", "since",
        "snapshot_schema", "source", "suppressed_keys", "twin",
        "twin_config_changed", "warnings",
    },
    "ImprintProvenanceDTO": {
        "code_fingerprint", "evidence_source", "imprint_id", "population",
        "provenance", "rows", "sidecar_schema", "warnings",
    },
}


@pytest.mark.parametrize("dto_name", sorted(_MIRRORED_DTO_FIELDS))
def test_a_console_dto_field_set_matches_the_hand_written_typescript_mirror(dto_name):
    """The SPA's `types.ts` is a HAND-WRITTEN mirror with no code generation,
    so nothing else notices when the two drift -- and the drift is silent in
    the direction that matters: a field added here is simply invisible to the
    Console, and a field renamed here reads as `undefined` in a pane.

    This is the cheap half of the guard; `o2/console/test/live-api.test.ts`
    is the other half (it checks the mirror against payloads a real server
    produced, and is skipped without one)."""
    from axrange.api import dtos as _dtos

    model = getattr(_dtos, dto_name)
    actual = {(f.alias or name) for name, f in model.model_fields.items()}
    expected = _MIRRORED_DTO_FIELDS[dto_name]
    assert actual == expected, (
        f"{dto_name}'s field set changed: "
        f"added={sorted(actual - expected)} removed={sorted(expected - actual)}. "
        "This DTO is mirrored BY HAND in `o2/console/src/api/types.ts` -- "
        "update the mirror (and this literal) together, or the Console reads "
        "`undefined` for the new shape."
    )


# ===========================================================================
# R7 (GAP-082) -- `docs/WORK_ORDER_console_level0_rendering.md` R7, decision
# D3 "tolerate on read" (Authority: user, 2026-09-11). The canonical id (the
# directory name = `twin_config.id` = the URL id) is the ONE addressing id;
# a baked export/sidecar id that differs is published in `warnings`, never
# refused, never a 5xx.
# ===========================================================================
from axrange.api.imprints import default_fixtures_dir as _packaged_imprints  # noqa: E402

REAL_DISCO = REAL_CAST_ROOT / "pit-395a61f04e-1780405200"
requires_real_disco = pytest.mark.skipif(
    not REAL_DISCO.is_dir(),
    reason="twins/pit-395a61f04e-1780405200 not present",
)

_CONSOLE_READ_ROUTES = ("", "/cast", "/topology", "/provenance", "/assertions", "/layout")


def _lenient_client(settings: ApiSettings) -> TestClient:
    """A client that REPORTS a 500 as a status instead of raising, so "no 5xx
    remains on this class" is an assertion rather than a traceback."""
    return TestClient(create_app(settings), raise_server_exceptions=False)


def _catalogs() -> list[Path]:
    """Every catalog that ships: the two test cast bundles, the packaged
    fixture, and the real cast bundles when present."""
    roots = [CAST_FIXTURES, _packaged_imprints()]
    if REAL_CAST_ROOT.is_dir():
        roots.append(REAL_CAST_ROOT)
    return roots


def _assert_served_consistently(c: TestClient, imprint: str, baked: str) -> dict:
    """The GAP-082 contract for one bundle: every Console read route answers
    200; the listing, the summary, the cast and the provenance publish the
    SAME canonical id and the same structural counts; the topology export's
    baked id is published as data; and the summary NAMES that baked id once."""
    for route in _CONSOLE_READ_ROUTES:
        r = c.get(f"/api/twins/{imprint}{route}")
        assert r.status_code == 200, (route, r.status_code, r.text[:300])
    listed = {e["id"]: e for e in c.get("/api/twins").json()["imprints"]}
    assert imprint in listed
    summary = c.get(f"/api/twins/{imprint}").json()
    cast = c.get(f"/api/twins/{imprint}/cast").json()
    topo = c.get(f"/api/twins/{imprint}/topology").json()
    prov = c.get(f"/api/twins/{imprint}/provenance").json()
    assert summary["id"] == cast["id"] == cast["twin_config_id"] == prov["imprint_id"] == imprint
    assert summary["hosts"] == cast["hosts"] == listed[imprint]["hosts"]
    assert summary["segments"] == cast["segments"] == listed[imprint]["segments"]
    assert topo["imprint_id"] == baked != imprint
    named = [w for w in summary["warnings"] if repr(baked) in w]
    assert len(named) == 1 and "never used as an address" in named[0], summary["warnings"]
    return summary


@requires_real_disco
def test_the_disco_bundle_lists_summarizes_and_serves_every_route(tmp_path):
    """GAP-082's own bundle, read-only: its export bakes `corpapcn-disco` (the
    observed PIT's imprint) under the canonical directory
    `pit-395a61f04e-1780405200`. Its summary answered 400 before R7."""
    c = _lenient_client(_settings(REAL_CAST_ROOT, tmp_path))
    summary = _assert_served_consistently(c, REAL_DISCO.name, "corpapcn-disco")
    assert summary["provenance"] == "canonical"
    assert summary["confidence_source"].startswith("canonical sidecar (")
    assert summary["windows"] == 9


def test_a_cast_bundle_whose_export_bakes_another_imprint_id_is_served_consistently(tmp_path):
    """The same class, unconditionally -- a checkout without the real bundles
    still covers it: `mini-cast-prov` with its export re-baked to an observed
    imprint's NAME, exactly the disco bundle's shape."""
    root = _copy_bundle(tmp_path, "mini-cast-prov")
    export = root / "mini-cast-prov" / "mini-cast-prov.topology.json"
    raw = json.loads(export.read_text())
    raw["imprint_id"] = "mini-disco"
    export.write_text(json.dumps(raw))
    c = _lenient_client(_settings(root, tmp_path))
    summary = _assert_served_consistently(c, "mini-cast-prov", "mini-disco")
    assert summary["provenance"] == "canonical"


@pytest.mark.parametrize("root", _catalogs(), ids=lambda p: p.name)
def test_every_listed_bundle_summarizes_with_its_listing_numbers(tmp_path, root):
    """"Lists" implies "summarizes" (R7) on every catalog that ships. The ids
    come from the LISTING at run time (LES-223), so a bundle added later is
    covered without editing this test."""
    c = _lenient_client(_settings(root, tmp_path))
    listed = c.get("/api/twins").json()["imprints"]
    assert listed, root
    for e in listed:
        r = c.get(f"/api/twins/{e['id']}")
        assert r.status_code == 200, (e["id"], r.status_code, r.text[:300])
        j = r.json()
        for key in ("id", "hosts", "segments", "as_of", "mean_confidence", "confidence_source"):
            assert j[key] == e[key], (e["id"], key, j[key], e[key])
        assert e["has_provenance"] is (j["provenance"] != "absent")


@pytest.mark.parametrize("shape", ["canonical", "absent"])
def test_the_addressing_guard_still_refuses_a_renamed_cast_bundle(tmp_path, shape):
    """The check R7 KEEPS: a directory whose twin config declares another id
    is refused on the summary AND the cast route, and skipped by the listing
    with its reason -- a baked id is data, `twin_config.id` is the address."""
    src = "mini-cast-prov" if shape == "canonical" else "mini-cast"
    root = tmp_path / "imprints"
    root.mkdir()
    dst = root / "renamed"
    shutil.copytree(CAST_FIXTURES / src, dst)
    for f in list(dst.iterdir()):
        if f.name.startswith(src + "."):
            f.rename(dst / ("renamed" + f.name[len(src):]))
    c = _lenient_client(_settings(root, tmp_path))
    r = c.get("/api/twins/renamed")
    assert r.status_code == 400 and "twin_config.id" in r.json()["error"], r.text
    assert c.get("/api/twins/renamed/cast").status_code == 400
    j = c.get("/api/twins").json()
    assert j["imprints"] == []
    assert [s["id"] for s in j["skipped"]] == ["renamed"]


def test_the_summary_route_reads_each_artifact_once(tmp_path, monkeypatch):
    """R7 made the summary read the export (for `as_of`/`windows` and its
    baked id); S4's rule still holds -- one read per file, and never the
    export's object graph for a handful of scalars."""
    root = _copy_bundle(tmp_path, "mini-cast-prov")
    c = _client(_settings(root, tmp_path))
    counts = _count_reads(monkeypatch, root)
    assert c.get("/api/twins/mini-cast-prov").status_code == 200
    assert counts, "the summary route read no bundle file at all?"
    for name, n in counts.items():
        assert n <= 1, (name, n, dict(counts))


# ===========================================================================
# R5 (GAP-079) -- the cast publishes its conduit ROSTER and the gateway's legs
# (`docs/WORK_ORDER_console_level0_rendering.md` R5). Additive: the
# `conduits` count stays, and `len(conduit_roster) == conduits` everywhere.
# ===========================================================================
@requires_real_sidecarless
def test_the_real_twin_publishes_its_22_conduits_and_the_gateways_27_legs(tmp_path):
    """The standing `corpapcn-twin`. The roster is the config's own
    `conduits:` block spelled as written -- compared with a RAW YAML parse,
    not the pydantic model the reader itself uses -- and the legs are the
    `kind: router` node's `nets:` in declaration order."""
    from ruamel.yaml import YAML

    raw = YAML(typ="safe").load(
        _real_config("corpapcn-twin").read_text()
    )["twin_config"]
    c = _client(_settings(REAL_CAST_ROOT, tmp_path))
    j = c.get("/api/twins/corpapcn-twin/cast").json()
    assert j["conduits"] == 22 == len(j["conduit_roster"])
    assert j["conduit_roster"] == [{"from": x["from"], "to": x["to"]} for x in raw["conduits"]]
    assert j["conduit_roster"][0] == {"from": "seg-10-20-192-0", "to": "seg-10-20-196-0"}
    routers = [n for n in raw["nodes"] if n.get("kind") == "router"]
    assert [n["name"] for n in routers] == ["gw"]
    assert j["routers"] == [
        {"name": "gw", "segments": [leg["segment"] for leg in routers[0]["nets"]]},
    ]
    legs = j["routers"][0]["segments"]
    assert len(legs) == 27
    assert legs[0] == "seg-10-1-10-0"
    # A leg on a segment the observed export does not carry is still a leg.
    assert "internet-sink" in legs


@pytest.mark.parametrize("root", _catalogs(), ids=lambda p: p.name)
def test_the_roster_agrees_with_the_count_on_every_listed_bundle(tmp_path, root):
    c = _client(_settings(root, tmp_path))
    for e in c.get("/api/twins").json()["imprints"]:
        j = c.get(f"/api/twins/{e['id']}/cast").json()
        assert len(j["conduit_roster"]) == j["conduits"], e["id"]
        assert all(set(x) == {"from", "to"} for x in j["conduit_roster"]), e["id"]
        for router in j["routers"]:
            assert set(router) == {"name", "segments"}, e["id"]
            assert len(set(router["segments"])) == len(router["segments"]), e["id"]


def test_a_sidecarless_bundle_with_no_conduits_or_router_carries_empty_rosters_and_lists(tmp_path):
    """`[]` means "the config declares none" -- R5's contract on the smallest
    honest bundle: no sidecar, no export, no conduits, no router."""
    root = tmp_path / "imprints"
    d = root / "bare"
    d.mkdir(parents=True)
    (d / "bare.yaml").write_text(
        "twin_config:\n  id: bare\n  segments: [{ name: lan, cidr: 10.5.0.0/24 }]\n"
        "  nodes:\n    - { name: h1, image: debian-12-cloud@v1, requires: [linux], net: lan }\n"
    )
    c = _client(_settings(root, tmp_path))
    j = c.get("/api/twins/bare/cast").json()
    assert (j["conduits"], j["conduit_roster"], j["routers"]) == (0, [], [])
    assert j["has_provenance"] is False
    assert [e["id"] for e in c.get("/api/twins").json()["imprints"]] == ["bare"]


def test_the_two_r5_fields_are_independent_on_the_packaged_fixture(tmp_path):
    """`corp-enterprise` declares no conduits, but its gateway has legs."""
    c = _client(ApiSettings(db_path=str(tmp_path / "s.db"), adapter_config="/nonexistent.conf"))
    j = c.get("/api/twins/corp-enterprise/cast").json()
    assert (j["conduits"], j["conduit_roster"]) == (0, [])
    assert len(j["routers"]) == 1
    assert sorted(j["routers"][0]["segments"]) == ["corp-lan", "db", "dmz", "mgmt"]


def test_a_config_with_several_routers_publishes_each_with_its_own_legs(tmp_path):
    """S5 on shipped content: `twins/datacenter` declares several
    routers, and each is published with ITS legs -- derived here from a RAW
    parse of the same file (LES-223) -- never one flattened union the Console
    would have had to call "the gateway"."""
    from ruamel.yaml import YAML

    src = CATALOG / "twins" / "datacenter" / "te.yaml"
    root = tmp_path / "imprints"
    (root / "datacenter").mkdir(parents=True)
    shutil.copy(src, root / "datacenter" / "datacenter.yaml")
    raw = YAML(typ="safe").load(src.read_text())["twin_config"]
    expected = []
    for node in raw["nodes"]:
        if node.get("kind") != "router":
            continue
        names = [leg["segment"] for leg in node.get("nets") or []] or (
            [node["net"]] if node.get("net") else []
        )
        expected.append({"name": node["name"], "segments": list(dict.fromkeys(names))})
    assert len(expected) >= 2, "the premise: datacenter is the multi-router config"
    j = _client(_settings(root, tmp_path)).get("/api/twins/datacenter/cast").json()
    assert j["routers"] == expected


# ===========================================================================
# Console S5.1.a (the combined fix round, A1) -- the cast publishes its SEGMENT
# roster: one entry per twin-config segment, `{name, cidr (deployed),
# zone_type}`, in config order. Additive: the `segments` count stays, and
# `len(segment_roster) == segments` everywhere. The roster is what lets the
# Console tell a segment the cast PRUNED (re-addable by assertion,
# `docs/design/14-operator-assertions.md` §3) from one the twin already has,
# and what makes the inspector's deployed-CIDR / zone facts true.
# ===========================================================================
#: The observed segments each real cast prunes -- an observed segment whose
#: NAME is absent from the roster. Measured off the files (2026-09-15).
_REAL_PRUNED = {
    "corpapcn-twin": [
        "10.20.205.0/24", "10.5.0.0/24", "10.64.7.0/24",
        "10.80.31.0/24", "10.80.63.0/24", "172.16.0.0/24",
    ],
    "corpapcn-golden": [],
    "pit-395a61f04e-1780405200": [],
}


@pytest.mark.parametrize("bundle", sorted(_REAL_PRUNED))
def test_a_real_cast_publishes_its_segment_roster_as_written(tmp_path, bundle):
    """Compared against a RAW YAML parse of the config -- never the pydantic
    model the reader itself uses (LES-223) -- on all three real bundles
    (LES-185): the roster is the config's `segments:` in order, spelled as
    written, `zone_type` null where the config declares none."""
    import json

    from ruamel.yaml import YAML

    d = REAL_CAST_ROOT / bundle
    if not d.is_dir():
        pytest.skip(f"twins/{bundle} not present")
    raw = YAML(typ="safe").load(_real_config(bundle).read_text())["twin_config"]
    j = _client(_settings(REAL_CAST_ROOT, tmp_path)).get(f"/api/twins/{bundle}/cast").json()

    expected = [
        {"name": x["name"], "cidr": x["cidr"], "zone_type": x.get("zone_type")}
        for x in raw["segments"]
    ]
    written = [{k: x[k] for k in ("name", "cidr", "zone_type")} for x in j["segment_roster"]]
    assert written == expected
    assert len(j["segment_roster"]) == j["segments"]
    # Every entry is exactly the four published fields; `observed_cidr` is the
    # sidecar's reading, not the config's, and is checked on its own below.
    assert all(set(x) == {"name", "cidr", "zone_type", "observed_cidr"} for x in j["segment_roster"])

    # The cast's own sink is carried and observed by nothing.
    names = [x["name"] for x in j["segment_roster"]]
    assert "internet-sink" in names

    # The join is by NAME: the roster's CIDRs are the DEPLOYED, relocated
    # networks, so no observed CIDR appears among them on a relocated cast.
    topo = json.loads(_real_artifact(d, f"{bundle}.topology.json").read_text())
    observed = {s["name"]: s["cidr"] for s in topo["segments"]}
    carried = [n for n in names if n in observed]
    assert carried, "the premise: the roster carries observed segments"
    assert all(
        x["cidr"] != observed[x["name"]] for x in j["segment_roster"] if x["name"] in observed
    )
    # ...and the observed segments it does NOT name are the ones the cast pruned.
    pruned = sorted(cidr for name, cidr in observed.items() if name not in set(names))
    assert pruned == sorted(_REAL_PRUNED[bundle])
    assert len(carried) + len(pruned) == len(observed)


#: The Console's checked-in copy of two real casts' segment rosters -- the
#: input its real-bundle allocator test builds a roster-backed graph from, so
#: that test never hand-copies what the cast prunes. Vendored, and pinned to the
#: real twin configs by the drift test below. Regenerate with the snippet in its
#: docstring when a cast is re-installed.
_CONSOLE_ROSTER_FIXTURE = REPO_ROOT.parent / "o2" / "console" / "test" / "fixtures" / "real-segment-rosters.json"


def test_the_console_segment_roster_fixture_matches_the_real_twin_configs():
    """The vendored-with-drift-test pattern: `real-segment-rosters.json` must be
    exactly each real twin config's `segments:` (`{name, cidr, zone_type}`,
    config order) -- read here with a RAW YAML parse, the same shape the API
    publishes. On drift, regenerate::

        .venv/bin/python -c "import json; from pathlib import Path; \\
        from ruamel.yaml import YAML; r=Path('twins'); \\
        print(json.dumps({b: [{'name': s['name'], 'cidr': s['cidr'], \\
        'zone_type': s.get('zone_type')} for s in YAML(typ='safe').load( \\
        (r/b/'te.yaml').read_text())['twin_config']['segments']] \\
        for b in ['corpapcn-twin', 'pit-395a61f04e-1780405200']}, \\
        indent=2, sort_keys=True))" > o2/console/test/fixtures/real-segment-rosters.json
    """
    import json

    from ruamel.yaml import YAML

    fixture = json.loads(_CONSOLE_ROSTER_FIXTURE.read_text())
    assert set(fixture) == {"corpapcn-twin", "pit-395a61f04e-1780405200"}
    checked = 0
    for bundle, roster in sorted(fixture.items()):
        path = _real_config(bundle)
        if not path.is_file():
            continue
        raw = YAML(typ="safe").load(path.read_text())["twin_config"]
        expected = [
            {"name": x["name"], "cidr": x["cidr"], "zone_type": x.get("zone_type")}
            for x in raw["segments"]
        ]
        assert roster == expected, (
            f"o2/console/test/fixtures/real-segment-rosters.json has drifted from {path} "
            "-- regenerate it (see this test's docstring)"
        )
        checked += 1
    if checked == 0:
        pytest.skip("no real cast bundle present to check the fixture against")


#: The Console's checked-in copy of the same two casts' CONDUIT rosters. Added
#: by review N5: the Console's real-bundle tests used to read this block out of
#: the twin config with a hand-written parser -- a second reader of a YAML file
#: that package has no YAML reader for. Same vendored-with-drift-test pattern as
#: the segment roster above.
_CONSOLE_CONDUIT_FIXTURE = (
    REPO_ROOT.parent / "o2" / "console" / "test" / "fixtures" / "real-conduit-rosters.json"
)


def test_the_console_conduit_roster_fixture_matches_the_real_twin_configs():
    """`real-conduit-rosters.json` must be exactly each real twin config's
    `conduits:` (`{from, to}`, config order). On drift, regenerate::

        .venv/bin/python -c "import json; from pathlib import Path; \\
        from ruamel.yaml import YAML; r=Path('twins'); \\
        print(json.dumps({b: [{'from': c['from'], 'to': c['to']} \\
        for c in YAML(typ='safe').load((r/b/'te.yaml').read_text()) \\
        ['twin_config'].get('conduits', [])] \\
        for b in ['corpapcn-twin', 'pit-395a61f04e-1780405200']}, \\
        indent=2, sort_keys=True))" > o2/console/test/fixtures/real-conduit-rosters.json
    """
    import json

    from ruamel.yaml import YAML

    fixture = json.loads(_CONSOLE_CONDUIT_FIXTURE.read_text())
    assert set(fixture) == {"corpapcn-twin", "pit-395a61f04e-1780405200"}
    checked = 0
    for bundle, roster in sorted(fixture.items()):
        path = _real_config(bundle)
        if not path.is_file():
            continue
        raw = YAML(typ="safe").load(path.read_text())["twin_config"]
        expected = [{"from": c["from"], "to": c["to"]} for c in raw.get("conduits", [])]
        assert roster == expected, (
            f"o2/console/test/fixtures/real-conduit-rosters.json has drifted from {path} "
            "-- regenerate it (see this test's docstring)"
        )
        # A roster the Console reads as EMPTY would silently turn every
        # mirror-conduits test into a no-op (LES-311), so it is never empty.
        assert roster, bundle
        checked += 1
    if checked == 0:
        pytest.skip("no real cast bundle present to check the fixture against")


@pytest.mark.parametrize("root", _catalogs(), ids=lambda p: p.name)
def test_the_segment_roster_agrees_with_the_segment_count_on_every_listed_bundle(tmp_path, root):
    c = _client(_settings(root, tmp_path))
    listed = c.get("/api/twins").json()["imprints"]
    assert listed, root
    for e in listed:
        j = c.get(f"/api/twins/{e['id']}/cast").json()
        assert len(j["segment_roster"]) == j["segments"], e["id"]
        for x in j["segment_roster"]:
            assert set(x) == {"name", "cidr", "zone_type", "observed_cidr"}, e["id"]
            assert x["observed_cidr"] is None or isinstance(x["observed_cidr"], str), e["id"]
            assert isinstance(x["name"], str) and x["name"], e["id"]
            assert isinstance(x["cidr"], str) and x["cidr"], e["id"]
            assert x["zone_type"] is None or isinstance(x["zone_type"], str), e["id"]


def test_a_bare_config_publishes_a_null_zone_type_as_null_not_the_effective_zone(tmp_path):
    """A segment with no `zone_type:` is published `null` -- the effective zone
    is a scenario-wide rule the expander applies, and defaulting it here would
    state something the config never said."""
    root = tmp_path / "imprints"
    d = root / "bare"
    d.mkdir(parents=True)
    (d / "bare.yaml").write_text(
        "twin_config:\n  id: bare\n"
        "  segments:\n"
        "    - { name: lan, cidr: 10.5.0.0/24 }\n"
        "    - { name: ot, cidr: 10.6.0.0/24, zone_type: vlan }\n"
        "  nodes:\n    - { name: h1, image: debian-12-cloud@v1, requires: [linux], net: lan }\n"
    )
    j = _client(_settings(root, tmp_path)).get("/api/twins/bare/cast").json()
    assert j["segments"] == 2
    assert j["segment_roster"] == [
        {"name": "lan", "cidr": "10.5.0.0/24", "zone_type": None, "observed_cidr": None},
        {"name": "ot", "cidr": "10.6.0.0/24", "zone_type": "vlan", "observed_cidr": None},
    ]


def test_the_packaged_fixture_publishes_its_segment_roster(tmp_path):
    """`corp-enterprise` has no topology export: the roster is published all
    the same -- it is a fact about the twin config, not about an observation."""
    from ruamel.yaml import YAML

    c = _client(ApiSettings(db_path=str(tmp_path / "s.db"), adapter_config="/nonexistent.conf"))
    j = c.get("/api/twins/corp-enterprise/cast").json()
    raw = YAML(typ="safe").load(
        (_packaged_imprints() / "corp-enterprise" / "corp-enterprise.yaml").read_text()
    )["twin_config"]
    assert [x["name"] for x in j["segment_roster"]] == [x["name"] for x in raw["segments"]]
    assert len(j["segment_roster"]) == j["segments"]
    assert j["has_topology_export"] is False


# ===========================================================================
# The GAP-082 review fix round -- every Console route runs the one addressing
# guard (C), the deploy pre-check is the NARROW one (E), relative scripts
# plan against the twin config's real directory (M2), and a plan or deploy
# leaves no materialized tempfile behind (S3). Pure: previews, a scratch
# state DB, a private TMPDIR; nothing is deployed.
# ===========================================================================
FIXT_ROOT = REPO_ROOT / "tests" / "fixtures"


def _plan_settings(imprints_dir: Path, tmp_path: Path) -> ApiSettings:
    """`_settings` plus the tracked fixture image catalog, which binds every
    image the cast bundles name (`debian-12-cloud@v1`, `ubuntu-server-22@v5`,
    `win11-finance@v4`, `windows-server-2022@v1`) -- so a plan never needs
    the untracked `deploy-dev45/` catalog."""
    return ApiSettings(
        db_path=str(tmp_path / "state.db"), adapter_config="/nonexistent.conf",
        imprints_dir=str(imprints_dir), images_root=str(FIXT_ROOT),
    )


def _private_tmp(tmp_path: Path, monkeypatch) -> Path:
    """This test's own TMPDIR (LES-233): the materialized twin config lands
    here, so "nothing left behind" is a statement about this run only."""
    import tempfile

    d = tmp_path / "tempfiles"
    d.mkdir(exist_ok=True)
    monkeypatch.setattr(tempfile, "tempdir", str(d))
    return d


def _plan_ops(plan) -> list:
    return list(plan.ops.values()) if isinstance(plan.ops, dict) else list(plan.ops)


def _renamed_bundle(tmp_path: Path) -> Path:
    """`renamed/` -- `mini-cast-prov` copied under a new directory name whose
    twin config still declares `mini-cast-prov` (LES-195's case)."""
    root = tmp_path / "imprints"
    root.mkdir()
    dst = root / "renamed"
    shutil.copytree(CAST_FIXTURES / "mini-cast-prov", dst)
    for f in list(dst.iterdir()):
        if f.name.startswith("mini-cast-prov."):
            f.rename(dst / ("renamed" + f.name[len("mini-cast-prov"):]))
    return root


def test_a_renamed_bundle_is_refused_by_every_console_route_that_addresses_it(tmp_path):
    """C: `/topology`, `/assertions` and `/layout` served a renamed bundle 200
    -- and the PUTs WROTE its operator files -- under an id the summary, the
    cast, the plan and the deploy all refuse. Keying them by directory alone
    was never deliberate, so every route now runs the one addressing guard
    (`imprints.check_imprint_addressable`)."""
    root = _renamed_bundle(tmp_path)
    c = _lenient_client(_settings(root, tmp_path))
    before = sorted(p.name for p in (root / "renamed").iterdir())
    for method, route, body in (
        ("GET", "/topology", None),
        ("GET", "/assertions", None),
        ("GET", "/layout", None),
        ("PUT", "/assertions", _VALID_DOC),
        ("PUT", "/layout", _LAYOUT),
        ("POST", "/apply", {"document": _VALID_DOC}),
        ("GET", "/diff?since=deadbeef", None),
    ):
        r = c.request(method, f"/api/twins/renamed{route}", json=body)
        assert r.status_code == 400, (method, route, r.status_code, r.text[:300])
        assert "twin_config.id='mini-cast-prov'" in r.json()["error"], (route, r.json())
    # Nothing was written into the bundle...
    assert sorted(p.name for p in (root / "renamed").iterdir()) == before
    # ...and the scenarios alias of `/topology` refuses it the same way.
    r = c.get("/api/scenarios/renamed/topology-export?source=imprint")
    assert r.status_code == 400 and "twin_config.id" in r.json()["error"], r.text[:300]


def test_the_plan_pre_check_reads_the_twin_config_and_never_the_sidecar_or_export(
    tmp_path, monkeypatch,
):
    """E (S2): "deploy as twin" reads the twin config and its scripts, so its
    pre-check is the NARROW addressing check. An undecodable sidecar and
    export make the summary refuse -- and the plan preview still answers,
    having opened neither file."""
    _private_tmp(tmp_path, monkeypatch)
    root = _copy_bundle(tmp_path, "mini-cast-prov")
    d = root / "mini-cast-prov"
    (d / "mini-cast-prov.provenance.json").write_bytes(b"\xff\xfe{}")
    (d / "mini-cast-prov.topology.json").write_bytes(b"\xff\xfe{}")
    c = _lenient_client(_plan_settings(root, tmp_path))
    assert c.get("/api/twins/mini-cast-prov").status_code == 400
    counts = _count_reads(monkeypatch, root)
    r = c.post("/api/twins/mini-cast-prov/plan", json={"target_node": "pxtest1"})
    assert r.status_code == 200, r.text[:400]
    assert "mini-cast-prov.provenance.json" not in counts, dict(counts)
    assert "mini-cast-prov.topology.json" not in counts, dict(counts)


def test_a_plan_preview_leaves_no_materialized_twin_config_behind(tmp_path, monkeypatch):
    """S3: the tempfile a twin config is materialized into is read by the
    validator and the planner only, and the preview deletes it on the way out
    -- after a 200 AND after a refusal raised once the file exists."""
    private = _private_tmp(tmp_path, monkeypatch)
    root = _copy_bundle(tmp_path, "mini-cast-prov")
    bad = root / "bad"
    bad.mkdir()
    (bad / "bad.yaml").write_text(  # expands, then fails validation or plan
        "twin_config:\n  id: bad\n  segments: [{ name: lan, cidr: 10.5.0.0/24 }]\n"
        "  nodes:\n    - { name: h1, image: no-such-image@v9, requires: [linux], net: lan }\n"
    )
    c = _lenient_client(_plan_settings(root, tmp_path))
    ok = c.post("/api/twins/mini-cast-prov/plan", json={"target_node": "pxtest1"})
    assert ok.status_code == 200, ok.text[:300]
    refused = c.post("/api/twins/bad/plan", json={"target_node": "pxtest1"})
    assert refused.status_code in (400, 422), refused.text[:300]
    assert list(private.iterdir()) == []


def test_a_deploy_plans_from_the_bundle_and_leaves_no_tempfile(tmp_path, monkeypatch):
    """S3 on the DEPLOY path, and the finding it rests on: nothing reads the
    materialized IR once planning is done -- `resolve_deploy_plan` replans
    from the in-memory objects and the runner is handed the in-memory plan --
    so `create_deploy` deletes it before the lease/runner stage. Driven
    through `service.create_deploy` with a RECORDING runner and a scratch
    state DB: no cluster, no HTTP deploy route."""
    from axrange.api import dtos, service

    private = _private_tmp(tmp_path, monkeypatch)
    root = _copy_bundle(tmp_path, "mini-cast-prov")
    calls: list[tuple] = []
    resp = service.create_deploy(
        _plan_settings(root, tmp_path),
        dtos.CreateDeployRequest(imprint="mini-cast-prov", target_node="pxtest1"),
        runner=lambda *args: calls.append(args),
        background=lambda job: job(),
    )
    assert resp.status == "running"
    [(_settings_arg, plan, _cfg, _ctx, deploy_id)] = calls
    assert deploy_id == resp.deploy_id
    shipped = {p.read_text() for p in (root / "mini-cast-prov" / "scripts").iterdir()}
    run_scripts = [op for op in _plan_ops(plan) if op.op_name == "run-script"]
    assert run_scripts, "the bundle's relative script installs are in the plan"
    for op in run_scripts:
        assert op.inputs_resolved["script_content"] in shipped, op.op_id
    assert list(private.iterdir()) == []


def _real_cast_bundles() -> list[str]:
    """Every CAST the real catalog carries -- a twin folder with a cast's
    generated export/sidecar, or a legacy directory holding its own
    `<name>.yaml` -- derived at collection time (LES-223). `twins/` also holds
    hand-authored twins; they are not casts (`twinconfig.bundle.
    has_cast_artifacts`)."""
    from axrange.twinconfig.bundle import has_cast_artifacts, is_twin_folder

    if not REAL_CAST_ROOT.is_dir():
        return []
    return sorted(
        d.name for d in REAL_CAST_ROOT.iterdir()
        if (is_twin_folder(d) and has_cast_artifacts(d, d.name))
        or (d / f"{d.name}.yaml").is_file()
    )


def test_the_real_cast_catalog_carries_the_three_bundles_the_review_planned():
    assert {"corpapcn-twin", "corpapcn-golden", "pit-395a61f04e-1780405200"} <= set(
        _real_cast_bundles()
    )


@pytest.mark.parametrize("bundle", _real_cast_bundles())
def test_every_real_cast_bundle_plans_through_the_api(tmp_path, monkeypatch, bundle):
    """M2 on REAL content. Every real cast bundle ships relative `scripts/`
    installs, and every one answered 400 "cannot read script" -- resolved
    against the tempdir its twin config was materialized into -- until the
    twin config's own directory was threaded through (`pit-...` once resolved
    through three symlinks into `corpapcn-disco/`). Pure: a preview, a
    private TMPDIR, no state DB written, nothing deployed."""
    private = _private_tmp(tmp_path, monkeypatch)
    c = _lenient_client(_plan_settings(REAL_CAST_ROOT, tmp_path))
    r = c.post(f"/api/twins/{bundle}/plan", json={"target_node": "pxtest1"})
    assert r.status_code == 200, r.text[:500]
    assert r.json()["ops"] > 0
    assert list(private.iterdir()) == []
    assert not (tmp_path / "state.db").exists(), "a preview must not write the state DB"


def test_a_bundle_of_symlinks_plans_against_the_links_target_directory(tmp_path, monkeypatch):
    """M2's symlink half (`pit-395a61f04e-1780405200` is three links into
    `corpapcn-disco/`): relative scripts resolve beside the file the link
    POINTS AT -- `registry.twin_config_source_dir` resolves symlinks -- never
    beside the link, where there is no `scripts/`."""
    from axrange.twinconfig.registry import twin_config_source_dir

    _private_tmp(tmp_path, monkeypatch)
    root = tmp_path / "imprints"
    real = root / "compiled"  # the compiler's own output directory
    shutil.copytree(CAST_FIXTURES / "mini-cast-prov", real)
    (real / "mini-cast-prov.yaml").rename(real / "twin_config.yaml")
    link = root / "mini-cast-prov"
    link.mkdir()
    (link / "mini-cast-prov.yaml").symlink_to(Path("..") / "compiled" / "twin_config.yaml")
    assert not (link / "scripts").exists()
    assert twin_config_source_dir("mini-cast-prov", catalog_dir=root) == real.resolve()
    c = _lenient_client(_plan_settings(root, tmp_path))
    r = c.post("/api/twins/mini-cast-prov/plan", json={"target_node": "pxtest1"})
    assert r.status_code == 200, r.text[:400]


def test_the_config_deploy_source_reads_scripts_from_the_twin_config_directory(
    tmp_path, monkeypatch,
):
    """M2 on the `config:` deploy source, real content: `twins/svc-verify`
    ships `scripts/serve-*.sh`. The planning step `create_deploy`
    runs for a config (`_resolve_config_scenario_path` -> `_plan_materialized`)
    reads every script from the twin config's own directory and deletes the
    tempfile. Driven directly -- nothing is deployed."""
    from axrange.api import service

    private = _private_tmp(tmp_path, monkeypatch)
    tc_dir = CATALOG / "twins" / "svc-verify"
    settings = ApiSettings(
        db_path=str(tmp_path / "state.db"), adapter_config="/nonexistent.conf",
        twin_configs_dir=str(CATALOG / "twins"), images_root=str(FIXT_ROOT),
    )
    sp, images_root, source_dir = service._resolve_config_scenario_path(
        settings, "svc-verify", None,
    )
    assert source_dir == tc_dir.resolve()
    plan, *_rest = service._plan_materialized(
        settings, sp, images_root, source_dir, target_node="pxtest1",
    )
    assert not sp.exists() and list(private.iterdir()) == []
    shipped = {p.read_text() for p in (tc_dir / "scripts").iterdir() if p.is_file()}
    run_scripts = [op for op in _plan_ops(plan) if op.op_name == "run-script"]
    assert run_scripts
    for op in run_scripts:
        assert op.inputs_resolved["script_content"] in shipped, op.op_id


# ---------------------------------------------------------------------------
# Unified Console -- the declared-design map: `node_roster` on GET .../cast
# ---------------------------------------------------------------------------
def test_cast_summary_publishes_the_node_roster_behind_node_specs_and_vms(tmp_path):
    """The roster the Console draws a bundle with NO topology export from: one
    entry per `nodes[]` spec (its length IS `node_specs`), whose counts sum to
    `vms`, each with its legs' segment names and its fanned-out node ids."""
    c = _client(_settings(CAST_FIXTURES, tmp_path))
    j = c.get("/api/twins/mini-cast/cast").json()
    roster = j["node_roster"]
    assert len(roster) == j["node_specs"]
    assert sum(n["count"] for n in roster) == j["vms"]
    for n in roster:
        assert set(n) == {"name", "kind", "role", "count", "segments", "node_ids", "members"}
        assert len(n["node_ids"]) == n["count"]
        assert all(s in {g["name"] for g in j["segment_roster"]} for s in n["segments"])


def test_cast_summary_node_roster_on_the_real_hand_authored_corp_twin(tmp_path):
    """The bundle the Console's declared map was built for: `twins/corp-twin`
    is hand-authored -- no topology export, no sidecar -- and checked in, so
    this asserts on it rather than skipping when absent (LES-352)."""
    assert (REAL_CAST_ROOT / "corp-twin").is_dir(), "twins/corp-twin is checked in"
    c = _client(_settings(REAL_CAST_ROOT, tmp_path))
    j = c.get("/api/twins/corp-twin/cast").json()
    assert j["has_topology_export"] is False and j["has_provenance"] is False
    roster = j["node_roster"]
    assert len(roster) == j["node_specs"] == 118
    assert sum(n["count"] for n in roster) == j["vms"] == 204
    routers = [n for n in roster if n["kind"] == "router"]
    assert [r["name"] for r in routers] == [r["name"] for r in j["routers"]]


def test_imprint_list_says_which_bundles_carry_a_topology_export(tmp_path):
    """The outline's "from capture" / "hand-authored" label: the three real
    cast bundles carry an export, `twins/corp-twin` (hand-authored) does not."""
    c = _client(_settings(REAL_CAST_ROOT, tmp_path))
    items = {i["id"]: i for i in c.get("/api/twins").json()["imprints"]}
    assert items["corp-twin"]["has_topology_export"] is False
    assert items["corp-twin"]["has_provenance"] is False
    for cast in ("corpapcn-golden", "corpapcn-twin"):
        assert items[cast]["has_topology_export"] is True, cast


# ---------------------------------------------------------------------------
# The rosters carry the sidecar's observed keys: `members` per node group and
# `observed_cidr` per segment -- what the design canvas's promote-to-assertion
# addresses the assertions seam by (a node NAME is a group name and joins
# against nothing there).
# ---------------------------------------------------------------------------
def _prov_bundle(tmp_path, patch):
    """A private copy of the `mini-cast-prov` fixture with its sidecar edited by
    `patch(fields)` -- the checked-in fixture predates the `members` row."""
    import json
    import shutil

    root = tmp_path / "bundles"
    shutil.copytree(CAST_FIXTURES / "mini-cast-prov", root / "mini-cast-prov")
    path = root / "mini-cast-prov" / "mini-cast-prov.provenance.json"
    doc = json.loads(path.read_text())
    patch(doc["fields"])
    path.write_text(json.dumps(doc))
    return root


def test_cast_roster_publishes_the_sidecars_members_and_observed_cidrs(tmp_path):
    def patch(fields):
        fields["twin_config.nodes[1].count"]["members"] = ["hostname:web-b", "address:10.0.0.7", "hostname:web-a"]
        fields["twin_config.nodes[2].count"]["members"] = "not-a-list"
        fields["twin_config.segments[0].cidr"] = {
            "evidence_class": "compiler", "signal": "synth/compiler.py --relocate",
            "transformation": {"kind": "relocate", "replaced": {"class": "observed", "value": "192.168.7.0/24"},
                               "to": "10.90.0.0/24"},
        }
        fields["twin_config.segments[1].cidr"] = {"evidence_class": "observed", "signal": "ir_segment.cidr"}
        fields["twin_config.segments[2].cidr"] = {"evidence_class": "asserted", "signal": "assertions"}
        fields["twin_config.segments[3].cidr"] = {"evidence_class": "compiler", "signal": "SINK_CIDR"}

    c = _client(_settings(_prov_bundle(tmp_path, patch), tmp_path))
    j = c.get("/api/twins/mini-cast-prov/cast").json()
    by_name = {n["name"]: n for n in j["node_roster"]}
    # Sorted, strings only; a malformed row and a row without the key are empty, never guessed.
    assert by_name["web-tier"]["members"] == ["address:10.0.0.7", "hostname:web-a", "hostname:web-b"]
    assert by_name["db-tier"]["members"] == []
    assert by_name["gw"]["members"] == [] and by_name["sink"]["members"] == []
    segs = {s["name"]: s["observed_cidr"] for s in j["segment_roster"]}
    assert segs == {"seg-corp": "192.168.7.0/24", "seg-data": "10.90.1.0/24",
                    "seg-thin": "10.90.2.0/24", "internet-sink": None}


def test_cast_roster_reads_no_members_off_a_sidecar_of_another_shape(tmp_path):
    """An unrecognized sidecar is never read as the canonical one (the
    reference loader's rule): its rows are present but contribute nothing."""
    import json

    root = _prov_bundle(tmp_path, lambda f: f["twin_config.nodes[1].count"].update(members=["hostname:x"]))
    path = root / "mini-cast-prov" / "mini-cast-prov.provenance.json"
    doc = json.loads(path.read_text())
    doc["schema"] = "something-else/9"
    path.write_text(json.dumps(doc))
    j = _client(_settings(root, tmp_path)).get("/api/twins/mini-cast-prov/cast").json()
    assert all(n["members"] == [] for n in j["node_roster"])
    assert all(s["observed_cidr"] is None for s in j["segment_roster"])
