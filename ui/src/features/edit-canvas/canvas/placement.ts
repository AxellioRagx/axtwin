/**
 * The canvas's placement: the lifted tiered engine decides WHAT is drawn and
 * in what order -- the site groups, which of a group's segments get a tile
 * under the per-group budget (`+N more` for the rest), the role bands inside
 * a segment and which bands collapse by occupancy rung, the top-K observed
 * arcs with their log-scaled widths, the WAN edge -- and this module turns
 * that into React Flow nodes and edges at CONTENT-DERIVED sizes: a segment is
 * a group node sized to the chips it holds, a site a group of segments, a
 * router a node spanning the segments it joins.
 *
 * The frozen console drew hosts only one zoom level down; this canvas draws
 * every chip at once, so the element budgets are applied TOGETHER as the
 * guard against DOM blow-up (`SPEC.md` Notes): over the level-0 box budget,
 * or over the band budget in any drawn segment, the caller falls back to the
 * list/matrix view instead of a denser canvas.
 *
 * Positions: every element's computed absolute top-left, then the pins
 * (`site:` / `segment:` / `host:` -> absolute) override it; React Flow wants a
 * child's position RELATIVE to its parent, so the conversion happens last and
 * `absolutePosition` reverses it after a drag. A child is deliberately NOT
 * clamped to its parent's box (`extent: "parent"`): the clamp would silently
 * move a pin the operator saved, and the attachment is the twin's fact, not
 * the box's -- a chip dragged clear of its segment is still on it, and the
 * attach gesture (Phase 2) is what changes that. Pure -- no React, no DOM.
 */
import type { Edge, Node } from "@xyflow/react";

import {
  BAND_PAD,
  CANVAS_PAD,
  CHIP_GAP_X,
  CHIP_GAP_Y,
  CHIP_H,
  CHIP_W,
  CHIPS_PER_ROW,
  DEVICE_GAP_X,
  DEVICE_GAP_Y,
  DEVICE_H,
  DEVICE_W,
  INTERNET_W,
  SITE_GAP_X,
  SITE_HEADER_H,
  SITE_PAD,
  TIER_GAP_Y,
  TIER_INTERNET_H,
  TILE_GAP_X,
  TILE_GAP_Y,
  TILE_H,
  TILE_W,
  type Box,
  type Point,
} from "../layout/geometry";
import { layout0, overflowAnchorKey, type Level0Options } from "../layout/level0";
import { layout1 } from "../layout/level1";
import { NO_PINS, type Pins } from "../layout/pins";
import { stackBands } from "../layout/tiers";
import { BUDGET_LEVEL0, BUDGET_LEVEL1_BANDS, type RungTally } from "../model/budgets";
import type { ConsoleGraph, EvidenceMixEntry, GraphNode } from "../model/graph";
import { shortName } from "../model/detail";
import { roleBadgeLabel, roleBucket } from "../model/roles";
import type { Annotation } from "../edit/annotations";
import type { ExtraLeg } from "./authored";

// ---------------------------------------------------------------------------
// Node and edge data -- `type` aliases, not interfaces, so they satisfy React
// Flow's `Record<string, unknown>` bound.
// ---------------------------------------------------------------------------
export type SiteData = {
  kind: "site";
  key: string;
  label: string;
  shown: number;
  total: number;
  northSouthBytes: number;
  /** Folded to its label by the operator (`Canvas.tsx`); its segments are hidden. */
  collapsed?: boolean;
};
export type SegmentData = {
  kind: "segment";
  name: string;
  cidr: string;
  deployedCidr: string | null;
  zoneType: string | null;
  hosts: number;
  ghosts: number;
  evidenceMix: EvidenceMixEntry[];
  roleBadges: Array<{ role: string; count: number }>;
  hasRouterLeg: boolean;
};
export type HostData = {
  kind: "host";
  nodeId: string;
  label: string;
  /** The chip's name at full detail (`model/detail.ts` `shortName`). */
  short: string;
  address: string | null;
  role: string | null;
  roleLabel: string;
  roleConfidence: number | null;
  roleEvidence: string | null;
  /** The placement evidence (`segment_evidence_class`): the chip's ring. */
  evidence: string | null;
  occupancy: number | null;
  isGhost: boolean;
  listen: string[];
  dial: string[];
};
export type RungsData = { kind: "rungs"; role: string; roleLabel: string; count: number; rungs: RungTally[] };
/** What crossed the twin's edge, attributed to its gateway router: the
 *  graph's `WanEdge` (`inferred` when no router leg was published). */
