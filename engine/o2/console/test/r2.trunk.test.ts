// @vitest-environment node
/**
 * R2 — the trunk (`docs/WORK_ORDER_console_level0_rendering.md` §3 R2), the
 * pure half: north-south aggregation, the inferred WAN edge, the shared
 * volume→width scale, the trunk geometry, the plane stamp, and §1's property 2
 * (`noConnectorCrossesATile`) over both committed level-0 goldens.
 *
 * `node` environment because two blocks read committed files off disk: under
 * `jsdom`, `import.meta.url` resolves against the document's
 * `http://localhost/` and a path built from it escapes the package (LES-260).
 *
 * The RENDER half — the tap badge's verbatim vantage, the leg labels, the
 * stale-pin note — is `r2.ui.test.tsx`.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import { buildGraph } from "../src/model/graph";
import { layout0 } from "../src/layout/level0";
import { layoutTrunk, LEG_LABEL_MIN_SHARE } from "../src/layout/trunk";
import {
  HAIRLINE,
  MIN_STROKE,
  STROKE_RANGE,
  normalizedLog10,
  strokeForBytes,
  volumeRange,
} from "../src/layout/scale";
import {
  PLANE_CONSTANTS,
  PLANE_EPOCH,
  PLANE_STAMP,
  pinPlaneStatus,
  planeCanonicalString,
  planeForSave,
  planeHash,
} from "../src/layout/plane";
import { TRUNK_BUS_GAP_Y } from "../src/layout/geometry";
import type { Pins } from "../src/layout/pins";
import { noConnectorCrossesATile, polylineCrossesBox } from "./layout/helpers";
import { trimmedCast, trimmedExport } from "./fixtures/trimmed";

const graph = buildGraph(trimmedExport, trimmedCast);

/** The fixture's three crossing edges, by hand from `fixtures/trimmed.ts`:
 *    10.80.72.200 → 8.8.8.8   80 B / 7 flows
 *    10.20.193.20 → 8.8.8.8   60 B / 9 flows
 *    8.8.8.8 → 10.80.72.200  5000 B / 1 flow
 *  Everything else is local↔local (east-west, R4's band) or has no segment. */
const NS_7280 = { bytes: 5080, flows: 8 };
const NS_193 = { bytes: 60, flows: 9 };

// ===========================================================================
// model/graph.ts -- north-south aggregation
// ===========================================================================
describe("north-south aggregation (R2)", () => {
  it("sums only the edges with exactly ONE local end, onto that end's segment", () => {
    const by = (cidr: string) => graph.segmentByCidr.get(cidr)!.northSouth;
    expect(by("10.80.72.0/24")).toEqual(NS_7280);
    expect(by("10.20.193.0/24")).toEqual(NS_193);
    // 10.80.20.0/24 carries the fixture's BUSIEST edges (1200 B + 900 B to the
    // DC) and they are all local↔local, so its north-south is zero: the axis
    // is traffic across the twin's edge, not traffic volume.
    expect(by("10.80.20.0/24")).toEqual({ bytes: 0, flows: 0 });
    expect(by("10.20.192.0/24")).toEqual({ bytes: 0, flows: 0 });
  });

  it("rolls up to the site groups that carry the trunk legs", () => {
    const sites = new Map(layout0(graph).sites.map((sp) => [sp.site.key, sp.site.northSouth]));
    expect(sites.get("site:10.80")).toEqual(NS_7280);
    expect(sites.get("site:10.20")).toEqual(NS_193);
    // A group with no crossing traffic at all -- the hairline-leg case.
    expect(sites.get("site:misc")).toEqual({ bytes: 0, flows: 0 });
  });

  it("the WAN edge's total is NOT the sum of the legs when a local end is unplaced", () => {
    // The fixture's unplaced locals have no crossing edges, so the two agree
    // here; the contract is that the total is published separately and the
    // difference is recoverable rather than dropped.
    const legTotal = layout0(graph).trunk!.legs.reduce((s, l) => s + l.bytes, 0);
    expect(graph.wanEdge!.bytes).toBe(legTotal);
    expect(graph.wanEdge!.crossingEdges).toBe(3);
    expect(graph.wanEdge!.flows).toBe(NS_7280.flows + NS_193.flows);
  });
});

