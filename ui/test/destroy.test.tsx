/**
 * Destroy from the console (2026-11-08): the state-aware Deploy / Destroy… control, one shared
 * confirm in three places (the Twins list, the twin page, the canvas bar), the dirty-copy gate,
 * progress that ticks the tiers off, the return to the undeployed state without a reload, and
 * the API's reason verbatim on a refusal. jsdom over the fake API.
 */
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { StrictMode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../src/api/client";
import type { Api } from "../src/api/discovery";
import type { DeployList, DeploySummary, DestroyProgress, DestroyResult } from "../src/api/types";
import { currentTier, destroyWord, standingTiers, useDestroy, type DestroyRun } from "../src/areas/shell/destroy";
import { DEPLOY, host, panel, seeded, enabled } from "./helpers/canvasFixtures";
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
  it("once the te is coming down the word carries the sweep's step: the VM count, then the phases", () => {
    const run: DestroyRun = { deployId: "dep-1", tiers: ["te", "tfc"], down: ["tfc"], elapsed: 230.2, phase: "running", error: null, result: null };
    expect(destroyWord({ ...run, sweep: { step: "vms", done: 42, total: 96 } })).toBe("destroying… te · 42/96 VMs removed · 230 s");
    expect(destroyWord({ ...run, sweep: { step: "snippets", done: 0, total: 0 } })).toBe("destroying… te · clearing hook snippets · 230 s");
    expect(destroyWord({ ...run, sweep: { step: "network", done: 0, total: 0 } })).toBe("destroying… te · removing networks · 230 s");
    // A child still coming down never shows the te's count.
    expect(destroyWord({ ...run, down: [], sweep: { step: "vms", done: 1, total: 96 } })).toBe("destroying… tfc · 230 s");
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
  it("a big te sweep shows its VM count from the API while the DELETE runs", async () => {
    let release: () => void = () => undefined;
    const { api, state } = seeded({ deploys: [DEPLOY] });
    state.destroyHold = new Promise<void>((r) => (release = r));
    renderAt(api, "/twins/corp-live/canvas");
    await screen.findAllByTestId("canvas-host");
    fireEvent.click(within(screen.getByTestId("exits")).getByTestId("destroy-open"));
    fireEvent.click(dialog().getByTestId("destroy-confirm"));
    state.destroyProgress = { "dep-1": { deploy_id: "dep-1", running: true, step: "vms", done: 42, total: 96, elapsed_s: 200 } };
    await waitFor(() => expect(screen.getByTestId("destroy-progress").textContent).toMatch(/destroying… te · 42\/96 VMs removed · \d+ s/), { timeout: 3000 });
    await act(async () => release());
    await waitFor(() => expect(screen.queryByTestId("destroy-dialog")).toBeNull());
  });

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
    await waitFor(() => expect(screen.getByTestId("dirty-marker").textContent).toContain("local edits (not pushed)"));
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
    await waitFor(() => expect(screen.getByTestId("dirty-marker").textContent).toContain("local edits (not pushed)"));
    fireEvent.click(await enabled("Apply"));
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
    await waitFor(() => expect(screen.getByTestId("dirty-marker").textContent).toContain("local edits (not pushed)"));
    await enabled("Persist");
    fireEvent.click(within(screen.getByTestId("exits")).getByTestId("destroy-open"));
    fireEvent.click(dialog().getByTestId("destroy-persist-first"));
    await waitFor(() => expect(dialog().queryByTestId("destroy-dirty")).toBeNull());
    expect(state.writes.some((w) => w.op === "put-twin-config" || w.op === "twin-config-write")).toBe(true);
    expect(state.writes.some((w) => w.op === "destroy")).toBe(false);
  });
});

