"""Unit tests for `axrange.api.imprints` — the aximprint-integration Track C
fixture reader (`docs/WORK_ORDER_aximprint_console.md`).

Covers: discovery/loading of the checked-in `corp-enterprise` fixture,
summary derivation (structural counts from the real twin_config content +
headline stats from the provenance sidecar), provenance-row parsing, error
handling on a malformed/missing/inconsistent fixture (fail-loud coercion,
2026-08-25 review fixes M1/M2), and — the HARD CONSTRAINT proof — that the
fixture's `twin_config` expands and validates cleanly through the REAL,
unchanged `axrange.twinconfig.expand` + `axrange.validator` pipeline (the
same idiom `tests/test_twinconfig_catalog.py` uses for the shipped
`twin-configs/*` catalog).
"""
from __future__ import annotations

import io
import json
import os
import time
from pathlib import Path
from typing import Optional

import pytest
from ruamel.yaml import YAML

from axrange.api.imprints import (
    ImprintDataError,
    ImprintNotFoundError,
    default_fixtures_dir,
    list_imprints,
    list_imprints_with_skips,
    load_imprint_summary,
    load_provenance,
)

#: Per-segment host counts, derived from the fixture's twin config.
_SAMPLE_SEGMENTS = {"corp-lan": 26, "dmz": 3, "db": 5, "mgmt": 8}

#: The fixture's four provenance records, keyed by twin-config path, each
#: carrying a confidence: the mean is over all four (0.91, 0.88, 0.79, 0.95).
_FIXTURE_MEAN = 0.8825
_FIXTURE_SOURCE = "canonical sidecar (4/4 fields)"


# ---------------------------------------------------------------------------
# The shipped `corp-enterprise` fixture
# ---------------------------------------------------------------------------
def test_default_fixtures_dir_exists_and_has_corp_enterprise():
    root = default_fixtures_dir()
    assert root.is_dir()
    assert (root / "corp-enterprise" / "corp-enterprise.yaml").is_file()
    assert (root / "corp-enterprise" / "corp-enterprise.provenance.json").is_file()


def test_list_imprints_returns_corp_enterprise_with_console_sample_counts():
    entries = list_imprints()
    by_id = {e.id: e for e in entries}
    assert "corp-enterprise" in by_id
    e = by_id["corp-enterprise"]
    assert e.hosts == 42
    assert e.segments == 4
    assert e.mean_confidence == pytest.approx(_FIXTURE_MEAN)
    assert e.confidence_source == _FIXTURE_SOURCE
    assert e.has_provenance is True
    assert e.as_of == ""  # no topology export -> empty, never invented


def test_list_imprints_sorted_by_id():
    ids = [e.id for e in list_imprints()]
    assert ids == sorted(ids)


def test_list_imprints_missing_dir_returns_empty(tmp_path):
    assert list_imprints(tmp_path / "does-not-exist") == []


def test_load_imprint_summary_of_the_packaged_fixture_is_the_cast_summary():
    """The packaged fixture carries a CANONICAL provenance file (the
    normalized shape with its pre-computed `summary` block was retired
    2026-10-29), so its numbers are the ones every cast bundle gets:
    structure from the twin config, confidence meaned over the records that
    carry one, services/personas counted from the bundle (it declares no
    `listen:` and ships no personas/), and nothing for what no artifact
    carries -- no topology export, so `as_of` is empty and `windows` None."""
    s = load_imprint_summary("corp-enterprise")
    assert s.id == "corp-enterprise"
    assert s.provenance == "canonical"
    assert s.as_of == ""
    assert s.store is None and s.days_observed is None and s.windows is None
    assert s.hosts == 42
    assert s.segments == 4
    assert (s.services, s.personas) == (0, 0)
    assert s.mean_confidence == pytest.approx(_FIXTURE_MEAN)
    assert s.confidence_source == _FIXTURE_SOURCE
    assert s.out_of_vocab_pct is None
    assert s.pit_id is None
    assert s.warnings == []


def test_load_imprint_summary_hosts_and_segments_are_derived_not_copied():
    """`hosts`/`segments` come from actually walking the twin_config's real
    nodes/segments (excluding the router) — not a stored sidecar number.

    NOTE (2026-08-25 review S1): this deliberately does NOT assert
    `sum(host_count for topology_segments) == hosts` as a general law — a
    multi-homed non-router host legitimately appears on every segment it
    touches while counting ONCE in the total, so the per-segment sum can
    exceed the total (see
    `test_host_counts_by_segment_membership_can_exceed_total_for_a_multihomed_host`
    below for that case in isolation). `corp-enterprise` merely HAPPENS to
    have no multi-homed hosts, so the two numbers agree here — that's a fact
    about this fixture's content, not a guarantee `_host_counts_by_segment`
    makes."""
    s = load_imprint_summary("corp-enterprise")
    assert len(s.topology_segments) == s.segments
    assert all(seg.host_count >= 0 for seg in s.topology_segments)


def test_load_imprint_summary_topology_segments_match_console_sample():
    s = load_imprint_summary("corp-enterprise")
    by_name = {seg.name: seg for seg in s.topology_segments}
    assert set(by_name) == set(_SAMPLE_SEGMENTS)
    for name, host_count in _SAMPLE_SEGMENTS.items():
        seg = by_name[name]
        assert seg.host_count == host_count
        assert seg.confidence is None  # no shape carries one: never a fabricated 0.0
        assert seg.cidr  # a real CIDR string, not blank


def test_load_imprint_summary_unknown_id_raises_not_found():
    with pytest.raises(ImprintNotFoundError):
        load_imprint_summary("does-not-exist")


def test_load_imprint_summary_rejects_path_traversal():
    with pytest.raises(ImprintNotFoundError):
        load_imprint_summary("../fixtures")


# ---------------------------------------------------------------------------
# Provenance rows
# ---------------------------------------------------------------------------
def test_load_provenance_of_the_packaged_fixture_folds_its_canonical_records():
    doc = load_provenance("corp-enterprise")
    assert doc.provenance == "canonical"
    assert doc.sidecar_schema == "axr-cast-provenance/1"
    assert doc.evidence_source == "sidecar fields"
    assert doc.population["placed_hosts"] == 42
    by_field = {r.field: r for r in doc.rows}
    assert set(by_field) == {
        "twin_config.segments[0].cidr", "twin_config.nodes[6].install[0].role",
        "twin_config.nodes[5].role", "twin_config.nodes[0].nets",
    }
    r = by_field["twin_config.segments[0].cidr"]
    assert r.evidence_class == "observed"
    assert (r.has_confidence, r.confidence) == (True, pytest.approx(0.91))
    assert "Zeek" in r.signal
    assert "observed" in r.evidence  # the record's `note`, the evidence column's analogue
    signals = " ".join(r.signal for r in doc.rows)
    assert "sql01" in signals and "dc01" in signals and "perimeter:wan" in signals
    # The only warning is the fidelity reader's: the fixture ships no export.
    assert len(doc.warnings) == 1 and "no corp-enterprise.topology.json" in doc.warnings[0]


def test_load_provenance_rows_sorted_by_field():
    fields = [r.field for r in load_provenance("corp-enterprise").rows]
    assert fields == sorted(fields)


def test_load_provenance_unknown_id_raises_not_found():
    with pytest.raises(ImprintNotFoundError):
        load_provenance("does-not-exist")


