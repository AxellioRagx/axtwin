/**
 * Templates on the canvas (`docs/design/canvas-add-panel.md` §5.2): a template's settings applied to
 * its fragment (the SAME expansion as the engine's `axrange/api/templates.py` -- `{expr}` over the
 * settings, a node counted 0 dropped with what names it, `profile:` resolved to an image), the
 * expansion written as a source document through the twin-config writers, then copied into the
 * design by the duplicate-subtree engine (`edit/subtree.ts` `copyInto`): names kept when free, else
 * `-2`; every segment on a fresh `/24` (the first on the subnet setting when it is not `auto`); the
 * node-level persona bindings carried. One pure `(doc) => files`, so one undo step. Pure.
 */
import type { ImageProfile, Template, TemplateParam } from "../../../api/types";
import type { Files } from "../edit/commands";
import { readDoc, type DocModel, type WorkingDoc } from "../edit/document";
import { copyInto } from "../edit/subtree";
import { addConduit, addLink, addNode, addSegment, type NewLink, type TeNet } from "../edit/te";
import { emptyTfc, setAssignment } from "../edit/tfc";
import { DEFAULT_LAN_PREFIX, lanSitePrefix } from "../edit/allocate";

export type Values = Record<string, number | string>;
type Point = { x: number; y: number };

// ---------------------------------------------------------------------------
// Expressions: a setting name, or arithmetic over them (+ - * /, parentheses, round floor ceil min max)
// ---------------------------------------------------------------------------
const FUNCS: Record<string, (...a: number[]) => number> = { round: (x) => Math.floor(x + 0.5), floor: Math.floor, ceil: Math.ceil, min: Math.min, max: Math.max };

type Tok = { kind: "num"; v: number } | { kind: "id"; v: string } | { kind: "op"; v: string };

function tokens(text: string): Tok[] {
  const out: Tok[] = [];
  const re = /\s*(?:(\d+(?:\.\d+)?)|([A-Za-z_][A-Za-z0-9_]*)|(\S))/gy;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null && m[0] !== "") {
    if (m[1] !== undefined) out.push({ kind: "num", v: Number(m[1]) });
    else if (m[2] !== undefined) out.push({ kind: "id", v: m[2] });
    else if (m[3] !== undefined) out.push({ kind: "op", v: m[3] });
  }
  return out;
}

export function evaluate(expr: string, values: Values): number | string {
  const toks = tokens(expr);
  const bad = () => new Error(`bad expression {${expr}}`);
  if (toks.length === 1 && toks[0]!.kind === "id" && !FUNCS[toks[0]!.v]) {
    const v = values[toks[0]!.v];
    if (v === undefined) throw new Error(`unknown setting '${toks[0]!.v}' in {${expr}}`);
    return v;
  }
  let i = 0;
  const peek = () => toks[i];
  const isOp = (v: string) => peek()?.kind === "op" && peek()!.v === v;
  const factor = (): number => {
    const t = peek();
    if (!t) throw bad();
    if (isOp("-")) {
      i++;
      return -factor();
    }
    if (isOp("(")) {
      i++;
      const v = add();
      if (!isOp(")")) throw bad();
      i++;
      return v;
    }
    if (t.kind === "num") {
      i++;
      return t.v;
    }
    if (t.kind === "id") {
      i++;
      if (isOp("(")) {
        const fn = FUNCS[t.v];
        if (!fn) throw new Error(`unknown function '${t.v}' in {${expr}}`);
        i++;
        const args = [add()];
        while (isOp(",")) {
          i++;
          args.push(add());
        }
        if (!isOp(")")) throw bad();
        i++;
        return fn(...args);
      }
      const v = values[t.v];
      if (typeof v !== "number") throw new Error(`setting '${t.v}' is not a number in {${expr}}`);
      return v;
    }
    throw bad();
  };
  const mul = (): number => {
    let v = factor();
    while (isOp("*") || isOp("/")) {
      const op = peek()!.v;
      i++;
      const r = factor();
      if (op === "/" && r === 0) throw new Error(`division by zero in {${expr}}`);
      v = op === "*" ? v * r : v / r;
    }
    return v;
  };
  const add = (): number => {
    let v = mul();
    while (isOp("+") || isOp("-")) {
      const op = peek()!.v;
      i++;
      const r = mul();
      v = op === "+" ? v + r : v - r;
    }
    return v;
  };
  const v = add();
  if (i !== toks.length) throw bad();
  return v;
}

