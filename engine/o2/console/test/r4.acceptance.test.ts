// @vitest-environment node
/**
 * R4 acceptance, verified INDEPENDENTLY of the tests that shipped with it
 * (`docs/WORK_ORDER_console_level0_rendering.md` §3 R4).
 *
 * The shipped R4 tests are thorough, and most of §3 R4's acceptance is already
 * asserted there. This file covers what they cannot certify about themselves:
 *
 *  1. **The drop into the band is load-bearing, not decoration.** The shipped
 *     tests assert the drop EXISTS (`dropFrom.y === bandTop`) and that nothing
 *     crosses. Neither states the property that makes the fix work — that the
 *     whole drawn path stays at or below the band top on the real bundle,
 *     where the three-quarter sag actually bit. Asserted here over every
 *     sampled point of every arc at BOTH budgets.
 *  2. **The pin limit, swept rather than sampled.** The shipped test pins one
 *     violating and one clean coordinate. A sweep is what makes those two
 *     readable as a window rather than a rule — and what would catch the limit
 *     silently disappearing (in which case the violating pin's `> 0` would
 *     fail, but nothing would say the sweep had emptied).
 *  3. **The band bound stated as a function of the constants**, not of one
 *     measured bundle: no drawn arc's control depth may exceed
 *     `ARC_DEPTH_MAX`, whatever the plane's width.
 */
