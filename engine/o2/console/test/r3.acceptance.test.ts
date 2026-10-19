// @vitest-environment node
/**
 * R3 acceptance, verified INDEPENDENTLY of the tests that shipped with it
 * (`docs/WORK_ORDER_console_level0_rendering.md` §3 R3).
 *
 * The shipped R3 tests are thorough. This file covers the three things they
 * cannot certify about themselves:
 *
 *  1. **A guard on the crossing guard.** `r3.infra.test.ts` asserts
 *     `noConnectorCrossesATile(l0)` is `[]` now that stubs are in the helper.
 *     That assertion passes just as happily if the stubs were never ADDED to
 *     `connectorsOf` — an empty contribution crosses nothing. Forcing a stub
 *     to cross proves the check can see stubs at all.
 *  2. **The pin limit that nothing asserts.** `layout/infra.ts` documents the
 *     WRAP limit (three rows) and `r3.infra.test.ts` measures it. The other
 *     limit — a column pinned under ANOTHER column's tiles drags its stub
 *     through them — is stated nowhere in a test. It is a real consequence of
 *     "a pin is an absolute override with no reflow" (`15` §4), so it is
 *     measured here in the same idiom rather than left to be discovered.
 *  3. **The `0 * Infinity` regression.** The unplaced-device path once
 *     produced `NaN` for every coordinate. `r3.infra.test.ts` catches that
 *     incidentally (a `NaN` fails its centre assertion); this states it
 *     directly, over EVERY number the layout publishes, so the next edit to
 *     that path cannot reintroduce a NaN somewhere nobody happens to assert.
 */