describe("the WAN edge: declared, inferred, or absent (R2)", () => {
  it("is DECLARED (not inferred) when the cast publishes router legs", () => {
    expect(graph.wanEdge).toEqual({
      inferred: false,
      evidenceClass: "observed",
      bytes: 5140,
      flows: 17,
      crossingEdges: 3,
    });
  });

  it("is INFERRED when the traffic crosses but no router is published", () => {
    const noRouter = buildGraph(trimmedExport, { ...trimmedCast, routers: [] });
    expect(noRouter.wanEdge?.inferred).toBe(true);
    // The class is the EDGES' own, never one invented for the box.
    expect(noRouter.wanEdge?.evidenceClass).toBe("observed");
    expect(layout0(noRouter).wanEdge).not.toBeNull();
  });

  it("is ABSENT when nothing crosses and nothing is declared -- no box, no trunk, no tap", () => {
    const bare = layout0(buildGraph(localOnlyExport(), { ...trimmedCast, routers: [] }));
    expect(bare.wanEdge).toBeNull();
    expect(bare.trunk).toBeNull();
    expect(bare.tap).toBeNull();
    // Nothing was invented in its place: the tiles are still there.
    expect(bare.tiles.length).toBeGreaterThan(0);
  });

  it("is drawn for a DECLARED gateway even with no crossing traffic at all", () => {
    // A twin whose gateway is silent in this window still HAS a gateway.
    const declared = buildGraph(localOnlyExport(), trimmedCast);
    expect(declared.wanEdge).toEqual({
      inferred: false, evidenceClass: null, bytes: 0, flows: 0, crossingEdges: 0,
    });
    const l0 = layout0(declared);
    expect(l0.trunk).not.toBeNull();
    expect(l0.trunk!.strokeWidth).toBe(HAIRLINE);
    expect(l0.trunk!.legs.every((leg) => leg.strokeWidth === HAIRLINE)).toBe(true);
  });
});

/** The fixture with every NON-LOCAL node (and the edges touching them) removed
 *  -- a twin whose traffic never leaves it. Derived from the graph's own
 *  `nonLocal` set rather than by naming addresses, so it cannot silently miss
 *  one the fixture grows later. */
function localOnlyExport() {
  const external = new Set(graph.nonLocal.map((n) => n.id));
  return {
    ...trimmedExport,
    nodes: trimmedExport.nodes.filter((n) => !external.has(n.id)),
    edges: trimmedExport.edges.filter((e) => !external.has(e.src) && !external.has(e.dst)),
  };
}

