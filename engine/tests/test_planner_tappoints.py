"""Tests for `axrange.planner.tappoints` (`docs/design/06-taps-sensors.md`,
`docs/WORK_ORDER_tap_scopes.md` -- supersedes the four-scope set this module
shipped with 2026-07-24, `docs/design/02-twin-model.md`;
`docs/WORK_ORDER_tap_eligibility.md`, Authority: user, 2026-07-28 --
eligibility = the FULL attachment topology, no `tap:` gate).

Resolution per scope is exercised directly against hand-built
`TwinInventory`/`HostInfo` instances (a "mock inventory" -- work order §4)
so these tests stay independent of the planner/adapter/state store
entirely; one round-trip test at the bottom drives a REAL
`build_plan`+`Executor` run (the same idiom `tests/test_twin_inventory.py`
uses) to prove the auto-derivation behaves correctly against actually
persisted op rows, not just a fixture standing in for them.

No more `TapPointInfo`/`tap_points=` anywhere in this file (D5: the twin
inventory publishes no candidate-capture-point catalog) -- a fixture's
`hosts=[...]` (full attachment topology) IS the eligibility source now.
"""
from __future__ import annotations

import dataclasses

import pytest

from axrange.planner.inventory import HostInfo, HostNet, TwinInventory, build_twin_inventory
from axrange.planner.tappoints import (
    TAP_SCOPES,
    TapPoint,
    TapPointResolutionError,
    TapPointSelector,
    TapPointShapeError,
    derive_default_tap_points,
    direction_for_scope,
    matches_selector,
    resolve_tap_point,
    select_tap_points,
)


def _host(host_id, nets, *, kind=None):
    """`nets` may contain `"__wan__"` -- marked `HostNet.external=True`
    automatically (mirroring `inventory.py`'s own real derivation, the only
    external net today), so test fixtures never have to spell out the flag
    by hand. `nic_index` is assigned SEQUENTIALLY by list position (0, 1,
    2, ...), matching a real deploy's `attach-vm-to-vnet` emission order --
    the full topology this builds IS the eligibility source now (`docs/
    WORK_ORDER_tap_eligibility.md` D1), so every leg needs a real
    `nic_index` for resolution to see it at all (a `None` nic_index is
    dropped, exactly like a real malformed/incomplete op record would be)."""
    return HostInfo(
        host_id=host_id, kind=kind,
        nets=[
            HostNet(net_id=n, nic_index=i, external=(n == "__wan__"))
            for i, n in enumerate(nets)
        ],
    )


# ---------------------------------------------------------------------------
# No `direction` field anywhere on `TapPoint`/`TapPointSelector` -- direction
# is derived from scope (`direction_for_scope`), never authored, and belongs
# to the BINDING a caller composes, not to the point/interface itself.
# ---------------------------------------------------------------------------
def test_tap_point_has_no_direction_field():
    field_names = {f.name for f in dataclasses.fields(TapPoint)}
    assert field_names == {"id", "scope", "target", "labels"}
    assert "direction" not in field_names


def test_tap_point_rejects_unknown_scope():
    with pytest.raises(ValueError, match="unknown scope"):
        TapPoint(id="x", scope="direction", target="x")


def test_tap_scopes_are_exactly_the_four_from_the_brief():
    assert set(TAP_SCOPES) == {"port", "segment", "perimeter", "boundary"}


@pytest.mark.parametrize(
    "scope,expected",
    [("port", "both"), ("perimeter", "both"), ("segment", "rx"), ("boundary", "rx")],
)
def test_direction_for_scope(scope, expected):
    assert direction_for_scope(scope) == expected


def test_direction_for_scope_rejects_unknown_scope():
    with pytest.raises(ValueError, match="unknown tap-point scope"):
        direction_for_scope("host")  # the dropped scope


# ---------------------------------------------------------------------------
# SHOULD-FIX (`docs/WORK_ORDER_tap_points.md` code review): `TapPoint`
# validates `target`'s SHAPE per scope at construction time, raising the
# typed `TapPointShapeError` (a `ValueError` subclass) rather than deferring
# to a bare `ValueError`/`AttributeError` inside `resolve_tap_point`'s
# scope-keyed unpacking -- matters most for a future author-declared point
# (`docs/WORK_ORDER_twin_configs.md`) this module cannot itself validate
# any earlier than construction.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "scope,target",
    [
        ("segment", ""),
        ("segment", 123),
        ("perimeter", ""),
        ("perimeter", 123),
        ("port", "not-a-dict"),
        ("port", {"host": "h"}),  # missing "net"
        ("port", {"host": "", "net": "n"}),  # empty host
        ("boundary", "not-a-tuple"),
        ("boundary", ("a",)),  # wrong length
        ("boundary", ("a", "", )),  # empty member
        ("boundary", ("a", "b", "c")),  # too long
    ],
)
def test_tap_point_rejects_malformed_target_per_scope(scope, target):
    with pytest.raises(TapPointShapeError):
        TapPoint(id="x", scope=scope, target=target)


