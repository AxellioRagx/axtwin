/**
 * What a switch's host row holds (2026-11-07, replacing the lifted `layout/level1.ts`, whose
 * geometry the tree never drew): a segment's hosts in role bands -- live hosts by
 * role bucket in the display order, ghosts last in the `below-threshold` band --
 * each band either its hosts (by address) or, past the per-band chip budget, one
 * tally per occupancy rung. Pure, no geometry.
 */
import { bandCollapses, tallyByRung, type RungTally } from "../model/budgets";
import type { ConsoleGraph, GraphNode } from "../model/graph";
import { orderRoles, roleBucket } from "../model/roles";

export const BELOW_THRESHOLD = "below-threshold";

export interface Band {
  /** A role bucket, or `below-threshold`. */
  role: string;
  hosts: GraphNode[];
  collapsed: boolean;
  /** Present when collapsed: a count per occupancy rung. */
  rungs: RungTally[];
}

function byAddress(a: GraphNode, b: GraphNode): number {
  const ka = a.address ?? a.id;
  const kb = b.address ?? b.id;
  return ka < kb ? -1 : ka > kb ? 1 : a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
}

function band(role: string, hosts: GraphNode[]): Band {
  const sorted = hosts.slice().sort(byAddress);
  const collapsed = bandCollapses(sorted.length);
  return { role, hosts: sorted, collapsed, rungs: collapsed ? tallyByRung(sorted.map((h) => h.occupancy)) : [] };
}

/** The role bands of the segment at `observedCidr`, or `null` when there is none. */
export function segmentBands(graph: ConsoleGraph, observedCidr: string): Band[] | null {
  const segment = graph.segmentByCidr.get(observedCidr);
  if (!segment) return null;
  const byRole = new Map<string, GraphNode[]>();
  const ghosts: GraphNode[] = [];
  for (const h of segment.hosts) {
    if (h.isGhost) {
      ghosts.push(h);
      continue;
    }
    const bucket = roleBucket(h.role, h.locality);
    byRole.set(bucket, [...(byRole.get(bucket) ?? []), h]);
  }
  const out = orderRoles(byRole.keys()).map((role) => band(role, byRole.get(role) ?? []));
  if (ghosts.length) out.push(band(BELOW_THRESHOLD, ghosts));
  return out;
}
