"""timespec — the closed grammar `harvest` uses to resolve `--start`/`--end`
into exact `Instant`s (`docs/PLAN_metadata_harvester.md` §1).

`harvest` can be told *"start 30 days back from now, end 2 days from the
start"* and this module turns that into two `Instant`s with the same
nanosecond discipline `timeutil` keeps everywhere else. The grammar is
deliberately small and closed — anything outside it is a typed error naming
what *is* accepted, never a guess or a partial match:

  * an absolute epoch (`timeutil.parse_instant`)
  * `now`
  * `<duration> back from now` / `<duration> ago`
  * `<duration> from the start` / `<duration> after the start` — valid for
    `--end` only; using it for `--start` is a typed error naming the
    circularity (the start being resolved can't reference itself), never a
    silent fallback.

No calendar semantics: a "day" is exactly 86400 seconds — no months, no DST,
no timezones. That is `timeutil.parse_duration`'s unit table, reused here,
not reimplemented.

Scope, deliberately narrow, same discipline as `timeutil.py`/`windows.py`:
  * No I/O, no clock reads. `now` is a required keyword argument on every
    function that needs it, never defaulted to `time.time()` inside this
    module — the caller (`harvest`'s CLI) resolves the clock exactly once at
    startup, so a single run can't resolve `now` twice and get two answers,
    and every test here is deterministic without monkeypatching time.
  * stdlib + `aximprint.acquire.timeutil`/`aximprint.acquire.errors` only — no float, no true
    division; see `test_no_float_anywhere` in `tests/acquire/test_timespec.py`
    (`LES-045`'s pattern).
"""

from __future__ import annotations

import re

from aximprint.acquire.errors import XtractError
from aximprint.acquire.timeutil import (
    MAX_PLAUSIBLE_NS,
    MIN_PLAUSIBLE_NS,
    Duration,
    DurationParseError,
    ImplausibleInstantError,
    Instant,
    InstantParseError,
    parse_duration,
    parse_instant,
)

__all__ = [
    "DurationSpecError",
    "InvalidExpressionError",
    "StartCircularityError",
    "TimeSpecError",
    "parse_human_duration",
    "resolve_end",
    "resolve_start",
]


class TimeSpecError(XtractError, ValueError):
    """Base class for every error this module raises."""


class DurationSpecError(TimeSpecError):
    """`parse_human_duration` could not parse its input; the message names
    the input and lists the accepted compact and spelled-out forms.
    """


class InvalidExpressionError(TimeSpecError):
    """A `--start`/`--end` expression did not match the closed grammar; the
    message names the input and lists the accepted forms for that field.
    """


class StartCircularityError(InvalidExpressionError):
    """`<duration> from the start` / `<duration> after the start` was used
    for `--start`. That phrasing means "relative to the start", but the
    start is exactly what is being resolved — it can't reference itself.
    Valid for `--end` only.
    """


# --- Phase 1: parse_human_duration -------------------------------------------

# Spelled-out unit word -> the compact suffix `timeutil.parse_duration`
# already understands. Only day/hour/minute/second are spelled out (the work
# order's own examples); sub-second units have no ambiguity-prone spelled
# form worth adding, so `"500ms"` stays the only way to say it.
_HUMAN_UNIT_TO_SUFFIX = {
    "day": "d",
    "hour": "h",
    "minute": "m",
    "second": "s",
}

# `<int> <unit>[s]`, case-insensitive, singular or plural. A decimal value
# (`"1.5 days"`) never matches this (`\d+` stops at the `.`) and falls
# through to the generic rejection below, alongside negative values (a
# leading `-` is not `\d+`) and mixed forms (`"1 day 2 hours"` has trailing
# text the `fullmatch` anchors reject).
_SPELLED_DURATION_RE = re.compile(
    r"^(?P<value>\d+)\s+(?P<unit>day|hour|minute|second)s?$", re.IGNORECASE
)


def _normalize_whitespace(s: str) -> str:
    """Collapse whitespace runs and strip — shared by `parse_human_duration`
    and the expression grammar below."""
    return " ".join(s.split())


