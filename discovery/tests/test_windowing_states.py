"""Window accumulator: normalized conn-state coverage + health honesty.

Synthetic events only (`LES-003`); the shapes mirror what a real capture's
conn_state mix looks like, never its contents.
"""
import pytest

from aximprint.events import (
    AlertEvent, FlowEvent, IdentityEvent, SensorHealthEvent, ServiceEvent,
)
from aximprint.learners import extract_assertions
from aximprint.windowing import WindowManager
from aximprint.windowing.windows import TOP_PEERS

T0 = 1_754_000_000.0
WINDOW, SUBBUCKETS = 3600, 12
ALIGNED = T0 - (T0 % WINDOW)          # windows are aligned; boundary tests say so out loud
SUBBUCKET_SECONDS = WINDOW / SUBBUCKETS


def _flow(ts, state, klass, src="10.0.0.21", dst="10.0.0.5", dport=445, proto="tcp"):
    return FlowEvent(ts=ts, src_ip=src, dst_ip=dst, src_port=51000, dst_port=dport,
                     proto=proto, service="", duration=1.0,
                     bytes_out=100, bytes_in=200, pkts_out=2, pkts_in=2,
                     state=state, state_class=klass)


def _open(window_start=ALIGNED, **kw):
    """A manager with a window already open, for driving `_add` directly.

    Used where the pathology is *within-window* disorder: `process()` would open a
    new window before the accumulator ever saw the out-of-order event.
    """
    wm = WindowManager(**kw)
    wm._cur_start = window_start
    return wm


def _one_window(events, **kw):
    wm = WindowManager(**kw)
    wins = list(wm.process(iter(events))) + list(wm.finalize())
    assert len(wins) == 1, "fixture must stay inside one window"
    return wins[0]


def test_reset_flows_still_prove_a_listener():
    """A connection that established and was later reset had a real listener.

    v0 gated `listen_ports` on `state == "SF"`, so the reset states — ~40% of
    flows on a real capture — never registered a service.
    """
    win = _one_window([
        _flow(T0 + 1, "RSTR", "reset_resp"),
        _flow(T0 + 2, "RSTO", "reset_orig"),
    ])
    assert win.nodes["10.0.0.5"]["listen_ports"] == ["445/tcp"]


def test_refused_flows_do_not_prove_a_listener():
    """`REJ` proves the host is up, not that the port is open."""
    win = _one_window([_flow(T0 + 1, "REJ", "refused", dport=23)])
    assert win.nodes["10.0.0.5"]["listen_ports"] == []


def test_state_mix_covers_every_flow():
    states = [("SF", "established"), ("RSTR", "reset_resp"), ("RSTR", "reset_resp"),
              ("RSTO", "reset_orig"), ("S0", "attempted"), ("REJ", "refused"),
              ("OTH", "other"), ("WAT", "unknown")]
    win = _one_window([_flow(T0 + i, s, k) for i, (s, k) in enumerate(states)])
    node = win.nodes["10.0.0.21"]
    assert node["state_mix"] == {"attempted": 1, "established": 1, "other": 1,
                                 "refused": 1, "reset_orig": 1, "reset_resp": 2,
                                 "unknown": 1}
    assert sum(node["state_mix"].values()) == node["flows_out"]
    assert list(node["state_mix"]) == sorted(node["state_mix"]), "sorted -> byte-stable"


#: Every sensor vocabulary this repo has a plugin for, as (state, state_class) pairs that all
#: mean the same two things: an unanswered attempt and a refusal. Zeek's are its `conn_state`
#: spellings; axquery's `state` is a raw cumulative-TCP-flag pair (`ingest/axquery/plugin.py
#: ::_state_class`); Suricata's is flag-derived the same way. Only the FIRST row satisfies a
#: `state == "S0"` test, which is exactly what `Q34` was.
_ATTEMPTED_REFUSED_BY_SENSOR = {
    "zeek": (("S0", "attempted"), ("REJ", "refused")),
    "axquery": (("O:2/R:0", "attempted"), ("O:2/R:20", "refused")),
    "suricata": (("new", "attempted"), ("closed", "refused")),
}


