/**
 * The twin config (`te.yaml`) as the canvas reads and edits it: segments,
 * node groups and their attachments (`net` / `nets[]`), read from the
 * working document's text, and the edits written back as LINE SPLICES so
 * every untouched line stays byte-for-byte (`yaml.ts`). The engine's loader
 * (`axrange.twinconfig`) is the authority on the file's meaning; this module
 * knows only the fields the canvas draws and changes.
 *
 * Every edit is `(text, ...) => text` and pure; the command stack
 * (`commands.ts`) keeps the before-text, so undo is a restore, not a reverse
 * edit.
 */
import {
  toPlain,
  contentEnd,
  encodeScalar,
  entry,
  get,
  insertLines,
  itemName,
  joinLines,
  parseYaml,
  removeSpan,
  replaceValueOnLine,
  rewriteFlowMap,
  scalarText,
  type YDoc,
  type YItem,
  type YMap,
  type YNode,
  type YSeq,
} from "./yaml";

export interface TeNet {
  segment: string;
  /** A pinned address, or `null` when the planner allocates one at deploy. */
  ip: string | null;
}

/** Provenance (`docs/design/topology-layers.md` §3.1): absent means `authored`. */
export type TeSource = "discovered" | "asserted" | "authored" | "unconfirmed";

export interface TeSegment {
  name: string;
  cidr: string;
  zoneType: string | null;
  isolated: boolean;
  /** `egress: true`: the segment is the twin's uplink (IR `Network.egress`), so a router with a leg on it is a gateway. */
  egress: boolean;
  source: TeSource | null;
  confidence: number | null;
}

/** A `members[]` entry: one instance's role override (topology-layers §5). */
export interface TeMember {
  index: number;
  role: string | null;
  source: TeSource | null;
}

/** A `links[]` entry: a wire between two device ports (topology-layers §2.3). */
export interface TeLink {
  a: { device: string; port: string };
  b: { device: string; port: string };
  mode: "trunk" | "access";
  native: string | null;
  vlans: string[];
  state: "up" | "down" | "blocked";
  source: TeSource | null;
  confidence: number | null;
  /** The list index, for edits that address the entry. */
  index: number;
}

export interface TeNode {
  name: string;
  kind: "host" | "router" | "switch";
  role: string | null;
  /** Empty on an imageless switch (it deploys as nothing but its segments' vnets). */
  image: string;
  /** The `count` as written: a number, a `${PARAM:default}` reference, or 1 when absent. */
  count: number | string;
  /** The count as a number when it can be known (a param reference's default). */
  countValue: number;
  nets: TeNet[];
  requires: string[];
  resources: Record<string, string | number> | null;
  members: TeMember[];
  source: TeSource | null;
  confidence: number | null;
  /** Keys this reader does not model but the node carries (install, provides, joins, ...). */
  otherKeys: string[];
}

export interface TeModel {
  id: string | null;
  description: string | null;
  params: Record<string, string | number | boolean | null>;
  segments: TeSegment[];
  nodes: TeNode[];
  links: TeLink[];
  /** What the reader could not make sense of; the engine's validator says more. */
  problems: string[];
}

const NODE_KEY_ORDER = ["name", "role", "kind", "image", "requires", "resources", "count", "net", "nets", "members", "source", "confidence", "vantage", "when", "install", "provides", "joins", "persona", "observation"];
const KNOWN_NODE_KEYS = new Set(["name", "role", "kind", "image", "count", "net", "nets", "requires", "resources", "members", "source", "confidence", "vantage"]);
const SOURCES = new Set(["discovered", "asserted", "authored", "unconfirmed"]);

function sourceOf(raw: YNode | null): TeSource | null {
  const s = scalarText(raw);
  return s && SOURCES.has(s) ? (s as TeSource) : null;
}

function numberOf(raw: YNode | null): number | null {
  const s = scalarText(raw);
  if (s === null) return null;
  const n = Number(s);
  return Number.isFinite(n) ? n : null;
}

export function emptyTe(problem?: string): TeModel {
  return { id: null, description: null, params: {}, segments: [], nodes: [], links: [], problems: problem ? [problem] : [] };
}

