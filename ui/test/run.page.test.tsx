import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { DiscoveryRun } from "../src/api/types";
import { runProgress } from "../src/model/sources";
import { ApiError } from "../src/api/client";
import { SPAN, fakeApi } from "./helpers/fakeApi";
import { renderAt } from "./helpers/render";

const FROM = SPAN.last_ts + 1 - 10 * 86400;
const TO = SPAN.last_ts + 1;

function running(over: Partial<DiscoveryRun> = {}): DiscoveryRun {
  return {
    id: "disc-7",
    status: "running",
    plugin: "axquery",
    name: "corp-apcn-10d",
    imprint_id: "corp-apcn-10d",
    stages_done: [],
    stages: ["ingest", "bake"],
    pit_id: null,
    error: null,
    created_at: "2026-10-28T10:57:33Z",
    finished_at: null,
    source_id: "src-1",
    started_at: "2026-10-28T10:57:33Z",
    ts_from: FROM,
    ts_to: TO,
    via: "ssh fxadmin@h:22 -> 127.0.0.1:9090 via 127.0.0.1:1",
    progress_ts: FROM + 2.5 * 86400,
    progress_at: "2026-10-28T12:00:00Z",
    cancel_requested_at: null,
    ...over,
  };
}

describe("run progress", () => {
  it("is the covered share of the window, with the remainder at the rate so far", () => {
    const now = Date.parse("2026-10-28T11:57:33Z"); // one hour in, 2.5 days covered
    const p = runProgress(running(), now)!;
    expect(p.fraction).toBeCloseTo(0.25, 5);
    expect(p.reached).toBe(FROM + 2.5 * 86400);
    expect(p.remainingS).toBeCloseTo(3 * 3600, 0);
    expect(p.ingestDone).toBe(false);
  });
  it("is full once ingest is done, empty before the first window, null without bounds", () => {
    expect(runProgress(running({ stages_done: ["ingest"] }), 0)).toMatchObject({ fraction: 1, ingestDone: true, remainingS: null });
    expect(runProgress(running({ progress_ts: null }), 0)).toMatchObject({ fraction: 0, reached: null, remainingS: null });
    expect(runProgress(running({ ts_from: null }), 0)).toBeNull();
    // An API from before progress existed omits the field; that is "no window yet", not NaN.
    const legacy = running();
    delete (legacy as Partial<DiscoveryRun>).progress_ts;
    expect(runProgress(legacy, 0)).toMatchObject({ fraction: 0, reached: null });
  });
});

