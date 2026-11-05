import { fireEvent, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ApiError } from "../src/api/client";
import { fakeApi, tunnelSource } from "./helpers/fakeApi";
import { renderAt } from "./helpers/render";

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

describe("a refused write is sign-in or read-only, never a credential field (C1.6, A8)", () => {
  it("has no credential field and shows who the API says the caller is", async () => {
    const { api } = fakeApi({ sources: [tunnelSource()], whoami: { user: "ann", can_write: true, mode: "proxy", signin_url: "" } });
    renderAt(api, "/discovery/sources/src-1");
    await screen.findByRole("button", { name: "Test connection" });
    expect(screen.queryByLabelText(/token/i)).toBeNull();
    expect(screen.queryByPlaceholderText(/token/i)).toBeNull();
    const who = await screen.findByTestId("whoami");
    expect(who.textContent).toContain("ann");
    expect(within(who).queryByText("read-only")).toBeNull();
  });
  it("says sign in required on a 401, linking the served sign-in URL with this page as the return", async () => {
    const { api, state } = fakeApi({ sources: [tunnelSource()], whoami: { user: null, can_write: false, mode: "proxy", signin_url: "/oauth2/start?rd=" } });
    state.refuse.probe = new ApiError({ kind: "unauthorized", status: 401, message: "sign in required: no identity header from a trusted proxy", url: "/api/x" });
    renderAt(api, "/discovery/sources/src-1");
    // The header already says so, from whoami, before any write.
    const who = await screen.findByTestId("whoami");
    expect(within(who).getByText("read-only")).toBeTruthy();
    expect((within(who).getByRole("link", { name: "Sign in" }) as HTMLAnchorElement).getAttribute("href")).toMatch(/^\/oauth2\/start\?rd=/);
    fireEvent.click(await screen.findByRole("button", { name: "Test connection" }));
    const line = await screen.findByRole("alert");
    expect(line.textContent).toContain("sign in required");
    expect(line.textContent).toContain("no identity header from a trusted proxy");
    expect(line.textContent).not.toMatch(/write token/i);
    const link = within(line).getByRole("link", { name: "Sign in" }) as HTMLAnchorElement;
    expect(link.getAttribute("href")).toMatch(/^\/oauth2\/start\?rd=http/);
  });
  it("says sign in required without a link when the deployment serves no sign-in URL", async () => {
    const { api, state } = fakeApi({ sources: [tunnelSource()], whoami: { user: null, can_write: false, mode: "token", signin_url: "" } });
    state.refuse.probe = new ApiError({ kind: "unauthorized", status: 401, message: "missing or invalid API token", url: "/api/x" });
    renderAt(api, "/discovery/sources/src-1");
    fireEvent.click(await screen.findByRole("button", { name: "Test connection" }));
    const line = await screen.findByRole("alert");
    expect(line.textContent).toContain("sign in required: missing or invalid API token");
    expect(within(line).queryByRole("link")).toBeNull();
  });
  it("says read-only for this account on a 403", async () => {
    const { api, state } = fakeApi({ sources: [tunnelSource()] });
    state.refuse.probe = new ApiError({ kind: "forbidden", status: 403, message: "forbidden", url: "/api/x" });
    renderAt(api, "/discovery/sources/src-1");
    fireEvent.click(await screen.findByRole("button", { name: "Test connection" }));
    const line = await screen.findByRole("alert");
    expect(line.textContent).toContain("read-only for this account");
    expect(within(line).queryByRole("link")).toBeNull();
  });
  it("keeps a source's own credential failure in the server's words", async () => {
    const { api, state } = fakeApi({ sources: [tunnelSource()] });
    state.refuse.probe = new ApiError({ kind: "bad-request", status: 400, message: "axquery refused the credential: 401 from http://127.0.0.1:9090", url: "/api/x" });
    renderAt(api, "/discovery/sources/src-1");
    fireEvent.click(await screen.findByRole("button", { name: "Test connection" }));
    const line = await screen.findByRole("alert");
    expect(line.textContent).toContain("axquery refused the credential");
    expect(line.textContent).not.toContain("sign in required");
    expect(line.textContent).not.toContain("read-only for this account");
  });
});
