/**
 * The tap plan (`tap/*.yaml`) as the canvas reads and edits it
 * (`docs/design/topology-layers.md` §8): one binding per tap point -- a
 * selector (`scope: segment | boundary | port | perimeter`, its `target`, a
 * port's `segment`), the sensors it delivers to, its mode -- read from the
 * document's text and edited as line splices like the traffic profile
 * (`tfc.ts`), so every untouched line stays byte-for-byte. A tap change is
 * the `taps` kind: live, the tap playout replays.
 */
import type { TeModel } from "./te";
import { contentEnd, encodeScalar, entry, get, insertLines, joinLines, parseYaml, removeSpan, rewriteFlowMap, scalarText, toPlain, type YMap, type YSeq } from "./yaml";

export interface TapBinding {
  /** The list index, what a selection and an edit address. */
  index: number;
  scope: string;
  /** The selector's target(s): a segment, a boundary's two segments, a port's host. */
  targets: string[];
  /** A port selector's segment. */
  segment: string | null;
  sensors: string[];
  mode: string | null;
  source: string | null;
}

export interface TapModel {
  target: string | null;
  bindings: TapBinding[];
  problems: string[];
}

function strings(node: ReturnType<typeof get>): string[] {
  const v = toPlain(node);
  if (v === null) return [];
  if (Array.isArray(v)) return v.map((x) => String(x));
  return [String(v)];
}

export function readTap(text: string): TapModel {
  const doc = parseYaml(text);
  const tp = get(doc.root, "tap-plan");
  if (!tp || tp.kind !== "map") return { target: null, bindings: [], problems: ["no `tap-plan:` mapping at the top of the file"] };
  const problems: string[] = [];
  const bindings: TapBinding[] = [];
  const bs = get(tp, "bindings");
  if (bs && bs.kind === "seq") {
    bs.items.forEach((it, index) => {
      const sel = get(it.value, "selector");
      const scope = scalarText(get(sel, "scope"));
      if (!scope) {
        problems.push(`tap binding at line ${it.line + 1} has no selector scope`);
        return;
      }
      bindings.push({
        index,
        scope,
        targets: strings(get(sel, "target")),
        segment: scalarText(get(sel, "segment")),
        sensors: strings(get(it.value, "sensors")),
        mode: scalarText(get(it.value, "mode")),
        source: scalarText(get(it.value, "source")),
      });
    });
  }
  return { target: scalarText(get(tp, "target")), bindings, problems };
}

export function emptyTap(twinId: string): string {
  return `tap-plan:\n  target: ${encodeScalar(twinId)}\n  bindings: []\n`;
}

/** The taps that sit on a segment: a `segment` binding naming it, or a
 *  `boundary` binding naming it among its targets. */
export function tapsOn(tap: TapModel | null, segment: string): TapBinding[] {
  if (!tap) return [];
  return tap.bindings.filter((b) => (b.scope === "segment" || b.scope === "boundary") && b.targets.includes(segment));
}

function bindingsSeq(text: string): { lines: string[]; tp: YMap; seq: YSeq | null; trailingNewline: boolean } {
  const doc = parseYaml(text);
  const lines = [...doc.lines];
  const tp = get(doc.root, "tap-plan");
  if (!tp || tp.kind !== "map") throw new Error("the tap plan has no `tap-plan:` mapping");
  const e = entry(tp, "bindings");
  const seq = e && e.value.kind === "seq" && !e.value.flow ? e.value : null;
  return { lines, tp, seq, trailingNewline: doc.trailingNewline };
}

/** Append a binding: a `segment` tap on `segment`, or a `port` tap on a
 *  device's port (`segment` names the leg it mirrors). */
