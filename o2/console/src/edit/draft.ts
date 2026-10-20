/**
 * The per-imprint DRAFT (`docs/WORK_ORDER_console_canvas.md` §3 S5.1.d;
 * `docs/PRODUCT_GAPS.md` GAP-094) -- the pending records of one imprint, kept on
 * this browser so a reload, the breadcrumb, an outline link or the back button
 * no longer discard them.
 *
 * S5.1's acceptance has described this since the amendment ("pending edits
 * persist in the store's draft"); until now no such mechanism existed, and
 * **Escape was the only guarded exit**. This is that mechanism.
 *
 * **What goes in, and what must never.** A draft holds only `pending` entries --
 * the records staged in this browser and not yet written to the assertions file
 * -- with the gesture tag each came from. It is operator content, not a
 * credential: the API's **write token lives in a module variable for the life of
 * the tab and is never persisted** (`api/client.ts`), and nothing here may
 * change that. `test/draft.test.ts` greps the source for every `setItem` to
 * prove no storage write can reach it.
 *
 * **Every access is wrapped.** `localStorage` throws on a hardened profile
 * (access itself throws, not just the call), and a `setItem` throws when the
 * origin's quota is full. A draft that cannot be written must SAY so once --
 * the operator is entitled to know their edits are browser-session-only after
 * all -- and must never take the app down with it, so every entry point here
 * returns an outcome and none of them throw.
 *
 * **Restoring is reconciliation, never a replay** (`reconcileDraft`). The file
 * and the bundle can both have moved on while a draft sat: another writer saved,
 * the cast was recompiled. So a record whose entry id the FILE now holds is
 * DROPPED with the reason -- restoring it would silently re-stage an edit over
 * somebody else's saved record, which is the merge LES-251 forbids -- and a
 * changed assertions etag or bundle CONTENT fingerprint is reported beside the
 * restored set rather than silently accepted. Restoring stages nothing on the server:
 * the records return as `pending`, and the save path's own 412 guard still
 * stands behind them.
 *
 * **The draft MIRRORS the pending set, which bounds how long a kept one lives.**
 * A draft refused by the world (`reconcileDraft`'s `keepDraft`) is held so the
 * operator can resolve the conflict and get their copy back -- but the next
 * pending write in that imprint replaces it, because the whole self-healing
 * property of this module is that the stored draft is whatever is pending now.
 * So "kept so you can resolve it" holds until other work is staged there; the
 * restore notice offers *discard the draft* throughout, and nothing is lost
 * silently.
 *
 * Pure except for the four storage functions, which take the `Storage` to use so
 * a test can drive a full or a throwing one.
 */
import type { AssertionBlock, AssertionRecord } from "../api/types";
import type { BatchRecord, GestureTag } from "./store";

/** The draft document's own schema. Bumped when the shape changes; a draft
 *  carrying any other value is discarded rather than guessed at. */
export const DRAFT_SCHEMA = "axr-console-draft/1";

/** One `localStorage` key per imprint, so switching imprints cannot carry one
 *  imprint's edits into another's file (LES-259's rule, applied to storage). */
export const DRAFT_KEY_PREFIX = "axrange.console.draft.";

export function draftKey(imprintId: string): string {
  return `${DRAFT_KEY_PREFIX}${imprintId}`;
}

/** A pending record as the draft stores it -- exactly `BatchRecord` plus the
 *  gesture tag, so a restored clone is still one gesture in the change list. */
export interface DraftEntry {
  block: AssertionBlock;
  record: AssertionRecord;
  gesture?: GestureTag;
}

