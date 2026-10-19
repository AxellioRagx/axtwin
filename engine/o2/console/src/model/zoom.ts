/**
 * **The URL is the zoom** (`docs/design/15-console-canvas.md` §7): the route
 * names the level, the query names the filters, the breadcrumb IS the route,
 * Escape goes up a level and the back button works because none of it is
 * hidden in a store.
 *
 * Pure and React-free on purpose -- `routes.tsx` is the only file that knows
 * about a router, and this one is what the tests round-trip.
 *
 * Two mechanical spellings, both taken in `docs/WORK_ORDER_console_canvas.md`
 * §3 S2 because `15` §3/§7's notation is not directly expressible in a URL:
 *
 *   - **a CIDR's prefix length is written with a hyphen**
 *     (`/segments/10.80.20.0-24`, never `%2F`), and
 *   - **`?occupancy<0.8` is spelled `?occupancy_lt=0.8`**.
 *
 * A host may be addressed with or without its segment. `/design/<id>/hosts/
 * <key>` is the flat form (an entity key is unique within an imprint, so the
 * segment is redundant); `/design/<id>/segments/<cidr>/hosts/<key>` is the
 * nested form `15` §3's table gives, and it is what the canvas emits when the
 * segment is known, because it is what makes Escape land on the segment
 * instead of the network. Both parse; the nested one is preserved on the way
 * back out.
 */
import { cidrToRoute, routeToCidr } from "./keys";
import { isEvidenceClass, type EvidenceClass } from "./evidence";

export type Zoom =
  /** `/design` -- the imprint catalog; no imprint is open. */
  | { level: "catalog" }
  /** `/design/<id>` -- level 0, the tiered network diagram. */
  | { level: "network"; imprint: string }
  /** `/design/<id>/segments/<cidr>` -- level 1, the spine drill. */
  | { level: "segment"; imprint: string; cidr: string }
  /** `/design/<id>[/segments/<cidr>]/hosts/<key>` -- level 2, the facts table. */
  | { level: "host"; imprint: string; key: string; cidr?: string }
  /** `/design/<id>/matrix` -- the adjacency of the current scope (v1: level 0). */
  | { level: "matrix"; imprint: string; cidr?: string };

export type ZoomLevel = Zoom["level"];

/** Where the Design area (the assertions canvas) is rooted in the one app. */
export const DESIGN_SEGMENT = "design";
export const DESIGN_ROOT = `/${DESIGN_SEGMENT}`;

/**
 * How a site group's tiles are ORDERED, and therefore which of them the
 * per-group budget shows (R6, `layout/columns.ts`).
 *
 *   - `hosts` — placed-host count desc, then CIDR. The v1 default: the budget
 *     shows the six segments that hold the most of the twin.
 *   - `cidr`  — the address order the map used before R6, kept as `?order=cidr`
 *     because a *ranking* answers "which segments matter" while an *address
 *     order* answers "where is 10.20.195.0/24", and both are real questions.
 *
 * Declared here, with the rest of the URL vocabulary, so `model/` does not have
 * to import from `layout/` to name a value the query carries.
 */
export const TILE_ORDERS = ["hosts", "cidr"] as const;
export type TileOrder = (typeof TILE_ORDERS)[number];

export function isTileOrder(value: string): value is TileOrder {
  return (TILE_ORDERS as readonly string[]).includes(value);
}

/** The shape a site pin key has, and therefore the shape `?expand=` takes --
 *  the server's own `_SITE_KEY_RE` (`axrange/api/console_files.py`), mirrored
 *  here so the two vocabularies cannot drift. */
const SITE_KEY_RE = /^site:[0-9A-Za-z._:-]+$/;

