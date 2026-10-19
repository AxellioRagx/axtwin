"""Combining PITs across vantages, and across time (D4/D10, build steps 4 and 8).

Synthetic events only (`LES-003`).

The property this file is really defending is that **the axis is not a formality**: across taps the
same window is seen twice, across spans it is seen once, and a merge that got that backwards would
produce numbers that look entirely reasonable and are wrong by a factor of the tap count. The
strongest single check is `test_a_time_merge_covers_everything_the_combined_span_materialize_finds`
— a real union has an independently computable answer, and this compares against it.
"""
import json

import pytest

from aximprint.events import FlowEvent
from aximprint.ir.compare import IncommensurableError, compare
from aximprint.ir.materialize import materialize
from aximprint.ir.merge import MergeError, merge
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


def _store(tmp_path, events, *, source, vantage=None, network=None, name=None):
    st = BeliefStore(str(tmp_path / (name or f"{source.replace('/', '_')}.duckdb")))
    wm = WindowManager(window_seconds=WINDOW)
    wins = list(wm.process(iter(events))) + list(wm.finalize())
    for w in wins:
        st.write_window(w, extract_assertions(w))
    st.write_entity_roster(source, wm.entity_roster())
    st.write_run({"source": source, "plugin": "zeek", "window_seconds": WINDOW,
                  "subbuckets": 12, "vantage": vantage, "network": network},
                 build_stamp(), len(wins), W1, W1 + 1)
    return st


def _write(ir, store, **kw):
    pit = materialize(store, **kw)
    pit.pop("_stats")
    ir.write_pit(pit)
    return pit["ir_pit"][0]["pit_id"]


def _two_vantages(tmp_path, *, network="corp", vantage_b="tap-b"):
    """Two taps on one network: A sees .10<->.20, B sees .20<->.30. One host in common."""
    ir = IRStore(str(tmp_path / "ir.duckdb"))
    a = _store(tmp_path, [_flow(W1 + 1, "10.0.1.10", "10.0.1.20")],
               source="/cap/a", vantage="tap-a", network=network)
    b = _store(tmp_path, [_flow(W1 + 2, "10.0.1.30", "10.0.1.20")],
               source="/cap/b", vantage=vantage_b, network=network)
    return ir, _write(ir, a), _write(ir, b)


def _rows(ir, pit_id, table):
    return ir.read_pit(pit_id)[table]


# -- the vantage axis --------------------------------------------------------------

def test_a_vantage_merge_unions_the_entities(tmp_path):
    ir, a, b = _two_vantages(tmp_path)
    out = merge(ir, [a, b], "vantage")
    keys = {e["entity_key"] for e in out["ir_entity"]}
    assert {"address:10.0.1.10", "address:10.0.1.20", "address:10.0.1.30"} <= keys


def test_each_entity_records_which_taps_saw_it(tmp_path):
    """The column multi-tap exists for: "what does B see that A does not" is a question about
    this list, and a merged PIT without it could not answer it at all."""
    ir, a, b = _two_vantages(tmp_path)
    out = merge(ir, [a, b], "vantage")
    seen = {e["entity_key"]: json.loads(e["vantages"]) for e in out["ir_entity"]}
    assert seen["address:10.0.1.20"] == ["tap-a", "tap-b"]   # the shared host
    assert seen["address:10.0.1.10"] == ["tap-a"]
    assert seen["address:10.0.1.30"] == ["tap-b"]


def test_a_merged_pit_names_its_axis_and_its_inputs(tmp_path):
    ir, a, b = _two_vantages(tmp_path)
    row = merge(ir, [a, b], "vantage")["ir_pit"][0]
    assert row["merge_axis"] == "vantage"
    assert json.loads(row["merged_from"]) == sorted([a, b])
    assert json.loads(row["vantages"]) == ["tap-a", "tap-b"]


def test_a_merged_pit_leaves_the_scalar_vantage_null(tmp_path):
    """"This PIT is tap A" and "this PIT contains tap A among others" are different claims, and
    a comparator must not read the second as the first."""
    ir, a, b = _two_vantages(tmp_path)
    assert merge(ir, [a, b], "vantage")["ir_pit"][0]["vantage"] is None


