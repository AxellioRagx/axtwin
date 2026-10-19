/**
 * A deployed twin, as the Twin area reads it: `GET /api/deploys/{id}/hosts`
 * (the state-store inventory joined per host with its personas, taps and last
 * error) turned into the one model the map, the host table and the host panel
 * share (ruling §2 rule 2: one model, one renderer).
 *
 * Pure and React-free. Everything here is RECORDED -- the state store's
 * account of what was deployed and applied, never a read of the cluster; the
 * views label it so (rule 3). A value the store does not hold stays `null`
 * and renders as unknown (rule 4).
 *
 * **Segments are the twin's NETS, by id.** The inventory publishes no CIDR per
 * net, so none is shown as a fact. For GROUPING only (which nets sit together
 * on the map) a net is bucketed by the two-octet prefix of the addresses its
 * hosts hold -- the same rule the Design map groups sites by
 * (`layout/sites.ts`); a net whose hosts hold no address is grouped with the
 * other unaddressed nets and says so.
 */
import type { PersonaInstance, TwinHost, TwinHostsDoc } from "../api/runtimeTypes";
import { compareCidr, ipv4ToInt } from "./keys";

/** The WAN uplink's net id (`HostNetDTO.external`). */
export const WAN_NET = "__wan__";

/** Recorded op status, worst first -- the order a rollup takes the first of. */
export const STATUS_ORDER = [
  "failed",
  "halted-due-to-upstream",
  "running",
  "pending",
  "destroyed",
  "completed",
] as const;

export function statusRank(status: string | null): number {
  if (status === null) return STATUS_ORDER.length; // unknown sorts after every known one
  const at = (STATUS_ORDER as readonly string[]).indexOf(status);
  return at < 0 ? STATUS_ORDER.length - 0.5 : at;
}

/** The worst of a set of statuses; `null` when none is known. */
export function worstStatus(statuses: Iterable<string | null>): string | null {
  let worst: string | null = null;
  for (const s of statuses) {
    if (s === null) continue;
    if (worst === null || statusRank(s) < statusRank(worst)) worst = s;
  }
  return worst;
}

export interface DeployedHost {
  id: string;
  raw: TwinHost;
  /** Non-WAN nets this host has a NIC on, in NIC order. */
  segments: string[];
  /** The first of `segments`, or `null` for a host on no twin net. */
  primarySegment: string | null;
  /** Every recorded address, NIC order (WAN last). */
  ips: string[];
  isRouter: boolean;
  /** On two or more twin nets (a switch, a router, a dual-homed server). */
  multiHomed: boolean;
  personas: PersonaInstance[];
  /** The persona ids, sorted, de-duplicated. */
  personaIds: string[];
  /** The host's recorded status: the worst of its VM op and its persona ops. */
  recorded: string | null;
}

export interface DeployedSegment {
  id: string;
  /** Host ids with a NIC on this net, sorted. */
  hosts: string[];
  /** Hosts whose ONLY twin net this is (the ones that "live" here). */
  residents: number;
  /** Bucket key for grouping: `a.b` of the members' addresses, else `null`. */
  prefix: string | null;
  /** A /24-shaped key used ONLY to order and group nets -- never displayed as
   *  the net's CIDR (the store does not record one). */
  sortCidr: string | null;
  /** Persona instances on this net's hosts, and how many hosts carry one. */
  personaInstances: number;
  hostsWithPersonas: number;
  /** Persona id -> instance count, most common first. */
  personaMix: Array<{ persona: string; count: number }>;
  /** Role -> host count, most common first. */
  roleMix: Array<{ role: string; count: number }>;
  recorded: string | null;
  /** Routers with a leg on this net. */
  routers: string[];
}

export interface DeployedTwin {
  deployId: string;
  twinId: string | null;
  target: string | null;
  deployStatus: string | null;
  hosts: DeployedHost[];
  hostById: ReadonlyMap<string, DeployedHost>;
  segments: DeployedSegment[];
  segmentById: ReadonlyMap<string, DeployedSegment>;
  routers: DeployedHost[];
  /** True when any router has a WAN uplink -- the map then draws the Internet. */
  hasWan: boolean;
  personaInstanceCount: number;
  hostsWithPersonas: number;
  /** Distinct persona ids across the twin, with instance counts. */
  personaMix: Array<{ persona: string; count: number }>;
  warnings: string[];
}

