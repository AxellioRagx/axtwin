/**
 * The east-west pairs the tree draws (`canvas/pairs.ts`, 2026-11-08, replacing
 * the lifted `layout/arcs.ts`): every conduit, the busiest observed pairs up to
 * the budget, merged on the boxes they are drawn between, keyed the way the
 * selection and the overlay key them.
 */
import { describe, expect, it } from "vitest";

import { HAIRLINE, MIN_STROKE, PAIR_TOP_K, STROKE_RANGE, selectPairs } from "../src/features/edit-canvas/canvas/pairs";
import { arcKey } from "../src/features/edit-canvas/canvas/placement";
import type { ConsoleGraph } from "../src/features/edit-canvas/model/graph";

function graph(trafficPairs: Array<[string, string, number]>, conduits: Array<[string, string]> = []): ConsoleGraph {
  return {
    trafficPairs: trafficPairs.map(([a, b, bytes]) => ({ a, b, bytes, flows: 1, protoMix: [] })),
    conduits: conduits.map(([a, b]) => ({ a, b })),
  } as unknown as ConsoleGraph;
}

const self = (s: string) => s;

describe("selectPairs", () => {
  it("draws every conduit, observed pairs busiest first up to the budget, and counts the rest", () => {
    const pairs: Array<[string, string, number]> = Array.from({ length: PAIR_TOP_K + 3 }, (_, i) => [`s${i}`, `t${i}`, 1000 + i]);
    const sel = selectPairs(graph(pairs, [["x", "y"]]), self);
    expect(sel.drawn.filter((p) => p.kind === "observed").length).toBe(PAIR_TOP_K);
    expect(sel.drawn[0]!.bytes).toBe(1000 + PAIR_TOP_K + 2);
    expect(sel.hiddenPairs).toBe(3);
    const conduit = sel.drawn.find((p) => p.kind === "asserted")!;
    expect(conduit).toMatchObject({ a: "x", b: "y", hasTraffic: false, strokeWidth: HAIRLINE });
    expect(selectPairs(graph(pairs), self, { showAll: true }).hiddenPairs).toBe(0);
  });
  it("a conduit the capture saw takes its volume and stays asserted", () => {
    const sel = selectPairs(graph([["b", "a", 500]], [["a", "b"]]), self);
    expect(sel.drawn).toHaveLength(1);
    expect(sel.drawn[0]).toMatchObject({ kind: "asserted", bytes: 500, hasTraffic: true });
  });
  it("merges pairs that land on one fold, keys them by the boxes, and counts a pair folded into one box", () => {
    const fold = "overflow:site:gw|10.2";
    const anchor = (s: string) => (s.startsWith("f") ? fold : s);
    const sel = selectPairs(graph([["a", "f1", 100], ["a", "f2", 300], ["f1", "f2", 50]]), anchor);
    expect(sel.drawn).toHaveLength(1);
    expect(sel.drawn[0]).toMatchObject({ key: arcKey("a", fold), bytes: 400, pairs: 2 });
    expect(sel.internalPairs).toBe(1);
  });
  it("skips a pair with an end that is not drawn; widths are log-scaled over the drawn set", () => {
    const sel = selectPairs(graph([["a", "b", 10], ["a", "c", 1_000_000], ["a", "gone", 5]]), (s) => (s === "gone" ? null : s));
    expect(sel.drawn.map((p) => p.b)).toEqual(["c", "b"]);
    expect(sel.drawn[0]!.strokeWidth).toBe(MIN_STROKE + STROKE_RANGE);
    expect(sel.drawn[1]!.strokeWidth).toBe(MIN_STROKE);
  });
});
