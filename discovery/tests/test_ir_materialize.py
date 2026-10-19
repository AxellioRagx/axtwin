"""Belief store -> PIT (`docs/WORK_ORDER_network_ir.md` build step 3).

Synthetic events only (`LES-003`). Two properties carry this file: a PIT is a **pure function** of
(store, as_of, build) — a comparator subtracts two of them, so any instability reads as drift — and
it **refuses** rather than degrades whenever it cannot say honestly what it describes.
"""
import json
import statistics

import pytest

from aximprint.events import FlowEvent, ProfileEvent
from aximprint.ir import SCHEDULE_CLASSES
from aximprint.ir.materialize import (
    MaterializeError,
    classify_schedule,
    materialize,
    pit_id_for,
)
from aximprint.ir.schema import SCHEMA_VERSION
from aximprint.ir.store import IRStore, IRStoreError
from aximprint.learners import extract_assertions
from aximprint.provenance import build_stamp
from aximprint.store.belief_store import BeliefStore
from aximprint.windowing import WindowManager
from aximprint.windowing.sketches import Quantiles

WINDOW = 3600
T0 = 1_754_000_000.0
ALIGNED = T0 - (T0 % WINDOW)


def _flow(ts, src, dst, dport=445, local_src=True, local_dst=True):
    return FlowEvent(ts=ts, src_ip=src, dst_ip=dst, src_port=51000, dst_port=dport,
                     proto="tcp", service="", duration=1.0, bytes_out=100, bytes_in=200,
                     pkts_out=2, pkts_in=2, state="SF", state_class="established",
                     local_src=local_src, local_dst=local_dst)


def _store(tmp_path, events, *, source="/cap/one", name="s.duckdb"):
    """A belief store with windows AND a `runs` row — materialize needs both."""
    st = BeliefStore(str(tmp_path / name))
    wm = WindowManager(window_seconds=WINDOW)
    wins = list(wm.process(iter(events))) + list(wm.finalize())
    for w in wins:
        st.write_window(w, extract_assertions(w))
    st.write_entity_roster(source, wm.entity_roster())
    st.write_run({"source": source, "plugin": "zeek", "window_seconds": WINDOW,
                  "subbuckets": 12}, build_stamp(), len(wins), ALIGNED, ALIGNED + 1)
    return st


def _basic(tmp_path, **kw):
    return _store(tmp_path, [
        _flow(ALIGNED + 1, "10.80.72.13", "10.80.20.5", 88),
        _flow(ALIGNED + 2, "10.80.72.13", "10.80.20.5", 389),
        _flow(ALIGNED + 3, "10.80.72.13", "140.82.113.4", 443, local_dst=False),
    ], **kw)


# -- shape -----------------------------------------------------------------------

def test_a_pit_carries_every_table_and_one_pit_row(tmp_path):
    pit = materialize(_basic(tmp_path))
    assert len(pit["ir_pit"]) == 1
    for table in ("ir_entity", "ir_segment", "ir_edge"):
        assert pit[table], f"{table} is empty"
    assert pit["ir_pit"][0]["schema_version"] == SCHEMA_VERSION


def test_every_row_carries_the_pit_id(tmp_path):
    pit = materialize(_basic(tmp_path))
    pit_id = pit["ir_pit"][0]["pit_id"]
    for table, rows in pit.items():
        if table.startswith("ir_"):
            assert all(r["pit_id"] == pit_id for r in rows), table


def test_as_of_none_is_resolved_to_a_concrete_timestamp(tmp_path):
    """A PIT whose `as_of` is unset cannot be ordered against another one, and ordering is the
    entire point of the artifact."""
    pit = materialize(_basic(tmp_path), as_of=None)
    assert isinstance(pit["ir_pit"][0]["as_of"], float)


def test_the_observation_extent_is_present_and_ordered(tmp_path):
    """D3: a PIT with no extent is uninterpretable — a 3-hour and a 71-hour capture are not the
    same claim, and a comparator needs to be able to refuse the pair."""
    row = materialize(_basic(tmp_path))["ir_pit"][0]
    assert row["observed_from"] <= row["as_of"] <= row["observed_to"]
    assert row["windows"] >= 1


# -- what locality buys, which is why the schema went to 1.1 ---------------------

def test_an_external_peer_and_a_local_unplaceable_host_are_distinguishable(tmp_path):
    """Before `locality`, both were just `segment IS NULL`. A comparator filtering the monitored
    network on that alone would silently drop every link-local host (`LES-006`)."""
    st = _store(tmp_path, [
        _flow(ALIGNED + 1, "10.80.72.13", "10.80.20.5"),
        _flow(ALIGNED + 2, "10.80.72.13", "140.82.113.4", local_dst=False),   # external
        _flow(ALIGNED + 3, "fe80::1", "10.80.20.5"),                          # local, unplaceable
    ])
    by_key = {e["entity_key"]: e for e in materialize(st)["ir_entity"]}
    external = by_key["address:140.82.113.4"]
    linklocal = by_key["address:fe80::1"]
    assert (external["locality"], external["segment"]) == ("external", None)
    assert (linklocal["locality"], linklocal["segment"]) == ("local", None)


