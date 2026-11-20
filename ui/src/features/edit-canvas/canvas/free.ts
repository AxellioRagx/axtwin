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
  const ids = new Set(p.nodes.map((n) => n.id));
  const edges = p.edges.map((e) => {
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
    return { ...e, source, data } as typeof e;
  });
  return { ...p, edges };
}
