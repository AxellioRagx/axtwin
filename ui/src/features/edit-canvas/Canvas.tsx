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
  useNodesInitialized,
  useNodesState,
  useOnViewportChange,
  useReactFlow,
  useStore,
  useStoreApi,
  type Connection,
  type Edge,
  type Node,
  type NodeChange,
  type Viewport,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import type { TopologyExport } from "../../api/types";
import { FAN_CHOICES, FAN_DEFAULT, fanLabel, fanOut } from "./canvas/fan";
import { decodePoints, nearestPolyline } from "./canvas/legs";
import { EMPTY_FILTER, isEmptyFilter, matchTraffic, type TrafficFilter } from "./canvas/overlay";
import { ID, absolutePosition, arcKey, pinKeyOf, type CanvasNode, type Placement } from "./canvas/placement";
import { useAppColorMode } from "./canvas/theme";
import { boundsOf, foldFitTargets, foldViewport } from "./canvas/fit";
import { Inspector, Popover, Rail, StatusStrip, type InspectorTab, type RailButton } from "./chrome";
import { Icon } from "./icons";
import { newAnnotation, type Annotation, type AnnotationKind } from "./edit/annotations";
import { EditContext, type EditState, type PaletteItem } from "./edit/EditContext";
import type { DocModel } from "./edit/document";
import { boundaryAt, portOnLink, sameSelector, selectorOf, type TapSelector } from "./edit/tap";
import type { TeModel } from "./edit/te";
import { LabOverlay, type LabSource } from "./canvas/LabOverlay";
import { MEMBERS_ZOOM, invZoom, nextLevel, nextMode, zoomBucket, type DetailLevel, type DetailMode } from "./model/detail";
import type { ConsoleGraph, GraphNode } from "./model/graph";
import { resolveSearch } from "./model/search";
import { PALETTE_MIME, placesWithoutSegment } from "./Palette";
import type { Selection } from "./SidePanel";
import { marqueeSubtree, subtreeCount, subtreeOrNull, subtreePrefixes } from "./edit/subtree";
import { DEVICE_H, DEVICE_W } from "./layout/geometry";
import { twoOctetPrefix } from "./model/keys";
import { crossingText, DetailContext, edgeTypes, GlowContext, LABELS_MODES, LabelsContext, legParts, NO_GLOW, nodeTypes, OverlayContext, ProvFilterContext, type Glow, type LabelsMode } from "./nodes";
import { arcScope, arcShown } from "./canvas/evidence";
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
  /** Rule 6 (track 4): a router's drop dragged from one bus to another re-homes that leg. */
  onRehome(node: string, from: string, to: string): void;
  onRemoveNode(name: string): void;
  onRemoveSegment(name: string): void;
  /** Add a bus under the site of `near` (a segment name), else of the selection. */
  onAddSegment(near?: string): void;
  /** A segment tap's chip was dropped on another pill (topology-layers §8): a `taps` change. */
  onMoveTap?(index: number, segment: string): void;
  /** A tap's chip was dropped on a segment, a router joining two segments (the boundary
   *  between them) or a link (a port on its VM end): one `taps` change (2026-11-09). */
  onRetargetTap?(index: number, sel: TapSelector): void;
  /** A member chip was dropped on another group (§5): that one member moves there (a topology change). */
  onMoveMember?(from: string, index: number, into: string): void;
  /** Copy the subtree under `root` (§6.2, §6.3): attached by a trunk to `attachTo`, else parked; a
   *  parked copy dropped on the canvas stands at `at` (its root's top-left); `prefix` re-allocates
   *  every copied segment under another site. The page selects the copy. */
  onDuplicateSubtree?(root: string, drop?: { attachTo?: string | null; at?: { x: number; y: number } | null; prefix?: string | null }): void;
  /** The attach tool between two devices or a device and a host, or a parked device dropped on another (§6.3, §6.4): one `links[]` entry. */
  onLink?(from: string, to: string): void;
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
  /** A bus was reordered: every lane of its site, re-ranked, as one change. */
  onPinMany: (pins: Record<string, { x: number; y: number }>) => void;
  /** A `+N more` pill was clicked (its key: `carrier:<device>`). */
  onToggleSite: (siteKey: string) => void;
  /** The rail's Fan-out: how many pills a carrier shows before folding (the page re-places). */
  fan?: number;
  onFan?: (n: number) => void;
  /** The Checks tab's provenance filter: the one source left bright, or null. */
  provFilter?: string | null;
  /** Whether the provenance overlay applies (R3-1): a cast twin's; a hand-authored twin has none (everything is authored). */
  provenanceOverlay?: boolean;
  initialViewport: Viewport | null;
  onViewport: (v: Viewport) => void;
  /** Lock-all's initial state (synced from the deploy state in edit mode). */
  locked?: boolean;
  /** What the inspector's Notes tab says about this twin. */
  notes: string[];
  /** The layout side-car's own group (unsaved moves, Save layout, Discard): floats at the
   *  canvas's bottom edge, beside the drawing it is about, not in the twin's bar. */
  layoutBar?: ReactNode;
  /** Phase 2: the editing gestures. Absent = Phase 1's read-only canvas. */
  edit?: CanvasEditProps;
  /** Edit mode: the palette, shown as a popover off the rail's `+`. */
  palette?: ReactNode;
  /** Edit mode: the inspector's Selection tab (the property panels) and its
   *  Checks tab (validation + pending changes), with the badge the tab wears. */
  inspector?: { selection: ReactNode; checks: ReactNode | null; checksBadge: string | null };
  /** The deployed (or default) target's lab, for the rail's Lab overlay (targets-and-lab §2.3); absent: no toggle. */
  lab?: LabSource | null;
}

const DEFAULT_GLOW_SECONDS = 8;
const GRID = 24;
/** A fit never zooms past this: a two-bus twin at 2.5 filled the window with four icons. */
const FIT_MAX_ZOOM = 1.5;
/** A folded site group (rendering rule 3) is its label alone. */
const SITE_FOLDED_W = 220;
const SITE_FOLDED_H = 30;
/** The labels toggle (rendering rule 4), kept per user in this browser. */
const LABELS_KEY = "axr-canvas-labels";
function readLabels(): LabelsMode {
  try {
    const v = localStorage.getItem(LABELS_KEY);
    return v === "all" || v === "none" || v === "selected" ? v : "selected";
  } catch {
    return "selected";
  }
}
function nextLabels(m: LabelsMode): LabelsMode {
  return LABELS_MODES[(LABELS_MODES.indexOf(m) + 1) % LABELS_MODES.length]!;
}
/** The fan-out limit N (rendering rule 7), kept per user in this browser. */
const FAN_KEY = "axr-canvas-fan";
function readFan(): number {
  try {
    const v = localStorage.getItem(FAN_KEY);
    if (v === "all") return Number.POSITIVE_INFINITY;
    const n = Number(v);
    return v !== null && FAN_CHOICES.includes(n) ? n : FAN_DEFAULT;
  } catch {
    return FAN_DEFAULT;
  }
}
function nextFan(n: number): number {
  return FAN_CHOICES[(FAN_CHOICES.indexOf(n) + 1) % FAN_CHOICES.length]!;
}

export function Canvas(props: CanvasProps) {
  return (
    <ReactFlowProvider>
      <CanvasInner {...props} />
    </ReactFlowProvider>
  );
}

/** The segment whose box holds `at` (absolute canvas coordinates), by name. Pure. */
/** The device (a switch or a router) whose box contains `at`, by name. Pure. */
export function deviceAt(nodes: ReadonlyArray<Pick<CanvasNode, "id" | "position" | "parentId" | "data" | "width" | "height" | "measured">>, at: { x: number; y: number }): string | null {
  for (const n of nodes) {
    if (n.data.kind !== "device" && n.data.kind !== "router") continue;
    const abs = absolutePosition(nodes, n.id);
    const w = n.measured?.width ?? n.width ?? 0;
    const h = n.measured?.height ?? n.height ?? 0;
    if (abs && at.x >= abs.x && at.x <= abs.x + w && at.y >= abs.y && at.y <= abs.y + h) return n.data.name;
  }
  return null;
}

