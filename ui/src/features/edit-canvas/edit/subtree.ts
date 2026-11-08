/**
 * Subtree select, duplicate and attach (`docs/design/topology-layers.md` §6,
 * track 5d; the rule tightened by R4-3, 2026-11-08). A device's subtree is the
 * device and the deeper devices its `up` links reach, the segments they carry
 * (their deepest carrier is in the set), the host groups ON those segments,
 * their member overrides and the links among that set only. A down or blocked
 * link never extends it; a host whose segment is outside it is never copied; a
 * copied link that would point outside it is dropped. Duplicate
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
  /** The host groups on those segments (a host wired to a subtree device but on no such segment is not). */
  hosts: string[];
  /** The `links[]` indices with both ends in the subtree (the uplink excluded). */
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
    // Only an `up` link extends the subtree (R4-3): a down or blocked one deploys nothing, so
    // whatever hangs off it is not below this device.
    for (const l of te.links) {
      const other = l.a.device === root ? l.b.device : l.b.device === root ? l.a.device : null;
      if (other && deviceNames.has(other) && below(other) < below(root) && (!uplink || (uplink.state !== "up" && l.state === "up"))) uplink = l;
    }
    const seen = new Set<string>([root]);
    const queue = [root];
    while (queue.length) {
      const d = queue.shift()!;
      devices.push(d);
      for (const l of te.links) {
        if (l.state !== "up") continue;
        const other = l.a.device === d ? l.b.device : l.b.device === d ? l.a.device : null;
        if (!other || !deviceNames.has(other) || seen.has(other)) continue;
        const deeper = below(other) > below(d) || (below(d) === Number.POSITIVE_INFINITY && below(other) === Number.POSITIVE_INFINITY);
        if (deeper) {
          seen.add(other);
          queue.push(other);
        }
      }
    }
  }
  const inTree = new Set(devices);
  // A segment belongs to the subtree when its deepest carrier does (ties go to the subtree). A
  // carrier is a device whose up links (or own legs) carry the segment; a router's own legs count,
  // so the segments it serves directly stay with it rather than with any copy of a switch below.
  const carried = (d: string): string[] => {
    const own = te.nodes.find((n) => n.name === d)?.kind === "router" ? (te.nodes.find((n) => n.name === d)?.nets.map((l) => l.segment) ?? []) : [];
    return [...new Set([...carriedBy(te, d), ...own])];
  };
  const segments: string[] = [];
  const carrier = new Map<string, { device: string; depth: number }>();
  for (const d of deviceNames) for (const s of carried(d)) {
    const cur = carrier.get(s);
    const dd = below(d);
    if (!cur || dd > cur.depth || (dd === cur.depth && inTree.has(d) && !inTree.has(cur.device))) carrier.set(s, { device: d, depth: dd });
  }
  for (const [s, c] of carrier) if (inTree.has(c.device)) segments.push(s);
  // The hosts ON those segments, and only those: a host wired to a subtree device but on a segment
  // outside the set (the file server behind a down port) is never copied.
  const hosts: string[] = [];
  for (const n of te.nodes) {
    if (isDevice(n)) continue;
    if (n.name === root || n.nets.some((l) => segments.includes(l.segment))) hosts.push(n.name);
  }
  // The links among that set only: both ends in it, the uplink aside.
  const members = new Set([...devices, ...hosts]);
  const links = te.links.filter((l) => l !== uplink && members.has(l.a.device) && members.has(l.b.device)).map((l) => l.index);
  return { root, devices, segments, hosts, links, uplink };
}

/** What a subtree holds, as the panel and the carried copy say it: `1 switch · 2 segments · 4 hosts`. */
export function subtreeCount(model: DocModel, t: Subtree): string {
  const te = model.te;
  const switches = t.devices.filter((d) => te.nodes.find((n) => n.name === d)?.kind === "switch").length;
  const routers = t.devices.length - switches;
  const instances = t.hosts.reduce((sum, h) => sum + (te.nodes.find((n) => n.name === h)?.countValue ?? 1), 0);
  return [routers ? `${routers} router${routers === 1 ? "" : "s"}` : "", switches ? `${switches} switch${switches === 1 ? "" : "es"}` : "", `${t.segments.length} segment${t.segments.length === 1 ? "" : "s"}`, `${instances} host${instances === 1 ? "" : "s"}`].filter(Boolean).join(" · ");
}

