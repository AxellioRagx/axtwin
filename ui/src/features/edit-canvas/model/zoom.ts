// Lifted (the model half only: no path/route grammar) from the frozen `o2/console/src/model/zoom.ts` (2026-10-30);
// pure, no React, no DOM. Import paths adapted; nothing imports `o2/console` at runtime.
/**
 * **The URL is the zoom** (`docs/design/15-console-canvas.md` §7): the route
 * names the level, the query names the filters, the breadcrumb IS the route,
 * Escape goes up a level and the back button works because none of it is
 * hidden in a store.
 *
 * Pure and React-free on purpose -- `routes.tsx` is the only file that knows
 * about a router, and this one is what the tests round-trip.
 *
 * Two mechanical spellings, both taken in `docs/WORK_ORDER_console_canvas.md`
 * §3 S2 because `15` §3/§7's notation is not directly expressible in a URL:
 *
 *   - **a CIDR's prefix length is written with a hyphen**
 *     (`/segments/10.80.20.0-24`, never `%2F`), and
 *   - **`?occupancy<0.8` is spelled `?occupancy_lt=0.8`**.
 *
 * A host may be addressed with or without its segment. `/design/<id>/hosts/
 * <key>` is the flat form (an entity key is unique within an imprint, so the
 * segment is redundant); `/design/<id>/segments/<cidr>/hosts/<key>` is the
 * nested form `15` §3's table gives, and it is what the canvas emits when the
 * segment is known, because it is what makes Escape land on the segment
 * instead of the network. Both parse; the nested one is preserved on the way
 * back out.
 */
import type { EvidenceClass } from "./evidence";

export type Zoom =
  /** `/design` -- the imprint catalog; no imprint is open. */
  | { level: "catalog" }
  /** `/design/<id>` -- level 0, the tiered network diagram. */
  | { level: "network"; imprint: string }
  /** `/design/<id>/segments/<cidr>` -- level 1, the spine drill. */
  | { level: "segment"; imprint: string; cidr: string }
  /** `/design/<id>[/segments/<cidr>]/hosts/<key>` -- level 2, the facts table. */
  | { level: "host"; imprint: string; key: string; cidr?: string }
  /** `/design/<id>/matrix` -- the adjacency of the current scope (v1: level 0). */
  | { level: "matrix"; imprint: string; cidr?: string };

export type ZoomLevel = Zoom["level"];

/** Where the Design area (the assertions canvas) is rooted in the one app. */
export const DESIGN_SEGMENT = "design";
export const DESIGN_ROOT = `/${DESIGN_SEGMENT}`;

/**
 * How a site group's tiles are ORDERED, and therefore which of them the
 * per-group budget shows (R6, `layout/columns.ts`).
 *
 *   - `hosts` — placed-host count desc, then CIDR. The v1 default: the budget
 *     shows the six segments that hold the most of the twin.
 *   - `cidr`  — the address order the map used before R6, kept as `?order=cidr`
 *     because a *ranking* answers "which segments matter" while an *address
 *     order* answers "where is 10.20.195.0/24", and both are real questions.
 *
 * Declared here, with the rest of the URL vocabulary, so `model/` does not have
 * to import from `layout/` to name a value the query carries.
 */
export const TILE_ORDERS = ["hosts", "cidr"] as const;
export type TileOrder = (typeof TILE_ORDERS)[number];

export function isTileOrder(value: string): value is TileOrder {
  return (TILE_ORDERS as readonly string[]).includes(value);
}