export type Crossing = { edges: number; bytes: number; flows: number; inferred: boolean };
export type RouterData = {
  kind: "router";
  name: string;
  segments: string[];
  drawnLegs: number;
  /** The config names this router as an uplink (a leg on an `egress: true` segment). */
  uplink: boolean;
  /** The crossing traffic this router carries as the gateway, or `null` when it is not one. */
  crossing: Crossing | null;
};
export type OverflowData = { kind: "overflow"; siteKey: string; hidden: number; total: number; expanded: boolean };
/** The Internet pill: short label and sub-line beside the icon (the slot
 *  has room for about twenty glyphs each), the full wording in `title`.
 *  Rendering rule 1 (2026-11-06): it is drawn only when the cast evidence
 *  has non-local peers or the config defines an uplink on a router; the
 *  gateway is folded into that router (`RouterData.uplink` / `crossing`),
 *  there is no WAN node. */
export type ChromeData = { kind: "internet"; label: string; sub: string; title: string };
export type UnplacedData = { kind: "unplaced"; count: number };
/** An annotation (Phase 2): a positioned SVG snippet, presentation only. */
export type AnnotationData = { kind: "annotation"; ann: Annotation };
export type CanvasData = SiteData | SegmentData | HostData | RungsData | RouterData | OverflowData | ChromeData | UnplacedData | AnnotationData;
export type CanvasNode = Node<CanvasData, string>;

export type ArcData = { kind: "arc"; key: string; arcKind: "observed" | "asserted"; bytes: number; flows: number; pairs: number; label: string };
/** `folded`: the leg goes to a group's `+N more` tile for `folded` hidden
 *  segments at once (one edge, not one per hidden segment); it names no
 *  single attachment, so it has no label, no detach, no selection. */
export type LegData = { kind: "leg"; label: string; from: "router" | "host"; folded?: number };
export type TrunkData = { kind: "trunk"; bytes: number; flows: number };
export type CanvasEdgeData = ArcData | LegData | TrunkData;
export type CanvasEdge = Edge<CanvasEdgeData, string>;

export interface Placement {
  nodes: CanvasNode[];
  edges: CanvasEdge[];
  width: number;
  height: number;
  /** Why the canvas must NOT draw this twin, or `null`. */
  overBudget: string | null;
  hiddenSegments: number;
  hiddenPairs: number;
  /** Segment NAME -> the arc anchor it is drawn through (itself, or its
   *  group's `+N more` tile when collapsed). */
  anchorByName: Record<string, string>;
}

export interface PlacementOptions extends Level0Options {
  /** A multi-homed authored host's further attachments, drawn as legs. */
  extraLegs?: readonly ExtraLeg[];
  /** The routers the config names as uplinks (`AuthoredGraph.uplinks`). */
  uplinks?: readonly string[];
}

export const ID = {
  site: (key: string) => key, // a site key is already `site:<prefix>`
  segment: (name: string) => `seg:${name}`,
  host: (nodeId: string) => `host:${nodeId}`,
  router: (name: string) => `router:${name}`,
  overflow: (siteKey: string) => overflowAnchorKey(siteKey),
  internet: "chrome:internet",
  wan: "chrome:wan",
  unplaced: "tile:unplaced",
};

export const HANDLE = { top: "top", bottom: "bottom", bottomIn: "bottom-in" } as const;

/** The pin key a node is saved under, or `null` for an element that is part
 *  of the fixed frame (chrome, a collapsed band, the `+N more` tile). A
 *  router is a node of the twin and pins as `host:<name>` like any node. */
export function pinKeyOf(node: Pick<CanvasNode, "data">): string | null {
  const d = node.data;
  switch (d.kind) {
    case "site":
      return d.key;
    case "segment":
      return `segment:${d.cidr}`;
    case "host":
      return `host:${d.nodeId}`;
    case "router":
      return `host:${d.name}`;
    default:
      return null;
  }
}

/** A node's absolute top-left from its (relative) position and its parents'. */
export function absolutePosition(nodes: ReadonlyArray<Pick<CanvasNode, "id" | "position" | "parentId">>, id: string): Point | null {
  const byId = new Map(nodes.map((n) => [n.id, n]));
  let n = byId.get(id);
  if (!n) return null;
  let x = n.position.x;
  let y = n.position.y;
  while (n.parentId) {
    const p = byId.get(n.parentId);
    if (!p) break;
    x += p.position.x;
    y += p.position.y;
    n = p;
  }
  return { x, y };
}

