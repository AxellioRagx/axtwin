/**
 * The Targets page (docs/design/targets-and-lab.md §2.3, §4 "auto, UI"): one card per target with its platform,
 * the twins deployed on it and its lab grouped by role, with health dots and Open links; read-only (Edit disabled
 * with the hint); the health re-read every 15 s; no credential on the page. Against the fake API only.
 */
import { act, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { DeploySummary, Target } from "../src/api/types";
import { HEALTH_POLL_MS } from "../src/areas/targets/TargetsPage";
import { deliveryText, labGroups, overallHealth, sensorDeliveries } from "../src/model/targets";
import { fakeApi } from "./helpers/fakeApi";
import { renderAt } from "./helpers/render";

const DEV45: Target = {
  node: "dev45", name: "dev45", default: true, nodes: ["dev45"], platform: "proxmox", error: null,
  sensors: ["XPress-SW-A", "Tcpdump-SW-A"],
  stores: [{ label: "state.db", path: "/home/rocky/.axrange/state.db", primary: true }],
  lab: {
    components: [
      { id: "grafana", role: "telemetry", type: "grafana", reach: { url: "http://10.80.72.52:3000" }, health_http: "/api/health" },
      { id: "XPress-SW-A", role: "sensor", type: "packetxpress", reach: { vmid: 104, transport_ip: "10.255.251.21", capture_iface: "gre-cap" }, protected: true },
      { id: "wazuh", role: "defence", type: "wazuh", reach: { url: "https://10.80.72.52:55000" } },
      { id: "caldera", role: "attack", type: "caldera", reach: { url: "http://10.80.72.52:8888" } },
      { id: "Tcpdump-SW-A", role: "sensor", type: "tcpdump", reach: { vmid: 260, transport_ip: "10.255.251.12" } },
    ],
  },
};

const row = (over: Partial<DeploySummary>): DeploySummary => ({
  deploy_id: "x", scenario_id: "x", status: "completed", started_at: 1, updated_at: 1, tier: "te", target: "dev45", ...over,
});

afterEach(() => vi.useRealTimers());

describe("the Targets page", () => {
  it("is a top-level area after Twins", async () => {
    const { api } = fakeApi({ targets: [DEV45] });
    renderAt(api, "/targets");
    const nav = screen.getByRole("navigation", { name: "Areas" });
    const names = within(nav).getAllByRole("link").map((a) => a.textContent);
    expect(names.indexOf("Targets")).toBe(names.indexOf("Twins") + 1);
    expect(within(nav).getByRole("link", { name: "Targets" }).className).toContain("active");
  });

  it("renders the target, its deployed twins and its lab by role with health dots and Open links", async () => {
    const { api } = fakeApi({
      targets: [DEV45],
      deploys: [
        row({ deploy_id: "switch-test-aaaa", scenario_id: "switch-test", follow_on: { tap: { status: "completed" }, tfc: { status: "completed" } } }),
        row({ deploy_id: "old-bbbb", scenario_id: "old", status: "destroyed" }),
      ],
      taps: {
        "switch-test-aaaa": { deploy_id: "tap-1", status: "completed", bindings: [{ tap: "boundary:office|servers", sensors: ["XPress-SW-A"] }, { tap: "segment:office", sensors: ["XPress-SW-A"] }] },
      },
      health: {
        dev45: {
          target: "dev45",
          components: [
            { id: "grafana", role: "telemetry", status: "down", checked_at: "2026-11-09T18:08:35Z", detail: "GET http://10.80.72.52:3000/api/health: no answer" },
            { id: "XPress-SW-A", role: "sensor", status: "up", detail: "VM XPress-SW-A at 10.80.72.43: ssh answers" },
            { id: "wazuh", role: "defence", status: "up" },
            { id: "caldera", role: "attack", status: "up" },
            { id: "Tcpdump-SW-A", role: "sensor", status: "unknown" },
          ],
        },
      },
    });
    renderAt(api, "/targets");
    const card = await screen.findByTestId("target-card");
    expect(within(card).getByRole("heading", { name: "dev45" })).toBeTruthy();
    expect(within(card).getByTestId("platform-badge").textContent).toBe("proxmox");
    expect(card.textContent).toContain("state.db");

    // Deployed twins: the standing one, linked to its canvas, with its tiers; the destroyed one is not listed.
    const twins = await within(card).findByTestId("target-twins");
    expect(within(twins).getByRole("link", { name: "switch-test" }).getAttribute("href")).toBe("/canvas/switch-test");
    expect(twins.textContent).toContain("te ✓ · tap ✓ · tfc ✓");
    expect(twins.textContent).not.toContain("old");

    // The lab in the deck's order, whatever order the file declared.
    const groups = within(card).getAllByTestId(/^lab-/).filter((el) => el.className === "lab-group");
    expect(groups.map((g) => g.querySelector("h4")!.textContent)).toEqual(["Sensors", "Telemetry", "Attack", "Defence"]);
    const sensors = within(card).getByTestId("lab-sensor");
    expect(within(sensors).getAllByTestId("lab-chip").map((c) => c.getAttribute("data-component"))).toEqual(["XPress-SW-A", "Tcpdump-SW-A"]);

    // Health dots per component, from the health read; the card's dot is down when any component is.
    await waitFor(() => expect(chip(card, "grafana").querySelector("[data-testid=component-health]")!.getAttribute("data-status")).toBe("down"));
    expect(chip(card, "XPress-SW-A").querySelector(".dot")!.getAttribute("data-status")).toBe("up");
    expect(chip(card, "grafana").querySelector(".dot")!.getAttribute("title")).toContain("no answer");
    expect(within(card).getByTestId("target-health").getAttribute("data-status")).toBe("down");

    // Open links for url components only, in a new tab.
    const open = within(chip(card, "grafana")).getByRole("link", { name: "Open grafana" });
    expect(open.getAttribute("href")).toBe("http://10.80.72.52:3000");
    expect(open.getAttribute("target")).toBe("_blank");
    expect(within(chip(card, "XPress-SW-A")).queryByRole("link")).toBeNull();

    // Sensors say which live bindings deliver to them.
    await waitFor(() => expect(within(chip(card, "XPress-SW-A")).getByTestId("sensor-delivery").textContent).toBe("2 bindings · switch-test"));
    expect(within(chip(card, "Tcpdump-SW-A")).getByTestId("sensor-delivery").textContent).toBe("no live binding");
  });

  it("is read-only: Edit is disabled with the hint; no credential is shown", async () => {
    const { api } = fakeApi({ targets: [DEV45] });
    const { container } = renderAt(api, "/targets");
    const card = await screen.findByTestId("target-card");
    const edit = within(card).getByRole("button", { name: "Edit" }) as HTMLButtonElement;
    expect(edit.disabled).toBe(true);
    expect(edit.getAttribute("title")).toBe("edit targets.yaml on the host; UI editing lands with A4");
    expect(card.textContent).toContain("edit targets.yaml on the host; UI editing lands with A4");
    expect(container.textContent).not.toMatch(/token|password|secret/i);
  });

  it("re-reads the health every 15 s", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { api, state } = fakeApi({ targets: [DEV45] });
    renderAt(api, "/targets");
    await screen.findByTestId("target-card");
    await waitFor(() => expect(state.healthReads.dev45).toBe(1));
    await act(async () => {
      vi.advanceTimersByTime(HEALTH_POLL_MS);
    });
    await waitFor(() => expect(state.healthReads.dev45).toBe(2));
    expect(HEALTH_POLL_MS).toBe(15_000);
  });

  it("a target without a lab says so and reads no health", async () => {
    const { api, state } = fakeApi({ targets: [{ ...DEV45, lab: { components: [] } }] });
    renderAt(api, "/targets");
    const card = await screen.findByTestId("target-card");
    expect(card.textContent).toContain("No lab declared for this target");
    expect(state.healthReads.dev45 ?? 0).toBe(0);
  });
});

