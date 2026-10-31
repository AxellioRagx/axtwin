/**
 * C3 on the design canvas (`ui/src/features/edit-canvas/SPEC.md`, Phase 2):
 * C3.1–C3.6 as written in `ui/Spec.md`, undo byte-equivalence, the API's
 * live / re-plan classification driving Apply, promote-to-assertion
 * re-opening a working copy whose baseline carries the change, N copies
 * named `{name}-{n}`, and attach allocating a segment-appropriate address.
 *
 * jsdom measures nothing: the tests read the document through what the page
 * shows and what the fake control plane recorded, never pixels.
 */
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { DeploySummary, Imprint, Persona, TopologyExport, TwinConfigDocument, CastRoster } from "../src/api/types";
import { segmentAt, connectionToAttach } from "../src/features/edit-canvas/Canvas";
import { paletteItems, standingDeploy } from "../src/features/edit-canvas/CanvasPage";
import { readTe } from "../src/features/edit-canvas/edit/te";
import { classify, fakeApi, type FakeState } from "./helpers/fakeApi";
import { twin } from "./imprints.page.test";
import { renderAt } from "./helpers/render";

// ---------------------------------------------------------------------------
// Fixtures: one small twin-config bundle, under three ids.
// ---------------------------------------------------------------------------
function teText(id: string): string {
  return `# ${id} -- a test twin
twin_config:
  id: ${id}
  description: a small twin for the canvas tests
  segments:
    - { name: net-lan, cidr: 10.1.0.0/24, zone_type: vlan }   # the LAN
    - { name: net-dmz, cidr: 10.1.1.0/24, zone_type: vlan }

  nodes:
    - name: gw
      role: gw
      kind: router
      image: debian-12-cloud@v1
      nets:
        - { segment: net-lan }
        - { segment: net-dmz }

    - name: ws01
      role: corp-workstation
      image: win11-finance@v4
      net: net-lan

    - name: web01
      role: web-tier
      image: ubuntu-server-22@v5
      net: net-dmz
`;
}

function twinYaml(id: string): string {
  return `twin:\n  id: ${id}\n  tiers:\n    te:\n      default: te.yaml\n    tfc:\n      default: tfc/default.yaml\n`;
}

const TFC = `traffic-profile:\n  target: test\n  assignments:\n    - role: corp-workstation\n      persona: corp-workstation\n`;

function document(id: string): TwinConfigDocument {
  return { id, te_file: "te.yaml", files: { "te.yaml": teText(id), "twin.yaml": twinYaml(id), "tfc/default.yaml": TFC }, skipped: [] };
}

const PERSONAS: Persona[] = [
  { id: "corp-workstation", os_family: "windows", drive: "browser", source: "catalog", requires: [], summary: "office worker", emits: [] },
  { id: "generic-endpoint", os_family: "linux", drive: "shell", source: "catalog", requires: [], summary: "chatter", emits: [] },
];

const DEPLOY: DeploySummary = { deploy_id: "dep-1", scenario_id: "corp-live", status: "completed", started_at: 1700000000, updated_at: 1700000100, tier: "te", target: "dev45" };
const IMPRINT: Imprint = { pit_id: "pit-1", name: "corp capture", derived_from: null, has_assertions: false, twins: [{ id: "corp-cast" }] };
const CAST_EXPORT: TopologyExport = {
  imprint_id: "corp-cast", pit_id: "pit-1", as_of: 1, network: "corp", vantage: "hq", windows: 1, schema_version: "1",
  segments: [{ cidr: "10.80.20.0/24", name: "net-lan", entities: 1, coverage_source: null }],
  nodes: [{ id: "address:10.80.20.4", address: "10.80.20.4", locality: "local", segment: "10.80.20.0/24", segment_evidence_class: "observed", role: "corp-workstation", role_confidence: 0.9, role_evidence_class: "observed", windows_seen: 1, listen_ports: [], dial_ports: [] }],
  edges: [], census: { entities_total: 1, with_role: 1, placed: 1, local: 1 },
};

/** The cast roster of `corp-cast`: the group `ws01` collapsed two observed hosts, `web01` one;
 *  `net-lan` was relocated from the observed 10.80.20.0/24, `net-dmz` observed as deployed. */
const CAST_ROSTER: CastRoster = {
  id: "corp-cast", pit_id: "pit-1",
  node_roster: [
    { name: "gw", kind: "router", role: "gw", count: 1, segments: ["net-lan", "net-dmz"], node_ids: ["gw"], members: [] },
    { name: "ws01", kind: "host", role: "corp-workstation", count: 1, segments: ["net-lan"], node_ids: ["ws01"], members: ["address:10.80.20.4", "hostname:fin-ws"] },
    { name: "web01", kind: "host", role: "web-tier", count: 1, segments: ["net-dmz"], node_ids: ["web01"], members: ["hostname:web-a"] },
  ],
  segment_roster: [
    { name: "net-lan", cidr: "10.1.0.0/24", zone_type: "vlan", observed_cidr: "10.80.20.0/24" },
    { name: "net-dmz", cidr: "10.1.1.0/24", zone_type: "vlan", observed_cidr: "10.1.1.0/24" },
  ],
};

function seeded(over: Partial<FakeState> = {}) {
  return fakeApi({
    twins: { "corp-auth": twin("corp-auth", { hosts: 2, segments: 2 }), "corp-live": twin("corp-live", { hosts: 2, segments: 2 }), "corp-cast": twin("corp-cast", { hosts: 2, segments: 2, pit_id: "pit-1" }) },
    documents: { "corp-auth": document("corp-auth"), "corp-live": document("corp-live"), "corp-cast": document("corp-cast") },
    twinConfigs: [
      { id: "corp-auth", description: null, cluster: null, parameterized: false, param_names: [], error: null },
      { id: "corp-live", description: null, cluster: null, parameterized: false, param_names: [], error: null },
    ],
    deploys: [DEPLOY],
    personas: PERSONAS,
    imprints: [IMPRINT],
    exports: { "corp-cast": CAST_EXPORT },
    casts: { "corp-cast": CAST_ROSTER },
    ...over,
  });
}

