/**
 * The per-host persona change as a pure state machine
 * (`src/model/personaChange.ts`): preview staleness, the one-apply rule,
 * refusals in plain words, and what a plan says about this host vs the rest.
 */
import { describe, expect, it } from "vitest";

import type { PersonaPlan } from "../src/api/runtimeTypes";
import {
  canConfirm,
  draftKey,
  initialPersonaChange,
  isDirty,
  personaChangeReducer as r,
  refusalText,
  jobFor,
  planChanges,
  recordedSettings,
  requestFor,
  resultWords,
  summarizePlan,
  type PersonaChangeState,
} from "../src/model/personaChange";

function plan(overrides: Partial<PersonaPlan> = {}): PersonaPlan {
  return {
    deploy_id: "t1",
    host_id: "ws-01",
    target: "dev45",
    tfc_deploy_id: "tfc-t1-1",
    creates_tfc_deploy: false,
    ok: true,
    role: { current: "corp-workstation", baseline: "corp-workstation", new: "corp-workstation", changed: false },
    start: [
      { instance_id: "ws-01-dev", persona: "dev-workstation", reason: "new" },
      { instance_id: "ws-01-mail", persona: "mail-client", reason: "re-assert" },
    ],
    stop: [{ instance_id: "ws-01-corp", persona: "corp-workstation" }],
    unchanged: { hosts: 105, instances: 83 },
    dependents: [],
    will_update_dependents: false,
    refusals: [],
    warnings: [],
    ...overrides,
  };
}

/** Swap corp-workstation for dev-workstation and preview, answering OK. */
function previewed(p: PersonaPlan = plan()): PersonaChangeState {
  let s = initialPersonaChange(["corp-workstation", "mail-client"]);
  s = r(s, { type: "swap", from: "corp-workstation", to: "dev-workstation" });
  s = r(s, { type: "preview-start" });
  return r(s, { type: "preview-ok", seq: s.seq, draftKey: draftKey(s.draft), plan: p });
}

describe("editing the draft", () => {
  it("starts from what the host runs now, and is not dirty until something changes", () => {
    const s = initialPersonaChange(["a", "b"]);
    expect(s.draft.personas).toEqual(["a", "b"]);
    expect(isDirty(s)).toBe(false);
    expect(isDirty(r(s, { type: "remove", persona: "a" }))).toBe(true);
    expect(isDirty(r(s, { type: "set-role", role: "file-server" }))).toBe(true);
  });

  it("add / remove / swap never duplicate a persona", () => {
    let s = initialPersonaChange(["a", "b"]);
    s = r(s, { type: "add", persona: "a" });
    expect(s.draft.personas).toEqual(["a", "b"]);
    s = r(s, { type: "swap", from: "a", to: "b" });
    expect(s.draft.personas).toEqual(["b"]);
    s = r(s, { type: "remove", persona: "b" });
    expect(s.draft.personas).toEqual([]);
  });

  it("the draft key ignores order but not the role or the opt-ins", () => {
    const a = { personas: ["x", "y"], role: null, updateDependents: false, allowUncontained: false, allowRoleOrphaning: false };
    expect(draftKey(a)).toBe(draftKey({ ...a, personas: ["y", "x"] }));
    expect(draftKey(a)).not.toBe(draftKey({ ...a, role: "" }));
    expect(draftKey(a)).not.toBe(draftKey({ ...a, updateDependents: true }));
    expect(draftKey(a)).not.toBe(draftKey({ ...a, allowRoleOrphaning: true }));
  });

  it("a KEPT persona goes back with its recorded schedule AND seed unchanged (S2); a new one with none", () => {
    const s = previewed();
    const recorded = new Map([["mail-client", { schedule: "business-hours", seed: 4242 }]]);
    const body = requestFor(s.draft, recorded);
    expect(body.assignments).toEqual([
      { persona: "dev-workstation", schedule: null, seed: null, intensity: null },
      { persona: "mail-client", schedule: "business-hours", seed: 4242, intensity: null },
    ]);
    expect(body.role).toBeNull();
    expect(body.allow_role_orphaning).toBe(false);
    expect("expect_plan" in body).toBe(false);
    expect(requestFor(s.draft, recorded, "fp-1").expect_plan).toBe("fp-1");
  });

  it("recordedSettings reads the seed and schedule off the host's persona rows", () => {
    const rows = [{ persona: "a", schedule: "24x7", seed: "s-1" }, { persona: "b", schedule: null, seed: undefined }] as never;
    expect([...recordedSettings(rows)]).toEqual([
      ["a", { schedule: "24x7", seed: "s-1" }],
      ["b", { schedule: null, seed: null }],
    ]);
  });
});

