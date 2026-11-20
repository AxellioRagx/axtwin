/**
 * Manual design, phase 1 (`SPEC.md` "Connect gestures", "Free layout"): what a handle drag makes
 * (router -> router a /30 transit with a leg each; router -> pane a new LAN), bulk wiring (chain,
 * ring, mesh, hub; a linked pair refused), and the free layout (positions kept, edges point to
 * point, no two transits on one path). jsdom draws no edge and runs no handle drag, so the gestures
 * are tested through the commands the canvas calls and the wiring dialog on the page.
 */
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { freeLayout, FREE_FAN } from "../src/features/edit-canvas/canvas/free";
import { placeTree } from "../src/features/edit-canvas/canvas/tree";
import { connectEndOf } from "../src/features/edit-canvas/Canvas";
import { nextFreeTransit, transitEnds } from "../src/features/edit-canvas/edit/allocate";
import { connectIntent, connectRouters, intentHint, routerToNewLan, wirePairs, wireRouters } from "../src/features/edit-canvas/edit/connect";
import { documentGraph, readDoc } from "../src/features/edit-canvas/edit/document";
import { readTe } from "../src/features/edit-canvas/edit/te";
import { buildGraph } from "../src/features/edit-canvas/model/graph";
import { check, seeded, twinYaml } from "./helpers/canvasFixtures";
import { renderAt } from "./helpers/render";

afterEach(() => localStorage.clear());

const routerNode = (name: string) => `  - name: ${name}\n    kind: router\n    image: ubuntu-server-22@v5\n    role: router\n`;
/** Three (or four) routers on an empty canvas, one LAN on the first so the twin has a segment. */
const TE = (routers: string[]) => `twin_config:\n  id: tri\n  segments:\n  - { name: lan, cidr: 10.1.0.0/24, zone_type: vlan }\n  nodes:\n${routers.map(routerNode).join("")}  - name: ws01\n    image: ubuntu-server-22@v5\n    net: lan\n`;
const doc = (te: string) => ({ id: "tri", teFile: "te.yaml", files: { "te.yaml": te } });

