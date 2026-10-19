// @vitest-environment node
/**
 * R3 — infrastructure over its group, and role badges on tiles
 * (`docs/WORK_ORDER_console_level0_rendering.md` §3 R3), the pure half: the
 * device→column placement, the row grid and the band it sizes, the stubs that
 * replaced the diagonal tie-lines (§2 F3), and the badge derivation.
 *
 * `node` environment because nothing here renders; the DRAWING half — that the
 * stubs reach the DOM, that no `.tie` line survives, that a badge is legible
 * text rather than a coloured sliver — is `r3.ui.test.tsx`.
 */
import { describe, expect, it } from "vitest";

import { buildGraph } from "../src/model/graph";
import { layout0 } from "../src/layout/level0";
import {
  devicesAcross,
  infraBandHeight,
  infraBandRows,
  infraGridFor,
  infraRows,
} from "../src/layout/infra";
import {
  DEVICE_GAP_X,
  DEVICE_GAP_Y,
  DEVICE_H,
  DEVICE_W,
  SITE_COLS,
  SITE_PAD,
  TIER_INFRA_H,
  TILE_GAP_X,
  TILE_W,
  nearestEdgeY,
} from "../src/layout/geometry";
import { INFRA_DEVICE_ROLES, ROLE_BADGE_MAX, roleBadgeLabel } from "../src/model/roles";
import { noConnectorCrossesATile, polylineCrossesBox } from "./layout/helpers";
import {
  trimmedCast,
  trimmedExport,
  withThreeDeviceRows,
  withUnplacedDevice,
  withWrappingDevices,
} from "./fixtures/trimmed";

const graph = buildGraph(trimmedExport, trimmedCast);
const COLUMN_W = SITE_COLS * TILE_W + (SITE_COLS - 1) * TILE_GAP_X + 2 * SITE_PAD;

/** Device counts as the per-column shape `infraRows`/`infraBandRows` take
 *  since R6 (a column's WIDTH is now per group, so the band cannot be sized
 *  from one shared width). Every case here is a full-width column, which is
 *  what these tests were always asserting about. */
const cols = (...counts: number[]): Array<{ count: number; width: number }> =>
  counts.map((count) => ({ count, width: COLUMN_W }));

/** The fixture's three devices: a DC and a resolver in `10.80.20.0/24` (site
 *  10.80) and a collector in `10.20.192.0/24` (site 10.20). */
const DC = "hostname:axdc01";
const RESOLVER = "address:10.80.20.5";
const COLLECTOR = "address:10.20.192.240";

