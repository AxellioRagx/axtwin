/**
 * Check (on demand, `SPEC.md` "The edit model"): nothing validates while the
 * operator edits. A Check runs `POST /api/validate` on the files on screen and
 * is recorded with exactly those files; the exits that overwrite a twin
 * (Save, Persist, Apply, Replace origin, Save As) open only while that record
 * is a pass AND the files are still the ones it judged. Pure.
 *
 * What a Check reports is the API's answer plus what the canvas knows the API
 * cannot see: its own structural rules (`validate.ts`), the switches parked
 * away from every router (R3-2: they used to hold every edit), and an
 * untitled design with no segment.
 */
import type { ValidateResult, ValidationFinding } from "../../../api/types";
import { sameFiles, type Files } from "./commands";
import type { DocModel } from "./document";
import { nodeOfEnd } from "./te";

/** The last Check: the files it ran on and the merged answer (or the error that stopped it). */
export interface CheckRecord {
  files: Files;
  result: ValidateResult | null;
  error: unknown;
}

export type CheckStatus = "unchecked" | "valid" | "invalid" | "changed";

/** The Check's state for the files on screen. */
export function checkStatus(check: CheckRecord | null, files: Files | null): CheckStatus {
  if (!check || !files) return "unchecked";
  if (!sameFiles(check.files, files)) return "changed";
  return check.result && !check.error && check.result.errors === 0 ? "valid" : "invalid";
}

/** True when the overwriting exits may run: a passing Check on exactly these files. */
export function checkPasses(check: CheckRecord | null, files: Files | null): boolean {
  return checkStatus(check, files) === "valid";
}

/** The badge word: `unchecked`, `valid`, `invalid (N)`, `changed since check`. */
export function checkBadge(check: CheckRecord | null, files: Files | null): string {
  const st = checkStatus(check, files);
  if (st === "changed") return "changed since check";
  if (st === "invalid") return `invalid (${check?.result ? check.result.errors : 1})`;
  return st;
}

export const PARKED_RULE = "CV-PARK";
export const EMPTY_RULE = "CV-EMPTY";

/** The canvas's own findings for a Check: its structural rules, each parked switch, an empty untitled design. */
export function localFindings(model: DocModel, parked: readonly string[], emptyReason: string | null): ValidationFinding[] {
  const out = [...model.findings];
  for (const name of parked) out.push({ rule_id: PARKED_RULE, severity: "error", message: `${name} is parked, not attached: drop it on a switch or router, or wire it with the attach tool`, location: `twin_config.nodes[${name}]` });
  if (emptyReason) out.push({ rule_id: EMPTY_RULE, severity: "error", message: emptyReason, location: "twin_config.segments" });
  return out;
}

/** The API's answer with the canvas's findings folded in (one finding each: the fake and the
 *  engine repeat some of the canvas's rules), counts recomputed. */
export function mergeFindings(result: ValidateResult, local: readonly ValidationFinding[]): ValidateResult {
  const findings = [...local, ...result.findings.filter((f) => !local.some((l) => l.rule_id === f.rule_id && l.message === f.message))];
  const errors = findings.filter((f) => f.severity === "error").length;
  const warnings = findings.filter((f) => f.severity === "warning").length;
  return { ...result, findings, errors, warnings, disposition: errors ? "FAIL" : result.disposition === "FAIL" ? "PASS" : result.disposition, exit_code: errors ? 1 : result.exit_code };
}

/** A save refused with 422 (the server re-validates on every write): its findings as a failing Check on those files. */
export function refusalAsCheck(e: unknown, files: Files, local: readonly ValidationFinding[] = []): CheckRecord | null {
  const ex = e as { status?: number; detail?: unknown; message?: string } | null;
  if (!ex || ex.status !== 422) return null;
  const raw = ex.detail && typeof ex.detail === "object" ? (ex.detail as { findings?: unknown }).findings : null;
  const findings: ValidationFinding[] = Array.isArray(raw) && raw.length ? (raw as ValidationFinding[]) : [{ rule_id: "API-422", severity: "error", message: ex.message ?? "the API refused the document", location: null }];
  const result: ValidateResult = { disposition: "FAIL", exit_code: 1, passes_run: [], errors: 0, warnings: 0, findings: [] };
  return { files, result: mergeFindings({ ...result, findings }, local), error: null };
}

