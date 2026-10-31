/** Mirrors of the control-plane DTOs this console reads (`api/Spec.md` A1, A7). */

export interface JsonSchemaProperty {
  type?: string;
  description?: string;
}

export interface PluginConfigSchema {
  type?: string;
  properties?: Record<string, JsonSchemaProperty>;
  required?: string[];
  additionalProperties?: boolean;
}

export interface DiscoveryPlugin {
  name: string;
  /** `path` (a file or directory the server reads) or `http` (a base URL). */
  source_kind: string;
  config_schema: PluginConfigSchema;
}

export interface DiscoveryPluginList {
  plugins: DiscoveryPlugin[];
}

export type Transport = "direct" | "ssh_tunnel";
export type Auth = "none" | "basic" | "api_key";

export interface SshAccess {
  host: string;
  user: string;
  /** A private-key PATH on the machine running `axrange serve`. */
  key_path: string;
  port: number;
}

export interface SourceAccess {
  transport: Transport;
  auth: Auth;
  user: string | null;
  ssh: SshAccess | null;
}

export interface SourceDefaults {
  days?: number;
  network?: string;
  vantage?: string;
  window?: number;
  subbuckets?: number;
  sensors?: string[];
}

export interface DiscoverySource {
  id: string;
  name: string;
  plugin: string;
  endpoint: string;
  access: SourceAccess;
  has_password: boolean;
  has_api_key: boolean;
  defaults: SourceDefaults;
  last_probe: (Probe & { probed_at: string }) | null;
  created_at: string;
  updated_at: string;
}

export interface DiscoverySourceList {
  sources: DiscoverySource[];
}

/** The write shape: secrets travel as their own fields, never in a read. */
export interface SourceWrite {
  name: string;
  plugin: string;
  endpoint: string;
  access: { transport: Transport; auth: Auth; user?: string | null; ssh?: SshAccess | null };
  password?: string;
  api_key?: string;
  defaults: SourceDefaults;
}

export type SourceUpdate = Partial<SourceWrite>;

export interface Probe {
  reachable: boolean;
  error: string | null;
  first_ts: number | null;
  last_ts: number | null;
  vantage: string | null;
  extra: Record<string, unknown>;
  via: string | null;
}

export interface ProbeResult extends Probe {
  source_id: string;
  probed_at: string;
}

export interface SourceRunRequest {
  /** Run options come from the plugin's own schema (C1.1), so the shape is open. */
  [k: string]: unknown;
  /** The imprint's name: what the bake labels the PIT. */
  name: string;
  days?: number;
  ts_from?: number;
  ts_to?: number;
  network?: string;
  vantage?: string;
  window?: number;
  subbuckets?: number;
  sensors?: string[];
}

export type RunStatus = "queued" | "running" | "completed" | "failed" | "cancelled";

export interface DiscoveryRun {
  id: string;
  status: RunStatus;
  plugin: string;
  /** The imprint's name as asked for; `imprint_id` carries the same value. */
  name: string;
  imprint_id: string;
  stages_done: string[];
  stages: string[];
  /** The PIT the bake produced: its identity, and its label (the id until renamed). */
  pit_id: string | null;
  pit_name?: string | null;
  error: string | null;
  created_at: string;
  finished_at: string | null;
  source_id: string | null;
  started_at: string | null;
  ts_from: number | null;
  ts_to: number | null;
  via: string | null;
  /** Source time the ingest has reached (the end of the last window it closed), and when it said so. */
  progress_ts: number | null;
  progress_at: string | null;
  /** Set once a cancel was asked for; the status turns `cancelled` when the stage is dead. */
  cancel_requested_at: string | null;
}

export interface DiscoveryRunList {
  runs: DiscoveryRun[];
}

/** One learned-topology segment of a twin, as `ImprintSegmentDTO` sends it. */
export interface ImprintSegment {
  name: string;
  cidr: string;
  host_count: number;
  confidence?: number | null;
  [k: string]: unknown;
}

/** A twin's lineage: the imprint it was cast from, or null for an authored twin. */
export interface CastFrom {
  pit_id: string;
  pit_name: string | null;
}

