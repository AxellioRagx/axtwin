/**
 * S5.1.a's two PURE modules, without a browser: what is selected
 * (`views/selection.ts`) and where a new segment goes (`edit/allocate.ts`).
 *
 * These are the rules the UI rests on -- "opening an element selects it", and
 * "the site `+` prefills the next free /24 and refuses a taken one" -- so they
 * are tested as functions rather than only through the rendered inspector.
 */
import { describe, expect, it } from "vitest";

import {
  resolveSelection,
  sameSelection,
  selectionFromFocus,
  type Focus,
  type Selection,
} from "../src/views/selection";
import {
  addedSegmentsOf,
  castStandingOf,
  claimMessage,
  claimOn,
  defaultZoneType,
  isRefusal,
  nameClaimMessage,
  nameClaimOn,
  nextFreeSlash24,
  observedSegmentInputs,
  segmentClaims,
  segmentLandingNote,
  segmentNameClaims,
  segmentNameFor,
  segmentNamePrefill,
  type SegmentClaim,
  type StoreEntryInput,
} from "../src/edit/allocate";
import { groupIntoSites } from "../src/layout/sites";

describe("resolveSelection -- an explicit selection wins, the URL is the fallback", () => {
  it("returns the explicit in-pane selection over the zoom's own subject", () => {
    const focus: Focus = { kind: "segment", cidr: "10.80.31.0/24" };
    const zoom = { level: "host", key: "hostname:axdc01", cidr: "10.80.20.0/24" };
    expect(resolveSelection(zoom, focus)).toEqual({ kind: "segment", cidr: "10.80.31.0/24" });
  });

  it("falls back to the element the URL names -- which is how a COLLAPSED segment is selected", () => {
    // R6's per-group budget can leave a segment with no tile to click. The
    // outline, search and the matrix still list every segment, and each of them
    // OPENS the segment -- so the fallback is what gives a collapsed segment
    // exactly the same actions as a drawn one, with no tile involved.
    expect(resolveSelection({ level: "segment", cidr: "10.20.195.0/24" }, null)).toEqual({
      kind: "segment",
      cidr: "10.20.195.0/24",
    });
    expect(resolveSelection({ level: "host", key: "address:10.80.20.5" }, null)).toEqual({
      kind: "host",
      key: "address:10.80.20.5",
    });
    // A segment-scoped matrix names a segment too.
    expect(resolveSelection({ level: "matrix", cidr: "10.80.20.0/24" }, null)).toEqual({
      kind: "segment",
      cidr: "10.80.20.0/24",
    });
  });

  it("is null where the URL names no element and nothing is selected", () => {
    expect(resolveSelection({ level: "network" }, null)).toBeNull();
    expect(resolveSelection({ level: "matrix" }, null)).toBeNull();
    expect(resolveSelection({ level: "catalog" }, null)).toBeNull();
    expect(resolveSelection(null, null)).toBeNull();
  });

  it("declines the matrix cell and the O7 drawers -- they are DETAIL, not edit subjects", () => {
    expect(resolveSelection({ level: "network" }, { kind: "cell", row: "a", col: "b" })).toBeNull();
    expect(resolveSelection({ level: "network" }, { kind: "unplaced" })).toBeNull();
    expect(resolveSelection({ level: "network" }, { kind: "nonlocal" })).toBeNull();
  });

  it("carries the site `+`'s own selection through unchanged", () => {
    const focus: Focus = { kind: "site-add", siteKey: "site:10.80" };
    expect(resolveSelection({ level: "network" }, focus)).toEqual(focus);
  });
});

describe("selectionFromFocus -- the explicit selection, by IDENTITY", () => {
  it("returns the very focus object, so a new gesture is distinguishable from a re-render", () => {
    // The inspector moves keyboard focus when this object CHANGES; a copy made
    // per call would change on every render and steal focus per keystroke.
    const focus: Focus = { kind: "segment", cidr: "10.80.20.0/24" };
    expect(selectionFromFocus(focus)).toBe(focus);
  });

  it("is null for no focus and for the detail kinds", () => {
    expect(selectionFromFocus(null)).toBeNull();
    expect(selectionFromFocus({ kind: "cell", row: "a", col: "b" })).toBeNull();
    expect(selectionFromFocus({ kind: "unplaced" })).toBeNull();
    expect(selectionFromFocus({ kind: "nonlocal" })).toBeNull();
  });
});

