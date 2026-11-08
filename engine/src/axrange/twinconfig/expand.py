"""Twin-config -> Scenario IR expansion (`docs/design/09-content-overlays.md`,
`docs/WORK_ORDER_twin_configs.md` §2).

**Version-lock note (do NOT touch `axrange.ir.schema`/`IR_SCHEMA.md` from
this module):** expansion's whole job is to produce a plain `dict` shaped
EXACTLY like a hand-written `target-environments/*/scenario.yaml` — the
same `scenario:`/`layers:`/`bindings:`/`overlays:`/`fabric:` keys, nothing
new. `axrange.ir.parser.parse_ir` / `axrange.validator.validate_full` /
`axrange.planner.build_plan` consume the result completely unchanged; this
module never imports from `axrange.validator`/`axrange.planner` (LES-025 —
it produces content the SAME layer target-environments/ authors already
write by hand, one layer below the engine, not a second engine). Params
(`twin_config.params:` + `--param` overrides) are resolved entirely HERE,
before the IR dict exists — the Scenario IR itself carries no template
syntax and needs no schema change.

Pipeline: raw YAML dict -> `params.resolve_params` (declared + overrides)
-> `params.substitute` (walks the whole tree except `params:` itself) ->
`TwinConfig.model_validate` (now-substituted, so `count: "${N:25}"` is a
real `int` 25) -> `_build_ir` (segments/nodes -> IR
networks/nodes/overlays/apps/bindings/fabric).

**No tap-related content anywhere in this pipeline** (`docs/
WORK_ORDER_tap_eligibility.md` D3, Authority: user, 2026-07-28 --
supersedes the `tap_points[]` catalog + `_apply_tap_points` materialization
this module used to carry, LES-103's own finding). A twin config authors no
`tap:` flags and no `tap_points:` catalog; a leftover `tap_points:` key is
rejected LOUDLY (`TwinConfigShapeError`), never silently absorbed -- the
schema was `extra="allow"` when that trap was found and is `extra="forbid"`
since 2026-08-04 (LES-111). Capture is authored entirely in
a standalone tap plan against the deployed twin's published topology
(`axrange.o2control.taps` + `axrange.planner.tappoints`).

**The deploy gate** (`gate.py`, `docs/WORK_ORDER_deploy_gate.md`, 2026-09-04)
is the second gate in `_build_ir`, after the authored `when:` one: a cast
config's `observation:` blocks are compared against `DEPLOY_MIN_OCCUPANCY`
(an ordinary param, default 0.8) and sub-threshold nodes/segments are not
emitted at all. It changes NOTHING for a config carrying no `observation:`
block -- every shipped `twin-configs/` config expands byte-identically, which
is what `tests/test_twinconfig_catalog.py`'s `plan_id` pin proves (LES-126: a
changed `ir_hash` would orphan every standing deploy of these configs).

That single `tap_points:` check has since generalized into
`_reject_removed_keys` / `_REMOVED_*_KEYS` (2026-08-04, Authority: user),
which covers every key removed from the twin-config vocabulary after an
audit traced each to zero consumers — `segments[].purpose`,
`nodes[].identity_role`, `conduits[].policy`, and the top-level
`identity.groups`/`identity.ous`. Add to those tables, not to a fresh
one-off `if`, when a key is retired.
"""
from __future__ import annotations

import ipaddress
import logging
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ruamel.yaml import YAML

from axrange.twinconfig.errors import (
    DeployGateRefusalError,
    TwinConfigParamError,
    TwinConfigShapeError,
)
from axrange.twinconfig.gate import (
    DeployGateReport,
    GatedEntity,
    GateVerdict,
    check_observation,
    deploy_thresholds,
    exact_param_value,
    gate_verdict,
    normalize_declared_thresholds,
)
from axrange.twinconfig.params import resolve_params, substitute
from axrange.twinconfig.registry import load_twin_config_raw
from axrange.twinconfig.schema import (
    LINK_MODES,
    LINK_STATES,
    SOURCES,
    TwinConfig,
    TwinConfigConduit,
    TwinConfigLink,
    TwinConfigMember,
    TwinConfigNode,
    TwinConfigObservation,
    TwinConfigProvenance,
    TwinConfigSegment,
)

_log = logging.getLogger(__name__)

# Auto-assigned VLAN tags when the scenario-wide zone type is "vlan" (any
# segment asked for one -- docs/ADAPTER.md: the whole scenario shares ONE SDN
# zone type). Base + step keep tags readable and out of common real-world
# ranges without needing per-segment author bookkeeping.
_VLAN_BASE = 10
_VLAN_STEP = 10

#: `nodes[].kind` vocabulary (`docs/design/topology-layers.md` §2.2). A
#: `switch` is a device wired by `links[]`: never a gateway, no address on
#: its ports, and -- unlike a host or a router -- allowed to carry no
#: `image:` (then it deploys as nothing but the vnets its links carry).
NODE_KINDS: tuple[str, ...] = ("host", "router", "switch")

#: The IR schema version the expander declares. `1` for every config that
#: uses none of the device-tree fields (byte-identical IR, so `ir_hash`/
#: `plan_id` cannot move -- LES-126); `4` once a config carries a switch, a
#: link, a member override or a provenance field (IR_SCHEMA §1.5, 3->4).
_IR_VERSION_BASE = 1
_IR_VERSION_DEVICE_TREE = 4


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def _included(when: Optional[str], params: dict[str, Any]) -> bool:
    if when is None:
        return True
    if when not in params:
        raise TwinConfigShapeError(
            f"'when: {when}' references a parameter that is neither declared "
            "in twin_config.params nor overridden -- typo in the gate name?"
        )
    return _truthy(params[when])


class _IpAlloc:
    """Sequential host-address allocator over one segment's CIDR. Reserves
    take priority (an explicit `ip:`/`gateway:` is never handed out again);
    `next()` skips reserved addresses lazily rather than pre-materializing
    every host in the block."""

    def __init__(self, cidr: str):
        self.network = ipaddress.ip_network(cidr, strict=False)
        self._iter = self.network.hosts()
        self._used: set[str] = set()
        self._pinned: dict[str, str] = {}

    def reserve(self, ip: str, *, pinned_by: Optional[str] = None) -> None:
        """Take `ip` out of the pool. `pinned_by` (a leg's own `ip:`, named
        `node/segment`) also refuses a SECOND leg pinning the same address in
        this CIDR -- which spans an `isolated:` extension and its anchor, so a
        duplicate across the bridge is caught too. Gateway/`reserved:`
        reserves pass no `pinned_by` (a router leg pinned to its segment's
        gateway is the same address, legitimately)."""
        if pinned_by is not None:
            prior = self._pinned.setdefault(ip, pinned_by)
            if prior != pinned_by:
                raise TwinConfigShapeError(
                    f"{prior} and {pinned_by} both pin {ip} (cidr {self.network}) -- "
                    "one address, two NICs on one broadcast domain; pin a different "
                    "address on one of them, or drop its `ip:` to allocate"
                )
        self._used.add(ip)

    def first_host(self) -> str:
        return str(next(self.network.hosts()))

    def next(self) -> str:
        for host in self._iter:
            s = str(host)
            if s in self._used:
                continue
            self._used.add(s)
            return s
        raise TwinConfigShapeError(
            f"segment {self.network}: exhausted its address pool"
        )


@dataclass
class _Leg:
    segment: str
    ip: Optional[str] = None
    # A pure layer-2 leg (`ip: none` / `l2: true`): never allocated, emitted
    # with no `ip:` (docs/WORK_ORDER_corp_twin.md §10 P0).
    l2: bool = False


@dataclass
class _NodeInstance:
    node_id: str
    spec: TwinConfigNode
    legs: list[_Leg] = field(default_factory=list)


#: The `ip:` spelling of an L2 leg. YAML reads a bare `none` as the STRING
#: "none" (only `null`/`~` are null), which is what makes it usable here.
_L2_IP_LITERAL = "none"


