/**
 * The untitled design's clean rule and the selection after an undo (`SPEC.md` "Two layers, one
 * rule" / the leave guard, 2026-11-20 round 2): undone back to empty, an untitled design is clean --
 * no "layout: N unsaved changes", no leave prompt, nothing kept in this browser for the next New --
 * and the selection keeps only what still exists.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { DEVICE_W } from "../src/features/edit-canvas/layout/geometry";
import { afterEach, describe, expect, it } from "vitest";

import type { Template } from "../src/api/types";
import { pruneSelection } from "../src/features/edit-canvas/SidePanel";
import { readTe } from "../src/features/edit-canvas/edit/te";
import { parseYaml, toPlain } from "../src/features/edit-canvas/edit/yaml";
import { seeded } from "./helpers/canvasFixtures";
import { renderAt } from "./helpers/render";

afterEach(() => {
  localStorage.clear();
  sessionStorage.clear();
});

const PROFILES = [
  { name: "linux-server", image: "debian-12-cloud@v1" },
  { name: "windows-client", image: "win11-finance@v4" },
  { name: "router", image: "debian-12-cloud@v1" },
];
const BRANCH: Template = (() => {
  const t = (toPlain(parseYaml(readFileSync(resolve(__dirname, "../../catalog/templates/branch-office/template.yaml"), "utf8")).root) as unknown as { template: Template }).template;
  return { ...{ description: "", icon: "", params: [], ports: [], fragment: {}, layout: {} }, ...t };
})();

const identityView = () => sessionStorage.setItem("axr-canvas-viewport:twin:__untitled__", JSON.stringify({ x: 0, y: 0, zoom: 1 }));
function quickAdd(search: string, x: number, y: number, id?: string) {
  fireEvent.doubleClick(window.document.querySelector(".react-flow__pane")!, { clientX: x, clientY: y });
  const quick = within(screen.getByTestId("quick-add"));
  fireEvent.change(quick.getByLabelText("Quick add search"), { target: { value: search } });
  const hits = quick.getAllByTestId("add-hit");
  fireEvent.click(id ? hits.find((h) => h.dataset["id"] === id)! : hits[0]!);
}
const router = (name: string) => screen.getAllByTestId("canvas-router").find((r) => r.dataset["nodeId"] === name)!;
const drag = (from: Element, to: Element) => {
  const o = { button: 0, pointerId: 1, isPrimary: true, pointerType: "mouse" };
  fireEvent.pointerDown(from, { ...o, clientX: 10, clientY: 10 });
  fireEvent.pointerMove(to, { ...o, clientX: 220, clientY: 140 });
  fireEvent.pointerUp(to, { ...o, clientX: 220, clientY: 140 });
};
const undo = () => screen.getByRole("button", { name: "Undo" });
/** Whether leaving the page now asks first (a `beforeunload` the page cancels). */
function guardFires(): boolean {
  const e = new Event("beforeunload", { cancelable: true });
  window.dispatchEvent(e);
  return e.defaultPrevented;
}
async function undoAll() {
  for (let i = 0; i < 20 && !undo().hasAttribute("disabled"); i++) {
    fireEvent.click(undo());
    await waitFor(() => expect(true).toBe(true));
  }
  expect(undo().hasAttribute("disabled")).toBe(true);
}

