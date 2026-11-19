import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { DiscoveryRun } from "../src/api/types";
import { AXQUERY, fakeApi, tunnelSource } from "./helpers/fakeApi";
import { renderAt } from "./helpers/render";

function pastRun(over: Partial<DiscoveryRun> = {}): DiscoveryRun {
  return {
    id: "disc-8d560f14", status: "completed", plugin: "axquery", name: "corp-apcn-1h", imprint_id: "corp-apcn-1h",
    stages_done: ["ingest", "bake", "cast", "install"], stages: ["ingest", "bake", "cast", "install"],
    pit_id: "pit-1", error: null, created_at: "2026-11-19T10:00:00Z", finished_at: "2026-11-19T10:05:00Z",
    source_id: "src-1", started_at: "2026-11-19T10:00:01Z", ts_from: 1791311437, ts_to: 1791315037,
    via: null, progress_ts: null, progress_at: null, cancel_requested_at: null,
    options: { network: "corp-from-run", vantage: "axellio-hq/rdr1:12" }, ...over,
  };
}

const field = (label: string) => screen.getByLabelText(label) as HTMLInputElement;
const hint = async () => (await screen.findByTestId("run-prefill-hint")).textContent;

describe("the run form pre-fills from the source's last run, then its legacy defaults, then built-ins", () => {
  it("fills from the last run and names it", async () => {
    const { api, state } = fakeApi({ plugins: [AXQUERY], sources: [tunnelSource()], runs: [pastRun()] });
    renderAt(api, "/discovery/sources/src-1");
    await waitFor(async () => expect(await hint()).toBe("Filled from run disc-8d560f14, the last one from this source."));
    const link = within(screen.getByTestId("run-prefill-hint")).getByRole("link", { name: "disc-8d560f14" });
    expect(link.getAttribute("href")).toBe("/discovery/runs/disc-8d560f14");
    expect(field("network").value).toBe("corp-from-run");
    expect(field("vantage").value).toBe("axellio-hq/rdr1:12");
    expect([field("Amount").value, (screen.getByLabelText("Unit") as HTMLSelectElement).value]).toEqual(["1", "hours"]);
    fireEvent.click(screen.getByRole("button", { name: "Start discovery" }));
    await waitFor(() => expect(state.writes.at(-1)?.op).toBe("run"));
    expect(state.writes.at(-1)!.body).toMatchObject({ days: 1 / 24, network: "corp-from-run", vantage: "axellio-hq/rdr1:12" });
  });

  it("falls back to the source's legacy saved defaults", async () => {
    const { api } = fakeApi({ plugins: [AXQUERY], sources: [tunnelSource({ defaults: { days: 3, network: "corp-apcn", sensors: ["XPress-SW-A"] } })] });
    renderAt(api, "/discovery/sources/src-1");
    await waitFor(async () => expect(await hint()).toMatch(/^Filled from this source's saved run defaults/));
    expect([field("Amount").value, field("network").value, field("Sensors").value]).toEqual(["3", "corp-apcn", "XPress-SW-A"]);
  });

  it("falls back to the built-ins for a source with neither", async () => {
    const { api } = fakeApi({ plugins: [AXQUERY], sources: [tunnelSource({ defaults: {} })] });
    renderAt(api, "/discovery/sources/src-1");
    await waitFor(async () => expect(await hint()).toBe("Built-in defaults: the last 10 days."));
    expect([field("Amount").value, field("network").value, field("Sensors").value]).toEqual(["10", "", ""]);
  });

  it("a run that arrives after the operator typed fills only the untouched fields", async () => {
    let release = () => {};
    const { api, state } = fakeApi({ plugins: [AXQUERY], sources: [tunnelSource({ defaults: {} })], runs: [pastRun()] });
    state.runsHold = new Promise<void>((r) => (release = r));
    renderAt(api, "/discovery/sources/src-1");
    await waitFor(async () => expect(await hint()).toBe("Built-in defaults: the last 10 days."));
    fireEvent.change(field("network"), { target: { value: "typed-net" } });
    fireEvent.change(field("Amount"), { target: { value: "5" } });
    release();
    await waitFor(async () => expect(await hint()).toMatch(/^Filled from run disc-8d560f14/));
    expect(field("network").value).toBe("typed-net");
    expect(field("Amount").value).toBe("5");
    expect((screen.getByLabelText("Unit") as HTMLSelectElement).value).toBe("days");
    // The field the operator never touched does take the run's value.
    expect(field("vantage").value).toBe("axellio-hq/rdr1:12");
  });

  it("only this source's runs count", async () => {
    const { api } = fakeApi({ plugins: [AXQUERY], sources: [tunnelSource({ defaults: {} })], runs: [pastRun({ source_id: "src-other" })] });
    renderAt(api, "/discovery/sources/src-1");
    await waitFor(async () => expect(await hint()).toBe("Built-in defaults: the last 10 days."));
  });
});
