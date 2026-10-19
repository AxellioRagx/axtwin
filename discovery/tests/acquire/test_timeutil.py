"""Tests for aximprint.acquire.timeutil.

The module under test is pure and small (no I/O, stdlib only) so these aim for
full branch coverage — per the work order, a gap here is a latent boundary
bug, not an acceptable trade-off.
"""

import ast
import pathlib

import pytest

from aximprint.acquire import timeutil
from aximprint.acquire.timeutil import (
    DurationParseError,
    ImplausibleInstantError,
    Instant,
    InstantParseError,
    TimeUtilError,
    format_instant,
    instant_from_fs_field,
    parse_duration,
    parse_instant,
)

# --- The no-float invariant -------------------------------------------------


def test_no_float_anywhere():
    """The single most important property of this module.

    Every `axapi` time field is a wire `string`; internally time is `int`
    nanoseconds. A `float(...)` call, or a true-division `/`, silently
    truncates a nanosecond value to double precision (~microseconds) and every
    window boundary computed from it downstream inherits the error.

    Parsed via `ast` (not a text scan) so comments and docstrings — which
    legitimately contain `/` in prose and paths like `docs/OPEN_QUESTIONS.md`
    — can't produce a false positive; only real code is inspected.
    """
    source = pathlib.Path(timeutil.__file__).read_text()
    tree = ast.parse(source, filename=timeutil.__file__)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "float":
            pytest.fail(f"float() call at line {node.lineno} — timeutil.py must never call float()")
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            pytest.fail(
                f"true division (`/`) at line {node.lineno} — timeutil.py must never use "
                "true division on a time value (floor division `//` is fine)"
            )


def test_q19_constant_is_flagged():
    """Q19 (docs/OPEN_QUESTIONS.md) must live behind exactly one flagged constant."""
    source = pathlib.Path(timeutil.__file__).read_text()
    assert "FS_FIELD_NS_PER_UNIT" in source
    assert "# Q19:" in source
    assert "docs/OPEN_QUESTIONS.md" in source


# --- parse_instant / format_instant -----------------------------------------

ROUND_TRIP_STRINGS = [
    "0",
    "1728484411",
    "1728484411.293105240",
    # Precision is preserved, not normalized: an explicit all-zero fraction
    # must not collapse to a bare integer.
    "1728484411.000000000",
    "0.000000001",
    "0.100000000",
]


@pytest.mark.parametrize("s", ROUND_TRIP_STRINGS)
def test_round_trip(s):
    assert format_instant(parse_instant(s)) == s


@pytest.mark.parametrize(
    "s, expected_ns",
    [
        ("0", 0),
        ("1728484411", 1728484411_000000000),
        ("1728484411.5", 1728484411_500000000),
        ("1728484411.05", 1728484411_050000000),
        ("1728484411.293105240", 1728484411_293105240),
        ("1728484411.123456789", 1728484411_123456789),
    ],
)
def test_parse_instant_magnitude(s, expected_ns):
    assert parse_instant(s).ns == expected_ns


def test_parse_instant_fractional_flag():
    assert parse_instant("5").fractional is False
    assert parse_instant("5.0").fractional is True
    assert parse_instant("5.000000000").fractional is True


@pytest.mark.parametrize(
    "s",
    [
        "",
        " ",
        "abc",
        "-5",
        "-5.0",
        "1.1234567890",  # 10 fractional digits
        " 5",
        "5 ",
        "5 5",
        "1e9",
        "1.5e3",
        "+5",
        "+5.0",
        "1.2.3",
        "5.",
        ".5",
        "5,0",
        "0x5",
    ],
)
def test_parse_instant_rejects(s):
    with pytest.raises(InstantParseError) as exc_info:
        parse_instant(s)
    assert repr(s) in str(exc_info.value)


def test_parse_instant_rejects_non_string():
    with pytest.raises(InstantParseError):
        parse_instant(5)  # type: ignore[arg-type]


def test_format_instant_never_truncates_regardless_of_flag():
    """format_instant must never silently drop a real sub-second remainder,
    even if (through misuse or a future caller) the fractional flag says
    False. The remainder itself, not just the flag, decides."""
    i = Instant(ns=1_500_000_000, fractional=False)
    assert format_instant(i) == "1.500000000"


