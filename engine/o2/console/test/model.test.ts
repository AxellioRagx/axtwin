import { describe, expect, it } from "vitest";

import type { TopologyExport, TopologyExportNode } from "../src/api/types";
import { compareCidr, cidrToRoute, parseCidr, routeToCidr, siteKeyForCidr, twoOctetPrefix } from "../src/model/keys";
import { compareRole, INFRA_DEVICE_ROLES, orderRoles, roleBucket, roleRank } from "../src/model/roles";
import { bandCollapses, rungOf, tallyByRung } from "../src/model/budgets";
import { anyFilterActive, buildGraph, nodePassesFilters } from "../src/model/graph";
import { resolveSearch } from "../src/model/search";
import { evidenceClassName, saturationFor } from "../src/model/evidence";
import { zoomToPath } from "../src/model/zoom";
import type { Filters } from "../src/model/zoom";
import { trimmedCast, trimmedExport } from "./fixtures/trimmed";
import { groupIntoSites } from "../src/layout/sites";

const graph = buildGraph(trimmedExport, trimmedCast);

describe("CIDR ordering and encoding", () => {
  it("compares numerically, not lexically", () => {
    // The lexical trap: "10.20" < "10.5" as strings; numerically 10.5 < 10.20.
    const sorted = ["10.20.0.0/24", "10.5.0.0/24", "10.80.0.0/24"].sort(compareCidr);
    expect(sorted).toEqual(["10.5.0.0/24", "10.20.0.0/24", "10.80.0.0/24"]);
  });

  it("round-trips a CIDR through the hyphen route form", () => {
    expect(cidrToRoute("10.80.20.0/24")).toBe("10.80.20.0-24");
    expect(routeToCidr("10.80.20.0-24")).toBe("10.80.20.0/24");
  });

  it("parses and buckets by two octets for RFC 1918 /24s", () => {
    expect(parseCidr("10.80.20.0/24")).toEqual({ address: "10.80.20.0", prefix: 24 });
    expect(twoOctetPrefix("10.80.20.0/24")).toBe("10.80");
    expect(siteKeyForCidr("10.80.20.0/24")).toBe("site:10.80");
    // A non-/24 falls back to a pinnable hyphen-form singleton key.
    expect(siteKeyForCidr("203.0.113.0/25")).toBe("site:203.0.113.0-25");
  });
});

describe("role ordering (fixed order, `15` §4)", () => {
  it("infrastructure sorts before endpoints, unroled and external last", () => {
    const ordered = orderRoles([
      "external",
      "generic-endpoint",
      "corp-workstation",
      "unroled-endpoint",
      "dns-resolver",
      "domain-controller",
    ]);
    expect(ordered).toEqual([
      "domain-controller",
      "dns-resolver",
      "corp-workstation",
      "generic-endpoint",
      "unroled-endpoint",
      "external",
    ]);
  });

  it("an unknown role sorts into the infra/endpoint seam, before workstations", () => {
    expect(compareRole("ot-plc", "corp-workstation")).toBeLessThan(0);
    expect(compareRole("dns-resolver", "ot-plc")).toBeLessThan(0);
  });

  it("roleBucket sends a non-local node to external regardless of role", () => {
    expect(roleBucket("dns-resolver", "external")).toBe("external");
    expect(roleBucket(null, "local")).toBe("unroled-endpoint");
    expect(roleBucket("web-tier", "local")).toBe("web-tier");
    expect(INFRA_DEVICE_ROLES.has("dns-resolver")).toBe(true);
    expect(INFRA_DEVICE_ROLES.has("corp-workstation")).toBe(false);
  });

  it("roleRank places external after unroled after every named role", () => {
    expect(roleRank("external")).toBeGreaterThan(roleRank("unroled-endpoint"));
    expect(roleRank("unroled-endpoint")).toBeGreaterThan(roleRank("generic-endpoint"));
  });
});

describe("occupancy rungs (O6)", () => {
  it("classifies by 13 §3.4 thresholds; null is unknown, not transient", () => {
    expect(rungOf(0.9)).toBe("core");
    expect(rungOf(0.8)).toBe("core");
    expect(rungOf(0.6)).toBe("regular");
    expect(rungOf(0.2)).toBe("transient");
    expect(rungOf(null)).toBe("unknown");
  });

  it("tallies a band by rung in fixed order, dropping empty rungs", () => {
    expect(tallyByRung([1, 1, 0.6, 0.2, 0.2])).toEqual([
      { rung: "core", count: 2 },
      { rung: "regular", count: 1 },
      { rung: "transient", count: 2 },
    ]);
    expect(bandCollapses(16)).toBe(true);
    expect(bandCollapses(15)).toBe(false);
  });
});

