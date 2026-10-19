import { describe, expect, it } from "vitest";

import {
  canSave,
  documentFrom,
  editReducer,
  entriesInState,
  entryId,
  hasUnsavedEdits,
  initialEditState,
  suppressedKeysFromDiff,
  suppressedKeysFromPopulation,
  type EditStoreState,
} from "../src/edit/store";
import type { AssertionsDoc, ImprintDiff } from "../src/api/types";

function doc(overrides: Partial<AssertionsDoc> = {}): AssertionsDoc {
  return {
    imprint_id: "corpapcn-golden",
    path: "/bundles/corpapcn-golden/corpapcn-golden.assertions.yaml",
    exists: true,
    etag: "etag-1",
    schema: "axr-assertions/1",
    imprint: "corpapcn-golden",
    as_of: "2026-09-08T09:00:00Z",
    source: "operator: tester",
    add: [],
    override: [],
    suppress: [],
    warnings: [],
    ...overrides,
  };
}

const SUPPRESS = { key: "address:10.1.10.222", note: "decommissioned" };

function withPendingSuppress(): EditStoreState {
  return editReducer(initialEditState("corpapcn-golden"), {
    type: "stage",
    block: "suppress",
    record: SUPPRESS,
  });
}

describe("the three edit states", () => {
  it("stages a gesture as PENDING", () => {
    const state = withPendingSuppress();
    expect(entriesInState(state, "pending")).toHaveLength(1);
    expect(hasUnsavedEdits(state)).toBe(true);
    expect(state.entries[0]!.id).toBe(entryId("suppress", SUPPRESS));
  });

  it("re-staging the same gesture replaces it instead of duplicating", () => {
    const once = withPendingSuppress();
    const twice = editReducer(once, {
      type: "stage",
      block: "suppress",
      record: { ...SUPPRESS, note: "changed my mind about the note" },
    });
    expect(twice.entries).toHaveLength(1);
    expect(twice.entries[0]!.record.note).toBe("changed my mind about the note");
  });

  it("pending -> saved -> applied", () => {
    let state = withPendingSuppress();

    state = editReducer(state, { type: "saveStarted" });
    expect(state.saving).toBe(true);

    state = editReducer(state, {
      type: "saveSucceeded",
      doc: doc({ suppress: [SUPPRESS], etag: "etag-2" }),
    });
    expect(state.saving).toBe(false);
    expect(state.etag).toBe("etag-2");
    expect(entriesInState(state, "saved")).toHaveLength(1);
    expect(hasUnsavedEdits(state)).toBe(false);

    // Applied only when something downstream NAMES the key -- here the
    // diff's `suppressed_keys` (the §12.6 decision).
    state = editReducer(state, { type: "applied", block: "suppress", keys: [SUPPRESS.key] });
    expect(entriesInState(state, "applied")).toHaveLength(1);
    expect(entriesInState(state, "saved")).toHaveLength(0);
  });

  it("leaves a saved record SAVED when nothing named it", () => {
    let state = withPendingSuppress();
    state = editReducer(state, {
      type: "saveSucceeded",
      doc: doc({ suppress: [SUPPRESS] }),
    });
    state = editReducer(state, { type: "applied", block: "suppress", keys: ["address:10.9.9.9"] });
    expect(entriesInState(state, "saved")).toHaveLength(1);
    expect(entriesInState(state, "applied")).toHaveLength(0);
  });

  it("marks a pending record applied when an APPLY named it (apply IS save)", () => {
    const state = editReducer(withPendingSuppress(), {
      type: "applied",
      block: "suppress",
      keys: [SUPPRESS.key],
    });
    expect(entriesInState(state, "applied")).toHaveLength(1);
  });

  it("discards a pending record", () => {
    const state = editReducer(withPendingSuppress(), {
      type: "discard",
      id: entryId("suppress", SUPPRESS),
    });
    expect(state.entries).toHaveLength(0);
  });
});