const hosts = () => screen.getAllByTestId("canvas-host").map((h) => h.dataset["nodeId"]).sort();
const segments = () => screen.getAllByTestId("canvas-segment").map((s) => s.dataset["name"]).sort();
const host = (name: string) => screen.getAllByTestId("canvas-host").find((h) => h.dataset["nodeId"] === name)!;
const segment = (name: string) => screen.getAllByTestId("canvas-segment").find((s) => s.dataset["name"] === name)!;
const panel = () => within(screen.getByTestId("side-panel"));
const item = (kind: string, id: string) => screen.getAllByTestId("palette-item").find((i) => i.dataset["kind"] === kind && i.dataset["id"] === id)!;
const commit = (input: HTMLElement, value: string) => {
  fireEvent.change(input, { target: { value } });
  fireEvent.keyDown(input, { key: "Enter" });
};
const settle = () => act(() => new Promise((r) => setTimeout(r, 350)));
const enabled = async (name: string) => {
  await waitFor(() => expect(screen.getByRole("button", { name }).hasAttribute("disabled")).toBe(false));
  return screen.getByRole("button", { name });
};

afterEach(() => {
  localStorage.clear();
});

describe("the design canvas, phase 2 (C3)", () => {
  it("C3.1: adds a segment and a node on an undeployed config; the editor shows the config valid; Save writes it", async () => {
    const { api, state } = seeded();
    renderAt(api, "/twins/corp-auth/canvas");
    await screen.findAllByTestId("canvas-host");
    expect(screen.getByTestId("mode").textContent).toContain("edit mode · undeployed");
    expect(screen.getByTestId("clean-marker").textContent).toBe("saved");
    expect(segments()).toEqual(["net-dmz", "net-lan"]);

    fireEvent.click(item("segment", "segment"));
    await waitFor(() => expect(segments()).toEqual(["net-dmz", "net-lan", "segment-3"]));
    expect(segment("segment-3").dataset["cidr"]).toBe("10.1.2.0/24"); // the next free /24 under the twin's prefix
    expect(screen.getByTestId("dirty-marker").textContent).toContain("unsaved changes");

    // Select the new segment, then click a node group from the palette: click-to-place.
    fireEvent.click(segment("segment-3"));
    await waitFor(() => expect(panel().getByTestId("segment-panel")).toBeTruthy());
    fireEvent.click(item("group", "corp-auth/web01"));
    await waitFor(() => expect(hosts()).toEqual(["web01", "web01-1", "ws01"]));
    expect(within(host("web01-1")).getByTestId("chip-address").textContent).toBe("10.1.2.10");
    await waitFor(() => expect(screen.getByTestId("validation-state").textContent).toBe("valid"));

    fireEvent.click(await enabled("Save"));
    await screen.findByTestId("clean-marker");
    const write = state.writes.find((w) => w.op === "put-twin-config")!;
    expect(write.id).toBe("corp-auth");
    const te = readTe((write.body as { files: Record<string, string> }).files["te.yaml"]!);
    expect(te.segments.map((s) => s.name)).toEqual(["net-lan", "net-dmz", "segment-3"]);
    expect(te.nodes.find((n) => n.name === "web01-1")).toMatchObject({ role: "web-tier", image: "ubuntu-server-22@v5", nets: [{ segment: "segment-3", ip: "10.1.2.10" }] });
    // The lines the operator never touched are byte-identical.
    const original = teText("corp-auth").split("\n");
    const saved = (write.body as { files: Record<string, string> }).files["te.yaml"]!.split("\n");
    for (const line of original) expect(saved).toContain(line);
  });

  it("C3.2: duplicates a section onto a target segment with segment-appropriate addressing, still valid", async () => {
    const { api } = seeded();
    renderAt(api, "/twins/corp-auth/canvas");
    await screen.findAllByTestId("canvas-host");
    fireEvent.click(host("ws01"));
    const p = await panel().findByTestId("node-panel");
    fireEvent.change(within(p).getByLabelText("Duplicate onto segment"), { target: { value: "net-dmz" } });
    fireEvent.click(within(p).getByRole("button", { name: "Duplicate" }));
    await waitFor(() => expect(hosts()).toEqual(["web01", "ws01", "ws01-1"]));
    expect(within(host("ws01-1")).getByTestId("chip-address").textContent).toBe("10.1.1.10");
    await waitFor(() => expect(screen.getByTestId("validation-state").textContent).toBe("valid"));
    // Again: the next free address, not the same one.
    fireEvent.click(host("ws01"));
    fireEvent.change(within(await panel().findByTestId("node-panel")).getByLabelText("Duplicate onto segment"), { target: { value: "net-dmz" } });
    fireEvent.click(within(panel().getByTestId("node-panel")).getByRole("button", { name: "Duplicate" }));
    await waitFor(() => expect(hosts()).toEqual(["web01", "ws01", "ws01-1", "ws01-2"]));
    expect(within(host("ws01-2")).getByTestId("chip-address").textContent).toBe("10.1.1.11");
  });

  it("C3.3: Save-As from an undeployed config and from a working copy appears in the list and reopens identical; a taken name is refused", async () => {
    const { api, state } = seeded();
    const r = renderAt(api, "/twins/corp-auth/canvas");
    await screen.findAllByTestId("canvas-host");
    fireEvent.click(item("segment", "segment"));
    await waitFor(() => expect(segments().length).toBe(3));
    fireEvent.change(screen.getByLabelText("Save as id"), { target: { value: "corp-auth-2" } });
    fireEvent.click(await enabled("Save as"));
    await waitFor(() => expect(state.writes.some((w) => w.op === "create-twin-config")).toBe(true));
    expect((await api.listTwinConfigs()).twin_configs.map((c) => c.id)).toContain("corp-auth-2");
    const created = state.documents["corp-auth-2"]!;
    expect(readTe(created.files["te.yaml"]!).id).toBe("corp-auth-2");
    expect(readTe(created.files["te.yaml"]!).segments.length).toBe(3);
    // It reopens as its own canvas, identical.
    await screen.findByText(/Twin corp-auth-2/);
    await waitFor(() => expect(segments().length).toBe(3));
    expect(screen.getByTestId("clean-marker")).toBeTruthy();
    r.unmount();

    // A taken name is refused with the reason, nothing overwritten.
    const r2 = renderAt(api, "/twins/corp-auth/canvas");
    await screen.findAllByTestId("canvas-host");
    fireEvent.change(screen.getByLabelText("Save as id"), { target: { value: "corp-live" } });
    fireEvent.click(await enabled("Save as"));
    await screen.findByText(/twin-config 'corp-live' already exists; Save-As never overwrites/);
    expect(readTe(state.documents["corp-live"]!.files["te.yaml"]!).segments.length).toBe(2);
    r2.unmount();

    // From a deployed twin's working copy.
    renderAt(api, "/twins/corp-live/canvas");
    await screen.findAllByTestId("canvas-host");
    fireEvent.click(item("segment", "segment"));
    await waitFor(() => expect(segments().length).toBe(3));
    await settle();
    fireEvent.change(screen.getByLabelText("Save as id"), { target: { value: "corp-live-copy" } });
    fireEvent.click(await enabled("Save as"));
    await waitFor(() => expect(state.documents["corp-live-copy"]).toBeTruthy());
    expect(readTe(state.documents["corp-live-copy"]!.files["te.yaml"]!).segments.length).toBe(3);
    // The baseline, the deploy and its working copy are untouched by a Save-As.
    expect(readTe(state.documents["corp-live"]!.files["te.yaml"]!).segments.length).toBe(2);
    expect((await api.getDeployConfig("dep-1")).dirty).toBe(true);
  });

  it("C3.4: an invalid edit is blocked with the validator's reason, and the API's refusal of a working copy reverts the edit", async () => {
    const { api, state } = seeded();
    const r = renderAt(api, "/twins/corp-auth/canvas");
    await screen.findAllByTestId("canvas-host");
    fireEvent.click(segment("net-dmz"));
    const p = await panel().findByTestId("segment-panel");
    commit(within(p).getByLabelText("CIDR"), "10.1.1.5/24");
    const blocked = await screen.findByTestId("blocked-reason");
    expect(blocked.textContent).toContain("CV-012");
    expect(blocked.textContent).toContain("'10.1.1.5/24' is not an IPv4 network address");
    expect(segment("net-dmz").dataset["cidr"]).toBe("10.1.1.0/24");
    expect(screen.getByTestId("clean-marker")).toBeTruthy();
    await waitFor(() => expect(screen.getByTestId("validation-state").textContent).toBe("valid"));
    // A node attached to a segment that is not there: the panel's attach list only offers real segments,
    // so the check is the removal of a segment with a pinned address elsewhere -- still valid; and a bad address.
    fireEvent.click(host("web01"));
    const np = await panel().findByTestId("node-panel");
    commit(within(np).getByLabelText("Address on net-dmz"), "10.9.9.9");
    expect((await screen.findByTestId("blocked-reason")).textContent).toContain("not a host address inside net-dmz");
    r.unmount();

    // The server's own refusal (A2.3): the PUT is refused, the edit reverted, the reason shown verbatim.
    const refused = seeded();
    renderAt(refused.api, "/twins/corp-live/canvas");
    await screen.findAllByTestId("canvas-host");
    refused.state.refuse.config = new (await import("../src/api/client")).ApiError({ kind: "unprocessable", status: 422, message: "twin-config 'corp-live' fails validation (1 error(s)): IMG-010 image 'win11-finance@v4' has no manifest", url: "" });
    fireEvent.click(host("ws01"));
    commit(within(await panel().findByTestId("node-panel")).getByLabelText("Role"), "finance-ws");
    await screen.findByText(/IMG-010 image 'win11-finance@v4' has no manifest/);
    await waitFor(() => expect(screen.getByTestId("clean-marker")).toBeTruthy());
    expect(state.writes.filter((w) => w.op === "put-deploy-config").length).toBe(0);
  });

  it("C3.5: editing a deployed twin shows a dirty marker and leaves the baseline unchanged until Persist", async () => {
    const { api, state } = seeded();
    renderAt(api, "/twins/corp-live/canvas");
    await screen.findAllByTestId("canvas-host");
    expect(screen.getByTestId("mode").textContent).toContain("edit mode · deploy dep-1 completed");
    expect(screen.getByTestId("clean-marker").textContent).toBe("working copy equals the baseline");
    fireEvent.click(host("ws01"));
    commit(within(await panel().findByTestId("node-panel")).getByLabelText("Role"), "finance-ws");
    await waitFor(() => expect(screen.getByTestId("dirty-marker").textContent).toContain("working copy dirty"));
    const put = state.writes.find((w) => w.op === "put-deploy-config")!;
    expect(put.id).toBe("dep-1");
    // The baseline is untouched: the catalog read is the original.
    expect((await api.getTwinConfig("corp-live")).document!.files["te.yaml"]).toBe(teText("corp-live"));
    expect(readTe((await api.getDeployConfig("dep-1")).document.files["te.yaml"]!).nodes.find((n) => n.name === "ws01")!.role).toBe("finance-ws");
    // Persist: the baseline becomes the working copy and the copy is clean.
    fireEvent.click(await enabled("Persist"));
    await screen.findByText("working copy equals the baseline");
    expect(readTe((await api.getTwinConfig("corp-live")).document!.files["te.yaml"]!).nodes.find((n) => n.name === "ws01")!.role).toBe("finance-ws");
    expect((await api.getDeployConfig("dep-1")).dirty).toBe(false);
  });

  it("C3.6: Reset clears the marker and shows baseline; a second console on the same deploy shows the same state", async () => {
    const { api } = seeded();
    const a = renderAt(api, "/twins/corp-live/canvas");
    const canvasA = a.container;
    await within(canvasA).findAllByTestId("canvas-host");
    fireEvent.click(within(canvasA).getAllByTestId("palette-item").find((i) => i.dataset["kind"] === "segment")!);
    await waitFor(() => expect(within(canvasA).getByTestId("dirty-marker")).toBeTruthy());
    await settle();
    expect((await api.getDeployConfig("dep-1")).dirty).toBe(true);

    // The second console reads the same working copy from the API.
    const b = renderAt(api, "/twins/corp-live/canvas");
    const canvasB = b.container;
    await within(canvasB).findAllByTestId("canvas-host");
    expect(within(canvasB).getByTestId("dirty-marker").textContent).toContain("working copy dirty");
    expect(within(canvasB).getAllByTestId("canvas-segment").length).toBe(3);

    fireEvent.click(within(canvasA).getByRole("button", { name: "Reset" }));
    await within(canvasA).findByText("working copy equals the baseline");
    await waitFor(() => expect(within(canvasA).getAllByTestId("canvas-segment").length).toBe(2));
    expect(within(canvasA).getByTestId("exit-note").textContent).toContain("reset to baseline");
    fireEvent.click(within(canvasB).getByRole("button", { name: "Refresh" }));
    await within(canvasB).findByText("working copy equals the baseline");
    await waitFor(() => expect(within(canvasB).getAllByTestId("canvas-segment").length).toBe(2));
  });

  it("undo returns the document byte-equivalent to the pre-edit text (Ctrl+Z / Ctrl+Y)", async () => {
    const { api, state } = seeded();
    renderAt(api, "/twins/corp-auth/canvas");
    await screen.findAllByTestId("canvas-host");
    fireEvent.click(item("segment", "segment"));
    await waitFor(() => expect(segments().length).toBe(3));
    fireEvent.click(host("ws01"));
    commit(within(await panel().findByTestId("node-panel")).getByLabelText("Role"), "finance-ws");
    await waitFor(() => expect(screen.getByRole("button", { name: "Undo" }).getAttribute("title")).toBe("undo: role of ws01"));
    fireEvent.keyDown(window, { key: "z", ctrlKey: true });
    fireEvent.keyDown(window, { key: "z", ctrlKey: true });
    await waitFor(() => expect(segments().length).toBe(2));
    // Clean means every file equals the loaded text, byte for byte.
    expect(screen.getByTestId("clean-marker").textContent).toBe("saved");
    fireEvent.keyDown(window, { key: "y", ctrlKey: true });
    await waitFor(() => expect(segments().length).toBe(3));
    fireEvent.click(await enabled("Save"));
    await waitFor(() => expect(state.writes.some((w) => w.op === "put-twin-config")).toBe(true));
    const files = (state.writes.find((w) => w.op === "put-twin-config")!.body as { files: Record<string, string> }).files;
    expect(files["te.yaml"]).toBe(teText("corp-auth").replace("    - { name: net-dmz, cidr: 10.1.1.0/24, zone_type: vlan }\n", "    - { name: net-dmz, cidr: 10.1.1.0/24, zone_type: vlan }\n    - { name: segment-3, cidr: 10.1.2.0/24, zone_type: vlan }\n"));
    expect(files["twin.yaml"]).toBe(twinYaml("corp-auth"));
  });

  it("a persona change on a deployed twin shows live and Apply is enabled; Apply reaches the twin", async () => {
    const { api, state } = seeded();
    renderAt(api, "/twins/corp-live/canvas");
    await screen.findAllByTestId("canvas-host");
    fireEvent.click(host("web01"));
    fireEvent.change(within(await panel().findByTestId("node-panel")).getByLabelText("Persona"), { target: { value: "generic-endpoint" } });
    await waitFor(() => expect(screen.getAllByTestId("pending-change").length).toBe(1));
    const change = screen.getByTestId("pending-change");
    expect(change.dataset["kind"]).toBe("attribute");
    expect(change.textContent).toContain("live");
    expect(change.textContent).toContain("tfc/default.yaml");
    const apply = screen.getByRole("button", { name: "Apply" });
    await waitFor(() => expect(apply.hasAttribute("disabled")).toBe(false));
    fireEvent.click(apply);
    await screen.findByText(/applied via tfc/);
    expect(state.writes.some((w) => w.op === "apply-deploy-config" && w.id === "dep-1")).toBe(true);
    expect((await api.getDeployConfig("dep-1")).applied?.tiers).toEqual(["tfc"]);
    // A role change with the image unchanged is live as well, naming the role.
    fireEvent.click(host("ws01"));
    commit(within(await panel().findByTestId("node-panel")).getByLabelText("Role"), "finance-ws");
    await waitFor(() => expect(screen.getAllByTestId("pending-change").length).toBe(2));
    expect(screen.getAllByTestId("pending-change").find((c) => c.textContent!.includes("te.yaml"))!.textContent).toContain("ws01 → finance-ws");
  });

  it("adding a segment on a deployed twin shows re-plan: Apply disabled, Persist and Save-As enabled, redeploy offered", async () => {
    const { api } = seeded();
    renderAt(api, "/twins/corp-live/canvas");
    await screen.findAllByTestId("canvas-host");
    fireEvent.click(item("segment", "segment"));
    await waitFor(() => expect(screen.getAllByTestId("pending-change").length).toBe(1));
    const change = screen.getByTestId("pending-change");
    expect(change.dataset["kind"]).toBe("topology");
    expect(change.textContent).toContain("re-plan");
    expect(change.textContent).toContain("the twin must be re-planned");
    await enabled("Persist");
    expect(screen.getByRole("button", { name: "Apply" }).hasAttribute("disabled")).toBe(true);
    fireEvent.change(screen.getByLabelText("Save as id"), { target: { value: "x" } });
    await enabled("Save as");
    expect(screen.getByTestId("redeploy-link").getAttribute("href")).toBe("/twins/corp-live");
  });

  it("promote-to-assertion on a cast twin re-opens a working copy whose baseline carries the change", async () => {
    const { api, state } = seeded({ deploys: [{ ...DEPLOY, deploy_id: "dep-2", scenario_id: "corp-cast" }] });
    renderAt(api, "/twins/corp-cast/canvas");
    await screen.findAllByTestId("canvas-host");
    // The canvas defaults to the design view of the document; the evidence view is a toggle.
    expect(hosts()).toEqual(["web01", "ws01"]);
    fireEvent.click(screen.getByRole("button", { name: "Evidence" }));
    await waitFor(() => expect(hosts()).toEqual(["address:10.80.20.4"]));
    fireEvent.click(screen.getByRole("button", { name: "Design" }));
    await waitFor(() => expect(hosts()).toEqual(["web01", "ws01"]));

    fireEvent.click(host("ws01"));
    commit(within(await panel().findByTestId("node-panel")).getByLabelText("Role"), "domain-controller");
    await waitFor(() => expect(screen.getAllByTestId("pending-change").length).toBe(1));
    const promote = await panel().findByTestId("promote-role");
    expect(promote.textContent).toBe("Promote to assertion: ws01 is a domain-controller");
    fireEvent.click(promote);
    await screen.findByText(/promoted: ws01 is a domain-controller; the twin was re-cast and the working copy re-opened/);
    const put = state.writes.find((w) => w.op === "put-assertions")!;
    expect(put.id).toBe("pit-1");
    // Keyed by the group's OBSERVED members off the cast roster, one record each -- never by the
    // node name, which is a cast group name the assertions seam cannot resolve.
    const note = "promoted from the design canvas: ws01 is a domain-controller";
    expect((put.body as { override: unknown[] }).override).toEqual([
      { key: "address:10.80.20.4", field: "role", value: "domain-controller", note },
      { key: "hostname:fin-ws", field: "role", value: "domain-controller", note },
    ]);
    expect(state.writes.some((w) => w.op === "cast" && (w.body as { replace: boolean }).replace === true)).toBe(true);
    // The baseline now carries the role, and the re-opened working copy has nothing pending.
    expect(readTe(state.documents["corp-cast"]!.files["te.yaml"]!).nodes.find((n) => n.name === "ws01")!.role).toBe("domain-controller");
    await waitFor(() => expect(screen.getByTestId("clean-marker")).toBeTruthy());
    expect(screen.getByTestId("pending-changes").textContent).toContain("none");
    // An authored twin has no imprint and no promote action.
  });

  it("promote is refused, with the reason, for a node the cast roster has no members for; a merge asserts observed networks", async () => {
    const roster: CastRoster = { ...CAST_ROSTER, node_roster: CAST_ROSTER.node_roster.map((n) => (n.name === "ws01" ? { ...n, members: [] } : n)) };
    const { api, state } = seeded({ deploys: [], casts: { "corp-cast": roster } });
    renderAt(api, "/twins/corp-cast/canvas");
    await screen.findAllByTestId("canvas-host");
    fireEvent.click(host("ws01"));
    commit(within(await panel().findByTestId("node-panel")).getByLabelText("Role"), "domain-controller");
    const promote = await panel().findByTestId("promote-role");
    expect(promote.hasAttribute("disabled")).toBe(true);
    expect(panel().getByTestId("promote-role-block").textContent).toContain("ws01 has no observed members in the cast");
    fireEvent.click(promote);
    expect(state.writes.some((w) => w.op === "put-assertions")).toBe(false);

    // A merge of net-dmz into net-lan: web01's observed member moves to net-lan's OBSERVED
    // network (10.80.20.0/24, not the deployed 10.1.0.0/24) and net-dmz's observed key is suppressed.
    fireEvent.click(segment("net-lan"));
    fireEvent.click(segment("net-dmz"), { shiftKey: true });
    const sp = await panel().findByTestId("segments-panel");
    fireEvent.change(within(sp).getByLabelText("Segment to merge away"), { target: { value: "net-dmz" } });
    const merge = await panel().findByTestId("promote-merge");
    expect(merge.hasAttribute("disabled")).toBe(false);
    fireEvent.click(merge);
    await screen.findByText(/promoted: net-dmz is net-lan/);
    const put = state.writes.find((w) => w.op === "put-assertions")!;
    const body = put.body as { override: Record<string, unknown>[]; suppress: Record<string, unknown>[] };
    const note = "promoted from the design canvas: net-dmz and net-lan are one segment";
    expect(body.override).toEqual([{ key: "hostname:web-a", field: "segment", value: "10.80.20.0/24", note }]);
    expect(body.suppress).toEqual([{ key: "10.1.1.0/24", note }]);
  });

  it("a twin with no imprint has no promote action; two selected segments offer a merge", async () => {
    const { api } = seeded();
    renderAt(api, "/twins/corp-auth/canvas");
    await screen.findAllByTestId("canvas-host");
    fireEvent.click(host("ws01"));
    commit(within(await panel().findByTestId("node-panel")).getByLabelText("Role"), "domain-controller");
    await waitFor(() => expect(screen.getByRole("button", { name: "Undo" }).hasAttribute("disabled")).toBe(false));
    expect(panel().queryByTestId("promote-role")).toBeNull();
    fireEvent.click(segment("net-lan"));
    fireEvent.click(segment("net-dmz"), { shiftKey: true });
    const sp = await panel().findByTestId("segments-panel");
    expect(panel().queryByTestId("promote-merge")).toBeNull();
    fireEvent.click(within(sp).getByTestId("merge-segments"));
    await waitFor(() => expect(segments()).toEqual(["net-lan"]));
    expect(hosts()).toEqual(["web01", "ws01"]);
    await waitFor(() => expect(screen.getByTestId("validation-state").textContent).toBe("valid"));
  });

  it("the palette places N copies named {name}-{n}, each on the next free address; a persona item binds the selected group", async () => {
    const { api } = seeded();
    renderAt(api, "/twins/corp-auth/canvas");
    await screen.findAllByTestId("canvas-host");
    fireEvent.change(screen.getByLabelText("Copies"), { target: { value: "3" } });
    fireEvent.click(segment("net-dmz"));
    await panel().findByTestId("segment-panel");
    fireEvent.click(item("image", "debian-12-cloud@v1"));
    await waitFor(() => expect(hosts()).toEqual(["debian-12-cloud-1", "debian-12-cloud-2", "debian-12-cloud-3", "web01", "ws01"]));
    expect(["debian-12-cloud-1", "debian-12-cloud-2", "debian-12-cloud-3"].map((n) => within(host(n)).getByTestId("chip-address").textContent)).toEqual(["10.1.1.10", "10.1.1.11", "10.1.1.12"]);
    // Without a selected segment an item arms click-to-place; Esc disarms (and Esc clears the selection).
    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() => expect(panel().queryByTestId("segment-panel")).toBeNull());
    fireEvent.click(item("image", "debian-12-cloud@v1"));
    expect(screen.getByText(/armed: debian-12-cloud@v1/)).toBeTruthy();
    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() => expect(screen.queryByText(/armed:/)).toBeNull());
    // A persona needs a selected group. (The category starts folded.)
    fireEvent.click(screen.getByRole("button", { name: /Personas/ }));
    fireEvent.click(item("persona", "generic-endpoint"));
    expect((await screen.findByTestId("blocked-reason")).textContent).toContain("select a node group first");
    fireEvent.click(host("web01"));
    await panel().findByTestId("node-panel");
    fireEvent.click(item("persona", "generic-endpoint"));
    await waitFor(() => expect((within(panel().getByTestId("node-panel")).getByLabelText("Persona") as HTMLSelectElement).value).toBe("generic-endpoint"));
    // The palette is searchable and categorised.
    fireEvent.change(screen.getByLabelText("Search the palette"), { target: { value: "ubuntu" } });
    // This twin's group first, then the other catalog twin's, then the image once.
    expect(screen.getAllByTestId("palette-item").map((i) => i.dataset["id"])).toEqual(["corp-auth/web01", "corp-live/web01", "ubuntu-server-22@v5"]);
  });

  it("attach allocates a segment-appropriate address: a second leg from the panel, a detach from the context menu, the sticky attach mode", async () => {
    const { api } = seeded();
    renderAt(api, "/twins/corp-auth/canvas");
    await screen.findAllByTestId("canvas-host");
    fireEvent.click(host("web01"));
    const p = await panel().findByTestId("node-panel");
    fireEvent.change(within(p).getByLabelText("Attach to segment"), { target: { value: "net-lan" } });
    fireEvent.click(within(p).getByRole("button", { name: "Attach" }));
    await waitFor(() => expect(within(panel().getByTestId("node-panel")).getAllByTestId("attachment").map((a) => a.dataset["segment"])).toEqual(["net-dmz", "net-lan"]));
    expect((within(panel().getByTestId("node-panel")).getByLabelText("Address on net-lan") as HTMLInputElement).value).toBe("10.1.0.10");
    // (The leg itself is an edge React Flow draws only once it has measured the handles, which jsdom never does.)
    // Right-click the chip: detach from the context menu.
    fireEvent.contextMenu(host("web01"));
    const menu = await screen.findByTestId("context-menu");
    fireEvent.click(within(menu).getByRole("button", { name: "Detach from net-lan" }));
    await waitFor(() => expect(within(panel().getByTestId("node-panel")).getAllByTestId("attachment").map((a) => a.dataset["segment"])).toEqual(["net-dmz"]));
    // Sticky attach mode: Attach, click a host, click a segment; stays armed until Esc.
    const tools = () => within(screen.getByLabelText("Edit tools"));
    fireEvent.click(tools().getByRole("button", { name: "Attach" }));
    fireEvent.click(host("ws01"));
    expect(screen.getByRole("button", { name: /Attach ws01 → click a segment/ })).toBeTruthy();
    fireEvent.click(segment("net-dmz"));
    await waitFor(() => expect(within(panel().getByTestId("node-panel")).getAllByTestId("attachment").map((a) => a.dataset["segment"])).toEqual(["net-lan", "net-dmz"]));
    expect((within(panel().getByTestId("node-panel")).getByLabelText("Address on net-dmz") as HTMLInputElement).value).toBe("10.1.1.10");
    expect(tools().getByRole("button", { name: "Attach: click a host" })).toBeTruthy();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(tools().getByRole("button", { name: "Attach" })).toBeTruthy();
    // A handle drag from a host to a segment bus is the same gesture.
    expect(connectionToAttach({ source: "host:ws01", target: "seg:net-dmz" })).toEqual({ node: "ws01", segment: "net-dmz" });
    expect(connectionToAttach({ source: "router:gw", target: "seg:net-dmz" })).toEqual({ node: "gw", segment: "net-dmz" });
    expect(connectionToAttach({ source: "seg:net-dmz", target: "host:ws01" })).toBeNull();
  });

  it("view mode while the deploy is running: locked except annotations, which go to the side-car", async () => {
    const { api, state } = seeded({ deploys: [{ ...DEPLOY, status: "running" }] });
    renderAt(api, "/twins/corp-live/canvas");
    await screen.findAllByTestId("canvas-host");
    expect(screen.getByTestId("mode").textContent).toContain("view mode · deploy dep-1 running");
    expect(screen.getByTestId("canvas").dataset["locked"]).toBe("true");
    expect(screen.getByTestId("canvas").dataset["mode"]).toBe("view");
    expect(screen.getByText(/view mode: the palette is read-only/)).toBeTruthy();
    fireEvent.click(host("ws01"));
    const p = await panel().findByTestId("node-panel");
    expect((within(p).getByLabelText("Role") as HTMLInputElement).disabled).toBe(true);
    expect(screen.getByRole("button", { name: "Apply" }).hasAttribute("disabled")).toBe(true);
    // Annotations still work.
    fireEvent.click(screen.getByRole("button", { name: "text" }));
    const ann = await screen.findByTestId("canvas-annotation");
    expect(ann.dataset["annKind"]).toBe("text");
    expect(ann.textContent).toContain("note");
    fireEvent.click(screen.getByRole("button", { name: "rect" }));
    await waitFor(() => expect(screen.getAllByTestId("canvas-annotation").length).toBe(2));
    fireEvent.click(screen.getByRole("button", { name: "Hide all" }));
    await waitFor(() => expect(screen.queryByTestId("canvas-annotation")).toBeNull());
    fireEvent.click(screen.getByRole("button", { name: "Show annotations" }));
    await waitFor(() => expect(screen.getAllByTestId("canvas-annotation").length).toBe(2));
    await waitFor(() => expect((state.layouts["corp-live"]?.annotations ?? []).length).toBe(2));
    expect(state.layouts["corp-live"]!.annotations.map((a) => a.kind)).toEqual(["text", "rect"]);
    expect(screen.getByText(/2 annotations in the layout side-car/)).toBeTruthy();
    expect(localStorage.getItem("axr-canvas-annotations:corp-live")).toBeNull();
  });
});

