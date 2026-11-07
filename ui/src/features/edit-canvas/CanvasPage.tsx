/**
 * The `features/edit-canvas` route: `/canvas/:id` (the Canvas area), also
 * reachable as `/twins/:id/canvas` from a twin's page.
 *
 * Reads the twin (summary and tree), its graph -- the cast topology export
 * when the twin has one, else the authored scenario topology -- the layout
 * side-car, the deploys standing on it, and the document the editor works
 * on: a deployed twin's server-side WORKING COPY (A2) or the undeployed
 * config's own files (A3). Edit mode when the twin is undeployed or its
 * working copy is open; view mode (locked except annotations) while its
 * deploy is running. The canvas draws the document, so every edit shows at
 * once; the evidence view draws what the API observed.
 *
 * Exits (`api/Spec.md`, "The edit model"): an undeployed config has Save and
 * Save-As; a deployed twin has Apply (only when every pending change the API
 * classified is live), Reset, Persist and Save-As, and a topology change
 * offers "redeploy from baseline". Every mutation is validated here first
 * and, on a deployed twin, by the API on the PUT of the working copy, whose
 * refusal reverts the edit and is shown verbatim. A cast twin's role change
 * or segment merge can be promoted to an assertion through the imprint's
 * seam, which re-casts and re-opens the working copy.
 */
import type { Viewport } from "@xyflow/react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";

import { ApiError } from "../../api/client";
import { useWhoami } from "../../api/whoami";
import { TopbarPortal } from "../../areas/shell/topbar";
import { useApi } from "../../api/context";
import type { CastRoster, DeployConfig, DeploySummary, LayoutAnnotation, LayoutDoc, LayoutWrite, Persona, Topology, TopologyExport, TwinConfigDetail, TwinSummary, TwinTree, ValidateResult } from "../../api/types";
import { ErrorLine, errorText } from "../../areas/shell/parts";
import { useLoad } from "../../areas/shell/useLoad";
import { TwinTree as TwinTreeView } from "../../areas/twins/TwinTree";
import { Canvas, type CanvasEditProps } from "./Canvas";
import { authoredGraph, EMPTY_TREE, treeStructure, type TreeStructure } from "./canvas/authored";
import { TREE_FAN_DEFAULT, placeTree } from "./canvas/tree";
import { castInput } from "./canvas/cast";
import { absolutePosition, pinKeyOf } from "./canvas/placement";
import { CANVAS_PLANE } from "./canvas/plane";
import { nextFreeSlash24 } from "./edit/allocate";
import { clearLegacyAnnotations, fromLayoutList, readLegacyAnnotations, toLayout } from "./edit/annotations";
import { sameFiles, type Files, type Stack } from "./edit/commands";
import { addSegment, attach, detach, documentGraph, moveTo, placeNodes, readDoc, rehome, removeNode, removeSegment, saveAsFiles, setPersona, type DocModel, type WorkingDoc, moveTap } from "./edit/document";
import { observedSegments } from "./canvas/evidence";
import type { PaletteItem } from "./edit/EditContext";
import { useEditor } from "./edit/useEditor";
import { layoutWrite, pinsOf, useLayoutDraft } from "./edit/useLayoutDraft";
import { matrix0 } from "./layout/matrix";
import { buildGraph, type ConsoleGraph } from "./model/graph";
import { twoOctetPrefix } from "./model/keys";
import { Palette } from "./Palette";
import { moveMember } from "./edit/members";
import { Checks, SidePanel, checksBadge, type ProvenanceAction, type ProvenanceSubject, type Selection } from "./SidePanel";

interface Loaded {
  twin: TwinSummary;
  tree: TwinTree | null;
  exportData: TopologyExport | null;
  topology: Topology | null;
  /** The cast's rosters: each node group's observed member keys and each segment's observed
   *  CIDR, what a promote-to-assertion is keyed by. Null when the twin was not cast. */
  cast: CastRoster | null;
  /** The twin's standing `te` deploy, newest first, or null when undeployed. */
  deploy: DeploySummary | null;
  /** Every deploy row the API listed: the tier rows (`tfc-<te>`, `tap-<te>`) stand beside the te one. */
  deploys: DeploySummary[];
  /** The deploy's working copy (A2), when deployed. */
  config: DeployConfig | null;
  /** The catalog entry with its document (A3): the baseline. Null when the catalog has no document for this id. */
  detail: TwinConfigDetail | null;
  /** The palette's sources: every twin-config's detail the API could serve, and the personas. */
  catalog: TwinConfigDetail[];
  personas: Persona[];
  paletteNote: string | null;
}

const SYNC_DELAY_MS = 250;
const VALIDATE_DELAY_MS = 300;
const POLL_MS = 5000;
/** While an exit is in flight the poll runs at this pace, for the progress state. */
const BUSY_POLL_MS = 1000;
/** A note about a finished exit fades after this; an error stays until the next exit. */
const NOTE_MS = 8000;
const CATALOG_DETAIL_LIMIT = 12;

/** The body for one more pin over the document's: the pins of this plane plus
 *  the moved element's new absolute top-left, the annotations carried through. */
export function nextLayoutWrite(current: LayoutDoc | null, id: string, key: string, at: { x: number; y: number }): LayoutWrite {
  return layoutWrite(current, id, { ...pinsOf(current), [key]: at }, current?.annotations ?? []);
}

/** The body for a new annotation list over the document's pins of this plane. */
export function annotationsWrite(current: LayoutDoc | null, id: string, annotations: readonly LayoutAnnotation[]): LayoutWrite {
  return { ...layoutWrite(current, id, pinsOf(current), annotations), annotations: [...annotations] };
}

/** The latest document validate (`POST /api/validate {document}`), tied to the files it judged. */
export interface DocValidation {
  files: Files;
  result: ValidateResult | null;
  error: unknown;
}

/** The twin's standing deploy: its newest `te` deploy that is running or completed. */
export function standingDeploy(deploys: readonly DeploySummary[], twinId: string): DeploySummary | null {
  const mine = deploys.filter((d) => d.scenario_id === twinId && (d.tier ?? "te") === "te" && (d.status === "running" || d.status === "completed"));
  mine.sort((a, b) => b.started_at - a.started_at);
  return mine[0] ?? null;
}

/** The tier playouts standing on a te deploy, from the deploy list: a tier row's
 *  `scenario_id` is `<tier>-<te deploy id>`; the newest row per tier that is not
 *  destroyed, as `tier -> status`. Pure. */
export function tierStatus(rows: readonly DeploySummary[], teDeployId: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const tier of ["tfc", "tap"]) {
    const mine = rows.filter((d) => d.scenario_id === `${tier}-${teDeployId}` && d.status !== "destroyed");
    mine.sort((a, b) => b.started_at - a.started_at);
    if (mine[0]) out[tier] = mine[0].status;
  }
  return out;
}

/** A tier's state in the pill: a check for the common case, the word when it
 *  matters (failed, running, anything else), so the bar stays one row. Pure. */
export function tierWord(status: string): string {
  return status === "completed" ? "✓" : status === "failed" ? "✗ failed" : status === "running" ? "⟳ running" : status;
}

/** The working copy's state word (`SPEC.md` "Shell and chrome"): `dirty` alone
 *  read as "not applied" to an operator, when it means "differs from the
 *  baseline". Two states instead: the copy's changes were applied and are not
 *  yet persisted, or the copy carries changes not applied. Pure. */
export function docState(config: Pick<DeployConfig, "dirty" | "applied" | "updated_at"> | null, editorDirty: boolean, unsynced: boolean): "clean" | "syncing" | "applied" | "unapplied" {
  if (unsynced) return "syncing";
  if (!(config?.dirty || editorDirty)) return "clean";
  if (config?.applied && config.updated_at && config.applied.at >= config.updated_at) return "applied";
  return "unapplied";
}