// ===========================================================================
// The row grid and the band it sizes
// ===========================================================================
describe("the device row grid (R3)", () => {
  it("fits three devices across a site column, and wraps the fourth", () => {
    expect(devicesAcross(COLUMN_W)).toBe(3);
    expect(infraGridFor(3, COLUMN_W)).toEqual({ perRow: 3, rows: 1 });
    // A wrapped column drops to one slot FEWER per row: that is the slack the
    // half-slot offset spends to keep every device inside the column.
    expect(infraGridFor(4, COLUMN_W)).toEqual({ perRow: 2, rows: 2 });
    expect(infraGridFor(5, COLUMN_W)).toEqual({ perRow: 2, rows: 3 });
    expect(infraGridFor(0, COLUMN_W)).toEqual({ perRow: 3, rows: 0 });
  });

  it("leaves ZERO spare slots at the shipped column width", () => {
    // The claim the epoch bump leans on: the busiest real column holds three
    // and three is ALL that fits, so one more device there moves every tile.
    // (`layout.real.test.ts` pins the real bundle against this same function.)
    expect(devicesAcross(COLUMN_W)).toBe(3);
    expect(infraGridFor(devicesAcross(COLUMN_W), COLUMN_W).rows).toBe(1);
    expect(infraGridFor(devicesAcross(COLUMN_W) + 1, COLUMN_W).rows).toBe(2);
  });

  it("gives the unplaced devices a row of their OWN, above the floor", () => {
    // Review S3: the band must be tall enough for the columns AND the centred
    // unplaced row, never share one between them.
    expect(infraBandRows(cols(1, 1), 0)).toBe(1);
    expect(infraBandRows(cols(1, 1), 1)).toBe(2);
    expect(infraBandRows(cols(4, 1), 2)).toBe(3);
    expect(infraBandRows(cols(), 0)).toBe(0);
  });

  it("takes the band from the DEEPEST column, and never below the tier floor", () => {
    expect(infraRows(cols())).toBe(0);
    expect(infraRows(cols(1, 3, 2))).toBe(1);
    expect(infraRows(cols(1, 4, 2))).toBe(2);
    // THE claim that makes this slice move no tile on any bundle that ships:
    // one row of devices is shorter than the tier floor, so the band -- and
    // therefore the segment tier's top edge -- is exactly where R2 left it.
    expect(infraBandHeight(1, TIER_INFRA_H)).toBe(TIER_INFRA_H);
    expect(infraBandHeight(0, TIER_INFRA_H)).toBe(TIER_INFRA_H);
    expect(DEVICE_H).toBeLessThan(TIER_INFRA_H);
    // Two rows do not fit, so the band grows -- and the plane moves with it,
    // which is what `PLANE_EPOCH` 2 carries.
    expect(infraBandHeight(2, TIER_INFRA_H)).toBe(2 * DEVICE_H + DEVICE_GAP_Y);
    expect(infraBandHeight(2, TIER_INFRA_H)).toBeGreaterThan(TIER_INFRA_H);
  });
});

// ===========================================================================
// Placement: every device over the column that holds its segment
// ===========================================================================
describe("an infra device sits over its own site column (R3 acceptance)", () => {
  const l0 = layout0(graph);
  const columnOf = (siteKey: string) => l0.sites.find((s) => s.site.key === siteKey)!.box;

  it("places every device's x-range INSIDE its column's x-range", () => {
    expect(l0.infraDevices).toHaveLength(3);
    for (const d of l0.infraDevices) {
      expect(d.siteKey, d.node.id).not.toBeNull();
      const col = columnOf(d.siteKey!);
      expect(d.box.x, d.node.id).toBeGreaterThanOrEqual(col.x);
      expect(d.box.x + d.box.w, d.node.id).toBeLessThanOrEqual(col.x + col.w);
    }
  });

  it("puts each device over the column holding ITS segment, not the canvas centre", () => {
    const site = new Map(l0.infraDevices.map((d) => [d.node.id, d.siteKey]));
    expect(site.get(DC)).toBe("site:10.80");
    expect(site.get(RESOLVER)).toBe("site:10.80");
    expect(site.get(COLLECTOR)).toBe("site:10.20");
    // Before R3 all three sat in one row centred on the canvas; the two 10.80
    // devices are now right of the 10.20 one, because their column is.
    const x = new Map(l0.infraDevices.map((d) => [d.node.id, d.box.x]));
    expect(x.get(COLLECTOR)!).toBeLessThan(x.get(DC)!);
  });

  it("lays a column's devices out left-to-right in ROLE order", () => {
    // `roles.ts`'s fixed order: domain-controller before dns-resolver.
    const x = new Map(l0.infraDevices.map((d) => [d.node.id, d.box.x]));
    expect(x.get(DC)!).toBeLessThan(x.get(RESOLVER)!);
  });

  it("returns them COLUMN-major, so document order matches what the eye sees", () => {
    // The view renders in array order and a device is a focusable `Nav`, so
    // this is the tab order a keyboard operator walks.
    expect(l0.infraDevices.map((d) => d.siteKey)).toEqual([
      "site:10.20",
      "site:10.80",
      "site:10.80",
    ]);
  });

  it("keeps a single row exactly where the pre-R3 centred row was", () => {
    // The claim that makes R3's golden diff carry no device `y` at all.
    const band = l0.rails.find((r) => r.tier === "infrastructure")!.box;
    for (const d of l0.infraDevices) {
      expect(d.box.y, d.node.id).toBe(band.y + (TIER_INFRA_H - DEVICE_H) / 2);
    }
  });
});

