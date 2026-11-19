import { describe, expect, it } from "vitest";

import {
  defaultImprintId,
  describeAccess,
  emptyForm,
  endpointPlugins,
  formatBig,
  fromSource,
  runOptions,
  siteRows,
  lastRunOf,
  prefillHint,
  runPrefill,
  toWrite,
  validateForm,
  windowPreview,
} from "../src/model/sources";
import type { DiscoveryRun } from "../src/api/types";
import { AXQUERY, FOO_HTTP, SPAN, ZEEK, tunnelSource } from "./helpers/fakeApi";

describe("the source form <-> the API record", () => {
  it("round-trips a tunnel source without inventing a secret", () => {
    const form = fromSource(tunnelSource());
    expect(form.access).toBe("ssh_tunnel");
    expect(form.ssh).toEqual({ host: "corp-apcn-reader.swlab.axellio.dom", port: "22", user: "fxadmin", keyPath: "/home/op/.ssh/id_ed25519" });
    const body = toWrite(form, true);
    expect(body.access).toEqual({
      transport: "ssh_tunnel",
      auth: "none",
      user: null,
      ssh: { host: "corp-apcn-reader.swlab.axellio.dom", user: "fxadmin", key_path: "/home/op/.ssh/id_ed25519", port: 22 },
    });
    expect(body).not.toHaveProperty("password");
    expect(body).not.toHaveProperty("api_key");
    // A source carries no run settings: the legacy stored defaults are never sent back.
    expect(body).not.toHaveProperty("defaults");
  });

  it("maps the single access choice onto transport x auth", () => {
    const f = { ...emptyForm("axquery"), name: "s", endpoint: "https://x:1", access: "basic" as const, user: "ro", password: "pw" };
    expect(toWrite(f).access).toMatchObject({ transport: "direct", auth: "basic", user: "ro" });
    expect(toWrite(f).password).toBe("pw");
    const k = { ...f, access: "api_key" as const, apiKey: "K" };
    expect(toWrite(k)).toMatchObject({ access: { transport: "direct", auth: "api_key", user: null }, api_key: "K" });
    const t = { ...f, access: "ssh_tunnel" as const, tunnelAuth: "basic" as const, ssh: { host: "h", port: "2222", user: "u", keyPath: "/k" } };
    expect(toWrite(t)).toMatchObject({ access: { transport: "ssh_tunnel", auth: "basic", user: "ro", ssh: { port: 2222 } }, password: "pw" });
  });

  it("on edit an empty secret is left out so the stored one is kept", () => {
    const f = { ...emptyForm("axquery"), name: "s", endpoint: "https://x:1", access: "api_key" as const, apiKey: "" };
    expect(toWrite(f, true)).not.toHaveProperty("api_key");
    expect(toWrite({ ...f, apiKey: "new" }, true).api_key).toBe("new");
  });

  it("validates what the screen can know, by name", () => {
    const f = emptyForm("");
    expect(validateForm(f)).toEqual([
      "a name is required",
      "choose a plugin",
      "the endpoint must be an absolute http(s) URL, e.g. http://127.0.0.1:9090",
    ]);
    const bare = { ...emptyForm("axquery"), name: "n", endpoint: "10.0.0.1:9090" };
    expect(validateForm(bare)).toEqual(["the endpoint must be an absolute http(s) URL, e.g. http://127.0.0.1:9090"]);
    const tunnel = { ...emptyForm("axquery"), name: "n", endpoint: "http://127.0.0.1:9090", access: "ssh_tunnel" as const };
    expect(validateForm(tunnel)).toEqual([
      "the SSH host is required",
      "the SSH user is required",
      "the SSH key path (on the server) is required",
    ]);
    const basic = { ...emptyForm("axquery"), name: "n", endpoint: "http://h:1", access: "basic" as const };
    expect(validateForm(basic)).toEqual(["basic auth needs a user", "basic auth needs a password"]);
    expect(validateForm(basic, true)).toEqual(["basic auth needs a user"]);
  });
});

describe("what the plugin schema drives", () => {
  it("only http plugins can back a stored source", () => {
    expect(endpointPlugins([AXQUERY, ZEEK, FOO_HTTP]).map((p) => p.name)).toEqual(["axquery", "foo-http"]);
  });
  it("run options are the schema minus source and the bounds", () => {
    expect(runOptions(AXQUERY).map((o) => o.key)).toEqual(["vantage", "network", "window", "subbuckets"]);
    expect(runOptions(FOO_HTTP)).toEqual([{ key: "shard", type: "integer", description: "which shard to read" }]);
    expect(runOptions(undefined)).toEqual([]);
  });
});

