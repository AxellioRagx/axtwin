/**
 * The canvas's node and edge vocabulary -- the data every node and edge
 * carries, the ids, the pin keys -- shared by the device-tree placement
 * (`canvas/tree.ts`, track 5) and the components (`nodes.tsx`). The bus
 * placement of track 4 lived here until 2026-11-07; git has it.
 *
 * (The earlier header, kept for the vocabulary it explains:)
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

import type { Point } from "../layout/geometry";
import { segmentBands } from "./bands";
import type { RungTally } from "../model/budgets";
import type { ConsoleGraph, EvidenceMixEntry, GraphNode } from "../model/graph";
import { shortName } from "../model/detail";
import { roleBadgeLabel, roleBucket } from "../model/roles";
import type { Annotation } from "../edit/annotations";

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
  source?: Source | null;
  confidence?: number | null;
  /** The device the segment hangs under in the tree (a switch's name, a router's, or none). */
  carrier?: string | null;
  /** The tree's hue for this branch (its carrier's). */
  hue?: string;
  /** Host icons folded behind the pill's `+N` (the per-segment host budget); 0 when all are drawn. */
  hiddenChips?: number;
  /** Whether the host budget is lifted for this pill (its `+N` was clicked). */
  hostsOpen?: boolean;
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
/** Provenance (`docs/design/topology-layers.md` §3.1), drawn by the overlay. */
export type Source = "discovered" | "asserted" | "authored" | "unconfirmed";
export type HostData = {
  kind: "host";
  nodeId: string;
  /** The device tree: where the node came from (absent == authored) and how firmly. */
  source?: Source | null;
  confidence?: number | null;
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
  source?: Source | null;
  confidence?: number | null;
  segments: string[];
  drawnLegs: number;
  /** The config names this router as an uplink (a leg on an `egress: true` segment). */
  uplink: boolean;
  /** The crossing traffic this router carries as the gateway, or `null` when it is not one. */
  crossing: Crossing | null;
  /** Rule 2: the router's one hue (`canvas/hues.ts`), on its ring, its legs and its buses' dots. */
  hue: string;
};
export type OverflowData = { kind: "overflow"; siteKey: string; hidden: number; total: number; expanded: boolean; /** A site fold's own words (`10.20 · 6 segments`); absent on a `+N more` fold. */ label?: string; /** The hosts behind the fold. */ hosts?: number };
/** A switch (or a router drawn as a device in the tree): the device tree's own node
 *  (`docs/design/topology-layers.md` §2.2, §7.2). */
export type DeviceData = {
  kind: "device";
  name: string;
  deviceKind: "switch" | "router";
  role: string | null;
  /** `L2` / `L3` / `FW` from the role, else the kind. */
  badge: string;
  image: string | null;
  /** The segments the device's links carry (and its own legs), by name. */
  segments: string[];
  /** The ports in use, from its links. */
  ports: string[];
  hue: string;
  source?: Source | null;
  confidence?: number | null;
  /** A switch wired to nothing (a parked duplicate): drawn with a "not attached" badge (R3-2). */
  parked?: boolean;
};
/** A tap chip on a segment or a link (topology-layers §8). */
export type TapData = { kind: "tap"; id: string; target: string; scope: string; sensors: string[]; source?: Source | null };
/** The Internet pill: short label and sub-line beside the icon (the slot
 *  has room for about twenty glyphs each), the full wording in `title`.
 *  Drawn only when the cast evidence has non-local peers or the config
 *  defines an uplink on a router; the gateway is folded into that router
 *  (`RouterData.uplink` / `crossing`), there is no WAN node. */
