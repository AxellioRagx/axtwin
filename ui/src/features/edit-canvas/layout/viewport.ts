// Lifted verbatim from the frozen `o2/console/src/layout/viewport.ts` (2026-10-30);
// pure, no React, no DOM. Import paths adapted; nothing imports `o2/console` at runtime.
/**
 * How much of the level-0 plane the browser shows, and at what scale (R1,
 * `docs/WORK_ORDER_console_level0_rendering.md` §3 R1 -- F6, "illegible").
 *
 * The rule the order rules, spelled once, here:
 *
 *   - the map's **base scale is 1 layout unit = 1 CSS px** and it **scrolls in
 *     both axes** at that scale -- legibility first, panning second;
 *   - a **fit-to-width** control shrinks the plane to the pane, but **never
 *     below `MIN_FIT_SCALE` (0.8)**; a plane that will not fit at 0.8 pans
 *     instead of shrinking further.
 *
 * Why a floor at all: the pre-R1 build sized the SVG `max-width: 100%; height:
 * auto`, which fitted a ~2,800-unit plane into a ~700 px pane at ~25 % and
 * rendered 11 px type at under 3 px. A floor makes the worst case a scroll
 * gesture rather than an unreadable page, and it bounds the on-screen type:
 * chrome fonts are declared in px on the SVG root (`theme/shell.css`), so the
 * smallest type the map can ever draw is `font-size * MIN_FIT_SCALE`.
 *
 * Pure -- no React, no DOM (the view measures; this decides). Not golden-tested
 * with the layout goldens on purpose: this is a VIEWPORT concern, not layout
 * (LES-268 -- the pure layout's coordinates must not move when the window
 * does), so it takes numbers and returns a number.
 */

/** 1 layout unit = 1 CSS px: the default, and the most legible scale. */
export const BASE_SCALE = 1;

/** The floor a fit-to-width may not cross (R1). Below it the map pans. */
export const MIN_FIT_SCALE = 0.8;

/** The two ways the map can be sized. `actual` is the default. */
export type MapScaleMode = "actual" | "fit";

/**
 * The scale for `contentWidth` layout units in an `available`-px-wide pane,
 * clamped to `[MIN_FIT_SCALE, BASE_SCALE]`.
 *
 * Never scales UP (a small twin is drawn at 1:1, not blown up to fill the
 * pane -- magnification would make a 6-tile twin look like a 40-tile one), and
 * never below the floor. A non-finite or non-positive input (an unmeasurable
 * pane, a zero-width plane) yields `BASE_SCALE`: an unknown viewport must not
 * shrink the drawing.
 */
export function fitScale(available: number, contentWidth: number): number {
  if (!Number.isFinite(available) || !Number.isFinite(contentWidth)) return BASE_SCALE;
  if (available <= 0 || contentWidth <= 0) return BASE_SCALE;
  const raw = available / contentWidth;
  if (raw >= BASE_SCALE) return BASE_SCALE;
  return Math.max(MIN_FIT_SCALE, raw);
}

/** The scale for a mode -- the one place the two modes' arithmetic lives, so a
 *  view never open-codes "fit means this, actual means that". */
export function scaleFor(
  mode: MapScaleMode,
  available: number,
  contentWidth: number,
): number {
  return mode === "fit" ? fitScale(available, contentWidth) : BASE_SCALE;
}

/**
 * A conservative estimate of the map pane's inner width for a window of
 * `windowWidth` CSS px, used ONLY when the pane cannot be measured (jsdom, or
 * the first paint before layout runs). It mirrors `theme/shell.css`'s grid
 * MINIMUMS with the outline pane **open** -- the narrowest the map ever is --
 * so an estimate errs toward a smaller scale rather than a clipped drawing.
 *
 * A real browser measures `clientWidth` and never reaches this; it exists so
 * the "at a 1,246 px window the map renders at >= 0.8" acceptance is testable
 * in jsdom, where every element measures 0.
 */
export function estimateMapWidth(windowWidth: number): number {
  if (!Number.isFinite(windowWidth) || windowWidth <= 0) return 0;
  // shell.css: outline `minmax(14rem, 20rem)` = 224 px at the 16 px root,
  // inspector `minmax(16rem, 24rem)` = 256 px, `.pane` padding 0.9rem each
  // side = 28.8 px, rounded up to 29.
  const OUTLINE_MIN = 224;
  const INSPECTOR_MIN = 256;
  const PANE_PADDING = 29;
  return Math.max(0, windowWidth - OUTLINE_MIN - INSPECTOR_MIN - PANE_PADDING);
}
