"""Streaming zeek ingest: k-way merge, coverage table, conn-state classes.

Synthetic fixtures only — a real capture carries PII and never enters a test
(`LES-003`). Shapes here mirror the reference capture's shapes, not its contents.
"""
import json
import math
import os
import random

import pytest

from aximprint.events import (
    FLOW_ACCEPTED, FLOW_STATE_CLASSES,
    FlowEvent, IdentityEvent, ProfileEvent, SensorHealthEvent, ServiceEvent,
)
from aximprint.ingest.zeek import plugin as zp
from aximprint.ingest.zeek import sources as zs

CONN_FIELDS = ("ts", "id.orig_h", "id.orig_p", "id.resp_h", "id.resp_p", "proto",
               "service", "duration", "orig_bytes", "resp_bytes", "conn_state",
               "orig_pkts", "resp_pkts")
T0 = 1_754_000_000

#: A stem guaranteed never to be a real Zeek log type, for a test that needs "some stem
#: outside `_PARSERS`/`_SKIP`". A REAL unrecognized stem is a landmine here (`LES-086`):
#: `sip` served this role until `docs/WORK_ORDER_zeek_stem_coverage.md` step 3's admit list
#: named it, at which point the test would break with no hint why the moment step 3 lands.
#: Mirrors `tests/acquire/test_zeek_runner.py`'s `_UNKNOWN_STEM`, same value, same reasoning.
_UNKNOWN_STEM = "__not_a_zeek_stem__"

#: Zeek's complete conn_state vocabulary (base/protocols/conn). The routing table
#: claims to be closed over it; "closed" is only a claim until the whole alphabet
#: is driven through, so this list is the test's own definition of complete.
ZEEK_CONN_STATES = ("S0", "S1", "S2", "S3", "SF", "REJ", "RSTO", "RSTR",
                    "RSTOS0", "RSTRH", "SH", "SHR", "OTH")


def _tsv(path, fields, rows):
    """Write a classic-TSV zeek log (the only format the reference capture uses)."""
    head = "#separator \\x09\n#fields\t" + "\t".join(fields)
    body = "\n".join("\t".join(str(c) for c in r) for r in rows)
    path.write_text(head + "\n" + body + "\n")


def _conn_row(ts, sip="10.0.0.21", dip="10.0.0.5", dport=88, state="SF",
              proto="tcp", service="-"):
    return (f"{ts:.6f}", sip, 51000, dip, dport, proto, service,
            "0.200000", 500, 1500, state, 5, 5)


def _plugin(quiet=True):
    p = zp.ZeekPlugin()
    if quiet:
        p.progress_rows = 0  # keep test output clean; also silences the summary
    return p


# -- ingest-plugin CONTRACT (`M7`) -----------------------------------------------
#
# `cli._pipeline` always calls `plugin.events(source, ts_from, ts_to)` — three
# positional arguments — so the ABSTRACT contract (`ingest.base.IngestPlugin`)
# must accept that shape too, independent of any one concrete plugin already
# happening to accept it. This predates `ZeekPlugin` drifting; `ZeekPlugin`
# itself was never the bug (`events(self, source, ts_from=None, ts_to=None)`
# already matched the driver) — the ABSTRACT method had not been widened to
# match, `LES-005`'s "a signature change on one side of a contract is a change
# on both" pointed the wrong direction.

def test_ingest_plugin_contract_accepts_the_drivers_call_shape():
    import inspect

    from aximprint.ingest.base import IngestPlugin

    params = list(inspect.signature(IngestPlugin.events).parameters)
    assert params == ["self", "source", "ts_from", "ts_to"]


def test_zeek_plugin_events_matches_the_widened_base_contract():
    """`ZeekPlugin.events` and `IngestPlugin.events` must accept the identical
    call shape — the concrete override is not allowed to narrow the contract
    even though Python's ABC machinery would not itself catch a narrowing."""
    import inspect

    from aximprint.ingest.base import IngestPlugin

    base_params = inspect.signature(IngestPlugin.events).parameters
    zeek_params = inspect.signature(zp.ZeekPlugin.events).parameters
    assert list(base_params) == list(zeek_params)


# -- streaming k-way merge (Q2) -------------------------------------------------

def test_merge_yields_nondecreasing_ts_across_files(tmp_path):
    """Interleaved timestamps across several logs must come out monotonic."""
    _tsv(tmp_path / "conn.log", CONN_FIELDS,
         [_conn_row(T0 + i * 10) for i in range(20)])
    _tsv(tmp_path / "ntp.log", ("ts", "id.orig_h", "id.orig_p", "id.resp_h",
                                "id.resp_p", "version", "mode", "stratum"),
         [(f"{T0 + 5 + i * 10:.6f}", "10.0.0.21", 123, "10.0.0.9", 123, 4, 3, 2)
          for i in range(20)])
    _tsv(tmp_path / "weird.log", ("ts", "id.orig_h", "id.resp_h", "name"),
         [(f"{T0 + 7 + i * 25:.6f}", "10.0.0.21", "10.0.0.5", "truncated_header")
          for i in range(8)])

    ts = [e.ts for e in _plugin().events(str(tmp_path))]
    assert len(ts) == 48
    assert ts == sorted(ts), "k-way merge must yield non-decreasing ts"


def test_merge_repairs_within_file_disorder(tmp_path):
    """A single log is only approximately sorted; the bounded lookahead repairs it.

    Mirrors the reference capture's shape: conn.log rows are keyed on connection
    start but written at close, so a long flow lands after later-starting ones.
    """
    rows = [_conn_row(T0 + t) for t in (0, 30, 10, 40, 5, 60, 50, 20)]
    _tsv(tmp_path / "conn.log", CONN_FIELDS, rows)

    p = _plugin()
    ts = [e.ts for e in p.events(str(tmp_path))]
    assert ts == sorted(ts)
    assert p.log_stats["conn"].unordered == 0, "8 rows sit well inside the lookahead"


def test_disorder_beyond_lookahead_is_counted_not_hidden(tmp_path):
    """Displacement the buffer cannot repair is reported, never emitted as ordered."""
    rows = [_conn_row(T0 + 100 + i) for i in range(8)] + [_conn_row(T0)]
    _tsv(tmp_path / "conn.log", CONN_FIELDS, rows)

    p = _plugin()
    p.reorder_rows = 2  # far smaller than the 8-row displacement
    out = [e.ts for e in p.events(str(tmp_path))]
    assert out != sorted(out), "fixture must actually exceed the lookahead"
    assert p.log_stats["conn"].unordered == 1


@pytest.mark.parametrize("capacity", [2, 16, 64])
def test_lookahead_boundary_is_exact(tmp_path, capacity):
    """State the edge of the repair claim instead of assuming it holds "somewhere".

    A row displaced by exactly `reorder_rows` is still repairable; one row further
    and the earlier row has already been emitted, so the late row is unrepairable
    — and must then be *counted*, not smuggled out as if it had been in order.
    """
    for displacement, repaired in ((capacity, True), (capacity + 1, False)):
        d = tmp_path / f"disp{displacement}"
        d.mkdir()
        # ascending rows, with the row that belongs first written `displacement` late
        offsets = list(range(1, capacity + 10))
        offsets.insert(displacement, 0)
        _tsv(d / "conn.log", CONN_FIELDS, [_conn_row(T0 + o) for o in offsets])

        p = _plugin()
        p.reorder_rows = capacity
        out = [e.ts for e in p.events(str(d))]
        assert (out == sorted(out)) is repaired, (
            f"displacement {displacement} vs lookahead {capacity}")
        assert p.log_stats["conn"].unordered == (0 if repaired else 1)


def test_lookahead_shortfall_is_named_in_the_run_summary(tmp_path, capsys):
    """Unrepairable disorder has to reach a human, not just an attribute.

    The audit only counts; the summary is where the operator learns the reorder
    buffer was too small for this capture. (It also reaches a *persisted* artifact
    — see `test_unrepairable_disorder_reaches_the_window_health_record`.)
    """
    _tsv(tmp_path / "conn.log", CONN_FIELDS,
         [_conn_row(T0 + 100 + i) for i in range(8)] + [_conn_row(T0)])

    p = zp.ZeekPlugin()
    p.reorder_rows = 2
    p.progress_rows = 10_000  # summary on, per-row chatter off
    list(p.events(str(tmp_path)))

    err = capsys.readouterr().err
    assert "1 row(s) displaced past the" in err
    assert "memory cap" in err, "a cap-forced flush is disclosed as such"


def test_reorder_bound_is_measured_in_seconds_not_rows(tmp_path):
    """The buffer's guarantee is a duration, because the pathology is a duration.

    A row bound protects only `capacity / row_rate` seconds, so the very same
    buffer defends ten times less time at ten times the line rate — the safety
    property weakens exactly as captures grow, against a displacement (a slow flow
    logged at close) that does not shrink with line rate.
    """
    wide_in_rows = tmp_path / "rows"
    wide_in_rows.mkdir()
    # 3000 rows written back-to-front inside a 9-second span: displacement is
    # 3000 ROWS but < 1 horizon of ts, so it is fully repaired.
    _tsv(wide_in_rows / "conn.log", CONN_FIELDS,
         [_conn_row(T0 + 9 - i * 0.003) for i in range(3000)])
    p = _plugin()
    out = [e.ts for e in p.events(str(wide_in_rows))]
    assert out == sorted(out) and p.log_stats["conn"].unordered == 0
    assert p.log_stats["conn"].capped == 0, "well inside the memory backstop"

    wide_in_time = tmp_path / "time"
    wide_in_time.mkdir()
    # Four rows, then one displaced by more than the horizon: few rows, too much
    # time. A row bound would have repaired it and called the stream ordered.
    _tsv(wide_in_time / "conn.log", CONN_FIELDS,
         [_conn_row(T0 + t) for t in (0, 2000, 3000, 4000, 1000)])
    p = _plugin()
    p.reorder_horizon_seconds = 600.0
    out = [e.ts for e in p.events(str(wide_in_time))]
    assert out != sorted(out)
    assert p.log_stats["conn"].unordered == 1
    assert p.log_stats["conn"].capped == 0, "the horizon refused it, not the cap"


def test_memory_cap_flush_is_recorded_as_a_broken_horizon(tmp_path):
    """When the backstop binds, the time guarantee is not being met. Say so."""
    _tsv(tmp_path / "conn.log", CONN_FIELDS,
         [_conn_row(T0 + i) for i in range(20)])
    p = _plugin()
    p.reorder_rows = 2  # far below what the 600s horizon would want to hold
    list(p.events(str(tmp_path)))
    assert p.log_stats["conn"].capped == 18
    assert p.log_stats["conn"].unordered == 0, "an ordered log survives a small cap"


def test_unrepairable_disorder_is_announced_as_a_health_event(tmp_path):
    """The ordering audit must leave the plugin, not just increment an attribute.

    `_LogStats.unordered` only ever reached stderr, so nothing persisted could
    answer "was this window's input ordered?" (§5.4 #5). It rides the event stream
    at the offending row's own ts, so it lands in that row's window.
    """
    _tsv(tmp_path / "conn.log", CONN_FIELDS,
         [_conn_row(T0 + 100 + i) for i in range(8)] + [_conn_row(T0)])
    p = _plugin()
    p.reorder_rows = 2
    events = list(p.events(str(tmp_path)))

    health = [e for e in events if isinstance(e, SensorHealthEvent)]
    assert len(health) == 1 == p.log_stats["conn"].unordered
    (h,) = health
    assert h.kind == "ingest_unordered" and h.detail == "conn"
    assert h.ts == T0, "attributed to the displaced row, not to the run"
    assert h.value > 0, "seconds of displacement, so a reader can size the damage"
    assert events.index(h) < events.index(next(e for e in events if e.ts == T0
                                               and isinstance(e, FlowEvent)))


def test_merge_tie_break_is_deterministic(tmp_path):
    """Equal ts across several logs must resolve identically on every pass.

    This is the property the real-capture determinism run exercises and the unit
    suite otherwise cannot see (`LES-002`): a single run always agrees with itself.
    """
    for name in ("conn", "dce_rpc", "ldap_search", "smb_mapping"):
        if name == "conn":
            _tsv(tmp_path / "conn.log", CONN_FIELDS,
                 [_conn_row(T0) for _ in range(5)])
        else:
            _tsv(tmp_path / f"{name}.log",
                 ("ts", "id.orig_h", "id.orig_p", "id.resp_h", "id.resp_p"),
                 [(f"{T0:.6f}", "10.0.0.21", 40000 + i, "10.0.0.5", 389)
                  for i in range(5)])

    def fingerprint():
        return json.dumps([[type(e).__name__, e.ts, getattr(e, "kind", ""),
                            getattr(e, "port", 0)]
                           for e in _plugin().events(str(tmp_path))])

    assert fingerprint() == fingerprint()


def test_events_is_streaming_not_materialized(tmp_path, monkeypatch):
    """Peak memory is O(open files x lookahead), not O(events).

    Proven by construction: pulling ONE event must not have consumed the file.
    """
    _tsv(tmp_path / "conn.log", CONN_FIELDS,
         [_conn_row(T0 + i) for i in range(20_000)])
    read = {"rows": 0}
    # Patched where the decoder LIVES (`sources`), not where it is consumed:
    # rebinding the name on `plugin` would leave `LogDirSource` calling the real
    # one, and this test would pass while counting nothing.
    real_rows = zs._rows

    def counting(path, stats=None):
        for r in real_rows(path, stats):
            read["rows"] += 1
            yield r

    monkeypatch.setattr(zs, "_rows", counting)
    stream = _plugin().events(str(tmp_path))
    next(stream)
    # Lower bound first: an upper bound alone is satisfied by a patch that went
    # dead (0 <= anything), so it would keep passing while measuring nothing.
    assert 0 < read["rows"] <= zp.REORDER_ROWS + 4
    stream.close()


def test_streaming_bound_holds_across_many_files(tmp_path, monkeypatch):
    """The bound is O(open files x lookahead) — never O(corpus), at any file count.

    The single-file test proves laziness. This one proves the k-way merge does not
    drain one source to fill another, which is what a `list()` hidden inside the
    merge would look like from the outside.
    """
    files, per_file, pulled = 4, 2_000, 100
    _tsv(tmp_path / "conn.log", CONN_FIELDS,
         [_conn_row(T0 + i) for i in range(per_file)])
    for stem in ("dce_rpc", "ldap_search", "smb_mapping"):
        _tsv(tmp_path / f"{stem}.log",
             ("ts", "id.orig_h", "id.orig_p", "id.resp_h", "id.resp_p"),
             [(f"{T0 + i:.6f}", "10.0.0.21", 40000, "10.0.0.5", 389)
              for i in range(per_file)])
    read = {"rows": 0}
    real_rows = zs._rows

    def counting(path, stats=None):
        for r in real_rows(path, stats):
            read["rows"] += 1
            yield r

    monkeypatch.setattr(zs, "_rows", counting)
    p = _plugin()
    p.reorder_rows = 8  # small enough that O(files x lookahead) is unmistakable
    stream = p.events(str(tmp_path))
    for _ in range(pulled):
        next(stream)
    # `>= pulled`: every event yielded came from a row, so this many rows must
    # have been read. Without it the upper bounds below pass on a dead patch.
    assert pulled <= read["rows"] <= files * (p.reorder_rows + 1) + pulled
    assert read["rows"] < files * per_file, "the corpus must never be materialized"
    stream.close()


# -- multi-window log-directory tree ---------------------------------------------
#
# `xtract harvest`'s cold tier writes N per-window Zeek log directories rather
# than one flat directory. `LogDirSource` auto-detects the shape: no matching
# file directly under root, but subdirectories present. `_window_dir` below
# names directories the way `window_id = timeutil.format_instant(window.start)`
# would (an epoch-seconds string, sometimes with a fractional part) purely to
# document the shape; nothing in the merge trusts the name for anything but a
# deterministic discovery order.

def _window_dir(root, name):
    d = root / name
    d.mkdir()
    return d


def test_window_tree_is_detected_when_root_holds_only_subdirectories(tmp_path):
    """No file directly under root, but subdirectories: window-tree mode."""
    w = _window_dir(tmp_path, "1785934195")
    _tsv(w / "conn.log", CONN_FIELDS, [_conn_row(T0)])
    events = list(_plugin().events(str(tmp_path)))
    assert len(events) == 1 and isinstance(events[0], FlowEvent)


def test_root_with_a_direct_file_stays_flat_even_alongside_subdirectories(tmp_path):
    """A file directly under root always means flat mode — never ambiguous.

    The recursive walk flat mode already uses still finds files inside the
    subdirectory too, so nothing is lost; only the window-tree mode's
    per-stem consolidation is skipped.
    """
    _tsv(tmp_path / "conn.log", CONN_FIELDS, [_conn_row(T0)])
    w = _window_dir(tmp_path, "1785934195")
    _tsv(w / "conn.log", CONN_FIELDS, [_conn_row(T0 + 1)])
    ts = [e.ts for e in _plugin().events(str(tmp_path))]
    assert ts == [T0, T0 + 1]