/** What a finding names on the canvas: a device (node group), a segment, a link or a tap. */
export type FindingTarget = { kind: "node"; name: string } | { kind: "segment"; name: string } | { kind: "link"; index: number } | { kind: "tap"; index: number };

/** The key a canvas element answers to (`nodes.tsx` reads its own the same way). */
export function targetKey(t: FindingTarget): string {
  return t.kind === "link" || t.kind === "tap" ? `${t.kind}:${t.index}` : `${t.kind}:${t.name}`;
}

const escape = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/** The element a finding is about, or null when it names none the canvas draws. The canvas's
 *  own rules say it in `location` (`twin_config.nodes[<name>]`, `links[<i>]`); the engine's
 *  locations index the EXPANDED IR, so its findings are matched by the names they quote
 *  (a member like `web01` resolves to its group), nodes before segments, longest name first. */
export function findingTarget(f: ValidationFinding, model: DocModel): FindingTarget | null {
  const loc = f.location ?? "";
  const named = /twin_config\.(nodes|segments)\[([^\]]+)\]/.exec(loc);
  if (named) {
    const name = named[2]!;
    if (named[1] === "nodes" && model.te.nodes.some((n) => n.name === name)) return { kind: "node", name };
    if (named[1] === "segments" && model.te.segments.some((s) => s.name === name)) return { kind: "segment", name };
  }
  const link = /twin_config\.links\[(\d+)\]/.exec(loc);
  if (link && model.te.links[Number(link[1])]) return { kind: "link", index: Number(link[1]) };
  const tapIdx = loc.startsWith("tap/") ? (/bindings\[(\d+)\]/.exec(loc)?.[1] ?? /binding (\d+)/.exec(f.message)?.[1]) : undefined;
  if (tapIdx !== undefined && model.tap?.bindings.some((b) => b.index === Number(tapIdx))) return { kind: "tap", index: Number(tapIdx) };
  const text = `${f.message} ${loc}`;
  const words = (name: string) => new RegExp(`(^|[^A-Za-z0-9._-])${escape(name)}([^A-Za-z0-9._-]|$)`).test(text);
  const nodes = [...model.te.nodes].sort((a, b) => b.name.length - a.name.length);
  for (const n of nodes) if (words(n.name)) return { kind: "node", name: n.name };
  // An instance name (`web01`) the expander made from a group.
  for (const m of text.matchAll(/[A-Za-z0-9][A-Za-z0-9._-]*\d{2,}/g)) {
    const hit = nodeOfEnd(model.te, m[0]);
    if (hit) return { kind: "node", name: hit.node.name };
  }
  const segs = [...model.te.segments].sort((a, b) => b.name.length - a.name.length);
  for (const s of segs) if (words(s.name) || (s.cidr && text.includes(s.cidr))) return { kind: "segment", name: s.name };
  return null;
}

/** One element's pin on the canvas: how many findings name it, the worst severity, the messages. */
export interface FindingPin {
  severity: "error" | "warning";
  messages: string[];
}

/** The pins for a Check's findings, keyed by `targetKey`. */
export function findingPins(findings: readonly ValidationFinding[], model: DocModel): Map<string, FindingPin> {
  const out = new Map<string, FindingPin>();
  for (const f of findings) {
    if (f.severity !== "error" && f.severity !== "warning") continue;
    const t = findingTarget(f, model);
    if (!t) continue;
    const key = targetKey(t);
    const pin = out.get(key) ?? { severity: "warning" as const, messages: [] };
    if (f.severity === "error") pin.severity = "error";
    pin.messages.push(`${f.rule_id}: ${f.message}`);
    out.set(key, pin);
  }
  return out;
}
