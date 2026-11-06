/**
 * Rendering rules 5-9 of 2026-11-06 (`SPEC.md` "Rendering rules", track 3):
 * a host's legs are its config attachments only and a switch-class host on
 * several segments is a router-style device; legs leave a router downward,
 * land on a per-router handle on the segment's top edge and run through
 * the gutters, never through a tile; a router's fan into one site collapses
 * past N; one count-badge style; the labels button shows its mode.
 */
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { authoredGraph } from "../src/features/edit-canvas/canvas/authored";
import { busId, fanId, fanOut } from "../src/features/edit-canvas/canvas/fan";
import { decodePoints, encodePoints, nearestPolyline, polylineMid, polylinePath } from "../src/features/edit-canvas/canvas/legs";
import { ID, place, type CanvasEdge, type CanvasNode, type Rect } from "../src/features/edit-canvas/canvas/placement";
import { buildGrid, crossesInterior, interiorsCrossed, routeAll, routeOnGrid, simplify } from "../src/features/edit-canvas/canvas/route";
import { wire } from "../src/features/edit-canvas/canvas/wiring";
import { documentGraph, readDoc } from "../src/features/edit-canvas/edit/document";
import { DEVICE_H, DEVICE_W } from "../src/features/edit-canvas/layout/geometry";
import { buildGraph } from "../src/features/edit-canvas/model/graph";
import { DEVICE_ROLES } from "../src/features/edit-canvas/model/roles";
import type { TopologyExport } from "../src/api/types";
import { host, hosts, seeded, twinYaml } from "./helpers/canvasFixtures";
import { renderAt } from "./helpers/render";

/** A corp-twin-shaped design: one router fanning into two sites (six and
 *  three segments), an `l2-switch` host on three segments, a workstation
 *  on two, plain hosts on one. */
const BIG_TE = `twin_config:
  id: corp-big
  segments:
    - { name: a1, cidr: 10.70.1.0/24 }
    - { name: a2, cidr: 10.70.2.0/24 }
    - { name: a3, cidr: 10.70.3.0/24 }
    - { name: a4, cidr: 10.70.4.0/24 }
    - { name: a5, cidr: 10.70.5.0/24 }
    - { name: a6, cidr: 10.70.6.0/24 }
    - { name: b1, cidr: 10.80.1.0/24 }
    - { name: b2, cidr: 10.80.2.0/24 }
    - { name: b3, cidr: 10.80.3.0/24 }
  nodes:
    - name: gw
      role: gw
      kind: router
      image: debian-12-cloud@v1
      nets:
        - { segment: a1 }
        - { segment: a2 }
        - { segment: a3 }
        - { segment: a4 }
        - { segment: a5 }
        - { segment: a6 }
        - { segment: b1 }
        - { segment: b2 }
        - { segment: b3 }
    - name: sw1
      role: l2-switch
      image: debian-12-cloud@v1
      nets:
        - { segment: a2, ip: 10.70.2.15 }
        - { segment: a5, l2: true }
        - { segment: b1, l2: true }
    - name: ws2
      role: corp-workstation
      image: debian-12-cloud@v1
      nets:
        - { segment: a1 }
        - { segment: b2 }
    - name: ws01
      role: corp-workstation
      image: debian-12-cloud@v1
      net: a1
    - name: web01
      role: web-tier
      image: debian-12-cloud@v1
      count: 2
      net: b3
`;

function withTwin(id: string, te: string) {
  const base = seeded();
  return seeded({
    twins: { ...base.state.twins, [id]: { ...base.state.twins["corp-auth"]!, id } },
    documents: { ...base.state.documents, [id]: { id, te_file: "te.yaml", files: { "te.yaml": te, "twin.yaml": twinYaml(id) }, skipped: [] } },
    twinConfigs: [...base.state.twinConfigs, { id, description: null, cluster: null, parameterized: false, param_names: [], error: null }],
  });
}

