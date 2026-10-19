"""windows — the pure window planner.

Turns `(start, end, interval)` into the exact list of `[t, t+interval)`
extraction windows a caller should request from `GET /axapi/pcap` (see
`docs/AXAPI_CONTRACT.md` §6.1). This module is the sole place window
boundaries are computed, which is what makes it exhaustively testable:

  * No I/O. No network, no filesystem, no clock reads. `plan()` is
    deterministic given its arguments.
  * stdlib + `aximprint.acquire.timeutil`/`aximprint.acquire.errors` only — no transport
    dependency, no float. All arithmetic is `Instant`/`Duration` integer
    nanoseconds; see `timeutil.test_no_float_anywhere` for why that matters.
  * No resume logic, no manifest, no filename construction — that is WO-6's
    output contract, built on top of this module's `Window.wire_params()`.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

from aximprint.acquire.errors import XtractError
from aximprint.acquire.timeutil import Duration, Instant, format_instant

__all__ = [
    "DEFAULT_MAX_WINDOWS",
    "InvalidIntervalError",
    "InvalidMaxWindowsError",
    "InvalidRangeError",
    "TooManyWindowsError",
    "Window",
    "WindowsError",
    "max_windows_for",
    "plan",
]


class WindowsError(XtractError, ValueError):
    """Base class for every error this module raises."""


class InvalidRangeError(WindowsError):
    """`end` is before `start`."""


class InvalidIntervalError(WindowsError):
    """`interval` is not a positive integer number of nanoseconds."""


class TooManyWindowsError(WindowsError):
    """`plan()` would materialize more windows than `max_windows` allows.

    Raised instead of silently generating (or truncating) an enormous plan.
    `--start <beginning of storage> --interval 1s` is millions of requests
    against a live capture appliance and is a typo, not an intent.
    """


class InvalidMaxWindowsError(WindowsError):
    """`max_windows` is not a positive `int`.

    The ceiling check's "here's an interval that would stay under the limit"
    suggestion divides by `max_windows`; a `0` (or negative, or non-`int`)
    value must be rejected with an actionable message here rather than
    reaching that division and raising a bare `ZeroDivisionError`.
    """


# Default ceiling on the number of windows `plan()` will materialize.
# Parameterized (see `plan(..., max_windows=...)`) so a caller — WO-6's CLI —
# can raise it deliberately (e.g. behind a `--yes` flag) instead of this
# module silently doing something expensive.
DEFAULT_MAX_WINDOWS = 10_000


def max_windows_for(yes: bool) -> int:
    """The `max_windows` ceiling a caller should pass to `plan()` (or apply to its own,
    non-`plan()`-mediated count of "windows" — `harvest --from-pcaps`'s directory listing):
    `DEFAULT_MAX_WINDOWS` ordinarily, or effectively unbounded (`sys.maxsize`) once an operator
    has explicitly opted in via `--yes`.

    Three call sites (`cli.py`'s `cmd_pcap`, `pipeline.py`'s `plan_harvest` and
    `plan_from_pcaps`) used to each spell `sys.maxsize if yes else DEFAULT_MAX_WINDOWS` by hand —
    one formula, in one place, so a future change to the "opted in" value is made once.
    """
    return sys.maxsize if yes else DEFAULT_MAX_WINDOWS


@dataclass(frozen=True)
class Window:
    """One half-open extraction window `[start, end)`.

    `index` (1-based) and `total` let a caller report progress ("window
    3/10") without recomputing the plan's length.
    """

    start: Instant
    end: Instant
    index: int
    total: int

    def wire_params(self) -> dict[str, str]:
        """The `start_time`/`end_time` strings to send to `GET /axapi/pcap`.

        Formatting delegates entirely to `timeutil.format_instant` —
        precision is preserved, never normalized. `start_time` is exactly
        `format_instant(self.start)`: this string doubles as the resume key
        and the output filename downstream (WO-6), so it must not be
        transformed here.
        """
        return {
            "start_time": format_instant(self.start),
            "end_time": format_instant(_wire_end(self.end)),
        }


# --- Q22: the end_time boundary — RESOLVED 2026-08-05 by live probe ----------
#
# `GET /axapi/pcap`'s `end_time` is **inclusive**, and an `end_time` whose
# fractional part is zero is treated as covering that entire second. Measured
# against CorpAPCN (PacketXpress 5.1.0), requesting `[…195, …225)`:
#
#   end_time sent          last packet returned    span
#   1785934225             1785934225.986821       30.98s   ← whole second
#   1785934225.000000000   1785934225.986821       30.98s   ← zero fraction, same
#   1785934225.500000000   1785934225.487038        _.__    ← non-zero fraction honored
#   1785934224.999999999   1785934224.993575       29.99s   ← clean half-open window
#
# So a zero fraction does NOT bound the range; only a non-zero one does.
# Sending `end` unadjusted made adjacent windows overlap by a full second of
# traffic (verified: window [t,t+30) returned packets at t+30.02..t+30.98,
# which the next window returned again). Subtracting 1ns fixes it and, as a
# side effect, guarantees the non-zero fraction the appliance requires.
#
# No other file may compensate for boundary semantics; every wire encoding of
# a window's end flows through `_wire_end`.
Q22_END_TIME_INCLUSIVE = True  # Q22: RESOLVED — see docs/OPEN_QUESTIONS.md


def _wire_end(end: Instant) -> Instant:
    """Apply the Q22 encoding to a window's `end` before it goes on the wire."""
    if Q22_END_TIME_INCLUSIVE:
        return end - Duration(1)
    return end


def plan(
    start: Instant,
    end: Instant,
    interval: Duration,
    *,
    max_windows: int = DEFAULT_MAX_WINDOWS,
) -> list[Window]:
    """Tile `[start, end)` into half-open `[t, t+interval)` windows.

    * `start == end` yields zero windows, not one empty one.
    * `end < start` raises `InvalidRangeError` naming both values.
    * `interval` must be a positive integer number of nanoseconds
      (`InvalidIntervalError` otherwise) — a non-positive or non-`int`
      interval (a `float`, or a `bool`, which is a `bool` subclass of `int`)
      cannot tile a range.
    * `interval > (end - start)` yields exactly one window, clipped to `end`.
    * The final window is always clipped to `end` — never dropped, never
      extended past it.
    * Raises `TooManyWindowsError` if the plan would exceed `max_windows`
      windows; raise it deliberately (WO-6's `--yes`) to override.

    Every boundary here is exact integer nanosecond arithmetic — no
    `float`, so no drift, however many windows are produced.
    """
    if not isinstance(max_windows, int) or isinstance(max_windows, bool) or max_windows < 1:
        raise InvalidMaxWindowsError(
            f"max_windows must be a positive int, got {type(max_windows).__name__}: {max_windows!r}"
        )
    if end < start:
        raise InvalidRangeError(
            f"end ({format_instant(end)}) is before start ({format_instant(start)})"
        )
    if not isinstance(interval, int) or isinstance(interval, bool):
        raise InvalidIntervalError(
            f"interval must be an int number of nanoseconds, got {type(interval).__name__}: "
            f"{interval!r}"
        )
    if interval <= 0:
        raise InvalidIntervalError(f"interval must be positive, got {interval} ns")
    if start == end:
        return []

    span = end - start
    window_count, remainder = divmod(span, interval)
    total = window_count + (1 if remainder else 0)

    if total > max_windows:
        # Ceiling division: the smallest interval that keeps `total` at or
        # under `max_windows`.
        min_interval = -(-span // max_windows)
        raise TooManyWindowsError(
            f"interval={interval}ns over a {span}ns range would produce {total} windows, "
            f"exceeding max_windows={max_windows}; use an interval of at least "
            f"{min_interval}ns to stay at or under the ceiling, or raise max_windows "
            f"deliberately"
        )

    windows: list[Window] = []
    t = start
    for i in range(1, total + 1):
        window_end = min(t + interval, end)
        windows.append(Window(start=t, end=window_end, index=i, total=total))
        t = window_end
    return windows