def test_a_placed_host_is_local_and_has_a_segment(tmp_path):
    row = next(e for e in materialize(_basic(tmp_path))["ir_entity"]
               if e["entity_key"] == "address:10.80.20.5")
    assert row["locality"] == "local" and row["segment"] == "10.80.20.0/24"


# -- provenance ------------------------------------------------------------------

def test_a_role_is_carried_as_imputed_and_absence_is_null(tmp_path):
    """Both halves matter: an inferred role is `imputed` (never `observed` — nobody saw "this
    host is a DC"), and a host no rule names carries a real null rather than a guess.

    This builds its own store rather than using `_basic`: since GAP-L5 added dial evidence,
    every entity in `_basic` matches something (the client dials 88 -> corp-workstation, the
    external host answered on 443 -> web-tier), so the null half needs a pair the catalog
    names on neither side. 9999/tcp is such a port.
    """
    st = _store(tmp_path, [
        _flow(ALIGNED + 1, "10.80.72.13", "10.80.20.5", 88),
        _flow(ALIGNED + 2, "10.80.72.13", "10.80.20.5", 389),
        _flow(ALIGNED + 3, "10.80.72.99", "10.80.20.7", 9999),
    ])
    ents = {e["entity_key"]: e for e in materialize(st)["ir_entity"]}
    dc = ents["address:10.80.20.5"]
    assert (dc["role"], dc["role_evidence_class"]) == ("domain-controller", "imputed")
    client = ents["address:10.80.72.13"]
    assert (client["role"], client["role_evidence_class"]) == ("corp-workstation", "imputed")
    roleless = ents["address:10.80.20.7"]
    assert roleless["role"] is None and roleless["role_evidence_class"] is None


def test_segments_carry_their_coverage_and_say_it_is_inferred(tmp_path):
    """D4a: Zeek reports no per-segment loss, so this is a proxy and must never read as
    measured."""
    for seg in materialize(_basic(tmp_path))["ir_segment"]:
        assert seg["coverage_source"] == "inferred_from_traffic"
        assert 0 < seg["windows_present"] <= seg["windows_total"]


def test_within_pit_presence_shape_is_carried(tmp_path):
    """D4a's finer time scale: "present in N of M windows" is shape, not a boolean."""
    for row in materialize(_basic(tmp_path))["ir_entity"]:
        assert row["windows_seen"] >= 1
        assert row["first_seen"] is not None and row["last_seen"] is not None


# -- determinism -----------------------------------------------------------------

def test_two_materializations_of_one_store_are_identical(tmp_path):
    """A comparator subtracts two PITs; any instability here reads as drift."""
    st = _basic(tmp_path)
    a, b = materialize(st), materialize(st)
    assert a == b


def test_the_pit_id_is_a_pure_function_of_source_and_as_of():
    assert pit_id_for("/cap/one", 100.0) == pit_id_for("/cap/one", 100.0)
    assert pit_id_for("/cap/one", 100.0) != pit_id_for("/cap/two", 100.0)
    assert pit_id_for("/cap/one", 100.0) != pit_id_for("/cap/one", 200.0)


def test_rows_are_sorted_at_the_persistence_boundary(tmp_path):
    """`LES-009`: a file's bytes must depend on row content, not on an upstream dict's order."""
    pit = materialize(_basic(tmp_path))
    assert [e["entity_key"] for e in pit["ir_entity"]] == \
        sorted(e["entity_key"] for e in pit["ir_entity"])


# -- refusals --------------------------------------------------------------------

def test_a_store_nothing_ever_closed_into_is_refused(tmp_path):
    """A PIT that cannot say what capture it describes is not a PIT."""
    st = BeliefStore(str(tmp_path / "empty.duckdb"))
    wm = WindowManager(window_seconds=WINDOW)
    for w in list(wm.process(iter([_flow(ALIGNED + 1, "10.0.0.1", "10.0.0.2")]))) + \
            list(wm.finalize()):
        st.write_window(w, extract_assertions(w))
    with pytest.raises(MaterializeError, match="no `runs` row"):
        materialize(st)


def test_a_store_mixing_two_sources_is_refused(tmp_path):
    """A PIT describes ONE network as of one time; attributing it to either source would be
    false for the other. Still refused after D3 replaced the source-equality rule, because
    neither run declared a network or a vantage that could say they are one capture."""
    st = _basic(tmp_path)
    st.write_run({"source": "/cap/two", "plugin": "zeek", "window_seconds": WINDOW,
                  "subbuckets": 12}, build_stamp(), 1, ALIGNED, ALIGNED + 2)
    with pytest.raises(MaterializeError, match="mixes 2 sources"):
        materialize(st)