/** One normalized arc key for a pair of anchors, whichever way round. */
export function arcKey(a: string, b: string): string {
  return a < b ? `${a}|${b}` : `${b}|${a}`;
}

// ---------------------------------------------------------------------------
// Chips inside a segment: the lifted level-1 band rule gives role order,
// which band collapses and its rung tally; the grid here is the canvas's own.
// ---------------------------------------------------------------------------
type ChipSpec = { node: GraphNode } | { rungs: RungsData };

function chipsOf(graph: ConsoleGraph, cidr: string): { chips: ChipSpec[]; bands: number } {
  const l1 = layout1(graph, cidr);
  if (!l1) return { chips: [], bands: 0 };
  const chips: ChipSpec[] = l1.bands.flatMap((b): ChipSpec[] =>
    b.collapsed
      ? [{ rungs: { kind: "rungs", role: b.role, roleLabel: roleBadgeLabel(b.role), count: b.count, rungs: b.rungs } }]
      : b.chips.map((c) => ({ node: c.node })),
  );
  return { chips, bands: l1.bands.length };
}

/** Row-major packing of a site's boxes at `cols` across, each column as wide
 *  as its widest box and each row as tall as its tallest; sets every box's
 *  `rel` and returns the site's size. */
function packSiteBoxes(boxes: Array<{ w: number; h: number; rel: Point }>, colsWanted: number): { w: number; h: number } {
  const cols = Math.max(1, Math.min(colsWanted, boxes.length || 1));
  const rows = Math.max(1, Math.ceil(boxes.length / cols));
  const colW = Array.from({ length: cols }, () => 0);
  const rowH = Array.from({ length: rows }, () => 0);
  boxes.forEach((b, i) => {
    colW[i % cols] = Math.max(colW[i % cols] ?? 0, b.w);
    rowH[Math.floor(i / cols)] = Math.max(rowH[Math.floor(i / cols)] ?? 0, b.h);
  });
  const colX = colW.map((_, c) => SITE_PAD + colW.slice(0, c).reduce((a, w) => a + w, 0) + c * TILE_GAP_X);
  const rowY = rowH.map((_, r) => SITE_HEADER_H + SITE_PAD + rowH.slice(0, r).reduce((a, h) => a + h, 0) + r * TILE_GAP_Y);
  boxes.forEach((b, i) => {
    b.rel = { x: colX[i % cols] ?? SITE_PAD, y: rowY[Math.floor(i / cols)] ?? SITE_HEADER_H + SITE_PAD };
  });
  const w = 2 * SITE_PAD + colW.reduce((a, x) => a + x, 0) + (cols - 1) * TILE_GAP_X;
  const h = boxes.length ? SITE_HEADER_H + 2 * SITE_PAD + rowH.reduce((a, x) => a + x, 0) + (rows - 1) * TILE_GAP_Y : SITE_HEADER_H + 2 * SITE_PAD;
  return { w, h };
}

export interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

function overlaps(a: Rect, b: Rect): boolean {
  return a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
}

/**
 * The nearest free spot for a box whose packed slot is under a PINNED box:
 * to the right of what it overlaps, and onto a new row under everything
 * when the row is full. An operator's pins are fixed; a box without one
 * (a segment just added, or one placed before the pins moved) goes into
 * free space instead of under a neighbour (2026-11-05: `segment-3` landed
 * under `office` at a 12 px offset, and a fresh load hid it there). Pure.
 */
export function freeSlot(at: Point, size: { w: number; h: number }, occupied: readonly Rect[], origin: Point, rowWidth: number): Point {
  let x = at.x;
  let y = at.y;
  const right = Math.max(origin.x + rowWidth, ...occupied.map((r) => r.x + r.w + SITE_PAD));
  for (let guard = 0; guard < 400; guard++) {
    const hit = occupied.find((r) => overlaps({ x, y, w: size.w, h: size.h }, r));
    if (!hit) return { x, y };
    x = hit.x + hit.w + TILE_GAP_X;
    if (x + size.w > right) {
      x = origin.x + SITE_PAD;
      y = Math.max(y, ...occupied.map((r) => r.y + r.h)) + TILE_GAP_Y;
    }
  }
  return { x, y };
}

