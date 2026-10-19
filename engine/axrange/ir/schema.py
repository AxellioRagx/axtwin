"""Typed IR models per docs/IR_SCHEMA.md (v1).

The IR is loaded permissively from YAML: validation lives in axrange.validator,
not in Pydantic field constraints. The schema's job here is to give the
validator a well-typed object graph to read, not to enforce rules. Most fields
are Optional with no default constraints so a malformed IR still parses into a
shape the rules can inspect and report on.

A few light defaults are applied where IR_SCHEMA explicitly defines one
(`reproducible: false`, empty lists/dicts), to keep rule code free of
None-handling boilerplate.

**No `tap:` field anywhere in this module** (`docs/WORK_ORDER_tap_eligibility.md`,
Authority: user, 2026-07-28) — the twin substrate is tap-agnostic; capture
eligibility, scope, and target are authored entirely in a standalone tap
plan (`axrange.o2control.taps`), resolved against the twin's published
topology, never against a flag in the Scenario IR. `scenario_schema_version`
recognized set is `{1, 2, 3}` (STR-020) — `3` marks this removal; a
pre-existing `1`/`2` IR is parsed identically (the removal applies
regardless of declared version, like every other IR change here).
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


# Pydantic v2 base: forbid mutation, allow extra keys (caught by STR-180 info).
class IRBase(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=False, str_strip_whitespace=False)


class NodeKind(str, Enum):
    HOST = "host"
    ROUTER = "router"
    SWITCH = "switch"


class NetRef(IRBase):
    """One entry in a multi-homed node's `nets:` list."""

    net: Optional[str] = None
    ip: Optional[str] = None
    # `tap:` REMOVED here (docs/WORK_ORDER_tap_eligibility.md, Authority:
    # user, 2026-07-28) -- the twin substrate is now tap-agnostic; a tap
    # plan's `port`/`segment`/`perimeter`/`boundary` selectors resolve
    # against the twin's FULL published topology (`hosts[].nets[]`), not a
    # subset flagged here. A leftover `tap:` on a leg is rejected loudly at
    # validate time (STR-200), not silently absorbed by Pydantic's
    # `extra="allow"`.


class StaticRoute(IRBase):
    to: Optional[str] = None
    via: Optional[str] = None


# --- Fabric slice — dynamic routing + overlay (docs/design/04-networking-capture.md) ---

class EbgpPeer(IRBase):
    neighbor: Optional[str] = None   # peer IP
    remote_asn: Optional[int] = None


class RoutingBgp(IRBase):
    asn: Optional[int] = None
    router_id: Optional[str] = None          # optional; derive from loopback if unset
    rr_server: bool = False                   # spine = route-reflector server
    rr_clients_of: Optional[list[str]] = None  # leaf points at its RR spines (node ids)
    evpn: bool = False                        # enable l2vpn-evpn AF on the iBGP peer-group(s)
    vtep: bool = False                        # leaf is a VXLAN VTEP
    ebgp_peers: Optional[list[EbgpPeer]] = None
    originate: Optional[list[str]] = None     # eBGP `network` prefixes


class RoutingOspf(IRBase):
    router_id: Optional[str] = None           # optional; derive from loopback if unset


class RoutingIsis(IRBase):
    net: Optional[str] = None                 # NET/NSAP, e.g. 49.0001.0000.0000.0001.00


class NodeRouting(IRBase):
    """Fabric routing intent on a `kind: router` node (docs/design/04-networking-capture.md)."""

    region: Optional[str] = None   # → fabric.regions[].id (picks the IGP + redistribution)
    role: Optional[str] = None     # spine|leaf|border|abr|area|provider|edge
    loopback: Optional[str] = None  # dum0; router-id / iBGP update-source / VTEP source
    isis: Optional[RoutingIsis] = None
    ospf: Optional[RoutingOspf] = None
    bgp: Optional[RoutingBgp] = None
    redistribute: Optional[list[str]] = None  # extra redistribute sources (border auto-adds maps)
    bfd: bool = False                          # attach BFD to IGP neighbors


class Resources(IRBase):
    cpu: Optional[Any] = None  # int per spec; permissive for rule reporting
    ram: Optional[Any] = None  # k8s size string
    disk: Optional[Any] = None


