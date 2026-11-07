"""PIT -> axRange `twin_config` + `tfc` + `tap` (`docs/WORK_ORDER_network_ir.md` build step 4).

`WORK_ORDER_synth_v1.md` phase 3b. Reads the **IR**, never the belief store directly — that
indirection is the whole point of D1's reversal: axRange's closed vocabulary no longer dictates
what this project's schema looks like, and both sides can move independently.

**Four constraints this had to satisfy, all of them findings from earlier steps rather than
choices made here:**

  * **Nodes come from the PLACED set** — `locality == 'local'` AND a segment — never from every
    local entity. Phase 1 measured 117 local hosts on nothing provisionable (IPv6 link-local,
    multicast); emitting those would put 117 duplicate hosts in the twin, each of them already
    present under its IPv4 address.
  * **Provenance goes in a SIDECAR.** axRange's `twin_config` is `extra="forbid"`, so the
    `{value, provenance}` wrappers the v0 stub emits inline are *rejected by their schema*. The
    compiled YAML is clean and `<name>.provenance.json` carries the per-field evidence beside it.
  * **`compiler` appears here and nowhere upstream.** It is not an IR evidence class
    (`ir/schema.py::EVIDENCE_CLASSES`) precisely because it is what a COMPILER does when a catalog
    supplies what discovery never observed. This module is where it legitimately enters.
    **Renamed from `defaulted` on 2026-09-04** (`docs/WORK_ORDER_reference_ingest.md` D5; axRange
    `docs/design/14-operator-assertions.md` §2): the category was always right and the name always
    misleading — "defaulted" reads as *a default nobody chose*, when what it marks is *the
    platform's own choice where the wire said nothing*. The meaning is unchanged, which keeps the
    rename out of `LES-131`'s hazard; the sidecar announces it with its own `schema:` key
    (`PROVENANCE_SCHEMA`) so a consumer outside this producer can tell a renamed class from a
    reader bug.
  * **`tap.sensors` is left unfilled.** Which Lab sensor captures which link is an `o2` fact
    aximprint cannot know, and a guess there is the exact failure `evidence_class` exists to
    prevent. The plan is deliberately incomplete and says so.

**And one policy this compiler no longer takes** (`docs/WORK_ORDER_deployment_scope.md`, ruled
2026-09-04): **the cast describes, the deploy selects.** Nothing is filtered here. Every placed
host becomes a node — role-less ones under `UNROLED_ROLE` — and every node and segment carries an
`observation: {windows_seen, windows_total, occupancy, role_confidence}` block the twin-config
expander gates on at `params.DEPLOY_MIN_OCCUPANCY`. A compile-time filter made "is every observed
host above the bar placed?" unanswerable, because the cast had already removed the evidence.

**Two mechanisms this compiler gained on 2026-09-04** (`docs/WORK_ORDER_reference_ingest.md` S1;
axRange `docs/design/14-operator-assertions.md` §2), both of which have *no producer in the store
yet* and are built ahead of one deliberately, because the store has never held two readings of one
fact and the resolution rule must exist before the first one arrives rather than be improvised
around it:

  * **`resolve_precedence`** — when two evidence classes speak to one field the higher one wins,
    in `EVIDENCE_CLASSES`' own declared order, and the sidecar records the loser under
    `overrides`. Nothing is erased: the store keeps both readings and the sidecar names the one
    that was beaten.
  * **`declared_transformation`** — a value this compiler replaces *for deployability* (a CIDR
    relocated out of the deployment target's address space today; the lab realm when S4 lands)
    is **not** a precedence event. It is applied after resolution, never competes, and always
    records the pre-transformation value beside it. That distinction is what lets a scorecard
    read such a row as *reproduced under a declared transformation* rather than as a mismatch —
    a `compiler`-class row that INVENTED a value (an image, the gateway) and one that MOVED an
    observed one are different claims and must not share a shape.

**And the producer those two were built ahead of, landed 2026-09-05** (S2): `compile_twin` takes
an `assertions=` set — a parsed `axr-assertions/1` file (`aximprint/operator_assertions.py`) — and
applies it as `add` / `override` / `suppress`. This is where `resolve_precedence` stops being
exercised only by unit tests: an `asserted` role genuinely contests the census's `imputed` one,
the sidecar names the loser, and D17's `drift` flag fires when the two disagree. Three properties
worth stating at the top because they constrain everything below:

  * **An assertion never changes what was observed, only what is emitted.** The PIT is read-only
    here and stays the complete view; every asserted value is a compile-time resolution recorded
    in the sidecar beside the reading it beat.
  * **A group is homogeneous in evidence class, by construction.** A host carrying an assertion
    gets its own node group (the group key gained an assertion signature), because a group's
    sidecar row publishes ONE class for the whole group — and publishing an `imputed` member's
    role as `asserted` because a sibling was hand-edited is the masquerade the model forbids.
  * **A shape error refuses; a resolution MISS reports.** An unknown field or a `kind: route` is
    the author's error and fails at parse time; a key this PIT does not carry is the PIT moving
    underneath a standing file, and it lands in `caveats` and in `population.assertions
    .ineffective` rather than failing the compile.
"""
from __future__ import annotations

import hashlib
import ipaddress
import itertools
import json
import statistics
from dataclasses import dataclass
from typing import Any, Optional

import yaml

from aximprint.catalog import (
    DEFAULT_NODE,
    ROLE_CATALOG,
    SYNTHETIC_ROLES,
    UNROLED_ROLE,
    match_role,
)
from aximprint.ir.materialize import classify_schedule
from aximprint.ir.schema import SCHEDULE_CLASSES, evidence_rank
from aximprint.operator_assertions import (
    ASSERTED_CONFIDENCE,
    ASSERTIONS_EVIDENCE_CLASS,
    ASSERTIONS_SCHEMA,
    canonical,
    conduit_key,
)
from aximprint.synth.personas import SINK_BUCKETS, sink_sub_role
from aximprint.synth.services import LAB_REALM, listen_specs, parse_port

#: The sidecar's own version, and the ONLY schema string this package declares.
#:
#: `LES-001`'s amended discriminator is *"a schema is versioned when something OUTSIDE its producer
#: depends on its shape"*, and axRange's imprint reader (`axrange/api/imprints.py`) is exactly
#: that: it reads `<id>.provenance.json` on a lifecycle that does not move with this build. Without
#: a declared version the 2026-09-04 `defaulted` -> `compiler` rename is indistinguishable at that
#: reader from a bug in its own parsing — which is the concrete reason this exists, rather than a
#: general appetite for versioning (`docs/WORK_ORDER_reference_ingest.md` D6/D-b, Authority: user).
#:
#: **Scope is the sidecar and nothing else.** The belief store, the event model, the feature shapes
#: and the plugin contract stay incubating and carry no version. It is deliberately NOT spelled
#: with the IR's own constant name, which `tests/test_ir_schema.py` refuses anywhere outside
#: `aximprint.ir` — that guard has to keep meaning exactly what it says.
PROVENANCE_SCHEMA = "axr-cast-provenance/1"

#: The sidecar-only evidence class: what this compiler chose where the wire said nothing.
#: Declared once, HERE, in the producer that emits it — `ir/schema.py::EVIDENCE_CLASSES`
#: deliberately does not carry it and `evidence_rank` refuses it, so it structurally cannot enter
#: a precedence comparison (axRange `docs/design/14-operator-assertions.md` §2).
COMPILER_CLASS = "compiler"

#: One synthetic router, multi-homed across every segment. **Not a discovered host** — phase 1
#: measured that this vantage carries neither of the signals DESIGN_BRIEF §8's gateway detection
#: needs (no `orig_l2_addr`, no TTL), so no real gateway is identifiable. It exists because
#: axRange's `expand.py` **errors** on a declared conduit with no `kind: router` node joining both
#: its segments, and because `TwinConfigSegment.gateway` is auto-assigned to `network+1` when a
#: router leg needs one. Every field of it is `COMPILER_CLASS`.
GATEWAY_NAME = "gw"

#: `zone_type` for every emitted segment. `simple` rather than `vlan` because this project
#: observes no VLAN tags at all — Zeek's `conn.log` carries none — so claiming otherwise would be
#: an invention. axRange treats `simple` as the default anyway; stating it makes the absence of
#: evidence visible rather than implicit.
ZONE_TYPE = "simple"

# -- the synthetic internet sink (`docs/JOURNAL_corpapcn_fidelity.md` GAP-C17) ---------------
#
# The twin's missing internet edge, as topology. `synth/personas.py::SINK_ROLE` holds the full
# argument for why it is addressed by ROLE and can therefore never leak; this is the other half
# -- the node groups and the segment they sit on.
#
# The CIDR is fixed content rather than derived, and it is EXCLUDED from `--relocate` so it
# survives `_cidr()` unchanged: relocation exists to move the twin out of the deployment
# target's address space, and a sink that moved with it would need its containment argument
# re-made for every prefix a caller picks. A guard below refuses any cast whose live segments
# would collide with it, so "fixed" cannot silently become "shared with a real segment".
#
# INSIDE `10.200.0.0/16`, deliberately: it keeps "did anything leave the twin?" answerable as a
# single grep for a destination not beginning `10.200.`, which is worth more than the tidiness
# of a separate prefix.
#
# The third octet is `254` and NOT a round number, which is a real constraint rather than
# taste. `_node_suffix`'s `rstrip(".0")` eats a whole RUN of trailing zeros, so any third octet
# ending in 0 collapses onto shorter siblings: `.200` -> `10-200-2`, which is also the suffix of
# relocation indices 2, 20 AND 200; `.250` -> `10-200-25`, colliding with 25 and 250 — and
# `10.200.25.0/24` is a live segment in the very twin this was measured on. `.254` ->
# `10-200-254` collides with nothing but itself. The sink therefore dodges that truncation
# outright instead of depending on the node-naming sidestep below, which stays as defence in
# depth. It is also unmistakable beside the source's own `10.20.200.0/24`, one octet away from
# the value this replaced and read in the same side-by-side compare table.
SINK_CIDR = "10.200.254.0/24"
SINK_SEGMENT_NAME = "internet-sink"

# -- deployment scope (`docs/WORK_ORDER_deployment_scope.md`, Authority: user 2026-09-04) -----
#
# THE CAST DESCRIBES; THE DEPLOY SELECTS. This compiler used to take two policy decisions that
# belong one layer down: it dropped role-less hosts (D5/D5a of `WORK_ORDER_visibility_and_vantage`)
# and it dropped hosts below a `--min-occupancy` bar (D11). Both are gone. Every placed host is
# emitted, annotated with an `observation:` block, and axRange's twin-config expander gates on
# that block at `DEPLOY_MIN_OCCUPANCY` (`axrange/docs/WORK_ORDER_deploy_gate.md`).
#
# D11's own docstring already argued the principle -- "one wide PIT serves many thresholds" -- it
# just applied it one layer too high. One CAST now serves every threshold too.

#: The deploy-time occupancy bar, emitted into every cast's `params:` so `twin-config show`
#: displays it. It is also axRange's own built-in default, so emitting it changes no behaviour --
#: it makes the bar VISIBLE in the document rather than implicit in the consumer (§9 ruling).
DEPLOY_MIN_OCCUPANCY = 0.8

#: THE FIXED OCCUPANCY LADDER (§9 ruling, replacing D3's exact-`windows_seen` group key).
#:
#: A node group is keyed on its members' presence CLASS, not on their exact window count. The
#: exact key was measured at 43 -> 87 groups on the CorpAPCN capture and would rename a group on
#: every re-imprint that moved one host by one window; the ladder is bounded at three groups per
#: today's one and keeps a group's name stable unless a host actually changes class.
#:
#: Ordered most-present first, and that order is also the emit order within a segment+role+band
#: (`_rung_rank`) -- deliberate rather than incidental, so a future rename cannot silently
#: reorder the document.
_RUNG_LADDER: tuple[tuple[str, float], ...] = (
    ("core", 0.8),          # >= 0.8 -- and 0.8 IS a rung: it is the default deploy bar
    ("regular", 0.5),       # 0.5 <= occupancy < 0.8
    ("transient", 0.0),     # < 0.5
)

#: The rung for a node group whose members were ASSERTED rather than observed
#: (`docs/WORK_ORDER_reference_ingest.md` S2). Off the ladder, like `UNMEASURED_RUNG` and for a
#: related but distinct reason: an unmeasured group has a presence nobody could compute, while an
#: asserted one has no presence to compute — nothing observed it, so there is no denominator and
#: no numerator either. Both carry NO `observation:` block, which axRange reads as "never gated
#: out"; keeping them apart is what stops an asserted host being reported in the caveat about
#: denominators, which would be a true sentence about the wrong hosts.
#:
#: It is also what keeps a group HOMOGENEOUS: an asserted host can never share a group with an
#: observed one, so no group's sidecar row has to publish one class for members of two.
ASSERTED_RUNG = "asserted"

#: Appended to a node group's name when the group carries an assertion that is not already
#: visible in its rung — an OVERRIDE of an observed host's role, image, resources or segment.
#: Visible in the twin config itself rather than only in the sidecar, on the same reasoning the
#: activity band and the occupancy rung are already in the name: a property that changes what a
#: node IS should not be reachable only by opening a second file.
ASSERTED_MARKER = "asserted"

#: The rung for a group whose occupancy cannot be computed at all -- a PIT claiming zero windows,
#: or an entity with no `windows_seen`. Such a group carries NO `observation:` block, which
#: axRange reads as "never gated out": a host is not excluded for a denominator problem
#: (`LES-006`), and the unmeasurable case is disclosed in `caveats` rather than silently filtered.
#: Off the ladder on purpose -- it is not a presence class, it is the absence of one.
UNMEASURED_RUNG = "unmeasured"

#: Matches axRange's own gate epsilon (`_deploy_thresholds`, `occupancy >= min - 1e-9`), so a
#: group NAMED `core` and a group that PASSES the 0.8 default bar are the same set. A float
#: boundary flip between the two would produce a `core` group the deploy silently drops.
_RUNG_EPSILON = 1e-9

def _is_ipv4_network(cidr: str) -> bool:
    """Whether a segment CIDR is one the twin IR can take (IPv4)."""
    try:
        return ipaddress.ip_network(cidr, strict=False).version == 4
    except ValueError:
        return False



def occupancy_rung(occupancy: Optional[float]) -> str:
    """Which presence class an occupancy falls in. `None` -> `UNMEASURED_RUNG`."""
    if occupancy is None:
        return UNMEASURED_RUNG
    for name, floor in _RUNG_LADDER:
        if occupancy >= floor - _RUNG_EPSILON:
            return name
    # Unreachable: the last rung's floor is 0.0 and occupancy is domain-gated to [0, 1] by
    # `_occupancy_of`. Kept as a positive gate rather than an implicit fall-through.
    raise ValueError(f"occupancy {occupancy!r} falls off the bottom of the ladder")


def _rung_rank(rung: str) -> int:
    """Sort position of a rung. Off-ladder rungs sort last, deterministically."""
    names = [n for n, _ in _RUNG_LADDER]
    return names.index(rung) if rung in names else len(names)


def _segment_occupancy(seg: dict) -> Optional[float]:
    """A segment's `windows_present / windows_total`, or `None` when it cannot be asked.

    Its OWN `windows_total`, not the PIT's: `ir/merge.py` takes the best-covered vantage's pair
    for a segment on the vantage axis, so the two can legitimately differ and the ratio must be
    read against the denominator the numerator was counted under (`LES-109`).
    """
    present, total = seg.get("windows_present"), seg.get("windows_total")
    if present is None or total is None or total < 1:
        return None
    occupancy = present / total
    if not 0.0 <= occupancy <= 1.0:
        raise ValueError(
            f"segment {seg.get('cidr')!r}: windows_present={present} of windows_total={total} "
            f"gives occupancy {occupancy} outside [0, 1]")
    return occupancy


def _node_suffix(cidr: str) -> str:
    """A short, stable, unique-per-segment node-name suffix: `10.80.72.0/24` -> `10-80-72`.

    Derived from the CIDR rather than an ordinal, deliberately. An ordinal ("segment 3") would
    renumber every node the moment a segment appeared or disappeared between two captures, which
    is exactly the instability a drift comparison would then report as change.

    Trailing all-zero octets are dropped a WHOLE OCTET at a time, never as a character run. The
    old `rstrip(".0")` ate individual chars, so a round third octet collapsed onto a shorter
    sibling — `10.20.200.0` -> `10-20-2`, indistinguishable from `10.20.2.0`. With axRange's
    separator-less `count:` fan-out (`instance_ids`: `f"{name}{i:02d}"`), a `count: 4` node on
    `.200` then minted `...-201..-204` and collided with the singleton `.201`/`.204` segments —
    the exact "duplicate node id after count fan-out" the axRange validator refuses. Octet-wise
    stripping keeps `10.20.200.0` -> `10-20-200`, distinct from every sibling. (The golden-1.5
    node name also ends in a non-numeric rung word — `-core`/`-transient` — which independently
    separates the suffix from the fan-out ordinal; this fix removes the collapse at its root so
    the suffix is correct and collision-safe even where that guard is absent.)
    """
    net = cidr.split("/")[0]
    if ":" in net:                       # IPv6: transform without the char-run strip
        return net.replace(":", "-").strip("-") or net
    octets = net.split(".")              # IPv4 dotted quad: drop WHOLE trailing zero octets
    while len(octets) > 1 and octets[-1] == "0":
        octets.pop()
    return "-".join(octets)


def _rule_for(role: Optional[str]):
    """The catalog row for a role, or None. First match wins — a role may have several rows
    (`web-tier` has one for 443 and one for 80) and they carry identical content.

    Falls through to `SYNTHETIC_ROLES` for a role the compiler invents rather than infers
    (`internet-sink-*`). Those are held out of `ROLE_CATALOG` so `match_role` structurally
    cannot return one, but a synthetic node still needs an image and a sizing like any other
    — and without this the sink's 15 VMs would take `DEFAULT_NODE`'s 2Gi/20Gi apiece, i.e.
    30Gi of RAM and 300Gi of disk to run an echo server.
    """
    if not role:
        return None
    return next((r for r in ROLE_CATALOG if r.role == role), None) or SYNTHETIC_ROLES.get(role)