# ---------------------------------------------------------------------------
# Hand-built fixtures (under tmp_path — never mutate the shipped fixture).
# `_write_fixture` builds a minimal, otherwise-valid, INTERNALLY CONSISTENT
# `<dirname>/{dirname}.yaml` + canonical `.provenance.json` pair (dir name ==
# tc.id, satisfying the M2 cross-check) that individual tests then
# deliberately corrupt via `tc_id` to exercise ONE failure path at a time.
# ---------------------------------------------------------------------------
#: One host, one segment, one confidence -- `confidence_source` reads
#: "canonical sidecar (1/1 fields)".
_ONE_FIELD_CANONICAL = {
    "schema": "axr-cast-provenance/1", "pit_id": "pit-1", "code_fingerprint": "f00",
    "fields": {"twin_config.nodes[1].net": {"evidence_class": "observed", "confidence": 0.5}},
}


def _write_fixture(
    root: Path, dirname: str, *,
    tc_id: Optional[str] = None,
    sidecar: Optional[dict] = _ONE_FIELD_CANONICAL,
) -> Path:
    d = root / dirname
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{dirname}.yaml").write_text(
        "twin_config:\n"
        f"  id: {tc_id if tc_id is not None else dirname}\n"
        "  segments:\n"
        "    - { name: lan, cidr: 10.5.0.0/24 }\n"
        "  nodes:\n"
        "    - { name: gw, kind: router, image: debian-12-cloud@v1, requires: [linux],\n"
        "        nets: [{ segment: lan }] }\n"
        "    - name: h1\n"
        "      image: debian-12-cloud@v1\n"
        "      requires: [linux]\n"
        "      net: lan\n"
    )
    if sidecar is not None:
        (d / f"{dirname}.provenance.json").write_text(json.dumps(sidecar))
    return d


def test_load_imprint_summary_without_a_sidecar_summarizes_and_says_so(tmp_path):
    """R7 (GAP-082): a bundle with NO sidecar summarizes -- the listing has
    published it since D3 (a), and a bundle that lists must summarize. What
    it lacks is STATED, never defaulted into a finding: the shape and the
    confidence source both read `absent`, services and personas are counts,
    and the normalized-only vocabulary stat is None. (Superseded: this used
    to raise, then published 0 + a warning.)"""
    d = tmp_path / "bare"
    d.mkdir()
    (d / "bare.yaml").write_text(
        "twin_config:\n  id: bare\n  segments: [{ name: lan, cidr: 10.5.0.0/24 }]\n"
        "  nodes:\n    - { name: h1, image: debian-12-cloud@v1, requires: [linux], net: lan }\n"
    )
    s = load_imprint_summary("bare", fixtures_dir=tmp_path)
    assert s.id == "bare"
    assert s.provenance == "absent"
    assert s.confidence_source == "absent"
    # Services and personas are COUNTS here (no `listen:`, no personas/
    # folder); the vocabulary stat has no source anywhere.
    assert (s.services, s.personas, s.mean_confidence, s.out_of_vocab_pct) == (0, 0, 0.0, None)
    assert not any("NOT a measurement" in w for w in s.warnings), s.warnings
    assert s.hosts == 1 and s.segments == 1
    assert s.as_of == "" and s.windows is None  # no export -> empty, never invented
    assert s.store is None and s.days_observed is None
    assert [seg.confidence for seg in s.topology_segments] == [None]


def test_list_imprints_skips_a_broken_subdirectory_rather_than_raising(tmp_path):
    """A subdirectory whose SIDECAR is broken (present but unparseable) is
    still skipped -- the D3 (a) tolerance below covers an ABSENT sidecar
    only, never a malformed one (`docs/WORK_ORDER_console_canvas.md` §8.1)."""
    _write_fixture(tmp_path, "good")
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "broken.yaml").write_text(
        "twin_config:\n  id: broken\n  segments: [{ name: lan, cidr: 10.5.0.0/24 }]\n"
        "  nodes:\n    - { name: h1, image: debian-12-cloud@v1, requires: [linux], net: lan }\n"
    )
    (broken / "broken.provenance.json").write_text("{not json at all")

    entries = list_imprints(tmp_path)
    assert [e.id for e in entries] == ["good"]


def test_list_imprints_skips_a_subdirectory_with_no_twin_config(tmp_path):
    """Nothing to list without the one required artifact -- the tolerance is
    about the SIDECAR, never about the twin config."""
    _write_fixture(tmp_path, "good")
    (tmp_path / "empty").mkdir()
    assert [e.id for e in list_imprints(tmp_path)] == ["good"]


# ---------------------------------------------------------------------------
# D3 (a) tolerant listing (`docs/WORK_ORDER_console_canvas.md` S1.8) -- a cast
# bundle with NO sidecar, or with a CANONICAL one, still lists; a bundle with
# a broken sidecar does not (above).
# ---------------------------------------------------------------------------
def test_list_imprints_lists_a_sidecarless_bundle_with_has_provenance_false(tmp_path):
    _write_fixture(tmp_path, "good")
    bare = tmp_path / "bare"
    bare.mkdir()
    (bare / "bare.yaml").write_text(
        "twin_config:\n  id: bare\n  segments: [{ name: lan, cidr: 10.5.0.0/24 }]\n"
        "  nodes:\n    - { name: h1, image: debian-12-cloud@v1, requires: [linux], net: lan }\n"
    )

    by_id = {e.id: e for e in list_imprints(tmp_path)}
    assert sorted(by_id) == ["bare", "good"]
    e = by_id["bare"]
    assert e.has_provenance is False
    assert e.confidence_source == "absent"
    # 0.0, NOT None: the shipped plugin calls `.toFixed(2)` on this field.
    assert e.mean_confidence == 0.0
    assert e.hosts == 1 and e.segments == 1
    assert e.as_of == ""  # no topology export -> empty, never invented
    assert by_id["good"].has_provenance is True
    assert by_id["good"].confidence_source == "canonical sidecar (1/1 fields)"


def test_list_imprints_lists_a_canonical_sidecar_bundle_with_has_provenance_true(tmp_path):
    """A CANONICAL provenance file with no confidences to mean still lists,
    with `confidence_source: absent` saying why the 0.0 is not evidence."""
    d = tmp_path / "canon"
    d.mkdir()
    (d / "canon.yaml").write_text(
        "twin_config:\n  id: canon\n  segments: [{ name: lan, cidr: 10.5.0.0/24 }]\n"
        "  nodes:\n    - { name: h1, image: debian-12-cloud@v1, requires: [linux], net: lan }\n"
    )
    (d / "canon.provenance.json").write_text(json.dumps({
        "schema": "axr-cast-provenance/1", "pit_id": "pit-1",
        "code_fingerprint": "abc123", "fields": {},
    }))

    e = {x.id: x for x in list_imprints(tmp_path)}["canon"]
    assert e.has_provenance is True
    assert e.confidence_source == "absent"
    assert e.mean_confidence == 0.0


def test_list_imprints_skips_a_sidecarless_bundle_whose_id_disagrees(tmp_path):
    """The M2 cross-check applies to the tolerant path too -- listing an
    imprint under a name its own content disagrees with is the LES-195
    failure the strict path refuses."""
    d = tmp_path / "dirname-a"
    d.mkdir()
    (d / "dirname-a.yaml").write_text(
        "twin_config:\n  id: content-id-b\n"
        "  segments: [{ name: lan, cidr: 10.5.0.0/24 }]\n"
        "  nodes:\n    - { name: h1, image: debian-12-cloud@v1, requires: [linux], net: lan }\n"
    )
    assert list_imprints(tmp_path) == []


# ---------------------------------------------------------------------------
# M2 (2026-08-25 review), as the GAP-082 review classifies it (S1) --
# directory name (canonical) vs. `twin_config.id` is the ADDRESSING guard and
# refuses. (The normalized shape's own `imprint_id` check retired with the
# shape, 2026-10-29: a canonical provenance file declares no imprint id.)
# ---------------------------------------------------------------------------


def test_load_imprint_summary_tc_id_mismatch_alone_raises(tmp_path):
    _write_fixture(tmp_path, "dirname-a", tc_id="content-id-b")
    with pytest.raises(ImprintDataError, match="twin_config.id"):
        load_imprint_summary("dirname-a", fixtures_dir=tmp_path)


