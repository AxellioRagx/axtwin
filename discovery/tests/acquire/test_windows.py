"""Tests for aximprint.acquire.windows.

The module under test is pure and small (no I/O, stdlib + timeutil only) so
these aim for full branch coverage — per the work order, a gap here is a
latent boundary bug, not an acceptable trade-off.
"""

import itertools
import pathlib

import pytest

from aximprint.acquire import windows
from aximprint.acquire.timeutil import (
    Duration,
    Instant,
    format_instant,
    parse_duration,
    parse_instant,
)
from aximprint.acquire.windows import (
    DEFAULT_MAX_WINDOWS,
    InvalidIntervalError,
    InvalidMaxWindowsError,
    InvalidRangeError,
    TooManyWindowsError,
    Window,
    WindowsError,
    plan,
)


def _i(seconds: int) -> Instant:
    return parse_instant(str(seconds))


def _d(seconds: int) -> Duration:
    return parse_duration(f"{seconds}s")


# --- Phase 1: window generation ----------------------------------------------


def test_evenly_divisible_range_tiles_exactly():
    ws = plan(_i(0), _i(9), _d(3))
    assert [(w.start.ns, w.end.ns) for w in ws] == [
        (0, 3_000_000_000),
        (3_000_000_000, 6_000_000_000),
        (6_000_000_000, 9_000_000_000),
    ]
    assert [w.index for w in ws] == [1, 2, 3]
    assert all(w.total == 3 for w in ws)


def test_unevenly_divisible_range_clips_final_window():
    # 10-second range, 3-second interval: [0,3) [3,6) [6,9) [9,10) — 4 windows,
    # the last one short. This is the work order's canonical example.
    ws = plan(_i(0), _i(10), _d(3))
    assert [(w.start.ns, w.end.ns) for w in ws] == [
        (0, 3_000_000_000),
        (3_000_000_000, 6_000_000_000),
        (6_000_000_000, 9_000_000_000),
        (9_000_000_000, 10_000_000_000),
    ]
    assert [w.index for w in ws] == [1, 2, 3, 4]
    assert all(w.total == 4 for w in ws)
    # The final window is short, not extended past `end`.
    assert ws[-1].end == _i(10)


def test_final_window_never_extended_past_end():
    ws = plan(_i(0), _i(10), _d(3))
    assert all(w.end <= _i(10) for w in ws)


def test_final_window_never_dropped():
    # A remainder of exactly 1ns must still produce a final window.
    start = Instant(ns=0, fractional=False)
    end = Instant(ns=3_000_000_001, fractional=False)
    ws = plan(start, end, Duration(1_000_000_000))
    assert len(ws) == 4
    assert ws[-1].start.ns == 3_000_000_000
    assert ws[-1].end.ns == 3_000_000_001


def test_start_equals_end_yields_zero_windows():
    assert plan(_i(5), _i(5), _d(3)) == []


def test_end_before_start_raises_naming_both_values():
    with pytest.raises(InvalidRangeError) as exc_info:
        plan(_i(10), _i(5), _d(1))
    message = str(exc_info.value)
    assert format_instant(_i(10)) in message
    assert format_instant(_i(5)) in message


def test_interval_larger_than_range_yields_one_clipped_window():
    ws = plan(_i(0), _i(5), _d(30))
    assert len(ws) == 1
    assert ws[0].start == _i(0)
    assert ws[0].end == _i(5)
    assert ws[0].index == 1
    assert ws[0].total == 1


def test_interval_equal_to_range_yields_one_unclipped_window():
    ws = plan(_i(0), _i(5), _d(5))
    assert len(ws) == 1
    assert ws[0].start == _i(0)
    assert ws[0].end == _i(5)


@pytest.mark.parametrize("bad_interval", [0, -1, -1_000_000_000])
def test_non_positive_interval_raises(bad_interval):
    with pytest.raises(InvalidIntervalError):
        plan(_i(0), _i(10), Duration(bad_interval))


