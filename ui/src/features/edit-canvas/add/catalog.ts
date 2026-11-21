/**
 * The Add panel's vocabulary (`docs/design/canvas-add-panel.md` §3, §4): the six device cards, the
 * role/profile groups read from the persona library (category, label, blurb -- D1, with the §4.2
 * fallback), the image a card + OS resolves to (D2), the one search over cards, roles and tools,
 * the "needs" hint (a role's `requires` with no holder on the canvas), the design-health line, and
 * the recent / favourite items this browser keeps. Nothing here names a persona the API did not
 * list: every role is read from `GET /api/personas`. Pure -- no React (storage reads guarded).
 */
import type { ImageProfile, Persona, Template } from "../../../api/types";
import { inTransitPool } from "../edit/allocate";
import { danglingLinks } from "../edit/document";
import { carriedBy, parkedDevices } from "../edit/subtree";
import type { TeModel } from "../edit/te";
import { L3_SWITCH_ROLE } from "../model/roles";

export type CardId = "router" | "l3switch" | "switch" | "server" | "workstation" | "ot";
export type Os = "linux" | "windows";
export type ToolId = "internet" | "tap";

export interface DeviceCard {
  id: CardId;
  label: string;
  /** The one line of explanation the card shows. */
  line: string;
  /** The OS choices, or null when the card has none (router, L3 switch, switch, OT). */
  oses: Os[] | null;
  /** The persona categories its role picker lists (traffic generators only under Advanced). */
  categories: string[];
  /** The name prefix a drop starts from when no role names one. */
  prefix: string;
}

export const DEVICE_CARDS: readonly DeviceCard[] = [
  { id: "router", label: "Router / firewall", line: "joins segments; an edge one uplinks to the internet", oses: null, categories: [], prefix: "router" },
  // An L3 switch is written as `kind: router`, `role: l3-switch` on the `l3-switch` image profile
  // (VyOS): the role, not the kind, marks it, so the planner and validator treat it as a router.
  { id: "l3switch", label: "L3 switch", line: "switches a LAN and routes between VLANs; core or aggregation", oses: null, categories: [], prefix: "l3sw" },
  { id: "switch", label: "Switch", line: "a LAN: hosts dropped on it join its segment", oses: null, categories: [], prefix: "sw" },
  { id: "server", label: "Server", line: "a Linux or Windows server with a role: file, mail, web, DC…", oses: ["linux", "windows"], categories: ["identity", "business", "network", "other"], prefix: "server" },
  { id: "workstation", label: "Workstation", line: "user machines with a profile: office, developer, executive…", oses: ["windows", "linux"], categories: ["endpoint"], prefix: "ws" },
  { id: "ot", label: "OT device", line: "PLC, HMI, historian or protocol gateway", oses: null, categories: ["ot"], prefix: "ot" },
];

export interface Tool {
  id: ToolId;
  label: string;
  line: string;
}

/** The Tools section (§6). Attacker is not offered: no attack-framework registry exists to say one is installed. */
export const TOOLS: readonly Tool[] = [
  { id: "internet", label: "Internet", line: "an internet segment with a sink a router can uplink to" },
  { id: "tap", label: "Sensor / tap", line: "drop on a segment or switch: mirror it to a sensor" },
];

export const CATEGORY_ORDER = ["identity", "business", "network", "endpoint", "ot", "other", "traffic"] as const;
export const CATEGORY_LABEL: Record<string, string> = {
  identity: "Identity & infrastructure",
  business: "Business apps",
  network: "Network services",
  endpoint: "Endpoints",
  ot: "OT/ICS",
  traffic: "Traffic generators",
  other: "Other",
};

const TRAFFIC_PREFIXES = ["loadgen-", "xseg-"];

/** §4.2: the file's category, else `traffic` for the generators, else `other` (the API applies the same rule). */
export function personaCategory(p: Pick<Persona, "id" | "category">): string {
  if (p.category && CATEGORY_LABEL[p.category]) return p.category;
  return TRAFFIC_PREFIXES.some((x) => p.id.startsWith(x)) ? "traffic" : "other";
}