# -- D3: network identity replaces source equality --------------------------------
#
# `docs/WORK_ORDER_visibility_and_vantage.md`, ratified 2026-08-17. The rule this supersedes
# conflated two cases it could not tell apart, and got one of them wrong in each direction:
# re-harvesting ONE tap into a new directory was falsely refused, and two DIFFERENT taps
# writing to one path were falsely accepted.

def _second_run(st, **meta):
    st.write_run({"source": "/cap/two", "plugin": "zeek", "window_seconds": WINDOW,
                  "subbuckets": 12, **meta}, build_stamp(), 1, ALIGNED, ALIGNED + 2)


def test_one_tap_re_harvested_into_a_new_directory_is_accepted(tmp_path):
    """The falsely-refused half. A path changed; the network did not."""
    st = _store(tmp_path, [_flow(ALIGNED + 1, "10.0.1.10", "10.0.1.20")], source="/cap/one")
    st.con.execute("UPDATE runs SET vantage = 'tap-a', network = 'corp'")
    _second_run(st, vantage="tap-a", network="corp")
    row = materialize(st)["ir_pit"][0]
    assert row["vantage"] == "tap-a" and row["network"] == "corp"
    assert row["source"] == "/cap/one + /cap/two", "the PIT says what it was built from"


def test_two_networks_in_one_store_are_refused(tmp_path):
    st = _store(tmp_path, [_flow(ALIGNED + 1, "10.0.1.10", "10.0.1.20")], source="/cap/one")
    st.con.execute("UPDATE runs SET network = 'corp'")
    _second_run(st, network="lab")
    with pytest.raises(MaterializeError, match="mixes 2 declared networks"):
        materialize(st)


def test_two_vantages_in_one_store_are_refused_and_point_at_merge(tmp_path):
    """A PIT is one tap point's view (D4). Unioning two taps HERE would hide the view difference
    inside a single artifact, which is exactly what `merge` exists to make visible instead."""
    st = _store(tmp_path, [_flow(ALIGNED + 1, "10.0.1.10", "10.0.1.20")], source="/cap/one")
    st.con.execute("UPDATE runs SET vantage = 'tap-a', network = 'corp'")
    _second_run(st, vantage="tap-b", network="corp")
    with pytest.raises(MaterializeError, match="merge --axis vantage"):
        materialize(st)


def test_a_vantage_declared_by_only_some_runs_does_not_join_two_sources(tmp_path):
    """Declared by EVERY run, not merely by one: the undeclared half is exactly the part a
    false claim would be about."""
    st = _store(tmp_path, [_flow(ALIGNED + 1, "10.0.1.10", "10.0.1.20")], source="/cap/one")
    st.con.execute("UPDATE runs SET vantage = 'tap-a'")
    _second_run(st)
    with pytest.raises(MaterializeError, match="neither one vantage nor one network"):
        materialize(st)


def test_a_pit_with_no_declaration_carries_none_never_a_placeholder(tmp_path):
    """`LES-006`. Unknown must not compare equal to another unknown."""
    row = materialize(_basic(tmp_path))["ir_pit"][0]
    assert row["vantage"] is None and row["network"] is None
    assert row["merge_axis"] is None and row["vantages"] is None


# -- D7: the referenced-but-unseen census ------------------------------------------

def _referenced(st, address, name, ts):
    """A DNS answer nothing was ever seen from — what the resolver records as `referenced`."""
    st.con.execute(
        "INSERT INTO identity_bindings VALUES (?, 'opened', 'dns', ?, ?, 'dnsname', ?, "
        "'referenced', ?, ?, NULL, ?, ?, NULL, 60, 5)",
        [ALIGNED, address, name, f"dnsname:{name}", address, ts, ts, ts])


def test_an_address_only_ever_named_by_dns_is_unseen(tmp_path):
    """2,418 of these on the 2026-08-11 baseline, sitting in the belief store and dropped by the
    IR until schema `1.2` — DESIGN_BRIEF §8's own first input to the visibility model."""
    st = _basic(tmp_path)
    _referenced(st, "10.80.72.99", "ghost.corp", ALIGNED + 5)
    rows = materialize(st)["ir_unseen"]
    assert [r["address"] for r in rows] == ["10.80.72.99"]
    assert rows[0]["reason"] == "referenced_by_dns"
    assert rows[0]["evidence_class"] == "referenced", "named, never observed"


def test_an_address_seen_in_a_flow_is_not_unseen_however_often_it_was_resolved(tmp_path):
    """The complement has to be exact, or the census becomes a list of every host with a name."""
    st = _basic(tmp_path)
    _referenced(st, "10.80.20.5", "dc.corp", ALIGNED + 5)      # a host the capture DID see
    assert materialize(st)["ir_unseen"] == []


