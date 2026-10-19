// @vitest-environment node
/**
 * R4 — the east-west band (`docs/WORK_ORDER_console_level0_rendering.md` §3 R4
 * and §4.1's arcs ruling), the pure half: the `trafficPairs` aggregation, the
 * arc geometry and its drop into the band, the top-K budget and what it hides,
 * the asserted-conduit family, §1 property 2 under pins, and the width
 * consequence D2 records.
 *
 * `node` environment: nothing here renders. The DRAWING half — that the arcs
 * reach the DOM, that no `.lane` survives, that the "N of M" disclosure is
 * said out loud and reachable — is `r4.ui.test.tsx`.
 */
import { describe, expect, it } from "vitest";

import { buildGraph } from "../src/model/graph";
import type { TopologyExport, TopologyExportEdge } from "../src/api/types";
import { layout0 } from "../src/layout/level0";
import {
  ARC_SAMPLES,
  ARC_TOP_K,
  arcPath,
  arcPolyline,
  layoutArcs,
  type Arc,
  type ArcAnchor,
} from "../src/layout/arcs";
import {
  ARC_BAND_PAD,
  ARC_DEPTH_MAX,
  ARC_DEPTH_MIN,
  ARC_DEPTH_PER_DX,
  TIER_GAP_Y,
} from "../src/layout/geometry";
import { HAIRLINE, MIN_STROKE, STROKE_RANGE } from "../src/layout/scale";
import { noConnectorCrossesATile } from "./layout/helpers";
import { trimmedCast, trimmedExport, withEastWestPairs } from "./fixtures/trimmed";

const graph = buildGraph(trimmedExport, trimmedCast);
/** The fixture's segment names, for readability. */
const S2080 = "seg-10-80-20-0";
const S7280 = "seg-10-80-72-0";
const S193 = "seg-10-20-193-0";
const S192 = "seg-10-20-192-0";

// ===========================================================================
// model/graph.ts -- the aggregation
// ===========================================================================
describe("trafficPairs: what counts as east-west (R4)", () => {
  it("aggregates the fixture's ONE local↔local cross-segment edge", () => {
    // 10.80.72.10 → 10.80.20.5, 150 B / 2 flows, udp. Everything else in the
    // fixture is same-segment, external, or has no segment at all.
    expect(graph.trafficPairs).toEqual([
      {
        a: S2080,
        b: S7280,
        bytes: 150,
        flows: 2,
        protoMix: [{ proto: "udp", bytes: 150 }],
      },
    ]);
  });

  it("excludes same-segment, external and unplaced ends -- each for its own reason", () => {
    const extra: TopologyExportEdge[] = [
      // same segment: level 1's business, and an arc from a group to itself is
      // not a picture.
      { src: "address:10.80.20.40", dst: "address:10.80.20.5", proto: "tcp", bytes: 9_000, flows: 1, evidence_class: "observed" },
      // one end external: that is NORTH-south (R2's trunk).
      { src: "address:10.80.20.40", dst: "address:8.8.8.8", proto: "tcp", bytes: 9_000, flows: 1, evidence_class: "observed" },
      // one end local but UNPLACED: no tile, so no anchor to draw from.
      { src: "address:10.80.20.40", dst: "address:10.99.99.99", proto: "tcp", bytes: 9_000, flows: 1, evidence_class: "observed" },
      // an id the export does not carry at all.
      { src: "address:10.80.20.40", dst: "address:172.16.0.1", proto: "tcp", bytes: 9_000, flows: 1, evidence_class: "observed" },
    ];
    const g = buildGraph(
      { ...trimmedExport, edges: [...trimmedExport.edges, ...extra] },
      trimmedCast,
    );
    // Not one of those four is drawable, so the census is unchanged.
    expect(g.trafficPairs).toEqual(graph.trafficPairs);
  });

  it("merges BOTH directions into one undirected pair, and keeps the proto mix", () => {
    const extra: TopologyExportEdge[] = [
      { src: "address:10.80.72.10", dst: "address:10.20.193.20", proto: "tcp", bytes: 100, flows: 3, evidence_class: "observed" },
      { src: "address:10.20.193.20", dst: "address:10.80.72.10", proto: "tcp", bytes: 400, flows: 4, evidence_class: "observed" },
      { src: "address:10.20.193.21", dst: "address:10.80.72.11", proto: "udp", bytes: 250, flows: 1, evidence_class: "observed" },
    ];
    const g = buildGraph(
      { ...trimmedExport, edges: [...trimmedExport.edges, ...extra] },
      trimmedCast,
    );
    const pair = g.trafficPairs.find((p) => p.a === S193 && p.b === S7280)!;
    expect(pair.bytes).toBe(750);
    expect(pair.flows).toBe(8);
    // Descending by bytes, so `protoMix[0]` is the dominant protocol with no
    // second sort at the call site.
    expect(pair.protoMix).toEqual([
      { proto: "tcp", bytes: 500 },
      { proto: "udp", bytes: 250 },
    ]);
  });

  it("sorts by bytes desc, then by pair key -- the order the budget slices", () => {
    const g = buildGraph(withEastWestPairs(), trimmedCast);
    expect(g.trafficPairs).toHaveLength(15);
    const bytes = g.trafficPairs.map((p) => p.bytes);
    expect(bytes).toEqual([...bytes].sort((x, y) => y - x));
    // A tie must not depend on the export's edge order.
    const tied = buildGraph(
      {
        ...trimmedExport,
        edges: [
          { src: "address:10.80.72.10", dst: "address:10.20.193.20", proto: "tcp", bytes: 500, flows: 1, evidence_class: "observed" },
          { src: "address:10.1.10.1", dst: "address:192.168.0.20", proto: "tcp", bytes: 500, flows: 1, evidence_class: "observed" },
        ],
      },
      trimmedCast,
    );
    expect(tied.trafficPairs.map((p) => `${p.a}|${p.b}`)).toEqual([
      "seg-10-1-10-0|seg-192-168-0-0",
      "seg-10-20-193-0|seg-10-80-72-0",
    ]);
  });
});

