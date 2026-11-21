/**
 * The FREE layout (B2, `SPEC.md` "Free layout"): for a design that is authored, not cast -- an
 * untitled design, a draft, a twin with no cast or discovery provenance -- every box stays exactly
 * where it was dropped or dragged (its pin), the first placement being the tree's own (dagre's), and
 * the edges are drawn POINT TO POINT between their two ends: no comb, no route through the
 * gutters, a router's legs leaving the router itself. Pure: a pass over the tree's placement.
 *
 * Point to point (2026-11-20, round 2): every edge between two drawn things runs from the centre
 * of one ICON to the centre of the other, clipped where it crosses each icon's outline (a router's
 * circle, a switch's rounded box, a host's circle). The handles do not decide it: the line is the
 * same whichever end a drag started from (`freeEnds` orders its two ends by node id). A transit's
 * pill sits on its own line, at the midpoint, nudged along the line (up to ±30% of its length) and
 * then off it when it would cover a node's name or another pill (`freeLayout` sets `data.pill`).
 */
import { DEVICE_H } from "../layout/geometry";
import { absolutePosition, ID, type CanvasEdge, type CanvasNode, type LabelSide, type Placement } from "./placement";

/** A fan wide enough that nothing folds: a free design shows every segment it has. */
export const FREE_FAN = 10_000;

/** A free edge's z: under the nodes (2), so names and icons sit on top of the lines. */
export const FREE_EDGE_Z = 1;

/** The side of a router's / switch's drawn icon box (`.cv-icon-box`), flush with the bottom-centre of its node. */
export const ICON_BOX = 34;
/** A switch's icon box corner radius (`.cv-device .cv-icon-box`). */
const ICON_BOX_RADIUS = 8;

export interface Point {
  x: number;
  y: number;
}

/** A node's icon as the free layout draws to it: a circle, or a rounded box (`half` its half side). */
export type Icon = { cx: number; cy: number; shape: "circle"; r: number } | { cx: number; cy: number; shape: "box"; half: number; hy: number; radius: number };

/** What `iconOf` needs to know of a node: its type, data kind, absolute top-left and size. */
export interface IconSource {
  type?: string;
  kind?: string;
  x: number;
  y: number;
  w: number;
  h: number;
}

/** The icon a node draws (`nodes.tsx`): a router's ring and a switch's box are flush with the
 *  bottom-centre of the node, its name above; a host is its own circle; anything else its box. */
export function iconOf(n: IconSource): Icon {
  const kind = n.kind ?? n.type;
  const cx = n.x + n.w / 2;
  if (kind === "router") return { cx, cy: n.y + n.h - ICON_BOX / 2, shape: "circle", r: ICON_BOX / 2 };
  if (kind === "device" || kind === "segment" || n.type === "switch" || n.type === "impliedSwitch") return { cx, cy: n.y + n.h - ICON_BOX / 2, shape: "box", half: ICON_BOX / 2, hy: ICON_BOX / 2, radius: ICON_BOX_RADIUS };
  if (kind === "host" || kind === "rungs") return { cx, cy: n.y + n.h / 2, shape: "circle", r: Math.min(n.w, n.h) / 2 };
  return { cx, cy: n.y + n.h / 2, shape: "box", half: n.w / 2, hy: n.h / 2, radius: 0 };
}

