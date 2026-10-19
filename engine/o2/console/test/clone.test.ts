// @vitest-environment node
/**
 * S5.1.b (clone) and S5.1.c (generated hosts) -- the PURE rules, run exactly as
 * the forms run them: `planClone` / the host allocator, then `buildBatch`, then
 * the reducer's `stageBatch` (`docs/WORK_ORDER_console_canvas.md` §3 S5.1).
 *
 *   - acceptance 1: a DEFAULT clone of the REAL bundles' `10.80.20.0/24`, on a
 *     graph built through `castInputFrom` with the drift-tested segment roster
 *     and the real conduit roster (`fixtures/realBundles.ts`, LES-311) -- its N
 *     counted from the RAW export, never from the planner (LES-187);
 *   - acceptance 3: mirrored conduits, on the fixture and on both real bundles;
 *   - acceptance 4: each collision refused with its own reason, and NO record
 *     staged -- read off the store, not off the planner's answer;
 *   - the atomic batch: a record refused mid-batch refuses all of it;
 *   - acceptance 7's pure half: the allocator and the persona listen table.
 *
 * Node environment: the real-bundle blocks read files (LES-260).
 */
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import type { AssertionRecord, CastSummary, TopologyExport } from "../src/api/types";
import { allocateHostAddresses, takenAddresses } from "../src/edit/allocate";
import { buildBatch } from "../src/edit/batch";
import {
  assertedHostValues,
  chooseHosts,
  cloneCidrRefusal,
  cloneDefaults,
  planClone,
  sourceConduits,
  type CloneEntryInput,
  type CloneOptions,
} from "../src/edit/clone";
import { batchRefusal, editReducer, initialEditState, type EditEntry, type EditStoreState } from "../src/edit/store";
import { buildGraph, type ConsoleGraph } from "../src/model/graph";
import { defaultListenFor, rolesWithListenDefaults, ROLES_WITH_DIALED_EMITS } from "../src/model/roles";
import { ROLE_LISTEN_DEFAULTS } from "../src/model/roleListen.generated";
import { castInputFrom } from "../src/views/useGraph";
import { bundleAvailable, conduitRosterFor, REAL_BUNDLES, realGraph } from "./fixtures/realBundles";
import { trimmedCast, trimmedExport } from "./fixtures/trimmed";

const SOURCE = "10.80.20.0/24";
const NOTE = "a second lab floor";

/** The fixture through the LIVE seam: the cast summary publishes its conduits
 *  and a segment roster (the fixture's own names, zone types from the golden
 *  inputs), so `graph.castConduits` and the cast standings are the live shape. */
function fixtureGraph(exportData: TopologyExport = trimmedExport): ConsoleGraph {
  const cast = {
    deploy_min_occupancy: trimmedCast.deployMinOccupancy,
    conduit_roster: trimmedCast.conduits,
    routers: trimmedCast.routers,
    segment_roster: exportData.segments.map((s) => ({
      name: s.name,
      cidr: s.cidr,
      zone_type: trimmedCast.zoneTypeByName?.[s.name] ?? null,
    })),
  } as unknown as CastSummary;
  return buildGraph(exportData, castInputFrom(cast, "trimmed-fixture"));
}

/** What the clone form does on Stage, and nothing more: plan, check the batch
 *  whole, then hand it to the reducer as ONE action. */
function stageClone(
  state: EditStoreState,
  graph: ConsoleGraph,
  options: CloneOptions,
  source = SOURCE,
): { state: EditStoreState; error: string | null } {
  const plan = planClone(graph, state.entries, source, options);
  if (!plan.ok) return { state, error: plan.error };
  const batch = buildBatch(plan.plan.gestures, state.entries);
  if (!batch.ok) return { state, error: batch.error };
  return {
    state: editReducer(state, {
      type: "stageBatch",
      records: batch.staged,
      gesture: { id: "g1", label: plan.plan.label, kind: "clone", source },
    }),
    error: null,
  };
}

function defaultsFor(graph: ConsoleGraph, entries: readonly CloneEntryInput[] = [], source = SOURCE): CloneOptions {
  const d = cloneDefaults(graph, entries, source);
  return {
    cidr: d.cidr ?? "",
    name: d.name,
    zoneType: d.zoneType,
    hosts: { mode: "all" },
    mapping: "same-host-part",
    mirrorConduits: false,
    note: NOTE,
  };
}

function entry(block: EditEntry["block"], record: AssertionRecord, state: EditEntry["state"] = "pending"): EditEntry {
  const key = record.key ?? "";
  return { id: block === "override" ? `${block}:${key}:${record.field}` : `${block}:${key}`, block, state, record };
}

function withEntries(entries: EditEntry[]): EditStoreState {
  return { ...initialEditState("trimmed-fixture"), entries };
}

/** The schema's `add: host` fields -- anything else on a cloned record would be
 *  something the source carried that is not an assertion (an evidence class, a
 *  confidence). */
const HOST_RECORD_FIELDS = new Set(["kind", "key", "segment", "role", "listen", "dial", "image", "resources", "note"]);

// ===========================================================================
// acceptance 1 -- a default clone of the REAL bundles' 10.80.20.0/24
// ===========================================================================
/** Measured off the raw export files (a Python pass, 2026-09-15) and pinned as
 *  literals, so the planner can never supply its own expectation. */
const REAL_EXPECT: Record<string, { n: number; notCloned: number; cidr: string }> = {
  "corpapcn-twin": { n: 14, notCloned: 4, cidr: "10.80.0.0/24" },
  "pit-395a61f04e-1780405200": { n: 18, notCloned: 6, cidr: "10.80.1.0/24" },
};

