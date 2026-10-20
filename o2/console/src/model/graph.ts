/**
 * The join layer: one export + one cast become **one graph**, keyed by
 * segment NAME (`docs/WORK_ORDER_console_canvas.md` §2, §4).
 *
 * This is the seam between the two coordinate systems the bundle carries. The
 * topology export addresses everything by the **observed** CIDR -- the
 * imprint's identity, and what an assertion key is in
 * (`10.1.10.0/24`). The cast (the twin config) carries the **deployed**,
 * relocated CIDR (`10.200.0.0/24`) and the structural extras the export has
 * no room for: the conduits and the gateway's legs. The two join on the
 * segment **name** (`seg-10-1-10-0`), never on a CIDR, because the CIDRs are a
 * *declared transformation* of each other, not a match (`14` §2). Tiles and
 * routes carry the observed CIDR (D6 (a)); the deployed one is an inspector
 * fact.
 *
 * Pure -- no React, no fetch. `views/` build a `CastInput` from the API and
 * hand it here; the golden and real-bundle tests build one from the raw
 * files. The layout modules take the `ConsoleGraph` this returns and never
 * touch the raw DTOs, so the join rule lives in exactly one place.
 *
 * **Every evidence class is the export's own `*_evidence_class`** -- a node's
 * placement class is `segment_evidence_class`, its role's class is
 * `role_evidence_class`, an edge's is `evidence_class`. Nothing here invents
 * one; a `null` field stays `null` and renders as "no evidence for this",
 * distinct from any of the six (`15` §6, acceptance).
 */
import type {
  TopologyExport,
  TopologyExportEdge,
  TopologyExportNode,
} from "../api/types";
import { ghostReasons, precedenceOf, type GhostReason } from "./evidence";
import { compareCidr } from "./keys";
import { anyDimmingFilter, type Filters } from "./zoom";
import {
  compareRole,
  INFRA_DEVICE_ROLES,
  orderRoles,
  roleBucket,
  ROLE_BADGE_MAX,
} from "./roles";

// ---------------------------------------------------------------------------
// Cast structural input -- what the twin config adds on top of the export
// ---------------------------------------------------------------------------
/** A conduit is a directed `{from,to}` in the twin config; on the map it is an
 *  undirected lane. Both ends are segment NAMES. */
export interface Conduit {
  from: string;
  to: string;
}

/** One router of the twin config and the segment NAMES it has a leg on. */
export interface RouterLegs {
  name: string;
  segments: readonly string[];
}

/** One `segments[]` entry of the twin config, as the cast summary publishes
 *  it (Console S5.1.a). `name` joins it to an observed segment; `cidr` is the
 *  DEPLOYED network, relocated on a `--relocate` cast; `zone_type` is `null`
 *  where the config declares none. */
export interface RosterSegment {
  name: string;
  cidr: string;
  zone_type: string | null;
}

/**
 * Where an observed segment stands against the cast by NAME alone (Console
 * S5.1.a) -- the pure join; `edit/allocate.ts` `castStandingOf` adds the one
 * thing only the edit store knows, a rename on record:
 *
 *  - `carried` -- the cast's roster names it;
 *  - `absent` -- the roster is published and does not name it: NOT in the cast
 *    under this name, which is "renamed or pruned" -- the compiler applies an
 *    add's name to a live segment, so a segment it carries under another name
 *    reads exactly like one it dropped;
 *  - `unpublished` -- the server did not publish a roster, so neither can be
 *    told, and saying either would be a guess.
 */
export type CastStatus = "carried" | "absent" | "unpublished";

/**
 * The cast's structural contribution to the graph. The two identity/config
 * fields (`imprintId`, `deployMinOccupancy`) are required; every STRUCTURAL
 * field below them is optional. The LIVE app builds this from `CastSummary`
 * (`views/useGraph.ts` `castInputFrom`), which since R5 (GAP-079) publishes
 * the conduit roster and the gateway's legs rather than only their counts,
 * and since Console S5.1.a the SEGMENT roster -- the deployed CIDR and zone
 * type per segment, and which observed segments the cast pruned. (Until then
 * the inspector's deployed-CIDR and zone facts read "not declared" on every
 * real bundle: only a test fixture ever supplied them.)
 */
