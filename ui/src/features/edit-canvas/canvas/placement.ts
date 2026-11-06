/**
 * The canvas's placement: the lifted tiered engine decides WHAT is drawn --
 * the site groups, which of a group's segments get a lane under the
 * per-group budget (`+N more` for the rest), the role bands inside a segment
 * and which bands collapse by occupancy rung, the top-K observed arcs with
 * their log-scaled widths -- and this module turns that into React Flow
 * nodes and edges on the BUS plane (rendering rules of track 4, 2026-11-06):
 * a segment is a horizontal bus lane sized to the icons on it, a site a
 * vertical stack of lanes behind a tint, a router a box in the row above
 * with one hue and one leg into every bus it serves.
 *
 * The frozen console drew hosts only one zoom level down; this canvas draws
 * every icon at once, so the element budgets are applied TOGETHER as the
 * guard against DOM blow-up (`SPEC.md` Notes): over the level-0 box budget,
 * or over the band budget in any drawn segment, the caller falls back to the
 * list/matrix view instead of a denser canvas.
 *
 * Positions: every element's computed absolute top-left, then the pins
 * override it -- a `site:` or `host:` pin is an absolute top-left, a
 * `segment:` pin is the lane's RANK within its site (its `y`; `x` is 0), so
 * a bus is reordered, never dropped free on the plane. React Flow wants a
 * child's position RELATIVE to its parent, so the conversion happens last
 * and `absolutePosition` reverses it after a drag. A child is deliberately
 * NOT clamped to its parent's box: the attachment is the twin's fact, not
 * the box's -- an icon dragged clear of its bus is still on it, and the
 * attach gesture is what changes that. Pure -- no React, no DOM.
 */
import type { Edge, Node } from "@xyflow/react";

import { CANVAS_PAD, DEVICE_GAP_X, DEVICE_GAP_Y, DEVICE_H, DEVICE_W, INTERNET_W, SITE_GAP_X, SITE_HEADER_H, SITE_PAD, TIER_GAP_Y, TIER_INTERNET_H, TILE_H, TILE_W, type Box, type Point } from "../layout/geometry";
import { layout0, overflowAnchorKey, siteKeyOfOverflowAnchor, type Level0Options } from "../layout/level0";
import { layout1 } from "../layout/level1";
import { NO_PINS, type Pins } from "../layout/pins";
import { stackBands } from "../layout/tiers";
import { BUDGET_LEVEL0, BUDGET_LEVEL1_BANDS, type RungTally } from "../model/budgets";
import type { ConsoleGraph, EvidenceMixEntry, GraphNode } from "../model/graph";
import { shortName } from "../model/detail";
import { compareCidr } from "../model/keys";
import { roleBadgeLabel, roleBucket } from "../model/roles";
import type { Annotation } from "../edit/annotations";
import type { ExtraLeg } from "./authored";
import { BUS_BADGE_W, BUS_GAP_Y, BUS_ICON, BUS_MIN_W, OVERFLOW_LANE_H, PORT_PAD, SITE_ROW_GAP_Y, chooseSitesPerRow, iconSlot, laneContentWidth, laneHeight, laneRows, planeSize, portRegionWidth, siteSize } from "./bus";
import { hueOf } from "./hues";

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
  /** Folded to its label by the operator (`Canvas.tsx`); its lanes are hidden. */
  collapsed?: boolean;
};
/** A router with a leg on a bus, with its hue: the dot at the bus's left end (rule 2). */
export type Served = { router: string; hue: string };
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
  /** The routers with a leg here, in router order (rule 2's dots). */
  served?: Served[];
  /** The lane's rank within its site, top to bottom: what a `segment:` pin saves. */
  rank?: number;
  /** How many host rows the lane holds. */
  rows?: number;
};
export type HostData = {
  kind: "host";
  nodeId: string;
  label: string;
  /** The icon's caption when labels are on (`model/detail.ts` `shortName`). */
  short: string;
  address: string | null;
  role: string | null;
  roleLabel: string;
  roleConfidence: number | null;
  roleEvidence: string | null;
  /** The placement evidence (`segment_evidence_class`): the icon's ring. */
  evidence: string | null;
  occupancy: number | null;
  isGhost: boolean;
  listen: string[];
  dial: string[];
  /** Rule 4: on more than one bus; it sits on the lowest and draws a stub to each other one. */
  multi?: boolean;
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
  /** Rule 2: the router's one hue (`canvas/hues.ts`), on its ring, its legs and its buses' dots. */
  hue: string;
};
export type OverflowData = { kind: "overflow"; siteKey: string; hidden: number; total: number; expanded: boolean };
/** The Internet pill: short label and sub-line beside the icon (the slot
 *  has room for about twenty glyphs each), the full wording in `title`.
 *  Drawn only when the cast evidence has non-local peers or the config
 *  defines an uplink on a router; the gateway is folded into that router
 *  (`RouterData.uplink` / `crossing`), there is no WAN node. */
