/**
 * Where a segment the edit store ADDS is drawn on level 0, and where a clone is
 * first proposed to go (`docs/WORK_ORDER_console_canvas.md` §3 S5.1.b).
 *
 * **A placed added segment is a tile at its PIN, and nothing else moves.** The
 * pure `layout0` lays out the OBSERVED topology and its goldens stay exactly
 * that; an added segment takes no grid slot, joins no column's budget and
 * reflows nothing. It has a position only when it has been PLACED -- an S4 pin
 * on `segment:<cidr>` (a clone's drop position is committed as one), or, for
 * this session, the placement a clone was staged with when the pin's `PUT` has
 * not landed -- and the tile is drawn at that absolute top-left, the S4 rule for
 * any pin (`15` §4). A segment added with no placement (the site `+`, a
 * generated segment) is not drawn here: that is S5.1.d's.
 *
 * **A placed tile in a COLLAPSED group is held, not drawn** -- R6's rule for a
 * pin on a tile its group's budget hid (`docs/WORK_ORDER_console_level0_
 * rendering.md` §14.1): the pin is kept and the tile appears when `?expand=`
 * opens the group. Its group is the one `groupIntoSites` forms for it, asked of
 * the same function over the observed segments plus every added one -- the rule
 * the outline lists added segments by -- so the map, the outline and the note
 * that discloses the held tile cannot disagree.
 *
 * Pure -- no React, no DOM. The view draws what this returns (LES-304: position
 * is the layout's, appearance the view's).
 */
import { siteColsFor } from "./columns";
import { CANVAS_PAD, TILE_GAP_X, TILE_GAP_Y, TILE_H, TILE_W, type Box, type Point } from "./geometry";
import { columnSlot, type Level0Layout, type SitePlacement } from "./level0";
import type { Pins } from "./pins";
import { groupIntoSites } from "./sites";

export interface AddedTileInput {
  cidr: string;
  /** This session's placement, used only when no pin is on file for it. */
  fallback: Point | null;
}

export interface AddedTilePlacement {
  cidr: string;
  box: Box;
  /** The group it belongs to, or `null` when it would form a group the map does
   *  not draw (a new prefix). */
  siteKey: string | null;
  /**
   * Where the position came from: a saved pin, this session's placement, or --
   * S5.1.d -- APPENDED to its site column because the segment has no placement
   * at all. An appended tile is the one the operator has not put anywhere; it
   * is still hatched, still selectable, and moving it makes it a pin like any
   * other.
   */
  from: "pin" | "session" | "appended";
  /**
   * What this tile's box OVERLAPS, in the map's own words -- an observed
   * segment's CIDR, or a group's `+N more`. Overlap is allowed by design (an S4
   * pin is absolute and nothing reflows around it), but an added tile drawn
   * over a drawn one takes its box AND its pointer events, so the covered tile
   * silently stops responding. Reported so the view can NAME it (review N2)
   * rather than leave the operator to discover it by clicking.
   */
  covers: string[];
}

export interface HeldAddedTile {
  cidr: string;
  siteKey: string;
  /** The group's label as the map's header spells it. */
  siteLabel: string;
}

/** An added segment the map cannot place at all (S5.1.d): it belongs to a group
 *  this map draws no column for -- a brand-new two-octet prefix, or one it
 *  pulls a singleton into. Reported so the budget note can say so; a placement
 *  (a drag, or a clone's drop) gives it a position like any other. */
export interface UnplaceableAddedTile {
  cidr: string;
  reason: string;
}

export interface AddedTiles {
  drawn: AddedTilePlacement[];
  held: HeldAddedTile[];
  /** Added segments with no placement AND no column to append to (S5.1.d). */
  unplaceable: UnplaceableAddedTile[];
}

/** The pin key a segment's tile is pinned under -- the same one S4 uses. */
export function segmentPinKey(cidr: string): string {
  return `segment:${cidr}`;
}

