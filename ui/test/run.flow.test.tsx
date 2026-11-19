import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ApiError } from "../src/api/client";
import { AXQUERY, FOO_HTTP, SPAN, fakeApi, tunnelSource } from "./helpers/fakeApi";
import { renderAt } from "./helpers/render";

describe("C1.2 -- test, run, poll, open", () => {
  it("tests the connection and shows the source's own span and census", async () => {
    const { api, state } = fakeApi({ sources: [tunnelSource()] });
    renderAt(api, "/discovery/sources/src-1");
    await screen.findByText("Not tested yet. Testing opens the access path and reads the source's own census.");
    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));
    await screen.findByText(/^Reachable \(/);
    expect(state.writes).toEqual([{ op: "probe", id: "src-1" }]);
    expect(screen.getByText("axellio-hq/corp-apcn-rdr1:12")).toBeTruthy();
    expect(screen.getByText("16.9 G (16,893,421,675)")).toBeTruthy();
    expect(within(screen.getByTestId("probe-panel")).getByText(/2026-09-06 19:30 UTC → 2026-10-06 19:30 UTC \(30\.0 days\)/)).toBeTruthy();
    // The run form previews the range the server will resolve, from the end of the data.
    expect(screen.getByTestId("range-selected").textContent).toMatch(/^Last 10 d of data: 2026-09-26 19:30 UTC → 2026-10-06 19:30 UTC/);
  });

  it("starts a 10-day run, polls it to completion and opens the imprint", async () => {
    const { api, state } = fakeApi({
      sources: [tunnelSource()],
      imprints: [{ pit_id: "pit-1", name: "corp-apcn-10d", derived_from: null, has_assertions: false, twins: [], as_of: 1791313200, entities: 2481, segments: 61, windows: 240 }],
      runSteps: [
        { status: "running", stages_done: ["ingest"], ts_from: SPAN.last_ts + 1 - 10 * 86400, ts_to: SPAN.last_ts + 1, via: "ssh fxadmin@x:22 -> 127.0.0.1:9090 via 127.0.0.1:1" },
        { status: "completed", stages_done: ["ingest", "bake"], pit_id: "pit-1", pit_name: "corp-apcn-10d", finished_at: "2026-10-27T17:03:18Z" },
      ],
    });
    renderAt(api, "/discovery/sources/src-1");
    await screen.findByRole("button", { name: "Start discovery" });
    fireEvent.change(screen.getByLabelText("Imprint name"), { target: { value: "corp-apcn-10d" } });
    fireEvent.click(screen.getByRole("button", { name: "Start discovery" }));
    await screen.findByText(/^Run disc-/);
    expect(state.writes.at(-1)).toEqual({
      op: "run",
      id: "src-1",
      body: { name: "corp-apcn-10d", days: 10, window: 3600, subbuckets: 12 },
    });
    // First poll: running, ingest done, window resolved.
    await waitFor(() => expect(screen.getByText("ingest").className).toBe("done"));
    await screen.findByText(/2026-09-26 19:30 UTC → 2026-10-06 19:30 UTC \(10\.00 days\)/);
    // Second poll (2 s later): completed, with the imprint on offer.
    const open = await screen.findByTestId("open-imprint", {}, { timeout: 6000 });
    expect(open.textContent).toBe("Open imprint corp-apcn-10d");
    fireEvent.click(open);
    await screen.findByRole("heading", { name: "Imprint corp-apcn-10d" });
    expect(screen.getByText(/2,481 \/ 61 \/ 240/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Cast into a twin" }).className).toContain("primary");
  }, 10000);

  it("a quick range is sent as fractional days, re-resolved when the run starts", async () => {
    const { api, state } = fakeApi({ sources: [tunnelSource()] });
    renderAt(api, "/discovery/sources/src-1");
    await screen.findByRole("button", { name: "Start discovery" });
    fireEvent.click(screen.getByRole("button", { name: "Last 6 h of data" }));
    fireEvent.click(screen.getByRole("button", { name: "Start discovery" }));
    await waitFor(() => expect(state.writes.some((w) => w.op === "run")).toBe(true));
    expect(state.writes.find((w) => w.op === "run")?.body).toMatchObject({ days: 0.25 });
  });

  it("the run form is drawn from the plugin's schema, not a UI list", async () => {
    const { api, state } = fakeApi({ plugins: [AXQUERY, FOO_HTTP], sources: [tunnelSource({ plugin: "foo-http", defaults: { days: 2 } })] });
    renderAt(api, "/discovery/sources/src-1");
    await screen.findByRole("button", { name: "Start discovery" });
    expect(screen.getByLabelText("shard")).toBeTruthy();
    expect(screen.queryByLabelText("network")).toBeNull();
    fireEvent.change(screen.getByLabelText("shard"), { target: { value: "3" } });
    fireEvent.click(screen.getByRole("button", { name: "Start discovery" }));
    await waitFor(() => expect(state.writes.some((w) => w.op === "run")).toBe(true));
    expect(state.writes.find((w) => w.op === "run")?.body).toMatchObject({ days: 2, shard: 3 });
  });

  it("interval and burst resolution sit under a collapsed Advanced, defaulted from the probe; no network or vantage anywhere", async () => {
    const probe = { source_id: "src-1", probed_at: "2026-10-27T16:00:00Z", reachable: true, error: null, ...SPAN, vantage: "axellio-hq/corp-apcn-rdr1:12", extra: { window: 900 }, via: null };
    const { api, state } = fakeApi({ sources: [tunnelSource({ last_probe: probe })] });
    renderAt(api, "/discovery/sources/src-1");
    await screen.findByRole("button", { name: "Start discovery" });
    const advanced = screen.getByText(/^Advanced/).closest("details")!;
    expect(advanced.open).toBe(false);
    const interval = screen.getByLabelText("Measurement interval") as HTMLSelectElement;
    const burst = screen.getByLabelText("Burst resolution") as HTMLSelectElement;
    expect(advanced.contains(interval) && advanced.contains(burst)).toBe(true);
    expect(interval.selectedOptions[0]!.textContent).toBe("15 min");
    expect(burst.selectedOptions[0]!.textContent).toBe("75 s");
    for (const k of ["network", "vantage", "Sensors"]) expect(screen.queryByLabelText(k)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Start discovery" }));
    await waitFor(() => expect(state.writes.some((w) => w.op === "run")).toBe(true));
    const body = state.writes.find((w) => w.op === "run")!.body as Record<string, unknown>;
    expect(body).toMatchObject({ window: 900, subbuckets: 12 });
    for (const k of ["network", "vantage", "sensors"]) expect(body).not.toHaveProperty(k);
  });

  it("a source page deletes the source behind a confirmation", async () => {
    const { api, state } = fakeApi({ sources: [tunnelSource()] });
    renderAt(api, "/discovery/sources/src-1");
    fireEvent.click(await screen.findByRole("button", { name: "Delete source" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm delete corp-apcn-reader" }));
    await screen.findByText(/No discovery sources saved yet/);
    expect(state.writes).toEqual([{ op: "delete", id: "src-1" }]);
  });

  it("a refused run shows the server's reason and stays on the form", async () => {
    const { api, state } = fakeApi({ sources: [tunnelSource()] });
    state.refuse.run = new ApiError({ kind: "conflict", status: 409, message: "imprint 'corp-apcn-10d' already exists in /catalog/twins; a discovery run never overwrites a bundle -- choose another id", url: "" });
    renderAt(api, "/discovery/sources/src-1");
    await screen.findByRole("button", { name: "Start discovery" });
    fireEvent.click(screen.getByRole("button", { name: "Start discovery" }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("never overwrites a bundle");
    expect(screen.getByRole("button", { name: "Start discovery" })).toBeTruthy();
  });

  it("a failed run shows its error", async () => {
    const { api } = fakeApi({
      sources: [tunnelSource()],
      runs: [{ id: "disc-9", status: "failed", plugin: "axquery", name: "x", imprint_id: "x", stages_done: [], stages: ["ingest", "bake"], pit_id: null, error: "ssh tunnel to fxadmin@h:22 exited (255) before the forward came up: Permission denied (publickey).", created_at: "2026-10-27T16:00:00Z", finished_at: "2026-10-27T16:00:05Z", source_id: "src-1", started_at: "2026-10-27T16:00:00Z", ts_from: null, ts_to: null, via: null, progress_ts: null, progress_at: null, cancel_requested_at: null }],
    });
    renderAt(api, "/discovery/runs/disc-9");
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Permission denied (publickey)");
  });
});