export interface Filters {
  /** `?ghosts=1` -- show entities the deploy gate or an assertion removed. */
  ghosts: boolean;
  /** `?class=imputed` -- one of the six; an unknown value is a `problem`. */
  evidenceClass: EvidenceClass | null;
  /** `?occupancy_lt=0.8` -- the `?occupancy<0.8` of the design note. */
  occupancyLt: number | null;
  /**
   * `?changed=1` -- the post-apply "changes" filter (`15` §12 O5). Turned ON
   * by an apply that changed the bundle; it keeps the changed set REACHABLE
   * after the five-second highlight fades. Unlike the other three, the set it
   * emphasizes is NOT in the URL -- it is the last apply's diff, held in the
   * workspace -- so this is only the flag; the workspace threads the set.
   */
  changed: boolean;
  /**
   * `?summary=1` -- the cast-summary bar above the drawing is EXPANDED (R1).
   * Collapsed (the default) the bar is one line; expanded it shows the cast
   * numbers and the evidence mix.
   *
   * Like `changed`, this is a VIEW flag rather than a subset filter: it
   * emphasizes nothing and `anyFilterActive` deliberately ignores it, so
   * opening the bar never dims a tile. It lives here, in the one URL grammar,
   * because the work order requires the state to survive a reload and NOT to
   * live in a store -- and because `routeFor(zoom, filters)` is the single
   * path every navigation already threads, so the bar stays open across a
   * drill, the breadcrumb and Escape without a second mechanism.
   */
  summary: boolean;
  /**
   * `?ew=all` -- the east-west band shows EVERY observed segment pair, not the
   * top-K by volume (R4, `layout/arcs.ts`; D2's v1 budget is 12).
   *
   * A VIEW flag like `summary`: it dims nothing and emphasizes nothing, it
   * changes what the map DRAWS. In the URL for the same two reasons the
   * summary bar is -- it must survive a reload and a pasted link, and
   * `routeFor(zoom, filters)` is the one path every navigation already
   * threads.
   *
   * Spelled `all` rather than `1` because the work order spells it that way,
   * and because the budget is a number: `?ew=all` reads as the opposite of
   * "the busiest twelve", where `?ew=1` would read as a count.
   *
   * **Widths move when this is toggled** and that is not a data change: the
   * stroke scale is normalised over the DRAWN set's own range (§5 D2,
   * `layout/scale.ts`), so the same pair is wider among 12 arcs than among 40.
   */
  eastWestAll: boolean;
  /**
   * `?order=cidr` -- the order a site group's tiles are RANKED in, which is
   * also which six the per-group budget shows (R6, `layout/columns.ts`).
   *
   * A VIEW flag like `summary` and `ew`: it dims nothing, it changes what the
   * map draws. The default (`hosts`) is not emitted in the canonical query.
   */
  tileOrder: TileOrder;
  /**
   * `?expand=site:10.80` -- site groups whose per-group budget is lifted, so
   * the group draws every segment it holds instead of its busiest six (R6).
   *
   * **Repeatable**, and the only key in this grammar that is: it is a SET, and
   * `?expand=site:10.80&expand=site:10.20` is the natural spelling of one. Every
   * other key takes one value and reports the rest (`readOne`).
   *
   * In the URL rather than a store for the reasons every view flag is: it must
   * survive a reload and a pasted link, and `routeFor(zoom, filters)` is the
   * one path every navigation already threads.
   */
  expand: readonly string[];
  /**
   * Every query value that was NOT understood, with the reason. A filter UI
   * that silently drops a typo leaves an operator reading a filtered view
   * they did not ask for (LES-235: publish what you skipped and why).
   */
  problems: string[];
}

export const NO_FILTERS: Readonly<Filters> = Object.freeze({
  ghosts: false,
  evidenceClass: null,
  occupancyLt: null,
  changed: false,
  summary: false,
  eastWestAll: false,
  tileOrder: "hosts" as TileOrder,
  expand: Object.freeze([]) as readonly string[],
  problems: [],
});

