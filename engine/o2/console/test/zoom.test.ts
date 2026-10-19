import { describe, expect, it } from "vitest";

import {
  NO_FILTERS,
  anyDimmingFilter,
  clearFilters,
  filtersToQuery,
  parentOf,
  parseFilters,
  parseRoute,
  parseZoomPath,
  routeFor,
  zoomToPath,
  type Filters,
  type Zoom,
} from "../src/model/zoom";

const ZOOMS: Zoom[] = [
  { level: "catalog" },
  { level: "network", imprint: "corpapcn-golden" },
  { level: "segment", imprint: "corpapcn-golden", cidr: "10.80.20.0/24" },
  { level: "host", imprint: "corpapcn-golden", key: "address:10.1.10.222" },
  {
    level: "host",
    imprint: "corpapcn-golden",
    key: "hostname:axdc01",
    cidr: "10.80.20.0/24",
  },
  { level: "matrix", imprint: "corpapcn-golden" },
  { level: "matrix", imprint: "corpapcn-golden", cidr: "10.80.20.0/24" },
];

describe("route <-> zoom", () => {
  it("round-trips every level, keeping the segment context of a host", () => {
    for (const zoom of ZOOMS) {
      expect(parseZoomPath(zoomToPath(zoom)), zoomToPath(zoom)).toEqual(zoom);
    }
  });

  it("spells the four levels the way the work order does", () => {
    expect(zoomToPath({ level: "network", imprint: "corpapcn-golden" })).toBe(
      "/design/corpapcn-golden",
    );
    expect(
      zoomToPath({
        level: "segment",
        imprint: "corpapcn-golden",
        cidr: "10.80.20.0/24",
      }),
    ).toBe("/design/corpapcn-golden/segments/10.80.20.0-24");
    expect(
      zoomToPath({
        level: "host",
        imprint: "corpapcn-golden",
        key: "address:10.1.10.222",
      }),
    ).toBe("/design/corpapcn-golden/hosts/address:10.1.10.222");
    expect(zoomToPath({ level: "matrix", imprint: "corpapcn-golden" })).toBe(
      "/design/corpapcn-golden/matrix",
    );
  });

  it("writes a CIDR with a hyphen and never with %2F", () => {
    const path = zoomToPath({
      level: "segment",
      imprint: "x",
      cidr: "10.80.20.0/24",
    });
    expect(path).toContain("10.80.20.0-24");
    expect(path.toUpperCase()).not.toContain("%2F");
  });

  it("keeps a host key readable (a colon is legal in a path segment)", () => {
    expect(
      zoomToPath({ level: "host", imprint: "x", key: "address:10.1.10.222" }),
    ).toContain("address:10.1.10.222");
  });

  it("parses the nested host form from `15` §3 as well as the flat one", () => {
    expect(
      parseZoomPath("/design/x/segments/10.80.20.0-24/hosts/hostname:a"),
    ).toEqual({
      level: "host",
      imprint: "x",
      key: "hostname:a",
      cidr: "10.80.20.0/24",
    });
    expect(parseZoomPath("/design/x/hosts/hostname:a")).toEqual({
      level: "host",
      imprint: "x",
      key: "hostname:a",
    });
  });

  it("returns null for a path it does not recognize, rather than guessing", () => {
    for (const bad of [
      "/imprints",
      "/design/x/segments",
      "/design/x/segments/not-a-cidr-here",
      "/design/x/hosts",
      "/design/x/nonsense",
      "/elsewhere",
      "/design/x/segments/10.80.20.0-24/hosts",
      "/design/%zz",
    ]) {
      expect(parseZoomPath(bad), bad).toBeNull();
    }
  });

  it("walks up one level at a time (Escape / the breadcrumb)", () => {
    const host: Zoom = {
      level: "host",
      imprint: "x",
      key: "hostname:a",
      cidr: "10.80.20.0/24",
    };
    const segment = parentOf(host);
    expect(segment).toEqual({ level: "segment", imprint: "x", cidr: "10.80.20.0/24" });
    expect(parentOf(segment!)).toEqual({ level: "network", imprint: "x" });
    expect(parentOf({ level: "network", imprint: "x" })).toEqual({ level: "catalog" });
    expect(parentOf({ level: "catalog" })).toBeNull();
    // A host with no segment context goes up to the network, not nowhere.
    expect(parentOf({ level: "host", imprint: "x", key: "hostname:a" })).toEqual({
      level: "network",
      imprint: "x",
    });
  });
});

