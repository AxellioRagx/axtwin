/**
 * The edit store: the three visible states of `docs/design/15-console-canvas.md`
 * §8 and the reducers that move between them, plus the assertions file's etag.
 *
 *   *pending*  -- in the browser only (hatched)
 *   *saved*    -- written to `<imprint>.assertions.yaml`, not yet compiled (outlined)
 *   *applied*  -- in the cast, and therefore rendered as an `asserted` element
 *
 * Pure: no React, no fetch, no clock. The shell owns the effects, this owns
 * the transitions, and the tests drive the transitions without a browser.
 *
 * **Why `applied` is not simply "the save succeeded"**: a saved record can be
 * *ineffective* -- a `suppress` naming a key no entity carries changes
 * nothing, and the compiler says so. So `applied` is only ever entered when
 * something downstream NAMES the record's key: the diff's `suppressed_keys`
 * or the bundle's own `population.suppressed[]` (the §12.6 decision, recorded
 * in `o2/console/README.md`). A record nobody named stays `saved` -- an
 * honest "written, effect unknown" rather than a claim.
 */
import type { AssertionBlock, AssertionRecord, AssertionsDoc } from "../api/types";
import type { ImprintDiff, ProvenancePopulation } from "../api/types";

export type EditState = "pending" | "saved" | "applied";

/**
 * The gesture a BATCH of records came from (S5.1.b/c): a clone, or a fresh
 * segment with generated hosts, stages `1 + N (+ M)` records at once
 * (`docs/design/15-console-canvas.md` §8, "one gesture, one batch"). Every entry
 * of the batch carries the same tag, so a later change list can show the
 * gesture with its records beneath it and undo them together (S5.1.d).
 *
 * **Browser knowledge, never file content.** The assertions file has no gesture
 * concept -- `documentFrom` writes `entry.record` and nothing else -- so a tag
 * survives a save and a re-read of the file only while the record it labels is
 * still the record on file (`loaded` below compares by VALUE), and a reload of
 * the page drops it.
 */
export interface GestureTag {
  /** Unique within this store's life. */
  id: string;
  /** A short human label -- `clone of 10.80.20.0/24 → 10.80.31.0/24`. */
  label: string;
  kind: "clone" | "generate";
  /** For a clone: the observed CIDR it copies. */
  source?: string;
}

export interface EditEntry {
  /** `<block>:<key>` (`<block>:<key>:<field>` for an override) -- stable, so
   *  re-staging the same gesture replaces rather than duplicates. */
  id: string;
  block: AssertionBlock;
  state: EditState;
  record: AssertionRecord;
  /** The batch gesture this record was staged by, when it was (S5.1.b/c). */
  gesture?: GestureTag;
}

/** One record of a batch, as `edit/records.ts` builds it. */
export interface BatchRecord {
  block: AssertionBlock;
  record: AssertionRecord;
}

/** One record coming back from a draft, with the gesture it was staged by
 *  (S5.1.d) -- so a restored clone is still ONE entry in the change list. */
export interface DraftRestoreEntry extends BatchRecord {
  gesture?: GestureTag | undefined;
}

export interface EditConflict {
  message: string;
  /**
   * The etag the server says is on disk. **Reported, never adopted** (M1):
   * see `saveConflict` below -- this is what the reload prompt shows and
   * what a human can compare, not a token the next save may reuse.
   */
  serverEtag: string | null;
}

export interface EditStoreState {
  imprintId: string;
  /** The assertions file's etag; `null` before the file exists. */
  etag: string | null;
  exists: boolean;
  entries: EditEntry[];
  saving: boolean;
  /** The last refused write. Cleared by a successful save or a reload. */
  conflict: EditConflict | null;
  /**
   * A save was refused as stale and the file has NOT been re-read yet. Only
   * `loaded` clears it, so the UI cannot re-issue a save from a conflicted
   * state -- the etag the store holds is known-stale, and a save carrying it
   * would either be refused again or, if the store had adopted the server's
   * token, succeed and overwrite the other writer.
   */
  mustReload: boolean;
  /** Keys the compiler reported as suppressed-but-matching-nothing. */
  ineffective: string[];
  /**
   * Inputs this store REFUSED, with the reason -- today only a document for
   * the wrong imprint (D4). Published rather than dropped, on the same rule
   * the API's tolerant listing follows: a silent refusal is indistinguishable
   * from a store that simply never received anything.
   */
  problems: string[];
}

