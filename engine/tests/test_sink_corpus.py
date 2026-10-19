"""Tests for `scripts/gen_sink_corpus.py` -- the twin sink's DNS map + route set.

The helper lives outside the package, so it is loaded with
`spec_from_file_location` against its real path (LES-029).

Every fixture here is HAND-WRITTEN and tiny. The real cast topology is 2.6 MB and
reading it in a test would buy nothing but seconds: the properties under test
(determinism, the two directions of the name<->address many-to-many, fail-loud on a
malformed input, refusal to emit an empty corpus) are all visible in a dozen entries.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "gen_sink_corpus.py"


def _load():
    spec = importlib.util.spec_from_file_location("gen_sink_corpus", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # Registered BEFORE exec: `@dataclass` resolves its own module out of `sys.modules`
    # and raises an opaque AttributeError when the module it is defined in is absent.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


gsc = _load()


def _dns(name: str, address: str, locality: str = "external") -> dict:
    return {
        "entity": f"address:{address}",
        "evidence_class": "referenced",
        "locality": locality,
        "name": name,
        "rows": 2,
    }


def _node(address: str, locality: str = "external") -> dict:
    return {"address": address, "id": f"address:{address}", "locality": locality}


def topology(**overrides) -> dict:
    """A small but complete cast topology: 3 names, 4 addresses, both cardinalities."""
    doc = {
        "imprint_id": "fixture-disco",
        "pit_id": "pit-fixture-1",
        "as_of": "1780405200.0",
        "schema_version": "1.4",
        "nodes": [
            _node("10.80.64.10", locality="local"),
            _node("203.0.113.5"),
            _node("203.0.113.6"),
            _node("198.51.100.20"),
            _node("192.0.2.77"),
        ],
        "observations": {
            "dns": {
                "names": [
                    # one name, two addresses
                    _dns("www.example.com", "203.0.113.5"),
                    _dns("www.example.com", "203.0.113.6"),
                    # two names, one address
                    _dns("cdn.example.net", "198.51.100.20"),
                    _dns("assets.example.net", "198.51.100.20"),
                    # internal -- the twin's own resolver owns this one
                    _dns("dc01.corp.local", "10.80.64.10", locality="local"),
                    # v6 answer -- skipped unless asked for
                    _dns("www.example.com", "2001:db8::5"),
                ]
            }
        },
    }
    doc.update(overrides)
    return doc


def write(directory: Path, doc) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "topology.json"
    path.write_text(json.dumps(doc) if not isinstance(doc, str) else doc, encoding="utf-8")
    return path


# ----------------------------------------------------------------- the happy corpus


def test_both_cardinalities_survive_the_map():
    corpus = gsc.build_corpus(topology())
    # one name -> several addresses, sorted and ALL kept (LES-227: never pick one)
    assert corpus.names["www.example.com"] == ["203.0.113.5", "203.0.113.6"]
    # several names -> one address
    assert corpus.names["cdn.example.net"] == ["198.51.100.20"]
    assert corpus.names["assets.example.net"] == ["198.51.100.20"]
    assert corpus.stats["names"] == 3
    assert corpus.stats["answers"] == 4


def test_internal_names_and_ipv6_are_skipped_and_counted():
    corpus = gsc.build_corpus(topology())
    assert "dc01.corp.local" not in corpus.names
    assert corpus.stats["skipped_not_external"] == 1
    assert corpus.stats["skipped_ipv6"] == 1


def test_include_aaaa_keeps_the_v6_answer():
    corpus = gsc.build_corpus(topology(), include_aaaa=True)
    assert "2001:db8::5" in corpus.names["www.example.com"]
    assert gsc.render_zone(corpus).count(" IN AAAA ") == 1
    # ...but a v6 address is still never routed: the route set stays v4-only.
    assert all(":" not in cidr for cidr in corpus.cidrs)


def test_external_peers_with_no_observed_name_are_still_routed():
    """192.0.2.77 is a peer nothing resolved to -- a literal-address dial still lands."""
    corpus = gsc.build_corpus(topology(), aggregate_min=0)
    assert "192.0.2.77/32" in corpus.cidrs
    assert not any(n.startswith("10.80.64.") for n in corpus.cidrs)


def test_non_unicast_and_private_external_addresses_are_dropped():
    doc = topology()
    doc["nodes"].extend(
        [_node("239.255.255.250"), _node("255.255.255.255"), _node("192.168.9.9")]
    )
    doc["observations"]["dns"]["names"].append(_dns("ssdp.example.org", "239.255.255.250"))
    corpus = gsc.build_corpus(doc, aggregate_min=0)
    assert "ssdp.example.org" not in corpus.names
    assert corpus.stats["skipped_non_unicast"] == 1
    assert corpus.stats["skipped_non_unicast_peer"] == 3
    assert not [c for c in corpus.cidrs if c.startswith(("239.", "255.", "192.168."))]


# --------------------------------------------------------------------- aggregation


def test_aggregate_min_zero_is_lossless():
    doc = topology()
    doc["nodes"].extend(_node(f"203.0.113.{i}") for i in range(10, 30))
    corpus = gsc.build_corpus(doc, aggregate_min=0)
    assert corpus.stats["claimed_addresses"] == corpus.stats["addresses"]
    assert "203.0.113.5/32" in corpus.cidrs


def test_aggregation_claims_a_dense_supernet_whole():
    doc = topology()
    doc["nodes"].extend(_node(f"203.0.113.{i}") for i in range(10, 30))
    corpus = gsc.build_corpus(doc, aggregate_prefix=24, aggregate_min=4)
    assert "203.0.113.0/24" in corpus.cidrs
    # the sparse /24s are NOT claimed whole
    assert "198.51.100.20/32" in corpus.cidrs
    assert corpus.stats["claimed_addresses"] > corpus.stats["addresses"]


def test_aggregation_arguments_are_range_checked():
    with pytest.raises(gsc.SinkCorpusError, match="aggregate-prefix"):
        gsc.build_corpus(topology(), aggregate_prefix=40)
    with pytest.raises(gsc.SinkCorpusError, match="aggregate-min"):
        gsc.build_corpus(topology(), aggregate_min=-1)


# -------------------------------------------------------------------- determinism


def test_same_topology_in_byte_identical_files_out(tmp_path):
    path = write(tmp_path, topology())
    _, first = gsc.generate(path, fmt="both")
    _, second = gsc.generate(path, fmt="both")
    assert first == second
    # and the serial is content-derived, not a clock: it survives a re-run
    assert gsc._serial("a\nb") == gsc._serial("a\nb")
    assert gsc._serial("a\nb") != gsc._serial("a\nc")


def test_reordering_the_observations_does_not_change_the_output(tmp_path):
    doc = topology()
    shuffled = topology()
    shuffled["observations"]["dns"]["names"].reverse()
    shuffled["nodes"].reverse()
    _, a = gsc.generate(write(tmp_path / "1", doc))
    _, b = gsc.generate(write(tmp_path / "2", shuffled))
    assert a == b


# ----------------------------------------------------------------------- rendering


def test_zone_is_a_root_zone_with_one_record_per_answer():
    corpus = gsc.build_corpus(topology())
    zone = gsc.render_zone(corpus)
    assert ". IN SOA ns.axr-sink. hostmaster.axr-sink. (" in zone
    assert ". IN NS ns.axr-sink." in zone
    assert f"ns.axr-sink. IN A {gsc.SINK_IP_PLACEHOLDER}" in zone
    assert "www.example.com. IN A 203.0.113.5" in zone
    assert "www.example.com. IN A 203.0.113.6" in zone
    assert zone.count(" IN A ") == 5  # 4 answers + the NS glue


def test_hosts_format_groups_the_names_on_one_line():
    corpus = gsc.build_corpus(topology())
    hosts = gsc.render_hosts(corpus)
    assert "198.51.100.20 assets.example.net cdn.example.net" in hosts


def test_installer_is_a_posix_script_that_lays_down_both_files():
    corpus = gsc.build_corpus(topology())
    installer = gsc.render_installer(gsc.render_zone(corpus), gsc.render_cidrs(corpus))
    assert installer.startswith("#!/bin/sh\n")
    assert "set -eu" in installer
    assert (
        f"base64 -d <<'AXR_SINK_ZONE_EOF' | gunzip > {gsc.GUEST_DIR}/{gsc.ZONE_FILE}"
        in installer
    )
    assert (
        f"base64 -d <<'AXR_SINK_CIDR_EOF' | gunzip > {gsc.GUEST_DIR}/{gsc.CIDR_FILE}"
        in installer
    )
    assert "command -v gunzip" in installer  # fail loud, never a half-written corpus
    # it refuses to deliver an empty corpus in-guest, too
    assert "empty corpus delivered" in installer


def _run_installer(installer: str, tmp_path) -> dict[str, str]:
    """Run the installer for real, with GUEST_DIR redirected into tmp_path."""
    script = tmp_path / "install.sh"
    script.write_text(installer.replace(gsc.GUEST_DIR, str(tmp_path / "guest")))
    proc = subprocess.run(
        ["sh", str(script)], capture_output=True, text=True, cwd=tmp_path
    )
    assert proc.returncode == 0, proc.stderr
    return {p.name: p.read_text() for p in (tmp_path / "guest").iterdir()}


def test_installer_round_trips_the_corpus_byte_for_byte(tmp_path):
    """The corpus travels gzip+base64 (the plain text is over the QGA
    per-argument ceiling), so what the guest ends up with has to be proven
    identical to what the generator rendered -- not merely present."""
    corpus = gsc.build_corpus(topology())
    zone, cidrs = gsc.render_zone(corpus), gsc.render_cidrs(corpus)
    landed = _run_installer(gsc.render_installer(zone, cidrs), tmp_path)
    assert landed[gsc.ZONE_FILE] == zone
    assert landed[gsc.CIDR_FILE] == cidrs


def test_installer_is_far_smaller_than_the_corpus_it_delivers(tmp_path):
    """Why compression exists here at all: `run-script` delivers the script as
    ONE base64 `agent/exec` argument and PVE refuses one of 131072 bytes or
    more (measured, dev45/PVE 9.2 2026-09-21 -- it surfaces as an unexplained
    `HTTP 596 Broken pipe`)."""
    corpus = gsc.build_corpus(topology())
    zone, cidrs = gsc.render_zone(corpus), gsc.render_cidrs(corpus)
    installer = gsc.render_installer(zone, cidrs)
    # the fixture corpus is tiny, so compare against a realistically repetitive
    # one: 20k zone lines, the shape a real cast produces.
    big = zone + "".join(f"host{n}.example.com. IN A 203.0.113.{n % 250}\n" for n in range(20_000))
    grown = gsc.render_installer(big, cidrs)
    assert len(grown) < len(big) // 4
    assert len(installer) < 8192  # the fixture stays trivially deliverable


def test_installer_is_byte_identical_across_runs():
    """Determinism survives the compression: gzip's mtime is zeroed, never a
    clock, so the same corpus still produces the same plan_id."""
    corpus = gsc.build_corpus(topology())
    zone, cidrs = gsc.render_zone(corpus), gsc.render_cidrs(corpus)
    assert gsc.render_installer(zone, cidrs) == gsc.render_installer(zone, cidrs)


def test_installer_survives_content_that_would_truncate_a_plain_heredoc(tmp_path):
    """The heredoc marker inside the corpus used to be a fatal condition. It
    cannot truncate a base64 heredoc (`_` is not in the alphabet), so it is now
    carried, not refused -- and the round trip proves it."""
    zone = "x.example.com. IN A 203.0.113.9\nAXR_SINK_ZONE_EOF\n"
    landed = _run_installer(gsc.render_installer(zone, "1.2.3.4/32\n"), tmp_path)
    assert landed[gsc.ZONE_FILE] == zone


def test_manifest_carries_the_cast_provenance_and_file_digests(tmp_path):
    corpus, files = gsc.generate(write(tmp_path, topology()), fmt="both")
    manifest = json.loads(files[gsc.MANIFEST_FILE])
    assert manifest["source"]["imprint_id"] == "fixture-disco"
    assert manifest["source"]["pit_id"] == "pit-fixture-1"
    assert manifest["counts"]["names"] == 3
    assert set(manifest["files"]) == {
        gsc.ZONE_FILE,
        gsc.HOSTS_FILE,
        gsc.CIDR_FILE,
        gsc.INSTALLER_FILE,
    }


# ------------------------------------------------------------------------ fail loud


@pytest.mark.parametrize(
    "mutate, expected",
    [
        (lambda d: d.pop("observations"), "observations"),
        (lambda d: d["observations"].pop("dns"), "observations.dns"),
        (lambda d: d["observations"]["dns"].pop("names"), "observations.dns.names"),
        (lambda d: d["observations"]["dns"].__setitem__("names", {}), "expected list"),
        (lambda d: d.pop("nodes"), "nodes"),
        (lambda d: d["observations"]["dns"]["names"].append({"name": "x.example.com"}), "missing 'entity'"),
        (lambda d: d["observations"]["dns"]["names"].append("nope"), "expected an object"),
        (
            lambda d: d["observations"]["dns"]["names"].append(
                {"entity": "host:abc", "name": "x.example.com", "locality": "external"}
            ),
            "not an 'address:<ip>' entity",
        ),
        (
            lambda d: d["observations"]["dns"]["names"].append(
                {"entity": "address:not-an-ip", "name": "x.example.com", "locality": "external"}
            ),
            "carries no valid IP",
        ),
        (
            lambda d: d["observations"]["dns"]["names"].append(
                {"entity": "address:203.0.113.9", "name": 42, "locality": "external"}
            ),
            "expected a string",
        ),
        (lambda d: d["nodes"].append({"id": "address:1.2.3.4", "locality": "external"}), "no 'address'"),
        (lambda d: d["nodes"].append({"address": "hello", "locality": "external"}), "is not an IP"),
    ],
)
def test_a_malformed_topology_fails_loud(mutate, expected):
    doc = topology()
    mutate(doc)
    with pytest.raises(gsc.SinkCorpusError) as exc:
        gsc.build_corpus(doc)
    assert expected in str(exc.value)


def test_unparseable_json_fails_loud(tmp_path):
    path = write(tmp_path, "{not json")
    with pytest.raises(gsc.SinkCorpusError, match="not valid JSON"):
        gsc.load_topology(path)


def test_a_missing_topology_fails_loud(tmp_path):
    with pytest.raises(gsc.SinkCorpusError, match="cannot read topology"):
        gsc.load_topology(tmp_path / "nope.json")


def test_a_json_array_is_not_a_topology(tmp_path):
    path = write(tmp_path, [])
    with pytest.raises(gsc.SinkCorpusError, match="not a JSON object"):
        gsc.load_topology(path)


def test_an_empty_corpus_is_refused_not_emitted():
    doc = topology()
    doc["observations"]["dns"]["names"] = [_dns("dc01.corp.local", "10.80.64.10", locality="local")]
    doc["nodes"] = [_node("10.80.64.10", locality="local")]
    with pytest.raises(gsc.SinkCorpusError, match="refusing to emit an empty corpus"):
        gsc.build_corpus(doc)


def test_an_empty_route_set_is_refused():
    """Names but no routable address: nothing would ever reach the sink."""
    doc = topology()
    doc["nodes"] = [_node("10.80.64.10", locality="local")]
    doc["observations"]["dns"]["names"] = [_dns("www.example.com", "2001:db8::5")]
    with pytest.raises(gsc.SinkCorpusError, match="empty corpus|empty route set"):
        gsc.build_corpus(doc)


# ------------------------------------------------------------------------------ cli


def test_main_writes_the_corpus_and_summarizes(tmp_path, capsys):
    path = write(tmp_path, topology())
    out = tmp_path / "out"
    assert gsc.main(["--topology", str(path), "--out", str(out), "--format", "both"]) == 0
    for name in (gsc.ZONE_FILE, gsc.HOSTS_FILE, gsc.CIDR_FILE, gsc.INSTALLER_FILE, gsc.MANIFEST_FILE):
        assert (out / name).is_file(), name
    assert (out / gsc.INSTALLER_FILE).stat().st_mode & 0o111
    summary = capsys.readouterr().out
    assert "dns names            : 3" in summary
    assert "routed cidrs" in summary


def test_main_dry_run_writes_nothing(tmp_path, capsys):
    path = write(tmp_path, topology())
    out = tmp_path / "out"
    assert gsc.main(["--topology", str(path), "--out", str(out), "--dry-run"]) == 0
    assert not out.exists()


def test_main_exits_1_on_a_malformed_topology(tmp_path, capsys):
    path = write(tmp_path, "{not json")
    assert gsc.main(["--topology", str(path), "--out", str(tmp_path / "out")]) == 1
    assert "gen_sink_corpus:" in capsys.readouterr().err
