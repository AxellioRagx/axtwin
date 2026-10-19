/**
 * **Clone a segment** (`docs/WORK_ORDER_console_canvas.md` §3 S5.1.b): one
 * gesture that stages `add: segment` + one `add: host` per chosen host (+ one
 * `add: conduit` per mirrored conduit) through the batch path
 * (`edit/batch.ts`). This module is the form's whole rule set -- the defaults,
 * which hosts come, where each lands, what is refused and why -- as pure
 * functions, so a test can run a real bundle through exactly what the form runs.
 *
 * **Nothing observed travels as evidence.** A cloned record carries values the
 * source host has -- its role, its listen and dial census, and an image or
 * sizing only when an assertion in this file gives the source one -- but never
 * the source's evidence classes or confidences: every record is an assertion,
 * and its note says so (`cloned from <source cidr>: <note>`).
 *
 * **What a clone may NOT reuse (the exact-versus-overlap ruling, S5.1.b).**
 * S5.1.a's site `+` refuses a CIDR the cast declares or an add already holds,
 * and ALLOWS an exact add of an observed segment the cast does not declare
 * under its name (it confirms that segment). A clone is stricter at the exact
 * match and identical at the overlap:
 *
 *  - a clone CIDR that EXACTLY equals an observed segment (whatever its
 *    standing against the cast), a segment the cast declares (joined by name
 *    through the roster and this file's renames, `castStandingOf`), or a
 *    pending/saved/applied add is REFUSED -- a clone creates a NEW segment, and
 *    reaching an existing one is *Add host here* on it;
 *  - a partial or containing OVERLAP only WARNS, as S5.1.a does: axRange's
 *    validator refuses overlapping networks (NET-010) unless the cast is
 *    relocated, which nothing on this side can tell;
 *  - host-level collisions are refused per host: a mapped address the export
 *    already carries, or that an add in this file already holds.
 *
 * (Ruled by the orchestrator for iteration 8, 2026-09-15; the work order's
 * "must not collide with an existing or pending segment" did not say which of
 * the two it meant, and a `1 + N` batch refused or accepted on that difference
 * is not the same gesture.)
 *
 * Pure -- no React, no fetch, no store object.
 */
import { groupIntoSites } from "../layout/sites";
import type { ConsoleGraph, GraphNode, GraphSegment } from "../model/graph";
import { ipv4ToInt, isIpv4, twoOctetPrefix } from "../model/keys";
import { roleBucket } from "../model/roles";
import {
  allocateHostAddresses,
  claimMessage,
  claimOn,
  defaultZoneType,
  intToIpv4,
  ipv4Range,
  nameClaimMessage,
  nameClaimOn,
  nextFreeSlash24,
  observedSegmentInputs,
  segmentClaims,
  segmentNameClaims,
  segmentNamePrefill,
  takenAddresses,
  type ClaimHit,
  type StoreEntryInput,
  type ZoneSource,
} from "./allocate";
import {
  isZoneType,
  portsFromCensus,
  strictCidr,
  ZONE_TYPES,
  type Gesture,
  type Resources,
  type ZoneType,
} from "./records";

// ---------------------------------------------------------------------------
// inputs
// ---------------------------------------------------------------------------
/** An edit-store entry as the clone rules read it (structural). */
export interface CloneEntryInput extends StoreEntryInput {
  record: StoreEntryInput["record"] & {
    field?: string | undefined;
    value?: unknown;
    image?: string | undefined;
    resources?: Record<string, unknown> | undefined;
  };
}

/** Which of the source's placed hosts come along. */
export type HostSelection =
  | { mode: "all" }
  | { mode: "roles"; roles: readonly string[] }
  /** `typed` is what the operator actually TYPED, carried so a refusal can quote
   *  it: `count` is `NaN` for anything that is not digits, and `JSON.
   *  stringify(NaN)` renders `null` -- "got null" told no one what they had
   *  entered (N3). Absent: the refusal quotes the number. */
  | { mode: "first"; count: number; typed?: string };

/** How a cloned host's address is chosen. */
export type AddressMapping = "same-host-part" | "renumber";

