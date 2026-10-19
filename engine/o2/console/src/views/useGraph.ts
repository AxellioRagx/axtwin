/**
 * `useGraph` -- the view-layer composition of `GET .../topology` and
 * `GET .../cast` into one `ConsoleGraph` (`model/graph.ts`). This is the only
 * place `api/` and `model/` meet: `views/` compose the two, per
 * `o2/console/README.md`'s boundary note.
 *
 * **The live graph carries the cast's conduits and routers** (R5, closes
 * GAP-079): `CastSummary` publishes the `conduit_roster` behind its
 * `conduits` count and every router with its legs (`routers`, per router
 * since the GAP-082 review), and `castInputFrom` hands both to `buildGraph`
 * -- so the east-west band and the WAN box draw from the same fetched summary
 * the summary strip reads, with nothing in between. (Until R5 the roster was
 * a hard-coded empty constant here, behind an in-UI note, because the API
 * published only the count.)
 */
import { useEffect, useMemo, useRef } from "react";

import { useCast, useTopology } from "../api/hooks";
import type { ConsoleApiError } from "../api/client";
import type { CastSummary, TopologyExport } from "../api/types";
import { buildGraph, type CastInput, type ConsoleGraph } from "../model/graph";
import { declaredExport } from "../model/declared";

/**
 * The cast summary's structural contribution, in the shape `buildGraph`
 * takes. Pure, and exported so a test can prove the wire fields reach the
 * graph without rendering anything.
 *
 * `imprintId` is the CANONICAL id threaded from the caller (the page URL),
 * never anything the summary or the export bakes (LES-263). The roster and
 * the routers pass through AS PUBLISHED: `buildGraph` resolves them, dedupes
 * them and reports an unresolvable end (`internet-sink`) itself. A field a
 * server too old to send it omits stays ABSENT here -- never defaulted to
 * `[]` -- so `graph.castOmits` can say "not published" rather than "the
 * twin declares none" (GAP-082 review, S4).
 */
export function castInputFrom(cast: CastSummary, imprintId: string): CastInput {
  return {
    imprintId,
    deployMinOccupancy: cast.deploy_min_occupancy,
    conduits: cast.conduit_roster,
    routers: cast.routers,
    // Console S5.1.a: the segment roster, passed through as published -- an
    // older server's ABSENT field stays absent, so the graph can say "not
    // published" rather than "the cast prunes every segment".
    segmentRoster: cast.segment_roster,
  };
}

/** The last graph built, with the very inputs it was built FROM. */
interface HeldGraph {
  imprintId: string;
  topology: TopologyExport;
  cast: CastSummary;
  graph: ConsoleGraph;
}

export interface GraphResource {
  /** The graph -- or, while the SAME imprint's cast is being re-read over the
   *  SAME topology it was built from (an apply's reload), that graph (see
   *  `useGraph`). */
  graph: ConsoleGraph | null;
  loading: boolean;
  /** A read that FAILED. A 404 on the topology read is not one: see
   *  `topologyAbsent`. */
  error: ConsoleApiError | null;
  /**
   * The bundle has no topology export -- the ordinary case `axrange/api/
   * service.py`'s `topology_export` documents ("404 when ... known id, but no
   * export yet ...; the caller is expected to treat a 404 as 'not applicable
   * yet'"), which is why it is kept OUT of `error`: presented as a failed read
   * it would blame a healthy bundle (LES-253). Only once the cast has been read
   * for THIS imprint (a 404 on both means the imprint was not found), and it
   * stays true while that cast is RE-read: an apply's reload must not turn a
   * bundle with no export into a failed read for its length (fix round, SF3).
   */
  topologyAbsent: boolean;
  /**
   * The graph is the bundle's DECLARED design (`model/declared.ts`), drawn
   * because there is no topology export: nothing in it is observed. `false`
   * for a capture-backed graph, or when there is no graph.
   */
  declared: boolean;
  /** True once both reads have returned, so a view can distinguish "still
   *  loading" from "loaded, and this is genuinely what there is". */
  ready: boolean;
  /** The cast summary re-exposed, so the workspace's summary strip and the
   *  graph share ONE `GET .../cast` rather than fetching it twice. */
  cast: CastSummary | null;
  castError: ConsoleApiError | null;
  castLoading: boolean;
  /** Re-read `GET .../cast` -- the S6 "reload the bundle" after an apply. Only
   *  the cast (twin config counts, gate, conduit roster, routers) is
   *  refetched, never the topology export, which is the vantage's IMMUTABLE
   *  capture and cannot change across a recompile; reloading it would only
   *  blink the map for no new data. */
  reloadCast: () => void;
}