/** One row of `GET /api/twins`: a deployable twin in the catalog. */
export interface TwinListItem {
  id: string;
  hosts: number;
  segments: number;
  as_of: string;
  mean_confidence: number;
  has_provenance: boolean;
  confidence_source: string;
  has_topology_export: boolean;
  cast_from: CastFrom | null;
  can_apply: boolean;
  apply_blocked_reason: string | null;
}

export interface TwinList {
  imprints: TwinListItem[];
  /** Catalog folders the listing could not read, with the reason. */
  skipped: Array<{ id: string; reason: string }>;
}

/** An imprint: what discovery produced, a PIT in the IR store with its operator state. */
export interface Imprint {
  pit_id: string;
  name: string;
  derived_from: string | null;
  has_assertions: boolean;
  twins: Array<{ id: string }>;
  as_of?: number | null;
  observed_from?: number | null;
  observed_to?: number | null;
  source?: string | null;
  network?: string | null;
  vantage?: string | null;
  windows?: number | null;
  entities?: number | null;
  segments?: number | null;
  merge_axis?: string | null;
}

export interface ImprintList {
  pits: Imprint[];
  /** True when the IR store was locked by a bake and this is the last listing read. */
  stale?: boolean;
  warnings?: string[];
}

/** The operator's claims about the network (`axr-assertions/1`), as the API round-trips it. */
export interface AssertionsDoc {
  imprint_id: string;
  path: string;
  exists: boolean;
  etag: string | null;
  schema: string;
  imprint: string;
  as_of: string | null;
  source: string | null;
  add: Record<string, unknown>[];
  override: Record<string, unknown>[];
  suppress: Record<string, unknown>[];
  warnings?: string[];
}

export interface CastRequest {
  twin_id: string;
  sensors?: string[];
  replace?: boolean;
  relocate?: string | null;
  max_count?: number | null;
  no_chatter?: boolean;
}

export interface CastResponse {
  twin: TwinSummary;
  pit_id: string;
  replaced: boolean;
  caveats: string[];
  install_report: string;
}

export interface TwinSummary {
  id: string;
  as_of: string;
  pit_id?: string | null;
  hosts: number;
  segments: number;
  services: number;
  personas: number;
  mean_confidence: number;
  days_observed?: number | null;
  windows?: number | null;
  topology_segments?: ImprintSegment[];
  warnings?: string[];
  [k: string]: unknown;
}

/** The old name of `TwinSummary`. */
export type ImprintSummary = TwinSummary;

// --- GET /api/twins/{id}/tree: the bundle as segment -> node group -> intent ---

export interface TreeObservation {
  windows_seen?: number | null;
  windows_total?: number | null;
  occupancy?: number | null;
  role_confidence?: number | null;
}

export interface TreeListen {
  port: number | string;
  proto: string;
}

export interface PersonaEmit {
  proto: string;
  ports: number[];
  peer_role: string;
  transport: string;
  cadence: string;
  volume: string;
  optional: boolean;
  peer_scope: string;
}

/** A persona the traffic profile binds to a node group, with the document's traffic. */
export interface TreePersona {
  id: string;
  bound_by: "node" | "role";
  instances: number;
  schedule?: string | null;
  seed?: unknown;
  intensity?: number | null;
  os_family?: string | null;
  drive?: string | null;
  missing: boolean;
  emits: PersonaEmit[];
}

/** A twin-config node GROUP: `count` instances fanned out at deploy. */
export interface TreeNode {
  name: string;
  kind: "host" | "router";
  role?: string | null;
  image: string;
  count: number;
  instances: string[];
  segments: string[];
  requires: string[];
  resources?: Record<string, unknown> | null;
  listen: TreeListen[];
  install: string[];
  joins?: string | null;
  observation?: TreeObservation | null;
  personas: TreePersona[];
}

export interface TreeRoleCount {
  role: string | null;
  hosts: number;
}

export interface TreeTap {
  sensors: string[];
  mode: string;
}

export interface TreeSegment {
  name: string;
  cidr: string;
  zone_type?: string | null;
  hosts: number;
  roles: TreeRoleCount[];
  observation?: TreeObservation | null;
  taps: TreeTap[];
  nodes: TreeNode[];
}

