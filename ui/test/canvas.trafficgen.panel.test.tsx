/**
 * The selection panel's Traffic section for a node group (design
 * `docs/design/traffic-generation-trex-astf.md` §4.1, canvas task item 3):
 * Real · Emulated, the traffic lines table (rows only with emulated
 * instances, mixes filtered to the server end's role, inline reasons), the
 * derived badge and E-W / N-S tag, the real persona's "no traffic", and the
 * twin summary's planned cps and generators. Every edit writes tfc only.
 */
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { Persona } from "../src/api/types";
import { PERSONAS, commit, seeded, twinYaml } from "./helpers/canvasFixtures";
import { renderAt } from "./helpers/render";

const mix = (id: string, role: string, label: string): Persona => ({
  id, os_family: "linux", drive: "trex-astf", source: "catalog", requires: [], summary: id, category: "traffic", label,
  emits: [{ proto: "tcp", ports: [80], peer_role: role, transport: "tcp", cadence: "", volume: "", optional: false, peer_scope: "" }],
  mix: [{ app: "x", weight: 1 }],
});
const ALL = [...PERSONAS, mix("web-browse", "web-tier", "Web browsing"), mix("sql-oltp", "db-tier", "SQL OLTP"),
  { id: "web-tier", os_family: "linux", drive: "agent", source: "catalog", requires: [], summary: "", emits: [] } as Persona];

const TE = `twin_config:
  id: tg
  segments:
    - { name: internet, cidr: 10.250.0.0/24, gateway: 10.250.0.1 }
    - { name: web-lan, cidr: 10.72.0.0/24, gateway: 10.72.0.1 }
    - { name: db-lan, cidr: 10.73.0.0/24, gateway: 10.73.0.1 }
  nodes:
    - name: gw
      kind: router
      image: debian-12-cloud@v1
      nets: [internet, web-lan, db-lan]
    - name: web
      role: web-tier
      image: debian-12-cloud@v1
      count: 2
      net: web-lan
    - name: db
      role: db-tier
      image: debian-12-cloud@v1
      net: db-lan
`;
const TFC = `traffic-profile:
  target: tg
  assignments:
    - role: web-tier
      persona: web-tier
`;

function withTwin() {
  const base = seeded();
  const id = "tg";
  const s = seeded({
    personas: ALL,
    twins: { ...base.state.twins, [id]: { ...base.state.twins["corp-auth"]!, id } },
    documents: { ...base.state.documents, [id]: { id, te_file: "te.yaml", files: { "twin.yaml": twinYaml(id), "te.yaml": TE, "tfc/default.yaml": TFC }, skipped: [] } },
    twinConfigs: [...base.state.twinConfigs, { id, description: null, cluster: null, parameterized: false, param_names: [], error: null }],
  });
  return s;
}
const hostIcon = (name: string) => screen.getAllByTestId("canvas-host").find((h) => h.dataset["nodeId"] === name)!;
const options = (sel: HTMLElement) => Array.from((sel as HTMLSelectElement).options).map((o) => o.value);

afterEach(() => {
  localStorage.clear();
  sessionStorage.clear();
});

async function openGroup(name: string) {
  fireEvent.click(hostIcon(name));
  return within(await screen.findByTestId("node-panel"));
}

