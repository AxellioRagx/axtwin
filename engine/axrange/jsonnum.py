"""ONE rule for a number read out of a JSON artifact and republished (GAP-082
review, round 2, SF2).

JSON parses more than a published number can carry. `1e400` becomes an
infinite float, a 400-digit literal an exact Python int, and Python's own
`json` module accepts `NaN` and `Infinity`. Republished, a non-finite float
serializes as `null`, and an integer past 2**53 - 1 reaches a browser as an
approximation -- a 400-digit `windows` arrives as `Infinity`, and the Console
then draws every host as a ghost.

So a WHOLE number an axRange reader takes from a bundle artifact and publishes
is a JSON number (an int, or an integral float) in `0 .. 2**53 - 1`, and a
FLOAT is finite. Anything else is REFUSED with the field's name -- never
clamped, never parsed out of a string. What the refusal becomes is the
caller's call: a strict reader answers 400 naming the field (`/topology`,
`/cast`); a tolerant one publishes `null` and states the problem (the
summary's `windows`).

Three drifting copies of this rule used to live in `axrange.api.imprints`,
`axrange.api.topology_export` and `axrange.fidelity.reference` -- one took an
int of any size, one any `float()`-able string, and none had an upper bound.
"""
from __future__ import annotations

import math
from typing import Any, Optional

#: The largest integer every JSON consumer reads exactly (JavaScript's
#: `Number.MAX_SAFE_INTEGER`).
MAX_WHOLE = 2**53 - 1


class NumberFieldError(ValueError):
    """`field` does not hold the kind of number it publishes.

    A `ValueError`, so a reader boundary that already maps `ValueError` to its
    own typed refusal keeps covering it; `field` is kept for a caller that
    reports it separately."""

    def __init__(self, field: str, problem: str) -> None:
        super().__init__(f"`{field}` {problem}")
        self.field = field


def _shown(value: Any) -> str:
    """`repr(value)`, cut short: the value this module exists for (a
    400-digit integer) would otherwise be echoed whole into every message."""
    text = repr(value)
    return text if len(text) <= 40 else f"{text[:24]}... ({len(text)} characters)"


def whole_number(value: Any, field: str) -> int:
    """`value` as a whole number in `0 .. MAX_WHOLE`: an `int` (never a
    `bool`) or a finite integral `float`. A string, a fraction, NaN or
    Infinity, a negative, or anything past `MAX_WHOLE` raises
    `NumberFieldError`."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise NumberFieldError(
            field, f"must be a whole number, got {type(value).__name__} {_shown(value)}"
        )
    if isinstance(value, float):
        if not (math.isfinite(value) and value.is_integer()):
            raise NumberFieldError(field, f"must be a whole number, got {_shown(value)}")
        value = int(value)
    if not 0 <= value <= MAX_WHOLE:
        raise NumberFieldError(
            field,
            f"is out of range (0 .. 2**53 - 1, the largest integer a browser "
            f"reads exactly): {_shown(value)}",
        )
    return value


def finite_number(value: Any, field: str) -> float:
    """`value` as a finite `float`: an `int` (never a `bool`) or a `float`.
    NaN, Infinity, an integer too large for any float, or a non-number (a
    numeric STRING included) raises `NumberFieldError`."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise NumberFieldError(
            field, f"must be a number, got {type(value).__name__} {_shown(value)}"
        )
    try:
        number = float(value)
    except OverflowError:
        raise NumberFieldError(field, f"is out of range for a number: {_shown(value)}") from None
    if not math.isfinite(number):
        raise NumberFieldError(field, f"must be a finite number, got {_shown(value)}")
    return number


def optional_whole_number(
    value: Any, field: str, *, default: Optional[int] = None,
) -> Optional[int]:
    """`whole_number`, with `None` (an absent or null key) read as `default`."""
    return default if value is None else whole_number(value, field)


def optional_finite_number(value: Any, field: str) -> Optional[float]:
    """`finite_number`, with `None` passed through."""
    return None if value is None else finite_number(value, field)


def is_finite_number(value: Any) -> bool:
    """The predicate form of `finite_number`, for a TOLERANT reader that
    counts what it ignores rather than refusing it."""
    try:
        finite_number(value, "value")
    except NumberFieldError:
        return False
    return True
