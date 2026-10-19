/**
 * The map pane (`15` §7, centre) -- S3 makes it DRAW. It dispatches by zoom
 * level to the four views (`Level0`/`Level1`/`Level2`/`Matrix`), each fed the
 * one `ConsoleGraph` `useGraph` built from the topology export + cast. The
 * graph area is a strict three-state render (loading / failed / drawn), never
 * a nullable collapse (LES-253).
 *
 * The cast-summary strip and the evidence mix are kept from S2 -- the numbers
 * the canvas is drawn FROM, fetched independently of the graph, so they still
 * read on a bundle whose topology export failed or is absent. Search and the
 * filter bar sit above the drawing; both are the URL (`model/zoom.ts`), so the
 * back button and a bookmark work.
 *
 * **R1: the summary is a COLLAPSIBLE ONE-LINE BAR, collapsed by default.** It
 * used to be a permanent 8-tile grid plus the evidence legend, which cost the
 * top half of the pane before the drawing began (`docs/WORK_ORDER_console_
 * level0_rendering.md` §2 F6). Its state is `?summary=1` in the URL -- not a
 * store -- so it survives a reload and a pasted link. Two things are NEVER
 * collapsed: a failed cast read and a failed provenance read. An error hidden
 * behind a disclosure is an error nobody sees, and both of these say something
 * about the SERVER, not about the bundle (LES-253).
 */
import type { AssertionsDoc, CastSummary, ImprintProvenance } from "../api/types";
import type { ConsoleApiError } from "../api/client";
import type { AuthoredPin, Pins } from "../layout/pins";
import { EMPTY_CHANGE_SET, type ChangeSet } from "../model/changes";
import { countByClass } from "../model/evidence";
import { configFileOf, isHandAuthored } from "../model/declared";
import { LiveTwinBridge } from "../areas/design/LiveTwinBridge";
import { filtersToQuery, type Filters, type Zoom } from "../model/zoom";
import { Link } from "react-router";
import type { AddedSegment } from "../edit/allocate";
import type { Point } from "../layout/geometry";
import type { GraphResource } from "./useGraph";
import type { Focus } from "./selection";
import { Level0, type AddedTileView, type CloneDraftView } from "./Level0";
import { Level1, PendingSegmentLevel1, type PendingChipView } from "./Level1";
import { Level2 } from "./Level2";
import { MatrixView } from "./Matrix";
import { FiltersBar } from "./Filters";
import { Search } from "./Search";

export interface MapPaneProps {
  zoom: Zoom | null;
  filters: Filters;
  pathname: string;
  graph: GraphResource;
  provenance: ImprintProvenance | null;
  provenanceError: ConsoleApiError | null;
  provenanceLoading: boolean;
  assertions: AssertionsDoc | null;
  /** S4 pins: the saved layout doc's pin set, and the `PUT .../layout` writer.
   *  Only level 0 consumes them (segments/sites are the pinnable elements). */
  savedPins?: Pins;
  /** Two arguments, declared at EVERY link of the seam (final review S2): the
   *  whole pin set, and the one pin this gesture wrote. A link that still
   *  declared the one-argument form would let a wrapper drop the second and
   *  degrade the plane stamp to reset-only without a type error. */
  onSavePins?: (pins: Pins, authored?: AuthoredPin) => void;
  /** The saved layout document's PLANE stamp (R2) -- level 0 compares it with
   *  the plane it draws and flags pins authored against another one. */
  pinsPlane?: string | null;
  /** How many pins on file are still the ones this session wrote, for the
   *  stale-pin note's progress ("N of M re-placed"). */
  reauthoredCount?: number;
  /** The last failed `PUT .../layout`, or absent (review S3) -- said beside the
   *  pin controls, and the tiles it affects are marked. */
  pinSaveError?: string | null;
  /** The last apply's map-highlightable change set (S6). The views mark a
   *  matching element `is-changed` while `?changed=1` is on. Absent ⇒ empty. */
  changeSet?: ChangeSet;
  /** True during the five-second post-apply window (`15` §12 O5): a changed
   *  element ALSO carries `is-changed-glow`, cleared by the workspace's timer. */
  changeGlow?: boolean;
  go: (path: string) => void;
  onFocus: (focus: Focus) => void;
  /** S5.1.a: the segment the inspector is acting on, so the map can mark it
   *  (`aria-current` and a class, `15` §7). Absent ⇒ nothing marked. */
  selectedCidr?: string | null;
  /** S5.1.a (A3): the segments the edit store adds that the export does not
   *  observe. Level 1 does not draw them; a route to one says so. */
  addedSegments?: readonly AddedSegment[];
  /** S5.1.b: the added segments as level 0 draws them -- a PLACED one (a clone's
   *  pin, or its placement this session) is drawn at its position. */
  addedTiles?: readonly AddedTileView[];
  /** S5.1.d: the hosts this file adds, with the segment each is staged on --
   *  drawn as hatched chips at level 1, on an observed segment or an added one. */
  addedHosts?: readonly (PendingChipView & { segment: string })[];
  /** S5.1.b: the clone being placed or filled in. */
  cloneDraft?: CloneDraftView | null;
  /** S5.1.b: a tap or click on level 0 placed the proposed clone. */
  onPlaceClone?: (at: Point) => void;
  /** S5.1.b: an Alt-drag of a tile dropped a clone of it at `at`. */
  onCloneDrop?: (sourceCidr: string, at: Point) => void;
}