class Boot(IRBase):
    order: Optional[int] = None


class DhcpConfig(IRBase):
    enabled: Optional[bool] = None
    range: Optional[list[Any]] = None


class Network(IRBase):
    id: Optional[str] = None
    cidr: Optional[str] = None
    vlan: Optional[int] = None
    gateway: Optional[str] = None
    dns: Optional[list[str]] = None
    dhcp: Optional[DhcpConfig] = None
    isolated: Optional[bool] = None  # NET-010 exception flag (forward-looking)
    egress: Optional[bool] = None  # opt-in SDN subnet+SNAT for internet/apt
                                   # egress (router slice Phase 1); needs a gateway
    # `tap:` REMOVED here (docs/WORK_ORDER_tap_eligibility.md, Authority:
    # user, 2026-07-28) -- see NetRef.tap's comment above; a leftover
    # `tap:` on a network is rejected loudly (STR-200).
    transport: bool = False  # an isolated capture-transport vnet the in-scenario
                             # sensors sit on (host-reachable for gretap delivery;
                             # not bridged to scenario traffic) — sensors design,
                             # docs/design/06-taps-sensors.md.
    ospf_area: Optional[Any] = None  # fabric slice: OSPF area for router legs on this
                                     # net (int or dotted); router↔router ⇒ p2p adjacency.
    vni: Optional[int] = None  # fabric slice: EVPN L2VNI — leaves attached to this net
                               # become VTEPs bridging it; a shared vni ⇒ stretched L2.


class Node(IRBase):
    id: Optional[str] = None
    kind: Optional[Any] = None  # NodeKind value or arbitrary; STR-080 enforces
    net: Optional[str] = None
    nets: Optional[list[NetRef]] = None
    image: Optional[str] = None
    requires: Optional[list[str]] = None
    resources: Optional[Resources] = None
    # `tap:` REMOVED here (docs/WORK_ORDER_tap_eligibility.md, Authority:
    # user, 2026-07-28) -- see NetRef.tap's comment above.
    boot: Optional[Boot] = None
    aliases: Optional[list[str]] = None
    static_routes: Optional[list[StaticRoute]] = None  # router-only
    joins: Optional[str] = None  # W2: AD domain FQDN this host joins (explicit
                                 # `node.joins:`, superseding OP_CATALOG I1=infer)
    routing: Optional[NodeRouting] = None  # fabric slice: dynamic-routing/overlay intent
                                           # (router-only; docs/design/04-networking-capture.md)
    default_agents_off: Optional[list[str]] = None  # A3 Stage A (docs/design/
                                 # 08-exercise-measurement.md): opt THIS node out of one
                                 # or more baked-in default agents when
                                 # `scenario.default_agents: true`. Values: "defense" |
                                 # "exporter". adversary is never a blanket default (it's
                                 # opted IN per node via overlays.adversary[].targets:),
                                 # so listing "adversary" here simply drops the node from
                                 # target processing if it's also listed there.
    role: Optional[str] = None  # NEW (docs/WORK_ORDER_traffic_tier.md,
                                 # docs/design/02-twin-model.md; Authority: user,
                                 # 2026-07-24; scenario_schema_version 1->2): a
                                 # persona-assignable role tag (e.g. "corp-workstation",
                                 # "domain-controller") -- what the Traffic tier binds a
                                 # persona onto (`hosts[].role` in the published twin
                                 # inventory, axrange.planner.inventory.build_twin_inventory).
                                 # Optional; additive; unrelated to `kind` (host/router/switch,
                                 # a topology-shape tag) or an identity/overlay role.


class InstallAction(IRBase):
    """Permissive — STR-130 enforces the closed action-key set."""

    pkg: Optional[str] = None
    version: Optional[str] = None
    config: Optional[str] = None
    role: Optional[str] = None
    container: Optional[str] = None
    script: Optional[str] = None


