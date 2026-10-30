/**
 * The `features/edit-canvas` route inside the twins area: `/twins/:id/canvas`.
 *
 * Reads the twin (summary and tree), then its graph -- the cast topology
 * export when the twin has one, else the authored scenario topology (the
 * API's 404 means "no export", never an error here) -- and the layout
 * side-car. Builds the lifted graph model, places it, and draws it; or, over
 * the element budgets, falls back to the existing list view and a role
 * matrix. The one write: a dragged node's position, PUT with the etag.
 */
import type { Viewport } from "@xyflow/react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router";

import { ApiError } from "../../api/client";
import { useApi } from "../../api/context";
import type { LayoutDoc, LayoutPins, LayoutWrite, Topology, TopologyExport, TwinSummary, TwinTree } from "../../api/types";
import { ErrorLine, errorText } from "../../areas/shell/parts";
import { useLoad } from "../../areas/shell/useLoad";
import { TwinTree as TwinTreeView } from "../../areas/twins/TwinTree";
import { Canvas } from "./Canvas";
import { authoredGraph } from "./canvas/authored";
import { castInput } from "./canvas/cast";
import { place } from "./canvas/placement";
import { CANVAS_PLANE } from "./canvas/plane";
import { matrix0 } from "./layout/matrix";
import { NO_PINS, type Pins } from "./layout/pins";
import { buildGraph, type ConsoleGraph } from "./model/graph";

interface Loaded {
  twin: TwinSummary;
  tree: TwinTree | null;
  exportData: TopologyExport | null;
  topology: Topology | null;
}

const LAYOUT_SCHEMA = "axr-layout/1";

/**
 * The PUT body after one drag: the document's pins when they were authored on
 * THIS plane (another plane's coordinates are not re-saved as this drawing's),
 * the dragged element's new absolute top-left, the plane stamp, and the etag
 * the document was read under as the precondition.
 */
export function nextLayoutWrite(current: LayoutDoc | null, id: string, key: string, at: { x: number; y: number }): LayoutWrite {
  const carried: LayoutPins = current && current.plane === CANVAS_PLANE ? current.pins : {};
  return { schema: LAYOUT_SCHEMA, imprint: id, pins: { ...carried, [key]: at }, plane: CANVAS_PLANE, etag: current?.etag ?? null };
}

function viewportKey(id: string): string {
  return `axr-canvas-viewport:${id}`;
}

function readViewport(id: string): Viewport | null {
  try {
    const raw = sessionStorage.getItem(viewportKey(id));
    if (!raw) return null;
    const v = JSON.parse(raw) as Partial<Viewport>;
    if (typeof v.x === "number" && typeof v.y === "number" && typeof v.zoom === "number") return { x: v.x, y: v.y, zoom: v.zoom };
  } catch {
    // no storage, or a stale value: start from fit-to-view
  }
  return null;
}