// ===========================================================================
// layout/scale.ts -- the shared volume -> width rule (R4 reuses it)
// ===========================================================================
describe("volume → stroke width (layout/scale.ts)", () => {
  it("takes the range from the drawing's own values, ignoring zero", () => {
    expect(volumeRange([0, 60, 5080])).toEqual({ min: 60, max: 5080 });
    expect(volumeRange([])).toEqual({ min: 0, max: 0 });
    expect(volumeRange([0, 0])).toEqual({ min: 0, max: 0 });
    expect(volumeRange([-5, Number.NaN, 7])).toEqual({ min: 7, max: 7 });
  });

  it("spends the WHOLE band on the values present: smallest non-zero 1, largest 6", () => {
    const range = volumeRange([0, 60, 5080]);
    expect(strokeForBytes(0, range)).toBe(HAIRLINE);
    expect(strokeForBytes(60, range)).toBe(MIN_STROKE);
    expect(strokeForBytes(5080, range)).toBe(MIN_STROKE + STROKE_RANGE);
  });

  it("un-compresses what an absolute-zero normalisation squeezed (review S2)", () => {
    // The pit bundle's three busiest east-west pairs. Normalised against
    // `log10(1+max)` alone these came out 6.00 / 5.05 / 4.39 -- the whole
    // top-12 inside 0.66px, so §1 property 3 ("magnitude visible before any
    // label") failed across eleven of twelve arcs. R4 inherits this scale.
    const range = volumeRange([2_230_038_148, 37_366_034, 2_144_764]);
    expect(strokeForBytes(2_230_038_148, range)).toBe(MIN_STROKE + STROKE_RANGE);
    expect(strokeForBytes(37_366_034, range)).toBeCloseTo(3.06, 2);
    expect(strokeForBytes(2_144_764, range)).toBe(MIN_STROKE);
    // The spread across those three is now >2px where it was <1.7px.
    const spread =
      strokeForBytes(2_230_038_148, range) - strokeForBytes(2_144_764, range);
    expect(spread).toBe(STROKE_RANGE);
  });

  it("is monotonic across four orders of magnitude", () => {
    const range = volumeRange([1, 100, 10_000, 1_000_000]);
    const widths = [1, 100, 10_000, 1_000_000].map((b) => strokeForBytes(b, range));
    expect(widths).toEqual([...widths].sort((a, b) => a - b));
    expect(widths[1]).toBeGreaterThan(widths[0]!);
    for (const w of widths) expect(w).toBeGreaterThanOrEqual(MIN_STROKE);
  });

  it("treats ONE distinct magnitude as the maximum rather than dividing by zero", () => {
    const range = volumeRange([500, 500]);
    expect(strokeForBytes(500, range)).toBe(MIN_STROKE + STROKE_RANGE);
    expect(normalizedLog10(500, range)).toBe(1);
  });

  it("is total on degenerate input rather than NaN-wide", () => {
    const range = volumeRange([10, 1000]);
    expect(strokeForBytes(Number.NaN, range)).toBe(HAIRLINE);
    expect(strokeForBytes(-5, range)).toBe(HAIRLINE);
    expect(strokeForBytes(10, { min: 0, max: 0 })).toBe(MIN_STROKE);
    expect(strokeForBytes(10, { min: 0, max: Number.NaN })).toBe(MIN_STROKE);
    // Outside a stale range, clamped at both ends instead of drawn off-scale.
    expect(strokeForBytes(5_000_000, range)).toBe(MIN_STROKE + STROKE_RANGE);
    expect(strokeForBytes(1, range)).toBe(MIN_STROKE);
    expect(normalizedLog10(5_000_000, range)).toBe(1);
  });
});