/** The shape a fitView should meet: a window is about this wide for its
 *  height, and the plane is judged with the tiers above the sites counted. */
export const PLANE_ASPECT_TARGET = 1.8;
const TIERS_H_ESTIMATE = TIER_INTERNET_H + 96 + 2 * TIER_GAP_Y;

/**
 * How many tile columns a site may use (the engine's count at most, the
 * same cap for every site) so that the sites in their one row make a plane
 * near `PLANE_ASPECT_TARGET`. The lifted engine packs 156 px tiles three
 * across; the canvas's chip-sized tiles are twice as wide, and at three
 * across a five-site cast was 4.5 : 1 -- a fitView used the middle third
 * of the window. Pure: the choice with the aspect nearest the target wins,
 * so a small twin stays two across and a wide one goes to one.
 */
export function chooseSiteCols(sites: ReadonlyArray<{ boxes: ReadonlyArray<{ w: number; h: number }>; engineCols: number }>): number {
  if (!sites.length) return 1;
  const most = Math.max(1, ...sites.map((s) => s.engineCols));
  let best = most;
  let bestScore = Number.POSITIVE_INFINITY;
  for (let k = most; k >= 1; k--) {
    let width = 2 * CANVAS_PAD + Math.max(0, sites.length - 1) * SITE_GAP_X;
    let tallest = 0;
    for (const s of sites) {
      const size = packSiteBoxes(s.boxes.map((b) => ({ w: b.w, h: b.h, rel: { x: 0, y: 0 } })), Math.min(k, s.engineCols));
      width += size.w;
      tallest = Math.max(tallest, size.h);
    }
    const aspect = width / (TIERS_H_ESTIMATE + tallest);
    const score = Math.abs(Math.log(aspect) - Math.log(PLANE_ASPECT_TARGET));
    if (score < bestScore) {
      bestScore = score;
      best = k;
    }
  }
  return best;
}

function gridFor(count: number): { cols: number; rows: number; w: number; h: number } {
  const cols = Math.max(1, Math.min(CHIPS_PER_ROW, count));
  const rows = Math.ceil(count / cols);
  const w = Math.max(TILE_W, 2 * BAND_PAD + cols * CHIP_W + (cols - 1) * CHIP_GAP_X);
  const h = Math.max(TILE_H, SITE_HEADER_H + 2 * BAND_PAD + rows * CHIP_H + Math.max(0, rows - 1) * CHIP_GAP_Y);
  return { cols, rows, w, h };
}

function chipSlot(i: number, cols: number): Point {
  return {
    x: BAND_PAD + (i % cols) * (CHIP_W + CHIP_GAP_X),
    y: SITE_HEADER_H + BAND_PAD + Math.floor(i / cols) * (CHIP_H + CHIP_GAP_Y),
  };
}

function hostData(n: GraphNode): HostData {
  const role = roleBucket(n.role, n.locality);
  return {
    kind: "host",
    nodeId: n.id,
    label: n.address ?? n.id,
    short: shortName(n.id, n.address),
    address: n.address,
    role: n.role,
    roleLabel: roleBadgeLabel(role),
    roleConfidence: n.roleConfidence,
    roleEvidence: n.roleEvidenceClass,
    evidence: n.segmentEvidenceClass,
    occupancy: n.occupancy,
    isGhost: n.isGhost,
    listen: n.listenPorts,
    dial: n.dialPorts,
  };
}

// ---------------------------------------------------------------------------
// The placement
// ---------------------------------------------------------------------------
interface PlacedBox {
  id: string;
  type: string;
  data: CanvasData;
  /** Relative to the site. */
  rel: Point;
  w: number;
  h: number;
  pinKey: string | null;
  children: Array<{ id: string; type: string; data: CanvasData; rel: Point; w: number; h: number; pinKey: string | null }>;
}

interface PlacedSite {
  key: string;
  data: SiteData;
  abs: Point;
  w: number;
  h: number;
  boxes: PlacedBox[];
}

/** A leg's layer: above a site (z 0), its segment boxes (z 1) and their chips
 *  (z 2), so a leg that crosses another box is drawn, not hidden behind it
 *  (2026-11-05: the router→servers leg showed only as a stub between boxes). */
