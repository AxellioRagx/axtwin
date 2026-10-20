/**
 * The EAST-WEST BAND (R4, `docs/WORK_ORDER_console_level0_rendering.md` §3 R4
 * and §4.1's **arcs ruling**) — segment-pair links drawn as volume-weighted
 * arcs beneath the segment tiles, replacing `lanes.ts`'s orthogonal channels.
 *
 *        [10.20 group]        [10.80 group]
 *        ──────┬──────        ──────┬──────   ← each arc leaves the GROUP's
 *              │                    │            bottom edge, at its tile's x
 *  · · · · · · │ · · · · · · · · · ·│· · · ·   ← the band top: a short drop
 *               ╲__________________╱              gets every arc INTO the band
 *                                                 before it curves (see below)
 *
 * **Why arcs, and why from the group's edge** (§4.1, Authority: user). A lane
 * from a *tile's* bottom-centre drops through every tile row beneath it — on
 * this content, six of six lanes crossed a tile — and one 11 px channel per
 * pair makes the band 440 px tall at 40 pairs while drawing a 2.2 GB link and a
 * 5 MB link at the same stroke. Anchoring at the group's bottom edge makes
 * "nothing crosses a tile" (§1 property 2) structural rather than lucky: the
 * arc starts *outside* every box by construction. The x is still the **tile's**
 * centre, so an arc points at the segment it belongs to rather than at the
 * middle of a column.
 *
 * Pure geometry, like `trunk.ts` and `infra.ts`: pairs and already-placed boxes
 * in, curves out. It knows nothing about the graph, protocols or React — which
 * is what lets `applyPins` re-run it over MOVED tiles and group boxes and get
 * arcs that track a dragged column for free (LES-266). Protocol never reaches
 * this module at all: `15` §5 keeps colour for evidence, so the mix belongs on
 * hover and in the inspector, not in the geometry.
 *
 * **Two families, one band.**
 *
 *  - **observed** — aggregated from the export's own edges
 *    (`model/graph.ts`'s `trafficPairs`), drawn **top-K by bytes** (`ARC_TOP_K`,
 *    `?ew=all` overrides). The remainder is counted in `ArcBand.hiddenPairs` and
 *    named in the UI; the matrix keeps the complete set (§8: top-K hides pairs
 *    from the MAP only).
 *  - **asserted** — the cast's own `conduits:`, every one of them, in the edit
 *    colour and **dashed when the pair carried no observed traffic**, so an
 *    asserted-but-silent conduit is visibly different from an observed-but-
 *    unasserted flow. That is the one property the retired lane geometry had
 *    that an aggregate-by-volume drawing loses — one lane, one conduit — and it
 *    is preserved for the set where it means something.
 *
 * A pair that is **both** is drawn ONCE, as asserted, carrying its observed
 * volume: two identical curves on the same two anchors are one curve plus a
 * misleading count (an implementation call; the alternative is a stroke nobody
 * can tell apart from an artefact).
 *
 * **One curve per ANCHOR pair, not per segment pair (R6).** An anchor may now
 * stand for more than one segment: R6's per-group budget draws a collapsed
 * group's `+N more` tile, and every segment folded behind it anchors there
 * (`ArcAnchor.key`). Without that, a budget that collapsed two thirds of the
 * tiles would silently delete two thirds of the band — measured on the shipped
 * casts: **24 of 40 pairs survive on the pit bundle and 16 of 31 on
 * `corpapcn-twin`, which loses 95 % of its east-west BYTES**, including the
 * widest arc on the map. So pairs whose ends resolve to the same two anchors
 * are **merged into one curve** carrying their summed volume and a `pairs`
 * count, by exactly the argument above: two identical curves are one curve plus
 * a misleading count. A pair whose two ends fold to the SAME anchor (both
 * collapsed in one group) is not drawable at all — an arc from a box to itself
 * is not a picture — and is counted in `internalPairs` rather than dropped in
 * silence.
 *
 * **The depth anchor is `max(bandTop, both endpoints)`, not `bandTop` alone**
 * (an implementation call, open to veto; the slice text says `bandTop +
 * min(D_MAX, D_MIN + k·|Δx|)`). On the pure layout the two are **identical** —
 * a column's box bottom is always above the band top, because the band starts a
 * full `TIER_GAP_Y` below the LOWEST tile while a column ends `SITE_PAD` below
 * its own — and `layoutArcs` is golden-pinned to prove it. They differ only
 * when a **pin** drags a group below the band, and there the literal rule puts
 * the control points ABOVE both endpoints, which bulges the curve back up
 * through the tiles of the very column that was dragged: the F4 defect,
 * re-entering through the geometry that retired it. Taking the anchor from the
 * boxes keeps every arc below both of its ends, whatever a pin does — the same
 * "approach a box from the side it is actually on" rule `geometry.nearestEdgeY`
 * gives R2's legs and R3's stubs.
 *
 * **Each arc DROPS to the band top before it curves, and that is not
 * decoration** — it is what makes §1 property 2 actually hold, and it was found
 * by measuring rather than by reasoning. A cubic does not reach its control
 * points: with both controls at one depth, the curve's lowest point is
 * `0.125·(y₀+y₃) + 0.75·y_control`, i.e. it sags only **three quarters** of the
 * way down. Site columns have *different heights* (a 16-segment column is six
 * tile rows, a one-segment column is one), so an arc between two SHORT columns
 * starts high, sags to three quarters of a depth measured from the band, and
 * passes straight through the lowest tiles of any TALL column between them.
 * Measured on the pit bundle before the fix: **1 crossing among the top 12 and
 * 6 under `?ew=all`** (`10.20.193.0/24 ↔ 192.168.0.0/24` through
 * `10.80.79.0/24`). Dropping to `bandTop` first puts the whole curve at or
 * below the band, where no tile is, by construction rather than by luck.
 *
 * The drop is safe for the same reason the arc is: it descends at its own
 * tile's x, inside its own column's x-span, from that column's bottom edge --
 * and columns do not overlap in x, so nothing else is under it. **It never
 * ascends**: a group a pin has dragged BELOW the band starts its curve at its
 * own edge instead (`Math.max(bandTop, …)`, the same "approach a box from the
 * side it is actually on" rule as `geometry.nearestEdgeY`).
 *
 * **The band's height is bounded by `ARC_DEPTH_MAX + ARC_BAND_PAD` regardless
 * of how many pairs are drawn** (§3 R4 acceptance), because the arcs share the
 * band rather than each taking a channel of their own -- and because the drop
 * is what lets the depth be measured from the band top for every arc alike.
 * Forty pairs and four cost the same height.
 *
 * **An ARC's width moves with R6's per-group budget; a trunk LEG's does not,
 * and the contrast is deliberate.** A leg answers "how much did this GROUP send
 * across the twin's edge", which is a property of the group and not of the
 * drawing: `layout/sites.ts` sums every member, collapsed ones included, so
 * expanding a group leaves every leg byte-identical. An arc answers "how much
 * does THIS CURVE carry", and a curve into a collapsed group carries the merged
 * volume of every pair folded onto that anchor -- so expanding changes both the
 * bytes on a curve and, through `layout/scale.ts`'s normalisation over the DRAWN
 * set (§11.3, LES-297), the width of every other arc beside it. Measured on the
 * pit bundle: expanding every group takes `foldedPairs` 8 → 0 and `hiddenPairs`
 * 20 → 28 while the legs hold still. Neither is a bug, and an operator who
 * toggles `?expand=` sees exactly this: the band re-weights, the trunk does not.
 */
