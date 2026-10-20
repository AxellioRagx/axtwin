// @vitest-environment node
/**
 * The layout contract (`docs/WORK_ORDER_console_canvas.md` §5), as tests:
 *
 *   1. **Determinism** -- `layout0` twice on one input is byte-identical.
 *   2. **Locality** -- adding one segment to a site changes only that site.
 *   3. **Budget** -- every level stays inside `15` §3's budget or collapses.
 *   4. **Goldens** -- committed coordinate snapshots per level, regenerated
 *      ONLY by `npm run layout:golden` (which sets `WRITE_GOLDEN`), never on a
 *      plain `npm run test`.
 *
 * The goldens live in `test/layout/*.golden.json`. In `WRITE_GOLDEN` mode this
 * file writes them and asserts nothing about their prior contents; otherwise
 * it reads them back and compares byte for byte.
 */
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import { buildGraph } from "../src/model/graph";
import { layout0 } from "../src/layout/level0";
import { layout1 } from "../src/layout/level1";
import { matrix0, matrixSegment } from "../src/layout/matrix";
import {
  BUDGET_LEVEL0,
  BUDGET_LEVEL1_BAND_CHIPS,
  BUDGET_LEVEL1_BANDS,
  BUDGET_MATRIX_AXIS,
} from "../src/model/budgets";
import { CANVAS_PAD, RAIL_GAP_X, RAIL_W } from "../src/layout/geometry";
import { TIERS } from "../src/layout/tiers";
import { buildGoldens, goldenText } from "./golden-data";
import { noConnectorCrossesATile } from "./layout/helpers";
import {
  trimmedCast,
  trimmedExport,
  withAddedSegment,
  withColumnAddingSegment,
  withOverBudgetSite,
} from "./fixtures/trimmed";

const GOLDEN_DIR = fileURLToPath(new URL("./layout/", import.meta.url));
const WRITE = !!process.env["WRITE_GOLDEN"];

function goldenPath(name: string): string {
  return `${GOLDEN_DIR}${name}.golden.json`;
}

describe("layout goldens (§5.4)", () => {
  const goldens = buildGoldens();

  if (WRITE) {
    it("writes every golden", () => {
      if (!existsSync(GOLDEN_DIR)) mkdirSync(GOLDEN_DIR, { recursive: true });
      for (const [name, value] of Object.entries(goldens)) {
        writeFileSync(goldenPath(name), goldenText(value));
      }
      expect(Object.keys(goldens).length).toBeGreaterThan(0);
    });
  } else {
    for (const [name, value] of Object.entries(goldens)) {
      it(`matches ${name}`, () => {
        const path = goldenPath(name);
        expect(existsSync(path), `${path} missing -- run \`npm run layout:golden\``).toBe(true);
        expect(goldenText(value)).toBe(readFileSync(path, "utf8"));
      });
    }
  }
});

// §1 property 2, checked on EVERY level-0 golden from R2 on (§6). It is a
// computed assertion rather than a look at a screenshot: "nothing crosses a
// tile" is exactly the claim that holds on the fixture someone drew and fails
// on the bundle that ships.
describe("no connector crosses a tile (§1.2, every level-0 golden)", () => {
  it("level0", () => {
    expect(noConnectorCrossesATile(layout0(buildGraph(trimmedExport, trimmedCast)))).toEqual([]);
  });

  it("level0-added-column", () => {
    expect(
      noConnectorCrossesATile(layout0(buildGraph(withColumnAddingSegment(), trimmedCast))),
    ).toEqual([]);
  });

  // R6: the budget changes WHICH tiles exist, so it changes what every
  // connector anchored to one of them touches. Both goldens, and the expanded
  // state that has no golden of its own.
  it("level0-collapsed (and its `?order=cidr` sibling)", () => {
    const graph = buildGraph(withOverBudgetSite(), trimmedCast);
    expect(noConnectorCrossesATile(layout0(graph))).toEqual([]);
    expect(noConnectorCrossesATile(layout0(graph, {}, { order: "cidr" }))).toEqual([]);
  });

  it("level0-collapsed, EXPANDED", () => {
    const graph = buildGraph(withOverBudgetSite(), trimmedCast);
    expect(
      noConnectorCrossesATile(layout0(graph, {}, { expanded: ["site:10.80"] })),
    ).toEqual([]);
  });
});

