// @vitest-environment node
/**
 * S5.1.b -- where a placed added segment is drawn, where a clone is proposed,
 * and the held-for-a-collapsed-group disclosure (`src/layout/added.ts`,
 * `heldAddedClause` in `src/views/Level0.tsx`).
 *
 * Pure: layouts in, boxes out. The view half (the hatched tile, the tap, the
 * Alt-drag) is `clone.ui.test.tsx`.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import { placeAddedTiles, proposedClonePlacement, segmentPinKey, slotAfterColumn } from "../src/layout/added";
import { arcPolyline } from "../src/layout/arcs";
import { TILE_GAP_X, TILE_H, TILE_W, type Box, type Point } from "../src/layout/geometry";
import { columnSlot, layout0 } from "../src/layout/level0";
import { NO_PINS } from "../src/layout/pins";
import { buildGraph } from "../src/model/graph";
import { NO_FILTERS } from "../src/model/zoom";
import { heldAddedClause, level0OptionsFor } from "../src/views/Level0";
import { layoutPointOf } from "../src/views/usePinDrag";
import { bundleAvailable, realGraph, REAL_BUNDLES } from "./fixtures/realBundles";
import { polylineCrossesBox } from "./layout/helpers";
import { trimmedCast, trimmedExport, withOverBudgetSite } from "./fixtures/trimmed";

const graph = buildGraph(trimmedExport, trimmedCast);
const l0 = layout0(graph);
const observed = graph.segments.map((s) => s.observedCidr);

describe("placeAddedTiles -- a placed added segment is a tile at its position, reflowing nothing", () => {
  it("draws at the PIN, falls back to this session's placement, and APPENDS an unplaced one (S5.1.d)", () => {
    const pins = { [segmentPinKey("10.80.31.0/24")]: { x: 900, y: 40 } };
    const { drawn, held } = placeAddedTiles(
      l0,
      observed,
      [
        { cidr: "10.80.31.0/24", fallback: { x: 1, y: 2 } },
        { cidr: "10.80.32.0/24", fallback: { x: 500, y: 600 } },
        { cidr: "10.80.33.0/24", fallback: null },
      ],
      pins,
    );
    expect(held).toEqual([]);
    // RESTATED TWICE. For review N2: a placement reports what it covers (both
    // of these land clear of every drawn box; the overlap case is its own test
    // below). For S5.1.d (LES-299): an UNPLACED added segment is no longer
    // undrawn -- it is appended to its own site column, where the operator can
    // see and move it. Its position is the column's own next slot rule.
    // Every column of this fixture is full, so the appended tile goes to the
    // PARKING LANE right of the whole grid -- clear of every drawn BOX, which
    // is the property that separates it from a clone's proposal (see
    // `appendSlot`). `slotAfterColumn` returns `null` for exactly that reason.
    //
    // RESTATED (review M1): the lane clears every box the plane draws, not only
    // the site columns -- `layout0` appends the O7 unplaced tile as a final
    // single-tile column outside any site, and the lane used to land on it.
    const column = l0.sites.find((sp) => sp.site.key === "site:10.80")!;
    expect(slotAfterColumn(l0, column, 0)).toBeNull();
    const appended = { x: planeRight(l0) + TILE_GAP_X, y: columnSlot(column.header, 2, 0).y };
    expect(drawn).toEqual([
      // A pin on file wins over the session's placement.
      { cidr: "10.80.31.0/24", box: { x: 900, y: 40, w: TILE_W, h: TILE_H }, siteKey: "site:10.80", from: "pin", covers: [] },
      { cidr: "10.80.32.0/24", box: { x: 500, y: 600, w: TILE_W, h: TILE_H }, siteKey: "site:10.80", from: "session", covers: [] },
      {
        cidr: "10.80.33.0/24",
        box: { x: appended.x, y: appended.y, w: TILE_W, h: TILE_H },
        siteKey: "site:10.80",
        from: "appended",
        covers: [],
      },
    ]);
  });

  it("changes NO coordinate of the pure layout -- a clone is drawn on top, never slotted in", () => {
    const pins = { [segmentPinKey("10.80.31.0/24")]: { x: 900, y: 40 } };
    // The layout the map draws with the clone's pin in the pin set is the
    // no-pins layout: no observed tile or site carries that key.
    expect(JSON.stringify(layout0(graph, pins))).toBe(JSON.stringify(l0));
  });

  it("HOLDS a placed tile whose group the per-group budget collapsed -- and draws it once the group is expanded", () => {
    const over = buildGraph(withOverBudgetSite(), trimmedCast);
    const collapsed = layout0(over);
    const cidrs = over.segments.map((s) => s.observedCidr);
    const input = [{ cidr: "10.80.99.0/24", fallback: { x: 10, y: 20 } }];
    expect(placeAddedTiles(collapsed, cidrs, input, NO_PINS)).toEqual({
      drawn: [],
      held: [{ cidr: "10.80.99.0/24", siteKey: "site:10.80", siteLabel: "10.80" }],
      unplaceable: [],
    });
    const expanded = layout0(over, NO_PINS, { expanded: ["site:10.80"] });
    expect(placeAddedTiles(expanded, cidrs, input, NO_PINS).drawn.map((d) => d.cidr)).toEqual(["10.80.99.0/24"]);
    // A group that is not over budget holds nothing.
    expect(placeAddedTiles(collapsed, cidrs, [{ cidr: "10.20.9.0/24", fallback: { x: 1, y: 1 } }], NO_PINS).held).toEqual([]);
  });

  it("counts each network ONCE -- a CIDR named twice does not pull a singleton out of misc", () => {
    // 10.1.10.0/24 is a misc singleton. Named as observed AND as an added tile,
    // a naive grouping sees two 10.1 members and forms a 10.1 group the map does
    // not draw -- so the tile would be drawn with no site. Once, it is misc.
    const { drawn } = placeAddedTiles(l0, [...observed, "10.1.10.0/24"], [{ cidr: "10.1.10.0/24", fallback: { x: 7, y: 8 } }], NO_PINS);
    expect(drawn.map((d) => d.siteKey)).toEqual(["site:misc"]);
  });

  it("its group is the one groupIntoSites forms WITH it -- an existing group, or one the map does not draw (no site)", () => {
    // 10.99.1.0/24 shares a /16 with nothing: it joins the fixture's misc group.
    const misc = placeAddedTiles(l0, observed, [{ cidr: "10.99.1.0/24", fallback: { x: 3, y: 4 } }], NO_PINS);
    expect(misc.drawn).toEqual([{ cidr: "10.99.1.0/24", box: { x: 3, y: 4, w: TILE_W, h: TILE_H }, siteKey: "site:misc", from: "session", covers: [] }]);
    // 10.1.20.0/24 pulls the singleton 10.1.10.0/24 OUT of misc into a 10.1
    // group this map has no column for: drawn, belonging to no drawn site.
    const fresh = placeAddedTiles(l0, observed, [{ cidr: "10.1.20.0/24", fallback: { x: 5, y: 6 } }], NO_PINS);
    expect(fresh.drawn).toEqual([{ cidr: "10.1.20.0/24", box: { x: 5, y: 6, w: TILE_W, h: TILE_H }, siteKey: null, from: "session", covers: [] }]);
  });

  it("NAMES the drawn tile a placement covers -- overlap is allowed, silence is not (N2)", () => {
    // Dropped exactly on a drawn tile's own box: it takes the box and, in the
    // view, that tile's pointer events too.
    const victim = l0.tiles[0]!;
    const onTop = placeAddedTiles(
      l0,
      observed,
      [{ cidr: "10.80.31.0/24", fallback: { x: victim.box.x, y: victim.box.y } }],
      NO_PINS,
    );
    // RESTATED (M1): the report covers every drawn BOX, so a tile dropped
    // across another group names that group too -- it takes the group header's
    // own `+` control along with the tile.
    expect(onTop.drawn[0]!.covers).toContain(victim.segment.observedCidr);
    const victimSite = l0.sites.find((sp) => sp.site.key === victim.siteKey)!;
    expect(onTop.drawn[0]!.covers).toContain(`the ${victimSite.site.label} group`);
    // Partly over it is still over it...
    const partial = placeAddedTiles(
      l0,
      observed,
      [{ cidr: "10.80.31.0/24", fallback: { x: victim.box.x + TILE_W - 1, y: victim.box.y } }],
      NO_PINS,
    );
    expect(partial.drawn[0]!.covers).toContain(victim.segment.observedCidr);
    // ...but an edge exactly touching the next slot is NOT: every tile in a
    // column would otherwise report covering its neighbour.
    const abutting = placeAddedTiles(
      l0,
      observed,
      [{ cidr: "10.80.31.0/24", fallback: { x: victim.box.x + TILE_W, y: victim.box.y } }],
      NO_PINS,
    );
    expect(abutting.drawn[0]!.covers).not.toContain(victim.segment.observedCidr);
  });

  it("names a `+N more` box it covers by what is behind it, not by a CIDR it has none of (N2)", () => {
    const over = buildGraph(withOverBudgetSite(), trimmedCast);
    const collapsed = layout0(over);
    const more = collapsed.overflow[0]!;
    const onMore = placeAddedTiles(
      collapsed,
      over.segments.map((s) => s.observedCidr),
      [{ cidr: "10.99.1.0/24", fallback: { x: more.box.x, y: more.box.y } }],
      NO_PINS,
    );
    expect(onMore.drawn[0]!.covers).toContain(`the ${more.hidden.length} collapsed segments of ${more.siteKey}`);
  });
});

/**
 * The right edge of EVERY box the plane draws in the segment tier -- the site
 * columns, the tiles, the `+N more` boxes, the O7 unplaced tile and the
 * infrastructure devices.
 *
 * Rebuilt here from the layout's own published boxes rather than imported from
 * `added.ts`, so the test measures the plane instead of agreeing with the rule
 * under test (LES-187).
 */
