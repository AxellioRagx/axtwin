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
import { MANIFEST, readManifest, setManifestId, wireTfcDefault, type Manifest } from "./manifest";
import {
  addNode,
  addSegment as teAddSegment,
  copyNames,
  readTe,
  removeNode as teRemoveNode,
  removeSegment as teRemoveSegment,
  renameNode as teRenameNode,
  renameSegment as teRenameSegment,
  setNodeField as teSetNodeField,
  setNodeNets,
  setSegmentField as teSetSegmentField,
  setTwinId,
  type NewNode,
  type TeModel,
  type TeNet,
  type TeNode,
} from "./te";
import { emptyTfc, readTfc, removeAssignment, renameNodeBindings, setAssignment, type AssignmentPatch, type TfcModel } from "./tfc";
import { validateTe } from "./validate";

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
  /** The canvas's own structural findings (`validate.ts`); the API's are shown beside them. */
  findings: ValidationFinding[];
}

export const DEFAULT_TFC_PATH = "tfc/default.yaml";

export function tfcPathOf(doc: WorkingDoc, manifest: Manifest | null): string | null {
  if (manifest?.tfc && doc.files[manifest.tfc] !== undefined) return manifest.tfc;
  if (doc.files[DEFAULT_TFC_PATH] !== undefined) return DEFAULT_TFC_PATH;
  const any = Object.keys(doc.files).find((p) => p.startsWith("tfc/") && p.endsWith(".yaml"));
  return any ?? null;
}

export function readDoc(doc: WorkingDoc): DocModel {
  const teText = doc.files[doc.teFile];
  const te = teText === undefined ? { id: null, description: null, params: {}, segments: [], nodes: [], problems: [`the document has no '${doc.teFile}'`] } : readTe(teText);
  const manifestText = doc.files[MANIFEST];
  const manifest = manifestText === undefined ? null : readManifest(manifestText);
  const tfcPath = tfcPathOf(doc, manifest);
  const tfc = tfcPath ? readTfc(doc.files[tfcPath]!) : null;
  return { te, manifest, tfcPath, tfc, findings: validateTe(te) };
}

// ---------------------------------------------------------------------------
// The document as a graph: the Phase 1 authored pipeline over the twin config
// ---------------------------------------------------------------------------
export function documentTopology(te: TeModel, id: string): Topology {
  const nodes: Topology["nodes"] = te.segments.map((s) => ({ id: s.name, type: "network", cidr: s.cidr }));
  const edges: Topology["edges"] = [];
  for (const n of te.nodes) {
    nodes.push({ id: n.name, type: n.kind === "router" ? "router" : "host", image: n.image });
    // The chip is labelled by name; the pinned address rides on the attachment label (EditContext), not here.
    for (const leg of n.nets) edges.push({ source: n.name, target: leg.segment, ip: null });
  }
  return { scenario_id: id, nodes, edges };
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
    const nodes = te.nodes.filter((n) => n.kind === "host" && n.nets[0]?.segment === s.name);
    return { name: s.name, cidr: s.cidr, zone_type: s.zoneType, hosts: nodes.reduce((a, n) => a + n.countValue, 0), roles: [], taps: [], nodes: nodes.map(treeNode) };
  });
  return { id, hosts: te.nodes.filter((n) => n.kind === "host").reduce((a, n) => a + n.countValue, 0), segments, routers: te.nodes.filter((n) => n.kind === "router").map(treeNode), warnings: [] };
}

export function documentGraph(model: DocModel, id: string): AuthoredGraph {
  return authoredGraph(documentTopology(model.te, id), documentTree(model.te, id));
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
  kind?: "host" | "router";
  /** The segment each copy attaches to; `null` places unattached. */
  segment: string | null;
  requires?: string[];
  resources?: Record<string, string | number> | null;
  /** A `count:` written on each copy (a template's fan-out), as opposed to N copies. */
  fanOut?: number | string;
}

/** Place `count` copies of a node template on a segment, each named
 *  `{name}-{n}` and pinned to the segment's next free address (C3.2). */
export function placeNodes(doc: WorkingDoc, model: DocModel, req: PlaceRequest): { files: Files; names: string[] } {
  const taken = new Set(model.te.nodes.map((n) => n.name));
  const names = copyNames(req.name, Math.max(1, req.count), taken);
  let addresses: string[] = [];
  if (req.segment) {
    if (!model.te.segments.some((s) => s.name === req.segment)) throw new Error(`no segment named '${req.segment}'`);
    const fanOut = req.fanOut !== undefined && req.fanOut !== 1;
    if (!fanOut) {
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
  if (n.countValue === 1 && typeof n.count === "number") {
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

export function detach(doc: WorkingDoc, model: DocModel, node: string, segment: string): Files {
  const n = model.te.nodes.find((x) => x.name === node);
  if (!n) throw new Error(`no node group named '${node}'`);
  return withTe(doc, (t) => setNodeNets(t, node, n.nets.filter((l) => l.segment !== segment)));
}

export function setAttachmentAddress(doc: WorkingDoc, model: DocModel, node: string, segment: string, ip: string | null): Files {
  const n = model.te.nodes.find((x) => x.name === node);
  if (!n) throw new Error(`no node group named '${node}'`);
  if (!n.nets.some((l) => l.segment === segment)) throw new Error(`'${node}' is not on ${segment}`);
  return withTe(doc, (t) => setNodeNets(t, node, n.nets.map((l) => (l.segment === segment ? { ...l, ip } : l))));
}

export function setNodeField(doc: WorkingDoc, node: string, key: "role" | "image" | "count" | "kind", value: string | number | null): Files {
  return withTe(doc, (t) => teSetNodeField(t, node, key, value));
}

export function renameNode(doc: WorkingDoc, model: DocModel, from: string, to: string): Files {
  const files = withTe(doc, (t) => teRenameNode(t, from, to));
  if (model.tfcPath && files[model.tfcPath] !== undefined) files[model.tfcPath] = renameNodeBindings(files[model.tfcPath]!, from, to);
  return files;
}

export function removeNode(doc: WorkingDoc, model: DocModel, node: string): Files {
  const files = withTe(doc, (t) => teRemoveNode(t, node));
  if (model.tfcPath && files[model.tfcPath] !== undefined) files[model.tfcPath] = removeAssignment(files[model.tfcPath]!, "node", node);
  return files;
}

export function addSegment(doc: WorkingDoc, seg: { name: string; cidr: string; zoneType?: string | null }): Files {
  return withTe(doc, (t) => teAddSegment(t, seg));
}

export function setSegmentField(doc: WorkingDoc, name: string, key: "cidr" | "zone_type", value: string | null): Files {
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
  text = teRemoveSegment(text, name);
  return { ...doc.files, [doc.teFile]: text };
}

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

/** The files a Save-As writes: the same document under a new id. */
export function saveAsFiles(doc: WorkingDoc, newId: string): Files {
  const files = withTe(doc, (t) => setTwinId(t, newId));
  if (files[MANIFEST] !== undefined) files[MANIFEST] = setManifestId(files[MANIFEST]!, newId);
  return files;
}
