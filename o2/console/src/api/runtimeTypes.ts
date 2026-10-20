/**
 * Hand-written mirrors of the RUN-TIME DTOs -- the Range, Twin and Catalog
 * areas' half of the API (`docs/reference/ui-consolidation/04-api-contract.md`,
 * implementing ruling §3). The Design area's DTOs stay in `types.ts`.
 *
 * Every field the contract marks nullable is nullable here, and the views
 * render `null` as "unknown", never as zero (ruling §2 rule 4). Every value
 * read from the cluster sits in an object carrying `checked_at`; anything
 * without one is RECORDED (state store) -- the views label it so (rule 3).
 */

/** A live read that may have failed: `state: "unknown"` carries the reason. */
export interface LiveState {
  state: string;
  checked_at: number | null;
  reason: string | null;
  detail?: string | null;
}

// ---- targets ------------------------------------------------------------
export interface TargetInfo {
  name?: string;
  default?: boolean;
  node: string;
  nodes?: string[];
  api_url: string;
  verify_tls?: boolean;
  has_token?: boolean;
  has_ssh_key?: boolean;
  db_path?: string | null;
  error?: string | null;
}

export interface TargetList {
  targets: TargetInfo[];
}

// ---- deploys --------------------------------------------------------------
export interface DeploySummary {
  deploy_id: string;
  scenario_id: string;
  scenario_version: number | null;
  status: string;
  started_at: number;
  updated_at: number;
  completed_at: number | null;
  /** `te | tap | tfc | exr | o2` -- added by the consolidation; absent on an
   *  older server. */
  tier?: string;
  plan_id?: string | null;
  target?: string | null;
  /** Which of the target's state stores records it (a short label). */
  store?: string | null;
}

export interface OpAttempt {
  attempt_id: number;
  started_at: number;
  ended_at: number | null;
  result: string | null;
  error_class: string | null;
  error_message: string | null;
}

export interface Op {
  op_id: string;
  op_name: string;
  op_catalog_id: string;
  kind: string;
  status: string;
  started_at: number | null;
  completed_at: number | null;
  attempts: OpAttempt[];
}

export interface DeployDetail {
  deploy: DeploySummary;
  plan_id: string;
  placement: Record<string, string>;
  ops: Op[];
  counts: Record<string, number>;
  host_edits?: HostEditJob[];
}

// ---- range ---------------------------------------------------------------
export interface RangeTwin {
  deploy_id: string;
  twin_id: string;
  /** Writes against a protected twin are refused (423) unless overridden. */
  protected?: boolean;
  /** The state store that records it (several per target are possible). */
  store?: string | null;
  status: string;
  plan_id: string | null;
  started_at: number;
  updated_at: number;
  completed_at: number | null;
  cluster: string | null;
  host_count: number | null;
  persona_instance_count: number | null;
  siblings: { tap: DeploySummary[]; tfc: DeploySummary[]; exr: DeploySummary[] };
  warnings: string[];
}

export interface RangeTarget {
  name: string;
  default: boolean;
  node: string | null;
  api_url: string | null;
  reachability: LiveState;
  store: { ok: boolean; reason: string | null };
  twins: RangeTwin[];
  /** The target's adapter.conf load error, if any. */
  error?: string | null;
  /** Every state store this target reads (the first/primary takes new deploys). */
  stores?: Array<{ label: string; path: string; primary: boolean; ok: boolean; reason: string | null }>;
  /** LIVE: axRange-named VMs on the cluster that no recorded deploy owns.
   *  `null` when not checked; absent on an older server. */
  unrecorded?: UnrecordedVms | null;
}

export interface UnrecordedVms {
  state: string;
  checked_at: number | null;
  reason: string | null;
  count: number | null;
  vms: Array<{ vmid: number; name: string | null; status: string | null; node: string | null }>;
}

export interface RangeDoc {
  generated_at: number;
  targets: RangeTarget[];
}

// ---- the twin's hosts ------------------------------------------------------
export interface HostNet {
  net_id: string;
  ip: string | null;
  nic_index: number | null;
  external: boolean;
}

export interface Heartbeat {
  alive: boolean | null;
  state: string;
  checked_at: number | null;
  reason: string | null;
  unit?: string | null;
}

export interface LastError {
  deploy_id: string;
  tier: string;
  op_id: string;
  op_name: string;
  error_class: string | null;
  error_message: string | null;
  at: number | null;
}