function planeRight(layout: ReturnType<typeof layout0>): number {
  const boxes: Box[] = [
    ...layout.sites.map((sp) => sp.box),
    ...layout.tiles.map((t) => t.box),
    ...layout.overflow.map((m) => m.box),
    ...layout.infraDevices.map((d) => d.box),
    ...(layout.unplaced ? [layout.unplaced.box] : []),
  ];
  return boxes.reduce((max, b) => Math.max(max, b.x + b.w), 0);
}

/** The box a proposal occupies, for a crossing test. */
function proposalBox(at: Point): Box {
  return { x: at.x, y: at.y, w: TILE_W, h: TILE_H };
}

/** How many of a layout's arcs pass through `box`. */
function arcsCrossing(layout: ReturnType<typeof layout0>, box: Box): number {
  return layout.arcs.filter((arc) => polylineCrossesBox(arcPolyline(arc), box)).length;
}

describe("proposedClonePlacement -- the next slot, never outside its own column", () => {
  it("is the source column's next grid slot while that slot stays inside the column", () => {
    // A column carrying a `+N more` tile has a reserved row the next slot falls
    // into, so the rule is the layout's own `columnSlot`, unchanged.
    const over = layout0(buildGraph(withOverBudgetSite(), trimmedCast));
    const column = over.sites.find((sp) => sp.site.key === "site:10.80")!;
    const shown = over.tiles.filter((t) => t.siteKey === "site:10.80").length;
    expect(shown).toBe(6);
    expect(over.overflow.some((m) => m.siteKey === "site:10.80")).toBe(true);
    const slot = columnSlot(column.header, 3, 7);
    expect(slot.y + TILE_H).toBeLessThanOrEqual(column.box.y + column.box.h);
    expect(proposedClonePlacement(over, "10.80.20.0/24")).toEqual({ x: slot.x, y: slot.y });
  });

  it("moves BESIDE the column when the next slot would leave it (N1)", () => {
    // A column whose tiles exactly fill its rows has no room for another: the
    // next slot falls past the group box's bottom edge, into the corridor the
    // arcs anchored on that group drop through.
    const column = l0.sites.find((sp) => sp.site.key === "site:10.80")!;
    const shown = l0.tiles.filter((t) => t.siteKey === "site:10.80").length;
    expect(shown).toBe(2);
    const wouldBe = columnSlot(column.header, 2, 2);
    expect(wouldBe.y + TILE_H).toBeGreaterThan(column.box.y + column.box.h); // the case this guards
    const at = proposedClonePlacement(l0, "10.80.20.0/24");
    expect(at.y).toBe(columnSlot(column.header, 2, 0).y);
    // RESTATED (M1): beside the column, but never past the plane's right edge
    // onto the O7 unplaced tile -- for the LAST column the two coincided.
    expect(at.x).toBe(Math.max(column.box.x + column.box.w, planeRight(l0)) + TILE_GAP_X);
  });

  it("follows a PINNED site column, reading the box wherever the box is", () => {
    const pinned = layout0(graph, { "site:10.80": { x: 2000, y: 900 } });
    const column = pinned.sites.find((sp) => sp.site.key === "site:10.80")!;
    const at = proposedClonePlacement(pinned, "10.80.20.0/24");
    expect(at.y).toBe(columnSlot(column.header, 2, 0).y);
    expect(at.x).toBe(Math.max(column.box.x + column.box.w, planeRight(pinned)) + TILE_GAP_X);
    // A pinned column dragged deep into the band keeps its proposal beside it
    // rather than sending it far from the segment it copies -- the documented
    // limit. It is still better than the naive slot: beside, not below.
    expect(at.y).toBeLessThan(columnSlot(column.header, 2, 2).y);
  });

  it("the MEASURED case: corpapcn-twin's misc group has no `+N more`, so its box ends early (N1)", () => {
    // Through the LIVE seam (LES-311): the options the app builds, not a bare
    // `layout0(graph)` -- which is how this survived a first fix aimed at the
    // east-west RAIL. Five of six columns carry a `+N more` tile and end at
    // 680; `site:misc` holds exactly six segments, so it has none and ends at
    // 568. The same proposal (572..668) is inside the first five and 100 units
    // outside the sixth, where six asserted arcs cross it.
    const bundle = REAL_BUNDLES.find((b) => b.id === "corpapcn-twin")!;
    if (!bundleAvailable(bundle)) return;
    const layout = layout0(realGraph(bundle), NO_PINS, level0OptionsFor(NO_FILTERS));
    const misc = layout.sites.find((sp) => sp.site.key === "site:misc")!;
    expect(layout.overflow.some((m) => m.siteKey === "site:misc")).toBe(false);
    const shown = layout.tiles.filter((t) => t.siteKey === "site:misc").length;
    const naive = columnSlot(misc.header, shown <= 4 ? 2 : 3, shown);
    // The rail test called this clear, and it was not.
    const rail = layout.rails.find((r) => r.tier === "east-west")!;
    expect(naive.y + TILE_H).toBeLessThan(rail.box.y);
    expect(naive.y + TILE_H).toBeGreaterThan(misc.box.y + misc.box.h);
    expect(arcsCrossing(layout, proposalBox(naive))).toBe(6);
    // The rule moves it, and nothing crosses it there.
    const at = proposedClonePlacement(layout, misc.site.segments[0]!.observedCidr);
    expect(at).not.toEqual({ x: naive.x, y: naive.y });
    expect(arcsCrossing(layout, proposalBox(at))).toBe(0);
  });

  it("no arc crosses the proposal, from any group of either real bundle, at either budget (N1)", () => {
    for (const bundle of REAL_BUNDLES) {
      if (!bundleAvailable(bundle)) continue;
      const real = realGraph(bundle);
      for (const expanded of [[], ["site:misc"], ["site:10.80"]]) {
        const layout = layout0(real, NO_PINS, { ...level0OptionsFor(NO_FILTERS), expanded });
        for (const sp of layout.sites) {
          const at = proposedClonePlacement(layout, sp.site.segments[0]!.observedCidr);
          const where = `${bundle.id} ${sp.site.key} expanded=${expanded.join()}`;
          expect(arcsCrossing(layout, proposalBox(at)), where).toBe(0);
          expect(at.y + TILE_H, where).toBeLessThanOrEqual(sp.box.y + sp.box.h);
        }
      }
    }
  });
});

