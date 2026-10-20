/**
 * The DECLARED design of a bundle with no topology export (Unified Console
 * design/16 rule 1: every area renders something useful for every twin,
 * including hand-authored ones with no aximprint export).
 *
 * `declaredExport` turns `GET .../cast`'s rosters -- segments (with their
 * deployed CIDRs), node specs (role, count, legs, fanned-out ids), routers and
 * conduits -- into the export SHAPE the canvas already draws, so the same
 * `buildGraph` -> `layout0/1` -> levels path renders it (one renderer). It is
 * NOT a capture, and it says so in the data:
 *
 *   - every fact carries the single class `declared` -- never one of the six
 *     evidence classes, never `observed` (the views render it neutral and
 *     name it "declared (from the twin config)");
 *   - there are no edges: nothing was observed talking to anything; the
 *     east-west band draws only the DECLARED conduits;
 *   - occupancy is not measured. Every declared host sits exactly AT full
 *     occupancy so the deploy gate keeps it -- which is what a deploy of the
 *     config does (no observation, no gate) -- and the counts read "would
 *     deploy".
 *
 * Hosts are keyed `hostname:<node id>` (the expander's own ids), since the
 * addresses are allocated at plan time and are not in the config. Routers are
 * drawn by the WAN tier from `cast.routers`, so they are not placed as hosts.
 *
 * Pure.
 */
import type { CastNode, CastSummary, TopologyExport, TopologyExportNode } from "../api/types";

/** The one class every declared fact carries. Not one of the six (`14` §2):
 *  it renders neutral, and its NAME says what it is. */
export const DECLARED_CLASS = "declared";

/** Whether a bundle is HAND-AUTHORED: no capture evidence at all -- no
 *  topology export and no provenance sidecar. */
export function isHandAuthored(cast: Pick<CastSummary, "has_topology_export" | "has_provenance">): boolean {
  return !cast.has_topology_export && !cast.has_provenance;
}

/** The file an operator edits to change a hand-authored twin. A `twins/<id>/`
 *  folder keeps it as `te.yaml`; a legacy bundle as `<id>.yaml`. */
export function configFileOf(cast: Pick<CastSummary, "bundle_dir" | "id">): string {
  const dir = cast.bundle_dir.replace(/\/+$/, "");
  return /(^|\/)twins\/[^/]+$/.test(dir) ? `${dir}/te.yaml` : `${dir}/${cast.id}.yaml`;
}

/**
 * The declared design as an export, or `null` when the server publishes no
 * segment roster to draw it from (a server too old to send one).
 */
export function declaredExport(cast: CastSummary): TopologyExport | null {
  const segments = cast.segment_roster;
  if (!segments) return null;
  const cidrByName = new Map(segments.map((s) => [s.name, s.cidr]));
  const roster: readonly CastNode[] = cast.node_roster ?? [];

  const nodes: TopologyExportNode[] = [];
  const perSegment = new Map<string, number>();
  for (const spec of roster) {
    if ((spec.kind ?? "host").toLowerCase() === "router") continue;
    const home = spec.segments.find((s) => cidrByName.has(s)) ?? null;
    const cidr = home === null ? null : cidrByName.get(home)!;
    for (const nodeId of spec.node_ids) {
      nodes.push({
        id: `hostname:${nodeId}`,
        address: null,
        locality: "local",
        segment: cidr,
        segment_evidence_class: DECLARED_CLASS,
        role: spec.role,
        role_confidence: null,
        role_evidence_class: spec.role === null ? null : DECLARED_CLASS,
        windows_seen: 1,
        listen_ports: [],
        dial_ports: [],
      });
      if (cidr !== null) perSegment.set(cidr, (perSegment.get(cidr) ?? 0) + 1);
    }
  }

  const placed = nodes.filter((n) => n.segment !== null).length;
  return {
    imprint_id: cast.id,
    pit_id: null,
    as_of: null,
    network: null,
    vantage: null,
    windows: 1,
    schema_version: null,
    segments: segments.map((s) => ({
      cidr: s.cidr,
      name: s.name,
      entities: perSegment.get(s.cidr) ?? 0,
      coverage_source: DECLARED_CLASS,
    })),
    nodes,
    edges: [],
    census: {
      entities_total: nodes.length,
      with_role: nodes.filter((n) => n.role !== null).length,
      placed,
      local: nodes.length,
    },
  };
}
