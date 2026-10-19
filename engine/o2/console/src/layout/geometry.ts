/**
 * Geometry primitives and the layout's fixed dimensions.
 *
 * Added to `docs/WORK_ORDER_console_canvas.md` §4's proposed module list (an
 * explicit implementation call there) for the same reason S2 added
 * `model/zoom.ts`: `Box`/`Point` and the spacing constants are a contract the
 * six pure layout modules share, and a second copy is a second contract. No
 * React, no DOM -- these are numbers a golden file pins.
 *
 * The unit is an abstract "layout unit" ≈ one CSS pixel at 1×; the SVG the
 * views draw scales the whole plane, so nothing here assumes a viewport.
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

export function centerOf(box: Box): Point {
  return { x: box.x + box.w / 2, y: box.y + box.h / 2 };
}

/** Two decimals -- the rounding every computed (as opposed to grid-derived)
 *  coordinate and width passes through, so a golden file pins a readable
 *  number rather than a 17-digit float. One copy, shared by `scale.ts`'s
 *  widths and `arcs.ts`'s control depths (LES-025). */
export function round2(value: number): number {
  return Math.round(value * 100) / 100;
}

/**
 * The y of the horizontal edge of `box` that faces `fromY` -- its TOP when the
 * connector comes from above, its BOTTOM when it comes from below.
 *
 * The one rule two connector families need (R2's trunk legs, R3's infra
 * stubs), so it lives here rather than twice: a connector that always ended at
 * `box.y` would run straight through the box it points at whenever the thing
 * it comes from sits beneath it -- which a pin can arrange at any time
 * (`15` §4: an absolute override with no reflow). Touching an edge is not
 * entering a box, which is why `test/layout/helpers.ts` clips against the
 * CLOSED box and then tests the midpoint for strict interiority.
 */
export function nearestEdgeY(box: Box, fromY: number): number {
  return fromY <= box.y ? box.y : box.y + box.h;
}

// --- level 0 dimensions ----------------------------------------------------
export const CANVAS_PAD = 24;

/** The left-edge **tier rail** gutter (R1, `15` §4: each tier is labelled by a
 *  rail at the left edge). Everything the diagram draws starts at
 *  `CANVAS_PAD + RAIL_W`, so a rail label never sits under a tile;
 *  `RAIL_GAP_X` is the breathing room between the label box and the content. */
export const RAIL_W = 128;
export const RAIL_GAP_X = 16;

/** A segment tile. */
export const TILE_W = 156;
export const TILE_H = 96;
export const TILE_GAP_X = 16;
export const TILE_GAP_Y = 16;

/** Tiles wrap into a fixed number of columns inside a site, so a site's WIDTH
 *  never changes when a tile is added -- only its height, and only that site's
 *  (the locality property, `15` §5). */
export const SITE_COLS = 3;
export const SITE_HEADER_H = 26;
export const SITE_GAP_X = 34;
export const SITE_PAD = 12;

/** The stacked tiers above the segment area, each a full-width band. */
export const TIER_INTERNET_H = 52;
export const TIER_WAN_H = 60;
export const TIER_INFRA_H = 96;
export const TIER_GAP_Y = 26;

/** An infrastructure device box (`15` §4). */
export const DEVICE_W = 150;
export const DEVICE_H = 66;
export const DEVICE_GAP_X = 18;
/** The vertical gap between wrapped device rows (R3): a device row that
 *  overflows its site column's width wraps, and the INFRASTRUCTURE band grows
 *  to hold the extra rows rather than overlapping the tier beneath it. */
export const DEVICE_GAP_Y = 12;

/** A tile's role badges (R3) -- `DC 2`, `EP 14`. Sized for a four-glyph
 *  abbreviation plus a count at 9 px; three of them span the tile's inner
 *  width, which is what retired the unlabeled 8 px histogram bar. */
export const BADGE_H = 13;
export const BADGE_GAP_X = 4;

/** The Internet pill and the WAN-edge box. */
export const INTERNET_W = 220;
export const WAN_W = 260;

/** The capture-tap badge on the Internet→WAN trunk (R2). Sized for the word
 *  `TAP`; the vantage string is drawn BESIDE it, never inside, because it is
 *  displayed verbatim and the two real bundles spell it differently
 *  (`axellio-hq/corp-apcn-rdr1:12` and `https://10.80.65.7:0-11`). */
export const TAP_W = 56;
export const TAP_H = 22;

/** The trunk's horizontal bus (R2) sits this far below the WAN-edge box; the
 *  site legs drop from it to each group header's top edge. It fits inside the
 *  existing WAN→infrastructure tier gap, so the trunk adds no height and moves
 *  no tile (`layout/plane.ts`: R2 invalidates no pin). */
export const TRUNK_BUS_GAP_Y = 14;

/**
 * The east-west band beneath the tiles (R4, `layout/arcs.ts`): every link is a
 * cubic arc leaving its group's bottom edge, so the band is a SHARED depth
 * rather than one channel per pair.
 *
 * The depth grows with the horizontal span (`ARC_DEPTH_MIN + ARC_DEPTH_PER_DX
 * · |Δx|`) so arcs between different column pairs do not lie on top of each
 * other, and is CLAMPED at `ARC_DEPTH_MAX` so the band's height is bounded by
 * `ARC_DEPTH_MAX + ARC_BAND_PAD` whatever the plane's width or the pair count
 * (§3 R4 acceptance). `ARC_DEPTH_PER_DX` is chosen so the clamp is reached
 * roughly at a full plane width rather than at three tiles, which is what
 * keeps the fan readable: one column apart (≈524) is ≈54 deep, a canvas apart
 * (≈2000) is the full 120.
 *
 * (These replaced `LANE_BAND_GAP_Y`/`LANE_CHANNEL_H`, retired with
 * `layout/lanes.ts` in R4 — 40 pairs × one 11 px channel was a 440 px band.)
 */
export const ARC_DEPTH_MIN = 28;
export const ARC_DEPTH_MAX = 120;
export const ARC_DEPTH_PER_DX = 0.05;
export const ARC_BAND_PAD = 20;

// --- level 1 (the spine drill) dimensions ---------------------------------
/** A host chip in a role band. */
export const CHIP_W = 132;
export const CHIP_H = 30;
export const CHIP_GAP_X = 10;
export const CHIP_GAP_Y = 8;
/** `15` §4: "two chips per row". */
export const CHIPS_PER_ROW = 2;

export const BAND_LABEL_H = 20;
export const BAND_GAP_Y = 16;
export const BAND_PAD = 8;
/** A collapsed band's rung summary row. */
export const RUNG_ROW_H = 26;

/** The spine sits in the middle; role bands to its left, peers to its right. */
export const SPINE_W = 150;
export const SPINE_GAP_X = 90;
export const BAND_COL_W = CHIPS_PER_ROW * CHIP_W + (CHIPS_PER_ROW - 1) * CHIP_GAP_X + 2 * BAND_PAD;

/** A conduit-peer / exit box on the right of the spine. */
export const PEER_W = 150;
export const PEER_H = 40;
export const PEER_GAP_Y = 12;

/** The router leg box above the spine. */
export const ROUTER_LEG_W = 150;
export const ROUTER_LEG_H = 44;

// --- matrix dimensions -----------------------------------------------------
export const MATRIX_CELL = 30;
export const MATRIX_HEADER = 128;