/** Where the ray from the icon's centre towards `to` crosses the icon's outline. Pure. */
export function clipToIcon(icon: Icon, to: Point): Point {
  const dx = to.x - icon.cx;
  const dy = to.y - icon.cy;
  const len = Math.hypot(dx, dy);
  if (len < 1e-6) return { x: icon.cx, y: icon.cy };
  if (icon.shape === "circle") return { x: icon.cx + (dx / len) * icon.r, y: icon.cy + (dy / len) * icon.r };
  // The box: where the ray leaves the rectangle, then onto a rounded corner when it leaves through one.
  const t = Math.min(dx ? icon.half / Math.abs(dx) : Infinity, dy ? icon.hy / Math.abs(dy) : Infinity);
  const p = { x: dx * t, y: dy * t };
  const rad = icon.radius;
  const kx = icon.half - rad;
  const ky = icon.hy - rad;
  if (rad > 0 && Math.abs(p.x) > kx && Math.abs(p.y) > ky) {
    // The corner circle: centre c = (±kx, ±ky), radius rad; solve |s·d − c| = rad for the far root.
    const c = { x: Math.sign(dx) * kx, y: Math.sign(dy) * ky };
    const ux = dx / len;
    const uy = dy / len;
    const b = ux * c.x + uy * c.y;
    const disc = b * b - (c.x * c.x + c.y * c.y - rad * rad);
    const s = b + Math.sqrt(Math.max(0, disc));
    return { x: icon.cx + ux * s, y: icon.cy + uy * s };
  }
  return { x: icon.cx + p.x, y: icon.cy + p.y };
}

/** The two ends of a free edge between icons `a` and `b`: each the centre-to-centre line clipped at
 *  its own icon's outline. Symmetric: `freeEnds(b, a)` is `freeEnds(a, b)` reversed. Pure. */
export function freeEnds(a: Icon, b: Icon): [Point, Point] {
  return [clipToIcon(a, { x: b.cx, y: b.cy }), clipToIcon(b, { x: a.cx, y: a.cy })];
}

/** Where a pill stands on its line: `t` along it (0.5 the midpoint), `off` px along its left normal. */
export interface PillAt {
  t: number;
  off: number;
}

/** A point `t` of the way along p→q, `off` px off it along the normal. The normal is taken in the
 *  canonical direction (`p` the end of the smaller node id), so the spot does not depend on which way
 *  the edge was dragged. Pure. */
export function alongLine(p: Point, q: Point, at: PillAt): Point {
  const dx = q.x - p.x;
  const dy = q.y - p.y;
  const len = Math.hypot(dx, dy) || 1;
  return { x: p.x + dx * at.t + (-dy / len) * at.off, y: p.y + dy * at.t + (dx / len) * at.off };
}

type Box = { l: number; t: number; r: number; b: number };
const overlaps = (a: Box, b: Box, pad = 2) => a.l < b.r + pad && b.l < a.r + pad && a.t < b.b + pad && b.t < a.b + pad;
const area = (a: Box, b: Box) => Math.max(0, Math.min(a.r, b.r) - Math.max(a.l, b.l)) * Math.max(0, Math.min(a.b, b.b) - Math.max(a.t, b.t));

/** The pill's text (`nodes.tsx` `LinkEdge`, seglink) and its drawn box at a centre, estimated from the
 *  label font (11px, `labelBgPadding` 6×2). */
export function pillText(d: { segment: string; cidr?: string }): string {
  return d.cidr ? `${d.segment} · ${d.cidr}` : d.segment;
}
const PILL_CHAR_W = 6.3;
const PILL_H = 18;
export function pillBox(c: Point, text: string): Box {
  const w = text.length * PILL_CHAR_W + 12;
  return { l: c.x - w / 2, r: c.x + w / 2, t: c.y - PILL_H / 2, b: c.y + PILL_H / 2 };
}

/** The name label a node carries, as a box (a router's, switch's or implied switch's name and sub
 *  line above its icon -- or beside / below it, `side`; a host's caption under it), or null.
 *  Estimated from the label fonts. */