/** The data of the edge a tap chip was dropped on (a device link, or a segment's link). */
export type TapDropEdge = { kind?: string; index?: number; segment?: string };

/** Where a dropped tap chip lands (topology-layers §8), or null (it springs back). Pure: on a router joining two
 *  segments, the boundary between them; on a segment (its body, below the pill), `{scope: segment, target}`; on a
 *  device link, a port on its VM end; on a segment's link, that segment. `edgeAt` is read only when nothing
 *  under the chip's centre decided it. */
export function tapDropSelector(
  others: ReadonlyArray<Pick<CanvasNode, "id" | "position" | "parentId" | "data" | "width" | "height" | "measured">>,
  centre: { x: number; y: number },
  te: TeModel,
  edgeAt: () => TapDropEdge | undefined,
): TapSelector | null {
  const dev = deviceAt(others, centre);
  const atDevice = dev ? boundaryAt(te, dev) : null;
  if (atDevice) return atDevice;
  const seg = segmentAt(others, { x: centre.x, y: centre.y + 20 });
  if (seg) return { scope: "segment", target: seg };
  const data = edgeAt();
  if (data?.kind === "link" && typeof data.index === "number") return portOnLink(te, data.index);
  if (data?.kind === "seglink" && data.segment) return { scope: "segment", target: data.segment };
  return null;
}

/** The React Flow edge under a pointer event (its id), skipping the dragged node on top. jsdom has no
 *  `elementsFromPoint`: null there. */
function edgeUnder(e: { clientX?: number; clientY?: number } | null | undefined): string | null {
  if (!e || typeof e.clientX !== "number" || typeof e.clientY !== "number" || typeof document.elementsFromPoint !== "function") return null;
  for (const el of document.elementsFromPoint(e.clientX, e.clientY)) {
    const g = el.closest('[data-testid^="rf__edge-"]');
    if (g) return g.getAttribute("data-testid")!.slice("rf__edge-".length);
  }
  return null;
}

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

/** The two-octet site prefix of the pill or site fold nearest ABOVE `at` whose span covers its x
 *  (where a drop "under a site" lands), or null. Pure. */
export function siteAbove(nodes: ReadonlyArray<Pick<CanvasNode, "id" | "position" | "parentId" | "data" | "width" | "height" | "measured">>, at: { x: number; y: number }): string | null {
  let best: { y: number; prefix: string } | null = null;
  for (const n of nodes) {
    if (n.parentId) continue;
    const d = n.data;
    const prefix = d.kind === "segment" ? twoOctetPrefix(d.cidr) : d.kind === "overflow" && d.siteKey.startsWith("site:") ? d.siteKey.slice(d.siteKey.indexOf("|") + 1) : null;
    if (!prefix) continue;
    const w = n.measured?.width ?? n.width ?? 0;
    if (at.x < n.position.x || at.x > n.position.x + w || n.position.y > at.y) continue;
    if (!best || n.position.y > best.y) best = { y: n.position.y, prefix };
  }
  return best?.prefix ?? null;
}

/** Where a carried subtree copy would land at `at` (§6.3): onto a device not in the subtree (a
 *  trunk), else parked there -- re-allocated under the site above when that site's prefix is not
 *  one the subtree's segments already sit in. Pure. */
export function carryTarget(nodes: Parameters<typeof deviceAt>[0], at: { x: number; y: number }, inSubtree: readonly string[], prefixes: readonly string[]): { attachTo: string } | { prefix: string | null } {
  const device = deviceAt(nodes, at);
  if (device && !inSubtree.includes(device)) return { attachTo: device };
  const site = siteAbove(nodes, at);
  return { prefix: site && !prefixes.includes(site) ? site : null };
}

/** A handle drag from a host or router onto a segment: the attach gesture's ends. Pure. */
export function connectionToAttach(c: Pick<Connection, "source" | "target">): { node: string; segment: string } | null {
  const node = c.source.startsWith("host:") ? c.source.slice(5) : c.source.startsWith("router:") ? c.source.slice(7) : null;
  const segment = c.target.startsWith(ID.segment("")) ? c.target.slice(ID.segment("").length) : null;
  return node && segment ? { node, segment } : null;
}

type MenuTarget = Parameters<EditState["onContextMenu"]>[1];
/** What a context menu is about: a node, a leg, a segment, or the empty pane
 *  (with the flow position under the pointer). */
type MenuSubject = MenuTarget | { kind: "pane"; at: { x: number; y: number } };

type Popover = "find" | "traffic" | "palette" | "annotate";

/** The Lab overlay's toggle (targets-and-lab §2.3), off by default, kept per user in this browser. */
const LAB_KEY = "axr-canvas-lab";
function readLab(): boolean {
  try {
    return localStorage.getItem(LAB_KEY) === "on";
  } catch {
    return false;
  }
}

/** The provenance overlay's toggle (§3.3), kept per user in this browser. */
const OVERLAY_KEY = "axr-canvas-overlay";
function readOverlay(): boolean {
  try {
    return localStorage.getItem(OVERLAY_KEY) === "on";
  } catch {
    return false;
  }
}

