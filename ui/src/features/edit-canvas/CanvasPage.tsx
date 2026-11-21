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
 * Validation is ON DEMAND (`SPEC.md` "The edit model"): nothing validates or
 * writes while the operator edits. Check (the bar's button, Ctrl+Enter) runs
 * `POST /api/validate` on the files on screen and records them; every exit
 * that overwrites a twin -- Save, Save As, Persist, Apply, Replace origin --
 * opens only on a passing Check of exactly the files on screen, and the API
 * validates again on the write (a 422 lands as the Check's findings). A
 * document that fails can only be kept as a DRAFT (`api/Spec.md` A3d), a
 * copy under its own id that never touches the twin it came from.
 *
 * A deployed twin's edits stay in this browser (the undo stack and the
 * edited text survive a reload) until Apply or Persist pushes them to the
 * working copy (A2) and runs the exit; Reset discards both. A cast twin's
 * role change or segment merge can be promoted to an assertion through the
 * imprint's seam, which re-casts and re-opens the working copy.
 *
 * `/canvas/new` is the same page over an UNTITLED design: a blank document
 * held in this browser (its undo stack and layout draft survive a reload),
 * no server read of a stored twin, no deploy. Its first save is a Save As
 * (on a passing Check) that names it in the catalog and moves the page to
 * `/canvas/<id>`, or a Save as draft.
 *
 * `/canvas/drafts/:id` is the page over a DRAFT: its document from the
 * drafts area, its layout in this browser, no deploy. Save draft rewrites
 * the draft; on a passing Check it can Replace its origin twin (A3 `PUT`,
 * after a confirm) or Save As a new twin.
 */
import type { Viewport } from "@xyflow/react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";

import { ApiError } from "../../api/client";
import { useWhoami } from "../../api/whoami";
import { TopbarPortal } from "../../areas/shell/topbar";
import { useApi } from "../../api/context";
import type { Target } from "../../api/types";
import type { CastRoster, DeployConfig, DeploySummary, DraftCheck, DraftDetail, LayoutAnnotation, LayoutDoc, LayoutWrite, ImageProfile, Persona, Template, Topology, TopologyExport, TwinConfigDetail, TwinSummary, TwinTree, ValidationFinding } from "../../api/types";
import { DestroyDialog, destroyWord, standingTiers, useDestroy } from "../../areas/shell/destroy";
import { DeployFlow } from "../../areas/shell/DeployFlow";
import { NO_SEGMENT_REASON, UNTITLED_ID, blankDesignFiles } from "../../areas/canvas/newDesign";
import { conduitOf, checkBadge, checkPasses, checkStatus, findingPins, findingTarget, localFindings, mergeFindings, refusalAsCheck, targetKey, type CheckRecord } from "./edit/check";
import { OpenDesignDialog } from "../../areas/canvas/OpenDesignDialog";
import { useLeaveGuard } from "../../areas/shell/leaveGuard";
import { remapFiles } from "../../model/preflight";
import { FOLLOW_ON_TIERS, deployMoving, tierReason, tierStatus, tierWord } from "../../areas/shell/deployState";
import { ErrorLine, errorText } from "../../areas/shell/parts";
import { useLoad } from "../../areas/shell/useLoad";
import { TwinTree as TwinTreeView } from "../../areas/twins/TwinTree";
import { Canvas, type CanvasEditProps } from "./Canvas";
import type { LabSource } from "./canvas/LabOverlay";
import { authoredGraph, EMPTY_TREE, treeStructure, type TreeStructure } from "./canvas/authored";
import { TREE_FAN_DEFAULT, placeTree, withoutFold } from "./canvas/tree";
import { FREE_FAN, freeLayout } from "./canvas/free";
import { castInput } from "./canvas/cast";
import { absolutePosition, pinKeyOf } from "./canvas/placement";
import { CANVAS_PLANE } from "./canvas/plane";
import { lanSitePrefix, nextFreeLan } from "./edit/allocate";
import { clearLegacyAnnotations, fromLayoutList, readLegacyAnnotations, toLayout } from "./edit/annotations";
import { sameFiles, type Files, type Stack } from "./edit/commands";
import { insertSwitch } from "./edit/switches";
import type { PlaceChoices, PlaceVia } from "./Canvas";
import { applyIntent, ospfImageGaps, OSPF_PROFILE, wirePlan, wirePlanText, wireRouters, type WireShape } from "./edit/connect";
import { addConduit, addSegment, danglingLinks, removeDanglingLinks, attach, detach, documentGraph, legacyLayout, moveTo, readDoc, rehome, removeNode, removeSegment, renamedFiles, retargetTap, saveAsFiles, type DocModel, type WorkingDoc, moveTap } from "./edit/document";
import { applyFabricPlan } from "./edit/fabric";
import { observedSegments } from "./canvas/evidence";
import type { PaletteItem } from "./edit/EditContext";
import { useEditor, type ApplyOptions } from "./edit/useEditor";
import { adoptBaselineProvenance } from "./edit/provenance";
import { LAYOUT_SCHEMA, layoutWrite, pinsOf, useLayoutDraft, type LayoutMode } from "./edit/useLayoutDraft";
import { matrix0 } from "./layout/matrix";
import { buildGraph, type ConsoleGraph } from "./model/graph";
import { AddPanel } from "./add/AddPanel";
import { healthHints, imageFor, personaLabel, pushRecent, readRefs, RECENT_KEY, writeRefs } from "./add/catalog";
import { applyDeviceDrop, applyInternetDrop, applyTapDrop, type DropTarget } from "./add/drop";
import { DropPopover, type DropCommit } from "./add/DropPopover";
import { QuickAdd } from "./add/QuickAdd";
import { applyTemplateDrop, type TemplateDrop, type Values } from "./add/template";
import { besideSubtree, clearOf, placeDropped } from "./add/placeDrop";
import { TemplatePopover, TemplatesSection } from "./add/Templates";
import { buildTemplate, carriedBindings, proposeTemplate, readMyTemplates, rememberMyTemplate, type Proposal } from "./add/saveTemplate";
import { SaveTemplateDialog } from "./add/SaveTemplateDialog";
import { refOfItem } from "./add/AddPanel";
import { SaveAsDialog } from "./SaveAsDialog";
import { moveMember } from "./edit/members";
import { attachDevices, copyPositions, duplicateSubtree, parkedDevices, type DuplicateResult } from "./edit/subtree";
import { Checks, SidePanel, checksBadge, knownSensors, pruneSelection, type FabricResult, type ProvenanceAction, type ProvenanceSubject, type Selection } from "./SidePanel";

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
  /** On `/canvas/drafts/:id`: the draft with its document (A3d); null otherwise. */
  draftRow: DraftDetail | null;
}

const POLL_MS = 5000;
/** While an exit is in flight the poll runs at this pace, for the progress state. */
const BUSY_POLL_MS = 1000;
/** A note about a finished exit fades after this; an error stays until the next exit. */
const NOTE_MS = 8000;

/** The body for one more pin over the document's: the pins of this plane plus
 *  the moved element's new absolute top-left, the annotations carried through. */
export function nextLayoutWrite(current: LayoutDoc | null, id: string, key: string, at: { x: number; y: number }): LayoutWrite {
  return layoutWrite(current, id, { ...pinsOf(current), [key]: at }, current?.annotations ?? []);
}

/** The body for a new annotation list over the document's pins of this plane. */
export function annotationsWrite(current: LayoutDoc | null, id: string, annotations: readonly LayoutAnnotation[]): LayoutWrite {
  return { ...layoutWrite(current, id, pinsOf(current), annotations), annotations: [...annotations] };
}

/** The twin's standing deploy: its newest `te` deploy that is running or completed. */
export function standingDeploy(deploys: readonly DeploySummary[], twinId: string): DeploySummary | null {
  const mine = deploys.filter((d) => d.scenario_id === twinId && (d.tier ?? "te") === "te" && (d.status === "running" || d.status === "completed"));
  mine.sort((a, b) => b.started_at - a.started_at);
  return mine[0] ?? null;
}

// The tier state per te deploy (rows, then the follow-on record) and its word: one implementation,
// the shell's, shared with the Twins pages.
export { tierStatus, tierWord } from "../../areas/shell/deployState";

/** The working copy's state word (`SPEC.md` "Shell and chrome"): `dirty` alone
 *  read as "not applied" to an operator, when it means "differs from the
 *  baseline". Two states instead: the copy's changes were applied and are not
 *  yet persisted, or the copy carries changes not applied. Pure. */
