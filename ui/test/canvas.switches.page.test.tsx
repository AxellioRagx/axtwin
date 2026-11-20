/**
 * Segments drawn as switches, on the page (topology-layers §7.1, 2026-11-11): an implied switch's
 * panel says what it is and offers Insert switch, which replaces it with a real switch through the
 * command stack and the API validate; an armed host clicked on a router lands on a new segment
 * under it, on a real switch on that switch's segment; a host on a switch of several segments picks
 * its VLAN in the inspector.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { readTe } from "../src/features/edit-canvas/edit/te";
import { check, seeded, segment, segments, settle, twinYaml } from "./helpers/canvasFixtures";
import { placeOnDevice } from "../src/features/edit-canvas/Canvas";
import { applyDeviceDrop } from "../src/features/edit-canvas/add/drop";
import { readDoc } from "../src/features/edit-canvas/edit/document";
import { renderAt } from "./helpers/render";

const CT = readFileSync(resolve(__dirname, "../../catalog/twins/canvas-test/te.yaml"), "utf8");

function withTwin(id: string, te: string) {
  const base = seeded();
  return seeded({
    twins: { ...base.state.twins, [id]: { ...base.state.twins["corp-auth"]!, id } },
    documents: { ...base.state.documents, [id]: { id, te_file: "te.yaml", files: { "te.yaml": te, "twin.yaml": twinYaml(id) }, skipped: [] } },
    twinConfigs: [...base.state.twinConfigs, { id, description: null, cluster: null, parameterized: false, param_names: [], error: null }],
  });
}
/** The files of the last API validate: what the editor holds after the edit. */
/** The files on screen: a Check sends exactly them (nothing validates on its own any more). */
const lastFiles = async (state: ReturnType<typeof seeded>["state"]) => (await check(), state.writes.filter((w) => w.op === "validate").at(-1)!.body as { files: Record<string, string> }).files;
const device = (name: string) => screen.getAllByTestId("canvas-device").find((d) => d.dataset["nodeId"] === name)!;

afterEach(() => {
  localStorage.clear();
  sessionStorage.clear();
});

describe("Insert switch", () => {
  it("an implied switch's panel names it implied; Insert switch puts a real one in its place, validated, undoable", async () => {
    const { api, state } = withTwin("ct", CT);
    renderAt(api, "/twins/ct/canvas");
    await screen.findAllByTestId("canvas-host");
    expect(segments()).toEqual(["office", "servers"]);
    expect(screen.queryAllByTestId("canvas-device")).toEqual([]);
    fireEvent.click(segment("servers"));
    const sp = await screen.findByTestId("segment-panel");
    expect(sp.dataset["implied"]).toBe("true");
    expect(within(sp).getByTestId("implied-note").textContent).toContain("no switch in the config carries servers");
    const validatesBefore = state.writes.filter((w) => w.op === "validate").length;
    fireEvent.click(within(sp).getByTestId("insert-switch"));
    await settle();
    // The real switch is drawn and selected; servers' implied switch is gone.
    await waitFor(() => expect(device("sw-servers")).toBeTruthy());
    expect(segments()).toEqual(["office"]);
    expect(within(await screen.findByTestId("node-panel")).getAllByTestId("carried-segment").map((r) => r.dataset["segment"])).toEqual(["servers"]);
    const te = readTe((await lastFiles(state))["te.yaml"]!);
    expect(te.nodes.find((n) => n.name === "sw-servers")).toMatchObject({ kind: "switch", source: "authored" });
    expect(te.links.map((l) => `${l.a.device}>${l.b.device} ${l.mode}`)).toEqual(["gw>sw-servers trunk", "sw-servers>file-server access"]);
    await waitFor(() => expect(state.writes.filter((w) => w.op === "validate").length).toBeGreaterThan(validatesBefore));
    // One undo step puts the implied switch back.
    fireEvent.keyDown(window, { key: "z", ctrlKey: true });
    await settle();
    await waitFor(() => expect(segments()).toEqual(["office", "servers"]));
    expect(screen.queryAllByTestId("canvas-device")).toEqual([]);
  });
  it("the implied switch's context menu offers Insert switch too", async () => {
    const { api, state } = withTwin("ct", CT);
    renderAt(api, "/twins/ct/canvas");
    await screen.findAllByTestId("canvas-host");
    fireEvent.contextMenu(segment("office"));
    fireEvent.click(within(await screen.findByTestId("context-menu")).getByRole("button", { name: "Insert switch" }));
    await settle();
    await waitFor(() => expect(device("sw-office")).toBeTruthy());
    expect(readTe((await lastFiles(state))["te.yaml"]!).links.map((l) => `${l.a.device}>${l.b.device}`)).toEqual(["gw>sw-office"]);
  });
});