def parse_human_duration(s: str) -> Duration:
    """Parse a duration string, compact or spelled-out, into nanoseconds.

    Accepts everything `timeutil.parse_duration` does (`"30s"`, `"500ms"`,
    a bare integer as seconds) **and** a spelled-out form: `"30 days"`,
    `"1 day"`, `"2 hours"`, `"90 minutes"`, `"45 seconds"` — singular or
    plural, case-insensitive. Delegates the actual unit arithmetic and
    zero/negative checks to `timeutil.parse_duration` rather than
    reimplementing them; only the spelled-out-to-compact translation lives
    here.

    Rejects (with `DurationSpecError`, naming the input and listing the
    accepted forms): a bare float (`"1.5 days"` — ambiguous at nanosecond
    precision; express it unambiguously instead, e.g. `"36 hours"`), zero,
    negative values, and mixed forms (`"1 day 2 hours"`).
    """
    if not isinstance(s, str):
        raise DurationSpecError(f"duration must be a string, got {type(s).__name__}: {s!r}")
    normalized = _normalize_whitespace(s)

    if " " not in normalized:
        # No whitespace: this can only be the compact form. Let
        # timeutil.parse_duration be the single source of truth for what's
        # valid, and translate its error into this module's own type.
        try:
            return parse_duration(normalized)
        except DurationParseError as exc:
            raise DurationSpecError(f"not a valid duration: {s!r} ({exc})") from exc

    match = _SPELLED_DURATION_RE.fullmatch(normalized)
    if match is None:
        raise DurationSpecError(
            f"not a valid duration: {s!r} — accepted forms: compact (e.g. '30d', '500ms', "
            "a bare integer of seconds) or spelled-out day/hour/minute/second, singular or "
            "plural, case-insensitive (e.g. '30 days', '1 day', '2 hours'); no decimals, "
            "no zero, no negative values, no mixed units"
        )
    value_str = match.group("value")
    unit_suffix = _HUMAN_UNIT_TO_SUFFIX[match.group("unit").lower()]
    try:
        return parse_duration(f"{value_str}{unit_suffix}")
    except DurationParseError as exc:
        raise DurationSpecError(f"not a valid duration: {s!r} ({exc})") from exc


# --- Phase 2: the expression grammar -----------------------------------------

_NOW_RE = re.compile(r"^now$", re.IGNORECASE)
_AGO_RE = re.compile(r"^(?P<dur>.+?)\s+(?:back\s+from\s+now|ago)$", re.IGNORECASE)
# "the" is optional: "2 days from start" and "2 days from the start" both match.
_FROM_START_RE = re.compile(
    r"^(?P<dur>.+?)\s+(?:from|after)\s+(?:the\s+)?start$", re.IGNORECASE
)

_START_ACCEPTED_FORMS = (
    "an absolute epoch ('1785934195' or '1785934195.5'), 'now', "
    "'<duration> back from now', '<duration> ago'"
)
_END_ACCEPTED_FORMS = (
    _START_ACCEPTED_FORMS + ", '<duration> from the start', '<duration> after the start'"
)


def _require_instant(value: object, name: str) -> None:
    if not isinstance(value, Instant):
        raise TypeError(f"{name} must be an Instant, got {type(value).__name__}: {value!r}")


