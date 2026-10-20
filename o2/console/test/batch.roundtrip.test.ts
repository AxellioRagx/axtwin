// @vitest-environment node
/**
 * S5.1.b/c acceptance 2 -- the FILE a batch writes: it validates against the
 * vendored `axr-assertions/1` schema, and re-reads byte-identically after a
 * canonical re-emit. S5's round-trip machinery (`records.roundtrip.test.ts`),
 * extended to a batch and to the schema file itself:
 *
 *   batch -> store -> `documentFrom` (what the client PUTs)
 *         -> validated against `axrange/api/schemas/axr-assertions-1.schema.json`
 *         -> written in the canonical FILE shape -> read back
 *         -> validated again -> re-emitted -> byte-identical
 *
 * **What "canonical" means here, and what this does not prove.** The re-emit is
 * the writers' shape, stated from their code rather than invented: axRange's
 * `console_files.py` `_dump_assertions`/`_file_record` (`kind`, `key`, the
 * content fields SORTED, `note`; `resources` keys sorted) and aximprint's
 * `operator_assertions.py` `to_document` port rule (a `listen`/`dial` list
 * deduplicated and ordered by its `"<port>/<proto>"` string). A batch whose
 * records are already in that form re-emits to itself -- and the test ALSO
 * asserts the re-read records EQUAL the ones the client sent, so a batch the
 * re-emit silently reordered or deduplicated fails here rather than churning
 * the file on its first save. The bytes on disk are YAML, which the SERVER
 * writes; its byte-identical no-op save is `tests/test_api_console.py`'s (S1).
 * This package has no YAML writer, so the bytes compared here are JSON.
 *
 * Node environment: real files (LES-260); its OWN temp directory (LES-233).
 */
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { afterAll, beforeAll, describe, expect, it } from "vitest";

import type { CastSummary } from "../src/api/types";
import { allocateHostAddresses, takenAddresses } from "../src/edit/allocate";
import { buildBatch } from "../src/edit/batch";
import { cloneDefaults, planClone } from "../src/edit/clone";
import { canonicalPorts, type Gesture } from "../src/edit/records";
import { documentFrom, editReducer, initialEditState, type EditEntry, type EditStoreState } from "../src/edit/store";
import { buildGraph, type ConsoleGraph } from "../src/model/graph";
import { defaultListenFor } from "../src/model/roles";
import { castInputFrom } from "../src/views/useGraph";
import { bundleAvailable, REAL_BUNDLES, realGraph } from "./fixtures/realBundles";
import { trimmedCast, trimmedExport } from "./fixtures/trimmed";
import { validateAgainst } from "./helpers/jsonschema";

const SCHEMA = JSON.parse(
  readFileSync(fileURLToPath(new URL("../../../axrange/api/schemas/axr-assertions-1.schema.json", import.meta.url)), "utf8"),
) as Record<string, unknown>;

const BASE = {
  schema: "axr-assertions/1",
  as_of: "2026-09-15T00:00:00Z",
  source: "operator: tester",
};

// ---------------------------------------------------------------------------
// the canonical re-emit
// ---------------------------------------------------------------------------
type Doc = Record<string, unknown> & {
  add: Array<Record<string, unknown>>;
  override: Array<Record<string, unknown>>;
  suppress: Array<Record<string, unknown>>;
};

function canonicalPortList(value: unknown): unknown {
  const entries = value as Array<{ port: number; proto: "icmp" | "tcp" | "udp" }>;
  return canonicalPortsByText(entries);
}

/** aximprint's rule, restated from `operator_assertions.py` so the test does not
 *  certify the SPA with the SPA's own `canonicalPorts`: dedupe the
 *  `"<port>/<proto>"` strings, sort them, write `{port, proto}` back. */
