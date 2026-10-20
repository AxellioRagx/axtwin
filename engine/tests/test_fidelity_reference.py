"""`axrange.fidelity.reference` — the cast-bundle reader (S1).

Includes the MANDATORY shipped-content regression (LES-185): the REAL
`twin-configs-cast/corpapcn-twin/` bundle, loaded end to end. A fixture
exercising row 1 cannot prove row 1 works on `count:`-collapsed groups
unless it mirrors that shape, and the fixture the rest of these tests use
mirrors it deliberately — but only the real bundle proves the reader
survives the producer's actual output.
"""
from __future__ import annotations

import json

import pytest

from axrange.fidelity.reference import (
    ABSENT,
    CAST_PROVENANCE_SCHEMA,
    PRESENT,
    REDERIVED_WARNING,
    SOURCE_REDERIVED,
    SOURCE_SIDECAR,
    ReferenceError,
    load_reference_bundle,
    resolve_bundle_dir,
)
from tests.fidelity_support import (
    CAST_BUNDLES,
    MINI_CAST,
    MINI_CAST_PROV,
    REAL_CAST,
    REPO_ROOT,
)

FIXTURE_IMPRINT = REPO_ROOT / "src" / "axrange" / "api" / "fixtures" / "imprints" / "corp-enterprise"


# ---------------------------------------------------------------------------
# Shipped content (LES-185)
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not REAL_CAST.is_dir(), reason="shipped cast bundle absent")
def test_real_corpapcn_bundle_loads_end_to_end():
    """S1's accept criteria, against the real bundle."""
    b = load_reference_bundle(REAL_CAST)
    assert b.bundle_id == "corpapcn-twin"
    assert len(b.observed_nodes) == 786
    assert len(b.observed_edges) == 1437
    assert len(b.observed_segments) == 32
    assert len(b.groups) == 62
    # `expanded_nodes` counts the router too (the cast's single `gw`);
    # `expanded_hosts` does not. Published separately on purpose — conflating
    # them is how a router silently becomes a host in a coverage count.
    assert b.expanded_nodes == 138
    assert b.expanded_hosts == 137
    assert len(b.placed_observed) == 141      # the census's own `placed`
    assert b.census.get("placed") == 141
    assert b.windows == 11
    assert b.topology_schema_version == "1.3"
    assert b.topology_export == PRESENT
    # §8.1: reported, never raised, and never silently re-derived without
    # saying so.
    assert b.provenance_sidecar == ABSENT
    assert any("provenance sidecar absent" in w for w in b.warnings)


@pytest.mark.skipif(not REAL_CAST.is_dir(), reason="shipped cast bundle absent")
def test_real_bundle_pit_id_is_not_treated_as_the_bundle_id():
    """The export's `imprint_id` is a PIT id by producer design. Publishing
    it as `pit_id` and cross-checking it against the directory name are two
    different things — only the twin config's own `id` is cross-checked
    (LES-195), or every real bundle would refuse to load."""
    b = load_reference_bundle(REAL_CAST)
    assert b.pit_id and b.pit_id.startswith("pit-")
    assert b.pit_id != b.bundle_id
    assert b.to_dict()["bundle_id"] == "corpapcn-twin"


# ---------------------------------------------------------------------------
# The fixture bundle + the sidecar-carrying imprint fixture
# ---------------------------------------------------------------------------
def test_mini_cast_bundle_shape():
    b = load_reference_bundle(MINI_CAST)
    assert b.bundle_id == "mini-cast"
    assert b.windows == 5
    assert {g.name for g in b.groups} == {
        "gw", "web-tier", "db-tier", "thin-endpoint", "sink", "win-endpoint",
    }
    by_name = {g.name: g for g in b.groups}
    # The `count:` fan-out must use the EXPANDER's own id rule, or row 3
    # compares against ids the deploy never minted.
    assert by_name["web-tier"].node_ids == ("web-tier01", "web-tier02")
    assert by_name["db-tier"].node_ids == ("db-tier",)
    assert by_name["gw"].is_router
    # One router legging every segment — the real cast's degenerate shape.
    assert len(by_name["gw"].segments) == 4
    assert b.expanded_nodes == 7 and b.expanded_hosts == 6


def test_imprint_fixture_has_a_sidecar_that_is_reported_but_not_consumed():
    """S1's second accept, plus M4: the Track-C imprint fixture yields 42
    hosts / 4 segments and reports its topology export absent. Its sidecar
    is the NORMALIZED Track-C shape, not the canonical compiler one, so it
    is reported `present` and deliberately NOT consumed -- two shapes exist
    and disagree, and reading one as the other is the fabrication the whole
    evidence model exists to prevent."""
    b = load_reference_bundle(FIXTURE_IMPRINT)
    assert b.expanded_hosts == 42
    assert b.expanded_nodes == 43        # + the `gw` router
    assert len(b.twin_segments) == 4
    assert b.topology_export == ABSENT
    assert b.provenance_sidecar == PRESENT
    assert b.provenance_schema is None   # the canonical shape carries one
    assert b.evidence_source == SOURCE_REDERIVED
    assert b.field_classes == {}
    assert any(CAST_PROVENANCE_SCHEMA in w for w in b.warnings)
    assert any(REDERIVED_WARNING in w for w in b.warnings)
    assert any("no corp-enterprise.topology.json" in w for w in b.warnings)