def test_tap_point_accepts_a_list_boundary_target_and_normalizes_to_tuple():
    """A `list` (as YAML/JSON naturally produces, e.g. `target: [a, b]`) is
    accepted for a `boundary` point and NORMALIZED to a tuple -- so
    `resolve_tap_point`'s `a, b = point.target` unpack and downstream
    hashing both behave identically regardless of whether the point came
    from Python code (a tuple) or a parsed selector/declaration (a list)."""
    p = TapPoint(id="x", scope="boundary", target=["corp", "dmz"])
    assert p.target == ("corp", "dmz")
    assert isinstance(p.target, tuple)


# ---------------------------------------------------------------------------
# SHOULD-FIX: `TapPoint` is hashable (identity by `id`) despite `port`/
# `boundary` targets being `dict`/`tuple` -- needed to `set()`-dedupe
# overlapping selector matches, the obvious way to combine several matched
# points' interfaces without double-processing one that matched twice.
# ---------------------------------------------------------------------------
def test_tap_point_is_hashable_and_set_dedupes_by_id():
    p1 = TapPoint(id="x", scope="port", target={"host": "h", "net": "n"})
    p2 = TapPoint(id="x", scope="port", target={"host": "h", "net": "n"})
    p3 = TapPoint(id="y", scope="boundary", target=("a", "b"))
    assert p1 == p2
    assert hash(p1) == hash(p2)
    assert {p1, p2, p3} == {p1, p3}  # p1/p2 dedupe (same id), p3 distinct


def test_tap_point_equality_is_by_id_not_by_other_fields():
    """Deliberate: identity is the `id`, not the full field tuple -- a
    `TapPoint` is its catalog entry, matching the "ambiguous ids" SHOULD-FIX
    (two points must never coexist with the same id)."""
    p1 = TapPoint(id="x", scope="segment", target="a")
    p2 = TapPoint(id="x", scope="segment", target="b")  # same id, diff target
    assert p1 == p2


# ---------------------------------------------------------------------------
# Flat / no-segmentation twin (the brief's worked example, `sme-flat`) --
# ONE net, no router: segment + port points only, ZERO perimeter/boundary --
# this is the FIRST-CLASS case the work order calls out explicitly.
# ---------------------------------------------------------------------------
def _flat_twin() -> TwinInventory:
    return TwinInventory(
        deploy_id="flat-1",
        hosts=[_host("h1", ["corp"]), _host("h2", ["corp"])],
        net_bridge={"corp": "br-corp"},
    )


def test_flat_twin_derives_segment_and_port_only():
    twin = _flat_twin()
    points = derive_default_tap_points(twin)
    by_scope: dict[str, list[TapPoint]] = {}
    for p in points:
        by_scope.setdefault(p.scope, []).append(p)
    assert set(by_scope) == {"segment", "port"}
    assert [p.id for p in by_scope["segment"]] == ["corp"]
    assert {p.id for p in by_scope["port"]} == {"h1:corp:0", "h2:corp:0"}


def test_flat_twin_segment_resolves_to_every_host_leg_on_that_net():
    twin = _flat_twin()
    seg = TapPoint(id="corp", scope="segment", target="corp")
    refs = resolve_tap_point(seg, twin)
    assert {r.as_tuple() for r in refs} == {("h1", "corp", 0), ("h2", "corp", 0)}


def test_flat_twin_port_resolves_to_exactly_one_interface():
    twin = _flat_twin()
    port = TapPoint(id="h1:corp:0", scope="port", target={"host": "h1", "net": "corp"})
    refs = resolve_tap_point(port, twin)
    assert [r.as_tuple() for r in refs] == [("h1", "corp", 0)]


def test_flat_twin_derives_no_boundary_or_perimeter_points():
    """The worked example exists to make work: a flat twin has no
    meaningful north-south/east-west split, and this module never invents
    one -- zero `boundary`/`perimeter` points here, `segment` describes it
    exactly."""
    twin = _flat_twin()
    points = derive_default_tap_points(twin)
    assert [p for p in points if p.scope in ("boundary", "perimeter")] == []


