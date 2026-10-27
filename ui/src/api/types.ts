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
  imprint_id: string;
  days?: number;
  ts_from?: number;
  ts_to?: number;
  network?: string;
  vantage?: string;
  window?: number;
  subbuckets?: number;
  sensors?: string[];
}

export type RunStatus = "queued" | "running" | "completed" | "failed";

export interface DiscoveryRun {
  id: string;
  status: RunStatus;
  plugin: string;
  imprint_id: string;
  stages_done: string[];
  stages: string[];
  pit_id: string | null;
  error: string | null;
  created_at: string;
  finished_at: string | null;
  source_id: string | null;
  started_at: string | null;
  ts_from: number | null;
  ts_to: number | null;
  via: string | null;
}

export interface DiscoveryRunList {
  runs: DiscoveryRun[];
}

export interface ImprintSegment {
  cidr?: string;
  name?: string;
  hosts?: number;
  [k: string]: unknown;
}

export interface ImprintSummary {
  id: string;
  as_of: string;
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
