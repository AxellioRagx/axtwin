/**
 * Structure versus evidence (rendering rule 2, 2026-11-06). Attachments --
 * a router's legs, a host's legs -- are the twin's structure and are always
 * drawn. Observed flows (the host↔host arcs with their log(bytes) width)
 * are evidence and are OFF by default: an arc is shown only while the
 * traffic overlay lights it, or while it touches the selection -- every arc
 * on a selected segment, and for a selected host the arcs its own observed
 * edges contribute to (its segment to each peer's). Pure -- no React.
 */
import type { CastRoster, TopologyExport } from "../../../api/types";
import type { ConsoleGraph } from "../model/graph";
import { arcKey, type CanvasNode } from "./placement";

export interface ArcScope {
  /** Arc anchors (segment names or `+N more` tile ids) a selected segment touches. */
  anchors: ReadonlySet<string>;
  /** Arc keys a selected host's own observed edges fall into. */
  keys: ReadonlySet<string>;
}

export const NO_SCOPE: ArcScope = { anchors: new Set(), keys: new Set() };

/** The scope the selected nodes open. `anchorByName` is the placement's. */
export function arcScope(selected: ReadonlyArray<Pick<CanvasNode, "data">>, graph: ConsoleGraph, exportData: TopologyExport | null, anchorByName: Record<string, string>): ArcScope {
  const anchors = new Set<string>();
  const keys = new Set<string>();
  const anchorOf = (name: string) => anchorByName[name] ?? name;
  const segmentNameOf = (nodeId: string): string | null => {
    const n = graph.nodeById.get(nodeId);
    const cidr = n?.segment ?? null;
    return cidr ? (graph.segmentByCidr.get(cidr)?.name ?? null) : null;
  };
  for (const n of selected) {
    const d = n.data;
    if (d.kind === "segment") anchors.add(anchorOf(d.name));
    else if (d.kind === "host" && exportData) {
      const mine = segmentNameOf(d.nodeId);
      if (!mine) continue;
      for (const e of exportData.edges) {
        const peer = e.src === d.nodeId ? e.dst : e.dst === d.nodeId ? e.src : null;
        if (!peer) continue;
        const theirs = segmentNameOf(peer);
        if (theirs && theirs !== mine) keys.add(arcKey(anchorOf(mine), anchorOf(theirs)));
      }
    }
  }
  return anchors.size || keys.size ? { anchors, keys } : NO_SCOPE;
}

/** Whether the arc with `key` (`a|b` of its two anchors) is drawn. */
export function arcShown(key: string, scope: ArcScope, lit: ReadonlySet<string>): boolean {
  if (lit.has(key) || scope.keys.has(key)) return true;
  if (!scope.anchors.size) return false;
  return key.split("|").some((a) => scope.anchors.has(a));
}

/**
 * Rule 3 (track 4): a switch-class host's OBSERVED adjacency is evidence, never
 * a structural leg. For a design node (a cast group name) the cast roster
 * says which observed entities are its members and which observed network
 * each segment name stands for; the export's edges say which peers those
 * members talked to. The segments those peers sit in, less the ones the
 * node's `nets[]` already name, are what the inspector offers to attach.
 * Sorted; empty when the twin was not cast or nothing was observed. Pure.
 */
export function observedSegments(exportData: TopologyExport | null, roster: CastRoster | null, node: string, attached: readonly string[]): string[] {
  if (!exportData || !roster) return [];
  const members = new Set(roster.node_roster.find((n) => n.name === node)?.members ?? []);
  if (!members.size) return [];
  const nameByObservedCidr = new Map<string, string>();
  for (const s of roster.segment_roster) if (s.observed_cidr) nameByObservedCidr.set(s.observed_cidr, s.name);
  const cidrOfEntity = new Map(exportData.nodes.map((n) => [n.id, n.segment]));
  const out = new Set<string>();
  for (const e of exportData.edges) {
    const peer = members.has(e.src) ? e.dst : members.has(e.dst) ? e.src : null;
    if (!peer || members.has(peer)) continue;
    const cidr = cidrOfEntity.get(peer);
    const name = cidr ? nameByObservedCidr.get(cidr) : undefined;
    if (name && !attached.includes(name)) out.add(name);
  }
  return [...out].sort();
}