/** The words an exit's refusal is shown with: a 409 names its code (`busy`,
 *  `cancelled`, `requires-replan`, ...) rather than the stale tier it mentions. */
export function exitErrorText(e: unknown): string {
  if (e instanceof ApiError && e.status === 409 && e.detail && typeof e.detail === "object") {
    const code = (e.detail as { code?: unknown }).code;
    if (code === "busy") return `busy: ${e.message}`;
    if (code === "cancelled") return `cancelled: ${e.message}`;
  }
  return errorText(e);
}

/** What the palette lists: a segment, the node groups and images every
 *  twin-config in the catalog declares (this twin's first), the personas. */
export function paletteItems(catalog: readonly TwinConfigDetail[], personas: readonly Persona[], ownId: string): PaletteItem[] {
  const items: PaletteItem[] = [{ kind: "segment", id: "segment", label: "Segment", summary: "a new L2 segment with the next free /24" }];
  const images = new Set<string>();
  const ordered = [...catalog].sort((a, b) => (a.id === ownId ? -1 : b.id === ownId ? 1 : a.id.localeCompare(b.id)));
  // A router joins segments: it places anywhere, with the image the catalog's
  // routers use (this twin's first), and gets its legs by attaching.
  const routerImage = ordered.flatMap((d) => d.nodes).find((n) => n.kind === "router" && n.image)?.image ?? null;
  items.push({ kind: "router", id: "router", label: "Router", image: routerImage ?? "router", summary: routerImage ? `a router with image ${routerImage}; attach it to each segment it joins` : "a router; no twin in the catalog names a router image, so pick one in the inspector" });
  for (const d of ordered) {
    for (const n of d.nodes) {
      const count = typeof n.count === "number" ? n.count : typeof n.count === "string" && /^\d+$/.test(n.count) ? Number(n.count) : typeof n.count === "string" ? (Number(/:(\d+)\}/.exec(n.count)?.[1]) || 1) : 1;
      items.push({ kind: "group", id: `${d.id}/${n.name}`, label: d.id === ownId ? n.name : `${n.name} (${d.id})`, nodeKind: n.kind === "router" ? "router" : n.kind === "switch" ? "switch" : "host", role: n.role ?? null, image: n.image ?? "", count, summary: `${n.kind} · ${n.image ?? "no image"}${n.role ? ` · ${n.role}` : ""}`, foreign: d.id !== ownId });
      if (n.image) images.add(n.image);
    }
  }
  for (const image of [...images].sort()) items.push({ kind: "image", id: image, label: image, available: true, summary: `image ${image}, named by a twin in the catalog` });
  for (const p of personas) items.push({ kind: "persona", id: p.id, label: p.id, os: p.os_family, summary: p.summary });
  return items;
}

function viewportKey(id: string): string {
  return `axr-canvas-viewport:${id}`;
}

/** The command stack kept in this browser per document -- the deploy's working
 *  copy, or the catalog twin when nothing is deployed -- and restored only when
 *  it still fits the text loaded: the last command's after-text (or, with
 *  everything undone, the next redo's before-text) must be what the server
 *  holds, path for path. A copy changed meanwhile (another console, a reset)
 *  fails that and drops the stack. (2026-11-06 round 2: the `updated_at` key
 *  alone did not catch every case and left undeployed twins out.) */
function undoKey(scope: string): string {
  return `axr-canvas-undo:${scope}`;
}

/** The stack describes `files` iff the text it ends at is the text loaded. */
export function stackFits(stack: Stack, files: Files): boolean {
  const last = stack.done[stack.done.length - 1];
  const next = stack.undone[stack.undone.length - 1];
  const end = last ? last.after : next ? next.before : null;
  if (!end) return false;
  return Object.entries(end).every(([path, text]) => files[path] === text);
}

export function readStoredStack(scope: string, files: Files | null): Stack | null {
  if (!files) return null;
  try {
    const raw = localStorage.getItem(undoKey(scope));
    if (!raw) return null;
    const v = JSON.parse(raw) as { stack?: Partial<Stack> };
    if (!v.stack || !Array.isArray(v.stack.done) || !Array.isArray(v.stack.undone)) return null;
    const stack = { done: v.stack.done, undone: v.stack.undone };
    return stackFits(stack, files) ? stack : null;
  } catch {
    return null;
  }
}

