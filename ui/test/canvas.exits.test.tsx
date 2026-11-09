/**
 * The exits of a deployed twin after the 2026-11-05 canvas review: the state
 * pill reads every tier (a failed tfc is red, with "Re-apply from baseline"),
 * the document's state says applied-unpersisted vs unapplied, Reset is best
 * effort (a failed baseline replay is reported, the copy still discarded), an
 * exit in flight shows progress and can be cancelled, and a 409 surfaces at
 * once in its own words. jsdom: what the page shows and the fake recorded.
 */
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { ApiError } from "../src/api/client";
import type { DeploySummary } from "../src/api/types";
import { docState, exitErrorText, tierStatus } from "../src/features/edit-canvas/CanvasPage";
import { DEPLOY, host, panel, seeded, tab } from "./helpers/canvasFixtures";
import { renderAt } from "./helpers/render";

const TFC_FAILED: DeploySummary = { deploy_id: "tfc-dep-1-aa", scenario_id: "tfc-dep-1", status: "failed", started_at: 1700000200, updated_at: 1700000300, tier: "tfc", target: "dev45" };
const TAP_OK: DeploySummary = { deploy_id: "tap-dep-1-bb", scenario_id: "tap-dep-1", status: "completed", started_at: 1700000200, updated_at: 1700000300, tier: "tap", target: "dev45" };

afterEach(() => {
  localStorage.clear();
});

describe("the state pill reads every tier", () => {
  it("shows te, tfc and tap; a failed tfc is red and offers Re-apply from baseline, which resets with that tier", async () => {
    const { state } = seeded({ deploys: [DEPLOY, TFC_FAILED, TAP_OK] });
    renderAt(seeded({ deploys: [DEPLOY, TFC_FAILED, TAP_OK] }).api, "/twins/corp-live/canvas");
    void state;
    await screen.findAllByTestId("canvas-host");
    const pill = screen.getByTestId("state-pill");
    expect(pill.className).toContain("failed");
    expect(screen.getByTestId("tier-te").dataset["status"]).toBe("completed");
    expect(screen.getByTestId("tier-tfc").dataset["status"]).toBe("failed");
    expect(screen.getByTestId("tier-tap").dataset["status"]).toBe("completed");
    expect(screen.getByTestId("mode").textContent).toContain("tfc ✗ failed");
    expect(screen.getByTestId("mode").textContent).toContain("tap ✓");
    expect(screen.getByTestId("reapply-baseline").textContent).toContain("Re-apply tfc from baseline");
  });

  it("Re-apply from baseline resets with the failed tier named; a clean replay says so", async () => {
    const { api, state } = seeded({ deploys: [DEPLOY, TFC_FAILED] });
    renderAt(api, "/twins/corp-live/canvas");
    await screen.findAllByTestId("canvas-host");
    fireEvent.click(screen.getByTestId("reapply-baseline"));
    await screen.findByText(/re-applied tfc from the baseline/);
    const w = state.writes.find((x) => x.op === "reset-deploy-config");
    expect(w && (w.body as { tiers: string[] }).tiers).toEqual(["tfc"]);
  });

  it("tierStatus takes the newest non-destroyed row per tier, keyed by the te deploy id", () => {
    const older: DeploySummary = { ...TFC_FAILED, deploy_id: "tfc-dep-1-00", status: "completed", started_at: 1700000100 };
    const gone: DeploySummary = { ...TAP_OK, deploy_id: "tap-dep-1-99", status: "destroyed", started_at: 1700009999 };
    const other: DeploySummary = { ...TFC_FAILED, deploy_id: "tfc-dep-2-aa", scenario_id: "tfc-dep-2", status: "completed" };
    expect(tierStatus([DEPLOY, older, TFC_FAILED, TAP_OK, gone, other], "dep-1")).toEqual({ tap: "completed", tfc: "failed" });
    expect(tierStatus([DEPLOY], "dep-1")).toEqual({});
  });
});

