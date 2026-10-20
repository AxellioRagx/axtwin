/**
 * R5 (GAP-079) from the OTHER side of the seam: a `CastSummary` from a server
 * too old to publish `conduit_roster`/`routers` (they did not exist before R5;
 * `routers` replaced the flat `router_segments` in the GAP-082 review), or one
 * that publishes them `null` or empty, must neither throw nor make geometry up.
 *
 * `castInputFrom` passes the two fields through as published
 * (`views/useGraph.ts`); `buildGraph`'s `?? []` is what keeps an absent field
 * from becoming an invented ASSERTED arc or an invented GATEWAY. These tests
 * pin the whole chain -- wire summary -> CastInput -> graph -> layout -> DOM --
 * per wire shape, so a regression anywhere along it fails here.
 *
 * **Updated for R4 (§3 R4, the arcs ruling).** "No published roster ⇒ no
 * east-west band" was this file's other original assertion, and it is no
 * longer the contract either (LES-299, twice over): the band now draws
 * OBSERVED segment-pair arcs from the export's own edges, and the roster adds
 * only the twin's DECLARED conduits on top. The fixture carries exactly one
 * observed pair -- `10.80.72.10 → 10.80.20.5`, the single local↔local edge
 * between two different drawn segments -- so a roster-less summary now draws
 * ONE arc where it drew none. The invention guarded against is unchanged: an
 * unpublished roster must never produce an ASSERTED arc.
 *
 * **Updated for R2 (`docs/WORK_ORDER_console_level0_rendering.md` §3 R2).**
 * "No published router ⇒ no WAN box" was this file's original assertion and is
 * no longer the contract: the WAN edge is now also drawn when the export shows
 * local↔non-local traffic, as an INFERRED edge labelled *egress (inferred from
 * observed traffic)*. The invention this file guards against is unchanged and
 * is still asserted at every site -- an unpublished `routers` must never
 * produce the claim that the twin has a **gateway**.
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ConsoleApi } from "../src/api/client";
import type { AssertionsDoc, CastSummary, ImprintList, ImprintProvenance } from "../src/api/types";
import { layout0 } from "../src/layout/level0";
import { buildGraph } from "../src/model/graph";
import { NO_FILTERS } from "../src/model/zoom";
import { TestApp } from "../src/routes";
import { Level0 } from "../src/views/Level0";
import { castInputFrom } from "../src/views/useGraph";
import { trimmedCast, trimmedExport } from "./fixtures/trimmed";

/** Four conduits (one to the unresolvable `internet-sink`) and one router
 *  with two legs -- the same roster the pure `trimmedCast` carries. */
const ROSTER = [...(trimmedCast.conduits ?? [])];
const ROUTERS = (trimmedCast.routers ?? []).map((r) => ({ name: r.name, segments: [...r.segments] }));
const LEGS = ROUTERS.flatMap((r) => r.segments);

/** What the WAN box reads when NO router is published and the edge is inferred
 *  from the fixture's own crossing traffic (R2). Never the word "gateway". */
const INFERRED_LABEL = "egress (inferred from observed traffic)";
/** What it reads when the cast declares the gateway and its legs (R5). */
const DECLARED_LABEL = `gateway · ${LEGS.length} legs · SNAT`;

const BASE: CastSummary = {
  id: "trimmed-fixture",
  bundle_dir: "/b/trimmed-fixture",
  twin_config_id: "trimmed-fixture",
  description: null,
  pit_id: null,
  bundle_schema: null,
  topology_schema_version: null,
  code_fingerprint: null,
  params: {},
  deploy_min_occupancy: trimmedCast.deployMinOccupancy,
  deploy_min_occupancy_source: "engine default",
  segments: trimmedExport.segments.length,
  node_specs: 0,
  vms: 0,
  hosts: 0,
  conduits: ROSTER.length,
  observed_nodes: trimmedExport.nodes.length,
  observed_edges: trimmedExport.edges.length,
  observed_segments: trimmedExport.segments.length,
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
  routers: ROUTERS,
};

