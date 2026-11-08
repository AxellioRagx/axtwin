/**
 * Groups and members (`docs/design/topology-layers.md` §5, track 5c,
 * 2026-11-07): split, merge, detach and move-one over the document
 * (`edit/members.ts`); on the canvas the `×N` badge's dot for overrides and
 * its double-click into member chips, a chip's click to the member panel
 * and its drag onto another group; the Members block's split form and the
 * node panel's merge.
 */
import { describe, expect, it } from "vitest";

import { readDoc } from "../src/features/edit-canvas/edit/document";
import { detachMember, instanceId, mergeGroups, mergeTargets, moveMember, splitGroup } from "../src/features/edit-canvas/edit/members";
import { readTe } from "../src/features/edit-canvas/edit/te";
import { readTfc } from "../src/features/edit-canvas/edit/tfc";

const TE = `twin_config:
  id: mem
  segments:
    - { name: office, cidr: 10.71.10.0/24 }
    - { name: servers, cidr: 10.71.20.0/24 }
  nodes:
    - name: ws
      role: corp-workstation
      image: debian-12-cloud@v1
      count: 4
      net: office
      members:
        - { index: 2, role: jump-host }
        - { index: 4, role: kiosk }
    - name: ops
      role: ops-workstation
      image: debian-12-cloud@v1
      count: 2
      net: office
    - name: srv
      role: file-server
      image: ubuntu-server-22@v5
      net: servers
    - name: access-sw
      kind: switch
      role: l2-switch
  links:
    - { a: { device: access-sw, port: P1 }, b: { device: srv, port: eth0 }, mode: access, vlans: [servers] }
`;
const TFC = `traffic-profile:
  target: mem
  assignments:
    - node: ws
      persona: corp-workstation
      intensity: 0.5
    - node: ws02
      persona: backup-server
    - node: ws04
      persona: kiosk-browser
    - node: ops
      persona: generic-endpoint
`;
const doc = (files: Record<string, string> = { "te.yaml": TE, "tfc/default.yaml": TFC }) => ({ id: "mem", teFile: "te.yaml", files });
const model = (d = doc()) => readDoc(d);
const nodeOf = (files: Record<string, string>, name: string) => readTe(files["te.yaml"]!).nodes.find((n) => n.name === name)!;
const bindings = (files: Record<string, string>) => readTfc(files["tfc/default.yaml"]!).assignments.map((a) => [a.target, a.persona, a.intensity]);

describe("split", () => {
  it("moves the picked members into a new group, re-indexing overrides and instance bindings on both sides; the group binding copies", () => {
    const files = splitGroup(doc(), model(), "ws", [2, 4], "jump");
    const ws = nodeOf(files, "ws");
    const jump = nodeOf(files, "jump");
    expect(ws.count).toBe(2);
    expect(ws.members).toEqual([]);
    expect(jump.count).toBe(2);
    expect(jump.role).toBe("corp-workstation");
    expect(jump.image).toBe("debian-12-cloud@v1");
    expect(jump.nets).toEqual([{ segment: "office", ip: null }]);
    expect(jump.members.map((m) => [m.index, m.role])).toEqual([[1, "jump-host"], [2, "kiosk"]]);
    expect(bindings(files)).toEqual([["ws", "corp-workstation", 0.5], ["jump01", "backup-server", null], ["jump02", "kiosk-browser", null], ["ops", "generic-endpoint", null], ["jump", "corp-workstation", 0.5]]);
    expect(instanceId("jump", 1)).toBe("jump01");
    // Nothing else moved: the segments, srv and the switch read the same.
    expect(files["te.yaml"]!.split("\n").slice(0, 5)).toEqual(TE.split("\n").slice(0, 5));
  });
  it("a kept member past a removed one re-indexes down, its binding following", () => {
    const files = splitGroup(doc(), model(), "ws", [1, 3], "other");
    expect(nodeOf(files, "ws").members.map((m) => [m.index, m.role])).toEqual([[1, "jump-host"], [2, "kiosk"]]);
    expect(bindings(files).map((b) => b[0])).toEqual(["ws", "ws01", "ws02", "ops", "other"]);
    expect(nodeOf(files, "other").members).toEqual([]);
  });
  it("refuses an empty pick, every member, a taken name and a parameterised count", () => {
    expect(() => splitGroup(doc(), model(), "ws", [], "x")).toThrow(/at least one/);
    expect(() => splitGroup(doc(), model(), "ws", [1, 2, 3, 4], "x")).toThrow(/leaves nothing/);
    expect(() => splitGroup(doc(), model(), "ws", [1], "ops")).toThrow(/already exists/);
    const d = doc({ "te.yaml": TE.replace("count: 4", "count: ${WS:4}") });
    expect(() => splitGroup(d, readDoc(d), "ws", [1], "x")).toThrow(/parameterised/);
  });
  it("detach splits one member off as `{name}-{index}`", () => {
    const r = detachMember(doc(), model(), "ws", 2);
    expect(r.newName).toBe("ws-2");
    expect(nodeOf(r.files, "ws-2").count).toBe(1);
    expect(nodeOf(r.files, "ws-2").members.map((m) => [m.index, m.role])).toEqual([[1, "jump-host"]]);
    expect(nodeOf(r.files, "ws").members.map((m) => [m.index, m.role])).toEqual([[3, "kiosk"]]);
    expect(bindings(r.files).map((b) => b[0])).toEqual(["ws", "ws-201", "ws03", "ops", "ws-2"]);
  });
});

