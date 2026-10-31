/**
 * Hand-written mirrors of `axrange/api/dtos.py` -- **seam 1** of
 * `docs/WORK_ORDER_console_canvas.md` §9. There is no code generation and
 * deliberately none: the API is the contract, and a hand-written mirror is a
 * place to record what a field MEANS, which a generator would drop.
 *
 * Three rules the Python side enforces and this file preserves:
 *
 *  1. **Absent is not zero.** A producer field aximprint has not shipped is
 *     `null` and reported absent -- `code_fingerprint: null` says "this
 *     bundle predates the fingerprint", where `0` would say something false.
 *     Every `Optional[...]` there is `| null` here, never `?:`.
 *  2. **`null` and empty are different answers** on the diff: a side is
 *     `null` when the endpoint COULD NOT compare it and present-and-empty
 *     when it compared and nothing moved (LES-240).
 *  3. **An assertion record OMITS its absent fields** (the one place `?:` is
 *     right): `axrange/api/dtos.py`'s `AssertionRecordDTO._compact` drops
 *     nulls so what `GET` returns is re-`PUT`-able unchanged, with `value` on
 *     an override the single documented exception (LES-231).
 */

// ---------------------------------------------------------------------------
// GET /api/imprints  (the catalog listing; tolerant -- it publishes `skipped`)
// ---------------------------------------------------------------------------
export interface ImprintListItem {
  id: string;
  /** Whether the server would accept `POST …/apply` for this bundle, and why
   *  not. OPTIONAL: a server predating the Unified Console omits both, and the
   *  Console then says nothing before the click (it never guesses). */
  can_apply?: boolean;
  apply_blocked_reason?: string | null;
  hosts: number;
  segments: number;
  /** "" for a bundle whose capture instant is only in an absent export. */
  as_of: string;
  /** Non-null for the shipped Grafana screen; read it WITH `confidence_source`. */
  mean_confidence: number;
  has_provenance: boolean;
  /** Whether the bundle carries a topology export (OPTIONAL: newer servers). */
  has_topology_export?: boolean;
  /** "sidecar" | "absent" -- whether `mean_confidence` means anything. */
  confidence_source: string;
}

/** A catalog subdirectory the tolerant listing could not publish, and why. */
export interface ImprintSkipped {
  id: string;
  reason: string;
}

export interface ImprintList {
  imprints: ImprintListItem[];
  skipped: ImprintSkipped[];
}

// ---------------------------------------------------------------------------
// GET /api/imprints/{id}  and  .../provenance
// ---------------------------------------------------------------------------
export interface ImprintSegment {
  name: string;
  cidr: string;
  host_count: number;
  confidence: number | null;
}

export interface ImprintSummary {
  id: string;
  can_apply?: boolean;
  apply_blocked_reason?: string | null;
  as_of: string;
  store: string | null;
  days_observed: number | null;
  windows: number | null;
  hosts: number;
  segments: number;
  services: number;
  personas: number;
  mean_confidence: number;
  out_of_vocab_pct: number;
  topology_segments: ImprintSegment[];
  /** "canonical" | "normalized" | "unrecognized" | "absent" -- WHAT WAS PARSED
   *  (R7, GAP-082; "unrecognized" = a sidecar whose `schema` is not consumed).
   *  `services`/`personas`/`out_of_vocab_pct` are measurements only when it
   *  reads "normalized"; otherwise they are 0 placeholders and `warnings`
   *  says so. */
  sidecar_shape: string;
  /** Where `mean_confidence` came from -- the listing entry's own field. */
  confidence_source: string;
  /** What the reader states instead of refusing: an export's baked id that
   *  differs from the canonical id, a normalized sidecar declaring none,
   *  non-finite confidences ignored, any unmeasured stat. */
  warnings: string[];
}

export interface ProvenanceOverride {
  /** The LOSING class -- what the fact would have been without the winner. */
  evidence_class: string | null;
  value: unknown;
  confidence: number | null;
  drift: boolean | null;
}

export interface ImprintProvenanceRow {
  field: string;
  signal: string;
  /** One of the six classes; published as the sidecar wrote it. */
  evidence_class: string;
  /** Non-null for the shipped plugin; read it WITH `has_confidence`. */
  confidence: number;
  evidence: string;
  has_confidence: boolean;
  source: string | null;
  note: string | null;
  overrides: ProvenanceOverride | null;
}

