/**
 * Orthogonal edges for the device tree (brief "Canvas rendering", review R3-4,
 * 2026-11-07): every structural edge leaves its source straight down, runs
 * horizontally only in the GUTTER between two ranks, descends through an
 * intermediate rank only at the column dagre reserved for it (the edge's dummy
 * node, which the layered layout keeps clear of every box in that rank), and
 * enters its target straight from above. A box never lies in a gutter and no two
 * boxes of one rank overlap, so no edge crosses a box's interior; the geometry
 * test (`canvas.orthogonal.test.ts`) checks it over the computed layout.
 *
 * Within one gutter each source gets its own lane (a small y offset), so two
 * parents' horizontals never share a line; the edges of one parent share its
 * lane and read as one branch. Pure -- no React, no DOM.
 */

export interface Point {
  x: number;
  y: number;
}

export interface Box {
  x: number;
  y: number;
  w: number;
  h: number;
}

/** One rank's vertical extent: the top of its tallest box to the bottom of its tallest. */
export interface Band {
  top: number;
  bottom: number;
}

export interface RouteRequest {
  id: string;
  src: Box;
  dst: Box;
  /** The ranks the two ends sit in (a host chip sits in its pill's rank). */
  srcRank: number;
  dstRank: number;
  /** For each rank strictly between the two, the x of the column the edge descends through. */
  via: number[];
  /** Edges with one lane key share a lane in every gutter (one parent's branch). */
  lane: string;
}

/** How far a lane keeps from the bands either side of its gutter (clear of a tap chip on a pill's edge). */
export const GUTTER_MARGIN = 14;
/** The most two lanes of one gutter are apart. */
export const LANE_STEP = 8;

const centreX = (b: Box) => b.x + b.w / 2;

/** Drop the middle of three collinear points and repeated points. */
function simplify(pts: Point[]): Point[] {
  const out: Point[] = [];
  for (const p of pts) {
    const last = out[out.length - 1];
    if (last && Math.abs(last.x - p.x) < 0.01 && Math.abs(last.y - p.y) < 0.01) continue;
    const prev = out[out.length - 2];
    if (prev && last && ((Math.abs(prev.x - last.x) < 0.01 && Math.abs(last.x - p.x) < 0.01) || (Math.abs(prev.y - last.y) < 0.01 && Math.abs(last.y - p.y) < 0.01))) out[out.length - 1] = p;
    else out.push(p);
  }
  return out;
}

/** Route every request; a request the tree cannot route (its target above its source) is absent. */
export function routeOrthogonal(bands: readonly Band[], reqs: readonly RouteRequest[]): Map<string, Point[]> {
  // Which lanes run horizontally in each gutter, so each gets its own y.
  const lanesIn = new Map<number, Map<string, number>>();
  const useLane = (gutter: number, lane: string, x: number) => {
    let m = lanesIn.get(gutter);
    if (!m) lanesIn.set(gutter, (m = new Map()));
    m.set(lane, Math.min(m.get(lane) ?? Infinity, x));
  };
  const usable = reqs.filter((r) => r.dstRank >= r.srcRank && bands[r.srcRank] && bands[r.dstRank]);
  for (const r of usable) {
    const last = r.dstRank === r.srcRank ? r.srcRank : r.dstRank - 1;
    for (let g = r.srcRank; g <= last; g++) useLane(g, r.lane, centreX(r.src));
  }
  const laneY = new Map<string, number>();
  for (const [g, lanes] of lanesIn) {
    const above = bands[g]!;
    const below = bands[g + 1];
    const top = above.bottom + GUTTER_MARGIN;
    const bottom = (below ? below.top : above.bottom + 2 * GUTTER_MARGIN + LANE_STEP * lanes.size) - GUTTER_MARGIN;
    const mid = (top + bottom) / 2;
    const order = [...lanes.entries()].sort((a, b) => a[1] - b[1] || (a[0] < b[0] ? -1 : 1));
    const n = order.length;
    const step = n > 1 ? Math.min(LANE_STEP, Math.max(0, bottom - top) / (n - 1)) : 0;
    order.forEach(([lane], k) => laneY.set(`${g}|${lane}`, mid + (k - (n - 1) / 2) * step));
  }
  const out = new Map<string, Point[]>();
  for (const r of usable) {
    const sx = centreX(r.src);
    const tx = centreX(r.dst);
    const pts: Point[] = [{ x: sx, y: r.src.y + r.src.h }];
    if (r.dstRank === r.srcRank) {
      // A sibling in the same rank: under both, along the gutter below, up into the target's bottom.
      const y = laneY.get(`${r.srcRank}|${r.lane}`)!;
      pts.push({ x: sx, y }, { x: tx, y }, { x: tx, y: r.dst.y + r.dst.h });
      out.set(r.id, simplify(pts));
      continue;
    }
    let x = sx;
    for (let g = r.srcRank; g < r.dstRank; g++) {
      const y = laneY.get(`${g}|${r.lane}`)!;
      const next = g + 1 < r.dstRank ? (r.via[g - r.srcRank] ?? x) : tx;
      pts.push({ x, y });
      if (next !== x) pts.push({ x: next, y });
      x = next;
    }
    pts.push({ x: tx, y: r.dst.y });
    out.set(r.id, simplify(pts));
  }
  return out;
}

