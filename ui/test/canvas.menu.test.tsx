/**
 * The Canvas area of the console: the brand, the Canvas entry of the top menu
 * with its drop-down of designs (New design, Open…, the twins), the designs page,
 * the blank design document, and the `/canvas/:id` route. The untitled canvas
 * behind New design is `canvas.untitled.test.tsx`.
 */
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { Imprint, TwinConfigDocument } from "../src/api/types";
import { blankDesignFiles, designIdProblem } from "../src/areas/canvas/newDesign";
import { saveAsFiles } from "../src/features/edit-canvas/edit/document";
import { readTe } from "../src/features/edit-canvas/edit/te";
import { fakeApi, type FakeState } from "./helpers/fakeApi";
import { renderAt } from "./helpers/render";
import { twin } from "./imprints.page.test";

const TE = `twin_config:
  id: corp-auth
  description: an authored twin
  segments:
    - { name: net-lan, cidr: 10.1.0.0/24, zone_type: vlan }
  nodes:
    - name: ws01
      role: corp-workstation
      image: win11-finance@v4
      net: net-lan
`;
const MANIFEST = `twin:\n  id: corp-auth\n  containment: uncontained\n  tiers:\n    te:\n      default: te.yaml\n`;
const DOC: TwinConfigDocument = { id: "corp-auth", te_file: "te.yaml", files: { "te.yaml": TE, "twin.yaml": MANIFEST }, skipped: [] };
const IMPRINTS: Imprint[] = [
  { pit_id: "pit-1", name: "corp capture", derived_from: null, has_assertions: false, twins: [{ id: "corp-cast" }] },
  { pit_id: "pit-2", name: "branch capture", derived_from: null, has_assertions: false, twins: [] },
];

function seeded(over: Partial<FakeState> = {}) {
  return fakeApi({
    twins: { "corp-auth": twin("corp-auth", { hosts: 1, segments: 1 }), "corp-cast": twin("corp-cast", { hosts: 2, segments: 2, pit_id: "pit-1" }) },
    documents: { "corp-auth": DOC },
    twinConfigs: [{ id: "corp-auth", description: "an authored twin", cluster: null, parameterized: false, param_names: [], error: null }],
    imprints: IMPRINTS,
    ...over,
  });
}