def test_a_self_consistent_canonical_fixture_carries_no_warning(tmp_path):
    _write_fixture(tmp_path, "aligned")
    assert load_imprint_summary("aligned", fixtures_dir=tmp_path).warnings == []


def test_load_imprint_summary_well_formed_fixture_publishes_directory_name(tmp_path):
    """The positive case: when all three ids genuinely agree, the published
    `id` is the directory name (`load_imprint_summary`'s own `imprint_id`
    argument) — the SAME value a caller used to look it up, guaranteeing
    `GET /api/twins/{id}` round-trips."""
    _write_fixture(tmp_path, "aligned")
    s = load_imprint_summary("aligned", fixtures_dir=tmp_path)
    assert s.id == "aligned"


def test_list_imprints_skips_a_subdirectory_with_mismatched_ids(tmp_path):
    _write_fixture(tmp_path, "good")
    _write_fixture(tmp_path, "bad-dir", tc_id="something-else")
    entries = list_imprints(tmp_path)
    assert [e.id for e in entries] == ["good"]


# ---------------------------------------------------------------------------
# R7 (GAP-082) — a CAST bundle summarizes; the addressing guard holds for
# every sidecar shape; a baked export id is published, never refused.
# ---------------------------------------------------------------------------
def _write_cast_bundle(
    root: Path, dirname: str, *,
    tc_id: Optional[str] = None,
    sidecar: Optional[dict] = None,
    export: Optional[dict] = None,
) -> Path:
    """A minimal CAST bundle: a two-segment twin config with a router and a
    counted host group, plus, optionally, a provenance file and a topology
    export."""
    d = root / dirname
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{dirname}.yaml").write_text(
        "twin_config:\n"
        f"  id: {tc_id if tc_id is not None else dirname}\n"
        "  segments:\n"
        "    - { name: lan, cidr: 10.5.0.0/24 }\n"
        "    - { name: dmz, cidr: 10.6.0.0/24 }\n"
        "  conduits:\n"
        "    - { from: lan, to: dmz }\n"
        "  nodes:\n"
        "    - { name: gw, kind: router, image: debian-12-cloud@v1, requires: [linux],\n"
        "        nets: [{ segment: lan }, { segment: dmz }] }\n"
        "    - { name: h, image: debian-12-cloud@v1, requires: [linux], net: lan, count: 3 }\n"
    )
    if sidecar is not None:
        (d / f"{dirname}.provenance.json").write_text(json.dumps(sidecar))
    if export is not None:
        (d / f"{dirname}.topology.json").write_text(json.dumps(export))
    return d


#: A canonical sidecar with confidence on two of its three fields.
_CANONICAL = {
    "schema": "axr-cast-provenance/1", "pit_id": "pit-x-1", "code_fingerprint": "f00",
    "fields": {
        "twin_config.nodes[1].role": {"class": "observed", "confidence": 0.8},
        "twin_config.nodes[1].image": {"class": "compiler"},
        "twin_config.segments[0].cidr": {"class": "observed", "confidence": 0.6},
    },
}

#: A provenance file whose `schema` this reader does not consume.
_UNRECOGNIZED = {"schema": "axr-cast-provenance/2", "fields": {}}

#: What a bundle's provenance file can be, by name, for a parametrized test.
_PROVENANCE = {"canonical": _CANONICAL, "unrecognized": _UNRECOGNIZED, "absent": None}

#: 2026-01-01T00:00:00Z as the export's epoch float.
_NEW_YEAR = 1767225600.0


@pytest.mark.parametrize("shape", sorted(_PROVENANCE))
def test_the_addressing_guard_refuses_for_every_provenance_state(tmp_path, shape):
    """The one id check R7 KEEPS (LES-195): a directory whose twin config
    declares another id is not served, whatever provenance file sits beside
    it -- and the listing skips it with the SAME reason the summary answers
    with."""
    _write_cast_bundle(tmp_path, "dirname-a", tc_id="content-id-b", sidecar=_PROVENANCE[shape])
    with pytest.raises(ImprintDataError, match=r"twin_config\.id='content-id-b'") as exc:
        load_imprint_summary("dirname-a", fixtures_dir=tmp_path)
    entries, skipped = list_imprints_with_skips(tmp_path)
    assert entries == []
    assert skipped == [("dirname-a", str(exc.value))]


def test_a_canonical_sidecar_bundle_summarizes_with_its_own_confidence_mean(tmp_path):
    _write_cast_bundle(tmp_path, "canon", sidecar=_CANONICAL)
    s = load_imprint_summary("canon", fixtures_dir=tmp_path)
    assert s.provenance == "canonical"
    # The mean over the fields that CARRY a confidence, coverage stated (S1).
    assert s.mean_confidence == pytest.approx(0.7)
    assert s.confidence_source == "canonical sidecar (2/3 fields)"
    assert (s.services, s.personas, s.out_of_vocab_pct) == (0, 0, None)
    assert not any("NOT a measurement" in w for w in s.warnings), s.warnings
    # The canonical shape declares no imprint id -- nothing of ITS to warn on.
    assert not any("provenance sidecar declares" in w for w in s.warnings), s.warnings
    # Membership excludes the router: 3 fanned-out hosts on `lan`, none on `dmz`.
    assert s.hosts == 3
    assert {seg.name: seg.host_count for seg in s.topology_segments} == {"lan": 3, "dmz": 0}


def test_an_export_that_bakes_another_imprint_id_is_a_warning_not_a_refusal(tmp_path):
    """GAP-082 in miniature: the export bakes the OBSERVED capture's id --
    live, `corpapcn-disco` inside `pit-395a61f04e-1780405200/`. The bundle
    summarizes under its canonical id, the export's scalars are published,
    and the baked id is NAMED -- never refused, never used as the address."""
    _write_cast_bundle(tmp_path, "pit-x-1", sidecar=_CANONICAL, export={
        "imprint_id": "observed-imprint", "pit_id": "pit-x-1",
        "as_of": _NEW_YEAR, "windows": 9,
        "segments": [], "nodes": [], "edges": [], "census": {},
    })
    s = load_imprint_summary("pit-x-1", fixtures_dir=tmp_path)
    assert s.id == "pit-x-1"
    assert s.windows == 9
    assert s.as_of == "2026-01-01T00:00:00Z"
    baked = [w for w in s.warnings if "'observed-imprint'" in w]
    assert len(baked) == 1, s.warnings
    assert "'pit-x-1'" in baked[0] and "never used as an address" in baked[0]


def test_an_export_whose_baked_id_matches_carries_no_id_warning(tmp_path):
    _write_cast_bundle(tmp_path, "golden-ish", sidecar=_CANONICAL, export={
        "imprint_id": "golden-ish", "pit_id": "pit-g-1", "as_of": _NEW_YEAR,
        "windows": 11, "segments": [], "nodes": [], "edges": [], "census": {},
    })
    s = load_imprint_summary("golden-ish", fixtures_dir=tmp_path)
    assert not any("topology export" in w for w in s.warnings), s.warnings


def test_a_damaged_export_scalar_is_reported_never_a_crash(tmp_path):
    """M1's rule on the new path: nothing read from the export may reach a
    DTO with the wrong type. A non-integer `windows` is published as `None`
    with the reason -- and the bundle still summarizes AND lists."""
    _write_cast_bundle(tmp_path, "bent", export={
        "imprint_id": "bent", "windows": "many", "as_of": _NEW_YEAR,
        "segments": [], "nodes": [], "edges": [], "census": {},
    })
    s = load_imprint_summary("bent", fixtures_dir=tmp_path)
    assert s.windows is None
    assert s.as_of == "2026-01-01T00:00:00Z"
    assert any("windows" in w and "'many'" in w for w in s.warnings), s.warnings
    assert [e.id for e in list_imprints(tmp_path)] == ["bent"]