describe("the connect commands", () => {
  it("the transit pool: the next free /30 skips what is in play; its ends are .1 and .2", () => {
    expect(nextFreeTransit([])).toBe("10.254.0.0/30");
    expect(nextFreeTransit(["10.254.0.0/30", "10.254.0.4/30"])).toBe("10.254.0.8/30");
    expect(nextFreeTransit(["10.254.0.0/24"])).toBe("10.254.1.0/30");
    expect(transitEnds("10.254.0.4/30")).toEqual(["10.254.0.5", "10.254.0.6"]);
  });

  it("router -> router: one transit `<a>--<b>`, a /30, zone_type vlan, a leg for each at .1 and .2; a second drag between them is refused", () => {
    const d = doc(TE(["r1", "r2", "r3"]));
    const m = readDoc(d);
    const intent = connectIntent(m.te, { kind: "router", name: "r1" }, { kind: "router", name: "r2" });
    expect(intent).toEqual({ kind: "transit", a: "r1", b: "r2", cidr: "10.254.0.0/30", name: "r1--r2" });
    expect(intentHint(intent)).toBe("new transit r1--r2 10.254.0.0/30");
    const r = connectRouters(d, m, "r1", "r2");
    const te = readTe(r.files["te.yaml"]!);
    expect(te.segments.find((s) => s.name === "r1--r2")).toMatchObject({ cidr: "10.254.0.0/30", zoneType: "vlan", ospfArea: null });
    expect(te.nodes.find((n) => n.name === "r1")!.nets).toEqual([{ segment: "r1--r2", ip: "10.254.0.1" }]);
    expect(te.nodes.find((n) => n.name === "r2")!.nets).toEqual([{ segment: "r1--r2", ip: "10.254.0.2" }]);
    const again = connectIntent(readDoc({ ...d, files: r.files }).te, { kind: "router", name: "r2" }, { kind: "router", name: "r1" });
    expect(again).toEqual({ kind: "refused", reason: "r2 and r1 are already linked (they share a segment)" });
  });

  it("router -> empty pane: a new LAN segment (the next free /24) with the router's leg; router -> segment attaches; host -> host is refused", () => {
    const d = doc(TE(["r1", "r2"]));
    const m = readDoc(d);
    expect(connectIntent(m.te, { kind: "router", name: "r1" }, { kind: "pane" })).toEqual({ kind: "lan", router: "r1", cidr: "10.1.1.0/24", name: "segment-2" });
    const r = routerToNewLan(d, m, "r1");
    const te = readTe(r.files["te.yaml"]!);
    expect(te.segments.find((s) => s.name === "segment-2")!.cidr).toBe("10.1.1.0/24");
    expect(te.nodes.find((n) => n.name === "r1")!.nets.map((l) => l.segment)).toEqual(["segment-2"]);
    expect(connectIntent(m.te, { kind: "router", name: "r2" }, { kind: "segment", name: "lan" })).toEqual({ kind: "attach", node: "r2", segment: "lan" });
    expect(connectIntent(m.te, { kind: "host", name: "ws01" }, { kind: "host", name: "ws01" }).kind).toBe("refused");
    expect(connectEndOf({ data: { kind: "router", name: "r1" } } as never)).toEqual({ kind: "router", name: "r1" });
    expect(connectEndOf({ data: { kind: "segment", name: "lan" } } as never)).toEqual({ kind: "segment", name: "lan" });
  });

  it("bulk wiring: chain, ring, hub; a full mesh of 4 makes 6 transits; a pair already linked is left out, all linked is refused", () => {
    const four = ["a", "b", "c", "d"];
    expect(wirePairs(four, "chain")).toEqual([["a", "b"], ["b", "c"], ["c", "d"]]);
    expect(wirePairs(four, "ring")).toEqual([["a", "b"], ["b", "c"], ["c", "d"], ["d", "a"]]);
    expect(wirePairs(four, "hub")).toEqual([["a", "b"], ["a", "c"], ["a", "d"]]);
    const d = doc(TE(four));
    const mesh = wireRouters(d, readDoc(d), four, "mesh", { ospfArea: 0 });
    expect(mesh.made).toEqual(["a--b", "a--c", "a--d", "b--c", "b--d", "c--d"]);
    const te = readTe(mesh.files["te.yaml"]!);
    const transits = te.segments.filter((s) => s.name.includes("--"));
    expect(transits).toHaveLength(6);
    expect(new Set(transits.map((s) => s.cidr)).size).toBe(6);
    expect(transits.every((s) => s.ospfArea === 0)).toBe(true);
    // A ring over the mesh adds nothing: every pair is linked.
    const d2 = { ...d, files: mesh.files };
    expect(() => wireRouters(d2, readDoc(d2), four, "ring")).toThrow(/every pair is already linked/);
    // A chain where one pair is linked: that pair is left out and named.
    const one = connectRouters(d, readDoc(d), "a", "b");
    const d3 = { ...d, files: one.files };
    expect(wireRouters(d3, readDoc(d3), ["a", "b", "c"], "chain")).toMatchObject({ made: ["b--c"], skipped: ["a–b"] });
  });
});

describe("the free layout", () => {
  const triangle = () => {
    const d = doc(TE(["r1", "r2", "r3"]));
    const w = wireRouters(d, readDoc(d), ["r1", "r2", "r3"], "ring");
    return readDoc({ ...d, files: w.files });
  };
  const place = (m: ReturnType<typeof readDoc>, pins = {}) => {
    const g = documentGraph(m, "tri");
    return freeLayout(placeTree(buildGraph(g.exportData, g.cast), pins, { extraLegs: g.extraLegs, uplinks: g.uplinks, tree: g.tree, fan: FREE_FAN }));
  };

  it("keeps a dropped or dragged position exactly; every edge is point to point (no route), a router's leg leaving the router", () => {
    const m = triangle();
    const p = place(m, { "host:r2": { x: 640, y: 40 } });
    expect(p.nodes.find((n) => n.id === "router:r2")!.position).toEqual({ x: 640, y: 40 });
    expect(p.edges.every((e) => (e.data as { free?: boolean }).free === true && !(e.data as { route?: unknown }).route)).toBe(true);
    // A router's leg leaves the router; a host's access edge (the same `leg:` id shape) keeps its switch.
    for (const e of p.edges.filter((x) => x.id.startsWith("leg:"))) {
      const name = /^leg:([^>]+)>/.exec(e.id)![1]!;
      expect(e.source).toBe(p.nodes.some((n) => n.id === `router:${name}`) ? `router:${name}` : e.source);
      expect(p.nodes.some((n) => n.id === e.source)).toBe(true);
    }
  });

  it("a triangle's three transits are three edges between three different router pairs (no shared path), each with its pill", () => {
    const p = place(triangle());
    const seglinks = p.edges.filter((e) => e.data?.kind === "seglink");
    expect(seglinks.map((e) => (e.data as { segment: string }).segment).sort()).toEqual(["r1--r2", "r2--r3", "r3--r1"]);
    const ends = seglinks.map((e) => [e.source, e.target].sort().join("|"));
    expect(new Set(ends).size).toBe(3);
    expect(ends.every((x) => x.split("|").every((id) => id.startsWith("router:")))).toBe(true);
  });
});