/** The big design placed as the page would place it: its nodes, its structural edges, and the tiles as absolute boxes. */
function placedBig() {
  const g = documentGraph(readDoc({ id: "corp-big", teFile: "te.yaml", files: { "te.yaml": BIG_TE } }), "corp-big");
  const graph = buildGraph(g.exportData, g.cast);
  const placed = place(graph, undefined, { extraLegs: g.extraLegs, uplinks: g.uplinks });
  const abs = (n: CanvasNode): Rect => {
    let x = n.position.x;
    let y = n.position.y;
    let p = n.parentId ? placed.nodes.find((m) => m.id === n.parentId) : undefined;
    while (p) {
      x += p.position.x;
      y += p.position.y;
      p = p.parentId ? placed.nodes.find((m) => m.id === p!.parentId) : undefined;
    }
    return { x, y, w: n.width ?? 0, h: n.height ?? 0 };
  };
  const tiles = placed.nodes.filter((n) => n.data.kind === "segment" || n.data.kind === "overflow").map((n) => ({ id: n.id, box: abs(n) }));
  const legs = placed.edges.filter((e) => e.type === "leg");
  return { g, graph, placed, abs, tiles, legs };
}

/** Every run of `pts` against the tiles, except a chip leg's own tile. */
function tilesCrossed(pts: ReturnType<typeof decodePoints>, tiles: Array<{ id: string; box: Rect }>, except: string | null): string[] {
  const idx = interiorsCrossed(pts, tiles.map((t) => t.box));
  return idx.map((i) => tiles[i]!.id).filter((id) => id !== except);
}

afterEach(() => {
  localStorage.clear();
  sessionStorage.clear();
});