export function docState(config: Pick<DeployConfig, "dirty" | "applied" | "updated_at"> | null, editorDirty: boolean, unsynced: boolean): "clean" | "syncing" | "applied" | "unapplied" {
  // `syncing`: the edits on screen are in this browser only; Apply or Persist pushes them.
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

/** An Add-panel item to the front of this browser's recent list (docs/design/canvas-add-panel.md §3). */
function rememberAdd(item: PaletteItem): void {
  const ref = refOfItem(item);
  if (ref) writeRefs(RECENT_KEY, pushRecent(readRefs(RECENT_KEY), ref));
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

export function writeStoredStack(scope: string, stack: Stack, local?: { files: Files; base: Files }): void {
  try {
    if (stack.done.length === 0 && stack.undone.length === 0) localStorage.removeItem(undoKey(scope));
    else localStorage.setItem(undoKey(scope), JSON.stringify(local ? { stack, files: local.files, base: local.base } : { stack }));
  } catch {
    // no storage: the stack lives for the page only
  }
}

/** A deployed twin's edits not yet pushed (Apply / Persist push them), as this browser kept them:
 *  restored only while the working copy is still the text they were made over (`base`) and the
 *  stack still ends at them; another console's change to the copy drops them. */
export function readStoredLocal(scope: string, server: Files | null): { stack: Stack; files: Files } | null {
  if (!server) return null;
  try {
    const raw = localStorage.getItem(undoKey(scope));
    if (!raw) return null;
    const v = JSON.parse(raw) as { stack?: Partial<Stack>; files?: Files; base?: Files };
    if (!v.files || !v.base || !v.stack || !Array.isArray(v.stack.done) || !Array.isArray(v.stack.undone)) return null;
    if (!sameFiles(v.base, server)) return null;
    const stack = { done: v.stack.done, undone: v.stack.undone };
    return stackFits(stack, v.files) ? { stack, files: v.files } : null;
  } catch {
    return null;
  }
}

/** The untitled design's browser scope: its files and stack (it has no server copy to fit a stack to). */
export const UNTITLED_SCOPE = "twin:__untitled__";

function untitledLayoutKey(scope: string = UNTITLED_SCOPE): string {
  return `axr-canvas-layout:${scope}`;
}

/** A draft's browser scope (its layout and stack): a draft has no layout side-car. */
export function draftScope(id: string): string {
  return `draft:${id}`;
}

/** What an untitled design was started from: blank (no source), or a clone of a catalog twin. */
export interface UntitledOrigin {
  /** The te file's path (a clone keeps its source's). */
  teFile: string;
  /** The twin it clones, or null for a blank design. */
  source: string | null;
}

export interface UntitledKept extends UntitledOrigin {
  files: Files;
  stack: Stack;
}

/** The untitled design as this browser last held it: the files, the stack that ends at them (empty
 *  for a clone not yet edited), and where it started. */
export function readUntitled(): UntitledKept | null {
  try {
    const raw = localStorage.getItem(undoKey(UNTITLED_SCOPE));
    if (!raw) return null;
    const v = JSON.parse(raw) as { files?: Files; stack?: Partial<Stack>; teFile?: string; source?: string | null };
    if (!v.files || !v.stack || !Array.isArray(v.stack.done) || !Array.isArray(v.stack.undone)) return null;
    const stack = { done: v.stack.done, undone: v.stack.undone };
    const empty = stack.done.length === 0 && stack.undone.length === 0;
    if (!empty && !stackFits(stack, v.files)) return null;
    return { files: v.files, stack, teFile: typeof v.teFile === "string" ? v.teFile : "te.yaml", source: typeof v.source === "string" ? v.source : null };
  } catch {
    return null;
  }
}

/** Keep the untitled design: a blank one -- nothing done, or undone back to its blank start
 *  (`blank`) -- is not kept, so the next New opens empty; a clone always is. */
export function writeUntitled(files: Files, stack: Stack, origin: UntitledOrigin, blank = false): void {
  try {
    if (!origin.source && (blank || (stack.done.length === 0 && stack.undone.length === 0))) localStorage.removeItem(undoKey(UNTITLED_SCOPE));
    else localStorage.setItem(undoKey(UNTITLED_SCOPE), JSON.stringify({ files, stack, teFile: origin.teFile, source: origin.source }));
  } catch {
    // no storage: the untitled design lives for the page only
  }
}

/** The untitled design's (or a draft's) layout, as a layout document nobody has saved. */
function readUntitledLayout(scope: string = UNTITLED_SCOPE, id: string = UNTITLED_ID): LayoutDoc {
  const empty: LayoutDoc = { imprint_id: id, path: "", exists: false, etag: null, schema: LAYOUT_SCHEMA, imprint: id, pins: {}, plane: CANVAS_PLANE, annotations: [] };
  try {
    const raw = localStorage.getItem(untitledLayoutKey(scope));
    if (!raw) return empty;
    const v = JSON.parse(raw) as { pins?: LayoutDoc["pins"]; annotations?: LayoutAnnotation[]; mode?: LayoutDoc["mode"] };
    return { ...empty, pins: v.pins ?? {}, annotations: Array.isArray(v.annotations) ? v.annotations : [], mode: v.mode === "tree" || v.mode === "free" ? v.mode : null };
  } catch {
    return empty;
  }
}

export function writeUntitledLayout(pins: LayoutDoc["pins"], annotations: LayoutAnnotation[], scope: string = UNTITLED_SCOPE, mode: LayoutDoc["mode"] = null): void {
  try {
    if (Object.keys(pins).length === 0 && annotations.length === 0 && !mode) localStorage.removeItem(untitledLayoutKey(scope));
    else localStorage.setItem(untitledLayoutKey(scope), JSON.stringify({ pins, annotations, ...(mode ? { mode } : {}) }));
  } catch {
    // no storage
  }
}

/** Forget the untitled design: after its Save-As, or when the operator lets it go. */
export function clearUntitled(): void {
  try {
    localStorage.removeItem(undoKey(UNTITLED_SCOPE));
    localStorage.removeItem(untitledLayoutKey());
  } catch {
    // no storage
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

export function CanvasPage({ untitled = false, draft: isDraft = false }: { untitled?: boolean; draft?: boolean } = {}) {
  const api = useApi();
  const whoami = useWhoami();
  const navigate = useNavigate();
  const params = useParams();
  const id = untitled ? UNTITLED_ID : (params.id ?? "");
  // The browser-side scope for what is kept per design (the viewport): the untitled one and each draft have their own.
  const scopeId = untitled ? UNTITLED_SCOPE : isDraft ? draftScope(id) : id;
  // Where layouts live in this browser (no side-car): the untitled design's and a draft's.
  const browserLayout = untitled || isDraft;
  // The untitled design's blank start: what its edits are measured against (dirty) until Save-As.
  const untitledSeed = useMemo(() => (untitled ? { id: UNTITLED_ID, te_file: "te.yaml", files: blankDesignFiles({ id: UNTITLED_ID }) } : null), [untitled]);
  // Where the untitled design started (blank, or a clone of a catalog twin): read with it, kept with it.
  const [origin, setOrigin] = useState<UntitledOrigin>({ teFile: "te.yaml", source: null });
  const { data, error, loading, reload } = useLoad<Loaded>(async (signal) => {
    // Untitled: nothing is stored on the server yet, so nothing is read.
    if (untitled) return { twin: { id, as_of: "", hosts: 0, segments: 0, services: 0, personas: 0, mean_confidence: 0 }, tree: null, exportData: null, topology: null, cast: null, deploy: null, deploys: [], config: null, detail: null, draftRow: null };
    // A draft: its document from the drafts area, nothing from the catalog (it is not a twin) and no deploy.
    if (isDraft) {
      const draftRow = await api.getDraft(id, signal);
      return { twin: { id, as_of: "", hosts: 0, segments: 0, services: 0, personas: 0, mean_confidence: 0 }, tree: null, exportData: null, topology: null, cast: null, deploy: null, deploys: [], config: null, detail: null, draftRow };
    }
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
    return { twin, tree, exportData, topology, cast, deploy, deploys: rows, config, detail, draftRow: null };
  }, [id, untitled, isDraft]);
  // The Add panel's sources (docs/design/canvas-add-panel.md §4): the persona library (roles,
  // grouped by category; one cheap read the member rows need too) and the image profiles (card +
  // OS -> image). The old palette's lazy read of every other twin's config went with "From other twins".
  const personaLoad = useLoad<{ personas: Persona[]; note: string | null }>(
    (signal) => api.listPersonas(signal).then((p) => ({ personas: p.personas, note: null }), (e: unknown) => ({ personas: [], note: `personas not listed: ${errorText(e)}` })),
    [id],
  );
  const profileLoad = useLoad<{ profiles: ImageProfile[]; note: string | null }>(
    (signal) => api.listImageProfiles(signal).then((p) => ({ profiles: p.profiles, note: null }), (e: unknown) => ({ profiles: [], note: `image profiles not listed: ${errorText(e)}` })),
    [],
  );
  // Templates (§5): built-in and saved, one read; `templatesVersion` re-reads after a save (phase 3).
  const [templatesVersion, setTemplatesVersion] = useState(0);
  const templateLoad = useLoad<{ templates: Template[]; note: string | null }>(
    (signal) => api.listTemplates(signal).then((t) => ({ templates: t.templates, note: t.warnings.length ? `templates: ${t.warnings.join("; ")}` : null }), (e: unknown) => ({ templates: [], note: `templates not listed: ${errorText(e)}` })),
    [templatesVersion],
  );
  const personas = personaLoad.data?.personas ?? [];
  const imageProfiles = profileLoad.data?.profiles ?? [];
  // The image OSPF needs (the `router-ospf` profile), or null when the profiles name none.
  const ospfImage = imageFor(imageProfiles, OSPF_PROFILE);
  const templates = useMemo(() => templateLoad.data?.templates ?? [], [templateLoad.data]);
  const paletteNote = [personaLoad.data?.note, profileLoad.data?.note, templateLoad.data?.note].filter(Boolean).join("; ") || null;
  const layout = useLoad<LayoutDoc>((signal) => (untitled ? Promise.resolve(readUntitledLayout()) : isDraft ? Promise.resolve(readUntitledLayout(draftScope(id), id)) : api.getLayout(id, signal)), [id, untitled, isDraft]);
  // The targets (docs/design/targets-and-lab.md §2.3): the deployed twin's target, else the default one, feeds the
  // tap inspector's Sensors (its lab's sensors, with adapter.conf's EXTERNAL_SENSORS for one release), the Lab
  // overlay and the deployed bar's target chip. A refusal leaves them empty.
  const targetsLoad = useLoad<Target[] | null>((signal) => api.listTargets(signal).then((t) => t.targets, () => null), []);
  const sensorTarget: Target | null = (() => {
    const list = targetsLoad.data ?? [];
    const named = data?.deploy?.target ? list.find((t) => (t.name ?? t.node) === data.deploy!.target) : null;
    return named ?? list.find((t) => t.default) ?? list[0] ?? null;
  })();
  const sensorTargetName = sensorTarget ? (sensorTarget.name ?? sensorTarget.node) : null;
  const declaredSensors = useMemo(
    () => (sensorTarget ? [...new Set([...(sensorTarget.lab?.components ?? []).filter((c) => c.role === "sensor").map((c) => c.id), ...(sensorTarget.sensors ?? [])])] : []),
    [sensorTarget],
  );
  const labSource: LabSource | null = useMemo(
    () =>
      sensorTarget && sensorTargetName
        ? {
            target: sensorTargetName,
            platform: sensorTarget.platform ?? null,
            components: sensorTarget.lab?.components ?? [],
            extraSensors: sensorTarget.sensors ?? [],
            loadHealth: (signal: AbortSignal) => api.getTargetHealth(sensorTargetName, signal),
          }
        : null,
    [sensorTarget, sensorTargetName, api],
  );

  // ---- positions and annotations: the layout side-car as a draft (Phase 1) ----
  // Moves and annotation changes stay on the canvas; the side-car is written
  // only by "Save layout" (or alongside a document Save / Persist).
  const editor = useEditor();
  // A pin whose node no longer exists (removed, or undone away) is dormant on a design kept in this
  // browser (the untitled one, a draft): kept for a redo, but neither an unsaved change nor
  // "something on the canvas" (2026-11-20: undone back to empty, the canvas still asked to leave).
  const liveTe = browserLayout ? (editor.model?.te ?? null) : null;
  const isLive = useMemo(() => {
    if (!liveTe) return null;
    const names = new Set(liveTe.nodes.map((n) => n.name));
    const cidrs = new Set(liveTe.segments.map((s) => s.cidr));
    return (key: string) => (key.startsWith("host:") ? names.has(key.slice(5)) : key.startsWith("segment:") ? cidrs.has(key.slice(8)) : true);
  }, [liveTe]);
  const draft = useLayoutDraft(api, id, layout.data, isLive);
  const { pins, annotations, onPin, onPinMany, setAnnotations } = draft;
  // The pins as they are now, for handlers memoized without them (the duplicate's drop).
  const pinsRef = useRef(pins);
  pinsRef.current = pins;
  const layoutChanges = draft.changes;
  const saveLayout = draft.save;
  // The untitled design's (or a draft's) layout is kept in this browser as it changes (it has no side-car to save to).
  const leavingLayout = useRef(false);
  // Written as it stands once loaded -- the live pins only, so a design undone back to empty leaves
  // nothing behind for the next New to reopen.
  const layoutLoaded = draft.doc !== null;
  const livePins = draft.livePins;
  useEffect(() => {
    if (!browserLayout || leavingLayout.current || !layoutLoaded) return;
    writeUntitledLayout(livePins, annotations.map(toLayout), untitled ? UNTITLED_SCOPE : draftScope(id), draft.mode);
  }, [browserLayout, untitled, id, livePins, annotations, layoutLoaded, draft.mode]);
  const [expanded, setExpanded] = useState<string[]>([]);
  const [initialViewport] = useState(() => readViewport(scopeId));

  // ---- the editor (Phase 2) ----
  const [config, setConfig] = useState<DeployConfig | null>(null);
  const [detail, setDetail] = useState<TwinConfigDetail | null>(null);
  const [view, setView] = useState<View>("design");
  const [selection, setSelection] = useState<Selection>({ kind: "none" });
  // After every document change (an edit, an undo, a redo) the selection keeps only what still
  // exists; nothing left is no selection, and the inspector shows the Twin summary.
  const docModel = editor.model;
  useEffect(() => {
    if (!docModel) return;
    setSelection((cur) => pruneSelection(cur, docModel));
  }, [docModel]);
  /** An Add-panel drop waiting on its popover (docs/design/canvas-add-panel.md §4.1): nothing is written until it closes. */
  /** Save as template (§5.3): the proposal for the selection, while its dialog is open. */
  const [saveTpl, setSaveTpl] = useState<{ proposal: Proposal; bare: Proposal | null; stripped: string[]; bareStripped: string[]; busy: boolean; error: string | null } | null>(null);
  /** The devices the canvas selects once drawn: a template's open ports after its drop (§5.2). */
  const [highlight, setHighlight] = useState<{ names: string[]; n: number } | null>(null);
  /** A template drop waiting on its settings form (§5.2). */
  const [pendingTemplate, setPendingTemplate] = useState<{ template: Template; at: { x: number; y: number } | null; screen: { x: number; y: number } } | null>(null);
  const [pendingDrop, setPendingDrop] = useState<{ item: PaletteItem; target: DropTarget; at: { x: number; y: number } | null; screen: { x: number; y: number } } | null>(null);
  const [blocked, setBlocked] = useState<string | null>(null);
  /** The working-copy poll's last failure; the next good poll clears it (R3-9). */
  const [pollError, setPollError] = useState<unknown>(null);
  const [exitError, setExitError] = useState<unknown>(null);
  const [exitNote, setExitNote] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  // Destroy (2026-11-08): the standing deploy comes down from the bar; the canvas then returns to
  // the undeployed edit mode (the working copy is gone with the deploy) without a page reload.
  const [destroyOpen, setDestroyOpen] = useState(false);
  // The bar's Deploy (an undeployed design): the deploy dialog (docs/design/targets-and-lab.md §2.3).
  const [deployOpen, setDeployOpen] = useState(false);
  // Open (the bar's button, Ctrl+O) and the untitled design's first-Save prompt.
  const [openDialog, setOpenDialog] = useState(false);
  const [saveAsOpen, setSaveAsOpen] = useState(false);
  // Save as draft (always available) and the draft's Replace <origin> confirm.
  const [draftOpen, setDraftOpen] = useState(false);
  const [replaceOpen, setReplaceOpen] = useState(false);
  // B3: the bulk-wiring dialog (2+ routers, a shape, OSPF on the new transits or not).
  const [wireDialog, setWireDialog] = useState<{ routers: string[]; shape: WireShape; ospf: boolean; area: number; switchImage: boolean } | null>(null);

  const destroyer = useDestroy(api, (r) => {
    setDestroyOpen(false);
    destroyer.clear();
    const came = (r.tiers ?? []).filter((t) => t.destroyed).map((t) => t.tier);
    setExitNote(`destroyed ${r.deploy_id}${came.length ? ` · ${came.join(" · ")} down` : ""}`);
    reload();
  });
  const destroyRun = destroyer.run;
  const destroying = destroyRun?.phase === "running";
  const [deployRows, setDeployRows] = useState<DeploySummary[]>([]);
  const [saveAsId, setSaveAsId] = useState("");
  // The last Check (on demand): the files it judged and the merged answer.
  const [check, setCheck] = useState<CheckRecord | null>(null);
  const [checking, setChecking] = useState(false);
  // A finding clicked in the Checks tab: the canvas zooms to the element it names.
  const [focus, setFocus] = useState<{ key: string; n: number } | null>(null);
  // The working copy's text as the server holds it (a deployed twin): what local edits are measured against.
  const synced = useRef<Files | null>(null);
  const deployed = Boolean(data?.deploy);
  const deployId = data?.deploy?.deploy_id ?? null;
  const locked = data?.deploy?.status === "running";

  useEffect(() => {
    if (!data) return;
    setConfig(data.config);
    setDetail(data.detail);
    setDeployRows(data.deploys);
    const document = data.config?.document ?? data.draftRow?.document ?? data.detail?.document ?? untitledSeed;
    setCheck(null);
    if (untitled && untitledSeed) {
      // The untitled design as this browser left it (a reload), else its blank start.
      const kept = readUntitled();
      setOrigin({ teFile: kept?.teFile ?? untitledSeed.te_file, source: kept?.source ?? null });
      // Measured against the blank start: a clone is unsaved work from its first moment.
      editor.load({ id: untitledSeed.id, teFile: kept?.teFile ?? untitledSeed.te_file, files: kept?.files ?? untitledSeed.files }, { saved: untitledSeed.files });
      if (kept) editor.restoreStack(kept.stack);
    } else if (document) {
      editor.load({ id: document.id, teFile: document.te_file, files: document.files });
      synced.current = { ...document.files };
      // A deployed twin's edits not yet pushed come back over the same working copy (they never left this browser).
      const local = data.config ? readStoredLocal(data.config.deploy_id, document.files) : null;
      if (local) {
        editor.load({ id: document.id, teFile: document.te_file, files: local.files }, { saved: document.files });
        editor.restoreStack(local.stack);
      } else {
        // The same text as this browser last edited: its Undo comes back.
        const stored = readStoredStack(data.config ? data.config.deploy_id : isDraft ? draftScope(id) : `twin:${id}`, document.files);
        if (stored) editor.restoreStack(stored);
      }
    }
    setSelection({ kind: "none" });
    setBlocked(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, id]);

  // A set an earlier build kept in this browser joins the canvas once (as
  // unsaved changes, for the next "Save layout") and is cleared from the browser.
  const migrated = useRef<string | null>(null);
  useEffect(() => {
    if (!layout.data || migrated.current === id || untitled) return;
    migrated.current = id;
    const legacy = readLegacyAnnotations(id);
    if (!legacy.length) return;
    const merged = [...fromLayoutList(layout.data.annotations), ...legacy.filter((a) => !layout.data!.annotations.some((x) => x.id === a.id))];
    setAnnotations(merged);
    clearLegacyAnnotations(id);
  }, [layout.data, id, untitled, setAnnotations]);

  // R3-2: a switch wired to nothing (a parked duplicate, a switch just placed) is drawn with its
  // "not attached" badge and reported by Check (`edit/check.ts`); it no longer holds any edit.
  const parked = useMemo(() => (editor.model ? parkedDevices(editor.model.te) : []), [editor.model]);
  const parkedKey = parked.join(",");
  // An untitled design with no segment is not yet a document the engine accepts: a Check finding.
  const emptyReason = untitled && editor.model && editor.model.te.segments.length === 0 ? NO_SEGMENT_REASON : null;

  // Check (on demand): the files on screen through `POST /api/validate`, the canvas's own findings
  // folded in, recorded with exactly those files. Nothing else validates.
  const checkSeq = useRef(0);
  const runCheck = useCallback(async (): Promise<CheckRecord | null> => {
    const files = editor.files;
    const doc = editor.doc;
    const model = editor.model;
    if (!files || !doc || !model) return null;
    const seq = ++checkSeq.current;
    setChecking(true);
    const local = localFindings(model, parkedDevices(model.te), untitled && model.te.segments.length === 0 ? NO_SEGMENT_REASON : null);
    let rec: CheckRecord;
    try {
      const result = await api.validateDocument({ id: doc.id, files });
      rec = { files, result: mergeFindings(result, local), error: null };
    } catch (e) {
      // A 4xx is the API refusing THIS document (a shape it cannot read): a failing Check with that
      // reason as its finding. Only a request that failed (network, 5xx) is "could not validate".
      if (e instanceof ApiError && typeof e.status === "number" && e.status >= 400 && e.status < 500) {
        const finding: ValidationFinding = { rule_id: `API-${e.status}`, severity: "error", message: e.message, location: null };
        rec = { files, result: mergeFindings({ disposition: "FAIL", exit_code: 1, passes_run: [], errors: 1, warnings: 0, findings: [finding] }, local), error: null };
      } else rec = { files, result: null, error: e };
    }
    try {
      if (seq === checkSeq.current) setCheck(rec);
      return rec;
    } finally {
      if (seq === checkSeq.current) setChecking(false);
    }
  }, [api, editor, untitled]);
  const runCheckRef = useRef(runCheck);
  runCheckRef.current = runCheck;

  // R3-9: a banner about a refused or blocked edit clears on the next change that lands (an
  // edit, an Undo, a Redo, a reload): it described a document that is no longer on screen.
  const lastFiles = useRef(editor.files);
  useEffect(() => {
    if (lastFiles.current === editor.files) return;
    lastFiles.current = editor.files;
    setBlocked(null);
  }, [editor.files]);

  // One mutation: the editor validates and records it; the reason it was blocked is shown in place.
  const edit = useCallback(
    (label: string, fn: (d: WorkingDoc, m: DocModel) => Files, options?: ApplyOptions): string | null => {
      if (locked) {
        setBlocked("view mode: the deploy is running");
        return "view mode";
      }
      const reason = editor.apply(label, fn, options);
      setBlocked(reason);
      return reason;
    },
    [editor, locked],
  );

  // The D11 offer (native-switches §12 S5): the engine plans the whole-fabric conversion over the
  // working document; the plan lands as one edit (one undo).
  const [fabricResult, setFabricResult] = useState<FabricResult | null>(null);
  const onConvertFabric = useCallback(async () => {
    const doc = editor.doc;
    const files = editor.files;
    if (!doc || !files) throw new Error("no document loaded");
    const plan = await api.fabricConversion({ id: doc.id, files }, doc.teFile);
    const blocked = edit(`convert the fabric to native switches (${plan.bridges.length} bridge VMs)`, (d, m) => applyFabricPlan(d, m, plan));
    if (!blocked) setFabricResult({ bridges: plan.bridges, notes: plan.notes, problems: plan.problems });
    return blocked;
  }, [api, editor, edit]);

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
      setPollError(null);
    } catch (e) {
      setPollError(e);
    }
  }, [api, deployId, editor]);
  // A tier queued or running behind the te (Deploy's follow-on, Start tap / tfc) polls fast too.
  const tierMoving = deployId ? deployMoving(deployRows, deployRows.find((d) => d.deploy_id === deployId) ?? null) : false;
  const inFlight = Boolean(busy) || Boolean(config?.exit) || tierMoving;
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

  // The stack is stored once the text is at rest on the server (saved to the catalog or the
  // drafts area), so the stored stack describes exactly the text a reload will get. A deployed
  // twin's edits stay in this browser until Apply / Persist: they are stored with the stack and
  // the working-copy text they were made over, and come back on a reload over that same copy.
  useEffect(() => {
    if (!editor.files || untitled) return;
    if (deployed && deployId) {
      const base = synced.current;
      if (base && !sameFiles(editor.files, base)) writeStoredStack(deployId, editor.stack, { files: editor.files, base });
      else writeStoredStack(deployId, editor.stack);
      return;
    }
    if (editor.dirty) return;
    writeStoredStack(isDraft ? draftScope(id) : `twin:${id}`, editor.stack);
  }, [deployed, deployId, id, untitled, isDraft, editor.files, editor.dirty, editor.stack]);
  // The untitled design has no server text to be at rest on: every change is kept in this browser.
  // `leaving` stops the writes once a Save-As (or a let-go) has dropped it.
  const leaving = useRef(false);
  useEffect(() => {
    if (!untitled || !editor.files || leaving.current) return;
    writeUntitled(editor.files, editor.stack, origin, Boolean(untitledSeed && sameFiles(editor.files, untitledSeed.files)));
  }, [untitled, editor.files, editor.stack, origin, untitledSeed]);

  // Ctrl+Enter runs Check, bound at the page level (2026-11-21): on the window, in the capture
  // phase (before anything on the page can stop it), from the moment the page is shown -- not only once the canvas has had a click -- and
  // still ignored while a text field has the focus.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!(e.ctrlKey || e.metaKey) || e.key !== "Enter") return;
      const target = e.target as HTMLElement | null;
      if (target && (/^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName) || target.isContentEditable)) return;
      e.preventDefault();
      void runCheckRef.current();
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, []);

  // Ctrl+Z / Ctrl+Y over the command stack, when no field has the focus.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!(e.ctrlKey || e.metaKey)) return;
      const target = e.target as HTMLElement | null;
      if (target && /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName)) return;
      const k = e.key.toLowerCase();
      if (k === "o") {
        e.preventDefault();
        setOpenDialog(true);
      } else if (k === "z" && !e.shiftKey) {
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
  // R3-1: a twin cast from an imprint reads a missing `source` as discovered (a cast older than
  // track 3 wrote none); a hand-authored twin reads it as authored and has no overlay to show.
  const castTwin = Boolean(data && (data.twin.pit_id || data.exportData || data.cast));
  const defaultSource = castTwin ? "discovered" : "authored";
  // The layout (B2): the choice saved with the layout, else free for an authored design (untitled, a
  // draft, a twin with no cast or discovery provenance) and the device tree for a cast twin.
  // Free only for a design still being drawn -- the untitled one or a draft -- unless the side-car
  // says otherwise; every catalog twin (authored or cast) and every deployed one opens in the tree
  // (2026-11-20: a finished twin opened free and lost its structure).
  const layoutMode: LayoutMode = draft.mode ?? (untitled || isDraft ? "free" : "tree");
  const layoutModeRef = useRef(layoutMode);
  layoutModeRef.current = layoutMode;
  // The rail's Fan-out (§7.6): how many implied switches a carrier shows before folding the rest.
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
  // A fold opened or closed, or the Fan-out changed: the tree re-ranks around it (brief "Folding"),
  // so the session's settled positions are dropped; a document edit keeps them (round 2).
  const foldState = useRef("");
  const placement = useMemo(() => {
    if (!built) return null;
    const design = built.kind === "design";
    const folds = `${fan}|${[...expanded].sort().join(",")}`;
    if (foldState.current && foldState.current !== folds) settled.current = {};
    foldState.current = folds;
    const taps = design && model?.tap ? model.tap.bindings.map((b) => ({ index: b.index, scope: b.scope, targets: b.targets, segment: b.segment, sensors: b.sensors, source: b.source })) : [];
    const free = design && layoutMode === "free";
    const known = design ? { ...settled.current, ...pins } : pins;
    const placed = placeTree(built.graph, known, { expanded, extraLegs: built.extraLegs, uplinks: built.uplinks, tree: built.tree, fan: free ? FREE_FAN : fan, taps, defaultSource, parked: design ? parked : undefined });
    // Linked routers with no position yet are spread (a zig-zag row), not left in dagre's column.
    const p = free ? freeLayout(placed, new Set(Object.keys(known))) : placed;
    if (design && !p.overBudget) {
      // The tree's own positions settle for the session (an implied switch, a
      // device, a router, a host): a later mutation places only what has no
      // position yet. A key two nodes share (two segments on one CIDR, §9 D3)
      // names neither, so it never settles.
      const keyCount = new Map<string, number>();
      for (const n of p.nodes) {
        const key = n.parentId ? null : pinKeyOf(n);
        if (key) keyCount.set(key, (keyCount.get(key) ?? 0) + 1);
      }
      for (const n of p.nodes) {
        const key = pinKeyOf(n);
        if (!key || key in settled.current || n.parentId || (keyCount.get(key) ?? 0) > 1) continue;
        const abs = absolutePosition(p.nodes, n.id);
        if (abs) settled.current[key] = abs;
      }
    }
    return p;
  }, [built, pins, expanded, fan, model?.tap, defaultSource, parkedKey, layoutMode]);

  const onToggleSite = useCallback((siteKey: string) => setExpanded((xs) => (xs.includes(siteKey) ? withoutFold(xs, siteKey) : [...xs, siteKey])), []);
  const onViewport = useCallback(
    (v: Viewport) => {
      try {
        sessionStorage.setItem(viewportKey(scopeId), JSON.stringify(v));
      } catch {
        // storage unavailable: the viewport is not restored, nothing else changes
      }
    },
    [scopeId],
  );

  // ---- the palette's placement ----
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
      const prefix = lanSitePrefix([nearSeg?.cidr, ...inPlay]);
      // The next /24 AFTER the highest in use under the prefix (.10, .20 -> .21), not the
      // first free one (.0, the site's own network address, legal but surprising); never a transit's.
      const cidr = nextFreeLan(prefix, inPlay);
      if (!cidr) throw new Error(`no free /24 under ${prefix ?? "the default LAN prefix"}`);
      const taken = new Set(m.te.segments.map((s) => s.name));
      let n = m.te.segments.length + 1;
      while (taken.has(`segment-${n}`)) n++;
      return addSegment(d, { name: `segment-${n}`, cidr, zoneType: "vlan" });
    });
  }, [edit]);
  // The Add panel (docs/design/canvas-add-panel.md): a card dropped, clicked or quick-added lands per
  // the §7.1 drop rules (a segment, a switch's segment, a router's new segment, the empty pane), then
  // its popover opens there; the commit writes the drop and every popover choice as ONE command.
  const onPlace = useCallback(
    (item: PaletteItem, segment: string | null, _count: number, at: { x: number; y: number } | null, via?: PlaceVia | PlaceChoices, screen?: { x: number; y: number }) => {
      if (via && "blocked" in via) {
        setBlocked(via.blocked);
        return;
      }
      const target: DropTarget = segment ? (via && "choices" in via ? { segment, choices: via.choices, via: via.via } : { segment }) : via && "router" in via ? { router: via.router } : { pane: true };
      const where = screen ?? { x: 80, y: 80 };
      if (item.kind === "template") {
        const template = templates.find((t) => t.id === item.id);
        if (!template) {
          setBlocked(`template ${item.id} is no longer in the catalog`);
          return;
        }
        setPendingTemplate({ template, at, screen: where });
        return;
      }
      if (item.kind === "tool" && item.tool === "internet") {
        const router = "router" in target ? target.router : null;
        if (edit(router ? `internet uplink for ${router}` : "add internet", (d, m) => applyInternetDrop(d, m, target, imageFor(imageProfiles, "linux-server")).files) === null) rememberAdd(item);
        return;
      }
      if (item.kind === "tool" && item.tool === "tap" && !("segment" in target)) {
        setBlocked("drop the Sensor / tap on a segment or a switch");
        return;
      }
      setPendingDrop({ item, target, at, screen: where });
    },
    [edit, imageProfiles, templates],
  );
  // A template lands as ONE command (§5.2): expanded, copied in by the duplicate-subtree engine, laid
  // out where it was dropped, its open ports selected so they stand out for wiring.
  const commitTemplate = useCallback(
    (values: Values) => {
      const p = pendingTemplate;
      setPendingTemplate(null);
      if (!p) return;
      let drop: TemplateDrop | null = null;
      let span: { before: DocModel["te"]; after: DocModel["te"] } | null = null;
      const reason = edit(`add template ${p.template.label}`, (d, m) => {
        drop = applyTemplateDrop(d, m, p.template, values, imageProfiles, p.at);
        span = { before: m.te, after: readDoc({ ...d, files: drop.files }).te };
        return drop.files;
      });
      if (reason || !drop) return;
      const done: TemplateDrop = drop;
      rememberAdd({ kind: "template", id: p.template.id, label: p.template.label });
      // The free layout: every node of the fragment near the drop point, the hosts included (the
      // template's layout names only some of them), so nothing it made is left at the origin.
      const made = span as { before: DocModel["te"]; after: DocModel["te"] } | null;
      const positions = layoutModeRef.current === "free" && made && p.at ? placeDropped(made.before, made.after, p.at, done.positions, { ...settled.current, ...pinsRef.current }, { clear: true }) : done.positions;
      if (Object.keys(positions).length) onPinMany(positions);
      if (done.ports.length) setHighlight((h) => ({ names: done.ports, n: (h?.n ?? 0) + 1 }));
    },
    [pendingTemplate, edit, imageProfiles, onPinMany],
  );
  const commitDrop = useCallback(
    (c: DropCommit) => {
      const p = pendingDrop;
      setPendingDrop(null);
      if (!p) return;
      if (c.kind === "tap") {
        if (edit(`tap on ${c.segment}`, (d, m) => applyTapDrop(d, m, c.segment, c.sensors).files) === null) rememberAdd(p.item);
        return;
      }
      const s = c.spec;
      let placed: string[] = [];
      let span: { before: DocModel["te"]; after: DocModel["te"] } | null = null;
      const where = "segment" in s.target ? ` on ${s.target.segment}` : "router" in s.target ? ` under ${s.target.router}` : "";
      const label = `add ${p.item.label}${s.role ? ` (${s.role})` : ""}${s.count > 1 ? ` ×${s.count}` : ""}${where}`;
      const reason = edit(label, (d, m) => {
        const r = applyDeviceDrop(d, m, s);
        placed = r.names;
        span = { before: m.te, after: readDoc({ ...d, files: r.files }).te };
        return r.files;
      });
      if (reason) return;
      const rolePersona = s.role ? personas.find((x) => x.id === s.role) : undefined;
      rememberAdd({ ...p.item, card: s.card, role: s.role, os: s.os, label: rolePersona ? personaLabel(rolePersona) : s.role ?? p.item.label });
      // A device dropped on the pane stands where it landed; a host on a segment hangs off it.
      const first = placed[0];
      const fixed: Record<string, { x: number; y: number }> = p.at && first && !("segment" in s.target) ? { [`host:${first}`]: { x: Math.round(p.at.x), y: Math.round(p.at.y) } } : {};
      // The free layout: everything the drop made -- its new segment's implied switch, the peers --
      // stands near the drop point too (2026-11-20: they landed at the canvas's top-left corner).
      const made = span as { before: DocModel["te"]; after: DocModel["te"] } | null;
      // A drop on the pane never lands on a box already there: the group moves clear (2026-11-21).
      if (layoutModeRef.current === "free" && made && p.at) onPinMany(placeDropped(made.before, made.after, p.at, fixed, { ...settled.current, ...pinsRef.current }, { clear: "pane" in s.target }));
      else if (fixed[`host:${first}`]) onPin(`host:${first}`, fixed[`host:${first}`]!);
    },
    [pendingDrop, edit, onPin, onPinMany, personas],
  );

  // Duplicate subtree (§6.2, B3): the copy keeps the original's shape -- its root at the drop point, or
  // beside the original when there is none (Enter, a device, the panel) -- its devices, implied switches
  // and hosts around it (they had landed far from their switch), clear of every box already there.
  const onDuplicate = useCallback(
    (root: string, attachTo: string | null, drop: { at?: { x: number; y: number } | null; prefix?: string | null; sameUplinks?: boolean }) => {
      let r: { result: DuplicateResult; model: DocModel; after: DocModel["te"] } | null = null;
      const prefix = drop.prefix ?? null;
      const label = `duplicate ${root}'s subtree${attachTo ? ` onto ${attachTo}` : drop.sameUplinks ? " with the same uplinks" : ""}${prefix ? ` under ${prefix}` : ""}`;
      if (edit(label, (d, m) => { const out = duplicateSubtree(d, m, root, attachTo, { prefix, sameUplinks: Boolean(drop.sameUplinks) }); r = { result: out, model: m, after: readDoc({ ...d, files: out.files }).te }; return out.files; }) !== null || !r) return;
      const done: { result: DuplicateResult; model: DocModel; after: DocModel["te"] } = r;
      const known = { ...settled.current, ...pinsRef.current };
      const at = drop.at ?? besideSubtree(done.model, root, known);
      if (at) onPinMany(clearOf(copyPositions(done.model, root, done.result, known, at), known, done.after));
      setSelection({ kind: "subtree", root: done.result.root });
    },
    [edit, onPinMany],
  );

  // ---- exits ----
  const files = editor.files;
  const filesRef = useRef(files);
  filesRef.current = files;
  const dirty = deployed ? Boolean(config?.dirty) || editor.dirty : editor.dirty;
  // The gate (`edit/check.ts`): the last Check passed AND the files on screen are the ones it judged.
  const valid = checkPasses(check, files);
  const checkWord = checking ? "checking…" : checkBadge(check, files);
  const checkState = checkStatus(check, files);
  const gateReason = valid ? null : checkState === "unchecked" ? "run Check first (Ctrl+Enter): a save needs a passing Check" : checkState === "changed" ? "the files changed since the last Check: Check again" : "the last Check failed: fix the findings, or Save as draft";
  // A deployed twin's edits not yet pushed to its working copy (Apply / Persist push them).
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
  // The server validates again on every write: a 422 lands as the Check's findings (on the files
  // it refused), never as a rollback of the edit.
  const asFindings = useCallback(
    (e: unknown, written: Files) => {
      const m = editor.model;
      const refused = refusalAsCheck(e, written, m ? localFindings(m, parkedDevices(m.te), null) : []);
      if (refused) setCheck(refused);
    },
    [editor.model],
  );
  const save = (rec: CheckRecord | null = check) =>
    run("save", async () => {
      if (!files) return null;
      if (!checkPasses(rec, files)) throw new Error(rec && checkStatus(rec, files) === "invalid" ? "the Check failed: fix the findings, or Save as draft" : (gateReason ?? "run Check first"));
      try {
        const d = await api.putTwinConfig(id, { id, files });
        setDetail(d);
      } catch (e) {
        asFindings(e, files);
        throw e;
      }
      editor.markSaved(files);
      const layoutSaved = layoutChanges > 0 ? await saveLayout() : null;
      return layoutSaved === null ? "saved" : layoutSaved ? "saved, layout too" : "saved; the layout was not (see above)";
    });
  const saveAs = () =>
    run("save-as", async () => {
      if (!editor.doc || !files) return null;
      if (!checkPasses(check, files)) throw new Error(gateReason ?? "run Check first");
      const newId = saveAsId.trim();
      if (!newId) throw new Error("give the new twin-config an id");
      try {
        await api.createTwinConfig({ id: newId, files: saveAsFiles(editor.doc, newId) });
      } catch (e) {
        asFindings(e, files);
        throw e;
      }
      // The layout as it stands on screen -- the saved pins AND the unsaved moves
      // and annotations -- goes with the new twin, so Save-As drops no draft.
      let carried = true;
      if (draft.doc || layoutChanges > 0) {
        try {
          await api.putLayout(newId, layoutWrite(null, newId, pins, annotations.map(toLayout), draft.mode));
          draft.discard();
        } catch {
          carried = false;
        }
      }
      setSaveAsId("");
      navigate(`/canvas/${encodeURIComponent(newId)}`);
      return `saved as ${newId}${layoutChanges > 0 ? (carried ? ", layout draft carried over" : "; the layout draft could not be written there") : ""}`;
    });
  // The untitled design's first Save: a Save-As under the id the prompt took, its layout draft and its
  // undo stack carried to the new id, then the named canvas -- clean, its Save a plain Save from now on.
  const saveUntitled = (newId: string, description: string) =>
    run("save-as", async () => {
      if (!editor.doc || !files) return null;
      if (!checkPasses(check, files)) throw new Error(gateReason ?? "run Check first");
      const written = saveAsFiles(editor.doc, newId, { description });
      try {
        await api.createTwinConfig({ id: newId, files: written });
      } catch (e) {
        asFindings(e, files);
        throw e;
      }
      let carried = true;
      const hasLayout = Object.keys(pins).length > 0 || annotations.length > 0;
      if (hasLayout) {
        try {
          await api.putLayout(newId, layoutWrite(null, newId, pins, annotations.map(toLayout), draft.mode));
        } catch {
          carried = false;
        }
      }
      // The stack under the new id, every command's text renamed as the files were, so Undo still fits.
      const rename = <T extends string | undefined>(f: Record<string, T>) => renamedFiles(f, editor.doc!.teFile, newId, { description });
      writeStoredStack(`twin:${newId}`, {
        done: editor.stack.done.map((c) => ({ ...c, before: rename(c.before), after: rename(c.after) })),
        undone: editor.stack.undone.map((c) => ({ ...c, before: rename(c.before), after: rename(c.after) })),
      });
      leaving.current = true;
      leavingLayout.current = true;
      clearUntitled();
      editor.markSaved(files);
      setSaveAsOpen(false);
      navigate(`/canvas/${encodeURIComponent(newId)}`, { replace: true });
      return `saved as ${newId}${hasLayout && !carried ? "; the layout draft could not be written there" : ""}`;
    });
  // ---- drafts (`api/Spec.md` A3d) ----
  // The twin a draft written from here may later replace: this twin, or a draft's own origin; an untitled design has none.
  const draftOrigin = isDraft ? (data?.draftRow?.origin ?? null) : untitled ? null : id;
  // The last Check, as the draft records it: only when it judged exactly these files.
  const lastCheck = (written: Files | null): DraftCheck | null => {
    if (!written || !check?.result || checkStatus(check, written) === "changed") return null;
    return { disposition: check.result.disposition, errors: check.result.errors, warnings: check.result.warnings, checked_at: new Date().toISOString().replace(/\.\d{3}Z$/, "Z") };
  };
  // Save as draft: always open. A COPY under a new id, written without the validation gate; the
  // twin (or working copy, or draft) on screen is never touched. The page moves to the draft.
  const saveAsDraft = (newId: string) =>
    run("save-draft", async () => {
      if (!editor.doc || !files) return null;
      const written = saveAsFiles(editor.doc, newId);
      await api.createDraft({ id: newId, files: written, origin: draftOrigin, last_check: lastCheck(files) });
      // The layout on screen goes with the draft (into this browser: a draft has no side-car).
      writeUntitledLayout(pins, annotations.map(toLayout), draftScope(newId), draft.mode);
      if (untitled) {
        leaving.current = true;
        leavingLayout.current = true;
        clearUntitled();
      }
      setDraftOpen(false);
      navigate(`/canvas/drafts/${encodeURIComponent(newId)}`);
      return `saved as draft ${newId}${draftOrigin ? `; ${draftOrigin} is not changed` : ""}`;
    });
  // On a draft: Save draft rewrites it (no gate, nothing else touched).
  const saveDraft = () =>
    run("save", async () => {
      if (!files) return null;
      const d = await api.putDraft(id, { id, files, last_check: lastCheck(files) });
      editor.markSaved(files);
      return `draft saved${d.last_check ? ` · last check ${d.last_check.disposition.toLowerCase()}` : ""}`;
    });
  // On a passing draft: Replace <origin>, the twin's own A3 `PUT` (which validates again and
  // snapshots the old baseline), after the explicit confirm. The draft is kept.
  const replaceOrigin = () =>
    run("replace", async () => {
      const origin = data?.draftRow?.origin;
      if (!origin || !editor.doc || !files) return null;
      if (!checkPasses(check, files)) throw new Error(gateReason ?? "run Check first");
      const written = saveAsFiles(editor.doc, origin);
      try {
        await api.putTwinConfig(origin, { id: origin, files: written });
      } catch (e) {
        asFindings(e, files);
        throw e;
      } finally {
        setReplaceOpen(false);
      }
      return `replaced ${origin} with this draft (its old baseline is kept in its history); the draft is kept`;
    });
  const applyExit = () =>
    run("apply", async () => {
      if (!deployId || !files) return null;
      if (!checkPasses(check, files)) throw new Error(gateReason ?? "run Check first");
      // Push the edits on screen to the working copy first (the API validates and classifies them), then apply.
      if (unsynced) {
        try {
          const cfg = await api.putDeployConfig(deployId, files);
          synced.current = { ...files };
          setConfig(cfg);
          editor.markSaved(files);
          if (!cfg.apply_allowed) return `pushed to the working copy, not applied: ${cfg.changes.some((c) => c.kind === "topology") ? "a pending change needs a re-plan (Persist or Save As, then redeploy)" : "nothing in it is live"}`;
        } catch (e) {
          asFindings(e, files);
          throw e;
        }
      }
      const r = await api.applyDeployConfig(deployId);
      setConfig(r.config);
      return `applied${r.applied.length ? ` via ${r.applied.join(", ")}` : ""}${Object.keys(r.roles).length ? ` · roles ${Object.entries(r.roles).map(([n, ro]) => `${n} → ${ro}`).join(", ")}` : ""}`;
    });
  // Start tap / Start tfc (2026-11-09): one follow-on tier from the twin's catalog default, detached;
  // the pill follows it from the te row's `follow_on` on the poll.
  const startTierExit = (tier: string) =>
    run(`start ${tier}`, async () => {
      if (!deployId) return null;
      await api.startTier(deployId, tier);
      await refresh();
      return `${tier} queued: it stands up from the twin's catalog default`;
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
  const persist = (rec: CheckRecord | null = check) =>
    run("persist", async () => {
      if (!deployId || !files) return null;
      if (!checkPasses(rec, files)) throw new Error(rec && checkStatus(rec, files) === "invalid" ? "the Check failed: fix the findings, or Save as draft" : (gateReason ?? "run Check first"));
      try {
        // Push the edits on screen to the working copy, then make it the baseline.
        if (unsynced) {
          await api.putDeployConfig(deployId, files);
          synced.current = { ...files };
        }
        await api.putTwinConfig(id, { id, files });
      } catch (e) {
        asFindings(e, files);
        throw e;
      }
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
        // The files as they are NOW (a split-and-assert edits and promotes in one click; this runs after its re-render).
        // What the promote asserted now reads `asserted` in the re-cast baseline; the working copy takes that mark.
        const base = await api.getTwinConfig(id).catch(() => null);
        const latest = filesRef.current && base?.document ? adoptBaselineProvenance(filesRef.current, base.document.files, base.document.te_file) : filesRef.current;
        if (deployId && latest) await api.putDeployConfig(deployId, latest).catch(() => null);
        reload();
        return `promoted: ${what}; the twin was re-cast and the working copy re-opened`;
      }),
    [api, pit, id, deployId, reload, run],
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

  // A member split (§5) is an override `field: group` per member, keyed by entity key. The roster's
  // members are the group's observed keys, sorted; the expander numbers instances 01..NN in that
  // order, so instance i is the i-th key. A roster whose size is not the group's count (a count
  // edited by hand, an older cast) keys nothing reliably and is refused.
  const splitBlock = useCallback(
    (node: string): string | null => {
      if (pit === null) return "this twin was not cast from an imprint; there is nothing to assert on";
      if (!cast) return "the cast roster could not be read; nothing to key an assertion by";
      if (!membersOf(node).length) return `${node} has no observed members in the cast (added by hand, synthetic, or an older cast); nothing to key the split by`;
      return null;
    },
    [pit, cast, membersOf],
  );
  // The keys are the observed hosts the operator named: the cast ties no instance to a host.
  const onPromoteSplit = useCallback(
    (node: string, keys: string[], name: string) => {
      const known = new Set(membersOf(node));
      if (!keys.length || keys.some((k) => !known.has(k))) return;
      const note = `promoted from the design canvas: split ${keys.length} of ${node} off as ${name}`;
      void promote({ override: keys.map((key) => ({ key, field: "group", value: name, note })) }, `${keys.length} of ${node} are ${name}`);
    },
    [membersOf, promote],
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
  // that file back to the baseline's text, as an edit (undoable; pushed by Apply / Persist).
  const baselineFiles = detail?.document?.files ?? null;
  const onDiscardChange = useCallback(
    (path: string) => {
      if (!baselineFiles) return;
      edit(
        `discard changes to ${path}`,
        (d) => {
          const files = { ...d.files };
          const base = baselineFiles[path];
          if (base === undefined) delete files[path];
          else files[path] = base;
          return files;
        },
        // The baseline's own text, provenance and all: nothing to stamp.
        { stamp: false },
      );
    },
    [edit, baselineFiles],
  );

  const notes = useMemo(() => {
    const out: string[] = [];
    if (layout.error) out.push(`positions are not saved for this twin: ${errorText(layout.error)}`);
    else if (draft.doc && draft.doc.exists && draft.doc.plane !== CANVAS_PLANE) out.push(`the saved positions were drawn on another plane (${draft.doc.plane ?? "unstamped"}) and are not applied; the next Save layout saves on this one`);
    if (built?.kind === "authored") out.push("authored twin: no observed evidence, no traffic to overlay");
    // An untitled design's state is on its pill ("untitled design · not saved yet") and in the layout
    // bar: it is not a note, so a brand-new design's Notes tab counts none (2026-11-20, round 2).
    if (built?.kind === "design" && !untitled) out.push(isDraft ? `design view: a draft${data?.draftRow?.origin ? ` of ${data.draftRow.origin}` : ""} (never deployed; its layout is kept in this browser)` : deployed ? "design view: the working copy (edits stay in this browser until Apply or Persist)" : "design view: the twin config");
    if (built && built.graph.castOmits.length) out.push(`the cast did not publish: ${built.graph.castOmits.join(", ")}`);
    if (data && !designAvailable) out.push(whoami.can_write ? "read-only: the catalog serves no document for this twin" : "read-only: the API serves this twin's document only to a caller that may write; sign in to edit it");
    if (placement?.unplaced) out.push(`${placement.unplaced} local entit${placement.unplaced === 1 ? "y" : "ies"} in no segment: observed on this network but bound to no subnet the cast could place`);
    if (annotations.length) out.push(`${annotations.length} annotation${annotations.length === 1 ? "" : "s"} in the layout side-car`);
    const legacy = editor.doc && model ? legacyLayout(editor.doc, model) : [];
    if (legacy.length) out.push(`legacy cast layout: read through ${legacy.join(", ")} (a re-cast files it as te.yaml, tfc/default.yaml, tap/default.yaml)`);
    return out;
  }, [layout.error, draft.doc, built, data, designAvailable, deployed, untitled, isDraft, annotations.length, whoami.can_write, placement?.unplaced, editor.doc, model]);

  // The last Check's findings pinned on the elements they name, while they describe the model on
  // screen (a check of other files still pins: the badge says "changed since check").
  const pins_ = useMemo(() => (model && check?.result ? findingPins(check.result.findings, model) : new Map()), [model, check]);
  // A finding clicked in the Checks tab: select the element it names and zoom to it.
  const onFinding = useCallback(
    (f: ValidationFinding) => {
      if (!model) return;
      const t = findingTarget(f, model);
      if (!t) return;
      if (t.kind === "conduit") {
        const c = conduitOf(model, t);
        if (c) setSelection({ kind: "conduit", index: c.index });
      } else setSelection(t.kind === "node" ? { kind: "node", names: [t.name] } : t.kind === "segment" ? { kind: "segment", names: [t.name] } : t.kind === "link" ? { kind: "link", index: t.index } : { kind: "tap", index: t.index });
      setFocus((cur) => ({ key: targetKey(t), n: (cur?.n ?? 0) + 1 }));
    },
    [model],
  );

  const editProps = useMemo<CanvasEditProps | undefined>(() => {
    if (!model || built?.kind !== "design") return undefined;
    return {
      model,
      locked: Boolean(locked),
      selection,
      onSelection: setSelection,
      onPlace,
      onAttach: (node, segment) => void edit(`attach ${node} to ${segment}`, (d, m) => attach(d, m, node, segment)),
      onMove: (node, segment) => void edit(`move ${node} to ${segment}`, (d, m) => moveTo(d, m, node, segment)),
      onDetach: (node, segment) => void edit(`detach ${node} from ${segment}`, (d, m) => detach(d, m, node, segment)),
      onRehome: (node, from, to) => void edit(`re-home ${node}: ${from} → ${to}`, (d, m) => rehome(d, m, node, from, to)),
      onRemoveNode: (name) => void edit(`remove ${name}`, (d, m) => removeNode(d, m, name)),
      onRemoveSegment: (name) => void edit(`remove segment ${name}`, (d, m) => removeSegment(d, m, name)),
      onAddSegment,
      onInsertSwitch: (segment) => {
        let made: { name: string; skipped: string[] } | null = null;
        if (edit(`insert switch on ${segment}`, (d, m) => { const out = insertSwitch(d, m, segment); made = out; return out.files; }) !== null || !made) return;
        const done: { name: string; skipped: string[] } = made;
        setSelection({ kind: "node", names: [done.name] });
      },
      onAddConduit: (from, to) => void edit(`declare conduit ${from} ↔ ${to}`, (d, m) => addConduit(d, m, from, to)),
      onConnectIntent: (intent, at) => {
        if (intent.kind === "refused") {
          setBlocked(intent.reason);
          return;
        }
        const label = intent.kind === "transit" ? `connect ${intent.a} — ${intent.b} (transit ${intent.cidr})` : intent.kind === "lan" ? `new segment ${intent.name} on ${intent.router}` : intent.kind === "attach" ? `attach ${intent.node} to ${intent.segment}` : intent.kind === "gateway" ? `${intent.router} gateway on ${intent.switch}` : `link ${intent.a} — ${intent.b}`;
        if (edit(label, (d, m) => applyIntent(d, m, intent)) !== null) return;
        // A new LAN stands where it was dropped: its implied switch's pin.
        if (intent.kind === "lan" && at) onPin(`segment:${intent.cidr}`, at);
        if (intent.kind === "transit" || intent.kind === "lan") setSelection({ kind: "segment", names: [intent.name] });
      },
      onWireRouters: (routers) => setWireDialog({ routers, shape: "chain", ospf: false, area: 0, switchImage: true }),
      onMoveTap: (index, segment) => void edit(`move tap ${index} to ${segment}`, (d, m) => moveTap(d, m, index, segment)),
      onRetargetTap: (index, sel) => void edit(`move tap ${index} to ${sel.scope} ${sel.scope === "boundary" ? sel.target.join(" ↔ ") : sel.scope === "port" ? `${sel.target} (${sel.segment})` : sel.target}`, (d, m) => retargetTap(d, m, index, sel)),
      onMoveMember: (from, index, into) => void edit(`move ${from}${String(index).padStart(2, "0")} to ${into}`, (d, m) => moveMember(d, m, from, index, into)),
      onDuplicateSubtree: (root, drop) => onDuplicate(root, drop?.attachTo ?? null, drop ?? {}),
      onLink: (from, to) => void edit(`link ${from} — ${to}`, (d, m) => attachDevices(d, m, from, to)),
      annotations,
      onAnnotations: setAnnotations,
      highlight,
      onSaveTemplate: () => {
        // With what hangs under the selected devices (the default), and the bare selection the dialog's option falls back to.
        const proposal = proposeTemplate(model, selectionRef.current, { under: true });
        const bare = proposal.under ? proposeTemplate(model, selectionRef.current, { under: false }) : null;
        setSaveTpl({ proposal, bare, stripped: carriedBindings(model, proposal.set.hosts, personas).stripped, bareStripped: bare ? carriedBindings(model, bare.set.hosts, personas).stripped : [], busy: false, error: null });
      },
    };
  }, [model, built, locked, selection, onPlace, edit, onAddSegment, annotations, setAnnotations, onPin, highlight, personas, onDuplicate]);

  const mode = !designAvailable ? "read-only" : locked ? "view" : "edit";
  const overBudget = Boolean(built && placement && placement.overBudget);
  // The per-tier state: the te row from the load, the tier rows from the poll.
  // The te row as the poll last read it (the page's load holds the one it opened with).
  const teRow = useMemo(() => (data?.deploy ? (deployRows.find((d) => d.deploy_id === data.deploy!.deploy_id) ?? data.deploy) : null), [deployRows, data?.deploy]);
  const tiers = useMemo(() => (teRow ? tierStatus(deployRows, teRow.deploy_id) : {}), [deployRows, teRow]);
  // A baseline re-apply acts on a tier whose playout row failed; a follow-on that failed with no row is started again instead.
  const tierFailed = Object.entries(teRow ? tierStatus(deployRows, teRow.deploy_id, { rowsOnly: true }) : {}).filter(([, st]) => st === "failed").map(([t]) => t);
  const anyFailed = teRow?.status === "failed" || Object.values(tiers).includes("failed");
  // Start tap / Start tfc: a completed te with no standing deploy of the tier, nothing queued or running for it.
  const startable = teRow?.status === "completed" && !destroying ? FOLLOW_ON_TIERS.filter((t) => !["completed", "running", "pending"].includes(tiers[t] ?? "")) : [];
  const state = docState(config, editor.dirty, unsynced);
  const exit = config?.exit ?? null;
  const exitWord = destroying ? destroyWord(destroyRun) : exit ? `${exit.kind === "reset" ? "resetting" : "applying"}… ${exit.tier ? `replaying ${exit.tier}` : "starting"} · ${Math.round(exit.elapsed_s)} s${exit.cancel_requested ? " · cancel requested" : ""}` : busy ? `${busy}…` : null;
  // Unsaved work asks before it is left: an untitled design with anything on it (it exists only in this
  // browser), an undeployed design or a draft with unsaved changes, or a deployed twin's edits not yet
  // pushed (kept in this browser, but not on the twin).
  // The one "is anything unsaved" rule the guard and the layout bar share: an untitled design is
  // clean when its document is at its blank start (or a clone's: never clean) and it has no live pin
  // and no annotation -- a pin for a node that no longer exists does not count (`isLive`).
  const untitledWork = untitled && (editor.dirty || Object.keys(draft.livePins).length > 0 || annotations.length > 0);
  useLeaveGuard(
    untitledWork || (!untitled && !deployed && editor.dirty) || unsynced,
    untitled ? "This untitled design is kept only in this browser and is not saved to the catalog. Leave and discard it?" : unsynced ? "The working copy has edits not pushed to the twin (Apply or Persist push them); they stay in this browser. Leave anyway?" : "The design has unsaved changes. Leave without saving them?",
    untitled
      ? () => {
          leaving.current = true;
          leavingLayout.current = true;
          clearUntitled();
        }
      : undefined,
  );

  // The layout group floats on the canvas, not in the twin bar (SPEC.md "Shell and chrome"): the bar holds the document's exits only.
  const layoutBar = layout.data ? (
    <span className="row cv-layout cv-float" role="group" aria-label="Layout" data-testid="layout-bar">
      {layoutChanges > 0 ? (
        <span className="cv-dirty" data-testid="layout-dirty" title="moves and annotations are written only by Save layout">
          ● layout: {layoutChanges} unsaved {layoutChanges === 1 ? "change" : "changes"}
        </span>
      ) : untitled ? (
        <span className="muted">layout: written with the design's first Save</span>
      ) : isDraft ? (
        <span className="muted">layout: kept in this browser</span>
      ) : draft.savedAt ? (
        <span className="muted" role="status">
          layout saved
        </span>
      ) : (
        <span className="muted">layout</span>
      )}
      {browserLayout ? null : (
        <>
          <button type="button" className="primary" onClick={() => void saveLayout()} disabled={layoutChanges === 0 || draft.saving} title="write the positions and annotations to the layout side-car">
            {draft.saving ? "Saving…" : "Save layout"}
          </button>
          <button type="button" onClick={draft.discard} disabled={layoutChanges === 0 || draft.saving} title="back to the layout as last saved">
            Discard
          </button>
        </>
      )}
      {built?.kind === "design" ? (
        <span className="cv-exit-group" role="group" aria-label="Layout mode" data-testid="layout-mode" data-mode={layoutMode}>
          <span className="muted">Layout:</span>
          {(["tree", "free"] as const).map((m) => (
            <button key={m} type="button" aria-pressed={layoutMode === m} onClick={() => { if (layoutMode !== m) { settled.current = {}; draft.setMode(m); } }} title={m === "tree" ? "the device tree: routers, switches and hosts laid out by branch, edges routed through the gutters" : "free: every box stays where you drop it, edges drawn point to point"}>
              {m}
            </button>
          ))}
        </span>
      ) : null}
      <button
        type="button"
        data-testid="relayout"
        onClick={() => {
          settled.current = {};
          draft.relayout();
        }}
        disabled={draft.saving || (Object.keys(pins).length === 0 && Object.keys(settled.current).length === 0)}
        title="drop every moved position and let the tree lay itself out again (Save layout keeps it)"
      >
        Re-layout
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
          {untitled ? (
            <span title={origin.source ? `a copy of ${origin.source}, not saved to the catalog yet: the first Save names it (${origin.source} is not changed)` : "not saved to the catalog yet: the first Save names it"}>Untitled</span>
          ) : isDraft ? (
            <span title={`draft ${id}${data?.draftRow?.origin ? ` of ${data.draftRow.origin}` : ""}: kept without the validation gate, never deployed`}>
              {id}
              <span className="cv-draft-label" data-testid="draft-label">draft</span>
            </span>
          ) : (
            <Link to={`/twins/${encodeURIComponent(id)}`} title={`Twin ${id}: the summary page`}>
              {id}
            </Link>
          )}
        </h1>
        <button type="button" className="cv-open" onClick={() => setOpenDialog(true)} title="open a design from the catalog (Ctrl+O)">
          Open…
        </button>
        {/* One pill: the origin, the mode and the deploy's state PER TIER (te / tfc / tap), red when a tier failed
            -- the te tier alone read "completed" over a failed traffic playout. The bar must not wrap at 1622 px. */}
        {built || data ? (
          <span className={`badge cv-pill${mode === "read-only" ? " read-only" : ""}${anyFailed ? " failed" : ""}`} title={teRow ? `deploy ${teRow.deploy_id}: te ${teRow.status}${Object.entries(tiers).map(([t, st]) => ` · ${t} ${st}${tierReason(deployRows, teRow.deploy_id, t) ? ` (${tierReason(deployRows, teRow.deploy_id, t)})` : ""}`).join("")}` : "no deploy stands on this twin"} data-testid="state-pill">
            {built ? <span>{built.kind === "cast" ? "cast · observed evidence" : built.kind === "authored" ? "authored" : untitled ? (origin.source ? `untitled · copy of ${origin.source}` : "untitled design") : isDraft ? (data?.draftRow?.origin ? `draft of ${data.draftRow.origin}` : "draft") : deployed ? "working copy" : "twin config"}</span> : null}
            {built && data ? " · " : null}
            {data ? (
              <span data-testid="mode">
                {mode}
                {data.deploy ? (
                  <>
                    {" · "}
                    <span className="mono cv-pill-id" title={data.deploy.deploy_id}>{data.deploy.deploy_id}</span>
                    <span className={`cv-tier${teRow!.status === "failed" ? " failed" : ""}${destroyRun?.down.includes("te") ? " down" : ""}`} data-testid="tier-te" data-status={teRow!.status} data-down={destroyRun?.down.includes("te") || undefined} title={`te ${teRow!.status}`}> · te {destroyRun?.down.includes("te") ? "✓ down" : tierWord(teRow!.status)}</span>
                    {Object.entries(tiers).map(([t, st]) => (
                      <span key={t} className={`cv-tier${st === "failed" ? " failed" : ""}${destroyRun?.down.includes(t) ? " down" : ""}`} data-testid={`tier-${t}`} data-status={st} data-down={destroyRun?.down.includes(t) || undefined} title={`${t} ${st}${tierReason(deployRows, teRow!.deploy_id, t) ? `: ${tierReason(deployRows, teRow!.deploy_id, t)}` : ""}`}> · {t} {destroyRun?.down.includes(t) ? "✓ down" : tierWord(st)}</span>
                    ))}
                  </>
                ) : untitled ? (
                  " · not saved yet"
                ) : isDraft ? (
                  " · never deployed"
                ) : (
                  " · undeployed"
                )}
              </span>
            ) : null}
          </span>
        ) : null}
        {data?.deploy?.target ? (
          <span className="badge cv-target-chip" data-testid="target-chip" title={`deployed on target ${data.deploy.target}`}>
            {data.deploy.target}
            {(() => {
              const t = (targetsLoad.data ?? []).find((x) => (x.name ?? x.node) === data.deploy!.target);
              return t?.platform ? ` · ${t.platform}` : "";
            })()}
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
              <span className={`cv-dirty${state === "applied" ? " applied" : ""}`} data-testid="dirty-marker" data-state={state} title={deployed ? (state === "applied" ? "every change in the working copy was applied to the running twin; the baseline does not have them yet (Persist)" : state === "syncing" ? "the edits are in this browser only: Apply or Persist pushes them (each needs a passing Check)" : "the working copy carries changes the twin does not run yet (Apply)") : "unsaved changes"}>
                ● {deployed ? (state === "syncing" ? "local edits (not pushed)" : state === "applied" ? "applied · unpersisted" : "unapplied changes") : "unsaved changes"}
              </span>
            ) : (
              <span className="cv-clean" data-testid="clean-marker" data-state="clean">
                {untitled ? "not saved yet" : isDraft ? "draft saved" : deployed ? "working copy equals the baseline" : "saved"}
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
            {/* Check (on demand, Ctrl+Enter): the only validation; its badge says whether the files on screen are the ones it passed. */}
            <span className="cv-exit-group" role="group" aria-label="Check">
              <button type="button" onClick={() => void runCheck()} disabled={checking || !files} data-testid="check-run" title="validate the files on screen (Ctrl+Enter)">
                {checking ? "Checking…" : "Check"}
              </button>
              <span className={`cv-check-badge ${checking ? "checking" : checkState}`} role="status" data-testid="check-badge" data-status={checking ? "checking" : checkState}>
                {checkWord}
              </span>
            </span>
            {isDraft ? (
              <span className="cv-exit-group" role="group" aria-label="Draft">
                <button type="button" className="primary" onClick={saveDraft} disabled={!dirty || busy !== null} data-testid="save-draft" title="write the draft (no validation gate; nothing else is touched)">
                  Save draft
                </button>
                {data?.draftRow?.origin ? (
                  <button type="button" onClick={() => { setExitError(null); setReplaceOpen(true); }} disabled={!valid || busy !== null} data-testid="replace-origin" title={gateReason ?? `overwrite ${data.draftRow.origin} with this draft (asks first)`}>
                    Replace {data.draftRow.origin}
                  </button>
                ) : null}
              </span>
            ) : !deployed ? (
              <span className="cv-exit-group" role="group" aria-label="Twin config">
                {untitled ? (
                  <button type="button" className="primary" onClick={() => {
                      setExitError(null);
                      setSaveAsOpen(true);
                    }} disabled={!valid || busy !== null} title={gateReason ?? "name the design and write it to the catalog"} data-testid="save-untitled">
                    Save
                  </button>
                ) : (
                  <button type="button" className="primary" onClick={() => void save()} disabled={!dirty || !valid || busy !== null} title={gateReason ?? "overwrite the twin with the files on screen"} data-testid="save">
                    Save
                  </button>
                )}
                {whoami.can_write && !untitled ? (
                  <button type="button" onClick={() => setDeployOpen(true)} disabled={busy !== null} data-testid="deploy-open" title="plan and deploy the saved twin on a target">
                    Deploy…
                  </button>
                ) : null}
              </span>
            ) : (
              <span className="cv-exit-group" role="group" aria-label="Working copy">
                <button type="button" className="primary" onClick={applyExit} disabled={!(config?.dirty || unsynced) || (!unsynced && !config?.apply_allowed) || !valid || busy !== null || Boolean(locked) || Boolean(exit) || destroying} title={gateReason ?? (topologyPending ? "a pending change needs a re-plan: Persist or Save-As, then redeploy" : "push the edits to the working copy and onto the running twin")}>
                  Apply
                </button>
                <button type="button" onClick={() => void resetExit()} disabled={!(config?.dirty || unsynced) || busy !== null || Boolean(locked) || Boolean(exit) || destroying} title="discard the working copy and the edits in this browser; what it applied is replayed from the baseline">
                  Reset
                </button>
                <button type="button" onClick={() => void persist()} disabled={!(config?.dirty || unsynced) || !valid || busy !== null || Boolean(locked) || Boolean(exit) || destroying} title={gateReason ?? "push the edits to the working copy and make it the baseline"}>
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
                {startable.map((t) => (
                  <button key={t} type="button" onClick={() => void startTierExit(t)} disabled={busy !== null || Boolean(exit)} data-testid={`start-${t}`} title={`no ${t} deploy stands on this twin: stand it up from the twin's catalog default (${t === "tap" ? "its tap plan" : "its traffic profile"})${tierReason(deployRows, teRow!.deploy_id, t) ? ` · last: ${tierReason(deployRows, teRow!.deploy_id, t)}` : ""}`}>
                    Start {t}
                  </button>
                ))}
                <button type="button" className="danger" onClick={() => setDestroyOpen(true)} disabled={busy !== null || Boolean(exit) || destroying} data-testid="destroy-open" title="bring the standing deploy down: te, with its tfc and tap (the tap's sensor binding is released)">
                  Destroy…
                </button>
                {topologyPending ? (
                  <Link to={`/twins/${encodeURIComponent(id)}`} data-testid="redeploy-link" title="a topology change needs a new plan: persist it, then deploy the baseline again">
                    Redeploy from baseline
                  </Link>
                ) : null}
              </span>
            )}
            {untitled ? null : (
            <form
              className="cv-saveas cv-exit-group"
              aria-label="Save as"
              onSubmit={(e) => {
                e.preventDefault();
                void saveAs();
              }}
            >
              <input type="text" aria-label="Save as id" placeholder="save as id" value={saveAsId} onChange={(e) => setSaveAsId(e.target.value)} disabled={busy !== null} />
              <button type="submit" disabled={!saveAsId.trim() || !valid || busy !== null} title={gateReason ?? "a new twin under this id"}>
                Save as
              </button>
            </form>
            )}
            {whoami.can_write ? (
              <button type="button" className="cv-exit-group" onClick={() => { setExitError(null); setDraftOpen(true); }} disabled={busy !== null || !files} data-testid="save-as-draft" title={`keep a copy under a new id without the validation gate${draftOrigin ? `; ${draftOrigin} is not changed` : ""}`}>
                Save as draft…
              </button>
            ) : null}
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
      {deployOpen && !data?.deploy ? (
        <div className="modal-backdrop" data-testid="deploy-backdrop" onClick={(e) => e.stopPropagation()}>
          <div className="modal panel" role="dialog" aria-modal="true" aria-label={`Deploy ${id}`} data-testid="deploy-dialog">
            <h2 style={{ marginTop: 0 }}>Deploy {id}</h2>
            <DeployFlow
              twinId={id}
              hold={dirty ? { reason: "The design has unsaved changes; the deploy runs the saved twin. Save checks it first and saves only on a pass.", action: { label: "Save", run: async () => { const rec = await runCheck(); await save(rec); } } } : null}
              remap={async (location, to) => {
                const reason = edit(`remap sensor at ${location} to ${to}`, (d) => remapFiles(d.files, location, to));
                if (reason) throw new Error(reason);
              }}
              remapNote="Remap edits the design's tap plan; Save it, then plan again."
              onDeployed={() => {
                setDeployOpen(false);
                reload();
              }}
            />
            <div className="row-actions">
              <button type="button" onClick={() => setDeployOpen(false)}>
                Close
              </button>
            </div>
          </div>
        </div>
      ) : null}
      {openDialog ? <OpenDesignDialog onClose={() => setOpenDialog(false)} /> : null}
      {saveAsOpen && untitled ? (
        <SaveAsDialog
          suggestedId={origin.source ? `${origin.source}-copy` : ""}
          busy={busy === "save-as"}
          error={exitError ? exitErrorText(exitError) : null}
          onSave={(newId, description) => void saveUntitled(newId, description)}
          onClose={() => setSaveAsOpen(false)}
        />
      ) : null}
      {draftOpen ? (
        <SaveAsDialog
          kind="draft"
          origin={draftOrigin}
          suggestedId={`${untitled ? (origin.source ?? "untitled") : isDraft ? (data?.draftRow?.origin ?? id) : id}-draft`}
          busy={busy === "save-draft"}
          error={exitError ? exitErrorText(exitError) : null}
          onSave={(newId) => void saveAsDraft(newId)}
          onClose={() => setDraftOpen(false)}
        />
      ) : null}
      {saveTpl && model ? (
        <SaveTemplateDialog
          proposal={saveTpl.proposal}
          bare={saveTpl.bare}
          strippedBindings={saveTpl.stripped}
          bareStripped={saveTpl.bareStripped}
          busy={saveTpl.busy}
          error={saveTpl.error}
          onClose={() => setSaveTpl(null)}
          onSave={(choices, used) => {
            const tpl = buildTemplate(model, used, choices, personas, { ...settled.current, ...pinsRef.current }, templates.map((t) => t.id));
            setSaveTpl((s) => (s ? { ...s, busy: true, error: null } : s));
            api.createTemplate(tpl).then(
              (saved) => {
                rememberMyTemplate(saved.id);
                setSaveTpl(null);
                setTemplatesVersion((v) => v + 1);
                setExitNote(`saved template ${saved.label} (${saved.id}): it is under My templates in the Add panel`);
              },
              (e: unknown) => {
                const findings = e instanceof ApiError && e.detail && typeof e.detail === "object" ? ((e.detail as { findings?: Array<{ message?: string }> }).findings ?? []) : [];
                setSaveTpl((s) => (s ? { ...s, busy: false, error: [errorText(e), ...findings.map((f) => f.message ?? "").filter(Boolean)].join(" · ") } : s));
              },
            );
          }}
        />
      ) : null}
      {wireDialog ? (
        <div className="modal-backdrop" data-testid="wire-backdrop">
          <form
            className="modal panel"
            role="dialog"
            aria-modal="true"
            aria-label="Connect routers"
            data-testid="wire-dialog"
            onSubmit={(e) => {
              e.preventDefault();
              const w = wireDialog;
              let made: string[] = [];
              let skipped: string[] = [];
              let ospfSet: string[] = [];
              const switchTo = w.ospf && w.switchImage && ospfImage && model ? { to: ospfImage, routers: ospfImageGaps(model.te, w.routers, ospfImage).map((g) => g.router) } : null;
              const reason = edit(`connect ${w.routers.length} routers as ${w.shape === "hub" ? "hub & spoke" : w.shape === "mesh" ? "full mesh" : w.shape}`, (d, m) => {
                const r = wireRouters(d, m, w.routers, w.shape, { ospfArea: w.ospf ? w.area : null, image: switchTo?.routers.length ? switchTo : null });
                made = r.made;
                skipped = r.skipped;
                ospfSet = r.ospf;
                return r.files;
              });
              if (reason) return;
              setWireDialog(null);
              const onOld = ospfSet.filter((t) => !made.includes(t));
              setExitNote(`${made.length} transit${made.length === 1 ? "" : "s"} made${w.ospf && made.length ? ` (ospf_area ${w.area})` : ""}${onOld.length ? `; ospf_area ${w.area} set on ${onOld.join(", ")}` : ""}${switchTo?.routers.length ? `; ${switchTo.routers.join(", ")} switched to ${switchTo.to}` : ""}${skipped.length ? `; already linked, left as they are: ${skipped.join(", ")}` : ""}`);
            }}
          >
            <h2 style={{ marginTop: 0 }}>Connect {wireDialog.routers.length} routers</h2>
            <p className="muted">Each pair gets a transit segment (a /30 from 10.254.0.0/16) with a leg for both routers; a pair already linked keeps its transit (OSPF, when ticked, is set on it too). One undo step.</p>
            <fieldset className="field">
              <legend>As</legend>
              {(["chain", "ring", "mesh", "hub"] as const).map((sh) => (
                <label key={sh} className="cv-check">
                  <input type="radio" name="wire-shape" value={sh} checked={wireDialog.shape === sh} onChange={() => setWireDialog({ ...wireDialog, shape: sh })} />
                  {sh === "mesh" ? "full mesh" : sh === "hub" ? `hub & spoke (hub: ${wireDialog.routers[0]})` : sh}
                </label>
              ))}
            </fieldset>
            <label className="cv-check">
              <input type="checkbox" checked={wireDialog.ospf} onChange={(e) => setWireDialog({ ...wireDialog, ospf: e.target.checked })} />
              run OSPF on these links (<span className="mono">ospf_area</span> on each transit, new or already drawn)
            </label>
            {wireDialog.ospf ? <OspfImageCheck te={model?.te ?? null} routers={wireDialog.routers} ospfImage={ospfImage} on={wireDialog.switchImage} onChange={(on) => setWireDialog({ ...wireDialog, switchImage: on })} /> : null}
            {wireDialog.ospf ? <p className="muted hint" data-testid="ospf-note">Not written here: each router&apos;s <span className="mono">routing:</span> block (region, role, loopback) and a <span className="mono">fabric:</span> region with <span className="mono">igp: ospf</span>; without them OSPF does not come up.</p> : null}
            {wireDialog.ospf ? (
              <div className="field">
                <label htmlFor="wire-area">Area</label>
                <input id="wire-area" type="number" min={0} value={wireDialog.area} onChange={(e) => setWireDialog({ ...wireDialog, area: Math.max(0, Number(e.target.value) || 0) })} />
              </div>
            ) : null}
            {(() => {
              const plan = model ? wirePlan(model.te, wireDialog.routers, wireDialog.shape) : [];
              return (
                <div data-testid="wire-plan">
                  {plan.length && plan.every((r) => r.kind === "linked") ? <p className="muted" data-testid="wire-all-linked">all pairs already linked{wireDialog.ospf ? ": Connect sets OSPF (and the image) only" : ""}</p> : null}
                  <ul className="muted mono" style={{ margin: 0, paddingLeft: "1.2em" }}>
                    {plan.map((r) => (
                      <li key={`${r.a}|${r.b}`} data-testid="wire-plan-row">{wirePlanText(r)}</li>
                    ))}
                  </ul>
                </div>
              );
            })()}
            {blocked ? <div className="error" role="alert">{blocked}</div> : null}
            <div className="row-actions">
              <button type="submit" className="primary">Connect</button>
              <button type="button" onClick={() => setWireDialog(null)}>Cancel</button>
            </div>
          </form>
        </div>
      ) : null}
      {replaceOpen && isDraft && data?.draftRow?.origin ? (
        <div className="modal-backdrop" data-testid="replace-backdrop">
          <div className="modal panel" role="dialog" aria-modal="true" aria-label={`Replace ${data.draftRow.origin}`} data-testid="replace-dialog">
            <h2 style={{ marginTop: 0 }}>Replace {data.draftRow.origin}?</h2>
            <p>
              The twin <span className="mono">{data.draftRow.origin}</span> is overwritten with this draft (its current baseline is kept in its history). The API validates the draft again first; the draft itself is kept.
            </p>
            {exitError && busy === null ? <div className="error" role="alert">{exitErrorText(exitError)}</div> : null}
            <div className="row-actions">
              <button type="button" className="danger" onClick={replaceOrigin} disabled={!valid || busy !== null} data-testid="replace-confirm">
                {busy === "replace" ? "Replacing…" : `Replace ${data.draftRow.origin}`}
              </button>
              <button type="button" onClick={() => setReplaceOpen(false)} disabled={busy !== null}>
                Cancel
              </button>
            </div>
          </div>
        </div>
      ) : null}
      {destroyOpen && data?.deploy ? (
        <DestroyDialog
          twinId={id}
          deploy={data.deploy}
          tiers={destroyRun?.tiers ?? standingTiers(deployRows, data.deploy.deploy_id)}
          run={destroyRun}
          dirty={dirty}
          dirtyKind={state === "applied" ? "applied" : "unapplied"}
          onPersistFirst={() => void runCheck().then((rec) => persist(rec))}
          onConfirm={() => destroyer.start(data.deploy!.deploy_id, standingTiers(deployRows, data.deploy!.deploy_id))}
          onClose={() => {
            setDestroyOpen(false);
            destroyer.clear();
          }}
        />
      ) : null}
      <div className="cv-strip" data-testid="canvas-strip">
        <ErrorLine error={error} prefix="could not load the twin" />
        <ErrorLine error={draft.error} prefix="could not save the layout" />
        <ErrorLine error={pollError} prefix="could not refresh the working copy" />
        {exitError ? (
          <div className="error" role="alert" data-testid="exit-error">
            {exitErrorText(exitError)}
            <button type="button" className="cv-dismiss" aria-label="Dismiss" onClick={() => setExitError(null)} title="dismiss">
              ×
            </button>
          </div>
        ) : null}
        {editProps && emptyReason ? (
          <p className="notice info" role="status" data-testid="empty-reason">
            {emptyReason}
          </p>
        ) : null}
        {editProps && blocked ? (
          <div className="error" role="alert" data-testid="blocked-reason">
            blocked: {blocked}
          </div>
        ) : null}
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
            provenanceOverlay={castTwin}
            lab={labSource}
            onFan={setFan}
            onPin={onPin}
            onPinMany={onPinMany}
            onToggleSite={onToggleSite}
            initialViewport={initialViewport}
            onViewport={onViewport}
            notes={notes}
            locked={Boolean(locked)}
            layoutBar={layoutBar}
            findings={pins_}
            focus={focus}
            {...(editProps ? { edit: editProps } : {})}
            {...(editProps && data && model
              ? {
                  palette: <AddPanel personas={personas} templateList={templates} health={healthHints(model.te, personas)} fixes={danglingLinks(model.te).length ? [{ label: `Remove ${danglingLinks(model.te).length === 1 ? "it" : "them"}`, run: () => void edit("remove dangling links", (d, m) => removeDanglingLinks(d, m)) }] : []} disabled={Boolean(locked)} note={paletteNote} templates={<TemplatesSection templates={templates} mine={readMyTemplates()} disabled={Boolean(locked)} />} />,
                  quickAdd: (pick: (item: PaletteItem) => void, close: () => void) => <QuickAdd personas={personas} templates={templates} onPick={pick} onClose={close} />,
                  ...(pendingTemplate
                    ? {
                        overlay: (
                          <TemplatePopover
                            key={`${pendingTemplate.template.id}:${pendingTemplate.screen.x}:${pendingTemplate.screen.y}`}
                            template={pendingTemplate.template}
                            profiles={imageProfiles}
                            at={pendingTemplate.screen}
                            where={pendingTemplate.at ? "here" : "at the centre"}
                            onCommit={commitTemplate}
                            onCancel={() => setPendingTemplate(null)}
                          />
                        ),
                      }
                    : {}),
                  ...(pendingDrop
                    ? {
                        overlay: (
                          <DropPopover
                            key={`${pendingDrop.item.kind}:${pendingDrop.item.id}:${pendingDrop.screen.x}:${pendingDrop.screen.y}`}
                            item={pendingDrop.item}
                            target={pendingDrop.target}
                            te={model.te}
                            personas={personas}
                            profiles={imageProfiles}
                            sensors={declaredSensors}
                            at={pendingDrop.screen}
                            onCommit={commitDrop}
                            onCancel={() => setPendingDrop(null)}
                          />
                        ),
                      }
                    : {}),
                }
              : {})}
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
                        images={[...new Set([...imageProfiles.map((p) => p.image), ...model.te.nodes.map((n) => n.image)].filter((x): x is string => Boolean(x)))].sort()}
                        personas={personas}
                        canPromote={pit !== null}
                        promoteBlock={promoteBlock}
                        apply={edit}
                        onPromoteRole={onPromoteRole}
                        onPromoteSplit={pit !== null ? onPromoteSplit : undefined}
                        splitBlock={splitBlock}
                        memberKeys={membersOf}
                        onPromoteMerge={onPromoteMerge}
                        annotations={annotations}
                        onAnnotation={(aid, patch) => setAnnotations(annotations.map((a) => (a.id === aid ? { ...a, ...patch } : a)))}
                        onRemoveAnnotation={(aid) => {
                          setAnnotations(annotations.filter((a) => a.id !== aid));
                          setSelection({ kind: "none" });
                        }}
                        changes={deployed ? (config?.changes ?? []) : null}
                        blocked={blocked}
                        onRenamed={(kind, from, to) => setSelection((sel) => (sel.kind === kind ? { kind, names: sel.names.map((n) => (n === from ? to : n)) } : sel))}
                        observedSegments={(node) => observedSegments(data?.exportData ?? null, cast, node, model.te.nodes.find((n) => n.name === node)?.nets.map((l) => l.segment) ?? [])}
                        onProvenance={pit !== null ? onProvenance : undefined}
                        provenanceBlock={provenanceBlock}
                        onSelect={setSelection}
                        onDuplicate={(root, attachTo, opts) => onDuplicate(root, attachTo, opts)}
                        sensors={declaredSensors}
                        sensorTarget={sensorTargetName}
                        onConvertFabric={onConvertFabric}
                        fabricResult={fabricResult}
                      />
                    ),
                    checks: (
                      <Checks
                        model={model}
                        check={check}
                        files={files}
                        checking={checking}
                        onCheck={() => void runCheck()}
                        onFinding={onFinding}
                        changes={deployed ? (config?.changes ?? []) : null}
                        onDiscard={deployed && baselineFiles && !locked ? onDiscardChange : undefined}
                        provenance={{ filter: provFilter, onFilter: setProvFilter, fallback: defaultSource }}
                        taps={{ filter: provFilter, onFilter: setProvFilter, sensors: knownSensors(model, declaredSensors), locked, apply: edit }}
                      />
                    ),
                    checksBadge: checksBadge({ check, files, checking }),
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

/** The wiring dialog's OSPF image check (2026-11-20, round 2): each selected router not on the
 *  OSPF-capable image (the `router-ospf` profile) gets a line, and one checkbox (on by default)
 *  switches them to it in the same edit. */
function OspfImageCheck({ te, routers, ospfImage, on, onChange }: { te: DocModel["te"] | null; routers: readonly string[]; ospfImage: string | null; on: boolean; onChange(on: boolean): void }) {
  if (!ospfImage) return <p className="hint" data-testid="ospf-image">No <span className="mono">{OSPF_PROFILE}</span> image profile: OSPF needs a VyOS router, and the profiles name no image for one.</p>;
  const gaps = te ? ospfImageGaps(te, routers, ospfImage) : [];
  if (!gaps.length) return null;
  return (
    <div className="field" data-testid="ospf-image">
      <ul className="hint">
        {gaps.map((g) => (
          <li key={g.router} data-testid="ospf-image-gap">
            {g.router} runs {g.image}; OSPF needs {ospfImage} (as datacenter&apos;s fabric)
          </li>
        ))}
      </ul>
      <label className="cv-check">
        <input type="checkbox" checked={on} onChange={(e) => onChange(e.target.checked)} />
        switch these routers to {ospfImage}
      </label>
    </div>
  );
}