describe("site grouping folds singletons into misc", () => {
  it("groups the trimmed twin into 10.20, 10.80 and a misc bucket", () => {
    const sites = groupIntoSites(graph.segments);
    expect(sites.map((s) => s.key)).toEqual(["site:10.20", "site:10.80", "site:misc"]);
    const misc = sites.find((s) => s.isMisc)!;
    // 10.1.10.0 and 192.168.0.0 are the two singleton /16s.
    expect(misc.segments.map((s) => s.observedCidr)).toEqual(["10.1.10.0/24", "192.168.0.0/24"]);
  });
});

describe("the graph joins export + cast", () => {
  it("computes ghosts from occupancy and the gate, and sorts segments by CIDR", () => {
    expect(graph.segments.map((s) => s.observedCidr)).toEqual([
      "10.1.10.0/24",
      "10.20.192.0/24",
      "10.20.193.0/24",
      "10.80.20.0/24",
      "10.80.72.0/24",
      "192.168.0.0/24",
    ]);
    const seg = graph.segmentByCidr.get("10.80.72.0/24")!;
    // 28 placed: 5 core generic-endpoint + 1 web-tier are live (windows_seen
    // 10); the 4 regular + 18 transient generic-endpoints are ghosts.
    expect(seg.hostCount).toBe(28);
    expect(seg.ghostCount).toBe(22);
    expect(seg.deployedCount).toBe(6);
  });

  it("re-points a conduit endpoint that resolves to no tile into a report", () => {
    expect(graph.conduits).toHaveLength(3);
    expect(graph.unresolvedConduitEndpoints).toEqual(["internet-sink"]);
  });

  it("carries the deployed CIDR as a declared transformation, keyed by name", () => {
    const seg = graph.segmentByName.get("seg-10-1-10-0")!;
    expect(seg.observedCidr).toBe("10.1.10.0/24");
    expect(seg.deployedCidr).toBe("10.200.0.0/24");
  });
});

describe("the CANONICAL id, never the export's baked imprint_id, drives navigation", () => {
  // The bug the goldens missed. `graph.imprintId` -- the id EVERY drill-down
  // route, re-fetch and search hop is built from -- was read from the topology
  // export's BAKED `imprint_id`, not the CANONICAL id the API is keyed on (the
  // directory name = `twin_config.id` = the page-URL id). The two AGREE in a
  // self-consistent fixture (why the golden/real-bundle tests stayed green) and
  // DISAGREE on a relocated/renamed twin: LIVE, `corpapcn-twin`'s export bakes
  // a `pit-*` id, so clicking a segment navigated to the pit id and 404'd. This
  // fixture forces that disagreement -- canonical != baked -- the same way
  // LES-262 forces a multi-class mix: a self-consistent fixture never runs the
  // path, so it must be chosen to break the two apart.
  const CANONICAL = "corpapcn-twin";
  const BAKED = "pit-63994c9543-1787823960";

  function mismatchedGraph() {
    const exportData: TopologyExport = {
      imprint_id: BAKED, // the export's own baked id -- must NOT drive routes
      pit_id: BAKED,
      as_of: null,
      network: null,
      vantage: null,
      windows: 10,
      schema_version: "1.4",
      segments: [
        { cidr: "10.0.0.0/24", name: "seg-10-0-0-0", entities: 1, coverage_source: null },
      ],
      nodes: [
        {
          id: "address:10.0.0.5",
          address: "10.0.0.5",
          locality: "local",
          segment: "10.0.0.0/24",
          segment_evidence_class: "observed",
          role: "generic-endpoint",
          role_confidence: 0.9,
          role_evidence_class: "observed",
          windows_seen: 10,
          listen_ports: [],
          dial_ports: [],
        },
      ],
      edges: [],
      census: { entities_total: 1, with_role: 1, placed: 1, local: 1 },
    };
    return buildGraph(exportData, { imprintId: CANONICAL, deployMinOccupancy: 0.8 });
  }

  it("sets graph.imprintId to the canonical id, not the baked export field", () => {
    const g = mismatchedGraph();
    expect(g.imprintId).toBe(CANONICAL);
    expect(g.imprintId).not.toBe(BAKED);
  });

  it("builds a drill route (the Level0 segment-tile click) that targets the canonical id", () => {
    const g = mismatchedGraph();
    // Verbatim the call `Level0`/`Level1` make off `graph.imprintId`.
    const route = zoomToPath({ level: "segment", imprint: g.imprintId, cidr: "10.0.0.0/24" });
    expect(route).toBe("/design/corpapcn-twin/segments/10.0.0.0-24");
    expect(route).not.toContain(BAKED);
  });

  it("resolves a search hit to the canonical id (the search.ts call-site)", () => {
    const g = mismatchedGraph();
    const hit = resolveSearch(g, "10.0.0.0/24");
    expect(hit.zoom).toMatchObject({ level: "segment", imprint: CANONICAL, cidr: "10.0.0.0/24" });
  });

  it("the self-consistent case (baked == canonical) is unchanged", () => {
    // The shared trimmed fixture: baked `trimmedExport.imprint_id` and the
    // canonical `trimmedCast.imprintId` are both "trimmed-fixture".
    expect(graph.imprintId).toBe("trimmed-fixture");
    expect(zoomToPath({ level: "network", imprint: graph.imprintId })).toBe("/design/trimmed-fixture");
  });
});