describe("a refused save (412)", () => {
  it("leaves the pending edit intact and REPORTS the server's etag without adopting it", () => {
    // **Inverted pin (M1).** The first version of this test asserted the
    // store adopted `etag-9`, which reads like "surface the new etag" and is
    // actually the lost-update bug: the next save would carry the FRESHER
    // token, the server's precondition would pass, and the other writer's
    // records would be overwritten with every layer reporting success. The
    // store keeps its own known etag and gates the save instead.
    let state = editReducer(withPendingSuppress(), {
      type: "loaded",
      doc: doc({ etag: "etag-1" }),
    });
    state = editReducer(state, { type: "saveStarted" });
    state = editReducer(state, {
      type: "saveConflict",
      message:
        "the assertions file changed since you loaded it (on disk: etag-9)",
      serverEtag: "etag-9",
    });

    // The operator's unsaved work survives -- the whole point of a
    // recoverable precondition failure.
    expect(entriesInState(state, "pending")).toHaveLength(1);
    expect(state.entries[0]!.record).toEqual(SUPPRESS);
    expect(state.saving).toBe(false);
    expect(state.etag).toBe("etag-1");                    // NOT adopted
    expect(state.conflict?.serverEtag).toBe("etag-9");    // reported
    expect(state.conflict?.message).toMatch(/etag-9/);
    expect(state.mustReload).toBe(true);
    expect(canSave(state)).toBe(false);
  });

  it("a second save from a conflicted state still carries the STALE etag, and is gated", () => {
    // The two halves of M1 together: the document a retry would build still
    // names the etag the store legitimately knows (so the server refuses it
    // again rather than silently winning), and `canSave` refuses to issue it
    // at all until the file has been re-read.
    let state = editReducer(withPendingSuppress(), {
      type: "loaded",
      doc: doc({ etag: "etag-1" }),
    });
    state = editReducer(state, {
      type: "saveConflict",
      message: "stale (on disk: etag-9)",
      serverEtag: "etag-9",
    });

    const retry = documentFrom(state, {
      schema: "axr-assertions/1",
      imprint: "corpapcn-golden",
      as_of: null,
      source: null,
    });
    expect(retry["etag"]).toBe("etag-1");
    expect(canSave(state)).toBe(false);

    // Only re-reading the file clears the gate and adopts an etag.
    state = editReducer(state, { type: "loaded", doc: doc({ etag: "etag-9" }) });
    expect(state.mustReload).toBe(false);
    expect(state.etag).toBe("etag-9");
    expect(canSave(state)).toBe(true);
  });

  it("keeps the pending edit through the reload that follows", () => {
    let state = editReducer(withPendingSuppress(), {
      type: "saveConflict",
      message: "stale",
      serverEtag: "etag-9",
    });
    // The SPA re-reads the file; somebody else's records arrive as `saved`
    // and the local pending one is still there to retry with.
    state = editReducer(state, {
      type: "loaded",
      doc: doc({
        etag: "etag-9",
        add: [{ kind: "segment", key: "10.80.31.0/24", name: "ot", note: "n" }],
      }),
    });
    expect(entriesInState(state, "saved")).toHaveLength(1);
    expect(entriesInState(state, "pending")).toHaveLength(1);
    expect(state.conflict).toBeNull();
    expect(state.etag).toBe("etag-9");
  });

  it("drops a pending edit that the reload shows was already saved", () => {
    const state = editReducer(withPendingSuppress(), {
      type: "loaded",
      doc: doc({ suppress: [SUPPRESS] }),
    });
    expect(state.entries).toHaveLength(1);
    expect(state.entries[0]!.state).toBe("saved");
  });
});

describe("the document a save sends", () => {
  it("rebuilds the three blocks and carries the etag", () => {
    const state = editReducer(
      editReducer(withPendingSuppress(), {
        type: "saveSucceeded",
        doc: doc({ suppress: [SUPPRESS], etag: "etag-2" }),
      }),
      {
        type: "stage",
        block: "override",
        record: { key: "address:10.80.20.9", field: "role", value: "file-server", note: "n" },
      },
    );
    const body = documentFrom(state, {
      schema: "axr-assertions/1",
      imprint: "corpapcn-golden",
      as_of: "2026-09-08T09:00:00Z",
      source: "operator: tester",
    });
    expect(body["suppress"]).toEqual([SUPPRESS]);
    expect(body["override"]).toHaveLength(1);
    expect(body["add"]).toEqual([]);
    expect(body["etag"]).toBe("etag-2");
  });
});

describe("where `applied` is read from", () => {
  it("takes the diff's suppressed-key ADDITIONS", () => {
    const diff = {
      suppressed_keys: { added: ["address:10.1.10.222"], removed: [], changed: [] },
    } as unknown as ImprintDiff;
    expect(suppressedKeysFromDiff(diff)).toEqual(["address:10.1.10.222"]);
  });

  it("yields nothing when the diff could NOT compare (null, not empty)", () => {
    const diff = { suppressed_keys: null } as unknown as ImprintDiff;
    expect(suppressedKeysFromDiff(diff)).toEqual([]);
  });

  it("separates a matched suppression from an INEFFECTIVE one", () => {
    // `matched: false` is the compiler saying no entity carries this key --
    // the record is in the file and did nothing. Calling that "applied"
    // would tell the operator their edit worked.
    const { applied, ineffective } = suppressedKeysFromPopulation({
      suppressed: [
        { key: "address:10.1.10.222", placed: true, matched: true },
        { key: "hostname:ghost", placed: false, matched: false },
        "address:10.2.0.7",
      ],
    });
    expect(applied).toEqual(["address:10.1.10.222", "address:10.2.0.7"]);
    expect(ineffective).toEqual(["hostname:ghost"]);
  });

  it("reads an ABSENT population as no keys, not as an empty suppression set", () => {
    expect(suppressedKeysFromPopulation(undefined)).toEqual({
      applied: [],
      ineffective: [],
    });
    expect(suppressedKeysFromPopulation({})).toEqual({
      applied: [],
      ineffective: [],
    });
  });
});