export interface Draft {
  schema: string;
  /** The CANONICAL imprint id (LES-263) -- checked on read, so a draft can
   *  never be rehydrated into a different imprint's store. */
  imprint: string;
  /** When it was written, ISO-8601. Shown to the operator on restore. */
  saved_at: string;
  /** The assertions etag the pending records were staged against, or `null`. */
  etag: string | null;
  /**
   * The bundle's CONTENT fingerprint at the time (`CastSummary.
   * bundle_fingerprint`, ruling R4's change key), or `null` when the bundle
   * publishes none. A different one means the bundle was recompiled under the
   * draft.
   *
   * **Absent on a draft kept before this stamp existed.** Those drafts
   * carried `fingerprint` -- the sidecar's `code_fingerprint`, which is the
   * PIT row's build stamp and does not move on a re-cast, so the recompile
   * check they fed could never fire. Such a draft's stamp is UNKNOWN, and
   * `reconcileDraft` says so rather than reading it as "unchanged"
   * (LES-273/LES-333).
   */
  bundle_fingerprint?: string | null;
  entries: DraftEntry[];
}

export type StorageOutcome = { ok: true } | { ok: false; reason: string };

/** The `Storage` to use, or `null` when this environment has none. Never
 *  throws: on a hardened profile the PROPERTY ACCESS itself throws. */
export function defaultStorage(): Storage | null {
  try {
    if (typeof window === "undefined") return null;
    return window.localStorage;
  } catch {
    return null;
  }
}

/** Why a storage operation failed, in words an operator can act on. */
function why(err: unknown): string {
  if (err instanceof Error && err.name === "QuotaExceededError") {
    return "this browser's storage for this site is full";
  }
  if (err instanceof Error && err.message) return err.message;
  return "this browser refused the write";
}

export interface ReadResult {
  /** The draft on file, or `null` when there is none to restore. */
  draft: Draft | null;
  /** Why there is none, when that is worth saying: a store that cannot be read,
   *  or a draft that had to be discarded. `null` when there simply was none. */
  problem: string | null;
}

/**
 * The draft for `imprintId`, or `null`. A draft that is unreadable, malformed,
 * of another schema, or filed under another imprint is DISCARDED and the reason
 * reported -- never partially believed.
 */
export function readDraft(imprintId: string, storage: Storage | null = defaultStorage()): ReadResult {
  if (storage === null) {
    return { draft: null, problem: "this browser has no storage available, so no draft was kept" };
  }
  let raw: string | null;
  try {
    raw = storage.getItem(draftKey(imprintId));
  } catch (err) {
    return { draft: null, problem: `the draft could not be read (${why(err)})` };
  }
  if (raw === null) return { draft: null, problem: null };
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return { draft: null, problem: "the stored draft was not readable JSON and was discarded" };
  }
  const draft = asDraft(parsed, imprintId);
  if (draft === null) {
    return {
      draft: null,
      problem: `the stored draft was not a ${DRAFT_SCHEMA} draft for this imprint and was discarded`,
    };
  }
  return { draft, problem: null };
}

/** Structural validation, refusing anything this module did not write. */
function asDraft(value: unknown, imprintId: string): Draft | null {
  if (typeof value !== "object" || value === null) return null;
  const d = value as Record<string, unknown>;
  if (d["schema"] !== DRAFT_SCHEMA) return null;
  if (typeof d["imprint"] !== "string" || d["imprint"] !== imprintId) return null;
  if (!Array.isArray(d["entries"])) return null;
  const entries: DraftEntry[] = [];
  for (const raw of d["entries"]) {
    if (typeof raw !== "object" || raw === null) return null;
    const e = raw as Record<string, unknown>;
    const block = e["block"];
    if (block !== "add" && block !== "override" && block !== "suppress") return null;
    const record = e["record"];
    if (typeof record !== "object" || record === null) return null;
    const entry: DraftEntry = { block, record: record as AssertionRecord };
    const gesture = e["gesture"];
    if (gesture && typeof gesture === "object") entry.gesture = gesture as GestureTag;
    entries.push(entry);
  }
  return {
    schema: DRAFT_SCHEMA,
    imprint: d["imprint"],
    saved_at: typeof d["saved_at"] === "string" ? d["saved_at"] : "",
    etag: typeof d["etag"] === "string" ? d["etag"] : null,
    // Absent stays ABSENT (a legacy draft whose stamp is unknown); present
    // but not a string is `null` (a bundle that published none).
    ...("bundle_fingerprint" in d
      ? { bundle_fingerprint: typeof d["bundle_fingerprint"] === "string" ? d["bundle_fingerprint"] : null }
      : {}),
    entries,
  };
}