# ---------------------------------------------------------------------------
# Multi-homed router joining two segments -- boundary derivation/resolution,
# and (M1) `segment`/`perimeter` including a router leg. Full-topology
# eligibility (`docs/WORK_ORDER_tap_eligibility.md` D1, Authority: user,
# 2026-07-28): NO gating parameter any more -- r-edge's legs are ALWAYS
# eligible once they exist in the topology, so `_router_twin` no longer
# takes a `dmz_tapped=` toggle.
# ---------------------------------------------------------------------------
def _router_twin() -> TwinInventory:
    return TwinInventory(
        deploy_id="rtr-1",
        hosts=[
            _host("h-a", ["corp"]),
            _host("h-other", ["dmz"]),
            _host("r-edge", ["corp", "dmz"], kind="router"),
        ],
        net_bridge={"corp": "br-corp", "dmz": "br-dmz"},
    )


def test_boundary_point_derived_for_router_joining_two_segments():
    twin = _router_twin()
    points = derive_default_tap_points(twin)
    boundary = [p for p in points if p.scope == "boundary"]
    assert len(boundary) == 1
    assert boundary[0].id == "corp--dmz"
    assert boundary[0].target == ("corp", "dmz")


def test_boundary_point_resolves_to_only_the_joining_router_legs():
    twin = _router_twin()
    b = TapPoint(id="corp--dmz", scope="boundary", target=("corp", "dmz"))
    refs = resolve_tap_point(b, twin)
    # ONLY r-edge's two legs -- h-a's corp leg must NOT appear (h-a does
    # not join BOTH segments, it's single-homed on corp).
    assert {r.as_tuple() for r in refs} == {
        ("r-edge", "corp", 0), ("r-edge", "dmz", 1),
    }


def test_boundary_point_resolves_successfully_with_no_gating_at_all():
    """`docs/WORK_ORDER_tap_eligibility.md` D1 (Authority: user, 2026-07-28):
    a host joining two segments resolves a `boundary` point SUCCESSFULLY --
    no `TapPointResolutionError` any more (that class is now unreachable in
    practice through auto-derivation, kept only as defensive code; see its
    own docstring). The OLD behavior this supersedes required a prior
    `tap:` declaration on at least one leg or raised an actionable error
    instead of resolving (M4, `docs/WORK_ORDER_tap_points.md` -- exactly
    the bug that made `ot-ics-range`'s `idmz-conduit` inexpressible with
    zero signal why); now ANY router that structurally joins two segments
    resolves, full stop -- no author action needed at all."""
    twin = TwinInventory(
        deploy_id="rtr-2",
        hosts=[_host("r-edge", ["corp", "dmz"], kind="router")],
        net_bridge={"corp": "br-corp", "dmz": "br-dmz"},
    )
    points = derive_default_tap_points(twin)
    boundary = [p for p in points if p.scope == "boundary"]
    assert len(boundary) == 1
    assert boundary[0].id == "corp--dmz"

    refs = resolve_tap_point(boundary[0], twin)
    assert {r.as_tuple() for r in refs} == {
        ("r-edge", "corp", 0), ("r-edge", "dmz", 1),
    }


def test_segment_includes_the_router_leg_not_just_leaf_hosts():
    """Brief §3.1: under rx-only, a router's transit leg is NOT excluded
    from `segment` -- excluding it would be a coverage hole (traffic
    arriving from another segment enters corp AT r-edge's port, and no
    other port on corp mirrors it), not a de-dup. `derive_default_tap_points`
    names exactly one `segment` point for `corp`, and it resolves BOTH
    h-a's leg and r-edge's leg."""
    twin = _router_twin()
    points = derive_default_tap_points(twin)
    corp_points = [p for p in points if p.scope == "segment" and p.id == "corp"]
    assert len(corp_points) == 1
    refs = {r.as_tuple() for r in resolve_tap_point(corp_points[0], twin)}
    assert refs == {("h-a", "corp", 0), ("r-edge", "corp", 0)}


def test_router_with_no_port_on_a_net_earns_no_perimeter_there_but_keeps_its_real_boundary():
    """A router that simply has no leg on a third net (`mgmt`) earns no
    `perimeter` point for it -- a TOPOLOGY fact (no router PORT exists
    there at all), not a gating one; the `boundary` it DOES join
    (corp<->dmz, the legs it actually has) derives and resolves normally
    regardless."""
    twin = TwinInventory(
        deploy_id="rtr-3",
        hosts=[
            _host("h-a", ["corp"]),
            _host("r-edge", ["corp", "dmz"], kind="router"),
        ],
        net_bridge={"corp": "br-corp", "dmz": "br-dmz", "mgmt": "br-mgmt"},
    )
    points = derive_default_tap_points(twin)
    perimeter_targets = {p.target for p in points if p.scope == "perimeter"}
    assert perimeter_targets == {"corp", "dmz"}
    assert "mgmt" not in perimeter_targets
    boundary = [p for p in points if p.scope == "boundary"]
    assert len(boundary) == 1
    refs = resolve_tap_point(boundary[0], twin)
    assert {r.as_tuple() for r in refs} == {
        ("r-edge", "corp", 0), ("r-edge", "dmz", 1),
    }


