/**
 * The canvas and the target's lab (docs/design/targets-and-lab.md §2.3, §4 "auto, UI"): the tap inspector's Sensors
 * offers only the deployed (or default) target's lab sensors -- a stale id struck through with a remap; the rail's
 * Lab overlay, off by default, docks the lab beside the drawing with health dots and dotted tap -> sensor lines, is
 * never a topology node and never touches the working copy. Against the fake API only.
 */
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import type { Target } from "../src/api/types";
import { readTap } from "../src/features/edit-canvas/edit/tap";
import { DEPLOY, document, seeded, settle, twinYaml } from "./helpers/canvasFixtures";
import { renderAt } from "./helpers/render";

const TAP = "tap-plan:\n  target: corp-auth\n  bindings:\n    - selector: { scope: segment, target: net-lan }\n      sensors: [XPress-SW-A, Old-Sensor]\n      mode: exclusive\n";

const DEV45: Target = {
  node: "dev45", name: "dev45", default: true, nodes: ["dev45"], platform: "proxmox", error: null,
  // The merged list GET /api/targets reports: the lab's sensor, plus one still in adapter.conf (the one-release fallback).
  sensors: ["XPress-SW-A", "Tcpdump-SW-A"],
  lab: {
    components: [
      { id: "XPress-SW-A", role: "sensor", type: "packetxpress", reach: { vmid: 104, transport_ip: "10.255.251.21" } },
      { id: "grafana", role: "telemetry", type: "grafana", reach: { url: "http://10.80.72.52:3000" } },
      { id: "caldera", role: "attack", type: "caldera", reach: { url: "http://10.80.72.52:8888" } },
      { id: "wazuh", role: "defence", type: "wazuh", reach: { url: "https://10.80.72.52:55000" } },
    ],
  },
};
const KVM1: Target = {
  node: "kvm1", name: "kvm1", default: false, nodes: ["kvm1"], platform: "libvirt", error: null, sensors: ["Zeek-K"],
  lab: { components: [{ id: "Zeek-K", role: "sensor", type: "zeek", reach: { vmid: 300, transport_ip: "10.9.9.9" } }] },
};

function twinWithTap(id: string) {
  const doc = document(id);
  doc.files["tap/default.yaml"] = TAP.replace("corp-auth", id);
  doc.files["twin.yaml"] = twinYaml(id).replace("tfc:", "tap:\n      default: tap/default.yaml\n    tfc:");
  return doc;
}

beforeEach(() => {
  try {
    localStorage.clear();
  } catch {
    // jsdom always has it
  }
});

async function openTap() {
  await screen.findAllByTestId("canvas-host");
  fireEvent.click(screen.getAllByTestId("canvas-tap")[0]!);
  return screen.findByTestId("tap-panel");
}

describe("the tap inspector's Sensors", () => {
  it("offers only the default target's lab sensors; a stale id is struck through and remaps", async () => {
    const { api, state } = seeded({ targets: [DEV45, KVM1], documents: { "corp-auth": twinWithTap("corp-auth") } });
    renderAt(api, "/canvas/corp-auth");
    let tp = await openTap();
    const field = await within(tp).findByTestId("tap-sensors");
    await waitFor(() => expect(field.textContent).toContain("the lab of dev45"));
    // The options: dev45's sensors, nothing from kvm1, no free-text input.
    expect(within(field).getAllByRole("checkbox").map((c) => c.getAttribute("aria-label"))).toEqual(["Sensor XPress-SW-A", "Sensor Tcpdump-SW-A"]);
    expect(within(field).queryByRole("textbox")).toBeNull();
    expect((within(field).getByLabelText("Sensor XPress-SW-A") as HTMLInputElement).checked).toBe(true);
    // Old-Sensor is not in the lab: struck through, with a remap to what the binding does not already name.
    const stale = within(field).getByTestId("stale-sensor");
    expect(stale.dataset["sensor"]).toBe("Old-Sensor");
    expect(within(stale).getByText("Old-Sensor").tagName).toBe("S");
    const remap = within(stale).getByLabelText("Remap Old-Sensor") as HTMLSelectElement;
    expect([...remap.options].map((o) => o.value)).toEqual(["", "Tcpdump-SW-A"]);
    fireEvent.change(remap, { target: { value: "Tcpdump-SW-A" } });
    await settle();
    // One taps edit in the design (undeployed: nothing written until Save); the stale row is gone.
    expect(state.writes.some((w) => w.op === "put-twin-config")).toBe(false);
    tp = await screen.findByTestId("tap-panel");
    expect(within(tp).queryByTestId("stale-sensor")).toBeNull();
    expect((within(tp).getByLabelText("Sensor Tcpdump-SW-A") as HTMLInputElement).checked).toBe(true);
    expect(screen.getByTestId("dirty-marker")).toBeTruthy();
  });

  it("a deployed twin offers its deploy's target's lab", async () => {
    const deploy = { ...DEPLOY, target: "kvm1" };
    const { api, state } = seeded({ targets: [DEV45, KVM1], deploys: [deploy], documents: { "corp-live": twinWithTap("corp-live") } });
    renderAt(api, "/twins/corp-live/canvas");
    const tp = await openTap();
    const field = await within(tp).findByTestId("tap-sensors");
    await waitFor(() => expect(field.textContent).toContain("the lab of kvm1"));
    expect(within(field).getAllByRole("checkbox").map((c) => c.getAttribute("aria-label"))).toEqual(["Sensor Zeek-K"]);
    // On kvm1 both of the plan's sensors are stale.
    expect(within(field).getAllByTestId("stale-sensor").map((r) => r.dataset["sensor"])).toEqual(["XPress-SW-A", "Old-Sensor"]);
    fireEvent.click(within(field).getByLabelText("Sensor Zeek-K"));
    await settle();
    // Deployed: the edit lands in the working copy.
    const put = state.writes.filter((w) => w.op === "put-deploy-config").at(-1) as { body: { files: Record<string, string> } };
    expect(readTap(put.body.files["tap/default.yaml"]!).bindings[0]!.sensors).toEqual(["XPress-SW-A", "Old-Sensor", "Zeek-K"]);
  });
});

