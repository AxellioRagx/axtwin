/**
 * The rendering rules of track 4 (`SPEC.md` "Rendering rules", 2026-11-06):
 * a segment is a bus with its hosts sitting on it; routers sit above, one
 * hue each, one vertical drop per bus they serve, the fan collapsing past N;
 * a switch-class host is a member of its bus, never a gateway, its observed
 * adjacency evidence the inspector offers to attach; a multi-homed host sits
 * on the lowest of its buses with a stub to each other one; the site is a
 * tint; a router's drop re-homes by drag and "Add segment" lands in the
 * selected site. The geometry is checked over the computed layout (jsdom
 * draws no edge): no leg crosses a host row.
 */
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { CastRoster, TopologyExport } from "../src/api/types";
import { rankAt, reorderPins } from "../src/features/edit-canvas/Canvas";
import { BUS_BADGE_W, BUS_LABEL_W, laneLineY } from "../src/features/edit-canvas/canvas/bus";
import { observedSegments } from "../src/features/edit-canvas/canvas/evidence";
import { busId, fanId, fanOut } from "../src/features/edit-canvas/canvas/fan";
import { HUE_GREY, ROUTER_HUES, hueOf } from "../src/features/edit-canvas/canvas/hues";
import { encodePoints } from "../src/features/edit-canvas/canvas/legs";
import { ID, place, type CanvasNode, type Rect } from "../src/features/edit-canvas/canvas/placement";
import { interiorsCrossed, routeAll } from "../src/features/edit-canvas/canvas/route";
import { wire } from "../src/features/edit-canvas/canvas/wiring";
import { documentGraph, readDoc, rehome } from "../src/features/edit-canvas/edit/document";
import { DEVICE_H } from "../src/features/edit-canvas/layout/geometry";
import { buildGraph } from "../src/features/edit-canvas/model/graph";
import { SWITCH_ROLES } from "../src/features/edit-canvas/model/roles";
import { twin } from "./imprints.page.test";
import { commit, host, hosts, item, panel, seeded, segment, segments, twinYaml } from "./helpers/canvasFixtures";
import { renderAt } from "./helpers/render";

/** A corp-twin-shaped design: one router into two sites (six and three
 *  buses), two `l2-switch` hosts on several buses, a workstation on two,
 *  plain hosts on one, a count group. */
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
    - name: hq
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
    - name: sw2
      role: l2-switch
      image: debian-12-cloud@v1
      nets:
        - { segment: b2, l2: true }
        - { segment: b3, l2: true }
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

/** Several routers (the datacenter twin's shape): one hue each, buses shared by two. */
const MULTI_TE = `twin_config:
  id: corp-multi
  segments:
    - { name: s1, cidr: 10.60.1.0/24 }
    - { name: s2, cidr: 10.60.2.0/24 }
    - { name: s3, cidr: 10.60.3.0/24 }
    - { name: s4, cidr: 10.60.4.0/24 }
  nodes:
    - name: gw
      kind: router
      image: debian-12-cloud@v1
      nets:
        - { segment: s1 }
        - { segment: s2 }
    - name: fab-r1
      kind: router
      image: debian-12-cloud@v1
      nets:
        - { segment: s2 }
        - { segment: s3 }
    - name: fab-r2
      kind: router
      image: debian-12-cloud@v1
      nets:
        - { segment: s3 }
        - { segment: s4 }
    - name: h1
      image: debian-12-cloud@v1
      net: s1
`;

/** Two sites: a lone 10.1 segment (the misc group) and two 10.2 segments. */
const TWO_SITES_TE = `twin_config:
  id: corp-two
  segments:
    - { name: a, cidr: 10.1.0.0/24 }
    - { name: b, cidr: 10.2.0.0/24 }
    - { name: b2, cidr: 10.2.1.0/24 }
  nodes:
    - name: gw
      kind: router
      image: debian-12-cloud@v1
      nets:
        - { segment: a }
        - { segment: b }
    - name: ws
      image: debian-12-cloud@v1
      net: b
`;

