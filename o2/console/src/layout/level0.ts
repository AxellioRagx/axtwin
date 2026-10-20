/**
 * Level 0 -- the tiered path diagram (`docs/design/15-console-canvas.md` §4).
 *
 * `layout0(graph)` is a **pure function of identity**: same graph, same
 * coordinates, byte for byte (`15` §5.1). Locality (§5.2) holds for a
 * **column-preserving** edit -- adding/removing a host, or a tile that fits an
 * existing site's current row grid -- which leaves every other tile and all
 * the chrome byte-identical. A **column-count-changing** edit (a tile that
 * adds a row to a site, or a new/promoted `/16` that adds a column) is NOT
 * local: the site row's width changes, so the centred chrome recentres and
 * downstream columns shift. That is a deliberate consequence of ordered
 * columns, and S4 **pins** are the escape hatch for holding a layout stable
 * across such an edit (`15` §4). Both the column-preserving guarantee and the
 * column-changing baseline are golden-tested.
 *
 * The idiom, top to bottom: the **Internet** pill (standing for the non-local
 * population, O7), the **WAN edge** (the gateway's egress), the
 * **infrastructure** devices (`15` §4), the **segment tiles** grouped into
 * site columns in prefix order, and beneath them the **east-west band**
 * (`arcs.ts`). An *unplaced* tile (O7) is appended as the last column
 * so the 49 local-but-unplaced entities are counted and openable rather than
 * silently invisible.
 *
 * **Column-preserving locality comes from the column layout.** Sites are
 * columns whose WIDTH is a function of how many tiles the site SHOWS
 * (`layout/columns.ts`), so a site grows only DOWNWARD; a tile added within an
 * existing site's current row grid does not move its neighbours (they sit
 * beside it, not below it), and the chrome, anchored to the total width, does
 * not move because the width did not change.
 * That is the common edit-loop case -- add/remove a host -- and the property
 * the loop rests on (§1). What it does NOT cover is a new ROW or a new COLUMN:
 * those change the site row's extent, and holding the layout stable across
 * them is S4's pins, not this function's job.
 *
 * **Not every segment gets a tile (R6).** A site group draws its busiest
 * `SITE_BUDGET` segments and collapses the rest behind one `+N more` tile;
 * `?expand=<site key>` lifts the budget for that group. The rules live in
 * `layout/columns.ts`; what matters here is that `tiles` is what the map
 * DRAWS, `overflow` says what it does not, and everything else -- the outline,
 * the matrix, search, the drill-down -- still carries every segment. Expanding
 * a group cannot move the tiles it was already showing (`columns.ts` states
 * why), so the locality above survives the budget.
 */
import type { ConsoleGraph, GraphNode, GraphSegment } from "../model/graph";
import {
  CANVAS_PAD,
  DEVICE_GAP_X,
  DEVICE_W,
  INTERNET_W,
  RAIL_GAP_X,
  RAIL_W,
  SITE_GAP_X,
  SITE_HEADER_H,
  SITE_PAD,
  TAP_H,
  TAP_W,
  TIER_GAP_Y,
  TIER_INFRA_H,
  TIER_INTERNET_H,
  TIER_WAN_H,
  TILE_GAP_X,
  TILE_GAP_Y,
  TILE_H,
  TILE_W,
  WAN_W,
  type Box,
} from "./geometry";
import {
  infraBandHeight,
  infraBandRows,
  layoutInfra,
  type InfraDeviceInput,
  type InfraPlacement,
  type InfraStub,
} from "./infra";
import { ARC_TOP_K, layoutArcs, type Arc, type ArcAnchor } from "./arcs";
import {
  budgetColumn,
  columnHeightFor,
  columnWidthFor,
  type ColumnBudget,
} from "./columns";
import type { TileOrder } from "../model/zoom";
import { NO_PINS, pinDelta, translateBox, type Pins } from "./pins";
import { groupIntoSites, type Site } from "./sites";
import { stackBands, TIERS, type Tier } from "./tiers";
import { layoutTrunk, type Trunk } from "./trunk";

export interface TilePlacement {
  kind: "tile";
  segment: GraphSegment;
  siteKey: string;
  box: Box;
}

export interface SitePlacement {
  site: Site;
  header: Box;
  /** The whole column's bounding box (header + tiles + padding). */
  box: Box;
}

