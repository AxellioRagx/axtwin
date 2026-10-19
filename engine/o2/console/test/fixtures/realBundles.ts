/**
 * The two shipped bundles, loaded the way the LIVE app builds its graph
 * (LES-311): `buildGraph(topology, castInputFrom(castSummary, id))`, with the
 * cast summary carrying BOTH drift-tested rosters -- the segments
 * (`fixtures/real-segment-rosters.json`) and the conduits
 * (`fixtures/real-conduit-rosters.json`), each pinned to the real twin configs
 * by `tests/test_api_console.py`.
 *
 * **Both rosters are VENDORED JSON, never parsed here** (review N5). The
 * conduit roster used to be read with a narrow hand parser over the twin
 * config's `conduits:` block -- a second reader of a YAML file this package has
 * no YAML reader for, which would have been the thing that drifted when the
 * block's shape changed. It follows `real-segment-rosters.json`'s pattern
 * instead: a checked-in copy, and one Python test that reads the real file with
 * a real parser and fails on any difference.
 *
 * Node environment only (filesystem, LES-260).
 */
import { existsSync, lstatSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import type { CastConduit, CastSegment, CastSummary, TopologyExport } from "../../src/api/types";
import { buildGraph, type ConsoleGraph } from "../../src/model/graph";
import { castInputFrom } from "../../src/views/useGraph";

const CAST_ROOT = new URL("../../../../twins/", import.meta.url);

export interface RealBundle {
  id: string;
  topology: string;
  twinConfig: string;
}

export const REAL_BUNDLES: readonly RealBundle[] = [
  {
    id: "corpapcn-twin",
    topology: fileURLToPath(new URL("corpapcn-twin/generated/corpapcn-twin.topology.json", CAST_ROOT)),
    twinConfig: fileURLToPath(new URL("corpapcn-twin/te.yaml", CAST_ROOT)),
  },
  {
    id: "pit-395a61f04e-1780405200",
    topology: fileURLToPath(new URL("pit-395a61f04e-1780405200/generated/pit-395a61f04e-1780405200.topology.json", CAST_ROOT)),
    twinConfig: fileURLToPath(new URL("pit-395a61f04e-1780405200/te.yaml", CAST_ROOT)),
  },
];

/** Present, or absent -- never silently skipped through a DANGLING symlink (the
 *  pit bundle's files are links into `corpapcn-disco/`). */
export function bundleAvailable(bundle: RealBundle): boolean {
  try {
    lstatSync(bundle.topology);
  } catch {
    return false;
  }
  if (!existsSync(bundle.topology)) {
    throw new Error(`${bundle.topology} is a DANGLING symlink -- restore its target rather than skip green`);
  }
  return true;
}

const REAL_ROSTERS = JSON.parse(
  readFileSync(fileURLToPath(new URL("./real-segment-rosters.json", import.meta.url)), "utf8"),
) as Record<string, CastSegment[]>;

const REAL_CONDUITS = JSON.parse(
  readFileSync(fileURLToPath(new URL("./real-conduit-rosters.json", import.meta.url)), "utf8"),
) as Record<string, CastConduit[]>;

/** A bundle's checked-in conduit roster -- the twin config's own `conduits:`,
 *  pinned to it by the Python drift test (see the module note). THROWS rather
 *  than returning an empty roster: a test running on "this twin declares no
 *  conduit" proves nothing about a clone that mirrors them (LES-311). */
export function conduitRosterFor(bundle: RealBundle): CastConduit[] {
  const roster = REAL_CONDUITS[bundle.id];
  if (!roster || roster.length === 0) {
    throw new Error(`no drift-tested conduit roster for ${bundle.id}`);
  }
  return roster.map((c) => ({ ...c }));
}

/** The real bundle's graph through the live seam, with its real segment roster
 *  AND its real conduit roster -- the shape the live cast summary publishes, so
 *  no test here runs on a "the twin declares no conduit" graph no operator can
 *  open (LES-311). Only `routers` is published empty: nothing a clone reads. */
export function realGraph(bundle: RealBundle): ConsoleGraph {
  const exportData = JSON.parse(readFileSync(bundle.topology, "utf8")) as TopologyExport;
  const roster = REAL_ROSTERS[bundle.id];
  if (!roster) throw new Error(`no drift-tested segment roster for ${bundle.id}`);
  const conduits = conduitRosterFor(bundle);
  const cast = {
    deploy_min_occupancy: 0.8,
    conduit_roster: conduits,
    routers: [],
    segment_roster: roster,
  } as unknown as CastSummary;
  return buildGraph(exportData, castInputFrom(cast, bundle.id));
}