#: Every name a scalar "count of attempted/unanswered" or "count of refused" flows has been
#: published under, or plausibly would be, mapped to the `FLOW_STATE_CLASSES` key that IS the
#: fact. Nothing emits these today (`Q34` deleted the two that did); the table exists so the
#: consistency check below keeps biting if one ever comes back under any of these names.
_HYGIENE_ALIASES = {"s0_count": "attempted", "attempted_count": "attempted",
                    "rej_count": "refused", "refused_count": "refused"}


@pytest.mark.parametrize("sensor", sorted(_ATTEMPTED_REFUSED_BY_SENSOR))
def test_hygiene_counts_are_keyed_on_the_normalized_class_not_the_sensor_word(sensor):
    """`Q34`: the published hygiene count must be right for a NON-Zeek state vocabulary.

    Before 2026-08-26 the bundle carried `s0_count`/`rej_count`, incremented on
    `ev.state == "S0"` / `"REJ"` — the sensor's own untranslated word. Both axquery rows
    below classify correctly and would still have published `s0_count: 0` / `rej_count: 0`,
    a confident "no such flows" where two exist (`LES-006`). Measured cost of exactly that,
    on the live axquery window of CHANGELOG 2026-08-20: **2,037 attempted flows, published
    as `s0_count: 0`** for five days.
    """
    (att_state, att_class), (rej_state, rej_class) = _ATTEMPTED_REFUSED_BY_SENSOR[sensor]
    win = _one_window([_flow(T0 + 1, att_state, att_class),
                       _flow(T0 + 2, att_state, att_class),
                       _flow(T0 + 3, rej_state, rej_class)])
    node = win.nodes["10.0.0.21"]
    assert node["state_mix"]["attempted"] == 2
    assert node["state_mix"]["refused"] == 1
    # The defect itself, stated so it holds under EITHER repair: no field this row publishes
    # may claim a count of attempted/refused flows that disagrees with the class census in the
    # same row. Against the pre-fix build this fails on `axquery` and `suricata` — `s0_count`
    # reads 0 beside `state_mix["attempted"] == 2` — and passes on `zeek`, which is the true
    # shape of `Q34`: nothing was ever wrong on the Zeek path.
    for alias, klass in _HYGIENE_ALIASES.items():
        if alias in node:
            assert node[alias] == node["state_mix"].get(klass, 0), (
                f"{alias} disagrees with state_mix[{klass!r}] on a {sensor} vocabulary")


@pytest.mark.parametrize("sensor", sorted(_ATTEMPTED_REFUSED_BY_SENSOR))
def test_no_hygiene_counter_is_named_for_one_sensors_spellings(sensor):
    """`Q34`, the other half: the Zeek-named scalars are GONE, not repaired.

    Deleted rather than renamed because a `state_class`-keyed scalar would duplicate a key
    `state_mix` already carries in the same row, under a vocabulary `features/vocabulary.py`
    declares CLOSED — so `attempted`/`refused` are guaranteed columns either way. This guard
    is what stops the field coming back, in Zeek spelling or in normalized spelling.
    """
    (att_state, att_class), _ = _ATTEMPTED_REFUSED_BY_SENSOR[sensor]
    node = _one_window([_flow(T0 + 1, att_state, att_class)]).nodes["10.0.0.21"]
    for gone in _HYGIENE_ALIASES:
        assert gone not in node, f"{gone} duplicates state_mix (`Q34`)"


