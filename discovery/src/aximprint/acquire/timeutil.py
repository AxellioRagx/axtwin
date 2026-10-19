"""timeutil — the only place `xtract` converts between a wire/human timestamp
and its internal representation.

Every `axapi` time field is typed `string` on the wire (confirmed on the
`POST /axapi/extract` payload). Internally an instant is **nanoseconds since
the unix epoch, as an `int`** — never a `float`. A `float` silently rounds
`1728484411.293105240` to microseconds, and every window boundary computed
from it downstream inherits that error. This module exists so that mistake
has exactly one place it could be made, and it is not made here: see
`test_no_float_anywhere` in `tests/acquire/test_timeutil.py`.

Scope, deliberately narrow:
  * No I/O. No network, no filesystem, no clock reads.
  * No ISO-8601 or other human-calendar parsing. Epoch in, epoch out.
  * Imports nothing outside the standard library except `aximprint.acquire.errors`, for
    the shared `XtractError` root every module's own error type reparents
    under (itself a zero-dependency, stdlib-only module) — so this module can
    still be tested exhaustively and every later module can depend on it
    without dragging in a transport dependency.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import total_ordering
from typing import NewType

from aximprint.acquire.errors import XtractError

__all__ = [
    "MAX_PLAUSIBLE_NS",
    "MIN_PLAUSIBLE_NS",
    "NS_PER_SECOND",
    "Duration",
    "DurationParseError",
    "ImplausibleInstantError",
    "Instant",
    "InstantParseError",
    "TimeUtilError",
    "format_instant",
    "instant_from_fs_field",
    "parse_duration",
    "parse_instant",
]

# Nanoseconds in one second. The one place that magic number lives; everything
# else in this module multiplies or divmods by this name, never a bare literal
# — and every other module that needs it (`cli.py`'s display formatting, for
# one) imports this name too, rather than redeclaring its own private copy.
NS_PER_SECOND = 1_000_000_000


class TimeUtilError(XtractError, ValueError):
    """Base class for every error this module raises."""


class InstantParseError(TimeUtilError):
    """A wire/human timestamp string or field could not be parsed as an Instant."""


class DurationParseError(TimeUtilError):
    """A duration string could not be parsed as a Duration."""


class ImplausibleInstantError(TimeUtilError):
    """An `instant_from_fs_field` conversion landed outside the plausible range.

    This is the guard against a unit mix-up (seconds vs. milliseconds vs.
    nanoseconds): the wrong unit is six-plus orders of magnitude off, and this
    fails loudly on the first record rather than returning a plausible-looking
    wrong window.
    """


@total_ordering
@dataclass(frozen=True, eq=False)
class Instant:
    """A single point in time: `ns` nanoseconds since the unix epoch.

    `fractional` remembers whether the *source* explicitly carried a fractional
    part (a wire string with a decimal point) so that `format_instant` can
    re-emit the caller's precision instead of normalizing it — an integer
    second in must produce an integer second out, and `"...0.000000000"` must
    not collapse to a bare integer. `format_instant` never loses precision
    regardless of this flag: it inspects the actual remainder too.

    Equality and ordering compare `ns` only — `fractional` is a formatting
    hint, not part of an instant's identity, so two instants built from the
    same nanosecond value (one from an integer-only source, one from an
    explicit `"...000000000"` source) compare equal.
    """

    ns: int
    fractional: bool

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Instant):
            return NotImplemented
        return self.ns == other.ns

    def __lt__(self, other: Instant) -> bool:
        if not isinstance(other, Instant):
            return NotImplemented
        return self.ns < other.ns

    def __hash__(self) -> int:
        return hash(self.ns)

    def __add__(self, other: Duration) -> Instant:
        if not isinstance(other, int) or isinstance(other, bool):
            return NotImplemented
        return Instant(self.ns + other, self._fractional_after(other))

    __radd__ = __add__

    def __sub__(self, other: Instant | Duration) -> Duration | Instant:
        if isinstance(other, Instant):
            return Duration(self.ns - other.ns)
        if isinstance(other, int) and not isinstance(other, bool):
            return Instant(self.ns - other, self._fractional_after(other))
        return NotImplemented

    def _fractional_after(self, duration_ns: int) -> bool:
        """Whether the result of +/- `duration_ns` should be fractional-precision.

        Once an instant is fractional-precision it stays that way; a whole
        instant gains fractional precision the moment a sub-second duration
        touches it, even if the resulting ns value happens to land on a whole
        second (`format_instant` decides display purely from the flag and the
        actual remainder, so this never loses information either way).
        """
        return self.fractional or (duration_ns % NS_PER_SECOND != 0)


Duration = NewType("Duration", int)


_INSTANT_RE = re.compile(r"(\d+)(?:\.(\d{1,9}))?")


def parse_instant(s: str) -> Instant:
    """Parse a wire/human timestamp string into an Instant.

    Accepts `"1728484411"` and `"1728484411.293105240"` (1-9 fractional
    digits, zero-padded to nanoseconds). Rejects empty strings, non-numeric
    input, negative numbers, more than 9 fractional digits, surrounding
    whitespace, exponent notation, and a leading `+` — all of `axapi`'s wire
    format never uses any of those.
    """
    if not isinstance(s, str):
        raise InstantParseError(f"instant must be a string, got {type(s).__name__}: {s!r}")
    match = _INSTANT_RE.fullmatch(s)
    if match is None:
        raise InstantParseError(f"not a valid instant string: {s!r}")
    seconds_str, fraction_str = match.groups()
    seconds = int(seconds_str)
    if fraction_str is None:
        return Instant(seconds * NS_PER_SECOND, False)
    fraction_ns = int(fraction_str.ljust(9, "0"))
    return Instant(seconds * NS_PER_SECOND + fraction_ns, True)


def format_instant(i: Instant) -> str:
    """Format an Instant back into a wire/human timestamp string.

    Precision is preserved, never normalized: an instant with no fractional
    remainder and no recorded fractional source formats as a bare integer;
    anything with an actual sub-second remainder, or a source that explicitly
    carried a (possibly all-zero) fractional part, formats as
    `%d.%09d`. `format_instant(parse_instant(s)) == s` for every `s`
    `parse_instant` accepts whose fractional part is absent or exactly 9
    digits.
    """
    seconds, remainder_ns = divmod(i.ns, NS_PER_SECOND)
    if remainder_ns or i.fractional:
        return f"{seconds}.{remainder_ns:09d}"
    return str(seconds)


_DURATION_UNIT_NS = {
    "ns": 1,
    "us": 1_000,
    "ms": 1_000_000,
    "s": NS_PER_SECOND,
    "m": 60 * NS_PER_SECOND,
    "h": 3600 * NS_PER_SECOND,
    "d": 86400 * NS_PER_SECOND,
}

_DURATION_RE = re.compile(r"(\d+)(ns|us|ms|s|m|h|d)?")


def parse_duration(s: str) -> Duration:
    """Parse a duration string into nanoseconds.

    Accepts a bare integer (seconds) or an integer with a `ns|us|ms|s|m|h|d`
    suffix (`"30s"`, `"5m"`, `"1h"`, `"500ms"`, `"100us"`). Zero, negative, and
    unsuffixed-decimal input (`"1.5"` — ambiguous unit) are all rejected;
    express a sub-second duration with an unambiguous suffix instead
    (`"1500ms"`).
    """
    if not isinstance(s, str):
        raise DurationParseError(f"duration must be a string, got {type(s).__name__}: {s!r}")
    match = _DURATION_RE.fullmatch(s)
    if match is None:
        raise DurationParseError(f"not a valid duration string: {s!r}")
    value_str, unit = match.groups()
    unit = unit or "s"
    value = int(value_str)
    if value == 0:
        raise DurationParseError(f"duration must be nonzero: {s!r}")
    return Duration(value * _DURATION_UNIT_NS[unit])


# --- Q19: the unit boundary — RESOLVED 2026-08-05 by live probe -------------
#
# `GET /axapi/fs/files` returns `time_start`/`time_end` as **strings holding
# whole-second unix epoch values** — the live probe against CorpAPCN
# (PacketXpress 5.1.0) confirmed both the unit (seconds — the assumption this
# constant already carried was correct) and the wire *type* (string, not the
# `integer` both vendor PDFs claimed). Evidence: file record
# `{"time_start": "1781795842", ...}` converts to 2026-06-18, matching that
# same record's own `"date": "Jun 18 15:17"` field. Every later module reaches
# this conversion through `instant_from_fs_field`, never inline, so a future
# unit correction is still a one-constant change here and nowhere else.
FS_FIELD_NS_PER_UNIT = NS_PER_SECOND  # Q19: RESOLVED — see docs/OPEN_QUESTIONS.md

# Roughly 1990-01-01T00:00:00Z .. 2100-01-01T00:00:00Z, as nanoseconds. A unit
# mix-up (seconds vs. milliseconds vs. nanoseconds) is six-plus orders of
# magnitude, which lands nowhere near this window — that is what makes this a
# reliable trip-wire rather than a plausible-looking wrong answer.
#
# Public (not `_`-prefixed) and exported: `aximprint.acquire.timespec.resolve_start`/`resolve_end` reuse this
# same trip-wire to catch a negative (or otherwise implausible) `Instant` that `now - duration` /
# `start + duration` arithmetic can produce, even though `parse_instant` already rejects a
# negative literal on input — arithmetic is the one way this codebase can still manufacture one.
# One trip-wire, one place it's defined; `timespec.py` must never declare a second one.
MIN_PLAUSIBLE_NS = 631152000 * NS_PER_SECOND
MAX_PLAUSIBLE_NS = 4102444800 * NS_PER_SECOND


def instant_from_fs_field(v: str) -> Instant:
    """Convert a raw `time_start`/`time_end` field from `GET /axapi/fs/files`
    into an Instant, using `FS_FIELD_NS_PER_UNIT` (Q19).

    The live probe against CorpAPCN (PacketXpress 5.1.0) confirmed this field
    is a **string** holding a whole-second unix epoch value (e.g.
    `"1781795842"`), not the `integer` both vendor PDFs claimed. Accepts only
    an all-digit string — the same disk-usage-footer junk record that crashed
    an earlier live run also surfaces here (`docs/AXAPI_CONTRACT.md` §5.3;
    e.g. `"time_start": "available,"`), so a non-numeric string must raise a
    typed, catchable error rather than the bare `ValueError` a raw `int(v)`
    would produce. Rejects a non-`str` input the same way.

    Raises `ImplausibleInstantError` if the converted value would fall outside
    roughly 1990-2100 — the loud failure that catches a wrong-unit conversion
    on the first record instead of silently producing a wrong-but-plausible
    range.
    """
    if not isinstance(v, str):
        raise InstantParseError(f"fs field must be a string, got {type(v).__name__}: {v!r}")
    if not v.isdigit():
        raise InstantParseError(f"fs field must be an all-digit string, got {v!r}")
    ns = int(v) * FS_FIELD_NS_PER_UNIT
    if ns < MIN_PLAUSIBLE_NS or ns > MAX_PLAUSIBLE_NS:
        raise ImplausibleInstantError(
            f"fs field {v!r} converts to {ns} ns, which is outside the plausible "
            f"1990-2100 range under FS_FIELD_NS_PER_UNIT={FS_FIELD_NS_PER_UNIT} "
            f"(Q19 unresolved, see docs/OPEN_QUESTIONS.md) — this is the signature "
            f"of a unit mix-up, not a real timestamp"
        )
    return Instant(ns, False)
