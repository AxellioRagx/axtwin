/**
 * Fan-out collapse per site (rendering rule 2, track 4, 2026-11-06): a
 * router with legs into more than N buses of one site group draws ONE trunk
 * to the site's top edge, badged with the count, and a rail inside the site
 * from that entry down to each bus, all in the router's hue. A router's
 * legs behind a group's `+N more` lane (the placement's folded `legs:` edge)
 * go through the same fan, the rail landing on the overflow lane. Selecting
 * the router, or opening the site, expands that site's fan to individual
 * legs. A multi-homed host's stubs are not a router's and pass through. Pure.
 */
import { EDGE_Z, ID, LEG_CLASS, type CanvasEdge } from "./placement";

export const FAN_DEFAULT = 4;
/** The rail's choices for N; `Infinity` never collapses. */
export const FAN_CHOICES: readonly number[] = [2, 4, 8, Number.POSITIVE_INFINITY];

export function fanLabel(n: number): string {
  return Number.isFinite(n) ? String(n) : "all";
}

export const fanId = (router: string, site: string) => `fan:${router}>${site}`;
export const busId = (router: string, target: string) => `bus:${router}>${target}`;

/** The router and site a fan or bus edge belongs to, from its id. */
export function fanParts(id: string): { router: string; rest: string } | null {
  const m = /^(?:fan|bus):([^>]+)>(.+)$/.exec(id);
  return m ? { router: m[1]!, rest: m[2]! } : null;
}

/**
 * The edges to draw: every leg of a router into one site is kept when the
 * site holds `n` or fewer of them and none is folded, or when `open` names
 * the router or the site; otherwise they become one fan edge and its bus.
 * Edges that are not router legs pass through untouched.
 */
export function fanOut(edges: readonly CanvasEdge[], n: number, open: ReadonlySet<string>): CanvasEdge[] {
  type Group = { router: string; site: string; legs: CanvasEdge[]; folded: CanvasEdge | null };
  const groups = new Map<string, Group>();
  const rest: CanvasEdge[] = [];
  for (const e of edges) {
    const d = e.data;
    if (!d || d.kind !== "leg" || !d.site) {
      rest.push(e);
      continue;
    }
    const router = e.source.startsWith("router:") ? e.source.slice(7) : e.source;
    const key = `${e.source}|${d.site}`;
    let g = groups.get(key);
    if (!g) {
      g = { router, site: d.site, legs: [], folded: null };
      groups.set(key, g);
    }
    if (d.folded) g.folded = e;
    else g.legs.push(e);
  }
  const out: CanvasEdge[] = [];
  for (const g of groups.values()) {
    const foldedCount = g.folded?.data?.kind === "leg" ? (g.folded.data.folded ?? 0) : 0;
    const total = g.legs.length + foldedCount;
    // N = all draws every leg, the folded overflow leg included.
    const collapse = Number.isFinite(n) && (g.legs.length > n || g.folded !== null) && !open.has(g.router) && !open.has(g.site);
    if (!collapse) {
      out.push(...g.legs);
      if (g.folded) out.push(g.folded);
      continue;
    }
    const first = g.legs[0] ?? g.folded!;
    const source = first.source;
    const hue = first.data?.kind === "leg" ? first.data.hue : "";
    const label = `${total} segment${total === 1 ? "" : "s"}${foldedCount ? ` · ${foldedCount} behind +N more` : ""}`;
    out.push({ id: fanId(g.router, g.site), type: "leg", className: LEG_CLASS, source, target: ID.site(g.site), sourceHandle: "bottom", targetHandle: "top", zIndex: EDGE_Z, data: { kind: "fan", router: g.router, site: g.site, count: total, label, hue } });
    for (const leg of g.legs) {
      out.push({ id: busId(g.router, leg.target), type: "leg", className: LEG_CLASS, source: ID.site(g.site), target: leg.target, sourceHandle: "bottom", targetHandle: "top", zIndex: EDGE_Z, data: { kind: "bus", router: g.router, site: g.site, segment: leg.data?.kind === "leg" ? leg.data.label : leg.target, hue } });
    }
    if (g.folded) {
      out.push({ id: busId(g.router, g.folded.target), type: "leg", className: LEG_CLASS, source: ID.site(g.site), target: g.folded.target, sourceHandle: "bottom", targetHandle: "top", zIndex: EDGE_Z, data: { kind: "bus", router: g.router, site: g.site, segment: `${foldedCount} behind +N more`, hue } });
    }
  }
  return [...out, ...rest];
}
