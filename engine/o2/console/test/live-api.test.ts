/**
 * The one test that runs against a REAL `axrange serve`, and is skipped
 * without one.
 *
 *     AXRANGE_LIVE_API=http://127.0.0.1:8011 \
 *     AXRANGE_LIVE_IMPRINT=corpapcn-golden npm run test
 *
 * Why it exists: `api/types.ts` is a **hand-written** mirror of
 * `axrange/api/dtos.py` (seam 1). Nothing in the unit suite can notice when
 * the two drift, because every fixture in it was written from the mirror --
 * the same trap LES-242 names for a fake backend. This asserts the mirror's
 * REQUIRED fields against payloads the real API produced from a real cast
 * bundle, and checks one error mapping against the server that emits it.
 *
 * It is skipped, not failed, without a server: a unit suite that needs a
 * running service to be green is a unit suite nobody runs.
 */
import { describe, expect, it } from "vitest";

import { ConsoleApiError, createClient } from "../src/api/client";
import { buildGraph } from "../src/model/graph";
import { castInputFrom } from "../src/views/useGraph";

const BASE = process.env.AXRANGE_LIVE_API;
const IMPRINT = process.env.AXRANGE_LIVE_IMPRINT ?? "corpapcn-golden";

describe.skipIf(!BASE)(`the live API at ${BASE ?? "(unset)"}`, () => {
  const api = createClient({ baseUrl: BASE ?? "" });

  function require_(value: unknown, name: string, kind: string): void {
    expect(value, `${name} is missing or not ${kind}`).not.toBeUndefined();
    if (kind !== "nullable") expect(typeof value, name).toBe(kind);
  }

  it("the catalog listing matches ImprintList", async () => {
    const catalog = await api.listImprints();
    expect(Array.isArray(catalog.imprints)).toBe(true);
    expect(Array.isArray(catalog.skipped)).toBe(true);
    for (const item of catalog.imprints) {
      require_(item.id, "id", "string");
      require_(item.hosts, "hosts", "number");
      require_(item.segments, "segments", "number");
      require_(item.as_of, "as_of", "string");
      require_(item.mean_confidence, "mean_confidence", "number");
      require_(item.has_provenance, "has_provenance", "boolean");
      require_(item.confidence_source, "confidence_source", "string");
    }
    for (const skip of catalog.skipped) {
      require_(skip.id, "skipped.id", "string");
      require_(skip.reason, "skipped.reason", "string");
    }
  });

  it("the cast summary matches CastSummary", async () => {
    const cast = await api.getCast(IMPRINT);
    for (const [name, kind] of [
      ["id", "string"], ["bundle_dir", "string"], ["twin_config_id", "string"],
      ["deploy_min_occupancy", "number"], ["deploy_min_occupancy_source", "string"],
      ["segments", "number"], ["node_specs", "number"], ["vms", "number"],
      ["hosts", "number"], ["conduits", "number"], ["observed_nodes", "number"],
      ["observed_edges", "number"], ["observed_segments", "number"],
      ["has_provenance", "boolean"], ["has_topology_export", "boolean"],
      ["has_assertions", "boolean"], ["has_layout", "boolean"],
      ["sidecar_shape", "string"],
    ] as const) {
      require_((cast as unknown as Record<string, unknown>)[name], name, kind);
    }
    expect(Array.isArray(cast.warnings)).toBe(true);
    // Absent is null, never a fabricated zero (the API's house rule).
    for (const nullable of [cast.code_fingerprint, cast.bundle_schema, cast.pit_id]) {
      expect(nullable === null || typeof nullable === "string").toBe(true);
    }
    // R5 (GAP-079): the roster behind the count and each router's legs --
    // the newest fields on the seam, so the least verified until this runs
    // against a real server (LES-258). Optional on the wire (an older server
    // omits them), but THIS server must publish both.
    expect(Array.isArray(cast.conduit_roster)).toBe(true);
    expect(cast.conduit_roster).toHaveLength(cast.conduits);
    for (const c of cast.conduit_roster ?? []) {
      require_(c.from, "conduit_roster[].from", "string");
      require_(c.to, "conduit_roster[].to", "string");
    }
    expect(Array.isArray(cast.routers)).toBe(true);
    for (const r of cast.routers ?? []) {
      require_(r.name, "routers[].name", "string");
      expect(Array.isArray(r.segments)).toBe(true);
      for (const s of r.segments) require_(s, "routers[].segments[]", "string");
      expect(new Set(r.segments).size).toBe(r.segments.length);
    }
    // S5.1.a's fix round (A1): the SEGMENT roster behind the count -- optional
    // on the wire like the two above, but a server with the field publishes one
    // entry per segment.
    expect(Array.isArray(cast.segment_roster)).toBe(true);
    expect(cast.segment_roster).toHaveLength(cast.segments);
    for (const g of cast.segment_roster ?? []) {
      require_(g.name, "segment_roster[].name", "string");
      require_(g.cidr, "segment_roster[].cidr", "string");
      expect(g.zone_type === null || typeof g.zone_type === "string").toBe(true);
    }
  });

  it("the roster and the legs ARRIVE in the graph the live map draws (R5)", async () => {
    const [topo, cast] = await Promise.all([api.getTopology(IMPRINT), api.getCast(IMPRINT)]);
    const graph = buildGraph(topo, castInputFrom(cast, IMPRINT));
    expect(graph.imprintId).toBe(IMPRINT);
    expect(graph.castOmits).toEqual([]);
    expect(graph.routerSegmentNames).toEqual(
      [...new Set((cast.routers ?? []).flatMap((r) => r.segments))].sort(),
    );
    const roster = cast.conduit_roster ?? [];
    if (roster.length > 0) {
      // Every conduit either resolves to a pair of tiles or names its
      // unresolvable end -- none silently vanishes on its way to the band.
      expect(graph.conduits.length).toBeGreaterThan(0);
      const ends = new Set(roster.flatMap((c) => [c.from, c.to]));
      for (const end of ends) {
        expect(
          graph.segmentByName.has(end) || graph.unresolvedConduitEndpoints.includes(end),
          `conduit end ${end} neither resolved nor reported`,
        ).toBe(true);
      }
    }
  });

  it("every listed imprint SUMMARIZES under its canonical id (R7, GAP-082)", async () => {
    const catalog = await api.listImprints();
    for (const item of catalog.imprints) {
      const summary = await api.getImprint(item.id);
      expect(summary.id).toBe(item.id);
      expect(summary.hosts).toBe(item.hosts);
      expect(summary.segments).toBe(item.segments);
      require_(summary.sidecar_shape, "summary.sidecar_shape", "string");
      require_(summary.confidence_source, "summary.confidence_source", "string");
      expect(Array.isArray(summary.warnings)).toBe(true);
    }
  });

  it("the topology export matches TopologyExport", async () => {
    const topo = await api.getTopology(IMPRINT);
    require_(topo.imprint_id, "imprint_id", "string");
    expect(Array.isArray(topo.nodes)).toBe(true);
    expect(Array.isArray(topo.edges)).toBe(true);
    expect(Array.isArray(topo.segments)).toBe(true);
    require_(topo.census.entities_total, "census.entities_total", "number");
    require_(topo.census.with_role, "census.with_role", "number");
    require_(topo.census.placed, "census.placed", "number");
    require_(topo.census.local, "census.local", "number");
    const node = topo.nodes[0];
    if (node) {
      require_(node.id, "node.id", "string");
      expect(Array.isArray(node.listen_ports)).toBe(true);
      expect(Array.isArray(node.dial_ports)).toBe(true);
    }
  });

  it("provenance matches ImprintProvenance, population included", async () => {
    const prov = await api.getProvenance(IMPRINT);
    require_(prov.sidecar_shape, "sidecar_shape", "string");
    require_(prov.evidence_source, "evidence_source", "string");
    expect(Array.isArray(prov.rows)).toBe(true);
    expect(typeof prov.population).toBe("object");
    const row = prov.rows[0];
    if (row) {
      require_(row.field, "row.field", "string");
      require_(row.evidence_class, "row.evidence_class", "string");
      require_(row.confidence, "row.confidence", "number");
      require_(row.has_confidence, "row.has_confidence", "boolean");
    }
    // The §12.6 authority, when the bundle has a canonical sidecar.
    if (prov.sidecar_shape === "canonical") {
      expect(Array.isArray(prov.population.suppressed)).toBe(true);
    }
  });

  it("the two operator files read as empty documents before the first save", async () => {
    const assertions = await api.getAssertions(IMPRINT);
    require_(assertions.schema, "schema", "string");
    require_(assertions.imprint, "imprint", "string");
    require_(assertions.exists, "exists", "boolean");
    expect(Array.isArray(assertions.add)).toBe(true);
    const layout = await api.getLayout(IMPRINT);
    require_(layout.schema, "layout.schema", "string");
    expect(typeof layout.pins).toBe("object");
  });

  it("maps the real server's 404 for an unknown `since=` fingerprint", async () => {
    // Only apply-produced fingerprints are diffable; the server 404s and
    // names the known set. This is the mapping under a real response.
    const error = (await api
      .getDiff(IMPRINT, "definitely-not-a-fingerprint")
      .catch((e: unknown) => e)) as ConsoleApiError;
    expect(error).toBeInstanceOf(ConsoleApiError);
    expect(error.kind).toBe("not-found");
    expect(error.message).toMatch(/apply/);
  });
});