def test_resolve_tap_point_returns_empty_for_no_match_never_raises():
    twin = _flat_twin()
    seg = TapPoint(id="nope", scope="segment", target="nope")
    assert resolve_tap_point(seg, twin) == []


# ---------------------------------------------------------------------------
# `perimeter` -- every ROUTER port on a target net, both directions; also
# the mechanism for the twin's WHOLE EXTERNAL EDGE (`docs/
# WORK_ORDER_tap_scopes.md` M2/M3: `__wan__` is just another net_id once
# recorded; `docs/WORK_ORDER_tap_eligibility.md` D1: no gate at all any
# more -- ANY router port is eligible).
# ---------------------------------------------------------------------------
def test_perimeter_point_derived_and_resolves_to_the_router_port_only():
    twin = _router_twin()
    points = derive_default_tap_points(twin)
    perimeters = {p.id: p for p in points if p.scope == "perimeter"}
    assert set(perimeters) == {"corp-perimeter", "dmz-perimeter"}
    refs = {r.as_tuple() for r in resolve_tap_point(perimeters["corp-perimeter"], twin)}
    # ONLY r-edge's corp leg -- h-a is not a router, so its corp leg must
    # NOT appear in a `perimeter` resolution.
    assert refs == {("r-edge", "corp", 0)}


def test_perimeter_wan_resolves_the_router_wan_uplink():
    """`perimeter: wan` (the author-facing alias resolved OUTSIDE this
    module, `axrange.o2control.taps`) is, from `tappoints`' point of view,
    just `target="__wan__"` -- the twin's external edge, published
    unconditionally (`HostNet.external=True`, `docs/WORK_ORDER_tap_scopes.md`
    M2) the same way any other leg is."""
    twin = TwinInventory(
        deploy_id="wan-1",
        hosts=[
            _host("h-a", ["corp"]),
            _host("gw", ["corp", "__wan__"], kind="router"),
        ],
        net_bridge={"corp": "br-corp"},
    )
    points = derive_default_tap_points(twin)
    perimeters = {p.id: p for p in points if p.scope == "perimeter"}
    assert "wan-perimeter" in perimeters
    assert perimeters["wan-perimeter"].target == "__wan__"
    refs = {r.as_tuple() for r in resolve_tap_point(perimeters["wan-perimeter"], twin)}
    assert refs == {("gw", "__wan__", 1)}


def test_perimeter_excludes_a_non_router_hosts_port_on_the_same_net():
    """A leaf host's own port on the net is NEVER a `perimeter` port --
    `perimeter` names ROUTER ports only, regardless of eligibility."""
    twin = _router_twin()
    perim = TapPoint(id="corp-perimeter", scope="perimeter", target="corp")
    refs = {r.as_tuple() for r in resolve_tap_point(perim, twin)}
    assert ("h-a", "corp", 0) not in refs


def test_perimeter_with_no_router_port_on_the_net_resolves_empty_not_error():
    """Unlike `boundary`, a `perimeter` target with no router port on it is
    simply `[]` -- there's no "conduit node exists but nothing's wired"
    case to signal for (a `perimeter` point isn't even NAMED in the catalog
    until >=1 router port exists on that net)."""
    twin = TwinInventory(
        deploy_id="rtr-4",
        hosts=[
            _host("h-a", ["corp"]),
            _host("r-edge", ["corp"], kind="router"),  # no dmz leg at all
        ],
        net_bridge={"corp": "br-corp", "dmz": "br-dmz"},
    )
    perim = TapPoint(id="dmz-perimeter", scope="perimeter", target="dmz")
    assert resolve_tap_point(perim, twin) == []
    assert [p for p in derive_default_tap_points(twin) if p.target == "dmz"
            and p.scope == "perimeter"] == []


# ---------------------------------------------------------------------------
# Selector matching -- id / scope / target(/segment) / labels.
# ---------------------------------------------------------------------------
def test_selector_matches_by_id():
    twin = _router_twin()
    points = derive_default_tap_points(twin)
    matched = select_tap_points(points, TapPointSelector(id="corp"))
    assert [p.id for p in matched] == ["corp"]