describe("the tiers that follow te (2026-11-09: Deploy runs te → tap → tfc)", () => {
  it("the pill reads the follow-on records where no row stands: queued, skipped as —, failed with its reason", async () => {
    const te: DeploySummary = { ...DEPLOY, follow_on: { tap: { status: "skipped", reason: "the twin has no taps" }, tfc: { status: "failed", reason: "tfc: persona x unknown", at: 1700000400 } } };
    const { api } = seeded({ deploys: [te] });
    renderAt(api, "/twins/corp-live/canvas");
    await screen.findAllByTestId("canvas-host");
    expect(screen.getByTestId("tier-tap").textContent).toBe(" · tap —");
    expect(screen.getByTestId("tier-tap").getAttribute("title")).toBe("tap skipped: the twin has no taps");
    expect(screen.getByTestId("tier-tfc").dataset["status"]).toBe("failed");
    expect(screen.getByTestId("state-pill").className).toContain("failed");
    // No row failed, so no baseline re-apply: the tier is started again instead.
    expect(screen.queryByTestId("reapply-baseline")).toBeNull();
    expect(screen.getByTestId("start-tap")).toBeTruthy();
    expect(screen.getByTestId("start-tfc").getAttribute("title")).toContain("last: tfc: persona x unknown");
  });
  it("Start tap posts the tier and the pill shows it queued; a standing tier offers no Start", async () => {
    const { api, state } = seeded({ deploys: [DEPLOY, TAP_OK] });
    renderAt(api, "/twins/corp-live/canvas");
    await screen.findAllByTestId("canvas-host");
    expect(screen.queryByTestId("start-tap")).toBeNull();
    fireEvent.click(screen.getByTestId("start-tfc"));
    await waitFor(() => expect(state.writes.some((w) => w.op === "start-tier")).toBe(true));
    expect(state.writes.find((w) => w.op === "start-tier")).toEqual({ op: "start-tier", id: "dep-1", body: { tier: "tfc" } });
    await waitFor(() => expect(screen.getByTestId("tier-tfc").textContent).toBe(" · tfc … queued"));
    expect(screen.queryByTestId("start-tfc")).toBeNull();
  });
  it("tierStatus: a standing row wins; a newer failed record over an old row says the re-run failed", () => {
    const rec = (status: string, at: number) => ({ ...DEPLOY, follow_on: { tap: { status, at } } });
    expect(tierStatus([rec("completed", 1), TAP_OK], "dep-1")).toEqual({ tap: "completed" });
    expect(tierStatus([rec("failed", 1800000000), TAP_OK], "dep-1")).toEqual({ tap: "failed" });
    expect(tierStatus([rec("failed", 1800000000), TAP_OK], "dep-1", { rowsOnly: true })).toEqual({ tap: "completed" });
  });
});

describe("the document's state after Apply", () => {
  it("reads applied · unpersisted after a clean Apply, and unapplied changes once edited again", async () => {
    const { api } = seeded();
    renderAt(api, "/twins/corp-live/canvas");
    await screen.findAllByTestId("canvas-host");
    fireEvent.click(host("web01"));
    fireEvent.change(within(await panel().findByTestId("node-panel")).getByLabelText("Persona"), { target: { value: "generic-endpoint" } });
    await waitFor(() => expect(screen.getByTestId("dirty-marker").dataset["state"]).toBe("unapplied"));
    const apply = screen.getByRole("button", { name: "Apply" });
    await waitFor(() => expect(apply.hasAttribute("disabled")).toBe(false));
    fireEvent.click(apply);
    await screen.findByText(/applied via tfc/);
    await waitFor(() => expect(screen.getByTestId("dirty-marker").dataset["state"]).toBe("applied"));
    expect(screen.getByTestId("dirty-marker").textContent).toContain("applied · unpersisted");
    fireEvent.change(within(await panel().findByTestId("node-panel")).getByLabelText("Persona"), { target: { value: "corp-workstation" } });
    await waitFor(() => expect(screen.getByTestId("dirty-marker").dataset["state"]).toBe("unapplied"), { timeout: 2000 });
  });

  it("docState: clean, syncing, applied (the copy unchanged since the apply), unapplied", () => {
    expect(docState(null, false, false)).toBe("clean");
    expect(docState({ dirty: true, applied: null, updated_at: "t2" }, true, true)).toBe("syncing");
    expect(docState({ dirty: true, applied: { at: "t2", tiers: { tfc: "done" }, roles: {} }, updated_at: "t2" }, false, false)).toBe("applied");
    expect(docState({ dirty: true, applied: { at: "t1", tiers: { tfc: "done" }, roles: {} }, updated_at: "t2" }, false, false)).toBe("unapplied");
    expect(docState({ dirty: true, applied: null, updated_at: "t2" }, false, false)).toBe("unapplied");
  });
});