export function personaLabel(p: Pick<Persona, "id" | "label">): string {
  return p.label?.trim() || p.id;
}

const PROTO_NAME: Record<string, string> = { smb: "SMB", nfs: "NFS", http: "HTTP", https: "HTTPS", dns: "DNS", ldap: "LDAP", kerberos: "Kerberos", smtp: "SMTP", imap: "IMAP", ssh: "SSH", rdp: "RDP", modbus: "Modbus", opcua: "OPC UA", mqtt: "MQTT" };

/** One plain sentence from `emits` when the file has no blurb: what it serves or talks to, and when. */
export function emitsSentence(p: Pick<Persona, "emits">): string {
  if (!p.emits.length) return "";
  const protos = [...new Set(p.emits.map((e) => PROTO_NAME[e.proto] ?? e.proto.toUpperCase()))].slice(0, 3).join("/");
  const peers = [...new Set(p.emits.map((e) => e.peer_role).filter((r) => r && r !== "?"))].slice(0, 2).join(" and ");
  const diurnal = p.emits.some((e) => e.cadence === "diurnal");
  const bursty = p.emits.some((e) => e.cadence.startsWith("burst"));
  const when = diurnal ? "business hours" : bursty ? "in bursts" : "around the clock";
  return `${protos}${peers ? ` with ${peers}` : ""}; ${when}.`;
}

export function personaBlurb(p: Pick<Persona, "blurb" | "emits">): string {
  return p.blurb?.trim() || emitsSentence(p);
}

/** The card a persona's role drops as: endpoints are workstations, OT roles OT devices, the rest servers. */
export function cardForPersona(p: Pick<Persona, "id" | "category">): CardId {
  const c = personaCategory(p);
  return c === "endpoint" ? "workstation" : c === "ot" ? "ot" : "server";
}

function osOf(p: Pick<Persona, "os_family">): Os {
  return p.os_family === "windows" ? "windows" : "linux";
}

export interface RoleGroup {
  category: string;
  label: string;
  personas: Persona[];
}

/** The roles a card offers for an OS, grouped by category in the §4.1 order; the traffic
 *  generators only when `traffic` (the popover's Advanced). OT has no OS choice: its roles list whole. */
export function rolesFor(card: CardId, os: Os | null, personas: readonly Persona[], traffic = false): RoleGroup[] {
  const def = DEVICE_CARDS.find((c) => c.id === card);
  if (!def || def.categories.length === 0) return [];
  const cats = traffic && card !== "ot" ? [...def.categories, "traffic"] : def.categories;
  const out: RoleGroup[] = [];
  for (const cat of CATEGORY_ORDER) {
    if (!cats.includes(cat)) continue;
    const list = personas.filter((p) => personaCategory(p) === cat && (os === null || card === "ot" || osOf(p) === os)).sort((a, b) => personaLabel(a).localeCompare(personaLabel(b)));
    if (list.length) out.push({ category: cat, label: CATEGORY_LABEL[cat]!, personas: list });
  }
  return out;
}

const PREFERRED: Record<string, string[]> = {
  "server:linux": ["file-server", "web-tier"],
  "server:windows": ["domain-controller"],
  "workstation:windows": ["corp-workstation"],
  "workstation:linux": ["power-user-dev", "generic-endpoint"],
  "ot:any": ["plc"],
};

/** The role a card starts with: a conventional one when installed, else the first listed. */
export function defaultRole(card: CardId, os: Os | null, personas: readonly Persona[]): string | null {
  const groups = rolesFor(card, os, personas);
  const all = groups.flatMap((g) => g.personas.map((p) => p.id));
  const pref = PREFERRED[`${card}:${card === "ot" ? "any" : os ?? "linux"}`] ?? [];
  return pref.find((id) => all.includes(id)) ?? all[0] ?? null;
}

