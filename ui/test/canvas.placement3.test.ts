/**
 * Round 3 item 3 (`SPEC.md` "Free layout"): hosts added to a selected switch stand in the row under it
 * even when the add had no drop point (3a: they landed at the origin, off screen), and Zoom to fit
 * covers every node the layout holds, measured or not (3b).
 */
import { describe, expect, it } from "vitest";

import { placeDropped } from "../src/features/edit-canvas/add/placeDrop";
import { allNodesBounds } from "../src/features/edit-canvas/canvas/placement";
import { HOST_GAP, TREE_RANK_SEP } from "../src/features/edit-canvas/canvas/tree";
import { BUS_ICON } from "../src/features/edit-canvas/canvas/bus";
import { DEVICE_H, DEVICE_W } from "../src/features/edit-canvas/layout/geometry";
import { readTe } from "../src/features/edit-canvas/edit/te";

const BEFORE = `twin_config:
  id: r
  segments:
    - { name: rack1-lan, cidr: 10.200.0.0/24, zone_type: vlan }
  nodes:
    - { name: rack1-tor, kind: switch, role: l2-switch, net: rack1-lan }
`;
const AFTER = BEFORE + `    - { name: rack1-web-1, image: debian-12-cloud@v1, net: rack1-lan }
    - { name: rack1-web-2, image: debian-12-cloud@v1, net: rack1-lan }
    - { name: rack1-web-3, image: debian-12-cloud@v1, net: rack1-lan }
`;

describe("3a: hosts clicked onto a selected switch", () => {
  it("stand in one row under the switch with no drop point, centred on it", () => {
    const sw = { x: 2400, y: 1600 };
    const pos = placeDropped(readTe(BEFORE), readTe(AFTER), null, {}, { "host:rack1-tor": sw });
    const row = ["rack1-web-1", "rack1-web-2", "rack1-web-3"].map((n) => pos[`host:${n}`]!);
    expect(row.every((p) => p.y === sw.y + DEVICE_H + TREE_RANK_SEP)).toBe(true);
    expect(row.map((p) => p.x - row[0]!.x)).toEqual([0, BUS_ICON + HOST_GAP, 2 * (BUS_ICON + HOST_GAP)]);
    const mid = (row[0]!.x + row[2]!.x + BUS_ICON) / 2;
    expect(Math.abs(mid - (sw.x + DEVICE_W / 2))).toBeLessThanOrEqual(1);
  });

  it("with the switch's spot unknown, they fall back to a row at the point given (the view's centre)", () => {
    const pos = placeDropped(readTe(BEFORE), readTe(AFTER), { x: 500, y: 300 }, {}, {});
    expect(pos["host:rack1-web-1"]).toEqual({ x: 500, y: 300 });
    expect(pos["host:rack1-web-3"]!.y).toBe(300);
  });
});

describe("3b: Zoom to fit covers every node", () => {
  it("bounds every node by its declared size, a child by its parent's offset, hidden ones left out", () => {
    const b = allNodesBounds([
      { id: "a", position: { x: 100, y: 100 }, width: 50, height: 20 },
      // An unmeasured, undeclared node far out (a host just placed) still counts as a point.
      { id: "b", position: { x: -900, y: 40 } },
      { id: "p", position: { x: 1000, y: 1000 }, width: 10, height: 10 },
      { id: "c", position: { x: 5, y: 5 }, parentId: "p", width: 30, height: 30 },
      { id: "h", position: { x: 99999, y: 99999 }, width: 1, height: 1, hidden: true },
    ]);
    expect(b).toEqual({ x: -900, y: 40, width: 1035 + 900, height: 1035 - 40 });
    expect(allNodesBounds([])).toBeNull();
  });
});
