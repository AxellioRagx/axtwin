"""API DTOs — purpose-shaped, UI-agnostic JSON (`docs/design/11-api-ui.md`).

These are the API's contract. They are NEVER the raw IR, Proxmox objects, or
`OpInstance`/`OpRow` shapes — consumers (UI now, AI core / CI later) bind to
these, not to axRange's internal schemas (which `docs/PLANNER.md` §7 warns will
refine). The mapping from engine objects → DTO lives in `service.py`.
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_serializer


# --------------------------------------------------------------------------
# Health / scenarios
# --------------------------------------------------------------------------
class HealthDTO(BaseModel):
    status: str
    version: str
    # Whether the configured state DB is reachable (read-only probe).
    state_db: bool


class ScenarioSummaryDTO(BaseModel):
    id: str
    path: str


class ScenarioListDTO(BaseModel):
    scenarios: list[ScenarioSummaryDTO]


# --------------------------------------------------------------------------
# Topology DTO (bipartite host <-> network graph) — §4
# --------------------------------------------------------------------------
class TopologyNodeDTO(BaseModel):
    """One IR node OR one network, as a graph node."""

    id: str
    # "host" | "router" | "switch" | "network"
    type: str
    # node-only enrichment (None for network nodes)
    image: Optional[str] = None
    requires: Optional[list[str]] = None
    # network-only enrichment
    cidr: Optional[str] = None
    vlan: Optional[int] = None
    # live op status overlay (joined from state.db); None when no deploy / pre-deploy.
    # one of: completed | running | failed | halted-due-to-upstream | pending
    status: Optional[str] = None


class TopologyEdgeDTO(BaseModel):
    """A node's attachment to a network."""

    source: str  # node id
    target: str  # network id
    ip: Optional[str] = None


class TopologyDTO(BaseModel):
    scenario_id: str
    # deploy whose status annotates the nodes (None if not annotated)
    deploy_id: Optional[str] = None
    nodes: list[TopologyNodeDTO]
    edges: list[TopologyEdgeDTO]


# --------------------------------------------------------------------------
# Node-detail DTO (hover card) — §4.1
# --------------------------------------------------------------------------
class NodeAppliedDTO(BaseModel):
    """A declared app/script/activity/overlay + whether axRange's op applied it."""

    kind: str  # app | script | activity | overlay-data | overlay-defense | overlay-identity
    id: str
    # op status from state.db (None = no matching op / not deployed)
    applied: Optional[str] = None


class NodeDetailDTO(BaseModel):
    id: str
    kind: str
    image: Optional[str] = None
    requires: Optional[list[str]] = None
    networks: list[TopologyEdgeDTO]  # this node's net attachments (ip/mac later)
    resources: Optional[dict[str, Any]] = None
    placement: Optional[str] = None  # PVE node, from state.db
    vmid: Optional[str] = None
    declared: list[NodeAppliedDTO]  # declared + applied? merge
    # Tier-3 live fields are filled only when ?live=true (DHCP IP, services...).
    live: Optional[dict[str, Any]] = None


# --------------------------------------------------------------------------
# Findings DTO (validate) — §4
# --------------------------------------------------------------------------
class FindingDTO(BaseModel):
    rule_id: str
    severity: str  # error | warning | info
    message: str
    location: Optional[str] = None


class ValidateResultDTO(BaseModel):
    disposition: str  # pass | pass_with_warnings | fail
    exit_code: int
    passes_run: list[int]
    errors: int
    warnings: int
    findings: list[FindingDTO]


# --------------------------------------------------------------------------
# Plan DTO
# --------------------------------------------------------------------------
class PlanSummaryDTO(BaseModel):
    plan_id: str
    scenario_id: str
    scenario_version: Optional[int] = None
    ir_hash: str
    ops: int
    barriers: int
    edges: int
    # the op-tree rendering (human-readable), handy for the iterate loop
    tree: str
    # Twin-config expansion-time advisory findings, mirroring
    # `CreateDeployResponse.config_warnings` (same producer: the deploy gate,
    # `docs/WORK_ORDER_deploy_gate.md` §4.5). Additive with a default, so
    # every existing construction and consumer is unchanged; populated on the
    # config/imprint-sourced plan paths, where a PREVIEW that silently shows a
    # gated-down op count would be exactly the wrong thing to be quiet about
    # (LES-089). Always `[]` on the plain `scenario` path.
    config_warnings: list[str] = []
    # Fix round 2: VMs the plan creates (`clone-vm` ops) and the RESOLVED
    # twin parameters it was built with (declared defaults + overrides +
    # inline `${NAME:default}` defaults). `{}` for a plain scenario.
    vm_count: int = 0
    params: dict[str, Any] = {}


# --------------------------------------------------------------------------
# Deploy DTOs
# --------------------------------------------------------------------------
class DeploySummaryDTO(BaseModel):
    deploy_id: str
    scenario_id: str
    scenario_version: Optional[int] = None
    status: str  # running | completed | failed | cancelled
    started_at: float
    updated_at: float
    completed_at: Optional[float] = None
    # Unified Console ruling §3.2 -- additive, defaulted. `tier` is the
    # DISPLAY tier (te | tap | tfc | exr | o2): a tap playout is stored as
    # `te` and shown as `tap`. `target` is the cluster target the row was
    # read from (`axrange.api.targets`).
    tier: Optional[str] = None
    plan_id: Optional[str] = None
    target: Optional[str] = None
    # Fix round 2: which of the target's state stores records this deploy
    # (a short label; the path is on `GET /api/targets`).
    store: Optional[str] = None


class DeployListDTO(BaseModel):
    deploys: list[DeploySummaryDTO]
    # Fix round 3 (S1): a store of the target that could not be read is
    # skipped and named here, never a 500 for the whole list.
    warnings: list[str] = []


class OpAttemptDTO(BaseModel):
    attempt_id: int
    started_at: float
    ended_at: Optional[float] = None
    result: Optional[str] = None  # success | error | timeout
    error_class: Optional[str] = None
    error_message: Optional[str] = None


class OpDTO(BaseModel):
    op_id: str
    op_name: str
    op_catalog_id: str
    kind: str
    status: str  # pending | running | completed | failed | halted-due-to-upstream
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    attempts: list[OpAttemptDTO]


class DeployDetailDTO(BaseModel):
    deploy: DeploySummaryDTO
    plan_id: str
    placement: dict[str, str]
    ops: list[OpDTO]
    # rollup counts for a quick gauge
    counts: dict[str, int]
    # tfc deploys only (fix round 1, M2/M4): every per-host persona apply's
    # persisted job record, newest first -- status, the EXECUTED plan with
    # its fingerprint, and diagnostics. `[]` for every other deploy.
    host_edits: list[dict[str, Any]] = []


# --------------------------------------------------------------------------
# Twin inventory DTOs (`docs/design/02-twin-model.md`,
# `docs/WORK_ORDER_tier_split.md` review SHOULD-FIX #10) -- `GET /api/deploys/
# {deploy_id}/inventory`, an API peer of `axrange te inventory` (`docs/design/
# 11-api-ui.md`: the API is a `cli/`-class consumer, never a second
# implementation). Clean DTOs only -- never `axrange.planner.inventory`'s
# dataclasses directly (the `api/` boundary rule).
# --------------------------------------------------------------------------
class HostNetDTO(BaseModel):
    net_id: str
    ip: Optional[str] = None
    nic_index: Optional[int] = None
    # `docs/WORK_ORDER_tap_scopes.md` M2 -- True for a router's synthetic WAN
    # uplink (net_id == "__wan__"); False for an ordinary twin segment. A
    # consumer that means "twin segments only" filters on this.
    external: bool = False


class HostListenDTO(BaseModel):
    """One DECLARED listen on a host (`docs/
    WORK_ORDER_protocol_faithful_services.md` S1) -- field-for-field with
    `planner.inventory.HostListen`, so `te inventory --json` and this
    endpoint publish the same shape (the divergence `HostInventoryDTO`'s own
    `vm_name` note records)."""

    port: int
    proto: str = "tcp"


class HostInventoryDTO(BaseModel):
    host_id: str
    vm_name: Optional[str] = None
    vmid: Optional[int] = None
    pve_node: Optional[str] = None
    vm_status: Optional[str] = None
    # EFFECTIVE role (a tfc-assigned role wins over the twin's own baseline,
    # `docs/WORK_ORDER_role_persona_tier.md` §4.2); `role_baseline` is the
    # twin's own, so a consumer can tell an overridden host from a plain one.
    role: Optional[str] = None
    role_baseline: Optional[str] = None
    # IR node kind (host/router/...) -- consumed by `tappoints._is_router_kind`
    # to find router ports for `perimeter` scope (not to exclude anything
    # from `segment` any more, `docs/WORK_ORDER_tap_scopes.md` M1).
    kind: Optional[str] = None
    nets: list[HostNetDTO] = []
    os: Optional[str] = None
    # The DECLARED listen set, or `None` when this host declares none (never
    # `[]` -- "declares nothing" and "declares an empty set" are different
    # claims; `planner.inventory.HostInfo.listen`).
    listen: Optional[list[HostListenDTO]] = None


# `TapPointInventoryDTO` REMOVED (`docs/WORK_ORDER_tap_eligibility.md` D5,
# Authority: user, 2026-07-28) -- the twin inventory publishes no
# candidate-capture-point catalog of its own any more.


class EgressPostureDTO(BaseModel):
    """A twin's CONTAINMENT POSTURE (GAP-101) -- field-for-field with
    `planner.egress.EgressPosture.to_dict()`, so `te info --json`,
    `te inventory --json` and both endpoints publish one shape.

    `phase_applied` is the router's own report (`configure-router`'s
    `egress_phase` output); `phase_declared` is what the twin asked for.
    `contained` is derived from the APPLIED phase only -- a declaration is
    not a containment.
    """

    router_host: Optional[str] = None
    phase_declared: Optional[str] = None
    phase_applied: Optional[str] = None
    sink_ip: Optional[str] = None
    mode: Optional[str] = None
    contained: bool = False
    # UTC timestamp of the live read (`te egress` / the containment gate)
    # `phase_applied` came from; None = configure-router's own report.
    live_read_at: Optional[str] = None


