/**
 * The canvas's own structural check of the working twin config, run on every
 * mutation so an edit that cannot be right is blocked in place with a reason
 * (`ui/Spec.md` C3.4). It mirrors the engine's loader rules the canvas can
 * know without the image manifests -- names, CIDRs, attachments, counts,
 * pinned addresses -- and is NEVER the last word: the API validates the
 * document through the validator proper on every `PUT` of a working copy
 * and on Save / Save-As, and its refusal is shown verbatim. Pure.
 */
import type { ValidationFinding } from "../../../api/types";
import { isIpv4 } from "../model/keys";
import { addressInSegment, isNetworkAddress } from "./allocate";
import type { TeModel } from "./te";

const NAME = /^[A-Za-z0-9][A-Za-z0-9._-]*$/;

export function validateTe(model: TeModel): ValidationFinding[] {
  const out: ValidationFinding[] = [];
  const err = (rule: string, message: string, location?: string) => out.push({ rule_id: rule, severity: "error", message, location: location ?? null });
  const warn = (rule: string, message: string, location?: string) => out.push({ rule_id: rule, severity: "warning", message, location: location ?? null });
  for (const p of model.problems) err("CV-000", p, "te.yaml");
  if (!model.id) err("CV-001", "the twin config has no id", "twin_config.id");
  const segNames = new Set<string>();
  for (const s of model.segments) {
    const loc = `twin_config.segments[${s.name}]`;
    if (!NAME.test(s.name)) err("CV-010", `segment name '${s.name}' is not a name (letters, digits, '.', '_', '-')`, loc);
    if (segNames.has(s.name)) err("CV-011", `two segments are named '${s.name}'`, loc);
    segNames.add(s.name);
    if (!isNetworkAddress(s.cidr)) err("CV-012", `segment '${s.name}': '${s.cidr}' is not an IPv4 network address in CIDR form (10.1.2.0/24)`, loc);
  }
  const nodeNames = new Set<string>();
  const pinned = new Map<string, string>();
  for (const n of model.nodes) {
    const loc = `twin_config.nodes[${n.name}]`;
    if (!NAME.test(n.name)) err("CV-020", `node name '${n.name}' is not a name (letters, digits, '.', '_', '-')`, loc);
    if (nodeNames.has(n.name)) err("CV-021", `two node groups are named '${n.name}'`, loc);
    nodeNames.add(n.name);
    if (!n.image.trim()) err("CV-022", `node '${n.name}' names no image`, `${loc}.image`);
    if (typeof n.count === "number" ? !(Number.isInteger(n.count) && n.count >= 1) : !/^\$\{[A-Za-z_][A-Za-z0-9_]*(:\d+)?\}$/.test(n.count)) {
      err("CV-023", `node '${n.name}': count must be a whole number of 1 or more, or a \${PARAM:default} reference, got ${JSON.stringify(n.count)}`, `${loc}.count`);
    }
    // A warning, not an error: a router is placed first and attached after
    // (every edit must leave the document free of errors, so an error here
    // would make a router impossible to place at all).
    if (n.nets.length === 0 && n.kind === "router") warn("CV-024", `router '${n.name}' joins no segment yet: attach it to each segment it routes`, `${loc}.nets`);
    const seen = new Set<string>();
    for (const leg of n.nets) {
      if (!segNames.has(leg.segment)) err("CV-025", `node '${n.name}' attaches to '${leg.segment}', which is not a segment of this twin`, `${loc}.net`);
      if (seen.has(leg.segment)) err("CV-026", `node '${n.name}' attaches to segment '${leg.segment}' twice`, `${loc}.nets`);
      seen.add(leg.segment);
      if (leg.ip) {
        const seg = model.segments.find((s) => s.name === leg.segment);
        if (!isIpv4(leg.ip)) err("CV-027", `node '${n.name}': '${leg.ip}' is not an IPv4 address`, `${loc}.nets`);
        else if (seg && isNetworkAddress(seg.cidr) && !addressInSegment(leg.ip, seg.cidr)) err("CV-028", `node '${n.name}': ${leg.ip} is not a host address inside ${seg.name} (${seg.cidr})`, `${loc}.nets`);
        const key = `${leg.segment}|${leg.ip}`;
        const holder = pinned.get(key);
        if (holder) err("CV-029", `node '${n.name}' and '${holder}' both pin ${leg.ip} on ${leg.segment}`, `${loc}.nets`);
        pinned.set(key, n.name);
        if (n.countValue > 1 && typeof n.count === "number") err("CV-030", `node '${n.name}' pins ${leg.ip} but fans out to ${n.count} instances; a pinned address needs a count of 1`, `${loc}.nets`);
      }
    }
  }
  return out;
}

export function firstReason(findings: readonly ValidationFinding[]): string | null {
  const e = findings.find((f) => f.severity === "error");
  return e ? `${e.rule_id}: ${e.message}` : null;
}