// ===========================================================================
// The store contract under the shell's real sequences (tester pass).
//
// The cases above drive one transition at a time. These drive the sequences
// the shell actually produces -- a refused save with no etag to adopt, a
// partial save, an apply whose evidence arrives as a DIFF, and the imprint
// switch -- because that is where a reducer that passes every single-step
// test still loses an operator's work.
// ===========================================================================
describe("switching imprints", () => {
  it("gives the next imprint a pristine store, sharing nothing with the last", () => {
    // The shell keys the workspace by imprint id, so a switch UNMOUNTS the
    // store rather than migrating it (App.tsx). The contract that makes that
    // safe is here: a fresh state carries no entries, no etag and no
    // conflict, and creating one cannot disturb the outgoing store -- no
    // shared array, no module-level singleton.
    let a = withPendingSuppress();
    a = editReducer(a, {
      type: "saveConflict",
      message: "stale",
      serverEtag: "etag-9",
    });

    const b = initialEditState("some-other-imprint");
    expect(b).toEqual({
      imprintId: "some-other-imprint",
      etag: null,
      exists: false,
      entries: [],
      saving: false,
      conflict: null,
      mustReload: false,
      ineffective: [],
      problems: [],
    });
    // The outgoing store is untouched by the incoming one's creation.
    expect(entriesInState(a, "pending")).toHaveLength(1);
    expect(a.conflict?.serverEtag).toBe("etag-9");
    expect(b.entries).not.toBe(a.entries);
  });

  it("has NO action that changes which imprint the store belongs to", () => {
    // If one existed, a switch could reuse the store and carry one imprint's
    // pending edits (and its etag) into another imprint's file. The store is
    // deliberately immutable in this field; the remount is the switch.
    const start = withPendingSuppress();
    const actions: Parameters<typeof editReducer>[1][] = [
      { type: "loaded", doc: doc({ imprint_id: "elsewhere", imprint: "elsewhere" }) },
      { type: "stage", block: "add", record: { kind: "host", key: "address:10.0.0.1" } },
      { type: "discard", id: entryId("suppress", SUPPRESS) },
      { type: "saveStarted" },
      { type: "saveSucceeded", doc: doc({ imprint_id: "elsewhere" }) },
      { type: "saveConflict", message: "stale", serverEtag: "e" },
      { type: "saveFailed", message: "boom" },
      { type: "applied", block: "suppress", keys: ["address:10.1.10.222"] },
    ];
    for (const action of actions) {
      expect(editReducer(start, action).imprintId, action.type).toBe(
        "corpapcn-golden",
      );
    }
  });
});

