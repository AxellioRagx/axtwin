"""`axrange.jsonnum` -- the ONE bounded rule for a number an axRange reader
takes out of a JSON artifact and republishes (GAP-082 review round 2, SF2).

The readers that use it are tested at their own boundaries (the summary's
tolerant `windows` in `test_imprints_reader.py`, the strict `/topology` and
`/cast` in `test_api_console.py`); this file pins the rule itself."""
from __future__ import annotations

import json
import math

import pytest

from axrange.jsonnum import (
    MAX_WHOLE,
    NumberFieldError,
    finite_number,
    is_finite_number,
    optional_finite_number,
    optional_whole_number,
    whole_number,
)


def test_the_bound_is_the_largest_integer_a_browser_reads_exactly():
    assert MAX_WHOLE == 2**53 - 1 == 9007199254740991
    # Past it a JavaScript Number can no longer tell neighbours apart -- the
    # reason the bound sits here and not at "fits in 64 bits".
    assert float(MAX_WHOLE + 1) == float(MAX_WHOLE + 2)
    assert float(MAX_WHOLE) != float(MAX_WHOLE - 1)


@pytest.mark.parametrize(("value", "expected"), [
    (0, 0), (7, 7), (3.0, 3), (MAX_WHOLE, MAX_WHOLE), (float(2**52), 2**52),
], ids=["zero", "int", "integral-float", "max", "large-integral-float"])
def test_a_whole_number_comes_back_as_an_int(value, expected):
    got = whole_number(value, "windows")
    assert (got, type(got)) == (expected, int)


@pytest.mark.parametrize(("value", "problem"), [
    (MAX_WHOLE + 1, "is out of range"),
    (int("9" * 400), "is out of range"),
    (1e20, "is out of range"),
    (-1, "is out of range"),
    (3.5, "must be a whole number"),
    (math.inf, "must be a whole number"),
    (math.nan, "must be a whole number"),
    (True, "must be a whole number"),
    ("3", "must be a whole number"),
    ([3], "must be a whole number"),
    (None, "must be a whole number"),
], ids=["max-plus-one", "400-digits", "large-float", "negative", "fraction", "infinity",
        "nan", "bool", "numeric-string", "list", "null"])
def test_anything_else_is_refused_naming_the_field_and_never_clamped(value, problem):
    with pytest.raises(NumberFieldError) as e:
        whole_number(value, "nodes[3].windows_seen")
    assert e.value.field == "nodes[3].windows_seen"
    assert str(e.value).startswith(f"`nodes[3].windows_seen` {problem}"), str(e.value)


def test_a_refusal_is_a_value_error_so_existing_reader_boundaries_keep_covering_it():
    # `topology_export.load_topology_export` turns a ValueError into its own
    # typed error -> 400; a new exception class would have escaped as a 500.
    assert issubclass(NumberFieldError, ValueError)


def test_a_huge_value_is_not_echoed_whole_into_the_message():
    with pytest.raises(NumberFieldError) as e:
        whole_number(int("9" * 400), "windows")
    assert len(str(e.value)) < 200 and "(400 characters)" in str(e.value), str(e.value)


@pytest.mark.parametrize("literal", ["1e400", "NaN", "Infinity", "-Infinity"])
def test_what_json_parses_but_cannot_republish_is_refused(literal):
    """Python's `json` accepts all four; published, each would be `null` (or
    the wrong number) on the wire."""
    value = json.loads(literal)
    with pytest.raises(NumberFieldError, match="must be a finite number"):
        finite_number(value, "as_of")
    with pytest.raises(NumberFieldError, match="must be a whole number"):
        whole_number(value, "windows")


@pytest.mark.parametrize(("value", "expected"), [
    (0, 0.0), (0.5, 0.5), (-2.5, -2.5), (10**300, 1e300),
], ids=["zero", "fraction", "negative", "large-int"])
def test_a_finite_number_comes_back_as_a_float(value, expected):
    got = finite_number(value, "role_confidence")
    assert (got, type(got)) == (expected, float)


@pytest.mark.parametrize(("value", "problem"), [
    (int("9" * 400), "is out of range for a number"),
    (True, "must be a number"),
    ("0.5", "must be a number"),
    (None, "must be a number"),
    ({"v": 1}, "must be a number"),
], ids=["400-digits", "bool", "numeric-string", "null", "object"])
def test_a_finite_number_refuses_what_is_not_one(value, problem):
    with pytest.raises(NumberFieldError) as e:
        finite_number(value, "as_of")
    assert str(e.value).startswith(f"`as_of` {problem}"), str(e.value)


def test_the_optional_forms_read_null_as_absent_and_nothing_else():
    assert optional_whole_number(None, "windows") is None
    assert optional_whole_number(None, "bytes", default=0) == 0
    assert optional_finite_number(None, "as_of") is None
    for not_absent in ("", 0.5, False):
        with pytest.raises(NumberFieldError):
            optional_whole_number(not_absent, "windows")


@pytest.mark.parametrize(("value", "expected"), [
    (0.5, True), (1, True), (0, True), (math.nan, False), (math.inf, False),
    (int("9" * 400), False), (True, False), ("0.5", False), (None, False), ([0.5], False),
])
def test_the_predicate_agrees_with_the_refusal(value, expected):
    assert is_finite_number(value) is expected