export type EditAction =
  /** `GET .../assertions` came back: the file's records are `saved`. */
  | { type: "loaded"; doc: AssertionsDoc }
  /** A gesture produced a record (S5 owns the gestures; this owns the state). */
  | { type: "stage"; block: AssertionBlock; record: AssertionRecord }
  /**
   * A gesture produced a BATCH (S5.1.b/c): staged all-or-nothing. A record
   * whose id the store already holds -- or that the batch names twice -- would
   * silently REPLACE an entry under `stage`'s rule, so the whole batch is
   * refused instead and the reason lands in `problems`.
   */
  | { type: "stageBatch"; records: readonly BatchRecord[]; gesture: GestureTag }
  /**
   * A DRAFT came back from this browser's storage (S5.1.d, `edit/draft.ts`).
   * Unlike `stageBatch` this is not one gesture -- a draft holds whatever was
   * pending, from any number of gestures -- and unlike `stage` it never
   * replaces: `reconcileDraft` has already dropped every record the file now
   * holds, and this repeats the check so the store's rule does not rest on the
   * caller having asked (LES-330's defence-in-depth clause).
   */
  | { type: "restoreDraft"; entries: readonly DraftRestoreEntry[] }
  | { type: "discard"; id: string }
  | { type: "saveStarted" }
  /** `PUT`/`POST` succeeded: pending becomes saved and the etag advances. */
  | { type: "saveSucceeded"; doc: AssertionsDoc }
  /** 412: the file moved under us. The pending edits are NOT lost. */
  | { type: "saveConflict"; message: string; serverEtag: string | null }
  | { type: "saveFailed"; message: string }
  /**
   * Something downstream named these keys as compiled, **within one block**.
   * The block is required because one key legitimately carries records in
   * more than one block -- an `override` and a `suppress` on the same host
   * are both valid -- and a key-only match would mark the override applied
   * on the strength of evidence about the suppression (S1).
   */
  | {
      type: "applied";
      block: AssertionBlock;
      keys: readonly string[];
      ineffective?: readonly string[];
    };

export function initialEditState(imprintId: string): EditStoreState {
  return {
    imprintId,
    etag: null,
    exists: false,
    entries: [],
    saving: false,
    conflict: null,
    mustReload: false,
    ineffective: [],
    problems: [],
  };
}

export function entryId(block: AssertionBlock, record: AssertionRecord): string {
  const key = record.key ?? "";
  return block === "override" && record.field
    ? `${block}:${key}:${record.field}`
    : `${block}:${key}`;
}

/**
 * Why `stageBatch` would refuse this batch whole, or `null` -- the store's own
 * identity rule, as a PURE function.
 *
 * Exported because the caller that stages a batch also has SIDE EFFECTS to
 * commit with it (a clone's placement is an S4 pin, a selection, a session
 * position), and those must hang off the same decision the reducer makes rather
 * than off a parallel one (review S4). Two independent checks is exactly how a
 * refused batch ends up with a pin and a selection for records that were never
 * staged; `edit/batch.ts` runs a strictly WIDER check earlier, which is what
 * makes this one unreachable today -- defence in depth, not dead code.
 */
export function batchRefusal(
  entries: readonly { id: string }[],
  records: readonly BatchRecord[],
): string | null {
  if (records.length === 0) return "it holds no record";
  const ids = records.map((r) => entryId(r.block, r.record));
  const held = new Set(entries.map((e) => e.id));
  const clash = ids.find((id, i) => held.has(id) || ids.indexOf(id) !== i);
  if (clash === undefined) return null;
  return held.has(clash)
    ? `${clash} is already in this store, and a batch never replaces a record`
    : `it names ${clash} twice`;
}