def test_selector_matches_by_scope():
    twin = _router_twin()
    points = derive_default_tap_points(twin)
    matched = select_tap_points(points, TapPointSelector(scope="boundary"))
    assert {p.id for p in matched} == {"corp--dmz"}


def test_selector_matches_by_target_across_scopes():
    """A `target="dmz"` selector matches BOTH the `corp<->dmz` boundary
    point (dmz is one of its two segments) and the `port` point on dmz --
    the caller need not know ahead of time which scope the match lands on."""
    twin = _router_twin()
    points = derive_default_tap_points(twin)
    matched = {p.id for p in select_tap_points(points, TapPointSelector(target="dmz"))}
    assert "corp--dmz" in matched
    assert "r-edge:dmz:1" in matched
    assert "corp" not in matched  # the corp segment point itself, excluded


def test_selector_matches_by_labels():
    p_ew = TapPoint(id="a", scope="segment", target="a", labels=("ew",))
    p_ns = TapPoint(id="b", scope="segment", target="b", labels=("ns", "egress"))
    matched = select_tap_points([p_ew, p_ns], TapPointSelector(labels=("ns",)))
    assert [p.id for p in matched] == ["b"]


def test_selector_combines_fields_with_and():
    p1 = TapPoint(id="a", scope="segment", target="a", labels=("ew",))
    p2 = TapPoint(id="b", scope="segment", target="b", labels=("ew",))
    matched = select_tap_points(
        [p1, p2], TapPointSelector(scope="segment", target="b", labels=("ew",))
    )
    assert [p.id for p in matched] == ["b"]


def test_selector_no_match_returns_empty_not_error():
    twin = _flat_twin()
    points = derive_default_tap_points(twin)
    assert select_tap_points(points, TapPointSelector(id="does-not-exist")) == []


def test_matches_selector_unset_fields_are_not_checked():
    p = TapPoint(id="a", scope="segment", target="a")
    assert matches_selector(p, TapPointSelector()) is True


# ---------------------------------------------------------------------------
# Real round-trip: derive against an ACTUAL `build_twin_inventory` output
# (build_plan + Executor + a fake adapter, `tests/test_twin_inventory.py`'s
# own idiom) -- proves the auto-derivation behaves correctly against
# persisted op rows, not merely a hand-built fixture standing in for them.
# ---------------------------------------------------------------------------
def test_derive_default_tap_points_against_real_twin_inventory(tmp_path, manifests):
    """`docs/WORK_ORDER_tap_eligibility.md` D1 (Authority: user, 2026-07-28):
    against a REAL `build_twin_inventory` output, EVERY host's leg is
    eligible -- including `h-other` (dmz), which carried no special
    declaration in the fixture IR at all (`tests/test_twin_inventory.py`'s
    `_IR`)."""
    from tests.test_twin_inventory import _build_and_run

    store, deploy_id = _build_and_run(tmp_path, manifests)
    try:
        twin = build_twin_inventory(store, deploy_id, manifests=manifests)
    finally:
        store.close()

    points = derive_default_tap_points(twin)
    by_scope: dict[str, list[str]] = {}
    for p in points:
        by_scope.setdefault(p.scope, []).append(p.id)

    # Both corp and dmz have >=1 attached interface -- each earns exactly
    # one `segment` point. `__wan__` NEVER earns a `segment` point (M1:
    # excluded from segment derivation, that's `perimeter`'s job).
    assert set(by_scope["segment"]) == {"corp", "dmz"}
    # r-edge is `kind: router` (this fixture's IR) -- its WAN uplink is
    # published unconditionally too, so it earns its own `port` point (WAN
    # included) and a `wan-perimeter` alongside the two segment-edge
    # perimeter points its corp/dmz legs already earn. `h-other:dmz:0` is
    # the headline D1 addition -- eligible with zero authoring.
    assert set(by_scope["port"]) == {
        "h-a:corp:0", "h-other:dmz:0",
        "r-edge:corp:0", "r-edge:dmz:1", "r-edge:__wan__:2",
    }
    assert by_scope["boundary"] == ["corp--dmz"]
    assert set(by_scope["perimeter"]) == {"corp-perimeter", "dmz-perimeter", "wan-perimeter"}

    seg = next(p for p in points if p.id == "corp")
    refs = {r.as_tuple() for r in resolve_tap_point(seg, twin)}
    assert refs == {("h-a", "corp", 0), ("r-edge", "corp", 0)}

    dmz_seg = next(p for p in points if p.id == "dmz")
    dmz_refs = {r.as_tuple() for r in resolve_tap_point(dmz_seg, twin)}
    assert dmz_refs == {("h-other", "dmz", 0), ("r-edge", "dmz", 1)}

    wan_perim = next(p for p in points if p.id == "wan-perimeter")
    wan_refs = {r.as_tuple() for r in resolve_tap_point(wan_perim, twin)}
    assert wan_refs == {("r-edge", "__wan__", 2)}