export const EDGE_Z = 3;
/** The class on a leg edge: `canvas.css` takes its pointer events away, so a
 *  leg over a box never catches a drag or a click meant for the box; the pane
 *  hit-tests the legs itself (`nearestLeg`). */
export const LEG_CLASS = "cv-leg-edge";

export function place(graph: ConsoleGraph, pins: Pins = NO_PINS, options: PlacementOptions = {}): Placement {
  const l0 = layout0(graph, NO_PINS, options);
  const empty = (overBudget: string | null): Placement => ({
    nodes: [],
    edges: [],
    width: 0,
    height: 0,
    overBudget,
    hiddenSegments: l0.hiddenSegments,
    hiddenPairs: l0.hiddenPairs,
    anchorByName: {},
  });

  // ---- the budgets, applied before anything is sized ----
  const boxCount = l0.tiles.length + l0.overflow.length + graph.routers.length + (l0.unplaced ? 1 : 0) + 2;
  if (boxCount > BUDGET_LEVEL0) {
    return empty(`${boxCount} boxes exceed the canvas budget of ${BUDGET_LEVEL0} (the frozen console's level-0 cap)`);
  }
  const chipsByCidr = new Map<string, ChipSpec[]>();
  for (const t of l0.tiles) {
    const { chips, bands } = chipsOf(graph, t.segment.observedCidr);
    if (bands > BUDGET_LEVEL1_BANDS) {
      return empty(`segment ${t.segment.observedCidr} holds ${bands} role bands, over the band budget of ${BUDGET_LEVEL1_BANDS}`);
    }
    chipsByCidr.set(t.segment.observedCidr, chips);
  }

  // ---- sites: the engine's grouping and order, re-packed at content sizes ----
  const sites: PlacedSite[] = [];
  const anchorByName: Record<string, string> = {};
  const pending: Array<{ key: string; sp: (typeof l0.sites)[number]; tiles: typeof l0.tiles; boxes: PlacedBox[]; engineCols: number }> = [];
  for (const sp of l0.sites) {
    const key = sp.site.key;
    const tiles = l0.tiles.filter((t) => t.siteKey === key);
    const overflow = l0.overflow.find((o) => o.siteKey === key) ?? null;
    const boxes: PlacedBox[] = tiles.map((t) => {
      const seg = t.segment;
      const chips = chipsByCidr.get(seg.observedCidr) ?? [];
      const grid = gridFor(chips.length);
      anchorByName[seg.name] = seg.name;
      return {
        id: ID.segment(seg.name),
        type: "segment",
        data: {
          kind: "segment",
          name: seg.name,
          cidr: seg.observedCidr,
          deployedCidr: seg.deployedCidr,
          zoneType: seg.zoneType,
          hosts: seg.hostCount,
          ghosts: seg.ghostCount,
          evidenceMix: seg.evidenceMix,
          roleBadges: seg.roleBadges,
          hasRouterLeg: seg.hasRouterLeg,
        },
        rel: { x: 0, y: 0 },
        w: grid.w,
        h: grid.h,
        pinKey: `segment:${seg.observedCidr}`,
        children: chips.map((c, i) =>
          "node" in c
            ? { id: ID.host(c.node.id), type: "host", data: hostData(c.node), rel: chipSlot(i, grid.cols), w: CHIP_W, h: CHIP_H, pinKey: `host:${c.node.id}` }
            : { id: `${ID.segment(seg.name)}:rungs:${c.rungs.role}`, type: "rungs", data: c.rungs, rel: chipSlot(i, grid.cols), w: CHIP_W, h: CHIP_H, pinKey: null },
        ),
      };
    });
    if (overflow) {
      for (const h of overflow.hidden) anchorByName[h.name] = ID.overflow(key);
      boxes.push({
        id: ID.overflow(key),
        type: "overflow",
        data: { kind: "overflow", siteKey: key, hidden: overflow.hidden.length, total: overflow.total, expanded: overflow.expanded },
        rel: { x: 0, y: 0 },
        w: TILE_W,
        h: TILE_H,
        pinKey: null,
        children: [],
      });
    }
    // The engine's grid is row-major at the column count it chose (distinct
    // x slots among its tile boxes); the canvas may use fewer columns, never
    // more, so a tile keeps its order and the plane keeps a window's shape.
    const slotX = new Set<number>(tiles.map((t) => t.box.x));
    if (overflow) slotX.add(overflow.box.x);
    pending.push({ key, sp, tiles, boxes, engineCols: Math.max(1, slotX.size) });
  }
  const maxCols = chooseSiteCols(pending.map((p) => ({ boxes: p.boxes.map((b) => ({ w: b.w, h: b.h })), engineCols: p.engineCols })));
  let cursorX = CANVAS_PAD;
  for (const { key, sp, tiles, boxes, engineCols } of pending) {
    const { w, h } = packSiteBoxes(boxes, Math.min(engineCols, maxCols));
    sites.push({
      key,
      data: { kind: "site", key, label: sp.site.label, shown: tiles.length, total: sp.site.segments.length, northSouthBytes: sp.site.northSouth.bytes },
      abs: { x: cursorX, y: 0 },
      w,
      h,
      boxes,
    });
    cursorX += w + SITE_GAP_X;
  }
  const unplacedW = l0.unplaced ? TILE_W : 0;
  const contentW = Math.max(INTERNET_W, cursorX - CANVAS_PAD - (sites.length ? SITE_GAP_X : 0) + (l0.unplaced ? (sites.length ? SITE_GAP_X : 0) + unplacedW : 0));
  const sitesH = Math.max(TILE_H, ...sites.map((s) => s.h));

  // ---- routers: one box per router, spanning the segments it joins ----
  const segAbsX = new Map<string, { left: number; right: number }>();
  for (const s of sites) {
    for (const b of s.boxes) {
      const left = s.abs.x + b.rel.x;
      segAbsX.set(b.id, { left, right: left + b.w });
    }
  }
  const routerSpans = graph.routers.map((r) => {
    const spans = r.segments
      .map((name) => segAbsX.get(anchorByName[name] === name ? ID.segment(name) : (anchorByName[name] ?? "")))
      .filter((s): s is { left: number; right: number } => !!s);
    if (!spans.length) return { router: r, left: CANVAS_PAD, w: DEVICE_W, drawnLegs: 0 };
    const left = Math.min(...spans.map((s) => s.left));
    const right = Math.max(...spans.map((s) => s.right));
    return { router: r, left, w: Math.max(DEVICE_W, right - left), drawnLegs: spans.length };
  });
  routerSpans.sort((a, b) => a.left - b.left || (a.router.name < b.router.name ? -1 : 1));
  const rowRight: number[] = [];
  const routerRows = routerSpans.map((s) => {
    let row = rowRight.findIndex((right) => right + DEVICE_GAP_X <= s.left);
    if (row < 0) {
      row = rowRight.length;
      rowRight.push(s.left + s.w);
    } else rowRight[row] = s.left + s.w;
    return row;
  });
  const infraH = rowRight.length ? rowRight.length * DEVICE_H + (rowRight.length - 1) * DEVICE_GAP_Y : 0;

  // ---- the tiers, stacked top to bottom with the lifted primitive ----
  // Rendering rule 1: the Internet is drawn only on evidence of non-local
  // peers or a configured uplink; the gateway is a badge on its router, so
  // there is no WAN band and a twin with neither starts at its router.
  const uplinks = [...(options.uplinks ?? [])];
  const showInternet = graph.nonLocal.length > 0 || uplinks.length > 0;
  // The gateways: the configured uplinks; else, with crossing EVIDENCE and
  // one router, that router (the old WAN label's "the gateway" rule). A
  // router alone is no gateway: an authored twin shows no badge.
  const crossing: Crossing | null = graph.wanEdge && graph.wanEdge.crossingEdges > 0 ? { edges: graph.wanEdge.crossingEdges, bytes: graph.wanEdge.bytes, flows: graph.wanEdge.flows, inferred: graph.wanEdge.inferred } : null;
  const gateways = uplinks.length ? uplinks : crossing && routerSpans.length === 1 ? [routerSpans[0]!.router.name] : [];
  const bands = stackBands([showInternet ? TIER_INTERNET_H : 0, infraH, sitesH], CANVAS_PAD, TIER_GAP_Y);
  const [internetBand, infraBand, segmentBand] = bands as [Box, Box, Box];
  for (const s of sites) s.abs = { x: s.abs.x, y: segmentBand.y };

  // ---- nodes, with pins applied on absolute coordinates, then made relative ----
  const pinned = (key: string | null, abs: Point): Point => {
    const p = key ? pins[key] : undefined;
    return p ? { x: p.x, y: p.y } : abs;
  };
  const nodes: CanvasNode[] = [];
  const common = { connectable: false, selectable: true } as const;
  if (showInternet) {
    nodes.push({
      id: ID.internet,
      type: "chrome",
      position: { x: CANVAS_PAD + (contentW - INTERNET_W) / 2, y: internetBand.y },
      width: INTERNET_W,
      height: TIER_INTERNET_H - 12,
      draggable: false,
      ...common,
      data: {
        kind: "internet",
        label: "Internet",
        sub: graph.nonLocal.length ? `${graph.nonLocal.length} non-local` : `uplink on ${gateways.join(", ")}`,
        title: graph.nonLocal.length ? `Internet · ${graph.nonLocal.length} non-local entities (outside every segment)` : `Internet · the config's uplink on ${gateways.join(", ")}`,
      },
    });
  }
  routerSpans.forEach((s, i) => {
    const key = `host:${s.router.name}`;
    const abs = pinned(key, { x: s.left, y: infraBand.y + (routerRows[i] ?? 0) * (DEVICE_H + DEVICE_GAP_Y) });
    nodes.push({
      id: ID.router(s.router.name),
      type: "router",
      position: abs,
      width: s.w,
      height: DEVICE_H,
      // Over the site tints and segment boxes (0 / 1), under the legs (EDGE_Z): a router
      // dragged into a site stays visible and hoverable.
      zIndex: 2,
      ...common,
      data: { kind: "router", name: s.router.name, segments: [...s.router.segments], drawnLegs: s.drawnLegs, uplink: uplinks.includes(s.router.name), crossing: gateways.includes(s.router.name) ? crossing : null },
    });
  });
  for (const s of sites) {
    const siteAbs = pinned(s.key, s.abs);
    // Pinned boxes are fixed; the others take their packed slot unless a pinned
    // box sits there, then the nearest free spot. The site grows to hold them all.
    const placedBoxes = s.boxes.map((b) => {
      const p = b.pinKey ? pins[b.pinKey] : undefined;
      return { b, abs: p ? { x: p.x, y: p.y } : { x: siteAbs.x + b.rel.x, y: siteAbs.y + b.rel.y }, fixed: Boolean(p) };
    });
    const occupied: Rect[] = placedBoxes.filter((x) => x.fixed).map((x) => ({ x: x.abs.x, y: x.abs.y, w: x.b.w, h: x.b.h }));
    if (occupied.length) {
      for (const pb of placedBoxes) {
        if (pb.fixed) continue;
        pb.abs = freeSlot(pb.abs, { w: pb.b.w, h: pb.b.h }, occupied, siteAbs, s.w);
        occupied.push({ x: pb.abs.x, y: pb.abs.y, w: pb.b.w, h: pb.b.h });
      }
    }
    // The site's box is the union of its packed box and every child box with
    // its padding (the header above), so a segment dragged out in any direction
    // takes the site's edge with it rather than leaving a hole (round 2).
    const siteOrigin = {
      x: Math.min(siteAbs.x, ...placedBoxes.map((x) => x.abs.x - SITE_PAD)),
      y: Math.min(siteAbs.y, ...placedBoxes.map((x) => x.abs.y - SITE_HEADER_H - SITE_PAD)),
    };
    const siteW = Math.max(siteAbs.x + s.w, ...placedBoxes.map((x) => x.abs.x + x.b.w + SITE_PAD)) - siteOrigin.x;
    const siteH = Math.max(siteAbs.y + s.h, ...placedBoxes.map((x) => x.abs.y + x.b.h + SITE_PAD)) - siteOrigin.y;
    // The tint is not a selection target (rule 3): selected, React Flow would
    // lift it over the routers and legs it lies under. It still drags (its pin).
    nodes.push({ id: ID.site(s.key), type: "site", position: siteOrigin, width: siteW, height: siteH, ...common, selectable: false, data: s.data });
    for (const { b, abs: boxAbs } of placedBoxes) {
      nodes.push({
        id: b.id,
        type: b.type,
        parentId: ID.site(s.key),
        position: { x: boxAbs.x - siteOrigin.x, y: boxAbs.y - siteOrigin.y },
        width: b.w,
        height: b.h,
        ...common,
        data: b.data,
      });
      for (const c of b.children) {
        const chipAbs = pinned(c.pinKey, { x: boxAbs.x + c.rel.x, y: boxAbs.y + c.rel.y });
        nodes.push({
          id: c.id,
          type: c.type,
          parentId: b.id,
          position: { x: chipAbs.x - boxAbs.x, y: chipAbs.y - boxAbs.y },
          width: c.w,
          height: c.h,
          draggable: c.pinKey !== null,
          ...common,
          data: c.data,
        });
      }
    }
  }
  if (l0.unplaced) {
    nodes.push({
      id: ID.unplaced,
      type: "unplaced",
      position: { x: cursorX, y: segmentBand.y },
      width: TILE_W,
      height: TILE_H,
      draggable: false,
      ...common,
      data: { kind: "unplaced", count: l0.unplaced.count },
    });
  }

  // ---- edges ----
  const edges: CanvasEdge[] = [];
  if (showInternet) {
    for (const name of gateways) {
      edges.push({ id: `trunk:internet>${name}`, type: "trunk", source: ID.internet, target: ID.router(name), sourceHandle: HANDLE.bottom, targetHandle: HANDLE.top, data: { kind: "trunk", bytes: crossing?.bytes ?? 0, flows: crossing?.flows ?? 0 } });
    }
  }
  for (const s of routerSpans) {
    const foldedTo = new Map<string, number>();
    for (const name of s.router.segments) {
      const anchor = anchorByName[name];
      if (!anchor) continue;
      if (anchor !== name) {
        foldedTo.set(anchor, (foldedTo.get(anchor) ?? 0) + 1);
        continue;
      }
      edges.push({ id: `leg:${s.router.name}>${name}`, type: "leg", className: LEG_CLASS, source: ID.router(s.router.name), target: ID.segment(name), sourceHandle: HANDLE.bottom, targetHandle: HANDLE.top, zIndex: EDGE_Z, data: { kind: "leg", label: name, from: "router" } });
    }
    // A router that joins segments behind a group's `+N more` tile draws ONE
    // leg to that tile, however many it joins there (a 45-leg router drew a
    // wedge of labelled lines to the same tile).
    for (const [anchor, n] of foldedTo) {
      edges.push({ id: `legs:${s.router.name}>${anchor}`, type: "leg", className: LEG_CLASS, source: ID.router(s.router.name), target: anchor, sourceHandle: HANDLE.bottom, targetHandle: HANDLE.top, zIndex: EDGE_Z, data: { kind: "leg", label: `${n} segment${n === 1 ? "" : "s"} behind +N more`, from: "router", folded: n } });
    }
  }
  for (const leg of options.extraLegs ?? []) {
    const anchor = anchorByName[leg.segment];
    if (!anchor) continue;
    const target = anchor === leg.segment ? ID.segment(leg.segment) : anchor;
    edges.push({ id: `leg:${leg.host}>${leg.segment}`, type: "leg", className: LEG_CLASS, source: ID.host(leg.host), target, sourceHandle: HANDLE.bottom, targetHandle: HANDLE.top, zIndex: EDGE_Z, data: { kind: "leg", label: leg.ip ?? leg.segment, from: "host" } });
  }
  for (const arc of l0.arcs) {
    const src = anchorByName[arc.a] === arc.a ? ID.segment(arc.a) : arc.a;
    const dst = anchorByName[arc.b] === arc.b ? ID.segment(arc.b) : arc.b;
    const key = arcKey(arc.a, arc.b);
    edges.push({
      id: `arc:${arc.kind}:${key}`,
      type: "arc",
      source: src,
      target: dst,
      sourceHandle: HANDLE.bottom,
      targetHandle: HANDLE.bottomIn,
      style: { strokeWidth: arc.strokeWidth },
      data: {
        kind: "arc",
        key,
        arcKind: arc.kind,
        bytes: arc.bytes,
        flows: arc.flows,
        pairs: arc.pairs,
        label: arc.kind === "asserted" && !arc.hasTraffic ? "conduit (declared, no traffic observed)" : `${arc.bytes.toLocaleString()} bytes · ${arc.flows.toLocaleString()} flows${arc.pairs > 1 ? ` · ${arc.pairs} pairs` : ""}`,
      },
    });
  }

  return {
    nodes,
    edges,
    width: contentW + 2 * CANVAS_PAD,
    height: segmentBand.y + sitesH + CANVAS_PAD,
    overBudget: null,
    hiddenSegments: l0.hiddenSegments,
    hiddenPairs: l0.hiddenPairs,
    anchorByName,
  };
}