/** The image profile a card + OS resolves to (§4.3); a switch takes none (it deploys imageless). OT roles are Linux servers. */
export function profileFor(card: CardId, os: Os | null): string | null {
  if (card === "switch") return null;
  if (card === "router") return "router";
  if (card === "l3switch") return L3_SWITCH_PROFILE;
  if (card === "ot") return "linux-server";
  return `${os ?? "linux"}-${card === "server" ? "server" : "client"}`;
}

/** The image profile (and the node `role:`) an L3 switch carries; the canvas recognises one by the role. */
export const L3_SWITCH_PROFILE = "l3-switch";
export { L3_SWITCH_ROLE };

/** Whether a node is an L3 switch: a router whose role says so (A2, 2026-11-21). */
export function isL3Switch(n: { kind: string; role: string | null } | null | undefined): boolean {
  return Boolean(n && n.kind === "router" && n.role === L3_SWITCH_ROLE);
}

export function imageFor(profiles: readonly ImageProfile[], profile: string | null): string | null {
  if (!profile) return null;
  return profiles.find((p) => p.name === profile)?.image ?? null;
}

// ---------------------------------------------------------------------------
// Search (§3): one box over cards, roles and tools
// ---------------------------------------------------------------------------
export type AddHit =
  | { kind: "device"; card: CardId; role: string | null; os: Os | null; label: string; detail: string; score: number }
  | { kind: "tool"; tool: ToolId; label: string; detail: string; score: number }
  | { kind: "template"; template: string; label: string; detail: string; score: number };

const CARD_WORDS: Record<CardId, string[]> = {
  router: ["router", "firewall", "gateway", "edge", "fw", "nat"],
  l3switch: ["l3", "layer", "3", "core", "aggregation", "agg", "multilayer", "distribution", "svi"],
  switch: ["switch", "lan", "vlan", "segment"],
  server: ["server"],
  workstation: ["workstation", "desktop", "laptop", "client", "pc", "user", "endpoint"],
  ot: ["ot", "ics", "scada", "industrial"],
};
const TOOL_WORDS: Record<ToolId, string[]> = { internet: ["internet", "wan", "uplink", "sink", "cloud"], tap: ["sensor", "tap", "mirror", "span", "capture"] };

function words(text: string): string[] {
  return text.toLowerCase().split(/[^a-z0-9]+/).filter(Boolean);
}

/** How well a query token matches a text: 3 an exact word, 2 a word prefix, 0 none. */
function tokenScore(token: string, text: string): number {
  let best = 0;
  for (const w of words(text)) {
    if (w === token) return 3;
    if (w.startsWith(token)) best = 2;
  }
  return best;
}

function score(tokens: string[], fields: Array<[string, number]>): number {
  let total = 0;
  for (const t of tokens) {
    let best = 0;
    for (const [text, weight] of fields) best = Math.max(best, tokenScore(t, text) * weight);
    if (best === 0) return 0; // every token must match somewhere
    total += best;
  }
  return total;
}

/** Search the panel: a role hit preselects its card, OS and role ("file" -> Server, file-server). The
 *  traffic generators are left out (they are Advanced-only). Best first. */
export function searchAdd(query: string, personas: readonly Persona[], templates: readonly Pick<Template, "id" | "label" | "description">[] = []): AddHit[] {
  const tokens = words(query);
  if (!tokens.length) return [];
  const hits: AddHit[] = [];
  for (const p of personas) {
    if (personaCategory(p) === "traffic") continue;
    const s = score(tokens, [[personaLabel(p), 3], [p.id, 2], [personaBlurb(p), 1], [CATEGORY_LABEL[personaCategory(p)] ?? "", 1]]);
    const card = cardForPersona(p);
    if (s) hits.push({ kind: "device", card, role: p.id, os: card === "ot" ? null : osOf(p), label: personaLabel(p), detail: `${DEVICE_CARDS.find((c) => c.id === card)!.label} · ${p.os_family || "any OS"}`, score: s + 1 });
  }
  for (const c of DEVICE_CARDS) {
    const s = score(tokens, [[c.label, 3], [CARD_WORDS[c.id].join(" "), 2], [c.line, 1]]);
    if (s) hits.push({ kind: "device", card: c.id, role: null, os: null, label: c.label, detail: c.line, score: s });
  }
  for (const t of TOOLS) {
    const s = score(tokens, [[t.label, 3], [TOOL_WORDS[t.id].join(" "), 2], [t.line, 1]]);
    if (s) hits.push({ kind: "tool", tool: t.id, label: t.label, detail: t.line, score: s });
  }
  for (const t of templates) {
    const s = score(tokens, [[t.label, 3], [t.id, 2], [t.description, 1], ["template", 1]]);
    if (s) hits.push({ kind: "template", template: t.id, label: t.label, detail: `template · ${t.description}`, score: s });
  }
  return hits.sort((a, b) => b.score - a.score || a.label.localeCompare(b.label));
}