describe("filters", () => {
  it("parses the three filters into typed state", () => {
    const filters = parseFilters("?ghosts=1&class=imputed&occupancy_lt=0.8");
    expect(filters).toEqual({
      ghosts: true,
      evidenceClass: "imputed",
      occupancyLt: 0.8,
      changed: false,
      summary: false,
      eastWestAll: false,
      tileOrder: "hosts",
      expand: [],
      problems: [],
    });
  });

  it("spells the design note's `occupancy<0.8` as `occupancy_lt`", () => {
    expect(parseFilters("?occupancy_lt=0.5").occupancyLt).toBe(0.5);
    // The `<` spelling is not a query parameter at all: unknown keys are
    // ignored, and the value they would have set stays null.
    expect(parseFilters("?occupancy<0.5").occupancyLt).toBeNull();
  });

  it("round-trips through the canonical query string", () => {
    const filters: Filters = {
      ghosts: true,
      evidenceClass: "asserted",
      occupancyLt: 0.8,
      changed: false,
      summary: false,
      eastWestAll: false,
      tileOrder: "hosts",
      expand: [],
      problems: [],
    };
    expect(parseFilters(filtersToQuery(filters))).toEqual(filters);
    expect(filtersToQuery(NO_FILTERS)).toBe("");
    expect(parseFilters(filtersToQuery(NO_FILTERS))).toEqual(NO_FILTERS);
  });

  it("parses and round-trips the post-apply `?changed=1` filter (15 §12 O5)", () => {
    expect(parseFilters("?changed=1").changed).toBe(true);
    expect(parseFilters("?changed=0").changed).toBe(false);
    expect(parseFilters("").changed).toBe(false);
    // Emitted only when true, and reported (not silently dropped) when unusable.
    expect(filtersToQuery({ ghosts: false, evidenceClass: null, occupancyLt: null, changed: true, summary: false, eastWestAll: false, tileOrder: "hosts", expand: [], problems: [] })).toBe("?changed=1");
    expect(filtersToQuery(NO_FILTERS)).not.toContain("changed");
    expect(parseFilters("?changed=maybe").problems.join(" ")).toMatch(/changed=maybe/);
  });

  it("parses and round-trips the `?summary=1` view flag (R1)", () => {
    expect(parseFilters("?summary=1").summary).toBe(true);
    expect(parseFilters("?summary=0").summary).toBe(false);
    expect(parseFilters("").summary).toBe(false);
    expect(filtersToQuery({ ...NO_FILTERS, summary: true })).toBe("?summary=1");
    expect(filtersToQuery(NO_FILTERS)).not.toContain("summary");
    expect(parseFilters("?summary=maybe").problems.join(" ")).toMatch(/summary=maybe/);
    // It travels WITH the filters (one grammar, one query) without being one.
    const both = parseFilters("?ghosts=1&summary=1");
    expect(both.ghosts).toBe(true);
    expect(both.summary).toBe(true);
    expect(parseFilters(filtersToQuery(both))).toEqual(both);
  });

  it("parses and round-trips the `?ew=all` east-west budget (R4)", () => {
    // A NAMED budget, not a boolean: `all` or the default `top`. It is a VIEW
    // flag -- it dims nothing -- but unlike `?summary=` it changes what the
    // map DRAWS, which is why the UI says "N of M" beside it (LES-307).
    expect(parseFilters("?ew=all").eastWestAll).toBe(true);
    expect(parseFilters("?ew=top").eastWestAll).toBe(false);
    expect(parseFilters("?ew=ALL").eastWestAll).toBe(true);
    expect(parseFilters("").eastWestAll).toBe(false);
    expect(filtersToQuery({ ...NO_FILTERS, eastWestAll: true })).toBe("?ew=all");
    expect(filtersToQuery(NO_FILTERS)).not.toContain("ew=");
    // Reported, never silently read as one of the two (D2's rule).
    expect(parseFilters("?ew=1").problems.join(" ")).toMatch(/ew=1/);
    expect(parseFilters("?ew=1").eastWestAll).toBe(false);
    expect(parseFilters("?ew=").problems).toEqual([]);
    const both = parseFilters("?ghosts=1&ew=all");
    expect(both.ghosts).toBe(true);
    expect(both.eastWestAll).toBe(true);
    expect(parseFilters(filtersToQuery(both))).toEqual(both);
  });

  it("`anyDimmingFilter` ignores `?ew=all` -- a budget is not a filter (R4)", () => {
    expect(anyDimmingFilter({ ...NO_FILTERS, eastWestAll: true })).toBe(false);
  });

  it("`clearFilters` drops the filters and the emphasis flag, keeps the view flags (S2)", () => {
    // R6 added two more VIEW flags, and this is the fixture that proves they
    // joined the right set -- the exact trap LES-288 names, one flag later.
    const busy = parseFilters(
      "?ghosts=1&class=imputed&occupancy_lt=0.5&changed=1&summary=1&ew=all" +
        "&order=cidr&expand=site:10.80",
    );
    const cleared = clearFilters(busy);
    // R4: `ew` survives `clear` for the same reason `summary` does -- clearing
    // a class filter must not silently re-budget the map to its top 12. R6's
    // `order` and `expand` are the same kind of state: clearing a filter must
    // not re-rank the tiles or slam an opened group shut.
    expect(filtersToQuery(cleared)).toBe("?summary=1&ew=all&order=cidr&expand=site:10.80");
    expect(cleared.eastWestAll).toBe(true);
    expect(cleared.tileOrder).toBe("cidr");
    expect(cleared.expand).toEqual(["site:10.80"]);
    expect(cleared.ghosts).toBe(false);
    expect(cleared.evidenceClass).toBeNull();
    expect(cleared.occupancyLt).toBeNull();
    expect(cleared.changed).toBe(false);
    expect(cleared.summary).toBe(true);
    // Clearing an already-clear set is a no-op, which is what makes "would
    // clearing change the query?" a sound visibility test for the chip.
    expect(filtersToQuery(clearFilters(cleared))).toBe(filtersToQuery(cleared));
  });

  it("`anyDimmingFilter` is the DIMMING set only -- not `changed`, not `summary` (S2)", () => {
    expect(anyDimmingFilter(NO_FILTERS)).toBe(false);
    expect(anyDimmingFilter({ ...NO_FILTERS, summary: true })).toBe(false);
    expect(anyDimmingFilter({ ...NO_FILTERS, changed: true })).toBe(false);
    expect(anyDimmingFilter({ ...NO_FILTERS, ghosts: true })).toBe(true);
    expect(anyDimmingFilter({ ...NO_FILTERS, evidenceClass: "imputed" })).toBe(true);
    expect(anyDimmingFilter({ ...NO_FILTERS, occupancyLt: 0.5 })).toBe(true);
  });

  it("REPORTS an unusable value instead of silently ignoring it", () => {
    const filters = parseFilters("?ghosts=maybe&class=nonsense&occupancy_lt=2");
    expect(filters.ghosts).toBe(false);
    expect(filters.evidenceClass).toBeNull();
    expect(filters.occupancyLt).toBeNull();
    expect(filters.problems).toHaveLength(3);
    expect(filters.problems.join(" ")).toMatch(/class=nonsense/);
  });
});