export interface CastInput {
  /**
   * The CANONICAL imprint id -- the id the API is keyed on (the directory name
   * = `twin_config.id` = the id in the page URL). It is the single source of
   * truth for `ConsoleGraph.imprintId`, and therefore for every drill-down
   * route, re-fetch and search hop the views build off the graph. It comes
   * from the CAST/registration side, NOT from the topology export's baked
   * `imprint_id`/`pit_id`, which addresses the observed point-in-time and can
   * legitimately disagree with the canonical id (a relocated/renamed twin) --
   * navigating to that baked value 404s against an API keyed on the canonical
   * one. The baked field stays available as a display/provenance fact; it must
   * never drive a route.
   */
  imprintId: string;
  /** The EFFECTIVE deploy-gate threshold (`CastSummary.deploy_min_occupancy`).
   *  A host whose occupancy is below it is a ghost (`13` §3.4). */
  deployMinOccupancy: number;
  /** Directed conduits from the twin config. `[]` ⇒ the config declares none;
   *  ABSENT ⇒ the cast did not publish a roster (a server older than R5). No
   *  east-west band either way -- `ConsoleGraph.castOmits` says which. */
  conduits?: readonly Conduit[];
  /** Every `kind: router` node and the segment names it has a leg on; the
   *  same `[]`-versus-absent rule. */
  routers?: readonly RouterLegs[];
  /** The cast's SEGMENT roster, as published. ABSENT or `null` ⇒ not
   *  published (a server predating the field); `[]` ⇒ the config declares no
   *  segment. When present it is the ONE source of each segment's deployed
   *  CIDR and zone type -- the two maps below are then ignored. */
  segmentRoster?: readonly RosterSegment[] | null;
  /** Segment name → deployed (relocated) CIDR, for the inspector's
   *  declared-transformation fact. A DIRECT input for the golden fixture,
   *  which predates the roster and pins these values in every layout golden;
   *  superseded by `segmentRoster` whenever one is supplied, so the live app
   *  has one source, not two. */
  deployedCidrByName?: Readonly<Record<string, string>>;
  /** Segment name → zone type (`simple`/`vlan`); the same rule as above. */
  zoneTypeByName?: Readonly<Record<string, string>>;
}

// ---------------------------------------------------------------------------
// The joined graph
// ---------------------------------------------------------------------------
export interface GraphNode {
  /** The entity key (`address:…`/`hostname:…`/`mac:…`) -- the export node id
   *  and the assertions file's addressing are the same string. */
  id: string;
  address: string | null;
  locality: string | null;
  /** The OBSERVED CIDR the export placed it in, or `null` when unplaced. */
  segment: string | null;
  role: string | null;
  roleConfidence: number | null;
  /** The export's own field -- never inferred. */
  roleEvidenceClass: string | null;
  /** The export's own field -- never inferred. */
  segmentEvidenceClass: string | null;
  windowsSeen: number | null;
  /** `windows_seen / windows`, `null` when either is absent -- absent is not
   *  zero occupancy (`13` §3.4 degrades to the export's counts, seam 1). */
  occupancy: number | null;
  /** Every ghost reason that applies (S3 computes only the gate reason;
   *  suppression is S5). Empty ⇒ a live element. */
  ghostReasons: GhostReason[];
  isGhost: boolean;
  listenPorts: string[];
  dialPorts: string[];
}

export interface EvidenceMixEntry {
  /** An evidence class the export used, or the sentinel `"(none)"` for a node
   *  whose relevant `*_evidence_class` is `null`. Never a fabricated class. */
  evidenceClass: string;
  count: number;
}

/** A volume in ONE window set (`docs/WORK_ORDER_console_level0_rendering.md`
 *  §8: never a rate -- the export counts windows, not seconds, so every label
 *  built from one says "in this window"). */
export interface Volume {
  bytes: number;
  flows: number;
}

/** One protocol's share of a pair's bytes. The export's own `proto` string,
 *  never normalized; an edge with none falls into the `"(none)"` sentinel the
 *  evidence mix already uses. */
export interface ProtoShare {
  proto: string;
  bytes: number;
}

/**
 * OBSERVED traffic between two segments (R4) -- the east-west band's units.
 *
 * The pair is UNDIRECTED and keyed by segment NAME (`a < b`), the same
 * normalized key `conduits` uses, so an arc, an asserted conduit and an
 * inspector entry all agree on what a pair is. `protoMix` is descending by
 * bytes (ties by protocol name), so `protoMix[0]` is the dominant protocol
 * without a second sort at the call site.
 */
export interface TrafficPair {
  a: string;
  b: string;
  bytes: number;
  flows: number;
  protoMix: ProtoShare[];
}

/**
 * The twin's external edge (R2). Present whenever there is something to draw
 * it from; `null` means the export observed NO traffic crossing the edge and
 * the cast declares no router -- in which case level 0 draws no WAN box and no
 * trunk, and says so, rather than inventing an edge.
 *
 * **`inferred` is the honest distinction**: with no router published, the edge
 * is a statement that local↔non-local traffic exists and nothing more -- it is
 * NOT the discovery of a NAT address (§8), and the view labels it *egress
 * (inferred from observed traffic)*, never "gateway". Once the cast publishes
 * a router (R5) the twin DECLARES a gateway and the label says so.
 *
 * It keys on whether a ROUTER was published, not on whether one has a leg: a
 * cast declaring `gw` with no segment still declares a gateway, and calling
 * that "inferred" would credit the observation for something the twin states.
 * (The leg COUNT is a separate question, and the label already drops it rather
 * than claiming "0 legs" -- the R5 rule.)
 */
