/**
 * The device tree (brief "Canvas rendering"; `docs/design/topology-layers.md` §7;
 * review round 3, R3-4 / R3-5 / R3-8, 2026-11-07). A layered (Sugiyama) layout
 * from `@dagrejs/dagre` decides WHERE everything sits; what each segment holds
 * (its host icons, the role bands that collapse by occupancy rung) comes from
 * `chipsOf`. The tree reads top to bottom:
 *
 *   edge routers (an uplink, else the router with most segments) → the other
 *   routers by hops → core / distribution switches (by hops, a router-less
 *   component rooted at its most-linked switch) → access → ToR / leaf → hosts.
 *
 * Segments are not a rank. With switches present a segment is a LABEL -- on the
 * trunk that carries it, and on the host leaf (the pill) its hosts sit on, which
 * hangs under its deepest carrier; a router whose branch already reaches a pill
 * through a trunk draws no leg of its own to it. With no switch (canvas-test,
 * datacenter) the tree is router → pills → hosts, the same tree. A segment whose
 * only members are routers (a `/30` such as `fab-link`) is a router-to-router
 * link labelled with it, never a pill.
 *
 * Edges are orthogonal and routed through the gutters between ranks
 * (`orthogonal.ts`), so none crosses a box. One hue per router only when there
 * are two or more; a one-router twin is neutral.
 *
 * Folding, folded by default: a carrier shows at most `fan` items and a
 * `+N more` for the rest; segments of one site (two-octet prefix) under one
 * carrier fold into one site pill until clicked; a pill shows at most
 * `TREE_HOST_BUDGET` host icons and a `+N`; the `×N` badge folds a group's
 * instances. A click expands in place and the tree re-ranks around it.
 *
 * Positions: dagre's, then the side-car's pins override them (absolute
 * top-lefts, plane epoch 6); Re-layout drops the pins. Pure -- no React, no DOM.
 */
import * as dagre from "@dagrejs/dagre";

import { CANVAS_PAD, DEVICE_H, DEVICE_W, INTERNET_W, TIER_INTERNET_H, TILE_W } from "../layout/geometry";
import { NO_PINS, type Pins } from "../layout/pins";
import { BUDGET_LEVEL1_BANDS } from "../model/budgets";
import type { ConsoleGraph, GraphSegment } from "../model/graph";
import { compareCidr, twoOctetPrefix } from "../model/keys";
import { switchBadge } from "../model/roles";
import type { ExtraLeg, TreeLink, TreeStructure } from "./authored";
import { BUS_ICON, OVERFLOW_LANE_H, iconSlot, laneHeight, laneRows } from "./bus";
import { HUE_GREY, hueOf } from "./hues";
import { routeOrthogonal, type Box, type RouteRequest } from "./orthogonal";
import { selectPairs } from "./pairs";
import { EDGE_Z, HANDLE, ID, LEG_CLASS, chipsOf, overflowAnchorKey, siteKeyOfOverflowAnchor, hostData, type CanvasEdge, type CanvasNode, type Crossing, type DeviceData, type Placement, type SegmentData, type Source } from "./placement";

export interface TreeOptions {
  /** The expand keys the operator opened (`carrier:`, `site:`, `hosts:`). */
  expanded?: readonly string[];
  /** Draw every observed pair, not the busiest `PAIR_TOP_K`. */
  showAllPairs?: boolean;
  /** A multi-homed host's further attachments (its stubs). */
  extraLegs?: readonly ExtraLeg[];
  /** The routers the config names as uplinks (`AuthoredGraph.uplinks`). */
  uplinks?: readonly string[];
  /** The switches, links and provenance the bipartite graph cannot carry. */
  tree?: TreeStructure;
  /** How many items (pills, site folds) a carrier shows before folding the rest into `+N more` (the rail's Fan-out). */
  fan?: number;
  /** The tap plan's bindings (topology-layers §8): a chip on each segment or device a tap sits on. */
  taps?: readonly TreeTap[];
  /** What a missing `source` means (review R3-1): `discovered` on a twin cast from an imprint
   *  (a cast older than track 3 wrote none), `authored` on a hand-authored twin. */
  defaultSource?: Source;
}

export interface TreeTap {
  index: number;
  scope: string;
  /** The segments (a `segment` / `boundary` tap) or the device (a `port` tap) it sits on. */
  targets: string[];
  /** A `port` tap's segment: where the chip goes when the port is a host's (the host sits on a pill). */
  segment?: string | null;
  sensors: string[];
  source: string | null;
}

/** The size of a tap chip on a pill's or a device's top edge. */
export const TAP_CHIP_W = 44;
export const TAP_CHIP_H = 18;

/** The pill's label column, left of its icons. */
export const PILL_LABEL_W = 132;
/** The pill's padding right of its last icon (the count badge sits there). */
export const PILL_PAD_RIGHT = 44;
/** The dagre spacing: between siblings, and between ranks (the gutter the edges run in). */
export const TREE_NODE_SEP = 36;
export const TREE_RANK_SEP = 72;
/** The default fold: a carrier with more items than this folds the rest. */
export const TREE_FAN_DEFAULT = 4;
/** The per-segment host budget: a pill draws this many host icons and folds the rest behind `+N`. */
export const TREE_HOST_BUDGET = 8;
/** The hue of every branch on a one-router twin (R3-5: hues only distinguish two or more routers). */
export const HUE_NEUTRAL = "#5f6b7a";
/** The tree's box budget: the pills, folds and devices it draws at once
 *  (a pill is one box however many icons it holds). Past it the page falls
 *  back to the list; the folds are what keep a large twin under it. */
export const TREE_BUDGET_BOXES = 120;

export const TREE_ID = {
  device: (name: string) => `device:${name}`,
  carrierOverflow: (carrier: string) => overflowAnchorKey(`carrier:${carrier}`),
  siteFold: (carrier: string, site: string) => overflowAnchorKey(`site:${carrier}|${site}`),
  link: (index: number) => `link:${index}`,
  carry: (device: string, segment: string) => `carry:${device}>${segment}`,
  segLink: (segment: string) => `seglink:${segment}`,
};

/** The expand key of a pill's host fold (`+N` on the pill). */
export const hostsKey = (segment: string) => `hosts:${segment}`;

/** The id of a device node: a switch is `device:<name>`, a router `router:<name>`. */
export function deviceNodeId(name: string, routers: ReadonlySet<string>): string {
  return routers.has(name) ? ID.router(name) : TREE_ID.device(name);
}

/** The segments a link carries, native first. */
export function linkSegments(link: Pick<TreeLink, "native" | "vlans">): string[] {
  const out: string[] = [];
  for (const s of [...(link.native ? [link.native] : []), ...link.vlans]) if (!out.includes(s)) out.push(s);
  return out;
}

