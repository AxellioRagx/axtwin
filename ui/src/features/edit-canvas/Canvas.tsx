/**
 * The pan/zoom canvas (Phase 1, read-only): the `RF` rows of `SPEC.md`'s
 * feature table -- select, zoom-to-fit, minimap, zoom controls, dark/light
 * following the app theme, lock-all, find-node-and-centre, the hover card --
 * and the traffic overlay. The one write is a node's position after a drag,
 * reported to the page as a pin; no editing gesture exists here (no connect,
 * no delete, no add).
 */
import {
  Background,
  Controls,
  MiniMap,
  Panel,
  ReactFlow,
  ReactFlowProvider,
  useNodesState,
  useReactFlow,
  type Viewport,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { TopologyExport } from "../../api/types";
import { EMPTY_FILTER, isEmptyFilter, matchTraffic, type TrafficFilter } from "./canvas/overlay";
import { ID, absolutePosition, arcKey, pinKeyOf, type CanvasNode, type Placement } from "./canvas/placement";
import { useAppColorMode } from "./canvas/theme";
import type { ConsoleGraph } from "./model/graph";
import { resolveSearch } from "./model/search";
import { edgeTypes, GlowContext, NO_GLOW, nodeTypes, type Glow } from "./nodes";
import "./canvas.css";

export interface CanvasProps {
  graph: ConsoleGraph;
  /** The observed edges the overlay filters; `null` for an authored twin. */
  exportData: TopologyExport | null;
  placement: Placement;
  /** A node was dragged: save its absolute top-left under this pin key. */
  onPin: (key: string, at: { x: number; y: number }) => void;
  /** A `+N more` tile was clicked. */
  onToggleSite: (siteKey: string) => void;
  initialViewport: Viewport | null;
  onViewport: (v: Viewport) => void;
  /** Lock-all's initial state (synced from the deploy state in Phase 2). */
  locked?: boolean;
  notes: string[];
}

const DEFAULT_GLOW_SECONDS = 8;

export function Canvas(props: CanvasProps) {
  return (
    <ReactFlowProvider>
      <CanvasInner {...props} />
    </ReactFlowProvider>
  );
}

function CanvasInner({ graph, exportData, placement, onPin, onToggleSite, initialViewport, onViewport, locked: lockedAtStart = false, notes }: CanvasProps) {
  const colorMode = useAppColorMode();
  const rf = useReactFlow<CanvasNode>();
  const [nodes, setNodes, onNodesChange] = useNodesState<CanvasNode>(placement.nodes);
  useEffect(() => setNodes(placement.nodes), [placement, setNodes]);
  const [locked, setLocked] = useState(lockedAtStart);
  const [query, setQuery] = useState("");
  const [findNote, setFindNote] = useState<string | null>(null);
  const [filter, setFilter] = useState<TrafficFilter>(EMPTY_FILTER);
  const [glowSeconds, setGlowSeconds] = useState(String(DEFAULT_GLOW_SECONDS));
  const [glow, setGlow] = useState<Glow>(NO_GLOW);
  const [overlayNote, setOverlayNote] = useState<string | null>(null);
  const glowTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => {
    if (glowTimer.current) clearTimeout(glowTimer.current);
  }, []);

  const onNodeDragStop = useCallback(
    (_e: unknown, node: CanvasNode) => {
      const key = pinKeyOf(node);
      if (!key) return;
      const current = rf.getNodes();
      const at = absolutePosition(current.map((n) => (n.id === node.id ? { ...n, position: node.position } : n)), node.id);
      if (at) onPin(key, { x: Math.round(at.x * 100) / 100, y: Math.round(at.y * 100) / 100 });
    },
    [rf, onPin],
  );

  const onNodeClick = useCallback(
    (_e: unknown, node: CanvasNode) => {
      if (node.data.kind === "overflow") onToggleSite(node.data.siteKey);
    },
    [onToggleSite],
  );

  const centreOn = useCallback(
    (id: string) => {
      setNodes((ns) => ns.map((n) => ({ ...n, selected: n.id === id })));
      void rf.fitView({ nodes: [{ id }], duration: 300, maxZoom: 1.5, padding: 0.4 });
    },
    [rf, setNodes],
  );

  const find = useCallback(() => {
    const r = resolveSearch(graph, query);
    setFindNote(r.note);
    if (!r.zoom) return;
    if (r.zoom.level === "host") centreOn(ID.host(r.zoom.key));
    else if (r.zoom.level === "segment") {
      const seg = graph.segmentByCidr.get(r.zoom.cidr);
      if (seg) centreOn(placement.anchorByName[seg.name] === seg.name ? ID.segment(seg.name) : (placement.anchorByName[seg.name] ?? ID.segment(seg.name)));
    }
  }, [graph, query, centreOn, placement]);

  const applyOverlay = useCallback(() => {
    if (glowTimer.current) clearTimeout(glowTimer.current);
    if (!exportData) {
      setOverlayNote("an authored twin has no observed traffic to overlay");
      return;
    }
    if (isEmptyFilter(filter)) {
      setGlow(NO_GLOW);
      setOverlayNote("set a protocol, port, byte floor or peer");
      return;
    }
    const m = matchTraffic(exportData, graph, filter);
    const arcs = new Set<string>();
    for (const pair of m.pairKeys) {
      const [a, b] = pair.split("|") as [string, string];
      arcs.add(arcKey(placement.anchorByName[a] ?? a, placement.anchorByName[b] ?? b));
    }
    setGlow({ hosts: m.hostIds, arcs });
    const seconds = Math.max(1, Number(glowSeconds) || DEFAULT_GLOW_SECONDS);
    setOverlayNote(`${m.matched} of ${m.total} observed edges match · ${m.hostIds.size} hosts lit for ${seconds}s${m.problems.length ? ` · ${m.problems.join("; ")}` : ""}`);
    glowTimer.current = setTimeout(() => {
      setGlow(NO_GLOW);
      setOverlayNote((n) => (n ? `${n} · faded` : n));
    }, seconds * 1000);
  }, [exportData, filter, graph, glowSeconds, placement]);

  const status = useMemo(() => {
    const parts: string[] = [];
    if (placement.hiddenSegments) parts.push(`${placement.hiddenSegments} segments behind "+N more" tiles`);
    if (placement.hiddenPairs) parts.push(`${placement.hiddenPairs} observed pairs not drawn (the top ${placement.edges.filter((e) => e.type === "arc").length} by volume are)`);
    return [...parts, ...notes];
  }, [placement, notes]);

  const viewportProps = initialViewport ? { defaultViewport: initialViewport } : { fitView: true };

  return (
    <GlowContext.Provider value={glow}>
      <div className="canvas-shell" data-testid="canvas" data-locked={locked ? "true" : "false"} data-color-mode={colorMode}>
        <ReactFlow<CanvasNode>
          nodes={nodes}
          edges={placement.edges}
          onNodesChange={onNodesChange}
          nodeTypes={nodeTypes}
          edgeTypes={edgeTypes}
          colorMode={colorMode}
          nodesDraggable={!locked}
          nodesConnectable={false}
          elementsSelectable
          deleteKeyCode={null}
          zoomOnDoubleClick={false}
          minZoom={0.1}
          maxZoom={2.5}
          onNodeDragStop={onNodeDragStop}
          onNodeClick={onNodeClick}
          onMoveEnd={(_e, v) => onViewport(v)}
          proOptions={{ hideAttribution: true }}
          {...viewportProps}
        >
          <Background gap={24} />
          <Controls showInteractive={false} />
          <MiniMap pannable zoomable nodeStrokeWidth={2} />
          <Panel position="top-left" className="cv-toolbar">
            <form
              className="row"
              onSubmit={(e) => {
                e.preventDefault();
                find();
              }}
            >
              <input type="text" aria-label="Find node" placeholder="name, address, role or CIDR" value={query} onChange={(e) => setQuery(e.target.value)} />
              <button type="submit">Find</button>
              <button type="button" onClick={() => void rf.fitView({ duration: 300 })}>
                Zoom to fit
              </button>
              <button type="button" aria-pressed={locked} onClick={() => setLocked((l) => !l)}>
                {locked ? "Unlock all" : "Lock all"}
              </button>
              {findNote ? <span className="muted" role="status">{findNote}</span> : null}
            </form>
            <form
              className="row cv-overlay"
              aria-label="Traffic overlay"
              onSubmit={(e) => {
                e.preventDefault();
                applyOverlay();
              }}
            >
              <span className="muted">Traffic</span>
              <input type="text" aria-label="Protocol" placeholder="proto" value={filter.proto} onChange={(e) => setFilter({ ...filter, proto: e.target.value })} />
              <input type="text" aria-label="Port" placeholder="port" value={filter.port} onChange={(e) => setFilter({ ...filter, port: e.target.value })} />
              <input type="text" aria-label="Minimum bytes" placeholder="bytes ≥" value={filter.minBytes} onChange={(e) => setFilter({ ...filter, minBytes: e.target.value })} />
              <input type="text" aria-label="Peer" placeholder="peer" value={filter.peer} onChange={(e) => setFilter({ ...filter, peer: e.target.value })} />
              <input type="text" aria-label="Glow seconds" value={glowSeconds} onChange={(e) => setGlowSeconds(e.target.value)} className="cv-seconds" />
              <button type="submit">Light up</button>
              {overlayNote ? <span className="muted" role="status" data-testid="overlay-note">{overlayNote}</span> : null}
            </form>
          </Panel>
          {status.length ? (
            <Panel position="bottom-left" className="cv-status">
              {status.map((s, i) => (
                <div key={i} className="muted">{s}</div>
              ))}
            </Panel>
          ) : null}
        </ReactFlow>
      </div>
    </GlowContext.Provider>
  );
}