describe("the run page", () => {
  it("draws the window as a bar with the reached point and the bounds", async () => {
    const { api } = fakeApi({ runs: [running()] });
    renderAt(api, "/discovery/runs/disc-7");
    const bar = await screen.findByRole("progressbar", { name: "Ingest progress" });
    expect(bar.getAttribute("aria-valuenow")).toBe("25");
    expect(screen.getByText(/^Ingest has reached 2026-09-29 07:30:37 UTC: 2\.50 of 10\.00 days \(25%\)/)).toBeTruthy();
    expect(screen.getByText("2026-09-26 19:30:37 UTC")).toBeTruthy();
    expect(screen.getByText("2026-10-06 19:30:37 UTC")).toBeTruthy();
  });

  it("cancels after a confirmation and shows the run stopping, then cancelled", async () => {
    const { api, state } = fakeApi({ runs: [running()] });
    renderAt(api, "/discovery/runs/disc-7");
    fireEvent.click(await screen.findByRole("button", { name: "Cancel run" }));
    expect(screen.getByRole("button", { name: "Keep running" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Confirm cancel disc-7" }));
    await screen.findByText(/^Cancel requested/);
    expect(state.writes).toEqual([{ op: "cancel", id: "disc-7" }]);
    expect(screen.getByText("cancelling")).toBeTruthy();
    // The next poll (2 s) finds the stage dead.
    await screen.findByText(/^Cancelled; the partial bundle/, {}, { timeout: 6000 });
    expect(screen.getByText("cancelled")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Cancel run" })).toBeNull();
  }, 10000);

  it("marks the stage a failed run died in red, the unreached ones plain", async () => {
    const { api } = fakeApi({ runs: [running({ status: "failed", stages_done: ["ingest"], error: "bake failed (exit 1): boom", finished_at: "2026-10-28T12:00:00Z" })] });
    renderAt(api, "/discovery/runs/disc-7");
    await screen.findByRole("alert");
    expect(screen.getByText("ingest").className).toBe("done");
    expect(screen.getByText("bake").className).toBe("failed");
  });

  it("a completed run whose imprint is gone says so instead of linking to nothing", async () => {
    const { api } = fakeApi({ runs: [running({ status: "completed", stages_done: ["ingest", "bake"], pit_id: "pit-1", pit_name: "corp-apcn-10d", finished_at: "2026-10-28T15:00:00Z" })] });
    renderAt(api, "/discovery/runs/disc-7");
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Imprint corp-apcn-10d is no longer in the store");
    expect(screen.queryByTestId("open-imprint")).toBeNull();
  });

  it("offers no cancel on a finished run", async () => {
    const { api } = fakeApi({
      runs: [running({ status: "completed", stages_done: ["ingest", "bake"], pit_id: "pit-1", finished_at: "2026-10-28T15:00:00Z" })],
      imprints: [{ pit_id: "pit-1", name: "corp-apcn-10d", derived_from: null, has_assertions: false, twins: [] }],
    });
    renderAt(api, "/discovery/runs/disc-7");
    await screen.findByTestId("open-imprint");
    expect(screen.queryByRole("button", { name: "Cancel run" })).toBeNull();
    expect(screen.getByRole("progressbar", { name: "Ingest progress" }).getAttribute("aria-valuenow")).toBe("100");
  });
});

describe("the run's one write", () => {
  const done = () => running({ id: "disc-9", status: "completed", stages_done: ["ingest", "bake"], pit_id: "pit-1", finished_at: "2026-10-28T15:00:00Z" });

  it("a finished run offers Delete run on its page and goes back to the list once deleted", async () => {
    const { api, state } = fakeApi({ runs: [done()] });
    renderAt(api, "/discovery/runs/disc-9");
    fireEvent.click(await screen.findByRole("button", { name: "Delete run" }));
    expect(screen.queryByRole("button", { name: "Cancel run" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Confirm delete disc-9" }));
    await screen.findByRole("heading", { name: "Discovery runs" });
    expect(state.writes).toEqual([{ op: "delete-run", id: "disc-9" }]);
    expect(state.runs).toEqual([]);
  });

  it("the table offers × on an active run and nothing destructive on a finished one", async () => {
    const { api, state } = fakeApi({ runs: [running(), done()] });
    renderAt(api, "/discovery/runs");
    expect((await screen.findAllByTestId("run-row")).length).toBe(2);
    expect(screen.queryByRole("button", { name: "Delete run disc-9" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Cancel run disc-7" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm cancel disc-7" }));
    await screen.findByText("cancelling");
    expect(state.writes).toEqual([{ op: "cancel", id: "disc-7" }]);
    expect(screen.queryByRole("button", { name: "Cancel run disc-7" })).toBeNull();
  });

  it("a refused delete is shown on the run page, which stays", async () => {
    const { api } = fakeApi({ runs: [done()], refuse: { delete: new ApiError({ kind: "unauthorized", status: 401, message: "unauthorized", url: "" }) } });
    renderAt(api, "/discovery/runs/disc-9");
    fireEvent.click(await screen.findByRole("button", { name: "Delete run" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm delete disc-9" }));
    await waitFor(() => expect(screen.getAllByRole("alert").some((a) => a.textContent?.includes("could not delete"))).toBe(true));
  });
});


describe("the PIT's name", () => {
  it("shows the name beside the id once renamed, and the rename reaches the API", async () => {
    const { api, state } = fakeApi({
      runs: [running({ status: "completed", stages_done: ["ingest", "bake"], pit_id: "pit-1", pit_name: "pit-1", finished_at: "2026-10-28T15:00:00Z" })],
      imprints: [{ pit_id: "pit-1", name: "pit-1", derived_from: null, has_assertions: false, twins: [] }],
    });
    renderAt(api, "/discovery/runs/disc-7");
    fireEvent.click(await screen.findByRole("button", { name: "Rename PIT" }));
    const box = screen.getByRole("textbox", { name: "PIT name" }) as HTMLInputElement;
    expect(box.value).toBe("pit-1");
    fireEvent.change(box, { target: { value: "day one" } });
    fireEvent.click(screen.getByRole("button", { name: "Save name" }));
    await screen.findByRole("button", { name: "Rename PIT" });
    expect(state.writes).toEqual([{ op: "rename-imprint", id: "pit-1", body: { name: "day one" } }]);
    expect(screen.getByText("day one", { selector: "strong" })).toBeTruthy();
    expect(screen.getByText("pit-1").tagName).toBe("CODE");
    expect(screen.getByText(/^Completed; imprint/).textContent).toContain("day one");
  });

  it("keeps the old name and shows the refusal when the API says no", async () => {
    const { api } = fakeApi({
      runs: [running({ status: "completed", stages_done: ["ingest", "bake"], pit_id: "pit-1", pit_name: "pit-1", finished_at: "2026-10-28T15:00:00Z" })],
      imprints: [{ pit_id: "pit-1", name: "pit-1", derived_from: null, has_assertions: false, twins: [] }],
      refuse: { rename: new ApiError({ kind: "conflict", status: 409, message: "'taken' is already the name or id of PIT 'pit-2'", url: "" }) },
    });
    renderAt(api, "/discovery/runs/disc-7");
    fireEvent.click(await screen.findByRole("button", { name: "Rename PIT" }));
    fireEvent.change(screen.getByRole("textbox", { name: "PIT name" }), { target: { value: "taken" } });
    fireEvent.click(screen.getByRole("button", { name: "Save name" }));
    await screen.findByText(/could not rename: .*already the name or id/);
    expect(screen.getByRole("textbox", { name: "PIT name" })).toBeTruthy();
  });
});
