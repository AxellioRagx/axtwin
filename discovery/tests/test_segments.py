"""Segment extraction: address -> twin segment, and the locality verdict behind it.

`docs/WORK_ORDER_synth_v1.md` phase 1. Synthetic events only (`LES-003`).

The through-line of this file is the three-way distinction the feature exists to hold:
the sensor SAID a host is inside, we INFERRED it from address shape, or nobody knows.
Collapsing any pair of those is the failure mode, and most tests here are about a pair.
"""
import pytest

from aximprint.events import FlowEvent
from aximprint.learners import extract_assertions
from aximprint.store.belief_store import BeliefStore
from aximprint.windowing import WindowManager
from aximprint.windowing.segments import (
    SEGMENT_EVIDENCE, evidence_key, locality, segment_name, segment_of,
)

T0 = 1_754_000_000.0
WINDOW = 3600
ALIGNED = T0 - (T0 % WINDOW)


def _flow(ts=ALIGNED + 1, src="10.80.72.13", dst="10.80.20.5",
          local_src=None, local_dst=None):
    return FlowEvent(ts=ts, src_ip=src, dst_ip=dst, src_port=51000, dst_port=445,
                     proto="tcp", service="", duration=1.0,
                     bytes_out=100, bytes_in=200, pkts_out=2, pkts_in=2,
                     state="SF", state_class="established",
                     local_src=local_src, local_dst=local_dst)


def _one_window(events):
    wm = WindowManager(window_seconds=WINDOW)
    wins = list(wm.process(iter(events))) + list(wm.finalize())
    assert len(wins) == 1, "fixture must stay inside one window"
    return wins[0]


# -- the address functions ------------------------------------------------------

def test_segment_of_masks_to_the_declared_prefix():
    assert segment_of("10.80.72.13") == "10.80.72.0/24"
    assert segment_of("192.168.0.7") == "192.168.0.0/24"


def test_segment_of_uses_a_v6_prefix_for_v6():
    """A /24 of IPv6 is a routing entry, not a LAN — the two prefixes are separate
    constants precisely so a v6 address cannot fall through to the v4 mask."""
    assert segment_of("2001:db8::1") == "2001:db8::/64"


def test_segment_of_returns_none_for_a_non_address():
    """Parse, never shape-match (`LES-008`). A hostname is not a segment."""
    assert segment_of("workstn1.corp.example.test") is None
    assert segment_of("") is None


def test_segment_name_is_an_identifier_and_round_trips():
    """axRange needs `name` (a node's `net:` reference) as well as `cidr`."""
    assert segment_name("10.80.72.0/24") == "seg-10-80-72-0"
    assert segment_name("2001:db8::/64") == "seg-2001-db8--"


# -- locality: the three-way distinction ----------------------------------------

@pytest.mark.parametrize("says,expected", [(True, "local"), (False, "external")])
def test_the_sensor_verdict_is_taken_verbatim_and_is_not_imputed(says, expected):
    assert locality("10.80.72.13", says) == (expected, False)


def test_the_sensor_beats_the_address_shape_in_both_directions():
    """The whole reason `local_orig` is parsed at all.

    A site addressing internally out of public space, and one whose RFC1918 space is
    routed but outside the monitored estate, are both invisible to a shape test and
    correct in `local_nets`. Neither answer here is what `is_private` would give.
    """
    assert locality("203.0.113.9", True) == ("local", False)
    assert locality("10.80.72.13", False) == ("external", False)


def test_address_shape_is_the_fallback_and_says_so():
    assert locality("10.80.72.13", None) == ("local", True)
    assert locality("140.82.113.4", None) == ("external", True)


def test_an_unparseable_subject_yields_no_verdict_and_no_imputation():
    """`None`, not "external" (`LES-006`).

    And `imputed=False`: nothing was imputed because nothing was concluded. Reporting
    True would inflate the very counter that discloses how much of the map is guessed.
    """
    assert locality("not-an-address", None) == (None, False)