import {
  ARC_BAND_PAD,
  ARC_DEPTH_MAX,
  ARC_DEPTH_MIN,
  ARC_DEPTH_PER_DX,
  round2,
  type Box,
  type Point,
} from "./geometry";
import { strokeForBytes, volumeRange } from "./scale";

/** How many OBSERVED pairs the map draws by default (D2's v1 default: 12).
 *  `?ew=all` lifts it; the matrix always has every pair. */
export const ARC_TOP_K = 12;

/**
 * How many straight segments `arcPolyline` flattens a curve into.
 *
 * The **crossing invariant** (`test/layout/helpers.ts`) is defined on
 * polylines, so an arc has to become one to be checked at all — and flattening
 * here, in the module that owns the curve, is what keeps the test from
 * re-deriving the Bézier (LES-025) while still letting the layout publish the
 * four control points rather than 49 sampled ones (LES-304 is satisfied by
 * publishing the geometry, not by publishing it twice). 48 segments over a span
 * that is at most a canvas wide puts each chord well inside a 96-unit tile's
 * height, so a crossing cannot slip between two samples.
 */
export const ARC_SAMPLES = 48;

export type ArcKind = "observed" | "asserted";

/** One drawn link: a cubic Bézier from `from` to `to` through `c1`/`c2`. */
export interface Arc {
  /**
   * The two ANCHOR keys, `a < b`. For a drawn tile that is the segment NAME —
   * the same normalized pair key `model/graph.ts` produces, so an arc, a matrix
   * cell and an inspector entry all agree on what a pair is. For a segment R6's
   * budget collapsed it is its group's overflow key, and then this arc stands
   * for `pairs` segment pairs rather than one (the view says so; the matrix
   * keeps every pair).
   */
  a: string;
  b: string;
  kind: ArcKind;
  /** `(tileA.centreX, groupA.bottom)` — the tile's x, the GROUP's edge. */
  from: Point;
  to: Point;
  /** Where the curve actually starts: straight below `from`, at the band top
   *  (or at `from` itself when a pin has dragged that group below the band).
   *  The short vertical between the two is the drop the module note explains. */
  dropFrom: Point;
  dropTo: Point;
  /** Both control points sit at one depth below the band top, so the curve is
   *  symmetric and cannot rise above the band whatever the two groups' heights
   *  are. */
  c1: Point;
  c2: Point;
  /** Observed volume for the pair in this window set (never a rate, §8). An
   *  asserted conduit with no observed traffic carries `0`. */
  bytes: number;
  flows: number;
  /**
   * Whether the export saw traffic on this pair. False only for an asserted
   * conduit the capture never observed — which is drawn dashed, and is the
   * element §4.1 singles out as worth telling apart from an observed flow.
   *
   * Named `hasTraffic` rather than `observed` deliberately: `kind` already has
   * an `"observed"` member, and the pair `{kind: "observed", observed: true}`
   * reads as a tautology while `{kind: "asserted", observed: true}` reads as a
   * contradiction — when in fact both are ordinary (review nit).
   */
  hasTraffic: boolean;
  /** From `layout/scale.ts`, over the DRAWN set's own range (§11.3): the
   *  thinnest non-zero arc is `MIN_STROKE` and the busiest is the full width,
   *  which is also why the same pair is drawn differently under `?ew=all`
   *  (§5 D2). `HAIRLINE` when `bytes` is 0. */
  strokeWidth: number;
  /** How many SEGMENT pairs this curve carries — `1` unless one of its ends is
   *  a collapsed group's overflow anchor (R6). Published so the count beside
   *  the band counts links rather than curves (LES-312). */
  pairs: number;
}