class TwinInventoryDTO(BaseModel):
    deploy_id: str
    deploy_status: Optional[str] = None
    hosts: list[HostInventoryDTO]
    # Full `net_id -> bridge` map + the VLAN underlay, if any (review
    # MUST-FIX, second round) -- kept in lockstep with `TwinInventory.
    # to_dict()` (the CLI's `--json` shape) so `te inventory --json` and
    # this endpoint never diverge on the SAME data again (the exact
    # `node`/`vm_name` mismatch review SHOULD-FIX #2 called out).
    net_bridge: dict[str, str] = {}
    vlan_underlay: Optional[str] = None
    # The twin's containment posture (GAP-101) -- `None` when the twin
    # declares none, never fabricated. Kept in lockstep with
    # `TwinInventory.to_dict()`'s `egress` key.
    egress: Optional[EgressPostureDTO] = None


# --------------------------------------------------------------------------
# Fidelity scorecard (`docs/WORK_ORDER_fidelity_scorecard.md` §4.2) -- an API
# peer of `axrange te scorecard`, NOT a second implementation: both call
# `axrange.fidelity.runner.scorecard_for_deploy` and render its ONE
# `Scorecard.to_dict()` (LES-037). Field-for-field with that dict, so the CLI's
# `--json` and this endpoint can never diverge on the same deploy.
# --------------------------------------------------------------------------
class ScorecardClauseDTO(BaseModel):
    clause: str
    verdict: str
    detail: Optional[str] = None
    # MANDATORY on a `not-computed` clause -- enforced upstream by
    # `fidelity.schema.Clause`, which refuses to construct one without it.
    reason: Optional[str] = None
    evidence_class: Optional[str] = None
    scored: bool = True


class ScorecardRowDTO(BaseModel):
    row: int
    dimension: str
    category: str
    channel: str
    # `gates` | `deferred` | `reported` -- derived from `gate_set`, never
    # authored per row, so the two can never contradict each other.
    status: str
    # `pass` | `fail` | `not-computed` | `attested` | `not-attested`.
    # `not-computed` is a FIRST-CLASS verdict distinct from `fail` (§4.1).
    verdict: str
    # True for rows 6/8/9/10 whatever the verdict, until spec v1.1 fixes
    # their tolerances (spec §8.3). A provisional row never contributes to
    # the document verdict on spec v1.
    provisional: bool = False
    reason: Optional[str] = None
    granularity: Optional[str] = None
    # Counts per evidence class (the six of `docs/design/14-operator-
    # assertions.md` §2, plus `unclassified` for a fact the reference
    # declared no class for -- never promoted to `observed`).
    evidence: dict[str, int] = {}
    measured: dict[str, Any] = {}
    clauses: list[ScorecardClauseDTO] = []


class ScorecardDTO(BaseModel):
    # The SPEC version certified against, not this DTO's shape version.
    scorecard_version: str
    generated_at: str
    # `golden` | `not-golden` | `incomplete`. An uncomputed GATING row makes
    # this `incomplete`, never `not-golden` (§4.1).
    verdict: str
    subject: dict[str, Any]
    reference: dict[str, Any]
    gate_set: dict[str, Any]
    rows: list[ScorecardRowDTO]
    warnings: list[str] = []


class CreateDeployRequest(BaseModel):
    # Exactly one of `scenario` / `config` / `imprint` (`docs/
    # WORK_ORDER_twin_configs.md` §2's `te deploy --config` CLI parity, plus
    # `docs/WORK_ORDER_aximprint_console.md`'s "deploy as twin" action --
    # `service.create_deploy` 400s on more than one / none set, mirroring
    # `cli.main._resolve_scenario_or_config`).
    scenario: Optional[str] = None  # scenario id (resolved via scenarios_dir) OR an absolute path
    config: Optional[str] = None    # twin-config id (resolved via the twin-configs catalog)
    imprint: Optional[str] = None   # imprint id (resolved via the imprints fixture catalog)
    params: Optional[dict[str, str]] = None  # --param overrides; only valid with `config`/`imprint`
    target_node: Optional[str] = None
    fresh: bool = False
    # Incremental update (`docs/WORK_ORDER_twin_incremental_update.md`) --
    # `update`/`fresh` are mutually exclusive (`create_deploy` 400s on both).
    # `allow_destroy` gates a non-empty removed set, mirroring
    # `DestroyRequest.allow_destroy`'s naming/semantics one field over.
    update: bool = False
    allow_destroy: bool = False
    # Content-only re-apply (`docs/WORK_ORDER_responder_op_row_drift.md`
    # option A) -- opt-in, `update`-only (`resolve_deploy_plan` 400s without
    # it, same precedent as `allow_destroy`): re-executes a mutated
    # `run-script` whose ONLY differing input is `script_content`, instead of
    # refusing it. Every other mutation still refuses.
    reapply: bool = False
    # GAP-098 (fix round 1): the `plan_id` the caller previewed (`/api/plan`,
    # `/api/imprints/{id}/plan`). When set, the server's re-plan must produce
    # the SAME plan_id or the deploy is refused 409 `plan-changed` -- the
    # plan an operator confirmed is the plan that runs.
    expect_plan_id: Optional[str] = None


class CreateDeployResponse(BaseModel):
    deploy_id: str
    status: str
    detached: bool
    # The plan_id actually deployed (GAP-098). None only on the idempotent
    # "already completed" short-circuit of an older caller path.
    plan_id: Optional[str] = None
    # Twin-config expansion-time advisory findings (e.g. unknown persona
    # ids) -- CLI/API parity follow-on (code review): `te deploy --config`
    # always collects + prints these (`cli.main._resolve_scenario_or_config`'s
    # `config_warnings`); this field is the API's equivalent so a caller
    # never silently loses them. Always `[]` on the `scenario` path.
    config_warnings: list[str] = []


class DestroyRequest(BaseModel):
    allow_destroy: bool = False


class DestroyActionDTO(BaseModel):
    kind: str          # vm | mirror | gretap | vnet | zone | sdn-apply | ovs-bridge
    target: str
    status: str        # removed | absent | guard-violation | error
    detail: Optional[str] = None


class DestroyResponse(BaseModel):
    deploy_id: str
    # True iff a clean sweep transitioned the deploy to `destroyed`.
    destroyed: bool
    removed: int
    errors: int
    actions: list[DestroyActionDTO]
    # UNEXPECTED failures from a bound sibling-tier cascade (tap/tfc/exr) --
    # each entry is `"{sibling}: {exception}"`. Empty when every cascade ran
    # clean (or found nothing bound). A non-empty list here means the TE's
    # OWN destroy still succeeded (`destroyed: true` is about this deploy
    # only), but a sibling artifact may still be standing and needs a
    # follow-up destroy (`docs/WORK_ORDER_exercise_tier.md` S3, code review --
    # "no silent failures": a programmatic caller must see this, not just an
    # operator watching CLI stdout).
    cascade_errors: list[str] = []


class RunResponse(BaseModel):
    deploy_id: str
    status: str
    message: str


# --------------------------------------------------------------------------
# Targets DTO (multi-PVE picker) — §3
# --------------------------------------------------------------------------
class TargetDTO(BaseModel):
    """A cluster target, redacted — no secrets. `node`..`has_ssh_key` keep
    their pre-2026-09-29 meaning (the Grafana plugin reads them); the rest
    is the Unified Console's multi-target view (`axrange.api.targets`)."""

    node: str
    api_url: str
    verify_tls: bool
    # token/key presence only, never the value
    has_token: bool
    has_ssh_key: bool
    name: Optional[str] = None
    default: bool = False
    nodes: list[str] = []
    db_path: Optional[str] = None
    # Set (and the facts above blank) when this target's adapter.conf fails
    # to load.
    error: Optional[str] = None
    # Fix round 2: every state store of this target ({label, path, primary}).
    stores: list[dict[str, Any]] = []


class TargetListDTO(BaseModel):
    targets: list[TargetDTO]


# --------------------------------------------------------------------------
# Pcaps DTO — list is a stub in Phase 1/2 (download deferred to Phase 2 API; the
# brief leaves the home open). We expose a list endpoint that is honest about
# being unimplemented rather than 404, so the UI can detect the capability.
# --------------------------------------------------------------------------
# --------------------------------------------------------------------------
# Discovery runs (`api/Spec.md` A1): `GET /api/discovery/plugins`,
# `POST /api/discovery/runs`, `GET /api/discovery/runs/{id}`.
# --------------------------------------------------------------------------
class DiscoveryPluginDTO(BaseModel):
    name: str
    # The plugin's own declared run-config schema (JSON-schema shaped; closed).
    # What `POST /api/discovery/runs` validates `config` against.
    config_schema: dict[str, Any]
    # How a `source` is addressed: `path` (a file/dir the server reads) or `http`
    # (a base URL). Only `http` plugins can back a stored discovery source (A7).
    source_kind: str = "path"


class DiscoveryPluginListDTO(BaseModel):
    plugins: list[DiscoveryPluginDTO]


class DiscoveryRunRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    # An installed plugin name (`GET /api/discovery/plugins`).
    plugin: str
    # Validated against that plugin's `config_schema`; `source` is required.
    config: dict[str, Any]
    # The imprint's NAME: what the bake labels the PIT (`ir_pit.name`), refused
    # if an imprint of that name exists. `imprint_id` is accepted as the old
    # spelling. A catalog id is chosen at cast time, not here.
    name: str = Field(validation_alias=AliasChoices("name", "imprint_id"))
    # Kept for one release: sensors are a cast-time choice now and ignored here.
    sensors: list[str] = []


class DiscoveryRunDTO(BaseModel):
    id: str
    status: str  # queued | running | completed | failed | cancelled
    plugin: str
    imprint_id: str
    # The four stages in order; the ones that have finished.
    stages_done: list[str]
    stages: list[str]
    # The imprint's name as asked for at POST; `imprint_id` carries the same
    # value for one release.
    name: str = ""
    # The PIT the bake produced: `pit_id` is its identity (a hash of source and
    # bounds, what the cast sidecar carries), `pit_name` its label, the name
    # until someone renames it (`PATCH /api/imprints/{id}`).
    pit_id: Optional[str] = None
    pit_name: Optional[str] = None
    error: Optional[str] = None
    created_at: str
    finished_at: Optional[str] = None
    # A7: present when the run came from a stored discovery source. `ts_from`/`ts_to`
    # are the bounds actually used (resolved from the source's span for a "last N
    # days" request); `via` says how the source was reached, never a credential.
    source_id: Optional[str] = None
    started_at: Optional[str] = None
    ts_from: Optional[float] = None
    ts_to: Optional[float] = None
    via: Optional[str] = None
    # Source time the ingest has reached (epoch seconds; the end of the last window it
    # closed) and when it last said so. Null until the first window closes; the stage
    # list says when ingest is over.
    progress_ts: Optional[float] = None
    progress_at: Optional[str] = None
    # Set once a cancel was asked for; the status turns `cancelled` when the stage is dead.
    cancel_requested_at: Optional[str] = None