def test_the_merged_id_is_distinct_from_every_contributors(tmp_path):
    """On the time axis every contributor shares one source, so an id derived from
    `(source, as_of)` would land on top of the latest input."""
    ir, a, b = _two_vantages(tmp_path)
    assert merge(ir, [a, b], "vantage")["ir_pit"][0]["pit_id"] not in {a, b}


def test_a_merge_is_deterministic(tmp_path):
    """A comparator subtracts two PITs, so any instability reads as drift (`LES-002`)."""
    ir, a, b = _two_vantages(tmp_path)
    first, second = merge(ir, [a, b], "vantage"), merge(ir, [a, b], "vantage")
    for table in ("ir_entity", "ir_segment", "ir_edge", "ir_identity", "ir_unseen"):
        assert first[table] == second[table], table


def test_edge_volumes_take_the_max_across_taps_never_the_sum(tmp_path):
    """Two taps on the path of one conversation both report it; adding their counts would double
    the traffic on every link the taps share — the most misleading number a multi-tap view could
    produce."""
    ir = IRStore(str(tmp_path / "ir.duckdb"))
    both = [_flow(W1 + 1, "10.0.1.10", "10.0.1.20")]
    a = _write(ir, _store(tmp_path, both, source="/cap/a", vantage="tap-a", network="corp"))
    b = _write(ir, _store(tmp_path, both, source="/cap/b", vantage="tap-b", network="corp"))
    edges = merge(ir, [a, b], "vantage")["ir_edge"]
    assert [e["flows"] for e in edges] == [1], "one flow seen twice is still one flow"


def test_a_vantage_merge_without_a_declared_network_is_refused(tmp_path):
    """Nothing in traffic identifies a network (D3), so without the declaration this would be
    unioning two arbitrary networks into a fiction."""
    ir = IRStore(str(tmp_path / "ir.duckdb"))
    a = _write(ir, _store(tmp_path, [_flow(W1 + 1, "10.0.1.10", "10.0.1.20")],
                          source="/cap/a", vantage="tap-a"))
    b = _write(ir, _store(tmp_path, [_flow(W1 + 2, "10.0.1.30", "10.0.1.20")],
                          source="/cap/b", vantage="tap-b"))
    with pytest.raises(MergeError, match="SAME network"):
        merge(ir, [a, b], "vantage")


def test_one_tap_appearing_twice_is_refused_on_the_vantage_axis(tmp_path):
    """Its coverage would be counted as if a second tap had confirmed it. Two spans from one tap
    are a `time` merge."""
    ir, a, b = _two_vantages(tmp_path, vantage_b="tap-a")
    with pytest.raises(MergeError, match="more than one contributing PIT"):
        merge(ir, [a, b], "vantage")


def test_a_pit_with_no_vantage_cannot_be_vantage_merged(tmp_path):
    ir = IRStore(str(tmp_path / "ir.duckdb"))
    a = _write(ir, _store(tmp_path, [_flow(W1 + 1, "10.0.1.10", "10.0.1.20")],
                          source="/cap/a", vantage="tap-a", network="corp"))
    b = _write(ir, _store(tmp_path, [_flow(W1 + 2, "10.0.1.30", "10.0.1.20")],
                          source="/cap/b", network="corp"))
    with pytest.raises(MergeError, match="name its vantage"):
        merge(ir, [a, b], "vantage")


# -- the time axis -----------------------------------------------------------------

def _two_spans(tmp_path, *, source="/cap/one", vantage="tap-a"):
    """One tap, two adjacent windows, materialized as two non-overlapping PITs."""
    events = [_flow(W1 + 1, "10.0.1.10", "10.0.1.20"),
              _flow(W2 + 1, "10.0.1.10", "10.0.1.20"),
              _flow(W2 + 2, "10.0.1.40", "10.0.1.20")]      # appears only in window 2
    st = _store(tmp_path, events, source=source, vantage=vantage, network="corp")
    ir = IRStore(str(tmp_path / "ir.duckdb"))
    return ir, st, _write(ir, st, since=W1, as_of=W1), _write(ir, st, since=W2, as_of=W2)


def test_a_time_merge_accumulates_windows(tmp_path):
    """Across disjoint spans the windows add — the denominator of every occupancy figure in the
    combined PIT, so getting it wrong rescales `windows_seen` for every host."""
    ir, _st, one, two = _two_spans(tmp_path)
    assert merge(ir, [one, two], "time")["ir_pit"][0]["windows"] == 2


