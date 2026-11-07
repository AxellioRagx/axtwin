/**
 * Subtree select, duplicate and attach (`docs/design/topology-layers.md` §6,
 * track 5d). A device's subtree is the device and everything the links hang
 * below it: the deeper devices, the segments they carry, the host groups on
 * those segments, their member overrides and the links among them. Duplicate
 * copies the lot under `{name}-{n}` names, every copied segment taking the
 * next free `/24` in its two-octet prefix and its hosts addresses in it;
 * roles, personas, counts and member overrides copy; taps never do; the copy
 * is authored. Attach writes one `links[]` entry between two devices (a
 * trunk carrying what the deeper one carries) or a device and a host (an
 * access link on the host's first segment), with free ports at both ends.
 */
import { allocateHostAddresses, nextFreeSlash24, takenAddresses } from "./allocate";
import type { Files } from "./commands";
import { readDoc, setPersona, type DocModel, type WorkingDoc } from "./document";
import { instanceId } from "./members";
import { addLink, addNode, addSegment, setMemberRole, type TeLink, type TeModel, type TeNode } from "./te";

export interface Subtree {
  root: string;
  /** The root and every device below it (switches and routers), the root first. */
  devices: string[];
  /** The segments whose deepest carrier is in the subtree. */
  segments: string[];
  /** The host groups on those segments or wired to a subtree device. */
  hosts: string[];
  /** The `links[]` indices with an end in the subtree (the uplink excluded). */
  links: number[];
  /** The link from the root up to a shallower device, or null when the root is parked or a root of its own. */
  uplink: TeLink | null;
}

function isDevice(n: TeNode): boolean {
  return n.kind === "switch" || n.kind === "router";
}

function segmentsOf(l: Pick<TeLink, "native" | "vlans">): string[] {
  const out: string[] = [];
  for (const s of [...(l.native ? [l.native] : []), ...l.vlans]) if (!out.includes(s)) out.push(s);
  return out;
}

/** Depth from the routers (0) along `up` links between devices; a device the routers do not reach has none. */
export function deviceDepths(te: TeModel): Map<string, number> {
  const devices = new Set(te.nodes.filter(isDevice).map((n) => n.name));
  const adj = new Map<string, string[]>();
  for (const l of te.links) {
    if (l.state !== "up" || !devices.has(l.a.device) || !devices.has(l.b.device)) continue;
    adj.set(l.a.device, [...(adj.get(l.a.device) ?? []), l.b.device]);
    adj.set(l.b.device, [...(adj.get(l.b.device) ?? []), l.a.device]);
  }
  const depth = new Map<string, number>();
  const queue: string[] = [];
  for (const n of te.nodes) if (n.kind === "router") { depth.set(n.name, 0); queue.push(n.name); }
  while (queue.length) {
    const d = queue.shift()!;
    for (const o of adj.get(d) ?? []) if (!depth.has(o)) { depth.set(o, depth.get(d)! + 1); queue.push(o); }
  }
  return depth;
}

/** The segments a device carries: its own legs (a switch's) and those of its `up` links (as the tree draws it). */
export function carriedBy(te: TeModel, device: string): string[] {
  const n = te.nodes.find((x) => x.name === device);
  const out: string[] = [];
  if (n?.kind === "switch") for (const l of n.nets) if (!out.includes(l.segment)) out.push(l.segment);
  for (const l of te.links) if (l.state === "up" && (l.a.device === device || l.b.device === device)) for (const s of segmentsOf(l)) if (!out.includes(s)) out.push(s);
  return out;
}

/** `subtreeOf`, or null when `root` is not (or no longer) a node of the document. */
export function subtreeOrNull(model: DocModel, root: string): Subtree | null {
  return model.te.nodes.some((n) => n.name === root) ? subtreeOf(model, root) : null;
}

export function subtreeOf(model: DocModel, root: string): Subtree {
  const te = model.te;
  const rootNode = te.nodes.find((n) => n.name === root);
  if (!rootNode) throw new Error(`no node group named '${root}'`);
  const depth = deviceDepths(te);
  const deviceNames = new Set(te.nodes.filter(isDevice).map((n) => n.name));
  const below = (d: string): number => depth.get(d) ?? Number.POSITIVE_INFINITY;
  const devices: string[] = [];
  let uplink: TeLink | null = null;
  if (isDevice(rootNode)) {
    const seen = new Set<string>([root]);
    const queue = [root];
    while (queue.length) {
      const d = queue.shift()!;
      devices.push(d);
      for (const l of te.links) {
        const other = l.a.device === d ? l.b.device : l.b.device === d ? l.a.device : null;
        if (!other || !deviceNames.has(other) || seen.has(other)) continue;
        if (d === root && below(other) < below(root)) { uplink = uplink ?? l; continue; }
        if (below(other) > below(d) || (below(other) === below(d) && l.state !== "up")) { seen.add(other); queue.push(other); }
      }
    }
  }
  const inTree = new Set(devices);
  // A segment belongs to the subtree when its deepest carrier does (ties go to the subtree).
  const segments: string[] = [];
  const carrier = new Map<string, { device: string; depth: number }>();
  for (const d of deviceNames) for (const s of carriedBy(te, d)) {
    const cur = carrier.get(s);
    const dd = below(d);
    if (!cur || dd > cur.depth || (dd === cur.depth && inTree.has(d) && !inTree.has(cur.device))) carrier.set(s, { device: d, depth: dd });
  }
  for (const [s, c] of carrier) if (inTree.has(c.device)) segments.push(s);
  const hosts: string[] = [];
  for (const n of te.nodes) {
    if (isDevice(n)) continue;
    const wired = te.links.some((l) => (l.a.device === n.name && inTree.has(l.b.device)) || (l.b.device === n.name && inTree.has(l.a.device)));
    if (n.name === root || wired || n.nets.some((l) => segments.includes(l.segment))) hosts.push(n.name);
  }
  const members = new Set([...devices, ...hosts]);
  const links = te.links.filter((l) => l !== uplink && (members.has(l.a.device) || members.has(l.b.device)) && !(l.a.device === root && !members.has(l.b.device)) && !(l.b.device === root && !members.has(l.a.device))).map((l) => l.index);
  return { root, devices, segments, hosts, links, uplink };
}

