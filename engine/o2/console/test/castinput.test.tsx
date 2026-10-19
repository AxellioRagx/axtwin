/**
 * R5 (GAP-079): the cast summary's conduit roster and router legs flow into
 * the live graph. Two halves:
 *
 *  - `castInputFrom` (pure): the wire fields become `buildGraph`'s input, and
 *    the canonical id is the caller's, never a baked one (LES-263);
 *  - `useGraph` (the hook the live map uses): the graph it builds from a
 *    FETCHED `CastSummary` carries that summary's roster and legs. This is
 *    the half that fails if a constant creeps back in between the fetch and
 *    the graph -- which is exactly what the pre-R5 empty roster constant was.
 */
import type { ReactNode } from "react";
import { render, renderHook, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ConsoleApi } from "../src/api/client";
import { ApiContext } from "../src/api/context";
import type { CastSummary } from "../src/api/types";
import { buildGraph, castStatusOf } from "../src/model/graph";
import { NO_FILTERS } from "../src/model/zoom";
import { Level0 } from "../src/views/Level0";
import { castInputFrom, useGraph } from "../src/views/useGraph";
import { trimmedCast, trimmedExport } from "./fixtures/trimmed";

const ROSTER = [
  { from: "seg-10-80-20-0", to: "seg-10-20-193-0" },
  { from: "seg-10-20-192-0", to: "seg-10-20-193-0" },
  { from: "seg-10-80-72-0", to: "seg-10-80-20-0" },
  { from: "seg-10-1-10-0", to: "internet-sink" }, // unresolvable on purpose
];
const LEGS = ["seg-10-80-20-0", "seg-10-20-193-0", "internet-sink"];

const CAST: CastSummary = {
  id: "trimmed-fixture",
  bundle_dir: "/b/trimmed-fixture",
  twin_config_id: "trimmed-fixture",
  description: null,
  pit_id: "pit-trim",
  bundle_schema: "axr-topology/1.5",
  topology_schema_version: "1.3",
  code_fingerprint: null,
  params: {},
  deploy_min_occupancy: 0.8,
  deploy_min_occupancy_source: "engine default",
  segments: 6,
  node_specs: 6,
  vms: 40,
  hosts: 40,
  conduits: ROSTER.length,
  observed_nodes: trimmedExport.nodes.length,
  observed_edges: trimmedExport.edges.length,
  observed_segments: 6,
  census: {},
  has_provenance: false,
  has_topology_export: true,
  has_assertions: false,
  has_layout: false,
  provenance_schema: null,
  provenance_path: null,
  sidecar_shape: "absent",
  evidence_source: "",
  warnings: [],
  conduit_roster: ROSTER,
  routers: [{ name: "gw", segments: LEGS }],
};

/** The three resolved, undirected, ordered pairs of `ROSTER`. */
const RESOLVED = [
  { a: "seg-10-20-192-0", b: "seg-10-20-193-0" },
  { a: "seg-10-20-193-0", b: "seg-10-80-20-0" },
  { a: "seg-10-80-20-0", b: "seg-10-80-72-0" },
];

describe("castInputFrom -- the cast summary's wire fields become the graph's input (R5)", () => {
  it("carries the roster and the router legs into buildGraph", () => {
    const graph = buildGraph(trimmedExport, castInputFrom(CAST, "trimmed-fixture"));
    expect(graph.conduits).toEqual(RESOLVED);
    expect(graph.unresolvedConduitEndpoints).toEqual(["internet-sink"]);
    expect(graph.routerSegmentNames).toEqual(["internet-sink", "seg-10-20-193-0", "seg-10-80-20-0"]);
    const legged = graph.segments.filter((s) => s.hasRouterLeg).map((s) => s.name).sort();
    expect(legged).toEqual(["seg-10-20-193-0", "seg-10-80-20-0"]);
  });

  it("takes the gate from the summary and the id from the CALLER, never a baked one (LES-263)", () => {
    const input = castInputFrom({ ...CAST, pit_id: "pit-baked", deploy_min_occupancy: 0.5 }, "canonical-id");
    expect(input.imprintId).toBe("canonical-id");
    expect(input.deployMinOccupancy).toBe(0.5);
  });

  it("a server too old to send the two fields degrades to an empty band, never a crash", () => {
    const old: Partial<CastSummary> = { ...CAST };
    delete old.conduit_roster;
    delete old.routers;
    const graph = buildGraph(trimmedExport, castInputFrom(old as CastSummary, "trimmed-fixture"));
    expect(graph.conduits).toEqual([]);
    expect(graph.routerSegmentNames).toEqual([]);
    // ...and the graph SAYS they were not published (GAP-082 review, S4).
    expect(graph.castOmits).toEqual(["conduits", "routers"]);
  });

  it("an empty roster is a statement, not an omission", () => {
    const graph = buildGraph(
      trimmedExport,
      castInputFrom({ ...CAST, conduits: 0, conduit_roster: [], routers: [] }, "trimmed-fixture"),
    );
    expect(graph.castOmits).toEqual([]);
  });
});

