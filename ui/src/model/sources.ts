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
  DiscoveryRun,
  DiscoverySource,
  JsonSchemaProperty,
  Probe,
  ProbeStage,
  ProbeStep,
  SourceWrite,
} from "../api/types";
import { formatInstant } from "./time";

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
  /** Verify the endpoint's TLS certificate (https only). Off for a sensor's self-signed one. */
  tlsVerify: boolean;
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
    tlsVerify: true,
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
  f.tlsVerify = src.access.tls_verify !== false;
  return f;
}

function num(text: string): number | undefined {
  const t = text.trim();
  if (!t) return undefined;
  const n = Number(t);
  return Number.isFinite(n) ? n : undefined;
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
    // No `defaults`: a source is the sensor and how to reach it. Run settings live on the
    // run form (`runPrefill`); a PUT without the field keeps any legacy stored value.
  };
  // Sent only when off: an absent field verifies, so a PUT without it turns it back on.
  if (!form.tlsVerify) body.access.tls_verify = false;
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

/** Epoch seconds in the console's one timestamp style (`model/time.ts`); `unknown` when absent. */
export function formatTs(epoch: number | null | undefined): string {
  if (epoch === null || epoch === undefined || !Number.isFinite(epoch)) return "unknown";
  return formatInstant(epoch, "unknown");
}