def _require_plausible(instant: Instant, expr: str, field_name: str) -> Instant:
    """Range-check an `Instant` this module itself computed (never one `parse_instant` already
    validated on the way in) before handing it back to a caller who may format it straight onto
    the wire. `parse_instant` already rejects a negative (or otherwise out-of-range) literal, so
    `resolve_start`/`resolve_end`'s own `now - duration` / `start + duration` arithmetic is the
    *only* way this module can still produce one — e.g. `resolve_start("30000 days ago", now=...)`
    walks straight past epoch zero into a negative `ns`, which `format_instant` would then render
    as a negative string a request could actually send. Reuses `timeutil`'s existing
    `MIN_PLAUSIBLE_NS`/`MAX_PLAUSIBLE_NS`/`ImplausibleInstantError` trip-wire rather than declaring
    a second one — see the comment on those constants in `timeutil.py`.
    """
    if instant.ns < MIN_PLAUSIBLE_NS or instant.ns > MAX_PLAUSIBLE_NS:
        raise ImplausibleInstantError(
            f"{field_name} expression {expr!r} resolves to {instant.ns} ns, outside the "
            f"plausible {MIN_PLAUSIBLE_NS}..{MAX_PLAUSIBLE_NS} range — the grammar rejects an "
            "out-of-range literal on parse, so this can only be the result of duration "
            "arithmetic (`now - duration` / `start + duration`) landing outside a sane range"
        )
    return instant


def _resolve_expression(
    expr: str,
    *,
    now: Instant,
    start: Instant | None,
    allow_from_start: bool,
    field_name: str,
    accepted_forms: str,
) -> Instant:
    if not isinstance(expr, str):
        raise InvalidExpressionError(
            f"{field_name} expression must be a string, got {type(expr).__name__}: {expr!r}"
        )
    normalized = _normalize_whitespace(expr)
    if normalized == "":
        raise InvalidExpressionError(f"{field_name} expression must not be empty")

    if _NOW_RE.fullmatch(normalized):
        return _require_plausible(now, expr, field_name)

    match = _AGO_RE.fullmatch(normalized)
    if match is not None:
        return _require_plausible(now - parse_human_duration(match.group("dur")), expr, field_name)

    match = _FROM_START_RE.fullmatch(normalized)
    if match is not None:
        if not allow_from_start:
            raise StartCircularityError(
                f"{expr!r} uses '<duration> from/after the start' for {field_name}, but "
                "the start is exactly what is being resolved — it can't be defined relative "
                f"to itself. That phrasing is valid for --end only; accepted forms for "
                f"{field_name}: {accepted_forms}"
            )
        # `start` is guaranteed non-None here: the only caller that passes
        # `allow_from_start=True` (`resolve_end`) always supplies a real
        # `Instant`, validated by `_require_instant` before this is reached.
        return _require_plausible(start + parse_human_duration(match.group("dur")), expr, field_name)

    try:
        return _require_plausible(parse_instant(normalized), expr, field_name)
    except InstantParseError:
        pass

    raise InvalidExpressionError(
        f"could not parse {field_name} expression {expr!r}; accepted forms for "
        f"{field_name}: {accepted_forms}"
    )


def resolve_start(expr: str, *, now: Instant) -> Instant:
    """Resolve a `--start` expression against the closed grammar.

    Accepts an absolute epoch, `"now"`, and `"<duration> back from now"` /
    `"<duration> ago"`. `"<duration> from/after the start"` is rejected with
    `StartCircularityError` naming the circularity — that phrasing is valid
    for `--end` only. Anything else raises `InvalidExpressionError` listing
    the accepted forms.
    """
    _require_instant(now, "now")
    return _resolve_expression(
        expr,
        now=now,
        start=None,
        allow_from_start=False,
        field_name="--start",
        accepted_forms=_START_ACCEPTED_FORMS,
    )


def resolve_end(expr: str | None, *, start: Instant, now: Instant) -> Instant | None:
    """Resolve a `--end` expression against the closed grammar.

    `expr is None` returns `None`, meaning "latest stored data" — `harvest`
    resolves that from the catalog, not here. Otherwise accepts everything
    `resolve_start` does, plus `"<duration> from the start"` /
    `"<duration> after the start"` (`start + duration`). Anything outside
    the grammar raises `InvalidExpressionError` listing the accepted forms.
    """
    if expr is None:
        return None
    _require_instant(now, "now")
    _require_instant(start, "start")
    return _resolve_expression(
        expr,
        now=now,
        start=start,
        allow_from_start=True,
        field_name="--end",
        accepted_forms=_END_ACCEPTED_FORMS,
    )