/**
 * Resolve each placed added segment to a drawn box or a held entry. `l0` is the
 * layout the map draws (pins and budgets applied); `pins` the effective pin set.
 */
export function placeAddedTiles(
  l0: Level0Layout,
  observedCidrs: readonly string[],
  added: readonly AddedTileInput[],
  pins: Pins,
): AddedTiles {
  const NONE = { bytes: 0, flows: 0 };
  // Each network ONCE: a bucket's size decides whether it is a site or folds
  // into misc, so a CIDR counted twice (an add of an observed segment, a draft
  // aimed at one) would re-group its neighbours.
  const members = [...new Set([...observedCidrs, ...added.map((a) => a.cidr)].map((c) => c.trim()))];
  const sites = groupIntoSites(members.map((c) => ({ observedCidr: c, northSouth: NONE })));
  const drawnSites = new Map(l0.sites.map((sp) => [sp.site.key, sp]));
  const drawn: AddedTilePlacement[] = [];
  const held: HeldAddedTile[] = [];
  const unplaceable: UnplaceableAddedTile[] = [];
  // How many tiles each column has already had APPENDED, so the second unplaced
  // segment of a group does not land on the first.
  const appendedIn = new Map<string, number>();
  // How many have gone to the PARKING LANE, across every column, so two
  // escapees from different groups never stack on each other.
  let parked = 0;
  for (const a of added) {
    const pinned = pins[segmentPinKey(a.cidr)];
    const at = pinned ?? a.fallback;
    const home = sites.find((s) => s.segments.some((m) => m.observedCidr === a.cidr)) ?? null;
    const drawnHome = home ? (drawnSites.get(home.key) ?? null) : null;
    const collapsed = drawnHome
      ? l0.overflow.some((more) => more.siteKey === drawnHome.site.key && more.hidden.length > 0)
      : false;
    if (collapsed && drawnHome) {
      held.push({
        cidr: a.cidr,
        siteKey: drawnHome.site.key,
        siteLabel: drawnHome.site.isMisc ? "misc" : drawnHome.site.label,
      });
      continue;
    }
    let box: Box;
    let from: AddedTilePlacement["from"];
    if (at) {
      box = { x: at.x, y: at.y, w: TILE_W, h: TILE_H };
      from = pinned ? "pin" : "session";
    } else {
      // S5.1.d: NO placement. Append it to its own site column, after the tiles
      // the column already shows -- through the same slot rule a clone's
      // proposal uses (`slotAfterColumn`), so an unplaced tile and a proposed
      // one can never disagree about where "after this column" is (LES-025).
      if (drawnHome === null) {
        unplaceable.push({
          cidr: a.cidr,
          reason:
            "it belongs to a group this map draws no column for, so there is nowhere to append it — " +
            "place it on the map to draw it",
        });
        continue;
      }
      const n = appendedIn.get(drawnHome.site.key) ?? 0;
      appendedIn.set(drawnHome.site.key, n + 1);
      const slot = appendSlot(l0, drawnHome, n, parked);
      if (slot.parked) parked += 1;
      box = { x: slot.at.x, y: slot.at.y, w: TILE_W, h: TILE_H };
      from = "appended";
    }
    drawn.push({
      cidr: a.cidr,
      box,
      siteKey: drawnHome ? drawnHome.site.key : null,
      from,
      covers: coveredBy(l0, box, drawnHome ? drawnHome.site.key : null),
    });
  }
  return { drawn, held, unplaceable };
}

/**
 * The `extra`-th free grid slot of `column`, after everything the column draws
 * -- its shown tiles and its `+N more` box -- or `null` when that slot would
 * fall outside the column's own box.
 *
 * The ONE place "after this column" is computed, so an appended unplaced tile
 * (S5.1.d) and a clone's proposed placement (`proposedClonePlacement`) cannot
 * disagree about it. What each does when the slot does NOT fit is their own
 * business, and deliberately different -- see both callers.
 */
