"""Adversarial coverage for the level-0 order's two BACKEND slices --
`docs/WORK_ORDER_console_level0_rendering.md` §3 **R5** (GAP-079: `GET
.../cast` publishes the twin config's conduit roster and each router's legs)
and **R7** (GAP-082: ONE summary builder for all three sidecar shapes; only
the ADDRESSING id refuses -- decision D3 "tolerate on read", Authority: user,
2026-09-11 -- plus, since the review's S1, a normalized sidecar that names
ANOTHER imprint).

The build's own tests prove the happy shape on real bundles. This module
attacks the edges of the same surface over real HTTP -- a LENIENT TestClient,
so a 5xx is an assertion rather than a traceback -- and never touches a
cluster (the one deploy call goes to a runner that only records):

  1. the addressing guard (directory name == `twin_config.id`) still
     refuses, never 5xx, on every route that resolves an imprint, for every
     sidecar shape -- listing, summary, provenance, cast, topology, plan and
     deploy;
  2. no route answers 5xx on a damaged bundle, and one damaged bundle never
     takes the listing down for a healthy neighbour;
  3. R5's roster and legs under adversarial twin configs (empty/null
     conduits, dangling ends, duplicates, two routers, host legs);
  4. the listing and the summary partition every shipped catalog the same
     way, in BOTH directions -- and every bundle it lists publishes the
     roster and legs its RAW twin config spells;
  5. the plan path R7 unblocked, on a bundle shaped like a real cast.

`xfail(strict=True)` marks a defect that is PRE-EXISTING -- present at HEAD,
not introduced by the R5/R7 diff -- found on these routes; each reason names
the root cause. `strict` turns the fix into an XPASS failure, so the marker
leaves with the fix instead of outliving it. (Every marker this module was
written with -- 23 cells -- flipped in the GAP-082 review fix round and left
with its fix.)
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Callable

import pytest

from axrange.api import ApiSettings, create_app
from axrange.api.imprints import default_fixtures_dir
from axrange.planner import StateStore
from axrange.twinconfig.bundle import bundle_config_path

try:
    from starlette.testclient import TestClient
except ImportError:  # pragma: no cover
    pytest.skip("starlette/httpx not installed", allow_module_level=True)


REPO_ROOT = Path(__file__).resolve().parent.parent
CATALOG = REPO_ROOT.parent / "catalog"  # twin content lives at the project root
FIXT_ROOT = REPO_ROOT / "tests" / "fixtures"
CAST_FIXTURES = FIXT_ROOT / "cast-bundles"
# The real cast bundles moved from `twin-configs-cast/` into `twins/<id>/`
# (`docs/WORK_ORDER_twin_consolidation.md` Phase 2): one root for every twin,
# the casts among them, read through `axrange.twinconfig.bundle`.
REAL_CAST_ROOT = CATALOG / "twins"

_SHAPES = ("normalized", "canonical", "absent")


@pytest.fixture(autouse=True)
def _private_tempdir(tmp_path, monkeypatch):
    """The plan/deploy pre-check materializes the twin config into a
    `NamedTemporaryFile(delete=False)` that nothing deletes. Point that at
    THIS test's directory so the module leaves nothing in the shared temp
    dir (LES-233)."""
    d = tmp_path / "tempfiles"
    d.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(d))


# ---------------------------------------------------------------------------
# Bundle writers
# ---------------------------------------------------------------------------
_SEGMENTS = (
    "    - { name: lan, cidr: 10.5.0.0/24 }\n"
    "    - { name: dmz, cidr: 10.6.0.0/24 }\n"
)
_CONDUITS = "  conduits:\n    - { from: lan, to: dmz }\n"
_NODES = (
    "    - { name: gw, kind: router, image: debian-12-cloud@v1, requires: [linux],\n"
    "        nets: [{ segment: lan }, { segment: dmz }] }\n"
    "    - { name: h, image: debian-12-cloud@v1, requires: [linux], net: lan, count: 3 }\n"
)


def _twin_config(tc_id: str, *, segments: str = _SEGMENTS, conduits: str = _CONDUITS,
                 nodes: str = _NODES) -> str:
    return (
        "twin_config:\n"
        f"  id: {tc_id}\n"
        "  segments:\n" + segments + conduits + "  nodes:\n" + nodes
    )


#: The real compiler's shape (`axr-cast-provenance/1`), one confidence.
_CANONICAL = {
    "schema": "axr-cast-provenance/1", "pit_id": "pit-x-1", "code_fingerprint": "f00",
    "fields": {"twin_config.nodes[1].role": {"class": "observed", "confidence": 0.8}},
}


def _normalized(imprint_id: Any) -> dict:
    """The Track-C fixture shape, self-consistent unless told otherwise."""
    return {
        "imprint_id": imprint_id, "as_of": "2026-01-01T00:00:00Z",
        "summary": {"services": 1, "personas": 1, "mean_confidence": 0.5,
                    "out_of_vocab_pct": 0.0},
        "fields": {},
    }


def _export(imprint_id: Any, *, drop: tuple[str, ...] = (), **overrides: Any) -> dict:
    doc = {
        "imprint_id": imprint_id, "pit_id": "pit-x-1", "as_of": 1767225600.0,
        "windows": 3, "segments": [], "nodes": [], "edges": [], "census": {},
    }
    doc.update(overrides)
    for key in drop:
        doc.pop(key, None)
    return doc


def _put(path: Path, artifact: Any) -> None:
    """`None` writes nothing (absent); bytes/str are written raw, so a test
    can hand a reader exactly the damage it is aimed at; anything else is
    JSON."""
    if artifact is None:
        return
    if isinstance(artifact, bytes):
        path.write_bytes(artifact)
    elif isinstance(artifact, str):
        path.write_text(artifact, encoding="utf-8")
    else:
        path.write_text(json.dumps(artifact), encoding="utf-8")


def _write_bundle(root: Path, name: str, *, tc_id: str | None = None,
                  sidecar: Any = None, export: Any = None, **tc: str) -> Path:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.yaml").write_text(_twin_config(tc_id or name, **tc), encoding="utf-8")
    _put(d / f"{name}.provenance.json", sidecar)
    _put(d / f"{name}.topology.json", export)
    return d


def _settings(tmp_path: Path, root: Path) -> ApiSettings:
    return ApiSettings(
        db_path=str(tmp_path / "state.db"),
        adapter_config="/nonexistent.conf",
        imprints_dir=str(root),
        # the `debian-12-cloud@v1` binding the plan path validates against
        images_root=str(FIXT_ROOT.resolve()),
    )


class _MustNotRun:
    """A deploy runner that only RECORDS a call: the deploy route's pre-check
    must refuse long before any runner is reached."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def __call__(self, *args: Any, **kwargs: Any) -> None:
        self.calls.append(args)


def _client(settings: ApiSettings, *, runner: _MustNotRun | None = None) -> TestClient:
    app = create_app(settings)
    if runner is not None:
        app.state.deploy_runner = runner
        app.state.deploy_background = lambda job: job()
    return TestClient(app, raise_server_exceptions=False)


def _cast(c: TestClient, name: str) -> dict:
    r = c.get(f"/api/twins/{name}/cast")
    assert r.status_code == 200, (name, r.status_code, r.text[:300])
    return r.json()