// ===========================================================================
// The fix round's A1 -- the cast publishes its SEGMENT roster, and the live
// graph consumes it. Until then `castInputFrom` supplied no deployed CIDR and
// no zone type, so every real segment read "not declared" for both -- a false
// statement only a test fixture's hand-made maps ever hid.
// ===========================================================================
/** The trimmed twin's roster as a server publishes it: every observed segment
 *  but `10.80.72.0/24` (PRUNED), relocated into 10.200, plus the cast's own
 *  unobserved `internet-sink`. */
const SEGMENT_ROSTER = [
  { name: "seg-10-1-10-0", cidr: "10.200.0.0/24", zone_type: "simple" },
  { name: "seg-10-20-192-0", cidr: "10.200.1.0/24", zone_type: null },
  { name: "seg-10-20-193-0", cidr: "10.200.2.0/24", zone_type: "vlan" },
  { name: "seg-10-80-20-0", cidr: "10.200.3.0/24", zone_type: "simple" },
  { name: "seg-192-168-0-0", cidr: "10.200.4.0/24", zone_type: "simple" },
  { name: "internet-sink", cidr: "10.200.254.0/24", zone_type: "simple" },
];

describe("castInputFrom -- the SEGMENT roster reaches the graph (A1)", () => {
  it("passes the roster through, and the graph's deployed CIDR and zone come from it -- not from a test map", () => {
    const graph = buildGraph(trimmedExport, castInputFrom({ ...CAST, segment_roster: SEGMENT_ROSTER }, "trimmed-fixture"));
    expect(graph.castSegments).toEqual(SEGMENT_ROSTER);
    const seg = (cidr: string) => graph.segmentByCidr.get(cidr)!;
    expect(seg("10.80.20.0/24").deployedCidr).toBe("10.200.3.0/24");
    expect(seg("10.80.20.0/24").zoneType).toBe("simple");
    expect(seg("10.20.193.0/24").zoneType).toBe("vlan");
    // A roster entry with no zone type is not given one.
    expect(seg("10.20.192.0/24").deployedCidr).toBe("10.200.1.0/24");
    expect(seg("10.20.192.0/24").zoneType).toBeNull();
    // The pruned segment is observed (it has a tile) and carries nothing from the cast.
    expect(seg("10.80.72.0/24").deployedCidr).toBeNull();
    expect(seg("10.80.72.0/24").zoneType).toBeNull();
  });

  it("joins by NAME: carried, absent -- and, with no roster, unpublished rather than a guess", () => {
    // Restated for the second fix round's SF1 (LES-299): a name the roster
    // lacks is `absent` -- "renamed or pruned" -- never asserted as `pruned`,
    // since the cast carries a renamed segment under a name the export lacks.
    const withRoster = buildGraph(trimmedExport, castInputFrom({ ...CAST, segment_roster: SEGMENT_ROSTER }, "trimmed-fixture"));
    expect(castStatusOf(withRoster, "seg-10-80-20-0")).toBe("carried");
    expect(castStatusOf(withRoster, "seg-10-80-72-0")).toBe("absent");
    const old: Partial<CastSummary> = { ...CAST };
    delete old.segment_roster;
    const withoutRoster = buildGraph(trimmedExport, castInputFrom(old as CastSummary, "trimmed-fixture"));
    expect(withoutRoster.castSegments).toBeNull();
    expect(castStatusOf(withoutRoster, "seg-10-80-20-0")).toBe("unpublished");
    expect(castStatusOf(withoutRoster, "seg-10-80-72-0")).toBe("unpublished");
    // A wire `null` is "not published" too.
    const nulled = buildGraph(trimmedExport, castInputFrom({ ...CAST, segment_roster: null } as unknown as CastSummary, "trimmed-fixture"));
    expect(nulled.castSegments).toBeNull();
  });

  it("an EMPTY roster is a statement -- the config declares no segment, so no observed one is declared", () => {
    const graph = buildGraph(trimmedExport, castInputFrom({ ...CAST, segments: 0, segment_roster: [] }, "trimmed-fixture"));
    expect(graph.castSegments).toEqual([]);
    expect(castStatusOf(graph, "seg-10-80-20-0")).toBe("absent");
  });

  it("the roster SUPERSEDES the golden fixture's direct maps -- one source in the live app, not two", () => {
    const graph = buildGraph(trimmedExport, { ...trimmedCast, segmentRoster: SEGMENT_ROSTER });
    // `trimmedCast` maps `seg-10-80-20-0` to 10.200.10.0/24; the roster says 10.200.3.0/24.
    expect(trimmedCast.deployedCidrByName?.["seg-10-80-20-0"]).toBe("10.200.10.0/24");
    expect(graph.segmentByCidr.get("10.80.20.0/24")!.deployedCidr).toBe("10.200.3.0/24");
    // ...and without a roster the maps still apply (the goldens pin those values).
    const golden = buildGraph(trimmedExport, trimmedCast);
    expect(golden.segmentByCidr.get("10.80.20.0/24")!.deployedCidr).toBe("10.200.10.0/24");
    expect(golden.castSegments).toBeNull();
  });
});