class CastFromDTO(BaseModel):
    """A twin's lineage: the imprint it was cast from (`cast_invocation.pit_id`
    in its topology export), with the imprint's current name when the IR store
    still holds it."""
    pit_id: str
    pit_name: Optional[str] = None


class PitTwinRefDTO(BaseModel):
    id: str


class PitDTO(BaseModel):
    """One imprint: a PIT in the API's IR store (`aximprint pit list`), with
    its operator state and the twins cast from it."""
    pit_id: str
    name: str
    derived_from: Optional[str] = None
    has_assertions: bool = False
    twins: list[PitTwinRefDTO] = []
    as_of: Optional[float] = None
    observed_from: Optional[float] = None
    observed_to: Optional[float] = None
    source: Optional[str] = None
    network: Optional[str] = None
    vantage: Optional[str] = None
    windows: Optional[int] = None
    entities: Optional[int] = None
    segments: Optional[int] = None
    merge_axis: Optional[str] = None


class PitListDTO(BaseModel):
    pits: list[PitDTO]


class PitRenameRequest(BaseModel):
    name: str


class PitCopyRequest(BaseModel):
    name: str


class CastRequestDTO(BaseModel):
    """`POST /api/imprints/{pit}/cast`: compile the imprint, with its
    assertions, into `catalog/twins/<twin_id>/`."""
    twin_id: str
    # o2/external sensor name(s) to fill into every tap binding; empty leaves
    # `sensors:` absent, which `tap deploy` refuses.
    sensors: list[str] = []
    # Replace an existing twin of that id (refused if it is a live deploy's).
    replace: bool = False
    relocate: Optional[str] = None
    max_count: Optional[int] = None
    no_chatter: bool = False


class CastResponseDTO(BaseModel):
    twin: "ImprintSummaryDTO"
    pit_id: str
    replaced: bool
    caveats: list[str] = []
    install_report: str = ""


class DiscoveryRunListDTO(BaseModel):
    runs: list[DiscoveryRunDTO]


# Discovery sources (`api/Spec.md` A7): `/api/discovery/sources[...]`. A saved way to
# reach a sensor's query API. Reads NEVER carry a secret: `has_password` /
# `has_api_key` say whether one is stored. Writes take the secret as its own
# top-level field (`password`, `api_key`) so a client can omit it on update to
# keep the stored value, or send "" to clear it.
class SshAccessDTO(BaseModel):
    host: str
    user: str
    # A private-key PATH on the machine running `axrange serve`; never key material.
    key_path: str
    port: int = 22


class SourceAccessDTO(BaseModel):
    transport: str = "direct"  # direct | ssh_tunnel
    auth: str = "none"  # none | basic | api_key
    user: Optional[str] = None  # basic auth only
    ssh: Optional[SshAccessDTO] = None  # ssh_tunnel only


class DiscoverySourceWrite(BaseModel):
    name: str
    plugin: str
    # The plugin's base URL as seen from where the access path lands (through
    # the tunnel, that is the SSH host's view: e.g. http://127.0.0.1:9090).
    endpoint: str
    access: SourceAccessDTO = SourceAccessDTO()
    password: Optional[str] = None
    api_key: Optional[str] = None
    # Run options the console pre-fills: days, network, vantage, window,
    # subbuckets, sensors.
    defaults: dict[str, Any] = {}


class DiscoverySourceUpdate(BaseModel):
    """Every field optional; a field not sent keeps its stored value."""
    name: Optional[str] = None
    plugin: Optional[str] = None
    endpoint: Optional[str] = None
    access: Optional[SourceAccessDTO] = None
    password: Optional[str] = None
    api_key: Optional[str] = None
    defaults: Optional[dict[str, Any]] = None


class DiscoverySourceDTO(BaseModel):
    id: str
    name: str
    plugin: str
    endpoint: str
    access: SourceAccessDTO
    has_password: bool
    has_api_key: bool
    defaults: dict[str, Any]
    # The last `POST .../probe` answer, verbatim, with `probed_at`; None if never probed.
    last_probe: Optional[dict[str, Any]] = None
    created_at: str
    updated_at: str


class DiscoverySourceListDTO(BaseModel):
    sources: list[DiscoverySourceDTO]


class DiscoveryProbeDTO(BaseModel):
    source_id: str
    reachable: bool
    error: Optional[str] = None
    first_ts: Optional[float] = None
    last_ts: Optional[float] = None
    vantage: Optional[str] = None
    # The plugin's own census of the source (axquery: chunks, packets, sites, ...).
    extra: dict[str, Any] = {}
    via: Optional[str] = None
    probed_at: str


class DiscoverySourceRunRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    # The imprint's NAME (`ir_pit.name`); `imprint_id` is the old spelling.
    name: str = Field(validation_alias=AliasChoices("name", "imprint_id"))
    # EITHER `days` (the last N days of what the source holds, resolved from its
    # own span when the run starts) OR explicit epoch-second bounds.
    days: Optional[float] = None
    ts_from: Optional[float] = None
    ts_to: Optional[float] = None
    # Run options; unset -> the source's defaults.
    network: Optional[str] = None
    vantage: Optional[str] = None
    window: Optional[int] = None
    subbuckets: Optional[int] = None
    sensors: Optional[list[str]] = None


class PcapListDTO(BaseModel):
    pcaps: list[dict[str, Any]]
    implemented: bool
    note: str


# --------------------------------------------------------------------------
# O2 surface DTOs (A1, `docs/design/05-o2-surface.md`) — clean DTOs
# only, never `axrange.o2control`'s dataclasses or raw op/state-store shapes
# (the `api/` boundary rule). `POST/GET/DELETE /api/o2/surface`.
# --------------------------------------------------------------------------
class SurfaceUpRequest(BaseModel):
    # A surface manifest path the SERVER can read (operator-trusted
    # deployment, same posture as `--surface` on the CLI — no inline YAML
    # content over the wire today). Omitting it stands up the built-in
    # single-tcpdump-sensor default (`axrange.o2control.default_surface_
    # manifest`).
    surface_path: Optional[str] = None
    target_node: Optional[str] = None


class SensorInventoryDTO(BaseModel):
    sensor_id: str
    type: str
    transport_ip: Optional[str] = None
    deploy_id: str


class SurfaceStatusDTO(BaseModel):
    deploy_id: Optional[str] = None
    status: str  # not-deployed | running | completed | failed | destroyed | ...
    sensors: list[SensorInventoryDTO]


class SurfaceDownRequest(BaseModel):
    allow_destroy: bool = False
    deploy_id: Optional[str] = None


class SurfaceDownResponse(BaseModel):
    deploy_id: str
    destroyed: bool
    removed: int
    errors: int
    actions: list[DestroyActionDTO]


# --------------------------------------------------------------------------
# Tap-plan DTOs (A2, `docs/design/06-taps-sensors.md`) -- clean DTOs only, never
# `axrange.o2control.taps`'s dataclasses or raw op/state-store shapes (the
# `api/` boundary rule). `POST/DELETE /api/taps`.
# --------------------------------------------------------------------------
class TapBindingDTO(BaseModel):
    # Exactly one of `tap` / `tap_point` / `selector` addresses a binding's
    # target on a REQUEST (`docs/WORK_ORDER_tap_points.md` -- evolves the
    # original raw net/host `tap` id with the semantic tap-point vocabulary);
    # a RESPONSE (`TapPlanStatusDTO.bindings`) always populates `tap` alone,
    # with `axrange.o2control.taps.binding_label`'s string standing in for
    # whichever addressing mode the original binding used.
    tap: Optional[str] = None
    tap_point: Optional[str] = None
    selector: Optional[dict[str, Any]] = None
    sensors: list[str]
    mode: str = "fanout"
    # Resolved mirror direction -- usually "rx" or "both" (`docs/
    # WORK_ORDER_tap_scopes.md` M4/M6), occasionally the joined "both+rx"
    # for a scope-less `selector:` matching points of differing scopes
    # (`TapBindingResult.direction`'s own docstring). `None` on a REQUEST
    # (direction is derived, never authored); populated on a RESPONSE. A
    # real field, not just baked into `tap`'s rendered text.
    direction: Optional[str] = None


class TapApplyRequest(BaseModel):
    # OPTIONAL (`docs/WORK_ORDER_tap_target_resolution.md`) -- either an
    # already-minted deploy id OR a scenario/twin-config NAME (resolved to
    # its newest live `te` deploy, `axrange.o2control.taps.
    # resolve_target_deploy`). `apply_tap_plan` itself 400s if this is
    # absent (there is no separate override field on this endpoint -- the
    # request body's own `target` IS the effective target, same posture as
    # the CLI's "plan target, else --target, else error").
    target: Optional[str] = None
    bindings: list[TapBindingDTO]
    target_node: Optional[str] = None


class TapPlanStatusDTO(BaseModel):
    deploy_id: Optional[str] = None
    target: str
    status: str  # not-applied | running | completed | failed | destroyed | ...
    bindings: list[TapBindingDTO]
    # Non-fatal apply-time findings (e.g. "capture UNVERIFIED for external
    # sensor ..." -- LES-001 forbids a silent "green deploy, no capture"
    # outcome, code review follow-on to the config-external-sensor feature).
    warnings: list[str] = []


# `GET /api/taps?verbose=true` -- the rich, per-binding "config + access
# facts" view (an API peer of `axrange tap info`, vs. the concise
# `TapPlanStatusDTO` `axrange tap status` returns). Maps
# `axrange.o2control.taps.{TapInfo,TapBindingInfo}` -> DTOs.
class TapBindingInfoDTO(BaseModel):
    host: str
    net_id: str
    nic_index: int
    sensors: list[str]
    # sensor_id -> resolved gretap device name / standing transport IP /
    # per-sensor capture-verified flag (False = config-external sink, no
    # heartbeat -- confirm out-of-band).
    gre_ports: dict[str, Optional[str]] = {}
    sensor_transport_ips: dict[str, Optional[str]] = {}
    capture_verified: dict[str, bool] = {}
    # sensor_id -> mirror direction ("rx"/"both", `docs/
    # WORK_ORDER_tap_scopes.md` M4/M6).
    directions: dict[str, str] = {}