export function slotAfterColumn(l0: Level0Layout, column: SitePlacement, extra: number): Point | null {
  const shown = l0.tiles.filter((t) => t.siteKey === column.site.key).length;
  const hasMore = l0.overflow.some((more) => more.siteKey === column.site.key);
  const cols = siteColsFor(shown);
  const slot = columnSlot(column.header, cols, shown + (hasMore ? 1 : 0) + extra);
  if (slot.y + TILE_H > column.box.y + column.box.h) return null;
  return { x: slot.x, y: slot.y };
}

/** The first tile row of a column -- where both fallbacks below start from. */
function firstRowY(l0: Level0Layout, column: SitePlacement): number {
  const shown = l0.tiles.filter((t) => t.siteKey === column.site.key).length;
  return columnSlot(column.header, siteColsFor(shown), 0).y;
}

/**
 * Where an UNPLACED added segment is drawn (S5.1.d): the next free slot of its
 * own column, or -- when the column has no room left -- a parking lane clear of
 * the whole grid.
 *
 * **An appended tile is DRAWN until the operator places it, so it must cover
 * nothing.** That is what separates it from a clone's *proposal*, which is
 * transient and about to be moved: the proposal's fallback goes beside its
 * column and accepts an overlap the tile then discloses, while this one may
 * not. Neither may drop below the group box, where the arcs anchored on that
 * group fall (LES-331).
 *
 * The lane sits to the right of EVERY box the segment tier draws -- and "every"
 * is the whole of the rule (review M1, LES-331 a second time in this module).
 * The first cut took it from `l0.sites` alone, and `layout0` also appends the
 * **O7 unplaced tile as a final single-tile column** outside any site: the
 * parked tile landed on it with 126 units of overlap on both real bundles, took
 * its pointer events, and `covers` said nothing because it scanned a different
 * set. `drawnBoxes` below is now the ONE set both the lane and the overlap
 * report read, so a lane that is not clear is at least named.
 *
 * None of this touches the pure layout: the column keeps its box, the R6 budget
 * is untouched and no drawn tile moves. The view grows its viewBox to contain
 * what this returns (LES-268).
 */
function appendSlot(
  l0: Level0Layout,
  column: SitePlacement,
  extra: number,
  parked: number,
): { at: Point; parked: boolean } {
  const slot = slotAfterColumn(l0, column, extra);
  if (slot !== null) return { at: slot, parked: false };
  return {
    at: { x: laneX(l0), y: firstRowY(l0, column) + parked * (TILE_H + TILE_GAP_Y) },
    parked: true,
  };
}

/**
 * Every box the plane draws in the segment tier and above, with the name the
 * map gives it.
 *
 * ONE function, two callers -- the parking lane's right edge and `coveredBy`'s
 * overlap report -- because a lane computed from one set and checked against
 * another is exactly how the O7 tile got covered silently. Site COLUMNS are
 * included as whole boxes (their header and padding are as clickable as their
 * tiles), and the individual tiles are listed too so an overlap can be named
 * precisely.
 */
function drawnBoxes(l0: Level0Layout): Array<{ box: Box; name: string; siteKey?: string }> {
  const out: Array<{ box: Box; name: string; siteKey?: string }> = [];
  for (const sp of l0.sites) out.push({ box: sp.box, name: `the ${sp.site.label} group`, siteKey: sp.site.key });
  for (const t of l0.tiles) out.push({ box: t.box, name: t.segment.observedCidr });
  for (const more of l0.overflow) {
    out.push({ box: more.box, name: `the ${more.hidden.length} collapsed segments of ${more.siteKey}` });
  }
  if (l0.unplaced) out.push({ box: l0.unplaced.box, name: `the ${l0.unplaced.count} unplaced entities` });
  for (const d of l0.infraDevices) out.push({ box: d.box, name: d.node.id });
  return out;
}

