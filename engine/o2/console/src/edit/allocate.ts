/**
 * Where a NEW segment goes: the next free `/24` inside a site's two-octet
 * prefix, which CIDRs and names are already held and by whom, where an
 * observed segment stands against the cast, where a new segment would group,
 * its default zone type, and which segments the edit store adds
 * (`docs/WORK_ORDER_console_canvas.md` §3 S5.1.a -- "prefilled with the next
 * free `/24` in that site's two-octet prefix ... and refuses a taken one").
 *
 * Pure -- no React, no fetch. Callers hand in what is in play and take the
 * answer. CIDR parsing is `model/keys.ts`'s and site grouping is
 * `layout/sites.ts`'s -- neither is restated here.
 *
 * **What "taken" means, and why it is decided HERE rather than in
 * `edit/records.ts`.** `records.ts` mirrors the server's own refusals, and a
 * mirror may be looser than its server but never stricter (LES-271). Whether a
 * CIDR is taken is not a document rule: the server validates the assertions
 * file with no topology in hand. It is a CONTENT question only the canvas can
 * answer:
 *
 *  - **A segment the cast declares** (its name, or a rename this file records,
 *    is in the cast's segment roster) is taken: adding it again is refused, and
 *    the right gesture is to select it and *Add host here*.
 *  - **An observed segment the cast does not declare under its name** is NOT
 *    refused -- but nothing here says an add brings it back, because the
 *    compiler does not: an `add: segment` of an OBSERVED CIDR only sets that
 *    segment's name and zone type, and the compiler keeps a segment only when a
 *    host is placed on it (aximprint `synth/compiler.py`, the asserted-segment
 *    pass, which skips an observed CIDR; `docs/design/14-operator-assertions.md`
 *    §3's "pruned by the cast … it is the OT lab" example predates that). The
 *    record is accepted, so it stays allowed (LES-271's direction), and the
 *    note says what does keep the segment. Nor is it called "pruned": the cast
 *    joins by NAME, and a segment it carries under another name reads the same.
 *  - **An `add: segment` already in the edit store** is refused: the store keys
 *    an add by its key, so a second one would silently REPLACE the first.
 *
 * With no roster published (a server predating it), whether the cast declares
 * an observed segment cannot be told, so every observed segment is refused --
 * the site `+` refuses a taken CIDR, and this one may be -- and the refusal says
 * why rather than blaming the twin.
 *
 * **An exact match is decided; an overlap is only said.** A CIDR that merely
 * overlaps a segment in play (`10.80.20.128/25` beside `10.80.20.0/24`) has a
 * real downstream check -- axRange's validator refuses overlapping networks
 * (NET-010) when a cast is not relocated -- but a relocated cast gives every
 * segment its own /24, so nothing on this side can tell; refusing would block
 * an edit no reader of record refuses (LES-271's direction). The ALLOCATOR is
 * conservative the other way: its suggestion never touches any observed or
 * staged network, including one the cast does not declare, because a prefill
 * is the canvas's own claim.
 */
import { groupIntoSites } from "../layout/sites";
import type { CastStatus, RosterSegment } from "../model/graph";
import { ipv4NetworkRange, ipv4ToInt, isIpv4, parseCidr, twoOctetPrefix } from "../model/keys";
import { isZoneType, ZONE_TYPES, type ZoneType } from "./records";

// ---------------------------------------------------------------------------
// inputs, typed structurally so this module takes no store or graph object
// ---------------------------------------------------------------------------
/** An edit-store entry as these rules read it. */
export interface StoreEntryInput {
  block: string;
  state: StoreState;
  record: {
    kind?: string | undefined;
    key?: string | undefined;
    name?: string | undefined;
    zone_type?: string | undefined;
    segment?: string | undefined;
  };
  /** The batch gesture the record came from, when it did (S5.1.b/c). */
  gesture?: { id: string; label: string; kind: string; source?: string | undefined } | undefined;
}

type StoreState = "pending" | "saved" | "applied";

