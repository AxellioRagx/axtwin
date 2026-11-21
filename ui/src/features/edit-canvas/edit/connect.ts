/**
 * Connect by dragging (`SPEC.md` "Connect gestures", B1/B3): what a handle drag between two things
 * on the canvas MAKES, as pure commands over the working document -- one undo step each, a batch
 * (chain / ring / mesh / hub) one step for all of it.
 *
 * - router -> router: a TRANSIT segment `<a>--<b>`, the two names sorted (de-duped `-2`, `-3`), the next free `/30` of the
 *   transit pool (`allocate.ts` `TRANSIT_POOL`), `zone_type: vlan` as the catalog's transits use,
 *   and a leg for each router pinned at the `/30`'s `.1` and `.2`. Two routers already sharing a
 *   segment are already linked: refused with the reason.
 * - router -> empty pane: a new LAN segment (the next free `/24` in the router's site, as the
 *   palette's Segment does) with the router's leg on it.
 * - router / host -> segment: the attach the canvas already has (`document.ts` `attach`).
 * - router (or L3 switch) <-> switch (A5, 2026-11-21): the router becomes the GATEWAY of every segment
 *   the switch carries -- an addressed leg on each (`.1` for the first router, `.2` for the second),
 *   wired by one single-segment link per segment (`subtree.ts` `gatewayOnSwitch`). It had been a bare
 *   trunk with no leg: the router was not the LAN's gateway and the engine refused the link.
 * - switch <-> switch / host: a `links[]` entry (`subtree.ts` `attachDevices`), unchanged.
 */
import { lanSitePrefix, nextFreeLan, nextFreeTransit, transitEnds, TRANSIT_POOL } from "./allocate";
import type { Files } from "./commands";
import { addSegment, attach, readDoc, setNodeField, setSegmentField, type DocModel, type WorkingDoc } from "./document";
import { attachDevices, gatewayOnSwitch, gatewayPlan, wiredAsGateway } from "./subtree";
import { addSegment as teAddSegment, setNodeNets, type TeModel } from "./te";

/** The zone_type a transit takes (the catalog's one transit, datacenter's `fab-link`, is `vlan`). */
export const TRANSIT_ZONE = "vlan";

export type ConnectEnd = { kind: "router" | "switch" | "host"; name: string } | { kind: "segment"; name: string } | { kind: "pane" };

/** What a drag from `from` to `to` would make, for the live hint and the drop; `refused` says why not. */
export type ConnectIntent =
  | { kind: "transit"; a: string; b: string; cidr: string; name: string }
  | { kind: "lan"; router: string; cidr: string; name: string }
  | { kind: "attach"; node: string; segment: string }
  | { kind: "link"; a: string; b: string }
  | { kind: "gateway"; router: string; switch: string; segments: string[] }
  | { kind: "refused"; reason: string };

/** Two routers already share a segment (a transit or a LAN): they are linked. */
export function routersLinked(te: Pick<TeModel, "nodes">, a: string, b: string): boolean {
  const na = te.nodes.find((n) => n.name === a);
  const nb = te.nodes.find((n) => n.name === b);
  return Boolean(na && nb && na.nets.some((l) => nb.nets.some((m) => m.segment === l.segment)));
}

function freeName(te: Pick<TeModel, "segments">, base: string): string {
  const taken = new Set(te.segments.map((s) => s.name));
  if (!taken.has(base)) return base;
  let n = 2;
  while (taken.has(`${base}-${n}`)) n++;
  return `${base}-${n}`;
}

/** The new LAN a router dropped on the pane gets: the next free /24 in its site (else the twin's first, else 10.200). */
function nextLan(te: TeModel, router: string): { cidr: string; name: string } | null {
  const inPlay = te.segments.map((s) => s.cidr);
  const own = te.nodes.find((n) => n.name === router)?.nets.map((l) => te.segments.find((s) => s.name === l.segment)?.cidr) ?? [];
  const cidr = nextFreeLan(lanSitePrefix([...own, ...inPlay]), inPlay);
  if (!cidr) return null;
  let n = te.segments.length + 1;
  const taken = new Set(te.segments.map((s) => s.name));
  while (taken.has(`segment-${n}`)) n++;
  return { cidr, name: `segment-${n}` };
}

