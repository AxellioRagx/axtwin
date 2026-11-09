/**
 * The deploy dialog against a target (docs/design/targets-and-lab.md §2.3, §4 "auto, UI"): a Target select with the
 * platform badge, "images bound for <platform>: N / N", a preflight row per sensor not in the target's lab with a
 * Remap select that writes the twin's tap plan, a row per unbound image with its path; Deploy disabled until the
 * preflight is clean; the deployed canvas bar's target chip. Against the fake API only.
 */
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { Target } from "../src/api/types";
import { missingSensor, remapFiles, sensorRef } from "../src/model/preflight";
import { DEPLOY, document, seeded, twinYaml } from "./helpers/canvasFixtures";
import { renderAt } from "./helpers/render";

const TAP = "tap-plan:\n  target: corp-auth\n  bindings:\n    - selector: { scope: segment, target: net-lan }\n      sensors: [XPress-SW-A]\n      mode: exclusive\n";

const DEV45: Target = {
  node: "dev45", name: "dev45", default: true, nodes: ["dev45"], platform: "proxmox", error: null, sensors: ["XPress-SW-A"],
  lab: { components: [{ id: "XPress-SW-A", role: "sensor", type: "packetxpress", reach: { vmid: 104, transport_ip: "10.255.251.21" } }] },
};
const KVM1: Target = {
  node: "kvm1", name: "kvm1", default: false, nodes: ["kvm1"], platform: "libvirt", error: null, sensors: ["Tcpdump-SW-A"],
  lab: { components: [{ id: "Tcpdump-SW-A", role: "sensor", type: "tcpdump", reach: { vmid: 260, transport_ip: "10.255.251.12" } }] },
};

function withTap() {
  const doc = document("corp-auth");
  doc.files["tap/default.yaml"] = TAP;
  doc.files["twin.yaml"] = twinYaml("corp-auth").replace("tfc:", "tap:\n      default: tap/default.yaml\n    tfc:");
  return doc;
}

describe("the deploy dialog on the twin page", () => {
  it("offers the targets; switching to one lacking a sensor shows Remap and blocks until resolved", async () => {
    const { api, state } = seeded({ targets: [DEV45, KVM1], documents: { "corp-auth": withTap() } });
    renderAt(api, "/twins/corp-auth#deploy");
    const panel = await screen.findByTestId("deploy-panel");
    const select = (await within(panel).findByLabelText("Target")) as HTMLSelectElement;
    expect([...select.options].map((o) => o.value)).toEqual(["dev45", "kvm1"]);
    expect(select.value).toBe("dev45");
    expect(within(panel).getByTestId("deploy-platform").textContent).toBe("proxmox");

    // On dev45 the preflight is clean: images bound, Deploy offered, the plan asked for that target.
    fireEvent.click(within(panel).getByRole("button", { name: "Plan the deploy" }));
    await within(panel).findByTestId("preflight-clean");
    expect(within(panel).getByTestId("images-bound").textContent).toBe("images bound for proxmox: 1 / 1");
    expect(state.writes.at(-1)).toMatchObject({ op: "plan", id: "corp-auth", target: "dev45" });
    expect((within(panel).getByRole("button", { name: "Deploy this plan" }) as HTMLButtonElement).disabled).toBe(false);

    // Switching the target re-runs the plan and its preflight: kvm1's lab has no XPress-SW-A.
    fireEvent.change(select, { target: { value: "kvm1" } });
    expect(within(panel).getByTestId("deploy-platform").textContent).toBe("libvirt");
    const row = await within(panel).findByTestId("preflight-row");
    expect(state.writes.at(-1)).toMatchObject({ op: "plan", target: "kvm1" });
    expect(row.textContent).toContain("sensor XPress-SW-A not in lab of target kvm1");
    expect(row.textContent).toContain("tap/default.yaml: tap-plan.bindings[0].sensors[0]");
    expect((within(panel).getByRole("button", { name: "Deploy this plan" }) as HTMLButtonElement).disabled).toBe(true);

    // Remap XPress-SW-A -> Tcpdump-SW-A: the twin's tap plan is written (the Save path), then re-planned clean.
    const remap = within(row).getByLabelText("Remap XPress-SW-A") as HTMLSelectElement;
    expect([...remap.options].map((o) => o.value)).toEqual(["", "Tcpdump-SW-A"]);
    fireEvent.change(remap, { target: { value: "Tcpdump-SW-A" } });
    await within(panel).findByTestId("preflight-clean");
    const put = state.writes.find((w) => w.op === "put-twin-config") as { body: { files: Record<string, string> } };
    expect(put.body.files["tap/default.yaml"]).toContain("sensors: [Tcpdump-SW-A]");
    expect(put.body.files["tap/default.yaml"]).toContain("mode: exclusive");
    expect(state.writes.at(-1)).toMatchObject({ op: "plan", target: "kvm1" });

    // Clean: Deploy, confirm; the deploy is asked for kvm1.
    fireEvent.click(within(panel).getByRole("button", { name: "Deploy this plan" }));
    fireEvent.click(within(panel).getByRole("button", { name: /^Confirm: create 2 VMs on kvm1$/ }));
    await within(panel).findByTestId("deploy-started");
    expect(state.writes.at(-1)).toMatchObject({ op: "deploy", id: "corp-auth", target: "kvm1", body: { target_node: "kvm1" } });
  });

  it("an image with no binding for the platform blocks, with the host's path and no remap", async () => {
    const { api } = seeded({
      targets: [DEV45, KVM1],
      documents: { "corp-auth": withTap() },
      preflightImages: { dev45: { bound: 2, total: 3, unbound: [{ image: "win11-finance@v4", location: "te.yaml: twin_config.nodes[1].image" }] } },
    });
    renderAt(api, "/twins/corp-auth");
    const panel = await screen.findByTestId("deploy-panel");
    await within(panel).findByLabelText("Target");
    fireEvent.click(within(panel).getByRole("button", { name: "Plan the deploy" }));
    const row = await within(panel).findByTestId("preflight-row");
    expect(row.getAttribute("data-rule")).toBe("TARGET-IMAGE");
    expect(row.textContent).toContain("image win11-finance@v4 has no binding for platform proxmox");
    expect(row.textContent).toContain("te.yaml: twin_config.nodes[1].image");
    expect(within(row).queryByRole("combobox")).toBeNull();
    expect(within(panel).getByTestId("images-bound").textContent).toBe("images bound for proxmox: 2 / 3");
    expect((within(panel).getByRole("button", { name: "Deploy this plan" }) as HTMLButtonElement).disabled).toBe(true);
  });
});