class TapInfoDTO(BaseModel):
    target: str
    target_status: str
    deploy_id: Optional[str] = None
    status: str  # not-applied | running | completed | failed | destroyed | ...
    bindings: list[TapBindingInfoDTO] = []
    warnings: list[str] = []
    fanout_note: str = ""
    # Config-external sensors resolved to live mgmt facts (masked unless
    # `?secrets=true` + an authorized token) -- same block `GET /api/o2/info`
    # carries, so the consumer can join a binding's external sensor id to its
    # access facts. forward ref -- `ExternalSensorAccessDTO` is defined below.
    external_sensors: list["ExternalSensorAccessDTO"] = []
    secrets_revealed: bool = False


class TapDestroyRequest(BaseModel):
    allow_destroy: bool = False
    target: Optional[str] = None
    deploy_id: Optional[str] = None


class TapDestroyResponse(BaseModel):
    deploy_id: str
    destroyed: bool
    removed: int
    errors: int
    actions: list[DestroyActionDTO]


# --------------------------------------------------------------------------
# Traffic-profile DTOs (the Traffic tier, `docs/WORK_ORDER_traffic_tier.md`)
# -- clean DTOs only, never `axrange.o2control.traffic`'s dataclasses or raw
# op/state-store shapes (the `api/` boundary rule). `POST/GET/DELETE /api/tfc`.
# --------------------------------------------------------------------------
class TrafficRoleAssignmentDTO(BaseModel):
    """One `roles[]` entry — the tfc-owned host→role map
    (`docs/WORK_ORDER_role_persona_tier.md` §4.4). Exactly one of
    `node`/`selector` addresses the host(s); addressing by `role` is
    deliberately NOT offered (it is the chicken-and-egg case: the role map is
    what defines roles in the first place)."""

    node: Optional[str] = None
    selector: Optional[dict[str, Any]] = None
    role: Optional[str] = None
    # RESPONSE-only: the host(s) this entry resolved onto.
    hosts: Optional[list[str]] = None


class TrafficAssignmentDTO(BaseModel):
    # Exactly one of `role` / `node` addresses a binding's target on a
    # REQUEST; a RESPONSE (`TrafficPlanStatusDTO.assignments`) instead
    # reports the resolved persona + the host(s) it landed on.
    role: Optional[str] = None
    node: Optional[str] = None
    persona: Optional[str] = None
    intensity: Optional[float] = None
    schedule: Optional[str] = None
    seed: Optional[Any] = None
    hosts: Optional[list[str]] = None


class TrafficReplayBindingDTO(BaseModel):
    template: str
    onto_role: str
    orig_ip: Optional[str] = None
    iface: str = "eth0"


class TrafficApplyRequest(BaseModel):
    # (Unified Console ruling §3.3) -- required to apply a profile that would
    # revert a per-host persona edit; without it such an apply is a 409.
    allow_revert_host_edits: bool = False

    # OPTIONAL and named `target` since 2026-08-04
    # (`docs/WORK_ORDER_role_persona_tier.md` §4.3) -- exact parity with
    # `TapApplyRequest.target`: either an already-minted `te` deploy id OR a
    # scenario/twin-config NAME (resolved to its newest live `te` deploy).
    # `twin` is the pre-2026-08-04 field name, still accepted so an existing
    # client keeps working; declaring both is a 400.
    target: Optional[str] = None
    twin: Optional[str] = None
    roles: list[TrafficRoleAssignmentDTO] = []
    assignments: list[TrafficAssignmentDTO] = []
    replay: list[TrafficReplayBindingDTO] = []
    target_node: Optional[str] = None


class TrafficPlanStatusDTO(BaseModel):
    deploy_id: Optional[str] = None
    twin: str
    status: str  # not-applied | running | completed | failed | destroyed | ...
    assignments: list[TrafficAssignmentDTO] = []
    warnings: list[str] = []


class TrafficDestroyRequest(BaseModel):
    allow_destroy: bool = False
    target: Optional[str] = None
    deploy_id: Optional[str] = None


class TrafficDestroyResponse(BaseModel):
    deploy_id: str
    destroyed: bool
    removed: int
    errors: int
    actions: list[DestroyActionDTO]


# --------------------------------------------------------------------------
# Exercise DTOs (the Exercise tier, `docs/WORK_ORDER_exercise_tier.md`) --
# clean DTOs only, never `axrange.o2control.exercise`'s dataclasses or the
# underlying IR pydantic shapes (`axrange.ir.schema.Activity`/
# `AdversaryEntry`) it reuses internally (the `api/` boundary rule).
# `POST/GET/DELETE /api/exr`.
# --------------------------------------------------------------------------
class ExerciseActivityDTO(BaseModel):
    id: str
    host: str
    inline_steps: list[dict[str, Any]] = []
    schedule: Optional[dict[str, Any]] = None
    seed: Optional[Any] = None


class ExerciseAdversaryDTO(BaseModel):
    id: str
    targets: list[str] = []
    server: str
    group: Optional[str] = None


class ExerciseApplyRequest(BaseModel):
    # `twin` is OPTIONAL since 2026-08-04 (name-targeting parity with tfc/tap,
    # `docs/WORK_ORDER_corp_enterprise_bundle.md` §4): a deploy id OR a
    # scenario/twin-config name. `target` overrides it (precedence
    # target > twin > error). `target_node` is the unrelated PVE cluster node.
    twin: Optional[str] = None
    target: Optional[str] = None
    activity: Optional[ExerciseActivityDTO] = None
    adversary: Optional[ExerciseAdversaryDTO] = None
    defense: list[dict[str, Any]] = []
    measurement: dict[str, Any] = {}
    target_node: Optional[str] = None


class ExerciseStatusDTO(BaseModel):
    deploy_id: Optional[str] = None
    twin: str
    status: str  # not-applied | running | completed | failed | destroyed | ...
    playout_hash: Optional[str] = None
    activity_host: Optional[str] = None
    adversary_targets: list[str] = []
    defense_notes: list[dict[str, Any]] = []
    measurement: dict[str, Any] = {}
    warnings: list[str] = []


class ExerciseDestroyRequest(BaseModel):
    allow_destroy: bool = False
    deploy_id: Optional[str] = None
    target: Optional[str] = None  # alternative: latest exr run against this twin


class ExerciseDestroyResponse(BaseModel):
    deploy_id: str
    destroyed: bool
    removed: int
    errors: int
    actions: list[DestroyActionDTO]


# --------------------------------------------------------------------------
# Access-facts DTOs (`docs/WORK_ORDER_access_facts.md` Part D/B/C) --
# `GET /api/deploys/{deploy_id}/access` is an API peer of `axrange te info`
# (`cli/main.py::cmd_te_info` + `axrange.planner.access`), NOT a second
# implementation: both call `axrange.planner.access.te_info_from_ops` /
# `fill_access_facts` and map the SAME `TeInfo`/`TeHostInfo` onto DTOs
# (mirrors `TwinInventoryDTO`'s "one derivation, two thin renderers" pattern
# above). `GET /api/o2/info` is the peer of `axrange o2 info`'s endpoint-
# resolution + external-sensor block.
# --------------------------------------------------------------------------
class HostAccessDTO(BaseModel):
    host_id: str
    kind: str  # "host" | "router"
    vmid: Optional[int] = None
    os_family: str = "linux"
    ips: dict[str, str] = {}  # net_id -> ip
    wan_ip: Optional[str] = None
    # `taps:`/`AccessTapDTO` REMOVED here (docs/WORK_ORDER_tap_eligibility.md,
    # Authority: user, 2026-07-28) -- a te records no tap-related state any
    # more; `GET /api/taps` (`TapInfo`/`TapBindingInfo` DTOs above) is the
    # API's capture-facts surface now, not this access-facts endpoint.
    # Login facts (`fill_access_facts`'s derived fields). `password` is
    # `None` for a router with no cloud-init-sourced login at all (VyOS et
    # al.) -- there IS no default-password account to show -- and is
    # MASKED ("********") unless the caller passed `?secrets=true` AND
    # (when a write token is configured) supplied it, mirroring the CLI's
    # `--no-secrets` default-on masking.
    ip: str  # the resolved "access_ip" (always set -- "(unresolved)" at worst)
    user: Optional[str] = None
    password: Optional[str] = None
    hint: Optional[str] = None


class SubnetAccessDTO(BaseModel):
    net_id: str
    cidr: Optional[str] = None
    gateway: Optional[str] = None
    snat: bool = False


class AccessFactsDTO(BaseModel):
    deploy_id: Optional[str] = None
    scenario_id: str
    status: Optional[str] = None
    ops_total: int = 0
    ops_done: int = 0
    hosts: list[HostAccessDTO] = []
    subnets: list[SubnetAccessDTO] = []
    # The twin's containment posture (GAP-101) -- the API peer of the
    # `egress:` line `te info` prints. `None` when the twin declares none.
    egress: Optional[EgressPostureDTO] = None
    # True iff this response carries REAL passwords (`?secrets=true`); False
    # means every `password` field above is masked.
    secrets_revealed: bool = False


# --------------------------------------------------------------------------
# O2 endpoint-resolution DTO (`docs/WORK_ORDER_access_facts.md` Part B/C) --
# an API peer of `axrange o2 info`'s endpoint-resolution + external-sensor
# block (`cli/main.py::cmd_o2_info`). Kept separate from `SurfaceStatusDTO`
# (review guidance: "a dedicated endpoint ... to keep surface-status
# stable") since it carries best-effort-resolved, potentially-secret data
# the surface-status shape never has.
# --------------------------------------------------------------------------
class ExternalSensorAccessDTO(BaseModel):
    id: str
    type: str
    vmid: int
    transport_ip: str
    capture_iface: str
    resolved_name: Optional[str] = None
    resolved_ip: Optional[str] = None
    access_user: Optional[str] = None
    access_password: Optional[str] = None
    access_hint: Optional[str] = None


class O2InfoDTO(BaseModel):
    # Resolved observability endpoint URLs (`AdapterConfig.resolve_o2_
    # endpoints`) -- grafana/loki/prometheus/caldera/wazuh, or a
    # "(not configured — set O2_SERVICES_VMID or a per-service O2_<SVC>_URL in
    # adapter.conf)"/"(services vmid=N — IP unresolved)" placeholder.
    endpoints: dict[str, str] = {}
    # Declared `EXTERNAL_SENSORS` entries + best-effort live name/IP
    # resolution (Part C) -- same shape `o2 info`/`tap info` both render.
    external_sensors: list[ExternalSensorAccessDTO] = []
    secrets_revealed: bool = False