export interface TwinTree {
  id: string;
  hosts: number;
  segments: TreeSegment[];
  routers: TreeNode[];
  traffic_profile?: string | null;
  tap_plan?: string | null;
  warnings: string[];
}

// --- GET /api/scenarios/{id}/topology: the AUTHORED twin as a bipartite host <-> network graph ---

export interface TopologyNode {
  id: string;
  /** `host` | `router` | `switch` | `network`. */
  type: string;
  image?: string | null;
  requires?: string[] | null;
  /** Network nodes only. */
  cidr?: string | null;
  vlan?: number | null;
  /** The live op status overlay when a deploy annotates the graph. */
  status?: string | null;
}

/** A node's attachment to a network, with the address it holds there. */
export interface TopologyEdge {
  source: string;
  target: string;
  ip?: string | null;
}

export interface Topology {
  scenario_id: string;
  deploy_id?: string | null;
  nodes: TopologyNode[];
  edges: TopologyEdge[];
}

// --- GET /api/twins/{id}/topology: the CAST twin with its observed evidence (the topology export) ---

export interface TopologyExportSegment {
  cidr: string;
  name: string;
  entities: number;
  coverage_source: string | null;
}

export interface TopologyExportNode {
  id: string;
  address: string | null;
  locality: string | null;
  segment: string | null;
  segment_evidence_class: string | null;
  /** `null` = the role was never discovered -- rendered distinctly, never hidden. */
  role: string | null;
  role_confidence: number | null;
  role_evidence_class: string | null;
  windows_seen: number | null;
  listen_ports: string[];
  dial_ports: string[];
}

export interface TopologyExportEdge {
  src: string;
  dst: string;
  proto: string | null;
  bytes: number;
  flows: number;
  evidence_class: string | null;
}

export interface TopologyExportCensus {
  entities_total: number;
  with_role: number;
  placed: number;
  local: number;
}

export interface TopologyExport {
  imprint_id: string;
  pit_id: string | null;
  as_of: number | null;
  network: string | null;
  vantage: string | null;
  windows: number | null;
  schema_version: string | null;
  segments: TopologyExportSegment[];
  nodes: TopologyExportNode[];
  edges: TopologyExportEdge[];
  census: TopologyExportCensus;
}

// --- GET|PUT /api/twins/{id}/layout: the canvas's positions side-car (`axr-layout/1`), ETag-guarded ---

/** `site:<prefix>` / `segment:<cidr>` / `host:<name>` -> an absolute top-left on the plane. */
export type LayoutPins = Record<string, { x: number; y: number }>;

/** One canvas annotation in the layout side-car: a positioned SVG snippet.
 *  `id` is 1-64 chars of `[A-Za-z0-9._:-]`, unique; `svg` at most 8 KiB and
 *  refused when it carries script, a foreignObject, an `on*=` attribute or
 *  an href; at most 200 per document. */
export interface LayoutAnnotation {
  id: string;
  /** `text` | `rect` | `ellipse` | `line`. */
  kind: string;
  x: number;
  y: number;
  w?: number | null;
  h?: number | null;
  svg: string;
  locked: boolean;
  hidden: boolean;
}

export interface LayoutDoc {
  imprint_id: string;
  path: string;
  exists: boolean;
  /** The precondition for the next PUT; rides in the body as well as the header. */
  etag: string | null;
  schema: string;
  imprint: string;
  pins: LayoutPins;
  /** The drawing plane the pins were authored on -- an opaque stamp the server round-trips. */
  plane: string | null;
  /** `[]` for a document without the key; an empty list PUT back writes no key. */
  annotations: LayoutAnnotation[];
  warnings?: string[];
}

/** The PUT body: the document as read, edited, with its etag as the precondition. */
export interface LayoutWrite {
  schema: string;
  imprint: string;
  pins: LayoutPins;
  plane: string;
  etag: string | null;
  annotations?: LayoutAnnotation[];
}

/** `POST /api/validate {document}`: a twin-config bundle validated through the Save / Persist path, nothing written. */
export interface ValidateDocumentRequest {
  document: { id: string; files: Record<string, string> };
}

