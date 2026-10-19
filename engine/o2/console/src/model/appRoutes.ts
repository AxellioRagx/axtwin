/**
 * The app's URL grammar above the Design area -- **the URL is the state**
 * (ruling §2 rule 8, kept from design/15):
 *
 *   `/` · `/range`                          the Range (home)
 *   `/twin/<deploy_id>`                     one deployed twin: map + host table
 *   `/twin/<deploy_id>/hosts/<host_id>`     ... with that host's panel open
 *   `/twin/<deploy_id>/hosts/<host_id>/personas`   ... changing its personas
 *   `/catalog` · `/catalog/<twins|scenarios>/<id>` the catalog, one item open
 *   `/telemetry`                            link-outs to the Grafana dashboards
 *   `/design[/...]`                         the assertions canvas (`zoom.ts`)
 *   `/imprints[/...]`                       legacy: redirected to `/design[/...]`
 *
 * The Twin page's query carries its view state: `?target=` (the cluster
 * target name, when it is not the default), `?segment=<net_id>` (the drilled
 * segment -- map and table both narrow to it), `?q=` (the table filter) and
 * `?sort=`/`?dir=`. Pure and React-free, round-trip tested like `zoom.ts`.
 */
import { legacyDesignRedirect, DESIGN_SEGMENT } from "./zoom";

export type CatalogKind = "twins" | "scenarios";

export type AppRoute =
  | { area: "range" }
  | { area: "twin"; deployId: string; host: string | null; personas: boolean }
  | { area: "catalog"; kind: CatalogKind | null; id: string | null }
  | { area: "telemetry" }
  | { area: "design" }
  | { area: "redirect"; to: string }
  | { area: "unknown" };

export type AppArea = "range" | "twin" | "catalog" | "telemetry" | "design";

export function parseAppPath(pathname: string, search = ""): AppRoute {
  const legacy = legacyDesignRedirect(pathname, search);
  if (legacy !== null) return { area: "redirect", to: legacy };
  // Design owns its whole subtree, including a path it cannot decode -- its
  // own grammar (`zoom.ts`) reports that as "no such view" in context.
  if (/^\/+design(?:\/|$)/.test(pathname)) return { area: "design" };
  const parts = split(pathname);
  if (parts === null) return { area: "unknown" };
  if (parts.length === 0) return { area: "range" };
  const [head, ...rest] = parts;
  switch (head) {
    case "range":
      return rest.length === 0 ? { area: "range" } : { area: "unknown" };
    case "telemetry":
      return rest.length === 0 ? { area: "telemetry" } : { area: "unknown" };
    case DESIGN_SEGMENT:
      return { area: "design" };
    case "catalog": {
      if (rest.length === 0) return { area: "catalog", kind: null, id: null };
      const kind = rest[0];
      if ((kind === "twins" || kind === "scenarios") && rest.length === 1) {
        return { area: "catalog", kind, id: null };
      }
      if ((kind === "twins" || kind === "scenarios") && rest.length === 2 && rest[1]) {
        return { area: "catalog", kind, id: rest[1] };
      }
      return { area: "unknown" };
    }
    case "twin": {
      const deployId = rest[0];
      if (!deployId) return { area: "unknown" };
      if (rest.length === 1) return { area: "twin", deployId, host: null, personas: false };
      if (rest[1] !== "hosts" || !rest[2]) return { area: "unknown" };
      if (rest.length === 3) return { area: "twin", deployId, host: rest[2], personas: false };
      if (rest.length === 4 && rest[3] === "personas") {
        return { area: "twin", deployId, host: rest[2], personas: true };
      }
      return { area: "unknown" };
    }
    default:
      return { area: "unknown" };
  }
}

export function areaOf(route: AppRoute): AppArea | null {
  switch (route.area) {
    case "range":
    case "twin":
    case "catalog":
    case "telemetry":
    case "design":
      return route.area;
    default:
      return null;
  }
}

// ---- builders --------------------------------------------------------------
export function rangePath(): string {
  return "/range";
}