const WHOLE = /^\{([^{}]+)\}$/;
const PART = /\{([^{}]+)\}/g;

/** `{expr}` replaced throughout (strings, arrays, objects and their keys); a lone `{expr}` keeps a number. */
export function substitute(value: unknown, values: Values): unknown {
  if (typeof value === "string") {
    const whole = WHOLE.exec(value.trim());
    if (whole) return evaluate(whole[1]!, values);
    return value.replace(PART, (_m, e: string) => String(evaluate(e, values)));
  }
  if (Array.isArray(value)) return value.map((v) => substitute(v, values));
  if (value && typeof value === "object") return Object.fromEntries(Object.entries(value).map(([k, v]) => [String(substitute(k, values)), substitute(v, values)]));
  return value;
}

/** Each setting's value: `given` over the default, clamped to its type. */
export function settingsValues(params: readonly TemplateParam[], given: Values = {}): Values {
  const out: Values = {};
  for (const p of params) {
    const kind = p.type ?? "int";
    let v: number | string | null | undefined = given[p.name] ?? p.default;
    if (kind === "int" || kind === "percent") {
      let n = Math.trunc(Number(v));
      if (!Number.isFinite(n)) n = Math.trunc(Number(p.default) || 0);
      const lo = p.min ?? 0;
      const hi = p.max ?? (kind === "percent" ? 100 : undefined);
      n = Math.max(lo, n);
      if (hi !== undefined) n = Math.min(hi, n);
      v = n;
    } else v = v === null || v === undefined ? "" : String(v);
    out[p.name] = v;
  }
  return out;
}

type Obj = Record<string, unknown>;
export interface Expanded {
  values: Values;
  segments: Obj[];
  nodes: Obj[];
  links: Obj[];
  conduits: Obj[];
  assignments: Obj[];
  ports: Array<{ name: string; device: string }>;
  layout: Record<string, Point>;
}

const list = (v: unknown): Obj[] => (Array.isArray(v) ? (v.filter((x) => x && typeof x === "object") as Obj[]) : []);

/** The template with its settings applied (mirrors the engine's `templates.expand`). */
export function expandTemplate(tpl: Pick<Template, "params" | "fragment" | "ports" | "layout">, given: Values = {}, profiles: readonly ImageProfile[] = []): Expanded {
  const values = settingsValues(tpl.params, given);
  const frag = substitute(tpl.fragment ?? {}, values) as Obj;
  const dropped = new Set<string>();
  const nodes: Obj[] = [];
  for (const raw of list(frag["nodes"])) {
    const n = { ...raw };
    const count = n["count"] ?? 1;
    if (typeof count === "number" && count <= 0) {
      dropped.add(String(n["name"]));
      continue;
    }
    const prof = n["profile"];
    delete n["profile"];
    if (prof && !n["image"]) n["image"] = profiles.find((p) => p.name === prof)?.image ?? "";
    nodes.push(n);
  }
  const end = (l: Obj, k: "a" | "b") => String((l[k] as Obj | undefined)?.["device"] ?? "");
  return {
    values,
    segments: list(frag["segments"]),
    nodes,
    links: list(frag["links"]).filter((l) => !dropped.has(end(l, "a")) && !dropped.has(end(l, "b"))),
    conduits: list(frag["conduits"]),
    assignments: list(frag["assignments"]).filter((a) => !dropped.has(String(a["node"]))),
    ports: (substitute(tpl.ports ?? [], values) as Array<{ name: string; device: string }>).filter((p) => !dropped.has(p.device)),
    layout: substitute(tpl.layout ?? {}, values) as Record<string, Point>,
  };
}

const str = (v: unknown): string | null => (v === undefined || v === null || v === "" ? null : String(v));