def test_an_unseen_host_inside_an_observed_segment_is_the_blind_spot_proper(tmp_path):
    """9 of the 2,418 on that baseline. On a near-full-visibility vantage that is small; on a
    partial one it IS the blind spot, which is why the strong case is separated from the rest."""
    st = _basic(tmp_path)
    _referenced(st, "10.80.72.99", "ghost.corp", ALIGNED + 5)   # inside 10.80.72.0/24
    _referenced(st, "93.184.216.34", "example.com", ALIGNED + 6)
    pit = materialize(st)
    rows = {r["address"]: r for r in pit["ir_unseen"]}
    assert rows["10.80.72.99"]["segment"] == "10.80.72.0/24"
    assert rows["10.80.72.99"]["locality"] == "local"
    assert rows["93.184.216.34"]["segment"] is None
    assert rows["93.184.216.34"]["locality"] == "external"
    assert pit["_stats"]["unseen_hosts_in_observed_segments"] == 1


def test_the_unseen_census_honors_the_span_bound(tmp_path):
    """`snapshot()` read `identity_bindings` UNBOUNDED — a real defect found while building this
    census. A `since`-bounded PIT (the shape `drift` requires) inherited every binding the store
    ever held, so a host first named in window 2 appeared in a PIT covering only window 1.
    """
    st = _store(tmp_path, [_flow(ALIGNED + 1, "10.80.72.13", "10.80.20.5"),
                           _flow(ALIGNED + WINDOW + 1, "10.80.72.13", "10.80.20.5")])
    # The binding transition happens in window 2, so it must not reach a window-1 PIT.
    st.con.execute(
        "INSERT INTO identity_bindings VALUES (?, 'opened', 'dns', '10.80.72.99', 'ghost.corp', "
        "'dnsname', 'dnsname:ghost.corp', 'referenced', '10.80.72.99', ?, NULL, ?, ?, NULL, 60, 5)",
        [ALIGNED + WINDOW, ALIGNED + WINDOW, ALIGNED + WINDOW, ALIGNED + WINDOW])
    assert materialize(st, since=ALIGNED, as_of=ALIGNED)["ir_unseen"] == []
    assert [r["address"] for r in
            materialize(st, since=ALIGNED + WINDOW, as_of=ALIGNED + WINDOW)["ir_unseen"]] == \
        ["10.80.72.99"]


def test_an_as_of_before_every_window_is_refused(tmp_path):
    with pytest.raises(MaterializeError, match="nothing to describe"):
        materialize(_basic(tmp_path), as_of=ALIGNED - 10_000)


# -- D1-D7: the behavioral profile (`docs/WORK_ORDER_traffic_profiles.md`) -----------------------

def _diurnal_events(hours, days, src="10.80.72.50", dst="10.80.20.5"):
    """One flow event per (day, hour) pair, each on its own distinct calendar day starting at
    `ALIGNED` — so the entity's own `windows_active` is exactly `len(hours) * days` and no two
    events ever land in the same window. `hours` is an OFFSET from `ALIGNED`'s own hour, not
    necessarily the absolute UTC hour, and callers that need the absolute hour derive it the same
    way `classify_schedule`'s callers do (`datetime.fromtimestamp(ts, tz=timezone.utc)`).
    """
    hours = sorted(hours)          # ascending ts throughout -- `WindowManager` is not a sorter
    events = []
    for day in range(days):
        for h in hours:
            events.append(_flow(ALIGNED + day * 86400 + h * 3600 + 1, src, dst))
    return events


def _traffic_row(pit, address="10.80.72.50"):
    return next(r for r in pit["ir_traffic_profile"] if r["entity_key"] == f"address:{address}")


# -- classify_schedule: pure, no store ---------------------------------------------

def test_classify_schedule_returns_none_for_no_evidence():
    """D4's floor, at the classifier's own boundary: `None` in, `None` out — never a class from
    nothing."""
    assert classify_schedule(None) is None


def test_classify_schedule_asserts_rather_than_silently_absorbing_a_wrong_shape():
    """A malformed histogram is a CALLER BUG, not an evidence gap (CONVENTIONS §3: assertions
    over silent defaults)."""
    with pytest.raises(AssertionError):
        classify_schedule([1.0] * 23)


def test_classify_schedule_calls_a_flat_distribution_24x7():
    assert classify_schedule([1.0 / 24] * 24) == "24x7"


def test_classify_schedule_calls_a_concentrated_distribution_business_hours():
    hist = [0.0] * 24
    for h in range(9, 17):        # 8 contiguous hours, `_BAND_HOURS`
        hist[h] = 1.0 / 8
    assert classify_schedule(hist) == "business-hours"


def test_classify_schedule_only_ever_returns_the_closed_vocabulary():
    flat = [1.0 / 24] * 24
    busy = [0.0] * 24
    for h in range(9, 17):
        busy[h] = 1.0 / 8
    assert classify_schedule(flat) in SCHEDULE_CLASSES
    assert classify_schedule(busy) in SCHEDULE_CLASSES