describe("rule 5: a host's legs are its config attachments; a switch-class host on several segments is a device", () => {
  it("the authored graph: the switch is a router-style device with the config's legs, the workstation keeps its chip and its second leg, nothing is inferred", () => {
    const { g } = placedBig();
    expect(g.devices).toEqual(["sw1"]);
    const sw = g.cast.routers!.find((r) => r.name === "sw1")!;
    expect(sw).toMatchObject({ kind: "host", role: "l2-switch", segments: ["a2", "a5", "b1"] });
    expect(g.exportData.nodes.map((n) => n.id)).not.toContain("sw1");
    expect(g.extraLegs).toEqual([{ host: "ws2", segment: "b2", ip: null }]);
    expect(DEVICE_ROLES.has("l2-switch")).toBe(true);
    // The role set is the caller's: with the switch role out of it the host is a chip again.
    const plain = authoredGraph({ scenario_id: "x", nodes: [{ id: "n1", type: "network", cidr: "10.0.0.0/24" }, { id: "n2", type: "network", cidr: "10.0.1.0/24" }, { id: "s", type: "host" }], edges: [{ source: "s", target: "n1" }, { source: "s", target: "n2" }] }, { id: "x", hosts: 1, segments: [{ name: "n1", cidr: "10.0.0.0/24", hosts: 1, roles: [], taps: [], nodes: [{ name: "s", kind: "host", role: "l2-switch", image: "i", count: 1, instances: ["s"], segments: ["n1", "n2"], requires: [], listen: [], install: [], personas: [] }] }], routers: [], warnings: [] }, { deviceRoles: new Set() });
    expect(plain.devices).toEqual([]);
    expect(plain.extraLegs.length).toBe(1);
  });

  it("the placement draws exactly the config's legs: the device's three from the router-style node, the workstation's one from its chip, none for a single-homed host", () => {
    const { placed, legs } = placedBig();
    const from = (id: string) => legs.filter((e) => e.source === id).map((e) => e.target).sort();
    expect(from(ID.router("sw1"))).toEqual([ID.segment("a2"), ID.segment("a5"), ID.segment("b1")]);
    expect(legs.filter((e) => e.source === ID.router("sw1")).every((e) => e.data?.kind === "leg" && e.data.from === "host")).toBe(true);
    expect(from(ID.host("ws2"))).toEqual([ID.segment("b2")]);
    expect(from(ID.host("ws01"))).toEqual([]);
    expect(placed.nodes.find((n) => n.id === ID.host("sw1"))).toBeUndefined();
    const dev = placed.nodes.find((n) => n.id === ID.router("sw1"))!;
    expect(dev.data).toMatchObject({ kind: "router", device: "l2-switch" });
    // A device's legs mark no tile as gatewayed: only the router's do.
    const a5 = placed.nodes.find((n) => n.data.kind === "segment" && n.data.name === "a5")!;
    expect(a5.data.kind === "segment" && a5.data.hasRouterLeg).toBe(true);
  });

  it("a cast twin's observed cross-segment traffic draws arcs, never a leg from a host", () => {
    const node = (id: string, segment: string): TopologyExport["nodes"][number] => ({ id, address: id, locality: "local", segment, segment_evidence_class: "observed", role: "l2-switch", role_confidence: 0.9, role_evidence_class: "observed", windows_seen: 1, listen_ports: [], dial_ports: [] });
    const exp: TopologyExport = {
      imprint_id: "c", pit_id: "p", as_of: 1, network: "n", vantage: "v", windows: 1, schema_version: "1",
      segments: [{ cidr: "10.1.0.0/24", name: "s1", entities: 1, coverage_source: null }, { cidr: "10.1.1.0/24", name: "s2", entities: 1, coverage_source: null }, { cidr: "10.1.2.0/24", name: "s3", entities: 1, coverage_source: null }],
      nodes: [node("10.1.0.5", "10.1.0.0/24"), node("10.1.1.5", "10.1.1.0/24"), node("10.1.2.5", "10.1.2.0/24")],
      edges: [
        { src: "10.1.0.5", dst: "10.1.1.5", proto: "tcp", bytes: 100, flows: 1, evidence_class: "observed" },
        { src: "10.1.0.5", dst: "10.1.2.5", proto: "tcp", bytes: 100, flows: 1, evidence_class: "observed" },
      ],
      census: { entities_total: 3, with_role: 3, placed: 3, local: 3 },
    };
    const placed = place(buildGraph(exp, { imprintId: "c", deployMinOccupancy: 0, routers: [] }));
    expect(placed.edges.filter((e) => e.type === "leg")).toEqual([]);
    expect(placed.edges.filter((e) => e.type === "arc").length).toBe(2);
  });

  it("on the page: the switch is a router-style node wearing its role, with three legs, and the inspector names it a host attached by the config", async () => {
    const { api } = withTwin("corp-big", BIG_TE);
    renderAt(api, "/twins/corp-big/canvas");
    await screen.findAllByTestId("canvas-host");
    expect(hosts()).toEqual(["web01", "ws01", "ws2"]);
    const routers = screen.getAllByTestId("canvas-router");
    const sw = routers.find((r) => r.dataset["nodeId"] === "sw1")!;
    expect(sw.dataset["device"]).toBe("l2-switch");
    expect(within(sw).getByTestId("device-role").textContent).toBe("role: l2-switch");
    expect(sw.textContent).toContain("3 legs");
    expect(routers.find((r) => r.dataset["nodeId"] === "gw")!.dataset["device"]).toBeUndefined();
    fireEvent.click(sw);
    const panel = await screen.findByTestId("node-panel");
    expect(panel.textContent).toContain("sw1");
  });
});