/** The next free port name on a device (`P<n>` on a switch, `eth<n>` otherwise). */
export function freePort(te: TeModel, device: string): string {
  const n = te.nodes.find((x) => x.name === device);
  const used = new Set<string>();
  for (const l of te.links) {
    if (l.a.device === device) used.add(l.a.port);
    if (l.b.device === device) used.add(l.b.port);
  }
  const prefix = n?.kind === "switch" ? "P" : "eth";
  for (let i = 1; i < 10000; i++) if (!used.has(`${prefix}${i}`)) return `${prefix}${i}`;
  return `${prefix}x`;
}

export interface DuplicateResult {
  files: Files;
  /** Old name → new name for every copied device, host and segment. */
  names: Record<string, string>;
  root: string;
}

/** `{name}-2`, `{name}-3`, … the first not taken (§6.2: the copy is the second of its name). */
function copyName(base: string, taken: ReadonlySet<string>): string {
  for (let n = 2; n < 10000; n++) if (!taken.has(`${base}-${n}`)) return `${base}-${n}`;
  return `${base}-copy`;
}

function prefixOf(cidr: string): string | null {
  const m = /^(\d{1,3}\.\d{1,3})\.\d{1,3}\.\d{1,3}\/\d{1,2}$/.exec(cidr);
  return m ? m[1]! : null;
}

