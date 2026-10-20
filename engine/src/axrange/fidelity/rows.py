"""The eleven fidelity rows (`docs/design/13-golden-twin-fidelity.md` §3.1,
`docs/WORK_ORDER_fidelity_scorecard.md` §3).

**Pure** over `(ReferenceBundle, Reproduced, ProbeReport, GateInfo)` -- no
`StateStore`, no cluster, no filesystem beyond what `reference.py` already
read. `__init__.py` builds `Reproduced`/`GateInfo` from the state store and
hands them here, the way `planner/plandiff.py` is a pure diff over two
already-built plans (§5).

**Evidence-class discipline (`docs/design/14-operator-assertions.md` §2, ruled
2026-09-04).** Every row publishes its mix. A class is only ever *read*, never
*derived*: a fact the reference declares no class for goes to
`unclassified`, not to `observed`. The two exceptions are both RULINGS
rather than inferences and are labelled as such -- row 3's role-to-install
mapping is `imputed` (spec §8.2) and the cast's `image`/`resources`/`gw`
choices are `compiler` (the compiler's own sidecar says so in prose in
every cast description).

**Deployment scope (§3.4, ruled 2026-09-04).** Rows 1 and 2 judge against the
**deployable** set at the twin's recorded threshold, not against everything
the cast emitted. A host or segment the deploy gate kept out is reported
`observed, below deploy threshold` -- never failed. When the threshold cannot
be CONFIRMED by re-expansion (`twinconfig.confirm_deploy_gate`), rows 1 and 2
decline to score rather than score against a set they cannot reproduce.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import cached_property
from typing import Any, Optional

from axrange.fidelity import probe as probe_mod
from axrange.fidelity.reference import (
    PRESENT,
    ObservedNode,
    ReferenceBundle,
    TwinGroup,
)
from axrange.fidelity.schema import (
    ATTESTED,
    FAIL,
    NOT_ATTESTED,
    NOT_COMPUTED,
    PASS,
    STATE_BELOW_DEPLOY_THRESHOLD,
    Clause,
    EvidenceMix,
    Row,
)

# Spec §3.1's own bar ("every host observed in >= 80% of windows"), used when
# the twin carries no recorded deploy threshold of its own -- which is the
# standing CorpAPCN twin's state (it was cast under the old drop policy, so
# its config has no `observation:` block at all and its gate never ran).
SPEC_OCCUPANCY_BAR = 0.8

# Per-entity name lists published in `measured` are capped so one row cannot
# turn the document into a host dump; the COUNT beside them is never capped.
NAME_CAP = 50


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------
@dataclass
class AppliedInstall:
    """What `COMPLETED` op rows say actually ran on one host."""

    host_id: str
    install_actions: list[dict[str, Any]] = field(default_factory=list)
    script_names: list[str] = field(default_factory=list)


@dataclass
class Reproduced:
    """The REPRODUCED side: the twin as the state store records it.

    `hosts` is `planner.inventory.build_twin_inventory`'s roster, reused
    rather than re-scanned (LES-025/LES-037). `applied` is the ONLY thing
    this module reads op rows for, and it is filtered to `COMPLETED`
    (LES-113/LES-159) and `.get()`-defensive (LES-096) by the builder in
    `__init__.py`.
    """

    deploy_id: str
    scenario_id: Optional[str] = None
    plan_id: Optional[str] = None
    ir_hash: Optional[str] = None
    deploy_status: Optional[str] = None
    tier: Optional[str] = None
    hosts: list[Any] = field(default_factory=list)          # HostInfo
    applied: dict[str, AppliedInstall] = field(default_factory=dict)
    op_census: dict[str, dict[str, int]] = field(default_factory=dict)
    # host_id -> the `os_family` `wait-os-ready` recorded (`linux`/`windows`),
    # sourced exactly as `o2control/taps.py::_scan_te_inventory` does. NOT
    # `HostInfo.os`, which is an image->manifest lookup that resolves to
    # `None` whenever no manifest registry is passed -- this one is always
    # present for a host that actually booted, and it is what decides whether
    # a host can run the python3 probe at all.
    host_os_family: dict[str, str] = field(default_factory=dict)

    # NOTE the two cached properties below: `hosts` must not be REPLACED
    # after either is first read. Every construction path builds `hosts`
    # up-front, and the row functions are pure readers.
    @cached_property
    def host_ids(self) -> set[str]:
        return {h.host_id for h in self.hosts}

    @cached_property
    def deployed_host_ids(self) -> set[str]:
        """Hosts whose `clone-vm` actually COMPLETED.

        `inputs_resolved_json` is persisted at plan-INSERT time, so a deploy
        that failed at op 3 still publishes a full roster with `vmid=None`
        (`planner/inventory.py`'s "Deploy/host completeness" note). Scoring
        those as reproduced would let a twin that never stood up read as
        covered -- the silent-success trap this whole document exists to
        avoid. Rows 1/2/3/5 score against THIS set; `host_ids` stays
        available for "what did the plan intend".
        """
        return {
            h.host_id for h in self.hosts
            if (getattr(h, "vm_status", None) or "") == "completed"
        }

    @property
    def planned_not_deployed(self) -> list[str]:
        return sorted(self.host_ids - self.deployed_host_ids)

    @property
    def attached_segments(self) -> set[str]:
        """Every twin segment a DEPLOYED host is actually attached to --
        the reproduced side's own answer to "which segments exist", read
        from `attach-vm-to-vnet` rather than from the config that asked for
        them. `__wan__` is excluded via the typed `external` flag."""
        out: set[str] = set()
        for h in self.hosts:
            if h.host_id not in self.deployed_host_ids:
                continue
            for n in h.nets:
                if not n.external:
                    out.add(n.net_id)
        return out

    @property
    def router_segments(self) -> set[str]:
        """Every twin segment a DEPLOYED `kind: router` host has a leg on.
        The external `__wan__` leg is excluded explicitly rather than by
        assuming it never appears (`HostNet.external`'s own contract)."""
        out: set[str] = set()
        for h in self.hosts:
            if (h.kind or "").lower() != "router":
                continue
            if h.host_id not in self.deployed_host_ids:
                continue
            for n in h.nets:
                if not n.external:
                    out.add(n.net_id)
        return out

    @cached_property
    def declared_listen(self) -> dict[str, set[tuple[int, str]]]:
        """`host_id -> {(port, transport), ...}` for every DEPLOYED host that
        DECLARES a listen set (`docs/WORK_ORDER_protocol_faithful_services.md`
        S1 -- the IR's `App.listen`, published as `HostInfo.listen`).

        Hosts that declare nothing are ABSENT from this map: a falsy
        `HostInfo.listen` means "this twin makes no claim about what this
        host serves" (every twin deployed before the field existed says
        exactly that), which is not a claim to serve nothing and must not be
        scored as one. `None` is the only shape that reaches here in practice
        -- an empty `listen: []` is refused at the author
        (`twinconfig.expand._check_listen`) -- but the test is truthiness, so
        a hand-built `[]` degrades to the same honest "no claim" rather than
        creating a second, silent meaning. Restricted to `deployed_host_ids`
        for the same reason every other row is: a planned-but-never-cloned
        host's declaration describes nothing that exists.

        `hosts` is duck-typed (`HostInfo`, and the row tests' own stubs), so
        `getattr` guards the ATTRIBUTE's existence -- not its value.
        """
        out: dict[str, set[tuple[int, str]]] = {}
        deployed = self.deployed_host_ids
        for h in self.hosts:
            if h.host_id not in deployed:
                continue
            declared = getattr(h, "listen", None)
            if not declared:
                continue
            out[h.host_id] = {
                (int(li.port), str(li.proto or "tcp").lower())
                for li in declared
                if li.port is not None
            }
        return out

    def is_linux(self, host_id: str) -> bool:
        """True when `wait-os-ready` recorded a Linux guest. An UNKNOWN OS
        is not assumed Linux: a host with no recorded family cannot be
        proven able to run the probe, and guessing is how 34 of 65 targets
        got peered to Windows guests that then aborted the whole run."""
        return self.host_os_family.get(host_id, "") == "linux"

    def completed(self, op_name: str) -> int:
        return int(self.op_census.get(op_name, {}).get("completed", 0))


@dataclass
class GateInfo:
    """The deploy gate's CONFIRMED state for this twin (§3.4).

    `confirmed=False` means a re-expansion did not reproduce the deploy's
    `ir_hash`: the threshold is UNCONFIRMED and the gated-out set is not
    reproducible, so rows 1 and 2 decline to score. `gate_active=False` means
    the config carries no `observation:` block at all -- there IS no recorded
    threshold, which is a different and equally honest answer.
    """

    confirmed: bool = False
    config_id: Optional[str] = None
    gate_active: bool = False
    min_occupancy: Optional[float] = None
    min_role_confidence: Optional[float] = None
    emitted_nodes: Optional[int] = None
    gated_nodes: tuple[str, ...] = ()
    gated_segments: tuple[str, ...] = ()
    reason: Optional[str] = None

    @property
    def threshold(self) -> Optional[float]:
        """The occupancy bar rows 1/2 judge against, or `None` when the
        threshold is UNCONFIRMED. A gate that ran gives its own number; a
        config with no observation block gives the spec's 0.8 bar, which is
        the SAME bar by construction (§3.4)."""
        if not self.confirmed:
            return None
        if self.gate_active and self.min_occupancy is not None:
            return float(self.min_occupancy)
        return SPEC_OCCUPANCY_BAR

    @property
    def threshold_source(self) -> str:
        if not self.confirmed:
            return "UNCONFIRMED"
        if self.gate_active:
            return "deploy gate (confirmed by ir_hash re-expansion)"
        return (
            "spec §3.1 default -- the twin config carries no observation: "
            "block, so this twin has no recorded deploy threshold"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "confirmed": self.confirmed,
            "config_id": self.config_id,
            "gate_active": self.gate_active,
            "threshold": self.threshold,
            "threshold_source": self.threshold_source,
            "min_occupancy": self.min_occupancy,
            "min_role_confidence": self.min_role_confidence,
            "emitted_nodes": self.emitted_nodes,
            "gated_nodes": list(self.gated_nodes),
            "gated_segments": list(self.gated_segments),
            "reason": self.reason,
        }


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
def _listed(name: str, items: list[Any], *, sort: bool = True) -> dict[str, Any]:
    """A capped list PLUS its full count, as one `measured` fragment.

    A truncated list with no count beside it silently understates every
    finding it holds -- "3 unreproduced hosts" reads the same whether there
    were 3 or 300. The count is never capped; the list always is.
    """
    ordered = sorted(items) if sort else list(items)
    out: dict[str, Any] = {name: ordered[:NAME_CAP], f"{name}_count": len(ordered)}
    if len(ordered) > NAME_CAP:
        out[f"{name}_truncated"] = True
    return out


def _group_key(role: Optional[str], segment: Optional[str]) -> tuple[str, str]:
    return (role or "", segment or "")


def _deployed_groups(ref: ReferenceBundle, gate: GateInfo) -> list[TwinGroup]:
    """Cast groups minus the ones the deploy gate kept out."""
    gated = set(gate.gated_nodes)
    return [g for g in ref.groups if g.name not in gated]


def _deployed_segment_names(ref: ReferenceBundle, gate: GateInfo) -> set[str]:
    gated = set(gate.gated_segments)
    return {s.name for s in ref.twin_segments if s.name not in gated}


def _twin_side_mix(
    ref: ReferenceBundle, mix: EvidenceMix, *, prefixes: tuple[str, ...],
    fallback_class: str, fallback_n: int,
) -> None:
    """Add the TWIN side's evidence facts to `mix`.

    When a canonical provenance sidecar was consumed, the classes are READ
    from it (`ref.field_classes`, keyed by twin-config path). Otherwise the
    caller's `fallback_class`/`fallback_n` stands in, and `reference.
    evidence_source` says `re-derived from topology export` so a reader can
    tell the two apart without inferring it from the warnings list (M4).
    """
    declared = ref.classes_for_prefix(*prefixes)
    if declared:
        for cls in declared:
            mix.add(cls)
        return
    mix.add(fallback_class, fallback_n)


def _empty_denominator_row(
    number: int, dimension: str, category: str, channel: str, *,
    reason: str, measured: dict[str, Any],
) -> Row:
    """A gating row whose denominator is empty is `not-computed` WITH the
    denominator published -- never `pass`. "100% of 0" is the classic
    vacuous pass (LES-101), and it is exactly as wrong on row 1 or row 2 as
    row 5 already treats it."""
    return Row(
        row=number, dimension=dimension, category=category, channel=channel,
        verdict=NOT_COMPUTED, reason=reason, measured=measured,
    )


def _no_topology_row(
    number: int, dimension: str, category: str, channel: str,
) -> Row:
    return Row(
        row=number, dimension=dimension, category=category, channel=channel,
        verdict=NOT_COMPUTED,
        reason=(
            "the cast bundle carries no <id>.topology.json, so there is no "
            "observed side to score against"
        ),
    )


def _unconfirmed_row(
    number: int, dimension: str, category: str, channel: str, gate: GateInfo,
) -> Row:
    return Row(
        row=number, dimension=dimension, category=category, channel=channel,
        verdict=NOT_COMPUTED,
        reason=(
            "threshold: UNCONFIRMED -- re-expanding the twin config did not "
            "reproduce the deploy's ir_hash ("
            + (gate.reason or "no reason recorded")
            + "), so the deployable set this row judges against is not "
            "reproducible (docs/design/13-golden-twin-fidelity.md §3.4)"
        ),
        measured={"deploy_gate": gate.to_dict()},
    )


# ---------------------------------------------------------------------------
# Row 1 -- asset
# ---------------------------------------------------------------------------
def row_1_asset(
    ref: ReferenceBundle, rep: Reproduced, gate: GateInfo,
) -> Row:
    """Every observed placed host above the deploy threshold is covered by
    its `(role, segment)` group's emitted hosts -- **group granularity**,
    naming every high-occupancy host the cast dropped (spec §3.1 as amended
    2026-09-04).

    Group-granular is a FINDING, not a shortcut: the cast collapses observed
    hosts into `(role, segment)` groups with a `count:`, so no per-host
    identity survives and the rule cannot be evaluated host to host (§3.2).
    The named drop list is the useful output -- the population the twin
    lacks.
    """
    if ref.topology_export != PRESENT:
        return _no_topology_row(1, "asset", "structural", "wire+reference")
    threshold = gate.threshold
    if threshold is None:
        return _unconfirmed_row(1, "asset", "structural", "wire+reference", gate)

    windows = ref.windows
    placed = ref.placed_observed
    if not windows:
        return Row(
            row=1, dimension="asset", category="structural",
            channel="wire+reference", verdict=NOT_COMPUTED,
            reason=(
                "the topology export declares no `windows` count, so an "
                "occupancy threshold cannot be expressed in windows_seen"
            ),
            measured={"observed_placed": len(placed)},
        )

    bar = threshold * windows
    above = [n for n in placed if n.windows_seen >= bar]
    below = [n for n in placed if n.windows_seen < bar]

    if not above:
        return _empty_denominator_row(
            1, "asset", "structural", "wire+reference",
            reason=(
                f"no observed placed host reaches the threshold "
                f"(windows_seen >= {bar:g} of {windows} windows) -- there is "
                "nothing to check coverage of, and 'covered 0 of 0' is not a "
                "pass (LES-101)"
            ),
            measured={
                "windows": windows,
                "threshold_occupancy": threshold,
                "threshold_windows_seen": bar,
                "threshold_source": gate.threshold_source,
                "observed_placed": len(placed),
                "observed_above_threshold": 0,
                "observed_below_bar": len(below),
            },
        )

    groups = _deployed_groups(ref, gate)
    # M5: a group's emitted count is the number of its ids that ACTUALLY
    # cloned, not its declared `count:`. A planned-but-never-deployed host
    # is not a reproduced host.
    deployed = rep.deployed_host_ids
    emitted: dict[tuple[str, str], int] = {}
    for g in groups:
        if g.is_router:
            # A router is plumbing, never occupancy evidence -- it follows the
            # segments it is attached to (LES-210). Counting the cast's single
            # `gw`, which legs every segment, as a group member would make
            # every group look covered.
            continue
        # A multi-homed non-router group counts toward EACH of its segments.
        # That is the generous reading, and it is inside the imprecision this
        # row already publishes as `granularity: "group"`: an observed host
        # carries exactly one segment, so a dual-homed twin group could
        # legitimately answer for either -- counting it toward neither would
        # be plainly wrong. Every shipped cast's only multi-homed node is the
        # router, which is excluded above.
        live = sum(1 for host_id in g.node_ids if host_id in deployed)
        for seg in (g.segments or ()):
            emitted[_group_key(g.role, seg)] = (
                emitted.get(_group_key(g.role, seg), 0) + live
            )

    observed_by_group: dict[tuple[str, str], list[ObservedNode]] = {}
    role_less: list[ObservedNode] = []
    for n in above:
        if not n.role:
            role_less.append(n)
            continue
        observed_by_group.setdefault(
            _group_key(n.role, n.segment_name), []
        ).append(n)

    uncovered_groups: list[dict[str, Any]] = []
    unreproduced: list[str] = []
    for key, members in sorted(observed_by_group.items()):
        have = emitted.get(key, 0)
        if have >= len(members):
            continue
        uncovered_groups.append({
            "role": key[0], "segment": key[1],
            "observed_above_threshold": len(members), "emitted": have,
            "unreproduced": sorted(m.id for m in members[have:])[:NAME_CAP],
        })
        unreproduced.extend(m.id for m in members[have:])
    for n in role_less:
        unreproduced.append(n.id)

    # Two DIFFERENT things, deliberately not conflated:
    #   * `below` -- observed placed hosts under row 1's own bar. Row 1 does
    #     not evaluate them at all; they are a plain count, not a state.
    #   * the DEPLOY GATE's own gated-out node specs -- the only things that
    #     carry the per-fact state `observed, below deploy threshold` (§3.4).
    #     A twin whose config has no `observation:` block (the standing
    #     CorpAPCN twin, cast under the old drop policy) has NONE, however
    #     many observed hosts sit under the bar.
    below_named = [n.id for n in below]
    gated_named = list(gate.gated_nodes) if gate.gate_active else []

    mix = EvidenceMix()
    for n in above:
        mix.add(n.segment_evidence_class)
        mix.add(n.role_evidence_class)
    # The twin side: read from the sidecar when one was consumed, else the
    # cast's image/sizing/gw choices stand in as one `compiler` fact per
    # emitted group (spec §3.1 -- reported, not scored).
    _twin_side_mix(
        ref, mix, prefixes=("twin_config.nodes",),
        fallback_class="compiler", fallback_n=len(groups),
    )

    verdict = FAIL if unreproduced else PASS
    clauses = [
        Clause(
            clause="occupancy",
            verdict=verdict,
            detail=(
                f"{len(above) - len(unreproduced)} of {len(above)} observed "
                f"placed hosts at or above windows_seen >= {bar:g} are covered "
                f"by their (role, segment) group; role-less (and therefore "
                f"absent from the cast): {len(role_less)}"
            ),
        ),
        Clause(
            clause="os_family",
            verdict=NOT_COMPUTED,
            reason=(
                "the topology export carries no OS attribute; OS family is "
                "imputed from the inferred role and is not scored until the "
                "reference channel lands (spec §3.1, ruled 2026-09-04)"
            ),
            evidence_class="imputed",
            scored=False,
        ),
        Clause(
            clause="image_and_sizing",
            verdict=NOT_COMPUTED,
            reason=(
                "image and resources are catalog defaults chosen by the cast "
                "compiler, reported as `compiler` and not scored (spec §3.1)"
            ),
            evidence_class="compiler",
            scored=False,
        ),
    ]
    if gated_named:
        clauses.append(Clause(
            clause="below_deploy_threshold",
            verdict=NOT_COMPUTED,
            reason=(
                f"{len(gate.gated_nodes)} node spec(s) are "
                f"{STATE_BELOW_DEPLOY_THRESHOLD} at "
                f"DEPLOY_MIN_OCCUPANCY={threshold:g} -- reported, never failed "
                "(§3.4)"
            ),
            scored=False,
        ))
    elif below:
        clauses.append(Clause(
            clause="not_evaluated_below_bar",
            verdict=NOT_COMPUTED,
            reason=(
                f"{len(below)} observed placed host(s) are under row 1's own "
                f"bar (windows_seen < {bar:g}) and are not evaluated. They are "
                f"NOT {STATE_BELOW_DEPLOY_THRESHOLD}: that state means the "
                "DEPLOY GATE kept an entity out, and this twin's config "
                "carries no observation: block, so its gate never ran"
            ),
            scored=False,
        ))

    return Row(
        row=1, dimension="asset", category="structural",
        channel="wire+reference", verdict=verdict, granularity="group",
        evidence=mix,
        measured={
            "windows": windows,
            "threshold_occupancy": threshold,
            "threshold_windows_seen": bar,
            "threshold_source": gate.threshold_source,
            "observed_placed": len(placed),
            "observed_above_threshold": len(above),
            "observed_below_bar": len(below),
            **_listed("observed_below_bar_hosts", below_named),
            **_listed("below_deploy_threshold_nodes", gated_named),
            **_listed("role_less_above_threshold", [n.id for n in role_less]),
            **_listed("uncovered_groups", uncovered_groups, sort=False),
            **_listed("unreproduced_hosts", unreproduced),
            "unreproduced_host_count": len(unreproduced),
            "twin_groups_deployed": len(groups),
            "twin_hosts_deployed": len(deployed),
            **_listed("planned_not_deployed", rep.planned_not_deployed),
            "evidence_source": ref.evidence_source,
            "evidence_note": (
                "counts one segment-placement fact and one role fact per "
                "evaluated observed host, read from the export's own "
                "*_evidence_class fields, plus the TWIN side's facts -- read "
                "from the provenance sidecar's `fields` when one was consumed "
                "(`evidence_source: sidecar fields`), else one `compiler` fact "
                "per emitted group (image/sizing). A fact the reference "
                "declares no class for is `unclassified`, never promoted to "
                "`observed`."
            ),
            "deploy_gate": gate.to_dict(),
        },
        clauses=clauses,
    )


# ---------------------------------------------------------------------------
# Row 2 -- topology
# ---------------------------------------------------------------------------
def row_2_topology(
    ref: ReferenceBundle, rep: Reproduced, gate: GateInfo,
) -> Row:
    """Every observed segment maps to a deployed twin segment, and every
    observed local cross-segment pair has both ends present AND a router
    attached to both."""
    if ref.topology_export != PRESENT:
        return _no_topology_row(2, "topology", "structural", "wire")
    if gate.threshold is None:
        return _unconfirmed_row(2, "topology", "structural", "wire", gate)

    declared = {s.name for s in ref.twin_segments}
    gated_segments = set(gate.gated_segments)
    # S5: the reproduced side is the DEPLOYED twin, not the config that asked
    # for it. `attached_segments` is what `attach-vm-to-vnet` actually
    # recorded for hosts whose clone completed; the config's own segment list
    # is the intent. Score against the intersection and REPORT the gap, so a
    # segment that was declared and never materialized cannot pass row 2 by
    # existing on paper.
    attached = rep.attached_segments
    deployed = _deployed_segment_names(ref, gate) & attached
    declared_not_attached = sorted((declared - gated_segments) - attached)
    attached_not_declared = sorted(attached - declared)

    observed_names = [s.name for s in ref.observed_segments]
    missing_absent: list[str] = []      # no twin segment at all -> FAIL
    missing_below: list[str] = []       # gated out by the deploy -> reported
    for name in observed_names:
        if name in deployed:
            continue
        # `deployed` is `declared - gated - unattached`, so reaching here
        # with a DECLARED name means the deploy gate removed it (the only
        # other way out is `declared_not_attached`, a real failure reported
        # by its own clause below).
        (missing_below if name in gated_segments
         else missing_absent).append(name)

    # Twin-only segments (the synthetic `internet-sink`) are the compiler's
    # own additions -- reported as `compiler`, never counted against the
    # observed side.
    twin_only = sorted(declared - set(observed_names))

    # Observed LOCAL cross-segment pairs, named through the export's own
    # segments[] (the twin's CIDRs are relocated, so only NAMES join).
    by_id = {n.id: n for n in ref.observed_nodes}
    pairs: set[tuple[str, str]] = set()
    pair_evidence: dict[tuple[str, str], list[Optional[str]]] = {}
    for e in ref.observed_edges:
        s, d = by_id.get(e.src), by_id.get(e.dst)
        if s is None or d is None or not s.placed or not d.placed:
            continue
        sn, dn = s.segment_name, d.segment_name
        if not sn or not dn or sn == dn:
            continue
        key = (sn, dn) if sn < dn else (dn, sn)
        pairs.add(key)
        pair_evidence.setdefault(key, []).append(e.evidence_class)

    if not observed_names and not pairs:
        return _empty_denominator_row(
            2, "topology", "structural", "wire",
            reason=(
                "the topology export carries no observed segment and no "
                "observed cross-segment conversation -- there is nothing to "
                "map or route, and 'mapped 0 of 0' is not a pass (LES-101)"
            ),
            measured={
                "observed_segments": 0,
                "observed_cross_segment_pairs": 0,
                "twin_segments_declared": len(declared),
                "twin_segments_deployed": len(deployed),
            },
        )

    routed_segments = rep.router_segments
    unrouted: list[str] = []
    out_of_scope: list[str] = []
    broken: list[str] = []
    for a, b in sorted(pairs):
        if a not in deployed or b not in deployed:
            if a in missing_below or b in missing_below:
                out_of_scope.append(f"{a}|{b}")
            else:
                broken.append(f"{a}|{b}")
            continue
        if a not in routed_segments or b not in routed_segments:
            unrouted.append(f"{a}|{b}")

    mix = EvidenceMix()
    for seg in ref.observed_segments:
        # The export declares no per-segment evidence class; the class of a
        # segment's EXISTENCE is carried by its member nodes' own
        # `segment_evidence_class`, which is a read, not a derivation.
        classes = [
            n.segment_evidence_class for n in ref.observed_nodes
            if n.segment_name == seg.name and n.segment_evidence_class
        ]
        mix.add(classes[0] if classes else None)
    # ONE fact per PAIR, not per edge: the row's unit of judgement is the
    # cross-segment conversation, and counting its 40 constituent edges would
    # let a single chatty pair dominate the whole row's mix.
    for key in sorted(pairs):
        classes = [c for c in pair_evidence.get(key, []) if c]
        mix.add(classes[0] if classes else None)
    _twin_side_mix(
        ref, mix, prefixes=("twin_config.segments",),
        fallback_class="compiler", fallback_n=len(twin_only),
    )

    verdict = FAIL if (
        missing_absent or broken or unrouted or declared_not_attached
    ) else PASS
    clauses = [
        Clause(
            clause="segment_mapping",
            verdict=FAIL if missing_absent else PASS,
            detail=(
                f"{len(observed_names) - len(missing_absent) - len(missing_below)} "
                f"of {len(observed_names)} observed segments map to a deployed "
                f"twin segment; with none: {len(missing_absent)}; below the "
                f"deploy threshold: {len(missing_below)}"
            ),
        ),
        Clause(
            clause="cross_segment_routing",
            verdict=FAIL if (broken or unrouted) else PASS,
            detail=(
                f"{len(pairs) - len(broken) - len(unrouted) - len(out_of_scope)} "
                f"of {len(pairs)} observed local cross-segment pairs have both "
                f"ends deployed and a router leg on each; touching a segment "
                f"the twin lacks: {len(broken)}; with no router leg: "
                f"{len(unrouted)}; out of deployment scope: {len(out_of_scope)}"
            ),
        ),
    ]
    if missing_below or out_of_scope:
        clauses.append(Clause(
            clause="below_deploy_threshold",
            verdict=NOT_COMPUTED,
            reason=(
                f"{len(missing_below)} observed segment(s) and "
                f"{len(out_of_scope)} pair(s) are {_scope_state(gate)} "
                f"({gate.threshold_source}) -- reported, never failed (§3.4)"
            ),
            scored=False,
        ))
    if declared_not_attached or attached_not_declared:
        clauses.append(Clause(
            clause="declared_vs_attached",
            verdict=FAIL if declared_not_attached else PASS,
            detail=(
                f"{len(declared_not_attached)} twin segment(s) are declared "
                "but have no deployed host attached "
                f"({', '.join(declared_not_attached[:5])}); "
                f"{len(attached_not_declared)} attached segment(s) are not in "
                "the cast's segment list"
            ),
        ))
    if twin_only:
        clauses.append(Clause(
            clause="twin_only_segments",
            verdict=NOT_COMPUTED,
            reason=(
                f"{len(twin_only)} twin segment(s) have no observed counterpart "
                f"({', '.join(twin_only[:5])}) -- synthetic, chosen by the cast "
                "compiler; reported as `compiler`, not scored"
            ),
            evidence_class="compiler",
            scored=False,
        ))

    return Row(
        row=2, dimension="topology", category="structural", channel="wire",
        verdict=verdict, evidence=mix,
        measured={
            "observed_segments": len(observed_names),
            "twin_segments_declared": len(declared),
            "twin_segments_deployed": len(deployed),
            "twin_segments_attached": len(attached),
            **_listed("missing_segments", missing_absent),
            "missing_segment_count": len(missing_absent),
            **_listed("below_threshold_segments", missing_below),
            **_listed("twin_only_segments", twin_only),
            **_listed("declared_not_attached", declared_not_attached),
            **_listed("attached_not_declared", attached_not_declared),
            "observed_cross_segment_pairs": len(pairs),
            **_listed("pairs_touching_missing_segment", broken),
            **_listed("pairs_without_router_leg", unrouted),
            **_listed("pairs_out_of_deployment_scope", out_of_scope),
            "router_segments": len(routed_segments),
            **_listed("planned_not_deployed", rep.planned_not_deployed),
            "evidence_source": ref.evidence_source,
            "deploy_gate": gate.to_dict(),
        },
        clauses=clauses,
    )


# ---------------------------------------------------------------------------
# Row 3 -- configuration
# ---------------------------------------------------------------------------
def _script_basename(action: dict[str, Any]) -> Optional[str]:
    script = action.get("script")
    if not script:
        return None
    return str(script).replace("\\", "/").rsplit("/", 1)[-1]


def row_3_configuration(
    ref: ReferenceBundle, rep: Reproduced, gate: GateInfo,
) -> Row:
    """Declared-vs-applied: every placed host's declared install list is
    APPLIED (spec §8.2's relaxed v1 rule). The role-to-install MAPPING is
    reported `imputed` and never scored -- the catalog that produced it
    (`aximprint catalog/roles.yaml`) is not axRange's to read (§3.2).

    Two shapes this row must get right or a correct twin reads as broken:
    the cast's `count:` fan-out (11 groups become 22 hosts and 27 ops), and
    the fact that `provision-app`/`run-script` are CONDITIONAL ops by design
    (LES-095) -- host EXISTENCE therefore comes from the inventory, which is
    built from unconditional `clone-vm`.
    """
    groups = [g for g in _deployed_groups(ref, gate) if g.install]
    if not groups:
        return Row(
            row=3, dimension="configuration", category="structural",
            channel="reference", verdict=NOT_COMPUTED,
            reason=(
                "the cast declares no `install:` on any deployed node group, "
                "so there is no declared install list to check as applied"
            ),
            measured={
                "groups_with_install": 0,
                "provision_app_completed": rep.completed("provision-app"),
                "run_script_completed": rep.completed("run-script"),
            },
        )

    declared_hosts = 0
    declared_actions = 0
    missing_hosts: list[str] = []
    unapplied: list[dict[str, Any]] = []
    # M5: a host whose `clone-vm` never completed is not a reproduced host,
    # however complete its plan-time op rows look.
    deployed = rep.deployed_host_ids
    for g in groups:
        for host_id in g.node_ids:
            declared_hosts += 1
            if host_id not in deployed:
                missing_hosts.append(host_id)
                continue
            applied = rep.applied.get(host_id) or AppliedInstall(host_id=host_id)
            for action in g.install:
                declared_actions += 1
                basename = _script_basename(action)
                if basename is not None:
                    ok = any(
                        name.endswith(basename) for name in applied.script_names
                    )
                    kind = "script"
                else:
                    ok = action in applied.install_actions
                    kind = "install_action"
                if not ok:
                    unapplied.append({
                        "host": host_id, "kind": kind,
                        "declared": basename or action,
                    })

    mix = EvidenceMix()
    # Every declared install action is the role catalog's imputation (ruled
    # spec §8.2) -- one imputed fact per declared action, unless a canonical
    # sidecar declares the classes itself.
    _twin_side_mix(
        ref, mix, prefixes=("twin_config.nodes",),
        fallback_class="imputed", fallback_n=declared_actions,
    )

    verdict = FAIL if (missing_hosts or unapplied) else PASS
    return Row(
        row=3, dimension="configuration", category="structural",
        channel="reference", verdict=verdict, evidence=mix,
        measured={
            "groups_with_install": len(groups),
            "declared_hosts": declared_hosts,
            "declared_actions": declared_actions,
            **_listed("unapplied", unapplied, sort=False),
            "unapplied_count": len(unapplied),
            **_listed("hosts_missing_from_roster", missing_hosts),
            **_listed("planned_not_deployed", rep.planned_not_deployed),
            "provision_app_completed": rep.completed("provision-app"),
            "run_script_completed": rep.completed("run-script"),
            "evidence_source": ref.evidence_source,
            "conditional_ops_note": (
                "provision-app and run-script are CONDITIONAL ops -- they fire "
                "only for a host whose group declares install: (LES-095), so "
                "their op counts are not a coverage measure. Host existence "
                "comes from the inventory, built from unconditional clone-vm, "
                "and only hosts whose clone-vm COMPLETED are scored. Only "
                "COMPLETED op rows are read (LES-113/LES-159)."
            ),
        },
        clauses=[
            Clause(
                clause="declared_vs_applied",
                verdict=verdict,
                detail=(
                    f"{declared_actions - len(unapplied)} of {declared_actions} "
                    f"declared install actions across {declared_hosts} placed "
                    f"host(s) are applied"
                ),
            ),
            Clause(
                clause="role_to_install_mapping",
                verdict=NOT_COMPUTED,
                reason=(
                    "the role-to-install mapping lives in aximprint's role "
                    "catalog, which axRange must not import; it is reported "
                    "imputed and never scored (ruled spec §8.2)"
                ),
                evidence_class="imputed",
                scored=False,
            ),
        ],
    )


# ---------------------------------------------------------------------------
# Row 5 -- protocol
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ObservedListenFact:
    """One `(observed host, port, transport)` the export recorded, plus the
    twin candidates its `(role, segment)` group resolves to."""

    observed_node_id: str
    segment: Optional[str]
    role: Optional[str]
    port: int
    transport: str
    method: str
    candidates: tuple[str, ...] = ()
    # Why this fact is NOT in row 5's denominator, when it is not:
    # `"not a service"` / `"no protocol-aware probe"` (the ruled port
    # classification, `probe.classify_listen_port`) or
    # `"below deploy threshold"` (§3.4). Empty for a scored fact.
    excluded: str = ""
    exclusion_reason: str = ""

    @property
    def key(self) -> str:
        return f"{self.observed_node_id}:{self.port}/{self.transport}"


BELOW_THRESHOLD = "below deploy threshold"


def _scope_state(gate: "GateInfo") -> str:
    """The per-fact state name for "the deploy did not include this".

    `observed, below deploy threshold` (§3.4) when a gate actually ran;
    otherwise the honest alternative -- row 1's own spec bar did the
    selecting, and no deploy threshold was recorded to be below.
    """
    if gate.gate_active:
        return STATE_BELOW_DEPLOY_THRESHOLD
    return "observed, below row 1's occupancy bar"


def _denominator_note() -> str:
    """Row 5's published `denominator_note`, DERIVED from
    `probe._EXCLUSIONS` + `probe.EPHEMERAL_PORT_FLOOR` rather than restating
    them.

    A hand-written enumeration is a copy that silently goes stale: adding
    QUIC `443/udp` to the exclusion table left both of this module's prose
    lists claiming a smaller set, in a document whose whole purpose is that a
    reader can trust what it says was taken out (LES-101). Derived, the note
    cannot disagree with the classifier a reader would check it against.
    """
    by_cat: dict[str, list[str]] = {
        # The ephemeral floor is a RANGE, not a table row, so it is seeded
        # here rather than derived -- but it is seeded into the same category
        # bucket, so it can never be enumerated twice or under a category the
        # classifier does not actually use.
        probe_mod.EXCLUDED_NOT_A_SERVICE: [
            f"every port >= {probe_mod.EPHEMERAL_PORT_FLOOR} (the IANA "
            "dynamic/ephemeral floor)"
        ],
    }
    for (port, transport), (category, _reason) in sorted(
        probe_mod._EXCLUSIONS.items()
    ):
        by_cat.setdefault(category, []).append(f"{port}/{transport}")
    parts = [
        f"{', '.join(ports)} as '{category}'"
        for category, ports in sorted(by_cat.items())
    ]
    return (
        "the denominator is the observed SERVICE listens on deployed hosts "
        "at or above the deploy threshold. Excluded, each published above "
        "with its own reason and count: " + "; ".join(parts)
        + "; and every fact below the deploy threshold (reported, never "
        "failed). The exclusion set is derived from "
        "axrange.fidelity.probe._EXCLUSIONS, never restated -- see "
        "docs/PRODUCT_GAPS.md GAP-061..064 for why each one is in it."
    )


def observed_listen_facts(
    ref: ReferenceBundle, rep: Reproduced, gate: GateInfo,
    *, include_excluded: bool = False,
) -> list[ObservedListenFact]:
    """Row 5's DENOMINATOR: the observed **service** listens on deployed
    hosts at or above the deploy threshold, resolved to twin candidates.

    **Ruled 2026-09-05 (orchestrator, an implementation refinement of
    `docs/WORK_ORDER_fidelity_scorecard.md` §3.2's "the observed listen
    set"; the doc-writer records it in the work order and spec 13).** The
    denominator is *service* listens, minus three classes of port that are
    each classified and PUBLISHED with a reason rather than silently
    dropped (LES-101 cuts both ways -- an unexplained filter is as
    dishonest as a vacuous pass):

      * **not a service** -- a dynamic/ephemeral allocation seen once in the
        capture window is an artefact of the window, and scoring the twin for
        not reproducing `57086/tcp` is scoring noise.
      * **no protocol-aware probe** -- a port that can only ever return
        `inconclusive` blocks row 5 from passing for a reason that has
        nothing to do with the twin.
      * **facts below the deploy threshold** (§3.4) -- reported, NEVER
        failed. Without this filter a gated-out host's listen fact resolves
        to no candidate and reads as a reproduction failure, which is the
        exact inversion of the deployment-scope ruling.

    The first two are a SUMMARY of the categories, deliberately not a list
    of the ports in them: `probe._EXCLUSIONS` + `probe.EPHEMERAL_PORT_FLOOR`
    are the single source, `_denominator_note()` derives the published note
    from that table, and each `docs/PRODUCT_GAPS.md` entry (GAP-061..064)
    records why one port is in it. A prose list here would be a fourth copy
    to forget -- which is exactly what happened when QUIC 443/udp was added
    (S2 of this build's review).

    Publish the denominator beside the ratio; "100% of 0" is the classic
    vacuous pass (LES-101, §3.2).

    `include_excluded=True` returns the excluded facts too (each carrying
    its reason), for the row's own reporting; the probe composer takes the
    default and never probes one.

    `compute_rows` calls this ONCE with `include_excluded=True` and threads
    the result into rows 4 and 5 (which filter it themselves) rather than
    letting each of the three consumers re-derive it -- it is O(observed
    nodes x listens x groups), and on the standing CorpAPCN twin that is 190
    local entities against 62 groups, three times over.
    """
    groups = _deployed_groups(ref, gate)
    deployed = rep.deployed_host_ids
    by_key: dict[tuple[str, str], list[TwinGroup]] = {}
    for g in groups:
        if g.is_router:
            continue
        for seg in (g.segments or ()):
            by_key.setdefault(_group_key(g.role, seg), []).append(g)

    # §3.4's deployment scope, applied to row 5 as well as rows 1/2. A
    # `None` threshold means UNCONFIRMED -- no filter can be applied, and
    # applying a guessed one would be worse than none.
    threshold = gate.threshold
    bar = (threshold * ref.windows) if (threshold is not None and ref.windows) else None
    gated_segments = set(gate.gated_segments)

    facts: list[ObservedListenFact] = []
    for n in ref.placed_observed:
        if not n.listen_ports:
            continue
        below = bool(
            (bar is not None and n.windows_seen < bar)
            or (n.segment_name and n.segment_name in gated_segments)
        )
        candidates = tuple(
            host_id
            for g in by_key.get(_group_key(n.role, n.segment_name), [])
            for host_id in g.node_ids
            if host_id in deployed
        )
        for spec in n.listen_ports:
            parsed = probe_mod.parse_listen_port(spec)
            if parsed is None:
                continue
            port, transport = parsed
            method, excluded, reason = probe_mod.classify_listen_port(port, transport)
            if below:
                # The phrase must name the bar that actually produced the
                # exclusion: a twin whose config carries no `observation:`
                # block has no RECORDED deploy threshold, so calling its
                # sub-bar hosts "below deploy threshold" would contradict
                # the same document's own threshold_source two lines away
                # (the identical trap row 1 hit).
                excluded, reason = BELOW_THRESHOLD, (
                    f"observed host is {_scope_state(gate)} "
                    f"({gate.threshold_source}) -- reported, never failed"
                )
                method = ""
            if excluded and not include_excluded:
                continue
            facts.append(ObservedListenFact(
                observed_node_id=n.id, segment=n.segment_name, role=n.role,
                port=port, transport=transport,
                method=method or probe_mod.method_for_port(port, transport),
                candidates=candidates,
                excluded=excluded, exclusion_reason=reason,
            ))
    return facts


def declared_vs_observed_listen(
    ref: ReferenceBundle, rep: Reproduced, gate: GateInfo,
    *, facts: Optional[list["ObservedListenFact"]] = None,
) -> dict[str, Any]:
    """The twin's DECLARED listen set reported against the OBSERVED one --
    an INFORMATIONAL `measured` fragment shared by rows 4 and 5
    (`docs/WORK_ORDER_protocol_faithful_services.md` S1, D2(b),
    Authority: user).

    **It changes no verdict rule, by design.** A declaration is not evidence:
    row 4 measures what is bound (in-guest `ss -lnH -tu`) and row 5 measures
    what answers (a protocol-aware probe), and neither may be decided by what
    the twin config SAYS -- that is precisely the substitution this whole
    work order exists to remove. What the delta is FOR is diagnosis: an
    `observed_not_declared` port names a service the cast saw and the twin
    was never asked to serve (an authoring gap), and a
    `declared_not_observed` port names one the twin claims and the reference
    never saw (a compiler/threshold question, or an honest superset).

    **Both inputs can be absent, and each absence is reported as an absence
    -- never as a mismatch** (LES-089/LES-220: name the state after the
    mechanism that produced it, and never manufacture a finding out of a
    missing input). All four cells of the (declared x observed) matrix are
    handled explicitly:

      * **neither** -- zero counts, a note naming both absences;
      * **declared, no observed side** (no topology export in the reference
        bundle) -- zero counts. Without this guard EVERY declared port would
        come out as `declared_not_observed`, since the observed set it is
        being differenced against is empty by construction rather than by
        measurement: a manufactured finding, and the worst kind, because it
        scales with how thoroughly the twin was authored;
      * **observed, nothing declared** -- zero counts (every twin deployed
        before this field existed, including the standing CorpAPCN one);
      * **both** -- the real comparison.

    Every branch publishes the SAME key set (`_listed`'s `*_facts` /
    `*_count` included, empty where there is nothing to list): the scorecard
    document is meant to be diffed, so a `measured` block whose shape depends
    on its content is a diff full of key churn.
    """
    declaring = rep.declared_listen
    have_observed = ref.topology_export == PRESENT
    if not declaring or not have_observed:
        if not declaring and not have_observed:
            why = (
                "no deployed host declares a listen set (App.listen) AND the "
                "reference bundle carries no topology export"
            )
        elif not declaring:
            why = "no deployed host declares a listen set (App.listen)"
        else:
            why = (
                "the reference bundle carries no topology export, so no "
                "OBSERVED listen set was consumed"
            )
        return {
            "declared_listen_hosts": len(declaring),
            "declared_listen_facts": sum(len(v) for v in declaring.values()),
            "declared_not_observed": 0,
            "observed_not_declared": 0,
            **_listed("declared_not_observed_facts", [], sort=False),
            **_listed("observed_not_declared_facts", [], sort=False),
            "declared_note": (
                f"{why} -- the declared-vs-observed comparison has no input "
                "on one side and is reported as absent, never as a mismatch"
            ),
        }

    # Observed facts, resolved to the twin hosts that would answer them. The
    # EXCLUDED ones are included here deliberately: a 9997/tcp fact is out of
    # row 5's SCORING denominator, but "the twin declares 9997 and the
    # reference saw it" is still a true statement about the declaration, and
    # dropping it would make a declared port look unobserved.
    observed_by_host: dict[str, set[tuple[int, str]]] = {}
    if facts is None:
        facts = observed_listen_facts(ref, rep, gate, include_excluded=True)
    for f in facts:
        for host_id in f.candidates:
            if host_id in declaring:
                observed_by_host.setdefault(host_id, set()).add((f.port, f.transport))

    declared_not_observed: list[dict[str, Any]] = []
    observed_not_declared: list[dict[str, Any]] = []
    declared_total = 0
    for host_id, declared in sorted(declaring.items()):
        declared_total += len(declared)
        seen = observed_by_host.get(host_id, set())
        for port, proto in sorted(declared - seen):
            declared_not_observed.append({
                "host": host_id, "port": port, "transport": proto,
            })
        for port, proto in sorted(seen - declared):
            observed_not_declared.append({
                "host": host_id, "port": port, "transport": proto,
            })

    return {
        "declared_listen_hosts": len(declaring),
        "declared_listen_facts": declared_total,
        "declared_not_observed": len(declared_not_observed),
        "observed_not_declared": len(observed_not_declared),
        **_listed("declared_not_observed_facts", declared_not_observed, sort=False),
        **_listed("observed_not_declared_facts", observed_not_declared, sort=False),
        "declared_note": (
            "the DECLARED listen set (App.listen) is reported against the "
            "observed one for diagnosis only -- a declaration is not evidence "
            "and enters no verdict rule; row 4 (in-guest ss) and row 5 (a "
            "protocol-aware probe) remain the measurement of record"
        ),
    }


def row_5_protocol(
    ref: ReferenceBundle, rep: Reproduced, gate: GateInfo,
    report: Optional[probe_mod.ProbeReport],
    *, listen_facts: Optional[list["ObservedListenFact"]] = None,
) -> Row:
    """Each observed `(host, port, protocol)` returns a protocol-VALID
    response to a protocol-aware probe.

    A fact whose `(role, segment)` group resolves to no twin host is
    `unresolved` and fails without a probe -- nothing to ask. A fact probed
    with a non-protocol-aware method can never `pass`; if nothing failed but
    some facts are inconclusive, the ROW is `not-computed` (with the count),
    never a pass by default.
    """
    if ref.topology_export != PRESENT:
        return _no_topology_row(5, "protocol", "behavioral", "wire")

    # Precomputed by `compute_rows` (one derivation shared with row 4);
    # re-derived here when a caller invokes the row directly, as the tests do.
    scored_and_excluded = listen_facts if listen_facts is not None else (
        observed_listen_facts(ref, rep, gate, include_excluded=True)
    )
    facts = [f for f in scored_and_excluded if not f.excluded]
    excluded = [f for f in scored_and_excluded if f.excluded]
    listen_hosts = len({f.observed_node_id for f in facts})
    by_reason: dict[str, list[dict[str, Any]]] = {}
    for f in excluded:
        by_reason.setdefault(f.excluded, []).append({
            "fact": f.key, "port": f.port, "transport": f.transport,
            "reason": f.exclusion_reason,
        })
    denominator = {
        "observed_listen_facts": len(facts),
        "observed_listen_hosts": listen_hosts,
        "observed_placed_hosts": len(ref.placed_observed),
        **_listed("port_set", sorted({f"{f.port}/{f.transport}" for f in facts})),
        "excluded_total": len(excluded),
        "excluded_not_a_service": len(by_reason.get(probe_mod.EXCLUDED_NOT_A_SERVICE, [])),
        "excluded_no_probe": len(by_reason.get(probe_mod.EXCLUDED_NO_PROBE, [])),
        "excluded_below_deploy_threshold": len(by_reason.get(BELOW_THRESHOLD, [])),
        **_listed(
            "excluded_facts",
            [dict(e, category=cat) for cat, items in sorted(by_reason.items())
             for e in items],
            sort=False,
        ),
        # Informational only -- see `declared_vs_observed_listen`'s docstring
        # for why this can never move row 5's verdict.
        **declared_vs_observed_listen(
            ref, rep, gate, facts=scored_and_excluded,
        ),
        "denominator_note": _denominator_note(),
        "evidence_source": ref.evidence_source,
    }

    mix = EvidenceMix()
    # schema_version 1.3 carries no per-listen evidence class (the row-5
    # server half's proposed 1.4 `services[]` adds one, §8.5) -- so these are
    # `unclassified` rather than assumed observed.
    mix.add(None, len(facts))

    def _scope_clauses() -> list[Clause]:
        out: list[Clause] = []
        below = by_reason.get(BELOW_THRESHOLD, [])
        if below:
            out.append(Clause(
                clause="below_deploy_threshold",
                verdict=NOT_COMPUTED,
                reason=(
                    f"{len(below)} observed listen fact(s) are "
                    f"{_scope_state(gate)} "
                    f"({gate.threshold_source}) -- excluded from the "
                    "denominator, reported, never failed (§3.4)"
                ),
                scored=False,
            ))
        if excluded and len(below) < len(excluded):
            out.append(Clause(
                clause="excluded_ports",
                verdict=NOT_COMPUTED,
                reason=(
                    f"{denominator['excluded_not_a_service']} port(s) are not "
                    f"a service and {denominator['excluded_no_probe']} have no "
                    "protocol-aware probe -- excluded from the denominator "
                    "with a per-fact reason (ruled 2026-09-05)"
                ),
                scored=False,
            ))
        return out

    if not facts:
        return Row(
            row=5, dimension="protocol", category="behavioral", channel="wire",
            verdict=NOT_COMPUTED,
            reason=(
                "the observed listen set is empty after the denominator's own "
                f"exclusions ({len(excluded)} excluded) -- there is no (host, "
                "port, protocol) fact to probe, and '100% of 0' is not a pass "
                "(LES-101)"
            ),
            evidence=mix, measured=denominator, clauses=_scope_clauses(),
        )

    if report is None or not report.ran:
        return Row(
            row=5, dimension="protocol", category="behavioral", channel="wire",
            verdict=NOT_COMPUTED,
            reason=(
                "the in-guest probe did not run (--no-probe, or ?probe=false) "
                f"-- {len(facts)} observed listen fact(s) across {listen_hosts} "
                "host(s) are unmeasured"
            ),
            evidence=mix, measured=denominator, clauses=_scope_clauses(),
        )

    passed: list[str] = []
    failed: list[dict[str, Any]] = []
    inconclusive: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []   # no twin host at all -> FAILS
    unmeasured: list[dict[str, Any]] = []   # a twin host, but no result
    for f in facts:
        if not f.candidates:
            # A genuine REPRODUCTION gap, not a measurement gap: the twin
            # has no host in this (role, segment) group to answer at all.
            # Row 1 flags the same population from the other direction.
            unresolved.append({
                "fact": f.key, "role": f.role, "segment": f.segment,
                "reason": "no deployed twin host in this (role, segment) group",
            })
            continue
        outcomes = []
        for host_id in f.candidates:
            r = report.result_for(
                probe_mod.target_key(host_id, f.port, f.transport)
            )
            if r is not None:
                outcomes.append(r)
        if not outcomes:
            # A MEASUREMENT gap -- no peer on the segment, no recorded
            # address, or the probe itself errored. Distinct from a failure
            # (LES-089: "capture UNVERIFIED", never a silent skip AND never
            # a manufactured failure): it stops the row passing, it does not
            # assert the service is absent. `report.notes` carries the why.
            unmeasured.append({
                "fact": f.key, "role": f.role, "segment": f.segment,
                "reason": "no probe result recorded for any candidate host",
            })
        elif any(o.status == probe_mod.PASS for o in outcomes):
            passed.append(f.key)
        elif all(o.status == probe_mod.INCONCLUSIVE for o in outcomes):
            inconclusive.append({
                "fact": f.key, "method": f.method,
                "reason": outcomes[0].detail or "not protocol-aware",
            })
        else:
            failed.append({
                "fact": f.key, "method": f.method,
                "candidates": list(f.candidates)[:5],
                "reason": next(
                    (o.detail for o in outcomes if o.status == probe_mod.FAIL),
                    "no protocol-valid response",
                ),
            })

    measured = dict(denominator)
    measured.update({
        "passed": len(passed),
        "failed": len(failed),
        "inconclusive": len(inconclusive),
        "unresolved": len(unresolved),
        "unmeasured": len(unmeasured),
        **_listed("failures", failed, sort=False),
        **_listed("inconclusive_facts", inconclusive, sort=False),
        **_listed("unresolved_facts", unresolved, sort=False),
        **_listed("unmeasured_facts", unmeasured, sort=False),
        **_listed("probe_errors", list(report.errors), sort=False),
        **_listed("probe_notes", list(report.notes), sort=False),
    })

    if failed or unresolved:
        verdict, reason = FAIL, None
    elif inconclusive or unmeasured:
        verdict = NOT_COMPUTED
        parts = []
        if inconclusive:
            parts.append(
                f"{len(inconclusive)} have no protocol-aware probe method, so "
                "a protocol-valid response cannot be asserted for them -- a "
                "bare TCP connect is never a pass"
            )
        if unmeasured:
            parts.append(
                f"{len(unmeasured)} could not be measured at all (no peer on "
                "the segment, no recorded address, or a probe error) -- see "
                "`probe_notes`/`probe_errors`"
            )
        reason = (
            f"of {len(facts)} observed listen fact(s): " + "; ".join(parts)
        )
    else:
        verdict, reason = PASS, None

    return Row(
        row=5, dimension="protocol", category="behavioral", channel="wire",
        verdict=verdict, reason=reason, evidence=mix, measured=measured,
        clauses=_scope_clauses() + [Clause(
            clause="protocol_valid_response",
            verdict=verdict,
            detail=(
                f"{len(passed)} of {len(facts)} observed listen facts answered "
                "with a protocol-valid response"
            ),
            reason=reason,
        )],
    )


# ---------------------------------------------------------------------------
# Rows 4, 6, 7 -- deferred; 8, 9, 10 -- provisional; 11 -- attested
# ---------------------------------------------------------------------------
def row_4_state(
    ref: ReferenceBundle, rep: Reproduced, gate: GateInfo,
    report: Optional[probe_mod.ProbeReport],
    *, listen_facts: Optional[list["ObservedListenFact"]] = None,
) -> Row:
    """`ss -lnH -tu` in every probed guest. DEFERRED for the first golden
    (spec §8.1's "ignore them for now"); it runs behind `--include-deferred`
    and is REPORTED, never entering the gate set.

    Takes `(ref, rep, gate)` like row 5 since 2026-09-05 so it can publish
    the DECLARED-vs-observed listen delta beside the in-guest one (`docs/
    WORK_ORDER_protocol_faithful_services.md` S1). Informational: the row's
    verdict is `not-computed` either way while it is deferred, and the
    declared set enters no rule when it stops being (`declared_vs_observed_
    listen`'s docstring)."""
    declared = declared_vs_observed_listen(ref, rep, gate, facts=listen_facts)
    if report is None or not report.listens_ran:
        return Row(
            row=4, dimension="state", category="operational",
            channel="reference+in-guest", verdict=NOT_COMPUTED,
            reason=(
                "deferred for the first golden (spec §3.3/§8.1); run it with "
                "--include-deferred, where it is reported and never gates"
            ),
            measured=dict(declared),
        )
    hosts = sorted(report.listens)
    total = sum(len(v) for v in report.listens.values())
    return Row(
        row=4, dimension="state", category="operational",
        channel="reference+in-guest", verdict=NOT_COMPUTED,
        reason=(
            "reported only -- row 4 is deferred for the first golden and never "
            "enters the gate set, whatever the in-guest listen set says"
        ),
        measured={
            "hosts_probed": len(hosts),
            "listen_facts": total,
            "listens": {h: report.listens[h] for h in hosts[:NAME_CAP]},
            "listens_truncated": len(hosts) > NAME_CAP,
            **declared,
        },
    )


def row_6_performance() -> Row:
    return Row(
        row=6, dimension="performance", category="operational", channel="wire",
        verdict=NOT_COMPUTED,
        reason=(
            "deferred by the category rule (spec §3.3) AND needs the "
            "fingerprint seam file (§8.2), which no cast bundle carries yet; "
            "its tolerance stays provisional until spec v1.1 (spec §8.3)"
        ),
    )


def row_7_security() -> Row:
    return Row(
        row=7, dimension="security", category="operational",
        channel="reference+wire", verdict=NOT_COMPUTED,
        reason=(
            "deferred for the first golden (spec §8.1): the reference channel "
            "(LDIF directory export) is unbuilt, and the firewall clause is a "
            "recorded reproduction gap that cannot pass in v1 regardless of "
            "input quality (spec §3.2)"
        ),
    )


_FINGERPRINT_REASON = (
    "the fingerprint seam file (<id>.fingerprint.json, "
    "docs/WORK_ORDER_fidelity_scorecard.md §8.2) is absent -- rows 8/9/10 need "
    "BOTH sides' axis dicts plus a recapture of the deployed twin, and axRange "
    "cannot import aximprint's shape_fingerprint.py to compute one"
)


def row_8_dependency() -> Row:
    return Row(
        row=8, dimension="dependency", category="structural", channel="wire",
        verdict=NOT_COMPUTED, reason=_FINGERPRINT_REASON,
    )


def row_9_temporal() -> Row:
    return Row(
        row=9, dimension="temporal", category="behavioral", channel="wire",
        verdict=NOT_COMPUTED,
        reason=(
            _FINGERPRINT_REASON
            + "; row 9 additionally needs an hourly activity envelope over a "
            ">= 24 h capture (§8.3)"
        ),
    )


def row_10_behavioral() -> Row:
    return Row(
        row=10, dimension="behavioral", category="behavioral", channel="wire",
        verdict=NOT_COMPUTED, reason=_FINGERPRINT_REASON,
    )


def row_11_visual(attestation: Optional[dict[str, Any]]) -> Row:
    """Operator-attested, never inferred (§3.2). Inferring a visual verdict
    from the existence of two React components is imputed-as-observed."""
    if not attestation:
        return Row(
            row=11, dimension="visual", category="—", channel="console",
            verdict=NOT_ATTESTED,
            measured={
                "how": (
                    "compare the Console's ImprintTopologyPanel against "
                    "DeployedTopology side by side, then record the "
                    "attestation with --attest-visual \"<note>\""
                ),
            },
        )
    mix = EvidenceMix()
    mix.add("asserted")
    return Row(
        row=11, dimension="visual", category="—", channel="console",
        verdict=ATTESTED, evidence=mix, measured={"attestation": attestation},
        clauses=[Clause(
            clause="side_by_side",
            verdict=ATTESTED,
            detail=str(attestation.get("note") or ""),
            evidence_class="asserted",
            scored=False,
        )],
    )


def compute_rows(
    ref: ReferenceBundle, rep: Reproduced, gate: GateInfo, *,
    report: Optional[probe_mod.ProbeReport] = None,
    attestation: Optional[dict[str, Any]] = None,
) -> list[Row]:
    """All eleven rows, in row order. Every row is ALWAYS present -- a row
    that could not be computed says so with a reason, it is never omitted
    (`rollup_verdict` treats a missing gating row as not-computed, but a
    silently absent row is still the shape a reader would misread)."""
    # ONE derivation of the observed listen facts, shared by rows 4 and 5
    # (each filters it for its own purpose). Skipped entirely when there is
    # no observed side to derive from -- `observed_listen_facts` would return
    # `[]` anyway, and both rows treat `None` as "derive it yourself", which
    # is what a direct caller (the tests) gets.
    listen_facts = (
        observed_listen_facts(ref, rep, gate, include_excluded=True)
        if ref.topology_export == PRESENT else None
    )
    return [
        row_1_asset(ref, rep, gate),
        row_2_topology(ref, rep, gate),
        row_3_configuration(ref, rep, gate),
        row_4_state(ref, rep, gate, report, listen_facts=listen_facts),
        row_5_protocol(ref, rep, gate, report, listen_facts=listen_facts),
        row_6_performance(),
        row_7_security(),
        row_8_dependency(),
        row_9_temporal(),
        row_10_behavioral(),
        row_11_visual(attestation),
    ]