// --- GET /api/twin-configs and GET /api/personas: the palette's sources (Phase 2 of the canvas) ---

export interface TwinConfigSummary {
  id: string;
  description: string | null;
  cluster: string | null;
  parameterized: boolean;
  param_names: string[];
  error: string | null;
}

export interface TwinConfigList {
  twin_configs: TwinConfigSummary[];
}

export interface Persona {
  id: string;
  os_family: string;
  drive: string;
  /** `catalog` | `twin`. */
  source: string;
  requires: string[];
  summary: string;
  emits: PersonaEmit[];
}

export interface PersonaList {
  twin: string | null;
  personas: Persona[];
  warnings: string[];
}

// --- The edit model (`api/Spec.md` A2, A3): the twin-config document, its write shapes and a deploy's working copy ---

/** A bundle's text files keyed by bundle-relative path; `te_file` names the twin config among them. */
export interface TwinConfigDocument {
  id: string;
  te_file: string;
  files: Record<string, string>;
  /** Bundle files that are not text and so not part of the document. */
  skipped: string[];
}

export interface TwinConfigDetail {
  id: string;
  description: string | null;
  cluster: string | null;
  params: Array<{ name: string; default?: unknown; description?: string | null; declared?: boolean }>;
  segments: Array<{ name: string; cidr: string; zone_type?: string | null; when?: string | null }>;
  nodes: Array<{ name: string; kind: string; image: string; count: unknown; role?: string | null; when?: string | null }>;
  identity_domain?: string | null;
  identity_user_count?: number;
  data_count?: number;
  document: TwinConfigDocument | null;
  warnings: string[];
}

/** `POST /api/twin-configs` (Save-As, `id` new) and `PUT /api/twin-configs/{id}` (Save / Persist). */
export interface TwinConfigWrite {
  id?: string;
  files: Record<string, string>;
}

export type PendingChangeKind = "attribute" | "topology" | "inert";

/** One file that differs between the baseline and the working copy, classified by the API. */
export interface PendingChange {
  path: string;
  op: "added" | "removed" | "modified" | string;
  kind: PendingChangeKind | string;
  /** The playouts an attribute change re-applies through (`tfc`, `tap`). */
  via: string[];
  /** The differing dotted YAML paths inside the file. */
  paths: string[];
  reason: string;
  /** A te `role` change classified attribute: node group -> new role. */
  roles: Record<string, string>;
}

export interface DeployConfig {
  deploy_id: string;
  twin_id: string;
  dirty: boolean;
  has_working_copy: boolean;
  document: TwinConfigDocument;
  changes: PendingChange[];
  /** True when every pending change is attribute or inert. */
  apply_allowed: boolean;
  applied: { at: string; tiers: string[]; roles: Record<string, string> } | null;
  updated_at: string | null;
  warnings: string[];
}

export interface DeployConfigApply {
  deploy_id: string;
  twin_id: string;
  applied: string[];
  roles: Record<string, string>;
  warnings: string[];
  config: DeployConfig;
}

export interface DeployConfigReset {
  deploy_id: string;
  twin_id: string;
  discarded: boolean;
  reverted: string[];
  warnings: string[];
  config: DeployConfig;
}

/** One row of `GET /api/deploys`: what the canvas reads to know whether a twin stands. */
export interface DeploySummary {
  deploy_id: string;
  scenario_id: string;
  scenario_version?: number | null;
  /** `running` | `completed` | `failed` | `cancelled` | `destroyed`. */
  status: string;
  started_at: number;
  updated_at: number;
  completed_at?: number | null;
  /** The display tier (`te` | `tap` | `tfc` | `exr` | `o2`); a twin's own deploy is `te`. */
  tier?: string | null;
  plan_id?: string | null;
  target?: string | null;
  store?: string | null;
}

export interface DeployList {
  deploys: DeploySummary[];
  warnings: string[];
}

export interface ValidationFinding {
  rule_id: string;
  severity: "error" | "warning" | "info" | string;
  message: string;
  location?: string | null;
}

export interface ValidateResult {
  disposition: string;
  exit_code: number;
  passes_run: number[];
  errors: number;
  warnings: number;
  findings: ValidationFinding[];
}