/**
 * The canonical sidecar's own population accounting, republished verbatim.
 *
 * **`suppressed[]` is the Console's durable "is my suppression applied"
 * authority** (`docs/WORK_ORDER_console_canvas.md` §12.6, settled in S2 --
 * see `o2/console/README.md`): the compiler states which authored keys it
 * honored, so the canvas never has to infer it from a twin-side removal. The
 * rows are `{key, note, placed, matched}` in a 2026-09 sidecar; older
 * producers wrote bare key strings, so both shapes are accepted.
 * Empty `{}` on a normalized or absent sidecar -- absent, not "nothing
 * suppressed".
 */
export interface ProvenancePopulation {
  pit_entities?: number;
  placed_hosts?: number;
  /** A COUNT, not a roster, and it excludes the synthetic router (LES-241). */
  emitted_hosts?: number;
  suppressed?: Array<string | SuppressedRow>;
  [key: string]: unknown;
}

export interface SuppressedRow {
  key: string;
  note?: string | null;
  /** Whether the suppressed entity WAS a placed host (i.e. the twin lost one). */
  placed?: boolean;
  /** Whether any entity with this key was in the PIT at all. */
  matched?: boolean;
}

export interface ImprintProvenance {
  imprint_id: string;
  rows: ImprintProvenanceRow[];
  /** "canonical" | "normalized" | "unrecognized" | "absent" -- WHAT WAS
   *  PARSED. Read this one: "unrecognized" has zero rows because nothing was
   *  consumed, which is not "no evidence". */
  sidecar_shape: string;
  sidecar_schema: string | null;
  evidence_source: string;
  code_fingerprint: string | null;
  population: ProvenancePopulation;
  warnings: string[];
}

// ---------------------------------------------------------------------------
// GET /api/imprints/{id}/topology  (the alias of the topology export)
// ---------------------------------------------------------------------------
export interface TopologyExportSegment {
  cidr: string;
  name: string;
  entities: number;
  coverage_source: string | null;
}

export interface TopologyExportNode {
  id: string;
  address: string | null;
  locality: string | null;
  segment: string | null;
  segment_evidence_class: string | null;
  /** `null` = the role was never discovered -- render it distinctly. */
  role: string | null;
  role_confidence: number | null;
  role_evidence_class: string | null;
  windows_seen: number | null;
  listen_ports: string[];
  dial_ports: string[];
}

export interface TopologyExportEdge {
  src: string;
  dst: string;
  proto: string | null;
  bytes: number;
  flows: number;
  evidence_class: string | null;
}

export interface TopologyExportCensus {
  entities_total: number;
  with_role: number;
  placed: number;
  local: number;
}

export interface TopologyExport {
  imprint_id: string;
  pit_id: string | null;
  as_of: number | null;
  network: string | null;
  vantage: string | null;
  windows: number | null;
  schema_version: string | null;
  segments: TopologyExportSegment[];
  nodes: TopologyExportNode[];
  edges: TopologyExportEdge[];
  census: TopologyExportCensus;
}

// ---------------------------------------------------------------------------
// GET /api/imprints/{id}/cast
// ---------------------------------------------------------------------------
/** One `conduits[]` entry of the twin config, spelled exactly as written
 *  (R5, GAP-079). Both ends are segment NAMES -- the join key into the
 *  topology export, never a CIDR. Directed as written; drawn undirected. */
export interface CastConduit {
  from: string;
  to: string;
}

/** One ROUTER the twin config builds -- one per expanded instance of a
 *  `kind: router` spec (`gw01`, `gw02` for `count: 2`) -- and the segment
 *  NAMES the spec has a leg on, declaration order, each once (R5; per router
 *  since the GAP-082 review, S5: a flat union could not say whether there is
 *  one gateway or three). */
export interface CastRouter {
  name: string;
  segments: string[];
}

/** One `segments[]` entry of the twin config (Console S5.1.a, the segment
 *  roster). `name` is the join key into the topology export; `cidr` is the
 *  DEPLOYED network -- relocated on a `--relocate` cast, so NOT the export's
 *  observed CIDR (which is why the join is by name); `zone_type` is `null`
 *  where the config declares none, never the effective zone. */
export interface CastSegment {
  name: string;
  cidr: string;
  zone_type: string | null;
  /** The network as observed (2026-10-31): the relocation's replaced value, or `cidr`
   *  when the sidecar's row is observed/asserted; null on a synthetic segment. */
  observed_cidr?: string | null;
}

/** One `nodes[]` spec of the twin config (the declared-design map): its role,
 *  resolved count, legs' segment NAMES and the node ids it fans out to. */