describe("preview staleness", () => {
  it("a preview answers only for the draft it was asked about", () => {
    const s = previewed();
    expect(s.phase.kind).toBe("previewed");
    expect(canConfirm(s)).toBe(true);
  });

  it("editing after a preview DISCARDS it -- Confirm is gone until a fresh preview", () => {
    const s = r(previewed(), { type: "add", persona: "web-browser" });
    expect(s.phase.kind).toBe("editing");
    expect(canConfirm(s)).toBe(false);
    expect(r(s, { type: "confirm" })).toBe(s);
  });

  it("an answer for an OLDER request is dropped", () => {
    let s = initialPersonaChange(["a"]);
    s = r(s, { type: "remove", persona: "a" });
    s = r(s, { type: "preview-start" });
    const first = s.seq;
    s = r(s, { type: "preview-start" });
    const late = r(s, { type: "preview-ok", seq: first, draftKey: draftKey(s.draft), plan: plan() });
    expect(late).toBe(s);
    expect(late.phase.kind).toBe("previewing");
  });

  it("an answer for a draft edited while the request was in flight is dropped", () => {
    let s = initialPersonaChange(["a"]);
    s = r(s, { type: "remove", persona: "a" });
    s = r(s, { type: "preview-start" });
    const askedFor = draftKey(s.draft);
    const seq = s.seq;
    s = r(s, { type: "add", persona: "b" }); // edit: back to editing
    const answer = r(s, { type: "preview-ok", seq, draftKey: askedFor, plan: plan() });
    expect(answer.phase.kind).toBe("editing");
    expect(canConfirm(answer)).toBe(false);
  });

  it("a refused preview can never be confirmed", () => {
    const s = previewed(plan({ ok: false, refusals: [{ code: "os-mismatch", message: "win-desktop is windows; ws-01 is linux" }] }));
    expect(s.phase.kind).toBe("previewed");
    expect(canConfirm(s)).toBe(false);
    expect(r(s, { type: "confirm" })).toBe(s);
  });
});

describe("one apply at a time", () => {
  it("confirm moves to applying; a second confirm is a no-op", () => {
    const applying = r(previewed(), { type: "confirm" });
    expect(applying.phase.kind).toBe("applying");
    expect(r(applying, { type: "confirm" })).toBe(applying);
  });

  it("while busy, edits and previews are ignored", () => {
    const applying = r(previewed(), { type: "confirm" });
    expect(r(applying, { type: "add", persona: "x" })).toBe(applying);
    expect(r(applying, { type: "preview-start" })).toBe(applying);
    const running = r(applying, { type: "apply-ok", tfcDeployId: "tfc-t1-1", plan: null });
    expect(running.phase).toMatchObject({ kind: "running", tfcDeployId: "tfc-t1-1" });
    expect(r(running, { type: "remove", persona: "mail-client" })).toBe(running);
  });

  it("the result is the tfc deploy's own outcome", () => {
    const running = r(r(previewed(), { type: "confirm" }), { type: "apply-ok", tfcDeployId: "tfc-t1-1", plan: null });
    const done = r(running, { type: "finished", outcome: "failed", detail: "drive-persona: timeout" });
    expect(done.phase).toMatchObject({ kind: "done", outcome: "failed", detail: "drive-persona: timeout" });
  });

  it("a refused apply returns control to the operator with the reason", () => {
    const failed = r(r(previewed(), { type: "confirm" }), { type: "apply-failed", message: "not contained", code: "containment" });
    expect(failed.phase).toMatchObject({ kind: "apply-failed", code: "containment" });
    const optIn = r(failed, { type: "set-allow-uncontained", value: true });
    expect(optIn.phase.kind).toBe("editing");
    expect(optIn.draft.allowUncontained).toBe(true);
  });
});

describe("plain words", () => {
  it("says what starts, stops and stays on THIS host, and how many others are untouched", () => {
    expect(summarizePlan(plan())).toEqual({
      start: ["dev-workstation"],
      stop: ["corp-workstation"],
      keep: ["mail-client"],
      alsoRestarted: [],
      otherHosts: 105,
      otherInstances: 83,
    });
  });

  it("a dependent re-driven on ANOTHER host is listed by host, never as started here", () => {
    const p = plan({
      start: [
        { host_id: "ws-01", instance_id: "ws-01-dev", persona: "dev-workstation", reason: "changed" },
        { host_id: "ws-07", instance_id: "ws-07-corp", persona: "corp-workstation", reason: "dependent" },
      ],
      stop: [],
    });
    const lines = summarizePlan(p);
    expect(lines.start).toEqual(["dev-workstation (restarted with new settings)"]);
    expect(lines.alsoRestarted).toEqual(["ws-07: corp-workstation"]);
    expect(lines.keep).toEqual([]);
  });

  it("an unknown count stays unknown, never zero", () => {
    const p = plan();
    // A server that omits the count: the summary says it does not know.
    (p as { unchanged: unknown }).unchanged = {};
    expect(summarizePlan(p).otherHosts).toBeNull();
  });

  it("every refusal code has a headline in words, with the server's message kept", () => {
    // The codes the contract names (refusals + the PUT's containment); the
    // dead `no-host`/`not-te` entries are gone -- those are 404/400 errors.
    for (const code of ["os-mismatch", "unknown-persona", "resolve", "replay-host", "last-holder", "instance-collision", "containment"]) {
      const t = refusalText({ code, message: `server says ${code}` });
      expect(t.headline).not.toMatch(/os-mismatch|replay-host|not-te/);
      expect(t.headline.length).toBeGreaterThan(20);
      expect(t.detail).toBe(`server says ${code}`);
    }
    expect(refusalText({ code: "new-code", message: "m" }).headline).toBe("The server refused this change.");
    expect(refusalText({ code: "no-host", message: "m" }).headline).toBe("The server refused this change.");
    expect(refusalText({ code: "last-holder", message: "m" }).headline).toMatch(/nobody to talk to/);
  });
});