// ===========================================================================
// layout/trunk.ts -- the geometry
// ===========================================================================
describe("the trunk (R2)", () => {
  const l0 = layout0(graph);
  const trunk = l0.trunk!;

  it("runs Internet → tap → WAN as ONE vertical, with the tap on it", () => {
    expect(trunk.spine).toHaveLength(2);
    expect(trunk.spine[0]!.x).toBe(trunk.spine[1]!.x);
    expect(trunk.spine[0]!.y).toBe(l0.internet.y + l0.internet.h);
    expect(trunk.spine[1]!.y).toBe(l0.wanEdge!.y);
    // The tap badge's centre is ON the spine, between the two.
    expect(l0.tap!.box.x + l0.tap!.box.w / 2).toBe(trunk.tapAnchor.x);
    expect(trunk.tapAnchor.x).toBe(trunk.spine[0]!.x);
    expect(trunk.tapAnchor.y).toBeGreaterThan(trunk.spine[0]!.y);
    expect(trunk.tapAnchor.y).toBeLessThan(trunk.spine[1]!.y);
  });

  it("carries the vantage and the window count VERBATIM from the export", () => {
    const vantage = "axellio-hq/corp-apcn-rdr1:12";
    const tap = layout0(buildGraph({ ...trimmedExport, vantage }, trimmedCast)).tap!;
    expect(tap.vantage).toBe(vantage);
    expect(tap.windows).toBe(trimmedExport.windows);
    // An export with no vantage says `null`, not `""`.
    expect(l0.tap!.vantage).toBeNull();
  });

  it("hangs ONE leg per site group, in site order, from a bus below the WAN box", () => {
    expect(trunk.legs.map((l) => l.siteKey)).toEqual(l0.sites.map((s) => s.site.key));
    // D1's v1 default: prefix order, `misc` last -- stability over salience.
    expect(trunk.legs.map((l) => l.siteKey)).toEqual(["site:10.20", "site:10.80", "site:misc"]);
    const busY = l0.wanEdge!.y + l0.wanEdge!.h + TRUNK_BUS_GAP_Y;
    expect(trunk.bus![0]!.y).toBe(busY);
    expect(trunk.bus![1]!.y).toBe(busY);
    expect(trunk.stem).toEqual([
      { x: trunk.spine[1]!.x, y: l0.wanEdge!.y + l0.wanEdge!.h },
      { x: trunk.spine[1]!.x, y: busY },
    ]);
  });

  it("ends every leg AT its group header's top-centre and enters NO group box", () => {
    for (const sp of l0.sites) {
      const leg = trunk.legs.find((l) => l.siteKey === sp.site.key)!;
      const end = leg.points[leg.points.length - 1]!;
      expect(end.x, sp.site.key).toBe(sp.header.x + sp.header.w / 2);
      expect(end.y, sp.site.key).toBe(sp.header.y);
      // Touching the top edge is not entering it.
      expect(polylineCrossesBox(leg.points, sp.box), sp.site.key).toBe(false);
    }
  });

  it("makes the busiest group identifiable by WIDTH alone, and gives an idle one a hairline", () => {
    const width = new Map(trunk.legs.map((l) => [l.siteKey, l.strokeWidth]));
    const widest = [...trunk.legs].sort((a, b) => b.strokeWidth - a.strokeWidth)[0]!;
    expect(widest.siteKey).toBe("site:10.80"); // 5,080 B vs 60 B vs 0
    expect(width.get("site:10.80")).toBe(MIN_STROKE + STROKE_RANGE);
    expect(width.get("site:misc")).toBe(HAIRLINE); // present, and visibly nothing
    // The smallest NON-ZERO leg is exactly MIN_STROKE: the band is spent on
    // the range this drawing contains (review S2), not on an absolute zero.
    expect(width.get("site:10.20")).toBe(MIN_STROKE);
    expect(width.get("site:10.20")!).toBeGreaterThan(HAIRLINE);
    expect(width.get("site:10.20")!).toBeLessThan(width.get("site:10.80")!);
  });

  it("labels a leg only above the share threshold -- and always the busiest", () => {
    const labelled = trunk.legs.filter((l) => l.labelled).map((l) => l.siteKey);
    expect(labelled).toEqual(["site:10.80"]);
    // 60 / 5080 = 1.2% -- under the 5% share, so it carries width but no label.
    expect(NS_193.bytes / NS_7280.bytes).toBeLessThan(LEG_LABEL_MIN_SHARE);
  });

  it("is a pure function of its inputs -- two calls are byte-identical", () => {
    const input = {
      internet: l0.internet,
      wanEdge: l0.wanEdge!,
      groups: l0.sites.map((sp) => ({
        siteKey: sp.site.key, header: sp.header,
        bytes: sp.site.northSouth.bytes, flows: sp.site.northSouth.flows,
      })),
      bytes: graph.wanEdge!.bytes,
      flows: graph.wanEdge!.flows,
    };
    expect(JSON.stringify(layoutTrunk(input))).toBe(JSON.stringify(layoutTrunk(input)));
  });

  it("draws no bus and no stem when there are no groups to reach", () => {
    const empty = layoutTrunk({
      internet: l0.internet, wanEdge: l0.wanEdge!, groups: [], bytes: 10, flows: 1,
    });
    expect(empty.legs).toEqual([]);
    expect(empty.bus).toBeNull();
    expect(empty.stem).toEqual([]);
    expect(empty.spine).toHaveLength(2); // the Internet→WAN link still exists
  });
});