function ipsOf(host: TwinHost): string[] {
  const ordered = [...host.nets].sort((a, b) => (a.nic_index ?? 99) - (b.nic_index ?? 99));
  const out: string[] = [];
  for (const n of ordered.filter((x) => !x.external)) if (n.ip) out.push(n.ip);
  for (const n of ordered.filter((x) => x.external)) if (n.ip) out.push(n.ip);
  return out;
}

function mix(values: Iterable<string>): Array<{ key: string; count: number }> {
  const counts = new Map<string, number>();
  for (const v of values) counts.set(v, (counts.get(v) ?? 0) + 1);
  return [...counts.entries()]
    .map(([key, count]) => ({ key, count }))
    .sort((a, b) => b.count - a.count || a.key.localeCompare(b.key));
}

/** `10.80.20.31` -> `10.80.20.0/24` (grouping key only). */
function slash24(ip: string): string | null {
  const n = ipv4ToInt(ip);
  if (n === null) return null;
  const net = n & 0xffffff00;
  return `${net >>> 24}.${(net >>> 16) & 255}.${(net >>> 8) & 255}.0/24`;
}

export function buildDeployedTwin(doc: TwinHostsDoc): DeployedTwin {
  const hosts: DeployedHost[] = doc.hosts
    .map((raw) => {
      const twinNets = [...raw.nets]
        .filter((n) => !n.external && n.net_id !== WAN_NET)
        .sort((a, b) => (a.nic_index ?? 99) - (b.nic_index ?? 99));
      const segments = [...new Set(twinNets.map((n) => n.net_id))];
      const isRouter = raw.kind === "router" || raw.nets.some((n) => n.external || n.net_id === WAN_NET);
      const personas = raw.personas ?? [];
      return {
        id: raw.host_id,
        raw,
        segments,
        primarySegment: segments[0] ?? null,
        ips: ipsOf(raw),
        isRouter,
        multiHomed: segments.length > 1,
        personas,
        personaIds: [...new Set(personas.map((p) => p.persona))].sort(),
        recorded: worstStatus([raw.vm_status, ...personas.map((p) => p.status)]),
      };
    })
    .sort((a, b) => a.id.localeCompare(b.id));

  const hostById = new Map(hosts.map((h) => [h.id, h]));

  const members = new Map<string, DeployedHost[]>();
  for (const h of hosts) {
    for (const s of h.segments) {
      const list = members.get(s) ?? [];
      list.push(h);
      members.set(s, list);
    }
  }

  const segments: DeployedSegment[] = [...members.entries()].map(([id, list]) => {
    const addrs: string[] = [];
    for (const h of list) {
      for (const n of h.raw.nets) if (n.net_id === id && n.ip) addrs.push(n.ip);
    }
    const cidrs = mix(addrs.map(slash24).filter((c): c is string => c !== null));
    const sortCidr = cidrs[0]?.key ?? null;
    const prefix = sortCidr ? sortCidr.split(".").slice(0, 2).join(".") : null;
    const personas = list.flatMap((h) => h.personas);
    return {
      id,
      hosts: list.map((h) => h.id).sort(),
      residents: list.filter((h) => h.segments.length === 1).length,
      prefix,
      sortCidr,
      personaInstances: personas.length,
      hostsWithPersonas: list.filter((h) => h.personas.length > 0).length,
      personaMix: mix(personas.map((p) => p.persona)).map(({ key, count }) => ({ persona: key, count })),
      roleMix: mix(list.map((h) => h.raw.role ?? "(no role)")).map(({ key, count }) => ({ role: key, count })),
      recorded: worstStatus(list.map((h) => h.recorded)),
      routers: list.filter((h) => h.isRouter).map((h) => h.id).sort(),
    };
  });
  segments.sort((a, b) => {
    if (a.sortCidr && b.sortCidr) return compareCidr(a.sortCidr, b.sortCidr) || a.id.localeCompare(b.id);
    if (a.sortCidr) return -1;
    if (b.sortCidr) return 1;
    return a.id.localeCompare(b.id);
  });

  const allPersonas = hosts.flatMap((h) => h.personas);
  const routers = hosts.filter((h) => h.isRouter);
  return {
    deployId: doc.deploy_id,
    twinId: doc.twin_id,
    target: doc.target,
    deployStatus: doc.deploy_status,
    hosts,
    hostById,
    segments,
    segmentById: new Map(segments.map((s) => [s.id, s])),
    routers,
    hasWan: routers.some((r) => r.raw.nets.some((n) => n.external || n.net_id === WAN_NET)),
    personaInstanceCount: allPersonas.length,
    hostsWithPersonas: hosts.filter((h) => h.personas.length > 0).length,
    personaMix: mix(allPersonas.map((p) => p.persona)).map(({ key, count }) => ({ persona: key, count })),
    warnings: doc.warnings ?? [],
  };
}