describe("merge", () => {
  it("targets are hosts of the same image on the same segments", () => {
    expect(mergeTargets(model(), "ws")).toEqual(["ops"]);
    expect(mergeTargets(model(), "srv")).toEqual([]);
    expect(mergeTargets(model(), "access-sw")).toEqual([]);
  });
  it("a link naming the merged group re-points to the target", () => {
    const d = doc({ "te.yaml": TE.replace("    - name: srv\n      role: file-server\n      image: ubuntu-server-22@v5\n      net: servers\n", "    - name: srv\n      role: file-server\n      image: debian-12-cloud@v1\n      net: office\n") });
    const files = mergeGroups(d, readDoc(d), "srv", "ops");
    expect(readTe(files["te.yaml"]!).links[0]!.b.device).toBe("ops");
    expect(readTe(files["te.yaml"]!).links[0]!.a.device).toBe("access-sw");
    expect(nodeOf(files, "ops").members.map((m) => [m.index, m.role])).toEqual([[3, "file-server"]]);
  });
  it("folds ops into ws: the count grows, differing roles become overrides, bindings re-key, ops goes", () => {
    const files = mergeGroups(doc(), model(), "ops", "ws");
    const ws = nodeOf(files, "ws");
    expect(ws.count).toBe(6);
    expect(ws.members.map((m) => [m.index, m.role])).toEqual([[2, "jump-host"], [4, "kiosk"], [5, "ops-workstation"], [6, "ops-workstation"]]);
    expect(readTe(files["te.yaml"]!).nodes.map((n) => n.name)).toEqual(["ws", "srv", "access-sw"]);
    expect(readTe(files["te.yaml"]!).links[0]!.b.device).toBe("srv");
    expect(bindings(files)).toEqual([["ws", "corp-workstation", 0.5], ["ws02", "backup-server", null], ["ws04", "kiosk-browser", null], ["ws05", "generic-endpoint", null], ["ws06", "generic-endpoint", null]]);
  });
  it("folds ws into ops: ws's overrides and instance bindings land past ops's two; the role of the plain members becomes an override", () => {
    const files = mergeGroups(doc(), model(), "ws", "ops");
    const ops = nodeOf(files, "ops");
    expect(ops.count).toBe(6);
    expect(ops.members.map((m) => [m.index, m.role])).toEqual([[3, "corp-workstation"], [4, "jump-host"], [5, "corp-workstation"], [6, "kiosk"]]);
    expect(bindings(files)).toEqual([["ops04", "backup-server", null], ["ops06", "kiosk-browser", null], ["ops", "generic-endpoint", null], ["ops03", "corp-workstation", 0.5], ["ops05", "corp-workstation", 0.5]]);
  });
  it("refuses a different image or segments", () => {
    expect(() => mergeGroups(doc(), model(), "srv", "ws")).toThrow(/differ in image or segments/);
  });
  it("move-one detaches then merges: member 2 of ws joins ops as its third, with its role and persona", () => {
    const files = moveMember(doc(), model(), "ws", 2, "ops");
    expect(nodeOf(files, "ws").count).toBe(3);
    expect(nodeOf(files, "ws").members.map((m) => [m.index, m.role])).toEqual([[3, "kiosk"]]);
    expect(nodeOf(files, "ops").count).toBe(3);
    expect(nodeOf(files, "ops").members.map((m) => [m.index, m.role])).toEqual([[3, "jump-host"]]);
    expect(readTe(files["te.yaml"]!).nodes.map((n) => n.name)).toEqual(["ws", "ops", "srv", "access-sw"]);
    expect(bindings(files)).toEqual([["ws", "corp-workstation", 0.5], ["ops03", "backup-server", null], ["ws03", "kiosk-browser", null], ["ops", "generic-endpoint", null]]);
  });
});