describe("rule 6: orthogonal legs in the gutters", () => {
  it("the grid router: a leg leaves downward, turns in the gutter and arrives from above, around a box in the way", () => {
    // A router at the top, a tile straight under it, the target tile under that.
    const boxes: Rect[] = [{ x: 100, y: 0, w: 40, h: 40 }, { x: 60, y: 100, w: 120, h: 60 }, { x: 60, y: 200, w: 120, h: 60 }];
    const routes = routeAll([{ id: "a", from: { x: 120, y: 40 }, to: { x: 120, y: 200 } }], boxes);
    const pts = routes.get("a")!;
    expect(pts[0]).toEqual({ x: 120, y: 40 });
    expect(pts[pts.length - 1]).toEqual({ x: 120, y: 200 });
    expect(pts[1]!.x).toBe(120); // leaves straight down
    expect(pts[1]!.y).toBeGreaterThan(40);
    const last = pts[pts.length - 2]!;
    expect(last.x).toBe(120); // arrives straight down
    expect(last.y).toBeLessThan(200);
    expect(interiorsCrossed(pts, boxes)).toEqual([]);
    // Every run is axis-aligned.
    for (let k = 1; k < pts.length; k++) expect(pts[k]!.x === pts[k - 1]!.x || pts[k]!.y === pts[k - 1]!.y).toBe(true);
    // The straight case is a straight line.
    const clear = routeAll([{ id: "b", from: { x: 120, y: 40 }, to: { x: 120, y: 100 } }], boxes.slice(0, 2)).get("b")!;
    expect(clear).toEqual([{ x: 120, y: 40 }, { x: 120, y: 100 }]);
    // A wall across the plane is gone around (the grid reaches past every box) ...
    const around = routeAll([{ id: "c", from: { x: 120, y: 40 }, to: { x: 120, y: 200 } }], [...boxes, { x: 0, y: 50, w: 300, h: 10 }]).get("c")!;
    expect(interiorsCrossed(around, [...boxes, { x: 0, y: 50, w: 300, h: 10 }])).toEqual([]);
    expect(around.some((p) => p.x < 0 || p.x > 300)).toBe(true);
    // ... but a port boxed in (a box stacked over the router's foot) has no route.
    const walled = routeAll([{ id: "d", from: { x: 120, y: 40 }, to: { x: 120, y: 200 } }], [...boxes, { x: 100, y: 44, w: 40, h: 10 }]);
    expect(walled.has("d")).toBe(false);
  });

  it("the helpers: a channel inside a box is blocked, a bend costs, collinear points fold, the path rounds its corners, the hit-test reads the points back", () => {
    const grid = buildGrid([{ x: 10, y: 10, w: 20, h: 20 }], [20], [20], 4);
    expect(grid.xs).toEqual([6, 20, 34]);
    expect(grid.ys).toEqual([6, 20, 34]);
    expect(routeOnGrid(grid, { x: 20, y: 6 }, { x: 20, y: 34 }, 40)).not.toContainEqual({ x: 20, y: 20 });
    expect(crossesInterior({ x: 0, y: 20 }, { x: 40, y: 20 }, { x: 10, y: 10, w: 20, h: 20 })).toBe(true);
    expect(crossesInterior({ x: 0, y: 10 }, { x: 40, y: 10 }, { x: 10, y: 10, w: 20, h: 20 })).toBe(false); // along the edge
    expect(simplify([{ x: 0, y: 0 }, { x: 0, y: 5 }, { x: 0, y: 9 }, { x: 4, y: 9 }, { x: 4, y: 9 }])).toEqual([{ x: 0, y: 0 }, { x: 0, y: 9 }, { x: 4, y: 9 }]);
    const pts = [{ x: 0, y: 0 }, { x: 0, y: 50 }, { x: 50, y: 50 }];
    expect(polylinePath(pts)).toBe("M 0 0 L 0 44 Q 0 50 6 50 L 50 50");
    expect(polylineMid(pts)).toEqual({ x: 0, y: 50 });
    expect(decodePoints(encodePoints(pts))).toEqual(pts);
    expect(decodePoints("1,2 x,y")).toEqual([]);
    expect(nearestPolyline({ x: 20, y: 53 }, [{ id: "l", pts }], 8)).toBe("l");
    expect(nearestPolyline({ x: 20, y: 20 }, [{ id: "l", pts }], 8)).toBeNull();
  });

  it("over the big design: every leg leaves its router's bottom, lands on its tile's top edge from above, and no run crosses a tile's interior -- with every leg drawn and with the fans collapsed", () => {
    const { placed, abs, tiles, legs } = placedBig();
    for (const n of [Number.POSITIVE_INFINITY, 4]) {
      const drawn = fanOut(legs, n, new Set());
      const t0 = performance.now();
      const { routes } = wire(placed.nodes, drawn);
      const ms = performance.now() - t0;
      expect(ms, `wiring ${drawn.length} edges took ${ms.toFixed(0)} ms`).toBeLessThan(2000);
      for (const e of drawn) {
        const pts = routes.get(e.id);
        expect(pts, `${e.id} is wired`).toBeTruthy();
        const src = placed.nodes.find((x) => x.id === e.source)!;
        const own = src.data.kind === "host" ? src.parentId! : null;
        expect(tilesCrossed(pts!, tiles, own), `${e.id} at N=${n} crosses no tile: ${encodePoints(pts!)}`).toEqual([]);
        for (let k = 1; k < pts!.length; k++) expect(pts![k]!.x === pts![k - 1]!.x || pts![k]!.y === pts![k - 1]!.y, `${e.id} run ${k} is axis-aligned`).toBe(true);
        if (e.data?.kind === "leg" && src.data.kind === "router") {
          const r = abs(src);
          expect(pts![0], `${e.id} leaves the router's bottom-centre`).toEqual({ x: r.x + r.w / 2, y: r.y + DEVICE_H });
          expect(pts![1]!.x).toBe(pts![0]!.x);
          expect(pts![1]!.y).toBeGreaterThan(pts![0]!.y);
        }
        if (e.data?.kind === "leg" || e.data?.kind === "bus") {
          const t = abs(placed.nodes.find((x) => x.id === e.target)!);
          const end = pts![pts!.length - 1]!;
          expect(end.y, `${e.id} ends on its tile's top edge`).toBe(t.y);
          expect(end.x).toBeGreaterThan(t.x);
          expect(end.x).toBeLessThan(t.x + t.w);
          const before = pts![pts!.length - 2]!;
          expect(before.x, `${e.id} arrives from above`).toBe(end.x);
          expect(before.y).toBeLessThan(end.y);
        }
        if (e.data?.kind === "fan") {
          const s = abs(placed.nodes.find((x) => x.id === e.target)!);
          expect(pts![pts!.length - 1]!.y, `${e.id} lands on the site's top edge`).toBe(s.y);
        }
      }
      // Rule 6's handles: the router and the switch both land on a2 and a5 at different x.
      if (n === Number.POSITIVE_INFINITY) {
        for (const seg of ["a2", "a5"]) {
          const xs = [routes.get(`leg:gw>${seg}`)!, routes.get(`leg:sw1>${seg}`)!].map((p) => p[p.length - 1]!.x);
          expect(xs[0]).not.toBe(xs[1]);
        }
      }
    }
    // A router's node is its drawn box (plane epoch 4), not a span over the segments it joins.
    expect(abs(placed.nodes.find((x) => x.id === ID.router("gw"))!).w).toBe(DEVICE_W);
  });
});