export function nameBox(n: IconSource & { label?: string | null; sub?: string | null }, side: LabelSide = "above"): Box | null {
  const kind = n.kind ?? n.type;
  const chars = Math.max(n.label?.length ?? 0, n.sub?.length ?? 0);
  if (!chars) return null;
  if (kind === "router" || kind === "device" || kind === "segment") {
    const cx = n.x + n.w / 2;
    const cy = n.y + n.h - ICON_BOX / 2;
    const full = chars * 7.4 + 8;
    if (side === "right") return { l: cx + ICON_BOX / 2 + 4, r: cx + ICON_BOX / 2 + 4 + full, t: cy - LABEL_H / 2, b: cy + LABEL_H / 2 };
    if (side === "left") return { l: cx - ICON_BOX / 2 - 4 - full, r: cx - ICON_BOX / 2 - 4, t: cy - LABEL_H / 2, b: cy + LABEL_H / 2 };
    if (side === "below") return { l: cx - full / 2, r: cx + full / 2, t: n.y + n.h + 2, b: n.y + n.h + 2 + LABEL_H };
    const w = Math.min(n.w, chars * 7.4 + 4);
    // The name and its sub line stack above the icon (flex-end): the box's top to the icon's top.
    return { l: cx - w / 2, r: cx + w / 2, t: n.y + n.h - ICON_BOX - 34, b: n.y + n.h - ICON_BOX - 2 };
  }
  if (kind === "host") {
    const w = chars * 6.2 + 6;
    return { l: n.x + n.w / 2 - w / 2, r: n.x + n.w / 2 + w / 2, t: n.y + n.h + 5, b: n.y + n.h + 19 };
  }
  return null;
}
/** A name and its sub line, two 12px lines with their halo. */
const LABEL_H = 32;

/** Whether the segment p→q passes through the box (Liang–Barsky). Pure. */
export function segmentHitsBox(p: Point, q: Point, b: Box): boolean {
  const dx = q.x - p.x;
  const dy = q.y - p.y;
  let lo = 0;
  let hi = 1;
  for (const [den, num] of [[-dx, p.x - b.l], [dx, b.r - p.x], [-dy, p.y - b.t], [dy, b.b - p.y]] as const) {
    if (den === 0) {
      if (num < 0) return false;
      continue;
    }
    const t = num / den;
    if (den < 0) lo = Math.max(lo, t);
    else hi = Math.min(hi, t);
    if (lo > hi) return false;
  }
  return true;
}

/** The sides a name tries, in order: above (the tree's own), then beside the icon, then below. */
const LABEL_SIDES: LabelSide[] = ["above", "right", "left", "below"];

/** Where each router's, switch's and implied switch's name stands (2026-11-21): above its icon
 *  unless an edge would cross it there, else the first side no edge crosses (the fewest, when every
 *  side is crossed). Only nodes that move off "above" are in the map. Pure. */
export function labelSides(srcs: ReadonlyMap<string, IconSource & { label?: string | null; sub?: string | null }>, lines: ReadonlyArray<{ p: Point; q: Point }>): Map<string, LabelSide> {
  const out = new Map<string, LabelSide>();
  for (const [id, s] of srcs) {
    const kind = s.kind ?? s.type;
    if (kind !== "router" && kind !== "device" && kind !== "segment") continue;
    let best: { side: LabelSide; hits: number } | null = null;
    for (const side of LABEL_SIDES) {
      const box = nameBox(s, side);
      if (!box) break;
      const hits = lines.filter((l) => segmentHitsBox(l.p, l.q, box)).length;
      if (!best || hits < best.hits) best = { side, hits };
      if (!hits) break;
    }
    if (best && best.side !== "above") out.set(id, best.side);
  }
  return out;
}

/** The "Not carried" box in the free layout (2026-11-21): just above the one segment it holds, or
 *  beside the first when it holds several -- the other spot, or the left, when that is taken --
 *  never more than a row away. Its top-left, or null when it holds nothing drawn. Pure. */