/**
 * **The filter taxonomy, stated once (S2).** `Filters` carries three kinds of
 * field, and they behave differently:
 *
 *   - **dimming filters** -- `ghosts` / `class` / `occupancy_lt`: they
 *     emphasize a subset, and `anyDimmingFilter` is what turns the dimming
 *     pass on (`model/graph.ts` delegates to it);
 *   - the **emphasis flag** `changed`: marks the post-apply set without
 *     dimming anything, but IS dropped by the clear control;
 *   - **view flags** -- `summary`, `eastWestAll`, and R6's `tileOrder` /
 *     `expand`: what the page shows of itself, dropped by nothing.
 *     (`eastWestAll` and `expand` change which elements are drawn, which is a
 *     BUDGET, not a filter: a filter emphasizes a subset of the same drawing,
 *     and everything a budget lifts is disclosed on screen and present in the
 *     outline, the matrix and search either way.)
 *
 * Restating that split at each use site (the clear target, the clear chip's
 * visibility, the dimming predicate) is precisely how a later flag joins the
 * wrong set by being forgotten at one of them.
 */
export function anyDimmingFilter(filters: Filters): boolean {
  return filters.ghosts || filters.evidenceClass !== null || filters.occupancyLt !== null;
}

/** The filter set `clear` produces: every dimming filter and the emphasis flag
 *  off, every VIEW flag preserved. `problems` describe the input rather than
 *  the state, so they are not carried. */
export function clearFilters(filters: Filters): Filters {
  return {
    ...filters,
    ghosts: false,
    evidenceClass: null,
    occupancyLt: null,
    changed: false,
    problems: [],
  };
}

// ---------------------------------------------------------------------------
// path <-> zoom
// ---------------------------------------------------------------------------
export function zoomToPath(zoom: Zoom): string {
  switch (zoom.level) {
    case "catalog":
      return DESIGN_ROOT;
    case "network":
      return imprintBase(zoom.imprint);
    case "segment":
      return `${imprintBase(zoom.imprint)}/segments/${cidrSegment(zoom.cidr)}`;
    case "host":
      return zoom.cidr
        ? `${imprintBase(zoom.imprint)}/segments/${cidrSegment(zoom.cidr)}/hosts/${encodeSegment(zoom.key)}`
        : `${imprintBase(zoom.imprint)}/hosts/${encodeSegment(zoom.key)}`;
    case "matrix":
      return zoom.cidr
        ? `${imprintBase(zoom.imprint)}/segments/${cidrSegment(zoom.cidr)}/matrix`
        : `${imprintBase(zoom.imprint)}/matrix`;
  }
}

/** `null` for a path this build does not recognize -- the shell renders a
 *  "no such view" pane rather than guessing at the operator's intent. */
export function parseZoomPath(pathname: string): Zoom | null {
  const parts = splitPath(pathname);
  // `null` = the path could not even be decoded, which is NOT the same
  // answer as "the path was empty" (that one is the catalog root). Collapsing
  // the two sent `/imprints/%zz` to the catalog, reporting a view the
  // operator never asked for.
  if (parts === null) return null;
  // The Design area's root (Unified Console ruling §2 rule 8): the canvas is
  // re-rooted under `/design`, so `/design` is the imprint catalog and
  // `/design/<id>/...` the levels. `/` is the Range now, not this grammar's.
  if (parts[0] !== DESIGN_SEGMENT) return null;
  if (parts.length === 1) return { level: "catalog" };
  const imprint = parts[1];
  if (!imprint) return null;

  if (parts.length === 2) return { level: "network", imprint };
  if (parts.length === 3 && parts[2] === "matrix") {
    return { level: "matrix", imprint };
  }
  if (parts.length === 3 && parts[2] === "hosts") return null; // no key
  if (parts.length === 4 && parts[2] === "hosts" && parts[3]) {
    return { level: "host", imprint, key: parts[3] };
  }
  if (parts[2] !== "segments" || !parts[3]) return null;
  const cidr = routeToCidr(parts[3]);
  if (!cidr) return null;
  if (parts.length === 4) return { level: "segment", imprint, cidr };
  if (parts.length === 5 && parts[4] === "matrix") {
    return { level: "matrix", imprint, cidr };
  }
  if (parts.length === 6 && parts[4] === "hosts" && parts[5]) {
    return { level: "host", imprint, key: parts[5], cidr };
  }
  return null;
}