export interface ArcBand {
  arcs: Arc[];
  bandTop: number;
  /** The layout's bottom contribution: the deepest arc plus padding, and equal
   *  to `bandTop` when nothing is drawn (an empty band takes no height). */
  bandBottom: number;
  /** Observed pairs that COULD have been drawn and were not, because of the
   *  top-K budget. `0` under `?ew=all`. A pair whose endpoint has no tile is
   *  not counted here — it was never drawable, which is a different fact. */
  hiddenPairs: number;
  /** Drawn pairs that reach a collapsed group's overflow anchor at one end or
   *  both (R6). They ARE on the map; they just do not point at a tile, so the
   *  band's sentence can say how much of it is drawn through a `+N more`. */
  foldedPairs: number;
  /** Pairs whose two ends fold to the SAME anchor — both segments collapsed in
   *  one group. Not drawable (a box to itself is not a curve), so they are
   *  counted and named rather than silently missing. */
  internalPairs: number;
}

/**
 * One segment's anchor boxes: the tile gives the x, the group gives the y.
 *
 * **`key` is the anchor's IDENTITY**, and it defaults to the name the anchor is
 * filed under — which is the whole of the rule for a drawn tile. R6's collapsed
 * segments share their group's `+N more` box, so they share one key, and
 * `layoutArcs` merges the pairs that land on it instead of stacking identical
 * curves. Two anchors sharing a key must carry the same boxes (the caller builds
 * one anchor object and files it under every folded name); the last wins.
 */