def test_an_unreadable_export_is_reported_and_the_bundle_still_summarizes(tmp_path):
    d = _write_cast_bundle(tmp_path, "torn")
    (d / "torn.topology.json").write_text("{ truncated")
    s = load_imprint_summary("torn", fixtures_dir=tmp_path)
    assert s.as_of == "" and s.windows is None
    assert any("unreadable" in w for w in s.warnings), s.warnings


# ---------------------------------------------------------------------------
# The GAP-082 review fix round: nothing a bundle carries may 5xx a reader
# (A, I); one precondition and one shape rule on every route (I); finite
# numbers only (I); the narrow addressing check (E); per-router legs (H);
# and the J nits.
# ---------------------------------------------------------------------------
_BAD_UTF8 = b"\xff\xfe{}"  # also how a UTF-16 (BOM) JSON file starts


@pytest.mark.parametrize("shape", sorted(_PROVENANCE))
def test_an_undecodable_export_degrades_to_a_warning_for_every_provenance_state(tmp_path, shape):
    """A (MUST-FIX): a UnicodeDecodeError is a ValueError, not a
    JSONDecodeError, so `except (OSError, json.JSONDecodeError)` let it 500
    the summary, the listing and the plan pre-check."""
    d = _write_cast_bundle(tmp_path, "b", sidecar=_PROVENANCE[shape])
    (d / "b.topology.json").write_bytes(_BAD_UTF8)
    s = load_imprint_summary("b", fixtures_dir=tmp_path)
    assert s.provenance == shape
    assert any("unreadable" in w for w in s.warnings), s.warnings
    assert [e.id for e in list_imprints(tmp_path)] == ["b"]


@pytest.mark.parametrize(("literal", "expected", "problem"), [
    ("9" * 400, None, "is out of range"),    # past 2**53 - 1: refused, never clamped...
    (str(2**53), None, "is out of range"),   # ...from the first integer a browser misreads
    (str(2**53 - 1), 2**53 - 1, None),       # the largest one it reads exactly is published
    ("1e400", None, "must be a whole number"),  # an infinite float is not a whole number
    ("3.0", 3, None),
    ("3.5", None, "must be a whole number"),
], ids=["huge-int", "two-to-the-53", "max-safe", "infinite", "integral-float", "fraction"])
def test_export_windows_never_overflows_the_summary(tmp_path, literal, expected, problem):
    """A (MUST-FIX): `float(<400-digit int>)` raised OverflowError and 500'd
    the summary, the listing and the plan pre-check. Round 2 (SF2): the ONE
    bounded rule (`axrange.jsonnum`) -- a value a browser would read as
    Infinity, or as a neighbouring integer, is published `null` WITH the
    reason, never clamped."""
    d = _write_cast_bundle(tmp_path, "b", sidecar=_CANONICAL)
    (d / "b.topology.json").write_text(
        '{"imprint_id": "b", "as_of": %r, "windows": %s, "segments": [], '
        '"nodes": [], "edges": [], "census": {}}' % (_NEW_YEAR, literal)
    )
    s = load_imprint_summary("b", fixtures_dir=tmp_path)
    assert s.windows == expected
    reasons = [w for w in s.warnings if "`windows`" in w]
    if problem is None:
        assert reasons == [], s.warnings
    else:
        assert len(reasons) == 1 and problem in reasons[0], s.warnings
    assert [e.id for e in list_imprints(tmp_path)] == ["b"]


def test_an_undecodable_sidecar_is_a_data_error_and_the_listing_skips_it(tmp_path):
    """I: the same class of 500 on the SIDECAR -- the summary, the plan
    pre-check and the WHOLE listing. Now one refusal, and the healthy
    neighbour stays listed."""
    d = _write_cast_bundle(tmp_path, "b")
    (d / "b.provenance.json").write_bytes(_BAD_UTF8)
    _write_cast_bundle(tmp_path, "healthy", sidecar=_CANONICAL)
    with pytest.raises(ImprintDataError, match="unreadable") as exc:
        load_imprint_summary("b", fixtures_dir=tmp_path)
    entries, skipped = list_imprints_with_skips(tmp_path)
    assert [e.id for e in entries] == ["healthy"]
    assert skipped == [("b", str(exc.value))]


def test_an_unrecognized_sidecar_schema_is_present_but_never_consumed(tmp_path):
    """I: a `schema` this reader does not consume is PRESENT and NOT consumed
    -- on the summary, the listing, the provenance route and the cast alike,
    exactly as the fidelity reader treats it."""
    from axrange.api.imprints import load_cast_summary

    _write_cast_bundle(tmp_path, "b", sidecar={
        "schema": "axr-cast-provenance/2",
        "fields": {"twin_config.nodes[1].role": {"class": "observed", "confidence": 0.9}},
    })
    s = load_imprint_summary("b", fixtures_dir=tmp_path)
    assert s.provenance == "unrecognized"
    assert (s.mean_confidence, s.confidence_source) == (0.0, "unrecognized sidecar (not consumed)")
    assert any(
        "'axr-cast-provenance/2'" in w and "not one this reader consumes" in w for w in s.warnings
    ), s.warnings
    [entry] = list_imprints(tmp_path)
    assert entry.has_provenance is True
    assert entry.confidence_source == s.confidence_source
    doc = load_provenance("b", fixtures_dir=tmp_path)
    assert (doc.provenance, doc.rows) == ("unrecognized", [])
    assert any("NOT consumed" in w for w in doc.warnings), doc.warnings
    assert load_cast_summary("b", fixtures_dir=tmp_path).provenance == "unrecognized"


def test_the_retired_normalized_shape_is_unrecognized_and_its_summary_block_is_never_read(tmp_path):
    """The Track-C normalized shape -- a schema-less document carrying
    pre-computed headline stats -- was retired 2026-10-29. One that still
    turns up is PRESENT and NOT consumed like any other unknown shape: its
    `summary` numbers never reach the summary (services/personas stay the
    bundle's own counts, the mean stays 0.0 with its source stated), its
    `imprint_id` is never read as an address, and every route says so in
    the same words."""
    from axrange.api.imprints import load_cast_summary

    _write_cast_bundle(tmp_path, "b", sidecar={
        "imprint_id": "some-other-imprint", "as_of": "2026-01-01T00:00:00Z",
        "summary": {"services": 99, "personas": 9, "mean_confidence": 0.9, "out_of_vocab_pct": 3.0},
        "segment_confidence": {"lan": 0.9},
        "fields": {"segment lan": {"evidence_class": "observed", "confidence": 0.9,
                                   "signal": "s", "evidence": "e"}},
    })
    s = load_imprint_summary("b", fixtures_dir=tmp_path)
    assert (s.provenance, s.confidence_source) == ("unrecognized", "unrecognized sidecar (not consumed)")
    assert (s.services, s.personas, s.mean_confidence, s.out_of_vocab_pct) == (0, 0, 0.0, None)
    assert [seg.confidence for seg in s.topology_segments] == [None, None]
    assert any("no `schema` key" in w and "NOT consumed" in w for w in s.warnings), s.warnings
    [entry] = list_imprints(tmp_path)
    assert (entry.id, entry.has_provenance, entry.confidence_source) == ("b", True, s.confidence_source)
    doc = load_provenance("b", fixtures_dir=tmp_path)
    assert (doc.provenance, doc.sidecar_schema, doc.rows) == ("unrecognized", None, [])
    assert any("declares no `schema`" in w for w in doc.warnings), doc.warnings
    assert load_cast_summary("b", fixtures_dir=tmp_path).provenance == "unrecognized"


