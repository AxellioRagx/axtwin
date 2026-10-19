"""`scripts/shape_fingerprint.py` — the locality override for the twin's synthetic sink.

The twin is given an "internet sink": a node group INSIDE the twin that hosts talk to, so
their outbound share stops being spent on local edges. For the comparison to stay
like-for-like the sink's segment has to be measured as EXTERNAL on the twin side.

`locality` is a SENSOR-STAMPED entity flag, not an address property
(`aximprint/windowing/segments.py:locality`). XPress-SW-A's Zeek has `Site::local_nets`
covering 10/8, so a sink anywhere in private space arrives stamped `local` with
observed/0.95 evidence — no choice of address range avoids it. So the override is a
post-ingest reclassification in the FINGERPRINT, and these tests pin the two properties
that makes it safe to have: it is inert when unused, and it applies to one side only.
"""
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import duckdb
import pytest

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _ROOT / "scripts" / "shape_fingerprint.py"
_spec = importlib.util.spec_from_file_location("shape_fingerprint", _SCRIPT)
sf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sf)

from aximprint.synth.compiler import SINK_CIDR

#: IMPORTED, never re-typed. The synthesis half decides where the sink lives and this half has
#: to be told; a second literal here would agree on the day it was written and drift silently
#: afterwards, because `_parse_segments` makes a NON-MATCHING override a no-op rather than an
#: error. The failure mode is the expensive kind: `external_dst_edge_share` reads ~0, every axis
#: looks byte-identical to a pre-sink run, and the measurement was simply never enabled.
SINK = SINK_CIDR
CORP = "10.200.11.0/24"        # an ordinary twin segment


def _store(path: Path, entities, edges, *, network="twin", segments=2) -> str:
    """A minimal materialized IR store: only the columns the fingerprint reads.

    `fingerprint` does `select *` and then `.get()`s, so a narrow table is a faithful
    stand-in for a real store and keeps what each test asserts visible in the test.
    """
    con = duckdb.connect(str(path))
    con.execute("create table ir_pit (source varchar, network varchar, windows integer, "
                "observed_from double, observed_to double, segments integer)")
    con.execute("insert into ir_pit values (?, ?, ?, ?, ?, ?)",
                ["zeek", network, 10, 0.0, 600.0, segments])
    con.execute("create table ir_entity (entity_key varchar, locality varchar, "
                "segment varchar, role varchar)")
    for key, loc, seg, role in entities:
        con.execute("insert into ir_entity values (?, ?, ?, ?)", [key, loc, seg, role])
    con.execute("create table ir_edge (src_key varchar, dst_key varchar)")
    for a, b in edges:
        con.execute("insert into ir_edge values (?, ?)", [a, b])
    con.close()
    return str(path)


@pytest.fixture
def twin(tmp_path):
    """Two ordinary hosts on CORP, one sink host on SINK, and edges into each."""
    return _store(
        tmp_path / "twin-ir.duckdb",
        entities=[
            ("ws1", "local", CORP, "corp-workstation"),
            ("ws2", "local", CORP, "corp-workstation"),
            ("sink1", "local", SINK, "web-tier"),
            ("pub1", "external", None, "web-tier"),
        ],
        edges=[("ws1", "ws2"), ("ws1", "sink1"), ("ws2", "sink1"), ("ws1", "pub1")],
    )


# -- the predicate ------------------------------------------------------------------

def test_is_local_default_is_the_sensor_verdict():
    """No override -> the sensor's stamp, unmodified. The backward-compat guarantee."""
    assert sf._is_local({"locality": "local", "segment": SINK}) is True
    assert sf._is_local({"locality": "external", "segment": None}) is False


def test_is_local_override_flips_only_the_named_segment():
    ext = frozenset({SINK})
    assert sf._is_local({"locality": "local", "segment": SINK}, ext) is False
    assert sf._is_local({"locality": "local", "segment": CORP}, ext) is True


def test_is_local_override_keyed_on_cidr_not_segment_name():
    """`--relocate` rewrites addresses but NOT the observed-octet segment names, so a name
    in a relocated twin points at where the segment used to be. Only the CIDR may match."""
    # A name derived from the sink's own CIDR, built rather than typed so it cannot go stale
    # the way the hardcoded `seg-10-200-200-0` did when the sink moved to `.254`.
    stale_name = "seg-" + SINK.split("/")[0].replace(".", "-")
    assert sf._is_local({"locality": "local", "segment": SINK},
                        frozenset({stale_name})) is True