describe("useGraph -- the live hook carries the FETCHED segment roster (A1)", () => {
  it("builds the deployed CIDRs from GET .../cast", async () => {
    const { result } = renderUseGraph({ ...CAST, segment_roster: SEGMENT_ROSTER });
    await waitFor(() => expect(result.current.graph).not.toBeNull());
    expect(result.current.graph!.castSegments).toEqual(SEGMENT_ROSTER);
    expect(result.current.graph!.segmentByCidr.get("10.80.20.0/24")!.deployedCidr).toBe("10.200.3.0/24");
  });
});

function clientServing(cast: CastSummary): ConsoleApi {
  const unused = (name: string) => () => Promise.reject(new Error(`${name} not used by this test`));
  return {
    listImprints: unused("listImprints"),
    getImprint: unused("getImprint"),
    getProvenance: unused("getProvenance"),
    getTopology: () => Promise.resolve(trimmedExport),
    getCast: () => Promise.resolve(cast),
    getAssertions: unused("getAssertions"),
    putAssertions: unused("putAssertions"),
    getLayout: unused("getLayout"),
    putLayout: unused("putLayout"),
    apply: unused("apply"),
    getDiff: unused("getDiff"),
  };
}

function renderUseGraph(cast: CastSummary) {
  const client = clientServing(cast);
  const wrapper = ({ children }: { children: ReactNode }) => (
    <ApiContext.Provider value={client}>{children}</ApiContext.Provider>
  );
  return renderHook(() => useGraph("trimmed-fixture"), { wrapper });
}

describe("useGraph -- the live hook builds its graph from GET .../cast (R5)", () => {
  it("draws the FETCHED roster and legs", async () => {
    const { result } = renderUseGraph(CAST);
    await waitFor(() => expect(result.current.graph).not.toBeNull());
    const graph = result.current.graph!;
    expect(graph.conduits).toEqual(RESOLVED);
    expect(graph.routerSegmentNames).toEqual(["internet-sink", "seg-10-20-193-0", "seg-10-80-20-0"]);
    expect(graph.imprintId).toBe("trimmed-fixture");
  });

  it("a cast that declares none builds an empty band -- the fetched roster decides, not a default", async () => {
    const { result } = renderUseGraph({ ...CAST, conduits: 0, conduit_roster: [], routers: [] });
    await waitFor(() => expect(result.current.graph).not.toBeNull());
    expect(result.current.graph!.conduits).toEqual([]);
    expect(result.current.graph!.routerSegmentNames).toEqual([]);
    expect(result.current.graph!.castOmits).toEqual([]);
  });
});

