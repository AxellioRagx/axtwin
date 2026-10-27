import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ZEEK, fakeApi } from "./helpers/fakeApi";
import { renderAt } from "./helpers/render";

describe("T0.5 -- every list empty renders, and says why", () => {
  it("no sources", async () => {
    const { api } = fakeApi({ sources: [] });
    renderAt(api, "/discovery");
    await screen.findByText("No discovery sources saved yet. Add one to start.");
  });
  it("no runs", async () => {
    const { api } = fakeApi({ runs: [] });
    renderAt(api, "/discovery/runs");
    await screen.findByText("No discovery run since the API started. Start one from a source.");
  });
  it("no endpoint plugin at all", async () => {
    const { api } = fakeApi({ plugins: [ZEEK] });
    renderAt(api, "/discovery/sources/new");
    await screen.findByText(/No installed discovery plugin reads from a network endpoint/);
    expect((screen.getByRole("button", { name: "Save source" }) as HTMLButtonElement).disabled).toBe(true);
  });
  it("zero plugins is not an error either", async () => {
    const { api } = fakeApi({ plugins: [] });
    renderAt(api, "/discovery/sources/new");
    await screen.findByText(/Plugins installed: none/);
  });
  it("an unknown source id says so", async () => {
    const { api } = fakeApi();
    renderAt(api, "/discovery/sources/src-nope");
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("no discovery source 'src-nope'");
  });
});