def test_is_local_unplaced_local_survives_an_override():
    """An entity with no segment at all must NOT be swept external by any override."""
    assert sf._is_local({"locality": "local", "segment": None}, frozenset({SINK})) is True


def test_parse_segments_accepts_commas_spaces_and_drops_blanks():
    assert sf._parse_segments(None) == frozenset()
    assert sf._parse_segments("") == frozenset()
    assert sf._parse_segments("  ,  ") == frozenset()
    assert sf._parse_segments(SINK) == frozenset({SINK})
    assert sf._parse_segments(f"{SINK},{CORP}") == frozenset({SINK, CORP})
    assert sf._parse_segments(f" {SINK} , {CORP} ") == frozenset({SINK, CORP})


# -- the fingerprint ----------------------------------------------------------------

def test_sink_entity_is_local_without_the_override(twin):
    fp = sf.fingerprint(twin)
    assert fp["counts"]["local"] == 3          # ws1, ws2, sink1
    assert fp["counts"]["placed"] == 3


def test_sink_entity_is_counted_external_with_the_override(twin):
    fp = sf.fingerprint(twin, external_segments={SINK})
    assert fp["counts"]["local"] == 2          # sink1 has left
    assert fp["counts"]["placed"] == 2


def test_override_moves_external_dst_edge_share(twin):
    """The axis the sink exists to move: 1 of 4 edges leaves the network, then 3 of 4."""
    assert sf.fingerprint(twin)["external_dst_edge_share"] == 0.25
    assert sf.fingerprint(
        twin, external_segments={SINK})["external_dst_edge_share"] == 0.75


def test_override_moves_role_edges_local_and_fan_out_local(twin):
    base = sf.fingerprint(twin)
    ovr = sf.fingerprint(twin, external_segments={SINK})
    assert sum(base["role_edges_local"].values()) == 3      # ->ws2, ->sink1, ->sink1
    assert sum(ovr["role_edges_local"].values()) == 1       # only ->ws2 survives
    assert base["fan_out_degree_local"] == {"1": 1, "2-5": 1}
    assert ovr["fan_out_degree_local"] == {"1": 1}          # ws2 drops to degree 0


def test_override_moves_roled_unplaced(twin):
    """A roled entity that is no longer local-and-placed is exactly what this counts."""
    assert sf.fingerprint(twin)["roled_unplaced"] == {"web-tier": 1}          # pub1
    assert sf.fingerprint(twin, external_segments={SINK})["roled_unplaced"] == {
        "web-tier": 2}                                                        # + sink1


def test_override_does_not_touch_the_locality_blind_axes(twin):
    """`role_edges`, `fan_out_degree` and the edge count are locality-free by design and
    must not move — the point of a per-axis fingerprint is that axes fail independently."""
    base = sf.fingerprint(twin)
    ovr = sf.fingerprint(twin, external_segments={SINK})
    assert base["role_edges"] == ovr["role_edges"]
    assert base["fan_out_degree"] == ovr["fan_out_degree"]
    assert base["counts"]["edges"] == ovr["counts"]["edges"]
    assert base["counts"]["entities"] == ovr["counts"]["entities"]


def test_absent_segment_override_is_completely_inert(twin):
    """`compare_twin_to_source.sh` DEFAULTS to the sink CIDR, so a run against a twin with
    no sink deployed must produce byte-identical output to a run with no override at all."""
    plain = json.dumps(sf.fingerprint(twin), sort_keys=True)
    absent = json.dumps(sf.fingerprint(twin, external_segments={"10.99.99.0/24"}),
                        sort_keys=True)
    assert plain == absent


def test_empty_override_is_byte_identical_to_no_argument(twin):
    plain = json.dumps(sf.fingerprint(twin), sort_keys=True)
    for empty in (frozenset(), set(), [], ()):
        assert json.dumps(sf.fingerprint(twin, external_segments=empty),
                          sort_keys=True) == plain


def test_unresolved_edge_endpoint_still_counts_external(tmp_path):
    """`is_local.get(k)` returns None for an endpoint that never became an entity ->
    falsy -> external. Preserved deliberately: an endpoint the materializer could not
    resolve is, by construction, not a host the twin stands up."""
    store = _store(tmp_path / "ghost.duckdb",
                   entities=[("ws1", "local", CORP, "corp-workstation")],
                   edges=[("ws1", "ghost")])
    for ext in ({}, {SINK}):
        fp = sf.fingerprint(store, external_segments=ext)
        assert fp["external_dst_edge_share"] == 1.0
        assert fp["fan_out_degree_local"] == {}


# -- compare(): the B side only ------------------------------------------------------