// ---------------------------------------------------------------------------
// Reading
// ---------------------------------------------------------------------------
export function countOf(raw: YNode | null): { count: number | string; value: number } {
  const t = scalarText(raw);
  if (t === null) return { count: 1, value: 1 };
  if (/^\d+$/.test(t)) return { count: Number(t), value: Number(t) };
  const m = /^\$\{[A-Za-z_][A-Za-z0-9_]*:(\d+)\}$/.exec(t);
  if (m) return { count: t, value: Number(m[1]) };
  return { count: t, value: 1 };
}

function netsOf(node: YNode): TeNet[] {
  const net = scalarText(get(node, "net"));
  const nets = get(node, "nets");
  const out: TeNet[] = [];
  if (net) out.push({ segment: net, ip: null });
  if (nets && nets.kind === "seq") {
    for (const it of nets.items) {
      if (it.value.kind === "map") {
        const seg = scalarText(get(it.value, "segment"));
        if (seg) out.push({ segment: seg, ip: scalarText(get(it.value, "ip")) });
      } else {
        const seg = scalarText(it.value);
        if (seg) out.push({ segment: seg, ip: null });
      }
    }
  }
  return out;
}

function stringList(node: YNode | null): string[] {
  if (!node || node.kind !== "seq") return [];
  return node.items.map((i) => scalarText(i.value)).filter((s): s is string => s !== null);
}

export function readTe(text: string): TeModel {
  const doc = parseYaml(text);
  const tc = get(doc.root, "twin_config");
  const problems: string[] = [];
  if (!tc || tc.kind !== "map") {
    return emptyTe("no `twin_config:` mapping at the top of the file");
  }
  const params: TeModel["params"] = {};
  const p = get(tc, "params");
  if (p && p.kind === "map") for (const e of p.entries) params[e.key] = e.value.kind === "scalar" ? e.value.value : null;
  const segments: TeSegment[] = [];
  const segs = get(tc, "segments");
  if (segs && segs.kind === "seq") {
    for (const it of segs.items) {
      const name = itemName(it);
      const cidr = scalarText(get(it.value, "cidr"));
      if (!name || !cidr) {
        problems.push(`segment at line ${it.line + 1} has no name or cidr`);
        continue;
      }
      segments.push({ name, cidr, zoneType: scalarText(get(it.value, "zone_type")), isolated: scalarText(get(it.value, "isolated")) === "true", egress: scalarText(get(it.value, "egress")) === "true", source: sourceOf(get(it.value, "source")), confidence: numberOf(get(it.value, "confidence")) });
    }
  }
  const nodes: TeNode[] = [];
  const ns = get(tc, "nodes");
  if (ns && ns.kind === "seq") {
    for (const it of ns.items) {
      const name = itemName(it);
      if (!name || it.value.kind !== "map") {
        problems.push(`node at line ${it.line + 1} has no name`);
        continue;
      }
      const c = countOf(get(it.value, "count"));
      const res = get(it.value, "resources");
      const resources: Record<string, string | number> = {};
      if (res && res.kind === "map") for (const e of res.entries) if (e.value.kind === "scalar" && e.value.value !== null && typeof e.value.value !== "boolean") resources[e.key] = e.value.value;
      const kindText = scalarText(get(it.value, "kind"));
      const members: TeMember[] = [];
      const ms = get(it.value, "members");
      if (ms && ms.kind === "seq") {
        for (const m of ms.items) {
          const index = numberOf(get(m.value, "index"));
          if (index === null) {
            problems.push(`node ${name}: a members[] entry at line ${m.line + 1} has no index`);
            continue;
          }
          members.push({ index, role: scalarText(get(m.value, "role")), source: sourceOf(get(m.value, "source")) });
        }
      }
      nodes.push({
        name,
        kind: kindText === "router" ? "router" : kindText === "switch" ? "switch" : "host",
        role: scalarText(get(it.value, "role")),
        image: scalarText(get(it.value, "image")) ?? "",
        count: c.count,
        countValue: c.value,
        nets: netsOf(it.value),
        requires: stringList(get(it.value, "requires")),
        resources: res ? resources : null,
        members,
        source: sourceOf(get(it.value, "source")),
        confidence: numberOf(get(it.value, "confidence")),
        otherKeys: it.value.entries.map((e) => e.key).filter((k) => !KNOWN_NODE_KEYS.has(k)),
      });
    }
  }
  const links: TeLink[] = [];
  const ls = get(tc, "links");
  if (ls && ls.kind === "seq") {
    ls.items.forEach((it, index) => {
      const a = get(it.value, "a");
      const b = get(it.value, "b");
      const end = (raw: YNode | null) => {
        const device = scalarText(get(raw, "device"));
        const port = scalarText(get(raw, "port"));
        return device && port ? { device, port } : null;
      };
      const ea = end(a);
      const eb = end(b);
      const modeText = scalarText(get(it.value, "mode"));
      if (!ea || !eb || (modeText !== "trunk" && modeText !== "access")) {
        problems.push(`link at line ${it.line + 1} needs a and b {device, port} and a mode (trunk | access)`);
        return;
      }
      const stateText = scalarText(get(it.value, "state"));
      links.push({
        a: ea,
        b: eb,
        mode: modeText,
        native: scalarText(get(it.value, "native")),
        vlans: stringList(get(it.value, "vlans")),
        state: stateText === "down" || stateText === "blocked" ? stateText : "up",
        source: sourceOf(get(it.value, "source")),
        confidence: numberOf(get(it.value, "confidence")),
        index,
      });
    });
  }
  return { id: scalarText(get(tc, "id")), description: scalarText(get(tc, "description")), params, segments, nodes, links, problems };
}

