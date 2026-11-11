/**
 * The AUTHORED twin (`GET /api/scenarios/{id}/topology`, a bipartite
 * host <-> network graph) rephrased in the topology export's vocabulary, so
 * the lifted graph model and the tiered placement engine draw it with the
 * same code as a cast twin. Nothing is invented: an authored graph carries no
 * observation, so every evidence class is `null`, `windows` is `null` and no
 * host is a ghost. Roles and listen claims come from the twin tree
 * (`GET /api/twins/{id}/tree`), joined on the instance id.
 *
 * Pure -- no React, no fetch.
 */
import type { Topology, TopologyEdge, TopologyExport, TopologyExportNode, TwinTree } from "../../../api/types";
import type { CastInput, RouterLegs } from "../model/graph";

/** A host's second and later attachments (rendering rule 4, track 4): a
 *  multi-homed host sits on the lowest of its buses with a stub to each of
 *  the others. A switch-class host is a host like any other -- a switch IN a
 *  segment is a member of it, not a gateway to it (the track-3 device rule
 *  is reverted). */
export interface ExtraLeg {
  host: string;
  /** The network id, which is the segment NAME in the export vocabulary. */
  segment: string;
  ip: string | null;
  /** The segment NAME the host sits on (its first leg): two segments may share a CIDR, so the CIDR does not name it. */
  home?: string;
}

/** The device tree's structure the bipartite graph cannot carry
 *  (`docs/design/topology-layers.md` §2): the switches, the links, and
 *  where every node and segment came from. */
export interface TreeDevice {
  name: string;
  kind: "switch" | "router";
  role: string | null;
  image: string | null;
  /** The segments the device's own legs name (a switch's L2 ports). */
  segments: string[];
}
export interface TreeLink {
  a: { device: string; port: string };
  b: { device: string; port: string };
  mode: string;
  native: string | null;
  vlans: string[];
  state: string;
  source: string | null;
  index: number;
}
export interface TreeStructure {
  devices: TreeDevice[];
  links: TreeLink[];
  /** `source` by node name and by segment name (absent == authored). */
  sources: Record<string, string>;
  confidences: Record<string, number>;
  /** The segment NAME each host sits on (its first leg): two segments may share a CIDR (§9 D3),
   *  so the graph's CIDR does not say which of the two switches a host hangs off. */
  homes?: Record<string, string>;
}

export const EMPTY_TREE: TreeStructure = { devices: [], links: [], sources: {}, confidences: {} };

export interface AuthoredGraph {
  exportData: TopologyExport;
  cast: CastInput;
  extraLegs: ExtraLeg[];
  /** The routers with a leg on an `egress: true` network: the config's
   *  uplinks, which are what draws an Internet node over an authored twin. */
  uplinks: string[];
  tree: TreeStructure;
}

export function treeStructure(topology: Topology): TreeStructure {
  const sources: Record<string, string> = {};
  const confidences: Record<string, number> = {};
  for (const n of topology.nodes) {
    if (n.source) sources[n.id] = n.source;
    if (typeof n.confidence === "number") confidences[n.id] = n.confidence;
  }
  const legs = new Map<string, string[]>();
  for (const e of topology.edges) legs.set(e.source, [...(legs.get(e.source) ?? []), e.target]);
  const devices: TreeDevice[] = topology.nodes
    .filter((n) => n.type === "switch")
    .map((n) => ({ name: n.id, kind: "switch" as const, role: n.role ?? null, image: n.image ?? null, segments: legs.get(n.id) ?? [] }));
  const links: TreeLink[] = (topology.links ?? []).map((l, index) => ({ a: { device: l.a, port: l.a_port }, b: { device: l.b, port: l.b_port }, mode: l.mode, native: l.native ?? null, vlans: l.vlans, state: l.state, source: l.source ?? null, index }));
  const networks = new Set(topology.nodes.filter((n) => n.type === "network").map((n) => n.id));
  const homes: Record<string, string> = {};
  for (const n of topology.nodes) {
    if (n.type !== "host") continue;
    const first = legs.get(n.id)?.find((t) => networks.has(t));
    if (first) homes[n.id] = first;
  }
  return { devices, links, sources, confidences, homes };
}