@pytest.fixture
def source(tmp_path):
    """A source-network store that happens to own the SINK cidr, so a leaked override
    would be visible in its numbers."""
    return _store(
        tmp_path / "src-ir.duckdb",
        entities=[
            ("s1", "local", SINK, "corp-workstation"),
            ("s2", "local", CORP, "web-tier"),
            ("sx", "external", None, "web-tier"),
        ],
        edges=[("s1", "s2"), ("s1", "sx"), ("s2", "sx")],
        network="corpapcn",
    )


def test_compare_leaves_the_source_side_untouched(source, twin):
    """The source has no synthetic sink; applying the override to both sides would
    silently re-measure the reference the twin is scored against."""
    plain = sf.compare(source, twin)
    ovr = sf.compare(source, twin, b_external_segments={SINK})
    a_only = [ln for ln in plain.splitlines() if ln.startswith("SOURCE")]
    assert a_only == [ln for ln in ovr.splitlines() if ln.startswith("SOURCE")]
    # the source's own local count is 2 in both, though s1 sits in the SINK cidr
    assert "local        source=2" in plain
    assert "local        source=2" in ovr
    assert "external_dst_edge_share:  source=0.6667" in plain
    assert "external_dst_edge_share:  source=0.6667" in ovr


def test_compare_prints_external_dst_edge_share(source, twin):
    """Computed since the axis existed, silently dropped by `compare` — the view an
    operator actually reads. A run could not be evaluated on the number it was run to move."""
    out = sf.compare(source, twin)
    assert "external_dst_edge_share:" in out
    assert "twin=0.2500" in out


def test_compare_moves_only_the_twin_side_and_discloses_the_override(source, twin):
    out = sf.compare(source, twin, b_external_segments={SINK})
    assert "external_dst_edge_share:  source=0.6667   twin=0.7500" in out
    assert f"twin external-segment override: {SINK}" in out
    assert "counts.segments above still includes it" in out


def test_compare_without_override_prints_no_disclosure(source, twin):
    assert "external-segment override" not in sf.compare(source, twin)


# -- the CLI ------------------------------------------------------------------------

def _run(*args):
    return subprocess.run([sys.executable, str(_SCRIPT), *args],
                          capture_output=True, text=True, check=False)


def test_cli_old_argument_forms_still_work(source, twin):
    """Every prior iteration's invocation must keep working unchanged."""
    fp = _run("fingerprint", twin)
    assert fp.returncode == 0
    assert json.loads(fp.stdout)["counts"]["local"] == 3
    cmp_ = _run("compare", source, twin)
    assert cmp_.returncode == 0
    assert "external-segment override" not in cmp_.stdout


def test_cli_accepts_the_optional_segment_argument(source, twin):
    fp = _run("fingerprint", twin, SINK)
    assert fp.returncode == 0
    assert json.loads(fp.stdout)["counts"]["local"] == 2
    cmp_ = _run("compare", source, twin, f"{SINK},10.99.99.0/24")
    assert cmp_.returncode == 0
    assert "twin=0.7500" in cmp_.stdout
    # an empty argument is the documented way to reproduce a pre-sink run exactly
    assert "external-segment override" not in _run("compare", source, twin, "").stdout


def test_cli_rejects_too_many_arguments(twin):
    assert _run("fingerprint", twin, SINK, "extra").returncode == 2


def test_the_three_sink_literals_cannot_drift_apart():
    """`SINK_CIDR` is spelled out in three places — this module, `synth/compiler.py`, and the
    `${6-...}` default in `scripts/compare_twin_to_source.sh` that makes `measure.sh` work
    unedited. Two of them are now one import; the shell cannot import Python, so it is pinned
    here instead.

    Worth a test rather than a comment because a mismatch is SILENT. `_parse_segments` treats
    an override matching no segment as a no-op — by design, so a pre-sink twin needs no edit —
    which means a drifted CIDR disables the whole twin-side locality correction without a
    single warning. `external_dst_edge_share` reads ~0, the run looks like a clean pre-sink
    baseline, and the only symptom is hours spent debugging a measurement nobody enabled."""
    driver = (_ROOT / "scripts" / "compare_twin_to_source.sh").read_text()
    m = re.search(r'^SINK_CIDRS="\$\{6-([^}]*)\}"', driver, re.MULTILINE)
    assert m, "compare_twin_to_source.sh must keep a defaulted SINK_CIDRS assignment"
    assert m.group(1) == SINK_CIDR, (
        f"shell default {m.group(1)!r} != compiler SINK_CIDR {SINK_CIDR!r} — a drifted override "
        f"matches no segment and silently disables the twin-side locality correction")
