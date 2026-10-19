"""The visibility report — what a vantage saw, and who it knows it missed (D7, build step 7).

Synthetic events only (`LES-003`). Nothing here infers anything: the report is two things the IR
already carries, read as a visibility signal rather than as an absence gate. DESIGN_BRIEF §8's
actual output — a tap-placement recommendation — is a report OVER this one and is out of scope,
which is itself worth pinning so nobody quietly grows one here.
"""
from aximprint.events import FlowEvent
from aximprint.ir.compare import COVERAGE_FLOOR
from aximprint.ir.materialize import materialize
from aximprint.ir.store import IRStore
from aximprint.ir.visibility import report
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


def _reported(tmp_path, events, referenced=(), **run_meta):
    st = BeliefStore(str(tmp_path / "s.duckdb"))
    wm = WindowManager(window_seconds=WINDOW)
    wins = list(wm.process(iter(events))) + list(wm.finalize())
    for w in wins:
        st.write_window(w, extract_assertions(w))
    for address, name in referenced:
        st.con.execute(
            "INSERT INTO identity_bindings VALUES (?, 'opened', 'dns', ?, ?, 'dnsname', ?, "
            "'referenced', ?, ?, NULL, ?, ?, NULL, 60, 7)",
            [W1, address, name, f"dnsname:{name}", address, W1, W1, W1])
    st.write_entity_roster("/cap/one", wm.entity_roster())
    st.write_run({"source": "/cap/one", "plugin": "zeek", "window_seconds": WINDOW,
                  "subbuckets": 12, **run_meta}, build_stamp(), len(wins), W1, W1 + 1)
    pit = materialize(st)
    pit.pop("_stats")
    ir = IRStore(str(tmp_path / "ir.duckdb"))
    ir.write_pit(pit)
    return report(ir, pit["ir_pit"][0]["pit_id"])


_TWO_WINDOWS = [
    _flow(W1 + 1, "10.0.1.10", "10.0.1.20"),
    _flow(W2 + 1, "10.0.1.10", "10.0.1.20"),
    _flow(W1 + 2, "10.0.2.10", "10.0.2.20"),      # a segment seen in window 1 only
]


def test_the_report_names_the_vantage_it_is_about(tmp_path):
    out = _reported(tmp_path, _TWO_WINDOWS, vantage="tap-a", network="corp")
    assert out["pit"]["vantages"] == ["tap-a"] and out["pit"]["network"] == "corp"


def test_segments_are_ordered_worst_covered_first(tmp_path):
    """The useful end of a coverage report is the badly-covered end."""
    out = _reported(tmp_path, _TWO_WINDOWS)
    coverage = [s["coverage"] for s in out["segments"]]
    assert coverage == sorted(coverage)
    assert out["segments"][0]["cidr"] == "10.0.2.0/24"    # 1 of 2 windows


def test_a_segment_below_the_floor_is_flagged_with_the_same_floor_compare_uses(tmp_path):
    """Published so the two agree by construction: this is where absence stops being
    interpretable, and an operator should see it coming."""
    out = _reported(tmp_path, _TWO_WINDOWS + [_flow(W1 + 3, "10.0.3.10", "10.0.3.20")])
    thin = next(s for s in out["segments"] if s["cidr"] == "10.0.3.0/24")
    assert thin["coverage"] == 0.5
    assert thin["below_floor"] is (0.5 < COVERAGE_FLOOR)


def test_coverage_is_labelled_as_inferred_never_measured(tmp_path):
    """Zeek reports no per-segment loss, so every figure here is a proxy (`LES-006`)."""
    out = _reported(tmp_path, _TWO_WINDOWS)
    assert {s["coverage_source"] for s in out["segments"]} == {"inferred_from_traffic"}


def test_the_unseen_census_itemizes_only_what_is_inside_an_observed_segment(tmp_path):
    """9 of 2,418 on the real baseline. Listing every CDN address a resolver ever answered with
    would bury the ones that are actually a blind spot in this estate."""
    out = _reported(tmp_path, _TWO_WINDOWS,
                    referenced=[("10.0.1.99", "ghost.corp"), ("93.184.216.34", "example.com")])
    assert out["unseen"]["total"] == 2
    assert [h["address"] for h in out["unseen"]["in_observed_segments"]] == ["10.0.1.99"]
    assert out["unseen"]["by_locality"] == {"external": 1, "local": 1}
    assert out["unseen"]["by_reason"] == {"referenced_by_dns": 1 + 1}


def test_the_report_declines_to_recommend_anything(tmp_path):
    """§8's actionable output is a report over this one, deliberately not made here — stated in
    the artifact so nobody grows one by accident."""
    out = _reported(tmp_path, _TWO_WINDOWS)
    assert "not made here" in out["note"]
    assert "recommend" not in {k for k in out}