export function connectIntent(te: TeModel, from: ConnectEnd, to: ConnectEnd, pool: string = TRANSIT_POOL): ConnectIntent {
  if (from.kind === "pane" || from.kind === "segment") return { kind: "refused", reason: "drag from a router, a switch or a host" };
  if (to.kind === "pane") {
    if (from.kind !== "router") return { kind: "refused", reason: `drop ${from.name} on a segment or a device` };
    const lan = nextLan(te, from.name);
    return lan ? { kind: "lan", router: from.name, ...lan } : { kind: "refused", reason: "no free /24 left for a new segment" };
  }
  if (to.kind === "segment") {
    if (from.kind === "switch") return { kind: "refused", reason: `${from.name} is a switch: link it to a device instead` };
    const n = te.nodes.find((x) => x.name === from.name);
    if (n?.nets.some((l) => l.segment === to.name)) return { kind: "refused", reason: `${from.name} is already on ${to.name}` };
    return { kind: "attach", node: from.name, segment: to.name };
  }
  if (from.name === to.name) return { kind: "refused", reason: "drop it on another device" };
  if (from.kind === "router" && to.kind === "router") {
    if (routersLinked(te, from.name, to.name)) return { kind: "refused", reason: `${from.name} and ${to.name} are already linked (they share a segment)` };
    const cidr = nextFreeTransit(te.segments.map((s) => s.cidr), pool);
    if (!cidr) return { kind: "refused", reason: `the transit pool ${pool} is full` };
    // The name sorts its two routers, so the drag's direction does not show in it (2026-11-20).
    const [x, y] = [from.name, to.name].sort();
    return { kind: "transit", a: from.name, b: to.name, cidr, name: freeName(te, `${x}--${y}`) };
  }
  if ((from.kind === "router" && to.kind === "switch") || (from.kind === "switch" && to.kind === "router")) {
    const [router, sw] = from.kind === "router" ? [from.name, to.name] : [to.name, from.name];
    const plan = gatewayPlan(te, router, sw);
    if (!plan.carried.length) return { kind: "refused", reason: `${sw} carries no segment yet: drop a host on it first` };
    if (!plan.legs.length && wiredAsGateway(plan)) return { kind: "refused", reason: `${router} is already the gateway on ${plan.carried.join(", ")}` };
    return { kind: "gateway", router, switch: sw, segments: plan.carried };
  }
  if (from.kind === "switch" || to.kind === "switch") return { kind: "link", a: from.name, b: to.name };
  if (from.kind === "host" && to.kind === "host") return { kind: "refused", reason: "two hosts join through a segment or a switch" };
  // A host and a router: the host onto one of the router's segments is an attach on the canvas; here, refuse with the way.
  return { kind: "refused", reason: `drop ${from.kind === "host" ? from.name : to.name} on a segment (or a switch) to attach it` };
}

/** The intent's words for the live hint. */
export function intentHint(i: ConnectIntent): string {
  switch (i.kind) {
    case "transit":
      return `new transit ${i.name} ${i.cidr}`;
    case "lan":
      return `new segment ${i.name} ${i.cidr} with ${i.router}'s leg`;
    case "attach":
      return `attach ${i.node} to ${i.segment}`;
    case "link":
      return `link ${i.a} — ${i.b}`;
    case "gateway":
      return `${i.router} becomes the gateway of ${i.segments.join(", ")} (${i.switch})`;
    default:
      return i.reason;
  }
}

