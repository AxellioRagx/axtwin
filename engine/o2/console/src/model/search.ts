/**
 * Search resolution (`docs/design/15-console-canvas.md` §7: "Search is the
 * primary navigation on a large twin: a name, an address, a role or a CIDR
 * jumps to the element at its level").
 *
 * Pure and testable -- in comes the graph and a raw query, out comes the zoom
 * to navigate to (or a reason it matched nothing). Added to §4's proposed
 * layout because it is a model concern (an identity → zoom function), not a
 * React one; `views/Search.tsx` is the input box over it.
 */
import type { ConsoleGraph } from "./graph";
import { parseCidr } from "./keys";
import { roleBucket } from "./roles";
import type { Zoom } from "./zoom";

export interface SearchResult {
  zoom: Zoom | null;
  /** What matched, or why nothing did -- shown beside the box. */
  note: string;
}

export function resolveSearch(graph: ConsoleGraph, raw: string): SearchResult {
  const q = raw.trim();
  if (!q) return { zoom: null, note: "type a name, address, role or CIDR" };
  const imprint = graph.imprintId;

  // 1. An exact entity key (`address:…` / `hostname:…` / `mac:…`).
  const exact = graph.nodeById.get(q);
  if (exact) {
    return { zoom: hostZoom(imprint, exact.id, exact.segment), note: `host ${exact.id}` };
  }

  // 2. A bare address.
  const byAddress = graph.nodes.find((n) => n.address === q);
  if (byAddress) {
    return { zoom: hostZoom(imprint, byAddress.id, byAddress.segment), note: `host ${byAddress.id}` };
  }

  // 3. A bare hostname (`hostname:<q>`).
  const asHostname = graph.nodeById.get(`hostname:${q}`);
  if (asHostname) {
    return { zoom: hostZoom(imprint, asHostname.id, asHostname.segment), note: `host ${asHostname.id}` };
  }

  // 4. A segment CIDR (observed).
  if (graph.segmentByCidr.has(q)) {
    return { zoom: { level: "segment", imprint, cidr: q }, note: `segment ${q}` };
  }
  // A CIDR-shaped query that names no segment.
  if (parseCidr(q)) {
    return { zoom: null, note: `no segment ${q} in this twin` };
  }

  // 5. A role -- roles live in the matrix (§7: "at its level").
  const roleMatch = graph.nodes.some((n) => roleBucket(n.role, n.locality) === q);
  if (roleMatch) {
    return { zoom: { level: "matrix", imprint }, note: `role ${q} -- see the matrix` };
  }

  return { zoom: null, note: `no name, address, role or CIDR matched "${q}"` };
}

function hostZoom(imprint: string, key: string, segment: string | null): Zoom {
  return segment
    ? { level: "host", imprint, key, cidr: segment }
    : { level: "host", imprint, key };
}
