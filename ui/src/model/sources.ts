/**
 * The discovery-source form, as pure data: what the operator chooses on the
 * screen <-> what the API stores. No React, no fetch.
 *
 * The screen offers ONE access choice -- user/password, API key, or an SSH
 * tunnel -- because that is how an operator thinks about reaching a sensor.
 * The API stores it as `transport` x `auth`, so a tunnel can still carry a
 * credential for the API behind it (`tunnelAuth`).
 */
import type {
  DiscoveryPlugin,
  DiscoverySource,
  JsonSchemaProperty,
  Probe,
  SourceDefaults,
  SourceWrite,
} from "../api/types";

export type AccessChoice = "none" | "basic" | "api_key" | "ssh_tunnel";
export type TunnelAuth = "none" | "basic" | "api_key";

export interface SourceForm {
  name: string;
  plugin: string;
  endpoint: string;
  access: AccessChoice;
  user: string;
  password: string;
  apiKey: string;
  tunnelAuth: TunnelAuth;
  ssh: { host: string; port: string; user: string; keyPath: string };
  defaults: { days: string; network: string; vantage: string; window: string; subbuckets: string; sensors: string };
}

export const ACCESS_LABELS: Record<AccessChoice, string> = {
  none: "No credential",
  basic: "User and password",
  api_key: "API key",
  ssh_tunnel: "SSH tunnel",
};

export function emptyForm(plugin = ""): SourceForm {
  return {
    name: "",
    plugin,
    endpoint: "",
    access: "none",
    user: "",
    password: "",
    apiKey: "",
    tunnelAuth: "none",
    ssh: { host: "", port: "22", user: "", keyPath: "" },
    defaults: { days: "10", network: "", vantage: "", window: "", subbuckets: "", sensors: "" },
  };
}

/** The effective auth the API will store for a form. */
export function effectiveAuth(form: SourceForm): TunnelAuth {
  return form.access === "ssh_tunnel" ? form.tunnelAuth : form.access;
}

export function fromSource(src: DiscoverySource): SourceForm {
  const f = emptyForm(src.plugin);
  f.name = src.name;
  f.endpoint = src.endpoint;
  const auth = src.access.auth;
  if (src.access.transport === "ssh_tunnel") {
    f.access = "ssh_tunnel";
    f.tunnelAuth = auth;
    const ssh = src.access.ssh;
    if (ssh) f.ssh = { host: ssh.host, port: String(ssh.port), user: ssh.user, keyPath: ssh.key_path };
  } else {
    f.access = auth;
  }
  f.user = src.access.user ?? "";
  const d = src.defaults;
  f.defaults = {
    days: d.days === undefined ? "" : String(d.days),
    network: d.network ?? "",
    vantage: d.vantage ?? "",
    window: d.window === undefined ? "" : String(d.window),
    subbuckets: d.subbuckets === undefined ? "" : String(d.subbuckets),
    sensors: (d.sensors ?? []).join(", "),
  };
  return f;
}

function num(text: string): number | undefined {
  const t = text.trim();
  if (!t) return undefined;
  const n = Number(t);
  return Number.isFinite(n) ? n : undefined;
}

export function defaultsFromForm(d: SourceForm["defaults"]): SourceDefaults {
  const out: SourceDefaults = {};
  const days = num(d.days);
  if (days !== undefined) out.days = days;
  if (d.network.trim()) out.network = d.network.trim();
  if (d.vantage.trim()) out.vantage = d.vantage.trim();
  const w = num(d.window);
  if (w !== undefined) out.window = w;
  const s = num(d.subbuckets);
  if (s !== undefined) out.subbuckets = s;
  const sensors = d.sensors.split(",").map((x) => x.trim()).filter(Boolean);
  if (sensors.length) out.sensors = sensors;
  return out;
}

/**
 * The write body. `editing` marks an update of a stored source: an EMPTY
 * secret field is then left out (the stored value is kept), because the
 * screen never shows the stored secret and an operator editing the name
 * must not wipe the password by saving.
 */
export function toWrite(form: SourceForm, editing = false): SourceWrite {
  const auth = effectiveAuth(form);
  const body: SourceWrite = {
    name: form.name.trim(),
    plugin: form.plugin,
    endpoint: form.endpoint.trim(),
    access: {
      transport: form.access === "ssh_tunnel" ? "ssh_tunnel" : "direct",
      auth,
      user: auth === "basic" ? form.user.trim() : null,
      ssh:
        form.access === "ssh_tunnel"
          ? {
              host: form.ssh.host.trim(),
              user: form.ssh.user.trim(),
              key_path: form.ssh.keyPath.trim(),
              port: num(form.ssh.port) ?? 22,
            }
          : null,
    },
    defaults: defaultsFromForm(form.defaults),
  };
  if (auth === "basic" && (form.password || !editing)) body.password = form.password;
  if (auth === "api_key" && (form.apiKey || !editing)) body.api_key = form.apiKey;
  return body;
}