# ===========================================================================
# 1. The addressing guard -- the one id check R7 keeps -- on EVERY route
# ===========================================================================
def _renamed_catalog(root: Path, shape: str) -> None:
    """`renamed/` whose twin config declares `another-twin` (a copied,
    renamed directory -- LES-195's case), beside a healthy neighbour."""
    sidecar = {"normalized": _normalized("renamed"), "canonical": _CANONICAL,
               "absent": None}[shape]
    _write_bundle(root, "renamed", tc_id="another-twin", sidecar=sidecar,
                  export=_export("renamed"))
    _write_bundle(root, "healthy", sidecar=_CANONICAL, export=_export("healthy"))


@pytest.mark.parametrize("shape", _SHAPES)
def test_the_addressing_guard_refuses_on_every_route_that_resolves_an_imprint(tmp_path, shape):
    root = tmp_path / "imprints"
    _renamed_catalog(root, shape)
    settings = _settings(tmp_path, root)
    runner = _MustNotRun()
    c = _client(settings, runner=runner)

    listing = c.get("/api/twins")
    assert listing.status_code == 200, listing.text[:300]
    body = listing.json()
    assert [e["id"] for e in body["imprints"]] == ["healthy"]
    skipped = {s["id"]: s["reason"] for s in body["skipped"]}
    assert "twin_config.id='another-twin'" in skipped["renamed"], skipped

    for method, route in (
        ("GET", ""), ("GET", "/provenance"), ("GET", "/cast"),
        ("POST", "/plan"), ("POST", "/deploy"),
    ):
        url = f"/api/twins/renamed{route}"
        r = c.post(url, json={"target_node": "pxtest1"}) if method == "POST" else c.get(url)
        assert r.status_code == 400, (method, route, r.status_code, r.text[:300])
        assert "another-twin" in r.json()["error"], (route, r.json()["error"])

    # The deploy pre-check refused before anything was recorded or run.
    assert runner.calls == []
    store = StateStore(settings.db_path)
    try:
        assert store.latest_deploys(limit=10) == []
    finally:
        store.close()
    # ...and the refusal is scoped to the one bundle.
    assert _cast(c, "healthy")["id"] == "healthy"


@pytest.mark.parametrize("shape", _SHAPES)
def test_the_topology_route_refuses_a_renamed_bundle_too(tmp_path, shape):
    root = tmp_path / "imprints"
    _renamed_catalog(root, shape)
    r = _client(_settings(tmp_path, root)).get("/api/twins/renamed/topology")
    assert r.status_code in (400, 404), (r.status_code, r.text[:200])


# ===========================================================================
# 2. No 5xx on a damaged bundle -- every route, one damage at a time
# ===========================================================================
_X = "x"  # the damaged bundle's canonical id in every case below


def _case(**kw: Any) -> Callable[[Path], Any]:
    return lambda root: _write_bundle(root, _X, **kw)


def _export_is_a_directory(root: Path) -> None:
    d = _write_bundle(root, _X, sidecar=_CANONICAL)
    (d / f"{_X}.topology.json").mkdir()


def _sidecar_is_a_directory(root: Path) -> None:
    d = _write_bundle(root, _X, export=_export(_X))
    (d / f"{_X}.provenance.json").mkdir()


def _export_unreadable(root: Path) -> None:
    d = _write_bundle(root, _X, sidecar=_CANONICAL, export=_export(_X))
    (d / f"{_X}.topology.json").chmod(0)


def _sidecar_unreadable(root: Path) -> None:
    d = _write_bundle(root, _X, sidecar=_CANONICAL, export=_export(_X))
    (d / f"{_X}.provenance.json").chmod(0)


_BAD_UTF8 = b"\xff\xfe{}"  # also what a UTF-16 (BOM) JSON file starts with
_HUGE_WINDOWS = (
    '{"imprint_id": "x", "pit_id": "pit-x-1", "as_of": 1767225600.0, "windows": '
    + "9" * 400 + ', "segments": [], "nodes": [], "edges": [], "census": {}}'
)
_INFINITE_WINDOWS = (
    '{"imprint_id": "x", "pit_id": "pit-x-1", "as_of": 1767225600.0, '
    '"windows": 1e400, "segments": [], "nodes": [], "edges": [], "census": {}}'
)
_TWO_ROUTERS = (
    "    - { name: gw1, kind: router, image: debian-12-cloud@v1, requires: [linux],\n"
    "        nets: [{ segment: lan }, { segment: dmz }] }\n"
    "    - { name: gw2, kind: router, image: debian-12-cloud@v1, requires: [linux],\n"
    "        nets: [{ segment: dmz }, { segment: lan }] }\n"
    "    - { name: h, image: debian-12-cloud@v1, requires: [linux], net: lan, count: 3 }\n"
)
_DUPLICATE_LEGS = (
    "    - { name: gw, kind: router, image: debian-12-cloud@v1, requires: [linux],\n"
    "        nets: [{ segment: lan }, { segment: dmz }, { segment: lan }] }\n"
    "    - { name: h, image: debian-12-cloud@v1, requires: [linux], count: 2,\n"
    "        nets: [{ segment: lan }, { segment: lan }] }\n"
)

_DAMAGE: dict[str, Callable[[Path], Any]] = {
    # -- the export's baked imprint_id is not a string ------------------------
    "export-imprint-id-int": _case(sidecar=_CANONICAL, export=_export(42)),
    "export-imprint-id-int-no-pit": _case(
        sidecar=_CANONICAL, export=_export(42, drop=("pit_id",))),
    "export-imprint-id-object-no-pit": _case(
        sidecar=_CANONICAL, export=_export({"a": 1}, drop=("pit_id",))),
    "export-imprint-id-list-no-pit": _case(
        sidecar=_CANONICAL, export=_export(["a"], drop=("pit_id",))),
    "export-imprint-id-bool-no-pit": _case(
        sidecar=_CANONICAL, export=_export(True, drop=("pit_id",))),
    "export-pit-id-int": _case(sidecar=_CANONICAL, export=_export(_X, pit_id=42)),
    # -- the export is missing or unreadable ---------------------------------
    "export-missing": _case(sidecar=_CANONICAL),
    "export-not-json": _case(sidecar=_CANONICAL, export="{ torn"),
    "export-not-an-object": _case(sidecar=_CANONICAL, export="[]"),
    "export-invalid-utf8": _case(sidecar=_CANONICAL, export=_BAD_UTF8),
    "export-invalid-utf8-normalized": _case(sidecar=_normalized(_X), export=_BAD_UTF8),
    "export-is-a-directory": _export_is_a_directory,
    "export-unreadable": _export_unreadable,
    "export-windows-huge-int": _case(sidecar=_CANONICAL, export=_HUGE_WINDOWS),
    "export-windows-infinite": _case(sidecar=_CANONICAL, export=_INFINITE_WINDOWS),
    # -- the sidecar is missing, unreadable or of an unknown shape -----------
    "sidecar-missing": _case(export=_export(_X)),
    "sidecar-not-json": _case(sidecar="{ torn", export=_export(_X)),
    "sidecar-invalid-utf8": _case(sidecar=_BAD_UTF8, export=_export(_X)),
    "sidecar-is-a-directory": _sidecar_is_a_directory,
    "sidecar-unreadable": _sidecar_unreadable,
    "sidecar-unknown-schema": _case(
        sidecar={"schema": "axr-cast-provenance/2", "fields": {}}, export=_export(_X)),
    "sidecar-json-array": _case(sidecar=[], export=_export(_X)),
    "sidecar-canonical-without-fields": _case(
        sidecar={"schema": "axr-cast-provenance/1"}, export=_export(_X)),
    "sidecar-normalized-imprint-id-int": _case(sidecar=_normalized(42), export=_export(_X)),
    # -- the twin config R5 reads the roster and the legs from ---------------
    "conduits-empty": _case(conduits="  conduits: []\n", export=_export(_X)),
    "conduits-null": _case(conduits="  conduits:\n", export=_export(_X)),
    "conduits-dangling-ends": _case(
        conduits="  conduits:\n    - { from: lan, to: nowhere }\n    - { from: ghost, to: dmz }\n",
        export=_export(_X)),
    "two-routers": _case(nodes=_TWO_ROUTERS, export=_export(_X)),
    "duplicate-legs": _case(nodes=_DUPLICATE_LEGS, export=_export(_X)),
}