export interface CastNode {
  name: string;
  kind: string;
  role: string | null;
  count: number;
  segments: string[];
  node_ids: string[];
  /** The observed entity keys the group collapsed (2026-10-31), sorted; empty on a
   *  synthetic or hand-authored group, a router, or an older sidecar. */
  members?: string[];
}

export interface CastSummary {
  id: string;
  bundle_dir: string;
  twin_config_id: string;
  description: string | null;
  pit_id: string | null;
  bundle_schema: string | null;
  topology_schema_version: string | null;
  /** The PIT row's build stamp (GAP-088). Does NOT move on a re-cast. */
  code_fingerprint: string | null;
  /**
   * The bundle's CONTENT fingerprint (ruling R4) -- the same value an apply
   * reports as `fingerprint_before/after`, so the one that moves when the
   * bundle is recompiled. Drafts are stamped with it. `null` when the bundle
   * carries none of its canonical files; ABSENT from an older server.
   */
  bundle_fingerprint?: string | null;
  params: Record<string, unknown>;
  /** The EFFECTIVE deploy-gate threshold -- what makes a host a ghost. */
  deploy_min_occupancy: number;
  deploy_min_occupancy_source: string;
  segments: number;
  node_specs: number;
  vms: number;
  hosts: number;
  /** A COUNT -- equal to `conduit_roster.length` on every bundle (R5). */
  conduits: number;
  observed_nodes: number;
  observed_edges: number;
  observed_segments: number;
  census: Record<string, unknown>;
  has_provenance: boolean;
  has_topology_export: boolean;
  has_assertions: boolean;
  has_layout: boolean;
  provenance_schema: string | null;
  provenance_path: string | null;
  sidecar_shape: string;
  evidence_source: string;
  warnings: string[];
  /** The ROSTER behind `conduits` (R5, GAP-079), in the config's own order.
   *  `[]` = the twin config declares none. OPTIONAL on the wire: a server
   *  older than R5 omits it, which means "not published", never "none" --
   *  the map says so in a note rather than presenting an empty band as a
   *  fact (GAP-082 review, S4). */
  conduit_roster?: CastConduit[];
  /** Every router the twin config builds (one per expanded instance) with
   *  its legs, including a leg on a segment the export does not carry
   *  (`internet-sink`). `[]` = the twin config declares no router; absent =
   *  not published (same rule as above). */
  routers?: CastRouter[];
  /** The ROSTER behind `segments` (Console S5.1.a), in the config's own order
   *  -- `segment_roster.length === segments`. An observed segment whose NAME
   *  is not in it is one the cast PRUNED. `[]` = the config declares no
   *  segment; ABSENT (a server predating the field) = not published, which
   *  the Console says rather than reading as "none" (the R5 rule). */
  segment_roster?: CastSegment[];
  /** The node specs (Unified Console, declared-design map). OPTIONAL: an
   *  older server omits it, and the declared map then draws segments only. */
  node_roster?: CastNode[];
}

// ---------------------------------------------------------------------------
// GET / PUT /api/imprints/{id}/assertions
// ---------------------------------------------------------------------------
/** The three blocks of the assertions file -- the closed set (`14` §3). */
export type AssertionBlock = "add" | "override" | "suppress";

/** `kind` on an `add` record -- the closed set the server derives from the
 *  vendored `axr-assertions/1` schema. Mirrored here for the editor's own
 *  pre-check; the server stays the authority and names the accepted set. */
export type AssertionAddKind = "segment" | "host" | "conduit";

export interface AssertionRecord {
  kind?: AssertionAddKind | string;
  key?: string;
  note?: string;
  name?: string;
  zone_type?: string;
  segment?: string;
  role?: string;
  image?: string;
  resources?: Record<string, unknown>;
  listen?: Array<Record<string, unknown>>;
  dial?: Array<Record<string, unknown>>;
  field?: string;
  /** Kept when `field` is set: an override may legitimately clear a field. */
  value?: unknown;
}

export interface AssertionsDoc {
  imprint_id: string;
  path: string;
  /** False before the first save -- a 200 with empty lists, never a 404. */
  exists: boolean;
  /** null until the file exists. Send it back on the next PUT. */
  etag: string | null;
  schema: string;
  imprint: string;
  as_of: string | null;
  source: string | null;
  add: AssertionRecord[];
  override: AssertionRecord[];
  suppress: AssertionRecord[];
  warnings: string[];
}