def test_non_int_interval_raises():
    with pytest.raises(InvalidIntervalError):
        plan(_i(0), _i(10), 1.5)  # type: ignore[arg-type]


def test_bool_interval_raises():
    # bool is an int subclass in Python; must be rejected explicitly, same
    # discipline as timeutil's Instant.__add__.
    with pytest.raises(InvalidIntervalError):
        plan(_i(0), _i(10), True)  # type: ignore[arg-type]


def test_windows_are_contiguous_non_overlapping_and_cover_the_range():
    start, end = _i(0), _i(97)
    ws = plan(start, end, _d(11))
    assert ws[0].start == start
    assert ws[-1].end == end
    for a, b in itertools.pairwise(ws):
        assert a.end == b.start  # contiguous: no gap, no overlap


def test_sub_second_interval_no_drift_across_thousands_of_windows():
    """The test that would catch a `float` creeping in.

    A sub-second interval (100ms) tiled across ten thousand windows must
    land on exact nanosecond boundaries — `float` accumulation would drift
    by the ten-thousandth window; `int` arithmetic cannot.
    """
    interval = parse_duration("100ms")
    n = 10_000
    start = Instant(ns=0, fractional=False)
    end = Instant(ns=n * interval, fractional=False)
    ws = plan(start, end, interval, max_windows=n + 1)
    assert len(ws) == n
    for i, w in enumerate(ws):
        assert w.start.ns == i * interval
        assert w.end.ns == (i + 1) * interval
    assert ws[-1].end == end


# --- Phase 2: wire encoding and Q22 -------------------------------------------


def test_q22_constant_is_flagged():
    """Q22 (docs/OPEN_QUESTIONS.md) must live behind exactly one flagged
    constant, and nowhere else may compensate for boundary semantics."""
    source = pathlib.Path(windows.__file__).read_text()
    assert "Q22_END_TIME_INCLUSIVE" in source
    assert "# Q22:" in source
    assert "docs/OPEN_QUESTIONS.md" in source


def test_wire_params_start_time_is_exactly_format_instant():
    w = Window(start=_i(3), end=_i(6), index=1, total=1)
    assert w.wire_params()["start_time"] == format_instant(_i(3))


def test_wire_params_end_time_unadjusted_when_q22_exclusive(monkeypatch):
    monkeypatch.setattr(windows, "Q22_END_TIME_INCLUSIVE", False)
    w = Window(start=_i(3), end=_i(6), index=1, total=1)
    assert w.wire_params()["end_time"] == format_instant(_i(6))


def test_wire_params_end_time_adjusted_when_q22_inclusive(monkeypatch):
    monkeypatch.setattr(windows, "Q22_END_TIME_INCLUSIVE", True)
    w = Window(start=_i(3), end=_i(6), index=1, total=1)
    expected = format_instant(Instant(ns=6_000_000_000 - 1, fractional=True))
    assert w.wire_params()["end_time"] == expected
    assert w.wire_params()["end_time"] != format_instant(_i(6))


def test_wire_params_encoding_flips_with_the_constant(monkeypatch):
    """The encoding must flip correctly when the constant flips — this is the
    test the work order calls out by name."""
    w = Window(start=_i(3), end=_i(6), index=1, total=1)

    monkeypatch.setattr(windows, "Q22_END_TIME_INCLUSIVE", False)
    exclusive_end_time = w.wire_params()["end_time"]

    monkeypatch.setattr(windows, "Q22_END_TIME_INCLUSIVE", True)
    inclusive_end_time = w.wire_params()["end_time"]

    assert exclusive_end_time != inclusive_end_time
    assert exclusive_end_time == format_instant(_i(6))
    assert inclusive_end_time == format_instant(Instant(ns=6_000_000_000 - 1, fractional=True))