export function addTapBinding(text: string, binding: { scope: "segment" | "port"; target: string; segment?: string | null; sensors: string[]; mode?: string | null; source?: string | null }): string {
  const { lines, tp, seq, trailingNewline } = bindingsSeq(text);
  const e = entry(tp, "bindings");
  let at: number;
  let dashIndent: number;
  if (seq && seq.items.length) {
    at = contentEnd(lines, seq.end);
    dashIndent = seq.indent;
  } else if (e) {
    const raw = lines[e.line]!;
    lines[e.line] = raw.slice(0, raw.indexOf("bindings") + "bindings:".length);
    at = e.line + 1;
    dashIndent = tp.indent + 2;
  } else {
    at = contentEnd(lines, tp.end);
    insertLines(lines, at, [`${" ".repeat(tp.indent)}bindings:`]);
    at++;
    dashIndent = tp.indent + 2;
  }
  const pad = " ".repeat(dashIndent + 2);
  const selector = binding.scope === "port" && binding.segment ? `{ scope: port, target: ${encodeScalar(binding.target)}, segment: ${encodeScalar(binding.segment)} }` : `{ scope: ${binding.scope}, target: ${encodeScalar(binding.target)} }`;
  const item = [`${" ".repeat(dashIndent)}- selector: ${selector}`];
  if (binding.sensors.length) item.push(`${pad}sensors: [${binding.sensors.map((s) => encodeScalar(s)).join(", ")}]`);
  item.push(`${pad}mode: ${binding.mode ?? "exclusive"}`);
  if (binding.source) item.push(`${pad}source: ${binding.source}`);
  insertLines(lines, at, item);
  return joinLines(lines, trailingNewline);
}

export function removeTapBinding(text: string, index: number): string {
  const { lines, seq, trailingNewline } = bindingsSeq(text);
  const it = seq?.items[index];
  if (!seq || !it) return text;
  removeSpan(lines, it.start, it.end, seq.indent);
  return joinLines(lines, trailingNewline);
}

/** Re-point a `segment` tap at another segment (a move, a `taps` change). */
export function moveTapBinding(text: string, index: number, segment: string): string {
  const { lines, seq, trailingNewline } = bindingsSeq(text);
  const it = seq?.items[index];
  if (!it) return text;
  const sel = get(it.value, "selector");
  if (!sel || sel.kind !== "map") throw new Error(`tap binding ${index} has no selector mapping`);
  if (scalarText(get(sel, "scope")) !== "segment") throw new Error(`tap binding ${index} is a ${scalarText(get(sel, "scope"))} tap; only a segment tap moves by drag`);
  if (sel.flow) rewriteFlowMap(lines, sel, "target", encodeScalar(segment));
  else {
    const e = entry(sel, "target");
    if (!e) throw new Error(`tap binding ${index} names no target`);
    const raw = lines[e.line]!;
    lines[e.line] = raw.slice(0, e.valueCol) + encodeScalar(segment);
  }
  return joinLines(lines, trailingNewline);
}

/** Set a binding's sensors (the list as written: `[a, b]`). */
export function setTapSensors(text: string, index: number, sensors: string[]): string {
  const { lines, seq, trailingNewline } = bindingsSeq(text);
  const it = seq?.items[index];
  if (!it || it.value.kind !== "map") return text;
  const m = it.value;
  const e = entry(m, "sensors");
  const raw = `[${sensors.map((s) => encodeScalar(s)).join(", ")}]`;
  if (e) {
    const line = lines[e.line]!;
    // A flow list sits on the key's line (valueCol points at it); a block list has no value on
    // the line (valueCol -1) and spans the lines below, which go.
    lines[e.line] = e.valueCol >= 0 ? line.slice(0, e.valueCol) + raw : `${line.slice(0, line.indexOf("sensors"))}sensors: ${raw}`;
    if (e.end > e.line + 1) removeSpan(lines, e.line + 1, e.end, m.indent);
  } else insertLines(lines, contentEnd(lines, it.end), [`${" ".repeat(m.indent)}sensors: ${raw}`]);
  return joinLines(lines, trailingNewline);
}