# --------------------------------------------------------------------------
# Twin-config DTOs (`docs/WORK_ORDER_twin_configs.md`) -- clean DTOs only,
# never `axrange.twinconfig.schema.TwinConfig` directly (the `api/` boundary
# rule) even though that pydantic model is itself already a clean, non-IR
# vocabulary -- API peers of `axrange twin-config list|show`
# (`cli/main.py::cmd_twin_config_list`/`cmd_twin_config_show`).
# `GET /api/twin-configs` / `GET /api/twin-configs/{config_id}`.
# --------------------------------------------------------------------------
class TwinConfigSummaryDTO(BaseModel):
    id: str
    description: Optional[str] = None
    # `twin.yaml cluster:` -- the target this twin is meant for (ruling
    # §3.1). None when unset or when the entry has no twin.yaml.
    cluster: Optional[str] = None
    # Fix round 2: whether the twin takes deploy-time parameters, and which.
    parameterized: bool = False
    param_names: list[str] = []
    # Set when the entry's `twins/<id>/twin.yaml` fails to load -- listed and
    # flagged, never shown healthy (`registry.TwinConfigCatalogEntry.error`).
    # Defaulted, so an existing consumer is untouched.
    error: Optional[str] = None


class TwinConfigListDTO(BaseModel):
    twin_configs: list[TwinConfigSummaryDTO]


class TwinConfigParamDTO(BaseModel):
    name: str
    default: Optional[Any] = None
    # Fix round 2: the comment written above/beside the param in te.yaml, and
    # whether it is declared in `params:` (vs an inline `${NAME:default}` only).
    description: Optional[str] = None
    declared: bool = True


class TwinConfigSegmentSummaryDTO(BaseModel):
    name: str
    cidr: str
    zone_type: Optional[str] = None
    when: Optional[str] = None


class TwinConfigNodeSummaryDTO(BaseModel):
    # `name` (the node-id stem, required) and `role` (the OPTIONAL semantic
    # baseline) were ONE required `role:` field until the role/persona split
    # (2026-08-04, `docs/WORK_ORDER_role_persona_tier.md` §4.5). `persona` is
    # GONE from this DTO with the inert twin-config field behind it: a twin
    # config declares no behavior, so there is nothing here to report. A
    # consumer that wants the persona bound to a host reads the `tfc` tier
    # (`GET /api/tfc`), not the twin config.
    name: str
    kind: str = "host"
    image: str
    count: Any = 1
    role: Optional[str] = None
    when: Optional[str] = None


# `TwinConfigTapPointSummaryDTO` REMOVED (`docs/WORK_ORDER_tap_eligibility.md`
# D3, Authority: user, 2026-07-28) -- the twin-config `tap_points:` block is
# retired; there is nothing left for this DTO to summarize.


class TwinConfigDetailDTO(BaseModel):
    id: str
    description: Optional[str] = None
    cluster: Optional[str] = None
    params: list[TwinConfigParamDTO] = []
    segments: list[TwinConfigSegmentSummaryDTO] = []
    nodes: list[TwinConfigNodeSummaryDTO] = []
    identity_domain: Optional[str] = None
    identity_user_count: int = 0
    data_count: int = 0


# --------------------------------------------------------------------------
# Imprint DTOs (aximprint-integration Track C,
# `docs/WORK_ORDER_aximprint_console.md`) -- clean DTOs only, never
# `axrange.api.imprints`'s dataclasses directly (the `api/` boundary rule).
# A read-only belief-store/provenance surface over a checked-in FIXTURE
# imprint (`axrange/api/fixtures/imprints/<id>/`) -- axRange reads only the
# two artifact files an imprint ships (`<id>.yaml` twin_config +
# `<id>.provenance.json` sidecar), never aximprint itself.
# `GET /api/imprints` / `GET /api/imprints/{id}` / `GET /api/imprints/{id}/
# provenance`. The "deploy as twin" action (Track C slice 2, below) reuses
# an imprint's `<id>.yaml` AS a twin_config -- it is byte-for-byte the same
# schema `axrange.twinconfig.schema.TwinConfigFile` already validates, and
# an imprint fixture's `<fixtures_dir>/<id>/<id>.yaml` layout is identical
# to the twin-configs catalog's own subfolder shape (`axrange.twinconfig.
# registry`) -- so "deploy this imprint" is "expand+validate+plan+deploy
# this catalog entry," exactly like an ordinary `te deploy --config <id>`.
# --------------------------------------------------------------------------
class ImprintListItemDTO(BaseModel):
    id: str
    hosts: int
    segments: int
    # "" for a bundle whose capture instant is only in its topology export
    # and that export is absent -- empty, never invented.
    as_of: str
    # NON-NULL, and 0.0 with `confidence_source: "absent"` for a bundle that
    # carries no normalized `summary` block to take it from (D3 (a), `docs/
    # WORK_ORDER_console_canvas.md` §8.1). **Read the pair, never the float.**
    # The non-null shape was originally owed to the Track C Grafana imprints
    # screen (`imp.mean_confidence * 100`); that screen was RETIRED at Console
    # S7 (D5, 2026-09-11) precisely because it rendered the float alone, so
    # this field has NO UI consumer today -- the plugin's surviving deploy
    # action reads no measurement at all, and the Console SPA reads the
    # qualifier. Kept non-null for the wire contract; the rule is unchanged.
    mean_confidence: float
    has_provenance: bool = True
    confidence_source: str = "sidecar"
    # Unified Console: whether the bundle carries a topology export (with
    # `has_provenance`, capture-backed vs hand-authored in the outline).
    has_topology_export: bool = False
    # The imprint this twin was cast from, or None for an authored twin
    # (`docs/design/imprint-twin-split.md` §3.2).
    cast_from: Optional["CastFromDTO"] = None
    # Fix round 1: whether `POST /api/imprints/{id}/apply` can run here, and
    # why not -- a twins/ folder, aximprint bin/store unset, or no
    # `cast_invocation` block -- so the UI states it instead of guessing.
    can_apply: bool = False
    apply_blocked_reason: Optional[str] = None


class ImprintSkippedDTO(BaseModel):
    """S5: a catalog subdirectory the tolerant listing could NOT publish, and
    why. A tolerant reader that silently drops what it cannot read leaves an
    operator staring at a Console missing a bundle, with no way to find out
    which one or why -- the skip is reported, never invisible."""

    id: str
    reason: str


class ImprintListDTO(BaseModel):
    imprints: list[ImprintListItemDTO]
    # Every subdirectory the tolerant listing could not publish, with the
    # reason (S5). Defaulted so an existing consumer is untouched.
    skipped: list[ImprintSkippedDTO] = []


class ImprintSegmentDTO(BaseModel):
    """One learned-topology segment (the Console's confidence-shaded
    topology panel). `confidence` is `None` when the imprint's sidecar
    carries no evidence for this segment -- never fabricated as 0.0."""

    name: str
    cidr: str
    host_count: int
    confidence: Optional[float] = None


class ImprintSummaryDTO(BaseModel):
    id: str
    as_of: str
    store: Optional[str] = None
    days_observed: Optional[int] = None
    windows: Optional[int] = None
    hosts: int
    segments: int
    services: int
    personas: int
    mean_confidence: float
    out_of_vocab_pct: float
    topology_segments: list[ImprintSegmentDTO] = []
    # R7 (GAP-082), all ADDITIVE. They were added beside a Grafana imprints
    # screen that read none of them and rendered the non-null numbers alone --
    # which is why that screen was retired at Console S7 (D5, 2026-09-11,
    # `docs/PRODUCT_GAPS.md` GAP-083). This whole DTO now has no UI consumer;
    # the Console SPA reads the qualifiers, and the plugin renders no
    # measurement. `sidecar_shape` is what was parsed
    # ("canonical" | "normalized" | "unrecognized" | "absent"), in the
    # vocabulary `CastSummaryDTO`/`ImprintProvenanceDTO` already use:
    # `out_of_vocab_pct` is a measurement ONLY when it reads "normalized", and
    # `warnings` says so in words when it is not; `services` (declared
    # listeners over host instances) and `personas` (persona documents in the
    # bundle) are counted from the bundle for every other shape.
    # REQUIRED, as is `confidence_source` (GAP-082 review): the one builder
    # states both, so a default could only ever be a claim nobody made.
    sidecar_shape: str
    # The PIT this twin was cast from (its topology export's baked id), or None.
    pit_id: Optional[str] = None
    # Where `mean_confidence` came from -- the listing entry's own field
    # (`ImprintListItemDTO.confidence_source`), so an entry and its summary
    # make the same statement.
    confidence_source: str
    # What the reader publishes instead of refusing: a topology export's
    # baked id that differs from the canonical directory name, a normalized
    # sidecar that declares no imprint_id, non-finite confidences ignored,
    # and any stat that was not measured.
    warnings: list[str] = []
    can_apply: bool = False
    apply_blocked_reason: Optional[str] = None


class ProvenanceOverrideDTO(BaseModel):
    """A higher evidence class winning over a lower one, published rather
    than erased (`docs/design/14-operator-assertions.md` §2). `evidence_class`
    is the LOSING class (the sidecar spells it `class`); `value` is what the
    fact would otherwise have been."""

    evidence_class: Optional[str] = None
    value: Optional[Any] = None
    confidence: Optional[float] = None
    drift: Optional[bool] = None


class ImprintProvenanceRowDTO(BaseModel):
    field: str
    signal: str
    # One of the SIX classes (`14` §2): asserted | imported | observed |
    # referenced | imputed | compiler. Published as the sidecar wrote it --
    # an unrecognized class is warned about, never rewritten.
    evidence_class: str
    # NON-NULL: a canonical `compiler` row has no confidence BY DEFINITION and
    # reports `has_confidence: false` with a 0.0 here. **Read the pair, never
    # the float alone.** The non-null shape was owed to the Track C Grafana
    # screen (`row.confidence.toFixed(2)`), which rendered exactly that lone
    # float and was retired for it at Console S7 (D5, 2026-09-11); no UI reads
    # this route today. The rule stands for whatever reads it next.
    confidence: float
    evidence: str
    has_confidence: bool = True
    # Canonical-sidecar extras. Null on the normalized shape, which has no
    # equivalent field -- absent, not empty.
    source: Optional[str] = None
    note: Optional[str] = None
    overrides: Optional[ProvenanceOverrideDTO] = None


