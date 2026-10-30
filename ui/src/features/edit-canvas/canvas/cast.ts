/**
 * The CAST twin's structural input for the lifted graph model: the topology
 * export (`GET /api/twins/{id}/topology`) carries the observed graph; the
 * twin tree (`GET /api/twins/{id}/tree`) carries what the twin config adds --
 * the routers with their legs and the segment roster (deployed CIDR, zone).
 * Both are joined on the segment NAME, never a CIDR (`model/graph.ts`).
 *
 * Pure -- no React, no fetch.
 */
import type { TwinSummary, TwinTree } from "../../../api/types";
import type { CastInput } from "../model/graph";

export function castInput(twinId: string, tree: TwinTree | null, twin: TwinSummary | null): CastInput {
  // The deploy gate is a cast fact; when the summary does not publish it, no
  // host is called a ghost on its account (0 is "nothing is below the gate").
  const gate = twin && typeof twin["deploy_min_occupancy"] === "number" ? (twin["deploy_min_occupancy"] as number) : 0;
  return {
    imprintId: twinId,
    deployMinOccupancy: gate,
    routers: tree ? tree.routers.map((r) => ({ name: r.name, segments: [...r.segments] })) : undefined,
    segmentRoster: tree ? tree.segments.map((s) => ({ name: s.name, cidr: s.cidr, zone_type: s.zone_type ?? null })) : null,
  };
}
