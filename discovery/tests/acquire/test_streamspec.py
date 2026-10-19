"""Tests for aximprint.acquire.streamspec.

The module under test is pure and small (no I/O, stdlib only) so these aim
for full branch coverage, same discipline as tests/acquire/test_timeutil.py and
tests/acquire/test_windows.py.
"""

import random

import pytest

from aximprint.acquire.streamspec import (
    DEFAULT_EXPAND_LIMIT,
    STREAM_ID_MAX,
    STREAM_ID_MIN,
    InvalidStreamSpecError,
    StreamSpecError,
    TooManyStreamIdsError,
    expand,
    validate,
)

# --- validate(): each grammar form -------------------------------------------


@pytest.mark.parametrize(
    "spec",
    [
        "0",
        "5",
        "1,2,3",
        "2-4",
        "2..4",
        "0,2-4,7..9",
        "4294967296",  # the documented upper bound, inclusive
        "0-4294967296",  # the full range is syntactically valid...
        str(STREAM_ID_MIN),
        str(STREAM_ID_MAX),
    ],
)
def test_validate_accepts_documented_grammar_forms(spec):
    validate(spec)  # must not raise


@pytest.mark.parametrize(
    "spec",
    [
        "",
        ",",
        "1,",
        ",1",
        "1,,2",
        "abc",
        "1-",
        "-1",
        "1..",
        "1.2",
        "1 - 2",
        " 1",
        "1,a-3",
        "4-2",  # lo > hi
        "4..2",
        "-1,2",
        "4294967297",  # one past the documented max
        "-5",
        "1e3",
    ],
)
def test_validate_rejects_malformed_input_naming_the_element(spec):
    with pytest.raises(InvalidStreamSpecError) as exc_info:
        validate(spec)
    # The error names the spec or the specific offending element, not just
    # "invalid" — a bare "invalid spec" message would leave an operator
    # guessing which of several comma-separated elements was the problem.
    assert str(exc_info.value)


def test_validate_rejects_non_string():
    with pytest.raises(InvalidStreamSpecError):
        validate(2)


# --- `-` and `..` equivalence -------------------------------------------------


def test_dash_and_dotdot_are_equivalent_range_separators():
    assert expand("2-4") == expand("2..4") == [2, 3, 4]


# --- expand(): sorted, deduplicated, overlapping ranges dedupe --------------


def test_expand_sorts_and_dedupes_plain_list():
    assert expand("5,1,3,1") == [1, 3, 5]


def test_expand_overlapping_ranges_dedupe():
    assert expand("1-3,2-4") == [1, 2, 3, 4]


def test_expand_mixed_singles_and_ranges():
    assert expand("0,2-4,7..9") == [0, 2, 3, 4, 7, 8, 9]


def test_expand_single_value_range_is_one_id():
    assert expand("5-5") == [5]


def test_expand_bare_zero():
    assert expand("0") == [0]


def test_expand_upper_bound_alone():
    assert expand(str(STREAM_ID_MAX)) == [STREAM_ID_MAX]


# --- the expansion cap --------------------------------------------------------


def test_expand_cap_fires_before_materializing_the_full_range():
    # This must fail fast, not hang trying to build a multi-billion-element
    # list — the literal foot-gun the work order calls out.
    with pytest.raises(TooManyStreamIdsError) as exc_info:
        expand("0-4294967296")
    assert "4294967296" not in str(exc_info.value) or "would expand" in str(exc_info.value)


def test_expand_cap_is_configurable():
    with pytest.raises(TooManyStreamIdsError):
        expand("0-10", max_ids=5)
    assert expand("0-10", max_ids=11) == list(range(11))


def test_expand_default_limit_allows_a_realistic_spec():
    assert len(expand("0-1000")) == 1001
    assert DEFAULT_EXPAND_LIMIT >= 1001


def test_expand_validates_before_expanding():
    # A malformed spec must raise InvalidStreamSpecError, not
    # TooManyStreamIdsError or some other accident of expansion order.
    with pytest.raises(InvalidStreamSpecError):
        expand("4-2")


# --- property test: expand() never reorders or drops a listed id -----------


def test_expand_never_drops_or_reorders_an_explicitly_listed_id():
    rng = random.Random(20260804)
    for _ in range(200):
        ids = sorted({rng.randint(0, 10_000) for _ in range(rng.randint(1, 20))})
        spec = ",".join(str(i) for i in ids)
        assert expand(spec) == ids


def test_expand_never_drops_an_id_covered_by_a_range():
    rng = random.Random(3141592)
    for _ in range(200):
        lo = rng.randint(0, 5_000)
        hi = lo + rng.randint(0, 50)
        sep = rng.choice(["-", ".."])
        spec = f"{lo}{sep}{hi}"
        assert expand(spec) == list(range(lo, hi + 1))


# --- shared error hierarchy ----------------------------------------------------


def test_stream_spec_error_is_also_an_xtract_error():
    from aximprint.acquire.errors import XtractError

    assert issubclass(StreamSpecError, XtractError)
    assert issubclass(StreamSpecError, ValueError)
