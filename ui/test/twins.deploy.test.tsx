/**
 * Deploying a twin from the console (2026-11-08): the Twins list's "Deploy" lands on the
 * twin page's Deploy panel -- plan (pure), then deploy pinned to that plan's id, behind a
 * confirmation. Against the fake API only; nothing here reaches a cluster.
 */
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ApiError } from "../src/api/client";
import type { TwinSummary } from "../src/api/types";
import { fakeApi } from "./helpers/fakeApi";
import { renderAt } from "./helpers/render";

const twin = (id: string, over: Partial<TwinSummary> = {}): TwinSummary => ({ id, as_of: "2026-10-06T19:00:00Z", pit_id: null, hosts: 262, segments: 45, services: 424, personas: 17, mean_confidence: 0.5, topology_segments: [], ...over });

describe("deploying a twin from the console", () => {
  it("the list's Deploy opens the panel; plan, confirm, deploy pinned to the plan; the deploy then stands", async () => {
    const { api, state } = fakeApi({ twins: { lab: twin("lab", { hosts: 12 }) } });
    renderAt(api, "/twins");
    const row = (await screen.findAllByTestId("twin-row"))[0]!;
    expect(within(row).getByRole("link", { name: "Deploy" }).getAttribute("href")).toBe("/twins/lab#deploy");

    renderAt(api, "/twins/lab#deploy");
    const panel = await screen.findByTestId("deploy-panel");
    await waitFor(() => expect(panel.textContent).toContain("on dev45"));
    // Nothing is offered to deploy before a plan; the plan writes nothing.
    expect(within(panel).queryByRole("button", { name: "Deploy this plan" })).toBeNull();
    fireEvent.click(within(panel).getByRole("button", { name: "Plan the deploy" }));
    const plan = await within(panel).findByTestId("deploy-plan");
    expect(plan.textContent).toContain("12 VMs");
    expect(plan.textContent).toContain("1 note from the deploy gate");
    expect(state.writes.map((w) => w.op)).toEqual(["plan"]);
    const planId = plan.querySelector(".mono")!.textContent!;

    // Two clicks: Deploy this plan, then the confirmation naming what it creates and where.
    fireEvent.click(within(panel).getByRole("button", { name: "Deploy this plan" }));
    expect(state.writes.map((w) => w.op)).toEqual(["plan"]);
    fireEvent.click(within(panel).getByRole("button", { name: "Keep" }));
    fireEvent.click(within(panel).getByRole("button", { name: "Deploy this plan" }));
    fireEvent.click(within(panel).getByRole("button", { name: "Confirm: create 12 VMs on dev45" }));
    const started = await within(panel).findByTestId("deploy-started");
    expect(started.textContent).toMatch(/Deploy dep-\d+ started/);
    // The whole twin: te, then the tap plan, then the traffic profile (2026-11-09).
    expect(state.writes.at(-1)).toEqual({ op: "deploy", id: "lab", body: { expect_plan_id: planId, follow_on: ["tap", "tfc"], target_node: "dev45" }, target: "dev45" });

    // The page re-reads: the deploy stands, its tiers queued behind te, and the panel offers no second one.
    await waitFor(() => expect(screen.getByTestId("deploy-state").textContent).toContain("te running"));
    expect(screen.getByTestId("tier-tap").textContent).toContain("tap queued");
    expect(screen.getByTestId("tier-tfc").textContent).toContain("tfc queued");
    expect(within(panel).getByTestId("deploy-blocked").textContent).toContain("A deploy stands on this twin");
    expect(within(panel).queryByRole("button", { name: "Plan the deploy" })).toBeNull();

    // The line follows the deploy to its end without a reload (it stayed at "te running"): te completes, tap is
    // skipped (no taps: "—"), tfc fails with its reason.
    const did = (state.writes.at(-1) as { id: string }).id && state.deploys.at(-1)!.deploy_id;
    state.deploys = state.deploys.map((d) => (d.deploy_id === did ? { ...d, status: "completed", follow_on: { tap: { status: "skipped", reason: "the twin has no taps" }, tfc: { status: "failed", reason: "persona web-tier: no such persona" } } } : d));
    await waitFor(() => expect(screen.getByTestId("tier-te").textContent).toBe("te completed"), { timeout: 5000 });
    expect(screen.getByTestId("tier-tap").textContent).toContain("tap —");
    expect(screen.getByTestId("tier-tap").getAttribute("title")).toBe("the twin has no taps");
    expect(screen.getByTestId("tier-tfc").textContent).toContain("tfc failed (persona web-tier: no such persona)");
  }, 10000);

  it("a refused deploy (the twin changed since the plan) shows the server's reason and keeps the plan", async () => {
    const { api, state } = fakeApi({ twins: { lab: twin("lab", { hosts: 2 }) } });
    state.refuse.deploy = new ApiError({ kind: "conflict", status: 409, message: "the deploy re-planned to lab-9; expected lab-plan100", url: "" });
    renderAt(api, "/twins/lab");
    const panel = await screen.findByTestId("deploy-panel");
    fireEvent.click(within(panel).getByRole("button", { name: "Plan the deploy" }));
    await within(panel).findByTestId("deploy-plan");
    fireEvent.click(within(panel).getByRole("button", { name: "Deploy this plan" }));
    fireEvent.click(within(panel).getByRole("button", { name: /^Confirm: create 2 VMs/ }));
    const alert = await within(panel).findByRole("alert");
    expect(alert.textContent).toBe("could not deploy: the deploy re-planned to lab-9; expected lab-plan100");
    expect(within(panel).getByTestId("deploy-plan")).toBeTruthy();
  });

  it("a refused plan says so; a multi-node target offers its nodes and a node change re-plans", async () => {
    const { api, state } = fakeApi({
      twins: { lab: twin("lab", { hosts: 2 }) },
      targets: [{ node: "pve1", name: "lab-cluster", default: true, nodes: ["pve1", "pve2"], platform: "proxmox", error: null }],
    });
    state.refuse.plan = new ApiError({ kind: "unprocessable", status: 422, message: "twin-config 'lab' fails validation (1 error(s)): CV-012 ipv6", url: "" });
    renderAt(api, "/twins/lab");
    const panel = await screen.findByTestId("deploy-panel");
    fireEvent.click(within(panel).getByRole("button", { name: "Plan the deploy" }));
    expect((await within(panel).findByRole("alert")).textContent).toContain("could not plan: twin-config 'lab' fails validation");
    delete state.refuse.plan;
    const select = (await within(panel).findByLabelText("Target node")) as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "pve2" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Plan the deploy" }));
    await within(panel).findByTestId("deploy-plan");
    expect(state.writes.at(-1)).toEqual({ op: "plan", id: "lab", body: { target_node: "pve2" }, target: "lab-cluster" });
    // A plan answers for one node: another node re-runs it (and its preflight).
    fireEvent.change(select, { target: { value: "pve1" } });
    await waitFor(() => expect(state.writes.at(-1)).toEqual({ op: "plan", id: "lab", body: { target_node: "pve1" }, target: "lab-cluster" }));
  });

  it("a read-only caller is told to sign in; nothing is offered", async () => {
    const { api } = fakeApi({ twins: { lab: twin("lab") }, whoami: { user: "anonymous", can_write: false, mode: "token", signin_url: "" } });
    renderAt(api, "/twins/lab");
    const panel = await screen.findByTestId("deploy-panel");
    await waitFor(() => expect(within(panel).getByTestId("deploy-blocked").textContent).toContain("sign in to deploy"));
    expect(within(panel).queryByRole("button")).toBeNull();
  });
});
