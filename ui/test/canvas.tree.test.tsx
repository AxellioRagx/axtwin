/**
 * The device tree (`docs/design/topology-layers.md` §7, track 5a, 2026-11-07):
 * a layered layout under the carrying device -- the Internet over the
 * gateway routers, switches by depth along their links, each segment a pill
 * under the deepest switch that carries it (else the router with a leg on
 * it), hosts as icons on the pill; a carrier folds past the rail's Fan-out;
 * one hue per router on its branch; links drawn between devices, dashed when
 * down or unconfirmed; provenance marks, and the overlay that shows them all.
 * The geometry is checked over the computed placement (jsdom draws no edge).
 */
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { HUE_GREY, hueOf } from "../src/features/edit-canvas/canvas/hues";
import { ID, type CanvasNode } from "../src/features/edit-canvas/canvas/placement";
import { TREE_ID, carriersOf, linkSegments, placeTree, pillSize, treeDepths } from "../src/features/edit-canvas/canvas/tree";
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
  it("pillSize: wide enough for its icon row and for its glyph row when compact", () => {
    expect(pillSize(2).w).toBeLessThan(pillSize(2, 4).w);
    expect(pillSize(12).rows).toBe(2);
  });
});

describe("placeTree on the switch-test shape", () => {
  it("draws the router, the switch as a device, the pills under their carriers, the hosts on the pills", () => {
    const p = placed("tree-test", TREE_TE);
    expect(p.overBudget).toBeNull();
    const types = new Map<string, number>();
    for (const n of p.nodes) types.set(n.type!, (types.get(n.type!) ?? 0) + 1);
    expect(types.get("router")).toBe(1);
    expect(types.get("switch")).toBe(1); // R3-7: its own node type
    expect(types.get("segment")).toBe(2);
    expect(types.get("host")).toBe(2);
    expect(types.get("site")).toBeUndefined();
    const office = p.byId.get(ID.segment("office"))!;
    const servers = p.byId.get(ID.segment("servers"))!;
    expect(office.data.kind === "segment" && office.data.carrier).toBe("access-sw"); // the trunk carries it
    expect(servers.data.kind === "segment" && servers.data.carrier).toBeNull(); // the down access link carries nothing
    // Ranks read top to bottom: router over the switch over its pill; the servers pill under the router.
    const gw = p.abs(p.byId.get(ID.router("gw"))!);
    const sw = p.abs(p.byId.get(TREE_ID.device("access-sw"))!);
    expect(gw.y).toBeLessThan(sw.y);
    expect(sw.y).toBeLessThan(p.abs(office).y);
    expect(gw.y).toBeLessThan(p.abs(servers).y);
    // A host chip sits inside its pill; the switch's badge is L2; provenance rides on the data.
    const ws = p.byId.get(ID.host("corp-workstation"))!;
    expect(ws.parentId).toBe(ID.segment("office"));
    const dev = p.byId.get(TREE_ID.device("access-sw"))!.data;
    expect(dev.kind === "device" && dev.badge).toBe("L2");
    expect(dev.kind === "device" && dev.source).toBe("asserted");
    expect(dev.kind === "device" && dev.ports).toEqual(["SFP1", "P24"]);
    expect(dev.kind === "device" && dev.segments).toEqual(["office"]);
    expect(office.data.kind === "segment" && office.data.source).toBe("discovered");
    expect(office.data.kind === "segment" && office.data.confidence).toBe(0.9);
  });
  it("draws the links between devices (and to a host), the router's legs and the carry branch, in the router's hue", () => {
    const p = placed("tree-test", TREE_TE);
    const links = p.edges.filter((e) => e.type === "link");
    expect(links.map((e) => e.id).sort()).toEqual(["link:0", "link:1"]);
    const trunk = links.find((e) => e.id === "link:0")!;
    expect([trunk.source, trunk.target]).toEqual([ID.router("gw"), TREE_ID.device("access-sw")]);
    expect(trunk.data?.kind === "link" && trunk.data.hue).toBe(hueOf(0));
    expect(trunk.data?.kind === "link" && trunk.data.label).toBe("eth1 — SFP1 · trunk · office");
    const down = links.find((e) => e.id === "link:1")!;
    expect([down.source, down.target]).toEqual([TREE_ID.device("access-sw"), ID.host("file-server")]);
    expect(down.data?.kind === "link" && down.data.state).toBe("down");
    expect(down.data?.kind === "link" && down.data.source).toBe("unconfirmed");
    const legs = p.edges.filter((e) => e.type === "leg" && e.data?.kind === "leg").map((e) => e.id).sort();
    expect(legs).toEqual(["leg:gw>office", "leg:gw>servers"]);
    const carry = p.edges.find((e) => e.id === TREE_ID.carry("access-sw", "office"))!;
    expect(carry.data?.kind === "carry" && carry.data.hue).toBe(hueOf(0));
    expect(p.edges.filter((e) => e.type === "arc")).toEqual([]);
  });
  it("a pin is an absolute top-left for a pill, a device and a host (epoch 6)", () => {
    const g = documentGraph(readDoc({ id: "t", teFile: "te.yaml", files: { "te.yaml": TREE_TE } }), "t");
    const graph = buildGraph(g.exportData, g.cast);
    const pinned = placeTree(graph, { "segment:10.71.10.0/24": { x: 900, y: 40 }, "host:access-sw": { x: 10, y: 20 } }, { tree: g.tree, extraLegs: g.extraLegs, uplinks: g.uplinks });
    expect(pinned.nodes.find((n) => n.id === ID.segment("office"))!.position).toEqual({ x: 900, y: 40 });
    expect(pinned.nodes.find((n) => n.id === TREE_ID.device("access-sw"))!.position).toEqual({ x: 10, y: 20 });
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
  it("chains the switches by depth, hangs each pill under the deepest carrier, folds past the fan, and colours the branch", () => {
    const p = placed("corp-tree", BIG_TE);
    const hq = p.abs(p.byId.get(ID.router("hq"))!);
    const core = p.abs(p.byId.get(TREE_ID.device("core"))!);
    const access = p.abs(p.byId.get(TREE_ID.device("access"))!);
    expect(hq.y).toBeLessThan(core.y);
    expect(core.y).toBeLessThan(access.y);
    // a1 and a2 are carried by `access` (deeper), a3..a6 by `core`, b1..b3 by the router alone.
    const carrier = (name: string) => {
      const d = p.byId.get(ID.segment(name))?.data;
      return d?.kind === "segment" ? d.carrier : "hidden";
    };
    expect(carrier("a1")).toBe("access");
    expect(carrier("a3")).toBe("core");
    expect(carrier("b1")).toBeNull();
    // The fold: `core` carries four (within the fan of 4), the router alone carries three: nothing folds at 4...
    expect(p.hiddenSegments).toBe(0);
    // ...and at a fan of 2 each carrier keeps two and folds the rest behind its own `+N more` pill.
    const folded = placed("corp-tree", BIG_TE, { fan: 2 });
    expect(folded.hiddenSegments).toBe(3); // a5, a6 under core; b3 under hq
    const over = folded.nodes.filter((n) => n.type === "overflow").map((n) => n.id).sort();
    expect(over).toEqual([TREE_ID.carrierOverflow("core"), TREE_ID.carrierOverflow("hq")]);
    expect(folded.anchorByName["a6"]).toBe(TREE_ID.carrierOverflow("core"));
    // The router's leg to a folded segment goes to the fold, once, however many fold there.
    expect(folded.edges.find((e) => e.id === `legs:hq>${TREE_ID.carrierOverflow("hq")}`)?.data).toMatchObject({ kind: "leg", folded: 1 });
    // Expanding the carrier lifts its fold.
    const open = placed("corp-tree", BIG_TE, { fan: 2, expanded: ["carrier:core"] });
    expect(open.hiddenSegments).toBe(1);
    // The branch wears the router's hue down to its pills.
    const a1 = p.byId.get(ID.segment("a1"))!.data;
    expect(a1.kind === "segment" && a1.hue).toBe(hueOf(0));
    const dev = p.byId.get(TREE_ID.device("access"))!.data;
    expect(dev.kind === "device" && dev.hue).toBe(hueOf(0));
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
    expect(segments()).toEqual(["office", "servers"]);
    expect(screen.getAllByTestId("canvas-segment").find((s) => s.dataset["name"] === "office")!.dataset["carrier"]).toBe("access-sw");
    expect(within(host("corp-workstation")).getByTestId("count-badge").textContent).toBe("×2·");
    // Overlay off: a dot on the asserted switch; nothing on a discovered (or unmarked, on a cast twin) item.
    expect(within(dev).getByTestId("prov-dot").dataset["source"]).toBe("asserted");
    expect(screen.queryAllByTestId("prov-badge")).toEqual([]);
    fireEvent.click(screen.getByTestId("rail-overlay"));
    await waitFor(() => expect(screen.getByTestId("canvas").dataset["overlay"]).toBe("on"));
    expect(within(dev).getByTestId("prov-badge").dataset["source"]).toBe("asserted");
    expect(screen.getAllByTestId("prov-badge").map((b) => b.dataset["source"]).sort()).toEqual(["asserted", "discovered", "discovered", "discovered", "discovered", "discovered"]); // R3-1: unmarked reads discovered on a cast twin
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