export function MapPane(props: MapPaneProps): JSX.Element {
  const { zoom, filters, pathname, graph, go, onFocus } = props;

  if (zoom === null) {
    return (
      <main className="pane pane-map">
        <h2>No such view</h2>
        <p className="notice">
          This URL does not name a level. Try <code>/design/&lt;id&gt;</code>,{" "}
          <code>/design/&lt;id&gt;/segments/10.80.20.0-24</code> or{" "}
          <code>/design/&lt;id&gt;/matrix</code>.
        </p>
      </main>
    );
  }

  if (zoom.level === "catalog") {
    return (
      <main className="pane pane-map">
        <h2>Console</h2>
        <p>Pick an imprint from the outline to open its canvas.</p>
      </main>
    );
  }

  const showSummary = zoom.level !== "host";

  return (
    // `tabIndex={-1}`: not a tab stop, but where keyboard focus returns when a
    // cleared selection's opener is no longer on the page (S5.1.a).
    <main className="pane pane-map" tabIndex={-1}>
      <h2>
        {zoom.imprint} <small className="meta">· {zoom.level}</small>
      </h2>
      <LiveTwinBridge imprintId={zoom.imprint} />
      {graph.cast && !graph.cast.has_topology_export && (
        <DeclaredStatus cast={graph.cast} drawn={graph.declared} />
      )}

      {showSummary && (
        <>
          <div className="summary-bar" data-testid="summary-bar">
            <Link
              className="summary-toggle"
              data-testid="summary-toggle"
              to={`${pathname}${filtersToQuery({ ...filters, summary: !filters.summary, problems: [] })}`}
              aria-expanded={filters.summary}
            >
              {filters.summary ? "▾" : "▸"} summary
            </Link>
            {graph.castLoading && <span className="meta">loading the cast summary…</span>}
            {graph.cast && !filters.summary && (
              <span className="meta summary-digest" data-testid="summary-digest">
                {summaryDigest(graph.cast)}
              </span>
            )}
            {/* Never collapsed: a failed read is not a fact about the bundle. */}
            {graph.castError && (
              <span className="notice is-error" role="alert">
                {graph.castError.kind}: {graph.castError.message}
              </span>
            )}
            {!filters.summary && props.provenanceError && (
              <ProvenanceReadError error={props.provenanceError} />
            )}
          </div>

          {filters.summary && (
            <div className="summary-strip" data-testid="summary-strip">
              {graph.cast && <CastNumbers cast={graph.cast} />}
              {/* NOT gated on the cast (M1). `EvidenceMix` implements the full
                  three-state contract off `provenance`/`error`/`loading` and
                  reads nothing from the cast -- gating it on that unrelated
                  read meant a FAILED provenance read rendered nowhere whenever
                  the cast read had also failed or was still in flight, which
                  contradicts this file's own "never collapsed" invariant. */}
              {/* A hand-authored twin has no evidence at all; the status line
                  above already says so in words, once. */}
              {!(graph.cast && isHandAuthored(graph.cast) && !props.provenanceError) && (
                <EvidenceMix
                  provenance={props.provenance}
                  error={props.provenanceError}
                  loading={props.provenanceLoading}
                />
              )}
            </div>
          )}
        </>
      )}

      {graph.graph && (
        <div className="map-controls">
          <Search graph={graph.graph} go={go} />
          {/* Evidence/occupancy filters describe capture evidence a declared
              design has not got -- offered only where they mean something. */}
          {!graph.declared && <FiltersBar filters={filters} pathname={pathname} />}
        </div>
      )}

      <DrawnView {...props} zoom={zoom} onFocus={onFocus} />
    </main>
  );
}

