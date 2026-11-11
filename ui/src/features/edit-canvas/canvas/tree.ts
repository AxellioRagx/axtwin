/**
 * The device tree (brief "Canvas rendering"; `docs/design/topology-layers.md` §7;
 * review round 3, R3-4 / R3-5 / R3-8, 2026-11-07; segments drawn as switches,
 * 2026-11-11). A layered (Sugiyama) layout from `@dagrejs/dagre` decides WHERE
 * everything sits; what a switch holds (its host icons, the role bands that
 * collapse by occupancy rung) comes from `chipsOf`. The tree reads top to bottom,
 * one uniform shape everywhere:
 *
 *   edge routers (an uplink, else the router with most segments) → the other
 *   routers by hops → switches (real or implied) → hosts.
 *
 * A segment is never a box. It is drawn as the switch that carries it:
 *
 *   1. A real carrier -- a `kind: switch` whose own legs, or an `up` access or
 *      trunk link, carry the segment (the deepest such switch): the segment's
 *      hosts hang directly off that switch as nodes of their own; the segment
 *      shows as the VLAN label (and, on a switch of two or more segments, a
 *      per-segment hue) on those access edges, on hover and in the inspector.
 *   2. No switch carries it: an IMPLIED switch, `vsw:<segment>` (`ID.segment`),
 *      drawn as the switch glyph with a dashed outline and an "implied" mark.
 *      Its data is the segment's (`kind: "segment"`, `implied: true`), so its
 *      hover and inspector show the segment's name, CIDR and zone type, and every
 *      gesture that targets a segment targets it. It is presentation only: it
 *      is never written to the twin config ("Insert switch" makes a real one).
 *   3. Only routers are on it (a `/30` such as `fab-link`): one router-to-router
 *      line labelled with it, as before.
 *   4. A multi-homed host is a child of its home segment's switch and draws
 *      between that switch and the switch of each further segment (a stub).
 *
 * A router whose branch already reaches a segment's carrier through a link draws
 * no leg of its own to it.
 *
 * Edges are orthogonal and routed through the gutters between ranks
 * (`orthogonal.ts`), so none crosses a box. One hue per router only when there
 * are two or more; a one-router twin is neutral.
 *
 * Folding, folded by default, per switch: the implied switches of one site
 * (two-octet prefix) under one carrier fold into one site fold, and every site
 * fold is always on the carrier's rail; the loose (site-less) implied switches
 * show up to `fan` and a `+N more` holds the rest. An open site shows `fan` of
 * its switches per page on its own row, then its own `+N more` for the next
 * page (R5-2). A switch (real or implied) shows at most `TREE_HOST_BUDGET` host
 * icons and a `+N` fold; the `×N` badge folds a group's instances. A click
 * expands in place and the tree re-ranks around it. Real switches are devices
 * ranked by their links and are not folded (they never were).
 *
 * Positions: dagre's, then the side-car's pins override them (absolute
 * top-lefts, plane epoch 7); Re-layout drops the pins. Pure -- no React, no DOM.
 */
import * as dagre from "@dagrejs/dagre";

import { CANVAS_PAD, DEVICE_H, DEVICE_W, INTERNET_W, TIER_INTERNET_H, TILE_W } from "../layout/geometry";
import { NO_PINS, type Pins } from "../layout/pins";
import { BUDGET_LEVEL1_BANDS } from "../model/budgets";
import type { ConsoleGraph, GraphSegment } from "../model/graph";
import { compareCidr, twoOctetPrefix } from "../model/keys";
import { switchBadge } from "../model/roles";
import type { ExtraLeg, TreeLink, TreeStructure } from "./authored";
import { BUS_ICON, OVERFLOW_LANE_H } from "./bus";
import { HUE_GREY, hueOf } from "./hues";
import { routeOrthogonal, type Box, type RouteRequest } from "./orthogonal";
import { selectPairs } from "./pairs";
import { EDGE_Z, HANDLE, ID, LEG_CLASS, chipsOf, overflowAnchorKey, siteKeyOfOverflowAnchor, hostData, type CanvasEdge, type CanvasNode, type ChipSpec, type Crossing, type DeviceData, type Placement, type SegmentData, type Source } from "./placement";

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
  /** How many items (implied switches, site folds) a carrier shows before folding the rest into `+N more` (the rail's Fan-out). */
  fan?: number;
  /** The tap plan's bindings (topology-layers §8): a chip on each switch or device a tap sits on. */
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
  /** A `port` tap's segment: where the chip goes when the port is a host's (the chip rides the host's switch). */
  segment?: string | null;
  sensors: string[];
  source: string | null;
}

/** The size of a tap chip on a switch's or a router's top edge. */
export const TAP_CHIP_W = 44;
export const TAP_CHIP_H = 18;
/** Half the drawn icon box of a switch or router (`nodes.tsx` `ROUTER_ICON`), where a tap chip sits beside it. */
const TAP_ICON_HALF = 17;

/** The dagre spacing: between siblings, and between ranks (the gutter the edges run in). */
export const TREE_NODE_SEP = 36;
export const TREE_RANK_SEP = 72;
/** The default fold: a carrier with more items than this folds the rest. */
export const TREE_FAN_DEFAULT = 4;
/** The expand key of an open site's page `page` (2, 3, ...): its row's `+N more` opens it (R5-2). */
export function sitePageKey(siteKey: string, page: number): string {
  return `${siteKey}#${page}`;
}
/** How many budgets of an open site's segments its row shows: 1, plus each further page opened in turn. */
export function sitePages(expanded: ReadonlySet<string>, siteKey: string): number {
  let pages = 1;
  while (expanded.has(sitePageKey(siteKey, pages + 1))) pages++;
  return pages;
}
/** Closing `key` closes what it opened: a site's further pages go with it. */
export function withoutFold(keys: readonly string[], key: string): string[] {
  return keys.filter((x) => x !== key && !x.startsWith(`${key}#`));
}
/** The per-switch host budget: a switch draws this many host icons and folds the rest behind `+N`. */
export const TREE_HOST_BUDGET = 8;
/** The `+N` host fold at the end of a switch's host row. */
export const HOST_FOLD_W = 44;
/** The hue of every branch on a one-router twin (R3-5: hues only distinguish two or more routers). */
export const HUE_NEUTRAL = "#5f6b7a";
/** The tree's box budget: the switches, folds, devices and host rows it draws at once
 *  (a switch's host row is one box however many icons it holds). Past it the page falls
 *  back to the list; the folds are what keep a large twin under it. */
export const TREE_BUDGET_BOXES = 120;

