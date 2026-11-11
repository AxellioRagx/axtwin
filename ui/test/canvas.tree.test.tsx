/**
 * The device tree (`docs/design/topology-layers.md` §7, track 5a, 2026-11-07;
 * segments drawn as switches, 2026-11-11): a layered layout under the carrying
 * device -- the Internet over the gateway routers, switches by depth along
 * their links, a segment's hosts as nodes of their own under the deepest
 * switch that carries it, else under the segment's IMPLIED switch (under the
 * router with a leg on it); no segment is a box; a carrier folds past the rail's Fan-out;
 * one hue per router on its branch; links drawn between devices, dashed when
 * down or unconfirmed; provenance marks, and the overlay that shows them all.
 * The geometry is checked over the computed placement (jsdom draws no edge).
 */
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { HUE_GREY, hueOf } from "../src/features/edit-canvas/canvas/hues";
import { ID, type CanvasNode } from "../src/features/edit-canvas/canvas/placement";
import { HUE_NEUTRAL, TREE_ID, boundaryRouter, carriersOf, hostsKey, linkSegments, placeTree, treeDepths } from "../src/features/edit-canvas/canvas/tree";
import { documentGraph, readDoc } from "../src/features/edit-canvas/edit/document";
import { readTe } from "../src/features/edit-canvas/edit/te";
import { validateTe } from "../src/features/edit-canvas/edit/validate";
import { buildGraph } from "../src/features/edit-canvas/model/graph";
import { host, hosts, seeded, segments, twinYaml } from "./helpers/canvasFixtures";
import { renderAt } from "./helpers/render";

/** switch-test's shape (the dev45 fixture): a router, an imageless switch on a
 *  trunk from it carrying `office`, a down access link to the file server,
 *  a ×2 workstation group with a member override, provenance on the switch. */
const TREE_TE = `twin_config:
  id: tree-test
  segments:
    - { name: office, cidr: 10.71.10.0/24, source: discovered, confidence: 0.9 }
    - { name: servers, cidr: 10.71.20.0/24 }
  nodes:
    - name: corp-workstation
      role: corp-workstation
      image: debian-12-cloud@v1
      count: 2
      net: office
      members:
        - { index: 2, role: jump-host, source: authored }
    - name: file-server
      role: file-server
      image: ubuntu-server-22@v5
      nets:
        - { segment: servers, ip: 10.71.20.10 }
    - name: access-sw
      kind: switch
      role: l2-switch
      source: asserted
    - name: gw
      kind: router
      image: debian-12-cloud@v1
      nets:
        - { segment: office }
        - { segment: servers }
  links:
    - a: { device: gw, port: eth1 }
      b: { device: access-sw, port: SFP1 }
      mode: trunk
      native: office
      vlans: [office]
      source: asserted
    - a: { device: access-sw, port: P24 }
      b: { device: file-server, port: eth0 }
      mode: access
      vlans: [servers]
      state: down
      source: unconfirmed
`;