# -- the aggregation, and both floors (D2/D4) ----------------------------------------

def test_windows_active_equals_ir_entitys_windows_seen(tmp_path):
    """D2's own acceptance line: "windows_active is distinct from ir_entity.windows_seen only if
    the latter's definition drifts" — asserted equal here, cross-field, so the two tables can
    never silently part (`LES-133`: a point check on one field cannot see its neighbour drift)."""
    pit = materialize(_store(tmp_path, _diurnal_events(range(24), 3)))
    seen = {e["entity_key"]: e["windows_seen"] for e in pit["ir_entity"]}
    for row in pit["ir_traffic_profile"]:
        assert row["windows_active"] == seen[row["entity_key"]]


def test_every_keyed_entity_gets_a_traffic_profile_row(tmp_path):
    pit = materialize(_basic(tmp_path))
    assert {e["entity_key"] for e in pit["ir_entity"]} == \
        {r["entity_key"] for r in pit["ir_traffic_profile"]}


def test_below_the_rate_floor_every_rate_and_diurnal_field_is_none(tmp_path):
    """D4: two windows is below `_RATE_MIN_WINDOWS` (proposed 4) — nothing about spread or shape
    may be published from that little evidence."""
    pit = materialize(_store(tmp_path, _diurnal_events([9], 2)))
    row = _traffic_row(pit)
    assert row["windows_active"] == 2
    assert row["windows_total"] >= row["windows_active"]
    for field in ("flows_per_window_q", "bytes_per_window_q", "burstiness_p50",
                 "client_ratio_p50", "interarrival_p50", "hod_flows", "dow_flows",
                 "schedule", "schedule_evidence_class"):
        assert row[field] is None, field
    assert pit["_stats"]["entities_below_rate_floor"] >= 1
    assert pit["_stats"]["entities_below_diurnal_floor"] >= 1


def test_between_the_floors_rate_fields_populate_and_diurnal_fields_stay_none(tmp_path):
    """D4: four windows clears the rate floor and not the diurnal one (proposed 48) — the two
    floors are independent, and a row must show that rather than gating everything on one."""
    pit = materialize(_store(tmp_path, _diurnal_events([9, 10, 11, 12], 1)))
    row = _traffic_row(pit)
    assert row["windows_active"] == 4
    assert row["flows_per_window_q"] is not None and row["bytes_per_window_q"] is not None
    assert row["burstiness_p50"] is not None
    assert row["client_ratio_p50"] is not None
    assert row["interarrival_p50"] is not None
    assert row["hod_flows"] is None and row["dow_flows"] is None
    assert row["schedule"] is None and row["schedule_evidence_class"] is None


def test_above_the_diurnal_floor_hod_flows_is_a_normalized_24_bucket_histogram(tmp_path):
    pit = materialize(_store(tmp_path, _diurnal_events(range(24), 3)))    # 72 windows
    row = _traffic_row(pit)
    assert row["windows_active"] == 72
    hod = json.loads(row["hod_flows"])
    assert len(hod) == 24
    assert pytest.approx(sum(hod), abs=1e-9) == 1.0
    assert row["schedule"] == "24x7"            # uniform across all 24 hours
    assert row["schedule_evidence_class"] == "imputed"
    assert row["evidence_class"] == "observed"


def test_dow_flows_is_a_normalized_seven_bucket_histogram(tmp_path):
    pit = materialize(_store(tmp_path, _diurnal_events(range(24), 3)))
    dow = json.loads(_traffic_row(pit)["dow_flows"])
    assert len(dow) == 7
    assert pytest.approx(sum(dow), abs=1e-9) == 1.0


def test_a_concentrated_entity_is_classified_business_hours_right_at_the_floor(tmp_path):
    """8 busy hours/day * 6 days = 48 windows — exactly `_DIURNAL_MIN_WINDOWS`, and every one of
    them concentrated in one contiguous band (the quiet hours never touch this entity at all, so
    their bucket is exactly zero rather than merely small)."""
    pit = materialize(_store(tmp_path, _diurnal_events(range(9, 17), 6)))
    row = _traffic_row(pit)
    assert row["windows_active"] == 48
    assert row["schedule"] == "business-hours"


def test_flows_per_window_q_is_p10_p50_p90_p99_over_active_windows(tmp_path):
    """One flow per window here, so every active window contributes the SAME total (2: one flow
    touches both `flows_out` on the source and `flows_in` would be on the peer — the row under
    test is the source, whose own `flows_out` is 1 each active window)."""
    pit = materialize(_store(tmp_path, _diurnal_events([9, 10, 11, 12], 1)))
    row = _traffic_row(pit)
    fq = json.loads(row["flows_per_window_q"])
    assert len(fq) == 4
    assert fq == [1.0, 1.0, 1.0, 1.0], "one flow per window, every quantile point is 1.0"