/** An ISO string in the same style; an unparseable one is shown as it came. */
export function formatIso(iso: string | null | undefined): string {
  if (!iso) return "unknown";
  return formatInstant(iso, iso);
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

/** A census figure at three significant digits with an SI prefix: 16.9 G, 15.5 TB. */
export function formatBig(n: unknown, unit = ""): string {
  if (typeof n !== "number" || !Number.isFinite(n)) return "unknown";
  const steps: Array<[number, string]> = [
    [1e12, "T"],
    [1e9, "G"],
    [1e6, "M"],
    [1e3, "k"],
  ];
  for (const [value, prefix] of steps) {
    if (Math.abs(n) >= value) return `${Number((n / value).toPrecision(3))} ${prefix}${unit}`;
  }
  return unit ? `${n.toLocaleString("en-US")} ${unit}` : n.toLocaleString("en-US");
}

export interface SiteRow {
  location: string;
  system: string;
  streams: number | null;
  chunks: number | null;
  first_ts: number | null;
  last_ts: number | null;
}

/** The probe's `extra.sites` (axquery's `/api/info` shape) as one row per system; anything
 *  that is not that shape is skipped rather than rendered as JSON. */
export function siteRows(sites: unknown): SiteRow[] {
  if (!Array.isArray(sites)) return [];
  const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
  const rows: SiteRow[] = [];
  for (const site of sites) {
    if (!site || typeof site !== "object") continue;
    const s = site as { location?: unknown; systems?: unknown };
    const location = typeof s.location === "string" ? s.location : "?";
    const systems = Array.isArray(s.systems) ? s.systems : [];
    for (const sys of systems) {
      if (!sys || typeof sys !== "object") continue;
      const y = sys as Record<string, unknown>;
      rows.push({
        location,
        system: typeof y.name === "string" ? y.name : "?",
        streams: num(y.streams),
        chunks: num(y.chunks),
        first_ts: num(y.first_ts),
        last_ts: num(y.last_ts),
      });
    }
  }
  return rows;
}

/** One line for how a source is reached, never a secret. */
export function describeAccess(src: DiscoverySource): string {
  const auth =
    src.access.auth === "basic"
      ? `basic auth as ${src.access.user ?? "?"}, ${src.has_password ? "password stored" : "no password stored"}`
      : src.access.auth === "api_key"
        ? src.has_api_key
          ? "API key stored"
          : "API key not stored"
        : "no credential";
  const tls = src.access.tls_verify === false ? ", TLS certificate not verified" : "";
  if (src.access.transport === "ssh_tunnel" && src.access.ssh) {
    const s = src.access.ssh;
    return `SSH tunnel via ${s.user}@${s.host}:${s.port}, ${auth}${tls}`;
  }
  return `direct, ${auth}${tls}`;
}

export interface RunProgress {
  /** 0..1 of the window the ingest has covered; 1 once ingest is done. */
  fraction: number;
  /** Source time reached, or null before the first window closed. */
  reached: number | null;
  /** Seconds the rest should take at the rate so far; null until there is a rate. */
  remainingS: number | null;
  ingestDone: boolean;
}

/** Where a run is in its window, from what the record says and the wall clock. */
export function runProgress(run: DiscoveryRun, nowMs: number): RunProgress | null {
  if (run.ts_from === null || run.ts_to === null || run.ts_to <= run.ts_from) return null;
  const ingestDone = run.stages_done.includes("ingest");
  const span = run.ts_to - run.ts_from;
  if (ingestDone) return { fraction: 1, reached: run.ts_to, remainingS: null, ingestDone };
  // `== null`: an API built before progress existed answers without the field at all.
  const reached = run.progress_ts == null ? null : Math.min(Math.max(run.progress_ts, run.ts_from), run.ts_to);
  const covered = reached === null ? 0 : reached - run.ts_from;
  const startedMs = run.started_at ? Date.parse(run.started_at) : NaN;
  const elapsedS = Number.isFinite(startedMs) ? (nowMs - startedMs) / 1000 : NaN;
  const remainingS = covered > 0 && elapsedS > 0 ? ((span - covered) / covered) * elapsedS : null;
  return { fraction: covered / span, reached, remainingS, ingestDone };
}

/** How a probe step reads in the one-line trail ("SSH ✓ · login ✓ · tunnel ✓ · API ✗"). */
export const STAGE_LABELS: Record<ProbeStage, string> = {
  ssh_connect: "SSH",
  ssh_auth: "login",
  ssh_forward: "tunnel",
  api_http: "API",
  plugin_census: "census",
};

/** The steps a probe walked, as one line; "" for a probe recorded before steps existed. */
export function formatSteps(steps: ProbeStep[] | undefined): string {
  return (steps ?? []).map((s) => `${STAGE_LABELS[s.stage] ?? s.stage} ${s.ok ? "✓" : "✗"}`).join(" · ");
}

/**
 * A failed probe in plain words: which step failed ("API") and why ("TLS: certificate not
 * trusted (self-signed) at 10.80.72.40"). Classified client-side from `stage` and the error
 * text, so the response shape stays as it is; the raw error stays available for a details
 * toggle. `null` for a reachable probe.
 */
export function probeFailure(probe: Pick<Probe, "reachable" | "error" | "stage" | "steps">): { step: string | null; reason: string } | null {
  if (probe.reachable) return null;
  const err = probe.error ?? "";
  const failed = probe.stage ?? (probe.steps ?? []).find((s) => !s.ok)?.stage ?? null;
  const step = failed ? (STAGE_LABELS[failed] ?? failed) : null;
  const host = hostIn(err);
  const at = host ? ` at ${host}` : "";
  const status = /\bHTTP (?:Error )?(\d{3})\b/i.exec(err)?.[1] ?? /\b(40[13])\b/.exec(err)?.[1];
  let reason: string;
  if (/CERTIFICATE_VERIFY_FAILED|self[- ]signed|certificate verify failed/i.test(err)) {
    reason = /self[- ]signed/i.test(err) ? `TLS: certificate not trusted (self-signed)${at}` : `TLS: certificate not trusted${at}`;
  } else if (/host key verification failed|REMOTE HOST IDENTIFICATION/i.test(err)) {
    reason = `SSH host key not trusted (not in known_hosts)${at}`;
  } else if (/permission denied/i.test(err)) {
    reason = `SSH login refused (permission denied)${at}`;
  } else if (status === "401" || status === "403") {
    reason = `rejected the credential (HTTP ${status})${at}`;
  } else if (/timed out|timeout/i.test(err)) {
    reason = `timed out${at}`;
  } else if (/connection refused/i.test(err)) {
    reason = `connection refused${at}`;
  } else if (/name or service not known|nodename nor servname|could not resolve|getaddrinfo|Name does not resolve/i.test(err)) {
    reason = `host name not found${at}`;
  } else {
    reason = err.split("\n")[0]?.trim() || "no reason given";
  }
  return { step, reason };
}

/** The first host a probe error names: from a URL, else from `user@host`. */
function hostIn(text: string): string | null {
  const url = /\bhttps?:\/\/([^/:\s]+)/i.exec(text);
  if (url) return url[1] ?? null;
  const ssh = /\b[\w.-]+@([\w.-]+)/.exec(text);
  return ssh ? (ssh[1] ?? null) : null;
}

/** The part of a form a connection test depends on: plugin, endpoint and every access
 *  field, secrets included. The name is left out -- it does not change reachability. */
export function probeKey(form: SourceForm): string {
  const auth = effectiveAuth(form);
  return JSON.stringify({
    plugin: form.plugin,
    endpoint: form.endpoint.trim(),
    access: form.access,
    auth,
    user: auth === "basic" ? form.user.trim() : "",
    password: auth === "basic" ? form.password : "",
    apiKey: auth === "api_key" ? form.apiKey : "",
    ssh: form.access === "ssh_tunnel" ? form.ssh : null,
    tlsVerify: form.tlsVerify,
  });
}

/** The run form's built-in window when nothing else says: the last 10 days. */
export const BUILTIN_RUN_DAYS = 10;

export interface RunPrefill {
  /** The window as "last <amount> <unit>". */
  amount: string;
  unit: "days" | "hours";
  /** Run options by key (network, vantage, window, subbuckets). */
  values: Record<string, string>;
  sensors: string;
  /** Where the values came from, for the hint: the most specific tier that supplied any. */
  from: { kind: "run"; runId: string } | { kind: "source" } | { kind: "builtin" };
}

const RUN_OPTION_KEYS = ["network", "vantage", "window", "subbuckets"] as const;

/** A window length in seconds as "last N days" (whole-ish days) or "last N hours". */
function windowAmount(seconds: number): { amount: string; unit: "days" | "hours" } {
  const days = seconds / 86400;
  const tidy = (n: number) => String(Number(n.toFixed(2)));
  if (days >= 1 && Math.abs(days - Math.round(days)) < 0.01) return { amount: tidy(Math.round(days)), unit: "days" };
  return { amount: tidy(seconds / 3600), unit: "hours" };
}

/** The source's most recent run (any status: what the operator last chose is the point). */
export function lastRunOf(runs: DiscoveryRun[] | undefined, sourceId: string): DiscoveryRun | undefined {
  return (runs ?? [])
    .filter((r) => r.source_id === sourceId)
    .reduce<DiscoveryRun | undefined>((best, r) => (!best || r.created_at > best.created_at ? r : best), undefined);
}

/**
 * What the run form starts with, field by field: (a) the source's most recent run -- its
 * window length and options -- then (b) the source's LEGACY stored defaults, then (c) the
 * built-ins (last 10 days). A run record carries no sensors (they are a cast-time choice), so
 * those come from (b) only. `probeExtras` (what the last test reported for vantage / window /
 * subbuckets) fill what (a) and (b) leave empty, below them.
 */
export function runPrefill(
  source: DiscoverySource,
  lastRun: DiscoveryRun | undefined,
  probeExtras: Record<string, string> = {},
): RunPrefill {
  const legacy = source.defaults ?? {};
  const opts = lastRun?.options ?? {};
  let usedRun = false;
  let usedSource = false;
  const values: Record<string, string> = {};
  for (const k of RUN_OPTION_KEYS) {
    const fromRun = opts[k];
    const fromSource = legacy[k];
    if (fromRun !== undefined && fromRun !== null && fromRun !== "") {
      values[k] = String(fromRun);
      usedRun = true;
    } else if (fromSource !== undefined && fromSource !== "") {
      values[k] = String(fromSource);
      usedSource = true;
    } else if (probeExtras[k] !== undefined) {
      values[k] = probeExtras[k];
    }
  }
  let amount = String(BUILTIN_RUN_DAYS);
  let unit: "days" | "hours" = "days";
  if (lastRun && lastRun.ts_from !== null && lastRun.ts_to !== null && lastRun.ts_to > lastRun.ts_from) {
    ({ amount, unit } = windowAmount(lastRun.ts_to - lastRun.ts_from));
    usedRun = true;
  } else if (typeof legacy.days === "number" && legacy.days > 0) {
    amount = String(legacy.days);
    usedSource = true;
  }
  const sensorList = legacy.sensors ?? [];
  if (sensorList.length) usedSource = true;
  const from: RunPrefill["from"] =
    usedRun && lastRun ? { kind: "run", runId: lastRun.id } : usedSource ? { kind: "source" } : { kind: "builtin" };
  return { amount, unit, values, sensors: sensorList.join(", "), from };
}

/** The hint under the run form naming where its starting values came from. */
export function prefillHint(from: RunPrefill["from"]): string {
  if (from.kind === "run") return `Filled from run ${from.runId}, the last one from this source.`;
  if (from.kind === "source") return "Filled from this source's saved run defaults (from before sources stopped carrying them).";
  return `Built-in defaults: the last ${BUILTIN_RUN_DAYS} days.`;
}
