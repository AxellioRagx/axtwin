/**
 * The Catalog's guarded deploy (rule 6) and the pure deploy flow, plus the
 * Telemetry link-outs and the Design area's frame (glossary, "would deploy").
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { RuntimeApi } from "../src/api/runtime";
import type { TwinConfigDetail } from "../src/api/runtimeTypes";
import { usableBaseUrl } from "../src/areas/telemetry/TelemetryPage";
import { GLOSSARY } from "../src/areas/design/DesignBar";
import {
  changedParams,
  declaredVmCount,
  deployFlowReducer as r,
  flowKey,
  initialDeployFlow,
  paramDefaults,
  type DeployPreview,
} from "../src/model/deployFlow";
import { TestApp } from "../src/routes";
import { apiError, fakeRuntime, pending, unusedDesignApi } from "./helpers/runtime";

const TARGETS = {
  targets: [
    { name: "dev45", default: true, node: "dev45", api_url: "https://10.80.76.108:8006" },
    { name: "pxtwin1", default: false, node: "pxtwin1", api_url: "https://10.80.1.2:8006" },
  ],
};

const DETAIL: TwinConfigDetail = {
  id: "sme-flat",
  description: "A small flat office.",
  cluster: "pxtwin1",
  params: [{ name: "WS_COUNT", default: 4, description: "workstations on the LAN", declared: true }],
  segments: [{ name: "lan", cidr: "10.9.0.0/24", zone_type: null, when: null }],
  nodes: [
    { name: "ws", kind: "host", image: "debian-12", count: 4, role: "corp-workstation", when: null },
    { name: "gw", kind: "router", image: "vyos", count: 1, role: null, when: null },
  ],
  identity_domain: null,
  identity_user_count: 0,
  data_count: 0,
};

function open(url: string, overrides: Partial<RuntimeApi> = {}, calls: string[] = []): void {
  const runtime = fakeRuntime(
    {
      getTargets: () => Promise.resolve(TARGETS),
      listTwinConfigs: () => Promise.resolve({ twin_configs: [{ id: "sme-flat", description: "A small flat office.", error: null, cluster: "pxtwin1" }] }),
      listScenarios: () => Promise.resolve({ scenarios: [{ id: "demo", path: "/s/demo.yaml" }] }),
      getTwinConfig: () => Promise.resolve(DETAIL),
      plan: (source) =>
        Promise.resolve({
          plan_id: `sme-flat-${source.params?.["WS_COUNT"] ?? "4"}`,
          scenario_id: "sme-flat",
          scenario_version: 1,
          ir_hash: "h",
          ops: 30,
          barriers: 2,
          edges: 29,
          tree: "",
          config_warnings: [],
          vm_count: 1 + Number(source.params?.["WS_COUNT"] ?? "4"),
          params: { WS_COUNT: Number(source.params?.["WS_COUNT"] ?? "4") },
        }),
      ...overrides,
    },
    calls,
  );
  render(<TestApp client={unusedDesignApi()} runtime={runtime} url={url} />);
}

describe("the deploy flow, pure", () => {
  const preview: DeployPreview = { vmCount: 5, vmCountNote: null, lines: [], warnings: [] };
  function previewed() {
    let s = initialDeployFlow({ kind: "twins", id: "sme-flat" }, "dev45");
    s = r(s, { type: "preview-start" });
    return r(s, { type: "preview-ok", seq: s.seq, key: flowKey(s.source, s.target), preview });
  }

  it("changing the cluster discards the preview", () => {
    const s = r(previewed(), { type: "set-target", target: "pxtwin1" });
    expect(s.phase.kind).toBe("idle");
    expect(r(s, { type: "ask-confirm" })).toBe(s);
  });

  it("an answer for another cluster is dropped", () => {
    let s = initialDeployFlow({ kind: "twins", id: "sme-flat" }, "dev45");
    s = r(s, { type: "preview-start" });
    const stale = r(s, { type: "preview-ok", seq: s.seq, key: flowKey(s.source, "pxtwin1"), preview });
    expect(stale.phase.kind).toBe("previewing");
  });

  it("confirm is one-shot and nothing moves a deploy in flight", () => {
    let s = r(previewed(), { type: "ask-confirm" });
    s = r(s, { type: "send" });
    expect(s.phase.kind).toBe("sending");
    expect(r(s, { type: "send" })).toBe(s);
    expect(r(s, { type: "set-target", target: "pxtwin1" })).toBe(s);
  });

  it("a parameter change discards the preview; an answer for the old values is dropped", () => {
    let s = previewed();
    s = r(s, { type: "set-params", params: { WS_COUNT: "9" } });
    expect(s.phase.kind).toBe("idle");
    s = r(s, { type: "preview-start" });
    const stale = r(s, { type: "preview-ok", seq: s.seq, key: flowKey(s.source, s.target, {}), preview });
    expect(stale.phase.kind).toBe("previewing");
    expect(r(s, { type: "preview-ok", seq: s.seq, key: flowKey(s.source, s.target, { WS_COUNT: "9" }), preview }).phase.kind).toBe("previewed");
  });

  it("only CHANGED parameters are sent; defaults become strings", () => {
    const defaults = paramDefaults([{ name: "A", default: 4 }, { name: "B", default: null }]);
    expect(defaults).toEqual({ A: "4", B: "" });
    expect(changedParams({ A: "4", B: "" }, defaults)).toEqual({});
    expect(changedParams({ A: "7", B: "" }, defaults)).toEqual({ A: "7" });
  });

  it("a VM count is never guessed", () => {
    expect(declaredVmCount([{ count: 4, when: null }, { count: "1", when: null }])).toEqual({ count: 5, note: null });
    expect(declaredVmCount([{ count: "{{ n }}", when: null }]).count).toBeNull();
    expect(declaredVmCount([{ count: 2, when: "with_dmz" }]).count).toBeNull();
  });
});

describe("the Catalog", () => {
  it("lists twins with their intended cluster, and scenarios", async () => {
    open("/catalog");
    const twin = await screen.findByTestId("catalog-twin");
    expect(twin.textContent).toMatch(/sme-flat.*for pxtwin1/);
    expect(await screen.findByRole("link", { name: "demo" })).toBeTruthy();
    expect(screen.getByText("Pick a twin or a scenario on the left.")).toBeTruthy();
  });

  it("validate and plan are ENABLED for a twin, and send its config and changed parameters", async () => {
    const sent: unknown[] = [];
    open("/catalog/twins/sme-flat", {
      validate: (source) => {
        sent.push(source);
        return Promise.resolve({ disposition: "pass", exit_code: 0, passes_run: [1], errors: 0, warnings: 0, findings: [] });
      },
    });
    const button = (await screen.findByTestId("validate")) as HTMLButtonElement;
    expect(button.disabled).toBe(false);
    fireEvent.change(await screen.findByTestId("param-WS_COUNT"), { target: { value: "9" } });
    fireEvent.click(button);
    expect(await screen.findByText(/Validation:/)).toBeTruthy();
    expect(sent).toEqual([{ config: "sme-flat", params: { WS_COUNT: "9" } }]);
  });

  it("shows the twin's parameters with their defaults; a change invalidates the preview; the plan's VM count and resolved values are shown; deploy sends the same params pinned to the plan", async () => {
    const plans: unknown[] = [];
    const deploys: unknown[] = [];
    open("/catalog/twins/sme-flat", {
      createDeploy: (body) => {
        deploys.push(body);
        return pending();
      },
    });
    const input = (await screen.findByTestId("param-WS_COUNT")) as HTMLInputElement;
    expect(input.value).toBe("4");
    expect(screen.getByTestId("twin-params").textContent).toMatch(/workstations on the LAN/);
    const select = screen.getByTestId("deploy-target") as HTMLSelectElement;
    await waitFor(() => expect(select.value).toBe("pxtwin1"));
    fireEvent.click(screen.getByTestId("deploy-preview"));
    expect((await screen.findByTestId("deploy-plan")).textContent).toMatch(/5 VMs/);
    fireEvent.change(input, { target: { value: "10" } });
    expect(screen.queryByTestId("deploy-plan")).toBeNull(); // invalidated
    fireEvent.click(screen.getByTestId("deploy-preview"));
    const plan = await screen.findByTestId("deploy-plan");
    expect(plan.textContent).toMatch(/11 VMs/);
    expect(screen.getByTestId("resolved-params").textContent).toBe("Parameters as planned: WS_COUNT = 10");
    fireEvent.click(screen.getByTestId("deploy-open-confirm"));
    expect(screen.getByTestId("deploy-confirm-box").textContent).toMatch(/11 real VMs on cluster pxtwin1/);
    fireEvent.click(screen.getByTestId("deploy-confirm"));
    await waitFor(() => expect(deploys).toHaveLength(1));
    expect(deploys[0]).toEqual({ config: "sme-flat", params: { WS_COUNT: "10" }, expect_plan_id: "sme-flat-10" });
    void plans;
  });

  it("pre-selects the twin's own cluster, warns when deploying elsewhere, and confirms by name and VM count", async () => {
    const calls: string[] = [];
    let sentTarget: string | null = "";
    open(
      "/catalog/twins/sme-flat",
      {
        createDeploy: (_body, target) => {
          sentTarget = target;
          return pending();
        },
      },
      calls,
    );
    const select = (await screen.findByTestId("deploy-target")) as HTMLSelectElement;
    await waitFor(() => expect(select.value).toBe("pxtwin1"));
    expect(screen.queryByTestId("cluster-mismatch")).toBeNull();
    fireEvent.change(select, { target: { value: "dev45" } });
    expect(screen.getByTestId("cluster-mismatch").textContent).toMatch(/meant for pxtwin1.*deploy it to dev45/);

    fireEvent.click(screen.getByTestId("deploy-preview"));
    const plan = await screen.findByTestId("deploy-plan");
    expect(plan.textContent).toMatch(/5 VMs/);
    expect(plan.textContent).toMatch(/plan sme-flat-4/);
    fireEvent.click(screen.getByTestId("deploy-open-confirm"));
    const box = screen.getByTestId("deploy-confirm-box");
    expect(box.textContent).toMatch(/This creates 5 real VMs on cluster dev45 \(https:\/\/10\.80\.76\.108:8006\)/);
    const go = within(box).getByTestId("deploy-confirm");
    expect(go.textContent).toBe("Deploy sme-flat to dev45");
    fireEvent.click(go);
    fireEvent.click(go);
    await waitFor(() => expect(calls.filter((c) => c === "createDeploy")).toHaveLength(1));
    expect(sentTarget).toBe("dev45");
  });

  it("changing the cluster after a preview takes the Deploy button away", async () => {
    open("/catalog/twins/sme-flat");
    const select = (await screen.findByTestId("deploy-target")) as HTMLSelectElement;
    await waitFor(() => expect(select.value).toBe("pxtwin1"));
    fireEvent.click(screen.getByTestId("deploy-preview"));
    await screen.findByTestId("deploy-open-confirm");
    fireEvent.change(select, { target: { value: "dev45" } });
    expect(screen.queryByTestId("deploy-open-confirm")).toBeNull();
  });
});

describe("the Catalog pins a scenario deploy to the plan it previewed (GAP-098)", () => {
  const PLAN = { plan_id: "demo-abc123", scenario_id: "demo", scenario_version: 1, ir_hash: "h", ops: 12, barriers: 1, edges: 11, tree: "", config_warnings: [] };
  const TOPO = { scenario_id: "demo", deploy_id: null, nodes: [{ id: "a", type: "host", image: null, cidr: null, status: null }, { id: "n", type: "network", image: null, cidr: "10.0.0.0/24", status: null }], edges: [] };

  it("sends expect_plan_id, and a plan-changed refusal says nothing was deployed", async () => {
    const bodies: unknown[] = [];
    open("/catalog/scenarios/demo", {
      plan: () => Promise.resolve(PLAN),
      getScenarioTopology: () => Promise.resolve(TOPO),
      createDeploy: (body) => {
        bodies.push(body);
        return Promise.reject(apiError("conflict", 409, "plan changed", { code: "plan-changed", plan_id: "demo-def456" }));
      },
    });
    const select = (await screen.findByTestId("deploy-target")) as HTMLSelectElement;
    await waitFor(() => expect(select.value).toBe("dev45"));
    fireEvent.click(screen.getByTestId("deploy-preview"));
    expect((await screen.findByTestId("deploy-plan")).textContent).toMatch(/1 VM/);
    fireEvent.click(screen.getByTestId("deploy-open-confirm"));
    fireEvent.click(screen.getByTestId("deploy-confirm"));
    expect((await screen.findByRole("alert")).textContent).toMatch(/plan changed since you previewed it \(it is now demo-def456\)\. Nothing was deployed/);
    expect(bodies).toEqual([{ scenario: "demo", expect_plan_id: "demo-abc123" }]);
  });
});

describe("Telemetry", () => {
  it("never turns the server's placeholder sentence into a link", () => {
    expect(usableBaseUrl("(not configured — set O2_SERVICES_VMID)")).toBeNull();
    expect(usableBaseUrl("(services vmid=5 — IP unresolved)")).toBeNull();
    expect(usableBaseUrl("http://10.1.2.3:3000/")).toBe("http://10.1.2.3:3000");
  });

  it("links the three dashboards off the served Grafana address, each with what it shows", async () => {
    open("/telemetry", { getO2Info: () => Promise.resolve({ endpoints: { grafana: "http://10.1.2.3:3000" } }) });
    await waitFor(() => expect(screen.getByTestId("dash-axr-capture-health").getAttribute("href")).toBe("http://10.1.2.3:3000/d/axr-capture-health"));
    expect(screen.getByTestId("dash-axr-suricata-alerts").getAttribute("href")).toBe("http://10.1.2.3:3000/d/axr-suricata-alerts");
    expect(screen.getByText(/Whether each sensor is capturing right now/)).toBeTruthy();
  });

  it("with no served address, says so and waits for one", async () => {
    open("/telemetry", { getO2Info: () => Promise.resolve({ endpoints: { grafana: "(not configured — set O2_SERVICES_VMID)" } }) });
    await waitFor(() => expect(screen.getByTestId("grafana-source").textContent).toMatch(/publishes no Grafana address/));
    // One sentence says what to do, and the field is the obvious next step.
    expect(screen.getByTestId("grafana-needed").textContent).toMatch(/^To open the dashboards, type your Grafana address below/);
    expect(document.activeElement).toBe(screen.getByTestId("grafana-url"));
    expect(screen.getByTestId("dash-axr-capture-health").tagName).toBe("SPAN");
    fireEvent.change(screen.getByTestId("grafana-url"), { target: { value: "http://g:3000" } });
    expect(screen.getByTestId("dash-axr-capture-health").getAttribute("href")).toBe("http://g:3000/d/axr-capture-health");
    expect(screen.queryByTestId("grafana-needed")).toBeNull();
  });
});

describe("the Design area frame", () => {
  it("carries a glossary of every word Design uses that the run-time screens do not", async () => {
    render(<TestApp client={unusedDesignApi()} runtime={fakeRuntime({ getTargets: () => pending() })} url="/design" />);
    fireEvent.click(await screen.findByTestId("glossary-toggle"));
    const g = screen.getByTestId("glossary");
    for (const term of ["imprint", "cast", "assertion", "ghost", "occupancy", "evidence classes", "conduit", "would deploy"]) {
      expect(GLOSSARY.some((e) => e.term === term), term).toBe(true);
      expect(within(g).getByText(term)).toBeTruthy();
    }
  });
});