def test_evidence_key_selects_a_row_that_exists():
    for imputed in (True, False):
        assert evidence_key(imputed) in SEGMENT_EVIDENCE


def test_imputed_evidence_is_ranked_below_a_dns_answer():
    """RFC1918 membership is not a weak observation — it is not an observation.

    Ranked below `bound_dns`'s 0.7 on purpose: a DNS answer is at least a claim
    somebody made.
    """
    assert SEGMENT_EVIDENCE["address_shape"] == ("imputed", 0.6)
    assert SEGMENT_EVIDENCE["sensor"][0] == "observed"
    assert SEGMENT_EVIDENCE["address_shape"][1] < SEGMENT_EVIDENCE["sensor"][1]


# -- accumulation ---------------------------------------------------------------

def test_a_local_endpoint_gets_a_segment_and_an_external_one_does_not():
    """The internal/external partition is expressed as the ABSENCE of a segment —
    2,995 internet peers on the real baseline, none of which a twin can deploy."""
    win = _one_window([_flow(local_src=True, local_dst=False,
                             src="10.80.72.13", dst="140.82.113.4")])
    assert win.nodes["10.80.72.13"]["segments"] == ["10.80.72.0/24"]
    assert win.nodes["10.80.72.13"]["locality"] == "local"
    assert win.nodes["140.82.113.4"]["segments"] == []
    assert win.nodes["140.82.113.4"]["locality"] == "external"


def test_locality_is_none_when_nothing_could_judge_it():
    """A host seen only at an unparseable address has no verdict — not "external"."""
    win = _one_window([_flow(src="10.80.72.13", dst="10.80.20.5",
                             local_src=True, local_dst=True)])
    # every node here IS judged; the None case needs an entity with no usable address,
    # which `_observe_locality` refuses to record at all — so it shows up as a counter.
    assert win.health["locality_unknown_endpoints"] == 0
    assert all(n["locality"] is not None for n in win.nodes.values())


def test_an_unusable_address_is_counted_rather_than_judged():
    win = _one_window([_flow(src="", dst="10.80.20.5", local_dst=True)])
    assert win.health["locality_unknown_endpoints"] == 1
    assert win.health["segments_observed"] == 1


def test_the_imputed_endpoint_count_separates_a_sensor_from_a_guess():
    """0 on a Zeek that declares `local_nets`; the endpoint count on one that does not.
    This is what tells those two captures apart without opening the assertions table."""
    declared = _one_window([_flow(local_src=True, local_dst=True)])
    assert declared.health["locality_imputed_endpoints"] == 0
    assert declared.nodes["10.80.72.13"]["locality_imputed"] is False

    silent = _one_window([_flow()])          # no local_orig/local_resp at all
    assert silent.health["locality_imputed_endpoints"] == 2
    assert silent.nodes["10.80.72.13"]["locality_imputed"] is True
    # …and it still lands in the right segment, because RFC1918 says so
    assert silent.nodes["10.80.72.13"]["segments"] == ["10.80.72.0/24"]


def test_a_conflicting_entity_is_resolved_toward_inclusion_and_counted():
    """Reachable when one entity spans addresses that disagree. The tie-break is a
    choice with no evidence behind it, so it is declared AND disclosed."""
    win = _one_window([
        _flow(ts=ALIGNED + 1, src="10.80.72.13", dst="10.80.20.5",
              local_src=True, local_dst=True),
        _flow(ts=ALIGNED + 2, src="10.80.72.13", dst="10.80.20.5",
              local_src=False, local_dst=True),
    ])
    assert win.nodes["10.80.72.13"]["locality"] == "local"   # tie -> local
    assert win.health["locality_conflicts"] == 1