/** A compact pill's glyph is this wide (`canvas.css` `.cv-glyph`), so a pill
 *  is at least as wide as its role glyphs when the zoom folds its icons. */
export const PILL_GLYPH_W = 60;

/** The pill's size for `icons` host icons and `badges` role glyphs: the label
 *  column, the wider of the icon row and the glyph row, the badge's room. */
export function pillSize(icons: number, badges = 0): { w: number; h: number; rows: number } {
  const rows = laneRows(icons);
  const perRow = Math.min(Math.max(icons, 1), 10);
  const iconsW = perRow * (BUS_ICON + 8);
  const glyphsW = Math.min(badges, 6) * PILL_GLYPH_W;
  return { w: PILL_LABEL_W + Math.max(iconsW, glyphsW) + PILL_PAD_RIGHT, h: laneHeight(rows), rows };
}

interface Device {
  name: string;
  kind: "switch" | "router";
  role: string | null;
  image: string | null;
  /** A router's legs / a switch's own L2 ports, by segment name. */
  legs: string[];
}

/** A router-to-router connection through a segment only routers are on. */
export interface RouterPair {
  segment: string;
  a: string;
  b: string;
}

/** The edge routers (rank 0): those the config names as uplinks, else the router with most segments. */
export function edgeRouters(devices: readonly Device[], uplinks: readonly string[]): string[] {
  const routers = devices.filter((d) => d.kind === "router");
  const named = routers.filter((r) => uplinks.includes(r.name)).map((r) => r.name);
  if (named.length) return named;
  const top = [...routers].sort((a, b) => b.legs.length - a.legs.length || (a.name < b.name ? -1 : 1))[0];
  return top ? [top.name] : [];
}

/** Breadth-first from the edge routers over the device links (and the router
 *  pairs): each device's depth and the router its branch descends from. A
 *  router no edge router reaches is a root of its own at 0; a switch no router
 *  reaches hangs at 1 under the most-linked switch of its component (`root`
 *  `null`). Pure, exported for the tests. */
export function treeDepths(devices: readonly Device[], links: readonly TreeLink[], roots?: readonly string[], pairs: readonly RouterPair[] = []): Map<string, { depth: number; root: string | null }> {
  const byName = new Map(devices.map((d) => [d.name, d]));
  const adj = new Map<string, string[]>();
  const join = (a: string, b: string) => {
    adj.set(a, [...(adj.get(a) ?? []), b]);
    adj.set(b, [...(adj.get(b) ?? []), a]);
  };
  for (const l of links) {
    if (l.state !== "up") continue;
    if (!byName.has(l.a.device) || !byName.has(l.b.device)) continue;
    join(l.a.device, l.b.device);
  }
  for (const p of pairs) if (byName.has(p.a) && byName.has(p.b)) join(p.a, p.b);
  const out = new Map<string, { depth: number; root: string | null }>();
  const bfs = (start: string[], depth: number, root: (s: string) => string | null) => {
    const queue: string[] = [];
    for (const s of start) {
      if (out.has(s)) continue;
      out.set(s, { depth, root: root(s) });
      queue.push(s);
    }
    while (queue.length) {
      const cur = queue.shift()!;
      const at = out.get(cur)!;
      for (const next of adj.get(cur) ?? []) {
        if (out.has(next)) continue;
        const d = byName.get(next)!;
        out.set(next, { depth: at.depth + 1, root: d.kind === "router" && at.root === null ? next : at.root });
        queue.push(next);
      }
    }
  };
  const routers = devices.filter((d) => d.kind === "router").map((d) => d.name);
  bfs(roots ? roots.filter((r) => byName.has(r)) : routers, 0, (s) => s);
  // Routers no edge router reaches: each a root of its own branch.
  bfs(routers.filter((r) => !out.has(r)), 0, (s) => s);
  // Router-less switch components: rooted at their most-linked switch, one rank down.
  const degree = (n: string) => (adj.get(n) ?? []).length;
  for (;;) {
    const rest = devices.filter((d) => !out.has(d.name)).sort((a, b) => degree(b.name) - degree(a.name) || (a.name < b.name ? -1 : 1));
    if (!rest.length) break;
    bfs([rest[0]!.name], 1, () => null);
  }
  return out;
}

/** Which device each segment hangs under: the deepest switch whose links or
 *  own ports carry it, else `null` (it then hangs under its routers' legs).
 *  Pure, exported for the tests. */
export function carriersOf(segments: readonly string[], devices: readonly Device[], links: readonly TreeLink[], depths: ReadonlyMap<string, { depth: number }>): Map<string, string | null> {
  const carrying = new Map<string, Set<string>>();
  const add = (seg: string, dev: string) => {
    let s = carrying.get(seg);
    if (!s) carrying.set(seg, (s = new Set()));
    s.add(dev);
  };
  const switches = new Set(devices.filter((d) => d.kind === "switch").map((d) => d.name));
  for (const d of devices) if (d.kind === "switch") for (const seg of d.legs) add(seg, d.name);
  for (const l of links) {
    if (l.state !== "up") continue;
    for (const end of [l.a, l.b]) if (switches.has(end.device)) for (const seg of linkSegments(l)) add(seg, end.device);
  }
  const out = new Map<string, string | null>();
  for (const seg of segments) {
    const cands = [...(carrying.get(seg) ?? [])].sort((a, b) => (depths.get(b)?.depth ?? 0) - (depths.get(a)?.depth ?? 0) || (a < b ? -1 : 1));
    out.set(seg, cands[0] ?? null);
  }
  return out;
}

/** The segments only routers are on (no host, no ghost, no switch carrying them) with two or more router legs. */
export function routerPairs(graph: ConsoleGraph, carried: ReadonlySet<string>): RouterPair[] {
  const out: RouterPair[] = [];
  for (const seg of graph.segments) {
    if (seg.hostCount > 0 || seg.ghostCount > 0 || carried.has(seg.name)) continue;
    const on = graph.routers.filter((r) => r.segments.includes(seg.name)).map((r) => r.name);
    if (on.length < 2) continue;
    for (let i = 1; i < on.length; i++) out.push({ segment: seg.name, a: on[0]!, b: on[i]! });
  }
  return out;
}