function withTwin(id: string, te: string, over: Parameters<typeof seeded>[0] = {}) {
  const base = seeded();
  return seeded({
    twins: { ...base.state.twins, [id]: { ...base.state.twins["corp-auth"]!, id } },
    documents: { ...base.state.documents, [id]: { id, te_file: "te.yaml", files: { "te.yaml": te, "twin.yaml": twinYaml(id) }, skipped: [] } },
    twinConfigs: [...base.state.twinConfigs, { id, description: null, cluster: null, parameterized: false, param_names: [], error: null }],
    ...over,
  });
}

/** A design placed as the page would place it: its nodes, its edges, and each node's absolute box. */
function placed(id: string, te: string) {
  const g = documentGraph(readDoc({ id, teFile: "te.yaml", files: { "te.yaml": te } }), id);
  const graph = buildGraph(g.exportData, g.cast);
  const p = place(graph, undefined, { extraLegs: g.extraLegs, uplinks: g.uplinks });
  const abs = (n: CanvasNode): Rect => {
    let x = n.position.x;
    let y = n.position.y;
    let parent = n.parentId ? p.nodes.find((m) => m.id === n.parentId) : undefined;
    while (parent) {
      x += parent.position.x;
      y += parent.position.y;
      parent = parent.parentId ? p.nodes.find((m) => m.id === parent!.parentId) : undefined;
    }
    return { x, y, w: n.width ?? 0, h: n.height ?? 0 };
  };
  const node = (nid: string) => p.nodes.find((n) => n.id === nid)!;
  return { g, graph, p, abs, node, legs: p.edges.filter((e) => e.type === "leg") };
}

afterEach(() => {
  localStorage.clear();
  sessionStorage.clear();
});

describe("rules 1 and 3: a segment is a bus, a switch is a member of it, legs are the routers'", () => {
  it("corp-big: one router node, legs only from routers to their nets, every switch an icon on a bus with no leg", () => {
    const { p, legs, node } = placed("corp-big", BIG_TE);
    expect(p.nodes.filter((n) => n.type === "router").map((n) => n.id)).toEqual([ID.router("hq")]);
    const routerLegs = legs.filter((e) => e.data?.kind === "leg");
    expect(routerLegs.length).toBe(9);
    expect(routerLegs.every((e) => e.source === ID.router("hq"))).toBe(true);
    expect(routerLegs.map((e) => e.target).sort()).toEqual(["a1", "a2", "a3", "a4", "a5", "a6", "b1", "b2", "b3"].map(ID.segment).sort());
    for (const sw of ["sw1", "sw2"]) {
      const n = node(ID.host(sw));
      expect(n.type).toBe("host");
      expect(n.parentId?.startsWith("seg:")).toBe(true);
      expect(legs.filter((e) => e.source === ID.host(sw) && e.data?.kind === "leg")).toEqual([]);
      expect(p.nodes.find((n2) => n2.id === ID.router(sw))).toBeUndefined();
    }
    expect(SWITCH_ROLES.has("l2-switch")).toBe(true);
    // No bus has two drops from one router, and a bus's dots are exactly the routers the config puts on it.
    const pairs = routerLegs.map((e) => `${e.source}>${e.target}`);
    expect(new Set(pairs).size).toBe(pairs.length);
    for (const seg of p.nodes.filter((n) => n.data.kind === "segment")) {
      expect(seg.data.kind === "segment" && seg.data.served!.map((s) => s.router)).toEqual(["hq"]);
    }
  });

  it("on the page: one router, every switch an icon with the switch glyph and its role badge on its bus, the inspector naming it a member", async () => {
    const { api } = withTwin("corp-big", BIG_TE);
    renderAt(api, "/twins/corp-big/canvas");
    await screen.findAllByTestId("canvas-host");
    expect(screen.getAllByTestId("canvas-router").map((r) => r.dataset["nodeId"])).toEqual(["hq"]);
    expect(hosts()).toEqual(["sw1", "sw2", "web01", "ws01", "ws2"]);
    const sw = host("sw1");
    expect(sw.dataset["switch"]).toBe("true");
    expect(within(sw).getByTestId("role-badge").textContent).toBe("L2");
    expect(sw.dataset["role"]).toBe("L2SW");
    expect(sw.dataset["multi"]).toBe("true");
    expect(segments().length).toBe(9);
    fireEvent.click(sw);
    const np = await screen.findByTestId("node-panel");
    expect(within(np).getByTestId("switch-note").textContent).toMatch(/member of its segments/);
    expect(within(np).getAllByTestId("attachment").map((a) => a.dataset["segment"])).toEqual(["a2", "a5", "b1"]);
    expect(screen.queryByTestId("observed-segments")).toBeNull(); // nothing observed on an authored twin
  });
});

