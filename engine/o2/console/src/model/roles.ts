/**
 * The fixed role order (`docs/design/15-console-canvas.md` §4) and the two
 * synthetic buckets the layout adds for entities a role does not place.
 *
 * §4 gives the order for level 1's role bands, top to bottom: **infrastructure
 * first** (`domain-controller`, `dns-resolver`, `file-server`, `mail-server`,
 * `web-tier`, `db-tier`, `monitoring-collector`, `egress-proxy`, …), then
 * `corp-workstation`, then `generic-endpoint`, then `unroled-endpoint`, then
 * the *below threshold* band. The matrix (§3) uses the same order for its rows
 * and columns so the map and the matrix agree.
 *
 * The list is a DISPLAY ORDER, never a filter: a role the export carries that
 * is not named here still gets a band and a matrix row -- it sorts after the
 * named infrastructure roles and before the endpoint roles, alphabetically
 * among its unknown peers, so the order is total and deterministic without
 * silently dropping a role the data actually has (`ot-*`, `bastion-jump`, a
 * future addition). The single rule this file must never break: it does not
 * decide what is present, only where present things sit.
 *
 * Pure and data-free -- `model/roles.ts` is added to §4's proposed layout for
 * the same reason `model/zoom.ts` was (§13.3): the order is a contract several
 * modules share, and a second copy is a second contract. The one piece of DATA
 * it re-exports is the persona library's per-role listen table (S5.1.c), which
 * is generated, checked in and drift-tested -- see `defaultListenFor`.
 */
import {
  ROLE_LISTEN_DEFAULTS,
  ROLES_WITH_DIALED_EMITS,
  type RoleListenEntry,
} from "./roleListen.generated";

/** Infrastructure roles, in the design's fixed order. Extended past §4's own
 *  "…" with the infra-shaped roles the CorpAPCN export actually carries, so
 *  they land in the infrastructure block rather than in the unknown tail. */
export const INFRA_ROLE_ORDER = [
  "domain-controller",
  "dns-resolver",
  "file-server",
  "mail-server",
  "web-tier",
  "db-tier",
  "monitoring-collector",
  "egress-proxy",
  "log-shipper",
  "network-device",
  "hypervisor",
  "bastion-jump",
] as const;

/** The endpoint roles, after every infrastructure role. */
export const ENDPOINT_ROLE_ORDER = ["corp-workstation", "generic-endpoint"] as const;

/**
 * A local entity the export gave no role. Its own band and matrix row, drawn
 * after `generic-endpoint` -- the population `census.with_role` counts against
 * `census.entities_total`, and this is where those un-fingerprinted-but-local
 * entities live. Distinct from `generic-endpoint`, which IS a discovered role.
 */
export const UNROLED = "unroled-endpoint";

/**
 * A non-local entity (`locality != "local"`). It has no tile (`15` §4, O7);
 * on the matrix it is one row and one column, the addressable stand-in for
 * every external peer the Internet tier summarizes.
 */
export const EXTERNAL = "external";

const NAMED_ORDER: readonly string[] = [
  ...INFRA_ROLE_ORDER,
  ...ENDPOINT_ROLE_ORDER,
];

/** Where the infrastructure block ends -- an unknown role sorts here, ahead
 *  of the endpoint roles, so a new infra-shaped role is grouped with its kind
 *  rather than after the workstations. */
const UNKNOWN_INSERT = INFRA_ROLE_ORDER.length;

/**
 * The set of roles the layout draws as **infrastructure devices** at level 0
 * (`15` §4: "the domain controller, the resolvers, the monitoring collector").
 * A device is a LOCAL entity carrying one of these roles -- local, because the
 * export also carries external peers with the same roles (12 external
 * `dns-resolver`/etc. on the CorpAPCN bundle), and an infra-tier that counted
 * those would not be "the servers the twin depends on" (LES-210: re-derive a
 * role-reading rule against the real population). On CorpAPCN this resolves to
 * exactly six: one `domain-controller`, four `dns-resolver`, one
 * `monitoring-collector`.
 */