/** The parking lane's x: clear of every box above, by a tile gap. */
function laneX(l0: Level0Layout): number {
  const right = drawnBoxes(l0).reduce((max, b) => Math.max(max, b.box.x + b.box.w), 0);
  return right + TILE_GAP_X;
}

/** Whether two boxes share any area. Touching edges are not an overlap: a tile
 *  in the next grid slot abuts its neighbour exactly. */
function overlaps(a: Box, b: Box): boolean {
  return a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
}

/**
 * Every drawn thing `box` covers, named as the map names it (`covers` above).
 *
 * **You do not cover the box you are INSIDE.** An appended tile sits in its own
 * group's column by design, and reporting "covers the 10.20 group" for that
 * would be noise that buries the overlaps that matter. `home` is the tile's own
 * site key, or `null` for a tile that belongs to no drawn group; every OTHER
 * group's box still reports, because a tile dropped across a group it does not
 * belong to does take that group's header and its `+` control.
 */
function coveredBy(l0: Level0Layout, box: Box, home: string | null): string[] {
  return drawnBoxes(l0)
    .filter((b) => (b.siteKey === undefined || b.siteKey !== home) && overlaps(box, b.box))
    .map((b) => b.name);
}

/**
 * Where a clone is PROPOSED to go before the operator chooses: the grid slot
 * straight after the last box of its source's column -- where a new tile in that
 * group would sit, so the proposal starts beside what it copies. Asked of the
 * one slot rule `layout0` places columns by (`columnSlot`), over the column as
 * drawn: a pinned site's moved header moves the proposal with it.
 *
 * A source the map draws no column for (it cannot happen for an observed
 * segment, but a total function says what it does) is proposed at the canvas's
 * right edge.
 *
 * **The proposal never leaves the COLUMN it is proposed in** (review N1). A slot
 * whose tile would fall past the group box's bottom edge is moved to the RIGHT
 * of the column instead, on its first tile row.
 *
 * The test is the column's own box, NOT the east-west rail -- the distinction
 * is the whole of this rule, and getting it wrong is how the defect survived a
 * first fix. Below a group box is the corridor the arcs anchored on that group
 * drop through, and it starts at the box's bottom edge, well above the rail's
 * top. Measured through the live seam (`level0OptionsFor(NO_FILTERS)`) on
 * `corpapcn-twin`: five of six columns carry a `+N more` tile, which reserves a
 * row, so their box bottom is 680 and the proposal (572..668) sits inside them,
 * crossing nothing. `site:misc` has exactly six segments and therefore NO `+N
 * more`, so its box bottom is 568 -- the same proposal falls 100 units outside
 * its own column and **six asserted arcs cross it**. A rail test (668 < 706)
 * calls that clear.
 *
 * **A pinned column keeps its proposal.** The rule reads the box wherever the
 * box is, so a site header dragged into the band takes the proposal with it
 * rather than sending it somewhere far from the segment it copies. Sitting
 * beside a column the operator themselves put there is not the defect this
 * guards.
 *
 * **The escape can overlap the next column**, and does on a narrow plane where
 * the source is not the last group. Accepted: this is a PROPOSAL the placement
 * step exists to move, and once placed the tile names whatever it covers
 * (`AddedTilePlacement.covers`), so the overlap is disclosed rather than silent.
 */
export function proposedClonePlacement(l0: Level0Layout, sourceCidr: string): Point {
  const column = l0.sites.find((sp) => sp.site.segments.some((m) => m.observedCidr === sourceCidr)) ?? null;
  if (column === null) return { x: l0.width, y: CANVAS_PAD };
  const slot = slotAfterColumn(l0, column, 0);
  if (slot !== null) return slot;
  // Beside the column, and never past the plane's own right edge onto the O7
  // unplaced tile (review M1): for the LAST column those two coincide, so the
  // proposal took the same coordinates the parking lane used to.
  const beside = column.box.x + column.box.w + TILE_GAP_X;
  return { x: Math.max(beside, laneX(l0)), y: firstRowY(l0, column) };
}
