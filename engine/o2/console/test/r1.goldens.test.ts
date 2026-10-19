// @vitest-environment node
/**
 * The file-reading half of the R1 audit (`docs/WORK_ORDER_console_level0_
 * rendering.md` §3 R1): the rails as they are COMMITTED, and the stylesheet
 * rule split that keeps level 1 and the matrix on the old fit-to-pane rule.
 *
 * Separate from `r1.acceptance.test.tsx` because both assertions read files
 * from the package, and `node` is the only environment where a path built
 * from `import.meta.url` stays inside it -- under `jsdom` that base is the
 * document's `http://localhost/`. `layout.golden.test.ts` opts into `node`
 * for the same reason.
 *
 * Why read the golden FILE rather than recompute `layout0`: the shipped R1
 * test asserts the rails against a freshly computed layout, which proves the
 * function is self-consistent but not that the committed artefact a reviewer
 * reads actually contains them. A golden nobody reads is a golden that rots.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import { buildGraph } from "../src/model/graph";
import { layout0 } from "../src/layout/level0";
import { CANVAS_PAD, RAIL_GAP_X, RAIL_W } from "../src/layout/geometry";
import { TIERS } from "../src/layout/tiers";
import { trimmedCast, trimmedExport } from "./fixtures/trimmed";

interface GoldenRail {
  tier: string;
  box: { x: number; y: number; w: number; h: number };
}

interface Level0Golden {
  width: number;
  height: number;
  rails: GoldenRail[];
}

function golden(name: string): Level0Golden {
  const path = fileURLToPath(new URL(`./layout/${name}.golden.json`, import.meta.url));
  return JSON.parse(readFileSync(path, "utf8")) as Level0Golden;
}

/** The two goldens R1 regenerated -- and the only two it may have. */
const REGENERATED = ["level0", "level0-added-column"] as const;

describe("the rails are IN the committed level-0 goldens", () => {
  for (const name of REGENERATED) {
    describe(name, () => {
      const g = golden(name);

      it("carries one rail per tier, in TIERS order", () => {
        expect(g.rails, "no `rails` key -- the golden predates R1").toBeDefined();
        expect(g.rails).toHaveLength(TIERS.length);
        expect(g.rails.map((r) => r.tier)).toEqual([...TIERS]);
      });

      it("stacks them top to bottom with NO overlap", () => {
        for (let i = 1; i < g.rails.length; i++) {
          const above = g.rails[i - 1]!;
          const below = g.rails[i]!;
          expect(
            above.box.y + above.box.h,
            `${above.tier} overlaps ${below.tier}`,
          ).toBeLessThanOrEqual(below.box.y);
        }
      });

      it("sits in the left gutter at one x and one width, never crossing it", () => {
        for (const rail of g.rails) {
          expect(rail.box.x, rail.tier).toBe(CANVAS_PAD);
          expect(rail.box.w, rail.tier).toBe(RAIL_W - RAIL_GAP_X);
          expect(rail.box.x + rail.box.w, rail.tier).toBeLessThanOrEqual(CANVAS_PAD + RAIL_W);
        }
      });
    });
  }

  it("the committed rails match what `layout0` produces today", () => {
    // The two goldens are a function of the SAME fixture the layout reads, so
    // a drift between artefact and function is a regeneration that was never
    // run -- not a coordinate anyone reviewed.
    const computed = layout0(buildGraph(trimmedExport, trimmedCast));
    expect(golden("level0").rails).toEqual(computed.rails);
  });
});

describe("the gutter is exactly RAIL_W -- the +128 the goldens moved by", () => {
  it("the leftmost drawn element sits exactly at the content origin", () => {
    // The audit claim is a UNIFORM (+128, 0) shift of every tile. The
    // structural invariant behind it: the gutter is neither wider nor
    // narrower than the constant that names it, and nothing draws inside it.
    const l0 = layout0(buildGraph(trimmedExport, trimmedCast));
    const xs = [
      l0.internet.x,
      ...(l0.wanEdge ? [l0.wanEdge.x] : []),
      ...(l0.unplaced ? [l0.unplaced.box.x] : []),
      ...l0.infraDevices.map((d) => d.box.x),
      ...l0.sites.map((s) => s.box.x),
      ...l0.tiles.map((t) => t.box.x),
    ];
    expect(Math.min(...xs)).toBe(CANVAS_PAD + RAIL_W);
  });

  it("the golden's own width leaves the content area right of the gutter", () => {
    const g = golden("level0");
    expect(g.width).toBeGreaterThan(CANVAS_PAD + RAIL_W);
  });
});

describe("level 1 and the matrix keep the fit-to-pane CSS rule", () => {
  const css = readFileSync(
    fileURLToPath(new URL("../src/theme/shell.css", import.meta.url)),
    "utf8",
  );

  it("`svg.level1, svg.matrix` still fits to the pane", () => {
    const shared = /svg\.level1,\s*svg\.matrix\s*\{([^}]*)\}/.exec(css);
    expect(shared, "the `svg.level1, svg.matrix` rule is gone").not.toBeNull();
    expect(shared![1]).toMatch(/max-width:\s*100%/);
    expect(shared![1]).toMatch(/height:\s*auto/);
  });

  it("`svg.level0` no longer does -- that is the whole width slice", () => {
    const level0 = /svg\.level0\s*\{([^}]*)\}/.exec(css);
    expect(level0, "the `svg.level0` rule is missing").not.toBeNull();
    expect(level0![1]).not.toMatch(/max-width/);
    expect(level0![1]).not.toMatch(/height:\s*auto/);
  });

  it("level 0 is not quietly still in the shared selector", () => {
    expect(css).not.toMatch(/svg\.level0\s*,\s*svg\.level1/);
    expect(css).not.toMatch(/svg\.level0\s*,\s*svg\.matrix/);
  });
});
