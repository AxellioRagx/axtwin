/**
 * The Twin page over the REAL 106-host corp-twin (`fixtures/corp-twin-hosts.
 * json`), through the real router and the real frame: what it looks like in
 * one click (ruling §7 b), personas per host on the same screen (c), recorded
 * status everywhere and live one click away with its age (e), and the map and
 * table sharing selection and the URL (rule 5, rule 8).
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useLocation } from "react-router";
import { describe, expect, it } from "vitest";

import type { RuntimeApi } from "../src/api/runtime";
import type { HealthDoc, TwinHostsDoc } from "../src/api/runtimeTypes";
import { ConsoleRoutes, TestApp } from "../src/routes";
import { apiError, corpTwinHosts, fakeRuntime, pending, unusedDesignApi } from "./helpers/runtime";

const ID = "corp-twin-07cfcdb2";

function Loc(): JSX.Element {
  const l = useLocation();
  return <output data-testid="loc">{`${l.pathname}${l.search}`}</output>;
}

function open(url: string, overrides: Partial<RuntimeApi> = {}, calls: string[] = []): void {
  const runtime = fakeRuntime(
    {
      getHosts: () => Promise.resolve(corpTwinHosts()),
      getTargets: () => Promise.resolve({ targets: [{ name: "dev45", default: true, node: "dev45", api_url: "https://x" }] }),
      getPersonas: () => pending(),
      getAccess: () => pending(),
      ...overrides,
    },
    calls,
  );
  render(
    <TestApp client={unusedDesignApi()} runtime={runtime} url={url}>
      <ConsoleRoutes />
      <Loc />
    </TestApp>,
  );
}

const loc = (): string => screen.getByTestId("loc").textContent ?? "";

function health(doc: TwinHostsDoc, checkedAgo: number): HealthDoc {
  const now = Date.now() / 1000;
  return {
    deploy_id: ID,
    target: "dev45",
    checked_at: now - checkedAgo,
    cluster: { state: "reachable", checked_at: now - checkedAgo, reason: null },
    hosts: doc.hosts.map((h) => ({
      host_id: h.host_id,
      vmid: h.vmid,
      recorded_status: h.vm_status,
      power: { state: h.host_id === "hq-mx" ? "stopped" : "running", checked_at: now - checkedAgo, reason: null },
    })),
    host_detail: null,
  };
}

describe("the Twin page -- what it looks like, in one screen", () => {
  it("summarises the twin: cluster, recorded status, hosts, personas", async () => {
    open(`/twin/${ID}`);
    const summary = await screen.findByTestId("twin-summary");
    expect(summary.textContent).toContain("dev45");
    expect(summary.textContent).toContain("106");
    expect(summary.textContent).toContain("84 running instances on 83 hosts");
    expect(within(summary).getAllByText("recorded").length).toBeGreaterThan(0);
  });

  it("the Hosts view lists all 106 hosts with their personas; the Map view aggregates them per segment", async () => {
    open(`/twin/${ID}?view=hosts`);
    const table = await screen.findByTestId("host-table");
    expect(within(table).getAllByRole("row")).toHaveLength(107); // + header
    const ws = within(table).getAllByRole("row").find((r) => r.getAttribute("data-host") === "corp-workstation-10-80-48-core01")!;
    expect(ws.textContent).toContain("corp-workstation"); // persona chip (short form)
    fireEvent.click(screen.getByTestId("view-map"));
    await waitFor(() => expect(loc()).toBe(`/twin/${ID}`));
    // Map: one tile per drawn segment, far fewer than 106 things to read.
    const tiles = screen.getAllByRole("button", { name: /^Segment / });
    expect(tiles.length).toBeGreaterThan(5);
    expect(tiles.length).toBeLessThan(40);
    expect(screen.queryByTestId("host-table")).toBeNull();
  });

  it("the Power (live) column says 'not checked' until asked -- recorded is never passed off as live", async () => {
    open(`/twin/${ID}?view=hosts`, { getHealth: () => Promise.resolve(health(corpTwinHosts(), 12)) });
    const table = await screen.findByTestId("host-table");
    const header = within(table).getAllByRole("columnheader").map((h) => h.textContent ?? "");
    expect(header).toContain("Status (recorded)");
    expect(header.some((h) => h.startsWith("Power (live)"))).toBe(true);
    expect(within(table).getAllByText("not checked")).toHaveLength(106);

    fireEvent.click(screen.getByTestId("check-live"));
    await waitFor(() => expect(within(table).queryAllByText("not checked")).toHaveLength(0));
    expect(header.length).toBe(8);
    expect(within(table).getAllByRole("columnheader").at(-1)!.textContent).toMatch(/Power \(live\) · 1[2-9] s ago/);
    expect(screen.getByText(/live · 1[2-9] s ago/)).toBeTruthy();
    const mx = within(table).getAllByRole("row").find((r) => r.getAttribute("data-host") === "hq-mx")!;
    expect(mx.textContent).toContain("stopped");
  });

  it("a tile click drills into the segment AND narrows the Hosts view -- one URL, shared selection", async () => {
    open(`/twin/${ID}`);
    const tile = (await screen.findAllByRole("button", { name: /^Segment vlan64:/ }))[0]!;
    fireEvent.click(tile);
    await waitFor(() => expect(loc()).toBe(`/twin/${ID}?segment=vlan64`));
    expect(screen.getAllByRole("button", { name: /^Host \S+, / }).length).toBe(36);
    fireEvent.click(screen.getByTestId("view-hosts"));
    await waitFor(() => expect(loc()).toBe(`/twin/${ID}?view=hosts&segment=vlan64`));
    expect(screen.getByTestId("host-count").textContent).toMatch(/^36 of 106 hosts$/);
    fireEvent.click(screen.getByTestId("view-map"));
    fireEvent.click(await screen.findByTestId("map-up"));
    await waitFor(() => expect(loc()).toBe(`/twin/${ID}`));
  });

  it("a table row and a map chip open the SAME host panel, at /twin/<id>/hosts/<host>", async () => {
    open(`/twin/${ID}?view=hosts&segment=vlan64`);
    const table = await screen.findByTestId("host-table");
    const first = within(table).getAllByRole("row")[1]!;
    const host = first.getAttribute("data-host")!;
    fireEvent.click(first);
    await waitFor(() => expect(loc()).toBe(`/twin/${ID}/hosts/${host}?view=hosts&segment=vlan64`));
    expect(await screen.findByTestId("host-panel")).toBeTruthy();
    expect(first.getAttribute("aria-selected")).toBe("true");
    // Switch to the map: the panel stays, and that host's chip is current there.
    fireEvent.click(screen.getByTestId("view-map"));
    await waitFor(() => expect(loc()).toBe(`/twin/${ID}/hosts/${host}?segment=vlan64`));
    expect(screen.getByTestId("host-panel")).toBeTruthy();
    const chip = screen.getAllByRole("button", { name: new RegExp(`^Host ${host},`) })[0]!;
    expect(chip.getAttribute("aria-current")).toBe("true");
  });

  it("the filter is in the URL (replace), and sorting toggles direction", async () => {
    open(`/twin/${ID}?view=hosts`);
    await screen.findByTestId("host-table");
    fireEvent.change(screen.getByTestId("host-filter"), { target: { value: "hq-mx" } });
    await waitFor(() => expect(loc()).toBe(`/twin/${ID}?view=hosts&q=hq-mx`));
    expect(screen.getByTestId("host-count").textContent).toBe("1 of 106 hosts");
    fireEvent.click(screen.getByRole("button", { name: /^Address/ }));
    await waitFor(() => expect(loc()).toBe(`/twin/${ID}?view=hosts&q=hq-mx&sort=ip`));
    fireEvent.click(screen.getByRole("button", { name: /^Address/ }));
    await waitFor(() => expect(loc()).toBe(`/twin/${ID}?view=hosts&q=hq-mx&sort=ip&dir=desc`));
  });

  it("the taps section is labelled recorded, not capture-verified", async () => {
    open(`/twin/${ID}?view=hosts`);
    const taps = await screen.findByTestId("taps-section");
    expect(taps.textContent).toMatch(/Recorded, not capture-verified/);
  });
});

describe("the Twin page -- when it cannot show the twin, it says why", () => {
  it("a deploy the store does not have", async () => {
    open(`/twin/nope`, { getHosts: () => Promise.reject(apiError("not-found", 404, "unknown deploy 'nope'")) });
    expect((await screen.findByRole("alert")).textContent).toMatch(/No twin deploy named nope/);
  });

  it("a server that predates the hosts endpoint", async () => {
    open(`/twin/${ID}`, { getHosts: () => Promise.reject(apiError("not-found", 404, "Not Found")) });
    expect((await screen.findByRole("alert")).textContent).toMatch(/predates the Unified Console/);
  });

  it("a host the inventory does not list", async () => {
    open(`/twin/${ID}/hosts/ghost-host`);
    expect(await screen.findByText(/has no host named/)).toBeTruthy();
  });

  it("an empty inventory", async () => {
    const doc = corpTwinHosts();
    doc.hosts = [];
    open(`/twin/${ID}`, { getHosts: () => Promise.resolve(doc) });
    expect(await screen.findByText("The recorded inventory for this twin lists no hosts.")).toBeTruthy();
  });

  it("a twin with no tap deploy says nothing is mirrored", async () => {
    const doc = corpTwinHosts();
    doc.tap = null;
    open(`/twin/${ID}?view=hosts`, { getHosts: () => Promise.resolve(doc) });
    expect(await screen.findByText(/No tap deploy is recorded for this twin/)).toBeTruthy();
  });
});

describe("fix round 1 -- the Twin page", () => {
  it("taps come from the target-aware /hosts model, never /api/taps (S9)", async () => {
    const calls: string[] = [];
    open(`/twin/${ID}?target=pxtwin1&view=hosts`, {}, calls);
    const taps = await screen.findByTestId("taps-section");
    fireEvent.click(within(taps).getByText(/mirrored NIC/));
    const rows = within(screen.getByTestId("taps-table")).getAllByRole("row");
    const expected = corpTwinHosts().hosts.reduce((n, h) => n + h.taps.length, 0);
    expect(expected).toBeGreaterThan(0);
    expect(rows).toHaveLength(expected + 1);
    expect(calls.some((c) => /tap/i.test(c))).toBe(false);
    expect(calls).toContain("getHosts");
  });

  it("on a protected twin the host panel's Change personas is LOCKED with the reason; the override unlocks it", async () => {
    const doc = corpTwinHosts();
    doc.protected = true;
    open(`/twin/${ID}/hosts/hq-mx`, { getHosts: () => Promise.resolve(doc) });
    const edit = await screen.findByTestId("edit-personas");
    expect(edit.getAttribute("data-locked")).toBe("true");
    expect(edit.className).not.toMatch(/primary/);
    expect(screen.getByTestId("edit-personas-reason").textContent).toMatch(/Protected — writes are refused/);
    expect(screen.getByTestId("destroy-open").getAttribute("data-locked")).toBe("true");
    fireEvent.click(edit);
    expect(loc()).toBe(`/twin/${ID}/hosts/hq-mx`); // not navigated to the editor
    fireEvent.change(screen.getByTestId("protection-typed"), { target: { value: ID } });
    fireEvent.click(screen.getByTestId("protection-confirm"));
    const live = screen.getByTestId("edit-personas");
    expect(live.getAttribute("data-locked")).toBeNull();
    // One override per page: the header's Destroy is unlocked too.
    expect(screen.getByTestId("destroy-open").getAttribute("data-locked")).toBeNull();
    fireEvent.click(live);
    await waitFor(() => expect(loc()).toBe(`/twin/${ID}/hosts/hq-mx/personas`));
  });

  it("an unprotected twin's write controls are ordinary buttons", async () => {
    open(`/twin/${ID}/hosts/hq-mx`);
    const edit = await screen.findByTestId("edit-personas");
    expect(edit.getAttribute("data-locked")).toBeNull();
    expect(edit.className).toMatch(/primary/);
  });

  it("a protected twin says so in the header and in the host panel", async () => {
    const doc = corpTwinHosts();
    doc.protected = true;
    open(`/twin/${ID}/hosts/hq-mx`, { getHosts: () => Promise.resolve(doc) });
    const title = await screen.findByRole("heading", { level: 1 });
    expect(within(title).getByTestId("protected-badge").textContent).toBe("Protected");
    expect(within(await screen.findByTestId("host-panel")).getByTestId("protected-badge")).toBeTruthy();
  });

  it("the host panel shows a persona that could not be stopped, and the last change's diagnostics", async () => {
    const doc = corpTwinHosts();
    const h = doc.hosts.find((x) => x.host_id === "file-server-10-80-75-core")!;
    h.personas = [{ ...h.personas[0]!, stop_error: { message: "unit still active", at: 1, heartbeat: null } }];
    h.host_edit = {
      job_id: "j1", host_id: h.host_id, status: "completed", started_at: 1, finished_at: 2, fingerprint: "f", plan: null,
      diagnostics: [{ level: "error", message: "stop of cast-file-server failed: unit still active" }],
    };
    open(`/twin/${ID}/hosts/${h.host_id}`, { getHosts: () => Promise.resolve(doc) });
    expect((await screen.findByTestId("stop-error")).textContent).toMatch(/Could not stop cast-file-server — it may still be running/);
    expect(screen.getByTestId("host-edit").textContent).toMatch(/error: stop of cast-file-server failed/);
  });
});

describe("fix round 2 -- Map | Hosts, and the persona filters", () => {
  it("the switch sits under the header, defaults to the map, and keeps its choice in the URL", async () => {
    open(`/twin/${ID}`);
    const sw = await screen.findByTestId("view-switch");
    const tabs = within(sw).getAllByRole("tab");
    expect(tabs.map((t) => t.textContent)).toEqual(["Map 47 segments", "Hosts 106 hosts"]);
    expect(screen.getByTestId("view-map").getAttribute("aria-selected")).toBe("true");
    fireEvent.click(screen.getByTestId("view-hosts"));
    await waitFor(() => expect(loc()).toBe(`/twin/${ID}?view=hosts`));
    expect(screen.getByTestId("view-hosts").getAttribute("aria-selected")).toBe("true");
    expect(screen.getByTestId("host-table")).toBeTruthy();
  });

  it("filters hosts with / without personas, and by one persona", async () => {
    open(`/twin/${ID}?view=hosts`);
    await screen.findByTestId("host-table");
    fireEvent.change(screen.getByTestId("persona-presence"), { target: { value: "without" } });
    await waitFor(() => expect(loc()).toBe(`/twin/${ID}?view=hosts&personas=without`));
    expect(screen.getByTestId("host-count").textContent).toBe("23 of 106 hosts");
    fireEvent.change(screen.getByTestId("persona-presence"), { target: { value: "with" } });
    expect(screen.getByTestId("host-count").textContent).toBe("83 of 106 hosts");
    fireEvent.change(screen.getByTestId("persona-filter"), { target: { value: "cast-file-server" } });
    await waitFor(() => expect(loc()).toBe(`/twin/${ID}?view=hosts&personas=with&persona=cast-file-server`));
    expect(screen.getByTestId("host-count").textContent).toBe("1 of 106 hosts");
  });
});

describe("fix round 3 -- a superseded host edit", () => {
  it("reads as 'superseded by a whole-profile apply', not as a live edit", async () => {
    const doc = corpTwinHosts();
    const h = doc.hosts.find((x) => x.host_id === "file-server-10-80-75-core")!;
    h.host_edit = {
      job_id: "j1", host_id: h.host_id, status: "completed", started_at: 1, finished_at: 2, fingerprint: "f", plan: null,
      diagnostics: [{ level: "info", message: "started dev-workstation" }],
      superseded: true, superseded_at: Date.now() / 1000 - 120,
    };
    open(`/twin/${ID}/hosts/${h.host_id}`, { getHosts: () => Promise.resolve(doc) });
    const sec = await screen.findByTestId("host-edit");
    expect(within(sec).getByTestId("host-edit-superseded").textContent).toMatch(/superseded by a whole-profile apply 2 min ago/);
    // Its record is history, behind a disclosure -- no live status badge.
    expect(within(sec).queryByText("up")).toBeNull();
    expect(sec.querySelector("details")!.open).toBe(false);
  });

  it("an edit that is not superseded still shows its live status", async () => {
    const doc = corpTwinHosts();
    const h = doc.hosts.find((x) => x.host_id === "file-server-10-80-75-core")!;
    h.host_edit = { job_id: "j1", host_id: h.host_id, status: "completed", started_at: 1, finished_at: 2, fingerprint: "f", plan: null, diagnostics: [] };
    open(`/twin/${ID}/hosts/${h.host_id}`, { getHosts: () => Promise.resolve(doc) });
    const sec = await screen.findByTestId("host-edit");
    expect(within(sec).queryByTestId("host-edit-superseded")).toBeNull();
    expect(within(sec).getByText("up")).toBeTruthy();
  });
});

