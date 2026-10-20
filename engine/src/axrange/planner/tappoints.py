"""Semantic tap-point model + resolution (`docs/design/06-taps-sensors.md`,
`docs/WORK_ORDER_tap_scopes.md` -- supersedes the four-scope set this module
shipped with 2026-07-24, `docs/design/02-twin-model.md`;
`docs/WORK_ORDER_tap_eligibility.md`, Authority: user, 2026-07-28 -- the twin
substrate becomes tap-agnostic, see "Eligibility" below).

A tap point names a set of SOURCE PORTS to SPAN (brief §1/§2) -- the vnet
*is* the switch, a VM NIC *is* a port, the sensor *is* the SPAN destination.
Four scopes, each answering a distinct question (brief §4):

    port      -- one host's leg on one net, both directions ("everything
                 this one interface carries"; renamed from `leg` -- same
                 `{host, net}` target shape, unchanged resolution)
    segment   -- every port on a net, rx only, ROUTERS INCLUDED (brief §3.1:
                 under rx-only, excluding a router's transit leg is a
                 COVERAGE HOLE, not a de-dup -- traffic arriving from any
                 other segment or the internet enters the net AT the
                 router's port, and nothing else on the net mirrors it)
    perimeter -- every ROUTER port attached to a net, both directions
                 ("everything this network exchanges with anything else");
                 also covers the twin's WHOLE EXTERNAL EDGE, since the
                 router's synthetic WAN uplink (`net_id == "__wan__"`,
                 `docs/WORK_ORDER_tap_scopes.md` M2/M3) is a net id like any
                 other -- `perimeter: wan` (`wan-perimeter`, the `wan` alias
                 resolved in `axrange.o2control.taps`, not here) is the same
                 rule applied at the twin's own boundary
    boundary  -- the joining router's port on each of two named (internal)
                 nets, rx each side ("specifically what flows between these
                 two networks")

The old `host` scope (every leg of one host) is DROPPED -- express it as N
`port` bindings or a `select_tap_points` query. **Direction is derived from
scope, never authored** (`direction_for_scope` below) -- it is a property of
the BINDING an `axrange.o2control.taps` resolution composes, not of the
`TapInterfaceRef`/`TapPoint` itself, so `resolve_tap_point`'s signature is
UNCHANGED (`list[TapInterfaceRef]`, no direction slot) -- see that function's
own docstring.

Builds on `axrange.planner.inventory.build_twin_inventory`'s published
`hosts[].nets[]` -- the twin's FULL attachment topology (redeploy-stable
`host:net:nic` interfaces, sourced from `attach-vm-to-vnet`, which fires
unconditionally for every leg regardless of any authoring choice). THIS
module is the semantic grouping LAYER on top of that raw topology.

Lives in the ENGINE (`axrange/planner/`), not `axrange/o2control/`, per
LES-025: the A2 tap playout (`axrange.o2control.taps`) is an engine
*client* -- it consumes this module's resolution the same way it already
consumes `build_twin_inventory`, and never grows its own copy of "what
counts as a tap point."

Two concerns, kept deliberately separate:

  * `derive_default_tap_points` -- an AUTO-DERIVATION heuristic that groups
    the twin's FULL attachment topology into a candidate tap-point catalog
    (one `segment` per net, one `port` per interface, one `perimeter` per
    net with a router port, one `boundary` per pair of nets a router
    joins). This is INTERNAL resolution machinery only, consumed at BIND
    TIME by `tap_point:`/`selector:` addressing -- never published (`docs/
    WORK_ORDER_tap_eligibility.md` D5; `axrange.planner.inventory` publishes
    only raw `hosts[].nets[]`, no derived catalog).
  * `resolve_tap_point` -- scope+target -> concrete interfaces, against a
    `TwinInventory`. Pure and generic: it works identically whether the
    `TapPoint` came from `derive_default_tap_points` below or a future
    author-declared one -- only `scope`/`target`'s SHAPE matters, never its
    provenance.

No adapter/cluster call anywhere in this module -- it is pure data
transformation over an already-built `TwinInventory` (itself just a
`state.db` read, `axrange.planner.inventory`'s own docstring).

**Eligibility = the tap plan's sole authority over the FULL topology**
(`docs/WORK_ORDER_tap_eligibility.md`, Authority: user, 2026-07-28,
superseding every earlier revision of this module). There is no more
`tap:`-flagged SUBSET to gate against -- `nodes[].tap` / `nodes[].nets[].tap`
/ `networks[].tap` are REMOVED from `IR_SCHEMA` entirely, and the op that
used to record a resolved tap-intent subset (`OP-MIR-005 record-tap-intent`)
is REMOVED from the op catalog. `resolve_tap_point`/`derive_default_tap_points`
resolve against EVERY interface `TwinInventory.hosts[].nets[]` carries
(including a router's WAN uplink, `HostNet.external=True`) -- ANY port a
tap-plan binding names is tappable, full stop. `segment` now genuinely means
"every port on the net" (not "every port someone flagged at deploy time"),
and `boundary`/`perimeter` resolve against whichever router node actually
joins the named net(s), whether or not an author ever thought to add `tap:`
to it. This is a POSTURE change, not a regression: a plan that used to
resolve 3 ports may now resolve 40 (`§7`'s capacity warning, `axrange.
o2control.taps`, names the fan-out/duplication cost, never blocks it).

A consequence worth stating plainly: `boundary` is NO LONGER gated on an
author having hand-flagged the joining `conduit`/`core`/`fw`/`gw`/`hq-core`
node's legs -- ANY router that joins two named segments resolves, and
`TapPointResolutionError` (below) is now unreachable in practice (it existed
to signal "this joining host exists but has no TAPPED leg," a state that
can no longer occur once eligibility covers the full topology) -- kept only
because a `boundary` target naming two segments NO HOST joins at all is
still a legitimate `[]`/never-derived case, distinct in kind.

There is likewise no more WAN-adjacent staleness caveat: `__wan__` is
recorded in `TwinInventory.hosts[].nets[]` (`HostNet.external=True`) by the
SAME unconditional `attach-vm-to-vnet` op every other leg is, so
`perimeter: wan` resolves against ANY deployed twin with a router --
including one deployed before this change landed, with no `--fresh`
redeploy required (the earlier LES-096 caveat here applied only to
`record-tap-intent`'s additive WAN entry, which no longer exists to be
stale). `_host_legs`/`segment`/`boundary` still EXCLUDE `external` nets
(brief §5.1: the WAN edge is `perimeter`'s job, not `boundary`'s or
`segment`'s) -- only `perimeter`'s own resolution and `port`'s (a single
interface, WAN or not) ever return a `__wan__` triple.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from axrange.planner.inventory import TwinInventory
# Fix round S5 (`docs/WORK_ORDER_tap_scopes.md`): the scope vocabulary
# (`TAP_SCOPES`/`direction_for_scope`/`scope_error_detail`) now lives in
# `axrange.tapscopes` -- a LEAF module with no engine deps, imported by
# this module AND `axrange.twinconfig.expand` (LES-097's "one exported
# table" rule; the two used to duplicate this vocabulary independently,
# and had already drifted in message text). Re-exported here unchanged so
# every EXISTING `from axrange.planner.tappoints import TAP_SCOPES` (etc.)
# call site keeps working with no import-path change.
from axrange.tapscopes import (  # noqa: F401 -- re-exported, see above
    TAP_SCOPES,
    direction_for_scope,
    scope_error_detail,
)


class TapPointShapeError(ValueError):
    """A `TapPoint`'s `scope`/`target` combination is malformed -- raised at
    CONSTRUCTION time (`TapPoint.__post_init__`), not deferred to a bare
    `ValueError`/`AttributeError` inside `resolve_tap_point`'s `scope`-keyed
    unpacking (SHOULD-FIX, `docs/WORK_ORDER_tap_points.md` code review). A
    subclass of `ValueError` so existing `pytest.raises(ValueError, ...)`
    coverage of the scope check keeps working unchanged."""


class TapPointResolutionError(Exception):
    """Raised by `resolve_tap_point` for a `boundary` point whose joining
    host(s) exist in the topology but have NO tapped leg on either segment
    (M4, `docs/WORK_ORDER_tap_points.md` code review) -- an ACTIONABLE
    failure ("this place exists but nothing is wired to capture it yet"),
    distinct from the ordinary "resolves to nothing" case (still a plain
    empty list for every other scope, and for a `boundary` target that no
    host actually joins at all).

    UNREACHABLE IN PRACTICE as of `docs/WORK_ORDER_tap_eligibility.md` (D1,
    2026-07-28): a joining host's legs are now, by construction, always
    part of the same full-topology set `resolve_tap_point` resolves
    against (there is no more `tap:`-gated subset for them to be excluded
    from) -- so `hosts_joining and not out` can no longer occur. Kept
    (not deleted) as defensive code and because the class remains a valid,
    typed distinction for a hypothetical future author-declared point whose
    target diverges from the auto-derived catalog's own invariants."""


