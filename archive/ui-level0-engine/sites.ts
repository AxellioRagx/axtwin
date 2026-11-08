// Lifted verbatim from the frozen `o2/console/src/layout/sites.ts` (2026-10-30);
// pure, no React, no DOM. Import paths adapted; nothing imports `o2/console` at runtime.
/**
 * Site grouping (O1, `docs/design/15-console-canvas.md` §4): the set function
 * that turns segments into the /16-ish groups level 0 lays out as columns.
 *
 * The rule, as ruled (`docs/WORK_ORDER_console_canvas.md` §7):
 *
 *   - **Two octets** for an RFC 1918 /24 (`10.80.*` → `site:10.80`).
 *   - **A single-segment site is merged into one shared `misc` group**, so the
 *     six one-tile /16s on CorpAPCN do not each cost a column (§4).
 *   - **A site that mixes prefix lengths renders split** -- carried as a
 *     `mixed` flag the view states in the UI, not hidden (O1).
 *
 * Pure and a set function on purpose: the two-octet bucket of ONE CIDR is in
 * `model/keys.ts`, but the "longest common prefix otherwise" and the
 * singleton-merge are properties of the whole segment set, so they live here.
 * The order is total (numeric two-octet prefix, `misc` last) so a golden pins
 * it, and adding a segment to an existing site changes only that site.
 *
 * **Longest-common-prefix is deferred, honestly.** Every segment the CorpAPCN
 * bundle carries is an RFC 1918 /24, so the two-octet rule places all of them
 * and the LCP branch has no data to exercise; v1 buckets any other IPv4 by its
 * two-octet prefix as a total stand-in and marks nothing. When a non-/24 or
 * non-RFC-1918 twin appears, this is the one function to revisit -- the layout
 * above it does not care how a site was formed, only that the grouping is a
 * deterministic set function.
 */
import type { GraphSegment, Volume } from "../model/graph";
import { compareCidr, parseCidr, twoOctetPrefix } from "../model/keys";

/**
 * What a site groups: a member's OBSERVED CIDR (the bucket key) and the
 * north-south volume the group's trunk leg sums. `GraphSegment` is the member
 * the map draws; the type is this narrow so `edit/allocate.ts` can ask the SAME
 * function where a segment that does not exist yet would land, rather than
 * restate the singleton-into-misc rule beside it (Console S5.1.a).
 */
export interface SiteMember {
  observedCidr: string;
  northSouth: Volume;
}

export interface Site<S extends SiteMember = GraphSegment> {
  /** `site:<prefix>` for a real bucket, `site:misc` for the merged
   *  singletons. Pinnable in S4 (`console_files.py`'s `_SITE_KEY_RE` admits
   *  both). */
  key: string;
  /** A short human label for the column header. */
  label: string;
  /**
   * The two-octet prefix every member shares (`10.80`) -- the one a new
   * segment born in this site is allocated from -- or `null` where there is no
   * single one: the `misc` group (singletons from different prefixes) and a
   * bucket that is not an IPv4 two-octet prefix. Published by the rule that
   * formed the bucket, so no consumer re-derives it by parsing `label`
   * (Console S5.1.a, B8).
   */
  prefix: string | null;
  /** Member segments, in CIDR order. */
  segments: S[];
  /** True when the members do not share one prefix length -- rendered split
   *  (O1), never silently grouped as if they matched. */
  mixed: boolean;
  /** True for the merged singleton bucket, so the view can label it. */
  isMisc: boolean;
  /** The group's NORTH-SOUTH volume (R2): its members' `GraphSegment.
   *  northSouth` summed, which is what sets its trunk leg's stroke width. A
   *  group with none still gets a leg -- a hairline, never nothing. */
  northSouth: Volume;
}

/** Sum the members' north-south volume. Summed HERE rather than in the layout
 *  because a site IS the set of its segments, and the set function that forms
 *  the group is the one place that knows which segments those are. */
function sumNorthSouth(segments: readonly SiteMember[]): Volume {
  return segments.reduce(
    (total, seg) => ({
      bytes: total.bytes + seg.northSouth.bytes,
      flows: total.flows + seg.northSouth.flows,
    }),
    { bytes: 0, flows: 0 },
  );
}

const MISC_KEY = "site:misc";

const RAW_BUCKET = "raw:";

function bucketKeyFor(cidr: string): string {
  const two = twoOctetPrefix(cidr);
  // A non-IPv4 / unparseable CIDR still needs a home; its own string is a
  // stable, total fallback (LCP deferred, see the module note).
  return two ?? `${RAW_BUCKET}${cidr}`;
}

/**
 * Group segments into sites. Singleton buckets fold into one `misc` group; a
 * bucket whose members differ in prefix length is flagged `mixed`. Real sites
 * come first in numeric two-octet order; `misc` is last.
 */
export function groupIntoSites<S extends SiteMember = GraphSegment>(segments: readonly S[]): Array<Site<S>> {
  const buckets = new Map<string, S[]>();
  for (const seg of segments) {
    const key = bucketKeyFor(seg.observedCidr);
    const list = buckets.get(key) ?? [];
    list.push(seg);
    buckets.set(key, list);
  }

  const real: Array<Site<S>> = [];
  const singletons: S[] = [];
  for (const [key, members] of buckets) {
    if (members.length === 1) {
      singletons.push(members[0]!);
      continue;
    }
    const sorted = members.slice().sort((a, b) => compareCidr(a.observedCidr, b.observedCidr));
    real.push({
      key: `site:${key}`,
      label: key,
      // The bucket key IS the prefix, unless it is the `raw:` fallback.
      prefix: key.startsWith(RAW_BUCKET) ? null : key,
      segments: sorted,
      mixed: prefixLengths(sorted).size > 1,
      isMisc: false,
      northSouth: sumNorthSouth(sorted),
    });
  }

  real.sort((a, b) => siteOrder(a) - siteOrder(b));

  const sites = real;
  if (singletons.length > 0) {
    const sorted = singletons.slice().sort((a, b) => compareCidr(a.observedCidr, b.observedCidr));
    sites.push({
      key: MISC_KEY,
      label: "misc",
      prefix: null,
      segments: sorted,
      // The misc group is singletons from DIFFERENT /16s, so it is "mixed" by
      // construction -- but that is the point of the bucket, not a warning, so
      // it carries `isMisc` and leaves `mixed` false.
      mixed: false,
      isMisc: true,
      northSouth: sumNorthSouth(sorted),
    });
  }
  return sites;
}

function prefixLengths(segments: readonly SiteMember[]): Set<number> {
  const out = new Set<number>();
  for (const s of segments) {
    const parsed = parseCidr(s.observedCidr);
    if (parsed) out.add(parsed.prefix);
  }
  return out;
}

/** Numeric order for a real site by its lowest member CIDR -- so `10.5.*`
 *  sorts before `10.20.*`, which a string compare of the label gets wrong. */
function siteOrder(site: Site<SiteMember>): number {
  const first = site.segments[0];
  if (!first) return Number.MAX_SAFE_INTEGER;
  const parsed = parseCidr(first.observedCidr);
  if (!parsed) return Number.MAX_SAFE_INTEGER;
  const octets = parsed.address.split(".");
  const a = Number(octets[0] ?? 0);
  const b = Number(octets[1] ?? 0);
  return a * 256 + b;
}
