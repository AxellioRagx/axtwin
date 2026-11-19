import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ApiError } from "../src/api/client";
import { fakeApi, tunnelSource } from "./helpers/fakeApi";
import { renderAt } from "./helpers/render";

const FAILED_AT_API = {
  reachable: false,
  error: "axquery /api/info unreachable at http://127.0.0.1:9090 (as seen from fxadmin@corp-apcn-reader.swlab.axellio.dom): [Errno 104] Connection reset by peer",
  first_ts: null,
  last_ts: null,
  vantage: null,
  extra: {},
  stage: "api_http" as const,
  steps: [
    { stage: "ssh_connect" as const, ok: true },
    { stage: "ssh_auth" as const, ok: true },
    { stage: "ssh_forward" as const, ok: true },
    { stage: "api_http" as const, ok: false },
  ],
};

async function fillTunnelSource() {
  await screen.findByLabelText("Plugin");
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "corp-apcn-reader" } });
  fireEvent.change(screen.getByLabelText("Endpoint"), { target: { value: "http://127.0.0.1:9090" } });
  fireEvent.click(screen.getByLabelText("SSH tunnel"));
  fireEvent.change(screen.getByLabelText("SSH host"), { target: { value: "corp-apcn-reader.swlab.axellio.dom" } });
  fireEvent.change(screen.getByLabelText("SSH user"), { target: { value: "fxadmin" } });
  fireEvent.change(screen.getByLabelText("SSH key path"), { target: { value: "/home/op/.ssh/id_ed25519" } });
}

const testButton = () => screen.getByTestId("test-connection") as HTMLButtonElement;

describe("Test connection on the new-source form", () => {
  it("runs the screen's own checks first and does not call the API when they fail", async () => {
    const { api, state } = fakeApi();
    renderAt(api, "/discovery/sources/new");
    await screen.findByLabelText("Plugin");
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "x" } });
    fireEvent.change(screen.getByLabelText("Endpoint"), { target: { value: "10.0.0.1:9090" } });
    fireEvent.click(testButton());
    expect(screen.getByRole("alert").textContent).toContain("the endpoint must be an absolute http(s) URL");
    expect(state.writes).toEqual([]);
    expect(screen.queryByTestId("test-result")).toBeNull();
  });

  it("probes the config as typed, shows it pending with only the button disabled, then the result", async () => {
    let release = () => {};
    const { api, state } = fakeApi();
    state.probeHold = new Promise<void>((r) => (release = r));
    renderAt(api, "/discovery/sources/new");
    await fillTunnelSource();
    fireEvent.click(testButton());
    await waitFor(() => expect(testButton().textContent).toBe("Testing…"));
    expect(testButton().disabled).toBe(true);
    // The form stays editable while the test runs.
    expect((screen.getByLabelText("Endpoint") as HTMLInputElement).disabled).toBe(false);
    expect((screen.getByRole("button", { name: "Save source" }) as HTMLButtonElement).disabled).toBe(false);
    release();
    await screen.findByText(/^Reachable \(/);
    expect(testButton().disabled).toBe(false);
    expect(state.writes).toHaveLength(1);
    const call = state.writes[0]!;
    expect(call.op).toBe("probe-config");
    expect(call.id).toBeUndefined();
    expect(call.body).toMatchObject({
      endpoint: "http://127.0.0.1:9090",
      access: { transport: "ssh_tunnel", ssh: { host: "corp-apcn-reader.swlab.axellio.dom", user: "fxadmin" } },
    });
    // A passing test keeps the plain label, and the result is saved with the source.
    fireEvent.click(screen.getByRole("button", { name: "Save source" }));
    await waitFor(() => expect(state.writes.filter((w) => w.op === "create")).toHaveLength(1));
    const created = state.writes.find((w) => w.op === "create")!.body as Record<string, unknown>;
    expect(created.last_probe).toMatchObject({ reachable: true, probed_at: "2026-11-19T12:00:00Z" });
    expect(created.last_probe).not.toHaveProperty("source_id");
    // The new source's page starts tested, not "Not tested yet".
    await screen.findByText("SSH tunnel via fxadmin@corp-apcn-reader.swlab.axellio.dom:22, no credential");
    expect(screen.queryByText(/^Not tested yet/)).toBeNull();
  });

  it("a failure shows the step that failed and the reason, and the save button says Save anyway", async () => {
    const { api, state } = fakeApi();
    state.probeConfigAnswer = FAILED_AT_API;
    renderAt(api, "/discovery/sources/new");
    await fillTunnelSource();
    fireEvent.click(testButton());
    const steps = await screen.findByTestId("probe-steps");
    expect(steps.textContent).toBe("SSH ✓ · login ✓ · tunnel ✓ · API ✗");
    const alert = screen.getByTestId("probe-failure");
    expect(alert.getAttribute("role")).toBe("alert");
    expect(alert.textContent).toContain("Connection reset by peer");
    expect(alert.textContent).toContain("http://127.0.0.1:9090");
    // Testing never gates saving.
    const save = screen.getByRole("button", { name: "Save anyway" });
    fireEvent.click(save);
    await waitFor(() => expect(state.writes.filter((w) => w.op === "create")).toHaveLength(1));
    const created = state.writes.find((w) => w.op === "create")!.body as Record<string, unknown>;
    expect(created.last_probe).toMatchObject({ reachable: false, stage: "api_http" });
  });

  it("saving without testing still works", async () => {
    const { api, state } = fakeApi();
    renderAt(api, "/discovery/sources/new");
    await fillTunnelSource();
    fireEvent.click(screen.getByRole("button", { name: "Save source" }));
    await waitFor(() => expect(state.writes).toHaveLength(1));
    expect(state.writes[0]!.op).toBe("create");
    expect(state.writes[0]!.body).not.toHaveProperty("last_probe");
  });

  it("a change to the endpoint, plugin or access marks the result out of date; a run default does not", async () => {
    const { api, state } = fakeApi();
    state.probeConfigAnswer = FAILED_AT_API;
    renderAt(api, "/discovery/sources/new");
    await fillTunnelSource();
    fireEvent.click(testButton());
    await screen.findByTestId("probe-steps");
    // Run defaults do not touch reachability.
    fireEvent.change(screen.getByLabelText("Network"), { target: { value: "corp-apcn" } });
    fireEvent.change(screen.getByLabelText("Days to read"), { target: { value: "3" } });
    expect(screen.queryByTestId("test-stale")).toBeNull();
    expect(screen.getByRole("button", { name: "Save anyway" })).toBeTruthy();

    fireEvent.change(screen.getByLabelText("SSH user"), { target: { value: "root" } });
    expect(screen.getByTestId("test-stale").textContent).toBe("Config changed since this test — test again");
    expect(screen.queryByTestId("probe-failure")).toBeNull();
    // An out-of-date failure says nothing about this config: the plain label comes back,
    // and it is not saved as the record's last test.
    fireEvent.click(screen.getByRole("button", { name: "Save source" }));
    await waitFor(() => expect(state.writes.filter((w) => w.op === "create")).toHaveLength(1));
    expect(state.writes.find((w) => w.op === "create")!.body).not.toHaveProperty("last_probe");
  });

  it("changing it back makes the same result current again; the endpoint and access choice count too", async () => {
    const { api, state } = fakeApi();
    state.probeConfigAnswer = FAILED_AT_API;
    renderAt(api, "/discovery/sources/new");
    await fillTunnelSource();
    fireEvent.click(testButton());
    await screen.findByTestId("probe-steps");
    fireEvent.change(screen.getByLabelText("Endpoint"), { target: { value: "http://127.0.0.1:9091" } });
    expect(screen.getByTestId("test-stale")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Endpoint"), { target: { value: "http://127.0.0.1:9090" } });
    expect(screen.queryByTestId("test-stale")).toBeNull();
    fireEvent.click(screen.getByLabelText("User and password"));
    expect(screen.getByTestId("test-stale")).toBeTruthy();
  });

  it("a refused test (e.g. 400) shows the server's reason and no result", async () => {
    const { api, state } = fakeApi();
    state.refuse.probeConfig = new ApiError({
      kind: "bad-request",
      status: 400,
      message: "endpoint must not carry a query string or fragment",
      url: "/api/discovery/sources/probe",
    });
    renderAt(api, "/discovery/sources/new");
    await fillTunnelSource();
    fireEvent.click(testButton());
    const alert = await screen.findByText(/the test could not run/);
    expect(alert.textContent).toContain("endpoint must not carry a query string or fragment");
    expect(screen.queryByTestId("test-result")).toBeNull();
  });
});

