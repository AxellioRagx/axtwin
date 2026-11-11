/**
 * The bus plane's geometry (rendering rules 1, 2 and 5 of track 4,
 * 2026-11-06): a segment is a horizontal BUS -- one line with its name and
 * CIDR at the left end and a host-count badge at the right end, its hosts
 * sitting on the line as icons that wrap to a row above past a width budget;
 * a site is a vertical stack of such lanes behind a faint tint; routers sit
 * in a row above and drop a vertical leg into each bus they serve, down the
 * site's PORT REGION at the right end of every lane, where no host sits.
 *
 * 2026-11-11: the device tree draws no bus and no pill (a segment is drawn as
 * its switch, topology-layers §7.1); of this file it reads only `BUS_ICON` (a
 * host icon's size) and `OVERFLOW_LANE_H` (a fold's height). The rest stays as
 * the plane stamp's inputs (`plane.ts`), so its hash is unchanged.
 *
 * Every constant here is read by the placement and so is part of the plane
 * stamp (`plane.ts`): changing one drops every saved layout. Pure.
 */
import { CANVAS_PAD, SITE_GAP_X, SITE_HEADER_H, SITE_PAD, TIER_GAP_Y, TIER_INTERNET_H, type Box } from "../layout/geometry";

/** The left end of a lane: the hue dots, the name and the CIDR. No host sits here. */
export const BUS_LABEL_W = 168;
/** The right end of a lane: the host-count badge. */
export const BUS_BADGE_W = 40;
/** A host on the bus: a 30 px icon (the chip's ring). */
export const BUS_ICON = 30;
export const BUS_ICON_GAP = 8;
/** One host row: the icon plus the channel above it (wider than a route margin, so a stub leaves upward into clear space). */
export const BUS_ROW_H = 40;
/** The width budget: past this many icons the hosts wrap to a row above. */
export const BUS_HOSTS_PER_ROW = 10;
/** The lane's bottom strip: the line is drawn on the lane's bottom edge, its centre `BUS_LINE_H / 2` above it. */
export const BUS_LINE_H = 4;
/** Between two lanes of a site: the routing channel. */
export const BUS_GAP_Y = 24;
/** A lane is never narrower than this (a bus with one host still reads as a bus). */
export const BUS_MIN_W = 360;
/** Between two drop columns of a site's port region. */
export const PORT_GAP = 14;
/** The port region keeps this clear of the last host and of the badge. */
export const PORT_PAD = 10;
/** The `+N more` lane of an over-budget site. */
export const OVERFLOW_LANE_H = 36;
/** Between two rows of sites. */
export const SITE_ROW_GAP_Y = 40;

/** A lane's height for `rows` host rows (at least one, so an empty bus still has its line and label). */
export function laneHeight(rows: number): number {
  return Math.max(1, rows) * BUS_ROW_H + BUS_LINE_H;
}

/** The y of a lane's bus line (its centre), from the lane's absolute box. */
export function laneLineY(box: Pick<Box, "y" | "h">): number {
  return box.y + box.h - BUS_LINE_H / 2;
}

/** How many host rows `icons` icons take under the width budget. */
export function laneRows(icons: number): number {
  return Math.max(1, Math.ceil(icons / BUS_HOSTS_PER_ROW));
}

/** The width a lane's content needs: the label and its widest host row. */
export function laneContentWidth(icons: number): number {
  return BUS_LABEL_W + Math.min(icons, BUS_HOSTS_PER_ROW) * (BUS_ICON + BUS_ICON_GAP);
}

/** The port region's width for `sources` drop columns (at least one). */
export function portRegionWidth(sources: number): number {
  return 2 * PORT_PAD + Math.max(1, sources) * PORT_GAP;
}

/** The x of the `index`-th of `count` drop columns in a port region whose
 *  right edge (the badge's left edge) is `right`: column 0 is the rightmost. */
export function portColumnX(right: number, count: number, index: number): number {
  void count;
  return right - PORT_PAD - PORT_GAP / 2 - index * PORT_GAP;
}

/** A host icon's slot inside its lane, relative to the lane's top-left. */
export function iconSlot(i: number, rows: number): { x: number; y: number } {
  const row = Math.floor(i / BUS_HOSTS_PER_ROW);
  const col = i % BUS_HOSTS_PER_ROW;
  // Rows fill from the bottom (the row on the line first), so a bus with few hosts sits them on the line.
  const fromBottom = rows - 1 - row;
  return { x: BUS_LABEL_W + col * (BUS_ICON + BUS_ICON_GAP), y: fromBottom * BUS_ROW_H + (BUS_ROW_H - BUS_ICON) };
}

/** The shape a fitView should meet: a window is about this wide for its height. */
export const PLANE_ASPECT_TARGET = 1.8;
const TIERS_H_ESTIMATE = TIER_INTERNET_H + 96 + 2 * TIER_GAP_Y;

/**
 * How many sites sit side by side in one row before the next row starts, so
 * the plane comes nearest `PLANE_ASPECT_TARGET`: five corp-twin sites in one
 * row are 5 : 1, two rows of three are nearer a window. Pure: the choice with
 * the aspect nearest the target wins; a lone site is one per row.
 */
export function chooseSitesPerRow(sites: ReadonlyArray<{ w: number; h: number }>): number {
  if (!sites.length) return 1;
  let best = sites.length;
  let bestScore = Number.POSITIVE_INFINITY;
  for (let k = sites.length; k >= 1; k--) {
    const size = planeSize(sites, k);
    const aspect = size.w / (TIERS_H_ESTIMATE + size.h);
    const score = Math.abs(Math.log(aspect) - Math.log(PLANE_ASPECT_TARGET));
    if (score < bestScore) {
      bestScore = score;
      best = k;
    }
  }
  return best;
}

/** The size of the site rows at `perRow` sites per row (the tiers above not counted). */
export function planeSize(sites: ReadonlyArray<{ w: number; h: number }>, perRow: number): { w: number; h: number } {
  let w = 0;
  let h = 0;
  for (let i = 0; i < sites.length; i += perRow) {
    const row = sites.slice(i, i + perRow);
    w = Math.max(w, row.reduce((a, s) => a + s.w, 0) + (row.length - 1) * SITE_GAP_X);
    h += Math.max(...row.map((s) => s.h)) + (i ? SITE_ROW_GAP_Y : 0);
  }
  return { w: w + 2 * CANVAS_PAD, h };
}

/** A site's box for `lanes` lane heights at inner width `innerW`. */
export function siteSize(innerW: number, laneHeights: readonly number[]): { w: number; h: number } {
  const lanesH = laneHeights.reduce((a, h) => a + h, 0) + Math.max(0, laneHeights.length - 1) * BUS_GAP_Y;
  return { w: innerW + 2 * SITE_PAD, h: SITE_HEADER_H + 2 * SITE_PAD + lanesH };
}