describe("an apply, and the evidence that it landed", () => {
  it("marks a pending suppress APPLIED when the diff names its key", () => {
    // The whole §12.6 join, end to end at store level: the operator staged
    // `suppress: address:10.1.10.222`, the apply recompiled, and the only
    // thing that can say the compiler HONORED it is the diff's authored-key
    // set -- the twin side names `generic-endpoint-*`, which joins to
    // nothing the canvas authored.
    let state = editReducer(withPendingSuppress(), {
      type: "saveSucceeded",
      doc: doc({ suppress: [SUPPRESS], etag: "etag-2" }),
    });
    const diff = {
      suppressed_keys: { added: [SUPPRESS.key], removed: [], changed: [] },
    } as unknown as ImprintDiff;
    state = editReducer(state, {
      type: "applied",
      block: "suppress",
      keys: suppressedKeysFromDiff(diff),
    });
    expect(entriesInState(state, "applied")).toHaveLength(1);
  });

  it("leaves it SAVED when the diff names other keys, or none", () => {
    const saved = editReducer(withPendingSuppress(), {
      type: "saveSucceeded",
      doc: doc({ suppress: [SUPPRESS], etag: "etag-2" }),
    });
    const diffs: ImprintDiff[] = [
      // somebody else's suppression moved
      { suppressed_keys: { added: ["hostname:other"], removed: [], changed: [] } },
      // compared, nothing moved
      { suppressed_keys: { added: [], removed: [], changed: [] } },
      // could not compare (no accounting on one side)
      { suppressed_keys: null },
      // an older server that does not publish the section at all
      {},
    ].map((d) => d as unknown as ImprintDiff);
    for (const diff of diffs) {
      const state = editReducer(saved, {
        type: "applied",
        block: "suppress",
        keys: suppressedKeysFromDiff(diff),
      });
      expect(entriesInState(state, "saved"), JSON.stringify(diff)).toHaveLength(1);
      expect(entriesInState(state, "applied"), JSON.stringify(diff)).toHaveLength(0);
    }
  });

  it("reads an ABSENT `suppressed_keys` field as no keys, like a null one", () => {
    // A server older than S2 omits the field entirely. `undefined` must
    // behave exactly like the explicit `null`: no keys, no state change --
    // never "everything applied" and never a throw.
    expect(suppressedKeysFromDiff({} as unknown as ImprintDiff)).toEqual([]);
    expect(
      suppressedKeysFromDiff({ suppressed_keys: undefined } as unknown as ImprintDiff),
    ).toEqual([]);
  });

  it("records an INEFFECTIVE key without ever calling it applied", () => {
    // `matched: false` is the compiler saying no entity carries this key.
    // The record is in the file and did nothing; marking it applied would
    // tell the operator their edit worked.
    const population = {
      suppressed: [{ key: SUPPRESS.key, placed: false, matched: false }],
    };
    const { applied, ineffective } = suppressedKeysFromPopulation(population);
    const state = editReducer(withPendingSuppress(), {
      type: "applied",
      block: "suppress",
      keys: applied,
      ineffective,
    });
    expect(state.ineffective).toEqual([SUPPRESS.key]);
    expect(entriesInState(state, "applied")).toHaveLength(0);
    expect(entriesInState(state, "pending")).toHaveLength(1);

    // A later apply that DID match replaces the ineffective list rather than
    // accumulating a stale warning next to a working edit.
    const fixed = editReducer(state, { type: "applied", block: "suppress", keys: [SUPPRESS.key] });
    expect(fixed.ineffective).toEqual([]);
    expect(entriesInState(fixed, "applied")).toHaveLength(1);
  });
});

describe("a refused save, in the shapes a server really sends", () => {
  it("keeps the etag it had when the 412 carries none to adopt", () => {
    // A proxy can strip the body, or a 409/`saveFailed` can arrive with no
    // etag at all. Overwriting the known-good etag with `null` would make
    // the NEXT save unguarded -- a lost-update bug born of an error path.
    let state = editReducer(withPendingSuppress(), {
      type: "saveSucceeded",
      doc: doc({ suppress: [SUPPRESS], etag: "etag-2" }),
    });
    state = editReducer(state, {
      type: "saveConflict",
      message: "the file moved",
      serverEtag: null,
    });
    expect(state.etag).toBe("etag-2");
    expect(state.conflict?.serverEtag).toBeNull();
    expect(state.saving).toBe(false);
  });

  it("clears the conflict once the retry succeeds", () => {
    let state = editReducer(withPendingSuppress(), {
      type: "saveConflict",
      message: "stale",
      serverEtag: "etag-9",
    });
    state = editReducer(state, { type: "saveStarted" });
    expect(state.conflict).toBeNull();       // cleared on the attempt itself
    state = editReducer(state, {
      type: "saveSucceeded",
      doc: doc({ suppress: [SUPPRESS], etag: "etag-10" }),
    });
    expect(state.conflict).toBeNull();
    expect(state.etag).toBe("etag-10");
    expect(hasUnsavedEdits(state)).toBe(false);
  });

  it("records a non-412 failure without touching the etag or the edits", () => {
    const state = editReducer(withPendingSuppress(), {
      type: "saveFailed",
      message: "503: the bundle directory is not writable",
    });
    expect(state.conflict?.message).toMatch(/not writable/);
    expect(state.conflict?.serverEtag).toBeNull();
    expect(entriesInState(state, "pending")).toHaveLength(1);
  });
});

describe("a partial save", () => {
  it("promotes only the records the saved document actually names", () => {
    // The server can accept a document and normalize a record out of it (an
    // override of a field it refuses, say). Anything the returned file does
    // not carry is NOT saved, and calling it saved would strand an edit the
    // operator believes is on disk.
    const two = editReducer(withPendingSuppress(), {
      type: "stage",
      block: "add",
      record: { kind: "host", key: "address:10.80.20.5", segment: "10.80.20.0/24" },
    });
    const state = editReducer(two, {
      type: "saveSucceeded",
      doc: doc({ suppress: [SUPPRESS], etag: "etag-2" }),   // the `add` is absent
    });
    expect(entriesInState(state, "saved")).toHaveLength(1);
    expect(entriesInState(state, "pending")).toHaveLength(1);
    expect(entriesInState(state, "pending")[0]!.block).toBe("add");
  });
});