// ===========================================================================
// layout/arcs.ts -- the geometry
// ===========================================================================
describe("the arc's anchors and its drop into the band (R4)", () => {
  const l0 = layout0(graph);
  const boxOf = (name: string) => l0.sites.find((s) => s.site.segments.some((x) => x.name === name))!;
  const tileOf = (name: string) => l0.tiles.find((t) => t.segment.name === name)!;

  it("starts and ends at the GROUP's bottom edge, at the TILE's centre x", () => {
    expect(l0.arcs.length).toBeGreaterThan(0);
    for (const arc of l0.arcs) {
      for (const [name, point] of [[arc.a, arc.from], [arc.b, arc.to]] as const) {
        const site = boxOf(name);
        const tile = tileOf(name);
        expect(point.y, `${arc.a}|${arc.b}`).toBe(site.box.y + site.box.h);
        expect(point.x, `${arc.a}|${arc.b}`).toBe(tile.box.x + tile.box.w / 2);
      }
    }
  });

  it("drops to the band top before curving -- the fix the pit bundle forced", () => {
    // A cubic sags only 3/4 of the way to its controls, so an arc between two
    // SHORT columns used to cut through a TALL column's lowest tiles. Measured
    // on the pit bundle before the fix: 1 crossing among the top 12, 6 under
    // `?ew=all`. Every arc now enters the band first.
    const bandTop = l0.rails.find((r) => r.tier === "east-west")!.box.y;
    for (const arc of l0.arcs) {
      expect(arc.dropFrom.x, `${arc.a}|${arc.b}`).toBe(arc.from.x);
      expect(arc.dropTo.x, `${arc.a}|${arc.b}`).toBe(arc.to.x);
      expect(arc.dropFrom.y, `${arc.a}|${arc.b}`).toBe(bandTop);
      expect(arc.dropTo.y, `${arc.a}|${arc.b}`).toBe(bandTop);
      // ...and every sampled point of the drawn path is at or below the band.
      for (const p of arcPolyline(arc)) {
        expect(p.y >= Math.min(arc.from.y, arc.to.y) - 1e-9, `${arc.a}|${arc.b}`).toBe(true);
      }
    }
    // R6: the band starts a tier gap below the deepest COLUMN, not below the
    // lowest tile. An arc leaves its group's bottom EDGE, and a column can now
    // end a row below its last tile (the `+N more` box takes a grid slot), so
    // measuring from the tiles put the band above the anchor of any such column
    // and its depth stopped being bounded by the clamp. Every column's bottom
    // is above the band top by construction now, rather than by the
    // `SITE_PAD` < `TIER_GAP_Y` arithmetic that held while every column ended
    // in a tile.
    expect(bandTop).toBe(Math.max(...l0.sites.map((s) => s.box.y + s.box.h)) + TIER_GAP_Y);
    expect(bandTop).toBeGreaterThan(Math.max(...l0.tiles.map((t) => t.box.y + t.box.h)));
  });

  it("takes its depth from |Δx|, clamped, with both controls at one y", () => {
    for (const arc of l0.arcs) {
      const expected = Math.min(
        ARC_DEPTH_MAX,
        ARC_DEPTH_MIN + ARC_DEPTH_PER_DX * Math.abs(arc.to.x - arc.from.x),
      );
      expect(arc.c1.y, `${arc.a}|${arc.b}`).toBe(arc.c2.y);
      expect(arc.c1.y - arc.dropFrom.y, `${arc.a}|${arc.b}`).toBeCloseTo(expected, 2);
      expect(arc.c1.x).toBe(arc.from.x);
      expect(arc.c2.x).toBe(arc.to.x);
    }
  });

  it("bounds the band by ARC_DEPTH_MAX + ARC_BAND_PAD whatever the pair count", () => {
    // 300 pairs over 25 segments, every one drawn (`limit: null`). One channel
    // per pair -- the retired lane geometry -- would be 3,300 units tall here.
    const anchors = new Map<string, ArcAnchor>();
    for (let i = 0; i < 25; i++) {
      anchors.set(`s${i}`, {
        tile: { x: i * 200, y: 100, w: 156, h: 96 },
        group: { x: i * 200 - 10, y: 80, w: 176, h: 140 },
      });
    }
    const observed = [];
    for (let i = 0; i < 25; i++) {
      for (let j = i + 1; j < 25; j++) {
        observed.push({ a: `s${i}`, b: `s${j}`, bytes: (25 - i) * 1000 + j, flows: 1 });
      }
    }
    const band = layoutArcs({ asserted: [], observed, anchors, bandTop: 500, limit: null });
    expect(band.arcs).toHaveLength(300);
    expect(band.bandBottom - band.bandTop).toBeLessThanOrEqual(ARC_DEPTH_MAX + ARC_BAND_PAD);
    // ...and with four pairs the band costs exactly the same as with 300,
    // which is what "the arcs share the band" means.
    const few = layoutArcs({ asserted: [], observed: observed.slice(0, 4), anchors, bandTop: 500, limit: null });
    expect(few.bandBottom - few.bandTop).toBeLessThanOrEqual(ARC_DEPTH_MAX + ARC_BAND_PAD);
  });

  it("collapses to nothing when there is nothing to draw", () => {
    const band = layoutArcs({ asserted: [], observed: [], anchors: new Map(), bandTop: 500, limit: null });
    expect(band.arcs).toEqual([]);
    expect(band.bandBottom).toBe(500);
    expect(band.hiddenPairs).toBe(0);
  });

  it("is a pure function of its inputs -- two calls are byte-identical", () => {
    const input = {
      asserted: graph.conduits,
      observed: graph.trafficPairs,
      anchors: new Map(
        l0.tiles.map((t) => [
          t.segment.name,
          { tile: t.box, group: l0.sites.find((s) => s.site.key === t.siteKey)!.box },
        ]),
      ),
      bandTop: 470,
      limit: ARC_TOP_K,
    };
    expect(JSON.stringify(layoutArcs(input))).toBe(JSON.stringify(layoutArcs(input)));
  });

  it("publishes a path and a polyline that agree at the ends", () => {
    const arc = l0.arcs[0]!;
    expect(arcPath(arc)).toBe(
      `M ${arc.from.x},${arc.from.y} L ${arc.dropFrom.x},${arc.dropFrom.y} ` +
        `C ${arc.c1.x},${arc.c1.y} ${arc.c2.x},${arc.c2.y} ${arc.dropTo.x},${arc.dropTo.y} ` +
        `L ${arc.to.x},${arc.to.y}`,
    );
    const points = arcPolyline(arc);
    expect(points).toHaveLength(ARC_SAMPLES + 3); // both drops + the samples
    expect(points[0]).toEqual(arc.from);
    expect(points[points.length - 1]).toEqual(arc.to);
    expect(points[1]).toEqual(arc.dropFrom); // t=0 is exact, not near
    expect(points[points.length - 2]).toEqual(arc.dropTo);
    for (const p of points) {
      expect(Number.isFinite(p.x) && Number.isFinite(p.y), JSON.stringify(p)).toBe(true);
    }
    // Total on a degenerate sample count rather than throwing or dividing by 0.
    expect(arcPolyline(arc, 0)).toHaveLength(5);
  });
});

