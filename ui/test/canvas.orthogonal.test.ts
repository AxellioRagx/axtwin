/**
 * The device tree's geometry (review round 3, R3-4 / R3-5, brief "Canvas
 * rendering", 2026-11-07): over the layout `placeTree` computes for the real
 * catalog twins and for shaped fixtures, every structural edge is an
 * orthogonal polyline that never crosses a box's interior (a box: a device, an
 * implied switch, a fold, the Internet, a host, a tap chip), the tree reads top to
 * bottom by rank, a segment only routers are on is a router-to-router link,
 * and hues appear only with two or more routers.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import { dropPoint, routeOrthogonal, segmentCrossesBox, type Box, type Point } from "../src/features/edit-canvas/canvas/orthogonal";
import { ID } from "../src/features/edit-canvas/canvas/placement";
import { HUE_NEUTRAL, TREE_HOST_BUDGET, TREE_ID, absoluteBoxes, hostsKey, laneOf, placeTree, type TreeOptions } from "../src/features/edit-canvas/canvas/tree";
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

const TWINS = ["canvas-test", "datacenter", "switch-test", "corp-enterprise", "corp-twin", "pit-395a61f04e-1780405200"];

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
  it("corp-twin with 10.180 open and every host fold open: routed, uncrossed, no node id twice (two segments on one CIDR)", () => {
    const te = readFileSync(resolve(CATALOG, "corp-twin", "te.yaml"), "utf8");
    const base = placed("corp-twin", te, { expanded: ["site:hq-mx|10.180"] });
    const folds = base.nodes.filter((n) => n.data.kind === "hostfold").map((n) => (n.data as { key: string }).key);
    expect(folds.length).toBeGreaterThan(0);
    const p = placed("corp-twin", te, { expanded: ["site:hq-mx|10.180", ...folds] });
    expect(p.overBudget).toBeNull();
    const ids = p.nodes.map((n) => n.id);
    expect(ids.length).toBe(new Set(ids).size);
    const edgeIds = p.edges.map((e) => e.id);
    expect(edgeIds.length).toBe(new Set(edgeIds).size);
    expect(crossings(base)).toEqual([]);
    expect(unrouted(base)).toEqual([]);
    expect(crossings(p)).toEqual([]);
    expect(unrouted(p)).toEqual([]);
  });
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
    expect(one.nodes.filter((n) => n.type === "impliedSwitch").every((n) => (n.data as { hue: string }).hue === HUE_NEUTRAL)).toBe(true);
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
  it("a switch draws at most eight host icons, each a node of its own, and a `+N` fold last on its row; its host key opens it", () => {
    const sw = ID.segment("s0");
    const hostsOf = (p: ReturnType<typeof placed>) => p.edges.filter((e) => e.source === sw && e.data?.kind === "leg").map((e) => p.nodes.find((n) => n.id === e.target)!);
    const p = placed("many", many(1, 1));
    const shown = hostsOf(p);
    expect(shown.length).toBe(TREE_HOST_BUDGET);
    expect(shown.every((n) => n.type === "host" && !n.parentId)).toBe(true);
    expect((p.nodes.find((n) => n.id === sw)!.data as { hiddenChips: number }).hiddenChips).toBe(4);
    const fold = p.nodes.find((n) => n.id === TREE_ID.hostFold(sw))!;
    expect(fold.data).toMatchObject({ kind: "hostfold", key: hostsKey(sw), hidden: 4, open: false });
    // The fold sits right of the last icon, on the icons' rank, and the icons sit under their switch.
    const xs = shown.map((n) => n.position.x);
    expect(fold.position.x).toBeGreaterThan(Math.max(...xs));
    expect(new Set([...shown, fold].map((n) => n.position.y + (n.height ?? 0) / 2)).size).toBe(1);
    const box = p.nodes.find((n) => n.id === sw)!;
    expect(box.position.y + (box.height ?? 0)).toBeLessThan(fold.position.y);
    expect(crossings(p)).toEqual([]);
    const open = placed("many", many(1, 1), { expanded: [hostsKey(sw)] });
    expect(hostsOf(open).length).toBe(12);
    expect(open.nodes.find((n) => n.id === TREE_ID.hostFold(sw))?.data).toMatchObject({ open: true, hidden: 0 });
    expect(crossings(open)).toEqual([]);
  });
  it("implied switches of one site under one carrier fold into one site fold; a click opens it in place", () => {
    const p = placed("many", many(9, 3)); // three sites of three segments under gw
    const folds = p.nodes.filter((n) => n.type === "overflow");
    expect(folds.map((n) => (n.data as { label?: string }).label)).toEqual(["10.10 · 3 segments", "10.11 · 3 segments", "10.12 · 3 segments"]);
    expect(p.nodes.filter((n) => n.type === "impliedSwitch")).toEqual([]);
    expect(p.nodes.filter((n) => n.type === "host")).toEqual([]); // folded with their switches
    const open = placed("many", many(9, 3), { expanded: ["site:gw|10.11"] });
    expect(open.nodes.filter((n) => n.type === "impliedSwitch").map((n) => n.id)).toEqual([ID.segment("s3"), ID.segment("s4"), ID.segment("s5")]);
    expect(open.nodes.filter((n) => n.type === "host").length).toBe(3 * TREE_HOST_BUDGET);
    expect(open.nodes.some((n) => n.id === TREE_ID.siteFold("gw", "10.11"))).toBe(false);
    expect(crossings(open)).toEqual([]);
  });
});

describe("positions", () => {
  it("a box that would land on a pinned one moves right into free space in its rank", () => {
    const te = readFileSync(resolve(CATALOG, "canvas-test", "te.yaml"), "utf8");
    const free = placed("ct", te);
    const pills = free.nodes.filter((n) => n.type === "impliedSwitch");
    expect(pills.length).toBe(2);
    const [a, b] = pills as [typeof pills[number], typeof pills[number]];
    const cidrOf = (n: typeof a) => (n.data as { cidr: string }).cidr;
    // Pin `a` exactly where `b` sits: `b` must not stay under it.
    const g = documentGraph(readDoc({ id: "ct", teFile: "te.yaml", files: { "te.yaml": te } }), "ct");
    const p = placeTree(buildGraph(g.exportData, g.cast), { [`segment:${cidrOf(a)}`]: { ...b.position } }, { extraLegs: g.extraLegs, uplinks: g.uplinks, tree: g.tree });
    const na = p.nodes.find((n) => n.id === a.id)!;
    const nb = p.nodes.find((n) => n.id === b.id)!;
    expect(na.position).toEqual(b.position);
    expect(nb.position.x).toBeGreaterThanOrEqual(na.position.x + (na.width ?? 0));
  });
});

const BRIDGED = `twin_config:
  id: bridged
  segments:
    - { name: lan, cidr: 10.1.1.0/24 }
    - { name: access, cidr: 10.1.2.0/24 }
    - { name: island, cidr: 10.9.9.0/24 }
  nodes:
    - { name: ws, role: workstation, image: img@v1, net: access }
    - { name: lonely, role: workstation, image: img@v1, net: island }
    - name: bridge
      role: l2-switch
      image: img@v1
      nets: [{ segment: lan, l2: true }, { segment: access, l2: true }]
    - { name: gw, kind: router, image: vyos@v1, net: lan }
`;

describe("segments no device carries", () => {
  it("a segment a bridging host reaches sits on the router's rail beside the bridged one (R4-2b), dashed from the router via the host; the rest under a labelled 'Not carried' box", () => {
    const p = placed("bridged", BRIDGED);
    const y = (id: string) => p.nodes.find((n) => n.id === id)!.position.y;
    // One row under the router: the bridged segment is not a child of the bridging host's pill.
    expect(y(ID.segment("access"))).toBe(y(ID.segment("lan")));
    expect(p.edges.find((e) => e.target === ID.segment("access") && e.source === ID.router("gw"))?.data).toMatchObject({ kind: "carry", dashed: true, label: "via bridge" });
    const box = p.nodes.find((n) => n.id === "chrome:uncarried")!;
    expect(box.data).toMatchObject({ kind: "uncarried", label: "Not carried" });
    expect(p.edges.filter((e) => e.source === "chrome:uncarried").map((e) => e.target)).toEqual([ID.segment("island")]);
    expect(y(ID.segment("island"))).toBeGreaterThan(box.position.y);
    expect(crossings(p)).toEqual([]);
    expect(unrouted(p)).toEqual([]);
  });
  it("corp-twin: every segment is reached through its bridging hosts; nothing is 'not carried'", () => {
    const te = readFileSync(resolve(CATALOG, "corp-twin", "te.yaml"), "utf8");
    for (const fan of [undefined, 1000]) {
      const p = placed("corp-twin", te, fan ? { fan } : {});
      expect(p.nodes.some((n) => n.id === "chrome:uncarried")).toBe(false);
      // No orphan: every top-level box but the routers and the Internet has an edge coming in.
      const reached = new Set(p.edges.filter((e) => e.type !== "arc").map((e) => e.target));
      const orphans = p.nodes.filter((n) => !n.parentId && n.type !== "router" && n.id !== ID.internet && !reached.has(n.id)).map((n) => n.id);
      expect(orphans).toEqual([]);
    }
  });
});

// tier-cast's shape (catalog of the live checkout, 2026-11-20): gw has four legs -- an access leg to a
// three-host segment and three segments only routers are on (transit-1 to gw-4, transit-2 to gw-2,
// seg-0 to gw-3). Each is its own port of gw: four separate paths, no shared horizontal or vertical.
const TIER = `twin_config:
  id: tier
  segments:
    - { name: seg-20, cidr: 10.74.20.0/24 }
    - { name: transit-1, cidr: 10.254.0.0/29 }
    - { name: transit-2, cidr: 10.254.0.8/29 }
    - { name: seg-0, cidr: 10.74.0.0/24 }
    - { name: seg-31, cidr: 10.74.31.0/24 }
    - { name: seg-41, cidr: 10.74.41.0/24 }
    - { name: seg-42, cidr: 10.74.42.0/24 }
    - { name: seg-10, cidr: 10.74.10.0/24 }
    - { name: sink, cidr: 10.200.254.0/24 }
  nodes:
    - { name: dns, role: dns, image: img@v1, net: seg-20 }
    - { name: files, role: file-server, image: img@v1, net: seg-20 }
    - { name: web, role: web, image: img@v1, net: seg-20 }
    - { name: ep31, role: workstation, image: img@v1, net: seg-31 }
    - { name: ep41, role: workstation, image: img@v1, net: seg-41 }
    - { name: web42, role: web, image: img@v1, net: seg-42 }
    - { name: web10, role: web, image: img@v1, net: seg-10 }
    - { name: sink-1, role: sink, image: img@v1, net: sink }
    - { name: sink-2, role: sink, image: img@v1, net: sink }
    - { name: gw, kind: router, image: vyos@v1, nets: [{ segment: seg-20 }, { segment: transit-1 }, { segment: transit-2 }, { segment: seg-0 }] }
    - { name: gw-2, kind: router, image: vyos@v1, nets: [{ segment: seg-41 }, { segment: seg-42 }, { segment: transit-2 }] }
    - { name: gw-3, kind: router, image: vyos@v1, nets: [{ segment: seg-10 }, { segment: seg-0 }, { segment: sink }] }
    - { name: gw-4, kind: router, image: vyos@v1, nets: [{ segment: seg-31 }, { segment: transit-1 }] }
`;

// A real switch carrying two VLANs on one trunk, two hosts on each (no access links: access edges).
const TRUNKED = `twin_config:
  id: trunked
  segments:
    - { name: lan-a, cidr: 10.1.1.0/24 }
    - { name: lan-b, cidr: 10.1.2.0/24 }
  nodes:
    - { name: a1, role: workstation, image: img@v1, net: lan-a }
    - { name: a2, role: workstation, image: img@v1, net: lan-a }
    - { name: b1, role: workstation, image: img@v1, net: lan-b }
    - { name: b2, role: workstation, image: img@v1, net: lan-b }
    - { name: sw, kind: switch, role: l2-switch }
    - { name: gw, kind: router, image: vyos@v1, nets: [{ segment: lan-a }, { segment: lan-b }] }
  links:
    - { a: { device: gw, port: eth1 }, b: { device: sw, port: SFP1 }, mode: trunk, vlans: [lan-a, lan-b] }
`;

type Placed = ReturnType<typeof placed>;
const routeOf = (p: Placed, id: string): Point[] => p.edges.find((e) => e.id === id)!.data!.route!.points;
/** The y of each horizontal run of a route. */
const horizontals = (pts: readonly Point[]) => pts.slice(1).flatMap((b, i) => (Math.abs(pts[i]!.y - b.y) < 0.01 && Math.abs(pts[i]!.x - b.x) > 0.01 ? [b.y] : []));
/** The x of each vertical run of a route, with its y extent. */
const verticals = (pts: readonly Point[]) => pts.slice(1).flatMap((b, i) => (Math.abs(pts[i]!.x - b.x) < 0.01 ? [{ x: b.x, lo: Math.min(pts[i]!.y, b.y), hi: Math.max(pts[i]!.y, b.y) }] : []));