describe("on the page", () => {
  function withTriangle() {
    const base = seeded();
    return seeded({
      twins: { ...base.state.twins, tri: { ...base.state.twins["corp-auth"]!, id: "tri" } },
      documents: { ...base.state.documents, tri: { id: "tri", te_file: "te.yaml", files: { "te.yaml": TE(["r1", "r2", "r3"]), "twin.yaml": twinYaml("tri") }, skipped: [] } },
      twinConfigs: [...base.state.twinConfigs, { id: "tri", description: null, cluster: null, parameterized: false, param_names: [], error: null }],
    });
  }
  const router = (name: string) => screen.getAllByTestId("canvas-router").find((r) => r.dataset["nodeId"] === name)!;

  it("3 routers, selected, wired as a ring from the menu: one undo step, three transits, and it passes Check (each has a role and an image)", async () => {
    const { api, state } = withTriangle();
    renderAt(api, "/twins/tri/canvas");
    await waitFor(() => expect(screen.getAllByTestId("canvas-router").length).toBe(3));
    expect(screen.getByTestId("layout-mode").dataset["mode"]).toBe("tree"); // a catalog twin opens in the tree
    fireEvent.click(router("r1"));
    fireEvent.click(router("r2"), { shiftKey: true });
    fireEvent.click(router("r3"), { shiftKey: true });
    fireEvent.contextMenu(router("r3"));
    fireEvent.click(within(await screen.findByTestId("context-menu")).getByText(/Connect 3 routers as/));
    const dlg = await screen.findByTestId("wire-dialog");
    fireEvent.click(within(dlg).getByLabelText("ring"));
    expect(dlg.textContent).toContain("r1–r2, r2–r3, r3–r1");
    fireEvent.click(within(dlg).getByRole("button", { name: "Connect" }));
    await waitFor(() => expect(screen.queryByTestId("wire-dialog")).toBeNull());
    expect(screen.getByTestId("exit-note").textContent).toContain("3 transits made");
    expect(screen.getByRole("button", { name: "Undo" }).getAttribute("title")).toBe("undo: connect 3 routers as ring");
    await check("valid");
    const te = readTe((state.writes.filter((w) => w.op === "validate").at(-1)!.body as { files: Record<string, string> }).files["te.yaml"]!);
    expect(te.segments.filter((s) => s.name.includes("--")).map((s) => s.cidr)).toEqual(["10.254.0.0/30", "10.254.0.4/30", "10.254.0.8/30"]);
    // One undo takes all three back.
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    await waitFor(() => expect(screen.queryByTestId("dirty-marker")).toBeNull());
  });

  it("the layout mode toggles, and the choice is saved with the layout", async () => {
    const { api, state } = withTriangle();
    renderAt(api, "/twins/tri/canvas");
    const modes = await screen.findByTestId("layout-mode");
    expect(modes.dataset["mode"]).toBe("tree");
    fireEvent.click(within(modes).getByRole("button", { name: "free" }));
    await waitFor(() => expect(screen.getByTestId("layout-mode").dataset["mode"]).toBe("free"));
    fireEvent.click(screen.getByRole("button", { name: "Save layout" }));
    await waitFor(() => expect(state.layouts["tri"]?.mode).toBe("free"));
  });
});