describe("determinism (§5.1)", () => {
  it("layout0 twice on one input is byte-identical", () => {
    const graph = buildGraph(trimmedExport, trimmedCast);
    expect(goldenText(layout0(graph))).toBe(goldenText(layout0(graph)));
  });

  it("a rebuilt graph yields the same layout", () => {
    const a = layout0(buildGraph(trimmedExport, trimmedCast));
    const b = layout0(buildGraph(trimmedExport, trimmedCast));
    expect(goldenText(a)).toBe(goldenText(b));
  });
});

describe("locality (§5.2)", () => {
  it("adding one segment to a site leaves every other element's coords equal", () => {
    // **R6 narrowed what "column-preserving" means for THIS fixture, and the
    // test says so rather than pretending otherwise.** A group showing two
    // tiles is now two columns wide, so a third tile starts a second ROW --
    // a row-adding edit, which `layout0`'s own docstring has always excluded
    // from the locality guarantee. Every tile, every site box and all the
    // chrome still hold still; the east-west band moves down with the taller
    // column, and that is asserted below instead of being claimed away.
    const before = layout0(buildGraph(trimmedExport, trimmedCast));
    const after = layout0(buildGraph(withAddedSegment(), trimmedCast));

    // The chrome does not move.
    expect(after.internet).toEqual(before.internet);
    expect(after.wanEdge).toEqual(before.wanEdge);
    expect(after.width).toBe(before.width);
    expect(after.infraDevices.map((d) => d.box)).toEqual(before.infraDevices.map((d) => d.box));

    // Every tile that existed before is at the SAME coordinates after.
    const afterByCidr = new Map(after.tiles.map((t) => [t.segment.observedCidr, t.box]));
    for (const t of before.tiles) {
      expect(afterByCidr.get(t.segment.observedCidr), t.segment.observedCidr).toEqual(t.box);
    }
    // Exactly one new tile, and it is the added segment.
    const added = after.tiles.filter(
      (t) => !before.tiles.some((b) => b.segment.observedCidr === t.segment.observedCidr),
    );
    expect(added.map((t) => t.segment.observedCidr)).toEqual(["10.80.90.0/24"]);
    // The new tile is in the 10.80 site it was added to, and it opened a second
    // ROW there (R6: two across for a group this size), which is what moves the
    // band. The arcs' ANCHORS are unchanged -- every tile and site box above is
    // byte-identical -- so what moves is the band's depth, not an endpoint.
    expect(added[0]!.siteKey).toBe("site:10.80");
    expect(added[0]!.box.y).toBeGreaterThan(before.tiles[0]!.box.y);
    // The same PAIRS are drawn, in the same order -- nothing about the band's
    // content changed. What moved is where the 10.80 column ENDS, which is
    // where its arcs are anchored, and with it the band beneath: a taller
    // column is exactly what a second row means.
    expect(after.arcs.map((a) => [a.a, a.b])).toEqual(before.arcs.map((a) => [a.a, a.b]));
    expect(after.arcs.map((a) => a.c1.y)).not.toEqual(before.arcs.map((a) => a.c1.y));
    const band = (l: typeof before) => l.rails.find((r) => r.tier === "east-west")!.box.y;
    expect(band(after)).toBeGreaterThan(band(before));
  });

  it("adding a segment to an OVER-BUDGET group moves nothing at all (R6)", () => {
    // The budget's own locality, and it is stronger than the row-grid one: a
    // group already showing its busiest six absorbs a new segment into its
    // `+N more` count, so not one coordinate on the plane changes.
    const base = buildGraph(withOverBudgetSite(), trimmedCast);
    const grown = withOverBudgetSite();
    const before = layout0(base);
    const after = layout0(
      buildGraph(
        {
          ...grown,
          segments: [
            ...grown.segments,
            { cidr: "10.80.95.0/24", name: "seg-10-80-95-0", entities: 0, coverage_source: "inferred_from_traffic" },
          ],
        },
        trimmedCast,
      ),
    );
    expect(after.tiles.map((t) => t.box)).toEqual(before.tiles.map((t) => t.box));
    expect(after.sites.map((s) => s.box)).toEqual(before.sites.map((s) => s.box));
    expect(after.arcs).toEqual(before.arcs);
    expect(after.width).toBe(before.width);
    expect(after.height).toBe(before.height);
    // The one thing that changes is the count the tile discloses.
    expect(after.hiddenSegments).toBe(before.hiddenSegments + 1);
  });

  it("a column-COUNT-changing edit is NOT local -- the chrome and downstream columns move (baseline)", () => {
    // The corrected §5.2: a new /16 forms a new site column, so the width
    // grows, the centred chrome recentres, and the misc column past it shifts.
    // This is pins territory (S4), not a locality guarantee; the assertion
    // documents the reflow rather than forbidding it.
    const before = layout0(buildGraph(trimmedExport, trimmedCast));
    const after = layout0(buildGraph(withColumnAddingSegment(), trimmedCast));
    expect(after.tiles.length).toBe(before.tiles.length + 2);
    expect(after.width).toBeGreaterThan(before.width);
    expect(after.internet.x).not.toBe(before.internet.x); // chrome recentred
    // The misc column's tiles moved right to make room for the new column.
    const miscBefore = before.tiles.find((t) => t.segment.observedCidr === "192.168.0.0/24")!;
    const miscAfter = after.tiles.find((t) => t.segment.observedCidr === "192.168.0.0/24")!;
    expect(miscAfter.box.x).not.toBe(miscBefore.box.x);
  });
});