def _legs_for(spec: TwinConfigNode, link_segments: tuple[str, ...] = ()) -> list[_Leg]:
    """A spec's legs: its declared `net:` / `nets[]`, then -- for a device
    the twin's `links[]` name -- every segment those links carry that it did
    not declare (`link_segments`, in link order). On a `kind: switch` a leg
    with no address is L2 (a switch port carries no address unless the
    author gives it one, the MS120's management SVI being the case); on a
    host a link-carried leg is an ordinary addressed attachment."""
    is_switch = spec.kind == "switch"
    legs: list[_Leg] = []
    if spec.nets:
        for j, leg in enumerate(spec.nets):
            ip_is_none = isinstance(leg.ip, str) and leg.ip.strip().lower() == _L2_IP_LITERAL
            if is_switch and leg.ip is None and leg.l2 is None:
                ip_is_none = True
            if leg.ip is None and "ip" in leg.model_fields_set and not leg.l2 and not is_switch:
                # `ip: null` / `ip: ~` reads like "no address" but is YAML null
                # -- the same as omitting `ip:`, i.e. ALLOCATE one. Say so.
                _log.warning(
                    "twin_config.nodes[%r].nets[%d] (segment %r): `ip: null` means "
                    "'allocate an address', not 'no address' -- for a pure L2 "
                    "(bridge-port) leg write `l2: true`",
                    spec.name, j, leg.segment,
                )
            if leg.l2 is False and ip_is_none:
                raise TwinConfigShapeError(
                    f"twin_config.nodes[{spec.name!r}].nets[{j}]: `l2: false` with "
                    "`ip: none` -- `ip: none` IS an L2 (address-less) leg; drop "
                    "`l2: false`, or give the leg an address"
                )
            l2 = bool(leg.l2) or ip_is_none
            if leg.l2 and leg.ip and not ip_is_none:
                raise TwinConfigShapeError(
                    f"twin_config.nodes[{spec.name!r}].nets[{j}]: `l2: true` with "
                    f"`ip: {leg.ip}` -- an L2 (bridge-port) leg carries no address; "
                    "drop one of the two"
                )
            if l2 and spec.kind == "router":
                raise TwinConfigShapeError(
                    f"twin_config.nodes[{spec.name!r}].nets[{j}]: a `kind: router` leg "
                    "cannot be L2 (`ip: none`/`l2: true`) -- a router leg IS its "
                    "segment's address; put the bridge on a `kind: host` device VM"
                )
            legs.append(_Leg(segment=leg.segment, ip=None if l2 else leg.ip, l2=l2))
    elif spec.net:
        legs.append(_Leg(segment=spec.net, l2=is_switch))
    declared = {leg.segment for leg in legs}
    for seg in link_segments:
        if seg in declared:
            continue
        declared.add(seg)
        legs.append(_Leg(segment=seg, l2=is_switch))
    if not legs:
        if is_switch:
            raise TwinConfigShapeError(
                f"node {spec.name!r} is a switch with no 'net:', 'nets:' and no "
                "link carrying a segment -- wire it in `links:` or attach it"
            )
        raise TwinConfigShapeError(
            f"node {spec.name!r} declares neither 'net:' nor 'nets:'"
        )
    return legs


def _check_provenance(what: str, entry: TwinConfigProvenance) -> None:
    """`source:` is one of SOURCES; `confidence:` is 0..1 and only on a
    cast's verdict (`discovered` / `unconfirmed`). Checked where the author
    writes it, like `listen:` (IR_SCHEMA §4's precedent); STR-220 repeats it
    on the expanded IR."""
    if entry.source is not None and entry.source not in SOURCES:
        raise TwinConfigShapeError(
            f"{what}: source {entry.source!r} is not one of {', '.join(SOURCES)}"
        )
    if entry.confidence is not None:
        try:
            c = float(entry.confidence)
        except (TypeError, ValueError):
            raise TwinConfigShapeError(
                f"{what}: confidence {entry.confidence!r} is not a number"
            ) from None
        if not 0.0 <= c <= 1.0:
            raise TwinConfigShapeError(f"{what}: confidence {c} is outside 0.0..1.0")
        if entry.source not in ("discovered", "unconfirmed"):
            raise TwinConfigShapeError(
                f"{what}: confidence is a cast's measure and belongs only on "
                f"`source: discovered` or `source: unconfirmed` (found "
                f"{entry.source or 'no source'})"
            )


def _emit_provenance(
    entry: dict[str, Any], src: TwinConfigProvenance, *, source: Optional[str] = None,
) -> None:
    """Copy the provenance fields onto an IR entry, only when set, so an
    entry without them is byte-identical to before. `source` (a member
    override's) wins over the group's."""
    if source or src.source:
        entry["source"] = source or src.source
    if src.confidence is not None:
        entry["confidence"] = float(src.confidence)
    if src.vantage:
        entry["vantage"] = src.vantage


def _check_node_shape(spec: TwinConfigNode) -> None:
    """Kind, image and provenance -- the per-node shape rules the device
    tree added (`docs/design/topology-layers.md` §2.2, §3.1, §5)."""
    what = f"twin_config.nodes[{spec.name!r}]"
    if spec.kind not in NODE_KINDS:
        raise TwinConfigShapeError(
            f"{what}: kind {spec.kind!r} is not one of {', '.join(NODE_KINDS)}"
        )
    if not spec.image and spec.kind != "switch":
        raise TwinConfigShapeError(
            f"{what}: a {spec.kind} needs an `image:` (only a `kind: switch` may "
            "omit it, and then deploys as nothing but its segments' vnets)"
        )
    _check_provenance(what, spec)
    for k, m in enumerate(spec.members or []):
        _check_provenance(f"{what}.members[{k}]", m)


def _apply_members(spec: TwinConfigNode, count: int) -> dict[int, TwinConfigMember]:
    """`members[]` by index, checked: every index an int in 1..count, no
    index twice (topology-layers §5)."""
    out: dict[int, TwinConfigMember] = {}
    for k, m in enumerate(spec.members or []):
        what = f"twin_config.nodes[{spec.name!r}].members[{k}]"
        try:
            idx = int(m.index)
        except (TypeError, ValueError):
            raise TwinConfigShapeError(f"{what}: index {m.index!r} is not a whole number") from None
        if not 1 <= idx <= count:
            raise TwinConfigShapeError(
                f"{what}: index {idx} is outside 1..{count} (the group's count)"
            )
        if idx in out:
            raise TwinConfigShapeError(f"{what}: index {idx} is overridden twice")
        out[idx] = m
    return out


def _link_segments(link: TwinConfigLink) -> list[str]:
    """The segments a link carries, native first, in authored order."""
    segs: list[str] = []
    for seg in ([link.native] if link.native else []) + list(link.vlans):
        if seg not in segs:
            segs.append(seg)
    return segs


def _link_label(link: TwinConfigLink) -> str:
    return f"{link.a.device}#{link.a.port} <-> {link.b.device}#{link.b.port}"


def _check_links(
    config_id: str, links: list[TwinConfigLink],
    specs_by_name: dict[str, TwinConfigNode], seg_names: set[str],
) -> None:
    """The twin-config shape of `links[]` (`docs/design/topology-layers.md`
    §2.3; the IR rules STR-210 / REF-270 / REF-280 / NET-120 / NET-130
    repeat it on the expanded IR): both ends name a node that is emitted
    and is a single device (never a `count > 1` group); a port is used once
    per device; the two ends differ; `mode` and `state` are in vocabulary;
    `native` only on a trunk; an access link carries exactly one segment;
    every segment named exists."""
    ports_used: dict[tuple[str, str], str] = {}
    for i, link in enumerate(links):
        what = f"twin_config.links[{i}] ({_link_label(link)})"
        if link.mode not in LINK_MODES:
            raise TwinConfigShapeError(
                f"{what}: mode {link.mode!r} is not one of {', '.join(LINK_MODES)}"
            )
        if link.state is not None and link.state not in LINK_STATES:
            raise TwinConfigShapeError(
                f"{what}: state {link.state!r} is not one of {', '.join(LINK_STATES)}"
            )
        if link.native and link.mode != "trunk":
            raise TwinConfigShapeError(f"{what}: `native:` is a trunk's field")
        if link.mode == "access" and len(link.vlans) != 1:
            raise TwinConfigShapeError(
                f"{what}: an access link carries exactly one segment in `vlans:` "
                f"(found {len(link.vlans)})"
            )
        if link.mode == "trunk" and not link.vlans and not link.native:
            raise TwinConfigShapeError(f"{what}: a trunk names the segments it carries")
        for seg in _link_segments(link):
            if seg not in seg_names:
                raise TwinConfigShapeError(f"{what}: unknown segment {seg!r}")
        if link.a.device == link.b.device:
            raise TwinConfigShapeError(f"{what}: both ends are {link.a.device!r}")
        for end in (link.a, link.b):
            if not end.port or not end.port.strip():
                raise TwinConfigShapeError(f"{what}: {end.device!r} has a blank port")
            spec = specs_by_name.get(end.device)
            if spec is None:
                raise TwinConfigShapeError(
                    f"{what}: {end.device!r} is not an emitted node (unknown, or "
                    "excluded by `when:`)"
                )
            try:
                is_group = int(spec.count) > 1
            except (TypeError, ValueError):
                is_group = False
            if is_group:
                raise TwinConfigShapeError(
                    f"{what}: {end.device!r} is a count > 1 group -- a link names "
                    "one device; attach a group to a segment with `net:`"
                )
            key = (end.device, end.port)
            if key in ports_used:
                raise TwinConfigShapeError(
                    f"{what}: port {end.port!r} on {end.device!r} is already used "
                    f"by {ports_used[key]}"
                )
            ports_used[key] = _link_label(link)
        _check_provenance(what, link)


