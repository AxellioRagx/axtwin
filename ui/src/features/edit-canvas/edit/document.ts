/**
 * The working document: the twin-config bundle's text files as the canvas
 * holds them (`TwinConfigDocument.files`), read into the models the canvas
 * draws from (`te.ts`, `tfc.ts`, `manifest.ts`) and changed through the
 * gestures the canvas offers -- place, attach, detach, duplicate, the side
 * panel's fields -- each a pure `(doc) => files` the command stack records.
 * The canvas draws the DOCUMENT (the authored-graph pipeline of Phase 1 over
 * the twin config instead of the API's topology), so every edit shows at
 * once and the baseline view stays what the API serves.
 *
 * Pure -- no React, no fetch.
 */
import type { Topology, TwinTree, ValidationFinding } from "../../../api/types";
import { authoredGraph, type AuthoredGraph } from "../canvas/authored";
import { allocateHostAddresses, takenAddresses } from "./allocate";
import type { Files } from "./commands";
import { isTierFile, MANIFEST, readManifest, retargetTierFile, setManifestCluster, setManifestDescription, setManifestId, unwireTierDefault, wireTapDefault, wireTfcDefault, type Manifest } from "./manifest";
import { addTapBinding, emptyTap, moveTapBinding, readTap, removeTapBinding, setTapSelector, setTapSensors, type TapModel, type TapSelector, addSpanBinding, setSpanSources, type SpanSource } from "./tap";
import {
  addConduit as teAddConduit,
  addLink as teAddLink,
  addNode,
  conduitImplemented,
  removeConduit as teRemoveConduit,
  addSegment as teAddSegment,
  copyNames,
  emptyTe,
  readTe,
  removeLink as teRemoveLink,
  setLinkField as teSetLinkField,
  setMemberRole as teSetMemberRole,
  type NewLink,
  linksNaming,
  nodeOfEnd,
  removeNodeAndLinks,
  unlinkSegment,
  removeSegment as teRemoveSegment,
  renameNode as teRenameNode,
  renameSegment as teRenameSegment,
  setNodeField as teSetNodeField,
  setNodeNets,
  setSegmentField as teSetSegmentField,
  setTwinDescription,
  setTwinId,
  type NewNode,
  type TeModel,
  type TeNet,
  type TeNode,
} from "./te";
import { emptyTfc, readTfc, removeAssignment, renameNodeBindings, setAssignment, setTrafficgen, type AssignmentPatch, type TfcBudget, type TfcEmulated, type TfcLine, type TfcModel } from "./tfc";
import { hostRoles } from "./traffic";
import { carriedBy } from "./subtree";
import { validateTe } from "./validate";
import { leaveGateways } from "./vrrp";

export interface WorkingDoc {
  id: string;
  teFile: string;
  files: Files;
}

export interface DocModel {
  te: TeModel;
  manifest: Manifest | null;
  /** The traffic profile the side panel edits, or `null` when the bundle has none yet. */
  tfcPath: string | null;
  tfc: TfcModel | null;
  /** The tap plan the inspector's Taps block edits (topology-layers §8), or `null` when the bundle has none. */
  tapPath: string | null;
  tap: TapModel | null;
  /** The canvas's own structural findings (`validate.ts`); the API's are shown beside them. */
  findings: ValidationFinding[];
}

export const DEFAULT_TFC_PATH = "tfc/default.yaml";
export const DEFAULT_TAP_PATH = "tap/default.yaml";

/** The names a cast's installer wrote before it filed casts as twin folders
 *  (2026-11-09): a bundle with no manifest still reads them (the engine's
 *  `edits.LEGACY_TIER_FILES`, the same list). */
export const LEGACY_TIER_FILES = { tfc: ["tfc-cast.yaml", "tfc.yaml"], tap: ["tap-cast.yaml", "tap.yaml"] } as const;

function tierPathOf(doc: WorkingDoc, manifest: Manifest | null, tier: "tfc" | "tap"): string | null {
  const declared = manifest?.[tier];
  if (declared && doc.files[declared] !== undefined) return declared;
  const dflt = tier === "tfc" ? DEFAULT_TFC_PATH : DEFAULT_TAP_PATH;
  if (doc.files[dflt] !== undefined) return dflt;
  const any = Object.keys(doc.files).find((p) => p.startsWith(`${tier}/`) && p.endsWith(".yaml"));
  if (any) return any;
  if (doc.files[MANIFEST] !== undefined) return null;
  return LEGACY_TIER_FILES[tier].find((p) => doc.files[p] !== undefined) ?? null;
}

export function tapPathOf(doc: WorkingDoc, manifest: Manifest | null): string | null {
  return tierPathOf(doc, manifest, "tap");
}

export function tfcPathOf(doc: WorkingDoc, manifest: Manifest | null): string | null {
  return tierPathOf(doc, manifest, "tfc");
}

/** The legacy cast names the document is read through, for the canvas Notes. */
export function legacyLayout(doc: WorkingDoc, model: DocModel): string[] {
  const legacy: readonly string[] = [...LEGACY_TIER_FILES.tfc, ...LEGACY_TIER_FILES.tap];
  const out = [model.tfcPath, model.tapPath].filter((p): p is string => p !== null && legacy.includes(p));
  if (doc.files[MANIFEST] === undefined && doc.teFile !== "te.yaml") out.unshift(doc.teFile);
  return out;
}