def test_late_flow_is_clamped_and_counted():
    """A flow behind the open window must not wrap into a negative sub-bucket index."""
    wm = WindowManager()
    events = [_flow(T0 + 600, "SF", "established"),      # opens window A
              _flow(T0 + 4000, "SF", "established"),     # closes A, opens B
              _flow(T0 + 700, "SF", "established")]      # belongs to A; arrives late
    wins = list(wm.process(iter(events))) + list(wm.finalize())
    assert len(wins) == 2
    assert wins[0].health["late_events"] == 0
    assert wins[1].health["late_events"] == 1
    flows = wins[1].nodes["10.0.0.21"]["subbucket_flows"]
    assert sum(flows) == 2 and all(n >= 0 for n in flows)


def test_flow_exactly_on_the_boundary_opens_the_next_window():
    """`ts == window_end` is the next window's first instant, not this one's last."""
    wm = WindowManager(window_seconds=WINDOW, subbuckets=SUBBUCKETS)
    events = [_flow(ALIGNED, "SF", "established"),                 # first instant of A
              _flow(ALIGNED + WINDOW - 0.001, "SF", "established"),  # last instant of A
              _flow(ALIGNED + WINDOW, "SF", "established")]        # first instant of B
    wins = list(wm.process(iter(events))) + list(wm.finalize())

    assert [w.window_start for w in wins] == [ALIGNED, ALIGNED + WINDOW]
    assert wins[0].nodes["10.0.0.21"]["subbucket_flows"] == [1] + [0] * 10 + [1]
    assert wins[1].nodes["10.0.0.21"]["subbucket_flows"] == [1] + [0] * 11
    assert all(w.health["late_events"] == 0 for w in wins), "on-boundary is not late"


def test_late_flow_clamps_at_both_ends():
    """Both arms of the clamp, and both counted.

    The upper arm is unreachable through `process()` today — an event past the end
    opens the next window before `_add` sees it — so it is driven directly. It
    is the defensive half of one guard, and a silent wrap there would corrupt
    burstiness exactly as a negative index would.
    """
    wm = WindowManager(window_seconds=WINDOW, subbuckets=SUBBUCKETS)
    wm._cur_start = ALIGNED
    wm._add(_flow(ALIGNED - WINDOW, "SF", "established"))       # a window early
    wm._add(_flow(ALIGNED + 2 * WINDOW, "SF", "established"))   # two windows late

    acc = wm._nodes["10.0.0.21"]
    assert wm._late == 2
    assert acc.subbucket_flows == [1] + [0] * 10 + [1], "clamped to the ends, never wrapped"
    assert all(n >= 0 for n in acc.subbucket_flows)


def test_accumulating_before_a_window_opens_fails_loud():
    """No open window is a programming error, not something to default through."""
    wm = WindowManager()
    with pytest.raises(RuntimeError, match="before a window was opened"):
        wm._add(_flow(T0, "SF", "established"))


def test_flow_less_than_one_subbucket_late_is_still_counted():
    """Lateness is measured against the window start, not against a sub-bucket."""
    wm = WindowManager(window_seconds=WINDOW, subbuckets=SUBBUCKETS)
    lag = SUBBUCKET_SECONDS / 2          # 150s: unambiguously behind window B's start
    events = [_flow(ALIGNED + 10, "SF", "established"),               # opens window A
              _flow(ALIGNED + WINDOW + 10, "SF", "established"),      # closes A, opens B
              _flow(ALIGNED + WINDOW - lag, "SF", "established")]     # belongs to A
    wins = list(wm.process(iter(events))) + list(wm.finalize())

    assert len(wins) == 2
    assert wins[1].health["late_events"] == 1


# -- within-window disorder (the bounded ingest reorder permits it) --------------

