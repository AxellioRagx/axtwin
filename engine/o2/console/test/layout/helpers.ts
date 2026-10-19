/**
 * Shared assertions over a computed layout
 * (`docs/WORK_ORDER_console_level0_rendering.md` §6).
 *
 * **`noConnectorCrossesATile` is §1's property 2**, and it is a *computed*
 * assertion rather than a visual check: "nothing crosses a tile" is exactly
 * the kind of claim that looks fine on the one fixture someone screenshotted
 * and is false on the bundle that ships. It is called by every level-0 golden
 * from R2 on; **R3's infra stubs joined it**, and R4's arcs join it next.
 *
 * NOT a `*.test.ts`, so vitest's `include` glob does not collect it — it is a
 * library the test files import.
 */
import { arcPolyline } from "../../src/layout/arcs";
import type { Box, Point } from "../../src/layout/geometry";
import type { Level0Layout } from "../../src/layout/level0";

/** Floating-point slack. Every coordinate the layout produces is an integer or
 *  a half (a box centre), so this only ever absorbs representation error. */
const EPS = 1e-9;

/**
 * Does the open segment `p1→p2` pass through the INTERIOR of `box`?
 *
 * Liang–Barsky against the CLOSED box, then a strict-interior test on the
 * midpoint of whatever survived. The two-step matters for the cases this
 * helper exists to allow:
 *
 *   - a connector that ENDS on a box's edge (a trunk leg stopping at a group
 *     header's top, a lane leaving a tile's bottom) clips to a single point,
 *     so `t1 - t0` is zero and it is not a crossing;
 *   - a connector that runs ALONG an edge has a positive clipped run but a
 *     midpoint on the boundary, so it is not a crossing either.
 *
 * Anything that genuinely enters the box has both a positive run and an
 * interior midpoint.
 */
function crossesBox(p1: Point, p2: Point, box: Box): boolean {
  const dx = p2.x - p1.x;
  const dy = p2.y - p1.y;
  let t0 = 0;
  let t1 = 1;
  const clips: ReadonlyArray<readonly [number, number]> = [
    [-dx, p1.x - box.x],
    [dx, box.x + box.w - p1.x],
    [-dy, p1.y - box.y],
    [dy, box.y + box.h - p1.y],
  ];
  for (const [p, q] of clips) {
    if (Math.abs(p) < EPS) {
      if (q < 0) return false; // parallel to this edge and outside it
      continue;
    }
    const r = q / p;
    if (p < 0) {
      if (r > t1) return false;
      if (r > t0) t0 = r;
    } else {
      if (r < t0) return false;
      if (r < t1) t1 = r;
    }
  }
  if (t1 - t0 <= EPS) return false; // touches at a point
  const mid = (t0 + t1) / 2;
  const mx = p1.x + dx * mid;
  const my = p1.y + dy * mid;
  return (
    mx > box.x + EPS &&
    mx < box.x + box.w - EPS &&
    my > box.y + EPS &&
    my < box.y + box.h - EPS
  );
}

interface NamedPolyline {
  name: string;
  points: readonly Point[];
}

/** Every connector the LAYOUT publishes, named for the failure message. Since
 *  R3 that includes the infra stubs, which the layout now publishes -- before
 *  R3 the view derived the tie-lines itself, so they could not be checked here
 *  at all, which is precisely how they came to cross tiles (§2 F3). */
function connectorsOf(layout: Level0Layout): NamedPolyline[] {
  const out: NamedPolyline[] = [];
  for (const stub of layout.infraStubs) {
    out.push({ name: `infra stub ${stub.deviceId}`, points: stub.points });
  }
  if (layout.trunk) {
    out.push({ name: "trunk spine", points: layout.trunk.spine });
    if (layout.trunk.stem.length > 0) {
      out.push({ name: "trunk stem", points: layout.trunk.stem });
    }
    if (layout.trunk.bus) out.push({ name: "trunk bus", points: layout.trunk.bus });
    for (const leg of layout.trunk.legs) {
      out.push({ name: `trunk leg ${leg.siteKey}`, points: leg.points });
    }
  }
  // R4's arcs, flattened by the module that OWNS the curve (`arcs.ts`), so this
  // helper re-derives no geometry (LES-025) and the layout still publishes four
  // control points rather than fifty samples. The kind is in the name because
  // an asserted arc and an observed one are different families of defect.
  for (const arc of layout.arcs) {
    out.push({ name: `${arc.kind} arc ${arc.a}↔${arc.b}`, points: arcPolyline(arc) });
  }
  return out;
}

/**
 * Every connector/tile pair where the connector passes through the tile's
 * interior, as readable strings. `[]` is the passing state.
 *
 * Returns the offenders rather than a boolean on purpose: a failing assertion
 * that names "trunk leg site:10.80 crosses tile 10.80.72.0/24" is actionable,
 * where `expect(ok).toBe(true)` sends the reader back to the layout to work
 * out which of forty tiles it meant.
 */
export function noConnectorCrossesATile(layout: Level0Layout): string[] {
  const violations: string[] = [];
  for (const connector of connectorsOf(layout)) {
    for (let i = 1; i < connector.points.length; i++) {
      const a = connector.points[i - 1]!;
      const b = connector.points[i]!;
      for (const tile of layout.tiles) {
        if (crossesBox(a, b, tile.box)) {
          violations.push(
            `${connector.name} crosses tile ${tile.segment.observedCidr}`,
          );
        }
      }
    }
  }
  return [...new Set(violations)].sort();
}

/** The same test against ONE box, exported so a slice can assert a narrower
 *  claim (R2: a trunk leg enters no SITE GROUP box either, not just no tile). */
export function polylineCrossesBox(points: readonly Point[], box: Box): boolean {
  for (let i = 1; i < points.length; i++) {
    if (crossesBox(points[i - 1]!, points[i]!, box)) return true;
  }
  return false;
}