export interface WanEdge {
  inferred: boolean;
  /**
   * The highest-precedence `evidence_class` among the crossing EDGES, or
   * `null` when none of them carries one. It is the edges' own field, never a
   * class invented for the box: a cast-declared gateway with no observed
   * crossing traffic reports `null` rather than claiming `compiler`.
   */
  evidenceClass: string | null;
  /** Total crossing volume, INCLUDING edges whose local end is unplaced --
   *  which is why this can exceed the sum of the per-site legs. */
  bytes: number;
  flows: number;
  /** How many export edges crossed the edge. `0` with `inferred: false` is the
   *  declared-but-silent gateway. */
  crossingEdges: number;
}

export interface GraphSegment {
  /** The join key. */
  name: string;
  /** The tile's identity and label (`15` §4, D6). */
  observedCidr: string;
  /** The deployed, relocated CIDR -- an inspector fact only. */
  deployedCidr: string | null;
  zoneType: string | null;
  /** The export segment's own entity count (may exceed placed hosts: it
   *  counts every entity the export tied to this segment). */
  entities: number;
  coverageSource: string | null;
  /** Placed hosts, sorted by id. */
  hosts: GraphNode[];
  hostCount: number;
  ghostCount: number;
  /** Hosts at or above the gate threshold -- the ones a deploy would stand up. */
  deployedCount: number;
  /** Mean occupancy of placed hosts that HAVE one; `null` when none do. */
  occupancy: number | null;
  /** Placed-host counts by role bucket, in the fixed role order. */
  roleHistogram: Array<{ role: string; count: number }>;
  /**
   * The tile's ROLE BADGES (R3): up to `ROLE_BADGE_MAX` buckets of
   * `roleHistogram` — **an infrastructure role first, then the busiest** — ties
   * broken by the fixed role order so the set is deterministic. See
   * `roleBadgesOf` for why the reservation exists: without it the two domain
   * controllers on `10.80.20.0/24` lose their badge to nine unfingerprinted
   * endpoints, on both real bundles.
   *
   * It replaces the unlabeled 8 px histogram bar the tile used to draw, which
   * encoded the same data in a channel nobody could read: a stack of coloured
   * slivers says *some mixture*, where `DC 2` says which roles and how many.
   * The FULL histogram stays above -- the badges are a projection for a
   * 156-unit tile, never the record (the outline tree and the inspector show
   * every bucket), so a segment with four roles is not a segment with three.
   */
  roleBadges: Array<{ role: string; count: number }>;
  /** Placed-host counts by `segment_evidence_class` (the placement evidence),
   *  in precedence order, `"(none)"` last -- the tile's evidence-mix ring. */
  evidenceMix: EvidenceMixEntry[];
  /** True when a router has a leg on this segment. */
  hasRouterLeg: boolean;
  /**
   * NORTH-SOUTH volume (R2): every edge with exactly one end in this segment
   * and the other end NON-LOCAL, summed. It is what sets the width of this
   * segment's group's trunk leg. Zero is a real answer -- a segment whose
   * hosts never talked off the twin in this window -- and is drawn as a
   * hairline, not as a missing connector.
   *
   * East-west traffic (both ends local) is excluded: that is R4's band.
   */
  northSouth: Volume;
  /**
   * EAST-WEST volume (R6): this segment's share of `trafficPairs` — every
   * observed pair it is an end of, summed. It is the second half of the byte
   * figure a level-0 tile carries, and it is **derived from the pairs the band
   * draws**, never recounted from the edges, so a tile and an arc can never
   * disagree about how much traffic a segment carried (LES-025).
   *
   * Zero is a real answer (a segment that talked only off-twin, or not at all),
   * and — like `northSouth` — it is a volume in ONE window set, never a rate.
   */
  eastWest: Volume;
}