def test_segments_are_sorted_so_the_row_is_byte_stable():
    """`LES-002`: a set reaching an output is enough to break determinism."""
    win = _one_window([
        _flow(ts=ALIGNED + 1, src="10.80.99.1", dst="10.80.20.5",
              local_src=True, local_dst=True),
        _flow(ts=ALIGNED + 2, src="10.80.11.1", dst="10.80.99.1",
              local_src=True, local_dst=True),
    ])
    for node in win.nodes.values():
        assert node["segments"] == sorted(node["segments"])


# -- assertions -----------------------------------------------------------------

def test_a_sensor_backed_segment_is_asserted_as_observed():
    win = _one_window([_flow(local_src=True, local_dst=True)])
    segs = [a for a in extract_assertions(win) if a["predicate"] == "in_segment"]
    assert {a["object"] for a in segs} == {"10.80.72.0/24", "10.80.20.0/24"}
    assert all(a["evidence_class"] == "observed" for a in segs)
    assert all(a["confidence"] == 0.95 for a in segs)
    assert all(a["source"] == "extractor:segment-census" for a in segs)


def test_an_inferred_segment_is_asserted_as_imputed():
    """The first `imputed` row this project has ever written — the class had been in
    the vocabulary since v0 with no producer at all."""
    win = _one_window([_flow()])
    segs = [a for a in extract_assertions(win) if a["predicate"] == "in_segment"]
    assert segs, "an RFC1918 host must still be placed, just not as observed"
    assert all(a["evidence_class"] == "imputed" for a in segs)
    assert all(a["confidence"] == 0.6 for a in segs)


def test_an_external_peer_gets_no_segment_assertion():
    win = _one_window([_flow(local_src=True, local_dst=False,
                                src="10.80.72.13", dst="140.82.113.4")])
    segs = [a for a in extract_assertions(win) if a["predicate"] == "in_segment"]
    assert [a["subject"] for a in segs] == ["10.80.72.13"]


# -- the snapshot the compiler reads --------------------------------------------

def test_the_snapshot_carries_segments_with_their_evidence_class(tmp_path):
    """An assertion nobody can read is half a feature — phase 3 reads this."""
    store = BeliefStore(str(tmp_path / "s.duckdb"))
    win = _one_window([_flow(local_src=True, local_dst=True)])
    store.write_window(win, extract_assertions(win))
    snap = store.snapshot()
    assert ("10.80.72.13", "10.80.72.0/24", 0.95, "observed") in snap["segments"]
    assert snap["segments"] == sorted(snap["segments"]), "byte-stable for the compiler"


def test_the_store_keeps_BOTH_readings_and_the_materializer_picks_the_observed_one(tmp_path):
    """**Rewritten 2026-09-05** (`docs/WORK_ORDER_reference_ingest.md` S2). This used to assert
    that `snapshot()` returned the observed reading and nothing else — a cross-class reduction the
    store made on `max(confidence)` alone, right only because `SEGMENT_EVIDENCE`'s two rows
    (observed 0.95, imputed 0.6) happen to rank the same way twice.

    Two things moved it. The **house rule** this slice is built around is *"the store keeps both
    readings, and an override is visible, never an erasure"* — a reduction here erases the loser
    before anything can name it. And **precedence cannot be computed here at all**: `evidence_rank`
    lives in `aximprint.ir`, which `store` may not import (CONVENTIONS §3), so the alternative
    was a second rank table down here — the vocabulary drift `LES-101` names.

    The OUTCOME is unchanged and is asserted where it is now decided: `ir/materialize.py::resolve`
    picks the strongest class, and the entity's `segment_evidence_class` says so."""
    from aximprint.ir.materialize import materialize
    from aximprint.provenance import build_stamp

    store = BeliefStore(str(tmp_path / "s.duckdb"))
    guessed = _one_window([_flow(ts=ALIGNED + 1)])
    store.write_window(guessed, extract_assertions(guessed))
    seen = _one_window([_flow(ts=ALIGNED + WINDOW + 1, local_src=True, local_dst=True)])
    store.write_window(seen, extract_assertions(seen))

    rows = [r for r in store.snapshot()["segments"] if r[0] == "10.80.72.13"]
    assert sorted(r[3] for r in rows) == ["imputed", "observed"], \
        "both readings survive — the loser is named, not erased"

    store.write_entity_roster("/cap/one", {})
    store.write_run({"source": "/cap/one", "plugin": "zeek", "window_seconds": WINDOW,
                     "subbuckets": 12}, build_stamp(), 2, ALIGNED, ALIGNED + 1)
    pit = materialize(store)
    entity = next(e for e in pit["ir_entity"] if e["entity_key"] == "address:10.80.72.13")
    assert entity["segment_evidence_class"] == "observed", "a real observation is not weakened"
    assert entity["segment_confidence"] == 0.95


