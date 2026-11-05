/**
 * The pan/zoom canvas. Phase 1 (read-only): the `RF` rows of `SPEC.md`'s
 * feature table -- select, zoom-to-fit, minimap, zoom controls, dark/light
 * following the app theme, lock-all, find-node-and-centre, the hover card --
 * and the traffic overlay; the one write is a node's position after a drag,
 * reported to the page as a pin. Phase 2 (`edit` given): the gestures over
 * the working document -- a palette item dropped or click-placed onto a
 * segment, a host dragged onto another segment, a handle dragged from a host
 * to a segment bus or the sticky attach mode (Esc leaves it), right-click
 * menus, snap-to-grid with Alt bypass, align H/V, annotations -- each
 * reported to the page, which owns the command stack. The canvas never
 * writes the twin config itself.
 */
import {
  Background,
  MiniMap,
  Panel,
  ReactFlow,
  ReactFlowProvider,
  SelectionMode,
  useNodesState,
  useReactFlow,
  type Connection,
  type Edge,
  type Node,
  type NodeChange,
  type Viewport,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import type { TopologyExport } from "../../api/types";
import { EMPTY_FILTER, isEmptyFilter, matchTraffic, type TrafficFilter } from "./canvas/overlay";
import { ID, absolutePosition, arcKey, pinKeyOf, type CanvasNode, type Placement } from "./canvas/placement";
import { useAppColorMode } from "./canvas/theme";
import { Inspector, Popover, Rail, StatusStrip, type InspectorTab, type RailButton } from "./chrome";
import { Icon } from "./icons";
import { newAnnotation, type Annotation, type AnnotationKind } from "./edit/annotations";
import { EditContext, type EditState, type PaletteItem } from "./edit/EditContext";
import type { DocModel } from "./edit/document";
import type { ConsoleGraph, GraphNode } from "./model/graph";
import { resolveSearch } from "./model/search";
import { PALETTE_MIME } from "./Palette";
import type { Selection } from "./SidePanel";
import { edgeTypes, GlowContext, legParts, NO_GLOW, nodeTypes, type Glow } from "./nodes";
import "./canvas.css";

export interface CanvasEditProps {
  model: DocModel;
  /** View mode: the deploy is running; nothing but annotations moves. */
  locked: boolean;
  selection: Selection;
  onSelection(sel: Selection): void;
  placing: PaletteItem | null;
  onPlacing(item: PaletteItem | null): void;
  /** A palette item landed on a segment (or nowhere, `segment: null`). */
  onPlace(item: PaletteItem, segment: string | null, count: number, at: { x: number; y: number } | null): void;
  onAttach(node: string, segment: string): void;
  /** An existing chip was dropped onto another segment. */
  onMove(node: string, segment: string): void;
  onDetach(node: string, segment: string): void;
  onRemoveNode(name: string): void;
  onRemoveSegment(name: string): void;
  onAddSegment(): void;
  annotations: Annotation[];
  onAnnotations(list: Annotation[]): void;
}

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
  /** Lock-all's initial state (synced from the deploy state in edit mode). */
  locked?: boolean;
  /** What the inspector's Notes tab says about this twin. */
  notes: string[];
  /** Phase 2: the editing gestures. Absent = Phase 1's read-only canvas. */
  edit?: CanvasEditProps;
  /** Edit mode: the palette, shown as a popover off the rail's `+`. */
  palette?: ReactNode;
  /** Edit mode: the inspector's Selection tab (the property panels) and its
   *  Checks tab (validation + pending changes), with the badge the tab wears. */
  inspector?: { selection: ReactNode; checks: ReactNode | null; checksBadge: string | null };
}

const DEFAULT_GLOW_SECONDS = 8;
const GRID = 24;

export function Canvas(props: CanvasProps) {
  return (
    <ReactFlowProvider>
      <CanvasInner {...props} />
    </ReactFlowProvider>
  );
}