def test_plan_windows_wire_params_round_trip_through_the_plan():
    ws = plan(_i(0), _i(10), _d(3))
    assert [w.wire_params()["start_time"] for w in ws] == ["0", "3", "6", "9"]
    # Q22: end_time is inclusive on the wire (Q22_END_TIME_INCLUSIVE == True by
    # default), so each window's logical end is sent as end - 1ns.
    assert [w.wire_params()["end_time"] for w in ws] == [
        "2.999999999",
        "5.999999999",
        "8.999999999",
        "9.999999999",
    ]


# --- Phase 3: guardrails ------------------------------------------------------


def test_max_windows_zero_raises_actionable_error_not_zero_division():
    """Reproduces the reviewer's finding: `max_windows=0` used to reach the
    ceiling branch's `-(-span // max_windows)` "suggest a smaller interval"
    calculation and divide by zero instead of raising a clear, actionable
    error.
    """
    with pytest.raises(InvalidMaxWindowsError):
        plan(_i(0), _i(100), _d(10), max_windows=0)


def test_max_windows_negative_raises_actionable_error():
    with pytest.raises(InvalidMaxWindowsError):
        plan(_i(0), _i(100), _d(10), max_windows=-1)


def test_max_windows_non_int_raises():
    with pytest.raises(InvalidMaxWindowsError):
        plan(_i(0), _i(100), _d(10), max_windows=1.5)  # type: ignore[arg-type]


def test_default_max_windows_ceiling_fires_with_actionable_message():
    start = Instant(ns=0, fractional=False)
    end = Instant(ns=(DEFAULT_MAX_WINDOWS + 1) * 1_000_000_000, fractional=False)
    interval = Duration(1_000_000_000)
    with pytest.raises(TooManyWindowsError) as exc_info:
        plan(start, end, interval)
    message = str(exc_info.value)
    assert str(DEFAULT_MAX_WINDOWS + 1) in message  # the count
    assert str(interval) in message  # the interval that produced it
    # An interval that would stay under the ceiling is also named.
    assert "ns" in message
    assert f"max_windows={DEFAULT_MAX_WINDOWS}" in message


def test_ceiling_does_not_fire_exactly_at_the_boundary():
    start = Instant(ns=0, fractional=False)
    end = Instant(ns=DEFAULT_MAX_WINDOWS * 1_000_000_000, fractional=False)
    interval = Duration(1_000_000_000)
    ws = plan(start, end, interval)
    assert len(ws) == DEFAULT_MAX_WINDOWS


def test_yes_equivalent_override_permits_a_larger_plan():
    start = Instant(ns=0, fractional=False)
    end = Instant(ns=(DEFAULT_MAX_WINDOWS + 1) * 1_000_000_000, fractional=False)
    interval = Duration(1_000_000_000)
    ws = plan(start, end, interval, max_windows=DEFAULT_MAX_WINDOWS + 1)
    assert len(ws) == DEFAULT_MAX_WINDOWS + 1


def test_ceiling_error_suggested_interval_actually_stays_under_ceiling():
    start = Instant(ns=0, fractional=False)
    end = Instant(ns=100_000 * 1_000_000_000, fractional=False)
    interval = Duration(1_000_000_000)
    max_windows = 10
    with pytest.raises(TooManyWindowsError) as exc_info:
        plan(start, end, interval, max_windows=max_windows)
    message = str(exc_info.value)
    # Pull the suggested interval (in ns) back out of the message and prove
    # it actually keeps the plan at or under the ceiling.
    import re

    match = re.search(r"at least (\d+)ns", message)
    assert match is not None, message
    suggested_ns = int(match.group(1))
    ws = plan(start, end, Duration(suggested_ns), max_windows=max_windows)
    assert len(ws) <= max_windows


# --- shared error hierarchy ----------------------------------------------------


def test_windows_error_is_also_an_xtract_error():
    from aximprint.acquire.errors import XtractError

    assert issubclass(WindowsError, XtractError)
    assert issubclass(WindowsError, ValueError)
