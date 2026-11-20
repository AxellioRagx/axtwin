/**
 * The whole-fabric conversion (`docs/design/native-switches.md` §12 S5, D11): the engine's plan for
 * corp-twin (`fixtures/corp-twin-fabric-plan.json`, pinned to the engine's answer by
 * `engine/tests/test_fabric_conversion.py`) applied to corp-twin's files as one edit, and the
 * inspector's offer on a bridge VM: one click converts every bridge VM, one undo restores them.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { FabricPlan } from "../src/api/types";
import { documentGraph, readDoc, type WorkingDoc } from "../src/features/edit-canvas/edit/document";
import { applyFabricPlan, isBridgeVm } from "../src/features/edit-canvas/edit/fabric";
import { readTap, spanOn } from "../src/features/edit-canvas/edit/tap";
import { readTe } from "../src/features/edit-canvas/edit/te";
import { check, seeded, settle, twinYaml } from "./helpers/canvasFixtures";
import { renderAt } from "./helpers/render";

const CORP = resolve(__dirname, "../../catalog/twins/corp-twin");
const read = (f: string) => readFileSync(resolve(CORP, f), "utf8");
const PLAN = JSON.parse(readFileSync(resolve(__dirname, "fixtures/corp-twin-fabric-plan.json"), "utf8")) as FabricPlan;
const TAPS = ["tap/default.yaml", "tap/uplink-span.yaml", "tap/gate90.yaml", "tap/fanout-suricata.yaml"];

function corpDoc(): WorkingDoc {
  const files: Record<string, string> = { "te.yaml": read("te.yaml"), "twin.yaml": read("twin.yaml") };
  for (const t of TAPS) files[t] = read(t);
  return { id: "corp-twin", teFile: "te.yaml", files };
}

afterEach(() => {
  localStorage.clear();
  sessionStorage.clear();
});

describe("applyFabricPlan on corp-twin", () => {
  const d0 = corpDoc();
  const files = applyFabricPlan(d0, readDoc(d0), PLAN);
  const te = readTe(files["te.yaml"]!);

  it("replaces every bridge VM with an imageless switch and adds the links", () => {
    expect(te.problems).toEqual([]);
    expect(te.nodes.filter(isBridgeVm)).toEqual([]);
    const sw = te.nodes.filter((n) => n.kind === "switch");
    expect(sw.map((n) => n.name).sort()).toEqual([...PLAN.bridges].sort());
    expect(sw.every((n) => !n.image && n.role === "l2-switch")).toBe(true);
    expect(te.nodes.find((n) => n.name === "ms120")!.nets).toEqual([{ segment: "mx-vlan20", ip: "10.180.16.15" }]);
    expect(te.links.length).toBe(PLAN.links.length);
    const sfp26 = te.links.find((l) => l.a.device === "mx-sw" && l.a.port === "sfp26")!;
    expect(sfp26).toMatchObject({ mode: "trunk", native: "mx-vlan1", state: "blocked" });
    expect(te.links.find((l) => l.a.port === "p17-hwlab2960s")!.state).toBe("down");
  });

  it("removes the partner and trunk segments and moves their legs, pinned addresses kept", () => {
    const names = new Set(te.segments.map((s) => s.name));
    for (const s of PLAN.remove_segments) expect(names.has(s)).toBe(false);
    expect(te.nodes.find((n) => n.name === "9k01")!.nets).toEqual([
      { segment: "mx-vlan935", ip: "10.180.93.2" },
      { segment: "vlan953", ip: "10.180.34.2" },
    ]);
    const dangling = te.nodes.flatMap((n) => n.nets.filter((l) => !names.has(l.segment)).map((l) => `${n.name}@${l.segment}`));
    expect(dangling).toEqual([]);
  });

  it("folds the MS120 SPAN into one span on ms120 and retargets the host-side bindings", () => {
    const tap = readTap(files["tap/default.yaml"]!);
    expect(tap.problems).toEqual([]);
    const span = spanOn(tap, "ms120")!;
    expect(span.sources.map((s) => s.port)).toEqual(["sfp25", "sfp26", "t-ms120-sgs5240", "t-ms120-axeprod", "t-ms120-cos", "t-ms120-labsw", "t-ms120-3850", "p17-hwlab2960s"]);
    expect(span.sensors).toEqual(["XPress-SW-A"]);
    const segs = tap.bindings.filter((b) => b.scope === "segment").flatMap((b) => b.targets);
    expect(segs).toContain("mx-vlan20");
    expect(segs).toContain("mx-vlan1");
    for (const s of PLAN.remove_segments) expect(segs).not.toContain(s);
    for (const t of TAPS) expect(readTap(files[t]!).problems).toEqual([]);
  });

  it("the converted document draws", () => {
    const g = documentGraph(readDoc({ ...d0, files }), "corp-twin");
    expect(g.exportData.nodes.length).toBeGreaterThan(0);
  });

  it("refuses a plan for bridges the document does not have", () => {
    const other: WorkingDoc = { id: "x", teFile: "te.yaml", files: { "te.yaml": read("te.yaml").replace("    - name: sgs-5240\n", "    - name: sgs-5240-renamed\n") } };
    expect(() => applyFabricPlan(other, readDoc(other), PLAN)).toThrow(/sgs-5240/);
  });
});

const MINI = `twin_config:
  id: mini-bridge
  segments:
    - { name: lan, cidr: 10.1.0.0/24, zone_type: simple }
    - { name: lan-access, cidr: 10.1.0.0/24, zone_type: simple, isolated: true }
  nodes:
    - name: gw
      role: gw
      kind: router
      image: debian-12-cloud@v1
      nets:
        - { segment: lan }
    - name: sw1
      role: l2-switch
      image: debian-12-cloud@v1
      nets:
        - { segment: lan, l2: true }
        - { segment: lan-access, l2: true }
    - name: ws
      image: debian-12-cloud@v1
      net: lan-access
`;
const MINI_PLAN: FabricPlan = {
  recipe: "scripts/serve-l2switch.sh",
  bridges: ["sw1"],
  switches: [{ name: "sw1", role: "l2-switch", stp: null, nets: [] }],
  remove_segments: ["lan-access"],
  node_nets: [{ node: "ws", nets: [{ segment: "lan", ip: null }] }],
  links: [{ a: { device: "sw1", port: "lan" }, b: { device: "gw", port: "eth0" }, mode: "access", native: null, vlans: ["lan"], state: null }],
  taps: [],
  notes: ["a note from the engine"],
  problems: [],
};

describe("the inspector's offer", () => {
  function withMini(plan?: FabricPlan) {
    const base = seeded();
    const id = "mini-bridge";
    return seeded({
      fabricPlan: plan,
      twins: { ...base.state.twins, [id]: { ...base.state.twins["corp-auth"]!, id } },
      documents: { ...base.state.documents, [id]: { id, te_file: "te.yaml", files: { "te.yaml": MINI, "twin.yaml": twinYaml(id) }, skipped: [] } },
      twinConfigs: [...base.state.twinConfigs, { id, description: null, cluster: null, parameterized: false, param_names: [], error: null }],
    });
  }
  const lastTe = async (state: ReturnType<typeof seeded>["state"]) => (await check(), readTe((state.writes.filter((w) => w.op === "validate").at(-1)!.body as { files: Record<string, string> }).files["te.yaml"]!));
  const select = async (name: string) => {
    const el = (await screen.findAllByTestId(/^canvas-(host|router|device)$/)).find((x) => x.dataset["nodeId"] === name);
    if (!el) throw new Error(JSON.stringify([...document.querySelectorAll("[data-node-id]")].map((x) => `${x.getAttribute("data-testid")}:${x.getAttribute("data-node-id")}`)));
    fireEvent.click(el);
    return screen.findByTestId("node-panel");
  };

  it("converts the bridge VMs in one edit, shows the engine's notes, and one undo restores them", async () => {
    const { api, state } = withMini(MINI_PLAN);
    renderAt(api, "/twins/mini-bridge/canvas");
    const panel = await select("sw1");
    const run = within(panel).getByTestId("convert-fabric-run");
    expect(run.textContent).toContain("1 bridge VMs");
    fireEvent.click(run);
    await settle();
    await waitFor(() => expect(state.writes.some((w) => w.op === "fabric-conversion")).toBe(true));
    await waitFor(async () => expect((await lastTe(state)).nodes.find((n) => n.name === "sw1")?.kind).toBe("switch"));
    const te = (await lastTe(state));
    expect(te.segments.map((s) => s.name)).toEqual(["lan"]);
    expect(te.nodes.find((n) => n.name === "ws")!.nets).toEqual([{ segment: "lan", ip: null }]);
    expect(te.links).toHaveLength(1);
    expect((await screen.findByTestId("convert-fabric-notes")).textContent).toContain("a note from the engine");
    fireEvent.keyDown(window, { key: "z", ctrlKey: true });
    await settle();
    await waitFor(async () => expect((await lastTe(state)).nodes.find((n) => n.name === "sw1")?.kind).toBe("host"));
    expect((await lastTe(state)).segments.map((s) => s.name)).toEqual(["lan", "lan-access"]);
  });

  it("shows the refusal in place and changes nothing", async () => {
    const { api, state } = withMini(undefined);
    renderAt(api, "/twins/mini-bridge/canvas");
    const panel = await select("sw1");
    await settle();
    const before = JSON.stringify(await lastTe(state));
    fireEvent.click(within(panel).getByTestId("convert-fabric-run"));
    expect((await within(panel).findByTestId("convert-fabric-error")).textContent).toContain("no bridge VM");
    await settle();
    expect(JSON.stringify(await lastTe(state))).toBe(before);
  });
});
