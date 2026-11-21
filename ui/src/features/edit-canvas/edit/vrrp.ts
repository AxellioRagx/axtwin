/**
 * First-hop redundancy on a LAN (design D5, `docs/design/canvas-add-panel.md` §8, Authority: user,
 * 2026-11-21): a segment's `vrrp:` block -- a VIP its hosts use as their gateway, floated by VRRP
 * between the routers (L3 switches) that have a leg on it. The engine expands the VIP into the
 * network's gateway (reserved, no leg's address), checks the group (NET-150) and renders VyOS
 * `high-availability vrrp` on each member. These are the segment panel's gestures and the clean-up
 * when a router leaves a segment, each a pure `(doc) => files`. Pure -- no React.
 */
import { gatewayAddress, intToIpv4, ipv4Range } from "./allocate";
import type { Files } from "./commands";
import type { DocModel, WorkingDoc } from "./document";
import { readTe, setNodeField, setNodeNets, setSegmentField, setSegmentVrrp, type TeModel, type TeNet } from "./te";

/** The routers with a leg on `segment`, in document order. */
export function routersOn(te: Pick<TeModel, "nodes">, segment: string): string[] {
  return te.nodes.filter((n) => n.kind === "router" && n.nets.some((l) => l.segment === segment)).map((n) => n.name);
}

/** The address hosts on `segment` route through: its VIP, its `gateway:`, the one router leg's address
 *  (pinned, else the `.1` the engine gives a lone unpinned router leg), or null (none, or two routers
 *  and no gateway written -- the engine then gives the LAN no gateway at all). */
export function effectiveGateway(te: Pick<TeModel, "nodes" | "segments">, segment: string): string | null {
  const seg = te.segments.find((s) => s.name === segment);
  if (!seg) return null;
  if (seg.vrrp?.vip) return seg.vrrp.vip;
  if (seg.gateway) return seg.gateway;
  const legs = te.nodes.filter((n) => n.kind === "router").flatMap((n) => n.nets.filter((l) => l.segment === segment));
  if (legs.length !== 1) return null;
  const r = ipv4Range(seg.cidr);
  return legs[0]!.ip ?? (r ? intToIpv4(r.start + 1) : null);
}

/** The members of a `vrrp:` group (or the routers that would be) not on a VyOS image: VRRP is
 *  rendered on VyOS only (NET-150). */
export function vrrpImageGaps(te: Pick<TeModel, "nodes">, members: readonly string[]): Array<{ router: string; image: string }> {
  return members.flatMap((m) => {
    const n = te.nodes.find((x) => x.name === m);
    return n && !n.image.toLowerCase().startsWith("vyos") ? [{ router: m, image: n.image || "no image" }] : [];
  });
}

function te(doc: WorkingDoc): string {
  const t = doc.files[doc.teFile];
  if (t === undefined) throw new Error(`the document has no '${doc.teFile}' to edit`);
  return t;
}

/**
 * "Share one gateway (VRRP)" on a LAN with two or more router legs: the VIP is the address the hosts
 * route through today (its `gateway:`, else the first router's, else `.1`); every member pinned at the
 * VIP (or unpinned, which the engine would give it) is re-pinned at the next free of `.2`..`.9`; the
 * `gateway:` line goes (the VIP is the gateway). `image`: switch the members not on VyOS to it in the
 * same edit (VRRP needs VyOS). One undo step.
 */
export function shareGateway(doc: WorkingDoc, model: DocModel, segment: string, opts: { vip?: string | null; image?: string | null } = {}): Files {
  const seg = model.te.segments.find((s) => s.name === segment);
  if (!seg) throw new Error(`no segment named '${segment}'`);
  if (seg.vrrp) throw new Error(`${segment} already shares one gateway (VRRP ${seg.vrrp.vip})`);
  const members = routersOn(model.te, segment);
  if (members.length < 2) throw new Error(`${segment} has ${members.length} router leg${members.length === 1 ? "" : "s"}; VRRP needs two or more`);
  const r = ipv4Range(seg.cidr);
  const first = model.te.nodes.find((n) => n.name === members[0])!.nets.find((l) => l.segment === segment)!;
  const vip = opts.vip?.trim() || seg.gateway || first.ip || (r ? intToIpv4(r.start + 1) : null);
  if (!vip) throw new Error(`${segment}'s CIDR ${seg.cidr} has no address for a VIP`);
  let text = setSegmentVrrp(te(doc), segment, { vip, members, vrid: null, priority: null });
  if (seg.gateway) text = setSegmentField(text, segment, "gateway", null);
  for (const m of members) {
    const cur = readTe(text);
    const n = cur.nodes.find((x) => x.name === m)!;
    const leg = n.nets.find((l) => l.segment === segment)!;
    if (leg.ip && leg.ip !== vip) continue;
    // Re-pin: the VIP is taken now, and an unpinned leg would not hold it either.
    const others = { segments: cur.segments, nodes: cur.nodes.map((x) => (x.name === m ? { ...x, nets: x.nets.filter((l) => l.segment !== segment) } : x)) };
    const ip = gatewayAddress(others, segment);
    text = setNodeNets(text, m, n.nets.map((l): TeNet => (l.segment === segment ? { segment, ip } : l)));
  }
  if (opts.image) for (const g of vrrpImageGaps(model.te, members)) text = setNodeField(text, g.router, "image", opts.image);
  return { ...doc.files, [doc.teFile]: text };
}