export interface PersonaInstance {
  instance_id: string;
  persona: string;
  os_family: string | null;
  drive: string | null;
  intensity: number | null;
  schedule: string | null;
  /** The recorded seed -- sent back unchanged to keep a persona as is. */
  seed: unknown;
  peers: Record<string, unknown | null>;
  peer_sets?: Record<string, unknown>;
  status: string;
  op_id: string | null;
  started_at: number | null;
  completed_at: number | null;
  origin: string | null;
  heartbeat: Heartbeat | null;
  last_error: LastError | null;
  /** A stop that raised, or whose post-stop heartbeat did not say dead: the
   *  row stays and the persona may still be running. */
  stop_error?: { message: string; at: number | null; heartbeat: Heartbeat | null } | null;
}

/** A per-host apply's persisted job record (`/hosts` `host_edit`, and
 *  `GET /api/deploys/{tfc}` `host_edits[]`). */
export interface HostEditJob {
  job_id: string;
  host_id: string;
  status: string;
  started_at: number | null;
  finished_at: number | null;
  fingerprint: string | null;
  plan: PersonaPlan | null;
  diagnostics: Array<{ level: string; message: string }>;
  /** A later whole-profile apply replaced this edit (fix round 4): it is
   *  history, not the host's live state. */
  superseded?: boolean;
  superseded_at?: number | null;
}

export interface HostTap {
  tap_deploy_id: string;
  net_id: string;
  nic_index: number | null;
  sensors: string[];
  directions: Record<string, string>;
  status: string;
}

export interface TwinHost {
  host_id: string;
  vm_name: string | null;
  vmid: number | null;
  pve_node: string | null;
  vm_status: string | null;
  role: string | null;
  role_baseline: string | null;
  role_origin: string | null;
  kind: string | null;
  os: string | null;
  nets: HostNet[];
  listen: Array<{ port: number; proto: string }> | null;
  personas: PersonaInstance[];
  taps: HostTap[];
  last_error: LastError | null;
  host_edit?: HostEditJob | null;
}

export interface SiblingRef {
  deploy_id: string;
  status: string;
}

export interface TwinHostsDoc {
  deploy_id: string;
  twin_id: string;
  protected?: boolean;
  /** The state store that records this twin. */
  store?: string | null;
  deploy_status: string | null;
  target: string | null;
  tfc: SiblingRef | null;
  tap: SiblingRef | null;
  hosts: TwinHost[];
  net_bridge: Record<string, string>;
  vlan_underlay?: string | null;
  egress: {
    router_host: string | null;
    phase_declared: string | null;
    phase_applied: string | null;
    contained: boolean;
    live_read_at: string | null;
  } | null;
  warnings: string[];
}

// ---- live health ------------------------------------------------------------
export interface HostPower {
  host_id: string;
  vmid: number | null;
  recorded_status: string | null;
  power: LiveState & { node?: string | null };
}

export interface PersonaLiveness {
  instance_id: string;
  persona: string;
  state: string;
  alive: boolean | null;
  unit: string | null;
  checked_at: number | null;
  reason: string | null;
}

export interface HealthDoc {
  deploy_id: string;
  target: string | null;
  checked_at: number;
  cluster: LiveState;
  hosts: HostPower[];
  host_detail: {
    host_id: string;
    qga: LiveState;
    personas: PersonaLiveness[];
  } | null;
}

// ---- access (existing endpoint) ------------------------------------------
export interface HostAccess {
  host_id: string;
  kind: string;
  vmid: number | null;
  os_family: string;
  ips: Record<string, string>;
  wan_ip: string | null;
  ip: string;
  user: string | null;
  password: string | null;
  hint: string | null;
}

export interface AccessFacts {
  deploy_id: string | null;
  scenario_id: string;
  status: string | null;
  hosts: HostAccess[];
  subnets: Array<{ net_id: string; cidr: string | null; gateway: string | null; snat: boolean }>;
}

// ---- personas ---------------------------------------------------------------
export interface PersonaEmit {
  proto: string;
  ports: number[];
  peer_role: string | null;
  transport: string | null;
  cadence: string | null;
  volume: string | null;
  optional: boolean;
  peer_scope: string | null;
}

export interface PersonaInfo {
  id: string;
  os_family: string;
  drive: string | null;
  source: string;
  requires: string[];
  summary: string;
  emits: PersonaEmit[];
}

export interface PersonaCatalog {
  twin: string | null;
  personas: PersonaInfo[];
  warnings: string[];
}

// ---- per-host persona change ----------------------------------------------
export interface PersonaAssignment {
  persona: string;
  schedule: string | null;
  seed: unknown;
  intensity: number | null;
}

export interface PersonaChangeRequest {
  assignments: PersonaAssignment[];
  /** `null` = keep; `""` = back to the twin's own role; a string = set it. */
  role: string | null;
  update_dependents: boolean;
  allow_uncontained: boolean;
  allow_role_orphaning: boolean;
  /** PUT only: the fingerprint of the plan the operator confirmed. */
  expect_plan?: string;
}

export interface Refusal {
  code: string;
  message: string;
}