export type ChromeData = { kind: "internet"; label: string; sub: string; title: string };
export type UnplacedData = { kind: "unplaced"; count: number };
/** An annotation (Phase 2): a positioned SVG snippet, presentation only. */
export type AnnotationData = { kind: "annotation"; ann: Annotation };
export type CanvasData = SiteData | SegmentData | HostData | RungsData | RouterData | OverflowData | ChromeData | UnplacedData | AnnotationData;
export type CanvasNode = Node<CanvasData, string>;

export type ArcData = { kind: "arc"; key: string; arcKind: "observed" | "asserted"; bytes: number; flows: number; pairs: number; label: string };
/** A router's leg onto a bus (rule 2): a vertical drop in the router's hue.
 *  `folded`: the leg goes to a group's `+N more` lane for `folded` hidden
 *  segments at once (one edge, not one per hidden segment); it names no
 *  single attachment, so it has no label, no detach, no selection. */
export type LegData = { kind: "leg"; label: string; folded?: number; /** The site group the target lies in (the fan collapse groups by it). */ site?: string; hue: string };
/** Rule 4: a multi-homed host's stub from its icon to each bus it is on but does not sit on. */
export type StubData = { kind: "stub"; host: string; segment: string; label: string; folded?: number; site?: string };
/** Rule 2's fan (`canvas/fan.ts`): one trunk from a router to a site for `count` buses... */
export type FanData = { kind: "fan"; router: string; site: string; count: number; label: string; hue: string };
/** ...and the rail inside the site from that entry down to each bus. */
export type BusData = { kind: "bus"; router: string; site: string; segment: string; hue: string };
export type TrunkData = { kind: "trunk"; bytes: number; flows: number };
export type CanvasEdgeData = ArcData | LegData | StubData | FanData | BusData | TrunkData;
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
  /** Segment NAME -> the anchor it is drawn through (itself, or its group's `+N more` lane when collapsed). */
  anchorByName: Record<string, string>;
}

export interface PlacementOptions extends Level0Options {
  /** A multi-homed authored host's further attachments (rule 4). */
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
 *  of the fixed frame (chrome, a collapsed band, the `+N more` lane). A
 *  router is a node of the twin and pins as `host:<name>` like any node; a
 *  segment's pin is its rank in its site (`{ x: 0, y: rank }`). */
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

/**
 * The lanes of a site in slot order: a ranked item takes its slot (the next
 * free one when two claim the same), the rest fill the free slots in the
 * order given. A rank past the end lands at the end. Pure.
 */
export function orderByRank<T>(items: readonly T[], rankOf: (item: T) => number | null): T[] {
  const n = items.length;
  const slots: Array<T | undefined> = new Array<T | undefined>(n).fill(undefined);
  const ranked = items.map((t, i) => ({ t, i, r: rankOf(t) })).filter((x): x is { t: T; i: number; r: number } => x.r !== null).sort((a, b) => a.r - b.r || a.i - b.i);
  for (const p of ranked) {
    let s = Math.max(0, Math.min(n - 1, Math.round(p.r)));
    while (s < n && slots[s] !== undefined) s++;
    if (s >= n) s = slots.indexOf(undefined);
    if (s >= 0) slots[s] = p.t;
  }
  const rest = items.filter((t) => rankOf(t) === null);
  let k = 0;
  for (let s = 0; s < n; s++) if (slots[s] === undefined) slots[s] = rest[k++];
  return slots as T[];
}

// ---------------------------------------------------------------------------
// Icons on a bus: the lifted level-1 band rule gives role order, which band
// collapses and its rung tally; the row is the canvas's own.
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

export interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

function hostData(n: GraphNode, multi: boolean): HostData {
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
    ...(multi ? { multi: true } : {}),
  };
}

