/**
 * The traffic profile (`tfc/<file>.yaml`, `traffic-profile.assignments[]`)
 * as the side panel reads and edits it: which persona a node group or a role
 * is bound to, with its intensity and schedule. Edits are line splices
 * (`yaml.ts`); a profile that does not exist yet is created, and the
 * manifest (`twin.yaml`) is wired to it, by `document.ts`.
 *
 * Pure -- no React, no DOM.
 */
import { contentEnd, encodeScalar, entry, get, insertLines, joinLines, parseYaml, removeSpan, replaceValueOnLine, scalarText, type YMap, type YSeq } from "./yaml";

export interface TfcAssignment {
  by: "node" | "role";
  target: string;
  persona: string;
  intensity: number | null;
  schedule: string | null;
  seed: string | null;
}

export interface TfcModel {
  target: string | null;
  assignments: TfcAssignment[];
}

export function readTfc(text: string): TfcModel {
  const doc = parseYaml(text);
  const tp = get(doc.root, "traffic-profile");
  const out: TfcModel = { target: scalarText(get(tp, "target")), assignments: [] };
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
