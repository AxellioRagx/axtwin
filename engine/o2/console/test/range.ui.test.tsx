/**
 * The Range (home) and the app frame: which twin is on which cluster with
 * zero clicks (ruling §7 a), recorded vs live labelling (rule 3), unknown is
 * never zero (rule 4), and every empty view says why (rule 1). Plus the old
 * `/imprints/...` redirect through the real router.
 */
import { render, screen, waitFor, within } from "@testing-library/react";
import { useLocation } from "react-router";
import { describe, expect, it } from "vitest";

import type { RangeDoc, RangeTarget } from "../src/api/runtimeTypes";
import { ConsoleRoutes, TestApp } from "../src/routes";
import { apiError, fakeRuntime, pending, unusedDesignApi } from "./helpers/runtime";

function target(overrides: Partial<RangeTarget> = {}): RangeTarget {
  // Ages are relative to when the test builds its data, not to module load.
  const NOW = Date.now() / 1000;
  return {
    name: "dev45",
    default: true,
    node: "dev45",
    api_url: "https://10.80.76.108:8006",
    reachability: { state: "reachable", checked_at: NOW - 30, reason: null, detail: "pve 9.2" },
    store: { ok: true, reason: null },
    twins: [
      {
        deploy_id: "corp-twin-07cfcdb2",
        twin_id: "corp-twin",
        status: "completed",
        plan_id: "corp-twin-7de6524d",
        started_at: NOW - 2 * 86400,
        updated_at: NOW - 2 * 86400,
        completed_at: NOW - 2 * 86400,
        cluster: "dev45",
        host_count: 106,
        persona_instance_count: 84,
        siblings: {
          tap: [{ deploy_id: "tap-corp-twin-07cfcdb2-8176c5ad", scenario_id: "tap-x", scenario_version: null, status: "completed", started_at: NOW - 3600, updated_at: 0, completed_at: 0, tier: "tap" }],
          tfc: [{ deploy_id: "tfc-corp-twin-07cfcdb2-785b1a06", scenario_id: "tfc-x", scenario_version: null, status: "completed", started_at: NOW - 3600, updated_at: 0, completed_at: 0, tier: "tfc" }],
          exr: [],
        },
        warnings: [],
      },
    ],
    ...overrides,
  };
}

function range(targets: RangeTarget[]): RangeDoc {
  return { generated_at: Date.now() / 1000, targets };
}

function open(url: string, getRange: () => Promise<RangeDoc>): void {
  render(
    <TestApp
      client={unusedDesignApi()}
      runtime={fakeRuntime({ getRange, getTargets: () => Promise.resolve({ targets: [{ name: "dev45", default: true, node: "dev45", api_url: "https://10.80.76.108:8006" }] }) })}
      url={url}
    />,
  );
}