def test_an_entity_below_a_floor_is_still_counted_never_dropped_from_ir_entity(tmp_path):
    """The floor gates FIELDS within a row, never the row's own existence — `ir_entity` and
    `ir_traffic_profile` must still agree on which entities exist."""
    pit = materialize(_store(tmp_path, _diurnal_events([9], 1)))
    assert len(pit["ir_traffic_profile"]) == len(pit["ir_entity"]) > 0


# -- the merge hazard (`LES-114`): traffic must aggregate under the RESOLVED entity -------------

def test_traffic_aggregates_under_the_resolved_entity_across_a_merge(tmp_path):
    """`node_features` is pre-merge, `ir_entity`/`snapshot()` are post-merge (`Q1` D2). A hostname
    binding arriving mid-capture resolves the bare address to `ent-0001`; if `_entity_traffic`
    forgot to canonicalize, the pre-binding windows would silently vanish from the profile the
    same way `LES-114`'s original bug dropped them from `_entity_extents`/`_entity_locality`.
    """
    from aximprint.events import IdentityEvent

    events = [_flow(ALIGNED + i * WINDOW + 1, "10.80.72.13", "10.80.20.5")
              for i in range(4)]
    events.append(IdentityEvent(ts=ALIGNED + 2 * WINDOW, kind="dhcp", ip="10.80.72.13",
                                hostname="workstn1", lease_seconds=3600.0))
    events.sort(key=lambda e: e.ts)
    pit = materialize(_store(tmp_path, events))
    hostnamed = [e for e in pit["ir_entity"] if e["key_kind"] == "hostname"]
    assert hostnamed, "the binding must have resolved to a hostname-keyed entity"
    key = hostnamed[0]["entity_key"]
    tp = {r["entity_key"]: r for r in pit["ir_traffic_profile"]}
    # All 4 windows must be under the ONE resolved key, not split 2-and-2 across the pre- and
    # post-merge spellings.
    assert tp[key]["windows_active"] == 4


# -- D7: `ir_capture_profile`, `profile_facts`' first reader -------------------------------------

def test_capture_profile_pools_across_windows_by_median_and_sums_n(tmp_path):
    """The rule taken for a channel `BeliefStore.snapshot`'s own docstring declines to rule on:
    the quantile stats reduce to the MEDIAN ACROSS WINDOWS of that window's own value (the same
    operation D2 already applies to `interarrival_p50`), `n` SUMS."""
    per_window_values = [[100.0, 200.0, 300.0], [500.0], [10.0, 20.0]]
    events = [ProfileEvent(ts=ALIGNED + i * WINDOW, parameter="ocsp_cache_lifetime", value=v)
              for i, values in enumerate(per_window_values) for v in values]
    events.sort(key=lambda e: e.ts)

    pit = materialize(_store(tmp_path, events))
    rows = {r["stat"]: r for r in pit["ir_capture_profile"]
            if r["parameter"] == "ocsp_cache_lifetime"}
    assert set(rows) == {"p10", "p50", "p90", "p99", "n"}

    per_window_stats = {"p10": [], "p50": [], "p90": [], "p99": []}
    n_total = 0
    for values in per_window_values:
        q = Quantiles()
        for v in values:
            q.add(v)
        p10, p50, p90, p99 = q.quantiles(points=(0.10, 0.50, 0.90, 0.99))
        per_window_stats["p10"].append(p10)
        per_window_stats["p50"].append(p50)
        per_window_stats["p90"].append(p90)
        per_window_stats["p99"].append(p99)
        n_total += len(values)

    for stat in ("p10", "p50", "p90", "p99"):
        assert rows[stat]["value"] == pytest.approx(statistics.median(per_window_stats[stat]))
    assert rows["n"]["value"] == n_total
    assert rows["n"]["unit"] == "count"
    assert all(r["windows"] == len(per_window_values) for r in rows.values())
    assert all(r["evidence_class"] == "observed" for r in rows.values())


def test_capture_profile_carries_the_parameters_own_unit(tmp_path):
    events = [ProfileEvent(ts=ALIGNED, parameter="ocsp_cache_lifetime", value=604800.0)]
    pit = materialize(_store(tmp_path, events))
    p50 = next(r for r in pit["ir_capture_profile"] if r["stat"] == "p50")
    assert p50["unit"] == "seconds"


def test_capture_profile_rows_are_sorted_at_the_persistence_boundary(tmp_path):
    events = [ProfileEvent(ts=ALIGNED, parameter="ocsp_cache_lifetime", value=v)
              for v in (1.0, 2.0, 3.0)]
    pit = materialize(_store(tmp_path, events))
    keys = [(r["parameter"], r["stat"]) for r in pit["ir_capture_profile"]]
    assert keys == sorted(keys)


def test_no_profile_evidence_is_an_empty_table_not_a_crash(tmp_path):
    assert materialize(_basic(tmp_path))["ir_capture_profile"] == []


