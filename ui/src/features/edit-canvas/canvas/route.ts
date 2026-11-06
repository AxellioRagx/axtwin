/**
 * Orthogonal connector routing in the gutters between boxes (rendering rule
 * 6, 2026-11-06): a leg leaves its node downward, runs along the channels
 * that lie a margin outside every box's edges, and arrives at its handle
 * from above, never through a box's interior.
 *
 * The channels are a sparse grid -- every box's four edges pushed out by the
 * margin, plus the ports' own lines -- and a leg is the cheapest path over
 * it by A* (length plus a bend penalty, so a leg prefers one corner to a
 * staircase). One grid serves every leg of a pass (`routeAll`); a leg with
 * no path (a port walled in by boxes the operator stacked) gets `null` and
 * the caller falls back to a straight line. Pure -- no React, no DOM.
 */
import type { Rect } from "./placement";

export interface Point {
  x: number;
  y: number;
}

export interface RouteOptions {
  /** How far a channel keeps from a box's edge (half a gutter, so two boxes a gutter apart share one channel). */
  margin: number;
  /** The cost of a corner in channel units. */
  bend: number;
}

export const ROUTE_DEFAULTS: RouteOptions = { margin: 8, bend: 40 };

export interface Grid {
  xs: number[];
  ys: number[];
  /** `hBlocked[j * (xs.length - 1) + i]`: the run from `xs[i]` to `xs[i+1]` along `ys[j]` crosses a box. */
  hBlocked: Uint8Array;
  /** `vBlocked[j * xs.length + i]`: the run from `ys[j]` to `ys[j+1]` along `xs[i]` crosses a box. */
  vBlocked: Uint8Array;
  xIndex: Map<number, number>;
  yIndex: Map<number, number>;
}

function sortedUnique(values: number[]): number[] {
  return [...new Set(values.map((v) => Math.round(v * 2) / 2))].sort((a, b) => a - b);
}

