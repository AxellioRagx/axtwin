// @vitest-environment node
/**
 * S5.1.a's allocator (`src/edit/allocate.ts`) beyond `selection.test.ts`:
 *
 *   1. the boundaries that file's sweep does not pin -- the LAST /24 of a
 *      prefix (the loop bound, from both sides), a prefix covered by claims of
 *      every width with exactly one hole, a claim with host bits set;
 *   2. "a prefill is staged as-is": every suggestion, with the name prefilled
 *      from it, is a record the writer mirror accepts unchanged, so the site
 *      `+` gesture really is "+, note, Stage";
 *   3. the module's own claim that `segmentLandingNote` and `layout/sites.ts`
 *      "cannot disagree" -- tested as a PROPERTY over sibling counts 0..3
 *      rather than restated (since the fix round the note CALLS
 *      `groupIntoSites`, so the property now pins the wording as well);
 *   4. the site `+` prefill on BOTH shipped bundles (LES-185: a hand fixture
 *      cannot prove what ships).
 *
 * **Which seams the real-bundle block uses, named (LES-311).** The live app
 * builds its graph as `buildGraph(topology, castInputFrom(castSummary))`, and
 * since the fix round's A1 the cast summary contributes the SEGMENT ROSTER too
 * -- which decides whether an observed segment is declared in the cast
 * (refused) or not under its name (allowed). The block therefore exercises
 * BOTH worlds: the graph built with no cast (a server predating the roster:
 * every observed segment refused, conservatively), and -- through
 * `castInputFrom` itself -- the graph built with the REAL roster. This package
 * has no YAML reader, so the roster comes from `fixtures/real-segment-
 * rosters.json`, a checked-in copy of each twin config's `segments:` that
 * `tests/test_api_console.py` pins to the real files (the vendored-with-drift-
 * test pattern). It used to be built from a hand-copied list of the pruned
 * CIDRs -- the very list the test then expected back, which could not fail
 * (the second fix round's circular-test finding, LES-187).
 *
 * Node environment: the real-bundle block reads files (LES-260).
 */