# -- determinism (`LES-002`) ------------------------------------------------------

def test_two_materializations_agree_on_the_behavioral_profile(tmp_path):
    st = _store(tmp_path, _diurnal_events(range(9, 17), 6) + [
        ProfileEvent(ts=ALIGNED, parameter="ocsp_cache_lifetime", value=100.0)])
    a, b = materialize(st), materialize(st)
    assert a["ir_traffic_profile"] == b["ir_traffic_profile"]
    assert a["ir_capture_profile"] == b["ir_capture_profile"]


# -- the store round trip for both new tables -------------------------------------

def test_the_behavioral_profile_tables_round_trip_through_the_store(tmp_path):
    pit = materialize(_store(tmp_path, _diurnal_events(range(9, 17), 6) + [
        ProfileEvent(ts=ALIGNED, parameter="ocsp_cache_lifetime", value=100.0)]))
    ir = IRStore(str(tmp_path / "ir.duckdb"))
    ir.write_pit(pit)
    got = ir.read_pit(pit["ir_pit"][0]["pit_id"])
    assert len(got["ir_traffic_profile"]) == len(pit["ir_traffic_profile"])
    assert len(got["ir_capture_profile"]) == len(pit["ir_capture_profile"])
    assert {r["entity_key"] for r in got["ir_traffic_profile"]} == \
        {r["entity_key"] for r in pit["ir_traffic_profile"]}


def test_a_floor_gated_null_survives_the_store_as_none_not_a_json_null_string(tmp_path):
    """`LES-006` at the persistence boundary: below a floor a field must round-trip as SQL NULL
    (`None` in Python), never the JSON string `"null"` a careless caller would have to parse
    before discovering the value is absent."""
    pit = materialize(_store(tmp_path, _diurnal_events([9], 2)))    # below both floors
    ir = IRStore(str(tmp_path / "ir.duckdb"))
    ir.write_pit(pit)
    row = _traffic_row(ir.read_pit(pit["ir_pit"][0]["pit_id"]))
    assert row["hod_flows"] is None and row["flows_per_window_q"] is None
    assert row["schedule"] is None


# -- the store -------------------------------------------------------------------

def test_a_pit_round_trips(tmp_path):
    pit = materialize(_basic(tmp_path))
    ir = IRStore(str(tmp_path / "ir.duckdb"))
    ir.write_pit(pit)
    got = ir.read_pit(pit["ir_pit"][0]["pit_id"])
    assert len(got["ir_entity"]) == len(pit["ir_entity"])
    assert {e["entity_key"] for e in got["ir_entity"]} == \
        {e["entity_key"] for e in pit["ir_entity"]}


def test_rewriting_a_pit_is_refused_by_default(tmp_path):
    """Something may already have compared against it. Silently replacing would change the
    meaning of a drift report already written, without changing anything a reader could see."""
    pit = materialize(_basic(tmp_path))
    ir = IRStore(str(tmp_path / "ir.duckdb"))
    ir.write_pit(pit)
    with pytest.raises(IRStoreError, match="already exists"):
        ir.write_pit(pit)


def test_replace_is_the_deliberate_act_and_does_not_duplicate(tmp_path):
    pit = materialize(_basic(tmp_path))
    ir = IRStore(str(tmp_path / "ir.duckdb"))
    ir.write_pit(pit)
    ir.write_pit(pit, replace=True)
    assert ir.pit_ids() == [pit["ir_pit"][0]["pit_id"]]
    assert len(ir.read_pit(pit["ir_pit"][0]["pit_id"])["ir_entity"]) == len(pit["ir_entity"])


def test_many_pits_live_in_one_store(tmp_path):
    """D2: a drift comparison is a join, not N file attaches."""
    ir = IRStore(str(tmp_path / "ir.duckdb"))
    for i, source in enumerate(("/cap/one", "/cap/two")):
        pit = materialize(_basic(tmp_path, source=source, name=f"s{i}.duckdb"))
        ir.write_pit(pit)
    assert len(ir.pit_ids()) == 2


def test_a_pit_from_an_unreadable_major_is_refused_at_open(tmp_path):
    """§11 fail-closed load, checked at OPEN so an unreadable artifact cannot be half-consumed."""
    path = str(tmp_path / "ir.duckdb")
    pit = materialize(_basic(tmp_path))
    ir = IRStore(path)
    ir.write_pit(pit)
    ir.con.execute("UPDATE ir_pit SET schema_version = '99.0'")
    ir.close()
    with pytest.raises(IRStoreError, match="cannot read"):
        IRStore(path)


def test_a_higher_minor_of_the_same_major_opens_fine(tmp_path):
    """Additive by definition — refusing it would defeat the reason the artifact is versioned."""
    path = str(tmp_path / "ir.duckdb")
    pit = materialize(_basic(tmp_path))
    ir = IRStore(path)
    ir.write_pit(pit)
    major = SCHEMA_VERSION.split(".")[0]
    ir.con.execute(f"UPDATE ir_pit SET schema_version = '{major}.99'")
    ir.close()
    IRStore(path).close()          # must not raise


