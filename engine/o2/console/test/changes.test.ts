/**
 * `model/changes.ts` -- the pure reduction of an apply's diff to the
 * map-highlightable change set (S6). No React, no fetch: the DOM wiring is
 * `apply.ui.test.tsx`; this pins the JOIN rule -- observed CIDRs + observed
 * node ids + authored suppressed keys, and NOT the twin-side ids that name no
 * drawn element.
 */
import { describe, expect, it } from "vitest";

import {
  changeSetFromDiff,
  changeSetSize,
  EMPTY_CHANGE_SET,
  twinHostChurn,
} from "../src/model/changes";
import type { DiffSection, ImprintDiff } from "../src/api/types";

const EMPTY: DiffSection = { added: [], removed: [], changed: [] };

function diff(part: Partial<ImprintDiff>): ImprintDiff {
  return {
    imprint_id: "x",
    since: "fp0",
    current_fingerprint: "fp1",
    snapshot_schema: "1",
    source: "topology export",
    observed: null,
    twin: null,
    suppressed_keys: null,
    twin_config_changed: false,
    warnings: [],
    ...part,
  };
}

describe("changeSetFromDiff", () => {
  it("null (no apply / could-not-compare) is the empty set", () => {
    expect(changeSetFromDiff(null)).toBe(EMPTY_CHANGE_SET);
    expect(changeSetFromDiff(undefined)).toBe(EMPTY_CHANGE_SET);
    expect(changeSetSize(changeSetFromDiff(null))).toBe(0);
  });

  it("collects OBSERVED segment CIDRs (added/removed/changed) into `segments`", () => {
    const set = changeSetFromDiff(
      diff({
        observed: {
          segments: {
            added: ["10.80.20.0/24"],
            removed: ["10.80.72.0/24"],
            changed: [{ key: "10.20.193.0/24", fields: {} }],
          },
          nodes: EMPTY,
        },
      }),
    );
    expect([...set.segments].sort()).toEqual([
      "10.20.193.0/24",
      "10.80.20.0/24",
      "10.80.72.0/24",
    ]);
    expect(set.nodes.size).toBe(0);
  });

  it("collects OBSERVED node ids AND authored suppressed keys into `nodes`", () => {
    const set = changeSetFromDiff(
      diff({
        observed: {
          nodes: { added: ["hostname:axdc01"], removed: [], changed: [] },
          segments: EMPTY,
        },
        // A suppress names the host by the SAME string the map draws it under.
        suppressed_keys: { added: ["address:10.80.20.5"], removed: [], changed: [] },
      }),
    );
    expect([...set.nodes].sort()).toEqual(["address:10.80.20.5", "hostname:axdc01"]);
    expect(set.segments.size).toBe(0);
  });

  it("does NOT fold in the TWIN side -- its minted ids name no drawn element", () => {
    // A twin-only change (an add-host lands here, keyed by the cast host id) is
    // real, but not highlightable on the observed map -- so it must not appear
    // in the ChangeSet (it would add a key that can never match an element).
    const set = changeSetFromDiff(
      diff({
        twin: {
          hosts: { added: ["te-abc-newhost"], removed: [], changed: [] },
          segments: { added: ["10.200.99.0/24"], removed: [], changed: [] },
          conduits: EMPTY,
        },
      }),
    );
    expect(changeSetSize(set)).toBe(0);
  });

});

describe("twinHostChurn (GAP-087)", () => {
  it("counts the twin's host adds and removals -- the number the map cannot show", () => {
    // The measured case: one suppression, and a compiler that had renamed 11
    // node groups since the cast. `changeSetFromDiff` reports 1 (correctly,
    // for what it joins on); this reports the population move behind it.
    const d = diff({
      twin: {
        hosts: {
          added: Array.from({ length: 22 }, (_, i) => `te-new-${i}`),
          removed: Array.from({ length: 23 }, (_, i) => `te-old-${i}`),
          changed: [],
        },
        segments: EMPTY,
        conduits: EMPTY,
      },
      suppressed_keys: { added: ["address:10.80.20.5"], removed: [], changed: [] },
    });
    expect(twinHostChurn(d)).toEqual({ added: 22, removed: 23 });
    expect(changeSetSize(changeSetFromDiff(d))).toBe(1);
  });

  it("a NOT-COMPARED twin side is null, not zero -- `null` is not empty (LES-240)", () => {
    expect(twinHostChurn(diff({ twin: null }))).toBeNull();
    expect(twinHostChurn(null)).toBeNull();
    expect(twinHostChurn(undefined)).toBeNull();
    // ... and a side that WAS compared and found nothing is a real zero.
    expect(
      twinHostChurn(diff({ twin: { hosts: EMPTY, segments: EMPTY, conduits: EMPTY } })),
    ).toEqual({ added: 0, removed: 0 });
  });
});
