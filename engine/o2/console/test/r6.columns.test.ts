// @vitest-environment node
/**
 * R6 — tile ranking, per-group budgets and the column grid
 * (`docs/WORK_ORDER_console_level0_rendering.md` §3 R6).
 *
 * The pure rules (`layout/columns.ts`), what they do to `layout0`, and the four
 * things the slice's acceptance names: six tiles plus a `+N more`; expanding
 * leaves every shown tile where it was; a collapsed segment stays reachable
 * everywhere else; and a pin on a collapsed tile survives and is applied when
 * the group is expanded.
 */
import { describe, expect, it } from "vitest";

import { buildGraph } from "../src/model/graph";
import { SITE_BUDGET, siteCollapses } from "../src/model/budgets";
import { resolveSearch } from "../src/model/search";
import {
  budgetColumn,
  columnHeightFor,
  columnWidthFor,
  orderSegments,
  siteColsFor,
  siteColsIsStableAcrossBudget,
  SITE_COLS_NARROW,
  SITE_COLS_NARROW_MAX,
} from "../src/layout/columns";
import { SITE_COLS, SITE_PAD, TILE_GAP_X, TILE_W } from "../src/layout/geometry";
import { layout0, overflowAnchorKey, siteKeyOfOverflowAnchor } from "../src/layout/level0";
import { groupIntoSites } from "../src/layout/sites";
import { noConnectorCrossesATile } from "./layout/helpers";
import {
  trimmedCast,
  trimmedExport,
  withCollapsedTraffic,
  withOverBudgetSite,
} from "./fixtures/trimmed";

const plain = buildGraph(trimmedExport, trimmedCast);
const over = buildGraph(withOverBudgetSite(), trimmedCast);
const BIG = "site:10.80";

// ===========================================================================
// The rules, in `layout/columns.ts`
// ===========================================================================
describe("the column rules (R6)", () => {
  it("is two tiles across for a small group and three otherwise", () => {
    for (let n = 0; n <= SITE_COLS_NARROW_MAX; n++) {
      expect(siteColsFor(n), `${n} shown`).toBe(SITE_COLS_NARROW);
    }
    for (const n of [SITE_COLS_NARROW_MAX + 1, SITE_BUDGET, 19, 200]) {
      expect(siteColsFor(n), `${n} shown`).toBe(SITE_COLS);
    }
  });

  it("NEVER re-widths a column by expanding it -- the locality property", () => {
    // Computed from the constants rather than asserted as prose (LES-306): a
    // group only collapses ABOVE `SITE_BUDGET`, and the budget and anything
    // larger are both in the wide band, so the shown tiles keep their slots.
    for (let total = 0; total <= 60; total++) {
      expect(siteColsIsStableAcrossBudget(total), `${total} segments`).toBe(true);
      if (siteCollapses(total)) {
        expect(siteColsFor(SITE_BUDGET)).toBe(siteColsFor(total));
      }
    }
  });

  it("sizes a column from its own column count", () => {
    expect(columnWidthFor(1)).toBe(TILE_W + 2 * SITE_PAD);
    expect(columnWidthFor(3)).toBe(3 * TILE_W + 2 * TILE_GAP_X + 2 * SITE_PAD);
    expect(columnWidthFor(2)).toBeLessThan(columnWidthFor(3));
    // The `+N more` box takes a grid slot like any other, so seven boxes at
    // three across is three rows.
    expect(columnHeightFor(6, 3)).toBeLessThan(columnHeightFor(7, 3));
  });

  it("ranks by placed-host count desc, then CIDR -- and CIDR order is the other option", () => {
    const segs = groupIntoSites(over.segments).find((s) => s.key === BIG)!.segments;
    const byHosts = orderSegments(segs, "hosts").map((s) => s.observedCidr);
    const byCidr = orderSegments(segs, "cidr").map((s) => s.observedCidr);
    expect(byHosts.slice(0, 3)).toEqual([
      "10.80.72.0/24", // 28 hosts
      "10.80.20.0/24", // 18
      "10.80.87.0/24", // 5 -- the LAST by CIDR, so the two orders differ in
      //                      which six they show, not only in what order
    ]);
    // The tie-break is not hypothetical: three segments hold exactly one host,
    // and the lowest CIDR of them is the one that gets a tile.
    const ones = segs.filter((s) => s.hostCount === 1).map((s) => s.observedCidr);
    expect(ones).toHaveLength(3);
    expect(byHosts.indexOf("10.80.84.0/24")).toBeLessThan(byHosts.indexOf("10.80.85.0/24"));
    expect(byCidr[0]).toBe("10.80.20.0/24");
    expect(byCidr).not.toEqual(byHosts);
    // The SETS differ too, not just the order -- a fixture where they coincide
    // would pass with either implementation (LES-312).
    expect(byHosts.slice(0, SITE_BUDGET)).toContain("10.80.87.0/24");
    expect(byCidr.slice(0, SITE_BUDGET)).not.toContain("10.80.87.0/24");
    expect(byCidr.slice(0, SITE_BUDGET)).toContain("10.80.84.0/24");
    expect(byHosts.slice(0, SITE_BUDGET)).not.toContain("10.80.84.0/24");
  });

  it("budgets a group, and leaves one under budget alone", () => {
    const segs = groupIntoSites(over.segments).find((s) => s.key === BIG)!.segments;
    const collapsed = budgetColumn(segs, { order: "hosts", expanded: false });
    expect(collapsed.shown).toHaveLength(SITE_BUDGET);
    expect(collapsed.hidden).toHaveLength(segs.length - SITE_BUDGET);
    expect(collapsed.overflow).toBe(true);
    expect(collapsed.boxes).toBe(SITE_BUDGET + 1);

    const expanded = budgetColumn(segs, { order: "hosts", expanded: true });
    expect(expanded.shown).toHaveLength(segs.length);
    expect(expanded.hidden).toEqual([]);
    expect(expanded.overflow).toBe(true); // the way back
    expect(expanded.width).toBe(collapsed.width);

    const small = groupIntoSites(plain.segments).find((s) => s.key === "site:10.20")!.segments;
    const fits = budgetColumn(small, { order: "hosts", expanded: false });
    expect(fits.overflow).toBe(false);
    expect(fits.hidden).toEqual([]);
    expect(fits.boxes).toBe(small.length);
  });
});

