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
  toWrite,
  validateForm,
  windowPreview,
} from "../src/model/sources";
import { AXQUERY, FOO_HTTP, SPAN, ZEEK, tunnelSource } from "./helpers/fakeApi";

describe("the source form <-> the API record", () => {
  it("round-trips a tunnel source without inventing a secret", () => {
    const form = fromSource(tunnelSource());
    expect(form.access).toBe("ssh_tunnel");
    expect(form.ssh).toEqual({ host: "corp-apcn-reader.swlab.axellio.dom", port: "22", user: "fxadmin", keyPath: "/home/op/.ssh/id_ed25519" });
    expect(form.defaults.days).toBe("10");
    const body = toWrite(form, true);
    expect(body.access).toEqual({
      transport: "ssh_tunnel",
      auth: "none",
      user: null,
      ssh: { host: "corp-apcn-reader.swlab.axellio.dom", user: "fxadmin", key_path: "/home/op/.ssh/id_ed25519", port: 22 },
    });
    expect(body).not.toHaveProperty("password");
    expect(body).not.toHaveProperty("api_key");
    expect(body.defaults).toEqual({ days: 10, network: "corp-apcn" });
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