import { existsSync, lstatSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import type { TopologyExport } from "../src/api/types";
import { buildGraph } from "../src/model/graph";
import { layout0 } from "../src/layout/level0";
import { arcPolyline, ARC_TOP_K, layoutArcs, type ArcAnchor } from "../src/layout/arcs";
import {
  ARC_BAND_PAD,
  ARC_DEPTH_MAX,
  ARC_DEPTH_MIN,
  ARC_DEPTH_PER_DX,
} from "../src/layout/geometry";
import { groupIntoSites } from "../src/layout/sites";
import { noConnectorCrossesATile } from "./layout/helpers";
import { trimmedCast, trimmedExport } from "./fixtures/trimmed";

const graph = buildGraph(trimmedExport, trimmedCast);

const PIT = fileURLToPath(
  new URL("../../../twins/pit-395a61f04e-1780405200/", import.meta.url),
);
const PIT_TOPOLOGY = `${PIT}generated/pit-395a61f04e-1780405200.topology.json`;

/** A dangling link is a hard failure, never a silent green skip (LES-301). */
function pitAvailable(): boolean {
  let linked = false;
  try {
    lstatSync(PIT_TOPOLOGY);
    linked = true;
  } catch {
    linked = false;
  }
  if (linked && !existsSync(PIT_TOPOLOGY)) {
    throw new Error(`${PIT_TOPOLOGY} is a DANGLING symlink -- restore its target.`);
  }
  return linked;
}

// ===========================================================================
// 1. The drop is what makes §1.2 hold -- stated as the property, on real data.
// ===========================================================================
describe.skipIf(!pitAvailable())("R4: the drop into the band, on the real bundle", () => {
  const exportData = JSON.parse(readFileSync(PIT_TOPOLOGY, "utf8")) as TopologyExport;
  const pit = buildGraph(exportData, {
    imprintId: "pit-395a61f04e-1780405200",
    deployMinOccupancy: 0.8,
  });

  it("keeps every sampled point of every arc at or below the band top, at BOTH budgets", () => {
    // THE property behind the fix. A cubic reaches only three quarters of the
    // way to its controls, so before the drop an arc between two SHORT columns
    // sagged through a TALL column's lowest tiles. Asserting "nothing crosses"
    // alone would not say why, and would not fail if the drop were replaced by
    // something that happened to clear this bundle's tiles.
    for (const showAll of [false, true]) {
      const l0 = layout0(pit, {}, { showAllPairs: showAll });
      const bandTop = l0.rails.find((r) => r.tier === "east-west")!.box.y;
      expect(l0.arcs.length).toBeGreaterThan(0);
      for (const arc of l0.arcs) {
        for (const p of arcPolyline(arc)) {
          // The two anchor points sit ON the group edges, above the band; every
          // CURVE point must be in the band.
          if (p === arc.from || p === arc.to) continue;
          expect(p.y, `${arc.a}|${arc.b} ew=${showAll}`).toBeGreaterThanOrEqual(bandTop - 1e-9);
        }
      }
    }
  });

  it("crosses no tile at K=12 and none under `?ew=all` -- the case that bit", () => {
    const top = layout0(pit);
    expect(top.arcs).toHaveLength(ARC_TOP_K);
    expect(noConnectorCrossesATile(top)).toEqual([]);

    // R6: one curve per pair is a claim about a drawing where every segment has
    // a tile, so it is asserted with the groups EXPANDED. Under the default
    // budget the same pairs are still all accounted for -- merged onto their
    // group's `+N more` anchor, or counted as internal to it -- and nothing
    // crosses a tile either way.
    const expanded = groupIntoSites(pit.segments).map((s) => s.key);
    const all = layout0(pit, {}, { showAllPairs: true, expanded });
    expect(all.arcs).toHaveLength(pit.trafficPairs.length);
    expect(noConnectorCrossesATile(all)).toEqual([]);

    const folded = layout0(pit, {}, { showAllPairs: true });
    expect(folded.arcs.length).toBeLessThan(pit.trafficPairs.length);
    expect(folded.arcs.reduce((n, a) => n + a.pairs, 0) + folded.internalPairs).toBe(
      pit.trafficPairs.length,
    );
    expect(noConnectorCrossesATile(folded)).toEqual([]);
  });

  it("bounds the band by the CONSTANTS, not by this bundle's measurements", () => {
    for (const showAll of [false, true]) {
      const l0 = layout0(pit, {}, { showAllPairs: showAll });
      const bandTop = l0.rails.find((r) => r.tier === "east-west")!.box.y;
      for (const arc of l0.arcs) {
        expect(arc.c1.y - bandTop, `${arc.a}|${arc.b}`).toBeLessThanOrEqual(ARC_DEPTH_MAX);
      }
      expect(l0.rails.find((r) => r.tier === "east-west")!.box.h).toBeLessThanOrEqual(
        ARC_DEPTH_MAX + ARC_BAND_PAD,
      );
    }
  });
});

// ===========================================================================
// 1b. ...and the clamp that bound is made of is actually REACHED somewhere.
// ===========================================================================
describe("R4: the depth clamp is exercised, not merely asserted", () => {
  it("clamps a span wider than any real bundle's, so the bound is not vacuous", () => {
    // Measured, and the reason this test exists: the clamp only engages past
    // |Δx| > (ARC_DEPTH_MAX - ARC_DEPTH_MIN) / ARC_DEPTH_PER_DX = 1840 units,
    // and NO pair on the pit bundle spans that far. So the real-bundle bound
    // above passes whether or not the clamp is there -- it states that the
    // band is bounded on that data, not that the constant does the bounding.
    // This drives a span past the knee, where removing the clamp changes the
    // answer.
    const knee = (ARC_DEPTH_MAX - ARC_DEPTH_MIN) / ARC_DEPTH_PER_DX;
    const span = Math.ceil(knee) + 1000;
    const anchors = new Map<string, ArcAnchor>([
      ["far-a", { tile: { x: 0, y: 100, w: 156, h: 96 }, group: { x: -10, y: 80, w: 176, h: 140 } }],
      [
        "far-b",
        { tile: { x: span, y: 100, w: 156, h: 96 }, group: { x: span - 10, y: 80, w: 176, h: 140 } },
      ],
    ]);
    const band = layoutArcs({
      asserted: [],
      observed: [{ a: "far-a", b: "far-b", bytes: 1000, flows: 1 }],
      anchors,
      bandTop: 500,
      limit: null,
    });
    const arc = band.arcs[0]!;
    // Unclamped this depth would be ARC_DEPTH_MIN + 0.05 * span, well past MAX.
    expect(ARC_DEPTH_MIN + ARC_DEPTH_PER_DX * span).toBeGreaterThan(ARC_DEPTH_MAX);
    expect(arc.c1.y - band.bandTop).toBe(ARC_DEPTH_MAX);
    expect(band.bandBottom - band.bandTop).toBeLessThanOrEqual(ARC_DEPTH_MAX + ARC_BAND_PAD);
  });
});

// ===========================================================================
// 2. The pin limit, swept -- so neither pinned coordinate reads as the rule.
// ===========================================================================
describe("R4: the pin limit is a WINDOW, measured by sweeping", () => {
  /** Arc-only violations for one pinned position. */
  function arcCrossings(key: string, x: number, y: number): number {
    return noConnectorCrossesATile(layout0(graph, { [key]: { x, y } })).filter((v) =>
      v.includes("arc "),
    ).length;
  }

  it("finds BOTH outcomes across a coarse sweep, for both columns", () => {
    // The shipped test pins one violating and one clean coordinate. That is
    // evidence about two points; this is what makes them readable as a window.
    // A coarser step than the code-writer's (60 vs 20) -- the claim is the
    // MIXTURE, and a coarse grid that still finds both is stronger evidence
    // that neither pinned coordinate was cherry-picked.
    for (const key of ["site:10.20", "site:10.80"]) {
      let crossing = 0;
      let clean = 0;
      for (let x = 140; x <= 1100; x += 60) {
        for (const y of [400, 620, 800]) {
          if (arcCrossings(key, x, y) > 0) crossing += 1;
          else clean += 1;
        }
      }
      expect(crossing, `${key}: nothing crosses -- the limit is gone`).toBeGreaterThan(0);
      expect(clean, `${key}: everything crosses -- this is not a window`).toBeGreaterThan(0);
    }
  });

  it("reproduces the two pinned coordinates exactly", () => {
    // The shipped test's own pair, re-measured here rather than taken on
    // trust: they must sit on opposite sides of the window.
    expect(arcCrossings("site:10.20", 400, 620)).toBeGreaterThan(0);
    expect(arcCrossings("site:10.20", 140, 620)).toBe(0);
  });

  it("leaves the UNPINNED drawing clean, so the limit is the pin's alone", () => {
    expect(noConnectorCrossesATile(layout0(graph))).toEqual([]);
  });
});