# ---------------------------------------------------------------------------
# Adversarial deepening (tester pass, `docs/WORK_ORDER_tap_points.md` §4).
# ---------------------------------------------------------------------------

# A host with TWO legs on the SAME net -- the `segment` point for that one
# net already covers BOTH legs fully (a `segment` point is "all eligible
# traffic on this net").
def _dual_leg_same_net_twin() -> TwinInventory:
    return TwinInventory(
        deploy_id="dual-1",
        hosts=[_host("dual", ["corp", "corp"])],
        net_bridge={"corp": "br-corp"},
    )


def test_host_with_two_legs_on_same_net_segment_point_covers_both():
    twin = _dual_leg_same_net_twin()
    points = derive_default_tap_points(twin)
    seg = next(p for p in points if p.scope == "segment" and p.id == "corp")
    refs = {r.as_tuple() for r in resolve_tap_point(seg, twin)}
    assert refs == {("dual", "corp", 0), ("dual", "corp", 1)}


def test_host_with_two_legs_on_same_net_both_get_individual_port_points():
    twin = _dual_leg_same_net_twin()
    points = derive_default_tap_points(twin)
    ports = {p.id for p in points if p.scope == "port"}
    assert ports == {"dual:corp:0", "dual:corp:1"}


# ---------------------------------------------------------------------------
# Empty-resolution edge cases -- every one of these must return `[]`, never
# raise, so an empty result is distinguishable from an error ONLY by being
# a normal, successful return (work order §4: "resolution returning an
# empty set must be distinguishable from an error").
# ---------------------------------------------------------------------------
def test_segment_naming_a_net_with_no_attached_hosts_resolves_empty():
    """A net NO host is attached to at all -- a `segment` point naming it
    resolves to `[]`, not an error, and not a KeyError/crash. (Under
    `docs/WORK_ORDER_tap_eligibility.md` D1, a net WITH an attached host
    always resolves -- this is the one genuinely-empty case left: an
    attachment that simply doesn't exist.)"""
    twin = TwinInventory(
        deploy_id="empty-seg-1",
        hosts=[_host("h1", ["corp"])],  # nothing attached to quiet-net at all
        net_bridge={"corp": "br-corp", "quiet-net": "br-quiet"},
    )
    seg = TapPoint(id="quiet-net", scope="segment", target="quiet-net")
    assert resolve_tap_point(seg, twin) == []


def test_boundary_naming_two_segments_sharing_no_host_resolves_empty():
    """Two segments that exist independently (each with its own host) but
    NO host joins them -- a `boundary` point naming the pair resolves to
    `[]`. This is the disjoint-topology case, distinct from
    `test_boundary_point_resolves_successfully_with_no_gating_at_all`
    (which has a genuinely joining host)."""
    twin = TwinInventory(
        deploy_id="disjoint-1",
        hosts=[_host("h1", ["corp"]), _host("h2", ["dmz"])],
        net_bridge={"corp": "br-corp", "dmz": "br-dmz"},
    )
    b = TapPoint(id="corp--dmz", scope="boundary", target=("corp", "dmz"))
    assert resolve_tap_point(b, twin) == []
    # And auto-derivation agrees: no boundary point is even named.
    assert [p for p in derive_default_tap_points(twin) if p.scope == "boundary"] == []


def test_port_naming_a_host_net_pair_that_does_not_exist_resolves_empty():
    twin = _flat_twin()
    port = TapPoint(id="ghost", scope="port", target={"host": "ghost", "net": "nowhere"})
    assert resolve_tap_point(port, twin) == []


def test_port_naming_real_host_wrong_net_resolves_empty():
    """A `port` target pairing a REAL host with a net it's not actually
    attached to -- still `[]`, not an error."""
    twin = _flat_twin()
    port = TapPoint(id="wrong-net", scope="port", target={"host": "h1", "net": "dmz"})
    assert resolve_tap_point(port, twin) == []


@pytest.mark.parametrize(
    "point",
    [
        TapPoint(id="x", scope="segment", target="does-not-exist"),
        TapPoint(id="x", scope="perimeter", target="does-not-exist"),
        TapPoint(id="x", scope="port", target={"host": "nope", "net": "nope"}),
        TapPoint(id="x", scope="boundary", target=("a", "b")),
    ],
)
def test_resolve_tap_point_never_raises_across_every_scope(point):
    """Every scope's no-match path returns `[]` -- the empty-vs-error
    contract is uniform across all four scopes, not just the ones the
    happy-path tests happened to cover."""
    twin = _flat_twin()
    assert resolve_tap_point(point, twin) == []


