import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ApiError } from "../src/api/client";
import type { DiscoveryRun, Imprint, TwinSummary } from "../src/api/types";
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
  it("has Discovery with Sources and Runs beneath it, Imprints and Twins", async () => {
    const { api } = fakeApi();
    renderAt(api, "/discovery/runs");
    const areas = screen.getByRole("navigation", { name: "Areas" });
    expect(areas.textContent).toBe("DiscoveryImprintsTwins");
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
  it("lists every imprint with its lineage, assertions and twins, and deletes behind a confirmation", async () => {
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

    fireEvent.click(screen.getByRole("button", { name: "Delete imprint corp-apcn-2h, corrected" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm delete corp-apcn-2h, corrected" }));
    await waitFor(() => expect(screen.getAllByTestId("imprint-row").length).toBe(1));
    expect(state.writes).toEqual([{ op: "delete-imprint", id: "pit-2" }]);
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

    // Assertions: empty to start, a save writes the document with the three lists.
    const box = (await screen.findByRole("textbox", { name: "Assertions document" })) as HTMLTextAreaElement;
    expect(JSON.parse(box.value)).toEqual({ add: [], override: [], suppress: [] });
    fireEvent.change(box, { target: { value: JSON.stringify({ add: [{ kind: "host", key: "address:10.1.1.1", role: "web-tier" }], override: [], suppress: [] }) } });
    fireEvent.click(screen.getByRole("button", { name: "Save assertions" }));
    await screen.findByText("saved");
    const put = state.writes.at(-1)!;
    expect(put.op).toBe("put-assertions");
    expect((put.body as Record<string, unknown>).add).toEqual([{ kind: "host", key: "address:10.1.1.1", role: "web-tier" }]);
    expect((put.body as Record<string, unknown>).source).toBe("operator:console");

    // Cast: lands a twin, named after the imprint (as it was when the page opened) by default.
    expect((screen.getByLabelText("Twin id") as HTMLInputElement).value).toBe("corp-apcn-2h");
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

  it("deletes from its page and returns to the list", async () => {
    const { api } = fakeApi({ imprints: [imprint("pit-1", "corp-apcn-2h")] });
    renderAt(api, "/imprints/pit-1");
    fireEvent.click(await screen.findByRole("button", { name: "Delete imprint" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm delete corp-apcn-2h" }));
    await screen.findByText(/No imprint yet/);
  });
});

describe("the twins page", () => {
  it("lists every twin with its lineage, and names unreadable folders", async () => {
    const { api, state } = fakeApi({
      imprints: [imprint("pit-1", "corp-apcn-2h")],
      twins: { test2: twin("test2", { pit_id: "pit-1" }), "corp-twin": twin("corp-twin", { hosts: 42, segments: 3 }) },
      skippedTwins: [{ id: "broken", reason: "no twin config" }],
      runs: [run("disc-new", "corp-apcn-2h", "completed", "2026-10-28T14:00:00Z", "pit-1")],
    });
    renderAt(api, "/twins");
    const rows = await screen.findAllByTestId("twin-row");
    expect(rows.length).toBe(2);
    const test2 = rows.find((r) => r.textContent?.startsWith("test2"))!;
    expect(test2.textContent).toContain("corp-apcn-2h");
    expect(test2.textContent).toContain("0.54");
    const corp = rows.find((r) => r.textContent?.startsWith("corp-twin"))!;
    expect(corp.textContent).toContain("authored");
    expect(screen.getByRole("link", { name: "corp-apcn-2h" }).getAttribute("href")).toBe("/imprints/pit-1");
    expect(screen.getByText("broken").parentElement?.textContent).toContain("no twin config");

    fireEvent.click(screen.getByRole("button", { name: "Delete twin corp-twin" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm delete corp-twin" }));
    await waitFor(() => expect(screen.getAllByTestId("twin-row").length).toBe(1));
    expect(state.writes).toEqual([{ op: "delete-twin", id: "corp-twin" }]);

    state.refuse.deleteTwin = new ApiError({ kind: "conflict", status: 409, message: "imprint 'test2' is the config of live deploy(s) ['dep-1']; destroy them first", url: "" });
    fireEvent.click(screen.getByRole("button", { name: "Delete twin test2" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm delete test2" }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("destroy them first");
    expect(screen.getAllByTestId("twin-row").length).toBe(1);
  });

  it("the twin page shows what it was cast from, each segment's host count, and deletes back to the list", async () => {
    const segments = [
      { name: "seg-10-1-10-0", cidr: "10.1.10.0/24", host_count: 6, confidence: null },
      { name: "seg-10-20-193-0", cidr: "10.20.193.0/24", host_count: 30, confidence: 0.7 },
    ];
    const { api } = fakeApi({ imprints: [imprint("pit-1", "corp-apcn-2h")], twins: { test2: twin("test2", { pit_id: "pit-1", topology_segments: segments }) } });
    renderAt(api, "/twins/test2");
    await screen.findByTestId("twin-summary");
    expect(screen.getByRole("link", { name: "pit-1" }).getAttribute("href")).toBe("/imprints/pit-1");
    const rows = screen.getAllByRole("row").slice(1).map((r) => Array.from(r.querySelectorAll("td")).map((c) => c.textContent));
    expect(rows).toEqual([
      ["10.1.10.0/24", "6"],
      ["10.20.193.0/24", "30"],
    ]);
    fireEvent.click(screen.getByRole("button", { name: "Delete twin" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm delete test2" }));
    await screen.findByText(/No twin in the catalog/);
  });
});
