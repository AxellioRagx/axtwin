/**
 * The canvas's own PLANE STAMP -- the frame of reference a saved pin's
 * absolute `{x, y}` is relative to (the rule `layout/plane.ts` states for the
 * frozen console's level-0 plane, applied to this drawing).
 *
 * The canvas re-packs the tiered engine's tiles at content-derived sizes, so
 * its coordinates are not the frozen console's `l0-*` plane: a layout
 * document stamped for that plane (or for none -- a legacy file) is read but
 * its pins are NOT applied here, and the page says so. The server round-trips
 * the stamp as an opaque token (`console_files.py`); the comparison lives here.
 *
 * `canvas-<epoch>-<hash>`: the hash covers every constant the placement reads,
 * so a slice that changes one cannot forget to invalidate; the epoch is for a
 * change to the packing RULES, which a hash over numbers cannot see.
 */
import {
  BAND_PAD,
  CANVAS_PAD,
  CHIP_GAP_X,
  CHIP_GAP_Y,
  CHIP_H,
  CHIP_W,
  CHIPS_PER_ROW,
  DEVICE_GAP_X,
  DEVICE_GAP_Y,
  DEVICE_H,
  DEVICE_W,
  SITE_GAP_X,
  SITE_HEADER_H,
  SITE_PAD,
  TIER_GAP_Y,
  TIER_INTERNET_H,
  TILE_GAP_X,
  TILE_GAP_Y,
  TILE_H,
  TILE_W,
} from "../layout/geometry";
import { SITE_BUDGET } from "../model/budgets";

// Epoch 2 (2026-11-05): a site's tile columns are chosen for the plane's
// shape (`placement.ts` `chooseSiteCols`), no longer the engine's count.
// Epoch 3 (2026-11-06, rendering rule 1): the WAN band is gone -- the
// gateway is a badge on its router -- so the router band and the sites sit
// higher; a pin saved under the old bands would put a router inside a site.
export const CANVAS_PLANE_EPOCH = 3;

export const CANVAS_PLANE_CONSTANTS: ReadonlyArray<readonly [string, number]> = [
  ["CANVAS_PAD", CANVAS_PAD],
  ["TIER_INTERNET_H", TIER_INTERNET_H],
  ["TIER_GAP_Y", TIER_GAP_Y],
  ["DEVICE_W", DEVICE_W],
  ["DEVICE_H", DEVICE_H],
  ["DEVICE_GAP_X", DEVICE_GAP_X],
  ["DEVICE_GAP_Y", DEVICE_GAP_Y],
  ["SITE_PAD", SITE_PAD],
  ["SITE_HEADER_H", SITE_HEADER_H],
  ["SITE_GAP_X", SITE_GAP_X],
  ["SITE_BUDGET", SITE_BUDGET],
  ["TILE_W", TILE_W],
  ["TILE_H", TILE_H],
  ["TILE_GAP_X", TILE_GAP_X],
  ["TILE_GAP_Y", TILE_GAP_Y],
  ["CHIP_W", CHIP_W],
  ["CHIP_H", CHIP_H],
  ["CHIP_GAP_X", CHIP_GAP_X],
  ["CHIP_GAP_Y", CHIP_GAP_Y],
  ["CHIPS_PER_ROW", CHIPS_PER_ROW],
  ["BAND_PAD", BAND_PAD],
];

/** FNV-1a over the canonical `name=value` list, as 8 hex digits. */
function fnv(text: string): string {
  let h = 0x811c9dc5;
  for (let i = 0; i < text.length; i++) {
    h ^= text.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return h.toString(16).padStart(8, "0");
}

export const CANVAS_PLANE = `canvas-${CANVAS_PLANE_EPOCH}-${fnv(CANVAS_PLANE_CONSTANTS.map(([k, v]) => `${k}=${v}`).join(";"))}`;