describe("rule 2: routers above, one hue each, one vertical drop per bus, the fan past N", () => {
  it("corp-multi: three routers wear three hues and a shared bus's dots name both, in those hues", () => {
    const { p, node } = placed("corp-multi", MULTI_TE);
    const hues = ["gw", "fab-r1", "fab-r2"].map((r) => {
      const d = node(ID.router(r)).data;
      return d.kind === "router" ? d.hue : "";
    });
    expect(new Set(hues).size).toBe(3);
    expect(hues).toEqual([ROUTER_HUES[0], ROUTER_HUES[1], ROUTER_HUES[2]]);
    expect(hueOf(8)).toBe(HUE_GREY);
    const served = (name: string) => {
      const d = node(ID.segment(name)).data;
      return d.kind === "segment" ? d.served! : [];
    };
    expect(served("s1")).toEqual([{ router: "gw", hue: hues[0] }]);
    expect(served("s2")).toEqual([{ router: "gw", hue: hues[0] }, { router: "fab-r1", hue: hues[1] }]);
    expect(served("s4")).toEqual([{ router: "fab-r2", hue: hues[2] }]);
    for (const e of p.edges.filter((x) => x.data?.kind === "leg")) {
      const d = e.data as { hue: string };
      const r = node(e.source).data;
      expect(d.hue).toBe(r.kind === "router" ? r.hue : null);
    }
  });

  it("on the page: the three routers' hues and the bus dots match", async () => {
    const { api } = withTwin("corp-multi", MULTI_TE);
    renderAt(api, "/twins/corp-multi/canvas");
    await screen.findAllByTestId("canvas-host");
    const hue = Object.fromEntries(screen.getAllByTestId("canvas-router").map((r) => [r.dataset["nodeId"], r.dataset["hue"]]));
    expect(new Set(Object.values(hue)).size).toBe(3);
    const dots = within(segment("s2")).getByTestId("bus-dots");
    const dotHues = [...dots.querySelectorAll<HTMLElement>(".cv-bus-dot")].map((d) => [d.dataset["router"], d.style.background]);
    expect(dotHues.map((d) => d[0])).toEqual(["gw", "fab-r1"]);
    const rgb = (hex: string) => `rgb(${parseInt(hex.slice(1, 3), 16)}, ${parseInt(hex.slice(3, 5), 16)}, ${parseInt(hex.slice(5, 7), 16)})`;
    expect(dotHues[0]![1]).toBe(rgb(hue["gw"]!));
    expect(dotHues[1]![1]).toBe(rgb(hue["fab-r1"]!));
  });

  it("canvas-test's shape: gw sits above its two buses with exactly two drops, each landing in the port region of its bus", () => {
    const { p, abs, node, legs } = placed("corp-auth", `twin_config:
  id: corp-auth
  segments:
    - { name: office, cidr: 10.70.10.0/24 }
    - { name: servers, cidr: 10.70.20.0/24 }
  nodes:
    - name: gw
      kind: router
      image: debian-12-cloud@v1
      nets:
        - { segment: office }
        - { segment: servers }
    - name: ws
      image: debian-12-cloud@v1
      net: office
`);
    const gw = abs(node(ID.router("gw")));
    const buses = p.nodes.filter((n) => n.data.kind === "segment").map((n) => abs(n));
    for (const b of buses) expect(gw.y + gw.h).toBeLessThan(b.y);
    expect(legs.map((e) => e.id).sort()).toEqual(["leg:gw>office", "leg:gw>servers"]);
    const { routes } = wire(p.nodes, legs);
    for (const e of legs) {
      const pts = routes.get(e.id)!;
      const lane = abs(node(e.target));
      const end = pts[pts.length - 1]!;
      expect(end.y).toBe(laneLineY(lane));
      expect(end.x).toBeGreaterThan(lane.x + BUS_LABEL_W);
      expect(end.x).toBeLessThan(lane.x + lane.w - BUS_BADGE_W);
      expect(pts[0]).toEqual({ x: gw.x + gw.w / 2, y: gw.y + DEVICE_H });
    }
  });

  it("the geometry, over corp-big with every leg drawn and with the fans collapsed: no run crosses a host row, a label or a badge; drops leave the router downward and land on the line from above", () => {
    const { p, abs, node, legs } = placed("corp-big", BIG_TE);
    for (const n of [Number.POSITIVE_INFINITY, 4]) {
      const drawn = fanOut(legs, n, new Set());
      const t0 = performance.now();
      const { routes, hostRows, obstacles } = wire(p.nodes, drawn);
      const ms = performance.now() - t0;
      expect(ms, `wiring ${drawn.length} edges took ${ms.toFixed(0)} ms`).toBeLessThan(2000);
      expect(hostRows.length).toBe(3); // a1 (ws2, ws01), a5 (sw1), b3 (sw2, web01)
      for (const e of drawn) {
        const pts = routes.get(e.id);
        expect(pts, `${e.id} is wired`).toBeTruthy();
        expect(interiorsCrossed(pts!, hostRows), `${e.id} at N=${n} crosses no host row: ${encodePoints(pts!)}`).toEqual([]);
        const src = node(e.source);
        const ownRows = src.data.kind === "host" ? hostRows.filter((r) => { const b = abs(src); return b.x >= r.x && b.x + b.w <= r.x + r.w && b.y >= r.y && b.y + b.h <= r.y + r.h; }) : [];
        const others = obstacles.filter((o) => !ownRows.includes(o));
        expect(interiorsCrossed(pts!, others), `${e.id} at N=${n} crosses no label, badge or box: ${encodePoints(pts!)}`).toEqual([]);
        for (let k = 1; k < pts!.length; k++) expect(pts![k]!.x === pts![k - 1]!.x || pts![k]!.y === pts![k - 1]!.y, `${e.id} run ${k} is axis-aligned`).toBe(true);
        const kind = e.data?.kind;
        if (kind === "leg" || kind === "fan") {
          const r = abs(src);
          expect(pts![0], `${e.id} leaves the router's bottom-centre`).toEqual({ x: r.x + r.w / 2, y: r.y + DEVICE_H });
          expect(pts![1]!.x).toBe(pts![0]!.x);
          expect(pts![1]!.y).toBeGreaterThan(pts![0]!.y);
        }
        if (kind === "leg" || kind === "bus") {
          const t = abs(node(e.target));
          const end = pts![pts!.length - 1]!;
          expect(end.y, `${e.id} ends on its bus's line`).toBe(laneLineY(t));
          expect(end.x).toBeGreaterThan(t.x + BUS_LABEL_W);
          expect(end.x).toBeLessThan(t.x + t.w - BUS_BADGE_W);
          const before = pts![pts!.length - 2]!;
          expect(before.x, `${e.id} arrives straight down`).toBe(end.x);
          expect(before.y).toBeLessThan(end.y);
        }
        if (kind === "stub") {
          const c = abs(src);
          expect(pts![0], `${e.id} leaves the icon's top`).toEqual({ x: c.x + c.w / 2, y: c.y });
          const t = abs(node(e.target));
          expect(pts![pts!.length - 1]!.y).toBe(laneLineY(t));
        }
        if (kind === "fan") {
          const s = abs(node(e.target));
          expect(pts![pts!.length - 1]!.y, `${e.id} lands on the site's top edge`).toBe(s.y);
        }
      }
      // Two sources into one site drop down different columns.
      const landings = new Map<string, number[]>();
      for (const e of drawn) {
        const pts = routes.get(e.id)!;
        if (e.data?.kind !== "leg" && e.data?.kind !== "stub") continue;
        const site = node(e.target).parentId!;
        landings.set(site, [...(landings.get(site) ?? []), pts[pts.length - 1]!.x]);
      }
      for (const [site, xs] of landings) {
        const bySource = new Map<number, Set<string>>();
        for (const e of drawn) {
          if (e.data?.kind !== "leg" && e.data?.kind !== "stub") continue;
          if (node(e.target).parentId !== site) continue;
          const x = routes.get(e.id)![routes.get(e.id)!.length - 1]!.x;
          bySource.set(x, new Set([...(bySource.get(x) ?? []), e.source]));
        }
        for (const [, sources] of bySource) expect(sources.size, `one column per source in ${site}: ${xs.join(",")}`).toBe(1);
      }
    }
  });

  it("the grid router arrives from below when asked: a stub up onto a bus above its icon", () => {
    const boxes: Rect[] = [{ x: 0, y: 100, w: 200, h: 30 }];
    const up = routeAll([{ id: "s", from: { x: 100, y: 200 }, to: { x: 150, y: 60 }, leave: "up", arrive: "up" }], boxes).get("s")!;
    expect(up[0]).toEqual({ x: 100, y: 200 });
    expect(up[up.length - 1]).toEqual({ x: 150, y: 60 });
    const before = up[up.length - 2]!;
    expect(before.x).toBe(150);
    expect(before.y).toBeGreaterThan(60);
    expect(interiorsCrossed(up, boxes)).toEqual([]);
  });

  it("fanOut: past N a router's legs into a site become one trunk and a rail in the router's hue; the rail's Fan-out button cycles and is kept", async () => {
    const { legs } = placed("corp-big", BIG_TE);
    const out = fanOut(legs, 4, new Set());
    const fan = out.find((e) => e.id === fanId("hq", "site:10.70"))!;
    expect(fan.target).toBe("site:10.70");
    expect(fan.data).toMatchObject({ kind: "fan", count: 6, label: "6 segments", hue: ROUTER_HUES[0] });
    expect(out.filter((e) => e.data?.kind === "bus" && e.source === "site:10.70").map((e) => e.id).sort()).toEqual(["a1", "a2", "a3", "a4", "a5", "a6"].map((s) => busId("hq", ID.segment(s))).sort());
    expect(out.filter((e) => e.data?.kind === "bus").every((e) => (e.data as { hue: string }).hue === ROUTER_HUES[0])).toBe(true);
    // The three legs into 10.80 stay; the stubs pass through untouched.
    expect(out.filter((e) => e.source === ID.router("hq") && e.data?.kind === "leg").length).toBe(3);
    expect(out.filter((e) => e.data?.kind === "stub").length).toBe(legs.filter((e) => e.data?.kind === "stub").length);
    for (const open of [new Set(["hq"]), new Set(["site:10.70"])]) expect(fanOut(legs, 4, open).some((e) => e.data?.kind === "fan")).toBe(false);
    expect(fanOut(legs, Number.POSITIVE_INFINITY, new Set()).length).toBe(legs.length);

    const { api } = seeded();
    renderAt(api, "/twins/corp-auth/canvas");
    await screen.findAllByTestId("canvas-host");
    const btn = screen.getByTestId("rail-fan");
    expect(btn.getAttribute("aria-label")).toBe("Fan-out: 4");
    fireEvent.click(btn);
    await waitFor(() => expect(screen.getByTestId("rail-fan").getAttribute("aria-label")).toBe("Fan-out: 8"));
    expect(localStorage.getItem("axr-canvas-fan")).toBe("8");
  });
});