// ---------------------------------------------------------------------------
// Needs (§4.1) and the design-health line (§3)
// ---------------------------------------------------------------------------
/** Every role some node (or a member override) on the canvas holds. */
export function rolesPresent(te: Pick<TeModel, "nodes">): Set<string> {
  const out = new Set<string>();
  for (const n of te.nodes) {
    if (n.role) out.add(n.role);
    for (const m of n.members) if (m.role) out.add(m.role);
  }
  return out;
}

/** The peer roles `persona` requires that nothing on the canvas (nor `extra`) holds. A role that
 *  requires itself (a softphone mesh) is met by a drop of two or more. */
export function missingPeers(persona: Pick<Persona, "id" | "requires"> | null | undefined, te: Pick<TeModel, "nodes">, extra: readonly string[] = [], count = 1): string[] {
  if (!persona) return [];
  const have = rolesPresent(te);
  for (const r of extra) have.add(r);
  return persona.requires.filter((r, i, all) => all.indexOf(r) === i && !have.has(r) && !(r === persona.id && count >= 2));
}

export interface PeerOffer {
  role: string;
  card: CardId;
  os: Os | null;
  /** The persona to bind, or null when the library has none for that role (it is written as a role only). */
  persona: string | null;
  count: number;
  /** "Add 5 workstations", "Add 1 Domain controller". */
  label: string;
}

/** What the needs hint's one click adds for a missing peer role: its own persona's card, five of an
 *  endpoint, one of anything else. */
export function peerOffer(role: string, personas: readonly Persona[]): PeerOffer {
  const p = personas.find((x) => x.id === role);
  if (!p) return { role, card: "server", os: "linux", persona: null, count: 1, label: `Add 1 ${role}` };
  const card = cardForPersona(p);
  const count = card === "workstation" ? 5 : 1;
  const noun = card === "workstation" ? "workstations" : personaLabel(p);
  return { role, card, os: card === "ot" ? null : osOf(p), persona: p.id, count, label: `Add ${count} ${noun}` };
}

/** The LANs with two or more gateways (A6, 2026-11-21): a segment outside the transit pool with a leg
 *  of two or more routers (an L3 switch is one) and a host or a switch on it. With no shared VIP (first-hop
 *  redundancy is not modelled: design D5), the servers route through one of them only. */
export function twoGatewayLans(te: Pick<TeModel, "nodes" | "links" | "segments">): Array<{ segment: string; routers: string[] }> {
  const out: Array<{ segment: string; routers: string[] }> = [];
  for (const s of te.segments) {
    if (inTransitPool(s.cidr)) continue;
    const routers = te.nodes.filter((n) => n.kind === "router" && n.nets.some((l) => l.segment === s.name)).map((n) => n.name);
    if (routers.length < 2) continue;
    const lan = te.nodes.some((n) => n.kind !== "router" && n.nets.some((l) => l.segment === s.name)) || te.nodes.some((n) => n.kind === "switch" && carriedBy(te as TeModel, n.name).includes(s.name));
    if (lan) out.push({ segment: s.name, routers });
  }
  return out;
}

/** The two-gateway hint's words: "rack1-srv has two gateways (agg-1, agg-2) and no shared VIP -- servers will use only one". */
export function twoGatewayText(g: { segment: string; routers: string[] }): string {
  const n = g.routers.length;
  const word = n === 2 ? "two" : n === 3 ? "three" : String(n);
  return `${g.segment} has ${word} gateways (${g.routers.join(", ")}) and no shared VIP — servers will use only one`;
}