describe("the canvas bar's Deploy and target chip", () => {
  it("an undeployed design opens the dialog; Remap edits the design, which must be saved before planning again", async () => {
    const { api, state } = seeded({ targets: [DEV45, KVM1], documents: { "corp-auth": withTap() } });
    renderAt(api, "/canvas/corp-auth");
    fireEvent.click(await screen.findByTestId("deploy-open"));
    const dialog = await screen.findByTestId("deploy-dialog");
    const select = (await within(dialog).findByLabelText("Target")) as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "kvm1" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Plan the deploy" }));
    const row = await within(dialog).findByTestId("preflight-row");
    fireEvent.change(within(row).getByLabelText("Remap XPress-SW-A"), { target: { value: "Tcpdump-SW-A" } });

    // The remap is an edit of the design (nothing written yet); the deploy runs the saved twin, so Save is asked for.
    const hold = await within(dialog).findByTestId("deploy-hold");
    expect(hold.textContent).toContain("unsaved changes");
    expect(state.writes.some((w) => w.op === "put-twin-config")).toBe(false);
    expect(screen.getByTestId("dirty-marker")).toBeTruthy();
    expect((within(dialog).getByRole("button", { name: "Plan again" }) as HTMLButtonElement).disabled).toBe(true);

    fireEvent.click(within(hold).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(within(dialog).queryByTestId("deploy-hold")).toBeNull());
    const put = state.writes.find((w) => w.op === "put-twin-config") as { body: { files: Record<string, string> } };
    expect(put.body.files["tap/default.yaml"]).toContain("sensors: [Tcpdump-SW-A]");
    fireEvent.click(within(dialog).getByRole("button", { name: "Plan again" }));
    await within(dialog).findByTestId("preflight-clean");
  });

  it("a deployed canvas carries the target chip 'dev45 · proxmox' and no Deploy", async () => {
    const { api } = seeded({ targets: [DEV45, KVM1], deploys: [DEPLOY] });
    renderAt(api, "/canvas/corp-live");
    const chip = await screen.findByTestId("target-chip");
    await waitFor(() => expect(chip.textContent).toBe("dev45 · proxmox"));
    expect(screen.queryByTestId("deploy-open")).toBeNull();
  });
});

describe("the preflight model", () => {
  it("reads a sensor finding's location and rewrites only that binding's sensor", () => {
    expect(sensorRef("tap/default.yaml: tap-plan.bindings[2].sensors[1]")).toEqual({ file: "tap/default.yaml", binding: 2, sensor: 1 });
    expect(sensorRef("te.yaml: twin_config.nodes[1].image")).toBeNull();
    expect(missingSensor({ rule_id: "TARGET-SENSOR", severity: "error", message: "sensor XPress-SW-A not in lab of target kvm1" })).toBe("XPress-SW-A");
    const two = TAP.replace("sensors: [XPress-SW-A]", "sensors: [XPress-SW-A, Tcpdump-SW-A]");
    const out = remapFiles({ "tap/default.yaml": two }, "tap/default.yaml: tap-plan.bindings[0].sensors[0]", "Tcpdump-SW-A");
    // A remap onto a sensor the binding already names drops the duplicate.
    expect(out["tap/default.yaml"]).toContain("sensors: [Tcpdump-SW-A]\n");
    expect(() => remapFiles({}, "tap/default.yaml: tap-plan.bindings[0].sensors[0]", "x")).toThrow("no tap/default.yaml");
  });
});