/** Write the draft. An EMPTY entry list clears the key instead of storing an
 *  empty document: nothing pending means nothing to restore, and a leftover key
 *  would offer an empty restore on the next visit. */
export function writeDraft(draft: Draft, storage: Storage | null = defaultStorage()): StorageOutcome {
  if (storage === null) {
    return { ok: false, reason: "this browser has no storage available" };
  }
  if (draft.entries.length === 0) return clearDraft(draft.imprint, storage);
  try {
    storage.setItem(draftKey(draft.imprint), JSON.stringify(draft));
    return { ok: true };
  } catch (err) {
    return { ok: false, reason: why(err) };
  }
}

export function clearDraft(imprintId: string, storage: Storage | null = defaultStorage()): StorageOutcome {
  if (storage === null) return { ok: false, reason: "this browser has no storage available" };
  try {
    storage.removeItem(draftKey(imprintId));
    return { ok: true };
  } catch (err) {
    return { ok: false, reason: why(err) };
  }
}

/**
 * Every imprint this browser holds a draft for, with its record count and when
 * it was taken (review E).
 *
 * The destination notice needs a workspace to have REPORTED a count, so a cold
 * start -- a new tab, a restored session -- said nothing though drafts were
 * sitting here. The keys are enumerable, so the catalog can say so on its own.
 *
 * Refuses nothing and throws nothing: an unreadable entry is skipped rather
 * than turned into an error, because this is a convenience over the store, not
 * a read anybody is waiting on.
 */
export function listDrafts(storage: Storage | null = defaultStorage()): Array<{
  imprint: string;
  count: number;
  savedAt: string;
}> {
  if (storage === null) return [];
  const out: Array<{ imprint: string; count: number; savedAt: string }> = [];
  try {
    for (let i = 0; i < storage.length; i++) {
      const key = storage.key(i);
      if (key === null || !key.startsWith(DRAFT_KEY_PREFIX)) continue;
      const imprint = key.slice(DRAFT_KEY_PREFIX.length);
      const { draft } = readDraft(imprint, storage);
      if (draft === null || draft.entries.length === 0) continue;
      out.push({ imprint, count: draft.entries.length, savedAt: draft.saved_at });
    }
  } catch {
    return out;
  }
  return out.sort((a, b) => (a.imprint < b.imprint ? -1 : a.imprint > b.imprint ? 1 : 0));
}

/** What the world looks like now, for `reconcileDraft` to compare against. */
export interface DraftWorld {
  /** The assertions etag the file carries now, or `null`. */
  etag: string | null;
  /**
   * The bundle's CONTENT fingerprint now (`CastSummary.bundle_fingerprint`): a
   * string, `null` when the cast genuinely publishes none, or `"unknown"` when
   * the cast could not be read at all.
   *
   * THREE outcomes, not two (review M3, LES-273). The first cut compared only
   * when both sides were non-null, so a cast that resolved AFTER the assertions
   * -- the ordinary order -- skipped the recompile warning silently, and looked
   * exactly like a bundle that publishes no fingerprint.
   */
  fingerprint: string | null | "unknown";
  /** The entry ids the store already holds -- the file's saved/applied records
   *  and anything already staged. A draft record for one of these is dropped. */
  heldIds: ReadonlySet<string>;
}

export interface Reconciled {
  /** The records to stage back, in draft order. */
  restore: BatchRecord[];
  /**
   * Whether the draft is worth KEEPING even though nothing (or not everything)
   * came back (review D).
   *
   * A draft refused because it is corrupt, foreign or of another schema is
   * gone for good and the key goes with it. A draft refused because of the
   * WORLD -- the file gained a conflicting record, or a different record under
   * the key -- describes a situation the operator can resolve, after which they
   * would reasonably expect their copy back. The notice already offers
   * *discard them*, so nothing is stuck.
   */
  keepDraft: boolean;
  /** The gesture tag each restored record came from, by index -- parallel to
   *  `restore`, so the store can re-tag them. */
  gestures: Array<GestureTag | undefined>;
  /** What was dropped and why, named one by one. */
  dropped: Array<{ id: string; why: string }>;
  /** What moved on under the draft, said plainly. `[]` when nothing did. */
  moved: string[];
}

