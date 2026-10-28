import { fireEvent, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { DiscoveryRun } from "../src/api/types";
import { runProgress } from "../src/model/sources";
import { SPAN, fakeApi } from "./helpers/fakeApi";
import { renderAt } from "./helpers/render";

const FROM = SPAN.last_ts + 1 - 10 * 86400;
const TO = SPAN.last_ts + 1;

function running(over: Partial<DiscoveryRun> = {}): DiscoveryRun {
  return {
    id: "disc-7",
    status: "running",
    plugin: "axquery",
    imprint_id: "corp-apcn-10d",
    stages_done: [],
    stages: ["ingest", "materialize", "cast", "install"],
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
    fireEvent.click(screen.getByRole("button", { name: "Confirm cancel" }));
    await screen.findByText(/^Cancel requested/);
    expect(state.writes).toEqual([{ op: "cancel", id: "disc-7" }]);
    expect(screen.getByText("cancelling")).toBeTruthy();
    // The next poll (2 s) finds the stage dead.
    await screen.findByText(/^Cancelled; the partial bundle/, {}, { timeout: 6000 });
    expect(screen.getByText("cancelled")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Cancel run" })).toBeNull();
  }, 10000);

  it("offers no cancel on a finished run", async () => {
    const { api } = fakeApi({ runs: [running({ status: "completed", stages_done: ["ingest", "materialize", "cast", "install"], pit_id: "pit-1", finished_at: "2026-10-28T15:00:00Z" })] });
    renderAt(api, "/discovery/runs/disc-7");
    await screen.findByTestId("open-imprint");
    expect(screen.queryByRole("button", { name: "Cancel run" })).toBeNull();
    expect(screen.getByRole("progressbar", { name: "Ingest progress" }).getAttribute("aria-valuenow")).toBe("100");
  });
});