def _declared_segments(spec: TwinConfigNode) -> list[str]:
    """The segment names a spec declares -- `_legs_for`'s name-only, tolerant
    sibling. `_legs_for` RAISES when a spec declares neither `net:` nor
    `nets:`, which is right on the emission path; this one is used for the
    PRE-gate occupancy census, where a spec that is about to be gated out
    must not fail the whole expansion on a shape the gate was going to
    discard anyway."""
    if spec.nets:
        return [leg.segment for leg in spec.nets]
    if spec.net:
        return [spec.net]
    return []


#: Transports a declared `listen:` entry may name. Closed on purpose: the
#: fidelity probe dispatches on exactly these two (`fidelity.probe.
#: method_for_port` has a TCP table and a UDP table and nothing else), so a
#: `proto: sctp` would publish a fact nothing can ever probe. Deliberately
#: NOT the application protocol -- `dns`/`http`/`krb` are DERIVED from
#: (port, transport) by the probe's own mapping, in ONE place (LES-037), and
#: an author who could write both would be able to declare `443/tcp` as
#: `ntp`.
LISTEN_TRANSPORTS: frozenset[str] = frozenset({"tcp", "udp"})

_MAX_PORT = 65535


def _check_listen(spec: TwinConfigNode) -> None:
    """Range-check + close the vocabulary of one node's declared `listen:`.

    Pydantic already refuses a missing/typeless `port:`/`proto:`
    (`TwinConfigListen`); this catches the two shapes it cannot -- an
    out-of-range port and an unknown transport -- and names
    `twin_config.nodes[...].listen[j].<field>` with the accepted set, so the
    message says which clause fired and what to write instead (LES-211).
    A duplicate `(port, proto)` is also refused: it would publish the same
    declared fact twice and double-count it in the scorecard's
    declared-vs-observed deltas. So is an EMPTY `listen: []` (review S6):
    every layer downstream treats ABSENT as "this twin makes no claim about
    this host" and there is no second meaning for "claims to serve nothing"
    -- an empty list silently collapses into the first, so it is refused
    where it is written instead (the `tap_points: []` precedent).
    """
    if spec.listen is not None and not spec.listen:
        raise TwinConfigShapeError(
            f"twin_config.nodes[{spec.name!r}].listen: is empty -- omit the "
            "key entirely to declare nothing (an empty list and an absent "
            "one would publish the same thing, so it is almost always a "
            "half-finished edit)"
        )
    seen: set[tuple[int, str]] = set()
    for j, entry in enumerate(spec.listen or []):
        where = f"twin_config.nodes[{spec.name!r}].listen[{j}]"
        try:
            port = int(entry.port)
        except (TypeError, ValueError):
            raise TwinConfigShapeError(
                f"{where}.port: {entry.port!r} is not a whole number"
            ) from None
        if not (1 <= port <= _MAX_PORT):
            raise TwinConfigShapeError(
                f"{where}.port: {port} is out of range (1-{_MAX_PORT})"
            )
        proto = str(entry.proto or "").strip().lower()
        if proto not in LISTEN_TRANSPORTS:
            raise TwinConfigShapeError(
                f"{where}.proto: {entry.proto!r} is not one of "
                f"{sorted(LISTEN_TRANSPORTS)} -- `proto:` is the TRANSPORT; "
                "the application protocol is derived from (port, transport) "
                "by the fidelity probe, never authored here"
            )
        if (port, proto) in seen:
            raise TwinConfigShapeError(
                f"{where}: duplicate declared listen {port}/{proto}"
            )
        seen.add((port, proto))


def _isolated_anchors(
    config_id: str, segments: list[TwinConfigSegment],
) -> dict[str, str]:
    """`{isolated segment name: anchor segment name}` for every `isolated:
    true` segment, after checking the one shape a twin config allows: an
    isolated segment's CIDR equals EXACTLY ONE non-isolated sibling's (its
    anchor -- where the gateway lives). Several extensions may share one
    anchor (both sides of a vPC-style dual link). Anything else -- no anchor,
    two anchors, a free-floating overlap, `isolated: false` spelled out -- is
    refused here, naming the segment, rather than reaching the validator as
    an unexplained NET-010 exemption (LES-211)."""
    anchors: dict[str, str] = {}
    plain = [s for s in segments if not s.isolated]
    for seg in segments:
        if seg.isolated is None:
            continue
        if seg.isolated is not True:
            raise TwinConfigShapeError(
                f"twin_config {config_id!r}: segment {seg.name!r}: `isolated:` only "
                "takes `true` (omit it for an ordinary segment)"
            )
        try:
            net = ipaddress.ip_network(seg.cidr, strict=False)
        except ValueError as e:
            raise TwinConfigShapeError(
                f"twin_config {config_id!r}: segment {seg.name!r}: bad cidr {seg.cidr!r}: {e}"
            ) from None
        same = []
        for other in plain:
            try:
                if ipaddress.ip_network(other.cidr, strict=False) == net:
                    same.append(other.name)
            except ValueError:
                continue
        if len(same) != 1:
            raise TwinConfigShapeError(
                f"twin_config {config_id!r}: segment {seg.name!r} is `isolated: true` "
                f"with cidr {seg.cidr}, which must equal exactly ONE non-isolated "
                f"segment's cidr (its anchor, where the gateway lives) -- found "
                f"{len(same)}{': ' + ', '.join(same) if same else ''}. An isolated "
                "segment is the same subnet on the far side of an L2 bridge, "
                "nothing else (docs/WORK_ORDER_corp_twin.md §10 P0)"
            )
        anchors[seg.name] = same[0]
    return anchors


def _find_joining_node(
    instances: list[_NodeInstance], seg_a: str, seg_b: str,
) -> Optional[_NodeInstance]:
    for inst in instances:
        segs = {leg.segment for leg in inst.legs}
        if seg_a in segs and seg_b in segs:
            return inst
    return None


# `_apply_tap_points` (tap-point materialization -- LES-103) REMOVED here
# (`docs/WORK_ORDER_tap_eligibility.md` D3, Authority: user, 2026-07-28) --
# the `tap_points[]` catalog it applied is retired, not relocated. A twin
# config authors NO capture intent at all any more; `_find_joining_node`
# (below) stays -- it is still needed by `_has_joining_node`'s UNRELATED
# conduit cross-check (a declared `conduits[]` entry with no implementing
# router is a real content bug, independent of tapping).


def _has_joining_node(instances: list[_NodeInstance], seg_a: str, seg_b: str) -> bool:
    return _find_joining_node(instances, seg_a, seg_b) is not None


def instance_ids(name: str, count: int) -> list[str]:
    """The node ids a spec fans out to (`ws` alone at `count: 1`, else
    `ws01`, `ws02`, ...). ONE derivation, shared by the emission loop, by
    the deploy gate's "which ids did this gated-out spec WOULD-have produced"
    lookup, and -- since 2026-09-05 -- by `axrange.fidelity.reference`, whose
    row 3 compares a cast group's declared installs against op rows keyed by
    these exact ids. PUBLIC for that third reason: two copies would drift the
    moment the naming convention did, and a copy that drifts by one character
    silently reads a correct twin as broken."""
    return [name if count == 1 else f"{name}{i:02d}" for i in range(1, count + 1)]


def _would_be_ids(spec: TwinConfigNode) -> list[str]:
    """`instance_ids` for a spec the deploy gate REMOVED, so it never
    reaches the emission loop's typed `count` validation. Lenient by design: a
    gated-out spec's bad `count:` is not an error (it is excluded, exactly as
    a `when:`-excluded spec's is), but its would-be ids are still needed to
    catch an `identity:`/`data:` entry pointing at one."""
    try:
        count = max(1, int(spec.count))
    except (ValueError, TypeError):
        count = 1
    return instance_ids(spec.name, count)