def _rule_evidence(rule) -> Optional[tuple[tuple[str, ...], tuple[str, ...]]]:
    """A catalog row's identity as `(requires, dials)`, or `None` when it has neither.

    `(role, requires)` is the form `LES-136` requires of any reference to a table that gains
    rows — never an index. `dials` rides along because two rows of one role can differ on it
    alone (`corp-workstation` has a `3389/tcp` listen row and an `88/tcp` dial row).

    `None` for a **`SyntheticRole`** — the internet sink — which has no `requires`/`dials` BY
    CONSTRUCTION: `catalog/roles.py` keeps those rows out of `ROLE_CATALOG` precisely so no
    evidence can ever name one, and asking it for its evidence is a category error rather than a
    missing field. Tested for by attribute rather than by `isinstance`, so a third content-only
    role kind is covered the day it appears.
    """
    requires = getattr(rule, "requires", None)
    if rule is None or requires is None:
        return None
    return tuple(sorted(requires)), tuple(sorted(rule.dials))


def _matched_evidence(role: str, members) -> Optional[tuple[tuple[str, ...], tuple[str, ...]]]:
    """The catalog row that ACTUALLY produced this group's role, re-derived from the port
    evidence the IR carries — or `None` when that cannot be said of the group as a whole.

    **This is not `_rule_for(role)`, and the difference is a correctness one.** `_rule_for`
    returns the FIRST row bearing a role's name, which is fine for `image`/`resources` (every
    row of a role carries identical content, see its own docstring) and wrong for provenance: a
    role NAME is a many-to-one projection of the catalog, and the inverse is not a function.
    Four shipped roles have several rows with different evidence — `generic-endpoint` ×5,
    `corp-workstation` ×4, `monitoring-collector` ×3, `web-tier` ×2 — so a name lookup publishes
    a specific, plausible, WRONG evidence claim. Measured on the CorpAPCN PIT before the fix:
    **70 of 122 roled placed hosts** would have been published under ports that did not produce
    their match, 45 of them dialling `123/udp` and captioned `->443/tcp`.

    The resolving identity is not persisted (`ir_entity` carries `role` and `role_confidence`,
    not which rule won), so it is re-derived here from `listen_ports`/`dial_ports` through the
    SAME `match_role` the census used — one rule, applied twice, rather than a second guess.

    **`None` for a heterogeneous group**, deliberately: a node group can legitimately hold hosts
    that reached one role by different rows, and a caller then degrades to naming the file and
    the role without a port list. That is the coarser TRUE statement, which is the only honest
    alternative to a specific wrong one (`LES-006`: absence beats a confident fabrication).
    `None` too when the re-derivation lands on a rule for a DIFFERENT role — see the comment at
    the guard, which is a measured case on this capture and not a hypothetical.
    """
    seen: set = set()
    for member in members:
        rule = match_role(_json_value(member.get("listen_ports")) or [],
                          _json_value(member.get("dial_ports")) or [])
        # A rule for a DIFFERENT role names no evidence for THIS one, and the case is real
        # rather than defensive: on the CorpAPCN PIT one host carries the stored role
        # `dns-resolver` while its PIT-aggregated ports now match the `domain-controller` row
        # (88+389). The stored role was inferred per window, and the PIT unions ports across
        # every window, so re-running the census over the union can legitimately land on a more
        # specific row than the one that fired. Naming that row would caption a `dns-resolver`
        # with a domain controller's evidence — a different fabrication from the one this
        # function fixes, and just as unfalsifiable. Degrade instead.
        if rule is None or rule.role != role:
            return None
        evidence = _rule_evidence(rule)
        if evidence is None:
            return None
        seen.add(evidence)
        if len(seen) > 1:
            return None
    return next(iter(seen)) if len(seen) == 1 else None


def _role_signal(role: str, evidence, *, synthetic: bool = False) -> str:
    """The `signal` for a catalog-derived field — the row that matched, or the honest coarser
    statement when no single row can be named.

    `"catalog/roles.yaml domain-controller (389/tcp+88/tcp)"` — ports sorted, so the string is
    stable rather than dependent on a frozenset's iteration order (`LES-009`). A `dials`-only row
    reads with its evidence prefixed `->`, so "answers on 443" and "dials 443" cannot read alike.

    `evidence` comes from `_matched_evidence`, never from `_rule_for`. With none — a synthetic
    role, a role with no catalog row, or a group whose members matched different rows — the
    string names the file and the role and stops there.
    """
    if synthetic:
        return f"catalog/roles.yaml (synthetic: {role})"
    if not evidence:
        return f"catalog/roles.yaml {role}"
    requires, dials = evidence
    ports = "+".join(list(requires) + [f"->{d}" for d in dials])
    return f"catalog/roles.yaml {role}" + (f" ({ports})" if ports else "")


def _json_value(value):
    """A JSON column's value, regardless of whether it arrived as a raw string (straight out of
    `ir.materialize.resolve`) or already parsed (a hand-built PIT in a test, or a future DuckDB
    version that decodes JSON columns itself) — the same defensive idiom `ir/merge.py::_json_list`
    and `ir/visibility.py` already use for the identical situation one layer over."""
    return json.loads(value) if isinstance(value, str) else value


def _role_seed(pit_id: str, role: str) -> int:
    """`docs/WORK_ORDER_traffic_profiles.md` D5: a deterministic, non-random seed for one role in
    one PIT — a COMPILER CHOICE, never a measurement, which is why its provenance is `defaulted`.

    Hashed rather than Python's builtin `hash()`, which is salted per PROCESS
    (`PYTHONHASHSEED`) — two casts of the identical PIT in two different processes would then
    disagree, exactly the determinism break `scripts/verify_determinism.py` exists to catch
    (`LES-002`). `sha256` is already this project's own stable-hash idiom for a derived id
    (`ir/materialize.py::pit_id_for`); the first 8 hex digits keep the result inside the 32-bit
    range `axrange/personas/drivers/base.py::effective_seed` folds any raw seed into anyway, so
    nothing downstream has to widen to hold it.
    """
    digest = hashlib.sha256(f"{pit_id}|{role}".encode("utf-8")).hexdigest()
    return int(digest[:8], 16)


# -- the evidence model (`docs/WORK_ORDER_reference_ingest.md` S1) ---------------------------
#
# Nothing in the store produces two classes for one fact TODAY. That is exactly why the rule is
# built now: S2's assertions file is the first producer, and the ordering question is much easier
# to answer while no data depends on the answer. Both helpers below are pure, take no PIT, and are
# exercised by unit tests with synthetic pairs — the acceptance the work order names.


@dataclass(frozen=True, slots=True)
class Fact:
    """One reading of one field, from one evidence class.

    Deliberately NOT a dict. The resolver's whole job is to compare things that are the same
    shape, and an untyped dict that grows a key per producer is already a schema (`LES-112`) —
    with the extra hazard here that a missing `evidence_class` key would silently sort somewhere.

    `confidence`, `source` and `signal` are what the sidecar publishes about the WINNER; a loser
    contributes only its class and its value, because "what it would have been" is the thing a
    reader needs and the rest belongs to the store.
    """

    #: One of `ir/schema.py::EVIDENCE_CLASSES`. `COMPILER_CLASS` is refused by `evidence_rank`.
    evidence_class: str
    value: Any
    #: Only where a producer supplies one — never invented, never a null (`LES-006`, D7).
    confidence: Optional[float] = None
    #: Who says so: `"operator:<name>"`, an export's id. Absent where the wire is the source.
    source: Optional[str] = None
    #: The rule or stem behind it — short, never a sentence (D7).
    signal: Optional[str] = None


def resolve_precedence(field: str, facts) -> tuple[Fact, Optional[dict]]:
    """Which reading of `field` wins, and the `overrides` record naming what it beat.

    Precedence is `EVIDENCE_CLASSES`' own declared order (`asserted` > `imported` > `observed` >
    `referenced` > `imputed`), read through `evidence_rank` so the ranking cannot drift from the
    vocabulary (`LES-101`).

    **The house rule this implements:** no class masquerades as a higher one, and an override is
    visible, never an erasure. The winner's sidecar record carries
    `overrides: {"class": …, "value": …}` naming the RUNNER-UP — the reading that would have won
    had the winner been absent, which is the alternative a reader actually needs. Lower-ranked
    candidates are not repeated here: they are in the store, which keeps every reading, and
    restating a third-place value in the sidecar would make it look like a second competition
    happened.

    **Two facts of the same class for one field are refused, loudly.** That is a contradiction
    within one producer, not a precedence question, and picking one of them by arrival order is
    precisely the row-order dependence `LES-009` forbids — there is no honest tiebreak, so there
    is no tiebreak (CONVENTIONS §3, fail closed).

    Returns `(winner, overrides_or_None)`; `overrides` is `None` when only one class spoke, which
    is every field in every cast until S2 lands.
    """
    facts = list(facts)
    if not facts:
        raise ValueError(f"{field}: resolve_precedence needs at least one fact")
    ranked = sorted(facts, key=lambda f: evidence_rank(f.evidence_class))
    for earlier, later in itertools.pairwise(ranked):
        if earlier.evidence_class == later.evidence_class:
            raise ValueError(
                f"{field}: two {earlier.evidence_class!r} facts speak to one field "
                f"({earlier.value!r} and {later.value!r}). That is a contradiction inside one "
                f"producer, not a precedence question — one of them is wrong, and choosing by "
                f"arrival order would make the cast depend on row order (`LES-009`).")
    winner, runner_up = ranked[0], (ranked[1] if len(ranked) > 1 else None)
    if runner_up is None:
        return winner, None
    return winner, {"class": runner_up.evidence_class, "value": runner_up.value}


def declared_transformation(kind: str, *, replaced: Fact, reason: str, **detail) -> dict:
    """The record a value replaced FOR DEPLOYABILITY carries, in place of competing.

    `docs/WORK_ORDER_reference_ingest.md` §5.6, adopted into axRange's
    `docs/design/14-operator-assertions.md` §2: *transformations declared by the compiler for
    deployability are not evidence, do not participate in precedence, are applied after
    resolution, and always record the pre-transformation value.*

    Two live cases, one of them built: **relocation** moves an observed CIDR out of the deployment
    target's own address space (measured, not hypothetical — GAP-C4), and **the lab realm** will
    stand in for an imported one when S4 lands. Both look, at first glance, like a `compiler`-class
    value beating an `observed` or `imported` one, which would invert the precedence order the
    whole model rests on. They are not precedence events at all, and this record is what says so.

    Why it matters downstream rather than only being tidy: a scorecard must read such a row as
    *reproduced under a declared transformation*, not as a mismatch — and it cannot, if the row is
    shaped identically to a `compiler` value that was INVENTED from nothing (an image, the
    synthetic gateway). The escape hatch stays ordinary: an `override:` assertion moves the choice
    into a class that outranks the evidence, and then it IS a precedence event.
    """
    # `from` carries the replaced reading's own provenance, not just its value — its class AND,
    # where the producer supplied them, its `signal` and `confidence`. Forensic completeness
    # (CONVENTIONS §3): the point of the record is that the pre-transformation reading survives,
    # and a reading stripped of what backed it has already lost half of itself. Absent rather
    # than null where the producer supplied nothing (`LES-006`).
    return {"kind": kind, "reason": reason,
            "from": {"evidence_class": replaced.evidence_class, "value": replaced.value,
                     **({"signal": replaced.signal} if replaced.signal else {}),
                     **({"confidence": replaced.confidence}
                        if replaced.confidence is not None else {})},
            **detail}


# -- the assertions view (`docs/WORK_ORDER_reference_ingest.md` S2) --------------------------


class _Assertions:
    """One parsed assertions file, indexed for the compiler and keeping its own audit.

    A thin index rather than a second parser: `operator_assertions` already refused every shape
    error, so nothing here validates. What it adds is the two things only a compiler can supply —
    **which records actually resolved against this PIT**, and **what the reading they beat was**.

    **Nothing here iterates the file's order** (D16). Every lookup is a dict keyed by the same
    `(kind, key)` / `(key, field)` merge keys the file itself merges on, and the one iteration that
    remains — `ineffective` — is sorted before it is published. Two compiles of one PIT with one
    file therefore produce identical bytes whether or not the operator reordered the document.
    """

    def __init__(self, aset):
        self.aset = aset
        self.source = aset.source
        self.segment_adds = {r.key: r for r in aset.adds if r.kind == "segment"}
        # A `device` add is a host add that also says what the device IS (`device_kind`), so it
        # takes the host path everywhere and its kind reaches the node through `host_fields`.
        self.host_adds = {r.key: r for r in aset.adds if r.kind in ("host", "device")}
        self.conduit_adds = {r.key: r for r in aset.adds if r.kind == "conduit"}
        self.link_adds = {r.key: r for r in aset.adds if r.kind == "link"}
        self.tap_adds = {r.key: r for r in aset.adds if r.kind == "tap"}
        self.suppress = {r.key: r for r in aset.suppress}
        self.confirm = {r.key: r for r in getattr(aset, "confirm", ())}
        self.doubt = {r.key: r for r in getattr(aset, "doubt", ())}
        self.host_over: dict[str, dict] = {}
        self.segment_over: dict[str, dict] = {}
        self.link_over: dict[str, dict] = {}
        for record in aset.overrides:
            target = {"host": self.host_over, "segment": self.segment_over,
                      "link": self.link_over}[record.kind]
            target.setdefault(record.key, {})[record.field] = record
        #: `(kind, key[, field])` tuples that found something in this PIT to act on. Its
        #: complement is what `ineffective` reports, so the two are derived from one fact rather
        #: than counted twice (`LES-026`).
        self.matched: set = set()
        self._ineffective: dict[tuple, str] = {}

    # -- lookups ----------------------------------------------------------------

    def host_fields(self, key: str) -> dict:
        """`{field: (value, record)}` for one host — its add's own content plus its overrides.

        The two cannot collide: a `(key, field)` declared by both is refused at parse time, since
        two hand-authored readings of one field are one class contradicting itself and there is no
        precedence between them.
        """
        out: dict = {}
        add = self.host_adds.get(key)
        if add is not None:
            out.update({name: (value, add) for name, value in add.fields.items()})
        out.update({name: (record.value, record)
                    for name, record in self.host_over.get(key, {}).items()})
        return out

    def segment_fields(self, cidr: str) -> dict:
        out: dict = {}
        add = self.segment_adds.get(cidr)
        if add is not None:
            out.update({name: (value, add) for name, value in add.fields.items()})
        out.update({name: (record.value, record)
                    for name, record in self.segment_over.get(cidr, {}).items()})
        return out

    def signature(self, entity_key: str) -> tuple:
        """The grouping-key component that keeps an asserted host out of an observed group.

        `()` for a host no record touches, which is every host of every cast that passes no
        assertions file — so the group key, the node names and therefore the emitted bytes are
        unchanged by this mechanism's existence (`LES-002`, and the property the determinism gate
        checks).

        Values go through `operator_assertions.canonical` rather than into the tuple raw: a
        `resources` mapping is the one value shape whose Python dict order comes from the file,
        and a group key that varied with it would rename a node between two compiles of one file.
        """
        fields = self.host_fields(entity_key)
        return tuple(sorted((name, canonical(value)) for name, (value, _r) in fields.items()))

    def mark_source(self, keys) -> Optional[str]:
        """The `source` a confirm / doubt record pins on an entity one of `keys` names
        (`docs/design/topology-layers.md` §3.2): `unconfirmed` when any is doubted, else
        `asserted` when any is confirmed, else `None`. Marks what it used."""
        hit: Optional[str] = None
        for key in keys:
            if key in self.doubt:
                self.mark("doubt", key)
                hit = "unconfirmed"
            elif key in self.confirm:
                self.mark("confirm", key)
                hit = hit or "asserted"
        return hit

    # -- audit ------------------------------------------------------------------

    def mark(self, *key) -> None:
        self.matched.add(tuple(key))

    def ineffective(self, kind: str, key: str, reason: str, field: Optional[str] = None) -> None:
        """Record a well-formed record that this PIT gave nothing to act on.

        Reported, never fatal, and never silent. The PIT moves underneath a standing assertions
        file by construction — a re-imprint from a later capture may not carry a host the operator
        asserted about last week — so refusing here would make every re-imprint an editing
        session, which is the same argument D17 makes for flagging drift instead of demoting the
        assertion. Deduplicated by key so one unresolvable record produces one line however many
        places noticed it.
        """
        self._ineffective[(kind, key, field or "")] = reason

    def report(self) -> list[dict]:
        return [{"kind": kind, "key": key, **({"field": field} if field else {}),
                 "reason": reason}
                for (kind, key, field), reason in sorted(self._ineffective.items())]

    def unmatched(self) -> list[dict]:
        """Every record the compiler never marked as having resolved — the honest complement of
        `matched`, derived rather than counted a second time."""
        rows = []
        for record in self.aset.adds:
            if ("add", record.kind, record.key) not in self.matched:
                rows.append({"kind": f"add:{record.kind}", "key": record.key})
        for record in self.aset.overrides:
            if ("override", record.key, record.field) not in self.matched:
                rows.append({"kind": f"override:{record.kind}.{record.field}",
                             "key": record.key})
        for record in self.aset.suppress:
            if ("suppress", record.key) not in self.matched:
                rows.append({"kind": "suppress", "key": record.key})
        for block in ("confirm", "doubt"):
            for record in getattr(self.aset, block, ()):
                if (block, record.key) not in self.matched:
                    rows.append({"kind": block, "key": record.key})
        return sorted(rows, key=lambda r: (r["kind"], r["key"]))


def _asserted_prov(record, field: str, *, beaten: Optional[dict] = None,
                   drift_pit: Optional[str] = None, source: str = "") -> dict:
    """The sidecar extras an `asserted` field's `prov()` row carries.

    `confidence` is the 1.0 convention (never authored, `14` §3), `source` names the operator, and
    `note` carries the record's own — which is the field a reader six months later actually needs,
    since an assertion outranks every class of evidence in the store and the reason it was made is
    the only thing that makes it reviewable.

    `overrides` names the reading it beat, and gains **`drift`** when that reading CONTRADICTS it
    (D17). Drift is flag-only by ruling: the assertion still wins, both readings stay in the
    artifact, and a `caveats` line names it — data never changes precedence. An `overrides` whose
    value AGREES with the winner is not drift and is not flagged, which keeps the flag meaning
    "the wire now says something else" rather than "an override exists".
    """
    extras: dict = {"confidence": ASSERTED_CONFIDENCE, "source": source,
                    "note": record.note}
    if beaten is not None:
        overrides = dict(beaten)
        if drift_pit is not None and beaten.get("value") != _asserted_value(record, field):
            overrides["drift"] = {"class": beaten["class"], "value": beaten["value"],
                                  "pit_id": drift_pit}
        extras["overrides"] = overrides
    return extras


