import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ApiError } from "../src/api/client";
import { AXQUERY, FOO_HTTP, ZEEK, fakeApi } from "./helpers/fakeApi";
import { renderAt } from "./helpers/render";

describe("C1.1 -- one form per installed endpoint plugin, from the schema", () => {
  it("lists every http plugin the API reports and none of the path ones", async () => {
    const { api } = fakeApi({ plugins: [AXQUERY, ZEEK, FOO_HTTP] });
    renderAt(api, "/discovery/sources/new");
    const select = (await screen.findByLabelText("Plugin")) as HTMLSelectElement;
    const names = within(select).getAllByRole("option").map((o) => o.textContent);
    expect(names).toEqual(["axquery", "foo-http"]);
    // The plugin's own wording for its source, not a string in the UI.
    await screen.findByText("an axquery instance, addressed as 'axquery:<base-url>'");
    fireEvent.change(select, { target: { value: "foo-http" } });
    await screen.findByText("a foo collector, addressed as 'foo-http:<base-url>'");
  });

  it("the access choice reveals exactly the fields that mode needs", async () => {
    const { api } = fakeApi();
    renderAt(api, "/discovery/sources/new");
    await screen.findByLabelText("Plugin");
    expect(screen.queryByLabelText("SSH host")).toBeNull();
    fireEvent.click(screen.getByLabelText("SSH tunnel"));
    expect(screen.getByLabelText("SSH host")).toBeTruthy();
    expect(screen.getByLabelText("SSH key path")).toBeTruthy();
    expect(screen.queryByLabelText("Password")).toBeNull();
    fireEvent.change(screen.getByLabelText("API credential behind the tunnel"), { target: { value: "basic" } });
    expect(screen.getByLabelText("Password")).toBeTruthy();
    fireEvent.click(screen.getByLabelText("API key"));
    expect(screen.queryByLabelText("SSH host")).toBeNull();
    expect(screen.getByLabelText("API key", { selector: "input#src-apikey" })).toBeTruthy();
  });

  it("saves a tunnel source with the body the API stores, and no secret", async () => {
    const { api, state } = fakeApi();
    renderAt(api, "/discovery/sources/new");
    await screen.findByLabelText("Plugin");
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "corp-apcn-reader" } });
    fireEvent.change(screen.getByLabelText("Endpoint"), { target: { value: "http://127.0.0.1:9090" } });
    fireEvent.click(screen.getByLabelText("SSH tunnel"));
    fireEvent.change(screen.getByLabelText("SSH host"), { target: { value: "corp-apcn-reader.swlab.axellio.dom" } });
    fireEvent.change(screen.getByLabelText("SSH user"), { target: { value: "fxadmin" } });
    fireEvent.change(screen.getByLabelText("SSH key path"), { target: { value: "/home/op/.ssh/id_ed25519" } });
    fireEvent.change(screen.getByLabelText("Days to read"), { target: { value: "10" } });
    fireEvent.change(screen.getByLabelText("Network"), { target: { value: "corp-apcn" } });
    fireEvent.click(screen.getByRole("button", { name: "Save source" }));
    await waitFor(() => expect(state.writes).toHaveLength(1));
    expect(state.writes[0]).toEqual({
      op: "create",
      body: {
        name: "corp-apcn-reader",
        plugin: "axquery",
        endpoint: "http://127.0.0.1:9090",
        access: {
          transport: "ssh_tunnel",
          auth: "none",
          user: null,
          ssh: { host: "corp-apcn-reader.swlab.axellio.dom", user: "fxadmin", key_path: "/home/op/.ssh/id_ed25519", port: 22 },
        },
        defaults: { days: 10, network: "corp-apcn" },
      },
    });
    // Lands on the new source's page.
    await screen.findByText("SSH tunnel via fxadmin@corp-apcn-reader.swlab.axellio.dom:22, no credential");
  });
});

describe("C1.3 -- an invalid source shows the API's reason", () => {
  it("shows what the screen itself can tell, before asking", async () => {
    const { api, state } = fakeApi();
    renderAt(api, "/discovery/sources/new");
    await screen.findByLabelText("Plugin");
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "x" } });
    fireEvent.change(screen.getByLabelText("Endpoint"), { target: { value: "10.0.0.1:9090" } });
    fireEvent.click(screen.getByRole("button", { name: "Save source" }));
    expect(screen.getByRole("alert").textContent).toContain("the endpoint must be an absolute http(s) URL");
    expect(state.writes).toEqual([]);
  });

  it("shows the server's own refusal, verbatim", async () => {
    const { api, state } = fakeApi();
    state.refuse.create = new ApiError({
      kind: "conflict",
      status: 409,
      message: "a discovery source named 'corp-apcn-reader' already exists",
      url: "",
    });
    renderAt(api, "/discovery/sources/new");
    await screen.findByLabelText("Plugin");
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "corp-apcn-reader" } });
    fireEvent.change(screen.getByLabelText("Endpoint"), { target: { value: "http://127.0.0.1:9090" } });
    fireEvent.click(screen.getByRole("button", { name: "Save source" }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe("the server refused the source: a discovery source named 'corp-apcn-reader' already exists");
  });
});