describe("sameSelection -- when `clear` would change nothing", () => {
  const seg = (cidr: string): Selection => ({ kind: "segment", cidr });
  it("matches the same subject by value, not by identity", () => {
    expect(sameSelection(seg("10.80.20.0/24"), seg("10.80.20.0/24"))).toBe(true);
    expect(sameSelection({ kind: "host", key: "hostname:axdc01" }, { kind: "host", key: "hostname:axdc01" })).toBe(true);
    expect(
      sameSelection({ kind: "site-add", siteKey: "site:10.80" }, { kind: "site-add", siteKey: "site:10.80" }),
    ).toBe(true);
  });

  it("differs across subjects, kinds and null", () => {
    expect(sameSelection(seg("10.80.20.0/24"), seg("10.80.72.0/24"))).toBe(false);
    expect(sameSelection(seg("10.80.20.0/24"), { kind: "host", key: "address:10.80.20.0" })).toBe(false);
    expect(sameSelection({ kind: "site-add", siteKey: null }, seg("10.80.20.0/24"))).toBe(false);
    expect(sameSelection({ kind: "site-add", siteKey: null }, { kind: "site-add", siteKey: "site:10.80" })).toBe(false);
    expect(sameSelection(seg("10.80.20.0/24"), null)).toBe(false);
    expect(sameSelection(null, null)).toBe(true);
  });
});

describe("nextFreeSlash24 -- the site `+`'s prefill", () => {
  it("skips the taken third octets and returns the first free one", () => {
    expect(nextFreeSlash24("10.80", ["10.80.0.0/24", "10.80.1.0/24"])).toBe("10.80.2.0/24");
    // The shipped fixture's `10.80` holds .20 and .72, so .0 is free.
    expect(nextFreeSlash24("10.80", ["10.80.20.0/24", "10.80.72.0/24"])).toBe("10.80.0.0/24");
  });

  it("ignores CIDRs in another prefix entirely", () => {
    expect(nextFreeSlash24("10.80", ["10.20.0.0/24", "192.168.0.0/24"])).toBe("10.80.0.0/24");
  });

  it("treats a /24 as occupied by ANY overlap, whatever the other side's prefix length", () => {
    // Narrower: two /25s inside the .0 octet.
    expect(nextFreeSlash24("10.80", ["10.80.0.0/25", "10.80.0.128/25"])).toBe("10.80.1.0/24");
    expect(nextFreeSlash24("10.80", ["10.80.0.128/25"])).toBe("10.80.1.0/24");
    // A /32 is an overlap too.
    expect(nextFreeSlash24("10.80", ["10.80.0.7/32"])).toBe("10.80.1.0/24");
    // Wider: a /23 spans .0 AND .1; a /22 spans .0 through .3.
    expect(nextFreeSlash24("10.80", ["10.80.0.0/23"])).toBe("10.80.2.0/24");
    expect(nextFreeSlash24("10.80", ["10.80.0.0/22"])).toBe("10.80.4.0/24");
    // A /16 over the prefix, or anything wider that contains it, leaves nothing.
    expect(nextFreeSlash24("10.80", ["10.80.0.0/16"])).toBeNull();
    expect(nextFreeSlash24("10.80", ["10.0.0.0/8"])).toBeNull();
    expect(nextFreeSlash24("10.80", ["0.0.0.0/0"])).toBeNull();
  });

  it("sweeps every prefix length over the .0 octet: occupied exactly when the ranges meet", () => {
    // LES-305: sweep the parameter rather than sampling it. A CIDR at
    // 10.80.0.0/p occupies the first /24 for every p (it always contains
    // 10.80.0.0); one at 10.80.1.0/p does so only when p <= 23 (the network
    // is masked back to 10.80.0.0). Both a violating and a clean coordinate
    // are pinned at each length.
    for (let p = 16; p <= 32; p++) {
      expect(nextFreeSlash24("10.80", [`10.80.0.0/${p}`]), `10.80.0.0/${p}`).not.toBe("10.80.0.0/24");
      const at1 = nextFreeSlash24("10.80", [`10.80.1.0/${p}`]);
      if (p <= 23) expect(at1, `10.80.1.0/${p}`).not.toBe("10.80.0.0/24");
      else expect(at1, `10.80.1.0/${p}`).toBe("10.80.0.0/24");
    }
  });

  it("ignores values that are not IPv4 CIDRs -- they cannot overlap a /24", () => {
    expect(nextFreeSlash24("10.80", ["2001:db8::/32", "not-a-cidr", ""])).toBe("10.80.0.0/24");
  });

  it("returns null -- never a wrong suggestion -- when all 256 are taken, or the prefix is not one", () => {
    const all = Array.from({ length: 256 }, (_, n) => `10.80.${n}.0/24`);
    expect(nextFreeSlash24("10.80", all)).toBeNull();
    expect(nextFreeSlash24("misc", [])).toBeNull();
    expect(nextFreeSlash24("10.300", [])).toBeNull();
  });
});