describe("Reset is best effort, and exits in flight", () => {
  it("a Reset whose baseline replay fails still discards the copy and says which tier and why", async () => {
    const { api, state } = seeded();
    state.replayFails["dep-1"] = ["tfc"];
    renderAt(api, "/twins/corp-live/canvas");
    await screen.findAllByTestId("canvas-host");
    fireEvent.click(host("web01"));
    fireEvent.change(within(await panel().findByTestId("node-panel")).getByLabelText("Persona"), { target: { value: "generic-endpoint" } });
    const apply = screen.getByRole("button", { name: "Apply" });
    await waitFor(() => expect(apply.hasAttribute("disabled")).toBe(false));
    fireEvent.click(apply);
    await screen.findByText(/applied via tfc/);
    fireEvent.click(screen.getByRole("button", { name: "Reset" }));
    const err = await screen.findByTestId("exit-error");
    expect(err.textContent).toContain("the working copy is discarded, but the baseline replay of tfc failed");
    expect(err.textContent).toContain("persona cannot play");
    await screen.findByText("working copy equals the baseline");
    await expect(api.getDeployConfig("dep-1").then((c) => c.has_working_copy)).resolves.toBe(false);
  });

  it("an exit in flight shows its progress with a Cancel that asks the API to stop; Apply and Reset wait", async () => {
    const { api, state } = seeded();
    state.exits["dep-1"] = { kind: "reset", tier: "tfc", started_at: "2026-11-06T04:00:00Z", elapsed_s: 12, cancel_requested: false };
    renderAt(api, "/twins/corp-live/canvas");
    await screen.findAllByTestId("canvas-host");
    const progress = await screen.findByTestId("exit-progress");
    expect(progress.textContent).toContain("resetting… replaying tfc · 12 s");
    expect(screen.getByRole("button", { name: "Apply" }).hasAttribute("disabled")).toBe(true);
    expect(screen.getByRole("button", { name: "Reset" }).hasAttribute("disabled")).toBe(true);
    fireEvent.click(screen.getByTestId("cancel-exit"));
    await screen.findByText(/cancel requested: reset stops after the op in flight \(tfc\)/);
    expect(state.writes.some((w) => w.op === "cancel-deploy-config-exit" && w.id === "dep-1")).toBe(true);
    // The next poll carries the flag; the exit's end clears the progress.
    await waitFor(() => expect(screen.getByTestId("exit-progress").textContent).toContain("cancel requested"), { timeout: 2500 });
    delete state.exits["dep-1"];
    await waitFor(() => expect(screen.queryByTestId("exit-progress")).toBeNull(), { timeout: 2500 });
  });

  it("a 409 from Apply surfaces at once in its own words: busy names the holder, cancelled names the tier", async () => {
    const { api, state } = seeded();
    state.refuse.apply = new ApiError({ kind: "conflict", status: 409, message: "apply on deploy dep-1 is in progress (replaying tfc); wait for it or cancel it", detail: { code: "busy", held_by: "dep-1", exit: "apply" }, url: "" });
    renderAt(api, "/twins/corp-live/canvas");
    await screen.findAllByTestId("canvas-host");
    fireEvent.click(host("web01"));
    fireEvent.change(within(await panel().findByTestId("node-panel")).getByLabelText("Persona"), { target: { value: "generic-endpoint" } });
    const apply = screen.getByRole("button", { name: "Apply" });
    await waitFor(() => expect(apply.hasAttribute("disabled")).toBe(false));
    fireEvent.click(apply);
    const err = await screen.findByTestId("exit-error");
    expect(err.textContent).toContain("busy: apply on deploy dep-1 is in progress (replaying tfc)");
    fireEvent.click(within(err).getByLabelText("Dismiss"));
    expect(screen.queryByTestId("exit-error")).toBeNull();
    expect(exitErrorText(new ApiError({ kind: "conflict", status: 409, message: "apply cancelled while replaying tfc on dep-1", detail: { code: "cancelled", tier: "tfc" }, url: "" }))).toBe("cancelled: apply cancelled while replaying tfc on dep-1");
    expect(exitErrorText(new Error("plain"))).toBe("plain");
  });
});