export interface Filters {
  /** `?ghosts=1` -- show entities the deploy gate or an assertion removed. */
  ghosts: boolean;
  /** `?class=imputed` -- one of the six; an unknown value is a `problem`. */
  evidenceClass: EvidenceClass | null;
  /** `?occupancy_lt=0.8` -- the `?occupancy<0.8` of the design note. */
  occupancyLt: number | null;
  /**
   * `?changed=1` -- the post-apply "changes" filter (`15` §12 O5). Turned ON
   * by an apply that changed the bundle; it keeps the changed set REACHABLE
   * after the five-second highlight fades. Unlike the other three, the set it
   * emphasizes is NOT in the URL -- it is the last apply's diff, held in the
   * workspace -- so this is only the flag; the workspace threads the set.
   */
  changed: boolean;
  /**
   * `?summary=1` -- the cast-summary bar above the drawing is EXPANDED (R1).
   * Collapsed (the default) the bar is one line; expanded it shows the cast
   * numbers and the evidence mix.
   *
   * Like `changed`, this is a VIEW flag rather than a subset filter: it
   * emphasizes nothing and `anyFilterActive` deliberately ignores it, so
   * opening the bar never dims a tile. It lives here, in the one URL grammar,
   * because the work order requires the state to survive a reload and NOT to
   * live in a store -- and because `routeFor(zoom, filters)` is the single
   * path every navigation already threads, so the bar stays open across a
   * drill, the breadcrumb and Escape without a second mechanism.
   */
  summary: boolean;
  /**
   * `?ew=all` -- the east-west band shows EVERY observed segment pair, not the
   * top-K by volume (R4, `layout/arcs.ts`; D2's v1 budget is 12).
   *
   * A VIEW flag like `summary`: it dims nothing and emphasizes nothing, it
   * changes what the map DRAWS. In the URL for the same two reasons the
   * summary bar is -- it must survive a reload and a pasted link, and
   * `routeFor(zoom, filters)` is the one path every navigation already
   * threads.
   *
   * Spelled `all` rather than `1` because the work order spells it that way,
   * and because the budget is a number: `?ew=all` reads as the opposite of
   * "the busiest twelve", where `?ew=1` would read as a count.
   *
   * **Widths move when this is toggled** and that is not a data change: the
   * stroke scale is normalised over the DRAWN set's own range (§5 D2,
   * `canvas/pairs.ts`), so the same pair is wider among 12 arcs than among 40.
   */
  eastWestAll: boolean;
  /**
   * `?order=cidr` -- the order a site group's tiles are RANKED in, which is
   * also which six the per-group budget shows (R6, `layout/columns.ts`).
   *
   * A VIEW flag like `summary` and `ew`: it dims nothing, it changes what the
   * map draws. The default (`hosts`) is not emitted in the canonical query.
   */
  tileOrder: TileOrder;
  /**
   * `?expand=site:10.80` -- site groups whose per-group budget is lifted, so
   * the group draws every segment it holds instead of its busiest six (R6).
   *
   * **Repeatable**, and the only key in this grammar that is: it is a SET, and
   * `?expand=site:10.80&expand=site:10.20` is the natural spelling of one. Every
   * other key takes one value and reports the rest (`readOne`).
   *
   * In the URL rather than a store for the reasons every view flag is: it must
   * survive a reload and a pasted link, and `routeFor(zoom, filters)` is the
   * one path every navigation already threads.
   */
  expand: readonly string[];
  /**
   * Every query value that was NOT understood, with the reason. A filter UI
   * that silently drops a typo leaves an operator reading a filtered view
   * they did not ask for (LES-235: publish what you skipped and why).
   */
  problems: string[];
}

export const NO_FILTERS: Readonly<Filters> = Object.freeze({
  ghosts: false,
  evidenceClass: null,
  occupancyLt: null,
  changed: false,
  summary: false,
  eastWestAll: false,
  tileOrder: "hosts" as TileOrder,
  expand: Object.freeze([]) as readonly string[],
  problems: [],
});

/**
 * **The filter taxonomy, stated once (S2).** `Filters` carries three kinds of
 * field, and they behave differently:
 *
 *   - **dimming filters** -- `ghosts` / `class` / `occupancy_lt`: they
 *     emphasize a subset, and `anyDimmingFilter` is what turns the dimming
 *     pass on (`model/graph.ts` delegates to it);
 *   - the **emphasis flag** `changed`: marks the post-apply set without
 *     dimming anything, but IS dropped by the clear control;
 *   - **view flags** -- `summary`, `eastWestAll`, and R6's `tileOrder` /
 *     `expand`: what the page shows of itself, dropped by nothing.
 *     (`eastWestAll` and `expand` change which elements are drawn, which is a
 *     BUDGET, not a filter: a filter emphasizes a subset of the same drawing,
 *     and everything a budget lifts is disclosed on screen and present in the
 *     outline, the matrix and search either way.)
 *
 * Restating that split at each use site (the clear target, the clear chip's
 * visibility, the dimming predicate) is precisely how a later flag joins the
 * wrong set by being forgotten at one of them.
 */
export function anyDimmingFilter(filters: Filters): boolean {
  return filters.ghosts || filters.evidenceClass !== null || filters.occupancyLt !== null;
}

/** The filter set `clear` produces: every dimming filter and the emphasis flag
 *  off, every VIEW flag preserved. `problems` describe the input rather than
 *  the state, so they are not carried. */
export function clearFilters(filters: Filters): Filters {
  return {
    ...filters,
    ghosts: false,
    evidenceClass: null,
    occupancyLt: null,
    changed: false,
    problems: [],
  };
}