// ---------------------------------------------------------------------------
// Locating
// ---------------------------------------------------------------------------
class TeText {
  readonly doc: YDoc;
  readonly lines: string[];
  constructor(readonly text: string) {
    this.doc = parseYaml(text);
    this.lines = [...this.doc.lines];
  }
  get tc(): YMap {
    const tc = get(this.doc.root, "twin_config");
    if (!tc || tc.kind !== "map") throw new Error("the twin config has no `twin_config:` mapping to edit");
    return tc;
  }
  seq(key: "segments" | "nodes" | "links"): YSeq | null {
    const s = get(this.tc, key);
    if (!s) return null;
    if (s.kind !== "seq") {
      if (s.kind === "scalar" && s.value === null) return null;
      throw new Error(`\`${key}:\` is not a list`);
    }
    return s;
  }
  node(name: string): YItem {
    const ns = this.seq("nodes");
    const it = ns?.items.find((i) => itemName(i) === name);
    if (!it || it.value.kind !== "map") throw new Error(`no node group named '${name}'`);
    return it;
  }
  segment(name: string): YItem {
    const ss = this.seq("segments");
    const it = ss?.items.find((i) => itemName(i) === name);
    if (!it || it.value.kind !== "map") throw new Error(`no segment named '${name}'`);
    return it;
  }
  out(): string {
    return joinLines(this.lines, this.doc.trailingNewline);
  }
}

/** Where a new item of a list goes, and at what indent, matching the list's
 *  own style; for an empty or absent list, a fresh `key:` line is written
 *  under `twin_config` and the indent follows the mapping's. */
function listSlot(t: TeText, key: "segments" | "nodes" | "links"): { at: number; dashIndent: number; blankBefore: boolean } {
  const seq = t.seq(key);
  if (seq && !seq.flow && seq.items.length) {
    const last = seq.items[seq.items.length - 1]!;
    const prev = seq.items[seq.items.length - 2];
    const gap = prev ? contentEnd(t.lines, prev.end) : -1;
    const blankBefore = prev !== undefined && gap < last.start && t.lines[gap]?.trim() === "";
    return { at: contentEnd(t.lines, last.end), dashIndent: seq.indent, blankBefore };
  }
  const tc = t.tc;
  const e = entry(tc, key);
  if (e) {
    // `key: []` or `key:` with nothing under it: rewrite the key line as a bare `key:`.
    const raw = t.lines[e.line]!;
    t.lines[e.line] = raw.slice(0, raw.indexOf(key) + key.length + 1);
    return { at: e.line + 1, dashIndent: tc.indent + 2, blankBefore: false };
  }
  // No list at all: put one after the last entry that precedes it in the usual order.
  const order = ["id", "description", "params", "segments", "nodes", "links"];
  const idx = order.indexOf(key);
  let at = contentEnd(t.lines, tc.end);
  for (const prior of tc.entries) {
    const pi = order.indexOf(prior.key);
    if (pi !== -1 && pi > idx) {
      at = prior.start;
      break;
    }
  }
  insertLines(t.lines, at, [`${" ".repeat(tc.indent)}${key}:`]);
  return { at: at + 1, dashIndent: tc.indent + 2, blankBefore: false };
}

