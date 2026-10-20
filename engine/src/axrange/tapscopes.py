"""Tap-scope vocabulary (`docs/design/06-taps-sensors.md`) -- a LEAF
module with NO engine dependencies (never imports `axrange.planner`/
`axrange.validator`), following the `axrange/sensors/` vocabulary-registry
precedent (fix round S5, `docs/WORK_ORDER_tap_scopes.md`; LES-097's "one
exported table" rule).

Originally shared by two consumers needing the EXACT same three things --
the closed scope set, the derived-direction rule, and the retired-scope
replacement wording -- from two different layers that must not import each
other: `axrange.planner.tappoints` (the engine's semantic tap-point model,
`axrange.o2control.taps` re-exports from it) and `axrange.twinconfig.expand`
(a content-authoring layer one level BELOW the engine, LES-025). The SECOND
consumer is gone as of `docs/WORK_ORDER_tap_eligibility.md` D3 (Authority:
user, 2026-07-28) -- `expand.py`'s own `tap_points[]`/`_apply_tap_points`
materialization (the only place it read this vocabulary) is retired
outright, so it no longer imports this module at all. Kept as its own leaf
module regardless (per that work order's own explicit constraint) rather
than folded back into `tappoints.py`: a future declarative tap-point source
could reintroduce a second consumer, and an earlier revision of this fix
round duplicating the three names/messages independently in two modules is
exactly how they drifted (the `host`-scope replacement text read
differently in each copy) -- worth keeping structurally impossible.
"""
from __future__ import annotations

# The four scopes brief §4 defines. `direction` is NOT a fifth scope/enum
# here -- see `direction_for_scope` below: it is derived from scope, never
# authored, and belongs to the BINDING a caller composes, not to a tap
# point/interface itself.
TAP_SCOPES = ("port", "segment", "perimeter", "boundary")


def direction_for_scope(scope: str) -> str:
    """The mirror direction a binding on `scope` should carry (brief §3/§4):
    `"rx"` for a MULTI-PORT scope (`segment`/`boundary` -- mirroring both
    directions on every port of a shared set double/N-counts every frame,
    so only the entry side, rx, is kept), `"both"` for a SINGLE-PORT-PER-
    TARGET scope (`port` -- the one interface named; `perimeter` -- distinct
    router ports, each the sole entry/exit for its own net, so both
    directions are safe and desired). Raises `ValueError` for anything not
    in `TAP_SCOPES` -- every caller already has a validated scope string in
    hand, so an unrecognized value here is a programming error, not user
    input to degrade gracefully for."""
    if scope in ("segment", "boundary"):
        return "rx"
    if scope in ("port", "perimeter"):
        return "both"
    raise ValueError(f"unknown tap-point scope: {scope!r} (must be one of {TAP_SCOPES})")


# The two RETIRED scope spellings (`docs/design/02-twin-model.md`'s
# original four), each with an actionable replacement. The wording is
# deliberately generic across BOTH consumers' addressing vocabularies --
# `axrange.o2control.taps`'s tap-plan bindings support `tap:`/`tap_point:`/
# `selector:`, while `axrange.twinconfig.expand`'s `tap_points:` entries
# have no `selector:` concept at all -- so the message names "N separate
# 'port' targets," never "or a selector," to stay correct in both places.
_RETIRED_SCOPE_REPLACEMENTS = {
    "leg": "renamed to 'port' (same {host, net} target shape)",
    "host": "dropped -- express it as N separate 'port' targets (one per leg)",
}


def scope_error_detail(scope: str) -> str:
    """The actionable detail for a bad `scope` value: a retired spelling
    (`leg`/`host`) gets its specific replacement guidance; anything else
    gets the generic "must be one of TAP_SCOPES" a genuine typo deserves.
    Callers prefix this with their own `"... {scope!r}: "` context."""
    retired = _RETIRED_SCOPE_REPLACEMENTS.get(scope)
    if retired is not None:
        return f"scope {scope!r} was {retired}"
    return f"unknown scope {scope!r} (must be one of {TAP_SCOPES})"
