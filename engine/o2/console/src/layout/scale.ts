/**
 * Volume → stroke width, in ONE place (R2, `docs/WORK_ORDER_console_level0_
 * rendering.md` §3 R2 — and R4's arcs reuse it verbatim, which is why it is a
 * module and not two lines inside `trunk.ts`).
 *
 * The rule the order states: `1 + 5 · normalised log10(bytes)`. **Log**,
 * because the real spread is four orders of magnitude (the CorpAPCN pit
 * bundle's north-south per site runs from 4.9 MB to 2.36 GB) and a linear
 * scale would draw every element but the busiest as the same hairline.
 *
 * **Normalised over the SET'S OWN log range, not against an absolute zero.**
 * This is the whole of the review's S2 and it matters more for R4 than for the
 * trunk: normalising by `log10(1+max)` alone spends most of the 1..6 band on
 * magnitudes no twin contains, so on the pit bundle a 2.2× difference between
 * the two big sites came out 0.18 px apart, and R4's top-12 east-west pairs
 * landed inside **0.66 px of each other** (5.05 … 4.39) — §1 property 3
 * ("magnitude visible before any label") failing across eleven of twelve arcs.
 * Against `[min .. max]` of the values actually present, those same twelve
 * span **2.06 px** (3.06 … 1.00). The cost is deliberate and worth stating:
 * width is **relative within one drawing**, so the thinnest non-zero element
 * is always `MIN_STROKE` and the widest always `MIN_STROKE + STROKE_RANGE`;
 * it is never an absolute byte rate (§8: a window set, not a rate).
 *
 * **Zero is a HAIRLINE, never nothing.** A site with no observed north-south
 * traffic still has a leg: "no traffic was observed on this edge in this
 * window" and "this group is not attached" are different statements, and
 * dropping the connector would draw the second one. Zero is outside the range
 * by construction (`min` is the smallest POSITIVE member), so it cannot be
 * confused with the thinnest real value.
 *
 * Pure — no React, no DOM. Widths are rounded to two decimals (`geometry.
 * round2`, shared with `arcs.ts`'s control depths) so a golden file pins a
 * readable number rather than a 17-digit float.
 */
import { round2 } from "./geometry";

/** The width of the thinnest NON-zero element (the set's own minimum). */
export const MIN_STROKE = 1;

/** Added to `MIN_STROKE` at the set maximum, so the widest element is 6. */
export const STROKE_RANGE = 5;

/**
 * A zero-volume element's width: present, and visibly nothing.
 *
 * **This number is the CLAIM, not the rendering.** The view marks a silent
 * element `.trunk.is-silent`, and that rule's `stroke-width: 1` (dashed)
 * overrides this presentation attribute in the browser — deliberately, because
 * half a pixel under 50 % opacity is not a claim anyone can see. So the DOM
 * attribute and a test reading it say `0.5` while the pixels say 1: the layout
 * states "no magnitude", the stylesheet states "and here is how to see that".
 * A test asserting the width should assert the class beside it (review S4).
 */
export const HAIRLINE = 0.5;

/**
 * The span a drawing's widths are scaled against: the smallest POSITIVE member
 * and the largest. `{min: 0, max: 0}` when the set has no positive member at
 * all — every element is then a hairline, which is the honest picture of a
 * twin whose edge carried nothing in this window.
 */
export interface VolumeRange {
  min: number;
  max: number;
}

/** Build the range from the values a single drawing will show. The caller
 *  passes its OWN set (the trunk's legs; R4's top-K pairs), which is what makes
 *  the band relative to the picture rather than to a global constant. */
export function volumeRange(values: Iterable<number>): VolumeRange {
  let min = Number.POSITIVE_INFINITY;
  let max = 0;
  for (const value of values) {
    if (!Number.isFinite(value) || value <= 0) continue;
    if (value < min) min = value;
    if (value > max) max = value;
  }
  return max > 0 ? { min, max } : { min: 0, max: 0 };
}

/**
 * `(log10(1+value) − log10(1+min)) / (log10(1+max) − log10(1+min))`, clamped
 * to `[0, 1]`.
 *
 * `1 +` so a single byte is not `-∞`. Clamped at both ends so a caller passing
 * a stale range draws a full-width or minimum-width element rather than one
 * off the scale. A set with ONE distinct magnitude (`min === max`) yields `1`:
 * that value is the maximum, and the alternative — dividing by zero — is not a
 * picture. Every degenerate input (non-finite, non-positive, an empty range)
 * yields `0`, the thinnest end: the honest answer to "how big is this relative
 * to a range I do not have" is the mark that claims least.
 */
export function normalizedLog10(value: number, range: VolumeRange): number {
  if (!Number.isFinite(value) || value <= 0) return 0;
  if (!Number.isFinite(range.max) || range.max <= 0) return 0;
  const lo = Math.log10(1 + Math.max(0, range.min));
  const hi = Math.log10(1 + range.max);
  if (hi <= lo) return 1;
  return Math.min(1, Math.max(0, (Math.log10(1 + value) - lo) / (hi - lo)));
}

/**
 * The stroke width for `bytes` within a drawing whose values span `range`.
 *
 *   - `bytes <= 0` (or non-finite) → `HAIRLINE`;
 *   - a valid pair → `MIN_STROKE + STROKE_RANGE · normalisedLog10`;
 *   - `bytes > 0` with an unusable range → `MIN_STROKE`, the thinnest width
 *     that still says "there is traffic here" without claiming a magnitude.
 */
export function strokeForBytes(bytes: number, range: VolumeRange): number {
  if (!Number.isFinite(bytes) || bytes <= 0) return HAIRLINE;
  if (!Number.isFinite(range.max) || range.max <= 0) return MIN_STROKE;
  return round2(MIN_STROKE + STROKE_RANGE * normalizedLog10(bytes, range));
}
