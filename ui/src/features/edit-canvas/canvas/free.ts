/**
 * The FREE layout (B2, `SPEC.md` "Free layout"): for a design that is authored, not cast -- an
 * untitled design, a draft, a twin with no cast or discovery provenance -- every box stays exactly
 * where it was dropped or dragged (its pin), the first placement being the tree's own (dagre's), and
 * the edges are drawn POINT TO POINT between their two ends: no comb, no route through the
 * gutters, a router's legs leaving the router itself. Transits keep their pill. Pure: a pass over
 * the tree's placement.
 */
import { ID, type Placement } from "./placement";

/** A fan wide enough that nothing folds: a free design shows every segment it has. */
export const FREE_FAN = 10_000;

export function freeLayout(p: Placement): Placement {
  const edges = p.edges.map((e) => {
    const data = { ...(e.data ?? {}) } as Record<string, unknown>;
    delete data["route"];
    data["free"] = true;
    // A router's leg leaves the router, not a site tile under it.
    const leg = /^leg:([^>]+)>/.exec(e.id);
    const source = leg && e.data?.kind === "leg" ? ID.router(leg[1]!) : e.source;
    return { ...e, source, data } as typeof e;
  });
  return { ...p, edges };
}