// ---- the host table ---------------------------------------------------------
export interface HostFilter {
  segment: string | null;
  q: string;
  /** `with` / `without` a persona; absent or `any` = both. */
  personas?: "any" | "with" | "without";
  /** Only hosts running this persona id. */
  persona?: string | null;
}

/** A host matches `q` when every whitespace-separated word appears in its id,
 *  a segment, an address, its role, its OS or a persona id (case-insensitive). */
export function filterHosts(hosts: readonly DeployedHost[], filter: HostFilter): DeployedHost[] {
  const words = filter.q.toLowerCase().split(/\s+/).filter(Boolean);
  return hosts.filter((h) => {
    if (filter.segment && !h.segments.includes(filter.segment)) return false;
    if (filter.personas === "with" && h.personas.length === 0) return false;
    if (filter.personas === "without" && h.personas.length > 0) return false;
    if (filter.persona && !h.personaIds.includes(filter.persona)) return false;
    if (words.length === 0) return true;
    const hay = [h.id, ...h.segments, ...h.ips, h.raw.role ?? "", h.raw.os ?? "", ...h.personaIds, h.recorded ?? ""]
      .join(" ")
      .toLowerCase();
    return words.every((w) => hay.includes(w));
  });
}

export type LiveLookup = (hostId: string) => string | null;

function compareIp(a: string | undefined, b: string | undefined): number {
  const na = a ? ipv4ToInt(a) : null;
  const nb = b ? ipv4ToInt(b) : null;
  if (na === null && nb === null) return 0;
  if (na === null) return 1;
  if (nb === null) return -1;
  return na - nb;
}

/** Unknown (null) always sorts LAST, whichever the direction -- an unknown is
 *  not "less than" anything. Ties break on the host id so the order is total. */
export function sortHosts(
  hosts: readonly DeployedHost[],
  key: string,
  dir: "asc" | "desc",
  live: LiveLookup = () => null,
): DeployedHost[] {
  const sign = dir === "desc" ? -1 : 1;
  const nullsLast = (a: string | null, b: string | null, cmp: (x: string, y: string) => number): number => {
    if (a === null && b === null) return 0;
    if (a === null) return 1;
    if (b === null) return -1;
    return sign * cmp(a, b);
  };
  const text = (x: string, y: string): number => x.localeCompare(y);
  const out = [...hosts];
  out.sort((a, b) => {
    let r = 0;
    switch (key) {
      case "segment":
        r = nullsLast(a.primarySegment, b.primarySegment, text);
        break;
      case "ip":
        r = sign * compareIp(a.ips[0], b.ips[0]);
        if (!a.ips[0] || !b.ips[0]) r = compareIp(a.ips[0], b.ips[0]);
        break;
      case "role":
        r = nullsLast(a.raw.role, b.raw.role, text);
        break;
      case "os":
        r = nullsLast(a.raw.os, b.raw.os, text);
        break;
      case "personas":
        r = sign * (a.personas.length - b.personas.length);
        break;
      case "recorded":
        r = nullsLast(a.recorded, b.recorded, (x, y) => statusRank(x) - statusRank(y));
        break;
      case "live":
        r = nullsLast(live(a.id), live(b.id), text);
        break;
      default:
        r = sign * a.id.localeCompare(b.id);
    }
    return r !== 0 ? r : a.id.localeCompare(b.id);
  });
  return out;
}
