// Lifted from the frozen `o2/console/src/edit/allocate.ts` (2026-10-30): the
// PURE address arithmetic only -- `ipv4Range`, `nextFreeSlash24`,
// `allocateHostAddresses`, `intToIpv4`, `HOST_NUMBERING_START`. That module's
// claim/standing logic edits imprint assertions and is not lifted; what is
// "taken" here is read from the twin config (`takenAddresses`). Nothing
// imports `o2/console` at runtime.
/**
 * Segment-appropriate addressing for a placed node (`ui/Spec.md` C3.2): the
 * lowest free host addresses of the segment's CIDR from `.10` upward,
 * skipping every address the twin config already pins there and the
 * gateway's `.1`; and the next free `/24` under a site's two-octet prefix
 * for a new segment. Deterministic, refuses rather than truncates.
 */
import { ipv4NetworkRange, ipv4ToInt, isIpv4, parseCidr } from "../model/keys";
import type { TeModel } from "./te";

export interface Range {
  start: number;
  end: number;
}

/** The inclusive address range of the IPv4 network a CIDR names, or `null`
 *  for anything that is not an IPv4 CIDR. */
export function ipv4Range(cidr: string): Range | null {
  const parsed = parseCidr(cidr.trim());
  if (!parsed || parsed.prefix > 32 || !isIpv4(parsed.address)) return null;
  const address = ipv4ToInt(parsed.address);
  return address === null ? null : ipv4NetworkRange(address, parsed.prefix);
}

/** Whether `cidr` is written as its network address (`10.1.2.0/24`, not `10.1.2.5/24`). */
export function isNetworkAddress(cidr: string): boolean {
  const parsed = parseCidr(cidr.trim());
  const r = ipv4Range(cidr);
  if (!parsed || !r) return false;
  return ipv4ToInt(parsed.address) === r.start;
}

/**
 * The next free `/24` in a two-octet prefix (`10.80` -> `10.80.0.0/24`,
 * `10.80.1.0/24`, ...), or `null` when none is free. Free means no overlap
 * with ANY CIDR in play, whatever its prefix length. With `after`, the
 * search starts past the highest `/24` in play under the prefix (`.10`,
 * `.20` -> `.21`) and falls back to the first free one when none is left.
 */
export function nextFreeSlash24(prefix: string, inPlay: readonly string[], options: { after?: boolean } = {}): string | null {
  const base = /^\d{1,3}\.\d{1,3}$/.test(prefix) ? ipv4ToInt(`${prefix}.0.0`) : null;
  if (base === null) return null;
  // Never a /24 inside the transit pool (2026-11-21), nor -- unless asked for it, as the Internet tool
  // does -- inside the internet's (B7): the pools are fenced off as if they were in play.
  const pool = ipv4Range(TRANSIT_POOL);
  const inet = ipv4Range(INTERNET_POOL);
  const loop = ipv4Range(LOOPBACK_POOL);
  const ranges = inPlay.map(ipv4Range).filter((r): r is Range => r !== null);
  if (pool) ranges.push(pool);
  if (loop) ranges.push(loop);
  if (inet && !(base >= inet.start && base <= inet.end)) ranges.push(inet);
  let from = 0;
  if (options.after) {
    const under = ranges.filter((r) => r !== pool && r !== inet && r !== loop && r.start >= base && r.start < base + 65536);
    if (under.length) from = Math.min(255, Math.floor((Math.max(...under.map((r) => r.end)) - base) / 256) + 1);
    if (from >= 256) from = 0;
  }
  for (let i = 0; i < 256; i++) {
    const n = (from + i) % 256;
    const start = base + n * 256;
    const end = start + 255;
    if (!ranges.some((r) => start <= r.end && r.start <= end)) return `${prefix}.${n}.0/24`;
  }
  return null;
}

/** Where a router-to-router transit takes its `/30` (B1): a pool outside the sites' `/24`s. */
export const TRANSIT_POOL = "10.254.0.0/16";

/** The site prefix a new LAN takes when the design has no LAN yet (only transits, or nothing):
 *  `10.200` -> `10.200.0.0/24`, `10.200.1.0/24`, ... -- outside `TRANSIT_POOL`. */
export const DEFAULT_LAN_PREFIX = "10.200";

