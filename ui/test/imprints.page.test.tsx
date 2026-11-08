import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ApiError } from "../src/api/client";
import type { DiscoveryRun, Imprint, TwinSummary, TwinTree } from "../src/api/types";
import { fakeApi } from "./helpers/fakeApi";
import { renderAt } from "./helpers/render";

export function imprint(pit_id: string, name: string, over: Partial<Imprint> = {}): Imprint {
  return { pit_id, name, derived_from: null, has_assertions: false, twins: [], as_of: 1791313200, observed_from: 1791309600, observed_to: 1791313200, source: "axquery:http://127.0.0.1:9090", network: "corp-apcn", vantage: "axellio-hq/corp-apcn-rdr1:12", windows: 3, entities: 1780, segments: 34, merge_axis: null, ...over };
}

export function twin(id: string, over: Partial<TwinSummary> = {}): TwinSummary {
  return { id, as_of: "2026-10-06T19:00:00Z", pit_id: null, hosts: 262, segments: 45, services: 424, personas: 17, mean_confidence: 0.54094, windows: 3, topology_segments: [], ...over };
}

function run(id: string, name: string, status: DiscoveryRun["status"], created_at: string, pit_id: string | null = null): DiscoveryRun {
  return {
    id, status, plugin: "axquery", name, imprint_id: name, stages_done: [], stages: ["ingest", "bake"],
    pit_id, pit_name: pit_id ? name : null, error: null, created_at, finished_at: null, source_id: "src-1",
    started_at: created_at, ts_from: null, ts_to: null, via: null, progress_ts: null, progress_at: null, cancel_requested_at: null,
  };
}

describe("the top menu", () => {
  it("has Discovery with Sources and Runs beneath it, Imprints, Twins and Canvas", async () => {
    const { api } = fakeApi();
    renderAt(api, "/discovery/runs");
    const areas = screen.getByRole("navigation", { name: "Areas" });
    expect(areas.textContent).toBe("DiscoveryImprintsTwinsCanvas▾");
    const sub = screen.getByRole("navigation", { name: "Discovery" });
    expect(sub.textContent).toBe("SourcesRuns");
    expect(screen.getByRole("link", { name: "Runs" }).className).toContain("active");
    expect(screen.getByRole("link", { name: "Sources" }).className).not.toContain("active");
    expect(screen.getByRole("link", { name: "Discovery" }).className).toContain("active");
  });

  it("shows no Discovery sub-menu under Imprints or Twins", async () => {
    const { api } = fakeApi();
    renderAt(api, "/twins");
    await screen.findByRole("heading", { name: "Twins" });
    expect(screen.queryByRole("navigation", { name: "Discovery" })).toBeNull();
    expect(screen.getByRole("link", { name: "Twins" }).className).toContain("active");
  });
});

describe("the imprints page", () => {
  it("lists every imprint with its lineage, assertions and twins; the row has no delete (that is on the page)", async () => {
    const { api, state } = fakeApi({
      imprints: [imprint("pit-1", "corp-apcn-2h", { has_assertions: true }), imprint("pit-2", "corp-apcn-2h, corrected", { derived_from: "pit-1" })],
      twins: { test2: twin("test2", { pit_id: "pit-1" }) },
    });
    renderAt(api, "/imprints");
    const rows = await screen.findAllByTestId("imprint-row");
    expect(rows.length).toBe(2);
    const first = rows[0]!;
    expect(first.textContent).toContain("corp-apcn-2h");
    expect(first.textContent).toContain("yes");
    expect(first.textContent).toContain("test2");
    expect(rows[1]!.textContent).toContain("pit-1");
    expect(screen.getByRole("link", { name: "corp-apcn-2h" }).getAttribute("href")).toBe("/imprints/pit-1");

    // One name+id presentation: the id sits under the name; one date style (R4-7: local time with
    // the zone, the exact UTC instant in the tooltip; the suite runs in UTC).
    expect(first.querySelector(".entity-id")?.textContent).toBe("pit-1");
    expect(first.querySelector("time")?.textContent).toMatch(/^\d{4}-\d\d-\d\d \d\d:\d\d [A-Z][A-Za-z0-9+:-]*$/);
    expect(first.querySelector("time")?.getAttribute("title")).toMatch(/^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d UTC$/);
    // No delete control on any row.
    expect(within(screen.getByRole("table")).queryAllByRole("button")).toEqual([]);
    expect(screen.queryByText("🗑")).toBeNull();
    // A click on the row opens the imprint.
    fireEvent.click(rows[1]!.querySelectorAll("td")[2]!);
    await screen.findByRole("heading", { name: "Imprint corp-apcn-2h, corrected" });
    expect(state.writes).toEqual([]);
  });

  it("says when there is none", async () => {
    const { api } = fakeApi();
    renderAt(api, "/imprints");
    await screen.findByText(/No imprint yet/);
  });

  it("says when the listing is the last one read because a bake holds the store", async () => {
    const { api } = fakeApi({ imprints: [imprint("pit-1", "corp-apcn-2h")], imprintWarnings: ["the IR store is being written (a run is baking into it); showing the last listing read from it"] });
    renderAt(api, "/imprints");
    await screen.findAllByTestId("imprint-row");
    expect(screen.getByRole("status").textContent).toContain("being written");
  });
});