@pytest.mark.parametrize("sidecar", [
    {"schema": "axr-cast-provenance/1"},
    {"schema": "axr-cast-provenance/1", "fields": []},
], ids=["canonical-without-fields", "canonical-fields-not-an-object"])
def test_a_sidecar_without_a_fields_object_is_refused_in_the_cast_routes_own_words(tmp_path, sidecar):
    """I: ONE precondition (`fidelity.reference.sidecar_fields_or_raise`), so
    the listing, the summary and `GET .../cast` agree on which bundles open --
    a canonical provenance file with no `fields` listed and summarized 200
    while the cast refused it."""
    from axrange.api.imprints import load_cast_summary

    _write_cast_bundle(tmp_path, "b", sidecar=sidecar)
    with pytest.raises(ImprintDataError, match="`fields` object") as summary_refusal:
        load_imprint_summary("b", fixtures_dir=tmp_path)
    with pytest.raises(ImprintDataError) as cast_refusal:
        load_cast_summary("b", fixtures_dir=tmp_path)
    assert str(summary_refusal.value) == str(cast_refusal.value)
    assert list_imprints_with_skips(tmp_path) == ([], [("b", str(summary_refusal.value))])


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity", "1e400"])
def test_a_non_finite_canonical_confidence_is_treated_as_absent_and_said_so(tmp_path, literal):
    """I: averaged in, one NaN made the mean non-finite -- which serializes as
    `null` and breaks the non-null `mean_confidence` the shipped plugin calls
    `.toFixed(2)` on (Python's own `json.dump` writes `NaN`). It is ABSENT
    now: counted in `confidence_source`, named in `warnings`, and the
    provenance row says `has_confidence: false`."""
    d = _write_cast_bundle(tmp_path, "b")
    (d / "b.provenance.json").write_text(
        '{"schema": "axr-cast-provenance/1", "fields": {'
        '"a": {"class": "observed", "confidence": %s}, '
        '"b": {"class": "observed", "confidence": 0.6}}}' % literal
    )
    s = load_imprint_summary("b", fixtures_dir=tmp_path)
    assert s.mean_confidence == pytest.approx(0.6)
    assert s.confidence_source == "canonical sidecar (1/2 fields; 1 ignored, not a finite number)"
    assert any("not finite numbers" in w for w in s.warnings), s.warnings
    [entry] = list_imprints(tmp_path)
    assert (entry.mean_confidence, entry.confidence_source) == (s.mean_confidence, s.confidence_source)
    doc = load_provenance("b", fixtures_dir=tmp_path)
    rows = {r.field: r for r in doc.rows}
    assert (rows["a"].has_confidence, rows["a"].confidence) == (False, 0.0)
    assert (rows["b"].has_confidence, rows["b"].confidence) == (True, 0.6)
    assert any("not a finite number" in w for w in doc.warnings), doc.warnings




def test_a_readable_export_with_one_bad_scalar_still_has_its_baked_id_compared(tmp_path):
    """J: a bad `windows` is no reason to stop reading the export's id --
    only an export that could not be parsed at all has nothing to say."""
    _write_cast_bundle(tmp_path, "b", sidecar=_CANONICAL, export={
        "imprint_id": "observed-imprint", "windows": "many", "as_of": _NEW_YEAR,
        "segments": [], "nodes": [], "edges": [], "census": {},
    })
    s = load_imprint_summary("b", fixtures_dir=tmp_path)
    assert any("'many'" in w for w in s.warnings), s.warnings
    assert any("'observed-imprint'" in w for w in s.warnings), s.warnings


@pytest.mark.parametrize("baked", [42, {"a": 1}, ["a"], True], ids=["int", "object", "list", "bool"])
def test_a_non_string_baked_export_id_is_named_for_what_it_is(tmp_path, baked):
    """J: the export declares an id, of the wrong type -- never "declares no
    imprint_id", and never coerced into an address."""
    _write_cast_bundle(tmp_path, "b", sidecar=_CANONICAL, export={
        "imprint_id": baked, "as_of": _NEW_YEAR, "windows": 3,
        "segments": [], "nodes": [], "edges": [], "census": {},
    })
    s = load_imprint_summary("b", fixtures_dir=tmp_path)
    assert s.id == "b"
    assert any("NON-STRING imprint_id" in w for w in s.warnings), s.warnings
    assert not any("declares no imprint_id" in w for w in s.warnings), s.warnings


def test_check_imprint_addressable_reads_the_twin_config_and_nothing_else(tmp_path):
    """E (S2): the deploy pre-check and every Console route's guard -- the
    404/400 split and the `twin_config.id` comparison, and NOTHING a deploy
    does not read (here the sidecar and the export are both undecodable)."""
    from axrange.api.imprints import ImprintNotFoundError, check_imprint_addressable

    d = _write_cast_bundle(tmp_path, "b")
    (d / "b.provenance.json").write_bytes(_BAD_UTF8)
    (d / "b.topology.json").write_bytes(_BAD_UTF8)
    assert check_imprint_addressable("b", fixtures_dir=tmp_path) == d
    _write_cast_bundle(tmp_path, "renamed", tc_id="another-twin")
    with pytest.raises(ImprintDataError, match="twin_config.id='another-twin'"):
        check_imprint_addressable("renamed", fixtures_dir=tmp_path)
    for missing in ("nope", "../etc"):
        with pytest.raises(ImprintNotFoundError):
            check_imprint_addressable(missing, fixtures_dir=tmp_path)


def test_the_cast_publishes_every_router_with_its_own_legs(tmp_path):
    """H (S5): per ROUTER, each leg once in declaration order -- never one
    flattened union, which cannot say whether there is one gateway or three."""
    from axrange.api.imprints import CastRouter, load_cast_summary

    d = tmp_path / "b"
    d.mkdir()
    (d / "b.yaml").write_text(
        "twin_config:\n  id: b\n  segments:\n"
        "    - { name: lan, cidr: 10.5.0.0/24 }\n"
        "    - { name: dmz, cidr: 10.6.0.0/24 }\n"
        "    - { name: core, cidr: 10.7.0.0/24 }\n"
        "  nodes:\n"
        "    - { name: gw1, kind: router, image: debian-12-cloud@v1, requires: [linux],\n"
        "        nets: [{ segment: lan }, { segment: dmz }, { segment: lan }] }\n"
        "    - { name: gw2, kind: router, image: debian-12-cloud@v1, requires: [linux], net: core }\n"
        "    - { name: h, image: debian-12-cloud@v1, requires: [linux], net: lan }\n"
    )
    assert load_cast_summary("b", fixtures_dir=tmp_path).routers == [
        CastRouter(name="gw1", segments=("lan", "dmz")),
        CastRouter(name="gw2", segments=("core",)),
    ]


def test_every_listed_entry_is_a_projection_of_its_summary(tmp_path):
    """"Lists" implies "summarizes", with the same numbers (R7): one builder
    serves both. Over all three provenance states, iterating the LISTING's
    own output (LES-223) rather than a hardcoded id list."""
    _write_cast_bundle(tmp_path, "odd", sidecar=_UNRECOGNIZED)
    _write_cast_bundle(tmp_path, "canon", sidecar=_CANONICAL)
    _write_cast_bundle(tmp_path, "bare")
    entries = list_imprints(tmp_path)
    assert {e.id for e in entries} == {"odd", "canon", "bare"}
    for e in entries:
        s = load_imprint_summary(e.id, fixtures_dir=tmp_path)
        assert (s.id, s.as_of, s.hosts, s.segments, s.mean_confidence, s.confidence_source) == (
            e.id, e.as_of, e.hosts, e.segments, e.mean_confidence, e.confidence_source,
        )
        assert e.has_provenance is (s.provenance != "absent")


