/**
 * Destroy from the console (2026-11-08): the state-aware Deploy / Destroy… control, one shared
 * confirm in three places (the Twins list, the twin page, the canvas bar), the dirty-copy gate,
 * progress that ticks the tiers off, the return to the undeployed state without a reload, and
 * the API's reason verbatim on a refusal. jsdom over the fake API.
 */
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { ApiError } from "../src/api/client";
import type { DeploySummary } from "../src/api/types";
import { currentTier, destroyWord, standingTiers, type DestroyRun } from "../src/areas/shell/destroy";
import { DEPLOY, host, panel, seeded } from "./helpers/canvasFixtures";
import { renderAt } from "./helpers/render";

const TFC: DeploySummary = { deploy_id: "tfc-dep-1-aa", scenario_id: "tfc-dep-1", status: "completed", started_at: 1700000200, updated_at: 1700000300, tier: "tfc", target: "dev45" };
const TAP: DeploySummary = { deploy_id: "tap-dep-1-bb", scenario_id: "tap-dep-1", status: "completed", started_at: 1700000200, updated_at: 1700000300, tier: "tap", target: "dev45" };

afterEach(() => {
  localStorage.clear();
});

const rowOf = (id: string) => screen.getAllByTestId("twin-row").find((r) => within(r).queryByText(id))!;
const dialog = () => within(screen.getByTestId("destroy-dialog"));

describe("the pure parts", () => {
  it("the standing tiers are te first, then the children standing on it", () => {
    expect(standingTiers([DEPLOY, TFC, TAP], "dep-1")).toEqual(["te", "tfc", "tap"]);
    expect(standingTiers([DEPLOY, { ...TAP, status: "destroyed" }], "dep-1")).toEqual(["te"]);
  });
  it("the progress names the tier coming down now: children first, the te last", () => {
    const run: DestroyRun = { deployId: "dep-1", tiers: ["te", "tfc", "tap"], down: [], elapsed: 12.4, phase: "running", error: null, result: null };
    expect(currentTier(run)).toBe("tfc");
    expect(destroyWord({ ...run, down: ["tfc", "tap"] })).toBe("destroying… te · 12 s");
    expect(destroyWord({ ...run, phase: "done" })).toBeNull();
  });
});

