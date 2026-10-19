"""Structural drift between two PITs (`docs/WORK_ORDER_network_ir.md` D8, build step 5).

Synthetic events only (`LES-003`). This is the step that tests whether D4 and D4a were designed
right, so most of these are about the two things a comparator gets wrong: claiming absence it
cannot support, and answering a question it should refuse.
"""
import pytest

from aximprint.events import FlowEvent
from aximprint.ir.compare import (
    COVERAGE_FLOOR, MAX_DURATION_RATIO, IncommensurableError, compare,
)
from aximprint.ir.materialize import materialize
from aximprint.ir.store import IRStore
from aximprint.learners import extract_assertions
from aximprint.provenance import build_stamp
from aximprint.store.belief_store import BeliefStore
from aximprint.windowing import WindowManager

WINDOW = 3600
T0 = 1_754_000_000.0
W1 = T0 - (T0 % WINDOW)
W2 = W1 + WINDOW


def _flow(ts, src, dst, dport=445, local_dst=True):
    return FlowEvent(ts=ts, src_ip=src, dst_ip=dst, src_port=51000, dst_port=dport,
                     proto="tcp", service="", duration=1.0, bytes_out=100, bytes_in=200,
                     pkts_out=2, pkts_in=2, state="SF", state_class="established",
                     local_src=True, local_dst=local_dst)


def _pits(tmp_path, events, spans, *, source="/cap/one", name="s.duckdb",
          vantage=None, network=None):
    """Materialize one PIT per `(since, as_of)` span from one belief store."""
    st = BeliefStore(str(tmp_path / name))
    wm = WindowManager(window_seconds=WINDOW)
    wins = list(wm.process(iter(events))) + list(wm.finalize())
    for w in wins:
        st.write_window(w, extract_assertions(w))
    st.write_entity_roster(source, wm.entity_roster())
    st.write_run({"source": source, "plugin": "zeek", "window_seconds": WINDOW,
                  "subbuckets": 12, "vantage": vantage, "network": network},
                 build_stamp(), len(wins), W1, W1 + 1)
    ir = IRStore(str(tmp_path / f"ir-{name}"))
    ids = []
    for since, as_of in spans:
        pit = materialize(st, as_of=as_of, since=since)
        pit.pop("_stats")
        ir.write_pit(pit)
        ids.append(pit["ir_pit"][0]["pit_id"])
    return ir, ids


def _two_windows(tmp_path, second_window_events, **kw):
    """Window 1 holds a stable pair plus a host that may or may not return in window 2."""
    base = [_flow(W1 + 1, "10.0.1.10", "10.0.1.20"),
            _flow(W1 + 2, "10.0.1.30", "10.0.1.20")]
    return _pits(tmp_path, base + second_window_events, [(W1, W1), (W2, W2)], **kw)


# -- refusals: D4a.4, and the point of the whole step ----------------------------

def test_unequal_observation_windows_are_refused(tmp_path):
    """Nearly every host would read as "added" in the longer PIT for no reason but that there
    was more time to see it. Measured: the two PITs a naive workflow produces first — as-of the
    first window vs as-of the last — differ by exactly 3x on the real baseline."""
    w3 = W2 + WINDOW
    events = [_flow(W1 + 1, "10.0.1.10", "10.0.1.20"),
              _flow(W2 + 1, "10.0.1.10", "10.0.1.20"),
              _flow(w3 + 1, "10.0.1.10", "10.0.1.20")]
    # 1 window vs cumulative 3 — the 3x the real baseline produces, and the first thing a naive
    # workflow reaches for. Deliberately not 2 windows: that is a ratio of exactly
    # `MAX_DURATION_RATIO`, which is ALLOWED, and a test sitting on a declared boundary tests the
    # boundary rather than the rule.
    ir, ids = _pits(tmp_path, events, [(W1, W1), (None, w3)])
    with pytest.raises(IncommensurableError, match="differ by"):
        compare(ir, ids[0], ids[1])


def test_exactly_the_limit_is_allowed_not_refused(tmp_path):
    """The boundary itself, pinned so a later edit to `MAX_DURATION_RATIO` has to face it: the
    check is `>`, so a 2x difference passes and a 2.01x one does not."""
    events = [_flow(W1 + 1, "10.0.1.10", "10.0.1.20"),
              _flow(W2 + 1, "10.0.1.10", "10.0.1.20")]
    ir, ids = _pits(tmp_path, events, [(W1, W1), (None, W2)])
    assert compare(ir, ids[0], ids[1])["commensurable"] is True


