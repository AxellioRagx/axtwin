/**
 * SPAN sessions on native switches (`docs/design/native-switches.md` §6, phase 2): the tap plan's
 * `span` binding read and written as line splices, its chip riding the switch it mirrors, and the
 * inspector's port editor's commands (add on the first direction, rewrite the sources, remove with
 * the last).
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import { placeTree } from "../src/features/edit-canvas/canvas/tree";
import { addSpan, documentGraph, readDoc, setSpanSourcesOf, type WorkingDoc } from "../src/features/edit-canvas/edit/document";
import { addSpanBinding, readTap, setSpanSources, spanOn } from "../src/features/edit-canvas/edit/tap";
import { buildGraph } from "../src/features/edit-canvas/model/graph";

const CATALOG = resolve(__dirname, "../../catalog/twins");
const read = (id: string, f: string) => readFileSync(resolve(CATALOG, id, f), "utf8");

describe("span bindings in the tap plan", () => {
  it("reads switch-test's SPAN session on access-sw", () => {
    const tap = readTap(read("switch-test", "tap/default.yaml"));
    expect(tap.problems).toEqual([]);
    const b = spanOn(tap, "access-sw");
    expect(b).not.toBeNull();
    expect(b!.scope).toBe("span");
    expect(b!.targets).toEqual(["access-sw"]);
    expect(b!.sources).toEqual([{ port: "SFP1", dir: "both" }]);
    expect(b!.sensors).toEqual(["XPress-SW-A"]);
  });

  it("adds a session and rewrites its sources without touching other lines", () => {
    const base = "# keep me\ntap-plan:\n  target: t\n  bindings:\n    - selector: { scope: segment, target: office }\n      sensors: [s1]\n      mode: exclusive\n";
    const added = addSpanBinding(base, { switch: "sw", sources: [{ port: "P1", dir: "rx" }], sensors: ["s1"] });
    expect(added.startsWith(base)).toBe(true);
    const t1 = readTap(added);
    expect(t1.problems).toEqual([]);
    expect(t1.bindings.map((b) => b.scope)).toEqual(["segment", "span"]);
    const edited = setSpanSources(added, 1, [{ port: "P1", dir: "tx" }, { port: "Po1", dir: "both", vlans: ["office"] }]);
    const t2 = readTap(edited);
    expect(t2.bindings[1]!.sources).toEqual([{ port: "P1", dir: "tx" }, { port: "Po1", dir: "both", vlans: ["office"] }]);
    expect(t2.bindings[1]!.sensors).toEqual(["s1"]);
    expect(edited.split("\n").slice(0, 7).join("\n")).toBe(base.trimEnd());
    expect(() => setSpanSources(added, 0, [{ port: "P1", dir: "rx" }])).toThrow(/not a SPAN session/);
  });
});

describe("the span commands", () => {
  const doc = (): WorkingDoc => ({
    id: "switch-test",
    teFile: "te.yaml",
    files: { "te.yaml": read("switch-test", "te.yaml"), "twin.yaml": read("switch-test", "twin.yaml") },
  });

  it("the first direction creates the tap plan with the session; the last cleared removes it", () => {
    const d0 = doc();
    const files = addSpan(d0, readDoc(d0), { switch: "access-sw", sources: [{ port: "P1", dir: "rx" }], sensors: ["XPress-SW-A"], source: "authored" });
    const d1 = { ...d0, files };
    const m1 = readDoc(d1);
    expect(m1.tapPath).not.toBeNull();
    const b = spanOn(m1.tap, "access-sw")!;
    expect(b.sources).toEqual([{ port: "P1", dir: "rx" }]);
    const d2 = { ...d1, files: setSpanSourcesOf(d1, m1, b.index, []) };
    expect(readDoc(d2).tap?.bindings ?? []).toEqual([]);
  });

  it("refuses a session on something that is not a switch", () => {
    const d0 = doc();
    expect(() => addSpan(d0, readDoc(d0), { switch: "gw", sources: [{ port: "eth1", dir: "both" }], sensors: ["s"] })).toThrow(/no switch 'gw'/);
  });
});

describe("the span chip", () => {
  it("rides the switch it mirrors", () => {
    const d: WorkingDoc = { id: "switch-test", teFile: "te.yaml", files: { "te.yaml": read("switch-test", "te.yaml") } };
    const g = documentGraph(readDoc(d), d.id);
    const placed = placeTree(buildGraph(g.exportData, g.cast), {}, {
      extraLegs: g.extraLegs, uplinks: g.uplinks, tree: g.tree,
      taps: [{ index: 0, scope: "span", targets: ["access-sw"], sensors: ["XPress-SW-A"], source: "authored" }],
    });
    const chip = placed.nodes.find((n) => n.type === "tap");
    expect(chip).toBeDefined();
    const parent = placed.nodes.find((n) => n.id === chip!.parentId);
    expect(parent?.id).toMatch(/access-sw$/);
    expect((chip!.data as { scope: string }).scope).toBe("span");
  });
});