@dataclass(frozen=True)
class TapInterfaceRef:
    """One concrete, resolved capture point -- a single host NIC on a
    single net. Matches the `(host_id, net_id, nic_index)` triple
    `axrange.o2control.taps` already keys its `iface_new_sensors` binding
    map on, so a resolved tap point drops straight into the EXISTING
    binding-resolution path with no reshaping -- this module adds
    addressing vocabulary, not a new capture mechanism (work order §2)."""

    host_id: str
    net_id: str
    nic_index: int

    def as_tuple(self) -> tuple[str, str, int]:
        return (self.host_id, self.net_id, self.nic_index)


@dataclass(frozen=True)
class TapPoint:
    """A named capture PLACE (`docs/design/06-taps-sensors.md`). `target`'s
    shape depends on `scope`, VALIDATED at construction time (`__post_init__`,
    SHOULD-FIX code review -- a mis-shaped target used to crash
    `resolve_tap_point` with a bare `ValueError`/`AttributeError` instead of a
    typed, actionable one; matters most for a HYPOTHETICAL future
    author-declared point this module cannot itself validate any earlier
    than construction):

      * `segment`/`perimeter` -- `target` is a non-empty `str` (a net_id --
        for `perimeter` this may be `"__wan__"`, the twin's external edge,
        `docs/WORK_ORDER_tap_scopes.md` M2/M3).
      * `boundary` -- `target` is a 2-tuple of non-empty net_id `str`s
        `(a, b)`; a `list` of the same shape is accepted and NORMALIZED to
        a tuple (never a segment+WAN pair -- brief §5.1: the WAN edge is
        `perimeter`'s job, not `boundary`'s).
      * `port` -- `target` is a `dict` `{"host": host_id, "net": net_id}`,
        both non-empty `str`s (renamed from `leg`, same shape).

    `labels` is an open, free-form tuple an author may use for SELECTION
    (including `"ew"`/`"ns"` when that genuinely describes the point) -- it
    is never read by `resolve_tap_point` itself, only by `select_tap_points`
    below, and only when a caller's own selector asks for it. **Not
    populated by `derive_default_tap_points`** -- and, as of `docs/
    WORK_ORDER_tap_eligibility.md` D3 (2026-07-28), there is no longer a
    declarative source that ever WOULD populate it: the twin-config
    `tap_points:` block this once deferred to is REMOVED, not merely
    unbuilt. A `labels:` selector always resolves empty against the
    auto-derived catalog; this is deliberate, permanent behavior, not a
    deferral (see `select_tap_points`'s docstring too).

    Identity is by `id` alone (`__eq__`/`__hash__` below) -- NOT the
    default dataclass field-tuple identity, because a `port`/`boundary`
    target (a `dict`/`tuple`) would otherwise be perfectly hashable (a
    tuple is), but hashing "by every field" is the wrong notion of
    identity for a catalog whose ids are the contract (`docs/
    WORK_ORDER_tap_points.md` "ambiguous ids" SHOULD-FIX: two points must
    never coexist with the same id, so an id-keyed hash/eq is the correct
    semantics, not an accident of which fields happen to be hashable)."""

    id: str
    scope: str
    target: Any
    labels: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.scope not in TAP_SCOPES:
            raise TapPointShapeError(
                f"tap point {self.id!r}: {scope_error_detail(self.scope)}"
            )
        if isinstance(self.labels, list):
            object.__setattr__(self, "labels", tuple(self.labels))

        if self.scope in ("segment", "perimeter"):
            if not isinstance(self.target, str) or not self.target:
                raise TapPointShapeError(
                    f"tap point {self.id!r} (scope={self.scope}): target "
                    f"must be a non-empty str, got {self.target!r}"
                )
        elif self.scope == "port":
            ok = (
                isinstance(self.target, dict)
                and isinstance(self.target.get("host"), str) and self.target.get("host")
                and isinstance(self.target.get("net"), str) and self.target.get("net")
            )
            if not ok:
                raise TapPointShapeError(
                    f"tap point {self.id!r} (scope=port): target must be "
                    '{"host": <non-empty str>, "net": <non-empty str>}, '
                    f"got {self.target!r}"
                )
        elif self.scope == "boundary":
            ok = (
                isinstance(self.target, (tuple, list))
                and len(self.target) == 2
                and all(isinstance(x, str) and x for x in self.target)
            )
            if not ok:
                raise TapPointShapeError(
                    f"tap point {self.id!r} (scope=boundary): target must "
                    f"be a 2-tuple of non-empty net_id strs, got "
                    f"{self.target!r}"
                )
            if isinstance(self.target, list):
                object.__setattr__(self, "target", tuple(self.target))

    def __hash__(self) -> int:
        return hash(self.id)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, TapPoint):
            return NotImplemented
        return self.id == other.id