/** The design-health line: hints only, never a Check ("2 roles missing peers · 1 segment with no router"). */
export function healthHints(te: TeModel, personas: readonly Persona[]): string[] {
  const out: string[] = [];
  const byId = new Map(personas.map((p) => [p.id, p]));
  const roles = [...rolesPresent(te)];
  const lacking = roles.filter((r) => missingPeers(byId.get(r), te, [], te.nodes.filter((n) => n.role === r).reduce((k, n) => k + n.countValue, 0)).length > 0);
  if (lacking.length) out.push(`${lacking.length} role${lacking.length === 1 ? "" : "s"} missing peers`);
  const routers = te.nodes.filter((n) => n.kind === "router");
  const routed = new Set<string>(routers.flatMap((r) => r.nets.map((l) => l.segment)));
  for (const l of te.links) {
    const ends = [l.a.device, l.b.device].map((d) => te.nodes.find((n) => n.name === d)?.kind);
    if (l.state === "up" && ends.includes("router")) for (const s of [...(l.native ? [l.native] : []), ...l.vlans]) routed.add(s);
  }
  if (routers.length) {
    const parked = new Set(parkedDevices(te));
    for (const sw of te.nodes.filter((n) => n.kind === "switch" && !parked.has(n.name))) for (const s of carriedBy(te, sw.name)) routed.add(s);
  }
  const bare = te.segments.filter((s) => !routed.has(s.name)).length;
  if (bare) out.push(`${bare} segment${bare === 1 ? "" : "s"} with no router`);
  // A6: a LAN with two gateways and no shared VIP.
  for (const g of twoGatewayLans(te)) out.push(twoGatewayText(g));
  // B1: a link naming a node or a segment the twin no longer has (the hint's fix removes them).
  const dangling = danglingLinks(te).length;
  if (dangling) out.push(`${dangling} dangling link${dangling === 1 ? "" : "s"}`);
  return out;
}

// ---------------------------------------------------------------------------
// Recent and favourites (§3), kept in this browser
// ---------------------------------------------------------------------------
/** A panel item as recent / favourites keep it. */
export type AddRef = { kind: "device"; card: CardId; role: string | null; os: Os | null; label: string } | { kind: "tool"; tool: ToolId; label: string } | { kind: "template"; template: string; label: string };

export const RECENT_KEY = "axr-add-recent";
export const FAVOURITES_KEY = "axr-add-favourites";
export const RECENT_MAX = 6;

export function refKey(r: AddRef): string {
  return r.kind === "device" ? `device:${r.card}:${r.role ?? ""}:${r.os ?? ""}` : r.kind === "tool" ? `tool:${r.tool}` : `template:${r.template}`;
}

export function readRefs(key: string): AddRef[] {
  try {
    const raw = localStorage.getItem(key);
    const v = raw ? (JSON.parse(raw) as unknown) : [];
    return Array.isArray(v) ? v.filter((x): x is AddRef => Boolean(x) && typeof x === "object" && typeof (x as AddRef).label === "string" && ["device", "tool", "template"].includes((x as AddRef).kind)) : [];
  } catch {
    return [];
  }
}

export function writeRefs(key: string, refs: readonly AddRef[]): void {
  try {
    localStorage.setItem(key, JSON.stringify(refs));
  } catch {
    // no storage: recent and favourites live for the page only
  }
}

/** `ref` moved to the front of the recent list, de-duplicated, capped at six. */
export function pushRecent(list: readonly AddRef[], ref: AddRef): AddRef[] {
  return [ref, ...list.filter((r) => refKey(r) !== refKey(ref))].slice(0, RECENT_MAX);
}

export function toggleFavourite(list: readonly AddRef[], ref: AddRef): AddRef[] {
  return list.some((r) => refKey(r) === refKey(ref)) ? list.filter((r) => refKey(r) !== refKey(ref)) : [...list, ref];
}
