/**
 * Round 2's small fixes (2026-11-20): a card's drops numbered from 1 (`router-1`, `router-2`, …); a
 * drop's segment named for what it is for (`file-server-lan`, `-2`); the role list scrolled to the
 * preselected role; the "add at least one segment" notice in the neutral style; no note on a
 * brand-new untitled design; the "Not carried" box explained and selectable.
 */
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Persona } from "../src/api/types";
import { seeded, tab, twinYaml } from "./helpers/canvasFixtures";
import { renderAt } from "./helpers/render";

afterEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  vi.restoreAllMocks();
});

const persona = (id: string, os: "linux" | "windows", category: string, label: string, requires: string[] = []): Persona => ({ id, os_family: os, drive: "scripted", source: "catalog", requires, summary: "", emits: [], category, label, blurb: "" });
const LIB: Persona[] = [
  persona("dns-server", "linux", "business", "DNS server"),
  persona("db-server", "linux", "business", "Database server"),
  persona("egress-proxy", "linux", "business", "Web proxy"),
  persona("file-server", "linux", "business", "File server", ["corp-workstation"]),
  persona("web-tier", "linux", "business", "Web / app server"),
  persona("corp-workstation", "windows", "endpoint", "Office user"),
];
const identityView = () => sessionStorage.setItem("axr-canvas-viewport:twin:__untitled__", JSON.stringify({ x: 0, y: 0, zoom: 1 }));
function quickAdd(search: string, x: number, y: number, id?: string) {
  fireEvent.doubleClick(window.document.querySelector(".react-flow__pane")!, { clientX: x, clientY: y });
  const quick = within(screen.getByTestId("quick-add"));
  fireEvent.change(quick.getByLabelText("Quick add search"), { target: { value: search } });
  const hits = quick.getAllByTestId("add-hit");
  fireEvent.click(id ? hits.find((h) => h.dataset["id"] === id)! : hits[0]!);
}
const pane = async () => waitFor(() => expect(window.document.querySelector(".react-flow__pane")).not.toBeNull());

describe("names", () => {
  it("new routers: one alone takes the bare prefix, the next continue at -2, -3 (round 3 item 5b)", async () => {
    const { api } = seeded();
    identityView();
    renderAt(api, "/canvas/new");
    await pane();
    for (const x of [100, 300, 500]) {
      quickAdd("router", x, 200, "router");
      fireEvent.click(await screen.findByTestId("drop-add"));
    }
    await waitFor(() => expect(screen.getAllByTestId("canvas-router").map((r) => r.dataset["nodeId"]).sort()).toEqual(["router", "router-2", "router-3"]));
  });

  it("a segment a drop makes is named for its first host's role, de-duplicated: file-server-lan, file-server-lan-2", async () => {
    const { api } = seeded({ personas: LIB });
    identityView();
    renderAt(api, "/canvas/new");
    await pane();
    for (const x of [100, 500]) {
      quickAdd("file", x, 200);
      fireEvent.click(await screen.findByTestId("drop-add"));
    }
    await waitFor(() => expect(screen.getAllByTestId("canvas-segment").map((s) => s.dataset["name"]).sort()).toEqual(["file-server-lan", "file-server-lan-2"]));
    expect(screen.getAllByTestId("canvas-host").map((h) => h.dataset["nodeId"]).sort()).toEqual(["file-server", "file-server-2"]);
  });
});

describe("the drop popover", () => {
  it("the role list scrolls to the preselected role (File server after searching 'file')", async () => {
    // jsdom lays nothing out: the list is 100 px tall at the top, the chosen role 300 px down.
    const rect = (top: number, height: number) => ({ top, bottom: top + height, left: 0, right: 200, width: 200, height, x: 0, y: top, toJSON: () => ({}) }) as DOMRect;
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
      if (this.dataset["testid"] === "drop-roles") return rect(0, 100);
      if (this.dataset["testid"] === "drop-role" && this.getAttribute("aria-checked") === "true") return rect(300, 20);
      return rect(0, 0);
    });
    vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockImplementation(function (this: HTMLElement) {
      return this.dataset["testid"] === "drop-roles" ? 100 : 0;
    });
    const { api } = seeded({ personas: LIB });
    identityView();
    renderAt(api, "/canvas/new");
    await pane();
    quickAdd("file", 300, 200);
    const roles = await screen.findByTestId("drop-roles");
    expect(within(roles).getAllByTestId("drop-role").find((r) => r.getAttribute("aria-checked") === "true")!.dataset["role"]).toBe("file-server");
    // Centred: 300 - (100 - 20) / 2.
    expect(roles.scrollTop).toBe(260);
  });
});

describe("the untitled design's chrome", () => {
  it("'add at least one segment before saving' is a neutral notice, not the success green; a brand-new design's Notes tab counts no notes", async () => {
    const { api } = seeded();
    renderAt(api, "/canvas/new");
    await screen.findByTestId("rail-add");
    const notice = screen.getByTestId("empty-reason");
    expect(notice.textContent).toBe("add at least one segment before saving");
    expect(notice.className).toBe("notice info");
    expect(screen.getByRole("tab", { name: /^Notes/ }).textContent).toBe("Notes");
    tab("Notes");
    expect(screen.getByText("nothing to note about this twin")).toBeTruthy();
  });
});

describe("the 'Not carried' box", () => {
  const TE = `twin_config:
  id: stray
  segments:
    - { name: lan, cidr: 10.1.0.0/24, zone_type: vlan }
    - { name: segment-4, cidr: 10.1.4.0/24, zone_type: vlan }
  nodes:
    - { name: gw, kind: router, image: x@1, nets: [ { segment: lan } ] }
    - { name: ws, image: x@1, net: segment-4 }
`;
  it("explains itself in plain words and, clicked, selects the segment it holds", async () => {
    const base = seeded();
    const { api } = seeded({
      twins: { ...base.state.twins, stray: { ...base.state.twins["corp-auth"]!, id: "stray" } },
      documents: { ...base.state.documents, stray: { id: "stray", te_file: "te.yaml", files: { "te.yaml": TE, "twin.yaml": twinYaml("stray") }, skipped: [] } },
      twinConfigs: [...base.state.twinConfigs, { id: "stray", description: null, cluster: null, parameterized: false, param_names: [], error: null }],
    });
    renderAt(api, "/twins/stray/canvas");
    const box = await screen.findByTestId("canvas-uncarried");
    expect(box.getAttribute("title")).toBe("segment-4 isn't on any router: nothing outside it can reach it\nclick to select it");
    fireEvent.click(box);
    await waitFor(() => expect(within(screen.getByTestId("side-panel")).getByTestId("segment-panel").textContent).toContain("segment-4"));
  });
});