/** A transit between two routers: the /30 segment and a leg for each at its .1 and .2. */
function addTransit(doc: WorkingDoc, model: DocModel, a: string, b: string, opts: { pool?: string; ospfArea?: number | null } = {}): { files: Files; segment: string; cidr: string } {
  const intent = connectIntent(model.te, { kind: "router", name: a }, { kind: "router", name: b }, opts.pool);
  if (intent.kind === "refused") throw new Error(intent.reason);
  if (intent.kind !== "transit") throw new Error(`${a} and ${b} are not two routers`);
  const ends = transitEnds(intent.cidr)!;
  let text = doc.files[doc.teFile];
  if (text === undefined) throw new Error(`the document has no '${doc.teFile}' to edit`);
  text = teAddSegment(text, { name: intent.name, cidr: intent.cidr, zoneType: TRANSIT_ZONE, ospfArea: opts.ospfArea ?? null });
  for (const [router, ip] of [[a, ends[0]], [b, ends[1]]] as const) {
    const n = model.te.nodes.find((x) => x.name === router)!;
    text = setNodeNets(text, router, [...n.nets, { segment: intent.name, ip }]);
  }
  return { files: { ...doc.files, [doc.teFile]: text }, segment: intent.name, cidr: intent.cidr };
}

/** router -> router (one undo step). */
export function connectRouters(doc: WorkingDoc, model: DocModel, a: string, b: string, opts: { pool?: string; ospfArea?: number | null } = {}): { files: Files; segment: string; cidr: string } {
  for (const r of [a, b]) if (model.te.nodes.find((n) => n.name === r)?.kind !== "router") throw new Error(`'${r}' is not a router`);
  return addTransit(doc, model, a, b, opts);
}

/** router -> pane: a new LAN segment with the router's leg on it. */
export function routerToNewLan(doc: WorkingDoc, model: DocModel, router: string): { files: Files; segment: string } {
  const intent = connectIntent(model.te, { kind: "router", name: router }, { kind: "pane" });
  if (intent.kind !== "lan") throw new Error(intent.kind === "refused" ? intent.reason : "not a router");
  const withSeg = addSegment(doc, { name: intent.name, cidr: intent.cidr, zoneType: "vlan" });
  const d2 = { ...doc, files: withSeg };
  return { files: attach(d2, readDoc(d2), router, intent.name), segment: intent.name };
}

/** Apply a drag's intent to the document (attach / link through the commands the canvas already has). */
export function applyIntent(doc: WorkingDoc, model: DocModel, i: ConnectIntent): Files {
  switch (i.kind) {
    case "transit":
      return connectRouters(doc, model, i.a, i.b).files;
    case "lan":
      return routerToNewLan(doc, model, i.router).files;
    case "attach":
      return attach(doc, model, i.node, i.segment);
    case "link":
      return attachDevices(doc, model, i.a, i.b);
    case "gateway":
      return gatewayOnSwitch(doc, model, i.router, i.switch);
    default:
      throw new Error(i.reason);
  }
}

export type WireShape = "chain" | "ring" | "mesh" | "hub";

/** The image profile OSPF needs (`catalog/image-profiles.yaml`): the planner passes `ospf_area` to
 *  the VyOS leg config, so only a router on this image runs it. */
export const OSPF_PROFILE = "router-ospf";

/** The routers of `routers` not on the OSPF-capable image, with the image each runs (2026-11-20, round 2). */
export function ospfImageGaps(te: Pick<TeModel, "nodes">, routers: readonly string[], ospfImage: string): Array<{ router: string; image: string }> {
  return routers.flatMap((r) => {
    const n = te.nodes.find((x) => x.name === r);
    return n && n.image !== ospfImage ? [{ router: r, image: n.image || "no image" }] : [];
  });
}

/** The router pairs a bulk wiring makes, in order (the first router is the hub). */
export function wirePairs(routers: readonly string[], shape: WireShape): Array<[string, string]> {
  const out: Array<[string, string]> = [];
  if (routers.length < 2) return out;
  if (shape === "chain" || shape === "ring") {
    for (let i = 0; i + 1 < routers.length; i++) out.push([routers[i]!, routers[i + 1]!]);
    if (shape === "ring" && routers.length > 2) out.push([routers[routers.length - 1]!, routers[0]!]);
  } else if (shape === "mesh") {
    for (let i = 0; i < routers.length; i++) for (let j = i + 1; j < routers.length; j++) out.push([routers[i]!, routers[j]!]);
  } else for (const r of routers.slice(1)) out.push([routers[0]!, r]);
  return out;
}

/** The transit two routers already have: a segment both have a leg on and nothing else does (the
 *  router-to-router link a drag or the wiring dialog made), or null. */