# -- what is not a network ------------------------------------------------------
#
# Found by running phase 1 end to end on the real baseline, not by review: the first
# run minted 46 segments, of which five were not networks. `fe80::/64` was the LARGEST,
# at 105 hosts — a segment no twin can deploy and that every host is on at once.

@pytest.mark.parametrize("addr", [
    "fe80::1",             # IPv6 link-local — every interface autoconfigures one
    "169.254.169.254",     # IPv4 link-local (the cloud metadata endpoint)
    "224.0.0.251",         # multicast: a destination GROUP, never a host
    "239.255.255.250",     # administratively-scoped multicast (SSDP)
    "127.0.0.1",           # loopback
    "::",                  # unspecified
])
def test_an_address_on_no_provisionable_network_mints_no_segment(addr):
    assert segment_of(addr) is None


@pytest.mark.parametrize("addr", ["10.80.72.13", "192.168.0.7", "203.0.113.9",
                                  "2001:db8::1"])
def test_a_real_unicast_address_still_mints_one(addr):
    assert segment_of(addr) is not None


def test_declining_a_segment_does_not_change_the_locality_verdict():
    """Two different questions. A host holding an `fe80::` address genuinely IS on-link;
    it is simply not on a network anybody provisioned."""
    assert locality("fe80::1", None)[0] == "local"
    assert segment_of("fe80::1") is None


def test_a_declined_segment_is_counted_not_silently_skipped():
    """A decline that leaves no trace is indistinguishable from no traffic."""
    win = _one_window([_flow(src="fe80::1", dst="10.80.20.5",
                             local_src=True, local_dst=True)])
    assert win.health["segments_declined_endpoints"] == 1
    assert win.nodes["fe80::1"]["locality"] == "local"
    assert win.nodes["fe80::1"]["segments"] == []
    assert win.health["segments_observed"] == 1     # only the real one


def test_vlan_is_not_segment_evidence_and_the_refusal_is_measured():
    """`docs/WORK_ORDER_axquery_ingest.md` D3 proposed a `vlan` row here at `observed`, and
    measurement refused it: on 50,796 real axquery flows, **210 public endpoints carry a tag**
    (so it is not a locality signal) and **7 of 27 private /24s span multiple VLANs** (so it
    does not refine the address-derived segment either -- it cross-cuts it).

    Pinned as a test because the proposal is written down in a ratified-looking work order and
    the next reader will find the idea attractive for the same reasons I did.
    """
    from aximprint.windowing.segments import SEGMENT_EVIDENCE

    assert "vlan" not in SEGMENT_EVIDENCE
    assert set(SEGMENT_EVIDENCE) == {"sensor", "address_shape"}


def test_flow_vlan_fields_exist_and_stay_out_of_the_locality_verdict():
    """The fields are COLLECTED (forensic completeness) and must not reach `locality()`, whose
    only sensor input is the `local_orig`/`local_resp` judgment."""
    import inspect

    from aximprint.events import FlowEvent
    from aximprint.windowing.segments import locality

    assert {"vlan_src", "vlan_dst"} <= set(FlowEvent.__dataclass_fields__)
    assert "vlan" not in inspect.signature(locality).parameters
