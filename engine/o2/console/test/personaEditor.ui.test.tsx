/**
 * Changing one host's personas through the real Twin page (ruling §7 d):
 * select host -> change -> preview -> apply, where the preview says what
 * starts, stops and stays, and how many other hosts are untouched; a stale
 * preview is never confirmable; a double click sends ONE write; refusals are
 * in plain words; the picker disables what cannot run on the host's OS; and
 * the result shows the recorded heartbeat, because "started" is not "running".
 */
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { setWriteToken } from "../src/api/client";
import type { RuntimeApi } from "../src/api/runtime";
import type {
  DeployDetail,
  PersonaApplyResponse,
  PersonaCatalog,
  PersonaChangeRequest,
  PersonaPlan,
  TwinHostsDoc,
} from "../src/api/runtimeTypes";
import { TestApp } from "../src/routes";
import { apiError, corpTwinHosts, deferred, fakeRuntime, pending, unusedDesignApi } from "./helpers/runtime";

// The token lives in a module variable for the tab (by design); a test that
// sets one must not hand it to the next test in this worker.
afterEach(() => setWriteToken(null));

const ID = "corp-twin-07cfcdb2";
/** A real linux host with one persona in the fixture. */
const HOST = "file-server-10-80-75-core";

const CATALOG: PersonaCatalog = {
  twin: "corp-twin",
  warnings: [],
  personas: [
    { id: "cast-file-server", os_family: "linux", drive: "real-service", source: "twin", requires: [], summary: "serves tcp/445 (smb) to workstations", emits: [] },
    {
      id: "dev-workstation",
      os_family: "linux",
      drive: "scripted",
      source: "catalog",
      requires: [],
      summary: "tcp/22 -> git-server; tcp/443 -> egress",
      emits: [
        { proto: "ssh", ports: [22], peer_role: "git-server", transport: "tcp", cadence: null, volume: null, optional: false, peer_scope: "one" },
        { proto: "https", ports: [443], peer_role: "egress", transport: "tcp", cadence: null, volume: null, optional: false, peer_scope: "one" },
      ],
    },
    { id: "win-office-desktop", os_family: "windows", drive: "scripted", source: "catalog", requires: [], summary: "tcp/445 -> file-server", emits: [] },
  ],
};

function plan(overrides: Partial<PersonaPlan> = {}): PersonaPlan {
  return {
    deploy_id: ID,
    host_id: HOST,
    target: "dev45",
    tfc_deploy_id: "tfc-corp-twin-07cfcdb2-785b1a06",
    creates_tfc_deploy: false,
    ok: true,
    role: { current: "file-server", baseline: "file-server", new: "file-server", changed: false },
    start: [{ instance_id: `${HOST}-dev-workstation`, persona: "dev-workstation", reason: "new" }],
    stop: [{ instance_id: `${HOST}-cast-file-server`, persona: "cast-file-server" }],
    unchanged: { hosts: 105, instances: 83 },
    dependents: [],
    will_update_dependents: false,
    refusals: [],
    warnings: [],
    ...overrides,
  };
}

function hostDoc(): TwinHostsDoc {
  const doc = corpTwinHosts();
  const h = doc.hosts.find((x) => x.host_id === HOST)!;
  expect(h.os).toBe("linux");
  expect(h.personas.map((p) => p.persona)).toEqual(["cast-file-server"]);
  return doc;
}

function detail(status: string): DeployDetail {
  return {
    deploy: { deploy_id: "tfc-corp-twin-07cfcdb2-785b1a06", scenario_id: "tfc", scenario_version: null, status, started_at: 0, updated_at: 0, completed_at: null },
    plan_id: "tfc-x",
    placement: {},
    ops: [],
    counts: status === "running" ? { completed: 84, running: 1 } : { completed: 85 },
  };
}