def test_window_tree_merges_across_directories_by_ts_not_directory_name(tmp_path):
    """The trap, minimally: a row physically stored in the LATER-named window
    directory is chronologically the EARLIEST, and must come out first.

    A directory-name sort (or a naive concatenation in that order) would place
    it after everything in the first directory instead.
    """
    w1 = _window_dir(tmp_path, "100")
    _tsv(w1 / "conn.log", CONN_FIELDS,
         [_conn_row(T0 + 50 + i) for i in range(3)])       # T0+50, T0+51, T0+52
    w2 = _window_dir(tmp_path, "200")
    _tsv(w2 / "conn.log", CONN_FIELDS,
         [_conn_row(T0)] + [_conn_row(T0 + 53 + i) for i in range(2)])  # T0, T0+53, T0+54

    p = _plugin()
    ts = [e.ts for e in p.events(str(tmp_path))]
    assert ts == sorted(ts)
    assert ts[0] == T0, "the late-written, chronologically-first row leads"
    assert p.log_stats["conn"].unordered == 0, "a true merge needs no repair here"


def test_window_tree_naive_concatenation_would_have_failed_this_one(tmp_path):
    """Same shape as above, sized so a directory-name concatenation would
    exceed even a generous horizon while a genuine merge needs none at all.

    heapq.merge always picks the smallest AVAILABLE head, so two individually
    ascending per-window streams merge exactly regardless of how far apart
    their values are — a real merge does not care about the horizon size here
    at all. A "sort directories by name and concatenate" implementation
    would, because by the time it reached the second directory's early row it
    would already have flushed everything up to the first directory's tail —
    an apparent displacement of ~50s here, comfortably beyond the tiny
    horizon this test sets specifically so that mistake cannot hide.
    """
    w1 = _window_dir(tmp_path, "100")
    _tsv(w1 / "conn.log", CONN_FIELDS, [_conn_row(T0 + 70 + i) for i in range(51)])
    w2 = _window_dir(tmp_path, "200")
    _tsv(w2 / "conn.log", CONN_FIELDS,
         [_conn_row(T0)] + [_conn_row(T0 + 121 + i) for i in range(2)])

    p = _plugin()
    p.reorder_horizon_seconds = 60.0  # far smaller than the ~50s naive-concat gap
    ts = [e.ts for e in p.events(str(tmp_path))]
    assert ts == sorted(ts)
    assert ts[0] == T0
    assert p.log_stats["conn"].unordered == 0


def test_window_tree_disorder_beyond_horizon_is_flagged_exactly_once(tmp_path):
    """Within-file disorder inside ONE window directory must still surface as
    exactly one `SensorHealthEvent`, never zero (laundered) and never two
    (double-counted by the new per-file pass AND the existing per-stream one).

    Mirrors `test_disorder_beyond_lookahead_is_counted_not_hidden`, now with a
    second window directory contributing MORE `conn` rows to the same stem,
    so the cross-directory merge is genuinely exercised alongside the
    within-file repair.

    `M1` changed what "far smaller than the displacement" means: the inner
    pass's cap is now an AGGREGATE `max(reorder_rows // len(paths), 64)`, not
    `reorder_rows` directly, so a 2-path stem floors at 64 regardless of how
    small `reorder_rows` is set. The prefix here (100 rows) is sized to
    exceed that floor by enough that the inner pass's own cap-forced flushing
    still leaves T0 arriving too late for even the OUTER, per-stream pass
    (`reorder_rows=10` there, unaffected by `M1`) to silently re-repair it —
    both facts checked directly below rather than assumed, since the exact
    margin is arithmetic worth re-verifying, not re-deriving by eye.
    """
    w1 = _window_dir(tmp_path, "100")
    _tsv(w1 / "conn.log", CONN_FIELDS,
         [_conn_row(T0 + 100 + i) for i in range(100)] + [_conn_row(T0)])
    w2 = _window_dir(tmp_path, "200")
    _tsv(w2 / "conn.log", CONN_FIELDS, [_conn_row(T0 + 300 + i) for i in range(5)])

    p = _plugin()
    p.reorder_rows = 10
    events = list(p.events(str(tmp_path)))

    health = [e for e in events
             if isinstance(e, SensorHealthEvent) and e.kind == "ingest_unordered"]
    assert len(health) == 1, "one displaced row is one health event, not two"
    assert p.log_stats["conn"].unordered == 1, "not double-counted by the inner pass"
    (h,) = health
    assert h.ts == T0 and h.detail == "conn"
    # `M2`: the inner pass's OWN cap-forced flushes are real and disclosed,
    # even though they are not what produced `health` above (that came from
    # the outer, per-stem pass re-judging the fully merged sequence).
    assert p.log_stats["conn"].source_capped > 0, (
        "the inner per-window-directory pass's cap genuinely bound here — "
        "if this is 0, the fixture no longer exercises M1's degraded regime "
        "and the test should be re-sized rather than left silently weaker")


def test_window_tree_mixed_width_window_ids_both_discovered(tmp_path):
    """`timeutil.format_instant` can emit `"1785934195"` and
    `"1785934195.000000000"` in the same tree; both must be read, and their
    events still merge on ts rather than on which name sorts first."""
    w1 = _window_dir(tmp_path, "1785934195")
    _tsv(w1 / "conn.log", CONN_FIELDS, [_conn_row(T0 + 1)])
    w2 = _window_dir(tmp_path, "1785934195.000000000")
    _tsv(w2 / "conn.log", CONN_FIELDS, [_conn_row(T0)])

    ts = [e.ts for e in _plugin().events(str(tmp_path))]
    assert sorted(ts) == [T0, T0 + 1]
    assert ts == [T0, T0 + 1], "ts order, not directory-name order"


def test_window_tree_consolidates_to_one_stream_per_stem(tmp_path):
    """`plugin.py`'s top-level merge must see O(stems) streams, never
    O(windows x stems) — the same reasoning `ParquetArchiveSource` already
    applies to its own partitions (module docstring). Directory names must be
    window-id-shaped (`M3`'s positive gate) — a non-numeric name like
    `"win0"` would make the whole tree fall back to flat mode instead."""
    for i in range(6):
        w = _window_dir(tmp_path, f"{1_785_000_000 + i * 300}")
        _tsv(w / "conn.log", CONN_FIELDS, [_conn_row(T0 + i * 10)])

    src = zs.LogDirSource(str(tmp_path))
    streams = src.streams({})
    assert [s.stem for s in streams] == ["conn"]
    assert src.inventory == {"conn": 6}, "inventory counts FILES, not streams"


def test_window_tree_inventory_counts_files_across_all_windows_per_stem(tmp_path):
    for i in range(3):
        w = _window_dir(tmp_path, f"win{i}")
        _tsv(w / "conn.log", CONN_FIELDS, [_conn_row(T0 + i)])
        _tsv(w / "dns.log", DNS_FIELDS, [])
    src = zs.LogDirSource(str(tmp_path))
    src.streams({})
    assert src.inventory == {"conn": 3, "dns": 3}


