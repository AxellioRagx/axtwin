// @vitest-environment node
/**
 * S4 pins -- the PURE layout half of the acceptance (`docs/WORK_ORDER_console_canvas.md`
 * §3 S4, §4, §5). Pins are ABSOLUTE overrides with NO reflow: a pinned element
 * lands at its top-left, every unpinned element keeps its computed place, and
 * a connector anchored to a moved tile follows. Node env because (b) reads a
 * committed golden off disk (LES-260).
 *
 *   (a) LOCALITY   -- one pin leaves every unpinned element byte-identical.
 *   (b) RESET      -- empty pins == the pure no-pins render == the golden.
 *   (c) PLACEMENT  -- a pinned tile/site lands at its top-left; the east-west
 *                     arcs re-anchor to it (R4; they were lanes until then).
 *   (d) HOST GUARD -- a host/address/hostname/mac pin key changes nothing.
 *
 * The DRAG gesture and the UI affordance are the jsdom half, in
 * `pins.ui.test.tsx`.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import { buildGraph } from "../src/model/graph";
import { layout0 } from "../src/layout/level0";
import { NO_PINS, type Pins } from "../src/layout/pins";
import { draggedTopLeft, isSuppressedClick, layoutScaleOf } from "../src/views/usePinDrag";
import { goldenText } from "./golden-data";
import { trimmedCast, trimmedExport } from "./fixtures/trimmed";

const graph = buildGraph(trimmedExport, trimmedCast);
const GOLDEN_DIR = fileURLToPath(new URL("./layout/", import.meta.url));

// The 10.80 site (10.80.20.0/24 + 10.80.72.0/24) is the pin subject: a conduit
// touches 10.80.20.0/24, so the lane-follows-a-pin path has real data.
const SEG = "10.80.20.0/24";
const SEG_NAME = graph.segmentByCidr.get(SEG)!.name; // seg-10-80-20-0
const SITE = "site:10.80";

describe("S4 pins -- locality (a): a pin leaves unpinned elements byte-identical", () => {
  it("a segment pin moves only that tile; all other tiles, sites and chrome are unchanged", () => {
    const base = layout0(graph);
    const baseTile = base.tiles.find((t) => t.segment.observedCidr === SEG)!;
    const pins: Pins = { [`segment:${SEG}`]: { x: baseTile.box.x + 900, y: baseTile.box.y + 500 } };
    const pinned = layout0(graph, pins);

    // The chrome (centred on the total width, which a pin never changes) and
    // every SITE column are untouched by a segment pin.
    expect(pinned.internet).toEqual(base.internet);
    expect(pinned.wanEdge).toEqual(base.wanEdge);
    expect(pinned.width).toBe(base.width);
    expect(pinned.height).toBe(base.height);
    // Still true after R3, and for a reason worth keeping: a SEGMENT pin moves
    // one tile, never a column, and a device is placed over its COLUMN.
    expect(pinned.infraDevices).toEqual(base.infraDevices);
    expect(pinned.infraStubs).toEqual(base.infraStubs);
    expect(pinned.unplaced).toEqual(base.unplaced);
    expect(pinned.sites).toEqual(base.sites);

    // Every OTHER tile is at the same coordinates.
    for (const t of base.tiles) {
      if (t.segment.observedCidr === SEG) continue;
      const after = pinned.tiles.find((x) => x.segment.observedCidr === t.segment.observedCidr)!;
      expect(after.box, t.segment.observedCidr).toEqual(t.box);
    }
    // The pinned tile moved to the pin.
    const moved = pinned.tiles.find((t) => t.segment.observedCidr === SEG)!;
    expect(moved.box).toEqual({ x: baseTile.box.x + 900, y: baseTile.box.y + 500, w: baseTile.box.w, h: baseTile.box.h });

    // Arcs NOT touching the pinned segment are byte-identical; the ones that
    // touch it differ (they follow the moved tile -- proven in (c)). R4: the
    // comparison is the whole curve, since an arc is four points rather than a
    // polyline.
    const curve = (a: { from: unknown; c1: unknown; c2: unknown; to: unknown }) => ({
      from: a.from,
      c1: a.c1,
      c2: a.c2,
      to: a.to,
    });
    expect(base.arcs.length).toBeGreaterThan(0);
    for (const arcBefore of base.arcs) {
      const arcAfter = pinned.arcs.find((a) => a.a === arcBefore.a && a.b === arcBefore.b)!;
      if (arcBefore.a === SEG_NAME || arcBefore.b === SEG_NAME) {
        expect(curve(arcAfter), `${arcBefore.a}|${arcBefore.b}`).not.toEqual(curve(arcBefore));
      } else {
        expect(curve(arcAfter), `${arcBefore.a}|${arcBefore.b}`).toEqual(curve(arcBefore));
      }
    }
  });

  it("a site pin moves that site's tiles only; other sites and their tiles are unchanged", () => {
    const base = layout0(graph);
    const site = base.sites.find((s) => s.site.key === SITE)!;
    const dx = 700;
    const dy = 400;
    const pinned = layout0(graph, { [SITE]: { x: site.box.x + dx, y: site.box.y + dy } });

    expect(pinned.internet).toEqual(base.internet);
    expect(pinned.width).toBe(base.width);
    expect(pinned.unplaced).toEqual(base.unplaced);

    // R3 (LES-299): a site pin no longer leaves EVERY device untouched, and
    // restating that is the point rather than deleting the assertion. A
    // device's x IS its column's centre, so the dragged column takes its own
    // devices with it (in x only -- they stay in the INFRASTRUCTURE band); a
    // device over any OTHER column is still byte-identical, which is the
    // locality claim this test is actually about.
    for (const d of base.infraDevices) {
      const after = pinned.infraDevices.find((x) => x.node.id === d.node.id)!;
      if (d.siteKey === SITE) {
        expect(after.box, d.node.id).toEqual({ ...d.box, x: d.box.x + dx });
      } else {
        expect(after, d.node.id).toEqual(d);
      }
    }
    expect(base.infraDevices.some((d) => d.siteKey === SITE)).toBe(true);
    expect(base.infraDevices.some((d) => d.siteKey !== SITE)).toBe(true);

    const memberCidrs = new Set(site.site.segments.map((seg) => seg.observedCidr));
    for (const sp of base.sites) {
      const after = pinned.sites.find((x) => x.site.key === sp.site.key)!;
      if (sp.site.key === SITE) {
        expect(after.box).toEqual({ ...sp.box, x: sp.box.x + dx, y: sp.box.y + dy });
        expect(after.header).toEqual({ ...sp.header, x: sp.header.x + dx, y: sp.header.y + dy });
      } else {
        expect(after.box, sp.site.key).toEqual(sp.box);
        expect(after.header, sp.site.key).toEqual(sp.header);
      }
    }
    for (const t of base.tiles) {
      const after = pinned.tiles.find((x) => x.segment.observedCidr === t.segment.observedCidr)!;
      if (memberCidrs.has(t.segment.observedCidr)) {
        expect(after.box).toEqual({ ...t.box, x: t.box.x + dx, y: t.box.y + dy });
      } else {
        expect(after.box, t.segment.observedCidr).toEqual(t.box);
      }
    }
  });
});

describe("S4 pins -- reset (b): empty pins reproduce the pure no-pins render", () => {
  it("layout0 with {} is byte-identical to layout0 with NO_PINS", () => {
    expect(goldenText(layout0(graph, {}))).toBe(goldenText(layout0(graph, NO_PINS)));
  });

  it("layout0 with {} reproduces the committed level0 golden byte-for-byte", () => {
    // Confirms `npm run layout:golden` with empty pins keeps the goldens
    // unchanged -- the pin seam adds behaviour, never changes the no-pins result.
    const golden = readFileSync(`${GOLDEN_DIR}level0.golden.json`, "utf8");
    expect(goldenText(layout0(graph, {}))).toBe(golden);
  });

  it("a reset AFTER a pin restores coordinates byte-identical to the no-pins render", () => {
    const pinned = layout0(graph, { [SITE]: { x: 5, y: 5 }, [`segment:${SEG}`]: { x: 9, y: 9 } });
    const reset = layout0(graph, {}); // reset pins == call with an empty set
    expect(goldenText(reset)).toBe(goldenText(layout0(graph)));
    expect(goldenText(reset)).not.toBe(goldenText(pinned));
  });
});

describe("S4 pins -- placement (c): a pinned element lands at its top-left; connectors follow", () => {
  it("a segment tile lands exactly at its pinned top-left", () => {
    const pinned = layout0(graph, { [`segment:${SEG}`]: { x: 1234, y: 777 } });
    const t = pinned.tiles.find((x) => x.segment.observedCidr === SEG)!;
    expect(t.box.x).toBe(1234);
    expect(t.box.y).toBe(777);
  });

  it("the pinned tile's east-west arc re-anchors to the moved tile's x (R4)", () => {
    // R4's anchor rule, and its consequence for a TILE pin: an arc leaves its
    // GROUP's bottom edge at its TILE's centre x. A segment pin moves the tile
    // and not the column, so the arc follows in x and keeps the group's y --
    // which is the ruling (§4.1), not an oversight, and is why this assertion
    // reads differently from the lane one it replaces.
    const pinned = layout0(graph, { [`segment:${SEG}`]: { x: 1234, y: 777 } });
    const t = pinned.tiles.find((x) => x.segment.observedCidr === SEG)!;
    const column = pinned.sites.find((s) => s.site.key === SITE)!.box;
    const movedX = t.box.x + t.box.w / 2;
    const groupBottom = column.y + column.h;
    const touching = pinned.arcs.filter((a) => a.a === SEG_NAME || a.b === SEG_NAME);
    expect(touching.length).toBeGreaterThan(0);
    for (const arc of touching) {
      const end = arc.a === SEG_NAME ? arc.from : arc.to;
      expect(end.x, `${arc.a}|${arc.b}`).toBe(movedX);
      expect(end.y, `${arc.a}|${arc.b}`).toBe(groupBottom);
    }
  });

  it("a site column lands at its pinned top-left and its tiles follow by the same delta", () => {
    const base = layout0(graph);
    const site = base.sites.find((s) => s.site.key === SITE)!;
    const target = { x: 42, y: 84 };
    const dx = target.x - site.box.x;
    const dy = target.y - site.box.y;
    const pinned = layout0(graph, { [SITE]: target });
    const moved = pinned.sites.find((s) => s.site.key === SITE)!;
    expect(moved.box.x).toBe(target.x);
    expect(moved.box.y).toBe(target.y);
    for (const seg of site.site.segments) {
      const bt = base.tiles.find((t) => t.segment.observedCidr === seg.observedCidr)!;
      const at = pinned.tiles.find((t) => t.segment.observedCidr === seg.observedCidr)!;
      expect(at.box.x, seg.observedCidr).toBe(bt.box.x + dx);
      expect(at.box.y, seg.observedCidr).toBe(bt.box.y + dy);
    }
  });
});

describe("S4 pins -- hosts are never pinnable (d, layout half)", () => {
  it("a host / address / hostname / mac pin key changes nothing (no matching element)", () => {
    const hostPins: Pins = {
      "host:address:10.80.20.10": { x: 5, y: 5 },
      "address:10.80.20.10": { x: 9, y: 9 },
      "hostname:axdc01": { x: 1, y: 1 },
      "mac:aa:bb:cc:dd:ee:ff": { x: 2, y: 2 },
    };
    // A non-empty pin set forces `applyPins` to run its full mapping -- no key
    // matches a site or a tile, so the output is byte-identical to no-pins.
    expect(goldenText(layout0(graph, hostPins))).toBe(goldenText(layout0(graph)));
  });
});

describe("layoutScaleOf -- the client->layout scale captured once at begin (S2)", () => {
  it("prefers the CTM: layout units per client pixel is 1/a, 1/d", () => {
    const svg = {
      getScreenCTM: () => ({ a: 0.5, d: 0.25 }), // layout->screen; 0.5x, 0.25x
    } as unknown as SVGSVGElement;
    expect(layoutScaleOf(svg)).toEqual({ sx: 2, sy: 4 });
  });

  it("falls back to the bounding-rect / viewBox ratio when no CTM is available", () => {
    const svg = {
      getScreenCTM: () => null,
      getBoundingClientRect: () => ({ left: 10, top: 20, width: 200, height: 100 }),
      viewBox: { baseVal: { x: 0, y: 0, width: 400, height: 200 } },
    } as unknown as SVGSVGElement;
    expect(layoutScaleOf(svg)).toEqual({ sx: 2, sy: 2 });
  });

  it("degrades to identity rather than throwing with no CTM and no viewBox", () => {
    const svg = {
      getScreenCTM: () => null,
      getBoundingClientRect: () => ({ left: 0, top: 0, width: 0, height: 0 }),
    } as unknown as SVGSVGElement;
    expect(layoutScaleOf(svg)).toEqual({ sx: 1, sy: 1 });
  });
});

describe("draggedTopLeft -- the S2 delta stays 1:1 in LAYOUT units under a non-identity scale", () => {
  it("scales the client delta by the captured scale, from the grabbed top-left", () => {
    const origin = { x: 100, y: 200 };
    const down = { x: 10, y: 10 };
    const scale = { sx: 2, sy: 2 }; // a 0.5x CTM: 1 client px = 2 layout units
    // A +20 client move is a +40 layout move -- consistent, not accelerating.
    expect(draggedTopLeft(origin, down, { x: 30, y: 30 }, scale)).toEqual({ x: 140, y: 240 });
    expect(draggedTopLeft(origin, down, { x: 50, y: 50 }, scale)).toEqual({ x: 180, y: 280 });
    // The per-client-pixel layout delta is CONSTANT across the two moves (2x),
    // which is exactly what a growing viewBox used to break (drift/accel).
    const a = draggedTopLeft(origin, down, { x: 30, y: 30 }, scale);
    const b = draggedTopLeft(origin, down, { x: 50, y: 50 }, scale);
    expect((b.x - a.x) / (50 - 30)).toBe(2);
  });
});

describe("isSuppressedClick -- the trailing-click window is bounded by TIME (S1)", () => {
  const commit = { key: "segment:10.80.20.0/24", at: 1000 };
  it("suppresses a same-key click INSIDE the window", () => {
    expect(isSuppressedClick(commit, "segment:10.80.20.0/24", 1000)).toBe(true);
    expect(isSuppressedClick(commit, "segment:10.80.20.0/24", 1200)).toBe(true);
  });
  it("does NOT suppress once the window has elapsed (the S1 fix)", () => {
    // The compat click routed to the <svg>, so it never cleared the flag; a
    // genuine drill on the tile 300 ms later must go through.
    expect(isSuppressedClick(commit, "segment:10.80.20.0/24", 1300)).toBe(false);
  });
  it("never suppresses a different key or with no armed commit", () => {
    expect(isSuppressedClick(commit, "segment:10.20.192.0/24", 1050)).toBe(false);
    expect(isSuppressedClick(null, "segment:10.80.20.0/24", 1050)).toBe(false);
  });
});