/** A roster entry, relocated the way real casts are. */
const cast = (name: string, i: number, zone: string | null = "simple") => ({ name, cidr: `10.200.${i}.0/24`, zone_type: zone });
/** An observed segment named the producer's way. */
const seen = (cidr: string) => ({ observedCidr: cidr, name: segmentNameFor(cidr) });
const saved = (key: string, name: string): StoreEntryInput => ({
  block: "add",
  state: "saved",
  record: { kind: "segment", key, name },
});

describe("castStandingOf -- where an observed segment stands against the cast (SF1: a rename on record)", () => {
  const roster = [cast("seg-10-80-20-0", 0), cast("ot-lab", 1, "vlan")];

  it("carried under its own name; absent -- renamed OR pruned -- when the roster names neither", () => {
    const standings = observedSegmentInputs([seen("10.80.20.0/24"), seen("10.80.72.0/24")], roster, []);
    expect(standings.map((o) => o.standing.status)).toEqual(["carried", "absent"]);
    expect(standings[0]!.standing.castSegment).toEqual(roster[0]);
    expect(standings[1]!.standing.castSegment).toBeNull();
  });

  it("RENAMED when this file records an add naming it as a segment the roster declares -- the join maps through it", () => {
    // `add: segment 10.80.72.0/24 name: ot-lab` -- the compiler applies the name
    // to the live observed segment, so the cast declares `ot-lab`, not
    // `seg-10-80-72-0`.
    const [standing] = observedSegmentInputs([seen("10.80.72.0/24")], roster, [saved("10.80.72.0/24", "ot-lab")]);
    expect(standing!.standing.status).toBe("renamed");
    expect(standing!.standing.castSegment).toEqual(roster[1]);
    expect(standing!.standing.record).toEqual({ state: "saved", name: "ot-lab" });
  });

  it("a record naming something the roster does NOT declare is not a rename -- still absent, with the record kept", () => {
    const [standing] = observedSegmentInputs([seen("10.80.72.0/24")], roster, [saved("10.80.72.0/24", "brand-new")]);
    expect(standing!.standing.status).toBe("absent");
    expect(standing!.standing.record?.name).toBe("brand-new");
  });

  it("unpublished when there is no roster, whatever the file says", () => {
    const standing = castStandingOf(null, { ...seen("10.80.72.0/24"), status: "unpublished" }, [saved("10.80.72.0/24", "ot-lab")]);
    expect(standing.status).toBe("unpublished");
  });
});