export const INFRA_DEVICE_ROLES: ReadonlySet<string> = new Set([
  "domain-controller",
  "dns-resolver",
  "monitoring-collector",
]);

/**
 * A total order key for a role band or matrix axis. Named roles keep their
 * fixed index; `unroled-endpoint` and `external` sit at the very end in that
 * order; an unknown role sorts into the infrastructure/endpoint seam,
 * alphabetically among its unknown peers, so the whole order is deterministic.
 */
export function roleRank(role: string): number {
  if (role === EXTERNAL) return 1_000_002;
  if (role === UNROLED) return 1_000_001;
  const at = NAMED_ORDER.indexOf(role);
  if (at >= 0) return at;
  // Unknown: after the last named infrastructure role (index
  // `UNKNOWN_INSERT - 1`) and before the first endpoint role (`corp-workstation`
  // at index `UNKNOWN_INSERT`). The `-0.5` lands it in exactly that seam; ties
  // among unknowns break by name in `compareRole`.
  return UNKNOWN_INSERT - 0.5;
}

/** Compare two role labels by the fixed order, breaking a tie (two unknowns,
 *  both landing on the seam rank) alphabetically. */
export function compareRole(a: string, b: string): number {
  const ra = roleRank(a);
  const rb = roleRank(b);
  if (ra !== rb) return ra - rb;
  return a < b ? -1 : a > b ? 1 : 0;
}

/** Order a set of role labels for a band stack or a matrix axis. */
export function orderRoles(roles: Iterable<string>): string[] {
  return [...new Set(roles)].sort(compareRole);
}

/** The bucket a node's raw `role` field maps to for banding and the matrix:
 *  its role when it has one, `unroled-endpoint` when it is local without one.
 *  A non-local node is `external` regardless of any role it carries. */
export function roleBucket(
  role: string | null | undefined,
  locality: string | null | undefined,
): string {
  if (locality !== "local") return EXTERNAL;
  return role ?? UNROLED;
}

/**
 * How many role badges a level-0 tile carries (R3). Three: enough to name what
 * a segment is FOR without turning a 156-unit tile into a legend, and the
 * count beside each one keeps the claim honest where a fourth role is hidden.
 */
export const ROLE_BADGE_MAX = 3;

/**
 * The short form a role is drawn as on a tile badge (R3) -- `domain-controller`
 * is `DC` because a tile has room for two or three glyphs, not for eighteen.
 *
 * It is a DISPLAY abbreviation and nothing else: the full role travels beside
 * it in the badge's `<title>`, the outline tree and the inspector still spell
 * it out, and no lookup anywhere keys on the short form. The map covers the
 * roles `INFRA_ROLE_ORDER`/`ENDPOINT_ROLE_ORDER` name plus the two synthetic
 * buckets; anything else falls through to `roleBadgeLabel`'s derivation, so a
 * role the export invents tomorrow still gets a badge rather than a blank.
 */
const ROLE_BADGE_ABBR: Readonly<Record<string, string>> = {
  "domain-controller": "DC",
  "dns-resolver": "DNS",
  "file-server": "FS",
  "mail-server": "MAIL",
  "web-tier": "WEB",
  "db-tier": "DB",
  "monitoring-collector": "MON",
  "egress-proxy": "PROXY",
  "log-shipper": "LOG",
  "network-device": "NET",
  hypervisor: "HV",
  "bastion-jump": "JUMP",
  "corp-workstation": "WS",
  "generic-endpoint": "EP",
  [UNROLED]: "NONE",
  [EXTERNAL]: "EXT",
};

/**
 * A role's badge label -- TOTAL, like every other function here: an unknown
 * role gets a derived short form rather than being dropped, because this file
 * "does not decide what is present, only where present things sit".
 *
 * The derivation: a hyphenated role becomes its parts' initials (`ot-historian`
 * → `OTH`), a single word its first four characters (`hypervisor` → `HYPE`),
 * both upper-cased and capped at four glyphs so the badge box never has to
 * grow. Deterministic, so a golden pins it.
 */