_ROUTES = ("list", "summary", "provenance", "topology", "cast", "plan")

#: (damage case, route) -> the PRE-EXISTING defect that cell still has,
#: marked `xfail(strict=True)`. Empty: all 14 cells this module was written
#: with -- the `/cast` pit-id fallback, the unchecked `/topology` export, the
#: `_coerce_int` overflow and the undecodable sidecar -- were fixed in the
#: GAP-082 review fix round and left with their markers.
_KNOWN: dict[tuple[str, str], str] = {}


def _damage_matrix():
    for case in _DAMAGE:
        for route in _ROUTES:
            marks = []
            if (case, route) in _KNOWN:
                marks.append(pytest.mark.xfail(strict=True, reason=_KNOWN[(case, route)]))
            if case.endswith("-unreadable"):
                marks.append(pytest.mark.skipif(
                    hasattr(os, "geteuid") and os.geteuid() == 0,
                    reason="root reads a mode-000 file"))
            yield pytest.param(case, route, id=f"{case}-{route}", marks=marks)


@pytest.fixture(scope="module")
def damaged(request, tmp_path_factory) -> TestClient:
    """ONE catalog -- the damaged bundle `x` beside a healthy neighbour -- and
    one client per damage case, shared by that case's route checks (building
    ~30 apps instead of ~180 keeps the matrix in the fast loop)."""
    case = request.param
    base = tmp_path_factory.mktemp(f"damaged-{case}")
    root = base / "imprints"
    _DAMAGE[case](root)
    _write_bundle(root, "healthy", sidecar=_CANONICAL, export=_export("healthy"))
    return _client(_settings(base, root))


@pytest.mark.parametrize(("damaged", "route"), list(_damage_matrix()), indirect=["damaged"])
def test_no_route_answers_5xx_on_a_damaged_bundle(damaged, route):
    """Author- or producer-damaged content is a 4xx or a published warning,
    never a 5xx (M1's rule) -- and never a catalog outage: a healthy
    neighbour stays listed, and the damaged bundle is ACCOUNTED FOR (listed
    or skipped with a reason), never silently gone (S5)."""
    c = damaged
    if route == "list":
        r = c.get("/api/twins")
        assert r.status_code == 200, (r.status_code, r.text[:300])
        body = r.json()
        listed = [e["id"] for e in body["imprints"]]
        assert "healthy" in listed, body
        assert _X in listed + [s["id"] for s in body["skipped"]], body
        return
    if route == "plan":
        r = c.post(f"/api/twins/{_X}/plan", json={"target_node": "pxtest1"})
    else:
        r = c.get(f"/api/twins/{_X}" + ("" if route == "summary" else f"/{route}"))
    assert r.status_code < 500, (route, r.status_code, r.text[:300])


# ===========================================================================
# 3. R5 -- the roster and the legs under adversarial twin configs
# ===========================================================================
def test_an_empty_conduits_block_publishes_an_empty_roster_and_keeps_the_legs(tmp_path):
    root = tmp_path / "imprints"
    _write_bundle(root, "b", conduits="  conduits: []\n")
    c = _client(_settings(tmp_path, root))
    j = _cast(c, "b")
    assert (j["conduits"], j["conduit_roster"]) == (0, [])
    assert j["routers"] == [{"name": "gw", "segments": ["lan", "dmz"]}]
    assert [e["id"] for e in c.get("/api/twins").json()["imprints"]] == ["b"]
    assert c.get("/api/twins/b").status_code == 200


def test_a_null_conduits_block_is_one_400_on_every_route_that_parses_the_config(tmp_path):
    """`conduits:` with no value is a shape error (a list is required) -- the
    SAME refusal on the summary, the cast and the plan, and the listing skips
    the bundle with the summary's own reason."""
    root = tmp_path / "imprints"
    _write_bundle(root, "b", conduits="  conduits:\n")
    c = _client(_settings(tmp_path, root))
    summary = c.get("/api/twins/b")
    assert summary.status_code == 400 and "conduits" in summary.json()["error"]
    assert c.get("/api/twins/b/cast").status_code == 400
    assert c.post("/api/twins/b/plan", json={"target_node": "pxtest1"}).status_code == 400
    body = c.get("/api/twins").json()
    assert body["imprints"] == []
    assert body["skipped"] == [{"id": "b", "reason": summary.json()["error"]}]


def test_conduits_naming_absent_segments_are_published_as_written_and_refused_at_plan(tmp_path):
    """The reader publishes, it does not interpret (R5): both ends verbatim,
    directed as written, counted -- the SPA reports what it cannot resolve.
    The expander is where a conduit no router implements is refused."""
    root = tmp_path / "imprints"
    _write_bundle(root, "b", conduits=(
        "  conduits:\n    - { from: lan, to: nowhere }\n    - { from: ghost, to: dmz }\n"
    ))
    c = _client(_settings(tmp_path, root))
    j = _cast(c, "b")
    assert j["conduit_roster"] == [
        {"from": "lan", "to": "nowhere"}, {"from": "ghost", "to": "dmz"},
    ]
    assert j["conduits"] == 2
    assert c.get("/api/twins/b").status_code == 200
    r = c.post("/api/twins/b/plan", json={"target_node": "pxtest1"})
    assert r.status_code == 400, r.text[:300]
    assert "'lan'->'nowhere'" in r.json()["error"], r.json()["error"]


def test_a_duplicated_roster_is_published_duplicates_and_all(tmp_path):
    """`len(conduit_roster) == conduits` holds with duplicates and reversed
    pairs in it -- deduping is the SPA's job, never the reader's."""
    root = tmp_path / "imprints"
    _write_bundle(root, "b", conduits=(
        "  conduits:\n"
        "    - { from: lan, to: dmz }\n"
        "    - { from: lan, to: dmz }\n"
        "    - { from: dmz, to: lan }\n"
    ))
    j = _cast(_client(_settings(tmp_path, root)), "b")
    assert j["conduits"] == 3 == len(j["conduit_roster"])
    assert j["conduit_roster"] == [
        {"from": "lan", "to": "dmz"}, {"from": "lan", "to": "dmz"}, {"from": "dmz", "to": "lan"},
    ]


