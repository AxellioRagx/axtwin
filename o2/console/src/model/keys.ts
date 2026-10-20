/**
 * Entity keys, the CIDR<->route encoding, and the site-bucket stub.
 *
 * **Entity keys are the assertions file's addressing** (`docs/design/
 * 14-operator-assertions.md` §3): `hostname:<name>`, `address:<ip>`,
 * `mac:<mac>`. aximprint's compiler is the reader of record and axRange's
 * writer accepts and refuses exactly what it does (`docs/WORK_ORDER_console_
 * canvas.md` §12.2) -- so the checks here are a fast local pre-flight for the
 * editor, never an authority. Anything this file passes may still be refused
 * by `PUT .../assertions`, and that refusal names the accepted set; anything
 * it rejects is a mistake worth catching before a round trip.
 */

export const ENTITY_KEY_KINDS = ["hostname", "address", "mac"] as const;
export type EntityKeyKind = (typeof ENTITY_KEY_KINDS)[number];

export interface EntityKey {
  kind: EntityKeyKind;
  value: string;
}

/** Parse `"<kind>:<value>"`. `null` for anything that is not one of the three
 *  known kinds -- including a bare hostname, which is a common paste and is
 *  NOT silently promoted to `hostname:` (the file's keys are explicit). */
export function parseEntityKey(raw: string): EntityKey | null {
  const at = raw.indexOf(":");
  if (at <= 0) return null;
  const kind = raw.slice(0, at);
  const value = raw.slice(at + 1);
  if (!value) return null;
  return isEntityKeyKind(kind) ? { kind, value } : null;
}

export function formatEntityKey(key: EntityKey): string {
  return `${key.kind}:${key.value}`;
}

export function isEntityKeyKind(value: string): value is EntityKeyKind {
  return (ENTITY_KEY_KINDS as readonly string[]).includes(value);
}

/**
 * The editor's pre-flight. `null` = nothing to say; a string = the reason the
 * server is expected to refuse or warn, in the server's own terms.
 *
 * The one rule worth mirroring exactly is the CIDR-in-an-`address:`-key
 * refusal (`axrange/api/console_files.py`): `address:10.80.20.0/24` names a
 * NETWORK, and a host assertion keyed on a network silently addresses nothing
 * -- the server refuses it naming the network form, so the editor should not
 * let an operator type it and wait for the round trip to find out.
 */
export function checkEntityKey(raw: string): string | null {
  const parsed = parseEntityKey(raw);
  if (!parsed) {
    return `not an entity key: expected one of ${ENTITY_KEY_KINDS.map((k) => `${k}:`).join(" / ")}`;
  }
  if (parsed.kind === "address") {
    if (parsed.value.includes("/")) {
      return "an `address:` key names a HOST address; this is a network (CIDR) form";
    }
    if (!isIpv4(parsed.value) && !parsed.value.includes(":")) {
      return "an `address:` key should be an IP address";
    }
  }
  return null;
}

// ---------------------------------------------------------------------------
// CIDR <-> route encoding
// ---------------------------------------------------------------------------
/**
 * A slash cannot travel in a path segment, so **the prefix length is written
 * with a hyphen** -- `/imprints/<id>/segments/10.80.20.0-24` (`docs/
 * WORK_ORDER_console_canvas.md` §3 S2, `15` §7). `%2F` was rejected as the
 * alternative: some proxies normalize or reject an encoded slash in a path,
 * and the URL is meant to be typed and pasted by an operator.
 *
 * The rule is mechanical -- the LAST `/` becomes the LAST `-` -- so it works
 * unchanged for an IPv6 CIDR, whose address half contains colons but never a
 * hyphen.
 */
export function cidrToRoute(cidr: string): string | null {
  const at = cidr.lastIndexOf("/");
  if (at <= 0) return null;
  const prefix = cidr.slice(at + 1);
  if (!/^\d{1,3}$/.test(prefix)) return null;
  const address = cidr.slice(0, at);
  if (!address || address.includes("-")) return null;
  return `${address}-${prefix}`;
}

export function routeToCidr(segment: string): string | null {
  const at = segment.lastIndexOf("-");
  if (at <= 0) return null;
  const prefix = segment.slice(at + 1);
  if (!/^\d{1,3}$/.test(prefix)) return null;
  const address = segment.slice(0, at);
  if (!address) return null;
  return `${address}/${prefix}`;
}

// ---------------------------------------------------------------------------
// The site bucket (O1) -- a STUB, and honest about why
// ---------------------------------------------------------------------------
/**
 * `15` §4's site rule is "two octets for RFC 1918 /24s, the **longest common
 * prefix** otherwise". Only the first half is computable from one CIDR: a
 * longest COMMON prefix is a property of the whole segment set, so the real
 * grouping is a set function and belongs to S3's `layout/sites.ts`. This
 * returns the two-octet bucket where the rule applies, and a segment's own
 * singleton bucket otherwise -- which renders as the "site that mixes
 * prefixes renders split until pinned" case the design already states, rather
 * than inventing a grouping the data does not support.
 *
 * **Every key this returns must be PINNABLE.** A site key is written into
 * `layout.json` as a pin, and the server validates it against
 * `^site:[0-9A-Za-z._:-]+$` (`axrange/api/console_files.py`'s
 * `_SITE_KEY_RE`) -- which admits no `/`. The singleton fallback therefore
 * spells its CIDR in the same hyphen form the routes use
 * (`site:203.0.113.0-24`), or S4's first drag of an unbucketed site would be
 * a 400 from a key this module produced.
 */