describe("rule 4: a multi-homed host sits on the lowest of its buses with a stub to each other one", () => {
  it("sw1 (a2, a5, b1) sits on a5 with stubs to a2 and b1; ws2 (a1, b2) stays on a1 with a stub to b2; sw2 (b2, b3) sits on b3", () => {
    const { node, legs } = placed("corp-big", BIG_TE);
    expect(node(ID.host("sw1")).parentId).toBe(ID.segment("a5"));
    expect(node(ID.host("sw1")).data).toMatchObject({ multi: true });
    expect(legs.filter((e) => e.source === ID.host("sw1")).map((e) => e.id).sort()).toEqual(["stub:sw1>a2", "stub:sw1>b1"]);
    expect(node(ID.host("ws2")).parentId).toBe(ID.segment("a1"));
    expect(legs.filter((e) => e.source === ID.host("ws2")).map((e) => e.id)).toEqual(["stub:ws2>b2"]);
    expect(node(ID.host("sw2")).parentId).toBe(ID.segment("b3"));
    expect(legs.filter((e) => e.source === ID.host("sw2")).map((e) => e.id)).toEqual(["stub:sw2>b2"]);
    expect(legs.filter((e) => e.source === ID.host("ws01"))).toEqual([]);
    expect(legs.every((e) => e.data?.kind !== "stub" || e.source.startsWith("host:"))).toBe(true);
  });
});