describe("the imprint page", () => {
  it("renames, saves as, edits assertions and casts into a twin", async () => {
    const { api, state } = fakeApi({ imprints: [imprint("pit-1", "corp-apcn-2h")] });
    renderAt(api, "/imprints/pit-1");
    await screen.findByRole("heading", { name: "Imprint corp-apcn-2h" });

    fireEvent.click(screen.getByRole("button", { name: "Rename imprint" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Imprint name" }), { target: { value: "day one" } });
    fireEvent.click(screen.getByRole("button", { name: "Save name" }));
    await screen.findByRole("heading", { name: "Imprint day one" });
    expect(state.writes).toEqual([{ op: "rename-imprint", id: "pit-1", body: { name: "day one" } }]);

    // Assertions: a list first; the JSON is the escape hatch under a collapsed Advanced.
    await screen.findByText("No assertion yet.");
    const advanced = screen.getByText(/^Advanced/).closest("details")!;
    expect(advanced.open).toBe(false);
    fireEvent.click(screen.getByText(/^Advanced/));
    const box = (await screen.findByLabelText("Assertions document")) as HTMLTextAreaElement;
    expect(advanced.contains(box)).toBe(true);
    expect(JSON.parse(box.value)).toEqual({ add: [], override: [], suppress: [] });
    fireEvent.change(box, { target: { value: JSON.stringify({ add: [{ kind: "host", key: "address:10.1.1.1", role: "web-tier", note: "seen on the whiteboard" }], override: [], suppress: [] }) } });
    fireEvent.click(screen.getByRole("button", { name: "Save assertions" }));
    await screen.findByText("saved");
    // The saved assertion is a row with its origin: the record's note and the document's source.
    const arow = await screen.findByTestId("assertion-row");
    expect(arow.textContent).toContain("address:10.1.1.1");
    expect(arow.textContent).toContain("seen on the whiteboard");
    expect(arow.textContent).toContain("operator:console");
    const put = state.writes.at(-1)!;
    expect(put.op).toBe("put-assertions");
    expect((put.body as Record<string, unknown>).add).toEqual([{ kind: "host", key: "address:10.1.1.1", role: "web-tier", note: "seen on the whiteboard" }]);
    expect((put.body as Record<string, unknown>).source).toBe("operator:console");

    // Cast: a primary button at the top opens the form; it lands a twin, named after the imprint by default.
    const castTop = screen.getByRole("button", { name: "Cast into a twin" });
    expect(castTop.className).toContain("primary");
    expect(screen.queryByLabelText("Twin id")).toBeNull();
    fireEvent.click(castTop);
    expect((screen.getByLabelText("Twin id") as HTMLInputElement).value).toBe("day-one");
    fireEvent.change(screen.getByLabelText("Twin id"), { target: { value: "corp-twin-2" } });
    fireEvent.change(screen.getByLabelText("Sensors"), { target: { value: "XPress-SW-A, XPress-SW-B" } });
    fireEvent.click(screen.getByRole("button", { name: "Cast" }));
    const status = await screen.findByRole("status");
    expect(status.textContent).toContain("Cast twin corp-twin-2: 1,780 hosts, 34 segments, 1 caveat(s).");
    expect(state.writes.at(-1)).toEqual({ op: "cast", id: "pit-1", body: { twin_id: "corp-twin-2", sensors: ["XPress-SW-A", "XPress-SW-B"], replace: false } });
    expect(screen.getAllByRole("link", { name: "corp-twin-2" }).length).toBe(2); // the result and the twins fact

    // The same id again is refused until "replace" is ticked.
    fireEvent.click(screen.getByRole("button", { name: "Cast" }));
    await screen.findByText(/could not cast: .*replace: true/);
    fireEvent.click(screen.getByLabelText(/Replace the twin/));
    fireEvent.click(screen.getByRole("button", { name: "Cast" }));
    await waitFor(() => expect(screen.getByRole("status").textContent).toContain("Replaced twin corp-twin-2"));

    // Save as: a copy, and the page moves to it.
    fireEvent.change(screen.getByRole("textbox", { name: "Save as name" }), { target: { value: "day one, take two" } });
    fireEvent.click(screen.getByRole("button", { name: "Save as" }));
    await screen.findByRole("heading", { name: "Imprint day one, take two" });
    expect(screen.getByText("pit-1", { selector: "a" })).toBeTruthy();
  });

  it("the JSON editor keeps confirm and doubt through a save (it used to drop them)", async () => {
    const { api, state } = fakeApi({ imprints: [imprint("pit-1", "corp-apcn-2h")] });
    const confirm = [{ key: "address:10.1.1.1", note: "seen on the rack" }];
    const doubt = [{ key: "cidr:10.9.0.0/24", note: "one probe only" }];
    state.assertions["pit-1"] = {
      imprint_id: "pit-1", path: "/store/pits/pit-1/pit-1.assertions.yaml", exists: true, etag: "etag-0",
      schema: "axr-assertions/2", imprint: "pit-1", as_of: "2026-11-08T00:00:00Z", source: "operator:console",
      add: [], override: [], suppress: [], confirm, doubt,
    };
    renderAt(api, "/imprints/pit-1");
    fireEvent.click(await screen.findByText(/^Advanced/));
    const box = (await screen.findByLabelText("Assertions document")) as HTMLTextAreaElement;
    await waitFor(() => expect(JSON.parse(box.value)).toEqual({ add: [], override: [], suppress: [], confirm, doubt }));
    // An unrelated edit: the marks go back with it.
    fireEvent.change(box, { target: { value: JSON.stringify({ ...JSON.parse(box.value), suppress: [{ key: "address:10.1.1.9", note: "scanner" }] }) } });
    fireEvent.click(screen.getByRole("button", { name: "Save assertions" }));
    await screen.findByText("saved");
    const body = state.writes.at(-1)!.body as Record<string, unknown>;
    expect(body.confirm).toEqual(confirm);
    expect(body.doubt).toEqual(doubt);
    expect(body.etag).toBe("etag-0");
    expect(JSON.parse(box.value).confirm).toEqual(confirm);
    // Emptying a block in the text removes it.
    fireEvent.change(box, { target: { value: JSON.stringify({ ...JSON.parse(box.value), doubt: [] }) } });
    fireEvent.click(screen.getByRole("button", { name: "Save assertions" }));
    await waitFor(() => expect((state.writes.at(-1)!.body as Record<string, unknown>).etag).not.toBe("etag-0"));
    expect("doubt" in (state.writes.at(-1)!.body as Record<string, unknown>)).toBe(false);
  });

  it("deletes from its page and returns to the list", async () => {
    const { api } = fakeApi({ imprints: [imprint("pit-1", "corp-apcn-2h")] });
    renderAt(api, "/imprints/pit-1");
    fireEvent.click(await screen.findByRole("button", { name: "Delete imprint" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm delete corp-apcn-2h" }));
    await screen.findByText(/No imprint yet/);
  });
});

describe("the twins page", () => {
  it("lists every twin: origin, per-tier deploy state, counts; no engineering columns and no trash on the row", async () => {
    const { api, state } = fakeApi({
      imprints: [imprint("pit-1", "corp-apcn-2h")],
      twins: { test2: twin("test2", { pit_id: "pit-1" }), "corp-twin": twin("corp-twin", { hosts: 42, segments: 3 }) },
      skippedTwins: [{ id: "broken", reason: "no twin config" }],
      runs: [run("disc-new", "corp-apcn-2h", "completed", "2026-10-28T14:00:00Z", "pit-1")],
      deploys: [
        { deploy_id: "dep-1", scenario_id: "test2", status: "completed", started_at: 100, updated_at: 100, tier: "te" },
        { deploy_id: "dep-2", scenario_id: "tfc-dep-1", status: "failed", started_at: 110, updated_at: 110, tier: "tfc" },
        { deploy_id: "dep-3", scenario_id: "tap-dep-1", status: "running", started_at: 111, updated_at: 111, tier: "tap" },
      ],
    });
    renderAt(api, "/twins");
    const rows = await screen.findAllByTestId("twin-row");
    expect(rows.length).toBe(2);
    expect(screen.getAllByRole("columnheader").map((h) => h.textContent)).toEqual(["Twin", "Origin", "Deploy", "Hosts", "Segments", "Last changed", ""]);
    const test2 = rows.find((r) => r.textContent?.startsWith("test2"))!;
    expect(test2.textContent).toContain("cast from corp-apcn-2h");
    expect(screen.getByRole("link", { name: "corp-apcn-2h" }).getAttribute("href")).toBe("/imprints/pit-1");
    const corp = rows.find((r) => r.textContent?.startsWith("corp-twin"))!;
    expect(corp.textContent).toContain("authored");
    for (const r of rows) {
      expect(r.textContent).not.toContain("As of: unknown");
      expect(r.textContent).not.toContain("Confidence");
      expect(r.textContent).not.toContain("none"); // no confidence cell
      expect(r.textContent).not.toContain("🗑");
    }
    // The canvas pill's wording per tier: te ✓, tfc failed, tap running; nothing standing is "undeployed".
    await waitFor(() => expect(within(test2).getByTestId("tier-te").textContent).toBe("te ✓"));
    expect(within(test2).getByTestId("tier-tfc").textContent).toBe("tfc ✗ failed");
    expect(within(test2).getByTestId("tier-tfc").className).toContain("tier-failed");
    expect(within(test2).getByTestId("tier-tap").textContent).toBe("tap ⟳ running");
    expect(within(corp).getByTestId("twin-deploy").textContent).toBe("undeployed");
    expect(corp.textContent).toContain("42");
    expect(corp.textContent).toContain("3");
    expect(within(corp).getByRole("link", { name: "Canvas" }).getAttribute("href")).toBe("/canvas/corp-twin");
    expect(within(corp).getByRole("link", { name: "Deploy" }).getAttribute("href")).toBe("/twins/corp-twin#deploy");
    expect(screen.getByText("broken").parentElement?.textContent).toContain("no twin config");

    // A quiet Delete… leads to the twin's page, already at its confirmation; nothing is written yet.
    fireEvent.click(within(corp).getByRole("button", { name: "Delete twin corp-twin…" }));
    await screen.findByRole("heading", { name: "Twin corp-twin" });
    expect(state.writes).toEqual([]);
    fireEvent.click(await screen.findByRole("button", { name: "Confirm delete corp-twin" }));
    await waitFor(() => expect(screen.getAllByTestId("twin-row").length).toBe(1));
    expect(state.writes).toEqual([{ op: "delete-twin", id: "corp-twin" }]);
  });

  it("a refused delete on the twin page shows the server's reason", async () => {
    const { api, state } = fakeApi({ twins: { test2: twin("test2") } });
    state.refuse.deleteTwin = new ApiError({ kind: "conflict", status: 409, message: "imprint 'test2' is the config of live deploy(s) ['dep-1']; destroy them first", url: "" });
    renderAt(api, "/twins/test2");
    fireEvent.click(await screen.findByRole("button", { name: "Delete twin" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm delete test2" }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("destroy them first");
  });

  it("the twin page shows what it was cast from, the tree of segments, groups and personas, and deletes back to the list", async () => {
    const tree: TwinTree = {
      id: "test2", hosts: 4, traffic_profile: "tfc-cast.yaml", tap_plan: "tap-cast.yaml", warnings: ["traffic profile assigns 'cast-ws' to node 'ghost', which the twin config does not declare"],
      routers: [{ name: "gw", kind: "router", role: "gw", image: "debian-12-cloud@v1", count: 1, instances: ["gw"], segments: ["lan", "dmz"], requires: ["linux"], resources: null, listen: [], install: [], joins: null, observation: null, personas: [] }],
      segments: [
        {
          name: "lan", cidr: "10.5.0.0/24", zone_type: "simple", hosts: 3, roles: [{ role: "corp-workstation", hosts: 3 }], observation: { windows_seen: 2, windows_total: 2, occupancy: 1.0 },
          taps: [{ sensors: ["sensor-a"], mode: "exclusive" }],
          nodes: [{
            name: "ws", kind: "host", role: "corp-workstation", image: "win11@v1", count: 3, instances: ["ws01", "ws02", "ws03"], segments: ["lan"], requires: ["windows"], resources: { cpu: 2, ram: "4Gi" },
            listen: [{ port: 3389, proto: "tcp" }], install: [], joins: "corp.example", observation: { windows_seen: 2, windows_total: 2, occupancy: 1.0, role_confidence: 0.35 },
            personas: [
              { id: "cast-ws", bound_by: "role", instances: 3, schedule: null, seed: 7, os_family: "windows", drive: "scripted", missing: false, emits: [{ proto: "https", ports: [443], peer_role: "web-tier", transport: "tcp", cadence: "cyclic:300s", volume: "low", optional: false, peer_scope: "one" }] },
              { id: "cast-ws-chatter", bound_by: "node", instances: 2, schedule: "24x7", seed: null, os_family: null, drive: null, missing: true, emits: [] },
            ],
          }],
        },
        { name: "dmz", cidr: "10.5.1.0/24", zone_type: "simple", hosts: 1, roles: [{ role: null, hosts: 1 }], observation: null, taps: [], nodes: [{ name: "lonely", kind: "host", role: null, image: "debian-12-cloud@v1", count: 1, instances: ["lonely"], segments: ["dmz"], requires: [], resources: null, listen: [], install: [], joins: null, observation: null, personas: [] }] },
      ],
    };
    const { api } = fakeApi({ imprints: [imprint("pit-1", "corp-apcn-2h")], twins: { test2: twin("test2", { pit_id: "pit-1" }) }, trees: { test2: tree } });
    renderAt(api, "/twins/test2");
    await screen.findByTestId("twin-summary");
    expect(screen.getByRole("link", { name: "pit-1" }).getAttribute("href")).toBe("/imprints/pit-1");

    // Collapsed: every segment is a row with its size and role mix; nothing below it yet.
    await screen.findByTestId("twin-tree");
    expect(screen.getByRole("status").textContent).toContain("node 'ghost'");
    const segments = screen.getAllByTestId("tree-segment");
    expect(segments.map((s) => s.querySelector("button")?.textContent)).toEqual([
      expect.stringContaining("10.5.0.0/24"), expect.stringContaining("10.5.1.0/24"),
    ]);
    expect(segments[0]!.textContent).toContain("3 hosts");
    expect(segments[0]!.textContent).not.toContain("corp-workstation"); // the mix waits inside
    expect(segments[0]!.textContent).toContain("tap sensor-a");
    expect(screen.queryAllByTestId("tree-node")).toHaveLength(1); // the router, listed once
    expect(screen.getByText("Routers")).toBeTruthy();

    // Open the segment: its groups appear with role, count, listeners and personas on the row.
    fireEvent.click(screen.getByRole("button", { name: /10\.5\.0\.0\/24/ }));
    const ws = screen.getAllByTestId("tree-node").find((n) => n.textContent?.includes("ws"))!;
    const wsRow = ws.querySelector("button")!;
    expect(wsRow.getAttribute("aria-expanded")).toBe("false");
    expect(wsRow.textContent).toContain("×3");
    expect(wsRow.textContent).toContain("corp-workstation");
    expect(wsRow.textContent).not.toContain("tcp/3389"); // listeners and personas wait inside
    expect(wsRow.textContent).not.toContain("cast-ws");
    expect(wsRow.textContent).toContain("role conf 0.35");
    expect(screen.queryByTestId("tree-detail")).toBeNull();

    // Open the group: its facts and each persona's traffic.
    fireEvent.click(wsRow);
    const detail = await screen.findByTestId("tree-detail");
    expect(detail.textContent).toContain("ws01, ws02, ws03");
    expect(detail.textContent).toContain("cpu 2 · ram 4Gi");
    expect(detail.textContent).toContain("corp.example");
    expect(detail.textContent).toContain("2 of 2 windows, occupancy 1.00, role confidence 0.35");
    const personas = screen.getAllByTestId("tree-persona");
    expect(personas).toHaveLength(2);
    expect(personas[0]!.textContent).toContain("cast-ws");
    expect(personas[0]!.textContent).toContain("by role");
    expect(personas[0]!.querySelectorAll("tbody tr")).toHaveLength(1);
    expect(personas[0]!.textContent).toContain("https/tcp");
    expect(personas[0]!.textContent).toContain("web-tier");
    expect(personas[1]!.textContent).toContain("2 of 3 instances");
    expect(personas[1]!.textContent).toContain("no document");

    // Close it again.
    fireEvent.click(wsRow);
    expect(screen.queryByTestId("tree-detail")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Delete twin" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm delete test2" }));
    await screen.findByText(/No twin in the catalog/);
  });
});