class ImprintProvenanceDTO(BaseModel):
    imprint_id: str
    rows: list[ImprintProvenanceRowDTO] = []
    # "canonical" | "normalized" | "unrecognized" | "absent" -- WHICH sidecar
    # shape was read (S1.4; "unrecognized" = a `schema` this reader does not
    # consume: present, zero rows, GAP-082 review). Stated, so a consumer
    # never infers it from the row contents.
    sidecar_shape: str = "normalized"
    sidecar_schema: Optional[str] = None
    # "sidecar fields" | "re-derived from topology export" -- whether the
    # producer supplied these classes (LES-219: bound to the derivation, not
    # to the artifact's absence).
    #
    # **S2 -- read this beside `sidecar_shape`, and note the SAME field name
    # means something adjacent-but-different on `GET .../cast`.** Here it is
    # the source of THESE PUBLISHED ROWS. There it is the fidelity reader's
    # own consumption state for the scorecard -- "did the scorecard's
    # evidence classes come from the producer" -- which can say
    # "re-derived..." for a bundle whose sidecar this endpoint reads happily
    # in some other shape. `sidecar_shape` is the unambiguous field for "what
    # did the provenance endpoint actually parse".
    evidence_source: str = "sidecar fields"
    code_fingerprint: Optional[str] = None
    # The canonical sidecar's own population accounting (suppressed hosts,
    # role-less entities, node groups). Empty on the normalized shape.
    population: dict[str, Any] = {}
    warnings: list[str] = []


# --------------------------------------------------------------------------
# Imprint "deploy as twin" DTOs (aximprint-integration Track C slice 2,
# `docs/WORK_ORDER_aximprint_console.md` PART A). The imprint id lives in the
# URL path (`POST /api/imprints/{imprint_id}/plan|deploy`), so these request
# bodies carry only what a bare `scenario`/`config` deploy's body ALSO carries
# minus the source field itself -- never a second `scenario`/`config`/
# `imprint` selector a caller could set inconsistently with the path.
# `imprint_deploy` (`service.py`) is a thin wrapper that maps these onto an
# ordinary `CreateDeployRequest(imprint=imprint_id, ...)` and calls the SAME
# `create_deploy` every other deploy source goes through (no second executor
# wiring) -- so `ImprintDeployRequest` mirrors `CreateDeployRequest`'s
# `--update` fields one-for-one, and the response IS `CreateDeployResponse`
# (not a new type).
# --------------------------------------------------------------------------
class ImprintPlanRequest(BaseModel):
    target_node: Optional[str] = None
    params: Optional[dict[str, str]] = None


class ImprintDeployRequest(BaseModel):
    target_node: Optional[str] = None
    params: Optional[dict[str, str]] = None
    fresh: bool = False
    update: bool = False
    allow_destroy: bool = False
    # GAP-098: the `plan_id` from `POST /api/imprints/{id}/plan`; a deploy
    # that re-plans to anything else is refused 409 (see CreateDeployRequest).
    expect_plan_id: Optional[str] = None



# --------------------------------------------------------------------------
# Topology export (the aximprint cast-topology-export artifact, `docs/
# CHANGELOG.md` 2026-08-27) -- `<id>.topology.json`, emitted by
# `aximprint-axrange/scripts/install_cast_as_twin.py` BESIDE the twin-
# config/imprint it casts from a capture, never imported/read from
# aximprint's own duckdb store (`axrange/api/topology_export.py` is the
# ONLY reader; this module never touches that package). This is the SOURCE
# for the Console's Logical (`segments`+`edges`, the conversation graph)
# and Physical (`nodes`, the full observed census incl. every `role: null`
# entity) views -- a shipped `twin_config` is NOT an acceptable substitute
# for either (a lossy projection with no traffic edges; see `docs/
# WORK_ORDER_console_selector_and_stale_deploys.md`'s decision record).
# `GET /api/scenarios/{id}/topology-export?source=twin-config|imprint`.
# --------------------------------------------------------------------------
class TopologyExportSegmentDTO(BaseModel):
    cidr: str
    name: str
    entities: int = 0
    coverage_source: Optional[str] = None


class TopologyExportNodeDTO(BaseModel):
    id: str
    address: Optional[str] = None
    locality: Optional[str] = None
    segment: Optional[str] = None
    segment_evidence_class: Optional[str] = None
    # None = role not discovered at all -- exactly the population `census.
    # with_role` counts against `census.entities_total`; render this
    # distinctly (constraint 2, work order) rather than hiding it.
    role: Optional[str] = None
    role_confidence: Optional[float] = None
    role_evidence_class: Optional[str] = None
    windows_seen: Optional[int] = None
    listen_ports: list[str] = []
    dial_ports: list[str] = []


class TopologyExportEdgeDTO(BaseModel):
    src: str
    dst: str
    proto: Optional[str] = None
    bytes: int = 0
    flows: int = 0
    evidence_class: Optional[str] = None


class TopologyExportCensusDTO(BaseModel):
    entities_total: int = 0
    with_role: int = 0
    placed: int = 0
    local: int = 0


class TopologyExportDTO(BaseModel):
    imprint_id: str
    pit_id: Optional[str] = None
    as_of: Optional[float] = None
    network: Optional[str] = None
    vantage: Optional[str] = None
    windows: Optional[int] = None
    schema_version: Optional[str] = None
    segments: list[TopologyExportSegmentDTO] = []
    nodes: list[TopologyExportNodeDTO] = []
    edges: list[TopologyExportEdgeDTO] = []
    census: TopologyExportCensusDTO


# --------------------------------------------------------------------------
# Console canvas DTOs (`docs/WORK_ORDER_console_canvas.md` S1;
# `docs/design/15-console-canvas.md` §10) -- the read + edit surface the
# assertions SPA binds to. Clean DTOs only: `axrange/api/imprints.py`'s and
# `console_files.py`'s dataclasses never cross this boundary.
#
# House rule throughout, inherited from the Track-C DTOs above: a value the
# producer has not shipped is `None`/`false` and reported ABSENT, never
# defaulted -- `code_fingerprint: null` says "this bundle predates the
# fingerprint", where `0.0`/`""` would say something false. The two
# deliberate exceptions are `mean_confidence` (list) and `confidence` (a
# provenance row), which stay non-null for the SHIPPED Grafana screen that
# calls `.toFixed(2)` on them and pair with `confidence_source`/
# `has_confidence` to say the value means nothing (D3 (a)).
# --------------------------------------------------------------------------
class ConduitDTO(BaseModel):
    """One `conduits[]` entry of the cast's twin config, spelled EXACTLY as
    the config spells it (R5, closes GAP-079). `from`/`to` are segment NAMES
    -- the key the Console joins the twin config to the topology export on,
    never a CIDR -- directed as written; the map draws it undirected."""

    # The wire name is `from`, a Python keyword -- the same idiom as
    # `schema_` below.
    from_: str = Field(alias="from")
    to: str

    model_config = {"populate_by_name": True}


class RouterDTO(BaseModel):
    """One ROUTER the cast's twin config builds (R5, reshaped by the GAP-082
    review, S5 and round 2): one per expanded instance of a `kind: router`
    spec -- `gw01`, `gw02` for `count: 2` -- with the segment NAMES the spec
    has a leg on, in declaration order, each once -- including a leg on a
    segment the topology export does not carry (`internet-sink`). Per router,
    never a flattened union: the Console names "the gateway" only when there
    is exactly one."""

    name: str
    segments: list[str] = []


class CastSegmentDTO(BaseModel):
    """One `segments[]` entry of the cast's twin config (Console S5.1.a):
    `name` is the join key to the topology export, `cidr` the DEPLOYED
    network (relocated on a `--relocate` cast, so it is not the export's
    observed CIDR), `zone_type` as written -- `null` when the config declares
    none, never the scenario-wide effective zone."""

    name: str
    cidr: str
    zone_type: Optional[str] = None


class CastNodeDTO(BaseModel):
    """One `nodes[]` spec of the cast's twin config (Unified Console,
    declared-design map): name, kind, role, the resolved `count`, every leg's
    segment NAME and the node ids the expander fans it out to."""

    name: str
    kind: str = "host"
    role: Optional[str] = None
    count: int = 0
    segments: list[str] = []
    node_ids: list[str] = []


class CastSummaryDTO(BaseModel):
    """`GET /api/imprints/{id}/cast` -- the compiled bundle summary."""

    # The DIRECTORY name this lookup resolved by (LES-195/LES-222); `pit_id`
    # is published and never used as an address.
    id: str
    bundle_dir: str
    twin_config_id: str
    description: Optional[str] = None
    pit_id: Optional[str] = None
    # `axr-topology/1.4` on a 2026-09-05 cast; null on an older bundle.
    bundle_schema: Optional[str] = None
    # The network-IR version (`1.3`), a DIFFERENT number from bundle_schema.
    topology_schema_version: Optional[str] = None
    # The PIT row's build stamp off the provenance sidecar (GAP-088). It does
    # NOT move on a re-cast, so it is no change detector -- see below.
    code_fingerprint: Optional[str] = None
    # The bundle's CONTENT fingerprint (ruling R4) -- the SAME value an
    # apply publishes as `fingerprint_before/after`, and so the one key that
    # says "this bundle was recompiled since". The Console stamps its drafts
    # with it. Null only when the bundle carries none of its canonical files.
    bundle_fingerprint: Optional[str] = None
    params: dict[str, Any] = {}
    # The EFFECTIVE deploy-gate threshold, with where it came from -- the
    # Console draws sub-threshold hosts as ghosts and needs the number the
    # expander would actually apply, not only the declared one.
    deploy_min_occupancy: float
    deploy_min_occupancy_source: str
    segments: int = 0
    node_specs: int = 0
    vms: int = 0
    hosts: int = 0
    conduits: int = 0
    observed_nodes: int = 0
    observed_edges: int = 0
    observed_segments: int = 0
    census: dict[str, Any] = {}
    has_provenance: bool = False
    has_topology_export: bool = False
    has_assertions: bool = False
    has_layout: bool = False
    provenance_schema: Optional[str] = None
    provenance_path: Optional[str] = None
    sidecar_shape: str = "absent"
    # S2: the FIDELITY reader's consumption state (`axrange.fidelity.
    # reference`), i.e. whether the SCORECARD's evidence classes come from
    # the producer -- NOT the same statement as
    # `ImprintProvenanceDTO.evidence_source`, which describes the rows that
    # endpoint publishes. Cross-referenced rather than renamed because both
    # spellings are already consumed; `sidecar_shape` is the field to read
    # for "what shape is this bundle's sidecar".
    evidence_source: str = ""
    warnings: list[str] = []
    # R5 (GAP-079), ADDITIVE -- the count fields above stay. `conduit_roster`
    # is the roster behind `conduits` (`len(conduit_roster) == conduits` on
    # every bundle) in the config's own order; `routers` is every `kind:
    # router` node with its legs (see `RouterDTO`). `[]` means the twin
    # config declares none -- never "unpublished". (Implementation calls:
    # `conduit_roster`, not `conduits`, because that name is the shipped
    # count; `routers`, not a flat `router_segments`, per the GAP-082
    # review's S5.)
    conduit_roster: list[ConduitDTO] = []
    routers: list[RouterDTO] = []
    # Console S5.1.a, ADDITIVE -- the roster behind the `segments` count
    # (`len(segment_roster) == segments` on every bundle), in the config's own
    # order. An observed segment whose name is not in it is one the cast
    # pruned; `[]` means the config declares no segment, never "unpublished"
    # (a server predating the field omits it, which the Console reads as not
    # published -- the R5 rule).
    segment_roster: list[CastSegmentDTO] = []
    # Unified Console, ADDITIVE -- the roster behind `node_specs`
    # (`len(node_roster) == node_specs`, `sum(count) == vms`), in the config's
    # own order. What the Console draws a bundle with no topology export from
    # (its DECLARED design); omitted by an older server, which the Console
    # reads as "not published".
    node_roster: list[CastNodeDTO] = []