/** The graph-backed drawing, three-state (LES-253). */
function DrawnView(props: MapPaneProps & { zoom: Zoom }): JSX.Element {
  const { zoom, filters, graph, assertions, go, onFocus } = props;
  const changeSet = props.changeSet ?? EMPTY_CHANGE_SET;
  const changeGlow = props.changeGlow ?? false;
  if (zoom.level === "catalog") return <></>;

  // A bundle with no topology export (a 404 the API documents as "not
  // applicable yet") says so FIRST -- ahead of "loading", so an apply's re-read
  // of the cast is not presented as the topology loading (fix round, SF3).
  if (graph.topologyAbsent && !graph.graph) {
    return (
      <p className="meta" data-testid="graph-absent">
        There is no capture map for this bundle, and this server does not publish its declared segments, so there is
        nothing to draw yet.
      </p>
    );
  }
  if (graph.loading && !graph.graph) {
    return <p className="meta" data-testid="graph-loading">loading the topology…</p>;
  }
  if (graph.error && !graph.graph) {
    return (
      <p className="notice is-error" role="alert" data-testid="graph-error">
        the topology read FAILED ({graph.error.kind}): {graph.error.message} — the
        summary above is from a separate read and is unaffected.
      </p>
    );
  }
  if (!graph.graph) {
    return (
      <p className="meta" data-testid="graph-absent">
        Nothing to draw for this bundle.
      </p>
    );
  }

  switch (zoom.level) {
    case "network":
      return (
        <Level0
          graph={graph.graph}
          declared={graph.declared}
          filters={filters}
          savedPins={props.savedPins}
          onSavePins={props.onSavePins}
          pinsPlane={props.pinsPlane}
          reauthoredCount={props.reauthoredCount}
          pinSaveError={props.pinSaveError}
          changeSet={changeSet}
          changeGlow={changeGlow}
          go={go}
          onFocus={onFocus}
          selectedCidr={props.selectedCidr ?? null}
          added={props.addedTiles}
          cloneDraft={props.cloneDraft ?? null}
          {...(props.onPlaceClone ? { onPlaceClone: props.onPlaceClone } : {})}
          {...(props.onCloneDrop ? { onCloneDrop: props.onCloneDrop } : {})}
        />
      );
    case "segment": {
      // A3: a route to a segment only the edit store adds. It is selectable and
      // actionable in the inspector, but level 1 draws the export, and the
      // export does not have it -- said plainly rather than as "no segment".
      // (Restated for S5.1.b, LES-299: level 0 now draws a PLACED added segment,
      // so the sentence names the level that does not.)
      const added = graph.graph.segmentByCidr.has(zoom.cidr)
        ? null
        : (props.addedSegments ?? []).find((a) => a.cidr === zoom.cidr) ?? null;
      const chips = (props.addedHosts ?? []).filter((h) => h.segment === zoom.cidr);
      if (added) {
        // S5.1.d: drawn, not refused. This was a notice saying level 1 "does
        // not draw added segments yet" -- accurate when it was written, and
        // exactly what this slice builds.
        return (
          <PendingSegmentLevel1
            cidr={added.cidr}
            name={added.name}
            state={added.state}
            chips={chips}
            onFocus={onFocus}
            selected={props.selectedCidr === zoom.cidr}
          />
        );
      }
      return (
        <Level1
          graph={graph.graph}
          declared={graph.declared}
          filters={filters}
          cidr={zoom.cidr}
          changeSet={changeSet}
          changeGlow={changeGlow}
          added={chips}
          go={go}
          onFocus={onFocus}
          selected={props.selectedCidr === zoom.cidr}
        />
      );
    }
    case "host":
      return (
        <Level2
          graph={graph.graph}
          declared={graph.declared}
          hostKey={zoom.key}
          assertions={assertions}
          filters={filters}
          changeSet={changeSet}
          changeGlow={changeGlow}
        />
      );
    case "matrix":
      if (graph.declared) {
        return (
          <p className="meta" data-testid="matrix-declared">
            The matrix shows who talks to whom, which needs capture evidence — this twin has none. Its declared conduits
            (which segments may talk) are drawn on the network map.
          </p>
        );
      }
      return <MatrixView graph={graph.graph} cidr={zoom.cidr} onFocus={onFocus} />;
  }
}