// ---------------------------------------------------------------------------
// where an observed segment stands against the cast
// ---------------------------------------------------------------------------
/**
 * Where an observed segment stands against the cast (fix round, SF1):
 *
 *  - `carried` -- the roster names it under its observed name;
 *  - `renamed` -- the roster does not, but an `add: segment` record in this file
 *    keyed on its CIDR sets a `name` the roster DOES name: the compiler applies
 *    an add's name to a live observed segment, so the cast carries it as that;
 *  - `absent` -- the roster names neither: not in the cast under this name,
 *    which is "renamed or pruned" -- the cast joins by name, and without a
 *    rename on record the two cannot be told apart;
 *  - `unpublished` -- the server published no roster.
 */
export type StandingStatus = "carried" | "renamed" | "absent" | "unpublished";

export interface CastStanding {
  status: StandingStatus;
  /** The roster segment it is carried as -- under its own name, or a rename's. */
  castSegment: RosterSegment | null;
  /** The `add: segment` record this file holds for its CIDR, if any. */
  record: { state: StoreState; name: string | null } | null;
}

/** The standing of one observed segment. `castStatusOf` (`model/graph.ts`) is
 *  the pure name join; this adds what only the edit store knows -- a rename. */
export function castStandingOf(
  roster: readonly RosterSegment[] | null,
  segment: { name: string; observedCidr: string; status: CastStatus },
  entries: readonly StoreEntryInput[],
): CastStanding {
  const cidr = segment.observedCidr.trim();
  const found = entries.find(
    (e) => e.block === "add" && e.record.kind === "segment" && (e.record.key ?? "").trim() === cidr,
  );
  const record = found ? { state: found.state, name: found.record.name ?? null } : null;
  if (roster === null || segment.status === "unpublished") {
    return { status: "unpublished", castSegment: null, record };
  }
  const own = roster.find((r) => r.name === segment.name) ?? null;
  if (own) return { status: "carried", castSegment: own, record };
  const renamedTo = record?.name ? (roster.find((r) => r.name === record.name) ?? null) : null;
  if (renamedTo) return { status: "renamed", castSegment: renamedTo, record };
  return { status: "absent", castSegment: null, record };
}

/** An observed segment as the claims read it. */
export interface ObservedSegmentInput {
  observedCidr: string;
  name: string;
  standing: CastStanding;
}

/** Every observed segment with its standing, in the order given. */
export function observedSegmentInputs(
  segments: ReadonlyArray<{ name: string; observedCidr: string }>,
  roster: readonly RosterSegment[] | null,
  entries: readonly StoreEntryInput[],
): ObservedSegmentInput[] {
  return segments.map((seg) => ({
    observedCidr: seg.observedCidr,
    name: seg.name,
    standing: castStandingOf(
      roster,
      { ...seg, status: roster === null ? "unpublished" : roster.some((r) => r.name === seg.name) ? "carried" : "absent" },
      entries,
    ),
  }));
}

// ---------------------------------------------------------------------------
// CIDR claims
// ---------------------------------------------------------------------------
/** Who holds a CIDR: an observed segment by its standing against the cast,
 *  or an `add: segment` in the edit store by its state. */
export type ClaimSource = "cast" | "unpublished" | "absent" | StoreState;

export interface SegmentClaim {
  cidr: string;
  source: ClaimSource;
  /** For a `cast` claim carried under ANOTHER name: that name. */
  castName?: string;
}

/** A CIDR `claimOn` found in play: which claim, and whether the match is the
 *  same network (`exact`) or only an overlap. */
export interface ClaimHit {
  claim: SegmentClaim;
  exact: boolean;
}

/** The sources whose EXACT match is a refusal. `absent` is the one that is
 *  not: the compiler accepts an add of such a segment (see the module note). */
const REFUSING: readonly ClaimSource[] = ["cast", "unpublished", "pending", "saved", "applied"];

/**
 * Every CIDR in play: the observed segments, each by its standing, then every
 * `add: segment` in the edit store, whatever its state -- a SAVED add is as much
 * a claim as a pending one, since the file already says the segment exists.
 */
export function segmentClaims(
  observed: readonly ObservedSegmentInput[],
  entries: readonly StoreEntryInput[],
): SegmentClaim[] {
  const out: SegmentClaim[] = observed.map((seg) => observedClaim(seg));
  for (const e of entries) {
    if (e.block !== "add" || e.record.kind !== "segment" || !e.record.key) continue;
    out.push({ cidr: e.record.key, source: e.state });
  }
  return out;
}

