/**
 * The device tree's geometry (review round 3, R3-4 / R3-5, brief "Canvas
 * rendering", 2026-11-07): over the layout `placeTree` computes for the real
 * catalog twins and for shaped fixtures, every structural edge is an
 * orthogonal polyline that never crosses a box's interior (a box: a device, a
 * pill, a fold, the Internet, a host chip, a tap chip), the tree reads top to
 * bottom by rank, a segment only routers are on is a router-to-router link,
 * and hues appear only with two or more routers.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import { routeOrthogonal, segmentCrossesBox, type Box } from "../src/features/edit-canvas/canvas/orthogonal";
import { ID } from "../src/features/edit-canvas/canvas/placement";
import { HUE_NEUTRAL, TREE_ID, absoluteBoxes, placeTree, type TreeOptions } from "../src/features/edit-canvas/canvas/tree";
import { hueOf } from "../src/features/edit-canvas/canvas/hues";
import { documentGraph, readDoc } from "../src/features/edit-canvas/edit/document";
import { buildGraph } from "../src/features/edit-canvas/model/graph";

const CATALOG = resolve(__dirname, "../../catalog/twins");

function placed(id: string, te: string, options: TreeOptions = {}) {
  const g = documentGraph(readDoc({ id, teFile: "te.yaml", files: { "te.yaml": te } }), id);
  const graph = buildGraph(g.exportData, g.cast);
  return placeTree(graph, {}, { extraLegs: g.extraLegs, uplinks: g.uplinks, tree: g.tree, ...options });
}

/** Every crossing of a structural edge through a box that is neither of its ends nor an end's parent. */
function crossings(p: ReturnType<typeof placed>): string[] {
  const boxes = absoluteBoxes(p.nodes);
  const parent = new Map(p.nodes.filter((n) => n.parentId).map((n) => [n.id, n.parentId!]));
  const out: string[] = [];
  for (const e of p.edges) {
    if (e.type === "arc") continue;
    const pts = e.data?.route?.points;
    if (!pts) continue;
    const own = new Set([e.source, e.target, parent.get(e.source), parent.get(e.target)].filter(Boolean) as string[]);
    for (let i = 1; i < pts.length; i++) {
      const a = pts[i - 1]!;
      const b = pts[i]!;
      expect(Math.abs(a.x - b.x) < 0.01 || Math.abs(a.y - b.y) < 0.01, `${e.id} segment ${i} is not axis-aligned`).toBe(true);
      for (const [id, box] of boxes) {
        if (own.has(id) || parent.get(id) === e.source || parent.get(id) === e.target) continue;
        if (segmentCrossesBox(a, b, box)) out.push(`${e.id} crosses ${id}`);
      }
    }
  }
  return out;
}

/** The structural edges that kept no route (drawn as a smoothstep). */
function unrouted(p: ReturnType<typeof placed>): string[] {
  return p.edges.filter((e) => e.type !== "arc" && !e.data?.route).map((e) => e.id);
}

const TWINS = ["canvas-test", "datacenter", "switch-test", "corp-enterprise", "corp-twin"];

describe("the orthogonal router", () => {
  it("leaves down, runs in the gutter, enters from above; a long edge descends through its column", () => {
    const bands = [{ top: 0, bottom: 40 }, { top: 100, bottom: 140 }, { top: 200, bottom: 240 }];
    const src: Box = { x: 0, y: 0, w: 40, h: 40 };
    const dst: Box = { x: 200, y: 200, w: 40, h: 40 };
    const r = routeOrthogonal(bands, [{ id: "e", src, dst, srcRank: 0, dstRank: 2, via: [120], lane: "s" }]).get("e")!;
    expect(r[0]).toEqual({ x: 20, y: 40 });
    expect(r[r.length - 1]).toEqual({ x: 220, y: 200 });
    // The horizontal runs sit strictly inside the gutters; the descent through rank 1 is at x = 120.
    for (const p of r.slice(1, -1)) expect((p.y > 40 && p.y < 100) || (p.y > 140 && p.y < 200)).toBe(true);
    expect(r.some((p) => p.x === 120)).toBe(true);
  });
  it("gives two parents' horizontals in one gutter their own lanes", () => {
    const bands = [{ top: 0, bottom: 40 }, { top: 100, bottom: 140 }];
    const r = routeOrthogonal(bands, [
      { id: "a", src: { x: 0, y: 0, w: 40, h: 40 }, dst: { x: 300, y: 100, w: 40, h: 40 }, srcRank: 0, dstRank: 1, via: [], lane: "p" },
      { id: "b", src: { x: 400, y: 0, w: 40, h: 40 }, dst: { x: 100, y: 100, w: 40, h: 40 }, srcRank: 0, dstRank: 1, via: [], lane: "q" },
    ]);
    expect(r.get("a")![1]!.y).not.toBe(r.get("b")![1]!.y);
  });
});

describe("no edge crosses a box, on the catalog's twins", () => {
  for (const id of TWINS) {
    it(`${id}: every structural edge is routed and none crosses a box, folded and fully open`, () => {
      const te = readFileSync(resolve(CATALOG, id, "te.yaml"), "utf8");
      const folded = placed(id, te);
      expect(folded.overBudget).toBeNull();
      expect(crossings(folded)).toEqual([]);
      expect(unrouted(folded)).toEqual([]);
      const open = placed(id, te, { fan: 1000 });
      expect(crossings(open)).toEqual([]);
    });
  }
});