def test_out_of_order_flow_never_records_a_negative_interarrival():
    """A flow behind its source's last flow must not poison the quantile sketch.

    `interarrival_q.add(ev.ts - last_flow_ts)` went negative under within-window
    disorder AND moved the node's clock backwards, so the *next* reading was wrong
    too — with `late_events` reading 0, because the flow was inside the window.
    """
    wm = _open()
    for offset in (100.0, 900.0, 200.0, 1000.0):   # 200 arrives behind 900
        wm._add(_flow(ALIGNED + offset, "SF", "established"))
    (win,) = list(wm.finalize())

    q = win.nodes["10.0.0.21"]["interarrival_q"]
    assert all(v >= 0 for v in q), f"negative interarrival in {q}"
    # 100->900 is 800s; the out-of-order flow is skipped and does NOT rewind the
    # clock, so 900->1000 reads 100s rather than the 800s a rewound clock gives.
    assert q == [100.0, 800.0, 800.0, 800.0]
    assert win.health["interarrival_skipped"] == 1
    assert win.health["late_events"] == 0, "in-window disorder is not lateness"


def test_edge_interval_is_never_inverted_by_out_of_order_flows():
    """`first_ts`/`last_ts` are an interval, so both ends move by min/max.

    Fixing `first_ts` at creation and overwriting `last_ts` produced edges with
    `last_ts < first_ts` — a negative-duration conversation — which the store and
    the synthesizer accept without complaint.
    """
    wm = _open()
    wm._add(_flow(ALIGNED + 900, "SF", "established"))
    wm._add(_flow(ALIGNED + 100, "SF", "established"))
    (win,) = list(wm.finalize())

    (edge,) = win.edges
    assert edge["first_ts"] == ALIGNED + 100 and edge["last_ts"] == ALIGNED + 900


def test_every_edge_row_agrees_that_time_moves_forward():
    """Exact rows and the `__longtail__` spill must obey the same interval rule.

    The aggregate row has always used min/max; the per-peer rows did not. One of
    the two was wrong, and only the disagreement made it visible.
    """
    wm = _open()
    for i in range(TOP_PEERS + 1):                       # forces the longtail spill
        wm._add(_flow(ALIGNED + 900, "SF", "established", dst=f"10.0.1.{i}"))
        wm._add(_flow(ALIGNED + 100, "SF", "established", dst=f"10.0.1.{i}"))
    (win,) = list(wm.finalize())

    assert any(e["dst"] == "__longtail__" for e in win.edges), "fixture must spill"
    assert all(e["first_ts"] <= e["last_ts"] for e in win.edges)
    assert all(e["first_ts"] == ALIGNED + 100 and e["last_ts"] == ALIGNED + 900
               for e in win.edges)


def test_identity_binding_interval_is_never_inverted():
    """The same interval rule, on the record the store persists.

    `last_seen` was already a max; `first_seen` was fixed at creation, so an
    out-of-order IdentityEvent produced a binding valid from after it ended.
    """
    wm = _open()
    wm._add(IdentityEvent(ts=ALIGNED + 900, kind="dhcp", ip="10.0.0.21",
                          mac="aa:bb:cc:dd:ee:01"))
    wm._add(IdentityEvent(ts=ALIGNED + 100, kind="dhcp", ip="10.0.0.21",
                          mac="aa:bb:cc:dd:ee:01"))
    (win,) = list(wm.finalize())

    binding = win.identity_delta[-1]
    assert binding["first_seen"] == ALIGNED + 100
    assert binding["last_seen"] == ALIGNED + 900


def test_late_non_flow_event_is_counted_too():
    """Lateness is a property of the event, not of the flow parser.

    Measured only inside `_add_flow`, a late `SensorHealthEvent` was folded into
    the NEXT window with nothing counting it: the degraded window reads clean and
    its neighbour reads degraded. The mis-attribution stands (that is what a
    streaming close costs), but it is now disclosed.
    """
    wm = WindowManager()
    events = [_flow(ALIGNED + 10, "SF", "established"),
              _flow(ALIGNED + WINDOW + 10, "SF", "established"),   # closes A, opens B
              SensorHealthEvent(ts=ALIGNED + 20, kind="capture_loss", value=40.0)]
    wins = list(wm.process(iter(events))) + list(wm.finalize())

    assert len(wins) == 2
    assert wins[0].health["capture_loss_pct_max"] is None, "A's own evidence arrived late"
    assert wins[1].health["capture_loss_pct_max"] == 40.0
    assert wins[1].health["late_events"] == 1, "the mis-attribution is disclosed"
    assert wins[0].health["late_events"] == 0


