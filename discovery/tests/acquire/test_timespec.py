"""Tests for aximprint.acquire.timespec.

The module under test is pure and small (no I/O, stdlib + aximprint.acquire.timeutil
only) so these aim for full branch coverage — per the work order, a gap here
is a latent boundary bug, not an acceptable trade-off.
"""

import ast
import pathlib

import pytest

from aximprint.acquire import timespec
from aximprint.acquire.timespec import (
    DurationSpecError,
    InvalidExpressionError,
    StartCircularityError,
    TimeSpecError,
    parse_human_duration,
    resolve_end,
    resolve_start,
)
from aximprint.acquire.timeutil import parse_duration, parse_instant

# --- The no-float invariant (LES-045's pattern) ------------------------------


def test_no_float_anywhere():
    """Same discipline as `test_timeutil.test_no_float_anywhere`: this module
    must never call `float()` or use true division on a time value. Parsed
    via `ast`, not a text scan, so a `/` in a docstring path
    (`docs/OPEN_QUESTIONS.md`) or comment can't false-positive.
    """
    source = pathlib.Path(timespec.__file__).read_text()
    tree = ast.parse(source, filename=timespec.__file__)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "float":
            pytest.fail(f"float() call at line {node.lineno} — timespec.py must never call float()")
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            pytest.fail(
                f"true division (`/`) at line {node.lineno} — timespec.py must never use "
                "true division on a time value (floor division `//` is fine)"
            )


# --- parse_human_duration: compact form (delegates to timeutil) -------------


@pytest.mark.parametrize(
    "s",
    ["30", "30s", "5m", "1h", "1d", "500ms", "100us", "100ns", "1000ms"],
)
def test_parse_human_duration_compact_matches_parse_duration(s):
    assert parse_human_duration(s) == parse_duration(s)


@pytest.mark.parametrize("s", ["0", "0s", "-5", "-5s", "1.5", "1.5s", "5x", "s", "ms"])
def test_parse_human_duration_compact_rejects(s):
    with pytest.raises(DurationSpecError) as exc_info:
        parse_human_duration(s)
    assert repr(s) in str(exc_info.value)


def test_parse_human_duration_rejects_non_string():
    with pytest.raises(DurationSpecError):
        parse_human_duration(30)  # type: ignore[arg-type]


# --- parse_human_duration: spelled-out form ----------------------------------


@pytest.mark.parametrize(
    "s, expected_ns",
    [
        ("30 days", 30 * 86400 * 1_000_000_000),
        ("1 day", 86400 * 1_000_000_000),
        ("2 hours", 2 * 3600 * 1_000_000_000),
        ("1 hour", 3600 * 1_000_000_000),
        ("90 minutes", 90 * 60 * 1_000_000_000),
        ("1 minute", 60 * 1_000_000_000),
        ("45 seconds", 45 * 1_000_000_000),
        ("1 second", 1_000_000_000),
    ],
)
def test_parse_human_duration_spelled_out(s, expected_ns):
    assert parse_human_duration(s) == expected_ns


def test_parse_human_duration_spelled_out_equivalence_with_compact():
    assert parse_human_duration("30 days") == parse_duration("30d")
    assert parse_human_duration("2 hours") == parse_duration("2h")
    assert parse_human_duration("90 minutes") == parse_duration("90m")
    assert parse_human_duration("45 seconds") == parse_duration("45s")


@pytest.mark.parametrize(
    "s",
    [
        "30 DAYS",
        "30 Days",
        "2 HOURS",
        "2 Hours",
        "90 MINUTES",
        "45 SECONDS",
    ],
)
def test_parse_human_duration_case_insensitive(s):
    # Doesn't raise, and normalizes to the same value as the lowercase form.
    assert parse_human_duration(s) == parse_human_duration(s.lower())


def test_parse_human_duration_collapses_and_strips_whitespace():
    assert parse_human_duration("  30   days  ") == parse_human_duration("30 days")


@pytest.mark.parametrize(
    "s",
    [
        "1.5 days",  # bare float — ambiguous at ns precision
        "-5 days",  # negative
        "0 days",  # zero
        "1 day 2 hours",  # mixed form
        "30 weeks",  # unsupported unit
        "day",  # missing value
    ],
)
def test_parse_human_duration_spelled_rejections(s):
    with pytest.raises(DurationSpecError) as exc_info:
        parse_human_duration(s)
    assert repr(s) in str(exc_info.value)


def test_parse_human_duration_zero_spelled_names_input():
    with pytest.raises(DurationSpecError) as exc_info:
        parse_human_duration("0 days")
    assert repr("0 days") in str(exc_info.value)


def test_parse_human_duration_error_is_a_time_spec_error():
    assert issubclass(DurationSpecError, TimeSpecError)


# --- resolve_start: absolute epoch -------------------------------------------

NOW = parse_instant("1785934195")


def test_resolve_start_absolute_epoch_integer():
    assert resolve_start("1785934195", now=NOW) == parse_instant("1785934195")