def _gated_reference_message(
    config_id: str, entry: str, node_id: str, row: GatedEntity,
) -> str:
    """The `identity:`/`data:` -> gated-out-node error text: entry, node,
    occupancy, effective threshold, remedy (work order §4.3 step 6). `row.
    reason` already names the occupancy, the window counts and the threshold
    it fell under."""
    # `exact_param_value`, not `%g`: a remedy is meant to be PASTED BACK, and
    # a 6-significant-digit rendering of a full-precision occupancy sits just
    # BELOW the value it names, so re-running at it refuses the node again.
    if row.metric == "role_confidence":
        confidence = row.role_confidence or 0.0
        remedy = (
            f"--param DEPLOY_MIN_ROLE_CONFIDENCE={exact_param_value(confidence)} "
            "(or lower)"
        )
    elif row.occupancy is not None:
        remedy = (
            f"--param DEPLOY_MIN_OCCUPANCY={exact_param_value(row.occupancy)} "
            "(or lower)"
        )
    else:
        remedy = "lower the threshold"

    # The reference names an INSTANCE id (`worker02`); the gate acted on the
    # SPEC (`worker`, all `count:` members at once). Name both when they
    # differ, or the operator goes looking for a `worker02:` entry to edit.
    spec = "" if node_id == row.name else f" (a member of the {row.name!r} spec)"
    return (
        f"twin_config {config_id!r}: {entry} references node {node_id!r}{spec}, "
        f"which the deploy gate removed -- {row.reason}. Remedy: {remedy}, move the "
        "entry onto a node that IS emitted, or drop the entry. Silently "
        "dropping an authored identity/dataset would change the twin's "
        "security posture with no signal, so it is refused instead."
    )


def _gated(
    kind: str, name: str, verdict: GateVerdict | str,
    obs: Optional[TwinConfigObservation], *, count: Optional[int] = None,
) -> GatedEntity:
    """One report row. `obs` is carried verbatim -- `role_confidence` stays
    `None` when it was absent, never defaulted (the gate's comparison is the
    only place absent reads as 0.0). A plain `str` verdict is a CASCADE drop
    (no clause decided it), so the row carries no `metric`."""
    reason = verdict.reason if isinstance(verdict, GateVerdict) else verdict
    metric = verdict.metric if isinstance(verdict, GateVerdict) else None
    return GatedEntity(
        kind=kind, name=name, reason=reason, metric=metric, count=count,
        occupancy=None if obs is None else obs.occupancy,
        windows_seen=None if obs is None else obs.windows_seen,
        windows_total=None if obs is None else obs.windows_total,
        role_confidence=None if obs is None else obs.role_confidence,
    )