def test_two_routers_publish_each_its_own_ordered_legs(tmp_path):
    """Each `kind: router` node with ITS legs, in declaration order, each once
    (per router since the GAP-082 review's S5, renamed from `router_segments`)
    -- and their union is still every segment ANY router reaches, in order. A
    multi-homed HOST's leg is not a router leg, and neither router is a host
    in the summary's count."""
    root = tmp_path / "imprints"
    _write_bundle(
        root, "b",
        segments=(
            "    - { name: lan, cidr: 10.5.0.0/24 }\n"
            "    - { name: dmz, cidr: 10.6.0.0/24 }\n"
            "    - { name: core, cidr: 10.7.0.0/24 }\n"
            "    - { name: iso, cidr: 10.8.0.0/24 }\n"
        ),
        conduits="  conduits:\n    - { from: lan, to: dmz }\n    - { from: core, to: lan }\n",
        nodes=(
            "    - { name: gw1, kind: router, image: debian-12-cloud@v1, requires: [linux],\n"
            "        nets: [{ segment: lan }, { segment: dmz }] }\n"
            "    - { name: gw2, kind: router, image: debian-12-cloud@v1, requires: [linux],\n"
            "        nets: [{ segment: core }, { segment: lan }] }\n"
            "    - { name: h, image: debian-12-cloud@v1, requires: [linux], count: 3,\n"
            "        nets: [{ segment: lan }, { segment: iso }] }\n"
        ),
    )
    c = _client(_settings(tmp_path, root))
    j = _cast(c, "b")
    assert j["routers"] == [
        {"name": "gw1", "segments": ["lan", "dmz"]},
        {"name": "gw2", "segments": ["core", "lan"]},
    ]
    union: list[str] = []
    for router in j["routers"]:
        union += [seg for seg in router["segments"] if seg not in union]
    assert union == ["lan", "dmz", "core"]
    s = c.get("/api/twins/b").json()
    assert s["hosts"] == 3 == j["hosts"]
    assert {seg["name"]: seg["host_count"] for seg in s["topology_segments"]} == {
        "lan": 3, "dmz": 0, "core": 0, "iso": 3,
    }


def test_duplicate_legs_are_published_once_and_never_crash_the_plan(tmp_path):
    root = tmp_path / "imprints"
    _write_bundle(root, "b", nodes=_DUPLICATE_LEGS)
    c = _client(_settings(tmp_path, root))
    assert _cast(c, "b")["routers"] == [{"name": "gw", "segments": ["lan", "dmz"]}]
    # Whether a duplicated HOST leg is legal is the validator's call (it
    # answers 422 today); the contract here is only that it never 5xxs.
    r = c.post("/api/twins/b/plan", json={"target_node": "pxtest1"})
    assert r.status_code < 500, (r.status_code, r.text[:300])


def test_a_single_homed_router_names_its_one_leg(tmp_path):
    """`net:` is the single-homed shorthand; on a router it is still a leg."""
    root = tmp_path / "imprints"
    _write_bundle(root, "b", conduits="  conduits: []\n", nodes=(
        "    - { name: gw, kind: router, image: debian-12-cloud@v1, requires: [linux], net: dmz }\n"
        "    - { name: h, image: debian-12-cloud@v1, requires: [linux], net: lan }\n"
    ))
    assert _cast(_client(_settings(tmp_path, root)), "b")["routers"] == [
        {"name": "gw", "segments": ["dmz"]},
    ]


def _assert_roster_is_the_raw_config(yaml_path: Path, cast: dict) -> None:
    """R5 against a RAW YAML parse, never the pydantic model the reader itself
    uses: the roster verbatim (duplicates and all), and every `kind: router`
    node with its `nets` (or single-homed `net`) in declaration order, each
    leg once (per router since the GAP-082 review's S5). Called on every
    bundle every shipped catalog lists (section 4) -- the build pinned only
    `corpapcn-twin`. No count is pinned here: an S6 apply legitimately
    recompiles a cast's twin config."""
    from ruamel.yaml import YAML

    raw = YAML(typ="safe").load(yaml_path.read_text(encoding="utf-8"))["twin_config"]
    assert cast["conduit_roster"] == [
        {"from": x["from"], "to": x["to"]} for x in (raw.get("conduits") or [])
    ], yaml_path.name
    assert cast["conduits"] == len(cast["conduit_roster"]), yaml_path.name
    routers: list[dict] = []
    for node in raw.get("nodes") or []:
        if node.get("kind") != "router":
            continue
        names = [leg["segment"] for leg in node.get("nets") or []] or (
            [node["net"]] if node.get("net") else []
        )
        legs: list[str] = []
        for seg in names:
            if seg not in legs:
                legs.append(seg)
        routers.append({"name": node["name"], "segments": legs})
    assert cast["routers"] == routers, yaml_path.name


# ===========================================================================
# 4. The listing and the summary partition every catalog the same way
# ===========================================================================
def _catalogs() -> list[Path]:
    roots = [CAST_FIXTURES, default_fixtures_dir()]
    if REAL_CAST_ROOT.is_dir():
        roots.append(REAL_CAST_ROOT)
    return roots


@pytest.mark.parametrize("root", _catalogs(), ids=lambda p: p.name)
def test_the_listing_and_the_summary_partition_every_shipped_catalog_identically(tmp_path, root):
    """Both directions, over every directory in the catalog: listed <=> the
    summary answers 200 with the entry's own numbers (and the cast opens);
    skipped => the summary answers 4xx, never 5xx, with the SAME reason
    wherever the bundle has a twin config to give one. No directory
    vanishes, none is both, and two reads agree to the byte -- warnings
    included."""
    c = _client(_settings(tmp_path, root))
    body = c.get("/api/twins").json()
    listed = {e["id"]: e for e in body["imprints"]}
    skipped = {s["id"]: s["reason"] for s in body["skipped"]}
    # `_`-prefixed: a `twins/` root's cross-twin assets folder, never a bundle.
    candidates = {
        d.name for d in root.iterdir() if d.is_dir() and not d.name.startswith((".", "_"))
    }
    assert len(listed) == len(body["imprints"]), "duplicate ids in the listing"
    assert not listed.keys() & skipped.keys()
    assert listed.keys() | skipped.keys() == candidates
    for name in sorted(candidates):
        first, second = c.get(f"/api/twins/{name}"), c.get(f"/api/twins/{name}")
        assert (first.status_code, first.json()) == (second.status_code, second.json()), name
        if name in listed:
            assert first.status_code == 200, (name, first.text[:300])
            s, e = first.json(), listed[name]
            for key in ("id", "as_of", "hosts", "segments", "mean_confidence",
                        "confidence_source"):
                assert s[key] == e[key], (name, key, s[key], e[key])
            assert e["has_provenance"] is (s["sidecar_shape"] != "absent"), name
            cast = _cast(c, name)
            assert (cast["hosts"], cast["segments"]) == (s["hosts"], s["segments"]), name
            _assert_roster_is_the_raw_config(bundle_config_path(root / name, name), cast)
        else:
            assert 400 <= first.status_code < 500, (name, first.status_code)
            if (root / name / f"{name}.yaml").is_file():
                assert first.json()["error"] == skipped[name], name


