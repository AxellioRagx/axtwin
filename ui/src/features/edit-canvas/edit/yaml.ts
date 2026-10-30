/**
 * A small, tolerant reader for the YAML subset the twin bundles use -- block
 * mappings and sequences, one-line (or bracket-balanced multi-line) flow
 * collections, plain / quoted / `|` and `>` block scalars, comments -- that
 * keeps every node's LINE SPAN, so the edit layer (`te.ts`, `tfc.ts`) can
 * splice the text it needs to change and leave every other line byte-for-
 * byte as it was. No YAML library is in the lock, and a library that
 * re-emits the document would reformat lines the operator never touched.
 *
 * It is a reader of what the engine writes and what hands author, not a
 * YAML implementation: anchors, tags, multi-document streams and complex
 * keys are not understood (a line it cannot place is kept as an opaque
 * scalar, never dropped). The engine's own loader is the authority on what
 * the document MEANS; this module only finds where things are.
 *
 * Pure -- no React, no DOM.
 */

export interface YScalar {
  kind: "scalar";
  /** The decoded value: numbers and booleans and null recognised from plain scalars; strings otherwise. */
  value: string | number | boolean | null;
  /** The source text of the value (quotes included), `""` for an absent value. */
  raw: string;
  /** The line the value starts on (-1 for a value inside a flow collection). */
  line: number;
}

export interface YEntry {
  key: string;
  value: YNode;
  /** The line the key is on. */
  line: number;
  /** Lines `[start, end)` this entry occupies, the key line included. */
  start: number;
  end: number;
  /** On the key line: where the value text starts and ends (before any comment); `valueCol === -1` when the value is a nested block. */
  valueCol: number;
  valueEnd: number;
}

export interface YMap {
  kind: "map";
  entries: YEntry[];
  /** Indent of the keys (block) or of the line the flow text sits on. */
  indent: number;
  flow: boolean;
  start: number;
  end: number;
  /** For a flow map: the line it starts on and the columns of its `{`..`}` on that line (single-line flows only). */
  flowLine?: number;
  flowCol?: number;
  flowEnd?: number;
}

export interface YItem {
  value: YNode;
  /** The line the `- ` is on. */
  line: number;
  start: number;
  end: number;
}

export interface YSeq {
  kind: "seq";
  items: YItem[];
  indent: number;
  flow: boolean;
  start: number;
  end: number;
  flowLine?: number;
  flowCol?: number;
  flowEnd?: number;
}

export type YNode = YScalar | YMap | YSeq;

export interface YDoc {
  root: YNode | null;
  lines: string[];
  /** Whether the text ended with a newline, so a re-join restores it. */
  trailingNewline: boolean;
}

interface Line {
  indent: number;
  /** The line with its comment and trailing spaces removed; `""` for a blank or comment-only line. */
  content: string;
  /** Column where the content ends (where a trailing comment or whitespace begins). */
  contentEnd: number;
}

// ---------------------------------------------------------------------------
// Lines and comments
// ---------------------------------------------------------------------------
/** Where a trailing `#` comment starts on a line, outside quotes, or -1. A
 *  `#` counts only at column 0 or after whitespace (YAML's rule). */