@dataclass(frozen=True)
class TapPointSelector:
    """Matches a `TapPoint` on `id` / `scope` / `target` / `labels` -- every
    field GIVEN (non-`None`/non-empty) must match (AND); a field left unset
    is simply not checked. `target` matches LOOSELY (`_target_matches`
    below): an exact match for a `segment`/`perimeter` point, a 2-sequence
    (order-independent) match for a `boundary` point's 2-tuple -- the
    brief's own §4 boundary-target shape, `target: [seg-a, seg-b]` -- or
    CONTAINMENT for a `boundary` point's 2-tuple (a bare scalar) or a `port`
    point's `{host, net}` dict -- so `target="dmz"` matches both a
    `corp<->dmz` boundary point and any `port` point whose net is `dmz`,
    without the caller needing to know which scope the match will land on
    ahead of time (the brief's "target/segment" phrasing).

    Construction here does NOT validate (`axrange.o2control.taps`'s
    `_selector_from_dict` does -- `scope` against `TAP_SCOPES`, `labels`
    against "must be a list of strings not a bare string" -- since that's
    where the untrusted raw dict enters; a hand-built `TapPointSelector` in
    engine-side code is assumed well-formed, matching `TapPoint`'s own
    split between construction-time shape checks there and conversion-time
    checks in the o2control caller)."""

    id: Optional[str] = None
    scope: Optional[str] = None
    target: Optional[Any] = None
    labels: tuple[str, ...] = ()