// ===========================================================================
// §1 property 2 -- nothing crosses a tile (the helper every golden now calls)
// ===========================================================================
describe("no connector crosses a tile (§1.2)", () => {
  it("holds for the level-0 fixture, trunk and east-west arcs together", () => {
    expect(noConnectorCrossesATile(layout0(graph))).toEqual([]);
  });

  it("holds for EVERY connector family when a pin drops a site over the band", () => {
    // **This assertion tightened in R4, as designed.** R2 could only say "no
    // *trunk* violation; every violation is a lane", because the lanes it
    // shared the check with DID cross tiles at this pin (F4). R4 retired the
    // lane geometry for arcs anchored at the group's bottom edge, so the
    // promise the R2 form deferred is now kept outright: the whole drawing is
    // clean here, and the assertion is an exact `[]` rather than a statement
    // about which family the survivors belong to.
    //
    // What it does NOT claim is that pins are always clean. Both other
    // families keep a measured no-reflow limit at other coordinates -- the
    // infra stubs in `r3.infra.test.ts`, the arcs in `r4.arcs.test.ts`.
    //
    // **Re-aimed for R6's plane (x=900 → x=420).** The per-group budget made a
    // small group's column two tiles wide instead of three, so the whole plane
    // narrowed (1514 units from 2374) and every coordinate in these pin tests
    // names a different place than it did. Swept on the new plane over
    // x ∈ [140, 1400] step 20 × y ∈ {620, 300} (132 positions): **89 clean, 23
    // with a trunk violation, 36 with a stub violation**. x=420 is clean at
    // both y values; that is a coordinate, not a rule, which is why the
    // violating side is pinned in `r3.infra.test.ts` and `r4.arcs.test.ts`.
    const pins: Pins = { "site:10.80": { x: 420, y: 620 } };
    expect(noConnectorCrossesATile(layout0(graph, pins))).toEqual([]);
  });

  it("the helper DETECTS a crossing rather than always passing", () => {
    // The guard on the guard: a line straight down the middle of the plane
    // from the top must be reported, or the assertions above prove nothing.
    const l0 = layout0(graph);
    const tile = l0.tiles[0]!.box;
    const through = [
      { x: tile.x + tile.w / 2, y: tile.y - 50 },
      { x: tile.x + tile.w / 2, y: tile.y + tile.h + 50 },
    ];
    expect(polylineCrossesBox(through, tile)).toBe(true);
    // ...while a line that merely touches the top edge is not a crossing.
    expect(polylineCrossesBox([
      { x: tile.x + tile.w / 2, y: tile.y - 50 },
      { x: tile.x + tile.w / 2, y: tile.y },
    ], tile)).toBe(false);
  });
});

// ===========================================================================
// The COMMITTED goldens carry the trunk (deleting `trunk.ts`'s output fails)
// ===========================================================================
describe("the trunk is IN the committed level-0 goldens", () => {
  for (const name of ["level0", "level0-added-column"] as const) {
    it(`${name} carries a trunk, a tap and one leg per site`, () => {
      const path = fileURLToPath(new URL(`./layout/${name}.golden.json`, import.meta.url));
      const golden = JSON.parse(readFileSync(path, "utf8")) as {
        trunk: { legs: Array<{ siteKey: string; strokeWidth: number }>; spine: unknown[] } | null;
        tap: { box: unknown; vantage: string | null; windows: number | null } | null;
        sites: Array<{ site: { key: string; northSouth: { bytes: number } } }>;
      };
      expect(golden.trunk, "no `trunk` key -- the golden predates R2").not.toBeNull();
      expect(golden.tap, "no `tap` key -- the golden predates R2").not.toBeNull();
      expect(golden.trunk!.legs.map((l) => l.siteKey)).toEqual(
        golden.sites.map((s) => s.site.key),
      );
      // And the north-south rollup is in the committed sites, not just derived
      // at test time.
      expect(golden.sites.some((s) => s.site.northSouth.bytes > 0)).toBe(true);
    });
  }

  it("the committed trunk matches what `layout0` produces today", () => {
    const path = fileURLToPath(new URL("./layout/level0.golden.json", import.meta.url));
    const golden = JSON.parse(readFileSync(path, "utf8")) as { trunk: unknown; tap: unknown };
    const computed = layout0(graph);
    expect(golden.trunk).toEqual(JSON.parse(JSON.stringify(computed.trunk)));
    expect(golden.tap).toEqual(JSON.parse(JSON.stringify(computed.tap)));
  });
});