// ===========================================================================
// What it does to the drawing
// ===========================================================================
describe("the budget on a layout (R6)", () => {
  it("draws six tiles and a `+N more` in the over-budget group, and nothing extra elsewhere", () => {
    const l0 = layout0(over);
    const inBig = l0.tiles.filter((t) => t.siteKey === BIG);
    const total = groupIntoSites(over.segments).find((s) => s.key === BIG)!.segments.length;
    expect(inBig).toHaveLength(SITE_BUDGET);
    expect(l0.overflow).toHaveLength(1);
    expect(l0.overflow[0]!.siteKey).toBe(BIG);
    expect(l0.overflow[0]!.hidden).toHaveLength(total - SITE_BUDGET);
    expect(l0.overflow[0]!.total).toBe(total);
    expect(l0.hiddenSegments).toBe(total - SITE_BUDGET);
    // The map's own count is the drawn one, and the twin's is still whole.
    expect(l0.tiles.length + l0.hiddenSegments).toBe(over.segments.length);
  });

  it("claims NO residual on a twin that fits", () => {
    const l0 = layout0(plain);
    expect(l0.overflow).toEqual([]);
    expect(l0.hiddenSegments).toBe(0);
    expect(l0.tiles).toHaveLength(plain.segments.length);
  });

  it("puts the `+N more` box in the grid slot after the last tile, inside the column", () => {
    const l0 = layout0(over);
    const more = l0.overflow[0]!;
    const column = l0.sites.find((s) => s.site.key === BIG)!;
    expect(more.box.x).toBeGreaterThanOrEqual(column.box.x);
    expect(more.box.x + more.box.w).toBeLessThanOrEqual(column.box.x + column.box.w);
    expect(more.box.y + more.box.h).toBeLessThanOrEqual(column.box.y + column.box.h);
    // Below every tile in its group: it is the END of the group, not a slot in
    // the middle of it.
    const lowest = Math.max(
      ...l0.tiles.filter((t) => t.siteKey === BIG).map((t) => t.box.y),
    );
    expect(more.box.y).toBeGreaterThanOrEqual(lowest);
  });

  it("EXPANDING leaves every already-shown tile exactly where it was (§3 R6)", () => {
    const collapsed = layout0(over);
    const expanded = layout0(over, {}, { expanded: [BIG] });
    const after = new Map(expanded.tiles.map((t) => [t.segment.observedCidr, t.box]));
    for (const t of collapsed.tiles) {
      expect(after.get(t.segment.observedCidr), t.segment.observedCidr).toEqual(t.box);
    }
    // ...and it is the whole group that appears, not a re-shuffle.
    expect(expanded.tiles).toHaveLength(over.segments.length);
    expect(expanded.hiddenSegments).toBe(0);
    // The chrome and the other columns do not move: the column's WIDTH is the
    // same either way, so the plane's width is too.
    expect(expanded.width).toBe(collapsed.width);
    expect(expanded.internet).toEqual(collapsed.internet);
    expect(expanded.sites.find((s) => s.site.key === "site:misc")!.box).toEqual(
      collapsed.sites.find((s) => s.site.key === "site:misc")!.box,
    );
    // The column itself grows DOWNWARD, which is the one thing that may move.
    const big = (l: typeof collapsed) => l.sites.find((s) => s.site.key === BIG)!.box;
    expect(big(expanded).h).toBeGreaterThan(big(collapsed).h);
    expect(big(expanded).x).toBe(big(collapsed).x);
  });

  it("keeps a collapsed segment in the outline, in search and in the model", () => {
    const l0 = layout0(over);
    const hidden = l0.overflow[0]!.hidden[0]!;
    expect(l0.tiles.some((t) => t.segment.name === hidden.name)).toBe(false);
    // The MAP is the only place it is missing.
    expect(over.segmentByCidr.has(hidden.cidr)).toBe(true);
    expect(
      groupIntoSites(over.segments)
        .find((s) => s.key === BIG)!
        .segments.some((s) => s.name === hidden.name),
    ).toBe(true);
    expect(resolveSearch(over, hidden.cidr).zoom).toEqual({
      level: "segment",
      imprint: over.imprintId,
      cidr: hidden.cidr,
    });
  });

  it("`?order=cidr` shows a DIFFERENT six, and says so by drawing them", () => {
    const byHosts = layout0(over).tiles.filter((t) => t.siteKey === BIG).map((t) => t.segment.observedCidr);
    const byCidr = layout0(over, {}, { order: "cidr" }).tiles
      .filter((t) => t.siteKey === BIG)
      .map((t) => t.segment.observedCidr);
    expect(byHosts).not.toEqual(byCidr);
    expect(byCidr[0]).toBe("10.80.20.0/24");
    // Both are complete projections of the same group -- same count, same
    // overflow, different six.
    expect(byCidr).toHaveLength(SITE_BUDGET);
  });
});

