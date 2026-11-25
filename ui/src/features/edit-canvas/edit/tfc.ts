/**
 * The traffic profile (`tfc/<file>.yaml`, `traffic-profile.assignments[]`)
 * as the side panel reads and edits it: which persona a node group or a role
 * is bound to, with its intensity and schedule. Edits are line splices
 * (`yaml.ts`); a profile that does not exist yet is created, and the
 * manifest (`twin.yaml`) is wired to it, by `document.ts`.
 *
 * Pure -- no React, no DOM.
 */
import { contentEnd, encodeScalar, entry, get, insertLines, joinLines, parseYaml, removeSpan, replaceValueOnLine, scalarText, toPlain, type YMap, type YSeq } from "./yaml";

export interface TfcAssignment {
  by: "node" | "role";
  target: string;
  persona: string;
  intensity: number | null;
  schedule: string | null;
  seed: string | null;
}

/** trafficgen T2 (`engine/src/axrange/trafficgen/SPEC.md`): the twin's ceilings for its traffic lines. */
export interface TfcBudget {
  cps: number;
  mbps: number;
}

/** `emulated[]`: instances with no VM added to a te node group, or a tfc-only external group. */
export interface TfcEmulated {
  group: string;
  count: number;
  external: boolean;
  segment: string | null;
  role: string | null;
  start: string | null;
}

/** `lines[]`: `from` group -> `to` group, an app mix, a rate. */
export interface TfcLine {
  /** As written (null: the engine's default `<from>--<to>--<mix>`). */
  id: string | null;
  from: string;
  to: string;
  mix: string;
  rate_cps: number;
  max_concurrent: number | null;
}

export interface TfcModel {
  target: string | null;
  assignments: TfcAssignment[];
  budget: TfcBudget | null;
  emulated: TfcEmulated[];
  lines: TfcLine[];
}

/** The id the engine gives a line (`lines[].id`, default `<from>--<to>--<mix>`). */
export function lineId(l: TfcLine): string {
  return l.id ?? `${l.from}--${l.to}--${l.mix}`;
}

export function readTfc(text: string): TfcModel {
  const doc = parseYaml(text);
  const tp = get(doc.root, "traffic-profile");
  const out: TfcModel = { target: scalarText(get(tp, "target") ?? get(tp, "twin")), assignments: [], budget: null, emulated: [], lines: [] };
  const num = (v: unknown): number | null => (typeof v === "number" ? v : typeof v === "string" && v.trim() !== "" && Number.isFinite(Number(v)) ? Number(v) : null);
  const str = (v: unknown): string | null => (v === null || v === undefined || v === "" ? null : String(v));
  const b = toPlain(get(tp, "budget"));
  if (b && typeof b === "object" && !Array.isArray(b)) {
    const cps = num(b["cps"]), mbps = num(b["mbps"]);
    if (cps !== null && mbps !== null) out.budget = { cps, mbps };
  }
  const em = toPlain(get(tp, "emulated"));
  if (Array.isArray(em)) {
    for (const e of em) {
      if (!e || typeof e !== "object" || Array.isArray(e)) continue;
      const group = str(e["group"]), count = num(e["count"]);
      if (!group || count === null) continue;
      out.emulated.push({ group, count, external: e["external"] === true, segment: str(e["segment"]), role: str(e["role"]), start: str(e["start"]) });
    }
  }
  const ls = toPlain(get(tp, "lines"));
  if (Array.isArray(ls)) {
    for (const l of ls) {
      if (!l || typeof l !== "object" || Array.isArray(l)) continue;
      const from = str(l["from"]), to = str(l["to"]), mix = str(l["mix"]), rate = num(l["rate_cps"]);
      if (!from || !to || !mix || rate === null) continue;
      out.lines.push({ id: str(l["id"]), from, to, mix, rate_cps: rate, max_concurrent: num(l["max_concurrent"]) });
    }
  }
  const as = get(tp, "assignments");
  if (as && as.kind === "seq") {
    for (const it of as.items) {
      const node = scalarText(get(it.value, "node"));
      const role = scalarText(get(it.value, "role"));
      const persona = scalarText(get(it.value, "persona"));
      if (!persona || (!node && !role)) continue;
      const intensity = scalarText(get(it.value, "intensity"));
      out.assignments.push({
        by: node ? "node" : "role",
        target: node ?? role!,
        persona,
        intensity: intensity !== null && Number.isFinite(Number(intensity)) ? Number(intensity) : null,
        schedule: scalarText(get(it.value, "schedule")),
        seed: scalarText(get(it.value, "seed")),
      });
    }
  }
  return out;
}

