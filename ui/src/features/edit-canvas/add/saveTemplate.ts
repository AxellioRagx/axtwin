/**
 * Save a selection as a template (`docs/design/canvas-add-panel.md` §5.3): what the dialog proposes
 * from the selection -- each host group's count as an int setting, the segments' CIDRs as
 * `subnet: auto`, the common name stem as `prefix`, a clean name for each discovered one
 * (`web-tier-10-74-20-1` -> `web-tier`) -- and the template it builds. Kept: structure, roles,
 * images (the OS), conduits inside the selection, the persona bindings the library has. Stripped:
 * observed addresses (pinned IPs, the CIDRs), provenance (`source`, `confidence`, `vantage`,
 * observations, evidence and every key the canvas does not model), taps (bound to sensors outside
 * any selection). A leg or link leaving the selection becomes a port. Pure.
 *
 * What hangs under the selection (2026-11-20, round 2): by default a selection of devices takes the
 * segments its routers and switches carry, and the host groups on them, when nothing else that is not
 * a host -- no unselected router, no unselected switch -- is on that segment (`under`). Unchecked in
 * the dialog, it is the bare selection, and the switch's segment and its hosts become a port.
 */
import type { Persona, Template, TemplateParam } from "../../../api/types";
import type { DocModel } from "../edit/document";
import { carriedBy, subtreeOf } from "../edit/subtree";
import type { TeModel, TeNode } from "../edit/te";
import type { Selection } from "../SidePanel";

type Point = { x: number; y: number };

export interface NameChoice {
  kind: "node" | "segment";
  /** The name in the design. */
  from: string;
  /** The suggested name, without the prefix (editable). */
  to: string;
}

export interface CountChoice {
  node: string;
  param: string;
  label: string;
  value: number;
  /** Becomes a setting (else the count is written as a number). */
  on: boolean;
}

export interface Proposal {
  label: string;
  description: string;
  icon: string;
  /** The default of the `prefix` setting: the names' common stem, else `tpl`. */
  stem: string;
  names: NameChoice[];
  counts: CountChoice[];
  /** Legs and links leaving the selection, as ports (by design name). */
  ports: Array<{ name: string; device: string; reason: string }>;
  stripped: { provenance: number; addresses: number; taps: number; bindings: string[]; conduits: number };
  /** Selected hosts with no segment inside the selection: left out of the template. */
  leftOut: string[];
  /** What `under` added to the bare selection (segments, host groups); null when it was not asked for or added nothing. */
  under: { segments: number; hosts: number } | null;
  set: { devices: string[]; segments: string[]; hosts: string[] };
}

const isDevice = (n: TeNode) => n.kind === "router" || n.kind === "switch";

/** A discovered name without its address part: `web-tier-10-74-20-1` -> `web-tier`; a segment that is nothing but one -> `lan`. */
export function cleanName(name: string, kind: "node" | "segment"): string {
  const m = /^(.*?)-\d{1,3}(?:-\d{1,3}){2,3}(?:-[a-z0-9]+)?$/i.exec(name);
  let out = m ? m[1]! : name;
  if (kind === "segment" && (out === "seg" || out === "segment" || out === "")) out = "lan";
  return out.replace(/[^A-Za-z0-9._-]+/g, "-") || (kind === "segment" ? "lan" : "node");
}

function dedupe(list: NameChoice[]): NameChoice[] {
  const taken = new Set<string>();
  return list.map((c) => {
    let to = c.to;
    for (let i = 2; taken.has(to); i++) to = `${c.to}-${i}`;
    taken.add(to);
    return { ...c, to };
  });
}

/** The selection's devices, segments and host groups. A segment is inside when every node with a leg
 *  on it and every switch carrying it is selected. */