// ===========================================================================
// Pins — the acceptance clause that is easiest to get wrong
// ===========================================================================
describe("a pin on a collapsed tile (R6)", () => {
  const l0 = layout0(over);
  const hiddenCidr = l0.overflow[0]!.hidden[0]!.cidr;
  const pin = { [`segment:${hiddenCidr}`]: { x: 1500, y: 900 } };

  it("is neither drawn nor dropped while the group is collapsed", () => {
    const pinned = layout0(over, pin);
    expect(pinned.tiles.some((t) => t.segment.observedCidr === hiddenCidr)).toBe(false);
    // Every OTHER tile is exactly where the unpinned layout put it: a pin for a
    // tile that is not drawn moves nothing.
    const base = new Map(l0.tiles.map((t) => [t.segment.observedCidr, t.box]));
    for (const t of pinned.tiles) {
      expect(base.get(t.segment.observedCidr), t.segment.observedCidr).toEqual(t.box);
    }
  });

  it("REAPPEARS at its pinned position when the group is expanded", () => {
    const pinned = layout0(over, pin, { expanded: [BIG] });
    const tile = pinned.tiles.find((t) => t.segment.observedCidr === hiddenCidr)!;
    expect(tile.box.x).toBe(1500);
    expect(tile.box.y).toBe(900);
  });

  it("travels with its group when the SITE is pinned", () => {
    // A site pin moves the header, the tiles AND the `+N more` box together --
    // an overflow left behind would anchor the collapsed arcs to where the
    // group used to be.
    const base = layout0(over);
    const moved = layout0(over, { [BIG]: { x: 300, y: 700 } });
    const dx = 300 - base.sites.find((s) => s.site.key === BIG)!.box.x;
    const dy = 700 - base.sites.find((s) => s.site.key === BIG)!.box.y;
    expect(moved.overflow[0]!.box.x).toBe(base.overflow[0]!.box.x + dx);
    expect(moved.overflow[0]!.box.y).toBe(base.overflow[0]!.box.y + dy);
  });
});

