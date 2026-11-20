/**
 * Manual design, phase 1 (`SPEC.md` "Connect gestures", "Free layout"): what a handle drag makes
 * (router -> router a /30 transit with a leg each; router -> pane a new LAN), bulk wiring (chain,
 * ring, mesh, hub; a linked pair refused), and the free layout (positions kept, edges point to
 * point, no two transits on one path). jsdom draws no edge and runs no handle drag, so the gestures
 * are tested through the commands the canvas calls and the wiring dialog on the page.
 */
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { alongLine, freeEdgeEnds, freeLayout, FREE_FAN, ICON_BOX, placePills, type PillAt } from "../src/features/edit-canvas/canvas/free";
import { TRANSIT_HUE } from "../src/features/edit-canvas/canvas/hues";
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
    expect(seglinks.map((e) => (e.data as { segment: string }).segment).sort()).toEqual(["r1--r2", "r1--r3", "r2--r3"]);
    const ends = seglinks.map((e) => [e.source, e.target].sort().join("|"));
    expect(new Set(ends).size).toBe(3);
    expect(ends.every((x) => x.split("|").every((id) => id.startsWith("router:")))).toBe(true);
  });
});

describe("free layout: point to point between icons (2026-11-20, round 2)", () => {
  const PINS = { "host:r1": { x: 0, y: 0 }, "host:r2": { x: 420, y: 0 }, "host:r3": { x: 210, y: 320 } };
  /** A triangle built by three drags, each `[from, to]`. */
  const built = (drags: Array<[string, string]>) => {
    let d: { id: string; teFile: string; files: Record<string, string> } = doc(TE(["r1", "r2", "r3"]));
    for (const [a, b] of drags) d = { ...d, files: connectRouters(d, readDoc(d), a, b).files };
    const m = readDoc(d);
    const g = documentGraph(m, "tri");
    return freeLayout(placeTree(buildGraph(g.exportData, g.cast), PINS, { extraLegs: g.extraLegs, uplinks: g.uplinks, tree: g.tree, fan: FREE_FAN }));
  };
  /** Each transit's drawing, by segment: its two ends (canonical order) and its pill's spot. */
  const drawing = (p: ReturnType<typeof built>) => {
    const ends = freeEdgeEnds(p);
    return Object.fromEntries(
      p.edges.filter((e) => e.data?.kind === "seglink").map((e) => {
        const d = e.data as { segment: string; pill?: PillAt };
        const x = ends.get(e.id)!;
        return [d.segment, { p: x.p, q: x.q, pill: d.pill }];
      }),
    );
  };
  const iconCentre = (p: ReturnType<typeof built>, name: string) => {
    const n = p.nodes.find((x) => x.id === `router:${name}`)!;
    return { x: n.position.x + n.width! / 2, y: n.position.y + n.height! - ICON_BOX / 2 };
  };

  it("in a 3-router triangle every edge end lies on its router icon's outline (within 2 px), and the drawing is the same whichever way each link was dragged", () => {
    const forward = built([["r1", "r2"], ["r2", "r3"], ["r3", "r1"]]);
    const backward = built([["r2", "r1"], ["r3", "r2"], ["r1", "r3"]]);
    const f = drawing(forward);
    expect(Object.keys(f).sort()).toEqual(["r1--r2", "r1--r3", "r2--r3"]);
    for (const [seg, x] of Object.entries(f)) {
      const [a, b] = seg.split("--");
      // Each end on one of the two routers' circles (r = 17), the two ends on different routers.
      const on = (pt: { x: number; y: number }, r: string) => Math.abs(Math.hypot(pt.x - iconCentre(forward, r).x, pt.y - iconCentre(forward, r).y) - ICON_BOX / 2) <= 2;
      expect((on(x.p, a!) && on(x.q, b!)) || (on(x.p, b!) && on(x.q, a!))).toBe(true);
      // The pill on its own line: at most ±30% from the midpoint.
      expect(Math.abs((x.pill?.t ?? 0.5) - 0.5)).toBeLessThanOrEqual(0.3 + 1e-9);
    }
    expect(drawing(backward)).toEqual(f);
  });

  it("a transit is named for its two routers sorted, whichever way it was dragged", () => {
    const d = doc(TE(["router-1", "router-2"]));
    expect(connectIntent(readDoc(d).te, { kind: "router", name: "router-2" }, { kind: "router", name: "router-1" })).toMatchObject({ kind: "transit", name: "router-1--router-2" });
  });

  it("every transit wears the one transit colour, in the free layout and the tree", () => {
    const p = built([["r1", "r2"], ["r2", "r3"], ["r3", "r1"]]);
    const seglinks = p.edges.filter((e) => e.data?.kind === "seglink");
    expect(seglinks.map((e) => (e.data as { hue: string }).hue)).toEqual([TRANSIT_HUE, TRANSIT_HUE, TRANSIT_HUE]);
  });

  it("a pill that would cover a name moves along its line (never onto the name)", () => {
    const line = { id: "a", p: { x: 0, y: 0 }, q: { x: 400, y: 0 }, text: "r1--r2 · 10.254.0.0/30" };
    const w = line.text.length * 6.3 + 12;
    const clear = (at: PillAt, name: { l: number; r: number; t: number; b: number }) => {
      const c = alongLine(line.p, line.q, at);
      return c.x + w / 2 < name.l || c.x - w / 2 > name.r || c.y - 9 > name.b || c.y + 9 < name.t;
    };
    const name = { l: 170, r: 230, t: -10, b: 10 };
    const at = placePills([line], [name], []).get("a")!;
    expect(at).toMatchObject({ off: 0 });
    expect(at.t).not.toBe(0.5);
    expect(Math.abs(at.t - 0.5)).toBeLessThanOrEqual(0.3);
    expect(clear(at, name)).toBe(true);
    // A name too wide to clear along the line: the pill steps off it, still never on the name.
    const wide = { l: 60, r: 340, t: -10, b: 10 };
    const off = placePills([line], [wide], []).get("a")!;
    expect(off.off).not.toBe(0);
    expect(clear(off, wide)).toBe(true);
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

  /** A press on `from`, a move and a release on `to` (the canvas's connect drag runs on pointer events). */
  const drag = (from: Element, to: Element) => {
    const o = { button: 0, pointerId: 1, isPrimary: true, pointerType: "mouse" };
    fireEvent.pointerDown(from, { ...o, clientX: 10, clientY: 10 });
    fireEvent.pointerMove(to, { ...o, clientX: 220, clientY: 140 });
    fireEvent.pointerUp(to, { ...o, clientX: 220, clientY: 140 });
  };
  const wrapper = (name: string) => router(name).closest(".react-flow__node")!;
  const undoTitle = () => screen.getByRole("button", { name: "Undo" }).getAttribute("title");

  it("connect zones: every router wears a ring that says 'drag to connect'; a drag from the ring makes a transit", async () => {
    const { api } = withTriangle();
    renderAt(api, "/twins/tri/canvas");
    await waitFor(() => expect(screen.getAllByTestId("canvas-router").length).toBe(3));
    const ring = within(router("r1")).getByTestId("connect-ring");
    expect(ring.getAttribute("title")).toBe("drag to connect");
    expect(ring.dataset["connectZone"]).toBe("ring");
    expect(router("r1").querySelectorAll(".cv-connect-ring .cv-connect-dot")).toHaveLength(4);
    drag(ring, router("r2"));
    await waitFor(() => expect(undoTitle()).toBe("undo: connect r1 — r2 (transit 10.254.0.0/30)"));
  });

  it("a drag from the icon moves the node: it draws no link, and the node stays draggable", async () => {
    const { api } = withTriangle();
    renderAt(api, "/twins/tri/canvas");
    await waitFor(() => expect(screen.getAllByTestId("canvas-router").length).toBe(3));
    const before = undoTitle();
    expect(wrapper("r1").className).toContain("draggable");
    drag(router("r1").querySelector(".cv-icon-box")!, router("r2"));
    expect(screen.queryByTestId("connect-hint")).toBeNull();
    expect(undoTitle()).toBe(before);
  });

  it("the Connect tool (C): a drag from anywhere on a device, the icon included, makes a transit; nodes do not move; Esc returns to Select", async () => {
    const { api } = withTriangle();
    renderAt(api, "/twins/tri/canvas");
    await waitFor(() => expect(screen.getAllByTestId("canvas-router").length).toBe(3));
    fireEvent.keyDown(window, { key: "c" });
    expect(screen.getByTestId("canvas").dataset["tool"]).toBe("connect");
    expect(screen.getByTestId("rail-connect").getAttribute("aria-pressed")).toBe("true");
    expect(wrapper("r1").className).not.toContain("draggable");
    drag(router("r1").querySelector(".cv-icon-box")!, router("r3"));
    await waitFor(() => expect(undoTitle()).toBe("undo: connect r1 — r3 (transit 10.254.0.0/30)"));
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.getByTestId("canvas").dataset["tool"]).toBe("select");
    fireEvent.click(screen.getByTestId("rail-connect"));
    expect(screen.getByTestId("canvas").dataset["tool"]).toBe("connect");
    fireEvent.keyDown(window, { key: "v" });
    expect(screen.getByTestId("canvas").dataset["tool"]).toBe("select");
  });

  it("a drag from a router's ring onto a segment's switch attaches the router there", async () => {
    const { api } = withTriangle();
    renderAt(api, "/twins/tri/canvas");
    await waitFor(() => expect(screen.getAllByTestId("canvas-router").length).toBe(3));
    const lan = screen.getAllByTestId("canvas-segment").find((x) => x.dataset["name"] === "lan")!;
    drag(within(router("r2")).getByTestId("connect-ring"), lan);
    await waitFor(() => expect(undoTitle()).toBe("undo: attach r2 to lan"));
  });

  it("OSPF needs a VyOS router: ticked, the dialog names each router not on the router-ospf image and switches them (checked by default) in the same undo step; what it cannot write is said in one line", async () => {
    const base = withTriangle();
    base.state.imageProfiles = [...base.state.imageProfiles.filter((p) => p.name !== "router-ospf"), { name: "router-ospf", image: "vyos@1.5" }];
    const { api, state } = base;
    renderAt(api, "/twins/tri/canvas");
    await waitFor(() => expect(screen.getAllByTestId("canvas-router").length).toBe(3));
    fireEvent.click(router("r1"));
    fireEvent.click(router("r2"), { shiftKey: true });
    fireEvent.contextMenu(router("r2"));
    fireEvent.click(within(await screen.findByTestId("context-menu")).getByText(/Connect 2 routers as/));
    const dlg = within(await screen.findByTestId("wire-dialog"));
    expect(dlg.queryByTestId("ospf-image")).toBeNull();
    fireEvent.click(dlg.getByLabelText(/run OSPF on these links/));
    expect(dlg.getAllByTestId("ospf-image-gap").map((x) => x.textContent)).toEqual([
      "r1 runs ubuntu-server-22@v5; OSPF needs vyos@1.5 (as datacenter's fabric)",
      "r2 runs ubuntu-server-22@v5; OSPF needs vyos@1.5 (as datacenter's fabric)",
    ]);
    const sw = dlg.getByLabelText("switch these routers to vyos@1.5") as HTMLInputElement;
    expect(sw.checked).toBe(true);
    expect(dlg.getByTestId("ospf-note").textContent).toBe("Not written here: each router's routing: block (region, role, loopback) and a fabric: region with igp: ospf; without them OSPF does not come up.");
    fireEvent.click(dlg.getByRole("button", { name: "Connect" }));
    await waitFor(() => expect(screen.queryByTestId("wire-dialog")).toBeNull());
    expect(screen.getByTestId("exit-note").textContent).toContain("r1, r2 switched to vyos@1.5");
    await check();
    const te = readTe((state.writes.filter((w) => w.op === "validate").at(-1)!.body as { files: Record<string, string> }).files["te.yaml"]!);
    expect(te.nodes.filter((n) => n.kind === "router").map((n) => `${n.name}:${n.image}`)).toEqual(["r1:vyos@1.5", "r2:vyos@1.5", "r3:ubuntu-server-22@v5"]);
    expect(te.segments.find((s) => s.name === "r1--r2")!.ospfArea).toBe(0);
    // One undo takes the transit and the image switch back together.
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    await waitFor(() => expect(screen.queryByTestId("dirty-marker")).toBeNull());
  });

  it("OSPF with no router-ospf profile: the dialog says the profiles name no VyOS image, and offers no switch", async () => {
    const base = withTriangle();
    base.state.imageProfiles = base.state.imageProfiles.filter((p) => p.name !== "router-ospf");
    renderAt(base.api, "/twins/tri/canvas");
    await waitFor(() => expect(screen.getAllByTestId("canvas-router").length).toBe(3));
    fireEvent.click(router("r1"));
    fireEvent.click(router("r3"), { shiftKey: true });
    fireEvent.contextMenu(router("r3"));
    fireEvent.click(within(await screen.findByTestId("context-menu")).getByText(/Connect 2 routers as/));
    const dlg = within(await screen.findByTestId("wire-dialog"));
    fireEvent.click(dlg.getByLabelText(/run OSPF on these links/));
    expect(dlg.getByTestId("ospf-image").textContent).toContain("No router-ospf image profile");
    expect(dlg.queryByLabelText(/switch these routers/)).toBeNull();
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