@pytest.mark.parametrize("event", [
    FlowEvent(ts=ALIGNED - 1, src_ip="10.0.0.21", dst_ip="10.0.0.5", proto="tcp",
              state="SF", state_class="established"),
    IdentityEvent(ts=ALIGNED - 1, kind="dns", ip="10.0.0.5", hostname="h"),
    ServiceEvent(ts=ALIGNED - 1, kind="ssl", ip="10.0.0.5", port=443),
    AlertEvent(ts=ALIGNED - 1, ip="10.0.0.21", category="Scan::Port_Scan"),
    SensorHealthEvent(ts=ALIGNED - 1, kind="capture_loss", value=1.0),
])
def test_every_event_class_outside_the_window_is_counted(event):
    """The class docstring's guarantee, stated as a test over the whole model.

    One counter, taken once before dispatch — so a new event class cannot be added
    without inheriting it.
    """
    wm = _open()
    wm._add(event)
    assert wm._late == 1, f"{type(event).__name__} escaped the lateness count"


def test_reset_derived_listener_reaches_the_assertion_layer_as_observed():
    """The widened gate has to survive the whole chain, provenance intact.

    A listener claim invented from a refusal would be the same bug as an imputed
    zero: `refused` proves the host answered, never that the port was open.
    """
    win = _one_window([_flow(T0 + 1, "RSTR", "reset_resp", dport=445),
                       _flow(T0 + 2, "REJ", "refused", dst="10.0.0.6", dport=23)])
    claims = [(a["subject"], a["object"], a["evidence_class"], a["confidence"])
              for a in extract_assertions(win) if a["predicate"] == "listens_on"]
    assert claims == [("10.0.0.5", "445/tcp", "observed", 0.95)]


def test_window_with_no_flows_still_closes_honestly():
    """A window can hold only service metadata; health must still be truthful."""
    win = _one_window([ServiceEvent(ts=T0 + 1, kind="ssl", ip="10.0.0.5", port=443)])
    assert win.nodes == {} and win.edges == []
    assert win.health["event_counts"] == {"ServiceEvent": 1}
    assert win.health["entities_active"] == 0
    assert win.health["late_events"] == 0
    assert win.health["sensor_pkts_dropped_max"] is None
    assert win.health["sensor_pkt_lag_max"] is None


def test_sensor_stats_reach_window_health():
    win = _one_window([
        _flow(T0 + 1, "SF", "established"),
        SensorHealthEvent(ts=T0 + 2, kind="pkts_dropped", value=1200.0, detail="w1"),
        SensorHealthEvent(ts=T0 + 3, kind="pkts_dropped", value=1500.0, detail="w1"),
        SensorHealthEvent(ts=T0 + 4, kind="pkt_lag", value=0.5, detail="w1"),
    ])
    assert win.health["sensor_pkts_dropped_max"] == 1500.0
    assert win.health["sensor_pkt_lag_max"] == 0.5


def test_absent_sensor_stats_are_unknown_not_zero():
    """No evidence must not read as a clean window (§5.4 #5: the asterisk travels)."""
    win = _one_window([_flow(T0 + 1, "SF", "established")])
    assert win.health["sensor_pkts_dropped_max"] is None
    assert win.health["sensor_pkt_lag_max"] is None


