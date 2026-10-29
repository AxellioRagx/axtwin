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
