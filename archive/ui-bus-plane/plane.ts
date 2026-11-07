// Lifted verbatim from the frozen `o2/console/src/layout/plane.ts` (2026-10-30);
// pure, no React, no DOM. Import paths adapted; nothing imports `o2/console` at runtime.
/**
 * The LAYOUT PLANE STAMP — the frame of reference a saved pin was authored
 * against (`docs/WORK_ORDER_console_level0_rendering.md` §8, LES-289; built in
 * R2 because R2, R3, R4 and R6 each move the plane again).
 *
 * `axr-layout/1` stores a pin as an ABSOLUTE `{x, y}` on a plane the document
 * does not identify. R1 shifted level 0's content origin right by `RAIL_W` for
 * the tier rails, and every stored pin silently moved 128 units left relative
 * to its tile — **the file still validated, still loaded, and now meant
 * something else**. A stored absolute coordinate with no frame of reference is
 * an undetectable silent corruption; this module is the frame of reference.
 *
 * **The stamp covers exactly the constants that decide where a PINNABLE
 * element lands** — the content origin, the tier stack above it and the
 * site/tile grid — because that is the only thing a pin's coordinates are
 * relative to. It deliberately does NOT cover a constant that positions
 * something unpinnable: R2 adds a tap badge and a trunk bus without moving a
 * single tile, so R2 does not invalidate a single pin and the stamp must not
 * say it does. (A cast that GAINS a segment also moves tiles, and that is not
 * a plane change: reflow-on-content is the ratified behaviour and pins are its
 * escape hatch — `15` §4.)
 *
 * **Two halves, one string, `l0-<epoch>-<hash>`:**
 *
 *  - the **hash** is derived from the constants themselves, so a slice that
 *    edits one cannot forget to bump anything — the failure mode LES-289 names
 *    ("'we'll fix it later' that is not attached to a named slice is how it
 *    becomes never") applies just as well to a hand-maintained version integer;
 *  - the **epoch** is the manual half, for a plane change no constant
 *    expresses: R6 changes *which tiles exist* (per-group budgets) and
 *    reorders them without touching a number here. Bump it in the slice that
 *    does that, and say so in the work order.
 *
 * **The epoch's scope is wider than the hash's, and this is the whole of it:**
 * a pin keyed `site:10.80` or `segment:<cidr>` also depends on rules that are
 * not constants at all -- `layout/sites.ts`'s two-octet bucketing, the
 * singleton→`misc` merge, and `model/keys.ts`'s `compareCidr` ordering, which
 * between them decide which group a tile is in and in what order columns and
 * tiles are laid out. **Bump the epoch for any change to the grouping or
 * ordering rule** (the deferred longest-common-prefix branch in `sites.ts`
 * included), exactly as for R6's budgets. A hash over numbers cannot see a
 * change in a comparator.
 *
 * Pure — no React, no DOM, no I/O. The SPA writes `PLANE_STAMP` with every
 * `PUT .../layout`; the server (`axrange/api/console_files.py`) round-trips it
 * as an opaque token and never interprets it. A file with NO stamp is
 * **legacy** — written before the stamp existed, so its pins are accepted and
 * flagged, never silently dropped.
 */
import { SITE_BUDGET } from "../model/budgets";
import { SITE_COLS_NARROW, SITE_COLS_NARROW_MAX } from "./columns";
import {
  CANVAS_PAD,
  RAIL_W,
  SITE_COLS,
  SITE_GAP_X,
  SITE_HEADER_H,
  SITE_PAD,
  TIER_GAP_Y,
  TIER_INFRA_H,
  TIER_INTERNET_H,
  TIER_WAN_H,
  TILE_GAP_X,
  TILE_GAP_Y,
  TILE_H,
  TILE_W,
} from "./geometry";