describe("rule 3 (a): observed adjacency is evidence the inspector offers to attach", () => {
  const EXPORT: TopologyExport = {
    imprint_id: "corp-sw", pit_id: "pit-1", as_of: 1, network: "corp", vantage: "hq", windows: 1, schema_version: "1",
    segments: [
      { cidr: "10.80.20.0/24", name: "net-lan", entities: 2, coverage_source: null },
      { cidr: "10.80.30.0/24", name: "net-dmz", entities: 1, coverage_source: null },
      { cidr: "10.80.40.0/24", name: "net-ot", entities: 1, coverage_source: null },
    ],
    nodes: [
      { id: "mac:aa", address: "10.80.20.2", locality: "local", segment: "10.80.20.0/24", segment_evidence_class: "observed", role: "l2-switch", role_confidence: 0.9, role_evidence_class: "observed", windows_seen: 1, listen_ports: [], dial_ports: [] },
      { id: "address:10.80.30.7", address: "10.80.30.7", locality: "local", segment: "10.80.30.0/24", segment_evidence_class: "observed", role: null, role_confidence: null, role_evidence_class: null, windows_seen: 1, listen_ports: [], dial_ports: [] },
      { id: "address:10.80.40.7", address: "10.80.40.7", locality: "local", segment: "10.80.40.0/24", segment_evidence_class: "observed", role: null, role_confidence: null, role_evidence_class: null, windows_seen: 1, listen_ports: [], dial_ports: [] },
    ],
    edges: [
      { src: "mac:aa", dst: "address:10.80.30.7", proto: "tcp", bytes: 10, flows: 1, evidence_class: "observed" },
      { src: "address:10.80.40.7", dst: "mac:aa", proto: "udp", bytes: 10, flows: 1, evidence_class: "observed" },
    ],
    census: { entities_total: 3, with_role: 1, placed: 3, local: 3 },
  };
  const ROSTER: CastRoster = {
    id: "corp-sw", pit_id: "pit-1",
    node_roster: [{ name: "sw", kind: "host", role: "l2-switch", count: 1, segments: ["net-lan"], node_ids: ["sw"], members: ["mac:aa"] }],
    segment_roster: [
      { name: "net-lan", cidr: "10.1.0.0/24", zone_type: null, observed_cidr: "10.80.20.0/24" },
      { name: "net-dmz", cidr: "10.1.1.0/24", zone_type: null, observed_cidr: "10.80.30.0/24" },
      { name: "net-ot", cidr: "10.1.2.0/24", zone_type: null, observed_cidr: "10.80.40.0/24" },
    ],
  };
  const SW_TE = `twin_config:
  id: corp-sw
  segments:
    - { name: net-lan, cidr: 10.1.0.0/24 }
    - { name: net-dmz, cidr: 10.1.1.0/24 }
    - { name: net-ot, cidr: 10.1.2.0/24 }
  nodes:
    - name: gw
      kind: router
      image: debian-12-cloud@v1
      nets:
        - { segment: net-lan }
        - { segment: net-dmz }
    - name: sw
      role: l2-switch
      image: debian-12-cloud@v1
      net: net-lan
`;

  it("observedSegments: the peers' segments by the roster's members and observed networks, less what nets[] names; nothing without a cast", () => {
    expect(observedSegments(EXPORT, ROSTER, "sw", ["net-lan"])).toEqual(["net-dmz", "net-ot"]);
    expect(observedSegments(EXPORT, ROSTER, "sw", ["net-lan", "net-dmz"])).toEqual(["net-ot"]);
    expect(observedSegments(EXPORT, ROSTER, "gw", [])).toEqual([]);
    expect(observedSegments(null, ROSTER, "sw", [])).toEqual([]);
    expect(observedSegments(EXPORT, null, "sw", [])).toEqual([]);
  });

  it("on the page: the switch's inspector reads 'observed on 2 segments' and Attach… writes them into nets[]; no leg was drawn for them before", async () => {
    const base = seeded();
    const { api } = withTwin("corp-sw", SW_TE, { twins: { ...base.state.twins, "corp-sw": twin("corp-sw", { hosts: 2, segments: 3, pit_id: "pit-1" }) }, exports: { ...base.state.exports, "corp-sw": EXPORT }, casts: { ...base.state.casts, "corp-sw": ROSTER } });
    renderAt(api, "/twins/corp-sw/canvas");
    await screen.findAllByTestId("canvas-host");
    expect(screen.getByTestId("state-pill").textContent).toContain("twin config"); // the design view, over the document
    fireEvent.click(host("sw"));
    const np = await screen.findByTestId("node-panel");
    const observed = within(np).getByTestId("observed-segments");
    expect(observed.textContent).toContain("observed on 2 segments it is not attached to: net-dmz, net-ot");
    fireEvent.click(within(observed).getByTestId("attach-observed"));
    await waitFor(() => expect(within(screen.getByTestId("node-panel")).getAllByTestId("attachment").map((a) => a.dataset["segment"])).toEqual(["net-lan", "net-dmz", "net-ot"]));
    expect(screen.queryByTestId("observed-segments")).toBeNull();
    // The document now carries them: a multi-homed host, drawn on its lowest bus.
    await waitFor(() => expect(host("sw").dataset["multi"]).toBe("true"));
  });
});

