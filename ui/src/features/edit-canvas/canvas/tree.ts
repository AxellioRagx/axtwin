/**
 * The device-tree placement (`docs/design/topology-layers.md` §7, track 5a,
 * 2026-11-07): the lifted level-0 engine still decides WHAT is drawn -- the
 * role bands inside a segment, which bands collapse by occupancy rung, the
 * top-K observed arcs -- and a layered (Sugiyama) layout from `@dagrejs/dagre`
 * decides WHERE. The tree reads top to bottom: the Internet over the gateway
 * routers (rank 0: an uplink, else the router with most segments), the
 * switches by depth below them along their links, each segment as a compact
 * pill under the device that carries it (the deepest switch whose trunk or
 * access link names it, else the router with a leg on it), the hosts as icons
 * on the pill. A config with no switches degrades to router → pills → hosts,
 * the same tree.
 *
 * Folding (§7.6): a carrier shows at most `fan` pills (the rail's Fan-out) and
 * a dashed `+N more` pill for the rest; a click on it expands in place. A
 * multi-homed host sits on its first segment's pill with a stub to each
 * other one. One hue per router (§7.5) on its ring, every branch below it
 * (its switches, their links, the pills they carry) and the dots on a pill.
 *
 * Positions: dagre's, then the pins override them -- a `host:` pin (a host,
 * a router or a switch) and a `segment:` pin are absolute top-lefts on this
 * plane (epoch 6: a bus's rank is gone). React Flow wants a child's position
 * relative to its parent, so a host chip's is converted last. Pure -- no
 * React, no DOM.
 */
import * as dagre from "@dagrejs/dagre";

import { CANVAS_PAD, DEVICE_H, DEVICE_W, INTERNET_W, TIER_INTERNET_H, TILE_H, TILE_W } from "../layout/geometry";
import { layout0, overflowAnchorKey, siteKeyOfOverflowAnchor, type Level0Options } from "../layout/level0";
import { NO_PINS, type Pins } from "../layout/pins";
import { BUDGET_LEVEL1_BANDS } from "../model/budgets";
import type { ConsoleGraph, GraphSegment } from "../model/graph";
import { compareCidr } from "../model/keys";
import { switchBadge } from "../model/roles";
import type { ExtraLeg, TreeLink, TreeStructure } from "./authored";
import { BUS_ICON, OVERFLOW_LANE_H, iconSlot, laneHeight, laneRows } from "./bus";
import { HUE_GREY, hueOf } from "./hues";
import { EDGE_Z, HANDLE, ID, LEG_CLASS, arcKey, chipsOf, hostData, type CanvasEdge, type CanvasNode, type Crossing, type DeviceData, type Placement, type SegmentData, type Source } from "./placement";

export interface TreeOptions extends Level0Options {
  /** A multi-homed host's further attachments (its stubs). */
  extraLegs?: readonly ExtraLeg[];
  /** The routers the config names as uplinks (`AuthoredGraph.uplinks`). */
  uplinks?: readonly string[];
  /** The switches, links and provenance the bipartite graph cannot carry. */
  tree?: TreeStructure;
  /** How many pills a carrier shows before folding the rest into `+N more` (the rail's Fan-out). */
  fan?: number;
}

/** The pill's label column, left of its icons. */
export const PILL_LABEL_W = 132;
/** The pill's padding right of its last icon (the count badge sits there). */
export const PILL_PAD_RIGHT = 44;
/** The dagre spacing: between siblings, and between ranks. */
export const TREE_NODE_SEP = 36;
export const TREE_RANK_SEP = 64;
/** The default fold: a carrier with more pills than this folds the rest. */
export const TREE_FAN_DEFAULT = 4;
/** The tree's box budget: the pills, folds and devices it draws at once
 *  (a pill is one box however many icons it holds). Past it the page falls
 *  back to the list, as the bus plane did at the frozen console's 48; the
 *  fold is what keeps a large twin under it. */
export const TREE_BUDGET_BOXES = 120;

export const TREE_ID = {
  device: (name: string) => `device:${name}`,
  carrierOverflow: (carrier: string) => overflowAnchorKey(`carrier:${carrier}`),
  link: (index: number) => `link:${index}`,
  carry: (device: string, segment: string) => `carry:${device}>${segment}`,
};

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

/** Breadth-first from the routers over the device links: each device's depth
 *  and the router its branch descends from (`null` for a switch no router
 *  reaches). Pure, exported for the tests. */