/** What a server may actually send for the two R5 fields. */
type Wire = Omit<CastSummary, "conduit_roster" | "routers"> & {
  conduit_roster?: CastSummary["conduit_roster"] | null;
  routers?: CastSummary["routers"] | null;
};

/** A pre-R5 server: the two keys are simply not there. */
function preR5(): Wire {
  const { conduit_roster: _roster, routers: _routers, ...rest } = BASE;
  return rest;
}

function drawn(wire: Wire) {
  const graph = buildGraph(trimmedExport, castInputFrom(wire as unknown as CastSummary, "trimmed-fixture"));
  return { graph, l0: layout0(graph) };
}

/** The fixture's single OBSERVED east-west pair (10.80.72.0/24 ↔ 10.80.20.0/24)
 *  -- drawn whatever the cast publishes, because it comes from the export. */
const OBSERVED_PAIRS = 1;
const asserted = (l0: ReturnType<typeof layout0>) => l0.arcs.filter((a) => a.kind === "asserted");
const observed = (l0: ReturnType<typeof layout0>) => l0.arcs.filter((a) => a.kind === "observed");

function renderLevel0(wire: Wire): void {
  const { graph } = drawn(wire);
  render(<Level0 graph={graph} filters={NO_FILTERS} go={() => {}} onFocus={() => {}} />);
}

const NOTHING_PUBLISHED: Array<[string, Wire]> = [
  ["absent (a pre-R5 server)", preR5()],
  ["null", { ...preR5(), conduit_roster: null, routers: null }],
  ["empty", { ...BASE, conduits: 0, conduit_roster: [], routers: [] }],
];

describe("a cast summary that publishes no roster and no legs invents nothing", () => {
  it.each(NOTHING_PUBLISHED)("%s: no ASSERTED arc, no gateway claimed, every tile still drawn", (_label, wire) => {
    const { graph, l0 } = drawn(wire);
    expect(graph.conduits).toEqual([]);
    expect(graph.unresolvedConduitEndpoints).toEqual([]);
    expect(graph.routerSegmentNames).toEqual([]);
    expect(graph.segments.some((s) => s.hasRouterLeg)).toBe(false);
    expect(asserted(l0)).toEqual([]);
    // R4: the OBSERVED band is the export's, not the cast's, so it still draws.
    expect(observed(l0)).toHaveLength(OBSERVED_PAIRS);
    expect(l0.routerSegmentCount).toBe(0);
    // R2: the box IS drawn -- from the export's own crossing traffic -- and it
    // is labelled as inferred. The invention guarded against here is unchanged
    // and still asserted: an unpublished `routers` must never yield "gateway".
    expect(l0.wanEdge).not.toBeNull();
    expect(graph.wanEdge?.inferred).toBe(true);

    renderLevel0(wire);
    expect(screen.getAllByTestId("segment-tile")).toHaveLength(trimmedExport.segments.length);
    const drawnArcs = screen.queryAllByTestId("east-west-arc");
    expect(drawnArcs).toHaveLength(OBSERVED_PAIRS);
    expect(drawnArcs.every((a) => a.getAttribute("data-kind") === "observed")).toBe(true);
    expect(screen.getByTestId("wan-label").textContent).toBe(INFERRED_LABEL);
  });
});