export function editReducer(
  state: EditStoreState,
  action: EditAction,
): EditStoreState {
  switch (action.type) {
    case "loaded": {
      // D4: a document for ANOTHER imprint is refused, not merged. The shell
      // remounts the store per imprint (the React `key` in App.tsx), so this
      // can only happen when an in-flight read for the outgoing imprint
      // resolves into the incoming one's store -- and merging it would write
      // one imprint's records into another imprint's file on the next save.
      if (action.doc.imprint_id !== state.imprintId) {
        return {
          ...state,
          problems: [
            ...state.problems,
            `ignored an assertions document for imprint ` +
              `"${action.doc.imprint_id}" -- this store belongs to ` +
              `"${state.imprintId}"`,
          ],
        };
      }
      // The file is the authority for what is SAVED; pending edits are the
      // operator's unsaved work and survive a reload of the file -- that is
      // the whole point of holding them separately.
      //
      // An entry the store already knows is APPLIED keeps that state while it
      // is still in the file: a re-read is not evidence that the compiler
      // stopped honoring it, and demoting it to `saved` would silently undo
      // the §12.6 join the last apply established.
      //
      // A GESTURE TAG (S5.1.b/c) survives the re-read on a stricter rule: only
      // while the file's record is still the very record the gesture staged,
      // compared by VALUE. The id alone is `<block>:<key>`, and another writer
      // may have replaced that key's record -- keeping the tag then would label
      // their record as part of this browser's clone (LES-296's value rule).
      const prior = new Map(state.entries.map((e) => [e.id, e]));
      const fromFile = entriesFromDoc(action.doc).map((e) => {
        const before = prior.get(e.id);
        let next: EditEntry = before?.state === "applied" ? { ...e, state: "applied" as const } : e;
        if (before?.gesture && sameRecord(before.record, e.record)) next = { ...next, gesture: before.gesture };
        return next;
      });
      const savedIds = new Set(fromFile.map((e) => e.id));
      const pending = state.entries.filter(
        (e) => e.state === "pending" && !savedIds.has(e.id),
      );
      return {
        ...state,
        etag: action.doc.etag,
        exists: action.doc.exists,
        entries: [...fromFile, ...pending],
        conflict: null,
        // The only action that clears the reload gate (M1). Two actions
        // adopt an etag and both are legitimate: this one (a READ of the
        // file) and `saveSucceeded` (the server's etag for bytes it just
        // wrote for us). The one that must NOT is `saveConflict` -- an etag
        // learned from a REFUSAL describes somebody else's write.
        mustReload: false,
      };
    }
    case "stage": {
      const id = entryId(action.block, action.record);
      const entry: EditEntry = {
        id,
        block: action.block,
        state: "pending",
        record: action.record,
      };
      const at = state.entries.findIndex((e) => e.id === id);
      const entries =
        at < 0
          ? [...state.entries, entry]
          : state.entries.map((e, i) => (i === at ? entry : e));
      return { ...state, entries };
    }
    case "stageBatch": {
      const why = batchRefusal(state.entries, action.records);
      if (why !== null) {
        return {
          ...state,
          problems: [...state.problems, `refused the batch "${action.gesture.label}": ${why} — nothing was staged`],
        };
      }
      const added: EditEntry[] = action.records.map((r) => ({
        id: entryId(r.block, r.record),
        block: r.block,
        state: "pending",
        record: r.record,
        gesture: action.gesture,
      }));
      return { ...state, entries: [...state.entries, ...added] };
    }
    case "restoreDraft": {
      // Additive and non-replacing: an id the store already holds stays as it
      // is. `reconcileDraft` drops those first and SAYS which; this is the
      // store's own guarantee that a draft can never overwrite the file's
      // record, whatever a caller passes.
      const held = new Set(state.entries.map((e) => e.id));
      const added: EditEntry[] = [];
      for (const r of action.entries) {
        const id = entryId(r.block, r.record);
        if (held.has(id)) continue;
        held.add(id);
        added.push({
          id,
          block: r.block,
          state: "pending",
          record: r.record,
          ...(r.gesture ? { gesture: r.gesture } : {}),
        });
      }
      if (added.length === 0) return state;
      return { ...state, entries: [...state.entries, ...added] };
    }
    case "discard":
      return {
        ...state,
        entries: state.entries.filter((e) => e.id !== action.id),
      };
    case "saveStarted":
      return { ...state, saving: true, conflict: null };
    case "saveSucceeded": {
      const saved = new Set(entriesFromDoc(action.doc).map((e) => e.id));
      return {
        ...state,
        saving: false,
        conflict: null,
        etag: action.doc.etag,
        exists: action.doc.exists,
        entries: state.entries.map((e) =>
          e.state === "pending" && saved.has(e.id) ? { ...e, state: "saved" } : e,
        ),
      };
    }
    case "saveConflict":
      // The pending edits stay EXACTLY as they were -- losing an operator's
      // unsaved work because the file moved would be the worst possible
      // answer to a recoverable precondition failure.
      //
      // **The store does NOT adopt the server's etag** (M1). Adopting it
      // turns the API's last-writer-REFUSED guard into last-writer-WINS: the
      // next save carries the fresher token, the server's precondition
      // passes, and the other writer's records are overwritten by a document
      // that was never reconciled with them -- with every layer reporting
      // success. The value is reported in `conflict.serverEtag` for the
      // reload prompt, and `mustReload` gates the save until the file has
      // actually been re-read.
      return {
        ...state,
        saving: false,
        mustReload: true,
        conflict: { message: action.message, serverEtag: action.serverEtag },
      };
    case "saveFailed":
      return {
        ...state,
        saving: false,
        conflict: { message: action.message, serverEtag: null },
      };
    case "applied": {
      const named = new Set(action.keys);
      return {
        ...state,
        saving: false,
        ineffective: [...(action.ineffective ?? [])],
        entries: state.entries.map((e) =>
          e.block === action.block &&
          e.record.key !== undefined &&
          named.has(e.record.key)
            ? { ...e, state: "applied" }
            : e,
        ),
      };
    }
  }
}