function CanvasInner({ graph, exportData, placement, onPin, onPinMany, onToggleSite, fan: fanProp, onFan, provFilter = null, provenanceOverlay = true, initialViewport, onViewport, locked: lockedAtStart = false, notes, layoutBar, edit, palette, inspector, lab = null }: CanvasProps) {
  const colorMode = useAppColorMode();
  const rf = useReactFlow<CanvasNode>();
  const [nodes, setNodes, onNodesChangeBase] = useNodesState<CanvasNode>(placement.nodes);
  const [lockedAll, setLockedAll] = useState(lockedAtStart);
  // ---- the chrome (`SPEC.md` "Shell and chrome"): the rail's tool, which
  // popover is open, which inspector tab is open (null = the tab strip only;
  // a click on a node opens Selection), and the read-only selection.
  const [tool, setTool] = useState<"select" | "pan">("select");
  // Space held: pan with any button while it is down (a trackpad has no middle button).
  const [spaceHeld, setSpaceHeld] = useState(false);
  useEffect(() => {
    const isField = (t: EventTarget | null) => {
      const el = t as HTMLElement | null;
      return Boolean(el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT" || el.isContentEditable));
    };
    const down = (e: KeyboardEvent) => {
      if (e.code === "Space" && !isField(e.target) && !e.repeat) {
        e.preventDefault();
        setSpaceHeld(true);
      }
    };
    const up = (e: KeyboardEvent) => {
      if (e.code === "Space") setSpaceHeld(false);
    };
    const blur = () => setSpaceHeld(false);
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    window.addEventListener("blur", blur);
    return () => {
      window.removeEventListener("keydown", down);
      window.removeEventListener("keyup", up);
      window.removeEventListener("blur", blur);
    };
  }, []);
  const [popover, setPopover] = useState<Popover | null>(null);
  const [inspectorTab, setInspectorTab] = useState<InspectorTab | null>(null);
  const [roSel, setRoSel] = useState<{ kind: "host" | "router" | "device" | "segment"; id: string } | null>(null);
  const togglePopover = useCallback((which: Popover) => setPopover((cur) => (cur === which ? null : which)), []);
  // ---- node level of detail (`model/detail.ts`): the zoom's bucket from the
  // store (re-renders only on a crossing), hysteresis in state, and the
  // rail's toggle that pins a level or lets the zoom decide.
  const bucket = useStore((st) => zoomBucket(st.transform[2]));
  const [autoLevel, setAutoLevel] = useState<DetailLevel>("full");
  const [detailMode, setDetailMode] = useState<DetailMode>("auto");
  const [labels, setLabels] = useState<LabelsMode>(readLabels);
  useEffect(() => {
    try {
      localStorage.setItem(LABELS_KEY, labels);
    } catch {
      // no storage: the choice lives for the page
    }
  }, [labels]);
  // Site groups folded to their label (rendering rule 3): their segments and chips are hidden.
  const [folded, setFolded] = useState<string[]>([]);
  const toggleFolded = useCallback((key: string) => setFolded((xs) => (xs.includes(key) ? xs.filter((x) => x !== key) : [...xs, key])), []);
  // The fan-out limit (rendering rule 7) and the sites whose fans the operator opened by a click on the tint.
  const [fanN, setFanN] = useState<number>(() => fanProp ?? readFan());
  useEffect(() => {
    try {
      localStorage.setItem(FAN_KEY, fanLabel(fanN));
    } catch {
      // no storage: the choice lives for the page
    }
    onFan?.(fanN);
  }, [fanN]); // eslint-disable-line react-hooks/exhaustive-deps
  // The provenance overlay (§3.3): off, a dot on asserted and authored items; on, every state.
  const [overlayPref, setOverlay] = useState<boolean>(readOverlay);
  // The Lab overlay: chrome over the drawing, never a node and never an edit.
  const [labOn, setLabOn] = useState<boolean>(readLab);
  useEffect(() => {
    try {
      localStorage.setItem(LAB_KEY, labOn ? "on" : "off");
    } catch {
      // per-viewer convenience only
    }
  }, [labOn]);
  const overlay = provenanceOverlay && overlayPref;
  useEffect(() => {
    try {
      localStorage.setItem(OVERLAY_KEY, overlayPref ? "on" : "off");
    } catch {
      // no storage: the choice lives for the page
    }
  }, [overlayPref]);
  const [fanOpen, setFanOpen] = useState<string[]>([]);
  const toggleFanOpen = useCallback((key: string) => setFanOpen((xs) => (xs.includes(key) ? xs.filter((x) => x !== key) : [...xs, key])), []);
  useEffect(() => {
    if (bucket === "mid") return;
    setAutoLevel((prev) => nextLevel(prev, bucket === "hi" ? 1 : 0));
  }, [bucket]);
  const detail: DetailLevel = detailMode === "auto" ? autoLevel : detailMode;
  // `--inv` on the shell keeps a compact tile's text at a constant screen
  // size (`canvas.css`); written straight to the DOM on every viewport
  // change, so no React render rides on a pan or a zoom.
  const setInv = useCallback((zoom: number) => shellRef.current?.style.setProperty("--inv", invZoom(zoom).toFixed(3)), []);
  useOnViewportChange({ onChange: (v) => setInv(v.zoom), onEnd: (v) => setInv(v.zoom) });
  useEffect(() => setInv(rf.getZoom()), [rf, setInv, placement]);
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
  // A handle drag (the attach gesture) is in flight: every bus's target handle grows to its whole lane.
  const [connecting, setConnecting] = useState(false);
  const [showAnnotations, setShowAnnotations] = useState(true);
  const [menu, setMenu] = useState<{ x: number; y: number; target: MenuSubject } | null>(null);
  const shellRef = useRef<HTMLDivElement | null>(null);
  /** The last node a double-click zoomed to (the shell's `data-zoom-target`). */
  const [zoomTarget, setZoomTarget] = useState<string | null>(null);
  const editing = Boolean(edit) && !locked;

  // Keyed on the annotation list, not the edit props as a whole: the node
  // array is rebuilt from this (below), which drops every `selected` flag,
  // and the edit props change on every selection.
  const annotations = edit?.annotations;
  const annotationNodes = useMemo<CanvasNode[]>(() => {
    if (!annotations || !showAnnotations) return [];
    return annotations.filter((a) => !a.hidden).map((a) => ({
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
  }, [annotations, showAnnotations]);
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
      // A tap's chip moves the tap (§8, 2026-11-09; before the pin-key check: a tap has no pin, and that
      // early return was why a dragged tap never moved): dropped on a router joining two segments, the boundary
      // between them; on a segment, that segment; on a link, a port on its VM end. Elsewhere it springs back.
      if (node.data.kind === "tap") {
        const current = rf.getNodes();
        const moved = current.map((n) => (n.id === node.id ? { ...n, position: node.position } : n));
        const at = absolutePosition(moved, node.id);
        const others = moved.filter((n) => n.id !== node.id);
        const centre = at ? { x: at.x + (node.measured?.width ?? node.width ?? 0) / 2, y: at.y + (node.measured?.height ?? node.height ?? 0) / 2 } : null;
        const index = Number(node.data.id);
        const binding = edit?.model.tap?.bindings.find((b) => b.index === index) ?? null;
        let sel: TapSelector | null = null;
        if (editing && edit && centre) {
          sel = tapDropSelector(others, centre, edit.model.te, () => {
            const edgeId = edgeUnder(_e as unknown as { clientX?: number; clientY?: number });
            return (edgeId ? rf.getEdge(edgeId)?.data : undefined) as TapDropEdge | undefined;
          });
        }
        // The chip goes back to its spot either way; a move redraws it where the tap now sits.
        setNodes((ns) => ns.map((n) => (n.id === node.id ? { ...n, position: current.find((c) => c.id === node.id)?.position ?? n.position } : n)));
        if (sel && binding && !sameSelector(sel, selectorOf(binding))) {
          if (edit?.onRetargetTap) edit.onRetargetTap(index, sel);
          else if (edit?.onMoveTap && sel.scope === "segment" && node.data.scope === "segment") edit.onMoveTap(index, sel.target);
        }
        return;
      }
      const key = pinKeyOf(node);
      if (!key) return;
      const current = rf.getNodes();
      const moved = current.map((n) => (n.id === node.id ? { ...n, position: node.position } : n));
      const at = absolutePosition(moved, node.id);
      if (!at) return;
      // A pill drags free on the tree plane: its `segment:` pin is an absolute top-left (epoch 6).
      void onPinMany;
      // A parked device (no link names it) dropped on another device attaches there (§6.3).
      if (editing && edit?.onLink && (node.data.kind === "device" || node.data.kind === "router")) {
        const name = node.data.name;
        const parked = !edit.model.te.links.some((l) => l.a.device === name || l.b.device === name);
        const w = node.measured?.width ?? node.width ?? 0;
        const h = node.measured?.height ?? node.height ?? 0;
        const under = parked ? deviceAt(moved.filter((n) => n.id !== node.id), { x: at.x + w / 2, y: at.y + h / 2 }) : null;
        if (under && under !== name) {
          edit.onLink(under, name);
          return;
        }
      }
      // A host icon dropped inside another bus's lane moves there (the attach gesture by drop);
      // on a lane it is already on, it just takes the spot.
      if (editing && edit && node.data.kind === "host") {
        const w = node.measured?.width ?? node.width ?? 0;
        const h = node.measured?.height ?? node.height ?? 0;
        const nodeId = node.data.nodeId;
        const under = segmentAt(moved.filter((n) => n.id !== node.id), { x: at.x + w / 2, y: at.y + h / 2 });
        const group = edit.model.te.nodes.find((n) => n.name === nodeId);
        if (under && group && !group.nets.some((l) => l.segment === under)) {
          edit.onMove(nodeId, under);
          return;
        }
      }
      onPin(key, { x: Math.round(at.x * 100) / 100, y: Math.round(at.y * 100) / 100 });
    },
    [rf, onPin, onPinMany, edit, editing],
  );

  // Selection is explicit -- a click, a Shift-click to extend, an edge click,
  // the pane to clear, the marquee for many -- and mirrored into React Flow's
  // own `selected` flags so the drawing agrees with the side panel.
  const select = useCallback(
    (next: Selection, ids: string[]) => {
      if (!edit) return;
      // Compared against the page's CURRENT selection, not the last one sent: the page resets
      // its selection on a reload (a re-cast, a deploy), and the same node must open again.
      if (JSON.stringify(next) !== JSON.stringify(selectionRef.current)) {
        edit.onSelection(next);
        if (next.kind !== "none") setInspectorTab("selection");
      }
      setNodes((ns) => ns.map((n) => (n.selected === ids.includes(n.id) ? n : { ...n, selected: ids.includes(n.id) })));
    },
    [edit, setNodes],
  );
  const selectionRef = useRef<Selection>({ kind: "none" });
  if (edit) selectionRef.current = edit.selection;
  // A subtree selection the page set (after a duplicate) is mirrored into React Flow's flags,
  // so the drawing agrees and the echo in onSelectionChange reads as the subtree.
  const subtreeKey = edit?.selection.kind === "subtree" ? edit.selection.root : null;
  useEffect(() => {
    if (!edit || subtreeKey === null) return;
    const t = subtreeOrNull(edit.model, subtreeKey);
    if (!t) return;
    const ids = new Set([...t.devices.map((d) => (graph.routers.some((r) => r.name === d) ? ID.router(d) : `device:${d}`)), ...t.segments.map(ID.segment), ...t.hosts.map(ID.host)]);
    setNodes((ns) => ns.map((n) => (n.selected === ids.has(n.id) ? n : { ...n, selected: ids.has(n.id) })));
  }, [subtreeKey, edit?.model, graph, setNodes, edit]);
  /** Alt-click / the menu's Select subtree (§6.1): the device and everything below it, as one selection. */
  const selectSubtree = useCallback(
    (root: string) => {
      if (!edit) return;
      const t = subtreeOrNull(edit.model, root);
      if (!t) return;
      const ids = [...t.devices.map((d) => (graph.routers.some((r) => r.name === d) ? ID.router(d) : `device:${d}`)), ...t.segments.map(ID.segment), ...t.hosts.map(ID.host)];
      select({ kind: "subtree", root }, ids);
    },
    [edit, graph, select],
  );

  // Alt bypasses snap while held; Esc leaves the attach mode, disarms the
  // palette and closes a popover.
  useEffect(() => {
    if (!edit) return;
    const down = (e: KeyboardEvent) => {
      if (e.key === "Alt") setAlt(true);
      if (e.key === "Escape") {
        setCarry(null);
        setAttachArmed(false);
        setAttachFrom(null);
        edit.onPlacing(null);
        setMenu(null);
        setPopover(null);
        select({ kind: "none" }, []);
      }
      // Delete removes the selection (nodes, segments, a leg, an annotation)
      // -- never from inside a field, never while the deploy locks the canvas.
      if (e.key === "Delete" || e.key === "Backspace") {
        const t = e.target as HTMLElement | null;
        if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable)) return;
        const sel = edit.selection;
        if (sel.kind === "annotation") {
          e.preventDefault();
          edit.onAnnotations(edit.annotations.filter((a) => a.id !== sel.id || a.locked));
          select({ kind: "none" }, []);
          return;
        }
        if (locked) return;
        e.preventDefault();
        if (sel.kind === "node") sel.names.forEach((n) => edit.onRemoveNode(n));
        else if (sel.kind === "segment") sel.names.forEach((n) => edit.onRemoveSegment(n));
        else if (sel.kind === "leg") edit.onDetach(sel.node, sel.segment);
        else return;
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
  }, [edit, select, locked]);

  // The legs take no pointer events (a leg over a box must not catch the
  // box's drag or click), so the pane finds the leg under the pointer: the
  // nearest within 8 screen px. Hover marks it (`.hover` shows its label).
  const legAt = useCallback(
    (clientX: number, clientY: number): string | null => {
      const shell = shellRef.current;
      if (!shell) return null;
      const at = rf.screenToFlowPosition({ x: clientX, y: clientY }, { snapToGrid: false }); // the snap would move the point off the leg
      const legs: Array<{ id: string; pts: Array<{ x: number; y: number }> }> = [];
      for (const g of shell.querySelectorAll<SVGGElement>("g[data-leg]")) {
        const pts = decodePoints(g.dataset["pts"]);
        if (pts.length && g.dataset["leg"]) legs.push({ id: g.dataset["leg"], pts });
      }
      return nearestPolyline(at, legs, 8 / rf.getZoom());
    },
    [rf],
  );
  // A leg is picked where it crosses the pane, a site's tint or a bus's lane
  // (its drops run down the lane's port region): an icon or a router under the
  // pointer wins over the leg (round 2: a leg over a chip's lower half had been
  // selecting the leg's segment).
  const legUnder = useCallback(
    (target: EventTarget | null, clientX: number, clientY: number): string | null => {
      const el = target instanceof Element ? target : null;
      const node = el?.closest(".react-flow__node");
      if (node && !el?.closest('[data-testid="canvas-site"],[data-testid="canvas-segment"]')) return null;
      return legAt(clientX, clientY);
    },
    [legAt],
  );
  const hoveredLeg = useRef<string | null>(null);
  const onShellMouseMove = useCallback(
    (e: React.MouseEvent) => {
      const box = shellRef.current?.getBoundingClientRect();
      pointer.current = { x: e.clientX - (box?.left ?? 0), y: e.clientY - (box?.top ?? 0) };
      const id = legUnder(e.target, e.clientX, e.clientY);
      if (id === hoveredLeg.current) return;
      const shell = shellRef.current;
      if (hoveredLeg.current) shell?.querySelector(`g[data-leg="${CSS.escape(hoveredLeg.current)}"]`)?.classList.remove("hover");
      if (id) shell?.querySelector(`g[data-leg="${CSS.escape(id)}"]`)?.classList.add("hover");
      hoveredLeg.current = id;
      shell?.classList.toggle("over-leg", Boolean(id));
    },
    [legUnder],
  );

  // ---- rule 6 (track 4): a router's drop dragged from one bus to another re-homes the leg.
  // The legs take no pointer events, so the shell catches the press over one (by the
  // pane's hit-test), follows the pointer with a rubber line, highlights the bus under
  // it, and on release asks the page to move the attachment. The press is kept from
  // React Flow (no marquee, no pan) by stopping the native mousedown in the capture phase.
  const [legDrag, setLegDrag] = useState<{ node: string; segment: string; hue: string; from: { x: number; y: number }; to: { x: number; y: number }; over: string | null } | null>(null);
  const legDragRef = useRef<typeof legDrag>(null);
  // ---- §6.2 / §6.3: Ctrl+D (or the menu) carries a floating copy of the subtree under the cursor;
  // a press drops it -- onto a switch or router (a trunk), or on the canvas (parked where it lands,
  // re-allocated under the site it lands under); Enter parks it in place, Esc or the right button cancels.
  const [carry, setCarry] = useState<{ root: string; label: string; devices: string[]; prefixes: string[]; x: number; y: number; target: ReturnType<typeof carryTarget> | null } | null>(null);
  const carryRef = useRef<typeof carry>(null);
  carryRef.current = carry;
  const pointer = useRef<{ x: number; y: number }>({ x: 0, y: 0 });
  const startCarry = useCallback(
    (root: string) => {
      if (!edit || !edit.onDuplicateSubtree || edit.locked) return;
      const t = subtreeOrNull(edit.model, root);
      if (!t) return;
      const label = `copy of ${root} · ${subtreeCount(edit.model, t)}`;
      setCarry({ root, label, devices: t.devices, prefixes: subtreePrefixes(edit.model, root), x: pointer.current.x, y: pointer.current.y, target: null });
    },
    [edit],
  );
  const dropCarry = useCallback(
    (clientX: number | null, clientY: number | null) => {
      const c = carryRef.current;
      if (!c || !edit?.onDuplicateSubtree) return;
      setCarry(null);
      if (clientX === null || clientY === null) {
        edit.onDuplicateSubtree(c.root);
        return;
      }
      const at = rf.screenToFlowPosition({ x: clientX, y: clientY }, { snapToGrid: false });
      const t = carryTarget(rf.getNodes(), at, c.devices, c.prefixes);
      if ("attachTo" in t) edit.onDuplicateSubtree(c.root, { attachTo: t.attachTo });
      else edit.onDuplicateSubtree(c.root, { at: { x: at.x - DEVICE_W / 2, y: at.y - DEVICE_H / 2 }, prefix: t.prefix });
    },
    [edit, rf],
  );
  useEffect(() => {
    if (!carry) return;
    const move = (e: PointerEvent | MouseEvent) => {
      const shell = shellRef.current?.getBoundingClientRect();
      const at = rf.screenToFlowPosition({ x: e.clientX, y: e.clientY }, { snapToGrid: false });
      const c = carryRef.current;
      const target = c ? carryTarget(rf.getNodes(), at, c.devices, c.prefixes) : null;
      setCarry((d) => (d ? { ...d, x: e.clientX - (shell?.left ?? 0), y: e.clientY - (shell?.top ?? 0), target } : d));
    };
    const key = (e: KeyboardEvent) => {
      if (e.key === "Enter") {
        e.preventDefault();
        dropCarry(null, null);
      }
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("keydown", key);
    return () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("keydown", key);
    };
  }, [carry !== null, rf, dropCarry]); // eslint-disable-line react-hooks/exhaustive-deps
  legDragRef.current = legDrag;
  const onShellPointerDownCapture = useCallback(
    (e: React.PointerEvent) => {
      if (carryRef.current) {
        // The press drops (or, with any other button, cancels) the carried copy, and nothing under it reacts.
        e.stopPropagation();
        e.preventDefault();
        if (e.button === 0) dropCarry(e.clientX, e.clientY);
        else setCarry(null);
        return;
      }
      if (!editing || !edit || e.button !== 0) return;
      const id = legUnder(e.target, e.clientX, e.clientY);
      const parts = id ? legParts(id) : null;
      if (!parts || parts.kind !== "leg") return;
      const router = rf.getNode(ID.router(parts.node));
      const hue = router?.data.kind === "router" ? router.data.hue : "";
      const shell = shellRef.current?.getBoundingClientRect();
      const port = router ? rf.flowToScreenPosition({ x: router.position.x + (router.measured?.width ?? router.width ?? 0) / 2, y: router.position.y + (router.measured?.height ?? router.height ?? 0) }) : { x: e.clientX, y: e.clientY };
      const from = { x: port.x - (shell?.left ?? 0), y: port.y - (shell?.top ?? 0) };
      const to = { x: e.clientX - (shell?.left ?? 0), y: e.clientY - (shell?.top ?? 0) };
      setLegDrag({ node: parts.node, segment: parts.segment, hue, from, to, over: null });
      e.preventDefault();
    },
    [editing, edit, legUnder, rf, dropCarry],
  );
  const onShellMouseDownCapture = useCallback((e: React.MouseEvent) => {
    if (legDragRef.current || carryRef.current) {
      e.stopPropagation();
      e.preventDefault();
    }
  }, []);
  useEffect(() => {
    if (!legDrag) return;
    const move = (e: PointerEvent) => {
      const shell = shellRef.current?.getBoundingClientRect();
      const at = rf.screenToFlowPosition({ x: e.clientX, y: e.clientY }, { snapToGrid: false });
      const over = segmentAt(rf.getNodes(), at);
      setLegDrag((d) => (d ? { ...d, to: { x: e.clientX - (shell?.left ?? 0), y: e.clientY - (shell?.top ?? 0) }, over } : d));
    };
    const up = (e: PointerEvent) => {
      const d = legDragRef.current;
      setLegDrag(null);
      if (!d || !edit) return;
      const at = rf.screenToFlowPosition({ x: e.clientX, y: e.clientY }, { snapToGrid: false });
      const over = segmentAt(rf.getNodes(), at);
      if (over && over !== d.segment) edit.onRehome(d.node, d.segment, over);
    };
    const cancel = (e: KeyboardEvent) => {
      if (e.key === "Escape") setLegDrag(null);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    window.addEventListener("keydown", cancel);
    return () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      window.removeEventListener("keydown", cancel);
    };
  }, [legDrag !== null, rf, edit]); // eslint-disable-line react-hooks/exhaustive-deps

  // A fold opened or closed refits the view once the tree has re-laid out (R5-2): the router plus
  // the open site's row (or its rail), padded, never below 50 % zoom; a row too wide for that is
  // framed from its left edge (`canvas/fit.ts`).
  const pendingFit = useRef<{ key: string } | null>(null);
  const rfStore = useStoreApi();
  useEffect(() => {
    const want = pendingFit.current;
    if (!want) return;
    const ids = foldFitTargets(placement, want.key);
    if (!ids) return;
    const t = window.setTimeout(() => {
      pendingFit.current = null;
      const b = boundsOf(placement.nodes, ids);
      const { width, height } = rfStore.getState();
      if (b && width && height) void rf.setViewport(foldViewport(b, { width, height }, { maxZoom: FIT_MAX_ZOOM }), { duration: 300 });
    }, 60);
    return () => window.clearTimeout(t);
  }, [placement, rf, rfStore]);

  const onNodeClick = useCallback(
    (e: React.MouseEvent, node: CanvasNode) => {
      if (node.data.kind === "overflow") {
        pendingFit.current = { key: node.data.siteKey };
        onToggleSite(node.data.siteKey);
      }
      // A switch's host fold (`+N`, the per-switch host budget) opens or closes in place.
      if (node.data.kind === "hostfold") {
        onToggleSite(node.data.key);
        return;
      }
      const fold = e.target instanceof Element ? e.target.closest<HTMLElement>("[data-site-collapse]") : null;
      if (fold?.dataset["siteCollapse"]) {
        toggleFolded(fold.dataset["siteCollapse"]);
        return;
      }
      if ((node.data.kind === "site" || node.data.kind === "segment") && edit) {
        const leg = legParts(legAt(e.clientX, e.clientY) ?? "");
        if (leg) {
          select({ kind: "leg" as const, node: leg.node, segment: leg.segment }, []);
          return;
        }
      }
      // A click on the tint itself (rule 7): open or close the site's fans.
      if (node.data.kind === "site") {
        if (!legAt(e.clientX, e.clientY)) toggleFanOpen(node.data.key);
        return;
      }
      if (!edit) {
        const d = node.data;
        if (d.kind === "host") setRoSel({ kind: "host", id: d.nodeId });
        else if (d.kind === "router") setRoSel({ kind: "router", id: d.name });
        else if (d.kind === "device") setRoSel({ kind: "device", id: d.name });
        else if (d.kind === "segment") setRoSel({ kind: "segment", id: d.name });
        else return;
        setInspectorTab("selection");
        return;
      }
      const d = node.data;
      const cur = selectionRef.current;
      if (d.kind === "tap") {
        select({ kind: "tap", index: Number(d.id) }, [node.id]);
        return;
      }
      if ((d.kind === "router" || d.kind === "device") && e.altKey && editing) {
        selectSubtree(d.name);
        return;
      }
      if (d.kind === "host" || d.kind === "router" || d.kind === "device") {
        const name = d.kind === "host" ? d.nodeId : d.name;
        const names = e.shiftKey && cur.kind === "node" ? [...cur.names.filter((n) => n !== name), name] : [name];
        select({ kind: "node", names }, names.map((n) => (graph.routers.some((r) => r.name === n) ? ID.router(n) : edit.model.te.nodes.find((x) => x.name === n)?.kind === "switch" ? `device:${n}` : ID.host(n))));
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
      if (attachArmed && (node.data.kind === "host" || node.data.kind === "router" || node.data.kind === "device")) {
        const name = node.data.kind === "host" ? node.data.nodeId : node.data.name;
        // The second click of the tool on a node (not a segment) wires a link when a device is at either end (§6.4).
        if (attachFrom && attachFrom !== name && edit.onLink) {
          const kindOf = (n: string) => edit.model.te.nodes.find((x) => x.name === n)?.kind ?? "host";
          if (kindOf(attachFrom) !== "host" || kindOf(name) !== "host") {
            edit.onLink(attachFrom, name);
            setAttachFrom(null);
            return;
          }
        }
        setAttachFrom(name);
      }
    },
    [onToggleSite, toggleFolded, toggleFanOpen, edit, editing, attachArmed, attachFrom, select, selectSubtree, legAt, graph],
  );

  // A click on a link edge (the device tree, §2.3) selects it for the inspector's link row.
  const onEdgeClick = useCallback(
    (_e: React.MouseEvent, edge: Edge) => {
      const d = edge.data as { kind?: string; index?: number } | undefined;
      if (!edit || !d || d.kind !== "link" || typeof d.index !== "number") return;
      select({ kind: "link", index: d.index }, []);
    },
    [edit, select],
  );


  // A marquee selects many at once; a single pick is the click handlers' above.
  const onSelectionChange = useCallback(
    ({ nodes: sel }: { nodes: Node[]; edges: Edge[] }) => {
      if (!edit || sel.length < 2) return;
      // A subtree selection (§6.1) is many nodes by design: React Flow echoing it back (also
      // while the page re-places after a duplicate) is not a marquee. A pane click clears it first.
      if (selectionRef.current.kind === "subtree") return;
      const data = sel.map((n) => n.data as CanvasNode["data"]);
      const hosts = data.filter((d) => d.kind === "host" || d.kind === "router" || d.kind === "device").map((d) => (d.kind === "host" ? d.nodeId : d.kind === "router" || d.kind === "device" ? d.name : ""));
      const segs = data.filter((d) => d.kind === "segment").map((d) => (d.kind === "segment" ? d.name : ""));
      const next: Selection = hosts.length ? { kind: "node", names: hosts } : segs.length ? { kind: "segment", names: segs } : { kind: "none" };
      if (JSON.stringify(next) !== JSON.stringify(selectionRef.current)) edit.onSelection(next);
    },
    [edit],
  );

  // A marquee that took a switch and only devices below it is that switch's subtree (§6.1).
  const onSelectionEnd = useCallback(() => {
    if (!edit) return;
    const picked = rf.getNodes().filter((n) => n.selected).flatMap((n) => (n.data.kind === "device" || n.data.kind === "router" ? [n.data.name] : []));
    const root = marqueeSubtree(edit.model, picked);
    if (root) selectSubtree(root);
  }, [edit, rf, selectSubtree]);

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

  const onNodeContextMenu = useCallback(
    (e: React.MouseEvent, node: CanvasNode) => {
      if (node.data.kind !== "site" || !edit) return;
      const leg = legParts(legAt(e.clientX, e.clientY) ?? "");
      if (leg) onContextMenu(e, { kind: "leg" as const, node: leg.node, segment: leg.segment });
    },
    [edit, legAt, onContextMenu],
  );

  const onPaneContextMenu = useCallback(
    (e: MouseEvent | React.MouseEvent) => {
      e.preventDefault();
      const box = shellRef.current?.getBoundingClientRect();
      const leg = legParts(legAt(e.clientX, e.clientY) ?? "");
      const at = rf.screenToFlowPosition({ x: e.clientX, y: e.clientY });
      setMenu({ x: e.clientX - (box?.left ?? 0), y: e.clientY - (box?.top ?? 0), target: leg ? { kind: "leg" as const, node: leg.node, segment: leg.segment } : { kind: "pane", at } });
    },
    [rf, legAt],
  );

  // Double-click a segment (or a site) to zoom to it: past the detail
  // threshold the tile expands to its chips.
  // Member chips (§5): open by a double-click, and for every `×N` group past `MEMBERS_ZOOM`
  // (re-rendered only on the crossing); a double-click past it closes one.
  const [membersOpened, setMembersOpened] = useState<ReadonlySet<string>>(() => new Set());
  const [membersClosed, setMembersClosed] = useState<ReadonlySet<string>>(() => new Set());
  const chipsZoom = useStore((st) => st.transform[2] >= MEMBERS_ZOOM);
  const teNodes = edit?.model.te.nodes;
  const membersOpen = useMemo<ReadonlySet<string>>(() => {
    if (!chipsZoom || !teNodes) return membersOpened;
    return new Set([...membersOpened, ...teNodes.filter((n) => n.countValue > 1 && !membersClosed.has(n.name)).map((n) => n.name)]);
  }, [chipsZoom, teNodes, membersOpened, membersClosed]);
  const toggleMembers = useCallback(
    (name: string) => {
      const shown = membersOpen.has(name);
      const flip = (s: ReadonlySet<string>, on: boolean) => { const n = new Set(s); if (on) n.add(name); else n.delete(name); return n; };
      setMembersOpened((s) => flip(s, !shown));
      setMembersClosed((s) => flip(s, shown));
    },
    [membersOpen],
  );
  // Esc folds every open strip back to its `×N` (R4-10), the ones the zoom opened included.
  useEffect(() => {
    const down = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || membersOpen.size === 0) return;
      setMembersOpened(new Set());
      setMembersClosed((s) => new Set([...s, ...membersOpen]));
    };
    window.addEventListener("keydown", down);
    return () => window.removeEventListener("keydown", down);
  }, [membersOpen]);
  const onNodeDoubleClick = useCallback(
    (_e: React.MouseEvent, node: CanvasNode) => {
      // R3-8: a double-click anywhere on a `×N` group's icon (not only its badge) opens its member chips.
      if (node.data.kind === "host" && edit) {
        const name = node.data.nodeId;
        const group = edit.model.te.nodes.find((n) => n.name === name);
        if (group && group.countValue > 1) toggleMembers(name);
        return;
      }
      if (node.data.kind !== "segment" && node.data.kind !== "site") return;
      setZoomTarget(node.id);
      void rf.fitView({ nodes: [{ id: node.id }], duration: 300, maxZoom: 1.5, padding: 0.15 });
    },
    [rf, edit, toggleMembers],
  );

  // The edges are controlled, so the selected leg's flag is derived here.
  // The legs are structure and always drawn; an arc is evidence, drawn only
  // while lit by the overlay or touching the selection.
  const selectedLegs = edit?.selection.kind === "leg" ? [`leg:${edit.selection.node}>${edit.selection.segment}`, `stub:${edit.selection.node}>${edit.selection.segment}`] : edit?.selection.kind === "link" ? [`link:${edit.selection.index}`] : [];
  const selectedNodes = useMemo(() => nodes.filter((n) => n.selected), [nodes]);
  const scope = useMemo(() => arcScope(selectedNodes, graph, exportData, placement.anchorByName), [selectedNodes, graph, exportData, placement.anchorByName]);
  // Rule 7: a selected router opens every fan it has; a click on a site's tint opens that site's.
  const fansOpen = useMemo(() => {
    const open = new Set(fanOpen);
    for (const n of selectedNodes) if (n.data.kind === "router") open.add(n.data.name);
    return open;
  }, [fanOpen, selectedNodes]);
  const edges = useMemo(() => {
    // The tree folds per carrier (`placeTree`'s `fan`); the bus plane's fan collapse is not applied to its legs.
    const structural = placement.edges.filter((e) => e.type === "leg" && e.data?.kind === "leg");
    void fanOut;
    void fansOpen;
    const others = placement.edges.flatMap((e) => {
      if (e.type === "leg" && e.data?.kind === "leg") return [];
      if (e.type === "arc") return e.data?.kind === "arc" && arcShown(e.data.key, scope, glow.arcs) ? [e] : [];
      return [selectedLegs.includes(e.id) ? { ...e, selected: true } : e];
    });
    return [...others, ...structural.map((e) => (selectedLegs.includes(e.id) ? { ...e, selected: true } : e))];
  }, [placement.edges, selectedLegs.join(","), scope, glow.arcs, fanN, fansOpen]); // eslint-disable-line react-hooks/exhaustive-deps

  const rehomeTarget = legDrag?.over ?? null;
  // Members (§5): which `×N` groups show their chips (per page), the chip gestures.
  const onToggleMembers = toggleMembers;
  const onSelectMember = useCallback((name: string, index: number) => { if (edit) { edit.onSelection({ kind: "member", node: name, index }); setInspectorTab("selection"); } }, [edit]);
  const onMoveMember = useCallback((from: string, index: number, into: string) => edit?.onMoveMember?.(from, index, into), [edit]);
  const editState = useMemo<EditState | null>(() => (edit ? { model: edit.model, locked, attachArmed, attachFrom, placing: edit.placing, rehomeTarget, onContextMenu, membersOpen, onToggleMembers, onSelectMember, onMoveMember } : null), [edit, locked, attachArmed, attachFrom, rehomeTarget, onContextMenu, membersOpen, onToggleMembers, onSelectMember, onMoveMember]);

  const align = useCallback(
    (axis: "h" | "v") => {
      const current = rf.getNodes();
      // A bus is ranked, not placed: only hosts and routers align.
      const sel = current.filter((n) => n.selected && pinKeyOf(n) && n.data.kind !== "segment");
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
    (kind: AnnotationKind, where?: { x: number; y: number }) => {
      if (!edit) return;
      const v = rf.getViewport();
      const shell = document.querySelector<HTMLElement>(".canvas-shell");
      const w = shell?.clientWidth || 800;
      const h = shell?.clientHeight || 500;
      const at = where ? { x: Math.round(where.x), y: Math.round(where.y) } : { x: Math.round((-v.x + w / 2) / v.zoom), y: Math.round((-v.y + h / 2) / v.zoom) };
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
    if (placement.siteSegments) parts.push(`${placement.siteSegments} segments in closed site folds`);
    if (placement.hiddenPairs) parts.push(`${placement.hiddenPairs} observed pairs not drawn (the top ${placement.edges.filter((e) => e.type === "arc").length} by volume are)`);
    return parts;
  }, [placement]);

  // The rail's shortcuts: only when the keyboard is on the canvas, never in a field.
  useEffect(() => {
    const down = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable)) return;
      // Ctrl+D duplicates the selected subtree (or the selected device's), parked (§6.2).
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "d" && edit?.onDuplicateSubtree && !edit.locked) {
        const sel = selectionRef.current;
        const root = sel.kind === "subtree" ? sel.root : sel.kind === "node" && sel.names.length === 1 ? sel.names[0]! : null;
        if (root) {
          e.preventDefault();
          startCarry(root);
        }
        return;
      }
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      switch (e.key) {
        case "v": setTool("select"); break;
        case "h": setTool("pan"); break;
        case "f": void rf.fitView({ duration: 300, maxZoom: FIT_MAX_ZOOM }); break;
        case "+": case "=": void rf.zoomIn({ duration: 200 }); break;
        case "-": void rf.zoomOut({ duration: 200 }); break;
        case "l": if (!edit?.locked) setLockedAll((l) => !l); break;
        case "d": setDetailMode(nextMode); break;
        case "n": setLabels(nextLabels); break;
        case "o": if (provenanceOverlay) setOverlay((o) => !o); break;
        case "/": e.preventDefault(); togglePopover("find"); break;
        case "t": togglePopover("traffic"); break;
        case "a": if (edit) togglePopover("palette"); break;
        default: return;
      }
    };
    window.addEventListener("keydown", down);
    return () => window.removeEventListener("keydown", down);
  }, [rf, edit, locked, togglePopover, provenanceOverlay, startCarry]);

  const centreOnListed = useCallback((id: string, kind: "host" | "router") => centreOn(kind === "router" ? ID.router(id) : ID.host(id)), [centreOn]);

  const viewTools: RailButton[] = [
    { name: "Select", key: "V", icon: <Icon.select />, pressed: tool === "select", onClick: () => setTool("select") },
    { name: "Pan", key: "H, or hold Space", icon: <Icon.pan />, pressed: tool === "pan" || spaceHeld, onClick: () => setTool("pan") },
    { name: "Zoom in", key: "+", icon: <Icon.zoomIn />, onClick: () => void rf.zoomIn({ duration: 200 }) },
    { name: "Zoom out", key: "-", icon: <Icon.zoomOut />, onClick: () => void rf.zoomOut({ duration: 200 }) },
    { name: "Zoom to fit", key: "F", icon: <Icon.fit />, onClick: () => void rf.fitView({ duration: 300, maxZoom: FIT_MAX_ZOOM }) },
    { name: lockedAll ? "Unlock all" : "Lock all", key: "L", icon: lockedAll ? <Icon.lock /> : <Icon.unlock />, pressed: lockedAll, disabled: Boolean(edit?.locked), onClick: () => setLockedAll((l) => !l) },
    { name: `Node detail: ${detailMode}`, key: "D", icon: detailMode === "auto" ? <Icon.detailAuto /> : detailMode === "full" ? <Icon.detailFull /> : <Icon.detailCompact />, pressed: detailMode !== "auto", onClick: () => setDetailMode(nextMode), testId: "rail-detail" },
    { name: `Labels: ${labels}`, key: "N", icon: Icon.labelsMode(labels), pressed: labels !== "selected", onClick: () => setLabels(nextLabels), testId: "rail-labels" },
    { name: `Fan-out: ${fanLabel(fanN)}`, icon: <Icon.fan />, pressed: fanN !== FAN_DEFAULT, onClick: () => setFanN(nextFan), testId: "rail-fan" },
    ...(provenanceOverlay ? [{ name: overlay ? "Provenance overlay: on" : "Provenance overlay: off", key: "O", icon: <Icon.overlay />, pressed: overlay, onClick: () => setOverlay((o) => !o), testId: "rail-overlay" }] : []),
    ...(lab ? [{ name: labOn ? "Lab overlay: on" : "Lab overlay: off", icon: <Icon.lab />, pressed: labOn, onClick: () => setLabOn((v) => !v), testId: "rail-lab" }] : []),
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

  // Fit ONCE, when the first nodes are measured, unless the session kept a
  // viewport; never on an edit (2026-11-05: the view jumped after a placement).
  // Zoom to fit (F) is the explicit way.
  const viewportProps = initialViewport ? { defaultViewport: initialViewport } : {};
  const nodesReady = useNodesInitialized();
  const fitted = useRef(Boolean(initialViewport));
  useEffect(() => {
    if (nodesReady && !fitted.current) {
      fitted.current = true;
      void rf.fitView({ maxZoom: FIT_MAX_ZOOM });
    }
  }, [nodesReady, rf]);

  // Compact keeps the host icons: on the device tree they are the switches' leaves (2026-11-11),
  // bounded by the per-switch host budget; only their captions go (`canvas.css`).
  const shownNodes = useMemo(() => {
    const foldedSites = new Set(folded);
    const hiddenIds = new Set<string>();
    return nodes.map((n) => {
      let out = n;
      if (n.data.kind === "site" && foldedSites.has(n.data.key)) out = { ...out, width: SITE_FOLDED_W, height: SITE_FOLDED_H, data: { ...n.data, collapsed: true } };
      // A parent comes before its children in the placement's order.
      if (n.parentId && (foldedSites.has(n.parentId) || hiddenIds.has(n.parentId))) {
        hiddenIds.add(n.id);
        out = { ...out, hidden: true };
      }
      return out;
    });
  }, [nodes, detail, folded]);

  // The tree plane draws a leg as the straight line between its two boxes
  // (`LegEdge`'s fallback); the gutter routing of the bus plane is gone with it.

  const menuItems = useMemo((): Array<{ label: string; run(): void; danger?: boolean }> => {
    if (!menu) return [];
    const t = menu.target;
    const items: Array<{ label: string; run(): void; danger?: boolean }> = [];
    if (t.kind === "pane") {
      items.push({ label: "Zoom to fit", run: () => void rf.fitView({ duration: 300, maxZoom: FIT_MAX_ZOOM }) });
      items.push({ label: lockedAll ? "Unlock all" : "Lock all", run: () => setLockedAll((l) => !l) });
      items.push({ label: "Find a node…", run: () => setPopover("find") });
      if (edit && editing) {
        if (edit.placing && placesWithoutSegment(edit.placing) && edit.placing.kind !== "segment" && edit.placing.kind !== "persona") items.push({ label: `Place ${edit.placing.label} here`, run: () => edit.onPlace(edit.placing!, null, edit.placing!.count ?? 1, t.at) });
        items.push({ label: "Add a segment", run: () => edit.onAddSegment(segmentAt(rf.getNodes(), t.at) ?? undefined) });
        items.push({ label: "Annotate here (text)", run: () => addAnnotation("text", t.at) });
        items.push({ label: "Annotate here (rect)", run: () => addAnnotation("rect", t.at) });
      }
      return items;
    }
    if (!edit) return [];
    if (t.kind === "host" || t.kind === "router") {
      const group = edit.model.te.nodes.find((n) => n.name === t.name);
      if (group && (group.kind === "switch" || group.kind === "router")) items.push({ label: "Select subtree", run: () => selectSubtree(t.name) });
      if (group && (group.kind === "switch" || group.kind === "router") && edit.onDuplicateSubtree) items.push({ label: "Duplicate subtree (Ctrl+D)", run: () => startCarry(t.name) });
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
      items.push({ label: "Add a segment in this site", run: () => edit.onAddSegment(t.name) });
      items.push({ label: `Remove segment ${t.name}`, run: () => edit.onRemoveSegment(t.name), danger: true });
    }
    return items;
  }, [menu, edit, editing, attachArmed, attachFrom, lockedAll, rf, addAnnotation, startCarry]);

  return (
    <GlowContext.Provider value={glow}>
      <EditContext.Provider value={editState}>
      <DetailContext.Provider value={detail}>
      <LabelsContext.Provider value={labels}>
      <OverlayContext.Provider value={overlay}>
      <ProvFilterContext.Provider value={provFilter}>
        <div
          ref={shellRef}
          onMouseMove={onShellMouseMove}
          onPointerDownCapture={onShellPointerDownCapture}
          onMouseDownCapture={onShellMouseDownCapture}
          className={`canvas-shell${editing ? " editing" : ""}${attachArmed ? " attaching" : ""}${connecting ? " connecting" : ""}${detail === "compact" ? " compact" : ""}${legDrag ? " rehoming" : ""}${carry ? " carrying" : ""}${overlay ? " overlay" : ""}`}
          data-testid="canvas"
          data-overlay={overlay ? "on" : "off"}
          data-zoom-target={zoomTarget ?? undefined}
          data-detail={detail}
          data-detail-mode={detailMode}
          data-panning={tool === "pan" || spaceHeld ? "true" : "false"}
          data-locked={locked ? "true" : "false"}
          data-color-mode={colorMode}
          data-mode={edit ? (locked ? "view" : "edit") : "readonly"}
          onClick={() => setMenu(null)}
        >
          {lab && labOn ? (
            <LabOverlay
              lab={lab}
              taps={shownNodes.filter((n) => n.data.kind === "tap").map((n) => ({ nodeId: n.id, sensors: (n.data as { sensors?: string[] }).sensors ?? [] }))}
              version={shownNodes}
            />
          ) : null}
          <ReactFlow<CanvasNode>
            nodes={shownNodes}
            edges={edges}
            onNodesChange={onNodesChange}
            nodeTypes={nodeTypes}
            edgeTypes={edgeTypes}
            colorMode={colorMode}
            nodesDraggable={!locked}
            nodesConnectable={editing}
            elementsSelectable
            panOnDrag={tool === "pan" || spaceHeld ? true : [1]}  // select mode: middle button pans; the right button is the pane menu
            selectionOnDrag={tool === "select" && !spaceHeld}
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
            onSelectionEnd={onSelectionEnd}
            onConnect={onConnect}
            onConnectStart={() => setConnecting(true)}
            onConnectEnd={() => setConnecting(false)}
            onDrop={onDrop}
            onDragOver={(e) => {
              if (editing) {
                e.preventDefault();
                e.dataTransfer.dropEffect = "copy";
              }
            }}
            onPaneClick={(e) => {
              setMenu(null);
              // An armed router lands where the pane is clicked, with no leg yet.
              if (editing && edit?.placing && placesWithoutSegment(edit.placing) && edit.placing.kind !== "segment" && edit.placing.kind !== "persona") {
                edit.onPlace(edit.placing, null, edit.placing.count ?? 1, rf.screenToFlowPosition({ x: e.clientX, y: e.clientY }));
                return;
              }
              const leg = legParts(legAt(e.clientX, e.clientY) ?? "");
              select(leg ? { kind: "leg" as const, node: leg.node, segment: leg.segment } : { kind: "none" }, []);
            }}
            onNodeContextMenu={onNodeContextMenu}
            onPaneContextMenu={onPaneContextMenu}
            onNodeDoubleClick={onNodeDoubleClick}
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
                selection={inspector ? inspector.selection : <ReadOnlySelection graph={graph} sel={roSel} placement={placement} />}
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
            {layoutBar ? (
              <Panel position="bottom-center" className="cv-chrome-layout">
                {layoutBar}
              </Panel>
            ) : null}
          </ReactFlow>
          {legDrag ? (
            <svg className="cv-rubber" aria-hidden="true" data-testid="rehome-rubber" data-over={legDrag.over ?? undefined}>
              <line x1={legDrag.from.x} y1={legDrag.from.y} x2={legDrag.to.x} y2={legDrag.to.y} style={{ stroke: legDrag.hue || "var(--accent)" }} />
              <circle cx={legDrag.to.x} cy={legDrag.to.y} r={4} style={{ fill: legDrag.hue || "var(--accent)" }} />
            </svg>
          ) : null}
          {legDrag ? <div className="cv-rehome-hint muted" role="status">{legDrag.over && legDrag.over !== legDrag.segment ? `drop: ${legDrag.node} ${legDrag.segment} → ${legDrag.over}` : `drag ${legDrag.node}'s ${legDrag.segment} port onto another bus · Esc cancels`}</div> : null}
          {carry ? (
            <div className="cv-carry-ghost" role="status" data-testid="carry-ghost" data-target={carry.target ? ("attachTo" in carry.target ? `device:${carry.target.attachTo}` : `park:${carry.target.prefix ?? ""}`) : undefined} style={{ left: carry.x + 14, top: carry.y + 14 }}>
              <strong>{carry.label}</strong>
              <span>{carry.target && "attachTo" in carry.target ? `drop: trunk it from ${carry.target.attachTo}` : carry.target && carry.target.prefix ? `drop: park it here, re-allocated under ${carry.target.prefix}` : "drop: park it here (not attached) · or on a switch or router"}</span>
              <span className="muted">Enter parks it in place · Esc cancels</span>
            </div>
          ) : null}
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
      </ProvFilterContext.Provider>
      </OverlayContext.Provider>
      </LabelsContext.Provider>
      </DetailContext.Provider>
      </EditContext.Provider>
    </GlowContext.Provider>
  );
}