export interface ArcAnchor {
  tile: Box;
  group: Box;
  key?: string;
}

/** An observed segment pair, already aggregated and sorted by bytes desc. */
export interface ArcPair {
  a: string;
  b: string;
  bytes: number;
  flows: number;
}

export interface ArcInput {
  /** The cast's resolved conduits, in the graph's order. Every one is drawn. */
  asserted: ReadonlyArray<{ a: string; b: string }>;
  /** Observed pairs, bytes desc (the order `trafficPairs` publishes). */
  observed: readonly ArcPair[];
  /** Segment name → its tile and its group box. A pair with an end missing
   *  here is skipped, exactly as a lane with no tile box was. */
  anchors: ReadonlyMap<string, ArcAnchor>;
  bandTop: number;
  /**
   * How many OBSERVED pairs to draw; `null` means every one (`?ew=all`).
   *
   * `null` rather than `Infinity` deliberately: this number is consumed by
   * arithmetic downstream, and `0 * Infinity` is `NaN` — the bug that gave
   * every unplaced device a `NaN` x one slice ago (LES-302).
   */
  limit: number | null;
}

function pairKey(a: string, b: string): string {
  return `${a} ${b}`;
}

function normalize(a: string, b: string): [string, string] {
  return a < b ? [a, b] : [b, a];
}

/** `(tile.centreX, group.bottom)` — the anchor rule, in one place. */
function anchorPoint(anchor: ArcAnchor): Point {
  return {
    x: anchor.tile.x + anchor.tile.w / 2,
    y: anchor.group.y + anchor.group.h,
  };
}

/**
 * Lay out the band.
 *
 * Deterministic: the arcs come out observed-first (bytes desc, the input's own
 * order) then asserted (the cast's order), which is also the drawing order —
 * an authored conduit paints over the traffic it describes rather than under
 * it.
 */
/** One drawn curve under construction: an ANCHOR pair and everything the
 *  segment pairs landing on it contribute. */
interface ArcAggregate {
  a: string;
  b: string;
  kind: ArcKind;
  bytes: number;
  flows: number;
  /** Segment pairs merged here (R6); `1` for the ordinary one-tile-to-one-tile
   *  case, which is every arc on a map with nothing collapsed. */
  pairs: number;
  /** How many of those reached this curve through a collapsed group's anchor. */
  folded: number;
}