def has_criteria(selector: TapPointSelector) -> bool:
    """True iff `selector` carries at least one EFFECTIVE matching
    criterion -- an all-unset selector matches EVERY point (`matches_selector`
    below, vacuously true on every check), which must never be an emergent
    "select everything" default (M1/M2, `docs/WORK_ORDER_tap_points.md`).
    Callers (`axrange.o2control.taps`, both at parse time and at resolve
    time) MUST call this before accepting a selector binding."""
    return (
        selector.id is not None or selector.scope is not None
        or selector.target is not None or bool(selector.labels)
    )


def _target_matches(point_target: Any, wanted: Any) -> bool:
    if isinstance(wanted, (list, tuple)):
        # The brief's own §4 boundary-target shape (`target: [a, b]`) --
        # order-independent 2-sequence match against a `boundary` point's
        # own 2-tuple. A sequence `wanted` against any OTHER scope's target
        # shape (str/dict) is not a match (there's nothing to compare it to).
        if isinstance(point_target, tuple) and len(point_target) == 2:
            return set(wanted) == set(point_target)
        return False
    if isinstance(point_target, tuple):
        return wanted in point_target
    if isinstance(point_target, dict):
        return wanted in point_target.values()
    return point_target == wanted


def matches_selector(point: TapPoint, selector: TapPointSelector) -> bool:
    if selector.id is not None and point.id != selector.id:
        return False
    if selector.scope is not None and point.scope != selector.scope:
        return False
    if selector.target is not None and not _target_matches(point.target, selector.target):
        return False
    if selector.labels and not set(selector.labels) <= set(point.labels):
        return False
    return True