# ---------------------------------------------------------------------------
# S1 (2026-08-25 review) — `_host_counts_by_segment` counts per-segment
# MEMBERSHIP, not a partition: a multi-homed non-router host counts once in
# the total but on EVERY segment it touches, so the per-segment sum can
# legitimately exceed the total.
# ---------------------------------------------------------------------------
def test_host_counts_by_segment_membership_can_exceed_total_for_a_multihomed_host(tmp_path):
    d = tmp_path / "multihomed"
    d.mkdir()
    (d / "multihomed.yaml").write_text(
        "twin_config:\n"
        "  id: multihomed\n"
        "  segments:\n"
        "    - { name: a, cidr: 10.6.0.0/24 }\n"
        "    - { name: b, cidr: 10.6.1.0/24 }\n"
        "  nodes:\n"
        "    - { name: gw, kind: router, image: debian-12-cloud@v1, requires: [linux],\n"
        "        nets: [{ segment: a }, { segment: b }] }\n"
        "    - { name: dual, image: debian-12-cloud@v1, requires: [linux],\n"
        "        nets: [{ segment: a }, { segment: b }] }\n"
    )
    s = load_imprint_summary("multihomed", fixtures_dir=tmp_path)
    assert s.hosts == 1  # the dual-homed host counts once in the total
    by_name = {seg.name: seg for seg in s.topology_segments}
    assert by_name["a"].host_count == 1
    assert by_name["b"].host_count == 1
    # per-segment MEMBERSHIP sums to more than the total host count
    assert sum(seg.host_count for seg in s.topology_segments) == 2 > s.hosts


# ---------------------------------------------------------------------------
# S3 (2026-08-25 review) — a non-numeric `node.count` must raise
# `ImprintDataError`, not silently fall back to `count=1` (symmetry with M1:
# a bad author-supplied value is surfaced, never quietly absorbed).
# ---------------------------------------------------------------------------
def test_load_imprint_summary_non_numeric_node_count_raises_data_error(tmp_path):
    d = tmp_path / "badcount"
    d.mkdir()
    (d / "badcount.yaml").write_text(
        "twin_config:\n"
        "  id: badcount\n"
        "  segments: [{ name: lan, cidr: 10.5.0.0/24 }]\n"
        "  nodes:\n"
        "    - { name: h1, image: debian-12-cloud@v1, requires: [linux], net: lan,\n"
        "        count: not-a-number }\n"
    )
    with pytest.raises(ImprintDataError, match="h1"):
        load_imprint_summary("badcount", fixtures_dir=tmp_path)


# ---------------------------------------------------------------------------
# HARD CONSTRAINT proof: the fixture's twin_config validates against the
# real, unchanged validator (same idiom as
# `tests/test_twinconfig_catalog.py::test_validates_at_default_params`).
# ---------------------------------------------------------------------------
def test_fixture_twin_config_expands_and_validates_cleanly(manifests):
    from axrange.twinconfig.expand import expand_twin_config
    from axrange.validator.engine import validate_full

    ir_dict, _params = expand_twin_config(
        "corp-enterprise", catalog_dir=default_fixtures_dir(),
    )
    assert ir_dict["scenario_schema_version"] in (1, 2, 3)  # STR-020's recognized set

    y = YAML(typ="safe")
    buf = io.StringIO()
    y.dump(ir_dict, buf)
    result = validate_full(text=buf.getvalue(), manifests=manifests)
    assert not result.errors, [(f.rule_id, f.message) for f in result.errors]
    assert not result.warnings, [(f.rule_id, f.message) for f in result.warnings]
    assert result.passes_run == [1, 2, 3]


def test_fixture_twin_config_host_count_matches_expanded_ir_node_count(manifests):
    """Cross-check the reader's own host count against the SAME structural
    fact the real expander produces (LES-025-style "don't trust two
    independent counts to agree" — count the expanded IR's non-router nodes
    directly, rather than re-trusting `load_imprint_summary`'s own math)."""
    from axrange.twinconfig.expand import expand_twin_config

    ir_dict, _params = expand_twin_config(
        "corp-enterprise", catalog_dir=default_fixtures_dir(),
    )
    non_router_nodes = [
        n for n in ir_dict["layers"]["infra"]["nodes"] if n.get("kind") != "router"
    ]
    s = load_imprint_summary("corp-enterprise")
    assert len(non_router_nodes) == s.hosts == 42


# ---------------------------------------------------------------------------
# S1.4's canonical-sidecar adapter (`docs/WORK_ORDER_console_canvas.md`) --
# the fail-loud paths, at the reader rather than over HTTP.
# ---------------------------------------------------------------------------
def _canonical_bundle(root: Path, name: str, fields: dict, **sidecar) -> Path:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.yaml").write_text(
        f"twin_config:\n  id: {name}\n"
        "  segments: [{ name: lan, cidr: 10.5.0.0/24 }]\n"
        "  nodes:\n    - { name: h1, image: debian-12-cloud@v1, requires: [linux], net: lan }\n"
    )
    body = {"schema": "axr-cast-provenance/1", "pit_id": "pit-1", "fields": fields}
    body.update(sidecar)
    (d / f"{name}.provenance.json").write_text(json.dumps(body))
    return d


def test_load_provenance_canonical_reports_an_unknown_class_without_rewriting_it(tmp_path):
    from axrange.api.imprints import load_provenance

    _canonical_bundle(tmp_path, "canon", {
        "twin_config.nodes[0].role": {"evidence_class": "guessed", "confidence": 0.5},
    })
    doc = load_provenance("canon", fixtures_dir=tmp_path)
    assert doc.provenance == "canonical"
    # Published as written -- a reader that normalized this would hide exactly
    # the producer/consumer drift the warning exists to surface.
    assert doc.rows[0].evidence_class == "guessed"
    assert any("guessed" in w for w in doc.warnings)


def test_load_provenance_canonical_non_object_overrides_is_a_typed_data_error(tmp_path):
    from axrange.api.imprints import load_provenance

    _canonical_bundle(tmp_path, "canon", {
        "twin_config.nodes[0].role": {
            "evidence_class": "asserted", "confidence": 1.0, "overrides": "imputed",
        },
    })
    with pytest.raises(ImprintDataError, match="overrides"):
        load_provenance("canon", fixtures_dir=tmp_path)


@pytest.mark.parametrize("value", ["high", "0.5", True, [0.5], {"value": 0.5}],
                         ids=["word", "numeric-string", "bool", "list", "object"])
def test_load_provenance_canonical_non_numeric_confidence_is_absent_on_the_row_and_in_the_mean(
    tmp_path, value,
):
    """LES-110 on the canonical branch: a bad author-supplied confidence is
    never a bare ValueError (500). GAP-082 review round 2 (9a): nor is it a
    refusal of the whole route -- the rows and the summary's mean apply ONE
    rule (`imprints._canonical_confidence`), so a value is on a row exactly
    when it is in the mean, and every ignored one is counted. Before, `"0.5"`
    was shown on its row and left out of the mean, and `"high"` 400'd the
    provenance route while the summary silently skipped it."""
    _canonical_bundle(tmp_path, "canon", {
        "twin_config.nodes[0].role": {"evidence_class": "observed", "confidence": value},
        "twin_config.nodes[0].image": {"evidence_class": "observed", "confidence": 0.4},
    })
    doc = load_provenance("canon", fixtures_dir=tmp_path)
    rows = {r.field: r for r in doc.rows}
    role, image = rows["twin_config.nodes[0].role"], rows["twin_config.nodes[0].image"]
    assert (role.has_confidence, role.confidence) == (False, 0.0)
    assert (image.has_confidence, image.confidence) == (True, 0.4)
    assert any("1 provenance row(s)" in w and "not a finite number" in w for w in doc.warnings), doc.warnings
    s = load_imprint_summary("canon", fixtures_dir=tmp_path)
    assert s.mean_confidence == pytest.approx(0.4)
    assert s.confidence_source == "canonical sidecar (1/2 fields; 1 ignored, not a finite number)"
    assert any("1 canonical confidence value(s)" in w for w in s.warnings), s.warnings