describe("an untitled design undone back to empty is clean", () => {
  it("routers placed and linked, then every step undone: no layout changes, no leave prompt, nothing kept for the next New", async () => {
    const { api } = seeded({ imageProfiles: PROFILES });
    identityView();
    renderAt(api, "/canvas/new");
    await waitFor(() => expect(window.document.querySelector(".react-flow__pane")).not.toBeNull());
    for (const [x, y] of [[200, 200], [500, 200]] as const) {
      quickAdd("router", x, y, "router");
      fireEvent.click(await screen.findByTestId("drop-add"));
    }
    await waitFor(() => expect(screen.getAllByTestId("canvas-router")).toHaveLength(2));
    const [a, b] = screen.getAllByTestId("canvas-router").map((r) => r.dataset["nodeId"]!);
    drag(within(router(a!)).getByTestId("connect-ring"), router(b!));
    await waitFor(() => expect(undo().getAttribute("title")).toMatch(/^undo: connect /));
    expect(screen.getByTestId("layout-dirty").textContent).toMatch(/layout: \d+ unsaved/);
    expect(guardFires()).toBe(true);
    expect(localStorage.getItem("axr-canvas-undo:twin:__untitled__")).not.toBeNull();

    await undoAll();
    await waitFor(() => expect(screen.queryAllByTestId("canvas-router")).toHaveLength(0));
    // The guard and the layout bar agree: nothing is unsaved.
    expect(screen.queryByTestId("layout-dirty")).toBeNull();
    expect(screen.queryByTestId("dirty-marker")).toBeNull();
    expect(guardFires()).toBe(false);
    // Nothing left in this browser for the next New to reopen.
    expect(localStorage.getItem("axr-canvas-undo:twin:__untitled__")).toBeNull();
    expect(localStorage.getItem("axr-canvas-layout:twin:__untitled__")).toBeNull();
    // A redo still brings the first router back where it stood (its pin was kept, dormant).
    fireEvent.click(screen.getByRole("button", { name: "Redo" }));
    await waitFor(() => expect(screen.getAllByTestId("canvas-router")).toHaveLength(1));
    // Centred on its drop point (B5): x = 200 - half a device box, snapped to the grid.
    expect(Math.abs(Number(router(a!).dataset["x"]) - (200 - DEVICE_W / 2))).toBeLessThanOrEqual(12);
  });
});

describe("the selection after an undo", () => {
  it("branch-gw and branch-sw selected, then undone away: the Selection tab shows the Twin summary, not the two groups", async () => {
    const { api } = seeded({ imageProfiles: PROFILES, templates: [BRANCH] });
    identityView();
    renderAt(api, "/canvas/new");
    await waitFor(() => expect(window.document.querySelector(".react-flow__pane")).not.toBeNull());
    quickAdd("branch", 400, 200);
    fireEvent.click(await screen.findByTestId("template-add"));
    await waitFor(() => expect(screen.getAllByTestId("canvas-router")).toHaveLength(1));
    fireEvent.click(router("branch-gw"));
    fireEvent.click(screen.getAllByTestId("canvas-device").find((d) => d.dataset["nodeId"] === "branch-sw")!, { shiftKey: true });
    await waitFor(() => expect(screen.getByTestId("side-panel").textContent).toContain("2 node groups"));
    await undoAll();
    await waitFor(() => expect(screen.queryAllByTestId("canvas-router")).toHaveLength(0));
    const side = screen.getByTestId("side-panel").textContent ?? "";
    expect(side).not.toContain("node groups");
    expect(side).not.toContain("branch-gw");
    expect(within(screen.getByTestId("side-panel")).getByRole("heading", { name: "Twin" })).toBeTruthy();
  });

  it("pruneSelection keeps what exists, drops what does not, and leaves nothing as no selection", () => {
    const te = readTe("twin_config:\n  id: t\n  segments:\n  - { name: lan, cidr: 10.1.0.0/24 }\n  nodes:\n  - { name: gw, kind: router, image: x, nets: [ { segment: lan } ] }\n");
    const m = { te, tap: null };
    expect(pruneSelection({ kind: "node", names: ["gw", "gone"] }, m)).toEqual({ kind: "node", names: ["gw"] });
    expect(pruneSelection({ kind: "node", names: ["gone"] }, m)).toEqual({ kind: "none" });
    expect(pruneSelection({ kind: "segment", names: ["lan"] }, m)).toEqual({ kind: "segment", names: ["lan"] });
    expect(pruneSelection({ kind: "leg", node: "gw", segment: "wan" }, m)).toEqual({ kind: "none" });
    expect(pruneSelection({ kind: "link", index: 0 }, m)).toEqual({ kind: "none" });
    expect(pruneSelection({ kind: "subtree", root: "gw" }, m)).toEqual({ kind: "subtree", root: "gw" });
    expect(pruneSelection({ kind: "annotation", id: "a1" }, m)).toEqual({ kind: "annotation", id: "a1" });
  });
});