/** Whether the axis-aligned segment a→b passes through the interior of `box` (its border shrunk by `inset`). */
export function segmentCrossesBox(a: Point, b: Point, box: Box, inset = 1): boolean {
  const x0 = box.x + inset;
  const x1 = box.x + box.w - inset;
  const y0 = box.y + inset;
  const y1 = box.y + box.h - inset;
  if (x1 <= x0 || y1 <= y0) return false;
  if (Math.abs(a.x - b.x) < 0.01) {
    const x = a.x;
    if (x <= x0 || x >= x1) return false;
    const lo = Math.min(a.y, b.y);
    const hi = Math.max(a.y, b.y);
    return hi > y0 && lo < y1;
  }
  if (Math.abs(a.y - b.y) < 0.01) {
    const y = a.y;
    if (y <= y0 || y >= y1) return false;
    const lo = Math.min(a.x, b.x);
    const hi = Math.max(a.x, b.x);
    return hi > x0 && lo < x1;
  }
  // Not axis-aligned: a straight line from the sampled points (never produced by the router).
  for (let t = 0; t <= 1; t += 0.02) {
    const x = a.x + (b.x - a.x) * t;
    const y = a.y + (b.y - a.y) * t;
    if (x > x0 && x < x1 && y > y0 && y < y1) return true;
  }
  return false;
}

/** The polyline as an SVG path with rounded corners of radius `r`. */
export function roundedPath(pts: readonly Point[], r = 6): string {
  if (pts.length < 2) return "";
  let d = `M ${pts[0]!.x} ${pts[0]!.y}`;
  for (let i = 1; i < pts.length - 1; i++) {
    const p = pts[i - 1]!;
    const c = pts[i]!;
    const n = pts[i + 1]!;
    const d1 = Math.hypot(c.x - p.x, c.y - p.y);
    const d2 = Math.hypot(n.x - c.x, n.y - c.y);
    const k = Math.min(r, d1 / 2, d2 / 2);
    const ax = c.x + ((p.x - c.x) / (d1 || 1)) * k;
    const ay = c.y + ((p.y - c.y) / (d1 || 1)) * k;
    const bx = c.x + ((n.x - c.x) / (d2 || 1)) * k;
    const by = c.y + ((n.y - c.y) / (d2 || 1)) * k;
    d += ` L ${ax} ${ay} Q ${c.x} ${c.y} ${bx} ${by}`;
  }
  const last = pts[pts.length - 1]!;
  return `${d} L ${last.x} ${last.y}`;
}

/** The point halfway along the polyline (for a label). */
export function midOf(pts: readonly Point[]): Point {
  let total = 0;
  for (let i = 1; i < pts.length; i++) total += Math.hypot(pts[i]!.x - pts[i - 1]!.x, pts[i]!.y - pts[i - 1]!.y);
  let left = total / 2;
  for (let i = 1; i < pts.length; i++) {
    const a = pts[i - 1]!;
    const b = pts[i]!;
    const len = Math.hypot(b.x - a.x, b.y - a.y);
    if (left <= len) return { x: a.x + ((b.x - a.x) * left) / (len || 1), y: a.y + ((b.y - a.y) * left) / (len || 1) };
    left -= len;
  }
  return pts[pts.length - 1] ?? { x: 0, y: 0 };
}