# ---------------------------------------------------------------------------
# IR assembly
# ---------------------------------------------------------------------------
def _build_ir(
    tc: TwinConfig, params: dict[str, Any],
) -> tuple[dict[str, Any], DeployGateReport]:
    # Resolved FIRST, and unconditionally: a bad `--param
    # DEPLOY_MIN_OCCUPANCY=abc` must fail loudly even on a config carrying no
    # `observation:` block at all, rather than be silently ignored because
    # nothing happened to be gated (work order §4.1).
    thresholds = deploy_thresholds(params)
    report = DeployGateReport(config_id=tc.id, thresholds=thresholds)

    # ---- Gate 1: `when:` -- AUTHORED intent, always first -----------------
    # `when:` says "this variant of the config"; `observation:` says "this is
    # what was seen". An entry `when:` already excluded never reaches the
    # deploy gate at all -- not even its block's validation (work order §4.3
    # step 1 / §3.3's last sentence).
    segments = [s for s in tc.segments if _included(s.when, params)]
    if not segments:
        raise TwinConfigShapeError(
            f"twin_config {tc.id!r}: no segments left after 'when' gating"
        )
    seg_by_name = {s.name: s for s in segments}
    anchor_of = _isolated_anchors(tc.id, segments)

    node_specs = [n for n in tc.nodes if _included(n.when, params)]
    for spec in node_specs:
        _check_node_shape(spec)
    for seg in segments:
        _check_provenance(f"twin_config.segments[{seg.name!r}]", seg)

    # ---- Links (topology-layers §2.3): shape-checked against the `when:`-
    # included node set and the included segments. A link naming an excluded
    # node is a hard error like a conduit's missing router; a link naming a
    # node the DEPLOY GATE removes is dropped below with a printed reason.
    specs_by_name = {n.name: n for n in node_specs}
    _check_links(tc.id, tc.links, specs_by_name, set(seg_by_name))
    links: list[TwinConfigLink] = list(tc.links)

    # ---- Gate 2: the deploy gate (`docs/WORK_ORDER_deploy_gate.md`) -------
    # `active` is what makes this whole feature invisible to a config with no
    # observation data: no block anywhere => no gating, no warnings, no
    # `scenario.labels`, byte-identical IR (and therefore `ir_hash`/`plan_id`
    # -- LES-126).
    report.active = any(
        e.observation is not None for e in [*segments, *node_specs]
    )
    for seg in segments:
        if seg.observation is not None:
            check_observation(
                seg.observation, f"segment {seg.name!r}",
                role_confidence_allowed=False,
            )
    for spec in node_specs:
        if spec.observation is not None:
            check_observation(
                spec.observation, f"node {spec.name!r}", role_confidence_allowed=True,
            )
        # The DECLARED listen set (`docs/WORK_ORDER_protocol_faithful_
        # services.md` S1). Checked here, beside `observation:`, and for the
        # same reason: both are cast-emitted blocks whose shape must fail at
        # the author, not three layers downstream in a published inventory.
        # Also for the same SCOPE reason -- post-`when:`, so a variant this
        # expansion excluded is not validated (§4.3 step 1's rule, applied
        # consistently rather than re-decided per block).
        if spec.listen is not None:
            _check_listen(spec)

    # Which segments had a NON-ROUTER occupant BEFORE the gate ran. This is
    # what makes "the gate emptied it" a decidable question below: a segment
    # that never had a host on it in the first place (a router<->router
    # transit link, a WAN/uplink segment -- both already anticipated by the
    # IP allocator's own ">=2 router legs, no explicit gateway" transit case)
    # was not emptied BY the gate and must not be removed by its cascade.
    occupied_before = {
        seg
        for spec in node_specs if spec.kind != "router"
        for seg in _declared_segments(spec)
    }
    for link in links:
        for end in (link.a, link.b):
            if specs_by_name[end.device].kind != "router":
                occupied_before.update(_link_segments(link))

    kept_specs: list[TwinConfigNode] = []
    # (row, would-be node ids) per gated-out spec. Turned into a node_id ->
    # row map AFTER fan-out (M3): the ids a gated spec WOULD have produced can
    # legitimately belong to a DIFFERENT, emitted spec (an emitted `worker01`
    # beside a gated-out `count: 3` `worker`), and refusing an `identity:`/
    # `data:` reference to a node that is actually there would be a false
    # refusal with an un-actionable remedy.
    gated_rows: list[tuple[GatedEntity, list[str]]] = []
    for spec in node_specs:
        verdict = gate_verdict(
            spec.observation, thresholds, role_confidence_applies=True,
        )
        if verdict is None:
            kept_specs.append(spec)
            continue
        if spec.kind == "router":
            # UNCONDITIONAL exemption: a router is twin plumbing, and gating
            # one out disconnects surviving segments -- a threshold change
            # would become a topology break. Printed, never silent (§3.4).
            report.kept_routers.append(_gated(
                "router", spec.name,
                GateVerdict(verdict.metric, f"{verdict.reason}, but routers are "
                            "never gated (twin plumbing)"),
                spec.observation,
            ))
            kept_specs.append(spec)
            continue
        would_be = _would_be_ids(spec)
        row = _gated("node", spec.name, verdict, spec.observation, count=len(would_be))
        report.gated.append(row)
        gated_rows.append((row, would_be))
    node_specs = kept_specs
    kept_names = {spec.name for spec in node_specs}
    surviving_links: list[TwinConfigLink] = []
    for link in links:
        gone = [e.device for e in (link.a, link.b) if e.device not in kept_names]
        if gone:
            report.gated.append(GatedEntity(
                kind="link", name=_link_label(link),
                reason=f"device(s) {gone} not emitted",
            ))
            continue
        surviving_links.append(link)
    links = surviving_links
    # The segments each device's links carry, in link order: a switch gets an
    # L2 leg on each, a host or router an ordinary leg (`_legs_for`). A link
    # that is `down` or `blocked` deploys nothing, so it carries nothing here.
    link_segs_by_device: dict[str, list[str]] = {}
    for link in links:
        if (link.state or "up") != "up":
            continue
        for end in (link.a, link.b):
            bucket = link_segs_by_device.setdefault(end.device, [])
            for seg in _link_segments(link):
                if seg not in bucket:
                    bucket.append(seg)

    # ---- Fan out count>1 into concrete node instances ----------------------
    instances: list[_NodeInstance] = []
    member_role: dict[str, str] = {}
    member_source: dict[str, str] = {}
    for spec in node_specs:
        # `spec.count` is typed `Any` on the model (schema.py's docstring
        # explains why -- an UNSUBSTITUTED load may still hold a raw
        # `"${N:25}"` template string), so a bad value (a literal
        # `count: notanumber`, or an UNDECLARED param overridden with a
        # non-numeric string -- declared int params are already guarded by
        # `params._coerce_like`, undeclared ones aren't) reaches here as
        # whatever `int()` chokes on. Every other author-facing failure in
        # this module is a typed `TwinConfig*Error`; a bare `ValueError`/
        # `TypeError` escaping to the operator here would be the one
        # exception (review M1, docs/WORK_ORDER_twin_configs.md).
        try:
            count = int(spec.count)
        except (ValueError, TypeError) as e:
            raise TwinConfigShapeError(
                f"node {spec.name!r}: count {spec.count!r} is not a whole number"
            ) from e
        # 0 or negative silently "disables" the role by fanning out zero
        # instances -- a likely authoring typo, not a supported way to opt a
        # role out (review S3). None of the five starter configs need a
        # "count: 0 to disable" escape hatch; if a future config genuinely
        # does, that's a deliberate design call, not a default to fall into
        # silently.
        if count < 1:
            raise TwinConfigShapeError(
                f"node {spec.name!r}: count {count} must be >= 1 "
                "(0/negative silently 'disables' the node -- not supported)"
            )
        members = _apply_members(spec, count)
        link_segs = tuple(link_segs_by_device.get(spec.name, ()))
        for k, node_id in enumerate(instance_ids(spec.name, count), start=1):
            instances.append(_NodeInstance(node_id=node_id, spec=spec, legs=_legs_for(spec, link_segs)))
            m = members.get(k)
            if m is not None:
                if m.role:
                    member_role[node_id] = m.role
                if m.source:
                    member_source[node_id] = m.source

    seen_ids: dict[str, int] = {}
    for inst in instances:
        seen_ids[inst.node_id] = seen_ids.get(inst.node_id, 0) + 1
    dupes = sorted(nid for nid, n in seen_ids.items() if n > 1)
    if dupes:
        raise TwinConfigShapeError(
            f"twin_config {tc.id!r}: duplicate node id(s) after count fan-out: {dupes}"
        )

    # node_id -> the row explaining why it is NOT here, for the identity/data
    # cross-check further down (which must name the occupancy, the threshold
    # and the remedy). An id an EMITTED spec actually produced is never in
    # here, however many gated specs would also have claimed it (M3).
    emitted_ids = {inst.node_id for inst in instances}
    gated_by_node_id: dict[str, GatedEntity] = {}
    for row, would_be in gated_rows:
        for node_id in would_be:
            if node_id in emitted_ids:
                continue
            gated_by_node_id.setdefault(node_id, row)

    for inst in instances:
        for leg in inst.legs:
            if leg.segment not in seg_by_name:
                raise TwinConfigShapeError(
                    f"node {inst.node_id!r} references unknown segment {leg.segment!r}"
                )

    # ---- Gate 2, segment half (work order §4.3 step 3) -------------------
    # A segment is emitted when it survived `when:`, survives its own block,
    # and THE GATE DID NOT EMPTY IT.
    #
    # "Emptied by the gate" is `occupied_before and not occupied_after`, not
    # simply "no host on it now" (ruling, 2026-09-04). A segment whose legs
    # are ALL routers -- a router<->router transit link, a WAN uplink -- has
    # no host occupant by construction, so a bare emptiness test dropped it
    # at EVERY threshold including 0.0, took its routers with it as
    # "stranded", and turned a threshold change into exactly the topology
    # break the router exemption exists to prevent (§3.4). Measured on
    # `dmz-perimeter` (lost `internet` + the `edge` router's uplink) and on
    # `datacenter --param FABRIC=true` (lost `fab-link`, `fab-r1`, `fab-r2`)
    # with a single annotated host that gated nothing out. Such a segment now
    # leaves only via its OWN observation block.
    #
    # **A router leg is derived plumbing, not observation evidence: a router
    # FOLLOWS its segments** (ruling, 2026-09-04). A cast's single `gw` legs
    # every segment it invented and is exempt from the node gate, so counting
    # its legs would (a) make every segment permanently "occupied", so the
    # empty-segment cascade could never fire, and (b) turn every below-bar
    # segment into the hard error below -- which is exactly what refused
    # every real cast on the first cut of this feature.
    #
    # The empty-drop is scoped to `report.active` deliberately: with no gate
    # running there is nothing to cascade FROM, and silently dropping a
    # deliberately-empty authored segment would change that config's
    # `ir_hash` -- orphaning any standing deploy of it (LES-126). A
    # `when:`-emptied segment therefore behaves exactly as it always has.
    occupied_after = {
        leg.segment
        for inst in instances if inst.spec.kind != "router"
        for leg in inst.legs
    }
    kept_segments: list[TwinConfigSegment] = []
    dropped_segments: set[str] = set()
    for seg in segments:
        # A segment has no ROLE, so `DEPLOY_MIN_ROLE_CONFIDENCE` is not
        # applicable to it -- only its occupancy gates it.
        verdict = gate_verdict(
            seg.observation, thresholds, role_confidence_applies=False,
        )
        if verdict is not None:
            legged = sorted(
                inst.node_id for inst in instances
                if inst.spec.kind != "router"
                and any(leg.segment == seg.name for leg in inst.legs)
            )
            if legged:
                # A HOST would have a leg into nothing: a cast inconsistency
                # (the segment's block disagrees with its own members'), not
                # an operator choice. Routers are not this error -- they get
                # their leg pruned below.
                raise DeployGateRefusalError(
                    f"twin_config {tc.id!r}: segment {seg.name!r} is gated out "
                    f"({verdict.reason}) but emitted node(s) {legged} still have a leg "
                    "on it -- the segment's observation block disagrees with "
                    "its members'; fix the producer"
                )
            report.gated.append(_gated("segment", seg.name, verdict, seg.observation))
            dropped_segments.add(seg.name)
            continue
        emptied = seg.name in occupied_before and seg.name not in occupied_after
        # `report.active` is belt-and-braces here -- `occupied_before !=
        # occupied_after` already implies the gate removed something -- but it
        # states the invariant a blockless config depends on: no observation
        # data, no cascade, byte-identical IR (LES-126).
        if report.active and emptied:
            report.gated.append(_gated(
                "segment", seg.name, "every host with a leg on it was gated out",
                seg.observation,
            ))
            dropped_segments.add(seg.name)
            continue
        kept_segments.append(seg)

    # The prune MUST land with the drop, in the same pass: `allocs` below is
    # keyed on the SURVIVING segments, so a leg still pointing at a dropped
    # one raises a bare KeyError mid-allocation.
    if dropped_segments:
        for inst in instances:
            surviving = [leg for leg in inst.legs if leg.segment not in dropped_segments]
            if len(surviving) == len(inst.legs):
                continue
            for leg in inst.legs:
                if leg.segment in dropped_segments:
                    report.gated.append(GatedEntity(
                        kind="router-leg", name=f"{inst.node_id}:{leg.segment}",
                        reason=f"segment {leg.segment!r} is not emitted",
                    ))
            inst.legs = surviving
        # A router whose every segment left has nothing to attach to. Not a
        # gating decision (routers are never gated) -- a cascade consequence,
        # reported as loudly as one, because emitting a node with an empty
        # `nets:` would fail far downstream with an unrelated-looking error.
        pruned_links: list[TwinConfigLink] = []
        for link in links:
            lost = [seg for seg in _link_segments(link) if seg in dropped_segments]
            if lost:
                report.gated.append(GatedEntity(
                    kind="link", name=_link_label(link),
                    reason=f"segment(s) {lost} not emitted",
                ))
                continue
            pruned_links.append(link)
        links = pruned_links
        stranded = [inst for inst in instances if not inst.legs]
        for inst in stranded:
            report.gated.append(GatedEntity(
                kind="node", name=inst.node_id,
                reason="every segment it had a leg on was gated out",
            ))
        if stranded:
            instances = [inst for inst in instances if inst.legs]

    if not kept_segments:
        raise TwinConfigShapeError(
            f"twin_config {tc.id!r}: no segments left after the deploy gate "
            f"(DEPLOY_MIN_OCCUPANCY={thresholds.min_occupancy:g}, "
            f"DEPLOY_MIN_ROLE_CONFIDENCE={thresholds.min_role_confidence:g}) -- "
            "lower the threshold with --param DEPLOY_MIN_OCCUPANCY=<fraction>"
        )
    segments = kept_segments
    seg_by_name = {s.name: s for s in segments}

    # ---- IP allocation -------------------------------------------------
    # ONE allocator per CIDR, not per segment (2026-09-26, docs/
    # WORK_ORDER_corp_twin.md §10 P0): an `isolated:` extension segment
    # carries its anchor's subnet on a separate broadcast domain, and two
    # per-segment allocators would hand the same address out on both sides of
    # the bridge. Every config without `isolated:` has one segment per CIDR
    # (NET-010 refuses overlaps), so this is the same allocator it always had.
    by_cidr: dict[str, _IpAlloc] = {}
    allocs: dict[str, _IpAlloc] = {}
    for name, seg in seg_by_name.items():
        key = str(ipaddress.ip_network(seg.cidr, strict=False))
        allocs[name] = by_cidr.setdefault(key, _IpAlloc(seg.cidr))
    # `reserved:` (a VIP, anything that is no node's leg): out of the pool,
    # and no leg may pin it either.
    reserved_in: dict[str, set[str]] = {}
    for name, seg in seg_by_name.items():
        for ip in seg.reserved or []:
            try:
                addr = ipaddress.ip_address(str(ip))
            except ValueError:
                raise TwinConfigShapeError(
                    f"twin_config {tc.id!r}: segment {name!r}: reserved "
                    f"{ip!r} is not an IP address"
                ) from None
            if addr not in allocs[name].network:
                raise TwinConfigShapeError(
                    f"twin_config {tc.id!r}: segment {name!r}: reserved {ip} is "
                    f"outside its cidr {seg.cidr}"
                )
            key = str(allocs[name].network)
            reserved_in.setdefault(key, set()).add(str(addr))
            allocs[name].reserve(str(addr))
    for inst in instances:
        for leg in inst.legs:
            if leg.ip:
                if leg.ip in reserved_in.get(str(allocs[leg.segment].network), ()):
                    raise TwinConfigShapeError(
                        f"twin_config {tc.id!r}: node {inst.node_id!r} pins {leg.ip} on "
                        f"segment {leg.segment!r}, which a segment `reserved:` "
                        "claims (a VIP is no node's address)"
                    )
                allocs[leg.segment].reserve(
                    leg.ip, pinned_by=f"node {inst.node_id!r} (segment {leg.segment!r})"
                )

    gateway_for_segment: dict[str, str] = {}
    for name, seg in seg_by_name.items():
        if seg.gateway:
            gateway_for_segment[name] = seg.gateway
            allocs[name].reserve(seg.gateway)

    # A LONE router leg on a segment (no explicit gateway) gets the segment's
    # gateway address (network+1) -- the same convention every hand-written
    # target-environments/ archetype uses ("gateway = the router's LAN IP").
    # A segment with >=2 router legs and no explicit gateway is a router<->
    # router TRANSIT link (e.g. a fabric spine<->leaf p2p) -- there is no
    # "gateway" relationship there, so those legs fall through to the same
    # plain sequential allocation every other leg gets (below), instead of
    # every router on the link colliding on one auto-assigned ".1".
    router_legs_by_segment: dict[str, list[_Leg]] = {}
    for inst in instances:
        if inst.spec.kind != "router":
            continue
        for leg in inst.legs:
            router_legs_by_segment.setdefault(leg.segment, []).append(leg)

    for seg_name, legs in router_legs_by_segment.items():
        unset_legs = [leg for leg in legs if not leg.ip]
        if not unset_legs:
            continue
        explicit_gw = gateway_for_segment.get(seg_name)
        if explicit_gw is not None:
            # Bind the author-declared gateway to (at most) the first unset
            # router leg; any FURTHER unset router leg on this same segment
            # falls through to plain sequential allocation below.
            unset_legs[0].ip = explicit_gw
        elif len(legs) == 1:
            gw = allocs[seg_name].first_host()
            allocs[seg_name].reserve(gw)
            gateway_for_segment[seg_name] = gw
            unset_legs[0].ip = gw
        # else: >=2 router legs, no explicit gateway -- a transit segment;
        # no gateway auto-assignment, every leg gets a plain address below.

    # An `isolated:` extension inherits its anchor's gateway (the anchor is
    # where the router leg -- or the authored `gateway:` -- lives), unless it
    # authored its own. An anchor the deploy gate removed leaves nothing to
    # inherit; the extension then simply has no gateway, like any other
    # router-less segment.
    for name in seg_by_name:
        anchor = anchor_of.get(name)
        if anchor and name not in gateway_for_segment and anchor in gateway_for_segment:
            gateway_for_segment[name] = gateway_for_segment[anchor]

    for inst in instances:
        for leg in inst.legs:
            if not leg.ip and not leg.l2:
                leg.ip = allocs[leg.segment].next()

    # Tap-point materialization REMOVED here (`docs/
    # WORK_ORDER_tap_eligibility.md` D3) -- a twin config authors no
    # capture intent to materialize.

    # ---- Conduits (work order §4.3 step 5) -------------------------------
    # A conduit naming a segment THE DEPLOY GATE removed is dropped with a
    # printed reason, BEFORE the cross-check below: a conduit is documentation
    # over the node set, its referent is gone by construction, and failing the
    # hard cross-check on a set the operator deliberately chose would make the
    # threshold unturnable. Scoped to the segments THIS GATE removed (gated
    # by their own block, or cascade-dropped for having no emitted host) -- a
    # conduit naming a `when:`-excluded segment keeps failing the cross-check
    # exactly as it always has (that is not this work order's subject).
    gate_removed_segments = {g.name for g in report.gated if g.kind == "segment"}
    conduits: list[TwinConfigConduit] = []
    for c in tc.conduits:
        _check_provenance(f"twin_config.conduits[{c.from_}->{c.to}]", c)  # type: ignore[arg-type]
        dropped = [s for s in (c.from_, c.to) if s in gate_removed_segments]
        if dropped:
            report.gated.append(GatedEntity(
                kind="conduit", name=f"{c.from_}->{c.to}",
                reason=f"segment(s) {dropped} not emitted",
            ))
            continue
        conduits.append(c)

    # ---- Conduit cross-check (documentation-only field, but a declared
    # conduit with no implementing router is a real content bug) ---------
    for c in conduits:
        if not _has_joining_node(instances, c.from_, c.to):
            raise TwinConfigShapeError(
                f"twin_config {tc.id!r}: conduit {c.from_!r}->{c.to!r} has no "
                "node attached to BOTH segments -- add a multi-homed router "
                "leg on both, or drop the conduit"
            )

    # ---- identity:/data: references (work order §4.3 step 6, D2) ---------
    # These are AUTHORED facts, unlike segments and conduits which are DERIVED
    # from the node set -- silently dropping a local user or a crown-jewel
    # dataset changes the twin's security posture with no signal, the class
    # LES-089/LES-172 exist to close. So: a hard error naming the entry, the
    # node, its occupancy, the effective threshold and the remedy.
    if gated_by_node_id:
        for user in (tc.identity.users if tc.identity and tc.identity.users else []):
            if user.host and user.host in gated_by_node_id:
                raise DeployGateRefusalError(_gated_reference_message(
                    tc.id, f"identity user {user.id!r} (host: {user.host!r})",
                    user.host, gated_by_node_id[user.host],
                ))
        for dataset in tc.data:
            if dataset.on_role in gated_by_node_id:
                raise DeployGateRefusalError(_gated_reference_message(
                    tc.id, f"data {dataset.dataset!r} (on_role: {dataset.on_role!r})",
                    dataset.on_role, gated_by_node_id[dataset.on_role],
                ))

    # ---- Zone type + VLAN assignment (docs/ADAPTER.md: one zone type per
    # scenario) -----------------------------------------------------------
    is_vlan = any((s.zone_type or "simple") == "vlan" for s in segments)
    vlan_for: dict[str, int] = {}
    if is_vlan:
        for i, s in enumerate(segments):
            vlan_for[s.name] = _VLAN_BASE + _VLAN_STEP * i

    networks_ir: list[dict[str, Any]] = []
    for s in segments:
        entry: dict[str, Any] = {"id": s.name, "cidr": s.cidr}
        if is_vlan:
            entry["vlan"] = vlan_for[s.name]
        gw = gateway_for_segment.get(s.name) or s.gateway
        if gw:
            entry["gateway"] = gw
        # `tap:` NEVER emitted here (`docs/WORK_ORDER_tap_eligibility.md`
        # D3) -- the field no longer exists on `TwinConfigSegment` or in
        # IR_SCHEMA; a network entry carries only real topology.
        if s.ospf_area is not None:
            entry["ospf_area"] = s.ospf_area
        if s.vni is not None:
            entry["vni"] = s.vni
        if s.isolated:
            entry["isolated"] = True
        _emit_provenance(entry, s)
        networks_ir.append(entry)

    nodes_ir: list[dict[str, Any]] = []
    apps_ir: list[dict[str, Any]] = []
    bindings_ir: list[dict[str, Any]] = []
    for inst in instances:
        spec = inst.spec
        node_entry: dict[str, Any] = {"id": inst.node_id, "kind": spec.kind}
        # An imageless switch (topology-layers §2.5) emits no `image:` key at
        # all; the planner then emits no VM for it.
        if spec.image:
            node_entry["image"] = spec.image
        # `role:` is the OPTIONAL semantic BASELINE since the role/persona
        # split (`docs/WORK_ORDER_role_persona_tier.md` §4.5) -- `name:` is
        # what `node_id` fans out from now. Emitted only when the config
        # actually declares one: a bare-bone twin publishes no
        # `hosts[].role` at all and lets a traffic profile's `roles:` block
        # supply the whole map. (Every shipped config still sets `role:`, so
        # its expanded IR -- and therefore its `ir_hash`/`plan_id` -- is
        # unchanged by the split.)
        if member_role.get(inst.node_id) or spec.role:
            node_entry["role"] = member_role.get(inst.node_id) or spec.role
        _emit_provenance(node_entry, spec, source=member_source.get(inst.node_id))
        if spec.requires:
            node_entry["requires"] = list(spec.requires)
        if spec.resources:
            node_entry["resources"] = dict(spec.resources)
        # An L2 leg carries no `ip:` key at all (not `ip: null`) -- the IR's
        # NetRef reads absent and None the same, and an absent key is the
        # honest shape for "this NIC has no address".
        node_entry["nets"] = [
            {"net": leg.segment} if leg.l2 else {"net": leg.segment, "ip": leg.ip}
            for leg in inst.legs
        ]
        # `tap:` NEVER emitted here either (`docs/WORK_ORDER_tap_eligibility.md`
        # D3) -- see the segment loop's comment above.
        if spec.joins:
            node_entry["joins"] = spec.joins
        if spec.routing:
            node_entry["routing"] = dict(spec.routing)
        nodes_ir.append(node_entry)

        # An app is synthesized when the node declares install actions OR a
        # declared listen set: `App` is the only IR object carrying a
        # `listen:` field, so a serve-nothing-install-nothing host that
        # nonetheless DECLARES what it serves (the assertion-authored shape of
        # `docs/design/14-operator-assertions.md` §3) still needs one. An app
        # with no `install:` emits NO op at all (`pass1_emit._emit_apps` emits
        # per install action / script), so this adds nothing to the DAG -- it
        # only gives `App.listen` somewhere to live. `provides:` deliberately
        # does NOT trigger app synthesis on its own: that would be a behavior
        # change to existing content, and no shipped config authors it without
        # `install:`.
        if spec.install or spec.listen:
            app_id = f"{inst.node_id}-app"
            app_entry: dict[str, Any] = {"id": app_id}
            if spec.install:
                app_entry["install"] = list(spec.install)
            if spec.provides:
                app_entry["provides"] = list(spec.provides)
            if spec.listen:
                app_entry["listen"] = [
                    {"port": int(li.port), "proto": str(li.proto).lower()}
                    for li in spec.listen
                ]
            apps_ir.append(app_entry)
            bindings_ir.append({"app": app_id, "host": inst.node_id})

    links_ir: list[dict[str, Any]] = []
    for link in links:
        link_entry: dict[str, Any] = {
            "a": {"device": link.a.device, "port": link.a.port},
            "b": {"device": link.b.device, "port": link.b.port},
            "mode": link.mode,
        }
        if link.native:
            link_entry["native"] = link.native
        link_entry["vlans"] = list(link.vlans)
        if link.state:
            link_entry["state"] = link.state
        _emit_provenance(link_entry, link)
        links_ir.append(link_entry)

    uses_device_tree = bool(links_ir) or any(
        spec.kind == "switch" or spec.members or spec.source is not None
        for spec in node_specs
    ) or any(s.source is not None for s in segments)

    ir: dict[str, Any] = {
        "scenario_schema_version": (
            _IR_VERSION_DEVICE_TREE if uses_device_tree else _IR_VERSION_BASE
        ),
        "scenario": {
            "id": tc.id,
            "version": 1,
            "description": tc.description or f"Expanded from twin-config '{tc.id}'.",
        },
        "layers": {
            "infra": {"networks": networks_ir, "nodes": nodes_ir},
            "apps": apps_ir,
            "activity": [],
        },
        "bindings": bindings_ir,
    }

    # `links:` only when the config has any (byte-identical IR otherwise).
    if links_ir:
        ir["layers"]["infra"]["links"] = links_ir

    # Only emitted when the config states it: leaving the key ABSENT (not
    # `None`) preserves the IR's own "unset == ON" semantics byte-for-byte,
    # so every existing config's materialized IR -- and therefore its
    # `ir_hash`/`plan_id` -- is completely unchanged by this field existing.
    if tc.default_agents is not None:
        ir["scenario"]["default_agents"] = tc.default_agents

    overlays: dict[str, Any] = {}
    if tc.identity and tc.identity.users:
        identity_entries = []
        for u in tc.identity.users:
            entry = {"id": u.id, "type": "user"}
            if tc.identity.domain and not u.host:
                entry["domain"] = tc.identity.domain
            if u.groups:
                entry["groups"] = list(u.groups)
            if u.ou:
                entry["ou"] = u.ou
            if u.password:
                entry["password"] = u.password
            if u.spn:
                entry["spn"] = u.spn
            if u.asrep_roastable is not None:
                entry["asrep_roastable"] = u.asrep_roastable
            if u.acl:
                entry["acl"] = list(u.acl)
            if u.delegation:
                entry["delegation"] = u.delegation
            if u.host:
                entry["host"] = u.host
            identity_entries.append(entry)
        overlays["identity"] = identity_entries
    if tc.data:
        data_entries = []
        for d in tc.data:
            # `path:` is required by the planner's emission (pass1_emit's
            # data-overlay check), not just the validator -- default it
            # rather than force every twin-config author to spell out an
            # in-guest path for the common case.
            entry = {"id": d.dataset, "host": d.on_role, "path": d.path or f"/srv/{d.dataset}/data"}
            if d.classification:
                entry["classification"] = d.classification
            if d.crown_jewel is not None:
                entry["crown_jewel"] = d.crown_jewel
            if d.synthetic_size:
                entry["synthetic_size"] = d.synthetic_size
            data_entries.append(entry)
        overlays["data"] = data_entries
    if overlays:
        ir["overlays"] = overlays

    if tc.fabric is not None and _included(tc.fabric_when, params):
        ir["fabric"] = dict(tc.fabric)

    # The twin's containment posture (GAP-101). Emitted ONLY when the config
    # declares it -- the key stays ABSENT otherwise, exactly like
    # `default_agents`/`fabric` above, so every config that declares no
    # `egress:` expands byte-identically and its `ir_hash`/`plan_id` cannot
    # move (LES-126, LES-213). `exclude_none` keeps an author's omitted
    # sub-field out of the IR too, so `egress: {phase: measure}` hashes the
    # same whether or not the model grows another optional field later.
    if tc.egress is not None:
        ir["egress"] = tc.egress.model_dump(exclude_none=True)

    # Counted off `instances`, not `node_specs`: the cascade can strand a
    # router after the node gate has already run.
    report.emitted_node_specs = len({inst.spec.name for inst in instances})
    report.emitted_nodes = len(instances)
    report.emitted_segments = len(segments)

    # Threshold recovery (work order §6, D1 (c)): record the EFFECTIVE
    # thresholds in the IR itself, so they join `ir_hash` and a scorecard can
    # PROVE which threshold a standing twin was deployed at (re-expand,
    # re-hash, compare) instead of guessing from a `params:` block that cannot
    # see a `--param` override. `Scenario.labels` is free-form and unvalidated
    # (docs/IR_SCHEMA.md) -- no version-locked change. Emitted ONLY when the
    # gate ran, which is what keeps every blockless config byte-identical;
    # MERGED into any existing labels map, never overwriting it.
    labels = report.labels()
    if labels:
        merged = dict(ir["scenario"].get("labels") or {})
        merged.update(labels)
        ir["scenario"]["labels"] = merged

    return ir, report