/** Stop sharing: the `vrrp:` block goes and the hosts route through the first member again (its
 *  address written as the segment's `gateway:`). */
export function stopSharing(doc: WorkingDoc, model: DocModel, segment: string): Files {
  const seg = model.te.segments.find((s) => s.name === segment);
  if (!seg?.vrrp) throw new Error(`${segment} shares no gateway`);
  let text = setSegmentVrrp(te(doc), segment, null);
  const first = seg.vrrp.members.map((m) => model.te.nodes.find((n) => n.name === m)?.nets.find((l) => l.segment === segment)?.ip).find(Boolean);
  if (first) text = setSegmentField(text, segment, "gateway", first);
  return { ...doc.files, [doc.teFile]: text };
}

/** Set the VIP (a host address of the segment no leg pins). */
export function setVip(doc: WorkingDoc, model: DocModel, segment: string, vip: string): Files {
  const seg = model.te.segments.find((s) => s.name === segment);
  if (!seg?.vrrp) throw new Error(`${segment} shares no gateway`);
  const holder = model.te.nodes.find((n) => n.nets.some((l) => l.segment === segment && l.ip === vip));
  if (holder) throw new Error(`${vip} is ${holder.name}'s address on ${segment}; a VIP is no node's address`);
  return { ...doc.files, [doc.teFile]: setSegmentVrrp(te(doc), segment, { ...seg.vrrp, vip }) };
}

/** Make `router` (just given a leg on `segment`) part of how the LAN is reached (A5 + D5): a member
 *  of its VRRP group when it has one; else, when the segment names no gateway, the gateway written
 *  out -- the first router's pinned address, or `.1` (which the engine binds to the first unpinned
 *  router leg) -- so a second router does not leave the LAN with none (the engine gives a segment of
 *  two router legs and no `gateway:` no gateway). Text in, text out. */
export function joinGateway(text: string, router: string, segment: string): string {
  const cur = readTe(text);
  const seg = cur.segments.find((s) => s.name === segment);
  if (!seg) return text;
  if (seg.vrrp) {
    return seg.vrrp.members.includes(router) ? text : setSegmentVrrp(text, segment, { ...seg.vrrp, members: [...seg.vrrp.members, router] });
  }
  if (seg.gateway) return text;
  const routers = routersOn(cur, segment);
  const first = cur.nodes.find((n) => n.name === routers[0])?.nets.find((l) => l.segment === segment);
  const r = ipv4Range(seg.cidr);
  const gw = first?.ip ?? (r ? intToIpv4(r.start + 1) : null);
  return gw ? setSegmentField(text, segment, "gateway", gw) : text;
}

/** `router` leaves `segments` (deleted, or a leg detached): it leaves their VRRP groups (a group left
 *  with one member goes, its VIP becoming the `gateway:` only if a router still holds it -- else the
 *  remaining member's address), and a `gateway:` naming its address moves to another router's
 *  pinned address there, else goes. Text in, text out; call it BEFORE the legs are removed. */
export function leaveGateways(text: string, router: string, segments: readonly string[]): string {
  for (const segment of segments) {
    const cur = readTe(text);
    const seg = cur.segments.find((s) => s.name === segment);
    if (!seg) continue;
    const others = cur.nodes.filter((n) => n.kind === "router" && n.name !== router).flatMap((n) => n.nets.filter((l) => l.segment === segment && l.ip).map((l) => l.ip!));
    if (seg.vrrp?.members.includes(router)) {
      const left = seg.vrrp.members.filter((m) => m !== router);
      if (left.length >= 2) text = setSegmentVrrp(text, segment, { ...seg.vrrp, members: left, priority: seg.vrrp.priority ? Object.fromEntries(Object.entries(seg.vrrp.priority).filter(([k]) => k !== router)) : null });
      else {
        text = setSegmentVrrp(text, segment, null);
        const rest = left[0] ? cur.nodes.find((n) => n.name === left[0])?.nets.find((l) => l.segment === segment)?.ip : null;
        if (rest) text = setSegmentField(text, segment, "gateway", rest);
      }
      continue;
    }
    if (!seg.gateway) continue;
    // Who holds the written gateway: a router pinned at it, else the first unpinned router leg (the
    // engine binds `gateway:` to it).
    const legs = cur.nodes.filter((n) => n.kind === "router").map((n) => ({ name: n.name, ip: n.nets.find((l) => l.segment === segment)?.ip, on: n.nets.some((l) => l.segment === segment) })).filter((x) => x.on);
    const holder = legs.find((x) => x.ip === seg.gateway)?.name ?? legs.find((x) => !x.ip)?.name ?? null;
    if (holder === router) text = setSegmentField(text, segment, "gateway", others[0] ?? null);
  }
  return text;
}