describe("the Range answers 'which twin is on which cluster' with zero clicks", () => {
  it("groups twins by cluster and shows each twin's facts on the landing page", async () => {
    open("/", () => Promise.resolve(range([target()])));
    const group = await screen.findByTestId("cluster-group");
    expect(group.getAttribute("data-cluster")).toBe("dev45");
    const card = within(group).getByTestId("twin-card");
    expect(card.textContent).toContain("corp-twin");
    expect(card.textContent).toContain("corp-twin-07cfcdb2");
    expect(card.textContent).toContain("106 hosts");
    expect(card.textContent).toContain("84 personas");
    expect(card.textContent).toMatch(/Taps\s*up/);
    expect(card.textContent).toMatch(/Traffic\s*up/);
    expect(card.textContent).toMatch(/Exercise\s*none/);
    expect(card.textContent).toMatch(/deployed 2 days ago/);
    // One click to the twin.
    const link = within(card).getByRole("link");
    expect(link.getAttribute("href")).toBe("/twin/corp-twin-07cfcdb2");
  });

  it("labels the card RECORDED and the cluster's reachability LIVE with its age", async () => {
    open("/range", () => Promise.resolve(range([target()])));
    const card = await screen.findByTestId("twin-card");
    expect(card.textContent).toMatch(/recorded/i);
    const reach = screen.getByTestId("reachability");
    // 30 s old when built; a loaded suite may take a few seconds to get here.
    expect(reach.textContent).toMatch(/reachable · live, checked 3\d s ago/);
  });

  it("an unknown count renders as unknown, never as 0", async () => {
    const t = target();
    t.twins[0]!.host_count = null;
    t.twins[0]!.persona_instance_count = null;
    t.twins[0]!.siblings.tfc = [];
    open("/", () => Promise.resolve(range([t])));
    const card = await screen.findByTestId("twin-card");
    expect(card.textContent).toContain("hosts: unknown");
    expect(card.textContent).toContain("no traffic deploy");
    expect(card.textContent).not.toMatch(/\b0 hosts\b/);
  });

  it("a protected twin carries a Protected badge on its card", async () => {
    const t = target();
    t.twins[0]!.protected = true;
    open("/", () => Promise.resolve(range([t])));
    const card = await screen.findByTestId("twin-card");
    expect(within(card).getByTestId("protected-badge").textContent).toBe("Protected");
  });

  it("an unprotected twin has none", async () => {
    open("/", () => Promise.resolve(range([target()])));
    const card = await screen.findByTestId("twin-card");
    expect(within(card).queryByTestId("protected-badge")).toBeNull();
  });

  it("a non-default cluster's twin link carries its target", async () => {
    open("/", () => Promise.resolve(range([target(), target({ name: "pxtwin1", default: false })])));
    const groups = await screen.findAllByTestId("cluster-group");
    const px = groups.find((g) => g.getAttribute("data-cluster") === "pxtwin1")!;
    expect(within(px).getByRole("link", { name: /Open twin/ }).getAttribute("href")).toBe("/twin/corp-twin-07cfcdb2?target=pxtwin1");
  });
});

describe("fix round 2 -- stores and unrecorded VMs", () => {
  const stores = [
    { label: "state.db", path: "/a/state.db", primary: true, ok: true, reason: null },
    { label: "axr-svcengines.state.db", path: "/b/axr-svcengines.state.db", primary: false, ok: true, reason: null },
  ];

  it("labels each twin's store only when the cluster reads more than one", async () => {
    const t = target({ stores });
    t.twins[0]!.store = "axr-svcengines.state.db";
    open("/", () => Promise.resolve(range([t])));
    expect((await screen.findByTestId("twin-store")).textContent).toBe("store axr-svcengines.state.db");
  });

  it("no store label with a single store", async () => {
    const t = target({ stores: [stores[0]!] });
    t.twins[0]!.store = "state.db";
    open("/", () => Promise.resolve(range([t])));
    await screen.findByTestId("twin-card");
    expect(screen.queryByTestId("twin-store")).toBeNull();
  });

  it("lists unrecorded VMs collapsed, live with age, read-only", async () => {
    const t = target({
      unrecorded: {
        state: "known",
        checked_at: Date.now() / 1000 - 20,
        reason: null,
        count: 2,
        vms: [
          { vmid: 4101, name: "te-old-ws-01", status: "running", node: "dev45" },
          { vmid: 4102, name: "te-old-ws-02", status: "stopped", node: "dev45" },
        ],
      },
    });
    open("/", () => Promise.resolve(range([t])));
    const sec = await screen.findByTestId("unrecorded");
    expect(sec.tagName).toBe("DETAILS");
    expect((sec as HTMLDetailsElement).open).toBe(false);
    expect(sec.textContent).toMatch(/VMs on this cluster that no recorded deploy owns: 2/);
    expect(sec.textContent).toMatch(/live · 2\d s ago/);
    const rows = within(screen.getByTestId("unrecorded-table")).getAllByRole("row");
    expect(rows.map((r) => r.textContent)).toContain("4101te-old-ws-01runningdev45");
    expect(within(sec).queryAllByRole("button")).toHaveLength(0);
  });

  it("a failed sweep reads unknown with the reason, never 0", async () => {
    const t = target({ unrecorded: { state: "unknown", checked_at: Date.now() / 1000, reason: "sweep timed out", count: null, vms: [] } });
    open("/", () => Promise.resolve(range([t])));
    const sec = await screen.findByTestId("unrecorded");
    expect(sec.textContent).toMatch(/owns: unknown/);
    expect(screen.getByTestId("unrecorded-unknown").textContent).toMatch(/sweep timed out/);
  });
});