export function readDoc(doc: WorkingDoc): DocModel {
  const teText = doc.files[doc.teFile];
  const te = teText === undefined ? emptyTe(`the document has no '${doc.teFile}'`) : readTe(teText);
  const manifestText = doc.files[MANIFEST];
  const manifest = manifestText === undefined ? null : readManifest(manifestText);
  const tfcPath = tfcPathOf(doc, manifest);
  const tfc = tfcPath ? readTfc(doc.files[tfcPath]!) : null;
  const tapPath = tapPathOf(doc, manifest);
  const tap = tapPath ? readTap(doc.files[tapPath]!) : null;
  return { te, manifest, tfcPath, tfc, tapPath, tap, findings: validateTe(te) };
}

// ---------------------------------------------------------------------------
// The document as a graph: the Phase 1 authored pipeline over the twin config
// ---------------------------------------------------------------------------
export function documentTopology(te: TeModel, id: string): Topology {
  const nodes: Topology["nodes"] = te.segments.map((s) => ({ id: s.name, type: "network", cidr: s.cidr, egress: s.egress || null, source: s.source, confidence: s.confidence }));
  const edges: Topology["edges"] = [];
  for (const n of te.nodes) {
    nodes.push({ id: n.name, type: n.kind, image: n.image || null, role: n.role, source: n.source, confidence: n.confidence });
    // The chip is labelled by name; the pinned address rides on the attachment label (EditContext), not here.
    for (const leg of n.nets) edges.push({ source: n.name, target: leg.segment, ip: null });
  }
  // The device tree (topology-layers §2.3): the document's links, as the API would list them.
  const links: Topology["links"] = te.links.map((l) => ({ a: l.a.device, a_port: l.a.port, b: l.b.device, b_port: l.b.port, mode: l.mode, native: l.native, vlans: l.vlans, state: l.state, source: l.source, confidence: l.confidence }));
  return { scenario_id: id, nodes, edges, links };
}

export function documentTree(te: TeModel, id: string): TwinTree {
  const treeNode = (n: TeNode): TwinTree["routers"][number] => ({
    name: n.name,
    kind: n.kind,
    role: n.role,
    image: n.image,
    count: n.countValue,
    instances: [n.name],
    segments: n.nets.map((l) => l.segment),
    requires: n.requires,
    listen: [],
    install: [],
    personas: [],
  });
  const segments: TwinTree["segments"] = te.segments.map((s) => {
    const nodes = te.nodes.filter((n) => n.kind !== "router" && n.nets[0]?.segment === s.name);
    return { name: s.name, cidr: s.cidr, zone_type: s.zoneType, hosts: nodes.reduce((a, n) => a + n.countValue, 0), roles: [], taps: [], nodes: nodes.map(treeNode) };
  });
  return { id, hosts: te.nodes.filter((n) => n.kind !== "router").reduce((a, n) => a + n.countValue, 0), segments, routers: te.nodes.filter((n) => n.kind === "router").map(treeNode), warnings: [] };
}

export function documentGraph(model: DocModel, id: string): AuthoredGraph {
  // The document's own conduits: drawn in the design view as declared connectors (`canvas/pairs.ts`).
  return authoredGraph(documentTopology(model.te, id), documentTree(model.te, id), model.te.conduits.map((c) => ({ from: c.from, to: c.to })));
}

// ---------------------------------------------------------------------------
// Mutations
// ---------------------------------------------------------------------------
function withTe(doc: WorkingDoc, fn: (text: string) => string): Files {
  const text = doc.files[doc.teFile];
  if (text === undefined) throw new Error(`the document has no '${doc.teFile}' to edit`);
  return { ...doc.files, [doc.teFile]: fn(text) };
}

export interface PlaceRequest {
  /** The base name; N copies are `{name}-{n}`. */
  name: string;
  count: number;
  image: string;
  role?: string | null;
  kind?: "host" | "router" | "switch";
  /** The segment each copy attaches to; `null` places unattached. */
  segment: string | null;
  requires?: string[];
  resources?: Record<string, string | number> | null;
  /** A `count:` written on each copy (a template's fan-out), as opposed to N copies. */
  fanOut?: number | string;
  /** Always `{name}-{n}`, the first free n from 1, even for a single copy (an Add-panel card's
   *  drop: `router-1`, `router-2`, …, 2026-11-20 round 2); otherwise a single copy keeps `{name}` when free. */
  numbered?: boolean;
}

/** Place `count` copies of a node template on a segment, each named
 *  `{name}-{n}` and pinned to the segment's next free address (C3.2). */