class AssertionRecordDTO(BaseModel):
    """One record of the assertions file. ONE flat shape for all three
    blocks rather than a per-kind union: the closed sets are enforced by
    `console_files.validate_assertions` (which names the accepted set in
    every refusal), and a union here would answer a malformed save with
    pydantic's generic error instead."""

    kind: Optional[str] = None
    key: Optional[str] = None
    note: Optional[str] = None
    # add: segment
    name: Optional[str] = None
    zone_type: Optional[str] = None
    # add: host
    segment: Optional[str] = None
    role: Optional[str] = None
    image: Optional[str] = None
    resources: Optional[dict[str, Any]] = None
    listen: Optional[list[dict[str, Any]]] = None
    dial: Optional[list[dict[str, Any]]] = None
    # override
    field: Optional[str] = None
    value: Optional[Any] = None

    @model_serializer
    def _compact(self) -> dict[str, Any]:
        """Absent fields are OMITTED, never emitted as `null`.

        Load-bearing, not cosmetic: what `GET .../assertions` returns must be
        re-PUT-able unchanged (the SPA's save is "PUT what you read, edited"),
        and a `field: null` riding along on an `add` record would come back as
        a refusal -- `field` is not an accepted key on an add. Caught by
        `test_assertions_put_round_trip_writes_the_file_and_sets_an_etag`.

        **`value` on an OVERRIDE record is the one exception, and it is the
        SAME exception `console_files._records` makes** (M4). An override may
        legitimately clear a field (`value: null`), and the file on disk
        carried it correctly -- but this serializer re-applied the null-drop
        with no such exception, so re-PUTting the literal GET payload, which
        is the SPA's documented save, came back 400 "an override record
        requires `value`". Two layers applying one null-drop, only one of them
        carrying the exception. The discriminator is `field`, which exists on
        an override record and on nothing else, so a null `value` riding
        along on an add stays dropped.
        """
        keep_null_value = self.field is not None
        return {
            k: v for k, v in self.__dict__.items()
            if v is not None or (k == "value" and keep_null_value)
        }


class AssertionsDocDTO(BaseModel):
    """`GET`/`PUT /api/imprints/{id}/assertions`. What GET returns is exactly
    what PUT accepts (the read-only decorations are stripped server-side), so
    the SPA round-trips one shape."""

    imprint_id: str
    path: str
    # False for a bundle with no assertions file yet -- a 200 with empty
    # lists, never a 404: "nothing asserted yet" is every imprint's ordinary
    # state before the first save.
    exists: bool = False
    # None until the file exists. Send it back in `If-Match` (or in the body)
    # on the next PUT; a stale one is refused, never merged.
    etag: Optional[str] = None
    # The wire name is `schema` (the file's own key); `schema_` avoids
    # shadowing `BaseModel.schema`. FastAPI serializes by alias.
    schema_: str = Field(default="axr-assertions/1", alias="schema")
    imprint: str
    as_of: Optional[str] = None
    source: Optional[str] = None
    add: list[AssertionRecordDTO] = []
    override: list[AssertionRecordDTO] = []
    suppress: list[AssertionRecordDTO] = []
    warnings: list[str] = []

    model_config = {"populate_by_name": True}


class LayoutDocDTO(BaseModel):
    """`GET`/`PUT /api/imprints/{id}/layout` -- the Console's pins, and the
    ONE document in this API that is not evidence: no engine or compiler path
    reads it (`docs/design/15-console-canvas.md` §4)."""

    imprint_id: str
    path: str
    exists: bool = False
    etag: Optional[str] = None
    schema_: str = Field(default="axr-layout/1", alias="schema")
    imprint: str
    # `site:<prefix>` / `segment:<cidr>` -> {x, y}. A host key is refused.
    pins: dict[str, dict[str, float]] = {}
    # The layout PLANE the pins were authored against -- an opaque token the
    # Console mints and this API round-trips (`o2/console/src/layout/plane.ts`).
    # `None` = a LEGACY file with no stamp, which is what a pin saved before
    # the stamp existed looks like; it is NOT "the current plane" (LES-289).
    plane: Optional[str] = None

    model_config = {"populate_by_name": True}


class ImprintApplyRequest(BaseModel):
    """`POST /api/imprints/{id}/apply` -- apply IS save + recompile
    (`docs/design/14-operator-assertions.md` §4), so the body is the
    assertions document the save would write plus its etag."""

    etag: Optional[str] = None
    document: Optional[dict[str, Any]] = None


class ImprintApplyResponse(BaseModel):
    imprint_id: str
    # The bundle's CONTENT fingerprint before and after the recompile -- a
    # sha256 over `<id>.yaml` + `<id>.topology.json` + `<id>.provenance.json`
    # that axRange computes itself (**R4**). This is what `changed` and the
    # history key mean, and the ONE value `GET .../diff?since=` accepts --
    # deliberately not either `*_code_fingerprint_*` pair below, which are
    # aximprint BUILD stamps and name no snapshot (GAP-089).
    fingerprint_before: Optional[str] = None
    fingerprint_after: Optional[str] = None
    changed: bool = False
    # ---- the two aximprint build stamps, which are NOT the same value ----
    # GAP-088: this pair was published as `code_fingerprint_before/after` and
    # described as "the compiler's build hash", which it never was -- it is
    # copied from `<id>.provenance.json`, whose `code_fingerprint` is the PIT
    # ROW's build stamp (the identity of the run that materialized the
    # CAPTURE). A re-cast does not re-materialize the PIT, so this pair is
    # identical across an apply even when the compiler genuinely changed
    # (measured: `3f5015bd85894407` on both sides of the apply that renamed
    # 11 node groups). Renamed to what it is; nothing read the old name.
    # Null on a bundle with no sidecar.
    pit_code_fingerprint_before: Optional[str] = None
    pit_code_fingerprint_after: Optional[str] = None
    # The build that ran the cast, from `<id>.topology.json`'s
    # `cast_invocation.code_fingerprint` -- the value that DOES move when the
    # sibling repo is upgraded. Null when the export records none.
    cast_code_fingerprint_before: Optional[str] = None
    cast_code_fingerprint_after: Optional[str] = None
    # GAP-087's verdict, and THREE-valued on purpose (LES-273/LES-333):
    # `true`/`false` when both build stamps were read, `null` when one was
    # not -- "I could not tell" is not "no". A consumer that renders only
    # `=== true` must render the `null` case as its own honest outcome.
    compiler_changed: Optional[bool] = None
    # The server's own sentence for the two `compiler_changed` outcomes an
    # operator needs told (changed, and could-not-tell), carrying the reason.
    # Null for the ordinary "same build" one. Deliberately NOT repeated in
    # `warnings` -- a client printing both would say it twice.
    compiler_note: Optional[str] = None
    assertions: AssertionsDocDTO
    # The ASSERTION record counts the compiler reported for the file this
    # apply saved (`{add, override, suppress}` -- its `assertions:` block, or
    # an explicit `record_counts` where a producer publishes one).
    record_counts: dict[str, Any] = {}
    # The BUNDLE's own counts from the same document (`segments`, `nodes`,
    # `conduits`, `assignments`, `provenance_fields`). Beside the record
    # counts, never folded into them: two different questions.
    cast_counts: dict[str, Any] = {}
    # The recompiler's own stdout document, verbatim (parsed) and CAPPED.
    # Null when it printed nothing or printed non-JSON -- with a warning.
    stdout_json: Optional[dict[str, Any]] = None
    stderr: str = ""
    returncode: int = 0
    argv: list[str] = []
    # Stage 2 (`install-cast`): the argv and its exit code. `None`/empty when
    # stage 1 failed before it ran.
    install_argv: list[str] = []
    install_returncode: Optional[int] = None
    # False when no pre-apply snapshot could be recorded, so
    # `GET .../diff?since=` cannot answer for this apply. Stated, not implied.
    history_written: bool = False
    warnings: list[str] = []


class DiffEntryDTO(BaseModel):
    key: str
    fields: dict[str, dict[str, Any]] = {}


class DiffSectionDTO(BaseModel):
    added: list[str] = []
    removed: list[str] = []
    changed: list[DiffEntryDTO] = []


class ObservedDiffDTO(BaseModel):
    """What the VANTAGE saw, keyed by the stable entity keys assertions use
    (observed node ids, observed segment CIDRs). An assertion cannot change
    this side -- which is exactly why it is not the only one."""

    nodes: DiffSectionDTO = DiffSectionDTO()
    segments: DiffSectionDTO = DiffSectionDTO()


class TwinDiffDTO(BaseModel):
    """What the CAST EMITTED, keyed by the twin's own ids. This is the side
    an operator's edit moves: a `suppress` shows here as a host removal, and
    showed nowhere at all before (G2, measured live 2026-09-06)."""

    hosts: DiffSectionDTO = DiffSectionDTO()
    segments: DiffSectionDTO = DiffSectionDTO()
    conduits: DiffSectionDTO = DiffSectionDTO()