// ---------------------------------------------------------------------------
// GET / PUT /api/imprints/{id}/layout   (pins; never evidence)
// ---------------------------------------------------------------------------
export interface LayoutDoc {
  imprint_id: string;
  path: string;
  exists: boolean;
  etag: string | null;
  schema: string;
  imprint: string;
  /** `site:<prefix>` / `segment:<cidr>` -> {x, y}. A host key is refused. */
  pins: Record<string, { x: number; y: number }>;
  /**
   * The LAYOUT PLANE the pins were authored against (`layout/plane.ts`), an
   * opaque token the server round-trips and never interprets. `null` = a
   * LEGACY file, written before the stamp existed — its pins are accepted and
   * flagged, never silently dropped or silently drawn on a plane that has
   * moved under them (LES-289).
   */
  plane: string | null;
}

// ---------------------------------------------------------------------------
// POST /api/imprints/{id}/apply   (SYNCHRONOUS -- ~2.7 s, not apply_id+poll)
// ---------------------------------------------------------------------------
export interface ApplyRequest {
  etag?: string | null;
  document: AssertionsDoc | Record<string, unknown>;
}

export interface ApplyResponse {
  imprint_id: string;
  /**
   * CONTENT fingerprints axRange computes; NOT either build stamp below.
   * `fingerprint_before` is also the ONLY value `getDiff`'s `since` accepts
   * — a build stamp names no history snapshot and 404s (GAP-089).
   */
  fingerprint_before: string | null;
  fingerprint_after: string | null;
  changed: boolean;
  /**
   * The **PIT row's** build stamp, from the bundle's provenance sidecar —
   * the identity of the run that materialized the CAPTURE. A re-cast does
   * not re-materialize the PIT, so this pair is IDENTICAL across an apply
   * even when the compiler genuinely changed. Published under the name it
   * has a value for; it was `code_fingerprint_before/after` and described as
   * the compiler's build hash, which it never was (GAP-088).
   */
  pit_code_fingerprint_before: string | null;
  pit_code_fingerprint_after: string | null;
  /**
   * The build that ran each cast (`cast_invocation.code_fingerprint`) — the
   * value that DOES move when the sibling repo is upgraded.
   */
  /** Optional: absent from a server that predates GAP-087. */
  cast_code_fingerprint_before?: string | null;
  cast_code_fingerprint_after?: string | null;
  /**
   * GAP-087, and THREE-valued on purpose: `true`/`false` when both build
   * stamps were read, **`null` when one was not** — "I could not tell" is
   * not "no" (LES-273/LES-333). Render the `null` case as its own outcome,
   * never as the quiet `false` one. **Absent** (`undefined`) from a server
   * that predates the verdict: render nothing — only `true` asserts a move.
   */
  compiler_changed?: boolean | null;
  /** The server's own sentence for the changed / could-not-tell outcomes,
   *  with its reason. `null` for the ordinary "same build" one. NOT repeated
   *  in `warnings` (render it once, from here). */
  compiler_note?: string | null;
  assertions: AssertionsDoc;
  record_counts: Record<string, unknown>;
  cast_counts: Record<string, unknown>;
  stdout_json: Record<string, unknown> | null;
  stderr: string;
  returncode: number;
  argv: string[];
  install_argv: string[];
  install_returncode: number | null;
  /** False when no snapshot was recorded, so `diff?since=` cannot answer. */
  history_written: boolean;
  warnings: string[];
}

// ---------------------------------------------------------------------------
// GET /api/imprints/{id}/diff?since=<fingerprint>
// ---------------------------------------------------------------------------
export interface DiffEntry {
  key: string;
  fields: Record<string, { before?: unknown; after?: unknown }>;
}

export interface DiffSection {
  added: string[];
  removed: string[];
  changed: DiffEntry[];
}

export interface ObservedDiff {
  nodes: DiffSection;
  segments: DiffSection;
}

export interface TwinDiff {
  hosts: DiffSection;
  segments: DiffSection;
  conduits: DiffSection;
}

export interface ImprintDiff {
  imprint_id: string;
  since: string;
  current_fingerprint: string | null;
  snapshot_schema: string | null;
  source: string;
  /** `null` = could not compare. Empty = compared, nothing moved. */
  observed: ObservedDiff | null;
  twin: TwinDiff | null;
  /**
   * The AUTHORED keys whose suppression state moved between the two casts
   * (§12.6, settled in S2). Set-granularity by construction: the sidecar's
   * suppressed rows do not name the twin node they removed, so no per-removal
   * `authored_key` column could be faithful. `null` when either side carries
   * no population accounting to compare.
   */
  suppressed_keys: DiffSection | null;
  twin_config_changed: boolean;
  warnings: string[];
}