// ===========================================================================
// The two families, and what happens where they meet
// ===========================================================================
describe("asserted conduits and observed traffic (R4)", () => {
  const l0 = layout0(graph);

  it("draws every resolvable conduit, in the edit family", () => {
    expect(l0.arcs.filter((a) => a.kind === "asserted")).toHaveLength(3);
    expect(graph.conduits).toHaveLength(3); // the 4th names `internet-sink`
  });

  it("marks an asserted conduit with NO observed traffic as silent, at the hairline", () => {
    const silent = l0.arcs.filter((a) => !a.hasTraffic);
    expect(silent.map((a) => `${a.a}|${a.b}`)).toEqual([
      `${S192}|${S193}`,
      `${S193}|${S2080}`,
    ]);
    for (const arc of silent) {
      expect(arc.bytes).toBe(0);
      expect(arc.strokeWidth).toBe(HAIRLINE);
    }
  });

  it("draws a pair that is BOTH asserted and observed exactly ONCE, carrying its volume", () => {
    // The fixture's single observed pair IS one of its conduits. Two identical
    // curves on the same two anchors would be one curve plus a misleading
    // count, so the asserted family wins and keeps the volume.
    const both = l0.arcs.filter((a) => a.a === S2080 && a.b === S7280);
    expect(both).toHaveLength(1);
    expect(both[0]!.kind).toBe("asserted");
    expect(both[0]!.hasTraffic).toBe(true);
    expect(both[0]!.bytes).toBe(150);
    expect(both[0]!.strokeWidth).toBe(MIN_STROKE + STROKE_RANGE);
  });

  it("never counts an asserted pair as hidden -- `hiddenPairs` is the BUDGET's", () => {
    expect(l0.hiddenPairs).toBe(0);
    // ...and a conduit whose end has no tile is not drawn and not hidden: it
    // was never drawable, which `unresolvedConduitEndpoints` reports instead.
    expect(graph.unresolvedConduitEndpoints).toEqual(["internet-sink"]);
  });
});