/**
 * The pre-consolidation spelling, `/imprints[/<id>/...]`, re-rooted to
 * `/design[/<id>/...]` with the query kept -- or `null` for any other path.
 * Old bookmarks and pasted links keep working (ruling §2 rule 8); the router
 * redirects with `replace`, so Back does not bounce through the old URL.
 * Mechanical on purpose: the tail is carried verbatim, so a legacy URL the
 * grammar would refuse is refused at its NEW address, where the "no such
 * view" pane says so, rather than being guessed at here.
 */
export function legacyDesignRedirect(pathname: string, search = ""): string | null {
  const match = /^\/+imprints(?=\/|$)(.*)$/.exec(pathname);
  if (!match) return null;
  const tail = (match[1] ?? "").replace(/^\/+/, "");
  return `${DESIGN_ROOT}${tail ? `/${tail}` : ""}${search}`;
}

/** Escape goes up a level: host -> its segment (when known) -> network ->
 *  catalog. `null` at the root. */
export function parentOf(zoom: Zoom): Zoom | null {
  switch (zoom.level) {
    case "catalog":
      return null;
    case "network":
      return { level: "catalog" };
    case "segment":
      return { level: "network", imprint: zoom.imprint };
    case "matrix":
      return zoom.cidr
        ? { level: "segment", imprint: zoom.imprint, cidr: zoom.cidr }
        : { level: "network", imprint: zoom.imprint };
    case "host":
      return zoom.cidr
        ? { level: "segment", imprint: zoom.imprint, cidr: zoom.cidr }
        : { level: "network", imprint: zoom.imprint };
  }
}

export function imprintOf(zoom: Zoom): string | null {
  return zoom.level === "catalog" ? null : zoom.imprint;
}

// ---------------------------------------------------------------------------
// query <-> filters
// ---------------------------------------------------------------------------
const TRUE_WORDS = new Set(["1", "true", "yes", "on"]);
const FALSE_WORDS = new Set(["0", "false", "no", "off", ""]);

/**
 * A plain decimal fraction and nothing else (D1). `Number()` alone accepts
 * far more than a URL should: `+` decodes to a SPACE, which `Number()` reads
 * as `0` -- a filter that hides everything, from a query the operator
 * believed was a typo. `0x1`, `1e-1`, `Infinity` and `%20` are the same
 * family. Anything outside this shape is reported rather than converted.
 */
const FRACTION_RE = /^\d*\.?\d+$/;

function searchParams(search: string): URLSearchParams {
  return new URLSearchParams(search.startsWith("?") ? search.slice(1) : search);
}

/**
 * The ONE value for a query key, and a problem when the URL carried more
 * (D2). `URLSearchParams.get` silently returns the first of N, so
 * `?class=observed&class=nonsense` filtered by the first and never mentioned
 * the second -- including when the dropped value was the unusable one the
 * operator would want to hear about.
 */
function readOne(
  params: URLSearchParams,
  name: string,
  problems: string[],
): string | null {
  const all = params.getAll(name);
  if (all.length > 1) {
    problems.push(
      `?${name}= appears ${all.length} times (${all.join(", ")}): only the ` +
        "first is used",
    );
  }
  return all.length > 0 ? all[0]! : null;
}

/** One boolean query flag, reported (never silently dropped) when the value is
 *  neither true-ish nor false-ish. ONE implementation for all three flags --
 *  the third copy of this block is what would have drifted. */
function readFlag(
  params: URLSearchParams,
  name: string,
  problems: string[],
): boolean {
  const raw = readOne(params, name, problems);
  if (raw === null) return false;
  const word = raw.toLowerCase();
  if (TRUE_WORDS.has(word)) return true;
  if (!FALSE_WORDS.has(word)) problems.push(`?${name}=${raw}: expected 1 or 0`);
  return false;
}