// ---------------------------------------------------------------------------
// Segments
// ---------------------------------------------------------------------------
export function addSegment(text: string, seg: { name: string; cidr: string; zoneType?: string | null }): string {
  const t = new TeText(text);
  if (t.seq("segments")?.items.some((i) => itemName(i) === seg.name)) throw new Error(`a segment named '${seg.name}' already exists`);
  const slot = listSlot(t, "segments");
  const pad = " ".repeat(slot.dashIndent);
  const zone = seg.zoneType ? `, zone_type: ${encodeScalar(seg.zoneType)}` : "";
  insertLines(t.lines, slot.at, [`${pad}- { name: ${encodeScalar(seg.name)}, cidr: ${encodeScalar(seg.cidr)}${zone} }`]);
  return t.out();
}

export function setSegmentField(text: string, name: string, key: "name" | "cidr" | "zone_type", value: string | null): string {
  const t = new TeText(text);
  const it = t.segment(name);
  const m = it.value as YMap;
  if (m.flow) {
    rewriteFlowMap(t.lines, m, key, value === null ? null : encodeScalar(value));
    return t.out();
  }
  const e = entry(m, key);
  if (e) {
    if (value === null) removeSpan(t.lines, e.start, e.end, m.indent);
    else replaceValueOnLine(t.lines, e, encodeScalar(value));
  } else if (value !== null) {
    insertLines(t.lines, contentEnd(t.lines, it.end), [`${" ".repeat(m.indent)}${key}: ${encodeScalar(value)}`]);
  }
  return t.out();
}

export function removeSegment(text: string, name: string): string {
  const t = new TeText(text);
  const it = t.segment(name);
  const seq = t.seq("segments")!;
  removeSpan(t.lines, it.start, it.end, seq.indent);
  return t.out();
}

/** Rename a segment and every attachment that names it. */
export function renameSegment(text: string, from: string, to: string): string {
  let out = setSegmentField(text, from, "name", to);
  const model = readTe(out);
  for (const n of model.nodes) {
    if (n.nets.some((x) => x.segment === from)) {
      out = setNodeNets(out, n.name, n.nets.map((x) => (x.segment === from ? { ...x, segment: to } : x)));
    }
  }
  return out;
}

// ---------------------------------------------------------------------------
// Nodes
// ---------------------------------------------------------------------------
export interface NewNode {
  name: string;
  kind?: "host" | "router" | "switch";
  role?: string | null;
  image: string;
  count?: number | string;
  nets: TeNet[];
  requires?: string[];
  resources?: Record<string, string | number> | null;
}

function netLines(indent: number, nets: readonly TeNet[]): string[] {
  const pad = " ".repeat(indent);
  if (nets.length === 0) return [];
  if (nets.length === 1 && nets[0]!.ip === null) return [`${pad}net: ${encodeScalar(nets[0]!.segment)}`];
  return [`${pad}nets:`, ...nets.map((n) => `${pad}  - { segment: ${encodeScalar(n.segment)}${n.ip ? `, ip: ${encodeScalar(n.ip)}` : ""} }`)];
}