/** The segment whose box holds `at` (absolute canvas coordinates), by name. Pure. */
export function segmentAt(nodes: ReadonlyArray<Pick<CanvasNode, "id" | "position" | "parentId" | "data" | "width" | "height" | "measured">>, at: { x: number; y: number }): string | null {
  for (const n of nodes) {
    if (n.data.kind !== "segment") continue;
    const abs = absolutePosition(nodes, n.id);
    const w = n.measured?.width ?? n.width ?? 0;
    const h = n.measured?.height ?? n.height ?? 0;
    if (abs && at.x >= abs.x && at.x <= abs.x + w && at.y >= abs.y && at.y <= abs.y + h) return n.data.name;
  }
  return null;
}

/** A handle drag from a host or router onto a segment: the attach gesture's ends. Pure. */
export function connectionToAttach(c: Pick<Connection, "source" | "target">): { node: string; segment: string } | null {
  const node = c.source.startsWith("host:") ? c.source.slice(5) : c.source.startsWith("router:") ? c.source.slice(7) : null;
  const segment = c.target.startsWith("seg:") ? c.target.slice(4) : null;
  return node && segment ? { node, segment } : null;
}

type MenuTarget = Parameters<EditState["onContextMenu"]>[1];

type Popover = "find" | "traffic" | "palette" | "annotate";