export interface DevicePlacement {
  kind: "device";
  node: GraphNode;
  box: Box;
  /**
   * The site column this device sits OVER (R3) -- `15` §4's "the infrastructure
   * devices are tied to the segment that holds them", drawn positionally rather
   * than with a line across the canvas.
   *
   * `null` when its segment has no tile: the device stays centred and gets no
   * stub, because there is no group to point at and pointing at the nearest one
   * would assert a membership the export does not support. The view draws that
   * case with a dashed outline.
   */
  siteKey: string | null;
}

export interface UnplacedPlacement {
  box: Box;
  count: number;
}

/**
 * A site group's `+N more` tile (R6) -- the one box that stands for every
 * segment the per-group budget collapsed, in the grid slot after the last tile
 * shown.
 *
 * It is drawn for a group that HOLDS more than `SITE_BUDGET` segments, whether
 * or not that group is expanded: collapsed it opens the group, expanded it
 * closes it again, and either way its presence is the visible statement that
 * the map is showing a projection of this group.
 */
export interface OverflowTile {
  siteKey: string;
  box: Box;
  /**
   * The segments this group holds and the map does not draw, in the group's own
   * ranked order. `[]` when the group is expanded.
   *
   * Name AND observed CIDR, and nothing else: a `TilePlacement` embeds its whole
   * `GraphSegment` (every host included), and a collapsed segment must not --
   * the arcs need the name to anchor on and the view needs the CIDR to say what
   * is in here, and the complete record is the outline, the matrix and the
   * inspector.
   */
  hidden: Array<{ name: string; cidr: string }>;
  expanded: boolean;
  /** The group's whole segment count, so the affordance can say "6 of 19". */
  total: number;
}

/**
 * The key every arc anchored to a COLLAPSED segment shares -- its group's
 * overflow tile (R6).
 *
 * A collapsed segment has no tile to leave from, and dropping its arcs would
 * delete most of the east-west band (measured: 95 % of `corpapcn-twin`'s
 * east-west bytes, including the widest arc on the map). So they anchor at the
 * `+N more` tile instead, and `layout/arcs.ts` merges the pairs that land on
 * one anchor into a single curve carrying their summed volume.
 */
export const OVERFLOW_ANCHOR_PREFIX = "overflow:";

export function overflowAnchorKey(siteKey: string): string {
  return `${OVERFLOW_ANCHOR_PREFIX}${siteKey}`;
}

/** The site an overflow anchor key names, or `null` for an ordinary segment
 *  anchor. The view uses it to label an arc that ends in a collapsed group. */
export function siteKeyOfOverflowAnchor(key: string): string | null {
  return key.startsWith(OVERFLOW_ANCHOR_PREFIX)
    ? key.slice(OVERFLOW_ANCHOR_PREFIX.length)
    : null;
}

/**
 * A left-edge tier rail (R1): the label box for one `TIERS` entry, spanning
 * that tier's vertical extent. The **segment** rail spans the whole tile
 * extent (its columns, not one band height) and the **east-west** rail spans
 * the conduit band -- so the two data tiers are as tall as what they hold.
 *
 * A rail with `box.h === 0` is a tier with nothing in it (no conduits, no
 * router, no infrastructure devices); it is still EMITTED, in `TIERS` order,
 * so the rail set is a total function of the tier vocabulary and the view
 * decides what to draw.
 *
 * Rails are **pre-pin band extents**: `applyPins` deliberately does NOT move
 * them. A pin repositions one tile or site; the tiers it sits in are the
 * diagram's fixed frame, and a rail that chased a dragged tile would stop
 * describing the tier.
 */
export interface TierRail {
  tier: Tier;
  box: Box;
}

/**
 * The capture tap on the Internet→WAN trunk (R2) -- the vantage the export was
 * taken from, which `15` §4's idiom puts on the link it actually monitors.
 *
 * **The vantage is carried VERBATIM and never parsed.** The two real bundles
 * disagree in shape (`axellio-hq/corp-apcn-rdr1:12` on the pit export,
 * `https://10.80.65.7:0-11` on the twin's), so any structure read out of it
 * would be read wrong on one of them; `null` renders as "vantage not recorded",
 * which is a different statement from an empty string.
 */