function canonicalPortsByText(entries: Array<{ port: number; proto: string }>): Array<{ port: number; proto: string }> {
  return [...new Set(entries.map((e) => `${e.port}/${e.proto}`))]
    .sort()
    .map((text) => ({ port: Number(text.split("/")[0]), proto: text.split("/")[1]! }));
}

function fileRecord(rec: Record<string, unknown>, block: string): Record<string, unknown> {
  if (block === "add") {
    const content = Object.keys(rec)
      .filter((k) => k !== "kind" && k !== "key" && k !== "note")
      .sort();
    const out: Record<string, unknown> = { kind: rec.kind, key: rec.key };
    for (const k of content) {
      const v = rec[k];
      out[k] =
        k === "listen" || k === "dial"
          ? canonicalPortList(v)
          : k === "resources"
            ? Object.fromEntries(Object.keys(v as object).sort().map((rk) => [rk, (v as Record<string, unknown>)[rk]]))
            : v;
    }
    out.note = rec.note;
    return out;
  }
  if (block === "override") return { key: rec.key, field: rec.field, value: rec.value, note: rec.note };
  return { key: rec.key, note: rec.note };
}

function emit(doc: Doc): string {
  const file = {
    schema: doc.schema,
    imprint: doc.imprint,
    as_of: doc.as_of,
    source: doc.source,
    add: doc.add.map((r) => fileRecord(r, "add")),
    override: doc.override.map((r) => fileRecord(r, "override")),
    suppress: doc.suppress.map((r) => fileRecord(r, "suppress")),
  };
  return `${JSON.stringify(file, null, 2)}\n`;
}

// ---------------------------------------------------------------------------
// the validator itself must refuse -- or a green run proves nothing
// ---------------------------------------------------------------------------
describe("the vendored-schema evaluator refuses what the schema refuses", () => {
  const good: Doc = {
    ...BASE,
    imprint: "x",
    add: [{ kind: "host", key: "address:10.80.0.5", segment: "10.80.0.0/24", listen: [{ port: 88, proto: "tcp" }], note: "n" }],
    override: [],
    suppress: [],
  };

  it("accepts a known-good document", () => {
    expect(validateAgainst(SCHEMA, good)).toEqual([]);
  });

  it.each([
    ["an empty listen list (portList minItems 1)", { listen: [] }],
    ["a protocol outside the closed set", { listen: [{ port: 88, proto: "sctp" }] }],
    ["a port above 65535", { listen: [{ port: 70000, proto: "tcp" }] }],
    ["an unknown host field", { evidence_class: "observed" }],
    ["an empty resources block", { resources: {} }],
    ["a resource key outside cpu/disk/ram", { resources: { gpu: 1 } }],
    ["a blank note", { note: "  " }],
  ])("refuses %s", (_name, patch) => {
    const bad = { ...good, add: [{ ...good.add[0]!, ...patch }] };
    expect(validateAgainst(SCHEMA, bad).length).toBeGreaterThan(0);
  });

  it("refuses a route add by name and a missing top-level `source`", () => {
    expect(validateAgainst(SCHEMA, { ...good, add: [{ kind: "route", key: "10.0.0.0/24", note: "n" }] }).length).toBeGreaterThan(0);
    const { source: _dropped, ...noSource } = good;
    expect(validateAgainst(SCHEMA, noSource).length).toBeGreaterThan(0);
  });

  it("throws on a keyword it does not implement, rather than skipping it", () => {
    expect(() => validateAgainst({ type: "object", uniqueItems: true }, {})).toThrow(/unsupported JSON Schema keyword "uniqueItems"/);
  });
});