function observedClaim(seg: ObservedSegmentInput): SegmentClaim {
  const { status, castSegment } = seg.standing;
  switch (status) {
    case "carried":
      return { cidr: seg.observedCidr, source: "cast" };
    case "renamed":
      return { cidr: seg.observedCidr, source: "cast", castName: castSegment?.name ?? seg.name };
    default:
      return { cidr: seg.observedCidr, source: status };
  }
}

/**
 * The claim `cidr` runs into, or `null` when nothing holds it.
 *
 * An EXACT match (the same string, or the same IPv4 network spelled
 * differently) wins over an overlap, and among exact matches the first
 * REFUSING one wins -- the observed segment's before the file's, so a segment
 * the cast declares says so rather than naming the add that renamed it.
 * Overlaps are looked for among the claims that are or will be in the twin; a
 * segment the cast does not declare is in neither. A value that is not an IPv4
 * CIDR can only match exactly.
 */
export function claimOn(cidr: string, claims: readonly SegmentClaim[]): ClaimHit | null {
  const want = cidr.trim();
  if (!want) return null;
  const range = ipv4Range(want);
  const same = claims.filter((c) => {
    if (c.cidr.trim() === want) return true;
    const other = range ? ipv4Range(c.cidr) : null;
    return other !== null && range !== null && other.start === range.start && other.end === range.end;
  });
  const refusing = same.find((c) => REFUSING.includes(c.source));
  if (refusing) return { claim: refusing, exact: true };
  if (same[0]) return { claim: same[0], exact: true };
  if (range === null) return null;
  for (const c of claims) {
    if (c.source === "absent") continue;
    const other = ipv4Range(c.cidr);
    if (other && range.start <= other.end && other.start <= range.end) {
      return { claim: c, exact: false };
    }
  }
  return null;
}

/** Whether a hit means Stage must refuse. */
export function isRefusal(hit: ClaimHit | null): boolean {
  return hit !== null && hit.exact && REFUSING.includes(hit.claim.source);
}

/** What the compiler does with an add of an observed segment the cast does not
 *  declare -- ONE sentence for the claim note and the inspector's fact. */
export const KEEPS_ONLY_WITH_A_HOST =
  "an add sets its name and zone type, but the compiler keeps a segment only when a host is placed " +
  "on it: select it and use Add host here";

/**
 * The hit in words -- ONE phrasing for the live note and the Stage refusal
 * alike, so the two cannot drift apart.
 */
export function claimMessage(cidr: string, hit: ClaimHit): string {
  const c = cidr.trim();
  if (hit.exact) {
    switch (hit.claim.source) {
      case "cast":
        return (
          `${c} is already declared in the cast` +
          `${hit.claim.castName ? ` (as ${hit.claim.castName})` : ""} — select it and use Add host here`
        );
      case "unpublished":
        return (
          `${c} is observed in this twin's export, but this server does not publish the cast's ` +
          "segment roster, so whether the cast declares it cannot be told — and the site + refuses " +
          "a taken CIDR"
        );
      case "absent":
        return (
          `${c} is observed, but the installed cast does not declare it under this name — renamed ` +
          `or pruned (the cast joins by name). ${capitalize(KEEPS_ONLY_WITH_A_HOST)}`
        );
      default:
        return `${c} is already ${storeClaimWords(hit.claim.source)}`;
    }
  }
  return (
    `${c} overlaps ${hit.claim.cidr.trim()}, ${overlapWords(hit.claim)} — the writer ` +
    "accepts it; axRange's validator refuses overlapping networks (NET-010) when a cast is " +
    "not relocated, while a relocated (--relocate) cast gives each segment its own /24"
  );
}