// ===========================================================================
// The GAP-082 review: the WAN label names "the gateway" only for exactly one
// router and counts only DRAWN legs (S5); an omitted roster is said to be
// unpublished in one line, never drawn as "none" (S4).
// ===========================================================================
function renderLevel0(cast: CastSummary): void {
  const graph = buildGraph(trimmedExport, castInputFrom(cast, "trimmed-fixture"));
  render(<Level0 graph={graph} filters={NO_FILTERS} go={() => {}} onFocus={() => {}} />);
}

describe("the WAN label says what it counts", () => {
  it("one router: the gateway, counting only the legs that land on a drawn tile", () => {
    // LEGS carries `internet-sink` -- a leg with no tile, so it is not counted.
    renderLevel0(CAST);
    expect(screen.getByTestId("wan-label").textContent).toBe("gateway · 2 legs · SNAT");
  });

  it("several routers are counted, never collapsed into one gateway", () => {
    renderLevel0({
      ...CAST,
      routers: [
        { name: "gw1", segments: ["seg-10-80-20-0"] },
        { name: "gw2", segments: ["seg-10-20-193-0", "internet-sink"] },
        { name: "gw3", segments: ["seg-10-80-20-0"] },
      ],
    });
    const label = screen.getByTestId("wan-label").textContent ?? "";
    expect(label).toBe("3 routers · legs on 2 segments");
    expect(label).not.toMatch(/gateway|SNAT/);
  });

  it("one router with no leg on a drawn tile: the gateway without a count, its legs in the tooltip", () => {
    // Never "0 legs" (GAP-082 review round 2): the box is drawn because a leg
    // exists, and the legs it could not place are named where a count would be.
    renderLevel0({ ...CAST, routers: [{ name: "gw", segments: ["internet-sink"] }] });
    const label = screen.getByTestId("wan-label");
    expect(label.textContent).toBe("gateway · SNAT");
    expect(label.parentElement?.querySelector("title")?.textContent).toBe(
      "router legs with no tile on this map (not counted): internet-sink",
    );
  });

  it("several routers with no leg on a drawn tile are counted, and claim no segments", () => {
    renderLevel0({
      ...CAST,
      routers: [
        { name: "gw01", segments: ["internet-sink"] },
        { name: "gw02", segments: ["internet-sink"] },
      ],
    });
    expect(screen.getByTestId("wan-label").textContent).toBe("2 routers");
  });
});

describe("an omitted roster is reported, an empty one is not", () => {
  it("both omitted: one line naming both, and what is therefore not drawn", () => {
    const old: Partial<CastSummary> = { ...CAST };
    delete old.conduit_roster;
    delete old.routers;
    renderLevel0(old as CastSummary);
    const note = screen.getByTestId("roster-unpublished").textContent ?? "";
    expect(note).toMatch(/does not publish the conduit roster or the routers' legs/);
    // R2: the box is still drawn from observed traffic when there is any, so
    // what an unpublished `routers` costs is the DECLARED edge.
    // R4: an unpublished roster no longer costs the BAND -- that draws from the
    // export's own edges now -- only the twin's DECLARED conduits.
    expect(note).toMatch(/the twin's declared conduits and the declared WAN edge are not drawn/);
  });

  it("only the routers omitted: the note names the declared WAN edge alone", () => {
    const old: Partial<CastSummary> = { ...CAST };
    delete old.routers;
    renderLevel0(old as CastSummary);
    const note = screen.getByTestId("roster-unpublished").textContent ?? "";
    expect(note).toMatch(/the routers' legs/);
    expect(note).toMatch(/the declared WAN edge is not drawn/);
    expect(note).not.toMatch(/conduit roster/);
  });

  it("empty lists are the twin declaring none: no note", () => {
    renderLevel0({ ...CAST, conduits: 0, conduit_roster: [], routers: [] });
    expect(screen.queryByTestId("roster-unpublished")).toBeNull();
  });
});