class Listen(IRBase):
    """One declared listen `{port, proto}` on an app.

    **Its first consumer landed 2026-09-05** (`docs/
    WORK_ORDER_protocol_faithful_services.md` S1, D2(b), Authority: user;
    `docs/design/13-golden-twin-fidelity.md` §8.2). The field itself is
    unchanged and predates that build -- `docs/IR_SCHEMA.md` called it
    "informational for network policy generation" with ZERO readers anywhere
    in `axrange/`. It is now the twin's **declared listen set**: what the
    twin says it serves, as distinct from what a capture OBSERVED it serving
    and from what an in-guest `ss -lnH -tu` finds actually bound.

    Wiring (all additive, no `scenario_schema_version` bump -- no shipped
    content set this field before the build, so every existing IR expands and
    hashes byte-identically):

      * authored on a twin-config node (`twinconfig.schema.TwinConfigNode.
        listen`) and passed through to the node's app by `twinconfig.expand`;
      * carried to `clone-vm`'s `node_listen` resolved input **only when
        non-empty** (`planner.pass1_emit`), which is what lets the published
        inventory read it back off an unconditional per-host op;
      * published as `hosts[].listen` by `planner.inventory.
        build_twin_inventory` (`te inventory --json`, `GET /api/deploys/
        {id}/inventory`);
      * reported against the OBSERVED listen set by the fidelity scorecard's
        rows 4/5 (`fidelity.rows`, informational counts only -- it changes no
        verdict rule).

    A declaration is not evidence: nothing here asserts the port is actually
    bound in the guest. Row 4 (`ss` in-guest) and row 5 (a protocol-aware
    probe) are the two channels that measure that, and they stay the
    measurement of record.
    """

    port: Optional[int] = None
    proto: Optional[str] = None


class App(IRBase):
    id: Optional[str] = None
    role: Optional[str] = None
    install: Optional[list[InstallAction]] = None
    listen: Optional[list[Listen]] = None
    env: Optional[dict[str, Any]] = None
    provides: Optional[list[dict[str, Any]]] = None


class ScheduleInterval(IRBase):
    """Jittered cadence bounds — `schedule.interval: {min, max}` (duration
    strings like "30s"/"5m"/"2h"). NEW (docs/SLICE_ACTIVITY.md §11.1/§11.4,
    the schedule executor). Additive to the v1 IR — stays
    `scenario_schema_version: 1`, matching the fabric-slice precedent
    (docs/IR_SCHEMA.md, the 2026-06-29 fabric `routing:` addition)."""

    min: Optional[str] = None
    max: Optional[str] = None


class Schedule(IRBase):
    start: Optional[str] = None
    end: Optional[str] = None
    days: Optional[list[str]] = None
    once: Optional[bool] = None
    interval: Optional[ScheduleInterval] = None  # NEW: seeded jittered cadence
                                                  # within the window (§11); mutually
                                                  # exclusive with `once: true`
                                                  # (validator ACT-060).


class InlineActivity(IRBase):
    # Each step is a permissive dict ({tool:, args?, target?} or {run:}) so no
    # per-step Pydantic model gates its keys -- `technique:` (an ATT&CK
    # technique id, e.g. "T1059", author-provided) is ADDITIVE ground-truth
    # labeling on a step (A4, docs/design/08-exercise-measurement.md): when
    # present, the rendered runner (config_exec/render.py::render_activity_
    # runner) emits a `[axr-truth]` marker for that step. Optional -- an
    # unlabeled step just renders with no marker, unchanged from before A4.
    # Additive to the v1 IR -- stays `scenario_schema_version: 1`, same
    # precedent as the fabric `routing:` / schedule `interval:` additions.
    steps: Optional[list[dict[str, Any]]] = None


class Activity(IRBase):
    id: Optional[str] = None
    actor: Optional[str] = None
    behavior: Optional[str] = None
    overrides: Optional[dict[str, Any]] = None
    inline: Optional[InlineActivity] = None
    intensity: Optional[float] = None
    schedule: Optional[Schedule] = None
    seed: Optional[Any] = None  # RNG seed for a jittered `schedule.interval`'s
                                # cadence (docs/SLICE_ACTIVITY.md §11.2) --
                                # un-reserved 2026-07-10 (was ACT-050-only-warn
                                # in the one-shot MVP; ACT-050 now lifts when a
                                # schedule.interval is present, since the seed
                                # has a purpose there).


class Infra(IRBase):
    networks: list[Network] = Field(default_factory=list)
    nodes: list[Node] = Field(default_factory=list)