export function authoredGraph(topology: Topology, tree: TwinTree | null): AuthoredGraph {
  const networks = topology.nodes.filter((n) => n.type === "network");
  const netById = new Map(networks.map((n) => [n.id, n]));
  const cidrOf = (netId: string): string => netById.get(netId)?.cidr ?? netId;

  // The tree's node GROUPS fan out to instances; a group of one is its own instance.
  const roleByInstance = new Map<string, string | null>();
  const listenByInstance = new Map<string, string[]>();
  const zoneByName = new Map<string, string | null>();
  if (tree) {
    for (const seg of tree.segments) zoneByName.set(seg.name, seg.zone_type ?? null);
    const groups = [...tree.segments.flatMap((s) => s.nodes), ...tree.routers];
    for (const g of groups) {
      const listen = g.listen.map((l) => `${l.proto}/${l.port}`);
      for (const inst of g.instances.length ? g.instances : [g.name]) {
        roleByInstance.set(inst, g.role ?? null);
        listenByInstance.set(inst, listen);
      }
      if (!roleByInstance.has(g.name)) {
        roleByInstance.set(g.name, g.role ?? null);
        listenByInstance.set(g.name, listen);
      }
    }
  }

  const legsOf = new Map<string, TopologyEdge[]>();
  for (const e of topology.edges) {
    if (!netById.has(e.target)) continue;
    const list = legsOf.get(e.source) ?? [];
    list.push(e);
    legsOf.set(e.source, list);
  }

  const nodes: TopologyExportNode[] = [];
  const routers: RouterLegs[] = [];
  const extraLegs: ExtraLeg[] = [];
  const uplinks: string[] = [];
  const egressNets = new Set(networks.filter((n) => n.egress).map((n) => n.id));
  const entities = new Map<string, number>();
  for (const n of topology.nodes) {
    if (n.type === "network") continue;
    // A switch is a device of the tree (`treeStructure`), not a host on a pill.
    if (n.type === "switch") continue;
    const legs = legsOf.get(n.id) ?? [];
    if (n.type === "router") {
      routers.push({ name: n.id, segments: legs.map((e) => e.target) });
      if (legs.some((e) => egressNets.has(e.target))) uplinks.push(n.id);
      continue;
    }
    // A host's attachments are ever only its `net` / `nets[]` -- nothing is
    // inferred; the first is where it sits, the rest are its stubs (rule 4).
    const first = legs[0];
    const segment = first ? cidrOf(first.target) : null;
    if (first) entities.set(first.target, (entities.get(first.target) ?? 0) + 1);
    nodes.push({
      id: n.id,
      address: first?.ip ?? null,
      locality: "local",
      segment,
      segment_evidence_class: null,
      role: roleByInstance.get(n.id) ?? null,
      role_confidence: null,
      role_evidence_class: null,
      windows_seen: null,
      listen_ports: listenByInstance.get(n.id) ?? [],
      dial_ports: [],
    });
    for (const leg of legs.slice(1)) extraLegs.push({ host: n.id, segment: leg.target, ip: leg.ip ?? null, home: first!.target });
  }

  const exportData: TopologyExport = {
    imprint_id: topology.scenario_id,
    pit_id: null,
    as_of: null,
    network: null,
    vantage: null,
    windows: null,
    schema_version: null,
    segments: networks.map((n) => ({ cidr: cidrOf(n.id), name: n.id, entities: entities.get(n.id) ?? 0, coverage_source: null })),
    nodes,
    edges: [],
    census: {
      entities_total: nodes.length,
      with_role: nodes.filter((n) => n.role !== null).length,
      placed: nodes.filter((n) => n.segment !== null).length,
      local: nodes.length,
    },
  };
  const cast: CastInput = {
    imprintId: topology.scenario_id,
    // No deploy gate is published for an authored graph, and no host has an
    // occupancy to measure against one: nothing is a ghost.
    deployMinOccupancy: 0,
    routers,
    segmentRoster: networks.map((n) => ({ name: n.id, cidr: cidrOf(n.id), zone_type: zoneByName.get(n.id) ?? null })),
  };
  return { exportData, cast, extraLegs, uplinks, tree: treeStructure(topology) };
}