export function parseFilters(search: string): Filters {
  const params = searchParams(search);
  const problems: string[] = [];

  const ghosts = readFlag(params, "ghosts", problems);
  const changed = readFlag(params, "changed", problems);
  const summary = readFlag(params, "summary", problems);

  // `?ew=` is a named budget, not a boolean: `all` or the default `top`.
  // Anything else is reported rather than silently read as one of them (D2's
  // rule for every query value in this grammar).
  let eastWestAll = false;
  const rawEw = readOne(params, "ew", problems);
  if (rawEw !== null && rawEw !== "") {
    const word = rawEw.toLowerCase();
    if (word === "all") eastWestAll = true;
    else if (word !== "top") problems.push(`?ew=${rawEw}: expected all or top`);
  }

  // `?order=` is a named ORDER, not a boolean -- the same rule as `?ew=`: one
  // of the known words, or reported. It decides which six tiles a group's
  // budget shows, so a typo silently read as the default would hide a
  // different six than the operator asked for.
  let tileOrder: TileOrder = "hosts";
  const rawOrder = readOne(params, "order", problems);
  if (rawOrder !== null && rawOrder !== "") {
    const word = rawOrder.toLowerCase();
    if (isTileOrder(word)) tileOrder = word;
    else problems.push(`?order=${rawOrder}: expected ${TILE_ORDERS.join(" or ")}`);
  }

  // `?expand=` is the ONE repeatable key in this grammar (it names a SET), so
  // it reads every value rather than reporting the extras as `readOne` does.
  // Deduped and sorted, so two spellings of one set give one canonical URL.
  const expandSet = new Set<string>();
  for (const raw of params.getAll("expand")) {
    const value = raw.trim();
    // An empty `?expand=` is "nothing to expand", exactly as an empty `?ew=`
    // is the default -- not a typo worth a line in the UI.
    if (value === "") continue;
    if (!SITE_KEY_RE.test(value)) {
      problems.push(`?expand=${raw}: expected a site key such as site:10.80`);
      continue;
    }
    expandSet.add(value);
  }
  const expand = [...expandSet].sort();

  let evidenceClass: EvidenceClass | null = null;
  const rawClass = readOne(params, "class", problems);
  if (rawClass !== null && rawClass !== "") {
    if (isEvidenceClass(rawClass)) evidenceClass = rawClass;
    else problems.push(`?class=${rawClass}: not one of the six evidence classes`);
  }

  let occupancyLt: number | null = null;
  const rawOccupancy = readOne(params, "occupancy_lt", problems);
  if (rawOccupancy !== null && rawOccupancy !== "") {
    // Shape first, THEN convert (D1) -- see `FRACTION_RE`.
    const value = FRACTION_RE.test(rawOccupancy) ? Number(rawOccupancy) : NaN;
    if (!Number.isFinite(value) || value < 0 || value > 1) {
      problems.push(
        `?occupancy_lt=${JSON.stringify(rawOccupancy)}: expected a plain ` +
          "decimal fraction 0..1 (e.g. 0.8)",
      );
    } else {
      occupancyLt = value;
    }
  }

  return {
    ghosts,
    evidenceClass,
    occupancyLt,
    changed,
    summary,
    eastWestAll,
    tileOrder,
    expand,
    problems,
  };
}

/** The canonical query string for a filter set: only non-default values, in a
 *  fixed order, so `parse(format(f))` is stable and two equal filter sets
 *  produce byte-identical URLs. `problems` are not re-emitted -- they describe
 *  the input, not the state. */
export function filtersToQuery(filters: Filters): string {
  const params = new URLSearchParams();
  if (filters.ghosts) params.set("ghosts", "1");
  if (filters.evidenceClass) params.set("class", filters.evidenceClass);
  if (filters.occupancyLt !== null) {
    params.set("occupancy_lt", String(filters.occupancyLt));
  }
  if (filters.changed) params.set("changed", "1");
  if (filters.summary) params.set("summary", "1");
  if (filters.eastWestAll) params.set("ew", "all");
  if (filters.tileOrder !== "hosts") params.set("order", filters.tileOrder);
  // Sorted, and `append` rather than `set`: this is the one key that may carry
  // several values, and two equal sets must produce byte-identical URLs.
  for (const key of [...filters.expand].sort()) params.append("expand", key);
  // `URLSearchParams` percent-encodes the `:` in `site:10.80`, and these URLs
  // are meant to be read, typed and pasted -- the same reason a CIDR in a path
  // uses a hyphen rather than `%2F`. A colon is legal unencoded in a query
  // (RFC 3986), and `parseFilters` accepts both spellings since
  // `URLSearchParams` decodes on the way in.
  const query = params.toString().replace(/%3A/g, ":");
  return query ? `?${query}` : "";
}