// ===========================================================================
// Pins re-anchor the legs (LES-266: re-run the primitive, don't translate)
// ===========================================================================
describe("a pinned site's leg follows it (R2 × S4)", () => {
  const base = layout0(graph);
  const moved = layout0(graph, { "site:10.80": { x: 900, y: 620 } });

  it("the moved group's leg ends at its MOVED header top-centre", () => {
    const sp = moved.sites.find((s) => s.site.key === "site:10.80")!;
    const leg = moved.trunk!.legs.find((l) => l.siteKey === "site:10.80")!;
    const end = leg.points[leg.points.length - 1]!;
    expect(end).toEqual({ x: sp.header.x + sp.header.w / 2, y: sp.header.y });
    expect(end).not.toEqual(
      base.trunk!.legs.find((l) => l.siteKey === "site:10.80")!.points[1],
    );
  });

  it("an UNPINNED group's leg recomputes byte-identical", () => {
    for (const key of ["site:10.20", "site:misc"]) {
      const before = base.trunk!.legs.find((l) => l.siteKey === key);
      const after = moved.trunk!.legs.find((l) => l.siteKey === key);
      expect(after, key).toEqual(before);
    }
    // The spine and the tap are anchored to chrome no pin can touch.
    expect(moved.trunk!.spine).toEqual(base.trunk!.spine);
    expect(moved.tap).toEqual(base.tap);
  });

  it("always REACHES every leg, and grows when a pin lands past its end", () => {
    // The invariant is reach, not width: a pin INSIDE the current span leaves
    // the bus exactly as it was (the first version of this test asserted
    // growth and was simply wrong about where x=900 lands).
    const reaches = (l: typeof base): boolean =>
      l.trunk!.legs.every((leg) => {
        const x = leg.points[0]!.x;
        return x >= l.trunk!.bus![0]!.x && x <= l.trunk!.bus![1]!.x;
      });
    const width = (l: typeof base) => l.trunk!.bus![1]!.x - l.trunk!.bus![0]!.x;
    expect(reaches(base)).toBe(true);
    expect(reaches(moved)).toBe(true);
    expect(width(moved)).toBe(width(base));

    // Past the right-hand end, it extends: the bus is a function of the MOVED
    // leg set, not a fixed span.
    const far = layout0(graph, { "site:10.20": { x: 3000, y: 620 } });
    expect(reaches(far)).toBe(true);
    expect(width(far)).toBeGreaterThan(width(base));
  });
});

// ===========================================================================
// Pins ACROSS the bus -- the S1 fix, and the limit it does not remove
// ===========================================================================
describe("a group pinned near the trunk bus (review S1)", () => {
  const base = layout0(graph);
  const busY = base.trunk!.bus![0]!.y;
  // The x is a parameter because R6 re-aimed ONE of the three cases below: the
  // budget narrowed the plane, and x=900 (clean before) now drags the column
  // over `site:misc`'s tiles. x=420 is measured clean at both y values; the two
  // cases that assert a LIMIT keep x=900, where the limit is still measured.
  const pinnedAt = (y: number, x = 900) => layout0(graph, { "site:10.80": { x, y } });

  it("below the bus: the trunk crosses nothing", () => {
    for (const y of [620, 300]) {
      const violations = noConnectorCrossesATile(pinnedAt(y, 420)).filter((v) =>
        v.startsWith("trunk"),
      );
      expect(violations, `pinned at y=${y}`).toEqual([]);
    }
  });

  it("ABOVE the bus: the leg ends at the header's BOTTOM edge and never enters it", () => {
    // The S1 fix. Ending at `header.y` regardless would drive the leg straight
    // down through the header box it is pointing at -- the reviewer measured it
    // at y=138 and y=24.
    for (const y of [138, 24]) {
      const l0 = pinnedAt(y);
      const sp = l0.sites.find((s) => s.site.key === "site:10.80")!;
      const leg = l0.trunk!.legs.find((x) => x.siteKey === "site:10.80")!;
      expect(sp.header.y, `y=${y} must really be above the bus`).toBeLessThan(busY);
      expect(leg.points[1]!.y, `y=${y}`).toBe(sp.header.y + sp.header.h);
      expect(polylineCrossesBox(leg.points, sp.header), `y=${y}`).toBe(false);
    }
  });

  it("a column dragged ACROSS the bus line is still crossed -- a known pins limit", () => {
    // Inherent, not a defect to fix here: a pin is an ABSOLUTE override with NO
    // reflow (`15` §4), so nothing re-routes around a box parked on top of the
    // bus. At y=100 the 10.80 column's first tile row straddles the bus line.
    // Recorded so the limit is a measured statement rather than a surprise;
    // the work order's §8 carries it for operators.
    const violations = noConnectorCrossesATile(pinnedAt(100)).filter((v) =>
      v.startsWith("trunk"),
    );
    expect(
      violations.length,
      "if this is empty the limit is gone -- delete this test and §8's note",
    ).toBeGreaterThan(0);
  });
});

