"""Typed twin-config models (`docs/design/09-content-overlays.md`).

Deliberately separate from `axrange.ir.schema.IR` — a twin-config is a
DIFFERENT, smaller vocabulary an author writes (segments/conduits/nodes/
identity/data + params); `expand.py` is the ONLY place that turns one into a
Scenario IR dict. This module never touches `axrange.ir.schema` (no
version-locked IR_SCHEMA change — see the module docstring in `expand.py`
for why).

Mostly `Optional` fields, but — UNLIKE `axrange.ir.schema` — a **closed**
vocabulary (`extra="forbid"` since 2026-08-04; see `TCBase`). Semantic
validation still lives in `expand.py`'s expansion-time checks (shape errors ->
`TwinConfigShapeError`/`TwinConfigParamError`) and, once expanded, in the
UNCHANGED four-pass `axrange.validator`; `forbid` only closes the *spelling*
of the vocabulary itself.

**No tap-related content anywhere in this module** (`docs/
WORK_ORDER_tap_eligibility.md` D3, Authority: user, 2026-07-28) -- a twin
config carries no `tap:` flags and no `tap_points:` catalog; a config
authoring either now fails loudly at expansion time (`expand.py`'s
`expand_twin_config_raw`), never silently. Capture is authored entirely in
a standalone tap plan against the deployed twin's topology.

**No inert fields** (2026-08-04, Authority: user). An audit traced every
field in this vocabulary to a consumer; the six that reached nothing --
`TwinConfigSegment.purpose`, `TwinConfigNode.identity_role`,
`TwinConfigConduit.policy`, the top-level `TwinConfigIdentity.groups`/`.ous`,
and finally `TwinConfigNode.persona` (removed with
`docs/WORK_ORDER_role_persona_tier.md`, which ships the traffic profiles
carrying its intent for real) -- were removed. Two guards now keep the
vocabulary honest: `expand._reject_removed_keys` gives a RETIRED key an
actionable message, and `extra="forbid"` rejects a key that never existed at
all (LES-111, LES-172). Before adding a field here, make sure something reads
it: a field that only documents intent is a comment, and a field that looks
wired but is not is the trap this sweep existed to close.
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class TCBase(BaseModel):
    # `extra="forbid"` (2026-08-04, Authority: user) -- was `"allow"`. A
    # permissive model is what let five inert fields exist unnoticed and what
    # would silently absorb a typo'd `presona:`/`instal:` today (LES-111,
    # LES-172). `expand._reject_removed_keys` still runs FIRST, so a key that
    # was deliberately RETIRED gets its own actionable message ("author it in
    # a traffic profile") rather than pydantic's generic extra-field error;
    # `forbid` is the catch-all underneath it for keys that never existed.
    #
    # Scope note: the typed passthrough blocks (`fabric`/`routing`/
    # `resources`/`install`/`provides`) are declared `dict`/`list[dict]`, so
    # their INNER keys are unaffected by this -- only the twin-config's own
    # vocabulary is closed. `count: Any` still absorbs an unsubstituted
    # `"${N:25}"` on the `registry.load_twin_config` path.
    model_config = ConfigDict(extra="forbid", frozen=False)


class TwinConfigObservation(TCBase):
    """One `observation:` block — what the imprint compiler actually SAW of
    this entity (`docs/WORK_ORDER_deploy_gate.md` §3,
    `docs/design/13-golden-twin-fidelity.md` §3.4 "the cast describes, the
    deploy selects").

    A cast twin-config no longer arrives pre-filtered: the compiler emits
    every observed host and annotates it here, and `expand.py`'s deploy gate
    (`gate.py`, `DEPLOY_MIN_OCCUPANCY`) decides at expansion time which of
    them become nodes. One cast then serves every threshold.

    **A block describes the WHOLE spec, all `count:` members included** — the
    cast keeps no per-host identity, so the gate is all-or-nothing per spec.
    Per the 2026-09-04 ruling (Authority: user), a `count: N` group carries
    its MINIMUM member's `windows_seen`/`occupancy` and is kept or dropped by
    that weakest member.

    **No block means "always emitted"** (`gate.gate_verdict`) — hand-authored
    `twin-configs/` content and compiler-invented entities (a cast's `gw`,
    its `internet-sink-*`) carry none, and an `occupancy: 1.0` block on those
    would assert an observation nobody ever made
    (`docs/design/14-operator-assertions.md`'s `compiler` evidence class).

    The three numeric fields are declared `Optional` ONLY so a partial block
    is reported against the entity by NAME (`gate.check_observation` rule 6:
    "node 'file-server-10-80-64'") instead of a pydantic list index; all
    three are required in practice and expansion errors without them.
    `role_confidence` is genuinely optional — segments have none, and a
    role-less host legitimately has none (it is never defaulted; see
    `gate.gate_verdict` for the one comparison that reads absent as 0.0 --
    and note a SEGMENT may not carry the field at all, since a segment has
    no role: `gate.check_observation` rejects it there).
    """

    windows_seen: Optional[int] = None
    windows_total: Optional[int] = None
    occupancy: Optional[float] = None
    role_confidence: Optional[float] = None


class TwinConfigSegment(TCBase):
    """One `segments[]` entry -> an IR `networks[]` entry.

    `zone_type` is per-segment DOCUMENTATION of author intent, but the
    underlying Proxmox SDN model has exactly ONE zone type per scenario
    (`docs/ADAPTER.md`: VLAN when ANY network declares a `vlan`, else
    Simple) — `expand.py` computes the scenario-wide effective zone type as
    "vlan if ANY segment asks for vlan" and, when vlan, assigns every
    segment a distinct auto-incrementing VLAN tag (even ones that said
    "simple") so no two segments silently share one untagged broadcast
    domain. `sme-flat` is the one config where every segment says
    "simple" and the scenario stays a true Simple zone.
    """

    name: str
    cidr: str
    zone_type: Optional[str] = None  # "simple" | "vlan"; None -> "simple"
    # `purpose:` REMOVED (2026-08-04, Authority: user) -- it reached nothing
    # (zero references in `expand.py`), so it was a YAML comment with extra
    # steps. Author segment documentation as an actual YAML comment; a
    # leftover `purpose:` is rejected by `_reject_removed_keys`.
    gateway: Optional[str] = None  # auto-assigned (network+1) if a router leg needs one
    # `tap:` REMOVED here (`docs/WORK_ORDER_tap_eligibility.md` D3, Authority:
    # user, 2026-07-28) -- a twin config authors no capture intent; taps are
    # authored entirely in a standalone tap plan against the deployed twin.
    ospf_area: Optional[Any] = None  # fabric slice passthrough (Network.ospf_area)
    vni: Optional[int] = None  # fabric slice passthrough (Network.vni)
    when: Optional[str] = None  # gate: only emitted if params[when] is truthy
    # What the imprint compiler observed of this segment; drives the deploy
    # gate (`gate.py`). Absent on every hand-authored config -> never gated
    # out. A segment gated out while an emitted node still has a leg on it is
    # a hard error, not a silent repair (expand.py, work order §4.3 step 3).
    observation: Optional[TwinConfigObservation] = None
    # `isolated: true` -> the IR's `Network.isolated` (the ratified NET-010
    # exemption, `validator/checks/network.py`). In a twin config it means ONE
    # thing: this segment is a separate broadcast domain CARRYING THE SAME
    # SUBNET as exactly one non-isolated sibling (its "anchor") -- an access
    # segment behind an L2 bridge VM, the far side of an 802.1Q trunk. The
    # anchor is where the gateway lives; the extension inherits it, and IP
    # allocation is shared per CIDR so the two can never hand out one address
    # twice (`expand._build_ir`, docs/WORK_ORDER_corp_twin.md §10 P0). Any
    # other use (a free-floating overlap) is refused at expansion. Absent on
    # every config written before 2026-09-26 -> byte-identical IR.
    isolated: Optional[bool] = None
    # Addresses the allocator must never hand out and no leg may pin: an
    # address owned by something that is not a node leg (a VRRP VIP a
    # keepalived pair floats between its boxes, docs/WORK_ORDER_corp_twin.md
    # §10 P0). Shared with every segment on the same CIDR (the allocator is
    # per CIDR). Never emitted into the IR. Absent -> byte-identical IR.
    reserved: Optional[list[str]] = None


class TwinConfigListen(TCBase):
    """One `nodes[].listen[]` entry -> an IR `App.listen` entry
    (`docs/WORK_ORDER_protocol_faithful_services.md` S1, D2(b),
    Authority: user, 2026-09-04).

    The twin's **declared** listen set: what this host says it serves.
    ABSENT (no `listen:` key at all) means "this config makes no claim about
    what this host serves"; an EMPTY list is refused at the author
    (`expand._check_listen`), because "I claim it serves nothing" is not a
    statement any consumer acts on differently and is far more likely to be a
    half-finished edit. It is deliberately NOT evidence that anything is
    bound -- the fidelity
    scorecard's row 4 (in-guest `ss -lnH -tu`) and row 5 (a protocol-aware
    probe) remain the measurement of record, and this set is reported
    ALONGSIDE the observed one, never instead of it.

    Both fields are REQUIRED (unlike the IR's own permissive `Listen`, whose
    two fields are `Optional` like every other field in that version-locked
    module): a half-authored `{port: 88}` reaches the published inventory and
    the scorecard as a fact nobody can probe, so it is refused where it is
    written. `expand._check_listen` additionally range-checks the port and
    closes the transport vocabulary, naming
    `twin_config.nodes[i].listen[j].<field>` (LES-211: a refusal must name the
    clause that fired).

    Shape-compatible with `docs/design/14-operator-assertions.md` §3's
    `add: {kind: host, ..., listen: [{port, proto}]}` record, so an operator
    assertion round-trips into a cast twin config without a translation step.

    **`port` is `Any`, not `int`, for the same reason `TwinConfigNode.count`
    is** (review S5): a STRICT load (`registry.load_twin_config`, which
    `axrange twin-config show` and -- more importantly --
    `fidelity.reference.load_reference_bundle` both use) reads the config
    BEFORE `${PARAM}` substitution, so an `int` annotation would refuse a
    param-templated `port: "${P:88}"` there and take the whole scorecard
    down over a field it only ever displays. The real check runs at
    expansion, where the value is a resolved literal:
    `expand._check_listen` coerces with `int()` and refuses loudly, naming
    `twin_config.nodes[...].listen[j].port`. `proto` stays `str` -- it is
    never a numeric knob and a template there would be `str` anyway.
    """

    port: Any
    proto: str


class TwinConfigNodeLeg(TCBase):
    """One `nodes[].nets[]` entry — a multi-homed node's leg."""

    segment: str
    # An address, or the literal `none` (== `l2: true`): a pure LAYER-2 leg --
    # a bridge-port NIC on an L2 device VM (`scripts/serve-l2switch.sh`) that
    # must carry no address. The allocator skips it and the IR leg is emitted
    # with no `ip:` (docs/WORK_ORDER_corp_twin.md §10 P0). Refused on a
    # `kind: router` (a router leg IS an address).
    ip: Optional[str] = None
    l2: Optional[bool] = None
    # `tap:` REMOVED here (`docs/WORK_ORDER_tap_eligibility.md` D3) -- see
    # `TwinConfigSegment.tap`'s comment above.


class TwinConfigNode(TCBase):
    """One `nodes[]` entry. `count > 1` fans out `count` identical clones
    (ids `{name}01`, `{name}02`, ... — `name` alone when `count == 1`), each
    given its own auto-allocated IP on its segment(s) unless the entry (or a
    per-leg override) sets one explicitly.

    **`name` vs `role`** (`docs/WORK_ORDER_role_persona_tier.md` §4.5,
    Authority: user, 2026-08-04) -- these were ONE required field (`role:`)
    until the role/persona decoupling split them:

      * **`name`** is the node-id stem. Purely syntactic: it names the host,
        and through `naming.py` its VM. Required.
      * **`role`** is the OPTIONAL semantic baseline published as the twin's
        `hosts[].role` -- what personas resolve `peer_role:` against and what
        a traffic profile's `assignments[].role` addresses. A traffic
        profile's `roles:` block OVERRIDES it at runtime without touching the
        VM (`axrange.planner.inventory` resolves tfc-assignment -> this
        baseline -> None).

    Every shipped config sets both to the same string, which is exactly what
    keeps node ids byte-identical across the split. Omitting `role:` is the
    supported "bare-bone twin" authoring choice: the twin then publishes no
    role at all and a traffic profile supplies the whole role map.
    """

    name: str
    kind: str = "host"  # "host" | "router"
    image: str
    role: Optional[str] = None
    # `persona:` REMOVED (2026-08-04, Authority: user,
    # `docs/WORK_ORDER_role_persona_tier.md`) -- it was INERT here: read only
    # by a catalog typo-warning, never written to the IR, never reaching the
    # traffic tier. Behavior belongs to the `tfc` tier; author it in a traffic
    # profile (`twin-configs/<id>/tfc-*.yaml`).
    # `identity_role:` REMOVED (2026-08-04, Authority: user) -- it had ZERO
    # references anywhere in `axrange/`, including this package. Identity is
    # authored in `identity.users[]`; a leftover `identity_role:` is rejected
    # by `_reject_removed_keys`.
    # `Any`, not `int`: an UNSUBSTITUTED load (`registry.load_twin_config`,
    # used by `axrange twin-config show`) may still hold a raw template
    # string like `"${N_WORKSTATIONS:25}"` here -- `expand.py` substitutes
    # BEFORE constructing this model, so by the time expansion's own
    # `TwinConfig.model_validate` runs, `count` is already a real int;
    # `int(spec.count)` in `_build_ir` coerces defensively either way.
    count: Any = 1
    net: Optional[str] = None  # single-homed shorthand: a segment name
    nets: Optional[list[TwinConfigNodeLeg]] = None  # multi-homed (router/conduit)
    requires: Optional[list[str]] = None
    resources: Optional[dict[str, Any]] = None
    joins: Optional[str] = None  # AD domain FQDN this node joins (Node.joins passthrough)
    routing: Optional[dict[str, Any]] = None  # fabric slice routing intent passthrough
    # No separate top-level `apps:` config section (brief §6.1's schema
    # doesn't have one) -- a role's install actions live directly on the
    # node; `expand.py` synthesizes one App + one app->host Binding per node
    # that declares `install:` (this is CONTENT vocabulary, not a second
    # IR section).
    install: Optional[list[dict[str, Any]]] = None  # App.install passthrough
    provides: Optional[list[dict[str, Any]]] = None  # App.provides passthrough
    # The DECLARED listen set (`docs/WORK_ORDER_protocol_faithful_services.md`
    # S1) -> `App.listen` on this node's app. A node that declares `listen:`
    # but NO `install:` still gets an app synthesized to carry it (`expand.
    # _build_ir`), because `App` is the only IR object with a `listen` field --
    # the same reason `provides:` rides on an app rather than on the node.
    # NOT inert: `pass1_emit` carries it into `clone-vm`'s `node_listen`
    # input, `planner.inventory` publishes it as `hosts[].listen`, and the
    # fidelity scorecard reports it against the observed set (LES-172 -- a
    # field that reaches nothing is a false affordance; this one is traced
    # end to end).
    listen: Optional[list[TwinConfigListen]] = None
    when: Optional[str] = None  # gate: only emitted if params[when] is truthy
    # What the imprint compiler observed of this host; drives the deploy gate
    # (`gate.py`). `when:` (authored intent) is applied FIRST and this
    # (observed evidence) second. A `kind: router` node is NEVER gated out,
    # block or no block -- gating one out disconnects surviving segments and
    # turns a threshold change into a topology break.
    observation: Optional[TwinConfigObservation] = None


class TwinConfigConduit(TCBase):
    """One `conduits[]` entry — DOCUMENTATION of an intended inter-segment
    policy (`docs/SLICE_ROUTER.md`'s per-rule firewall policy is a
    follow-on, not modeled by the IR yet). The actual topology comes from
    `nodes[]` (a `kind: router` node multi-homed across the segments it
    joins); `expand.py` cross-checks that some router node actually joins
    every declared conduit's two segments and errors loudly if not (the
    same "don't let content silently not do what it says" posture as tap
    points).
    """

    from_: str = Field(alias="from")
    to: str
    # `policy:` REMOVED (2026-08-04, Authority: user) -- zero references in
    # `expand.py`. The conduit ENTRY is load-bearing (the cross-check below
    # errors when no router implements it); its `policy:` string was not.
    # Per-rule firewall policy remains unbuilt (`docs/SLICE_ROUTER.md`); when
    # it lands it gets a real, wired IR field rather than reviving this one.

    # `extra="forbid"` for the same reason as `TCBase` -- this model only
    # re-declares `model_config` to add `populate_by_name` for the `from`
    # alias, so it must repeat the closed-vocabulary setting too or the
    # conduit block would be the one hole left in it.
    model_config = ConfigDict(extra="forbid", frozen=False, populate_by_name=True)


class TwinConfigIdentityUser(TCBase):
    id: str
    groups: Optional[list[str]] = None
    ou: Optional[str] = None
    password: Optional[str] = None
    spn: Optional[str] = None
    asrep_roastable: Optional[bool] = None
    acl: Optional[list[dict[str, str]]] = None
    delegation: Optional[str] = None
    host: Optional[str] = None  # local-user host binding (no domain)


class TwinConfigIdentity(TCBase):
    """The `identity:` block. NOTE the per-USER `groups:`/`ou:` on
    `TwinConfigIdentityUser` are wired (they reach `overlays.identity` in
    `expand.py`) — it was only the top-level, whole-domain lists that were
    inert and are now removed."""

    domain: Optional[str] = None
    users: Optional[list[TwinConfigIdentityUser]] = None
    # Top-level `groups:`/`ous:` REMOVED (2026-08-04, Authority: user) --
    # `expand.py` reads only `u.groups`/`u.ou` (per-user); these two
    # whole-domain lists reached nothing. Author them per-user; a leftover
    # top-level `groups:`/`ous:` is rejected by `_reject_removed_keys`.


class TwinConfigData(TCBase):
    dataset: str
    on_role: str  # a node id this dataset attaches to
    path: Optional[str] = None  # in-guest path; auto-defaulted (expand.py) if omitted
    classification: Optional[str] = None
    crown_jewel: Optional[bool] = None
    synthetic_size: Optional[str] = None


# `TwinConfigTapPoint` (the `tap_points[]` catalog) REMOVED here (`docs/
# WORK_ORDER_tap_eligibility.md` D3, Authority: user, 2026-07-28) -- the
# named/labeled tap-point catalog is retired entirely, not relocated. A
# `tap_points:` key in a loaded config's raw dict now fails LOUDLY at
# expansion time (`expand.py`'s `_reject_removed_keys`), never silently
# absorbed -- and since 2026-08-04 this model is `extra="forbid"` anyway, so
# even an un-tabled stray key can no longer be swallowed (LES-111).


class TwinConfigEgress(TCBase):
    """The twin-level `egress:` block — the twin's CONTAINMENT POSTURE
    (GAP-101, `docs/WORK_ORDER_protocol_engines.md` §E1).

    Passed through to the expanded IR's top-level `egress:`
    (`axrange.ir.schema.Egress`, where the full reasoning lives) and from
    there onto EVERY `kind: router` node's `configure-router` op input by
    `axrange.planner.egress.resolve_egress_policy`. Twin-level rather than
    per-node because a twin with two routers must not be able to declare two
    different containment postures — one router left in `provision` re-opens
    the real internet for everything behind it.

    Traced to a consumer end to end (LES-172), which is the bar for a field
    existing here at all: author -> `expand._build_ir` -> `ir["egress"]` ->
    `pass1_emit` -> `configure-router`'s `egress` input ->
    `rest._apply_egress_policy` -> the in-guest engine, and back out again as
    `egress_phase` on the op row, which `te info`/`te inventory` publish.

    OMITTING the block entirely keeps today's behavior exactly (permissive
    LAN->WAN masquerade, NO policy installed) — every shipped twin config
    apt-installs over that masquerade. Omitting only `phase:` inside a
    declared block is `measure` (default CLOSED).
    """

    phase: Optional[str] = None      # provision | measure; omitted => measure
    sink: Optional[str] = None       # node NAME to take the sink address from;
                                     # default: the twin's unique `role: sink` node
    sink_ip: Optional[str] = None    # explicit override (wins over resolution)
    mode: Optional[str] = None       # default | cidrs
    cidrs: Optional[list[str]] = None  # mode: cidrs only


class TwinConfig(TCBase):
    id: str
    description: Optional[str] = None
    params: dict[str, Any] = Field(default_factory=dict)
    segments: list[TwinConfigSegment] = Field(default_factory=list)
    conduits: list[TwinConfigConduit] = Field(default_factory=list)
    nodes: list[TwinConfigNode] = Field(default_factory=list)
    identity: Optional[TwinConfigIdentity] = None
    data: list[TwinConfigData] = Field(default_factory=list)
    fabric: Optional[dict[str, Any]] = None  # gated top-level fabric: passthrough (datacenter FABRIC)
    fabric_when: Optional[str] = None  # gate: fabric: only emitted if params[fabric_when] is truthy
    egress: Optional[TwinConfigEgress] = None  # GAP-101: the twin's containment posture,
                                   # passed through to the IR's top-level `egress:` and
                                   # from there to every router. ABSENT == unchanged.
    default_agents: Optional[bool] = None  # passthrough to the IR's `scenario.default_agents`
                                   # (docs/IR_SCHEMA.md; A3 Stage A). Unset (None) keeps the
                                   # baked-in default-ON sweep exactly as before -- this field
                                   # only exists so a config CAN opt out, which it previously
                                   # could not: `expand.py` emitted a fixed `scenario:` block
                                   # with no way to reach the IR field, and TCBase forbids
                                   # extras, so there was no workaround short of hand-editing
                                   # the materialized IR. A cast twin measured on-the-wire
                                   # wants this off: node-exporter scrapes and wazuh-agent
                                   # enrollment are twin-only traffic with no counterpart in
                                   # the source capture the twin is being compared against.


class TwinConfigFile(TCBase):
    """The on-disk wrapper: `twin-configs/<id>/<id>.yaml`'s root shape is
    `{twin_config: {...}}` — mirrors the Scenario IR's own `{scenario: {...}}`
    root-key convention."""

    twin_config: TwinConfig
