/**
 * The Canvas area of the console: the brand, the Canvas entry of the top menu
 * with its drop-down of designs, the designs page, the new-design page that
 * writes a blank or copied twin-config through A3, and the `/canvas/:id` route.
 */
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { Imprint, TwinConfigDocument } from "../src/api/types";
import { blankDesignFiles, blankDesignProblem, copiedDesignFiles } from "../src/areas/canvas/newDesign";
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
    const items = await within(menu).findAllByRole("menuitem");
    expect(items.map((a) => a.getAttribute("href"))).toEqual(["/canvas/new", "/canvas/corp-auth", "/canvas/corp-cast", "/canvas"]);
    expect(items[1]!.textContent).toBe("corp-authauthored");
    expect(items[2]!.textContent).toContain("corp-cast");
    expect(items[2]!.textContent).toContain("cast from");
    // Picking one closes the menu and opens it on the canvas; the sub-bar names the open design.
    fireEvent.click(items[1]!);
    await screen.findByRole("heading", { name: /Twin corp-auth · canvas/ });
    expect(screen.queryByRole("menu", { name: "Designs" })).toBeNull();
    const sub = screen.getByRole("navigation", { name: "Canvas" });
    expect(sub.textContent).toBe("DesignsNew designcorp-auth");
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
  it("is one segment, no nodes, and a manifest claiming no containment", () => {
    const files = blankDesignFiles({ id: "my-lab", description: 'A "quoted" lab', segment: "lan", cidr: "10.0.0.0/24" });
    expect(Object.keys(files).sort()).toEqual(["te.yaml", "twin.yaml"]);
    const te = readTe(files["te.yaml"]!);
    expect(te.id).toBe("my-lab");
    expect(te.description).toBe('A "quoted" lab');
    expect(te.segments.map((s) => [s.name, s.cidr, s.zoneType])).toEqual([["lan", "10.0.0.0/24", "vlan"]]);
    expect(te.nodes).toEqual([]);
    expect(te.problems).toEqual([]);
    expect(files["twin.yaml"]).toContain("containment: uncontained");
    expect(files["twin.yaml"]).toContain("id: my-lab");
    expect(files["twin.yaml"]).toContain("default: te.yaml");
  });

  it("refuses a bad id, segment name or network, and copies take the new id", () => {
    expect(blankDesignProblem({ id: "-x", description: "", segment: "lan", cidr: "10.0.0.0/24" })).toMatch(/the id must/);
    expect(blankDesignProblem({ id: "x", description: "", segment: "a b", cidr: "10.0.0.0/24" })).toMatch(/segment name/);
    expect(blankDesignProblem({ id: "x", description: "", segment: "lan", cidr: "10.0.0.1/24" })).toMatch(/not an IPv4 network/);
    expect(blankDesignProblem({ id: "x", description: "", segment: "lan", cidr: "10.0.0.0/24" })).toBeNull();
    const copy = copiedDesignFiles(DOC, "corp-two");
    expect(readTe(copy["te.yaml"]!).id).toBe("corp-two");
    expect(readTe(copy["te.yaml"]!).nodes.map((n) => n.name)).toEqual(["ws01"]);
    expect(copy["twin.yaml"]).toContain("id: corp-two");
  });
});

describe("the new-design page", () => {
  it("creates a blank design through A3 and opens it on the canvas", async () => {
    const { api, state } = seeded();
    renderAt(api, "/canvas/new");
    await screen.findByRole("heading", { name: "New design" });
    const create = screen.getByRole("button", { name: "Create and open" }) as HTMLButtonElement;
    expect(create.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("Id"), { target: { value: "bad id" } });
    await screen.findByTestId("new-design-problem");
    expect(create.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("Id"), { target: { value: "my-lab" } });
    fireEvent.change(screen.getByLabelText("Description"), { target: { value: "a lab" } });
    fireEvent.change(screen.getByLabelText("First segment"), { target: { value: "office" } });
    fireEvent.change(screen.getByLabelText("Its network"), { target: { value: "10.9.0.0/24" } });
    await waitFor(() => expect(create.disabled).toBe(false));
    fireEvent.click(create);
    await screen.findByRole("heading", { name: /Twin my-lab · canvas/ });
    const write = state.writes.find((w) => w.op === "create-twin-config");
    expect(write?.id).toBe("my-lab");
    const files = (write!.body as { files: Record<string, string> }).files;
    const te = readTe(files["te.yaml"]!);
    expect(te.description).toBe("a lab");
    expect(te.segments.map((s) => [s.name, s.cidr])).toEqual([["office", "10.9.0.0/24"]]);
    expect(te.nodes).toEqual([]);
    expect(state.documents["my-lab"]).toBeTruthy();
    // The sub-bar names it, and the design is now in the catalog the menu lists.
    expect(screen.getByRole("navigation", { name: "Canvas" }).textContent).toBe("DesignsNew designmy-lab");
  });

  it("copies an existing twin-config under the new id", async () => {
    const { api, state } = seeded();
    renderAt(api, "/canvas/new");
    await screen.findByRole("option", { name: "Copy of corp-auth" });
    fireEvent.change(screen.getByLabelText("Start from"), { target: { value: "corp-auth" } });
    expect(screen.queryByLabelText("First segment")).toBeNull();
    fireEvent.change(screen.getByLabelText("Id"), { target: { value: "corp-two" } });
    fireEvent.click(screen.getByRole("button", { name: "Create and open" }));
    await screen.findByRole("heading", { name: /Twin corp-two · canvas/ });
    const write = state.writes.find((w) => w.op === "create-twin-config");
    const files = (write!.body as { files: Record<string, string> }).files;
    expect(readTe(files["te.yaml"]!).id).toBe("corp-two");
    expect(readTe(files["te.yaml"]!).nodes.map((n) => n.name)).toEqual(["ws01"]);
  });

  it("shows the API's refusal in place and stays on the form", async () => {
    const { api } = seeded();
    renderAt(api, "/canvas/new");
    await screen.findByRole("heading", { name: "New design" });
    fireEvent.change(screen.getByLabelText("Id"), { target: { value: "corp-auth" } });
    fireEvent.click(screen.getByRole("button", { name: "Create and open" }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("already exists");
    expect(screen.getByRole("heading", { name: "New design" })).toBeTruthy();
  });
});

describe("the canvas routes", () => {
  it("opens the same canvas at /canvas/:id and at /twins/:id/canvas", async () => {
    const { api } = seeded();
    const a = renderAt(api, "/canvas/corp-auth");
    await screen.findByRole("heading", { name: /Twin corp-auth · canvas/ });
    expect(screen.getByRole("navigation", { name: "Canvas" }).textContent).toBe("DesignsNew designcorp-auth");
    a.unmount();
    renderAt(api, "/twins/corp-auth/canvas");
    await screen.findByRole("heading", { name: /Twin corp-auth · canvas/ });
    expect(screen.getByRole("link", { name: "Canvas" }).className).toContain("active");
  });
});
