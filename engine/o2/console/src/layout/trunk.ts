/**
 * The TRUNK (R2, `docs/WORK_ORDER_console_level0_rendering.md` §3 R2) — the
 * connected geometry that turns level 0's grid of tiles into a *path*:
 *
 *     Internet pill
 *          │            ← the spine, carrying the tap badge
 *      WAN edge
 *          │            ← the stem
 *     ─────┴───────     ← the bus
 *      │   │    │       ← one leg per site group, width by north-south bytes
 *   [10.20][10.80][misc]
 *
 * Pure geometry: in come four boxes' worth of already-placed chrome (the
 * Internet pill, the WAN edge, each site header) plus each group's north-south
 * volume; out come orthogonal polylines. It knows nothing about the graph, the
 * export or React — `level0.ts` hands it coordinates and `Level0.tsx` draws
 * what it returns, which is what lets `applyPins` re-run it over MOVED site
 * headers and get a leg that tracks a dragged group for free (LES-266).
 *
 * **A leg ends AT its group header's NEAREST horizontal edge and never enters
 * the header box.** Normally that is the top edge; a group a pin has dragged
 * ABOVE the bus is approached from below, so the leg stops at its BOTTOM edge
 * instead — ending at the top would run the leg straight through the header it
 * is pointing at (review S1). The §1 property-2 invariant ("nothing crosses a
 * tile") is checked as computed geometry by `test/layout/helpers.ts`'s
 * `noConnectorCrossesATile`, not by looking at a screenshot.
 *
 * **Known limit, pinned by test rather than fixed:** a column pinned so that
 * its TILES straddle the bus line is crossed by the bus — and, dragged far
 * enough above it, by its own leg. That is inherent to an absolute-override
 * pin with no reflow (`15` §4): nothing re-routes around a pinned box. It is
 * recorded in `test/r2.trunk.test.ts` with the coordinates that produce it.
 *
 * **Every group gets a leg, including one with no observed north-south
 * traffic** — a hairline (`layout/scale.ts`), never nothing. "No traffic was
 * observed here in this window" and "this group is not attached to the edge"
 * are different statements and only the first is ours to make.
 *
 * The bus sits in the existing WAN→infrastructure tier gap, so the trunk adds
 * no height to the plane and moves no tile. The legs DO cross the
 * infrastructure band, where R1's device row is still centred — they are drawn
 * beneath it, so a device reads as sitting on the trunk rather than being
 * struck through. R3 moves each device over its own site column and gives it
 * an orthogonal stub, which is what settles that band properly.
 */
import { nearestEdgeY, TRUNK_BUS_GAP_Y, type Box, type Point } from "./geometry";
import { HAIRLINE, MIN_STROKE, STROKE_RANGE, strokeForBytes, volumeRange } from "./scale";

/** A group's leg: the vertical from the bus down to its header's top-centre. */
export interface TrunkLeg {
  /** `site:<prefix>` / `site:misc` — the same key a pin uses. */
  siteKey: string;
  /** `[{bus}, {header's nearest horizontal edge, centred}]` — orthogonal, one
   *  vertical. */
  points: Point[];
  /** North-south volume for the group, in this window set (never a rate). */
  bytes: number;
  flows: number;
  /** From `layout/scale.ts`; `HAIRLINE` when `bytes` is 0. */
  strokeWidth: number;
  /** Whether the view draws this leg's byte label. Decided HERE, so the
   *  golden pins which legs are labelled rather than leaving it to a view
   *  constant nothing checks. */
  labelled: boolean;
}

export interface Trunk {
  /** Internet pill bottom-centre → WAN edge top-centre. One vertical when the
   *  two are centred on the same x (they are, by construction in `level0.ts`),
   *  an orthogonal dogleg otherwise — never a diagonal. */
  spine: Point[];
  /** Where the tap badge's centre sits on the spine. */
  tapAnchor: Point;
  /** WAN edge bottom-centre → the bus. Empty when there are no legs to reach. */
  stem: Point[];
  /** The horizontal bus the legs hang from; `null` when there are no legs. */
  bus: Point[] | null;
  /** One per site group, in the order the groups were given (prefix order,
   *  `misc` last — D1's v1 default: stability over salience, since the width
   *  already carries salience). */
  legs: TrunkLeg[];
  /** The whole edge's volume, which is NOT the sum of the legs: traffic whose
   *  local end is an UNPLACED entity crosses the edge but belongs to no group.
   *  Published separately so the spine's width is honest and the difference is
   *  recoverable rather than quietly dropped. */
  bytes: number;
  flows: number;
  /** The spine's width — always the full width, since the spine carries the
   *  set's maximum by definition. */
  strokeWidth: number;
}

