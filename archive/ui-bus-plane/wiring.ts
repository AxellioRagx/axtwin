/**
 * The legs' geometry over the LIVE node boxes (rendering rule 2, track 4,
 * 2026-11-06): a leg leaves a router from the bottom of its icon and drops
 * onto its bus's line in the site's PORT REGION -- the right end of every
 * lane, left of the count badge, where no host sits -- one drop column per
 * source into the site, so parallel drops never overlap. Between the two it
 * runs orthogonally through the gutters (`route.ts`), never through a host
 * row, a bus label or a badge. A fan lands on its site's top edge at the
 * router's column and its rail runs straight down that column to each bus.
 * A multi-homed host's stub (rule 4) leaves its icon's top and lands on the
 * other bus's line the same way, from below when that bus is above it.
 *
 * Positions come from React Flow's node state, so a drag re-wires; sizes
 * are the measured ones when the DOM has them, else the placement's. A leg
 * the router cannot route (a port walled in) is a straight line. Pure --
 * no React, no DOM.
 */
import { DEVICE_H, SITE_PAD } from "../layout/geometry";
import { BUS_BADGE_W, BUS_LABEL_W, BUS_LINE_H, BUS_ROW_H, laneLineY, portColumnX } from "./bus";
import { absolutePosition, type CanvasData, type CanvasEdge, type Rect } from "./placement";
import { routeAll, type LegRequest, type Point, type RouteOptions } from "./route";

export interface WireNode {
  id: string;
  type?: string;
  parentId?: string;
  position: Point;
  width?: number;
  height?: number;
  measured?: { width?: number; height?: number };
  hidden?: boolean;
  data: CanvasData;
}

/** The side of a router's drawn icon box (`.cv-icon-box`), flush with the bottom-centre of its node. */
export const ROUTER_ICON = 34;
/** The badge on a bus line: this tall, centred on the line. */
const BADGE_H = 20;

export interface Wiring {
  /** Edge id -> the polyline to draw, `from` and `to` included. */
  routes: Map<string, Point[]>;
  /** The boxes the routing kept clear of, for the acceptance check. */
  obstacles: Rect[];
  /** The host rows alone (a leg must never cross one): the acceptance check's subject. */
  hostRows: Rect[];
}

/** The absolute box of a node: measured when the DOM has laid it out, else the placement's. */
export function boxOf(nodes: readonly WireNode[], n: WireNode): Rect | null {
  const at = absolutePosition(nodes, n.id);
  if (!at) return null;
  return { x: at.x, y: at.y, w: n.measured?.width ?? n.width ?? 0, h: n.measured?.height ?? n.height ?? 0 };
}

/** Every shown node's absolute box in one pass (a parent precedes its children in the placement's order). */
function boxes(shown: readonly WireNode[]): Map<string, Rect> {
  const out = new Map<string, Rect>();
  const byId = new Map(shown.map((n) => [n.id, n]));
  const absOf = (n: WireNode): Point => {
    const known = out.get(n.id);
    if (known) return { x: known.x, y: known.y };
    const parent = n.parentId ? byId.get(n.parentId) : undefined;
    const p = parent ? absOf(parent) : { x: 0, y: 0 };
    return { x: p.x + n.position.x, y: p.y + n.position.y };
  };
  for (const n of shown) {
    const at = absOf(n);
    out.set(n.id, { x: at.x, y: at.y, w: n.measured?.width ?? n.width ?? 0, h: n.measured?.height ?? n.height ?? 0 });
  }
  return out;
}

/** A channel keeps this much clear above a router's name (its name sits at the slot's top). */
const ROUTER_BOX_PAD_Y = 10;

/** The box a router draws (its node, `DEVICE_W` × `DEVICE_H`), with headroom above the name so a leg never grazes it. */
export function routerBox(slot: Rect): Rect {
  return { x: slot.x, y: slot.y - ROUTER_BOX_PAD_Y, w: slot.w, h: (slot.h || DEVICE_H) + ROUTER_BOX_PAD_Y };
}

