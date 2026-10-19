// @vitest-environment node
/**
 * The R1 width rule (`docs/WORK_ORDER_console_level0_rendering.md` §3 R1),
 * as a pure test: 1 layout unit = 1 CSS px by default, fit-to-width never
 * below 0.8, and a plane that will not fit at 0.8 pans instead of shrinking.
 *
 * The rendered half of the acceptance -- "at a 1,246 px window with the
 * outline open the level-0 SVG renders at >= 0.8 scale" -- is in
 * `views.test.tsx`, against the real `Level0`.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import {
  BASE_SCALE,
  estimateMapWidth,
  fitScale,
  MIN_FIT_SCALE,
  scaleFor,
} from "../src/layout/viewport";

describe("fitScale (R1)", () => {
  it("never scales below the 0.8 floor, however wide the plane", () => {
    // The live pit bundle: a ~2,800-unit plane in a ~737 px pane would fit at
    // 0.26 -- the ~25 % render that made 11 px type illegible (F6).
    expect(fitScale(737, 2800)).toBe(MIN_FIT_SCALE);
    expect(fitScale(100, 100000)).toBe(MIN_FIT_SCALE);
  });

  it("never scales UP: a small plane is drawn at 1:1, not blown up", () => {
    expect(fitScale(1600, 800)).toBe(BASE_SCALE);
    expect(fitScale(900, 900)).toBe(BASE_SCALE);
  });

  it("scales proportionally between the floor and 1:1", () => {
    expect(fitScale(900, 1000)).toBeCloseTo(0.9, 10);
    expect(fitScale(850, 1000)).toBeCloseTo(0.85, 10);
  });

  it("an unmeasurable pane or an empty plane yields 1:1, never a shrink", () => {
    // A zero/NaN measurement is "I don't know", and the honest answer to that
    // is the legible default -- not a scale computed from a bogus number.
    expect(fitScale(0, 1000)).toBe(BASE_SCALE);
    expect(fitScale(-10, 1000)).toBe(BASE_SCALE);
    expect(fitScale(NaN, 1000)).toBe(BASE_SCALE);
    expect(fitScale(800, 0)).toBe(BASE_SCALE);
    expect(fitScale(800, Number.POSITIVE_INFINITY)).toBe(BASE_SCALE);
  });
});

describe("scaleFor (R1)", () => {
  it("`actual` is 1 layout unit = 1 CSS px, whatever the pane", () => {
    expect(scaleFor("actual", 300, 3000)).toBe(BASE_SCALE);
    expect(scaleFor("actual", 3000, 300)).toBe(BASE_SCALE);
  });

  it("`fit` is the clamped fit", () => {
    expect(scaleFor("fit", 737, 2800)).toBe(MIN_FIT_SCALE);
    expect(scaleFor("fit", 900, 1000)).toBeCloseTo(0.9, 10);
  });
});

describe("estimateMapWidth (the jsdom / first-paint fallback)", () => {
  it("subtracts BOTH panes' minimums -- the narrowest the map ever is", () => {
    // 1246 - 224 (outline min) - 256 (inspector min) - 29 (pane padding).
    expect(estimateMapWidth(1246)).toBe(737);
    expect(estimateMapWidth(1600)).toBe(1091);
  });

  it("never returns a negative width on a window narrower than the chrome", () => {
    expect(estimateMapWidth(320)).toBe(0);
    expect(estimateMapWidth(0)).toBe(0);
    expect(estimateMapWidth(NaN)).toBe(0);
  });

  // -------------------------------------------------------------------------
  // S6: `estimateMapWidth` MIRRORS the stylesheet's grid minimums, and a
  // comment is not a guard. This reads the stylesheet and fails if the numbers
  // drift apart -- including the one a numeric check would miss: a `font-size`
  // on `html` rescales every `rem` at once, so 14rem stops being 224 px while
  // every literal above still looks right.
  // -------------------------------------------------------------------------
  const css = readFileSync(
    fileURLToPath(new URL("../src/theme/shell.css", import.meta.url)),
    "utf8",
  );

  it("the shell's grid minimums are still 14rem and 16rem", () => {
    const shell = /(?:^|\n)\.shell\s*\{([^}]*)\}/.exec(css);
    expect(shell, "the `.shell` rule is gone -- the estimate has no basis").not.toBeNull();
    const columns = /grid-template-columns:\s*([^;]+);/.exec(shell![1]!);
    expect(columns, "`.shell` no longer declares grid-template-columns").not.toBeNull();
    // 224 px and 256 px in `estimateMapWidth`, at the 16 px root below.
    expect(columns![1]).toContain("minmax(14rem, 20rem)");
    expect(columns![1]).toContain("minmax(16rem, 24rem)");
  });

  it("the pane's horizontal padding is still 0.9rem", () => {
    const pane = /(?:^|\n)\.pane\s*\{([^}]*)\}/.exec(css);
    expect(pane, "the `.pane` rule is gone").not.toBeNull();
    expect(pane![1]).toMatch(/padding:\s*[^;]*\s0\.9rem\s*;/); // 29 px in the estimate
  });

  it("NOTHING sets a font-size on `html` -- that would rescale every rem", () => {
    // The failure this catches is silent: move `font-size` from `body` to
    // `html` and every `rem` above changes value while the CSS still reads as
    // 14/16rem and the estimate still says 224/256 px.
    const offenders: string[] = [];
    for (const [, selector, body] of css.matchAll(/([^{}]*)\{([^{}]*)\}/g)) {
      const selects = (selector ?? "")
        .split(",")
        .some((part) => part.trim().split(/\s+/).includes("html"));
      if (selects && /font-size\s*:/.test(body ?? "")) offenders.push(selector!.trim());
    }
    expect(offenders).toEqual([]);
  });

  it("the acceptance case: a 1,246 px window with the outline open", () => {
    // Whatever the plane's width, the scale at this window is at or above the
    // floor -- which is the whole of R1's acceptance, stated in one line.
    const available = estimateMapWidth(1246);
    for (const contentWidth of [600, 1200, 2800, 9000]) {
      expect(fitScale(available, contentWidth)).toBeGreaterThanOrEqual(MIN_FIT_SCALE);
    }
  });
});
