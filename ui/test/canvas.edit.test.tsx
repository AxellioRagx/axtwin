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
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { CastRoster, DeploySummary } from "../src/api/types";
import { segmentAt } from "../src/features/edit-canvas/Canvas";
import { standingDeploy } from "../src/features/edit-canvas/CanvasPage";
import { readTe } from "../src/features/edit-canvas/edit/te";
import { classify } from "./helpers/fakeApi";
import { CAST_ROSTER, DEPLOY, TFC, ann, check, checkStatus, commit, document, addDevice, addSegment, card, chooseAndAdd, enabled, host, hosts, openPalette, panel, seeded, segment, segments, settle, tab, teText, twinYaml } from "./helpers/canvasFixtures";
import { renderAt } from "./helpers/render";

afterEach(() => {
  localStorage.clear();
});

describe("the design canvas, phase 2 (C3)", () => {
  it("C3.1: adds a segment and a node on an undeployed config; the editor shows the config valid; Save writes it", async () => {
    const { api, state } = seeded();
    renderAt(api, "/twins/corp-auth/canvas");
    await screen.findAllByTestId("canvas-host");
    expect(screen.getByTestId("mode").textContent).toContain("edit · undeployed");
    expect(screen.getByTestId("clean-marker").textContent).toBe("saved");
    expect(segments()).toEqual(["net-dmz", "net-lan"]);

    addSegment();
    await waitFor(() => expect(segments()).toEqual(["net-dmz", "net-lan", "segment-3"]));
    expect(segment("segment-3").dataset["cidr"]).toBe("10.1.2.0/24"); // the next free /24 under the twin's prefix
    expect(screen.getByTestId("dirty-marker").textContent).toContain("unsaved changes");

    // Select the new segment, click the Server card: it lands there; its popover names it web01.
    fireEvent.click(segment("segment-3"));
    addDevice("server", { prefix: "web01" });
    await waitFor(() => expect(hosts()).toEqual(["web01", "web01-2", "ws01"]));
    expect(host("web01-2").dataset["address"]).toBe("10.1.2.10");
    await check("valid");

    fireEvent.click(await enabled("Save"));
    await screen.findByTestId("clean-marker");
    const write = state.writes.find((w) => w.op === "put-twin-config")!;
    expect(write.id).toBe("corp-auth");
    const te = readTe((write.body as { files: Record<string, string> }).files["te.yaml"]!);
    expect(te.segments.map((s) => s.name)).toEqual(["net-lan", "net-dmz", "segment-3"]);
    expect(te.nodes.find((n) => n.name === "web01-2")).toMatchObject({ role: "web-tier", image: "debian-12-cloud@v1", nets: [{ segment: "segment-3", ip: "10.1.2.10" }] });
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
    expect(host("ws01-1").dataset["address"]).toBe("10.1.1.10");
    await check("valid");
    // Again: the next free address, not the same one.
    fireEvent.click(host("ws01"));
    fireEvent.change(within(await panel().findByTestId("node-panel")).getByLabelText("Duplicate onto segment"), { target: { value: "net-dmz" } });
    fireEvent.click(within(panel().getByTestId("node-panel")).getByRole("button", { name: "Duplicate" }));
    await waitFor(() => expect(hosts()).toEqual(["web01", "ws01", "ws01-1", "ws01-2"]));
    expect(host("ws01-2").dataset["address"]).toBe("10.1.1.11");
  });

  it("R3-6: opening the Add panel reads no other twin (\"From other twins\" is gone, and its lazy load with it)", async () => {
    const { api, state } = seeded();
    const reads: string[] = [];
    const list = api.listTwinConfigs.bind(api);
    const one = api.getTwinConfig.bind(api);
    api.listTwinConfigs = (signal) => (reads.push("list"), list(signal));
    api.getTwinConfig = (cid, signal) => (reads.push(cid), one(cid, signal));
    renderAt(api, "/twins/corp-auth/canvas");
    await screen.findAllByTestId("canvas-host");
    await settle();
    expect(reads).toEqual(["corp-auth"]); // this twin's own document only
    openPalette();
    await settle();
    expect(reads).toEqual(["corp-auth"]);
    expect(state.writes.filter((w) => w.op !== "validate")).toEqual([]);
  });

  it("C3.3: Save-As from an undeployed config and from a working copy appears in the list and reopens identical; a taken name is refused", async () => {
    const { api, state } = seeded();
    const r = renderAt(api, "/twins/corp-auth/canvas");
    await screen.findAllByTestId("canvas-host");
    addSegment();
    await waitFor(() => expect(segments().length).toBe(3));
    fireEvent.change(screen.getByLabelText("Save as id"), { target: { value: "corp-auth-2" } });
    fireEvent.click(await enabled("Save as"));
    await waitFor(() => expect(state.writes.some((w) => w.op === "create-twin-config")).toBe(true));
    expect((await api.listTwinConfigs()).twin_configs.map((c) => c.id)).toContain("corp-auth-2");
    const created = state.documents["corp-auth-2"]!;
    expect(readTe(created.files["te.yaml"]!).id).toBe("corp-auth-2");
    expect(readTe(created.files["te.yaml"]!).segments.length).toBe(3);
    // It reopens as its own canvas, identical.
    await screen.findByRole("heading", { name: "corp-auth-2" });
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
    addSegment();
    await waitFor(() => expect(segments().length).toBe(3));
    await settle();
    fireEvent.change(screen.getByLabelText("Save as id"), { target: { value: "corp-live-copy" } });
    fireEvent.click(await enabled("Save as"));
    await waitFor(() => expect(state.documents["corp-live-copy"]).toBeTruthy());
    expect(readTe(state.documents["corp-live-copy"]!.files["te.yaml"]!).segments.length).toBe(3);
    // The baseline, the deploy and its working copy are untouched by a Save-As (the edits were never pushed).
    expect(readTe(state.documents["corp-live"]!.files["te.yaml"]!).segments.length).toBe(2);
    expect(state.writes.some((w) => w.op === "put-deploy-config")).toBe(false);
  });

  it("C3.4: an invalid edit lands (nothing validates while editing); Check reports it with its rule, pinned on the segment; a 422 on Save shows as findings, the edit kept", async () => {
    const { api, state } = seeded();
    const r = renderAt(api, "/twins/corp-auth/canvas");
    await screen.findAllByTestId("canvas-host");
    fireEvent.click(segment("net-dmz"));
    const p = await panel().findByTestId("segment-panel");
    commit(within(p).getByLabelText("CIDR"), "10.1.1.5/24");
    await waitFor(() => expect(segment("net-dmz").dataset["cidr"]).toBe("10.1.1.5/24"));
    expect(screen.queryByTestId("blocked-reason")).toBeNull();
    expect(screen.getByTestId("dirty-marker")).toBeTruthy();
    await check("invalid");
    tab("Checks");
    const f = screen.getAllByTestId("finding").find((x) => x.dataset["rule"] === "CV-012")!;
    expect(f.textContent).toContain("'10.1.1.5/24' is not an IPv4 network address");
    expect(f.dataset["target"]).toBe("segment:net-dmz");
    expect(within(segment("net-dmz")).getByTestId("finding-badge").dataset["severity"]).toBe("error");
    expect((screen.getByTestId("save") as HTMLButtonElement).disabled).toBe(true);
    r.unmount();

    // The server re-validates on the write: its 422 becomes the Check's findings; the edit stays on screen.
    const refused = seeded();
    renderAt(refused.api, "/twins/corp-auth/canvas");
    await screen.findAllByTestId("canvas-host");
    fireEvent.click(host("ws01"));
    commit(within(await panel().findByTestId("node-panel")).getByLabelText("Role"), "finance-ws");
    await check("valid");
    refused.state.refuse.twinConfigWrite = new (await import("../src/api/client")).ApiError({ kind: "unprocessable", status: 422, message: "twin-config 'corp-auth' fails validation (1 error(s))", detail: { code: "invalid", findings: [{ rule_id: "IMG-010", severity: "error", message: "node 'ws01': image 'win11-finance@v4' has no manifest", location: "layers.infra.nodes[3]" }] }, url: "" });
    fireEvent.click(screen.getByTestId("save"));
    await waitFor(() => expect(checkStatus()).toBe("invalid"));
    tab("Checks");
    expect(screen.getAllByTestId("finding").find((x) => x.dataset["rule"] === "IMG-010")!.dataset["target"]).toBe("node:ws01");
    expect(screen.getByTestId("dirty-marker")).toBeTruthy();
    expect(state.writes.filter((w) => w.op === "put-deploy-config").length).toBe(0);
  });

  it("C3.5: editing a deployed twin shows a dirty marker and leaves the baseline unchanged until Persist", async () => {
    const { api, state } = seeded();
    renderAt(api, "/twins/corp-live/canvas");
    await screen.findAllByTestId("canvas-host");
    expect(screen.getByTestId("mode").textContent).toContain("edit · dep-1 · te ✓");
    expect(screen.getByTestId("clean-marker").textContent).toBe("working copy equals the baseline");
    fireEvent.click(host("ws01"));
    commit(within(await panel().findByTestId("node-panel")).getByLabelText("Role"), "finance-ws");
    await waitFor(() => expect(screen.getByTestId("dirty-marker").textContent).toContain("local edits (not pushed)"));
    await settle();
    // The edit stays in this browser: no PUT of the working copy, the baseline untouched.
    expect(state.writes.some((w) => w.op === "put-deploy-config")).toBe(false);
    expect((await api.getTwinConfig("corp-live")).document!.files["te.yaml"]).toBe(teText("corp-live"));
    // Persist (on a passing Check): pushes the working copy, then the baseline becomes it and the copy is clean.
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
    fireEvent.contextMenu(canvasA.querySelector(".react-flow__pane")!, { clientX: 300, clientY: 200 });
    fireEvent.click(within(within(canvasA).getByTestId("context-menu")).getByRole("button", { name: "Add a segment" }));
    await waitFor(() => expect(within(canvasA).getByTestId("dirty-marker")).toBeTruthy());
    await settle();
    // Local only until pushed: Apply (on a passing Check) pushes it; a topology change is then not applied.
    expect((await api.getDeployConfig("dep-1")).dirty).toBe(false);
    fireEvent.click(within(canvasA).getByTestId("check-run"));
    const applyA = within(canvasA).getByRole("button", { name: "Apply" });
    await waitFor(() => expect(applyA.hasAttribute("disabled")).toBe(false));
    fireEvent.click(applyA);
    await within(canvasA).findByText(/pushed to the working copy, not applied: a pending change needs a re-plan/);
    expect((await api.getDeployConfig("dep-1")).dirty).toBe(true);

    // The second console reads the same working copy from the API.
    const b = renderAt(api, "/twins/corp-live/canvas");
    const canvasB = b.container;
    await within(canvasB).findAllByTestId("canvas-host");
    expect(within(canvasB).getByTestId("dirty-marker").textContent).toContain("unapplied changes");
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
    addSegment();
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
    expect(files["te.yaml"]).toBe(teText("corp-auth").replace("    - { name: net-dmz, cidr: 10.1.1.0/24, zone_type: vlan }\n", "    - { name: net-dmz, cidr: 10.1.1.0/24, zone_type: vlan }\n    - { name: segment-3, cidr: 10.1.2.0/24, zone_type: vlan, source: authored }\n"));
    expect(files["twin.yaml"]).toBe(twinYaml("corp-auth"));
  });

  it("a persona change on a deployed twin shows live and Apply is enabled; Apply reaches the twin", async () => {
    const { api, state } = seeded();
    renderAt(api, "/twins/corp-live/canvas");
    await screen.findAllByTestId("canvas-host");
    fireEvent.click(host("web01"));
    fireEvent.change(within(await panel().findByTestId("node-panel")).getByLabelText("Persona"), { target: { value: "generic-endpoint" } });
    await settle();
    expect(state.writes.some((w) => w.op === "put-deploy-config")).toBe(false);
    // Apply (on a passing Check) pushes the edit to the working copy, then applies it.
    fireEvent.click(await enabled("Apply"));
    await screen.findByText(/applied via tfc/);
    expect(state.writes.map((w) => w.op).filter((op) => op === "put-deploy-config" || op === "apply-deploy-config")).toEqual(["put-deploy-config", "apply-deploy-config"]);
    expect((await api.getDeployConfig("dep-1")).applied?.tiers).toEqual({ tfc: "done" });
    tab("Checks");
    const change = screen.getByTestId("pending-change");
    expect(change.dataset["kind"]).toBe("traffic");
    expect(change.textContent).toContain("live");
    expect(change.textContent).toContain("tfc/default.yaml");
    // A role change with the image unchanged is live as well, naming the role.
    fireEvent.click(host("ws01"));
    commit(within(await panel().findByTestId("node-panel")).getByLabelText("Role"), "finance-ws");
    fireEvent.click(await enabled("Apply"));
    await waitFor(() => expect(state.writes.filter((w) => w.op === "apply-deploy-config").length).toBe(2));
    tab("Checks");
    await waitFor(() => expect(screen.getAllByTestId("pending-change").length).toBe(2));
    expect(screen.getAllByTestId("pending-change").find((c) => c.textContent!.includes("te.yaml"))!.textContent).toContain("ws01 → finance-ws");
  });

  it("adding a segment on a deployed twin shows re-plan: Apply disabled, Persist and Save-As enabled, redeploy offered", async () => {
    const { api } = seeded();
    renderAt(api, "/twins/corp-live/canvas");
    await screen.findAllByTestId("canvas-host");
    addSegment();
    // Apply pushes the edit; the API classifies it as topology, so it is pushed and not applied.
    fireEvent.click(await enabled("Apply"));
    await screen.findByText(/pushed to the working copy, not applied/);
    tab("Checks");
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
    await waitFor(() => expect(screen.getByTestId("dirty-marker")).toBeTruthy());
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
    tab("Checks");
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
    await check("valid");
  });

  it("a card dropped with a count writes one group with that count; a role picked in its popover binds its persona", async () => {
    const { api, state } = seeded();
    renderAt(api, "/twins/corp-auth/canvas");
    await screen.findAllByTestId("canvas-host");
    // A card's click lands on the selected segment.
    fireEvent.click(segment("net-dmz"));
    await panel().findByTestId("segment-panel");
    addDevice("workstation", { os: "Linux", role: "generic-endpoint", count: 3, prefix: "lab" });
    await waitFor(() => expect(hosts()).toContain("lab"));
    await check("valid");
    fireEvent.click(await enabled("Save"));
    await screen.findByTestId("clean-marker");
    const files = (state.writes.find((w) => w.op === "put-twin-config")!.body as { files: Record<string, string> }).files;
    expect(readTe(files["te.yaml"]!).nodes.find((n) => n.name === "lab")).toMatchObject({ count: 3, role: "generic-endpoint", image: "debian-12-cloud@v1", nets: [{ segment: "net-dmz", ip: null }] });
    expect(files["tfc/default.yaml"]).toContain("- node: lab\n      persona: generic-endpoint");
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
    // A connect drag from a host to a segment's switch is the same attach (`canvas.connect.test.tsx`).
  });

  it("view mode while the deploy is running: locked except annotations, which go to the side-car", async () => {
    const { api, state } = seeded({ deploys: [{ ...DEPLOY, status: "running" }] });
    renderAt(api, "/twins/corp-live/canvas");
    await screen.findAllByTestId("canvas-host");
    expect(screen.getByTestId("mode").textContent).toContain("view · dep-1 · te ⟳ running");
    expect(screen.getByTestId("canvas").dataset["locked"]).toBe("true");
    expect(screen.getByTestId("canvas").dataset["mode"]).toBe("view");
    openPalette();
    expect(screen.getByText(/view mode: the Add panel is read-only/)).toBeTruthy();
    fireEvent.click(host("ws01"));
    const p = await panel().findByTestId("node-panel");
    expect((within(p).getByLabelText("Role") as HTMLInputElement).disabled).toBe(true);
    expect(screen.getByRole("button", { name: "Apply" }).hasAttribute("disabled")).toBe(true);
    // Annotations still work.
    fireEvent.click(ann("text"));
    const first = await screen.findByTestId("canvas-annotation");
    expect(first.dataset["annKind"]).toBe("text");
    expect(first.textContent).toContain("note");
    fireEvent.click(ann("rect"));
    await waitFor(() => expect(screen.getAllByTestId("canvas-annotation").length).toBe(2));
    fireEvent.click(ann("Hide all"));
    await waitFor(() => expect(screen.queryByTestId("canvas-annotation")).toBeNull());
    fireEvent.click(ann("Show annotations"));
    await waitFor(() => expect(screen.getAllByTestId("canvas-annotation").length).toBe(2));
    // Nothing reached the side-car yet: annotations, like moves, wait for "Save layout".
    expect(state.layouts["corp-live"]).toBeUndefined();
    expect(screen.getByTestId("layout-dirty").textContent).toMatch(/layout: \d+ unsaved changes/);
    fireEvent.click(screen.getByRole("button", { name: "Save layout" }));
    await waitFor(() => expect((state.layouts["corp-live"]?.annotations ?? []).length).toBe(2));
    expect(state.layouts["corp-live"]!.annotations.map((a) => a.kind)).toEqual(["text", "rect"]);
    expect(screen.queryByTestId("layout-dirty")).toBeNull();
    tab("Notes");
    expect(screen.getByText(/2 annotations in the layout side-car/)).toBeTruthy();
    expect(localStorage.getItem("axr-canvas-annotations:corp-live")).toBeNull();
  });
});