def test_window_tree_streaming_bound_holds_across_many_window_directories(
        tmp_path, monkeypatch):
    """Peak reads stay O(window directories x the `M1` aggregate share) for
    one pull — INDEPENDENT of how many rows each window directory holds —
    the window-tree analogue of `test_streaming_bound_holds_across_many_files`.

    `per_window` (2,000) is deliberately far above `max_rows` (the `M1`
    floor, 64, since `reorder_rows(8) // n_windows(30)` rounds to 0): before
    `M1`, this exact shape was `test_default_reorder_rows_does_not_bound
    _memory_when_windows_are_short`'s finding generalized — every window's
    ts span (2,000 rows spanning under a minute) sits inside the horizon, so
    only end-of-file released a buffer, and `heapq.merge`'s eager priming
    forced all 30 to fill AT ONCE regardless of the cap. The read count
    measured below (1,972) is independent of `per_window` — confirmed by
    hand across 100/500/2,000 rows/window, all three landing at the SAME
    number — which is the actual property this test exists to pin: not one
    magic bound, but that growing the corpus per window does not move it.
    """
    n_windows, per_window, pulled = 30, 2000, 15
    for w in range(n_windows):
        d = _window_dir(tmp_path, f"{1_785_000_000 + w * 300}")
        _tsv(d / "conn.log", CONN_FIELDS,
             [_conn_row(T0 + w * 300 + i) for i in range(per_window)])

    read = {"rows": 0}
    real_rows = zs._rows

    def counting(path, stats=None):
        for r in real_rows(path, stats):
            read["rows"] += 1
            yield r

    monkeypatch.setattr(zs, "_rows", counting)
    p = _plugin()
    p.reorder_rows = 8
    stream = p.events(str(tmp_path))
    for _ in range(pulled):
        next(stream)
    # `_merged_rows`'s own formula, not a re-derived guess: `M1`'s aggregate
    # share per window, floored — plus slack for the outer, per-stem
    # `_in_order` pass's own fill cost on top (`self.reorder_rows` there,
    # unaffected by `M1`) and a small per-window rounding margin.
    max_rows = max(p.reorder_rows // n_windows, 64)
    assert pulled <= read["rows"] <= n_windows * (max_rows + 10) + pulled
    assert read["rows"] < n_windows * per_window, "the corpus must never be materialized"
    stream.close()


def test_from_to_is_refused_for_a_window_tree_too(tmp_path):
    """A window directory's name is not a sound index to slice on (a
    late-closing flow can land in a later window than the one its ts
    belongs to), so the log-directory refusal covers this shape too."""
    w = _window_dir(tmp_path, "1785934195")
    _tsv(w / "conn.log", CONN_FIELDS, [_conn_row(T0)])
    with pytest.raises(ValueError, match="parquet"):
        list(_plugin().events(str(tmp_path), T0, T0 + 5))


def test_window_tree_empty_root_yields_no_streams(tmp_path):
    assert list(_plugin().events(str(tmp_path))) == []


# -- exactly-once disorder reporting (tester-added: `LES-006` "no evidence" vs
# "evidence of none", stress-testing the D4 fix in
# WORK_ORDER_multi_window_log_read.md) ------------------------------------------

def test_window_tree_disorder_reporting_is_exactly_once_and_lossless(tmp_path):
    """Property, not a hand-predicted count: for ANY window-tree shape, the
    number of `SensorHealthEvent(ingest_unordered)` emitted for "conn" must
    equal the number of places the ACTUAL output ts sequence steps backward
    relative to its own running max — recomputed independently of the
    algorithm's own bookkeeping, so this catches a double-report (the
    original bug: the inner per-file pass and the outer per-stem pass both
    flagging the same displaced row) or a silent UNDER-report (the inner
    pass's throwaway tally swallowing evidence the outer pass then fails to
    rediscover, once its arrival position has been reshuffled by the inner
    repair) with equal sensitivity. Also checks nothing is lost: the
    multiset of output ts equals the multiset fed in.

    Fuzzed across window count, rows per window, `reorder_rows`, and
    `reorder_horizon_seconds`, with each file's rows written in an arbitrary
    (shuffled) physical order — seeded, so a failure is reproducible.
    """
    rng = random.Random(20260810)
    for trial in range(40):
        troot = tmp_path / f"trial{trial}"
        troot.mkdir()
        n_win = rng.randint(1, 6)
        expect_ts = []
        for w in range(n_win):
            base = T0 + w * rng.randint(50, 2000)
            tss = [round(base + rng.uniform(0, 100), 6) for _ in range(rng.randint(1, 12))]
            rng.shuffle(tss)  # arbitrary physical write order within the file
            d = _window_dir(troot, f"w{w}")
            _tsv(d / "conn.log", CONN_FIELDS, [_conn_row(ts) for ts in tss])
            expect_ts += tss

        p = _plugin()
        p.reorder_rows = rng.choice([1, 2, 3, 5, 8, 50])
        p.reorder_horizon_seconds = rng.choice([1.0, 10.0, 100.0, 600.0])
        events = list(p.events(str(troot)))
        flows = [e for e in events if isinstance(e, FlowEvent)]
        health = [e for e in events if isinstance(e, SensorHealthEvent)
                 and e.kind == "ingest_unordered"]

        assert sorted(e.ts for e in flows) == sorted(expect_ts), (
            f"trial {trial}: data lost or fabricated")

        running_max = float("-inf")
        backward = 0
        for e in flows:
            if e.ts < running_max:
                backward += 1
            else:
                running_max = e.ts
        assert backward == len(health) == p.log_stats["conn"].unordered, (
            f"trial {trial}: {backward} true backward step(s) in the output "
            f"but {len(health)} health event(s) reported "
            f"(reorder_rows={p.reorder_rows}, horizon={p.reorder_horizon_seconds})")


@pytest.mark.parametrize("n_windows,stragglers_per_window", [(1, 1), (1, 3), (3, 1), (5, 1)])
def test_window_tree_exactly_once_holds_under_the_write_at_close_pathology(
        tmp_path, n_windows, stragglers_per_window):
    """Companion to `..._exactly_once_and_lossless` above, added after
    independently mutation-testing `M1`/`M2`: reintroducing the ORIGINAL
    double-report bug (making `_PerFileTally.unordered` forward to the real,
    shared counter — exactly what it exists NOT to do, per its own docstring)
    caused ZERO failures across 100+ trials of that test's pure-uniform
    -shuffle fuzz, post-`M1`. Traced to why: a uniformly shuffled small file,
    drained via "evict the current minimum once the buffer exceeds
    `max_rows`", empirically almost never produces a genuine backward step
    unless a SMALL value arrives late enough in PHYSICAL WRITE order that
    something else has ALREADY been evicted ahead of it — and `M1`'s
    aggregate floor (`max(reorder_rows // len(paths), 64)`) means a small
    per-window row count (comfortably under 64, which most of that test's
    trials use) never even reaches that regime. So the INNER, per-file
    pass's own `k < last` branch — the one line `_PerFileTally` exists to
    keep from leaking into the real, shared counter — was going almost
    entirely unexercised by that test despite it passing, a false sense of
    coverage this test closes.

    This instead builds the actual write-at-close pathology directly: each
    window's file is an ascending run comfortably longer than the aggregate
    cap (so cap-forced eviction genuinely happens mid-file, pushing `last`
    up), with `stragglers_per_window` late-closing rows APPENDED AT THE
    PHYSICAL END of the SAME file — logged after everything else, exactly
    like a long-lived flow. Verified against the reintroduced-bug mutation
    directly (not merely by inspection) before being added here: every one
    of several hand-built variations of this shape showed `unordered` become
    exactly DOUBLE the true count under the bug; this parametrization
    generalizes it across a single crowded file, several simultaneous
    stragglers in one file, and disorder spread across independent windows.
    """
    ascending_len = 150  # comfortably above every aggregate cap this test reaches
    expect_ts: list[float] = []
    for w in range(n_windows):
        base = T0 + w * 1000
        ascending = [base + i for i in range(ascending_len)]
        stragglers = [base - 500 - i * 5 for i in range(stragglers_per_window)]
        rows = ascending + stragglers  # stragglers physically LAST, like a late close
        d = _window_dir(tmp_path, str(w))
        _tsv(d / "conn.log", CONN_FIELDS, [_conn_row(ts) for ts in rows])
        expect_ts += rows

    p = _plugin()
    p.reorder_rows = 10  # aggregate cap = max(10 // n_windows, 64) = 64 for all cases here
    events = list(p.events(str(tmp_path)))
    flows = [e for e in events if isinstance(e, FlowEvent)]
    health = [e for e in events
             if isinstance(e, SensorHealthEvent) and e.kind == "ingest_unordered"]

    assert sorted(e.ts for e in flows) == sorted(expect_ts), "data lost or fabricated"
    assert p.log_stats["conn"].source_capped > 0, (
        "fixture must actually exceed the aggregate cap, or this test silently "
        "stops exercising M1's degraded-repair regime")

    running_max = float("-inf")
    backward = 0
    for e in flows:
        if e.ts < running_max:
            backward += 1
        else:
            running_max = e.ts
    expected_incidents = n_windows * stragglers_per_window
    assert backward == expected_incidents, (
        "fixture sanity: every straggler must be a genuine backward step")
    assert backward == len(health) == p.log_stats["conn"].unordered, (
        f"{backward} true backward step(s) but {len(health)} health event(s) and "
        f"unordered={p.log_stats['conn'].unordered} — not exactly-once")


# -- determinism under discovery-order permutation (tester-added: `LES-009`) -----
#
# `scripts/verify_determinism.py` re-runs the SAME input twice, so it cannot see
# an order dependency both runs would share alike — this is the check the
# module docstring promises ("a non-deterministic discovery order would reach
# an output byte") but nothing previously exercised directly.

def test_output_is_invariant_to_directory_listing_order(tmp_path, monkeypatch):
    """Every directory listing this round's merge code performs —
    `root.iterdir()` (implemented via `os.listdir`) and `rglob("*")`
    (routed through `os.scandir`, NOT `Path.iterdir`, on this interpreter —
    confirmed by reading `pathlib`'s own source before writing this test) —
    is piped through `sorted(...)` before use. Perturbing the OS-level
    listing order must not change a single output byte if that holds.
    """
    rng = random.Random(2026081099)
    for w in range(10):
        d = _window_dir(tmp_path, f"{1_785_000_000 + w * 137}")
        tss = [T0 + w * 50 + rng.uniform(-20, 60) for _ in range(rng.randint(1, 6))]
        _tsv(d / "conn.log", CONN_FIELDS, [_conn_row(ts) for ts in tss])

    def fingerprint():
        p = _plugin()
        p.reorder_rows = 3
        events = list(p.events(str(tmp_path)))
        return json.dumps([[type(e).__name__, e.ts, getattr(e, "kind", ""),
                            getattr(e, "value", None)] for e in events])

    baseline = fingerprint()

    real_listdir, real_scandir = os.listdir, os.scandir

    class _ShuffledScandir(list):
        """A list is a valid scandir() stand-in: callers do `with scandir(p)
        as it: list(it)`, so this only needs __enter__/__exit__ added."""
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def shuffled_listdir(path="."):
        names = list(real_listdir(path))
        random.Random(hash(str(path)) ^ 0x5678).shuffle(names)
        return names

    def shuffled_scandir(path="."):
        entries = list(real_scandir(path))
        random.Random(hash(str(path)) ^ 0x1234).shuffle(entries)
        return _ShuffledScandir(entries)

    monkeypatch.setattr(os, "listdir", shuffled_listdir)
    monkeypatch.setattr(os, "scandir", shuffled_scandir)
    perturbed = fingerprint()

    assert perturbed == baseline, (
        "output depends on directory-listing order — a non-deterministic "
        "discovery order would reach an output byte (LES-009)")


# -- the auto-detection boundary (tester-added: fail-loud/fail-closed probes) ----

def test_root_with_direct_log_and_window_dirs_stays_flat_with_no_stream_consolidation(tmp_path):
    """D1's documented choice (`docs/WORK_ORDER_multi_window_log_read.md`): a
    root that holds a matching log file directly ALSO stays flat even when it
    holds window-dir-shaped subdirectories too — correctness holds (every row
    is still found, via the recursive walk, and still merges to non-decreasing
    ts), and window-tree mode's memory-saving stream CONSOLIDATION (one stream
    per stem, not one per file) is skipped in favor of flat mode.

    Originally locked in as a SILENT downgrade (no `notes()` line at all,
    unlike genuine window-tree mode — see
    `test_window_tree_merges_across_directories_by_ts_not_directory_name`) —
    `M3` closed that: `streams()` now emits a note on this branch too, naming
    the shape found and why window-tree consolidation was not applied, so an
    operator who leaves one stray flat log next to what is otherwise a
    harvest cold-tier tree gets a diagnostic instead of nothing.
    """
    _tsv(tmp_path / "conn.log", CONN_FIELDS, [_conn_row(T0 + 5000)])
    for name in ("100", "200", "300"):
        d = _window_dir(tmp_path, name)
        _tsv(d / "conn.log", CONN_FIELDS, [_conn_row(T0)])

    src = zs.LogDirSource(str(tmp_path))
    streams = src.streams(zp.wanted_columns())
    assert [s.stem for s in streams] == ["conn"] * 4, (
        "4 separate 'conn' streams — NOT consolidated to 1, unlike a pure "
        "window tree (test_window_tree_consolidates_to_one_stream_per_stem)")
    (note,) = src.notes()
    assert "flat directory" in note and "4 log file(s)" in note, (
        "the flat-mode choice is now named, not silent (`M3`)")

    # correctness still holds despite the downgrade
    ts = [e.ts for e in _plugin().events(str(tmp_path))]
    assert ts == sorted(ts)


def test_nested_window_dirs_fall_back_to_flat_rather_than_miscounting(tmp_path):
    """A root whose immediate child is a WRAPPER directory (not itself a
    window_id — the true per-pcap-window directories are nested one level
    deeper, under it) used to be silently swept into window-tree mode and
    reported as "1 window directory" — an undercount, since the two REAL
    window directories live under `wrapper`, one level down. `M3`'s positive
    gate closes this a different way than "count more carefully": `wrapper`
    does not parse as a window_id, so the root does not confidently look
    like a window tree AT ALL, and reading falls back to flat mode instead —
    no "N window directories" claim is made, so there is nothing left to
    undercount. Data is still fully found and correctly ordered either way
    (the recursive walk in flat mode is unbounded in depth); only the
    per-stem CONSOLIDATION and the window-tree-shaped note are foregone,
    exactly as `test_root_with_direct_log_and_window_dirs_stays_flat_with_no_stream_consolidation`
    documents for the OTHER way a root can fail the gate.
    """
    wrapper = tmp_path / "wrapper"
    wrapper.mkdir()
    for i, name in enumerate(("100", "200")):
        d = wrapper / name
        d.mkdir()
        _tsv(d / "conn.log", CONN_FIELDS, [_conn_row(T0 + i)])

    src = zs.LogDirSource(str(tmp_path))
    streams = src.streams(zp.wanted_columns())
    assert [s.stem for s in streams] == ["conn", "conn"], (
        "flat mode: one stream per file, not consolidated per stem")
    assert src.inventory == {"conn": 2}, "file-level count, accurate either way"
    (note,) = src.notes()
    assert "wrapper" in note and "flat directory" in note, (
        "names the non-conforming subdirectory and the fallback, rather "
        "than claiming a window-directory count it cannot back up")

    ts = [e.ts for e in _plugin().events(str(tmp_path))]
    assert ts == [T0, T0 + 1], "data is still correctly found and ordered via the recursive walk"


def test_window_tree_lexicographic_name_order_never_leaks_into_row_order(tmp_path):
    """Width-mismatched directory names sort DIFFERENTLY under a plain string
    sort than under numeric value ('10' < '9' lexicographically, though
    9 < 10 numerically) — the sharpest version of the module docstring's "a
    plain string sort of the two does not even reliably recover numeric order
    at that width for every pair". Sized like
    `test_window_tree_naive_concatenation_would_have_failed_this_one`: a naive
    concatenation in EITHER sort order would visibly fail against the tiny
    horizon below, so only a true value-based merge passes.
    """
    d10 = _window_dir(tmp_path, "10")   # sorts FIRST lexicographically
    _tsv(d10 / "conn.log", CONN_FIELDS, [_conn_row(T0 + 70 + i) for i in range(51)])
    d9 = _window_dir(tmp_path, "9")     # sorts SECOND lexicographically, but
                                        # its rows are chronologically EARLIEST
    _tsv(d9 / "conn.log", CONN_FIELDS,
         [_conn_row(T0)] + [_conn_row(T0 + 121 + i) for i in range(2)])

    p = _plugin()
    p.reorder_horizon_seconds = 60.0  # far smaller than the ~50s naive-concat gap
    ts = [e.ts for e in p.events(str(tmp_path))]
    assert ts == sorted(ts)
    assert ts[0] == T0
    assert p.log_stats["conn"].unordered == 0


# -- `_looks_like_window_id`'s positive gate (tester-added: `M3` adversarial probe) --

@pytest.mark.parametrize("name", [
    "1785934195",              # genuine: bare epoch seconds
    "1785934195.000000000",    # genuine: fractional, all-zero
    "1785934195.123456789",    # genuine: fractional, real fraction
    "0",                       # genuine (degenerate): epoch zero
])
def test_window_id_gate_never_rejects_a_plausible_format_instant_shape(name):
    """A false REJECT is the worse failure mode (`M3`'s own reasoning,
    restated in the task that asked for this probe): it would silently
    disable window-tree mode on a genuine harvest tree, with no crash and no
    obviously-wrong output to notice — just a quiet loss of the memory
    -consolidation guarantee. Every shape `timeutil.format_instant` is
    documented to produce must pass.
    """
    assert zs._looks_like_window_id(name) is True


@pytest.mark.parametrize("name", [
    "nan", "NaN", "NAN",                       # not ASCII digits: rejected by the shape check
    "inf", "-inf", "+inf", "Infinity", "-Infinity", "infinity",
    "1e400",                                    # contains 'e': rejected by the shape check too
    "0x10",                                     # float() does not accept hex
    "", " ",                                    # empty / whitespace-only
    "3.14.15",                                  # too many dots
    "1785934195,5",                              # comma decimal separator: not accepted
    "1785934195abc", "abc1785934195",           # trailing/leading garbage
    "win0", "wrapper", "backup", ".git", "__pycache__", "tmp",  # ordinary directory names
    "2026-08-10",                               # ISO date (hyphens): not a float
    "v1.2.3", "lost+found", "100%",
    "①②③", "Ⅷ",                                  # unicode digit-LIKE but not numeric-value chars
])
def test_window_id_gate_correctly_rejects_non_window_id_shapes(name):
    """The companion to the false-reject check above: ordinary directory
    names, and every `nan`/`inf` spelling, must not be swept into
    window-tree mode. All of these are rejected by `_WINDOW_ID_SHAPE` (the
    digit-run-plus-optional-fraction regex) alone, before `math.isfinite` is
    ever reached — see `test_digit_only_overflow_is_rejected_by_isfinite
    _specifically` for the one shape the regex cannot rule out on its own.
    """
    assert zs._looks_like_window_id(name) is False


def test_digit_only_overflow_is_rejected_by_isfinite_specifically():
    """`_WINDOW_ID_SHAPE` alone cannot reject this: a long enough run of
    ASCII digits, with no letters and no more than one `.`, matches the
    shape regex just as validly as a real epoch-seconds string does — but
    `float()` overflows it to `inf`. `math.isfinite` stays as a genuine
    second gate specifically for this case (the task's own instruction: keep
    it if still parsing afterward) — every OTHER non-finite spelling above
    is already rejected by the shape check and never reaches this line, so
    this is the one input that would slip through if `isfinite` were ever
    dropped.
    """
    huge = "9" * 400
    assert zs._WINDOW_ID_SHAPE.fullmatch(huge), "must reach the isfinite check to prove anything"
    assert not math.isfinite(float(huge)), "the fixture must actually overflow"
    assert zs._looks_like_window_id(huge) is False


@pytest.mark.parametrize("name", [
    "-1",              # a real window_id should never be negative (pre-epoch)
    "1e5", "1E5",       # scientific notation: format_instant never emits this shape
    "1_000", "2026_08_10", "1_2_3",  # PEP-515 underscore digit grouping
    "  100  ",          # leading/trailing whitespace
    "+100",              # redundant leading sign
    "٣٤",                # Arabic-Indic digits for "34" -- a real Unicode DECIMAL digit,
                         # accepted by float()'s Unicode-aware parser
])
def test_window_id_gate_rejects_the_previously_documented_false_accepts(name):
    """These were genuine false accepts under the old `float()`-based gate
    (found by adversarial probing: "`float()` is more permissive than it
    looks") — `_looks_like_window_id` used to validate "parses as a finite
    float", which is WEAKER than "is the digit shape `format_instant`
    actually emits": none of these would ever be produced by the real
    harvester, but all of them used to pass.

    The gate was tightened to `_WINDOW_ID_SHAPE` (ASCII digits, optionally
    one `.` and more ASCII digits — nothing else) specifically because of
    this finding (LES-030: a positive gate on a declared shape, not a proxy
    for one — `LES-008`'s error in a different costume). Every one of these
    is now correctly rejected; see
    `test_previously_false_accepted_directory_now_falls_back_to_flat` for
    the realistic one (`"2026_08_10"`) exercised end to end.
    """
    assert zs._looks_like_window_id(name) is False


def test_previously_false_accepted_directory_now_falls_back_to_flat(tmp_path):
    """The realistic collision: an operator's human-dated backup folder
    (`"2026_08_10"`) sitting next to a genuine two-window harvest tree. Under
    the old gate this was swept into window-tree mode and *misreported* as
    "3 window directories" (data was still correct even then — nothing
    downstream of the gate trusts a directory's name for anything but
    discovery order, `LES-013`) — under the tightened gate, `"2026_08_10"`
    no longer looks like a window_id at all, so `M3`'s existing "one
    non-conforming name falls the WHOLE root back to flat" rule applies:
    window-tree mode does not engage, and the note names the actual
    offending directory rather than miscounting.
    """
    for name, ts in (("1785934195", T0 + 5), ("1785934255", T0 + 10)):
        d = _window_dir(tmp_path, name)
        _tsv(d / "conn.log", CONN_FIELDS, [_conn_row(ts)])
    backup = _window_dir(tmp_path, "2026_08_10")
    _tsv(backup / "conn.log", CONN_FIELDS, [_conn_row(T0)])  # unrelated content

    src = zs.LogDirSource(str(tmp_path))
    streams = src.streams(zp.wanted_columns())
    assert len(streams) == 3, "flat mode: one stream per file, not consolidated"
    (note,) = src.notes()
    assert "2026_08_10" in note and "flat directory" in note, (
        "names the actual non-conforming directory, claims no window-directory "
        "count at all rather than a wrong one")

    ts_out = [e.ts for e in _plugin().events(str(tmp_path))]
    assert ts_out == sorted(ts_out) == [T0, T0 + 5, T0 + 10], (
        "data is still correctly found and ordered via the recursive walk")


def test_single_file_locator_produces_a_note(tmp_path):
    """A gap in `M3`'s "every branch says what shape it found and why"
    claim: `streams()` used to return immediately on `self.root.is_file()`
    (`--source` pointing at one log file directly, rather than a directory)
    BEFORE reaching any of the branches that append to `self._notes` — so
    this shape, alone among every branch `streams()` can take, was silent.
    Fixed: the single-file branch now names its own shape too.
    """
    f = tmp_path / "conn.log"
    _tsv(f, CONN_FIELDS, [_conn_row(T0)])
    src = zs.LogDirSource(str(f))
    src.streams(zp.wanted_columns())
    (note,) = src.notes()
    assert "single log file" in note


# -- inventory_is_complete varies PER INSTANCE, by locator shape (code review `M1`) --
#
# `LogDirSource` is one class with (at least) five `streams()` branches, and
# completeness is a property of which SHAPE a given instance's `streams()` call
# actually found, not a fact true of the class -- a single-file locator can never
# be complete (structurally one stem), while every branch that does a recursive
# filesystem walk (flat, empty, the not-a-window-tree fallback, window-tree) is.

def test_single_file_locator_cannot_claim_a_complete_inventory(tmp_path):
    """The exact measurement from code review `M1`: a single-file locator
    reported `unparsed_log_types: []` in every window of a real run — a false
    "every stem accounted for" for a locator that structurally never looked at
    the other 17 files sitting beside it in the same directory. `conn.log`
    here has an unparsed sibling on disk (`_UNKNOWN_STEM`, never a real stem
    name — `LES-086`) that this locator never opens."""
    (tmp_path / f"{_UNKNOWN_STEM}.log").write_text("")  # never seen by a single-file locator
    f = tmp_path / "conn.log"
    _tsv(f, CONN_FIELDS, [_conn_row(T0)])

    src = zs.LogDirSource(str(f))
    src.streams(zp.wanted_columns())
    assert src.inventory_is_complete is False

    p = _plugin()
    list(p.events(str(f)))
    assert p.unparsed_stems is None, (
        "must be unaudited, not a false {} claiming reporter.log was accounted for")


def test_flat_directory_locator_claims_a_complete_inventory(tmp_path):
    _tsv(tmp_path / "conn.log", CONN_FIELDS, [_conn_row(T0)])
    src = zs.LogDirSource(str(tmp_path))
    src.streams(zp.wanted_columns())
    assert src.inventory_is_complete is True


def test_empty_directory_locator_claims_a_complete_inventory(tmp_path):
    """Audited and found nothing is still audited — the recursive walk DID
    run, it simply found no log file and no subdirectory."""
    src = zs.LogDirSource(str(tmp_path))
    assert src.streams(zp.wanted_columns()) == []
    assert src.inventory_is_complete is True


def test_not_a_window_tree_fallback_locator_claims_a_complete_inventory(tmp_path):
    """The "subdirectories present, but not every name is a window_id" branch
    falls back to a flat, recursive read — the SAME walk the genuine flat
    branch does, so it is complete for the identical reason."""
    w = _window_dir(tmp_path, "1785934195")
    _tsv(w / "conn.log", CONN_FIELDS, [_conn_row(T0)])
    backup = _window_dir(tmp_path, "not_a_window_id")
    _tsv(backup / "conn.log", CONN_FIELDS, [_conn_row(T0 + 1)])

    src = zs.LogDirSource(str(tmp_path))
    src.streams(zp.wanted_columns())
    assert src.inventory_is_complete is True


def test_window_tree_locator_claims_a_complete_inventory(tmp_path):
    w = _window_dir(tmp_path, "1785934195")
    _tsv(w / "conn.log", CONN_FIELDS, [_conn_row(T0)])
    src = zs.LogDirSource(str(tmp_path))
    src.streams(zp.wanted_columns())
    assert src.inventory_is_complete is True


def test_inventory_is_complete_resets_across_repeated_streams_calls_on_one_instance(tmp_path):
    """Same discipline `inventory`/`notes()` already have (`streams()`'s own
    comment): a verdict from an EARLIER call on this instance must not survive
    into a later, differently-shaped call. Ordered FLAT-then-single-file
    deliberately, not the reverse: the single-file branch has no explicit
    `self.inventory_is_complete = False` assignment of its own (`False` is
    already the reset default, so restating it would be redundant) — which
    means this exact order is the one that actually exercises the reset. The
    reverse order would pass even with the reset deleted, because the flat
    branch DOES assign explicitly and would overwrite a stale `False` either way.
    """
    flat_dir = tmp_path / "flat"
    flat_dir.mkdir()
    _tsv(flat_dir / "conn.log", CONN_FIELDS, [_conn_row(T0)])
    src = zs.LogDirSource(str(flat_dir))
    src.streams(zp.wanted_columns())
    assert src.inventory_is_complete is True

    f = tmp_path / "conn.log"
    _tsv(f, CONN_FIELDS, [_conn_row(T0)])
    src.root = f
    src.streams(zp.wanted_columns())
    assert src.inventory_is_complete is False, (
        "must not still read True from the earlier, flat-directory call")


# -- memory at realistic fan-out (tester-added: `Q18`, "reasoned, not measured") -

def test_default_reorder_rows_does_not_bound_memory_when_windows_are_short(
        tmp_path, monkeypatch):
    """`Q18` (closed): this regime is now a KNOWN, DISCLOSED consequence of
    `M1`'s aggregate budget, not an open question — but for THESE numbers it
    still fully materializes, honestly, and this test now says why rather
    than merely pinning that it does.

    `M1` bounds a stem's inner reorder buffers to an AGGREGATE
    `max(reorder_rows // n_windows, 64)` per window directory, replacing the
    unbounded-per-substream design that measured 670 MB at 300 x 2,000.
    Here `reorder_rows` is left at its DEFAULT (8192, the production
    configuration) against `n_windows=40`: `8192 // 40 = 204`, comfortably
    ABOVE `per_window` (150) — so the aggregate budget genuinely exceeds this
    whole corpus (40 x 150 = 6,000 < 8,192), and holding all of it is the
    CORRECT, intended consequence of that fact, not a bug `M1` left behind. A
    single FLAT file of the same 6,000-row size would do exactly the same
    thing against the same default (see `test_events_is_streaming_not
    _materialized`, which uses a 20,000-row fixture specifically so
    `REORDER_ROWS` cannot swallow it whole) — this test's finding is that
    window-tree mode inherits that property too, via `heapq.merge`'s eager
    priming forcing every one of the 40 buffers to fill AT ONCE rather than
    one at a time, which a flat directory's single buffer never has to do.

    `source_capped == 0` is the check that tells the two regimes apart: this
    materialization is NOT a cap-forced, lossy-repair degradation (`M1`'s
    accepted trade-off, see `test_window_tree_disorder_beyond_horizon_is
    _flagged_exactly_once`) — nothing here needed to be forced early, there
    was simply enough budget to hold it all, honestly, the same way a
    generously-sized flat-directory buffer would.

    A harvester whose per-window row counts run higher relative to
    `reorder_rows / n_windows` moves OUT of this regime and into `M1`'s
    bounded one instead — demonstrated at `test_window_tree_streaming
    _bound_holds_across_many_window_directories`, using the identical
    `_rows()`-read-counting technique, sized so the aggregate genuinely binds.
    """
    n_windows, per_window, win_secs = 40, 150, 60
    for w in range(n_windows):
        d = _window_dir(tmp_path, f"{1_785_000_000 + w * win_secs}")
        _tsv(d / "conn.log", CONN_FIELDS,
             [_conn_row(T0 + w * win_secs + i * (win_secs / per_window))
              for i in range(per_window)])

    read = {"rows": 0}
    real_rows = zs._rows

    def counting(path, stats=None):
        for r in real_rows(path, stats):
            read["rows"] += 1
            yield r

    monkeypatch.setattr(zs, "_rows", counting)
    p = _plugin()  # DEFAULT reorder_rows (8192) — the production configuration
    stream = p.events(str(tmp_path))
    next(stream)
    total = n_windows * per_window
    assert read["rows"] == total, (
        f"expected full materialization ({total} rows) before the first "
        f"event, because reorder_rows({p.reorder_rows}) // n_windows"
        f"({n_windows}) = {p.reorder_rows // n_windows} exceeds per_window "
        f"({per_window}) — the aggregate budget legitimately covers this "
        f"whole corpus; got {read['rows']} of {total}")
    stream.close()
    assert p.log_stats["conn"].source_capped == 0, (
        "this materialization must be the HONEST kind — the budget was "
        "never exceeded, so nothing was cap-forced early; a non-zero count "
        "here would mean this fixture drifted into M1's degraded regime "
        "without the test noticing")


def test_reorder_by_key_bypasses_buffering_for_an_unorderable_key():
    """`key(item) is None` must not touch the running max or be counted as
    disorder — a malformed row must not make well-ordered neighbours look
    catastrophically displaced (see `reorder_by_key`'s own docstring).

    An unorderable item is released the instant it is seen rather than
    waiting in the buffer like everything else, so it can legitimately emerge
    before an EARLIER, still-buffered orderable item — that is fine, since a
    row this function cannot order is exactly the row a downstream parser is
    about to drop anyway; only its effect on the ORDERABLE items is checked.
    """

    class _Tally:
        unordered = 0
        capped = 0

    def key(item):
        return None if item == "bad" else float(item)

    tally = _Tally()
    out = list(zs.reorder_by_key(
        [10.0, "bad", 11.0, 12.0], key, horizon=600.0, max_rows=100, stats=tally))

    assert {item for item, _ in out} == {10.0, "bad", 11.0, 12.0}, "nothing lost"
    assert next(d for item, d in out if item == "bad") is None, (
        "an unorderable item never carries a displacement")
    numeric = [item for item, _ in out if item != "bad"]
    assert numeric == sorted(numeric), "orderable items still come out sorted"
    assert tally.unordered == 0, "an unorderable item is never counted as disorder"


# -- conn-state coverage --------------------------------------------------------

@pytest.mark.parametrize("state,klass", [
    ("SF", "established"), ("S1", "established"),
    ("RSTR", "reset_resp"), ("RSTO", "reset_orig"),
    ("REJ", "refused"), ("S0", "attempted"), ("SH", "attempted"),
    ("OTH", "other"), ("SHR", "partial"),
    ("NOT_A_ZEEK_STATE", "unknown"),
])
def test_conn_state_classes(tmp_path, state, klass):
    _tsv(tmp_path / "conn.log", CONN_FIELDS, [_conn_row(T0, state=state)])
    (ev,) = list(_plugin().events(str(tmp_path)))
    assert isinstance(ev, FlowEvent)
    assert ev.state == state, "the sensor's own word is preserved verbatim"
    assert ev.state_class == klass
    assert ev.state_class in FLOW_STATE_CLASSES


def test_every_zeek_conn_state_maps_to_a_declared_class(tmp_path):
    """The table calls itself closed; drive Zeek's whole alphabet through it.

    A token missing from the table does not raise — it becomes "unknown" and is
    only visible later, in a node's state_mix. So the alphabet is checked here,
    where a gap is a red test rather than a quietly mislabelled 40% of traffic.
    """
    _tsv(tmp_path / "conn.log", CONN_FIELDS,
         [_conn_row(T0 + i, state=s) for i, s in enumerate(ZEEK_CONN_STATES)])
    events = list(_plugin().events(str(tmp_path)))

    assert len(events) == len(ZEEK_CONN_STATES)
    for ev, state in zip(events, ZEEK_CONN_STATES):
        assert ev.state == state
        assert ev.state_class in FLOW_STATE_CLASSES
        assert ev.state_class != "unknown", f"{state} is real Zeek, not a fall-through"

    assert set(zp._CONN_STATE_CLASS) == set(ZEEK_CONN_STATES), "no invented states either"
    assert set(zp._CONN_STATE_CLASS.values()) <= set(FLOW_STATE_CLASSES)
    assert FLOW_ACCEPTED <= set(FLOW_STATE_CLASSES)
    assert set(FLOW_STATE_CLASSES) - set(zp._CONN_STATE_CLASS.values()) == {"unknown"}, (
        "every declared class must be reachable from a real sensor state, "
        "except 'unknown' which only the fall-through produces")


def test_unset_conn_state_is_unknown_and_proves_nothing(tmp_path):
    """`-` is absence of evidence; it must not borrow a neighbouring class's meaning."""
    _tsv(tmp_path / "conn.log", CONN_FIELDS, [_conn_row(T0, state="-")])
    (ev,) = list(_plugin().events(str(tmp_path)))
    assert ev.state == "" and ev.state_class == "unknown"
    assert ev.state_class not in FLOW_ACCEPTED, "unknown must never manufacture a listener"


def test_reset_states_are_distinct_from_established_and_attempted(tmp_path):
    """`RSTR`/`RSTO` land in neither v0 bucket; they must be their own classes."""
    _tsv(tmp_path / "conn.log", CONN_FIELDS,
         [_conn_row(T0 + i, state=s) for i, s in enumerate(("SF", "RSTR", "RSTO", "S0"))])
    classes = [e.state_class for e in _plugin().events(str(tmp_path))]
    assert classes == ["established", "reset_resp", "reset_orig", "attempted"]
    assert len(set(classes)) == 4


# -- log-type coverage ----------------------------------------------------------

def _write_all_new_types(tmp_path):
    _tsv(tmp_path / "dce_rpc.log",
         ("ts", "id.orig_h", "id.orig_p", "id.resp_h", "id.resp_p", "rtt",
          "named_pipe", "endpoint", "operation"),
         [(f"{T0:.6f}", "10.0.0.21", 40000, "10.0.0.5", 135, "0.001",
           "1025", "epmapper", "ept_map")])
    _tsv(tmp_path / "ldap_search.log",
         ("ts", "id.orig_h", "id.orig_p", "id.resp_h", "id.resp_p", "scope",
          "base_object", "result_count", "result", "filter", "diagnostic_message",
          "attributes"),
         [(f"{T0 + 1:.6f}", "10.0.0.21", 40001, "10.0.0.5", 389, "sub",
           "dc=example", 3, "success", "(objectClass=*)", "0000208D: NameErr",
           "cn,mail")])
    _tsv(tmp_path / "smb_mapping.log",
         ("ts", "id.orig_h", "id.orig_p", "id.resp_h", "id.resp_p", "path",
          "service", "native_file_system", "share_type"),
         [(f"{T0 + 2:.6f}", "10.0.0.21", 40002, "10.0.0.7", 445, "-", "-", "-", "DISK")])
    _tsv(tmp_path / "smb_files.log",
         ("ts", "id.orig_h", "id.orig_p", "id.resp_h", "id.resp_p", "action",
          "path", "name", "size"),
         [(f"{T0 + 3:.6f}", "10.0.0.21", 40003, "10.0.0.7", 445,
           "SMB::FILE_OPEN", "-", "secret-doc.xlsx", 4096)])
    _tsv(tmp_path / "ntp.log",
         ("ts", "id.orig_h", "id.orig_p", "id.resp_h", "id.resp_p", "version",
          "mode", "stratum", "poll"),
         [(f"{T0 + 4:.6f}", "10.0.0.21", 123, "10.0.0.9", 123, 4, 3, 2, "64.000000")])
    _tsv(tmp_path / "known_services.log",
         ("ts", "host", "port_num", "port_proto", "service"),
         [(f"{T0 + 5:.6f}", "10.0.0.5", 389, "tcp", "ldap_tcp")])
    _tsv(tmp_path / "software.log",
         ("ts", "host", "host_p", "software_type", "name", "version.major",
          "version.minor", "unparsed_version"),
         [(f"{T0 + 5.5:.6f}", "10.0.0.21", 0, "HTTP::BROWSER", "Chrome", 120, 0,
           "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0")])
    _tsv(tmp_path / "known_hosts.log", ("ts", "host"), [(f"{T0 + 6:.6f}", "10.0.0.5")])
    _tsv(tmp_path / "stats.log",
         ("ts", "peer", "mem", "pkts_proc", "pkts_dropped", "pkt_lag"),
         [(f"{T0 + 7:.6f}", "worker-1", 512, 90_000, 1_200, "0.5")])


def test_each_new_log_type_produces_its_event(tmp_path):
    _write_all_new_types(tmp_path)
    events = list(_plugin().events(str(tmp_path)))

    services = {e.kind: e for e in events if isinstance(e, ServiceEvent)}
    assert set(services) == {"dce_rpc", "ldap", "smb", "smb_files", "ntp",
                             "known_service", "software"}
    assert services["dce_rpc"].port == 135
    assert services["dce_rpc"].attrs["endpoint"] == "epmapper"
    # `base_object`/`filter`/`diagnostic_message`/`attributes`, `smb_files`' `name` and `ntp`'s
    # `poll` used to be dropped here "by design" — reversed under forensic completeness
    # (CONVENTIONS §3, CHANGELOG 2026-08-12; `docs/WORK_ORDER_zeek_stem_coverage.md` build
    # step 2b). None of it reaches a feature yet: all three kinds stay projection-declined
    # (`_SERVICE_NOT_PROJECTED`), so it rides the event only.
    assert services["ldap"].attrs == {
        "scope": "sub", "result": "success", "result_count": "3",
        "base_object": "dc=example", "filter": "(objectClass=*)",
        "diagnostic_message": "0000208D: NameErr", "attributes": "cn,mail",
    }
    assert services["smb"].attrs == {"share_type": "DISK"}, "unset fields stay out"
    assert services["smb_files"].attrs == {"action": "SMB::FILE_OPEN",
                                           "name": "secret-doc.xlsx"}
    assert services["ntp"].attrs == {"version": "4", "mode": "3", "stratum": "2",
                                     "poll": "64.000000"}
    assert services["known_service"].port == 389

    identities = [e for e in events if isinstance(e, IdentityEvent)]
    assert [e.kind for e in identities] == ["known_host"]
    assert identities[0].ip == "10.0.0.5"

    health = {e.kind: e.value for e in events if isinstance(e, SensorHealthEvent)}
    # `pkts_proc` joined the loop this round (code review addendum, user-ruled): the
    # fixture's `stats.log` row already carried it, unread until now.
    assert health == {"pkts_dropped": 1200.0, "pkt_lag": 0.5, "pkts_proc": 90_000.0}


def test_vector_field_from_json_lines_does_not_crash_s(tmp_path):
    """`attributes` (Zeek `vector[string]`) arrives as a native JSON list on the JSON-lines
    transport, not the TSV path's own comma-joined string. `_s()` used to test
    `v in _UNSET` directly, which raises `TypeError: unhashable type: 'list'` on a list —
    a crash on a perfectly conforming log, since `sources.py`'s own module docstring
    advertises TSV *and* JSON-lines as first-class inputs. `known_services.service`
    (`set[string]`) was already exposed to this pre-existing hazard; `ldap_search`'s
    `attributes` (collected since `docs/WORK_ORDER_zeek_stem_coverage.md` build step 2b) is
    the second field to reach it, which is what surfaced the fix.

    Also checks TRANSPORT EQUIVALENCE at the single-field level: the JSON-lines list must
    produce the exact string a TSV row's own comma-joined `attributes` cell would have (the
    assertion in `test_each_new_log_type_produces_its_event` above, `"cn,mail"`) — not
    merely "does not raise".
    """
    (tmp_path / "ldap_search.log").write_text(json.dumps({
        "ts": T0, "id.orig_h": "10.0.0.21", "id.orig_p": 40001,
        "id.resp_h": "10.0.0.5", "id.resp_p": 389, "scope": "sub",
        "attributes": ["cn", "mail"],
    }) + "\n")
    (ev,) = list(_plugin().events(str(tmp_path)))
    assert ev.attrs == {"scope": "sub", "attributes": "cn,mail"}


DNS_FIELDS = ("ts", "id.orig_h", "id.resp_h", "query", "qtype_name", "answers", "TTLs")


def test_dns_binds_only_parseable_addresses(tmp_path):
    """A binding is an identity claim: parse the address, never guess at its shape.

    `a.count(".") == 3` reads any four-label name as an IPv4 address, so CNAME /
    MX / SRV answers were bound as `ip` and reached the store as *observed*
    identity assertions. On the reference capture that was 34.4% of dns-derived
    bindings, and with dhcp.log absent dns is the primary identity source.
    """
    _tsv(tmp_path / "dns.log", DNS_FIELDS,
         [(f"{T0:.6f}", "10.0.0.21", "10.0.0.5", "svc.corp.example", "A",
           "10.0.0.7,cdn.edge.example.net,2001:db8::1,mail.corp.example.org",
           "60,60,60,60")])
    events = list(_plugin().events(str(tmp_path)))

    assert all(isinstance(e, IdentityEvent) and e.kind == "dns" for e in events)
    assert [e.ip for e in events] == ["10.0.0.7", "2001:db8::1"]
    assert all(e.hostname == "svc.corp.example" for e in events)


@pytest.mark.parametrize("answer", [
    "cdn.edge.example.net",     # four labels: the exact shape the heuristic accepted
    "mail.corp.example.org",
    "10.0.0.7.",                # trailing dot: four dots, still not an address
    "999.1.1.1",                # dotted quad, out of range
    "010.1.1.1",                # leading zeros: ambiguous, so refused
    "not an address",
])
def test_dns_non_address_answers_bind_nothing(tmp_path, answer):
    _tsv(tmp_path / "dns.log", DNS_FIELDS,
         [(f"{T0:.6f}", "10.0.0.21", "10.0.0.5", "svc.corp.example", "CNAME",
           answer, "60")])
    assert list(_plugin().events(str(tmp_path))) == []


# -- parse hygiene: an unrecognized shape is rejected, not defaulted through -----

def test_truncated_row_is_skipped_and_counted(tmp_path):
    """`zip` stops at the shorter side, so a short row yields a complete-looking event.

    Routine on the last line of a killed writer: real endpoints, manufactured
    zeros, `state_class` quietly `unknown`.
    """
    head = "#separator \\x09\n#fields\t" + "\t".join(CONN_FIELDS)
    good = "\t".join(str(c) for c in _conn_row(T0))
    truncated = "\t".join(str(c) for c in _conn_row(T0 + 1)[:6])
    (tmp_path / "conn.log").write_text(f"{head}\n{good}\n{truncated}\n")

    p = _plugin()
    events = list(p.events(str(tmp_path)))
    assert len(events) == 1 and events[0].ts == T0
    assert p.log_stats["conn"].malformed == 1
    assert p.log_stats["conn"].rows == 1, "a skipped row is not a parsed row"


def test_malformed_rows_are_named_in_the_run_summary(tmp_path, capsys):
    """A dropped row is a coverage fact; it has to reach the operator."""
    head = "#separator \\x09\n#fields\t" + "\t".join(CONN_FIELDS)
    good = "\t".join(str(c) for c in _conn_row(T0))
    truncated = "\t".join(str(c) for c in _conn_row(T0 + 1)[:6])
    (tmp_path / "conn.log").write_text(f"{head}\n{good}\n{truncated}\n")

    p = zp.ZeekPlugin()
    p.progress_rows = 10_000  # summary on, per-row chatter off
    list(p.events(str(tmp_path)))
    assert "1 malformed row(s) skipped" in capsys.readouterr().err


@pytest.mark.parametrize("ts", ["nan", "inf", "-inf"])
def test_non_finite_ts_is_rejected_at_the_plugin(tmp_path, ts):
    """`_f("nan") -> nan` and every `if not ts` guard accepts it: NaN is truthy.

    It then raises deep inside an accumulator, with nothing left to name the file.
    """
    row = (ts,) + _conn_row(T0)[1:]
    _tsv(tmp_path / "conn.log", CONN_FIELDS, [row])

    p = _plugin()
    assert list(p.events(str(tmp_path))) == []
    assert p.log_stats["conn"].malformed == 1


def test_unset_field_markers_are_read_from_the_header(tmp_path):
    """`#unset_field`/`#empty_field` are declared per file, exactly like `#separator`.

    Hardcoding Zeek's defaults leaks the literal marker through as a value under a
    non-default config — an unset `service` becoming a protocol label downstream.
    """
    head = "\n".join(["#separator \\x09", "#unset_field\t(unset)",
                      "#empty_field\t(none)", "#fields\t" + "\t".join(CONN_FIELDS)])
    row = _conn_row(T0, service="(unset)", state="(unset)")
    (tmp_path / "conn.log").write_text(
        head + "\n" + "\t".join(str(c) for c in row) + "\n")

    (ev,) = list(_plugin().events(str(tmp_path)))
    assert ev.service == "", "the marker is absence, never a value"
    assert ev.state == "" and ev.state_class == "unknown"


def test_unset_capture_loss_emits_no_event(tmp_path):
    """An unset `percent_lost` would manufacture an observed 0.0%-loss event."""
    _tsv(tmp_path / "capture_loss.log", ("ts", "ts_delta", "peer", "gaps", "acks",
                                         "percent_lost"),
         [(f"{T0:.6f}", "900.0", "worker-1", 0, 0, "-"),
          (f"{T0 + 1:.6f}", "900.0", "worker-1", 3, 100, "2.5")])
    events = list(_plugin().events(str(tmp_path)))
    assert [(e.kind, e.value) for e in events] == [("capture_loss", 2.5)]


@pytest.mark.parametrize("state,imputed", [
    ("S0", False),    # never answered: no bytes is the observation
    ("REJ", False),   # refused: likewise
    ("OTH", True),    # midstream — the sensor did not see the payload, 0 understates
    ("SHR", True),
    ("SF", True),     # established with unset counters: not something to read as 0
])
def test_unset_conn_counters_are_marked_imputed_unless_zero_is_honest(
        tmp_path, state, imputed):
    """`0` is only the honest reading when nothing was ever exchanged."""
    row = (f"{T0:.6f}", "10.0.0.21", 51000, "10.0.0.5", 445, "tcp", "-",
           "-", "-", "-", state, "-", "-")
    _tsv(tmp_path / "conn.log", CONN_FIELDS, [row])
    (ev,) = list(_plugin().events(str(tmp_path)))
    assert ev.bytes_out == 0 and ev.duration == 0.0
    assert ev.counters_imputed is imputed


def test_fully_set_conn_counters_are_never_marked_imputed(tmp_path):
    _tsv(tmp_path / "conn.log", CONN_FIELDS, [_conn_row(T0, state="OTH")])
    (ev,) = list(_plugin().events(str(tmp_path)))
    assert ev.counters_imputed is False


def test_unset_sensor_counters_emit_no_health_event(tmp_path):
    """`-` means unknown. A 0.0 here would be an imputed 'clean' read as observed.

    Every counter `_stats` reads (`pkts_dropped`/`pkt_lag`/`pkts_proc`/`bytes_recv`) is
    unset here, including `pkts_proc`/`bytes_recv` (joined `_stats`'s loop this round) --
    `mem` stays populated to prove an UNRELATED, unread column being set does not somehow
    manufacture an event either.
    """
    _tsv(tmp_path / "stats.log",
         ("ts", "peer", "mem", "pkts_proc", "bytes_recv", "pkts_dropped", "pkt_lag"),
         [(f"{T0:.6f}", "worker-1", 512, "-", "-", "-", "-")])
    assert list(_plugin().events(str(tmp_path))) == []


def test_skipped_types_are_skipped_by_name(tmp_path, capsys):
    """Skipping is a named decision, not an accidental fall-through.

    Iterates `zp._SKIP` itself rather than a hardcoded tuple of its current members: a
    hardcoded copy would keep passing, silently exercising fewer stems than `_SKIP` actually
    names, the moment `docs/WORK_ORDER_zeek_stem_coverage.md` D3 adds to it (e.g. `analyzer`/
    `reporter`) -- under-delivering quietly is worse than a loud break (`LES-020`'s shape).
    """
    skip_stems = sorted(zp._SKIP)
    for stem in skip_stems:
        _tsv(tmp_path / f"{stem}.log", ("ts", "peer", "value"),
             [(f"{T0:.6f}", "worker-1", 1)])
    _tsv(tmp_path / "conn.log", CONN_FIELDS, [_conn_row(T0)])

    p = zp.ZeekPlugin()
    events = list(p.events(str(tmp_path)))
    assert len(events) == 1 and isinstance(events[0], FlowEvent)
    assert set(p.log_stats) == {"conn"}, "skipped logs are never read"

    err = capsys.readouterr().err
    for stem in skip_stems:
        assert f"skip {stem}" in err, "each skip is announced by name"
    # A bare `"UNRECOGNIZED" not in err` cannot fail here BY CONSTRUCTION, whatever the
    # code does: every stem this fixture writes is drawn from `zp._SKIP`'s own keys, so
    # there is no fixture stem the unrecognized branch could ever see -- that branch, and
    # its own test, live at `test_unrecognized_log_type_is_reported_and_not_parsed`. What
    # IS load-bearing here: exactly one skip announcement per `_SKIP` entry, counted by
    # matching the line PREFIX `_say` always writes (`"[zeek] skip "`) rather than a raw
    # substring anywhere in `err` -- a raw `err.count("skip ")` over-counts the moment a
    # reason string uses the word in its own prose (`_SKIP["pe"]`: "refines files' own
    # skip", `_SKIP["analyzer"]`: "this skip also has a named path back"). This DOES catch
    # a stem misfiled into BOTH `_PARSERS` and `_SKIP`: it would then parse instead of
    # skip, dropping its line and undercounting here (on top of already breaking the
    # `log_stats` assertion above).
    skip_line_count = sum(1 for ln in err.splitlines() if ln.startswith("[zeek] skip "))
    assert skip_line_count == len(skip_stems), (
        f"expected exactly {len(skip_stems)} skip announcement(s), one per _SKIP entry; "
        f"got {skip_line_count}")


def test_unrecognized_log_type_is_reported_and_not_parsed(tmp_path, capsys):
    """Fail loud, fail closed: an unmapped log type is named, and left unparsed.

    `_UNKNOWN_STEM`, not a real stem name (`sip` used to sit here, but `sip` is on
    `docs/WORK_ORDER_zeek_stem_coverage.md` step 3's admit list -- the moment that step
    lands, `sip` moves into `_PARSERS` and this test would break with no hint why, the
    exact `LES-086` landmine D1 already fixed four instances of one file over).
    """
    _tsv(tmp_path / f"{_UNKNOWN_STEM}.log", ("ts", "id.orig_h", "id.resp_h"),
         [(f"{T0:.6f}", "10.0.0.21", "10.0.0.5")])

    p = zp.ZeekPlugin()
    assert list(p.events(str(tmp_path))) == []
    err = capsys.readouterr().err
    assert "UNRECOGNIZED" in err and f"'{_UNKNOWN_STEM}'" in err
    assert _UNKNOWN_STEM not in zp._PARSERS and _UNKNOWN_STEM not in zp._SKIP


def test_every_parser_and_skip_entry_is_disjoint():
    """One routing table, no stem in both halves."""
    assert not set(zp._PARSERS) & set(zp._SKIP)


# -- unparsed_stems: the source-level inventory `unparsed_log_types` is built from --
# `docs/WORK_ORDER_zeek_stem_coverage.md` D5, build step 4.

def test_unparsed_stems_is_none_until_events_is_iterated(tmp_path):
    """`IngestPlugin.unparsed_stems`'s contract: unset before a single `next()`.
    Merely calling `events(...)` builds the generator and runs nothing — a
    generator function's body does not execute until it is iterated."""
    _tsv(tmp_path / "conn.log", CONN_FIELDS, [_conn_row(T0)])
    p = zp.ZeekPlugin()
    p.events(str(tmp_path))  # constructed, never driven
    assert p.unparsed_stems is None


def test_unparsed_stems_is_empty_dict_when_every_present_stem_is_parsed(tmp_path):
    """`{}`, not `None`: the source WAS looked at and nothing was left over —
    "audited, clean" (D5), distinct from "nobody looked"."""
    _tsv(tmp_path / "conn.log", CONN_FIELDS, [_conn_row(T0)])
    p = _plugin()
    assert list(p.events(str(tmp_path)))  # non-empty: the fixture actually parsed
    assert p.unparsed_stems == {}


def test_unparsed_stems_counts_both_declared_skips_and_unrecognized_stems_together(tmp_path):
    """D5's scope decision, exercised: a stem counts here whether it is a DECLARED
    `_SKIP` member or genuinely UNRECOGNIZED — the literal reading of "stems
    present in the input and not parsed", with no carve-out for a skip that was
    merely decided rather than merely unmapped."""
    skip_stem = sorted(zp._SKIP)[0]
    _tsv(tmp_path / f"{skip_stem}.log", ("ts", "peer", "value"),
         [(f"{T0:.6f}", "worker-1", 1)])
    _tsv(tmp_path / f"{_UNKNOWN_STEM}.log", ("ts", "id.orig_h", "id.resp_h"),
         [(f"{T0:.6f}", "10.0.0.21", "10.0.0.5")])
    _tsv(tmp_path / "conn.log", CONN_FIELDS, [_conn_row(T0)])

    p = _plugin()
    list(p.events(str(tmp_path)))
    assert p.unparsed_stems == {skip_stem: 1, _UNKNOWN_STEM: 1}


def test_unrecognized_stems_excludes_declared_skips(tmp_path):
    """Code review `S4`, additive to D5: `unrecognized_stems` is the SUBSET of
    `unparsed_stems` with no `_SKIP` entry — the declared skip stem from the
    same fixture as the test above must NOT appear here, only the genuinely
    unrecognized one."""
    skip_stem = sorted(zp._SKIP)[0]
    _tsv(tmp_path / f"{skip_stem}.log", ("ts", "peer", "value"),
         [(f"{T0:.6f}", "worker-1", 1)])
    _tsv(tmp_path / f"{_UNKNOWN_STEM}.log", ("ts", "id.orig_h", "id.resp_h"),
         [(f"{T0:.6f}", "10.0.0.21", "10.0.0.5")])
    _tsv(tmp_path / "conn.log", CONN_FIELDS, [_conn_row(T0)])

    p = _plugin()
    list(p.events(str(tmp_path)))
    assert p.unparsed_stems == {skip_stem: 1, _UNKNOWN_STEM: 1}, "sanity: both present"
    assert p.unrecognized_stems == {_UNKNOWN_STEM: 1}, \
        "the declared skip must be excluded; only the genuinely unrecognized stem remains"


def test_unrecognized_stems_is_empty_dict_when_everything_left_over_is_a_declared_skip(tmp_path):
    """`[]`, not `None`: audited, and every stem left over turned out to be a
    DECLARED skip — a real, distinct finding from "nothing was left over at
    all" (`test_unparsed_stems_is_empty_dict_when_every_present_stem_is_parsed`)
    and from "unaudited"."""
    skip_stem = sorted(zp._SKIP)[0]
    _tsv(tmp_path / f"{skip_stem}.log", ("ts", "peer", "value"),
         [(f"{T0:.6f}", "worker-1", 1)])
    _tsv(tmp_path / "conn.log", CONN_FIELDS, [_conn_row(T0)])

    p = _plugin()
    list(p.events(str(tmp_path)))
    assert p.unparsed_stems == {skip_stem: 1}
    assert p.unrecognized_stems == {}


def test_unrecognized_stems_is_final_after_exactly_one_event_is_pulled(tmp_path):
    """Mirrors `test_unparsed_stems_is_final_after_exactly_one_event_is_pulled`
    — the identical sequencing argument, computed at the SAME synchronous
    point, applies to `unrecognized_stems` too."""
    skip_stem = sorted(zp._SKIP)[0]
    _tsv(tmp_path / f"{skip_stem}.log", ("ts", "peer", "value"),
         [(f"{T0:.6f}", "worker-1", 1)])
    _tsv(tmp_path / f"{_UNKNOWN_STEM}.log", ("ts", "id.orig_h", "id.resp_h"),
         [(f"{T0:.6f}", "10.0.0.21", "10.0.0.5")])
    _tsv(tmp_path / "conn.log", CONN_FIELDS,
         [_conn_row(T0), _conn_row(T0 + 1), _conn_row(T0 + 2)])

    p = _plugin()
    events = p.events(str(tmp_path))
    assert p.unrecognized_stems is None, "nothing pulled yet"
    next(events)
    assert p.unrecognized_stems == {_UNKNOWN_STEM: 1}, \
        "must already be final after exactly one event"
    list(events)
    assert p.unrecognized_stems == {_UNKNOWN_STEM: 1}


def test_unparsed_stems_is_final_after_exactly_one_event_is_pulled(tmp_path):
    """The crux of D5's build step 4: a caller that has pulled exactly ONE event
    already sees the run's final, complete answer — `unparsed_stems` is computed
    synchronously, before `events()`'s own first `yield`, not discovered
    gradually as the stream is consumed. This is what makes it correct to pass
    into `WindowManager` for window 1 specifically (`cli.py`'s own comment on
    `wm`'s construction): no window can close without process() having already
    pulled at least one event, so by the time ANY window closes this attribute
    already holds the value this test confirms below.
    """
    _tsv(tmp_path / f"{_UNKNOWN_STEM}.log", ("ts", "id.orig_h", "id.resp_h"),
         [(f"{T0:.6f}", "10.0.0.21", "10.0.0.5")])
    _tsv(tmp_path / "conn.log", CONN_FIELDS,
         [_conn_row(T0), _conn_row(T0 + 1), _conn_row(T0 + 2)])

    p = _plugin()
    events = p.events(str(tmp_path))
    assert p.unparsed_stems is None, "nothing pulled yet"
    next(events)
    assert p.unparsed_stems == {_UNKNOWN_STEM: 1}, \
        "must already be final after exactly one event, not only after the whole stream"
    list(events)  # drain the rest
    assert p.unparsed_stems == {_UNKNOWN_STEM: 1}, "draining the rest must not change it"


def test_unparsed_stems_is_final_even_when_events_yields_nothing_at_all(tmp_path):
    """The degenerate case the sequencing argument still has to cover: a source
    with only unparsed traffic yields zero events, but the very first `next()`
    call (even the one that immediately raises `StopIteration`) still runs the
    generator's body up to that point — `unparsed_stems` is set unconditionally
    before the merge that would ever `yield` anything."""
    _tsv(tmp_path / f"{_UNKNOWN_STEM}.log", ("ts", "id.orig_h", "id.resp_h"),
         [(f"{T0:.6f}", "10.0.0.21", "10.0.0.5")])
    p = _plugin()
    assert list(p.events(str(tmp_path))) == []
    assert p.unparsed_stems == {_UNKNOWN_STEM: 1}


def test_unparsed_stems_resets_across_repeated_events_calls(tmp_path):
    """Same discipline `log_stats` already has (reset at the top of `events()`):
    a second call describes only that call's own source, never a stale union
    carried over from an earlier one."""
    _tsv(tmp_path / f"{_UNKNOWN_STEM}.log", ("ts", "id.orig_h", "id.resp_h"),
         [(f"{T0:.6f}", "10.0.0.21", "10.0.0.5")])
    _tsv(tmp_path / "conn.log", CONN_FIELDS, [_conn_row(T0)])
    p = _plugin()
    list(p.events(str(tmp_path)))
    assert p.unparsed_stems == {_UNKNOWN_STEM: 1}

    (tmp_path / f"{_UNKNOWN_STEM}.log").unlink()
    list(p.events(str(tmp_path)))
    assert p.unparsed_stems == {}, "must not still carry the first call's stem"


# -- progress reporting ---------------------------------------------------------

def test_progress_is_reported_to_stderr(tmp_path, capsys):
    _tsv(tmp_path / "conn.log", CONN_FIELDS,
         [_conn_row(T0 + i) for i in range(25)])
    p = zp.ZeekPlugin()
    p.progress_rows = 10
    list(p.events(str(tmp_path)))
    err = capsys.readouterr().err
    assert "conn: 10 rows" in err and "conn: 20 rows" in err
    assert "done: 25 rows -> 25 events" in err


# -- software (banner fingerprint) ----------------------------------------------

def test_software_parses_to_a_service_event_carrying_the_raw_banner(tmp_path):
    """The raw banner stays on the EVENT — it is the only place an OS token survives
    (`Windows NT 10.0` here), and `Q26` is about mining it. What must not happen is that
    banner reaching a node feature, which `test_software_node_feature_excludes_the_raw_banner`
    pins from the other side.
    """
    _tsv(tmp_path / "software.log",
         ("ts", "host", "host_p", "software_type", "name", "version.major",
          "version.minor", "unparsed_version"),
         [(f"{T0:.6f}", "10.0.0.21", 0, "HTTP::BROWSER", "Chrome", 120, 0,
           "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0")])

    ev = next(e for e in _plugin().events(str(tmp_path)) if isinstance(e, ServiceEvent))
    assert ev.kind == "software" and ev.ip == "10.0.0.21"
    assert ev.attrs["software_type"] == "HTTP::BROWSER"
    assert ev.attrs["name"] == "Chrome"
    assert "Windows NT 10.0" in ev.attrs["unparsed_version"]


def test_software_row_without_type_or_name_mints_no_key(tmp_path):
    """A row identifying nothing must not become an empty vocabulary key — the positive-gate
    habit (CONVENTIONS §3) applied where a key is minted, per `LES-017`.
    """
    _tsv(tmp_path / "software.log",
         ("ts", "host", "host_p", "software_type", "name"),
         [(f"{T0:.6f}", "10.0.0.21", 0, "-", "-")])

    from aximprint.windowing.windows import WindowManager
    wm = WindowManager(window_seconds=3600)
    wins = list(wm.process(_plugin().events(str(tmp_path)))) + list(wm.finalize())
    for win in wins:
        for feats in win.nodes.values():
            assert feats["services"] == {}


def test_software_node_feature_excludes_the_raw_banner_and_reaches_an_assertion(tmp_path):
    """The other half of the parser test: what windowing PUBLISHES is the bounded triple, and
    the near-unique `unparsed_version` never becomes a categorical dimension (`LES-017`).
    Also pins that the fact actually reaches the extractors — before this round every
    ServiceEvent was parsed and then dropped, because `WindowResult` had no channel for one.
    """
    from aximprint.learners import extract_assertions
    from aximprint.windowing.windows import WindowManager

    _tsv(tmp_path / "software.log",
         ("ts", "host", "host_p", "software_type", "name", "version.major",
          "version.minor", "unparsed_version"),
         [(f"{T0:.6f}", "10.0.0.21", 0, "HTTP::BROWSER", "Chrome", 120, 0,
           "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0")])

    wm = WindowManager(window_seconds=3600)
    wins = list(wm.process(_plugin().events(str(tmp_path)))) + list(wm.finalize())
    feats = wins[0].nodes["10.0.0.21"]

    assert feats["services"]["software"] == ["HTTP::BROWSER|Chrome|120|0"]
    assert feats["services_truncated"] == {}
    blob = repr(feats)
    assert "Mozilla" not in blob and "Windows NT" not in blob, \
        "the raw banner must not reach a published node feature"

    preds = {(a["predicate"], a["object"]) for a in extract_assertions(wins[0])}
    assert ("runs_software", "HTTP::BROWSER|Chrome|120|0") in preds


def test_software_keys_are_capped_and_the_truncation_is_disclosed(tmp_path):
    """A cap whose overflow is invisible turns 'saw nothing more' into 'stopped looking'."""
    from aximprint.windowing.windows import SERVICE_MAX_KEYS, WindowManager

    over = SERVICE_MAX_KEYS + 5
    _tsv(tmp_path / "software.log",
         ("ts", "host", "host_p", "software_type", "name", "version.major", "version.minor"),
         [(f"{T0 + i:.6f}", "10.0.0.21", 0, "HTTP::BROWSER", f"Browser{i}", i, 0)
          for i in range(over)])

    wm = WindowManager(window_seconds=3600)
    wins = list(wm.process(_plugin().events(str(tmp_path)))) + list(wm.finalize())
    feats = wins[0].nodes["10.0.0.21"]

    assert len(feats["services"]["software"]) == SERVICE_MAX_KEYS
    assert feats["services_truncated"]["software"] == over - SERVICE_MAX_KEYS


# -- service projection (`Q27`) --------------------------------------------------

def test_every_admitted_service_kind_projects_to_an_assertion(tmp_path):
    """`Q27` option A: each admitted kind lands as a durable per-entity property, then an
    assertion. Before this, ~93,000 ServiceEvents a run were parsed and dropped because
    `WindowResult` had no channel one could ride.
    """
    from aximprint.learners import extract_assertions
    from aximprint.windowing.windows import SERVICE_PROJECTIONS, WindowManager

    _write_all_new_types(tmp_path)
    # `_write_all_new_types` predates `ssl`, so add it here rather than widening a shared
    # fixture whose own test pins its exact event-kind set.
    _tsv(tmp_path / "ssl.log",
         ("ts", "id.orig_h", "id.orig_p", "id.resp_h", "id.resp_p", "server_name",
          "version", "ja3", "ja3s"),
         [(f"{T0 + 8:.6f}", "10.0.0.21", 44300, "10.0.0.8", 443, "svc.example.test",
           "TLSv13", "aabb", "ccdd")])
    # `_write_all_new_types` also predates `docs/WORK_ORDER_zeek_stem_coverage.md` step 3's
    # eight new SERVICE_PROJECTIONS kinds -- same reasoning as `ssl` above, added here rather
    # than in the shared fixture so `test_each_new_log_type_produces_its_event`'s exact-set
    # pin stays untouched.
    _tsv(tmp_path / "http.log",
         ("ts", "id.orig_h", "id.orig_p", "id.resp_h", "id.resp_p", "host", "uri",
          "method", "user_agent"),
         [(f"{T0 + 9:.6f}", "10.0.0.21", 55000, "10.0.0.10", 80, "svc.example.test",
           "/", "GET", "curl/8.0")])
    _tsv(tmp_path / "ssh.log",
         ("ts", "id.orig_h", "id.orig_p", "id.resp_h", "id.resp_p", "server",
          "auth_success"),
         [(f"{T0 + 10:.6f}", "10.0.0.21", 55001, "10.0.0.11", 22,
           "SSH-2.0-OpenSSH_9.0", "T")])
    _tsv(tmp_path / "quic.log",
         ("ts", "id.orig_h", "id.orig_p", "id.resp_h", "id.resp_p", "version",
          "server_name"),
         [(f"{T0 + 11:.6f}", "10.0.0.21", 55002, "10.0.0.12", 443, "1",
           "svc.example.test")])
    _tsv(tmp_path / "snmp.log",
         ("ts", "id.orig_h", "id.orig_p", "id.resp_h", "id.resp_p", "version",
          "display_string"),
         [(f"{T0 + 12:.6f}", "10.0.0.21", 55003, "10.0.0.13", 161, "2c",
           "Cisco IOS Software")])
    _tsv(tmp_path / "tunnel.log",
         ("ts", "id.orig_h", "id.orig_p", "id.resp_h", "id.resp_p", "tunnel_type",
          "action"),
         [(f"{T0 + 13:.6f}", "10.0.0.21", 55004, "10.0.0.14", 4789, "Tunnel::VXLAN",
           "Tunnel::DISCOVER")])
    _tsv(tmp_path / "smtp.log",
         ("ts", "id.orig_h", "id.orig_p", "id.resp_h", "id.resp_p", "helo",
          "x_originating_ip"),
         [(f"{T0 + 14:.6f}", "10.0.0.21", 55005, "10.0.0.15", 25, "mail.example.test",
           "203.0.113.9")])
    _tsv(tmp_path / "sip.log",
         ("ts", "id.orig_h", "id.orig_p", "id.resp_h", "id.resp_p", "user_agent"),
         [(f"{T0 + 15:.6f}", "10.0.0.21", 55006, "10.0.0.16", 5060, "Zeek-SIP-Test/1.0")])
    _tsv(tmp_path / "ldap.log",
         ("ts", "id.orig_h", "id.orig_p", "id.resp_h", "id.resp_p", "opcode"),
         [(f"{T0 + 16:.6f}", "10.0.0.21", 55007, "10.0.0.17", 389, "bindRequest")])
    # `docs/WORK_ORDER_zeek_stems_wide_vantage.md` build steps 2-4's six projected kinds. Each
    # row populates exactly the fields that kind's projection tuple names -- tuples chosen from
    # MEASURED population on the ten-day cold tier, not from the work order's schema listing.
    _tsv(tmp_path / "known_certs.log",
         ("ts", "host", "port_num", "subject", "issuer_subject", "serial"),
         [(f"{T0 + 17:.6f}", "10.0.0.22", 443, "CN=svc.example.test", "CN=Test CA", "01AB")])
    _tsv(tmp_path / "rdp.log",
         ("ts", "id.orig_h", "id.orig_p", "id.resp_h", "id.resp_p", "cookie", "result",
          "security_protocol", "client_name"),
         [(f"{T0 + 18:.6f}", "10.0.0.21", 55008, "10.0.0.23", 3389, "mstshash=x",
           "encrypted", "HYBRID_EX", "-")])
    _tsv(tmp_path / "rfb.log",
         ("ts", "id.orig_h", "id.orig_p", "id.resp_h", "id.resp_p", "server_major_version",
          "server_minor_version", "authentication_method", "desktop_name"),
         [(f"{T0 + 19:.6f}", "10.0.0.21", 55009, "10.0.0.24", 5900, "3", "8", "VNC",
           "somedesk")])
    _tsv(tmp_path / "syslog.log",
         ("ts", "id.orig_h", "id.orig_p", "id.resp_h", "id.resp_p", "proto", "facility",
          "severity", "message"),
         [(f"{T0 + 20:.6f}", "10.0.0.21", 55010, "10.0.0.25", 514, "udp", "LOCAL0", "INFO",
           "a free text line that must never reach a projection key")])
    _tsv(tmp_path / "mqtt_connect.log",
         ("ts", "id.orig_h", "id.orig_p", "id.resp_h", "id.resp_p", "proto_version",
          "client_id", "connect_status"),
         [(f"{T0 + 21:.6f}", "10.0.0.21", 55011, "10.0.0.26", 1883, "-", "-",
           "Connection Accepted")])
    _tsv(tmp_path / "websocket.log",
         ("ts", "id.orig_h", "id.orig_p", "id.resp_h", "id.resp_p", "host", "uri",
          "user_agent", "subprotocol", "server_extensions"),
         [(f"{T0 + 22:.6f}", "10.0.0.21", 55012, "10.0.0.27", 8080, "ws.example.test", "/s",
           "curl/8.0", "-", "permessage-deflate")])
    wm = WindowManager(window_seconds=3600)
    wins = list(wm.process(_plugin().events(str(tmp_path)))) + list(wm.finalize())
    assertions = [a for w in wins for a in extract_assertions(w)]
    seen = {a["predicate"] for a in assertions}

    for kind, (_, predicate, _) in SERVICE_PROJECTIONS.items():
        assert predicate in seen, f"{kind} produced no assertion"


def test_declined_service_kinds_are_named_not_merely_absent():
    """An omission and a decision look identical in a table that only lists what it keeps.
    `_SERVICE_NOT_PROJECTED` is the half that says which is which — same discipline as the
    plugin's `_SKIP`. Both tables must also be disjoint, or a kind is two things at once.
    """
    from aximprint.windowing import windows as w

    assert not set(w.SERVICE_PROJECTIONS) & set(w._SERVICE_NOT_PROJECTED)
    for kind, reason in w._SERVICE_NOT_PROJECTED.items():
        assert reason.strip(), f"{kind} is declined with no reason"


def test_an_unruled_service_kind_is_counted_not_silently_dropped(tmp_path):
    """The positive gate (CONVENTIONS §3): a kind in NEITHER table is a kind nobody has ruled
    on, and must surface as a health fact. This is the failure mode `Q27` was opened about —
    events vanishing with the counters still reading clean.
    """
    from aximprint.events import ServiceEvent
    from aximprint.windowing.windows import WindowManager

    wm = WindowManager(window_seconds=3600)
    wins = list(wm.process([
        ServiceEvent(ts=T0, kind="__unruled__", ip="10.0.0.9", attrs={"a": "b"}),
    ])) + list(wm.finalize())

    assert wins[0].health["unmodelled_service_kinds"] == 1


def test_a_declined_kind_is_not_counted_as_unmodelled_and_materializes_no_entity(tmp_path):
    """Two halves of the same gate. Declining a kind on purpose must not read as an unruled one,
    or the health signal cries wolf on every window — and it must not conjure an entity either.
    A node is created only where an ADMITTED kind yields an identifying fact, so evidence the
    pipeline has decided to ignore cannot put a host on the map that nothing else observed.
    """
    from aximprint.events import ServiceEvent
    from aximprint.windowing.windows import WindowManager

    wm = WindowManager(window_seconds=3600)
    wins = list(wm.process([
        ServiceEvent(ts=T0, kind="ntp", ip="10.0.0.9", attrs={"version": "4"}),
    ])) + list(wm.finalize())

    assert wins[0].health["unmodelled_service_kinds"] == 0
    assert "10.0.0.9" not in wins[0].nodes


# -- the ten stems of docs/WORK_ORDER_zeek_stem_coverage.md step 3 (`Q24`) -------

def test_http_collects_credentials_and_projects_serves_http(tmp_path):
    """`Q24`'s amendment box voids the earlier "never username/password/uri/referrer/
    user_agent" refusal -- a parser takes every field the log offers (CONVENTIONS §3).
    """
    from aximprint.learners import extract_assertions
    from aximprint.windowing.windows import WindowManager

    _tsv(tmp_path / "http.log",
         ("ts", "id.orig_h", "id.orig_p", "id.resp_h", "id.resp_p", "host", "uri",
          "referrer", "user_agent", "username", "password"),
         [(f"{T0:.6f}", "10.0.0.21", 51000, "10.0.0.10", 80, "svc.example.test",
           "/admin", "https://ref.example.test", "curl/8.0", "alice", "hunter2")])

    ev = next(e for e in _plugin().events(str(tmp_path)) if isinstance(e, ServiceEvent))
    assert ev.kind == "http" and ev.ip == "10.0.0.10" and ev.port == 80
    assert ev.attrs["username"] == "alice" and ev.attrs["password"] == "hunter2"
    assert ev.attrs["uri"] == "/admin" and ev.attrs["referrer"] == "https://ref.example.test"

    wm = WindowManager(window_seconds=3600)
    wins = list(wm.process(_plugin().events(str(tmp_path)))) + list(wm.finalize())
    assert wins[0].nodes["10.0.0.10"]["services"]["http"] == ["svc.example.test"]
    preds = {(a["predicate"], a["object"], a["confidence"]) for a in extract_assertions(wins[0])}
    assert ("serves_http", "svc.example.test", 0.9) in preds


def test_ssh_collects_host_key_and_projects_at_point_nine(tmp_path):
    """`host_key`'s earlier refusal is void (`Q24` amendment box); `serves_ssh` sits at 0.9,
    not `software`'s 0.7 -- the one judgment call this round's admits make.
    """
    from aximprint.windowing.windows import SERVICE_PROJECTIONS

    _tsv(tmp_path / "ssh.log",
         ("ts", "id.orig_h", "id.orig_p", "id.resp_h", "id.resp_p", "server", "host_key"),
         [(f"{T0:.6f}", "10.0.0.21", 51001, "10.0.0.11", 22, "SSH-2.0-OpenSSH_9.0",
           "SHA256:abc123")])

    ev = next(e for e in _plugin().events(str(tmp_path)) if isinstance(e, ServiceEvent))
    assert ev.attrs["server"] == "SSH-2.0-OpenSSH_9.0"
    assert ev.attrs["host_key"] == "SHA256:abc123"
    assert SERVICE_PROJECTIONS["ssh"] == (("server",), "serves_ssh", 0.9)


def test_quic_mirrors_ssl_fields(tmp_path):
    _tsv(tmp_path / "quic.log",
         ("ts", "id.orig_h", "id.orig_p", "id.resp_h", "id.resp_p", "version",
          "server_name"),
         [(f"{T0:.6f}", "10.0.0.21", 51002, "10.0.0.12", 443, "1", "svc.example.test")])

    ev = next(e for e in _plugin().events(str(tmp_path)) if isinstance(e, ServiceEvent))
    assert ev.kind == "quic"
    assert ev.attrs == {"version": "1", "server_name": "svc.example.test"}


def test_snmp_collects_community_and_duration(tmp_path):
    """`community` (a credential) and `duration` are both collected under forensic
    completeness (CONVENTIONS §3). `duration` was excluded on this stem's first landing,
    on the reasoning that `scripts/make_zeek_archive.py`'s `_TYPES` typed the bare column
    name `duration` DOUBLE globally (for `conn.log`'s own, unrelated field) -- RESTORED
    (code review `S1`, `docs/WORK_ORDER_zeek_stem_coverage.md` step 3) once that typing was
    made `(stem, column)`-keyed instead, which fixes the actual defect rather than
    forfeiting the field.
    """
    _tsv(tmp_path / "snmp.log",
         ("ts", "id.orig_h", "id.orig_p", "id.resp_h", "id.resp_p", "duration",
          "version", "community", "display_string"),
         [(f"{T0:.6f}", "10.0.0.21", 51003, "10.0.0.13", 161, "0.500000", "2c",
           "public", "Cisco IOS Software")])

    ev = next(e for e in _plugin().events(str(tmp_path)) if isinstance(e, ServiceEvent))
    assert ev.attrs["community"] == "public"
    assert ev.attrs["display_string"] == "Cisco IOS Software"
    assert ev.attrs["duration"] == "0.500000", "the raw Zeek text, not a reformatted float"


def test_tunnel_projects_terminates_tunnel_not_serves_tunnel(tmp_path):
    """The one predicate in this round that departs from the `serves_*` convention on
    purpose -- responder-keyed like every other kind, but "serves" would imply an offered
    service, which an overlay endpoint is not.
    """
    from aximprint.learners import extract_assertions
    from aximprint.windowing.windows import WindowManager

    _tsv(tmp_path / "tunnel.log",
         ("ts", "id.orig_h", "id.orig_p", "id.resp_h", "id.resp_p", "tunnel_type",
          "action"),
         [(f"{T0:.6f}", "10.0.0.21", 51004, "10.0.0.14", 4789, "Tunnel::VXLAN",
           "Tunnel::DISCOVER")])

    wm = WindowManager(window_seconds=3600)
    wins = list(wm.process(_plugin().events(str(tmp_path)))) + list(wm.finalize())
    preds = {a["predicate"] for a in extract_assertions(wins[0])}
    assert "terminates_tunnel" in preds
    assert "serves_tunnel" not in preds


def test_ntlm_binds_hostname_preferring_dns_name_over_netbios_name(tmp_path):
    """The only new identity source among this round's ten: binds a Windows host NAME to
    the address that answered it, at the address itself.
    """
    _tsv(tmp_path / "ntlm.log",
         ("ts", "id.orig_h", "id.orig_p", "id.resp_h", "id.resp_p",
          "server_nb_computer_name", "server_dns_computer_name", "username"),
         [(f"{T0:.6f}", "10.0.0.21", 51005, "10.0.0.18", 445, "WORKSTN1",
           "workstn1.corp.example.test", "alice")])

    (ev,) = list(_plugin().events(str(tmp_path)))
    assert isinstance(ev, IdentityEvent)
    assert ev.kind == "ntlm" and ev.ip == "10.0.0.18"
    assert ev.hostname == "workstn1.corp.example.test", "the DNS name wins when both are set"


def test_ntlm_falls_back_to_netbios_name_when_dns_name_unset(tmp_path):
    _tsv(tmp_path / "ntlm.log",
         ("ts", "id.orig_h", "id.orig_p", "id.resp_h", "id.resp_p",
          "server_nb_computer_name", "server_dns_computer_name"),
         [(f"{T0:.6f}", "10.0.0.21", 51006, "10.0.0.19", 445, "WORKSTN2", "-")])

    (ev,) = list(_plugin().events(str(tmp_path)))
    assert ev.hostname == "WORKSTN2"


def test_ntlm_emits_nothing_without_any_hostname(tmp_path):
    """Positive gate (CONVENTIONS §3): a row identifying nothing must not become a
    fact-less `IdentityEvent` -- mirrors `_dhcp`'s requirement that its own bind target
    be present.
    """
    _tsv(tmp_path / "ntlm.log",
         ("ts", "id.orig_h", "id.orig_p", "id.resp_h", "id.resp_p",
          "server_nb_computer_name", "server_dns_computer_name"),
         [(f"{T0:.6f}", "10.0.0.21", 51007, "10.0.0.20", 445, "-", "-")])

    assert list(_plugin().events(str(tmp_path))) == []


def test_ntlm_binding_reaches_the_store_as_bound_ntlm_at_confidence_point_nine(tmp_path):
    """The runtime surface this round actually adds beyond the raw `IdentityEvent`: run
    through `EntityResolver.observe` (`windowing.entities`) and `extract_assertions`
    (`learners`, via `windowing.windows.IDENTITY_PROJECTIONS`), an `ntlm` row produces an
    `identity_delta` entry and a `bound_ntlm` assertion.

    Pins `0.9` (code review `M4`, `docs/WORK_ORDER_zeek_stem_coverage.md` step 3) — FIXED
    from the `0.7` this stem's first landing shipped with, which silently fell into the
    inline confidence tuple's `else` bucket alongside `dns` (a THIRD PARTY's claim)
    despite `_ntlm`'s own docstring arguing the binding is made "AT THE ADDRESS ITSELF",
    the same property that earns `dhcp`/`kerberos` the `0.9` tier. `IDENTITY_PROJECTIONS`
    (`windowing.windows`) now declares this explicitly instead of an inline literal a new
    kind could silently fall through.
    """
    from aximprint.learners import extract_assertions
    from aximprint.windowing.windows import WindowManager

    _tsv(tmp_path / "ntlm.log",
         ("ts", "id.orig_h", "id.orig_p", "id.resp_h", "id.resp_p",
          "server_dns_computer_name"),
         [(f"{T0:.6f}", "10.0.0.21", 51012, "10.0.0.18", 445,
           "workstn1.corp.example.test")])

    wm = WindowManager(window_seconds=3600)
    wins = list(wm.process(_plugin().events(str(tmp_path)))) + list(wm.finalize())
    # Two records for ONE binding since `Q1`'s resolver landed (`Q9`): `opened` when the
    # evidence arrives, `final` at end of stream carrying the observation extent. `bound_to`
    # keeps the FQDN exactly as Zeek reported it while `identifier` carries the parsed short
    # name the resolver actually keys on (`windowing.entities.computer_name`) — a Kerberos
    # machine account naming this same host would land on that identical key.
    opened, final = wins[0].identity_delta
    assert [r["change"] for r in (opened, final)] == ["opened", "final"]
    assert opened == {
        "change": "opened", "kind": "ntlm", "ip": "10.0.0.18",
        "bound_to": "workstn1.corp.example.test",
        "namespace": "hostname", "identifier": "hostname:workstn1", "scope": "host",
        "entity": "ent-0001", "from_ts": T0, "to_ts": None,
        "first_seen": T0, "last_seen": T0, "lease_seconds": None, "record_ttl": None,
        "count": 1,
    }
    assert wins[0].health["unmodelled_identity_kinds"] == 0
    assertions = {(a["subject"], a["predicate"], a["object"], a["confidence"],
                   a["evidence_class"]) for a in extract_assertions(wins[0])}
    # Subject is the resolved ENTITY, not `10.0.0.18` — the mac/name belongs to the host, and
    # the address it was seen at is preserved on the binding record itself.
    assert ("ent-0001", "bound_ntlm", "workstn1.corp.example.test", 0.9, "observed") \
        in assertions
    # ONE assertion for the binding, not one per record: `closed`/`final` change the interval,
    # not the claim.
    assert sum(1 for a in extract_assertions(wins[0]) if a["predicate"] == "bound_ntlm") == 1


def test_an_unruled_identity_kind_is_counted_not_silently_assigned_a_predicate(monkeypatch):
    """The identity-side mirror of `test_an_unruled_service_kind_is_counted_not_silently_dropped`
    (`M5`, code review): a bound kind absent from `IDENTITY_PROJECTIONS` must surface as a
    health fact, not silently mint a `bound_<kind>` predicate at a guessed confidence.

    There are now TWO tables an identity kind can be missing from, and they count separately
    because they answer different questions (`Q1`): `IDENTITY_SCOPES` (`windowing.entities`)
    decides what a kind can RESOLVE, `IDENTITY_PROJECTIONS` (`windowing.windows`) decides what a
    binding is ASSERTED as. Both cases are exercised here rather than only the one that used to
    exist, because a kind in neither now stops at the FIRST table — so the old fixture (a bare
    `__unruled__` kind) no longer reaches the second one at all, and a test asserting only
    `unmodelled_identity_kinds` would silently have stopped measuring it (`LES-031`).
    """
    from aximprint.events import IdentityEvent
    from aximprint.learners import extract_assertions
    from aximprint.windowing import entities as ent
    from aximprint.windowing.windows import WindowManager

    # (1) a kind in NEITHER table: no scope, so no binding at all, counted as unscoped.
    wm = WindowManager(window_seconds=3600)
    wins = list(wm.process([
        IdentityEvent(ts=T0, kind="__unruled__", ip="10.0.0.9", hostname="h"),
    ])) + list(wm.finalize())
    assert wins[0].health["unscoped_identity_kinds"] == 1
    assert wins[0].health["unmodelled_identity_kinds"] == 0, \
        "a kind that never resolved cannot also be an unprojected BINDING"
    assert wins[0].identity_delta == []
    assert extract_assertions(wins[0]) == []

    # (2) a kind that CAN resolve but has no declared predicate: the binding is kept, the
    #     assertion is withheld, and the gap is a counted health fact.
    monkeypatch.setitem(ent.IDENTITY_SCOPES, "__scoped_only__", ent._identifiers_ntlm)
    wm = WindowManager(window_seconds=3600)
    wins = list(wm.process([
        IdentityEvent(ts=T0, kind="__scoped_only__", ip="10.0.0.9", hostname="h"),
    ])) + list(wm.finalize())
    assert wins[0].health["unmodelled_identity_kinds"] == 1
    assert wins[0].health["unscoped_identity_kinds"] == 0
    assert wins[0].identity_delta[0]["kind"] == "__scoped_only__", (
        "identity_delta itself still carries the raw binding -- only the ASSERTION is withheld")
    assert extract_assertions(wins[0]) == []


def test_smtp_collects_relay_chain_originating_ip_and_date_but_does_not_project_helo(tmp_path):
    """`path`/`x_originating_ip` are the actual reason this stem was admitted -- IP
    addresses of hosts the sensor never saw at all, the one referenced-entity case usable
    under today's `entity == ip` resolver (`Q24` amendment 1). `date` was excluded on this
    stem's first landing (a Hive-partition-key collision on the archive transport) and is
    RESTORED here (code review `S1`): the fix belongs at the transport
    (`ParquetArchiveSource._read` now passes `hive_partitioning=false`), not as a permanent
    field forfeiture.

    `helo` is collected but NOT projected: it is the CLIENT's self-announced name attached
    to an event keyed on the SERVER, a keying bug (`R1`, user-ruled code review) --
    `windowing.windows._SERVICE_NOT_PROJECTED["smtp"]` declines it, reversing this kind's
    own earlier `Q24` ruling.
    """
    from aximprint.learners import extract_assertions
    from aximprint.windowing.windows import WindowManager

    _tsv(tmp_path / "smtp.log",
         ("ts", "id.orig_h", "id.orig_p", "id.resp_h", "id.resp_p", "date", "helo",
          "path", "x_originating_ip"),
         [(f"{T0:.6f}", "10.0.0.21", 51008, "10.0.0.15", 25,
           "Wed, 06 Aug 2026 10:15:00 -0700", "mail.example.test",
           "10.0.0.15,198.51.100.7", "198.51.100.7")])

    ev = next(e for e in _plugin().events(str(tmp_path)) if isinstance(e, ServiceEvent))
    assert ev.attrs["path"] == "10.0.0.15,198.51.100.7"
    assert ev.attrs["x_originating_ip"] == "198.51.100.7"
    assert ev.attrs["date"] == "Wed, 06 Aug 2026 10:15:00 -0700"
    assert ev.attrs["helo"] == "mail.example.test"

    wm = WindowManager(window_seconds=3600)
    wins = list(wm.process(_plugin().events(str(tmp_path)))) + list(wm.finalize())
    # A declined kind must not conjure an entity either (`_add_service` returns before ever
    # calling `self._node(...)` for it) -- matches
    # `test_a_declined_kind_is_not_counted_as_unmodelled_and_materializes_no_entity`.
    assert "10.0.0.15" not in wins[0].nodes, "smtp is declined, not projected"
    assert wins[0].health["unmodelled_service_kinds"] == 0, "declined, not unruled"
    preds = {a["predicate"] for a in extract_assertions(wins[0])}
    assert "serves_smtp" not in preds


def test_sip_collects_date_but_does_not_project_user_agent(tmp_path):
    """`date` was excluded on this stem's first landing (the same Hive-partition-key
    collision `smtp`'s test names) and is RESTORED here (code review `S1`). `user_agent`
    is collected but NOT projected: it is the CLIENT's own header attached to an event
    keyed on the SERVER (`R1`, user-ruled code review) --
    `windowing.windows._SERVICE_NOT_PROJECTED["sip"]` declines it.
    """
    from aximprint.learners import extract_assertions
    from aximprint.windowing.windows import WindowManager

    _tsv(tmp_path / "sip.log",
         ("ts", "id.orig_h", "id.orig_p", "id.resp_h", "id.resp_p", "date",
          "user_agent"),
         [(f"{T0:.6f}", "10.0.0.21", 51009, "10.0.0.16", 5060,
           "Wed, 06 Aug 2026 10:15:00 -0700", "Zeek-SIP-Test/1.0")])

    ev = next(e for e in _plugin().events(str(tmp_path)) if isinstance(e, ServiceEvent))
    assert ev.attrs == {"date": "Wed, 06 Aug 2026 10:15:00 -0700",
                        "user_agent": "Zeek-SIP-Test/1.0"}

    wm = WindowManager(window_seconds=3600)
    wins = list(wm.process(_plugin().events(str(tmp_path)))) + list(wm.finalize())
    assert "10.0.0.16" not in wins[0].nodes, "sip is declined, not projected"
    assert wins[0].health["unmodelled_service_kinds"] == 0, "declined, not unruled"
    preds = {a["predicate"] for a in extract_assertions(wins[0])}
    assert "serves_sip" not in preds


def test_ldap_op_is_a_distinct_kind_from_ldap_search(tmp_path):
    """`ldap.log` -> kind `ldap_op`, NOT `ldap`: `ldap_search.log`'s parser already owns
    kind `"ldap"`. Getting this wrong silently merges two different facts under one
    predicate (`docs/WORK_ORDER_zeek_stem_coverage.md` step 3).
    """
    _tsv(tmp_path / "ldap.log",
         ("ts", "id.orig_h", "id.orig_p", "id.resp_h", "id.resp_p", "opcode",
          "argument"),
         [(f"{T0:.6f}", "10.0.0.21", 51010, "10.0.0.17", 389, "bindRequest",
           "cn=admin,dc=example,dc=test")])
    _tsv(tmp_path / "ldap_search.log",
         ("ts", "id.orig_h", "id.orig_p", "id.resp_h", "id.resp_p", "scope"),
         [(f"{T0 + 1:.6f}", "10.0.0.21", 51011, "10.0.0.17", 389, "sub")])

    services = {e.kind: e for e in _plugin().events(str(tmp_path)) if isinstance(e, ServiceEvent)}
    assert set(services) == {"ldap_op", "ldap"}
    assert services["ldap_op"].attrs["opcode"] == "bindRequest"
    assert services["ldap_op"].attrs["argument"] == "cn=admin,dc=example,dc=test"


def test_x509_has_no_connection_tuple_and_stays_structurally_inert(tmp_path):
    """Verified against the baseline (`Q24` "Headers verified"): `x509.log` carries no
    `id.orig_h`/`id.resp_h` at all. `ip` therefore stays empty, and `_add_service`'s own
    `if not ev.ip: return` makes the event inert for node/edge features -- collected in
    full, but not attributable, until a `ssl.cert_chain_fps` join or `Q1` changes that.
    """
    from aximprint.windowing.windows import WindowManager

    _tsv(tmp_path / "x509.log",
         ("ts", "fingerprint", "certificate.subject", "san.dns"),
         [(f"{T0:.6f}", "aa:bb:cc", "CN=svc.example.test", "svc.example.test,alt.example.test")])

    ev = next(e for e in _plugin().events(str(tmp_path)) if isinstance(e, ServiceEvent))
    assert ev.kind == "x509" and ev.ip == ""
    assert ev.attrs["fingerprint"] == "aa:bb:cc"
    assert ev.attrs["san.dns"] == "svc.example.test,alt.example.test"

    wm = WindowManager(window_seconds=3600)
    wins = list(wm.process(_plugin().events(str(tmp_path)))) + list(wm.finalize())
    assert wins[0].nodes == {}, "an ip-less service event must not conjure a node"


# -- ocsp / the subject-less profile channel (`Q29`) -----------------------------

def test_ocsp_produces_a_profile_event_never_a_service_event(tmp_path):
    """`ocsp.log` carries no connection tuple at all -- no `id.orig_h`/`id.resp_h`, only the
    response's own `id` (a hash) -- verified against the baseline (`Q24` "Headers verified").
    So `_ocsp` cannot yield a `ServiceEvent` the way every other admitted stem this round does;
    it is the subject-less channel's first occupant instead (`Q29`).
    `nextUpdate - thisUpdate` is the response's cache lifetime.
    """
    _tsv(tmp_path / "ocsp.log",
         ("ts", "id", "hashAlgorithm", "issuerNameHash", "issuerKeyHash", "serialNumber",
          "certStatus", "revoketime", "revokereason", "thisUpdate", "nextUpdate"),
         [(f"{T0:.6f}", "F3cWtN3H0T", "sha1", "AAAA", "BBBB", "0F0E", "good", "-", "-",
           f"{T0:.6f}", f"{T0 + 604800:.6f}")])

    events = list(_plugin().events(str(tmp_path)))
    assert not any(isinstance(e, ServiceEvent) for e in events), \
        "ocsp must never become a ServiceEvent -- it has no ip to require"
    ev = next(e for e in events if isinstance(e, ProfileEvent))
    assert ev.parameter == "ocsp_cache_lifetime"
    assert ev.value == 604800.0


def test_ocsp_never_imputes_a_delta_when_a_timestamp_is_unset(tmp_path):
    """`_num`, not `_f`: an unset `thisUpdate`/`nextUpdate` must not be coerced to `0.0` and
    subtracted into a manufactured delta (CONVENTIONS §3, "imputed never masquerades as
    observed"). On the real baseline both are always set; this is the honest-absence case a
    parser written only to that observation would silently start getting wrong.
    """
    _tsv(tmp_path / "ocsp.log",
         ("ts", "id", "certStatus", "thisUpdate", "nextUpdate"),
         [(f"{T0:.6f}", "resp-1", "good", "-", f"{T0 + 604800:.6f}"),
          (f"{T0 + 1:.6f}", "resp-2", "good", f"{T0:.6f}", "-")])

    assert list(_plugin().events(str(tmp_path))) == [], \
        "neither row has both thisUpdate and nextUpdate set"


def test_ocsp_reaches_window_profile_with_no_node_edge_or_binding_created(tmp_path):
    """The full path: `_ocsp` -> `WindowManager._add_profile` -> `WindowResult.capture_profile`
    (`Q29`). No node, edge or identity binding is ever created -- there is no address to
    resolve into an entity, unlike every other admitted stem this round.
    """
    from aximprint.windowing.windows import WindowManager

    _tsv(tmp_path / "ocsp.log",
         ("ts", "id", "certStatus", "thisUpdate", "nextUpdate"),
         [(f"{T0:.6f}", "resp-1", "good", f"{T0:.6f}", f"{T0 + 604800:.6f}"),
          (f"{T0 + 1:.6f}", "resp-2", "good", f"{T0 + 100:.6f}", f"{T0 + 100 + 601200:.6f}")])

    wm = WindowManager(window_seconds=3600)
    wins = list(wm.process(_plugin().events(str(tmp_path)))) + list(wm.finalize())

    assert wins[0].nodes == {} and wins[0].edges == [] and wins[0].identity_delta == []
    assert wins[0].health["unmodelled_profile_parameters"] == 0
    assert wins[0].health["profile_values_refused"] == 0
    stats = wins[0].capture_profile["ocsp_cache_lifetime"]
    assert set(stats) == {"p10", "p50", "p90", "p99", "n"}
    assert stats["n"] == 2, "the sample count behind the summary (code review S1)"
    assert min(stats["p10"], stats["p50"], stats["p90"], stats["p99"]) == 601200.0
    assert max(stats["p10"], stats["p50"], stats["p90"], stats["p99"]) == 604800.0


def test_an_unruled_profile_parameter_is_counted_not_silently_dropped(tmp_path):
    """Mirrors `test_an_unruled_service_kind_is_counted_not_silently_dropped` one channel over
    (`Q29`): a `ProfileEvent.parameter` absent from `PROFILE_PARAMETERS` must surface as a
    health fact, never vanish with the counters still reading clean.
    """
    from aximprint.windowing.windows import WindowManager

    wm = WindowManager(window_seconds=3600)
    wins = list(wm.process([
        ProfileEvent(ts=T0, parameter="__unruled_parameter__", value=1.0),
    ])) + list(wm.finalize())

    assert wins[0].health["unmodelled_profile_parameters"] == 1
    assert wins[0].capture_profile == {}, \
        "an unruled parameter must not be accumulated under a guess"


def test_add_profile_refuses_and_counts_a_negative_value(tmp_path):
    """`LES-005` recurring verbatim (code review `M2`): its own origin incident was a negative
    interarrival reaching a quantile sketch with `_add_flow` raising nothing. `_add_profile`'s
    own gate is the same shape -- refused and counted (`profile_values_refused`), never
    silently clamped into the sketch wearing an observed face. Before the fix this value
    reached the sketch and published as `p10: -86400.0` with every other health counter
    (including `unmodelled_profile_parameters`) reading clean.
    """
    from aximprint.windowing.windows import WindowManager

    wm = WindowManager(window_seconds=3600)
    wins = list(wm.process([
        ProfileEvent(ts=T0, parameter="ocsp_cache_lifetime", value=-86400.0),
        ProfileEvent(ts=T0 + 1, parameter="ocsp_cache_lifetime", value=604800.0),
    ])) + list(wm.finalize())

    assert wins[0].health["profile_values_refused"] == 1
    assert wins[0].health["unmodelled_profile_parameters"] == 0, \
        "a recognized parameter with a bad VALUE is not the same finding as an unrecognized one"
    stats = wins[0].capture_profile["ocsp_cache_lifetime"]
    assert stats["n"] == 1, "the refused reading must not count toward the sample size either"
    assert min(stats["p10"], stats["p50"], stats["p90"], stats["p99"]) >= 0, \
        "the negative reading must never reach the sketch"


def test_ocsp_clock_skew_produces_a_negative_delta_refused_not_published(tmp_path):
    """End to end: a responder whose clock reports `nextUpdate` before `thisUpdate` -- clock
    skew, not a parse failure -- makes `_ocsp` emit a NEGATIVE `ProfileEvent.value` exactly as
    it always has (the parser only validates presence, per its own docstring); `_add_profile`'s
    gate is what stops it from ever reaching a published quantile (code review `M2`).
    """
    from aximprint.windowing.windows import WindowManager

    _tsv(tmp_path / "ocsp.log",
         ("ts", "id", "certStatus", "thisUpdate", "nextUpdate"),
         [(f"{T0:.6f}", "resp-skewed", "good", f"{T0:.6f}", f"{T0 - 86400:.6f}")])

    ev = next(e for e in _plugin().events(str(tmp_path)) if isinstance(e, ProfileEvent))
    assert ev.value == -86400.0, "the parser itself still emits the raw, unvalidated delta"

    wm = WindowManager(window_seconds=3600)
    wins = list(wm.process(_plugin().events(str(tmp_path)))) + list(wm.finalize())
    assert wins[0].health["profile_values_refused"] == 1
    assert wins[0].capture_profile == {}, \
        "the one reading this window had was refused -- nothing left to summarize"


def test_profile_quantiles_are_byte_stable_across_two_runs(tmp_path):
    """`Q8`'s O(input) caveat is inherited honestly from `sketches.Quantiles`, but the OUTPUT
    must still be exactly reproducible run to run (CONVENTIONS §3) -- the same discipline
    `flow_size_q`/`duration_q`/etc. already have, exercised here for the one channel with no
    entity to key determinism off of at all.
    """
    from aximprint.windowing.windows import WindowManager

    _tsv(tmp_path / "ocsp.log",
         ("ts", "id", "certStatus", "thisUpdate", "nextUpdate"),
         [(f"{T0 + i:.6f}", f"resp-{i}", "good", f"{T0:.6f}", f"{T0 + 600000 + i * 37:.6f}")
          for i in range(20)])

    def _run():
        wm = WindowManager(window_seconds=3600)
        wins = list(wm.process(_plugin().events(str(tmp_path)))) + list(wm.finalize())
        return wins[0].capture_profile

    assert _run() == _run()


# -- locality: `local_orig`/`local_resp` -> `FlowEvent` ---------------------------
#
# `docs/WORK_ORDER_synth_v1.md` phase 1. Measured 100% populated on the 2026-08-11
# baseline, but a Zeek that never declared `Site::local_nets` omits them — and that
# case must stay distinguishable from "the responder is outside", not silently become
# it (`LES-006`). These four tests are all about that one distinction.

_LOCAL_FIELDS = (*CONN_FIELDS, "local_orig", "local_resp")


def _conn_events(tmp_path, rows, fields=_LOCAL_FIELDS):
    _tsv(tmp_path / "conn.log", fields, rows)
    return [e for e in _plugin().events(str(tmp_path)) if isinstance(e, FlowEvent)]


def test_local_orig_and_local_resp_reach_the_flow_event(tmp_path):
    (ev,) = _conn_events(tmp_path, [(*_conn_row(T0), "T", "F")])
    assert (ev.local_src, ev.local_dst) == (True, False)


def test_an_unset_locality_is_none_not_false(tmp_path):
    """The whole point of `_bool` over `_s(...) == "T"`.

    `False` would assert the endpoint is OUTSIDE the monitored network, which drops a
    real host out of the twin on evidence nobody supplied.
    """
    (ev,) = _conn_events(tmp_path, [(*_conn_row(T0), "-", "-")])
    assert ev.local_src is None and ev.local_dst is None


def test_a_conn_log_without_the_columns_at_all_still_parses(tmp_path):
    """They are OPTIONAL columns: a Zeek with no `local_nets` writes a valid log."""
    (ev,) = _conn_events(tmp_path, [_conn_row(T0)], fields=CONN_FIELDS)
    assert ev.local_src is None and ev.local_dst is None
    assert ev.src_ip == "10.0.0.21", "the rest of the row is unaffected"


def test_an_unrecognized_locality_token_is_unknown_not_false(tmp_path):
    """Fail closed: a token outside the known vocabulary is 'nobody knows', never a
    silent 'external' (CONVENTIONS §3, an unrecognized shape is reported not coerced)."""
    (ev,) = _conn_events(tmp_path, [(*_conn_row(T0), "maybe", "T")])
    assert ev.local_src is None and ev.local_dst is True


# -- radius (docs/WORK_ORDER_zeek_stems_wide_vantage.md build step 1, D1/R2) ------

def test_radius_binds_mac_and_username_to_framed_addr_not_to_either_endpoint(tmp_path):
    """The subject is `framed_addr`. Binding to an endpoint would attach every
    authenticated host to the RADIUS server -- R2, and an unrecoverable false merge.
    """
    _tsv(tmp_path / "radius.log",
         ("ts", "id.orig_h", "id.orig_p", "id.resp_h", "id.resp_p",
          "username", "mac", "framed_addr", "result"),
         [(f"{T0:.6f}", "10.0.0.9", 41001, "10.0.0.7", 1812,
           "alice", "aa:bb:cc:dd:ee:ff", "10.0.0.55", "success")])

    (ev,) = list(_plugin().events(str(tmp_path)))
    assert isinstance(ev, IdentityEvent)
    assert ev.kind == "radius"
    assert ev.ip == "10.0.0.55", "the subject is framed_addr"
    assert ev.ip not in ("10.0.0.9", "10.0.0.7"), "never either end of the RADIUS flow"
    assert ev.mac == "aa:bb:cc:dd:ee:ff" and ev.principal == "alice"


def test_radius_emits_nothing_without_a_framed_addr(tmp_path):
    """Measured reality on the ten-day baseline: all 22 rows look like this (scan
    traffic, `mac`/`framed_addr` empty). No subject, so nothing is anchored.
    """
    _tsv(tmp_path / "radius.log",
         ("ts", "id.orig_h", "id.orig_p", "id.resp_h", "id.resp_p",
          "username", "mac", "framed_addr", "result"),
         [(f"{T0:.6f}", "199.45.154.183", 59984, "50.209.234.65", 1812,
           "nessus", "-", "-", "unknown")])

    assert list(_plugin().events(str(tmp_path))) == []


def test_radius_refuses_a_framed_addr_that_is_not_an_address(tmp_path):
    """A binding is an identity claim -- parse the address, never trust the column type."""
    _tsv(tmp_path / "radius.log",
         ("ts", "id.orig_h", "id.orig_p", "id.resp_h", "id.resp_p",
          "username", "mac", "framed_addr", "result"),
         [(f"{T0:.6f}", "10.0.0.9", 41002, "10.0.0.7", 1812,
           "bob", "aa:bb:cc:dd:ee:01", "not-an-address", "success")])

    assert list(_plugin().events(str(tmp_path))) == []


def test_radius_is_no_longer_an_unrecognized_stem(tmp_path):
    """The acceptance criterion this build step moves: `radius` was one of the eight
    stems a wide vantage produces and this build reported as unrecognized.
    """
    _tsv(tmp_path / "radius.log",
         ("ts", "id.orig_h", "id.orig_p", "id.resp_h", "id.resp_p",
          "username", "mac", "framed_addr", "result"),
         [(f"{T0:.6f}", "10.0.0.9", 41003, "10.0.0.7", 1812,
           "carol", "aa:bb:cc:dd:ee:02", "10.0.0.56", "success")])

    plugin = _plugin()
    events = plugin.events(str(tmp_path))
    next(events, None)
    assert "radius" not in (plugin.unrecognized_stems or {})
    assert "radius" not in (plugin.unparsed_stems or {})


# -- build steps 2-4: the other seven wide-vantage stems -------------------------

def test_known_certs_is_keyed_on_host_not_on_a_connection_tuple(tmp_path):
    """R4: it is the `known_*` shape, not `_responder_service` -- and it is the address
    `x509` structurally lacks."""
    _tsv(tmp_path / "known_certs.log",
         ("ts", "host", "port_num", "subject", "issuer_subject", "serial"),
         [(f"{T0:.6f}", "10.0.0.22", 443, "CN=svc.example.test", "CN=Test CA", "01AB")])

    (ev,) = list(_plugin().events(str(tmp_path)))
    assert isinstance(ev, ServiceEvent)
    assert ev.kind == "known_cert" and ev.ip == "10.0.0.22" and ev.port == 443
    assert ev.attrs["subject"] == "CN=svc.example.test"


def test_rdp_emits_a_service_event_and_never_an_identity_event(tmp_path):
    """`client_name` is 0/116 on the ten-day capture and structurally invisible under NLA,
    so build step 2's IdentityEvent half is cancelled. A populated `client_name` must STILL
    not mint an identifier -- the decision is the parser's, not the data's."""
    _tsv(tmp_path / "rdp.log",
         ("ts", "id.orig_h", "id.orig_p", "id.resp_h", "id.resp_p", "cookie", "result",
          "security_protocol", "client_name"),
         [(f"{T0:.6f}", "10.0.0.21", 3390, "10.0.0.23", 3389, "mstshash=alice",
           "encrypted", "HYBRID_EX", "WORKSTN9")])

    events = list(_plugin().events(str(tmp_path)))
    assert [type(e).__name__ for e in events] == ["ServiceEvent"]
    (ev,) = events
    assert ev.kind == "rdp" and ev.ip == "10.0.0.23"
    assert ev.attrs["client_name"] == "WORKSTN9", "collected (forensic completeness)"
    assert ev.attrs["cookie"] == "mstshash=alice", "collected, never minted as a user"


def test_syslog_message_is_collected_but_never_reaches_a_projection_key(tmp_path):
    """R5, the whole of D3: free text in a projection tuple burns all SERVICE_MAX_KEYS
    slots on noise. The bound already existed; choosing the tuple right is the fix."""
    from aximprint.windowing.windows import SERVICE_PROJECTIONS

    _tsv(tmp_path / "syslog.log",
         ("ts", "id.orig_h", "id.orig_p", "id.resp_h", "id.resp_p", "proto", "facility",
          "severity", "message"),
         [(f"{T0:.6f}", "10.0.0.21", 55010, "10.0.0.25", 514, "udp", "LOCAL0", "INFO",
           "unbounded free text")])

    (ev,) = list(_plugin().events(str(tmp_path)))
    assert ev.attrs["message"] == "unbounded free text", "collected"
    projected, _, _ = SERVICE_PROJECTIONS["syslog"]
    assert "message" not in projected, "never projected"


def test_no_responder_keyed_projection_names_a_client_emitted_field():
    """R6, and `R1` before it -- twice caught by a reviewer, never encoded until here.
    A responder-keyed event must not project a field the CLIENT announced.
    """
    from aximprint.windowing.windows import SERVICE_PROJECTIONS

    client_emitted = {
        "rdp": {"client_name", "keyboard_layout", "client_build", "cookie",
                "client_dig_product_id", "client_channels"},
        "websocket": {"host", "uri", "user_agent", "client_protocols", "client_extensions"},
        "mqtt": {"client_id", "will_topic", "will_payload"},
        "rfb": {"client_major_version", "client_minor_version"},
    }
    for kind, forbidden in client_emitted.items():
        projected, _, _ = SERVICE_PROJECTIONS[kind]
        leaked = forbidden & set(projected)
        assert not leaked, f"{kind} projects client-emitted field(s): {sorted(leaked)}"


def test_redis_is_declined_by_name_rather_than_given_an_empty_projection():
    """An empty projection tuple would silently produce NOTHING (`_add_service`'s
    `if not parts: return`) while still claiming a projection exists. Declining says so."""
    from aximprint.windowing.windows import SERVICE_PROJECTIONS, _SERVICE_NOT_PROJECTED

    assert "redis" not in SERVICE_PROJECTIONS
    assert "redis" in _SERVICE_NOT_PROJECTED
    for kind, (attrs, _, _) in SERVICE_PROJECTIONS.items():
        assert attrs, f"{kind} has an empty projection tuple, which asserts nothing"


def test_all_forty_three_wide_vantage_stems_are_accounted_for_by_name():
    """The work order's headline acceptance: a twelve-stream vantage produces 43 stems and
    this build now names every one -- 36 parsed + 7 declared skipped, none unrecognized."""
    from aximprint.ingest.zeek import plugin as zp

    assert len(zp._PARSERS) == 36
    assert len(zp._SKIP) == 7
    the_eight = {"known_certs", "syslog", "rdp", "radius", "rfb", "mqtt_connect", "redis",
                 "websocket"}
    assert the_eight <= set(zp._PARSERS), sorted(the_eight - set(zp._PARSERS))