def test_sensor_pkts_proc_and_bytes_recv_sum_not_max_across_the_window():
    """`SENSOR_HEALTH_AGGREGATION` reduces `pkts_proc`/`bytes_recv` with `sum`, not `max` —
    unlike `pkts_dropped`/`pkt_lag` right above. Both readings here have the SECOND value
    SMALLER than the first (the documented real-baseline shape: each `stats.log` row is a
    delta since the previous report, so a busy window can legitimately report a smaller
    number in a later interval than an earlier one) — `max` or "last value wins" would both
    give `1000.0`/`50.0` here; only `sum` recovers `1600.0`/`90.0`, the window total.
    """
    win = _one_window([
        _flow(T0 + 1, "SF", "established"),
        SensorHealthEvent(ts=T0 + 2, kind="pkts_proc", value=1000.0, detail="zeek"),
        SensorHealthEvent(ts=T0 + 3, kind="pkts_proc", value=600.0, detail="zeek"),
        SensorHealthEvent(ts=T0 + 4, kind="bytes_recv", value=50.0, detail="zeek"),
        SensorHealthEvent(ts=T0 + 5, kind="bytes_recv", value=40.0, detail="zeek"),
    ])
    assert win.health["sensor_pkts_proc_sum"] == 1600.0
    assert win.health["sensor_bytes_recv_sum"] == 90.0
    # No cross-kind contamination: readings for one declared kind must never leak into
    # another's reduction, and a kind with no evidence this window stays None, not 0.0.
    assert win.health["sensor_pkts_dropped_max"] is None
    assert win.health["sensor_pkt_lag_max"] is None


def test_absent_pkts_proc_and_bytes_recv_are_unknown_not_zero():
    """The same `None`-not-`0.0` discipline `test_absent_sensor_stats_are_unknown_not_zero`
    checks for `pkts_dropped`/`pkt_lag`, extended to this round's two `sum`-reduced kinds.
    """
    win = _one_window([_flow(T0 + 1, "SF", "established")])
    assert win.health["sensor_pkts_proc_sum"] is None
    assert win.health["sensor_bytes_recv_sum"] is None


def test_every_declared_sensor_health_reducer_is_one_reduce_sensor_health_implements():
    """Table/function consistency, same discipline as
    `test_declined_service_kinds_are_named_not_merely_absent` one layer over: a
    `SENSOR_HEALTH_AGGREGATION` entry naming a reducer `_reduce_sensor_health` does not
    implement would silently divide window health into an untested third case. Guards
    against a future kind being declared with a typo'd or new reducer string with nothing
    to catch it before `_reduce_sensor_health`'s own fail-loud branch does, at run time,
    inside a window close.
    """
    from aximprint.windowing.windows import SENSOR_HEALTH_AGGREGATION

    assert set(SENSOR_HEALTH_AGGREGATION.values()) <= {"max", "sum"}


def test_reduce_sensor_health_rejects_an_undeclared_reducer():
    """Direct unit coverage for `_reduce_sensor_health`'s own fail-loud branch (CONVENTIONS
    §3): reachable only if `SENSOR_HEALTH_AGGREGATION` is ever edited to name a reducer this
    function does not implement — dead code on every declared table entry today, per
    `test_every_declared_sensor_health_reducer_is_one_reduce_sensor_health_implements`, but
    a guard that is never exercised is indistinguishable from one that does not work
    (`LES-031`). Called directly rather than routed through a table edit, so this test does
    not itself depend on the table staying inconsistent to pass.
    """
    from aximprint.windowing.windows import _reduce_sensor_health

    with pytest.raises(ValueError, match="unrecognized SENSOR_HEALTH_AGGREGATION reducer"):
        _reduce_sensor_health("average", [1.0, 2.0])


def test_reduce_sensor_health_none_for_no_readings_regardless_of_reducer():
    """`None`, never `0`/`0.0`, for both reducers when nothing was collected — the same
    "unaudited, not zero" claim `WindowManager` makes at the window level
    (`test_absent_sensor_stats_are_unknown_not_zero`), checked here at the function that
    actually enforces it, for both `[]` and `None` (`WindowManager._sensor_health.get(kind)`
    returns `None` for a kind with zero readings this window, never `[]`).
    """
    from aximprint.windowing.windows import _reduce_sensor_health

    assert _reduce_sensor_health("max", []) is None
    assert _reduce_sensor_health("sum", []) is None
    assert _reduce_sensor_health("max", None) is None
    assert _reduce_sensor_health("sum", None) is None