export interface PersonaPlan {
  deploy_id: string;
  host_id: string;
  target: string | null;
  tfc_deploy_id: string | null;
  creates_tfc_deploy: boolean;
  ok: boolean;
  role: { current: string | null; baseline: string | null; new: string | null; changed: boolean };
  /** Every instance that will be (re)driven -- this host's, and (reason
   *  `dependent`) other hosts' re-driven because of a role change. */
  start: Array<{
    host_id?: string;
    instance_id: string;
    persona: string;
    os_family?: string | null;
    reason: string;
    peers?: Record<string, unknown>;
    schedule?: string | null;
  }>;
  stop: Array<{ host_id?: string; instance_id: string; persona: string; op_id?: string | null }>;
  unchanged: { hosts: number; instances: number };
  dependents: Array<{
    host_id: string;
    instance_id: string;
    persona: string;
    stale_peers: Record<string, { recorded: unknown; new: unknown }>;
    /** The role change leaves this dependent's needed role with no holder. */
    no_new_holder?: boolean;
  }>;
  will_update_dependents: boolean;
  refusals: Refusal[];
  warnings: string[];
  /** Identifies this plan; the PUT's `expect_plan`. */
  fingerprint?: string;
}

export interface PersonaApplyResponse {
  deploy_id: string;
  host_id: string;
  tfc_deploy_id: string;
  status: string;
  detached: boolean;
  plan: PersonaPlan;
  warnings?: string[];
}

export interface ProfileExport {
  deploy_id: string;
  tfc_deploy_id: string;
  twin_id: string | null;
  instances: number;
  host_edits: string[];
  yaml: string;
  notes: string[];
}

// ---- catalog -------------------------------------------------------------------
export interface TwinConfigSummary {
  id: string;
  description: string | null;
  error: string | null;
  cluster?: string | null;
  parameterized?: boolean;
  param_names?: string[];
}

export interface TwinConfigList {
  twin_configs: TwinConfigSummary[];
}

export interface TwinConfigDetail {
  id: string;
  description: string | null;
  cluster?: string | null;
  /** `declared` = in the twin's `params:` block (vs an inline
   *  `${NAME:default}` only). */
  params: Array<{ name: string; default: unknown; description?: string | null; declared?: boolean }>;
  segments: Array<{ name: string; cidr: string; zone_type: string | null; when: string | null }>;
  nodes: Array<{ name: string; kind: string; image: string; count: unknown; role: string | null; when: string | null }>;
  identity_domain: string | null;
  identity_user_count: number;
  data_count: number;
}

export interface ScenarioList {
  scenarios: Array<{ id: string; path: string }>;
}

export interface Finding {
  rule_id: string;
  severity: string;
  message: string;
  location: string | null;
}

export interface ValidateResult {
  disposition: string;
  exit_code: number;
  passes_run: number[];
  errors: number;
  warnings: number;
  findings: Finding[];
}

export interface PlanSummary {
  plan_id: string;
  scenario_id: string;
  scenario_version: number | null;
  ir_hash: string;
  ops: number;
  barriers: number;
  edges: number;
  tree: string;
  config_warnings: string[];
  /** VMs the plan creates (fix round 2); absent on an older server. */
  vm_count?: number;
  /** The parameters as RESOLVED for this plan (`{}` for a plain scenario). */
  params?: Record<string, unknown>;
}

/** What `POST /api/plan` / `/api/validate` plan: exactly one source. */
export interface PlanSource {
  scenario?: string;
  config?: string;
  imprint?: string;
  params?: Record<string, string>;
}

export interface ScenarioTopology {
  scenario_id: string;
  deploy_id: string | null;
  nodes: Array<{ id: string; type: string; image: string | null; cidr: string | null; status: string | null }>;
  edges: Array<{ source: string; target: string; ip: string | null }>;
}

export interface CreateDeployRequest {
  scenario?: string;
  config?: string;
  imprint?: string;
  params?: Record<string, string>;
  /** The `plan_id` the operator previewed; a mismatch is a 409 plan-changed. */
  expect_plan_id?: string;
}

export interface CreateDeployResponse {
  deploy_id: string;
  status: string;
  detached: boolean;
  config_warnings: string[];
  plan_id?: string | null;
}

export interface DestroyResponse {
  deploy_id: string;
  destroyed: boolean;
  removed: number;
  errors: number;
  actions: Array<{ kind: string; target: string; status: string; detail: string | null }>;
  cascade_errors: string[];
}

// ---- served config -------------------------------------------------------------
/** Telemetry link-outs; the base URL may come from the server (`/api/o2/info`
 *  endpoints) or be typed on the Telemetry page. */
export interface O2Info {
  endpoints: Record<string, string>;
}