class Layers(IRBase):
    infra: Infra = Field(default_factory=Infra)
    apps: list[App] = Field(default_factory=list)
    activity: list[Activity] = Field(default_factory=list)


class Binding(IRBase):
    """One bindings[] entry. Kind is determined by which keys are present.

    Recognized kinds (per IR_SCHEMA §6): app→host, activity→host,
    activity→targets, plus overlay bindings (currently absent in the IR
    structure — overlays bind through their own fields).

    The former tap→sensor binding kind (`tap_host`/`tap_net`/`sensor`) is
    REMOVED (`docs/WORK_ORDER_tap_eligibility.md` D6, Authority: user,
    2026-07-28) -- it was te-side tap routing the standalone tap plan
    already replaced; a tap plan now selects purely by `scope`/`target`
    against the twin's published topology. A leftover `tap_host`/`tap_net`
    binding needs no dedicated validator rule -- STR-100's closed
    binding-kind set already rejects it (no kind matches those keys any
    more).
    """

    app: Optional[str] = None
    host: Optional[str] = None
    activity: Optional[str] = None
    targets: Optional[list[str]] = None
    conditions: Optional[dict[str, Any]] = None  # accepted-and-ignored in v1


class Sensor(IRBase):
    """One top-level sensors[] entry (sensors design, 2026-06-23 — unifies the
    former capture appliance + Suricata network-sensor; evolves per-tap sensor routing).

    A **typed** capture/analysis node. `type` selects the tool from the
    sensor-type registry (`axrange.sensors`); `None` defaults to `tcpdump-sensor`
    (the former standing capture appliance). `captures` selects which tapped
    net/host sources feed it (omitted = all taps); exactly one sensor is
    `primary` (the default for any tap without a tap→sensor binding).
    `transport` names the isolated capture vnet its NIC binds to. **Phase A**
    resolves the transport endpoint from config
    (`SENSOR_<ID>_TRANSPORT_IP`); the in-scenario sensor-VM deploy (image/resources/
    export materialized) is **Phase B** (docs/design/06-taps-sensors.md).
    """

    id: Optional[str] = None
    type: Optional[str] = None  # tcpdump-sensor | suricata-sensor | zeek-sensor;
                                # None ⇒ tcpdump-sensor (the former capture appliance)
    primary: bool = False
    transport: Optional[str] = None  # transport vnet id its capture NIC binds to
    captures: Optional[list[str]] = None  # tapped net/host ids it subscribes to
    image: Optional[str] = None  # override the type's default base image (Phase B)
    resources: Optional[Resources] = None  # VM sizing — pcap storage etc. (Phase B)
    export: Optional[dict[str, Any]] = None  # artifact export config (Phase B/C)
    rules: Optional[str] = None  # detection ruleset FILE (path rel. to the scenario
                                 # dir) loaded by a `suricata-sensor`/`zeek-sensor` —
                                 # the in-scenario IDS blue-team content. Read at emit
                                 # time into the deploy-sensor config_content.


class IdentityEntry(IRBase):
    id: Optional[str] = None
    type: Optional[str] = None
    domain: Optional[str] = None
    groups: Optional[list[str]] = None
    privileges: Optional[list[str]] = None
    external: bool = False
    host: Optional[str] = None  # W2: local-user host binding for no-`domain:`
                                # identities (New-LocalUser / useradd on this node;
                                # explicit, parallels node.joins — SLICE_WINDOWS_AD §6.3)
    # Vulnerable-AD / SPN-OU modeling (makes the domain exploitable — the OP-ACT AD
    # attack chain's intended targets; docs/SLICE_WINDOWS_AD.md §9):
    password: Optional[str] = None  # explicit account password (often weak — a
                                    # crackable Kerberoast/AS-REP target); falls back
                                    # to the lab default when unset.
    spn: Optional[str] = None       # a servicePrincipalName -> Kerberoastable service
                                    # account (forced RC4 so the TGS is crackable).
    ou: Optional[str] = None        # organizational unit to create + place the account
                                    # in (idempotent New-ADOrganizationalUnit).
    asrep_roastable: Optional[bool] = None  # set DONT_REQUIRE_PREAUTH -> AS-REP roastable.
    # Dangerous-ACL / delegation weakening — the BloodHound attack-path edges (§9):
    acl: Optional[list[dict[str, str]]] = None  # this principal is granted a dangerous
                                    # right over a target: [{right: GenericAll, target:
                                    # <identity-id|group>}]. Rights: GenericAll /
                                    # GenericWrite / WriteDacl / WriteOwner /
                                    # ForceChangePassword / AddMember.
    delegation: Optional[str] = None  # "unconstrained" -> TRUSTED_FOR_DELEGATION (the
                                    # account can impersonate any user that auths to it).
    delegation_to: Optional[list[str]] = None  # constrained delegation: SPNs the account
                                    # may delegate to (msDS-AllowedToDelegateTo).