export function addNode(text: string, node: NewNode): string {
  const t = new TeText(text);
  if (t.seq("nodes")?.items.some((i) => itemName(i) === node.name)) throw new Error(`a node group named '${node.name}' already exists`);
  const slot = listSlot(t, "nodes");
  const dash = " ".repeat(slot.dashIndent);
  const keyIndent = slot.dashIndent + 2;
  const pad = " ".repeat(keyIndent);
  const lines: string[] = [];
  if (slot.blankBefore) lines.push("");
  lines.push(`${dash}- name: ${encodeScalar(node.name)}`);
  if (node.role) lines.push(`${pad}role: ${encodeScalar(node.role)}`);
  if (node.kind === "router" || node.kind === "switch") lines.push(`${pad}kind: ${node.kind}`);
  lines.push(`${pad}image: ${encodeScalar(node.image)}`);
  if (node.requires?.length) lines.push(`${pad}requires: [${node.requires.map(encodeScalar).join(", ")}]`);
  if (node.resources && Object.keys(node.resources).length) lines.push(`${pad}resources: { ${Object.entries(node.resources).map(([k, v]) => `${k}: ${encodeScalar(v)}`).join(", ")} }`);
  if (node.count !== undefined && node.count !== 1) lines.push(`${pad}count: ${typeof node.count === "number" ? node.count : encodeScalar(node.count)}`);
  lines.push(...netLines(keyIndent, node.nets));
  insertLines(t.lines, slot.at, lines);
  return t.out();
}

export function removeNode(text: string, name: string): string {
  const t = new TeText(text);
  const it = t.node(name);
  removeSpan(t.lines, it.start, it.end, t.seq("nodes")!.indent);
  return t.out();
}

/** Set (or with `null`, remove) a scalar field of a node group, keeping the
 *  node's other lines; a new key lands at its conventional place. */
export function setNodeField(text: string, name: string, key: "name" | "role" | "image" | "count" | "kind", value: string | number | null): string {
  const t = new TeText(text);
  const it = t.node(name);
  const m = it.value as YMap;
  const e = entry(m, key);
  const raw = value === null ? null : typeof value === "number" ? String(value) : key === "count" && /^\$\{.*\}$/.test(value) ? JSON.stringify(value) : encodeScalar(value);
  if (e) {
    if (raw === null) removeSpan(t.lines, e.start, e.end, m.indent);
    else if (e.valueCol === -1) {
      removeSpan(t.lines, e.start, e.end, m.indent);
      insertLines(t.lines, e.start, [`${" ".repeat(m.indent)}${key}: ${raw}`]);
    } else replaceValueOnLine(t.lines, e, raw);
    return t.out();
  }
  if (raw === null) return text;
  const at = insertionLine(t, it, key);
  insertLines(t.lines, at, [`${" ".repeat(m.indent)}${key}: ${raw}`]);
  return t.out();
}

/** The line a new node key goes on: before the first existing key that
 *  follows it in the conventional order, else after the node's last line. */
function insertionLine(t: TeText, it: YItem, key: string): number {
  const m = it.value as YMap;
  const idx = NODE_KEY_ORDER.indexOf(key);
  for (const e of m.entries) {
    const ei = NODE_KEY_ORDER.indexOf(e.key);
    if (ei === -1) continue;
    if (ei > idx) return e.start;
  }
  return contentEnd(t.lines, it.end);
}

/** Replace a node group's attachments: `net: X` for one unpinned leg, a
 *  `nets:` list otherwise, written where the old attachment was. */
export function setNodeNets(text: string, name: string, nets: readonly TeNet[]): string {
  const t = new TeText(text);
  const it = t.node(name);
  const m = it.value as YMap;
  const seen = new Set<string>();
  for (const n of nets) {
    if (seen.has(n.segment)) throw new Error(`node '${name}' would attach to segment '${n.segment}' twice`);
    seen.add(n.segment);
  }
  const old = m.entries.filter((e) => e.key === "net" || e.key === "nets");
  let at: number;
  if (old.length) {
    // The old attachment's own lines go (its comments stay); the new one lands where it began.
    at = old[0]!.start;
    for (const e of [...old].reverse()) t.lines.splice(e.start, e.end - e.start);
  } else at = insertionLine(t, it, "net");
  insertLines(t.lines, at, netLines(m.indent, nets));
  return t.out();
}

/** Rename a node group; traffic-profile references are the caller's (`tfc.ts`). */
export function renameNode(text: string, from: string, to: string): string {
  const t = new TeText(text);
  if (t.seq("nodes")?.items.some((i) => itemName(i) === to)) throw new Error(`a node group named '${to}' already exists`);
  return setNodeField(text, from, "name", to);
}

/** The `twin_config.id` line, rewritten for a Save-As. */
export function setTwinId(text: string, id: string): string {
  const t = new TeText(text);
  const e = entry(t.tc, "id");
  if (!e) {
    insertLines(t.lines, t.tc.start, [`${" ".repeat(t.tc.indent)}id: ${encodeScalar(id)}`]);
    return t.out();
  }
  replaceValueOnLine(t.lines, e, encodeScalar(id));
  return t.out();
}