/** A corp-shaped design: one router over two switches in a chain, nine segments, a group of three. */
const BIG_TE = `twin_config:
  id: corp-tree
  segments:
${["a1", "a2", "a3", "a4", "a5", "a6", "b1", "b2", "b3"].map((n, i) => `    - { name: ${n}, cidr: 10.${n.startsWith("a") ? 70 : 80}.${i + 1}.0/24 }`).join("\n")}
  nodes:
    - name: hq
      kind: router
      image: debian-12-cloud@v1
      nets:
${["a1", "a2", "a3", "a4", "a5", "a6", "b1", "b2", "b3"].map((n) => `        - { segment: ${n} }`).join("\n")}
    - { name: core, kind: switch, role: l2-switch }
    - { name: access, kind: switch, role: l2-switch }
    - { name: ws, role: corp-workstation, image: debian-12-cloud@v1, count: 3, net: a1 }
    - { name: srv, role: file-server, image: debian-12-cloud@v1, net: b1 }
  links:
    - { a: { device: hq, port: eth1 }, b: { device: core, port: SFP25 }, mode: trunk, vlans: [a1, a2, a3, a4, a5, a6] }
    - { a: { device: core, port: P1 }, b: { device: access, port: Po48 }, mode: trunk, vlans: [a1, a2] }
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

/** A design placed as the page would place it. */
function placed(id: string, te: string, options: Parameters<typeof placeTree>[2] = {}) {
  const g = documentGraph(readDoc({ id, teFile: "te.yaml", files: { "te.yaml": te } }), id);
  const graph = buildGraph(g.exportData, g.cast);
  const p = placeTree(graph, {}, { extraLegs: g.extraLegs, uplinks: g.uplinks, tree: g.tree, ...options });
  const byId = new Map(p.nodes.map((n) => [n.id, n]));
  const abs = (n: CanvasNode): { x: number; y: number } => {
    const parent = n.parentId ? byId.get(n.parentId) : null;
    const pa = parent ? abs(parent) : { x: 0, y: 0 };
    return { x: pa.x + n.position.x, y: pa.y + n.position.y };
  };
  return { ...p, byId, abs, tree: g.tree };
}

afterEach(() => {
  localStorage.clear();
  sessionStorage.clear();
});

describe("the twin config reader (te.ts) carries the device tree", () => {
  it("reads kind: switch, links[], members[] and source / confidence", () => {
    const te = readTe(TREE_TE);
    expect(te.problems).toEqual([]);
    const sw = te.nodes.find((n) => n.name === "access-sw")!;
    expect(sw.kind).toBe("switch");
    expect(sw.image).toBe("");
    expect(sw.source).toBe("asserted");
    const ws = te.nodes.find((n) => n.name === "corp-workstation")!;
    expect(ws.members).toEqual([{ index: 2, role: "jump-host", source: "authored" }]);
    expect(te.segments[0]!.source).toBe("discovered");
    expect(te.segments[0]!.confidence).toBe(0.9);
    expect(te.links.map((l) => [l.a.device, l.a.port, l.b.device, l.b.port, l.mode, l.state])).toEqual([
      ["gw", "eth1", "access-sw", "SFP1", "trunk", "up"],
      ["access-sw", "P24", "file-server", "eth0", "access", "down"],
    ]);
    expect(te.links[0]!.native).toBe("office");
    expect(te.links[1]!.source).toBe("unconfirmed");
  });
});

describe("the canvas's own checks know the device tree", () => {
  it("an imageless switch is valid; a parked one warns; a bad link, member or port errs with the rule", () => {
    expect(validateTe(readTe(TREE_TE)).filter((f) => f.severity === "error")).toEqual([]);
    const parked = readTe(TREE_TE.replace("  links:\n", "  links: []\n").split("    - a: { device: gw, port: eth1 }")[0]!);
    expect(validateTe(parked).map((f) => f.rule_id)).toContain("CV-048");
    const bad = readTe(TREE_TE
      .replace("        - { index: 2, role: jump-host, source: authored }", "        - { index: 3, role: jump-host }\n        - { index: 3, role: x }")
      .replace("    - a: { device: access-sw, port: P24 }", "    - a: { device: access-sw, port: SFP1 }")
      .replace("      vlans: [servers]\n      state: down", "      vlans: [servers, office]\n      state: down"));
    const ids = validateTe(bad).map((f) => f.rule_id);
    for (const r of ["CV-033", "CV-034", "CV-042", "CV-045"]) expect(ids).toContain(r);
  });
});

describe("the tree's pure rules", () => {
  it("linkSegments: native first, then the VLANs, no repeats", () => {
    expect(linkSegments({ native: "a", vlans: ["b", "a", "c"] })).toEqual(["a", "b", "c"]);
    expect(linkSegments({ native: null, vlans: ["b"] })).toEqual(["b"]);
  });
  it("treeDepths: routers at 0, switches by hops along up links, an unreached switch at 1 with no root", () => {
    const devices = [
      { name: "r", kind: "router" as const, role: null, image: null, legs: [] },
      { name: "s1", kind: "switch" as const, role: null, image: null, legs: [] },
      { name: "s2", kind: "switch" as const, role: null, image: null, legs: [] },
      { name: "lonely", kind: "switch" as const, role: null, image: null, legs: [] },
    ];
    const link = (a: string, b: string, state = "up") => ({ a: { device: a, port: "p" }, b: { device: b, port: "q" }, mode: "trunk", native: null, vlans: ["x"], state, source: null, index: 0 });
    const d = treeDepths(devices, [link("r", "s1"), link("s1", "s2"), link("s2", "lonely", "down")]);
    expect(d.get("r")).toEqual({ depth: 0, root: "r" });
    expect(d.get("s1")).toEqual({ depth: 1, root: "r" });
    expect(d.get("s2")).toEqual({ depth: 2, root: "r" });
    expect(d.get("lonely")).toEqual({ depth: 1, root: null });
  });
  it("carriersOf: the deepest switch whose link or port carries the segment; none when only a router has it", () => {
    const devices = [
      { name: "r", kind: "router" as const, role: null, image: null, legs: ["x", "y", "z"] },
      { name: "s1", kind: "switch" as const, role: null, image: null, legs: [] },
      { name: "s2", kind: "switch" as const, role: null, image: null, legs: ["z"] },
    ];
    const link = (a: string, b: string, vlans: string[]) => ({ a: { device: a, port: "p" }, b: { device: b, port: "q" }, mode: "trunk", native: null, vlans, state: "up", source: null, index: 0 });
    const links = [link("r", "s1", ["x", "y"]), link("s1", "s2", ["x"])];
    const depths = treeDepths(devices, links);
    const c = carriersOf(["x", "y", "z"], devices, links, depths);
    expect(c.get("x")).toBe("s2"); // carried by s1 and s2: the deeper wins
    expect(c.get("y")).toBe("s1");
    expect(c.get("z")).toBe("s2"); // a switch's own port
  });
  it("boundaryRouter: the router with a leg on every segment a boundary tap names; hostsKey: by the switch's node id", () => {
    const routers = [{ name: "a", segments: ["x"] }, { name: "b", segments: ["x", "y"] }];
    expect(boundaryRouter(routers, ["x", "y"])).toBe("b");
    expect(boundaryRouter(routers, ["x", "z"])).toBeNull();
    expect(boundaryRouter(routers, [])).toBeNull();
    expect(hostsKey(ID.segment("office"))).toBe("hosts:vsw:office");
    expect(hostsKey(TREE_ID.device("access-sw"))).toBe("hosts:device:access-sw");
  });
});

describe("placeTree on the switch-test shape", () => {
  it("draws the router, the real switch with its hosts, an implied switch for the switch-less segment, no segment box", () => {
    const p = placed("tree-test", TREE_TE);
    expect(p.overBudget).toBeNull();
    const types = new Map<string, number>();
    for (const n of p.nodes) types.set(n.type!, (types.get(n.type!) ?? 0) + 1);
    expect(types.get("router")).toBe(1);
    expect(types.get("switch")).toBe(1); // R3-7: its own node type
    expect(types.get("impliedSwitch")).toBe(1); // servers: no switch carries it (the access link is down)
    expect(types.get("segment")).toBeUndefined(); // no pill, ever
    expect(types.get("host")).toBe(2);
    expect(types.get("site")).toBeUndefined();
    // office: the trunk carries it, so access-sw stands for it and has no node of its own.
    expect(p.byId.has(ID.segment("office"))).toBe(false);
    expect(p.anchorByName["office"]).toBe(TREE_ID.device("access-sw"));
    const servers = p.byId.get(ID.segment("servers"))!;
    expect(servers.id).toBe("vsw:servers");
    expect(servers.data).toMatchObject({ kind: "segment", implied: true, name: "servers", cidr: "10.71.20.0/24", carrier: null });
    expect(p.anchorByName["servers"]).toBe("servers");
    // Ranks read top to bottom: router over the switch over its hosts; the implied switch under the router over its host.
    const gw = p.abs(p.byId.get(ID.router("gw"))!);
    const sw = p.abs(p.byId.get(TREE_ID.device("access-sw"))!);
    const ws = p.byId.get(ID.host("corp-workstation"))!;
    const fs = p.byId.get(ID.host("file-server"))!;
    expect(gw.y).toBeLessThan(sw.y);
    expect(sw.y).toBeLessThan(p.abs(ws).y);
    expect(gw.y).toBeLessThan(p.abs(servers).y);
    expect(p.abs(servers).y).toBeLessThan(p.abs(fs).y);
    // A host is a node of its own (no parent box); the switch's badge is L2; provenance rides on the data.
    expect(ws.parentId).toBeUndefined();
    expect(fs.parentId).toBeUndefined();
    const dev = p.byId.get(TREE_ID.device("access-sw"))!.data;
    expect(dev.kind === "device" && dev.badge).toBe("L2");
    expect(dev.kind === "device" && dev.source).toBe("asserted");
    expect(dev.kind === "device" && dev.ports).toEqual(["SFP1", "P24"]);
    expect(dev.kind === "device" && dev.segments).toEqual(["office"]);
  });
  it("draws the links between devices (and to a host), the router's legs and the carry branch; one router is neutral (R3-5)", () => {
    const p = placed("tree-test", TREE_TE);
    const links = p.edges.filter((e) => e.type === "link");
    expect(links.map((e) => e.id).sort()).toEqual(["link:0", "link:1"]);
    const trunk = links.find((e) => e.id === "link:0")!;
    expect([trunk.source, trunk.target]).toEqual([ID.router("gw"), TREE_ID.device("access-sw")]);
    expect(trunk.data?.kind === "link" && trunk.data.hue).toBe(HUE_NEUTRAL);
    expect(trunk.data?.kind === "link" && trunk.data.label).toBe("eth1 — SFP1 · trunk · office");
    const down = links.find((e) => e.id === "link:1")!;
    expect([down.source, down.target]).toEqual([TREE_ID.device("access-sw"), ID.host("file-server")]);
    expect(down.data?.kind === "link" && down.data.state).toBe("down");
    expect(down.data?.kind === "link" && down.data.source).toBe("unconfirmed");
    const legs = p.edges.filter((e) => e.type === "leg" && e.data?.kind === "leg").map((e) => e.id).sort();
    // office is reached through gw's own trunk to access-sw: no leg of gw's own to it (R3-4, it crossed the trunk).
    expect(legs).toEqual(["leg:corp-workstation>office", "leg:file-server>servers", "leg:gw>servers"]);
    expect(p.edges.find((e) => e.id === "leg:gw>servers")).toMatchObject({ source: ID.router("gw"), target: ID.segment("servers") });
    // The access edges (rule 1): the real switch to its host, labelled with the VLAN; the implied switch to its host.
    const access = p.edges.find((e) => e.id === TREE_ID.access("corp-workstation", "office"))!;
    expect([access.source, access.target]).toEqual([TREE_ID.device("access-sw"), ID.host("corp-workstation")]);
    expect(access.data).toMatchObject({ kind: "leg", segment: "office", vlan: "office", hue: HUE_NEUTRAL });
    const fsLeg = p.edges.find((e) => e.id === TREE_ID.access("file-server", "servers"))!;
    expect([fsLeg.source, fsLeg.target]).toEqual([ID.segment("servers"), ID.host("file-server")]);
    expect(fsLeg.data && "vlan" in fsLeg.data).toBe(false); // the implied switch IS the segment: no label needed
    expect(p.edges.filter((e) => e.type === "arc")).toEqual([]);
  });
  it("a pin is an absolute top-left for an implied switch, a device and a host (epoch 7)", () => {
    const g = documentGraph(readDoc({ id: "t", teFile: "te.yaml", files: { "te.yaml": TREE_TE } }), "t");
    const graph = buildGraph(g.exportData, g.cast);
    const pinned = placeTree(graph, { "segment:10.71.20.0/24": { x: 900, y: 40 }, "host:access-sw": { x: 10, y: 20 }, "host:file-server": { x: 600, y: 500 } }, { tree: g.tree, extraLegs: g.extraLegs, uplinks: g.uplinks });
    expect(pinned.nodes.find((n) => n.id === ID.segment("servers"))!.position).toEqual({ x: 900, y: 40 });
    expect(pinned.nodes.find((n) => n.id === TREE_ID.device("access-sw"))!.position).toEqual({ x: 10, y: 20 });
    expect(pinned.nodes.find((n) => n.id === ID.host("file-server"))!.position).toEqual({ x: 600, y: 500 });
  });
});

describe("the tree's box budget", () => {
  it("falls back past 120 drawn boxes (devices never fold), with the reason", () => {
    const names = Array.from({ length: 125 }, (_, i) => `sw${i}`);
    const te = `twin_config:\n  id: huge\n  segments:\n    - { name: a, cidr: 10.0.0.0/24 }\n  nodes:\n    - { name: gw, kind: router, image: img@v1, net: a }\n${names.map((n) => `    - { name: ${n}, kind: switch, net: a }`).join("\n")}\n`;
    const p = placed("huge", te);
    expect(p.overBudget).toMatch(/boxes exceed the canvas budget of 120/);
    expect(p.nodes).toEqual([]);
  });
});

describe("placeTree on a corp-shaped design", () => {
  it("chains the switches by depth, hangs each segment's hosts off the deepest carrier, folds the implied switches past the fan, and colours the branch", () => {
    const p = placed("corp-tree", BIG_TE);
    const hq = p.abs(p.byId.get(ID.router("hq"))!);
    const core = p.abs(p.byId.get(TREE_ID.device("core"))!);
    const access = p.abs(p.byId.get(TREE_ID.device("access"))!);
    expect(hq.y).toBeLessThan(core.y);
    expect(core.y).toBeLessThan(access.y);
    // a1 and a2 are carried by `access` (deeper), a3..a6 by `core`: no node of their own. b1..b3 by the
    // router alone: each an implied switch.
    expect(p.anchorByName["a1"]).toBe(TREE_ID.device("access"));
    expect(p.anchorByName["a3"]).toBe(TREE_ID.device("core"));
    expect(p.byId.has(ID.segment("a1"))).toBe(false);
    expect(p.byId.get(ID.segment("b1"))?.type).toBe("impliedSwitch");
    // The ×3 group on a1 hangs off `access`, the deepest carrier.
    const ws = p.edges.find((e) => e.id === TREE_ID.access("ws", "a1"))!;
    expect([ws.source, ws.target]).toEqual([TREE_ID.device("access"), ID.host("ws")]);
    expect(access.y).toBeLessThan(p.abs(p.byId.get(ID.host("ws"))!).y);
    // The fold: the router's rail holds three implied switches (within the fan of 4): nothing folds at 4...
    expect(p.hiddenSegments).toBe(0);
    // ...and at a fan of 2 the rail keeps two and folds the rest behind its `+N more`. A real switch
    // carries its segments itself and never folds (a3..a6 stay with `core`).
    const folded = placed("corp-tree", BIG_TE, { fan: 2 });
    expect(folded.hiddenSegments).toBe(1); // b3 under hq
    const over = folded.nodes.filter((n) => n.type === "overflow").map((n) => n.id).sort();
    expect(over).toEqual([TREE_ID.carrierOverflow("hq")]);
    expect(folded.anchorByName["b3"]).toBe(TREE_ID.carrierOverflow("hq"));
    expect(folded.anchorByName["a6"]).toBe(TREE_ID.device("core"));
    // The router's leg to a folded segment goes to the fold, once, however many fold there.
    expect(folded.edges.find((e) => e.id === `legs:hq>${TREE_ID.carrierOverflow("hq")}`)?.data).toMatchObject({ kind: "leg", folded: 1 });
    // Expanding the carrier lifts its fold.
    const open = placed("corp-tree", BIG_TE, { fan: 2, expanded: ["carrier:hq"] });
    expect(open.hiddenSegments).toBe(0);
    // One router: the branch is neutral down to its switches (R3-5).
    const b1 = p.byId.get(ID.segment("b1"))!.data;
    expect(b1.kind === "segment" && b1.hue).toBe(HUE_NEUTRAL);
    const dev = p.byId.get(TREE_ID.device("access"))!.data;
    expect(dev.kind === "device" && dev.hue).toBe(HUE_NEUTRAL);
    expect(HUE_GREY).not.toBe(hueOf(0));
  });
});

describe("on the page", () => {
  it("a hand-authored twin has no provenance overlay toggle (R3-1)", async () => {
    const { api } = withTwin("tree-test", TREE_TE);
    renderAt(api, "/twins/tree-test/canvas");
    await screen.findAllByTestId("canvas-host");
    expect(screen.queryByTestId("rail-overlay")).toBeNull();
    fireEvent.keyDown(window, { key: "o" });
    expect(screen.getByTestId("canvas").dataset["overlay"]).toBe("off");
  });
  it("the design renders the switch as a device with its badge, the pills, the ×N badge, and the link; the overlay marks provenance", async () => {
    const base = seeded();
    const { api } = withTwin("tree-test", TREE_TE, { twins: { ...base.state.twins, "tree-test": { ...base.state.twins["corp-auth"]!, id: "tree-test", pit_id: "pit-tree" } } });
    renderAt(api, "/twins/tree-test/canvas");
    await screen.findAllByTestId("canvas-host");
    expect(screen.getAllByTestId("canvas-router").map((r) => r.dataset["nodeId"])).toEqual(["gw"]);
    const dev = screen.getByTestId("canvas-device");
    expect(dev.dataset["nodeId"]).toBe("access-sw");
    expect(within(dev).getByTestId("device-badge").textContent).toBe("L2");
    expect(hosts()).toEqual(["corp-workstation", "file-server"]);
    // Only the switch-less segment draws, as its implied switch; office is access-sw's.
    expect(segments()).toEqual(["servers"]);
    expect(screen.getByTestId("canvas-segment").dataset["implied"]).toBe("true");
    expect(within(screen.getByTestId("canvas-segment")).getByTestId("implied-mark")).toBeTruthy();
    expect(within(host("corp-workstation")).getByTestId("count-badge").textContent).toBe("×2·");
    // Overlay off: a dot on the asserted switch; nothing on a discovered (or unmarked, on a cast twin) item.
    expect(within(dev).getByTestId("prov-dot").dataset["source"]).toBe("asserted");
    expect(screen.queryAllByTestId("prov-badge")).toEqual([]);
    fireEvent.click(screen.getByTestId("rail-overlay"));
    await waitFor(() => expect(screen.getByTestId("canvas").dataset["overlay"]).toBe("on"));
    expect(within(dev).getByTestId("prov-badge").dataset["source"]).toBe("asserted");
    expect(screen.getAllByTestId("prov-badge").map((b) => b.dataset["source"]).sort()).toEqual(["asserted", "discovered", "discovered", "discovered", "discovered"]); // R3-1: unmarked reads discovered on a cast twin (gw, two hosts, servers)
    fireEvent.keyDown(window, { key: "o" });
    await waitFor(() => expect(screen.getByTestId("canvas").dataset["overlay"]).toBe("off"));
  });
  it("clicking the switch opens its node panel; the hover card names its ports", async () => {
    const { api } = withTwin("tree-test", TREE_TE);
    renderAt(api, "/twins/tree-test/canvas");
    await screen.findAllByTestId("canvas-host");
    fireEvent.click(screen.getByTestId("canvas-device"));
    const np = await screen.findByTestId("node-panel");
    expect(np.textContent).toContain("access-sw");
  });
});

/** corp-twin's shape in small (R4-2b / R4-8, 2026-11-08): one router with legs on several sites,
 *  and bridging hosts (`l2-switch` hosts with legs on two segments) that carry 10.180, 10.199 and
 *  10.192 segments the router has no leg on. */
const RAIL_TE = `twin_config:
  id: rail
  segments:
    - { name: s100, cidr: 10.100.21.0/24 }
    - { name: s120a, cidr: 10.120.1.0/24 }
    - { name: s120b, cidr: 10.120.2.0/24 }
    - { name: s180a, cidr: 10.180.0.0/20 }
    - { name: s180b, cidr: 10.180.88.0/24 }
    - { name: s192, cidr: 10.192.168.0/24 }
    - { name: s199a, cidr: 10.199.0.0/29 }
    - { name: s199b, cidr: 10.199.0.8/29 }
  nodes:
    - name: hq
      kind: router
      image: debian-12-cloud@v1
      nets: [{ segment: s100 }, { segment: s120a }, { segment: s120b }, { segment: s180b }]
    - { name: ws100, role: corp-workstation, image: debian-12-cloud@v1, net: s100 }
    - { name: ws120a, role: corp-workstation, image: debian-12-cloud@v1, net: s120a }
    - { name: ws120b, role: corp-workstation, image: debian-12-cloud@v1, net: s120b }
    - { name: ws180a, role: corp-workstation, image: debian-12-cloud@v1, net: s180a }
    - name: mx-sw
      role: l2-switch
      image: debian-12-cloud@v1
      nets: [{ segment: s180b }, { segment: s180a }, { segment: s199a }]
    - name: ms120
      role: l2-switch
      image: debian-12-cloud@v1
      nets: [{ segment: s199a }, { segment: s199b }]
    - name: labswitch
      role: l2-switch
      image: debian-12-cloud@v1
      nets: [{ segment: s199b }, { segment: s192 }]