def test_format_instant_whole_second_no_flag_is_bare_integer():
    i = Instant(ns=5_000_000_000, fractional=False)
    assert format_instant(i) == "5"


# --- parse_duration ----------------------------------------------------------


@pytest.mark.parametrize(
    "s, expected_ns",
    [
        ("30", 30_000_000_000),
        ("30s", 30_000_000_000),
        ("5m", 5 * 60 * 1_000_000_000),
        ("1h", 3600 * 1_000_000_000),
        ("1d", 86400 * 1_000_000_000),
        ("500ms", 500_000_000),
        ("100us", 100_000),
        ("100ns", 100),
        ("1000ms", 1_000_000_000),
    ],
)
def test_parse_duration_units(s, expected_ns):
    assert parse_duration(s) == expected_ns


def test_parse_duration_ms_seconds_equivalence():
    assert parse_duration("1000ms") == parse_duration("1s")


@pytest.mark.parametrize(
    "s",
    [
        "",
        "0",
        "0s",
        "-5",
        "-5s",
        "1.5",
        "1.5s",
        "5x",
        "s",
        "ms",
        "5 s",
        " 5s",
    ],
)
def test_parse_duration_rejects(s):
    with pytest.raises(DurationParseError) as exc_info:
        parse_duration(s)
    assert repr(s) in str(exc_info.value)


def test_parse_duration_rejects_non_string():
    with pytest.raises(DurationParseError):
        parse_duration(30)  # type: ignore[arg-type]


# --- instant_from_fs_field (Q19 — RESOLVED: epoch-seconds strings) ------------


def test_instant_from_fs_field_passes_through_exactly():
    v = "1728484411"
    result = instant_from_fs_field(v)
    assert result.ns == int(v) * timeutil.FS_FIELD_NS_PER_UNIT
    assert result.fractional is False


def test_instant_from_fs_field_live_probe_evidence_record():
    """The exact live-probe record (`docs/AXAPI_CONTRACT.md` §5.3, 2026-08-05):
    `"time_start": "1781795842"` converts to 2026-06-18, matching that same
    file record's own `"date": "Jun 18 15:17"` field — the evidence that
    resolved Q19.
    """
    from datetime import UTC, datetime

    result = instant_from_fs_field("1781795842")
    dt = datetime.fromtimestamp(result.ns / timeutil.NS_PER_SECOND, tz=UTC)
    assert (dt.year, dt.month, dt.day) == (2026, 6, 18)


def test_instant_from_fs_field_too_small_raises():
    # "0" converts to ns == 0 under any positive unit constant — always well
    # before the 1990 floor.
    with pytest.raises(ImplausibleInstantError):
        instant_from_fs_field("0")


def test_instant_from_fs_field_negative_raises_instant_parse_error():
    # A leading "-" is not an all-digit string, so this is a typed parse
    # failure (InstantParseError), not a plausibility failure — `axapi` never
    # sends a negative fs field, but a malformed one must still fail loudly
    # and typed, never a bare ValueError.
    with pytest.raises(InstantParseError):
        instant_from_fs_field("-1")


def test_instant_from_fs_field_astronomically_large_raises():
    # Guaranteed past the 2100 ceiling under any plausible unit constant.
    with pytest.raises(ImplausibleInstantError):
        instant_from_fs_field(str(10**30))


def test_instant_from_fs_field_ms_ns_mixup_fails_loudly():
    """The concrete failure mode the work order calls out: a millisecond-epoch
    value run through a seconds-assuming conversion is six orders of
    magnitude off, and must raise rather than silently returning a
    plausible-looking wrong range.

    This test is pinned to the resolved Q19 unit (seconds, confirmed by the
    live probe — see `test_instant_from_fs_field_live_probe_evidence_record`).
    """
    assert timeutil.FS_FIELD_NS_PER_UNIT == 1_000_000_000, (
        "this test encodes the resolved Q19 unit (seconds)"
    )
    ms_epoch_value = "1728484411293"  # a real millisecond-epoch timestamp
    with pytest.raises(ImplausibleInstantError):
        instant_from_fs_field(ms_epoch_value)