export function siteKeyForCidr(cidr: string): string | null {
  // The split and the digits-only prefix are `parseCidr`'s: `Number()` read
  // `10.80.20.0/0x18` as a /24 (the fix round's B1).
  const parsed = parseCidr(cidr);
  if (parsed === null) return null;
  const { address, prefix } = parsed;
  const octets = address.split(".");
  if (octets.length === 4 && isIpv4(address) && prefix === 24 && isRfc1918(octets)) {
    return `site:${octets[0]}.${octets[1]}`;
  }
  const route = cidrToRoute(cidr);
  return route === null ? null : `site:${route}`;
}

function isRfc1918(octets: string[]): boolean {
  const a = Number(octets[0]);
  const b = Number(octets[1]);
  if (a === 10) return true;
  if (a === 192 && b === 168) return true;
  return a === 172 && b >= 16 && b <= 31;
}

export function isIpv4(value: string): boolean {
  const parts = value.split(".");
  if (parts.length !== 4) return false;
  return parts.every((part) => /^\d{1,3}$/.test(part) && Number(part) <= 255);
}

// ---------------------------------------------------------------------------
// CIDR ordering (`15` §4: "segments as tiles in CIDR order")
// ---------------------------------------------------------------------------
/**
 * The address half of an IPv4 CIDR as a 32-bit integer, or `null` for
 * anything that is not a dotted quad. Used only for ORDERING, so a value it
 * cannot place sorts last rather than throwing -- the layout must be a total
 * function of whatever the export contains.
 */
export function ipv4ToInt(address: string): number | null {
  if (!isIpv4(address)) return null;
  const parts = address.split(".");
  let value = 0;
  for (const part of parts) value = value * 256 + Number(part);
  // `>>> 0` keeps it an unsigned 32-bit magnitude for a stable compare.
  return value >>> 0;
}

export interface ParsedCidr {
  address: string;
  prefix: number;
}

/**
 * `<address>/<prefix>` split into its two halves, or `null`. The prefix is
 * DIGITS ONLY (one to three), 0..128.
 *
 * Not `Number(...)` on whatever follows the slash (LES-254): `Number("")` is
 * `0`, so a half-typed `10.80.31.0/` parsed as a /0 -- a network over the whole
 * address space that every other CIDR "overlaps" -- and `Number("0x18")` is
 * `24`, a spelling no writer accepts. The address half is left to the caller
 * (IPv4 or not is its question).
 */
export function parseCidr(cidr: string): ParsedCidr | null {
  const at = cidr.lastIndexOf("/");
  if (at <= 0) return null;
  const address = cidr.slice(0, at);
  const prefixText = cidr.slice(at + 1);
  if (!/^\d{1,3}$/.test(prefixText)) return null;
  const prefix = Number(prefixText);
  if (prefix > 128) return null;
  return { address, prefix };
}

/**
 * The inclusive address range of the IPv4 NETWORK `address/prefix` names --
 * host bits masked off -- as unsigned integers. `address` is `ipv4ToInt`'s
 * value; `prefix` is 0..32.
 *
 * The one network-mask rule in the package: `edit/records.ts`'s `strictCidr`
 * asks "is this the network address?" (`start === address`) and
 * `edit/allocate.ts` asks "do these two networks meet?" off the same range.
 * Arithmetic, not a shift: `<<` takes its count mod 32 in JS, which turns a /0
 * into a /32, and a signed result makes everything above 2^31 negative.
 */
export function ipv4NetworkRange(address: number, prefix: number): { start: number; end: number } {
  const size = 2 ** (32 - prefix);
  const start = Math.floor(address / size) * size;
  return { start, end: start + size - 1 };
}

/**
 * A total order over CIDRs: numeric by address, then by prefix, so
 * `10.5.0.0/24` sorts before `10.20.192.0/24` (a lexical sort gets this
 * wrong -- `"10.20"` < `"10.5"` as strings). A CIDR that does not parse as
 * IPv4 sorts after every one that does, then lexically among its own kind,
 * so IPv6 or a malformed value has a stable place instead of no place.
 */
export function compareCidr(a: string, b: string): number {
  const pa = parseCidr(a);
  const pb = parseCidr(b);
  const ia = pa ? ipv4ToInt(pa.address) : null;
  const ib = pb ? ipv4ToInt(pb.address) : null;
  if (ia !== null && ib !== null) {
    if (ia !== ib) return ia - ib;
    return (pa?.prefix ?? 0) - (pb?.prefix ?? 0);
  }
  if (ia !== null) return -1;
  if (ib !== null) return 1;
  return a < b ? -1 : a > b ? 1 : 0;
}

/** The `a.b` two-octet prefix of a dotted quad, `null` otherwise. The RFC
 *  1918 /24 site rule (`15` §4, O1) buckets by this; `layout/sites.ts` owns
 *  the whole-set grouping this feeds. */
export function twoOctetPrefix(cidr: string): string | null {
  const parsed = parseCidr(cidr);
  if (!parsed || !isIpv4(parsed.address)) return null;
  const octets = parsed.address.split(".");
  return `${octets[0]}.${octets[1]}`;
}