describe("the targets model", () => {
  it("groups by role in the deck's order and leaves empty roles out", () => {
    expect(labGroups(DEV45.lab!.components).map((g) => g.label)).toEqual(["Sensors", "Telemetry", "Attack", "Defence"]);
    expect(labGroups([DEV45.lab!.components[0]!]).map((g) => g.label)).toEqual(["Telemetry"]);
  });

  it("overall health: down beats unknown beats up", () => {
    const comps = DEV45.lab!.components;
    const all = (status: string) => comps.map((c) => ({ id: c.id, role: c.role, status }));
    expect(overallHealth(comps, all("up"))).toBe("up");
    expect(overallHealth(comps, null)).toBe("unknown");
    expect(overallHealth(comps, [...all("up").slice(1), { id: "grafana", role: "telemetry", status: "down" }])).toBe("down");
    expect(overallHealth(comps, all("up").slice(1))).toBe("unknown");
  });

  it("counts only live tap playouts' bindings", () => {
    const d = sensorDeliveries([
      { twin: "a", status: { status: "completed", bindings: [{ sensors: ["S1", "S2"] }, { sensors: ["S1"] }] } },
      { twin: "b", status: { status: "running", bindings: [{ sensors: ["S1"] }] } },
      { twin: "c", status: { status: "destroyed", bindings: [{ sensors: ["S1"] }] } },
    ]);
    expect(deliveryText(d.get("S1"))).toBe("3 bindings · a, b");
    expect(deliveryText(d.get("S2"))).toBe("1 binding · a");
    expect(deliveryText(d.get("S3"))).toBe("no live binding");
  });
});

function chip(card: HTMLElement, id: string): HTMLElement {
  return card.querySelector(`[data-component="${id}"]`) as HTMLElement;
}