describe("rule 6: edit gestures follow the metaphor", () => {
  it("rehome moves one attachment to another bus and lets its address go; rankAt counts the lanes above a drop", () => {
    const doc = { id: "corp-big", teFile: "te.yaml", files: { "te.yaml": BIG_TE } };
    const model = readDoc(doc);
    expect(() => rehome(doc, model, "hq", "a6", "b2")).toThrow(/already on b2/);
    expect(() => rehome(doc, model, "hq", "zz", "b2")).toThrow(/not on zz/);
    expect(() => rehome(doc, model, "hq", "a6", "nope")).toThrow(/no segment named/);
    const sw = rehome(doc, model, "sw1", "a2", "a3");
    expect(readDoc({ ...doc, files: sw }).te.nodes.find((n) => n.name === "sw1")!.nets).toEqual([{ segment: "a3", ip: null }, { segment: "a5", ip: null }, { segment: "b1", ip: null }]);

    const { p, abs, node } = placed("corp-big", BIG_TE);
    const a3 = abs(node(ID.segment("a3")));
    const a1 = abs(node(ID.segment("a1")));
    expect(rankAt(p.nodes, ID.segment("a3"), a3.y + a3.h / 2)).toBe(2);
    expect(rankAt(p.nodes, ID.segment("a3"), a1.y - 5)).toBe(0);
    expect(rankAt(p.nodes, ID.segment("a3"), 1e6)).toBe(5);
    expect(rankAt(p.nodes, "nope", 0)).toBeNull();
    // A reorder re-ranks the whole site round the dropped lane: a1 dropped below a3 gives a2 0, a3 1, a1 2, the rest after.
    expect(reorderPins(p.nodes, ID.segment("a1"), a3.y + a3.h + 2)).toEqual({ "segment:10.70.2.0/24": { x: 0, y: 0 }, "segment:10.70.3.0/24": { x: 0, y: 1 }, "segment:10.70.1.0/24": { x: 0, y: 2 }, "segment:10.70.4.0/24": { x: 0, y: 3 }, "segment:10.70.5.0/24": { x: 0, y: 4 }, "segment:10.70.6.0/24": { x: 0, y: 5 } });
    expect(reorderPins(p.nodes, "nope", 0)).toBeNull();
  });

  it("'Add segment' lands in the selected bus's site: after the site's highest /24, not the first segment's", async () => {
    const { api } = withTwin("corp-two", TWO_SITES_TE);
    renderAt(api, "/twins/corp-two/canvas");
    await screen.findAllByTestId("canvas-host");
    expect(segments()).toEqual(["a", "b", "b2"]);
    fireEvent.click(segment("b2"));
    await screen.findByTestId("segment-panel");
    fireEvent.click(item("segment", "segment"));
    await waitFor(() => expect(segments()).toEqual(["a", "b", "b2", "segment-4"]));
    expect(segment("segment-4").dataset["cidr"]).toBe("10.2.2.0/24");
    // Under the 10.1 bus: the site of that segment.
    fireEvent.contextMenu(segment("a"));
    fireEvent.click(within(await screen.findByTestId("context-menu")).getByRole("button", { name: /Add a segment in this site/ }));
    await waitFor(() => expect(segments()).toEqual(["a", "b", "b2", "segment-4", "segment-5"]));
    expect(segment("segment-5").dataset["cidr"]).toBe("10.1.1.0/24");
  });

  it("a host icon dropped on a bus it is already on just takes the spot (no move), the attach gesture still lands on a bus", async () => {
    const { api } = withTwin("corp-big", BIG_TE);
    renderAt(api, "/twins/corp-big/canvas");
    await screen.findAllByTestId("canvas-host");
    // The rail's attach gesture: a host, then a bus.
    fireEvent.click(screen.getByTestId("rail-attach"));
    fireEvent.click(host("ws01"));
    fireEvent.click(segment("a3"));
    fireEvent.click(host("ws01"));
    const np = await screen.findByTestId("node-panel");
    await waitFor(() => expect(within(np).getAllByTestId("attachment").map((a) => a.dataset["segment"])).toEqual(["a1", "a3"]));
    // Multi-homed now: it moved to the lower bus of the two, a stub to a1.
    await waitFor(() => expect(host("ws01").dataset["multi"]).toBe("true"));
    commit(within(np).getByLabelText("Node name"), "ws-renamed");
    await waitFor(() => expect(host("ws-renamed")).toBeTruthy());
  });
});