/** The box a leg is clipped against in the straight-line fallback: a router's icon, else the node. */
export function clipBox(n: Pick<WireNode, "type">, box: Rect): Rect {
  if (n.type === "router") return { x: box.x + box.w / 2 - ROUTER_ICON / 2, y: box.y + box.h - ROUTER_ICON, w: ROUTER_ICON, h: ROUTER_ICON };
  return box;
}

const inside = (inner: Rect, outer: Rect) => inner.x >= outer.x && inner.y >= outer.y && inner.x + inner.w <= outer.x + outer.w && inner.y + inner.h <= outer.y + outer.h;
const union = (a: Rect, b: Rect): Rect => {
  const x = Math.min(a.x, b.x);
  const y = Math.min(a.y, b.y);
  return { x, y, w: Math.max(a.x + a.w, b.x + b.w) - x, h: Math.max(a.y + a.h, b.y + b.h) - y };
};

/** The host rows of a lane: its icons grouped by row (by their y within the lane), each row one box. An icon dragged clear of its lane is its own box. */
export function hostRowsOf(lane: Rect, icons: readonly Rect[]): Rect[] {
  const rows = new Map<number, Rect>();
  const loose: Rect[] = [];
  for (const b of icons) {
    if (!inside(b, lane)) {
      loose.push(b);
      continue;
    }
    const row = Math.round((b.y + b.h - lane.y) / BUS_ROW_H);
    const cur = rows.get(row);
    rows.set(row, cur ? union(cur, b) : { ...b });
  }
  return [...rows.values(), ...loose];
}

/**
 * Wire every leg, stub, fan and bus edge over the nodes as they stand.
 * `edges` is the list the canvas draws (after the fan collapse); edges of
 * other kinds are ignored.
 */