export function commentStart(raw: string): number {
  let quote: string | null = null;
  for (let i = 0; i < raw.length; i++) {
    const c = raw[i]!;
    if (quote) {
      if (c === "\\" && quote === '"') i++;
      else if (c === quote) quote = null;
      continue;
    }
    if (c === '"' || c === "'") {
      // A quote opens a string only at a value boundary; mid-word (`don't`) it is text.
      const prev = i === 0 ? " " : raw[i - 1]!;
      if (/[\s:,\[{\-]/.test(prev)) quote = c;
      continue;
    }
    if (c === "#" && (i === 0 || /\s/.test(raw[i - 1]!))) return i;
  }
  return -1;
}

function analyse(raw: string): Line {
  const at = commentStart(raw);
  const body = at === -1 ? raw : raw.slice(0, at);
  const trimmedRight = body.replace(/\s+$/, "");
  const indent = trimmedRight.length ? trimmedRight.length - trimmedRight.trimStart().length : 0;
  return { indent, content: trimmedRight.trimStart(), contentEnd: trimmedRight.length };
}

// ---------------------------------------------------------------------------
// Scalars
// ---------------------------------------------------------------------------
export function decodeScalar(raw: string): string | number | boolean | null {
  const t = raw.trim();
  if (t === "" || t === "~" || t === "null") return null;
  if (t === "true") return true;
  if (t === "false") return false;
  if (/^[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?$/.test(t) && !/^[-+]?0\d/.test(t)) return Number(t);
  if (t.startsWith('"') && t.endsWith('"') && t.length >= 2) {
    try {
      return JSON.parse(t) as string;
    } catch {
      return t.slice(1, -1);
    }
  }
  if (t.startsWith("'") && t.endsWith("'") && t.length >= 2) return t.slice(1, -1).replace(/''/g, "'");
  return t;
}

/** The YAML spelling of a value for a line this module writes: plain when
 *  it reads back as itself, double-quoted otherwise. */
export function encodeScalar(value: string | number | boolean | null): string {
  if (value === null) return "null";
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  const plainOk = value !== "" && decodeScalar(value) === value && !/^[\s\-?:,\[\]{}#&*!|>'"%@`]/.test(value) && !/[:#]\s|\s$|^\s/.test(value) && !/[\[\]{},]/.test(value);
  return plainOk ? value : JSON.stringify(value);
}

function scalar(raw: string, line: number): YScalar {
  return { kind: "scalar", value: decodeScalar(raw), raw: raw.trim(), line };
}

// ---------------------------------------------------------------------------
// Flow collections: `{ a: b, c: [d, e] }` on one (joined) string
// ---------------------------------------------------------------------------
function splitTop(text: string, sep: string): string[] {
  const out: string[] = [];
  let depth = 0;
  let quote: string | null = null;
  let cur = "";
  for (let i = 0; i < text.length; i++) {
    const c = text[i]!;
    if (quote) {
      cur += c;
      if (c === "\\" && quote === '"') {
        cur += text[i + 1] ?? "";
        i++;
      } else if (c === quote) quote = null;
      continue;
    }
    if (c === '"' || c === "'") {
      quote = c;
      cur += c;
      continue;
    }
    if (c === "{" || c === "[") depth++;
    else if (c === "}" || c === "]") depth--;
    if (c === sep && depth === 0) {
      out.push(cur);
      cur = "";
      continue;
    }
    cur += c;
  }
  if (cur.trim() !== "") out.push(cur);
  return out;
}

function parseFlow(text: string, line: number): YNode {
  const t = text.trim();
  if (t.startsWith("{") && t.endsWith("}")) {
    const entries: YEntry[] = [];
    for (const part of splitTop(t.slice(1, -1), ",")) {
      const p = part.trim();
      if (!p) continue;
      const at = keySplit(p);
      if (at === -1) {
        entries.push({ key: p, value: scalar("", -1), line, start: line, end: line + 1, valueCol: -1, valueEnd: -1 });
        continue;
      }
      const key = unquoteKey(p.slice(0, at).trim());
      const v = p.slice(at + 1).trim();
      entries.push({ key, value: parseFlowValue(v, line), line, start: line, end: line + 1, valueCol: -1, valueEnd: -1 });
    }
    return { kind: "map", entries, indent: 0, flow: true, start: line, end: line + 1 };
  }
  if (t.startsWith("[") && t.endsWith("]")) {
    const items: YItem[] = [];
    for (const part of splitTop(t.slice(1, -1), ",")) {
      const p = part.trim();
      if (!p) continue;
      items.push({ value: parseFlowValue(p, line), line, start: line, end: line + 1 });
    }
    return { kind: "seq", items, indent: 0, flow: true, start: line, end: line + 1 };
  }
  return scalar(t, line);
}

function parseFlowValue(v: string, line: number): YNode {
  if (v.startsWith("{") || v.startsWith("[")) return parseFlow(v, line);
  return scalar(v, line);
}

/** The index of the `:` that separates a mapping key from its value -- a
 *  colon followed by a space or at the end -- outside quotes; -1 when none. */
function keySplit(text: string): number {
  let quote: string | null = null;
  for (let i = 0; i < text.length; i++) {
    const c = text[i]!;
    if (quote) {
      if (c === quote) quote = null;
      continue;
    }
    if ((c === '"' || c === "'") && i === 0) {
      quote = c;
      continue;
    }
    if (c === "{" || c === "[") return -1;
    if (c === ":" && (i === text.length - 1 || /\s/.test(text[i + 1]!))) return i;
  }
  return -1;
}

function unquoteKey(k: string): string {
  const d = decodeScalar(k);
  return typeof d === "string" ? d : k;
}

function balanced(text: string): boolean {
  let depth = 0;
  let quote: string | null = null;
  for (let i = 0; i < text.length; i++) {
    const c = text[i]!;
    if (quote) {
      if (c === "\\" && quote === '"') i++;
      else if (c === quote) quote = null;
      continue;
    }
    if (c === '"' || c === "'") quote = c;
    else if (c === "{" || c === "[") depth++;
    else if (c === "}" || c === "]") depth--;
  }
  return depth <= 0;
}

// ---------------------------------------------------------------------------
// The block parser
// ---------------------------------------------------------------------------
class Parser {
  readonly lines: Line[];
  constructor(readonly raw: string[]) {
    this.lines = raw.map(analyse);
  }

  /** The next line at or after `i` that carries content, or `lines.length`. */
  nextContent(i: number): number {
    while (i < this.lines.length && this.lines[i]!.content === "") i++;
    return i;
  }

  /** Parse the block that starts at the first content line at/after `from`
   *  whose indent is >= `minIndent`; returns the node and the line after it. */
  parseBlock(from: number, minIndent: number): { node: YNode; end: number } {
    const i = this.nextContent(from);
    if (i >= this.lines.length || this.lines[i]!.indent < minIndent) return { node: scalar("", i), end: from };
    const L = this.lines[i]!;
    if (L.content === "-" || L.content.startsWith("- ")) return this.parseSeq(i, L.indent);
    if (keySplit(L.content) !== -1) return this.parseMap(i, L.indent);
    // A lone scalar (or something this reader cannot place): one line, kept verbatim.
    return { node: scalar(L.content, i), end: i + 1 };
  }

  parseMap(start: number, indent: number): { node: YMap; end: number } {
    const entries: YEntry[] = [];
    let i = start;
    for (;;) {
      const at = this.nextContent(i);
      if (at >= this.lines.length) break;
      const L = this.lines[at]!;
      if (L.indent !== indent) break;
      const split = keySplit(L.content);
      if (split === -1) break;
      if (L.content.startsWith("- ")) break;
      const key = unquoteKey(L.content.slice(0, split).trim());
      const rest = L.content.slice(split + 1).trim();
      const valueCol = L.indent + split + 1 + (L.content.slice(split + 1).length - L.content.slice(split + 1).trimStart().length);
      const r = this.parseValue(at, indent, rest, valueCol, L.contentEnd);
      entries.push({ key, value: r.node, line: at, start: at, end: r.end, valueCol: r.valueCol, valueEnd: r.valueEnd });
      i = r.end;
    }
    return { node: { kind: "map", entries, indent, flow: false, start, end: i }, end: i };
  }

  parseSeq(start: number, indent: number): { node: YSeq; end: number } {
    const items: YItem[] = [];
    let i = start;
    for (;;) {
      const at = this.nextContent(i);
      if (at >= this.lines.length) break;
      const L = this.lines[at]!;
      if (L.indent !== indent || !(L.content === "-" || L.content.startsWith("- "))) break;
      const rest = L.content.slice(1).trim();
      const restCol = L.indent + 1 + (L.content.slice(1).length - L.content.slice(1).trimStart().length);
      if (rest === "") {
        const r = this.parseBlock(at + 1, indent + 1);
        items.push({ value: r.node, line: at, start: at, end: Math.max(r.end, at + 1) });
        i = Math.max(r.end, at + 1);
        continue;
      }
      if (keySplit(rest) !== -1 && !rest.startsWith("{") && !rest.startsWith("[")) {
        // `- key: value` -- a mapping whose first entry shares the dash's line.
        const saved = this.lines[at]!;
        this.lines[at] = { indent: restCol, content: rest, contentEnd: saved.contentEnd };
        const r = this.parseMap(at, restCol);
        this.lines[at] = saved;
        items.push({ value: r.node, line: at, start: at, end: r.end });
        i = r.end;
        continue;
      }
      const r = this.parseValue(at, indent, rest, restCol, L.contentEnd);
      items.push({ value: r.node, line: at, start: at, end: r.end });
      i = r.end;
    }
    return { node: { kind: "seq", items, indent, flow: false, start, end: i }, end: i };
  }

  /** The value after `key:` or `- ` on line `at`: a nested block, a block
   *  scalar, a flow collection (possibly spanning lines) or a scalar. */
  parseValue(at: number, indent: number, rest: string, valueCol: number, contentEnd: number): { node: YNode; end: number; valueCol: number; valueEnd: number } {
    // An anchor or tag alone on the key line (`resources: &id001`) announces
    // the nested block below it; one before a value is dropped from the value.
    if (/^[&!]\S*$/.test(rest)) rest = "";
    else if (/^[&!]\S*\s+/.test(rest)) rest = rest.replace(/^[&!]\S*\s+/, "");
    if (rest === "") {
      // Nested block on the following lines: deeper, or a sequence at the same indent (YAML allows `key:\n- item`).
      const n = this.nextContent(at + 1);
      if (n < this.lines.length) {
        const N = this.lines[n]!;
        if (N.indent > indent || (N.indent === indent && (N.content === "-" || N.content.startsWith("- ")))) {
          const r = this.parseBlock(at + 1, N.indent);
          return { node: r.node, end: r.end, valueCol: -1, valueEnd: -1 };
        }
      }
      return { node: scalar("", at), end: at + 1, valueCol, valueEnd: contentEnd };
    }
    if (/^[|>][-+]?\d*$/.test(rest)) {
      // A block scalar: every following line that is blank or deeper than the key.
      let end = at + 1;
      let last = at + 1;
      while (end < this.lines.length) {
        const L = this.lines[end]!;
        const rawLine = this.raw[end]!;
        if (rawLine.trim() === "" || L.indent > indent || (L.content === "" && rawLine.length - rawLine.trimStart().length > indent)) {
          if (rawLine.trim() !== "") last = end + 1;
          end++;
          continue;
        }
        break;
      }
      const body = this.raw.slice(at + 1, last);
      const strip = body.filter((l) => l.trim() !== "").reduce((m, l) => Math.min(m, l.length - l.trimStart().length), Infinity);
      const text = body.map((l) => (l.trim() === "" ? "" : l.slice(Number.isFinite(strip) ? strip : 0))).join("\n");
      return { node: { kind: "scalar", value: rest.startsWith(">") ? text.replace(/\n(?!\n)/g, " ").trim() : text, raw: rest, line: at }, end: last, valueCol, valueEnd: contentEnd };
    }
    if (rest.startsWith("{") || rest.startsWith("[")) {
      let text = rest;
      let end = at + 1;
      while (!balanced(text) && end < this.lines.length) {
        text += " " + this.lines[end]!.content;
        end++;
      }
      const node = parseFlow(text, at);
      if (node.kind !== "scalar" && end === at + 1) {
        node.flowLine = at;
        node.flowCol = valueCol;
        node.flowEnd = contentEnd;
      }
      return { node, end, valueCol, valueEnd: contentEnd };
    }
    // A quoted scalar may continue on following lines until its closing
    // quote; a plain one may continue on deeper lines that are not entries.
    let end = at + 1;
    let text = rest;
    if ((rest.startsWith('"') && !closesQuote(rest, '"')) || (rest.startsWith("'") && !closesQuote(rest, "'"))) {
      const q = rest[0]!;
      while (end < this.lines.length) {
        const more = this.raw[end]!.trim();
        text += "\n" + more;
        end++;
        if (closesQuote(text, q)) break;
      }
      return { node: { kind: "scalar", value: foldQuoted(text), raw: rest, line: at }, end, valueCol, valueEnd: contentEnd };
    }
    while (end < this.lines.length) {
      const L = this.lines[end]!;
      if (L.content === "" || L.indent <= indent || L.content.startsWith("- ") || keySplit(L.content) !== -1) break;
      text += " " + L.content;
      end++;
    }
    return { node: scalar(text, at), end, valueCol, valueEnd: contentEnd };
  }
}

/** Whether `text`, which opens with quote `q`, also closes it. */
function closesQuote(text: string, q: string): boolean {
  for (let i = 1; i < text.length; i++) {
    const c = text[i]!;
    if (c === "\\" && q === '"') {
      i++;
      continue;
    }
    if (c === q) {
      if (q === "'" && text[i + 1] === "'") {
        i++;
        continue;
      }
      return i === text.length - 1;
    }
  }
  return false;
}

/** A multi-line quoted scalar folded the way YAML folds it: a line break is a
 *  space, an escaped break (`\` at the end of a line) joins directly. */
function foldQuoted(text: string): string {
  const q = text[0]!;
  const inner = text.slice(1, -1);
  // `\` before a break joins the lines; `\ ` is an escaped space; a bare break folds to a space.
  const folded = q === '"' ? inner.replace(/\\\n\s*/g, "").replace(/\\ /g, " ").replace(/\n\s*/g, " ") : inner.replace(/\n\s*/g, " ");
  const d = decodeScalar(q + folded + q);
  return typeof d === "string" ? d : folded;
}

export function parseYaml(text: string): YDoc {
  const trailingNewline = text.endsWith("\n");
  const lines = text.split("\n");
  if (trailingNewline) lines.pop();
  const p = new Parser(lines);
  const first = p.nextContent(0);
  const root = first < lines.length ? p.parseBlock(0, 0).node : null;
  return { root, lines, trailingNewline };
}

export function joinLines(lines: readonly string[], trailingNewline: boolean): string {
  return lines.join("\n") + (trailingNewline ? "\n" : "");
}

// ---------------------------------------------------------------------------
// Reading
// ---------------------------------------------------------------------------
export type Plain = string | number | boolean | null | Plain[] | { [k: string]: Plain };

export function toPlain(node: YNode | null): Plain {
  if (!node) return null;
  if (node.kind === "scalar") return node.value;
  if (node.kind === "seq") return node.items.map((i) => toPlain(i.value));
  const out: { [k: string]: Plain } = {};
  for (const e of node.entries) out[e.key] = toPlain(e.value);
  return out;
}

export function entry(node: YNode | null | undefined, key: string): YEntry | null {
  if (!node || node.kind !== "map") return null;
  return node.entries.find((e) => e.key === key) ?? null;
}

export function get(node: YNode | null | undefined, ...path: string[]): YNode | null {
  let cur: YNode | null | undefined = node;
  for (const k of path) {
    const e = entry(cur, k);
    if (!e) return null;
    cur = e.value;
  }
  return cur ?? null;
}

export function scalarText(node: YNode | null | undefined): string | null {
  if (!node || node.kind !== "scalar" || node.value === null) return null;
  return typeof node.value === "string" ? node.value : String(node.value);
}

/** A sequence item's name field (`- name: x` or `- { name: x }`). */
export function itemName(item: YItem): string | null {
  return scalarText(get(item.value, "name"));
}

// ---------------------------------------------------------------------------
// Writing: line splices that leave every other line as it was
// ---------------------------------------------------------------------------
/** Replace the value text on a scalar entry's key line, keeping the key, the
 *  indent and any trailing comment. */
export function replaceValueOnLine(lines: string[], e: { line: number; valueCol: number; valueEnd: number }, valueRaw: string): void {
  const raw = lines[e.line]!;
  if (e.valueCol === -1) throw new Error(`line ${e.line + 1} holds a nested block, not a scalar`);
  const head = raw.slice(0, e.valueCol).replace(/\s+$/, "");
  const tail = raw.slice(Math.max(e.valueEnd, e.valueCol));
  lines[e.line] = `${head} ${valueRaw}${tail}`.replace(/^(\s*\S.*?:)\s+$/, "$1");
  if (valueRaw === "") lines[e.line] = `${head}${tail}`;
}

/** Re-emit a single-line flow mapping with one pair changed (or added, or
 *  removed when `value` is null), keeping the rest of the line. The flow's
 *  own spacing is normalised; nothing outside its brackets changes. */
export function rewriteFlowMap(lines: string[], m: YMap, key: string, value: string | null): void {
  if (!m.flow || m.flowLine === undefined || m.flowCol === undefined || m.flowEnd === undefined) throw new Error("not a single-line flow mapping");
  const pairs: Array<[string, string]> = [];
  let seen = false;
  for (const e of m.entries) {
    const raw = e.value.kind === "scalar" ? e.value.raw : flowText(e.value);
    if (e.key === key) {
      seen = true;
      if (value !== null) pairs.push([key, value]);
    } else pairs.push([e.key, raw]);
  }
  if (!seen && value !== null) pairs.push([key, value]);
  const text = `{ ${pairs.map(([k, v]) => `${k}: ${v}`).join(", ")} }`;
  const raw = lines[m.flowLine]!;
  lines[m.flowLine] = raw.slice(0, m.flowCol) + text + raw.slice(m.flowEnd);
}

export function flowText(node: YNode): string {
  if (node.kind === "scalar") return node.raw === "" ? "null" : node.raw;
  if (node.kind === "seq") return `[${node.items.map((i) => flowText(i.value)).join(", ")}]`;
  return `{ ${node.entries.map((e) => `${e.key}: ${flowText(e.value)}`).join(", ")} }`;
}

/** Remove lines `[start, end)`, and the comment lines immediately above
 *  `start` that sit at `indent` or deeper (a comment written for the item). */
export function removeSpan(lines: string[], start: number, end: number, indent: number): void {
  let s = start;
  while (s > 0) {
    const prev = lines[s - 1]!;
    const t = prev.trimStart();
    if (t.startsWith("#") && prev.length - t.length >= indent) s--;
    else break;
  }
  lines.splice(s, end - s);
  // A blank line left between two blank lines is one too many.
  if (s > 0 && s < lines.length && lines[s - 1]!.trim() === "" && lines[s]!.trim() === "") lines.splice(s, 1);
}

export function insertLines(lines: string[], at: number, newLines: readonly string[]): void {
  lines.splice(at, 0, ...newLines);
}

/** The end of a block (the line after its last content line), stepping back
 *  over trailing blank lines so an insertion lands right after the content. */
export function contentEnd(lines: readonly string[], end: number): number {
  let e = end;
  while (e > 0 && lines[e - 1]!.trim() === "") e--;
  return e;
}