/** The first index whose value is strictly greater than `v`. */
function above(sorted: number[], v: number): number {
  let lo = 0;
  let hi = sorted.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (sorted[mid]! <= v) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

/** The channel grid for a set of boxes and the lines the ports need. */
export function buildGrid(boxes: readonly Rect[], portXs: readonly number[], portYs: readonly number[], margin: number): Grid {
  const xs = sortedUnique([...boxes.flatMap((b) => [b.x - margin, b.x + b.w + margin]), ...portXs]);
  const ys = sortedUnique([...boxes.flatMap((b) => [b.y - margin, b.y + b.h + margin]), ...portYs]);
  const cols = xs.length;
  const rows = ys.length;
  const hBlocked = new Uint8Array(Math.max(0, rows * (cols - 1)));
  const vBlocked = new Uint8Array(Math.max(0, (rows - 1) * cols));
  for (const b of boxes) {
    const right = b.x + b.w;
    const bottom = b.y + b.h;
    // Channels strictly inside the box: a run along one of them is blocked
    // where it overlaps the box's open interior.
    const j0 = above(ys, b.y);
    const j1 = above(ys, bottom - 0.001) - 1; // ys[j] < bottom
    const i0 = above(xs, b.x);
    const i1 = above(xs, right - 0.001) - 1; // xs[i] < right
    for (let j = j0; j <= j1; j++) {
      if (ys[j]! >= bottom) break;
      // horizontal runs i..i+1 with xs[i] < right and xs[i+1] > b.x
      for (let i = Math.max(0, i0 - 1); i <= Math.min(cols - 2, i1); i++) {
        if (xs[i + 1]! > b.x && xs[i]! < right) hBlocked[j * (cols - 1) + i] = 1;
      }
    }
    for (let i = i0; i <= i1; i++) {
      if (xs[i]! >= right) break;
      for (let j = Math.max(0, j0 - 1); j <= Math.min(rows - 2, j1); j++) {
        if (ys[j + 1]! > b.y && ys[j]! < bottom) vBlocked[j * cols + i] = 1;
      }
    }
  }
  return { xs, ys, hBlocked, vBlocked, xIndex: new Map(xs.map((v, i) => [v, i])), yIndex: new Map(ys.map((v, i) => [v, i])) };
}

// Directions: 0 right, 1 down, 2 left, 3 up.
const DX = [1, 0, -1, 0];
const DY = [0, 1, 0, -1];

/** A binary min-heap of (cost, state) pairs. */
class Heap {
  private cost: number[] = [];
  private item: number[] = [];
  get size(): number {
    return this.cost.length;
  }
  push(c: number, s: number): void {
    const cost = this.cost;
    const item = this.item;
    cost.push(c);
    item.push(s);
    let i = cost.length - 1;
    while (i > 0) {
      const p = (i - 1) >> 1;
      if (cost[p]! <= cost[i]!) break;
      [cost[p], cost[i]] = [cost[i]!, cost[p]!];
      [item[p], item[i]] = [item[i]!, item[p]!];
      i = p;
    }
  }
  pop(): [number, number] {
    const cost = this.cost;
    const item = this.item;
    const top: [number, number] = [cost[0]!, item[0]!];
    const lc = cost.pop()!;
    const li = item.pop()!;
    if (cost.length) {
      cost[0] = lc;
      item[0] = li;
      let i = 0;
      for (;;) {
        const l = 2 * i + 1;
        const r = l + 1;
        let m = i;
        if (l < cost.length && cost[l]! < cost[m]!) m = l;
        if (r < cost.length && cost[r]! < cost[m]!) m = r;
        if (m === i) break;
        [cost[m], cost[i]] = [cost[i]!, cost[m]!];
        [item[m], item[i]] = [item[i]!, item[m]!];
        i = m;
      }
    }
    return top;
  }
}

/** At most this many states a search visits before giving up (a walled-in port on a huge plane). */
const SEARCH_CAP = 400_000;

/**
 * The cheapest orthogonal path on `grid` from `from` (a grid point, entered
 * moving DOWN) to `to` (a grid point, to be left moving DOWN), or `null`.
 * The result is the corner points only, `from` and `to` included.
 */
export function routeOnGrid(grid: Grid, from: Point, to: Point, bend: number, startDir: 1 | 3 = 1): Point[] | null {
  const { xs, ys, hBlocked, vBlocked } = grid;
  const cols = xs.length;
  const rows = ys.length;
  const i0 = grid.xIndex.get(from.x);
  const j0 = grid.yIndex.get(from.y);
  const i1 = grid.xIndex.get(to.x);
  const j1 = grid.yIndex.get(to.y);
  if (i0 === undefined || j0 === undefined || i1 === undefined || j1 === undefined) return null;
  const n = cols * rows * 4;
  const g = new Float64Array(n).fill(Number.POSITIVE_INFINITY);
  const parent = new Int32Array(n).fill(-1);
  const closed = new Uint8Array(n);
  const key = (i: number, j: number, d: number) => (j * cols + i) * 4 + d;
  const h = (i: number, j: number) => Math.abs(xs[i]! - xs[i1]!) + Math.abs(ys[j]! - ys[j1]!);
  const heap = new Heap();
  const s0 = key(i0, j0, startDir);
  g[s0] = 0;
  heap.push(h(i0, j0), s0);
  let visited = 0;
  let goal = -1;
  while (heap.size) {
    const [, s] = heap.pop();
    if (closed[s]) continue;
    closed[s] = 1;
    if (++visited > SEARCH_CAP) return null;
    const d = s & 3;
    const cell = s >> 2;
    const i = cell % cols;
    const j = (cell - i) / cols;
    if (i === i1 && j === j1) {
      // Leaving `to` downward: a last corner when the arrival was not down.
      goal = s;
      break;
    }
    for (let nd = 0; nd < 4; nd++) {
      if (nd === (d + 2) % 4) continue; // no U-turn
      const ni = i + DX[nd]!;
      const nj = j + DY[nd]!;
      if (ni < 0 || ni >= cols || nj < 0 || nj >= rows) continue;
      const blocked = nd === 0 ? hBlocked[j * (cols - 1) + i] : nd === 2 ? hBlocked[j * (cols - 1) + ni] : nd === 1 ? vBlocked[j * cols + i] : vBlocked[nj * cols + i];
      if (blocked) continue;
      const step = nd === 0 || nd === 2 ? Math.abs(xs[ni]! - xs[i]!) : Math.abs(ys[nj]! - ys[j]!);
      const ns = key(ni, nj, nd);
      const cost = g[s]! + step + (nd === d ? 0 : bend) + (ni === i1 && nj === j1 && nd !== 1 ? bend : 0);
      if (cost < g[ns]!) {
        g[ns] = cost;
        parent[ns] = s;
        heap.push(cost + h(ni, nj), ns);
      }
    }
  }
  if (goal < 0) return null;
  const pts: Point[] = [];
  for (let s = goal; s >= 0; s = parent[s]!) {
    const cell = s >> 2;
    const i = cell % cols;
    const j = (cell - i) / cols;
    pts.push({ x: xs[i]!, y: ys[j]! });
  }
  pts.reverse();
  return simplify(pts);
}

/** Drop the points that lie on a straight run between their neighbours. */
export function simplify(pts: readonly Point[]): Point[] {
  const out: Point[] = [];
  for (const p of pts) {
    const a = out[out.length - 2];
    const b = out[out.length - 1];
    if (a && b && ((a.x === b.x && b.x === p.x) || (a.y === b.y && b.y === p.y))) out[out.length - 1] = p;
    else if (!b || b.x !== p.x || b.y !== p.y) out.push(p);
  }
  return out;
}

export interface LegRequest {
  id: string;
  /** Where the leg leaves its node: on the node's bottom edge, moving down (or its top edge, moving up). */
  from: Point;
  /** Where the leg lands: on the target's top edge, arriving from above. */
  to: Point;
  leave?: "down" | "up";
  /** Points drawn before `from` as they are (a chip's stub inside its own tile). */
  prefix?: readonly Point[];
}

/**
 * Every leg of a pass over one grid: each leg steps a margin out of its
 * port before the search and a margin in after it, so it leaves its node
 * the way it was asked and arrives from above whatever the search did in
 * between. Legs with no path are absent from the map.
 */
export function routeAll(legs: readonly LegRequest[], boxes: readonly Rect[], options: Partial<RouteOptions> = {}): Map<string, Point[]> {
  const { margin, bend } = { ...ROUTE_DEFAULTS, ...options };
  const r = (v: number) => Math.round(v * 2) / 2;
  const starts = legs.map((l) => ({ x: r(l.from.x), y: r(l.leave === "up" ? l.from.y - margin : l.from.y + margin) }));
  const ends = legs.map((l) => ({ x: r(l.to.x), y: r(l.to.y - margin) }));
  const grid = buildGrid(boxes, [...starts.map((p) => p.x), ...ends.map((p) => p.x)], [...starts.map((p) => p.y), ...ends.map((p) => p.y)], margin);
  const out = new Map<string, Point[]>();
  legs.forEach((leg, k) => {
    const inner = routeOnGrid(grid, starts[k]!, ends[k]!, bend, leg.leave === "up" ? 3 : 1);
    if (!inner) return;
    out.set(leg.id, simplify([...(leg.prefix ?? []), leg.from, ...inner, leg.to]));
  });
  return out;
}

/** True when the open segment `a`–`b` (axis-aligned) passes through `r`'s interior. */
export function crossesInterior(a: Point, b: Point, r: Rect): boolean {
  const x0 = Math.min(a.x, b.x);
  const x1 = Math.max(a.x, b.x);
  const y0 = Math.min(a.y, b.y);
  const y1 = Math.max(a.y, b.y);
  if (a.y === b.y) return a.y > r.y && a.y < r.y + r.h && x1 > r.x && x0 < r.x + r.w;
  if (a.x === b.x) return a.x > r.x && a.x < r.x + r.w && y1 > r.y && y0 < r.y + r.h;
  // A slanted fallback line: test its bounding box, which is what the eye sees crossed.
  return x1 > r.x && x0 < r.x + r.w && y1 > r.y && y0 < r.y + r.h;
}

/** The boxes a polyline's runs pass through, by index -- the acceptance check. */
export function interiorsCrossed(pts: readonly Point[], boxes: readonly Rect[]): number[] {
  const hit = new Set<number>();
  for (let k = 1; k < pts.length; k++) {
    boxes.forEach((r, idx) => {
      if (crossesInterior(pts[k - 1]!, pts[k]!, r)) hit.add(idx);
    });
  }
  return [...hit].sort((a, b) => a - b);
}