/** The inspector's Selection tab without an editor (a cast or authored
 *  twin): the clicked host's or segment's facts, read from the graph. */
function ReadOnlySelection({ graph, sel, placement }: { graph: ConsoleGraph; sel: { kind: "host" | "router" | "device" | "segment"; id: string } | null; placement: Placement }) {
  if (!sel) return <p className="muted">click a host, a router, a switch or a segment</p>;
  if (sel.kind === "device") {
    const drawn = placement.nodes.find((n) => n.id === `device:${sel.id}`)?.data;
    const dd = drawn?.kind === "device" ? drawn : null;
    return (
      <div className="cv-ro" data-testid="ro-device">
        <h3 className="mono">{dd ? <span className="cv-hue-swatch" style={{ background: dd.hue }} aria-hidden="true" /> : null}{sel.id}</h3>
        <dl className="facts">
          <dt>kind</dt><dd>{dd?.deviceKind ?? "switch"}{dd?.role ? ` · ${dd.role}` : ""}</dd>
          <dt>ports</dt><dd className="mono">{dd?.ports.join(", ") || "none"}</dd>
          <dt>segments</dt><dd className="mono">{dd?.segments.join(", ") || "none"}</dd>
          <dt>provenance</dt><dd>{dd?.source ?? "authored"}</dd>
        </dl>
      </div>
    );
  }
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
    const drawn = placement.nodes.find((n) => n.id === ID.router(sel.id))?.data;
    const rd = drawn?.kind === "router" ? drawn : null;
    return (
      <div className="cv-ro" data-testid="ro-router">
        <h3 className="mono">{rd ? <span className="cv-hue-swatch" style={{ background: rd.hue }} aria-hidden="true" /> : null}{sel.id}</h3>
        <dl className="facts">
          <dt>ports</dt><dd>{r ? r.segments.join(", ") : "none"}</dd>
          {rd?.uplink || rd?.crossing ? <><dt>WAN</dt><dd>{rd.uplink ? "the config's uplink" : "the gateway by observed crossing traffic"}</dd></> : null}
          {rd?.crossing ? <><dt>crossing</dt><dd data-testid="ro-crossing">{crossingText(rd.crossing)}</dd></> : null}
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