describe("each R5 field lights only its own geometry", () => {
  it("a roster without legs draws the ASSERTED arcs and an INFERRED edge, never a gateway", () => {
    const { graph, l0 } = drawn({ ...preR5(), conduit_roster: ROSTER });
    expect(asserted(l0)).toHaveLength(3); // 4 conduits, one to `internet-sink`
    // The fixture's one observed pair IS one of those conduits, so it is drawn
    // once -- as asserted -- and never as a second identical curve (R4).
    expect(observed(l0)).toEqual([]);
    expect(graph.unresolvedConduitEndpoints).toEqual(["internet-sink"]);
    expect(graph.wanEdge?.inferred).toBe(true);
    renderLevel0({ ...preR5(), conduit_roster: ROSTER });
    expect(screen.getAllByTestId("east-west-arc")).toHaveLength(3);
    expect(screen.getByTestId("wan-label").textContent).toBe(INFERRED_LABEL);
  });

  it("legs without a roster draw the WAN box and no ASSERTED arc", () => {
    const { l0 } = drawn({ ...preR5(), routers: ROUTERS });
    expect(asserted(l0)).toEqual([]);
    expect(observed(l0)).toHaveLength(OBSERVED_PAIRS);
    expect(l0.wanEdge).not.toBeNull();
    expect(l0.routerSegmentCount).toBe(LEGS.length);
    renderLevel0({ ...preR5(), routers: ROUTERS });
    expect(screen.queryAllByTestId("east-west-arc")).toHaveLength(OBSERVED_PAIRS);
    expect(screen.getByTestId("wan-label").textContent).toBe(`gateway · ${LEGS.length} legs · SNAT`);
  });

  it("a single leg reads in the singular", () => {
    renderLevel0({ ...preR5(), routers: [{ name: "gw", segments: LEGS.slice(0, 1) }] });
    expect(screen.getByTestId("wan-label").textContent).toBe("gateway · 1 leg · SNAT");
  });
});

describe("a roster the graph cannot resolve reports its ends and draws nothing", () => {
  it("no end names a drawn segment: zero ASSERTED arcs, every end reported", () => {
    const { graph, l0 } = drawn({
      ...preR5(),
      conduit_roster: [
        { from: "nowhere", to: "internet-sink" },
        { from: "ghost", to: "nowhere" },
      ],
    });
    expect(asserted(l0)).toEqual([]);
    expect(graph.unresolvedConduitEndpoints).toEqual(["ghost", "internet-sink", "nowhere"]);
    // An unresolvable roster names no router either, so the edge stays inferred.
    expect(graph.wanEdge?.inferred).toBe(true);
  });

  it("duplicate, reversed and self-loop entries draw one ASSERTED arc per real pair", () => {
    const a = "seg-10-80-20-0";
    const b = "seg-10-20-193-0";
    const { graph, l0 } = drawn({
      ...preR5(),
      conduit_roster: [
        { from: a, to: b },
        { from: b, to: a },
        { from: a, to: b },
        { from: a, to: a },
      ],
    });
    expect(graph.conduits).toEqual([{ a: b, b: a }]); // ordered: "seg-10-20…" < "seg-10-80…"
    expect(asserted(l0)).toHaveLength(1);
    expect(observed(l0)).toHaveLength(OBSERVED_PAIRS); // untouched by the roster
    expect(graph.unresolvedConduitEndpoints).toEqual([]);
  });
});

// ===========================================================================
// GAP-082 review re-test. `castOmits` says "not published" for an ABSENT or
// NULL field and nothing for `[]` -- per field, in every combination; the WAN
// label's counting rule at its edges; and the note end to end through the
// live shell (fetch -> useGraph -> MapPane -> Level0).
// ===========================================================================
const DRAWN_A = "seg-10-80-20-0";
const DRAWN_B = "seg-10-20-193-0";

const PUBLISH_MATRIX: Array<[string, Partial<Wire>, Array<"conduits" | "routers">]> = [
  ["both absent", {}, ["conduits", "routers"]],
  ["both null", { conduit_roster: null, routers: null }, ["conduits", "routers"]],
  ["roster null, routers []", { conduit_roster: null, routers: [] }, ["conduits"]],
  ["roster absent, routers published", { routers: ROUTERS }, ["conduits"]],
  ["roster [], routers null", { conduit_roster: [], routers: null }, ["routers"]],
  ["roster published, routers absent", { conduit_roster: ROSTER }, ["routers"]],
  ["both []", { conduit_roster: [], routers: [] }, []],
  ["both published", { conduit_roster: ROSTER, routers: ROUTERS }, []],
];