export function CanvasPage() {
  const api = useApi();
  const { id = "" } = useParams();
  const { data, error, loading } = useLoad<Loaded>(async (signal) => {
    const twin = await api.getTwin(id, signal);
    const tree = await api.getTwinTree(id, signal).catch(() => null);
    let exportData: TopologyExport | null = null;
    let topology: Topology | null = null;
    try {
      exportData = await api.getTwinTopology(id, signal);
    } catch (e) {
      if (!(e instanceof ApiError && e.kind === "not-found")) throw e;
      topology = await api.getScenarioTopology(id, signal);
    }
    return { twin, tree, exportData, topology };
  }, [id]);
  const layout = useLoad<LayoutDoc>((signal) => api.getLayout(id, signal), [id]);

  // The pins this page draws with and writes back: the document's when it
  // was authored on this plane, else none (another plane's coordinates are
  // not this drawing's). Unknown keys are carried through a write untouched.
  const [doc, setDoc] = useState<LayoutDoc | null>(null);
  const [pins, setPins] = useState<Pins>(NO_PINS);
  const [saveError, setSaveError] = useState<unknown>(null);
  const [savedAt, setSavedAt] = useState<string | null>(null);
  useEffect(() => {
    if (!layout.data) return;
    setDoc(layout.data);
    setPins(layout.data.plane === CANVAS_PLANE ? layout.data.pins : NO_PINS);
  }, [layout.data]);
  const [expanded, setExpanded] = useState<string[]>([]);
  const [initialViewport] = useState(() => readViewport(id));

  const built = useMemo<{ kind: "authored" | "cast"; graph: ConsoleGraph; exportData: TopologyExport | null; extraLegs: ReturnType<typeof authoredGraph>["extraLegs"] } | null>(() => {
    if (!data) return null;
    if (data.exportData) {
      return { kind: "cast", graph: buildGraph(data.exportData, castInput(id, data.tree, data.twin)), exportData: data.exportData, extraLegs: [] };
    }
    if (data.topology) {
      const a = authoredGraph(data.topology, data.tree);
      return { kind: "authored", graph: buildGraph(a.exportData, a.cast), exportData: null, extraLegs: a.extraLegs };
    }
    return null;
  }, [data, id]);

  const placement = useMemo(() => (built ? place(built.graph, pins, { expanded, extraLegs: built.extraLegs }) : null), [built, pins, expanded]);

  // Writes are chained so two quick drags never race on one etag.
  const queue = useRef<Promise<void>>(Promise.resolve());
  const docRef = useRef<LayoutDoc | null>(null);
  docRef.current = doc;
  const onPin = useCallback(
    (key: string, at: { x: number; y: number }) => {
      setPins((p) => ({ ...p, [key]: at }));
      queue.current = queue.current.then(async () => {
        try {
          const saved = await api.putLayout(id, nextLayoutWrite(docRef.current, id, key, at));
          setDoc(saved);
          setSaveError(null);
          setSavedAt(new Date().toISOString());
        } catch (e) {
          setSaveError(e);
          if (e instanceof ApiError && e.status === 412) layout.reload();
        }
      });
    },
    [api, id, layout],
  );
  const onToggleSite = useCallback((siteKey: string) => setExpanded((xs) => (xs.includes(siteKey) ? xs.filter((x) => x !== siteKey) : [...xs, siteKey])), []);
  const onViewport = useCallback(
    (v: Viewport) => {
      try {
        sessionStorage.setItem(viewportKey(id), JSON.stringify(v));
      } catch {
        // storage unavailable: the viewport is not restored, nothing else changes
      }
    },
    [id],
  );

  const notes = useMemo(() => {
    const out: string[] = [];
    if (layout.error) out.push(`positions are not saved for this twin: ${errorText(layout.error)}`);
    else if (doc && doc.exists && doc.plane !== CANVAS_PLANE) out.push(`the saved positions were drawn on another plane (${doc.plane ?? "unstamped"}) and are not applied; the next drag saves on this one`);
    if (built?.kind === "authored") out.push("authored twin: no observed evidence, no traffic to overlay");
    if (built && built.graph.castOmits.length) out.push(`the cast did not publish: ${built.graph.castOmits.join(", ")}`);
    return out;
  }, [layout.error, doc, built]);

  return (
    <div className="canvas-page">
      <div className="row">
        <h1 style={{ flex: 1 }}>
          <Link to={`/twins/${encodeURIComponent(id)}`}>Twin {id}</Link> · canvas
        </h1>
        {built ? <span className="badge">{built.kind === "cast" ? "cast twin · observed evidence" : "authored twin"}</span> : null}
        {savedAt ? <span className="muted" role="status">positions saved</span> : null}
      </div>
      <ErrorLine error={error} prefix="could not load the twin" />
      <ErrorLine error={saveError} prefix="could not save the positions" />
      {loading && !data ? <p className="muted">Loading the twin…</p> : null}
      {built && placement ? (
        placement.overBudget ? (
          <Fallback id={id} kind={built.kind} graph={built.graph} reason={placement.overBudget} />
        ) : (
          <Canvas graph={built.graph} exportData={built.exportData} placement={placement} onPin={onPin} onToggleSite={onToggleSite} initialViewport={initialViewport} onViewport={onViewport} notes={notes} />
        )
      ) : null}
    </div>
  );
}

/** Over the element budgets: the existing list view, and a role x role
 *  matrix of the observed census (the lifted `matrix0`) when it is within
 *  its own axis budget -- never a denser canvas. */
function Fallback({ id, kind, graph, reason }: { id: string; kind: "authored" | "cast"; graph: ConsoleGraph; reason: string }) {
  const matrix = useMemo(() => (kind === "cast" ? matrix0(graph) : null), [kind, graph]);
  return (
    <div className="panel" data-testid="canvas-fallback">
      <p className="notice" role="status">
        Too large for the canvas: {reason}. Showing the list{matrix && !matrix.overBudget ? " and the role matrix" : ""} instead.
      </p>
      {matrix && !matrix.overBudget && matrix.axis.length ? <MatrixTable matrix={matrix} /> : null}
      {matrix?.overBudget ? <p className="muted">The role matrix has {matrix.axisCount} axes, over its budget; the list is the view.</p> : null}
      <h2>Segments</h2>
      <TwinTreeView id={id} />
    </div>
  );
}

function MatrixTable({ matrix }: { matrix: ReturnType<typeof matrix0> }) {
  const cell = new Map(matrix.cells.map((c) => [`${c.row}>${c.col}`, c]));
  return (
    <table className="list cv-matrix" data-testid="canvas-matrix">
      <thead>
        <tr>
          <th>src \ dst</th>
          {matrix.axis.map((a) => (
            <th key={a} className="mono">{a}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {matrix.axis.map((row) => (
          <tr key={row}>
            <th className="mono">{row}</th>
            {matrix.axis.map((col) => {
              const c = cell.get(`${row}>${col}`);
              return (
                <td key={col} className="mono" title={c ? `${c.flows} flows · ${c.bytes} bytes · ${c.protos.join(", ")}` : ""}>
                  {c ? c.flows : ""}
                </td>
              );
            })}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
