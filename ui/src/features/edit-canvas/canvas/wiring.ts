/**
 * The legs' geometry over the LIVE node boxes (rendering rule 6,
 * 2026-11-06): a leg leaves a router from the bottom of its icon, lands on
 * one of its segment's top-edge handles -- a tile has one handle per
 * attached router, spread across its top edge, so parallel legs never
 * overlap -- and runs orthogonally through the gutters between boxes
 * (`route.ts`), never through a tile's interior. A fan (rule 7) lands on
 * its site's top edge, at the router's x clamped into the site, and its bus
 * leaves that entry point for each segment's handle.
 *
 * Positions come from React Flow's node state, so a drag re-wires; sizes
 * are the measured ones when the DOM has them, else the placement's. A leg
 * the router cannot route (a port walled in) is a straight line. Pure --
 * no React, no DOM.
 */
import { DEVICE_H } from "../layout/geometry";
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

/** The side of a router's drawn icon box (`.cv-icon-box`), flush with the
 *  bottom-centre of its node. */
export const ROUTER_ICON = 34;
/** A fan's entry keeps this far inside its site's left and right edges. */
const ENTRY_INSET = 24;
/** Two fans entering one site keep this far apart. */
const ENTRY_GAP = 14;

export interface Wiring {
  /** Edge id -> the polyline to draw, `from` and `to` included. */
  routes: Map<string, Point[]>;
  /** The boxes the routing kept clear of, for the acceptance check. */
  obstacles: Rect[];
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

/** The box a router draws (its node, `DEVICE_W` × `DEVICE_H`), with headroom
 *  above the name so a leg never grazes it. */
export function routerBox(slot: Rect): Rect {
  return { x: slot.x, y: slot.y - ROUTER_BOX_PAD_Y, w: slot.w, h: (slot.h || DEVICE_H) + ROUTER_BOX_PAD_Y };
}

/** The box a leg is clipped against in the straight-line fallback: a router's icon, else the node. */
export function clipBox(n: Pick<WireNode, "type">, box: Rect): Rect {
  if (n.type === "router") return { x: box.x + box.w / 2 - ROUTER_ICON / 2, y: box.y + box.h - ROUTER_ICON, w: ROUTER_ICON, h: ROUTER_ICON };
  return box;
}

const inside = (inner: Rect, outer: Rect) => inner.x >= outer.x && inner.y >= outer.y && inner.x + inner.w <= outer.x + outer.w && inner.y + inner.h <= outer.y + outer.h;

/**
 * Wire every leg, fan and bus edge over the nodes as they stand. `edges` is
 * the list the canvas draws (after the fan collapse); edges of other kinds
 * are ignored.
 */
export function wire(nodes: readonly WireNode[], edges: readonly CanvasEdge[], options: Partial<RouteOptions> = {}): Wiring {
  const shown = nodes.filter((n) => !n.hidden);
  const byId = new Map(shown.map((n) => [n.id, n]));
  const box = boxes(shown);
  // Obstacles: the tiles, the chrome, each router's drawn box, and a chip
  // that was dragged clear of its tile. Site tints and annotations are not.
  const obstacles: Rect[] = [];
  for (const n of shown) {
    const b = box.get(n.id);
    if (!b) continue;
    const k = n.data.kind;
    if (k === "segment" || k === "overflow" || k === "unplaced" || k === "internet") obstacles.push(b);
    else if (k === "router") obstacles.push(routerBox(b));
    else if ((k === "host" || k === "rungs") && n.parentId) {
      const parent = box.get(n.parentId);
      if (parent && !inside(b, parent)) obstacles.push(b);
    }
  }

  // The source port of a leg: a router's icon bottom; a chip's bottom-centre.
  const port = (id: string): Point | null => {
    const n = byId.get(id);
    const b = box.get(id);
    if (!n || !b) return null;
    return { x: b.x + b.w / 2, y: b.y + b.h };
  };
  const sourceX = (id: string) => port(id)?.x ?? 0;

  // Handles: a tile's top edge carries one per attached source (by the
  // sources' x), so legs from two routers into one tile land apart.
  const attached = new Map<string, Set<string>>();
  const attach = (target: string, source: string) => {
    let s = attached.get(target);
    if (!s) attached.set(target, (s = new Set()));
    s.add(source);
  };
  const fanEntryKey = (router: string, site: string) => `${router}|${site}`;
  for (const e of edges) {
    const d = e.data;
    if (!d) continue;
    if (d.kind === "leg") attach(e.target, e.source);
    else if (d.kind === "bus") attach(e.target, fanEntryKey(d.router, d.site));
  }
  // A fan's entry on its site's top edge: the router's x clamped into the
  // site; several fans into one site are spread ENTRY_GAP apart.
  const entries = new Map<string, Point>();
  const perSite = new Map<string, Array<{ key: string; x: number }>>();
  for (const e of edges) {
    const d = e.data;
    if (!d || d.kind !== "fan") continue;
    const site = box.get(e.target);
    if (!site) continue;
    const x = Math.min(site.x + site.w - ENTRY_INSET, Math.max(site.x + ENTRY_INSET, sourceX(e.source)));
    const list = perSite.get(e.target) ?? [];
    list.push({ key: fanEntryKey(d.router, d.site), x });
    perSite.set(e.target, list);
  }
  for (const [siteId, list] of perSite) {
    const site = box.get(siteId)!;
    list.sort((a, b) => a.x - b.x);
    let last = Number.NEGATIVE_INFINITY;
    for (const it of list) {
      const x = Math.max(it.x, last + ENTRY_GAP);
      entries.set(it.key, { x, y: site.y });
      last = x;
    }
  }
  const handleX = (target: string, sourceKey: string): number | null => {
    const b = box.get(target);
    const set = attached.get(target);
    if (!b || !set) return null;
    const sources = [...set].sort((a, b2) => (entries.get(a)?.x ?? sourceX(a)) - (entries.get(b2)?.x ?? sourceX(b2)));
    const i = sources.indexOf(sourceKey);
    const k = sources.length;
    return b.x + (b.w * ((i < 0 ? 0 : i) + 1)) / (k + 1);
  };

  const requests: LegRequest[] = [];
  const fallback = new Map<string, Point[]>();
  for (const e of edges) {
    const d = e.data;
    if (!d) continue;
    if (d.kind === "leg") {
      const from = port(e.source);
      const tb = box.get(e.target);
      const hx = handleX(e.target, e.source);
      if (!from || !tb || hx === null) continue;
      const to = { x: hx, y: tb.y };
      const src = byId.get(e.source)!;
      if (src.data.kind === "host" || src.data.kind === "rungs") {
        // A chip inside its tile leaves through the tile's top edge at its own
        // x (its bottom is the tile's interior); the stub inside is drawn as is.
        const cb = box.get(e.source)!;
        const parent = src.parentId ? box.get(src.parentId) : null;
        if (parent && inside(cb, parent)) {
          const exit = { x: cb.x + cb.w / 2, y: parent.y };
          requests.push({ id: e.id, from: exit, to, leave: "up", prefix: [{ x: exit.x, y: cb.y }] });
          fallback.set(e.id, [{ x: exit.x, y: cb.y }, to]);
          continue;
        }
      }
      requests.push({ id: e.id, from, to });
      fallback.set(e.id, [from, to]);
    } else if (d.kind === "fan") {
      const from = port(e.source);
      const to = entries.get(fanEntryKey(d.router, d.site));
      if (!from || !to) continue;
      requests.push({ id: e.id, from, to });
      fallback.set(e.id, [from, to]);
    } else if (d.kind === "bus") {
      const from = entries.get(fanEntryKey(d.router, d.site));
      const tb = box.get(e.target);
      const hx = handleX(e.target, fanEntryKey(d.router, d.site));
      if (!from || !tb || hx === null) continue;
      const to = { x: hx, y: tb.y };
      requests.push({ id: e.id, from, to });
      fallback.set(e.id, [from, to]);
    }
  }
  const routes = routeAll(requests, obstacles, options);
  for (const [id, pts] of fallback) if (!routes.has(id)) routes.set(id, pts);
  return { routes, obstacles };
}