// ===========================================================================
// The top-K budget, and the width consequence D2 records
// ===========================================================================
describe("the top-K budget (D2: K = 12) and `?ew=all` (R4)", () => {
  const many = buildGraph(withEastWestPairs(), { ...trimmedCast, conduits: [] });

  it("draws the busiest K and COUNTS the rest", () => {
    expect(many.trafficPairs).toHaveLength(15);
    const l0 = layout0(many);
    expect(l0.arcs).toHaveLength(ARC_TOP_K);
    expect(l0.hiddenPairs).toBe(15 - ARC_TOP_K);
    // The drawn set is exactly the first K of `trafficPairs` -- the busiest.
    expect(l0.arcs.map((a) => `${a.a}|${a.b}`)).toEqual(
      many.trafficPairs.slice(0, ARC_TOP_K).map((p) => `${p.a}|${p.b}`),
    );
    expect(l0.arcs.every((a) => a.kind === "observed")).toBe(true);
  });

  it("`?ew=all` draws every pair and hides none", () => {
    const all = layout0(many, {}, { showAllPairs: true });
    expect(all.arcs).toHaveLength(15);
    expect(all.hiddenPairs).toBe(0);
    expect(noConnectorCrossesATile(all)).toEqual([]);
  });

  it("draws the SAME pair at a different width under `?ew=all` -- measured (§5 D2)", () => {
    // The deliberate cost of normalising over the DRAWN set (§11.3): twelve
    // arcs are discriminable, and toggling the budget moves every width. It is
    // recorded here and beside D2 so nobody reads a width change as a data
    // change.
    const top = layout0(many);
    const all = layout0(many, {}, { showAllPairs: true });
    const widthOf = (l: typeof top, i: number) => l.arcs[i]!.strokeWidth;
    expect(widthOf(top, 0)).toBe(widthOf(all, 0)); // the maximum is the maximum
    expect(widthOf(top, 1)).toBeCloseTo(5.74, 2);
    expect(widthOf(all, 1)).toBeCloseTo(5.87, 2);
    expect(widthOf(top, 11)).toBeCloseTo(1, 2);
    expect(widthOf(all, 11)).toBeCloseTo(3.56, 2);
    // Every pair drawn in both views is WIDER (or equal) in the larger set,
    // because the range it is measured against got wider at the bottom.
    for (let i = 0; i < ARC_TOP_K; i++) {
      expect(widthOf(all, i), `pair ${i}`).toBeGreaterThanOrEqual(widthOf(top, i));
    }
  });

  it("keeps the asserted set OUT of the budget: the twin's conduits are all drawn", () => {
    const withRoster = layout0(buildGraph(withEastWestPairs(), trimmedCast));
    expect(withRoster.arcs.filter((a) => a.kind === "asserted")).toHaveLength(3);
    // 15 pairs, 3 of them asserted: 12 observed fit the budget exactly, so
    // nothing is hidden and the asserted three are extra, not instead.
    expect(withRoster.arcs).toHaveLength(15);
    expect(withRoster.hiddenPairs).toBe(0);
  });
});