export const TREE_ID = {
  device: (name: string) => `device:${name}`,
  carrierOverflow: (carrier: string) => overflowAnchorKey(`carrier:${carrier}`),
  siteFold: (carrier: string, site: string) => overflowAnchorKey(`site:${carrier}|${site}`),
  /** An open site's own `+N more`, at the end of its row. */
  rowOverflow: (siteKey: string) => overflowAnchorKey(`more:${siteKey}`),
  link: (index: number) => `link:${index}`,
  segLink: (segment: string) => `seglink:${segment}`,
  /** A switch's (real or implied) `+N` host fold, by the switch's node id. */
  hostFold: (parent: string) => `hostfold:${parent}`,
  /** A host's access edge from its switch: an attachment, so it is a leg (`leg:<host>><segment>`). */
  access: (host: string, segment: string) => `leg:${host}>${segment}`,
};

/** The expand key of a switch's host fold (`+N`), by the switch's node id (`vsw:<segment>`, `device:<name>`). */
export const hostsKey = (parent: string) => `hosts:${parent}`;

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

/** Which real switch carries each segment (§7.1 rule 1): the deepest switch whose
 *  `up` links or own ports carry it, else `null` (the segment is then drawn as an
 *  implied switch, rule 2). Pure, exported for the tests. */
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

/** The router a `boundary` tap sits on (§8): the one with a leg on every segment it names, or `null`. */
export function boundaryRouter(routers: ReadonlyArray<{ name: string; segments: readonly string[] }>, segments: readonly string[]): string | null {
  return routers.find((r) => segments.length > 0 && segments.every((s) => r.segments.includes(s)))?.name ?? null;
}