export function uncarriedSpot(box: { w: number; h: number }, targets: readonly Box[], taken: readonly Box[]): Point | null {
  const first = targets[0];
  if (!first) return null;
  const GAP = 16;
  const cx = (first.l + first.r) / 2;
  const cy = (first.t + first.b) / 2;
  const above = { x: cx - box.w / 2, y: first.t - GAP - box.h };
  const right = { x: first.r + GAP, y: cy - box.h / 2 };
  const left = { x: first.l - GAP - box.w, y: cy - box.h / 2 };
  const spots = targets.length === 1 ? [above, right, left] : [right, above, left];
  const clear = spots.find((at) => !taken.some((t) => overlaps({ l: at.x, t: at.y, r: at.x + box.w, b: at.y + box.h }, t, 4)));
  return clear ?? spots[0]!;
}

/** The candidate spots a pill tries, in order: the midpoint, then along the line out to ±30%, then
 *  the same spots off the line on either side. */
const ALONG = [0.5, 0.44, 0.56, 0.38, 0.62, 0.32, 0.68, 0.26, 0.74, 0.2, 0.8];
const PILL_SPOTS: PillAt[] = [...ALONG.map((t) => ({ t, off: 0 })), ...[16, -16, 28, -28].flatMap((off) => ALONG.map((t) => ({ t, off })))];

/** Where each pill stands: the first spot (`PILL_SPOTS`) whose box covers no node's name, no other
 *  icon and no pill already placed; failing that, the one covering the least name. Pure. */
export function placePills(lines: ReadonlyArray<{ id: string; p: Point; q: Point; text: string }>, names: readonly Box[], icons: readonly Box[], edges: ReadonlyArray<{ id: string; p: Point; q: Point }> = []): Map<string, PillAt> {
  const out = new Map<string, PillAt>();
  const placed: Box[] = [];
  for (const l of [...lines].sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0))) {
    let best: { at: PillAt; box: Box; cost: number } | null = null;
    for (const at of PILL_SPOTS) {
      const box = pillBox(alongLine(l.p, l.q, at), l.text);
      const nameCost = names.reduce((s, n) => s + area(box, n), 0);
      const pillCost = placed.reduce((s, n) => s + area(box, n), 0);
      const iconCost = icons.reduce((s, n) => s + area(box, n), 0);
      // Another edge's line running under the pill (2026-11-21: router1-1--router3-1 cut through
      // router1-1--router2-1's pill where both leave router1-1).
      const edgeHits = edges.filter((e) => e.id !== l.id && segmentHitsBox(e.p, e.q, box)).length;
      if (!edgeHits && !names.some((n) => overlaps(box, n)) && !placed.some((n) => overlaps(box, n)) && !icons.some((n) => overlaps(box, n, 0))) {
        best = { at, box, cost: 0 };
        break;
      }
      // A name is never to be covered: it weighs far more than a pill or an icon.
      const cost = nameCost * 100 + pillCost * 10 + iconCost + edgeHits * 2000;
      if (!best || cost < best.cost) best = { at, box, cost };
    }
    out.set(l.id, best!.at);
    placed.push(best!.box);
  }
  return out;
}

/** What the free layout reads of a node: its icon source and label, from its placement. */
function sourceOf(nodes: readonly CanvasNode[], n: CanvasNode): (IconSource & { label: string | null; sub: string | null }) | null {
  const at = absolutePosition(nodes, n.id);
  if (!at) return null;
  const d = n.data;
  const w = n.width ?? 0;
  const h = n.height ?? 0;
  const label = d.kind === "router" || d.kind === "device" || d.kind === "segment" ? d.name : d.kind === "host" ? d.short : null;
  const sub = d.kind === "router" ? `${d.segments.length} legs` : d.kind === "device" ? `${d.ports.length || d.segments.length} ports` : d.kind === "segment" ? d.cidr : null;
  return { type: n.type, kind: d.kind, x: at.x, y: at.y, w: w || (d.kind === "host" ? 30 : 0), h: h || (d.kind === "host" ? 30 : DEVICE_H), label, sub };
}

/** The ends of a free edge in canonical order -- the end of the smaller node id first -- with which
 *  of them is the edge's own source. */