/** The persona in effect for a node group: its own binding first, then its role's. */
export function effectiveAssignment(tfc: TfcModel | null, node: string, role: string | null): TfcAssignment | null {
  if (!tfc) return null;
  return tfc.assignments.find((a) => a.by === "node" && a.target === node) ?? (role ? (tfc.assignments.find((a) => a.by === "role" && a.target === role) ?? null) : null);
}

/** A fresh profile for a twin, with no assignments yet. */
export function emptyTfc(twinId: string): string {
  return `# Traffic profile: ${twinId} / default (written by the design canvas)\ntraffic-profile:\n  target: ${encodeScalar(twinId)}\n  assignments: []\n`;
}

export interface AssignmentPatch {
  persona?: string;
  intensity?: number | null;
  schedule?: string | null;
}

/** Set a node group's (or a role's) binding: the persona, and optionally its
 *  intensity and schedule (`null` removes the key). A binding that does not
 *  exist is appended; `persona` is then required. */
export function setAssignment(text: string, by: "node" | "role", target: string, patch: AssignmentPatch): string {
  const doc = parseYaml(text);
  const lines = [...doc.lines];
  const tp = get(doc.root, "traffic-profile");
  if (!tp || tp.kind !== "map") throw new Error("the traffic profile has no `traffic-profile:` mapping");
  const asEntry = entry(tp, "assignments");
  let seq: YSeq | null = asEntry && asEntry.value.kind === "seq" && !asEntry.value.flow ? asEntry.value : null;
  const found = seq?.items.find((it) => scalarText(get(it.value, by)) === target) ?? null;
  if (found && found.value.kind === "map") {
    const m = found.value;
    const set = (key: string, value: string | number | null | undefined) => {
      if (value === undefined) return;
      const e = entry(m, key);
      const raw = value === null ? null : typeof value === "number" ? String(value) : encodeScalar(value);
      if (e) {
        if (raw === null) removeSpan(lines, e.start, e.end, m.indent);
        else replaceValueOnLine(lines, e, raw);
      } else if (raw !== null) insertLines(lines, contentEnd(lines, found.end), [`${" ".repeat(m.indent)}${key}: ${raw}`]);
    };
    // Later keys first so earlier line numbers stay valid.
    set("schedule", patch.schedule);
    set("intensity", patch.intensity);
    set("persona", patch.persona);
    return joinLines(lines, doc.trailingNewline);
  }
  if (!patch.persona) throw new Error(`no binding for ${by} '${target}' to change; choose a persona first`);
  let at: number;
  let dashIndent: number;
  if (seq && seq.items.length) {
    at = contentEnd(lines, seq.end);
    dashIndent = seq.indent;
  } else if (asEntry) {
    const raw = lines[asEntry.line]!;
    lines[asEntry.line] = raw.slice(0, raw.indexOf("assignments") + "assignments:".length);
    at = asEntry.line + 1;
    dashIndent = tp.indent + 2;
    seq = null;
  } else {
    at = contentEnd(lines, tp.end);
    insertLines(lines, at, [`${" ".repeat(tp.indent)}assignments:`]);
    at++;
    dashIndent = tp.indent + 2;
  }
  const pad = " ".repeat(dashIndent + 2);
  const item = [`${" ".repeat(dashIndent)}- ${by}: ${encodeScalar(target)}`, `${pad}persona: ${encodeScalar(patch.persona)}`];
  if (typeof patch.intensity === "number") item.push(`${pad}intensity: ${patch.intensity}`);
  if (patch.schedule) item.push(`${pad}schedule: ${encodeScalar(patch.schedule)}`);
  insertLines(lines, at, item);
  return joinLines(lines, doc.trailingNewline);
}

