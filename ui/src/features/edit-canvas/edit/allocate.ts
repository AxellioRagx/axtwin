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
  const ranges = inPlay.map(ipv4Range).filter((r): r is Range => r !== null);
  let from = 0;
  if (options.after) {
    const under = ranges.filter((r) => r.start >= base && r.start < base + 65536);
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