`;

describe("a router's rail (R4-2b) and an open site's row (R4-8)", () => {
  const box = (p: ReturnType<typeof placed>, id: string) => {
    const n = p.byId.get(id);
    if (!n) throw new Error(`no node ${id}`);
    return { ...p.abs(n), w: n.width ?? 0, h: n.height ?? 0 };
  };
  it("every site and segment the router serves, or a bridging host carries, sits on its rail in one row, site then CIDR, and no parent", () => {
    const p = placed("rail", RAIL_TE);
    expect(p.overBudget).toBeNull();
    // Nothing is "not carried": the bridged segments joined the router's rail with their sites.
    expect(p.nodes.some((n) => n.data.kind === "uncarried")).toBe(false);
    const rail = [ID.segment("s100"), TREE_ID.siteFold("hq", "10.120"), TREE_ID.siteFold("hq", "10.180"), ID.segment("s192"), TREE_ID.siteFold("hq", "10.199")];
    expect(p.rows?.[ID.router("hq")]).toEqual(rail);
    const boxes = rail.map((id) => box(p, id));
    expect(new Set(boxes.map((b) => Math.round(b.y + b.h / 2))).size).toBe(1);
    const xs = boxes.map((b) => b.x);
    expect([...xs].sort((a, b) => a - b)).toEqual(xs);
    // Two loose pills are within the budget: nothing overflows.
    expect(p.byId.has(TREE_ID.carrierOverflow("hq"))).toBe(false);
    expect(p.anchorByName["s199a"]).toBe(TREE_ID.siteFold("hq", "10.199"));
  });
  it("past the budget, only loose segments fold behind +N more, last on the rail and a leaf; every site stays", () => {
    const p = placed("rail", RAIL_TE, { fan: 1 });
    const rail = [ID.segment("s100"), TREE_ID.siteFold("hq", "10.120"), TREE_ID.siteFold("hq", "10.180"), TREE_ID.siteFold("hq", "10.199"), TREE_ID.carrierOverflow("hq")];
    expect(p.rows?.[ID.router("hq")]).toEqual(rail);
    const xs = rail.map((id) => box(p, id).x);
    expect([...xs].sort((a, b) => a - b)).toEqual(xs);
    expect(p.anchorByName["s192"]).toBe(TREE_ID.carrierOverflow("hq"));
    expect(p.byId.get(TREE_ID.carrierOverflow("hq"))?.data).toMatchObject({ hidden: 1 });
    expect(p.hiddenSegments).toBe(1);
    // The overflow is a leaf: nothing hangs below it (hosts hang below their switches, not under it).
    const over = box(p, TREE_ID.carrierOverflow("hq"));
    expect(p.nodes.filter((n) => !n.parentId && p.abs(n).y > over.y + over.h && p.abs(n).x < over.x + over.w && p.abs(n).x + (n.width ?? 0) > over.x)).toEqual([]);
    expect(p.edges.filter((e) => e.source === TREE_ID.carrierOverflow("hq"))).toEqual([]);
  });
  it("opened, the carrier's items stay one row with the collapse tile last, folding back what +N more held; a fold of bridged segments hangs from the router, dashed, naming the bridge", () => {
    const p = placed("rail", RAIL_TE, { fan: 1, expanded: ["carrier:hq"] });
    const tile = p.nodes.find((n) => n.data.kind === "overflow" && n.data.siteKey === "carrier:hq")!;
    expect(tile.data).toMatchObject({ expanded: true, hidden: 1 });
    const rail = [ID.segment("s100"), TREE_ID.siteFold("hq", "10.120"), TREE_ID.siteFold("hq", "10.180"), ID.segment("s192"), TREE_ID.siteFold("hq", "10.199"), tile.id];
    expect(p.rows?.[ID.router("hq")]).toEqual(rail);
    const xs = rail.map((id) => box(p, id).x);
    expect([...xs].sort((a, b) => a - b)).toEqual(xs);
    expect(new Set(rail.map((id) => Math.round(box(p, id).y + box(p, id).h / 2))).size).toBe(1);
    const bridge = p.edges.find((e) => e.target === TREE_ID.siteFold("hq", "10.199"));
    expect(bridge).toMatchObject({ source: ID.router("hq"), data: { kind: "carry", dashed: true } });
    expect((bridge!.data as { label?: string }).label).toMatch(/^via /);
  });
  it("an open site is one tile on the rail; its segments fan in their own row below it, CIDR order, legs drawn from the tile, never chained through a pill", () => {
    const p = placed("rail", RAIL_TE, { expanded: ["carrier:hq", "site:hq|10.180"] });
    const tile = p.nodes.find((n) => n.data.kind === "overflow" && n.data.siteKey === "site:hq|10.180")!;
    const t = box(p, tile.id);
    const a = box(p, ID.segment("s180a"));
    const b = box(p, ID.segment("s180b"));
    expect(Math.round(a.y)).toBe(Math.round(b.y));
    expect(a.y).toBeGreaterThan(t.y + t.h);
    expect(a.x).toBeLessThan(b.x);
    // The router's real leg to s180b is drawn from the tile; the bridged s180a hangs from the tile, dashed.
    expect(p.edges.find((e) => e.id === "leg:hq>s180b")?.source).toBe(tile.id);
    expect(p.edges.find((e) => e.target === ID.segment("s180a") && e.source === tile.id)?.data).toMatchObject({ dashed: true });
    // No pill is the parent of another pill.
    const pills = new Set(p.nodes.filter((n) => n.type === "segment").map((n) => n.id));
    expect(p.edges.filter((e) => pills.has(e.source) && pills.has(e.target) && e.data?.kind !== "stub")).toEqual([]);
  });
});