describe("budget (§5.3)", () => {
  const graph = buildGraph(trimmedExport, trimmedCast);

  it("level 0 stays within the box budget", () => {
    const l0 = layout0(graph);
    const boxes =
      l0.tiles.length +
      l0.infraDevices.length +
      1 + // internet
      (l0.wanEdge ? 1 : 0) +
      (l0.unplaced ? 1 : 0);
    expect(boxes).toBeLessThanOrEqual(BUDGET_LEVEL0);
  });

  it("level 1 bands stay within budget or collapse", () => {
    for (const cidr of ["10.80.20.0/24", "10.80.72.0/24", "10.20.193.0/24"]) {
      const l1 = layout1(graph, cidr)!;
      expect(l1.bands.length, cidr).toBeLessThanOrEqual(BUDGET_LEVEL1_BANDS);
      for (const b of l1.bands) {
        if (!b.collapsed) {
          expect(b.chips.length, `${cidr} ${b.role}`).toBeLessThanOrEqual(
            BUDGET_LEVEL1_BAND_CHIPS,
          );
        }
      }
    }
  });

  it("a band over the chip budget collapses by rung (O6, LES-185)", () => {
    // 10.80.72.0/24: 5 live generic-endpoint + 22 ghosts -> the
    // below-threshold band exceeds 15 chips and collapses into rungs.
    const l1 = layout1(graph, "10.80.72.0/24")!;
    const below = l1.bands.find((b) => b.role === "below-threshold")!;
    expect(below.count).toBe(22);
    expect(below.collapsed).toBe(true);
    expect(below.chips).toHaveLength(0);
    expect(below.rungs).toEqual([
      { rung: "regular", count: 4 },
      { rung: "transient", count: 18 },
    ]);
  });

  it("a live role band over the budget collapses too", () => {
    // 10.20.193.0/24: 18 live corp-workstations -> the role band collapses.
    const l1 = layout1(graph, "10.20.193.0/24")!;
    const ws = l1.bands.find((b) => b.role === "corp-workstation")!;
    expect(ws.count).toBe(18);
    expect(ws.collapsed).toBe(true);
    expect(ws.rungs).toEqual([{ rung: "core", count: 18 }]);
  });

  it("the matrix axis stays within 40x40", () => {
    const m = matrix0(graph);
    expect(m.overBudget).toBe(false);
    expect(m.axisCount).toBeLessThanOrEqual(BUDGET_MATRIX_AXIS);
    const seg = matrixSegment(graph, "10.80.72.0/24")!;
    expect(seg.axisCount).toBeLessThanOrEqual(BUDGET_MATRIX_AXIS);
  });
});