// ===========================================================================
// §1 property 2 -- and the pin limit, swept rather than sampled (LES-305)
// ===========================================================================
describe("no connector crosses a tile, with arcs in the check (§1.2, R4)", () => {
  it("holds on the fixture and on the 15-pair fixture", () => {
    expect(noConnectorCrossesATile(layout0(graph))).toEqual([]);
    expect(
      noConnectorCrossesATile(layout0(buildGraph(withEastWestPairs(), trimmedCast))),
    ).toEqual([]);
  });

  it("the helper can actually SEE an arc -- a guard on the guard", () => {
    // Every "crosses nothing" assertion above would pass just as happily if the
    // arcs had never been added to `connectorsOf`: an empty contribution
    // crosses nothing (LES-298). Drive one through a tile and it must be named.
    const l0 = layout0(graph);
    const tile = l0.tiles[0]!;
    const cx = tile.box.x + tile.box.w / 2;
    const probe: Arc = {
      ...l0.arcs[0]!,
      a: "probe-a",
      b: "probe-b",
      // Explicit, not inherited: the fixture's arcs are all ASSERTED, and the
      // violation is named by kind -- so spreading one silently tested the
      // other family's message.
      kind: "observed",
      from: { x: cx, y: tile.box.y - 40 },
      dropFrom: { x: cx, y: tile.box.y - 40 },
      c1: { x: cx, y: tile.box.y + tile.box.h + 40 },
      c2: { x: cx, y: tile.box.y + tile.box.h + 40 },
      dropTo: { x: cx, y: tile.box.y + tile.box.h + 40 },
      to: { x: cx, y: tile.box.y + tile.box.h + 40 },
    };
    expect(noConnectorCrossesATile({ ...l0, arcs: [probe] })).toEqual([
      `observed arc probe-a↔probe-b crosses tile ${tile.segment.observedCidr}`,
    ]);
  });
});