def test_absent_capture_loss_is_unknown_not_zero():
    """The same discipline, on the field it was never applied to.

    `0.0` here is a claim the sensor observed no loss. A window with no
    capture_loss.log evidence at all made exactly that claim.
    """
    win = _one_window([_flow(T0 + 1, "SF", "established")])
    assert win.health["capture_loss_pct_max"] is None

    win = _one_window([_flow(T0 + 1, "SF", "established"),
                       SensorHealthEvent(ts=T0 + 2, kind="capture_loss", value=0.0)])
    assert win.health["capture_loss_pct_max"] == 0.0, "observed zero loss is a finding"


def test_ingest_disorder_is_inherited_by_the_window_health_record():
    """The ingest layer's ordering audit has to survive into a persisted artifact.

    It is the only place the system knows a stream could not be repaired; the
    window it fed is the only place that fact can still be acted on.
    """
    win = _one_window([_flow(T0 + 1, "SF", "established"),
                       SensorHealthEvent(ts=T0 + 2, kind="ingest_unordered",
                                         value=12.5, detail="conn")])
    assert win.health["ingest_unordered"] == 1
    assert win.health["unmodelled_health_kinds"] == 0, "a modelled kind, not a stray"


def test_unaudited_input_ordering_is_unknown_not_clean():
    """None means "nobody audited", which is not the same claim as "it was ordered"."""
    win = _one_window([_flow(T0 + 1, "SF", "established")])
    assert win.health["ingest_unordered"] is None


# -- `unparsed_log_types` (`docs/WORK_ORDER_zeek_stem_coverage.md` D5) -----------

def test_unparsed_log_types_is_none_without_a_provider():
    """Same discipline as `ingest_unordered`: no provider wired in at all reads
    as unaudited, not as "the source had nothing left over" — every other test
    in this file constructs `WindowManager()` bare, and their windows must read
    `None` here, never `[]`."""
    win = _one_window([_flow(T0 + 1, "SF", "established")])
    assert win.health["unparsed_log_types"] is None


def test_unparsed_log_types_empty_dict_reads_as_clean_not_unaudited():
    """A provider that DID look and found nothing left over answers `[]`, not
    `None` — the same "no evidence" vs "evidence of none" split every other
    field in this record already makes (`LES-006`)."""
    win = _one_window([_flow(T0 + 1, "SF", "established")],
                      unparsed_log_types_provider=lambda: {})
    assert win.health["unparsed_log_types"] == []


def test_unparsed_log_types_is_sorted_names_only_not_counts():
    """The health record carries NAMES (a set, idempotent under union); counts
    are D5's other half and live in `runs` instead (`cli._pipeline`)."""
    win = _one_window([_flow(T0 + 1, "SF", "established")],
                      unparsed_log_types_provider=lambda: {"zeta": 3, "alpha": 1})
    assert win.health["unparsed_log_types"] == ["alpha", "zeta"]


def test_unparsed_log_types_reaches_every_window_including_the_first():
    """The build's own crux, proven at the layer that owns `health`'s shape: the
    provider is invoked FRESH at every `_close()`, never cached at construction,
    so a value that only becomes correct partway through a run — exactly
    `ingest/zeek/plugin.py::events`'s own laziness — still reaches window 1, not
    only whichever window happened to be open once it settled.

    Simulated here without a real plugin, so this is a regression test on
    `WindowManager` alone: a mutable one-slot box starts `None` ("not yet
    known") and is filled in only AFTER `WindowManager` is constructed but
    BEFORE a single event is fed in — mirroring the real timing exactly
    (`cli._pipeline` constructs `WindowManager` before `plugin.events()` has
    been touched at all, and the plugin resolves its own answer on the first
    event pulled from that generator). If the provider were ever resolved once
    at construction instead of read fresh per `_close()`, this test would see
    every window read `None` instead of the list asserted below.
    """
    box: dict = {"value": None}
    wm = WindowManager(unparsed_log_types_provider=lambda: box["value"])
    box["value"] = {"reporter": 1, "telemetry": 4}  # "settles" before any event arrives
    events = [_flow(T0 + w * WINDOW + 1, "SF", "established") for w in range(3)]
    wins = list(wm.process(iter(events))) + list(wm.finalize())
    assert len(wins) == 3, "fixture must span three separate windows"
    for win in wins:
        assert win.health["unparsed_log_types"] == ["reporter", "telemetry"]