describe("the Lab overlay", () => {
  it("is off by default; on, it docks the lab by role with health dots and draws tap -> sensor lines", async () => {
    const { api } = seeded({
      targets: [DEV45, KVM1],
      documents: { "corp-auth": twinWithTap("corp-auth") },
      health: { dev45: { target: "dev45", components: [{ id: "XPress-SW-A", role: "sensor", status: "up" }, { id: "grafana", role: "telemetry", status: "down" }, { id: "caldera", role: "attack", status: "up" }, { id: "wazuh", role: "defence", status: "unknown" }] } },
    });
    renderAt(api, "/canvas/corp-auth");
    await screen.findAllByTestId("canvas-host");
    const toggle = await screen.findByTestId("rail-lab");
    expect(toggle.getAttribute("aria-label")).toBe("Lab overlay: off");
    expect(screen.queryByTestId("lab-overlay")).toBeNull();

    fireEvent.click(toggle);
    const overlay = await screen.findByTestId("lab-overlay");
    expect(screen.getByTestId("rail-lab").getAttribute("aria-label")).toBe("Lab overlay: on");
    expect(overlay.textContent).toContain("dev45");
    // Sensors on top, then Telemetry, Attack, Defence; the adapter.conf sensor sits among the sensors without a probe.
    const groups = within(overlay).getAllByTestId(/^lab-overlay-(sensor|telemetry|attack|defence)$/).map((g) => g.dataset["testid"] ?? g.getAttribute("data-testid"));
    expect(groups).toEqual(["lab-overlay-sensor", "lab-overlay-telemetry", "lab-overlay-attack", "lab-overlay-defence"]);
    expect(within(within(overlay).getByTestId("lab-overlay-sensor")).getAllByTestId("lab-overlay-chip").map((c) => c.dataset["component"])).toEqual(["XPress-SW-A", "Tcpdump-SW-A"]);
    await waitFor(() => expect(chip(overlay, "grafana").querySelector(".dot")!.getAttribute("data-status")).toBe("down"));
    expect(chip(overlay, "XPress-SW-A").querySelector(".dot")!.getAttribute("data-status")).toBe("up");
    // A dotted line from the tap chip to each sensor it delivers to that the lab column shows (Old-Sensor has none).
    await waitFor(() => expect(screen.getAllByTestId("lab-line").map((l) => l.getAttribute("data-sensor"))).toEqual(["XPress-SW-A"]));

    fireEvent.click(screen.getByTestId("rail-lab"));
    expect(screen.queryByTestId("lab-overlay")).toBeNull();
    expect(screen.queryAllByTestId("lab-line")).toEqual([]);
  });

  it("is never a topology node and never touches the working copy", async () => {
    const { api, state } = seeded({ targets: [DEV45], deploys: [DEPLOY], documents: { "corp-live": twinWithTap("corp-live") } });
    renderAt(api, "/twins/corp-live/canvas");
    await screen.findAllByTestId("canvas-host");
    await screen.findByTestId("clean-marker");
    const nodesBefore = window.document.querySelectorAll(".react-flow__node").length;
    const hostsBefore = screen.getAllByTestId("canvas-host").length;
    const writesBefore = state.writes.length;
    fireEvent.click(await screen.findByTestId("rail-lab"));
    const overlay = await screen.findByTestId("lab-overlay");
    await settle();
    expect(window.document.querySelectorAll(".react-flow__node").length).toBe(nodesBefore);
    expect(screen.getAllByTestId("canvas-host").length).toBe(hostsBefore);
    // The column is chrome outside the graph: no node carries a lab component.
    expect(overlay.closest(".react-flow__node")).toBeNull();
    expect([...window.document.querySelectorAll(".react-flow__node")].some((n) => n.textContent?.includes("grafana"))).toBe(false);
    // Nothing written: the page's own background validate (a read) may run, but no document, working-copy or layout write.
    expect(state.writes.slice(writesBefore).filter((w) => w.op !== "validate")).toEqual([]);
    expect(screen.getByTestId("clean-marker")).toBeTruthy();
  });

  it("the toggle is kept per viewer in this browser", async () => {
    const first = seeded({ targets: [DEV45], documents: { "corp-auth": twinWithTap("corp-auth") } });
    const { unmount } = renderAt(first.api, "/canvas/corp-auth");
    fireEvent.click(await screen.findByTestId("rail-lab"));
    await screen.findByTestId("lab-overlay");
    unmount();
    const second = seeded({ targets: [DEV45], documents: { "corp-auth": twinWithTap("corp-auth") } });
    renderAt(second.api, "/canvas/corp-auth");
    expect(await screen.findByTestId("lab-overlay")).toBeTruthy();
  });
});

function chip(overlay: HTMLElement, id: string): HTMLElement {
  return overlay.querySelector(`[data-component="${id}"]`) as HTMLElement;
}
