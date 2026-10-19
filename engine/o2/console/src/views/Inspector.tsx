/**
 * The inspector pane (`15` §7, right). S3 adds a **focus detail** block at the
 * top -- a matrix cell's edge list, or the O7 *unplaced* / *external* drawers
 * -- above the frame facts (the parsed zoom, filters and the assertions file's
 * state) that S2 rendered. The level-2 facts table itself is the map pane at
 * the host level (`Level2`); this pane stays the frame's inspector.
 *
 * Rendering the parsed state is not filler -- it is how a reader checks that
 * `?class=nonsense` was REFUSED rather than silently ignored (`15` §7).
 */
import { useMemo, type ReactNode } from "react";

import type { AssertionsDoc } from "../api/types";
import type { ConsoleGraph } from "../model/graph";
import type { EditStoreState } from "../edit/store";
import { entriesInState } from "../edit/store";
import { roleBucket } from "../model/roles";
import type { Filters, Zoom } from "../model/zoom";
import { matrix0, matrixSegment } from "../layout/matrix";
import { Legend } from "./Legend";
import { Fact } from "./paneParts";
import type { Focus } from "./selection";

export interface InspectorProps {
  /** The graph is a DECLARED design (no capture evidence). */
  declared?: boolean;
  zoom: Zoom | null;
  filters: Filters;
  edits: EditStoreState;
  assertions: AssertionsDoc | null;
  graph: ConsoleGraph | null;
  focus: Focus;
  onClearFocus: () => void;
  /**
   * S5.1.a: the SELECTED element's facts and actions
   * (`views/ElementActions.tsx`), built by the workspace which owns the store
   * dispatch. Drawn FIRST -- above the frame facts -- because the selected
   * element is what the operator is acting on, and the parsed-route facts
   * below it are context for that, not the subject.
   */
  actions?: ReactNode;
  /** The change list and the save/apply controls (`views/EditPanel.tsx`),
   *  built by the workspace which owns the store dispatch and the save
   *  handlers. Absent on the catalog root or before an imprint is chosen. */
  editPanel?: ReactNode;
  /** What a refused keyboard gesture was refused for (fix round, T5: Escape
   *  will not leave an imprint with unsaved edits). Rendered in a live region
   *  that is always present, so the message is announced when it appears. */
  status?: string | null;
}

export function Inspector(props: InspectorProps): JSX.Element {
  const { zoom, filters, edits, assertions, graph, focus, onClearFocus, editPanel } = props;
  return (
    <aside className="pane pane-inspector" aria-label="Inspector">
      <p
        className={props.status ? "notice" : "sr-only"}
        role="status"
        aria-live="polite"
        data-testid="inspector-status"
      >
        {props.status ?? ""}
      </p>
      {/* The matrix cell / O7 drawers. A segment or host SELECTION is a
          different `Focus` kind and renders through `actions` below, so the two
          never draw at once for the same click. */}
      {focus && graph && (
        <FocusDetail zoom={zoom} graph={graph} focus={focus} onClear={onClearFocus} />
      )}

      {props.actions}

      <h2>View</h2>
      <dl className="facts">
        <Fact name="showing" value={zoom ? (LEVEL_WORDS[zoom.level] ?? zoom.level) : "a page this canvas does not know"} />
        {zoom && zoom.level !== "catalog" && <Fact name="imprint" value={zoom.imprint} />}
        {zoom && (zoom.level === "segment" || zoom.level === "matrix") && zoom.cidr && (
          <Fact name="segment" value={zoom.cidr} />
        )}
        {zoom && zoom.level === "host" && <Fact name="host" value={zoom.key} />}
        {zoom && zoom.level === "host" && zoom.cidr && <Fact name="segment" value={zoom.cidr} />}
      </dl>

      <h3>Filters</h3>
      <dl className="facts">
        <Fact name="ghosts" value={filters.ghosts ? "shown" : "hidden"} />
        <Fact name="class" value={filters.evidenceClass ?? "any"} />
        <Fact
          name="occupancy_lt"
          value={filters.occupancyLt === null ? "—" : String(filters.occupancyLt)}
        />
      </dl>
      {filters.problems.map((p) => (
        <p className="notice" key={p}>
          {p}
        </p>
      ))}

      <h3>Assertions</h3>
      <dl className="facts">
        <Fact name="file" value={assertions?.exists ? assertions.path : "not written yet"} />
        <Fact name="etag" value={edits.etag ?? "—"} />
        <Fact name="pending" value={String(entriesInState(edits, "pending").length)} />
        <Fact name="saved" value={String(entriesInState(edits, "saved").length)} />
        <Fact name="applied" value={String(entriesInState(edits, "applied").length)} />
      </dl>
      {edits.conflict && (
        <p className="notice is-error" role="alert" data-testid="edit-conflict">
          {edits.conflict.message}
          {edits.mustReload && (
            <>
              {" "}
              <strong>Reload the file before saving again</strong> — the etag
              this browser holds is stale, and the one on disk (
              <span className="mono">{edits.conflict.serverEtag ?? "unknown"}</span>
              ) is reported, not adopted.
            </>
          )}
        </p>
      )}
      {edits.problems.map((p) => (
        <p className="notice" key={p} data-testid="edit-problem">
          {p}
        </p>
      ))}
      {assertions?.warnings.map((w) => (
        <p className="notice" key={w}>
          {w}
        </p>
      ))}

      {editPanel}

      <h3>Evidence</h3>
      <Legend declared={!!props.declared} />
    </aside>
  );
}