class DataEntry(IRBase):
    id: Optional[str] = None
    host: Optional[str] = None
    path: Optional[str] = None
    classification: Optional[str] = None
    crown_jewel: Optional[bool] = None
    synthetic_size: Optional[str] = None


class DefenseObserves(IRBase):
    net: Optional[str] = None
    mode: Optional[str] = None


class DefenseEntry(IRBase):
    id: Optional[str] = None
    tool: Optional[str] = None
    host: Optional[str] = None
    observes: Optional[list[DefenseObserves]] = None
    installed_on: Optional[list[str]] = None
    ruleset: Optional[str] = None
    config: Optional[str] = None
    tap_from: Optional[str] = None


class AdversaryEntry(IRBase):
    """A3 Stage A (docs/design/08-exercise-measurement.md,
    docs/design/09-content-overlays.md): declarative red-team overlay,
    parallel to `overlays.defense`. Plants a Sandcat (Caldera) agent on
    `targets:` and points it at the Caldera `server:` (the O2 Caldera server --
    Stage B, not yet stood up; `server:` is a REQUIRED explicit field for now,
    a TODO for A1/o2control to resolve live). Execution/playout is MANUAL at
    the Caldera console (brief §4/§7) -- this overlay only plants + points the
    agent, it never runs an operation itself."""

    id: Optional[str] = None
    targets: Optional[list[str]] = None  # nodes to plant the Sandcat agent on
    server: Optional[str] = None         # Caldera server host[:port] or URL
                                          # (TODO Stage B/A1: default from a
                                          # live O2 inventory; explicit for now)
    group: Optional[str] = None          # Caldera agent group tag (default "red")
    adversary: Optional[str] = None      # informational: adversary-profile/plan
                                          # reference (not compiled here -- see
                                          # docs/design/09-content-overlays.md
                                          # for the `engine: atomic|caldera`
                                          # OP-ACT-compiling ladder, a separate,
                                          # not-yet-built follow-on)
    techniques: Optional[list[str]] = None  # A4 (docs/design/08-exercise-measurement.md):
                                          # informational ATT&CK technique ids
                                          # this Caldera plan is expected to exercise
                                          # -- ground truth for a Caldera-driven run
                                          # comes from its OPERATION REPORT at
                                          # playout time (not compiled/executed here,
                                          # same "not compiled" stance as `adversary`
                                          # above); this list is only a forward-
                                          # looking hint for the Caldera-report-
                                          # ingestion adapter (deferred, "clean seam"
                                          # per the A4 work order).


class Overlays(IRBase):
    identity: Optional[list[IdentityEntry]] = None
    data: Optional[list[DataEntry]] = None
    defense: Optional[list[DefenseEntry]] = None
    adversary: Optional[list[AdversaryEntry]] = None  # A3 Stage A


class FabricRegion(IRBase):
    id: Optional[str] = None
    igp: Optional[str] = None  # ospf | isis | both | none
    isis_area: Optional[str] = None  # e.g. 49.0001 (informational; per-router NET carries it)


class FabricTenant(IRBase):
    """Symmetric-IRB tenant — a routing VRF + its L3VNI (docs/design/04-networking-capture.md).

    When present, VTEP leaves create an anycast distributed gateway on each L2VNI
    bridge (the network's `gateway`) and route between segments over the L3VNI via
    EVPN Type-5. Single tenant in v1.
    """

    name: Optional[str] = None   # VRF name
    l3vni: Optional[int] = None  # the routing (L3) VNI
    table: Optional[int] = None  # VRF kernel route-table id