/** The extra machinery `reconcileDraft` borrows from the store and the record
 *  writer, passed in rather than imported so this module stays free of both. */
export interface ReconcileDeps {
  /** The records the store already holds, for a VALUE comparison (S3). */
  heldRecords: ReadonlyArray<{ block: AssertionBlock; record: AssertionRecord }>;
  /** `edit/store.ts`'s canonical-JSON compare. */
  sameRecord: (a: AssertionRecord, b: AssertionRecord) => boolean;
  /** `edit/records.ts`'s cross-record refusals -- `14` §3's own rules (S2).
   *  Optional so a caller with no writer (a pure test of the id rules) can
   *  omit it; when omitted the refusals simply do not run. */
  crossRecordConflict?: (
    existing: ReadonlyArray<{ block: AssertionBlock; record: AssertionRecord }>,
    staged: { block: AssertionBlock; record: AssertionRecord },
  ) => string | null;
  /** `edit/records.ts`'s shape check for a record read from storage (S8). */
  validRecord?: (block: AssertionBlock, record: AssertionRecord) => string | null;
}

/**
 * Reconcile a draft against the world it is being restored into.
 *
 * **The back door runs the FRONT door's refusals** (review S2). A draft is the
 * only way a record reaches the store without passing through a gesture, and a
 * record that no gesture could produce must not arrive through it: a draft
 * `add: host X` restored into a file that has since gained `suppress: X` used
 * to leave both in the store and both in the next PUT -- a combination `14` §3
 * calls a refusal. So every draft record is checked against the world AND
 * against the draft records already accepted, with the rule's own message.
 *
 * Four rules, and none of them is silent:
 *  1. a record whose entry id the store already holds is DROPPED. If the held
 *     record is the SAME record by value it is a duplicate; if it differs, the
 *     file now says something else under that key, and that is the case the
 *     operator has to look at, so the two are worded differently (S3);
 *  2. a record that fails the writer's shape check, or `14` §3's cross-record
 *     refusals, is dropped with the rule's own reason;
 *  3. a changed assertions etag or bundle fingerprint is REPORTED beside the
 *     restored set -- the draft is still the operator's work and is offered, but
 *     it was taken against a file or a bundle that has since moved;
 *  4. everything else returns as `pending`, which commits nothing: the save
 *     path's own etag guard still stands behind it.
 *
 * `entryIdOf` is the store's own id function, passed in rather than imported, so
 * this module stays free of the store's reducer.
 */