// ===========================================================================
// The stub that replaced the tie-line
// ===========================================================================
describe("the infra stub (R3, retiring F3)", () => {
  const l0 = layout0(graph);

  it("is ONE vertical from the device's bottom to its group header's top", () => {
    expect(l0.infraStubs).toHaveLength(3);
    for (const stub of l0.infraStubs) {
      const device = l0.infraDevices.find((d) => d.node.id === stub.deviceId)!;
      const site = l0.sites.find((s) => s.site.key === stub.siteKey)!;
      expect(stub.points, stub.deviceId).toHaveLength(2);
      // Orthogonal: one x, so it is a vertical and not the diagonal it replaced.
      expect(stub.points[0]!.x, stub.deviceId).toBe(stub.points[1]!.x);
      expect(stub.points[0]!.x, stub.deviceId).toBe(device.box.x + device.box.w / 2);
      expect(stub.points[0]!.y, stub.deviceId).toBe(device.box.y + device.box.h);
      expect(stub.points[1]!.y, stub.deviceId).toBe(site.header.y);
      // Touching the header's top edge is not entering it.
      expect(polylineCrossesBox(stub.points, site.box), stub.deviceId).toBe(false);
    }
  });

  it("crosses NO tile -- the §1.2 property, now covering stubs", () => {
    expect(noConnectorCrossesATile(l0)).toEqual([]);
  });

  it("is SHORT: it spans the tier gap, not the canvas", () => {
    // The F3 complaint was distance as much as direction -- a tie-line ran from
    // the canvas centre to a tile rows below. A stub never leaves the gap
    // between the band and the headers.
    for (const stub of l0.infraStubs) {
      const length = Math.abs(stub.points[1]!.y - stub.points[0]!.y);
      expect(length, stub.deviceId).toBeLessThan(TIER_INFRA_H);
    }
  });

  it("approaches a group pinned ABOVE the band from below, and still enters nothing", () => {
    // The `nearestEdgeY` rule, shared with R2's trunk legs: ending at the top
    // edge regardless would drive the stub through the header it points at.
    const pinned = layout0(graph, { "site:10.80": { x: 900, y: 24 } });
    const site = pinned.sites.find((s) => s.site.key === "site:10.80")!;
    const stubs = pinned.infraStubs.filter((s) => s.siteKey === "site:10.80");
    expect(stubs.length).toBeGreaterThan(0);
    for (const stub of stubs) {
      expect(stub.points[1]!.y, stub.deviceId).toBe(site.header.y + site.header.h);
      expect(polylineCrossesBox(stub.points, site.header), stub.deviceId).toBe(false);
    }
    expect(nearestEdgeY(site.header, 999)).toBe(site.header.y + site.header.h);
  });
});