@pytest.mark.parametrize("sidecar", [
    pytest.param({"schema": "axr-cast-provenance/1"}, id="canonical-without-fields"),
    pytest.param({"schema": "axr-cast-provenance/1", "fields": []},
                 id="canonical-fields-not-an-object"),
    pytest.param(_CANONICAL, id="well-formed-canonical"),
])
def test_a_bundle_lists_if_and_only_if_its_cast_opens(tmp_path, sidecar):
    root = tmp_path / "imprints"
    _write_bundle(root, "b", sidecar=sidecar, export=_export("b"))
    c = _client(_settings(tmp_path, root))
    listed = "b" in [e["id"] for e in c.get("/api/twins").json()["imprints"]]
    summarizes = c.get("/api/twins/b").status_code == 200
    opens = c.get("/api/twins/b/cast").status_code == 200
    assert listed == summarizes == opens, {
        "listed": listed, "summarizes": summarizes, "cast opens": opens,
    }


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "1e400"])
def test_a_non_finite_canonical_confidence_never_publishes_a_null_mean(tmp_path, literal):
    root = tmp_path / "imprints"
    _write_bundle(root, "b", export=_export("b"), sidecar=(
        '{"schema": "axr-cast-provenance/1", "fields": {'
        '"a": {"class": "observed", "confidence": %s}, '
        '"b": {"class": "observed", "confidence": 0.6}}}' % literal
    ))
    c = _client(_settings(tmp_path, root))
    docs = list(c.get("/api/twins").json()["imprints"])
    summary = c.get("/api/twins/b")
    if summary.status_code == 200:
        docs.append(summary.json())
    assert docs, "neither listed nor summarized"
    for doc in docs:
        mean = doc["mean_confidence"]
        assert isinstance(mean, (int, float)) and math.isfinite(mean), doc


# ===========================================================================
# 5. The plan path R7 unblocked, on a bundle shaped like a real cast
# ===========================================================================
def test_a_cast_bundle_with_relative_script_installs_plans_through_the_api(tmp_path):
    root = tmp_path / "imprints"
    shutil.copytree(CAST_FIXTURES / "mini-cast-prov", root / "mini-cast-prov")
    c = _client(_settings(tmp_path, root))
    # R7's half holds: the summary pre-check no longer refuses a cast bundle.
    assert c.get("/api/twins/mini-cast-prov").status_code == 200
    r = c.post("/api/twins/mini-cast-prov/plan", json={"target_node": "pxtest1"})
    assert r.status_code == 200, r.text[:400]


# ===========================================================================
# 6. The GAP-082 review fix round's new surfaces (tester re-test, 2026-09-11):
#    `check_imprint_addressable`'s 404/400 split on EVERY route; the
#    `unrecognized` sidecar shape and the shared `fields` precondition, each
#    agreeing across routes; mistyped NESTED export fields; per-router
#    `routers` content; the symlinked pit bundle's operator routes; and the
#    materialized tempfile on the `config:` deploy path.
# ===========================================================================
def _assertions_doc(imprint_id: str) -> dict:
    return {
        "schema": "axr-assertions/1", "imprint": imprint_id,
        "as_of": "2026-09-11T00:00:00Z", "source": "operator: tester",
        "add": [], "override": [],
        "suppress": [{"key": "address:10.5.0.9", "note": "out of scope"}],
    }


def _layout_doc(imprint_id: str) -> dict:
    return {"schema": "axr-layout/1", "imprint": imprint_id,
            "pins": {"site:10.5": {"x": 1, "y": 2}}}


def _every_route(imprint_id: str) -> list[tuple[str, str, Any]]:
    """(method, path, body) for EVERY route that resolves an imprint by id:
    the reads, both operator-file writes, apply, diff, plan and deploy."""
    return [
        ("GET", "", None), ("GET", "/provenance", None), ("GET", "/cast", None),
        ("GET", "/topology", None), ("GET", "/assertions", None), ("GET", "/layout", None),
        ("GET", "/diff?since=0123456789abcdef", None),
        ("PUT", "/assertions", _assertions_doc(imprint_id)),
        ("PUT", "/layout", _layout_doc(imprint_id)),
        ("POST", "/apply", {"document": _assertions_doc(imprint_id)}),
        ("POST", "/plan", {"target_node": "pxtest1"}),
        ("POST", "/deploy", {"target_node": "pxtest1"}),
    ]


def _tree(root: Path) -> list[tuple[str, int]]:
    """Every path under `root` (dot-entries included) with its size -- what a
    refused write must leave exactly as it found it."""
    return sorted(
        (str(p.relative_to(root)), p.lstat().st_size) for p in root.rglob("*")
    )


def _deploy_rows(settings: ApiSettings) -> list:
    store = StateStore(settings.db_path)
    try:
        return store.latest_deploys(limit=10)
    finally:
        store.close()


@pytest.mark.parametrize("ref", ["nope", "%2e", "%2e%2e", ".hidden"],
                         ids=["unknown", "dot", "dot-dot", "existing-dot-directory"])
def test_an_unaddressable_id_is_404_on_every_route(tmp_path, ref):
    """The 404 half of `check_imprint_addressable`: no such bundle, or an id
    that is not a catalog id at all -- including a dot-directory that EXISTS
    and holds a well-formed bundle. Every route answers 404, nothing is
    written anywhere in the catalog, and no deploy is recorded or run."""
    root = tmp_path / "imprints"
    _write_bundle(root, "healthy", sidecar=_CANONICAL, export=_export("healthy"))
    hidden = root / ".hidden"
    hidden.mkdir()
    (hidden / ".hidden.yaml").write_text(_twin_config(".hidden"), encoding="utf-8")
    settings = _settings(tmp_path, root)
    runner = _MustNotRun()
    c = _client(settings, runner=runner)
    before = _tree(root)
    for method, route, body in _every_route(ref):
        r = c.request(method, f"/api/twins/{ref}{route}", json=body)
        assert r.status_code == 404, (ref, method, route, r.status_code, r.text[:200])
    assert runner.calls == [] and _deploy_rows(settings) == []
    assert _tree(root) == before


def _unaddressable(root: Path, case: str) -> None:
    """A directory that EXISTS under a usable id but is not an addressable
    bundle -- the 400 half."""
    d = root / "b"
    d.mkdir(parents=True)
    if case == "renamed":
        (d / "b.yaml").write_text(_twin_config("another-twin"), encoding="utf-8")
    elif case == "unparseable-yaml":
        (d / "b.yaml").write_text("twin_config: [unclosed\n", encoding="utf-8")
    elif case == "no-twin-config-key":
        (d / "b.yaml").write_text("scenario:\n  id: b\n", encoding="utf-8")
    elif case == "only-a-generic-stem":
        # The compiler's own output names (`twin_config.yaml`, ...), declaring
        # the directory's id -- but there is no `b.yaml` to address.
        (d / "twin_config.yaml").write_text(_twin_config("b"), encoding="utf-8")
    elif case == "renamed-beside-a-stray-yaml-naming-the-dir":
        (d / "b.yaml").write_text(_twin_config("another-twin"), encoding="utf-8")
        (d / "twin_config.yaml").write_text(_twin_config("b"), encoding="utf-8")
    else:  # pragma: no cover
        raise AssertionError(case)
    _put(d / "b.provenance.json", _CANONICAL)
    _put(d / "b.topology.json", _export("b"))


