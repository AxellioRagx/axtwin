// @vitest-environment node
/**
 * The DECLARED-design fallback (Unified Console design/16 rule 1), golden-
 * tested like the capture-backed layouts: the real hand-authored corp-twin's
 * `GET /api/imprints/corp-twin/cast` (`fixtures/corp-twin-cast.json`, captured
 * read-only from :8080 -- see its `_provenance`), assembled through the SAME
 * seam the app uses (`castInputFrom` + `declaredExport` + `buildGraph`, LES-311)
 * and laid out by the SAME engine. Goldens: `test/layout/declared-*.golden.json`,
 * written only with `WRITE_GOLDEN=1 npx vitest run test/declared.golden.test.ts`.
 */
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import type { CastSummary } from "../src/api/types";
import { layout0 } from "../src/layout/level0";
import { layout1 } from "../src/layout/level1";
import { DECLARED_CLASS, declaredExport, isHandAuthored } from "../src/model/declared";
import { buildGraph } from "../src/model/graph";
import { castInputFrom } from "../src/views/useGraph";
import { goldenText } from "./golden-data";
import { noConnectorCrossesATile } from "./layout/helpers";

const cast = JSON.parse(
  readFileSync(fileURLToPath(new URL("./fixtures/corp-twin-cast.json", import.meta.url)), "utf8"),
) as CastSummary;
const GOLDEN_DIR = fileURLToPath(new URL("./layout/", import.meta.url));
const WRITE = !!process.env["WRITE_GOLDEN"];

const doc = declaredExport(cast)!;
const graph = buildGraph(doc, castInputFrom(cast, "corp-twin"));
const busiest = [...doc.segments].sort((a, b) => b.entities - a.entities || a.cidr.localeCompare(b.cidr))[0]!;

const goldens: Record<string, unknown> = {
  "declared-corp-twin-level0": layout0(graph),
  [`declared-corp-twin-level1-${busiest.cidr.replace("/", "-")}`]: layout1(graph, busiest.cidr),
};

describe("declared design -- the real corp-twin cast", () => {
  it("is hand-authored, and every declared host and segment is drawn", () => {
    expect(isHandAuthored(cast)).toBe(true);
    const routers = (cast.node_roster ?? []).filter((n) => n.kind === "router");
    const hosts = (cast.node_roster ?? []).filter((n) => n.kind !== "router");
    expect(doc.nodes).toHaveLength(hosts.reduce((n, s) => n + s.count, 0));
    expect(doc.nodes.length + routers.reduce((n, s) => n + s.count, 0)).toBe(cast.vms);
    expect(graph.segments).toHaveLength(cast.segments);
    expect(graph.routers.map((r) => r.name)).toEqual(cast.routers!.map((r) => r.name));
  });

  it("nothing in it is observed: one class, `declared`, and no traffic", () => {
    for (const n of doc.nodes) {
      expect(n.segment_evidence_class).toBe(DECLARED_CLASS);
      expect([DECLARED_CLASS, null]).toContain(n.role_evidence_class);
    }
    expect(doc.edges).toEqual([]);
    expect(graph.trafficPairs).toEqual([]);
    // Every declared host would deploy (no observation, no gate).
    expect(graph.segments.reduce((n, s) => n + s.ghostCount, 0)).toBe(0);
  });

  it("the declared conduits are the east-west band", () => {
    expect(graph.conduits.length).toBeGreaterThan(0);
    expect(graph.conduits.length).toBeLessThanOrEqual(cast.conduits);
  });

  it("no connector crosses a tile", () => {
    expect(noConnectorCrossesATile(layout0(graph))).toEqual([]);
  });

  it("is deterministic", () => {
    const again = buildGraph(declaredExport(cast)!, castInputFrom(cast, "corp-twin"));
    expect(goldenText(layout0(again))).toBe(goldenText(goldens["declared-corp-twin-level0"]));
  });

  if (WRITE) {
    it("writes the declared goldens", () => {
      for (const [name, value] of Object.entries(goldens)) writeFileSync(`${GOLDEN_DIR}${name}.golden.json`, goldenText(value));
    });
  } else {
    for (const [name, value] of Object.entries(goldens)) {
      it(`matches ${name}`, () => {
        const path = `${GOLDEN_DIR}${name}.golden.json`;
        expect(existsSync(path), `${path} missing -- WRITE_GOLDEN=1 npx vitest run test/declared.golden.test.ts`).toBe(true);
        expect(goldenText(value)).toBe(readFileSync(path, "utf8"));
      });
    }
  }
});

describe("declaredExport's edges", () => {
  it("answers null when the server publishes no segment roster", () => {
    expect(declaredExport({ ...cast, segment_roster: undefined } as unknown as CastSummary)).toBeNull();
  });

  it("an older server with no node roster still draws the segments, empty", () => {
    const d = declaredExport({ ...cast, node_roster: undefined })!;
    expect(d.nodes).toEqual([]);
    expect(d.segments).toHaveLength(cast.segments);
  });
});
