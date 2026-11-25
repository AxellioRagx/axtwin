/**
 * Emulated instances and traffic lines (design `docs/design/traffic-generation-trex-astf.md`
 * §4.1, contract `engine/src/axrange/trafficgen/SPEC.md`), as the selection panel derives
 * them: the instances split, a group's client / server badge, a line's E-W / N-S tag, the
 * planned generators, the budget line, and each row's reasons (the TGN rules the canvas can
 * see before a Check, plus the planner's one-generator-per-group limit).
 *
 * Nothing here writes; the writers are in `document.ts`. The engine is the authority: a Check
 * runs the same rules (and the planner) on the files.
 *
 * Pure -- no React, no DOM.
 */
import type { Persona } from "../../../api/types";
import { isAppMix } from "../add/catalog";
import type { DocModel } from "./document";
import { instanceId } from "./members";
import type { TeNode } from "./te";
import { lineId, type TfcAssignment, type TfcEmulated, type TfcLine, type TfcModel } from "./tfc";

/** The segment an emulated group's addresses come from: a te host group on exactly one segment,
 *  or an external group's `segment`. Null when neither (TGN-030). */
export function groupSegment(model: DocModel, group: string): string | null {
  const ext = model.tfc?.emulated.find((e) => e.group === group && e.external);
  if (ext) return ext.segment;
  const n = model.te.nodes.find((x) => x.name === group);
  if (!n || n.kind !== "host" || n.nets.length !== 1) return null;
  return n.nets[0]!.segment;
}

/** The role a line end plays against: a te group's role, an external group's `role`. */
export function groupRole(model: DocModel, group: string): string | null {
  const ext = model.tfc?.emulated.find((e) => e.group === group && e.external);
  if (ext) return ext.role;
  return model.te.nodes.find((x) => x.name === group)?.role ?? null;
}

export function emulatedCount(tfc: TfcModel | null, group: string): number {
  return tfc?.emulated.find((e) => e.group === group)?.count ?? 0;
}

export function externalGroups(tfc: TfcModel | null): TfcEmulated[] {
  return (tfc?.emulated ?? []).filter((e) => e.external);
}

/** Can a group carry emulated instances? Only a te host group on one segment (TGN-030). */
export function canEmulate(n: TeNode): boolean {
  return n.kind === "host" && n.nets.length === 1;
}

/** Client (sends on a line), server (is a line's `to`), both, or null (on no line). */
export function groupBadge(tfc: TfcModel | null, group: string): "client" | "server" | "both" | null {
  const lines = tfc?.lines ?? [];
  const c = lines.some((l) => l.from === group);
  const s = lines.some((l) => l.to === group);
  return c && s ? "both" : c ? "client" : s ? "server" : null;
}

/** North-south when either end is an external group on the internet segment, east-west otherwise
 *  (§5). An emulated-only group on an internal segment (the Emulated group tool) is east-west. */
export function lineTag(tfc: TfcModel | null, l: Pick<TfcLine, "from" | "to">): "N-S" | "E-W" {
  const ext = new Set(externalGroups(tfc).filter((e) => (e.segment ?? "internet") === "internet").map((e) => e.group));
  return ext.has(l.from) || ext.has(l.to) ? "N-S" : "E-W";
}

/** The generators the planner would make: one per distinct (client segment, server segment). */
export function plannedGenerators(model: DocModel): string[] {
  const out = new Set<string>();
  for (const l of model.tfc?.lines ?? []) {
    const cs = groupSegment(model, l.from), ss = groupSegment(model, l.to);
    if (cs && ss) out.add(`gen-${cs}--${ss}`);
  }
  return [...out];
}

/** The design health line's traffic part: planned cps against the budget, and the generator count. */
export function trafficHealth(model: DocModel): { cps: number; budget: number | null; mbps: number | null; generators: number; lines: number } | null {
  const t = model.tfc;
  if (!t || (!t.lines.length && !t.emulated.length)) return null;
  return {
    cps: t.lines.reduce((s, l) => s + l.rate_cps, 0),
    budget: t.budget?.cps ?? null,
    mbps: t.budget?.mbps ?? null,
    generators: plannedGenerators(model).length,
    lines: t.lines.length,
  };
}

/** The app mixes a line may pick: `drive: trex-astf` mixes whose `emits` name the server end's role. */
export function mixesFor(personas: readonly Persona[], serverRole: string | null): Persona[] {
  return personas.filter((p) => isAppMix(p) && (serverRole === null || p.emits.some((e) => e.peer_role === serverRole)));
}