class Fabric(IRBase):
    """Top-level fabric block — dynamic routing + overlay (docs/design/04-networking-capture.md)."""

    fabric_asn: Optional[int] = None  # default iBGP AS
    regions: Optional[list[FabricRegion]] = None
    tenant: Optional[FabricTenant] = None  # symmetric-IRB tenant VRF + L3VNI (§3.5)


class Egress(IRBase):
    """Top-level egress block — the twin's CONTAINMENT POSTURE (GAP-101,
    `docs/WORK_ORDER_protocol_engines.md` §E1).

    Shaped and placed exactly like `fabric:` one class up: a cross-node
    policy the author states ONCE for the whole twin, which `pass1_emit`
    attaches to every `kind: router` node's `configure-router` op
    (`build_fabric_inputs` is the same pattern). It is TWIN-level, not
    node-level, on purpose: containment is a property of the whole twin, and
    a twin with two routers must not be able to declare two different
    postures — one router left in `provision` re-opens the real internet for
    everything behind it, which is exactly the breach GAP-101 records (a
    `github.com` handshake that returned a real Sectigo certificate instead
    of the lab CA's).

    ABSENT (the whole block omitted) means UNCHANGED: no policy is installed
    and the router keeps today's permissive LAN->WAN masquerade. That is
    deliberate and must not become "closed" — every shipped twin config
    apt-installs over that masquerade, and a silent flip to `measure` would
    break all of them. DECLARED-but-`phase`-less is `measure` (default
    CLOSED, `adapter_proxmox.egress_render.normalize_phase`): once a twin
    says the word `egress:`, the fail-safe direction is containment.

    `sink_ip` is resolved from the twin's own `role: sink` node when omitted
    (`axrange.planner.egress.resolve_egress_policy`) — the address is a fact
    the twin already states, and typing it twice is how the two drift.
    """

    phase: Optional[str] = None      # provision | measure; omitted => measure
    sink: Optional[str] = None       # node id to take the sink address from
                                     # (default: the unique `role: sink` node)
    sink_ip: Optional[str] = None    # explicit override; wins over resolution
    mode: Optional[str] = None       # default | cidrs (egress_render.EGRESS_MODES)
    cidrs: Optional[list[str]] = None  # mode: cidrs only — observed prefixes


class Scenario(IRBase):
    id: Optional[str] = None
    version: Optional[int] = None
    description: Optional[str] = None
    seed: Optional[Any] = None  # int per spec; STR-150 enforces type
    reproducible: bool = False
    labels: Optional[dict[str, Any]] = None
    includes: Optional[list[str]] = None
    default_agents: Optional[bool] = None  # A3 Stage A (docs/design/
                                   # 08-exercise-measurement.md): the baked-in
                                   # default-agent set (defense=wazuh-agent,
                                   # exporter=node-exporter) on every TE host node.
                                   # ON BY DEFAULT (user decision, 2026-07-11) -- unset
                                   # (None) or explicit `true` both mean ON, no
                                   # per-host installed_on: listing needed; explicit
                                   # `false` is the scenario-level escape hatch that
                                   # disables the whole sweep. Per-node opt-out:
                                   # node.default_agents_off (works either way).
                                   # adversary is never part of this blanket sweep
                                   # (see AdversaryEntry) -- it stays overlay-declared.


class IR(IRBase):
    """The top-level parsed IR, post-include-splice.

    `source_files` is set by the parser to the list of files that contributed
    content (main + any included fragments), used by INC-010 to attribute
    duplicate IDs to a specific file.
    """

    scenario_schema_version: Optional[int] = None
    scenario: Optional[Scenario] = None
    layers: Optional[Layers] = None
    bindings: Optional[list[Binding]] = None
    sensors: Optional[list[Sensor]] = None
    overlays: Optional[Overlays] = None
    fabric: Optional[Fabric] = None  # fabric slice: dynamic routing + overlay (§3.1)
    egress: Optional[Egress] = None  # GAP-101: the twin's containment posture, attached
                                     # to every router's configure-router (see `Egress`).
                                     # ABSENT == today's permissive masquerade, unchanged.

    # Parser-populated, not part of the spec:
    source_files: list[str] = Field(default_factory=list, exclude=True)
    raw: dict[str, Any] = Field(default_factory=dict, exclude=True, repr=False)