def _derive_segment(entity_key: str, known_cidrs) -> Optional[str]:
    """Which known segment an added host's key falls inside — D11(a)'s derivation.

    `None` when it cannot be answered, which the caller turns into a refusal naming the key: an
    address alone names no network, and a `hostname:` or `mac:` key names one even less. Only an
    `address:` key can be derived at all, and it is derived by CONTAINMENT rather than by
    string-prefix matching (`LES-008`) — `10.80.3.5` is not in `10.80.31.0/24`, and a prefix
    comparison says it is.

    Ties go to the LONGEST prefix, which is both the routing convention and the only choice that
    is stable: two nested segments containing one address are a real shape (a /16 declared beside
    a /24 inside it), and picking the shorter would place a host on a network its own subnet also
    covers.
    """
    if not entity_key.startswith("address:"):
        return None
    try:
        addr = ipaddress.ip_address(entity_key.split(":", 1)[1])
    except ValueError:
        return None
    containing = [ipaddress.ip_network(c) for c in known_cidrs
                  if ipaddress.ip_network(c).version == addr.version
                  and addr in ipaddress.ip_network(c)]
    if not containing:
        return None
    return str(max(containing, key=lambda n: (n.prefixlen, str(n))))


def _asserted_entity(key: str, cidr: str, record) -> dict:
    """An `add: host` record as an `ir_entity`-shaped row the rest of this compiler can read.

    **`windows_seen` is `None`, not 0**, and that is the whole honesty of the record: nothing
    observed this host, so it has no presence to report, and a zero would assert it was looked for
    and never seen (`LES-006`). It reaches `ASSERTED_RUNG` on that basis and therefore carries no
    `observation:` block, which axRange reads as "always emitted" — correct, since a deploy gate
    over observed occupancy has no business filtering a host an operator declared.

    `role_confidence`/`segment_confidence` are `None` for the same reason: there is no inference
    to be confident about. The 1.0 the sidecar publishes is the ASSERTION's convention, supplied
    at the `prov()` site, not a measurement smuggled in through a member row.
    """
    return {
        "pit_id": None, "entity_key": key, "key_kind": key.split(":", 1)[0],
        "source_entity": key, "locality": "local",
        "segment": cidr, "segment_confidence": None, "segment_evidence_class": None,
        "role": record.fields.get("role"), "role_confidence": None,
        "role_evidence_class": ASSERTIONS_EVIDENCE_CLASS if record.fields.get("role") else None,
        "listen_ports": json.dumps(record.fields.get("listen") or []),
        "dial_ports": json.dumps(record.fields.get("dial") or []),
        "windows_seen": None, "first_seen": None, "last_seen": None,
        "addresses": json.dumps([key.split(":", 1)[1]]), "identifiers": json.dumps([]),
        "vantages": None,
    }


def _pit_role_facts(members, matched, *, exclude_keys=frozenset(), drop_class=None):
    """The reading(s) THE PIT ITSELF carries for one node group's role, one `Fact` per class.

    Returns `(facts, ambiguous_values)`.

    **The class is read from `ir_entity.role_evidence_class`, never assumed to be `imputed`.**
    That constant was right while the role census was the only producer, and D4 made the column
    a real one: an operator's assertion ingested into the belief store materializes as `asserted`,
    so a compiler that hardcoded `imputed` here would publish an assertion as an inference — the
    same masquerade one layer further along, and invisible because the VALUE would be right.

    **`drop_class` is what stops one assertion being counted twice.** An assertion can reach a
    cast through two doors — the file, and a belief store that already ingested it — and when both
    are used the PIT's reading and the file's are the SAME fact, not two. `resolve_precedence`
    refuses two facts of one class, correctly, so the duplicate has to be removed here rather than
    discovered as a crash. If the two genuinely disagree the store's reading is dropped and the
    file's stands, which is the right way round: the file is the authority the operator edits.

    **A class whose members disagree on the value degrades to nothing**, and the values are
    reported so a caveat can name them. A group's provenance publishes ONE reading per class, and
    picking one member's when they differ is a specific, plausible, wrong evidence claim in the
    artifact whose job is keeping observed and invented apart (`LES-140`, `LES-006`).
    """
    by_class: dict[str, list] = {}
    for member in members:
        if not member["role"] or member["entity_key"] in exclude_keys:
            continue
        by_class.setdefault(member["role_evidence_class"] or "imputed", []).append(member)
    facts, ambiguous = [], []
    for cls in sorted(by_class, key=evidence_rank):
        if cls == drop_class:
            continue
        rows = by_class[cls]
        values = {m["role"] for m in rows}
        if len(values) > 1:
            ambiguous.extend(sorted(values))
            continue
        value = next(iter(values))
        confidences = [m["role_confidence"] for m in rows if m["role_confidence"] is not None]
        facts.append(Fact(
            cls, value,
            confidence=max(confidences) if confidences else None,
            # `matched` names the catalog row that fired and is only meaningful for a role the
            # CENSUS produced; any other class was not derived from a catalog row at all, so the
            # signal degrades to the column it came from rather than naming ports that did not
            # produce it (`LES-140`).
            source=("extractor:role-census" if cls == "imputed" else f"ir_entity ({cls})"),
            signal=(_role_signal(value, matched if value == (rows[0]["role"]) else None)
                    if cls == "imputed" else "ir_entity.role_evidence_class")))
    return facts, sorted(set(ambiguous))


def _asserted_value(record, field: str):
    """The value a record asserts for `field` — an `override`'s `value`, an `add`'s field."""
    return record.value if hasattr(record, "value") else record.fields.get(field)