export interface LineReason {
  rule: string;
  message: string;
}

/** Why line `index` would be refused, as far as the canvas can tell before a Check. */
export function lineReasons(model: DocModel, personas: readonly Persona[], index: number): LineReason[] {
  const t = model.tfc;
  const l = t?.lines[index];
  if (!t || !l) return [];
  const out: LineReason[] = [];
  const known = (g: string) => t.emulated.some((e) => e.group === g) || model.te.nodes.some((n) => n.name === g);
  for (const [end, g] of [["from", l.from], ["to", l.to]] as const) {
    if (!known(g)) out.push({ rule: "TGN-030", message: `${end} '${g}' is neither a node group nor an external group` });
  }
  const p = personas.find((x) => x.id === l.mix);
  if (personas.length && (!p || !isAppMix(p))) out.push({ rule: "TGN-030", message: `'${l.mix}' is not an app mix` });
  if (known(l.from) && emulatedCount(t, l.from) === 0) out.push({ rule: "TGN-010", message: `${l.from} has no emulated instances: TRex cannot send from a real VM's address` });
  if (known(l.to) && emulatedCount(t, l.to) === 0) out.push({ rule: "TGN-020", message: `${l.to} has no emulated instances: lines to a real server are refused in this phase` });
  const role = groupRole(model, l.to);
  if (p && isAppMix(p) && role && !p.emits.some((e) => e.peer_role === role)) out.push({ rule: "TGN-030", message: `${l.mix} emits to no ${role}` });
  for (const g of [l.from, l.to]) {
    const clash = generatorClash(model, g, index);
    if (clash) out.push({ rule: "PLAN", message: clash });
  }
  if (t.budget) {
    const cps = t.lines.reduce((s, x) => s + x.rate_cps, 0);
    if (cps > t.budget.cps) out.push({ rule: "TGN-040", message: `planned ${cps} cps exceeds the budget ${t.budget.cps}` });
  }
  return out;
}

/** The planner's limit (one generator answers a group's addresses): the reason line `index`
 *  puts `group` on a second generator, naming the line that put it on the first; or null. */
export function generatorClash(model: DocModel, group: string, index: number): string | null {
  const lines = model.tfc?.lines ?? [];
  const gen = (l: TfcLine) => {
    const cs = groupSegment(model, l.from), ss = groupSegment(model, l.to);
    return cs && ss ? `gen-${cs}--${ss}` : null;
  };
  const mine = gen(lines[index]!);
  if (!mine) return null;
  for (let i = 0; i < index; i++) {
    const o = lines[i]!;
    if (o.from !== group && o.to !== group) continue;
    const g = gen(o);
    if (g && g !== mine) return `${group} would need two generators: line '${lineId(o)}' puts it on ${g}, this line on ${mine}`;
  }
  return null;
}

// ---------------------------------------------------------------------------
// "No traffic" for real VMs (the real persona off)
// ---------------------------------------------------------------------------
/** The host ids a te group expands to: its name for one instance, `<name>01`.. otherwise. */
export function groupInstances(n: Pick<TeNode, "name" | "countValue">): string[] {
  if (n.countValue <= 1) return [n.name];
  return Array.from({ length: n.countValue }, (_, i) => instanceId(n.name, i + 1));
}

/** Every te host id with its effective role (a member's override, else its group's). */
export function hostRoles(model: DocModel): { host: string; group: string; role: string | null }[] {
  const out: { host: string; group: string; role: string | null }[] = [];
  for (const n of model.te.nodes) {
    const ids = groupInstances(n);
    ids.forEach((host, i) => {
      const m = n.members.find((x) => x.index === (ids.length === 1 ? 1 : i + 1));
      out.push({ host, group: n.name, role: m?.role ?? n.role });
    });
  }
  return out;
}

/** The assignments that put a persona on `host` (its own `node:` binding, its group's, its role's). */
export function bindingsOn(model: DocModel, host: string): TfcAssignment[] {
  const t = model.tfc;
  if (!t) return [];
  const h = hostRoles(model).find((x) => x.host === host);
  return t.assignments.filter((a) => (a.by === "node" ? a.target === host || (h && a.target === h.group) : h !== undefined && a.target === h.role));
}

/** True when no assignment drives any of `hosts` (the real VMs stay up, idle). */
export function hasNoTraffic(model: DocModel, hosts: readonly string[]): boolean {
  return hosts.every((h) => bindingsOn(model, h).length === 0);
}