# ---------------------------------------------------------------------------
# `direction` is absent everywhere on the point/selector model itself -- and
# a label literally named `ns` (or `ew`) is an ORDINARY free-form label with
# no special resolution meaning, never a taxonomy axis.
# ---------------------------------------------------------------------------
def test_no_direction_field_anywhere_in_selector_either():
    field_names = {f.name for f in dataclasses.fields(TapPointSelector)}
    assert "direction" not in field_names


def test_label_named_ns_is_an_ordinary_label_no_special_meaning():
    """A point labelled `ns` and a point labelled `gold` (an arbitrary,
    non-directional label) are matched by `select_tap_points` through the
    EXACT SAME mechanism -- proving `ns`/`ew` carry no special resolution
    weight, they're just strings in a set."""
    p_ns = TapPoint(id="a", scope="segment", target="a", labels=("ns",))
    p_gold = TapPoint(id="b", scope="segment", target="b", labels=("gold",))
    p_both = TapPoint(id="c", scope="segment", target="c", labels=("ns", "gold"))
    points = [p_ns, p_gold, p_both]

    assert {p.id for p in select_tap_points(points, TapPointSelector(labels=("ns",)))} == {
        "a", "c",
    }
    assert {p.id for p in select_tap_points(points, TapPointSelector(labels=("gold",)))} == {
        "b", "c",
    }
    # A point can carry `ns` alongside any other label with zero interaction
    # between them -- `ns` is not consumed/stripped/treated differently.
    assert set(p_both.labels) == {"ns", "gold"}


def test_selector_with_every_field_unset_matches_every_point():
    """The "matches everything" selector (all fields left `None`/empty) --
    exercised against a LIST of several distinct points, not just the
    single-point smoke test above, since an empty-selector's "select
    everything" behavior is exactly what an unaddressed API-composed
    binding would fall through to (see `tests/test_o2_taps.py`'s
    `test_apply_tap_plan_binding_with_no_addressing_selects_every_point_a_real_gap`)."""
    points = [
        TapPoint(id="a", scope="segment", target="a"),
        TapPoint(id="b", scope="perimeter", target="b"),
        TapPoint(id="c", scope="port", target={"host": "h", "net": "n"}),
        TapPoint(id="d", scope="boundary", target=("x", "y")),
    ]
    matched = select_tap_points(points, TapPointSelector())
    assert matched == points  # every point, in the SAME order given


def test_segment_resolves_every_attached_leg_not_just_the_first():
    """A net with THREE attached hosts (not just two, and not just "the
    first") -- `segment` resolution must return ALL of them."""
    twin = TwinInventory(
        deploy_id="three-1",
        hosts=[_host("h1", ["corp"]), _host("h2", ["corp"]), _host("h3", ["corp"])],
        net_bridge={"corp": "br-corp"},
    )
    seg = TapPoint(id="corp", scope="segment", target="corp")
    refs = {r.as_tuple() for r in resolve_tap_point(seg, twin)}
    assert refs == {("h1", "corp", 0), ("h2", "corp", 0), ("h3", "corp", 0)}


def test_segment_includes_every_attached_host_no_gating():
    """`docs/WORK_ORDER_tap_eligibility.md` D1 (Authority: user, 2026-07-28):
    a host physically ON the segment is ALWAYS included in that segment's
    tap point -- there is no more "tapped vs. untapped host" distinction to
    gate on. The OLD behavior this supersedes: a host with no `tap:`
    declared anywhere contributed NOTHING (module docstring's earlier
    "an untapped leg has no install-ovs-mirror hook to point at" framing --
    now moot, since eligibility is no longer a per-host authoring choice).
    Named as its own test (not just buried inside the real-inventory
    round-trip test) so the change is unambiguous."""
    twin = TwinInventory(
        deploy_id="mixed-1",
        hosts=[_host("h-a", ["corp"]), _host("h-b", ["corp"])],
        net_bridge={"corp": "br-corp"},
    )
    seg = TapPoint(id="corp", scope="segment", target="corp")
    refs = {r.as_tuple() for r in resolve_tap_point(seg, twin)}
    assert refs == {("h-a", "corp", 0), ("h-b", "corp", 0)}


