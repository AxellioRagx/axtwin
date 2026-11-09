/**
 * `source: authored` on what the canvas writes (`docs/design/topology-layers.md` §3.1, R4 item 2,
 * 2026-11-08). One pass at the editor's choke point (`useEditor.apply`), not a flag threaded
 * through every writer: the document before and after an edit is read, and every segment, node,
 * link, member override and tap binding that is new or whose content changed is stamped
 * `source: authored`, its cast-only `confidence` and `vantage` dropped. An `asserted` item keeps
 * its mark (the assertions seam owns it, and a re-cast re-applies it); an item already authored is
 * left alone. Identity: segments and nodes by name, members by `node#index`, links by their two
 * ends (sorted), tap bindings by their selector; a renamed item is a new one.
 *
 * Pure -- no React.
 */
import type { Files } from "./commands";
import { readTap, type TapBinding } from "./tap";
import { readTe, type TeLink, type TeModel, type TeSource } from "./te";
import { contentEnd, entry, get, insertLines, itemName, joinLines, parseYaml, removeSpan, replaceValueOnLine, rewriteFlowMap, scalarText, type YItem, type YMap } from "./yaml";

const KEEP: ReadonlySet<TeSource | string> = new Set(["authored", "asserted"]);
/** Node keys that conventionally follow `source` (te.ts's NODE_KEY_ORDER). */
const AFTER_SOURCE = new Set(["confidence", "vantage", "when", "install", "provides", "joins", "persona", "observation"]);

type Locate = (root: ReturnType<typeof parseYaml>["root"]) => YItem | null;

export function linkKey(l: Pick<TeLink, "a" | "b">): string {
  return [`${l.a.device}#${l.a.port}`, `${l.b.device}#${l.b.port}`].sort().join("|");
}

function bindingKey(b: TapBinding): string {
  return `${b.scope}:${b.targets.join(",")}@${b.segment ?? ""}`;
}

/** Set one key of a list item's mapping (`raw`, or remove with null), as a line splice. */
function setKey(lines: string[], it: YItem, key: string, raw: string | null, nodeOrder: boolean): void {
  const m = it.value as YMap;
  if (m.flow) {
    rewriteFlowMap(lines, m, key, raw);
    return;
  }
  const e = entry(m, key);
  if (e) {
    if (raw === null) removeSpan(lines, e.start, e.end, m.indent);
    else if (e.valueCol === -1) {
      removeSpan(lines, e.start, e.end, m.indent);
      insertLines(lines, e.start, [`${" ".repeat(m.indent)}${key}: ${raw}`]);
    } else replaceValueOnLine(lines, e, raw);
    return;
  }
  if (raw === null) return;
  let at = contentEnd(lines, it.end);
  if (nodeOrder) for (const x of m.entries) if (AFTER_SOURCE.has(x.key)) { at = x.start; break; }
  insertLines(lines, at, [`${" ".repeat(m.indent)}${key}: ${raw}`]);
}

/** Stamp one item: `source: authored`, no `confidence` / `vantage`. Re-parses per key (positions move). */
function stamp(text: string, locate: Locate, nodeOrder = false): string {
  let out = text;
  for (const [key, raw] of [["confidence", null], ["vantage", null], ["source", "authored"]] as const) {
    const doc = parseYaml(out);
    const it = locate(doc.root);
    if (!it || it.value.kind !== "map") return out;
    const cur = entry(it.value, key);
    if (raw === null ? !cur : scalarText(cur?.value) === raw) continue;
    const lines = [...doc.lines];
    if (it.value.flow && it.value.flowLine === undefined) return out; // a multi-line flow item: not addressable
    setKey(lines, it, key, raw, nodeOrder);
    out = joinLines(lines, doc.trailingNewline);
  }
  return out;
}

const listOf = (root: ReturnType<typeof parseYaml>["root"], key: string) => {
  const s = get(root, "twin_config", key);
  return s && s.kind === "seq" ? s.items : [];
};

function changed<T>(before: Map<string, T>, after: Map<string, T>): string[] {
  const out: string[] = [];
  for (const [k, v] of after) {
    const b = before.get(k);
    if (b === undefined || JSON.stringify(b) !== JSON.stringify(v)) out.push(k);
  }
  return out;
}

function teShapes(te: TeModel) {
  const segments = new Map(te.segments.map((s) => [s.name, { cidr: s.cidr, zoneType: s.zoneType, isolated: s.isolated, egress: s.egress }]));
  const nodes = new Map(te.nodes.map((n) => [n.name, { kind: n.kind, role: n.role, image: n.image, count: n.count, nets: n.nets, requires: n.requires, resources: n.resources, otherKeys: n.otherKeys }]));
  const members = new Map<string, { role: string | null }>(te.nodes.flatMap((n) => n.members.map((m) => [`${n.name}#${m.index}`, { role: m.role }] as [string, { role: string | null }])));
  const links = new Map(te.links.map((l) => [linkKey(l), { mode: l.mode, native: l.native, vlans: l.vlans, state: l.state }]));
  return { segments, nodes, members, links };
}