/** `{name}-{n}` names for N copies, skipping names the twin already holds. */
export function copyNames(base: string, count: number, taken: ReadonlySet<string>): string[] {
  const out: string[] = [];
  let n = 1;
  while (out.length < count) {
    const candidate = count === 1 && !taken.has(base) ? base : `${base}-${n}`;
    n++;
    if (!taken.has(candidate) && !out.includes(candidate)) out.push(candidate);
    if (n > count + taken.size + 1000) break;
  }
  return out;
}

// ---------------------------------------------------------------------------
// Members (topology-layers §5) and links (§2.3)
// ---------------------------------------------------------------------------
/** Set (or with `null` remove) one instance's role override on a `count: N`
 *  group: `members: [{ index, role }]`, one flow-map entry per instance. */
export function setMemberRole(text: string, name: string, index: number, role: string | null): string {
  const t = new TeText(text);
  const it = t.node(name);
  const m = it.value as YMap;
  const e = entry(m, "members");
  const seq = e && e.value.kind === "seq" && !e.value.flow ? e.value : null;
  const found = seq?.items.find((x) => scalarText(get(x.value, "index")) === String(index)) ?? null;
  if (found) {
    if (role === null) {
      if (seq!.items.length === 1 && e) removeSpan(t.lines, e.start, e.end, m.indent);
      else removeSpan(t.lines, found.start, found.end, seq!.indent);
      return t.out();
    }
    if (found.value.kind === "map" && found.value.flow) rewriteFlowMap(t.lines, found.value, "role", encodeScalar(role));
    else if (found.value.kind === "map") {
      const re = entry(found.value, "role");
      if (re) replaceValueOnLine(t.lines, re, encodeScalar(role));
      else insertLines(t.lines, contentEnd(t.lines, found.end), [`${" ".repeat(found.value.indent)}role: ${encodeScalar(role)}`]);
    }
    return t.out();
  }
  if (role === null) return text;
  const line = (dashIndent: number) => `${" ".repeat(dashIndent)}- { index: ${index}, role: ${encodeScalar(role)} }`;
  if (seq && seq.items.length) insertLines(t.lines, contentEnd(t.lines, seq.end), [line(seq.indent)]);
  else if (e) {
    const raw = t.lines[e.line]!;
    t.lines[e.line] = raw.slice(0, raw.indexOf("members") + "members:".length);
    insertLines(t.lines, e.line + 1, [line(m.indent + 2)]);
  } else {
    const at = insertionLine(t, it, "members");
    insertLines(t.lines, at, [`${" ".repeat(m.indent)}members:`, line(m.indent + 2)]);
  }
  return t.out();
}

export interface NewLink {
  a: { device: string; port: string };
  b: { device: string; port: string };
  mode: "trunk" | "access";
  native?: string | null;
  vlans: string[];
  state?: "up" | "down" | "blocked" | null;
  source?: TeSource | null;
}

function linkLines(dashIndent: number, link: NewLink): string[] {
  const pad = " ".repeat(dashIndent + 2);
  const out = [`${" ".repeat(dashIndent)}- a: { device: ${encodeScalar(link.a.device)}, port: ${encodeScalar(link.a.port)} }`, `${pad}b: { device: ${encodeScalar(link.b.device)}, port: ${encodeScalar(link.b.port)} }`, `${pad}mode: ${link.mode}`];
  if (link.native) out.push(`${pad}native: ${encodeScalar(link.native)}`);
  out.push(`${pad}vlans: [${link.vlans.map((v) => encodeScalar(v)).join(", ")}]`);
  if (link.state && link.state !== "up") out.push(`${pad}state: ${link.state}`);
  if (link.source) out.push(`${pad}source: ${link.source}`);
  return out;
}

/** Append a `links[]` entry. */
export function addLink(text: string, link: NewLink): string {
  const t = new TeText(text);
  const slot = listSlot(t, "links");
  insertLines(t.lines, slot.at, linkLines(slot.dashIndent, link));
  return t.out();
}