describe("the document a save sends, before the file exists", () => {
  it("omits `etag` entirely rather than sending a null one", () => {
    // The first save of a new file has nothing to guard against. An
    // `etag: null` in the body is a different request from no etag at all,
    // and the server reads the two differently.
    const body = documentFrom(withPendingSuppress(), {
      schema: "axr-assertions/1",
      imprint: "corpapcn-golden",
      as_of: null,
      source: null,
    });
    expect("etag" in body).toBe(false);
    expect("as_of" in body).toBe(false);
    expect("source" in body).toBe(false);
    expect(body["suppress"]).toEqual([SUPPRESS]);
  });
});

describe("one key, two blocks", () => {
  const OVERRIDE = {
    key: SUPPRESS.key,
    field: "role",
    value: "file-server",
    note: "it answers SMB",
  };

  it("applies only the BLOCK the evidence was about (S1)", () => {
    // An `override` and a `suppress` on one host are both legal records, and
    // the only evidence the Console gets for a suppression is a KEY. Matching
    // on the key alone marked the override applied too, on the strength of
    // evidence about the suppression.
    let state = editReducer(withPendingSuppress(), {
      type: "stage",
      block: "override",
      record: OVERRIDE,
    });
    state = editReducer(state, {
      type: "applied",
      block: "suppress",
      keys: [SUPPRESS.key],
    });

    const applied = entriesInState(state, "applied");
    expect(applied).toHaveLength(1);
    expect(applied[0]!.block).toBe("suppress");
    expect(entriesInState(state, "pending").map((e) => e.block)).toEqual([
      "override",
    ]);
  });
});

describe("a document for the WRONG imprint", () => {
  it("is refused and reported, never merged (D4)", () => {
    // The shell remounts the store per imprint, so this can only arrive as an
    // in-flight read for the OUTGOING imprint resolving into the incoming
    // one's store -- and merging it would write one imprint's records into
    // another imprint's file on the next save.
    const before = withPendingSuppress();
    const after = editReducer(before, {
      type: "loaded",
      doc: doc({ imprint_id: "some-other-imprint", suppress: [SUPPRESS] }),
    });
    expect(after.entries).toEqual(before.entries);
    expect(after.etag).toBeNull();
    expect(after.exists).toBe(false);
    expect(after.problems).toHaveLength(1);
    expect(after.problems[0]).toMatch(/some-other-imprint/);
    expect(after.problems[0]).toMatch(/corpapcn-golden/);
  });

  it("accepts the document for its own imprint", () => {
    const state = editReducer(withPendingSuppress(), {
      type: "loaded",
      doc: doc({ suppress: [SUPPRESS] }),
    });
    expect(state.problems).toEqual([]);
    expect(entriesInState(state, "saved")).toHaveLength(1);
  });
});

describe("a re-read of the file", () => {
  it("keeps an APPLIED record applied while it is still in the file", () => {
    // A reload is not evidence that the compiler stopped honoring a record;
    // demoting it to `saved` would silently undo the last apply's §12.6 join
    // and show the operator an edit that looks uncompiled.
    let state = editReducer(withPendingSuppress(), {
      type: "saveSucceeded",
      doc: doc({ suppress: [SUPPRESS], etag: "etag-2" }),
    });
    state = editReducer(state, {
      type: "applied",
      block: "suppress",
      keys: [SUPPRESS.key],
    });
    expect(entriesInState(state, "applied")).toHaveLength(1);

    state = editReducer(state, {
      type: "loaded",
      doc: doc({ suppress: [SUPPRESS], etag: "etag-2" }),
    });
    expect(entriesInState(state, "applied")).toHaveLength(1);
    expect(entriesInState(state, "saved")).toHaveLength(0);
  });

  it("does NOT resurrect an applied record the file no longer carries", () => {
    let state = editReducer(withPendingSuppress(), {
      type: "saveSucceeded",
      doc: doc({ suppress: [SUPPRESS], etag: "etag-2" }),
    });
    state = editReducer(state, {
      type: "applied",
      block: "suppress",
      keys: [SUPPRESS.key],
    });
    state = editReducer(state, { type: "loaded", doc: doc({ etag: "etag-3" }) });
    expect(state.entries).toHaveLength(0);
  });
});