export interface ConsoleGraph {
  imprintId: string;
  windows: number | null;
  /** The export's `vantage` -- where the capture was taken. Carried through
   *  VERBATIM for the tap badge (R2) and never parsed: the two real bundles
   *  spell it differently (`axellio-hq/corp-apcn-rdr1:12` on the pit export,
   *  `https://10.80.65.7:0-11` on the twin's), so any structure read into it
   *  would be read wrong on one of them. */
  vantage: string | null;
  deployMinOccupancy: number;
  /** Every export segment, as a tile, sorted by observed CIDR. */
  segments: GraphSegment[];
  segmentByName: Map<string, GraphSegment>;
  segmentByCidr: Map<string, GraphSegment>;
  /** Resolved undirected conduits (both ends are real segments), deduped and
   *  ordered -- the ASSERTED half of the east-west band (R4). */
  conduits: Array<{ a: string; b: string }>;
  /** OBSERVED segment-pair traffic (R4), bytes desc -- the east-west band's
   *  other half, aggregated from the export's own edges. */
  trafficPairs: TrafficPair[];
  /** Conduit endpoints the graph could not resolve to a tile (e.g. the cast's
   *  `internet-sink`), reported rather than dropped silently. */
  unresolvedConduitEndpoints: string[];
  /**
   * The cast's conduit roster AS PUBLISHED -- each `{from, to}` by segment name,
   * unresolved ends included -- or `null` when the server did not publish one
   * (`castOmits` says so for the map). `conduits` above is the resolved, deduped
   * drawing; this is what a CLONE mirrors from (S5.1.b), which must be able to
   * say which of a segment's conduits it could not re-key and why, rather than
   * see only the ones that happened to resolve.
   */
  castConduits: readonly Conduit[] | null;
  /** Every segment ANY router has a leg on, sorted. It marks the tiles that
   *  carry a router leg and feeds the WAN label's leg count; since R2 it no
   *  longer decides whether the WAN box is DRAWN -- `wanEdge` does. */
  routerSegmentNames: string[];
  /** Each router with its legs (each once), in the cast's order. The WAN
   *  label says "the gateway" only when there is exactly one. */
  routers: RouterLegs[];
  /** The twin's external edge (R2), or `null` when nothing observed or
   *  declared supports drawing one. */
  wanEdge: WanEdge | null;
  /** The structural cast fields the input did NOT carry -- absent or null,
   *  as opposed to `[]`. The view says they were not published instead of
   *  presenting a missing band or WAN box as a statement about the twin. */
  castOmits: Array<"conduits" | "routers">;
  /** The cast's SEGMENT roster (Console S5.1.a), or `null` when the server did
   *  not publish one -- kept apart from `castOmits` because the map draws
   *  nothing from it: it decides what the site `+` may add
   *  (`edit/allocate.ts`) and what the inspector can say. `castStatusOf` reads
   *  it for one segment. */
  castSegments: readonly RosterSegment[] | null;
  /** The six-ish infrastructure devices (`15` §4): LOCAL nodes whose role is
   *  a `INFRA_DEVICE_ROLES` member. Sorted by (role order, id). */
  infraDevices: GraphNode[];
  /** Local entities the export placed in no segment (O7). */
  unplacedLocal: GraphNode[];
  /** Non-local entities -- the Internet tier's population (O7). */
  nonLocal: GraphNode[];
  nodes: GraphNode[];
  nodeById: Map<string, GraphNode>;
  edges: TopologyExportEdge[];
  census: TopologyExport["census"];
}

// ---------------------------------------------------------------------------
// build
// ---------------------------------------------------------------------------
// ---------------------------------------------------------------------------
// filters (`15` §7: `?ghosts=` / `?class=` / `?occupancy_lt=`)
// ---------------------------------------------------------------------------
export function anyFilterActive(filters: Filters): boolean {
  // The taxonomy lives in `model/zoom.ts` (S2): this is the DIMMING set, and a
  // view flag such as `?summary=` must never turn the dimming pass on.
  return anyDimmingFilter(filters);
}

/**
 * Whether a node MATCHES the active filters -- the emphasized set (`15` §7).
 * Nothing is hidden by a filter (counts stay honest); a non-matching node is
 * dimmed by the view. The class filter matches EITHER the placement or the
 * role evidence class, since both are the export's own `*_evidence_class`.
 */
export function nodePassesFilters(node: GraphNode, filters: Filters): boolean {
  if (filters.ghosts && !node.isGhost) return false;
  if (
    filters.evidenceClass !== null &&
    node.roleEvidenceClass !== filters.evidenceClass &&
    node.segmentEvidenceClass !== filters.evidenceClass
  ) {
    return false;
  }
  if (
    filters.occupancyLt !== null &&
    !(node.occupancy !== null && node.occupancy < filters.occupancyLt)
  ) {
    return false;
  }
  return true;
}

function occupancyOf(windowsSeen: number | null, windows: number | null): number | null {
  if (windowsSeen === null || windows === null || windows <= 0) return null;
  return windowsSeen / windows;
}

function toNode(raw: TopologyExportNode, windows: number | null, threshold: number): GraphNode {
  const occupancy = occupancyOf(raw.windows_seen ?? null, windows);
  const reasons = ghostReasons({ occupancy, threshold });
  return {
    id: raw.id,
    address: raw.address,
    locality: raw.locality,
    segment: raw.segment,
    role: raw.role,
    roleConfidence: raw.role_confidence,
    roleEvidenceClass: raw.role_evidence_class,
    segmentEvidenceClass: raw.segment_evidence_class,
    windowsSeen: raw.windows_seen,
    occupancy,
    ghostReasons: reasons,
    isGhost: reasons.length > 0,
    listenPorts: [...raw.listen_ports],
    dialPorts: [...raw.dial_ports],
  };
}

function byId(a: { id: string }, b: { id: string }): number {
  return a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
}

const NONE = "(none)";

/** A placed host's evidence for the tile ring is its `segment_evidence_class`
 *  -- the reason it sits in this segment. `null` becomes the sentinel, never a
 *  guessed class. */
function evidenceMixOf(hosts: readonly GraphNode[]): EvidenceMixEntry[] {
  const counts = new Map<string, number>();
  for (const h of hosts) {
    const cls = h.segmentEvidenceClass ?? NONE;
    counts.set(cls, (counts.get(cls) ?? 0) + 1);
  }
  // Ordered by evidence PRECEDENCE (`14` §2, via `precedenceOf`): the first
  // band of the ring and the "dominant" outline dot must show the highest-
  // precedence class present, not the alphabetically-first one -- `imputed`
  // sorts before `observed` alphabetically but ranks BELOW it, so an
  // alphabetical sort names the wrong dominant class for any multi-class mix.
  // Unknown producer classes sort after the six but before the `(none)`
  // sentinel; ties break by name for stability.
  return [...counts.entries()]
    .map(([evidenceClass, count]) => ({ evidenceClass, count }))
    .sort((x, y) => {
      const rx = mixRank(x.evidenceClass);
      const ry = mixRank(y.evidenceClass);
      if (rx !== ry) return rx - ry;
      return x.evidenceClass < y.evidenceClass ? -1 : x.evidenceClass > y.evidenceClass ? 1 : 0;
    });
}

