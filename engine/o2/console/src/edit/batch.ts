/**
 * The BATCH path (`docs/WORK_ORDER_console_canvas.md` §3 S5.1.b/c): one gesture,
 * many records -- a clone is `add: segment` + N × `add: host` (+ M × `add:
 * conduit`), a fresh segment with generated hosts is `add: segment` + N × `add:
 * host`. `docs/design/15-console-canvas.md` §8 refines principle 5 to exactly
 * this: **a gesture may emit a batch of records of the ruled kinds, never a new
 * kind**, so every record still comes out of `edit/records.ts`'s `buildRecord`.
 *
 * **All or nothing.** A batch is checked whole before a single record is
 * staged, and the first record that would be refused refuses the batch, with
 * the reason naming that record. Three checks, each one the single-record path
 * already applies, in the order a record meets them:
 *
 *  1. `buildRecord` -- the writer's closed sets and shapes, mirrored from the
 *     server (LES-271: never stricter than it);
 *  2. the store's own identity rule -- an entry id (`<block>:<key>`) the store
 *     already holds, or one the batch names twice. `stage` REPLACES such an
 *     entry (S5's re-stage rule, said aloud by the add forms), which a batch of
 *     sixteen records must never do silently: one of them overwriting an
 *     operator's saved host is not a gesture anyone meant;
 *  3. `crossRecordConflict` -- `14` §3's two cross-record refusals, checked
 *     against what is staged AND the batch records before it, so a batch cannot
 *     conflict with itself either.
 *
 * The reducer's `stageBatch` repeats rule 2 (`edit/store.ts`), so a batch that
 * reached it anyway is still refused whole -- the store's atomicity does not
 * rest on every caller having asked this module first.
 *
 * Pure -- no React, no fetch, no store object.
 */
import type { AssertionBlock, AssertionRecord } from "../api/types";
import { buildRecord, crossRecordConflict, type Gesture, type StagedRecord } from "./records";
import { entryId } from "./store";

/** An entry the batch is checked against, read structurally. */
export interface BatchExistingEntry {
  block: AssertionBlock;
  state: string;
  record: AssertionRecord;
}

export type BatchResult =
  | { ok: true; staged: StagedRecord[] }
  | {
      ok: false;
      /** The whole refusal, naming the offending record. */
      error: string;
      /** The offending record's position in the batch (0-based). */
      index: number;
    };

/**
 * Check a batch of gestures whole and return the records to stage, or the one
 * refusal that stops all of them.
 */
export function buildBatch(gestures: readonly Gesture[], existing: readonly BatchExistingEntry[]): BatchResult {
  if (gestures.length === 0) {
    return { ok: false, error: "the gesture produced no record — nothing was staged", index: 0 };
  }
  const held = new Map(existing.map((e) => [entryId(e.block, e.record), e]));
  const accepted: StagedRecord[] = [];
  const acceptedIds = new Set<string>();
  for (let i = 0; i < gestures.length; i++) {
    const g = gestures[i]!;
    const where = `record ${i + 1} of ${gestures.length} (${describeGesture(g)})`;
    const refuse = (why: string): BatchResult => ({
      ok: false,
      error: `${where}: ${why} — the batch is refused whole, and nothing was staged`,
      index: i,
    });

    const built = buildRecord(g);
    if (!built.ok) return refuse(built.error);
    const id = entryId(built.staged.block, built.staged.record);
    const already = held.get(id);
    if (already) {
      return refuse(
        `${built.staged.record.key ?? id} is already ${storeWords(already.state)}, and a batch never ` +
          "replaces a record",
      );
    }
    if (acceptedIds.has(id)) return refuse(`the batch names ${built.staged.record.key ?? id} twice`);
    const conflict = crossRecordConflict([...existing, ...accepted], built.staged);
    if (conflict) return refuse(conflict);
    accepted.push(built.staged);
    acceptedIds.add(id);
  }
  return { ok: true, staged: accepted };
}

/** A gesture in a few words -- enough to find the record a refusal names. */
export function describeGesture(g: Gesture): string {
  switch (g.kind) {
    case "add-segment":
      return `add segment ${g.cidr.trim()}`;
    case "add-host":
      return `add host ${g.key.trim()}`;
    case "add-conduit":
      return `add conduit ${g.cidrA.trim()} | ${g.cidrB.trim()}`;
    case "change-role":
      return `change role of ${g.key.trim()}`;
    case "suppress-host":
      return `suppress ${g.key.trim()}`;
    case "override-image":
      return `override image of ${g.key.trim()}`;
    case "override-resources":
      return `override sizing of ${g.key.trim()}`;
  }
}

/** A store state in words (the store's own semantics, `edit/store.ts`). */
function storeWords(state: string): string {
  switch (state) {
    case "pending":
      return "claimed by a pending record (staged in this browser, not saved yet)";
    case "saved":
      return "claimed by a saved record (written to the assertions file)";
    case "applied":
      return "claimed by an applied record (the compiler reports it in the cast)";
    default:
      return "claimed by a record in this file";
  }
}