describe("the document validate gate and the side-car annotations", () => {
  it("validation is on demand: no validate request fires on edits; the badge reads unchecked, valid, changed since check; Save opens only on a passing Check of unchanged files", async () => {
    const { api, state } = seeded();
    renderAt(api, "/twins/corp-auth/canvas");
    await screen.findAllByTestId("canvas-host");
    expect(checkStatus()).toBe("unchecked");
    expect(screen.getByTestId("check-badge").textContent).toBe("unchecked");
    // Edits: nothing is validated or written while editing.
    addSegment();
    await waitFor(() => expect(segments().length).toBe(3));
    fireEvent.click(host("ws01"));
    commit(within(await panel().findByTestId("node-panel")).getByLabelText("Role"), "finance-ws");
    await settle();
    expect(state.writes.filter((w) => w.op === "validate")).toEqual([]);
    expect(screen.getByTestId("save").hasAttribute("disabled")).toBe(true);
    // Check: one request on exactly the files on screen; valid; Save opens.
    fireEvent.click(screen.getByTestId("check-run"));
    await waitFor(() => expect(screen.getByTestId("check-badge").textContent).toBe("valid"));
    expect(state.writes.filter((w) => w.op === "validate").length).toBe(1);
    expect(screen.getByTestId("save").hasAttribute("disabled")).toBe(false);
    tab("Checks");
    expect(screen.getByTestId("validation-api").textContent).toBe("pass · 0 errors, 0 warnings");
    expect(screen.getByRole("tab", { name: /^Checks/ }).textContent).toContain("valid");
    // One more edit: the check no longer covers the files, Save closes until the next Check.
    tab("Selection");
    fireEvent.click(host("ws01"));
    commit(within(await panel().findByTestId("node-panel")).getByLabelText("Role"), "kiosk");
    await waitFor(() => expect(screen.getByTestId("check-badge").textContent).toBe("changed since check"));
    expect(screen.getByTestId("save").hasAttribute("disabled")).toBe(true);
    expect(state.writes.filter((w) => w.op === "validate").length).toBe(1);
    // Ctrl+Enter (no field focused) checks again.
    (window.document.activeElement as HTMLElement | null)?.blur();
    fireEvent.keyDown(window, { key: "Enter", ctrlKey: true });
    await waitFor(() => expect(checkStatus()).toBe("valid"));
    expect(state.writes.filter((w) => w.op === "validate").length).toBe(2);
    // Undo back to the checked text... is not the checked text any more: changed again.
    fireEvent.keyDown(window, { key: "z", ctrlKey: true });
    await waitFor(() => expect(checkStatus()).toBe("changed"));
  });

  it("a failing Check (an image the manifests do not know) closes Save and Save as; only Save as draft stays open, and the draft does not touch the twin", async () => {
    const { api, state } = seeded();
    renderAt(api, "/twins/corp-auth/canvas");
    await screen.findAllByTestId("canvas-host");
    fireEvent.click(host("web01"));
    const p = await panel().findByTestId("node-panel");
    fireEvent.change(within(p).getByLabelText("Image"), { target: { value: "ubuntu-server-99@v1" } });
    fireEvent.blur(within(p).getByLabelText("Image"));
    await check("invalid");
    expect(screen.getByTestId("check-badge").textContent).toBe("invalid (1)");
    tab("Checks");
    const finding = screen.getAllByTestId("finding").find((f) => f.dataset["rule"] === "IMG-010")!;
    expect(finding.textContent).toContain("image 'ubuntu-server-99@v1' has no manifest");
    expect(finding.textContent).toContain("twin_config.nodes[web01].image");
    expect(screen.getByTestId("save").hasAttribute("disabled")).toBe(true);
    fireEvent.change(screen.getByLabelText("Save as id"), { target: { value: "x" } });
    expect(screen.getByRole("button", { name: "Save as" }).hasAttribute("disabled")).toBe(true);
    expect(screen.getByTestId("save-as-draft").hasAttribute("disabled")).toBe(false);
    // Save as draft: the suggested id, checked against the catalog, then the draft call -- never the twin's own id.
    fireEvent.click(screen.getByTestId("save-as-draft"));
    const dlg = await screen.findByTestId("draft-dialog");
    expect((within(dlg).getByLabelText("Id") as HTMLInputElement).value).toBe("corp-auth-draft");
    fireEvent.change(within(dlg).getByLabelText("Id"), { target: { value: "corp-live" } });
    expect((await within(dlg).findByTestId("saveas-problem")).textContent).toContain("already");
    fireEvent.change(within(dlg).getByLabelText("Id"), { target: { value: "corp-auth-draft" } });
    await waitFor(() => expect((within(dlg).getByRole("button", { name: "Save draft" }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(within(dlg).getByRole("button", { name: "Save draft" }));
    await waitFor(() => expect(state.drafts["corp-auth-draft"]).toBeTruthy());
    const created = state.writes.find((w) => w.op === "create-draft")!;
    expect(created.id).toBe("corp-auth-draft");
    expect(created.body).toMatchObject({ origin: "corp-auth", last_check: { disposition: "FAIL", errors: 1 } });
    expect(readTe(state.drafts["corp-auth-draft"]!.document.files["te.yaml"]!).id).toBe("corp-auth-draft");
    // The original twin is never touched.
    expect(state.writes.some((w) => (w.op === "put-twin-config" || w.op === "create-twin-config") && w.id === "corp-auth")).toBe(false);
    expect(state.documents["corp-auth"]!.files["te.yaml"]).toBe(teText("corp-auth"));
    // The page is the draft now: labelled, no Deploy.
    await screen.findByTestId("draft-label");
    expect(screen.queryByTestId("deploy-open")).toBeNull();
  });

  it("layout changes stay on the canvas until Save layout; Discard puts them back; a document Save carries them", async () => {
    const { api, state } = seeded();
    renderAt(api, "/twins/corp-auth/canvas");
    await screen.findAllByTestId("canvas-host");
    const layoutWrites = () => state.writes.filter((w) => w.op === "put-layout");
    expect(screen.getByRole("button", { name: "Save layout" }).hasAttribute("disabled")).toBe(true);
    expect(screen.getByRole("button", { name: "Discard" }).hasAttribute("disabled")).toBe(true);
    // Two annotations (the same draft every drag lands in): two changes, nothing written.
    fireEvent.click(ann("text"));
    fireEvent.click(ann("rect"));
    await waitFor(() => expect(screen.getAllByTestId("canvas-annotation").length).toBe(2));
    expect(screen.getByTestId("layout-dirty").textContent).toBe("● layout: 2 unsaved changes");
    expect(layoutWrites()).toEqual([]);
    expect(state.layouts["corp-auth"]).toBeUndefined();
    // Discard: back to the layout as loaded, still nothing written.
    fireEvent.click(screen.getByRole("button", { name: "Discard" }));
    await waitFor(() => expect(screen.queryByTestId("canvas-annotation")).toBeNull());
    expect(screen.queryByTestId("layout-dirty")).toBeNull();
    expect(screen.getByRole("button", { name: "Save layout" }).hasAttribute("disabled")).toBe(true);
    expect(layoutWrites()).toEqual([]);
    // Change again and Save layout: ONE write, no etag on a first save.
    fireEvent.click(ann("ellipse"));
    await screen.findByTestId("layout-dirty");
    expect(screen.getByTestId("layout-dirty").textContent).toBe("● layout: 1 unsaved change");
    fireEvent.click(screen.getByRole("button", { name: "Save layout" }));
    await waitFor(() => expect(layoutWrites().length).toBe(1));
    expect((layoutWrites()[0]!.body as { etag: string | null }).etag).toBeNull();
    expect(state.layouts["corp-auth"]!.annotations.map((a) => a.kind)).toEqual(["ellipse"]);
    await screen.findByText("layout saved");
    expect(screen.queryByTestId("layout-dirty")).toBeNull();
    // A document Save writes a dirty layout alongside, with the saved etag as the precondition.
    fireEvent.click(ann("line"));
    await screen.findByTestId("layout-dirty");
    addSegment();
    fireEvent.click(await enabled("Save"));
    await waitFor(() => expect(screen.getByTestId("exit-note").textContent).toContain("saved, layout too"));
    expect(layoutWrites().length).toBe(2);
    expect(String((layoutWrites()[1]!.body as { etag: string | null }).etag)).toMatch(/^layout-etag-\d+$/);
    expect(state.layouts["corp-auth"]!.annotations.map((a) => a.kind)).toEqual(["ellipse", "line"]);
    expect(state.writes.filter((w) => w.op === "put-twin-config").length).toBe(1);
    expect(screen.queryByTestId("layout-dirty")).toBeNull();
  });

  it("annotations round-trip through the layout document with the etag, and survive a reload", async () => {
    const { api, state } = seeded();
    const r = renderAt(api, "/twins/corp-auth/canvas");
    await screen.findAllByTestId("canvas-host");
    fireEvent.click(ann("text"));
    await screen.findByTestId("canvas-annotation");
    fireEvent.click(ann("ellipse"));
    await waitFor(() => expect(screen.getAllByTestId("canvas-annotation").length).toBe(2));
    expect(state.writes.filter((w) => w.op === "put-layout")).toEqual([]);
    fireEvent.click(screen.getByRole("button", { name: "Save layout" }));
    await waitFor(() => expect(state.layouts["corp-auth"]?.annotations.length).toBe(2));
    const saved = state.layouts["corp-auth"]!.annotations;
    expect(saved[0]).toMatchObject({ kind: "text", locked: false, hidden: false, w: 160, h: 28 });
    expect(saved[0]!.svg).toMatch(/^<text x="-?[\d.]+" y="-?[\d.]+">note<\/text>$/);
    expect(saved[1]!.svg).toMatch(/^<ellipse cx=.* fill="none" stroke="currentColor"\/>$/);
    // Edit the text and lock it from the panel, then Save layout: the second write carries the first's etag as its precondition.
    fireEvent.click(screen.getAllByTestId("canvas-annotation")[0]!);
    const ap = await panel().findByTestId("annotation-panel");
    fireEvent.change(within(ap).getByLabelText("Annotation text"), { target: { value: "crown jewels <here> & onion = 3" } });
    fireEvent.click(within(ap).getByLabelText("locked"));
    await screen.findByTestId("layout-dirty");
    fireEvent.click(screen.getByRole("button", { name: "Save layout" }));
    await waitFor(() => expect(state.layouts["corp-auth"]!.annotations[0]!.locked).toBe(true));
    const writes = state.writes.filter((w) => w.op === "put-layout");
    expect(writes.length).toBe(2);
    expect((writes[0]!.body as { etag: string | null }).etag).toBeNull();
    expect(String((writes[1]!.body as { etag: string | null }).etag)).toMatch(/^layout-etag-\d+$/);
    expect(state.layouts["corp-auth"]!.annotations[0]!.svg).toBe(`<text x="${saved[0]!.x}" y="${saved[0]!.y + 14}">crown jewels &lt;here&gt; &amp; onion &#61; 3</text>`);
    r.unmount();
    // A fresh console reads them back from the side-car, text included.
    renderAt(api, "/twins/corp-auth/canvas");
    await screen.findAllByTestId("canvas-host");
    await waitFor(() => expect(screen.getAllByTestId("canvas-annotation").length).toBe(2));
    const back = screen.getAllByTestId("canvas-annotation").find((a) => a.dataset["annKind"] === "text")!;
    expect(back.textContent).toContain("crown jewels <here> & onion = 3");
    expect(back.className).toContain("locked");
    tab("Notes");
    expect(screen.getByText(/2 annotations in the layout side-car/)).toBeTruthy();
    // A pin write carries the annotations through untouched.
    const { nextLayoutWrite } = await import("../src/features/edit-canvas/CanvasPage");
    const doc = await api.getLayout("corp-auth");
    expect(nextLayoutWrite(doc, "corp-auth", "host:ws01", { x: 1, y: 2 }).annotations).toEqual(doc.annotations);
  });

  it("migrates a set an earlier build kept in this browser onto the canvas once, as unsaved changes, then clears it", async () => {
    const { api, state } = seeded();
    localStorage.setItem("axr-canvas-annotations:corp-auth", JSON.stringify([{ id: "old-1", kind: "rect", x: 10, y: 20, w: 100, h: 50, text: "", locked: true }, { id: "old-2", kind: "text", x: 5, y: 5, w: 160, h: 28, text: "legacy note", locked: false }]));
    renderAt(api, "/twins/corp-auth/canvas");
    await screen.findAllByTestId("canvas-host");
    await waitFor(() => expect(screen.getAllByTestId("canvas-annotation").length).toBe(2));
    expect(state.layouts["corp-auth"]).toBeUndefined();
    await screen.findByTestId("layout-dirty");
    fireEvent.click(screen.getByRole("button", { name: "Save layout" }));
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
    fireEvent.click(ann("rect"));
    await screen.findByTestId("layout-dirty");
    fireEvent.click(screen.getByRole("button", { name: "Save layout" }));
    await screen.findByText(/could not save the layout: layout for 'corp-auth': annotations\[1\] `svg` carries a <script> element/);
    // The refused changes stay on the canvas, unsaved.
    expect(screen.getByTestId("layout-dirty")).toBeTruthy();
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

  it("the fake control plane classifies by the tier file touched, as the API does", () => {
    const base = document("x").files;
    expect(classify(base, { ...base, "tfc/default.yaml": TFC + "    - node: web01\n      persona: generic-endpoint\n" }, "te.yaml")).toMatchObject([{ path: "tfc/default.yaml", kind: "traffic", via: ["tfc"] }]);
    expect(classify(base, { ...base, "tap/default.yaml": "tap-plan: {}\n" }, "te.yaml")).toMatchObject([{ path: "tap/default.yaml", op: "added", kind: "taps", via: ["tap"] }]);
    expect(classify(base, { ...base, "te.yaml": base["te.yaml"]!.replace("role: web-tier", "role: api-tier") }, "te.yaml")).toMatchObject([{ kind: "traffic", roles: { web01: "api-tier" } }]);
    expect(classify(base, { ...base, "te.yaml": base["te.yaml"]!.replace("role: web-tier", "role: api-tier").replace("ubuntu-server-22@v5", "ubuntu-server-24@v1") }, "te.yaml")).toMatchObject([{ kind: "topology" }]);
    expect(classify(base, { ...base, "exr/x.yaml": "exercise: {}\n" }, "te.yaml")).toMatchObject([{ kind: "inert" }]);
  });
});

describe("the inspector widens for Checks and can be dragged", () => {
  it("opens Checks at its own width, follows a drag of its left edge, and a double-click restores the tab's width", async () => {
    const { api } = seeded();
    renderAt(api, "/twins/corp-auth/canvas");
    await screen.findAllByTestId("canvas-host");
    tab("Checks");
    const inspector = () => screen.getByTestId("inspector");
    expect(inspector().dataset["width"]).toBe("460");
    tab("Selection");
    expect(inspector().dataset["width"]).toBe("300");
    const grip = screen.getByRole("separator", { name: "Resize the inspector" });
    fireEvent.pointerDown(grip, { clientX: 1300 });
    fireEvent.pointerMove(window, { clientX: 1200 });
    fireEvent.pointerUp(window);
    // jsdom's boxes are at 0, so the right edge is 0 and the width clamps to the minimum.
    expect(inspector().dataset["width"]).toBe("240");
    fireEvent.doubleClick(grip);
    expect(inspector().dataset["width"]).toBe("300");
  });
});

describe("the basics: Delete, double-click to zoom, the pane menu", () => {
  it("Delete removes the selected node, segment or leg; not from a field, not in view mode", async () => {
    const { api } = seeded();
    renderAt(api, "/twins/corp-auth/canvas");
    await screen.findAllByTestId("canvas-host");
    fireEvent.click(host("ws01"));
    await panel().findByTestId("node-panel");
    // Typing Delete inside a field is editing, not removing.
    fireEvent.keyDown(within(panel().getByTestId("node-panel")).getByLabelText("Role"), { key: "Delete" });
    expect(hosts()).toEqual(["web01", "ws01"]);
    fireEvent.keyDown(window, { key: "Delete" });
    await waitFor(() => expect(hosts()).toEqual(["web01"]));
    await waitFor(() => expect(panel().queryByTestId("node-panel")).toBeNull());
    fireEvent.click(segment("net-dmz"));
    await panel().findByTestId("segment-panel");
    fireEvent.keyDown(window, { key: "Backspace" });
    await waitFor(() => expect(segments()).toEqual(["net-lan"]));
    expect(screen.getByTestId("dirty-marker")).toBeTruthy();
  });

  it("Delete does nothing to the twin in view mode", async () => {
    const { api } = seeded({ deploys: [{ ...DEPLOY, status: "running" }] });
    renderAt(api, "/twins/corp-live/canvas");
    await screen.findAllByTestId("canvas-host");
    fireEvent.click(host("ws01"));
    await panel().findByTestId("node-panel");
    fireEvent.keyDown(window, { key: "Delete" });
    await settle();
    expect(hosts()).toContain("ws01");
  });

  it("double-click on a segment zooms to it; right-click on the pane opens the pane menu", async () => {
    const { api } = seeded();
    renderAt(api, "/twins/corp-auth/canvas");
    await screen.findAllByTestId("canvas-host");
    fireEvent.doubleClick(segment("net-dmz"));
    await waitFor(() => expect(screen.getByTestId("canvas").dataset["zoomTarget"]).toBe("vsw:net-dmz"));
    fireEvent.doubleClick(host("ws01")); // a host is not a zoom target
    expect(screen.getByTestId("canvas").dataset["zoomTarget"]).toBe("vsw:net-dmz");
    const pane = window.document.querySelector(".react-flow__pane")!;
    fireEvent.contextMenu(pane, { clientX: 300, clientY: 200 });
    const menu = await screen.findByTestId("context-menu");
    const labels = within(menu).getAllByRole("button").map((b) => b.textContent);
    expect(labels).toEqual(["Zoom to fit", "Lock all", "Find a node…", "Add a segment", "Annotate here (text)", "Annotate here (rect)"]);
    fireEvent.click(within(menu).getByRole("button", { name: "Add a segment" }));
    await waitFor(() => expect(segments().length).toBe(3));
    expect(screen.queryByTestId("context-menu")).toBeNull();
    fireEvent.contextMenu(pane, { clientX: 300, clientY: 200 });
    fireEvent.click(within(await screen.findByTestId("context-menu")).getByRole("button", { name: "Annotate here (text)" }));
    await screen.findByTestId("canvas-annotation");
    fireEvent.contextMenu(pane, { clientX: 300, clientY: 200 });
    fireEvent.click(within(await screen.findByTestId("context-menu")).getByRole("button", { name: "Find a node…" }));
    await screen.findByTestId("popover-find");
  });

  it("the pane menu works read-only too, without the edit items", async () => {
    const { api } = seeded();
    renderAt(api, "/twins/corp-cast/canvas");
    await screen.findAllByTestId("canvas-host");
    fireEvent.click(screen.getByRole("button", { name: "Evidence" }));
    fireEvent.contextMenu(window.document.querySelector(".react-flow__pane")!, { clientX: 300, clientY: 200 });
    const menu = await screen.findByTestId("context-menu");
    expect(within(menu).getAllByRole("button").map((b) => b.textContent)).toEqual(["Zoom to fit", "Lock all", "Find a node…"]);
    fireEvent.click(within(menu).getByRole("button", { name: "Lock all" }));
    expect(screen.getByTestId("canvas").dataset["locked"]).toBe("true");
  });
});

describe("a router joins segments: it places without one", () => {
  it("the Router card lands on the pane with no leg, is invalid until a leg is attached, and dropped on a segment that is its first leg", async () => {
    const { api } = seeded();
    renderAt(api, "/twins/corp-auth/canvas");
    await screen.findAllByTestId("canvas-host");
    const routers = () => screen.getAllByTestId("canvas-router").map((r) => r.dataset["nodeId"]).sort();
    expect(routers()).toEqual(["gw"]);
    // Nothing selected: the click lands at the view's centre, an empty spot; the router has no leg.
    expect(card("router").title).toContain("joins segments");
    fireEvent.click(card("router"));
    chooseAndAdd();
    await waitFor(() => expect(routers()).toEqual(["gw", "router"]));
    expect(screen.queryByTestId("blocked-reason")).toBeNull();
    // Unattached is a WARNING (an error would have blocked the placement): the document stays saveable, the Checks tab says what is missing.
    await check("valid");
    tab("Checks");
    const cv024 = screen.getAllByTestId("finding").find((f) => f.dataset["rule"] === "CV-024")!;
    expect(cv024.textContent).toContain("router 'router' joins no segment yet");
    expect(cv024.dataset["severity"] ?? "warning").toBe("warning");
    // Its legs are attachments: from the node panel, then it is valid.
    fireEvent.click(screen.getAllByTestId("canvas-router").find((x) => x.dataset["nodeId"] === "router")!);
    const p = await panel().findByTestId("node-panel");
    expect((within(p).getByLabelText("Kind") as HTMLSelectElement).value).toBe("router");
    fireEvent.change(within(p).getByLabelText("Attach to segment"), { target: { value: "net-lan" } });
    fireEvent.click(within(p).getByRole("button", { name: "Attach" }));
    await waitFor(() => expect(within(panel().getByTestId("node-panel")).getAllByTestId("attachment").map((a) => a.dataset["segment"])).toEqual(["net-lan"]));
    // A router's leg is NOT pinned to the next free host address: the engine puts a lone router leg at the gateway (network+1).
    expect((within(panel().getByTestId("node-panel")).getByLabelText("Address on net-lan") as HTMLInputElement).value).toBe("");
    await check("valid");
    tab("Checks");
    expect(screen.queryAllByTestId("finding").some((f) => f.dataset["rule"] === "CV-024")).toBe(false);
    // A selected segment: the router's first leg.
    fireEvent.click(segment("net-dmz"));
    addDevice("router");
    await waitFor(() => expect(routers()).toEqual(["gw", "router", "router-2"]));
    fireEvent.click(screen.getAllByTestId("canvas-router").find((x) => x.dataset["nodeId"] === "router-2")!);
    await waitFor(() => expect(within(panel().getByTestId("node-panel")).getAllByTestId("attachment").map((a) => a.dataset["segment"])).toEqual(["net-dmz"]));
    expect((within(panel().getByTestId("node-panel")).getByLabelText("Address on net-dmz") as HTMLInputElement).value).toBe("");
  });

  it("a host dropped on the empty pane gets a new segment of its own; the pane menu offers no 'Place here'", async () => {
    const { api } = seeded();
    renderAt(api, "/twins/corp-auth/canvas");
    await screen.findAllByTestId("canvas-host");
    addDevice("server", { prefix: "solo" });
    await waitFor(() => expect(hosts()).toContain("solo"));
    expect(segments()).toEqual(["net-dmz", "net-lan", "web-tier-lan"]); // named for its first host's role
    fireEvent.contextMenu(window.document.querySelector(".react-flow__pane")!, { clientX: 300, clientY: 700 });
    const menu = await screen.findByTestId("context-menu");
    expect(within(menu).queryByRole("button", { name: /^Place / })).toBeNull();
  });
});