@pytest.mark.parametrize("v", [1728484411, 1728484411.0, True, "available,", "", "1.5", None])
def test_instant_from_fs_field_rejects_non_numeric_string(v):
    """`GET /axapi/fs/files` returns `time_start`/`time_end` as strings, not
    the `integer` both vendor PDFs claimed (Q19, resolved by live probe) — a
    bare `int`, a `float`, a `bool`, or a non-digit string (including the
    disk-usage-footer junk record's `"time_start": "available,"`, per
    `docs/AXAPI_CONTRACT.md` §5.3) must all raise the typed
    `InstantParseError`, never a bare `ValueError`/`TypeError` from an
    unguarded `int(v)`.
    """
    with pytest.raises(InstantParseError):
        instant_from_fs_field(v)


# --- Instant arithmetic -------------------------------------------------------


def test_add_whole_second_duration_stays_integer_precision():
    i = parse_instant("100")
    result = i + parse_duration("30s")
    assert result.ns == 130_000_000_000
    assert result.fractional is False


def test_add_sub_second_duration_becomes_fractional():
    i = parse_instant("100")
    result = i + parse_duration("500ms")
    assert result.ns == 100_500_000_000
    assert result.fractional is True


def test_add_to_fractional_instant_stays_fractional():
    i = parse_instant("100.5")
    result = i + parse_duration("30s")
    assert result.ns == 130_500_000_000
    assert result.fractional is True


def test_radd_is_commutative():
    i = parse_instant("100")
    d = parse_duration("500ms")
    assert (d + i) == (i + d)
    assert (d + i).fractional is True


def test_add_rejects_non_int():
    with pytest.raises(TypeError):
        parse_instant("100") + "500ms"


def test_add_rejects_bool():
    with pytest.raises(TypeError):
        parse_instant("100") + True


def test_sub_instant_minus_duration():
    i = parse_instant("100.5")
    result = i - parse_duration("500ms")
    assert result.ns == 100_000_000_000
    assert result.fractional is True  # still fractional: sub-second delta touched it


def test_sub_instant_minus_instant_yields_duration():
    a = parse_instant("100")
    b = parse_instant("30")
    assert (a - b) == 70_000_000_000


def test_sub_rejects_unsupported_type():
    with pytest.raises(TypeError):
        parse_instant("100") - "30"


# --- Instant equality / ordering ---------------------------------------------


def test_equality_ignores_fractional_flag():
    a = Instant(ns=5_000_000_000, fractional=False)
    b = Instant(ns=5_000_000_000, fractional=True)
    assert a == b
    assert hash(a) == hash(b)


def test_equality_against_non_instant():
    assert (parse_instant("5") == 5) is False


def test_ordering():
    a = parse_instant("5")
    b = parse_instant("10")
    assert a < b
    assert b > a


def test_ordering_is_reflexive_across_distinct_equal_instants():
    # Distinct objects with the same ns, so this exercises the comparison
    # logic rather than an identity shortcut.
    a = parse_instant("5")
    a_again = parse_instant("5")
    assert a is not a_again
    assert a <= a_again
    assert a >= a_again
    assert not (a < a_again)


def test_ordering_against_non_instant_raises():
    with pytest.raises(TypeError):
        _ = parse_instant("5") < 5


# --- shared error hierarchy ----------------------------------------------------


def test_time_util_error_is_also_an_xtract_error():
    """`TimeUtilError` is reparented under the shared `aximprint.acquire.errors.XtractError`
    root (alongside its existing `ValueError` base, so `except ValueError`
    call sites keep working) — so `cli.py`'s `_run_mapping_errors` can
    recognize it as a known xtract error rather than an unexpected bug.
    """
    from aximprint.acquire.errors import XtractError

    assert issubclass(TimeUtilError, XtractError)
    assert issubclass(TimeUtilError, ValueError)


# --- NS_PER_SECOND is the one, public, shared copy of this constant ------------


def test_ns_per_second_is_exported_and_correct():
    """`cli.py` used to redeclare its own private `_NS_PER_SECOND` copy of
    this constant for display formatting — a duplicate that could silently
    drift from this module's. `timeutil.NS_PER_SECOND` is the one copy every
    other module should import.
    """
    from aximprint.acquire.timeutil import NS_PER_SECOND

    assert NS_PER_SECOND == 1_000_000_000
