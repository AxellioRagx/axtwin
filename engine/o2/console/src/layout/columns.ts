/**
 * PER-GROUP TILE BUDGETS and the column grid (R6, `docs/WORK_ORDER_console_
 * level0_rendering.md` §3 R6) — which of a site group's segments the map draws,
 * in what order, and how wide the column that holds them is.
 *
 * Before R6 a site column was a fixed three tiles across and as tall as the
 * group was long, so the 19-segment `10.80` group on the pit bundle was **seven
 * tile rows** and the plane ran 1,287 units deep — off the bottom of any window
 * (§2 F5). The three rules here, together, are the fix:
 *
 *  - **RANKING.** A group's tiles are ordered by **placed-host count desc, then
 *    CIDR** (`?order=cidr` keeps the address order). The budget below then shows
 *    the six segments holding the most of the twin, rather than the six with the
 *    lowest addresses.
 *  - **BUDGET.** At most `SITE_BUDGET` (6) tiles per group; the rest collapse
 *    into one `+N more` tile at the end of the group, and `?expand=<site key>`
 *    lifts it. Collapsing is a projection of the MAP — never a filter: a
 *    collapsed segment keeps its outline row, its matrix axis, its search hit
 *    and its drill-down.
 *  - **WIDTH.** The column is `siteColsFor(shown)` tiles across — **2 for four
 *    or fewer, 3 otherwise** — so a small group no longer reserves a full
 *    three-wide column it cannot fill.
 *
 * **Collapsing and expanding never change a column's WIDTH**, and that is a
 * property rather than a coincidence: a group only collapses when it holds more
 * than `SITE_BUDGET` segments, and both `SITE_BUDGET` and anything larger map to
 * the same `SITE_COLS`. So the six shown tiles sit at exactly the same
 * coordinates before and after an expand — §3 R6's locality clause — and the
 * only thing that grows is the column's height. `siteColsForIsStableAcross
 * Budget()` states it as an assertion the tests pin rather than a comment.
 *
 * Pure, in the idiom R2's `trunk.ts`, R3's `infra.ts` and R4's `arcs.ts`
 * established: segments and an operator's two budget choices in, a decision out.
 * It draws nothing and knows nothing about React — which is what lets
 * `layout0` and `applyPins` both call it and get the same answer (LES-025).
 *
 * **What this deliberately does NOT do: vary a tile's SIZE.** §2 F5 lists "every
 * tile the same size" among the symptoms, and §12.6 named a varying size as
 * R6's; the slice's own scope line (§3 R6) does not, and this build does not
 * either (an implementation call, open to veto). Magnitude is carried by the
 * byte figure on the tile, by the trunk leg's width and by the arc's; a tile
 * whose size followed its volume would move every other tile in its column
 * whenever a host appeared, which is exactly the column-preserving locality
 * `layout/level0.ts` is built to hold.
 */
import type { GraphSegment } from "../model/graph";
import { SITE_BUDGET, siteCollapses } from "../model/budgets";
import { compareCidr } from "../model/keys";
import type { TileOrder } from "../model/zoom";
import {
  SITE_COLS,
  SITE_HEADER_H,
  SITE_PAD,
  TILE_GAP_X,
  TILE_GAP_Y,
  TILE_H,
  TILE_W,
} from "./geometry";

/** A group of this many SHOWN tiles or fewer is laid out two across. */
export const SITE_COLS_NARROW = 2;
export const SITE_COLS_NARROW_MAX = 4;

/**
 * How many tiles across a group's column is, from the number it SHOWS (never
 * from the number it holds — an expand must not re-width the column).
 */
export function siteColsFor(shown: number): number {
  return shown <= SITE_COLS_NARROW_MAX ? SITE_COLS_NARROW : SITE_COLS;
}

/**
 * The property the locality clause rests on: a column that collapses is always
 * at least `SITE_BUDGET` wide-by-rule, and so is the same column expanded.
 * Exported so a test asserts it over the whole reachable range rather than a
 * comment claiming it (LES-306: compute the claim from the constants).
 */
