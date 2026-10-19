/**
 * The change list counted by GESTURE (`docs/WORK_ORDER_console_canvas.md` §3
 * S5.1.d): one entry per gesture -- `clone of 10.80.20.0/24 → 10.80.31.0/24 ·
 * 1 segment · 24 hosts · 3 conduits · pending` -- that expands to its records.
 *
 * S5.1.b/c tagged every record a batch staged with the gesture it came from
 * (`edit/store.ts`'s `GestureTag`); the list stayed one row per record. This is
 * the grouping that tag was for.
 *
 * **A solitary record is not a group.** Most gestures are one record -- a role
 * override, a suppression, a single added host -- and wrapping each of them in a
 * header with an expander would make the list harder to read, not easier. A
 * record with no gesture tag, and a gesture that staged exactly one record, both
 * render as one plain row (`GestureGroup.solo`).
 *
 * **A group's state is the WEAKEST of its records', not the first's.** A clone
 * saved and then partly re-staged holds saved and pending records at once, and
 * calling that group "saved" would tell the operator their unsaved work is on
 * disk. Pending wins, then saved, then applied.
 *
 * Pure -- no React, no store object.
 */
import type { EditEntry, EditState, GestureTag } from "./store";

/**
 * A gesture id unique to this BROWSING SESSION, not to a mount (review M2).
 *
 * The first cut minted `gesture-1`, `gesture-2`, ... from a `useRef(0)` that
 * resets whenever the workspace remounts -- and the draft (`edit/draft.ts`)
 * persists each record's whole `GestureTag`. So a clone staged, reloaded and
 * then followed by a second clone produced TWO batches under one id: one
 * change-list row labelled with the first, counting both, whose single undo
 * removed all 47 records and both pins. The id also keys `groupByGesture`, the
 * expander state and `data-gesture`, so a collision is wrong in four places.
 *
 * `crypto.randomUUID()` where the platform has it (every browser this app
 * supports, and node 19+), with a random-prefixed counter as the fallback --
 * never a bare counter, because a bare counter is exactly the bug.
 */
export function newGestureId(): string {
  const c = globalThis.crypto;
  if (c && typeof c.randomUUID === "function") return `g-${c.randomUUID()}`;
  sequence += 1;
  return `g-${prefix}-${sequence}`;
}

const prefix = Math.random().toString(36).slice(2, 10);
let sequence = 0;

export interface GestureCounts {
  segments: number;
  hosts: number;
  conduits: number;
  overrides: number;
  suppressions: number;
}

export interface GestureGroup {
  /** A stable key for the row: the gesture id, or the lone entry's id. */
  key: string;
  /** The gesture these records came from, or `null` for an untagged record. */
  gesture: GestureTag | null;
  entries: EditEntry[];
  counts: GestureCounts;
  /** The weakest state among the records (see the module note). */
  state: EditState;
  /** Render as ONE plain row: an untagged record, or a gesture of one. */
  solo: boolean;
}

/** Pending is weaker than saved, which is weaker than applied. */
const RANK: Record<EditState, number> = { pending: 0, saved: 1, applied: 2 };

/**
 * The entries grouped by gesture, in store order.
 *
 * A gesture's group sits at the position of its FIRST record, so the list keeps
 * the order things were staged in and a batch does not jump to the bottom when
 * one of its records is re-staged.
 */
export function groupByGesture(entries: readonly EditEntry[]): GestureGroup[] {
  const groups: GestureGroup[] = [];
  const byGesture = new Map<string, GestureGroup>();
  for (const entry of entries) {
    const id = entry.gesture?.id;
    if (id === undefined) {
      groups.push(soloGroup(entry));
      continue;
    }
    const existing = byGesture.get(id);
    if (existing) {
      existing.entries.push(entry);
      continue;
    }
    const group: GestureGroup = {
      key: id,
      gesture: entry.gesture ?? null,
      entries: [entry],
      counts: emptyCounts(),
      state: "applied",
      solo: false,
    };
    byGesture.set(id, group);
    groups.push(group);
  }
  for (const group of groups) {
    group.counts = countOf(group.entries);
    group.state = weakestState(group.entries);
    // A gesture that staged exactly one record is a row, not a group.
    if (group.entries.length === 1) group.solo = true;
  }
  return groups;
}

function soloGroup(entry: EditEntry): GestureGroup {
  return {
    key: entry.id,
    gesture: entry.gesture ?? null,
    entries: [entry],
    counts: countOf([entry]),
    state: entry.state,
    solo: true,
  };
}

function emptyCounts(): GestureCounts {
  return { segments: 0, hosts: 0, conduits: 0, overrides: 0, suppressions: 0 };
}

function countOf(entries: readonly EditEntry[]): GestureCounts {
  const counts = emptyCounts();
  for (const e of entries) {
    if (e.block === "override") counts.overrides += 1;
    else if (e.block === "suppress") counts.suppressions += 1;
    else if (e.record.kind === "segment") counts.segments += 1;
    else if (e.record.kind === "conduit") counts.conduits += 1;
    else counts.hosts += 1;
  }
  return counts;
}

function weakestState(entries: readonly EditEntry[]): EditState {
  let state: EditState = "applied";
  for (const e of entries) if (RANK[e.state] < RANK[state]) state = e.state;
  return state;
}

/**
 * The counts in the work order's own shape -- `1 segment · 24 hosts ·
 * 3 conduits` -- naming only what the gesture actually staged, so a clone with
 * no mirrored conduits does not read "0 conduits".
 */
export function gestureCounts(counts: GestureCounts): string {
  const parts: string[] = [];
  const add = (n: number, one: string, many: string): void => {
    if (n > 0) parts.push(`${n} ${n === 1 ? one : many}`);
  };
  add(counts.segments, "segment", "segments");
  add(counts.hosts, "host", "hosts");
  add(counts.conduits, "conduit", "conduits");
  add(counts.overrides, "override", "overrides");
  add(counts.suppressions, "suppression", "suppressions");
  return parts.join(" · ");
}

/** The whole header line: label · counts · state. */
export function gestureHeadline(group: GestureGroup): string {
  const label = group.gesture?.label ?? "";
  return [label, gestureCounts(group.counts), group.state].filter((p) => p !== "").join(" · ");
}