export function layoutArcs(input: ArcInput): ArcBand {
  const { anchors, bandTop } = input;
  const volumeByPair = new Map<string, ArcPair>();
  for (const pair of input.observed) {
    const [a, b] = normalize(pair.a, pair.b);
    volumeByPair.set(pairKey(a, b), pair);
  }

  // A segment name resolves to the ANCHOR KEY it is drawn at: its own name when
  // it has a tile, its group's overflow key when R6's budget collapsed it.
  const anchorByKey = new Map<string, ArcAnchor>();
  for (const [name, anchor] of anchors) anchorByKey.set(anchor.key ?? name, anchor);
  const keyOf = (name: string): string | null => {
    const anchor = anchors.get(name);
    return anchor ? (anchor.key ?? name) : null;
  };

  const agg = new Map<string, ArcAggregate>();
  let internalPairs = 0;
  const upsert = (
    ka: string,
    kb: string,
    kind: ArcKind,
    bytes: number,
    flows: number,
    folded: boolean,
  ): void => {
    const [a, b] = normalize(ka, kb);
    const key = pairKey(a, b);
    const entry = agg.get(key) ?? { a, b, kind, bytes: 0, flows: 0, pairs: 0, folded: 0 };
    entry.bytes += bytes;
    entry.flows += flows;
    entry.pairs += 1;
    if (folded) entry.folded += 1;
    // Asserted wins: a curve one conduit declares stays declared even when an
    // undeclared observed pair folds onto the same two anchors.
    if (kind === "asserted") entry.kind = "asserted";
    agg.set(key, entry);
  };

  // 1. The asserted set: every resolvable conduit, with whatever volume the
  //    export observed on it (0 ⇒ declared but silent ⇒ dashed).
  const assertedNamePairs = new Set<string>();
  for (const conduit of input.asserted) {
    const [a, b] = normalize(conduit.a, conduit.b);
    const namePair = pairKey(a, b);
    // `graph.conduits` is already normalized and deduped, so on the live path
    // this never fires. It stays because `layoutArcs` is a pure module with
    // other callers (tests hand it raw pairs): without it a duplicated input
    // would draw two identical curves on one pair, which is the same
    // "one curve plus a misleading count" the asserted/observed overlap rule
    // exists to avoid.
    if (assertedNamePairs.has(namePair)) continue;
    const ka = keyOf(a);
    const kb = keyOf(b);
    if (ka === null || kb === null) continue;
    // Marked consumed even when it turns out to be internal, so the observed
    // pass does not draw the pair a conduit already accounted for.
    assertedNamePairs.add(namePair);
    if (ka === kb) {
      internalPairs += 1;
      continue;
    }
    const volume = volumeByPair.get(namePair);
    upsert(ka, kb, "asserted", volume?.bytes ?? 0, volume?.flows ?? 0, ka !== a || kb !== b);
  }

  // 2. The observed set: drawable pairs the asserted set does not already
  //    carry. `hiddenPairs` counts what the BUDGET dropped, never what had
  //    nowhere to land.
  for (const pair of input.observed) {
    const [a, b] = normalize(pair.a, pair.b);
    if (assertedNamePairs.has(pairKey(a, b))) continue;
    const ka = keyOf(a);
    const kb = keyOf(b);
    if (ka === null || kb === null) continue;
    if (ka === kb) {
      internalPairs += 1;
      continue;
    }
    upsert(ka, kb, "observed", pair.bytes, pair.flows, ka !== a || kb !== b);
  }

  // 3. The budget applies to the OBSERVED curves only (§4.1: every declared
  //    conduit is drawn), re-sorted after the merge because an aggregate's
  //    volume is its members' sum — the input order was per pair.
  const asserted = [...agg.values()].filter((entry) => entry.kind === "asserted");
  const observed = [...agg.values()]
    .filter((entry) => entry.kind === "observed")
    .sort((x, y) => (y.bytes !== x.bytes ? y.bytes - x.bytes : cmp(x.a, y.a) || cmp(x.b, y.b)));
  const budget = input.limit ?? observed.length;
  const shown = observed.slice(0, Math.max(0, budget));
  // Counted in PAIRS, not in curves: "28 smaller links hidden" has to mean
  // links, or the sentence beside the band counts something the operator
  // cannot (LES-312).
  const hiddenPairs = observed
    .slice(shown.length)
    .reduce((sum, entry) => sum + entry.pairs, 0);

  // 4. One width scale over the DRAWN set (§11.3 / LES-297) — asserted arcs
  //    included, because they are on the drawing and a scale that ignored them
  //    would draw the busiest conduit at some other arc's width.
  const drawn = [...shown, ...asserted];
  const range = volumeRange(drawn.map((entry) => entry.bytes));

  const arcs: Arc[] = [];
  for (const entry of drawn) {
    arcs.push(arcFor(entry, anchorByKey, bandTop, range));
  }
  const foldedPairs = drawn.reduce((sum, entry) => sum + entry.folded, 0);

  const deepest = arcs.reduce((max, arc) => Math.max(max, arc.c1.y), bandTop);
  return {
    arcs,
    bandTop,
    bandBottom: arcs.length === 0 ? bandTop : round2(deepest + ARC_BAND_PAD),
    hiddenPairs,
    foldedPairs,
    internalPairs,
  };
}