export function placeNodes(doc: WorkingDoc, model: DocModel, req: PlaceRequest): { files: Files; names: string[] } {
  const taken = new Set(model.te.nodes.map((n) => n.name));
  const names = copyNames(req.name, Math.max(1, req.count), taken, req.numbered);
  let addresses: string[] = [];
  if (req.segment) {
    if (!model.te.segments.some((s) => s.name === req.segment)) throw new Error(`no segment named '${req.segment}'`);
    const fanOut = req.fanOut !== undefined && req.fanOut !== 1;
    // A router placed on a segment gets that leg unpinned, as `attach` does.
    if (!fanOut && req.kind !== "router") {
      const a = allocateHostAddresses(model.te.segments.find((s) => s.name === req.segment)!.cidr, names.length, takenAddresses(model.te, req.segment));
      if (!a.ok) throw new Error(a.error);
      addresses = a.addresses;
    }
  }
  let text = doc.files[doc.teFile];
  if (text === undefined) throw new Error(`the document has no '${doc.teFile}' to edit`);
  names.forEach((name, i) => {
    const node: NewNode = {
      name,
      kind: req.kind ?? "host",
      role: req.role ?? null,
      image: req.image,
      nets: req.segment ? [{ segment: req.segment, ip: addresses[i] ?? null }] : [],
      requires: req.requires ?? [],
      resources: req.resources ?? null,
    };
    if (req.fanOut !== undefined) node.count = req.fanOut;
    text = addNode(text!, node);
  });
  return { files: { ...doc.files, [doc.teFile]: text }, names };
}

/** Copies of the selected node groups onto a target segment, re-addressed there (C3.2). */
export function duplicateOnto(doc: WorkingDoc, model: DocModel, nodeNames: readonly string[], segment: string): { files: Files; names: string[] } {
  let cur: WorkingDoc = doc;
  let curModel = model;
  const out: string[] = [];
  for (const name of nodeNames) {
    const n = curModel.te.nodes.find((x) => x.name === name);
    if (!n) throw new Error(`no node group named '${name}'`);
    const r = placeNodes(cur, curModel, { name, count: 1, image: n.image, role: n.role, kind: n.kind, segment, requires: n.requires, resources: n.resources, fanOut: n.countValue > 1 ? n.count : undefined });
    out.push(...r.names);
    cur = { ...cur, files: r.files };
    curModel = readDoc(cur);
  }
  return { files: cur.files, names: out };
}

/** Attach a node group to a segment (a first leg, or a further one on a
 *  router or a multi-homed host), pinned to the segment's next free address
 *  when the group is a single instance. */
export function attach(doc: WorkingDoc, model: DocModel, node: string, segment: string): Files {
  const n = model.te.nodes.find((x) => x.name === node);
  if (!n) throw new Error(`no node group named '${node}'`);
  const seg = model.te.segments.find((s) => s.name === segment);
  if (!seg) throw new Error(`no segment named '${segment}'`);
  if (n.nets.some((l) => l.segment === segment)) throw new Error(`'${node}' is already on ${segment}`);
  let ip: string | null = null;
  // A router's leg is left unpinned: the engine gives a lone router leg the
  // segment's gateway address (network+1), which is what the segment's hosts
  // route through. Pinning the next free host address here would park the
  // router at .10 with nothing at the gateway.
  if (n.kind !== "router" && n.countValue === 1 && typeof n.count === "number") {
    const a = allocateHostAddresses(seg.cidr, 1, takenAddresses(model.te, segment));
    if (!a.ok) throw new Error(a.error);
    ip = a.addresses[0]!;
  }
  return withTe(doc, (t) => setNodeNets(t, node, [...n.nets, { segment, ip }]));
}

/** Move a node group's FIRST leg to another segment (a drop onto a segment), re-addressed there. */
export function moveTo(doc: WorkingDoc, model: DocModel, node: string, segment: string): Files {
  const n = model.te.nodes.find((x) => x.name === node);
  if (!n) throw new Error(`no node group named '${node}'`);
  if (n.nets.length === 0) return attach(doc, model, node, segment);
  if (n.nets[0]!.segment === segment) return doc.files;
  if (n.nets.some((l) => l.segment === segment)) throw new Error(`'${node}' already has a leg on ${segment}`);
  const seg = model.te.segments.find((s) => s.name === segment);
  if (!seg) throw new Error(`no segment named '${segment}'`);
  let ip: string | null = null;
  if (n.nets[0]!.ip && n.countValue === 1) {
    const a = allocateHostAddresses(seg.cidr, 1, takenAddresses(model.te, segment));
    if (!a.ok) throw new Error(a.error);
    ip = a.addresses[0]!;
  }
  const nets: TeNet[] = [{ segment, ip }, ...n.nets.slice(1)];
  return withTe(doc, (t) => setNodeNets(t, node, nets));
}

/** Re-home one leg (rule 6, track 4): a router's drop dragged from one bus to
 *  another moves that attachment to the other segment, its address let go
 *  (the planner allocates one there). A topology change: the API classifies
 *  it as a re-plan. */
export function rehome(doc: WorkingDoc, model: DocModel, node: string, from: string, to: string): Files {
  const n = model.te.nodes.find((x) => x.name === node);
  if (!n) throw new Error(`no node group named '${node}'`);
  if (!model.te.segments.some((s) => s.name === to)) throw new Error(`no segment named '${to}'`);
  if (!n.nets.some((l) => l.segment === from)) throw new Error(`'${node}' is not on ${from}`);
  if (n.nets.some((l) => l.segment === to)) throw new Error(`'${node}' is already on ${to}`);
  return withTe(doc, (t) => setNodeNets(t, node, n.nets.map((l) => (l.segment === from ? { segment: to, ip: null } : l))));
}