export interface TapBadge {
  box: Box;
  vantage: string | null;
  /** How many capture windows the export covers -- the time bound every
   *  volume on this map is "in" (§8: a window set, never a rate). */
  windows: number | null;
}

export interface Level0Layout {
  width: number;
  height: number;
  /** The five tier rails (R1), always in `TIERS` order, top to bottom, never
   *  overlapping -- the left-edge labels that make the stack read as a path. */
  rails: TierRail[];
  /** The Internet pill; its label carries the non-local count (O7). */
  internet: Box;
  nonLocalCount: number;
  /**
   * The WAN edge box (R2): drawn whenever `graph.wanEdge` exists -- i.e. when
   * ANY local↔non-local traffic was observed (the edge is then *inferred*) or
   * the cast publishes a router leg (it is *declared*). `null` only when
   * neither holds, and then there is no trunk either and the view says so.
   */
  wanEdge: Box | null;
  routerSegmentCount: number;
  /** The path from the Internet pill down to the site groups (R2); `null`
   *  exactly when `wanEdge` is. */
  trunk: Trunk | null;
  /** The tap badge on the trunk's spine; `null` exactly when `trunk` is. */
  tap: TapBadge | null;
  infraDevices: DevicePlacement[];
  /** One per PLACED device (R3): the short orthogonal vertical from the device
   *  down to its group's header, which replaced the diagonal tie-line that
   *  crossed whatever lay between (§2 F3). An unplaced device has none. */
  infraStubs: InfraStub[];
  sites: SitePlacement[];
  /** The tiles the map DRAWS -- since R6 that is each group's budgeted set,
   *  not every segment the twin has. `overflow` below names the rest. */
  tiles: TilePlacement[];
  /** One `+N more` tile per over-budget group (R6), collapsed or expanded. */
  overflow: OverflowTile[];
  /** How many segments the budget collapsed, across every group. `0` when the
   *  map draws them all. Named in the UI, never silent: eighteen tiles of
   *  thirty-nine read as "this twin has eighteen segments" otherwise
   *  (LES-307). */
  hiddenSegments: number;
  /** The *unplaced* tile (O7); `null` when every local entity is placed. */
  unplaced: UnplacedPlacement | null;
  /** The east-west band (R4): the top-K observed segment pairs by volume, plus
   *  every asserted conduit, each a cubic arc leaving its group's bottom edge
   *  (`arcs.ts`). It replaced the orthogonal lanes, which dropped from a
   *  TILE's edge through every row beneath it (§2 F4). */
  arcs: Arc[];
  /** Observed pairs the top-K budget left off the MAP (§8: the matrix keeps
   *  every one). `0` under `?ew=all`. Named in the UI, never silent — three
   *  drawn arcs of forty read as "there are three links" otherwise
   *  (LES-307). */
  hiddenPairs: number;
  /** Pairs drawn through a collapsed group's `+N more` anchor (R6) — on the
   *  map, but pointing at a box rather than at a tile. */
  foldedPairs: number;
  /** Pairs with BOTH ends collapsed in one group, which no curve can carry.
   *  Counted so the band's sentence can say so (LES-307). */
  internalPairs: number;
  /** Conduit endpoints that resolved to no tile (reported, not dropped). */
  unresolvedConduitEndpoints: string[];
}

/** What the VIEW may vary about a level-0 drawing without changing its
 *  identity. Everything here is a budget the operator chose in the URL, never
 *  data: with no options the layout is the pure function the goldens pin. */
export interface Level0Options {
  /** `?ew=all` — draw every observed segment pair, not the top `ARC_TOP_K`.
   *  Widths move with it (§5 D2), which the UI says out loud. */
  showAllPairs?: boolean;
  /** `?order=` — how each group's tiles are ranked, and therefore which of them
   *  the budget shows (R6). Default `hosts`. */
  order?: TileOrder;
  /** `?expand=` — the site keys whose per-group budget is lifted (R6). */
  expanded?: readonly string[];
}

interface Column {
  key: string;
  header: Box;
  box: Box;
  tiles: TilePlacement[];
  overflow: OverflowTile | null;
}

/**
 * The `index`-th tile-sized grid slot of a site column whose header's top-left
 * is `origin`, `cols` tiles across -- row-major, the rule every tile and the
 * `+N more` box are placed by. ONE function (LES-025): `placeColumn` lays a
 * column out with it, and a pending clone's proposed placement
 * (`layout/added.ts`) asks it for the slot after a column's last box, so the
 * proposal cannot drift from the grid it is proposed in.
 */