describe("Test connection on the edit form", () => {
  it("tests the edited config with the saved secrets behind it", async () => {
    const { api, state } = fakeApi({ sources: [tunnelSource()] });
    renderAt(api, "/discovery/sources/src-1/edit");
    await waitFor(() => expect((screen.getByLabelText("Name") as HTMLInputElement).value).toBe("corp-apcn-reader"));
    fireEvent.click(testButton());
    await screen.findByText(/^Reachable \(/);
    expect(state.writes[0]).toMatchObject({ op: "probe-config", id: "src-1" });
    expect(screen.getByRole("button", { name: "Save changes" })).toBeTruthy();
  });

  it("a failed test relabels Save changes to Save anyway", async () => {
    const { api, state } = fakeApi({ sources: [tunnelSource()] });
    state.probeConfigAnswer = FAILED_AT_API;
    renderAt(api, "/discovery/sources/src-1/edit");
    await waitFor(() => expect((screen.getByLabelText("Name") as HTMLInputElement).value).toBe("corp-apcn-reader"));
    fireEvent.click(testButton());
    await screen.findByTestId("probe-steps");
    fireEvent.click(screen.getByRole("button", { name: "Save anyway" }));
    await waitFor(() => expect(state.writes.filter((w) => w.op === "update")).toHaveLength(1));
  });
});

describe("the source page reads a failed test the same way", () => {
  it("shows the failed step on a saved source's last test", async () => {
    const { api } = fakeApi({
      sources: [tunnelSource({ last_probe: { ...FAILED_AT_API, via: null, probed_at: "2026-11-19T12:00:00Z" } })],
    });
    renderAt(api, "/discovery/sources/src-1");
    expect((await screen.findByTestId("probe-steps")).textContent).toBe("SSH ✓ · login ✓ · tunnel ✓ · API ✗");
  });
});