export function placeTree(graph: ConsoleGraph, pins: Pins = NO_PINS, options: TreeOptions = {}): Placement {
  // What is drawn: every segment, as a switch (the tree folds per carrier and site below).
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
  const nodeIdOf = (name: string) => deviceNodeId(name, routerNames);
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

  const chipsByCidr = new Map<string, ChipSpec[]>();
  for (const seg of shown) {
    const { chips, bands } = chipsOf(graph, seg.observedCidr);
    if (bands > BUDGET_LEVEL1_BANDS) return empty(`segment ${seg.observedCidr} holds ${bands} role bands, over the band budget of ${BUDGET_LEVEL1_BANDS}`);
    chipsByCidr.set(seg.observedCidr, chips);
  }

  // ---- a multi-homed host hangs off its first segment's switch, with a stub from each other one ----
  const extraByHost = new Map<string, ExtraLeg[]>();
  for (const leg of options.extraLegs ?? []) extraByHost.set(leg.host, [...(extraByHost.get(leg.host) ?? []), leg]);
  const stubs: Array<{ host: string; segment: string; ip: string | null; home?: string }> = [];
  for (const [host, extras] of extraByHost) {
    if (!graph.nodeById.get(host)) continue;
    for (const e of extras) stubs.push({ host, segment: e.segment, ip: e.ip, home: e.home });
  }

  // ---- folding: the IMPLIED switches, per carrier, by site; `fan` items, then `+N more` ----
  const NO_CARRIER = "none";
  const baseCarrier = (seg: GraphSegment): string => {
    const c = carrierOf.get(seg.name);
    if (c) return c;
    const r = graph.routers.find((x) => x.segments.includes(seg.name));
    return r ? r.name : NO_CARRIER;
  };
  // A segment no device carries but a multi-homed host bridges to a carried one (corp-twin's
  // `l2-switch` hosts) sits on THAT carrier's rail, with its site (R4-2b, 2026-11-08): it used to
  // hang under whichever box held the bridging host, so 10.180, 10.199 and vlan192 chained down
  // off the "+N more" fold as if they were its children.
  const adopted = new Map<string, { carrier: string; via: string[] }>();
  /** A host's home segment NAME: the tree's record (its first leg), else its CIDR's segment. */
  const homeName = (host: string): string | undefined => {
    const h = tree.homes?.[host];
    if (h) return h;
    const cidr = graph.nodeById.get(host)?.segment;
    return cidr ? nameOfCidr.get(cidr) : undefined;
  };
  const homeOf = (st: { host: string; home?: string }): string | undefined => {
    if (st.home) return st.home;
    if (tree.homes?.[st.host]) return tree.homes[st.host];
    const cidr = graph.nodeById.get(st.host)?.segment;
    return cidr ? nameOfCidr.get(cidr) : undefined;
  };
  {
    const byName = new Map(shown.map((sg) => [sg.name, sg]));
    const carrierNow = (name: string): string => {
      const sg = byName.get(name);
      return sg ? (adopted.get(name)?.carrier ?? baseCarrier(sg)) : NO_CARRIER;
    };
    for (let grew = true; grew; ) {
      grew = false;
      for (const st of stubs) {
        const a = homeOf(st);
        if (!a || !byName.has(a) || !byName.has(st.segment)) continue;
        for (const [from, to] of [[a, st.segment], [st.segment, a]] as const) {
          const cf = carrierNow(from);
          if (cf === NO_CARRIER || carrierNow(to) !== NO_CARRIER) continue;
          adopted.set(to, { carrier: cf, via: [st.host] });
          grew = true;
        }
      }
    }
    for (const st of stubs) {
      const a = homeOf(st);
      for (const name of [st.segment, a]) {
        const ad = name ? adopted.get(name) : undefined;
        if (ad && !ad.via.includes(st.host)) ad.via.push(st.host);
      }
    }
  }
  const carrierKey = (seg: GraphSegment): string => adopted.get(seg.name)?.carrier ?? baseCarrier(seg);
  type Item = { kind: "switch"; seg: GraphSegment } | { kind: "site"; site: string; segs: GraphSegment[] } | { kind: "open"; key: string; site: string; segs: GraphSegment[] };
  /** Each carrier's rail, left to right: its items (site, then CIDR), the `+N more` last. */
  const rails = new Map<string, string[]>();
  /** The implied switches an open site tile holds (its own row, below the rail), by tile id. */
  const tileRows = new Map<string, string[]>();
  /** Which open site tile an implied switch hangs under. */
  const underTile = new Map<string, string>();
  const openFoldId = (key: string) => overflowAnchorKey(`open:${key}`);
  const byCarrier = new Map<string, GraphSegment[]>();
  // Only a segment no real switch carries is an item of a rail: a carried one is its switch.
  for (const seg of shown) if (!carrierOf.get(seg.name)) byCarrier.set(carrierKey(seg), [...(byCarrier.get(carrierKey(seg)) ?? []), seg]);
  /** Segment name -> what stands for it: itself (its implied switch is drawn), its carrier's node id, or a fold's id. */
  const anchorByName: Record<string, string> = {};
  for (const seg of shown) {
    const c = carrierOf.get(seg.name);
    if (c) anchorByName[seg.name] = nodeIdOf(c);
  }
  /** The implied switches drawn. */
  const drawnSegments: GraphSegment[] = [];
  const siteFolds: Array<{ carrier: string; site: string; segs: GraphSegment[] }> = [];
  const folded = new Map<string, { hidden: GraphSegment[]; total: number; items: number }>();
  const openFolds: Array<{ carrier: string; key: string; total: number; foldBack: number }> = [];
  /** An open site's own `+N more` at the end of its row: the next budget of its segments (R5-2). */
  const rowFolds: Array<{ carrier: string; key: string; tile: string; next: string; hidden: GraphSegment[]; total: number }> = [];
  /** The tile a row fold's segments hang under (their legs and rank come from it). */
  const tileOfAnchor = new Map<string, string>();
  for (const [carrier, segs] of byCarrier) {
    const bySite = new Map<string, GraphSegment[]>();
    for (const s of segs) {
      const site = twoOctetPrefix(s.observedCidr) ?? s.observedCidr;
      bySite.set(site, [...(bySite.get(site) ?? []), s]);
    }
    const items: Item[] = [];
    for (const [site, group] of bySite) {
      const siteKey = `site:${carrier}|${site}`;
      // One site under the carrier is not worth a fold of its own: its switches stand under the carrier's budget.
      if (group.length === 1 || bySite.size === 1) for (const seg of group) items.push({ kind: "switch", seg });
      // An open site is ONE item on the rail (its collapse tile); its switches fan in a row below it (R4-8).
      else if (expanded.has(siteKey)) items.push({ kind: "open", key: siteKey, site, segs: group });
      else items.push({ kind: "site", site, segs: group });
    }
    // R5-2 (2026-11-08): every site a carrier serves is always on its rail; the budget (the rail's
    // Fan-out) counts the loose (site-less) switches only, and "+N more" holds only those beyond it.
    const carrierOpen = expanded.has(`carrier:${carrier}`);
    const loose = items.filter((it): it is Extract<Item, { kind: "switch" }> => it.kind === "switch");
    const open = carrierOpen || loose.length <= fan;
    const kept = new Set<Item>(loose.slice(0, fan));
    const visible = open ? items : items.filter((it) => it.kind !== "switch" || kept.has(it));
    const rail: string[] = [];
    for (const it of visible) {
      if (it.kind === "switch") {
        anchorByName[it.seg.name] = it.seg.name;
        drawnSegments.push(it.seg);
        rail.push(ID.segment(it.seg.name));
      } else if (it.kind === "open") {
        // An open site opens a budget of its switches per page on its own row, then its own "+N more".
        const tile = openFoldId(it.key);
        const pages = sitePages(expanded, it.key);
        const shownSegs = it.segs.slice(0, fan * pages);
        const rest = it.segs.slice(shownSegs.length);
        openFolds.push({ carrier, key: it.key, total: it.segs.length, foldBack: it.segs.length });
        rail.push(tile);
        const row = shownSegs.map((sg) => ID.segment(sg.name));
        for (const sg of shownSegs) {
          anchorByName[sg.name] = sg.name;
          drawnSegments.push(sg);
          underTile.set(sg.name, tile);
        }
        if (rest.length) {
          const more = TREE_ID.rowOverflow(it.key);
          rowFolds.push({ carrier, key: it.key, tile, next: sitePageKey(it.key, pages + 1), hidden: rest, total: it.segs.length });
          tileOfAnchor.set(more, tile);
          for (const sg of rest) anchorByName[sg.name] = more;
          row.push(more);
        }
        tileRows.set(tile, row);
      } else {
        siteFolds.push({ carrier, site: it.site, segs: it.segs });
        for (const s of it.segs) anchorByName[s.name] = TREE_ID.siteFold(carrier, it.site);
        rail.push(TREE_ID.siteFold(carrier, it.site));
      }
    }
    const beyond = loose.slice(fan).map((it) => it.seg);
    if (!open) {
      folded.set(carrier, { hidden: beyond, total: segs.length, items: beyond.length });
      for (const s of beyond) anchorByName[s.name] = TREE_ID.carrierOverflow(carrier);
      rail.push(TREE_ID.carrierOverflow(carrier));
    } else if (carrierOpen && beyond.length) {
      // Its "fold N back" names the very segments the collapsed "+N more" holds.
      openFolds.push({ carrier, key: `carrier:${carrier}`, total: segs.length, foldBack: beyond.length });
      rail.push(openFoldId(`carrier:${carrier}`));
    }
    rails.set(carrier, rail);
  }
  /** The node a segment is drawn through: its implied switch, its carrier, or the fold holding it. */
  const anchorNode = (seg: string): string | null => {
    const a = anchorByName[seg];
    return a ? (a === seg ? ID.segment(seg) : a) : null;
  };
  const isFold = (id: string) => siteKeyOfOverflowAnchor(id) !== null;

  // ---- each drawn switch's host row (§7.6: the budget, `+N`, role bands and `×N` are per switch) ----
  /** A switch's node id -> the segments whose hosts hang off it, in CIDR order. */
  const rowSegs = new Map<string, GraphSegment[]>();
  for (const seg of shown) {
    const a = anchorNode(seg.name);
    if (!a || isFold(a)) continue;
    rowSegs.set(a, [...(rowSegs.get(a) ?? []), seg]);
  }
  type RowChip = { chip: ChipSpec; seg: GraphSegment; id: string };
  const chipId = (parent: string, c: ChipSpec, seg: GraphSegment) => ("node" in c ? ID.host(c.node.id) : `${parent}:rungs:${seg.name}:${c.rungs.role}`);
  const hostRows = new Map<string, { chips: RowChip[]; hidden: number; open: boolean; hosts: number }>();
  /** A drawn host's switch: its home segment's. */
  const parentOfHost = new Map<string, string>();
  const seenChip = new Set<string>();
  for (const [parent, segs] of rowSegs) {
    // A host belongs to its home segment's row only: two segments on one CIDR (§9 D3) share chips.
    const mine = (chip: ChipSpec, seg: GraphSegment) => !("node" in chip) || (homeName(chip.node.id) ?? seg.name) === seg.name;
    const all = segs.flatMap((seg) => (chipsByCidr.get(seg.observedCidr) ?? []).filter((chip) => mine(chip, seg)).map((chip) => ({ chip, seg, id: chipId(parent, chip, seg) }))).filter((c) => !seenChip.has(c.id) && Boolean(seenChip.add(c.id)));
    if (!all.length) continue;
    const open = expanded.has(hostsKey(parent));
    const vis = open || all.length <= TREE_HOST_BUDGET ? all : all.slice(0, TREE_HOST_BUDGET);
    hostRows.set(parent, { chips: vis, hidden: all.length - vis.length, open, hosts: segs.reduce((n, s) => n + s.hostCount, 0) });
    for (const c of vis) if ("node" in c.chip) parentOfHost.set(c.chip.node.id, parent);
  }
  // The segments behind a "+N more" (a carrier's, or an open site's row's): what those folds say.
  // A closed site's segments are counted apart: its fold names them itself (R5-2).
  const hiddenSegments = [...folded.values()].reduce((n, f) => n + f.hidden.length, 0) + rowFolds.reduce((n, f) => n + f.hidden.length, 0);
  const siteSegments = siteFolds.reduce((n, f) => n + f.segs.length, 0);
  const boxCount = drawnSegments.length + siteFolds.length + folded.size + openFolds.length + rowFolds.length + devices.length + hostRows.size + 2;
  if (boxCount > TREE_BUDGET_BOXES) return empty(`${boxCount} boxes exceed the canvas budget of ${TREE_BUDGET_BOXES} after folding (the tree's cap)`);

  // ---- the Internet and the gateways ----
  const showInternet = graph.nonLocal.length > 0 || uplinks.length > 0;
  const crossing: Crossing | null = graph.wanEdge && graph.wanEdge.crossingEdges > 0 ? { edges: graph.wanEdge.crossingEdges, bytes: graph.wanEdge.bytes, flows: graph.wanEdge.flows, inferred: graph.wanEdge.inferred } : null;
  const gateways = uplinks.length ? uplinks : crossing || graph.nonLocal.length ? edgeRouters(devices, []) : [];

  // ---- the legs a router draws: not to a segment its own branch reaches by a link, not to a router pair ----
  const legsOf = (r: { name: string; segments: readonly string[] }): string[] =>
    r.segments.filter((name) => {
      if (pairSegments.has(name)) return false;
      const c = carrierOf.get(name);
      return !(c && depths.get(c)?.root === r.name);
    });

  // ---- dagre: devices, implied switches, folds, hosts; every structural edge between them ranks them ----
  const g = new dagre.graphlib.Graph({ multigraph: false, compound: false });
  g.setGraph({ rankdir: "TB", nodesep: TREE_NODE_SEP, ranksep: TREE_RANK_SEP, edgesep: 12, marginx: CANVAS_PAD, marginy: CANVAS_PAD });
  g.setDefaultEdgeLabel(() => ({}));
  const INTERNET_H = TIER_INTERNET_H - 12;
  const UNCARRIED_ID = "chrome:uncarried";
  if (showInternet) g.setNode(ID.internet, { width: INTERNET_W, height: INTERNET_H });
  for (const d of devices) g.setNode(nodeIdOf(d.name), { width: DEVICE_W, height: DEVICE_H });
  // Boxes go to dagre in rail order (each carrier's items left to right, then each open site's row):
  // siblings under one parent keep their insertion order through its crossing reduction.
  const boxOf = new Map<string, { width: number; height: number }>();
  for (const seg of drawnSegments) boxOf.set(ID.segment(seg.name), { width: DEVICE_W, height: DEVICE_H });
  for (const f of siteFolds) boxOf.set(TREE_ID.siteFold(f.carrier, f.site), { width: TILE_W, height: OVERFLOW_LANE_H });
  for (const carrier of folded.keys()) boxOf.set(TREE_ID.carrierOverflow(carrier), { width: TILE_W, height: OVERFLOW_LANE_H });
  for (const f of openFolds) boxOf.set(openFoldId(f.key), { width: TILE_W, height: OVERFLOW_LANE_H });
  for (const f of rowFolds) boxOf.set(TREE_ID.rowOverflow(f.key), { width: TILE_W, height: OVERFLOW_LANE_H });
  /** Each switch's host row, left to right: its icons and role bands, the `+N` fold last. */
  const hostRowIds = new Map<string, string[]>();
  for (const [parent, row] of hostRows) hostRowIds.set(parent, [...row.chips.map((c) => c.id), ...(row.hidden || row.open ? [TREE_ID.hostFold(parent)] : [])]);
  // A switch's host row goes to dagre right after the switch, so dagre's first order of the host
  // rank follows the rail's and no access edge has to cross another to reach its host.
  const setRow = (parent: string) => {
    for (const id of hostRowIds.get(parent) ?? []) if (!g.hasNode(id)) g.setNode(id, { width: id.startsWith("hostfold:") ? HOST_FOLD_W : BUS_ICON, height: BUS_ICON });
  };
  for (const d of devices) setRow(nodeIdOf(d.name));
  for (const id of [...[...rails.values()].flat(), ...[...tileRows.values()].flat(), ...boxOf.keys()]) {
    const b = boxOf.get(id);
    if (b && !g.hasNode(id)) g.setNode(id, b);
    setRow(id);
  }
  /** `weight: 0` reserves the edge's column through the ranks between without pulling either end. */
  const rankEdge = (a: string, b: string, weight = 1) => {
    if (a !== b && g.hasNode(a) && g.hasNode(b) && !g.hasEdge(b, a)) g.setEdge(a, b, weight === 1 ? {} : { weight });
  };
  const shallow = (x: string, y: string): [string, string] => ((depths.get(x)?.depth ?? 0) <= (depths.get(y)?.depth ?? 0) ? [x, y] : [y, x]);
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
  // Each rail under its carrier, each open site's row under its tile (the tile is the row's parent, so
  // the row is one rank below the rail and never chains through a switch's hosts).
  for (const [tile, row] of tileRows) for (const id of row) rankEdge(tile, id);
  for (const [carrier, rail] of rails) if (deviceNames.has(carrier)) for (const id of rail) rankEdge(nodeIdOf(carrier), id);
  for (const f of siteFolds) if (deviceNames.has(f.carrier)) rankEdge(nodeIdOf(f.carrier), TREE_ID.siteFold(f.carrier, f.site));
  for (const carrier of folded.keys()) if (deviceNames.has(carrier)) rankEdge(nodeIdOf(carrier), TREE_ID.carrierOverflow(carrier));
  for (const f of openFolds) if (deviceNames.has(f.carrier)) rankEdge(nodeIdOf(f.carrier), openFoldId(f.key));
  for (const r of graph.routers) for (const name of legsOf(r)) {
    const t = anchorNode(name);
    if (t) rankEdge(underTile.get(name) ?? tileOfAnchor.get(t) ?? ID.router(r.name), t);
  }
  // Every switch above its host row.
  for (const [parent, ids] of hostRowIds) for (const id of ids) rankEdge(parent, id);
  /** A host's box: its own icon when drawn, else what stands for its home segment (its switch, or a fold). */
  const homeAnchorOfHost = (host: string): string | null => {
    const name = homeName(host);
    return name ? anchorNode(name) : null;
  };
  // A link from a device to a host ranks the device above the host.
  for (const l of links) {
    for (const [dev, other] of [[l.a.device, l.b.device], [l.b.device, l.a.device]] as const) {
      if (!deviceNames.has(dev) || deviceNames.has(other)) continue;
      const box = g.hasNode(ID.host(other)) ? ID.host(other) : homeAnchorOfHost(other);
      if (box) rankEdge(nodeIdOf(dev), box);
    }
  }
  // A multi-homed host draws between its switches: each further switch (or the fold holding it)
  // ranks above it too (rule 4), so its stub has a column reserved through the ranks between.
  for (const st of stubs) {
    const from = anchorNode(st.segment);
    if (from && from !== homeAnchorOfHost(st.host) && g.hasNode(ID.host(st.host))) rankEdge(from, ID.host(st.host), isFold(from) ? 0 : 1);
  }
  // Items no device carries (a segment no router has a leg on, no switch carries). A host with a leg
  // on two segments bridges them (corp-twin's bridging hosts, `role: l2-switch` with `l2: true` legs):
  // an uncarried item a bridge reaches from a ranked one hangs under that one's switch, the stub its
  // edge. What no bridge reaches hangs under a visible "not carried" box one rank down, labelled,
  // not beside the routers at rank 0 where it would read as a gateway.
  const ranked = new Set<string>();
  const uncarriedItems = new Set<string>();
  for (const seg of shown) {
    const it = anchorNode(seg.name);
    if (!it) continue;
    (carrierKey(seg) === NO_CARRIER ? uncarriedItems : ranked).add(it);
  }
  for (const it of ranked) uncarriedItems.delete(it);
  if (folded.has(NO_CARRIER)) uncarriedItems.add(TREE_ID.carrierOverflow(NO_CARRIER));
  const bridges: Array<[string, string, string]> = [];
  for (const st of stubs) {
    const a = homeAnchorOfHost(st.host);
    const b = anchorNode(st.segment);
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
  orderRows(g, [...rails.values(), ...tileRows.values()]);
  // Each switch's host row as one block centred under its switch, packed left to right in its rank.
  packHostRows(g, hostRowIds);

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
  // The implied switches (rule 2): the segment's own data, drawn as a dashed switch.
  const hueOfSegment = (seg: GraphSegment): string => {
    const c = carrierOf.get(seg.name) ?? carrierKey(seg);
    return deviceNames.has(c) ? hueOfDevice(c) : plainHue;
  };
  for (const seg of drawnSegments) {
    const id = ID.segment(seg.name);
    const abs = pinned(`segment:${seg.observedCidr}`, topLeft(id));
    const served = graph.routers.filter((r) => r.segments.includes(seg.name)).map((r) => ({ router: r.name, hue: hueByRouter.get(r.name)! }));
    const row = hostRows.get(id);
    const data: SegmentData = {
      kind: "segment",
      implied: true,
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
      carrier: null,
      hue: hueOfSegment(seg),
      source: source(seg.name),
      confidence: confidence(seg.name),
      hiddenChips: row?.hidden ?? 0,
      hostsOpen: row?.open ?? false,
    };
    nodes.push({ id, type: "impliedSwitch", position: abs, width: DEVICE_W, height: DEVICE_H, zIndex: 2, ...common, data });
  }
  // The host rows: every host a node of its own under its switch, then the role bands, then `+N`.
  const multi = new Set(extraByHost.keys());
  for (const [parent, row] of hostRows) {
    for (const c of row.chips) {
      if ("node" in c.chip) {
        const n = c.chip.node;
        nodes.push({ id: c.id, type: "host", position: pinned(`host:${n.id}`, topLeft(c.id)), width: BUS_ICON, height: BUS_ICON, zIndex: 2, ...common, data: { ...hostData(n, multi.has(n.id)), source: source(n.id), confidence: confidence(n.id) } });
      } else {
        nodes.push({ id: c.id, type: "rungs", position: topLeft(c.id), width: BUS_ICON, height: BUS_ICON, draggable: false, zIndex: 2, ...common, data: c.chip.rungs });
      }
    }
    if (row.hidden || row.open) {
      const id = TREE_ID.hostFold(parent);
      nodes.push({ id, type: "hostfold", position: topLeft(id), width: HOST_FOLD_W, height: BUS_ICON, draggable: false, zIndex: 2, ...common, data: { kind: "hostfold", key: hostsKey(parent), hidden: row.hidden, open: row.open, total: row.chips.length + row.hidden } });
    }
  }
  // Taps (§8): an amber chip on the top edge of the switch or router a binding names. A segment tap
  // rides the segment's switch (real or implied); a boundary tap ONE chip on the router with a leg on
  // both its segments; a port tap the device, or for a host's port the host's switch (at the left).
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const chipsOn = new Map<string, number>();
  const pushTap = (tap: TreeTap, parentId: string | null, target: string, left: boolean) => {
    const parent = parentId ? byId.get(parentId) : undefined;
    if (!parent || nodes.some((n) => n.id === `tap:${tap.index}:${target}`)) return;
    const k = chipsOn.get(parent.id) ?? 0;
    chipsOn.set(parent.id, k + 1);
    // Beside the icon (bottom-centre of the node, `ROUTER_ICON` square), right of its count badge,
    // clear of the name above; a host port's chip on the node's top-left corner instead.
    const w = parent.width ?? DEVICE_W;
    const h = parent.height ?? DEVICE_H;
    const step = (TAP_CHIP_W + 4) * k;
    const at = left ? { x: 6 + step, y: -TAP_CHIP_H / 2 } : { x: w / 2 + TAP_ICON_HALF + 18 + step, y: h - TAP_ICON_HALF - TAP_CHIP_H / 2 };
    nodes.push({ id: `tap:${tap.index}:${target}`, type: "tap", parentId: parent.id, position: at, width: TAP_CHIP_W, height: TAP_CHIP_H, zIndex: 4, draggable: true, selectable: true, connectable: false, data: { kind: "tap", id: String(tap.index), target, scope: tap.scope, sensors: [...tap.sensors], source: (tap.source as Source | null) ?? fallbackSource } });
  };
  /** A segment's switch node (real or implied), never a fold. */
  const switchOf = (seg: string | null | undefined): string | null => {
    const a = seg ? anchorNode(seg) : null;
    return a && !isFold(a) ? a : null;
  };
  for (const tap of options.taps ?? []) {
    if (tap.scope === "boundary") {
      const router = boundaryRouter(graph.routers, tap.targets);
      if (router) {
        pushTap(tap, nodeIdOf(router), router, false);
        continue;
      }
      // No one router forms the boundary: a chip on each side's switch.
      for (const t of tap.targets) pushTap(tap, switchOf(t), t, false);
      continue;
    }
    for (const target of tap.targets) {
      if (tap.scope === "port") {
        if (deviceNames.has(target)) pushTap(tap, nodeIdOf(target), target, false);
        else pushTap(tap, parentOfHost.get(target) ?? switchOf(tap.segment) ?? homeAnchorOfHost(target), target, true);
      } else pushTap(tap, switchOf(target), target, false);
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
    nodes.push({ id, type: "overflow", position: topLeft(id), width: TILE_W, height: OVERFLOW_LANE_H, draggable: false, ...common, data: { kind: "overflow", siteKey: f.key, hidden: f.foldBack, total: f.total, expanded: true, ...(f.key.startsWith("site:") ? { label: f.key.slice(f.key.indexOf("|") + 1) } : {}) } });
  }
  for (const f of rowFolds) {
    const id = TREE_ID.rowOverflow(f.key);
    nodes.push({ id, type: "overflow", position: topLeft(id), width: TILE_W, height: OVERFLOW_LANE_H, draggable: false, ...common, data: { kind: "overflow", siteKey: f.next, hidden: f.hidden.length, total: f.total, expanded: false, hosts: hostsIn(f.hidden) } });
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
  // The links (§2.3): between two devices, or from a device to a host's icon (its switch, or the fold, when not drawn).
  const drawnIds = new Set(nodes.map((n) => n.id));
  /** `device>host` pairs an `up` link already wires: that link IS the host's access edge. */
  const wired = new Set<string>();
  for (const l of links) {
    const endId = (dev: string): string | null => {
      if (deviceNames.has(dev)) return nodeIdOf(dev);
      if (!graph.nodeById.has(dev)) return null;
      return drawnIds.has(ID.host(dev)) ? ID.host(dev) : homeAnchorOfHost(dev);
    };
    const da = depths.get(l.a.device)?.depth ?? 99;
    const db = depths.get(l.b.device)?.depth ?? 99;
    const [from, to] = da <= db ? [l.a, l.b] : [l.b, l.a];
    const src = endId(from.device);
    const dst = endId(to.device);
    if (!src || !dst || src === dst) continue;
    if (l.state === "up") wired.add(`${src}>${dst}`);
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
  // The access edges (rule 1): a switch to each host on its row, labelled with the host's segment (the
  // VLAN); on a real switch of two or more segments each segment wears its own hue and its first edge
  // keeps its VLAN label on. A host an `up` link already wires to the switch draws only the link.
  for (const [parent, row] of hostRows) {
    const real = !parent.startsWith(ID.segment(""));
    const segNames = [...new Set(row.chips.map((c) => c.seg.name))];
    const several = real && segNames.length > 1;
    const labelled = new Set<string>();
    const branchHue = deviceNames.has(parent.slice(parent.indexOf(":") + 1)) ? hueOfDevice(parent.slice(parent.indexOf(":") + 1)) : hueOfSegment(row.chips[0]!.seg);
    for (const c of row.chips) {
      const hue = several ? hueOf(segNames.indexOf(c.seg.name)) : branchHue;
      if ("node" in c.chip) {
        if (wired.has(`${parent}>${c.id}`)) continue;
        const vlan = real && !labelled.has(c.seg.name) ? c.seg.name : undefined;
        if (vlan) labelled.add(vlan);
        edges.push({ id: TREE_ID.access(c.chip.node.id, c.seg.name), type: "leg", className: LEG_CLASS, source: parent, target: c.id, sourceHandle: HANDLE.bottom, targetHandle: HANDLE.top, zIndex: EDGE_Z, data: { kind: "leg", label: c.seg.name, segment: c.seg.name, hue, ...(vlan ? { vlan } : {}) } });
      } else {
        edges.push({ id: `rungs:${c.id}`, type: "leg", className: LEG_CLASS, source: parent, target: c.id, sourceHandle: HANDLE.bottom, targetHandle: HANDLE.top, zIndex: EDGE_Z, data: { kind: "carry", device: parent, segment: c.seg.name, label: "", hue } });
      }
    }
    if (row.hidden || row.open) edges.push({ id: `fold:${parent}>${TREE_ID.hostFold(parent)}`, type: "leg", className: LEG_CLASS, source: parent, target: TREE_ID.hostFold(parent), sourceHandle: HANDLE.bottom, targetHandle: HANDLE.top, zIndex: EDGE_Z, data: { kind: "carry", device: parent, segment: "", label: "", hue: branchHue, dashed: true } });
  }
  // A switch's folds (a router's ride its legs below) and every open fold's collapse tile.
  const foldEdge = (carrier: string, target: string, label: string) => {
    if (!deviceNames.has(carrier)) return;
    edges.push({ id: `fold:${carrier}>${target}`, type: "leg", className: LEG_CLASS, source: nodeIdOf(carrier), target, sourceHandle: HANDLE.bottom, targetHandle: HANDLE.top, zIndex: EDGE_Z, data: { kind: "carry", device: carrier, segment: target, label, hue: hueOfDevice(carrier) } });
  };
  for (const f of siteFolds) if (!routerNames.has(f.carrier)) foldEdge(f.carrier, TREE_ID.siteFold(f.carrier, f.site), `${f.site} · ${f.segs.length} segments`);
  for (const [carrier, f] of folded) if (!routerNames.has(carrier)) foldEdge(carrier, TREE_ID.carrierOverflow(carrier), `${f.hidden.length} more`);
  for (const f of openFolds) foldEdge(f.carrier, openFoldId(f.key), "collapse");
  // A router's legs are the config's `nets[]`: one per segment it does not reach by a link -- to the
  // segment's implied switch, or its carrier outside the router's branch; folded ones go to the fold.
  for (const r of graph.routers) {
    const hue = hueByRouter.get(r.name)!;
    const foldedTo = new Map<string, number>();
    for (const name of legsOf(r)) {
      const target = anchorNode(name);
      if (!target) continue;
      if (isFold(target)) {
        foldedTo.set(target, (foldedTo.get(target) ?? 0) + 1);
        continue;
      }
      const segOf = graph.segmentByName.get(name);
      edges.push({ id: `leg:${r.name}>${name}`, type: "leg", className: LEG_CLASS, source: underTile.get(name) ?? ID.router(r.name), target, sourceHandle: HANDLE.bottom, targetHandle: HANDLE.top, zIndex: EDGE_Z, data: { kind: "leg", label: name, segment: name, site: segOf ? `carrier:${carrierKey(segOf)}` : undefined, hue } });
    }
    for (const [anchor, n] of foldedTo) edges.push({ id: `legs:${r.name}>${anchor}`, type: "leg", className: LEG_CLASS, source: tileOfAnchor.get(anchor) ?? ID.router(r.name), target: anchor, sourceHandle: HANDLE.bottom, targetHandle: HANDLE.top, zIndex: EDGE_Z, data: { kind: "leg", label: `${n} segment${n === 1 ? "" : "s"} folded`, folded: n, site: siteKeyOfOverflowAnchor(anchor) ?? undefined, hue } });
  }
  // An item on a rail (or an open site's row) its parent draws no leg to -- an implied switch a
  // bridging host adopted onto the rail, or a fold of only such segments: one dashed edge from the
  // parent naming the hosts that bridge it (R4-2b).
  {
    const hasEdge = new Set(edges.map((x) => `${x.source}>${x.target}`));
    const viaOf = (id: string): string[] => {
      const names = drawnSegments.some((sg) => ID.segment(sg.name) === id) ? [id.slice(ID.segment("").length)] : Object.entries(anchorByName).filter(([, a]) => a === id).map(([n]) => n);
      return [...new Set(names.flatMap((n) => adopted.get(n)?.via ?? []))];
    };
    const dashedFrom = (parent: string, id: string) => {
      if (hasEdge.has(`${parent}>${id}`)) return;
      const via = viaOf(id);
      if (!via.length) return;
      edges.push({ id: `bridge:${parent}>${id}`, type: "leg", className: LEG_CLASS, source: parent, target: id, sourceHandle: HANDLE.bottom, targetHandle: HANDLE.top, zIndex: EDGE_Z, data: { kind: "carry", device: "", segment: id, label: `via ${via.slice(0, 2).join(", ")}${via.length > 2 ? ` +${via.length - 2}` : ""}`, hue: plainHue, dashed: true } });
      hasEdge.add(`${parent}>${id}`);
    };
    for (const [carrier, rail] of rails) if (deviceNames.has(carrier)) for (const id of rail) dashedFrom(nodeIdOf(carrier), id);
    for (const [tile, row] of tileRows) for (const id of row) dashedFrom(tile, id);
  }
  // The "not carried" box's members: dashed, so the box reads as a group, not a device.
  for (const id of notCarried) if (g.hasNode(UNCARRIED_ID)) edges.push({ id: `uncarried>${id}`, type: "leg", className: LEG_CLASS, source: UNCARRIED_ID, target: id, sourceHandle: HANDLE.bottom, targetHandle: HANDLE.top, zIndex: EDGE_Z, data: { kind: "carry", device: "", segment: id, label: "", hue: plainHue, dashed: true } });
  // A multi-homed host's stubs: from each further segment's switch (or its fold) down to the host.
  const stubFolded = new Map<string, { host: string; anchor: string; n: number }>();
  for (const st of stubs) {
    const anchor = anchorNode(st.segment);
    if (!anchor || !drawnIds.has(ID.host(st.host)) || anchor === parentOfHost.get(st.host)) continue;
    if (isFold(anchor)) {
      const k = `${st.host}>${anchor}`;
      const cur = stubFolded.get(k) ?? { host: st.host, anchor, n: 0 };
      cur.n += 1;
      stubFolded.set(k, cur);
      continue;
    }
    if (wired.has(`${anchor}>${ID.host(st.host)}`)) continue;
    edges.push({ id: `stub:${st.host}>${st.segment}`, type: "leg", className: LEG_CLASS, source: anchor, target: ID.host(st.host), sourceHandle: HANDLE.bottom, targetHandle: HANDLE.top, zIndex: EDGE_Z, data: { kind: "stub", host: st.host, segment: st.segment, label: st.ip ?? st.segment } });
  }
  for (const f of stubFolded.values()) edges.push({ id: `stubs:${f.host}>${f.anchor}`, type: "leg", className: LEG_CLASS, source: f.anchor, target: ID.host(f.host), sourceHandle: HANDLE.bottom, targetHandle: HANDLE.top, zIndex: EDGE_Z, data: { kind: "stub", host: f.host, segment: f.anchor, label: `${f.n} segment${f.n === 1 ? "" : "s"} folded`, folded: f.n } });
  // A bridge that ranked an item and that no drawn stub shows (its host is folded away): one dashed
  // edge between the two boxes, naming the bridging hosts.
  const stubShown = (from: string, to: string) => edges.some((x) => x.data?.kind === "stub" && x.source === to && parentOfHost.get(x.target.slice(ID.host("").length)) === from);
  for (const b of bridged.values()) {
    if (stubShown(b.from, b.to) || stubShown(b.to, b.from)) continue;
    const label = `via ${b.hosts.slice(0, 2).join(", ")}${b.hosts.length > 2 ? ` +${b.hosts.length - 2}` : ""}`;
    edges.push({ id: `bridge:${b.from}>${b.to}`, type: "leg", className: LEG_CLASS, source: b.from, target: b.to, sourceHandle: HANDLE.bottom, targetHandle: HANDLE.top, zIndex: EDGE_Z, data: { kind: "carry", device: "", segment: b.to, label, hue: plainHue, dashed: true } });
  }

  // ---- a new box never lands on a pinned one: it moves right into free space (brief "Positions") ----
  freeSpace(nodes, pins);

  const rowsOut: Record<string, string[]> = {};
  for (const [carrier, rail] of rails) if (deviceNames.has(carrier)) rowsOut[nodeIdOf(carrier)] = [...rail];
  for (const [tile, row] of tileRows) rowsOut[tile] = [...row];

  // ---- orthogonal routes through the gutters (R3-4), on dagre's positions ----
  routeTree(g, nodes, edges);

  // The east-west pairs: evidence, drawn under Light-up or for the selection (never structure, never
  // routed), merged on the boxes they are drawn between (`pairs.ts`).
  const pairSel = selectPairs(graph, (name) => anchorByName[name] ?? null, { showAll: options.showAllPairs === true });
  const boxOfAnchor = (a: string): string => (anchorByName[a] === a ? ID.segment(a) : a);
  for (const p of pairSel.drawn) {
    edges.push({ id: `arc:${p.kind}:${p.key}`, type: "arc", source: boxOfAnchor(p.a), target: boxOfAnchor(p.b), sourceHandle: HANDLE.bottom, targetHandle: HANDLE.bottomIn, style: { strokeWidth: p.strokeWidth }, data: { kind: "arc", key: p.key, arcKind: p.kind, bytes: p.bytes, flows: p.flows, pairs: p.pairs, label: p.kind === "asserted" && !p.hasTraffic ? "conduit (declared, no traffic observed)" : `${p.bytes.toLocaleString()} bytes · ${p.flows.toLocaleString()} flows${p.pairs > 1 ? ` · ${p.pairs} pairs` : ""}` } });
  }

  return { nodes, edges, width: (gw ?? 0) + CANVAS_PAD, height: (gh ?? 0) + CANVAS_PAD, overBudget: null, hiddenSegments, siteSegments, rows: rowsOut, hiddenPairs: pairSel.hiddenPairs, anchorByName, unplaced: graph.unplacedLocal.length };
}

/** Lay each row (a carrier's rail, an open site's switches) left to right in its given order, where
 *  dagre put its boxes in one rank with nothing else between them: the row keeps its left edge and
 *  dagre's spacing, only the order is ours (R4-2b: site then CIDR, `+N more` last). A row dagre
 *  split across ranks, or interleaved with another box, is left as dagre placed it. Mutates `g`. */
function orderRows(g: dagre.graphlib.Graph, rows: readonly (readonly string[])[]): void {
  for (const row of rows) {
    const boxes = row.filter((id) => g.hasNode(id)).map((id) => ({ id, n: g.node(id) as { x: number; y: number; width: number; height: number } }));
    if (boxes.length < 2) continue;
    const y = boxes[0]!.n.y;
    if (boxes.some((b) => Math.abs(b.n.y - y) > 0.5)) continue;
    const left = Math.min(...boxes.map((b) => b.n.x - b.n.width / 2));
    const right = Math.max(...boxes.map((b) => b.n.x + b.n.width / 2));
    const mine = new Set(row);
    const between = g.nodes().some((id) => {
      if (mine.has(id)) return false;
      const n = g.node(id) as { x: number; y: number } | undefined;
      return n !== undefined && Math.abs(n.y - y) <= 0.5 && n.x > left && n.x < right;
    });
    if (between) continue;
    const widths = boxes.reduce((w, b) => w + b.n.width, 0);
    const gap = boxes.length > 1 ? Math.max(TREE_NODE_SEP, (right - left - widths) / (boxes.length - 1)) : 0;
    let x = left;
    for (const b of boxes) {
      b.n.x = x + b.n.width / 2;
      x += b.n.width + gap;
    }
  }
}

/** Between two icons of one host row. */
export const HOST_GAP = 14;

/** The host rows (§7.6), after the rails are ordered: each switch's row is one block in its given
 *  order, `HOST_GAP` apart, centred under its switch; the blocks of one rank are packed left to
 *  right in their switches' order, clear of every other box in that rank (which stays where dagre
 *  put it). A host row is a leaf, so moving it moves nothing else. Grows the graph's width when a
 *  block is pushed past it. Mutates `g`. */
function packHostRows(g: dagre.graphlib.Graph, rows: ReadonlyMap<string, readonly string[]>): void {
  type N = { x: number; y: number; width: number; height: number };
  const inRow = new Set([...rows.values()].flat());
  const blocks: Array<{ ids: string[]; y: number; w: number; cx: number; at: number }> = [];
  for (const [parent, ids] of rows) {
    const present = ids.filter((id) => g.hasNode(id));
    const p = g.node(parent) as N | undefined;
    if (!present.length || !p) continue;
    const ns = present.map((id) => g.node(id) as N);
    // dagre may have put a row on two ranks (a multi-homed host ranked under two switches): its lowest.
    const y = Math.max(...ns.map((n) => n.y));
    const w = ns.reduce((sum, n) => sum + n.width, 0) + HOST_GAP * (ns.length - 1);
    // Where dagre put the row: its fallback when the spot under the switch is taken.
    const at = Math.min(...ns.map((n) => n.x - n.width / 2));
    blocks.push({ ids: present, y, w, cx: p.x, at });
  }
  const byRank = new Map<number, typeof blocks>();
  for (const b of blocks) byRank.set(Math.round(b.y), [...(byRank.get(Math.round(b.y)) ?? []), b]);
  let maxRight = 0;
  for (const [y, list] of byRank) {
    const fixed = g.nodes().filter((id) => !inRow.has(id)).map((id) => g.node(id) as N | undefined).filter((n): n is N => n !== undefined && Math.abs(n.y - y) <= BUS_ICON).map((n) => ({ l: n.x - n.width / 2, r: n.x + n.width / 2 }));
    // The columns dagre reserved through this rank for a longer edge (its dummy nodes' points).
    for (const e of g.edges()) {
      const pts = (g.edge(e) as { points?: Array<{ x: number; y: number }> } | undefined)?.points ?? [];
      for (const q of pts.slice(1, -1)) if (Math.abs(q.y - y) < 1) fixed.push({ l: q.x - 4, r: q.x + 4 });
    }
    list.sort((a, b) => a.cx - b.cx);
    let cursor = -Infinity;
    const hitAt = (l: number, w: number) => fixed.find((f) => l < f.r + TREE_NODE_SEP / 2 && f.l < l + w + TREE_NODE_SEP / 2);
    for (const b of list) {
      // Under the switch if that is free, else where dagre found room, else pushed right of what is in the way.
      const free = [b.cx - b.w / 2, b.at].map((l) => Math.max(l, cursor)).find((l) => !hitAt(l, b.w));
      let l = free ?? Math.max(b.cx - b.w / 2, cursor);
      for (let guard = 0; guard < 100; guard++) {
        const hit = hitAt(l, b.w);
        if (!hit) break;
        l = hit.r + TREE_NODE_SEP / 2;
      }
      let x = l;
      for (const id of b.ids) {
        const n = g.node(id) as N;
        n.x = x + n.width / 2;
        n.y = b.y;
        x += n.width + HOST_GAP;
      }
      cursor = l + b.w + TREE_NODE_SEP;
      maxRight = Math.max(maxRight, l + b.w);
    }
  }
  const graph = g.graph() as { width?: number };
  if (graph.width !== undefined && maxRight + CANVAS_PAD > graph.width) graph.width = maxRight + CANVAS_PAD;
}

/** The pin key of a top-level node of the tree (`host:` for a device or a host, `segment:` for an implied switch). */
function pinKeyOfNode(n: CanvasNode): string | null {
  const d = n.data;
  if (d.kind === "router" || d.kind === "device") return `host:${d.name}`;
  if (d.kind === "host") return `host:${d.nodeId}`;
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
  // An icon of a host row (a host, a role band, the `+N` fold) keeps the row's own gap, not a box's.
  const small = (b: Box) => b.w <= HOST_FOLD_W;
  const clear = (a: Box, b: Box) => (small(a) && small(b) ? HOST_GAP / 2 : TREE_NODE_SEP / 2);
  const overlaps = (a: Box, b: Box) => a.x < b.x + b.w + clear(a, b) && b.x < a.x + a.w + clear(a, b) && a.y < b.y + b.h && b.y < a.y + a.h;
  for (const n of top) {
    if (pinned.includes(n)) continue;
    const box: Box = { x: n.position.x, y: n.position.y, w: n.width ?? 0, h: n.height ?? 0 };
    for (let guard = 0; guard < 200; guard++) {
      const hit = placed.filter((p) => overlaps(box, p));
      if (!hit.length) break;
      box.x = Math.max(...hit.map((p) => p.x + p.w)) + (small(box) && hit.every(small) ? HOST_GAP : TREE_NODE_SEP);
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