/** Copy `root`'s subtree, attached by a trunk to `attachTo` (a device) or parked when null. */
export function duplicateSubtree(doc: WorkingDoc, model: DocModel, root: string, attachTo: string | null): DuplicateResult {
  const te = model.te;
  const tree = subtreeOf(model, root);
  if (attachTo !== null) {
    const t = te.nodes.find((n) => n.name === attachTo);
    if (!t || !isDevice(t)) throw new Error(`'${attachTo}' is not a switch or a router; a subtree attaches to a device`);
    if (tree.devices.includes(attachTo)) throw new Error(`'${attachTo}' is inside the subtree being copied`);
  }
  const names: Record<string, string> = {};
  const takenNodes = new Set(te.nodes.map((n) => n.name));
  for (const n of [...tree.devices, ...tree.hosts]) {
    const nn = copyName(n, takenNodes);
    names[n] = nn;
    takenNodes.add(nn);
  }
  const takenSegs = new Set(te.segments.map((s) => s.name));
  const inPlay = te.segments.map((s) => s.cidr);
  const cidrs: Record<string, string> = {};
  for (const s of tree.segments) {
    const seg = te.segments.find((x) => x.name === s)!;
    const nn = copyName(s, takenSegs);
    names[s] = nn;
    takenSegs.add(nn);
    const prefix = prefixOf(seg.cidr);
    const cidr = prefix ? nextFreeSlash24(prefix, inPlay, { after: true }) : null;
    if (!cidr) throw new Error(`no free /24 left under ${prefix ?? seg.cidr} for a copy of ${s}`);
    cidrs[nn] = cidr;
    inPlay.push(cidr);
  }
  const seg = (s: string) => names[s] ?? s;

  let text = doc.files[doc.teFile];
  if (text === undefined) throw new Error(`the document has no '${doc.teFile}' to edit`);
  for (const s of tree.segments) text = addSegment(text, { name: names[s]!, cidr: cidrs[names[s]!]!, zoneType: te.segments.find((x) => x.name === s)!.zoneType });
  const takenIps = new Map<string, Set<string>>();
  const allocate = (segment: string, count: number): string[] => {
    const cidr = cidrs[segment] ?? te.segments.find((x) => x.name === segment)?.cidr;
    if (!cidr) return [];
    const taken = takenIps.get(segment) ?? takenAddresses(te, segment);
    const a = allocateHostAddresses(cidr, count, taken);
    if (!a.ok) throw new Error(a.error);
    for (const ip of a.addresses) taken.add(ip);
    takenIps.set(segment, taken);
    return a.addresses;
  };
  for (const name of [...tree.devices, ...tree.hosts]) {
    const n = te.nodes.find((x) => x.name === name)!;
    const nets = n.nets.map((l) => ({ segment: seg(l.segment), ip: l.ip && n.countValue === 1 && !isDevice(n) ? allocate(seg(l.segment), 1)[0] ?? null : null }));
    text = addNode(text, { name: names[name]!, kind: n.kind, role: n.role, image: n.image, count: n.count, nets, requires: n.requires, resources: n.resources });
    for (const m of n.members) if (m.role) text = setMemberRole(text, names[name]!, m.index, m.role);
  }
  for (const i of tree.links) {
    const l = te.links[i]!;
    text = addLink(text, { a: { device: names[l.a.device] ?? l.a.device, port: l.a.port }, b: { device: names[l.b.device] ?? l.b.device, port: l.b.port }, mode: l.mode, native: l.native ? seg(l.native) : null, vlans: l.vlans.map(seg), state: l.state === "up" ? null : l.state });
  }
  const newRoot = names[root]!;
  if (attachTo !== null) {
    const up = tree.uplink;
    const carried = tree.segments.map(seg);
    const native = up?.native ? seg(up.native) : null;
    const vlans = up ? up.vlans.map(seg).filter((s) => carried.includes(s) || !tree.segments.includes(s)) : carried;
    const rootPort = up ? (up.a.device === root ? up.a.port : up.b.port) : freePort(readDocTe(text, doc), newRoot);
    text = addLink(text, { a: { device: attachTo, port: freePort(readDocTe(text, doc), attachTo) }, b: { device: newRoot, port: rootPort }, mode: "trunk", native, vlans: vlans.length || native ? vlans : carried });
  }
  let files: Files = { ...doc.files, [doc.teFile]: text };
  // Traffic: the copied groups' bindings (group and instance) copy under the new names; taps never do.
  if (model.tfcPath && files[model.tfcPath] !== undefined && model.tfc) {
    for (const name of tree.hosts) {
      const n = te.nodes.find((x) => x.name === name)!;
      const group = model.tfc.assignments.find((a) => a.by === "node" && a.target === name);
      if (group) {
        const next = readDoc({ ...doc, files });
        files = setPersona({ ...doc, files }, next, "node", names[name]!, { persona: group.persona, intensity: group.intensity, schedule: group.schedule });
      }
      for (let i = 1; i <= n.countValue; i++) {
        const a = model.tfc.assignments.find((x) => x.by === "node" && x.target === instanceId(name, i));
        if (!a) continue;
        const next = readDoc({ ...doc, files });
        files = setPersona({ ...doc, files }, next, "node", instanceId(names[name]!, i), { persona: a.persona, intensity: a.intensity, schedule: a.schedule });
      }
    }
  }
  return { files, names, root: newRoot };
}

function readDocTe(text: string, doc: WorkingDoc): TeModel {
  return readDoc({ ...doc, files: { ...doc.files, [doc.teFile]: text } }).te;
}

/** One `links[]` entry between a device and a device or a host (§6.4). */
export function attachDevices(doc: WorkingDoc, model: DocModel, from: string, to: string): Files {
  const te = model.te;
  if (from === to) throw new Error("a link joins two different devices");
  const a = te.nodes.find((n) => n.name === from);
  const b = te.nodes.find((n) => n.name === to);
  if (!a || !b) throw new Error(`no node group named '${a ? to : from}'`);
  // The device end goes first; a host-to-host pair has no device to link through.
  const [dev, other] = isDevice(a) ? [a, b] : isDevice(b) ? [b, a] : [null, null];
  if (!dev || !other) throw new Error(`neither '${from}' nor '${to}' is a switch or a router; a link needs a device at one end`);
  if (te.links.some((l) => (l.a.device === dev.name && l.b.device === other.name) || (l.b.device === dev.name && l.a.device === other.name))) throw new Error(`'${dev.name}' and '${other.name}' are already linked`);
  if (isDevice(other)) {
    const depth = deviceDepths(te);
    const deeper = (depth.get(other.name) ?? Number.POSITIVE_INFINITY) >= (depth.get(dev.name) ?? Number.POSITIVE_INFINITY) ? other : dev;
    const vlans = carriedBy(te, deeper.name);
    if (!vlans.length) throw new Error(`'${deeper.name}' carries no segment yet; give it a link to a host or a segment first, then trunk it`);
    const text = addLink(doc.files[doc.teFile]!, { a: { device: dev.name, port: freePort(te, dev.name) }, b: { device: other.name, port: freePort(te, other.name) }, mode: "trunk", vlans });
    return { ...doc.files, [doc.teFile]: text };
  }
  if (other.countValue > 1) throw new Error(`'${other.name}' is a group of ${other.countValue}; a link names one device`);
  const segment = other.nets[0]?.segment;
  if (!segment) throw new Error(`'${other.name}' is on no segment; attach it to one first`);
  const text = addLink(doc.files[doc.teFile]!, { a: { device: dev.name, port: freePort(te, dev.name) }, b: { device: other.name, port: freePort(te, other.name) }, mode: "access", vlans: [segment] });
  return { ...doc.files, [doc.teFile]: text };
}
