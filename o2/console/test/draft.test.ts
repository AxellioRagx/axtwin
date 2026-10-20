// @vitest-environment node
/**
 * S5.1.d acceptance 6 -- the per-imprint DRAFT (`src/edit/draft.ts`, closing
 * `docs/PRODUCT_GAPS.md` GAP-094).
 *
 * The pure half: what is stored, what comes back, what is dropped on the way,
 * and what a blocked or full store does. The round trip through the real shell
 * -- a reload, an imprint switch, the discard offer -- is `s51d.ui.test.tsx`.
 *
 * Node environment: this reads the source tree for the write-token guard
 * (LES-260), and drives its own `Storage` doubles rather than jsdom's.
 */
import { readFileSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import {
  clearDraft,
  DRAFT_KEY_PREFIX,
  DRAFT_SCHEMA,
  draftKey,
  readDraft,
  reconcileDraft,
  restoreNotice,
  writeDraft,
  type Draft,
} from "../src/edit/draft";
import { newGestureId } from "../src/edit/gestures";
import { crossRecordConflict, invalidStoredRecord } from "../src/edit/records";
import { editReducer, entryId, initialEditState, sameRecord, type EditEntry } from "../src/edit/store";
import type { AssertionRecord } from "../src/api/types";

/** A `Storage` that behaves, with the contents visible to the test. */
function fakeStorage(seed: Record<string, string> = {}): Storage & { map: Map<string, string> } {
  const map = new Map(Object.entries(seed));
  return {
    map,
    get length() {
      return map.size;
    },
    clear: () => map.clear(),
    getItem: (k: string) => map.get(k) ?? null,
    key: (i: number) => [...map.keys()][i] ?? null,
    removeItem: (k: string) => void map.delete(k),
    setItem: (k: string, v: string) => void map.set(k, v),
  } as Storage & { map: Map<string, string> };
}

/** A `Storage` whose every operation throws -- a hardened profile, a full one. */
function hostileStorage(err: Error): Storage {
  const boom = (): never => {
    throw err;
  };
  return {
    length: 0,
    clear: boom,
    getItem: boom,
    key: boom,
    removeItem: boom,
    setItem: boom,
  } as unknown as Storage;
}

const HOST = { kind: "host" as const, key: "address:10.80.31.10", segment: "10.80.31.0/24", note: "n" };
const SEGMENT = { kind: "segment" as const, key: "10.80.31.0/24", name: "lab", note: "n" };

function draftOf(entries: Draft["entries"], over: Partial<Draft> = {}): Draft {
  return {
    schema: DRAFT_SCHEMA,
    imprint: "bundle-a",
    saved_at: "2026-09-16T10:00:00Z",
    etag: "etag-1",
    bundle_fingerprint: "fp-1",
    entries,
    ...over,
  };
}

const WORLD = { etag: "etag-1", fingerprint: "fp-1", heldIds: new Set<string>() };

/** The deps the app passes: the store's value compare, the writer's refusals
 *  and its shape check -- so these tests exercise the same back door the shell
 *  opens, not a looser one. */
function deps(held: ReadonlyArray<{ block: "add" | "override" | "suppress"; record: AssertionRecord }> = []) {
  return { heldRecords: held, sameRecord, crossRecordConflict, validRecord: invalidStoredRecord };
}

describe("the draft round-trips one imprint's pending records", () => {
  it("writes under a key scoped to the imprint, and reads exactly what was written", () => {
    const store = fakeStorage();
    const draft = draftOf([{ block: "add", record: SEGMENT }, { block: "add", record: HOST }]);
    expect(writeDraft(draft, store)).toEqual({ ok: true });
    expect([...store.map.keys()]).toEqual([`${DRAFT_KEY_PREFIX}bundle-a`]);
    expect(readDraft("bundle-a", store)).toEqual({ draft, problem: null });
    // ...and NOT into another imprint's slot: the key is the whole separation
    // (LES-259, applied to storage).
    expect(readDraft("bundle-b", store)).toEqual({ draft: null, problem: null });
  });

  it("an EMPTY draft clears the key rather than storing an empty document", () => {
    // Otherwise the next visit is offered a restore of nothing.
    const store = fakeStorage({ [draftKey("bundle-a")]: "{}" });
    expect(writeDraft(draftOf([]), store)).toEqual({ ok: true });
    expect(store.map.size).toBe(0);
    expect(readDraft("bundle-a", store).draft).toBeNull();
  });

  it("clearDraft removes only its own imprint's key", () => {
    const store = fakeStorage({
      [draftKey("bundle-a")]: "x",
      [draftKey("bundle-b")]: "y",
      "axrange.console.outline": "closed",
    });
    expect(clearDraft("bundle-a", store)).toEqual({ ok: true });
    expect([...store.map.keys()].sort()).toEqual([draftKey("bundle-b"), "axrange.console.outline"].sort());
  });
});

describe("a draft this module did not write is DISCARDED, with the reason", () => {
  const cases: Array<[string, string, RegExp]> = [
    ["not JSON", "{oh no", /not readable JSON/],
    ["another schema", JSON.stringify({ schema: "axr-console-draft/9", imprint: "bundle-a", entries: [] }), /not a axr-console-draft\/1 draft/],
    ["another imprint", JSON.stringify({ schema: DRAFT_SCHEMA, imprint: "bundle-b", entries: [] }), /not a .* draft for this imprint/],
    ["no entries array", JSON.stringify({ schema: DRAFT_SCHEMA, imprint: "bundle-a" }), /not a .* draft/],
    ["a bad block", JSON.stringify({ schema: DRAFT_SCHEMA, imprint: "bundle-a", entries: [{ block: "delete", record: {} }] }), /not a .* draft/],
    ["a non-object record", JSON.stringify({ schema: DRAFT_SCHEMA, imprint: "bundle-a", entries: [{ block: "add", record: 7 }] }), /not a .* draft/],
  ];
  for (const [what, raw, reason] of cases) {
    it(`refuses ${what}`, () => {
      const store = fakeStorage({ [draftKey("bundle-a")]: raw });
      const result = readDraft("bundle-a", store);
      expect(result.draft).toBeNull();
      expect(result.problem).toMatch(reason);
    });
  }
});

describe("a blocked or full store degrades VISIBLY and never throws (acceptance 6)", () => {
  it("says so on a read, a write and a clear -- and returns rather than raising", () => {
    const blocked = hostileStorage(new Error("The operation is insecure."));
    expect(readDraft("bundle-a", blocked)).toEqual({
      draft: null,
      problem: "the draft could not be read (The operation is insecure.)",
    });
    const write = writeDraft(draftOf([{ block: "add", record: HOST }]), blocked);
    expect(write).toEqual({ ok: false, reason: "The operation is insecure." });
    expect(clearDraft("bundle-a", blocked).ok).toBe(false);
  });

  it("names a FULL store as full, in words an operator can act on", () => {
    const full = new Error("quota");
    full.name = "QuotaExceededError";
    const write = writeDraft(draftOf([{ block: "add", record: HOST }]), hostileStorage(full));
    expect(write).toEqual({ ok: false, reason: "this browser's storage for this site is full" });
  });

  it("a MISSING store (no window, a hardened profile) is reported, not crashed into", () => {
    expect(readDraft("bundle-a", null).problem).toMatch(/no storage available/);
    expect(writeDraft(draftOf([{ block: "add", record: HOST }]), null).ok).toBe(false);
    expect(clearDraft("bundle-a", null).ok).toBe(false);
  });
});

describe("restoring is RECONCILIATION, never a replay (acceptance 6)", () => {
  it("restores what still applies, in draft order, with each record's gesture", () => {
    const gesture = { id: "g1", label: "clone of a → b", kind: "clone" as const, source: "a" };
    const draft = draftOf([
      { block: "add", record: SEGMENT, gesture },
      { block: "add", record: HOST, gesture },
    ]);
    const result = reconcileDraft(draft, WORLD, entryId, deps());
    expect(result.restore.map((r) => r.record.key)).toEqual(["10.80.31.0/24", "address:10.80.31.10"]);
    expect(result.gestures).toEqual([gesture, gesture]);
    expect(result.dropped).toEqual([]);
    expect(result.moved).toEqual([]);
  });

  it("DROPS a record the file now holds, naming it -- the merge LES-251 forbids", () => {
    const draft = draftOf([{ block: "add", record: SEGMENT }, { block: "add", record: HOST }]);
    const held = new Set([entryId("add", HOST)]);
    const held2 = HOST;
    const result = reconcileDraft(draft, { ...WORLD, heldIds: held }, entryId, deps([{ block: "add", record: held2 }]));
    expect(result.restore.map((r) => r.record.key)).toEqual(["10.80.31.0/24"]);
    expect(result.dropped).toEqual([
      { id: "add:address:10.80.31.10", why: "the file already holds this record, so the draft's copy was not restored" },
    ]);
  });

  it("restores a record named TWICE only once, and says so", () => {
    const draft = draftOf([{ block: "add", record: HOST }, { block: "add", record: HOST }]);
    const result = reconcileDraft(draft, WORLD, entryId, deps());
    expect(result.restore).toHaveLength(1);
    expect(result.dropped[0]!.why).toMatch(/named this record twice/);
  });

  it("REPORTS a file or a bundle that moved on, and still offers the work", () => {
    const draft = draftOf([{ block: "add", record: HOST }]);
    const moved = reconcileDraft(draft, { etag: "etag-9", fingerprint: "fp-9", heldIds: new Set() }, entryId, deps());
    expect(moved.restore).toHaveLength(1);
    expect(moved.moved).toEqual([
      "the assertions file has been written since this draft was taken",
      "the bundle has been recompiled since this draft was taken (its content has changed)",
    ]);
    // An UNKNOWN fingerprint on either side is not a difference: a bundle that
    // publishes none must not read as "recompiled" every single time.
    const unknown = reconcileDraft(draftOf([{ block: "add", record: HOST }], { bundle_fingerprint: null }), WORLD, entryId, deps());
    expect(unknown.moved).toEqual([]);
  });

  it("the notice names the count, when it was taken, what was left out and what moved", () => {
    const draft = draftOf([{ block: "add", record: SEGMENT }, { block: "add", record: HOST }]);
    const result = reconcileDraft(
      draft,
      { etag: "etag-9", fingerprint: "fp-1", heldIds: new Set([entryId("add", HOST)]) },
      entryId,
      deps([{ block: "add", record: HOST }]),
    );
    expect(restoreNotice(draft, result)).toBe(
      "Restored 1 unsaved edit from a draft from 2026-09-16T10:00:00Z kept on this browser. " +
        "1 record was left out: add:address:10.80.31.10 — the file already holds this record, so the draft's " +
        "copy was not restored. The assertions file has been written since this draft was taken.",
    );
  });

  it("says so when NOTHING could be restored -- silence would read as 'there was no draft'", () => {
    const draft = draftOf([{ block: "add", record: HOST }]);
    const result = reconcileDraft(draft, { ...WORLD, heldIds: new Set([entryId("add", HOST)]) }, entryId, deps([{ block: "add", record: HOST }]));
    expect(result.restore).toEqual([]);
    expect(restoreNotice(draft, result)).toMatch(/^A draft from 2026-09-16T10:00:00Z was found, and none of it could be restored/);
  });
});

describe("the STORE's own restore rule (defence in depth, LES-330)", () => {
  it("never replaces a record the store already holds, whatever the caller passes", () => {
    const held: EditEntry = { id: entryId("add", HOST), block: "add", state: "saved", record: HOST };
    const before = { ...initialEditState("bundle-a"), entries: [held] };
    const after = editReducer(before, {
      type: "restoreDraft",
      entries: [
        { block: "add", record: { ...HOST, note: "the draft's other note" } },
        { block: "add", record: SEGMENT },
      ],
    });
    // The saved record is untouched...
    expect(after.entries[0]).toBe(held);
    // ...and only the new one arrived, as pending.
    expect(after.entries).toHaveLength(2);
    expect(after.entries[1]!.state).toBe("pending");
    expect(after.entries[1]!.record.key).toBe("10.80.31.0/24");
  });

  it("restores the gesture tag, so a restored clone is still ONE change-list entry", () => {
    const gesture = { id: "g1", label: "clone of a → b", kind: "clone" as const };
    const after = editReducer(initialEditState("bundle-a"), {
      type: "restoreDraft",
      entries: [{ block: "add", record: SEGMENT, gesture }, { block: "add", record: HOST, gesture }],
    });
    expect(after.entries.map((e) => e.gesture?.id)).toEqual(["g1", "g1"]);
  });

  it("a restore of nothing returns the SAME state object -- no render for an empty draft", () => {
    const before = initialEditState("bundle-a");
    expect(editReducer(before, { type: "restoreDraft", entries: [] })).toBe(before);
  });
});

// ===========================================================================
// the rule that is not negotiable
// ===========================================================================
describe("the write token is never persisted", () => {
  const SRC = fileURLToPath(new URL("../src/", import.meta.url));

  function sources(dir: string): string[] {
    const out: string[] = [];
    for (const e of readdirSync(dir, { withFileTypes: true })) {
      const path = `${dir}${e.name}`;
      if (e.isDirectory()) out.push(...sources(`${path}/`));
      else if (/\.tsx?$/.test(e.name)) out.push(path);
    }
    return out;
  }

  it("every storage write in the source uses a known key, and none of them mentions a token", () => {
    // A structural grep, not a promise in a comment: `api/client.ts` keeps the
    // bearer token in a module variable for the life of the tab, and this fails
    // the moment any code writes something token-shaped into a browser store.
    const files = sources(SRC);
    expect(files.length).toBeGreaterThan(20);
    // BALANCED parens (review N1): `[^)]*` stopped at the first inner `)`, so
    // `setItem(k, JSON.stringify(draft))` was scanned as far as `stringify(`
    // and the VALUE was never looked at -- while the module's own docstring
    // claimed every write was checked.
    const writes: Array<{ file: string; call: string }> = [];
    for (const file of files) {
      const text = readFileSync(file, "utf8");
      let at = text.indexOf(".setItem");
      while (at >= 0) {
        const open = text.indexOf("(", at);
        let depth = 0;
        let end = open;
        for (; end < text.length; end++) {
          if (text[end] === "(") depth += 1;
          else if (text[end] === ")") {
            depth -= 1;
            if (depth === 0) break;
          }
        }
        writes.push({ file: file.slice(SRC.length), call: text.slice(at, end + 1) });
        at = text.indexOf(".setItem", end + 1);
      }
    }
    // The scan really reaches the value argument, or it proves nothing.
    expect(writes.some((w) => w.call.includes("JSON.stringify"))).toBe(true);
    // The three storage writers this app has, and no others (the Telemetry
    // page keeps the typed Grafana address -- a URL, not a credential).
    expect(writes.map((w) => w.file).sort()).toEqual(["App.tsx", "areas/telemetry/TelemetryPage.tsx", "edit/draft.ts"]);
    for (const w of writes) {
      expect(w.call, w.file).not.toMatch(/token|bearer|secret|password|authorization/i);
    }
    // The keys they write under are the two documented ones.
    const app = readFileSync(`${SRC}App.tsx`, "utf8");
    expect(app).toMatch(/const OUTLINE_KEY = "axrange\.console\.outline"/);
    expect(DRAFT_KEY_PREFIX).toBe("axrange.console.draft.");
    const telemetry = readFileSync(`${SRC}areas/telemetry/TelemetryPage.tsx`, "utf8");
    expect(telemetry).toMatch(/const STORAGE_KEY = "axrange\.console\.grafanaUrl"/);
  });

  it("nothing a draft carries can hold one: a draft is records, and records are the file's own shape", () => {
    const store = fakeStorage();
    writeDraft(draftOf([{ block: "add", record: HOST }]), store);
    const raw = store.map.get(draftKey("bundle-a"))!;
    expect(raw).not.toMatch(/token/i);
    // The stored document's top-level keys are exactly the draft's.
    expect(Object.keys(JSON.parse(raw)).sort()).toEqual(
      ["bundle_fingerprint", "entries", "etag", "imprint", "saved_at", "schema"],
    );
  });
});

// ===========================================================================
// the combined fix round -- M3, S2, S3, S8
// ===========================================================================
describe("the recompile check has THREE outcomes, not two (M3)", () => {
  const draft = draftOf([{ block: "add", record: HOST }], { bundle_fingerprint: "fp-OLD" });

  it("reports a bundle whose CONTENT fingerprint moved -- a recompile, not necessarily a new compiler", () => {
    const r = reconcileDraft(draft, { etag: "etag-1", fingerprint: "fp-NEW", heldIds: new Set() }, entryId, deps());
    expect(r.moved).toEqual(["the bundle has been recompiled since this draft was taken (its content has changed)"]);
    expect(r.moved[0]).not.toMatch(/compiler build/);
  });

  it("says nothing when the bundle is the SAME", () => {
    const r = reconcileDraft(draft, { etag: "etag-1", fingerprint: "fp-OLD", heldIds: new Set() }, entryId, deps());
    expect(r.moved).toEqual([]);
  });

  it("says the check could not be MADE when the cast could not be read", () => {
    // `null` used to cover both "this bundle publishes none" and "it has not
    // arrived / could not be read", so a cast that resolved second skipped the
    // warning silently. `"unknown"` is the third outcome (LES-273).
    const r = reconcileDraft(draft, { etag: "etag-1", fingerprint: "unknown", heldIds: new Set() }, entryId, deps());
    expect(r.moved).toEqual([
      "whether the bundle has been recompiled since this draft was taken could not be checked — this bundle's summary could not be read",
    ]);
    // ...and the work is still offered.
    expect(r.restore).toHaveLength(1);
  });

  it("a bundle that genuinely publishes NO fingerprint is not a recompile", () => {
    const r = reconcileDraft(draft, { etag: "etag-1", fingerprint: null, heldIds: new Set() }, entryId, deps());
    expect(r.moved).toEqual([]);
  });
});

describe("a draft kept BEFORE the content stamp existed (fix round 1, GAP-087..090)", () => {
  // What an earlier Console stored: `fingerprint` (the PIT build stamp, which
  // no recompile moves) and no `bundle_fingerprint` at all.
  const LEGACY = JSON.stringify({
    schema: DRAFT_SCHEMA,
    imprint: "bundle-a",
    saved_at: "2026-09-16T10:00:00Z",
    etag: "etag-1",
    fingerprint: "fixture-0000000000",
    entries: [{ block: "add", record: HOST }],
  });

  it("reads as UNKNOWN, never as unchanged (LES-273/LES-333) -- and the work is still offered", () => {
    const store = fakeStorage();
    store.map.set(draftKey("bundle-a"), LEGACY);
    const { draft } = readDraft("bundle-a", store);
    expect(draft).not.toBeNull();
    expect(draft!.bundle_fingerprint).toBeUndefined();
    // Even against a world whose fingerprint would MATCH anything it might
    // have held, the check is not made.
    const r = reconcileDraft(draft!, { etag: "etag-1", fingerprint: "fp-1", heldIds: new Set() }, entryId, deps());
    expect(r.moved).toEqual([
      "whether the bundle has been recompiled since this draft was taken could not be checked — the draft " +
        "was kept by an earlier Console that did not record which bundle it was taken against",
    ]);
    expect(r.restore).toHaveLength(1);
  });

  it("a legacy draft re-written without a save STAYS unknown -- not silently re-stamped", () => {
    const store = fakeStorage();
    store.map.set(draftKey("bundle-a"), LEGACY);
    const { draft } = readDraft("bundle-a", store);
    writeDraft({ ...draft! }, store);
    const again = readDraft("bundle-a", store).draft!;
    expect(again.bundle_fingerprint).toBeUndefined();
  });
});

describe("the back door runs the front door's refusals (S2, S3, S8)", () => {
  it("drops an `add` whose key the file now SUPPRESSES, in `14` §3's own words", () => {
    // No gesture can produce this pair, and the store had no way to refuse it:
    // the draft's copy and the file's suppression both went into the next PUT.
    const suppressed = { key: "address:10.80.31.10", note: "out of scope" };
    const draft = draftOf([{ block: "add", record: HOST }]);
    const r = reconcileDraft(
      draft,
      { etag: "etag-1", fingerprint: "fp-1", heldIds: new Set([entryId("suppress", suppressed)]) },
      entryId,
      deps([{ block: "suppress", record: suppressed }]),
    );
    expect(r.restore).toEqual([]);
    expect(r.dropped[0]!.why).toMatch(/^key address:10\.80\.31\.10 is already in `suppress`/);
    expect(r.dropped[0]!.why).toMatch(/the draft's copy was not restored$/);
  });

  it("refuses a conflict the draft makes WITH ITSELF, not only with the file", () => {
    const draft = draftOf([
      { block: "add", record: HOST },
      { block: "suppress", record: { key: "address:10.80.31.10", note: "and also suppress it" } },
    ]);
    const r = reconcileDraft(draft, WORLD, entryId, deps());
    expect(r.restore).toHaveLength(1);
    expect(r.restore[0]!.block).toBe("add");
    expect(r.dropped[0]!.why).toMatch(/is already in `add`/);
  });

  it("distinguishes 'the file has this record' from 'the file has a DIFFERENT one' (S3)", () => {
    const different = { ...HOST, role: "file-server", note: "somebody else's" };
    const draft = draftOf([{ block: "add", record: HOST }]);
    const same = reconcileDraft(
      draft,
      { ...WORLD, heldIds: new Set([entryId("add", HOST)]) },
      entryId,
      deps([{ block: "add", record: HOST }]),
    );
    expect(same.dropped[0]!.why).toBe("the file already holds this record, so the draft's copy was not restored");
    const diverged = reconcileDraft(
      draft,
      { ...WORLD, heldIds: new Set([entryId("add", different)]) },
      entryId,
      deps([{ block: "add", record: different }]),
    );
    expect(diverged.dropped[0]!.why).toBe(
      "the file now holds a different record under this key, so the draft's copy was not restored",
    );
  });

  it("drops a record no gesture could have made (S8), naming the shape rule", () => {
    const draft = draftOf([
      { block: "add", record: { nonsense: true, key: 42 } as unknown as AssertionRecord },
      { block: "add", record: { kind: "host", key: "address:10.80.31.11", segment: "10.80.31.0/24" } as AssertionRecord },
      { block: "add", record: { kind: "wormhole", key: "x", note: "n" } as unknown as AssertionRecord },
      { block: "override", record: { key: "address:10.80.31.12", field: "colour", value: "red", note: "n" } as unknown as AssertionRecord },
    ]);
    const r = reconcileDraft(draft, WORLD, entryId, deps());
    expect(r.restore).toEqual([]);
    expect(r.dropped.map((d) => d.why)).toEqual([
      expect.stringMatching(/non-empty string `key`/) as unknown as string,
      expect.stringMatching(/non-empty note/) as unknown as string,
      expect.stringMatching(/an `add` record's kind is one of segment \/ host \/ conduit, not "wormhole"/) as unknown as string,
      expect.stringMatching(/an `override` record's field is one of role \/ image \/ resources, not "colour"/) as unknown as string,
    ]);
  });

  it("a well-formed record still comes back -- the shape check refuses only what could not be ours", () => {
    const draft = draftOf([{ block: "add", record: HOST }, { block: "suppress", record: { key: "address:9.9.9.9", note: "out of scope" } }]);
    expect(reconcileDraft(draft, WORLD, entryId, deps()).restore).toHaveLength(2);
  });
});

describe("a gesture id is unique to the SESSION, not to a mount (M2)", () => {
  it("never repeats, and is not a bare counter a remount would restart", () => {
    const ids = new Set<string>();
    for (let i = 0; i < 500; i++) ids.add(newGestureId());
    expect(ids.size).toBe(500);
    // A bare `gesture-<n>` is exactly the shape that collided with a restored
    // draft: the id must carry something the next mount cannot reproduce.
    for (const id of ids) expect(id).not.toMatch(/^gesture-\d+$/);
  });
});

describe("the draft's fingerprint advances on a SAVE, and on nothing else (fix round A)", () => {
  const APP = fileURLToPath(new URL("../src/App.tsx", import.meta.url));

  it("is not written by any effect keyed on an etag -- a STRUCTURAL guard", () => {
    // The two behavioural tests for this (`s51d.ui.test.tsx`) catch the bug
    // only when run ALONE: the defect is a race between the restore setting
    // `restoredRef` and the store's first etag arriving, and the preceding
    // tests shift the interleaving enough to hide it in a whole-file run. So
    // the mechanism is pinned here as well, where nothing can reorder it.
    //
    // The rule: the stamp is advanced in `performSave`'s success continuation
    // (the one place that means "a save happened") and in the restore, where it
    // takes the draft's OWN value. An effect watching `edits.etag` advanced it
    // on the initial load's etag -- no save, no second writer -- which made the
    // recompile warning one-shot.
    //
    // WHICH HALF CARRIES THE WEIGHT: the positional count below. The name check
    // that follows pins the retired ref by its old spelling, so the same
    // mechanism re-introduced under another name would slip past it -- it is a
    // cheap reminder, not the guard.
    const src = readFileSync(APP, "utf8");
    expect(src).toMatch(/draftFingerprintRef/);
    expect(src).not.toMatch(/lastSavedEtagRef/);
    // Every assignment, with the enclosing construct.
    const assignments = [...src.matchAll(/draftFingerprintRef\.current\s*=/g)].map((m) => {
      const before = src.slice(0, m.index ?? 0);
      const effect = before.lastIndexOf("useEffect(");
      const save = before.lastIndexOf("const performSave");
      return effect > save ? "effect" : "performSave";
    });
    expect(assignments.length).toBeGreaterThan(0);
    // The restore effect legitimately sets it (to the draft's own value); the
    // only OTHER writer is the save continuation.
    expect(assignments.filter((a) => a === "performSave")).toHaveLength(1);
  });
});