def test_resolve_start_absolute_epoch_fractional():
    result = resolve_start("1785934195.5", now=NOW)
    assert result.ns == parse_instant("1785934195.5").ns


# --- resolve_start: now ------------------------------------------------------


def test_resolve_start_now():
    assert resolve_start("now", now=NOW).ns == NOW.ns


def test_resolve_start_now_case_insensitive():
    assert resolve_start("NOW", now=NOW).ns == NOW.ns
    assert resolve_start("Now", now=NOW).ns == NOW.ns


# --- resolve_start: <duration> back from now / ago ---------------------------


def test_resolve_start_back_from_now():
    result = resolve_start("30 days back from now", now=NOW)
    assert result.ns == (NOW - parse_duration("30d")).ns


def test_resolve_start_ago():
    result = resolve_start("30 days ago", now=NOW)
    assert result.ns == (NOW - parse_duration("30d")).ns


def test_resolve_start_ago_compact_duration():
    result = resolve_start("30d ago", now=NOW)
    assert result.ns == (NOW - parse_duration("30d")).ns


def test_resolve_start_back_from_now_and_ago_are_equivalent():
    a = resolve_start("30 days back from now", now=NOW)
    b = resolve_start("30 days ago", now=NOW)
    assert a.ns == b.ns


# --- resolve_start: from-the-start rejected (StartCircularityError) ---------


@pytest.mark.parametrize(
    "expr",
    [
        "2 days from the start",
        "2 days after the start",
        "2 days from start",
        "2 days after start",
    ],
)
def test_resolve_start_from_start_is_circularity_error(expr):
    with pytest.raises(StartCircularityError) as exc_info:
        resolve_start(expr, now=NOW)
    assert repr(expr) in str(exc_info.value)
    # StartCircularityError is a distinguishable subtype of the generic
    # grammar error, so a caller can catch either.
    assert isinstance(exc_info.value, InvalidExpressionError)


# --- resolve_start: unknown phrasing -----------------------------------------


@pytest.mark.parametrize(
    "expr",
    [
        "",
        "   ",
        "yesterday",
        "next tuesday",
        "30 days",
        "ago",
        "back from now",
        "not a real expression",
    ],
)
def test_resolve_start_unknown_phrasing_rejected(expr):
    with pytest.raises(InvalidExpressionError) as exc_info:
        resolve_start(expr, now=NOW)
    assert "--start" in str(exc_info.value)


def test_resolve_start_rejects_non_string():
    with pytest.raises(InvalidExpressionError):
        resolve_start(1785934195, now=NOW)  # type: ignore[arg-type]


def test_resolve_start_rejects_non_instant_now():
    with pytest.raises(TypeError):
        resolve_start("now", now=1785934195)  # type: ignore[arg-type]


def test_resolve_start_propagates_duration_error():
    with pytest.raises(DurationSpecError):
        resolve_start("1.5 days ago", now=NOW)


# --- resolve_end: every row of the table -------------------------------------

START = parse_instant("1785934195")


def test_resolve_end_absolute_epoch():
    result = resolve_end("1785934225", start=START, now=NOW)
    assert result.ns == parse_instant("1785934225").ns


def test_resolve_end_now():
    assert resolve_end("now", start=START, now=NOW).ns == NOW.ns


def test_resolve_end_back_from_now():
    result = resolve_end("2 days back from now", start=START, now=NOW)
    assert result.ns == (NOW - parse_duration("2d")).ns


def test_resolve_end_ago():
    result = resolve_end("2 days ago", start=START, now=NOW)
    assert result.ns == (NOW - parse_duration("2d")).ns


def test_resolve_end_from_the_start():
    result = resolve_end("2 days from the start", start=START, now=NOW)
    assert result.ns == (START + parse_duration("2d")).ns


def test_resolve_end_after_the_start():
    result = resolve_end("2 days after the start", start=START, now=NOW)
    assert result.ns == (START + parse_duration("2d")).ns


def test_resolve_end_from_start_the_optional():
    a = resolve_end("2 days from start", start=START, now=NOW)
    b = resolve_end("2 days from the start", start=START, now=NOW)
    assert a.ns == b.ns


def test_resolve_end_after_start_the_optional():
    a = resolve_end("2 days after start", start=START, now=NOW)
    b = resolve_end("2 days after the start", start=START, now=NOW)
    assert a.ns == b.ns


def test_resolve_end_none_returns_none():
    assert resolve_end(None, start=START, now=NOW) is None


def test_resolve_end_unknown_phrasing_rejected():
    with pytest.raises(InvalidExpressionError) as exc_info:
        resolve_end("not a real expression", start=START, now=NOW)
    assert "--end" in str(exc_info.value)


def test_resolve_end_rejects_non_string():
    with pytest.raises(InvalidExpressionError):
        resolve_end(1785934195, start=START, now=NOW)  # type: ignore[arg-type]


def test_resolve_end_rejects_non_instant_now():
    with pytest.raises(TypeError):
        resolve_end("now", start=START, now=1785934195)  # type: ignore[arg-type]