describe("absent or null is 'not published'; [] is 'the twin declares none' -- per field", () => {
  it.each(PUBLISH_MATRIX)("%s", (_label, fields, omits) => {
    const wire: Wire = { ...preR5(), ...fields };
    expect(drawn(wire).graph.castOmits).toEqual(omits);
    renderLevel0(wire);
    const note = screen.queryByTestId("roster-unpublished");
    if (omits.length === 0) {
      expect(note).toBeNull();
    } else {
      const text = note?.textContent ?? "";
      // The note names exactly what was omitted, and what is therefore not drawn.
      expect(text.includes("the conduit roster")).toBe(omits.includes("conduits"));
      // R4: what an unpublished roster costs is the twin's DECLARED conduits,
      // not the band -- which now draws from the export. GAP-079's old "the
      // east-west band is not drawn" wording is deleted with the fact.
      expect(text.includes("the twin's declared conduits")).toBe(omits.includes("conduits"));
      expect(text.includes("the east-west band")).toBe(false);
      expect(text.includes("the routers' legs")).toBe(omits.includes("routers"));
      // R2: an unpublished `routers` no longer means "no WAN box" -- the box is
      // still drawn from observed traffic -- so what the note reports missing
      // is the DECLARED edge.
      expect(text.includes("the declared WAN edge")).toBe(omits.includes("routers"));
    }
    // ...and the drawing agrees with the note: the gateway is claimed only
    // where the cast published one; otherwise the edge reads as inferred.
    expect(screen.getByTestId("wan-label").textContent).toBe(
      fields.routers === ROUTERS ? DECLARED_LABEL : INFERRED_LABEL,
    );
  });
});

describe("the WAN label at its edges", () => {
  function label(routers: Array<{ name: string; segments: string[] }>): string | null {
    renderLevel0({ ...preR5(), routers });
    return screen.queryByTestId("wan-label")?.textContent ?? null;
  }

  it("one router's duplicated legs count once", () => {
    expect(label([{ name: "gw", segments: [DRAWN_A, DRAWN_A, DRAWN_B] }])).toBe("gateway · 2 legs · SNAT");
  });

  it("a router with no legs is still one of the routers", () => {
    expect(label([{ name: "gw1", segments: [DRAWN_A] }, { name: "gw2", segments: [] }])).toBe(
      "2 routers · legs on 1 segment",
    );
  });

  it("two routers sharing every leg are counted as two, on the segments they share", () => {
    expect(label([{ name: "gw1", segments: [DRAWN_A, DRAWN_B] }, { name: "gw2", segments: [DRAWN_B, DRAWN_A] }])).toBe(
      "2 routers · legs on 2 segments",
    );
  });

  it("one router whose only legs have no tile: the box is drawn and claims no leg count", () => {
    // Pins the ruled counting rule ("N counts legs on DRAWN tiles") at its edge:
    // the WAN box is drawn because a router leg exists, and since none of its
    // legs lands on a drawn tile the label names the gateway WITHOUT a count --
    // never "0 legs" (GAP-082 review round 2); the box's tooltip names the
    // legs it could not place.
    const { l0 } = drawn({ ...preR5(), routers: [{ name: "gw", segments: ["internet-sink", "nowhere"] }] });
    expect(l0.wanEdge).not.toBeNull();
    expect(label([{ name: "gw", segments: ["internet-sink", "nowhere"] }])).toBe("gateway · SNAT");
  });

  it("a router whose segments are null neither throws nor invents a leg", () => {
    const wire = { ...preR5(), routers: [{ name: "gw", segments: null }] } as unknown as Wire;
    const { graph, l0 } = drawn(wire);
    expect(graph.routers).toEqual([{ name: "gw", segments: [] }]);
    // R2: the cast DECLARED a gateway, so the edge is not "inferred" even
    // though the router names no segment -- crediting the observation for
    // something the twin states would be the wrong way round. The leg count is
    // a separate question, and the label drops it rather than saying "0 legs".
    expect(l0.wanEdge).not.toBeNull();
    expect(graph.wanEdge?.inferred).toBe(false);
    expect(label([{ name: "gw", segments: null as unknown as string[] }])).toBe("gateway · SNAT");
  });
});