def _two_stores(tmp_path, *, a_kw, b_kw):
    """Two PITs from two belief stores, gathered into one IR store so they can be compared."""
    flow = [_flow(W1 + 1, "10.0.1.10", "10.0.1.20")]
    a, ida = _pits(tmp_path, flow, [(W1, W1)], name="a.duckdb", **a_kw)
    b, idb = _pits(tmp_path, flow, [(W1, W1)], name="b.duckdb", **b_kw)
    for pit_id in idb:
        a.write_pit(b.read_pit(pit_id))
    return a, ida[0], idb[0]


def test_different_sources_with_no_declared_network_are_refused(tmp_path):
    """Every difference between two networks is trivially 'drift' — and with neither PIT
    declaring a network, nothing here can tell two networks from one captured twice."""
    ir, a, b = _two_stores(tmp_path, a_kw={"source": "/cap/one"}, b_kw={"source": "/cap/two"})
    with pytest.raises(IncommensurableError, match="different sources"):
        compare(ir, a, b)


# -- D3/D4: network identity and the vantage set ----------------------------------
#
# `docs/WORK_ORDER_visibility_and_vantage.md`, ratified 2026-08-17. The comparator had two
# failure modes before this and the SECOND is the dangerous one, because it answers.

def test_one_tap_re_harvested_into_a_new_directory_now_compares(tmp_path):
    """The falsely-REFUSED half: a filesystem path changed and the network did not."""
    ir, a, b = _two_stores(
        tmp_path,
        a_kw={"source": "/cap/monday", "vantage": "tap-a", "network": "corp"},
        b_kw={"source": "/cap/tuesday", "vantage": "tap-a", "network": "corp"})
    with pytest.raises(IncommensurableError, match="as-of"):
        compare(ir, a, b)   # same instant: refused for THAT reason, not for the source


def test_two_different_taps_on_one_path_are_no_longer_silently_compared(tmp_path):
    """The falsely-ACCEPTED half, and the dangerous one: everything visible from one tap and not
    the other was being reported as network change."""
    ir, a, b = _two_stores(
        tmp_path,
        a_kw={"source": "/cap/shared", "vantage": "tap-a", "network": "corp"},
        b_kw={"source": "/cap/shared", "vantage": "tap-b", "network": "corp"})
    with pytest.raises(IncommensurableError, match="different vantages"):
        compare(ir, a, b)


def test_two_declared_networks_that_differ_are_refused(tmp_path):
    """The declaration decides, and it decides in BOTH directions: it can join two sources, and
    it can separate them."""
    ir, a, b = _two_stores(
        tmp_path,
        a_kw={"source": "/cap/corp", "network": "corp"},
        b_kw={"source": "/cap/lab", "network": "lab"})
    with pytest.raises(IncommensurableError, match="different networks"):
        compare(ir, a, b)


def test_two_pits_that_both_record_no_vantage_still_compare(tmp_path):
    """Nothing knows they differ, which is the state every capture was in before this existed.
    Refusing here would make the tool unusable on an ordinary log directory."""
    ir, ids = _two_windows(tmp_path, [_flow(W2 + 1, "10.0.1.10", "10.0.1.20")])
    assert compare(ir, ids[0], ids[1])["commensurable"] is True


def test_the_report_names_the_view_each_side_is(tmp_path):
    """The fact whose absence let two different taps compare silently."""
    ir, ids = _two_windows(tmp_path, [_flow(W2 + 1, "10.0.1.10", "10.0.1.20")],
                           vantage="tap-a", network="corp")
    report = compare(ir, ids[0], ids[1])
    assert report["from"]["vantages"] == ["tap-a"] and report["to"]["network"] == "corp"


def test_a_pit_compared_with_itself_is_refused(tmp_path):
    """No direction, so no drift."""
    ir, ids = _two_windows(tmp_path, [_flow(W2 + 1, "10.0.1.10", "10.0.1.20")])
    with pytest.raises(IncommensurableError, match="as-of"):
        compare(ir, ids[0], ids[0])