// ---------------------------------------------------------------------------
// the round trip
// ---------------------------------------------------------------------------
describe("S5.1.b/c acceptance 2 -- a batch's file validates, and re-reads byte-identical after a canonical re-emit", () => {
  let dir: string;
  beforeAll(() => {
    dir = mkdtempSync(join(tmpdir(), "axr-console-s51b-"));
  });
  afterAll(() => {
    rmSync(dir, { recursive: true, force: true });
  });

  function roundTrip(state: EditStoreState, imprint: string, name: string): void {
    const document = documentFrom(state, { ...BASE, imprint }) as Doc;
    // What the client PUTs is valid against the schema file itself...
    expect(validateAgainst(SCHEMA, document)).toEqual([]);

    const path = join(dir, `${name}.assertions.json`);
    const written = emit(document);
    writeFileSync(path, written, "utf-8");
    const reread = JSON.parse(readFileSync(path, "utf-8")) as Doc;

    // ...so is what was written, and a canonical re-emit of it is the same bytes.
    expect(validateAgainst(SCHEMA, reread)).toEqual([]);
    expect(emit(reread)).toBe(written);
    // The re-emit changed NOTHING the client sent -- record order, every value,
    // every port list's order -- so the first save is already the canonical
    // file (key ORDER aside, which JSON objects do not carry into `toEqual`).
    expect(reread.add).toEqual(document.add);
    expect(reread.override).toEqual(document.override);
    expect(reread.suppress).toEqual(document.suppress);
    for (const rec of document.add) {
      for (const field of ["listen", "dial"] as const) {
        const list = rec[field] as Array<{ port: number; proto: string }> | undefined;
        if (list) expect(list, `${String(rec.key)} ${field}`).toEqual(canonicalPortsByText(list));
      }
    }
  }

  for (const bundle of REAL_BUNDLES) {
    it.skipIf(!bundleAvailable(bundle))(`a default clone of ${bundle.id}'s 10.80.20.0/24, conduits mirrored`, () => {
      const graph = realGraph(bundle);
      const d = cloneDefaults(graph, [], "10.80.20.0/24");
      const plan = planClone(graph, [], "10.80.20.0/24", {
        cidr: d.cidr!,
        name: d.name,
        zoneType: d.zoneType,
        hosts: { mode: "all" },
        mapping: "same-host-part",
        mirrorConduits: true,
        note: "a second lab floor",
      });
      if (!plan.ok) throw new Error(plan.error);
      const batch = buildBatch(plan.plan.gestures, []);
      if (!batch.ok) throw new Error(batch.error);
      const state = editReducer(initialEditState(bundle.id), {
        type: "stageBatch",
        records: batch.staged,
        gesture: { id: "g1", label: plan.plan.label, kind: "clone", source: "10.80.20.0/24" },
      });
      // All three add kinds are in the file under test.
      expect(new Set(state.entries.map((e) => e.record.kind))).toEqual(new Set(["segment", "host", "conduit"]));
      roundTrip(state, bundle.id, bundle.id);
    });
  }

  it("a clone carrying an asserted image and sizing, beside the file's own saved records", () => {
    const cast = {
      deploy_min_occupancy: 0.8,
      conduit_roster: trimmedCast.conduits,
      routers: trimmedCast.routers,
      segment_roster: trimmedExport.segments.map((s) => ({ name: s.name, cidr: s.cidr, zone_type: null })),
    } as unknown as CastSummary;
    const graph: ConsoleGraph = buildGraph(trimmedExport, castInputFrom(cast, "trimmed-fixture"));
    const saved: EditEntry[] = [
      {
        id: "override:address:10.80.20.5:image",
        block: "override",
        state: "saved",
        record: { key: "address:10.80.20.5", field: "image", value: "debian-12", note: "pin it" },
      },
      {
        id: "override:address:10.80.20.30:resources",
        block: "override",
        state: "saved",
        // Keys out of order on purpose: the canonical re-emit sorts them, and
        // the clone must already carry them sorted.
        record: { key: "address:10.80.20.30", field: "resources", value: { ram: "4096", cpu: 2 }, note: "size it" },
      },
    ];
    const before = { ...initialEditState("trimmed-fixture"), entries: saved };
    const d = cloneDefaults(graph, saved, "10.80.20.0/24");
    const plan = planClone(graph, saved, "10.80.20.0/24", {
      cidr: d.cidr!,
      name: d.name,
      zoneType: d.zoneType,
      hosts: { mode: "all" },
      mapping: "same-host-part",
      mirrorConduits: true,
      note: "n",
    });
    if (!plan.ok) throw new Error(plan.error);
    const batch = buildBatch(plan.plan.gestures, saved);
    if (!batch.ok) throw new Error(batch.error);
    const state = editReducer(before, {
      type: "stageBatch",
      records: batch.staged,
      gesture: { id: "g1", label: plan.plan.label, kind: "clone", source: "10.80.20.0/24" },
    });
    const withImage = state.entries.find((e) => e.record.key === "address:10.80.0.5");
    expect(withImage?.record.image).toBe("debian-12");
    expect(Object.keys(state.entries.find((e) => e.record.key === "address:10.80.0.30")?.record.resources ?? {})).toEqual(["cpu", "ram"]);
    roundTrip(state, "trimmed-fixture", "image-and-sizing");
  });

  it("a fresh segment with generated hosts (S5.1.c)", () => {
    const graph = buildGraph(trimmedExport, trimmedCast);
    const taken = takenAddresses(graph.nodes, []);
    const allocation = allocateHostAddresses("10.80.0.0/24", 3, taken);
    if (!allocation.ok) throw new Error(allocation.error);
    const listen = canonicalPorts(defaultListenFor("domain-controller").entries);
    const gestures: Gesture[] = [
      { kind: "add-segment", cidr: "10.80.0.0/24", name: "seg-10-80-0-0", zoneType: "simple", note: "n" },
      ...allocation.addresses.map(
        (a): Gesture => ({ kind: "add-host", key: `address:${a}`, segment: "10.80.0.0/24", role: "domain-controller", listen, note: "n" }),
      ),
    ];
    const batch = buildBatch(gestures, []);
    if (!batch.ok) throw new Error(batch.error);
    const state = editReducer(initialEditState("trimmed-fixture"), {
      type: "stageBatch",
      records: batch.staged,
      gesture: { id: "g1", label: "add segment 10.80.0.0/24 with 3 hosts", kind: "generate" },
    });
    roundTrip(state, "trimmed-fixture", "generated");
  });

  it("acceptance 5 -- undoing a SAVED clone leaves the file byte-identical to before it", () => {
    // The pending case is trivial (nothing was written); the one that can drift
    // is a clone the operator SAVED and then took back, where the file is
    // re-emitted from the entries that remain. Compared on the BYTES, against a
    // document captured before the clone existed (iteration-8 tester).
    const graph = realGraph(REAL_BUNDLES[0]!);
    const before: EditEntry[] = [
      { id: "add:address:10.80.20.99", block: "add", state: "saved",
        record: { kind: "host", key: "address:10.80.20.99", segment: "10.80.20.0/24", note: "a host of my own" } },
      { id: "suppress:hostname:axdc01", block: "suppress", state: "saved",
        record: { key: "hostname:axdc01", note: "out of scope" } },
    ];
    const base = { ...BASE, imprint: REAL_BUNDLES[0]!.id };
    const start: EditStoreState = { ...initialEditState(REAL_BUNDLES[0]!.id), entries: before };
    const original = emit(documentFrom(start, base) as Doc);

    const d = cloneDefaults(graph, before, "10.80.20.0/24");
    const plan = planClone(graph, before, "10.80.20.0/24", {
      cidr: d.cidr!, name: d.name, zoneType: d.zoneType, hosts: { mode: "all" },
      mapping: "same-host-part", mirrorConduits: true, note: "a second lab floor",
    });
    if (!plan.ok) throw new Error(plan.error);
    const batch = buildBatch(plan.plan.gestures, before);
    if (!batch.ok) throw new Error(batch.error);
    const staged = editReducer(start, {
      type: "stageBatch", records: batch.staged,
      gesture: { id: "g1", label: plan.plan.label, kind: "clone", source: "10.80.20.0/24" },
    });
    // SAVED: the file now holds the operator's two records AND the whole batch.
    const savedDoc = documentFrom(staged, base) as Doc;
    const savedState = editReducer(staged, {
      type: "saveSucceeded",
      doc: { imprint_id: base.imprint, path: "", exists: true, etag: "e2", schema: BASE.schema, imprint: base.imprint,
             as_of: BASE.as_of, source: BASE.source, add: savedDoc.add as never, override: savedDoc.override as never,
             suppress: savedDoc.suppress as never, warnings: [] },
    });
    expect(savedState.entries.every((e) => e.state === "saved")).toBe(true);
    expect(savedState.entries.length).toBe(before.length + batch.staged.length);
    expect(emit(documentFrom(savedState, base) as Doc)).not.toBe(original);

    // UNDO the gesture -- every record it staged, by its id, as the change
    // list's "remove all" does -- and the file is the one from before.
    const ids = new Set(batch.staged.map((s) => `${s.block}:${s.record.key ?? ""}`));
    let undone = savedState;
    for (const id of ids) undone = editReducer(undone, { type: "discard", id });
    expect(undone.entries.map((e) => e.id)).toEqual(before.map((e) => e.id));
    const restored = emit(documentFrom(undone, base) as Doc);
    expect(restored).toBe(original);
    // Validated as the FILE, not as the client's write envelope: `documentFrom`
    // also carries the `etag` a PUT is guarded with, which the file schema
    // (`additionalProperties: false`) does not describe and `emit` drops.
    expect(validateAgainst(SCHEMA, JSON.parse(restored) as Doc)).toEqual([]);
  });

  it("the PUT envelope's `etag` is a REQUEST field the file schema refuses -- and it never reaches the file", () => {
    // The reviewer's "one thing to check": `documentFrom` carries `etag`, which
    // `axr-assertions/1` does not describe. That is correct and deliberate --
    // it is the write guard, not content -- and the two must be provably
    // distinct, or a writer that passed the body through would put it on disk.
    const state = editReducer({ ...initialEditState("x"), etag: "abc123" }, {
      type: "stageBatch",
      records: [{ block: "add", record: { kind: "segment", key: "10.80.0.0/24", name: "a", note: "n" } }],
      gesture: { id: "g1", label: "l", kind: "generate" },
    });
    const envelope = documentFrom(state, { ...BASE, imprint: "x" });
    expect(envelope["etag"]).toBe("abc123");
    // The FILE schema refuses it: `additionalProperties: false` (LES-271 -- the
    // client mirror is never stricter than the server, and here it is exact).
    const problems = validateAgainst(SCHEMA, envelope as unknown as Doc);
    expect(problems.join(" ")).toMatch(/etag/);
    // ...and what is emitted as the file drops it.
    expect(validateAgainst(SCHEMA, JSON.parse(emit(envelope as Doc)) as Doc)).toEqual([]);
    expect(emit(envelope as Doc)).not.toMatch(/etag/);
    // The server's own half -- that a PUT carrying it writes no `etag:` key --
    // is `tests/test_api_console.py`'s
    // `test_a_clone_shaped_batch_re_saves_byte_identical`.
  });

  it("a gesture tag never reaches the file", () => {
    const state = editReducer(initialEditState("x"), {
      type: "stageBatch",
      records: [{ block: "add", record: { kind: "segment", key: "10.80.0.0/24", name: "a", note: "n" } }],
      gesture: { id: "g1", label: "l", kind: "generate" },
    });
    expect(state.entries[0]!.gesture?.id).toBe("g1");
    const text = JSON.stringify(documentFrom(state, { ...BASE, imprint: "x" }));
    expect(text).not.toMatch(/gesture|g1/);
  });
});