export function treeDepths(devices: readonly Device[], links: readonly TreeLink[]): Map<string, { depth: number; root: string | null }> {
  const byName = new Map(devices.map((d) => [d.name, d]));
  const adj = new Map<string, string[]>();
  for (const l of links) {
    if (l.state !== "up") continue;
    if (!byName.has(l.a.device) || !byName.has(l.b.device)) continue;
    adj.set(l.a.device, [...(adj.get(l.a.device) ?? []), l.b.device]);
    adj.set(l.b.device, [...(adj.get(l.b.device) ?? []), l.a.device]);
  }
  const out = new Map<string, { depth: number; root: string | null }>();
  const queue: string[] = [];
  for (const d of devices) {
    if (d.kind === "router") {
      out.set(d.name, { depth: 0, root: d.name });
      queue.push(d.name);
    }
  }
  while (queue.length) {
    const cur = queue.shift()!;
    const at = out.get(cur)!;
    for (const next of adj.get(cur) ?? []) {
      if (out.has(next)) continue;
      out.set(next, { depth: at.depth + 1, root: at.root });
      queue.push(next);
    }
  }
  for (const d of devices) if (!out.has(d.name)) out.set(d.name, { depth: 1, root: null });
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

export function placeTree(graph: ConsoleGraph, pins: Pins = NO_PINS, options: TreeOptions = {}): Placement {
  // What is drawn: every site expanded (the tree folds per carrier, not per site).
  const probe = layout0(graph, NO_PINS, options);
  const l0 = layout0(graph, NO_PINS, { ...options, expanded: probe.sites.map((s) => s.site.key) });
  const tree = options.tree ?? { devices: [], links: [], sources: {}, confidences: {} };
  const fan = options.fan ?? TREE_FAN_DEFAULT;
  const empty = (overBudget: string | null): Placement => ({ nodes: [], edges: [], width: 0, height: 0, overBudget, hiddenSegments: 0, hiddenPairs: l0.hiddenPairs, anchorByName: {} });

  const shown: GraphSegment[] = l0.tiles.map((t) => t.segment).sort((a, b) => compareCidr(a.observedCidr, b.observedCidr));
  const chipsByCidr = new Map<string, ReturnType<typeof chipsOf>["chips"]>();
  for (const seg of shown) {
    const { chips, bands } = chipsOf(graph, seg.observedCidr);
    if (bands > BUDGET_LEVEL1_BANDS) return empty(`segment ${seg.observedCidr} holds ${bands} role bands, over the band budget of ${BUDGET_LEVEL1_BANDS}`);
    chipsByCidr.set(seg.observedCidr, chips);
  }
  const nameOfCidr = new Map(graph.segments.map((s) => [s.observedCidr, s.name]));
  const cidrOfName = new Map(graph.segments.map((s) => [s.name, s.observedCidr]));
  const source = (name: string): Source | null => (tree.sources[name] as Source | undefined) ?? null;
  const confidence = (name: string): number | null => tree.confidences[name] ?? null;

  // ---- the devices: routers (the graph's) and switches (the tree's) ----
  const routerNames = new Set(graph.routers.map((r) => r.name));
  const devices: Device[] = [
    ...graph.routers.map((r): Device => ({ name: r.name, kind: "router", role: null, image: null, legs: [...r.segments] })),
    ...tree.devices.filter((d) => !routerNames.has(d.name)).map((d): Device => ({ name: d.name, kind: d.kind, role: d.role, image: d.image, legs: [...d.segments] })),
  ];
  const deviceNames = new Set(devices.map((d) => d.name));
  const links = tree.links.filter((l) => deviceNames.has(l.a.device) || deviceNames.has(l.b.device));
  const depths = treeDepths(devices, links);
  const hueByRouter = new Map(graph.routers.map((r, i) => [r.name, hueOf(i)]));
  const hueOfDevice = (name: string): string => {
    const root = depths.get(name)?.root;
    return (root && hueByRouter.get(root)) ?? hueByRouter.get(name) ?? HUE_GREY;
  };
  const carrierOf = carriersOf(shown.map((s) => s.name), devices, links, depths);

  // ---- rule 4: a multi-homed host sits on its first segment's pill, with a stub to each other one ----
  const extraByHost = new Map<string, ExtraLeg[]>();
  for (const leg of options.extraLegs ?? []) extraByHost.set(leg.host, [...(extraByHost.get(leg.host) ?? []), leg]);
  const stubs: Array<{ host: string; segment: string; ip: string | null }> = [];
  for (const [host, extras] of extraByHost) {
    if (!graph.nodeById.get(host)) continue;
    for (const e of extras) stubs.push({ host, segment: e.segment, ip: e.ip });
  }

  // ---- folding: per carrier, the first `fan` pills by CIDR, the rest behind `+N more` ----
  // A segment no device carries and no router has a leg on (a cast with no
  // router, a parked segment) folds under the `none` carrier like any other.
  const NO_CARRIER = "none";
  const carrierKey = (seg: GraphSegment): string => {
    const c = carrierOf.get(seg.name);
    if (c) return c;
    const r = graph.routers.find((x) => x.segments.includes(seg.name));
    return r ? r.name : NO_CARRIER;
  };
  const byCarrier = new Map<string, GraphSegment[]>();
  for (const seg of shown) byCarrier.set(carrierKey(seg), [...(byCarrier.get(carrierKey(seg)) ?? []), seg]);
  const expanded = new Set(options.expanded ?? []);
  const anchorByName: Record<string, string> = {};
  const folded = new Map<string, { hidden: GraphSegment[]; total: number }>();
  const drawnSegments: GraphSegment[] = [];
  for (const [carrier, segs] of byCarrier) {
    const open = expanded.has(`carrier:${carrier}`) || segs.length <= fan;
    const visible = open ? segs : segs.slice(0, fan);
    for (const s of visible) {
      anchorByName[s.name] = s.name;
      drawnSegments.push(s);
    }
    if (!open) {
      const hidden = segs.slice(fan);
      folded.set(carrier, { hidden, total: segs.length });
      for (const s of hidden) anchorByName[s.name] = TREE_ID.carrierOverflow(carrier);
    }
  }
  const hiddenSegments = [...folded.values()].reduce((n, f) => n + f.hidden.length, 0);
  // The budget, on what is DRAWN after the fold: the pills, the folds, the devices.
  const boxCount = drawnSegments.length + folded.size + devices.length + (l0.unplaced ? 1 : 0) + 2;
  if (boxCount > TREE_BUDGET_BOXES) return empty(`${boxCount} boxes exceed the canvas budget of ${TREE_BUDGET_BOXES} after folding (the tree's cap)`);

  // ---- the Internet and the gateways ----
  const uplinks = [...(options.uplinks ?? [])];
  const showInternet = graph.nonLocal.length > 0 || uplinks.length > 0;
  const crossing: Crossing | null = graph.wanEdge && graph.wanEdge.crossingEdges > 0 ? { edges: graph.wanEdge.crossingEdges, bytes: graph.wanEdge.bytes, flows: graph.wanEdge.flows, inferred: graph.wanEdge.inferred } : null;
  const gateways = uplinks.length ? uplinks : crossing && graph.routers.length === 1 ? [graph.routers[0]!.name] : [];

  // ---- dagre: the devices, the pills and the folds, ranked by the edges ----
  const g = new dagre.graphlib.Graph({ multigraph: false, compound: false });
  g.setGraph({ rankdir: "TB", nodesep: TREE_NODE_SEP, ranksep: TREE_RANK_SEP, marginx: CANVAS_PAD, marginy: CANVAS_PAD });
  g.setDefaultEdgeLabel(() => ({}));
  if (showInternet) g.setNode(ID.internet, { width: INTERNET_W, height: TIER_INTERNET_H - 12 });
  const nodeIdOf = (name: string) => deviceNodeId(name, routerNames);
  for (const d of devices) g.setNode(nodeIdOf(d.name), { width: DEVICE_W, height: DEVICE_H });
  const sizeOf = new Map<string, ReturnType<typeof pillSize>>();
  for (const seg of drawnSegments) {
    const size = pillSize((chipsByCidr.get(seg.observedCidr) ?? []).length, seg.roleBadges.length + (seg.ghostCount ? 1 : 0));
    sizeOf.set(seg.name, size);
    g.setNode(ID.segment(seg.name), { width: size.w, height: size.h });
  }
  for (const carrier of folded.keys()) g.setNode(TREE_ID.carrierOverflow(carrier), { width: TILE_W, height: OVERFLOW_LANE_H });
  if (l0.unplaced) g.setNode(ID.unplaced, { width: TILE_W, height: TILE_H });
  // Ranking edges: the Internet over its gateways; a link from the shallower
  // device to the deeper; a carrier over its pills; a router over a pill no
  // switch carries. The routers with no edge at all still sit at rank 0.
  for (const name of gateways) if (showInternet && deviceNames.has(name)) g.setEdge(ID.internet, nodeIdOf(name));
  for (const l of links) {
    if (!deviceNames.has(l.a.device) || !deviceNames.has(l.b.device)) continue;
    const da = depths.get(l.a.device)?.depth ?? 0;
    const db = depths.get(l.b.device)?.depth ?? 0;
    const [from, to] = da <= db ? [l.a.device, l.b.device] : [l.b.device, l.a.device];
    g.setEdge(nodeIdOf(from), nodeIdOf(to));
  }
  for (const seg of drawnSegments) {
    const carrier = carrierKey(seg);
    if (carrier && deviceNames.has(carrier)) g.setEdge(nodeIdOf(carrier), ID.segment(seg.name));
  }
  for (const carrier of folded.keys()) if (deviceNames.has(carrier)) g.setEdge(nodeIdOf(carrier), TREE_ID.carrierOverflow(carrier));
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
        data: { kind: "router", name: d.name, segments: [...r.segments], drawnLegs: r.segments.filter((s) => anchorByName[s]).length, uplink: uplinks.includes(d.name), crossing: gateways.includes(d.name) ? crossing : null, hue: hueByRouter.get(d.name)!, source: source(d.name), confidence: confidence(d.name) },
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
    };
    nodes.push({ id, type: "device", position: abs, width: DEVICE_W, height: DEVICE_H, zIndex: 2, ...common, data });
  }
  const multi = new Set(extraByHost.keys());
  for (const seg of drawnSegments) {
    const id = ID.segment(seg.name);
    const size = sizeOf.get(seg.name)!;
    const abs = pinned(`segment:${seg.observedCidr}`, topLeft(id));
    const carrier = carrierOf.get(seg.name) ?? null;
    const served = graph.routers.filter((r) => r.segments.includes(seg.name)).map((r) => ({ router: r.name, hue: hueByRouter.get(r.name)! }));
    const hue = carrier ? hueOfDevice(carrier) : (served[0]?.hue ?? HUE_GREY);
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
      served,
      rows: size.rows,
      carrier,
      hue,
      source: source(seg.name),
      confidence: confidence(seg.name),
    };
    nodes.push({ id, type: "segment", position: abs, width: size.w, height: size.h, zIndex: 1, ...common, data });
    const chips = chipsByCidr.get(seg.observedCidr) ?? [];
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
  for (const [carrier, f] of folded) {
    const id = TREE_ID.carrierOverflow(carrier);
    nodes.push({ id, type: "overflow", position: topLeft(id), width: TILE_W, height: OVERFLOW_LANE_H, draggable: false, ...common, data: { kind: "overflow", siteKey: `carrier:${carrier}`, hidden: f.hidden.length, total: f.total, expanded: false } });
  }
  if (l0.unplaced) {
    nodes.push({ id: ID.unplaced, type: "unplaced", position: topLeft(ID.unplaced), width: TILE_W, height: TILE_H, draggable: false, ...common, data: { kind: "unplaced", count: l0.unplaced.count } });
  }

  // ---- edges ----
  const edges: CanvasEdge[] = [];
  if (showInternet) for (const name of gateways) if (deviceNames.has(name)) edges.push({ id: `trunk:internet>${name}`, type: "trunk", source: ID.internet, target: nodeIdOf(name), sourceHandle: HANDLE.bottom, targetHandle: HANDLE.top, data: { kind: "trunk", bytes: crossing?.bytes ?? 0, flows: crossing?.flows ?? 0 } });
  // The links (§2.3): between two devices, or from a device to a host's icon.
  for (const l of links) {
    const endId = (dev: string): string | null => (deviceNames.has(dev) ? nodeIdOf(dev) : graph.nodeById.has(dev) ? ID.host(dev) : null);
    const da = depths.get(l.a.device)?.depth ?? 99;
    const db = depths.get(l.b.device)?.depth ?? 99;
    const [from, to] = da <= db ? [l.a, l.b] : [l.b, l.a];
    const src = endId(from.device);
    const dst = endId(to.device);
    if (!src || !dst) continue;
    const hueSrc = deviceNames.has(from.device) ? hueOfDevice(from.device) : HUE_GREY;
    const segs = linkSegments(l);
    edges.push({
      id: TREE_ID.link(l.index),
      type: "link",
      source: src,
      target: dst,
      sourceHandle: HANDLE.bottom,
      targetHandle: HANDLE.top,
      zIndex: EDGE_Z,
      data: { kind: "link", a: from.device, aPort: from.port, b: to.device, bPort: to.port, mode: l.mode, native: l.native, vlans: l.vlans, state: l.state, hue: hueSrc, source: (l.source as Source | null) ?? null, label: `${from.port} — ${to.port} · ${l.mode}${segs.length ? ` · ${segs.join(", ")}` : ""}${l.state !== "up" ? ` · ${l.state}` : ""}`, index: l.index },
    });
  }
  // A switch carrying a pill (§7.1): the segment is a label on the branch.
  for (const seg of drawnSegments) {
    const carrier = carrierOf.get(seg.name);
    if (!carrier || !deviceNames.has(carrier)) continue;
    edges.push({ id: TREE_ID.carry(carrier, seg.name), type: "leg", className: LEG_CLASS, source: nodeIdOf(carrier), target: ID.segment(seg.name), sourceHandle: HANDLE.bottom, targetHandle: HANDLE.top, zIndex: EDGE_Z, data: { kind: "carry", device: carrier, segment: seg.name, label: seg.name, hue: hueOfDevice(carrier) } });
  }
  // A router's legs are the config's `nets[]`: one per pill, re-homed by drag; folded ones go to the `+N more` pill.
  for (const r of graph.routers) {
    const hue = hueByRouter.get(r.name)!;
    const foldedTo = new Map<string, number>();
    for (const name of r.segments) {
      const anchor = anchorByName[name];
      if (!anchor) continue;
      if (anchor !== name) {
        foldedTo.set(anchor, (foldedTo.get(anchor) ?? 0) + 1);
        continue;
      }
      const segOf = graph.segmentByName.get(name);
      edges.push({ id: `leg:${r.name}>${name}`, type: "leg", className: LEG_CLASS, source: ID.router(r.name), target: ID.segment(name), sourceHandle: HANDLE.bottom, targetHandle: HANDLE.top, zIndex: EDGE_Z, data: { kind: "leg", label: name, site: segOf ? `carrier:${carrierKey(segOf)}` : undefined, hue } });
    }
    for (const [anchor, n] of foldedTo) edges.push({ id: `legs:${r.name}>${anchor}`, type: "leg", className: LEG_CLASS, source: ID.router(r.name), target: anchor, sourceHandle: HANDLE.bottom, targetHandle: HANDLE.top, zIndex: EDGE_Z, data: { kind: "leg", label: `${n} segment${n === 1 ? "" : "s"} behind +N more`, folded: n, site: siteKeyOfOverflowAnchor(anchor) ?? undefined, hue } });
  }
  // A multi-homed host's stubs.
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
    edges.push({ id: `stub:${st.host}>${st.segment}`, type: "leg", className: LEG_CLASS, source: ID.host(st.host), target: ID.segment(st.segment), sourceHandle: HANDLE.bottom, targetHandle: HANDLE.top, zIndex: EDGE_Z, data: { kind: "stub", host: st.host, segment: st.segment, label: st.ip ?? st.segment } });
  }
  for (const f of stubFolded.values()) edges.push({ id: `stubs:${f.host}>${f.anchor}`, type: "leg", className: LEG_CLASS, source: ID.host(f.host), target: f.anchor, sourceHandle: HANDLE.bottom, targetHandle: HANDLE.top, zIndex: EDGE_Z, data: { kind: "stub", host: f.host, segment: f.anchor, label: `${f.n} segment${f.n === 1 ? "" : "s"} behind +N more`, folded: f.n } });
  // The observed pairs: evidence, drawn under Light-up or for the selection.
  for (const arc of l0.arcs) {
    const srcName = nameOfCidr.get(arc.a) ?? arc.a;
    const dstName = nameOfCidr.get(arc.b) ?? arc.b;
    const src = anchorByName[srcName] === srcName ? ID.segment(srcName) : anchorByName[srcName];
    const dst = anchorByName[dstName] === dstName ? ID.segment(dstName) : anchorByName[dstName];
    if (!src || !dst) continue;
    const key = arcKey(arc.a, arc.b);
    edges.push({ id: `arc:${arc.kind}:${key}`, type: "arc", source: src, target: dst, sourceHandle: HANDLE.bottom, targetHandle: HANDLE.bottomIn, style: { strokeWidth: arc.strokeWidth }, data: { kind: "arc", key, arcKind: arc.kind, bytes: arc.bytes, flows: arc.flows, pairs: arc.pairs, label: arc.kind === "asserted" && !arc.hasTraffic ? "conduit (declared, no traffic observed)" : `${arc.bytes.toLocaleString()} bytes · ${arc.flows.toLocaleString()} flows${arc.pairs > 1 ? ` · ${arc.pairs} pairs` : ""}` } });
  }
  void cidrOfName;

  return { nodes, edges, width: (gw ?? 0) + CANVAS_PAD, height: (gh ?? 0) + CANVAS_PAD, overBudget: null, hiddenSegments, hiddenPairs: l0.hiddenPairs, anchorByName };
}