/** Where the Internet tool's segment and sink live (`add/drop.ts` `ensureInternet`): never a LAN's
 *  range (B7, 2026-11-21: a template rack had landed on 10.250.1.0/24, beside the internet's /24). */
export const INTERNET_POOL = "10.250.0.0/16";

/** Whether a CIDR overlaps `INTERNET_POOL`. */
export function inInternetPool(cidr: string): boolean {
  const r = ipv4Range(cidr);
  const p = ipv4Range(INTERNET_POOL);
  return !!r && !!p && r.start <= p.end && p.start <= r.end;
}

/** Whether a CIDR overlaps `TRANSIT_POOL` (a router-to-router transit, never a LAN). */
export function inTransitPool(cidr: string): boolean {
  const r = ipv4Range(cidr);
  const p = ipv4Range(TRANSIT_POOL);
  return !!r && !!p && r.start <= p.end && p.start <= r.end;
}

/** The two-octet site prefix of the first LAN among `cidrs` (in order, so a caller puts the CIDR it
 *  prefers first), skipping every CIDR inside `TRANSIT_POOL`; `null` when there is no LAN. */
export function lanSitePrefix(cidrs: readonly (string | null | undefined)[]): string | null {
  for (const c of cidrs) {
    if (!c || inTransitPool(c) || inInternetPool(c)) continue;
    const r = ipv4Range(c);
    if (r) return `${Math.floor(r.start / 16777216) % 256}.${Math.floor(r.start / 65536) % 256}`;
  }
  return null;
}

/** The /24 a new LAN gets: the next free one after the highest in use under `prefix` (a LAN site
 *  prefix, `lanSitePrefix`), else the first free one under `DEFAULT_LAN_PREFIX`; never in the pool. */
export function nextFreeLan(prefix: string | null, inPlay: readonly string[]): string | null {
  return (prefix ? nextFreeSlash24(prefix, inPlay, { after: true }) : null) ?? nextFreeSlash24(DEFAULT_LAN_PREFIX, inPlay, { after: !prefix });
}

/** The next free `/30` in `pool` (default `TRANSIT_POOL`), skipping every CIDR in play (any overlap),
 *  or `null` when the pool is full. */
export function nextFreeTransit(inPlay: readonly string[], pool: string = TRANSIT_POOL): string | null {
  const p = ipv4Range(pool);
  if (!p) return null;
  const ranges = inPlay.map(ipv4Range).filter((r): r is Range => r !== null);
  for (let start = p.start; start + 3 <= p.end; start += 4) {
    const end = start + 3;
    if (!ranges.some((r) => start <= r.end && r.start <= end)) return `${intToIpv4(start)}/30`;
  }
  return null;
}

/** Where an OSPF router's `routing.loopback` takes its `/32` (round 3, 2026-11-21): outside the
 *  transit, LAN and internet pools, and clear of the lab's `10.255.251.0/24` transport (the
 *  sensors' `transport_ip`s). The renderer derives the OSPF router-id from it. */
export const LOOPBACK_POOL = "10.255.0.0/24";

/** The next free loopback `/32` in `pool` (default `LOOPBACK_POOL`) from `.1` upward, skipping every
 *  address in `taken` (bare or `/32`), or `null` when the pool is full. */
export function nextFreeLoopback(taken: Iterable<string>, pool: string = LOOPBACK_POOL): string | null {
  const p = ipv4Range(pool);
  if (!p) return null;
  const used = new Set<string>();
  for (const t of taken) used.add(t.split("/")[0]!.trim());
  for (let at = p.start + 1; at < p.end; at++) {
    const ip = intToIpv4(at);
    if (!used.has(ip)) return `${ip}/32`;
  }
  return null;
}

/** The two usable addresses of a `/30` (`.1` and `.2` of it). */
export function transitEnds(cidr: string): [string, string] | null {
  const r = ipv4Range(cidr);
  if (!r || r.end - r.start !== 3) return null;
  return [intToIpv4(r.start + 1), intToIpv4(r.start + 2)];
}

/** The first host address generated hosts are numbered from (`.10`). */
export const HOST_NUMBERING_START = 10;

export type AddressAllocation = { ok: true; addresses: string[] } | { ok: false; error: string };