// ---------------------------------------------------------------------------
// Moving a tap (topology-layers §8, 2026-11-09): any binding re-pointed at a
// segment, a boundary of two segments, or one end of a link (a port selector)
// -- by a drag of its chip or the inspector's Scope / Target -- as ONE splice
// of its selector, so a move is one taps change and one undo step.
// ---------------------------------------------------------------------------
export type TapSelector =
  | { scope: "segment"; target: string }
  | { scope: "boundary"; target: [string, string] }
  | { scope: "port"; target: string; segment: string };

export function selectorText(sel: TapSelector): string {
  if (sel.scope === "boundary") return `{ scope: boundary, target: [${sel.target.map((t) => encodeScalar(t)).join(", ")}] }`;
  if (sel.scope === "port") return `{ scope: port, target: ${encodeScalar(sel.target)}, segment: ${encodeScalar(sel.segment)} }`;
  return `{ scope: segment, target: ${encodeScalar(sel.target)} }`;
}

/** The binding's selector as a `TapSelector`, or null when it is not one the canvas moves. */
export function selectorOf(b: TapBinding): TapSelector | null {
  if (b.scope === "segment" && b.targets[0]) return { scope: "segment", target: b.targets[0] };
  if (b.scope === "boundary" && b.targets.length === 2) return { scope: "boundary", target: [b.targets[0]!, b.targets[1]!] };
  if (b.scope === "port" && b.targets[0] && b.segment) return { scope: "port", target: b.targets[0], segment: b.segment };
  return null;
}

export function sameSelector(a: TapSelector | null, b: TapSelector | null): boolean {
  return a !== null && b !== null && selectorText(a) === selectorText(b);
}

/** Replace binding `index`'s selector with `sel`, written as one flow map on the
 *  `selector:` line (a block selector's nested lines go); every other line stays. */
export function setTapSelector(text: string, index: number, sel: TapSelector): string {
  const { lines, seq, trailingNewline } = bindingsSeq(text);
  const it = seq?.items[index];
  if (!it || it.value.kind !== "map") throw new Error(`no tap binding ${index}`);
  const e = entry(it.value, "selector");
  if (!e) throw new Error(`tap binding ${index} addresses its point by \`tap:\`/\`tap_point:\`, not a selector: re-create it to move it`);
  const line = lines[e.line]!;
  lines[e.line] = `${line.slice(0, line.indexOf("selector") + "selector:".length)} ${selectorText(sel)}`;
  if (e.end > e.line + 1) lines.splice(e.line + 1, contentEnd(lines, e.end) - (e.line + 1));
  return joinLines(lines, trailingNewline);
}

/** A tap dropped on a link: a port selector on the end that is a VM (a switch
 *  with no image deploys nothing to mirror), on the segment the link carries
 *  (its native, else its first VLAN). Null when neither end can carry it. */
export function portOnLink(te: TeModel, linkIndex: number): TapSelector | null {
  const l = te.links.find((x) => x.index === linkIndex);
  if (!l) return null;
  const carried = l.native ?? l.vlans[0] ?? null;
  for (const end of [l.a, l.b]) {
    const n = te.nodes.find((x) => x.name === end.device);
    if (!n || !n.image || n.kind === "switch") continue;
    const seg = carried && n.nets.some((x) => x.segment === carried) ? carried : (n.nets.find((x) => l.vlans.includes(x.segment))?.segment ?? n.nets[0]?.segment ?? null);
    if (seg) return { scope: "port", target: n.name, segment: seg };
  }
  return null;
}

/** A tap dropped on a router that joins exactly two segments: the boundary
 *  between them (the point between the two segments' drops). */
export function boundaryAt(te: TeModel, router: string): TapSelector | null {
  const n = te.nodes.find((x) => x.name === router);
  const segs = [...new Set(n?.nets.map((x) => x.segment) ?? [])];
  return n && n.kind === "router" && segs.length === 2 ? { scope: "boundary", target: [segs[0]!, segs[1]!] } : null;
}