/** The collapsed bar's one line: the figures an operator glances at, in the
 *  same order as the expanded grid, so expanding adds detail rather than
 *  rearranging. A bundle with no export says so instead of showing a zero. */
function summaryDigest(cast: CastSummary): string {
  return [
    `${cast.segments} segments`,
    `${cast.vms} VMs`,
    `${cast.conduits} conduits`,
    cast.has_topology_export
      ? `${cast.observed_nodes} nodes / ${cast.observed_edges} edges observed`
      : "declared design only (no capture)",
    `gate ${cast.deploy_min_occupancy}`,
  ].join(" · ");
}

function CastNumbers({ cast }: { cast: CastSummary }): JSX.Element {
  return (
    <div className="numbers" data-testid="cast-numbers">
      <Stat label="segments" value={cast.segments} note="in the twin config" />
      <Stat label="node specs" value={cast.node_specs} />
      <Stat label="VMs" value={cast.vms} note="after count: fan-out" />
      <Stat label="conduits" value={cast.conduits} />
      <Stat
        label="observed nodes"
        value={cast.has_topology_export ? cast.observed_nodes : null}
        note={cast.has_topology_export ? "from the capture" : "no capture for this twin"}
      />
      <Stat label="observed edges" value={cast.has_topology_export ? cast.observed_edges : null} />
      <Stat label="deploy gate" value={cast.deploy_min_occupancy} note={cast.deploy_min_occupancy_source} />
      {/* GAP-088: this said "compiler build hash", which it is NOT -- the
          sidecar's `code_fingerprint` is the PIT ROW's build stamp, the
          identity of the run that materialized the capture, and it does not
          move when the compiler is upgraded. The build that ran the cast
          lives in the topology export's `cast_invocation` and is reported by
          an apply (`cast_code_fingerprint_*`), not here. */}
      <Stat
        label="capture build"
        value={cast.code_fingerprint}
        note={cast.code_fingerprint ? "PIT build stamp" : cast.has_topology_export ? "not recorded" : "no capture"}
      />
    </div>
  );
}

function Stat(props: { label: string; value: number | string | null; note?: string }): JSX.Element {
  return (
    <div className="number">
      <div className="value">{props.value === null ? "—" : String(props.value)}</div>
      <div className="label">{props.label}</div>
      {props.note && <div className="note">{props.note}</div>}
    </div>
  );
}

/** The failed-read sentence, in ONE place: it is rendered by the collapsed
 *  summary bar AND by the expanded evidence mix, and the two must not drift
 *  into two different accounts of the same failure. */
function ProvenanceReadError({ error }: { error: ConsoleApiError }): JSX.Element {
  return (
    <p className="notice is-error" role="alert" data-testid="evidence-error">
      the provenance read FAILED ({error.kind}): {error.message} — this says
      nothing about whether the bundle has a sidecar.
    </p>
  );
}