describe("rule 7: the fan collapses per site", () => {
  it("fanOut: past N a router's legs into a site become one fan edge and a bus; a selected router or an opened site expands them; the folded overflow leg goes through the fan", () => {
    const { legs } = placedBig();
    const gwA = legs.filter((e) => e.source === ID.router("gw") && e.data?.kind === "leg" && e.data.site === "site:10.70");
    expect(gwA.length).toBe(6);
    const out = fanOut(legs, 4, new Set());
    const fan = out.find((e) => e.id === fanId("gw", "site:10.70"))!;
    expect(fan).toBeTruthy();
    expect(fan.target).toBe("site:10.70");
    expect(fan.data).toMatchObject({ kind: "fan", count: 6, label: "6 segments" });
    expect(out.filter((e) => e.data?.kind === "bus" && e.source === "site:10.70").map((e) => e.id).sort()).toEqual(["a1", "a2", "a3", "a4", "a5", "a6"].map((s) => busId("gw", ID.segment(s))).sort());
    expect(out.some((e) => e.id === `leg:gw>a1`)).toBe(false);
    // Three legs into 10.80 stay as they are; the switch's legs (two sites, under N) too.
    expect(out.filter((e) => e.source === ID.router("gw") && e.data?.kind === "leg").length).toBe(3);
    expect(out.filter((e) => e.source === ID.router("sw1")).length).toBe(3);
    expect(out.filter((e) => e.source === ID.host("ws2")).length).toBe(1);
    // Opened by the router's name or the site's key: the six legs are back.
    for (const open of [new Set(["gw"]), new Set(["site:10.70"])]) {
      const opened = fanOut(legs, 4, open);
      expect(opened.some((e) => e.data?.kind === "fan")).toBe(false);
      expect(opened.length).toBe(legs.length);
    }
    // N = all never collapses; N = 2 collapses the 10.80 side and the switch's... no: the switch has one leg per site but two into 10.70 -- under 2.
    expect(fanOut(legs, Number.POSITIVE_INFINITY, new Set()).length).toBe(legs.length);
    expect(fanOut(legs, 2, new Set()).filter((e) => e.data?.kind === "fan").map((e) => e.id).sort()).toEqual([fanId("gw", "site:10.70"), fanId("gw", "site:10.80")]);
    // A folded overflow leg is a fan however few legs are drawn.
    const folded: CanvasEdge = { id: "legs:gw>overflow:site:10.90", type: "leg", source: ID.router("gw"), target: "overflow:site:10.90", data: { kind: "leg", label: "3 segments behind +N more", from: "router", folded: 3, site: "site:10.90" } };
    const withFold = fanOut([folded], 4, new Set());
    expect(withFold.map((e) => e.id)).toEqual([fanId("gw", "site:10.90"), busId("gw", "overflow:site:10.90")]);
    expect(withFold[0]!.data).toMatchObject({ kind: "fan", count: 3, label: "3 segments · 3 behind +N more" });
  });

  it("the rail's Fan-out button cycles 4 → 8 → all → 2 and is kept per user", async () => {
    const { api } = seeded();
    renderAt(api, "/twins/corp-auth/canvas");
    await screen.findAllByTestId("canvas-host");
    const btn = screen.getByTestId("rail-fan");
    expect(btn.getAttribute("aria-label")).toBe("Fan-out: 4");
    fireEvent.click(btn);
    await waitFor(() => expect(screen.getByTestId("rail-fan").getAttribute("aria-label")).toBe("Fan-out: 8"));
    expect(localStorage.getItem("axr-canvas-fan")).toBe("8");
    fireEvent.click(screen.getByTestId("rail-fan"));
    await waitFor(() => expect(screen.getByTestId("rail-fan").getAttribute("aria-label")).toBe("Fan-out: all"));
    expect(localStorage.getItem("axr-canvas-fan")).toBe("all");
    fireEvent.click(screen.getByTestId("rail-fan"));
    await waitFor(() => expect(screen.getByTestId("rail-fan").getAttribute("aria-label")).toBe("Fan-out: 2"));
  });
});

describe("rules 8 and 9: the badge and the labels button", () => {
  it("a count group's chip carries the one `×N` badge; the labels button's icon changes with its mode", async () => {
    const { api } = withTwin("corp-big", BIG_TE);
    renderAt(api, "/twins/corp-big/canvas");
    await screen.findAllByTestId("canvas-host");
    const badge = within(host("web01")).getByTestId("count-badge");
    expect(badge.textContent).toBe("×2");
    expect(badge.className).toContain("cv-count-badge");
    const icon = () => screen.getByTestId("rail-labels").innerHTML;
    const selected = icon();
    expect(screen.getByTestId("rail-labels").getAttribute("aria-label")).toBe("Labels: selected");
    fireEvent.click(screen.getByTestId("rail-labels"));
    await waitFor(() => expect(screen.getByTestId("rail-labels").getAttribute("aria-label")).toBe("Labels: none"));
    const none = icon();
    fireEvent.click(screen.getByTestId("rail-labels"));
    await waitFor(() => expect(screen.getByTestId("rail-labels").getAttribute("aria-label")).toBe("Labels: all"));
    const all = icon();
    expect(new Set([selected, none, all]).size).toBe(3);
  });
});
