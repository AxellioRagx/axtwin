/**
 * Subtree select, duplicate and attach (`docs/design/topology-layers.md`
 * §6, track 5d, 2026-11-07): `edit/subtree.ts` over the document; on the
 * canvas the context menu's Select subtree, Ctrl+D, the subtree panel's
 * Duplicate onto a device, the attach tool between devices, and a parked
 * device dropped on another to attach.
 */
import { describe, expect, it } from "vitest";

import { readDoc } from "../src/features/edit-canvas/edit/document";
import { attachDevices, carriedBy, deviceDepths, duplicateSubtree, freePort, subtreeOf } from "../src/features/edit-canvas/edit/subtree";
import { readTe } from "../src/features/edit-canvas/edit/te";
import { readTfc } from "../src/features/edit-canvas/edit/tfc";

const TE = `twin_config:
  id: sub
  segments:
    - { name: a1, cidr: 10.70.1.0/24, zone_type: vlan }
    - { name: a2, cidr: 10.70.2.0/24 }
    - { name: b1, cidr: 10.80.1.0/24 }
  nodes:
    - name: hq
      kind: router
      image: debian-12-cloud@v1
      nets:
        - { segment: a1 }
        - { segment: a2 }
        - { segment: b1 }
    - { name: core, kind: switch, role: l2-switch }
    - { name: access, kind: switch, role: l2-switch }
    - name: ws
      role: corp-workstation
      image: debian-12-cloud@v1
      count: 3
      net: a1
      members:
        - { index: 2, role: jump-host }
    - name: printer
      role: printer
      image: debian-12-cloud@v1
      nets:
        - { segment: a2, ip: 10.70.2.10 }
    - { name: srv, role: file-server, image: debian-12-cloud@v1, net: b1 }
  links:
    - { a: { device: hq, port: eth1 }, b: { device: core, port: SFP25 }, mode: trunk, vlans: [a1, a2, b1] }
    - { a: { device: core, port: P1 }, b: { device: access, port: Po48 }, mode: trunk, native: a1, vlans: [a1, a2] }
    - { a: { device: access, port: P2 }, b: { device: printer, port: eth0 }, mode: access, vlans: [a2] }
`;
const TFC = `traffic-profile:
  target: sub
  assignments:
    - node: ws
      persona: corp-workstation
    - node: ws02
      persona: backup-server
`;
const TAP = `tap-plan:\n  target: sub\n  bindings:\n    - selector: { scope: segment, target: a1 }\n      sensors: [x]\n      mode: exclusive\n`;
const doc = (files: Record<string, string> = { "te.yaml": TE, "tfc/default.yaml": TFC, "tap/default.yaml": TAP }) => ({ id: "sub", teFile: "te.yaml", files });
const model = (d = doc()) => readDoc(d);

describe("the subtree", () => {
  it("depths run from the router; a device carries its links' segments", () => {
    const d = deviceDepths(model().te);
    expect([...d]).toEqual([["hq", 0], ["core", 1], ["access", 2]]);
    expect(carriedBy(model().te, "access")).toEqual(["a1", "a2"]);
    expect(carriedBy(model().te, "core")).toEqual(["a1", "a2", "b1"]);
  });
  it("of the access switch: itself, a1 and a2 (their deepest carrier), the workstations and the printer, its links but not its uplink", () => {
    const t = subtreeOf(model(), "access");
    expect(t.devices).toEqual(["access"]);
    expect(t.segments.sort()).toEqual(["a1", "a2"]);
    expect(t.hosts).toEqual(["ws", "printer"]);
    expect(t.links).toEqual([2]);
    expect(t.uplink?.index).toBe(1);
  });
  it("of core: both switches, every segment but the router's own b1 stays with core (its deepest carrier), all three links below the uplink", () => {
    const t = subtreeOf(model(), "core");
    expect(t.devices).toEqual(["core", "access"]);
    expect(t.segments.sort()).toEqual(["a1", "a2", "b1"]);
    expect(t.hosts).toEqual(["ws", "printer", "srv"]);
    expect(t.links).toEqual([1, 2]);
    expect(t.uplink?.index).toBe(0);
  });
  it("of a host: the host alone", () => {
    const t = subtreeOf(model(), "ws");
    expect(t).toMatchObject({ devices: [], segments: [], hosts: ["ws"], links: [], uplink: null });
  });
  it("free ports skip the used ones", () => {
    expect(freePort(model().te, "access")).toBe("P1");
    expect(freePort(model().te, "core")).toBe("P2");
    expect(freePort(model().te, "hq")).toBe("eth2");
    expect(freePort(model().te, "ws")).toBe("eth1");
  });
});

