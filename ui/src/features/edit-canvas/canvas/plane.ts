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
  CANVAS_PAD,
  DEVICE_GAP_X,
  DEVICE_GAP_Y,
  DEVICE_H,
  DEVICE_W,
  INTERNET_W,
  SITE_GAP_X,
  SITE_HEADER_H,
  SITE_PAD,
  TIER_GAP_Y,
  TIER_INTERNET_H,
  TILE_H,
  TILE_W,
} from "../layout/geometry";
import { SITE_BUDGET } from "../model/budgets";
import { BUS_BADGE_W, BUS_GAP_Y, BUS_HOSTS_PER_ROW, BUS_ICON, BUS_ICON_GAP, BUS_LABEL_W, BUS_LINE_H, BUS_MIN_W, BUS_ROW_H, OVERFLOW_LANE_H, PLANE_ASPECT_TARGET, PORT_GAP, PORT_PAD, SITE_ROW_GAP_Y } from "./bus";

// Epoch 2 (2026-11-05): a site's tile columns are chosen for the plane's
// shape (`placement.ts` `chooseSiteCols`), no longer the engine's count.
// Epoch 3 (2026-11-06, rendering rule 1): the WAN band is gone -- the
// gateway is a badge on its router -- so the router band and the sites sit
// higher; a pin saved under the old bands would put a router inside a site.
// Epoch 4 (2026-11-06, rendering rule 5): a router's node is its drawn box
// (`DEVICE_W` wide), no longer a span over the segments it joins; a pin
// saved for the span's left edge would put the icon `(span - DEVICE_W) / 2`
// to the left of where it was.
// Epoch 5 (2026-11-06, track 4): the BUS plane -- a segment is a lane in a
// vertical stack, not a tile in a grid, and a `segment:` pin is the lane's
// RANK in its site, not a free position; a tile's pin read as a rank would
// put every bus at the end of its site.
// Epoch 6 (2026-11-07, the device tree, `docs/design/topology-layers.md` §7):
// positions come from a layered layout under the carrying device, a segment
// is a compact pill whose `segment:` pin is an absolute top-left (a bus's
// rank is gone), and a switch is a node of its own; a pin saved on the bus
// plane would put a pill at a rank number.
// Epoch 7 (2026-11-11, segments drawn as switches, topology-layers §7.1): a segment is no longer a
// pill -- a segment no real switch carries is an implied switch the size of a device, a carried one
// has no box at all, and every host is a node of its own under its switch; a `segment:` or `host:`
// pin saved on the pill plane would put an implied switch at a pill's top-left and a host at an
// icon slot inside a pill that is gone.
export const CANVAS_PLANE_EPOCH = 7;

export const CANVAS_PLANE_CONSTANTS: ReadonlyArray<readonly [string, number]> = [
  ["CANVAS_PAD", CANVAS_PAD],
  ["TIER_INTERNET_H", TIER_INTERNET_H],
  ["TIER_GAP_Y", TIER_GAP_Y],
  ["INTERNET_W", INTERNET_W],
  ["DEVICE_W", DEVICE_W],
  ["DEVICE_H", DEVICE_H],
  ["DEVICE_GAP_X", DEVICE_GAP_X],
  ["DEVICE_GAP_Y", DEVICE_GAP_Y],
  ["SITE_PAD", SITE_PAD],
  ["SITE_HEADER_H", SITE_HEADER_H],
  ["SITE_GAP_X", SITE_GAP_X],
  ["SITE_ROW_GAP_Y", SITE_ROW_GAP_Y],
  ["SITE_BUDGET", SITE_BUDGET],
  ["TILE_W", TILE_W],
  ["TILE_H", TILE_H],
  ["BUS_LABEL_W", BUS_LABEL_W],
  ["BUS_BADGE_W", BUS_BADGE_W],
  ["BUS_ICON", BUS_ICON],
  ["BUS_ICON_GAP", BUS_ICON_GAP],
  ["BUS_ROW_H", BUS_ROW_H],
  ["BUS_HOSTS_PER_ROW", BUS_HOSTS_PER_ROW],
  ["BUS_LINE_H", BUS_LINE_H],
  ["BUS_GAP_Y", BUS_GAP_Y],
  ["BUS_MIN_W", BUS_MIN_W],
  ["PORT_GAP", PORT_GAP],
  ["PORT_PAD", PORT_PAD],
  ["OVERFLOW_LANE_H", OVERFLOW_LANE_H],
  ["PLANE_ASPECT_TARGET", PLANE_ASPECT_TARGET],
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