export function columnSlot(origin: { x: number; y: number }, cols: number, index: number): Box {
  return {
    x: origin.x + SITE_PAD + (index % cols) * (TILE_W + TILE_GAP_X),
    y: origin.y + SITE_HEADER_H + SITE_PAD + Math.floor(index / cols) * (TILE_H + TILE_GAP_Y),
    w: TILE_W,
    h: TILE_H,
  };
}

/**
 * Place one site's SHOWN tiles row-major at the column's own width, with the
 * `+N more` box -- when the group has one -- in the grid slot straight after
 * them (R6). The ranking, the budget and the width are all
 * `layout/columns.ts`'s; this turns its answer into coordinates.
 */
function placeColumn(key: string, x: number, y: number, budget: ColumnBudget): Column {
  const slot = (i: number): Box => columnSlot({ x, y }, budget.cols, i);
  const tiles: TilePlacement[] = budget.shown.map((segment, i) => ({
    kind: "tile" as const,
    segment,
    siteKey: key,
    box: slot(i),
  }));
  const overflow: OverflowTile | null = budget.overflow
    ? {
        siteKey: key,
        box: slot(budget.shown.length),
        hidden: budget.hidden.map((s) => ({ name: s.name, cidr: s.observedCidr })),
        expanded: budget.expanded,
        total: budget.shown.length + budget.hidden.length,
      }
    : null;
  return {
    key,
    header: { x, y, w: budget.width, h: SITE_HEADER_H },
    box: { x, y, w: budget.width, h: columnHeightFor(budget.boxes, budget.cols) },
    tiles,
    overflow,
  };
}