describe("a pinned column drags its arcs (R4 × S4) -- swept, not sampled", () => {
  // LES-305: two structures with different pitches make a single probe evidence
  // about that coordinate and nothing more. A sweep measures it instead — and
  // the GRID is quoted with the count, because a bare "146 of 294" is not
  // checkable and two readers sweeping different grids get different answers
  // (LES-306). Grid: `x ∈ [140, 1100]` step 20 × `y ∈ {620, 484, 300, 100, 24,
  // lowestTileBottom + 40}` = 49 × 6 = 294 positions per column, counting a
  // position as violating when `noConnectorCrossesATile` names any `arc `:
  //
  //   site:10.20 pinned -> 129 of 294 positions cross a tile with an ARC
  //   site:10.80 pinned ->  85 of 294
  //
  // (`r4.acceptance.test.ts` sweeps a deliberately coarser grid — step 60 over
  // three y values — and asserts the MIXTURE rather than a count, which is the
  // claim that does not depend on the grid at all.)
  //
  // So BOTH a violating and a clean coordinate are pinned below, and neither
  // can be read as the rule. The cause is the no-reflow pin (`15` §4): an arc
  // is anchored to two group edges, and a column parked over another column's
  // tiles drags its end of the curve across them. Nothing re-routes.
  const violating = { key: "site:10.20", at: { x: 400, y: 620 } };
  const clean = { key: "site:10.20", at: { x: 140, y: 620 } };

  it("DOES cross at a measured coordinate", () => {
    const arcs = noConnectorCrossesATile(
      layout0(graph, { [violating.key]: violating.at }),
    ).filter((v) => v.includes("arc "));
    expect(
      arcs.length,
      "if this is empty the limit is gone -- delete this test and the §8 note",
    ).toBeGreaterThan(0);
  });

  it("and is clean at another, so neither coordinate reads as the rule", () => {
    expect(
      noConnectorCrossesATile(layout0(graph, { [clean.key]: clean.at })).filter((v) =>
        v.includes("arc "),
      ),
    ).toEqual([]);
  });

  it("is inherent to the pin, not to the arc: the geometry still holds", () => {
    // Whatever the pin, every arc still leaves a group edge, drops into the
    // band and never rises above it -- it is the COLUMN that was parked
    // somewhere impossible.
    const moved = layout0(graph, { [violating.key]: violating.at });
    const bandTop = moved.rails.find((r) => r.tier === "east-west")!.box.y;
    for (const arc of moved.arcs) {
      const site = moved.sites.find((s) => s.site.segments.some((x) => x.name === arc.a))!;
      expect(arc.from.y, `${arc.a}|${arc.b}`).toBe(site.box.y + site.box.h);
      expect(arc.dropFrom.y, `${arc.a}|${arc.b}`).toBeGreaterThanOrEqual(
        Math.min(bandTop, arc.from.y),
      );
      expect(arc.c1.y, `${arc.a}|${arc.b}`).toBeGreaterThanOrEqual(arc.dropFrom.y);
    }
  });

  it("a pinned SITE's arcs follow it while an unpinned neighbour's are byte-identical", () => {
    // The S4 golden, extended to arcs (§3 R4 acceptance).
    const base = layout0(graph);
    // x=420 since R6 (was 900): the per-group budget narrowed the plane, so the
    // old coordinate now drags this column over another's tiles. The sweep that
    // re-aimed it is quoted in `r2.trunk.test.ts`.
    const moved = layout0(graph, { "site:10.80": { x: 420, y: 620 } });
    const key = (a: Arc) => `${a.a}|${a.b}`;
    // The pinned site holds 10.80.20.0/24 (`S2080`) and 10.80.72.0/24
    // (`S7280`), so an arc touching EITHER moves -- at whichever of its two
    // ends names a moved segment, which for `S193|S2080` is the `to` end and
    // not the `from`. Comparing the whole arc says "it moved" without
    // asserting WHICH end, which is the pair's own business.
    const touched = new Set([`${S193}|${S2080}`, `${S2080}|${S7280}`]);
    expect(base.arcs.map(key).filter((k) => touched.has(k))).toHaveLength(2);
    for (const before of base.arcs) {
      const after = moved.arcs.find((x) => key(x) === key(before))!;
      if (touched.has(key(before))) {
        expect(after, key(before)).not.toEqual(before);
        const end = key(before) === `${S193}|${S2080}` ? "to" : "from";
        expect(after[end], key(before)).not.toEqual(before[end]);
      } else {
        expect(after, key(before)).toEqual(before);
      }
    }
    // ...and this particular pin crosses nothing at all, which is why
    // `r2.trunk.test.ts` can now assert exactly `[]` (R2 had to say "every
    // violation is a lane" because the lanes did cross here).
    expect(noConnectorCrossesATile(moved)).toEqual([]);
  });
});

// ===========================================================================
// A bundle with no east-west traffic at all
// ===========================================================================
describe("a twin whose capture saw no east-west traffic (R4)", () => {
  const bare: TopologyExport = { ...trimmedExport, edges: [] };

  it("draws an empty band and reports it, rather than drawing nothing silently", () => {
    const g = buildGraph(bare, { ...trimmedCast, conduits: [] });
    const l0 = layout0(g);
    expect(g.trafficPairs).toEqual([]);
    expect(l0.arcs).toEqual([]);
    expect(l0.hiddenPairs).toBe(0);
    // The band takes no height when it holds nothing, so the EAST-WEST rail
    // draws no label over empty space (R1's rule).
    expect(l0.rails.find((r) => r.tier === "east-west")!.box.h).toBe(0);
  });

  it("still draws the twin's DECLARED conduits, dashed, when it has some", () => {
    const l0 = layout0(buildGraph(bare, trimmedCast));
    expect(l0.arcs).toHaveLength(3);
    expect(l0.arcs.every((a) => a.kind === "asserted" && !a.hasTraffic)).toBe(true);
  });
});
