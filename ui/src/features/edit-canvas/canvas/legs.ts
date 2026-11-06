/**
 * A leg's line and the pane's hit-test on it. A leg runs from the centre of
 * the node it leaves to the centre of the segment it lands on, clipped at
 * each box's border, so it always points at its target whatever side the
 * segment was dragged to (2026-11-06 round 2: handle-to-handle, a segment
 * moved above its router drew a flat line back through the router). The
 * legs are drawn above the boxes but take no pointer events (`canvas.css`),
 * so a drag or a click on a box under a leg reaches the box; the pane
 * finds the leg under the pointer with `nearestLeg`. Pure -- no React, no DOM.
 */
import type { Rect } from "./placement";

export interface Point {
  x: number;
  y: number;
}

export interface Line {
  from: Point;
  to: Point;
  mid: Point;
}

/** Where the line from `a`'s centre to `b`'s centre leaves `a`'s box. */
function exit(box: Rect, dx: number, dy: number): number {
  const tx = dx === 0 ? Infinity : box.w / 2 / Math.abs(dx);
  const ty = dy === 0 ? Infinity : box.h / 2 / Math.abs(dy);
  return Math.min(tx, ty);
}

/** The centre-to-centre line between two boxes, clipped at both borders.
 *  Boxes that overlap (a segment dropped onto its router) get the full
 *  centre-to-centre line, drawn over them, so the attachment stays visible;
 *  `null` only when the centres coincide. */
export function clipLine(a: Rect, b: Rect): Line | null {
  const ca = { x: a.x + a.w / 2, y: a.y + a.h / 2 };
  const cb = { x: b.x + b.w / 2, y: b.y + b.h / 2 };
  const dx = cb.x - ca.x;
  const dy = cb.y - ca.y;
  if (dx === 0 && dy === 0) return null;
  const ta = exit(a, dx, dy);
  const tb = exit(b, dx, dy);
  const clipped = ta + tb < 1;
  const from = clipped ? { x: ca.x + dx * ta, y: ca.y + dy * ta } : ca;
  const to = clipped ? { x: cb.x - dx * tb, y: cb.y - dy * tb } : cb;
  return { from, to, mid: { x: (from.x + to.x) / 2, y: (from.y + to.y) / 2 } };
}

/** The SVG path of a leg, and its inverse for the hit-test. */
export function legPath(line: Pick<Line, "from" | "to">): string {
  return `M ${line.from.x} ${line.from.y} L ${line.to.x} ${line.to.y}`;
}

export function parseLegPath(d: string): Pick<Line, "from" | "to"> | null {
  const m = /^M\s*(-?[\d.]+)[ ,]+(-?[\d.]+)\s*L\s*(-?[\d.]+)[ ,]+(-?[\d.]+)$/.exec(d.trim());
  if (!m) return null;
  const [x1, y1, x2, y2] = m.slice(1, 5).map(Number) as [number, number, number, number];
  return { from: { x: x1, y: y1 }, to: { x: x2, y: y2 } };
}

/** The distance from a point to a line segment. */
export function distanceToSegment(p: Point, from: Point, to: Point): number {
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  const len2 = dx * dx + dy * dy;
  const t = len2 === 0 ? 0 : Math.max(0, Math.min(1, ((p.x - from.x) * dx + (p.y - from.y) * dy) / len2));
  const qx = from.x + t * dx;
  const qy = from.y + t * dy;
  return Math.hypot(p.x - qx, p.y - qy);
}

/** The id of the leg nearest `at` within `tolerance` (flow units), else null. */
export function nearestLeg(at: Point, legs: ReadonlyArray<{ id: string } & Pick<Line, "from" | "to">>, tolerance: number): string | null {
  let best: { id: string; d: number } | null = null;
  for (const leg of legs) {
    const d = distanceToSegment(at, leg.from, leg.to);
    if (d <= tolerance && (!best || d < best.d)) best = { id: leg.id, d };
  }
  return best?.id ?? null;
}