function capitalize(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function overlapWords(claim: SegmentClaim): string {
  switch (claim.source) {
    case "cast":
      return "a segment declared in the cast";
    case "unpublished":
      return "an observed segment";
    case "absent":
      return "an observed segment the cast does not declare";
    default:
      return storeClaimWords(claim.source);
  }
}

/** A store state in words -- the store's own semantics (`edit/store.ts`): a
 *  saved record is WRITTEN, with its effect on the cast unknown until
 *  something downstream names it. */
function storeClaimWords(state: ClaimSource): string {
  switch (state) {
    case "pending":
      return "claimed by a pending add (staged in this browser, not saved yet)";
    case "saved":
      return "claimed by a saved add (written to the assertions file; its effect on the cast is not known)";
    case "applied":
      return "claimed by an applied add (the compiler reports it in the cast)";
    default:
      return "claimed";
  }
}

// ---------------------------------------------------------------------------
// the next free /24
// ---------------------------------------------------------------------------
/**
 * The next free `/24` in a two-octet prefix (`10.80` -> `10.80.0.0/24`,
 * `10.80.1.0/24`, ...), or `null` when none is free -- or when `prefix` is not
 * a two-octet dotted prefix at all.
 *
 * **Free means no overlap with ANY CIDR in play, whatever its prefix length --
 * observed segments the cast does not declare included.** `10.80.20.0/25` does not equal
 * `10.80.20.0/24` as a string, but a `/24` allocated over it would overlap it;
 * `10.80.0.0/23` spans two `/24`s; a `10.80.0.0/16` or a `10.0.0.0/8` in play
 * leaves nothing free at all.
 */
export function nextFreeSlash24(prefix: string, inPlay: readonly string[]): string | null {
  const base = /^\d{1,3}\.\d{1,3}$/.test(prefix) ? ipv4ToInt(`${prefix}.0.0`) : null;
  if (base === null) return null;
  const ranges = inPlay.map(ipv4Range).filter((r): r is Range => r !== null);
  for (let n = 0; n < 256; n++) {
    const start = base + n * 256;
    const end = start + 255;
    if (!ranges.some((r) => start <= r.end && r.start <= end)) return `${prefix}.${n}.0/24`;
  }
  return null;
}

export interface Range {
  start: number;
  end: number;
}

/** The inclusive address range of the IPv4 network a CIDR names, or `null` for
 *  anything that is not an IPv4 CIDR -- `model/keys.ts`'s one mask rule.
 *  Exported for the clone's address mapping (`edit/clone.ts`), which must read
 *  networks exactly as the claims here do. */
export function ipv4Range(cidr: string): Range | null {
  const parsed = parseCidr(cidr.trim());
  if (!parsed || parsed.prefix > 32 || !isIpv4(parsed.address)) return null;
  const address = ipv4ToInt(parsed.address);
  return address === null ? null : ipv4NetworkRange(address, parsed.prefix);
}

// ---------------------------------------------------------------------------
// names
// ---------------------------------------------------------------------------
/** Who holds a segment NAME, and for which CIDR (`null` when the holder has no
 *  observed CIDR -- a roster segment nothing observed, the cast's
 *  `internet-sink`). */
export interface NameClaim {
  name: string;
  cidr: string | null;
  source: "cast" | "observed" | StoreState;
}

/**
 * Every segment name in play: the cast's roster (each joined to the observed
 * CIDR it declares -- under its own name, or through a rename this file
 * records), the observed segments, and every `add: segment` in the store.
 *
 * A twin config with two segments of one name fails validation (STR-095), and
 * every join by name -- conduits, router legs, the Console's own tiles -- goes
 * ambiguous. The compiler takes an add's `name` in place of the observed one,
 * so what collides is a name held by a DIFFERENT network.
 */
export function segmentNameClaims(
  roster: ReadonlyArray<{ name: string }> | null,
  observed: readonly ObservedSegmentInput[],
  entries: readonly StoreEntryInput[],
): NameClaim[] {
  const observedCidrByCastName = new Map<string, string>();
  for (const seg of observed) {
    const cast = seg.standing.castSegment;
    if (cast !== null) observedCidrByCastName.set(cast.name, seg.observedCidr);
  }
  const out: NameClaim[] = [];
  for (const seg of roster ?? []) {
    out.push({ name: seg.name, cidr: observedCidrByCastName.get(seg.name) ?? null, source: "cast" });
  }
  for (const seg of observed) out.push({ name: seg.name, cidr: seg.observedCidr, source: "observed" });
  for (const e of entries) {
    if (e.block !== "add" || e.record.kind !== "segment" || !e.record.name || !e.record.key) continue;
    out.push({ name: e.record.name, cidr: e.record.key, source: e.state });
  }
  return out;
}

/**
 * The claim a `name` for the network `cidr` collides with, or `null`. The same
 * network under its own name is not a collision: an add of an observed segment
 * under the name the export -- or the cast -- already gives it names nothing
 * new.
 */
export function nameClaimOn(name: string, cidr: string, claims: readonly NameClaim[]): NameClaim | null {
  const want = name.trim();
  if (!want) return null;
  const network = cidr.trim();
  return claims.find((c) => c.name === want && (c.cidr === null || c.cidr.trim() !== network)) ?? null;
}

/** A name collision in words -- the Stage refusal. */
export function nameClaimMessage(name: string, claim: NameClaim): string {
  const holder =
    claim.cidr === null
      ? "a segment declared in the cast that nothing observed"
      : `${claim.cidr}, ${nameHolderWords(claim.source)}`;
  return (
    `the name ${name.trim()} is already used by ${holder} — two segments with one name ` +
    "fail validation (STR-095) and make every join by name ambiguous"
  );
}

function nameHolderWords(source: NameClaim["source"]): string {
  switch (source) {
    case "cast":
      return "a segment declared in the cast";
    case "observed":
      return "an observed segment";
    default:
      return storeClaimWords(source).replace(/^claimed by /, "");
  }
}

/** The conventional segment name for a CIDR -- the producer's own rule
 *  (aximprint `windowing/segments.py` `segment_name`): `seg-` plus the dashed
 *  network address, the prefix length dropped. */
export function segmentNameFor(cidr: string): string {
  const at = cidr.lastIndexOf("/");
  const address = (at > 0 ? cidr.slice(0, at) : cidr).trim();
  if (!address) return "";
  return `seg-${address.replace(/[.:]/g, "-")}`;
}

/**
 * The name to PREFILL for a new segment: the producer's convention whenever it
 * is free, so a plain `/24` keeps the name every cast would give it -- and only
 * a clash is disambiguated, first by the prefix length (`10.80.20.0/23` beside
 * an observed `10.80.20.0/24` -> `seg-10-80-20-0-23`), then by a counter. The
 * convention drops the length because the producer only ever emits `/24`s,
 * which is exactly why a `/23` or `/25` at an observed network address would
 * otherwise inherit that segment's name.
 */
export function segmentNamePrefill(cidr: string, claims: readonly NameClaim[]): string {
  const base = segmentNameFor(cidr);
  if (!base) return "";
  const free = (name: string): boolean => nameClaimOn(name, cidr, claims) === null;
  if (free(base)) return base;
  const parsed = parseCidr(cidr.trim());
  const withLength = parsed ? `${base}-${parsed.prefix}` : base;
  if (withLength !== base && free(withLength)) return withLength;
  // Bounded: each claim can block at most one counter.
  for (let n = 2; n <= claims.length + 2; n++) {
    const candidate = `${withLength}-${n}`;
    if (free(candidate)) return candidate;
  }
  return `${withLength}-${claims.length + 3}`;
}

// ---------------------------------------------------------------------------
// where a new segment would group
// ---------------------------------------------------------------------------
/**
 * Where a new segment would group once it is part of the twin -- asked of
 * `layout/sites.ts`'s `groupIntoSites` itself, over the OBSERVED segments plus
 * the new one, so this sentence and the grouping cannot disagree. Staged adds
 * are not drawn, so they are not counted as siblings; any that share the
 * prefix are said separately. Nothing here promises a map placement: the map
 * draws an added segment only once it is PLACED (a clone, S5.1.b), and a segment
 * added from a site `+` has no placement.
 *
 * Returns a sentence, or `null` when the CIDR has no two-octet prefix to
 * group by (unparseable or empty -- the form's own refusal covers that).
 */
export function segmentLandingNote(
  cidr: string,
  observedCidrs: readonly string[],
  stagedCidrs: readonly string[] = [],
): string | null {
  const want = cidr.trim();
  const prefix = twoOctetPrefix(want);
  if (prefix === null) return null;
  const NONE = { bytes: 0, flows: 0 };
  const members = [...observedCidrs.filter((c) => c.trim() !== want), want].map((c) => ({
    observedCidr: c.trim(),
    northSouth: NONE,
  }));
  const home = groupIntoSites(members).find((site) => site.segments.some((m) => m.observedCidr === want));
  // An add of an OBSERVED CIDR is already counted among the observed siblings;
  // counting it again as an add was a double count (fix round, tester).
  const observedSet = new Set(observedCidrs.map((c) => c.trim()));
  const staged = stagedCidrs.filter(
    (c) => c.trim() !== want && !observedSet.has(c.trim()) && twoOctetPrefix(c.trim()) === prefix,
  ).length;
  const stagedClause =
    staged > 0
      ? `; ${staged} add${staged === 1 ? "" : "s"} in this file also share${staged === 1 ? "s" : ""} ${prefix} and ${staged === 1 ? "is" : "are"} not counted`
      : "";
  // Restated for S5.1.b (LES-299): a CLONE, placed on the map, is drawn now,
  // so the sentence claims only what this form adds -- a segment with no
  // placement, which the map does not draw until S5.1.d.
  const notDrawn = " — a segment added here is listed in the outline, not drawn on the map yet";
  if (!home || home.isMisc) {
    return `would sit in the shared misc group: no observed segment shares ${prefix}${stagedClause}${notDrawn}`;
  }
  const siblings = home.segments.length - 1;
  return (
    `would group with ${home.label}, beside ${siblings} observed segment${siblings === 1 ? "" : "s"}` +
    `${stagedClause}${notDrawn}`
  );
}

// ---------------------------------------------------------------------------
// the zone type a new segment starts at
// ---------------------------------------------------------------------------
/** Where a default zone type came from: the site's segments, the whole cast,
 *  or nowhere -- no zone type is declared (or no roster is published). */
export type ZoneSource = "site" | "twin" | "unpublished";

/**
 * The zone type a new segment's form starts at (fix round, ZT; S5.1.b's "zone
 * type: the source's", for a segment that has no single source): the one most
 * of the SITE's cast segments declare, else the one most of the whole cast
 * declares, else `simple` -- said as such. A tie goes to the earlier of
 * `ZONE_TYPES` (`simple`, then `vlan`), so the answer is a pure function of the
 * roster; a value outside the closed set is not counted.
 *
 * **The last resort is `simple`, never `vlan`.** The twin-config schema's own
 * default for an undeclared zone is `simple` (`axrange/twinconfig/schema.py`),
 * and `vlan` is not a per-segment choice in effect: ONE `vlan` segment turns the
 * whole scenario into a VLAN zone (the expander's scenario-wide rule), and a
 * VLAN zone rejects SNAT egress -- so a default that nothing declared must not
 * be the one that changes every other segment.
 */
export function defaultZoneType(
  siteZones: ReadonlyArray<string | null>,
  twinZones: ReadonlyArray<string | null>,
): { zone: ZoneType; source: ZoneSource } {
  const site = dominantZone(siteZones);
  if (site !== null) return { zone: site, source: "site" };
  const twin = dominantZone(twinZones);
  if (twin !== null) return { zone: twin, source: "twin" };
  return { zone: "simple", source: "unpublished" };
}

function dominantZone(zones: ReadonlyArray<string | null>): ZoneType | null {
  let best: ZoneType | null = null;
  let bestCount = 0;
  for (const zone of ZONE_TYPES) {
    const count = zones.filter((z) => z !== null && isZoneType(z) && z === zone).length;
    if (count > bestCount) {
      best = zone;
      bestCount = count;
    }
  }
  return best;
}

/** The site `+`'s accessible name -- ONE helper for the map's header and the
 *  outline's row, so the two cannot drift (LES-307), and no promise of a
 *  prefill: a full prefix, or the misc group, has none to give. */
export function addSegmentLabel(site: { isMisc: boolean; label: string }): string {
  return `Add a segment in ${site.isMisc ? "the misc group" : site.label}`;
}

// ---------------------------------------------------------------------------
// the segments the store adds
// ---------------------------------------------------------------------------
/** A segment an `add: segment` in the edit store brings, that the export does
 *  not observe -- selectable, actionable, not yet drawn. */
export interface AddedSegment {
  cidr: string;
  name: string;
  zoneType: string | null;
  state: StoreState;
  /** The CLONE this segment was staged by, when it was (S5.1.b) -- its source's
   *  observed CIDR. `null` for any other add, and for a clone whose gesture tag
   *  a page reload dropped (the file has no gesture concept). */
  cloneOf: string | null;
}

/**
 * The segments the edit store ADDS that the export does not observe, in store
 * order. An add of an OBSERVED segment is not among them: the export observes
 * it, so it already has a tile and a row of its own.
 */
export function addedSegmentsOf(
  entries: readonly StoreEntryInput[],
  observedCidrs: readonly string[],
): AddedSegment[] {
  const observed = new Set(observedCidrs.map((c) => c.trim()));
  const out: AddedSegment[] = [];
  for (const e of entries) {
    if (e.block !== "add" || e.record.kind !== "segment" || !e.record.key) continue;
    const cidr = e.record.key.trim();
    if (observed.has(cidr)) continue;
    out.push({
      cidr,
      name: e.record.name ?? "",
      zoneType: e.record.zone_type ?? null,
      state: e.state,
      cloneOf: e.gesture?.kind === "clone" ? (e.gesture.source ?? null) : null,
    });
  }
  return out;
}

// ---------------------------------------------------------------------------
// host addresses (S5.1.b/c)
// ---------------------------------------------------------------------------
/** A node as the address rule reads it. */
export interface AddressNodeInput {
  id: string;
  address: string | null;
}

/**
 * Every IPv4 host address already in play: each address the topology export
 * carries -- an `address:` node key's value, and any node's own `address` field
 * that is an IP (a `hostname:`-keyed node carries one on some bundles and an
 * `ent-0008` placeholder on others) -- plus each `address:` key ANY record in the
 * edit store holds, whatever its block or state. A generated or cloned host may
 * use none of them: the store keys an add by its key, so a second host on a
 * held address would REPLACE the first; a host keyed on an address the export
 * already carries would merge into that entity in the compiler; and an `add` of
 * a key the file suppresses -- or overrides a field of -- is `14` §3's
 * cross-record refusal. An allocator that CHOOSES addresses must not choose one
 * it already knows will be refused.
 *
 * The export side counts EVERY node, local or not, placed or not: an unplaced
 * or external entity with the address still owns it.
 */
export function takenAddresses(
  nodes: readonly AddressNodeInput[],
  entries: readonly StoreEntryInput[],
): Set<string> {
  const out = new Set<string>();
  const add = (value: string | null | undefined): void => {
    const v = (value ?? "").trim();
    if (isIpv4(v)) out.add(v);
  };
  for (const node of nodes) {
    if (node.id.startsWith("address:")) add(node.id.slice("address:".length));
    add(node.address);
  }
  for (const e of entries) {
    const key = (e.record.key ?? "").trim();
    if (key.startsWith("address:")) add(key.slice("address:".length));
  }
  return out;
}

/** The first host address generated hosts are numbered from (`.10`). */
export const HOST_NUMBERING_START = 10;

export type AddressAllocation =
  | { ok: true; addresses: string[] }
  | { ok: false; error: string };

/**
 * `count` host addresses in the IPv4 network `cidr`, from `.10` upward
 * (`HOST_NUMBERING_START` above the network address), skipping every address in
 * `taken` and stopping short of the broadcast address -- or a refusal saying how
 * many there are room for. Deterministic: the lowest free addresses, in order.
 *
 * A refusal rather than a partial list: a form asked for N hosts that stages
 * fewer has silently dropped the rest.
 */
export function allocateHostAddresses(
  cidr: string,
  count: number,
  taken: ReadonlySet<string>,
  from: number = HOST_NUMBERING_START,
): AddressAllocation {
  const range = ipv4Range(cidr);
  if (range === null) return { ok: false, error: `${cidr.trim()} is not an IPv4 network to number hosts in` };
  if (!Number.isInteger(count) || count < 0) {
    return { ok: false, error: `a host count is a whole number, got ${JSON.stringify(count)}` };
  }
  const out: string[] = [];
  // The last usable address is the one below the broadcast address.
  const last = range.end - 1;
  for (let at = range.start + from; at <= last && out.length < count; at++) {
    const address = intToIpv4(at);
    if (!taken.has(address)) out.push(address);
  }
  if (out.length < count) {
    return {
      ok: false,
      error:
        `${cidr.trim()} has room for ${out.length} more host${out.length === 1 ? "" : "s"} from ` +
        `.${from} upward (skipping the addresses already in play), not ${count}`,
    };
  }
  return { ok: true, addresses: out };
}

/** An unsigned 32-bit address as a dotted quad. */
export function intToIpv4(value: number): string {
  const v = Math.floor(value);
  return [Math.floor(v / 16777216) % 256, Math.floor(v / 65536) % 256, Math.floor(v / 256) % 256, v % 256].join(".");
}