/** The subtree a marquee picked (§6.1, "marquee works too"): when the picked devices are a switch
 *  and devices below it, that switch's subtree; otherwise (no device, a router at the top, devices
 *  from two branches) null and the marquee stays a plain selection. */
export function marqueeSubtree(model: DocModel, picked: readonly string[]): string | null {
  const te = model.te;
  const devices = picked.filter((name) => te.nodes.some((n) => n.name === name && isDevice(n)));
  if (!devices.length) return null;
  const depth = deviceDepths(te);
  const at = (d: string) => depth.get(d) ?? Number.POSITIVE_INFINITY;
  const root = [...devices].sort((a, b) => at(a) - at(b) || (a < b ? -1 : 1))[0]!;
  if (te.nodes.find((n) => n.name === root)?.kind !== "switch") return null;
  const t = subtreeOf(model, root);
  return devices.every((d) => t.devices.includes(d)) ? root : null;
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
  /** Each copied segment's (new name's) CIDR. */
  cidrs: Record<string, string>;
}

type Point = { x: number; y: number };

/** Where a copy dropped on the canvas stands: its root at `at`, and every copied device and
 *  segment the original has a position for at that position shifted by the same offset, so
 *  the copy keeps the original's shape instead of its pills going wherever the tree puts
 *  them (2026-11-08). `positions` is what is on screen, by pin key (`host:<name>`,
 *  `segment:<cidr>`); an item without one (folded away) is left to the tree. */