describe("segmentClaims -- who holds a CIDR (carried, absent, unpublished, or the store)", () => {
  it("names each observed segment by its standing, then every `add: segment` by its state -- never a host or a conduit", () => {
    const entries: StoreEntryInput[] = [
      { block: "add", state: "pending", record: { kind: "segment", key: "10.80.31.0/24" } },
      { block: "add", state: "saved", record: { kind: "segment", key: "10.80.32.0/24" } },
      { block: "add", state: "applied", record: { kind: "segment", key: "10.80.33.0/24" } },
      { block: "add", state: "pending", record: { kind: "host", key: "address:10.80.31.5" } },
      { block: "add", state: "pending", record: { kind: "conduit", key: "a|b" } },
      { block: "suppress", state: "pending", record: { key: "address:10.80.20.5" } },
    ];
    const roster = [cast("seg-10-80-20-0", 0)];
    expect(
      segmentClaims(observedSegmentInputs([seen("10.80.20.0/24"), seen("10.80.63.0/24")], roster, entries), entries),
    ).toEqual([
      { cidr: "10.80.20.0/24", source: "cast" },
      { cidr: "10.80.63.0/24", source: "absent" },
      { cidr: "10.80.31.0/24", source: "pending" },
      { cidr: "10.80.32.0/24", source: "saved" },
      { cidr: "10.80.33.0/24", source: "applied" },
    ]);
    expect(segmentClaims(observedSegmentInputs([seen("10.5.0.0/24")], null, []), [])).toEqual([
      { cidr: "10.5.0.0/24", source: "unpublished" },
    ]);
  });
});