def test_unrecognized_log_types_is_independent_of_unparsed_log_types():
    """Code review `S4`, additive to D5: a SEPARATE provider, a SEPARATE health
    key, and the SUBSET relationship is the CALLER's (the ingest plugin's) job
    to enforce, not something `WindowManager` cross-checks — this constructs a
    provider pair that would be INCONSISTENT for a real plugin (the
    "unrecognized" set is not actually a subset of the "unparsed" one) purely
    to prove `WindowManager` reads each callable independently, with no hidden
    coupling between the two."""
    win = _one_window(
        [_flow(T0 + 1, "SF", "established")],
        unparsed_log_types_provider=lambda: {"reporter": 1},
        unrecognized_log_types_provider=lambda: {"telemetry": 4})
    assert win.health["unparsed_log_types"] == ["reporter"]
    assert win.health["unrecognized_log_types"] == ["telemetry"]


def test_unrecognized_log_types_is_none_without_a_provider():
    win = _one_window([_flow(T0 + 1, "SF", "established")])
    assert win.health["unrecognized_log_types"] is None


def test_unrecognized_log_types_empty_dict_reads_as_clean_not_unaudited():
    win = _one_window([_flow(T0 + 1, "SF", "established")],
                      unrecognized_log_types_provider=lambda: {})
    assert win.health["unrecognized_log_types"] == []


def test_unmodelled_health_kind_is_counted():
    """`event_counts` reads `SensorHealthEvent: 1` either way — that is the problem."""
    win = _one_window([SensorHealthEvent(ts=T0 + 1, kind="reassembly_overflow",
                                         value=3.0)])
    assert win.health["event_counts"] == {"SensorHealthEvent": 1}
    assert win.health["unmodelled_health_kinds"] == 1


def test_unknown_state_flows_are_counted_in_health():
    """"Closed table" is only a claim until an unmapped state shows up as a number.

    Otherwise it is visible solely by scanning every node's `state_mix`.
    """
    win = _one_window([_flow(T0 + 1, "SF", "established"),
                       _flow(T0 + 2, "NOT_A_ZEEK_STATE", "unknown")])
    assert win.health["unknown_state_flows"] == 1
    assert win.nodes["10.0.0.21"]["state_mix"]["unknown"] == 1


def test_imputed_flow_counters_are_counted_in_health():
    """A flow whose byte counters were unset reads as 0 bytes; say how many."""
    imputed = _flow(T0 + 1, "OTH", "other")
    imputed.counters_imputed = True
    win = _one_window([imputed, _flow(T0 + 2, "SF", "established")])
    assert win.health["flows_imputed_counters"] == 1


def test_icmp_never_manufactures_a_listener():
    """`dst_port` is only a port for tcp/udp.

    For ICMP Zeek puts the message *type* there and can still assign an accepted
    state, so an echo request became a `listens_on 8/icmp` claim at confidence
    0.95 — a service asserted on a port the host does not have.
    """
    win = _one_window([_flow(T0 + 1, "SF", "established", dport=8, proto="icmp"),
                       _flow(T0 + 2, "SF", "established", dport=443)])
    assert win.nodes["10.0.0.5"]["listen_ports"] == ["443/tcp"]
    claims = [a["object"] for a in extract_assertions(win)
              if a["predicate"] == "listens_on"]
    assert claims == ["443/tcp"]