@pytest.mark.parametrize("case", [
    "renamed", "unparseable-yaml", "no-twin-config-key", "only-a-generic-stem",
    "renamed-beside-a-stray-yaml-naming-the-dir",
])
def test_an_unaddressable_bundle_is_400_on_every_route(tmp_path, case):
    """The 400 half of `check_imprint_addressable`, over EVERY route -- the
    guard's own docstring promises the routes "agree on what an addressable
    bundle is". A directory that exists but whose `<id>.yaml` is missing,
    unparseable, not a twin config, or declares another twin is refused
    everywhere (400, never 404, never 5xx), nothing is written, no deploy is
    recorded or run, and the listing skips it."""
    root = tmp_path / "imprints"
    _unaddressable(root, case)
    _write_bundle(root, "healthy", sidecar=_CANONICAL, export=_export("healthy"))
    settings = _settings(tmp_path, root)
    runner = _MustNotRun()
    c = _client(settings, runner=runner)
    before = _tree(root)
    statuses = {}
    for method, route, body in _every_route("b"):
        r = c.request(method, f"/api/twins/b{route}", json=body)
        statuses[f"{method} {route or '(summary)'}"] = r.status_code
    assert set(statuses.values()) == {400}, statuses
    assert runner.calls == [] and _deploy_rows(settings) == []
    assert _tree(root) == before
    body = c.get("/api/twins").json()
    assert [e["id"] for e in body["imprints"]] == ["healthy"]
    assert [s["id"] for s in body["skipped"]] == ["b"]


@pytest.mark.parametrize("schema", [
    "axr-cast-provenance/2", 5, "", ["axr-cast-provenance/1"], {"version": 2},
], ids=["future-version", "number", "empty-string", "list", "object"])
def test_an_unrecognized_sidecar_is_present_and_unconsumed_on_every_route(tmp_path, schema):
    """`unrecognized` (a `schema` this reader does not consume): PRESENT and
    NOT consumed, stated the same way by the summary, the listing, `/cast`
    and `/provenance` -- and nothing that never reads the sidecar (the
    topology export, the plan) is affected. The sidecar even names ANOTHER
    imprint: an unconsumed document is not read for an id either."""
    root = tmp_path / "imprints"
    _write_bundle(root, "b", export=_export("b"), sidecar={
        "schema": schema, "imprint_id": "some-other-imprint",
        "fields": {"twin_config.nodes[1].role": {"class": "observed", "confidence": 0.9}},
    })
    c = _client(_settings(tmp_path, root))
    summary = c.get("/api/twins/b")
    assert summary.status_code == 200, summary.text[:300]
    s = summary.json()
    assert (s["sidecar_shape"], s["mean_confidence"], s["confidence_source"]) == (
        "unrecognized", 0.0, "unrecognized sidecar (not consumed)",
    )
    assert any("not one this reader consumes" in w for w in s["warnings"]), s["warnings"]
    [entry] = c.get("/api/twins").json()["imprints"]
    assert (entry["id"], entry["has_provenance"], entry["confidence_source"]) == (
        "b", True, s["confidence_source"],
    )
    cast = _cast(c, "b")
    assert (cast["sidecar_shape"], cast["has_provenance"]) == ("unrecognized", True)
    prov = c.get("/api/twins/b/provenance")
    assert prov.status_code == 200, prov.text[:300]
    assert (prov.json()["sidecar_shape"], prov.json()["rows"]) == ("unrecognized", [])
    assert c.get("/api/twins/b/topology").status_code == 200
    r = c.post("/api/twins/b/plan", json={"target_node": "pxtest1"})
    assert r.status_code == 200, r.text[:300]


@pytest.mark.parametrize("sidecar", [
    {"schema": "axr-cast-provenance/1"},
    {"schema": "axr-cast-provenance/1", "fields": None},
    {"schema": "axr-cast-provenance/1", "fields": "every field"},
    {"schema": "axr-cast-provenance/1", "fields": []},
    {"imprint_id": "b", "as_of": "2026-01-01T00:00:00Z", "summary": {
        "services": 1, "personas": 1, "mean_confidence": 0.5, "out_of_vocab_pct": 0.0}},
    # No `unrecognized-missing` case since the GAP-082 review's round 2 (SF3):
    # the shape is detected FIRST, and only the two shapes that are READ owe a
    # `fields` object. An unrecognized schema is present and unconsumed with
    # or without one (test_imprints_reader.py::
    # test_an_unrecognized_sidecar_is_held_to_no_precondition).
], ids=["canonical-missing", "canonical-null", "canonical-string", "canonical-list",
        "normalized-missing"])
def test_a_sidecar_without_a_fields_object_is_refused_in_one_voice_by_every_reader(tmp_path, sidecar):
    """ONE precondition (`sidecar_fields_or_raise`), so every route that READS
    the sidecar refuses the same file in the same words -- the summary,
    `/cast`, `/provenance` and the listing's skip reason, word for word --
    for both shapes that are read (canonical and normalized), while the
    routes that never read it (the topology export, the plan) are untouched,
    and a healthy neighbour stays listed."""
    root = tmp_path / "imprints"
    _write_bundle(root, "b", sidecar=sidecar, export=_export("b"))
    _write_bundle(root, "healthy", sidecar=_CANONICAL, export=_export("healthy"))
    c = _client(_settings(tmp_path, root))
    refusals = {}
    for route in ("", "/cast", "/provenance"):
        r = c.get(f"/api/twins/b{route}")
        assert r.status_code == 400, (route, r.status_code, r.text[:300])
        refusals[route or "summary"] = r.json()["error"]
    assert len(set(refusals.values())) == 1, refusals
    assert "`fields` object" in refusals["summary"], refusals
    body = c.get("/api/twins").json()
    assert [e["id"] for e in body["imprints"]] == ["healthy"]
    assert body["skipped"] == [{"id": "b", "reason": refusals["summary"]}]
    assert c.get("/api/twins/b/topology").status_code == 200
    r = c.post("/api/twins/b/plan", json={"target_node": "pxtest1"})
    assert r.status_code == 200, r.text[:300]


#: A topology export with one segment and one node, so a NESTED value has
#: somewhere to be wrong.
_NESTED_BASE = {
    "imprint_id": "b", "pit_id": "pit-b-1", "as_of": 1767225600.0, "windows": 3,
    "segments": [{"cidr": "10.5.0.0/24", "name": "seg-10-5-0-0", "entities": 1}],
    "nodes": [{"id": "address:10.5.0.2", "address": "10.5.0.2", "locality": "local",
               "segment": "10.5.0.0/24", "windows_seen": 3}],
    "edges": [{"src": "address:10.5.0.2", "dst": "address:10.6.0.3", "proto": "tcp",
               "bytes": 10, "flows": 1}],
    "census": {"entities_total": 1},
}


def _nested(**patch: Any) -> dict:
    """`_NESTED_BASE` with ONE value replaced -- `segments__0__cidr=...`."""
    doc = json.loads(json.dumps(_NESTED_BASE))
    for path, value in patch.items():
        target: Any = doc
        keys = path.split("__")
        for key in keys[:-1]:
            target = target[int(key)] if key.isdigit() else target[key]
        last = keys[-1]
        target[int(last) if last.isdigit() else last] = value
    return doc