def test_a_differing_schema_major_is_refused(tmp_path):
    """A subtractive change may have removed a field this comparison depends on."""
    ir, ids = _two_windows(tmp_path, [_flow(W2 + 1, "10.0.1.10", "10.0.1.20")])
    ir.con.execute("UPDATE ir_pit SET schema_version = '9.0' WHERE pit_id = ?", [ids[1]])
    with pytest.raises(IncommensurableError, match="schema MAJOR"):
        compare(ir, ids[0], ids[1])


def test_an_unknown_pit_is_refused(tmp_path):
    ir, ids = _two_windows(tmp_path, [_flow(W2 + 1, "10.0.1.10", "10.0.1.20")])
    with pytest.raises(IncommensurableError, match="no PIT"):
        compare(ir, ids[0], "pit-nope-1")


def test_the_duration_limit_is_declared_not_magic():
    assert MAX_DURATION_RATIO >= 1.0 and 0 < COVERAGE_FLOOR <= 1.0


# -- absence classification: D4's whole reason to exist --------------------------

def test_a_host_missing_from_a_well_covered_segment_is_observed_absent(tmp_path):
    """The segment was seen and the host was not. Evidence of absence — never proof: a host
    that talks once a day is absent from a covered three-hour PIT too."""
    ir, ids = _two_windows(tmp_path, [_flow(W2 + 1, "10.0.1.10", "10.0.1.20")])
    removed = compare(ir, ids[0], ids[1])["entities"]["removed"]
    gone = {r["entity_key"]: r for r in removed}
    assert gone["address:10.0.1.30"]["classification"] == "observed_absent"
    assert gone["address:10.0.1.30"]["segment_coverage"] == 1.0


def test_a_host_whose_segment_vanished_entirely_is_not_observed(tmp_path):
    """The comparator must NOT claim these left the network — nothing watched their segment.
    Measured on the real baseline: 4 of 16 removed hosts fell in this class, all in the one
    segment that stopped being observed between the two PITs."""
    ir, ids = _pits(tmp_path, [
        _flow(W1 + 1, "10.0.1.10", "10.0.1.20"),
        _flow(W1 + 2, "10.9.9.5", "10.9.9.6"),       # a segment seen only in window 1
        _flow(W2 + 1, "10.0.1.10", "10.0.1.20"),
    ], [(W1, W1), (W2, W2)])
    gone = {r["entity_key"]: r for r in compare(ir, ids[0], ids[1])["entities"]["removed"]}
    assert gone["address:10.9.9.5"]["classification"] == "not_observed"
    assert gone["address:10.9.9.5"]["segment_coverage"] is None


def test_every_removed_entity_gets_exactly_one_verdict(tmp_path):
    """"We did not classify it" would be a third state nobody asked for."""
    ir, ids = _two_windows(tmp_path, [_flow(W2 + 1, "10.0.1.10", "10.0.1.20")])
    for row in compare(ir, ids[0], ids[1])["entities"]["removed"]:
        assert row["classification"] in ("observed_absent", "not_observed")


# -- partitioning by locality, which is what schema 1.1 bought -------------------

def test_external_peers_are_counted_and_never_itemized(tmp_path):
    """CDN and cloud addresses rotate for reasons that are not network drift; itemizing them
    buries the signal. Measured on the real baseline: 1,963 external changes against 61 local
    ones — a naive diff is 97% noise."""
    ir, ids = _pits(tmp_path, [
        _flow(W1 + 1, "10.0.1.10", "10.0.1.20"),
        _flow(W1 + 2, "10.0.1.10", "203.0.113.9", local_dst=False),
        _flow(W2 + 1, "10.0.1.10", "10.0.1.20"),
        _flow(W2 + 2, "10.0.1.10", "198.51.100.7", local_dst=False),
    ], [(W1, W1), (W2, W2)])
    report = compare(ir, ids[0], ids[1])
    assert report["external_peers"]["added"] == 1
    assert report["external_peers"]["removed"] == 1
    listed = {r["entity_key"] for r in report["entities"]["added"]} | \
             {r["entity_key"] for r in report["entities"]["removed"]}
    assert not any(k.startswith("address:203.") or k.startswith("address:198.") for k in listed)


# -- what counts as a change ------------------------------------------------------