/** Sort key for the evidence-mix ring: a known class by its precedence index
 *  (0 = highest), an unknown producer class after all six, the `(none)`
 *  sentinel last. */
function mixRank(evidenceClass: string): number {
  if (evidenceClass === NONE) return 1_000_000;
  const p = precedenceOf(evidenceClass);
  return p === null ? 1_000 : p;
}

function roleHistogramOf(hosts: readonly GraphNode[]): Array<{ role: string; count: number }> {
  const counts = new Map<string, number>();
  for (const h of hosts) {
    const bucket = roleBucket(h.role, h.locality);
    counts.set(bucket, (counts.get(bucket) ?? 0) + 1);
  }
  return orderRoles(counts.keys()).map((role) => ({
    role,
    count: counts.get(role) ?? 0,
  }));
}

/**
 * The tile's badge set (R3): **an infrastructure role first, then the busiest
 * buckets**, up to `ROLE_BADGE_MAX`. Derived from the histogram rather than
 * recounted, so the two can never disagree about what a segment holds
 * (LES-025). The tie-break inside each group is the fixed role order, NOT the
 * alphabet — two roles with the same count must resolve the same way on every
 * machine, and `roleRank` is the order the map, the bands and the matrix
 * already agree on.
 *
 * **Why not simply "highest count first"** (which is what the work order's
 * slice text says, and what this function did first): measured against the two
 * real bundles, a pure count ranking HIDES the domain controllers on
 * `10.80.20.0/24` — the pit export ranks it `generic-endpoint 9,
 * corp-workstation 7, unroled-endpoint 5, domain-controller 2` and the twin
 * export hides `dns-resolver` as well. That fails the same work order's own
 * acceptance ("the 10.80.20.0/24 tile carries a `DC` badge with count 2"), and
 * it fails it for the worst possible reason: the buckets that win on count are
 * `generic-endpoint` and `unroled-endpoint`, which are *absences of a
 * fingerprint* rather than roles, and they say the same thing on nearly every
 * segment of a corporate twin. A tile's three badges are its answer to "what is
 * this segment FOR"; two domain controllers are that answer and nine
 * unfingerprinted endpoints are not.
 *
 * The reserved set is `INFRA_DEVICE_ROLES` — deliberately the SAME set that
 * earns a box in the infrastructure band, not a second vocabulary: a role
 * important enough to draw as a device is important enough to name on the tile
 * that holds it. A segment with no such role is unaffected and ranks purely by
 * count. (Authority: implementation (Claude Code); the departure from the
 * slice's literal wording is recorded because the two clauses of that slice
 * cannot both hold on the data that ships.)
 */
function roleBadgesOf(
  histogram: ReadonlyArray<{ role: string; count: number }>,
): Array<{ role: string; count: number }> {
  const byCount = (
    a: { role: string; count: number },
    b: { role: string; count: number },
  ): number => (b.count !== a.count ? b.count - a.count : compareRole(a.role, b.role));
  const entries = histogram.map((entry) => ({ ...entry }));
  return [
    ...entries.filter((e) => INFRA_DEVICE_ROLES.has(e.role)).sort(byCount),
    ...entries.filter((e) => !INFRA_DEVICE_ROLES.has(e.role)).sort(byCount),
  ].slice(0, ROLE_BADGE_MAX);
}

interface NorthSouthTotals {
  /** Observed CIDR → the segment's crossing volume. A segment with none is
   *  absent from the map and reads as `{0, 0}`. */
  bySegment: Map<string, Volume>;
  /** Every crossing edge, including those whose local end is unplaced. */
  total: Volume;
  crossingEdges: number;
  /** The highest-precedence class among the crossing edges, `null` when none
   *  of them declares one. */
  evidenceClass: string | null;
}

