/**
 * Tier assignment for level 0 (`docs/design/15-console-canvas.md` §4) and the
 * one vertical-stacking primitive the levels share.
 *
 * Level 0 is a **tiered path diagram**, top to bottom: the Internet, the WAN
 * edge (the gateway's egress), the infrastructure devices, then the segment
 * tiles, then the east-west conduit band. Tier is the layout rule -- a
 * device's place on the path -- so this module owns the ordered vocabulary and
 * the y-band stacker; `level0.ts` and `level1.ts` fill the bands.
 *
 * Pure -- no React, no DOM.
 */

/** The tiers, top to bottom. `east-west` is the conduit band beneath the
 *  tiles, not a device tier. */
export const TIERS = [
  "internet",
  "wan-edge",
  "infrastructure",
  "segment",
  "east-west",
] as const;

export type Tier = (typeof TIERS)[number];

/**
 * The rail label per tier (R1) -- the left-edge words of the reference report:
 * `INTERNET / WAN EDGE / INFRASTRUCTURE / SEGMENTS / EAST-WEST`. They live
 * beside `TIERS` because the ordered vocabulary and its words are one thing;
 * the view only renders them, and `level0.ts` only positions the boxes.
 *
 * The `segment` tier is labelled in the PLURAL ("SEGMENTS") -- the rail names
 * the band, not one element.
 */
export const TIER_LABELS: Record<Tier, string> = {
  internet: "INTERNET",
  "wan-edge": "WAN EDGE",
  infrastructure: "INFRASTRUCTURE",
  segment: "SEGMENTS",
  "east-west": "EAST-WEST",
};

export interface Band {
  y: number;
  h: number;
}

/**
 * Stack a list of band heights top to bottom from `startY`, `gap` between
 * each, returning one `{y, h}` per input in order. The single vertical-layout
 * primitive: level 0 stacks its tiers with it and level 1 stacks its role
 * bands with it, so "what sits above what" has one implementation and one
 * golden shape. A zero-height band still gets a slot (its `h` is `0`), so an
 * empty tier collapses without shifting the index of the ones below it.
 */
export function stackBands(
  heights: readonly number[],
  startY: number,
  gap: number,
): Band[] {
  const out: Band[] = [];
  let y = startY;
  for (let i = 0; i < heights.length; i++) {
    const h = heights[i] ?? 0;
    out.push({ y, h });
    // No gap after a collapsed (zero-height) band, so a run of empty bands
    // does not open a ragged hole the golden would then pin.
    y += h + (h > 0 && i < heights.length - 1 ? gap : 0);
  }
  return out;
}