/** The te text with every item the edit created or changed stamped authored. */
export function stampTe(before: string | undefined, after: string): string {
  const prev = before === undefined ? null : readTe(before);
  const next = readTe(after);
  if (next.problems.length && next.nodes.length === 0) return after;
  const p = teShapes(prev ?? { ...next, segments: [], nodes: [], links: [] });
  const n = teShapes(next);
  let text = after;
  const src = {
    segment: new Map(next.segments.map((s) => [s.name, s.source])),
    node: new Map(next.nodes.map((x) => [x.name, x.source])),
    member: new Map<string, TeSource | null>(next.nodes.flatMap((x) => x.members.map((m) => [`${x.name}#${m.index}`, m.source] as [string, TeSource | null]))),
    link: new Map(next.links.map((l) => [linkKey(l), l.source])),
  };
  const due = (kind: keyof typeof src, k: string) => !KEEP.has(src[kind].get(k) ?? "");
  for (const name of changed(p.segments, n.segments)) if (due("segment", name)) text = stamp(text, (r) => listOf(r, "segments").find((i) => itemName(i) === name) ?? null);
  for (const name of changed(p.nodes, n.nodes)) if (due("node", name)) text = stamp(text, (r) => listOf(r, "nodes").find((i) => itemName(i) === name) ?? null, true);
  for (const k of changed(p.members, n.members)) {
    if (!due("member", k)) continue;
    const [node, index] = [k.slice(0, k.lastIndexOf("#")), k.slice(k.lastIndexOf("#") + 1)];
    text = stamp(text, (r) => {
      const it = listOf(r, "nodes").find((i) => itemName(i) === node);
      const ms = it ? get(it.value, "members") : null;
      return ms && ms.kind === "seq" ? (ms.items.find((m) => scalarText(get(m.value, "index")) === index) ?? null) : null;
    });
  }
  for (const k of changed(p.links, n.links)) {
    if (!due("link", k)) continue;
    text = stamp(text, (r) =>
      listOf(r, "links").find((i) => {
        const end = (e: string) => `${scalarText(get(i.value, e, "device"))}#${scalarText(get(i.value, e, "port"))}`;
        return [end("a"), end("b")].sort().join("|") === k;
      }) ?? null,
    );
  }
  return text;
}

/** The tap plan's text with every binding the edit created or changed stamped authored. A
 *  binding's SENSORS are not its provenance (2026-11-09): they are the lab fact the cast cannot
 *  observe and its installer fills in, so a discovered tap given a sensor stays discovered; where
 *  it sits (its selector, the identity) and its mode are. */
export function stampTap(before: string | undefined, after: string): string {
  const shape = (text: string | undefined) => new Map((text === undefined ? [] : readTap(text).bindings).map((b) => [bindingKey(b), { mode: b.mode }]));
  const next = readTap(after);
  let text = after;
  for (const k of changed(shape(before), shape(after))) {
    const b = next.bindings.find((x) => bindingKey(x) === k);
    if (!b || KEEP.has(b.source ?? "")) continue;
    text = stamp(text, (r) => {
      const bs = get(r, "tap-plan", "bindings");
      return bs && bs.kind === "seq" ? (bs.items[b.index] ?? null) : null;
    });
  }
  return text;
}

/** Every file of an edit's result, stamped: the te file and the tap plan(s). */
export function stampAuthored(prev: Files, next: Files, teFile: string): Files {
  let out = next;
  for (const path of Object.keys(next)) {
    if (prev[path] === next[path]) continue;
    const isTe = path === teFile;
    const isTap = path.startsWith("tap/") && path.endsWith(".yaml");
    if (!isTe && !isTap) continue;
    try {
      const text = isTe ? stampTe(prev[path], next[path]!) : stampTap(prev[path], next[path]!);
      if (text !== next[path]) out = { ...out, [path]: text };
    } catch {
      // A file the reader cannot address is left as the edit wrote it; the validator says why.
    }
  }
  return out;
}

/** A te or tap text with its provenance keys gone (block lines and flow pairs), for comparison only. */
function withoutProvenance(text: string): string {
  return text
    .split("\n")
    .filter((l) => !/^\s*(source|confidence|vantage):/.test(l))
    .map((l) => l.replace(/,\s*(source|confidence|vantage):\s*[^,}]+(?=\s*[,}])/g, ""))
    .join("\n");
}

/** After a promote-to-assertion re-casts the twin, the working copy's files that now match the new
 *  baseline in everything but provenance take the baseline's text: the change went through the
 *  seam, so the cast's `asserted` mark is the record, not the canvas's `authored` one. */
export function adoptBaselineProvenance(files: Files, baseline: Files | null | undefined, teFile: string): Files {
  if (!baseline) return files;
  let out = files;
  for (const path of Object.keys(files)) {
    if (path !== teFile && !(path.startsWith("tap/") && path.endsWith(".yaml"))) continue;
    const base = baseline[path];
    if (base !== undefined && base !== files[path] && withoutProvenance(base) === withoutProvenance(files[path]!)) out = { ...out, [path]: base };
  }
  return out;
}