describe("the window", () => {
  it("previews the last N days from the probe, clamped to the span", () => {
    const probe = { reachable: true, error: null, ...SPAN, vantage: null, extra: {}, via: null };
    expect(windowPreview(probe, 10)).toEqual({ from: SPAN.last_ts + 1 - 10 * 86400, to: SPAN.last_ts + 1 });
    expect(windowPreview(probe, 365)).toEqual({ from: SPAN.first_ts, to: SPAN.last_ts + 1 });
    expect(windowPreview(null, 10)).toBeNull();
    expect(windowPreview(probe, 0)).toBeNull();
  });
  it("names the imprint after the source and the day", () => {
    expect(defaultImprintId("Corp APCN reader", new Date("2026-10-27T16:00:00Z"))).toBe("corp-apcn-reader-20261027");
    expect(defaultImprintId("", new Date("2026-10-27T16:00:00Z"))).toBe("discovery-20261027");
  });
  it("reads a census figure at three significant digits", () => {
    expect(formatBig(16893421675)).toBe("16.9 G");
    expect(formatBig(15494524704945, "B")).toBe("15.5 TB");
    expect(formatBig(2558306)).toBe("2.56 M");
    expect(formatBig(69)).toBe("69");
    expect(formatBig(null)).toBe("unknown");
  });
  it("turns the sensor's sites into rows and skips what is not that shape", () => {
    expect(siteRows([{ location: "axellio-hq", systems: [{ name: "corp-apcn-rdr1", chunks: 69, first_ts: 1, last_ts: 2, streams: 12 }] }])).toEqual([
      { location: "axellio-hq", system: "corp-apcn-rdr1", streams: 12, chunks: 69, first_ts: 1, last_ts: 2 },
    ]);
    expect(siteRows("nope")).toEqual([]);
    expect(siteRows([{ location: "x" }, 3, { systems: [null, { name: "s" }] }])).toEqual([
      { location: "?", system: "s", streams: null, chunks: null, first_ts: null, last_ts: null },
    ]);
  });
  it("describes access without a secret", () => {
    expect(describeAccess(tunnelSource())).toBe("SSH tunnel via fxadmin@corp-apcn-reader.swlab.axellio.dom:22, no credential");
    expect(describeAccess(tunnelSource({ access: { transport: "direct", auth: "basic", user: "ro", ssh: null } }))).toBe(
      "direct, basic auth as ro, no password stored",
    );
    expect(
      describeAccess(tunnelSource({ access: { transport: "direct", auth: "basic", user: "ro", ssh: null }, has_password: true })),
    ).toBe("direct, basic auth as ro, password stored");
  });
});

describe("probe steps and the test key", () => {
  it("formats the step trail and tolerates a probe recorded before steps existed", async () => {
    const { formatSteps } = await import("../src/model/sources");
    expect(formatSteps([{ stage: "api_http", ok: true }, { stage: "plugin_census", ok: false }])).toBe("API ✓ · census ✗");
    expect(formatSteps(undefined)).toBe("");
  });

  it("the key follows plugin, endpoint and access, not the name", async () => {
    const { emptyForm, probeKey } = await import("../src/model/sources");
    const f = { ...emptyForm("axquery"), endpoint: "http://127.0.0.1:9090" };
    const k = probeKey(f);
    expect(probeKey({ ...f, name: "renamed" })).toBe(k);
    expect(probeKey({ ...f, plugin: "elastic" })).not.toBe(k);
    expect(probeKey({ ...f, access: "api_key", apiKey: "k" })).not.toBe(k);
    expect(probeKey({ ...f, ssh: { ...f.ssh, host: "h" } })).toBe(k); // ssh unused without a tunnel
    expect(probeKey({ ...f, access: "ssh_tunnel" })).not.toBe(k);
  });
});