export function layout0(
  graph: ConsoleGraph,
  pins: Pins = NO_PINS,
  options: Level0Options = {},
): Level0Layout {
  // 1. Which column each infrastructure device belongs over (R3).
  //
  // Decided BEFORE the tiers are stacked, because the number of device ROWS a
  // column needs is what the INFRASTRUCTURE band must be tall enough for -- and
  // that band's height sets the segment tier's top edge. It needs no y at all:
  // a device's column follows from its segment, which is set data.
  const sites = groupIntoSites(graph.segments);
  // R6: which of each group's segments get a tile, in what order, and how wide
  // its column is. Decided FIRST, because the column width is now per group and
  // the infrastructure band is sized against it.
  const expandedKeys = new Set(options.expanded ?? []);
  const budgets = sites.map((site) =>
    budgetColumn(site.segments, {
      order: options.order ?? "hosts",
      expanded: expandedKeys.has(site.key),
    }),
  );
  // Over EVERY segment, collapsed ones included: a device's column is where its
  // segment lives, which the budget does not change -- only whether that
  // segment is drawn as a tile.
  const siteKeyByCidr = new Map<string, string>();
  for (const site of sites) {
    for (const seg of site.segments) siteKeyByCidr.set(seg.observedCidr, site.key);
  }
  const deviceInputs: InfraDeviceInput[] = graph.infraDevices.map((node) => ({
    id: node.id,
    siteKey: node.segment !== null ? (siteKeyByCidr.get(node.segment) ?? null) : null,
  }));
  const deviceCount = deviceInputs.length;
  const unplacedDevices = deviceInputs.filter((d) => d.siteKey === null).length;
  const infraH = infraBandHeight(
    infraBandRows(
      sites.map((site, i) => ({
        count: deviceInputs.filter((d) => d.siteKey === site.key).length,
        width: budgets[i]!.width,
      })),
      unplacedDevices,
    ),
    TIER_INFRA_H,
  );

  // 2. The three stacked top tiers. Only the INFRASTRUCTURE band's height is
  //    content-derived, and only a column that WRAPS makes it exceed
  //    `TIER_INFRA_H` -- which is why no bundle in the tree moves a tile by a
  //    single unit in this slice (`layout/plane.ts` carries the epoch that
  //    covers the ones that would).
  const [internetBand, wanBand, infraBand] = stackBands(
    [TIER_INTERNET_H, TIER_WAN_H, infraH],
    CANVAS_PAD,
    TIER_GAP_Y,
  ) as [{ y: number; h: number }, { y: number; h: number }, { y: number; h: number }];

  // 3. Site columns for the segment tier, left to right in prefix order.
  //
  // Everything the diagram draws starts RIGHT of the tier-rail gutter (R1), so
  // a rail label never sits under a tile. The gutter is a fixed left margin: it
  // shifts the whole plane once and changes no relative geometry.
  const contentX = CANVAS_PAD + RAIL_W;
  const segTierTop = infraBand.y + infraBand.h + TIER_GAP_Y;
  const columns: Column[] = [];
  let x = contentX;
  sites.forEach((site, i) => {
    const budget = budgets[i]!;
    columns.push(placeColumn(site.key, x, segTierTop, budget));
    x += budget.width + SITE_GAP_X;
  });

  // 4. The unplaced tile (O7), a final single-tile column.
  let unplaced: UnplacedPlacement | null = null;
  if (graph.unplacedLocal.length > 0) {
    unplaced = {
      count: graph.unplacedLocal.length,
      box: { x: x + SITE_PAD, y: segTierTop + SITE_HEADER_H + SITE_PAD, w: TILE_W, h: TILE_H },
    };
    // One tile wide, because it holds exactly one tile by construction (R6). It
    // is not a site group with a budget, so it takes the width its content
    // needs rather than the widest column's -- which is 344 units of plane the
    // pit bundle no longer spends on a single box.
    x += columnWidthFor(1) + SITE_GAP_X;
  }
  const sitesRight = x - SITE_GAP_X;

  // 5. Total width: the wider of the site row and the centred top tiers. Since
  //    R3 a PLACED device cannot widen the canvas -- it sits inside a column
  //    that is already counted -- so the only device row left to measure here
  //    is the centred one of UNPLACED devices.
  const unplacedDevicesWidth =
    unplacedDevices === 0
      ? 0
      : unplacedDevices * DEVICE_W + (unplacedDevices - 1) * DEVICE_GAP_X;
  const tiersWidth = Math.max(INTERNET_W, WAN_W, unplacedDevicesWidth);
  const width = Math.max(sitesRight + CANVAS_PAD, contentX + tiersWidth + CANVAS_PAD);
  // The chrome centres over the CONTENT area (right of the rail gutter), not
  // over the whole canvas -- the gutter is the one asymmetric margin, and
  // centring on `width / 2` would pull the Internet pill left onto the rails.
  const midX = contentX + (width - contentX - CANVAS_PAD) / 2;

  // 6. Centre the top tiers within the total width.
  const internet: Box = {
    x: midX - INTERNET_W / 2,
    y: internetBand.y,
    w: INTERNET_W,
    h: internetBand.h,
  };
  // The WAN edge is the twin's egress: DECLARED when the cast publishes router
  // legs (`CastSummary.routers`, R5), INFERRED when the export merely shows
  // local↔non-local traffic (R2). A bundle with neither shows the Internet
  // pill and no WAN box -- an invented edge would be worse than an absent one.
  const wanEdge: Box | null =
    graph.wanEdge !== null
      ? { x: midX - WAN_W / 2, y: wanBand.y, w: WAN_W, h: wanBand.h }
      : null;

  // 7. The infrastructure band (R3): every device over the column that holds
  //    its segment, with a short orthogonal stub down to that column's header.
  const infra = layoutInfra({
    devices: deviceInputs,
    groups: columns.map((c) => ({ siteKey: c.key, header: c.header })),
    band: infraBand,
    unplacedCx: midX,
  });
  const infraDevices = devicePlacements(infra.devices, graph);

  // 8. The east-west band beneath the lowest tile (R4): the observed
  //    segment-pair arcs (top-K by volume) and every asserted conduit. Each
  //    leaves its GROUP's bottom edge at its TILE's x, which is what makes
  //    "no connector crosses a tile" structural rather than lucky.
  const allTiles = columns.flatMap((c) => c.tiles);
  // The band sits below the deepest COLUMN, not below the lowest tile.
  //
  // An arc leaves its group's BOTTOM EDGE (`arcs.ts`), and since R6 a column can
  // end a whole row below its last tile -- the `+N more` box takes a grid slot
  // of its own. Measured from the tiles alone, the band top landed ABOVE the
  // anchor of any column ending in an overflow box, so each of those arcs took
  // its depth from its anchor instead and the band grew past the clamp its
  // height is supposed to be bounded by (201.8 units against a 140 bound, on the
  // pit bundle). Every column's bottom edge is now above the band top by
  // construction, rather than by the `SITE_PAD` < `TIER_GAP_Y` arithmetic that
  // happened to hold while every column ended in a tile.
  //
  // It is still a function of the PURE extent, so pins move an arc's endpoints
  // without dragging the whole band around.
  const columnsBottom = columns.reduce((max, c) => Math.max(max, c.box.y + c.box.h), segTierTop);
  const arcBandTop = columnsBottom + TIER_GAP_Y;
  const arcLimit = options.showAllPairs === true ? null : ARC_TOP_K;

  const sitePlacements: SitePlacement[] = columns.map((c, i) => ({
    site: sites[i]!,
    header: c.header,
    box: c.box,
  }));
  const overflowTiles = columns.flatMap((c) => (c.overflow ? [c.overflow] : []));
  const hiddenSegments = overflowTiles.reduce((n, more) => n + more.hidden.length, 0);

  const band = layoutArcs({
    asserted: graph.conduits,
    observed: graph.trafficPairs,
    anchors: arcAnchors(allTiles, sitePlacements, overflowTiles),
    bandTop: arcBandTop,
    limit: arcLimit,
  });

  // 8b. The trunk (R2) and its tap badge. Its bus sits in the existing
  //     WAN→infrastructure tier gap and its legs stop AT each group header's
  //     top edge, so it adds no height and enters no box.
  const trunk = trunkFor(internet, wanEdge, sitePlacements, graph.wanEdge);
  const tap: TapBadge | null = trunk
    ? {
        box: {
          x: trunk.tapAnchor.x - TAP_W / 2,
          y: trunk.tapAnchor.y - TAP_H / 2,
          w: TAP_W,
          h: TAP_H,
        },
        vantage: graph.vantage,
        windows: graph.windows,
      }
    : null;

  const height = Math.max(columnsBottom, band.bandBottom) + CANVAS_PAD;

  // 9. The tier rails (R1). Built by MAPPING `TIERS`, so "in TIERS order" is
  //    structural rather than a convention a later edit could break: the three
  //    fixed bands take their stacked extent, the two data tiers take the
  //    extent of what they actually hold (the columns; the conduit band).
  //    EVERY data tier's height is CONTENT-derived (S4): a band's reserved
  //    height is not evidence that anything is in it, so a cast with no router
  //    gets no WAN-EDGE rail and one with no infrastructure devices gets no
  //    INFRASTRUCTURE rail -- the same overclaim the two lower tiers already
  //    avoided. (The Internet pill is always drawn, so its band always counts.)
  const railExtent: Record<Tier, { y: number; h: number }> = {
    internet: internetBand,
    "wan-edge": { y: wanBand.y, h: wanEdge ? wanBand.h : 0 },
    infrastructure: { y: infraBand.y, h: deviceCount > 0 ? infraBand.h : 0 },
    segment: { y: segTierTop, h: Math.max(0, columnsBottom - segTierTop) },
    "east-west": { y: arcBandTop, h: Math.max(0, band.bandBottom - arcBandTop) },
  };
  const rails: TierRail[] = TIERS.map((tier) => ({
    tier,
    box: {
      x: CANVAS_PAD,
      y: railExtent[tier].y,
      w: RAIL_W - RAIL_GAP_X,
      h: railExtent[tier].h,
    },
  }));

  const layout: Level0Layout = {
    width,
    height,
    rails,
    internet,
    nonLocalCount: graph.nonLocal.length,
    wanEdge,
    routerSegmentCount: graph.routerSegmentNames.length,
    trunk,
    tap,
    infraDevices,
    infraStubs: infra.stubs,
    sites: sitePlacements,
    tiles: allTiles,
    overflow: overflowTiles,
    hiddenSegments,
    unplaced,
    arcs: band.arcs,
    hiddenPairs: band.hiddenPairs,
    foldedPairs: band.foldedPairs,
    internalPairs: band.internalPairs,
    unresolvedConduitEndpoints: graph.unresolvedConduitEndpoints,
  };

  return applyPins(layout, pins, {
    graph,
    arcBandTop,
    arcLimit,
    infra: { devices: deviceInputs, band: infraBand, unplacedCx: midX },
  });
}