describe("the Traffic section: instances split and traffic lines", () => {
  it("Real · Emulated: Emulated writes tfc; a line is addable only with emulated instances; mixes follow the peer's role", async () => {
    const { api } = withTwin();
    renderAt(api, "/twins/tg/canvas");
    await screen.findAllByTestId("canvas-host");
    let panel = await openGroup("db");
    commit(panel.getByLabelText("Emulated instances"), "2");
    panel = await openGroup("web");
    expect(within(panel.getByTestId("instances")).getByLabelText("Count")).toHaveProperty("value", "2");
    // No emulated instances yet: the add row is disabled, with the reason.
    expect(panel.getByTestId("add-line-hint").textContent).toMatch(/needs emulated instances/);
    expect(panel.getByLabelText("New line peer")).toHaveProperty("disabled", true);
    // Real 0 is a hint, never written.
    commit(within(panel.getByTestId("instances")).getByLabelText("Count"), "0");
    expect(panel.getByTestId("real-zero-hint").textContent).toMatch(/te refuses/);
    commit(panel.getByLabelText("Emulated instances"), "4");
    await waitFor(() => expect(panel.queryByTestId("add-line-hint")).toBeNull());
    fireEvent.change(panel.getByLabelText("New line peer"), { target: { value: "db" } });
    expect(options(panel.getByLabelText("New line mix"))).toEqual(["", "sql-oltp"]);
    fireEvent.change(panel.getByLabelText("New line mix"), { target: { value: "sql-oltp" } });
    fireEvent.change(panel.getByLabelText("New line rate"), { target: { value: "20" } });
    fireEvent.click(panel.getByTestId("add-line-button"));
    const row = await panel.findByTestId("line-row");
    expect(row.dataset["line"]).toBe("web--db--sql-oltp");
    expect(within(row).getByTestId("line-tag").textContent).toBe("E-W");
    expect(panel.getByTestId("traffic-badge").textContent).toBe("client");
    expect(panel.queryByTestId("line-why")).toBeNull();
  });

  it("a row names the conflicting line when the group would need two generators", async () => {
    const { api, state } = withTwin();
    const tfc = `${TFC}  budget: { cps: 100, mbps: 60 }
  emulated:
    - { group: web, count: 4 }
    - { group: db, count: 1 }
    - { group: users, external: true, segment: internet, role: internet-user, count: 200 }
  lines:
    - { from: users, to: web, mix: web-browse, rate_cps: 30 }
    - { from: web, to: db, mix: sql-oltp, rate_cps: 20 }
`;
    state.documents["tg"]!.files["tfc/default.yaml"] = tfc;
    renderAt(api, "/twins/tg/canvas");
    await screen.findAllByTestId("canvas-host");
    const panel = await openGroup("web");
    expect(panel.getByTestId("traffic-badge").textContent).toBe("both");
    const tags = panel.getAllByTestId("line-tag").map((t) => t.textContent);
    expect(tags).toEqual(["N-S", "E-W"]);
    const why = panel.getByTestId("line-why");
    expect(why.dataset["line"]).toBe("web--db--sql-oltp");
    expect(why.textContent).toContain("web would need two generators: line 'users--web--web-browse' puts it on gen-internet--web-lan");
  });

  it("no traffic on a real group rewrites the role binding; the twin summary shows planned cps and generators", async () => {
    const { api, state } = withTwin();
    state.documents["tg"]!.files["tfc/default.yaml"] = `${TFC}  budget: { cps: 40, mbps: 60 }
  emulated:
    - { group: web, count: 4 }
    - { group: db, count: 1 }
  lines:
    - { from: web, to: db, mix: sql-oltp, rate_cps: 50 }
`;
    renderAt(api, "/twins/tg/canvas");
    await screen.findAllByTestId("canvas-host");
    const panel = await openGroup("web");
    const persona = panel.getByLabelText("Persona");
    expect(options(persona)).toContain("__no-traffic__");
    fireEvent.change(persona, { target: { value: "__no-traffic__" } });
    await waitFor(() => expect((panel.getByLabelText("Persona") as HTMLSelectElement).value).toBe("__no-traffic__"));
    expect(panel.getByText(/Real persona: no traffic/)).toBeTruthy();
    // Over budget: the row says so (TGN-040).
    expect(panel.getByTestId("line-why").textContent).toContain("TGN-040");
    // The twin summary (nothing selected).
    fireEvent.keyDown(window, { key: "Escape" });
    const summary = await screen.findByTestId("traffic-health");
    expect(summary.textContent).toBe("1 line · planned 50 cps of 40 budget (over budget) · 1 generator planned");
  });
});

describe("Internet users / services from the Add panel (item 4)", () => {
  it("adds an external group on the internet segment, hatched, selectable, and a peer in the lines table", async () => {
    const { api } = withTwin();
    renderAt(api, "/twins/tg/canvas");
    await screen.findAllByTestId("canvas-host");
    const { addTool } = await import("./helpers/canvasFixtures");
    fireEvent.click(addTool("internet-users"));
    const pop = within(await screen.findByTestId("drop-popover"));
    fireEvent.change(pop.getByTestId("drop-external-count"), { target: { value: "200" } });
    fireEvent.click(pop.getByRole("button", { name: /^Add/ }));
    const ext = await waitFor(() => {
      const h = screen.getAllByTestId("canvas-host").find((x) => x.dataset["nodeId"] === "internet-users");
      if (!h) throw new Error("not drawn yet");
      return h;
    });
    expect(ext.dataset["emulated"]).toBe("external");
    expect(within(ext).getByTestId("emulated-count").textContent).toBe("×200");
    // Selected: its own panel (count, lines, remove).
    fireEvent.click(ext);
    const { tab } = await import("./helpers/canvasFixtures");
    tab("Selection");
    const panel = within(await screen.findByTestId("external-panel"));
    expect((panel.getByLabelText("Emulated instances") as HTMLInputElement).value).toBe("200");
    // A peer in a group's lines table.
    const web = await openGroup("web");
    commit(web.getByLabelText("Emulated instances"), "4");
    await waitFor(() => expect(web.queryByTestId("add-line-hint")).toBeNull());
    expect(options(web.getByLabelText("New line peer"))).toContain("internet-users");
    fireEvent.change(web.getByLabelText("New line direction"), { target: { value: "serves" } });
    fireEvent.change(web.getByLabelText("New line peer"), { target: { value: "internet-users" } });
    expect(options(web.getByLabelText("New line mix"))).toEqual(["", "web-browse"]);
    fireEvent.change(web.getByLabelText("New line mix"), { target: { value: "web-browse" } });
    fireEvent.change(web.getByLabelText("New line rate"), { target: { value: "30" } });
    fireEvent.click(web.getByTestId("add-line-button"));
    const row = await web.findByTestId("line-row");
    expect(row.dataset["line"]).toBe("internet-users--web--web-browse");
    expect(within(row).getByTestId("line-tag").textContent).toBe("N-S");
    expect(web.getByTestId("traffic-badge").textContent).toBe("server");
  });
});