import { existsSync, lstatSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import type { TopologyExport } from "../src/api/types";
import { buildGraph } from "../src/model/graph";
import { layout0 } from "../src/layout/level0";
import { roleBadgeLabel } from "../src/model/roles";
import { noConnectorCrossesATile } from "./layout/helpers";
import { trimmedCast, trimmedExport, withUnplacedDevice } from "./fixtures/trimmed";

const graph = buildGraph(trimmedExport, trimmedCast);

// ===========================================================================
// 1. Acceptance, computed independently: every device inside its own column.
// ===========================================================================
describe("R3 acceptance: a device's x-range lies within its column's", () => {
  it("holds for every placed device on the shipped fixture", () => {
    const l0 = layout0(graph);
    const columns = new Map(l0.sites.map((s) => [s.site.key, s.box]));
    expect(l0.infraDevices.length).toBeGreaterThan(0);
    for (const d of l0.infraDevices) {
      expect(d.siteKey, d.node.id).not.toBeNull();
      const col = columns.get(d.siteKey!);
      expect(col, `${d.node.id} names a column that is not drawn`).toBeDefined();
      expect(d.box.x, d.node.id).toBeGreaterThanOrEqual(col!.x);
      expect(d.box.x + d.box.w, d.node.id).toBeLessThanOrEqual(col!.x + col!.w);
    }
  });

  it("and every stub lands INSIDE its own group's header span", () => {
    // The reason a stub is a single vertical at all: the device is over the
    // column, so the drop cannot miss the header.
    const l0 = layout0(graph);
    const headers = new Map(l0.sites.map((s) => [s.site.key, s.header]));
    for (const stub of l0.infraStubs) {
      const header = headers.get(stub.siteKey)!;
      const x = stub.points[stub.points.length - 1]!.x;
      expect(x, stub.deviceId).toBeGreaterThanOrEqual(header.x);
      expect(x, stub.deviceId).toBeLessThanOrEqual(header.x + header.w);
    }
  });
});

// ===========================================================================
// 2. The guard on the crossing guard + the unasserted pin limit.
// ===========================================================================
describe("R3: the crossing check can actually SEE a stub", () => {
  it("names an `infra stub` violation for a stub drawn through a tile", () => {
    // Construction-INDEPENDENT on purpose: hand the helper a stub straight
    // down the middle of a tile. If the stubs had never been added to
    // `connectorsOf`, every "crosses no tile" assertion in the suite would be
    // vacuously true FOR STUBS, and nothing else would notice -- an empty
    // contribution crosses nothing.
    const l0 = layout0(graph);
    const tile = l0.tiles[0]!;
    const cx = tile.box.x + tile.box.w / 2;
    const probe = {
      ...l0,
      infraStubs: [
        {
          deviceId: "probe",
          siteKey: tile.siteKey,
          points: [
            { x: cx, y: tile.box.y - 60 },
            { x: cx, y: tile.box.y + tile.box.h + 60 },
          ],
        },
      ],
    };
    expect(noConnectorCrossesATile(probe)).toEqual([
      `infra stub probe crosses tile ${tile.segment.observedCidr}`,
    ]);
  });

  it("...and the shipped drawing has none", () => {
    // Stated beside it so the probe above cannot be misread as "R3 crosses
    // tiles": the real layout is clean.
    expect(noConnectorCrossesATile(layout0(graph))).toEqual([]);
  });
});

describe("R3: a column pinned OVER another drags its stub across that column's tiles", () => {
  it("is a real pin limit that no other test in the suite records", () => {
    // `15` §4: a pin is an absolute override with NO reflow, so nothing routes
    // around a column parked on top of another's tiles. The shipped tests
    // assert the CLEAN pins (`r2.trunk.test.ts` at x=900, `r3.infra.test.ts`
    // moving a column right and down); this is the other side.
    //
    // **DERIVED from the layout, not written as a literal** -- R6 is the second
    // plane change in three slices, and a derived coordinate survives the next
    // one while a literal has to be re-aimed by hand.
    //
    // `site:10.20` holds ONE device, so its stub sits exactly on the moved
    // column's centre. Park that centre on a tile of ANOTHER column and drop
    // the column below that tile's row, and the stub must pass through it.
    // (Swept for context: dragging `site:10.20` over x ∈ [140, 1400] step 20 ×
    // y ∈ {620, 300}, 31 of 132 positions produce a stub violation.)
    const base = layout0(graph);
    const victim = base.tiles.find((t) => t.siteKey === "site:10.80")!;
    const column = base.sites.find((s) => s.site.key === "site:10.20")!.box;
    const lowest = Math.max(...base.tiles.map((t) => t.box.y + t.box.h));
    const moved = layout0(graph, {
      "site:10.20": {
        x: victim.box.x + victim.box.w / 2 - column.w / 2,
        y: lowest + 40,
      },
    });

    const stubs = noConnectorCrossesATile(moved).filter((v) => v.startsWith("infra stub"));
    expect(
      stubs,
      "if this is empty the limit is gone -- delete this test and say so in " +
        "`layout/infra.ts`, which documents only the WRAP limit today",
    ).not.toEqual([]);
    expect(stubs.some((v) => v.includes("10.80.72.0/24"))).toBe(true);
  });
});

// ===========================================================================
// 3. The `0 * Infinity` regression: no NaN anywhere, ever.
// ===========================================================================
/** Every finite-number leaf in an arbitrary structure, with its path. */
function numberLeaves(value: unknown, path: string, out: Array<[string, number]>): void {
  if (typeof value === "number") {
    out.push([path, value]);
  } else if (Array.isArray(value)) {
    value.forEach((v, i) => numberLeaves(v, `${path}[${i}]`, out));
  } else if (value && typeof value === "object") {
    for (const [k, v] of Object.entries(value)) numberLeaves(v, `${path}/${k}`, out);
  }
}

describe("R3: an unplaced device produces no NaN (the `0 * Infinity` bug)", () => {
  it("every number the layout publishes is finite", () => {
    const l0 = layout0(buildGraph(withUnplacedDevice(), trimmedCast));
    const numbers: Array<[string, number]> = [];
    numberLeaves(l0, "", numbers);
    expect(numbers.length).toBeGreaterThan(100); // it really did walk the layout
    const bad = numbers.filter(([, n]) => !Number.isFinite(n));
    expect(bad, `non-finite coordinates: ${JSON.stringify(bad)}`).toEqual([]);
  });

  it("including the orphan's own box, which is what went NaN", () => {
    const l0 = layout0(buildGraph(withUnplacedDevice(), trimmedCast));
    const orphan = l0.infraDevices.find((d) => d.siteKey === null)!;
    expect(orphan).toBeDefined();
    for (const [k, v] of Object.entries(orphan.box)) {
      expect(Number.isFinite(v), `box.${k} = ${v}`).toBe(true);
    }
  });
});

// ===========================================================================
// 4. The real pit bundle -- the acceptance's own example, read off the data.
// ===========================================================================
const PIT = fileURLToPath(
  new URL("../../../twins/pit-395a61f04e-1780405200/", import.meta.url),
);
const PIT_TOPOLOGY = `${PIT}generated/pit-395a61f04e-1780405200.topology.json`;

/** A dangling link is a hard failure, never a silent green skip (the guard
 *  `layout.real.test.ts` adopted; repeated here so this file cannot skip
 *  itself away either). */
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

describe.skipIf(!pitAvailable())("R3 acceptance on the real pit bundle", () => {
  const exportData = JSON.parse(readFileSync(PIT_TOPOLOGY, "utf8")) as TopologyExport;
  const pit = buildGraph(exportData, {
    imprintId: "pit-395a61f04e-1780405200",
    deployMinOccupancy: 0.8,
  });

  it("puts BOTH domain controllers over the column holding 10.80.20.0/24", () => {
    const l0 = layout0(pit);
    const dcs = l0.infraDevices.filter((d) => d.node.role === "domain-controller");
    expect(dcs.length).toBe(2);
    // The column is read off the tile, so this cannot pass by hardcoding.
    const siteKey = l0.tiles.find((t) => t.segment.observedCidr === "10.80.20.0/24")!.siteKey;
    const column = l0.sites.find((s) => s.site.key === siteKey)!.box;
    for (const dc of dcs) {
      expect(dc.siteKey, dc.node.id).toBe(siteKey);
      expect(dc.box.x, dc.node.id).toBeGreaterThanOrEqual(column.x);
      expect(dc.box.x + dc.box.w, dc.node.id).toBeLessThanOrEqual(column.x + column.w);
    }
  });

  it("badges that tile `DC 2` -- the count matching the devices drawn", () => {
    const seg = pit.segmentByCidr.get("10.80.20.0/24")!;
    const dc = seg.roleBadges.find((b) => b.role === "domain-controller");
    expect(dc, "the DC badge lost its slot to an unfingerprinted bucket").toBeDefined();
    expect(dc!.count).toBe(2);
    expect(roleBadgeLabel(dc!.role)).toBe("DC");
    // The badge count and the band's device count are the same two machines.
    const drawn = layout0(pit).infraDevices.filter((d) => d.node.role === "domain-controller");
    expect(dc!.count).toBe(drawn.length);
  });

  it("keeps every device inside its column, and crosses no tile at 39 segments", () => {
    const l0 = layout0(pit);
    const columns = new Map(l0.sites.map((s) => [s.site.key, s.box]));
    for (const d of l0.infraDevices) {
      const col = columns.get(d.siteKey!)!;
      expect(d.box.x, d.node.id).toBeGreaterThanOrEqual(col.x);
      expect(d.box.x + d.box.w, d.node.id).toBeLessThanOrEqual(col.x + col.w);
    }
    expect(noConnectorCrossesATile(l0)).toEqual([]);
  });
});