describe("filters emphasize a subset, never hide (`15` §7)", () => {
  const dc = graph.nodeById.get("hostname:axdc01")!;
  const ghost = graph.segmentByCidr.get("10.80.72.0/24")!.hosts.find((h) => h.isGhost)!;

  it("no filter is not active and everything passes", () => {
    const f: Filters = { ghosts: false, evidenceClass: null, occupancyLt: null, changed: false, summary: false, eastWestAll: false, tileOrder: "hosts", expand: [], problems: [] };
    expect(anyFilterActive(f)).toBe(false);
    expect(nodePassesFilters(dc, f)).toBe(true);
  });

  it("the `?summary=` VIEW flag is not a filter -- it emphasizes nothing (R1)", () => {
    // It rides in the same query and the same typed state, so the one thing
    // that must not happen is opening the summary bar dimming the whole map.
    const f: Filters = { ghosts: false, evidenceClass: null, occupancyLt: null, changed: false, summary: true, eastWestAll: false, tileOrder: "hosts", expand: [], problems: [] };
    expect(anyFilterActive(f)).toBe(false);
    expect(nodePassesFilters(dc, f)).toBe(true);
  });

  it("ghosts=1 passes only ghosts", () => {
    const f: Filters = { ghosts: true, evidenceClass: null, occupancyLt: null, changed: false, summary: false, eastWestAll: false, tileOrder: "hosts", expand: [], problems: [] };
    expect(nodePassesFilters(dc, f)).toBe(false);
    expect(nodePassesFilters(ghost, f)).toBe(true);
  });

  it("class matches either placement or role evidence class", () => {
    const f: Filters = { ghosts: false, evidenceClass: "imputed", occupancyLt: null, changed: false, summary: false, eastWestAll: false, tileOrder: "hosts", expand: [], problems: [] };
    // The DC's role class is imputed.
    expect(nodePassesFilters(dc, f)).toBe(true);
  });

  it("occupancy_lt emphasizes only known-occupancy nodes below the threshold", () => {
    const f: Filters = { ghosts: false, evidenceClass: null, occupancyLt: 0.8, changed: false, summary: false, eastWestAll: false, tileOrder: "hosts", expand: [], problems: [] };
    expect(anyFilterActive(f)).toBe(true);
    // The DC is fully present (occupancy 1.0) -- not below 0.8 -- so it is dimmed.
    expect(dc.occupancy).not.toBeNull();
    expect(nodePassesFilters(dc, f)).toBe(false);
    // A ghost sits below the deploy gate (occupancy < 0.8) -- so it is emphasized.
    expect(ghost.occupancy).not.toBeNull();
    expect(ghost.occupancy! < 0.8).toBe(true);
    expect(nodePassesFilters(ghost, f)).toBe(true);
  });

  it("occupancy_lt is a strict `<`: a node exactly at the threshold is dimmed", () => {
    const atThreshold: Filters = {
      ghosts: false,
      evidenceClass: null,
      occupancyLt: dc.occupancy!,
      changed: false,
      summary: false,
      eastWestAll: false,
      tileOrder: "hosts",
      expand: [],
      problems: [],
    };
    expect(nodePassesFilters(dc, atThreshold)).toBe(false);
  });

  it("occupancy_lt never emphasizes a node whose occupancy is unknown (null guard)", () => {
    const f: Filters = { ghosts: false, evidenceClass: null, occupancyLt: 1, changed: false, summary: false, eastWestAll: false, tileOrder: "hosts", expand: [], problems: [] };
    const unknown = { ...dc, occupancy: null };
    expect(nodePassesFilters(unknown, f)).toBe(false);
  });
});