// ---------------------------------------------------------------------------
// The placement
// ---------------------------------------------------------------------------
interface Lane {
  id: string;
  type: "segment" | "overflow";
  data: SegmentData | OverflowData;
  /** Relative to the site. */
  rel: Point;
  w: number;
  h: number;
  rows: number;
  children: Array<{ id: string; type: string; data: CanvasData; rel: Point; w: number; h: number; pinKey: string | null }>;
}

interface PlacedSite {
  key: string;
  data: SiteData;
  abs: Point;
  w: number;
  h: number;
  lanes: Lane[];
}

/** A leg's layer: above a site (z 0), its lanes (z 1) and their icons (z 2),
 *  so a leg that crosses another box is drawn, not hidden behind it. */
export const EDGE_Z = 3;
/** The class on a leg edge: `canvas.css` takes its pointer events away, so a
 *  leg over a box never catches a drag or a click meant for the box; the pane
 *  hit-tests the legs itself (`nearestPolyline`). */
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

  // ---- the anchors: a shown segment is its own, a hidden one its group's `+N more` lane ----
  const anchorByName: Record<string, string> = {};
  const siteOfSegment = new Map<string, string>();
  for (const t of l0.tiles) {
    anchorByName[t.segment.name] = t.segment.name;
    siteOfSegment.set(t.segment.name, t.siteKey);
  }
  for (const o of l0.overflow) {
    for (const h of o.hidden) {
      anchorByName[h.name] = ID.overflow(o.siteKey);
      siteOfSegment.set(h.name, o.siteKey);
    }
  }
  const cidrOfName = new Map(graph.segments.map((s) => [s.name, s.observedCidr]));
  const nameOfCidr = new Map(graph.segments.map((s) => [s.observedCidr, s.name]));

  // ---- the lanes of each site, in rank order (a `segment:` pin is a rank) ----
  const rankOf = (cidr: string): number | null => {
    const p = pins[`segment:${cidr}`];
    return p && Number.isFinite(p.y) ? p.y : null;
  };
  const laneOrder = new Map<string, string[]>(); // site key -> segment names in slot order
  for (const sp of l0.sites) {
    const shown = l0.tiles.filter((t) => t.siteKey === sp.site.key).map((t) => t.segment);
    shown.sort((a, b) => compareCidr(a.observedCidr, b.observedCidr));
    laneOrder.set(sp.site.key, orderByRank(shown, (s) => rankOf(s.observedCidr)).map((s) => s.name));
  }
  const laneIndex = (name: string): number => laneOrder.get(siteOfSegment.get(name) ?? "")?.indexOf(name) ?? -1;

  // ---- rule 4: a multi-homed host sits on the LOWEST of its buses in its first
  // segment's site and draws a stub to each other bus it is on ----
  const extraByHost = new Map<string, ExtraLeg[]>();
  for (const leg of options.extraLegs ?? []) extraByHost.set(leg.host, [...(extraByHost.get(leg.host) ?? []), leg]);
  const multi = new Set<string>();
  const stubs: Array<{ host: string; segment: string; ip: string | null }> = [];
  for (const [host, extras] of extraByHost) {
    const node = graph.nodeById.get(host);
    const firstName = node?.segment ? nameOfCidr.get(node.segment) : undefined;
    if (!node || !firstName) continue;
    multi.add(host);
    const attachments = [{ segment: firstName, ip: node.address }, ...extras.map((e) => ({ segment: e.segment, ip: e.ip }))];
    const firstSite = siteOfSegment.get(firstName);
    let home = firstName;
    let homeIndex = laneIndex(firstName);
    for (const a of attachments) {
      if (siteOfSegment.get(a.segment) !== firstSite || anchorByName[a.segment] !== a.segment) continue;
      const idx = laneIndex(a.segment);
      if (idx > homeIndex) {
        home = a.segment;
        homeIndex = idx;
      }
    }
    if (home !== firstName) {
      const fromCidr = node.segment!;
      const toCidr = cidrOfName.get(home)!;
      const from = chipsByCidr.get(fromCidr) ?? [];
      const at = from.findIndex((c) => "node" in c && c.node.id === host);
      if (at >= 0) {
        const [spec] = from.splice(at, 1);
        chipsByCidr.set(toCidr, [...(chipsByCidr.get(toCidr) ?? []), spec!]);
      } else home = firstName; // collapsed into a rung band: it stays where the band is
    }
    for (const a of attachments) if (a.segment !== home) stubs.push({ host, segment: a.segment, ip: a.ip });
  }

  // ---- rule 2: one hue per router; the drop sources per site size the port region ----
  const hueByRouter = new Map(graph.routers.map((r, i) => [r.name, hueOf(i)]));
  const sourcesBySite = new Map<string, Set<string>>();
  const addSource = (site: string | undefined, key: string) => {
    if (!site) return;
    let s = sourcesBySite.get(site);
    if (!s) sourcesBySite.set(site, (s = new Set()));
    s.add(key);
  };
  for (const r of graph.routers) for (const name of r.segments) addSource(siteOfSegment.get(name), `router:${r.name}`);
  for (const s of stubs) addSource(siteOfSegment.get(s.segment), `host:${s.host}`);

  // ---- sites: lanes stacked at the site's inner width ----
  const sites: PlacedSite[] = [];
  for (const sp of l0.sites) {
    const key = sp.site.key;
    const order = laneOrder.get(key) ?? [];
    const overflow = l0.overflow.find((o) => o.siteKey === key) ?? null;
    const specs = order.map((name) => {
      const seg = graph.segmentByName.get(name)!;
      const chips = chipsByCidr.get(seg.observedCidr) ?? [];
      return { seg, chips, rows: laneRows(chips.length), contentW: laneContentWidth(chips.length) };
    });
    // At least as wide as the row of routers serving it (`DEVICE_W` each), so they share one row above rather than stacking.
    const routersHere = graph.routers.filter((r) => r.segments.some((name) => siteOfSegment.get(name) === key)).length;
    const routersW = routersHere ? routersHere * (DEVICE_W + DEVICE_GAP_X) - DEVICE_GAP_X + 2 * PORT_PAD : 0;
    const innerW = Math.max(BUS_MIN_W, routersW, Math.max(0, ...specs.map((s) => s.contentW)) + portRegionWidth(sourcesBySite.get(key)?.size ?? 0) + BUS_BADGE_W);
    const lanes: Lane[] = specs.map(({ seg, chips, rows }, rank) => ({
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
        served: graph.routers.filter((r) => r.segments.includes(seg.name)).map((r) => ({ router: r.name, hue: hueByRouter.get(r.name)! })),
        rank,
        rows,
      },
      rel: { x: 0, y: 0 },
      w: innerW,
      h: laneHeight(rows),
      rows,
      children: chips.map((c, i) =>
        "node" in c
          ? { id: ID.host(c.node.id), type: "host", data: hostData(c.node, multi.has(c.node.id)), rel: iconSlot(i, rows), w: BUS_ICON, h: BUS_ICON, pinKey: `host:${c.node.id}` }
          : { id: `${ID.segment(seg.name)}:rungs:${c.rungs.role}`, type: "rungs", data: c.rungs, rel: iconSlot(i, rows), w: BUS_ICON, h: BUS_ICON, pinKey: null },
      ),
    }));
    if (overflow) {
      lanes.push({
        id: ID.overflow(key),
        type: "overflow",
        data: { kind: "overflow", siteKey: key, hidden: overflow.hidden.length, total: overflow.total, expanded: overflow.expanded },
        rel: { x: 0, y: 0 },
        w: innerW,
        h: OVERFLOW_LANE_H,
        rows: 0,
        children: [],
      });
    }
    let y = SITE_HEADER_H + SITE_PAD;
    for (const lane of lanes) {
      lane.rel = { x: SITE_PAD, y };
      y += lane.h + BUS_GAP_Y;
    }
    const size = siteSize(innerW, lanes.map((l) => l.h));
    sites.push({ key, data: { kind: "site", key, label: sp.site.label, shown: specs.length, total: sp.site.segments.length, northSouthBytes: sp.site.northSouth.bytes }, abs: { x: 0, y: 0 }, w: size.w, h: size.h, lanes });
  }

  // ---- the sites in rows near a window's shape ----
  const perRow = chooseSitesPerRow(sites.map((s) => ({ w: s.w, h: s.h })));
  const rowsOfSites: PlacedSite[][] = [];
  for (let i = 0; i < sites.length; i += perRow) rowsOfSites.push(sites.slice(i, i + perRow));
  let rowY = 0;
  for (const row of rowsOfSites) {
    let cursorX = CANVAS_PAD;
    for (const s of row) {
      s.abs = { x: cursorX, y: rowY };
      cursorX += s.w + SITE_GAP_X;
    }
    rowY += Math.max(...row.map((s) => s.h)) + SITE_ROW_GAP_Y;
  }
  const sitesSize = planeSize(sites.map((s) => ({ w: s.w, h: s.h })), perRow);
  const unplacedW = l0.unplaced ? TILE_W : 0;
  const lastRow = rowsOfSites[rowsOfSites.length - 1] ?? [];
  const lastRowRight = lastRow.length ? Math.max(...lastRow.map((s) => s.abs.x + s.w)) : CANVAS_PAD;
  const unplacedX = lastRowRight + (lastRow.length ? SITE_GAP_X : 0);
  const contentW = Math.max(INTERNET_W, sitesSize.w - 2 * CANVAS_PAD, l0.unplaced ? unplacedX + unplacedW - CANVAS_PAD : 0);
  const sitesH = Math.max(TILE_H, sitesSize.h);

  // ---- routers: one box per router, over the lanes it serves ----
  const laneAbsX = new Map<string, { left: number; right: number }>();
  for (const s of sites) {
    for (const l of s.lanes) {
      const left = s.abs.x + l.rel.x;
      laneAbsX.set(l.id, { left, right: left + l.w });
    }
  }
  // A router's node is its DRAWN box (`DEVICE_W` wide: the icon with its name
  // and leg count). The lanes it serves decide where the icon goes: their
  // span's centre, or along the span to share a row.
  const routerSpans = graph.routers.map((r) => {
    const spans = r.segments
      .map((name) => laneAbsX.get(anchorByName[name] === name ? ID.segment(name) : (anchorByName[name] ?? "")))
      .filter((s): s is { left: number; right: number } => !!s);
    if (!spans.length) return { router: r, spanLeft: CANVAS_PAD, spanW: DEVICE_W, left: CANVAS_PAD, w: DEVICE_W, drawnLegs: 0 };
    const left = Math.min(...spans.map((s) => s.left));
    const right = Math.max(...spans.map((s) => s.right));
    const spanW = Math.max(DEVICE_W, right - left);
    return { router: r, spanLeft: left, spanW, left: left + spanW / 2 - DEVICE_W / 2, w: DEVICE_W, drawnLegs: spans.length };
  });
  routerSpans.sort((a, b) => a.spanLeft - b.spanLeft || (a.router.name < b.router.name ? -1 : 1));
  // A row holds every router whose box fits beside the others'; a router's
  // foot stays clear (no box in a lower row under another's icon, where its
  // legs leave straight down, nor in an upper row over its own).
  const rows: Array<Array<{ l: number; r: number; cx: number }>> = [];
  const under = (cx: number, b: { l: number; r: number }) => cx >= b.l - DEVICE_GAP_X && cx <= b.r + DEVICE_GAP_X;
  const W = DEVICE_W;
  const G = DEVICE_GAP_X;
  const boxAt = (cx: number) => ({ l: cx - W / 2, r: cx + W / 2, cx });
  const fits = (box: { l: number; r: number; cx: number }, i: number) => rows[i]!.every((b) => box.r + G <= b.l || box.l >= b.r + G) && rows.every((other, j) => j === i || other.every((b) => !under(b.cx, box) && !under(box.cx, b)));
  const routerRows = routerSpans.map((s) => {
    const cx0 = s.spanLeft + s.spanW / 2;
    const lo = s.spanLeft + W / 2;
    const hi = s.spanLeft + s.spanW - W / 2;
    const all = rows.flat();
    const candidates = [cx0, ...all.flatMap((b) => [b.r + G + W / 2 + 1, b.l - G - W / 2 - 1])].filter((x) => x === cx0 || (x >= lo && x <= hi)).sort((a, b) => Math.abs(a - cx0) - Math.abs(b - cx0));
    for (let i = 0; i < rows.length; i++) {
      for (const x of candidates) {
        const box = boxAt(x);
        if (fits(box, i)) {
          rows[i]!.push(box);
          s.left = box.l;
          return i;
        }
      }
    }
    let box = boxAt(cx0);
    const sorted = [...all].sort((a, b) => a.l - b.l);
    for (let guard = 0; guard < sorted.length + 1; guard++) {
      const hit = sorted.find((b) => under(b.cx, box) || under(box.cx, b));
      if (!hit) break;
      box = boxAt(hit.r + G + W / 2 + 1);
    }
    s.left = box.l;
    rows.push([box]);
    return rows.length - 1;
  });
  const infraH = rows.length ? rows.length * DEVICE_H + (rows.length - 1) * DEVICE_GAP_Y : 0;

  // ---- the tiers, stacked top to bottom with the lifted primitive ----
  // The Internet is drawn only on evidence of non-local peers or a configured
  // uplink; the gateway is a badge on its router, so a twin with neither
  // starts at its router.
  const uplinks = [...(options.uplinks ?? [])];
  const showInternet = graph.nonLocal.length > 0 || uplinks.length > 0;
  const crossing: Crossing | null = graph.wanEdge && graph.wanEdge.crossingEdges > 0 ? { edges: graph.wanEdge.crossingEdges, bytes: graph.wanEdge.bytes, flows: graph.wanEdge.flows, inferred: graph.wanEdge.inferred } : null;
  const gateways = uplinks.length ? uplinks : crossing && routerSpans.length === 1 ? [routerSpans[0]!.router.name] : [];
  const bands = stackBands([showInternet ? TIER_INTERNET_H : 0, infraH, sitesH], CANVAS_PAD, TIER_GAP_Y);
  const [internetBand, infraBand, segmentBand] = bands as [Box, Box, Box];
  for (const s of sites) s.abs = { x: s.abs.x, y: segmentBand.y + s.abs.y };

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
      // Over the site tints and lanes (0 / 1), under the legs (EDGE_Z): a router dragged into a site stays visible.
      zIndex: 2,
      ...common,
      data: { kind: "router", name: s.router.name, segments: [...s.router.segments], drawnLegs: s.drawnLegs, uplink: uplinks.includes(s.router.name), crossing: gateways.includes(s.router.name) ? crossing : null, hue: hueByRouter.get(s.router.name)! },
    });
  });
  for (const s of sites) {
    const siteAbs = pinned(s.key, s.abs);
    // The site's box is the union of its packed box and every lane's box with
    // its padding, so an icon dragged out never leaves a hole in the tint.
    const laneAbs = s.lanes.map((l) => ({ l, abs: { x: siteAbs.x + l.rel.x, y: siteAbs.y + l.rel.y } }));
    const siteOrigin = { x: siteAbs.x, y: siteAbs.y };
    const siteW = s.w;
    const siteH = s.h;
    // The tint is not a selection target: selected, React Flow would lift it over the routers and legs it lies under. It still drags (its pin).
    nodes.push({ id: ID.site(s.key), type: "site", position: siteOrigin, width: siteW, height: siteH, ...common, selectable: false, data: s.data });
    for (const { l, abs: laneAt } of laneAbs) {
      nodes.push({
        id: l.id,
        type: l.type,
        parentId: ID.site(s.key),
        position: { x: laneAt.x - siteOrigin.x, y: laneAt.y - siteOrigin.y },
        width: l.w,
        height: l.h,
        ...common,
        data: l.data,
      });
      for (const c of l.children) {
        const chipAbs = pinned(c.pinKey, { x: laneAt.x + c.rel.x, y: laneAt.y + c.rel.y });
        nodes.push({
          id: c.id,
          type: c.type,
          parentId: l.id,
          position: { x: chipAbs.x - laneAt.x, y: chipAbs.y - laneAt.y },
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
      position: { x: unplacedX, y: segmentBand.y },
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
  // Rule 2: a router's legs are the config's `nets[]`, one per bus, in its hue.
  for (const s of routerSpans) {
    const hue = hueByRouter.get(s.router.name)!;
    const foldedTo = new Map<string, number>();
    for (const name of s.router.segments) {
      const anchor = anchorByName[name];
      if (!anchor) continue;
      if (anchor !== name) {
        foldedTo.set(anchor, (foldedTo.get(anchor) ?? 0) + 1);
        continue;
      }
      edges.push({ id: `leg:${s.router.name}>${name}`, type: "leg", className: LEG_CLASS, source: ID.router(s.router.name), target: ID.segment(name), sourceHandle: HANDLE.bottom, targetHandle: HANDLE.top, zIndex: EDGE_Z, data: { kind: "leg", label: name, site: siteOfSegment.get(name), hue } });
    }
    // A router that joins segments behind a group's `+N more` lane draws ONE leg to that lane, however many it joins there.
    for (const [anchor, n] of foldedTo) {
      edges.push({ id: `legs:${s.router.name}>${anchor}`, type: "leg", className: LEG_CLASS, source: ID.router(s.router.name), target: anchor, sourceHandle: HANDLE.bottom, targetHandle: HANDLE.top, zIndex: EDGE_Z, data: { kind: "leg", label: `${n} segment${n === 1 ? "" : "s"} behind +N more`, folded: n, site: siteKeyOfOverflowAnchor(anchor) ?? undefined, hue } });
    }
  }
  // Rule 4: a multi-homed host's stubs, from its icon to each other bus it is on.
  const stubFolded = new Map<string, { host: string; anchor: string; n: number }>();
  for (const st of stubs) {
    const anchor = anchorByName[st.segment];
    if (!anchor) continue;
    if (anchor !== st.segment) {
      const k = `${st.host}>${anchor}`;
      const cur = stubFolded.get(k) ?? { host: st.host, anchor, n: 0 };
      cur.n += 1;
      stubFolded.set(k, cur);
      continue;
    }
    edges.push({ id: `stub:${st.host}>${st.segment}`, type: "leg", className: LEG_CLASS, source: ID.host(st.host), target: ID.segment(st.segment), sourceHandle: HANDLE.bottom, targetHandle: HANDLE.top, zIndex: EDGE_Z, data: { kind: "stub", host: st.host, segment: st.segment, label: st.ip ?? st.segment, site: siteOfSegment.get(st.segment) } });
  }
  for (const f of stubFolded.values()) {
    edges.push({ id: `stubs:${f.host}>${f.anchor}`, type: "leg", className: LEG_CLASS, source: ID.host(f.host), target: f.anchor, sourceHandle: HANDLE.bottom, targetHandle: HANDLE.top, zIndex: EDGE_Z, data: { kind: "stub", host: f.host, segment: f.anchor, label: `${f.n} segment${f.n === 1 ? "" : "s"} behind +N more`, folded: f.n, site: siteKeyOfOverflowAnchor(f.anchor) ?? undefined } });
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