export function roleBadgeLabel(role: string): string {
  // TRIMMED throughout, and that is the whole subtlety. An empty role reaches
  // here at all because `roleBucket` returns `role ?? UNROLED`, and `??` passes
  // a `""` an export wrote straight through as its own bucket -- but a
  // WHITESPACE role is worse than an empty one: `"   "` is truthy, so an
  // untrimmed derivation sails past the fallback below and returns `"   "`,
  // drawing a badge with no glyphs and an aria-label that reads as though a
  // role had been named. Trimming at every step is what makes "total, never
  // blank" true rather than merely intended. Latent: no real bundle carries a
  // blank or whitespace role.
  const key = role.trim();
  const known = ROLE_BADGE_ABBR[key];
  if (known) return known;
  const parts = key.split("-").filter((p) => p.trim().length > 0);
  const derived =
    parts.length > 1
      ? parts.map((p) => p.trim().slice(0, 1)).join("")
      : (parts[0] ?? "").trim().slice(0, 4);
  const label = derived.toUpperCase().slice(0, 4).trim();
  return label || (ROLE_BADGE_ABBR[UNROLED] ?? UNROLED);
}

/**
 * A role's DEFAULT LISTEN SET for a host the Console generates on a fresh
 * segment (`docs/WORK_ORDER_console_canvas.md` §3 S5.1.c) -- the ports the
 * persona library says that role LISTENS on: what a `real-service` persona of
 * that id serves, plus what any dialing persona reaches that role on, each over
 * the transport the persona schema resolves (`roleListen.generated.ts`'s header
 * states the drive-aware rule and what it skips).
 *
 * `servedBy` is the difference between "no persona in the library serves this
 * role" (`none`: an EMPTY prefill that must say so) and a real default
 * (`persona`) -- an empty list alone cannot tell the operator which it is. The
 * lookup is by the role as typed, trimmed; an empty role has no default.
 *
 * **`dialedPorts` is the prefill's own caveat, and callers must state it.** A
 * `real-service` persona's emits carry no direction, so a port the role DIALS
 * (a web tier's 3306 to the db tier) is filed under the role itself and arrives
 * here looking exactly like a served one. This flag says the generated table
 * KNOWS this role holds such a port (`ROLES_WITH_DIALED_EMITS`); it does not say
 * which. A prefill is a starting point to prune, never a claim that the host
 * serves the port -- and a form that offers it under the word "listens" without
 * the caveat writes 3306 into the file, the recompiled twin config,
 * `te inventory`'s `hosts[].listen` and the scorecard's rows 4/5.
 *
 * Each call returns FRESH entry objects, so a form that edits the prefill can
 * never write into the shared table.
 */
export function defaultListenFor(role: string): {
  entries: Array<{ port: number; proto: RoleListenEntry["proto"] }>;
  servedBy: "persona" | "none";
  dialedPorts: boolean;
} {
  const key = role.trim();
  const found = key ? ROLE_LISTEN_DEFAULTS[key] : undefined;
  const dialedPorts = key !== "" && ROLES_WITH_DIALED_EMITS.includes(key);
  if (!found || found.length === 0) return { entries: [], servedBy: "none", dialedPorts };
  return {
    entries: found.map((e) => ({ port: e.port, proto: e.proto })),
    servedBy: "persona",
    dialedPorts,
  };
}

/** The roles whose default holds a port they DIAL (see `defaultListenFor`) --
 *  re-exported so a caller can name them all, not only the one typed. */
export { ROLES_WITH_DIALED_EMITS };

/** Every role the persona library serves, sorted -- the form's suggestions. */
export function rolesWithListenDefaults(): string[] {
  return Object.keys(ROLE_LISTEN_DEFAULTS).sort();
}
