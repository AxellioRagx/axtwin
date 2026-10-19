/**
 * The single source of truth for what the layout goldens contain
 * (`docs/WORK_ORDER_console_canvas.md` §5). Both the writer
 * (`npm run layout:golden`, gated on `WRITE_GOLDEN`) and the comparer
 * (`layout.golden.test.ts`, on every `npm run test`) import THIS, so the two
 * can never describe different layouts.
 *
 * Every value is produced from the checked-in trimmed fixture by the pure
 * layout functions, so a golden is a deterministic function of identity and a
 * diff is a deliberate, reviewed act.
 */
import { buildGraph } from "../src/model/graph";
import { layout0 } from "../src/layout/level0";
import { layout1 } from "../src/layout/level1";
import { matrix0, matrixSegment } from "../src/layout/matrix";
import {
  trimmedCast,
  trimmedExport,
  withColumnAddingSegment,
  withOverBudgetSite,
} from "./fixtures/trimmed";

export function buildGoldens(): Record<string, unknown> {
  const graph = buildGraph(trimmedExport, trimmedCast);
  const overBudget = buildGraph(withOverBudgetSite(), trimmedCast);
  return {
    level0: layout0(graph),
    // R6's per-group budget, BOTH orders, pinned as coordinates (§3 R6: "the
    // golden documents both orders"). The shipped fixture's groups all fit
    // inside `SITE_BUDGET`, so without these two the collapse, the `+N more`
    // tile and the ranking would be drawn by code no golden covers.
    "level0-collapsed": layout0(overBudget),
    "level0-collapsed-cidr": layout0(overBudget, {}, { order: "cidr" }),
    // The column-COUNT-changing baseline (§5.2, corrected): a new `10.99` site
    // column reflows the misc column, the unplaced tile, the width and the
    // centred chrome. This golden pins that reflow as the reviewed reference
    // S4's pins commit against -- it is a baseline, not a locality claim.
    "level0-added-column": layout0(buildGraph(withColumnAddingSegment(), trimmedCast)),
    // The multi-role subject (mirrors the real 10.80.20.0/24).
    "level1-10.80.20.0-24": layout1(graph, "10.80.20.0/24"),
    // The LES-185 27-host band: a below-threshold band that collapses by rung.
    "level1-10.80.72.0-24": layout1(graph, "10.80.72.0/24"),
    // The 18-host live corp-workstation band: a role band that collapses.
    "level1-10.20.193.0-24": layout1(graph, "10.20.193.0/24"),
    matrix0: matrix0(graph),
    "matrix-seg-10.80.72.0-24": matrixSegment(graph, "10.80.72.0/24"),
  };
}

/** The stable on-disk form: 2-space JSON with a trailing newline, so a golden
 *  diff reads cleanly in review. */
export function goldenText(value: unknown): string {
  return `${JSON.stringify(value, null, 2)}\n`;
}