export function selectionSet(model: DocModel, sel: Selection, opts: { under?: boolean } = {}): { devices: string[]; segments: string[]; hosts: string[]; leftOut: string[] } {
  const te = model.te;
  if (sel.kind === "subtree") {
    const t = subtreeOf(model, sel.root);
    return { devices: t.devices, segments: t.segments, hosts: t.hosts, leftOut: [] };
  }
  let picked: string[] = [];
  if (sel.kind === "node") picked = sel.names.filter((n) => te.nodes.some((x) => x.name === n));
  else if (sel.kind === "segment") picked = te.nodes.filter((n) => !isDevice(n) && n.nets.length > 0 && n.nets.every((l) => sel.names.includes(l.segment))).map((n) => n.name);
  if (opts.under && sel.kind === "node") picked = [...picked, ...hostsUnder(te, picked)];
  const inSel = new Set(picked);
  const segments = te.segments
    .map((s) => s.name)
    .filter((s) => {
      const on = te.nodes.filter((n) => n.nets.some((l) => l.segment === s) || (n.kind === "switch" && carriedBy(te, n.name).includes(s)));
      return (on.length > 0 || (sel.kind === "segment" && sel.names.includes(s))) && on.every((n) => inSel.has(n.name));
    });
  const devices = picked.filter((n) => isDevice(te.nodes.find((x) => x.name === n)!));
  const hostsAll = picked.filter((n) => !devices.includes(n));
  const hosts = hostsAll.filter((n) => te.nodes.find((x) => x.name === n)!.nets.some((l) => segments.includes(l.segment)));
  return { devices, segments, hosts, leftOut: hostsAll.filter((n) => !hosts.includes(n)) };
}

/** The host groups under the selected devices: on a segment a selected router or switch carries,
 *  where every node on it that is not a host is selected (no unselected router or switch joins it). */
export function hostsUnder(te: TeModel, picked: readonly string[]): string[] {
  const inSel = new Set(picked);
  const carried = new Set<string>();
  for (const name of picked) {
    const n = te.nodes.find((x) => x.name === name);
    if (!n || !isDevice(n)) continue;
    for (const l of n.nets) carried.add(l.segment);
    if (n.kind === "switch") for (const s of carriedBy(te, n.name)) carried.add(s);
  }
  const out: string[] = [];
  for (const seg of carried) {
    const on = te.nodes.filter((n) => n.nets.some((l) => l.segment === seg) || (n.kind === "switch" && carriedBy(te, n.name).includes(seg)));
    if (on.some((n) => n.kind !== "host" && !inSel.has(n.name))) continue;
    for (const n of on) if (n.kind === "host" && !inSel.has(n.name) && !out.includes(n.name)) out.push(n.name);
  }
  return out;
}

function commonStem(names: readonly string[]): string | null {
  if (names.length < 2) return null;
  const parts = names.map((n) => n.split("-"));
  const first = parts[0]![0];
  return first && parts.every((p) => p.length > 1 && p[0] === first) ? first : null;
}