export type ChromeData = { kind: "internet" | "uncarried"; label: string; sub: string; title: string };
export type UnplacedData = { kind: "unplaced"; count: number };
/** An annotation (Phase 2): a positioned SVG snippet, presentation only. */
export type AnnotationData = { kind: "annotation"; ann: Annotation };
export type CanvasData = SiteData | SegmentData | HostData | RungsData | RouterData | OverflowData | ChromeData | UnplacedData | AnnotationData | DeviceData | TapData;
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
/** A `links[]` entry drawn between two devices, or a device's carrying of a segment (topology-layers §2.3). */
export type LinkData = { kind: "link"; a: string; aPort: string; b: string; bPort: string; mode: string; native: string | null; vlans: string[]; state: string; hue: string; source?: Source | null; label: string; index: number };
export type CarryData = { kind: "carry"; device: string; segment: string; label: string; hue: string; /** The "not carried" group's members (no device). */ dashed?: boolean };
/** A segment whose only members are routers (a `/30` such as `fab-link`): a router-to-router link labelled with it. */
export type SegLinkData = { kind: "seglink"; segment: string; cidr: string; a: string; b: string; hue: string; source?: Source | null; label: string };
/** The orthogonal polyline the tree routed through the gutters (`canvas/tree.ts` `routeTree`), absolute on the
 *  plane, and where its two end nodes stood when it was routed: an edge whose end has since moved draws a smoothstep. */
export type EdgeRoute = { points: Array<{ x: number; y: number }>; at: [{ x: number; y: number }, { x: number; y: number }] };
export type CanvasEdgeData = (ArcData | LegData | StubData | FanData | BusData | TrunkData | LinkData | CarryData | SegLinkData) & { route?: EdgeRoute };
export type CanvasEdge = Edge<CanvasEdgeData, string>;

export interface Placement {
  nodes: CanvasNode[];
  edges: CanvasEdge[];
  width: number;
  height: number;
  /** Why the canvas must NOT draw this twin, or `null`. */
  overBudget: string | null;
  hiddenSegments: number;
  /** Segments inside closed site folds (each fold names its own count); the tree's only. */
  siteSegments?: number;
  /** The tree's rows: a carrier's node id -> its rail, an open site's tile id -> its row (left to right). */
  rows?: Record<string, string[]>;
  hiddenPairs: number;
  /** Segment NAME -> the anchor it is drawn through (itself, or its group's `+N more` lane when collapsed). */
  anchorByName: Record<string, string>;
  /** Local entities in no segment (O7): a count for the Notes tab, not a box on the drawing (R3-9). */
  unplaced?: number;
}

/** The id prefix of a fold box (a `+N more`, a site fold, an open fold's collapse tile). */
export const OVERFLOW_ANCHOR_PREFIX = "overflow:";

export function overflowAnchorKey(key: string): string {
  return `${OVERFLOW_ANCHOR_PREFIX}${key}`;
}

/** The expand key a fold id names, or `null` for an ordinary anchor. */
export function siteKeyOfOverflowAnchor(id: string): string | null {
  return id.startsWith(OVERFLOW_ANCHOR_PREFIX) ? id.slice(OVERFLOW_ANCHOR_PREFIX.length) : null;
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
    case "device":
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
// Icons on a pill: `bands.ts` gives role order, which band collapses and its
// rung tally; the row is the canvas's own.
// ---------------------------------------------------------------------------
export type ChipSpec = { node: GraphNode } | { rungs: RungsData };

export function chipsOf(graph: ConsoleGraph, cidr: string): { chips: ChipSpec[]; bands: number } {
  const bands = segmentBands(graph, cidr);
  if (!bands) return { chips: [], bands: 0 };
  const chips: ChipSpec[] = bands.flatMap((b): ChipSpec[] =>
    b.collapsed
      ? [{ rungs: { kind: "rungs", role: b.role, roleLabel: roleBadgeLabel(b.role), count: b.hosts.length, rungs: b.rungs } }]
      : b.hosts.map((node) => ({ node })),
  );
  return { chips, bands: bands.length };
}

export interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

export function hostData(n: GraphNode, multi: boolean): HostData {
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

/** A leg's layer: above a pill (z 1) and its icons (z 2), so a leg that
 *  crosses another box is drawn, not hidden behind it. */
export const EDGE_Z = 3;
/** The class on a leg edge: `canvas.css` takes its pointer events away, so a
 *  leg over a box never catches a drag or a click meant for the box; the pane
 *  hit-tests the legs itself (`nearestPolyline`). */
export const LEG_CLASS = "cv-leg-edge";