export function useGraph(imprintId: string | null): GraphResource {
  const topology = useTopology(imprintId);
  const cast = useCast(imprintId);

  // Design/16 rule 1: a bundle with NO topology export still draws -- its
  // declared design, through the same buildGraph. Only once the export read
  // has answered 404 (an absent export is the ordinary case for a
  // hand-authored twin) and the cast is in hand.
  const exportAbsent = topology.error?.kind === "not-found";
  const declared = useMemo<ConsoleGraph | null>(() => {
    if (!exportAbsent || !cast.data || imprintId === null) return null;
    const doc = declaredExport(cast.data);
    return doc ? buildGraph(doc, castInputFrom(cast.data, imprintId)) : null;
  }, [exportAbsent, cast.data, imprintId]);
  // Held across a re-read of the SAME imprint's cast (an apply's reload), so
  // the declared map does not blink -- keyed on the imprint it was built for.
  const lastDeclared = useRef<{ imprintId: string; graph: ConsoleGraph } | null>(null);
  useEffect(() => {
    if (declared !== null && imprintId !== null) lastDeclared.current = { imprintId, graph: declared };
  }, [declared, imprintId]);

  const built = useMemo<ConsoleGraph | null>(() => {
    if (!topology.data || !cast.data || imprintId === null) return null;
    // The CANONICAL id (this hook's argument, from the page URL via
    // `imprintOf(zoom)`) is the single source of truth for `graph.imprintId`
    // and therefore every drill route -- NOT the topology export's baked
    // `imprint_id`, which can disagree with the id the API is keyed on.
    return buildGraph(topology.data, castInputFrom(cast.data, imprintId));
  }, [topology.data, cast.data, imprintId]);

  // S5.1.a (B2): a re-read of the cast drops its data for the length of the
  // request (`useResource` clears before it fetches), and without a hold the
  // graph went null with it -- unmounting every view keyed on it, including the
  // actions frame and whatever an operator had typed there, on every apply.
  //
  // The hold is keyed on the INPUTS the graph was built from, never on an id
  // label (fix round, MF1): it applies only while the topology on hand is the
  // very object the held graph was built from, and the one read in flight is
  // THIS imprint's cast. The first cut keyed it on the imprint id alone, and a
  // graph derived from imprint A's data under B's id was held -- and drawn, and
  // edited into B's file -- for the whole of B's load. (`useResource` now
  // withholds another subject's data outright, so no such graph can be built;
  // this is the second wall, not the only one.)
  const lastBuilt = useRef<HeldGraph | null>(null);
  useEffect(() => {
    if (built !== null && imprintId !== null && topology.data !== null && cast.data !== null) {
      lastBuilt.current = { imprintId, topology: topology.data, cast: cast.data, graph: built };
    }
  }, [built, imprintId, topology.data, cast.data]);
  const held = lastBuilt.current;
  const castReReading = cast.loading && cast.data === null && cast.error === null;
  const holding =
    built === null &&
    held !== null &&
    held.imprintId === imprintId &&
    topology.data !== null &&
    topology.data === held.topology &&
    topology.error === null &&
    castReReading;
  const heldDeclared =
    exportAbsent && castReReading && lastDeclared.current?.imprintId === imprintId ? lastDeclared.current.graph : null;
  const declaredGraph = declared ?? heldDeclared;
  const graph = built ?? (holding && held !== null ? held.graph : null) ?? declaredGraph;

  // SF3: "has the cast been read for this imprint?" must survive the re-read
  // an apply does, so the 404 on the export keeps meaning "not applicable".
  const castReadFor = useRef<string | null>(null);
  useEffect(() => {
    if (cast.data !== null && imprintId !== null) castReadFor.current = imprintId;
  }, [cast.data, imprintId]);
  const castKnown = cast.data !== null || (imprintId !== null && castReadFor.current === imprintId);
  const topologyAbsent = topology.error?.kind === "not-found" && cast.error === null && castKnown;

  return {
    graph,
    loading: topology.loading || cast.loading,
    error: topologyAbsent ? cast.error : (topology.error ?? cast.error),
    topologyAbsent,
    declared: built === null && graph !== null && graph === declaredGraph,
    ready: !!topology.data && !!cast.data,
    cast: cast.data,
    castError: cast.error,
    castLoading: cast.loading,
    reloadCast: cast.reload,
  };
}