/**
 * Every segment's arc anchor: its own tile (which gives the x) and its site
 * column's box (which gives the y) — or, for a segment R6's budget collapsed,
 * its group's `+N more` tile under one shared key.
 *
 * ONE function, both callers — `layout0` and `applyPins` — so the pure pass and
 * the pinned pass cannot disagree about which box an arc leaves from
 * (LES-025). A tile whose column is not drawn is skipped rather than anchored
 * to nothing.
 *
 * **A collapsed segment keeps an anchor rather than losing its arcs.** One
 * anchor OBJECT per overflow tile, filed under every name folded behind it, so
 * `layoutArcs` sees one key and merges what lands on it.
 */
function arcAnchors(
  tiles: readonly TilePlacement[],
  sites: readonly SitePlacement[],
  overflow: readonly OverflowTile[],
): Map<string, ArcAnchor> {
  const groupByKey = new Map(sites.map((sp) => [sp.site.key, sp.box]));
  const out = new Map<string, ArcAnchor>();
  for (const tile of tiles) {
    // No explicit `key`: an anchor's identity defaults to the name it is filed
    // under, which is exactly right for a segment with a tile of its own.
    const group = groupByKey.get(tile.siteKey);
    if (group) out.set(tile.segment.name, { tile: tile.box, group });
  }
  for (const more of overflow) {
    const group = groupByKey.get(more.siteKey);
    if (!group) continue;
    const anchor: ArcAnchor = {
      tile: more.box,
      group,
      key: overflowAnchorKey(more.siteKey),
    };
    for (const seg of more.hidden) out.set(seg.name, anchor);
  }
  return out;
}