export interface TrunkGroup {
  siteKey: string;
  header: Box;
  bytes: number;
  flows: number;
}

export interface TrunkInput {
  internet: Box;
  wanEdge: Box;
  groups: readonly TrunkGroup[];
  /** Total local↔non-local volume, including unplaced local ends. */
  bytes: number;
  flows: number;
}

/** A leg is labelled when it carries at least this share of the BUSIEST leg's
 *  bytes. Against the busiest leg rather than the trunk total, so the label set
 *  does not thin out on a twin whose traffic is mostly from unplaced entities;
 *  the busiest leg is therefore always labelled. */
export const LEG_LABEL_MIN_SHARE = 0.05;

function centerX(box: Box): number {
  return box.x + box.w / 2;
}

export function layoutTrunk(input: TrunkInput): Trunk {
  const { internet, wanEdge } = input;
  const internetCx = centerX(internet);
  const internetBottom = internet.y + internet.h;
  const wanCx = centerX(wanEdge);
  const wanTop = wanEdge.y;
  const wanBottom = wanEdge.y + wanEdge.h;

  // The spine. Both boxes are centred on the same x in `level0.ts`, so this is
  // one vertical; the dogleg branch keeps the function total (and orthogonal)
  // if a later slice ever decentres one of them.
  const spine: Point[] =
    internetCx === wanCx
      ? [
          { x: internetCx, y: internetBottom },
          { x: wanCx, y: wanTop },
        ]
      : [
          { x: internetCx, y: internetBottom },
          { x: internetCx, y: (internetBottom + wanTop) / 2 },
          { x: wanCx, y: (internetBottom + wanTop) / 2 },
          { x: wanCx, y: wanTop },
        ];
  const tapAnchor: Point = {
    x: internetCx === wanCx ? wanCx : (internetCx + wanCx) / 2,
    y: (internetBottom + wanTop) / 2,
  };

  // The widths are relative to THIS drawing's own spread (review S2), so the
  // thinnest non-zero leg is always 1 and the busiest always 6.
  const range = volumeRange(input.groups.map((g) => g.bytes));
  const busY = wanBottom + TRUNK_BUS_GAP_Y;
  const legs: TrunkLeg[] = input.groups.map((group) => {
    const legX = centerX(group.header);
    // S1: the header's NEAREST horizontal edge. A group pinned above the bus is
    // approached from below, so the leg stops at its bottom edge; ending at the
    // top would drive the leg through the box it points at. The rule is shared
    // with R3's infra stubs (`geometry.ts`), which need exactly the same one.
    const endY = nearestEdgeY(group.header, busY);
    return {
      siteKey: group.siteKey,
      points: [
        { x: legX, y: busY },
        { x: legX, y: endY },
      ],
      bytes: group.bytes,
      flows: group.flows,
      strokeWidth: strokeForBytes(group.bytes, range),
      labelled: group.bytes > 0 && group.bytes >= LEG_LABEL_MIN_SHARE * range.max,
    };
  });

  // No groups ⇒ no bus and no stem: a stem to a bus that reaches nothing would
  // draw an edge into empty space.
  if (legs.length === 0) {
    return {
      spine, tapAnchor, stem: [], bus: null, legs,
      bytes: input.bytes, flows: input.flows,
      strokeWidth: spineWidth(input.bytes),
    };
  }

  const xs = [wanCx, ...legs.map((leg) => leg.points[0]!.x)];
  return {
    spine,
    tapAnchor,
    stem: [
      { x: wanCx, y: wanBottom },
      { x: wanCx, y: busY },
    ],
    bus: [
      { x: Math.min(...xs), y: busY },
      { x: Math.max(...xs), y: busY },
    ],
    legs,
    bytes: input.bytes,
    flows: input.flows,
    strokeWidth: spineWidth(input.bytes),
  };
}

/**
 * The spine carries everything every leg does and more, so it IS this
 * drawing's maximum by construction -- stated directly rather than run through
 * a degenerate one-value range, which would say the same thing less clearly. A
 * cast-declared gateway whose edge was silent in this window gets the hairline.
 */
function spineWidth(bytes: number): number {
  return bytes > 0 ? MIN_STROKE + STROKE_RANGE : HAIRLINE;
}