export function transitBetween(te: Pick<TeModel, "nodes">, a: string, b: string): string | null {
  const na = te.nodes.find((n) => n.name === a);
  const nb = te.nodes.find((n) => n.name === b);
  if (!na || !nb) return null;
  for (const l of na.nets) {
    if (!nb.nets.some((m) => m.segment === l.segment)) continue;
    const on = te.nodes.filter((n) => n.nets.some((m) => m.segment === l.segment));
    if (on.length === 2) return l.segment;
  }
  return null;
}

/** One pair of a bulk wiring, for the dialog's preview: a transit to make (its real, sorted `a--b`
 *  name), or the pair already linked (its transit, when it has one; else the segment they share). */
export type WirePlanRow = { a: string; b: string; kind: "new"; name: string } | { a: string; b: string; kind: "linked"; segment: string | null };

export function wirePlan(te: Pick<TeModel, "nodes" | "segments">, routers: readonly string[], shape: WireShape): WirePlanRow[] {
  const names = new Set(te.segments.map((s) => s.name));
  return wirePairs(routers, shape).map(([a, b]) => {
    if (routersLinked(te, a, b)) {
      const shared = transitBetween(te, a, b) ?? te.nodes.find((n) => n.name === a)!.nets.find((l) => te.nodes.find((n) => n.name === b)!.nets.some((m) => m.segment === l.segment))?.segment ?? null;
      return { a, b, kind: "linked", segment: shared };
    }
    const [x, y] = [a, b].sort();
    let name = `${x}--${y}`;
    for (let n = 2; names.has(name); n++) name = `${x}--${y}-${n}`;
    names.add(name);
    return { a, b, kind: "new", name };
  });
}

/** A plan row's words: "new: a--b" or "already linked: <transit>". */
export function wirePlanText(r: WirePlanRow): string {
  return r.kind === "new" ? `new: ${r.name}` : `already linked: ${r.segment ?? `${r.a} and ${r.b}`}`;
}

/** Bulk wiring (B3): every pair of the shape gets a transit, as ONE edit; a pair already linked is
 *  left as it is (and named). With `ospfArea`, the transits the pairs already have take it too
 *  (2026-11-21), so OSPF can be turned on for links already drawn; a wiring that makes nothing and
 *  sets nothing is a refusal. */
export function wireRouters(doc: WorkingDoc, model: DocModel, routers: readonly string[], shape: WireShape, opts: { pool?: string; ospfArea?: number | null; image?: { to: string; routers: readonly string[] } | null } = {}): { files: Files; made: string[]; skipped: string[]; ospf: string[] } {
  if (routers.length < 2) throw new Error("select two or more routers");
  for (const r of routers) if (model.te.nodes.find((n) => n.name === r)?.kind !== "router") throw new Error(`'${r}' is not a router`);
  let cur: WorkingDoc = doc;
  let m = model;
  const made: string[] = [];
  const skipped: string[] = [];
  const ospf: string[] = [];
  for (const [a, b] of wirePairs(routers, shape)) {
    if (routersLinked(m.te, a, b)) {
      const t = transitBetween(m.te, a, b);
      skipped.push(t ?? `${a} and ${b}`);
      if (t && typeof opts.ospfArea === "number" && m.te.segments.find((s) => s.name === t)?.ospfArea !== opts.ospfArea) {
        cur = { ...cur, files: setSegmentField(cur, t, "ospf_area", opts.ospfArea) };
        m = readDoc(cur);
        ospf.push(t);
      }
      continue;
    }
    const r = addTransit(cur, m, a, b, opts);
    made.push(r.segment);
    if (typeof opts.ospfArea === "number") ospf.push(r.segment);
    cur = { ...cur, files: r.files };
    m = readDoc(cur);
  }
  // "switch these routers to <the OSPF image>": in the same edit, so one undo takes it all back.
  const switched = opts.image?.routers ?? [];
  if (!made.length && !ospf.length && !switched.length) throw new Error(`all pairs already linked (${skipped.join(", ")}); nothing to change`);
  for (const r of switched) cur = { ...cur, files: setNodeField(cur, r, "image", opts.image!.to) };
  return { files: cur.files, made, skipped, ospf };
}