def test_only_the_declared_fields_count_as_drift(tmp_path):
    """`windows_seen`, `first_seen`/`last_seen` and `source_entity` all differ between two PITs
    of the same stable network — the first two move with the window range, and the third is
    run-scoped by `Q1` D3. Diffing every column would report all of that as change."""
    ir, ids = _pits(tmp_path, [
        _flow(W1 + 1, "10.0.1.10", "10.0.1.20"),
        _flow(W2 + 1, "10.0.1.10", "10.0.1.20"),
    ], [(W1, W1), (W2, W2)])
    report = compare(ir, ids[0], ids[1])
    assert report["entities"]["changed"] == []
    assert report["entities"]["unchanged"] == 2


def test_a_role_change_is_reported_with_both_sides(tmp_path):
    ir, ids = _pits(tmp_path, [
        _flow(W1 + 1, "10.0.1.10", "10.0.1.20", dport=88),
        _flow(W1 + 2, "10.0.1.10", "10.0.1.20", dport=389),
        _flow(W2 + 1, "10.0.1.10", "10.0.1.20", dport=9999),
    ], [(W1, W1), (W2, W2)])
    changed = {c["entity_key"]: c for c in compare(ir, ids[0], ids[1])["entities"]["changed"]}
    assert changed["address:10.0.1.20"]["changes"]["role"] == \
        {"from": "domain-controller", "to": None}


# -- caveats: weaken, never invalidate -------------------------------------------

def test_weak_address_joins_are_disclosed(tmp_path):
    """The honest limit of the whole report on a DHCP network."""
    ir, ids = _two_windows(tmp_path, [_flow(W2 + 1, "10.0.1.10", "10.0.1.20")])
    assert any("address` key" in c for c in compare(ir, ids[0], ids[1])["caveats"])


def test_a_build_change_is_a_caveat_not_a_refusal(tmp_path):
    """`LES-113`: entity keys derive from the resolver's normalization, so a build change may
    make hosts appear replaced rather than persist. Worth flagging, not worth refusing — the
    comparison is still the best available answer."""
    ir, ids = _two_windows(tmp_path, [_flow(W2 + 1, "10.0.1.10", "10.0.1.20")])
    ir.con.execute("UPDATE ir_pit SET code_fingerprint = 'deadbeef' WHERE pit_id = ?", [ids[1]])
    report = compare(ir, ids[0], ids[1])
    assert report["commensurable"] is True
    assert any("different builds" in c for c in report["caveats"])


# -- determinism ------------------------------------------------------------------

def test_two_comparisons_of_one_pair_are_identical(tmp_path):
    """A drift report that varied would be indistinguishable from drift."""
    ir, ids = _two_windows(tmp_path, [_flow(W2 + 1, "10.0.1.10", "10.0.1.20")])
    assert compare(ir, ids[0], ids[1]) == compare(ir, ids[0], ids[1])


def test_every_list_in_the_report_is_sorted(tmp_path):
    ir, ids = _pits(tmp_path, [
        _flow(W1 + 1, "10.0.1.10", "10.0.1.20"),
        _flow(W2 + 1, "10.0.1.99", "10.0.1.20"),
        _flow(W2 + 2, "10.0.1.11", "10.0.1.20"),
    ], [(W1, W1), (W2, W2)])
    report = compare(ir, ids[0], ids[1])
    added = [r["entity_key"] for r in report["entities"]["added"]]
    assert added == sorted(added)
    assert report["segments"]["added"] == sorted(report["segments"]["added"])


# -- the `since` bound step 5 proved necessary ------------------------------------

def test_a_bounded_pit_excludes_windows_outside_its_span(tmp_path):
    """Without `since`, every PIT is cumulative, so two from one store have nested extents and
    are always refused. This is the gap build step 5 exposed."""
    ir, ids = _pits(tmp_path, [
        _flow(W1 + 1, "10.0.1.10", "10.0.1.20"),
        _flow(W2 + 1, "10.0.2.10", "10.0.2.20"),
    ], [(W1, W1), (W2, W2)])
    first, second = ir.read_pit(ids[0]), ir.read_pit(ids[1])
    assert {e["entity_key"] for e in first["ir_entity"]} == \
        {"address:10.0.1.10", "address:10.0.1.20"}
    assert {e["entity_key"] for e in second["ir_entity"]} == \
        {"address:10.0.2.10", "address:10.0.2.20"}


def test_two_bounded_pits_of_equal_length_are_commensurable(tmp_path):
    ir, ids = _two_windows(tmp_path, [_flow(W2 + 1, "10.0.1.10", "10.0.1.20")])
    assert compare(ir, ids[0], ids[1])["commensurable"] is True
