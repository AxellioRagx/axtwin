/**
 * The east-west pairs the tree draws as evidence (2026-11-07, replacing the
 * selection half of the lifted `layout/arcs.ts`, whose band geometry the tree
 * never drew). Every asserted conduit is drawn; the observed pairs are drawn
 * busiest first up to `PAIR_TOP_K` (all with `showAllPairs`). Pairs are merged
 * on the ANCHORS they are drawn between -- the switch (real or implied) that
 * stands for a segment, or the fold that holds a folded segment -- so a curve's key is the same `arcKey(anchor, anchor)` the
 * selection and the overlay compute, and a pair whose two ends fold into one
 * box is counted, not drawn as a loop. Pure.
 */
import type { ConsoleGraph } from "../model/graph";
import { arcKey } from "./placement";

/** How many observed curves the tree draws by default. */
export const PAIR_TOP_K = 12;
/** The stroke of the thinnest observed curve, the range above it, and a silent conduit's hairline. */
export const MIN_STROKE = 1;
export const STROKE_RANGE = 5;
export const HAIRLINE = 0.5;

export interface DrawnPair {
  /** The two anchors (segment names or fold ids), `a < b`. */
  a: string;
  b: string;
  key: string;
  kind: "observed" | "asserted";
  bytes: number;
  flows: number;
  /** How many segment pairs this curve carries (more than one through a fold). */
  pairs: number;
  hasTraffic: boolean;
  strokeWidth: number;
}

export interface PairSelection {
  drawn: DrawnPair[];
  /** Observed pairs the top-K budget left off (counted in segment pairs). */
  hiddenPairs: number;
  /** Pairs whose two ends fold into the same box. */
  internalPairs: number;
}

/** A log-scaled stroke over the drawn set's own byte range. */
export function strokeForBytes(bytes: number, min: number, max: number): number {
  if (!Number.isFinite(bytes) || bytes <= 0) return HAIRLINE;
  if (max <= 0) return MIN_STROKE;
  const lo = Math.log10(1 + min);
  const hi = Math.log10(1 + max);
  const t = hi <= lo ? 1 : Math.min(1, Math.max(0, (Math.log10(1 + bytes) - lo) / (hi - lo)));
  return Math.round((MIN_STROKE + STROKE_RANGE * t) * 100) / 100;
}

export function selectPairs(graph: ConsoleGraph, anchorOf: (segment: string) => string | null, options: { showAll?: boolean } = {}): PairSelection {
  const norm = (a: string, b: string): [string, string] => (a < b ? [a, b] : [b, a]);
  const volume = new Map<string, { bytes: number; flows: number }>();
  for (const p of graph.trafficPairs) volume.set(arcKey(p.a, p.b), p);
  const agg = new Map<string, DrawnPair>();
  let internalPairs = 0;
  const add = (sa: string, sb: string, kind: DrawnPair["kind"], bytes: number, flows: number): boolean => {
    const ka = anchorOf(sa);
    const kb = anchorOf(sb);
    if (!ka || !kb) return false;
    if (ka === kb) {
      internalPairs += 1;
      return true;
    }
    const [a, b] = norm(ka, kb);
    const key = arcKey(a, b);
    const e = agg.get(key) ?? { a, b, key, kind, bytes: 0, flows: 0, pairs: 0, hasTraffic: false, strokeWidth: HAIRLINE };
    e.bytes += bytes;
    e.flows += flows;
    e.pairs += 1;
    if (kind === "asserted") e.kind = "asserted";
    agg.set(key, e);
    return true;
  };
  const asserted = new Set<string>();
  for (const c of graph.conduits) {
    const k = arcKey(c.a, c.b);
    if (asserted.has(k)) continue;
    const v = volume.get(k);
    if (add(c.a, c.b, "asserted", v?.bytes ?? 0, v?.flows ?? 0)) asserted.add(k);
  }
  for (const p of graph.trafficPairs) if (!asserted.has(arcKey(p.a, p.b))) add(p.a, p.b, "observed", p.bytes, p.flows);
  const all = [...agg.values()];
  const declared = all.filter((e) => e.kind === "asserted");
  const observed = all.filter((e) => e.kind === "observed").sort((x, y) => y.bytes - x.bytes || (x.key < y.key ? -1 : x.key > y.key ? 1 : 0));
  const shown = options.showAll ? observed : observed.slice(0, PAIR_TOP_K);
  const hiddenPairs = observed.slice(shown.length).reduce((n, e) => n + e.pairs, 0);
  const drawn = [...shown, ...declared];
  const positive = drawn.map((e) => e.bytes).filter((b) => Number.isFinite(b) && b > 0);
  const min = positive.length ? Math.min(...positive) : 0;
  const max = positive.length ? Math.max(...positive) : 0;
  for (const e of drawn) {
    e.hasTraffic = e.bytes > 0;
    e.strokeWidth = strokeForBytes(e.bytes, min, max);
  }
  return { drawn, hiddenPairs, internalPairs };
}