describe("the badge and the labels button", () => {
  it("a count group's icon carries the one `×N` badge inside it; the labels button's icon changes with its mode and a caption shows under a selected icon", async () => {
    const { api } = withTwin("corp-big", BIG_TE);
    renderAt(api, "/twins/corp-big/canvas");
    await screen.findAllByTestId("canvas-host");
    const badge = within(host("web01")).getByTestId("count-badge");
    expect(badge.textContent).toBe("×2");
    expect(host("web01").textContent).toBe("×2"); // no caption by default
    const icon = () => screen.getByTestId("rail-labels").innerHTML;
    const selected = icon();
    fireEvent.click(host("ws01"));
    await waitFor(() => expect(host("ws01").textContent).toContain("ws01"));
    fireEvent.click(screen.getByTestId("rail-labels"));
    await waitFor(() => expect(screen.getByTestId("rail-labels").getAttribute("aria-label")).toBe("Labels: none"));
    const none = icon();
    fireEvent.click(screen.getByTestId("rail-labels"));
    await waitFor(() => expect(screen.getByTestId("rail-labels").getAttribute("aria-label")).toBe("Labels: all"));
    const all = icon();
    expect(new Set([selected, none, all]).size).toBe(3);
    expect(host("web01").textContent).toContain("web01");
  });

  it("the Hosts tab lists the switch as a host and centres on its icon", async () => {
    const { api } = withTwin("corp-big", BIG_TE);
    renderAt(api, "/twins/corp-big/canvas");
    await screen.findAllByTestId("canvas-host");
    fireEvent.click(screen.getByRole("tab", { name: /^Hosts/ }));
    const row = screen.getAllByTestId("hosts-row").find((r) => r.dataset["id"] === "sw1")!;
    expect(row.textContent).toContain("l2-switch");
    fireEvent.click(row);
    await waitFor(() => expect(host("sw1").className).toContain("selected"));
    void panel;
  });
});