export function detach(doc: WorkingDoc, model: DocModel, node: string, segment: string): Files {
  const n = model.te.nodes.find((x) => x.name === node);
  if (!n) throw new Error(`no node group named '${node}'`);
  return withTe(doc, (t) => setNodeNets(n.kind === "router" ? leaveGateways(t, node, [segment]) : t, node, n.nets.filter((l) => l.segment !== segment)));
}

export function setAttachmentAddress(doc: WorkingDoc, model: DocModel, node: string, segment: string, ip: string | null): Files {
  const n = model.te.nodes.find((x) => x.name === node);
  if (!n) throw new Error(`no node group named '${node}'`);
  if (!n.nets.some((l) => l.segment === segment)) throw new Error(`'${node}' is not on ${segment}`);
  return withTe(doc, (t) => setNodeNets(t, node, n.nets.map((l) => (l.segment === segment ? { ...l, ip } : l))));
}

export function setNodeField(doc: WorkingDoc, node: string, key: "role" | "image" | "count" | "kind" | "stp", value: string | number | null): Files {
  return withTe(doc, (t) => teSetNodeField(t, node, key, value));
}

export function renameNode(doc: WorkingDoc, model: DocModel, from: string, to: string): Files {
  const files = withTe(doc, (t) => teRenameNode(t, from, to));
  if (model.tfcPath && files[model.tfcPath] !== undefined) files[model.tfcPath] = renameNodeBindings(files[model.tfcPath]!, from, to);
  return files;
}

/** What removing node group `node` takes with it (B1, 2026-11-21): the links naming it, and -- for a
 *  switch -- the segments it alone carried (no other switch carries them once it is gone), each with
 *  the hosts still on it (`hosted`: a segment with hosts on it is kept unless the caller says not to). */
export function removalPlan(model: DocModel, node: string): { links: number[]; segments: string[]; hosted: Record<string, number> } {
  const te = model.te;
  const n = te.nodes.find((x) => x.name === node);
  const links = linksNaming(te, node);
  if (n?.kind !== "switch") return { links, segments: [], hosted: {} };
  const gone = new Set(links);
  const rest: TeModel = { ...te, nodes: te.nodes.filter((x) => x.name !== node), links: te.links.filter((l) => !gone.has(l.index)) };
  const segments = carriedBy(te, node).filter((seg) => te.segments.some((x) => x.name === seg) && !rest.nodes.some((x) => x.kind === "switch" && carriedBy(rest, x.name).includes(seg)));
  const hosted: Record<string, number> = {};
  for (const seg of segments) {
    const k = te.nodes.filter((x) => x.kind !== "router" && x.kind !== "switch" && x.nets.some((l) => l.segment === seg)).reduce((a, x) => a + x.countValue, 0);
    if (k) hosted[seg] = k;
  }
  return { links, segments, hosted };
}

/** Remove a node group with what hangs on it (B1, 2026-11-21): every `links[]` entry naming it, its
 *  traffic binding, and -- for a switch -- the segments it alone carried (`removalPlan`), each with the
 *  legs on it and the links carrying it. A segment with hosts still on it is KEPT (it draws as an
 *  implied switch again) unless `keepSegments` is false; `true` keeps every one. One edit. */
export function removeNode(doc: WorkingDoc, model: DocModel, node: string, options: { keepSegments?: boolean } = {}): Files {
  const plan = removalPlan(model, node);
  const drop = plan.segments.filter((seg) => (options.keepSegments === undefined ? !plan.hosted[seg] : !options.keepSegments));
  const n = model.te.nodes.find((x) => x.name === node);
  // A router leaving its LANs leaves their VRRP groups and hands on a gateway written at it (D5).
  const leave = n?.kind === "router" ? (t: string) => leaveGateways(t, node, n.nets.map((l) => l.segment)) : (t: string) => t;
  let cur: WorkingDoc = { ...doc, files: withTe(doc, (t) => removeNodeAndLinks(leave(t), node)) };
  for (const seg of drop) cur = { ...cur, files: removeSegment(cur, readDoc(cur), seg) };
  const files = { ...cur.files };
  if (model.tfcPath && files[model.tfcPath] !== undefined) files[model.tfcPath] = removeAssignment(files[model.tfcPath]!, "node", node);
  return files;
}

/** The links an end of which names no node of the twin, or that carry a segment the twin does not
 *  have: left behind by a hand edit or an older delete (B1). The design hint's one click removes them. */
export function danglingLinks(te: Pick<TeModel, "nodes" | "links" | "segments">): number[] {
  const segs = new Set(te.segments.map((s) => s.name));
  return te.links.filter((l) => !nodeOfEnd(te, l.a.device) || !nodeOfEnd(te, l.b.device) || [...(l.native ? [l.native] : []), ...l.vlans].some((v) => !segs.has(v))).map((l) => l.index);
}

export function removeDanglingLinks(doc: WorkingDoc, model: DocModel): Files {
  const idx = danglingLinks(model.te);
  if (!idx.length) throw new Error("no dangling link to remove");
  return withTe(doc, (t) => idx.sort((a, b) => b - a).reduce((text, i) => teRemoveLink(text, i), t));
}