describe("placement infers the segment", () => {
  // A drop lands per the §7.1 rules (`placeOnDevice`, the target a drag resolves under the pointer);
  // jsdom measures nothing, so the target is resolved here and the drop applied as the page applies it.
  it("a host dropped on the router lands on a new segment under it (a new implied switch)", () => {
    const doc = { id: "ct", teFile: "te.yaml", files: { "te.yaml": CT } };
    const model = readDoc(doc);
    const t = placeOnDevice(model.te, "gw");
    expect(t).toEqual({ router: "gw" });
    const r = applyDeviceDrop(doc, model, { card: "server", os: "linux", role: null, persona: null, count: 1, prefix: "srv", target: t as { router: string }, image: "debian-12-cloud@v1" });
    const te = readTe(r.files["te.yaml"]!);
    expect(te.segments.find((s) => s.name === "segment-3")?.cidr).toBe("10.70.21.0/24");
    expect(te.nodes.find((n) => n.name === "gw")!.nets.map((l) => l.segment)).toContain("segment-3");
    expect(te.nodes.find((n) => n.name === "srv")).toMatchObject({ image: "debian-12-cloud@v1", nets: [{ segment: "segment-3", ip: "10.70.21.10" }] });
  });
  it("a host dropped on a real switch lands on its segment; a switch of several offers the VLAN picker", async () => {
    const te = `twin_config:
  id: multi
  segments:
    - { name: users, cidr: 10.50.1.0/24 }
    - { name: voice, cidr: 10.50.2.0/24 }
  nodes:
    - { name: gw, kind: router, image: vyos@v1, nets: [{ segment: users }, { segment: voice }] }
    - { name: core, kind: switch, role: l2-switch }
    - { name: pc, role: workstation, image: debian-12-cloud@v1, nets: [{ segment: voice, ip: 10.50.2.10 }] }
  links:
    - { a: { device: gw, port: eth1 }, b: { device: core, port: P1 }, mode: trunk, native: users, vlans: [users, voice] }
`;
    // The trunk's native, users.
    const doc = { id: "multi", teFile: "te.yaml", files: { "te.yaml": te } };
    const model = readDoc(doc);
    const t = placeOnDevice(model.te, "core");
    expect(t).toEqual({ segment: "users" });
    const placed = readTe(applyDeviceDrop(doc, model, { card: "server", os: "linux", role: null, persona: null, count: 1, prefix: "srv", target: t as { segment: string }, image: "debian-12-cloud@v1" }).files["te.yaml"]!).nodes.find((n) => n.name === "srv")!;
    expect(placed.nets.map((l) => l.segment)).toEqual(["users"]);
    // The picker moves pc to the other VLAN, re-addressed there.
    const { api, state } = withTwin("multi", te);
    renderAt(api, "/twins/multi/canvas");
    await screen.findAllByTestId("canvas-host");
    expect(screen.queryAllByTestId("canvas-segment")).toEqual([]); // core carries both: no implied switch
    fireEvent.click(screen.getAllByTestId("canvas-host").find((h) => h.dataset["nodeId"] === "pc")!);
    const picker = within(await screen.findByTestId("node-panel")).getByTestId("vlan-picker") as HTMLSelectElement;
    expect([...picker.options].map((o) => o.value)).toEqual(["users", "voice"]);
    expect(picker.value).toBe("voice");
    fireEvent.change(picker, { target: { value: "users" } });
    await settle();
    await waitFor(async () => expect(readTe((await lastFiles(state))["te.yaml"]!).nodes.find((n) => n.name === "pc")!.nets).toEqual([{ segment: "users", ip: "10.50.1.10" }]), { timeout: 3000 });
  });
});