// ===========================================================================
// R1 -- tier rails (`docs/WORK_ORDER_console_level0_rendering.md` §3 R1). The
// rails are in `level0.golden.json` (they are part of `layout0`'s output); the
// properties the golden CANNOT state -- the order, the absence of overlap, the
// gutter every other element respects -- are computed here.
// ===========================================================================
describe("tier rails (R1)", () => {
  const l0 = layout0(buildGraph(trimmedExport, trimmedCast));

  it("emits one rail per tier, in TIERS order", () => {
    expect(l0.rails.map((r) => r.tier)).toEqual([...TIERS]);
  });

  it("stacks them top to bottom with no overlap", () => {
    for (let i = 1; i < l0.rails.length; i++) {
      const above = l0.rails[i - 1]!;
      const below = l0.rails[i]!;
      expect(
        above.box.y + above.box.h,
        `${above.tier} overlaps ${below.tier}`,
      ).toBeLessThanOrEqual(below.box.y);
    }
  });

  it("puts every rail in the left gutter, at one width", () => {
    for (const rail of l0.rails) {
      expect(rail.box.x, rail.tier).toBe(CANVAS_PAD);
      expect(rail.box.w, rail.tier).toBe(RAIL_W - RAIL_GAP_X);
    }
  });

  it("the segment rail spans the FULL tile extent (not one band height)", () => {
    const rail = l0.rails.find((r) => r.tier === "segment")!;
    const top = Math.min(...l0.tiles.map((t) => t.box.y));
    const bottom = Math.max(...l0.tiles.map((t) => t.box.y + t.box.h));
    expect(rail.box.y).toBeLessThanOrEqual(top);
    expect(rail.box.y + rail.box.h).toBeGreaterThanOrEqual(bottom);
  });

  it("the east-west rail spans the arc band (R4)", () => {
    const rail = l0.rails.find((r) => r.tier === "east-west")!;
    // The deepest point of a symmetric cubic is its control depth, so the
    // control points bound the band without flattening anything.
    const arcYs = l0.arcs.flatMap((arc) => [arc.from.y, arc.c1.y, arc.c2.y, arc.to.y]);
    expect(l0.arcs.length).toBeGreaterThan(0); // the fixture has three
    expect(rail.box.h).toBeGreaterThan(0);
    expect(rail.box.y + rail.box.h).toBeGreaterThanOrEqual(Math.max(...arcYs));
  });

  it("a tier with no CONTENT has a zero-height rail, not a reserved one (S4)", () => {
    // A band's reserved height is not evidence that anything is in it: a cast
    // with no router and an export with no infrastructure devices must not
    // draw WAN EDGE and INFRASTRUCTURE rails over empty space.
    // Drop exactly the nodes the graph itself counts as infrastructure
    // devices -- naming roles here would guess at the fixture's content and
    // silently leave one behind (it did: `monitoring-collector`).
    const base = buildGraph(trimmedExport, trimmedCast);
    const infraIds = new Set(base.infraDevices.map((d) => d.id));
    // R2: the WAN-EDGE tier has content whenever the twin has an external edge
    // AT ALL -- DECLARED by a router leg or INFERRED from observed crossing
    // traffic -- so emptying it now means dropping the non-local population
    // (and the edges reaching it) as well as the routers. Dropping only the
    // routers leaves an inferred edge, which is R2 working, not a rail bug;
    // the inferred case has its own coverage in `r2.trunk.test.ts`.
    const externalIds = new Set(base.nonLocal.map((n) => n.id));
    const dropped = new Set([...infraIds, ...externalIds]);
    // R4 widened the EAST-WEST tier the same way (LES-299): its content used
    // to be "the cast published conduits", and is now "conduits OR observed
    // local↔local traffic". Dropping the roster alone therefore leaves the
    // band full of OBSERVED arcs -- which is R4 working, not a rail bug -- so
    // emptying this tier now means dropping the edges too. The observed case
    // has its own coverage in `r4.arcs.test.ts`.
    const bare = layout0(
      buildGraph(
        {
          ...trimmedExport,
          nodes: trimmedExport.nodes.filter((n) => !dropped.has(n.id)),
          edges: [],
        },
        { ...trimmedCast, conduits: [], routers: [] },
      ),
    );
    const heightOf = (tier: string): number =>
      bare.rails.find((r) => r.tier === tier)!.box.h;
    expect(bare.wanEdge).toBeNull();
    expect(bare.infraDevices).toHaveLength(0);
    expect(heightOf("wan-edge")).toBe(0);
    expect(heightOf("infrastructure")).toBe(0);
    expect(heightOf("east-west")).toBe(0);
    // The tiers that DO have content are unaffected.
    expect(heightOf("internet")).toBeGreaterThan(0);
    expect(heightOf("segment")).toBeGreaterThan(0);
    // Still a total function of TIERS -- five rails, in order.
    expect(bare.rails.map((r) => r.tier)).toEqual([...TIERS]);
  });

  it("NOTHING the diagram draws intrudes into the gutter", () => {
    // The rail labels are only readable because this holds -- it is the
    // reason the whole plane shifted right by RAIL_W in this slice.
    const contentX = CANVAS_PAD + RAIL_W;
    const boxes = [
      l0.internet,
      ...(l0.wanEdge ? [l0.wanEdge] : []),
      ...(l0.unplaced ? [l0.unplaced.box] : []),
      ...l0.infraDevices.map((d) => d.box),
      ...l0.sites.map((s) => s.box),
      ...l0.tiles.map((t) => t.box),
    ];
    for (const box of boxes) {
      expect(box.x).toBeGreaterThanOrEqual(contentX);
    }
  });
});