import { existsSync, lstatSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import type { CastSegment, CastSummary, TopologyExport } from "../src/api/types";
import {
  claimOn,
  isRefusal,
  nextFreeSlash24,
  observedSegmentInputs,
  segmentClaims,
  segmentLandingNote,
  segmentNameFor,
} from "../src/edit/allocate";
import { buildRecord, strictCidr } from "../src/edit/records";
import { groupIntoSites } from "../src/layout/sites";
import { buildGraph, type ConsoleGraph, type GraphSegment } from "../src/model/graph";
import { twoOctetPrefix } from "../src/model/keys";
import { castInputFrom } from "../src/views/useGraph";

/** Every /24 of `10.80` except the ones named. */
function all10_80Except(...free: number[]): string[] {
  return Array.from({ length: 256 }, (_, n) => n)
    .filter((n) => !free.includes(n))
    .map((n) => `10.80.${n}.0/24`);
}

describe("nextFreeSlash24 -- the boundaries", () => {
  it("finds the LAST /24 of the prefix, and the one below it: the loop bound pinned from both sides", () => {
    // `selection.test.ts` pins all-256-taken -> null. That passes just as well
    // for a loop that stops at .254, which would then never offer .255.
    expect(nextFreeSlash24("10.80", all10_80Except(255))).toBe("10.80.255.0/24");
    expect(nextFreeSlash24("10.80", all10_80Except(254))).toBe("10.80.254.0/24");
    expect(nextFreeSlash24("10.80", all10_80Except())).toBeNull();
  });

  it("sees the one hole left by claims of every width from /17 to /24 -- no string of which is a /24", () => {
    const halving = [
      "10.80.0.0/17", // .0   - .127
      "10.80.128.0/18", // .128 - .191
      "10.80.192.0/19", // .192 - .223
      "10.80.224.0/20", // .224 - .239
      "10.80.240.0/21", // .240 - .247
      "10.80.248.0/22", // .248 - .251
      "10.80.252.0/23", // .252 - .253
      "10.80.254.0/24", // .254
    ];
    expect(nextFreeSlash24("10.80", halving)).toBe("10.80.255.0/24");
    // Close the hole and nothing is left.
    expect(nextFreeSlash24("10.80", [...halving, "10.80.255.0/24"])).toBeNull();
  });

  it("reads a claim with HOST BITS set as the network it names -- no more, no less -- whitespace aside", () => {
    // `10.80.0.200/24` names 10.80.0.0/24. Read as a range STARTING at .200 it
    // would spill into 10.80.1.x and push the suggestion to .2 -- so the
    // coordinate is chosen near the top of its /24, where masked and unmasked
    // readings give different answers (a claim at .77 cannot tell them apart).
    expect(nextFreeSlash24("10.80", ["10.80.0.200/24"])).toBe("10.80.1.0/24");
    expect(nextFreeSlash24("10.80", [" 10.80.0.200/24 ", " 10.80.1.0/24 "])).toBe("10.80.2.0/24");
  });

  it("allocates at the top and the bottom of the IPv4 space without overflow", () => {
    // `255.255.x` sits above 2^31: a signed shift anywhere in the arithmetic
    // would turn it negative and match nothing.
    expect(nextFreeSlash24("255.255", [])).toBe("255.255.0.0/24");
    expect(nextFreeSlash24("255.255", ["255.255.0.0/24", "255.255.1.0/25"])).toBe("255.255.2.0/24");
    expect(nextFreeSlash24("255.255", all10_80Except().map((c) => c.replace(/^10\.80\./, "255.255.")))).toBeNull();
    expect(nextFreeSlash24("0.0", ["0.0.0.0/24"])).toBe("0.0.1.0/24");
  });
});

describe("a prefill is staged AS-IS -- the site `+` gesture is '+, note, Stage'", () => {
  it("every suggestion, with its prefilled name, is a record the writer mirror accepts unchanged", () => {
    // Swept over how full the prefix is, including the last free /24.
    for (const taken of [0, 1, 9, 10, 99, 100, 128, 254, 255]) {
      const inPlay = Array.from({ length: taken }, (_, n) => `10.80.${n}.0/24`);
      const suggestion = nextFreeSlash24("10.80", inPlay);
      expect(suggestion, `${taken} taken`).toBe(`10.80.${taken}.0/24`);
      // The writer mirror's canonical form IS the suggestion: nothing to fix up.
      expect(strictCidr(suggestion!), `${taken} taken`).toBe(suggestion);
      const built = buildRecord({
        kind: "add-segment",
        cidr: suggestion!,
        name: segmentNameFor(suggestion!),
        zoneType: "vlan",
        note: "n",
      });
      expect(built.ok, `${taken} taken`).toBe(true);
      if (built.ok) {
        expect(built.staged.record).toEqual({
          kind: "segment",
          key: suggestion,
          name: `seg-10-80-${taken}-0`,
          zone_type: "vlan",
          note: "n",
        });
      }
    }
  });
});

/** The smallest `GraphSegment` `groupIntoSites` reads. */
function stub(cidr: string): GraphSegment {
  return { observedCidr: cidr, northSouth: { bytes: 0, flows: 0 } } as unknown as GraphSegment;
}

describe("segmentLandingNote says what groupIntoSites DOES -- the one-bucket-key claim, as a property", () => {
  // A twin with prefixes holding 1 (10.1, 192.168), 2 (10.20), 3 (10.80) and
  // 1 non-RFC-1918 (8.8) segment -- so a new CIDR can meet every sibling count
  // from 0 to 3, including the one that pulls a singleton OUT of misc.
  const twin = [
    "8.8.4.0/24",
    "10.1.10.0/24",
    "10.20.192.0/24",
    "10.20.193.0/24",
    "10.80.20.0/24",
    "10.80.72.0/24",
    "10.80.73.0/24",
    "192.168.0.0/24",
  ];
  const candidates = [
    "10.99.1.0/24", // 0 siblings: misc
    "172.16.0.0/24", // 0 siblings: misc
    "10.1.20.0/24", // 1 sibling, today in misc: the pair becomes a site
    "192.168.1.0/24", // 1 sibling, today in misc
    "8.8.8.0/24", // 1 sibling, not RFC 1918: still bucketed by two octets
    "10.20.0.0/24", // 2 siblings
    "10.80.0.0/24", // 3 siblings
  ];

  it("agrees for every sibling count from 0 to 3, including the singleton a new segment pulls out of misc", () => {
    // Restated for the fix round's B6 (LES-299): the note no longer says
    // "lands in" -- nothing places an added segment on the map yet -- and it
    // counts OBSERVED siblings. The property is unchanged: its group is exactly
    // the group `groupIntoSites` forms.
    for (const cidr of candidates) {
      const note = segmentLandingNote(cidr, twin);
      const sites = groupIntoSites([...twin, cidr].map(stub));
      const home = sites.find((s) => s.segments.some((seg) => seg.observedCidr === cidr))!;
      if (home.isMisc) {
        expect(note, cidr).toMatch(/^would sit in the shared misc group/);
        expect(note, cidr).not.toMatch(/would group with/);
      } else {
        const siblings = home.segments.length - 1;
        expect(home.label, cidr).toBe(twoOctetPrefix(cidr));
        expect(note, cidr).toBe(
          `would group with ${home.label}, beside ${siblings} observed segment${siblings === 1 ? "" : "s"}` +
            " — a segment added here is listed in the outline, not drawn on the map yet",
        );
      }
    }
    // Both branches were actually taken -- a candidate list that happened to
    // land every CIDR in misc (or none) would pass the loop vacuously.
    const homes = candidates.map((cidr) =>
      groupIntoSites([...twin, cidr].map(stub)).find((s) => s.segments.some((seg) => seg.observedCidr === cidr))!,
    );
    expect(homes.filter((h) => h.isMisc)).toHaveLength(2);
    expect(homes.filter((h) => !h.isMisc)).toHaveLength(5);
  });
});

// ===========================================================================
// The two shipped bundles
// ===========================================================================
const CAST_ROOT = new URL("../../../twins/", import.meta.url);

interface RealBundle {
  id: string;
  topology: string;
  /** The observed segments the cast does not declare -- an EXPECTATION, never
   *  an input: the roster the test runs on is the drift-tested fixture. The
   *  same list `tests/test_api_console.py` `_REAL_PRUNED` measures off the
   *  real twin config through the API. */
  pruned: string[];
  /** What an operator sees prefilled under each real site's `+`, measured
   *  off the file. Pinned so the browser check has a number to look for, and
   *  so a change to the allocator's ordering shows up as a diff. */
  expected: Record<string, string>;
}

const BUNDLES: RealBundle[] = [
  {
    id: "corpapcn-twin",
    topology: fileURLToPath(new URL("corpapcn-twin/generated/corpapcn-twin.topology.json", CAST_ROOT)),
    pruned: ["10.20.205.0/24", "10.5.0.0/24", "10.64.7.0/24", "10.80.31.0/24", "10.80.63.0/24", "172.16.0.0/24"],
    expected: { "10.20": "10.20.0.0/24", "10.80": "10.80.0.0/24" },
  },
  {
    // The pit bundle's `10.80` OBSERVES `10.80.0.0/24`, so its first free /24
    // is `.1` -- the one real case where the prefill must skip a taken `.0`.
    id: "pit-395a61f04e-1780405200",
    topology: fileURLToPath(
      new URL("pit-395a61f04e-1780405200/generated/pit-395a61f04e-1780405200.topology.json", CAST_ROOT),
    ),
    pruned: [],
    expected: { "10.20": "10.20.0.0/24", "10.80": "10.80.1.0/24" },
  },
];

/** Present, or absent -- never silently skipped through a DANGLING symlink
 *  (the pit export is a link into `corpapcn-disco/`; `layout.real.test.ts`). */
function available(path: string): boolean {
  try {
    lstatSync(path);
  } catch {
    return false;
  }
  if (!existsSync(path)) {
    throw new Error(`${path} is a DANGLING symlink -- restore its target rather than skip green`);
  }
  return true;
}

/** Each real cast's segment roster, as its twin config declares it. */
const REAL_ROSTERS = JSON.parse(
  readFileSync(fileURLToPath(new URL("./fixtures/real-segment-rosters.json", import.meta.url)), "utf8"),
) as Record<string, CastSegment[]>;

for (const bundle of BUNDLES) {
  describe.skipIf(!available(bundle.topology))(`the site + on the real ${bundle.id} bundle`, () => {
    const exportData = JSON.parse(readFileSync(bundle.topology, "utf8")) as TopologyExport;
    // No cast at all: the world of a server that predates the segment roster.
    const graph = buildGraph(exportData, { imprintId: bundle.id, deployMinOccupancy: 0.8 });
    const sites = groupIntoSites(graph.segments);
    const observedOf = (g: ConsoleGraph) => observedSegmentInputs(g.segments, g.castSegments, []);
    // The live app's claims with an empty edit store.
    const claims = segmentClaims(observedOf(graph), []);
    const inPlay = claims.map((c) => c.cidr);

    it("prefills, under every real site, a free /24 in that site's prefix that stages unchanged", () => {
      const real = sites.filter((s) => !s.isMisc);
      expect(real.map((s) => s.label)).toEqual(Object.keys(bundle.expected));
      for (const site of real) {
        const prefix = site.prefix;
        expect(prefix, site.key).toBe(site.label);
        const suggestion = nextFreeSlash24(prefix!, inPlay);
        expect(suggestion, site.key).toBe(bundle.expected[site.label]);
        expect(twoOctetPrefix(suggestion!), site.key).toBe(site.label);
        // Free of EVERYTHING in play -- not even an overlap to warn about.
        expect(claimOn(suggestion!, claims), site.key).toBeNull();
        expect(strictCidr(suggestion!), site.key).toBe(suggestion);
        // And it would group where the `+` was pressed, beside that site's own
        // observed count (B6's wording, LES-299).
        expect(segmentLandingNote(suggestion!, inPlay), site.key).toBe(
          `would group with ${site.label}, beside ${site.segments.length} observed segments` +
            " — a segment added here is listed in the outline, not drawn on the map yet",
        );
        const built = buildRecord({
          kind: "add-segment",
          cidr: suggestion!,
          name: segmentNameFor(suggestion!),
          zoneType: "vlan",
          note: "n",
        });
        expect(built.ok, site.key).toBe(true);
      }
    });

    it("gives the misc group no prefix, so its `+` prefills nothing rather than a wrong prefix", () => {
      const misc = sites.find((s) => s.isMisc);
      expect(misc).toBeDefined();
      expect(misc!.prefix).toBeNull();
      // It really is singletons from different prefixes (nothing to allocate in).
      const prefixes = misc!.segments.map((s) => twoOctetPrefix(s.observedCidr));
      expect(new Set(prefixes).size).toBe(misc!.segments.length);
    });

    it("with NO roster, refuses EVERY observed segment exactly, naming why -- swept, not sampled", () => {
      // Restated for A2 (LES-299): the claim no longer says "the twin" -- with
      // no roster, carried and pruned cannot be told apart, and the refusal
      // says THAT rather than blaming the twin.
      expect(graph.segments.length).toBeGreaterThan(30);
      expect(graph.castSegments).toBeNull();
      for (const seg of graph.segments) {
        const hit = claimOn(seg.observedCidr, claims);
        expect(hit?.exact, seg.observedCidr).toBe(true);
        expect(hit?.claim.source, seg.observedCidr).toBe("unpublished");
        expect(isRefusal(hit), seg.observedCidr).toBe(true);
      }
    });

    it("WITH the REAL roster, refuses exactly the declared segments and allows exactly the undeclared ones -- swept", () => {
      // The roster is the twin config's own (the drift-tested fixture); the
      // expectation (`bundle.pruned`) is a separate literal, so neither can be
      // derived from the other.
      const roster = REAL_ROSTERS[bundle.id];
      expect(roster, bundle.id).toBeDefined();
      // Through `castInputFrom` -- the seam the live app uses -- not a hand-made
      // `CastInput`.
      const cast = { deploy_min_occupancy: 0.8, conduit_roster: [], routers: [], segment_roster: roster } as unknown as CastSummary;
      const withRoster = buildGraph(exportData, castInputFrom(cast, bundle.id));
      const rosterClaims = segmentClaims(observedOf(withRoster), []);
      const refused: string[] = [];
      const allowed: string[] = [];
      for (const seg of withRoster.segments) {
        const hit = claimOn(seg.observedCidr, rosterClaims);
        expect(hit?.exact, seg.observedCidr).toBe(true);
        (isRefusal(hit) ? refused : allowed).push(seg.observedCidr);
      }
      expect(allowed.sort()).toEqual([...bundle.pruned].sort());
      expect(refused).toHaveLength(withRoster.segments.length - bundle.pruned.length);
      // ...and the suggestion still skips the undeclared ones: never an observed CIDR.
      for (const site of groupIntoSites(withRoster.segments).filter((s) => s.prefix !== null)) {
        const suggestion = nextFreeSlash24(site.prefix!, rosterClaims.map((c) => c.cidr))!;
        expect(withRoster.segmentByCidr.has(suggestion), site.key).toBe(false);
      }
    });
  });
}
