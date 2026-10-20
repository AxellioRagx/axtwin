// @vitest-environment node
/**
 * S5.1.d acceptance 3 -- the change list counted by GESTURE
 * (`src/edit/gestures.ts`). Pure: entries in, groups out.
 *
 * The rendered list, the expander and the whole-gesture undo are
 * `s51d.ui.test.tsx`.
 */
import { describe, expect, it } from "vitest";

import { gestureCounts, gestureHeadline, groupByGesture } from "../src/edit/gestures";
import type { EditEntry, EditState, GestureTag } from "../src/edit/store";

const CLONE: GestureTag = { id: "g1", label: "clone of 10.80.20.0/24 → 10.80.31.0/24", kind: "clone", source: "10.80.20.0/24" };
const GENERATE: GestureTag = { id: "g2", label: "add segment 10.80.9.0/24 with 3 hosts", kind: "generate" };

function entry(
  id: string,
  block: EditEntry["block"],
  record: EditEntry["record"],
  state: EditState = "pending",
  gesture?: GestureTag,
): EditEntry {
  return { id, block, state, record, ...(gesture ? { gesture } : {}) };
}

function clone(hosts: number, conduits: number, state: EditState = "pending"): EditEntry[] {
  const out = [entry("add:10.80.31.0/24", "add", { kind: "segment", key: "10.80.31.0/24", note: "n" }, state, CLONE)];
  for (let i = 0; i < hosts; i++) {
    out.push(entry(`add:address:10.80.31.${10 + i}`, "add", { kind: "host", key: `address:10.80.31.${10 + i}`, segment: "10.80.31.0/24", note: "n" }, state, CLONE));
  }
  for (let i = 0; i < conduits; i++) {
    out.push(entry(`add:10.80.31.0/24|10.80.${i}.0/24`, "add", { kind: "conduit", key: `10.80.31.0/24|10.80.${i}.0/24`, note: "n" }, state, CLONE));
  }
  return out;
}

describe("groupByGesture -- one entry per gesture, in store order", () => {
  it("a clone is ONE group carrying its records, in the work order's own shape", () => {
    const groups = groupByGesture(clone(24, 3));
    expect(groups).toHaveLength(1);
    const g = groups[0]!;
    expect(g.solo).toBe(false);
    expect(g.entries).toHaveLength(28);
    expect(g.counts).toEqual({ segments: 1, hosts: 24, conduits: 3, overrides: 0, suppressions: 0 });
    // The headline the work order writes out verbatim.
    expect(gestureHeadline(g)).toBe(
      "clone of 10.80.20.0/24 → 10.80.31.0/24 · 1 segment · 24 hosts · 3 conduits · pending",
    );
  });

  it("names only what the gesture staged -- a clone with no conduits does not read '0 conduits'", () => {
    expect(gestureCounts({ segments: 1, hosts: 17, conduits: 0, overrides: 0, suppressions: 0 })).toBe(
      "1 segment · 17 hosts",
    );
    expect(gestureCounts({ segments: 0, hosts: 1, conduits: 0, overrides: 0, suppressions: 0 })).toBe("1 host");
    expect(gestureCounts({ segments: 0, hosts: 0, conduits: 0, overrides: 2, suppressions: 1 })).toBe(
      "2 overrides · 1 suppression",
    );
  });

  it("a record with NO gesture is one plain row, never wrapped in ceremony", () => {
    const groups = groupByGesture([
      entry("override:address:1:role", "override", { key: "address:1", field: "role", value: "x", note: "n" }),
      entry("suppress:address:2", "suppress", { key: "address:2", note: "n" }),
    ]);
    expect(groups).toHaveLength(2);
    for (const g of groups) {
      expect(g.solo).toBe(true);
      expect(g.gesture).toBeNull();
      expect(g.entries).toHaveLength(1);
    }
  });

  it("a gesture that staged exactly ONE record is a row too", () => {
    // A batch of one is still a batch -- but a header with an expander over a
    // single child is noise, not structure.
    const groups = groupByGesture([
      entry("add:10.80.9.0/24", "add", { kind: "segment", key: "10.80.9.0/24", note: "n" }, "pending", GENERATE),
    ]);
    expect(groups[0]!.solo).toBe(true);
    expect(groups[0]!.gesture).toBe(GENERATE);
  });

  it("keeps store order, and a gesture sits where its FIRST record is", () => {
    const entries = [
      entry("suppress:a", "suppress", { key: "a", note: "n" }),
      ...clone(1, 0),
      entry("suppress:z", "suppress", { key: "z", note: "n" }),
    ];
    expect(groupByGesture(entries).map((g) => g.key)).toEqual(["suppress:a", "g1", "suppress:z"]);
  });

  it("interleaved gestures each keep their own records", () => {
    const entries = [
      entry("add:s1", "add", { kind: "segment", key: "s1", note: "n" }, "pending", CLONE),
      entry("add:s2", "add", { kind: "segment", key: "s2", note: "n" }, "pending", GENERATE),
      entry("add:h1", "add", { kind: "host", key: "h1", segment: "s1", note: "n" }, "pending", CLONE),
    ];
    const groups = groupByGesture(entries);
    expect(groups.map((g) => g.key)).toEqual(["g1", "g2"]);
    expect(groups[0]!.entries.map((e) => e.id)).toEqual(["add:s1", "add:h1"]);
    expect(groups[1]!.entries.map((e) => e.id)).toEqual(["add:s2"]);
  });
});

describe("a group's state is the WEAKEST of its records'", () => {
  it("pending wins over saved, and saved over applied", () => {
    expect(groupByGesture(clone(2, 0, "saved"))[0]!.state).toBe("saved");
    expect(groupByGesture(clone(2, 0, "applied"))[0]!.state).toBe("applied");
    // A saved clone with one record re-staged is NOT "saved": saying so would
    // tell the operator their unsaved work is on disk.
    const mixed = clone(2, 0, "saved");
    mixed[1] = { ...mixed[1]!, state: "pending" };
    expect(groupByGesture(mixed)[0]!.state).toBe("pending");
    const appliedAndSaved = clone(2, 0, "applied");
    appliedAndSaved[1] = { ...appliedAndSaved[1]!, state: "saved" };
    expect(groupByGesture(appliedAndSaved)[0]!.state).toBe("saved");
  });
});