// ===========================================================================
// Pins: a dragged column takes its devices with it (LES-266)
// ===========================================================================
describe("a pinned site's devices follow its column (R3 × S4)", () => {
  const base = layout0(graph);
  const dx = 700;
  const dy = 400;
  const site = base.sites.find((s) => s.site.key === "site:10.80")!;
  const moved = layout0(graph, { "site:10.80": { x: site.box.x + dx, y: site.box.y + dy } });

  it("moves the pinned column's devices by the same dx, and NOT in y", () => {
    // A device's x IS its column's centre, so it tracks the drag; it stays in
    // the INFRASTRUCTURE band, because that is the tier the rail names.
    for (const id of [DC, RESOLVER]) {
      const before = base.infraDevices.find((d) => d.node.id === id)!;
      const after = moved.infraDevices.find((d) => d.node.id === id)!;
      expect(after.box.x, id).toBe(before.box.x + dx);
      expect(after.box.y, id).toBe(before.box.y);
    }
  });

  it("leaves an UNPINNED column's device byte-identical", () => {
    const before = base.infraDevices.find((d) => d.node.id === COLLECTOR)!;
    const after = moved.infraDevices.find((d) => d.node.id === COLLECTOR)!;
    expect(after).toEqual(before);
    expect(moved.infraStubs.find((s) => s.deviceId === COLLECTOR)).toEqual(
      base.infraStubs.find((s) => s.deviceId === COLLECTOR),
    );
  });

  it("keeps the moved column's stub a VERTICAL, re-anchored to the moved header", () => {
    const header = moved.sites.find((s) => s.site.key === "site:10.80")!.header;
    for (const stub of moved.infraStubs.filter((s) => s.siteKey === "site:10.80")) {
      expect(stub.points[0]!.x, stub.deviceId).toBe(stub.points[1]!.x);
      expect(stub.points[1]!.y, stub.deviceId).toBe(header.y);
      expect(stub.points[1]!.x).toBeGreaterThanOrEqual(header.x);
      expect(stub.points[1]!.x).toBeLessThanOrEqual(header.x + header.w);
    }
  });
});

// ===========================================================================
// A pinned column drags its stub: the OTHER pin limit, both sides recorded
// ===========================================================================
describe("a column pinned under another column's tiles (R3, review M1)", () => {
  // **Re-aimed for R6's plane, and the coordinates ARE the record.** The
  // per-group budget made a group showing four tiles or fewer two columns wide
  // instead of three (352 units, from 524), so every column moved and the
  // arithmetic that aimed the pre-R6 coordinates describes a plane that no
  // longer exists. Both sides were re-measured by sweeping x ∈ [140, 1400] step
  // 20 × y ∈ {620, 300}, dragging each column in turn: 31 of the 132 positions
  // put a stub through another column's tile.
  //
  // `site:10.20` holds ONE device, so its stub sits on the moved column's
  // centre. The violating pin is therefore DERIVED rather than written as a
  // literal -- park that centre on another column's tile and drop the column
  // below that tile's row -- so the next plane change re-aims it instead of
  // falsifying it (R6 is the second such change in three slices).
  const pure = layout0(graph);
  const victim = pure.tiles.find((t) => t.siteKey === "site:10.80")!;
  const movedColumn = pure.sites.find((s) => s.site.key === "site:10.20")!.box;
  const crossing = layout0(graph, {
    "site:10.20": {
      x: victim.box.x + victim.box.w / 2 - movedColumn.w / 2,
      y: Math.max(...pure.tiles.map((t) => t.box.y + t.box.h)) + 40,
    },
  });
  // And a CLEAN coordinate beside it, so neither reads as the rule. x=420,
  // y=620 is clean for every connector family (the same coordinate
  // `r2.trunk.test.ts` and `r4.arcs.test.ts` now use); it is a window, not a
  // property -- 36 of the 132 swept positions put a stub through a tile.
  const clean = layout0(graph, { "site:10.80": { x: 420, y: 620 } });

  it("DOES cross -- a measured limit, not a corner case", () => {
    const violations = noConnectorCrossesATile(crossing).filter((v) =>
      v.startsWith("infra stub"),
    );
    expect(
      violations.length,
      "if this is empty the limit is gone -- delete this test and the note in " +
        "`layout/infra.ts`, and say so in the work order's §8",
    ).toBeGreaterThan(0);
    // It crosses a tile belonging to ANOTHER column, which is the claim --
    // read off the layout rather than hardcoded, so a plane change re-aims it
    // instead of falsifying it.
    const foreign = crossing.tiles.filter((t) => t.siteKey !== "site:10.20");
    expect(violations.some((v) => foreign.some((t) => v.includes(t.segment.observedCidr)))).toBe(
      true,
    );
  });

  it("and is clean at the R2 coordinate -- both sides pinned, so neither reads as the rule", () => {
    expect(noConnectorCrossesATile(clean).filter((v) => v.startsWith("infra stub"))).toEqual([]);
  });

  it("is inherent to the no-reflow pin, not to the stub's own geometry", () => {
    // Whatever the pin, the stub is still ONE vertical ending on its own
    // header: it is the column that was parked somewhere impossible.
    for (const stub of crossing.infraStubs) {
      expect(stub.points[0]!.x, stub.deviceId).toBe(stub.points[1]!.x);
      const header = crossing.sites.find((s) => s.site.key === stub.siteKey)!.header;
      expect(stub.points[1]!.x).toBeGreaterThanOrEqual(header.x);
      expect(stub.points[1]!.x).toBeLessThanOrEqual(header.x + header.w);
    }
  });
});