const slug = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
const paramName = (s: string) => {
  const p = s.replace(/[^A-Za-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
  return /^[A-Za-z_]/.test(p) ? p : `n_${p}`;
};

/** What the dialog proposes for `sel`: with what hangs under the selected devices (`under`, the
 *  default), or the bare selection. */
export function proposeTemplate(model: DocModel, sel: Selection, opts: { under?: boolean } = { under: true }): Proposal {
  const te = model.te;
  const set = selectionSet(model, sel, opts);
  const bare = opts.under ? selectionSet(model, sel) : null;
  const under = bare && (set.segments.length > bare.segments.length || set.hosts.length > bare.hosts.length) ? { segments: set.segments.length - bare.segments.length, hosts: set.hosts.length - bare.hosts.length } : null;
  const nodes = [...set.devices, ...set.hosts];
  let names = dedupe([...nodes.map((n) => ({ kind: "node" as const, from: n, to: cleanName(n, "node") })), ...set.segments.map((s) => ({ kind: "segment" as const, from: s, to: cleanName(s, "segment") }))]);
  const stem = commonStem(names.map((c) => c.to));
  if (stem) names = names.map((c) => ({ ...c, to: c.to.slice(stem.length + 1) }));
  const usedParams = new Set(["prefix", "subnet"]);
  const counts: CountChoice[] = set.hosts.map((h) => {
    const n = te.nodes.find((x) => x.name === h)!;
    const to = names.find((c) => c.kind === "node" && c.from === h)!.to;
    let param = paramName(to);
    for (let i = 2; usedParams.has(param); i++) param = `${paramName(to)}_${i}`;
    usedParams.add(param);
    return { node: h, param, label: `${to}: N`, value: Math.max(1, n.countValue), on: n.countValue > 1 };
  });
  const ports: Proposal["ports"] = [];
  const portNames = new Set<string>();
  const port = (base: string, device: string, reason: string) => {
    let name = base;
    for (let i = 2; portNames.has(name); i++) name = `${base}-${i}`;
    portNames.add(name);
    ports.push({ name, device, reason });
  };
  for (const n of nodes) {
    const node = te.nodes.find((x) => x.name === n)!;
    // A leg leaving the selection: the port is named after the segment it plugged into, which the template does not carry.
    for (const l of node.nets) if (!set.segments.includes(l.segment)) port(isDevice(node) ? l.segment : "uplink", n, `its leg on ${l.segment}`);
  }
  for (const l of te.links) {
    const a = nodes.includes(l.a.device);
    const b = nodes.includes(l.b.device);
    if (a !== b) port(`to-${cleanName(a ? l.b.device : l.a.device, "node")}`, a ? l.a.device : l.b.device, `its link to ${a ? l.b.device : l.a.device}`);
  }
  // What goes: provenance, observed addresses, taps, bindings the library lacks.
  let provenance = 0;
  const prov = (x: { source?: unknown; confidence?: unknown; vantage?: unknown }) => [x.source, x.confidence, x.vantage].filter((v) => v !== null && v !== undefined).length;
  for (const n of nodes) {
    const node = te.nodes.find((x) => x.name === n)!;
    provenance += prov(node) + node.otherKeys.filter((k) => k !== "stp").length;
  }
  for (const s of set.segments) provenance += prov(te.segments.find((x) => x.name === s)!);
  const insideConduits = te.conduits.filter((c) => set.segments.includes(c.from) && set.segments.includes(c.to));
  for (const c of insideConduits) provenance += prov(c);
  const addresses = set.segments.length + nodes.reduce((k, n) => k + te.nodes.find((x) => x.name === n)!.nets.filter((l) => l.ip).length, 0);
  const touches = (target: string) => set.segments.includes(target) || nodes.includes(target);
  const taps = (model.tap?.bindings ?? []).filter((b) => b.targets.some(touches) || (b.segment ? set.segments.includes(b.segment) : false)).length;
  const conduits = te.conduits.filter((c) => (set.segments.includes(c.from) || set.segments.includes(c.to)) && !insideConduits.includes(c)).length;
  const label = stem ? `${stem} section` : "Saved section";
  return { label, description: "", icon: "custom", stem: stem ?? "tpl", names, counts, ports, stripped: { provenance, addresses, taps, bindings: [], conduits }, leftOut: set.leftOut, under, set: { devices: set.devices, segments: set.segments, hosts: set.hosts } };
}

/** The persona bindings the template carries: node-level ones, and role-level ones turned into
 *  node-level for the selected groups holding that role; only personas the library lists (a cast's
 *  twin-local persona is not there on another twin). The rest are named as stripped. */
export function carriedBindings(model: DocModel, hosts: readonly string[], personas: readonly Persona[]): { carried: Array<{ node: string; persona: string; intensity: number | null; schedule: string | null }>; stripped: string[] } {
  const carried: Array<{ node: string; persona: string; intensity: number | null; schedule: string | null }> = [];
  const stripped: string[] = [];
  const known = new Set(personas.map((p) => p.id));
  for (const h of hosts) {
    const n = model.te.nodes.find((x) => x.name === h)!;
    const a = model.tfc?.assignments.find((x) => x.by === "node" && x.target === h) ?? model.tfc?.assignments.find((x) => x.by === "role" && x.target === n.role);
    if (!a) continue;
    if (known.has(a.persona)) carried.push({ node: h, persona: a.persona, intensity: a.intensity, schedule: a.schedule });
    else stripped.push(`${h}: ${a.persona}`);
  }
  return { carried, stripped };
}

export interface SaveChoices {
  label: string;
  description: string;
  icon: string;
  prefix: string;
  /** Edited names, by design name (without the prefix). */
  names: Record<string, string>;
  /** The count settings kept, by node. */
  counts: Record<string, { on: boolean; label: string }>;
}

/** The template the dialog saves. */
export function buildTemplate(model: DocModel, p: Proposal, choices: SaveChoices, personas: readonly Persona[], positions: Readonly<Record<string, Point>>, takenIds: readonly string[]): Template {
  const te: TeModel = model.te;
  const nameOf = (from: string) => `{prefix}-${choices.names[from] ?? p.names.find((c) => c.from === from)!.to}`;
  const params: TemplateParam[] = [];
  for (const c of p.counts) {
    const ch = choices.counts[c.node] ?? { on: c.on, label: c.label };
    if (ch.on) params.push({ name: c.param, label: ch.label, type: "int", default: c.value, min: 0, max: 1000 });
  }
  params.push({ name: "prefix", label: "Name prefix", type: "name", default: choices.prefix || p.stem });
  if (p.set.segments.length) params.push({ name: "subnet", label: p.set.segments.length > 1 ? "First subnet" : "Subnet", type: "cidr", default: "auto" });
  const segments = p.set.segments.map((s, i) => ({ name: nameOf(s), cidr: `10.0.${i}.0/24`, ...(te.segments.find((x) => x.name === s)!.zoneType ? { zone_type: te.segments.find((x) => x.name === s)!.zoneType } : {}) }));
  const nodes = [...p.set.devices, ...p.set.hosts].map((name) => {
    const n = te.nodes.find((x) => x.name === name)!;
    const legs = n.nets.filter((l) => p.set.segments.includes(l.segment)).map((l) => nameOf(l.segment));
    const c = p.counts.find((x) => x.node === name);
    const countOn = c && (choices.counts[name]?.on ?? c.on);
    const out: Record<string, unknown> = { name: nameOf(name) };
    if (n.kind !== "host") out["kind"] = n.kind;
    if (n.role) out["role"] = n.role;
    if (n.image) out["image"] = n.image;
    if (n.kind === "switch" && n.otherKeys.includes("stp")) out["stp"] = "rstp";
    if (countOn) out["count"] = `{${c!.param}}`;
    else if (n.countValue > 1) out["count"] = n.countValue;
    if (legs.length === 1) out["net"] = legs[0];
    else if (legs.length > 1) out["nets"] = legs.map((segment) => ({ segment }));
    if (n.requires.length) out["requires"] = [...n.requires];
    if (n.resources) out["resources"] = { ...n.resources };
    return out;
  });
  const inside = new Set([...p.set.devices, ...p.set.hosts]);
  const links = te.links
    .filter((l) => inside.has(l.a.device) && inside.has(l.b.device))
    .map((l) => {
      const vlans = l.vlans.filter((v) => p.set.segments.includes(v)).map(nameOf);
      const native = l.native && p.set.segments.includes(l.native) ? nameOf(l.native) : null;
      return { a: { device: nameOf(l.a.device), port: l.a.port }, b: { device: nameOf(l.b.device), port: l.b.port }, mode: l.mode, ...(native ? { native } : {}), vlans, ...(l.state !== "up" ? { state: l.state } : {}) };
    })
    .filter((l) => l.vlans.length || "native" in l);
  const conduits = te.conduits.filter((c) => p.set.segments.includes(c.from) && p.set.segments.includes(c.to)).map((c) => ({ from: nameOf(c.from), to: nameOf(c.to) }));
  const assignments = carriedBindings(model, p.set.hosts, personas).carried.map((a) => ({ node: nameOf(a.node), persona: a.persona, ...(a.intensity !== null ? { intensity: a.intensity } : {}), ...(a.schedule ? { schedule: a.schedule } : {}) }));
  const fragment: Record<string, unknown> = { segments, nodes };
  if (links.length) fragment["links"] = links;
  if (conduits.length) fragment["conduits"] = conduits;
  if (assignments.length) fragment["assignments"] = assignments;
  // Positions relative to the section's top-left, devices first (hosts hang off their segment).
  const placed = [...p.set.devices, ...p.set.hosts].map((n) => [n, positions[`host:${n}`]] as const).filter((x): x is readonly [string, Point] => Boolean(x[1]));
  const layout: Record<string, Point> = {};
  if (placed.length) {
    const minX = Math.min(...placed.map(([, q]) => q.x));
    const minY = Math.min(...placed.map(([, q]) => q.y));
    for (const [n, q] of placed) layout[nameOf(n)] = { x: Math.round(q.x - minX), y: Math.round(q.y - minY) };
  }
  let id = slug(choices.label) || "template";
  const base = id;
  for (let i = 2; takenIds.includes(id); i++) id = `${base}-${i}`;
  return { id, builtin: false, label: choices.label.trim() || id, description: choices.description.trim(), icon: choices.icon, params, ports: p.ports.map((x) => ({ name: x.name, device: nameOf(x.device) })), fragment, layout };
}

// ---- "My templates": the ones this browser saved (the catalog holds no author; no sharing model, §5.3) ----
export const MY_TEMPLATES_KEY = "axr-my-templates";

export function readMyTemplates(): string[] {
  try {
    const v = JSON.parse(localStorage.getItem(MY_TEMPLATES_KEY) ?? "[]") as unknown;
    return Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : [];
  } catch {
    return [];
  }
}

export function rememberMyTemplate(id: string): void {
  try {
    const cur = readMyTemplates();
    if (!cur.includes(id)) localStorage.setItem(MY_TEMPLATES_KEY, JSON.stringify([...cur, id]));
  } catch {
    // no storage: it still lists, under "From the team"
  }
}
