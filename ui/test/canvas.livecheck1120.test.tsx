/**
 * The live check of 2026-11-20 (5173): a LAN never takes a transit's range;
 * the wiring dialog shows the pairs already linked and runs OSPF on them; the
 * uncarried box sits beside what it holds; an edge never crosses a name label;
 * Ctrl+Enter checks as soon as the page is shown.
 */
import { readdirSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { Template } from "../src/api/types";
import { applyDeviceDrop } from "../src/features/edit-canvas/add/drop";
import { pinBox, placeDropped } from "../src/features/edit-canvas/add/placeDrop";
import { DEVICE_H, DEVICE_W } from "../src/features/edit-canvas/layout/geometry";
import { applyTemplateDrop } from "../src/features/edit-canvas/add/template";
import { alongLine, FREE_FAN, freeEdgeEnds, freeLayout, nameBox, pillBox, pillText, segmentHitsBox, type PillAt } from "../src/features/edit-canvas/canvas/free";
import { absolutePosition, type Placement } from "../src/features/edit-canvas/canvas/placement";
import { placeTree } from "../src/features/edit-canvas/canvas/tree";
import { buildGraph } from "../src/features/edit-canvas/model/graph";
import { DEFAULT_LAN_PREFIX, inTransitPool, lanSitePrefix, nextFreeLan, nextFreeSlash24, nextFreeTransit } from "../src/features/edit-canvas/edit/allocate";
import { routerToNewLan, wirePlan, wirePlanText, wireRouters } from "../src/features/edit-canvas/edit/connect";
import { documentGraph, readDoc, type WorkingDoc } from "../src/features/edit-canvas/edit/document";
import { readTe } from "../src/features/edit-canvas/edit/te";
import { SidePanel, type SidePanelProps } from "../src/features/edit-canvas/SidePanel";
import { newSegmentOnRouter } from "../src/features/edit-canvas/edit/switches";
import { parseYaml, toPlain } from "../src/features/edit-canvas/edit/yaml";
import { check, seeded, twinYaml } from "./helpers/canvasFixtures";
import { renderAt } from "./helpers/render";

afterEach(() => localStorage.clear());

/** A triangle of routers whose only CIDRs are its transits. */
export const TRIANGLE = `twin_config:
  id: tri
  segments:
    - { name: router1-1--router2-1, cidr: 10.254.0.0/30, zone_type: vlan }
    - { name: router2-1--router3-1, cidr: 10.254.0.4/30, zone_type: vlan }
    - { name: router1-1--router3-1, cidr: 10.254.0.8/30, zone_type: vlan }
  nodes:
    - name: router1-1
      role: gw
      kind: router
      image: debian-12-cloud@v1
      nets:
        - { segment: router1-1--router2-1, ip: 10.254.0.1 }
        - { segment: router1-1--router3-1, ip: 10.254.0.9 }
    - name: router2-1
      role: gw
      kind: router
      image: debian-12-cloud@v1
      nets:
        - { segment: router1-1--router2-1, ip: 10.254.0.2 }
        - { segment: router2-1--router3-1, ip: 10.254.0.5 }
    - name: router3-1
      role: gw
      kind: router
      image: debian-12-cloud@v1
      nets:
        - { segment: router2-1--router3-1, ip: 10.254.0.6 }
        - { segment: router1-1--router3-1, ip: 10.254.0.10 }
`;
export const TRI_DOC = { id: "tri", teFile: "te.yaml", files: { "te.yaml": TRIANGLE } };

const DIR = resolve(__dirname, "../../catalog/templates");
const BUILTINS: Template[] = readdirSync(DIR)
  .filter((d) => !d.startsWith("."))
  .map((d) => {
    const t = (toPlain(parseYaml(readFileSync(resolve(DIR, d, "template.yaml"), "utf8")).root) as unknown as { template: Partial<Template> & Pick<Template, "id" | "label"> }).template;
    return { builtin: false, description: "", icon: "", params: [], ports: [], fragment: {}, layout: {}, ...t };
  });
const PROFILES = ["linux-server", "linux-client", "windows-server", "windows-client", "router", "router-ospf"].map((name) => ({ name, image: "debian-12-cloud@v1" }));

const lans = (files: Record<string, string>) => readDoc({ ...TRI_DOC, files }).te.segments.filter((s) => !TRI_DOC.files["te.yaml"].includes(s.name));

describe("1. a LAN never takes an address from the transit range", () => {
  it("allocation: the pool is fenced off, transits are not a site, the default prefix is outside the pool", () => {
    expect(inTransitPool(DEFAULT_LAN_PREFIX + ".0.0/24")).toBe(false);
    expect(nextFreeSlash24("10.254", [])).toBeNull();
    expect(lanSitePrefix(["10.254.0.0/30", "10.254.0.4/30"])).toBeNull();
    expect(lanSitePrefix(["10.254.0.0/30", "10.70.10.0/24"])).toBe("10.70");
    expect(nextFreeLan(null, ["10.254.0.0/30"])).toBe(`${DEFAULT_LAN_PREFIX}.0.0/24`);
    expect(nextFreeLan("10.70", ["10.70.10.0/24", "10.254.0.0/30"])).toBe("10.70.11.0/24");
    // A transit still skips any LAN in play.
    expect(nextFreeTransit(["10.254.0.0/30", "10.254.0.4/24"])).toBe("10.254.1.0/30");
  });

  it("a File server dropped on the empty pane of a transits-only design gets a LAN outside 10.254.0.0/16", () => {
    const m = readDoc(TRI_DOC);
    const r = applyDeviceDrop(TRI_DOC, m, { card: "server", os: "linux", role: "file-server", persona: null, count: 1, prefix: "file-server", target: { pane: true }, image: "debian-12-cloud@v1" });
    const made = lans(r.files);
    expect(made.map((s) => s.cidr)).toEqual([`${DEFAULT_LAN_PREFIX}.0.0/24`]);
    expect(made.every((s) => !inTransitPool(s.cidr))).toBe(true);
  });

  it("router -> empty pane and a host dropped on a router: a LAN outside the pool", () => {
    const m = readDoc(TRI_DOC);
    expect(inTransitPool(lans(routerToNewLan(TRI_DOC, m, "router1-1").files)[0]!.cidr)).toBe(false);
    expect(inTransitPool(lans(newSegmentOnRouter(TRI_DOC, m, "router2-1").files)[0]!.cidr)).toBe(false);
  });

  it.each(BUILTINS.map((t) => [t.id]))("template %s dropped (subnet: auto) on a transits-only design: no LAN in the pool", (id) => {
    const t = BUILTINS.find((x) => x.id === id)!;
    const r = applyTemplateDrop(TRI_DOC, readDoc(TRI_DOC), t, {}, PROFILES, null);
    const after = readDoc({ ...TRI_DOC, files: r.files });
    expect(after.te.problems).toEqual([]);
    for (const s of lans(r.files)) {
      const isTransit = /\/30$/.test(s.cidr);
      if (!isTransit) expect(inTransitPool(s.cidr), `${s.name} ${s.cidr}`).toBe(false);
    }
  });

  it("an existing design with LANs keeps its prefix, past the transits", () => {
    const withLan = TRIANGLE.replace("  segments:\n", "  segments:\n    - { name: office, cidr: 10.70.10.0/24 }\n");
    const doc = { ...TRI_DOC, files: { "te.yaml": withLan } };
    const r = applyDeviceDrop(doc, readDoc(doc), { card: "server", os: "linux", role: "file-server", persona: null, count: 1, prefix: "file-server", target: { pane: true }, image: null });
    expect(readDoc({ ...doc, files: r.files }).te.segments.find((s) => s.name === "file-server-lan")!.cidr).toBe("10.70.11.0/24");
    const t = applyTemplateDrop(doc, readDoc(doc), BUILTINS.find((x) => x.id === "office-segment")!, {}, PROFILES, null);
    const made = readDoc({ ...doc, files: t.files }).te.segments.filter((s) => !withLan.includes(s.name));
    expect(made.length).toBeGreaterThan(0);
    for (const s of made) expect(s.cidr.startsWith("10.70.")).toBe(true);
  });
});

const TRANSITS = ["router1-1--router2-1", "router2-1--router3-1", "router1-1--router3-1"];

export function withTriangle() {
  const base = seeded();
  return seeded({
    twins: { ...base.state.twins, tri: { ...base.state.twins["corp-auth"]!, id: "tri" } },
    documents: { ...base.state.documents, tri: { id: "tri", te_file: "te.yaml", files: { "te.yaml": TRIANGLE, "twin.yaml": twinYaml("tri") }, skipped: [] } },
    twinConfigs: [...base.state.twinConfigs, { id: "tri", description: null, cluster: null, parameterized: false, param_names: [], error: null }],
  });
}

describe("2. the router wiring dialog shows the links already drawn and runs OSPF on them", () => {
  const routers = ["router1-1", "router2-1", "router3-1"];

  it("the plan names each pair by its real, sorted `--` name: new, or already linked by its transit", () => {
    const m = readDoc(TRI_DOC);
    expect(wirePlan(m.te, routers, "chain").map(wirePlanText)).toEqual(["already linked: router1-1--router2-1", "already linked: router2-1--router3-1"]);
    const two = TRIANGLE.replace(/    - \{ name: router1-1--router3-1.*\n/, "").replace(/        - \{ segment: router1-1--router3-1.*\n/g, "");
    expect(wirePlan(readDoc({ ...TRI_DOC, files: { "te.yaml": two } }).te, ["router3-1", "router1-1"], "chain").map(wirePlanText)).toEqual(["new: router1-1--router3-1"]);
  });

  it("an all-linked triangle: Connect creates nothing and sets ospf_area on all 3 transits", () => {
    const r = wireRouters(TRI_DOC, readDoc(TRI_DOC), routers, "ring", { ospfArea: 0 });
    expect(r.made).toEqual([]);
    expect(r.ospf.sort()).toEqual([...TRANSITS].sort());
    const te = readTe(r.files["te.yaml"]!);
    expect(te.segments).toHaveLength(3);
    expect(te.segments.map((s) => s.ospfArea)).toEqual([0, 0, 0]);
    // Nothing to make and no OSPF/image choice: a refusal, said as such.
    expect(() => wireRouters(TRI_DOC, readDoc(TRI_DOC), routers, "ring")).toThrow(/all pairs already linked/);
  });

  it("on the page: the dialog says all pairs are already linked, and Connect with OSPF sets the area on the 3 transits in one undo step", async () => {
    const { api, state } = withTriangle();
    renderAt(api, "/twins/tri/canvas");
    await waitFor(() => expect(screen.getAllByTestId("canvas-router").length).toBe(3));
    const router = (name: string) => screen.getAllByTestId("canvas-router").find((r) => r.dataset["nodeId"] === name)!;
    fireEvent.click(router("router1-1"));
    fireEvent.click(router("router2-1"), { shiftKey: true });
    fireEvent.click(router("router3-1"), { shiftKey: true });
    fireEvent.contextMenu(router("router3-1"));
    fireEvent.click(within(await screen.findByTestId("context-menu")).getByText(/Connect 3 routers as/));
    const dlg = within(await screen.findByTestId("wire-dialog"));
    fireEvent.click(dlg.getByLabelText("ring"));
    expect(dlg.getAllByTestId("wire-plan-row").map((r) => r.textContent)).toEqual(TRANSITS.map((t) => `already linked: ${t}`));
    expect(dlg.getByTestId("wire-all-linked").textContent).toBe("all pairs already linked");
    fireEvent.click(dlg.getByLabelText(/run OSPF on these links/));
    const sw = dlg.queryByLabelText(/switch these routers/) as HTMLInputElement | null;
    if (sw?.checked) fireEvent.click(sw);
    fireEvent.click(dlg.getByRole("button", { name: "Connect" }));
    await waitFor(() => expect(screen.queryByTestId("wire-dialog")).toBeNull());
    expect(screen.getByTestId("exit-note").textContent).toContain("0 transits made; ospf_area 0 set on");
    expect(screen.getByRole("button", { name: "Undo" }).getAttribute("title")).toBe("undo: connect 3 routers as ring");
    await check();
    const te = readTe((state.writes.filter((w) => w.op === "validate").at(-1)!.body as { files: Record<string, string> }).files["te.yaml"]!);
    expect(te.segments.map((s) => `${s.name}:${s.ospfArea}`)).toEqual(TRIANGLE.match(/name: (router\S+--\S+),/g)!.map((x) => `${x.slice(6, -1)}:0`));
  });

  it("a transit's inspector has an OSPF area field (empty = off) that writes and clears ospf_area", () => {
    let doc: WorkingDoc = TRI_DOC;
    const props = (): SidePanelProps => ({
      twinId: "tri",
      model: readDoc(doc),
      baseline: null,
      selection: { kind: "segment", names: ["router1-1--router2-1"] },
      locked: false,
      images: [],
      personas: [],
      canPromote: false,
      promoteBlock: () => null,
      apply: (_label, fn) => {
        try {
          doc = { ...doc, files: fn(doc, readDoc(doc)) };
          return null;
        } catch (e) {
          return String(e);
        }
      },
      onPromoteRole: () => {},
      onPromoteMerge: () => {},
      annotations: [],
      onAnnotation: () => {},
      onRemoveAnnotation: () => {},
      changes: null,
      blocked: null,
    });
    const view = render(<SidePanel {...props()} />);
    const field = () => screen.getByLabelText("OSPF area") as HTMLInputElement;
    expect(field().value).toBe("");
    fireEvent.change(field(), { target: { value: "1" } });
    fireEvent.keyDown(field(), { key: "Enter" });
    expect(readTe(doc.files["te.yaml"]!).segments.find((s) => s.name === "router1-1--router2-1")!.ospfArea).toBe(1);
    view.rerender(<SidePanel {...props()} />);
    expect(field().value).toBe("1");
    fireEvent.change(field(), { target: { value: "" } });
    fireEvent.keyDown(field(), { key: "Enter" });
    expect(readTe(doc.files["te.yaml"]!).segments.find((s) => s.name === "router1-1--router2-1")!.ospfArea).toBeNull();
    expect(doc.files["te.yaml"]).not.toContain("ospf_area");
  });
});

/** A design laid out free, with `pins` (top-lefts by `host:<name>` / `segment:<cidr>`). */
function free(te: string, pins: Record<string, { x: number; y: number }>, spread = false): Placement {
  const g = documentGraph(readDoc({ ...TRI_DOC, files: { "te.yaml": te } }), "tri");
  return freeLayout(placeTree(buildGraph(g.exportData, g.cast), pins, { extraLegs: g.extraLegs, uplinks: g.uplinks, tree: g.tree, fan: FREE_FAN }), spread ? new Set(Object.keys(pins)) : null);
}
/** A node's absolute box. */
function boxOf(p: Placement, id: string) {
  const n = p.nodes.find((x) => x.id === id)!;
  const at = absolutePosition(p.nodes, id)!;
  return { l: at.x, t: at.y, r: at.x + (n.width ?? 0), b: at.y + (n.height ?? 0) };
}

describe("3. the 'Not carried' box stands next to what it holds", () => {
  const STRAY = TRIANGLE.replace("  segments:\n", "  segments:\n    - { name: orphan, cidr: 10.9.0.0/24, zone_type: vlan }\n") + "    - { name: ws, image: x@1, net: orphan }\n";
  const PINS = { "host:router1-1": { x: 300, y: 0 }, "host:router2-1": { x: 0, y: 260 }, "host:router3-1": { x: 600, y: 260 } };

  it("free layout: just above the one uncarried segment, never more than a row away -- wherever that segment was dragged", () => {
    for (const at of [{ x: 900, y: 700 }, { x: -400, y: 40 }]) {
      const p = free(STRAY, { ...PINS, "segment:10.9.0.0/24": at });
      const seg = p.nodes.find((n) => n.data.kind === "segment" && n.data.name === "orphan")!;
      const box = boxOf(p, "chrome:uncarried");
      const target = boxOf(p, seg.id);
      expect(box.b).toBeLessThanOrEqual(target.t);
      expect(target.t - box.b).toBeLessThan(40);
      expect(Math.abs((box.l + box.r) / 2 - (target.l + target.r) / 2)).toBeLessThan(1);
    }
  });

  it("several: beside the first one", () => {
    const two = STRAY.replace("  segments:\n", "  segments:\n    - { name: orphan-2, cidr: 10.9.1.0/24, zone_type: vlan }\n") + "    - { name: ws2, image: x@1, net: orphan-2 }\n";
    const p = free(two, { ...PINS, "segment:10.9.0.0/24": { x: 900, y: 700 }, "segment:10.9.1.0/24": { x: 300, y: 700 } });
    const held = p.edges.filter((e) => e.source === "chrome:uncarried").map((e) => boxOf(p, e.target));
    expect(held).toHaveLength(2);
    const box = boxOf(p, "chrome:uncarried");
    const first = held[0]!;
    // Beside it (same row), not a row or more away.
    expect(box.l >= first.r || box.r <= first.l).toBe(true);
    expect(Math.abs((box.t + box.b) / 2 - (first.t + first.b) / 2)).toBeLessThan(1);
  });

  it("on the page: clicked, the Selection panel repeats its words, each segment a row that selects it", async () => {
    const base = seeded();
    const { api } = seeded({
      twins: { ...base.state.twins, tri: { ...base.state.twins["corp-auth"]!, id: "tri" } },
      documents: { ...base.state.documents, tri: { id: "tri", te_file: "te.yaml", files: { "te.yaml": STRAY, "twin.yaml": twinYaml("tri") }, skipped: [] } },
      twinConfigs: [...base.state.twinConfigs, { id: "tri", description: null, cluster: null, parameterized: false, param_names: [], error: null }],
    });
    renderAt(api, "/twins/tri/canvas");
    const box = await screen.findByTestId("canvas-uncarried");
    expect(box.getAttribute("title")).toBe("orphan isn't on any router: nothing outside it can reach it\nclick to select it");
    fireEvent.click(box);
    const note = await within(screen.getByTestId("side-panel")).findByTestId("uncarried-note");
    const rows = within(note).getAllByTestId("uncarried-row");
    expect(rows.map((r) => r.textContent)).toEqual(["orphan isn't on any router: nothing outside it can reach it"]);
    fireEvent.click(within(rows[0]!).getByRole("button"));
    await waitFor(() => expect(within(screen.getByTestId("side-panel")).queryByTestId("uncarried-note")).toBeNull());
    expect(within(screen.getByTestId("side-panel")).getByTestId("segment-panel").textContent).toContain("orphan");
  });
});

describe("4. no edge crosses a name label", () => {
  it("in the 3-router triangle (router1-1 above router3-1), no edge segment intersects a node's label box", () => {
    const p = free(TRIANGLE, { "host:router1-1": { x: 300, y: 0 }, "host:router2-1": { x: 0, y: 300 }, "host:router3-1": { x: 300, y: 300 } });
    const ends = freeEdgeEnds(p);
    expect(ends.size).toBe(3);
    const labelled = p.nodes.filter((n) => n.data.kind === "router");
    // The name the edge from above would cross has moved off it.
    expect(labelled.some((n) => n.data.kind === "router" && n.data.labelSide && n.data.labelSide !== "above")).toBe(true);
    for (const n of labelled) {
      if (n.data.kind !== "router") continue;
      const at = absolutePosition(p.nodes, n.id)!;
      const box = nameBox({ type: n.type, kind: "router", x: at.x, y: at.y, w: n.width ?? 0, h: n.height ?? 0, label: n.data.name, sub: `${n.data.segments.length} legs` }, n.data.labelSide);
      expect(box).not.toBeNull();
      for (const [id, e] of ends) expect(segmentHitsBox(e.p, e.q, box!), `${id} crosses ${n.data.name}'s label`).toBe(false);
    }
    // The routers themselves stay where they were pinned.
    expect(p.nodes.find((n) => n.id === "router:router1-1")!.position).toEqual({ x: 300, y: 0 });
  });
});

describe("5. Ctrl+Enter checks as soon as the canvas page is shown", () => {
  it("mount the page, fire Ctrl+Enter on the document: a validate call, with no click first", async () => {
    const { api, state } = withTriangle();
    renderAt(api, "/twins/tri/canvas");
    await waitFor(() => expect(screen.getAllByTestId("canvas-router").length).toBe(3));
    expect(state.writes.filter((w) => w.op === "validate")).toHaveLength(0);
    fireEvent.keyDown(document, { key: "Enter", ctrlKey: true });
    await waitFor(() => expect(state.writes.filter((w) => w.op === "validate")).toHaveLength(1));
    // Still ignored while a text field has the focus.
    const field = document.createElement("input");
    document.body.appendChild(field);
    field.focus();
    fireEvent.keyDown(field, { key: "Enter", ctrlKey: true });
    await new Promise((r) => setTimeout(r, 50));
    expect(state.writes.filter((w) => w.op === "validate")).toHaveLength(1);
    field.remove();
  });
});

describe("follow-ups: a drop never lands on a box, a pill never sits on another edge", () => {
  const server = { card: "server", os: "linux", role: "file-server", persona: null, count: 1, prefix: "file-server", target: { pane: true }, image: null } as const;
  const known = { "host:router1-1": { x: 300, y: 0 }, "host:router2-1": { x: 0, y: 300 }, "host:router3-1": { x: 300, y: 300 } };
  const hostNames = new Set(["file-server-1"]);
  const overlapsAny = (pins: Record<string, { x: number; y: number }>) =>
    Object.entries(pins).some(([key, p]) => {
      const a = pinBox(key, p, hostNames);
      return Object.values(known).some((k) => a.l < k.x + DEVICE_W && k.x < a.r && a.t < k.y + DEVICE_H && k.y < a.b);
    });

  it("a File server dropped on the pane where router1-1 stands: the server and its new LAN move clear, their shape kept", () => {
    const m = readDoc(TRI_DOC);
    const after = readDoc({ ...TRI_DOC, files: applyDeviceDrop(TRI_DOC, m, server).files }).te;
    const at = { x: known["host:router1-1"].x + 20, y: known["host:router1-1"].y + 20 };
    const fixed = { "host:file-server-1": at };
    const asked = placeDropped(m.te, after, at, fixed, known);
    expect(overlapsAny(asked)).toBe(true); // what the drop asked for lands on router1-1
    const pins = placeDropped(m.te, after, at, fixed, known, { clear: true });
    expect(Object.keys(pins).sort()).toEqual(Object.keys(asked).sort());
    expect(overlapsAny(pins)).toBe(false);
    const dx = pins["host:file-server-1"]!.x - asked["host:file-server-1"]!.x;
    const dy = pins["host:file-server-1"]!.y - asked["host:file-server-1"]!.y;
    for (const k of Object.keys(pins)) expect(pins[k]).toEqual({ x: asked[k]!.x + dx, y: asked[k]!.y + dy });
  });

  it("dropped in clear space: it stays exactly where it was dropped", () => {
    const m = readDoc(TRI_DOC);
    const after = readDoc({ ...TRI_DOC, files: applyDeviceDrop(TRI_DOC, m, server).files }).te;
    const at = { x: 900, y: 600 };
    expect(placeDropped(m.te, after, at, { "host:file-server-1": at }, known, { clear: true })).toEqual(placeDropped(m.te, after, at, { "host:file-server-1": at }, known));
  });

  it.each([
    ["router1-1 above router3-1", known],
    ["spread wide", { "host:router1-1": { x: 300, y: 0 }, "host:router2-1": { x: -200, y: 260 }, "host:router3-1": { x: 700, y: 300 } }],
  ])("the triangle (%s): no transit's pill has another edge running under it", (_, pins) => {
    const p = free(TRIANGLE, pins);
    const ends = freeEdgeEnds(p);
    const seglinks = p.edges.filter((e) => e.data?.kind === "seglink");
    expect(seglinks).toHaveLength(3);
    for (const e of seglinks) {
      const d = e.data as unknown as { segment: string; cidr?: string; pill: PillAt };
      const own = ends.get(e.id)!;
      const box = pillBox(alongLine(own.p, own.q, d.pill), pillText(d));
      for (const [id, o] of ends) if (id !== e.id) expect(segmentHitsBox(o.p, o.q, box), `${id} runs under ${d.segment}'s pill`).toBe(false);
    }
  });
});

describe("follow-up: the first free placement spreads linked routers", () => {
  const pillsClear = (p: Placement) => {
    const ends = freeEdgeEnds(p);
    for (const e of p.edges.filter((x) => x.data?.kind === "seglink")) {
      const d = e.data as unknown as { segment: string; cidr?: string; pill: PillAt };
      const own = ends.get(e.id)!;
      const box = pillBox(alongLine(own.p, own.q, d.pill), pillText(d));
      for (const [id, o] of ends) if (id !== e.id) expect(segmentHitsBox(o.p, o.q, box), `${id} runs under ${d.segment}'s pill`).toBe(false);
    }
  };
  const routers = (p: Placement) => p.nodes.filter((n) => n.data.kind === "router");

  it("a triangle with no positions: a zig-zag, not dagre's column; no pill under another edge, no name crossed", () => {
    const column = free(TRIANGLE, {});
    const xs = (p: Placement) => routers(p).map((n) => n.position.x);
    expect(Math.max(...xs(column)) - Math.min(...xs(column))).toBeLessThan(100); // the stacked column it was
    const p = free(TRIANGLE, {}, true);
    expect(Math.max(...xs(p)) - Math.min(...xs(p))).toBeGreaterThan(400);
    pillsClear(p);
    const ends = freeEdgeEnds(p);
    for (const n of routers(p)) {
      if (n.data.kind !== "router") continue;
      const at = absolutePosition(p.nodes, n.id)!;
      const box = nameBox({ type: n.type, kind: "router", x: at.x, y: at.y, w: n.width ?? 0, h: n.height ?? 0, label: n.data.name, sub: `${n.data.segments.length} legs` }, n.data.labelSide)!;
      for (const [id, e] of ends) expect(segmentHitsBox(e.p, e.q, box), `${id} crosses ${n.data.name}'s label`).toBe(false);
    }
  });

  it("a router that already has a position never moves; the others spread around nothing of it", () => {
    const pins = { "host:router2-1": { x: 40, y: 500 } };
    const p = free(TRIANGLE, pins, true);
    expect(p.nodes.find((n) => n.id === "router:router2-1")!.position).toEqual({ x: 40, y: 500 });
    // router1-1 and router3-1 share a transit and have no position: they spread apart.
    const a = p.nodes.find((n) => n.id === "router:router1-1")!.position;
    const b = p.nodes.find((n) => n.id === "router:router3-1")!.position;
    expect(Math.abs(a.x - b.x)).toBeGreaterThan(250);
  });

  it("each router carries its LAN and hosts along, and no two carried groups overlap", () => {
    const withLans = TRIANGLE.replace("  segments:\n", "  segments:\n    - { name: lan-a, cidr: 10.20.1.0/24 }\n    - { name: lan-b, cidr: 10.20.2.0/24 }\n")
      .replace("        - { segment: router1-1--router3-1, ip: 10.254.0.9 }\n", "        - { segment: router1-1--router3-1, ip: 10.254.0.9 }\n        - { segment: lan-a }\n")
      .replace("        - { segment: router1-1--router3-1, ip: 10.254.0.10 }\n", "        - { segment: router1-1--router3-1, ip: 10.254.0.10 }\n        - { segment: lan-b }\n")
      + "    - { name: ws-a, image: x@1, net: lan-a, count: 3 }\n    - { name: ws-b, image: x@1, net: lan-b }\n";
    const before = free(withLans, {});
    const p = free(withLans, {}, true);
    const pos = (q: Placement, id: string) => q.nodes.find((n) => n.id === id)!.position;
    const seg = (q: Placement, name: string) => q.nodes.find((n) => n.data.kind === "segment" && n.data.name === name)!.id;
    // lan-a keeps its offset from router1-1, lan-b from router3-1.
    for (const [r, l] of [["router:router1-1", "lan-a"], ["router:router3-1", "lan-b"]] as const) {
      expect(pos(p, seg(p, l)).x - pos(p, r).x).toBeCloseTo(pos(before, seg(before, l)).x - pos(before, r).x, -0.5);
      expect(pos(p, seg(p, l)).y - pos(p, r).y).toBeCloseTo(pos(before, seg(before, l)).y - pos(before, r).y, -0.5);
    }
    // No two drawn boxes overlap.
    const boxes = p.nodes.filter((n) => !n.parentId && ["router", "segment", "host"].includes(n.data.kind)).map((n) => ({ id: n.id, ...boxOf(p, n.id) }));
    for (let i = 0; i < boxes.length; i++) for (let j = i + 1; j < boxes.length; j++) {
      const a = boxes[i]!;
      const b = boxes[j]!;
      expect(a.l < b.r && b.l < a.r && a.t < b.b && b.t < a.b, `${a.id} overlaps ${b.id}`).toBe(false);
    }
  });
});
