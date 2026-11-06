/**
 * The smaller findings of the 2026-11-05 canvas review: a rename re-keys the
 * selection, Save-As carries the layout draft to the new twin, a new segment
 * takes the /24 after the highest in use, an authored chip's card has no
 * evidence row, and the twin page shows what stands on the twin and no
 * discovery facts for an authored one.
 */
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { DeploySummary } from "../src/api/types";
import { nextFreeSlash24 } from "../src/features/edit-canvas/edit/allocate";
import { DEPLOY, commit, enabled, host, item, panel, seeded, segment, segments } from "./helpers/canvasFixtures";
import { renderAt } from "./helpers/render";

afterEach(() => {
  localStorage.clear();
  sessionStorage.clear();
});

describe("rename, Save-As, the allocator, the card", () => {
  it("renaming the selected node group or segment keeps it selected under its new name", async () => {
    const { api } = seeded();
    renderAt(api, "/twins/corp-auth/canvas");
    await screen.findAllByTestId("canvas-host");
    fireEvent.click(host("ws01"));
    commit(within(await panel().findByTestId("node-panel")).getByLabelText("Node name"), "finance-ws");
    await waitFor(() => expect(host("finance-ws")).toBeTruthy());
    const np = panel().getByTestId("node-panel");
    expect(np.textContent).toContain("finance-ws");
    expect(np.textContent).not.toContain("is not in the document");
    expect((within(np).getByLabelText("Node name") as HTMLInputElement).value).toBe("finance-ws");
    fireEvent.click(segment("net-dmz"));
    commit(within(await panel().findByTestId("segment-panel")).getByLabelText("Segment name"), "dmz");
    await waitFor(() => expect(segments()).toEqual(["dmz", "net-lan"]));
    const sp = panel().getByTestId("segment-panel");
    expect(sp.textContent).not.toContain("is not in the document");
    expect((within(sp).getByLabelText("Segment name") as HTMLInputElement).value).toBe("dmz");
  });

  it("Save-As writes the layout as it stands -- saved pins and the unsaved draft -- to the new twin, and says so", async () => {
    const { api, state } = seeded();
    renderAt(api, "/twins/corp-auth/canvas");
    await screen.findAllByTestId("canvas-host");
    // An unsaved move: a pin through the canvas's own drag handler is a React Flow gesture jsdom
    // cannot drive, so the draft is made through an annotation, which counts the same way.
    fireEvent.click(screen.getByTestId("rail-annotate"));
    fireEvent.click(within(screen.getByTestId("popover-annotate")).getByRole("button", { name: "text" }));
    await waitFor(() => expect(screen.getByTestId("layout-dirty").textContent).toContain("1 unsaved change"));
    fireEvent.change(screen.getByLabelText("Save as id"), { target: { value: "corp-copy" } });
    fireEvent.click(await enabled("Save as"));
    await waitFor(() => expect(state.writes.some((w) => w.op === "create-twin-config" && w.id === "corp-copy")).toBe(true));
    const put = state.writes.find((w) => w.op === "put-layout" && w.id === "corp-copy");
    expect(put).toBeTruthy();
    expect((put!.body as { annotations: unknown[] }).annotations.length).toBe(1);
    expect((put!.body as { imprint: string }).imprint).toBe("corp-copy");
    await screen.findByText(/saved as corp-copy, layout draft carried over/);
    // The new twin opens with the carried annotation and no draft to lose.
    await waitFor(() => expect(screen.getByTestId("mode").textContent).toContain("edit mode · undeployed"));
    await waitFor(() => expect(screen.queryByTestId("layout-dirty")).toBeNull());
  });

  it("a new segment takes the /24 after the highest in use under the prefix, then falls back to the first free", () => {
    expect(nextFreeSlash24("10.70", ["10.70.10.0/24", "10.70.20.0/24"], { after: true })).toBe("10.70.21.0/24");
    expect(nextFreeSlash24("10.70", ["10.70.10.0/24", "10.70.20.0/24"])).toBe("10.70.0.0/24");
    expect(nextFreeSlash24("10.70", ["10.70.255.0/24"], { after: true })).toBe("10.70.0.0/24");
    expect(nextFreeSlash24("10.70", ["10.70.0.0/16"], { after: true })).toBeNull();
    expect(nextFreeSlash24("10.70", [], { after: true })).toBe("10.70.0.0/24");
  });

  it("on the canvas: segment-3 lands after .1 and .0 are in play, not on the site's network address", async () => {
    const { api } = seeded();
    renderAt(api, "/twins/corp-auth/canvas");
    await screen.findAllByTestId("canvas-host");
    fireEvent.click(item("segment", "segment"));
    await waitFor(() => expect(segments()).toEqual(["net-dmz", "net-lan", "segment-3"]));
    expect(segment("segment-3").dataset["cidr"]).toBe("10.1.2.0/24");
  });

  it("an authored chip's hover card names no evidence (there is none to name)", async () => {
    const { api } = seeded();
    renderAt(api, "/twins/corp-auth/canvas");
    await screen.findAllByTestId("canvas-host");
    fireEvent.mouseEnter(host("ws01"));
    const card = await screen.findByTestId("canvas-hover-card");
    expect(card.textContent).toContain("ws01");
    expect(card.textContent).not.toContain("Evidence");
    expect(card.textContent).not.toContain("no evidence");
  });
});

describe("the twin page", () => {
  const TFC_FAILED: DeploySummary = { deploy_id: "tfc-dep-1-aa", scenario_id: "tfc-dep-1", status: "failed", started_at: 1700000200, updated_at: 1700000300, tier: "tfc", target: "dev45" };

  it("shows the standing deploy with every tier's state, a failed one marked, and no discovery facts for an authored twin", async () => {
    const { api } = seeded({ deploys: [DEPLOY, TFC_FAILED] });
    renderAt(api, "/twins/corp-live");
    const summary = await screen.findByTestId("twin-summary");
    expect(summary.textContent).toContain("authored on the canvas, not discovered");
    expect(summary.textContent).not.toContain("As of");
    expect(summary.textContent).not.toContain("Mean confidence");
    const state = within(summary).getByTestId("deploy-state");
    expect(state.textContent).toContain("dep-1");
    expect(within(state).getByTestId("tier-te").textContent).toBe("te completed");
    expect(within(state).getByTestId("tier-tfc").textContent).toContain("tfc failed");
    expect(within(state).getByTestId("tier-tfc").className).toContain("error");
    expect(state.textContent).toContain("on dev45");
  });

  it("an undeployed twin says so; a cast twin keeps its discovery facts", async () => {
    const { api } = seeded();
    renderAt(api, "/twins/corp-auth");
    const summary = await screen.findByTestId("twin-summary");
    expect(within(summary).getByTestId("deploy-state").textContent).toContain("none: the twin is in the catalog, not deployed");
    const cast = seeded();
    renderAt(cast.api, "/twins/corp-cast");
    await waitFor(() => expect(screen.getAllByTestId("twin-summary").length).toBe(2));
    const second = screen.getAllByTestId("twin-summary")[1]!;
    expect(second.textContent).toContain("cast from pit-1");
    expect(second.textContent).toContain("As of");
    expect(second.textContent).toContain("Mean confidence");
  });
});