/** Attach each placed box to the node it belongs to. An id the graph no longer
 *  carries is dropped rather than drawn as an empty box -- `layoutInfra` is
 *  given ids, and this is the one place they become nodes again. */
function devicePlacements(
  placed: readonly InfraPlacement[],
  graph: ConsoleGraph,
): DevicePlacement[] {
  const out: DevicePlacement[] = [];
  for (const p of placed) {
    const node = graph.nodeById.get(p.id);
    if (node) out.push({ kind: "device", node, box: p.box, siteKey: p.siteKey });
  }
  return out;
}

/**
 * The trunk, from the already-placed chrome. Extracted so `layout0` and
 * `applyPins` call ONE assembly rather than two that could disagree about
 * which volume feeds which leg (LES-025/LES-266).
 *
 * `null` whenever there is no WAN edge to hang it from -- the bundle with no
 * observed crossing traffic and no declared router, which draws neither box
 * nor path and says so in the view.
 */
function trunkFor(
  internet: Box,
  wanEdge: Box | null,
  sites: readonly SitePlacement[],
  wan: ConsoleGraph["wanEdge"],
): Trunk | null {
  if (!wanEdge || !wan) return null;
  return layoutTrunk({
    internet,
    wanEdge,
    groups: sites.map((sp) => ({
      siteKey: sp.site.key,
      header: sp.header,
      bytes: sp.site.northSouth.bytes,
      flows: sp.site.northSouth.flows,
    })),
    bytes: wan.bytes,
    flows: wan.flows,
  });
}

/**
 * Move pinned sites and tiles to their saved positions (S4). A no-op for the
 * default `NO_PINS`, which is why the golden tests -- computed with no pins --
 * are the pure function of identity, and *reset pins* is exactly this call
 * with an empty set.
 *
 * A pin is an ABSOLUTE override with NO reflow (`15` §4): the pinned element
 * ignores its computed slot and lands at its pinned top-left, and every
 * UNPINNED element keeps its computed place -- it does not shift to fill the
 * gap or make room. The one thing that follows a moved element is a connector
 * anchored to it: an east-west arc, a trunk leg and an infra stub are all
 * re-derived from the moved boxes below.
 */