describe("search resolution", () => {
  it("resolves an entity key, a CIDR and a role", () => {
    expect(resolveSearch(graph, "hostname:axdc01").zoom).toMatchObject({ level: "host", key: "hostname:axdc01" });
    expect(resolveSearch(graph, "10.80.72.0/24").zoom).toMatchObject({ level: "segment", cidr: "10.80.72.0/24" });
    expect(resolveSearch(graph, "10.80.20.10").zoom).toMatchObject({ level: "host" });
    expect(resolveSearch(graph, "domain-controller").zoom).toMatchObject({ level: "matrix" });
  });

  it("reports a miss instead of guessing", () => {
    const miss = resolveSearch(graph, "does-not-exist");
    expect(miss.zoom).toBeNull();
    expect(miss.note).toMatch(/no name, address, role or CIDR/);
  });
});

describe("evidence rendering helpers", () => {
  it("maps a class to its swatch, and null to neutral", () => {
    expect(evidenceClassName("observed")).toBe("ev-observed");
    expect(evidenceClassName(null)).toBe("ev-none");
    expect(evidenceClassName("nonsense")).toBe("ev-none");
  });

  it("floors a known-but-low confidence and never fades an absent one", () => {
    expect(saturationFor(null)).toBe(1);
    expect(saturationFor(0)).toBe(0.35);
    expect(saturationFor(1)).toBe(1);
  });
});

describe("evidence-mix ring orders by PRECEDENCE, not alphabetically (SHOULD-FIX A)", () => {
  // A minimal export: one segment, placed hosts with the given placement
  // evidence classes. `observed` outranks `imputed` in precedence but sorts
  // AFTER it alphabetically -- the exact disagreement the alphabetical sort
  // got wrong, and why the all-`observed` real fixture never caught it.
  function mixGraph(classes: Array<string | null>) {
    const nodes: TopologyExportNode[] = classes.map((cls, i) => ({
      id: `address:10.0.0.${i + 1}`,
      address: `10.0.0.${i + 1}`,
      locality: "local",
      segment: "10.0.0.0/24",
      segment_evidence_class: cls,
      role: "generic-endpoint",
      role_confidence: 0.5,
      role_evidence_class: "imputed",
      windows_seen: 10,
      listen_ports: [],
      dial_ports: [],
    }));
    const exportData: TopologyExport = {
      imprint_id: "mix",
      pit_id: null,
      as_of: null,
      network: null,
      vantage: null,
      windows: 10,
      schema_version: "1.3",
      segments: [{ cidr: "10.0.0.0/24", name: "seg-10-0-0-0", entities: nodes.length, coverage_source: null }],
      nodes,
      edges: [],
      census: { entities_total: nodes.length, with_role: nodes.length, placed: nodes.length, local: nodes.length },
    };
    return buildGraph(exportData, { imprintId: "mix", deployMinOccupancy: 0.8 });
  }

  it("puts the highest-precedence class first, not the alphabetically-first", () => {
    const seg = mixGraph(["imputed", "observed", "imputed"]).segmentByCidr.get("10.0.0.0/24")!;
    // Alphabetical would be ["imputed", "observed"]; precedence is the reverse.
    expect(seg.evidenceMix.map((m) => m.evidenceClass)).toEqual(["observed", "imputed"]);
    expect(seg.evidenceMix[0]!.count).toBe(1); // one observed
    expect(seg.evidenceMix[1]!.count).toBe(2); // two imputed
  });

  it("sorts an unknown producer class after the six and the (none) sentinel last", () => {
    const seg = mixGraph(["imputed", null, "made-up", "observed"]).segmentByCidr.get("10.0.0.0/24")!;
    expect(seg.evidenceMix.map((m) => m.evidenceClass)).toEqual([
      "observed", // precedence 2
      "imputed", // precedence 4
      "made-up", // unknown -> after the six
      "(none)", // sentinel -> last
    ]);
  });
});