describe("the Twins list", () => {
  it("is state-aware: Deploy while undeployed, Destroy… (and no Deploy) once a deploy stands", async () => {
    renderAt(seeded({ deploys: [DEPLOY, TFC, TAP] }).api, "/twins");
    await screen.findAllByTestId("twin-row");
    const live = rowOf("corp-live");
    expect(within(live).queryByRole("link", { name: "Deploy" })).toBeNull();
    expect(within(live).getByTestId("destroy-open").textContent).toBe("Destroy…");
    const auth = rowOf("corp-auth");
    expect(within(auth).getByRole("link", { name: "Deploy" })).toBeTruthy();
    expect(within(auth).queryByTestId("destroy-open")).toBeNull();
  });

  it("the confirm names the twin, target, deploy id, the tiers and the sensor binding; confirmed, the row flips to undeployed with Deploy back", async () => {
    const { api, state } = seeded({ deploys: [DEPLOY, TFC, TAP] });
    renderAt(api, "/twins");
    await screen.findAllByTestId("twin-row");
    fireEvent.click(within(rowOf("corp-live")).getByTestId("destroy-open"));
    const d = screen.getByTestId("destroy-dialog");
    expect(d.textContent).toContain("corp-live");
    expect(d.textContent).toContain("dev45");
    expect(dialog().getByTestId("destroy-deploy-id").textContent).toBe("dep-1");
    expect(dialog().getByTestId("destroy-tiers").textContent).toBe("te · tfc · tap");
    expect(dialog().getByTestId("destroy-binding").textContent).toContain("sensor binding is released");
    // The dialog's clicks never reach the row (which navigates).
    fireEvent.click(dialog().getByTestId("destroy-confirm"));
    await waitFor(() => expect(screen.queryByTestId("destroy-dialog")).toBeNull());
    expect(state.writes.filter((w) => w.op === "destroy").map((w) => w.id)).toEqual(["dep-1"]);
    await waitFor(() => expect(within(rowOf("corp-live")).getByTestId("twin-deploy").textContent).toContain("undeployed"));
    expect(within(rowOf("corp-live")).getByRole("link", { name: "Deploy" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Twins" })).toBeTruthy();
  });

  it("a refusal shows the API's reason verbatim and keeps the deploy", async () => {
    const { api, state } = seeded();
    state.refuse.destroy = new ApiError({ kind: "conflict", status: 409, message: "another deploy of scenario 'corp-live' is in progress; refusing to destroy", url: "" });
    renderAt(api, "/twins");
    await screen.findAllByTestId("twin-row");
    fireEvent.click(within(rowOf("corp-live")).getByTestId("destroy-open"));
    fireEvent.click(dialog().getByTestId("destroy-confirm"));
    const err = await within(screen.getByTestId("destroy-dialog")).findByRole("alert");
    expect(err.textContent).toBe("could not destroy: another deploy of scenario 'corp-live' is in progress; refusing to destroy");
  });
});

describe("the twin page", () => {
  it("offers Destroy… in place of Deploy when a deploy stands, and Destroy… opens the shared confirm", async () => {
    renderAt(seeded({ deploys: [DEPLOY, TFC] }).api, "/twins/corp-live");
    await screen.findByTestId("twin-summary");
    expect(screen.queryByRole("button", { name: /Plan the deploy/ })).toBeNull();
    fireEvent.click(within(screen.getByTestId("deploy-panel")).getByTestId("destroy-open"));
    expect(dialog().getByTestId("destroy-tiers").textContent).toBe("te · tfc");
    expect(dialog().getByTestId("destroy-binding").textContent).toContain("No tap plan stands");
  });

  it("Delete… on a deployed twin says to destroy first and links to Destroy, never sending the delete", async () => {
    const { api, state } = seeded();
    renderAt(api, "/twins/corp-live#delete");
    await screen.findByTestId("twin-summary");
    const blocked = screen.getByTestId("delete-blocked");
    expect(blocked.textContent).toContain("Destroy the deploy first");
    fireEvent.click(within(blocked).getByTestId("delete-blocked-destroy"));
    expect(screen.getByTestId("destroy-dialog")).toBeTruthy();
    expect(state.writes.some((w) => w.op === "delete-twin")).toBe(false);
  });

  it("after the destroy the page reads undeployed and offers Deploy again", async () => {
    const { api } = seeded();
    renderAt(api, "/twins/corp-live");
    await screen.findByTestId("twin-summary");
    fireEvent.click(within(screen.getByTestId("deploy-panel")).getByTestId("destroy-open"));
    fireEvent.click(dialog().getByTestId("destroy-confirm"));
    await waitFor(() => expect(screen.getByRole("button", { name: /Plan the deploy/ })).toBeTruthy());
    expect(screen.getByTestId("deploy-state").textContent).toContain("none standing");
  });
});

describe("the canvas bar", () => {
  it("Destroy… sits in the exits; in flight it disables the exits, shows progress and ticks tiers; done, the canvas is the undeployed edit mode", async () => {
    let release: () => void = () => undefined;
    const { api, state } = seeded({ deploys: [DEPLOY, TFC, TAP] });
    state.destroyHold = new Promise<void>((r) => (release = r));
    renderAt(api, "/twins/corp-live/canvas");
    await screen.findAllByTestId("canvas-host");
    const exits = within(screen.getByTestId("exits"));
    fireEvent.click(exits.getByTestId("destroy-open"));
    expect(dialog().queryByTestId("destroy-dirty")).toBeNull();
    fireEvent.click(dialog().getByTestId("destroy-confirm"));
    await waitFor(() => expect(screen.getByTestId("exit-progress").textContent).toMatch(/^destroying… tfc · \d+ s/));
    expect(exits.getByTestId("destroy-open").hasAttribute("disabled")).toBe(true);
    expect(exits.getByRole("button", { name: "Reset" }).hasAttribute("disabled")).toBe(true);
    // The children come down first: a poll sees the tfc gone and ticks it off.
    state.deploys = [DEPLOY, { ...TFC, status: "destroyed" }, TAP];
    await waitFor(() => expect(within(screen.getByTestId("destroy-dialog")).getByTestId("destroy-tier-tfc").dataset["down"]).toBe("true"), { timeout: 3000 });
    await act(async () => release());
    await waitFor(() => expect(screen.queryByTestId("destroy-dialog")).toBeNull());
    // The fake answers with what it found standing (the tfc row was already gone): the note names what came down.
    expect((await screen.findByTestId("exit-note")).textContent).toContain("destroyed dep-1 · tap · te down");
    await waitFor(() => expect(screen.getByRole("button", { name: "Save" })).toBeTruthy());
    expect(screen.queryByRole("button", { name: "Apply" })).toBeNull();
    expect(screen.getByTestId("mode").textContent).toBe("edit · undeployed");
    expect(screen.queryByTestId("destroy-open")).toBeNull();
  });

  it("a dirty working copy must be acknowledged: the checkbox gates Destroy, and Persist first is offered", async () => {
    const { api, state } = seeded();
    renderAt(api, "/twins/corp-live/canvas");
    await screen.findAllByTestId("canvas-host");
    fireEvent.click(host("web01"));
    fireEvent.change(within(await panel().findByTestId("node-panel")).getByLabelText("Persona"), { target: { value: "generic-endpoint" } });
    await waitFor(() => expect(screen.getByTestId("dirty-marker").textContent).toContain("unapplied changes"));
    fireEvent.click(within(screen.getByTestId("exits")).getByTestId("destroy-open"));
    const gate = dialog().getByTestId("destroy-dirty");
    expect(gate.textContent).toContain("Unapplied changes will be discarded.");
    const confirm = dialog().getByTestId("destroy-confirm");
    expect(confirm.hasAttribute("disabled")).toBe(true);
    expect(dialog().getByTestId("destroy-persist-first")).toBeTruthy();
    fireEvent.click(within(gate).getByRole("checkbox"));
    expect(confirm.hasAttribute("disabled")).toBe(false);
    fireEvent.click(confirm);
    await waitFor(() => expect(state.writes.some((w) => w.op === "destroy" && w.id === "dep-1")).toBe(true));
  });

  it("applied but never persisted: the dialog says the running twin's changes are lost, not 'unapplied'", async () => {
    const { api } = seeded();
    renderAt(api, "/twins/corp-live/canvas");
    await screen.findAllByTestId("canvas-host");
    fireEvent.click(host("web01"));
    fireEvent.change(within(await panel().findByTestId("node-panel")).getByLabelText("Persona"), { target: { value: "generic-endpoint" } });
    await waitFor(() => expect(screen.getByTestId("dirty-marker").textContent).toContain("unapplied changes"));
    const apply = screen.getByRole("button", { name: "Apply" });
    await waitFor(() => expect(apply.hasAttribute("disabled")).toBe(false));
    fireEvent.click(apply);
    await waitFor(() => expect(screen.getByTestId("dirty-marker").textContent).toContain("applied · unpersisted"));
    fireEvent.click(within(screen.getByTestId("exits")).getByTestId("destroy-open"));
    const gate = dialog().getByTestId("destroy-dirty");
    expect(gate.dataset["kind"]).toBe("applied");
    expect(gate.textContent).toContain("applied changes that were never persisted");
    expect(gate.textContent).not.toContain("Unapplied");
    expect(within(gate).getByRole("checkbox").parentElement!.textContent).toContain("Lose the applied, unpersisted changes");
    expect(dialog().getByTestId("destroy-confirm").hasAttribute("disabled")).toBe(true);
    expect(dialog().getByTestId("destroy-persist-first")).toBeTruthy();
  });

  it("Persist first writes the baseline and leaves the confirm open, now clean", async () => {
    const { api, state } = seeded();
    renderAt(api, "/twins/corp-live/canvas");
    await screen.findAllByTestId("canvas-host");
    fireEvent.click(host("web01"));
    fireEvent.change(within(await panel().findByTestId("node-panel")).getByLabelText("Persona"), { target: { value: "generic-endpoint" } });
    await waitFor(() => expect(screen.getByTestId("dirty-marker").textContent).toContain("unapplied changes"));
    const persist = screen.getByRole("button", { name: "Persist" });
    await waitFor(() => expect(persist.hasAttribute("disabled")).toBe(false));
    fireEvent.click(within(screen.getByTestId("exits")).getByTestId("destroy-open"));
    fireEvent.click(dialog().getByTestId("destroy-persist-first"));
    await waitFor(() => expect(dialog().queryByTestId("destroy-dirty")).toBeNull());
    expect(state.writes.some((w) => w.op === "put-twin-config" || w.op === "twin-config-write")).toBe(true);
    expect(state.writes.some((w) => w.op === "destroy")).toBe(false);
  });
});