export function copyPositions(model: DocModel, root: string, result: Pick<DuplicateResult, "names" | "root" | "cidrs">, positions: Readonly<Record<string, Point>>, at: Point): Record<string, Point> {
  const out: Record<string, Point> = { [`host:${result.root}`]: { x: Math.round(at.x), y: Math.round(at.y) } };
  const from = positions[`host:${root}`];
  if (!from) return out;
  const dx = at.x - from.x;
  const dy = at.y - from.y;
  const shift = (p: Point): Point => ({ x: Math.round(p.x + dx), y: Math.round(p.y + dy) });
  const t = subtreeOf(model, root);
  for (const d of t.devices) {
    const p = positions[`host:${d}`];
    const nn = result.names[d];
    if (p && nn && d !== root) out[`host:${nn}`] = shift(p);
  }
  for (const s of t.segments) {
    const cidr = model.te.segments.find((x) => x.name === s)?.cidr;
    const p = cidr ? positions[`segment:${cidr}`] : undefined;
    const nn = result.names[s];
    const ncidr = nn ? result.cidrs[nn] : undefined;
    if (p && ncidr) out[`segment:${ncidr}`] = shift(p);
  }
  return out;
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

export interface DuplicateOptions {
  /** Re-allocate every copied segment under this two-octet prefix (`10.180`): the copy was dropped
   *  under another site (§6.3). Absent: each copy stays in its original's prefix. */
  prefix?: string | null;
}

/** The two-octet prefixes the subtree's segments sit in. */
export function subtreePrefixes(model: DocModel, root: string): string[] {
  const t = subtreeOf(model, root);
  const out: string[] = [];
  for (const s of t.segments) {
    const p = prefixOf(model.te.segments.find((x) => x.name === s)?.cidr ?? "");
    if (p && !out.includes(p)) out.push(p);
  }
  return out;
}

/** Copy `root`'s subtree, attached by a trunk to `attachTo` (a device) or parked when null. */
export function duplicateSubtree(doc: WorkingDoc, model: DocModel, root: string, attachTo: string | null, options: DuplicateOptions = {}): DuplicateResult {
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
    if (options.prefix && !/^\d{1,3}\.\d{1,3}$/.test(options.prefix)) throw new Error(`'${options.prefix}' is not a two-octet site prefix`);
    const prefix = options.prefix || prefixOf(seg.cidr);
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
  // A PARKED copy (R3-2) keeps what its uplink carried as ports of its own on the copied segments,
  // so the attach tool can trunk exactly those later (the uplink itself is never copied).
  const upCarried = attachTo === null && tree.uplink ? [...(tree.uplink.native ? [tree.uplink.native] : []), ...tree.uplink.vlans].filter((s, i, xs) => tree.segments.includes(s) && xs.indexOf(s) === i) : [];
  for (const name of [...tree.devices, ...tree.hosts]) {
    const n = te.nodes.find((x) => x.name === name)!;
    // A leg on a segment outside the set is not copied: the copy never joins an original segment.
    const nets = n.nets.filter((l) => tree.segments.includes(l.segment)).map((l) => ({ segment: seg(l.segment), ip: l.ip && n.countValue === 1 && !isDevice(n) ? allocate(seg(l.segment), 1)[0] ?? null : null }));
    if (name === root) for (const s of upCarried) if (!nets.some((l) => l.segment === seg(s))) nets.push({ segment: seg(s), ip: null });
    text = addNode(text, { name: names[name]!, kind: n.kind, role: n.role, image: n.image, count: n.count, nets, requires: n.requires, resources: n.resources });
    for (const m of n.members) if (m.role) text = setMemberRole(text, names[name]!, m.index, m.role);
  }
  for (const i of tree.links) {
    const l = te.links[i]!;
    // A copied link carries only the copied segments; one that would point outside the set is dropped.
    const vlans = l.vlans.filter((v) => tree.segments.includes(v)).map(seg);
    const native = l.native && tree.segments.includes(l.native) ? seg(l.native) : null;
    if (!vlans.length && !native) continue;
    text = addLink(text, { a: { device: names[l.a.device]!, port: l.a.port }, b: { device: names[l.b.device]!, port: l.b.port }, mode: l.mode, native, vlans, state: l.state === "up" ? null : l.state });
  }
  const newRoot = names[root]!;
  if (attachTo !== null) {
    const up = tree.uplink;
    const carried = tree.segments.map(seg);
    // The new trunk carries the copied segments only, never an original one (that would hang the
    // original segment under the copy).
    const native = up?.native && tree.segments.includes(up.native) ? seg(up.native) : null;
    const vlans = up ? up.vlans.filter((s) => tree.segments.includes(s)).map(seg) : carried;
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
  return { files, names, root: newRoot, cidrs };
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

/**
 * The switches no router reaches (R3-2): a parked duplicate, or a switch just placed. Reached
 * means a chain of up links from a router, or a segment the switch has a port on (or a link of
 * its carries) that a router has a leg on. A twin with no router parks nothing (there is nothing
 * to attach to). While any switch is parked the page holds the document locally: no API
 * validate, no working-copy sync, every exit blocked with the reason.
 */
export function parkedDevices(te: TeModel): string[] {
  const routers = te.nodes.filter((n) => n.kind === "router");
  if (!routers.length) return [];
  const switches = te.nodes.filter((n) => n.kind === "switch");
  const reached = new Set(routers.map((r) => r.name));
  const routed = new Set(routers.flatMap((r) => r.nets.map((l) => l.segment)));
  const segsOf = (name: string): string[] => {
    const own = te.nodes.find((n) => n.name === name)?.nets.map((l) => l.segment) ?? [];
    // A down or blocked link deploys nothing (topology-layers §2.5): it carries no segment here either.
    const carried = te.links.filter((l) => l.state === "up" && (l.a.device === name || l.b.device === name)).flatMap((l) => [...(l.native ? [l.native] : []), ...l.vlans]);
    return [...own, ...carried];
  };
  for (let changed = true; changed; ) {
    changed = false;
    for (const s of switches) {
      if (reached.has(s.name)) continue;
      const linked = te.links.some((l) => l.state === "up" && ((l.a.device === s.name && reached.has(l.b.device)) || (l.b.device === s.name && reached.has(l.a.device))));
      if (linked || segsOf(s.name).some((seg) => routed.has(seg))) {
        reached.add(s.name);
        changed = true;
      }
    }
  }
  return switches.filter((s) => !reached.has(s.name)).map((s) => s.name);
}