export function wire(nodes: readonly WireNode[], edges: readonly CanvasEdge[], options: Partial<RouteOptions> = {}): Wiring {
  const shown = nodes.filter((n) => !n.hidden);
  const byId = new Map(shown.map((n) => [n.id, n]));
  const box = boxes(shown);
  // Obstacles: each bus's host rows, label and badge; the chrome; each
  // router's drawn box. The lanes themselves (a drop ends on a lane's line,
  // inside its box) and the site tints are not.
  const obstacles: Rect[] = [];
  const hostRows: Rect[] = [];
  const iconsByLane = new Map<string, Rect[]>();
  for (const n of shown) {
    const b = box.get(n.id);
    if (!b) continue;
    const k = n.data.kind;
    if (k === "overflow" || k === "unplaced" || k === "internet") obstacles.push(b);
    else if (k === "router") obstacles.push(routerBox(b));
    else if (k === "segment") {
      obstacles.push({ x: b.x, y: b.y, w: BUS_LABEL_W, h: b.h - BUS_LINE_H });
      obstacles.push({ x: b.x + b.w - BUS_BADGE_W, y: laneLineY(b) - BADGE_H / 2, w: BUS_BADGE_W, h: BADGE_H });
    } else if ((k === "host" || k === "rungs") && n.parentId) {
      iconsByLane.set(n.parentId, [...(iconsByLane.get(n.parentId) ?? []), b]);
    }
  }
  for (const [laneId, icons] of iconsByLane) {
    const lane = box.get(laneId);
    const rows = lane ? hostRowsOf(lane, icons) : icons;
    hostRows.push(...rows);
    obstacles.push(...rows);
  }

  // The source port of a leg: a router's icon bottom; a chip's top (a stub leaves upward).
  const bottom = (id: string): Point | null => {
    const b = box.get(id);
    return b ? { x: b.x + b.w / 2, y: b.y + b.h } : null;
  };
  const top = (id: string): Point | null => {
    const b = box.get(id);
    return b ? { x: b.x + b.w / 2, y: b.y } : null;
  };
  const sourceX = (id: string) => box.get(id)?.x ?? 0;

  // The drop columns of a site's port region: one per source into the site
  // (a router, a fan entry, a multi-homed host), by the sources' x from the
  // left, so drops never cross one another.
  const fanEntryKey = (router: string, site: string) => `${router}|${site}`;
  const siteOfTarget = (id: string): string | null => {
    const n = byId.get(id);
    if (!n) return null;
    return n.data.kind === "site" ? n.id : (n.parentId ?? null);
  };
  const sourcesBySite = new Map<string, Map<string, number>>();
  const addSource = (site: string | null, key: string, x: number) => {
    if (!site) return;
    let s = sourcesBySite.get(site);
    if (!s) sourcesBySite.set(site, (s = new Map()));
    if (!s.has(key)) s.set(key, x);
  };
  for (const e of edges) {
    const d = e.data;
    if (!d) continue;
    if (d.kind === "leg" || d.kind === "stub") addSource(siteOfTarget(e.target), e.source, sourceX(e.source));
    else if (d.kind === "fan") addSource(e.target, fanEntryKey(d.router, d.site), sourceX(e.source));
    else if (d.kind === "bus") addSource(siteOfTarget(e.target), fanEntryKey(d.router, d.site), sourceX(ID_ROUTER(d.router)));
  }
  const columnOf = new Map<string, number>(); // `${site}|${key}` -> index from the right
  for (const [site, sources] of sourcesBySite) {
    const ordered = [...sources.entries()].sort((a, b) => a[1] - b[1] || (a[0] < b[0] ? -1 : 1));
    ordered.forEach(([key], i) => columnOf.set(`${site}|${key}`, ordered.length - 1 - i));
  }
  /** The drop x in `target`'s port region for `key`: the target's right edge less the badge, then the column. */
  const portX = (target: string, key: string): number | null => {
    const b = box.get(target);
    const n = byId.get(target);
    if (!b || !n) return null;
    const site = n.data.kind === "site" ? n.id : (n.parentId ?? target);
    const idx = columnOf.get(`${site}|${key}`) ?? 0;
    const right = n.data.kind === "site" ? b.x + b.w - SITE_PAD - BUS_BADGE_W : b.x + b.w - BUS_BADGE_W;
    return portColumnX(right, 0, idx);
  };
  /** Where a drop lands on `target`: a bus's line, an overflow lane's top edge, a site's top edge. */
  const landingY = (target: string): number | null => {
    const b = box.get(target);
    const n = byId.get(target);
    if (!b || !n) return null;
    return n.data.kind === "segment" ? laneLineY(b) : b.y;
  };

  const requests: LegRequest[] = [];
  const fallback = new Map<string, Point[]>();
  for (const e of edges) {
    const d = e.data;
    if (!d) continue;
    if (d.kind === "leg") {
      const from = bottom(e.source);
      const x = portX(e.target, e.source);
      const y = landingY(e.target);
      if (!from || x === null || y === null) continue;
      const to = { x, y };
      requests.push({ id: e.id, from, to });
      fallback.set(e.id, [from, to]);
    } else if (d.kind === "stub") {
      const from = top(e.source);
      const x = portX(e.target, e.source);
      const y = landingY(e.target);
      if (!from || x === null || y === null) continue;
      const to = { x, y };
      requests.push({ id: e.id, from, to, leave: "up", arrive: y < from.y ? "up" : "down" });
      fallback.set(e.id, [from, to]);
    } else if (d.kind === "fan") {
      const from = bottom(e.source);
      const x = portX(e.target, fanEntryKey(d.router, d.site));
      const y = landingY(e.target);
      if (!from || x === null || y === null) continue;
      const to = { x, y };
      requests.push({ id: e.id, from, to });
      fallback.set(e.id, [from, to]);
    } else if (d.kind === "bus") {
      const key = fanEntryKey(d.router, d.site);
      const siteBox = box.get(d.site);
      const ex = portX(d.site, key);
      const x = portX(e.target, key);
      const y = landingY(e.target);
      if (!siteBox || ex === null || x === null || y === null) continue;
      const from = { x: ex, y: siteBox.y };
      const to = { x, y };
      requests.push({ id: e.id, from, to });
      fallback.set(e.id, [from, to]);
    }
  }
  const routes = routeAll(requests, obstacles, options);
  for (const [id, pts] of fallback) if (!routes.has(id)) routes.set(id, pts);
  return { routes, obstacles, hostRows };
}

const ID_ROUTER = (name: string) => `router:${name}`;