def select_tap_points(
    points: list[TapPoint], selector: TapPointSelector
) -> list[TapPoint]:
    """Every point in `points` the selector matches, in the SAME order
    `points` was given. `[]` is a legitimate (if useless) result -- callers
    decide whether an empty match is an error, this function never raises.
    NOTE: `derive_default_tap_points` never assigns `labels` (no declarative
    source exists to draw one from -- `docs/WORK_ORDER_tap_eligibility.md`
    D3 permanently removed the twin-config `tap_points:` block that once
    would have supplied them) -- a `labels:` selector against the
    auto-derived catalog always resolves empty; that is deliberate,
    permanent behavior (`TapPoint`'s own docstring), not a bug in this
    function. `axrange.o2control.taps`'s own tap-plan grammar also no
    longer ACCEPTS a `selector: {labels: ...}` key (D3) -- this function's
    `labels` matching stays generic/reachable only via a hand-built
    `TapPointSelector` in engine-side code."""
    return [p for p in points if matches_selector(p, selector)]


# ---------------------------------------------------------------------------
# Twin-inventory helpers.
#
# `docs/WORK_ORDER_tap_eligibility.md` D1 (Authority: user, 2026-07-28):
# `_tapped_ifaces` (feeding `TapIndex.tapped`, and every `idx.tapped` read
# below) now returns the twin's FULL attachment topology -- EVERY interface
# `twin.hosts[].nets[]` carries, WAN uplink included -- not a `tap:`-flagged
# SUBSET. There is no more distinction between "tappable" and "attached":
# the tap plan is the sole authority over what it chooses to bind, and any
# port it names is eligible (`attach-vm-to-vnet` fires unconditionally for
# every leg regardless of any authoring choice, LES-095, so this is a
# complete, redeploy-stable set). The NAME (`_tapped_ifaces`/`idx.tapped`,
# both below and at every call site) is kept UNCHANGED (a purely internal
# identifier, never part of this module's public surface) so this is a
# one-function diff, not a project-wide rename -- see the module docstring's
# "Eligibility" section for the full rationale.
#
# `TapIndex` precomputes both ONCE per resolve pass (Perf, `docs/
# WORK_ORDER_tap_points.md` code review) -- `resolve_tap_point` used to
# rebuild both from `twin` on EVERY call (once per matched point per
# binding); `derive_default_tap_points`/`resolve_tap_point` both accept an
# optional pre-built `index=` now, building one internally (unchanged
# behavior) when a caller doesn't supply one.
# ---------------------------------------------------------------------------
def _tapped_ifaces(twin: TwinInventory) -> set[tuple[str, str, int]]:
    """Every `(host_id, net_id, nic_index)` triple in the twin's topology,
    from `HostInfo.nets` (`attach-vm-to-vnet`'s unconditional output) --
    external (WAN) legs INCLUDED, so `perimeter: wan` has data to resolve
    against on ANY deployed twin, no `--fresh` redeploy ever required
    (unlike the retired `record-tap-intent`-sourced predecessor of this
    function, whose additive WAN entry could go stale on an
    already-`completed` op, LES-096 -- that caveat no longer applies: this
    reads the SAME unconditional op every other leg does)."""
    out: set[tuple[str, str, int]] = set()
    for h in twin.hosts:
        for n in h.nets:
            if n.nic_index is None:
                continue
            out.add((h.host_id, n.net_id, n.nic_index))
    return out