// ===========================================================================
// An unplaced device: centred, dashed, and connected to nothing
// ===========================================================================
describe("a device whose segment has no tile (R3)", () => {
  const l0 = layout0(buildGraph(withUnplacedDevice(), trimmedCast));
  const orphan = l0.infraDevices.find((d) => d.node.id === "address:10.250.0.9")!;

  it("stays CENTRED on the canvas, as before R3", () => {
    expect(orphan).toBeDefined();
    expect(orphan.siteKey).toBeNull();
    // The chrome's centre: the Internet pill is centred on the same midX.
    expect(orphan.box.x + orphan.box.w / 2).toBe(l0.internet.x + l0.internet.w / 2);
  });

  it("gets NO stub -- there is no group to point at", () => {
    expect(l0.infraStubs.some((s) => s.deviceId === orphan.node.id)).toBe(false);
    // ...while every PLACED device still has one.
    expect(l0.infraStubs).toHaveLength(l0.infraDevices.length - 1);
  });

  it("is ordered last, after every placed device", () => {
    expect(l0.infraDevices[l0.infraDevices.length - 1]!.node.id).toBe(orphan.node.id);
  });

  it("takes its OWN row, so it can never be drawn on top of a placed device", () => {
    // Review S3. Unplaced devices are centred on the canvas midpoint -- a
    // position no column owns -- so sharing a row with the columns means an
    // overlap whenever that midpoint falls over an occupied slot. It does not
    // on this fixture (midX 1251 vs centres 339/813/981) or on either real
    // bundle, which is precisely why the row separation is structural rather
    // than left to coordinates.
    const placed = l0.infraDevices.filter((d) => d.siteKey !== null);
    expect(orphan.box.y).toBeGreaterThan(placed[0]!.box.y);
    for (const a of l0.infraDevices) {
      for (const b of l0.infraDevices) {
        if (a.node.id === b.node.id) continue;
        const overlaps =
          a.box.x < b.box.x + b.box.w &&
          b.box.x < a.box.x + a.box.w &&
          a.box.y < b.box.y + b.box.h &&
          b.box.y < a.box.y + a.box.h;
        expect(overlaps, `${a.node.id} overlaps ${b.node.id}`).toBe(false);
      }
    }
  });

  it("crosses no tile, and moves the tiles down by exactly its row", () => {
    expect(noConnectorCrossesATile(l0)).toEqual([]);
    const base = layout0(graph);
    // The honest cost of S3's own row: the band grows, so the segment tier does
    // move. No golden carries an unplaced device, so no golden moved with it.
    const band = l0.rails.find((r) => r.tier === "infrastructure")!.box;
    expect(band.h).toBe(2 * DEVICE_H + DEVICE_GAP_Y);
    const delta = band.h - TIER_INFRA_H;
    for (const t of base.tiles) {
      const after = l0.tiles.find((x) => x.segment.observedCidr === t.segment.observedCidr)!;
      expect(after.box.y, t.segment.observedCidr).toBe(t.box.y + delta);
      expect(after.box.x, t.segment.observedCidr).toBe(t.box.x);
    }
  });
});