class ImprintDiffDTO(BaseModel):
    """`GET /api/imprints/{id}/diff?since=<fingerprint>` -- what changed in
    the bundle since a fingerprint an APPLY produced.

    **`null` and empty are different answers.** A side is `null` when this
    endpoint could not compare it (an older snapshot with no such section, or
    one in a schema version it does not read); it is present-and-empty when
    it compared and nothing moved. A consumer that treats the two alike will
    report "no change" for "no idea"."""

    imprint_id: str
    since: str
    current_fingerprint: Optional[str] = None
    # The recorded snapshot's own version. `null` for one written before
    # snapshot versioning existed -- in which case both sides are `null`.
    snapshot_schema: Optional[str] = None
    # "topology export" | "absent" -- describes the OBSERVED side only; an
    # older snapshot taken from a bundle with no export has nothing there.
    source: str = "topology export"
    observed: Optional[ObservedDiffDTO] = None
    twin: Optional[TwinDiffDTO] = None
    # The AUTHORED keys whose suppression state moved -- the join between an
    # operator's edit (`suppress: {key: address:10.1.10.222}`) and the twin
    # side above, which is keyed by the twin's OWN node ids and so names
    # `generic-endpoint-10-1-1-core` instead (**the §12.6 residual, settled in
    # S2**; `docs/WORK_ORDER_console_canvas.md`, `o2/console/README.md`).
    #
    # **Set granularity by construction, and published as such.** The
    # sidecar's suppressed rows carry `{key, note, placed, matched}` and do
    # NOT name the twin node a suppression removed; the twin's group ids come
    # from aximprint's own grouping rule. A per-removal `authored_key` column
    # on `TwinDiffDTO.hosts.removed` was therefore REFUSED: with two
    # suppressions and three removals it would attribute keys to hosts by
    # correlation and present it as a join. `changed` is always empty here --
    # a key is a member of a set, not a record with fields.
    #
    # `null` when either side has no `population.suppressed` accounting to
    # compare, with the reason in `warnings` (the same null-vs-empty contract
    # `observed`/`twin` carry). The DURABLE authority for "is this suppression
    # in the cast" is `GET .../provenance`'s `population.suppressed[]`, which
    # states it without needing a fingerprint to compare against.
    suppressed_keys: Optional[DiffSectionDTO] = None
    # The twin config's bytes moved. `True` with both sides empty means a
    # field-level edit (an image, a size, a role) or a change the snapshot
    # does not model -- the endpoint reports the measurement and does NOT
    # claim which.
    twin_config_changed: bool = False
    warnings: list[str] = []


# --------------------------------------------------------------------------
# Unified Console run-time reads + the per-host persona apply
# (`docs/reference/ui-consolidation/00-design-ruling.md` §3.1-§3.3; the wire
# contract is `docs/reference/ui-consolidation/04-api-contract.md`).
#
# Recorded vs live: a value read from the CLUSTER always sits in an object
# carrying `checked_at` (epoch seconds); one that could not be read is
# `state: "unknown"` with a `reason` -- never a zero or a false.
# --------------------------------------------------------------------------
class LiveValueDTO(BaseModel):
    state: str  # e.g. reachable | unreachable | running | stopped | alive | dead | unknown
    checked_at: Optional[float] = None
    reason: Optional[str] = None
    detail: Optional[str] = None


class StoreStateDTO(BaseModel):
    ok: bool
    reason: Optional[str] = None


class RangeTwinDTO(BaseModel):
    deploy_id: str
    twin_id: str
    status: str
    plan_id: Optional[str] = None
    started_at: float
    updated_at: float
    completed_at: Optional[float] = None
    cluster: str
    # Recorded. `None` = could not be derived (the reason is in `warnings`).
    host_count: Optional[int] = None
    # Completed `drive-persona` rows of the standing tfc sibling; `None` when
    # the twin has no tfc deploy (never a fabricated 0).
    persona_instance_count: Optional[int] = None
    siblings: dict[str, list[DeploySummaryDTO]] = {}
    warnings: list[str] = []
    protected: bool = False
    store: Optional[str] = None


class RangeTargetDTO(BaseModel):
    name: str
    default: bool = False
    node: Optional[str] = None
    api_url: Optional[str] = None
    reachability: LiveValueDTO
    store: StoreStateDTO
    twins: list[RangeTwinDTO] = []
    error: Optional[str] = None
    # Fix round 2: per-store status ({label, path, primary, ok, reason}),
    # target-level warnings (e.g. a deploy id recorded in two stores), and
    # axRange-named VMs on the cluster no deploy records (None with live=false):
    # {state: known|unknown, checked_at, reason, count, vms[{vmid, name, status, node}]}.
    stores: list[dict[str, Any]] = []
    warnings: list[str] = []
    unrecorded: Optional[dict[str, Any]] = None


class RangeDTO(BaseModel):
    generated_at: float
    targets: list[RangeTargetDTO]


class HostPersonaInstanceDTO(BaseModel):
    instance_id: str
    persona: str
    os_family: Optional[str] = None
    drive: Optional[str] = None
    # Never recorded by the engine (a profile's `intensity` is informational
    # and is not persisted) -- always None, present so a table column can say so.
    intensity: Optional[float] = None
    schedule: Optional[str] = None
    seed: Optional[Any] = None
    # Values Optional / Any: one odd recorded row must not 500 `/hosts`.
    peers: dict[str, Optional[str]] = {}
    peer_sets: dict[str, Any] = {}
    status: str
    op_id: str
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    origin: str = "profile"  # profile | host-edit
    # The post-start heartbeat a per-host apply recorded (live AT that time,
    # so it carries its own `checked_at`); None when none was taken.
    heartbeat: Optional[dict[str, Any]] = None
    last_error: Optional[dict[str, Any]] = None
    # Set when a per-host apply tried to stop this instance and could not
    # confirm it (the stop raised, or the post-stop heartbeat was not
    # `dead`): {message, at, heartbeat}. The row then stays as it was.
    stop_error: Optional[dict[str, Any]] = None


class HostTapDTO(BaseModel):
    tap_deploy_id: str
    net_id: str
    nic_index: Optional[int] = None
    sensors: list[str] = []
    directions: dict[str, str] = {}
    status: str


class TwinHostDTO(HostInventoryDTO):
    role_origin: str = "baseline"  # baseline | tfc | host-edit
    personas: list[HostPersonaInstanceDTO] = []
    taps: list[HostTapDTO] = []
    # Newest failed attempt of any te/tfc op scoped to this host:
    # {deploy_id, tier, op_id, op_name, error_class, error_message, at}.
    last_error: Optional[dict[str, Any]] = None
    # The newest per-host persona apply job on this host (persisted, M2):
    # {job_id, host_id, status, started_at, finished_at, fingerprint, plan,
    # diagnostics[{level, message}]}; None when the host was never edited.
    host_edit: Optional[dict[str, Any]] = None


class SiblingRefDTO(BaseModel):
    deploy_id: str
    status: str


class TwinHostsDTO(BaseModel):
    deploy_id: str
    twin_id: str
    deploy_status: Optional[str] = None
    target: str
    store: Optional[str] = None
    tfc: Optional[SiblingRefDTO] = None
    tap: Optional[SiblingRefDTO] = None
    # Server-side protection (fix round 1): writes need override_protection.
    protected: bool = False
    hosts: list[TwinHostDTO]
    net_bridge: dict[str, str] = {}
    vlan_underlay: Optional[str] = None
    egress: Optional[EgressPostureDTO] = None
    warnings: list[str] = []


class HealthPowerDTO(LiveValueDTO):
    node: Optional[str] = None


class HealthHostDTO(BaseModel):
    host_id: str
    vmid: Optional[int] = None
    recorded_status: Optional[str] = None
    power: HealthPowerDTO


class HealthPersonaDTO(BaseModel):
    instance_id: str
    persona: str
    state: str  # alive | dead | unknown
    alive: Optional[bool] = None
    unit: Optional[str] = None
    checked_at: Optional[float] = None
    reason: Optional[str] = None


class HealthHostDetailDTO(BaseModel):
    host_id: str
    qga: LiveValueDTO
    personas: list[HealthPersonaDTO] = []


class DeployHealthDTO(BaseModel):
    deploy_id: str
    target: str
    checked_at: float
    cluster: LiveValueDTO
    hosts: list[HealthHostDTO]
    host_detail: Optional[HealthHostDetailDTO] = None


class PersonaEmitDTO(BaseModel):
    proto: str
    ports: list[int] = []
    peer_role: str = ""
    transport: str = ""
    cadence: str = ""
    volume: str = ""
    optional: bool = False
    peer_scope: str = "one"


class PersonaDTO(BaseModel):
    id: str
    os_family: str
    drive: str
    source: str  # catalog | twin
    requires: list[str] = []
    summary: str = ""
    emits: list[PersonaEmitDTO] = []


class PersonaListDTO(BaseModel):
    twin: Optional[str] = None
    personas: list[PersonaDTO]
    warnings: list[str] = []


class HostPersonaAssignmentDTO(BaseModel):
    persona: str
    schedule: Optional[str] = None
    seed: Optional[Any] = None
    intensity: Optional[float] = None


class HostPersonasRequest(BaseModel):
    """`assignments` is the host's COMPLETE desired persona set: `[]` stops
    them all; omitted/null KEEPS the host's running set (a role-only edit).
    `role`: omitted/null keeps the host's role, `""` drops its tfc override
    (back to the twin baseline), a string sets it."""

    assignments: Optional[list[HostPersonaAssignmentDTO]] = None
    role: Optional[str] = None
    update_dependents: bool = False
    allow_uncontained: bool = False
    # Refused otherwise: a role change that leaves a role other running
    # personas hard-need with no holder (refusal code `last-holder`, S7).
    allow_role_orphaning: bool = False
    target_node: Optional[str] = None
    # PUT only (M4): the `fingerprint` of the plan the operator previewed.
    # When the plan computed under the lease differs -> 409 `plan-changed`
    # carrying the fresh plan. Omitted -> no precondition (CLI/scripts).
    expect_plan: Optional[str] = None


class HostPersonasPlanDTO(BaseModel):
    deploy_id: str
    host_id: str
    target: str
    tfc_deploy_id: Optional[str] = None
    creates_tfc_deploy: bool
    ok: bool
    role: dict[str, Any]
    start: list[dict[str, Any]] = []
    stop: list[dict[str, Any]] = []
    unchanged: dict[str, int]
    dependents: list[dict[str, Any]] = []
    will_update_dependents: bool = False
    refusals: list[dict[str, str]] = []
    warnings: list[str] = []
    fingerprint: str = ""


class HostPersonasApplyResponse(BaseModel):
    deploy_id: str
    host_id: str
    tfc_deploy_id: str
    status: str  # running (detached) | completed | failed
    detached: bool
    plan: HostPersonasPlanDTO
    warnings: list[str] = []


class TfcProfileDTO(BaseModel):
    deploy_id: str
    tfc_deploy_id: str
    twin_id: str
    instances: int
    host_edits: list[str] = []
    yaml: str
    notes: list[str] = []
