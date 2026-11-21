/**
 * The canvas in the console's shell: XTwin (the brand, the home link to the
 * untitled canvas at `/`), no Canvas menu, the blank design document, and the
 * `/canvas/:id` route. The untitled canvas itself and Open are
 * `canvas.untitled.test.tsx`.
 */
import { fireEvent, screen, within } from "@testing-library/react";
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

describe("XTwin, the home link", () => {
  it("is the brand -- the X mark and \"Twin\" -- linking home, the untitled canvas; there is no Canvas menu", async () => {
    const { api } = seeded();
    renderAt(api, "/twins");
    const brand = screen.getByRole("link", { name: "XTwin: a new design on the canvas" });
    expect(brand.className).toBe("brand");
    expect(brand.getAttribute("href")).toBe("/");
    expect(brand.textContent).toBe("Twin");
    expect(brand.querySelector("svg.brand-x")).not.toBeNull();
    const areas = screen.getByRole("navigation", { name: "Areas" });
    expect(within(areas).queryByRole("link", { name: "Canvas" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Open a design" })).toBeNull();
    fireEvent.click(brand);
    // Home is the untitled canvas: the canvas is the page, so no sub-bar and no centred column.
    await screen.findByRole("heading", { name: "Untitled" });
    await screen.findByTestId("rail-add");
    expect(document.querySelector("main.canvas-main")).not.toBeNull();
    expect(screen.queryByRole("navigation", { name: "Discovery" })).toBeNull();
  });

  it("opens / and /canvas/new on the untitled canvas; /canvas and an unknown path go home", async () => {
    const { api } = seeded();
    for (const path of ["/", "/canvas/new", "/canvas", "/nowhere"]) {
      const r = renderAt(api, path);
      await screen.findByRole("heading", { name: "Untitled" });
      r.unmount();
    }
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
    expect(screen.getByRole("navigation", { name: "Areas" }).querySelector("a.active")).toBeNull();
    a.unmount();
    renderAt(api, "/twins/corp-auth/canvas");
    await screen.findByRole("heading", { name: "corp-auth" });
    // Reached from a twin's page, Twins stays lit; /canvas/:id lights no area (no Canvas entry any more).
    expect(screen.getByRole("navigation", { name: "Areas" }).querySelector("a.active")?.textContent).toBe("Twins");
  });
});