export function removeAssignment(text: string, by: "node" | "role", target: string): string {
  const doc = parseYaml(text);
  const lines = [...doc.lines];
  const as = get(doc.root, "traffic-profile", "assignments");
  if (!as || as.kind !== "seq") return text;
  const found = as.items.find((it) => scalarText(get(it.value, by)) === target);
  if (!found) return text;
  removeSpan(lines, found.start, found.end, as.indent);
  return joinLines(lines, doc.trailingNewline);
}

/** Rename every `node:` binding of a renamed node group. */
export function renameNodeBindings(text: string, from: string, to: string): string {
  const doc = parseYaml(text);
  const lines = [...doc.lines];
  const as = get(doc.root, "traffic-profile", "assignments");
  if (!as || as.kind !== "seq") return text;
  let changed = false;
  for (const it of as.items) {
    if (it.value.kind !== "map") continue;
    const e = entry(it.value as YMap, "node");
    if (e && scalarText(e.value) === from) {
      replaceValueOnLine(lines, e, encodeScalar(to));
      changed = true;
    }
  }
  return changed ? joinLines(lines, doc.trailingNewline) : text;
}

// ---------------------------------------------------------------------------
// trafficgen blocks (`budget`, `emulated`, `lines`): each block is rewritten
// whole from the model, one flow map per item (the shape the gate profile and
// the SPEC use); every other line of the profile is kept as it was.
// ---------------------------------------------------------------------------
type TgKey = "budget" | "emulated" | "lines";

function flow(pairs: [string, string | number | boolean | null][]): string {
  return `{ ${pairs.filter(([, v]) => v !== null).map(([k, v]) => `${k}: ${typeof v === "string" ? encodeScalar(v) : String(v)}`).join(", ")} }`;
}

function blockLines(key: TgKey, model: Pick<TfcModel, TgKey>, indent: number): string[] | null {
  const pad = " ".repeat(indent);
  if (key === "budget") return model.budget ? [`${pad}budget: ${flow([["cps", model.budget.cps], ["mbps", model.budget.mbps]])}`] : null;
  if (key === "emulated") {
    if (!model.emulated.length) return null;
    return [`${pad}emulated:`, ...model.emulated.map((e) => `${pad}  - ${flow(e.external
      ? [["group", e.group], ["external", true], ["segment", e.segment], ["role", e.role], ["count", e.count], ["start", e.start]]
      : [["group", e.group], ["count", e.count], ["start", e.start]])}`)];
  }
  if (!model.lines.length) return null;
  return [`${pad}lines:`, ...model.lines.map((l) => `${pad}  - ${flow([["id", l.id], ["from", l.from], ["to", l.to], ["mix", l.mix], ["rate_cps", l.rate_cps], ["max_concurrent", l.max_concurrent]])}`)];
}

function writeBlock(text: string, key: TgKey, model: Pick<TfcModel, TgKey>): string {
  const doc = parseYaml(text);
  const lines = [...doc.lines];
  const tp = get(doc.root, "traffic-profile");
  if (!tp || tp.kind !== "map" || tp.flow) throw new Error("the traffic profile has no `traffic-profile:` mapping");
  const e = entry(tp, key);
  const next = blockLines(key, model, tp.indent);
  if (e) {
    const end = Math.max(contentEnd(lines, e.end), e.start + 1);
    lines.splice(e.start, end - e.start, ...(next ?? []));
  } else if (next) {
    insertLines(lines, contentEnd(lines, tp.end), next);
  } else return text;
  return joinLines(lines, doc.trailingNewline);
}

/** Write the profile's `budget` / `emulated` / `lines` to `model` (an empty list or null removes the block). */
export function setTrafficgen(text: string, model: Pick<TfcModel, TgKey>): string {
  const current = readTfc(text);
  let out = text;
  const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);
  for (const key of ["budget", "emulated", "lines"] as const) {
    if (!same(current[key], model[key])) out = writeBlock(out, key, model);
  }
  return out;
}