/**
 * NORTH-SOUTH aggregation (R2): for every edge with exactly one LOCAL end,
 * add its volume to that end's segment.
 *
 * Three deliberate rules, each of which is a claim the map then makes:
 *
 *  - **both ends must be KNOWN nodes.** An edge naming an id the export does
 *    not carry cannot be said to cross the twin's edge in either direction, so
 *    it is counted nowhere rather than guessed as external.
 *  - **exactly one end local.** local↔local is east-west (R4's band);
 *    external↔external is not this twin's traffic at all. **Anything that is
 *    not the string `local` counts as NON-local**, including a `null` or an
 *    unrecognized locality: the export's own vocabulary is `local`/`external`
 *    on both real bundles, so this is latent today, but the effect if a third
 *    value ever appears is that its edges are treated as crossing the twin's
 *    edge rather than being dropped.
 *  - **an UNPLACED local end counts toward the total and toward no segment.**
 *    It crossed the edge, so the trunk's own volume includes it; it has no
 *    tile, so no leg can carry it.
 *
 * **A documented asymmetry with R4's east-west aggregation** (review nit): this
 * function tests `local.segment` for TRUTHINESS, while `trafficPairsOf` requires
 * the segment to resolve to one the map draws a tile for. So a local end placed
 * in a CIDR absent from `segments[]` reaches the trunk's TOTAL here and is
 * excluded from an arc there. Both bundles have zero such nodes, so this is a
 * difference in strictness with no observable consequence today -- recorded
 * rather than unified, because the two axes legitimately want different rules:
 * the trunk's total is "what crossed the edge" (drawable or not), an arc needs
 * two anchors to exist at all.
 *
 * The total therefore differs from the sum of the legs for **two** reasons --
 * a local end with no segment at all, and a local end whose `segment` names a
 * CIDR that is not in the export's `segments[]` (so it gets no tile either;
 * zero occurrences on both real bundles). `Trunk.bytes` publishes the total
 * and the view names the remainder, rather than quietly dropping it.
 */
function northSouthOf(
  byId: ReadonlyMap<string, GraphNode>,
  edges: readonly TopologyExportEdge[],
): NorthSouthTotals {
  const bySegment = new Map<string, Volume>();
  const total: Volume = { bytes: 0, flows: 0 };
  const classes = new Set<string>();
  let crossingEdges = 0;

  for (const edge of edges) {
    const src = byId.get(edge.src);
    const dst = byId.get(edge.dst);
    if (!src || !dst) continue;
    const srcLocal = src.locality === "local";
    if (srcLocal === (dst.locality === "local")) continue;
    crossingEdges += 1;
    total.bytes += edge.bytes;
    total.flows += edge.flows;
    if (edge.evidence_class) classes.add(edge.evidence_class);
    const local = srcLocal ? src : dst;
    if (!local.segment) continue;
    const current = bySegment.get(local.segment) ?? { bytes: 0, flows: 0 };
    bySegment.set(local.segment, {
      bytes: current.bytes + edge.bytes,
      flows: current.flows + edge.flows,
    });
  }

  // By PRECEDENCE, not alphabetically (LES-262): `imputed` sorts before
  // `observed` by name and ranks below it, so a name sort would report the
  // wrong dominant class for any multi-class edge set.
  const evidenceClass =
    [...classes].sort((a, b) => {
      const rank = mixRank(a) - mixRank(b);
      return rank !== 0 ? rank : a < b ? -1 : a > b ? 1 : 0;
    })[0] ?? null;
  return { bySegment, total, crossingEdges, evidenceClass };
}

/**
 * EAST-WEST aggregation (R4): every edge with BOTH ends local and placed in a
 * segment this map draws, summed onto the unordered pair of their segments.
 *
 * Four exclusions, each a claim the band then does not make:
 *
 *  - **an unknown node id** -- the same rule north-south uses: an edge naming
 *    an id the export does not carry is counted nowhere rather than guessed.
 *  - **a NON-LOCAL end** -- that is north-south (R2's trunk), and counting it
 *    here would draw the Internet as a segment pair.
 *  - **an UNPLACED end, or one placed in a segment with no tile** -- there is
 *    no anchor to draw from. It is excluded rather than hidden: the matrix
 *    still carries the edge, and `hiddenPairs` is deliberately NOT where this
 *    lands, because "budgeted out of the map" and "nowhere to draw it" are
 *    different facts.
 *  - **both ends in the SAME segment** -- intra-segment traffic is level 1's,
 *    and an arc from a group's edge back to itself is not a picture.
 *
 * Both directions of a pair merge into one entry (an arc is undirected), and
 * `protoMix` keeps the per-protocol split for the hover text and the inspector
 * -- `15` §5 keeps colour for evidence, so protocol never reaches the geometry.
 */
function trafficPairsOf(
  byId: ReadonlyMap<string, GraphNode>,
  edges: readonly TopologyExportEdge[],
  nameByCidr: ReadonlyMap<string, string>,
): TrafficPair[] {
  interface Acc {
    a: string;
    b: string;
    bytes: number;
    flows: number;
    proto: Map<string, number>;
  }
  const acc = new Map<string, Acc>();
  for (const edge of edges) {
    const src = byId.get(edge.src);
    const dst = byId.get(edge.dst);
    if (!src || !dst) continue;
    if (src.locality !== "local" || dst.locality !== "local") continue;
    if (!src.segment || !dst.segment) continue;
    const na = nameByCidr.get(src.segment);
    const nb = nameByCidr.get(dst.segment);
    if (!na || !nb || na === nb) continue;
    const [a, b] = na < nb ? [na, nb] : [nb, na];
    const key = `${a} ${b}`;
    const entry = acc.get(key) ?? { a, b, bytes: 0, flows: 0, proto: new Map<string, number>() };
    entry.bytes += edge.bytes;
    entry.flows += edge.flows;
    const proto = edge.proto || NONE;
    entry.proto.set(proto, (entry.proto.get(proto) ?? 0) + edge.bytes);
    acc.set(key, entry);
  }
  return [...acc.values()]
    .map((entry) => ({
      a: entry.a,
      b: entry.b,
      bytes: entry.bytes,
      flows: entry.flows,
      protoMix: [...entry.proto.entries()]
        .map(([proto, bytes]) => ({ proto, bytes }))
        .sort((x, y) => (y.bytes !== x.bytes ? y.bytes - x.bytes : cmp(x.proto, y.proto))),
    }))
    // Bytes desc, then the pair key -- the top-K budget slices this list, so
    // its order IS which arcs the map draws (a tie must not depend on the
    // export's edge order).
    .sort((x, y) => (y.bytes !== x.bytes ? y.bytes - x.bytes : cmp(x.a, y.a) || cmp(x.b, y.b)));
}