def test_resolve_end_rejects_non_instant_start():
    with pytest.raises(TypeError):
        resolve_end("2 days from the start", start=1785934195, now=NOW)  # type: ignore[arg-type]


def test_resolve_end_none_skips_now_and_start_validation():
    # expr is None short-circuits before now/start are even inspected — a
    # caller resolving "latest stored data" shouldn't need a real Instant
    # for start when there isn't one to reference yet.
    assert resolve_end(None, start="not an instant", now="not an instant") is None  # type: ignore[arg-type]


def test_resolve_end_propagates_duration_error():
    with pytest.raises(DurationSpecError):
        resolve_end("1.5 days ago", start=START, now=NOW)


def test_resolve_end_propagates_duration_error_from_start_form():
    with pytest.raises(DurationSpecError):
        resolve_end("1.5 days from the start", start=START, now=NOW)


# --- Phase 3: purity and the clock seam --------------------------------------


def test_resolving_same_expression_twice_with_same_now_is_identical():
    a = resolve_start("30 days ago", now=NOW)
    b = resolve_start("30 days ago", now=NOW)
    assert a.ns == b.ns


def test_resolve_start_now_ns_equals_injected_now():
    x = parse_instant("1700000000")
    assert resolve_start("now", now=x).ns == x.ns


def test_resolve_end_resolving_from_start_twice_is_identical():
    a = resolve_end("2 days from the start", start=START, now=NOW)
    b = resolve_end("2 days from the start", start=START, now=NOW)
    assert a.ns == b.ns


# --- shared error hierarchy ---------------------------------------------------


def test_time_spec_error_is_also_an_xtract_error():
    from aximprint.acquire.errors import XtractError

    assert issubclass(TimeSpecError, XtractError)
    assert issubclass(TimeSpecError, ValueError)


def test_invalid_expression_error_is_a_time_spec_error():
    assert issubclass(InvalidExpressionError, TimeSpecError)


def test_start_circularity_error_is_an_invalid_expression_error():
    assert issubclass(StartCircularityError, InvalidExpressionError)


# --- implausible-instant arithmetic (finding 5: `now - duration` can go negative) -------------


def test_resolve_start_ago_far_enough_back_raises_implausible_instant_error():
    """`parse_instant` already rejects a negative literal on input, so `now - duration` arithmetic
    is the only way `timespec.py` can still manufacture one — 30000 days back from `NOW` (2026-08)
    walks straight past epoch zero. Must raise, never silently hand back a negative `Instant` that
    `format_instant` would render as `start_time=-806065805...` on the wire.
    """
    from aximprint.acquire.timeutil import ImplausibleInstantError

    with pytest.raises(ImplausibleInstantError):
        resolve_start("30000 days ago", now=NOW)


def test_resolve_start_ago_far_enough_back_never_produces_a_negative_instant():
    """Belt-and-suspenders on the above: even if some future refactor swapped the exception type,
    a negative `ns` must never escape this module — assert it directly, not just the error type.
    """
    from aximprint.acquire.timeutil import ImplausibleInstantError

    try:
        result = resolve_start("30000 days ago", now=NOW)
    except ImplausibleInstantError:
        return
    assert result.ns >= 0, f"resolve_start produced a negative instant: {result.ns}"


def test_resolve_end_from_the_start_far_enough_forward_raises_implausible_instant_error():
    """The `<duration> from the start` arithmetic path is exercised too, not just `ago` — both are
    the same class of bug (unchecked `Instant` arithmetic reaching the caller)."""
    from aximprint.acquire.timeutil import ImplausibleInstantError

    with pytest.raises(ImplausibleInstantError):
        resolve_end("500000 days from the start", start=START, now=NOW)


def test_implausible_instant_error_reused_not_redeclared():
    """LES-041-style discipline, stated as a test: `timespec.py` must reuse `timeutil`'s existing
    `ImplausibleInstantError`/`MIN_PLAUSIBLE_NS`/`MAX_PLAUSIBLE_NS` trip-wire rather than declare a
    second, parallel one — so `except ImplausibleInstantError` at any existing call site (e.g.
    `catalog.py`'s use of `instant_from_fs_field`) already catches this path too.
    """
    import aximprint.acquire.timeutil as timeutil_mod

    assert timespec.ImplausibleInstantError is timeutil_mod.ImplausibleInstantError
    assert timespec.MIN_PLAUSIBLE_NS == timeutil_mod.MIN_PLAUSIBLE_NS
    assert timespec.MAX_PLAUSIBLE_NS == timeutil_mod.MAX_PLAUSIBLE_NS


def test_ordinary_resolve_start_and_resolve_end_are_unaffected_by_the_plausibility_check():
    """The plausibility check must not false-positive on any value already covered by the
    existing test suite above — a regression here would silently reject perfectly ordinary
    windows, not just implausible ones.
    """
    assert resolve_start("30 days ago", now=NOW).ns == (NOW - parse_duration("30d")).ns
    assert resolve_end("2 days from the start", start=START, now=NOW).ns == (
        START + parse_duration("2d")
    ).ns