# ---------------------------------------------------------------------------
# Removed-key rejection
#
# Every key here was traced to ZERO consumers before being removed from
# `twinconfig.schema` -- an author who writes one is expressing intent that
# reaches nothing. `TCBase` was `extra="allow"` when these were removed and
# would have silently absorbed and dropped them with no signal (LES-111); it
# is `extra="forbid"` since 2026-08-04, but that only yields pydantic's
# generic unknown-key error -- these entries reject them LOUDLY, with a
# message that names where the intent BELONGS instead.
#
# Checked on the RAW, pre-substitution dict: a stray key's mere PRESENCE is
# the whole problem, independent of what its value substitutes to.
# ---------------------------------------------------------------------------
_REMOVED_TOP_LEVEL_KEYS: dict[str, str] = {
    "tap_points": (
        "taps are authored in the tap plan (scope+target selector against "
        "the deployed twin's topology), not the twin config "
        "(docs/WORK_ORDER_tap_eligibility.md)"
    ),
}

# (section, key, remedy) -- `section` is a top-level list of mappings.
_REMOVED_SECTION_KEYS: tuple[tuple[str, str, str], ...] = (
    ("segments", "purpose", "it reached nothing; write segment documentation as a YAML comment"),
    ("nodes", "identity_role", "it reached nothing; identity is authored in identity.users[]"),
    ("nodes", "persona", (
        "it reached nothing; behavior belongs to the tfc tier -- author it in "
        "a traffic profile (twin-configs/<id>/tfc-*.yaml), which can bind a "
        "persona onto a DEPLOYED twin without redeploying the node "
        "(docs/WORK_ORDER_role_persona_tier.md)"
    )),
    ("conduits", "policy", (
        "it reached nothing; the conduit ENTRY is still load-bearing (it is "
        "cross-checked against an implementing router), but per-rule firewall "
        "policy is unbuilt (docs/SLICE_ROUTER.md) -- write it as a YAML comment"
    )),
)