def test_boundary_segment_to_wan_resolves_empty_not_error():
    """A segment<->WAN boundary point can be NAMED (the schema imposes no
    restriction on `target`'s two net ids) but cannot resolve to anything:
    `_host_legs` EXCLUDES `external` nets (brief §5.1: the WAN edge is
    `perimeter`'s job, not `boundary`'s), so no host is ever recorded as
    "joining" `corp` and `__wan__` for boundary purposes -- `[]`,
    gracefully, not a crash. `perimeter: wan` is the correct mechanism for
    the twin's external edge, exercised separately above."""
    twin = TwinInventory(
        deploy_id="wan-boundary-1",
        hosts=[_host("r-edge", ["corp", "__wan__"], kind="router")],
        net_bridge={"corp": "br-corp"},
    )
    b = TapPoint(id="corp--wan", scope="boundary", target=("corp", "__wan__"))
    assert resolve_tap_point(b, twin) == []
    assert [p for p in derive_default_tap_points(twin) if p.scope == "boundary"] == []


def test_selector_matching_nothing_vs_matching_everything_are_distinguishable():
    """A selector that matches NOTHING (`[]`) and one that matches
    EVERYTHING (the full list) are both legitimate, ordinary return values
    -- neither is an error, and they are trivially distinguishable by the
    caller (an empty list vs. a non-empty one), never conflated."""
    points = [TapPoint(id="a", scope="segment", target="a")]
    assert select_tap_points(points, TapPointSelector(id="nope")) == []
    assert select_tap_points(points, TapPointSelector()) == points


# ---------------------------------------------------------------------------
# SHOULD-FIX: a selector `target` may be a 2-SEQUENCE (the brief's own §4
# boundary-target shape, `target: [seg-a, seg-b]`) -- order-independent
# match against a `boundary` point's own 2-tuple, distinct from the scalar
# containment match a bare `target="dmz"` does.
# ---------------------------------------------------------------------------
def test_selector_target_as_two_sequence_matches_boundary_point_order_independent():
    b = TapPoint(id="corp--dmz", scope="boundary", target=("corp", "dmz"))
    other = TapPoint(id="corp--mgmt", scope="boundary", target=("corp", "mgmt"))
    matched = select_tap_points(
        [b, other], TapPointSelector(target=["dmz", "corp"])  # reversed order
    )
    assert [p.id for p in matched] == ["corp--dmz"]


def test_selector_target_as_two_sequence_does_not_match_non_boundary_scopes():
    seg = TapPoint(id="corp", scope="segment", target="corp")
    assert select_tap_points([seg], TapPointSelector(target=["corp", "dmz"])) == []


# ---------------------------------------------------------------------------
# Perf (code review): `TapIndex`/`build_tap_index` precompute once; passing
# an explicit `index=` to `derive_default_tap_points`/`resolve_tap_point`
# must be EQUIVALENT to letting each build its own (the default, `index=
# None`) -- proves the precomputed path is not a silently different
# derivation.
# ---------------------------------------------------------------------------
def test_precomputed_index_is_equivalent_to_default_derivation():
    from axrange.planner.tappoints import build_tap_index

    twin = _router_twin()
    default_points = derive_default_tap_points(twin)
    idx = build_tap_index(twin)
    indexed_points = derive_default_tap_points(twin, index=idx)
    assert default_points == indexed_points

    boundary = next(p for p in default_points if p.scope == "boundary")
    default_refs = {r.as_tuple() for r in resolve_tap_point(boundary, twin)}
    indexed_refs = {r.as_tuple() for r in resolve_tap_point(boundary, twin, index=idx)}
    assert default_refs == indexed_refs


# ---------------------------------------------------------------------------
# M5's data half: `segment_attached_hosts` -- every host attached to a net,
# the OTHER half of a `segment` point's coverage picture `resolve_tap_point`
# alone can't show (`docs/WORK_ORDER_tap_points.md`). As of `docs/
# WORK_ORDER_tap_eligibility.md` D1, this function's result and
# `resolve_tap_point`'s segment result are now ALWAYS THE SAME SET (both
# read the full topology) -- kept as its own function/test since it's still
# a useful, independently-named primitive (and the two are allowed to
# diverge for a hypothetical future author-declared point).
# ---------------------------------------------------------------------------
def test_segment_attached_hosts_covers_every_host_on_the_net():
    from axrange.planner.tappoints import segment_attached_hosts

    twin = TwinInventory(
        deploy_id="mixed-2",
        hosts=[
            _host("h-a", ["corp"]), _host("h-b", ["corp"]),
            _host("h-elsewhere", ["dmz"]),
        ],
        net_bridge={"corp": "br-corp", "dmz": "br-dmz"},
    )
    assert segment_attached_hosts(twin, "corp") == ["h-a", "h-b"]
    assert segment_attached_hosts(twin, "dmz") == ["h-elsewhere"]
    assert segment_attached_hosts(twin, "no-such-net") == []