export function canonicalEnds(sourceId: string, targetId: string, src: Icon, dst: Icon): { p: Point; q: Point; flipped: boolean } {
  const flipped = targetId < sourceId;
  const [a, b] = flipped ? freeEnds(dst, src) : freeEnds(src, dst);
  return { p: a, q: b, flipped };
}

/** The "Not carried" box's node id (`tree.ts`). */
const UNCARRIED = "chrome:uncarried";

/** The room a transit needs between two routers laid side by side: its pill (name · /30) and a margin. */
const SPREAD_GAP = 48;
/** How far the zig-zag's second row sits below the first: a pill, a name and a margin. */
const SPREAD_DROP = 150;

/**
 * The first free placement of linked routers (2026-11-21): dagre ranks a transit like a parent and
 * child, so a triangle came out as a column of routers a few px apart, every pill across another
 * line. Each set of routers joined by transits whose routers have no position yet (no pin, nothing
 * settled -- `pinned` holds the keys that have one) is laid out left to right in the order of its
 * chain, alternately on two rows (a zig-zag, so no three stand on one line), each router carrying
 * what hangs under it (its switches and hosts that have no position either, shape kept), the
 * routers far enough apart for a pill between them and no two carried groups overlapping. A router
 * with a position never moves. Pure.
 */
export function spreadRouters(p: Placement, pinned: ReadonlySet<string>): CanvasNode[] {
  const byId = new Map(p.nodes.map((n) => [n.id, n]));
  const keyOf = (n: CanvasNode): string | null => {
    const d = n.data;
    if (d.kind === "router" || d.kind === "device") return `host:${d.name}`;
    if (d.kind === "host") return `host:${d.nodeId}`;
    if (d.kind === "segment") return `segment:${d.cidr}`;
    return null;
  };
  const movable = (n: CanvasNode | undefined): n is CanvasNode => {
    if (!n || n.parentId) return false;
    const k = keyOf(n);
    return k !== null && !pinned.has(k);
  };
  const fresh = (id: string) => byId.get(id)?.data.kind === "router" && movable(byId.get(id));
  // The transit graph among routers with no position.
  const peers = new Map<string, string[]>();
  for (const e of p.edges) {
    if (e.data?.kind !== "seglink" || !fresh(e.source) || !fresh(e.target) || e.source === e.target) continue;
    peers.set(e.source, [...(peers.get(e.source) ?? []), e.target]);
    peers.set(e.target, [...(peers.get(e.target) ?? []), e.source]);
  }
  if (!peers.size) return p.nodes;
  // What hangs under each router: reached over its legs and access edges, through nodes with no position.
  const adj = new Map<string, string[]>();
  for (const e of p.edges) {
    if (e.data?.kind === "seglink") continue;
    adj.set(e.source, [...(adj.get(e.source) ?? []), e.target]);
    adj.set(e.target, [...(adj.get(e.target) ?? []), e.source]);
  }
  const owner = new Map<string, string>();
  const order = [...peers.keys()].sort((a, b) => {
    const pa = byId.get(a)!.position;
    const pb = byId.get(b)!.position;
    return pa.y - pb.y || pa.x - pb.x || (a < b ? -1 : 1);
  });
  for (const r of order) owner.set(r, r);
  for (const r of order) {
    const queue = [r];
    while (queue.length) {
      const at = queue.shift()!;
      for (const nb of adj.get(at) ?? []) {
        const n = byId.get(nb);
        if (owner.has(nb) || !movable(n) || n.data.kind === "router") continue;
        owner.set(nb, r);
        queue.push(nb);
      }
    }
  }
  const sizeOf = (n: CanvasNode) => ({ w: n.width ?? (n.data.kind === "host" ? 30 : 0), h: n.height ?? (n.data.kind === "host" ? 30 : DEVICE_H) });
  const delta = new Map<string, { dx: number; dy: number }>();
  const seen = new Set<string>();
  for (const start of order) {
    if (seen.has(start)) continue;
    // The component in chain order: depth first from its top-left router, nearest peer first.
    const chain: string[] = [];
    const visit = (r: string) => {
      seen.add(r);
      chain.push(r);
      for (const nb of [...(peers.get(r) ?? [])].sort((a, b) => order.indexOf(a) - order.indexOf(b))) if (!seen.has(nb)) visit(nb);
    };
    visit(start);
    if (chain.length < 2) continue;
    const pillW = Math.max(...p.edges.filter((e) => e.data?.kind === "seglink" && chain.includes(e.source) && chain.includes(e.target)).map((e) => pillText(e.data as { segment: string; cidr?: string }).length * PILL_CHAR_W + 12));
    const groups = chain.map((r) => {
      const members = p.nodes.filter((n) => owner.get(n.id) === r);
      const l = Math.min(...members.map((n) => n.position.x));
      const rr = Math.max(...members.map((n) => n.position.x + sizeOf(n).w));
      return { r, members, l, rr, at: byId.get(r)!.position };
    });
    const baseX = Math.min(...groups.map((g) => g.l));
    const baseY = Math.min(...groups.map((g) => g.at.y));
    let cursor = baseX;
    let lastCx = -Infinity;
    groups.forEach((g, i) => {
      const half = sizeOf(byId.get(g.r)!).w / 2;
      const routerOff = g.at.x - g.l;
      // Past the last group, and this router's icon a pill (and a margin) past the last router's.
      const left = Math.max(cursor, lastCx + pillW + SPREAD_GAP - half - routerOff);
      const x = left + routerOff;
      const d = { dx: x - g.at.x, dy: baseY + (i % 2 ? SPREAD_DROP : 0) - g.at.y };
      for (const m of g.members) delta.set(m.id, d);
      cursor = left + (g.rr - g.l) + SPREAD_GAP;
      lastCx = x + half;
    });
  }
  if (!delta.size) return p.nodes;
  return p.nodes.map((n) => {
    const d = delta.get(n.id);
    return d ? { ...n, position: { x: Math.round(n.position.x + d.dx), y: Math.round(n.position.y + d.dy) } } : n;
  });
}