const OLD_CATALOG: ImprintList = {
  imprints: [
    { id: "trimmed-fixture", hosts: 40, segments: 6, as_of: "", mean_confidence: 0, has_provenance: false, confidence_source: "absent" },
  ],
  skipped: [],
};

const OLD_PROVENANCE: ImprintProvenance = {
  imprint_id: "trimmed-fixture",
  rows: [],
  sidecar_shape: "absent",
  sidecar_schema: null,
  evidence_source: "absent",
  code_fingerprint: null,
  population: {},
  warnings: [],
};

const OLD_ASSERTIONS: AssertionsDoc = {
  imprint_id: "trimmed-fixture",
  path: "/b/trimmed-fixture/trimmed-fixture.assertions.yaml",
  exists: false,
  etag: null,
  schema: "axr-assertions/1",
  imprint: "trimmed-fixture",
  as_of: null,
  source: null,
  add: [],
  override: [],
  suppress: [],
  warnings: [],
};

/** A whole `ConsoleApi` (typed, so a new method breaks this double at
 *  compile time) serving `wire` as `GET .../cast`. */
function apiServing(wire: Wire): ConsoleApi {
  const unused = (name: string) => () => Promise.reject(new Error(`${name} not used`));
  return {
    listImprints: () => Promise.resolve(OLD_CATALOG),
    getImprint: unused("getImprint"),
    getProvenance: () => Promise.resolve(OLD_PROVENANCE),
    getTopology: () => Promise.resolve(trimmedExport),
    getCast: () => Promise.resolve(wire as unknown as CastSummary),
    getAssertions: () => Promise.resolve(OLD_ASSERTIONS),
    putAssertions: unused("putAssertions"),
    getLayout: unused("getLayout"),
    putLayout: unused("putLayout"),
    apply: unused("apply"),
    getDiff: unused("getDiff"),
  };
}

describe("end to end: the live shell says what an older server did not publish", () => {
  it("a pre-R5 summary: the note, no ASSERTED arc, and an INFERRED edge", async () => {
    render(<TestApp client={apiServing(preR5())} url="/design/trimmed-fixture" />);
    expect(await screen.findByTestId("level0-svg")).toBeDefined();
    const note = screen.getByTestId("roster-unpublished").textContent ?? "";
    expect(note).toMatch(/does not publish the conduit roster or the routers' legs/);
    const arcs = screen.queryAllByTestId("east-west-arc");
    expect(arcs).toHaveLength(OBSERVED_PAIRS); // R4: the export's own band
    expect(arcs.every((a) => a.getAttribute("data-kind") === "observed")).toBe(true);
    expect(screen.getByTestId("wan-label").textContent).toBe(INFERRED_LABEL);
  });

  it("a current server declaring none: no note, no ASSERTED arc, no gateway claimed", async () => {
    render(<TestApp client={apiServing({ ...BASE, conduits: 0, conduit_roster: [], routers: [] })} url="/design/trimmed-fixture" />);
    expect(await screen.findByTestId("level0-svg")).toBeDefined();
    expect(screen.queryByTestId("roster-unpublished")).toBeNull();
    const arcs = screen.queryAllByTestId("east-west-arc");
    expect(arcs).toHaveLength(OBSERVED_PAIRS);
    expect(arcs.every((a) => a.getAttribute("data-kind") === "observed")).toBe(true);
    expect(screen.getByTestId("wan-label").textContent).toBe(INFERRED_LABEL);
  });
});