describe("heldAddedClause -- a held clone is SAID, in one sentence", () => {
  it("says nothing when nothing is held", () => {
    expect(heldAddedClause([])).toBe("");
  });

  it("names the pending clone and the group that holds it, and how to see it", () => {
    expect(heldAddedClause([{ siteLabel: "10.80", state: "pending", clone: true }])).toBe(
      " · 1 pending clone held in 10.80 — not drawn while its group is collapsed: open it to see it",
    );
  });

  it("counts by state and kind, per group", () => {
    expect(
      heldAddedClause([
        { siteLabel: "10.80", state: "pending", clone: true },
        { siteLabel: "10.80", state: "pending", clone: true },
        { siteLabel: "10.80", state: "draft", clone: true },
        { siteLabel: "10.20", state: "saved", clone: false },
      ]),
    ).toBe(
      " · 2 pending clones and the clone being filled in held in 10.80; 1 saved added segment held in 10.20 — " +
        "not drawn while their groups are collapsed: open them to see them",
    );
  });
});

describe("the pending hatch is not overridden by the stylesheet", () => {
  /**
   * The pending clone tile is hatched by its `fill` ATTRIBUTE, set to the map's
   * SVG pattern -- and any CSS rule that sets `fill` on the element beats a
   * presentation attribute, so the hatch would be invisible in every browser
   * while every jsdom test (which reads the attribute) stayed green. The first
   * cut drew the tile with the `.tile` class, whose rule sets `fill`. So: read
   * the stylesheets and prove no rule's final compound selector can match a
   * pending, draft or ghost clone tile and set its fill.
   */
  const css = ["../src/theme/shell.css", "../src/theme/tokens.css"]
    .map((rel) => readFileSync(fileURLToPath(new URL(rel, import.meta.url)), "utf8"))
    .join("\n")
    .replace(/\/\*[\s\S]*?\*\//g, "");

  /** Every innermost `selectors { declarations }` block that sets `fill`. */
  const fillSelectors: string[] = [];
  for (const match of css.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    if (!/(^|;|\s)fill\s*:/.test(match[2]!)) continue;
    for (const selector of match[1]!.split(",")) fillSelectors.push(selector.trim());
  }

  function setsFillOn(classes: readonly string[]): string[] {
    const have = new Set(classes);
    return fillSelectors.filter((selector) => {
      const last = selector.split(/[\s>+~]+/).filter(Boolean).pop() ?? "";
      if (last === "rect" || last === "*") return true;
      if (!/^(\.[\w-]+)+$/.test(last)) return false;
      return last
        .split(".")
        .filter(Boolean)
        .every((cls) => have.has(cls));
    });
  }

  it("found the fill rules it reads -- the parser is not vacuous", () => {
    expect(fillSelectors).toContain(".tile");
    expect(setsFillOn(["tile"])).toContain(".tile");
  });

  it.each([
    ["a pending clone tile", ["added-tile", "is-pending"]],
    ["the clone being placed or filled in", ["added-tile", "is-draft"]],
    ["the Alt-drag ghost", ["added-tile", "is-ghost"]],
  ])("no rule sets the fill of %s", (_name, classes) => {
    expect(setsFillOn(classes)).toEqual([]);
  });
});

describe("layoutPointOf -- where a tap lands in layout units", () => {
  it("inverts the screen CTM -- scaled AND translated, the real-browser branch jsdom never reaches", () => {
    // Layout -> screen: x' = 0.5x + 100, y' = 0.5y + 40 (a map drawn at half
    // size, scrolled). A tap at screen (300, 240) is layout (400, 400).
    const svg = {
      getScreenCTM: () => ({ a: 0.5, b: 0, c: 0, d: 0.5, e: 100, f: 40 }),
      getBoundingClientRect: () => ({ left: 0, top: 0, width: 0, height: 0 }),
      getAttribute: () => null,
    } as unknown as SVGSVGElement;
    expect(layoutPointOf(svg, 300, 240)).toEqual({ x: 400, y: 400 });
  });

  it("without a CTM, maps through the rect against the viewBox -- and 1:1 from the viewBox origin when nothing is laid out", () => {
    const laidOut = {
      getScreenCTM: () => null,
      getBoundingClientRect: () => ({ left: 10, top: 20, width: 500, height: 250 }),
      getAttribute: (name: string) => (name === "viewBox" ? "-50 0 1000 500" : null),
    } as unknown as SVGSVGElement;
    expect(layoutPointOf(laidOut, 260, 145)).toEqual({ x: -50 + 250 * 2, y: 125 * 2 });
    const jsdom = {
      getBoundingClientRect: () => ({ left: 0, top: 0, width: 0, height: 0 }),
      getAttribute: (name: string) => (name === "viewBox" ? "-50 0 1000 500" : null),
    } as unknown as SVGSVGElement;
    expect(layoutPointOf(jsdom, 700, 520)).toEqual({ x: 650, y: 520 });
  });
});