// ---- on the page ----
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach } from "vitest";

import { commit, seeded, settle, twinYaml } from "./helpers/canvasFixtures";
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
const hostIcon = (name: string) => screen.getAllByTestId("canvas-host").find((h) => h.dataset["nodeId"] === name)!;

afterEach(() => {
  localStorage.clear();
  sessionStorage.clear();
});

describe("members on the canvas and in the inspector", () => {
  it("R3-8: a double-click anywhere on a ×N icon (not only its badge) opens and closes the chips; a ×1 icon has none", async () => {
    const { api } = withTwin("mem", { "te.yaml": TE, "tfc/default.yaml": TFC });
    renderAt(api, "/twins/mem/canvas");
    await screen.findAllByTestId("canvas-host");
    fireEvent.doubleClick(hostIcon("ws"));
    expect(within(await screen.findByTestId("member-chips")).getAllByTestId("member-chip").length).toBe(4);
    fireEvent.doubleClick(hostIcon("ws"));
    await waitFor(() => expect(screen.queryByTestId("member-chips")).toBeNull());
    fireEvent.doubleClick(hostIcon("srv"));
    expect(screen.queryByTestId("member-chips")).toBeNull();
  });
  it("R4-10: Esc folds the open chips back, the zoom's included; an overridden member's chip wears the dot", async () => {
    sessionStorage.setItem("axr-canvas-viewport:mem", JSON.stringify({ x: 0, y: 0, zoom: 2 }));
    const { api } = withTwin("mem", { "te.yaml": TE, "tfc/default.yaml": TFC });
    renderAt(api, "/twins/mem/canvas");
    await screen.findAllByTestId("canvas-host");
    await waitFor(() => expect(screen.queryAllByTestId("member-chips").length).toBe(2));
    const ws = within(screen.getAllByTestId("member-chips").find((st) => within(st).getAllByTestId("member-chip").length === 4)!);
    expect(ws.getAllByTestId("member-chip").map((c) => c.dataset["overridden"] ?? "")).toEqual(["", "true", "", "true"]);
    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() => expect(screen.queryByTestId("member-chips")).toBeNull());
    // A double-click opens one again.
    fireEvent.doubleClick(hostIcon("ws"));
    expect(within(await screen.findByTestId("member-chips")).getAllByTestId("member-chip").length).toBe(4);
    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() => expect(screen.queryByTestId("member-chips")).toBeNull());
  });
  it("past the zoom threshold every ×N group shows its chips; a double-click closes one there", async () => {
    sessionStorage.setItem("axr-canvas-viewport:mem", JSON.stringify({ x: 0, y: 0, zoom: 2 }));
    const { api } = withTwin("mem", { "te.yaml": TE, "tfc/default.yaml": TFC });
    renderAt(api, "/twins/mem/canvas");
    await screen.findAllByTestId("canvas-host");
    const strips = () => screen.queryAllByTestId("member-chips").map((st) => within(st).getAllByTestId("member-chip").length).sort();
    await waitFor(() => expect(strips()).toEqual([2, 4]));
    fireEvent.doubleClick(hostIcon("ws"));
    await waitFor(() => expect(strips()).toEqual([2]));
    fireEvent.doubleClick(hostIcon("ws"));
    await waitFor(() => expect(strips()).toEqual([2, 4]));
  });
  it("the ×N badge wears a dot for overrides; a double-click opens the chips; a chip opens the member panel, whose Detach makes a ×1 group", async () => {
    const { api, state } = withTwin("mem", { "te.yaml": TE, "tfc/default.yaml": TFC });
    renderAt(api, "/twins/mem/canvas");
    await screen.findAllByTestId("canvas-host");
    const badge = within(hostIcon("ws")).getByTestId("count-badge");
    expect(badge.dataset["overrides"]).toBe("2");
    expect(within(hostIcon("ops")).getByTestId("count-badge").dataset["overrides"]).toBeUndefined();
    expect(screen.queryByTestId("member-chips")).toBeNull();
    fireEvent.doubleClick(badge);
    const chips = within(await screen.findByTestId("member-chips")).getAllByTestId("member-chip");
    expect(chips.map((c) => [c.dataset["index"], c.classList.contains("overridden")])).toEqual([["1", false], ["2", true], ["3", false], ["4", true]]);
    fireEvent.click(chips[1]!);
    const mp = await screen.findByTestId("member-panel");
    expect(mp.textContent).toContain("ws02");
    expect((within(mp).getByLabelText("Member role") as HTMLInputElement).value).toBe("jump-host");
    expect((within(mp).getByLabelText("Member persona") as HTMLSelectElement).value).toBe("backup-server");
    fireEvent.click(within(mp).getByTestId("detach-member"));
    await settle();
    const te = readTe(lastFiles(state)["te.yaml"]!);
    expect(te.nodes.map((n) => [n.name, n.count])).toEqual([["ws", 3], ["ops", 2], ["srv", 1], ["access-sw", 1], ["ws-2", 1]]);
    expect(te.nodes.find((n) => n.name === "ws-2")!.members.map((m) => [m.index, m.role])).toEqual([[1, "jump-host"]]);
    // The detached group is selected: its node panel opens, and the canvas shows it.
    await waitFor(() => expect(screen.getByTestId("node-panel").textContent).toContain("ws-2"));
    await waitFor(() => expect(screen.getAllByTestId("canvas-host").map((h) => h.dataset["nodeId"]).sort()).toEqual(["ops", "srv", "ws", "ws-2"]));
  });
  it("a chip dragged onto another group moves that member there", async () => {
    const { api, state } = withTwin("mem", { "te.yaml": TE, "tfc/default.yaml": TFC });
    renderAt(api, "/twins/mem/canvas");
    await screen.findAllByTestId("canvas-host");
    fireEvent.doubleClick(within(hostIcon("ws")).getByTestId("count-badge"));
    const chips = within(await screen.findByTestId("member-chips")).getAllByTestId("member-chip");
    const dataTransfer = { setData() {}, getData: () => "", effectAllowed: "", dropEffect: "" };
    fireEvent.dragStart(chips[3]!, { dataTransfer });
    fireEvent.dragOver(hostIcon("ops"), { dataTransfer });
    fireEvent.drop(hostIcon("ops"), { dataTransfer });
    await settle();
    const te = readTe(lastFiles(state)["te.yaml"]!);
    expect(te.nodes.find((n) => n.name === "ws")!.count).toBe(3);
    expect(te.nodes.find((n) => n.name === "ops")!.count).toBe(3);
    expect(te.nodes.find((n) => n.name === "ops")!.members.map((m) => [m.index, m.role])).toEqual([[3, "kiosk"]]);
    expect(readTfc(lastFiles(state)["tfc/default.yaml"]!).assignments.map((a) => [a.target, a.persona]).sort()).toEqual([["ops", "generic-endpoint"], ["ops03", "kiosk-browser"], ["ws", "corp-workstation"], ["ws02", "backup-server"]]);
  });
  it("multi-select rows: Shift-click picks a range, the bulk row sets the role and the persona of every picked member as one undo step", async () => {
    const { api, state } = withTwin("mem", { "te.yaml": TE, "tfc/default.yaml": TFC });
    renderAt(api, "/twins/mem/canvas");
    await screen.findAllByTestId("canvas-host");
    fireEvent.click(hostIcon("ws"));
    const np = await screen.findByTestId("node-panel");
    expect(within(np).queryByTestId("members-bulk")).toBeNull();
    fireEvent.click(within(np).getByLabelText("Pick ws01"));
    fireEvent.click(within(np).getByLabelText("Pick ws03"), { shiftKey: true });
    expect(within(np).getAllByTestId("member-row").filter((r) => r.dataset["picked"]).map((r) => r.dataset["index"])).toEqual(["1", "2", "3"]);
    const bulk = within(np).getByTestId("members-bulk");
    fireEvent.change(within(bulk).getByLabelText("Role for picked members"), { target: { value: "lab-host" } });
    fireEvent.click(within(bulk).getByTestId("bulk-role"));
    await settle();
    expect(nodeOf(lastFiles(state), "ws").members.map((m) => [m.index, m.role]).sort()).toEqual([[1, "lab-host"], [2, "lab-host"], [3, "lab-host"], [4, "kiosk"]]);
    fireEvent.change(within(bulk).getByLabelText("Persona for picked members"), { target: { value: "generic-endpoint" } });
    fireEvent.click(within(bulk).getByTestId("bulk-persona"));
    await settle();
    const bound = readTfc(lastFiles(state)["tfc/default.yaml"]!).assignments.filter((a) => /^ws0[123]$/.test(a.target)).map((a) => [a.target, a.persona]).sort();
    expect(bound).toEqual([["ws01", "generic-endpoint"], ["ws02", "generic-endpoint"], ["ws03", "generic-endpoint"]]);
    // One Undo takes the three persona bindings back together, a second the three roles.
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    await settle();
    expect(readTfc(lastFiles(state)["tfc/default.yaml"]!).assignments.find((a) => a.target === "ws02")!.persona).toBe("backup-server");
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    await settle();
    expect(nodeOf(lastFiles(state), "ws").members.map((m) => [m.index, m.role]).sort()).toEqual([[2, "jump-host"], [4, "kiosk"]]);
    // The header box picks every member.
    fireEvent.click(within(np).getByLabelText("Pick every member"));
    expect(within(np).getAllByTestId("member-row").filter((r) => r.dataset["picked"]).length).toBe(4);
  });
  it("the Members block splits the picked members into a named group; the node panel merges into another group", async () => {
    const { api, state } = withTwin("mem", { "te.yaml": TE, "tfc/default.yaml": TFC });
    renderAt(api, "/twins/mem/canvas");
    await screen.findAllByTestId("canvas-host");
    fireEvent.click(hostIcon("ws"));
    const np = await screen.findByTestId("node-panel");
    fireEvent.click(within(np).getByLabelText("Pick ws01"));
    fireEvent.click(within(np).getByLabelText("Pick ws03"));
    expect((within(np).getByTestId("split") as HTMLButtonElement).disabled).toBe(true);
    commit(within(np).getByLabelText("New group name"), "plain");
    expect((within(np).getByTestId("split") as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(within(np).getByTestId("split"));
    await settle();
    let te = readTe(lastFiles(state)["te.yaml"]!);
    expect(te.nodes.map((n) => [n.name, n.count])).toEqual([["ws", 2], ["ops", 2], ["srv", 1], ["access-sw", 1], ["plain", 2]]);
    expect(te.nodes.find((n) => n.name === "ws")!.members.map((m) => [m.index, m.role])).toEqual([[1, "jump-host"], [2, "kiosk"]]);
    // Merge ws (×2, two overrides) into ops.
    const merge = within(np).getByLabelText("Merge into group") as HTMLSelectElement;
    expect([...merge.options].map((o) => o.value)).toEqual(["", "ops", "plain"]);
    fireEvent.change(merge, { target: { value: "ops" } });
    fireEvent.click(within(np).getByTestId("merge"));
    await settle();
    te = readTe(lastFiles(state)["te.yaml"]!);
    expect(te.nodes.map((n) => [n.name, n.count])).toEqual([["ops", 4], ["srv", 1], ["access-sw", 1], ["plain", 2]]);
    expect(te.nodes.find((n) => n.name === "ops")!.members.map((m) => [m.index, m.role])).toEqual([[3, "jump-host"], [4, "kiosk"]]);
    await waitFor(() => expect(screen.getByTestId("node-panel").textContent).toContain("ops"));
  });
});