/** The expansion as a document of its own (twin-config text through the writers, a traffic profile for its bindings). */
export function templateSource(x: Expanded): WorkingDoc {
  let te = "twin_config:\n  id: template\n";
  for (const s of x.segments) te = addSegment(te, { name: String(s["name"]), cidr: String(s["cidr"]), zoneType: str(s["zone_type"]) });
  for (const n of x.nodes) {
    const nets: TeNet[] = Array.isArray(n["nets"]) ? list(n["nets"]).map((l) => ({ segment: String(l["segment"]), ip: str(l["ip"]) })) : n["net"] ? [{ segment: String(n["net"]), ip: null }] : [];
    const kind = n["kind"] === "router" || n["kind"] === "switch" ? n["kind"] : "host";
    const count = n["count"];
    te = addNode(te, {
      name: String(n["name"]),
      kind,
      role: str(n["role"]),
      image: str(n["image"]) ?? "",
      nets,
      requires: Array.isArray(n["requires"]) ? n["requires"].map(String) : [],
      resources: n["resources"] && typeof n["resources"] === "object" ? (n["resources"] as Record<string, string | number>) : null,
      ...(typeof count === "number" || typeof count === "string" ? { count } : {}),
      ...(n["stp"] === "rstp" ? { stp: "rstp" as const } : {}),
    });
  }
  for (const l of x.links) {
    const a = l["a"] as Obj;
    const b = l["b"] as Obj;
    const link: NewLink = { a: { device: String(a["device"]), port: String(a["port"]) }, b: { device: String(b["device"]), port: String(b["port"]) }, mode: l["mode"] === "access" ? "access" : "trunk", native: str(l["native"]), vlans: Array.isArray(l["vlans"]) ? l["vlans"].map(String) : [] };
    te = addLink(te, link);
  }
  for (const c of x.conduits) te = addConduit(te, String(c["from"]), String(c["to"]));
  const files: Files = { "te.yaml": te };
  if (x.assignments.length) {
    let tfc = emptyTfc("template");
    for (const a of x.assignments) {
      const patch: { persona: string; intensity?: number | null; schedule?: string | null } = { persona: String(a["persona"]) };
      if (typeof a["intensity"] === "number") patch.intensity = a["intensity"];
      if (str(a["schedule"])) patch.schedule = String(a["schedule"]);
      tfc = setAssignment(tfc, "node", String(a["node"]), patch);
    }
    files["tfc/default.yaml"] = tfc;
  }
  return { id: "template", teFile: "te.yaml", files };
}

export interface TemplateDrop {
  files: Files;
  /** Expanded name -> name in the design. */
  names: Record<string, string>;
  cidrs: Record<string, string>;
  /** The open ports' devices, as named in the design. */
  ports: string[];
  /** Where each laid-out item stands, by pin key, for a drop at `at`. */
  positions: Record<string, Point>;
}

/** A name kept when free, else `{name}-2`, `-3`, … */
function keepOrNumber(name: string, taken: ReadonlySet<string>): string {
  if (!taken.has(name)) return name;
  for (let i = 2; i < 10000; i++) if (!taken.has(`${name}-${i}`)) return `${name}-${i}`;
  return `${name}-copy`;
}

/** Drop a template into the design: expand, then copy through the duplicate-subtree engine. */
export function applyTemplateDrop(doc: WorkingDoc, model: DocModel, tpl: Template, given: Values, profiles: readonly ImageProfile[], at: Point | null): TemplateDrop {
  const x = expandTemplate(tpl, given, profiles);
  const src = templateSource(x);
  const source = readDoc(src);
  if (source.te.problems.length) throw new Error(`template ${tpl.id}: ${source.te.problems[0]}`);
  const subnet = tpl.params.find((p) => (p.type ?? "int") === "cidr");
  const given_ = subnet ? String(x.values[subnet.name] ?? "auto").trim() : "auto";
  const firstCidr = given_ && given_ !== "auto" ? given_ : null;
  // `auto`: the design's own site (its first LAN's two-octet prefix; a transit is not a site), else
  // the template's placeholders.
  // One LAN pool for a template and a hand drop (B7): the design's first LAN's site, else the default
  // LAN prefix (10.200) -- never the internet's or the transits' range.
  const site = lanSitePrefix(model.te.segments.map((s) => s.cidr)) ?? DEFAULT_LAN_PREFIX;
  const set = {
    devices: source.te.nodes.filter((n) => n.kind !== "host").map((n) => n.name),
    segments: source.te.segments.map((s) => s.name),
    hosts: source.te.nodes.filter((n) => n.kind === "host").map((n) => n.name),
    links: source.te.links.map((l) => l.index),
  };
  const r = copyInto(doc, model, source, set, { rename: keepOrNumber, prefix: firstCidr ? null : site, firstCidr, conduits: true });
  const positions: Record<string, Point> = {};
  if (at) {
    for (const [key, p] of Object.entries(x.layout)) {
      const nn = r.names[key];
      if (!nn || typeof p?.x !== "number" || typeof p?.y !== "number") continue;
      const cidr = r.cidrs[nn];
      positions[cidr ? `segment:${cidr}` : `host:${nn}`] = { x: Math.round(at.x + p.x), y: Math.round(at.y + p.y) };
    }
  }
  return { files: r.files, names: r.names, cidrs: r.cidrs, ports: x.ports.map((p) => r.names[p.device] ?? p.device), positions };
}

