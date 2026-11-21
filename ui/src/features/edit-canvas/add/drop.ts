/**
 * What an Add-panel drop writes (`docs/design/canvas-add-panel.md` §4, §6): the card, the drop
 * popover's choices and the needs hint's peers, as ONE pure `(doc) => files` the command stack
 * records -- so a drop and everything chosen in its popover is one undo step. Every write is
 * today's twin-config vocabulary through the existing commands (`placeNodes`, `setPersona`,
 * `setNodeField`, `attach`, `insertSwitch`, `addTap`); no schema change. Pure -- no React.
 */
import { DEFAULT_LAN_PREFIX, lanSitePrefix, nextFreeLan, nextFreeSlash24 } from "../edit/allocate";
import type { Files } from "../edit/commands";
import { addSegment, addTap, attach, placeNodes, readDoc, setAttachmentAddress, setNodeField, setPersona, type DocModel, type WorkingDoc } from "../edit/document";
import { insertSwitch, newSegmentOnRouter } from "../edit/switches";
import { addNode } from "../edit/te";
import type { CardId, Os } from "./catalog";

/** Where the drop landed (`Canvas.tsx` `placeTarget`, §7.1 drop rules). */
export type DropTarget =
  /** On a segment (an implied switch, or a real switch carrying one). */
  | { segment: string }
  /** On a router: a new segment under it, the device on that. */
  | { router: string }
  /** On the empty pane: a host gets a new segment of its own, a router or switch stands alone. */
  | { pane: true };

export interface PeerDrop {
  card: CardId;
  os: Os | null;
  role: string;
  persona: string | null;
  count: number;
  image: string | null;
}

export interface DeviceDrop {
  card: CardId;
  os: Os | null;
  /** The node's `role:` (a persona id, or a bare role the library has no persona for). */
  role: string | null;
  /** The persona bound to the group through the traffic profile, or null. */
  persona: string | null;
  /** N instances, written as the group's `count:` (§4); 1 writes none. */
  count: number;
  /** The group's name: `{prefix}-{n}`, the first free n from 1 (`router-1`, `router-2`, …). */
  prefix: string;
  target: DropTarget;
  /** The image (the profile's, or Advanced's override); null writes `image: ""` (none resolved). */
  image: string | null;
  /** Advanced: a pinned address on the segment (a single instance only). */
  ip?: string | null;
  /** Advanced: the persona knobs. */
  intensity?: number | null;
  schedule?: string | null;
  /** Router / firewall. */
  routerType?: "router" | "firewall";
  placement?: "edge" | "internal";
  /** Switch: plain, or managed (`stp: rstp`; it trunks VLANs as any native switch does). */
  switchType?: "plain" | "managed";
  /** The needs hint's one-click peers, dropped on the same segment. */
  peers?: PeerDrop[];
}

export interface DropResult {
  files: Files;
  /** The node groups made (the device first, then its peers; a switch's name). */
  names: string[];
  /** The segment the device ended on, if any. */
  segment: string | null;
}

/** The segment an Internet tool makes and a router's edge placement uplinks to. */
export const INTERNET_SEGMENT = "internet";
export const INTERNET_PREFIX = "10.250";

/** The name of a segment a drop makes (2026-11-20, round 2): `<base>-lan` -- the first host's role,
 *  else the card (`file-server-lan`, `server-lan`, `switch-lan`) -- `-2`, `-3` when taken. */
export function dropSegmentName(m: Pick<DocModel, "te">, base: string): string {
  const taken = new Set(m.te.segments.map((s) => s.name));
  const stem = `${base}-lan`;
  let name = stem;
  for (let i = 2; taken.has(name); i++) name = `${stem}-${i}`;
  return name;
}

/** A new LAN segment in the twin's first site (the next free /24 after the highest in use), else 10.200. */
function newLan(doc: WorkingDoc, m: DocModel, base: string): { doc: WorkingDoc; segment: string } {
  const inPlay = m.te.segments.map((s) => s.cidr);
  // The first LAN's site: the internet segment and every transit (TRANSIT_POOL) are not a site.
  const prefix = lanSitePrefix(m.te.segments.filter((s) => s.name !== INTERNET_SEGMENT).map((s) => s.cidr));
  const cidr = nextFreeLan(prefix, inPlay);
  if (!cidr) throw new Error(`no free /24 under ${prefix ?? DEFAULT_LAN_PREFIX} for a new segment`);
  const segment = dropSegmentName(m, base);
  return { doc: { ...doc, files: addSegment(doc, { name: segment, cidr, zoneType: "vlan" }) }, segment };
}

/** The design's internet segment, made (with its sink) when it has none. */
export function ensureInternet(doc: WorkingDoc, m: DocModel, sinkImage: string | null): { doc: WorkingDoc; segment: string; sink: string | null } {
  if (m.te.segments.some((s) => s.name === INTERNET_SEGMENT)) return { doc, segment: INTERNET_SEGMENT, sink: null };
  const cidr = nextFreeSlash24(INTERNET_PREFIX, m.te.segments.map((s) => s.cidr));
  if (!cidr) throw new Error(`no free /24 under ${INTERNET_PREFIX} for the internet segment`);
  let cur: WorkingDoc = { ...doc, files: addSegment(doc, { name: INTERNET_SEGMENT, cidr, zoneType: "vlan" }) };
  const r = placeNodes(cur, readDoc(cur), { name: "internet-sink", count: 1, image: sinkImage ?? "", role: "internet-sink", kind: "host", segment: INTERNET_SEGMENT });
  cur = { ...cur, files: r.files };
  return { doc: cur, segment: INTERNET_SEGMENT, sink: r.names[0] ?? null };
}