/** Three outcomes, three answers (LES-253): loading / failed / absent, and
 *  only the third is a statement about the bundle. */
function EvidenceMix({
  provenance,
  error,
  loading,
}: {
  provenance: ImprintProvenance | null;
  error: ConsoleApiError | null;
  loading: boolean;
}): JSX.Element {
  if (loading) {
    return (
      <p className="meta" data-testid="evidence-loading">
        reading the provenance sidecar…
      </p>
    );
  }
  if (error) {
    return <ProvenanceReadError error={error} />;
  }
  if (!provenance || provenance.sidecar_shape === "absent") {
    return (
      <p className="notice" data-testid="evidence-absent">
        No evidence record comes with this bundle, so there is no evidence mix to show — that is &ldquo;not
        recorded&rdquo;, not &ldquo;none&rdquo;.
      </p>
    );
  }
  if (provenance.sidecar_shape === "unrecognized") {
    // Present, NOT consumed (GAP-082 review): zero rows here are "nothing was
    // read", and an empty legend would say "no evidence" instead.
    return (
      <p className="notice" data-testid="evidence-unrecognized">
        a provenance sidecar is present, but its schema (
        <span className="mono">{provenance.sidecar_schema ?? "none"}</span>) is not
        one this Console reads — the evidence mix is <strong>not consumed</strong>,
        not empty.
      </p>
    );
  }
  const counts = countByClass(provenance.rows);
  return (
    <>
      <h3>Evidence mix</h3>
      <ul className="legend" data-testid="evidence-mix">
        {counts.known.map(({ evidenceClass, count }) => (
          <li key={evidenceClass} className={`ev-${evidenceClass}`}>
            <span className="ev-swatch" />
            <span className="name">{evidenceClass}</span>
            <span className="mono">{count}</span>
          </li>
        ))}
        {counts.unknown.map(({ evidenceClass, count }) => (
          <li key={evidenceClass}>
            <span className="ev-swatch" />
            <span className="name">{evidenceClass}</span>
            <span className="mono">{count}</span>
            <span className="why">unrecognized class — published as written</span>
          </li>
        ))}
      </ul>
    </>
  );
}

/**
 * ONE plain line for a bundle with no topology export (design/16 rule 7:
 * plain words), with the exact technical reasons behind a disclosure.
 */
export function DeclaredStatus({ cast, drawn }: { cast: CastSummary; drawn: boolean }): JSX.Element {
  const hand = isHandAuthored(cast);
  const file = configFileOf(cast);
  return (
    <div className="notice declared-status" data-testid="declared-status">
      <p>
        {hand ? (
          <>
            <strong>Hand-authored twin</strong> — {drawn ? "showing its declared design from " : "its design lives in "}
            <code>{file}</code>. There is no capture evidence (no aximprint export), so nothing here is observed.
          </>
        ) : (
          <>
            <strong>No capture map for this bundle</strong> —{" "}
            {drawn ? "showing its declared design from its twin config" : "its twin config is all there is to show"}.
            Nothing here is observed.
          </>
        )}
      </p>
      <details>
        <summary>details</summary>
        <ul className="meta">
          <li>
            The bundle has no topology export (<code>GET /api/imprints/{cast.id}/topology</code> answers 404), so there
            is no observed host, segment or traffic.
          </li>
          <li>
            {cast.has_provenance
              ? "It does carry a provenance sidecar."
              : "It carries no provenance sidecar (the evidence record aximprint writes beside a cast)."}
          </li>
          <li>
            Counts are the twin config&apos;s own: {cast.segments} segments, {cast.node_specs} node specs, {cast.vms}{" "}
            VMs after <code>count:</code> fan-out. Every host is drawn as one that would deploy.
          </li>
          {!cast.node_roster && <li>This server does not publish the node specs, so segments are drawn without hosts.</li>}
        </ul>
      </details>
    </div>
  );
}

