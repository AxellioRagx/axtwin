// @vitest-environment node
/**
 * S5.1.d acceptance 2 -- **pending tiles touch the budget and the goldens not
 * at all** -- and the stylesheet guards that keep *pending* readable as
 * something other than a colour (LES-327).
 *
 * Node environment: both halves read files from the package (LES-260).
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import { placeAddedTiles } from "../src/layout/added";
import { layout0 } from "../src/layout/level0";
import type { Box } from "../src/layout/geometry";
import { NO_PINS } from "../src/layout/pins";
import { buildGraph } from "../src/model/graph";
import { NO_FILTERS } from "../src/model/zoom";
import { level0OptionsFor } from "../src/views/Level0";
import { bundleAvailable, realGraph, REAL_BUNDLES } from "./fixtures/realBundles";
import { trimmedCast, trimmedExport, withOverBudgetSite } from "./fixtures/trimmed";

const CSS = fileURLToPath(new URL("../src/theme/shell.css", import.meta.url));

/** Whether two boxes share any area; touching edges do not. */
function overlaps(a: Box, b: Box): boolean {
  return a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
}

describe("acceptance 2 -- the budget and the goldens are untouched by pending tiles", () => {
  it("the level-0 goldens are byte-identical whatever this file adds", () => {
    // `placeAddedTiles` reads a layout; it must never be able to produce one.
    // Compared on the BYTES of the committed goldens, not on a recomputation,
    // so a change to the golden FILE would also fail (LES-320: a golden that
    // moves must be accounted for, and this one must not move at all).
    for (const [name, graph] of [
      ["level0", buildGraph(trimmedExport, trimmedCast)],
      ["level0-collapsed", buildGraph(withOverBudgetSite(), trimmedCast)],
    ] as const) {
      const path = fileURLToPath(new URL(`./layout/${name}.golden.json`, import.meta.url));
      const before = readFileSync(path, "utf8");
      const l0 = layout0(graph);
      const observed = graph.segments.map((s) => s.observedCidr);
      // Three added segments, in every state a tile can be in: pinned,
      // placed this session, and not placed at all.
      placeAddedTiles(
        l0,
        observed,
        [
          { cidr: "10.80.201.0/24", fallback: null },
          { cidr: "10.80.202.0/24", fallback: { x: 4, y: 5 } },
          { cidr: "10.80.203.0/24", fallback: null },
        ],
        { "segment:10.80.201.0/24": { x: 1, y: 2 } },
      );
      expect(readFileSync(path, "utf8"), name).toBe(before);
      // ...and the layout object itself is the same function of the graph.
      expect(JSON.stringify(layout0(graph))).toBe(JSON.stringify(l0));
    }
  });

  it("no drawn tile moves, the budget does not change, and no shown segment is displaced", () => {
    for (const bundle of REAL_BUNDLES) {
      if (!bundleAvailable(bundle)) continue;
      const graph = realGraph(bundle);
      const options = { ...level0OptionsFor(NO_FILTERS), expanded: ["site:10.20", "site:10.80", "site:misc"] };
      const before = layout0(graph, NO_PINS, options);
      const snapshot = JSON.stringify(before);
      const observed = graph.segments.map((s) => s.observedCidr);
      const out = placeAddedTiles(
        before,
        observed,
        [
          { cidr: "10.20.250.0/24", fallback: null },
          { cidr: "10.80.250.0/24", fallback: null },
          { cidr: "10.80.251.0/24", fallback: null },
          { cidr: "10.20.251.0/24", fallback: null },
        ],
        NO_PINS,
      );
      // Every one of them is drawn...
      expect(out.drawn.map((d) => d.cidr).sort(), bundle.id).toEqual([
        "10.20.250.0/24",
        "10.20.251.0/24",
        "10.80.250.0/24",
        "10.80.251.0/24",
      ]);
      // ...appended, none of them covering a drawn tile -- the property that
      // separates an appended tile from a clone's *proposal*, which may.
      for (const d of out.drawn) {
        expect(d.from, `${bundle.id} ${d.cidr}`).toBe("appended");
        expect(d.covers, `${bundle.id} ${d.cidr}`).toEqual([]);
      }
      // ...and the layout is untouched: same tiles, same budget, same bytes.
      expect(JSON.stringify(layout0(graph, NO_PINS, options)), bundle.id).toBe(snapshot);
      expect(before.hiddenSegments, bundle.id).toBe(layout0(graph, NO_PINS, options).hiddenSegments);
    }
  });

  it("a column with NO ROOM parks its tiles right of every column -- covering nothing, and never each other", () => {
    // The parking lane is the branch `appendSlot` falls to when the column's own
    // box has no slot left (iteration-9 tester: nothing exercised it, and it is
    // the half of S5.1.d that is supposed to guarantee "covers nothing" when the
    // easy answer -- append in the column -- has run out).
    for (const bundle of REAL_BUNDLES) {
      if (!bundleAvailable(bundle)) continue;
      const graph = realGraph(bundle);
      // Expanded, or these groups are collapsed and every tile is HELD instead.
      const l0 = layout0(graph, NO_PINS, { expanded: ["site:10.20", "site:10.80", "site:misc"] });
      const observed = graph.segments.map((s) => s.observedCidr);
      const right = Math.max(...l0.sites.map((sp) => sp.box.x + sp.box.w));
      // Every box the plane actually draws, rebuilt from the layout's own
      // published geometry (review M1). The lane used to be derived from
      // `l0.sites` alone and landed on the O7 unplaced tile, which `layout0`
      // appends as a final single-tile column OUTSIDE any site.
      const planeBoxes: Array<{ box: Box; what: string }> = [
        ...l0.sites.map((sp) => ({ box: sp.box, what: `group ${sp.site.key}` })),
        ...l0.tiles.map((t) => ({ box: t.box, what: t.segment.observedCidr })),
        ...l0.overflow.map((m) => ({ box: m.box, what: `+N more ${m.siteKey}` })),
        ...l0.infraDevices.map((d) => ({ box: d.box, what: d.node.id })),
        ...(l0.unplaced ? [{ box: l0.unplaced.box, what: "the O7 unplaced tile" }] : []),
      ];
      // Not a vacuous check: the bundle really does draw one.
      expect(l0.unplaced, bundle.id).not.toBeNull();
      const out = placeAddedTiles(
        l0,
        observed,
        ["10.20.250.0/24", "10.80.250.0/24", "10.80.251.0/24", "10.20.251.0/24", "10.80.252.0/24", "10.80.253.0/24"]
          .map((cidr) => ({ cidr, fallback: null })),
        NO_PINS,
      );
      expect(out.drawn, bundle.id).toHaveLength(6);

      const parked = out.drawn.filter((d) => d.box.x > right);
      const inColumn = out.drawn.filter((d) => d.box.x <= right);
      // BOTH branches ran, or this test would pass without reaching the lane.
      expect(inColumn.length, `${bundle.id} in column`).toBeGreaterThan(0);
      expect(parked.length, `${bundle.id} parked`).toBeGreaterThan(0);

      // The lane is one column, to the right of every site box, stacked down.
      expect(new Set(parked.map((d) => d.box.x)).size, bundle.id).toBe(1);
      expect(new Set(parked.map((d) => d.box.y)).size, bundle.id).toBe(parked.length);

      // Nothing drawn covers ANY box the plane draws, and no two of them share
      // any area -- the property that separates an appended tile from a clone's
      // proposal. MEASURED against the plane's real boxes, never against the
      // `covers` the same module derives: a value that certifies itself proved
      // nothing when `coveredBy` scanned a narrower set than the lane did
      // (review M1, LES-187).
      for (const d of out.drawn) {
        for (const other of planeBoxes) {
          const hit = overlaps(d.box, other.box);
          // A tile inside its OWN group's column is not "covering" it.
          const ownGroup = other.what === `group ${d.siteKey}`;
          expect(hit && !ownGroup, `${bundle.id}: ${d.cidr} over ${other.what}`).toBe(false);
        }
      }
      // ...and the derived report agrees with the measurement.
      for (const d of out.drawn) expect(d.covers, `${bundle.id} ${d.cidr}`).toEqual([]);
      for (let i = 0; i < out.drawn.length; i++) {
        for (let j = i + 1; j < out.drawn.length; j++) {
          const overlap = overlaps(out.drawn[i]!.box, out.drawn[j]!.box);
          expect(overlap, `${bundle.id}: ${out.drawn[i]!.cidr} over ${out.drawn[j]!.cidr}`).toBe(false);
        }
      }
    }
  });

  it("an unplaced tile whose group the budget COLLAPSED is held, exactly as a placed one is", () => {
    const graph = buildGraph(withOverBudgetSite(), trimmedCast);
    const collapsed = layout0(graph);
    const cidrs = graph.segments.map((s) => s.observedCidr);
    const out = placeAddedTiles(collapsed, cidrs, [{ cidr: "10.80.99.0/24", fallback: null }], NO_PINS);
    expect(out.drawn).toEqual([]);
    expect(out.held).toEqual([{ cidr: "10.80.99.0/24", siteKey: "site:10.80", siteLabel: "10.80" }]);
    // ...and drawn once the group is opened.
    const expanded = layout0(graph, NO_PINS, { expanded: ["site:10.80"] });
    const opened = placeAddedTiles(expanded, cidrs, [{ cidr: "10.80.99.0/24", fallback: null }], NO_PINS);
    expect(opened.held).toEqual([]);
    expect(opened.drawn.map((d) => d.from)).toEqual(["appended"]);
  });

  it("an added segment whose group the map draws NO column for is reported, not silently dropped", () => {
    const graph = buildGraph(trimmedExport, trimmedCast);
    const l0 = layout0(graph);
    // 10.1.20.0/24 pulls the misc singleton 10.1.10.0/24 into a 10.1 group
    // this map has no column for.
    const out = placeAddedTiles(l0, graph.segments.map((s) => s.observedCidr), [{ cidr: "10.1.20.0/24", fallback: null }], NO_PINS);
    expect(out.drawn).toEqual([]);
    expect(out.held).toEqual([]);
    expect(out.unplaceable).toEqual([
      { cidr: "10.1.20.0/24", reason: expect.stringMatching(/no column for/) as unknown as string },
    ]);
  });
});