def test_a_time_merge_sums_edge_volumes(tmp_path):
    ir, _st, one, two = _two_spans(tmp_path)
    edges = {(e["src_key"], e["dst_key"]): e for e in merge(ir, [one, two], "time")["ir_edge"]}
    assert edges[("address:10.0.1.10", "address:10.0.1.20")]["flows"] == 2


def test_a_time_merge_covers_everything_the_combined_span_materialize_finds(tmp_path):
    """**D10's own acceptance check, and the measurement that made "preferred" a fact.**

    A merged PIT's membership is a SUPERSET of the span materialize's, never a subset: the merge
    combines rows that were already KEYED, so it cannot re-key them, while entity resolution works
    across the seam the merge cannot see. Measured on the 2026-08-11 baseline — 3,237 entities
    merged against 3,231 materialized, the six extras being hosts whose hostname or MAC evidence
    fell in only one of the two windows (`10.80.20.178` -> `hostname:idrac-c28pf33`, and five
    like it). It fragments identity; it never loses a host.

    On this fixture, which carries no host-scoped identity evidence at all, the two are equal —
    asserted as well, because that equality is what proves the union ARITHMETIC right and isolates
    the difference above to resolution rather than to a counting bug."""
    ir, st, one, two = _two_spans(tmp_path)
    merged = merge(ir, [one, two], "time")
    whole = materialize(st, since=W1, as_of=W2)
    merged_keys = {e["entity_key"] for e in merged["ir_entity"]}
    whole_keys = {e["entity_key"] for e in whole["ir_entity"]}
    assert merged_keys >= whole_keys, "a combined view must never lose a host"
    assert merged_keys == whole_keys, "no cross-seam identity evidence in this fixture"
    assert merged["ir_pit"][0]["windows"] == whole["ir_pit"][0]["windows"]


def test_overlapping_spans_are_refused(tmp_path):
    """THE refusal of this axis: every window in the overlap would be counted twice, inflating
    edge volumes and occupancy into a busier network than the one observed."""
    ir, st, one, _two = _two_spans(tmp_path)
    both = _write(ir, st, since=W1, as_of=W2)
    with pytest.raises(MergeError, match="overlap"):
        merge(ir, [one, both], "time")


def test_a_time_merge_across_two_vantages_is_refused(tmp_path):
    """Combine the vantages first, then the spans — otherwise a view difference is absorbed into
    a time series where nothing can see it."""
    ir, a, b = _two_vantages(tmp_path)
    with pytest.raises(MergeError, match="one vantage"):
        merge(ir, [a, b], "time")


# -- the census correction, which is what combining views is FOR --------------------

def test_a_host_one_tap_only_referenced_stops_being_unseen_when_another_saw_it(tmp_path):
    """The whole value of the combination. Leaving it in the census would report a blind spot
    the merged vantage does not have."""
    ir = IRStore(str(tmp_path / "ir.duckdb"))
    a = _store(tmp_path, [_flow(W1 + 1, "10.0.1.10", "10.0.1.20")],
               source="/cap/a", vantage="tap-a", network="corp")
    # Tap A resolves a name to 10.0.1.30 and never sees it; tap B watches it send traffic.
    a.con.execute(
        "INSERT INTO identity_bindings VALUES (?, 'opened', 'dns', '10.0.1.30', 'host30.corp', "
        "'dnsname', 'dnsname:host30.corp', 'referenced', '10.0.1.30', ?, NULL, ?, ?, NULL, 60, 3)",
        [W1, W1, W1, W1])
    a_id = _write(ir, a)
    b_id = _write(ir, _store(tmp_path, [_flow(W1 + 2, "10.0.1.30", "10.0.1.20")],
                             source="/cap/b", vantage="tap-b", network="corp"))
    assert {r["address"] for r in _rows(ir, a_id, "ir_unseen")} == {"10.0.1.30"}
    out = merge(ir, [a_id, b_id], "vantage")
    assert not out["ir_unseen"], "tap B observed it, so the combined view is not blind to it"
    assert out["_stats"]["unseen_resolved_by_merge"] == 1


# -- and the comparator's own gate -------------------------------------------------

