"""streamspec — the `stream_ids` grammar (`docs/AXAPI_CONTRACT.md` §6.2).

`validate()` and `expand()` are deliberately separate functions, not two
modes of one function:

  * `validate(spec)` only checks the spec is well-formed. It never produces
    integers and is cheap enough to call on every CLI invocation before a
    spec is sent anywhere.
  * `expand(spec)` produces the individual stream IDs, sorted and
    deduplicated, for **local cataloguing only** (gap detection,
    cross-checking against `fs/files`). It never round-trips back onto the
    wire — the extract path always sends the caller's original string
    verbatim (`LES-036`). Re-spelling a range in a different grammar than the
    one the caller used is exactly how an off-by-one stream selection gets
    introduced against someone else's parser.

Scope, deliberately narrow, same discipline as `timeutil.py`/`windows.py`:
  * No I/O.
  * stdlib only, except `aximprint.acquire.errors` for the shared `XtractError` root
    every module's own error type reparents under.
"""

from __future__ import annotations

import re

from aximprint.acquire.errors import XtractError

__all__ = [
    "DEFAULT_EXPAND_LIMIT",
    "STREAM_ID_MAX",
    "STREAM_ID_MIN",
    "InvalidStreamSpecError",
    "StreamSpecError",
    "TooManyStreamIdsError",
    "expand",
    "validate",
]

# "n and m are integer values in the range 0-4294967296" (ICG worked-example
# comment, docs/AXAPI_CONTRACT.md §6.2). Read as an inclusive bound at both
# ends — the doc gives no reason to think either end is exclusive.
STREAM_ID_MIN = 0
STREAM_ID_MAX = 4294967296

# The foot-gun guard (work order §2 Phase 1): `expand("0-4294967296")` must
# not attempt to materialize 4294967297 integers. This is a generous ceiling
# for "local cataloguing of a real stream set", not a guess at how many
# streams an appliance actually has.
DEFAULT_EXPAND_LIMIT = 100_000


class StreamSpecError(XtractError, ValueError):
    """Base class for every error this module raises."""


class InvalidStreamSpecError(StreamSpecError):
    """`spec` does not conform to `n[,n][,n-m][,n..m]`, naming the offending element."""


class TooManyStreamIdsError(StreamSpecError):
    """`expand(spec)` would materialize more IDs than `max_ids` allows."""


# One token is either a bare integer or a range `n-m` / `n..m`. Anchored with
# `fullmatch` per token (not per whole spec) so a malformed element can be
# named individually in the error message.
_TOKEN_RE = re.compile(r"(?P<lo>\d+)(?:(?P<sep>-|\.\.)(?P<hi>\d+))?")


def _split_tokens(spec: str) -> list[str]:
    if not isinstance(spec, str):
        raise InvalidStreamSpecError(f"stream spec must be a string, got {type(spec).__name__}: {spec!r}")
    if spec == "":
        raise InvalidStreamSpecError("stream spec must not be empty")
    return spec.split(",")


def _parse_token(token: str) -> tuple[int, int]:
    """Parse one comma-separated element into an inclusive `(lo, hi)` pair
    (`lo == hi` for a bare integer). Raises naming `token` on any mismatch:
    malformed syntax, an id out of `[STREAM_ID_MIN, STREAM_ID_MAX]`, or a
    range where `lo > hi`.
    """
    match = _TOKEN_RE.fullmatch(token)
    if match is None:
        raise InvalidStreamSpecError(f"malformed stream spec element {token!r} in spec")
    lo = int(match.group("lo"))
    hi_str = match.group("hi")
    hi = lo if hi_str is None else int(hi_str)
    for n in (lo, hi):
        if not (STREAM_ID_MIN <= n <= STREAM_ID_MAX):
            raise InvalidStreamSpecError(
                f"stream id {n} in element {token!r} is outside the valid range "
                f"[{STREAM_ID_MIN}, {STREAM_ID_MAX}]"
            )
    if lo > hi:
        raise InvalidStreamSpecError(f"range {token!r} has lo > hi ({lo} > {hi})")
    return lo, hi


def validate(spec: str) -> None:
    """Validate `spec` against `n[,n][,n-m][,n..m]`.

    Raises `InvalidStreamSpecError`, naming the offending element, on any
    malformed input: an empty spec, an empty element (e.g. a stray comma), a
    non-numeric token, an id outside `[0, 4294967296]`, or a range with
    `lo > hi`. Never yields integers — see `expand()` for that.
    """
    for token in _split_tokens(spec):
        if token == "":
            raise InvalidStreamSpecError(f"empty element in stream spec {spec!r}")
        _parse_token(token)


def expand(spec: str, *, max_ids: int = DEFAULT_EXPAND_LIMIT) -> list[int]:
    """Expand `spec` into its individual stream IDs, sorted and deduplicated.

    For **local cataloguing only** — never re-sent to the wire (`LES-036`;
    `xtract` always sends the caller's original spec string verbatim).
    Validates `spec` first (so every `validate()` failure mode also fails
    here with the same message), then raises `TooManyStreamIdsError` if the
    expansion would exceed `max_ids` — computed from the range bounds before
    any materialization, so `expand("0-4294967296")` fails fast rather than
    attempting to build a multi-billion-element list.
    """
    validate(spec)
    tokens = _split_tokens(spec)
    ranges = [_parse_token(token) for token in tokens]

    total = sum(hi - lo + 1 for lo, hi in ranges)
    if total > max_ids:
        raise TooManyStreamIdsError(
            f"stream spec {spec!r} would expand to {total} ids, exceeding max_ids={max_ids}; "
            "narrow the spec, or pass a larger max_ids deliberately if this is really needed "
            "for local cataloguing"
        )

    ids: set[int] = set()
    for lo, hi in ranges:
        ids.update(range(lo, hi + 1))
    return sorted(ids)