def compile_twin(pit: dict, *, max_count: Optional[int] = None,
                 relocate: Optional[str] = None, assertions=None) -> dict:
    """Compile one PIT into the three axRange documents plus a provenance sidecar.

    `assertions` is a parsed `operator_assertions.AssertionSet` or `None`
    (`docs/WORK_ORDER_reference_ingest.md` S2). With `None` — every cast until an operator writes
    a file — nothing below changes at all: no group gains a signature, no segment is synthesized,
    and the emitted bytes are what they were before the mechanism existed. That is checked rather
    than asserted (`tests/test_synth_compiler.py`, and the determinism gate compiles both ways).

    `max_count` caps a collapsed node group's `count:` — **D3 of `WORK_ORDER_synth_v1.md`, which
    is still an open user decision.** The default is uncapped, which is the honest reading of the
    evidence: the network really does have that many hosts. When a cap does bite it is recorded
    in `caveats`, never silently applied, because a twin quietly missing 40 hosts would
    misrepresent the network it claims to replicate. It is a SCALE-DOWN knob, not a scope one,
    which is why it survived the change below.

    **NO FILTER RUNS HERE ANY MORE** (`docs/WORK_ORDER_deployment_scope.md` D1, Authority: user).
    Every placed host becomes a node, and every segment carrying one becomes a segment. The two
    filters this compiler used to apply — role-BEARING only (D5/D5a of
    `WORK_ORDER_visibility_and_vantage.md`) and `--min-occupancy` (D11 of the same) — are now
    **annotation**: a role-less host is emitted under `UNROLED_ROLE`, and presence is published
    as an `observation:` block on every node and segment. axRange's expander gates on that block
    at `DEPLOY_MIN_OCCUPANCY`, so one cast serves every threshold and the capacity question is
    answered where capacity actually lives.

    What that leaves in `caveats` and the sidecar's `population` block is *accounting*, not a
    drop list: how many placed hosts carry no inferred role, how many groups could not be given a
    block at all, and which segments were genuinely empty. The reconstruction identity is now an
    equality rather than a sum over exclusions — `placed_hosts == sum(observed)`.
    """
    from aximprint.windowing.segments import segment_name

    pit_row = pit["ir_pit"][0]
    twin_id = pit_row["pit_id"]
    provenance: dict[str, dict] = {}
    caveats: list[str] = []
    # `_NO_ASSERTIONS` is not a null object with empty tables — it is `None`, checked at each of
    # the six sites that consult it. A null object would be tidier and would also make "does this
    # cast carry assertions?" unanswerable from the code, which is the question every one of those
    # sites is really asking.
    aview = _Assertions(assertions) if assertions is not None else None
    #: Did this PIT observe the network AFTER the assertions file was written? The one thing that
    #: distinguishes D17's drift — a later capture contradicting a standing assertion — from the
    #: ordinary contradiction an override exists to resolve. `False` when either bound is missing:
    #: a PIT with no `observed_to` cannot be placed in time relative to anything, and claiming
    #: drift on an unknown ordering would be a fabricated finding (`LES-006`).
    pit_is_later = bool(
        aview is not None and pit_row.get("observed_to") is not None
        and pit_row["observed_to"] > assertions.as_of_epoch)

    def prov(path: str, evidence_class: str, signal: str, **extra) -> None:
        """One field's sidecar row.

        `signal` is **positional and required** (D7), not an optional keyword. Every value this
        compiler emits has a nameable rule or stem behind it — a catalog row, an IR column, a
        hash — so there is no honest reason to omit one, and a lesson that says "prefer X" gets
        honoured by doing X only if the signature stops you doing otherwise (`LES-114`). Short:
        it names the rule, never a sentence; the sentence goes in `reason`.

        `confidence` is the mirror image and stays an ordinary keyword: it is emitted **only
        where a producer supplied one** (a role census's score, a segment binding's), and is
        ABSENT — never null, never invented — everywhere else (`LES-006`). axRange's Console
        shades on it, and a fabricated 1.0 would shade a compiler's guess as certainty.
        """
        provenance[path] = {"evidence_class": evidence_class, "signal": signal, **extra}

    # -- which entities become nodes --------------------------------------------
    # PLACED only. `locality == 'local'` alone would add every link-local and multicast address
    # as a host (phase 1: 117 of them), each already in the twin under its IPv4 address.
    #
    # An `override: {field: segment}` is applied HERE rather than after grouping, because it
    # decides PLACEMENT: a local entity the vantage never bound to a network is not placed at all
    # until an operator says which one it is on, and the group key carries the segment.
    def _segment_of(entity) -> Optional[str]:
        if aview is not None:
            asserted = aview.host_fields(entity["entity_key"]).get("segment")
            if asserted is not None:
                return asserted[0]
        seg = entity["segment"]
        # The twin IR is IPv4-only (axRange refuses a non-IPv4 segment CIDR outright), so an
        # IPv6 segment the windowing minted -- a ULA /64 passes `is_provisionable` -- can never
        # be a twin segment, and a host placed only there is not a twin node. Counted and
        # named below rather than dropped quietly.
        return seg if seg and _is_ipv4_network(seg) else None

    placed = [e for e in pit["ir_entity"] if e["locality"] == "local" and _segment_of(e)]
    v6_segments = sorted({e["segment"] for e in pit["ir_entity"]
                          if e["locality"] == "local" and e["segment"]
                          and not _is_ipv4_network(e["segment"])})
    v6_entities = sum(1 for e in pit["ir_entity"]
                      if e["locality"] == "local" and e["segment"] in v6_segments)
    if v6_segments:
        caveats.append(
            f"{len(v6_segments)} IPv6 segment(s) carrying {v6_entities} local entities are not "
            f"twin segments and those entities are not twin nodes: the twin IR is IPv4-only, and "
            f"a dual-stack host is already present under its IPv4 address. Segments: "
            f"{v6_segments}.")
    if skipped := sum(1 for e in pit["ir_entity"]
                      if e["locality"] == "local" and not _segment_of(e)
                      and e["segment"] not in v6_segments):
        caveats.append(
            f"{skipped} local entities were observed on no provisionable network (link-local, "
            f"multicast) and are NOT twin nodes; each is already present under its routable "
            f"address.")

    # -- assertions: added hosts, suppression, and the audit of what resolved -----
    #
    # Three passes over the file, all keyed rather than iterated in document order (D16). The
    # accounting they produce is what makes D18's rule true — *a suppressed entity never silently
    # vanishes* — and what turns the population block's reconstruction identity from
    # `placed_hosts == sum(observed)` into `placed_hosts == sum(observed) + suppressed`.
    asserted_hosts: list[dict] = []
    asserted_host_keys: set = set()
    suppressed_rows: list[dict] = []
    if aview is not None:
        observed_keys = {e["entity_key"] for e in pit["ir_entity"]}
        # Every CIDR a host may be placed on: observed segments plus asserted ones. Used both to
        # derive an added host's segment (D11(a)) and to refuse one that names nothing.
        known_cidrs = ({sg["cidr"] for sg in pit["ir_segment"]} | set(aview.segment_adds))
        for key, record in sorted(aview.host_adds.items()):
            if key in observed_keys:
                # Already discovered. The add's own CONTENT still applies — it reaches the node
                # through `host_fields` like any override — but nothing is created, and saying so
                # is better than silently treating a redundant add as a new host.
                aview.mark("add", record.kind, key)
                aview.ineffective(
                    f"add:{record.kind}", key,
                    "already observed in this PIT; the record's fields apply as overrides and no "
                    "host was created")
                continue
            cidr = record.fields.get("segment") or _derive_segment(key, known_cidrs)
            if cidr is None:
                # D11(a), ruled: required when it cannot be derived, refused loudly when neither.
                # A hard refusal rather than an `ineffective` line because it is not a resolution
                # miss — a host with no network is not a twin node under ANY PIT, so the file is
                # wrong rather than stale.
                raise ValueError(
                    f"add: host {key!r} names no `segment:` and none can be derived from its key "
                    f"(D11(a)). An address alone names no network; add `segment: <cidr>` to the "
                    f"record, or `add` the segment it sits on first. Known segments: "
                    f"{sorted(known_cidrs)}")
            if cidr not in known_cidrs:
                raise ValueError(
                    f"add: host {key!r} declares `segment: {cidr}`, which is neither an observed "
                    f"segment nor one this file adds. Add it with `- {{kind: segment, key: "
                    f"{cidr}, ...}}`, or correct the CIDR. Known segments: {sorted(known_cidrs)}")
            aview.mark("add", record.kind, key)
            asserted_hosts.append(_asserted_entity(key, cidr, record))
            asserted_host_keys.add(key)
        placed.extend(asserted_hosts)

        # Suppression (D18). `placed` before the filter is what `population.placed_hosts` keeps
        # meaning, so the identity still closes; the asserted hosts are counted separately
        # because a group of them reports `observed: 0` by construction.
        for key, record in sorted(aview.suppress.items()):
            in_pit = key in observed_keys or key in asserted_host_keys
            was_placed = any(e["entity_key"] == key for e in placed)
            if in_pit:
                aview.mark("suppress", key)
            else:
                aview.ineffective("suppress", key, "no entity with this key is in the PIT")
            suppressed_rows.append({"key": key, "note": record.note,
                                    "placed": was_placed, "matched": in_pit})
        if aview.suppress:
            placed = [e for e in placed if e["entity_key"] not in aview.suppress]

        # An override on an entity that is not a twin node changes nothing, and that is worth a
        # line rather than a shrug: `field: role` on an EXTERNAL peer is the mistake an operator
        # makes when they read a fingerprint report rather than the twin.
        for key, fields in sorted(aview.host_over.items()):
            if key in {e["entity_key"] for e in placed}:
                continue
            for name in sorted(fields):
                if key in aview.suppress:
                    aview.mark("override", key, name)
                    continue
                aview.ineffective(
                    "override:host", key,
                    "no PLACED host carries this key — it is external, unplaced, or absent from "
                    "this PIT, so the override has nothing to act on", field=name)

    windows = pit_row["windows"] or 0

    def _occupancy_of(entity) -> Optional[float]:
        """`windows_seen / windows`, or `None` when the question cannot be asked.

        `None` is a real answer, not a zero (`LES-006`): a PIT claiming no windows, or an entity
        carrying no `windows_seen`, has no denominator — and a host is never excluded for a
        denominator problem. Such a group is emitted with NO `observation:` block, which axRange
        reads as "always emitted", and the case is disclosed in `caveats`.

        A ratio outside [0, 1] is a contradiction rather than an absence, and is refused loudly
        (CONVENTIONS §3). `ir/materialize.py` pins `1 <= windows_seen <= windows` and both merge
        axes preserve it, so reaching this is a producer bug, not a shape this consumer should
        paper over — axRange's expander would refuse it one layer later anyway.
        """
        seen = entity["windows_seen"]
        if seen is None or windows < 1:
            return None
        occupancy = seen / windows
        if not 0.0 <= occupancy <= 1.0:
            raise ValueError(
                f"{entity['entity_key']}: windows_seen={seen} against a PIT of {windows} "
                f"window(s) gives occupancy {occupancy} outside [0, 1]")
        return occupancy

    # **ROLE-LESS HOSTS ARE EMITTED** (`docs/WORK_ORDER_deployment_scope.md` D4, Authority: user).
    # This reverses D5/D5a of `WORK_ORDER_visibility_and_vantage.md`, which dropped them: 130 of
    # 141 placed hosts on the CorpAPCN capture match no rule, and dropping them made "is every
    # observed host above the bar placed?" unanswerable — the cast had already removed the
    # evidence. They carry `UNROLED_ROLE`, which has an image and NO persona: present because it
    # was observed, generating nothing it was not observed generating. `roleless` is kept only as
    # accounting for the sidecar now — nothing is removed from `placed`.
    def _role_of(entity) -> str:
        """The role a node group is keyed on — the ASSERTED one where an operator supplied it.

        Applied before grouping, because the role IS part of the group key: a host asserted to be
        a domain controller has to land in a domain-controller group, not in the resolver group
        the census put it in with a footnote.
        """
        if aview is not None:
            asserted = aview.host_fields(entity["entity_key"]).get("role")
            if asserted is not None:
                return asserted[0]
        return entity["role"] or UNROLED_ROLE

    # `roleless` is accounting for the sidecar and is computed AFTER the assertions are known: a
    # host whose role an operator supplied is no longer role-less, and counting it as one would
    # report the vantage's gap as still open after it had been closed by hand.
    roleless = [e for e in placed if _role_of(e) == UNROLED_ROLE]

    # ACTIVITY BANDING (`docs/JOURNAL_corpapcn_fidelity.md` GAP-C12).
    #
    # Grouping on (role, segment) alone collapses a role's whole population into one node group
    # with a `count:`, so every member deploys identical and is driven by one persona derived
    # from the role's MEAN. That erases the single most consequential property of a real network:
    # its traffic is dominated by a few hosts. Measured on CorpAPCN, `generic-endpoint` spans
    # 102 hosts whose per-window bytes run from 0 to 2,862,768 — the busiest is **1,423x the
    # median**. Flattening that is why the twin reproduced the fan-out SHAPE well (TVD 0.055) and
    # the edge MIX badly (TVD 0.563): with no heavy hitters, `generic-endpoint -> web-tier` came
    # out 38 against the source's 298, and the 21+ peer bucket was empty.
    #
    # So a group is keyed on (role, band, segment). The band is derived from the entity's own
    # observed volume against its role's median — evidence, not a fixed ladder — and a role whose
    # population is genuinely uniform produces exactly one band, unchanged from before.
    from aximprint.synth.personas import activity_bands

    _bands = activity_bands(pit)

    def _band(entity) -> str:
        return _bands.get(entity["entity_key"], "")

    # PLUS THE OCCUPANCY RUNG (`docs/WORK_ORDER_deployment_scope.md` §9, Authority: user). The
    # deploy gate is all-or-nothing per node group, so a group whose members straddle the bar
    # cannot be gated correctly at all — one number has to stand for the whole group. The ladder
    # makes the group homogeneous by presence CLASS, and the block below then carries the group's
    # weakest member, so the gate compares an exact observed number and acts on whole groups.
    #
    # PLUS THE ASSERTION SIGNATURE (`docs/WORK_ORDER_reference_ingest.md` S2). A group's sidecar
    # rows publish ONE evidence class and ONE image/resources for the whole group, so a host
    # carrying an assertion cannot share a group with one that does not — the row would publish an
    # `imputed` member's role as `asserted`, or apply one host's asserted image to its neighbours.
    # The signature is `()` for every host of every cast compiled without a file, so the key, the
    # names and the bytes are unchanged when the mechanism is unused.
    #
    # An ADDED host takes `ASSERTED_RUNG` rather than a computed one: it has no `windows_seen`, so
    # there is no occupancy to place it on the ladder, and `UNMEASURED_RUNG` would file it under
    # "the denominator was missing" when the truth is that nothing observed it at all.
    groups: dict[tuple, list] = {}
    for entity in placed:
        rung = (ASSERTED_RUNG if entity["entity_key"] in asserted_host_keys
                else occupancy_rung(_occupancy_of(entity)))
        signature = aview.signature(entity["entity_key"]) if aview is not None else ()
        groups.setdefault(
            (_role_of(entity), _band(entity), _segment_of(entity), rung, signature), []
        ).append(entity)

    # THE INTERNET SINK (`docs/JOURNAL_corpapcn_fidelity.md` GAP-C17).
    #
    # After the placed/role filters above, deliberately: there is no observed entity behind a
    # sink node, so it must not be subject to a test about observed hosts. Before `live_cidrs`
    # below, equally deliberately: its segment only reaches the twin because a group sits on it.
    #
    # Only when the twin has hosts at all. Injecting a sink into an EMPTY twin would replace
    # "this filter left nothing" — a real, disclosed outcome — with a twin containing nothing
    # but 15 nodes for the absent hosts to talk to.
    #: Every segment a real, observed host group sits on — captured BEFORE the sink is injected
    #: so the collision guard below can ask "does a twin HOST end up on the sink's network?"
    #: without having to subtract the sink back out of its own answer.
    host_cidrs = {cidr for _role, _band, cidr, _rung, _sig in groups}
    sink_groups = []
    if groups:
        for hosts in SINK_BUCKETS:
            role = sink_sub_role(hosts)
            # An empty member list, not a list of placeholder entities. The count comes from
            # `hosts` below; inventing member dicts would put fabricated `role_confidence` and
            # `entity_key` values into a provenance sidecar whose whole job is to keep observed
            # and invented apart.
            # No rung: a synthetic group observes nothing, so it has no presence class to
            # belong to and (below) carries no `observation:` block either.
            groups[(role, "", SINK_CIDR, "", ())] = []
            sink_groups.append(role)

    # -- segments ---------------------------------------------------------------
    # Only segments a surviving node sits on. A declared segment with no node in it is a network
    # the twin would stand up empty; worse, axRange's expander ERRORS on a conduit whose segments
    # have no router leg, so an orphan silently becomes a deploy-time failure rather than an
    # unused row. Pruned here, counted, and named in the caveats — never dropped quietly.
    live_cidrs = {cidr for _role, _band, cidr, _rung, _sig in groups}
    # The sink has no `ir_segment` row — nothing observed it — so one is synthesized in the same
    # shape the real rows carry. Without it every `next(sg for sg in all_segments ...)` lookup
    # below raises StopIteration on the first sink node. `windows_present` is 0 against the
    # PIT's own total: the honest reading is "seen in none of them", not a fabricated presence.
    # IPv6 rows never reach the twin (see `_segment_of`); they are named in the caveats above
    # and must not be mistaken for the D7 emptiness prune below.
    ir_segments_v4 = [sg for sg in pit["ir_segment"] if _is_ipv4_network(sg["cidr"])]
    if sink_groups:
        all_segments = ir_segments_v4 + [{
            "cidr": SINK_CIDR, "name": SINK_SEGMENT_NAME,
            "windows_present": 0, "windows_total": windows,
            "coverage_source": "synthesized",
        }]
    else:
        all_segments = ir_segments_v4

    # ASSERTED SEGMENTS (`docs/WORK_ORDER_reference_ingest.md` S2). Two cases, and only the first
    # creates anything: a CIDR the PIT never observed becomes a synthesized row, and one it did
    # observe is left exactly as it is — the record's `name`/`zone_type` reach it through
    # `aview.segment_fields` below like any override, which is why an `add` of an
    # already-observed segment is a content edit rather than a duplicate.
    #
    # `windows_present`/`windows_total` are absent rather than zero, the same distinction the sink
    # does NOT make and for a different reason: the sink is a network nothing COULD see, while an
    # asserted segment is one the operator says exists and this vantage did not cover. Both carry
    # no `observation:` block; only the second could ever be measured by a wider capture.
    asserted_cidrs: set = set()
    if aview is not None:
        observed_cidrs = {sg["cidr"] for sg in all_segments}
        extra = []
        for cidr, record in sorted(aview.segment_adds.items()):
            aview.mark("add", "segment", cidr)
            if cidr in observed_cidrs:
                continue
            asserted_cidrs.add(cidr)
            extra.append({"cidr": cidr,
                          "name": record.fields.get("name") or segment_name(cidr),
                          "windows_present": None, "windows_total": None,
                          "coverage_source": None})
        all_segments = all_segments + extra
        # An asserted segment survives the emptiness prune below: the operator declared it, and a
        # segment declared into existence and then dropped for having no observed host would be a
        # silently ignored record — the failure the closed field vocabularies exist to prevent.
        live_cidrs = live_cidrs | asserted_cidrs
        for cidr in sorted(aview.segment_over):
            if cidr in live_cidrs or cidr in observed_cidrs:
                continue
            for name in sorted(aview.segment_over[cidr]):
                aview.ineffective(
                    "override:segment", cidr,
                    "no segment with this CIDR is in the twin — add it with `kind: segment`, or "
                    "correct the CIDR", field=name)
    all_segments = sorted(all_segments, key=lambda s: s["cidr"])

    # `relocate` — re-address the twin into a caller-named prefix.
    #
    # A cast reproduces the OBSERVED address space, which is right for a description and can be
    # fatal for a deployment: the twin has to be stood up somewhere, and a real network's CIDRs
    # may collide with the lab hosting it. Measured, not hypothetical
    # (`docs/JOURNAL_corpapcn_fidelity.md` GAP-C4): a CorpAPCN cast produced segments
    # 10.80.64.0/24 and 10.80.72.0/24 while the target cluster itself lives in 10.80.72.0/20 and
    # its default gateway is 10.80.64.1 — so the twin's router built a LAN leg carrying its own
    # upstream gateway's address, and every guest lost egress. Nothing in the capture could have
    # revealed that; it is a property of the destination, which is why this is opt-in and named
    # by the caller rather than guessed.
    #
    # Deterministic and order-stable: segments are already sorted by CIDR, so segment i becomes
    # <prefix-first-two-octets>.<i>.0/24 and the HOST octet is preserved, which keeps a twin
    # address readable against its source (10.80.64.2 -> 10.200.4.2). The original is recorded in
    # provenance per segment, so relocation moves the address, never loses it.
    reloc: dict[str, str] = {}
    if relocate:
        base = relocate.split("/")[0].split(".")
        # The sink is excluded, which is what makes its address FIXED (see `SINK_CIDR`). It
        # must also not consume a cursor index, or every real segment would shift by one the
        # day the sink was introduced and two otherwise-identical casts would stop comparing.
        live_sorted = [sg for sg in all_segments
                       if sg["cidr"] in live_cidrs and sg["cidr"] != SINK_CIDR]
        if len(live_sorted) > 256:
            raise ValueError(
                f"relocate: {len(live_sorted)} segments exceed the 256 a /16 can carry as /24s"
            )
        for i, sg in enumerate(live_sorted):
            reloc[sg["cidr"]] = f"{base[0]}.{base[1]}.{i}.0/24"

    def _cidr(observed: str) -> str:
        return reloc.get(observed, observed)

    # The cursor grows from index 0 and this capture has 26 live segments, so the sink is 228
    # away today — and "far away" is not a guarantee. Index 254 is squarely INSIDE the range the
    # guard above permits (it refuses at >256, so 0..255 are all reachable), which makes this a
    # live path a wider capture reaches rather than a theoretical one: a 255-segment cast walks
    # a real segment straight onto the sink's address and puts twin hosts on the network the
    # sink's containment argument assumes is synthetic. Same outcome from a caller relocating
    # into some other /16 that happens to contain it. Refused loudly rather than sorted by
    # precedence: a collision here is a wrong twin, not a naming inconvenience.
    if sink_groups:
        collided = sorted(c for c in host_cidrs if _cidr(c) == SINK_CIDR)
        if collided:
            raise ValueError(
                f"the internet sink's segment {SINK_CIDR} collides with live segment(s) "
                f"{collided}. The sink must sit on a network no twin host does; pick a "
                f"different --relocate prefix, or move SINK_CIDR.")


    segments, pruned_segments, unmeasured_segments = [], [], []
    asserted_segment_names: list[str] = []
    for seg in all_segments:
        if seg["cidr"] not in live_cidrs:
            pruned_segments.append(seg["cidr"])
            continue
        i = len(segments)
        # A segment's two overridable fields (D12), resolved before the row is built so the
        # emitted document and its provenance describe the same value.
        seg_asserted = aview.segment_fields(seg["cidr"]) if aview is not None else {}
        name_value, name_record = seg_asserted.get("name", (seg["name"], None))
        zone_value, zone_record = seg_asserted.get("zone_type", (ZONE_TYPE, None))
        seg_entry = {"name": name_value, "cidr": _cidr(seg["cidr"]), "zone_type": zone_value}
        # PROVENANCE IN THE DOCUMENT (`docs/design/topology-layers.md` §3.1): one word
        # summarising the sidecar. An operator's mark wins; then an asserted segment (added, or
        # with a field overridden); the sink's segment is the compiler's placeholder; a segment
        # present in fewer than half the windows is the cast's own weak-evidence verdict.
        seg_marked = aview.mark_source([seg["cidr"]]) if aview is not None else None
        seg_occ = _segment_occupancy(seg)
        if seg_marked is not None:
            seg_source = seg_marked
        elif seg["cidr"] in asserted_cidrs or seg_asserted:
            seg_source = ASSERTIONS_EVIDENCE_CLASS
        elif seg.get("coverage_source") == "synthesized":
            seg_source = "unconfirmed"
        elif seg_occ is not None and seg_occ < _RUNG_LADDER[-2][1]:
            seg_source = "unconfirmed"
        else:
            seg_source = "discovered"
        seg_entry["source"] = seg_source
        if seg_source in ("discovered", "unconfirmed") and seg_occ is not None \
                and seg.get("coverage_source") != "synthesized":
            seg_entry["confidence"] = seg_occ
            if pit_row.get("vantage"):
                seg_entry["vantage"] = pit_row["vantage"]
        segments.append(seg_entry)
        if name_record is not None:
            aview.mark("override", seg["cidr"], "name")
            # `name` has no provenance row on a segment nobody asserted, and gains one only here.
            # Deliberate rather than an oversight: `ir_segment.name` is DERIVED
            # (`windowing/segments.py::segment_name` on the CIDR), so the class it would carry is
            # `compiler` on every segment of every cast, and emitting 32 rows saying so would
            # churn a cross-repo artifact to publish a constant. What is worth a row is the one
            # case where somebody chose the name.
            prov(f"twin_config.segments[{i}].name", ASSERTIONS_EVIDENCE_CLASS,
                 f"{ASSERTIONS_SCHEMA} segment.name",
                 **_asserted_prov(name_record, "name", source=aview.source,
                                  beaten={"class": COMPILER_CLASS, "value": seg["name"]}))
        # THE SEGMENT'S OBSERVATION BLOCK (`docs/WORK_ORDER_deployment_scope.md` D2/§4).
        #
        # `windows_present` against the PIT's own window total, and NO `role_confidence`: a
        # segment has no role, and `LES-006` is explicit that "no evidence" and "evidence of
        # none" are different claims — `null` would be a value. Absent is the honest shape, and
        # axRange reads absent as not-applicable.
        #
        # The sink's segment carries none at all (D5): nothing observed it, and a block of zeros
        # would say it was never SEEN, which is a different claim from nothing could see it.
        # `no block => always emitted` is exactly the reading that makes that safe.
        #
        # Weaker evidence than a node's, and worth knowing at the gate: `windows_present`'s
        # `coverage_source` is `inferred_from_traffic` — a proxy for coverage, never a
        # measurement (`ir/schema.py::COVERAGE_SOURCES`).
        if seg["cidr"] in asserted_cidrs:
            # An ASSERTED segment: no observation block, and NOT counted as unmeasured. Nothing
            # observed it, so there is no occupancy to compute rather than a denominator that went
            # missing — filing it under the unmeasured caveat would be a true sentence about the
            # wrong segments.
            asserted_segment_names.append(seg["cidr"])
            add_record = aview.segment_adds[seg["cidr"]]
            prov(f"twin_config.segments[{i}].cidr", ASSERTIONS_EVIDENCE_CLASS,
                 f"{ASSERTIONS_SCHEMA} add:segment",
                 **_asserted_prov(add_record, "key", source=aview.source),
                 reason="declared by an operator: this vantage carries no evidence of the "
                        "segment at all, so it is present because somebody said so and carries "
                        "no `observation:` block")
        elif seg["cidr"] != SINK_CIDR:
            occupancy = _segment_occupancy(seg)
            if occupancy is None:
                unmeasured_segments.append(seg["cidr"])
            else:
                segments[-1]["observation"] = {
                    "windows_seen": int(seg["windows_present"]),
                    "windows_total": int(seg["windows_total"]),
                    "occupancy": occupancy,
                }
                prov(f"twin_config.segments[{i}].observation", "observed",
                     "ir_segment.windows_present / .windows_total",
                     coverage_source=seg["coverage_source"],
                     note="windows_present is a PROXY for segment coverage (its coverage_source "
                          "is inferred_from_traffic), so this block is weaker evidence than a "
                          "node's; role_confidence is absent because a segment has no role")
        if seg["cidr"] in asserted_cidrs:
            pass                      # its `.cidr` row is written in the branch above
        elif seg["cidr"] == SINK_CIDR:
            # `COMPILER_CLASS`, never `observed`: nothing in any capture saw this network, and the
            # relocation branch below would otherwise label it `observed` for the wrong reason
            # (it is not in `reloc` — because it is deliberately never relocated).
            #
            # An INVENTION, and therefore no `transformation` record: there is no
            # pre-transformation value, because nothing was transformed. That is the distinction
            # `declared_transformation` exists to keep visible — see the relocation branch below.
            prov(f"twin_config.segments[{i}].cidr", COMPILER_CLASS,
                 "synth/compiler.py SINK_CIDR",
                 windows_present=0, windows_total=seg["windows_total"],
                 coverage_source=seg["coverage_source"],
                 reason="synthetic: the internet sink's own segment (GAP-C17). It stands in for "
                        "every destination outside the twin, which is 85% of this source's "
                        "edges, and is fixed rather than relocated so its containment argument "
                        "does not have to be re-made per deployment prefix.")
        elif seg["cidr"] in reloc:
            # A DECLARED DEPLOYMENT TRANSFORMATION, not a precedence event (§5.6; axRange
            # `docs/design/14-operator-assertions.md` §2). The CIDR was genuinely observed; the
            # compiler then MOVED it so the twin can stand up somewhere that does not collide
            # with the deployment target's own address space (GAP-C4, measured). The class stays
            # `COMPILER_CLASS` — the emitted value is this platform's, not the network's — but it
            # never entered `resolve_precedence`, and the `transformation` record carries the
            # pre-transformation reading so a scorecard reads the row as *reproduced under a
            # declared transformation* rather than as a mismatch.
            prov(f"twin_config.segments[{i}].cidr", COMPILER_CLASS,
                 "synth/compiler.py --relocate",
                 windows_present=seg["windows_present"], windows_total=seg["windows_total"],
                 coverage_source=seg["coverage_source"],
                 transformation=declared_transformation(
                     "relocate",
                     replaced=Fact("observed", seg["cidr"],
                                   signal="ir_segment.cidr"),
                     reason="relocated to avoid colliding with the deployment target's own "
                            "address space",
                     to=_cidr(seg["cidr"])))
        else:
            prov(f"twin_config.segments[{i}].cidr", "observed", "ir_segment.cidr",
                 windows_present=seg["windows_present"], windows_total=seg["windows_total"],
                 coverage_source=seg["coverage_source"])
        if zone_record is None:
            prov(f"twin_config.segments[{i}].zone_type", COMPILER_CLASS,
                 "synth/compiler.py ZONE_TYPE",
                 reason="no VLAN evidence exists at this vantage")
        else:
            # D11(c)'s worked example. The compiler's own `ZONE_TYPE` is what it would have been,
            # so it goes in `overrides` — and note that a `compiler` value appearing THERE is not
            # a masquerade: `overrides` answers "what would this have been", the compiler's
            # constant genuinely is that answer, and it never passed through `evidence_rank`,
            # which refuses the class outright.
            aview.mark("override", seg["cidr"], "zone_type")
            prov(f"twin_config.segments[{i}].zone_type", ASSERTIONS_EVIDENCE_CLASS,
                 f"{ASSERTIONS_SCHEMA} segment.zone_type",
                 **_asserted_prov(zone_record, "zone_type", source=aview.source,
                                  beaten={"class": COMPILER_CLASS, "value": ZONE_TYPE}))

    # -- nodes ------------------------------------------------------------------
    nodes, capped, population, unmeasured_nodes = [], [], [], []
    #: entity key -> the node group it compiled into, and the group's emitted count: what the
    #: link and tap records below resolve their device ends against.
    node_name_of_key: dict[str, str] = {}
    node_count_of_name: dict[str, int] = {}
    #: `(name, observed_count)` for a domain-promoting group clamped to ONE forest root — see the
    #: clamp in the node loop below. Emitted as a caveat, and the observed count is kept in the
    #: `population` sidecar, so the second DC is disclosed, never silently dropped.
    promoter_clamped: list[tuple[str, int]] = []
    #: Node groups whose members were ASSERTED into existence, and therefore carry no
    #: `observation:` block for a different reason than an unmeasured one does.
    asserted_nodes: list[str] = []
    #: `(node, beaten_class, beaten_value)` — a standing assertion this PIT now contradicts (D17).
    drifted: list[tuple] = []
    #: `(node, [prior roles])` — an asserted group whose members disagreed about what they were
    #: before, so no single runner-up could be named.
    ambiguous_role_groups: list[tuple] = []
    #: Node groups that PROMOTE a domain (`install: [{role: adds}]`). Collected in the node loop
    #: and read after it by the `joins:`/`identity:` blocks below, because "is there a domain in
    #: this twin at all" cannot be answered until every group has been emitted — and authoring a
    #: `joins:` against a forest no node stands up is a deploy that fails at the join, not at
    #: validation.
    domain_nodes: list[str] = []
    # The sort gains the rung, and gains it as a LADDER RANK rather than as its name (D8): the
    # total order stays a property of the ladder's declared order, so renaming a rung can never
    # silently reorder the emitted document. Name is the final tiebreak so the key is total.
    # The sort key gains the ASSERTION SIGNATURE as its last component, so the total order stays
    # total once one (role, band, cidr, rung) can hold several groups. Nothing else about the
    # order changes: with no assertions every signature is `()` and the key compares exactly as
    # it did.
    node_names: dict[str, int] = {}
    for (role, band, cidr, rung, signature), members in sorted(
            groups.items(),
            key=lambda kv: (kv[0][2], kv[0][0] or "", kv[0][1], _rung_rank(kv[0][3]), kv[0][3],
                            kv[0][4])):
        rule = _rule_for(role)
        content = ({"image": rule.image, "resources": rule.resources, "install": list(rule.install)}
                   if rule else dict(DEFAULT_NODE, install=list(DEFAULT_NODE["install"])))
        group_record = None  # set below for an observed group an operator split (`group`)
        # Content assertions (D12: `image`, `resources`). Applied to the group as a whole, which
        # is safe precisely BECAUSE the signature is in the group key: every member of this group
        # carries the same assertion, so there is no host here inheriting a neighbour's hand-edit.
        # `dict(signature)` is the same content in a comparable form; the records themselves are
        # needed for the notes, so the lookup goes through `host_fields` on any member.
        asserted_fields = (aview.host_fields(members[0]["entity_key"])
                           if aview is not None and members else {})
        for _field_name in ("image", "resources"):
            if _field_name in asserted_fields:
                content[_field_name] = asserted_fields[_field_name][0]
        # `rule` is the row looked up BY NAME, and it is the right source for CONTENT: every row
        # of a role carries identical `image`/`resources`/`install` (`_rule_for`'s own
        # docstring). It is the WRONG source for PROVENANCE, because a role name maps to several
        # rows with DIFFERENT evidence. The two are therefore derived separately and
        # deliberately — `matched` re-runs the census's own `match_role` over the members' port
        # evidence, so the sidecar names the row that actually fired.
        matched = _matched_evidence(role, members)
        synthetic = role in SYNTHETIC_ROLES
        if synthetic:
            # Named by role alone. The segment suffix carries no information for a node group
            # that only ever sits on one segment — and it is also DEFENCE IN DEPTH against
            # `_node_suffix`'s `rstrip(".0")`, which eats a run of trailing zeros rather than
            # one octet and so collapses any round third octet onto its shorter siblings
            # (`.200` -> `10-200-2`, shared with relocation indices 2 and 20). `SINK_CIDR`
            # avoids that outright by not ending in a zero, so this is the second lock, not the
            # first — but it is what keeps the sink's names stable if that constant ever moves.
            name = role
            # The `count:` IS the fan-out mechanism (`SINK_BUCKETS`): capping
            # `internet-sink-8` to 4 would silently halve every persona that emits at it,
            # while `max_count` exists to bound how much of an OBSERVED population is
            # deployed. There is no observed population here to bound.
            count = int(role.rsplit("-", 1)[1])
        else:
            # The band is part of the NODE NAME, so a heavy hitter is visible as such in the
            # twin config rather than hidden inside a count — and so is the RUNG, for the same
            # reason: a transient population is visible as such rather than buried in a block
            # nobody reads. `role` is never empty now (a role-less host carries `UNROLED_ROLE`),
            # so there is no `'host'` fallback left to take.
            name = (f"{role}{'-' + band if band else ''}-{_node_suffix(cidr)}-{rung}")
            # THE OBSERVED SHORT HOSTNAME, where the wire gave one and the group is one host.
            #
            # `axrange/docs/WORK_ORDER_protocol_faithful_services.md` §10.1 D4's implementation
            # reading, ruled by the user: *"the DC's short hostname may still follow the observed
            # `axdc01`; only the realm is replaced."* It matters more than cosmetics for this one
            # role: axRange sets a node's guest hostname from its NAME, and a domain controller's
            # hostname is on the wire in every Kerberos, LDAP and SMB frame the twin emits — the
            # exact strings a detection rule reads. `domain-controller-10-80-2-core` would put a
            # compiler-generated identifier there in place of one the capture supplied.
            #
            # SCOPED to `domain-controller`, one member, and a hostname-keyed member, all three.
            # Widening it to every single-member group is a defensible follow-on and is NOT taken
            # here: a node name is addressed by `net:`, by every `tfc` assignment and by the
            # installer's instance expansion, so a rename is a blast radius that should be bought
            # for a stated reason rather than as a side effect.
            if (role == "domain-controller" and len(members) == 1
                    and str(members[0].get("entity_key", "")).startswith("hostname:")):
                observed_name = str(members[0]["entity_key"]).split(":", 1)[1]
                if observed_name:
                    name = observed_name
            # An OVERRIDE of an observed host is not visible in the rung (the host was observed,
            # so it keeps its real presence class), and two groups of one (role, band, segment,
            # rung) would otherwise share a name — an invalid twin config, since `net:` and every
            # `tfc` assignment address a node by name. An ADDED host already carries
            # `ASSERTED_RUNG` in its name and needs no second marker.
            if signature and rung != ASSERTED_RUNG:
                name = f"{name}-{ASSERTED_MARKER}"
            # A MEMBER SPLIT (`docs/design/topology-layers.md` §5): an asserted `group` names
            # the node group these hosts form. The signature already keeps them apart from the
            # group their role, segment and presence would put them in; this gives it the name.
            group_record = asserted_fields.get("group") if asserted_fields else None
            if group_record is not None:
                name = group_record[0]
                for member in members:
                    aview.mark("override", member["entity_key"], "group")
            # Deterministic disambiguation for the case the marker alone does not settle: two
            # DIFFERENT assertions on hosts that would otherwise group together (two images, say).
            # The iteration order above is total, so the suffix is a property of the content
            # rather than of dict order (`LES-009`).
            if name in node_names:
                node_names[name] += 1
                name = f"{name}-{node_names[name]}"
            else:
                node_names[name] = 1
            count = len(members)
            if max_count is not None and count > max_count:
                capped.append((name, count, max_count))
                count = max_count
        clamped_to_root = False        # set True when a count>1 domain promoter is cut to one root
        node = {"name": name, "image": content["image"],
                # `_cidr(cidr)`, not `cidr`: `segments[]` carries the relocated value, while
                # the group key is the OBSERVED one. Comparing the two raises StopIteration on
                # the first node of any relocated cast.
                "net": next(s["name"] for s in segments if s["cidr"] == _cidr(cidr))}
        if role:
            node["role"] = role
        if content["resources"]:
            node["resources"] = content["resources"]
        if content["install"]:
            node["install"] = content["install"]
        # A DEVICE (`docs/design/topology-layers.md` §2.2): an asserted `device_kind` makes the
        # group a switch or a router. A switch's attachment is an L2 port (no address), and
        # without an asserted image it carries none -- it then deploys as nothing but the vnets
        # its links carry (§2.5, decision 3). `host` turns a device back into a host.
        device_record = asserted_fields.get("device_kind") if asserted_fields else None
        device_kind = device_record[0] if device_record is not None else None
        if device_record is not None:
            for member in members:
                aview.mark("override", member["entity_key"], "device_kind")
        if device_kind in ("switch", "router"):
            node["kind"] = device_kind
            if device_kind == "switch":
                node["nets"] = [{"segment": node.pop("net"), "l2": True}]
                if "image" not in asserted_fields:
                    node.pop("image", None)
                    node.pop("install", None)
        # `provides: [{domain: ...}]`, and it is REQUIRED rather than decorative: axRange's
        # `pass1_emit` errors on a `role: adds` install action with no domain to promote into
        # (`axrange/planner/pass1_emit.py`). Emitted from the install action rather than from the
        # role name, so a future catalog row that promotes a differently-named role gets it too.
        #
        # THE LAB REALM (`services.LAB_REALM`), not the observed one — D4, Authority: user. That
        # is a `compiler`-class value in the strict sense: what the platform chose where the wire
        # said nothing it was willing to reuse. When the observed realm lands as a first-class IR
        # fact (reference-ingest S5) this becomes a `declared_transformation` with the observed
        # value recorded beside it; today there is no observed realm at the IR boundary to record
        # as replaced, and inventing one to "replace" would be the fabrication the class model
        # exists to prevent.
        if any(isinstance(a, dict) and a.get("role") == "adds"
               for a in (content["install"] or [])):
            node["provides"] = [{"domain": LAB_REALM}]
            domain_nodes.append(name)
            # ONE forest root per realm. axRange promotes a NEW AD DS forest for every `role: adds`
            # host and emits a `domain-ready[<fqdn>]` barrier for each (`axrange/planner/
            # pass1_emit.py`), so two instances of one realm collide on that op_id and the plan is
            # refused. A real network's second DC is a REPLICA of the SAME forest, which axRange
            # has no op to stand up — so the group deploys a single root. The observed count is
            # preserved in the `population` sidecar (`observed`), and a caveat says so, so the
            # extra DC is DISCLOSED rather than silently dropped (CONVENTIONS §3).
            if count > 1:
                promoter_clamped.append((name, count))
                count = 1
                clamped_to_root = True
        if count > 1:
            node["count"] = count
        # THE DECLARED LISTEN SET (`axrange/docs/WORK_ORDER_protocol_faithful_services.md` §6.2b,
        # D2(b) ruled by the user: wire the already-present-but-inert `App.listen`, plus a
        # `listen:` passthrough on `TwinConfigNode`).
        #
        # **From the OBSERVED listen census, never from the persona dial set** (§2). That is the
        # whole correction: the echo responder derived each role's bound set from every port some
        # persona DIALS at that role, so `internet-sink-1` bound 88/tcp while the `web-tier` nodes
        # bound nothing at all. A host declares what it was seen ANSWERING on.
        #
        # A UNION across the group's members, which is the honest granularity: axRange fans a
        # group out into `count` identical clones, so its members are one host as far as the twin
        # is concerned. A union across a ROLE would not be — this source's four `dns-resolver`
        # hosts answer on 1, 2 and 9 ports respectively, and a role-level set would make three of
        # them serve six ports nothing ever saw them serve.
        #
        # A SINK group carries nothing here: it has no members and no census, and its own set is
        # filled in by the caller from what it stands in for (§7.4). Absent, not empty, so the two
        # states stay distinguishable.
        listens = listen_specs(members)
        if listens:
            node["listen"] = [{"port": p, "proto": t}
                              for p, t in (parse_port(s) for s in listens)]
        # THE NODE'S OBSERVATION BLOCK (`docs/WORK_ORDER_deployment_scope.md` D2/D5/§9).
        #
        # Compiler-class nodes carry NONE — the gateway below and the sink groups here. They
        # observe nothing, and a block of zeros would assert they were never SEEN rather than
        # that nothing could see them (`LES-006`). axRange reads "no block" as always-emitted,
        # which is what makes that absence safe rather than a gap.
        #
        # The group carries its **weakest member**, not an aggregate. The gate is all-or-nothing
        # per node group, so a group is kept or dropped by the member closest to the bar — the
        # same reading `occupancy` and `role_confidence` must both take, or a group could pass a
        # confidence gate carrying a member below it. That deliberately differs from the
        # `.role` provenance below, which reports the STRONGEST evidence behind the role LABEL:
        # two different questions, and naming both here is what keeps the difference deliberate.
        group_is_asserted = rung == ASSERTED_RUNG
        if group_is_asserted:
            # An ADDED host observes nothing, so it carries no `observation:` block for the same
            # reason the gateway and the sink do — and it is NOT "unmeasured": there is no
            # denominator missing, there is no measurement to have. axRange reads "no block" as
            # always emitted, which is the right reading for a host an operator declared.
            asserted_nodes.append(name)
        elif not synthetic:
            occupancies = [_occupancy_of(m) for m in members]
            if any(o is None for o in occupancies):
                unmeasured_nodes.append(name)
            else:
                worst = min(range(len(members)), key=lambda k: occupancies[k])
                block = {
                    "windows_seen": int(members[worst]["windows_seen"]),
                    "windows_total": int(windows),
                    "occupancy": occupancies[worst],
                }
                # ABSENT, not null, on a role-less node: `UNROLED_ROLE` is what a host gets when
                # nothing matched, so there is no confidence in a role to report (§4).
                #
                # And absent, too, when ANY member lacks one — a `min` over the members that
                # happen to carry a value would report a complete reading of a partial one, the
                # inverse of `LES-127`'s failure and just as invisible at the consumer. All or
                # nothing, exactly as the occupancy half above.
                #
                # ABSENT, too, when the group's role is ASSERTED. `role_confidence` is the
                # census's confidence in the role it inferred, and this group no longer carries
                # that role — publishing 0.7 beside `role: domain-controller` would report a
                # confidence in a claim nobody made. The assertion's own 1.0 belongs to the
                # `.role` provenance row, not to an observation block, which is a statement about
                # what the capture SAW.
                confidences = [m["role_confidence"] for m in members]
                role_asserted = bool(asserted_fields) and "role" in asserted_fields
                if (role != UNROLED_ROLE and not role_asserted and confidences
                        and all(c is not None for c in confidences)):
                    block["role_confidence"] = min(confidences)
                node["observation"] = block
        # PROVENANCE IN THE DOCUMENT (`docs/design/topology-layers.md` §3.1). An operator's
        # confirm / doubt mark on any member wins; an asserted group (added, or carrying an
        # override) is `asserted`; the sink is the compiler's placeholder; a transient or
        # unmeasured group is the cast's own weak-evidence verdict; everything else was
        # discovered, and says how firmly (its occupancy) and from where (the vantage).
        member_keys = [m["entity_key"] for m in members]
        marked_source = aview.mark_source(member_keys) if aview is not None else None
        if marked_source is not None:
            node_source = marked_source
        elif group_is_asserted or signature:
            node_source = ASSERTIONS_EVIDENCE_CLASS
        elif synthetic:
            node_source = "unconfirmed"
        elif rung == _RUNG_LADDER[-1][0] or name in unmeasured_nodes:
            node_source = "unconfirmed"
        else:
            node_source = "discovered"
        node["source"] = node_source
        if node_source in ("discovered", "unconfirmed") and "observation" in node:
            node["confidence"] = node["observation"]["occupancy"]
            if pit_row.get("vantage"):
                node["vantage"] = pit_row["vantage"]
        idx = len(nodes)
        nodes.append(node)
        for member in members:
            node_name_of_key[member["entity_key"]] = name
        node_count_of_name[name] = count
        if device_record is not None:
            prov(f"twin_config.nodes[{idx}].kind", ASSERTIONS_EVIDENCE_CLASS,
                 f"{ASSERTIONS_SCHEMA} host.device_kind",
                 **_asserted_prov(device_record[1], "device_kind", source=aview.source),
                 reason="declared by an operator: this vantage carries no device evidence "
                        "(a switch is a MAC on a port at best)")
        if group_record is not None:
            prov(f"twin_config.nodes[{idx}].name", ASSERTIONS_EVIDENCE_CLASS,
                 f"{ASSERTIONS_SCHEMA} host.group",
                 **_asserted_prov(group_record[1], "group", source=aview.source),
                 reason="a member split: these hosts are a separate group by the operator's say")
        # D6: observed vs emitted, per group, as structured data rather than only as prose in a
        # caveat. A consumer reconstructing the real population from the twin alone needs numbers
        # it can add up — a twin that silently under-represents its network is the same failure
        # as a redacted field (CONVENTIONS §3).
        # `observed: 0` for a sink group, and it must stay 0: the sidecar's reconstruction
        # identity is `placed_hosts == sum(observed)`, and a synthetic group claiming 8 observed
        # hosts would break it — quietly inflating the network this twin says it found by 15
        # hosts that never existed.
        # An ASSERTED group reports `observed: 0` and `asserted: <n>`, exactly like a synthetic
        # one reports `observed: 0`: the reconstruction identity counts hosts the capture placed,
        # and a group of hosts an operator declared would inflate the network this twin claims to
        # have FOUND. The two counts sit side by side so nothing has to be re-derived.
        population.append({"node": name, "role": role, "segment": cidr,
                          **({} if synthetic else {"rung": rung}),
                          "observed": 0 if (synthetic or group_is_asserted) else len(members),
                          **({"asserted": len(members)} if group_is_asserted else {}),
                          "emitted": count,
                          "capped_by": "single_forest_root" if clamped_to_root
                                       else "max_count" if count != len(members) and not synthetic
                                       else None,
                          **({"synthesized": "internet-sink"} if synthetic else {})})
        if synthetic:
            prov(f"twin_config.nodes[{idx}]", COMPILER_CLASS,
                 "synth/personas.py SINK_BUCKETS",
                 reason=f"synthetic: part of the internet sink (GAP-C17). {count} host(s) "
                        f"holding role {role!r}, which exists so personas can aim the 85% of "
                        f"this source's conversation that leaves the network at something "
                        f"INSIDE the twin. Sizes are binary (1/2/4/8) so a persona composes "
                        f"any external fan-out in 1..15 from `peer_scope: all` emits.")
        else:
            # `confidence` on `.net` is the SEGMENT binding's own score, carried up from
            # `ir_entity.segment_confidence` — a producer-supplied number, so it is publishable
            # (D7). `min` over the members and ALL-OR-NOTHING, exactly like the observation
            # block's `role_confidence`: a `min` over only the members that happen to carry one
            # would report a complete reading of a partial one (`LES-127`).
            seg_confidences = [m.get("segment_confidence") for m in members]
            seg_conf = (min(seg_confidences)
                        if seg_confidences and all(c is not None for c in seg_confidences)
                        else None)
            # An ASSERTED placement must not be published as `observed` — that is the masquerade
            # the whole class model exists to prevent, and it is the easiest one to ship by
            # accident here: the group key already carries the effective segment, so the row's
            # VALUE is right while its class describes where the value came from before the
            # override. Caught by asking for the `.net` row of a segment-overridden group; nothing
            # in the emitted twin config would have shown it.
            #
            # The beaten reading is the members' own observed segment, named when they agree on
            # one and degraded when they do not (`LES-140`). `seg_conf` belongs to that beaten
            # reading, not to the assertion, so it rides in `overrides` rather than on the row.
            net_record = asserted_fields.get("segment") if asserted_fields else None
            if net_record is None:
                prov(f"twin_config.nodes[{idx}].net", "observed", "ir_entity.segment",
                     segment=cidr,
                     **({"confidence": seg_conf} if seg_conf is not None else {}))
            else:
                for member in members:
                    aview.mark("override", member["entity_key"], "segment")
                observed_segments = {m["segment"] for m in members
                                     if m["segment"]
                                     and m["entity_key"] not in asserted_host_keys}
                beaten = None
                if len(observed_segments) == 1:
                    beaten = {"class": "observed", "value": next(iter(observed_segments))}
                    if seg_conf is not None:
                        beaten["confidence"] = seg_conf
                drift_hit = (beaten is not None and pit_is_later
                             and beaten["value"] != cidr)
                if drift_hit:
                    beaten = {**beaten, "drift": {"class": beaten["class"],
                                                  "value": beaten["value"], "pit_id": twin_id}}
                    drifted.append((name, beaten["class"], beaten["value"]))
                prov(f"twin_config.nodes[{idx}].net", ASSERTIONS_EVIDENCE_CLASS,
                     f"{ASSERTIONS_SCHEMA} host.segment", segment=cidr,
                     **_asserted_prov(net_record[1], "segment", source=aview.source,
                                      beaten=beaten))
            # `members` names the group's observed entity keys (D10's `<key_kind>:<value>`
            # form), sorted, so a consumer that wants to assert on ONE host of this group --
            # the design canvas's promote-to-assertion -- can address it by the key the
            # assertions seam joins on. A twin-config node NAME is a group name
            # (`role-band-segment-rung`) and joins against nothing. Empty for a synthetic
            # group: it observed no one.
            prov(f"twin_config.nodes[{idx}].count", "observed", "ir_entity (placed members)",
                 hosts_observed=len(members),
                 members=sorted(str(m["entity_key"]) for m in members))
            if "listen" in node:
                # `observed` on an ordinary group, `asserted` on one an operator declared into
                # existence (`_asserted_entity` carries the record's own `listen:` through
                # `listen_ports`). Reported per group rather than per port because a group is
                # homogeneous in evidence class by construction — the assertion signature is in
                # the group key.
                prov(f"twin_config.nodes[{idx}].listen",
                     ASSERTIONS_EVIDENCE_CLASS if group_is_asserted else "observed",
                     f"{ASSERTIONS_SCHEMA} host.listen" if group_is_asserted
                     else "ir_entity.listen_ports",
                     ports=[f"{e['port']}/{e['proto']}" for e in node["listen"]],
                     reason="the observed listen census, unioned across this group's members. "
                            "NOT the persona dial set: a host declares what it was seen "
                            "ANSWERING on, which is what the server half is driven by "
                            "(axrange protocol-faithful-services work order §2).")
            if role == UNROLED_ROLE and not (asserted_fields and "role" in asserted_fields):
                # `COMPILER_CLASS`, not `imputed`: nothing inferred this role. It is what a
                # COMPILER supplies when no evidence rule matched — the same class as `image` —
                # and calling it `imputed` would claim an inference that never happened. NO
                # `confidence`: there is no inference to be confident about (`LES-006`).
                # `source` and `signal` coincide here, and only here: the `default:` block is not
                # a catalog ROW, so there is nothing more specific for `signal` to name. Kept
                # rather than deduplicated, because `source` is the field
                # `docs/WORK_ORDER_fidelity_scorecard.md` §8.4 reads on these rows.
                prov(f"twin_config.nodes[{idx}].role", COMPILER_CLASS,
                     "catalog/roles.yaml (default block)",
                     source="catalog/roles.yaml (default block)",
                     reason="no evidence rule matched any member; the node is present because "
                            "it was OBSERVED, and carries no persona (D4)")
            else:
                # A role is INFERRED from listening ports, never observed. Carrying that through
                # to the twin's own provenance is the point of the whole chain. `max`, unlike
                # the observation block's `min`: this reports the strongest evidence behind the
                # LABEL, while the block reports the weakest member a gate must not admit.
                #
                # `signal` names the catalog ROW THAT MATCHED — which ports made this role
                # match is what a reader of the Console's provenance table wants, and naming a
                # DIFFERENT row of the same name is a fabricated evidence claim inside the one
                # artifact whose job is to keep observed and invented apart. See
                # `_matched_evidence` for why the name lookup cannot answer this and for the
                # 70-of-122 measurement that settled it. Pinned by `(role, requires)` rather
                # than by index: a catalog gains rows between writings (`LES-136`).
                #
                # ROUTED THROUGH THE RESOLVER, with a one-element list. That is not ceremony:
                # `role` is the first field an assertions file will override (axRange
                # `docs/design/14-operator-assertions.md` §3's own worked example is exactly
                # this row), and a resolver nothing calls is a rule nobody has to keep working
                # — `LES-101`'s "documentation in a data costume", one layer over. Calling it
                # on the single-fact case makes it live code today, exercised by every cast,
                # and turns S2's change into "append a `Fact` to this list" rather than "start
                # calling a function that has never run". Output is byte-identical while there
                # is one fact: `resolve_precedence` returns no `overrides` for an uncontested
                # reading.
                #
                # **S2 is where that second `Fact` arrives.** An asserted role beats the census's
                # `imputed` one, and the beaten reading is each member's PRE-override role — the
                # answer a reader wants ("it would have been a dns-resolver"), not the winner
                # restated. When the members disagree about what they were before, no single
                # runner-up can be named and the imputed fact is dropped rather than one member's
                # reading published for the group: the coarser true statement beats a specific
                # plausible wrong one (`LES-140`, `LES-006`).
                role_record = (asserted_fields.get("role") if asserted_fields else None)
                role_facts, ambiguous = _pit_role_facts(
                    members, matched,
                    exclude_keys=asserted_host_keys,
                    drop_class=(ASSERTIONS_EVIDENCE_CLASS if role_record is not None else None))
                if ambiguous:
                    ambiguous_role_groups.append((name, ambiguous))
                if role_record is not None:
                    role_facts.insert(0, Fact(
                        ASSERTIONS_EVIDENCE_CLASS, role, confidence=ASSERTED_CONFIDENCE,
                        source=aview.source,
                        signal=f"{ASSERTIONS_SCHEMA} host.role"))
                    for member in members:
                        aview.mark("override", member["entity_key"], "role")
                won, overridden = resolve_precedence(
                    f"twin_config.nodes[{idx}].role", role_facts)
                # D17, flag only. Drift is a **LATER** PIT contradicting a standing assertion,
                # and "later" is a real test rather than a figure of speech: the PIT's
                # `observed_to` must fall after the file's own `as_of`. Without that clause the
                # flag fires on every override ever written — an operator asserts a role
                # PRECISELY because this PIT's census said something else, so the contradiction
                # it was written to fix would be reported as evidence that it had gone stale, and
                # a flag that is always on says nothing. Measured on the CorpAPCN cast, where it
                # fired on the second-DC override the file exists to make.
                #
                # Flag only, either way: the assertion still wins, both readings stay in the
                # artifact, and data never changes precedence.
                if (overridden and won.evidence_class == ASSERTIONS_EVIDENCE_CLASS
                        and overridden["value"] != won.value and pit_is_later):
                    overridden = {**overridden,
                                  "drift": {"class": overridden["class"],
                                            "value": overridden["value"], "pit_id": twin_id}}
                    drifted.append((name, overridden["class"], overridden["value"]))
                # `confidence`/`source` are spread only when the winner HAS them. Passing
                # `confidence=None` would emit a literal null, which is precisely the reading
                # `LES-006` forbids — "no producer supplied one" and "the producer supplied
                # nothing" are different claims, and only absence says the first. A
                # `Fact` from an operator assertion carries a source and no signal-worthy
                # confidence of its own beyond the 1.0 convention, so this branch is reached
                # in both directions once S2 lands.
                prov(f"twin_config.nodes[{idx}].role", won.evidence_class, won.signal,
                     **({"confidence": won.confidence} if won.confidence is not None else {}),
                     **({"source": won.source} if won.source else {}),
                     **({"note": role_record[1].note} if role_record is not None else {}),
                     **({"overrides": overridden} if overridden else {}))
            if "observation" in node:
                prov(f"twin_config.nodes[{idx}].observation", "observed",
                     "ir_entity.windows_seen / ir_pit.windows (group minimum)",
                     rung=rung, hosts_observed=len(members),
                     note="the group's WEAKEST member: the deploy gate is all-or-nothing per "
                          "node group, so it must not admit a member below the bar")
        # `source` is kept EXACTLY as it was: `docs/WORK_ORDER_fidelity_scorecard.md` §8.4 names
        # this row by that field as the carrier it would extend, and churning a cross-repo seam
        # for tidiness is not a trade worth making. `signal` is the addition, and it is strictly
        # more specific — it names the catalog ROW (with the ports that matched), which is what
        # §8.4 actually wants; the two coincide only for the `default:` block, where no row
        # exists to be more specific about.
        _catalog_source = "catalog/roles.yaml" + ("" if rule else " (default block)")
        _catalog_signal = (_catalog_source if rule is None
                           else _role_signal(role, matched, synthetic=synthetic))
        # `image` and `resources` are the two `compiler`-class fields an assertion can move into a
        # SCORED class (D12; axRange `14` §4). The catalog value they displace goes in
        # `overrides` — "what it would have been" — which is exactly what a scorecard needs to
        # tell a hand-chosen image from a default one.
        for _field_name, _default in (("image", content["image"]),
                                      ("resources", content["resources"])):
            _record = asserted_fields.get(_field_name) if asserted_fields else None
            if _field_name == "resources" and not content["resources"]:
                continue
            _path = f"twin_config.nodes[{idx}].{_field_name}"
            if _record is None:
                prov(_path, COMPILER_CLASS, _catalog_signal,
                     source=(_catalog_source if _field_name == "image"
                             else "catalog/roles.yaml"))
                continue
            for member in members:
                aview.mark("override", member["entity_key"], _field_name)
            _catalog_default = (rule.image if rule else DEFAULT_NODE["image"]) \
                if _field_name == "image" else (rule.resources if rule
                                                else DEFAULT_NODE["resources"])
            prov(_path, ASSERTIONS_EVIDENCE_CLASS, f"{ASSERTIONS_SCHEMA} host.{_field_name}",
                 **_asserted_prov(_record[1], _field_name, source=aview.source,
                                  beaten={"class": COMPILER_CLASS, "value": _catalog_default}))
        if "provides" in node:
            prov(f"twin_config.nodes[{idx}].provides", COMPILER_CLASS,
                 "synth/services.py LAB_REALM",
                 source="axrange protocol-faithful-services work order D4 (Authority: user)",
                 reason=f"the twin's forest is the LAB realm {LAB_REALM!r}, not the realm the "
                        f"wire carries. `role: adds` requires a domain to promote into, and the "
                        f"observed forest name is the single most identifying string in the "
                        f"source network — so the platform supplies its own rather than "
                        f"reproducing one. The observed realm is NOT recorded beside this value "
                        f"because there is no realm fact at the IR boundary to record: it "
                        f"survives only inside `Identifier.raw` "
                        f"(`docs/WORK_ORDER_reference_ingest.md` S5).")
    # -- the domain: who joins it, and who is in it -----------------------------
    #
    # `axrange/docs/WORK_ORDER_protocol_faithful_services.md` **S3**, under D1(a) + D4. Both
    # blocks below are gated on a DC actually having been emitted: a `joins:` against a forest
    # no node promotes is a deploy that fails minutes in, at the join, rather than at validation.
    identity_block: Optional[dict] = None
    if domain_nodes:
        # WINDOWS members only. `join-host-to-domain` is a Windows op (`docs/SLICE_WINDOWS_AD.md`)
        # and a Linux node carrying `joins:` would be authored nonsense; the check is on the
        # IMAGE rather than on the role, because the role/image pairing is catalog content that
        # can move underneath this.
        joined = []
        for node in nodes:
            is_windows = "win" in str(node.get("image", "")).lower()
            if node.get("role") == "corp-workstation" and is_windows:
                node["joins"] = LAB_REALM
                joined.append(node["name"])
        for idx_j, node in enumerate(nodes):
            if node.get("joins"):
                prov(f"twin_config.nodes[{idx_j}].joins", COMPILER_CLASS,
                     "synth/services.py LAB_REALM",
                     reason="a Windows workstation in a twin that stands up a domain joins it: "
                            "3389/135/137 answer as domain-member built-ins rather than as "
                            "standalone ones, and the Kerberos and NTLM the source's own "
                            "workstations carry have no counterpart otherwise. The realm is the "
                            "lab constant (D4), so nothing observed is reproduced.")
        # THE USER POPULATION IS IMPUTED, and this is the honest limit rather than an omission.
        # The wire carries TWO principals on this source (`matthew.admin` and `Guest`), and
        # nothing at all carries passwords, groups, OUs or SIDs — that is the directory-export
        # channel `docs/WORK_ORDER_reference_ingest.md` S3 opens, sequenced after the first
        # golden. So the twin's users are LAB DEFAULTS in the shape axRange's own
        # `twin-configs/corp-enterprise/corp-enterprise.yaml` already ships, marked `imputed`.
        #
        # The four accounts are not arbitrary: they are the canonical exercisable AD population —
        # an ordinary user, a domain admin, a KERBEROASTABLE service account (SPN + weak
        # password) and an AS-REP-ROASTABLE user. A domain with none of them answers Kerberos and
        # is not attackable, which makes the whole promotion decorative.
        identity_block = {
            "domain": LAB_REALM,
            "users": [
                {"id": "alice", "groups": ["Domain Users"], "ou": "Finance"},
                {"id": "bob-admin", "groups": ["Domain Admins"], "ou": "IT"},
                {"id": "svc-sql", "groups": ["Domain Users"],
                 "spn": f"MSSQLSvc/sql01.{LAB_REALM}:1433",
                 "password": "Summer2024!", "ou": "Service Accounts"},
                {"id": "jsmith", "groups": ["Domain Users"], "asrep_roastable": True,
                 "password": "Password123", "ou": "Finance"},
            ],
        }
        prov("twin_config.identity", "imputed", "lab-default AD population",
             source="axrange twin-configs/corp-enterprise/corp-enterprise.yaml",
             reason="IMPUTED, in full. This source's wire carries two Kerberos principals, one "
                    "of them 'Guest', and no passwords, groups, OUs or SIDs at all. A directory "
                    "export is the channel that supplies a real population "
                    "(`docs/WORK_ORDER_reference_ingest.md` S3); until it lands these are lab "
                    "defaults chosen to make the domain EXERCISABLE (one kerberoastable SPN "
                    "account, one AS-REP-roastable user), not to describe anyone observed.")
        caveats.append(
            f"a real AD domain is stood up: node(s) {sorted(domain_nodes)} carry "
            f"`install: [{{role: adds}}]` + `provides: [{{domain: {LAB_REALM}}}]`, and "
            f"{len(joined)} Windows workstation(s) carry `joins: {LAB_REALM}`. TWO things an "
            f"operator must know. (1) The realm is the LAB one, imputed — the observed forest "
            f"name is deliberately not reproduced (D4, Authority: user). (2) The domain's USERS "
            f"are lab defaults, imputed in full: this capture carries two principals and no "
            f"credentials, so nothing here describes an observed person. Budget deploy time: an "
            f"AD promotion reboots and each join takes 1-2 minutes, effectively serialized "
            f"(axRange GAP-051), and a joining member must be able to RESOLVE the DC.")
    for name, actual, cap in capped:
        caveats.append(f"node {name!r}: {actual} hosts observed, capped to {cap} "
                       f"(D3 is an open decision; the network really has {actual}).")
    for name, observed in promoter_clamped:
        caveats.append(
            f"node {name!r}: {observed} domain controllers observed on one segment, deployed as "
            f"ONE forest root (count 1). axRange stands up a single AD DS forest per realm and "
            f"emits a `domain-ready[{LAB_REALM}]` barrier per `role: adds` host, so a second "
            f"promoter would collide on that op and refuse the plan; a real replica DC is the same "
            f"forest, which axRange has no op to stand up. The observed count is kept in the "
            f"population sidecar (`observed: {observed}`, `capped_by: single_forest_root`).")
    if roleless:
        caveats.append(
            f"{len(roleless)} placed host(s) match no evidence rule and are emitted as "
            f"{UNROLED_ROLE!r} — an image and NO persona, so they stand up as observed and "
            f"generate nothing they were not observed generating (D4). They ARE in this twin: "
            f"the cast no longer drops them (D1). Whether they DEPLOY is the deploy's decision, "
            f"taken against each node's `observation:` block at `DEPLOY_MIN_OCCUPANCY`. Role "
            f"inference for pure clients (`WORK_ORDER_synth_v1.md` phase 2, finding 2) is what "
            f"would give them a real role; see the sidecar's `population.role_less` for the "
            f"count per segment.")
    caveats.append(
        f"NOTHING IS DROPPED AT COMPILE TIME (`docs/WORK_ORDER_deployment_scope.md` D1). Every "
        f"emitted node and segment carries an `observation:` block, and the deployment bar lives "
        f"in `params.DEPLOY_MIN_OCCUPANCY` ({DEPLOY_MIN_OCCUPANCY}) where axRange's expander "
        f"reads it — one cast serves every threshold. A host or segment below the bar is "
        f"OBSERVED, BELOW DEPLOY THRESHOLD, never a cast drop. Compiler-class nodes (the "
        f"{GATEWAY_NAME!r} router, the internet sink and its segment) carry no block and are "
        f"always emitted.")
    if aview is not None:
        counts = aview.aset.counts
        caveats.append(
            f"{len(aview.aset)} OPERATOR ASSERTION(S) were applied from "
            f"{aview.aset.path or '<in memory>'} ({aview.aset.schema}, as_of "
            f"{aview.aset.as_of}, source {aview.source!r}): "
            f"{counts['add']} add, {counts['override']} override, {counts['suppress']} suppress. "
            f"An assertion is NOT an observation: it wins precedence over every class the wire "
            f"produces, carries confidence 1.0 by convention, and is scored as `asserted` — a "
            f"twin can be made to pass on assertions alone, and the sidecar's evidence mix is "
            f"what says so (axRange `docs/design/14-operator-assertions.md` §2).")
    if suppressed_rows:
        placed_suppressed = [r for r in suppressed_rows if r["placed"]]
        caveats.append(
            f"{len(suppressed_rows)} entity(s) were SUPPRESSED by assertion, "
            f"{len(placed_suppressed)} of which were placed hosts and are therefore absent from "
            f"this twin: {sorted(r['key'] for r in suppressed_rows)}. None of them vanished "
            f"silently — every one is in `population.suppressed` with its note, and the "
            f"reconstruction identity is now `placed_hosts == sum(observed) + suppressed` (D18). "
            f"Suppressing an entity that was never a twin node (an external peer) is accounting "
            f"only: it changes nothing in the emitted documents.")
    if asserted_nodes or asserted_segment_names:
        caveats.append(
            f"{len(asserted_nodes)} node group(s) and {len(asserted_segment_names)} segment(s) "
            f"are in this twin because an operator ASSERTED them, not because anything observed "
            f"them: nodes {sorted(asserted_nodes)}, segments {sorted(asserted_segment_names)}. "
            f"They carry NO `observation:` block — there is no presence to report, which is a "
            f"different claim from a presence that could not be measured — so axRange never "
            f"gates them out at `DEPLOY_MIN_OCCUPANCY`.")
    for node_name, beaten_class, beaten_value in drifted:
        caveats.append(
            f"ASSERTION DRIFT on node {node_name!r}: this PIT's {beaten_class} reading is "
            f"{beaten_value!r}, which contradicts the standing assertion. The assertion still "
            f"WINS — drift is flag only, data never changes precedence (D17) — and both readings "
            f"are in the sidecar under that field's `overrides.drift`. Change the assertion if "
            f"the network changed.")
    for node_name, priors in ambiguous_role_groups:
        caveats.append(
            f"node {node_name!r}: its members' pre-assertion roles were {priors}, so no single "
            f"runner-up could be named and the field's `overrides` is absent. The coarser true "
            f"statement, not one member's reading published for the group (`LES-140`).")
    if unmeasured_nodes or unmeasured_segments:
        caveats.append(
            f"{len(unmeasured_nodes)} node group(s) and {len(unmeasured_segments)} segment(s) "
            f"carry NO `observation:` block because their occupancy cannot be computed (a PIT "
            f"claiming {windows} window(s), or a missing window count). They are emitted and "
            f"axRange will never gate them out — a host is not excluded for a denominator "
            f"problem (`LES-006`) — but nothing about their presence has been asserted: "
            f"nodes {sorted(unmeasured_nodes)}, segments {sorted(unmeasured_segments)}.")
    if pruned_segments:
        # D7. With every placed host emitted, a segment is pruned only when it had ZERO placed
        # hosts — genuinely empty, not emptied by a filter. The prune itself is unchanged and
        # stays, because axRange's expander ERRORS on a conduit whose segments carry no router
        # leg, so an orphan is a deploy-time failure rather than an unused row. A segment falling
        # below the DEPLOY threshold is a different thing entirely and is not this.
        caveats.append(
            f"{len(pruned_segments)} observed segment(s) carry no placed host at all — nothing "
            f"provisionable was seen on them (link-local, multicast) — and were dropped from the "
            f"twin with any conduit touching them: {pruned_segments}. This is emptiness, not a "
            f"threshold: a segment below the deploy bar is still emitted, carrying its block.")

    # The router. Last so its index is stable regardless of how many host groups precede it, and
    # only when there is a segment for it to be on — a router with an empty `nets:` is not a
    # degraded twin, it is an invalid document, and emitting one would turn "this filter left
    # nothing" into a schema error at deploy time instead of a caveat here.
    if segments:
        gw_idx = len(nodes)
        # EVERY segment, INCLUDING the internet sink's — and that contradicts
        # `docs/JOURNAL_corpapcn_fidelity.md` GAP-C17, which specified "a segment the gw does
        # not forward". Recorded here rather than silently, because the reversal is deliberate:
        #
        #   * A sink with no router leg is UNREACHABLE. Nothing outside its own /24 can send it
        #     a packet, so it absorbs nothing and the twin's hosts go straight back to
        #     manufacturing local edges — the sink would be 15 idle VMs.
        #   * Containment never rested on that leg. The leak vector is what addresses a persona
        #     can NAME, and the answer is none: `axrange/personas/schema.py::Emit` has no
        #     address or CIDR field, so peer resolution can only ever produce in-twin host
        #     addresses (`synth/personas.py::SINK_ROLE`). Removing a leg does not narrow a
        #     surface that does not exist.
        #   * The sink's connected /24 beats the gateway's default route by longest-prefix
        #     match, deterministically rather than by luck: axRange's `configure-router`
        #     installs no routes and no metrics of its own, so there is no competing entry.
        nodes.append({"name": GATEWAY_NAME, "role": "gw", "kind": "router",
                      "image": DEFAULT_NODE["image"],
                      "nets": [{"segment": s["name"]} for s in segments],
                      # The compiler's placeholder for a gateway nothing identified: the
                      # thing an operator confirms or replaces (topology-layers §3.1).
                      "source": "unconfirmed"})
        prov(f"twin_config.nodes[{gw_idx}]", COMPILER_CLASS,
             "synth/compiler.py GATEWAY_NAME",
             reason="synthetic: no gateway is identifiable at this vantage (no L2 addresses, no "
                    "TTL in conn.log). Present so declared conduits validate and so segments get "
                    "a network+1 gateway address.")
        caveats.append(
            f"node {GATEWAY_NAME!r} is synthetic. DESIGN_BRIEF §8's gateway detection needs MAC "
            f"clustering or TTL bins, and this vantage supplies neither.")
    else:
        caveats.append(
            "EMPTY TWIN: this PIT placed no host at all — every local entity was observed on "
            "nothing provisionable (link-local, multicast) — so there are no segments, no router "
            "and no conduits. NOT a filter outcome: since D1 nothing is dropped at compile time, "
            "so an empty cast now means an empty PLACED set and nothing else. The PIT itself is "
            "unaffected — it is the complete view (D9); this is the compiler declining to "
            "invent one.")
    if sink_groups:
        caveats.append(
            f"the {len(sink_groups)} {sorted(sink_groups)} node group(s) on segment "
            f"{SINK_SEGMENT_NAME!r} ({SINK_CIDR}, {sum(SINK_BUCKETS)} hosts) are SYNTHETIC and "
            f"observe nothing (GAP-C17). They stand in for every destination outside the twin — "
            f"85% of this source's edges — so personas have somewhere to put outbound traffic "
            f"other than each other. Reached by ROLE only: axRange personas cannot name an "
            f"address, so no synthetic traffic can leave for a real network. Fan-out is "
            f"expressible up to {sum(SINK_BUCKETS)} distinct external peers per host; 7 of the "
            f"132 source hosts with external conversations exceed that (the 16..53 tail, max 53) "
            f"and are clamped, so the busiest hosts' external fan-out is UNDER-represented. Its "
            f"own segment is NOT tapped — see the tap-plan caveat below for why that loses "
            f"nothing.")

    # -- conduits ---------------------------------------------------------------
    seg_of = {e["entity_key"]: e["segment"] for e in placed}
    # Keyed on the OBSERVED cidr, because every caller looks up by the cidr the PIT carries.
    # `segments[]` holds the relocated value, so building this from `s["cidr"]` directly would
    # KeyError on the first conduit of any relocated cast — the same trap as the node -> segment
    # lookup above, and the reason both go through `_cidr()` rather than comparing raw strings.
    name_of = {observed: nm for observed, nm in
               ((sg["cidr"], next(s["name"] for s in segments if s["cidr"] == _cidr(sg["cidr"])))
                for sg in all_segments if sg["cidr"] in live_cidrs)}
    observed_pairs = sorted({tuple(sorted((seg_of[e["src_key"]], seg_of[e["dst_key"]])))
                             for e in pit["ir_edge"]
                             if e["src_key"] in seg_of and e["dst_key"] in seg_of
                             and seg_of[e["src_key"]] != seg_of[e["dst_key"]]})
    # ASSERTED CONDUITS (D13: conduits land, routes are refused by name). A pair whose two
    # segments are both live joins the set; one naming a segment the twin does not carry is
    # REPORTED rather than emitted, because `name_of` would `KeyError` and because a conduit to
    # nowhere is a stale record, not a malformed one — the operator's remedy is `add: segment`.
    asserted_pairs: list[tuple] = []
    if aview is not None:
        for key, record in sorted(aview.conduit_adds.items()):
            a, b = key.split("|")
            missing = sorted(c for c in (a, b) if c not in live_cidrs)
            if missing:
                aview.ineffective(
                    "add:conduit", key,
                    f"segment(s) {missing} are not in this twin — add them with `kind: segment`, "
                    f"or correct the CIDRs")
                continue
            aview.mark("add", "conduit", key)
            pair = (a, b)
            if pair not in observed_pairs:
                asserted_pairs.append(pair)
    pairs = sorted(set(observed_pairs) | set(asserted_pairs))
    conduits = [{"from": name_of[a], "to": name_of[b]} for a, b in pairs]
    _asserted_pairs = set(asserted_pairs)
    for i, (a, b) in enumerate(pairs):
        if (a, b) in _asserted_pairs:
            record = aview.conduit_adds[conduit_key(a, b)]
            prov(f"twin_config.conduits[{i}]", ASSERTIONS_EVIDENCE_CLASS,
                 f"{ASSERTIONS_SCHEMA} add:conduit", between=[a, b],
                 **_asserted_prov(record, "key", source=aview.source),
                 reason="declared by an operator: no observed edge crosses these two segments "
                        "at this vantage")
        else:
            prov(f"twin_config.conduits[{i}]", "observed",
                 "ir_edge (cross-segment pairs over placed entities)", between=[a, b])

    # ASSERTED LINKS (`docs/design/topology-layers.md` §2.3, §3.2). No vantage this compiler
    # reads carries link evidence (a switch is at best a MAC on a port), so every link is an
    # operator's record: each end's entity key resolves to the node group it compiled into
    # (which must be one device, never a `count > 1` group), the segments it carries resolve
    # by CIDR to the twin's segment names, and anything that does not resolve is REPORTED
    # rather than emitted, like a conduit to nowhere.
    links_out: list[dict] = []
    if aview is not None:
        for key, record in sorted(aview.link_adds.items()):
            fields = dict(record.fields)
            for fname, orec in sorted(aview.link_over.get(key, {}).items()):
                fields[fname] = orec.value
            ends: list[dict] = []
            problem: Optional[str] = None
            for end in key.split("|"):
                host_key, port = end.rsplit("#", 1)
                node_name = node_name_of_key.get(host_key)
                if node_name is None:
                    problem = (f"device {host_key!r} is not a twin node (unplaced, suppressed "
                               f"or absent from this PIT) -- add it with `kind: device`")
                    break
                if node_count_of_name.get(node_name, 1) > 1:
                    problem = (f"device {host_key!r} is one of the {node_count_of_name[node_name]} "
                               f"hosts of group {node_name!r}; a link names one device -- split "
                               f"it out with `override: {{field: group}}` first")
                    break
                ends.append({"device": node_name, "port": port})
            if problem is None:
                named = ([fields["native"]] if fields.get("native") else []) + list(fields.get("vlans") or [])
                missing = sorted(c for c in named if c not in name_of)
                if missing:
                    problem = (f"segment(s) {missing} are not in this twin -- add them with "
                               f"`kind: segment`, or correct the CIDRs")
            if problem is not None:
                aview.ineffective("add:link", key, problem)
                continue
            aview.mark("add", "link", key)
            for fname in aview.link_over.get(key, {}):
                aview.mark("override", key, fname)
            entry: dict = {"a": ends[0], "b": ends[1], "mode": fields.get("mode", "trunk")}
            if fields.get("native"):
                entry["native"] = name_of[fields["native"]]
            entry["vlans"] = [name_of[c] for c in (fields.get("vlans") or [])]
            if fields.get("state"):
                entry["state"] = fields["state"]
            entry["source"] = aview.mark_source([key]) or ASSERTIONS_EVIDENCE_CLASS
            i = len(links_out)
            links_out.append(entry)
            prov(f"twin_config.links[{i}]", ASSERTIONS_EVIDENCE_CLASS,
                 f"{ASSERTIONS_SCHEMA} add:link",
                 **_asserted_prov(record, "key", source=aview.source),
                 reason="declared by an operator: this vantage carries no link evidence")

    # A POSITIVE GATE on the one property every downstream reference depends on: `net:` on a
    # segment, `tfc` `assignments[].node`, `tap` bindings and axRange's own expander all address a
    # node BY NAME, so two nodes sharing one is not a cosmetic collision — it is a twin config
    # whose references are ambiguous. Nothing guaranteed it before: `_node_suffix`'s `rstrip(".0")`
    # eats a RUN of trailing zeros, so `10.0.200.0/24` and `10.0.2.0/24` both suffix to `10-0-2`
    # (the sink's own constant dodges this by not ending in a zero, and says so). Assertions add a
    # second way to collide, which is what made it worth asserting rather than hoping.
    _names = [n["name"] for n in nodes]
    if len(_names) != len(set(_names)):
        dupes = sorted({n for n in _names if _names.count(n) > 1})
        raise ValueError(
            f"two node groups compiled to the same name(s) {dupes}. A twin config addresses "
            f"nodes BY NAME from `net:`, `tfc` assignments and tap bindings, so this document "
            f"would be ambiguous rather than merely untidy. The usual cause is `_node_suffix`'s "
            f"trailing-zero truncation collapsing two segments onto one suffix.")

    # The ineffective-assertion caveats are written HERE, after the conduit pass — the last site
    # that can add to the report. Written earlier they missed it, and a caveat block that is
    # complete only for the records the compiler happened to reach first is worse than none.
    if aview is not None:
        for row in aview.report():
            caveats.append(
                f"assertion {row['kind']} {row['key']!r}"
                f"{' field ' + repr(row['field']) if row.get('field') else ''} had NO EFFECT: "
                f"{row['reason']}. Reported rather than refused — a PIT moves underneath a "
                f"standing assertions file, and a re-imprint that no longer sees a host must not "
                f"become an editing session.")

    twin_config = {"twin_config": {
        "id": twin_id,
        "description": (
            f"Discovered from {pit_row['source']} as of {pit_row['as_of']}, over "
            f"{pit_row['windows']} window(s) at vantage {pit_row.get('vantage') or 'unknown'}"
            + (f" on network {pit_row['network']}" if pit_row.get("network") else "")
            + f". Compiled by aximprint from network IR {pit_row['schema_version']}. Every "
              f"`image`, `resources` and the `{GATEWAY_NAME}` node are catalog defaults, not "
              f"observations — see the provenance sidecar."),
        # A CAST TWIN CARRIES NO INSTRUMENTATION AGENTS. Unconditional, and emitted here rather
        # than left to the operator, because the last re-cast silently discarded it as a
        # hand-edit and put it back to ON.
        #
        # axRange's default-agent sweep is default-ON, so an unset field puts a Prometheus
        # node-exporter and a Wazuh agent on EVERY host: 274 extra ops on this twin, which is
        # the visible symptom and the least of it. The disqualifying part is that both agents
        # TALK. Their scrapes and enrolments are conversations the source network never had,
        # the tap captures them like any other traffic, and the fingerprint scores them as
        # edges — so the twin would be measured partly against its own instrumentation. With
        # the o2-services host down they are worse, not better: a refused connect is still an
        # S0 conn to Zeek, so failed agents still manufacture edges.
        #
        # A twin that exists to be MEASURED cannot carry uninstrumented traffic generators.
        # That is a property of casting, not a per-twin preference, which is why there is no
        # flag for it. (axRange's own `TwinConfig.default_agents` docstring reaches the same
        # conclusion independently: "A cast twin measured on-the-wire wants this off".)
        "default_agents": False,
        # THE DEPLOYMENT BAR, STATED IN THE DOCUMENT (`WORK_ORDER_deployment_scope.md` §9).
        #
        # 0.8 is also axRange's own built-in default, so emitting it changes no behaviour. That
        # is the point: an implicit default is a policy nobody can read off the artifact, and
        # `twin-config show` prints `params:`. An operator overrides it per deploy with
        # `--param DEPLOY_MIN_OCCUPANCY=0.5`, which yields a different node set and therefore a
        # different `plan_id` — correctly, because it is a different twin.
        "params": {"DEPLOY_MIN_OCCUPANCY": DEPLOY_MIN_OCCUPANCY},
        "segments": segments, "nodes": nodes, "conduits": conduits,
        # ABSENT unless an operator declared wiring (topology-layers §2.3): a cast with no
        # link records expands byte-identically to one compiled before links existed.
        **({"links": links_out} if links_out else {}),
        # ABSENT unless this twin actually stands a domain up. axRange's `TwinConfig` model takes
        # `identity: Optional[...]`, and emitting an empty block on a twin with no DC would
        # publish a forest name nothing promotes.
        **({"identity": identity_block} if identity_block else {})}}
    prov("twin_config.params.DEPLOY_MIN_OCCUPANCY", COMPILER_CLASS,
         "synth/compiler.py DEPLOY_MIN_OCCUPANCY",
         reason="the DEPLOY-time occupancy bar, not an observation. Emitted so the threshold is "
                "visible in the document rather than implicit in axRange's built-in default, "
                "which carries the same value.")
    prov("twin_config.default_agents", COMPILER_CLASS,
         "synth/compiler.py (cast policy: no instrumentation agents)",
         reason="a cast twin is a MEASUREMENT subject: axRange's default-ON agent sweep would "
                "put a Prometheus exporter and a Wazuh agent on every host, whose scrapes and "
                "enrolments the tap captures and the fingerprint scores as edges the source "
                "network never had. Failed agents are no safer — an S0 conn is still an edge.")

    # -- tfc --------------------------------------------------------------------
    # One assignment per role that (a) reached the twin and (b) has a persona. `dns-resolver` has
    # `persona: null` — axRange ships no such persona and its own `corp-workstation` says so — so
    # it reaches `twin_config` with a role and gets no assignment here. That is a real state, and
    # D5a is why such a role is not filtered out one step earlier.
    assignments, personaless_roles, too_short = [], [], []
    span = (pit_row["observed_to"] or 0) - (pit_row["observed_from"] or 0)
    roles_with_persona = []
    for role in sorted({n["role"] for n in nodes if n.get("role")} - {"gw"}):
        rule = _rule_for(role)
        if rule and rule.persona:
            roles_with_persona.append((role, rule))
        else:
            personaless_roles.append(role)

    # `docs/WORK_ORDER_traffic_profiles.md` D1/D2/D3/D5/D6: the role-level behavioral projection
    # of `ir_traffic_profile`. The IR keeps the finest grain it observed (per entity); this is
    # where — and ONLY where — it collapses to per-role, so a pooling choice made here can never
    # re-enter drift comparison as though it were observed (D1, `docs/WORK_ORDER_network_ir.md`
    # D9). `pit.get(...)` rather than `pit["ir_traffic_profile"]`: a PIT materialized before this
    # table existed (schema `1.2` or earlier) has no key for it at all, and every field this
    # section emits must degrade to "no evidence" rather than a `KeyError`.
    traffic_by_key = {r["entity_key"]: r for r in pit.get("ir_traffic_profile") or []}
    role_hod: dict[str, Optional[list]] = {}
    role_rate: dict[str, Optional[float]] = {}
    role_diurnal_coverage: dict[str, tuple[int, int]] = {}
    for role, _rule in roles_with_persona:
        members = [e for e in placed if e["role"] == role]
        profiles = [traffic_by_key[e["entity_key"]] for e in members
                    if e["entity_key"] in traffic_by_key]
        hods = [_json_value(p["hod_flows"]) for p in profiles if p["hod_flows"] is not None]
        role_diurnal_coverage[role] = (len(hods), len(members))
        # An unweighted mean of already-normalized (sum-to-1.0) member histograms: the mean of
        # several vectors that each sum to 1.0 itself sums to 1.0, so no separate renormalization
        # step is needed. Members below D4's diurnal floor contribute nothing — they are excluded
        # from `hods` above, not zero-filled — so a role every one of whose members lacks diurnal
        # evidence gets `None` here, never a fabricated flat histogram.
        role_hod[role] = ([sum(vals) / len(hods) for vals in zip(*hods)] if hods else None)
        rates = [_json_value(p["flows_per_window_q"])[1] for p in profiles
                 if p["flows_per_window_q"] is not None]      # index 1 == p50
        role_rate[role] = statistics.median(rates) if rates else None

    # D6: a RELATIVE figure, normalized against the median rate over the SAME population that
    # receives an assignment — every OTHER role a twin might also carry (role-less hosts, a
    # personaless role) never reaches a `tfc` assignment either, so including their rate in the
    # denominator would normalize against a population this field is never emitted for.
    _measured_rates = [v for v in role_rate.values() if v is not None]
    pit_median_rate = statistics.median(_measured_rates) if _measured_rates else None

    roles_with_schedule: list[str] = []
    roles_with_intensity: list[str] = []
    for role, rule in roles_with_persona:
        assignment = {"role": role, "persona": rule.persona}
        idx = len(assignments)
        # D5: ALWAYS present for a role that gets an assignment at all — a pure, deterministic
        # hash of (pit_id, role), never gated by an evidence floor because it is a COMPILER
        # CHOICE, not a measurement (`defaulted`, the same posture as `image`/`resources` above).
        assignment["seed"] = _role_seed(twin_id, role)
        prov(f"traffic_profile.assignments[{idx}].seed", COMPILER_CLASS,
             "sha256(pit_id|role)[:8]",
             reason="deterministic hash of (pit_id, role); a compiler choice, not a measurement")
        schedule = classify_schedule(role_hod[role])
        if schedule is not None:
            assert schedule in SCHEDULE_CLASSES, (
                f"classify_schedule returned {schedule!r}, outside {SCHEDULE_CLASSES} — "
                f"axRange's own parser would refuse this at apply time (D3/(i))")
            assignment["schedule"] = schedule
            roles_with_schedule.append(role)
            with_evidence, total_members = role_diurnal_coverage[role]
            prov(f"traffic_profile.assignments[{idx}].schedule", "imputed",
                 "ir_traffic_profile.hod_flows (classify_schedule)",
                 members_with_diurnal_evidence=with_evidence, members_total=total_members)
        if role_rate[role] is not None and pit_median_rate:
            assignment["intensity"] = role_rate[role] / pit_median_rate
            roles_with_intensity.append(role)
            prov(f"traffic_profile.assignments[{idx}].intensity", "observed",
                 "ir_traffic_profile.flows_per_window_q p50 (role median / PIT median)",
                 note="no axRange persona driver reads this today (D6); informational, "
                      "disclosed so a measured value is never mistaken for one withheld")
        assignments.append(assignment)
        # D12: a persona whose slowest emit is `diurnal` cannot have been OBSERVED in a
        # three-hour capture, so assigning it is a claim about behavior nothing in this PIT
        # could have seen. Warned, not refused: the assignment is still the best available
        # reading of the role, and the operator's remedy is a longer capture, which is an
        # acquisition decision no compiler flag can make.
        if rule.min_capture_seconds and span < rule.min_capture_seconds:
            too_short.append((role, rule.persona, rule.persona_cadence, span,
                              rule.min_capture_seconds))
    if roles_with_schedule:
        caveats.append(
            f"schedule is set on {len(roles_with_schedule)} assignment(s) "
            f"({sorted(roles_with_schedule)}) from the SHAPE of observed activity (D3), never a "
            f"local clock — the capture carries no timezone. axRange's business-hours gate uses "
            f"ITS OWN clock, so a twin deployed in a different timezone shifts which hours are "
            f"treated as busy; this is a fidelity caveat, not a correctness one.")
    if roles_with_intensity:
        caveats.append(
            f"intensity is set on {len(roles_with_intensity)} assignment(s) "
            f"({sorted(roles_with_intensity)}) but is INERT downstream today (D6): no axRange "
            f"persona driver reads it — their own comment calls it 'optional, informational "
            f"(persona.knobs override -- follow-on)'. Emitted anyway: a measured value withheld "
            f"at emit time is indistinguishable downstream from one never measured.")
    if personaless_roles:
        # Two different reasons a role can land here, and conflating them would misdescribe the
        # larger population. `dns-resolver` runs a real service other roles depend on (D5a);
        # `UNROLED_ROLE` runs nothing at all and is present because it was OBSERVED (D4) —
        # giving it a traffic-generating persona would manufacture conversations the source
        # never had, in the artifact being measured on the wire.
        caveats.append(
            f"roles {personaless_roles} have no axRange persona and generate no traffic. Two "
            f"reasons, both deliberate: a role like 'dns-resolver' is in the twin because it "
            f"runs a real service other roles depend on (D5a), while {UNROLED_ROLE!r} runs "
            f"nothing at all and is in the twin because it was OBSERVED (D4) — a host whose "
            f"behaviour named no role must not be given behaviour it was never seen having.")
    for role, persona, cadence, actual, needed in too_short:
        caveats.append(
            f"persona {persona!r} (role {role!r}) has cadence {cadence}, which needs at least "
            f"{needed / 3600:.1f}h of capture to have been observed at all; this PIT spans "
            f"{actual / 3600:.1f}h. The assignment stands on the role, not on observed behavior "
            f"at that cadence (D12) — capture longer to support it.")
    traffic_profile = {"traffic-profile": {"target": twin_id, "assignments": assignments}}

    # -- tap --------------------------------------------------------------------
    # `sensors` is deliberately ABSENT, not empty. Which Lab sensor captures which link is an `o2`
    # fact this project cannot know; a guess would be the exact failure `evidence_class` exists to
    # prevent. A plan that fails axRange's validator with "sensors missing" is a better artifact
    # than one that passes carrying a fabricated sensor name.
    #
    # EVERY SEGMENT EXCEPT THE SINK'S. A sink flow already crosses a tapped wire — its source
    # host's own segment — so binding the sink segment too mirrors the identical flow a second
    # time. Measured: 33% of the twin's offered bytes go to the sink and are echoed back, so
    # double-tapping inflates the flow and byte axes while the row-keyed ones survive unchanged.
    # Omitting the binding is strictly better rather than a trade: nothing becomes invisible
    # (the flows are still captured at their source), and the sink hosts still materialize
    # carrying `segment = SINK_CIDR`, which is what the fingerprint's twin-side locality
    # override resolves against — it keys on the CIDR an entity reports, not on being tapped.
    tapped = [s for s in segments if s["cidr"] != SINK_CIDR]
    tap_plan = {"tap-plan": {
        "target": twin_id,
        "bindings": [{"selector": {"scope": "segment", "target": s["name"]}, "mode": "exclusive"}
                     for s in tapped]}}
    # ASSERTED TAPS (`docs/design/topology-layers.md` §8): a sensor binding the operator placed
    # on a whole segment or on one device port -- the one binding kind that DOES name its
    # sensors, because the operator knows them. A port tap resolves its segment through the
    # asserted link that carries the port; a port on no link is reported, not guessed.
    if aview is not None:
        for key, record in sorted(aview.tap_adds.items()):
            kind_, rest = key.split(":", 1)
            binding: Optional[dict] = None
            if kind_ == "segment":
                if rest not in name_of:
                    aview.ineffective("add:tap", key, f"segment {rest!r} is not in this twin")
                    continue
                binding = {"selector": {"scope": "segment", "target": name_of[rest]}}
            else:
                host_key, port = rest.rsplit("#", 1)
                node_name = node_name_of_key.get(host_key)
                if node_name is None:
                    aview.ineffective("add:tap", key, f"device {host_key!r} is not a twin node")
                    continue
                carried = next(
                    (([lk.get("native")] if lk.get("native") else []) + list(lk.get("vlans") or [])
                     for lk in links_out
                     if any(e["device"] == node_name and e["port"] == port for e in (lk["a"], lk["b"]))),
                    None)
                if not carried:
                    aview.ineffective(
                        "add:tap", key,
                        f"port {port!r} of {node_name!r} is on no link in this twin -- add the "
                        f"link first, or tap the segment")
                    continue
                binding = {"selector": {"scope": "port", "target": node_name, "segment": carried[0]}}
            binding["mode"] = "exclusive"
            binding["sensors"] = list(record.fields.get("sensors") or [])
            aview.mark("add", "tap", key)
            j = len(tap_plan["tap-plan"]["bindings"])
            tap_plan["tap-plan"]["bindings"].append(binding)
            prov(f"tap_plan.bindings[{j}]", ASSERTIONS_EVIDENCE_CLASS,
                 f"{ASSERTIONS_SCHEMA} add:tap",
                 **_asserted_prov(record, "key", source=aview.source),
                 reason="placed by an operator; the one binding whose sensors are named")
    caveats.append(
        "tap plan is INCOMPLETE by design: every binding omits `sensors`, which names Lab (`o2`) "
        "sensors aximprint has no way to observe. Fill them in before applying.")
    if sink_groups:
        caveats.append(
            f"the internet sink's segment ({SINK_SEGMENT_NAME}, {SINK_CIDR}) is deliberately NOT "
            f"tapped: {len(tapped)} of the twin's {len(segments)} segments carry a binding. An "
            f"outbound flow is already captured on its source host's segment, so tapping the "
            f"sink as well would mirror the same flow twice and inflate the flow and byte axes "
            f"(the sink echoes, and ~a third of offered bytes go to it) while leaving the "
            f"row-keyed axes unchanged. Nothing is lost by omitting it, and the sink's hosts "
            f"still report `segment: {SINK_CIDR}`, which is what the fingerprint's twin-side "
            f"locality override keys on.")

    # D6, kept and re-pointed: **what the network has, against what the twin deploys**, structured
    # rather than only narrated in `caveats`. It used to be drop accounting; since D1 nothing is
    # dropped, so it is ANNOTATION accounting — the same numbers, describing a different
    # decision, and the identity that keeps it honest is now an equality:
    #
    #     placed_hosts == sum(observed) over node_groups
    #
    # (`docs/WORK_ORDER_reference_ingest.md`'s `suppressed` sub-block extends this to
    # `placed_hosts == sum(observed) + suppressed` when it lands; nothing suppresses anything
    # today, and inventing that key here would be half of another work order's schema.)
    #
    # `role_less` survives as a COUNT, not a drop list: how much of the placed population carries
    # no inferred role and is therefore emitted as `UNROLED_ROLE`. `excluded_by_occupancy` is
    # GONE, because nothing at compile time excludes anything any more — the deploy's own gate
    # reports what it excluded, against `params.DEPLOY_MIN_OCCUPANCY`.
    roleless_by_segment: dict[str, int] = {}
    for entity in roleless:
        roleless_by_segment[entity["segment"]] = roleless_by_segment.get(entity["segment"], 0) + 1
    population_block = {
        "pit_entities": len(pit["ir_entity"]),
        # OBSERVED placed hosts, before suppression and excluding hosts an operator ADDED — which
        # is what keeps the reconstruction identity readable as a statement about the capture:
        #
        #     placed_hosts == sum(observed over node_groups) + suppressed(placed)
        #
        # An asserted host is counted in its group's `asserted:` instead, and a suppressed one in
        # `suppressed` (D18): a twin that quietly removed a host from both sides of the identity
        # would be arithmetically consistent and would have lost the record of the removal, which
        # is the failure the block exists to prevent.
        "placed_hosts": len(placed) + len([r for r in suppressed_rows if r["placed"]])
                        - len([e for e in placed if e["entity_key"] in asserted_host_keys]),
        "emitted_hosts": sum(g["emitted"] for g in population),
        "node_groups": population,
        "role_less": {"hosts": len(roleless),
                      "by_segment": dict(sorted(roleless_by_segment.items())),
                      "policy": f"D4: emitted as {UNROLED_ROLE!r} — an image rule and no "
                                f"persona. Annotation, not a drop; the cast emits every placed "
                                f"host and the deploy gates on `observation:`"},
        "deploy_gate": {"param": "DEPLOY_MIN_OCCUPANCY", "value": DEPLOY_MIN_OCCUPANCY,
                        "policy": "D1: the cast describes, the deploy selects. axRange's "
                                  "twin-config expander applies this to every emitted node's "
                                  "and segment's `observation:` block; no block means never "
                                  "gated out",
                        "rungs": {name: floor for name, floor in _RUNG_LADDER},
                        "unmeasured_nodes": sorted(unmeasured_nodes),
                        "unmeasured_segments": sorted(unmeasured_segments)},
        "segments_pruned": pruned_segments,
        # D8/D18. Present on EVERY cast, empty when nothing was suppressed, so a consumer reads
        # one shape rather than branching on the key's existence — the same posture the rest of
        # this block already takes.
        "suppressed": sorted(suppressed_rows, key=lambda r: r["key"]),
    }
    if aview is not None:
        # The assertions account (`docs/WORK_ORDER_reference_ingest.md` S2). Structured, beside
        # the caveats rather than only inside them, because axRange's Console and the fidelity
        # scorecard both need to READ what was asserted rather than parse prose out of a
        # sentence — the same argument `population.node_groups` already made about drops.
        population_block["assertions"] = {
            "schema": aview.aset.schema,
            "path": aview.aset.path,
            "imprint": aview.aset.imprint,
            "as_of": aview.aset.as_of,
            "source": aview.source,
            "counts": aview.aset.counts,
            "added_hosts": sorted(asserted_host_keys),
            "added_segments": sorted(asserted_segment_names),
            "added_conduits": sorted(conduit_key(a, b) for a, b in asserted_pairs),
            "added_devices": sorted(k for k, r in aview.host_adds.items() if r.kind == "device"),
            "added_links": sorted(aview.link_adds),
            "added_taps": sorted(aview.tap_adds),
            "confirmed": sorted(aview.confirm),
            "doubted": sorted(aview.doubt),
            "ineffective": aview.report(),
            # Every record the compile never acted on, derived from `matched` rather than counted
            # a second time. A record can be `unmatched` without being `ineffective` only if a
            # site forgot to mark it, so the two lists disagreeing is itself a signal.
            "unmatched": aview.unmatched(),
        }
    return {"twin_config": twin_config, "traffic_profile": traffic_profile,
            "tap_plan": tap_plan,
            # THE SIDECAR'S OWN VERSION, FIRST (D6/D-b). `schema_version` beside it is the IR's,
            # passed through — two different artifacts, two different versions, and conflating
            # them is exactly what `docs/WORK_ORDER_reference_ingest.md` §7.4 found in the
            # topology export. This one says which SIDECAR shape a reader is holding, which is
            # what makes the `defaulted` -> `compiler` rename announceable rather than
            # indistinguishable from a bug in the reader.
            "provenance": {"schema": PROVENANCE_SCHEMA,
                           "pit_id": twin_id, "schema_version": pit_row["schema_version"],
                           "code_fingerprint": pit_row["code_fingerprint"],
                           # Which tap this twin is a replica OF. A twin compiled from one
                           # vantage represents that vantage's view and nothing wider (D1/D9),
                           # and the sidecar is where a consumer can see which.
                           "vantage": pit_row.get("vantage"),
                           "vantages": pit_row.get("vantages"),
                           "network": pit_row.get("network"),
                           "population": population_block,
                           "fields": dict(sorted(provenance.items()))},
            "caveats": caveats}


def to_yaml(doc: dict) -> str:
    """Byte-stable YAML. `sort_keys=False` so the declared key order survives — a compiler whose
    output key order varied would make every re-compile look like a change (§9, `LES-002`)."""
    return yaml.safe_dump(doc, sort_keys=False, default_flow_style=False)