/**
 * `count` host addresses in the IPv4 network `cidr`, from `.10` upward,
 * skipping every address in `taken` and stopping short of the broadcast
 * address -- or a refusal saying how many there are room for.
 */
export function allocateHostAddresses(cidr: string, count: number, taken: ReadonlySet<string>, from: number = HOST_NUMBERING_START): AddressAllocation {
  const range = ipv4Range(cidr);
  if (range === null) return { ok: false, error: `${cidr.trim()} is not an IPv4 network to number hosts in` };
  if (!Number.isInteger(count) || count < 0) return { ok: false, error: `a host count is a whole number, got ${JSON.stringify(count)}` };
  const out: string[] = [];
  const last = range.end - 1;
  for (let at = range.start + from; at <= last && out.length < count; at++) {
    const address = intToIpv4(at);
    if (!taken.has(address)) out.push(address);
  }
  if (out.length < count) {
    return { ok: false, error: `${cidr.trim()} has room for ${out.length} more host${out.length === 1 ? "" : "s"} from .${from} upward (skipping the addresses already in play), not ${count}` };
  }
  return { ok: true, addresses: out };
}

/** An unsigned 32-bit address as a dotted quad. */
export function intToIpv4(value: number): string {
  const v = Math.floor(value);
  return [Math.floor(v / 16777216) % 256, Math.floor(v / 65536) % 256, Math.floor(v / 256) % 256, v % 256].join(".");
}

/** Every address the twin config pins on a segment, plus the gateway (`.1`),
 *  which the planner gives the segment's router leg. */
export function takenAddresses(model: TeModel, segment: string): Set<string> {
  const out = new Set<string>();
  const seg = model.segments.find((s) => s.name === segment);
  const r = seg ? ipv4Range(seg.cidr) : null;
  if (r) out.add(intToIpv4(r.start + 1));
  for (const n of model.nodes) for (const leg of n.nets) if (leg.segment === segment && leg.ip && isIpv4(leg.ip)) out.add(leg.ip);
  return out;
}

/** Whether an address sits inside a segment's network (not its network or broadcast address). */
export function addressInSegment(ip: string, cidr: string): boolean {
  const r = ipv4Range(cidr);
  const v = ipv4ToInt(ip);
  if (!r || v === null) return false;
  return v > r.start && v < r.end;
}

/** Where a router's leg on a LAN is pinned when it becomes that LAN's gateway (A5, 2026-11-21): the
 *  lowest of `.1` .. `.9` (below the hosts' `.10`) that nothing pins, so the first router takes `.1`
 *  and the second `.2`. An unpinned router leg already there holds `.1` (the planner gives a lone
 *  router leg the gateway address). `null` when none is free: the leg is left to the planner. */
export function gatewayAddress(model: Pick<TeModel, "segments" | "nodes">, segment: string): string | null {
  const seg = model.segments.find((s) => s.name === segment);
  const r = seg ? ipv4Range(seg.cidr) : null;
  if (!r || r.end - r.start < 4) return null;
  const taken = new Set<string>();
  // A VRRP VIP is no leg's address (D5), and an authored gateway belongs to the router it names.
  if (seg?.vrrp?.vip) taken.add(seg.vrrp.vip);
  for (const n of model.nodes)
    for (const leg of n.nets) {
      if (leg.segment !== segment) continue;
      if (leg.ip && isIpv4(leg.ip)) taken.add(leg.ip);
      else if (!leg.ip && n.kind === "router") taken.add(intToIpv4(r.start + 1));
    }
  for (let i = 1; i < HOST_NUMBERING_START && r.start + i < r.end; i++) {
    const ip = intToIpv4(r.start + i);
    if (!taken.has(ip)) return ip;
  }
  return null;
}

/** The same host offset in another network (`10.200.0.1` of `10.200.0.0/24` -> `10.200.1.1` of
 *  `10.200.1.0/24`): a copied segment's VIP or gateway (B3, D5). Null when it does not fit. */
export function shiftAddress(ip: string, from: string, to: string): string | null {
  const a = ipv4ToInt(ip);
  const f = ipv4Range(from);
  const t = ipv4Range(to);
  if (a === null || !f || !t) return null;
  const off = a - f.start;
  return off > 0 && t.start + off < t.end ? intToIpv4(t.start + off) : null;
}