export function placeTree(graph: ConsoleGraph, pins: Pins = NO_PINS, options: TreeOptions = {}): Placement {
  // What is drawn: every segment (the tree folds per carrier and site below).
  const tree = options.tree ?? { devices: [], links: [], sources: {}, confidences: {} };
  const fan = options.fan ?? TREE_FAN_DEFAULT;
  const expanded = new Set(options.expanded ?? []);
  const empty = (overBudget: string | null): Placement => ({ nodes: [], edges: [], width: 0, height: 0, overBudget, hiddenSegments: 0, hiddenPairs: 0, anchorByName: {} });

  const allShown: GraphSegment[] = [...graph.segments].sort((a, b) => compareCidr(a.observedCidr, b.observedCidr));
  const nameOfCidr = new Map(graph.segments.map((s) => [s.observedCidr, s.name]));
  const fallbackSource: Source | null = options.defaultSource ?? null;
  const source = (name: string): Source | null => (tree.sources[name] as Source | undefined) ?? fallbackSource;
  const confidence = (name: string): number | null => tree.confidences[name] ?? null;

  // ---- the devices: routers (the graph's) and switches (the tree's) ----
  const routerNames = new Set(graph.routers.map((r) => r.name));
  const devices: Device[] = [
    ...graph.routers.map((r): Device => ({ name: r.name, kind: "router", role: null, image: null, legs: [...r.segments] })),
    ...tree.devices.filter((d) => !routerNames.has(d.name)).map((d): Device => ({ name: d.name, kind: d.kind, role: d.role, image: d.image, legs: [...d.segments] })),
  ];
  const deviceNames = new Set(devices.map((d) => d.name));
  const links = tree.links.filter((l) => deviceNames.has(l.a.device) || deviceNames.has(l.b.device));
  const uplinks = [...(options.uplinks ?? [])];
  // Carriers first on bare depths (routers at 0), to know which segments a switch carries.
  const carried = new Set([...carriersOf(allShown.map((s) => s.name), devices, links, treeDepths(devices, links)).entries()].filter(([, c]) => c !== null).map(([s]) => s));
  const pairs = routerPairs(graph, carried);
  const pairSegments = new Set(pairs.map((p) => p.segment));
  const depths = treeDepths(devices, links, edgeRouters(devices, uplinks), pairs);
  const multiRouter = graph.routers.length >= 2;
  const hueByRouter = new Map(graph.routers.map((r, i) => [r.name, multiRouter ? hueOf(i) : HUE_NEUTRAL]));
  const plainHue = multiRouter ? HUE_GREY : HUE_NEUTRAL;
  const hueOfDevice = (name: string): string => {
    const root = depths.get(name)?.root;
    return (root && hueByRouter.get(root)) ?? hueByRouter.get(name) ?? plainHue;
  };
  const shown = allShown.filter((s) => !pairSegments.has(s.name));
  const carrierOf = carriersOf(shown.map((s) => s.name), devices, links, depths);

  const chipsByCidr = new Map<string, ReturnType<typeof chipsOf>["chips"]>();
  for (const seg of shown) {
    const { chips, bands } = chipsOf(graph, seg.observedCidr);
    if (bands > BUDGET_LEVEL1_BANDS) return empty(`segment ${seg.observedCidr} holds ${bands} role bands, over the band budget of ${BUDGET_LEVEL1_BANDS}`);
    chipsByCidr.set(seg.observedCidr, chips);
  }

  // ---- a multi-homed host sits on its first segment's pill, with a stub to each other one ----
  const extraByHost = new Map<string, ExtraLeg[]>();
  for (const leg of options.extraLegs ?? []) extraByHost.set(leg.host, [...(extraByHost.get(leg.host) ?? []), leg]);
  const stubs: Array<{ host: string; segment: string; ip: string | null }> = [];
  for (const [host, extras] of extraByHost) {
    if (!graph.nodeById.get(host)) continue;
    for (const e of extras) stubs.push({ host, segment: e.segment, ip: e.ip });
  }

  // ---- folding: per carrier, by site; a site of several segments folds into one pill; `fan` items, then `+N more` ----
  const NO_CARRIER = "none";
  const carrierKey = (seg: GraphSegment): string => {
    const c = carrierOf.get(seg.name);
    if (c) return c;
    const r = graph.routers.find((x) => x.segments.includes(seg.name));
    return r ? r.name : NO_CARRIER;
  };
  type Item = { kind: "pill"; seg: GraphSegment } | { kind: "site"; site: string; segs: GraphSegment[] };
  const byCarrier = new Map<string, GraphSegment[]>();
  for (const seg of shown) byCarrier.set(carrierKey(seg), [...(byCarrier.get(carrierKey(seg)) ?? []), seg]);
  const anchorByName: Record<string, string> = {};
  const drawnSegments: GraphSegment[] = [];
  const siteFolds: Array<{ carrier: string; site: string; segs: GraphSegment[] }> = [];
  const folded = new Map<string, { hidden: GraphSegment[]; total: number; items: number }>();
  const openFolds: Array<{ carrier: string; key: string; total: number }> = [];
  for (const [carrier, segs] of byCarrier) {
    const bySite = new Map<string, GraphSegment[]>();
    for (const s of segs) {
      const site = twoOctetPrefix(s.observedCidr) ?? s.observedCidr;
      bySite.set(site, [...(bySite.get(site) ?? []), s]);
    }
    const items: Item[] = [];
    for (const [site, group] of bySite) {
      const siteKey = `site:${carrier}|${site}`;
      // One site under the carrier is not worth a fold of its own: its pills stand under the carrier's budget.
      if (group.length === 1 || bySite.size === 1 || expanded.has(siteKey)) {
        for (const seg of group) items.push({ kind: "pill", seg });
        if (group.length > 1 && bySite.size > 1) openFolds.push({ carrier, key: siteKey, total: group.length });
      } else items.push({ kind: "site", site, segs: group });
    }
    const carrierOpen = expanded.has(`carrier:${carrier}`);
    const open = carrierOpen || items.length <= fan;
    const visible = open ? items : items.slice(0, fan);
    for (const it of visible) {
      if (it.kind === "pill") {
        anchorByName[it.seg.name] = it.seg.name;
        drawnSegments.push(it.seg);
      } else {
        siteFolds.push({ carrier, site: it.site, segs: it.segs });
        for (const s of it.segs) anchorByName[s.name] = TREE_ID.siteFold(carrier, it.site);
      }
    }
    if (!open) {
      const rest = items.slice(fan);
      const hidden = rest.flatMap((it) => (it.kind === "pill" ? [it.seg] : it.segs));
      folded.set(carrier, { hidden, total: segs.length, items: rest.length });
      for (const s of hidden) anchorByName[s.name] = TREE_ID.carrierOverflow(carrier);
    } else if (carrierOpen && items.length > fan) openFolds.push({ carrier, key: `carrier:${carrier}`, total: segs.length });
  }
  const hiddenSegments = [...folded.values()].reduce((n, f) => n + f.hidden.length, 0) + siteFolds.reduce((n, f) => n + f.segs.length, 0);
  const boxCount = drawnSegments.length + siteFolds.length + folded.size + openFolds.length + devices.length + 2;
  if (boxCount > TREE_BUDGET_BOXES) return empty(`${boxCount} boxes exceed the canvas budget of ${TREE_BUDGET_BOXES} after folding (the tree's cap)`);

  // ---- the Internet and the gateways ----
  const showInternet = graph.nonLocal.length > 0 || uplinks.length > 0;
  const crossing: Crossing | null = graph.wanEdge && graph.wanEdge.crossingEdges > 0 ? { edges: graph.wanEdge.crossingEdges, bytes: graph.wanEdge.bytes, flows: graph.wanEdge.flows, inferred: graph.wanEdge.inferred } : null;
  const gateways = uplinks.length ? uplinks : crossing || graph.nonLocal.length ? edgeRouters(devices, []) : [];

  // ---- the legs a router draws: not to a pill its own branch reaches by a trunk, not to a router pair ----
  const nodeIdOf = (name: string) => deviceNodeId(name, routerNames);
  const legsOf = (r: { name: string; segments: readonly string[] }): string[] =>
    r.segments.filter((name) => {
      if (pairSegments.has(name)) return false;
      const c = carrierOf.get(name);
      return !(c && depths.get(c)?.root === r.name);
    });

  // ---- dagre: devices, pills, folds; every structural edge between them ranks them ----
  const g = new dagre.graphlib.Graph({ multigraph: false, compound: false });
  g.setGraph({ rankdir: "TB", nodesep: TREE_NODE_SEP, ranksep: TREE_RANK_SEP, edgesep: 12, marginx: CANVAS_PAD, marginy: CANVAS_PAD });
  g.setDefaultEdgeLabel(() => ({}));
  const INTERNET_H = TIER_INTERNET_H - 12;
  const UNCARRIED_ID = "chrome:uncarried";
  if (showInternet) g.setNode(ID.internet, { width: INTERNET_W, height: INTERNET_H });
  for (const d of devices) g.setNode(nodeIdOf(d.name), { width: DEVICE_W, height: DEVICE_H });
  const sizeOf = new Map<string, ReturnType<typeof pillSize>>();
  const visibleChips = new Map<string, ReturnType<typeof chipsOf>["chips"]>();
  const hiddenChipsOf = new Map<string, number>();
  for (const seg of drawnSegments) {
    const chips = chipsByCidr.get(seg.observedCidr) ?? [];
    const open = expanded.has(hostsKey(seg.name));
    const vis = open || chips.length <= TREE_HOST_BUDGET ? chips : chips.slice(0, TREE_HOST_BUDGET);
    visibleChips.set(seg.name, vis);
    hiddenChipsOf.set(seg.name, chips.length - vis.length);
    const size = pillSize(vis.length + (chips.length > vis.length ? 1 : 0), seg.roleBadges.length + (seg.ghostCount ? 1 : 0));
    sizeOf.set(seg.name, size);
    g.setNode(ID.segment(seg.name), { width: size.w, height: size.h });
  }
  for (const f of siteFolds) g.setNode(TREE_ID.siteFold(f.carrier, f.site), { width: TILE_W, height: OVERFLOW_LANE_H });
  for (const carrier of folded.keys()) g.setNode(TREE_ID.carrierOverflow(carrier), { width: TILE_W, height: OVERFLOW_LANE_H });
  const openFoldId = (key: string) => overflowAnchorKey(`open:${key}`);
  for (const f of openFolds) g.setNode(openFoldId(f.key), { width: TILE_W, height: OVERFLOW_LANE_H });
  const rankEdge = (a: string, b: string) => {
    if (a !== b && g.hasNode(a) && g.hasNode(b) && !g.hasEdge(b, a)) g.setEdge(a, b);
  };
  const shallow = (x: string, y: string): [string, string] => ((depths.get(x)?.depth ?? 0) <= (depths.get(y)?.depth ?? 0) ? [x, y] : [y, x]);
  const pillOrFold = (seg: string): string | null => {
    const a = anchorByName[seg];
    return a ? (a === seg ? ID.segment(seg) : a) : null;
  };
  for (const name of gateways) if (showInternet && deviceNames.has(name)) rankEdge(ID.internet, nodeIdOf(name));
  for (const p of pairs) {
    const [a, b] = shallow(p.a, p.b);
    rankEdge(nodeIdOf(a), nodeIdOf(b));
  }
  for (const l of links) {
    if (deviceNames.has(l.a.device) && deviceNames.has(l.b.device)) {
      const [a, b] = shallow(l.a.device, l.b.device);
      rankEdge(nodeIdOf(a), nodeIdOf(b));
    }
  }
  for (const seg of drawnSegments) {
    const carrier = carrierKey(seg);
    if (deviceNames.has(carrier)) rankEdge(nodeIdOf(carrier), ID.segment(seg.name));
  }
  for (const f of siteFolds) if (deviceNames.has(f.carrier)) rankEdge(nodeIdOf(f.carrier), TREE_ID.siteFold(f.carrier, f.site));
  for (const carrier of folded.keys()) if (deviceNames.has(carrier)) rankEdge(nodeIdOf(carrier), TREE_ID.carrierOverflow(carrier));
  for (const f of openFolds) if (deviceNames.has(f.carrier)) rankEdge(nodeIdOf(f.carrier), openFoldId(f.key));
  for (const r of graph.routers) for (const name of legsOf(r)) {
    const t = pillOrFold(name);
    if (t) rankEdge(ID.router(r.name), t);
  }
  // A link from a device to a host ranks the device above the host's pill.
  const pillOfHost = (host: string): string | null => {
    const seg = graph.nodeById.get(host)?.segment;
    const name = seg ? nameOfCidr.get(seg) : undefined;
    return name ? pillOrFold(name) : null;
  };
  for (const l of links) {
    for (const [dev, other] of [[l.a.device, l.b.device], [l.b.device, l.a.device]] as const) {
      if (!deviceNames.has(dev) || deviceNames.has(other)) continue;
      const pill = pillOfHost(other);
      if (pill) rankEdge(nodeIdOf(dev), pill);
    }
  }
  // Items no device carries (a segment no router has a leg on, no switch carries). A host with a leg
  // on two segments bridges them (corp-twin's bridging hosts, `role: l2-switch` with `l2: true` legs):
  // an uncarried item a bridge reaches from a ranked one hangs under that one's pill, the stub its
  // edge. What no bridge reaches hangs under a visible "not carried" box one rank down, labelled,
  // not beside the routers at rank 0 where it would read as a gateway.
  const itemOf = (seg: GraphSegment): string | null => pillOrFold(seg.name);
  const ranked = new Set<string>();
  const uncarriedItems = new Set<string>();
  for (const seg of shown) {
    const it = itemOf(seg);
    if (!it) continue;
    (carrierKey(seg) === NO_CARRIER ? uncarriedItems : ranked).add(it);
  }
  for (const it of ranked) uncarriedItems.delete(it);
  if (folded.has(NO_CARRIER)) uncarriedItems.add(TREE_ID.carrierOverflow(NO_CARRIER));
  const bridges: Array<[string, string, string]> = [];
  for (const st of stubs) {
    const a = pillOfHost(st.host);
    const b = pillOrFold(st.segment);
    if (a && b && a !== b) bridges.push([a, b, st.host]);
  }
  /** The bridge edges that ranked an item: `from>to` -> the hosts bridging them. */
  const bridged = new Map<string, { from: string; to: string; hosts: string[] }>();
  for (let grew = true; grew; ) {
    grew = false;
    for (const [a, b] of bridges) {
      const [from, to] = ranked.has(a) && uncarriedItems.has(b) ? [a, b] : ranked.has(b) && uncarriedItems.has(a) ? [b, a] : [null, null];
      if (!from || !to) continue;
      rankEdge(from, to);
      bridged.set(`${from}>${to}`, { from, to, hosts: [] });
      uncarriedItems.delete(to);
      ranked.add(to);
      grew = true;
    }
  }
  const notCarried = [...uncarriedItems];
  if (devices.length && notCarried.length) {
    g.setNode(UNCARRIED_ID, { width: INTERNET_W, height: INTERNET_H });
    for (const [a, b, host] of bridges) {
    const e = bridged.get(`${a}>${b}`) ?? bridged.get(`${b}>${a}`);
    if (e && !e.hosts.includes(host)) e.hosts.push(host);
  }
  for (const id of notCarried) rankEdge(UNCARRIED_ID, id);
  }
  dagre.layout(g);

  const topLeft = (id: string): { x: number; y: number } => {
    const n = g.node(id);
    return n ? { x: n.x - n.width / 2, y: n.y - n.height / 2 } : { x: CANVAS_PAD, y: CANVAS_PAD };
  };
  const pinned = (key: string | null, abs: { x: number; y: number }) => {
    const p = key ? pins[key] : undefined;
    return p ? { x: p.x, y: p.y } : abs;
  };
  const { width: gw, height: gh } = g.graph() as { width?: number; height?: number };

  // ---- nodes ----
  const nodes: CanvasNode[] = [];
  const common = { connectable: false, selectable: true } as const;
  if (showInternet) {
    nodes.push({
      id: ID.internet,
      type: "chrome",
      position: topLeft(ID.internet),
      width: INTERNET_W,
      height: INTERNET_H,
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
  const portsOf = (name: string): string[] => links.flatMap((l) => [l.a, l.b].filter((e) => e.device === name).map((e) => e.port));
  const carriedBy = (name: string): string[] => {
    const out = new Set<string>();
    for (const l of links) if (l.state === "up" && (l.a.device === name || l.b.device === name)) for (const s of linkSegments(l)) out.add(s);
    return [...out];
  };
  for (const d of devices) {
    const id = nodeIdOf(d.name);
    const abs = pinned(`host:${d.name}`, topLeft(id));
    if (d.kind === "router") {
      const r = graph.routers.find((x) => x.name === d.name)!;
      nodes.push({
        id,
        type: "router",
        position: abs,
        width: DEVICE_W,
        height: DEVICE_H,
        zIndex: 2,
        ...common,
        data: { kind: "router", name: d.name, segments: [...r.segments], drawnLegs: legsOf(r).filter((s) => anchorByName[s]).length, uplink: uplinks.includes(d.name), crossing: gateways.includes(d.name) ? crossing : null, hue: hueByRouter.get(d.name)!, source: source(d.name), confidence: confidence(d.name) },
      });
      continue;
    }
    const data: DeviceData = {
      kind: "device",
      name: d.name,
      deviceKind: d.kind,
      role: d.role,
      badge: d.role ? switchBadge(d.role) : d.kind === "switch" ? "SW" : "RTR",
      image: d.image,
      segments: [...new Set([...d.legs, ...carriedBy(d.name)])],
      ports: portsOf(d.name),
      hue: hueOfDevice(d.name),
      source: source(d.name),
      confidence: confidence(d.name),
      // No router reaches it -- by links, or by a segment it shares with a router's legs (`edit/subtree.ts` `parkedDevices`).
      parked: d.kind === "switch" && graph.routers.length > 0 && depths.get(d.name)?.root == null && ![...d.legs, ...carriedBy(d.name)].some((seg) => graph.routers.some((r) => r.segments.includes(seg))),
    };
    // R3-7: a switch is its own node type, so the DOM (and a test) can tell it from any other device.
    nodes.push({ id, type: d.kind === "switch" ? "switch" : "device", position: abs, width: DEVICE_W, height: DEVICE_H, zIndex: 2, ...common, data });
  }
  if (g.hasNode(UNCARRIED_ID)) {
    const n = notCarried.length;
    nodes.push({ id: UNCARRIED_ID, type: "chrome", position: topLeft(UNCARRIED_ID), width: INTERNET_W, height: INTERNET_H, draggable: false, ...common, data: { kind: "uncarried", label: "Not carried", sub: `${n} item${n === 1 ? "" : "s"} no device reaches`, title: "No router leg, switch or bridging host in the config reaches these segments: add a router leg or a link to place them in the tree" } });
  }
  const multi = new Set(extraByHost.keys());
  for (const seg of drawnSegments) {
    const id = ID.segment(seg.name);
    const size = sizeOf.get(seg.name)!;
    const abs = pinned(`segment:${seg.observedCidr}`, topLeft(id));
    const carrier = carrierOf.get(seg.name) ?? null;
    const served = graph.routers.filter((r) => r.segments.includes(seg.name)).map((r) => ({ router: r.name, hue: hueByRouter.get(r.name)! }));
    const hue = carrier ? hueOfDevice(carrier) : (served[0]?.hue ?? plainHue);
    const data: SegmentData = {
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
      served: multiRouter ? served : [],
      rows: size.rows,
      carrier,
      hue,
      source: source(seg.name),
      confidence: confidence(seg.name),
      hiddenChips: hiddenChipsOf.get(seg.name) ?? 0,
      hostsOpen: expanded.has(hostsKey(seg.name)),
    };
    nodes.push({ id, type: "segment", position: abs, width: size.w, height: size.h, zIndex: 1, ...common, data });
    const chips = visibleChips.get(seg.name) ?? [];
    chips.forEach((c, i) => {
      const slot = iconSlot(i, size.rows);
      // The label column is narrower than a bus's: shift the lifted slot left by the difference.
      const rel = { x: slot.x - (168 - PILL_LABEL_W), y: slot.y };
      if ("node" in c) {
        const chipAbs = pinned(`host:${c.node.id}`, { x: abs.x + rel.x, y: abs.y + rel.y });
        nodes.push({
          id: ID.host(c.node.id),
          type: "host",
          parentId: id,
          position: { x: chipAbs.x - abs.x, y: chipAbs.y - abs.y },
          width: BUS_ICON,
          height: BUS_ICON,
          ...common,
          data: { ...hostData(c.node, multi.has(c.node.id)), source: source(c.node.id), confidence: confidence(c.node.id) },
        });
      } else {
        nodes.push({ id: `${id}:rungs:${c.rungs.role}`, type: "rungs", parentId: id, position: rel, width: BUS_ICON, height: BUS_ICON, draggable: false, ...common, data: c.rungs });
      }
    });
  }
  // Taps (§8): an amber chip on the top edge of every pill (or device) a binding names.
  for (const tap of options.taps ?? []) {
    for (const target of tap.targets) {
      // A port tap on a device rides the device; on a host it rides the pill the port is on, at the left.
      const onHost = tap.scope === "port" && !deviceNames.has(target);
      const pill = (seg: string | null | undefined) => (seg && anchorByName[seg] === seg ? ID.segment(seg) : null);
      const parentId = tap.scope === "port" ? (deviceNames.has(target) ? nodeIdOf(target) : pill(tap.segment)) : pill(target);
      if (!parentId) continue;
      const parent = nodes.find((n) => n.id === parentId);
      if (!parent) continue;
      const w = parent.width ?? DEVICE_W;
      nodes.push({ id: `tap:${tap.index}:${target}`, type: "tap", parentId, position: { x: onHost ? 6 : w - TAP_CHIP_W - 6, y: -TAP_CHIP_H / 2 }, width: TAP_CHIP_W, height: TAP_CHIP_H, zIndex: 4, draggable: tap.scope === "segment", selectable: true, connectable: false, data: { kind: "tap", id: String(tap.index), target, scope: tap.scope, sensors: [...tap.sensors], source: (tap.source as Source | null) ?? fallbackSource } });
    }
  }
  const hostsIn = (segs: readonly GraphSegment[]) => segs.reduce((n, s) => n + s.hostCount, 0);
  for (const f of siteFolds) {
    const id = TREE_ID.siteFold(f.carrier, f.site);
    nodes.push({ id, type: "overflow", position: topLeft(id), width: TILE_W, height: OVERFLOW_LANE_H, draggable: false, ...common, data: { kind: "overflow", siteKey: `site:${f.carrier}|${f.site}`, hidden: f.segs.length, total: f.segs.length, expanded: false, label: `${f.site} · ${f.segs.length} segments`, hosts: hostsIn(f.segs) } });
  }
  for (const [carrier, f] of folded) {
    const id = TREE_ID.carrierOverflow(carrier);
    nodes.push({ id, type: "overflow", position: topLeft(id), width: TILE_W, height: OVERFLOW_LANE_H, draggable: false, ...common, data: { kind: "overflow", siteKey: `carrier:${carrier}`, hidden: f.hidden.length, total: f.total, expanded: false, hosts: hostsIn(f.hidden) } });
  }
  for (const f of openFolds) {
    const id = openFoldId(f.key);
    nodes.push({ id, type: "overflow", position: topLeft(id), width: TILE_W, height: OVERFLOW_LANE_H, draggable: false, ...common, data: { kind: "overflow", siteKey: f.key, hidden: 0, total: f.total, expanded: true, ...(f.key.startsWith("site:") ? { label: f.key.slice(f.key.indexOf("|") + 1) } : {}) } });
  }

  // ---- edges ----
  const edges: CanvasEdge[] = [];
  if (showInternet) for (const name of gateways) if (deviceNames.has(name)) edges.push({ id: `trunk:internet>${name}`, type: "trunk", source: ID.internet, target: nodeIdOf(name), sourceHandle: HANDLE.bottom, targetHandle: HANDLE.top, data: { kind: "trunk", bytes: crossing?.bytes ?? 0, flows: crossing?.flows ?? 0 } });
  // A segment only routers are on: one router-to-router link labelled with it.
  for (const p of pairs) {
    const [a, b] = shallow(p.a, p.b);
    const seg = graph.segmentByName.get(p.segment);
    edges.push({ id: TREE_ID.segLink(`${p.segment}:${b}`), type: "link", source: nodeIdOf(a), target: nodeIdOf(b), sourceHandle: HANDLE.bottom, targetHandle: HANDLE.top, zIndex: EDGE_Z, data: { kind: "seglink", segment: p.segment, cidr: seg?.observedCidr ?? "", a, b, hue: hueOfDevice(a), source: source(p.segment), label: `${p.segment}${seg ? ` · ${seg.observedCidr}` : ""}` } });
  }
  // The links (§2.3): between two devices, or from a device to a host's icon (its pill's fold when folded).
  const drawnIds = new Set(nodes.map((n) => n.id));
  for (const l of links) {
    const endId = (dev: string): string | null => {
      if (deviceNames.has(dev)) return nodeIdOf(dev);
      if (!graph.nodeById.has(dev)) return null;
      return drawnIds.has(ID.host(dev)) ? ID.host(dev) : pillOfHost(dev);
    };
    const da = depths.get(l.a.device)?.depth ?? 99;
    const db = depths.get(l.b.device)?.depth ?? 99;
    const [from, to] = da <= db ? [l.a, l.b] : [l.b, l.a];
    const src = endId(from.device);
    const dst = endId(to.device);
    if (!src || !dst || src === dst) continue;
    const hueSrc = deviceNames.has(from.device) ? hueOfDevice(from.device) : plainHue;
    const segs = linkSegments(l);
    edges.push({
      id: TREE_ID.link(l.index),
      type: "link",
      source: src,
      target: dst,
      sourceHandle: HANDLE.bottom,
      targetHandle: HANDLE.top,
      zIndex: EDGE_Z,
      data: { kind: "link", a: from.device, aPort: from.port, b: to.device, bPort: to.port, mode: l.mode, native: l.native, vlans: l.vlans, state: l.state, hue: hueSrc, source: (l.source as Source | null) ?? fallbackSource, label: `${from.port} — ${to.port} · ${l.mode}${segs.length ? ` · ${segs.join(", ")}` : ""}${l.state !== "up" ? ` · ${l.state}` : ""}`, index: l.index },
    });
  }
  // A switch carrying a pill (§7.1): the segment is a label on the branch.
  for (const seg of drawnSegments) {
    const carrier = carrierOf.get(seg.name);
    if (!carrier || !deviceNames.has(carrier)) continue;
    edges.push({ id: TREE_ID.carry(carrier, seg.name), type: "leg", className: LEG_CLASS, source: nodeIdOf(carrier), target: ID.segment(seg.name), sourceHandle: HANDLE.bottom, targetHandle: HANDLE.top, zIndex: EDGE_Z, data: { kind: "carry", device: carrier, segment: seg.name, label: seg.name, hue: hueOfDevice(carrier) } });
  }
  // A switch's folds (a router's ride its legs below) and every open fold's collapse tile.
  const foldEdge = (carrier: string, target: string, label: string) => {
    if (!deviceNames.has(carrier)) return;
    edges.push({ id: `fold:${carrier}>${target}`, type: "leg", className: LEG_CLASS, source: nodeIdOf(carrier), target, sourceHandle: HANDLE.bottom, targetHandle: HANDLE.top, zIndex: EDGE_Z, data: { kind: "carry", device: carrier, segment: target, label, hue: hueOfDevice(carrier) } });
  };
  for (const f of siteFolds) if (!routerNames.has(f.carrier)) foldEdge(f.carrier, TREE_ID.siteFold(f.carrier, f.site), `${f.site} · ${f.segs.length} segments`);
  for (const [carrier, f] of folded) if (!routerNames.has(carrier)) foldEdge(carrier, TREE_ID.carrierOverflow(carrier), `${f.hidden.length} more`);
  for (const f of openFolds) foldEdge(f.carrier, openFoldId(f.key), "collapse");
  // A router's legs are the config's `nets[]`: one per pill it does not reach by a trunk, re-homed by drag; folded ones go to the fold.
  for (const r of graph.routers) {
    const hue = hueByRouter.get(r.name)!;
    const foldedTo = new Map<string, number>();
    for (const name of legsOf(r)) {
      const anchor = anchorByName[name];
      if (!anchor) continue;
      if (anchor !== name) {
        foldedTo.set(anchor, (foldedTo.get(anchor) ?? 0) + 1);
        continue;
      }
      const segOf = graph.segmentByName.get(name);
      edges.push({ id: `leg:${r.name}>${name}`, type: "leg", className: LEG_CLASS, source: ID.router(r.name), target: ID.segment(name), sourceHandle: HANDLE.bottom, targetHandle: HANDLE.top, zIndex: EDGE_Z, data: { kind: "leg", label: name, site: segOf ? `carrier:${carrierKey(segOf)}` : undefined, hue } });
    }
    for (const [anchor, n] of foldedTo) edges.push({ id: `legs:${r.name}>${anchor}`, type: "leg", className: LEG_CLASS, source: ID.router(r.name), target: anchor, sourceHandle: HANDLE.bottom, targetHandle: HANDLE.top, zIndex: EDGE_Z, data: { kind: "leg", label: `${n} segment${n === 1 ? "" : "s"} folded`, folded: n, site: siteKeyOfOverflowAnchor(anchor) ?? undefined, hue } });
  }
  // The "not carried" box's members: dashed, so the box reads as a group, not a device.
  for (const id of notCarried) if (g.hasNode(UNCARRIED_ID)) edges.push({ id: `uncarried>${id}`, type: "leg", className: LEG_CLASS, source: UNCARRIED_ID, target: id, sourceHandle: HANDLE.bottom, targetHandle: HANDLE.top, zIndex: EDGE_Z, data: { kind: "carry", device: "", segment: id, label: "", hue: plainHue, dashed: true } });
  // A multi-homed host's stubs.
  const stubFolded = new Map<string, { host: string; anchor: string; n: number }>();
  for (const st of stubs) {
    const anchor = anchorByName[st.segment];
    if (!anchor || !drawnIds.has(ID.host(st.host))) continue;
    if (anchor !== st.segment) {
      const k = `${st.host}>${anchor}`;
      const cur = stubFolded.get(k) ?? { host: st.host, anchor, n: 0 };
      cur.n += 1;
      stubFolded.set(k, cur);
      continue;
    }
    edges.push({ id: `stub:${st.host}>${st.segment}`, type: "leg", className: LEG_CLASS, source: ID.host(st.host), target: ID.segment(st.segment), sourceHandle: HANDLE.bottom, targetHandle: HANDLE.top, zIndex: EDGE_Z, data: { kind: "stub", host: st.host, segment: st.segment, label: st.ip ?? st.segment } });
  }
  for (const f of stubFolded.values()) edges.push({ id: `stubs:${f.host}>${f.anchor}`, type: "leg", className: LEG_CLASS, source: ID.host(f.host), target: f.anchor, sourceHandle: HANDLE.bottom, targetHandle: HANDLE.top, zIndex: EDGE_Z, data: { kind: "stub", host: f.host, segment: f.anchor, label: `${f.n} segment${f.n === 1 ? "" : "s"} folded`, folded: f.n } });
  // A bridge that ranked an item and that no drawn stub shows (its host is folded away): one dashed
  // edge between the two boxes, naming the bridging hosts.
  const parentOfNode = new Map(nodes.filter((n) => n.parentId).map((n) => [n.id, n.parentId!]));
  const stubShown = (from: string, to: string) => edges.some((x) => x.data?.kind === "stub" && x.target === to && parentOfNode.get(x.source) === from);
  for (const b of bridged.values()) {
    if (stubShown(b.from, b.to) || stubShown(b.to, b.from)) continue;
    const label = `via ${b.hosts.slice(0, 2).join(", ")}${b.hosts.length > 2 ? ` +${b.hosts.length - 2}` : ""}`;
    edges.push({ id: `bridge:${b.from}>${b.to}`, type: "leg", className: LEG_CLASS, source: b.from, target: b.to, sourceHandle: HANDLE.bottom, targetHandle: HANDLE.top, zIndex: EDGE_Z, data: { kind: "carry", device: "", segment: b.to, label, hue: plainHue, dashed: true } });
  }

  // ---- a new box never lands on a pinned one: it moves right into free space (brief "Positions") ----
  freeSpace(nodes, pins);

  // ---- orthogonal routes through the gutters (R3-4), on dagre's positions ----
  routeTree(g, nodes, edges);

  // The east-west pairs: evidence, drawn under Light-up or for the selection (never structure, never
  // routed), merged on the boxes they are drawn between (`pairs.ts`).
  const pairSel = selectPairs(graph, (name) => anchorByName[name] ?? null, { showAll: options.showAllPairs === true });
  const boxOfAnchor = (a: string): string => (anchorByName[a] === a ? ID.segment(a) : a);
  for (const p of pairSel.drawn) {
    edges.push({ id: `arc:${p.kind}:${p.key}`, type: "arc", source: boxOfAnchor(p.a), target: boxOfAnchor(p.b), sourceHandle: HANDLE.bottom, targetHandle: HANDLE.bottomIn, style: { strokeWidth: p.strokeWidth }, data: { kind: "arc", key: p.key, arcKind: p.kind, bytes: p.bytes, flows: p.flows, pairs: p.pairs, label: p.kind === "asserted" && !p.hasTraffic ? "conduit (declared, no traffic observed)" : `${p.bytes.toLocaleString()} bytes · ${p.flows.toLocaleString()} flows${p.pairs > 1 ? ` · ${p.pairs} pairs` : ""}` } });
  }

  return { nodes, edges, width: (gw ?? 0) + CANVAS_PAD, height: (gh ?? 0) + CANVAS_PAD, overBudget: null, hiddenSegments, hiddenPairs: pairSel.hiddenPairs, anchorByName, unplaced: graph.unplacedLocal.length };
}

/** The pin key of a top-level node of the tree (`host:` for a device, `segment:` for a pill). */
function pinKeyOfNode(n: CanvasNode): string | null {
  const d = n.data;
  if (d.kind === "router" || d.kind === "device") return `host:${d.name}`;
  if (d.kind === "segment") return `segment:${d.cidr}`;
  return null;
}

/** Move every unpinned top-level box that overlaps a pinned (or already settled) one to the
 *  right of it, in its own rank, until it overlaps nothing. Mutates the nodes' positions. */
function freeSpace(nodes: CanvasNode[], pins: Pins): void {
  const top = nodes.filter((n) => !n.parentId);
  const pinned = top.filter((n) => {
    const k = pinKeyOfNode(n);
    return k !== null && pins[k] !== undefined;
  });
  if (!pinned.length) return;
  const placed: Box[] = pinned.map((n) => ({ x: n.position.x, y: n.position.y, w: n.width ?? 0, h: n.height ?? 0 }));
  const overlaps = (a: Box, b: Box) => a.x < b.x + b.w + TREE_NODE_SEP / 2 && b.x < a.x + a.w + TREE_NODE_SEP / 2 && a.y < b.y + b.h && b.y < a.y + a.h;
  for (const n of top) {
    if (pinned.includes(n)) continue;
    const box: Box = { x: n.position.x, y: n.position.y, w: n.width ?? 0, h: n.height ?? 0 };
    for (let guard = 0; guard < 200; guard++) {
      const hit = placed.filter((p) => overlaps(box, p));
      if (!hit.length) break;
      box.x = Math.max(...hit.map((p) => p.x + p.w)) + TREE_NODE_SEP;
    }
    n.position = { x: box.x, y: box.y };
    placed.push(box);
  }
}

/** The absolute box of every node (a child's from its parent's position). */
export function absoluteBoxes(nodes: readonly CanvasNode[]): Map<string, Box> {
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const out = new Map<string, Box>();
  const abs = (n: CanvasNode): { x: number; y: number } => {
    const parent = n.parentId ? byId.get(n.parentId) : undefined;
    const pa = parent ? abs(parent) : { x: 0, y: 0 };
    return { x: pa.x + n.position.x, y: pa.y + n.position.y };
  };
  for (const n of nodes) {
    const p = abs(n);
    out.set(n.id, { x: p.x, y: p.y, w: n.width ?? 0, h: n.height ?? 0 });
  }
  return out;
}

/** Route every structural edge orthogonally on the dagre ranks (`orthogonal.ts`); an edge whose
 *  end was pinned away from dagre's place, or that runs upward, keeps no route and draws a
 *  smoothstep. Mutates the edges' `data.route`. */
function routeTree(g: dagre.graphlib.Graph, nodes: readonly CanvasNode[], edges: CanvasEdge[]): void {
  const boxes = absoluteBoxes(nodes);
  const parentOf = new Map(nodes.filter((n) => n.parentId).map((n) => [n.id, n.parentId!]));
  // Ranks: dagre's node centres, one band per distinct y.
  const ys = new Map<number, Box[]>();
  for (const id of g.nodes()) {
    const n = g.node(id);
    if (!n) continue;
    const y = Math.round(n.y);
    ys.set(y, [...(ys.get(y) ?? []), { x: n.x - n.width / 2, y: n.y - n.height / 2, w: n.width, h: n.height }]);
  }
  const rankYs = [...ys.keys()].sort((a, b) => a - b);
  const bands = rankYs.map((y) => {
    const bs = ys.get(y)!;
    return { top: Math.min(...bs.map((b) => b.y)), bottom: Math.max(...bs.map((b) => b.y + b.h)) };
  });
  const rankOf = new Map<string, number>();
  for (const id of g.nodes()) {
    const n = g.node(id);
    if (n) rankOf.set(id, rankYs.indexOf(Math.round(n.y)));
  }
  const dagreBox = (id: string): Box | null => {
    const n = g.node(id);
    return n ? { x: n.x - n.width / 2, y: n.y - n.height / 2, w: n.width, h: n.height } : null;
  };
  // An end at dagre's place (a top-level node) or inside a node at dagre's place (a host chip).
  const endOf = (id: string): { box: Box; rank: number; top: string } | null => {
    const top = parentOf.get(id) ?? id;
    const placed = dagreBox(top);
    const now = boxes.get(top);
    const box = boxes.get(id);
    if (!placed || !now || !box || Math.abs(placed.x - now.x) > 0.5 || Math.abs(placed.y - now.y) > 0.5) return null;
    const rank = rankOf.get(top);
    return rank === undefined || rank < 0 ? null : { box, rank, top };
  };
  const reqs: RouteRequest[] = [];
  for (const e of edges) {
    if (e.type === "arc" || !e.data) continue;
    const s = endOf(e.source);
    const t = endOf(e.target);
    if (!s || !t) continue;
    const via: number[] = [];
    if (t.rank > s.rank + 1) {
      const pts = (g.edge(s.top, t.top) as { points?: Array<{ x: number; y: number }> } | undefined)?.points ?? [];
      for (let r = s.rank + 1; r < t.rank; r++) {
        const cy = rankYs[r]!;
        const p = pts.filter((q) => Math.abs(q.y - cy) < 1)[0];
        via.push(p ? p.x : Number.NaN);
      }
      if (via.some((x) => Number.isNaN(x))) continue; // no column reserved: leave it to the smoothstep
    }
    reqs.push({ id: e.id, src: s.box, dst: t.box, srcRank: s.rank, dstRank: t.rank, via, lane: e.source });
  }
  const routes = routeOrthogonal(bands, reqs);
  const at = (id: string) => {
    const b = boxes.get(parentOf.get(id) ?? id)!;
    return { x: b.x, y: b.y };
  };
  for (const e of edges) {
    const pts = routes.get(e.id);
    if (pts && e.data) e.data = { ...e.data, route: { points: pts, at: [at(e.source), at(e.target)] } };
  }
}