function open(overrides: Partial<RuntimeApi>, calls: string[] = []): void {
  const runtime = fakeRuntime(
    {
      getHosts: () => Promise.resolve(hostDoc()),
      getTargets: () => Promise.resolve({ targets: [] }),
      getAccess: () => pending(),
      getPersonas: () => Promise.resolve(CATALOG),
      ...overrides,
    },
    calls,
  );
  render(<TestApp client={unusedDesignApi()} runtime={runtime} url={`/twin/${ID}/hosts/${HOST}/personas`} />);
}

async function swapToDev(): Promise<void> {
  const editor = await screen.findByTestId("persona-editor");
  await waitFor(() => expect((screen.getByTestId("persona-picker") as HTMLSelectElement).disabled).toBe(false));
  fireEvent.click(within(editor).getByTestId("remove-cast-file-server"));
  fireEvent.change(screen.getByTestId("persona-picker"), { target: { value: "dev-workstation" } });
  fireEvent.click(screen.getByTestId("persona-add"));
}

describe("changing one host's personas", () => {
  it("the picker offers only what runs on the host's OS; the rest are disabled WITH the reason", async () => {
    open({});
    await screen.findByTestId("persona-editor");
    await waitFor(() => expect((screen.getByTestId("persona-picker") as HTMLSelectElement).disabled).toBe(false));
    const options = [...(screen.getByTestId("persona-picker") as HTMLSelectElement).options];
    const win = options.find((o) => o.value === "win-office-desktop")!;
    expect(win.disabled).toBe(true);
    expect(win.textContent).toContain("runs on windows; this host is linux");
    expect(options.find((o) => o.value === "dev-workstation")!.disabled).toBe(false);
    // Fix round 2: the picker says what a persona does, in words.
    expect(options.find((o) => o.value === "dev-workstation")!.textContent).toBe(
      "dev-workstation — Opens remote sessions (SSH) to a git server; browses the web (HTTPS) to the internet.",
    );
    // Already on the host: disabled, and said.
    expect(options.find((o) => o.value === "cast-file-server")!.textContent).toContain("already on");
  });

  it("previews start / stop / unchanged on this host and the untouched rest, then applies to this host only", async () => {
    const bodies: PersonaChangeRequest[] = [];
    const calls: string[] = [];
    let polls = 0;
    const after = hostDoc();
    const h = after.hosts.find((x) => x.host_id === HOST)!;
    h.personas = [
      { ...h.personas[0]!, instance_id: `${HOST}-dev-workstation`, persona: "dev-workstation", origin: "host-edit",
        heartbeat: { alive: true, state: "alive", checked_at: Date.now() / 1000, reason: null } },
    ];
    let hostsReads = 0;
    open(
      {
        getHosts: () => Promise.resolve(++hostsReads === 1 ? hostDoc() : after),
        planHostPersonas: (_id, _h, _t, body) => {
          bodies.push(body);
          return Promise.resolve(plan());
        },
        putHostPersonas: (_id, _h, _t, body) => {
          bodies.push(body);
          return Promise.resolve<PersonaApplyResponse>({ deploy_id: ID, host_id: HOST, tfc_deploy_id: "tfc-corp-twin-07cfcdb2-785b1a06", status: "running", detached: true, plan: plan() });
        },
        getDeploy: () => Promise.resolve(detail(++polls === 1 ? "running" : "completed")),
      },
      calls,
    );
    await swapToDev();
    fireEvent.click(screen.getByTestId("persona-preview"));
    const shown = await screen.findByTestId("persona-plan");
    expect(within(shown).getByTestId("plan-start").textContent).toMatch(/Will start on .*: dev-workstation/);
    expect(within(shown).getByTestId("plan-stop").textContent).toMatch(/Will stop on .*: cast-file-server/);
    expect(within(shown).getByTestId("plan-keep").textContent).toMatch(/Unchanged on .*: nothing/);
    expect(within(shown).getByTestId("plan-others").textContent).toBe("Other hosts: 105 other hosts untouched (83 persona instances keep running)");
    expect(bodies[0]!.assignments.map((a) => a.persona)).toEqual(["dev-workstation"]);

    fireEvent.click(screen.getByTestId("persona-confirm"));
    expect(await screen.findByTestId("persona-progress")).toBeTruthy();
    const result = await screen.findByTestId("persona-result", {}, { timeout: 5000 });
    expect(result.textContent).toMatch(/Applied\./);
    await waitFor(() => expect(screen.getByTestId("result-heartbeats").textContent).toMatch(/dev-workstation.*after start: alive/));
    // The write went to THIS host's endpoint, once, with the same body previewed.
    expect(calls.filter((c) => c === "putHostPersonas")).toHaveLength(1);
    expect(bodies[1]).toEqual(bodies[0]);
  });

  it("an edit after the preview drops it: no Confirm until a fresh preview", async () => {
    open({ planHostPersonas: () => Promise.resolve(plan()) });
    await swapToDev();
    fireEvent.click(screen.getByTestId("persona-preview"));
    expect(await screen.findByTestId("persona-confirm")).toBeTruthy();
    fireEvent.change(screen.getByTestId("role-picker"), { target: { value: "dns-resolver" } });
    expect(screen.queryByTestId("persona-confirm")).toBeNull();
    expect(screen.queryByTestId("persona-plan")).toBeNull();
  });

  it("a preview answer that lands AFTER the draft changed is discarded", async () => {
    const slow = deferred<PersonaPlan>();
    open({ planHostPersonas: () => slow.promise });
    await swapToDev();
    fireEvent.click(screen.getByTestId("persona-preview"));
    // Edit while the preview is in flight...
    fireEvent.change(screen.getByTestId("role-picker"), { target: { value: "dns-resolver" } });
    await act(async () => slow.resolve(plan()));
    // ...and the late answer is not shown as if it described the new draft.
    expect(screen.queryByTestId("persona-plan")).toBeNull();
    expect(screen.queryByTestId("persona-confirm")).toBeNull();
  });

  it("a double click on Apply sends ONE write", async () => {
    const calls: string[] = [];
    open({ planHostPersonas: () => Promise.resolve(plan()), putHostPersonas: () => pending() }, calls);
    await swapToDev();
    fireEvent.click(screen.getByTestId("persona-preview"));
    const confirm = await screen.findByTestId("persona-confirm");
    fireEvent.click(confirm);
    fireEvent.click(confirm);
    await screen.findByTestId("persona-progress");
    expect(calls.filter((c) => c === "putHostPersonas")).toHaveLength(1);
  });

  it("refusals are said in plain words, and nothing can be applied", async () => {
    open({
      planHostPersonas: () =>
        Promise.resolve(plan({ ok: false, refusals: [{ code: "os-mismatch", message: "persona win-office-desktop is windows; host is linux" }] })),
    });
    await swapToDev();
    fireEvent.click(screen.getByTestId("persona-preview"));
    const refusals = await screen.findByTestId("refusals");
    expect(refusals.textContent).toMatch(/This change cannot be applied/);
    expect(refusals.textContent).toMatch(/does not run on this host's operating system/);
    expect(refusals.textContent).toMatch(/persona win-office-desktop is windows; host is linux/);
    expect(screen.queryByTestId("persona-confirm")).toBeNull();
  });

  it("a containment refusal on apply offers the explicit opt-in, and needs a new preview", async () => {
    open({
      planHostPersonas: () => Promise.resolve(plan()),
      putHostPersonas: () =>
        Promise.reject(
          apiError("conflict", 409, "HTTP 409", {
            refusals: [{ code: "containment", message: "router hq-mx egress phase is provision, not measure" }],
          }),
        ),
    });
    await swapToDev();
    fireEvent.click(screen.getByTestId("persona-preview"));
    fireEvent.click(await screen.findByTestId("persona-confirm"));
    const failed = await screen.findByTestId("persona-apply-failed");
    expect(failed.textContent).toMatch(/Not applied\./);
    expect(failed.textContent).toMatch(/not holding traffic inside the twin.*egress phase is provision/);
    fireEvent.click(within(failed).getByRole("checkbox"));
    expect(screen.queryByTestId("persona-confirm")).toBeNull();
    expect((screen.getByTestId("persona-preview") as HTMLButtonElement).disabled).toBe(false);
  });

  it("a held lease says another change is running, in words", async () => {
    open({
      planHostPersonas: () => Promise.resolve(plan()),
      putHostPersonas: () => Promise.reject(apiError("conflict", 409, "lease held", { held_by_deploy_id: "tfc-corp-twin-07cfcdb2-785b1a06" })),
    });
    await swapToDev();
    fireEvent.click(screen.getByTestId("persona-preview"));
    fireEvent.click(await screen.findByTestId("persona-confirm"));
    const failed = await screen.findByTestId("persona-apply-failed");
    expect(failed.textContent).toMatch(/Another traffic change on this twin is running \(tfc-corp-twin-07cfcdb2-785b1a06\)/);
    expect(within(failed).queryByRole("checkbox")).toBeNull();
  });

  it("a 401 asks for the write token, then retries the same write", async () => {
    let attempts = 0;
    open({
      planHostPersonas: () => Promise.resolve(plan()),
      putHostPersonas: () => (++attempts === 1 ? Promise.reject(apiError("unauthorized", 401, "missing token")) : pending()),
    });
    await swapToDev();
    fireEvent.click(screen.getByTestId("persona-preview"));
    fireEvent.click(await screen.findByTestId("persona-confirm"));
    const dialog = await screen.findByTestId("token-dialog");
    expect(dialog.textContent).toMatch(/needs this server's write token/);
    fireEvent.change(screen.getByTestId("token-input"), { target: { value: "s3cret" } });
    fireEvent.click(screen.getByTestId("token-submit"));
    await waitFor(() => expect(attempts).toBe(2));
    // Memory only (README): nothing token-shaped reached storage.
    expect(JSON.stringify({ ...window.localStorage })).not.toContain("s3cret");
    expect(screen.getByTestId("token-forget")).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// Fix round 1 (06-code-review.md): M4, M2, S2, S7, protection, the dependents
// checkbox across its own re-preview.
// ---------------------------------------------------------------------------
function protectedDoc(): TwinHostsDoc {
  return { ...hostDoc(), protected: true };
}

describe("fix round 1 -- the plan that runs is the plan shown (M4)", () => {
  it("sends the previewed fingerprint; a 409 plan-changed shows the fresh plan, what moved, and needs a new confirm", async () => {
    const bodies: PersonaChangeRequest[] = [];
    let puts = 0;
    const fresh = plan({ fingerprint: "fp-2", unchanged: { hosts: 104, instances: 82 } });
    open({
      planHostPersonas: () => Promise.resolve(plan({ fingerprint: "fp-1" })),
      putHostPersonas: (_i, _h, _t, body) => {
        bodies.push(body);
        return ++puts === 1
          ? Promise.reject(apiError("conflict", 409, "the plan changed since it was previewed", { code: "plan-changed", plan: fresh }))
          : pending();
      },
    });
    await swapToDev();
    fireEvent.click(screen.getByTestId("persona-preview"));
    fireEvent.click(await screen.findByTestId("persona-confirm"));
    const changed = await screen.findByTestId("plan-changed");
    expect(changed.textContent).toMatch(/The twin changed since you previewed/);
    expect(changed.textContent).toMatch(/Other hosts untouched: 105 → 104/);
    expect(screen.getByTestId("plan-others").textContent).toMatch(/104 other hosts untouched/);
    expect(bodies[0]!.expect_plan).toBe("fp-1");
    // Nothing ran: a NEW confirm is required, and it carries the fresh fingerprint.
    fireEvent.click(screen.getByTestId("persona-confirm"));
    await waitFor(() => expect(bodies).toHaveLength(2));
    expect(bodies[1]!.expect_plan).toBe("fp-2");
  });

  it("while it runs, the screen shows the plan the server EXECUTED, not the preview", async () => {
    const executed = plan({
      fingerprint: "fp-x",
      start: [{ host_id: HOST, instance_id: "x", persona: "dns-caching-client", reason: "new" }],
    });
    open({
      planHostPersonas: () => Promise.resolve(plan({ fingerprint: "fp-x" })),
      putHostPersonas: () =>
        Promise.resolve<PersonaApplyResponse>({ deploy_id: ID, host_id: HOST, tfc_deploy_id: "tfc-1", status: "running", detached: true, plan: executed }),
      getDeploy: () => pending(),
    });
    await swapToDev();
    fireEvent.click(screen.getByTestId("persona-preview"));
    fireEvent.click(await screen.findByTestId("persona-confirm"));
    await screen.findByText("What the server is doing");
    expect(screen.getByTestId("plan-start").textContent).toMatch(/dns-caching-client/);
    expect(screen.getByTestId("plan-start").textContent).not.toMatch(/dev-workstation/);
  });
});

describe("fix round 1 -- the result is honest (M2)", () => {
  it("a stop that failed reads 'could not stop … it may still be running', never 'Applied.'", async () => {
    const after = hostDoc();
    const h = after.hosts.find((x) => x.host_id === HOST)!;
    h.personas = [{ ...h.personas[0]!, stop_error: { message: "QGA timeout after 60 s", at: 1, heartbeat: null } }];
    let reads = 0;
    open({
      getHosts: () => Promise.resolve(++reads === 1 ? hostDoc() : after),
      planHostPersonas: () => Promise.resolve(plan({ fingerprint: "fp" })),
      putHostPersonas: () =>
        Promise.resolve<PersonaApplyResponse>({ deploy_id: ID, host_id: HOST, tfc_deploy_id: "tfc-1", status: "running", detached: true, plan: plan({ fingerprint: "fp" }) }),
      getDeploy: () =>
        Promise.resolve({
          ...detail("completed"),
          host_edits: [
            { job_id: "j", host_id: HOST, status: "completed", started_at: 1, finished_at: 2, fingerprint: "fp", plan: null,
              diagnostics: [{ level: "warning", message: "stop of cast-file-server raised" }] },
          ],
        }),
    });
    await swapToDev();
    fireEvent.click(screen.getByTestId("persona-preview"));
    fireEvent.click(await screen.findByTestId("persona-confirm"));
    const result = await screen.findByTestId("persona-result", {}, { timeout: 5000 });
    await waitFor(() => expect(result.textContent).toMatch(/Could not stop cast-file-server — it may still be running \(QGA timeout after 60 s\)/));
    expect(result.textContent).toMatch(/Applied, with problems/);
    expect(result.textContent).not.toMatch(/^Applied\.$/m);
    expect(result.textContent).toMatch(/warning: stop of cast-file-server raised/);
  });

  it("the job's own status decides the outcome over the tfc deploy's", async () => {
    open({
      planHostPersonas: () => Promise.resolve(plan({ fingerprint: "fp" })),
      putHostPersonas: () =>
        Promise.resolve<PersonaApplyResponse>({ deploy_id: ID, host_id: HOST, tfc_deploy_id: "tfc-1", status: "running", detached: true, plan: plan({ fingerprint: "fp" }) }),
      getDeploy: () =>
        Promise.resolve({
          ...detail("completed"),
          host_edits: [{ job_id: "j", host_id: HOST, status: "failed", started_at: 1, finished_at: 2, fingerprint: "fp", plan: null,
            diagnostics: [{ level: "error", message: "per-host apply crashed: boom" }] }],
        }),
    });
    await swapToDev();
    fireEvent.click(screen.getByTestId("persona-preview"));
    fireEvent.click(await screen.findByTestId("persona-confirm"));
    const result = await screen.findByTestId("persona-result", {}, { timeout: 5000 });
    expect(result.textContent).toMatch(/The apply ended failed\./);
    expect(result.textContent).toMatch(/per-host apply crashed: boom/);
  });
});

describe("fix round 1 -- a kept persona is left exactly as it is (S2)", () => {
  it("sends the kept persona's recorded seed and schedule, and the preview lists it as unchanged", async () => {
    const doc = hostDoc();
    const h = doc.hosts.find((x) => x.host_id === HOST)!;
    h.personas = [{ ...h.personas[0]!, seed: 1337, schedule: "business-hours" }];
    const bodies: PersonaChangeRequest[] = [];
    open({
      getHosts: () => Promise.resolve(doc),
      planHostPersonas: (_i, _h, _t, body) => {
        bodies.push(body);
        return Promise.resolve(
          plan({
            start: [
              { host_id: HOST, instance_id: `${HOST}-cast-file-server`, persona: "cast-file-server", reason: "re-assert" },
              { host_id: HOST, instance_id: `${HOST}-dev-workstation`, persona: "dev-workstation", reason: "new" },
            ],
            stop: [],
          }),
        );
      },
    });
    await screen.findByTestId("persona-editor");
    await waitFor(() => expect((screen.getByTestId("persona-picker") as HTMLSelectElement).disabled).toBe(false));
    fireEvent.change(screen.getByTestId("persona-picker"), { target: { value: "dev-workstation" } });
    fireEvent.click(screen.getByTestId("persona-add"));
    fireEvent.click(screen.getByTestId("persona-preview"));
    await screen.findByTestId("persona-plan");
    expect(bodies[0]!.assignments).toEqual([
      { persona: "cast-file-server", schedule: "business-hours", seed: 1337, intensity: null },
      { persona: "dev-workstation", schedule: null, seed: null, intensity: null },
    ]);
    expect(screen.getByTestId("plan-keep").textContent).toMatch(/Unchanged on .*: cast-file-server/);
    expect(screen.getByTestId("plan-start").textContent).not.toMatch(/cast-file-server/);
  });
});

describe("fix round 1 -- opt-ins stay on screen across the re-preview they trigger", () => {
  it("'update dependents' stays visible while the preview refreshes, and the refresh includes it", async () => {
    const bodies: PersonaChangeRequest[] = [];
    const second = deferred<PersonaPlan>();
    const withDeps = plan({
      role: { current: "file-server", baseline: "file-server", new: "dns-resolver", changed: true },
      dependents: [{ host_id: "ws-07", instance_id: "ws-07-x", persona: "cast-corp-workstation-local", stale_peers: { "file-server": { recorded: "10.1.1.1", new: "10.1.1.2" } } }],
    });
    open({
      planHostPersonas: (_i, _h, _t, body) => {
        bodies.push(body);
        return bodies.length === 1 ? Promise.resolve(withDeps) : second.promise;
      },
    });
    await swapToDev();
    fireEvent.click(screen.getByTestId("persona-preview"));
    const box = await screen.findByTestId("update-dependents");
    fireEvent.click(box);
    // Refreshing: the checkbox is still there, checked, and the screen says why it is dimmed.
    expect(screen.getByTestId("update-dependents")).toBeTruthy();
    expect((screen.getByTestId("update-dependents") as HTMLInputElement).checked).toBe(true);
    expect(screen.getByTestId("plan-refreshing").textContent).toMatch(/Refreshing the preview/);
    expect(screen.queryByTestId("persona-confirm")).toBeNull();
    await waitFor(() => expect(bodies).toHaveLength(2));
    expect(bodies[1]!.update_dependents).toBe(true);
    await act(async () => second.resolve({ ...withDeps, will_update_dependents: true }));
    expect(screen.queryByTestId("plan-refreshing")).toBeNull();
    expect(await screen.findByTestId("persona-confirm")).toBeTruthy();
  });

  it("the last-holder refusal is said in words and its opt-in re-previews with allow_role_orphaning (S7)", async () => {
    const bodies: PersonaChangeRequest[] = [];
    open({
      planHostPersonas: (_i, _h, _t, body) => {
        bodies.push(body);
        return Promise.resolve(
          body.allow_role_orphaning
            ? plan({ dependents: [{ host_id: "ws-07", instance_id: "i", persona: "p", stale_peers: {}, no_new_holder: true }] })
            : plan({ ok: false, refusals: [{ code: "last-holder", message: "no host would hold file-server; ws-07 needs it" }] }),
        );
      },
    });
    await swapToDev();
    fireEvent.click(screen.getByTestId("persona-preview"));
    const refusals = await screen.findByTestId("refusals");
    expect(refusals.textContent).toMatch(/nobody to talk to/);
    fireEvent.click(within(screen.getByTestId("role-orphaning")).getByRole("checkbox"));
    await waitFor(() => expect(bodies).toHaveLength(2));
    expect(bodies[1]!.allow_role_orphaning).toBe(true);
    const none = await screen.findByTestId("no-new-holder");
    expect(none.textContent).toMatch(/ws-07.*nobody to talk to/);
    expect(none.textContent).not.toMatch(/old address/);
  });
});

describe("fix round 1 -- protected twins", () => {
  it("disables the apply with the reason until protection is overridden by typing the deploy id, then sends it", async () => {
    const overrides: Array<string | null | undefined> = [];
    open({
      getHosts: () => Promise.resolve(protectedDoc()),
      planHostPersonas: () => Promise.resolve(plan({ fingerprint: "fp" })),
      putHostPersonas: (_i, _h, _t, _b, override) => {
        overrides.push(override);
        return pending();
      },
    });
    await swapToDev();
    expect(within(screen.getByTestId("persona-editor")).getByTestId("protected-badge")).toBeTruthy();
    fireEvent.click(screen.getByTestId("persona-preview"));
    const confirm = (await screen.findByTestId("persona-confirm")) as HTMLButtonElement;
    expect(confirm.disabled).toBe(true);
    expect(screen.getByTestId("confirm-blocked").textContent).toMatch(/Protected — writes are refused unless you override by name/);
    expect(confirm.className).toMatch(/locked/);
    expect(confirm.className).not.toMatch(/primary/);
    fireEvent.click(screen.getByTestId("protection-open"));
    fireEvent.change(screen.getByTestId("protection-typed"), { target: { value: "corp-twin" } });
    expect((screen.getByTestId("protection-confirm") as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByTestId("protection-typed"), { target: { value: ID } });
    fireEvent.click(screen.getByTestId("protection-confirm"));
    expect(screen.getByTestId("protection-overridden")).toBeTruthy();
    expect(confirm.disabled).toBe(false);
    fireEvent.click(confirm);
    await waitFor(() => expect(overrides).toEqual([ID]));
  });

  it("an unannounced 423 is said in words and offers the override step", async () => {
    open({
      planHostPersonas: () => Promise.resolve(plan({ fingerprint: "fp" })),
      putHostPersonas: () => Promise.reject(apiError("locked", 423, "deploy is protected", { code: "protected", deploy_id: ID })),
    });
    await swapToDev();
    fireEvent.click(screen.getByTestId("persona-preview"));
    fireEvent.click(await screen.findByTestId("persona-confirm"));
    const failed = await screen.findByTestId("persona-apply-failed");
    expect(failed.textContent).toMatch(/This twin is protected/);
    expect(within(failed).getByTestId("protection-gate")).toBeTruthy();
  });
});

describe("fix round 2 -- personas in plain words", () => {
  it("a draft persona leads with a sentence; the exact list is behind 'details'", async () => {
    open({});
    await swapToDev();
    const row = screen.getAllByTestId("draft-persona").find((r) => r.getAttribute("data-persona") === "dev-workstation")!;
    expect(within(row).getByTestId("persona-sentence").textContent).toBe(
      "Opens remote sessions (SSH) to a git server; browses the web (HTTPS) to the internet.",
    );
    const details = within(row).getByText("details").closest("details")!;
    expect(details.open).toBe(false);
    expect(within(row).getByTestId("persona-literal").textContent).toBe("tcp/22 -> git-server; tcp/443 -> egress");
  });
});