/**
 * Bumped ONLY for a plane change no constant below expresses. R1's rail gutter
 * and R2's trunk are both covered by the hash — the first because it moved
 * `CANVAS_PAD + RAIL_W`, the second because it moved nothing at all.
 *
 * **2 (R3): the INFRASTRUCTURE band's height became CONTENT-derived.**
 * `TIER_INFRA_H` is now a FLOOR rather than the band: a site column whose infra
 * devices overflow its width wraps to a second row, and the band grows to hold
 * it — which pushes the segment tier, and with it every tile, down. No constant
 * below moved, and a hash over numbers cannot see a rule that reads the data
 * (the same argument `sites.ts`'s bucketing and R6's budgets get), so the epoch
 * is the only carrier.
 *
 * **What it cost, and why it was still right.** No bundle in the tree wraps
 * today: the busiest real column holds three devices and exactly three fit
 * across — **zero spare slots**, so a single added device in that column moves
 * every tile. For every bundle that ships R2's plane and R3's plane are
 * identical, so that bump made
 * the app say "these pins may no longer line up" where in fact they do. That is
 * the SAFE direction of a question one global token cannot answer per-bundle —
 * a false flag is a sentence the operator dismisses, a missing one is the
 * silent misplacement this module exists to stop (LES-289). Blast radius today
 * is zero (the only `layout.json` in the tree holds `"pins": {}`), and pins are
 * accepted and flagged either way, never dropped.
 *
 * **3 (R6): the per-group budget changed WHICH TILES EXIST, and reordered the
 * rest.** This is the case the epoch was designed for (R2 named it when the
 * stamp was built): a group now draws its busiest `SITE_BUDGET` segments ranked
 * by placed-host count, with the rest behind a `+N more` tile, and a column is
 * two or three tiles across depending on how many it shows. A pin keyed
 * `segment:<cidr>` can therefore name a tile that is no longer drawn — it is
 * kept and re-applied when the group is expanded — and every tile that IS drawn
 * has almost certainly moved. The three constants that decide it are hashed
 * below as well, so a later change to the *numbers* invalidates on its own; the
 * epoch covers this build's change to the *rules* (the ranking comparator and
 * the collapse), which a hash over numbers cannot see.
 */
export const PLANE_EPOCH = 3;

/**
 * The constants a pin's coordinates are relative to, as `(name, value)` pairs
 * in a FIXED order. Named rather than positional so the canonical string a
 * reviewer reads says which number changed, and so reordering this list
 * (which changes the stamp) is a visible act.
 */
export const PLANE_CONSTANTS: ReadonlyArray<readonly [string, number]> = [
  // the content origin (R1's gutter lives here)
  ["CANVAS_PAD", CANVAS_PAD],
  ["RAIL_W", RAIL_W],
  // the tier stack ABOVE the tiles -- it sets the segment tier's top edge
  ["TIER_INTERNET_H", TIER_INTERNET_H],
  ["TIER_WAN_H", TIER_WAN_H],
  ["TIER_INFRA_H", TIER_INFRA_H],
  ["TIER_GAP_Y", TIER_GAP_Y],
  // the site/tile grid, INCLUDING R6's per-group budget: which tiles a group
  // draws, and how many across, decide where every pinnable tile in it lands.
  ["SITE_COLS", SITE_COLS],
  ["SITE_COLS_NARROW", SITE_COLS_NARROW],
  ["SITE_COLS_NARROW_MAX", SITE_COLS_NARROW_MAX],
  ["SITE_BUDGET", SITE_BUDGET],
  ["SITE_HEADER_H", SITE_HEADER_H],
  ["SITE_GAP_X", SITE_GAP_X],
  ["SITE_PAD", SITE_PAD],
  ["TILE_W", TILE_W],
  ["TILE_H", TILE_H],
  ["TILE_GAP_X", TILE_GAP_X],
  ["TILE_GAP_Y", TILE_GAP_Y],
];

/** The exact string the hash is taken over — exported so a test can read it
 *  and a reviewer can diff it, rather than being told a hex value moved. */
export function planeCanonicalString(
  constants: ReadonlyArray<readonly [string, number]> = PLANE_CONSTANTS,
): string {
  return constants.map(([name, value]) => `${name}=${value}`).join(";");
}

/** FNV-1a (32-bit), as 8 lowercase hex digits. A checksum, not a security
 *  primitive: its job is that two different constant sets differ, and the
 *  Console ships no crypto dependency (`15` §9). */