def _host_legs(twin: TwinInventory) -> dict[str, set[str]]:
    """host_id -> the set of net_ids it has ANY leg on (tapped or not) --
    needed to find which host(s) actually JOIN two segments (boundary
    derivation/resolution) rather than merely having a tapped interface on
    one of them, and (M5) to detect a `segment` point's PARTIAL coverage.

    EXCLUDES `external` legs (`HostNet.external`, i.e. today just a router's
    synthetic `__wan__` uplink, `docs/WORK_ORDER_tap_scopes.md` M2) --
    `boundary`/`segment` are about TWIN SEGMENTS (brief §5.1: the WAN edge is
    `perimeter`'s job, not `boundary`'s or `segment`'s); including `__wan__`
    here would let a router's WAN leg silently count toward its "multi-homed,
    joins segments A and B" boundary shape, which is not what a `boundary`
    point means. `perimeter` resolution (below) reads `idx.tapped` directly
    instead, so it can still reach the WAN uplink.

    ALSO excludes a leg with `nic_index is None` (code review follow-up,
    `docs/WORK_ORDER_tap_eligibility.md`) -- aligned with `_tapped_ifaces`
    above, which structurally can't carry one (`nic_index` is a required
    `int` in its tuple shape). Without this, a nic-index-less leg could make
    a host "join" a segment here while contributing no interface `idx.tapped`
    ever sees, which is exactly the narrow gap that made `TapPointResolutionError`
    ("should be unreachable") theoretically reachable -- unaddressable legs now
    agree on both sides, closing it for real rather than by message-softening."""
    out: dict[str, set[str]] = {}
    for h in twin.hosts:
        out[h.host_id] = {n.net_id for n in h.nets if not n.external and n.nic_index is not None}
    return out


def _is_router_kind(kind: Optional[str]) -> bool:
    """True iff a `HostInfo.kind` value identifies a router -- the ONE
    predicate `perimeter` resolution uses to find router ports on a target
    net. `inventory.py` already normalizes/back-fills this field (LES-096:
    `clone-vm`'s `node_kind` input, or `"router"` back-filled from the
    presence of a `configure-router`/`configure-routing` op), so a plain
    equality check is all this needs -- retained as its own named predicate
    (not inlined) so every call site reads the same way."""
    return kind == "router"


@dataclass(frozen=True)
class TapIndex:
    """Precomputed twin facts, built ONCE per resolve pass and threaded
    through `derive_default_tap_points`/`resolve_tap_point`/
    `segment_attached_hosts` (Perf, code review) instead of each
    recomputing `_tapped_ifaces`/`_host_legs` from `twin` independently."""

    tapped: frozenset[tuple[str, str, int]]
    legs: dict[str, frozenset[str]]
    # host_ids identified as `kind: router` (`_is_router_kind`) -- load-bearing
    # for `perimeter` resolution (`docs/WORK_ORDER_tap_scopes.md` M1), no
    # longer used for any `segment`/`boundary` EXCLUSION (brief §3.1: under
    # rx-only, excluding a router's transit leg from `segment` is a coverage
    # hole, not a de-dup -- see the module docstring).
    routers: frozenset[str]


def build_tap_index(twin: TwinInventory) -> TapIndex:
    return TapIndex(
        tapped=frozenset(_tapped_ifaces(twin)),
        legs={h: frozenset(nets) for h, nets in _host_legs(twin).items()},
        routers=frozenset(h.host_id for h in twin.hosts if _is_router_kind(h.kind)),
    )


def segment_attached_hosts(
    twin: TwinInventory, net_id: str, *, index: Optional[TapIndex] = None,
) -> list[str]:
    """Every host with ANY leg on `net_id` -- the FULL membership of a
    `segment` tap point's net (M5, `docs/WORK_ORDER_tap_points.md` code
    review; originally the OTHER half of a segment's coverage picture,
    contrasted against `resolve_tap_point`'s then-tap-gated SUBSET).

    As of `docs/WORK_ORDER_tap_eligibility.md` D1 (2026-07-28), a `segment`
    point's `resolve_tap_point` result and THIS function's result are
    ALWAYS THE SAME SET (both now read the full attachment topology, with
    no more `tap:`-gated subset to differ from) -- so `axrange.o2control.
    taps`'s M5 "segment covers N of M attached hosts" warning, which
    compares the two, is now STRUCTURALLY unable to fire for a genuine
    `segment`-scope match (kept there as defensive code, same posture as
    `TapPointResolutionError` above). This function is still correct and
    still used (e.g. by that comparison), just no longer capable of
    disagreeing with `resolve_tap_point`."""
    idx = index or build_tap_index(twin)
    return sorted(h for h, nets in idx.legs.items() if net_id in nets)


# ---------------------------------------------------------------------------
# Auto-derivation -- the ONLY candidate tap-point catalog (`docs/
# WORK_ORDER_tap_eligibility.md` D3: the twin-config's own declarative
# `tap_points:` block this used to "stand in until" is REMOVED, not landed --
# there is no future author-declared source waiting in the wings any more).
# INTERNAL resolution machinery, consumed at bind time by `tap_point:`/
# `selector:` addressing (`axrange.o2control.taps`) -- never published (D5).
# ---------------------------------------------------------------------------
_WAN_NET_ID = "__wan__"