export interface CloneOptions {
  cidr: string;
  name: string;
  zoneType: ZoneType;
  hosts: HostSelection;
  mapping: AddressMapping;
  mirrorConduits: boolean;
  note: string;
}

// ---------------------------------------------------------------------------
// defaults
// ---------------------------------------------------------------------------
export interface CloneDefaults {
  /** The next free `/24` in the source's site, or `null` when none is free. */
  cidr: string | null;
  /** The two-octet prefix it was allocated in, or `null`. */
  prefix: string | null;
  /** Whether `prefix` is the site's own or -- for a source in the misc group,
   *  which has no single prefix -- the source's own two octets. */
  prefixSource: "site" | "source";
  name: string;
  zoneType: ZoneType;
  /** `source`: the source segment's declared zone type; otherwise the site ->
   *  cast -> `simple` chain S5.1.a's site `+` uses (LES-326). */
  zoneSource: "source" | ZoneSource;
}

/**
 * The clone form's starting values (the work order's table): the next free `/24`
 * in the source's site -- never an observed CIDR, since every observed segment
 * and every add is a claim -- the conventional collision-free name for it
 * (`segmentNamePrefill`), and the source's declared zone type.
 */
export function cloneDefaults(
  graph: ConsoleGraph,
  entries: readonly CloneEntryInput[],
  sourceCidr: string,
): CloneDefaults {
  const observed = observedSegmentInputs(graph.segments, graph.castSegments, entries);
  const claims = segmentClaims(observed, entries);
  const site = groupIntoSites(graph.segments).find((s) => s.segments.some((m) => m.observedCidr === sourceCidr)) ?? null;
  const prefix = site?.prefix ?? twoOctetPrefix(sourceCidr);
  const prefixSource = site?.prefix ? "site" : "source";
  const cidr = prefix === null ? null : nextFreeSlash24(prefix, claims.map((c) => c.cidr));
  const name = cidr === null ? "" : segmentNamePrefill(cidr, segmentNameClaims(graph.castSegments, observed, entries));

  const declared = observed.find((o) => o.observedCidr === sourceCidr)?.standing.castSegment?.zone_type ?? null;
  if (declared !== null && isZoneType(declared)) {
    return { cidr, prefix, prefixSource, name, zoneType: declared, zoneSource: "source" };
  }
  const siteCidrs = new Set((site?.segments ?? []).map((m) => m.observedCidr));
  const fallback = defaultZoneType(
    observed.filter((o) => siteCidrs.has(o.observedCidr)).map((o) => o.standing.castSegment?.zone_type ?? null),
    (graph.castSegments ?? []).map((r) => r.zone_type),
  );
  return { cidr, prefix, prefixSource, name, zoneType: fallback.zone, zoneSource: fallback.source };
}

// ---------------------------------------------------------------------------
// hosts
// ---------------------------------------------------------------------------
export interface NotCloned {
  key: string;
  reason: string;
}

export interface HostChoice {
  /** The chosen hosts that CAN be cloned, in address order. */
  chosen: GraphNode[];
  /** The hosts the selection names that cannot be, each with its reason. */
  notCloned: NotCloned[];
  /** A selection the form must refuse outright, or `null`. */
  error: string | null;
}

const HOSTNAME_REASON =
  "a hostname names one machine, so it is not cloned — add such a host by hand on the clone if it belongs there";
const MAC_REASON =
  "a MAC address names one interface, so it is not cloned — add such a host by hand on the clone if it belongs there";

/** A placed host's IPv4 address as an integer, when its KEY is `address:<ipv4>`. */
function keyAddress(node: GraphNode): number | null {
  if (!node.id.startsWith("address:")) return null;
  const value = node.id.slice("address:".length).trim();
  return isIpv4(value) ? ipv4ToInt(value) : null;
}

function notClonableReason(node: GraphNode): string | null {
  if (node.id.startsWith("hostname:")) return HOSTNAME_REASON;
  if (node.id.startsWith("mac:")) return MAC_REASON;
  if (keyAddress(node) === null) {
    return "its key carries no IPv4 address to map onto the clone's network, so it is not cloned";
  }
  return null;
}