describe("useDestroy ends the run (2026-11-21: the dialog sat on 'Destroying…' after the destroy finished)", () => {
  const RESULT: DestroyResult = { deploy_id: "dep-1", destroyed: true, removed: 3, errors: 0, actions: [], cascade_errors: [], tiers: [{ tier: "te", deploy_id: "dep-1", destroyed: true, removed: 3, errors: 0 }] };
  const IDLE: DestroyProgress = { deploy_id: "dep-1", running: false, step: null, done: 0, total: 0, elapsed_s: null };
  /** A fake of the three calls the hook makes; `rows` is what `listDeploys` answers now. */
  function fake(destroy: () => Promise<DestroyResult>, rows: { now: DeploySummary[] }, progress: () => DestroyProgress = () => IDLE) {
    return {
      destroyDeploy: vi.fn(destroy),
      listDeploys: vi.fn(async (): Promise<DeployList> => ({ deploys: rows.now, warnings: [] })),
      getDestroyProgress: vi.fn(async () => progress()),
    } as unknown as Api;
  }
  function Harness({ api, onDone, tiers = ["te"] }: { api: Api; onDone: (r: DestroyResult) => void; tiers?: string[] }) {
    const { run, start } = useDestroy(api, onDone);
    return (
      <div>
        <button type="button" onClick={() => start("dep-1", tiers)}>go</button>
        <span data-testid="phase">{run?.phase ?? "idle"}</span>
        <span data-testid="down">{run?.down.join(",") ?? ""}</span>
        <span data-testid="error">{run?.error ? String(run.error) : ""}</span>
      </div>
    );
  }
  const phase = () => screen.getByTestId("phase").textContent;

  it("(a) under StrictMode, a resolved destroyDeploy moves the phase to done and calls onDone once", async () => {
    const onDone = vi.fn();
    const api = fake(async () => RESULT, { now: [DEPLOY] });
    render(<StrictMode><Harness api={api} onDone={onDone} /></StrictMode>);
    fireEvent.click(screen.getByText("go"));
    await waitFor(() => expect(phase()).toBe("done"));
    expect(onDone).toHaveBeenCalledTimes(1);
    expect(onDone).toHaveBeenCalledWith(RESULT);
  });

  it("(b) destroyDeploy never resolving: a deploy list that omits the deploy and an idle sweep end the run, once; a late answer changes nothing", async () => {
    const onDone = vi.fn();
    let answer: (r: DestroyResult) => void = () => undefined;
    const rows = { now: [DEPLOY, TFC] };
    const api = fake(() => new Promise<DestroyResult>((r) => (answer = r)), rows);
    render(<StrictMode><Harness api={api} onDone={onDone} tiers={["te", "tfc"]} /></StrictMode>);
    fireEvent.click(screen.getByText("go"));
    // Still standing: the poll keeps it running.
    await waitFor(() => expect(vi.mocked(api.listDeploys)).toHaveBeenCalled(), { timeout: 3000 });
    expect(phase()).toBe("running");
    // The API drops the destroyed deploy (and its child) from the list.
    rows.now = [];
    await waitFor(() => expect(phase()).toBe("done"), { timeout: 4000 });
    expect(screen.getByTestId("down").textContent).toBe("te,tfc");
    expect(onDone).toHaveBeenCalledTimes(1);
    expect(onDone.mock.calls[0]![0]).toMatchObject({ deploy_id: "dep-1", destroyed: true });
    await act(async () => answer(RESULT));
    await new Promise((r) => setTimeout(r, 1700));
    expect(onDone).toHaveBeenCalledTimes(1);
    expect(phase()).toBe("done");
  });

  it("the run waits for the sweep: the deploy gone from the list while the sweep still runs is not done yet", async () => {
    const onDone = vi.fn();
    let sweeping = true;
    const api = fake(() => new Promise<DestroyResult>(() => undefined), { now: [] }, () => (sweeping ? { ...IDLE, running: true, step: "network" } : IDLE));
    render(<StrictMode><Harness api={api} onDone={onDone} /></StrictMode>);
    fireEvent.click(screen.getByText("go"));
    await waitFor(() => expect(vi.mocked(api.getDestroyProgress)).toHaveBeenCalled(), { timeout: 3000 });
    await new Promise((r) => setTimeout(r, 200));
    expect(phase()).toBe("running");
    sweeping = false;
    await waitFor(() => expect(phase()).toBe("done"), { timeout: 4000 });
    expect(onDone).toHaveBeenCalledTimes(1);
  });

  it("a lost DELETE answer (no answer, a gateway timeout) leaves the poll to decide: the deploy gone, the run is done", async () => {
    const onDone = vi.fn();
    const rows = { now: [DEPLOY] };
    const api = fake(async () => {
      throw new ApiError({ kind: "network", status: 0, message: "no answer from /api/deploys/dep-1", url: "/api/deploys/dep-1" });
    }, rows);
    render(<StrictMode><Harness api={api} onDone={onDone} /></StrictMode>);
    fireEvent.click(screen.getByText("go"));
    await new Promise((r) => setTimeout(r, 50));
    expect(phase()).toBe("running");
    rows.now = [];
    await waitFor(() => expect(phase()).toBe("done"), { timeout: 4000 });
    expect(onDone).toHaveBeenCalledTimes(1);
  });

  it("a lost DELETE answer with nothing coming down (te standing, sweep idle) fails with that error, never hangs", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const onDone = vi.fn();
      const api = fake(async () => {
        throw new ApiError({ kind: "http", status: 504, message: "gateway timeout", url: "/api/deploys/dep-1" });
      }, { now: [DEPLOY] });
      render(<StrictMode><Harness api={api} onDone={onDone} /></StrictMode>);
      fireEvent.click(screen.getByText("go"));
      for (let i = 0; i < 6; i++) await act(async () => void (await vi.advanceTimersByTimeAsync(1500)));
      await waitFor(() => expect(phase()).toBe("failed"));
      expect(screen.getByTestId("error").textContent).toContain("gateway timeout");
      expect(onDone).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("the three places close and refresh when the poll sees the destroy finished (StrictMode, the DELETE answer never comes)", () => {
  /** The API as it is now: a destroyed deploy (and its children) leave the list; the DELETE never answers. */
  const finishWithoutAnswer = (state: { deploys: DeploySummary[]; destroyHold?: Promise<void> }) => {
    state.destroyHold = new Promise<void>(() => undefined);
    return () => (state.deploys = state.deploys.filter((d) => d.deploy_id !== "dep-1" && !d.scenario_id.endsWith("-dep-1")));
  };

  it("the Twins list: the dialog closes and the row offers Deploy again", async () => {
    const { api, state } = seeded({ deploys: [DEPLOY, TFC, TAP] });
    const finish = finishWithoutAnswer(state);
    renderAt(api, "/twins", { strict: true });
    await screen.findAllByTestId("twin-row");
    fireEvent.click(within(rowOf("corp-live")).getByTestId("destroy-open"));
    fireEvent.click(dialog().getByTestId("destroy-confirm"));
    await waitFor(() => expect(dialog().getByTestId("destroy-progress")).toBeTruthy());
    finish();
    await waitFor(() => expect(screen.queryByTestId("destroy-dialog")).toBeNull(), { timeout: 4000 });
    await waitFor(() => expect(within(rowOf("corp-live")).getByRole("link", { name: "Deploy" })).toBeTruthy());
  });

  it("the twin page: the dialog closes and the page reads undeployed", async () => {
    const { api, state } = seeded();
    const finish = finishWithoutAnswer(state);
    renderAt(api, "/twins/corp-live", { strict: true });
    await screen.findByTestId("twin-summary");
    fireEvent.click(within(screen.getByTestId("deploy-panel")).getByTestId("destroy-open"));
    fireEvent.click(dialog().getByTestId("destroy-confirm"));
    await waitFor(() => expect(dialog().getByTestId("destroy-progress")).toBeTruthy());
    finish();
    await waitFor(() => expect(screen.queryByTestId("destroy-dialog")).toBeNull(), { timeout: 4000 });
    await waitFor(() => expect(screen.getByRole("button", { name: /Plan the deploy/ })).toBeTruthy());
    expect(screen.getByTestId("deploy-state").textContent).toMatch(/^none/);
  });

  it("the canvas bar: the dialog closes and the canvas is the undeployed edit mode", async () => {
    const { api, state } = seeded({ deploys: [DEPLOY] });
    const finish = finishWithoutAnswer(state);
    renderAt(api, "/twins/corp-live/canvas", { strict: true });
    await screen.findAllByTestId("canvas-host");
    fireEvent.click(within(screen.getByTestId("exits")).getByTestId("destroy-open"));
    fireEvent.click(dialog().getByTestId("destroy-confirm"));
    await waitFor(() => expect(dialog().getByTestId("destroy-progress")).toBeTruthy());
    finish();
    await waitFor(() => expect(screen.queryByTestId("destroy-dialog")).toBeNull(), { timeout: 4000 });
    expect((await screen.findByTestId("exit-note")).textContent).toContain("destroyed dep-1");
    await waitFor(() => expect(screen.getByTestId("mode").textContent).toBe("edit · undeployed"));
  });
});
