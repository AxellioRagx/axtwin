import { fireEvent, screen, waitFor } from "@testing-library/react";
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
    expect(screen.getByText(/2026-09-06 19:30:36 UTC → 2026-10-06 19:30:36 UTC \(30\.0 days\)/)).toBeTruthy();
    // The run form previews the window the server will resolve.
    expect(screen.getByText(/Resolves to 2026-09-26 19:30:37 UTC → 2026-10-06 19:30:37 UTC/)).toBeTruthy();
  });

  it("starts a 10-day run, polls it to completion and opens the imprint", async () => {
    const { api, state } = fakeApi({
      sources: [tunnelSource()],
      imprints: {
        "corp-apcn-10d": {
          id: "corp-apcn-10d", as_of: "2026-10-04T19:00:00Z", hosts: 2481, segments: 61, services: 0, personas: 0,
          mean_confidence: 0.61, windows: 240, topology_segments: [{ cidr: "10.80.20.0/24", hosts: 120 }],
        },
      },
      runSteps: [
        { status: "running", stages_done: ["ingest"], ts_from: SPAN.last_ts + 1 - 10 * 86400, ts_to: SPAN.last_ts + 1, via: "ssh fxadmin@x:22 -> 127.0.0.1:9090 via 127.0.0.1:1" },
        { status: "completed", stages_done: ["ingest", "materialize", "cast", "install"], pit_id: "pit-1", finished_at: "2026-10-27T17:03:18Z" },
      ],
    });
    renderAt(api, "/discovery/sources/src-1");
    await screen.findByRole("button", { name: "Start discovery" });
    fireEvent.change(screen.getByLabelText("Imprint id"), { target: { value: "corp-apcn-10d" } });
    fireEvent.click(screen.getByRole("button", { name: "Start discovery" }));
    await screen.findByText(/^Run disc-/);
    expect(state.writes.at(-1)).toEqual({
      op: "run",
      id: "src-1",
      body: { imprint_id: "corp-apcn-10d", days: 10, network: "corp-apcn", sensors: [] },
    });
    // First poll: running, ingest done, window resolved.
    await waitFor(() => expect(screen.getByText("ingest").className).toBe("done"));
    await screen.findByText(/2026-09-26 19:30:37 UTC → 2026-10-06 19:30:37 UTC \(10\.00 days\)/);
    // Second poll (2 s later): completed, with the imprint on offer.
    const open = await screen.findByTestId("open-imprint", {}, { timeout: 6000 });
    expect(open.textContent).toBe("Open imprint corp-apcn-10d");
    fireEvent.click(open);
    await screen.findByText("Imprint corp-apcn-10d");
    expect(screen.getByText("2,481")).toBeTruthy();
    expect(screen.getByText("10.80.20.0/24")).toBeTruthy();
  }, 10000);

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
      runs: [{ id: "disc-9", status: "failed", plugin: "axquery", imprint_id: "x", stages_done: [], stages: ["ingest", "materialize", "cast", "install"], pit_id: null, error: "ssh tunnel to fxadmin@h:22 exited (255) before the forward came up: Permission denied (publickey).", created_at: "2026-10-27T16:00:00Z", finished_at: "2026-10-27T16:00:05Z", source_id: "src-1", started_at: "2026-10-27T16:00:00Z", ts_from: null, ts_to: null, via: null }],
    });
    renderAt(api, "/discovery/runs/disc-9");
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Permission denied (publickey)");
  });
});