/** The transient focus detail: a matrix cell's edges, or an O7 drawer. */
function FocusDetail(props: {
  zoom: Zoom | null;
  graph: ConsoleGraph;
  focus: Focus;
  onClear: () => void;
}): JSX.Element {
  const { zoom, graph, focus, onClear } = props;
  const scope = zoom?.level === "matrix" ? zoom.cidr : undefined;

  // Build the matrix ONCE per (graph, scope), not on every inspector re-render
  // (an edit-store change re-renders this pane), and only when a cell is
  // focused -- reusing the layout `MatrixView` already computed rather than a
  // fresh O(edges) pass each render (SHOULD-FIX C). `useMemo` is before the
  // early return so the hook order is unconditional.
  const cellMatrix = useMemo(
    () =>
      focus?.kind === "cell"
        ? scope === undefined
          ? matrix0(graph)
          : matrixSegment(graph, scope)
        : null,
    [graph, scope, focus?.kind],
  );

  if (!focus) return <></>;

  // S5.1.a's two in-place selection kinds are rendered by
  // `views/ElementActions.tsx` (facts + actions), not as a focus detail -- they
  // share the one `Focus` slot, so this pane must decline them rather than fall
  // through to a drawer. (A host is never an in-place selection: it is a route.)
  if (focus.kind === "segment" || focus.kind === "site-add" || focus.kind === "clone") {
    return <></>;
  }

  if (focus.kind === "cell") {
    const cell = cellMatrix?.cells.find((c) => c.row === focus.row && c.col === focus.col) ?? null;
    // `cellMatrix === null` (a segment scope that resolved to nothing) is
    // unknown ("—"); a resolved matrix with no matching cell is a real zero.
    const known = cellMatrix !== null;
    return (
      <section className="focus" data-testid="focus-cell">
        <h3>
          cell <button type="button" className="link" onClick={onClear}>clear</button>
        </h3>
        <dl className="facts">
          <Fact name="from" value={focus.row} />
          <Fact name="to" value={focus.col} />
          <Fact name="flows" value={known ? String(cell?.flows ?? 0) : "—"} />
          <Fact name="bytes" value={known ? String(cell?.bytes ?? 0) : "—"} />
          <Fact name="edges" value={known ? String(cell?.edgeCount ?? 0) : "—"} />
          <Fact name="protos" value={cell && cell.protos.length ? cell.protos.join(", ") : "—"} />
        </dl>
      </section>
    );
  }

  if (focus.kind === "unplaced") {
    return (
      <section className="focus" data-testid="focus-unplaced">
        <h3>
          unplaced · {graph.unplacedLocal.length}{" "}
          <button type="button" className="link" onClick={onClear}>clear</button>
        </h3>
        <p className="meta">local entities the export placed in no segment (O7).</p>
        <ul className="drawer-list">
          {graph.unplacedLocal.slice(0, 50).map((n) => (
            <li key={n.id} className="mono">
              {n.id} <span className="meta">{n.role ?? "unroled"}</span>
            </li>
          ))}
        </ul>
        {graph.unplacedLocal.length > 50 && <p className="meta">… and {graph.unplacedLocal.length - 50} more</p>}
      </section>
    );
  }

  // nonlocal
  return (
    <section className="focus" data-testid="focus-nonlocal">
      <h3>
        external · {graph.nonLocal.length}{" "}
        <button type="button" className="link" onClick={onClear}>clear</button>
      </h3>
      <p className="meta">non-local entities — the Internet tier's population, addressable in the matrix's <code>external</code> row/column (O7).</p>
      <ul className="drawer-list">
        {graph.nonLocal.slice(0, 50).map((n) => (
          <li key={n.id} className="mono">
            {n.id} <span className="meta">{roleBucket(n.role, n.locality)}</span>
          </li>
        ))}
      </ul>
      {graph.nonLocal.length > 50 && <p className="meta">… and {graph.nonLocal.length - 50} more</p>}
    </section>
  );
}

/** The zoom level in an operator's words (the right pane used to print the
 *  grammar's own token, "catalog", which read as jargon). */
export const LEVEL_WORDS: Readonly<Record<string, string>> = {
  catalog: "the list of imprints",
  network: "the whole network",
  segment: "one segment",
  host: "one host",
  matrix: "who talks to whom",
};