/** Lay a tree placement out free. `pinned`: the pin keys that already have a position (a pin or a
 *  settled spot); given, linked routers without one are spread first (`spreadRouters`). */
export function freeLayout(placed: Placement, pinned: ReadonlySet<string> | null = null): Placement {
  const p = pinned ? { ...placed, nodes: spreadRouters(placed, pinned) } : placed;
  const ids = new Set(p.nodes.map((n) => n.id));
  const sources = new Map(p.nodes.flatMap((n) => {
    const s = n.data.kind === "annotation" || n.data.kind === "site" ? null : sourceOf(p.nodes, n);
    return s ? [[n.id, s] as const] : [];
  }));
  const edges: CanvasEdge[] = p.edges.map((e) => {
    const data = { ...(e.data ?? {}) } as Record<string, unknown>;
    delete data["route"];
    data["free"] = true;
    // A router's leg leaves the router, not a site tile under it. A host's access edge has the same
    // `leg:<name>><segment>` id (tree.ts `TREE_ID.access`) and keeps its switch as the source: only
    // a name that IS a router is re-sourced (2026-11-20: re-sourcing every leg pointed the access
    // edges at a router that does not exist, and the hosts floated unconnected).
    const leg = /^leg:([^>]+)>/.exec(e.id);
    const router = leg ? ID.router(leg[1]!) : null;
    const source = router && e.data?.kind === "leg" && ids.has(router) ? router : e.source;
    // Under the nodes (z 2), not over them as in the tree: a line that passes a name runs beneath
    // it (the name wears a halo, `canvas.css`), never across its text.
    return { ...e, source, data, zIndex: FREE_EDGE_Z } as CanvasEdge;
  });
  // The "Not carried" box beside what it holds, not where dagre left it (2026-11-21).
  let nodes = p.nodes;
  const held = edges.filter((e) => e.source === UNCARRIED).map((e) => sources.get(e.target)).filter((s): s is NonNullable<typeof s> => !!s);
  const chrome = nodes.find((n) => n.id === UNCARRIED);
  if (chrome && held.length) {
    const asBox = (s: IconSource) => ({ l: s.x, t: s.y, r: s.x + s.w, b: s.y + s.h });
    const taken = [...sources.entries()].filter(([id]) => id !== UNCARRIED).map(([, s]) => asBox(s));
    const at = uncarriedSpot({ w: chrome.width ?? 0, h: chrome.height ?? 0 }, held.map(asBox), taken);
    if (at) {
      nodes = nodes.map((n) => (n.id === UNCARRIED ? { ...n, position: at, parentId: undefined } : n));
      sources.set(UNCARRIED, { ...sources.get(UNCARRIED)!, x: at.x, y: at.y });
    }
  }
  // Every edge as drawn (point to point between icons), for the names and the pills to keep clear of.
  const drawn: Array<{ e: CanvasEdge; p: Point; q: Point }> = [];
  for (const e of edges) {
    const s = sources.get(e.source);
    const t = sources.get(e.target);
    if (!s || !t) continue;
    const { p: a, q: b } = canonicalEnds(e.source, e.target, iconOf(s), iconOf(t));
    drawn.push({ e, p: a, q: b });
  }
  // A name an edge would cross moves beside (or below) its icon (2026-11-21).
  const sides = labelSides(sources, drawn);
  if (sides.size) nodes = nodes.map((n) => (sides.has(n.id) ? ({ ...n, data: { ...n.data, labelSide: sides.get(n.id) } } as CanvasNode) : n));
  // Every pill (a transit's) on its own line, clear of the names and of each other.
  const lines: Array<{ id: string; p: Point; q: Point; text: string }> = [];
  for (const { e, p: a, q: b } of drawn) {
    const d = e.data as { kind?: string; segment?: string; cidr?: string } | undefined;
    if (d?.kind !== "seglink" || !d.segment) continue;
    lines.push({ id: e.id, p: a, q: b, text: pillText({ segment: d.segment, cidr: d.cidr }) });
  }
  if (lines.length) {
    const names = [...sources.entries()].map(([id, s]) => nameBox(s, sides.get(id))).filter((b): b is Box => b !== null);
    const icons = [...sources.values()].map((s) => {
      const i = iconOf(s);
      const hx = i.shape === "circle" ? i.r : i.half;
      const hy = i.shape === "circle" ? i.r : i.hy;
      return { l: i.cx - hx, r: i.cx + hx, t: i.cy - hy, b: i.cy + hy };
    });
    const at = placePills(lines, names, icons, drawn.map((d) => ({ id: d.e.id, p: d.p, q: d.q })));
    for (const e of edges) {
      const spot = at.get(e.id);
      if (spot) (e.data as Record<string, unknown>)["pill"] = spot;
    }
  }
  return { ...p, nodes, edges };
}

/** The ends of every free edge between two drawn nodes, from the placement (what the edge
 *  components draw while nothing is being dragged): canonical order, as `canonicalEnds`. Pure. */
export function freeEdgeEnds(p: Placement): Map<string, { p: Point; q: Point; flipped: boolean }> {
  const out = new Map<string, { p: Point; q: Point; flipped: boolean }>();
  for (const e of p.edges) {
    const s = p.nodes.find((n) => n.id === e.source);
    const t = p.nodes.find((n) => n.id === e.target);
    if (!s || !t) continue;
    const a = sourceOf(p.nodes, s);
    const b = sourceOf(p.nodes, t);
    if (!a || !b) continue;
    out.set(e.id, canonicalEnds(e.source, e.target, iconOf(a), iconOf(b)));
  }
  return out;
}