def test_load_cast_summary_non_numeric_deploy_min_occupancy_is_a_typed_data_error(tmp_path):
    from axrange.api.imprints import load_cast_summary

    d = tmp_path / "canon"
    d.mkdir()
    (d / "canon.yaml").write_text(
        "twin_config:\n  id: canon\n"
        "  params: { DEPLOY_MIN_OCCUPANCY: most-of-them }\n"
        "  segments: [{ name: lan, cidr: 10.5.0.0/24 }]\n"
        "  nodes:\n    - { name: h1, image: debian-12-cloud@v1, requires: [linux], net: lan }\n"
    )
    with pytest.raises(ImprintDataError, match="DEPLOY_MIN_OCCUPANCY"):
        load_cast_summary("canon", fixtures_dir=tmp_path)


# ---------------------------------------------------------------------------
# GAP-082 review, round 2 -- one addressing guard on every reader, shape
# before preconditions, a counted router spec, and a cached twin-config parse.
# ---------------------------------------------------------------------------
_ONE_HOST_CONFIG = (
    "twin_config:\n  id: {id}\n"
    "  segments: [{{ name: lan, cidr: 10.5.0.0/24 }}]\n"
    "  nodes:\n    - {{ name: h1, image: debian-12-cloud@v1, requires: [linux], net: lan }}\n"
)


@pytest.mark.parametrize(("case", "reason"), [
    ("no-id-yaml-beside-a-compiler-named-one", "missing b.yaml"),
    ("id-yaml-names-another-twin", "twin_config.id='another-twin'"),
])
def test_cast_and_provenance_run_the_same_addressing_guard_as_the_summary(tmp_path, case, reason):
    """SF4: the fidelity reader accepts ANY `*.yaml` in the directory that
    declares the directory's id. So a bundle with no `<id>.yaml` beside a
    compiler-named `twin_config.yaml`, and one whose `<id>.yaml` names
    ANOTHER twin beside a stray file naming this one, opened on `/cast` and
    `/provenance` while every other route refused them -- the second
    serving another twin's config under this id (LES-195). One guard now,
    so one refusal, word for word."""
    from axrange.api.imprints import check_imprint_addressable, load_cast_summary

    d = tmp_path / "b"
    d.mkdir()
    (d / "twin_config.yaml").write_text(_ONE_HOST_CONFIG.format(id="b"))
    if case == "id-yaml-names-another-twin":
        (d / "b.yaml").write_text(_ONE_HOST_CONFIG.format(id="another-twin"))
    refusals = set()
    for reader in (load_imprint_summary, load_cast_summary, load_provenance,
                   check_imprint_addressable):
        with pytest.raises(ImprintDataError) as e:
            reader("b", fixtures_dir=tmp_path)
        assert reason in str(e.value), (reader.__name__, str(e.value))
        refusals.add(str(e.value))
    assert len(refusals) == 1, refusals


@pytest.mark.parametrize("sidecar", [
    {"schema": "axr-cast-provenance/2"},
    {"schema": "axr-cast-provenance/2", "records": {"twin_config.nodes[1].role": {"class": "observed"}}},
    {"schema": "axr-cast-provenance/2", "fields": ["renamed"]},
    {"imprint_id": "b", "as_of": "2026-01-01T00:00:00Z", "summary": {
        "services": 1, "personas": 1, "mean_confidence": 0.5, "out_of_vocab_pct": 0.0}},
], ids=["no-fields", "fields-renamed", "fields-not-an-object", "schema-less-without-fields"])
def test_an_unrecognized_sidecar_is_held_to_no_precondition(tmp_path, sidecar):
    """SF3: shape FIRST, then preconditions. Only the one shape that is READ
    -- canonical -- owes a `fields` object; an unrecognized schema, or none
    at all, is present and NOT consumed with or without one, so a future
    schema that renames the key does not make its bundle vanish."""
    from axrange.api.imprints import load_cast_summary

    _write_cast_bundle(tmp_path, "b", sidecar=sidecar)
    s = load_imprint_summary("b", fixtures_dir=tmp_path)
    assert (s.provenance, s.confidence_source) == (
        "unrecognized", "unrecognized sidecar (not consumed)",
    )
    [entry] = list_imprints(tmp_path)
    assert (entry.id, entry.has_provenance) == ("b", True)
    cast = load_cast_summary("b", fixtures_dir=tmp_path)
    assert (cast.provenance, cast.has_provenance) == ("unrecognized", True)
    doc = load_provenance("b", fixtures_dir=tmp_path)
    assert (doc.provenance, doc.rows) == ("unrecognized", [])


@pytest.mark.parametrize("schema", [5, True, ["axr-cast-provenance/1"], {"version": 1}],
                         ids=["number", "bool", "list", "object"])
def test_a_non_string_schema_is_unrecognized_and_never_published_as_a_schema_name(tmp_path, schema):
    """A `schema` of the wrong type is an unrecognized sidecar -- the summary,
    `/cast` and `/provenance` agree -- published as NO schema name rather
    than `str()`-ed into one the producer never wrote (`"5"`,
    `"['axr-cast-provenance/1']"`)."""
    from axrange.api.imprints import load_cast_summary

    _write_cast_bundle(tmp_path, "b", sidecar={"schema": schema, "fields": {}})
    assert load_imprint_summary("b", fixtures_dir=tmp_path).provenance == "unrecognized"
    cast = load_cast_summary("b", fixtures_dir=tmp_path)
    assert (cast.provenance, cast.provenance_schema) == ("unrecognized", None)
    assert any("`schema` is not a string" in w for w in cast.warnings), cast.warnings
    doc = load_provenance("b", fixtures_dir=tmp_path)
    assert (doc.provenance, doc.sidecar_schema, doc.rows) == ("unrecognized", None, [])


def test_a_counted_router_spec_publishes_one_router_per_instance_named_as_deployed(tmp_path):
    """A `count: 2` router spec builds TWO routers. The cast publishes one
    entry per instance, under the ids a deploy gives them (the fidelity
    reader's `TwinGroup.node_ids`, which are the expander's own), each with
    the spec's legs -- so the Console never names "the gateway" of a
    two-router twin."""
    from axrange.api.imprints import load_cast_summary
    from axrange.twinconfig.expand import expand_twin_config

    d = tmp_path / "b"
    d.mkdir()
    (d / "b.yaml").write_text(
        "twin_config:\n  id: b\n"
        "  segments:\n    - { name: lan, cidr: 10.5.0.0/24 }\n    - { name: dmz, cidr: 10.6.0.0/24 }\n"
        "  nodes:\n"
        "    - { name: gw, kind: router, count: 2, image: debian-12-cloud@v1, requires: [linux],\n"
        "        nets: [{ segment: lan }, { segment: dmz }] }\n"
        "    - { name: h, image: debian-12-cloud@v1, requires: [linux], net: lan }\n"
    )
    cast = load_cast_summary("b", fixtures_dir=tmp_path)
    assert [(r.name, r.segments) for r in cast.routers] == [
        ("gw01", ("lan", "dmz")), ("gw02", ("lan", "dmz")),
    ]
    ir, _params = expand_twin_config("b", catalog_dir=tmp_path)
    built = [n["id"] for n in ir["layers"]["infra"]["nodes"] if n.get("kind") == "router"]
    assert built == [r.name for r in cast.routers]