_NESTED: dict[str, dict] = {
    "census-list": _nested(census=[1]),
    "census-number": _nested(census=5),
    "census-value-string": _nested(census={"entities_total": "lots"}),
    "segment-cidr-list": _nested(segments__0__cidr=["10.5.0.0/24"]),
    "segment-cidr-object": _nested(segments__0__cidr={"a": 1}),
    "segment-entities-string": _nested(segments__0__entities="many"),
    "segment-coverage-object": _nested(segments__0__coverage_source={"a": 1}),
    "segment-not-an-object": _nested(segments=[5]),
    "node-segment-list": _nested(nodes__0__segment=["10.5.0.0/24"]),
    "node-segment-object": _nested(nodes__0__segment={"a": 1}),
    "node-address-number": _nested(nodes__0__address=42),
    "node-role-confidence-string": _nested(nodes__0__role_confidence="high"),
    "node-listen-ports-number": _nested(nodes__0__listen_ports=5),
    "node-listen-ports-objects": _nested(nodes__0__listen_ports=[{"port": 1}]),
    "edge-bytes-string": _nested(edges__0__bytes="big"),
    "edge-proto-object": _nested(edges__0__proto={"x": 1}),
    "edges-object": _nested(edges={"a": 1}),
    "nodes-object": _nested(nodes={"a": 1}),
}


@pytest.mark.parametrize("case", list(_NESTED))
def test_a_mistyped_nested_export_field_is_never_a_5xx(tmp_path, case):
    """GAP-082 review: a NESTED export value of the wrong type is
    producer-damaged content -- a 4xx naming it, never a 5xx -- on the routes
    that parse the export's object graph (`/topology`, `/cast`, and
    `/provenance`, which loads the same bundle). The summary and the listing
    read top-level scalars only and answer 200 regardless."""
    root = tmp_path / "imprints"
    _write_bundle(root, "b", export=_NESTED[case])
    c = _client(_settings(tmp_path, root))
    statuses = {"list": c.get("/api/twins").status_code}
    for route in ("", "/topology", "/cast", "/provenance"):
        statuses[route or "summary"] = c.get(f"/api/twins/b{route}").status_code
    assert all(status < 500 for status in statuses.values()), statuses
    assert (statuses["summary"], statuses["list"]) == (200, 200), statuses


@pytest.mark.parametrize("nodes", [
    # a router with no leg at all
    "    - { name: gw, kind: router, image: debian-12-cloud@v1, requires: [linux] }\n"
    "    - { name: h, image: debian-12-cloud@v1, requires: [linux], net: lan }\n",
    # two router specs under ONE name
    "    - { name: gw, kind: router, image: debian-12-cloud@v1, requires: [linux], net: lan }\n"
    "    - { name: gw, kind: router, image: debian-12-cloud@v1, requires: [linux], net: dmz }\n"
    "    - { name: h, image: debian-12-cloud@v1, requires: [linux], net: lan }\n",
    # a leg on a segment the config does not declare (the cast's `internet-sink`)
    "    - { name: gw, kind: router, image: debian-12-cloud@v1, requires: [linux],\n"
    "        nets: [{ segment: lan }, { segment: internet-sink }] }\n"
    "    - { name: h, image: debian-12-cloud@v1, requires: [linux], net: lan }\n",
    # three routers, legs shared and re-ordered between them
    "    - { name: r1, kind: router, image: debian-12-cloud@v1, requires: [linux],\n"
    "        nets: [{ segment: dmz }, { segment: lan }] }\n"
    "    - { name: r2, kind: router, image: debian-12-cloud@v1, requires: [linux], net: lan }\n"
    "    - { name: r3, kind: router, image: debian-12-cloud@v1, requires: [linux],\n"
    "        nets: [{ segment: lan }, { segment: dmz }, { segment: dmz }] }\n"
    "    - { name: h, image: debian-12-cloud@v1, requires: [linux], net: lan }\n",
], ids=["router-without-legs", "two-routers-one-name", "leg-on-an-undeclared-segment",
        "three-routers-shared-legs"])
def test_multi_router_content_is_published_as_the_raw_config_spells_it(tmp_path, nodes):
    """`routers` is every `kind: router` node with ITS legs, each once, in
    declaration order -- compared with a RAW YAML parse. No router is ever a
    host in the summary's count, and none of these shapes 5xxs the plan."""
    root = tmp_path / "imprints"
    d = _write_bundle(root, "b", conduits="  conduits: []\n", nodes=nodes)
    c = _client(_settings(tmp_path, root))
    cast = _cast(c, "b")
    _assert_roster_is_the_raw_config(d / "b.yaml", cast)
    assert c.get("/api/twins/b").json()["hosts"] == 1
    r = c.post("/api/twins/b/plan", json={"target_node": "pxtest1"})
    assert r.status_code < 500, r.text[:300]


def test_a_router_spec_with_a_count_publishes_every_router_it_fans_out_to(tmp_path):
    """`count: 2` on a `kind: router` node fans out to TWO router nodes
    (`gw01`, `gw02` -- the same expander fan-out the summary counts per
    instance for hosts). A roster with ONE entry for that spec hands the
    Console, whose WAN label says "the gateway" only for exactly one router
    (GAP-082 review, S5), a one-router picture of a two-router twin. How the
    reader says "two" -- one entry per instance, or a count on the entry -- is
    its call; this counts either way."""
    from axrange.twinconfig.expand import expand_twin_config

    root = tmp_path / "imprints"
    _write_bundle(root, "b", conduits="  conduits: []\n", nodes=(
        "    - { name: gw, kind: router, count: 2, image: debian-12-cloud@v1, requires: [linux],\n"
        "        nets: [{ segment: lan }, { segment: dmz }] }\n"
        "    - { name: h, image: debian-12-cloud@v1, requires: [linux], net: lan }\n"
    ))
    ir, _params = expand_twin_config("b", catalog_dir=root)
    built = [n for n in ir["layers"]["infra"]["nodes"] if n.get("kind") == "router"]
    assert len(built) == 2, built  # what a deploy of this twin builds
    cast = _cast(_client(_settings(tmp_path, root)), "b")
    assert sum(int(r.get("count", 1)) for r in cast["routers"]) == len(built), cast["routers"]


PIT = "pit-395a61f04e-1780405200"
# The pit twin's REAL content (`twins/<pit>/`, was `twin-configs-cast/
# corpapcn-disco/`). The symlinked bundle these tests are about -- a
# `<pit>/` of three links into a generically-named `corpapcn-disco/`, the
# shape the discovery loop produced -- no longer exists in the repo, so
# `_symlinked_pit` rebuilds it in `tmp_path` from that real content.
REAL_PIT_TWIN = CATALOG / "twins" / PIT


def test_the_real_pit_twin_the_symlink_tests_rebuild_from_is_present():
    """Not a skip: the two symlink tests below must never go silently green
    because the content they are built from moved again."""
    assert (REAL_PIT_TWIN / "te.yaml").is_file()
    assert (REAL_PIT_TWIN / "generated" / f"{PIT}.topology.json").is_file()