function cmp(a: string, b: string): number {
  return a < b ? -1 : a > b ? 1 : 0;
}

/**
 * Each segment's share of the east-west pairs (R6) — a segment is credited with
 * every pair it is an end of, so the totals across segments sum to **twice** the
 * band's bytes. That is the honest reading of "how much east-west traffic did
 * this segment carry": both ends carried all of it.
 */
function eastWestOf(pairs: readonly TrafficPair[]): Map<string, Volume> {
  const out = new Map<string, Volume>();
  const add = (name: string, pair: TrafficPair): void => {
    const current = out.get(name) ?? { bytes: 0, flows: 0 };
    out.set(name, { bytes: current.bytes + pair.bytes, flows: current.flows + pair.flows });
  };
  for (const pair of pairs) {
    add(pair.a, pair);
    add(pair.b, pair);
  }
  return out;
}

function meanOccupancy(hosts: readonly GraphNode[]): number | null {
  const values = hosts.map((h) => h.occupancy).filter((o): o is number => o !== null);
  if (values.length === 0) return null;
  return values.reduce((sum, o) => sum + o, 0) / values.length;
}

/**
 * Where one observed segment stands against the cast -- by NAME, the join key,
 * never by CIDR (a relocated cast's roster carries deployed networks). See
 * `CastStatus`.
 */
export function castStatusOf(graph: Pick<ConsoleGraph, "castSegments">, segmentName: string): CastStatus {
  if (graph.castSegments === null) return "unpublished";
  return graph.castSegments.some((seg) => seg.name === segmentName) ? "carried" : "absent";
}

