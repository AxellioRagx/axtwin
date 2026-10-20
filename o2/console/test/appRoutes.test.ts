/**
 * The app's URL grammar (`src/model/appRoutes.ts`) and the legacy Design
 * redirect (`zoom.ts`'s `legacyDesignRedirect`): round trips, canonical
 * queries, and every old `/imprints/...` spelling landing on `/design/...`.
 */
import { describe, expect, it } from "vitest";

import {
  catalogPath,
  hostPath,
  parseAppPath,
  parseTwinQuery,
  personasPath,
  twinPath,
  twinQueryString,
} from "../src/model/appRoutes";
import { legacyDesignRedirect, parseRoute, routeFor, zoomToPath } from "../src/model/zoom";

describe("parseAppPath", () => {
  it("names the area for every top-level page", () => {
    expect(parseAppPath("/")).toEqual({ area: "range" });
    expect(parseAppPath("/range")).toEqual({ area: "range" });
    expect(parseAppPath("/telemetry")).toEqual({ area: "telemetry" });
    expect(parseAppPath("/catalog")).toEqual({ area: "catalog", kind: null, id: null });
    expect(parseAppPath("/catalog/twins/corp-twin")).toEqual({ area: "catalog", kind: "twins", id: "corp-twin" });
    expect(parseAppPath("/design")).toEqual({ area: "design" });
    expect(parseAppPath("/design/x/segments/10.80.20.0-24")).toEqual({ area: "design" });
    expect(parseAppPath("/design/%zz")).toEqual({ area: "design" }); // Design reports it in context
  });

  it("parses the Twin page, its host panel and its persona editor", () => {
    expect(parseAppPath("/twin/corp-twin-07cfcdb2")).toEqual({ area: "twin", deployId: "corp-twin-07cfcdb2", host: null, personas: false });
    expect(parseAppPath("/twin/t/hosts/hq-mx")).toEqual({ area: "twin", deployId: "t", host: "hq-mx", personas: false });
    expect(parseAppPath("/twin/t/hosts/hq-mx/personas")).toEqual({ area: "twin", deployId: "t", host: "hq-mx", personas: true });
  });

  it("refuses what it does not know rather than guessing", () => {
    for (const bad of ["/twin", "/twin/t/hosts", "/twin/t/nope", "/twin/t/hosts/h/x", "/catalog/other", "/range/x", "/elsewhere", "/twin/%zz"]) {
      expect(parseAppPath(bad), bad).toEqual({ area: "unknown" });
    }
  });

  it("round-trips the builders", () => {
    const q = {
      target: "pxtwin1",
      view: "hosts" as const,
      segment: "vlan20",
      q: "ws 10.80",
      personas: "with" as const,
      persona: "cast-file-server",
      sort: "ip" as const,
      dir: "desc" as const,
    };
    for (const [path, want] of [
      [twinPath("a b/c", q), { area: "twin", deployId: "a b/c", host: null, personas: false }],
      [hostPath("t", "host 1", q), { area: "twin", deployId: "t", host: "host 1", personas: false }],
      [personasPath("t", "h", q), { area: "twin", deployId: "t", host: "h", personas: true }],
      [catalogPath("scenarios", "s/1"), { area: "catalog", kind: "scenarios", id: "s/1" }],
    ] as const) {
      const at = path.indexOf("?");
      expect(parseAppPath(at < 0 ? path : path.slice(0, at)), path).toEqual(want);
      if (path.startsWith("/twin")) expect(parseTwinQuery(path.slice(at))).toEqual(q);
    }
  });
});

describe("the Twin page query", () => {
  it("is canonical: defaults omitted, fixed order", () => {
    expect(twinQueryString({})).toBe("");
    expect(twinQueryString({ sort: "host", dir: "asc", target: null })).toBe("");
    expect(twinQueryString({ dir: "desc", segment: "vlan20", target: "dev45" })).toBe("?target=dev45&segment=vlan20&dir=desc");
    expect(twinQueryString({ view: "map", personas: "any", persona: null })).toBe("");
    expect(twinQueryString({ view: "hosts", personas: "without" })).toBe("?view=hosts&personas=without");
    expect(parseTwinQuery("?view=bogus&personas=bogus").view).toBe("map");
    expect(parseTwinQuery("?personas=bogus").personas).toBe("any");
  });

  it("an unknown sort key falls back to the host column", () => {
    expect(parseTwinQuery("?sort=nonsense").sort).toBe("host");
  });
});

describe("old Design URLs redirect to /design", () => {
  it("re-roots every /imprints spelling and keeps the query", () => {
    expect(legacyDesignRedirect("/imprints")).toBe("/design");
    expect(legacyDesignRedirect("/imprints/")).toBe("/design");
    expect(legacyDesignRedirect("/imprints/corpapcn-golden", "?summary=1")).toBe("/design/corpapcn-golden?summary=1");
    expect(legacyDesignRedirect("/imprints/x/segments/10.80.20.0-24/hosts/address:10.80.20.5")).toBe(
      "/design/x/segments/10.80.20.0-24/hosts/address:10.80.20.5",
    );
    expect(parseAppPath("/imprints/x/matrix", "?ghosts=1")).toEqual({ area: "redirect", to: "/design/x/matrix?ghosts=1" });
  });

  it("leaves every other path alone -- including one that merely starts with the word", () => {
    expect(legacyDesignRedirect("/imprintsx")).toBeNull();
    expect(legacyDesignRedirect("/design/imprints")).toBeNull();
    expect(legacyDesignRedirect("/api/imprints/x")).toBeNull();
  });

  it("the redirected URL parses to the same zoom the old one named", () => {
    const zoom = { level: "host" as const, imprint: "x", key: "hostname:a", cidr: "10.80.20.0/24" };
    const old = zoomToPath(zoom).replace(/^\/design/, "/imprints");
    expect(parseRoute(legacyDesignRedirect(old)!).zoom).toEqual(zoom);
    expect(routeFor(zoom)).toBe(legacyDesignRedirect(old));
  });
});