/**
 * The hosts a selection brings. Only an `address:`-keyed host can be cloned --
 * its address is the one part of its identity a new network can re-base; a
 * `hostname:` or `mac:` key names one machine, and a copy under the same key
 * would be the same entity twice. Those are LISTED, never silently skipped.
 *
 * A selection that names hosts of which NONE can be cloned is refused: staging
 * a bare segment would drop the operator's stated intent -- "bring the domain
 * controllers" -- in silence.
 */
/** What a rejected "first N" actually held, in the operator's own words (N3). */
function gotWhat(selection: { count: number; typed?: string }): string {
  const typed = selection.typed;
  if (typed !== undefined) {
    return typed.trim() === "" ? "nothing typed" : `${JSON.stringify(typed)}, which is not a whole number`;
  }
  return Number.isNaN(selection.count) ? "no number at all" : JSON.stringify(selection.count);
}

export function chooseHosts(source: GraphSegment, selection: HostSelection): HostChoice {
  const byAddress = (a: GraphNode, b: GraphNode): number => (keyAddress(a) ?? 0) - (keyAddress(b) ?? 0);
  let named: GraphNode[];
  switch (selection.mode) {
    case "all":
      named = source.hosts.slice();
      break;
    case "roles": {
      if (selection.roles.length === 0) {
        return { chosen: [], notCloned: [], error: "choose at least one role to bring, or bring all placed hosts" };
      }
      const wanted = new Set(selection.roles);
      named = source.hosts.filter((h) => wanted.has(roleBucket(h.role, h.locality)));
      break;
    }
    case "first": {
      const clonable = source.hosts.filter((h) => notClonableReason(h) === null).sort(byAddress);
      if (!Number.isInteger(selection.count) || selection.count < 1 || selection.count > clonable.length) {
        return {
          chosen: [],
          notCloned: [],
          error:
            `"first N by address" takes a whole number from 1 to ${clonable.length} ` +
            `(the address-keyed hosts on ${source.observedCidr}), got ${gotWhat(selection)}`,
        };
      }
      // The hosts that CANNOT come are still listed: "first N" is read over the
      // address-keyed hosts, and the operator is told which ones never counted.
      const notCloned = source.hosts
        .map((h) => ({ host: h, reason: notClonableReason(h) }))
        .filter((x): x is { host: GraphNode; reason: string } => x.reason !== null)
        .map((x) => ({ key: x.host.id, reason: x.reason }));
      return { chosen: clonable.slice(0, selection.count), notCloned, error: null };
    }
  }
  const chosen: GraphNode[] = [];
  const notCloned: NotCloned[] = [];
  for (const host of named) {
    const reason = notClonableReason(host);
    if (reason === null) chosen.push(host);
    else notCloned.push({ key: host.id, reason });
  }
  chosen.sort(byAddress);
  if (named.length > 0 && chosen.length === 0) {
    return {
      chosen,
      notCloned,
      error:
        `none of the ${named.length} host${named.length === 1 ? "" : "s"} this selection names can be cloned ` +
        `(${notCloned.map((n) => n.key).join(", ")}) — choose other hosts, or add these by hand`,
    };
  }
  return { chosen, notCloned, error: null };
}

/**
 * The image and sizing an assertion in this file gives a host -- an `override`
 * of the field, or the host's own `add: host` record carrying it -- in any state.
 * Only these travel with a clone: an image or sizing nothing asserted is the
 * compiler's to choose for the new host too.
 */
export function assertedHostValues(
  entries: readonly CloneEntryInput[],
  key: string,
): { image?: string; resources?: Resources } {
  const out: { image?: string; resources?: Resources } = {};
  for (const e of entries) {
    if ((e.record.key ?? "").trim() !== key) continue;
    if (e.block === "override" && e.record.field === "image" && typeof e.record.value === "string") {
      out.image = e.record.value;
    }
    if (e.block === "override" && e.record.field === "resources" && isResources(e.record.value)) {
      out.resources = e.record.value;
    }
    if (e.block === "add" && e.record.kind === "host") {
      if (typeof e.record.image === "string") out.image = e.record.image;
      if (isResources(e.record.resources)) out.resources = e.record.resources;
    }
  }
  return out;
}