describe("the whole route", () => {
  it("parses path and query together", () => {
    const route = parseRoute(
      "/design/corpapcn-golden/segments/10.80.20.0-24?ghosts=1&class=observed",
    );
    expect(route.zoom).toEqual({
      level: "segment",
      imprint: "corpapcn-golden",
      cidr: "10.80.20.0/24",
    });
    expect(route.filters.ghosts).toBe(true);
    expect(route.filters.evidenceClass).toBe("observed");
  });

  it("normalizes `?view=matrix` (the design note's spelling) to /matrix", () => {
    expect(parseRoute("/design/x?view=matrix").zoom).toEqual({
      level: "matrix",
      imprint: "x",
    });
    expect(parseRoute("/design/x/segments/10.80.20.0-24?view=matrix").zoom).toEqual({
      level: "matrix",
      imprint: "x",
      cidr: "10.80.20.0/24",
    });
    expect(parseRoute("/design/x?view=nonsense").filters.problems).toHaveLength(1);
  });

  it("routeFor is the inverse of parseRoute, filters and all", () => {
    const filters: Filters = {
      ghosts: true,
      evidenceClass: "imputed",
      occupancyLt: 0.8,
      changed: false,
      summary: false,
      eastWestAll: false,
      tileOrder: "hosts",
      expand: [],
      problems: [],
    };
    for (const zoom of ZOOMS) {
      const url = routeFor(zoom, filters);
      const back = parseRoute(url);
      expect(back.zoom, url).toEqual(zoom);
      expect(back.filters, url).toEqual(filters);
    }
  });
});

