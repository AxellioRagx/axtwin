/**
 * Structure versus evidence (rendering rule 2, 2026-11-06). Attachments --
 * a router's legs, a host's legs -- are the twin's structure and are always
 * drawn. Observed flows (the host↔host arcs with their log(bytes) width)
 * are evidence and are OFF by default: an arc is shown only while the
 * traffic overlay lights it, or while it touches the selection -- every arc
 * on a selected segment, and for a selected host the arcs its own observed
 * edges contribute to (its segment to each peer's). Pure -- no React.
 */
import type { TopologyExport } from "../../../api/types";
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
