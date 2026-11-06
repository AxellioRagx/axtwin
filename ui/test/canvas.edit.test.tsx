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
import { segmentAt, connectionToAttach } from "../src/features/edit-canvas/Canvas";
import { paletteItems, standingDeploy } from "../src/features/edit-canvas/CanvasPage";
import { readTe } from "../src/features/edit-canvas/edit/te";
import { classify } from "./helpers/fakeApi";
import { CAST_ROSTER, DEPLOY, PERSONAS, TFC, ann, clickPane, commit, document, enabled, host, hosts, item, openPalette, panel, seeded, segment, segments, settle, tab, teText, twinYaml } from "./helpers/canvasFixtures";
import { renderAt } from "./helpers/render";

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

    // Arm a node group from the palette, then click the new segment: click-to-place, one rule.
    fireEvent.click(item("group", "corp-auth/web01"));
    expect(screen.getByTestId("palette-hint").textContent).toContain("armed: web01");
    fireEvent.click(segment("segment-3"));
    await waitFor(() => expect(hosts()).toEqual(["web01", "web01-1", "ws01"]));
    expect(screen.getByTestId("palette-hint").textContent).not.toContain("armed");
    expect(host("web01-1").dataset["address"]).toBe("10.1.2.10");
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
    expect(host("ws01-1").dataset["address"]).toBe("10.1.1.10");
    await waitFor(() => expect(screen.getByTestId("validation-state").textContent).toBe("valid"));
    // Again: the next free address, not the same one.
    fireEvent.click(host("ws01"));
    fireEvent.change(within(await panel().findByTestId("node-panel")).getByLabelText("Duplicate onto segment"), { target: { value: "net-dmz" } });
    fireEvent.click(within(panel().getByTestId("node-panel")).getByRole("button", { name: "Duplicate" }));
    await waitFor(() => expect(hosts()).toEqual(["web01", "ws01", "ws01-1", "ws01-2"]));
    expect(host("ws01-2").dataset["address"]).toBe("10.1.1.11");
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
    expect(screen.getByTestId("mode").textContent).toContain("edit mode · dep-1 te completed");
    expect(screen.getByTestId("clean-marker").textContent).toBe("working copy equals the baseline");
    fireEvent.click(host("ws01"));
    commit(within(await panel().findByTestId("node-panel")).getByLabelText("Role"), "finance-ws");
    await waitFor(() => expect(screen.getByTestId("dirty-marker").textContent).toContain("unapplied changes"));
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
    fireEvent.click(within(canvasA).getByTestId("rail-add"));
    fireEvent.click(within(canvasA).getAllByTestId("palette-item").find((i) => i.dataset["kind"] === "segment")!);
    await waitFor(() => expect(within(canvasA).getByTestId("dirty-marker")).toBeTruthy());
    await settle();
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
    tab("Checks");
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
    expect((await api.getDeployConfig("dep-1")).applied?.tiers).toEqual({ tfc: "done" });
    // A role change with the image unchanged is live as well, naming the role.
    fireEvent.click(host("ws01"));
    commit(within(await panel().findByTestId("node-panel")).getByLabelText("Role"), "finance-ws");
    tab("Checks");
    await waitFor(() => expect(screen.getAllByTestId("pending-change").length).toBe(2));
    expect(screen.getAllByTestId("pending-change").find((c) => c.textContent!.includes("te.yaml"))!.textContent).toContain("ws01 → finance-ws");
  });

  it("adding a segment on a deployed twin shows re-plan: Apply disabled, Persist and Save-As enabled, redeploy offered", async () => {
    const { api } = seeded();
    renderAt(api, "/twins/corp-live/canvas");
    await screen.findAllByTestId("canvas-host");
    fireEvent.click(item("segment", "segment"));
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
    tab("Checks");
    await waitFor(() => expect(screen.getAllByTestId("pending-change").length).toBe(1));
    tab("Selection");
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
    await waitFor(() => expect(screen.getByTestId("validation-state").textContent).toBe("valid"));
  });

  it("the palette places N copies named {name}-{n}, each on the next free address; a persona item binds the selected group", async () => {
    const { api } = seeded();
    renderAt(api, "/twins/corp-auth/canvas");
    await screen.findAllByTestId("canvas-host");
    openPalette();
    fireEvent.change(screen.getByLabelText("Copies"), { target: { value: "3" } });
    // Arm the image, click the segment: three copies land there. A selected segment is not a target by itself.
    fireEvent.click(segment("net-dmz"));
    await panel().findByTestId("segment-panel");
    fireEvent.click(item("image", "debian-12-cloud@v1"));
    expect(hosts()).toEqual(["web01", "ws01"]);
    expect(screen.getByText(/armed: debian-12-cloud@v1/)).toBeTruthy();
    fireEvent.click(segment("net-dmz"));
    await waitFor(() => expect(hosts()).toEqual(["debian-12-cloud-1", "debian-12-cloud-2", "debian-12-cloud-3", "web01", "ws01"]));
    expect(["debian-12-cloud-1", "debian-12-cloud-2", "debian-12-cloud-3"].map((n) => host(n).dataset["address"])).toEqual(["10.1.1.10", "10.1.1.11", "10.1.1.12"]);
    // A second click on the armed item disarms it, as does Esc (which also clears the selection).
    fireEvent.click(item("image", "debian-12-cloud@v1"));
    expect(screen.getByText(/armed: debian-12-cloud@v1/)).toBeTruthy();
    fireEvent.click(item("image", "debian-12-cloud@v1"));
    expect(screen.queryByText(/armed:/)).toBeNull();
    fireEvent.click(item("image", "debian-12-cloud@v1"));
    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() => expect(screen.queryByText(/armed:/)).toBeNull());
    await waitFor(() => expect(panel().queryByTestId("segment-panel")).toBeNull());
    // A persona needs a selected group. (The category starts folded; Esc closed the popover.)
    openPalette();
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
    expect(screen.getAllByTestId("palette-item").map((i) => i.dataset["id"])).toEqual(["corp-auth/web01", "ubuntu-server-22@v5", "corp-live/web01"]);
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
    expect(screen.getByTestId("mode").textContent).toContain("view mode · dep-1 te running");
    expect(screen.getByTestId("canvas").dataset["locked"]).toBe("true");
    expect(screen.getByTestId("canvas").dataset["mode"]).toBe("view");
    openPalette();
    expect(screen.getByText(/view mode: the palette is read-only/)).toBeTruthy();
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
  it("every mutation is validated through POST /api/validate {document}: an error finding disables Save and is shown with its rule and place; a clean answer enables it", async () => {
    const { api, state } = seeded();
    renderAt(api, "/twins/corp-auth/canvas");
    await screen.findAllByTestId("canvas-host");
    await waitFor(() => expect(screen.getByTestId("validation-state").textContent).toBe("valid"));
    expect(state.writes.filter((w) => w.op === "validate").length).toBe(1);
    tab("Checks");
    expect(screen.getByTestId("validation-api").textContent).toBe("API: pass · 0 errors, 0 warnings");
    // An image the manifests do not know passes the canvas's own check and fails the API's.
    fireEvent.click(host("web01"));
    const p = await panel().findByTestId("node-panel");
    fireEvent.change(within(p).getByLabelText("Image"), { target: { value: "ubuntu-server-99@v1" } });
    fireEvent.blur(within(p).getByLabelText("Image"));
    await waitFor(() => expect(screen.getByTestId("validation-state").textContent).toBe("invalid"));
    tab("Checks");
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
    tab("Selection");
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
    fireEvent.click(item("segment", "segment"));
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

  it("lists the palette from the API: a segment, this twin's groups first, every image once, the personas", () => {
    const own = { id: "a", description: null, cluster: null, params: [], segments: [], nodes: [{ name: "ws", kind: "host", image: "img-1", count: "${N:5}", role: "r" }], document: null, warnings: [] };
    const other = { ...own, id: "b", nodes: [{ name: "db", kind: "host", image: "img-1", count: 2, role: null }] };
    const items = paletteItems([other, own], PERSONAS, "a");
    expect(items.map((i) => `${i.kind}:${i.id}`)).toEqual(["segment:segment", "router:router", "group:a/ws", "group:b/db", "image:img-1", "persona:corp-workstation", "persona:generic-endpoint"]);
    expect(items[2]).toMatchObject({ label: "ws", count: 5, role: "r" });
    expect(items[1]).toMatchObject({ kind: "router", id: "router", label: "Router" });
    expect(items[3]).toMatchObject({ label: "db (b)", count: 2 });
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
    await waitFor(() => expect(screen.getByTestId("canvas").dataset["zoomTarget"]).toBe("seg:net-dmz"));
    fireEvent.doubleClick(host("ws01")); // a host is not a zoom target
    expect(screen.getByTestId("canvas").dataset["zoomTarget"]).toBe("seg:net-dmz");
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
  it("the palette's Router lands on the pane with no leg, is invalid until a leg is attached, and a selected segment becomes its first leg", async () => {
    const { api } = seeded();
    renderAt(api, "/twins/corp-auth/canvas");
    await screen.findAllByTestId("canvas-host");
    const routers = () => screen.getAllByTestId("canvas-router").map((r) => r.dataset["nodeId"]).sort();
    expect(routers()).toEqual(["gw"]);
    // Armed, then an empty spot: the router lands there with no leg.
    const r = item("router", "router");
    expect(r.title).toContain("attach it to each segment it joins");
    fireEvent.click(r);
    expect(screen.getByTestId("palette-hint").textContent).toContain("or an empty spot for no leg yet");
    clickPane();
    await waitFor(() => expect(routers()).toEqual(["gw", "router"]));
    expect(screen.queryByTestId("blocked-reason")).toBeNull();
    // Unattached is a WARNING (an error would have blocked the placement): the document stays saveable, the Checks tab says what is missing.
    await waitFor(() => expect(screen.getByTestId("validation-state").textContent).toBe("valid"));
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
    tab("Checks");
    await waitFor(() => expect(screen.queryAllByTestId("finding").some((f) => f.dataset["rule"] === "CV-024")).toBe(false));
    // Armed, then a segment: that segment is the router's first leg.
    fireEvent.click(item("router", "router"));
    fireEvent.click(segment("net-dmz"));
    await waitFor(() => expect(routers()).toEqual(["gw", "router", "router-1"]));
    fireEvent.click(screen.getAllByTestId("canvas-router").find((x) => x.dataset["nodeId"] === "router-1")!);
    await waitFor(() => expect(within(panel().getByTestId("node-panel")).getAllByTestId("attachment").map((a) => a.dataset["segment"])).toEqual(["net-dmz"]));
    expect((within(panel().getByTestId("node-panel")).getByLabelText("Address on net-dmz") as HTMLInputElement).value).toBe("");
  });

  it("a group copied from a router stays a router; an armed host image gets no 'Place here' on the pane", async () => {
    const { api } = seeded();
    renderAt(api, "/twins/corp-auth/canvas");
    await screen.findAllByTestId("canvas-host");
    const routers = () => screen.getAllByTestId("canvas-router").map((r) => r.dataset["nodeId"]).sort();
    fireEvent.click(item("group", "corp-auth/gw"));
    clickPane();
    await waitFor(() => expect(routers()).toEqual(["gw", "gw-1"]));
    expect(screen.getAllByTestId("canvas-host").map((h) => h.dataset["nodeId"])).not.toContain("gw-1");
    fireEvent.click(item("image", "debian-12-cloud@v1"));
    expect(screen.getByText(/armed: debian-12-cloud@v1/)).toBeTruthy();
    fireEvent.contextMenu(window.document.querySelector(".react-flow__pane")!, { clientX: 300, clientY: 700 });
    const menu = await screen.findByTestId("context-menu");
    expect(within(menu).queryByRole("button", { name: /^Place / })).toBeNull();
  });
});