def test_canonical_sidecar_is_consumed_and_says_so():
    """M4: `evidence_source` is PUBLISHED, so a reader can tell "the
    producer said so" from "axRange re-derived it" without inferring it
    from the presence of a warning -- which is exactly how the distinction
    would go missing the day the sidecar lands."""
    b = load_reference_bundle(MINI_CAST_PROV)
    assert b.provenance_sidecar == PRESENT
    assert b.provenance_schema == CAST_PROVENANCE_SCHEMA
    assert b.evidence_source == SOURCE_SIDECAR
    assert b.field_classes["twin_config.nodes[0].image"] == "compiler"
    assert b.field_classes["twin_config.nodes[1].role"] == "imputed"
    # An override is VISIBLE, never an erasure (`14` §2's house rule).
    assert b.field_classes["twin_config.nodes[2].role"] == "asserted"
    assert b.overridden_fields == ["twin_config.nodes[2].role"]
    # The `population` block is read, so row 1's dropped-host accounting is
    # no longer the only thing standing behind that warning.
    assert b.population["role_less"] == ["address:10.1.0.13"]
    # No re-derivation warning: the classes came from the producer.
    assert not any(REDERIVED_WARNING in w for w in b.warnings)
    d = b.to_dict()
    assert d["evidence_source"] == SOURCE_SIDECAR
    assert d["provenance_fields"] == len(b.field_classes)
    assert d["overridden_field_count"] == 1


def test_a_sidecar_with_no_classes_keeps_the_rederivation_warning(tmp_path):
    """The warning follows the RE-DERIVATION, not the file's absence: a
    sidecar that lands but carries nothing consumable must not make it
    vanish while the classes are still re-derived (M4)."""
    import shutil

    d = tmp_path / "mini-cast"
    shutil.copytree(MINI_CAST, d)
    (d / "mini-cast.provenance.json").write_text(json.dumps({
        "schema": CAST_PROVENANCE_SCHEMA, "pit_id": "pit-x", "fields": {
            "twin_config.nodes[0].image": {"source": "catalog/roles.yaml"},
        },
    }))
    b = load_reference_bundle(d)
    assert b.provenance_sidecar == PRESENT
    assert b.evidence_source == SOURCE_REDERIVED
    assert any(REDERIVED_WARNING in w for w in b.warnings)


# ---------------------------------------------------------------------------
# Fail-loud (LES-110) and id discipline (LES-195/LES-198)
# ---------------------------------------------------------------------------
def test_missing_directory_raises_typed_error():
    with pytest.raises(ReferenceError, match="not found"):
        load_reference_bundle(CAST_BUNDLES / "no-such-bundle")


def test_directory_without_a_twin_config_raises(tmp_path):
    (tmp_path / "empty-bundle").mkdir()
    with pytest.raises(ReferenceError, match="no twin config"):
        load_reference_bundle(tmp_path / "empty-bundle")


def test_content_id_disagreeing_with_directory_name_fails_loud(tmp_path):
    """LES-195: serving one twin's reference under another's id is worse
    than refusing."""
    d = tmp_path / "renamed"
    d.mkdir()
    src = (MINI_CAST / "mini-cast.yaml").read_text()
    (d / "renamed.yaml").write_text(src)
    with pytest.raises(ReferenceError, match="disagrees with"):
        load_reference_bundle(d)


def test_expected_id_mismatch_refuses(tmp_path):
    with pytest.raises(ReferenceError, match="expected"):
        load_reference_bundle(MINI_CAST, expected_id="some-other-twin")


def test_malformed_topology_json_raises_typed_error(tmp_path):
    d = tmp_path / "broken"
    d.mkdir()
    (d / "broken.yaml").write_text(
        (MINI_CAST / "mini-cast.yaml").read_text().replace(
            "id: mini-cast", "id: broken", 1
        )
    )
    (d / "broken.topology.json").write_text("{not json")
    with pytest.raises(ReferenceError, match="topology export"):
        load_reference_bundle(d)


def test_topology_without_a_nodes_list_raises(tmp_path):
    d = tmp_path / "noNodes"
    d.mkdir()
    (d / "noNodes.yaml").write_text(
        (MINI_CAST / "mini-cast.yaml").read_text().replace(
            "id: mini-cast", "id: noNodes", 1
        )
    )
    (d / "noNodes.topology.json").write_text(json.dumps({"segments": [], "edges": []}))
    with pytest.raises(ReferenceError, match="`nodes` must be a list"):
        load_reference_bundle(d)


def test_resolve_bundle_dir_pins_the_catalog():
    """LES-198: an explicit `--reference` wins outright, and the default is
    ONE named root — never a first-hit chain across catalogs."""
    assert resolve_bundle_dir(reference_dir=MINI_CAST) == MINI_CAST
    assert resolve_bundle_dir(
        reference_root=CAST_BUNDLES, bundle_id="mini-cast",
    ) == MINI_CAST
    assert resolve_bundle_dir(
        reference_root=CAST_BUNDLES, bundle_id="does-not-exist",
    ) is None
    assert resolve_bundle_dir(bundle_id=None) is None