// ---------------------------------------------------------------------------
// selectors
// ---------------------------------------------------------------------------
export function entriesInState(
  state: EditStoreState,
  which: EditState,
): EditEntry[] {
  return state.entries.filter((e) => e.state === which);
}

export function hasUnsavedEdits(state: EditStoreState): boolean {
  return state.entries.some((e) => e.state === "pending");
}

/**
 * May a save be issued right now? `false` while one is in flight, and
 * `false` after a stale-etag refusal until the file has been re-read (M1) --
 * the store's etag is known-stale at that point, so any save built from it
 * is either a second refusal or, worse, a lost update.
 */
export function canSave(state: EditStoreState): boolean {
  return !state.saving && !state.mustReload;
}

/** The document to `PUT` / to hand to `apply`: the file's shape, rebuilt from
 *  every entry that is not purely local scaffolding. */
export function documentFrom(
  state: EditStoreState,
  base: Pick<AssertionsDoc, "schema" | "imprint" | "as_of" | "source">,
): Record<string, unknown> {
  const block = (which: AssertionBlock): AssertionRecord[] =>
    state.entries.filter((e) => e.block === which).map((e) => e.record);
  return {
    schema: base.schema,
    imprint: base.imprint,
    ...(base.as_of ? { as_of: base.as_of } : {}),
    ...(base.source ? { source: base.source } : {}),
    add: block("add"),
    override: block("override"),
    suppress: block("suppress"),
    // The WRITE ENVELOPE's guard, not content: `axr-assertions/1` does not
    // describe `etag` and refuses it (`additionalProperties: false`), and the
    // server never writes it -- confirmed on both sides
    // (`test/batch.roundtrip.test.ts` for the schema, `tests/
    // test_api_console.py::test_a_clone_shaped_batch_re_saves_byte_identical`
    // for the bytes on disk).
    ...(state.etag ? { etag: state.etag } : {}),
  };
}

// ---------------------------------------------------------------------------
// what "applied" is read from
// ---------------------------------------------------------------------------
/**
 * The authored keys a diff says became suppressed since a fingerprint.
 * `null`/absent is "could not compare", which yields no keys and therefore
 * no state change -- never an implied "nothing was applied".
 */
export function suppressedKeysFromDiff(diff: ImprintDiff): string[] {
  return diff.suppressed_keys ? [...diff.suppressed_keys.added] : [];
}

/**
 * The durable authority, readable at any time from `GET .../provenance`:
 * the compiler's own `population.suppressed[]`.
 *
 * A row with `matched: false` is the compiler saying **no entity carries this
 * key** -- the record is in the file and did nothing. Reporting it as applied
 * would tell the operator their edit worked, so it comes back separately.
 */
export function suppressedKeysFromPopulation(
  population: ProvenancePopulation | undefined,
): { applied: string[]; ineffective: string[] } {
  const rows = population?.suppressed;
  const applied: string[] = [];
  const ineffective: string[] = [];
  if (!Array.isArray(rows)) return { applied, ineffective };
  for (const row of rows) {
    if (typeof row === "string") {
      applied.push(row);
      continue;
    }
    if (typeof row?.key !== "string") continue;
    if (row.matched === false) ineffective.push(row.key);
    else applied.push(row.key);
  }
  return { applied, ineffective };
}

/** Every entry a batch gesture staged, in store order -- what S5.1.d's
 *  per-gesture change list groups and its undo removes together. */
export function entriesOfGesture(state: EditStoreState, gestureId: string): EditEntry[] {
  return state.entries.filter((e) => e.gesture?.id === gestureId);
}

/**
 * Two records are the same record when they carry the same fields with the same
 * values, whatever order the keys arrive in -- the server re-emits records in
 * its own key order, so a key-order-sensitive compare would call every re-read
 * record "changed".
 */
export function sameRecord(a: AssertionRecord, b: AssertionRecord): boolean {
  return canonicalJson(a) === canonicalJson(b);
}

function canonicalJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value !== null && typeof value === "object") {
    const obj = value as Record<string, unknown>;
    return `{${Object.keys(obj)
      .filter((k) => obj[k] !== undefined)
      .sort()
      .map((k) => `${JSON.stringify(k)}:${canonicalJson(obj[k])}`)
      .join(",")}}`;
  }
  return JSON.stringify(value);
}

export function entriesFromDoc(doc: AssertionsDoc): EditEntry[] {
  const of = (block: AssertionBlock, records: AssertionRecord[]): EditEntry[] =>
    records.map((record) => ({
      id: entryId(block, record),
      block,
      state: "saved" as const,
      record,
    }));
  return [
    ...of("add", doc.add),
    ...of("override", doc.override),
    ...of("suppress", doc.suppress),
  ];
}
