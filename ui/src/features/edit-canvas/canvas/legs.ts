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

/** The corner radius an orthogonal leg turns with (the smoothstep look). */
export const LEG_CORNER = 6;

/**
 * The SVG path of an orthogonal polyline with rounded corners (rendering
 * rule 6): each corner is cut `LEG_CORNER` short on both runs (less on a
 * short run) and bridged with a quadratic curve through the corner point.
 */
export function polylinePath(pts: readonly Point[], radius = LEG_CORNER): string {
  if (pts.length === 0) return "";
  if (pts.length === 1) return `M ${pts[0]!.x} ${pts[0]!.y}`;
  let d = `M ${pts[0]!.x} ${pts[0]!.y}`;
  for (let i = 1; i < pts.length - 1; i++) {
    const a = pts[i - 1]!;
    const c = pts[i]!;
    const b = pts[i + 1]!;
    const inLen = Math.hypot(c.x - a.x, c.y - a.y);
    const outLen = Math.hypot(b.x - c.x, b.y - c.y);
    const r = Math.min(radius, inLen / 2, outLen / 2);
    if (r <= 0 || inLen === 0 || outLen === 0) continue;
    const p1 = { x: c.x - ((c.x - a.x) / inLen) * r, y: c.y - ((c.y - a.y) / inLen) * r };
    const p2 = { x: c.x + ((b.x - c.x) / outLen) * r, y: c.y + ((b.y - c.y) / outLen) * r };
    d += ` L ${p1.x} ${p1.y} Q ${c.x} ${c.y} ${p2.x} ${p2.y}`;
  }
  const last = pts[pts.length - 1]!;
  return `${d} L ${last.x} ${last.y}`;
}

/** The polyline as the `data-pts` attribute the pane's hit-test reads back, and its inverse. */
export function encodePoints(pts: readonly Point[]): string {
  return pts.map((p) => `${p.x},${p.y}`).join(" ");
}

export function decodePoints(s: string | null | undefined): Point[] {
  if (!s) return [];
  const out: Point[] = [];
  for (const pair of s.trim().split(/\s+/)) {
    const [x, y] = pair.split(",").map(Number);
    if (x === undefined || y === undefined || Number.isNaN(x) || Number.isNaN(y)) return [];
    out.push({ x, y });
  }
  return out;
}

/** The point halfway along a polyline by length: where its label sits. */
export function polylineMid(pts: readonly Point[]): Point {
  if (pts.length === 0) return { x: 0, y: 0 };
  if (pts.length === 1) return pts[0]!;
  let total = 0;
  for (let i = 1; i < pts.length; i++) total += Math.hypot(pts[i]!.x - pts[i - 1]!.x, pts[i]!.y - pts[i - 1]!.y);
  let left = total / 2;
  for (let i = 1; i < pts.length; i++) {
    const a = pts[i - 1]!;
    const b = pts[i]!;
    const len = Math.hypot(b.x - a.x, b.y - a.y);
    if (len >= left) {
      const t = len === 0 ? 0 : left / len;
      return { x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t };
    }
    left -= len;
  }
  return pts[pts.length - 1]!;
}

/** The distance from a point to a polyline. */
export function distanceToPolyline(p: Point, pts: readonly Point[]): number {
  if (pts.length === 1) return Math.hypot(p.x - pts[0]!.x, p.y - pts[0]!.y);
  let best = Number.POSITIVE_INFINITY;
  for (let i = 1; i < pts.length; i++) best = Math.min(best, distanceToSegment(p, pts[i - 1]!, pts[i]!));
  return best;
}

/** The id of the polyline nearest `at` within `tolerance` (flow units), else null. */
export function nearestPolyline(at: Point, legs: ReadonlyArray<{ id: string; pts: readonly Point[] }>, tolerance: number): string | null {
  let best: { id: string; d: number } | null = null;
  for (const leg of legs) {
    if (!leg.pts.length) continue;
    const d = distanceToPolyline(at, leg.pts);
    if (d <= tolerance && (!best || d < best.d)) best = { id: leg.id, d };
  }
  return best?.id ?? null;
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
