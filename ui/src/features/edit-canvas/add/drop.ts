/**
 * What an Add-panel drop writes (`docs/design/canvas-add-panel.md` §4, §6): the card, the drop
 * popover's choices and the needs hint's peers, as ONE pure `(doc) => files` the command stack
 * records -- so a drop and everything chosen in its popover is one undo step. Every write is
 * today's twin-config vocabulary through the existing commands (`placeNodes`, `setPersona`,
 * `setNodeField`, `attach`, `insertSwitch`, `addTap`); no schema change. Pure -- no React.
 */
import { DEFAULT_LAN_PREFIX, gatewayAddress, lanSitePrefix, nextFreeLan, nextFreeSlash24 } from "../edit/allocate";
import type { Files } from "../edit/commands";
import { addSegment, addTap, attach, placeNodes, readDoc, setAttachmentAddress, setExternalGroup, setNodeField, setPersona, type DocModel, type WorkingDoc } from "../edit/document";
import { insertSwitch, newSegmentOnRouter } from "../edit/switches";
import { addNode } from "../edit/te";
import { L3_SWITCH_ROLE, type CardId, type Os } from "./catalog";

/** Where the drop landed (`Canvas.tsx` `placeTarget`, §7.1 drop rules). */
export type DropTarget =
  /** On a segment (an implied switch, or a real switch carrying one); `choices` when a switch carrying
   *  several was dropped on (the popover asks which, B2), `via` that switch. */
  | { segment: string; choices?: string[]; via?: string | null }
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
  /** N instances: a host group's `count:` (§4; 1 writes none); for a router or an L3 switch, N separate nodes. */
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
  /** L3 switch: the tier hint (it only seeds the prefix: `core`, `agg`; nothing else is written). */
  tier?: "core" | "aggregation" | null;
  /** Router / firewall. */
  routerType?: "router" | "firewall";
  placement?: "edge" | "internal";
  /** L3 switch, core tier (round 3 item 5d): uplink each to the internet, as a Router's edge placement does. */
  uplink?: boolean;
  /** The internet sink's image when an uplink makes the internet (the `linux-server` profile). */
  sinkImage?: string | null;
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
/** The two-octet prefix of `allocate.ts` `INTERNET_POOL`. */
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

/** The stem a switch's new LAN is named from (round 3 item 5a): its prefix without the last token
 *  (`rack1-tor` -> `rack1`, so `rack1-lan`), the prefix itself when it is one token (`sw` -> `sw-lan`). */
export function lanStemOf(prefix: string): string {
  const parts = prefix.split("-").filter(Boolean);
  return parts.length >= 2 ? parts.slice(0, -1).join("-") : prefix || "switch";
}

/** A new LAN segment in the twin's first site (the next free /24 after the highest in use), else 10.200. */
function newLan(doc: WorkingDoc, m: DocModel, base: string): { doc: WorkingDoc; segment: string } {
  const inPlay = m.te.segments.map((s) => s.cidr);
  // The first LAN's site: the internet (INTERNET_POOL) and every transit (TRANSIT_POOL) are not a site.
  const prefix = lanSitePrefix(m.te.segments.map((s) => s.cidr));
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
  if (spec.card === "router" || spec.card === "l3switch") {
    // A router or an L3 switch: N separate named nodes (`core-1`, `core-2`; B6), never one fanned-out
    // group -- each needs links of its own. An L3 switch is `kind: router`, `role: l3-switch`.
    const seg = "segment" in spec.target ? spec.target.segment : null;
    const role = spec.card === "l3switch" ? L3_SWITCH_ROLE : spec.routerType === "firewall" ? "firewall" : "gw";
    const r = placeNodes(cur, model, { name: spec.prefix, count: Math.max(1, spec.count), image: spec.image ?? "", role, kind: "router", segment: seg, numbered: true });
    cur = { ...cur, files: r.files };
    names.push(...r.names);
    segment = seg;
    // Dropped on another router: it stands beside it with no leg yet (drag between them for a transit).
    if (spec.card === "router" && spec.placement === "edge") {
      const inet = ensureInternet(cur, readDoc(cur), spec.image);
      cur = inet.doc;
      for (const name of r.names) cur = { ...cur, files: attach(cur, readDoc(cur), name, inet.segment) };
    }
    // A core L3 switch's uplink (round 3 item 5d): a leg on the internet, pinned as a gateway (.1, .2, ...)
    // so two cores never share the address the planner gives a lone router leg.
    if (spec.card === "l3switch" && spec.uplink) {
      const inet = ensureInternet(cur, readDoc(cur), spec.sinkImage ?? null);
      cur = inet.doc;
      for (const name of r.names) {
        const m = readDoc(cur);
        if (m.te.nodes.find((n) => n.name === name)?.nets.some((l) => l.segment === inet.segment)) continue;
        const ip = gatewayAddress(m.te, inet.segment);
        cur = { ...cur, files: attach(cur, m, name, inet.segment) };
        if (ip) cur = { ...cur, files: setAttachmentAddress(cur, readDoc(cur), name, inet.segment, ip) };
      }
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
      // Count N (B6): N switches, each on a LAN of its own (sw-1 on sw-lan, sw-2 on sw-lan-2).
      // Round 3: the LAN is named after the switch (`rack1-tor` -> `rack1-lan`, 5a); one switch takes the
      // bare prefix, the -N suffix only when there are several (5b).
      const many = Math.max(1, spec.count) > 1;
      for (let k = 0; k < Math.max(1, spec.count); k++) {
        const land = landing(cur, readDoc(cur), spec.target, lanStemOf(spec.prefix));
        cur = land.doc;
        segment ??= land.segment;
        const m = readDoc(cur);
        const taken = new Set(m.te.nodes.map((n) => n.name));
        let name = many ? `${spec.prefix}-1` : spec.prefix;
        for (let i = 2; taken.has(name); i++) name = `${spec.prefix}-${i}`;
        const text = cur.files[cur.teFile];
        if (text === undefined) throw new Error(`the document has no '${cur.teFile}' to edit`);
        cur = { ...cur, files: { ...cur.files, [cur.teFile]: addNode(text, { name, kind: "switch", role: "l2-switch", image: "", nets: [{ segment: land.segment, ip: null }] }) } };
        names.push(name);
      }
    }
    if (spec.switchType === "managed") for (const n of names) cur = { ...cur, files: setNodeField(cur, n, "stp", "rstp") };
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

/** The Internet users / services tools (design trafficgen §4.3, §5): an external group of the traffic
 *  profile (`emulated[]`, `external: true`) on the internet segment, made (with its sink) when the
 *  design has none. Its addresses are free addresses of that segment, as the gate run used (T7 open
 *  for a public-looking pool). Writes te only when the internet segment is new; the group is tfc. */
export function applyExternalDrop(doc: WorkingDoc, model: DocModel, ext: { name: string; role: string; count: number }, sinkImage: string | null): DropResult {
  const inet = ensureInternet(doc, model, sinkImage);
  const m = readDoc(inet.doc);
  const taken = new Set([...m.te.nodes.map((n) => n.name), ...(m.tfc?.emulated ?? []).map((e) => e.group)]);
  let name = ext.name;
  for (let n = 2; taken.has(name); n++) name = `${ext.name}-${n}`;
  const files = setExternalGroup(inet.doc, m, { group: name, segment: inet.segment, role: ext.role, count: ext.count });
  return { files, names: [name], segment: inet.segment };
}