export function addSegment(doc: WorkingDoc, seg: { name: string; cidr: string; zoneType?: string | null }): Files {
  return withTe(doc, (t) => teAddSegment(t, seg));
}

export function setSegmentField(doc: WorkingDoc, name: string, key: "cidr" | "zone_type" | "ospf_area", value: string | number | null): Files {
  return withTe(doc, (t) => teSetSegmentField(t, name, key, value));
}

export function renameSegment(doc: WorkingDoc, from: string, to: string): Files {
  return withTe(doc, (t) => teRenameSegment(t, from, to));
}

/** Remove a segment and every leg on it. */
export function removeSegment(doc: WorkingDoc, model: DocModel, name: string): Files {
  let text = doc.files[doc.teFile];
  if (text === undefined) throw new Error(`the document has no '${doc.teFile}' to edit`);
  for (const n of model.te.nodes) {
    if (n.nets.some((l) => l.segment === name)) text = setNodeNets(text, n.name, n.nets.filter((l) => l.segment !== name));
  }
  // A conduit naming it goes with it (the engine refuses one whose segment is gone), last index first.
  for (const c of [...model.te.conduits].reverse()) if (c.from === name || c.to === name) text = teRemoveConduit(text, c.index);
  // So does every link's carrying of it; a link left carrying nothing goes (B1, CV-044).
  text = unlinkSegment(text, name);
  text = teRemoveSegment(text, name);
  return { ...doc.files, [doc.teFile]: text };
}

/** Declare a conduit between two segments of the twin (one undo step). Refused for a segment that is
 *  not there, a segment with itself, or a pair already declared (either direction). */
export function addConduit(doc: WorkingDoc, model: DocModel, from: string, to: string): Files {
  for (const s of [from, to]) if (!model.te.segments.some((x) => x.name === s)) throw new Error(`no segment named '${s}'`);
  if (from === to) throw new Error("a conduit joins two different segments");
  if (model.te.conduits.some((c) => (c.from === from && c.to === to) || (c.from === to && c.to === from))) throw new Error(`a conduit between ${from} and ${to} is already declared`);
  return withTe(doc, (t) => teAddConduit(t, from, to));
}

export function removeConduit(doc: WorkingDoc, model: DocModel, index: number): Files {
  if (!model.te.conduits.some((c) => c.index === index)) throw new Error(`no conduit at index ${index}`);
  return withTe(doc, (t) => teRemoveConduit(t, index));
}

export { conduitImplemented };

/** Merge segment `from` into `to`: every leg moves over, `from` goes. */
export function mergeSegments(doc: WorkingDoc, model: DocModel, from: string, to: string): Files {
  let text = doc.files[doc.teFile];
  if (text === undefined) throw new Error(`the document has no '${doc.teFile}' to edit`);
  if (!model.te.segments.some((s) => s.name === to)) throw new Error(`no segment named '${to}'`);
  for (const n of model.te.nodes) {
    if (!n.nets.some((l) => l.segment === from)) continue;
    const nets: TeNet[] = [];
    for (const l of n.nets) {
      if (l.segment === from) {
        if (nets.some((x) => x.segment === to) || n.nets.some((x) => x.segment === to)) continue;
        nets.push({ segment: to, ip: null });
      } else nets.push(l);
    }
    text = setNodeNets(text, n.name, nets);
  }
  text = teRemoveSegment(text, from);
  return { ...doc.files, [doc.teFile]: text };
}

/** Bind a persona (with intensity / schedule) to a node group through the
 *  traffic profile, creating the profile and wiring the manifest when the
 *  bundle has none. */
export function setPersona(doc: WorkingDoc, model: DocModel, by: "node" | "role", target: string, patch: AssignmentPatch): Files {
  const files = { ...doc.files };
  let path = model.tfcPath;
  if (!path) {
    path = DEFAULT_TFC_PATH;
    files[path] = emptyTfc(doc.id);
    if (files[MANIFEST] !== undefined) files[MANIFEST] = wireTfcDefault(files[MANIFEST]!, path);
  }
  files[path] = setAssignment(files[path]!, by, target, patch);
  return files;
}

export function clearPersona(doc: WorkingDoc, model: DocModel, by: "node" | "role", target: string): Files {
  if (!model.tfcPath) return doc.files;
  return { ...doc.files, [model.tfcPath]: removeAssignment(doc.files[model.tfcPath]!, by, target) };
}

// ---- trafficgen (design traffic-generation-trex-astf.md §4.1): emulated instances, traffic
// lines, the budget, and the real persona off. Every one writes the tfc profile only; te is
// never written. ----

/** The tfc profile's path and text, created (and wired in the manifest) when the bundle has none. */
function tfcFiles(doc: WorkingDoc, model: DocModel): { files: Files; path: string } {
  const files = { ...doc.files };
  let path = model.tfcPath;
  if (!path) {
    path = DEFAULT_TFC_PATH;
    files[path] = emptyTfc(doc.id);
    if (files[MANIFEST] !== undefined) files[MANIFEST] = wireTfcDefault(files[MANIFEST]!, path);
  }
  return { files, path };
}

type Tg = Pick<TfcModel, "budget" | "emulated" | "lines">;