// ===========================================================================
// Wrapping: the second row, the grown band, and the limit past it
// ===========================================================================
describe("a column whose devices WRAP (R3)", () => {
  const base = layout0(graph);
  const l0 = layout0(buildGraph(withWrappingDevices(), trimmedCast));
  const column = l0.sites.find((s) => s.site.key === "site:10.80")!.box;
  const wrapped = l0.infraDevices.filter((d) => d.siteKey === "site:10.80");

  // **Restated for R6's narrower column (LES-299), not weakened.** This group
  // shows two tiles, so its column is now two tiles wide (352 units, from 524)
  // and fits TWO devices across instead of three. `infraGridFor` then drops to
  // one slot fewer per row -- one device per row -- so the same four devices
  // wrap into four rows rather than two. The properties asserted are the same
  // three: inside the column, the band grows to hold them, and adjacent rows
  // are offset by half a slot.
  it("puts four devices in FOUR rows at a two-across column, every one still inside it", () => {
    expect(wrapped).toHaveLength(4);
    expect(devicesAcross(column.w)).toBe(2);
    expect(infraGridFor(4, column.w)).toEqual({ perRow: 1, rows: 4 });
    const rows = new Set(wrapped.map((d) => d.box.y));
    expect(rows.size).toBe(4);
    for (const d of wrapped) {
      expect(d.box.x, d.node.id).toBeGreaterThanOrEqual(column.x);
      expect(d.box.x + d.box.w, d.node.id).toBeLessThanOrEqual(column.x + column.w);
    }
  });

  it("grows the INFRASTRUCTURE band instead of overlapping the tier beneath", () => {
    const band = l0.rails.find((r) => r.tier === "infrastructure")!.box;
    expect(band.h).toBe(4 * DEVICE_H + 3 * DEVICE_GAP_Y);
    // ...and THAT is the plane change: every tile moves down by the difference.
    const delta = band.h - TIER_INFRA_H;
    expect(delta).toBeGreaterThan(0);
    for (const t of base.tiles) {
      const after = l0.tiles.find((x) => x.segment.observedCidr === t.segment.observedCidr)!;
      expect(after.box.y, t.segment.observedCidr).toBe(t.box.y + delta);
      expect(after.box.x, t.segment.observedCidr).toBe(t.box.x);
    }
  });

  it("offsets ADJACENT rows by half a slot, and lapses beyond two rows", () => {
    // The F3 defect, one tier up: a vertical from an upper-row device that
    // lands on the box below it is the same "line through whatever lies
    // between" this slice exists to retire. Exact between ADJACENT rows...
    const half = (DEVICE_W + DEVICE_GAP_X) / 2;
    const rowYs = [...new Set(wrapped.map((d) => d.box.y))].sort((a, b) => a - b);
    const centres = (y: number) =>
      wrapped.filter((d) => d.box.y === y).map((d) => d.box.x + d.box.w / 2);
    for (let i = 1; i < rowYs.length; i++) {
      for (const upper of centres(rowYs[i - 1]!)) {
        for (const lower of centres(rowYs[i]!)) {
          expect(Math.abs(upper - lower), `${upper} vs ${lower}`).toBeGreaterThanOrEqual(half);
        }
      }
    }
    // ...and NOT beyond them: rows 0 and 2 share a parity, so they share an x,
    // and a stub from the first descends through the third. R3 recorded this
    // limit at three rows (`layout/infra.ts`, §8); R6's two-across column makes
    // it reachable at FOUR devices in a small group rather than five in a wide
    // one, because one device per row is all such a column fits. Still a
    // fixture-only case: no bundle in the tree has a device in a small group.
    expect(centres(rowYs[0]!)).toEqual(centres(rowYs[2]!));
    const throughADevice = l0.infraStubs.filter((stub) =>
      l0.infraDevices.some(
        (d) => d.node.id !== stub.deviceId && polylineCrossesBox(stub.points, d.box),
      ),
    );
    expect(
      throughADevice.length,
      "if this is empty the alternate-row clearance now holds at 4 rows -- " +
        "delete this half of the test and the limit in `layout/infra.ts`",
    ).toBeGreaterThan(0);
  });

  it("still crosses no TILE, which is the §1.2 property (the limit above is device-on-device)", () => {
    expect(noConnectorCrossesATile(l0)).toEqual([]);
  });

  it("[retired assertion, kept as the record] no stub crossed a device at TWO rows", () => {
    // The pre-R6 form of the test above, restated rather than deleted
    // (LES-299): at two rows the offset is exact and this held outright. The
    // two-row case is no longer reachable from this fixture -- a two-across
    // column wraps one device per row -- so the claim is asserted through the
    // grid function instead of through a layout.
    expect(infraGridFor(3, 524).rows).toBe(1);
    expect(infraGridFor(4, 524)).toEqual({ perRow: 2, rows: 2 });
  });

  it("keeps every stub clear of the device in the row DIRECTLY below it", () => {
    // What the half-slot offset actually buys, stated as the property it
    // guarantees rather than as the whole-band claim it cannot (the rows that
    // share a parity are covered, as a measured limit, two tests up). Before
    // R6 this was asserted over every device pair, because two rows was all
    // this fixture could reach.
    const rowYs = [...new Set(l0.infraDevices.map((d) => d.box.y))].sort((a, b) => a - b);
    const rowOf = (y: number) => rowYs.indexOf(y);
    for (const stub of l0.infraStubs) {
      const source = l0.infraDevices.find((d) => d.node.id === stub.deviceId)!;
      for (const device of l0.infraDevices) {
        if (device.node.id === stub.deviceId) continue;
        if (rowOf(device.box.y) !== rowOf(source.box.y) + 1) continue;
        expect(
          polylineCrossesBox(stub.points, device.box),
          `${stub.deviceId} crosses ${device.node.id} one row below it`,
        ).toBe(false);
      }
    }
  });

  it("still crosses no tile", () => {
    expect(noConnectorCrossesATile(l0)).toEqual([]);
  });

  it("at THREE rows the clearance lapses -- measured, not claimed away", () => {
    // Rows 1 and 3 share a parity, so they share a slot grid and a stub from
    // the middle row lands on the row below it. Fixing it needs real routing
    // (a dogleg per device), which no bundle in the tree justifies: the
    // busiest real column holds three devices in ONE row. Recorded here with
    // the shape that produces it, in R2's idiom.
    const three = layout0(buildGraph(withThreeDeviceRows(), trimmedCast));
    const hits: string[] = [];
    for (const stub of three.infraStubs) {
      for (const device of three.infraDevices) {
        if (device.node.id === stub.deviceId) continue;
        if (polylineCrossesBox(stub.points, device.box)) {
          hits.push(`${stub.deviceId} → ${device.node.id}`);
        }
      }
    }
    expect(
      hits.length,
      "if this is empty the three-row limit is gone -- delete this test and the note in `layout/infra.ts`",
    ).toBeGreaterThan(0);
    // Even then, no stub crosses a TILE: the §1.2 property holds regardless.
    expect(noConnectorCrossesATile(three)).toEqual([]);
  });
});