describe("duplicate", () => {
  it("copies the access subtree under -2 names onto core: new /24s after the originals, addresses re-allocated, overrides and bindings copied, a trunk from core's next port, no tap", () => {
    const r = duplicateSubtree(doc(), model(), "access", "core");
    expect(r.root).toBe("access-2");
    expect(r.names).toEqual({ access: "access-2", ws: "ws-2", printer: "printer-2", a1: "a1-2", a2: "a2-2" });
    const te = readTe(r.files["te.yaml"]!);
    expect(te.problems).toEqual([]);
    expect(te.segments.map((s) => [s.name, s.cidr, s.zoneType])).toEqual([["a1", "10.70.1.0/24", "vlan"], ["a2", "10.70.2.0/24", null], ["b1", "10.80.1.0/24", null], ["a1-2", "10.70.3.0/24", "vlan"], ["a2-2", "10.70.4.0/24", null]]);
    const ws2 = te.nodes.find((n) => n.name === "ws-2")!;
    expect(ws2).toMatchObject({ role: "corp-workstation", count: 3, nets: [{ segment: "a1-2", ip: null }] });
    expect(ws2.members.map((m) => [m.index, m.role])).toEqual([[2, "jump-host"]]);
    expect(te.nodes.find((n) => n.name === "printer-2")!.nets).toEqual([{ segment: "a2-2", ip: "10.70.4.10" }]);
    const sw = te.nodes.find((n) => n.name === "access-2")!;
    expect(sw.kind).toBe("switch");
    expect(sw.image).toBe("");
    expect(te.links.slice(3).map((l) => [l.a.device, l.a.port, l.b.device, l.b.port, l.mode, l.native, l.vlans])).toEqual([
      ["access-2", "P2", "printer-2", "eth0", "access", null, ["a2-2"]],
      ["core", "P2", "access-2", "Po48", "trunk", "a1-2", ["a1-2", "a2-2"]],
    ]);
    expect(readTfc(r.files["tfc/default.yaml"]!).assignments.map((a) => [a.target, a.persona])).toEqual([["ws", "corp-workstation"], ["ws02", "backup-server"], ["ws-2", "corp-workstation"], ["ws-202", "backup-server"]]);
    expect(r.files["tap/default.yaml"]).toBe(TAP);
    // The originals read as before.
    expect(r.files["te.yaml"]!.split("\n").slice(0, 6)).toEqual(TE.split("\n").slice(0, 6));
  });
  it("parked (no target) writes no uplink; a second copy takes -3", () => {
    const r = duplicateSubtree(doc(), model(), "access", null);
    const te = readTe(r.files["te.yaml"]!);
    expect(te.links.length).toBe(4);
    const again = duplicateSubtree({ ...doc(), files: r.files }, readDoc({ ...doc(), files: r.files }), "access", null);
    expect(again.root).toBe("access-3");
    expect(readTe(again.files["te.yaml"]!).segments.map((s) => s.cidr).slice(-2)).toEqual(["10.70.5.0/24", "10.70.6.0/24"]);
  });
  it("refuses a target inside the subtree or a host target", () => {
    expect(() => duplicateSubtree(doc(), model(), "core", "access")).toThrow(/inside the subtree/);
    expect(() => duplicateSubtree(doc(), model(), "access", "ws")).toThrow(/not a switch or a router/);
  });
});

describe("attach", () => {
  it("device to device: a trunk carrying what the deeper one carries, free ports at both ends", () => {
    const r = duplicateSubtree(doc(), model(), "access", null);
    const d = { ...doc(), files: r.files };
    const files = attachDevices(d, readDoc(d), "core", "access-2");
    const l = readTe(files["te.yaml"]!).links.at(-1)!;
    // The parked copy kept what the original's uplink carried (a1 native) as its own port, so the trunk carries both (R3-2).
    expect([l.a.device, l.a.port, l.b.device, l.b.port, l.mode, l.vlans]).toEqual(["core", "P2", "access-2", "P1", "trunk", ["a1-2", "a2-2"]]);
  });
  it("device to host: an access link on the host's first segment; a host end may come first", () => {
    const files = attachDevices(doc(), model(), "srv", "core");
    const l = readTe(files["te.yaml"]!).links.at(-1)!;
    expect([l.a.device, l.a.port, l.b.device, l.b.port, l.mode, l.vlans]).toEqual(["core", "P2", "srv", "eth1", "access", ["b1"]]);
  });
  it("refuses two hosts, a ×N group, an already linked pair and a device carrying nothing", () => {
    expect(() => attachDevices(doc(), model(), "srv", "ws")).toThrow(/needs a device/);
    expect(() => attachDevices(doc(), model(), "core", "ws")).toThrow(/group of 3/);
    expect(() => attachDevices(doc(), model(), "core", "access")).toThrow(/already linked/);
    const d = doc({ "te.yaml": TE + "    - { name: lone, kind: switch, role: l2-switch }\n".replace("    - { name", "  nodes2: []\n").slice(0, 0) + "" });
    void d;
    const bare = doc({ "te.yaml": TE.replace("    - { name: srv,", "    - { name: lone, kind: switch, role: l2-switch }\n    - { name: srv,") });
    expect(() => attachDevices(bare, readDoc(bare), "core", "lone")).toThrow(/carries no segment/);
  });
});