// ===========================================================================
// Adversarial inputs (tester pass).
//
// `parseZoomPath` / `parseFilters` are the SPA's only untrusted-string
// parsers: everything above them (the pane, the fetch, the breadcrumb) reads
// their output as fact, and the input is whatever a browser, a bookmark or a
// pasted link supplies. The cases above cover the shapes the canvas EMITS;
// these cover the shapes it RECEIVES.
// ===========================================================================
describe("adversarial route inputs", () => {
  it("round-trips an IPv6 CIDR in the hyphen form (the docstring's claim)", () => {
    // `cidrToRoute`'s rule is "the LAST slash becomes the LAST hyphen", which
    // it says works unchanged for IPv6 because an address half has colons but
    // never a hyphen. `keys.test.ts` pins the encoder; this pins the ROUTE,
    // where a colon-rich segment also has to survive the path split.
    for (const cidr of ["fd00::/64", "2001:db8:1::/48", "fe80::/10", "::/0"]) {
      const path = zoomToPath({ level: "segment", imprint: "x", cidr });
      expect(path.toUpperCase(), path).not.toContain("%2F");
      expect(parseZoomPath(path), path).toEqual({
        level: "segment",
        imprint: "x",
        cidr,
      });
      // ... and as the scope of a matrix and of a nested host, the two
      // routes that carry a CIDR as a NON-final segment.
      expect(
        parseZoomPath(`/design/x/segments/${path.split("/").pop()}/matrix`),
      ).toEqual({ level: "matrix", imprint: "x", cidr });
    }
  });

  it("carries a `mac:` key, all six colons of it, flat and nested", () => {
    const key = "mac:aa:bb:cc:dd:ee:ff";
    expect(parseZoomPath(zoomToPath({ level: "host", imprint: "x", key }))).toEqual({
      level: "host",
      imprint: "x",
      key,
    });
    expect(
      parseZoomPath(`/design/x/segments/10.80.20.0-24/hosts/${key}`),
    ).toEqual({ level: "host", imprint: "x", key, cidr: "10.80.20.0/24" });
  });

  it("ERRORS on a malformed % escape rather than routing to the root", () => {
    // The trap this closes: `decodeURIComponent` throws, and an earlier shape
    // that caught it into `[]` sent every malformed path to the catalog --
    // rendering a view the operator never asked for. `null` (unrecognized)
    // and `{level:"catalog"}` (the root) are different answers.
    for (const bad of [
      "/design/%zz",
      "/design/%",
      "/%",
      "/design/x/hosts/%E0%A4%A",     // a truncated UTF-8 sequence
      "/design/x/segments/10.80.20.0-24/hosts/%C0",
    ]) {
      expect(parseZoomPath(bad), bad).toBeNull();
      // ... and through the whole-route entry point the shell actually calls.
      expect(parseRoute(bad).zoom, bad).toBeNull();
      expect(parseRoute(`${bad}?ghosts=1`).zoom, bad).toBeNull();
    }
    // The Design root is still the catalog -- the distinction is the point.
    expect(parseZoomPath("/design")).toEqual({ level: "catalog" });
  });

  it("reports every unusable `occupancy_lt`, naming the value", () => {
    for (const raw of ["NaN", "-1", "2", "abc", "Infinity", "1.0000001", "0.5.5"]) {
      const filters = parseFilters(`?occupancy_lt=${encodeURIComponent(raw)}`);
      expect(filters.occupancyLt, raw).toBeNull();
      expect(filters.problems, raw).toHaveLength(1);
      expect(filters.problems[0], raw).toContain(raw);
    }
    // The boundaries are IN: `occupancy_lt=1` (everything) and `=0` (nothing)
    // are both legal filters an operator can mean.
    expect(parseFilters("?occupancy_lt=1").occupancyLt).toBe(1);
    expect(parseFilters("?occupancy_lt=0").occupancyLt).toBe(0);
    expect(parseFilters("?occupancy_lt=.5").occupancyLt).toBe(0.5);
  });

  it("reports an unknown `class=`, and matches the six exactly", () => {
    for (const raw of ["nonsense", "Observed", "OBSERVED", "observed ", "defaulted"]) {
      const filters = parseFilters(`?class=${encodeURIComponent(raw)}`);
      expect(filters.evidenceClass, raw).toBeNull();
      expect(filters.problems, raw).toHaveLength(1);
    }
    // `defaulted` above is the RENAMED class (`14` §2 renamed it `compiler`);
    // accepting the old spelling silently would show a filtered view under a
    // vocabulary that no longer exists.
    expect(parseFilters("?class=compiler").evidenceClass).toBe("compiler");
    // An empty value is "no filter", not an error -- `?class=` is what a
    // cleared select box emits.
    expect(parseFilters("?class=").problems).toEqual([]);
    expect(parseFilters("?class=").evidenceClass).toBeNull();
  });

  it("takes the FIRST of a duplicated query key, deterministically, AND says so", () => {
    // A hand-edited URL can repeat a key. First-wins is `URLSearchParams.get`
    // semantics; what matters here is that it is deterministic and that the
    // WINNER is still validated -- a duplicate cannot smuggle a bad value in.
    expect(parseFilters("?ghosts=1&ghosts=0").ghosts).toBe(true);
    expect(parseFilters("?ghosts=0&ghosts=1").ghosts).toBe(false);
    expect(parseFilters("?class=observed&class=bogus").evidenceClass).toBe("observed");
    expect(parseFilters("?occupancy_lt=0.5&occupancy_lt=abc").occupancyLt).toBe(0.5);

    // D2: the DROPPED value is reported too. Silently discarding it left an
    // operator looking at a view filtered by a value they had replaced, with
    // nothing on screen to say the second one was ignored.
    const dropped = parseFilters("?class=observed&class=bogus").problems;
    expect(dropped).toHaveLength(1);
    expect(dropped[0]).toMatch(/appears 2 times/);

    // ... and when the WINNER is also unusable, both facts are reported.
    const both = parseFilters("?class=bogus&class=observed").problems;
    expect(both).toHaveLength(2);
    expect(both.join(" ")).toMatch(/appears 2 times/);
    expect(both.join(" ")).toMatch(/not one of the six/);
  });

  it("tolerates a trailing slash and doubled slashes at every level", () => {
    // A browser, a proxy and an operator's paste all produce these; the URL
    // is the zoom, so `/design/x/` must be the same view as `/design/x`.
    const same: Array<[string, string]> = [
      ["/design/x/", "/design/x"],
      ["/design/x/segments/10.80.20.0-24/", "/design/x/segments/10.80.20.0-24"],
      ["/design/x/hosts/hostname:a/", "/design/x/hosts/hostname:a"],
      ["/design/x/matrix/", "/design/x/matrix"],
      ["//design//x//", "/design/x"],
    ];
    for (const [lenient, canonical] of same) {
      expect(parseZoomPath(lenient), lenient).toEqual(parseZoomPath(canonical));
      expect(parseZoomPath(lenient), lenient).not.toBeNull();
    }
  });

  it("keeps a key with a path-breaking character readable AND parseable", () => {
    // `encodeSegment` escapes only `% / ? # <space>` so `address:10.1.0.14`
    // stays legible. Anything it does escape must still round-trip.
    for (const key of [
      "hostname:a b",
      "hostname:100%",
      "hostname:a/b",
      "hostname:a?b",
      "hostname:a#b",
    ]) {
      const path = zoomToPath({ level: "host", imprint: "x", key });
      expect(parseZoomPath(path), key).toEqual({ level: "host", imprint: "x", key });
    }
  });

  it("does not accept %2F as the CIDR separator (the hyphen is the spelling)", () => {
    // `15` §7 chose the hyphen because proxies normalize or reject an encoded
    // slash. A URL that uses one is unrecognized, not silently re-interpreted.
    expect(parseZoomPath("/design/x/segments/10.80.20.0%2F24")).toBeNull();
  });
});

