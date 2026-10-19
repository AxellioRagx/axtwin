/**
 * Per-level element budgets (`docs/design/15-console-canvas.md` §3, §11) and
 * the level-1 collapse rule (O6, `docs/WORK_ORDER_console_canvas.md` §7).
 *
 * The budgets are the whole reason the Console scales where the plugin's
 * single hairball did not: an aggregate that would exceed its budget collapses
 * into a summary rather than the frame rate. They are stated here, once, as
 * numbers a test asserts against the real bundle -- "no view exceeds its
 * budget" is the acceptance line, and a budget nobody checks is a hope.
 */

/** ≈ 40 boxes at level 0 (§11). Counted as TILES + infrastructure devices +
 *  the fixed chrome (Internet pill, WAN edge) + the one *unplaced* tile (O7).
 *  CorpAPCN: 32 tiles + 6 devices + 2 chrome + 1 unplaced = 41, and the budget
 *  has the small headroom §11's "about 40" implies. */
export const BUDGET_LEVEL0 = 48;

/**
 * ≤ 6 segment tiles drawn per SITE GROUP at level 0 (R6, `docs/WORK_ORDER_
 * console_level0_rendering.md` §3 R6 — an implementation call, open to veto).
 *
 * It lives beside the other budgets rather than in `layout/`, for the reason
 * this file exists: a budget stated in one place is a number a test can assert
 * against real content, and a budget restated at its use site is two budgets.
 * The *collapse* it drives is `layout/columns.ts`'s, exactly as the level-1
 * chip budget above drives `tallyByRung`.
 *
 * **Six, measured rather than chosen:** the 10.80 group holds 19 segments on
 * the pit bundle and 16 on both corpapcn casts, so that column was six tile
 * rows tall and ran off the bottom of any window. Six shown is two rows at
 * three across (three with the `+N more` tile), which puts every group's column
 * at the same, bounded height — the point of the budget is that the plane's
 * height stops being a function of the busiest group.
 *
 * **Collapsing is a projection of the MAP, never a filter** (§3 R6): a
 * collapsed segment keeps its outline row, its matrix axis, its search hit and
 * its level-1 drill — only its tile is not drawn, and the `+N more` tile says
 * how many and opens them.
 */
export const SITE_BUDGET = 6;

/** Whether a site group of `segments` collapses. The sibling of
 *  `bandCollapses` below, spelled the same way on purpose. */
export function siteCollapses(segments: number): boolean {
  return segments > SITE_BUDGET;
}

/** ≤ 6 role bands per segment (§3). A band is a role bucket present among the
 *  segment's placed hosts, plus the *below threshold* band. */
export const BUDGET_LEVEL1_BANDS = 8;

/** ≤ 15 chips in a band before it collapses (§3, O6). */
export const BUDGET_LEVEL1_BAND_CHIPS = 15;

/** ≤ 40 × 40 (§3, §11). The matrix collapses nothing; it refuses to draw an
 *  axis past this instead (a scope with more than 40 roles is a data problem,
 *  not a layout one). */
export const BUDGET_MATRIX_AXIS = 40;

// ---------------------------------------------------------------------------
// O6: the occupancy-rung collapse for an over-budget role band
// ---------------------------------------------------------------------------
/**
 * `15` §3's "one chip per group with its `count:`" is NOT computable on this
 * bundle: a cast node spec carries no member keys (`docs/WORK_ORDER_console_
 * canvas.md` §2, O6), so the map cannot say which chip is which group member.
 * v1 collapses a band over the chip budget by **occupancy rung** (`13` §3.4:
 * core ≥ 0.8 / regular 0.5–0.8 / transient < 0.5) with a count per rung,
 * expanding on click. This IS computable -- occupancy is `windows_seen /
 * windows` per node -- and it is the actionable axis: the transient rung is
 * exactly the ghosts the deploy gate drops.
 */
export const OCCUPANCY_RUNGS = ["core", "regular", "transient"] as const;
export type OccupancyRung = (typeof OCCUPANCY_RUNGS)[number];

/** `13` §3.4's thresholds. `null` occupancy is *unknown*, not transient -- it
 *  goes to its own rung so an absent measurement is never read as a low one. */
export const RUNG_CORE_MIN = 0.8;
export const RUNG_REGULAR_MIN = 0.5;

export function rungOf(occupancy: number | null): OccupancyRung | "unknown" {
  if (occupancy === null) return "unknown";
  if (occupancy >= RUNG_CORE_MIN) return "core";
  if (occupancy >= RUNG_REGULAR_MIN) return "regular";
  return "transient";
}

export interface RungTally {
  rung: OccupancyRung | "unknown";
  count: number;
}

/**
 * Tally a band's members by occupancy rung, in the fixed rung order with
 * `unknown` last, dropping empty rungs. Used only when a band exceeds
 * `BUDGET_LEVEL1_BAND_CHIPS`; below that the band draws its chips directly.
 */
export function tallyByRung(
  occupancies: ReadonlyArray<number | null>,
): RungTally[] {
  const counts = new Map<OccupancyRung | "unknown", number>();
  for (const o of occupancies) {
    const rung = rungOf(o);
    counts.set(rung, (counts.get(rung) ?? 0) + 1);
  }
  const order: Array<OccupancyRung | "unknown"> = [...OCCUPANCY_RUNGS, "unknown"];
  return order
    .filter((rung) => (counts.get(rung) ?? 0) > 0)
    .map((rung) => ({ rung, count: counts.get(rung) ?? 0 }));
}

/** Whether a band of `chips` collapses. */
export function bandCollapses(chips: number): boolean {
  return chips > BUDGET_LEVEL1_BAND_CHIPS;
}