export function buildGraph(exportData: TopologyExport, cast: CastInput): ConsoleGraph {
  const windows = exportData.windows;
  const threshold = cast.deployMinOccupancy;

  const nodes = exportData.nodes.map((raw) => toNode(raw, windows, threshold));
  const nodeById = new Map(nodes.map((n) => [n.id, n]));

  // Group placed hosts by observed CIDR.
  const placedByCidr = new Map<string, GraphNode[]>();
  const unplacedLocal: GraphNode[] = [];
  const nonLocal: GraphNode[] = [];
  for (const n of nodes) {
    if (n.locality !== "local") {
      nonLocal.push(n);
      continue;
    }
    if (n.segment) {
      const list = placedByCidr.get(n.segment) ?? [];
      list.push(n);
      placedByCidr.set(n.segment, list);
    } else {
      unplacedLocal.push(n);
    }
  }

  // Each router's legs, deduped in declaration order; their union marks the
  // tiles that carry a router leg. (Whether the WAN box is drawn is `wanEdge`'s
  // question since R2, not this set's.)
  const routers: RouterLegs[] = (cast.routers ?? []).map((r) => ({
    name: r.name,
    segments: [...new Set(r.segments ?? [])],
  }));
  const routerNames = new Set(routers.flatMap((r) => r.segments));
  // `== null`: a wire `null` is "not published" exactly like a missing key.
  const castOmits: Array<"conduits" | "routers"> = [];
  if (cast.conduits == null) castOmits.push("conduits");
  if (cast.routers == null) castOmits.push("routers");
  // The roster, when supplied, is the ONE source of the two inspector facts;
  // the direct maps are the golden fixture's (see `CastInput`).
  const roster = cast.segmentRoster ?? null;
  const deployedByName: Readonly<Record<string, string>> = roster
    ? Object.fromEntries(roster.map((seg) => [seg.name, seg.cidr]))
    : (cast.deployedCidrByName ?? {});
  const zoneByName: Readonly<Record<string, string>> = roster
    ? Object.fromEntries(
        roster.flatMap((seg) => (seg.zone_type == null ? [] : [[seg.name, seg.zone_type]])),
      )
    : (cast.zoneTypeByName ?? {});

  // R2: north-south volume per segment, and the twin's external edge. The edge
  // is INFERRED from observed crossing traffic when the cast publishes no
  // router leg -- a statement that non-local traffic exists, never the
  // discovery of a NAT address (§8) -- and DECLARED once it does.
  // `nodeById` is built above; rebuilding the same index here would be a
  // second copy of one lookup (S6).
  const northSouth = northSouthOf(nodeById, exportData.edges);
  const wanEdge: WanEdge | null =
    northSouth.crossingEdges > 0 || routers.length > 0
      ? {
          inferred: routers.length === 0,
          evidenceClass: northSouth.evidenceClass,
          bytes: northSouth.total.bytes,
          flows: northSouth.total.flows,
          crossingEdges: northSouth.crossingEdges,
        }
      : null;

  // Built in two steps because the two halves of a tile's byte figure come
  // from different places: `northSouth` is per segment, while `eastWest` is a
  // share of the PAIRS, and `trafficPairsOf` needs the segment set to resolve a
  // CIDR to a name. So the segments are assembled without it, the pairs are
  // aggregated, and each segment's share is attached from those same pairs
  // rather than recounted from the edges (LES-025).
  const placed: Array<Omit<GraphSegment, "eastWest">> = exportData.segments
    .map((seg) => {
      const hosts = (placedByCidr.get(seg.cidr) ?? []).slice().sort(byId);
      const ghostCount = hosts.filter((h) => h.isGhost).length;
      const roleHistogram = roleHistogramOf(hosts);
      return {
        name: seg.name,
        observedCidr: seg.cidr,
        deployedCidr: deployedByName[seg.name] ?? null,
        zoneType: zoneByName[seg.name] ?? null,
        entities: seg.entities,
        coverageSource: seg.coverage_source,
        hosts,
        hostCount: hosts.length,
        ghostCount,
        deployedCount: hosts.length - ghostCount,
        occupancy: meanOccupancy(hosts),
        roleHistogram,
        roleBadges: roleBadgesOf(roleHistogram),
        evidenceMix: evidenceMixOf(hosts),
        hasRouterLeg: routerNames.has(seg.name),
        northSouth: northSouth.bySegment.get(seg.cidr) ?? { bytes: 0, flows: 0 },
      };
    })
    .sort((a, b) => compareCidr(a.observedCidr, b.observedCidr));

  // R4: the observed half of the east-west band. Keyed on the segments the map
  // knows about, so a pair it could not name never reaches it.
  const trafficPairs = trafficPairsOf(
    nodeById,
    exportData.edges,
    new Map(placed.map((s) => [s.observedCidr, s.name])),
  );
  const eastWestByName = eastWestOf(trafficPairs);
  const segments: GraphSegment[] = placed.map((seg) => ({
    ...seg,
    eastWest: eastWestByName.get(seg.name) ?? { bytes: 0, flows: 0 },
  }));

  const segmentByName = new Map(segments.map((s) => [s.name, s]));
  const segmentByCidr = new Map(segments.map((s) => [s.observedCidr, s]));

  // Resolve conduits to undirected, deduped tile pairs; anything whose end is
  // not a real segment (the cast's `internet-sink`) is reported, not dropped.
  const conduitSet = new Map<string, { a: string; b: string }>();
  const unresolved = new Set<string>();
  for (const c of cast.conduits ?? []) {
    const hasFrom = segmentByName.has(c.from);
    const hasTo = segmentByName.has(c.to);
    if (!hasFrom) unresolved.add(c.from);
    if (!hasTo) unresolved.add(c.to);
    if (!hasFrom || !hasTo || c.from === c.to) continue;
    const [a, b] = c.from < c.to ? [c.from, c.to] : [c.to, c.from];
    conduitSet.set(`${a}\x00${b}`, { a, b });
  }
  const conduits = [...conduitSet.values()].sort((x, y) =>
    x.a === y.a ? (x.b < y.b ? -1 : x.b > y.b ? 1 : 0) : x.a < y.a ? -1 : 1,
  );

  const infraDevices = nodes
    .filter((n) => n.locality === "local" && n.role !== null && INFRA_DEVICE_ROLES.has(n.role))
    .sort((a, b) => {
      // (role order, id) -- these are all local, roled devices.
      const byRole = compareRole(a.role ?? "", b.role ?? "");
      return byRole !== 0 ? byRole : byId(a, b);
    });

  return {
    // The CANONICAL id (the cast/URL id the API is keyed on), NEVER the
    // export's baked `imprint_id`/`pit_id` -- the latter can disagree with the
    // canonical id (a relocated/renamed twin), and every drill-down route,
    // re-fetch and search hop is built off `graph.imprintId`, so a baked value
    // here 404s the whole canvas navigation. See `CastInput.imprintId`.
    imprintId: cast.imprintId,
    windows,
    vantage: exportData.vantage,
    deployMinOccupancy: threshold,
    segments,
    segmentByName,
    segmentByCidr,
    conduits,
    trafficPairs,
    unresolvedConduitEndpoints: [...unresolved].sort(),
    castConduits: cast.conduits ?? null,
    routerSegmentNames: [...routerNames].sort(),
    routers,
    wanEdge,
    castOmits,
    castSegments: roster,
    infraDevices,
    unplacedLocal: unplacedLocal.slice().sort(byId),
    nonLocal: nonLocal.slice().sort(byId),
    nodes,
    nodeById,
    edges: exportData.edges,
    census: exportData.census,
  };
}