for (const bundle of REAL_BUNDLES) {
  describe.skipIf(!bundleAvailable(bundle))(`S5.1.b acceptance 1 -- a DEFAULT clone of ${bundle.id}'s ${SOURCE}`, () => {
    const graph = realGraph(bundle);
    const raw = JSON.parse(readFileSync(bundle.topology, "utf8")) as TopologyExport;
    const placed = raw.nodes.filter((n) => n.segment === SOURCE && n.locality === "local");
    const addressKeyed = placed.filter((n) => n.id.startsWith("address:"));
    const otherKeyed = placed.filter((n) => !n.id.startsWith("address:"));
    const expected = REAL_EXPECT[bundle.id]!;

    it("the raw export holds the counts the acceptance names -- read before the planner is asked", () => {
      expect(addressKeyed).toHaveLength(expected.n);
      expect(otherKeyed).toHaveLength(expected.notCloned);
    });

    it("emits exactly 1 + N + 0 records, re-bases every host key on the same host part, and notes every record", () => {
      const options = defaultsFor(graph);
      // The defaults are the form's: the next free /24 in the source's site.
      expect(options.cidr).toBe(expected.cidr);
      const { state, error } = stageClone(initialEditState(bundle.id), graph, options);
      expect(error).toBeNull();

      const byKind = (kind: string): EditEntry[] => state.entries.filter((e) => e.record.kind === kind);
      expect(state.entries).toHaveLength(1 + expected.n + 0);
      expect(byKind("segment")).toHaveLength(1);
      expect(byKind("host")).toHaveLength(expected.n);
      expect(byKind("conduit")).toHaveLength(0);
      expect(byKind("segment")[0]!.record.key).toBe(expected.cidr);

      // Every host key re-based: the same last octet as its source, in the
      // clone's network -- checked against the RAW export's address keys.
      const cloneNet = expected.cidr.slice(0, expected.cidr.lastIndexOf(".") + 1);
      const wanted = addressKeyed.map((n) => `address:${cloneNet}${n.id.slice(n.id.lastIndexOf(".") + 1)}`).sort();
      expect(byKind("host").map((e) => e.record.key).sort()).toEqual(wanted);
      for (const e of byKind("host")) expect(e.record.segment).toBe(expected.cidr);

      // Every record carries the propagated note -- and the batch's gesture tag.
      for (const e of state.entries) {
        expect(e.record.note, e.id).toBe(`cloned from ${SOURCE}: ${NOTE}`);
        expect(e.gesture?.kind, e.id).toBe("clone");
        expect(e.state, e.id).toBe("pending");
      }
    });

    it("reports the hostname- (and MAC-) keyed hosts under NOT CLONED, with the reason, and stages none of them", () => {
      const plan = planClone(graph, [], SOURCE, defaultsFor(graph));
      if (!plan.ok) throw new Error(plan.error);
      expect(plan.plan.notCloned.map((n) => n.key).sort()).toEqual(otherKeyed.map((n) => n.id).sort());
      for (const n of plan.plan.notCloned) {
        expect(n.reason, n.key).toMatch(n.key.startsWith("hostname:") ? /a hostname names one machine/ : /a MAC address names one interface/);
      }
      const staged = new Set(plan.plan.hosts.map((h) => h.from));
      for (const n of otherKeyed) expect(staged.has(n.id), n.id).toBe(false);
    });

    it("copies role, listen and dial from the source host -- and NOTHING observed as evidence", () => {
      const plan = planClone(graph, [], SOURCE, defaultsFor(graph));
      if (!plan.ok) throw new Error(plan.error);
      const rawById = new Map(raw.nodes.map((n) => [n.id, n]));
      for (let i = 0; i < plan.plan.hosts.length; i++) {
        const source = rawById.get(plan.plan.hosts[i]!.from)!;
        const gesture = plan.plan.gestures[i + 1]!;
        if (gesture.kind !== "add-host") throw new Error(`gesture ${i + 1} is ${gesture.kind}`);
        expect(gesture.role, source.id).toBe(source.role ?? undefined);
        // The export's census strings, verbatim and in their order.
        expect((gesture.listen ?? []).map((p) => `${p.port}/${p.proto}`), source.id).toEqual(source.listen_ports);
        expect((gesture.dial ?? []).map((p) => `${p.port}/${p.proto}`), source.id).toEqual(source.dial_ports);
        // No image or sizing: nothing in an empty file asserts one.
        expect(gesture.image, source.id).toBeUndefined();
        expect(gesture.resources, source.id).toBeUndefined();
      }
      const batch = buildBatch(plan.plan.gestures, []);
      if (!batch.ok) throw new Error(batch.error);
      for (const staged of batch.staged.slice(1)) {
        for (const field of Object.keys(staged.record)) expect(HOST_RECORD_FIELDS.has(field), field).toBe(true);
      }
    });
  });
}