function editTrafficgen(doc: WorkingDoc, model: DocModel, change: (tg: Tg) => Tg): Files {
  const { files, path } = tfcFiles(doc, model);
  const cur = readTfc(files[path]!);
  const next = change({ budget: cur.budget, emulated: cur.emulated, lines: cur.lines });
  // `budget` is required once a line exists (TGN T2): a first line brings a default one that fits it.
  if (next.lines.length && !next.budget) {
    const cps = next.lines.reduce((s, l) => s + l.rate_cps, 0);
    next.budget = { ...DEFAULT_BUDGET, cps: Math.max(DEFAULT_BUDGET.cps, Math.ceil(cps)) };
  }
  files[path] = setTrafficgen(files[path]!, next);
  return files;
}

/** The budget a first traffic line brings when the profile has none (the gate run's scale). */
export const DEFAULT_BUDGET: TfcBudget = { cps: 100, mbps: 60 };

/** Emulated instances of a te node group (0 removes its `emulated[]` entry; its lines stay, and
 *  their rows say why they are refused). */
export function setEmulated(doc: WorkingDoc, model: DocModel, group: string, count: number): Files {
  if (!Number.isInteger(count) || count < 0) throw new Error(`emulated instances must be a whole number ≥ 0, got ${count}`);
  return editTrafficgen(doc, model, (tg) => {
    const rest = tg.emulated.filter((e) => e.group !== group);
    if (count === 0) return { ...tg, emulated: rest };
    const cur = tg.emulated.find((e) => e.group === group);
    const entry: TfcEmulated = cur ? { ...cur, count } : { group, count, external: false, segment: null, role: null, start: null };
    return { ...tg, emulated: cur ? tg.emulated.map((e) => (e.group === group ? entry : e)) : [...tg.emulated, entry] };
  });
}

/** Add or change an external group (Internet users / services: `external: true` on a segment).
 *  `previous` names the entry it replaces (a rename follows into its lines); null adds. */
export function setExternalGroup(doc: WorkingDoc, model: DocModel, ext: { group: string; segment: string; role: string; count: number }, previous: string | null = null): Files {
  if (!ext.group.trim()) throw new Error("an external group needs a name");
  if (model.te.nodes.some((n) => n.name === ext.group)) throw new Error(`'${ext.group}' is a node group; an external group needs its own name`);
  if (!Number.isInteger(ext.count) || ext.count < 1) throw new Error("an external group has at least 1 instance");
  return editTrafficgen(doc, model, (tg) => {
    if (tg.emulated.some((e) => e.group === ext.group && e.group !== previous)) throw new Error(`'${ext.group}' is already in the traffic profile`);
    const entry: TfcEmulated = { group: ext.group, count: ext.count, external: true, segment: ext.segment, role: ext.role, start: null };
    const emulated = previous && tg.emulated.some((e) => e.group === previous) ? tg.emulated.map((e) => (e.group === previous ? entry : e)) : [...tg.emulated, entry];
    const lines = previous && previous !== ext.group ? tg.lines.map((l) => ({ ...l, from: l.from === previous ? ext.group : l.from, to: l.to === previous ? ext.group : l.to })) : tg.lines;
    return { ...tg, emulated, lines };
  });
}

/** Remove an external group and every line that names it. */
export function removeExternalGroup(doc: WorkingDoc, model: DocModel, group: string): Files {
  return editTrafficgen(doc, model, (tg) => ({ ...tg, emulated: tg.emulated.filter((e) => e.group !== group), lines: tg.lines.filter((l) => l.from !== group && l.to !== group) }));
}

export function addLine(doc: WorkingDoc, model: DocModel, line: TfcLine): Files {
  return editTrafficgen(doc, model, (tg) => {
    const id = (l: TfcLine) => l.id ?? `${l.from}--${l.to}--${l.mix}`;
    let l = line;
    // Ids are unique (the parser refuses a repeat): a second line of the same ends and mix gets a suffix.
    for (let n = 2; tg.lines.some((x) => id(x) === id(l)); n++) l = { ...line, id: `${line.from}--${line.to}--${line.mix}-${n}` };
    return { ...tg, lines: [...tg.lines, l] };
  });
}

export function setLine(doc: WorkingDoc, model: DocModel, index: number, patch: Partial<TfcLine>): Files {
  return editTrafficgen(doc, model, (tg) => {
    if (!tg.lines[index]) throw new Error(`no traffic line ${index}`);
    return { ...tg, lines: tg.lines.map((l, i) => (i === index ? { ...l, ...patch } : l)) };
  });
}

export function removeLine(doc: WorkingDoc, model: DocModel, index: number): Files {
  return editTrafficgen(doc, model, (tg) => ({ ...tg, lines: tg.lines.filter((_, i) => i !== index) }));
}

export function setBudget(doc: WorkingDoc, model: DocModel, budget: TfcBudget | null): Files {
  if (budget && (!(budget.cps > 0) || !(budget.mbps > 0))) throw new Error("the budget's cps and Mbps are numbers > 0");
  return editTrafficgen(doc, model, (tg) => {
    if (!budget && tg.lines.length) throw new Error("the budget is required while the profile has traffic lines");
    return { ...tg, budget };
  });
}