export function siteColsIsStableAcrossBudget(total: number): boolean {
  if (!siteCollapses(total)) return true;
  return siteColsFor(SITE_BUDGET) === siteColsFor(total);
}

/** A column `cols` tiles across, including its padding. */
export function columnWidthFor(cols: number): number {
  const n = Math.max(1, cols);
  return n * TILE_W + (n - 1) * TILE_GAP_X + 2 * SITE_PAD;
}

/** A column holding `boxes` tile-sized boxes at `cols` across (the `+N more`
 *  tile is one of them — it takes a grid slot like any other). */
export function columnHeightFor(boxes: number, cols: number): number {
  const rows = Math.max(1, Math.ceil(Math.max(0, boxes) / Math.max(1, cols)));
  return SITE_HEADER_H + rows * TILE_H + (rows - 1) * TILE_GAP_Y + 2 * SITE_PAD;
}

/**
 * A group's segments in drawing order.
 *
 * `hosts` (the default) is **placed-host count desc, then CIDR**; `cidr` is the
 * address order. Both are total and deterministic — the CIDR tie-break is what
 * makes the ranking one, and it fires on real content: the pit bundle's `10.80`
 * group holds three segments with four hosts each, and `10.80.34.0/24` is drawn
 * while `10.80.65.0/24` and `10.80.73.0/24` are not.
 *
 * **A measured consequence, recorded rather than smoothed over:** a host count
 * is not a volume. `10.20.195.0/24` is the pit bundle's largest egress talker
 * (1.77 GB north-south, §2) and holds **one** placed host, so the default
 * ranking collapses it. Its bytes are still in its group's trunk leg (a leg sums
 * the whole group, collapsed members included), its tile is one `?expand=` away,
 * and `?order=cidr` — where it sits third in `10.20` — draws it. The alternative,
 * ranking by bytes, would hide the segments that hold the twin's hosts, which is
 * what a level-0 tile is mostly for.
 */
export function orderSegments(
  segments: readonly GraphSegment[],
  order: TileOrder,
): GraphSegment[] {
  const byCidr = (a: GraphSegment, b: GraphSegment): number =>
    compareCidr(a.observedCidr, b.observedCidr);
  const copy = segments.slice();
  if (order === "cidr") return copy.sort(byCidr);
  return copy.sort((a, b) => (b.hostCount !== a.hostCount ? b.hostCount - a.hostCount : byCidr(a, b)));
}

export interface ColumnBudget {
  /** The segments this column DRAWS a tile for, in drawing order. */
  shown: GraphSegment[];
  /** The segments it does not, in the same order. `[]` when the group is under
   *  budget **or** expanded — the two are different situations and the
   *  `overflow` flag below is what tells them apart. */
  hidden: GraphSegment[];
  /** Whether this group carries an overflow box at all: true exactly when the
   *  group holds more than `SITE_BUDGET` segments, collapsed or expanded. An
   *  expanded group keeps the box as the way back. */
  overflow: boolean;
  expanded: boolean;
  cols: number;
  width: number;
  /** Tile-sized boxes in the grid: the shown tiles plus the overflow box. */
  boxes: number;
}

/**
 * Rank, budget and size one site group's column.
 *
 * `expanded` is the operator's `?expand=<site key>` for THIS group — resolved by
 * the caller, so this stays a function of one column rather than of the URL.
 */
export function budgetColumn(
  segments: readonly GraphSegment[],
  options: { order: TileOrder; expanded: boolean },
): ColumnBudget {
  const ordered = orderSegments(segments, options.order);
  const overflow = siteCollapses(ordered.length);
  const expanded = overflow && options.expanded;
  const shown = overflow && !expanded ? ordered.slice(0, SITE_BUDGET) : ordered;
  const hidden = overflow && !expanded ? ordered.slice(SITE_BUDGET) : [];
  const cols = siteColsFor(shown.length);
  return {
    shown,
    hidden,
    overflow,
    expanded,
    cols,
    width: columnWidthFor(cols),
    boxes: shown.length + (overflow ? 1 : 0),
  };
}