describe("query values that are not what they look like (D1/D3)", () => {
  it("refuses everything `Number()` would accept but a URL should not", () => {
    // `+` decodes to a SPACE, which `Number(" ")` reads as 0 -- a filter that
    // hides everything, from a query the operator thinks is a typo. The rest
    // are the same family: a shape check has to run BEFORE the conversion.
    for (const raw of ["+", "%20", "0x1", "1e-1", "Infinity", " 0.5", "0.5 ", "-0.5", ".5.5"]) {
      const filters = parseFilters(`?occupancy_lt=${raw}`);
      expect(filters.occupancyLt, raw).toBeNull();
      expect(filters.problems, raw).toHaveLength(1);
      expect(filters.problems[0], raw).toMatch(/decimal fraction/);
    }
  });

  it("still accepts the plain decimal forms", () => {
    for (const [raw, value] of [
      ["0.8", 0.8],
      ["0", 0],
      ["1", 1],
      [".5", 0.5],
      ["0.05", 0.05],
    ] as Array<[string, number]>) {
      const filters = parseFilters(`?occupancy_lt=${raw}`);
      expect(filters.occupancyLt, raw).toBe(value);
      expect(filters.problems, raw).toEqual([]);
    }
  });

  it("reports `?view=matrix` where there is no matrix, instead of dropping it", () => {
    // The same input class was answered two ways: an unknown `?view=` was
    // reported, while `?view=matrix` on a level with no matrix vanished.
    const onHost = parseRoute("/design/x/hosts/hostname:a?view=matrix");
    expect(onHost.zoom).toEqual({ level: "host", imprint: "x", key: "hostname:a" });
    expect(onHost.filters.problems.join(" ")).toMatch(/view=matrix does not apply/);

    const onCatalog = parseRoute("/design?view=matrix");
    expect(onCatalog.zoom).toEqual({ level: "catalog" });
    expect(onCatalog.filters.problems.join(" ")).toMatch(/view=matrix does not apply/);

    // ... and where it DOES apply, it still normalizes silently.
    expect(parseRoute("/design/x?view=matrix").filters.problems).toEqual([]);
    expect(parseRoute("/design/x/matrix?view=matrix").filters.problems).toEqual([]);
  });

  it("reports a duplicated `view` key like any other", () => {
    const route = parseRoute("/design/x?view=matrix&view=map");
    expect(route.zoom).toEqual({ level: "matrix", imprint: "x" });
    expect(route.filters.problems.join(" ")).toMatch(/\?view= appears 2 times/);
  });
});