describe("claimOn / isRefusal / claimMessage -- what is really taken, with what the compiler really does", () => {
  const claims: SegmentClaim[] = [
    { cidr: "10.80.20.0/24", source: "cast" },
    { cidr: "10.80.31.0/24", source: "absent" },
    { cidr: "10.5.0.0/24", source: "unpublished" },
    { cidr: "10.80.0.0/24", source: "pending" },
    { cidr: "10.80.1.0/24", source: "saved" },
    { cidr: "10.80.2.0/24", source: "applied" },
  ];

  it("REFUSES a segment declared in the cast, and points at the gesture that is wanted instead", () => {
    const hit = claimOn(" 10.80.20.0/24 ", claims);
    expect(hit).toEqual({ claim: claims[0], exact: true });
    expect(isRefusal(hit)).toBe(true);
    expect(claimMessage("10.80.20.0/24", hit!)).toBe(
      "10.80.20.0/24 is already declared in the cast — select it and use Add host here",
    );
  });

  it("a RENAMED carried segment is refused under the name the cast gives it", () => {
    const hit = claimOn("10.80.72.0/24", [{ cidr: "10.80.72.0/24", source: "cast", castName: "ot-lab" }]);
    expect(isRefusal(hit)).toBe(true);
    expect(claimMessage("10.80.72.0/24", hit!)).toBe(
      "10.80.72.0/24 is already declared in the cast (as ot-lab) — select it and use Add host here",
    );
  });

  it("ALLOWS an add of an observed segment the cast does not declare -- and says what the compiler does, not 'restores'", () => {
    // Restated for the second fix round's MF2 (LES-299): the compiler skips an
    // add of an OBSERVED CIDR -- it only sets that segment's name and zone type
    // -- and keeps a segment only when a host is placed on it. "Adding it
    // restores that segment" was false. And it is not called "pruned": the cast
    // joins by name, so renamed and pruned read the same (SF1).
    const hit = claimOn("10.80.31.0/24", claims);
    expect(hit).toEqual({ claim: claims[1], exact: true });
    expect(isRefusal(hit)).toBe(false);
    const words = claimMessage("10.80.31.0/24", hit!);
    expect(words).toBe(
      "10.80.31.0/24 is observed, but the installed cast does not declare it under this name — renamed or pruned " +
        "(the cast joins by name). An add sets its name and zone type, but the compiler keeps a segment only when a " +
        "host is placed on it: select it and use Add host here",
    );
    expect(words).not.toMatch(/restor/);
  });

  it("with no roster published, refuses and says WHY -- the site + refuses a taken CIDR -- rather than blaming the twin", () => {
    const hit = claimOn("10.5.0.0/24", claims);
    expect(isRefusal(hit)).toBe(true);
    const words = claimMessage("10.5.0.0/24", hit!);
    expect(words).toMatch(/does not publish the cast's segment roster/);
    expect(words).toMatch(/the site \+ refuses a taken CIDR$/);
    expect(words).not.toMatch(/risk a duplicate|already declared in the cast/);
  });

  it("refuses a CIDR the edit store already holds, in the store's own words for each state", () => {
    expect(claimMessage("10.80.0.0/24", claimOn("10.80.0.0/24", claims)!)).toBe(
      "10.80.0.0/24 is already claimed by a pending add (staged in this browser, not saved yet)",
    );
    // SAVED is "written, effect unknown" -- `edit/store.ts`'s semantics -- not
    // "not applied yet", which stops being true the moment an apply runs.
    const savedWords = claimMessage("10.80.1.0/24", claimOn("10.80.1.0/24", claims)!);
    expect(savedWords).toBe(
      "10.80.1.0/24 is already claimed by a saved add (written to the assertions file; its effect on the cast is not known)",
    );
    expect(savedWords).not.toMatch(/not applied/);
    expect(claimMessage("10.80.2.0/24", claimOn("10.80.2.0/24", claims)!)).toMatch(/applied add/);
    for (const cidr of ["10.80.0.0/24", "10.80.1.0/24", "10.80.2.0/24"]) {
      expect(isRefusal(claimOn(cidr, claims)), cidr).toBe(true);
    }
  });

  it("an absent segment ALREADY added by a pending record reports the add -- the refusal wins", () => {
    const both: SegmentClaim[] = [
      { cidr: "10.80.31.0/24", source: "absent" },
      { cidr: "10.80.31.0/24", source: "pending" },
    ];
    const hit = claimOn("10.80.31.0/24", both);
    expect(hit?.claim.source).toBe("pending");
    expect(isRefusal(hit)).toBe(true);
  });

  it("reports an OVERLAP as an overlap, naming the real downstream check -- never as a refusal", () => {
    const narrower = claimOn("10.80.20.128/25", claims);
    expect(narrower).toEqual({ claim: claims[0], exact: false });
    expect(isRefusal(narrower)).toBe(false);
    const words = claimMessage("10.80.20.128/25", narrower!);
    expect(words).toMatch(/^10\.80\.20\.128\/25 overlaps 10\.80\.20\.0\/24, a segment declared in the cast/);
    // B9: the check that exists downstream, and when it does not apply.
    expect(words).toMatch(/NET-010/);
    expect(words).toMatch(/--relocate/);
  });

  it("does not warn about overlapping a segment the cast does not declare -- it is not in the twin", () => {
    expect(claimOn("10.80.31.128/25", claims)).toBeNull();
  });

  it("is null for a free CIDR, an empty value, and a non-IPv4 value that matches nothing", () => {
    expect(claimOn("10.80.21.0/24", claims)).toBeNull();
    expect(claimOn("", claims)).toBeNull();
    expect(claimOn("   ", claims)).toBeNull();
    expect(claimOn("2001:db8::/32", claims)).toBeNull();
    // ...but a non-IPv4 value still matches itself exactly.
    expect(claimOn("2001:db8::/32", [{ cidr: "2001:db8::/32", source: "cast" }])).toEqual({
      claim: { cidr: "2001:db8::/32", source: "cast" },
      exact: true,
    });
  });

  it("the allocator never suggests an observed CIDR -- one the cast does not declare included", () => {
    const inPlay = segmentClaims(observedSegmentInputs([seen("10.80.0.0/24")], [], []), []).map((c) => c.cidr);
    expect(nextFreeSlash24("10.80", inPlay)).toBe("10.80.1.0/24");
  });
});

describe("defaultZoneType -- the zone a new segment starts at (ZT)", () => {
  it("is the SITE's dominant declared zone type, then the whole cast's, then simple -- saying which", () => {
    // Restated (LES-299): the last resort is `simple`, the twin config's own
    // default -- never `vlan`, which one segment would make the whole twin's
    // zone, and a VLAN zone rejects SNAT egress.
    expect(defaultZoneType(["simple", "simple", "vlan"], ["vlan", "vlan", "vlan"])).toEqual({ zone: "simple", source: "site" });
    expect(defaultZoneType([], ["vlan", "simple", "vlan"])).toEqual({ zone: "vlan", source: "twin" });
    expect(defaultZoneType([null, null], [null])).toEqual({ zone: "simple", source: "unpublished" });
    expect(defaultZoneType([], [])).toEqual({ zone: "simple", source: "unpublished" });
  });

  it("breaks a tie deterministically -- the earlier of ZONE_TYPES (simple) -- and ignores a value outside the closed set", () => {
    expect(defaultZoneType(["vlan", "simple"], [])).toEqual({ zone: "simple", source: "site" });
    expect(defaultZoneType(["vlan", "simple"], []).zone).toBe(defaultZoneType(["simple", "vlan"], []).zone);
    expect(defaultZoneType(["evpn", "evpn", "vlan"], [])).toEqual({ zone: "vlan", source: "site" });
  });
});

describe("Site.prefix -- the grouping publishes the prefix; nothing re-derives it from a label (B8)", () => {
  const member = (cidr: string) => ({ observedCidr: cidr, northSouth: { bytes: 0, flows: 0 } });
  it("gives a real site its two-octet prefix, and the misc group and a raw bucket none", () => {
    const sites = groupIntoSites(
      ["10.80.20.0/24", "10.80.72.0/24", "10.1.10.0/24", "192.168.0.0/24"].map(member),
    );
    expect(sites.map((s) => [s.key, s.prefix])).toEqual([
      ["site:10.80", "10.80"],
      ["site:misc", null],
    ]);
    // Two identical non-IPv4 members form a `raw:` bucket -- a real site whose
    // label is not a prefix, so it publishes none.
    const raw = groupIntoSites(["2001:db8::/32", "2001:db8::/32"].map(member));
    expect(raw).toHaveLength(1);
    expect(raw[0]!.isMisc).toBe(false);
    expect(raw[0]!.prefix).toBeNull();
  });
});

describe("names -- no two segments share one, and the prefill keeps the producer's convention (A4)", () => {
  const roster = [cast("seg-10-80-20-0", 0), cast("internet-sink", 254)];
  const entries: StoreEntryInput[] = [
    { block: "add", state: "pending", record: { kind: "segment", key: "10.80.40.0/24", name: "ot-lab" } },
  ];
  const observed = observedSegmentInputs([seen("10.80.20.0/24"), seen("10.80.31.0/24")], roster, entries);
  const claims = segmentNameClaims(roster, observed, entries);

  it("a plain /24 keeps the conventional name -- the one aximprint's `segment_name` gives it", () => {
    expect(segmentNamePrefill("10.80.50.0/24", claims)).toBe("seg-10-80-50-0");
  });

  it("a /23 and a /25 at an OBSERVED network address are disambiguated by their prefix length", () => {
    // `segmentNameFor` drops the length, so both would otherwise inherit
    // `seg-10-80-20-0` -- the name of the /24 the cast carries.
    expect(segmentNameFor("10.80.20.0/23")).toBe("seg-10-80-20-0");
    expect(segmentNamePrefill("10.80.20.0/23", claims)).toBe("seg-10-80-20-0-23");
    expect(segmentNamePrefill("10.80.20.0/25", claims)).toBe("seg-10-80-20-0-25");
    // ...and a counter follows when that is taken too.
    const more = [...claims, { name: "seg-10-80-20-0-23", cidr: "10.99.0.0/24", source: "pending" as const }];
    expect(segmentNamePrefill("10.80.20.0/23", more)).toBe("seg-10-80-20-0-23-2");
  });

  it("an add of an observed segment under its own observed name is not a collision", () => {
    expect(nameClaimOn("seg-10-80-31-0", "10.80.31.0/24", claims)).toBeNull();
    expect(segmentNamePrefill("10.80.31.0/24", claims)).toBe("seg-10-80-31-0");
  });

  it("SF1: the cast's name for a RENAMED segment maps to that segment's network -- re-adding it under that name is no clash", () => {
    const renamedRoster = [cast("seg-10-80-20-0", 0), cast("ot-lab", 1)];
    const renameOnRecord: StoreEntryInput[] = [
      { block: "add", state: "saved", record: { kind: "segment", key: "10.80.72.0/24", name: "ot-lab" } },
    ];
    const standings = observedSegmentInputs([seen("10.80.20.0/24"), seen("10.80.72.0/24")], renamedRoster, renameOnRecord);
    const renamedClaims = segmentNameClaims(renamedRoster, standings, renameOnRecord);
    // The roster's `ot-lab` is joined to 10.80.72.0/24 THROUGH the record...
    expect(renamedClaims.find((c) => c.source === "cast" && c.name === "ot-lab")?.cidr).toBe("10.80.72.0/24");
    // ...so the same network under the cast's own name is not a collision --
    expect(nameClaimOn("ot-lab", "10.80.72.0/24", renamedClaims)).toBeNull();
    // -- while another network taking it still is.
    expect(nameClaimOn("ot-lab", "10.80.50.0/24", renamedClaims)?.cidr).toBe("10.80.72.0/24");
  });

  it("refuses a name another network holds -- a roster name, an observed name, a staged add's -- with STR-095 named", () => {
    const castName = nameClaimOn("seg-10-80-20-0", "10.80.20.0/23", claims);
    expect(castName?.source).toBe("cast");
    expect(nameClaimMessage("seg-10-80-20-0", castName!)).toBe(
      "the name seg-10-80-20-0 is already used by 10.80.20.0/24, a segment declared in the cast — two segments with " +
        "one name fail validation (STR-095) and make every join by name ambiguous",
    );
    // A roster segment nothing observed collides with ANY network.
    const sink = nameClaimOn("internet-sink", "10.80.50.0/24", claims);
    expect(sink).toEqual({ name: "internet-sink", cidr: null, source: "cast" });
    expect(nameClaimMessage("internet-sink", sink!)).toMatch(/a segment declared in the cast that nothing observed/);
    // An observed segment's name, taken for a DIFFERENT network.
    expect(nameClaimOn("seg-10-80-31-0", "10.80.50.0/24", claims)?.source).toBe("observed");
    // A staged add's.
    const staged = nameClaimOn("ot-lab", "10.80.50.0/24", claims);
    expect(staged?.source).toBe("pending");
    expect(nameClaimMessage("ot-lab", staged!)).toMatch(/10\.80\.40\.0\/24, a pending add/);
  });
});

describe("addedSegmentsOf -- the segments the store adds that the export does not observe (A3)", () => {
  it("lists add: segment records in store order, with their state -- an add of an OBSERVED segment excluded", () => {
    const entries = [
      { block: "add", state: "pending" as const, record: { kind: "segment", key: "10.80.31.0/24", name: "ot", zone_type: "vlan" } },
      { block: "add", state: "saved" as const, record: { kind: "segment", key: "10.99.1.0/24", name: "lab" } },
      { block: "add", state: "pending" as const, record: { kind: "host", key: "address:10.99.1.5" } },
    ];
    // Restated for S5.1.b (LES-299): an added segment now also says whether a
    // CLONE staged it (`cloneOf`, from its gesture tag) -- `null` for these,
    // which carry no tag. Nothing else about the listing moved.
    expect(addedSegmentsOf(entries, ["10.80.20.0/24"])).toEqual([
      { cidr: "10.80.31.0/24", name: "ot", zoneType: "vlan", state: "pending", cloneOf: null },
      { cidr: "10.99.1.0/24", name: "lab", zoneType: null, state: "saved", cloneOf: null },
    ]);
    // Observed (the pruned 10.80.31.0/24 is in the export): it has its own tile.
    expect(addedSegmentsOf(entries, ["10.80.31.0/24"]).map((a) => a.cidr)).toEqual(["10.99.1.0/24"]);
  });
});

describe("segmentNameFor -- the name prefill", () => {
  it("is the dashed address form both shipped bundles use", () => {
    expect(segmentNameFor("10.80.31.0/24")).toBe("seg-10-80-31-0");
    expect(segmentNameFor("")).toBe("");
  });
});

describe("segmentLandingNote -- where a new segment would group (B6: no map promise, staged adds not counted)", () => {
  it("names the site group and counts only OBSERVED siblings, promising no placement on the map", () => {
    // Restated for S5.1.b (LES-299): the closing clause used to say "added
    // segments are not drawn on the map yet", which a placed clone now
    // contradicts; it claims only what the site + adds. Everything else pinned.
    const note = segmentLandingNote("10.80.31.0/24", ["10.80.20.0/24", "10.80.72.0/24"]);
    expect(note).toBe(
      "would group with 10.80, beside 2 observed segments — a segment added here is listed in the outline, not drawn on the map yet",
    );
    expect(note).not.toMatch(/lands in/);
  });

  it("says MISC when nothing shares the prefix -- a singleton bucket folds there", () => {
    // `layout/sites.ts` folds a one-member bucket into the shared `misc` group,
    // so a site-less add would sit there.
    const note = segmentLandingNote("10.99.1.0/24", ["10.80.20.0/24"]);
    expect(note).toMatch(/^would sit in the shared misc group: no observed segment shares 10\.99/);
  });

  it("counts a singleton that the new segment turns into a site -- the pair leaves misc together", () => {
    // `10.1.10.0/24` sits alone in misc today; a second `10.1` segment makes a
    // `10.1` site of both, which is what the note must say.
    expect(segmentLandingNote("10.1.20.0/24", ["10.1.10.0/24", "10.80.20.0/24"])).toBe(
      "would group with 10.1, beside 1 observed segment — a segment added here is listed in the outline, not drawn on the map yet",
    );
  });

  it("does not silently count STAGED adds as siblings -- it says them apart", () => {
    const note = segmentLandingNote("10.80.31.0/24", ["10.80.20.0/24"], ["10.80.40.0/24", "10.80.41.0/24", "10.20.1.0/24"]);
    expect(note).toBe(
      "would group with 10.80, beside 1 observed segment; 2 adds in this file also share 10.80 and are not " +
        "counted — a segment added here is listed in the outline, not drawn on the map yet",
    );
  });

  it("does not count an add of an OBSERVED segment as a staged add -- it is already an observed sibling", () => {
    // The tester's miscount: `10.80.31.0/24` is observed AND added by this file,
    // and was counted twice -- once among the observed, once as an add.
    const note = segmentLandingNote(
      "10.80.40.0/24",
      ["10.80.20.0/24", "10.80.31.0/24"],
      ["10.80.31.0/24", "10.80.41.0/24"],
    );
    expect(note).toBe(
      "would group with 10.80, beside 2 observed segments; 1 add in this file also shares 10.80 and is not " +
        "counted — a segment added here is listed in the outline, not drawn on the map yet",
    );
  });

  it("does not count the CIDR being added as its own sibling", () => {
    const note = segmentLandingNote("10.99.1.0/24", ["10.99.1.0/24"], ["10.99.1.0/24"]);
    expect(note).toMatch(/misc/);
    expect(note).not.toMatch(/adds in this file/);
  });

  it("has nothing to say about an unparseable CIDR (the form refuses it)", () => {
    expect(segmentLandingNote("", [])).toBeNull();
    expect(segmentLandingNote("not-a-cidr", [])).toBeNull();
    // B1: a half-typed prefix length is not a /0.
    expect(segmentLandingNote("10.80.31.0/", [])).toBeNull();
  });
});