function CanvasInner({ graph, exportData, placement, onPin, onToggleSite, initialViewport, onViewport, locked: lockedAtStart = false, notes, edit, palette, inspector }: CanvasProps) {
  const colorMode = useAppColorMode();
  const rf = useReactFlow<CanvasNode>();
  const [nodes, setNodes, onNodesChangeBase] = useNodesState<CanvasNode>(placement.nodes);
  const [lockedAll, setLockedAll] = useState(lockedAtStart);
  // ---- the chrome (`SPEC.md` "Shell and chrome"): the rail's tool, which
  // popover is open, which inspector tab is open (null = the tab strip only;
  // a click on a node opens Selection), and the read-only selection.
  const [tool, setTool] = useState<"select" | "pan">("select");
  const [popover, setPopover] = useState<Popover | null>(null);
  const [inspectorTab, setInspectorTab] = useState<InspectorTab | null>(null);
  const [roSel, setRoSel] = useState<{ kind: "host" | "router" | "segment"; id: string } | null>(null);
  const togglePopover = useCallback((which: Popover) => setPopover((cur) => (cur === which ? null : which)), []);
  const locked = lockedAll || Boolean(edit?.locked);
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

  // ---- Phase 2 state: grid, snap, attach mode, menus, annotations ----
  const [grid, setGrid] = useState(true);
  const [snap, setSnap] = useState(true);
  const [alt, setAlt] = useState(false);
  const [attachArmed, setAttachArmed] = useState(false);
  const [attachFrom, setAttachFrom] = useState<string | null>(null);
  const [showAnnotations, setShowAnnotations] = useState(true);
  const [menu, setMenu] = useState<{ x: number; y: number; target: MenuTarget } | null>(null);
  const editing = Boolean(edit) && !locked;

  const annotationNodes = useMemo<CanvasNode[]>(() => {
    if (!edit || !showAnnotations) return [];
    return edit.annotations.filter((a) => !a.hidden).map((a) => ({
      id: `ann:${a.id}`,
      type: "annotation",
      position: { x: a.x, y: a.y },
      width: a.w,
      height: Math.max(a.h, a.kind === "line" ? 2 : 0),
      draggable: !a.locked,
      selectable: true,
      zIndex: -1,
      data: { kind: "annotation", ann: a },
    }));
  }, [edit, showAnnotations]);
  useEffect(() => setNodes([...placement.nodes, ...annotationNodes]), [placement, annotationNodes, setNodes]);

  // Annotation resizes land in the annotation list when the resize ends.
  const onNodesChange = useCallback(
    (changes: NodeChange<CanvasNode>[]) => {
      onNodesChangeBase(changes);
      if (!edit) return;
      for (const c of changes) {
        if (c.type === "dimensions" && c.id.startsWith("ann:") && c.resizing === false && c.dimensions) {
          const id = c.id.slice(4);
          const dims = c.dimensions;
          edit.onAnnotations(edit.annotations.map((a) => (a.id === id ? { ...a, w: Math.round(dims.width), h: Math.round(dims.height) } : a)));
        }
      }
    },
    [onNodesChangeBase, edit],
  );

  const onNodeDragStop = useCallback(
    (_e: unknown, node: CanvasNode) => {
      if (node.data.kind === "annotation" && edit) {
        const a = node.data.ann;
        edit.onAnnotations(edit.annotations.map((x) => (x.id === a.id ? { ...x, x: Math.round(node.position.x), y: Math.round(node.position.y) } : x)));
        return;
      }
      const key = pinKeyOf(node);
      if (!key) return;
      const current = rf.getNodes();
      const moved = current.map((n) => (n.id === node.id ? { ...n, position: node.position } : n));
      const at = absolutePosition(moved, node.id);
      if (!at) return;
      // A host chip dropped inside another segment's box moves there (the attach gesture by drop).
      if (editing && edit && node.data.kind === "host") {
        const w = node.measured?.width ?? node.width ?? 0;
        const h = node.measured?.height ?? node.height ?? 0;
        const nodeId = node.data.nodeId;
        const under = segmentAt(moved.filter((n) => n.id !== node.id), { x: at.x + w / 2, y: at.y + h / 2 });
        const group = edit.model.te.nodes.find((n) => n.name === nodeId);
        if (under && group && group.nets[0]?.segment !== under) {
          edit.onMove(nodeId, under);
          return;
        }
      }
      onPin(key, { x: Math.round(at.x * 100) / 100, y: Math.round(at.y * 100) / 100 });
    },
    [rf, onPin, edit, editing],
  );

  // Selection is explicit -- a click, a Shift-click to extend, an edge click,
  // the pane to clear, the marquee for many -- and mirrored into React Flow's
  // own `selected` flags so the drawing agrees with the side panel.
  const select = useCallback(
    (next: Selection, ids: string[]) => {
      if (!edit) return;
      const key = JSON.stringify(next);
      if (key !== lastSel.current) {
        lastSel.current = key;
        edit.onSelection(next);
        if (next.kind !== "none") setInspectorTab("selection");
      }
      setNodes((ns) => ns.map((n) => (n.selected === ids.includes(n.id) ? n : { ...n, selected: ids.includes(n.id) })));
    },
    [edit, setNodes],
  );
  const lastSel = useRef<string>("");
  const selectionRef = useRef<Selection>({ kind: "none" });
  if (edit) selectionRef.current = edit.selection;

  // Alt bypasses snap while held; Esc leaves the attach mode, disarms the
  // palette and closes a popover.
  useEffect(() => {
    if (!edit) return;
    const down = (e: KeyboardEvent) => {
      if (e.key === "Alt") setAlt(true);
      if (e.key === "Escape") {
        setAttachArmed(false);
        setAttachFrom(null);
        edit.onPlacing(null);
        setMenu(null);
        setPopover(null);
        select({ kind: "none" }, []);
      }
    };
    const up = (e: KeyboardEvent) => {
      if (e.key === "Alt") setAlt(false);
    };
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    return () => {
      window.removeEventListener("keydown", down);
      window.removeEventListener("keyup", up);
    };
  }, [edit, select]);

  const onNodeClick = useCallback(
    (e: React.MouseEvent, node: CanvasNode) => {
      if (node.data.kind === "overflow") onToggleSite(node.data.siteKey);
      if (!edit) {
        const d = node.data;
        if (d.kind === "host") setRoSel({ kind: "host", id: d.nodeId });
        else if (d.kind === "router") setRoSel({ kind: "router", id: d.name });
        else if (d.kind === "segment") setRoSel({ kind: "segment", id: d.name });
        else return;
        setInspectorTab("selection");
        return;
      }
      const d = node.data;
      const cur = selectionRef.current;
      if (d.kind === "host" || d.kind === "router") {
        const name = d.kind === "host" ? d.nodeId : d.name;
        const names = e.shiftKey && cur.kind === "node" ? [...cur.names.filter((n) => n !== name), name] : [name];
        select({ kind: "node", names }, names.map((n) => (edit.model.te.nodes.find((x) => x.name === n)?.kind === "router" ? ID.router(n) : ID.host(n))));
      } else if (d.kind === "segment") {
        // While a gesture is armed a segment is its TARGET, not the selection's subject.
        if (!(editing && (edit.placing || (attachArmed && attachFrom)))) {
          const names = e.shiftKey && cur.kind === "segment" ? [...cur.names.filter((n) => n !== d.name), d.name] : [d.name];
          select({ kind: "segment", names }, names.map(ID.segment));
        }
      } else if (d.kind === "annotation") {
        select({ kind: "annotation", id: d.ann.id }, [node.id]);
      }
      if (!editing) return;
      if (node.data.kind === "segment") {
        if (edit.placing) {
          edit.onPlace(edit.placing, node.data.name, edit.placing.count ?? 1, null);
          return;
        }
        if (attachArmed && attachFrom) {
          edit.onAttach(attachFrom, node.data.name);
          setAttachFrom(null);
          return;
        }
      }
      if (attachArmed && (node.data.kind === "host" || node.data.kind === "router")) setAttachFrom(node.data.kind === "host" ? node.data.nodeId : node.data.name);
    },
    [onToggleSite, edit, editing, attachArmed, attachFrom, select],
  );

  const onEdgeClick = useCallback(
    (_e: React.MouseEvent, edge: Edge) => {
      const leg = legParts(edge.id);
      if (leg) select({ kind: "leg", ...leg }, []);
    },
    [select],
  );

  // A marquee selects many at once; a single pick is the click handlers' above.
  const onSelectionChange = useCallback(
    ({ nodes: sel }: { nodes: Node[]; edges: Edge[] }) => {
      if (!edit || sel.length < 2) return;
      const data = sel.map((n) => n.data as CanvasNode["data"]);
      const hosts = data.filter((d) => d.kind === "host" || d.kind === "router").map((d) => (d.kind === "host" ? d.nodeId : d.kind === "router" ? d.name : ""));
      const segs = data.filter((d) => d.kind === "segment").map((d) => (d.kind === "segment" ? d.name : ""));
      const next: Selection = hosts.length ? { kind: "node", names: hosts } : segs.length ? { kind: "segment", names: segs } : { kind: "none" };
      const key = JSON.stringify(next);
      if (key !== lastSel.current) {
        lastSel.current = key;
        edit.onSelection(next);
      }
    },
    [edit],
  );

  const onConnect = useCallback(
    (c: Connection) => {
      const a = connectionToAttach(c);
      if (a && edit && editing) edit.onAttach(a.node, a.segment);
    },
    [edit, editing],
  );

  const onDrop = useCallback(
    (e: React.DragEvent) => {
      if (!edit || !editing) return;
      const raw = e.dataTransfer.getData(PALETTE_MIME);
      if (!raw) return;
      e.preventDefault();
      let item: PaletteItem & { count?: number };
      try {
        item = JSON.parse(raw) as PaletteItem & { count?: number };
      } catch {
        return;
      }
      const at = rf.screenToFlowPosition({ x: e.clientX, y: e.clientY });
      const seg = segmentAt(rf.getNodes(), at);
      edit.onPlace(item, seg, item.count ?? 1, at);
    },
    [edit, editing, rf],
  );

  const onContextMenu = useCallback<EditState["onContextMenu"]>((e, target) => {
    e.preventDefault();
    e.stopPropagation();
    const shell = (e.currentTarget as HTMLElement).closest(".canvas-shell") as HTMLElement | null;
    const box = shell?.getBoundingClientRect();
    setMenu({ x: e.clientX - (box?.left ?? 0), y: e.clientY - (box?.top ?? 0), target });
  }, []);

  const editState = useMemo<EditState | null>(() => (edit ? { model: edit.model, locked, attachArmed, attachFrom, placing: edit.placing, onContextMenu } : null), [edit, locked, attachArmed, attachFrom, onContextMenu]);

  const align = useCallback(
    (axis: "h" | "v") => {
      const current = rf.getNodes();
      const sel = current.filter((n) => n.selected && pinKeyOf(n));
      if (sel.length < 2) return;
      const abs = sel.map((n) => ({ n, at: absolutePosition(current, n.id)! }));
      const ref = axis === "h" ? Math.min(...abs.map((a) => a.at.y)) : Math.min(...abs.map((a) => a.at.x));
      const moved = new Map<string, { x: number; y: number }>();
      for (const { n, at } of abs) {
        const target = axis === "h" ? { x: at.x, y: ref } : { x: ref, y: at.y };
        moved.set(n.id, target);
        onPin(pinKeyOf(n)!, target);
      }
      setNodes((ns) =>
        ns.map((n) => {
          const t = moved.get(n.id);
          if (!t) return n;
          const parent = n.parentId ? absolutePosition(ns, n.parentId) : null;
          return { ...n, position: parent ? { x: t.x - parent.x, y: t.y - parent.y } : t };
        }),
      );
    },
    [rf, onPin, setNodes],
  );

  const addAnnotation = useCallback(
    (kind: AnnotationKind) => {
      if (!edit) return;
      const v = rf.getViewport();
      const shell = document.querySelector<HTMLElement>(".canvas-shell");
      const w = shell?.clientWidth || 800;
      const h = shell?.clientHeight || 500;
      const at = { x: Math.round((-v.x + w / 2) / v.zoom), y: Math.round((-v.y + h / 2) / v.zoom) };
      edit.onAnnotations([...edit.annotations, newAnnotation(kind, at)]);
      setShowAnnotations(true);
    },
    [edit, rf],
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
    return parts;
  }, [placement]);

  // The rail's shortcuts: only when the keyboard is on the canvas, never in a field.
  useEffect(() => {
    const down = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable)) return;
      switch (e.key) {
        case "v": setTool("select"); break;
        case "h": setTool("pan"); break;
        case "f": void rf.fitView({ duration: 300 }); break;
        case "+": case "=": void rf.zoomIn({ duration: 200 }); break;
        case "-": void rf.zoomOut({ duration: 200 }); break;
        case "l": if (!edit?.locked) setLockedAll((l) => !l); break;
        case "/": e.preventDefault(); togglePopover("find"); break;
        case "t": togglePopover("traffic"); break;
        case "a": if (edit) togglePopover("palette"); break;
        default: return;
      }
    };
    window.addEventListener("keydown", down);
    return () => window.removeEventListener("keydown", down);
  }, [rf, edit, locked, togglePopover]);

  const centreOnListed = useCallback((id: string, kind: "host" | "router") => centreOn(kind === "router" ? ID.router(id) : ID.host(id)), [centreOn]);

  const viewTools: RailButton[] = [
    { name: "Select", key: "V", icon: <Icon.select />, pressed: tool === "select", onClick: () => setTool("select") },
    { name: "Pan", key: "H", icon: <Icon.pan />, pressed: tool === "pan", onClick: () => setTool("pan") },
    { name: "Zoom in", key: "+", icon: <Icon.zoomIn />, onClick: () => void rf.zoomIn({ duration: 200 }) },
    { name: "Zoom out", key: "-", icon: <Icon.zoomOut />, onClick: () => void rf.zoomOut({ duration: 200 }) },
    { name: "Zoom to fit", key: "F", icon: <Icon.fit />, onClick: () => void rf.fitView({ duration: 300 }) },
    { name: lockedAll ? "Unlock all" : "Lock all", key: "L", icon: lockedAll ? <Icon.lock /> : <Icon.unlock />, pressed: lockedAll, disabled: Boolean(edit?.locked), onClick: () => setLockedAll((l) => !l) },
    { name: "Light up traffic", key: "T", icon: <Icon.light />, pressed: popover === "traffic", onClick: () => togglePopover("traffic") },
    { name: "Find a node", key: "/", icon: <Icon.find />, pressed: popover === "find", onClick: () => togglePopover("find") },
  ];
  const editTools: RailButton[] | null = edit
    ? [
        { name: "Add to the design", key: "A", icon: <Icon.plus />, pressed: popover === "palette", onClick: () => togglePopover("palette"), testId: "rail-add" },
        { name: attachArmed ? (attachFrom ? `Attach ${attachFrom} → click a segment` : "Attach: click a host") : "Attach", icon: <Icon.attach />, pressed: attachArmed, disabled: !editing, onClick: () => { setAttachArmed((a) => !a); setAttachFrom(null); }, testId: "rail-attach" },
        { name: "Snap to grid", icon: <Icon.snap />, pressed: snap, onClick: () => setSnap((v) => !v), testId: "rail-snap" },
        { name: "Show grid", icon: <Icon.grid />, pressed: grid, onClick: () => setGrid((g) => !g), testId: "rail-grid" },
        { name: "Align horizontally", icon: <Icon.alignH />, disabled: !editing, onClick: () => align("h"), testId: "rail-align-h" },
        { name: "Align vertically", icon: <Icon.alignV />, disabled: !editing, onClick: () => align("v"), testId: "rail-align-v" },
        { name: "Annotate", icon: <Icon.annotate />, pressed: popover === "annotate", onClick: () => togglePopover("annotate"), testId: "rail-annotate" },
      ]
    : null;

  const viewportProps = initialViewport ? { defaultViewport: initialViewport } : { fitView: true };

  const menuItems = useMemo((): Array<{ label: string; run(): void; danger?: boolean }> => {
    if (!menu || !edit) return [];
    const t = menu.target;
    const items: Array<{ label: string; run(): void; danger?: boolean }> = [];
    if (t.kind === "host" || t.kind === "router") {
      const group = edit.model.te.nodes.find((n) => n.name === t.name);
      items.push({
        label: "Attach to a segment… (then click one)",
        run: () => {
          setAttachArmed(true);
          setAttachFrom(t.name);
        },
      });
      for (const leg of group?.nets ?? []) items.push({ label: `Detach from ${leg.segment}`, run: () => edit.onDetach(t.name, leg.segment) });
      items.push({ label: `Remove ${t.name}`, run: () => edit.onRemoveNode(t.name), danger: true });
    } else if (t.kind === "leg") {
      items.push({ label: `Detach ${t.node} from ${t.segment}`, run: () => edit.onDetach(t.node, t.segment), danger: true });
    } else if (t.kind === "segment") {
      if (edit.placing) items.push({ label: `Place ${edit.placing.label} here`, run: () => edit.onPlace(edit.placing!, t.name, edit.placing!.count ?? 1, null) });
      if (attachArmed && attachFrom) items.push({ label: `Attach ${attachFrom} here`, run: () => edit.onAttach(attachFrom, t.name) });
      items.push({ label: "Add a segment", run: () => edit.onAddSegment() });
      items.push({ label: `Remove segment ${t.name}`, run: () => edit.onRemoveSegment(t.name), danger: true });
    }
    return items;
  }, [menu, edit, attachArmed, attachFrom]);

  return (
    <GlowContext.Provider value={glow}>
      <EditContext.Provider value={editState}>
        <div
          className={`canvas-shell${editing ? " editing" : ""}${attachArmed ? " attaching" : ""}`}
          data-testid="canvas"
          data-locked={locked ? "true" : "false"}
          data-color-mode={colorMode}
          data-mode={edit ? (locked ? "view" : "edit") : "readonly"}
          onClick={() => setMenu(null)}
        >
          <ReactFlow<CanvasNode>
            nodes={nodes}
            edges={placement.edges}
            onNodesChange={onNodesChange}
            nodeTypes={nodeTypes}
            edgeTypes={edgeTypes}
            colorMode={colorMode}
            nodesDraggable={!locked}
            nodesConnectable={editing}
            elementsSelectable
            panOnDrag={tool === "pan" ? true : [1, 2]}
            selectionOnDrag={tool === "select"}
            selectionMode={SelectionMode.Partial}
            deleteKeyCode={null}
            zoomOnDoubleClick={false}
            minZoom={0.1}
            maxZoom={2.5}
            snapToGrid={editing && snap && !alt}
            snapGrid={[GRID, GRID]}
            onNodeDragStop={onNodeDragStop}
            onNodeClick={onNodeClick}
            onEdgeClick={onEdgeClick}
            onSelectionChange={onSelectionChange}
            onConnect={onConnect}
            onDrop={onDrop}
            onDragOver={(e) => {
              if (editing) {
                e.preventDefault();
                e.dataTransfer.dropEffect = "copy";
              }
            }}
            onPaneClick={() => {
              setMenu(null);
              select({ kind: "none" }, []);
            }}
            onMoveEnd={(_e, v) => onViewport(v)}
            proOptions={{ hideAttribution: true }}
            {...viewportProps}
          >
            {grid ? <Background gap={GRID} /> : null}
            <MiniMap pannable zoomable nodeStrokeWidth={2} position="bottom-right" />
            <Panel position="top-left" className="cv-chrome-left">
              <Rail view={viewTools} edit={editTools}>
                {popover === "find" ? (
                  <Popover title="Find a node" onClose={() => setPopover(null)} testId="popover-find">
                    <form
                      className="row"
                      onSubmit={(e) => {
                        e.preventDefault();
                        find();
                      }}
                    >
                      <input type="text" aria-label="Find node" placeholder="name, address, role or CIDR" value={query} onChange={(e) => setQuery(e.target.value)} autoFocus />
                      <button type="submit">Find</button>
                    </form>
                    {findNote ? <p className="muted" role="status">{findNote}</p> : null}
                  </Popover>
                ) : null}
                {popover === "traffic" ? (
                  <Popover title="Light up traffic" onClose={() => setPopover(null)} testId="popover-traffic">
                    <form
                      className="cv-overlay"
                      aria-label="Traffic overlay"
                      onSubmit={(e) => {
                        e.preventDefault();
                        applyOverlay();
                      }}
                    >
                      <label className="cv-field"><span className="cv-field-label">Protocol</span><input type="text" aria-label="Protocol" placeholder="tcp, udp…" value={filter.proto} onChange={(e) => setFilter({ ...filter, proto: e.target.value })} /></label>
                      <label className="cv-field"><span className="cv-field-label">Port</span><input type="text" aria-label="Port" placeholder="443" value={filter.port} onChange={(e) => setFilter({ ...filter, port: e.target.value })} /></label>
                      <label className="cv-field"><span className="cv-field-label">Minimum bytes</span><input type="text" aria-label="Minimum bytes" placeholder="bytes ≥" value={filter.minBytes} onChange={(e) => setFilter({ ...filter, minBytes: e.target.value })} /></label>
                      <label className="cv-field"><span className="cv-field-label">Peer</span><input type="text" aria-label="Peer" placeholder="address or prefix" value={filter.peer} onChange={(e) => setFilter({ ...filter, peer: e.target.value })} /></label>
                      <div className="row">
                        <label className="cv-count">
                          for
                          <input type="text" aria-label="Glow seconds" value={glowSeconds} onChange={(e) => setGlowSeconds(e.target.value)} className="cv-seconds" />
                          s
                        </label>
                        <button type="submit" className="primary">Light up</button>
                      </div>
                    </form>
                    {overlayNote ? <p className="muted" role="status" data-testid="overlay-note">{overlayNote}</p> : null}
                  </Popover>
                ) : null}
                {popover === "palette" && palette ? (
                  <Popover title="Add to the design" onClose={() => setPopover(null)} wide testId="popover-palette">
                    {palette}
                  </Popover>
                ) : null}
                {popover === "annotate" && edit ? (
                  <Popover title="Annotate" onClose={() => setPopover(null)} testId="popover-annotate">
                    <div className="row" aria-label="Annotations">
                      <button type="button" onClick={() => addAnnotation("text")}>text</button>
                      <button type="button" onClick={() => addAnnotation("rect")}>rect</button>
                      <button type="button" onClick={() => addAnnotation("ellipse")}>ellipse</button>
                      <button type="button" onClick={() => addAnnotation("line")}>line</button>
                    </div>
                    <div className="row">
                      <button type="button" aria-pressed={!showAnnotations} onClick={() => setShowAnnotations((v) => !v)}>
                        {showAnnotations ? "Hide all" : "Show annotations"}
                      </button>
                    </div>
                    <p className="muted">a new annotation lands at the viewport's centre; drag it, resize it, and edit its text in the inspector</p>
                  </Popover>
                ) : null}
              </Rail>
            </Panel>
            <Panel position="top-right" className="cv-chrome-right">
              <Inspector
                open={inspectorTab}
                onOpen={setInspectorTab}
                selection={inspector ? inspector.selection : <ReadOnlySelection graph={graph} sel={roSel} />}
                graph={graph}
                onCentre={centreOnListed}
                notes={notes}
                checks={inspector?.checks ?? null}
                checksBadge={inspector?.checksBadge ?? null}
                hasSelection={edit ? edit.selection.kind !== "none" : roSel !== null}
              />
            </Panel>
            {status.length ? (
              <Panel position="bottom-left" className="cv-chrome-bottom">
                <StatusStrip lines={status} />
              </Panel>
            ) : null}
          </ReactFlow>
          {menu && menuItems.length ? (
            <ul className="cv-menu" role="menu" style={{ left: menu.x, top: menu.y }} data-testid="context-menu">
              {menuItems.map((m) => (
                <li key={m.label} role="menuitem">
                  <button
                    type="button"
                    className={m.danger ? "danger" : undefined}
                    onClick={(e) => {
                      e.stopPropagation();
                      setMenu(null);
                      m.run();
                    }}
                  >
                    {m.label}
                  </button>
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      </EditContext.Provider>
    </GlowContext.Provider>
  );
}

/** The inspector's Selection tab without an editor (a cast or authored
 *  twin): the clicked host's or segment's facts, read from the graph. */
function ReadOnlySelection({ graph, sel }: { graph: ConsoleGraph; sel: { kind: "host" | "router" | "segment"; id: string } | null }) {
  if (!sel) return <p className="muted">click a host, a router or a segment</p>;
  if (sel.kind === "segment") {
    const seg = graph.segmentByName.get(sel.id);
    if (!seg) return <p className="muted">segment {sel.id} is not in the graph</p>;
    return (
      <div className="cv-ro" data-testid="ro-segment">
        <h3 className="mono">{seg.name}</h3>
        <dl className="facts">
          <dt>observed</dt><dd className="mono">{seg.observedCidr}</dd>
          {seg.deployedCidr ? <><dt>deployed</dt><dd className="mono">{seg.deployedCidr}</dd></> : null}
          <dt>hosts</dt><dd>{seg.hostCount}{seg.ghostCount ? ` (${seg.ghostCount} ghosts)` : ""}</dd>
          {seg.zoneType ? <><dt>zone</dt><dd>{seg.zoneType}</dd></> : null}
        </dl>
      </div>
    );
  }
  if (sel.kind === "router") {
    const r = graph.routers.find((x) => x.name === sel.id);
    return (
      <div className="cv-ro" data-testid="ro-router">
        <h3 className="mono">{sel.id}</h3>
        <dl className="facts">
          <dt>legs</dt><dd>{r ? r.segments.join(", ") : "none"}</dd>
        </dl>
      </div>
    );
  }
  const n: GraphNode | undefined = graph.nodeById.get(sel.id);
  if (!n) return <p className="muted">{sel.id} is not in the graph</p>;
  return (
    <div className="cv-ro" data-testid="ro-host">
      <h3 className="mono">{n.address ?? n.id}</h3>
      <dl className="facts">
        <dt>key</dt><dd className="mono">{n.id}</dd>
        <dt>segment</dt><dd className="mono">{n.segment ?? "unplaced"}</dd>
        <dt>role</dt><dd>{n.role ?? <span className="muted">not discovered</span>}{n.roleEvidenceClass ? <span className="muted"> · {n.roleEvidenceClass}</span> : null}</dd>
        {n.listenPorts.length ? <><dt>listens</dt><dd className="mono">{n.listenPorts.join(", ")}</dd></> : null}
        {n.occupancy !== null ? <><dt>occupancy</dt><dd>{Math.round(n.occupancy * 100)}%</dd></> : null}
        {n.isGhost ? <><dt>ghost</dt><dd>{n.ghostReasons.join(", ")}</dd></> : null}
      </dl>
    </div>
  );
}