# Keys removed from the `identity:` block itself. NOTE these are deliberately
# NOT checked inside `identity.users[]`, where `groups:`/`ou:` are real, wired
# fields (`_build_ir` reads `u.groups`/`u.ou`) -- only the top-level,
# whole-domain lists were inert.
_REMOVED_IDENTITY_KEYS: dict[str, str] = {
    "groups": "it reached nothing; author a user's groups per-user in identity.users[].groups",
    "ous": "it reached nothing; author a user's OU per-user in identity.users[].ou",
}


def _reject_removed_keys(tc_raw: dict[str, Any]) -> None:
    """Fail loudly on any key removed from the twin-config vocabulary."""
    for key, remedy in _REMOVED_TOP_LEVEL_KEYS.items():
        if key in tc_raw:
            raise TwinConfigShapeError(f"twin_config.{key}: is removed -- {remedy}")

    for section, key, remedy in _REMOVED_SECTION_KEYS:
        entries = tc_raw.get(section)
        if not isinstance(entries, list):
            continue
        for i, entry in enumerate(entries):
            if isinstance(entry, dict) and key in entry:
                raise TwinConfigShapeError(
                    f"twin_config.{section}[{i}].{key}: is removed -- {remedy}"
                )

    identity = tc_raw.get("identity")
    if isinstance(identity, dict):
        for key, remedy in _REMOVED_IDENTITY_KEYS.items():
            if key in identity:
                raise TwinConfigShapeError(f"twin_config.identity.{key}: is removed -- {remedy}")


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------
def expand_twin_config_raw(
    raw: dict[str, Any], *,
    param_overrides: Optional[dict[str, str]] = None,
    warnings: Optional[list[str]] = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Expand an already-loaded twin-config raw dict (`{"twin_config": {...}}`)
    into `(ir_dict, effective_params)`. Pure -- no filesystem access.

    `warnings` (review S1) is an optional, purely ADDITIVE out-parameter --
    a caller passes a mutable list and reads it back after the call; every
    caller that only unpacks the `(ir_dict, effective_params)` 2-tuple keeps
    working unchanged. It is REAL plumbing with real consumers: `cli.main.
    _resolve_scenario_or_config` prints what lands here, and the API returns
    it as `CreateDeployResponse.config_warnings`.

    Its producer since 2026-09-04 is the **deploy gate** (`gate.py`,
    `docs/WORK_ORDER_deploy_gate.md` §4.5): the effective thresholds, the
    counts, and one line per gated-out node/segment, dropped conduit and
    exempted router. Detail lines are capped at
    `gate.WARNING_DETAIL_CAP`; `axrange twin-config show <id>` prints the
    uncapped table. A config carrying no `observation:` block produces NO
    lines at all -- there is nothing to report, and an empty warnings list
    is still a meaningful answer.
    """
    ir_dict, effective_params, report = expand_twin_config_raw_with_gate(
        raw, param_overrides=param_overrides,
    )
    if warnings is not None:
        warnings.extend(report.warning_lines())
    return ir_dict, effective_params


def expand_twin_config_raw_with_gate(
    raw: dict[str, Any], *,
    param_overrides: Optional[dict[str, str]] = None,
) -> tuple[dict[str, Any], dict[str, Any], "DeployGateReport"]:
    """`expand_twin_config_raw` plus the structured deploy-gate report
    (`gate.DeployGateReport`) -- what `axrange twin-config show` renders as its
    uncapped `deploy gate:` section and what `gate.confirm_deploy_gate` reads.

    Separate entry point rather than a fourth out-parameter: the 2-tuple
    return of `expand_twin_config_raw` is what every existing caller unpacks,
    and the report is a typed object, not a list of strings the `warnings`
    channel could carry.
    """
    if not isinstance(raw, dict) or "twin_config" not in raw:
        raise TwinConfigShapeError("root must be a mapping with a top-level 'twin_config:' key")
    tc_raw = raw["twin_config"]
    if not isinstance(tc_raw, dict):
        raise TwinConfigShapeError("'twin_config:' must be a mapping")

    _reject_removed_keys(tc_raw)

    declared_params = tc_raw.get("params") or {}
    if not isinstance(declared_params, dict):
        raise TwinConfigShapeError("twin_config.params must be a mapping")
    # A config declaring `DEPLOY_MIN_ROLE_CONFIDENCE: 0` (an int -- the
    # natural way to write "off") would otherwise make `--param
    # DEPLOY_MIN_ROLE_CONFIDENCE=0.9` fail as "not a valid int", because
    # `params._coerce_like` types an override against the declared default.
    declared_params = normalize_declared_thresholds(declared_params)

    try:
        effective_params = resolve_params(declared_params, param_overrides)
    except TwinConfigParamError:
        raise

    # Substitute everything except the params block itself (already resolved
    # above -- substituting it too would be a harmless no-op today since it
    # holds only literal defaults, but keeping it untouched documents that
    # `params:` is the resolved-value table, not template body).
    substituted = {
        key: (value if key == "params" else substitute(value, effective_params))
        for key, value in tc_raw.items()
    }

    try:
        tc = TwinConfig.model_validate(substituted)
    except TwinConfigShapeError:
        raise
    except Exception as e:  # noqa: BLE001 -- reduced to one typed error
        raise TwinConfigShapeError(f"twin_config shape error: {e}") from e

    ir_dict, report = _build_ir(tc, effective_params)
    return ir_dict, effective_params, report


def expand_twin_config(
    config_id: str, *,
    catalog_dir: Optional[str | Path] = None,
    param_overrides: Optional[dict[str, str]] = None,
    warnings: Optional[list[str]] = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Look up `config_id` in the catalog dir and expand it. Returns
    `(ir_dict, effective_params)` -- the CLI/API materialize `ir_dict` to a
    file before handing it to the unchanged validate/plan/deploy path
    (`materialize_twin_config` below), matching `axrange.o2control.control.
    _write_temp_scenario`'s idiom (LES-037). `warnings`: see
    `expand_twin_config_raw`'s docstring."""
    raw, _path = load_twin_config_raw(
        config_id, catalog_dir=catalog_dir, warnings=warnings,
    )
    return expand_twin_config_raw(
        raw, param_overrides=param_overrides, warnings=warnings,
    )


def materialize_twin_config(
    config_id: str, *,
    catalog_dir: Optional[str | Path] = None,
    param_overrides: Optional[dict[str, str]] = None,
    out_path: Optional[str | Path] = None,
    warnings: Optional[list[str]] = None,
) -> Path:
    """Expand `config_id` and serialize the resulting IR dict to a YAML file
    -- either `out_path` (if given) or a throwaway tempfile, matching
    `axrange.o2control.control._write_temp_scenario`'s pattern of
    materializing a composed IR to a real file so the SAME `scenario_path:
    Path`-shaped `validate_full`/`build_plan_and_ctx` entry points work
    unchanged (LES-037) -- reused here rather than adding an in-memory-only
    entry point to either of those. `warnings`: see
    `expand_twin_config_raw`'s docstring.
    """
    ir_dict, _params = expand_twin_config(
        config_id, catalog_dir=catalog_dir, param_overrides=param_overrides,
        warnings=warnings,
    )
    if out_path is not None:
        target = Path(out_path)
        with target.open("w", encoding="utf-8") as f:
            YAML(typ="safe").dump(ir_dict, f)
        return target

    fd = tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", prefix=f"twin-config-{config_id}-", delete=False,
    )
    try:
        YAML(typ="safe").dump(ir_dict, fd)
    finally:
        fd.close()
    return Path(fd.name)