describe("the document validate gate and the side-car annotations", () => {
  it("every mutation is validated through POST /api/validate {document}: an error finding disables Save and is shown with its rule and place; a clean answer enables it", async () => {
    const { api, state } = seeded();
    renderAt(api, "/twins/corp-auth/canvas");
    await screen.findAllByTestId("canvas-host");
    await waitFor(() => expect(screen.getByTestId("validation-state").textContent).toBe("valid"));
    expect(state.writes.filter((w) => w.op === "validate").length).toBe(1);
    expect(screen.getByTestId("validation-api").textContent).toBe("API: pass · 0 errors, 0 warnings");
    // An image the manifests do not know passes the canvas's own check and fails the API's.
    fireEvent.click(host("web01"));
    const p = await panel().findByTestId("node-panel");
    fireEvent.change(within(p).getByLabelText("Image"), { target: { value: "ubuntu-server-99@v1" } });
    fireEvent.blur(within(p).getByLabelText("Image"));
    await waitFor(() => expect(screen.getByTestId("validation-state").textContent).toBe("invalid"));
    const finding = screen.getAllByTestId("finding").find((f) => f.dataset["rule"] === "IMG-010")!;
    expect(finding.textContent).toContain("image 'ubuntu-server-99@v1' has no manifest");
    expect(finding.textContent).toContain("twin_config.nodes[web01].image");
    expect(screen.getByTestId("validation-api").textContent).toBe("API: fail · 1 error, 0 warnings");
    expect(screen.getByRole("button", { name: "Save" }).hasAttribute("disabled")).toBe(true);
    fireEvent.change(screen.getByLabelText("Save as id"), { target: { value: "x" } });
    expect(screen.getByRole("button", { name: "Save as" }).hasAttribute("disabled")).toBe(true);
    const validated = state.writes.filter((w) => w.op === "validate");
    expect((validated.at(-1)!.body as { files: Record<string, string> }).files["te.yaml"]).toContain("ubuntu-server-99@v1");
    // Another known image: the next answer is clean and the exits open (the document still differs from the saved one).
    commit(within(panel().getByTestId("node-panel")).getByLabelText("Image"), "debian-12-cloud@v1");
    await waitFor(() => expect(screen.getByTestId("validation-state").textContent).toBe("valid"));
    fireEvent.click(await enabled("Save"));
    await screen.findByTestId("clean-marker");
    // The API's answer is tied to the files it judged: until the next answer lands the state is "validating", never a stale "valid".
    fireEvent.click(item("segment", "segment"));
    expect(screen.getByTestId("validation-state").textContent).toBe("validating");
    expect(screen.getByRole("button", { name: "Save" }).hasAttribute("disabled")).toBe(true);
    await waitFor(() => expect(screen.getByTestId("validation-state").textContent).toBe("valid"));
  });

  it("annotations round-trip through the layout document with the etag, and survive a reload", async () => {
    const { api, state } = seeded();
    const r = renderAt(api, "/twins/corp-auth/canvas");
    await screen.findAllByTestId("canvas-host");
    fireEvent.click(screen.getByRole("button", { name: "text" }));
    await screen.findByTestId("canvas-annotation");
    fireEvent.click(screen.getByRole("button", { name: "ellipse" }));
    await waitFor(() => expect(state.layouts["corp-auth"]?.annotations.length).toBe(2));
    const writes = state.writes.filter((w) => w.op === "put-layout");
    expect(writes.length).toBe(2);
    // The second write carried the first's etag as its precondition and both annotations as positioned SVG.
    expect((writes[0]!.body as { etag: string | null }).etag).toBeNull();
    expect(String((writes[1]!.body as { etag: string | null }).etag)).toMatch(/^layout-etag-\d+$/);
    const saved = state.layouts["corp-auth"]!.annotations;
    expect(saved[0]).toMatchObject({ kind: "text", locked: false, hidden: false, w: 160, h: 28 });
    expect(saved[0]!.svg).toMatch(/^<text x="-?[\d.]+" y="-?[\d.]+">note<\/text>$/);
    expect(saved[1]!.svg).toMatch(/^<ellipse cx=.* fill="none" stroke="currentColor"\/>$/);
    // Edit the text and lock it from the panel: each change is a write.
    fireEvent.click(screen.getAllByTestId("canvas-annotation")[0]!);
    const ap = await panel().findByTestId("annotation-panel");
    fireEvent.change(within(ap).getByLabelText("Annotation text"), { target: { value: "crown jewels <here> & onion = 3" } });
    fireEvent.click(within(ap).getByLabelText("locked"));
    await waitFor(() => expect(state.layouts["corp-auth"]!.annotations[0]!.locked).toBe(true));
    expect(state.layouts["corp-auth"]!.annotations[0]!.svg).toBe(`<text x="${saved[0]!.x}" y="${saved[0]!.y + 14}">crown jewels &lt;here&gt; &amp; onion &#61; 3</text>`);
    r.unmount();
    // A fresh console reads them back from the side-car, text included.
    renderAt(api, "/twins/corp-auth/canvas");
    await screen.findAllByTestId("canvas-host");
    await waitFor(() => expect(screen.getAllByTestId("canvas-annotation").length).toBe(2));
    const back = screen.getAllByTestId("canvas-annotation").find((a) => a.dataset["annKind"] === "text")!;
    expect(back.textContent).toContain("crown jewels <here> & onion = 3");
    expect(back.className).toContain("locked");
    expect(screen.getByText(/2 annotations in the layout side-car/)).toBeTruthy();
    // A pin write carries the annotations through untouched.
    const { nextLayoutWrite } = await import("../src/features/edit-canvas/CanvasPage");
    const doc = await api.getLayout("corp-auth");
    expect(nextLayoutWrite(doc, "corp-auth", "host:ws01", { x: 1, y: 2 }).annotations).toEqual(doc.annotations);
  });

  it("migrates a set an earlier build kept in this browser into the side-car once, then clears it", async () => {
    const { api, state } = seeded();
    localStorage.setItem("axr-canvas-annotations:corp-auth", JSON.stringify([{ id: "old-1", kind: "rect", x: 10, y: 20, w: 100, h: 50, text: "", locked: true }, { id: "old-2", kind: "text", x: 5, y: 5, w: 160, h: 28, text: "legacy note", locked: false }]));
    renderAt(api, "/twins/corp-auth/canvas");
    await screen.findAllByTestId("canvas-host");
    await waitFor(() => expect(state.layouts["corp-auth"]?.annotations.map((a) => a.id)).toEqual(["old-1", "old-2"]));
    expect(state.layouts["corp-auth"]!.annotations[0]).toMatchObject({ kind: "rect", x: 10, y: 20, w: 100, h: 50, locked: true, hidden: false });
    expect(state.layouts["corp-auth"]!.annotations[1]!.svg).toBe('<text x="5" y="19">legacy note</text>');
    expect(localStorage.getItem("axr-canvas-annotations:corp-auth")).toBeNull();
    await waitFor(() => expect(screen.getAllByTestId("canvas-annotation").length).toBe(2));
    expect(state.writes.filter((w) => w.op === "put-layout").length).toBe(1);
  });

  it("a snippet the server's sanitiser refuses shows the 400 in place; what the canvas emits never trips it", async () => {
    const { api, state } = seeded();
    // The fake applies the server's rules: these are refused by name, nothing stripped.
    const base = { schema: "axr-layout/1", imprint: "corp-auth", pins: {}, plane: "l0-1-test", etag: null };
    for (const [svg, what] of [["<rect onclick=\"x()\"/>", "an on*= event-handler attribute"], ["<a href=\"j\"><text>x</text></a>", "an href / xlink:href attribute"], ["<script>1</script>", "a <script> element"], ["<foreignObject/>", "a <foreignObject> element"]] as const) {
      await expect(api.putLayout("corp-auth", { ...base, annotations: [{ id: "a", kind: "rect", x: 0, y: 0, svg, locked: false, hidden: false }] })).rejects.toMatchObject({ status: 400, message: expect.stringContaining(what) });
    }
    await expect(api.putLayout("corp-auth", { ...base, annotations: [{ id: "bad id", kind: "rect", x: 0, y: 0, svg: "<rect/>", locked: false, hidden: false }] })).rejects.toMatchObject({ status: 400, message: expect.stringContaining("`id` must be 1-64 characters") });
    expect(state.layouts["corp-auth"]).toBeUndefined();
    // The canvas escapes every character that could spell one of them.
    const { toSvg } = await import("../src/features/edit-canvas/edit/annotations");
    const hostile = toSvg({ id: "t", kind: "text", x: 0, y: 0, w: 1, h: 1, text: "<script>onload=1 href=x</script>", locked: false, hidden: false });
    await api.putLayout("corp-auth", { ...base, annotations: [{ id: "t", kind: "text", x: 0, y: 0, svg: hostile, locked: false, hidden: false }] });
    expect(state.layouts["corp-auth"]!.annotations[0]!.svg).toBe('<text x="0" y="14">&lt;script&gt;onload&#61;1 href&#61;x&lt;/script&gt;</text>');
    // A refusal the server makes is surfaced on the page.
    renderAt(api, "/twins/corp-auth/canvas");
    await screen.findAllByTestId("canvas-host");
    state.refuse.layout = new (await import("../src/api/client")).ApiError({ kind: "bad-request", status: 400, message: "layout for 'corp-auth': annotations[1] `svg` carries a <script> element, which a layout annotation may not (the file is served back to browsers)", url: "" });
    fireEvent.click(screen.getByRole("button", { name: "rect" }));
    await screen.findByText(/could not save the layout: layout for 'corp-auth': annotations\[1\] `svg` carries a <script> element/);
  });
});