// ===========================================================================
// layout/plane.ts -- the stamp saved pins are measured against
// ===========================================================================
describe("the layout plane stamp (R2, LES-289)", () => {
  it("is `l0-<epoch>-<hash>`, derived from the constants it names", () => {
    expect(PLANE_STAMP).toBe(`l0-${PLANE_EPOCH}-${planeHash(planeCanonicalString())}`);
    expect(PLANE_STAMP).toMatch(/^l0-\d+-[0-9a-f]{8}$/);
    // The canonical string is readable, so a reviewer sees WHICH number moved.
    expect(planeCanonicalString()).toContain("RAIL_W=128");
    expect(planeCanonicalString()).toContain("TILE_W=156");
  });

  it("changes when a constant a pin depends on moves -- automatically", () => {
    // R1's own regression, in miniature: the rail gutter shifted the content
    // origin and every stored pin silently moved 128 units with it.
    const shifted = PLANE_CONSTANTS.map(([n, v]) => [n, n === "RAIL_W" ? v + 1 : v] as const);
    expect(planeHash(planeCanonicalString(shifted))).not.toBe(planeHash(planeCanonicalString()));
  });

  it("advances the stamp ONLY when the whole set was re-authored (review M1)", () => {
    const stale = "l0-1-00000000";
    const A = { x: 1, y: 2 };
    const B = { x: 3, y: 4 };
    const pins = { "site:10.80": A, "segment:10.20.192.0/24": B };
    const both = new Map([["site:10.80", A], ["segment:10.20.192.0/24", B]]);
    const onlyA = new Map([["site:10.80", A]]);

    // THE BUG: a drag commits the WHOLE set, so stamping on any save would
    // re-label the pin the operator never touched as current -- retiring the
    // warning for ever with that pin still in the wrong place.
    expect(planeForSave(pins, onlyA, stale)).toBe(stale);
    expect(planeForSave(pins, onlyA, null)).toBeNull();
    // Every surviving pin re-placed: the set now describes this plane.
    expect(planeForSave(pins, both, stale)).toBe(PLANE_STAMP);
    // A reset writes the empty set -- nothing is left to be stale.
    expect(planeForSave({}, undefined, stale)).toBe(PLANE_STAMP);
    // A caller that cannot say keeps the document's stamp, never inventing one.
    expect(planeForSave(pins, undefined, stale)).toBe(stale);
    // ...and a current document is not demoted by an ordinary save.
    expect(planeForSave(pins, both, PLANE_STAMP)).toBe(PLANE_STAMP);
  });

  it("counts a pin as re-authored by VALUE, not by key (final review S1)", () => {
    // The sequence the key-only form got wrong, and this app builds on purpose:
    //   stale {A:p1,B:p2} → drag A (stays stale, right)
    //   → PUT 412s because a second writer saved {A:q1,B:q2} → reload
    //   → drag B → the KEY set {A,B} now covers everything → it ADVANCED,
    //     certifying A:q1, which nobody re-placed against the plane on screen.
    const stale = "l0-1-00000000";
    const p1 = { x: 1, y: 2 };
    const q1 = { x: 99, y: 99 }; // the other writer's A
    const p2 = { x: 3, y: 4 };
    const mine = new Map([["site:a", p1], ["site:b", p2]]); // both keys are ours

    // Both pins still carry OUR coordinates: wholly re-authored.
    expect(planeForSave({ "site:a": p1, "site:b": p2 }, mine, stale)).toBe(PLANE_STAMP);
    // A's value is now the other writer's. The key is still in our map and
    // must NOT be enough: the stamp stays stale and the operator is asked to
    // re-place it.
    expect(planeForSave({ "site:a": q1, "site:b": p2 }, mine, stale)).toBe(stale);
    // A near-miss is a different write, not a rounding question.
    expect(planeForSave({ "site:a": { x: 1, y: 3 }, "site:b": p2 }, mine, stale)).toBe(stale);
  });

  it("classifies a saved document four ways", () => {
    expect(pinPlaneStatus(PLANE_STAMP, 0)).toBe("none"); // nothing to check
    expect(pinPlaneStatus(null, 0)).toBe("none");
    expect(pinPlaneStatus(PLANE_STAMP, 2)).toBe("current");
    expect(pinPlaneStatus(null, 2)).toBe("legacy"); // saved before the stamp
    expect(pinPlaneStatus(undefined, 2)).toBe("legacy");
    expect(pinPlaneStatus("   ", 2)).toBe("legacy");
    expect(pinPlaneStatus("l0-1-00000000", 2)).toBe("stale");
  });
});