describe("the run form's pre-fill: last run, then legacy source defaults, then built-ins", () => {
  const run = (over: Partial<DiscoveryRun> = {}): DiscoveryRun => ({
    id: "disc-8d560f14", status: "completed", plugin: "axquery", name: "n", imprint_id: "n",
    stages_done: [], stages: [], pit_id: null, error: null, created_at: "2026-11-19T10:00:00Z",
    finished_at: null, source_id: "src-1", started_at: null, ts_from: 1791311437, ts_to: 1791315037,
    via: null, progress_ts: null, progress_at: null, cancel_requested_at: null,
    options: { network: "corp-apcn", window: 120 }, ...over,
  });

  it("takes the window length and options from the last run, and names it", () => {
    const p = runPrefill(tunnelSource({ defaults: { days: 3, network: "legacy", vantage: "v:legacy" } }), run());
    expect(p.from).toEqual({ kind: "run", runId: "disc-8d560f14" });
    expect([p.amount, p.unit]).toEqual(["1", "hours"]);
    // The run's options win; what it did not set falls through to the legacy defaults.
    expect(p.values).toEqual({ network: "corp-apcn", window: "120", vantage: "v:legacy" });
    expect(prefillHint(p.from)).toBe("Filled from run disc-8d560f14, the last one from this source.");
  });

  it("reads a whole-day window as days", () => {
    const p = runPrefill(tunnelSource({ defaults: {} }), run({ ts_from: 0, ts_to: 3 * 86400 }));
    expect([p.amount, p.unit]).toEqual(["3", "days"]);
  });

  it("falls back to the source's legacy defaults, sensors included", () => {
    const p = runPrefill(tunnelSource({ defaults: { days: 2, network: "corp-apcn", sensors: ["XPress-SW-A"] } }), undefined);
    expect(p.from).toEqual({ kind: "source" });
    expect([p.amount, p.unit, p.values.network, p.sensors]).toEqual(["2", "days", "corp-apcn", "XPress-SW-A"]);
  });

  it("falls back to the built-ins: the last 10 days, nothing else", () => {
    const p = runPrefill(tunnelSource({ defaults: {} }), undefined);
    expect(p).toEqual({ amount: "10", unit: "days", values: {}, sensors: "", from: { kind: "builtin" } });
    expect(prefillHint(p.from)).toBe("Built-in defaults: the last 10 days.");
  });

  it("what the last test reported sits below the run and the source, above nothing", () => {
    const extras = { vantage: "v:probe", window: "60" };
    expect(runPrefill(tunnelSource({ defaults: {} }), undefined, extras).values).toEqual(extras);
    expect(runPrefill(tunnelSource({ defaults: {} }), run(), extras).values).toEqual({ network: "corp-apcn", window: "120", vantage: "v:probe" });
  });

  it("the last run is the newest from THIS source, whatever its status", () => {
    const runs = [
      run({ id: "a", created_at: "2026-11-18T00:00:00Z" }),
      run({ id: "b", created_at: "2026-11-19T00:00:00Z", status: "failed" }),
      run({ id: "c", created_at: "2026-11-20T00:00:00Z", source_id: "src-other" }),
    ];
    expect(lastRunOf(runs, "src-1")?.id).toBe("b");
    expect(lastRunOf(undefined, "src-1")).toBeUndefined();
  });
});

describe("a failed probe in plain words", () => {
  it("classifies the common failures and falls back to the first line", async () => {
    const { probeFailure } = await import("../src/model/sources");
    const f = (error: string, stage: "api_http" | "ssh_auth" | "ssh_connect" = "api_http") => probeFailure({ reachable: false, error, stage });
    expect(f("unreachable at https://h.example/api: HTTP Error 401: Unauthorized")).toEqual({ step: "API", reason: "rejected the credential (HTTP 401) at h.example" });
    expect(f("ssh fxadmin@rdr1: Permission denied (publickey).", "ssh_auth")).toEqual({ step: "login", reason: "SSH login refused (permission denied) at rdr1" });
    expect(f("Host key verification failed.", "ssh_connect")?.reason).toBe("SSH host key not trusted (not in known_hosts)");
    expect(f("unreachable at http://127.0.0.1:9090: [Errno 111] Connection refused")?.reason).toBe("connection refused at 127.0.0.1");
    expect(f("timed out after 60s at api_http")?.reason).toBe("timed out");
    expect(f("something odd\nsecond line")?.reason).toBe("something odd");
    expect(probeFailure({ reachable: true, error: null })).toBeNull();
  });
});