/** The real persona off for `hosts` (te host ids): no assignment drives them, so the VMs stay up
 *  idle. Written without a new key: their own `node:` bindings (and their group's) go, and a
 *  `role:` binding that covers them is replaced by `node:` bindings for the role's other hosts,
 *  each with the role binding's persona, intensity and schedule. */
export function setNoTraffic(doc: WorkingDoc, model: DocModel, hosts: readonly string[]): Files {
  if (!model.tfcPath || !model.tfc) return doc.files;
  const off = new Set(hosts);
  const roles = hostRoles(model);
  const groupsOff = new Set(roles.filter((h) => off.has(h.host)).map((h) => h.group));
  let text = doc.files[model.tfcPath]!;
  for (const a of model.tfc.assignments) {
    if (a.by === "node" && (off.has(a.target) || (groupsOff.has(a.target) && roles.filter((h) => h.group === a.target).every((h) => off.has(h.host))))) {
      text = removeAssignment(text, "node", a.target);
    } else if (a.by === "node" && groupsOff.has(a.target)) {
      // A group binding that covers some hosts kept on: bind those instances one by one.
      text = removeAssignment(text, "node", a.target);
      for (const h of roles.filter((x) => x.group === a.target && !off.has(x.host))) text = setAssignment(text, "node", h.host, { persona: a.persona, intensity: a.intensity, schedule: a.schedule });
    }
  }
  for (const a of model.tfc.assignments) {
    if (a.by !== "role") continue;
    const covered = roles.filter((h) => h.role === a.target);
    if (!covered.some((h) => off.has(h.host))) continue;
    text = removeAssignment(text, "role", a.target);
    for (const h of covered) {
      if (off.has(h.host)) continue;
      if (readTfc(text).assignments.some((x) => x.by === "node" && x.target === h.host && x.persona === a.persona)) continue;
      text = setAssignment(text, "node", h.host, { persona: a.persona, intensity: a.intensity, schedule: a.schedule });
    }
  }
  return { ...doc.files, [model.tfcPath]: text };
}

// ---- the device tree (topology-layers §2.3, §5, §8) ----
export function setMemberRole(doc: WorkingDoc, node: string, index: number, role: string | null): Files {
  return withTe(doc, (t) => teSetMemberRole(t, node, index, role));
}

export function addLink(doc: WorkingDoc, link: NewLink): Files {
  return withTe(doc, (t) => teAddLink(t, link));
}

export function removeLink(doc: WorkingDoc, index: number): Files {
  return withTe(doc, (t) => teRemoveLink(t, index));
}

export function setLinkField(doc: WorkingDoc, index: number, key: Parameters<typeof teSetLinkField>[2], value: string | string[] | null): Files {
  return withTe(doc, (t) => teSetLinkField(t, index, key, value));
}

/** Add a tap on a segment (or a device port) through the tap plan, creating
 *  the plan and wiring the manifest when the bundle has none. A `taps`
 *  change: live. */
export function addTap(doc: WorkingDoc, model: DocModel, binding: Parameters<typeof addTapBinding>[1]): Files {
  const files = { ...doc.files };
  let path = model.tapPath;
  if (!path) {
    path = DEFAULT_TAP_PATH;
    files[path] = emptyTap(doc.id);
    if (files[MANIFEST] !== undefined) files[MANIFEST] = wireTapDefault(files[MANIFEST]!, path);
  }
  files[path] = addTapBinding(files[path]!, binding);
  return files;
}

/** Remove a tap. The LAST binding takes the tap plan with it -- the file and
 *  its manifest entry (2026-11-09: `bindings: []` was refused by the tap
 *  loader); the next Add tap recreates both. One command, so one undo. */
export function removeTap(doc: WorkingDoc, model: DocModel, index: number): Files {
  if (!model.tapPath) return doc.files;
  const path = model.tapPath;
  const next = removeTapBinding(doc.files[path]!, index);
  if (readTap(next).bindings.length > 0 || !model.tap?.bindings.some((b) => b.index === index)) return { ...doc.files, [path]: next };
  const files = { ...doc.files };
  delete files[path];
  if (files[MANIFEST] !== undefined && model.manifest?.tap === path) files[MANIFEST] = unwireTierDefault(files[MANIFEST]!, "tap");
  return files;
}

export function moveTap(doc: WorkingDoc, model: DocModel, index: number, segment: string): Files {
  if (!model.tapPath) return doc.files;
  return { ...doc.files, [model.tapPath]: moveTapBinding(doc.files[model.tapPath]!, index, segment) };
}

/** Move a tap to a segment, a boundary or a link's port (topology-layers §8):
 *  one `taps` change. Refused (thrown, the editor shows why) when the target
 *  names no segment or device of the document. */
