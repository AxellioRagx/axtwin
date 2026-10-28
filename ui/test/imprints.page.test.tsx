import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ApiError } from "../src/api/client";
import type { DiscoveryRun, ImprintSummary } from "../src/api/types";
import { fakeApi } from "./helpers/fakeApi";
import { renderAt } from "./helpers/render";

function imprint(id: string, over: Partial<ImprintSummary> = {}): ImprintSummary {
  return { id, as_of: "2026-10-06T19:00:00Z", hosts: 262, segments: 45, services: 424, personas: 17, mean_confidence: 0.54094, windows: 3, topology_segments: [], ...over };
}

function run(id: string, imprint_id: string, status: DiscoveryRun["status"], created_at: string): DiscoveryRun {
  return {
    id, status, plugin: "axquery", imprint_id, stages_done: [], stages: ["ingest", "materialize", "cast", "install"],
    pit_id: status === "completed" ? `pit-${id}` : null, error: null, created_at, finished_at: null, source_id: "src-1",
    started_at: created_at, ts_from: null, ts_to: null, via: null, progress_ts: null, progress_at: null, cancel_requested_at: null,
  };
}

describe("the top menu", () => {
  it("has Discovery with Sources and Runs beneath it, and Imprints", async () => {
    const { api } = fakeApi();
    renderAt(api, "/discovery/runs");
    const areas = screen.getByRole("navigation", { name: "Areas" });
    expect(areas.textContent).toBe("DiscoveryImprints");
    const sub = screen.getByRole("navigation", { name: "Discovery" });
    expect(sub.textContent).toBe("SourcesRuns");
    expect(screen.getByRole("link", { name: "Runs" }).className).toContain("active");
    expect(screen.getByRole("link", { name: "Sources" }).className).not.toContain("active");
    expect(screen.getByRole("link", { name: "Discovery" }).className).toContain("active");
  });

  it("shows no Discovery sub-menu under Imprints", async () => {
    const { api } = fakeApi();
    renderAt(api, "/imprints");
    await screen.findByRole("heading", { name: "Imprints" });
    expect(screen.queryByRole("navigation", { name: "Discovery" })).toBeNull();
    expect(screen.getByRole("link", { name: "Imprints" }).className).toContain("active");
  });
});

describe("the imprints page", () => {
  it("lists every imprint with its newest run, PIT and confidence, and names unreadable folders", async () => {
    const { api } = fakeApi({
      imprints: { test2: imprint("test2"), "corp-twin": imprint("corp-twin", { hosts: 42, segments: 3 }) },
      runs: [
        run("disc-new", "test2", "completed", "2026-10-28T14:00:00Z"),
        run("disc-old", "test2", "failed", "2026-10-28T11:55:00Z"),
      ],
      skippedImprints: [{ id: "broken", reason: "no twin config" }],
    });
    renderAt(api, "/imprints");
    const rows = await screen.findAllByTestId("imprint-row");
    expect(rows.length).toBe(2);
    const test2 = rows.find((r) => r.textContent?.startsWith("test2"))!;
    expect(test2.textContent).toContain("disc-new");
    expect(test2.textContent).toContain("completed");
    expect(test2.textContent).toContain("pit-disc-new");
    expect(test2.textContent).not.toContain("disc-old");
    expect(test2.textContent).toContain("0.54");
    const corp = rows.find((r) => r.textContent?.startsWith("corp-twin"))!;
    expect(corp.textContent).toContain("—");
    expect(screen.getByRole("link", { name: "test2" }).getAttribute("href")).toBe("/imprints/test2");
    expect(screen.getByText("broken").parentElement?.textContent).toContain("no twin config");
  });

  it("says when the catalog is empty", async () => {
    const { api } = fakeApi();
    renderAt(api, "/imprints");
    await screen.findByText(/No imprint in the catalog/);
  });
});

describe("deleting an imprint", () => {
  it("removes the row after a confirmation, and keeps it when the API refuses", async () => {
    const { api, state } = fakeApi({ imprints: { test2: imprint("test2"), keep: imprint("keep") } });
    renderAt(api, "/imprints");
    await screen.findAllByTestId("imprint-row");
    fireEvent.click(screen.getByRole("button", { name: "Delete imprint test2" }));
    fireEvent.click(screen.getByRole("button", { name: "Keep" }));
    expect(state.writes).toEqual([]);
    fireEvent.click(screen.getByRole("button", { name: "Delete imprint test2" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm delete test2" }));
    await waitFor(() => expect(screen.queryByRole("link", { name: "test2" })).toBeNull());
    expect(state.writes).toEqual([{ op: "delete-imprint", id: "test2" }]);
    expect(screen.getAllByTestId("imprint-row").length).toBe(1);

    state.refuse.deleteImprint = new ApiError({ kind: "conflict", status: 409, message: "a discovery run for imprint 'keep' is still active; cancel it first", url: "" });
    fireEvent.click(screen.getByRole("button", { name: "Delete imprint keep" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm delete keep" }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("cancel it first");
    expect(screen.getAllByTestId("imprint-row").length).toBe(1);
  });

  it("the imprint page offers Delete imprint and returns to the list once gone", async () => {
    const { api, state } = fakeApi({ imprints: { test2: imprint("test2") } });
    renderAt(api, "/imprints/test2");
    fireEvent.click(await screen.findByRole("button", { name: "Delete imprint" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm delete test2" }));
    await screen.findByText(/No imprint in the catalog/);
    expect(state.writes).toEqual([{ op: "delete-imprint", id: "test2" }]);
  });
});