def test_drift_refuses_a_one_tap_pit_against_a_merged_one(tmp_path):
    """A view change wearing drift's clothes: everything the second tap sees would read as
    added. Refusing it is the same discipline as the duration limit (D4)."""
    ir, a, b = _two_vantages(tmp_path)
    merged = merge(ir, [a, b], "vantage")
    ir.write_pit({k: v for k, v in merged.items() if k != "_stats"})
    with pytest.raises(IncommensurableError, match="different vantages"):
        compare(ir, a, merged["ir_pit"][0]["pit_id"])


# -- found in self-review, so pinned ------------------------------------------------

def test_the_result_does_not_depend_on_the_order_the_pits_were_named(tmp_path):
    """A union is commutative, and the artifact must be too. Every tie-break below
    (strongest segment, best coverage) would otherwise resolve by which contributor was read
    first, and a comparator subtracting two PITs reads any instability as drift (`LES-002`)."""
    ir, a, b = _two_vantages(tmp_path)
    forward, backward = merge(ir, [a, b], "vantage"), merge(ir, [b, a], "vantage")
    for table in ("ir_pit", "ir_entity", "ir_segment", "ir_edge", "ir_identity", "ir_unseen"):
        assert forward[table] == backward[table], table


def test_two_sources_with_no_declared_network_cannot_be_time_merged(tmp_path):
    """The check first read `len(networks) > 1`, which PASSES when every PIT declares nothing
    at all (`{None}` has length one) — exactly the case that cannot be justified."""
    ir = IRStore(str(tmp_path / "ir.duckdb"))
    a = _write(ir, _store(tmp_path, [_flow(W1 + 1, "10.0.1.10", "10.0.1.20")], source="/cap/a"))
    b = _write(ir, _store(tmp_path, [_flow(W2 + 1, "10.0.1.10", "10.0.1.20")], source="/cap/b"))
    with pytest.raises(MergeError, match="different sources and declare no common network"):
        merge(ir, [a, b], "time")


def test_two_declared_networks_cannot_be_time_merged(tmp_path):
    """Spans of two different networks are not one network's history."""
    ir = IRStore(str(tmp_path / "ir.duckdb"))
    a = _write(ir, _store(tmp_path, [_flow(W1 + 1, "10.0.1.10", "10.0.1.20")],
                          source="/cap/a", network="corp"))
    b = _write(ir, _store(tmp_path, [_flow(W2 + 1, "10.0.1.10", "10.0.1.20")],
                          source="/cap/b", network="lab"))
    with pytest.raises(MergeError, match="not one network's history"):
        merge(ir, [a, b], "time")


# -- evidence ranking: ONE table, not two (`docs/WORK_ORDER_reference_ingest.md` D1) -------


def test_the_merge_ranks_evidence_by_the_schema_vocabulary_not_its_own_table():
    """This module declared `_EVIDENCE_RANK = ("observed", "referenced", "imputed")` until
    2026-09-04 — a SECOND rank table, and exactly the drift D1 made `EVIDENCE_CLASSES` an
    ordered tuple to prevent (`LES-101`).

    It had already gone wrong at the moment of the widening: `asserted` and `imported` rank
    ABOVE `observed`, and the old table's `else 99` fallback ranked both BELOW `imputed` —
    inverting the model on every merged PIT, invisibly, because nothing produces those classes
    yet. This is the pin that a future class cannot repeat it."""
    from aximprint.ir.merge import _strongest

    assert _strongest(["imputed", "asserted"]) == "asserted"
    assert _strongest(["observed", "imported"]) == "imported"
    assert _strongest(["referenced", "observed"]) == "observed"
    assert _strongest(["imputed", "referenced"]) == "referenced"
    assert _strongest([None, "", "imputed"]) == "imputed"
    assert _strongest([None, ""]) is None


def test_an_unrankable_evidence_class_refuses_the_merge_rather_than_sorting_last():
    """Fail closed (CONVENTIONS §3). Ranking an unknown class last is a CLAIM — that the reading
    nobody here understands is the weakest one available — and this merge cannot compute an
    honest `evidence_class` for a value it cannot place (`LES-006`)."""
    from aximprint.ir.merge import MergeError, _strongest

    with pytest.raises(MergeError, match="not in this build's vocabulary"):
        _strongest(["observed", "hearsay"])