def _symlinked_pit(root: Path) -> tuple[Path, Path]:
    """`root/corpapcn-disco/` holding the pit twin's content under the cast's
    GENERIC stems, and `root/<pit>/` of three relative links into it -- the
    layout the discovery loop wrote (`twin_config.yaml`, `topology.json`,
    `provenance.json`, with `scripts/` beside the link TARGET)."""
    disco, pit = root / "corpapcn-disco", root / PIT
    disco.mkdir(parents=True)
    shutil.copy(REAL_PIT_TWIN / "te.yaml", disco / "twin_config.yaml")
    for kind in ("topology", "provenance"):
        shutil.copy(REAL_PIT_TWIN / "generated" / f"{PIT}.{kind}.json", disco / f"{kind}.json")
    for sub in ("scripts", "personas"):
        shutil.copytree(REAL_PIT_TWIN / sub, disco / sub)
    pit.mkdir()
    for name, target in ((f"{PIT}.yaml", "twin_config.yaml"),
                         (f"{PIT}.topology.json", "topology.json"),
                         (f"{PIT}.provenance.json", "provenance.json")):
        (pit / name).symlink_to(f"../corpapcn-disco/{target}")
    return pit, disco


def _links(d: Path) -> dict[str, str]:
    return {p.name: os.readlink(p) for p in d.iterdir() if p.is_symlink()}


def _digest(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*")) if p.is_file() and not p.is_symlink()
    }


def test_the_symlinked_pit_bundle_passes_the_guard_on_its_operator_read_routes(tmp_path):
    """READ-ONLY, on the rebuilt symlinked bundle (`_symlinked_pit`, real
    content): the pit bundle's `<id>.yaml` is a link whose TARGET declares
    `twin_config.id: pit-395a61f04e-1780405200`, and the one addressing guard
    reads through it -- the assertions and layout reads answer for the
    bundle, and the diff answers for itself (an unknown fingerprint: nothing
    has been applied here) rather than 400ing at the guard. The links are
    untouched."""
    root = tmp_path / "imprints"
    real_pit, _disco = _symlinked_pit(root)
    links = _links(real_pit)
    c = _client(_settings(tmp_path, root))
    for route in ("/assertions", "/layout"):
        r = c.get(f"/api/twins/{PIT}{route}")
        assert r.status_code == 200, (route, r.text[:300])
        assert r.json()["imprint_id"] == PIT
    r = c.get(f"/api/twins/{PIT}/diff?since=0123456789abcdef")
    assert r.status_code == 404, r.text[:300]
    assert "twin_config.id" not in r.json()["error"], r.json()["error"]
    assert _links(real_pit) == links


class _RecordingRecompile:
    """A recompile backend that only RECORDS -- the suite never runs
    aximprint, so nothing is installed and the bundle's content does not
    change."""

    def __init__(self, imprint_id: str) -> None:
        self.calls: list = []
        self._document = {
            "imprint_id": imprint_id,
            "record_counts": {"add": 0, "override": 0, "suppress": 1},
        }

    def recompile(self, request):
        from axrange.api.recompile import RecompileResult

        self.calls.append(request)
        return RecompileResult(
            argv=["fake", "cast"], returncode=0, stdout=json.dumps(self._document),
            stderr="", document=self._document,
        )


def test_the_symlinked_pit_bundle_takes_operator_writes_without_writing_through_its_links(tmp_path):
    """On a scratch COPY of the pit bundle and its link target (links kept as
    links): the layout PUT, an apply (a recording backend) and a diff against
    the apply's own snapshot all pass the addressing guard. Every file axRange
    writes lands in the pit directory as a regular file; the three links
    still point where they did; the directory they point into is
    byte-identical afterwards."""
    root = tmp_path / "imprints"
    pit, disco = _symlinked_pit(root)
    links = _links(pit)
    assert set(links) == {f"{PIT}.yaml", f"{PIT}.topology.json", f"{PIT}.provenance.json"}
    disco_before = _digest(disco)
    settings = _settings(tmp_path, root)
    settings.aximprint_bin = "/usr/bin/false-aximprint"  # never executed
    settings.aximprint_store = str(tmp_path / "ir.duckdb")
    backend = _RecordingRecompile(PIT)
    app = create_app(settings)
    app.state.recompile_backend = backend
    c = TestClient(app, raise_server_exceptions=False)

    layout = c.get(f"/api/twins/{PIT}/layout")
    assert layout.status_code == 200, layout.text[:300]
    etag = layout.json()["etag"]
    put = c.put(f"/api/twins/{PIT}/layout", json=_layout_doc(PIT),
                headers={"If-Match": etag} if etag else {})
    assert put.status_code == 200, put.text[:300]
    applied = c.post(f"/api/twins/{PIT}/apply", json={"document": _assertions_doc(PIT)})
    assert applied.status_code == 200, applied.text[:400]
    assert len(backend.calls) == 1
    diff = c.get(f"/api/twins/{PIT}/diff?since={applied.json()['fingerprint_before']}")
    assert diff.status_code == 200, diff.text[:300]
    assert c.get(f"/api/twins/{PIT}/assertions").json()["exists"] is True

    assert _links(pit) == links
    assert _digest(disco) == disco_before
    written = {p.name for p in pit.iterdir() if not p.is_symlink()}
    assert {"layout.json", f"{PIT}.assertions.yaml", ".history"} <= written, written


def test_a_config_deploy_plans_from_the_config_directory_and_leaves_no_tempfile(tmp_path):
    """The `config:` DEPLOY source end to end through `create_deploy` (the
    imprint source has its own test): relative scripts are read from the twin
    config's directory, the materialized IR is gone once planning is done --
    and a second deploy of the same config, refused or resumed AFTER
    planning, leaves nothing behind either. A recording runner and a scratch
    state DB: nothing is deployed."""
    from axrange.api import dtos, service
    from axrange.api.service import ApiError

    private = Path(tempfile.gettempdir())  # this test's own (`_private_tempdir`)
    catalog = tmp_path / "twin-configs"
    shutil.copytree(CAST_FIXTURES / "mini-cast-prov", catalog / "mini-cast-prov")
    settings = ApiSettings(
        db_path=str(tmp_path / "state.db"), adapter_config="/nonexistent.conf",
        twin_configs_dir=str(catalog), images_root=str(FIXT_ROOT.resolve()),
    )
    calls: list[tuple] = []
    request = dtos.CreateDeployRequest(config="mini-cast-prov", target_node="pxtest1")
    service.create_deploy(settings, request, runner=lambda *args: calls.append(args),
                          background=lambda job: job())
    assert len(calls) == 1
    plan = calls[0][1]
    ops = list(plan.ops.values()) if isinstance(plan.ops, dict) else list(plan.ops)
    shipped = {p.read_text() for p in (catalog / "mini-cast-prov" / "scripts").iterdir()}
    run_scripts = [op for op in ops if op.op_name == "run-script"]
    assert run_scripts
    assert all(op.inputs_resolved["script_content"] in shipped for op in run_scripts)
    assert list(private.iterdir()) == []
    try:
        service.create_deploy(settings, request, runner=lambda *args: calls.append(args),
                              background=lambda job: job())
    except ApiError:
        pass
    assert list(private.iterdir()) == []