// ---------------------------------------------------------------------------
// the whole route
// ---------------------------------------------------------------------------
export interface Route {
  zoom: Zoom | null;
  filters: Filters;
}

/**
 * Parse a full `pathname + search`. `?view=matrix` (the spelling `15` §3/§7
 * uses) is accepted as an ALIAS for the `/matrix` path and normalized to it,
 * so one canonical URL comes back out.
 */
export function parseRoute(url: string): Route {
  const at = url.indexOf("?");
  const pathname = at < 0 ? url : url.slice(0, at);
  const search = at < 0 ? "" : url.slice(at);
  const filters = parseFilters(search);
  const zoom = parseZoomPath(pathname);
  const view = readOne(searchParams(search), "view", filters.problems);
  if (view === null || view === "" || view === "map") return { zoom, filters };
  if (view !== "matrix") {
    filters.problems.push(`?view=${view}: the only alias is view=matrix`);
    return { zoom, filters };
  }
  // `?view=matrix` only means something at a level that HAS a matrix. On a
  // host route or the catalog it used to be dropped in silence, while an
  // unknown `?view=` was reported -- the same input class answered two
  // different ways (D3).
  if (zoom?.level === "network") {
    return { zoom: { level: "matrix", imprint: zoom.imprint }, filters };
  }
  if (zoom?.level === "segment") {
    return {
      zoom: { level: "matrix", imprint: zoom.imprint, cidr: zoom.cidr },
      filters,
    };
  }
  if (zoom?.level === "matrix") return { zoom, filters };
  filters.problems.push(
    `?view=matrix does not apply to ${zoom ? `the ${zoom.level} level` : "this route"}` +
      " -- the matrix is a view of a network or a segment",
  );
  return { zoom, filters };
}

/** The canonical URL for a zoom plus filters -- what the router pushes. */
export function routeFor(zoom: Zoom, filters: Filters = NO_FILTERS): string {
  return `${zoomToPath(zoom)}${filtersToQuery(filters)}`;
}

// ---------------------------------------------------------------------------
// internals
// ---------------------------------------------------------------------------
function imprintBase(imprint: string): string {
  return `${DESIGN_ROOT}/${encodeSegment(imprint)}`;
}

function cidrSegment(cidr: string): string {
  // A CIDR that cannot be spelled in a route is a programming error at the
  // call site, not an operator input: the caller got it from the bundle.
  return cidrToRoute(cidr) ?? encodeSegment(cidr);
}

/**
 * Percent-encode only what breaks a path segment. `encodeURIComponent` would
 * also escape the `:` in `address:10.1.0.14`, and these URLs are meant to be
 * read, typed and pasted -- the same reason the CIDR uses a hyphen rather
 * than `%2F`.
 */
function encodeSegment(value: string): string {
  return value.replace(/[%/?#\s]/g, (c) => `%${c.charCodeAt(0).toString(16).toUpperCase().padStart(2, "0")}`);
}

function splitPath(pathname: string): string[] | null {
  const out: string[] = [];
  for (const raw of pathname.split("/")) {
    if (!raw) continue;
    let decoded: string;
    try {
      decoded = decodeURIComponent(raw);
    } catch {
      // A malformed percent escape is not a path we know. `null` (never
      // `[]`) so the caller can tell it apart from the empty path, and no
      // throw escapes into a router render.
      return null;
    }
    out.push(decoded);
  }
  return out;
}