export function writeStoredStack(scope: string, stack: Stack): void {
  try {
    if (stack.done.length === 0 && stack.undone.length === 0) localStorage.removeItem(undoKey(scope));
    else localStorage.setItem(undoKey(scope), JSON.stringify({ stack }));
  } catch {
    // no storage: the stack lives for the page only
  }
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

type View = "design" | "evidence";

export function CanvasPage() {
  const api = useApi();
  const whoami = useWhoami();
  const navigate = useNavigate();
  const { id = "" } = useParams();
  const { data, error, loading, reload } = useLoad<Loaded>(async (signal) => {
    const twin = await api.getTwin(id, signal);
    const tree = await api.getTwinTree(id, signal).catch(() => null);
    let exportData: TopologyExport | null = null;
    let topology: Topology | null = null;
    try {
      exportData = await api.getTwinTopology(id, signal);
    } catch (e) {
      if (!(e instanceof ApiError && e.kind === "not-found")) throw e;
      topology = await api.getScenarioTopology(id, signal).catch(() => null);
    }
    const cast = twin.pit_id ? await api.getCast(id, signal).catch(() => null) : null;
    const deploys = await api.listDeploys(signal).catch(() => ({ deploys: [], warnings: [] }));
    const deploy = standingDeploy(deploys.deploys, id);
    const rows = deploys.deploys;
    const config = deploy ? await api.getDeployConfig(deploy.deploy_id, signal) : null;
    let detail: TwinConfigDetail | null = null;
    try {
      detail = await api.getTwinConfig(id, signal);
    } catch (e) {
      if (!(e instanceof ApiError && e.kind === "not-found")) throw e;
    }
    const paletteNotes: string[] = [];
    const list = await api.listTwinConfigs(signal).catch((e: unknown) => {
      paletteNotes.push(`twin-configs not listed: ${errorText(e)}`);
      return { twin_configs: [] };
    });
    const others = list.twin_configs.filter((c) => c.id !== id && !c.error).slice(0, CATALOG_DETAIL_LIMIT);
    const details = await Promise.all(others.map((c) => api.getTwinConfig(c.id, signal).catch(() => null)));
    const catalog = [...(detail ? [detail] : []), ...details.filter((d): d is TwinConfigDetail => d !== null)];
    const personaList = await api.listPersonas(signal).catch((e: unknown) => {
      paletteNotes.push(`personas not listed: ${errorText(e)}`);
      return { twin: null, personas: [], warnings: [] };
    });
    return { twin, tree, exportData, topology, cast, deploy, deploys: rows, config, detail, catalog, personas: personaList.personas, paletteNote: paletteNotes.join("; ") || null };
  }, [id]);
  const layout = useLoad<LayoutDoc>((signal) => api.getLayout(id, signal), [id]);

  // ---- positions and annotations: the layout side-car as a draft (Phase 1) ----
  // Moves and annotation changes stay on the canvas; the side-car is written
  // only by "Save layout" (or alongside a document Save / Persist).
  const draft = useLayoutDraft(api, id, layout.data);
  const { pins, annotations, onPin, onPinMany, setAnnotations } = draft;
  const layoutChanges = draft.changes;
  const saveLayout = draft.save;
  const [expanded, setExpanded] = useState<string[]>([]);
  const [initialViewport] = useState(() => readViewport(id));

  // ---- the editor (Phase 2) ----
  const editor = useEditor();
  const [config, setConfig] = useState<DeployConfig | null>(null);
  const [detail, setDetail] = useState<TwinConfigDetail | null>(null);
  const [view, setView] = useState<View>("design");
  const [selection, setSelection] = useState<Selection>({ kind: "none" });
  const [placing, setPlacing] = useState<PaletteItem | null>(null);
  const [copies, setCopies] = useState(1);
  const [blocked, setBlocked] = useState<string | null>(null);
  const [serverReason, setServerReason] = useState<string | null>(null);
  const [exitError, setExitError] = useState<unknown>(null);
  const [exitNote, setExitNote] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [deployRows, setDeployRows] = useState<DeploySummary[]>([]);
  const [saveAsId, setSaveAsId] = useState("");
  const [validation, setValidation] = useState<DocValidation | null>(null);
  const [validating, setValidating] = useState(false);
  const synced = useRef<Files | null>(null);
  const deployed = Boolean(data?.deploy);
  const deployId = data?.deploy?.deploy_id ?? null;
  const locked = data?.deploy?.status === "running";

  useEffect(() => {
    if (!data) return;
    setConfig(data.config);
    setDetail(data.detail);
    setDeployRows(data.deploys);
    const document = data.config?.document ?? data.detail?.document ?? null;
    if (document) {
      editor.load({ id: document.id, teFile: document.te_file, files: document.files });
      synced.current = { ...document.files };
      // The same text as this browser last edited: its Undo comes back.
      const stored = readStoredStack(data.config ? data.config.deploy_id : `twin:${id}`, document.files);
      if (stored) editor.restoreStack(stored);
    }
    setSelection({ kind: "none" });
    setServerReason(null);
    setBlocked(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, id]);

  // A set an earlier build kept in this browser joins the canvas once (as
  // unsaved changes, for the next "Save layout") and is cleared from the browser.
  const migrated = useRef<string | null>(null);
  useEffect(() => {
    if (!layout.data || migrated.current === id) return;
    migrated.current = id;
    const legacy = readLegacyAnnotations(id);
    if (!legacy.length) return;
    const merged = [...fromLayoutList(layout.data.annotations), ...legacy.filter((a) => !layout.data!.annotations.some((x) => x.id === a.id))];
    setAnnotations(merged);
    clearLegacyAnnotations(id);
  }, [layout.data, id, setAnnotations]);

  // Every mutation is validated by the API through the document route after a
  // short quiet spell; the exits are enabled only on a clean answer for the
  // files as they stand. The structural check stays the instant hint.
  useEffect(() => {
    if (!editor.files || !editor.doc) return;
    const files = editor.files;
    const docId = editor.doc.id;
    const ctl = new AbortController();
    setValidating(true);
    const t = setTimeout(() => {
      api.validateDocument({ id: docId, files }, ctl.signal).then(
        (result) => {
          if (ctl.signal.aborted) return;
          setValidation({ files, result, error: null });
          setValidating(false);
        },
        (e: unknown) => {
          if (ctl.signal.aborted) return;
          setValidation({ files, result: null, error: e });
          setValidating(false);
        },
      );
    }, VALIDATE_DELAY_MS);
    return () => {
      clearTimeout(t);
      ctl.abort();
    };
  }, [editor.files, editor.doc, api]);

  // One mutation: the editor validates and records it; the reason it was blocked is shown in place.
  const edit = useCallback(
    (label: string, fn: (d: WorkingDoc, m: DocModel) => Files): string | null => {
      if (locked) {
        setBlocked("view mode: the deploy is running");
        return "view mode";
      }
      const reason = editor.apply(label, fn);
      setBlocked(reason);
      if (!reason) setServerReason(null);
      return reason;
    },
    [editor, locked],
  );

  // A deployed twin's edits sync to the working copy (A2) after a short quiet
  // spell; the API validates and classifies, and a refusal reverts the edit.
  const syncQueue = useRef<Promise<void>>(Promise.resolve());
  useEffect(() => {
    if (!deployed || !deployId || locked || !editor.files) return;
    const files = editor.files;
    if (synced.current && sameFiles(files, synced.current)) return;
    const t = setTimeout(() => {
      syncQueue.current = syncQueue.current.then(async () => {
        try {
          const cfg = await api.putDeployConfig(deployId, files);
          synced.current = { ...files };
          setConfig(cfg);
          editor.markSaved(files);
          setServerReason(null);
        } catch (e) {
          setServerReason(errorText(e));
          if (synced.current) editor.undoUntil(synced.current);
        }
      });
    }, SYNC_DELAY_MS);
    return () => clearTimeout(t);
  }, [editor.files, deployed, deployId, locked, api, editor]);

  // Another console's edits or reset show up here (C3.6): a poll, and a Refresh
  // button. While an exit is in flight the poll runs faster and carries the
  // exit's progress (`config.exit`); the tier rows ride along for the pill.
  const refresh = useCallback(async () => {
    if (!deployId) return;
    try {
      const cfg = await api.getDeployConfig(deployId);
      setConfig((prev) => {
        const sameExit = JSON.stringify(prev?.exit ?? null) === JSON.stringify(cfg.exit ?? null);
        if (prev && sameExit && prev.updated_at === cfg.updated_at && prev.dirty === cfg.dirty && prev.has_working_copy === cfg.has_working_copy) return prev;
        return cfg;
      });
      const rows = await api.listDeploys().catch(() => null);
      if (rows) setDeployRows((prev) => (JSON.stringify(prev) === JSON.stringify(rows.deploys) ? prev : rows.deploys));
      const local = editor.files;
      const unsynced = local && synced.current && !sameFiles(local, synced.current);
      if (!unsynced && (!synced.current || !sameFiles(cfg.document.files, synced.current))) {
        editor.load({ id: cfg.document.id, teFile: cfg.document.te_file, files: cfg.document.files });
        synced.current = { ...cfg.document.files };
      }
    } catch (e) {
      setExitError(e);
    }
  }, [api, deployId, editor]);
  const inFlight = Boolean(busy) || Boolean(config?.exit);
  useEffect(() => {
    if (!deployId) return;
    const t = setInterval(() => void refresh(), inFlight ? BUSY_POLL_MS : POLL_MS);
    return () => clearInterval(t);
  }, [deployId, refresh, inFlight]);

  // A finished exit's note fades; an error stays until the next exit.
  useEffect(() => {
    if (!exitNote) return;
    const t = setTimeout(() => setExitNote(null), NOTE_MS);
    return () => clearTimeout(t);
  }, [exitNote]);

  // The stack is stored once the text is at rest on the server -- synced to
  // the working copy, or saved to the catalog -- so the stored stack describes
  // exactly the text a reload will get.
  useEffect(() => {
    if (!editor.files) return;
    const atRest = deployed ? Boolean(synced.current && sameFiles(editor.files, synced.current)) : !editor.dirty;
    if (!atRest) return;
    writeStoredStack(deployId ?? `twin:${id}`, editor.stack);
  }, [deployed, deployId, id, editor.files, editor.dirty, editor.stack]);

  // Ctrl+Z / Ctrl+Y over the command stack, when no field has the focus.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!(e.ctrlKey || e.metaKey)) return;
      const target = e.target as HTMLElement | null;
      if (target && /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName)) return;
      const k = e.key.toLowerCase();
      if (k === "z" && !e.shiftKey) {
        e.preventDefault();
        editor.undo();
      } else if (k === "y" || (k === "z" && e.shiftKey)) {
        e.preventDefault();
        editor.redo();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [editor]);

  // ---- what is drawn ----
  const model = editor.model;
  const designAvailable = model !== null;
  // The document reaches the editor one effect after the load lands: until it
  // does, nothing is drawn, so the design view never flashes the evidence one.
  const docPending = Boolean(data && (data.config?.document ?? data.detail?.document)) && (!model || editor.doc?.id !== (data?.config?.document ?? data?.detail?.document)?.id);
  const built = useMemo<{ kind: "authored" | "cast" | "design"; graph: ConsoleGraph; exportData: TopologyExport | null; extraLegs: ReturnType<typeof authoredGraph>["extraLegs"]; uplinks: string[]; tree: TreeStructure } | null>(() => {
    if (!data || docPending) return null;
    if (model && (view === "design" || !data.exportData)) {
      const g = documentGraph(model, id);
      return { kind: "design", graph: buildGraph(g.exportData, g.cast), exportData: null, extraLegs: g.extraLegs, uplinks: g.uplinks, tree: g.tree };
    }
    if (data.exportData) {
      // A cast twin's Internet is its evidence (non-local peers); the config's uplink marker is the authored path's.
      // Its switches and links come from the expanded config's topology when the API serves one.
      return { kind: "cast", graph: buildGraph(data.exportData, castInput(id, data.tree, data.twin)), exportData: data.exportData, extraLegs: [], uplinks: [], tree: data.topology ? treeStructure(data.topology) : EMPTY_TREE };
    }
    if (data.topology) {
      const a = authoredGraph(data.topology, data.tree);
      return { kind: "authored", graph: buildGraph(a.exportData, a.cast), exportData: null, extraLegs: a.extraLegs, uplinks: a.uplinks, tree: a.tree };
    }
    return null;
  }, [data, id, model, view, docPending]);
  // The rail's Fan-out (§7.6): how many pills a carrier shows before folding the rest.
  const [fan, setFan] = useState<number>(TREE_FAN_DEFAULT);

  // In the design view a site's computed position and a bus's RANK in its site
  // SETTLE for the session: a later mutation places only what has no position
  // yet, so adding a node never shuffles its neighbours and a new segment
  // lands at the end of its site (2026-11-05: every add/remove re-placed the
  // segments and lost the drag). A drag's pin wins over a settled position;
  // Discard returns to the settled one.
  const settled = useRef<Record<string, { x: number; y: number }>>({});
  useEffect(() => {
    settled.current = {};
  }, [id]);
  const placement = useMemo(() => {
    if (!built) return null;
    const design = built.kind === "design";
    const taps = design && model?.tap ? model.tap.bindings.map((b) => ({ index: b.index, scope: b.scope, targets: b.targets, segment: b.segment, sensors: b.sensors, source: b.source })) : [];
    const p = placeTree(built.graph, design ? { ...settled.current, ...pins } : pins, { expanded, extraLegs: built.extraLegs, uplinks: built.uplinks, tree: built.tree, fan, taps });
    if (design && !p.overBudget) {
      // The tree's own positions settle for the session (a pill, a device, a
      // router): a later mutation places only what has no position yet.
      for (const n of p.nodes) {
        const key = pinKeyOf(n);
        if (!key || key in settled.current || n.parentId) continue;
        const abs = absolutePosition(p.nodes, n.id);
        if (abs) settled.current[key] = abs;
      }
    }
    return p;
  }, [built, pins, expanded, fan, model?.tap]);

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

  // ---- the palette's placement ----
  const items = useMemo(() => (data ? paletteItems(data.catalog, data.personas, id) : []), [data, id]);
  // Rule 6 (track 4): "Add segment" inserts the new bus under the selected site -- the
  // site of the segment it was asked beside, else of the selection, else of the first segment.
  const selectionRef = useRef<Selection>(selection);
  selectionRef.current = selection;
  const onAddSegment = useCallback((near?: string) => {
    edit("add segment", (d, m) => {
      const inPlay = m.te.segments.map((s) => s.cidr);
      const sel = selectionRef.current;
      const nearName = near ?? (sel.kind === "segment" ? sel.names[0] : sel.kind === "node" ? m.te.nodes.find((n) => n.name === sel.names[0])?.nets[0]?.segment : sel.kind === "leg" ? sel.segment : undefined);
      const nearSeg = nearName ? m.te.segments.find((s) => s.name === nearName) : undefined;
      const prefix = (nearSeg ? twoOctetPrefix(nearSeg.cidr) : null) ?? (m.te.segments[0] ? twoOctetPrefix(m.te.segments[0].cidr) : null) ?? "10.200";
      // The next /24 AFTER the highest in use under the prefix (.10, .20 -> .21), not the
      // first free one (.0, the site's own network address, legal but surprising).
      const cidr = nextFreeSlash24(prefix, inPlay, { after: true }) ?? nextFreeSlash24("10.200", inPlay);
      if (!cidr) throw new Error(`no free /24 under ${prefix}`);
      const taken = new Set(m.te.segments.map((s) => s.name));
      let n = m.te.segments.length + 1;
      while (taken.has(`segment-${n}`)) n++;
      return addSegment(d, { name: `segment-${n}`, cidr, zoneType: "vlan" });
    });
  }, [edit]);
  const onPlace = useCallback(
    (item: PaletteItem, segment: string | null, count: number, at: { x: number; y: number } | null) => {
      setPlacing(null);
      if (item.kind === "segment") {
        onAddSegment();
        return;
      }
      if (item.kind === "persona") {
        if (selection.kind !== "node") {
          setBlocked("select a node group first, then pick a persona");
          return;
        }
        const names = selection.names;
        edit(`persona ${item.id} on ${names.join(", ")}`, (d) => names.reduce((files, n) => setPersona({ ...d, files }, readDoc({ ...d, files }), "node", n, { persona: item.id }), d.files));
        return;
      }
      const nodeKind = item.kind === "router" || item.nodeKind === "router" ? "router" : "host";
      if (!segment && nodeKind !== "router") {
        setBlocked(`${item.label} needs a segment: arm it, then click a segment (or drag it onto one)`);
        return;
      }
      const name = item.kind === "group" ? item.id.split("/").pop()! : item.id.replace(/@.*$/, "").replace(/[^A-Za-z0-9._-]+/g, "-");
      let placed: string[] = [];
      const reason = edit(`place ${item.label} ×${count}${segment ? ` on ${segment}` : ""}`, (d, m) => {
        const r = placeNodes(d, m, { name, count, image: item.image ?? item.id, role: item.role ?? null, kind: nodeKind, segment, fanOut: item.kind === "group" && item.count && item.count > 1 ? item.count : undefined });
        placed = r.names;
        return r.files;
      });
      if (!reason && at && placed[0]) onPin(`host:${placed[0]}`, { x: Math.round(at.x), y: Math.round(at.y) });
    },
    [edit, onAddSegment, onPin, selection],
  );

  // ---- exits ----
  const files = editor.files;
  const dirty = deployed ? Boolean(config?.dirty) || editor.dirty : editor.dirty;
  const structurallyValid = model ? !model.findings.some((f) => f.severity === "error") : false;
  // The gate: the API's latest answer, for exactly the files on screen, with no error.
  const apiValid = Boolean(files && validation && validation.result && validation.result.errors === 0 && sameFiles(validation.files, files));
  const valid = structurallyValid && apiValid;
  const unsynced = deployed && Boolean(files && synced.current && !sameFiles(files, synced.current));
  const topologyPending = (config?.changes ?? []).some((c) => c.kind === "topology");
  const run = useCallback(
    async (what: string, fn: () => Promise<string | null>) => {
      setBusy(what);
      setExitError(null);
      setExitNote(null);
      try {
        const note = await fn();
        setExitNote(note);
      } catch (e) {
        setExitError(e);
      } finally {
        setBusy(null);
      }
    },
    [],
  );
  const save = () =>
    run("save", async () => {
      if (!files) return null;
      const d = await api.putTwinConfig(id, { id, files });
      setDetail(d);
      editor.markSaved(files);
      const layoutSaved = layoutChanges > 0 ? await saveLayout() : null;
      return layoutSaved === null ? "saved" : layoutSaved ? "saved, layout too" : "saved; the layout was not (see above)";
    });
  const saveAs = () =>
    run("save-as", async () => {
      if (!editor.doc) return null;
      const newId = saveAsId.trim();
      if (!newId) throw new Error("give the new twin-config an id");
      await api.createTwinConfig({ id: newId, files: saveAsFiles(editor.doc, newId) });
      // The layout as it stands on screen -- the saved pins AND the unsaved moves
      // and annotations -- goes with the new twin, so Save-As drops no draft.
      let carried = true;
      if (draft.doc || layoutChanges > 0) {
        try {
          await api.putLayout(newId, layoutWrite(null, newId, pins, annotations.map(toLayout)));
          draft.discard();
        } catch {
          carried = false;
        }
      }
      setSaveAsId("");
      navigate(`/canvas/${encodeURIComponent(newId)}`);
      return `saved as ${newId}${layoutChanges > 0 ? (carried ? ", layout draft carried over" : "; the layout draft could not be written there") : ""}`;
    });
  const applyExit = () =>
    run("apply", async () => {
      if (!deployId) return null;
      const r = await api.applyDeployConfig(deployId);
      setConfig(r.config);
      return `applied${r.applied.length ? ` via ${r.applied.join(", ")}` : ""}${Object.keys(r.roles).length ? ` · roles ${Object.entries(r.roles).map(([n, ro]) => `${n} → ${ro}`).join(", ")}` : ""}`;
    });
  const resetExit = (tiers?: string[]) =>
    run(tiers ? `re-apply ${tiers.join(", ")}` : "reset", async () => {
      if (!deployId) return null;
      const r = await api.resetDeployConfig(deployId, tiers ? { tiers } : undefined);
      setConfig(r.config);
      editor.load({ id: r.config.document.id, teFile: r.config.document.te_file, files: r.config.document.files });
      synced.current = { ...r.config.document.files };
      void refresh();
      const failed = r.replay_failed ?? [];
      if (failed.length) {
        // The copy is gone; the twin still runs what it applied to these tiers. Not an error: say so and offer the retry.
        setExitError(new Error(`${r.discarded ? "the working copy is discarded, but " : ""}the baseline replay of ${failed.join(", ")} failed: ${r.warnings.join("; ")}`));
        return null;
      }
      return `${tiers ? `re-applied ${r.reverted.join(", ") || "nothing"} from the baseline` : "reset to baseline"}${!tiers && r.reverted.length ? ` · reverted ${r.reverted.join(", ")}` : ""}`;
    });
  const cancelExit = () => {
    if (!deployId) return;
    api.cancelDeployConfigExit(deployId).then(
      (c) => setExitNote(`cancel requested: ${c.kind} stops after the op in flight${c.tier ? ` (${c.tier})` : ""}`),
      (e: unknown) => setExitError(e),
    );
  };
  const persist = () =>
    run("persist", async () => {
      if (!deployId || !files) return null;
      await api.putTwinConfig(id, { id, files });
      const cfg = await api.getDeployConfig(deployId);
      setConfig(cfg);
      setDetail(await api.getTwinConfig(id).catch(() => null));
      editor.load({ id: cfg.document.id, teFile: cfg.document.te_file, files: cfg.document.files });
      synced.current = { ...cfg.document.files };
      if (layoutChanges > 0) await saveLayout();
      return "persisted: the baseline is the working copy";
    });

  // ---- promote to assertion (a cast twin only) ----
  const pit = data?.twin.pit_id ?? null;
  const promote = useCallback(
    (records: { override?: Record<string, unknown>[]; suppress?: Record<string, unknown>[]; add?: Record<string, unknown>[]; confirm?: Record<string, unknown>[]; doubt?: Record<string, unknown>[] }, what: string) =>
      run("promote", async () => {
        if (!pit) throw new Error("this twin was not cast from an imprint; there is nothing to assert on");
        const current = await api.getAssertions(pit);
        const same = (a: Record<string, unknown>, b: Record<string, unknown>) => a["key"] === b["key"] && a["field"] === b["field"];
        const byKey = (a: Record<string, unknown>, b: Record<string, unknown>) => a["key"] === b["key"];
        const merge = (have: Record<string, unknown>[] | undefined, want: Record<string, unknown>[] | undefined, eq: (a: Record<string, unknown>, b: Record<string, unknown>) => boolean) => [...(have ?? []).filter((o) => !(want ?? []).some((r) => eq(r, o))), ...(want ?? [])];
        const override = merge(current.override, records.override, same);
        const suppress = merge(current.suppress, records.suppress, byKey);
        const add = merge(current.add, records.add, byKey);
        // A confirm lifts the same key out of doubt and the other way round (§3.2: the two blocks are exclusive per key).
        const confirm = merge(current.confirm, records.confirm, byKey).filter((c) => !(records.doubt ?? []).some((d) => byKey(d, c)));
        const doubt = merge(current.doubt, records.doubt, byKey).filter((d) => !(records.confirm ?? []).some((c) => byKey(c, d)));
        const body: Record<string, unknown> = {
          schema: "axr-assertions/2",
          imprint: current.imprint,
          as_of: current.as_of ?? new Date().toISOString().replace(/\.\d{3}Z$/, "Z"),
          source: current.source ?? "operator:canvas",
          add,
          override,
          suppress,
        };
        if (confirm.length) body["confirm"] = confirm;
        if (doubt.length) body["doubt"] = doubt;
        if (current.etag) body["etag"] = current.etag;
        await api.putAssertions(pit, body);
        await api.castImprint(pit, { twin_id: id, replace: true });
        // Re-open the working copy over the re-cast baseline: the edit is now the baseline's.
        if (deployId && files) await api.putDeployConfig(deployId, files).catch(() => null);
        reload();
        return `promoted: ${what}; the twin was re-cast and the working copy re-opened`;
      }),
    [api, pit, id, deployId, files, reload, run],
  );
  // A twin-config node NAME is a cast group name (`role-band-segment-rung`) and a segment's
  // `cidr` the deployed (possibly relocated) network; neither is a key the assertions seam
  // resolves. The cast roster carries what is: each group's observed member keys and each
  // segment's observed CIDR. A node or segment the roster cannot key is not promotable.
  const cast = data?.cast ?? null;
  const membersOf = useCallback((node: string): string[] => cast?.node_roster.find((n) => n.name === node)?.members ?? [], [cast]);
  const observedCidrOf = useCallback((segment: string): string | null => cast?.segment_roster.find((s) => s.name === segment)?.observed_cidr ?? null, [cast]);
  const promoteBlock = useCallback(
    (what: { node: string } | { from: string; to: string }): string | null => {
      if (pit === null) return "this twin was not cast from an imprint; there is nothing to assert on";
      if (!cast) return "the cast roster could not be read; nothing to key an assertion by";
      if ("node" in what) {
        return membersOf(what.node).length ? null : `${what.node} has no observed members in the cast (added by hand, synthetic, or an older cast); nothing to assert on`;
      }
      if (observedCidrOf(what.from) === null || observedCidrOf(what.to) === null) return `${what.from} or ${what.to} has no observed network in the cast; nothing to assert on`;
      const hosts = model?.te.nodes.filter((n) => n.nets.some((l) => l.segment === what.from)) ?? [];
      return hosts.some((n) => membersOf(n.name).length) ? null : `no host on ${what.from} has observed members in the cast; nothing to assert on`;
    },
    [pit, cast, membersOf, observedCidrOf, model],
  );
  const onPromoteRole = useCallback(
    (node: string, role: string) => {
      const note = `promoted from the design canvas: ${node} is a ${role}`;
      const override = membersOf(node).map((key) => ({ key, field: "role", value: role, note }));
      void promote({ override, suppress: [] }, `${node} is a ${role}`);
    },
    [membersOf, promote],
  );
  const onPromoteMerge = useCallback(
    (from: string, to: string) => {
      if (!model) return;
      const fromCidr = observedCidrOf(from);
      const toCidr = observedCidrOf(to);
      if (fromCidr === null || toCidr === null) return;
      const note = `promoted from the design canvas: ${from} and ${to} are one segment`;
      const hosts = model.te.nodes.filter((n) => n.nets.some((l) => l.segment === from)).flatMap((n) => membersOf(n.name).map((key) => ({ key, field: "segment", value: toCidr, note })));
      void promote({ override: hosts, suppress: [{ key: fromCidr, note }] }, `${from} is ${to}`);
    },
    [model, observedCidrOf, membersOf, promote],
  );

  // ---- the Provenance block (topology-layers §3.2, §3.3): confirm / doubt / assert through the seam ----
  // A device's key is its one observed member (a switch or router the cast keyed); a link's key is
  // the two ports on those keys; a segment's its observed CIDR; a tap's `segment:<cidr>` or `port:<key>#<port>`.
  const deviceKey = useCallback((node: string): string | null => membersOf(node)[0] ?? null, [membersOf]);
  const linkKey = useCallback(
    (index: number): string | null => {
      const l = model?.te.links[index];
      if (!l) return null;
      const ka = deviceKey(l.a.device);
      const kb = deviceKey(l.b.device);
      if (!ka || !kb) return null;
      return [`${ka}#${l.a.port}`, `${kb}#${l.b.port}`].sort().join("|");
    },
    [model, deviceKey],
  );
  const tapKey = useCallback(
    (index: number): string | null => {
      const b = model?.tap?.bindings[index];
      if (!b) return null;
      if (b.scope === "segment") {
        const cidr = observedCidrOf(b.targets[0] ?? "");
        return cidr ? `segment:${cidr}` : null;
      }
      if (b.scope === "port" && b.segment) {
        const k = deviceKey(b.targets[0] ?? "");
        return k ? `port:${k}#${b.segment}` : null;
      }
      return null;
    },
    [model, observedCidrOf, deviceKey],
  );
  const provenanceBlock = useCallback(
    (subject: ProvenanceSubject): string | null => {
      if (pit === null) return "this twin was not cast from an imprint; there is nothing to confirm or doubt";
      if (!cast) return "the cast roster could not be read; nothing to key an assertion by";
      if ("node" in subject) return membersOf(subject.node).length ? null : `${subject.node} has no observed members in the cast; nothing to key it by`;
      if ("segment" in subject) return observedCidrOf(subject.segment) ? null : `${subject.segment} has no observed network in the cast; nothing to key it by`;
      if ("link" in subject) return linkKey(subject.link) ? null : "one end of this link is a device the cast did not key; nothing to key the link by";
      return tapKey(subject.tap) ? null : "this tap sits on nothing the cast keyed (a segment's observed network or a device port); nothing to key it by";
    },
    [pit, cast, membersOf, observedCidrOf, linkKey, tapKey],
  );
  const onProvenance = useCallback(
    (action: ProvenanceAction) => {
      if (!model) return;
      const note = "from the design canvas";
      if (action.kind === "confirm" || action.kind === "doubt") {
        const sub = action.subject;
        const keys = "node" in sub ? membersOf(sub.node) : "segment" in sub ? [observedCidrOf(sub.segment)] : "link" in sub ? [linkKey(sub.link)] : [];
        const marks = keys.filter((k): k is string => Boolean(k)).map((key) => ({ key, note }));
        if (!marks.length) return;
        const what = "node" in sub ? sub.node : "segment" in sub ? sub.segment : "link" in sub ? `link ${sub.link}` : `tap ${sub.tap}`;
        void promote(action.kind === "confirm" ? { confirm: marks } : { doubt: marks }, `${action.kind === "confirm" ? "confirmed" : "marked unconfirmed"}: ${what}`);
        return;
      }
      if (action.kind === "assert-device") {
        const override = membersOf(action.node).map((key) => ({ key, field: "device_kind", value: action.deviceKind, note: `${note}: ${action.node} is a ${action.deviceKind}` }));
        void promote({ override }, `${action.node} is a ${action.deviceKind}`);
        return;
      }
      if (action.kind === "assert-link") {
        const l = model.te.links[action.index];
        const key = linkKey(action.index);
        if (!l || !key) return;
        const vlans = l.vlans.map((v) => observedCidrOf(v)).filter((c): c is string => Boolean(c));
        const native = l.native ? observedCidrOf(l.native) : null;
        const rec: Record<string, unknown> = { kind: "link", key, mode: l.mode, note: `${note}: ${l.a.device}#${l.a.port} — ${l.b.device}#${l.b.port}` };
        if (vlans.length) rec["vlans"] = vlans;
        if (native) rec["native"] = native;
        if (l.state !== "up") rec["state"] = l.state;
        void promote({ add: [rec] }, `link ${l.a.device}#${l.a.port} — ${l.b.device}#${l.b.port}`);
        return;
      }
      if (action.kind !== "assert-tap") return;
      const b = model.tap?.bindings[action.index];
      const key = tapKey(action.index);
      if (!b || !key) return;
      void promote({ add: [{ kind: "tap", key, sensors: b.sensors, note: `${note}: tap on ${b.scope} ${b.targets.join(" ")}` }] }, `tap on ${b.targets.join(" ")}`);
    },
    [model, membersOf, observedCidrOf, linkKey, tapKey, promote],
  );
  const [provFilter, setProvFilter] = useState<string | null>(null);

  const baselineModel = useMemo<DocModel | null>(() => (detail?.document ? readDoc({ id: detail.document.id, teFile: detail.document.te_file, files: detail.document.files }) : null), [detail]);
  // One pending change is one file's delta from the baseline: discarding it puts
  // that file back to the baseline's text, as an edit (undoable, validated, synced).
  const baselineFiles = detail?.document?.files ?? null;
  const onDiscardChange = useCallback(
    (path: string) => {
      if (!baselineFiles) return;
      edit(`discard changes to ${path}`, (d) => {
        const files = { ...d.files };
        const base = baselineFiles[path];
        if (base === undefined) delete files[path];
        else files[path] = base;
        return files;
      });
    },
    [edit, baselineFiles],
  );

  const notes = useMemo(() => {
    const out: string[] = [];
    if (layout.error) out.push(`positions are not saved for this twin: ${errorText(layout.error)}`);
    else if (draft.doc && draft.doc.exists && draft.doc.plane !== CANVAS_PLANE) out.push(`the saved positions were drawn on another plane (${draft.doc.plane ?? "unstamped"}) and are not applied; the next Save layout saves on this one`);
    if (built?.kind === "authored") out.push("authored twin: no observed evidence, no traffic to overlay");
    if (built?.kind === "design") out.push(deployed ? "design view: the working copy" : "design view: the twin config");
    if (built && built.graph.castOmits.length) out.push(`the cast did not publish: ${built.graph.castOmits.join(", ")}`);
    if (data && !designAvailable) out.push(whoami.can_write ? "read-only: the catalog serves no document for this twin" : "read-only: the API serves this twin's document only to a caller that may write; sign in to edit it");
    if (annotations.length) out.push(`${annotations.length} annotation${annotations.length === 1 ? "" : "s"} in the layout side-car`);
    return out;
  }, [layout.error, draft.doc, built, data, designAvailable, deployed, annotations.length, whoami.can_write]);

  const editProps = useMemo<CanvasEditProps | undefined>(() => {
    if (!model || built?.kind !== "design") return undefined;
    return {
      model,
      locked: Boolean(locked),
      selection,
      onSelection: setSelection,
      placing,
      onPlacing: setPlacing,
      onPlace,
      onAttach: (node, segment) => void edit(`attach ${node} to ${segment}`, (d, m) => attach(d, m, node, segment)),
      onMove: (node, segment) => void edit(`move ${node} to ${segment}`, (d, m) => moveTo(d, m, node, segment)),
      onDetach: (node, segment) => void edit(`detach ${node} from ${segment}`, (d, m) => detach(d, m, node, segment)),
      onRehome: (node, from, to) => void edit(`re-home ${node}: ${from} → ${to}`, (d, m) => rehome(d, m, node, from, to)),
      onRemoveNode: (name) => void edit(`remove ${name}`, (d, m) => removeNode(d, m, name)),
      onRemoveSegment: (name) => void edit(`remove segment ${name}`, (d, m) => removeSegment(d, m, name)),
      onAddSegment,
      onMoveTap: (index, segment) => void edit(`move tap ${index} to ${segment}`, (d, m) => moveTap(d, m, index, segment)),
      onMoveMember: (from, index, into) => void edit(`move ${from}${String(index).padStart(2, "0")} to ${into}`, (d, m) => moveMember(d, m, from, index, into)),
      annotations,
      onAnnotations: setAnnotations,
    };
  }, [model, built, locked, selection, placing, onPlace, edit, onAddSegment, annotations, setAnnotations]);

  const mode = !designAvailable ? "read-only" : locked ? "view" : "edit";
  const overBudget = Boolean(built && placement && placement.overBudget);
  // The per-tier state: the te row from the load, the tier rows from the poll.
  const tiers = useMemo(() => (data?.deploy ? tierStatus(deployRows, data.deploy.deploy_id) : {}), [deployRows, data?.deploy]);
  const tierFailed = Object.entries(tiers).filter(([, st]) => st === "failed").map(([t]) => t);
  const anyFailed = data?.deploy?.status === "failed" || tierFailed.length > 0;
  const state = docState(config, editor.dirty, unsynced);
  const exit = config?.exit ?? null;
  const exitWord = exit ? `${exit.kind === "reset" ? "resetting" : "applying"}… ${exit.tier ? `replaying ${exit.tier}` : "starting"} · ${Math.round(exit.elapsed_s)} s${exit.cancel_requested ? " · cancel requested" : ""}` : busy ? `${busy}…` : null;
  // The layout group floats on the canvas, not in the twin bar (SPEC.md "Shell and chrome"): the bar holds the document's exits only.
  const layoutBar = layout.data ? (
    <span className="row cv-layout cv-float" role="group" aria-label="Layout" data-testid="layout-bar">
      {layoutChanges > 0 ? (
        <span className="cv-dirty" data-testid="layout-dirty" title="moves and annotations are written only by Save layout">
          ● layout: {layoutChanges} unsaved {layoutChanges === 1 ? "change" : "changes"}
        </span>
      ) : draft.savedAt ? (
        <span className="muted" role="status">
          layout saved
        </span>
      ) : (
        <span className="muted">layout</span>
      )}
      <button type="button" className="primary" onClick={() => void saveLayout()} disabled={layoutChanges === 0 || draft.saving} title="write the positions and annotations to the layout side-car">
        {draft.saving ? "Saving…" : "Save layout"}
      </button>
      <button type="button" onClick={draft.discard} disabled={layoutChanges === 0 || draft.saving} title="back to the layout as last saved">
        Discard
      </button>
    </span>
  ) : null;

  // The canvas is the page: the title, the pills and the exits live in the
  // one top bar (`TopbarPortal`); messages float as a strip over the drawing;
  // the drawing itself fills the window.
  return (
    <div className={`canvas-page${editProps ? " editing" : ""}${overBudget ? " fallback" : ""}`}>
      <TopbarPortal>
        <h1 className="cv-title">
          <Link to={`/twins/${encodeURIComponent(id)}`} title={`Twin ${id}: the summary page`}>
            {id}
          </Link>
        </h1>
        {/* One pill: the origin, the mode and the deploy's state PER TIER (te / tfc / tap), red when a tier failed
            -- the te tier alone read "completed" over a failed traffic playout. The bar must not wrap at 1622 px. */}
        {built || data ? (
          <span className={`badge cv-pill${mode === "read-only" ? " read-only" : ""}${anyFailed ? " failed" : ""}`} title={data?.deploy ? `deploy ${data.deploy.deploy_id}: te ${data.deploy.status}${Object.entries(tiers).map(([t, st]) => ` · ${t} ${st}`).join("")}` : "no deploy stands on this twin"} data-testid="state-pill">
            {built ? <span>{built.kind === "cast" ? "cast · observed evidence" : built.kind === "authored" ? "authored" : deployed ? "working copy" : "twin config"}</span> : null}
            {built && data ? " · " : null}
            {data ? (
              <span data-testid="mode">
                {mode}
                {data.deploy ? (
                  <>
                    {" · "}
                    <span className="mono cv-pill-id" title={data.deploy.deploy_id}>{data.deploy.deploy_id}</span>
                    <span className={`cv-tier${data.deploy.status === "failed" ? " failed" : ""}`} data-testid="tier-te" data-status={data.deploy.status} title={`te ${data.deploy.status}`}> · te {tierWord(data.deploy.status)}</span>
                    {Object.entries(tiers).map(([t, st]) => (
                      <span key={t} className={`cv-tier${st === "failed" ? " failed" : ""}`} data-testid={`tier-${t}`} data-status={st} title={`${t} ${st}`}> · {t} {tierWord(st)}</span>
                    ))}
                  </>
                ) : (
                  " · undeployed"
                )}
              </span>
            ) : null}
          </span>
        ) : null}
        {data?.exportData && designAvailable ? (
          <span className="row cv-view" role="group" aria-label="View">
            <button type="button" aria-pressed={view === "design"} onClick={() => setView("design")}>
              Design
            </button>
            <button type="button" aria-pressed={view === "evidence"} onClick={() => setView("evidence")}>
              Evidence
            </button>
          </span>
        ) : null}
        <span className="topbar-spacer" />
        {designAvailable && data ? (
          <div className="cv-exits" data-testid="exits">
            {/* The document's state word: the two deployed states say whether the changes reached the twin, not only that they differ from the baseline. */}
            {dirty ? (
              <span className={`cv-dirty${state === "applied" ? " applied" : ""}`} data-testid="dirty-marker" data-state={state} title={deployed ? (state === "applied" ? "every change in the working copy was applied to the running twin; the baseline does not have them yet (Persist)" : state === "syncing" ? "the edit is on its way to the working copy" : "the working copy carries changes the twin does not run yet (Apply)") : "unsaved changes"}>
                ● {deployed ? (state === "syncing" ? "editing… (syncing to the working copy)" : state === "applied" ? "applied · unpersisted" : "unapplied changes") : "unsaved changes"}
              </span>
            ) : (
              <span className="cv-clean" data-testid="clean-marker" data-state="clean">
                {deployed ? "working copy equals the baseline" : "saved"}
              </span>
            )}
            <span className="cv-exit-group" role="group" aria-label="History">
              <button type="button" className="cv-iconbtn" aria-label="Undo" onClick={editor.undo} disabled={!editor.canUndo || Boolean(locked)} title={editor.lastLabel ? `undo: ${editor.lastLabel}` : "nothing to undo"}>
                ↶
              </button>
              <button type="button" className="cv-iconbtn" aria-label="Redo" onClick={editor.redo} disabled={!editor.canRedo || Boolean(locked)} title="redo (Ctrl+Y)">
                ↷
              </button>
            </span>
            {!deployed ? (
              <span className="cv-exit-group" role="group" aria-label="Twin config">
                <button type="button" className="primary" onClick={save} disabled={!dirty || !valid || busy !== null}>
                  Save
                </button>
              </span>
            ) : (
              <span className="cv-exit-group" role="group" aria-label="Working copy">
                <button type="button" className="primary" onClick={applyExit} disabled={!config?.dirty || !config.apply_allowed || unsynced || !valid || busy !== null || Boolean(locked) || Boolean(exit)} title={topologyPending ? "a pending change needs a re-plan: Persist or Save-As, then redeploy" : "push the live changes onto the running twin"}>
                  Apply
                </button>
                <button type="button" onClick={() => void resetExit()} disabled={!config?.dirty || busy !== null || Boolean(locked) || Boolean(exit)} title="discard the working copy; what it applied is replayed from the baseline">
                  Reset
                </button>
                <button type="button" onClick={persist} disabled={!config?.dirty || unsynced || !valid || busy !== null || Boolean(locked) || Boolean(exit)}>
                  Persist
                </button>
                <button type="button" onClick={() => void refresh()} disabled={busy !== null}>
                  Refresh
                </button>
                {tierFailed.length && !exit && busy === null ? (
                  <button type="button" onClick={() => void resetExit(tierFailed)} data-testid="reapply-baseline" title={`the ${tierFailed.join(" and ")} playout ended failed: replay the baseline's ${tierFailed.join(", ")} onto the running twin`}>
                    Re-apply {tierFailed.join(", ")} from baseline
                  </button>
                ) : null}
                {topologyPending ? (
                  <Link to={`/twins/${encodeURIComponent(id)}`} data-testid="redeploy-link" title="a topology change needs a new plan: persist it, then deploy the baseline again">
                    Redeploy from baseline
                  </Link>
                ) : null}
              </span>
            )}
            <form
              className="cv-saveas cv-exit-group"
              aria-label="Save as"
              onSubmit={(e) => {
                e.preventDefault();
                void saveAs();
              }}
            >
              <input type="text" aria-label="Save as id" placeholder="save as id" value={saveAsId} onChange={(e) => setSaveAsId(e.target.value)} disabled={busy !== null} />
              <button type="submit" disabled={!saveAsId.trim() || !valid || busy !== null}>
                Save as
              </button>
            </form>
            {exitWord ? (
              <span className="cv-progress" role="status" data-testid="exit-progress">
                <span className="cv-spinner" aria-hidden="true" />
                {exitWord}
                {deployed && (exit || busy === "apply" || busy === "reset" || busy?.startsWith("re-apply")) ? (
                  <button type="button" onClick={cancelExit} disabled={Boolean(exit?.cancel_requested)} title="stop after the op in flight; the tier stays recorded attempted" data-testid="cancel-exit">
                    Cancel
                  </button>
                ) : null}
              </span>
            ) : null}
          </div>
        ) : null}
      </TopbarPortal>
      <div className="cv-strip" data-testid="canvas-strip">
        <ErrorLine error={error} prefix="could not load the twin" />
        <ErrorLine error={draft.error} prefix="could not save the layout" />
        {exitError ? (
          <div className="error" role="alert" data-testid="exit-error">
            {exitErrorText(exitError)}
            <button type="button" className="cv-dismiss" aria-label="Dismiss" onClick={() => setExitError(null)} title="dismiss">
              ×
            </button>
          </div>
        ) : null}
        {editProps && blocked ? (
          <div className="error" role="alert" data-testid="blocked-reason">
            blocked: {blocked}
          </div>
        ) : null}
        {editProps ? <ErrorLine error={serverReason} prefix="the API refused the document" /> : null}
        {draft.note ? <p className="notice" role="status" data-testid="layout-note">{draft.note}</p> : null}
        {exitNote ? (
          <p className="notice" role="status" data-testid="exit-note">
            {exitNote}
            <button type="button" className="cv-dismiss" aria-label="Dismiss" onClick={() => setExitNote(null)} title="dismiss">
              ×
            </button>
          </p>
        ) : null}
        {loading && !data ? <p className="muted">Loading the twin…</p> : null}
      </div>
      {built && placement ? (
        placement.overBudget ? (
          <Fallback id={id} kind={built.kind} graph={built.graph} reason={placement.overBudget} />
        ) : (
          <Canvas
            graph={built.graph}
            exportData={built.exportData}
            placement={placement}
            fan={fan}
            provFilter={provFilter}
            onFan={setFan}
            onPin={onPin}
            onPinMany={onPinMany}
            onToggleSite={onToggleSite}
            initialViewport={initialViewport}
            onViewport={onViewport}
            notes={notes}
            locked={Boolean(locked)}
            layoutBar={layoutBar}
            {...(editProps ? { edit: editProps } : {})}
            {...(editProps && data ? { palette: <Palette items={items} placing={placing} onArm={setPlacing} onPlace={(item, n) => onPlace(item, null, n, null)} count={copies} onCount={setCopies} hasNodeSelection={selection.kind === "node"} disabled={Boolean(locked)} note={data.paletteNote} /> } : {})}
            {...(editProps && model
              ? {
                  inspector: {
                    selection: (
                      <SidePanel
                        twinId={id}
                        model={model}
                        baseline={baselineModel}
                        selection={selection}
                        locked={Boolean(locked)}
                        images={[...new Set(items.filter((i) => i.kind === "image").map((i) => i.id))]}
                        personas={data?.personas ?? []}
                        canPromote={pit !== null}
                        promoteBlock={promoteBlock}
                        apply={edit}
                        onPromoteRole={onPromoteRole}
                        onPromoteMerge={onPromoteMerge}
                        annotations={annotations}
                        onAnnotation={(aid, patch) => setAnnotations(annotations.map((a) => (a.id === aid ? { ...a, ...patch } : a)))}
                        onRemoveAnnotation={(aid) => {
                          setAnnotations(annotations.filter((a) => a.id !== aid));
                          setSelection({ kind: "none" });
                        }}
                        serverReason={serverReason}
                        validation={validation && files && sameFiles(validation.files, files) ? validation : null}
                        validating={validating || !(validation && files && sameFiles(validation.files, files))}
                        changes={deployed ? (config?.changes ?? []) : null}
                        blocked={blocked}
                        onRenamed={(kind, from, to) => setSelection((sel) => (sel.kind === kind ? { kind, names: sel.names.map((n) => (n === from ? to : n)) } : sel))}
                        observedSegments={(node) => observedSegments(data?.exportData ?? null, cast, node, model.te.nodes.find((n) => n.name === node)?.nets.map((l) => l.segment) ?? [])}
                        onProvenance={pit !== null ? onProvenance : undefined}
                        provenanceBlock={provenanceBlock}
                        onSelect={setSelection}
                      />
                    ),
                    checks: (
                      <Checks
                        model={model}
                        serverReason={serverReason}
                        validation={validation && files && sameFiles(validation.files, files) ? validation : null}
                        validating={validating || !(validation && files && sameFiles(validation.files, files))}
                        changes={deployed ? (config?.changes ?? []) : null}
                        onDiscard={deployed && baselineFiles && !locked ? onDiscardChange : undefined}
                        provenance={{ filter: provFilter, onFilter: setProvFilter }}
                      />
                    ),
                    checksBadge: checksBadge({
                      model,
                      serverReason,
                      validation: validation && files && sameFiles(validation.files, files) ? validation : null,
                      validating: validating || !(validation && files && sameFiles(validation.files, files)),
                    }),
                  },
                }
              : {})}
          />
        )
      ) : null}
    </div>
  );
}

/** Over the element budgets: the existing list view, and a role x role
 *  matrix of the observed census (the lifted `matrix0`) when it is within
 *  its own axis budget -- never a denser canvas. */
function Fallback({ id, kind, graph, reason }: { id: string; kind: "authored" | "cast" | "design"; graph: ConsoleGraph; reason: string }) {
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