export function removeLink(text: string, index: number): string {
  const t = new TeText(text);
  const seq = t.seq("links");
  const it = seq?.items[index];
  if (!seq || !it) return text;
  if (seq.items.length === 1) {
    const e = entry(t.tc, "links");
    if (e) removeSpan(t.lines, e.start, e.end, t.tc.indent);
  } else removeSpan(t.lines, it.start, it.end, seq.indent);
  return t.out();
}

/** Set one field of a `links[]` entry: `mode`, `state`, `native`, `vlans`
 *  (a list), or an end's `device` / `port` (`a.port`). `null` removes an
 *  optional key. */
/** A plain value as flow YAML: maps `{ k: v }`, lists `[a, b]`, scalars quoted as needed;
 *  `mode` / `state` / `source` values are bare words. */
function flowText(v: unknown): string {
  if (Array.isArray(v)) return `[${v.map((x) => flowText(x)).join(", ")}]`;
  if (v && typeof v === "object") return `{ ${Object.entries(v as Record<string, unknown>).map(([k, x]) => `${k}: ${flowText(x)}`).join(", ")} }`;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  if (v === null || v === undefined) return "null";
  return encodeScalar(String(v));
}

export function setLinkField(text: string, index: number, key: "mode" | "state" | "native" | "vlans" | "a.device" | "a.port" | "b.device" | "b.port", value: string | string[] | null): string {
  const t = new TeText(text);
  const seq = t.seq("links");
  const it = seq?.items[index];
  if (!it || it.value.kind !== "map") throw new Error(`no link at index ${index}`);
  const m = it.value;
  // A link written as one flow line (`- { a: { device, port }, b: ..., mode, vlans }`) is
  // re-serialised whole: a nested flow map is not addressable on its own.
  if (m.flow && m.flowLine !== undefined && m.flowCol !== undefined && m.flowEnd !== undefined) {
    const plain = toPlain(m) as Record<string, unknown>;
    if (key.includes(".")) {
      const [end, field] = key.split(".") as ["a" | "b", "device" | "port"];
      if (typeof value !== "string" || !value.trim()) throw new Error(`a link end's ${field} is a non-empty string`);
      const endMap = plain[end];
      if (!endMap || typeof endMap !== "object") throw new Error(`link ${index} has no ${end} end`);
      (endMap as Record<string, unknown>)[field] = value.trim();
    } else if (value === null) delete plain[key];
    else plain[key] = value;
    const raw = t.lines[m.flowLine]!;
    t.lines[m.flowLine] = raw.slice(0, m.flowCol) + flowText(plain) + raw.slice(m.flowEnd);
    return t.out();
  }
  if (key.includes(".")) {
    const [end, field] = key.split(".") as ["a" | "b", "device" | "port"];
    const endNode = get(m, end);
    if (!endNode || endNode.kind !== "map") throw new Error(`link ${index} has no ${end} end`);
    if (typeof value !== "string" || !value.trim()) throw new Error(`a link end's ${field} is a non-empty string`);
    if (endNode.flow) rewriteFlowMap(t.lines, endNode, field, encodeScalar(value.trim()));
    else {
      const fe = entry(endNode, field);
      if (fe) replaceValueOnLine(t.lines, fe, encodeScalar(value.trim()));
      else insertLines(t.lines, contentEnd(t.lines, endNode.end), [`${" ".repeat(endNode.indent)}${field}: ${encodeScalar(value.trim())}`]);
    }
    return t.out();
  }
  const raw = value === null ? null : Array.isArray(value) ? `[${value.map((v) => encodeScalar(v)).join(", ")}]` : key === "mode" || key === "state" ? value : encodeScalar(value);
  const e = entry(m, key);
  if (e) {
    if (raw === null) removeSpan(t.lines, e.start, e.end, m.indent);
    else {
      const line = t.lines[e.line]!;
      t.lines[e.line] = line.slice(0, e.valueCol) + raw;
      if (e.end > e.line + 1) removeSpan(t.lines, e.line + 1, e.end, m.indent);
    }
  } else if (raw !== null) insertLines(t.lines, contentEnd(t.lines, it.end), [`${" ".repeat(m.indent)}${key}: ${raw}`]);
  return t.out();
}