describe("lanes by segment, spread router exits (tier-cast, 2026-11-20)", () => {
  it("a router's legs get distinct lane y's and distinct exit x's: no two share a horizontal or a vertical", () => {
    const p = placed("tier", TIER);
    expect(crossings(p)).toEqual([]);
    expect(unrouted(p)).toEqual([]);
    const legs = p.edges.filter((e) => e.type !== "arc" && e.source === ID.router("gw"));
    expect(legs.map((e) => e.data?.kind).sort()).toEqual(["leg", "seglink", "seglink", "seglink"]);
    const routes = legs.map((e) => e.data!.route!.points);
    const exits = routes.map((r) => r[0]!.x);
    expect(new Set(exits).size).toBe(4);
    // Left to right in their targets' order.
    const boxes = absoluteBoxes(p.nodes);
    const byTarget = legs.map((e, i) => ({ tx: boxes.get(e.target)!.x + boxes.get(e.target)!.w / 2, exit: exits[i]! })).sort((a, b) => a.tx - b.tx);
    expect(byTarget.map((x) => x.exit)).toEqual([...exits].sort((a, b) => a - b));
    const lanes = routes.map((r) => horizontals(r));
    expect(lanes.every((h) => h.length === 1)).toBe(true);
    expect(new Set(lanes.flat()).size).toBe(4);
    // No two legs overlap on a vertical either.
    for (let i = 0; i < routes.length; i++)
      for (let j = i + 1; j < routes.length; j++)
        for (const a of verticals(routes[i]!)) for (const b of verticals(routes[j]!)) expect(Math.abs(a.x - b.x) < 0.01 && a.lo < b.hi && b.lo < a.hi, `${legs[i]!.id} and ${legs[j]!.id} share x=${a.x}`).toBe(false);
    for (const e of legs) expect(laneOf(e)).toBe(`seg:${(e.data as { segment: string }).segment}`);
  });
  it("the hosts of one switch share a lane: an implied switch's, and a real switch's per segment (one line per VLAN)", () => {
    const p = placed("tier", TIER);
    const sw = ID.segment("seg-20");
    const access = p.edges.filter((e) => e.source === sw && e.data?.kind === "leg");
    expect(access.length).toBe(3);
    const ys = new Set(access.flatMap((e) => horizontals(e.data!.route!.points)));
    expect(ys.size).toBe(1);
    // The implied switch's single-host segment keeps its switch and "1" badge (unchanged).
    expect(p.nodes.find((n) => n.id === ID.segment("seg-31"))?.data).toMatchObject({ kind: "segment", implied: true, hosts: 1 });

    const t = placed("trunked", TRUNKED);
    expect(crossings(t)).toEqual([]);
    const bySeg = new Map<string, Set<number>>();
    for (const e of t.edges.filter((x) => x.source === "device:sw" && x.data?.kind === "leg")) {
      const seg = (e.data as { segment: string }).segment;
      bySeg.set(seg, new Set([...(bySeg.get(seg) ?? []), ...horizontals(e.data!.route!.points)]));
    }
    expect([...bySeg.keys()].sort()).toEqual(["lan-a", "lan-b"]);
    expect([...bySeg.values()].map((s) => s.size)).toEqual([1, 1]);
    expect([...bySeg.get("lan-a")!][0]).not.toBe([...bySeg.get("lan-b")!][0]);
  });
  it("a seglink's pill sits on the child router's vertical drop, not on a horizontal any edge runs along", () => {
    const p = placed("tier", TIER);
    const boxes = absoluteBoxes(p.nodes);
    const links = p.edges.filter((e) => e.data?.kind === "seglink");
    expect(links.map((e) => (e.data as { segment: string }).segment).sort()).toEqual(["seg-0", "transit-1", "transit-2"]);
    const allRuns = p.edges.filter((e) => e.data?.route).map((e) => e.data!.route!.points).flatMap((pts) => pts.slice(1).map((b, i) => [pts[i]!, b] as const));
    for (const e of links) {
      const pts = routeOf(p, e.id);
      const at = dropPoint(pts);
      const child = boxes.get(e.target)!;
      expect(at.x).toBeCloseTo(child.x + child.w / 2);
      expect(at.y).toBeLessThan(child.y);
      expect(at.y).toBeGreaterThan(horizontals(pts).at(-1)!);
      const onRun = allRuns.some(([a, b]) => Math.abs(a.y - b.y) < 0.01 && Math.abs(a.y - at.y) < 1 && Math.min(a.x, b.x) <= at.x && at.x <= Math.max(a.x, b.x));
      expect(onRun, `${e.id}'s pill on a horizontal`).toBe(false);
    }
  });
  it("an edge of no single segment (a bridge's 'via', a host fold) still lanes by its source", () => {
    const b = placed("bridged", BRIDGED);
    const via = b.edges.find((e) => e.id.startsWith("bridge:"))!;
    expect(via.data).toMatchObject({ kind: "carry", dashed: true });
    expect(laneOf(via)).toBe(via.source);
    const segs = Array.from({ length: 12 }, (_, h) => `    - { name: h${h}, role: workstation, image: img@v1, net: s0 }`).join("\n");
    const f = placed("fold", `twin_config:\n  id: fold\n  segments:\n    - { name: s0, cidr: 10.1.0.0/24 }\n  nodes:\n${segs}\n    - { name: gw, kind: router, image: vyos@v1, net: s0 }\n`);
    const fold = f.edges.find((e) => e.id.startsWith("fold:"))!;
    expect(laneOf(fold)).toBe(fold.source);
    // The fold runs on its own line beside the hosts' segment lane.
    const hostLane = horizontals(f.edges.find((e) => e.source === fold.source && e.data?.kind === "leg")!.data!.route!.points);
    expect(horizontals(fold.data!.route!.points)).not.toEqual(hostLane);
  });
});