/** What the screen can tell before asking the server. The server's own
 *  refusal is still shown verbatim when it disagrees (C1.3). */
export function validateForm(form: SourceForm, editing = false): string[] {
  const problems: string[] = [];
  if (!form.name.trim()) problems.push("a name is required");
  if (!form.plugin) problems.push("choose a plugin");
  if (!/^https?:\/\/[^/\s]+/.test(form.endpoint.trim())) {
    problems.push("the endpoint must be an absolute http(s) URL, e.g. http://127.0.0.1:9090");
  }
  const auth = effectiveAuth(form);
  if (auth === "basic") {
    if (!form.user.trim()) problems.push("basic auth needs a user");
    if (!form.password && !editing) problems.push("basic auth needs a password");
  }
  if (auth === "api_key" && !form.apiKey && !editing) problems.push("an API key is required");
  if (form.access === "ssh_tunnel") {
    if (!form.ssh.host.trim()) problems.push("the SSH host is required");
    if (!form.ssh.user.trim()) problems.push("the SSH user is required");
    if (!form.ssh.keyPath.trim()) problems.push("the SSH key path (on the server) is required");
    const port = num(form.ssh.port);
    if (port === undefined || port < 1 || port > 65535) problems.push("the SSH port must be 1..65535");
  }
  const days = num(form.defaults.days);
  if (form.defaults.days.trim() && (days === undefined || days <= 0)) problems.push("days must be a positive number");
  return problems;
}

/** Plugins a stored source can be built for: the ones addressed by a URL. */
export function endpointPlugins(plugins: DiscoveryPlugin[]): DiscoveryPlugin[] {
  return plugins.filter((p) => p.source_kind === "http");
}

export interface RunOption {
  key: string;
  type: string;
  description: string;
}

/** The run options a plugin's own schema declares, minus `source` (the stored
 *  endpoint) and the time bounds (the window picker owns those). C1.1: the
 *  run form is drawn from this, not from a list in the UI. */
export function runOptions(plugin: DiscoveryPlugin | undefined): RunOption[] {
  const props: Record<string, JsonSchemaProperty> = plugin?.config_schema.properties ?? {};
  return Object.entries(props)
    .filter(([k]) => !["source", "ts_from", "ts_to"].includes(k))
    .map(([key, spec]) => ({ key, type: spec.type ?? "string", description: spec.description ?? "" }));
}

export function sourceHelp(plugin: DiscoveryPlugin | undefined): string {
  return plugin?.config_schema.properties?.source?.description ?? "";
}

/** `<slug>-<YYYYMMDD>`: a catalog id that is safe and says where it came from. */
export function defaultImprintId(sourceName: string, now: Date): string {
  const slug = sourceName.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "") || "discovery";
  const y = now.getUTCFullYear();
  const m = String(now.getUTCMonth() + 1).padStart(2, "0");
  const d = String(now.getUTCDate()).padStart(2, "0");
  return `${slug}-${y}${m}${d}`;
}

/** The bounds "the last N days" will resolve to, from the last probe --
 *  the same arithmetic the server applies when the run starts. */
export function windowPreview(probe: Probe | null | undefined, days: number): { from: number; to: number } | null {
  if (!probe || probe.last_ts === null || !(days > 0)) return null;
  const to = probe.last_ts + 1;
  let from = to - days * 86400;
  if (probe.first_ts !== null && from < probe.first_ts) from = probe.first_ts;
  return { from, to };
}

export function formatTs(epoch: number | null | undefined): string {
  if (epoch === null || epoch === undefined || !Number.isFinite(epoch)) return "unknown";
  return new Date(epoch * 1000).toISOString().replace("T", " ").replace(/\.\d+Z$/, " UTC");
}

export function formatIso(iso: string | null | undefined): string {
  if (!iso) return "unknown";
  const t = Date.parse(iso);
  return Number.isFinite(t) ? formatTs(t / 1000) : iso;
}

export function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "unknown";
  const s = Math.round(seconds);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${s % 60}s`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}

export function formatCount(n: unknown): string {
  return typeof n === "number" && Number.isFinite(n) ? n.toLocaleString("en-US") : "unknown";
}

/** One line for how a source is reached, never a secret. */
export function describeAccess(src: DiscoverySource): string {
  const auth =
    src.access.auth === "basic"
      ? `basic auth as ${src.access.user ?? "?"}`
      : src.access.auth === "api_key"
        ? "API key"
        : "no credential";
  if (src.access.transport === "ssh_tunnel" && src.access.ssh) {
    const s = src.access.ssh;
    return `SSH tunnel via ${s.user}@${s.host}:${s.port}, ${auth}`;
  }
  return `direct, ${auth}`;
}