describe("every empty Range says why", () => {
  it("a cluster with no twins points at the Catalog", async () => {
    open("/", () => Promise.resolve(range([target({ twins: [] })])));
    const empty = await screen.findByTestId("cluster-empty");
    expect(empty.textContent).toMatch(/No twin is deployed on dev45/);
    expect(within(empty).getByRole("link", { name: "Catalog" }).getAttribute("href")).toBe("/catalog");
  });

  it("no cluster configured is said, not shown as an empty list", async () => {
    open("/", () => Promise.resolve(range([])));
    expect((await screen.findByTestId("range-empty")).textContent).toMatch(/no cluster target configured/);
  });

  it("an unreadable store is said per cluster", async () => {
    open("/", () => Promise.resolve(range([target({ store: { ok: false, reason: "state.db schema v2" }, twins: [] })])));
    expect(await screen.findByText(/cannot be read: state.db schema v2/)).toBeTruthy();
    expect(screen.queryByTestId("cluster-empty")).toBeNull();
  });

  it("an unreachable cluster says so, with the reason, and still lists its recorded twins", async () => {
    open("/", () =>
      Promise.resolve(range([target({ reachability: { state: "unreachable", checked_at: Date.now() / 1000 - 5, reason: "connection refused" } })])),
    );
    const reach = await screen.findByTestId("reachability");
    expect(reach.textContent).toMatch(/unreachable/);
    expect(screen.getByText("(connection refused)")).toBeTruthy();
    expect(screen.getByTestId("twin-card")).toBeTruthy();
  });

  it("a server that predates the Range endpoint is named as such", async () => {
    open("/", () => Promise.reject(apiError("not-found", 404, "Not Found")));
    expect((await screen.findByRole("alert")).textContent).toMatch(/predates the Unified Console/);
  });

  it("while reading, it says so instead of showing an empty range", async () => {
    open("/", () => pending());
    expect(await screen.findByText("Reading the range…")).toBeTruthy();
    expect(screen.queryByTestId("range-empty")).toBeNull();
  });
});

describe("the frame", () => {
  it("shows the four areas in order, marks the current one, and names the cluster", async () => {
    open("/", () => pending());
    const nav = screen.getByRole("navigation", { name: "Areas" });
    expect(within(nav).getAllByRole("link").map((a) => a.textContent)).toEqual(["Range", "Catalog", "Design", "Telemetry"]);
    expect(within(nav).getByRole("link", { name: "Range" }).getAttribute("aria-current")).toBe("page");
    await waitFor(() => expect(screen.getByTestId("cluster-indicator").textContent).toBe("cluster: dev45"));
    // The write token is one clearly labelled control, not a field on every page.
    expect(screen.getByTestId("token-open").textContent).toBe("Write token: not set");
  });

  it("redirects an old /imprints URL to /design, keeping the query", async () => {
    function Loc(): JSX.Element {
      const l = useLocation();
      return <output data-testid="loc">{`${l.pathname}${l.search}`}</output>;
    }
    render(
      <TestApp client={unusedDesignApi()} runtime={fakeRuntime()} url="/imprints/corpapcn-golden/matrix?ghosts=1">
        <ConsoleRoutes />
        <Loc />
      </TestApp>,
    );
    await waitFor(() => expect(screen.getByTestId("loc").textContent).toBe("/design/corpapcn-golden/matrix?ghosts=1"));
  });
});