// ===========================================================================
// Role badges on the tile
// ===========================================================================
describe("tile role badges (R3)", () => {
  it("reserves a slot for an infrastructure role, then fills by count", () => {
    // 10.80.20.0/24: 11 generic-endpoint, 3 corp-workstation, 2 unroled,
    // 1 domain-controller, 1 dns-resolver -- five buckets, three badges.
    //
    // A pure count ranking would show `EP 11 / WS 3 / NONE 2` and hide BOTH
    // infrastructure roles, which is the measured behaviour on both real
    // bundles too (the pit export hides `domain-controller 2` behind nine
    // unfingerprinted endpoints). The two busiest buckets there are absences
    // of a fingerprint, not roles.
    const seg = graph.segmentByCidr.get("10.80.20.0/24")!;
    expect(seg.roleHistogram.length).toBeGreaterThan(ROLE_BADGE_MAX);
    expect(seg.roleBadges).toEqual([
      { role: "domain-controller", count: 1 },
      { role: "dns-resolver", count: 1 },
      { role: "generic-endpoint", count: 11 },
    ]);
    // The reservation is exactly `INFRA_DEVICE_ROLES` -- the set that earns a
    // box in the band -- not a second vocabulary.
    for (const badge of seg.roleBadges.slice(0, 2)) {
      expect(INFRA_DEVICE_ROLES.has(badge.role), badge.role).toBe(true);
    }
  });

  it("ranks a segment with NO infrastructure role purely by count", () => {
    // 10.80.72.0/24: 27 generic-endpoint + 1 web-tier. `web-tier` is an infra
    // ROLE but not an infra DEVICE role, so nothing is reserved here.
    const seg = graph.segmentByCidr.get("10.80.72.0/24")!;
    expect(seg.roleBadges).toEqual([
      { role: "generic-endpoint", count: 27 },
      { role: "web-tier", count: 1 },
    ]);
    expect(seg.roleBadges.some((b) => INFRA_DEVICE_ROLES.has(b.role))).toBe(false);
  });

  it("breaks a count tie by the fixed role order, never the alphabet", () => {
    // 10.1.10.0/24: one network-device and one generic-endpoint, both 1.
    // `roles.ts` ranks the infrastructure role first; the alphabet would not.
    const seg = graph.segmentByCidr.get("10.1.10.0/24")!;
    expect(seg.roleBadges.map((b) => b.role)).toEqual(["network-device", "generic-endpoint"]);
  });

  it("never exceeds the badge budget, and is empty for an empty segment", () => {
    for (const seg of graph.segments) {
      expect(seg.roleBadges.length, seg.observedCidr).toBeLessThanOrEqual(ROLE_BADGE_MAX);
      // Every badge is a real bucket of the histogram it came from.
      for (const badge of seg.roleBadges) {
        expect(seg.roleHistogram, seg.observedCidr).toContainEqual(badge);
      }
    }
    expect(graph.segmentByCidr.get("192.168.0.0/24")!.roleBadges).toEqual([
      { role: "generic-endpoint", count: 1 },
    ]);
  });

  it("abbreviates a known role, and derives a TOTAL label for an unknown one", () => {
    expect(roleBadgeLabel("domain-controller")).toBe("DC");
    expect(roleBadgeLabel("dns-resolver")).toBe("DNS");
    expect(roleBadgeLabel("generic-endpoint")).toBe("EP");
    expect(roleBadgeLabel("unroled-endpoint")).toBe("NONE");
    // Unknown roles still get a badge -- `roles.ts` decides where a role sits,
    // never whether it exists.
    expect(roleBadgeLabel("ot-historian")).toBe("OH");
    expect(roleBadgeLabel("hypervisor")).toBe("HV");
    expect(roleBadgeLabel("kubernetes")).toBe("KUBE");
  });

  it("is TOTAL: never blank, for empty OR whitespace-only input", () => {
    // `roleBucket` returns `role ?? UNROLED`, so an export's `role: ""` reaches
    // here as its own bucket -- and `"   "` is WORSE than `""`, because
    // whitespace is truthy and an untrimmed derivation returns it unchanged:
    // a badge with no glyphs, and an aria-label that reads as though a role had
    // been named.
    //
    // Asserted as "non-empty AFTER trimming" as well as by value, because
    // `not.toBe("")` -- the form this test shipped with -- PASSES on `"   "`
    // and so established nothing.
    for (const blank of ["", "   ", "\t", "-", " - ", "--"]) {
      const label = roleBadgeLabel(blank);
      expect(label.trim(), `roleBadgeLabel(${JSON.stringify(blank)})`).not.toBe("");
      expect(label, `roleBadgeLabel(${JSON.stringify(blank)})`).toBe("NONE");
    }
    // A padded but REAL role still resolves to its own abbreviation, not the
    // unroled fallback -- the trim is a normalisation, not a discard.
    expect(roleBadgeLabel(" dns-resolver ")).toBe("DNS");
  });
});