def _count_parses(monkeypatch) -> list[Path]:
    """A COLD twin-config cache, and every uncached parse behind it."""
    from collections import OrderedDict

    from axrange.api import imprints

    monkeypatch.setattr(imprints, "_TWIN_CONFIG_CACHE", OrderedDict())
    parsed: list[Path] = []
    real = imprints._parse_twin_config

    def _spy(path: Path, imprint_id: str):
        parsed.append(path)
        return real(path, imprint_id)

    monkeypatch.setattr(imprints, "_parse_twin_config", _spy)
    return parsed


def test_the_twin_config_parse_is_cached_per_file(tmp_path, monkeypatch):
    """SF1: every route runs the addressing guard, and one parse of the real
    pit bundle's twin config costs ~210 ms -- which a pin drop paid on every
    layout write. Each file is parsed ONCE however many readers ask."""
    from axrange.api.imprints import check_imprint_addressable, load_cast_summary

    parsed = _count_parses(monkeypatch)
    _write_cast_bundle(tmp_path, "b", sidecar=_CANONICAL)
    _write_cast_bundle(tmp_path, "c", sidecar=_CANONICAL)
    for _ in range(3):
        check_imprint_addressable("b", fixtures_dir=tmp_path)
        load_imprint_summary("b", fixtures_dir=tmp_path)
        load_cast_summary("b", fixtures_dir=tmp_path)
        load_provenance("b", fixtures_dir=tmp_path)
        list_imprints(tmp_path)
    assert sorted(p.parent.name for p in parsed) == ["b", "c"]


def test_a_cached_twin_config_is_never_shared_between_callers(tmp_path, monkeypatch):
    """The cache hands out DEEP COPIES. The twin-config models are mutable and
    one is mutated in place today (`expand.py`'s IP allocation assigns
    `leg.ip`), so `frozen=True` is not available -- and a shared instance
    would let a single in-place edit anywhere corrupt every later request for
    that file, process-wide and silently, until its mtime changed. Both kinds
    of mutation are exercised: an attribute (which `frozen` would have caught)
    and a container (which it would not)."""
    from axrange.api.imprints import _load_twin_config, resolve_imprint_dir

    parsed = _count_parses(monkeypatch)
    _write_cast_bundle(tmp_path, "b", sidecar=_CANONICAL)
    d = resolve_imprint_dir(tmp_path, "b")
    # BOTH paths hand out a copy, and both are mutated here: the cache MISS
    # (which copies what it just parsed) and the cache HIT (which copies what
    # it stored). Mutating only the miss's object cannot catch a hit that
    # returns the stored instance -- the revert proof for this fix found
    # exactly that hole in the first cut of this test.
    first = _load_twin_config(d, "b")           # a cache MISS
    second = _load_twin_config(d, "b")          # a cache HIT
    before = (first.id, [n.name for n in first.nodes], [s.cidr for s in first.segments])
    assert second is not first

    for caller in (first, second):
        caller.id = "hijacked"                  # an attribute...
        caller.nodes.pop()                      # ...a list...
        caller.segments[0].cidr = "10.9.9.0/24"  # ...and a nested model's field

    third = _load_twin_config(d, "b")
    assert third is not first and third is not second
    assert (third.id, [n.name for n in third.nodes],
            [s.cidr for s in third.segments]) == before
    # One parse still covers all three reads -- copies, never a re-read.
    assert len(parsed) == 1, parsed
    # ...and the routes that read the same file are untouched by the mutation.
    summary = load_imprint_summary("b", fixtures_dir=tmp_path)
    assert (summary.id, [s.cidr for s in summary.topology_segments]) == ("b", before[2])


def _rewrite_in_place(path: Path, old: str, new: str, *, restore_mtime: bool) -> None:
    """`old` -> `new` in `path`, IN PLACE (same inode), optionally putting
    the mtime back as a restore would. A coarse-clock filesystem cannot tell
    two writes within one tick apart -- then no `stat` could, the cache
    included -- so retry briefly, and skip rather than pass vacuously."""
    before = path.stat()
    text = path.read_text()
    assert old in text
    for _ in range(50):
        path.write_text(text.replace(old, new))
        if restore_mtime:
            os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        after = path.stat()
        assert after.st_ino == before.st_ino
        if (after.st_mtime_ns, after.st_ctime_ns) != (before.st_mtime_ns, before.st_ctime_ns):
            return
        time.sleep(0.01)
    pytest.skip("this filesystem's timestamps did not advance -- no stat can see the rewrite")


@pytest.mark.parametrize("edit", ["longer", "same-size", "same-size-mtime-restored"])
def test_any_rewrite_of_the_twin_config_invalidates_its_cached_parse(tmp_path, monkeypatch, edit):
    """An apply's install stage rewrites `<id>.yaml`: the next read must see
    the new content -- including a rewrite of the same size, and one whose
    mtime a restore put back (ctime cannot be set, so it is in the key)."""
    parsed = _count_parses(monkeypatch)
    d = _write_cast_bundle(tmp_path, "b", sidecar=_CANONICAL)
    path = d / "b.yaml"

    def cidrs() -> list[str]:
        return [seg.cidr for seg in load_imprint_summary("b", fixtures_dir=tmp_path).topology_segments]

    assert cidrs() == ["10.5.0.0/24", "10.6.0.0/24"]
    new = "10.66.0.0/24" if edit == "longer" else "10.7.0.0/24"
    size = path.stat().st_size
    _rewrite_in_place(path, "10.6.0.0/24", new, restore_mtime=edit == "same-size-mtime-restored")
    assert (path.stat().st_size == size) is (edit != "longer")
    assert cidrs() == ["10.5.0.0/24", new]
    assert cidrs() == ["10.5.0.0/24", new]
    assert len(parsed) == 2, parsed


def test_a_cast_bundle_counts_its_services_and_personas_from_what_it_ships(tmp_path):
    """CorpAPCN `test2` (2026-10-28, two hours): 262 hosts, 103 declared listeners on 35
    hosts, 17 persona documents -- and the summary read services 0, personas 0, because
    those two were taken only from a normalized sidecar's `summary` block. A cast bundle
    carries both: `listen:` on its nodes, persona files in `personas/`. Counted per host
    INSTANCE for services (the notion `hosts` uses), per document for personas; only
    `out_of_vocab_pct` has no source, and reads None rather than a warning."""
    d = _write_cast_bundle(tmp_path, "served", sidecar=_CANONICAL)
    (d / "served.yaml").write_text(
        "twin_config:\n"
        "  id: served\n"
        "  segments:\n"
        "    - { name: lan, cidr: 10.5.0.0/24 }\n"
        "  nodes:\n"
        "    - { name: gw, kind: router, image: debian-12-cloud@v1, requires: [linux],\n"
        "        nets: [{ segment: lan }], listen: [{ port: 22, proto: tcp }] }\n"
        "    - { name: web, image: debian-12-cloud@v1, requires: [linux], net: lan, count: 3,\n"
        "        listen: [{ port: 80, proto: tcp }, { port: 443, proto: tcp }] }\n"
        "    - { name: dns, image: debian-12-cloud@v1, requires: [linux], net: lan,\n"
        "        listen: [{ port: 53, proto: udp }] }\n"
        "    - { name: quiet, image: debian-12-cloud@v1, requires: [linux], net: lan }\n"
    )
    (d / "personas").mkdir()
    for pid in ("cast-web-tier", "cast-web-tier-local", "cast-dns-resolver"):
        (d / "personas" / f"{pid}.yaml").write_text(f"persona:\n  id: {pid}\n")
    (d / "personas" / "README.md").write_text("not a persona\n")
    s = load_imprint_summary("served", fixtures_dir=tmp_path)
    assert s.hosts == 5
    assert s.services == 3 * 2 + 1, "per instance, router excluded, no claim counts nothing"
    assert s.personas == 3
    assert s.out_of_vocab_pct is None
    assert not any("out_of_vocab_pct" in w for w in s.warnings), s.warnings