describe("the plan the server executes, not the one previewed (M4)", () => {
  it("a 409 plan-changed puts the FRESH plan up as a new preview, needing a new confirm", () => {
    const applying = r(previewed(plan({ fingerprint: "fp-old" })), { type: "confirm" });
    const fresh = plan({ fingerprint: "fp-new", stop: [], unchanged: { hosts: 104, instances: 82 } });
    const s = r(applying, { type: "plan-changed", plan: fresh });
    expect(s.phase).toMatchObject({ kind: "previewed", plan: { fingerprint: "fp-new" }, previous: { fingerprint: "fp-old" } });
    expect(canConfirm(s)).toBe(true);
    const again = r(s, { type: "confirm" });
    expect(again.phase).toMatchObject({ kind: "applying", plan: { fingerprint: "fp-new" } });
  });

  it("apply-ok REPLACES the preview with the executed plan", () => {
    const executed = plan({ fingerprint: "fp-exec", start: [{ instance_id: "x", persona: "other", reason: "new" }] });
    const s = r(r(previewed(), { type: "confirm" }), { type: "apply-ok", tfcDeployId: "t", plan: executed });
    expect(s.phase).toMatchObject({ kind: "running", plan: { fingerprint: "fp-exec" } });
  });

  it("says in words what changed between the two plans", () => {
    const lines = planChanges(plan(), plan({ stop: [], unchanged: { hosts: 104, instances: 82 } }));
    expect(lines).toContain("Stops no longer: corp-workstation");
    expect(lines).toContain("Other hosts untouched: 105 → 104");
    expect(planChanges(plan(), plan())[0]).toMatch(/detail this summary does not show/);
  });
});

describe("the result says what happened (M2)", () => {
  const row = (overrides: Record<string, unknown> = {}) => ({ instance_id: "i", persona: "p", stop_error: null, ...overrides }) as never;

  it("'Applied.' only when nothing went wrong", () => {
    expect(resultWords("completed", [], [row()])).toEqual({ tone: "good", headline: "Applied.", problems: [] });
  });

  it("a stop that failed reads as 'could not stop X -- it may still be running', never as Applied.", () => {
    const w = resultWords("completed", [], [row({ persona: "corp-ws", stop_error: { message: "QGA timeout", at: 1, heartbeat: null } })]);
    expect(w.tone).toBe("warn");
    expect(w.headline).not.toBe("Applied.");
    expect(w.problems[0]).toBe("Could not stop corp-ws — it may still be running (QGA timeout).");
  });

  it("an error diagnostic is a problem; a failed job is a failure", () => {
    expect(resultWords("completed", [{ level: "error", message: "per-host apply crashed" }], []).problems).toEqual(["per-host apply crashed"]);
    expect(resultWords("completed", [{ level: "info", message: "fine" }], []).tone).toBe("good");
    expect(resultWords("failed", [], []).tone).toBe("bad");
  });

  it("finds this host's job, by the executed fingerprint first", () => {
    const job = (id: string, host: string, fp: string, at: number) =>
      ({ job_id: id, host_id: host, status: "completed", started_at: at, finished_at: at, fingerprint: fp, plan: null, diagnostics: [] });
    const jobs = [job("1", "h", "a", 1), job("2", "h", "b", 2), job("3", "other", "a", 3)];
    expect(jobFor(jobs, "h", "a")?.job_id).toBe("1");
    expect(jobFor(jobs, "h", "zzz")?.job_id).toBe("2");
    expect(jobFor(undefined, "h", "a")).toBeNull();
    // A superseded job is never taken as the current one by the fallback.
    const superseded = { ...job("4", "h", "c", 9), superseded: true };
    expect(jobFor([...jobs, superseded], "h", "zzz")?.job_id).toBe("2");
  });
});