def derive_default_tap_points(
    twin: TwinInventory, *, index: Optional[TapIndex] = None,
) -> list[TapPoint]:
    """Group the twin's FULL attachment topology (`docs/
    WORK_ORDER_tap_eligibility.md` D1 -- no `tap:` gating, no labels) into
    candidate tap points (`docs/design/06-taps-sensors.md`):

      * one `segment` point per (non-WAN) net_id that has >=1 attached
        interface -- `target` is that net_id. `__wan__` is EXCLUDED (a
        `segment` point means a twin's own network, never its external edge
        -- that's `perimeter`'s job, below).
      * one `port` point per individual attached interface, ALWAYS --
        including a router's WAN uplink (`net_id == "__wan__"`,
        `docs/WORK_ORDER_tap_scopes.md` M3) -- direct addressability of "one
        specific interface" must never depend on whether the larger
        groupings below also happen to exist; renamed from `leg` (M1, same
        id shape `host:net:nic`).
      * one `perimeter` point per (net_id, has-a-router-port) pair --
        `target` is that net_id, id is `f"{net_id}-perimeter"` EXCEPT for
        the WAN uplink, whose id is `wan-perimeter` (M1). Resolves to every
        `kind: router` port on that net (`resolve_tap_point`, below) -- this
        is what makes the twin's external edge reachable as an ordinary tap
        point, no actionable-error placeholder needed (unlike `boundary`, a
        `perimeter` point with no router port on the net simply doesn't
        exist in the catalog -- there's no "conduit node" concept to signal
        a gap for).
      * one `boundary` point for EVERY pair of (non-WAN) segments joined by
        a host with legs on BOTH (M4, `docs/WORK_ORDER_tap_points.md` code
        review). As of D1, this ALWAYS resolves to real interfaces when the
        pair is derived (`TapPointResolutionError` is now unreachable in
        practice through this catalog -- see the module docstring) --
        derivation no longer depends on whether an author remembered to tap
        the joining `conduit`/`core`/`fw`/`gw`/`hq-core` node; ANY host that
        structurally joins two segments earns the point, and it resolves.
        Never a segment+WAN pair (`_host_legs` excludes `external` nets --
        brief §5.1).

    A flat/unsegmented twin (one net, no multi-homed host) derives exactly
    the `segment` + `port` points and ZERO `boundary`/`perimeter` points --
    the worked example (`sme-flat`) this exists to make work: "a flat twin
    has no meaningful north-south/east-west split at all, yet still needs
    tap points," and a `segment` tap point describes it exactly."""
    idx = index or build_tap_index(twin)
    tapped = idx.tapped
    legs = idx.legs
    routers = idx.routers
    points: list[TapPoint] = []

    # segment -- every net EXCEPT the twin's external edge.
    net_ids = sorted({net_id for (_h, net_id, _n) in tapped if net_id != _WAN_NET_ID})
    for net_id in net_ids:
        points.append(TapPoint(id=net_id, scope="segment", target=net_id))

    # port (every attached interface, individually addressable, WAN included)
    for host_id, net_id, nic_index in sorted(tapped):
        points.append(
            TapPoint(
                id=f"{host_id}:{net_id}:{nic_index}", scope="port",
                target={"host": host_id, "net": net_id},
            )
        )

    # perimeter -- every net (WAN included) with >=1 ROUTER port on it.
    perimeter_net_ids = sorted(
        {net_id for (h, net_id, _n) in tapped if h in routers}
    )
    for net_id in perimeter_net_ids:
        point_id = "wan-perimeter" if net_id == _WAN_NET_ID else f"{net_id}-perimeter"
        points.append(TapPoint(id=point_id, scope="perimeter", target=net_id))

    # boundary: a pair of (non-WAN) segments sharing a host with legs on
    # BOTH -- emitted UNCONDITIONALLY (existence is a DERIVATION-time
    # concern; `resolve_tap_point`'s `TapPointResolutionError` is a
    # separate, now-unreachable-in-practice RESOLUTION-time concern, see
    # the module docstring). `legs` (from `_host_legs`) already excludes
    # `external` nets, so no `__wan__` pair can ever be derived here.
    seg_pairs: set[tuple[str, str]] = set()
    for nets in legs.values():
        if len(nets) < 2:
            continue
        ordered = sorted(nets)
        for i, a in enumerate(ordered):
            for b in ordered[i + 1:]:
                seg_pairs.add((a, b))
    for a, b in sorted(seg_pairs):
        points.append(TapPoint(id=f"{a}--{b}", scope="boundary", target=(a, b)))

    return points