export function retargetTap(doc: WorkingDoc, model: DocModel, index: number, sel: TapSelector): Files {
  if (!model.tapPath) return doc.files;
  const segs = new Set(model.te.segments.map((x) => x.name));
  const names = sel.scope === "boundary" ? sel.target : sel.scope === "segment" ? [sel.target] : [sel.segment];
  const unknown = names.filter((x) => !segs.has(x));
  if (unknown.length) throw new Error(`no segment ${unknown.map((x) => `'${x}'`).join(", ")} in the twin`);
  if (sel.scope === "boundary" && sel.target[0] === sel.target[1]) throw new Error("a boundary joins two different segments");
  if (sel.scope === "port") {
    const n = model.te.nodes.find((x) => x.name === sel.target);
    if (!n) throw new Error(`no device '${sel.target}' in the twin`);
    if (!n.nets.some((x) => x.segment === sel.segment)) throw new Error(`${sel.target} has no leg on ${sel.segment}`);
  }
  return { ...doc.files, [model.tapPath]: setTapSelector(doc.files[model.tapPath]!, index, sel) };
}

export function setTapSensorsOf(doc: WorkingDoc, model: DocModel, index: number, sensors: string[]): Files {
  if (!model.tapPath) return doc.files;
  return { ...doc.files, [model.tapPath]: setTapSensors(doc.files[model.tapPath]!, index, sensors) };
}

/** Add a SPAN session on a native switch (native-switches §6), creating the
 *  tap plan when the bundle has none. A `taps` change: live. */
export function addSpan(doc: WorkingDoc, model: DocModel, binding: Parameters<typeof addSpanBinding>[1]): Files {
  const sw = model.te.nodes.find((n) => n.name === binding.switch);
  if (!sw || sw.kind !== "switch") throw new Error(`no switch '${binding.switch}' in the twin`);
  const files = { ...doc.files };
  let path = model.tapPath;
  if (!path) {
    path = DEFAULT_TAP_PATH;
    files[path] = emptyTap(doc.id);
    if (files[MANIFEST] !== undefined) files[MANIFEST] = wireTapDefault(files[MANIFEST]!, path);
  }
  files[path] = addSpanBinding(files[path]!, binding);
  return files;
}

/** Replace a SPAN session's sources; the last source removed removes the tap. */
export function setSpanSourcesOf(doc: WorkingDoc, model: DocModel, index: number, sources: SpanSource[]): Files {
  if (!model.tapPath) return doc.files;
  if (!sources.length) return removeTap(doc, model, index);
  return { ...doc.files, [model.tapPath]: setSpanSources(doc.files[model.tapPath]!, index, sources) };
}

/** The taps that name no sensor (a cast's caveat: "every binding omits sensors"). */
export function tapsWithoutSensor(model: DocModel): number[] {
  return (model.tap?.bindings ?? []).filter((b) => b.sensors.length === 0).map((b) => b.index);
}

/** Set the same sensors on every tap in `indices` (one command, one undo). */
export function setSensorsOnTaps(doc: WorkingDoc, model: DocModel, indices: number[], sensors: string[]): Files {
  if (!model.tapPath) return doc.files;
  let text = doc.files[model.tapPath]!;
  for (const i of indices) text = setTapSensors(text, i, sensors);
  return { ...doc.files, [model.tapPath]: text };
}

export interface SaveAsOptions {
  /** Written into `twin_config.description` and `twin.description` when given (non-blank). */
  description?: string;
}

/** The files a Save-As writes: the same document under a new id (and, when given, a new description). */
export function saveAsFiles(doc: WorkingDoc, newId: string, options: SaveAsOptions = {}): Files {
  if (doc.files[doc.teFile] === undefined) throw new Error(`the document has no '${doc.teFile}' to edit`);
  return renamedFiles(doc.files, doc.teFile, newId, options) as Files;
}

/** The same rewrite over a partial file set (a command's before/after text): the te file and the
 *  manifest take the new id and description, every other path is carried as it is. */
export function renamedFiles<T extends string | undefined>(files: Record<string, T>, teFile: string, newId: string, options: SaveAsOptions = {}): Record<string, T> {
  const description = options.description?.trim() || null;
  const out: Record<string, T> = { ...files };
  const te = files[teFile];
  // The id the bundle had: the manifest's, else the te file's (for the tier files' header comments).
  const manifestText = files[MANIFEST];
  const oldId = (manifestText !== undefined ? readManifest(manifestText).id : null)
    ?? (te !== undefined ? readTe(te).id : null);
  if (te !== undefined) {
    const withId = setTwinId(te, newId);
    out[teFile] = (description ? setTwinDescription(withId, description) : withId) as T;
  }
  const manifest = files[MANIFEST];
  if (manifest !== undefined) {
    const withId = setManifestId(manifest, newId);
    out[MANIFEST] = (description ? setManifestDescription(withId, description) : withId) as T;
  }
  // Every tier file names the twin it is for (`traffic-profile.target`, `tap-plan.target`,
  // `exercise.twin`): a copy that kept the old id would apply to the twin it was cloned from.
  for (const [path, text] of Object.entries(files)) {
    if (text === undefined || !isTierFile(path)) continue;
    out[path] = retargetTierFile(text, newId, oldId) as T;
  }
  return out;
}

/** D6: the twin's `cluster:` -- the target it deploys to and its dropped devices' images are chosen
 *  for (null: removed, the default target). A bundle with no manifest is refused. */
export function setTwinCluster(doc: WorkingDoc, cluster: string | null): Files {
  const m = doc.files[MANIFEST];
  if (m === undefined) throw new Error(`the design has no ${MANIFEST} to name a target in`);
  return { ...doc.files, [MANIFEST]: setManifestCluster(m, cluster) };
}