describe("pure pieces of the edit canvas", () => {
  it("finds the segment under a point from the placement's boxes", () => {
    const nodes = [
      { id: "site:10.1", position: { x: 100, y: 100 }, data: { kind: "site" as const, key: "site:10.1", label: "", shown: 1, total: 1, northSouthBytes: 0 }, width: 500, height: 300 },
      { id: "seg:net-lan", position: { x: 10, y: 20 }, parentId: "site:10.1", width: 200, height: 100, data: { kind: "segment" as const, name: "net-lan", cidr: "10.1.0.0/24", deployedCidr: null, zoneType: null, hosts: 0, ghosts: 0, evidenceMix: [], roleBadges: [], hasRouterLeg: false } },
    ];
    expect(segmentAt(nodes, { x: 150, y: 150 })).toBe("net-lan");
    expect(segmentAt(nodes, { x: 50, y: 50 })).toBeNull();
  });

  it("picks the twin's standing te deploy, newest first, never a destroyed or tap one", () => {
    const d = (over: Partial<DeploySummary>): DeploySummary => ({ ...DEPLOY, ...over });
    expect(standingDeploy([d({ status: "destroyed" })], "corp-live")).toBeNull();
    expect(standingDeploy([d({ tier: "tap", deploy_id: "t" })], "corp-live")).toBeNull();
    expect(standingDeploy([d({ deploy_id: "old", started_at: 1 }), d({ deploy_id: "new", started_at: 2 })], "corp-live")!.deploy_id).toBe("new");
    expect(standingDeploy([d({ scenario_id: "other" })], "corp-live")).toBeNull();
  });

  it("lists the palette from the API: a segment, this twin's groups first, every image once, the personas", () => {
    const own = { id: "a", description: null, cluster: null, params: [], segments: [], nodes: [{ name: "ws", kind: "host", image: "img-1", count: "${N:5}", role: "r" }], document: null, warnings: [] };
    const other = { ...own, id: "b", nodes: [{ name: "db", kind: "host", image: "img-1", count: 2, role: null }] };
    const items = paletteItems([other, own], PERSONAS, "a");
    expect(items.map((i) => `${i.kind}:${i.id}`)).toEqual(["segment:segment", "group:a/ws", "group:b/db", "image:img-1", "persona:corp-workstation", "persona:generic-endpoint"]);
    expect(items[1]).toMatchObject({ label: "ws", count: 5, role: "r" });
    expect(items[2]).toMatchObject({ label: "db (b)", count: 2 });
  });

  it("the fake control plane classifies by the tier file touched, as the API does", () => {
    const base = document("x").files;
    expect(classify(base, { ...base, "tfc/default.yaml": TFC + "    - node: web01\n      persona: generic-endpoint\n" }, "te.yaml")).toMatchObject([{ path: "tfc/default.yaml", kind: "attribute", via: ["tfc"] }]);
    expect(classify(base, { ...base, "tap/default.yaml": "tap-plan: {}\n" }, "te.yaml")).toMatchObject([{ path: "tap/default.yaml", op: "added", kind: "attribute", via: ["tap"] }]);
    expect(classify(base, { ...base, "te.yaml": base["te.yaml"]!.replace("role: web-tier", "role: api-tier") }, "te.yaml")).toMatchObject([{ kind: "attribute", roles: { web01: "api-tier" } }]);
    expect(classify(base, { ...base, "te.yaml": base["te.yaml"]!.replace("role: web-tier", "role: api-tier").replace("ubuntu-server-22@v5", "ubuntu-server-24@v1") }, "te.yaml")).toMatchObject([{ kind: "topology" }]);
    expect(classify(base, { ...base, "exr/x.yaml": "exercise: {}\n" }, "te.yaml")).toMatchObject([{ kind: "inert" }]);
  });
});