export function twinPath(deployId: string, query: Partial<TwinQuery> = {}): string {
  return `/twin/${seg(deployId)}${twinQueryString(query)}`;
}

export function hostPath(deployId: string, host: string, query: Partial<TwinQuery> = {}): string {
  return `/twin/${seg(deployId)}/hosts/${seg(host)}${twinQueryString(query)}`;
}

export function personasPath(deployId: string, host: string, query: Partial<TwinQuery> = {}): string {
  return `/twin/${seg(deployId)}/hosts/${seg(host)}/personas${twinQueryString(query)}`;
}

export function catalogPath(kind: CatalogKind | null = null, id: string | null = null, target: string | null = null): string {
  const base = kind ? (id ? `/catalog/${kind}/${seg(id)}` : `/catalog/${kind}`) : "/catalog";
  return target ? `${base}?target=${encodeURIComponent(target)}` : base;
}

// ---- the Twin page's query -------------------------------------------------
export const SORT_KEYS = ["host", "segment", "ip", "role", "os", "personas", "recorded", "live"] as const;
export type SortKey = (typeof SORT_KEYS)[number];

export type TwinView = "map" | "hosts";
export type PersonaPresence = "any" | "with" | "without";

export interface TwinQuery {
  target: string | null;
  /** Which of the two equal views fills the page (`?view=hosts`; map default). */
  view: TwinView;
  segment: string | null;
  q: string;
  /** `?personas=with|without` -- hosts that run a persona, or none. */
  personas: PersonaPresence;
  /** `?persona=<id>` -- hosts running this persona. */
  persona: string | null;
  sort: SortKey;
  dir: "asc" | "desc";
}

export const DEFAULT_TWIN_QUERY: Readonly<TwinQuery> = Object.freeze({
  target: null,
  view: "map" as TwinView,
  segment: null,
  q: "",
  personas: "any" as PersonaPresence,
  persona: null,
  sort: "host" as SortKey,
  dir: "asc" as const,
});

export function parseTwinQuery(search: string): TwinQuery {
  const params = new URLSearchParams(search.startsWith("?") ? search.slice(1) : search);
  const sortRaw = params.get("sort");
  const sort: SortKey = sortRaw && (SORT_KEYS as readonly string[]).includes(sortRaw) ? (sortRaw as SortKey) : "host";
  const presence = params.get("personas");
  return {
    target: nonEmpty(params.get("target")),
    view: params.get("view") === "hosts" ? "hosts" : "map",
    segment: nonEmpty(params.get("segment")),
    q: params.get("q") ?? "",
    personas: presence === "with" || presence === "without" ? presence : "any",
    persona: nonEmpty(params.get("persona")),
    sort,
    dir: params.get("dir") === "desc" ? "desc" : "asc",
  };
}

/** Only non-default values, in a fixed order -- one canonical URL per state. */
export function twinQueryString(query: Partial<TwinQuery>): string {
  const full = { ...DEFAULT_TWIN_QUERY, ...query };
  const params = new URLSearchParams();
  if (full.target) params.set("target", full.target);
  if (full.view !== "map") params.set("view", full.view);
  if (full.segment) params.set("segment", full.segment);
  if (full.q) params.set("q", full.q);
  if (full.personas !== "any") params.set("personas", full.personas);
  if (full.persona) params.set("persona", full.persona);
  if (full.sort !== "host") params.set("sort", full.sort);
  if (full.dir !== "asc") params.set("dir", full.dir);
  const text = params.toString();
  return text ? `?${text}` : "";
}

// ---- internals ---------------------------------------------------------------
function nonEmpty(value: string | null): string | null {
  return value && value.trim() ? value : null;
}

function seg(value: string): string {
  return value.replace(/[%/?#\s]/g, (c) => `%${c.charCodeAt(0).toString(16).toUpperCase().padStart(2, "0")}`);
}

function split(pathname: string): string[] | null {
  const out: string[] = [];
  for (const raw of pathname.split("/")) {
    if (!raw) continue;
    try {
      out.push(decodeURIComponent(raw));
    } catch {
      return null;
    }
  }
  return out;
}
