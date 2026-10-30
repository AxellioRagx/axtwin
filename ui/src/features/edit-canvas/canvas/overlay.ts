/**
 * The traffic overlay (`SPEC.md`, Run-time): a filter over the OBSERVED edges
 * of the topology export -- `proto`, port, bytes, peer -- names the
 * attachments (host chips) and the segment arcs that carry a matching edge,
 * for the canvas to make glow for a timeout. Rendered from the export's own
 * evidence with no backend change; an authored twin has no observed edge and
 * therefore nothing to match.
 *
 * The export's edges carry no port: `port` matches an endpoint's own
 * `listen_ports` / `dial_ports` claims (`tcp/443`, `443`), so it says "an
 * edge touching a host that claims this port", not "an edge on this port".
 * Pure -- no React.
 */
import type { TopologyExport } from "../../../api/types";
import type { ConsoleGraph } from "../model/graph";
import { arcKey } from "./placement";

export interface TrafficFilter {
  proto: string;
  port: string;
  minBytes: string;
  peer: string;
}

export const EMPTY_FILTER: TrafficFilter = { proto: "", port: "", minBytes: "", peer: "" };

export function isEmptyFilter(f: TrafficFilter): boolean {
  return !f.proto.trim() && !f.port.trim() && !f.minBytes.trim() && !f.peer.trim();
}

export interface TrafficMatch {
  /** Export node ids (entity keys) at either end of a matching edge. */
  hostIds: Set<string>;
  /** Normalized segment-NAME pair keys of matching local<->local edges. */
  pairKeys: Set<string>;
  matched: number;
  total: number;
  /** A filter value that could not be read, with the reason. */
  problems: string[];
}

function portOf(claim: string): string {
  const tail = claim.split("/").pop() ?? claim;
  return tail.trim();
}

export function matchTraffic(exportData: TopologyExport, graph: ConsoleGraph, f: TrafficFilter): TrafficMatch {
  const problems: string[] = [];
  const proto = f.proto.trim().toLowerCase();
  const port = f.port.trim();
  const peer = f.peer.trim().toLowerCase();
  let minBytes: number | null = null;
  if (f.minBytes.trim()) {
    const n = Number(f.minBytes.trim());
    if (Number.isFinite(n) && n >= 0) minBytes = n;
    else problems.push(`bytes "${f.minBytes.trim()}" is not a number`);
  }
  const hostIds = new Set<string>();
  const pairKeys = new Set<string>();
  let matched = 0;
  const claimsPort = (id: string): boolean => {
    const n = graph.nodeById.get(id);
    if (!n) return false;
    return [...n.listenPorts, ...n.dialPorts].some((c) => portOf(c) === port);
  };
  const namesPeer = (id: string): boolean => {
    const n = graph.nodeById.get(id);
    return id.toLowerCase().includes(peer) || (n?.address ?? "").toLowerCase().includes(peer);
  };
  for (const e of exportData.edges) {
    if (proto && (e.proto ?? "").toLowerCase() !== proto) continue;
    if (minBytes !== null && e.bytes < minBytes) continue;
    if (port && !claimsPort(e.src) && !claimsPort(e.dst)) continue;
    if (peer && !namesPeer(e.src) && !namesPeer(e.dst)) continue;
    matched++;
    hostIds.add(e.src);
    hostIds.add(e.dst);
    const a = graph.nodeById.get(e.src)?.segment;
    const b = graph.nodeById.get(e.dst)?.segment;
    const na = a ? graph.segmentByCidr.get(a)?.name : undefined;
    const nb = b ? graph.segmentByCidr.get(b)?.name : undefined;
    if (na && nb && na !== nb) pairKeys.add(arcKey(na, nb));
  }
  return { hostIds, pairKeys, matched, total: exportData.edges.length, problems };
}