function isResources(value: unknown): value is Resources {
  return (
    value !== null &&
    typeof value === "object" &&
    !Array.isArray(value) &&
    Object.values(value as Record<string, unknown>).every((v) => typeof v === "string" || typeof v === "number")
  );
}

// ---------------------------------------------------------------------------
// conduits
// ---------------------------------------------------------------------------
export interface SourceConduits {
  /** The observed/added CIDRs the source has a conduit to, sorted. */
  peers: string[];
  /** Conduits the source has that cannot be re-keyed, each with its reason. */
  notMirrored: Array<{ peer: string; reason: string }>;
  /** Whether the server published the cast's conduit roster at all. */
  rosterPublished: boolean;
}

/**
 * The conduits a segment has: the ones the cast declares on it (by segment
 * NAME, the roster's key), resolved to the peer's observed CIDR, and the ones an
 * `add: conduit` in this file names (by CIDR). A cast conduit whose other end has
 * no observed CIDR on this map -- the cast's `internet-sink` -- cannot be written
 * as a conduit key, which names two networks, so it is listed as not mirrored.
 */
export function sourceConduits(
  graph: ConsoleGraph,
  entries: readonly CloneEntryInput[],
  sourceCidr: string,
): SourceConduits {
  const source = graph.segmentByCidr.get(sourceCidr) ?? null;
  const peers = new Set<string>();
  const notMirrored = new Map<string, string>();
  if (source !== null) {
    for (const c of graph.castConduits ?? []) {
      const other = c.from === source.name ? c.to : c.to === source.name ? c.from : null;
      if (other === null || other === source.name) continue;
      const peer = graph.segmentByName.get(other);
      if (peer) peers.add(peer.observedCidr);
      else {
        notMirrored.set(
          other,
          `the cast declares a conduit from ${source.name} to ${other}, which has no observed network on this ` +
            "map — a conduit key names two networks, so it cannot be re-keyed to the clone",
        );
      }
    }
  }
  for (const e of entries) {
    if (e.block !== "add" || e.record.kind !== "conduit") continue;
    const halves = (e.record.key ?? "").split("|").map((h) => h.trim());
    if (halves.length !== 2 || !halves.includes(sourceCidr)) continue;
    const other = halves[0] === sourceCidr ? halves[1]! : halves[0]!;
    if (other && other !== sourceCidr) peers.add(other);
  }
  return {
    peers: [...peers].sort(),
    notMirrored: [...notMirrored.entries()].sort(([a], [b]) => (a < b ? -1 : 1)).map(([peer, reason]) => ({ peer, reason })),
    rosterPublished: graph.castConduits !== null,
  };
}

/** A conduit's key as `buildRecord` writes it: the halves sorted, `|`-joined. */
export function conduitKeyFor(a: string, b: string): string {
  const [left, right] = a < b ? [a, b] : [b, a];
  return `${left}|${right}`;
}

// ---------------------------------------------------------------------------
// the plan
// ---------------------------------------------------------------------------
export interface ClonePlan {
  /** The gestures to hand the batch path: the segment, the hosts in address
   *  order, then the mirrored conduits sorted by key. */
  gestures: Gesture[];
  /** The clone's canonical CIDR. */
  cidr: string;
  /** Each cloned host's key, from the source's to the clone's. */
  hosts: Array<{ from: string; to: string }>;
  notCloned: NotCloned[];
  /** The peers a conduit is mirrored to, in key order. */
  conduits: string[];
  notMirrored: Array<{ peer: string; reason: string }>;
  /** Said, not refused -- an overlap (NET-010). */
  warnings: string[];
  /** The batch's gesture label. */
  label: string;
  /** The note every record carries. */
  note: string;
}

export type CloneResult =
  | { ok: true; plan: ClonePlan }
  | {
      ok: false;
      error: string;
      /** A host-address collision the RENUMBER mapping would not have -- the
       *  form offers it beside the refusal. */
      offerRenumber: boolean;
    };

/** The note a cloned record carries (the work order's table). */
export function clonedNote(sourceCidr: string, note: string): string {
  return `cloned from ${sourceCidr}: ${note.trim()}`;
}