// ---- on the page ----
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach } from "vitest";

import { seeded, settle, twinYaml } from "./helpers/canvasFixtures";
import { renderAt } from "./helpers/render";

function withTwin(id: string, files: Record<string, string>) {
  const base = seeded();
  return seeded({
    twins: { ...base.state.twins, [id]: { ...base.state.twins["corp-auth"]!, id } },
    documents: { ...base.state.documents, [id]: { id, te_file: "te.yaml", files: { "twin.yaml": twinYaml(id), ...files }, skipped: [] } },
    twinConfigs: [...base.state.twinConfigs, { id, description: null, cluster: null, parameterized: false, param_names: [], error: null }],
  });
}
const lastFiles = (state: ReturnType<typeof seeded>["state"]) => (state.writes.filter((w) => w.op === "validate").at(-1)!.body as { files: Record<string, string> }).files;
const device = (name: string) => screen.getAllByTestId("canvas-device").find((d) => d.dataset["nodeId"] === name)!;
const hostIcon = (name: string) => screen.getAllByTestId("canvas-host").find((h) => h.dataset["nodeId"] === name)!;

afterEach(() => {
  localStorage.clear();
  sessionStorage.clear();
});

describe("on the page", () => {
  it("Alt-click on a switch selects its subtree, the panel counting it; Ctrl+D parks a copy and selects it", async () => {
    const { api, state } = withTwin("sub", { "te.yaml": TE, "tfc/default.yaml": TFC });
    renderAt(api, "/twins/sub/canvas");
    await screen.findAllByTestId("canvas-host");
    fireEvent.click(device("access"), { altKey: true });
    const sp = await screen.findByTestId("subtree-panel");
    expect(within(sp).getByTestId("subtree-count").textContent).toBe("1 switch · 2 segments · 4 hosts");
    expect(device("access").classList.contains("selected")).toBe(true);
    expect(hostIcon("printer").classList.contains("selected")).toBe(true);
    expect(hostIcon("srv").classList.contains("selected")).toBe(false);
    const validatesBefore = state.writes.filter((w) => w.op === "validate").length;
    fireEvent.keyDown(window, { key: "d", ctrlKey: true });
    await settle();
    // R3-2: the copy is held locally, parked: drawn and selected, never sent to the API to be refused.
    await waitFor(() => expect(screen.getByTestId("subtree-panel").textContent).toContain("access-2"));
    expect(screen.getByTestId("subtree-panel").textContent).toContain("none (parked)");
    await waitFor(() => expect(screen.getAllByTestId("canvas-device").map((d) => d.dataset["nodeId"]).sort()).toEqual(["access", "access-2", "core"]));
    expect(within(device("access-2")).getByTestId("parked-badge").textContent).toBe("not attached");
    expect(screen.getByTestId("parked-reason").textContent).toContain("access-2 is parked, not attached");
    expect(state.writes.filter((w) => w.op === "validate").length).toBe(validatesBefore);
    expect((screen.getByRole("button", { name: "Save" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByRole("button", { name: "Save" }).getAttribute("title")).toContain("parked");
    expect(hostIcon("ws-2")).toBeTruthy(); // the copy's hosts came too; its taps never do
    // One undo step removes the whole copy, and the hold lifts.
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    await waitFor(() => expect(screen.getAllByTestId("canvas-device").map((d) => d.dataset["nodeId"]).sort()).toEqual(["access", "core"]));
    expect(screen.queryByTestId("parked-reason")).toBeNull();
  });
  it("R3-2: a parked copy validates once attached, and the copy carries no tap", async () => {
    const TAP = "tap-plan:\n  target: sub\n  bindings:\n    - { selector: { scope: segment, target: a1 }, mode: exclusive, sensors: [s1] }\n";
    const { api, state } = withTwin("sub", { "te.yaml": TE, "tap/default.yaml": TAP, "twin.yaml": twinYaml("sub").replace("tfc:", "tap:\n      default: tap/default.yaml\n    tfc:") });
    renderAt(api, "/twins/sub/canvas");
    await screen.findAllByTestId("canvas-host");
    fireEvent.click(device("access"), { altKey: true });
    fireEvent.keyDown(window, { key: "d", ctrlKey: true });
    await screen.findByTestId("parked-reason");
    fireEvent.click(screen.getByTestId("rail-attach"));
    fireEvent.click(screen.getAllByTestId("canvas-router")[0]!);
    fireEvent.click(device("access-2"));
    await waitFor(() => expect(screen.queryByTestId("parked-reason")).toBeNull());
    await settle();
    const files = lastFiles(state);
    const te = readTe(files["te.yaml"]!);
    expect(te.nodes.map((n) => n.name)).toContain("access-2");
    expect(te.links.at(-1)).toMatchObject({ a: { device: "hq" }, b: { device: "access-2" }, mode: "trunk" });
    if (files["tap/default.yaml"]) expect(files["tap/default.yaml"]).not.toContain("a1-2");
  });
  it("the subtree panel duplicates onto a device: the copy hangs from core by a trunk", async () => {
    const { api, state } = withTwin("sub", { "te.yaml": TE });
    renderAt(api, "/twins/sub/canvas");
    await screen.findAllByTestId("canvas-host");
    fireEvent.click(device("access"), { altKey: true });
    const sp = await screen.findByTestId("subtree-panel");
    fireEvent.change(within(sp).getByLabelText("Duplicate subtree onto device"), { target: { value: "core" } });
    fireEvent.click(within(sp).getByTestId("duplicate-subtree"));
    await settle();
    const te = readTe(lastFiles(state)["te.yaml"]!);
    expect(te.links.at(-1)).toMatchObject({ a: { device: "core", port: "P2" }, b: { device: "access-2", port: "Po48" }, mode: "trunk", native: "a1-2" });
  });
  it("the attach tool between a device and a host writes an access link; between two devices a trunk", async () => {
    const { api, state } = withTwin("sub", { "te.yaml": TE });
    renderAt(api, "/twins/sub/canvas");
    await screen.findAllByTestId("canvas-host");
    fireEvent.click(screen.getByTestId("rail-attach"));
    fireEvent.click(device("core"));
    fireEvent.click(hostIcon("srv"));
    await settle();
    let te = readTe(lastFiles(state)["te.yaml"]!);
    expect(te.links.at(-1)).toMatchObject({ a: { device: "core", port: "P2" }, b: { device: "srv", port: "eth1" }, mode: "access", vlans: ["b1"] });
    // A parked switch copy, then the tool from hq to it: a trunk carrying what the copy carries.
    fireEvent.click(device("access"), { altKey: true });
    fireEvent.keyDown(window, { key: "d", ctrlKey: true });
    await settle();
    // The tool stays armed after a link: the next two clicks wire the next pair.
    fireEvent.click(screen.getAllByTestId("canvas-router")[0]!);
    fireEvent.click(device("access-2"));
    await settle();
    te = readTe(lastFiles(state)["te.yaml"]!);
    expect(te.links.at(-1)).toMatchObject({ a: { device: "hq", port: "eth2" }, b: { device: "access-2", port: "P1" }, mode: "trunk", vlans: ["a1-2", "a2-2"] });
  });
});

describe("parkedDevices (R3-2)", () => {
  it("a copy whose only link is down is parked: a down link carries nothing (switch-test's access-sw copy)", async () => {
    const { readFileSync } = await import("node:fs");
    const { resolve } = await import("node:path");
    const { parkedDevices } = await import("../src/features/edit-canvas/edit/subtree");
    const te = readFileSync(resolve(__dirname, "../../catalog/twins/switch-test/te.yaml"), "utf8");
    const d = { id: "s", teFile: "te.yaml", files: { "te.yaml": te } };
    const m = readDoc(d);
    expect(parkedDevices(m.te)).toEqual([]);
    const out = duplicateSubtree(d, m, "access-sw", null);
    expect(parkedDevices(readDoc({ ...d, files: out.files }).te)).toEqual(["access-sw-2"]);
    const attached = attachDevices({ ...d, files: out.files }, readDoc({ ...d, files: out.files }), "gw", "access-sw-2");
    expect(parkedDevices(readDoc({ ...d, files: attached }).te)).toEqual([]);
  });
});