// ===========================================================================
// The east-west band under a budget — the interaction that would have
// silently deleted most of it
// ===========================================================================
describe("arcs through a collapsed group (R6)", () => {
  const traffic = buildGraph(withCollapsedTraffic(), trimmedCast);

  it("anchors a collapsed segment's arcs at its group's `+N more`, MERGED", () => {
    const l0 = layout0(traffic);
    const key = overflowAnchorKey(BIG);
    const folded = l0.arcs.filter((a) => a.a === key || a.b === key);
    expect(folded).toHaveLength(1); // two pairs, ONE curve
    expect(folded[0]!.pairs).toBe(2);
    expect(folded[0]!.bytes).toBe(1_000_000); // 900k + 100k
    expect(siteKeyOfOverflowAnchor(folded[0]!.a === key ? folded[0]!.a : folded[0]!.b)).toBe(BIG);
    expect(l0.foldedPairs).toBe(2);
    // The pair with BOTH ends collapsed in one group is counted, not drawn.
    expect(l0.internalPairs).toBe(1);
  });

  it("gives every pair its own curve again when the group is expanded", () => {
    const l0 = layout0(traffic, {}, { expanded: [BIG] });
    expect(l0.foldedPairs).toBe(0);
    expect(l0.internalPairs).toBe(0);
    expect(l0.arcs.every((a) => a.pairs === 1)).toBe(true);
    // All three of the composed links are drawn, one curve each.
    const drawn = l0.arcs.reduce((n, a) => n + a.pairs, 0);
    expect(drawn).toBe(layout0(traffic).arcs.reduce((n, a) => n + a.pairs, 0) + 1);
  });

  it("would otherwise have LOST them: no collapsed end is silently dropped", () => {
    // The whole reason the fold exists. Every observed pair is either DRAWN --
    // as itself, or inside the aggregate its collapsed end anchors to -- or
    // counted as internal to one group. Checked pair by pair rather than as a
    // sum, because an aggregate's `pairs` counts both families and a total
    // would balance for the wrong reasons.
    const l0 = layout0(traffic);
    const hidden = new Set(l0.overflow.flatMap((m) => m.hidden.map((s) => s.name)));
    const site = new Map<string, string>();
    for (const s of groupIntoSites(traffic.segments)) {
      for (const seg of s.segments) site.set(seg.name, s.key);
    }
    const anchorOf = (name: string): string =>
      hidden.has(name) ? overflowAnchorKey(site.get(name)!) : name;
    const drawn = new Set(l0.arcs.map((a) => `${a.a}|${a.b}`));
    let internal = 0;
    for (const pair of traffic.trafficPairs) {
      const [ka, kb] = [anchorOf(pair.a), anchorOf(pair.b)].sort();
      if (ka === kb) {
        internal += 1;
        continue;
      }
      expect(drawn.has(`${ka}|${kb}`), `${pair.a}|${pair.b}`).toBe(true);
    }
    expect(internal).toBe(1);
    expect(l0.internalPairs).toBe(1);
    expect(l0.hiddenPairs).toBe(0); // nothing is budgeted out at this pair count
  });
});

// ===========================================================================
// §1 property 2, across the budget's states
// ===========================================================================
describe("no connector crosses a tile, collapsed or expanded (§1.2)", () => {
  it("holds on the over-budget fixture, both ways", () => {
    expect(noConnectorCrossesATile(layout0(over))).toEqual([]);
    expect(noConnectorCrossesATile(layout0(over, {}, { expanded: [BIG] }))).toEqual([]);
    expect(noConnectorCrossesATile(layout0(over, {}, { order: "cidr" }))).toEqual([]);
    expect(
      noConnectorCrossesATile(layout0(buildGraph(withCollapsedTraffic(), trimmedCast))),
    ).toEqual([]);
  });
});
