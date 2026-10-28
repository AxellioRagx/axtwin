import { fireEvent, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { ApiError, setWriteToken } from "../src/api/client";
import { fakeApi, tunnelSource } from "./helpers/fakeApi";
import { renderAt } from "./helpers/render";

afterEach(() => setWriteToken(null));

describe("the source page after a test", () => {
  it("reads the census as figures and the sites as a table, not JSON", async () => {
    const { api } = fakeApi({ sources: [tunnelSource()] });
    renderAt(api, "/discovery/sources/src-1");
    fireEvent.click(await screen.findByRole("button", { name: "Test connection" }));
    await screen.findByText(/^Reachable \(/);
    expect(screen.getByText("16.9 G (16,893,421,675)")).toBeTruthy();
    expect(screen.getByText("15.5 TB (15,494,524,704,945)")).toBeTruthy();
    expect(screen.getByText("Bytes")).toBeTruthy();
    const sites = screen.getByRole("table", { name: "Sites" });
    expect(within(sites).getByText("corp-apcn-rdr1")).toBeTruthy();
    expect(within(sites).getByText("12")).toBeTruthy();
    // First/Last are epoch seconds: the span above already gives the UTC reading.
    expect(within(sites).getByText("1788723036")).toBeTruthy();
    expect(within(sites).getByText("1791315036")).toBeTruthy();
    expect(screen.queryByText(/"systems"/)).toBeNull();
    // The access line carries the credential state; there is no second "Credential" row.
    expect(screen.getByText("SSH tunnel via fxadmin@corp-apcn-reader.swlab.axellio.dom:22, no credential")).toBeTruthy();
    expect(screen.queryByText("Credential")).toBeNull();
  });
});

describe("a refused write names the token that is missing", () => {
  it("says the console's write token is unset, not the source's credential", async () => {
    const { api, state } = fakeApi({ sources: [tunnelSource()] });
    state.refuse.probe = new ApiError({ kind: "unauthorized", status: 401, message: "missing or invalid API token", url: "/api/x" });
    renderAt(api, "/discovery/sources/src-1");
    fireEvent.click(await screen.findByRole("button", { name: "Test connection" }));
    const line = await screen.findByRole("alert");
    expect(line.textContent).toContain("missing or invalid API token");
    expect(line.textContent).toContain("no write token is set in the header");
    expect(line.textContent).toContain("not the source's credential");
  });
  it("says the token was refused when one is set", async () => {
    setWriteToken("wrong");
    const { api, state } = fakeApi({ sources: [tunnelSource()] });
    state.refuse.probe = new ApiError({ kind: "unauthorized", status: 401, message: "missing or invalid API token", url: "/api/x" });
    renderAt(api, "/discovery/sources/src-1");
    fireEvent.click(await screen.findByRole("button", { name: "Test connection" }));
    const line = await screen.findByRole("alert");
    expect(line.textContent).toContain("the write token in the header was refused");
  });
});