describe("the hatch survives a browser -- the stylesheet cannot override it (LES-327)", () => {
  /** Every selector-block pair in the stylesheet, flattened. */
  function rules(): Array<{ selector: string; body: string }> {
    const text = readFileSync(CSS, "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
    const out: Array<{ selector: string; body: string }> = [];
    for (const m of text.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
      out.push({ selector: m[1]!.trim(), body: m[2]!.trim() });
    }
    return out;
  }

  it("finds the rules it parses -- a regex that stops matching is a green empty guard", () => {
    const all = rules();
    expect(all.length).toBeGreaterThan(100);
    expect(all.some((r) => r.selector === ".added-chip")).toBe(true);
    expect(all.some((r) => r.selector === ".added-tile")).toBe(true);
    expect(all.some((r) => r.selector === ".chip" && /fill:/.test(r.body))).toBe(true);
  });

  it("no rule matching a hatched element sets `fill`", () => {
    // The hatch is a `fill` PRESENTATION ATTRIBUTE, which any CSS `fill` beats
    // -- and jsdom applies no stylesheet, so no unit test of the rendered
    // attribute could ever catch it. These two classes exist precisely so the
    // hatched elements match no rule that sets `fill`.
    for (const { selector, body } of rules()) {
      const hatched = /(^|[\s,])\.(added-tile|added-chip)(\.[\w-]+)?([\s,]|$)/.test(selector);
      if (!hatched) continue;
      // `is-saved`/`is-applied` are NOT hatched -- they may set a flat fill.
      if (/is-saved|is-applied/.test(selector)) continue;
      expect(/(^|;)\s*fill\s*:/.test(body), `${selector} { ${body} }`).toBe(false);
    }
  });

  it("pending is legible without colour: a pattern, a dash and a word", () => {
    const text = readFileSync(CSS, "utf8");
    // The hatch pattern's own line, and the dashed stroke both states share.
    expect(text).toMatch(/\.hatch-line\s*\{[^}]*stroke:/);
    expect(text).toMatch(/\.added-tile\s*\{[^}]*stroke-dasharray:/);
    expect(text).toMatch(/\.added-chip\s*\{[^}]*stroke-dasharray:/);
  });
});