const MULTI = `twin_config:
  id: multi
  segments:
    - { name: lan-a, cidr: 10.1.1.0/24 }
    - { name: lan-b, cidr: 10.2.1.0/24 }
    - { name: lan-c, cidr: 10.1.2.0/24 }
    - { name: fab-link, cidr: 10.9.0.0/30 }
  nodes:
    - { name: ws-c, role: workstation, image: img@v1, net: lan-c }
    - { name: ws-a, role: workstation, image: img@v1, net: lan-a }
    - { name: ws-b, role: workstation, image: img@v1, net: lan-b }
    - name: edge
      kind: router
      image: vyos@v1
      nets: [{ segment: lan-a }, { segment: lan-c }, { segment: fab-link }]
    - name: branch
      kind: router
      image: vyos@v1
      nets: [{ segment: lan-b }, { segment: fab-link }]
`;

describe("routers", () => {
  it("a segment only routers are on is a router-to-router link labelled with it; the edge router (most segments, no uplink named) ranks first", () => {
    const p = placed("multi", MULTI);
    expect(p.nodes.some((n) => n.id === ID.segment("fab-link"))).toBe(false);
    const link = p.edges.find((e) => e.data?.kind === "seglink")!;
    expect(link.data).toMatchObject({ kind: "seglink", segment: "fab-link", a: "edge", b: "branch" });
    expect([link.source, link.target]).toEqual([ID.router("edge"), ID.router("branch")]);
    const y = (id: string) => p.nodes.find((n) => n.id === id)!.position.y;
    expect(y(ID.router("edge"))).toBeLessThan(y(ID.router("branch")));
    expect(crossings(p)).toEqual([]);
    expect(unrouted(p)).toEqual([]);
  });
  it("hues only with two or more routers (R3-5)", () => {
    const two = placed("multi", MULTI);
    const hue = (p: ReturnType<typeof placed>, id: string) => (p.nodes.find((n) => n.id === id)!.data as { hue: string }).hue;
    expect(hue(two, ID.router("edge"))).toBe(hueOf(0));
    expect(hue(two, ID.router("branch"))).toBe(hueOf(1));
    const one = placed("ct", readFileSync(resolve(CATALOG, "canvas-test", "te.yaml"), "utf8"));
    const routers = one.nodes.filter((n) => n.type === "router");
    expect(routers.length).toBe(1);
    expect(hue(one, routers[0]!.id)).toBe(HUE_NEUTRAL);
    expect(one.nodes.filter((n) => n.type === "segment").every((n) => (n.data as { hue: string }).hue === HUE_NEUTRAL)).toBe(true);
  });
});

describe("folding", () => {
  const many = (n: number, per: number) => {
    const segs: string[] = [];
    const nodes: string[] = [];
    for (let i = 0; i < n; i++) {
      segs.push(`    - { name: s${i}, cidr: 10.${10 + Math.floor(i / per)}.${i % per}.0/24 }`);
      for (let h = 0; h < 12; h++) nodes.push(`    - { name: h${i}-${h}, role: workstation, image: img@v1, net: s${i} }`);
    }
    return `twin_config:\n  id: many\n  segments:\n${segs.join("\n")}\n  nodes:\n${nodes.join("\n")}\n    - name: gw\n      kind: router\n      image: vyos@v1\n      nets: [${Array.from({ length: n }, (_, i) => `{ segment: s${i} }`).join(", ")}]\n`;
  };
  it("a pill draws at most eight host icons and a `+N`; its host key opens it", () => {
    const p = placed("many", many(1, 1));
    const pill = p.nodes.find((n) => n.id === ID.segment("s0"))!;
    expect(p.nodes.filter((n) => n.parentId === pill.id && n.type === "host").length).toBe(8);
    expect((pill.data as { hiddenChips: number }).hiddenChips).toBe(4);
    const open = placed("many", many(1, 1), { expanded: ["hosts:s0"] });
    expect(open.nodes.filter((n) => n.parentId === ID.segment("s0") && n.type === "host").length).toBe(12);
  });
  it("segments of one site under one carrier fold into one site pill; a click opens it in place", () => {
    const p = placed("many", many(9, 3)); // three sites of three segments under gw
    const folds = p.nodes.filter((n) => n.type === "overflow");
    expect(folds.map((n) => (n.data as { label?: string }).label)).toEqual(["10.10 · 3 segments", "10.11 · 3 segments", "10.12 · 3 segments"]);
    expect(p.nodes.filter((n) => n.type === "segment")).toEqual([]);
    const open = placed("many", many(9, 3), { expanded: ["site:gw|10.11"] });
    expect(open.nodes.filter((n) => n.type === "segment").map((n) => n.id)).toEqual([ID.segment("s3"), ID.segment("s4"), ID.segment("s5")]);
    expect(open.nodes.some((n) => n.id === TREE_ID.siteFold("gw", "10.11"))).toBe(false);
    expect(crossings(open)).toEqual([]);
  });
});
