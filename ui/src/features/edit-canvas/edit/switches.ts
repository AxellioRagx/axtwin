/**
 * Segments drawn as switches, the edit side (`docs/design/topology-layers.md` §7.1, 2026-11-11):
 * where a host dropped on a switch or a router lands, and "Insert switch", which turns a
 * segment's implied switch into a real `kind: switch` node. The model is unchanged -- a
 * segment is still the L2 primitive a host's `net` names; these only choose WHICH segment
 * and write the ordinary twin-config edits for it. Pure -- no React.
 */
import { nextFreeSlash24 } from "./allocate";
import type { Files } from "./commands";
import { addLink, addSegment, attach, readDoc, type DocModel, type WorkingDoc } from "./document";
import { carriedBy, freePort } from "./subtree";
import { addNode, type TeModel } from "./te";
import { twoOctetPrefix } from "../model/keys";

/** The segment a host dropped on switch `sw` joins (§7.1 drop rules): the one segment it carries;
 *  of several, the native VLAN of its uplink trunk, else the most-populated one. `choices` are
 *  every segment it carries (the inspector's VLAN picker offers them). `segment` is null when the
 *  switch carries nothing (the drop has nowhere to go). */
export function switchSegment(te: TeModel, sw: string): { segment: string | null; choices: string[] } {
  const choices = carriedBy(te, sw).filter((s) => te.segments.some((x) => x.name === s));
  if (choices.length <= 1) return { segment: choices[0] ?? null, choices };
  // The uplink: an up trunk to a router first, else any up trunk, with a native it carries.
  const trunks = te.links.filter((l) => l.state === "up" && l.mode === "trunk" && (l.a.device === sw || l.b.device === sw) && l.native && choices.includes(l.native));
  const kindOf = (name: string) => te.nodes.find((n) => n.name === name)?.kind;
  const toRouter = trunks.find((l) => kindOf(l.a.device === sw ? l.b.device : l.a.device) === "router");
  const native = (toRouter ?? trunks[0])?.native;
  if (native) return { segment: native, choices };
  const population = (seg: string) => te.nodes.filter((n) => n.kind !== "router" && n.kind !== "switch" && n.nets[0]?.segment === seg).reduce((k, n) => k + n.countValue, 0);
  const busiest = [...choices].sort((a, b) => population(b) - population(a) || choices.indexOf(a) - choices.indexOf(b))[0]!;
  return { segment: busiest, choices };
}

/** The switch (real) a host's home segment hangs off, and what else that switch carries: the
 *  inspector's VLAN picker shows when it carries two or more. Null when no real switch carries it. */
export function hostSwitch(te: TeModel, host: string): { switch: string; segment: string; choices: string[] } | null {
  const n = te.nodes.find((x) => x.name === host);
  const seg = n?.nets[0]?.segment;
  if (!n || !seg || n.kind === "router" || n.kind === "switch") return null;
  const sw = te.nodes.find((x) => x.kind === "switch" && carriedBy(te, x.name).includes(seg));
  return sw ? { switch: sw.name, segment: seg, choices: carriedBy(te, sw.name).filter((s) => te.segments.some((x) => x.name === s)) } : null;
}

/** A name not yet taken: `base`, else `base-2`, `base-3`, ... */
function freeName(base: string, taken: ReadonlySet<string>): string {
  if (!taken.has(base)) return base;
  for (let i = 2; ; i++) if (!taken.has(`${base}-${i}`)) return `${base}-${i}`;
}

/** A new segment under router `router` (a host dropped on a router, §7.1): the next free /24 in the
 *  router's site (the two-octet prefix of its first leg; else of the twin's first segment; else
 *  10.200), named `segment-N`, `zone_type: vlan`, with the router's leg on it. It draws under a new
 *  implied switch. */
export function newSegmentOnRouter(doc: WorkingDoc, model: DocModel, router: string): { files: Files; segment: string } {
  const r = model.te.nodes.find((n) => n.name === router);
  if (!r || r.kind !== "router") throw new Error(`no router named '${router}'`);
  const cidrOf = (seg: string | undefined) => model.te.segments.find((s) => s.name === seg)?.cidr;
  const inPlay = model.te.segments.map((s) => s.cidr);
  const prefix = twoOctetPrefix(cidrOf(r.nets[0]?.segment) ?? "") ?? twoOctetPrefix(model.te.segments[0]?.cidr ?? "") ?? "10.200";
  const cidr = nextFreeSlash24(prefix, inPlay, { after: true }) ?? nextFreeSlash24("10.200", inPlay);
  if (!cidr) throw new Error(`no free /24 under ${prefix} for a new segment on ${router}`);
  const taken = new Set(model.te.segments.map((s) => s.name));
  let n = model.te.segments.length + 1;
  while (taken.has(`segment-${n}`)) n++;
  const segment = `segment-${n}`;
  const withSeg: WorkingDoc = { ...doc, files: addSegment(doc, { name: segment, cidr, zoneType: "vlan" }) };
  return { files: attach(withSeg, readDoc(withSeg), router, segment), segment };
}

/** "Insert switch" on segment `segment`'s implied switch (§7.1): a `kind: switch` node, imageless
 *  and `source: authored`; a trunk from the router with a leg on the segment to it (native and
 *  only VLAN the segment); an access link from it to each single-instance host whose first leg
 *  is the segment. A `count > 1` group cannot be named by a link (the twin config refuses it,
 *  §5); it stays on its `net:` and still draws under the switch, which now carries its segment.
 *  The implied switch is gone: the real one carries the segment. One command, one undo step. */
export function insertSwitch(doc: WorkingDoc, model: DocModel, segment: string): { files: Files; name: string; skipped: string[] } {
  const te = model.te;
  if (!te.segments.some((s) => s.name === segment)) throw new Error(`no segment named '${segment}'`);
  const carrier = te.nodes.find((n) => n.kind === "switch" && carriedBy(te, n.name).includes(segment));
  if (carrier) throw new Error(`${segment} is already carried by switch ${carrier.name}`);
  const router = te.nodes.find((n) => n.kind === "router" && n.nets.some((l) => l.segment === segment));
  const onIt = te.nodes.filter((n) => n.kind !== "router" && n.kind !== "switch" && n.nets[0]?.segment === segment);
  const singles = onIt.filter((n) => n.countValue === 1 && typeof n.count === "number");
  const skipped = onIt.filter((n) => !singles.includes(n)).map((n) => n.name);
  if (!router && singles.length === 0) throw new Error(`nothing to wire a switch on ${segment} to: no router has a leg on it and no single host is on it`);
  const name = freeName(`sw-${segment}`, new Set(te.nodes.map((n) => n.name)));
  let cur: WorkingDoc = doc;
  const text = cur.files[cur.teFile];
  if (text === undefined) throw new Error(`the document has no '${cur.teFile}' to edit`);
  cur = { ...cur, files: { ...cur.files, [cur.teFile]: addNode(text, { name, kind: "switch", role: "l2-switch", image: "", nets: [], source: "authored" }) } };
  const link = (a: { device: string }, b: { device: string }, mode: "trunk" | "access") => {
    const m = readDoc(cur).te;
    const files = addLink(cur, { a: { device: a.device, port: freePort(m, a.device) }, b: { device: b.device, port: freePort(m, b.device) }, mode, native: mode === "trunk" ? segment : null, vlans: [segment], source: "authored" });
    cur = { ...cur, files };
  };
  if (router) link({ device: router.name }, { device: name }, "trunk");
  for (const h of singles) link({ device: name }, { device: h.name }, "access");
  return { files: cur.files, name, skipped };
}