// ---------------------------------------------------------------------------
// The card: device counts and a preview drawn from the fragment
// ---------------------------------------------------------------------------
export interface TemplateCounts {
  routers: number;
  switches: number;
  hosts: number;
  segments: number;
}

export function templateCounts(x: Expanded): TemplateCounts {
  let routers = 0;
  let switches = 0;
  let hosts = 0;
  for (const n of x.nodes) {
    if (n["kind"] === "router") routers++;
    else if (n["kind"] === "switch") switches++;
    else hosts += typeof n["count"] === "number" ? n["count"] : 1;
  }
  return { routers, switches, hosts, segments: x.segments.length };
}

export function countsText(c: TemplateCounts): string {
  const part = (n: number, one: string, many: string) => (n ? `${n} ${n === 1 ? one : many}` : null);
  return [part(c.routers, "router", "routers"), part(c.switches, "switch", "switches"), part(c.hosts, "host", "hosts")].filter(Boolean).join(" · ") || "empty";
}

export interface PreviewShape {
  width: number;
  height: number;
  routers: Point[];
  segments: Array<{ x1: number; x2: number; y: number; name: string }>;
  switches: Point[];
  hosts: Array<Point & { count: number }>;
  edges: Array<{ a: Point; b: Point }>;
}

/** The small picture a template card shows: routers on top, each segment a bar, its switch at the left, its hosts under it. */
export function previewShape(x: Expanded, width = 120, height = 64): PreviewShape {
  const segs = x.segments.map((s) => String(s["name"]));
  const segOf = (n: Obj): string[] => (Array.isArray(n["nets"]) ? list(n["nets"]).map((l) => String(l["segment"])) : n["net"] ? [String(n["net"])] : []);
  const colW = width / Math.max(1, segs.length);
  const segments = segs.map((name, i) => ({ name, x1: i * colW + 6, x2: (i + 1) * colW - 6, y: 30 }));
  const routersN = x.nodes.filter((n) => n["kind"] === "router");
  const routers = routersN.map((_, i) => ({ x: ((i + 1) * width) / (routersN.length + 1), y: 10 }));
  const edges: PreviewShape["edges"] = [];
  routersN.forEach((r, i) => {
    for (const s of segOf(r)) {
      const sg = segments.find((g) => g.name === s);
      if (sg) edges.push({ a: routers[i]!, b: { x: (sg.x1 + sg.x2) / 2, y: sg.y } });
    }
  });
  // A switch with no leg (wired by a link) sits on the segment its link carries.
  const linked = (name: string): string[] => x.links.filter((l) => (l["a"] as Obj)?.["device"] === name || (l["b"] as Obj)?.["device"] === name).flatMap((l) => [l["native"], ...(Array.isArray(l["vlans"]) ? l["vlans"] : [])].filter(Boolean).map(String));
  const switches: Point[] = [];
  for (const n of x.nodes.filter((m) => m["kind"] === "switch")) {
    const s = segOf(n)[0] ?? linked(String(n["name"]))[0];
    const sg = segments.find((g) => g.name === s);
    if (sg) switches.push({ x: sg.x1 + 4, y: sg.y });
  }
  const hosts: PreviewShape["hosts"] = [];
  for (const sg of segments) {
    const on = x.nodes.filter((n) => n["kind"] !== "router" && n["kind"] !== "switch" && segOf(n)[0] === sg.name);
    on.forEach((n, j) => hosts.push({ x: sg.x1 + ((j + 1) * (sg.x2 - sg.x1)) / (on.length + 1), y: 48, count: typeof n["count"] === "number" ? n["count"] : 1 }));
  }
  return { width, height, routers, segments, switches, hosts, edges };
}