def resolve_tap_point(
    point: TapPoint, twin: TwinInventory, *, index: Optional[TapIndex] = None,
) -> list[TapInterfaceRef]:
    """`TapPoint` -> the concrete interfaces it covers, resolved against the
    twin's FULL attachment topology (`docs/WORK_ORDER_tap_eligibility.md` D1
    -- ANY port a point names is eligible, no `tap:` gate anywhere). Generic
    over WHERE the point came from (auto-derived above, or a future
    author-declared one) -- only `scope`/`target`'s shape matters (already
    validated at `TapPoint` construction, `TapPointShapeError`).

    Returns `[]` for a target that resolves to nothing, for every scope
    EXCEPT one case: a `boundary` point whose joining host(s) genuinely
    exist in the topology (`_host_legs`) but have no matching leg on either
    segment raises `TapPointResolutionError` instead (M4) -- "this place
    exists, nothing is wired to capture it, here is what to do about it,"
    not a silent empty match indistinguishable from "no such boundary
    exists at all." As of D1 this is UNREACHABLE in practice (see that
    class's own docstring) -- kept as defensive code. Every other scope,
    and a `boundary` target that no host actually joins, still just returns
    `[]` -- the CALLER's (`axrange.o2control.taps`) decision to reject an
    empty binding or not."""
    idx = index or build_tap_index(twin)
    tapped = idx.tapped
    if point.scope == "segment":
        out = {i for i in tapped if i[1] == point.target}
    elif point.scope == "port":
        host_id = point.target.get("host")
        net_id = point.target.get("net")
        out = {i for i in tapped if i[0] == host_id and i[1] == net_id}
    elif point.scope == "perimeter":
        # Every ROUTER port on the target net -- `target` may be `"__wan__"`
        # (the twin's external edge, `docs/WORK_ORDER_tap_scopes.md` M2/M3),
        # which is just another net_id value from this module's point of
        # view; the `wan` author-facing alias is resolved to the sentinel
        # BEFORE this function ever sees it (`axrange.o2control.taps`, M3),
        # not here.
        #
        # Nit (fix round, `docs/WORK_ORDER_tap_scopes.md`, final round): if
        # this resolves EMPTY, the likely cause is `idx.routers` being empty
        # because `HostInfo.kind` never resolved to `"router"` for the node
        # an operator expected (LES-096: a deploy predating `node_kind`/the
        # `configure-router` back-fill), NOT "no router is attached to this
        # net at all" (D1: every attached router port is now eligible, so an
        # empty result here means either no router touches this net, or its
        # `kind` didn't resolve). Check `kind` first when debugging an
        # unexpectedly-empty `perimeter` resolution -- there is no leg-count
        # fallback here the way `_is_route_ambiguous`/`_is_router_host`
        # (`o2control/taps.py`) have.
        out = {i for i in tapped if i[1] == point.target and i[0] in idx.routers}
    elif point.scope == "boundary":
        a, b = point.target
        legs = idx.legs
        hosts_joining = sorted(h for h, nets in legs.items() if a in nets and b in nets)
        out = {i for i in tapped if i[0] in hosts_joining and i[1] in (a, b)}
        if hosts_joining and not out:  # pragma: no cover -- see class docstring
            raise TapPointResolutionError(
                f"boundary {point.id}: joining host(s) "
                f"{', '.join(repr(h) for h in hosts_joining)} exist but "
                f"resolved no interface on {a!r} or {b!r} -- this should be "
                "unreachable under full-topology eligibility; please report"
            )
    else:  # pragma: no cover -- TapPoint.__post_init__ already rejects this
        raise TapPointShapeError(f"unknown tap-point scope: {point.scope!r}")
    return [TapInterfaceRef(*i) for i in sorted(out)]