/** The batch gesture's label -- `clone of 10.80.20.0/24 → 10.80.31.0/24`. */
export function cloneLabel(sourceCidr: string, cloneCidr: string): string {
  return `clone of ${sourceCidr} → ${cloneCidr}`;
}

/**
 * Why a clone CIDR is refused -- in the words of the exact-match ruling above.
 * `null` for a hit that is only an overlap (a warning, `claimMessage`'s).
 */
export function cloneCidrRefusal(cidr: string, hit: ClaimHit | null): string | null {
  if (hit === null || !hit.exact) return null;
  const c = cidr.trim();
  const reach = "a clone creates a NEW segment; to put hosts on an existing one, select it and use Add host here";
  switch (hit.claim.source) {
    case "cast":
      return `${c} is already declared in the cast${hit.claim.castName ? ` (as ${hit.claim.castName})` : ""} — ${reach}`;
    case "absent":
      return `${c} is an observed segment (the installed cast does not declare it under this name) — ${reach}`;
    case "unpublished":
      return `${c} is an observed segment in this twin's export — ${reach}`;
    default:
      return `${claimMessage(c, hit)} — a clone never reuses a segment this file already adds`;
  }
}

/**
 * Plan the clone, or refuse it with the reason. Everything the batch needs is
 * decided here; `edit/batch.ts` then checks the records whole against the store.
 */