function applyPins(layout: Level0Layout, pins: Pins, ctx: PinContext): Level0Layout {
  if (Object.keys(pins).length === 0) return layout;
  const { graph } = ctx;

  const tileBoxes = new Map(layout.tiles.map((t) => [t.segment.observedCidr, t.box]));

  // Site pins move the header, the column box and every member tile together.
  // The deltas are kept so the group's `+N more` tile travels with it too (R6):
  // it is part of the column, and an overflow box left behind would anchor the
  // collapsed segments' arcs to where the group used to be.
  const siteDeltas = new Map<string, { dx: number; dy: number }>();
  const sites = layout.sites.map((sp) => {
    const delta = pinDelta(sp.site.key, sp.box, pins);
    if (!delta) return sp;
    siteDeltas.set(sp.site.key, delta);
    for (const t of sp.site.segments) {
      const box = tileBoxes.get(t.observedCidr);
      if (box) tileBoxes.set(t.observedCidr, translateBox(box, delta.dx, delta.dy));
    }
    return {
      ...sp,
      header: translateBox(sp.header, delta.dx, delta.dy),
      box: translateBox(sp.box, delta.dx, delta.dy),
    };
  });

  // Segment pins move a single tile (applied after site pins, so a tile pin
  // wins over its site's -- the more specific gesture).
  const tiles = layout.tiles.map((t) => {
    const afterSite = tileBoxes.get(t.segment.observedCidr) ?? t.box;
    const delta = pinDelta(`segment:${t.segment.observedCidr}`, afterSite, pins);
    return { ...t, box: delta ? translateBox(afterSite, delta.dx, delta.dy) : afterSite };
  });

  // Re-anchor the east-west arcs from the MOVED tile and group boxes, reusing
  // the same `layoutArcs` primitive (LES-025/LES-266) with the pure
  // `arcBandTop` and the same budget, so the band stays put while an arc's
  // endpoint tracks what a pin moved. A pair neither pin touched recomputes
  // byte-identical (same anchors, same order, same limit), so this leaves
  // locality intact. The x follows the TILE and the y the GROUP, by
  // construction: a tile dragged out of its column is therefore reached in x
  // only -- the anchor rule the arcs ruling chose (§4.1), not an oversight.
  const overflow = layout.overflow.map((more) => {
    const delta = siteDeltas.get(more.siteKey);
    return delta ? { ...more, box: translateBox(more.box, delta.dx, delta.dy) } : more;
  });

  const band = layoutArcs({
    asserted: graph.conduits,
    observed: graph.trafficPairs,
    anchors: arcAnchors(tiles, sites, overflow),
    bandTop: ctx.arcBandTop,
    limit: ctx.arcLimit,
  });

  // The devices and their stubs are re-derived over the MOVED headers for the
  // same reason (LES-266): a device's x IS its column's centre, so a dragged
  // group takes its devices with it and every stub stays the short vertical it
  // was, instead of stretching into the diagonal R3 just retired. A column no
  // pin touched recomputes byte-identical. The band does not move -- the
  // devices stay in their tier, which is what the INFRASTRUCTURE rail names.
  const infra = layoutInfra({
    devices: ctx.infra.devices,
    groups: sites.map((sp) => ({ siteKey: sp.site.key, header: sp.header })),
    band: ctx.infra.band,
    unplacedCx: ctx.infra.unplacedCx,
  });

  // The TRUNK legs are re-anchored the same way and for the same reason: a
  // dragged site's leg tracks its header, the bus re-spans the moved set, and
  // a group no pin touched recomputes byte-identical because the primitive is
  // deterministic (LES-266). The SPINE and the tap do not move -- they are
  // anchored to the chrome, which no pin can touch.
  const trunk = trunkFor(layout.internet, layout.wanEdge, sites, graph.wanEdge);

  return {
    ...layout,
    sites,
    tiles,
    overflow,
    arcs: band.arcs,
    // The counts move with the arcs: a pin can re-anchor a curve, and a
    // sentence describing the pure pass would then describe a different band.
    hiddenPairs: band.hiddenPairs,
    foldedPairs: band.foldedPairs,
    internalPairs: band.internalPairs,
    trunk,
    infraDevices: devicePlacements(infra.devices, graph),
    infraStubs: infra.stubs,
  };
}

/** What `applyPins` needs to re-run the primitives over moved boxes, bundled
 *  so the re-derivations can grow (arcs, trunk, and the infra band) without
 *  the signature growing a parameter each time. */
interface PinContext {
  graph: ConsoleGraph;
  arcBandTop: number;
  /** The SAME budget the pure pass used, so a pin cannot change WHICH pairs
   *  are drawn -- only where they are anchored. */
  arcLimit: number | null;
  infra: {
    devices: readonly InfraDeviceInput[];
    band: { y: number; h: number };
    unplacedCx: number;
  };
}