export function planeHash(text: string): string {
  let hash = 0x811c9dc5;
  for (let i = 0; i < text.length; i++) {
    hash ^= text.charCodeAt(i);
    // `Math.imul` keeps the multiply in 32 bits; `*` would lose precision.
    hash = Math.imul(hash, 0x01000193);
  }
  return (hash >>> 0).toString(16).padStart(8, "0");
}

/** The stamp this build writes and compares against. */
export const PLANE_STAMP = `l0-${PLANE_EPOCH}-${planeHash(planeCanonicalString())}`;

/**
 * What a saved layout document's stamp says about the pins in it.
 *
 *   - `none`    — there are no pins, so the stamp is irrelevant. Reported as
 *                 its own value rather than folded into `current`, because
 *                 "nothing to check" and "checked and fine" are different
 *                 answers and only the first is true of an empty file.
 *   - `current` — authored against this plane; draw them and say nothing.
 *   - `legacy`  — no stamp at all: saved before the stamp existed. The pins
 *                 are ACCEPTED (they are the operator's work, and the plane
 *                 may well not have moved since), flagged, and stamped on the
 *                 next write.
 *   - `stale`   — authored against a DIFFERENT plane. Also accepted and
 *                 flagged: dropping them silently would destroy work the
 *                 operator can still recover by dragging, and drawing them
 *                 without a word is the silent corruption this exists to stop.
 */
export type PinPlaneStatus = "none" | "current" | "legacy" | "stale";

export function pinPlaneStatus(
  stamp: string | null | undefined,
  pinCount: number,
): PinPlaneStatus {
  if (pinCount <= 0) return "none";
  const value = (stamp ?? "").trim();
  if (!value) return "legacy";
  return value === PLANE_STAMP ? "current" : "stale";
}

/**
 * The stamp to WRITE with a pin set — the other half of the guard, and the one
 * that is easy to get catastrophically wrong (review M1).
 *
 * A drag commits the **whole** set, not the one pin it moved. Stamping the
 * document current on every save therefore re-labels N inherited pins as
 * "authored against this plane" off a single gesture: the warning disappears
 * for ever and N−1 pins are silently in the wrong place — exactly the
 * undetectable corruption the stamp exists to prevent (LES-289), re-entering
 * through its own mechanism.
 *
 * So the stamp advances only when the set is **wholly re-authored against the
 * plane on screen**: a reset (the empty set, vacuously — there is nothing left
 * to be stale) or every surviving pin re-placed since the document was loaded.
 * Otherwise the document keeps the stamp it arrived with, so a legacy file
 * stays legacy and a stale one stays stale until the operator finishes the job.
 * A caller that cannot say what it authored gets the same conservative answer
 * — never a claim we cannot back.
 *
 * **Re-authorship is identified by VALUE, not by key** (final review S1). A set
 * of keys says "we re-placed A at some point", which stops being true the
 * moment the document underneath is replaced — and this app builds that path
 * deliberately: a 412 rolls our save back and `reloadLayout()` swaps in the
 * other writer's coordinates. Key-only, this sequence certifies a pin nobody
 * re-placed:
 *
 *     stale {A:p1, B:p2} → drag A (stays stale, right)
 *     → PUT 412s, a second writer saved {A:q1, B:q2} → reload
 *     → drag B → keys {A,B} now cover the set → ADVANCES, blessing A:q1
 *
 * Comparing the coordinates closes it: after our own successful PUT the server
 * echoes our values, so a key we wrote stays re-authored; after someone else's
 * write it does not, and the operator is asked to re-place it. The check is an
 * exact match because both sides are the same numbers we computed — no
 * tolerance to tune, and a near-miss means a different write.
 *
 * The UI's advice and this rule are one thing: `pinPlaneNote` says *reset, or
 * re-place every one*, because that is precisely what clears the flag.
 */
export function planeForSave(
  pins: Readonly<Record<string, { x: number; y: number }>>,
  authored: ReadonlyMap<string, { x: number; y: number }> | undefined,
  currentPlane: string | null | undefined,
): string | null {
  const wholly = Object.keys(pins).every((key) => {
    const mine = authored?.get(key);
    const now = pins[key];
    return !!mine && !!now && mine.x === now.x && mine.y === now.y;
  });
  return wholly ? PLANE_STAMP : (currentPlane ?? null);
}