function cmp(a: string, b: string): number {
  return a < b ? -1 : a > b ? 1 : 0;
}

function arcFor(
  entry: ArcAggregate,
  anchorByKey: ReadonlyMap<string, ArcAnchor>,
  bandTop: number,
  range: ReturnType<typeof volumeRange>,
): Arc {
  const { a, b, kind, bytes, flows } = entry;
  const from = anchorPoint(anchorByKey.get(a)!);
  const to = anchorPoint(anchorByKey.get(b)!);
  // Deeper for a longer span, so two arcs between different pairs of columns
  // do not lie on top of each other -- and clamped, so the band cannot grow
  // with the plane's width.
  const depth = Math.min(
    ARC_DEPTH_MAX,
    ARC_DEPTH_MIN + ARC_DEPTH_PER_DX * Math.abs(to.x - from.x),
  );
  // The curve starts INSIDE the band (never above it, never ascending to reach
  // it), so a tall column between the two ends cannot be crossed -- the whole
  // of the module note's measured fix.
  const dropFrom = { x: from.x, y: Math.max(bandTop, from.y) };
  const dropTo = { x: to.x, y: Math.max(bandTop, to.y) };
  // BELOW both ends, always: equal to `bandTop + depth` on the pure layout,
  // and the thing that stops a pinned column's own arc bulging back up through
  // its tiles.
  const depthY = round2(Math.max(bandTop, from.y, to.y) + depth);
  return {
    a,
    b,
    kind,
    from,
    to,
    dropFrom,
    dropTo,
    c1: { x: from.x, y: depthY },
    c2: { x: to.x, y: depthY },
    bytes,
    flows,
    hasTraffic: bytes > 0,
    strokeWidth: strokeForBytes(bytes, range),
    pairs: entry.pairs,
  };
}

/** The SVG path for one arc: drop into the band, curve across, rise back to
 *  the far group's edge. A string of the layout's own numbers, so the view does
 *  no geometry (LES-268: the view decides appearance, the layout decides
 *  position — and a curve's control points are position). */
export function arcPath(arc: Arc): string {
  return (
    `M ${arc.from.x},${arc.from.y} ` +
    `L ${arc.dropFrom.x},${arc.dropFrom.y} ` +
    `C ${arc.c1.x},${arc.c1.y} ${arc.c2.x},${arc.c2.y} ${arc.dropTo.x},${arc.dropTo.y} ` +
    `L ${arc.to.x},${arc.to.y}`
  );
}

/**
 * The arc as a polyline, for anything that reasons about the curve as segments
 * — today, the `noConnectorCrossesATile` invariant (§1 property 2).
 *
 * Total and exact at the ends: `t = 0` and `t = 1` reduce to `from` and `to`
 * with no floating-point drift, so an arc that *touches* a box's edge at its
 * anchor is not reported as entering it.
 */
export function arcPolyline(arc: Arc, samples: number = ARC_SAMPLES): Point[] {
  const n = Math.max(2, Math.floor(samples));
  // The two DROPS are part of the drawn path and are therefore part of what the
  // crossing invariant must see -- leaving them out would check three quarters
  // of the geometry and report on all of it.
  const out: Point[] = [arc.from];
  for (let i = 0; i <= n; i++) out.push(pointAt(arc, i / n));
  out.push(arc.to);
  return out;
}

function pointAt(arc: Arc, t: number): Point {
  const u = 1 - t;
  const w0 = u * u * u;
  const w1 = 3 * u * u * t;
  const w2 = 3 * u * t * t;
  const w3 = t * t * t;
  return {
    x: w0 * arc.dropFrom.x + w1 * arc.c1.x + w2 * arc.c2.x + w3 * arc.dropTo.x,
    y: w0 * arc.dropFrom.y + w1 * arc.c1.y + w2 * arc.c2.y + w3 * arc.dropTo.y,
  };
}