// ===========================================================================
// acceptance 3 -- mirrored conduits
// ===========================================================================
describe("S5.1.b acceptance 3 -- a mirrored-conduits clone adds exactly the source's conduit count, re-keyed and sorted", () => {
  it("on the fixture: the two conduits the cast declares on 10.80.20.0/24", () => {
    const graph = fixtureGraph();
    // Counted off the cast INPUT, by name -- not off `sourceConduits`.
    const declared = (trimmedCast.conduits ?? []).filter((c) => c.from === "seg-10-80-20-0" || c.to === "seg-10-80-20-0");
    expect(declared).toHaveLength(2);

    const options = { ...defaultsFor(graph), mirrorConduits: true };
    const { state, error } = stageClone(initialEditState("trimmed-fixture"), graph, options);
    expect(error).toBeNull();
    const conduits = state.entries.filter((e) => e.record.kind === "conduit");
    expect(conduits).toHaveLength(declared.length);
    // Re-keyed onto the clone, halves sorted, and the records in key order.
    expect(conduits.map((e) => e.record.key)).toEqual([
      "10.20.193.0/24|10.80.0.0/24",
      "10.80.0.0/24|10.80.72.0/24",
    ]);
    const keys = conduits.map((e) => e.record.key ?? "");
    expect([...keys].sort()).toEqual(keys);
    // ...after the segment and the hosts, each carrying the note.
    expect(state.entries[state.entries.length - 1]!.record.kind).toBe("conduit");
    for (const e of conduits) expect(e.record.note).toBe(`cloned from ${SOURCE}: ${NOTE}`);
  });

  it("a conduit to a network the map has no CIDR for is listed as NOT MIRRORED, with the reason -- never dropped", () => {
    const graph = fixtureGraph();
    const conduits = sourceConduits(graph, [], "10.1.10.0/24");
    expect(conduits.peers).toEqual([]);
    expect(conduits.notMirrored).toEqual([
      { peer: "internet-sink", reason: expect.stringMatching(/has no observed network on this map/) as unknown as string },
    ]);
  });

  it("a source conduit whose peer IS the clone's own CIDR is listed as not mirrored, with the reason (N4)", () => {
    const graph = fixtureGraph();
    // 10.80.72.0/24 is a declared peer of the source. Clone the source ONTO
    // that CIDR... which the cast claims, so use the file's own peer instead:
    // a conduit this file adds from the source to the CIDR being cloned to.
    const target = "10.80.0.0/24";
    const file = [entry("add", { kind: "conduit", key: `${SOURCE}|${target}`, note: "n" }, "saved")];
    expect(sourceConduits(graph, file, SOURCE).peers).toContain(target);
    const plan = planClone(graph, file, SOURCE, {
      ...defaultsFor(graph, file),
      cidr: target,
      mirrorConduits: true,
    });
    if (!plan.ok) throw new Error(plan.error);
    // Not in the mirrored set...
    expect(plan.plan.conduits).not.toContain(target);
    // ...and SAID, rather than silently one short of the checkbox's count.
    expect(plan.plan.notMirrored).toContainEqual({
      peer: target,
      reason: expect.stringMatching(/which is this clone's own network — a conduit joins two networks, and one to itself is not one$/) as unknown as string,
    });
    // The mirrored ones are still whole: every other declared peer travels.
    expect(plan.plan.conduits).toEqual(["10.20.193.0/24", "10.80.72.0/24"]);
  });

  it("a conduit this FILE adds on the source is mirrored too, once, beside the cast's", () => {
    const graph = fixtureGraph();
    const file = [entry("add", { kind: "conduit", key: "10.1.10.0/24|10.80.20.0/24", note: "n" }, "saved")];
    // 10.80.72.0/24 is ALSO declared by the cast: named twice, mirrored once.
    const twice = [...file, entry("add", { kind: "conduit", key: "10.80.20.0/24|10.80.72.0/24", note: "n" })];
    expect(sourceConduits(graph, twice, SOURCE).peers).toEqual(["10.1.10.0/24", "10.20.193.0/24", "10.80.72.0/24"]);
  });

  for (const bundle of REAL_BUNDLES) {
    it.skipIf(!bundleAvailable(bundle))(`on the real ${bundle.id} bundle: the cast's own conduits on ${SOURCE}`, () => {
      // Ground truth from a real YAML parse (2026-09-15), pinned: the roster's
      // total, and how many of its conduits touch the source.
      const truth: Record<string, { total: number; onSource: number }> = {
        "corpapcn-twin": { total: 22, onSource: 10 },
        "pit-395a61f04e-1780405200": { total: 40, onSource: 17 },
      };
      const roster = conduitRosterFor(bundle);
      expect(roster).toHaveLength(truth[bundle.id]!.total);

      const graph = realGraph(bundle);
      const plan = planClone(graph, [], SOURCE, { ...defaultsFor(graph), mirrorConduits: true });
      if (!plan.ok) throw new Error(plan.error);
      const conduits = plan.plan.gestures.filter((g) => g.kind === "add-conduit");
      expect(conduits).toHaveLength(truth[bundle.id]!.onSource);
      expect(plan.plan.notMirrored).toEqual([]);
      const batch = buildBatch(plan.plan.gestures, []);
      if (!batch.ok) throw new Error(batch.error);
      const keys = batch.staged.filter((s) => s.record.kind === "conduit").map((s) => s.record.key ?? "");
      expect([...keys].sort()).toEqual(keys);
      const clone = REAL_EXPECT[bundle.id]!.cidr;
      for (const key of keys) expect(key.split("|"), key).toContain(clone);
    });
  }
});

// ===========================================================================
// acceptance 4 -- collisions: refused with the reason, nothing staged
// ===========================================================================
describe("S5.1.b acceptance 4 -- each collision is refused with its OWN reason, and no partial batch is staged", () => {
  const graph = fixtureGraph();

  /** Stage through the form's path and assert the store did not move. */
  function refusedWithoutStaging(state: EditStoreState, options: CloneOptions, g = graph): string {
    const { state: after, error } = stageClone(state, g, options);
    expect(error).not.toBeNull();
    expect(after.entries).toEqual(state.entries);
    expect(after.entries).toBe(state.entries);
    return error!;
  }

  it("an address the EXPORT already carries (an unplaced local entity at the mapped address)", () => {
    const withUnplaced: TopologyExport = {
      ...trimmedExport,
      nodes: [
        ...trimmedExport.nodes,
        { ...trimmedExport.nodes[0]!, id: "address:10.80.9.5", address: "10.80.9.5", segment: null, role: null, listen_ports: [], dial_ports: [] },
      ],
    };
    const g = fixtureGraph(withUnplaced);
    const error = refusedWithoutStaging(initialEditState("trimmed-fixture"), { ...defaultsFor(g), cidr: "10.80.9.0/24", name: "seg-10-80-9-0" }, g);
    expect(error).toMatch(/^address:10\.80\.9\.5 \(the clone of address:10\.80\.20\.5\) already exists in this twin's export/);
    // ...and the planner offers the mapping that avoids it, which then stages.
    const plan = planClone(g, [], SOURCE, { ...defaultsFor(g), cidr: "10.80.9.0/24", name: "seg-10-80-9-0" });
    expect(plan.ok === false && plan.offerRenumber).toBe(true);
    const renumbered = stageClone(initialEditState("trimmed-fixture"), g, {
      ...defaultsFor(g),
      cidr: "10.80.9.0/24",
      name: "seg-10-80-9-0",
      mapping: "renumber",
    });
    expect(renumbered.error).toBeNull();
    expect(renumbered.state.entries.map((e) => e.record.key)).not.toContain("address:10.80.9.5");
  });

  it("an address a PENDING add in this file already holds", () => {
    const state = withEntries([entry("add", { kind: "host", key: "address:10.80.0.30", segment: "10.80.72.0/24", note: "n" })]);
    const error = refusedWithoutStaging(state, defaultsFor(graph, state.entries));
    expect(error).toMatch(/^address:10\.80\.0\.30 \(the clone of address:10\.80\.20\.30\) is already claimed by a pending add record in this file/);
  });

  it("an EXACT CIDR -- an observed segment the cast declares, one it does not declare under its name, and a pending add", () => {
    const declared = refusedWithoutStaging(initialEditState("trimmed-fixture"), {
      ...defaultsFor(graph),
      cidr: "10.80.72.0/24",
      name: "seg-10-80-72-0",
    });
    expect(declared).toMatch(/^10\.80\.72\.0\/24 is already declared in the cast — a clone creates a NEW segment/);

    // A roster that does NOT name seg-10-80-72-0: S5.1.a's site + would ALLOW
    // this exact add (it confirms the observed segment). A clone refuses it.
    const cast = {
      deploy_min_occupancy: 0.8,
      conduit_roster: [],
      routers: [],
      segment_roster: trimmedExport.segments.filter((s) => s.name !== "seg-10-80-72-0").map((s) => ({ name: s.name, cidr: s.cidr, zone_type: null })),
    } as unknown as CastSummary;
    const pruned = buildGraph(trimmedExport, castInputFrom(cast, "trimmed-fixture"));
    const absent = refusedWithoutStaging(
      initialEditState("trimmed-fixture"),
      { ...defaultsFor(pruned), cidr: "10.80.72.0/24", name: "seg-10-80-72-0" },
      pruned,
    );
    expect(absent).toMatch(/^10\.80\.72\.0\/24 is an observed segment \(the installed cast does not declare it under this name\)/);

    const pendingSegment = withEntries([entry("add", { kind: "segment", key: "10.80.9.0/24", name: "lab", note: "n" })]);
    const pending = refusedWithoutStaging(pendingSegment, { ...defaultsFor(graph, pendingSegment.entries), cidr: "10.80.9.0/24", name: "lab" });
    expect(pending).toMatch(/^10\.80\.9\.0\/24 is already claimed by a pending add .* a clone never reuses a segment this file already adds$/);
  });

  it("a HOSTNAME key: listed, never staged -- and a selection that brings only hostname-keyed hosts is refused whole", () => {
    // The whole segment: the hostname-keyed domain controller is listed with its
    // reason, and no record for it exists.
    const plan = planClone(graph, [], SOURCE, defaultsFor(graph));
    if (!plan.ok) throw new Error(plan.error);
    expect(plan.plan.notCloned).toEqual([
      { key: "hostname:axdc01", reason: expect.stringMatching(/^a hostname names one machine/) as unknown as string },
    ]);
    expect(plan.plan.hosts.map((h) => h.from)).not.toContain("hostname:axdc01");

    // By role: the domain controllers of 10.80.20.0/24 are ONE hostname-keyed
    // host, so the selection brings no host at all -- refused, naming it.
    const error = refusedWithoutStaging(initialEditState("trimmed-fixture"), {
      ...defaultsFor(graph),
      hosts: { mode: "roles", roles: ["domain-controller"] },
    });
    expect(error).toMatch(/^none of the 1 host this selection names can be cloned \(hostname:axdc01\)/);
  });

  it("a DUPLICATE pending clone -- the second clone onto the first one's CIDR", () => {
    const first = stageClone(initialEditState("trimmed-fixture"), graph, defaultsFor(graph));
    expect(first.error).toBeNull();
    expect(first.state.entries.length).toBeGreaterThan(1);
    // The next clone's DEFAULT moved past the staged one...
    expect(cloneDefaults(graph, first.state.entries, SOURCE).cidr).toBe("10.80.1.0/24");
    // ...and aiming it back at the staged CIDR is refused, nothing added.
    const error = refusedWithoutStaging(first.state, {
      ...defaultsFor(graph, first.state.entries),
      cidr: "10.80.0.0/24",
      name: "seg-10-80-0-0",
    });
    expect(error).toMatch(/^10\.80\.0\.0\/24 is already claimed by a pending add/);
  });

  it("an OVERLAP is said, not refused (NET-010), as S5.1.a says it", () => {
    const plan = planClone(graph, [], SOURCE, { ...defaultsFor(graph), cidr: "10.80.20.128/25", name: "seg-10-80-20-128-25" });
    if (!plan.ok) throw new Error(plan.error);
    expect(plan.plan.warnings).toHaveLength(1);
    expect(plan.plan.warnings[0]).toMatch(/^10\.80\.20\.128\/25 overlaps 10\.80\.20\.0\/24, .*NET-010/);
    expect(cloneCidrRefusal("10.80.20.128/25", { claim: { cidr: SOURCE, source: "cast" }, exact: false })).toBeNull();
  });

  it("a CIDR that is not the network address", () => {
    const error = refusedWithoutStaging(initialEditState("trimmed-fixture"), { ...defaultsFor(graph), cidr: "10.80.0.5/24" });
    expect(error).toMatch(/is not a network address — a clone's CIDR must be the network address/);
  });
});

// ===========================================================================
// the ATOMIC batch
// ===========================================================================
describe("the batch path is all or nothing", () => {
  const graph = fixtureGraph();

  it("a record refused MID-batch refuses every record, naming the one that failed", () => {
    // An override of the ROLE of the address the clone's third host lands on.
    // Two layers stop it: the PLANNER, which counts every key the file holds as
    // taken (so a clone never lands on it), and -- for a batch that reaches the
    // batch path anyway -- `14` §3's cross-record rule at that record. The batch
    // is built here from a plan made WITHOUT the override, to reach the second.
    const state = withEntries([
      entry("override", { key: "address:10.80.0.31", field: "role", value: "file-server", note: "n" }, "saved"),
    ]);
    const planner = planClone(graph, state.entries, SOURCE, defaultsFor(graph, state.entries));
    expect(planner.ok === false && planner.error).toMatch(/^address:10\.80\.0\.31 .* already claimed by a saved override record/);

    const plan = planClone(graph, [], SOURCE, defaultsFor(graph));
    if (!plan.ok) throw new Error(plan.error);
    const at = plan.plan.gestures.findIndex((g) => g.kind === "add-host" && g.key === "address:10.80.0.31");
    expect(at).toBeGreaterThan(1);
    const batch = buildBatch(plan.plan.gestures, state.entries);
    expect(batch.ok).toBe(false);
    if (batch.ok) return;
    expect(batch.index).toBe(at);
    expect(batch.error).toMatch(
      new RegExp(`^record ${at + 1} of ${plan.plan.gestures.length} \\(add host address:10\\.80\\.0\\.31\\): .*already overridden.* — the batch is refused whole, and nothing was staged$`),
    );
    expect(stageClone(state, graph, defaultsFor(graph, state.entries)).state.entries).toBe(state.entries);
  });

  it("the REDUCER refuses a batch whose id the store already holds -- whole, with the reason in problems", () => {
    const held = entry("add", { kind: "host", key: "address:10.80.0.5", segment: "10.80.0.0/24", note: "mine" }, "saved");
    const before = withEntries([held]);
    const after = editReducer(before, {
      type: "stageBatch",
      records: [
        { block: "add", record: { kind: "segment", key: "10.80.0.0/24", name: "x", note: "n" } },
        { block: "add", record: { kind: "host", key: "address:10.80.0.5", segment: "10.80.0.0/24", note: "n" } },
      ],
      gesture: { id: "g9", label: "clone of a → b", kind: "clone" },
    });
    expect(after.entries).toBe(before.entries);
    expect(after.problems).toEqual([
      'refused the batch "clone of a → b": add:address:10.80.0.5 is already in this store, and a batch never replaces a record — nothing was staged',
    ]);
    // ...and the saved record it would have replaced is untouched.
    expect(after.entries[0]!.record.note).toBe("mine");
  });

  it("the reducer and its CALLER ask ONE pure question, so a refused batch commits no side effect (S4)", () => {
    // The clone's placement (an S4 pin), its session position and the selection
    // are committed beside the dispatch. They must hang off the SAME decision
    // the reducer makes, not a second, independently-written one -- which is
    // exactly how a refused batch ends up with a pin for records that do not
    // exist. `batchRefusal` is that one question.
    const held = entry("add", { kind: "host", key: "address:10.80.0.5", segment: "10.80.0.0/24", note: "mine" }, "saved");
    const records = [
      { block: "add" as const, record: { kind: "segment" as const, key: "10.80.0.0/24", name: "x", note: "n" } },
      { block: "add" as const, record: { kind: "host" as const, key: "address:10.80.0.5", segment: "10.80.0.0/24", note: "n" } },
    ];
    const why = batchRefusal([held], records);
    expect(why).toBe("add:address:10.80.0.5 is already in this store, and a batch never replaces a record");
    // The reducer's own problem line is built from this very string...
    const after = editReducer(withEntries([held]), {
      type: "stageBatch",
      records,
      gesture: { id: "g9", label: "clone of a → b", kind: "clone" },
    });
    expect(after.problems[0]).toBe(`refused the batch "clone of a → b": ${why} — nothing was staged`);
    // ...and it agrees on every other shape the reducer refuses.
    expect(batchRefusal([], [])).toBe("it holds no record");
    expect(batchRefusal([], [records[0]!, records[0]!])).toBe("it names add:10.80.0.0/24 twice");
    expect(batchRefusal([], records)).toBeNull();
    // An accepted batch: the decision is `null` and the records ARE staged.
    expect(editReducer(initialEditState("trimmed-fixture"), {
      type: "stageBatch",
      records,
      gesture: { id: "g9", label: "clone of a → b", kind: "clone" },
    }).entries).toHaveLength(2);
  });

  it("a record refused at the very END of the batch -- a mirrored conduit the file already holds -- refuses all of it", () => {
    // The clone's conduits come LAST, so this is the one position the mid-batch
    // case above cannot reach: every earlier record is fine and the refusal
    // arrives on the final one. Reachable by hand -- the operator stages the
    // conduit first, then clones with mirroring on (iteration-8 tester).
    const held = entry("add", { kind: "conduit", key: "10.80.0.0/24|10.80.72.0/24", note: "by hand" }, "saved");
    const options = { ...defaultsFor(graph, [held]), mirrorConduits: true };
    const plan = planClone(graph, [held], SOURCE, options);
    if (!plan.ok) throw new Error(plan.error);
    const last = plan.plan.gestures.length - 1;
    // The held record is the LAST gesture, not merely somewhere in the batch.
    const lastGesture = plan.plan.gestures[last]!;
    expect(lastGesture.kind).toBe("add-conduit");
    expect(lastGesture.kind === "add-conduit" && [lastGesture.cidrA, lastGesture.cidrB].sort()).toEqual([
      "10.80.0.0/24",
      "10.80.72.0/24",
    ]);

    const batch = buildBatch(plan.plan.gestures, [held]);
    expect(batch.ok).toBe(false);
    if (batch.ok) return;
    expect(batch.index).toBe(last);
    expect(batch.error).toMatch(
      new RegExp(
        `^record ${last + 1} of ${last + 1} \\(add conduit .*\\): 10\\.80\\.0\\.0/24\\|10\\.80\\.72\\.0/24 is already ` +
          "claimed by a saved record .* — the batch is refused whole, and nothing was staged$",
      ),
    );

    // ...and NOTHING is staged: not the segment, not one of the 17 hosts, not
    // the conduit that was fine -- read off the store, never off the planner.
    const before = withEntries([held]);
    const after = stageClone(before, graph, options);
    expect(after.error).toBe(batch.error);
    expect(after.state.entries).toBe(before.entries);
    expect(after.state.entries).toHaveLength(1);
  });

  it("a batch that names one record twice is refused whole by the batch builder AND the reducer", () => {
    const twice = [
      { kind: "add-host" as const, key: "address:10.80.0.5", segment: "10.80.0.0/24", note: "n" },
      { kind: "add-host" as const, key: "address:10.80.0.5", segment: "10.80.0.0/24", note: "n" },
    ];
    const batch = buildBatch(twice, []);
    expect(batch.ok === false && batch.error).toMatch(/^record 2 of 2 \(add host address:10\.80\.0\.5\): the batch names address:10\.80\.0\.5 twice/);
    const state = editReducer(initialEditState("x"), {
      type: "stageBatch",
      records: [
        { block: "add", record: { kind: "host", key: "address:10.80.0.5", note: "n" } },
        { block: "add", record: { kind: "host", key: "address:10.80.0.5", note: "n" } },
      ],
      gesture: { id: "g", label: "l", kind: "generate" },
    });
    expect(state.entries).toEqual([]);
    expect(state.problems[0]).toMatch(/names add:address:10\.80\.0\.5 twice/);
  });
});

// ===========================================================================
// the rest of the clone form's table
// ===========================================================================
describe("hosts to bring, address mapping, and what is copied", () => {
  const graph = fixtureGraph();
  const segment = graph.segmentByCidr.get(SOURCE)!;

  it("all / by role / first N by address -- each counted off the fixture's own bands", () => {
    expect(chooseHosts(segment, { mode: "all" }).chosen).toHaveLength(17);
    const endpoints = chooseHosts(segment, { mode: "roles", roles: ["generic-endpoint"] });
    expect(endpoints.chosen.map((h) => h.id)).toEqual([
      ...[40, 41, 42, 43, 44, 45, 46, 47, 60, 61, 62].map((o) => `address:10.80.20.${o}`),
    ]);
    // Numeric address order, never lexical (".5" before ".30").
    expect(chooseHosts(segment, { mode: "first", count: 3 }).chosen.map((h) => h.id)).toEqual([
      "address:10.80.20.5",
      "address:10.80.20.30",
      "address:10.80.20.31",
    ]);
    expect(chooseHosts(segment, { mode: "first", count: 0 }).error).toMatch(/from 1 to 17/);
    expect(chooseHosts(segment, { mode: "first", count: 18 }).error).toMatch(/from 1 to 17/);
    expect(chooseHosts(segment, { mode: "roles", roles: [] }).error).toMatch(/^choose at least one role/);
  });

  it('says what "first N" actually got, never `got null` (N3)', () => {
    // `JSON.stringify(NaN)` is the string `null`, so the refusal used to end
    // "got null" for anything that was not digits -- a value the operator never
    // typed and cannot act on.
    expect(chooseHosts(segment, { mode: "first", count: NaN, typed: "two" }).error).toBe(
      '"first N by address" takes a whole number from 1 to 17 (the address-keyed hosts on ' +
        `${SOURCE}), got "two", which is not a whole number`,
    );
    expect(chooseHosts(segment, { mode: "first", count: NaN, typed: "  " }).error).toMatch(/got nothing typed$/);
    // A real number out of range is still quoted as the number.
    expect(chooseHosts(segment, { mode: "first", count: 18, typed: "18" }).error).toMatch(/got "18", which is not a whole number$/);
    expect(chooseHosts(segment, { mode: "first", count: 18 }).error).toMatch(/got 18$/);
    // ...and a caller that passes no `typed` at all still says something true.
    expect(chooseHosts(segment, { mode: "first", count: NaN }).error).toMatch(/got no number at all$/);
  });

  it("renumber from .10 numbers in address order and skips every address in play", () => {
    const state = withEntries([entry("add", { kind: "host", key: "address:10.80.0.12", segment: "10.80.72.0/24", note: "n" })]);
    const plan = planClone(graph, state.entries, SOURCE, { ...defaultsFor(graph, state.entries), mapping: "renumber" });
    if (!plan.ok) throw new Error(plan.error);
    expect(plan.plan.hosts.slice(0, 4)).toEqual([
      { from: "address:10.80.20.5", to: "address:10.80.0.10" },
      { from: "address:10.80.20.30", to: "address:10.80.0.11" },
      { from: "address:10.80.20.31", to: "address:10.80.0.13" },
      { from: "address:10.80.20.32", to: "address:10.80.0.14" },
    ]);
  });

  it("an image or sizing travels ONLY where an assertion in this file gives the source host one", () => {
    const state = withEntries([
      entry("override", { key: "address:10.80.20.5", field: "image", value: "debian-12", note: "n" }, "saved"),
      entry("override", { key: "address:10.80.20.30", field: "resources", value: { cpu: 2, ram: "4096" }, note: "n" }),
    ]);
    expect(assertedHostValues(state.entries, "address:10.80.20.5")).toEqual({ image: "debian-12" });
    const plan = planClone(graph, state.entries, SOURCE, defaultsFor(graph, state.entries));
    if (!plan.ok) throw new Error(plan.error);
    const hostOf = (from: string) => {
      const i = plan.plan.hosts.findIndex((h) => h.from === from);
      return plan.plan.gestures[i + 1]!;
    };
    expect(hostOf("address:10.80.20.5")).toMatchObject({ image: "debian-12" });
    expect(hostOf("address:10.80.20.30")).toMatchObject({ resources: { cpu: 2, ram: "4096" } });
    expect("image" in hostOf("address:10.80.20.31")).toBe(false);
    expect("resources" in hostOf("address:10.80.20.31")).toBe(false);
  });

  it("the zone type starts at the source's declared one", () => {
    // The fixture's roster declares seg-10-80-20-0 simple and seg-10-20-193-0 vlan.
    expect(cloneDefaults(graph, [], SOURCE)).toMatchObject({ zoneType: "simple", zoneSource: "source" });
    expect(cloneDefaults(graph, [], "10.20.193.0/24")).toMatchObject({ zoneType: "vlan", zoneSource: "source", cidr: "10.20.0.0/24" });
    // A source in the MISC group allocates in its own two octets.
    expect(cloneDefaults(graph, [], "10.1.10.0/24")).toMatchObject({ prefix: "10.1", prefixSource: "source", cidr: "10.1.0.0/24" });
  });
});

// ===========================================================================
// acceptance 7, the pure half -- generated hosts
// ===========================================================================
describe("S5.1.c -- host addresses from .10, and the persona library's listen defaults", () => {
  it("allocates from .10 upward, skipping taken addresses, and refuses a count the network cannot hold", () => {
    expect(allocateHostAddresses("10.80.0.0/24", 3, new Set())).toEqual({ ok: true, addresses: ["10.80.0.10", "10.80.0.11", "10.80.0.12"] });
    expect(allocateHostAddresses("10.80.0.0/24", 3, new Set(["10.80.0.11"]))).toEqual({
      ok: true,
      addresses: ["10.80.0.10", "10.80.0.12", "10.80.0.13"],
    });
    // A /27 has hosts .1-.30: .10-.30 is 21 addresses.
    expect(allocateHostAddresses("10.80.0.0/27", 21, new Set()).ok).toBe(true);
    const full = allocateHostAddresses("10.80.0.0/27", 22, new Set());
    expect(full.ok === false && full.error).toMatch(/has room for 21 more hosts from \.10 upward/);
  });

  it("counts as taken every address the export carries (keys and address fields) and every address: key the FILE holds", () => {
    const graph = fixtureGraph();
    const taken = takenAddresses(graph.nodes, [
      entry("add", { kind: "host", key: "address:10.80.0.99", note: "n" }),
      entry("suppress", { key: "address:10.9.9.9", note: "n" }),
      entry("override", { key: "address:10.9.9.8", field: "role", value: "x", note: "n" }),
    ]);
    expect(taken.has("10.80.20.5")).toBe(true); // an address: key
    expect(taken.has("10.80.20.10")).toBe(true); // hostname:axdc01's address field
    expect(taken.has("10.99.99.99")).toBe(true); // an unplaced local entity
    expect(taken.has("8.8.8.8")).toBe(true); // a non-local one
    expect(taken.has("10.80.0.99")).toBe(true); // an add in the file
    // A suppression or an override holds its key too: an add of it is `14` §3's
    // cross-record refusal, so an allocator that CHOOSES must not choose it.
    expect(taken.has("10.9.9.9")).toBe(true);
    expect(taken.has("10.9.9.8")).toBe(true);
  });

  it("a generated host is never numbered onto a key the file SUPPRESSES -- the batch would be refused", () => {
    const graph = fixtureGraph();
    const suppressed = [entry("suppress", { key: "address:10.80.0.11", note: "out of scope" }, "saved")];
    const allocation = allocateHostAddresses("10.80.0.0/24", 3, takenAddresses(graph.nodes, suppressed));
    expect(allocation).toEqual({ ok: true, addresses: ["10.80.0.10", "10.80.0.12", "10.80.0.13"] });
    // ...and had it been chosen, the batch path refuses it whole, naming it.
    const refused = buildBatch(
      [
        { kind: "add-segment", cidr: "10.80.0.0/24", name: "lab", note: "n" },
        { kind: "add-host", key: "address:10.80.0.11", segment: "10.80.0.0/24", note: "n" },
      ],
      suppressed,
    );
    expect(refused.ok === false && refused.error).toMatch(/^record 2 of 2 \(add host address:10\.80\.0\.11\): key address:10\.80\.0\.11 is already in `suppress`/);
  });

  it("a role the persona library serves prefills its table entry; one it does not prefills EMPTY and says so", () => {
    const dc = defaultListenFor("domain-controller");
    expect(dc.servedBy).toBe("persona");
    expect(dc.entries).toEqual(ROLE_LISTEN_DEFAULTS["domain-controller"]);
    expect(dc.entries).toContainEqual({ port: 88, proto: "tcp" });
    // Drive-aware: the DC's SERVED ports are the DC's, never its clients'.
    expect(defaultListenFor("corp-workstation").entries).not.toContainEqual({ port: 88, proto: "tcp" });
    expect(dc.entries).toContainEqual({ port: 53, proto: "udp" });
    // RESTATED (review M1): the result now carries the prefill's own caveat.
    const none = defaultListenFor("generic-endpoint");
    expect(none).toEqual({ entries: [], servedBy: "none", dialedPorts: false });
    expect(defaultListenFor("  ")).toEqual({ entries: [], servedBy: "none", dialedPorts: false });
    // A caller editing the prefill cannot write into the shared table.
    dc.entries[0]!.port = 1;
    expect(defaultListenFor("domain-controller").entries[0]!.port).not.toBe(1);
  });

  it("FLAGS a role whose default holds a port it dials, and the flag names real ports (M1)", () => {
    // The five roles the catalog files a dialled port under. Each one is a
    // `real-service` persona whose emit list mixes what it serves with what it
    // calls out to, and rule (b) cannot tell them apart.
    expect([...ROLES_WITH_DIALED_EMITS].sort()).toEqual([
      "db-replica",
      "egress-proxy",
      "monitoring-collector",
      "protocol-gateway",
      "web-tier",
    ]);
    for (const role of ROLES_WITH_DIALED_EMITS) {
      expect(defaultListenFor(role).dialedPorts, role).toBe(true);
      // Not an empty warning: each one really does have a default to prune.
      expect(defaultListenFor(role).servedBy, role).toBe("persona");
    }
    // The scenario the caveat is about: a generated web tier would be given
    // 3306 -- the port it DIALS on the db tier -- alongside the 443 it serves.
    expect(defaultListenFor("web-tier").entries).toContainEqual({ port: 3306, proto: "tcp" });
    expect(defaultListenFor("web-tier").entries).toContainEqual({ port: 443, proto: "tcp" });
    // ...and monitoring-collector's 9100/161, which it SCRAPES on workstations,
    // sit beside the 514/2055 it genuinely receives.
    expect(defaultListenFor("monitoring-collector").entries).toContainEqual({ port: 9100, proto: "tcp" });
    expect(defaultListenFor("monitoring-collector").entries).toContainEqual({ port: 514, proto: "udp" });
    // A role with no such emit is not flagged.
    expect(defaultListenFor("domain-controller").dialedPorts).toBe(false);
    expect(defaultListenFor("corp-workstation").dialedPorts).toBe(false);
  });

  it("leaves LOAD-GENERATOR scaffolding out of the table (S7)", () => {
    // `loadgen-*` personas exist to characterize capture throughput, not to
    // model an environment: their netflow emits had given every workstation and
    // DC four 999x/udp ports, and were the ONLY source of the roles `gw` and
    // `loadgen`.
    for (const role of ["corp-workstation", "domain-controller"]) {
      for (const port of [9995, 9996, 9997, 9998]) {
        expect(defaultListenFor(role).entries, `${role} ${port}`).not.toContainEqual({ port, proto: "udp" });
      }
    }
    expect(rolesWithListenDefaults()).not.toContain("loadgen");
    expect(rolesWithListenDefaults()).not.toContain("gw");
    // corp-workstation keeps only what a real persona reaches it on.
    expect(defaultListenFor("corp-workstation").entries).toEqual([
      { port: 22, proto: "tcp" },
      { port: 3389, proto: "tcp" },
      { port: 443, proto: "tcp" },
      { port: 445, proto: "tcp" },
      { port: 5985, proto: "tcp" },
      { port: 80, proto: "tcp" },
      { port: 9100, proto: "tcp" },
    ]);
    // `monitoring-collector` keeps 9997/TCP -- that is `log-shipper`'s splunk
    // emit, not a loadgen netflow port, and the two must not be confused.
    expect(defaultListenFor("monitoring-collector").entries).toContainEqual({ port: 9997, proto: "tcp" });
  });
});