describe("the brand and the Canvas entry of the top menu", () => {
  it("is axTwin, with Canvas after Twins and a drop-down of the designs", async () => {
    const { api } = seeded();
    renderAt(api, "/twins");
    expect(screen.getByText("axTwin").className).toBe("brand");
    expect(document.title === "" || document.title === "axTwin").toBe(true);
    const areas = screen.getByRole("navigation", { name: "Areas" });
    expect(within(areas).getByRole("link", { name: "Canvas" }).getAttribute("href")).toBe("/canvas");
    expect(screen.queryByRole("menu", { name: "Designs" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Open a design" }));
    const menu = await screen.findByRole("menu", { name: "Designs" });
    await within(menu).findByRole("menuitem", { name: /corp-auth/ });
    const items = within(menu).getAllByRole("menuitem");
    expect(items.map((a) => a.textContent)).toEqual(["New design", "Open…", "corp-authauthored", expect.stringContaining("corp-cast"), "All designs"]);
    expect(items.map((a) => a.getAttribute("href"))).toEqual(["/canvas/new", null, "/canvas/corp-auth", "/canvas/corp-cast", "/canvas"]);
    expect(items[3]!.textContent).toContain("cast from");
    // Picking one closes the menu and opens it on the canvas: the canvas is the
    // page, so the bar names the open design and there is no sub-bar.
    fireEvent.click(items[2]!);
    await screen.findByRole("heading", { name: "corp-auth" });
    expect(screen.queryByRole("menu", { name: "Designs" })).toBeNull();
    expect(screen.queryByRole("navigation", { name: "Canvas" })).toBeNull();
    expect(within(areas).getByRole("link", { name: "Canvas" }).className).toContain("active");
  });

  it("closes on Escape and says when the catalog is empty", async () => {
    const { api } = fakeApi();
    renderAt(api, "/discovery");
    fireEvent.click(screen.getByRole("button", { name: "Open a design" }));
    await screen.findByText("No twin in the catalog yet.");
    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("menu", { name: "Designs" })).toBeNull());
    expect(screen.queryByRole("navigation", { name: "Canvas" })).toBeNull();
  });
});

describe("the designs page", () => {
  it("lists every twin with its origin and an Open link, and the imprints not yet cast", async () => {
    const { api } = seeded();
    renderAt(api, "/canvas");
    await screen.findByRole("heading", { name: "Canvas" });
    const rows = await screen.findAllByTestId("design-row");
    expect(rows).toHaveLength(2);
    expect(within(rows[0]!).getByRole("link", { name: "corp-auth" }).getAttribute("href")).toBe("/canvas/corp-auth");
    expect(within(rows[0]!).getByText("authored")).toBeTruthy();
    expect(within(rows[1]!).getByRole("link", { name: "corp capture" }).getAttribute("href")).toBe("/imprints/pit-1");
    expect(within(rows[1]!).getByRole("link", { name: "Open corp-cast on the canvas" }).getAttribute("href")).toBe("/canvas/corp-cast");
    expect(screen.getByRole("button", { name: "New design" }).getAttribute("href")).toBe("/canvas/new");
    await screen.findByRole("heading", { name: "Imprints not yet cast" });
    expect(screen.getByRole("link", { name: "branch capture" }).getAttribute("href")).toBe("/imprints/pit-2");
    expect(screen.queryByRole("link", { name: "corp capture" })).not.toBeNull();
    const sub = screen.getByRole("navigation", { name: "Canvas" });
    expect(within(sub).getByRole("link", { name: "Designs" }).className).toContain("active");
    expect(within(sub).getByRole("link", { name: "New design" }).className).not.toContain("active");
  });

  it("says so when there is nothing to open", async () => {
    const { api } = fakeApi();
    renderAt(api, "/canvas");
    await screen.findByText("No twin to open yet. Cast an imprint, or start a new design.");
  });
});

describe("the blank design document", () => {
  it("is no segment and no nodes (the untitled start), or one segment, with a manifest claiming no containment", () => {
    const empty = blankDesignFiles({ id: "untitled" });
    expect(Object.keys(empty).sort()).toEqual(["te.yaml", "twin.yaml"]);
    const none = readTe(empty["te.yaml"]!);
    expect(none.id).toBe("untitled");
    expect(none.segments).toEqual([]);
    expect(none.nodes).toEqual([]);
    expect(none.problems).toEqual([]);
    const files = blankDesignFiles({ id: "my-lab", description: 'A "quoted" lab', segment: { name: "lan", cidr: "10.0.0.0/24" } });
    const te = readTe(files["te.yaml"]!);
    expect(te.description).toBe('A "quoted" lab');
    expect(te.segments.map((s) => [s.name, s.cidr, s.zoneType])).toEqual([["lan", "10.0.0.0/24", "vlan"]]);
    expect(files["twin.yaml"]).toContain("containment: uncontained");
    expect(files["twin.yaml"]).toContain("id: my-lab");
    expect(files["twin.yaml"]).toContain("default: te.yaml");
  });

  it("refuses a bad or taken id, and a Save-As rewrites the ids and the description", () => {
    expect(designIdProblem("", new Set())).toMatch(/give the design an id/);
    expect(designIdProblem("-x", new Set())).toMatch(/the id must/);
    expect(designIdProblem("corp-auth", new Set(["corp-auth"]))).toMatch(/already in the catalog/);
    expect(designIdProblem("corp-two", new Set(["corp-auth"]))).toBeNull();
    const copy = saveAsFiles({ id: DOC.id, teFile: DOC.te_file, files: DOC.files }, "corp-two", { description: "the second" });
    expect(readTe(copy["te.yaml"]!).id).toBe("corp-two");
    expect(readTe(copy["te.yaml"]!).description).toBe("the second");
    expect(readTe(copy["te.yaml"]!).nodes.map((n) => n.name)).toEqual(["ws01"]);
    expect(copy["twin.yaml"]).toContain("id: corp-two");
    // The manifest had no description: one is added under `twin:`.
    expect(copy["twin.yaml"]).toContain("description: the second");
    // No description given: only the ids change.
    expect(readTe(saveAsFiles({ id: DOC.id, teFile: DOC.te_file, files: DOC.files }, "corp-three")["te.yaml"]!).description).toBe("an authored twin");
  });
});

describe("the canvas routes", () => {
  it("opens the same canvas at /canvas/:id and at /twins/:id/canvas", async () => {
    const { api } = seeded();
    const a = renderAt(api, "/canvas/corp-auth");
    await screen.findByRole("heading", { name: "corp-auth" });
    // The canvas is the page: no sub-bar, no centred column; the title links the twin's summary page.
    expect(screen.queryByRole("navigation", { name: "Canvas" })).toBeNull();
    expect(document.querySelector("main.main")).toBeNull();
    expect(document.querySelector("main.canvas-main")).not.toBeNull();
    expect(screen.getByRole("heading", { name: "corp-auth" }).closest("header.topbar")).not.toBeNull();
    expect(screen.getByRole("link", { name: "corp-auth" }).getAttribute("href")).toBe("/twins/corp-auth");
    a.unmount();
    renderAt(api, "/twins/corp-auth/canvas");
    await screen.findByRole("heading", { name: "corp-auth" });
    expect(screen.getByRole("link", { name: "Canvas" }).className).toContain("active");
  });
});