export function planClone(
  graph: ConsoleGraph,
  entries: readonly CloneEntryInput[],
  sourceCidr: string,
  options: CloneOptions,
): CloneResult {
  const refuse = (error: string, offerRenumber = false): CloneResult => ({ ok: false, error, offerRenumber });
  const source = graph.segmentByCidr.get(sourceCidr) ?? null;
  if (source === null) {
    return refuse(`${sourceCidr} is not an observed segment of this twin, so there is nothing to clone`);
  }
  if (!options.note.trim()) {
    return refuse(`a clone needs a note: it is written on every record as "${clonedNote(sourceCidr, "<note>")}"`);
  }

  // The CIDR: the network address, not a segment in play.
  const cidr = strictCidr(options.cidr);
  if (cidr === null) {
    return refuse(
      `${JSON.stringify(options.cidr)} is not a network address — a clone's CIDR must be the network address ` +
        "(host bits zero), e.g. 10.80.31.0/24",
    );
  }
  const clone = ipv4Range(cidr);
  if (clone === null) return refuse(`${cidr} is not an IPv4 network, and host addresses are mapped in IPv4 only`);
  const observed = observedSegmentInputs(graph.segments, graph.castSegments, entries);
  const claims = segmentClaims(observed, entries);
  const hit = claimOn(cidr, claims);
  const refusal = cloneCidrRefusal(cidr, hit);
  if (refusal !== null) return refuse(refusal);
  const warnings = hit !== null ? [claimMessage(cidr, hit)] : [];

  // The name: never one a DIFFERENT network holds (STR-095).
  const nameHit = nameClaimOn(options.name, cidr, segmentNameClaims(graph.castSegments, observed, entries));
  if (nameHit !== null) return refuse(nameClaimMessage(options.name, nameHit));
  if (!(ZONE_TYPES as readonly string[]).includes(options.zoneType)) {
    return refuse(`zone type ${options.zoneType} is not one of ${ZONE_TYPES.join(" / ")}`);
  }

  // The hosts, and where each lands.
  const choice = chooseHosts(source, options.hosts);
  if (choice.error !== null) return refuse(choice.error);
  const sourceRange = ipv4Range(source.observedCidr);
  const exportTaken = takenAddresses(graph.nodes, []);
  const fileTaken = takenAddresses([], entries);
  let targets: string[];
  if (options.mapping === "renumber") {
    const allocation = allocateHostAddresses(cidr, choice.chosen.length, new Set([...exportTaken, ...fileTaken]));
    if (!allocation.ok) return refuse(allocation.error);
    targets = allocation.addresses;
  } else {
    targets = [];
    for (const host of choice.chosen) {
      const at = keyAddress(host)!;
      const offset = sourceRange === null ? -1 : at - sourceRange.start;
      if (sourceRange === null || offset < 0 || at > sourceRange.end) {
        return refuse(`${host.id} is not inside ${source.observedCidr}, so it has no host part to keep — renumber instead`, true);
      }
      const mapped = clone.start + offset;
      const lastHost = clone.end - (clone.end - clone.start >= 3 ? 1 : 0);
      const firstHost = clone.start + (clone.end - clone.start >= 3 ? 1 : 0);
      if (mapped > lastHost || mapped < firstHost) {
        return refuse(
          `${host.id}'s host part (.${offset}) has no host address in ${cidr} — use "renumber from .10"`,
          true,
        );
      }
      const address = intToIpv4(mapped);
      if (exportTaken.has(address)) {
        return refuse(
          `address:${address} (the clone of ${host.id}) already exists in this twin's export — two hosts ` +
            'cannot share one key; use "renumber from .10"',
          true,
        );
      }
      if (fileTaken.has(address)) {
        const holder = entries.find((e) => (e.record.key ?? "").trim() === `address:${address}`);
        const what = holder ? `${holder.state} ${holder.block}` : "staged";
        return refuse(
          `address:${address} (the clone of ${host.id}) is already claimed by a ${what} record in this file — ` +
            'a clone never reuses a key the file already holds; use "renumber from .10"',
          true,
        );
      }
      targets.push(address);
    }
  }

  const note = clonedNote(sourceCidr, options.note);
  const gestures: Gesture[] = [{ kind: "add-segment", cidr, name: options.name, zoneType: options.zoneType, note }];
  const hosts: ClonePlan["hosts"] = [];
  for (let i = 0; i < choice.chosen.length; i++) {
    const host = choice.chosen[i]!;
    const key = `address:${targets[i]!}`;
    const listen = portsFromCensus(host.listenPorts);
    if (typeof listen === "string") return refuse(`${host.id}'s listen set: ${listen}`);
    const dial = portsFromCensus(host.dialPorts);
    if (typeof dial === "string") return refuse(`${host.id}'s dial set: ${dial}`);
    const asserted = assertedHostValues(entries, host.id);
    gestures.push({
      kind: "add-host",
      key,
      segment: cidr,
      ...(host.role !== null && host.role.trim() ? { role: host.role } : {}),
      ...(listen.length > 0 ? { listen } : {}),
      ...(dial.length > 0 ? { dial } : {}),
      ...(asserted.image !== undefined ? { image: asserted.image } : {}),
      ...(asserted.resources !== undefined ? { resources: asserted.resources } : {}),
      note,
    });
    hosts.push({ from: host.id, to: key });
  }

  const conduits = options.mirrorConduits ? sourceConduits(graph, entries, sourceCidr) : null;
  // N4: a source conduit whose peer IS the clone's own CIDR cannot be mirrored
  // -- mirroring it would key a conduit from the clone to itself, which
  // `buildRecord` refuses and `14` §3 has no meaning for. Dropping it silently
  // made "mirror conduits (3)" stage two with no word about the third, so it is
  // LISTED under *not mirrored* like every other one that cannot travel.
  const selfPeer = (conduits?.peers ?? []).includes(cidr)
    ? [
        {
          peer: cidr,
          reason:
            `the source already has a conduit to ${cidr}, which is this clone's own network — ` +
            "a conduit joins two networks, and one to itself is not one",
        },
      ]
    : [];
  const peers = (conduits?.peers ?? [])
    .filter((peer) => peer !== cidr)
    .sort((a, b) => {
      const ka = conduitKeyFor(cidr, a);
      const kb = conduitKeyFor(cidr, b);
      return ka < kb ? -1 : ka > kb ? 1 : 0;
    });
  for (const peer of peers) gestures.push({ kind: "add-conduit", cidrA: cidr, cidrB: peer, note });

  return {
    ok: true,
    plan: {
      gestures,
      cidr,
      hosts,
      notCloned: choice.notCloned,
      conduits: peers,
      notMirrored: [...(conduits?.notMirrored ?? []), ...selfPeer].sort((a, b) =>
        a.peer < b.peer ? -1 : a.peer > b.peer ? 1 : 0,
      ),
      warnings,
      label: cloneLabel(sourceCidr, cidr),
      note,
    },
  };
}