/** The segment a host or switch drop lands on: the one dropped on, a new one under the router, or a new LAN. */
function landing(doc: WorkingDoc, m: DocModel, target: DropTarget, base: string): { doc: WorkingDoc; segment: string } {
  if ("segment" in target) return { doc, segment: target.segment };
  if ("router" in target) {
    const made = newSegmentOnRouter(doc, m, target.router, dropSegmentName(m, base));
    return { doc: { ...doc, files: made.files }, segment: made.segment };
  }
  return newLan(doc, m, base);
}

function placeHostGroup(doc: WorkingDoc, req: { prefix: string; count: number; image: string | null; role: string | null; persona: string | null; segment: string; ip?: string | null; intensity?: number | null; schedule?: string | null }): { doc: WorkingDoc; name: string } {
  const m = readDoc(doc);
  const r = placeNodes(doc, m, { name: req.prefix, count: 1, image: req.image ?? "", role: req.role, kind: "host", segment: req.segment, fanOut: req.count > 1 ? req.count : undefined, numbered: true });
  let cur: WorkingDoc = { ...doc, files: r.files };
  const name = r.names[0]!;
  if (req.ip && req.count <= 1) cur = { ...cur, files: setAttachmentAddress(cur, readDoc(cur), name, req.segment, req.ip) };
  if (req.persona) {
    const patch: { persona: string; intensity?: number | null; schedule?: string | null } = { persona: req.persona };
    if (req.intensity !== undefined && req.intensity !== null) patch.intensity = req.intensity;
    if (req.schedule) patch.schedule = req.schedule;
    cur = { ...cur, files: setPersona(cur, readDoc(cur), "node", name, patch) };
  }
  return { doc: cur, name };
}

/** Apply a device drop: the device, its popover choices and its peers, as one set of files. */
export function applyDeviceDrop(doc: WorkingDoc, model: DocModel, spec: DeviceDrop): DropResult {
  const names: string[] = [];
  let cur = doc;
  let segment: string | null = null;
  if (spec.card === "router") {
    const seg = "segment" in spec.target ? spec.target.segment : null;
    const r = placeNodes(cur, model, { name: spec.prefix, count: 1, image: spec.image ?? "", role: spec.routerType === "firewall" ? "firewall" : "gw", kind: "router", segment: seg, numbered: true });
    cur = { ...cur, files: r.files };
    const name = r.names[0]!;
    names.push(name);
    segment = seg;
    // Dropped on another router: it stands beside it with no leg yet (drag between them for a transit).
    if (spec.placement === "edge") {
      const inet = ensureInternet(cur, readDoc(cur), spec.image);
      cur = inet.doc;
      cur = { ...cur, files: attach(cur, readDoc(cur), name, inet.segment) };
    }
    return { files: cur.files, names, segment };
  }
  if (spec.card === "switch") {
    if ("segment" in spec.target) {
      const out = insertSwitch(cur, model, spec.target.segment);
      cur = { ...cur, files: out.files };
      names.push(out.name);
      segment = spec.target.segment;
    } else {
      const land = landing(cur, model, spec.target, spec.card);
      cur = land.doc;
      segment = land.segment;
      const m = readDoc(cur);
      const taken = new Set(m.te.nodes.map((n) => n.name));
      let name = `${spec.prefix}-1`;
      for (let i = 2; taken.has(name); i++) name = `${spec.prefix}-${i}`;
      const text = cur.files[cur.teFile];
      if (text === undefined) throw new Error(`the document has no '${cur.teFile}' to edit`);
      cur = { ...cur, files: { ...cur.files, [cur.teFile]: addNode(text, { name, kind: "switch", role: "l2-switch", image: "", nets: [{ segment, ip: null }] }) } };
      names.push(name);
    }
    if (spec.switchType === "managed") cur = { ...cur, files: setNodeField(cur, names[0]!, "stp", "rstp") };
    return { files: cur.files, names, segment };
  }
  // A host: server, workstation or OT device.
  const land = landing(cur, model, spec.target, spec.role ?? spec.card);
  cur = land.doc;
  segment = land.segment;
  const placed = placeHostGroup(cur, { prefix: spec.prefix, count: spec.count, image: spec.image, role: spec.role, persona: spec.persona, segment, ip: spec.ip ?? null, intensity: spec.intensity ?? null, schedule: spec.schedule ?? null });
  cur = placed.doc;
  names.push(placed.name);
  for (const p of spec.peers ?? []) {
    const peer = placeHostGroup(cur, { prefix: p.role, count: p.count, image: p.image, role: p.role, persona: p.persona, segment });
    cur = peer.doc;
    names.push(peer.name);
  }
  return { files: cur.files, names, segment };
}

/** The Internet tool: the internet segment and its sink, the router dropped on uplinked to it. */
export function applyInternetDrop(doc: WorkingDoc, model: DocModel, target: DropTarget, sinkImage: string | null): DropResult {
  const inet = ensureInternet(doc, model, sinkImage);
  let cur = inet.doc;
  if ("router" in target) {
    const r = readDoc(cur).te.nodes.find((n) => n.name === target.router);
    if (r && !r.nets.some((l) => l.segment === inet.segment)) cur = { ...cur, files: attach(cur, readDoc(cur), target.router, inet.segment) };
  }
  return { files: cur.files, names: inet.sink ? [inet.sink] : [], segment: inet.segment };
}

/** The Sensor / tap tool: a segment tap on the segment dropped on (or a switch's), with its sensors. */
export function applyTapDrop(doc: WorkingDoc, model: DocModel, segment: string, sensors: string[]): DropResult {
  if (!sensors.length) throw new Error("name a sensor: the tap tier refuses a tap with none");
  return { files: addTap(doc, model, { scope: "segment", target: segment, sensors, source: "authored" }), names: [], segment };
}