# -- within-PIT occupancy, and the trap that corrupted it ------------------------

def test_windows_seen_never_exceeds_the_pits_own_window_count(tmp_path):
    """The invariant that caught it: a host cannot be present in more windows than exist.

    `snapshot()` used to SUM per-spelling window counts, and a merged host has rows under BOTH
    its pre-merge address and its `ent-NNNN` id (`Q1` D2, forward-only) over OVERLAPPING ranges.
    Measured on the 2026-08-11 baseline before the fix: 20 of 24 resolved entities reported more
    windows than the store had, `ent-0001` claiming 4 of 3.

    This matters beyond arithmetic: `windows_seen` is the within-PIT presence shape (D4a), which
    is what tells an always-on host from an occasional one — and it was wrong for precisely the
    entities resolution worked hardest on. Third instance of the pre-merge/post-merge trap
    (`LES-114`).
    """
    st = _store(tmp_path, [
        _flow(ALIGNED + 1, "10.80.72.13", "10.80.20.5"),
        _flow(ALIGNED + WINDOW + 1, "10.80.72.13", "10.80.20.5"),
    ])
    pit = materialize(st)
    total = pit["ir_pit"][0]["windows"]
    for row in pit["ir_entity"]:
        assert 1 <= row["windows_seen"] <= total, (
            f"{row['entity_key']}: seen in {row['windows_seen']} of {total} windows")


def test_a_merged_entity_counts_overlapping_windows_once(tmp_path):
    """The mechanism itself, isolated: two spellings of one host over the same window must
    contribute one window, not two."""
    st = _store(tmp_path, [_flow(ALIGNED + 1, "10.80.72.13", "10.80.20.5")])
    counts = {e: seen for e, _last, seen in st.snapshot()["entities"]}
    assert set(counts.values()) == {1}, f"one window, so every entity is seen once: {counts}"


# -- evidence/conclusions split (WORK_ORDER_evidence_plane_snapshots.md step 1) ---

def test_materialize_is_exactly_resolve_of_snapshot_evidence(tmp_path):
    """Step 1's whole acceptance: a pure refactor with byte-identical output.

    Also verified against the REAL ten-day store (58,563 entities, 109,173 edges, 755,567
    identity rows) before landing — same SHA-256 — because a fixture cannot show that the split
    survives ten days of real evidence (`LES-118` rule 2).
    """
    import hashlib
    import json

    from aximprint.ir.materialize import resolve, snapshot_evidence

    st = _basic(tmp_path)
    one = materialize(st, as_of=ALIGNED + WINDOW)
    two = resolve(snapshot_evidence(st, as_of=ALIGNED + WINDOW))

    def digest(pit):
        return hashlib.sha256(json.dumps(pit, sort_keys=True, default=str).encode()).hexdigest()

    assert digest(one) == digest(two)


def test_resolve_is_a_pure_function_of_its_evidence(tmp_path):
    """Same evidence twice must give the same PIT. This is what makes an evidence PIT worth
    freezing at all: if resolution could vary, composing snapshots would not be well-defined."""
    import hashlib
    import json

    from aximprint.ir.materialize import resolve, snapshot_evidence

    st = _basic(tmp_path)
    ev = snapshot_evidence(st, as_of=ALIGNED + WINDOW)

    def digest(pit):
        return hashlib.sha256(json.dumps(pit, sort_keys=True, default=str).encode()).hexdigest()

    assert digest(resolve(ev)) == digest(resolve(ev))


def test_resolve_holds_no_live_store_connection(tmp_path):
    """The property the split exists to establish: what a PIT is derived from is exactly what
    the `EvidenceSnapshot` carries, not a connection it can re-query.

    Proved by CLOSING the store before resolving. If any read had been left below the boundary
    this raises instead of returning a PIT — which is a far stronger check than inspecting the
    dataclass's fields, since a store could hide inside any of them.
    """
    from aximprint.ir.materialize import resolve, snapshot_evidence

    st = _basic(tmp_path)
    ev = snapshot_evidence(st, as_of=ALIGNED + WINDOW)
    st.con.close()

    pit = resolve(ev)
    assert pit["ir_entity"], "resolve must work from frozen evidence alone"


def test_an_empty_span_is_refused_by_the_evidence_reader_not_by_resolve(tmp_path):
    """The refusal belongs on the evidence side: a span with no windows describes nothing, and
    finding that out after resolution would waste the expensive half."""
    from aximprint.ir.materialize import MaterializeError, snapshot_evidence

    st = _basic(tmp_path)
    with pytest.raises(MaterializeError, match="nothing to describe"):
        snapshot_evidence(st, as_of=ALIGNED - 10_000, since=ALIGNED - 20_000)