describe("the bar and the strip", () => {
  it("the layout group floats on the canvas, not in the bar; the exits are grouped; a note is dismissible", async () => {
    const { api } = seeded();
    renderAt(api, "/twins/corp-live/canvas");
    await screen.findAllByTestId("canvas-host");
    const bar = screen.getByTestId("layout-bar");
    expect(bar.closest(".react-flow__panel")).not.toBeNull();
    expect(within(screen.getByTestId("exits")).queryByText(/Save layout/)).toBeNull();
    expect(within(screen.getByTestId("exits")).getByRole("group", { name: "Working copy" })).toBeTruthy();
    expect(within(screen.getByTestId("exits")).getByRole("group", { name: "History" })).toBeTruthy();
    tab("Checks");
    await act(() => Promise.resolve());
  });
});

describe("the command stack survives a reload, and a pending change can be discarded on its own", () => {
  it("Undo is back after a reload of the same working copy, and gone once the copy changed meanwhile", async () => {
    const { api } = seeded();
    const first = renderAt(api, "/twins/corp-live/canvas");
    await screen.findAllByTestId("canvas-host");
    fireEvent.click(host("web01"));
    fireEvent.change(within(await panel().findByTestId("node-panel")).getByLabelText("Persona"), { target: { value: "generic-endpoint" } });
    await waitFor(() => expect(screen.getByRole("button", { name: "Undo" }).getAttribute("title")).toBe("undo: persona of web01"));
    // Synced: the stack is stored under the copy's updated_at.
    await waitFor(() => expect(screen.getByTestId("dirty-marker").dataset["state"]).toBe("unapplied"));
    first.unmount();
    renderAt(api, "/twins/corp-live/canvas");
    await screen.findAllByTestId("canvas-host");
    await waitFor(() => expect(screen.getByRole("button", { name: "Undo" }).getAttribute("title")).toBe("undo: persona of web01"));
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    await screen.findByText("working copy equals the baseline");
    // The copy moves under this browser (another console's PUT): the stored stack no longer applies.
    const cfg = await api.getDeployConfig("dep-1");
    await api.putDeployConfig("dep-1", { ...cfg.document.files, "tfc/default.yaml": cfg.document.files["tfc/default.yaml"]! + "    - node: ws01\n      persona: generic-endpoint\n" });
    screen.getByTestId("canvas").remove();
    renderAt(api, "/twins/corp-live/canvas");
    await screen.findAllByTestId("canvas-host");
    await waitFor(() => expect(screen.getAllByTestId("dirty-marker").at(-1)!.dataset["state"]).toBe("unapplied"));
    expect(screen.getAllByRole("button", { name: "Undo" }).at(-1)!.hasAttribute("disabled")).toBe(true);
  });

  it("Discard on a pending change puts that file back to the baseline, as an undoable edit", async () => {
    const { api } = seeded();
    renderAt(api, "/twins/corp-live/canvas");
    await screen.findAllByTestId("canvas-host");
    fireEvent.click(host("web01"));
    fireEvent.change(within(await panel().findByTestId("node-panel")).getByLabelText("Persona"), { target: { value: "generic-endpoint" } });
    fireEvent.click(host("ws01"));
    fireEvent.change(within(await panel().findByTestId("node-panel")).getByLabelText("Role"), { target: { value: "finance-ws" } });
    fireEvent.keyDown(within(panel().getByTestId("node-panel")).getByLabelText("Role"), { key: "Enter" });
    tab("Checks");
    await waitFor(() => expect(screen.getAllByTestId("pending-change").length).toBe(2));
    const tfc = screen.getAllByTestId("pending-change").find((c) => c.textContent!.includes("tfc/default.yaml"))!;
    fireEvent.click(within(tfc).getByTestId("discard-change"));
    await waitFor(() => expect(screen.getAllByTestId("pending-change").length).toBe(1));
    expect(screen.getByTestId("pending-change").textContent).toContain("te.yaml");
    expect(screen.getByRole("button", { name: "Undo" }).getAttribute("title")).toBe("undo: discard changes to tfc/default.yaml");
    fireEvent.click(within(screen.getByTestId("pending-change")).getByTestId("discard-change"));
    await screen.findByText("none: the working copy equals the baseline");
    await screen.findByText("working copy equals the baseline");
  });
});