export function reconcileDraft(
  draft: Draft,
  world: DraftWorld,
  entryIdOf: (block: AssertionBlock, record: AssertionRecord) => string,
  deps: ReconcileDeps = { heldRecords: [], sameRecord: () => false },
): Reconciled {
  const restore: BatchRecord[] = [];
  const gestures: Array<GestureTag | undefined> = [];
  const dropped: Reconciled["dropped"] = [];
  const seen = new Set<string>();
  // Set by a drop whose cause is the WORLD rather than the draft (review D).
  let worldDependent = false;
  const accepted: Array<{ block: AssertionBlock; record: AssertionRecord }> = [];
  const heldById = new Map(
    deps.heldRecords.map((e) => [entryIdOf(e.block, e.record), e] as const),
  );
  for (const entry of draft.entries) {
    const id = entryIdOf(entry.block, entry.record);
    if (world.heldIds.has(id)) {
      // S3: the file holding the SAME record is a duplicate; the file holding
      // a DIFFERENT one under that key is a divergence the operator must see.
      const held = heldById.get(id);
      const same = held !== undefined && deps.sameRecord(held.record, entry.record);
      // A DIFFERENT record under the key is the file having moved; the same one
      // is a plain duplicate, and nothing the operator can act on.
      if (!same) worldDependent = true;
      dropped.push({
        id,
        why:
          held === undefined || same
            ? "the file already holds this record, so the draft's copy was not restored"
            : "the file now holds a different record under this key, so the draft's copy was not restored",
      });
      continue;
    }
    if (seen.has(id)) {
      dropped.push({ id, why: "the draft named this record twice; it was restored once" });
      continue;
    }
    // S8: a hand-edited draft can carry anything. The writer's own shape check
    // runs before the record is offered to the store, so a nonsense record is
    // named here rather than refused by the server three gestures later.
    const shape = deps.validRecord?.(entry.block, entry.record) ?? null;
    if (shape !== null) {
      dropped.push({ id, why: `${shape} — the draft's copy was not restored` });
      continue;
    }
    // S2: `14` §3's cross-record refusals, against the world AND the records
    // this restore has already accepted.
    const conflict =
      deps.crossRecordConflict?.([...deps.heldRecords, ...accepted], { block: entry.block, record: entry.record }) ??
      null;
    if (conflict !== null) {
      worldDependent = true;
      dropped.push({ id, why: `${conflict} — the draft's copy was not restored` });
      continue;
    }
    seen.add(id);
    accepted.push({ block: entry.block, record: entry.record });
    restore.push({ block: entry.block, record: entry.record });
    gestures.push(entry.gesture);
  }
  const moved: string[] = [];
  if (draft.etag !== null && world.etag !== null && draft.etag !== world.etag) {
    moved.push("the assertions file has been written since this draft was taken");
  }
  const stamp = draft.bundle_fingerprint;
  if (stamp === undefined) {
    // A draft kept before the content stamp existed: what it carried was the
    // PIT build stamp, which no recompile moves, so there is nothing to
    // compare. Saying nothing would read as "unchanged" (LES-273/LES-333).
    moved.push(
      "whether the bundle has been recompiled since this draft was taken could not be checked — the " +
        "draft was kept by an earlier Console that did not record which bundle it was taken against",
    );
  } else if (stamp !== null && world.fingerprint === "unknown") {
    // The check could not be MADE. Saying nothing here reads as "the bundle is
    // unchanged", which is the one thing this does not know (LES-273).
    moved.push(
      "whether the bundle has been recompiled since this draft was taken could not be checked — this " +
        "bundle's summary could not be read",
    );
  } else if (stamp !== null && world.fingerprint !== null && world.fingerprint !== "unknown" && stamp !== world.fingerprint) {
    // The CONTENT moved -- an apply or a re-cast. Not necessarily a different
    // compiler build: that is a separate question the apply answers.
    moved.push("the bundle has been recompiled since this draft was taken (its content has changed)");
  }
  // Kept while there is still something to keep: records that came back, or a
  // refusal the operator can resolve. A draft whose every record was dropped
  // for a reason that will never change is not worth offering again.
  return { restore, gestures, dropped, moved, keepDraft: restore.length > 0 || worldDependent };
}

/** The restore notice, in one sentence -- said once, where the operator lands.
 *  ONE helper, so the visible notice and the accessible announcement cannot
 *  disagree (LES-307). */
export function restoreNotice(draft: Draft, result: Reconciled): string {
  const n = result.restore.length;
  const when = draft.saved_at ? ` from ${draft.saved_at}` : "";
  const head =
    n === 0
      ? `A draft${when} was found, and none of it could be restored`
      : `Restored ${n} unsaved edit${n === 1 ? "" : "s"} from a draft${when} kept on this browser`;
  const parts = [head];
  if (result.dropped.length > 0) {
    parts.push(
      `${result.dropped.length} record${result.dropped.length === 1 ? " was" : "s were"} left out: ` +
        result.dropped.map((d) => `${d.id} — ${d.why}`).join("; "),
    );
  }
  // The `moved` clauses read as sentence fragments on their own (they are
  // written to be listed as well as joined), so they are capitalized HERE
  // rather than in `reconcileDraft`, where a caller may want the clause.
  for (const m of result.moved) parts.push(m.charAt(0).toUpperCase() + m.slice(1));
  return `${parts.join(". ")}.`;
}
