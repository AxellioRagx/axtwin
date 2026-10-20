/**
 * Changing ONE host's personas -- the scalpel (ruling §3.3) -- as a pure state
 * machine: edit -> preview -> confirm -> progress -> result (rule 6).
 *
 * Three properties are the point of it being a reducer the tests drive
 * directly rather than state scattered through a component:
 *
 *   1. **A preview answers only for the draft it was asked about.** Every
 *      preview request carries a sequence number AND the draft's key; an
 *      answer for an older request, or for a draft the operator has since
 *      edited, is DISCARDED. Editing after a preview drops it -- Confirm is
 *      then unavailable until a fresh preview is shown, so a confirmed change
 *      always matches the plan the operator read.
 *   2. **One apply at a time.** `confirm` is accepted only from a current,
 *      refusal-free preview; while an apply is in flight or running, a second
 *      confirm (a double click) is a no-op and edits are ignored.
 *   3. **"Started" is not "running".** The result carries the tfc deploy's own
 *      outcome; the host's recorded heartbeat is read afterwards and shown
 *      beside it (the view's job), never inferred from "completed".
 */
import type { HostEditJob, PersonaAssignment, PersonaChangeRequest, PersonaInstance, PersonaPlan, Refusal } from "../api/runtimeTypes";

export interface PersonaDraft {
  /** The host's COMPLETE desired persona set, in the order the operator built it. */
  personas: string[];
  /** `null` = keep the host's role; `""` = back to the twin's own; else set it. */
  role: string | null;
  updateDependents: boolean;
  allowUncontained: boolean;
  /** Opt-in to a role change that leaves a role other personas need with no
   *  holder (refusal `last-holder`). */
  allowRoleOrphaning: boolean;
}

export type Phase =
  | { kind: "editing" }
  | { kind: "previewing"; seq: number; draftKey: string }
  /** `previous` is set when the server refused the confirm because the plan
   *  changed since the operator read it: the fresh plan is shown with what
   *  moved, and needs a NEW confirm. */
  | { kind: "previewed"; draftKey: string; plan: PersonaPlan; previous?: PersonaPlan }
  | { kind: "preview-failed"; draftKey: string; message: string }
  | { kind: "applying"; draftKey: string; plan: PersonaPlan }
  /** From here on `plan` is the plan the server EXECUTED (the PUT's answer),
   *  never the preview. */
  | { kind: "running"; plan: PersonaPlan; tfcDeployId: string }
  | { kind: "done"; plan: PersonaPlan; tfcDeployId: string; outcome: string; detail: string | null; diagnostics: Diagnostic[] }
  | { kind: "apply-failed"; plan: PersonaPlan; message: string; code: string | null };

export interface PersonaChangeState {
  /** What the host runs now (recorded), the draft starts from it. */
  baseline: string[];
  draft: PersonaDraft;
  phase: Phase;
  seq: number;
}

export type PersonaChangeEvent =
  | { type: "add"; persona: string }
  | { type: "remove"; persona: string }
  | { type: "swap"; from: string; to: string }
  | { type: "set-role"; role: string | null }
  | { type: "set-dependents"; value: boolean }
  | { type: "set-allow-uncontained"; value: boolean }
  | { type: "set-allow-role-orphaning"; value: boolean }
  | { type: "preview-start" }
  | { type: "preview-ok"; seq: number; draftKey: string; plan: PersonaPlan }
  | { type: "preview-failed"; seq: number; draftKey: string; message: string }
  | { type: "confirm" }
  | { type: "apply-ok"; tfcDeployId: string; plan: PersonaPlan | null }
  | { type: "plan-changed"; plan: PersonaPlan }
  | { type: "apply-failed"; message: string; code: string | null }
  | { type: "finished"; outcome: string; detail: string | null; diagnostics?: Diagnostic[] }
  | { type: "reset"; baseline: string[] };

export function initialPersonaChange(baseline: readonly string[]): PersonaChangeState {
  return {
    baseline: [...baseline],
    draft: { personas: [...baseline], role: null, updateDependents: false, allowUncontained: false, allowRoleOrphaning: false },
    phase: { kind: "editing" },
    seq: 0,
  };
}

/** A draft's identity: the persona SET (order does not change the plan), the
 *  role and both opt-ins. */
export function draftKey(draft: PersonaDraft): string {
  return JSON.stringify([
    [...new Set(draft.personas)].sort(),
    draft.role,
    draft.updateDependents,
    draft.allowUncontained,
    draft.allowRoleOrphaning,
  ]);
}

export function isBusy(phase: Phase): boolean {
  return phase.kind === "applying" || phase.kind === "running";
}

/** Whether the draft differs from what the host runs now at all. */
export function isDirty(state: PersonaChangeState): boolean {
  const a = [...new Set(state.draft.personas)].sort();
  const b = [...new Set(state.baseline)].sort();
  return state.draft.role !== null || a.length !== b.length || a.some((p, i) => p !== b[i]);
}

/** Confirm is offered only for a preview of THIS draft that the server did
 *  not refuse. */
export function canConfirm(state: PersonaChangeState): boolean {
  return (
    state.phase.kind === "previewed" &&
    state.phase.draftKey === draftKey(state.draft) &&
    state.phase.plan.ok &&
    state.phase.plan.refusals.length === 0
  );
}

function edited(state: PersonaChangeState, draft: PersonaDraft): PersonaChangeState {
  // An edit invalidates any preview (its answer described another draft) and
  // leaves a finished result behind: the operator is composing a new change.
  return { ...state, draft, phase: state.phase.kind === "editing" ? state.phase : { kind: "editing" } };
}

export function personaChangeReducer(state: PersonaChangeState, event: PersonaChangeEvent): PersonaChangeState {
  if (isBusy(state.phase)) {
    // Nothing but the apply's own progress moves a busy machine.
    if (event.type === "apply-ok" && state.phase.kind === "applying") {
      // The executed plan replaces the preview (M4); an older server that
      // sends none leaves the preview, which is then all there is.
      return { ...state, phase: { kind: "running", plan: event.plan ?? state.phase.plan, tfcDeployId: event.tfcDeployId } };
    }
    if (event.type === "plan-changed" && state.phase.kind === "applying") {
      return {
        ...state,
        phase: { kind: "previewed", draftKey: state.phase.draftKey, plan: event.plan, previous: state.phase.plan },
      };
    }
    if (event.type === "apply-failed" && state.phase.kind === "applying") {
      return { ...state, phase: { kind: "apply-failed", plan: state.phase.plan, message: event.message, code: event.code } };
    }
    if (event.type === "finished" && state.phase.kind === "running") {
      return {
        ...state,
        phase: {
          kind: "done",
          plan: state.phase.plan,
          tfcDeployId: state.phase.tfcDeployId,
          outcome: event.outcome,
          detail: event.detail,
          diagnostics: event.diagnostics ?? [],
        },
      };
    }
    return state;
  }

  switch (event.type) {
    case "add": {
      if (state.draft.personas.includes(event.persona)) return state;
      return edited(state, { ...state.draft, personas: [...state.draft.personas, event.persona] });
    }
    case "remove":
      if (!state.draft.personas.includes(event.persona)) return state;
      return edited(state, { ...state.draft, personas: state.draft.personas.filter((p) => p !== event.persona) });
    case "swap": {
      if (!state.draft.personas.includes(event.from) || event.from === event.to) return state;
      const next = state.draft.personas.map((p) => (p === event.from ? event.to : p));
      return edited(state, { ...state.draft, personas: [...new Set(next)] });
    }
    case "set-role":
      if (state.draft.role === event.role) return state;
      return edited(state, { ...state.draft, role: event.role });
    case "set-dependents":
      if (state.draft.updateDependents === event.value) return state;
      return edited(state, { ...state.draft, updateDependents: event.value });
    case "set-allow-role-orphaning":
      if (state.draft.allowRoleOrphaning === event.value) return state;
      return edited(state, { ...state.draft, allowRoleOrphaning: event.value });
    case "set-allow-uncontained":
      if (state.draft.allowUncontained === event.value) return state;
      return edited(state, { ...state.draft, allowUncontained: event.value });
    case "preview-start": {
      const seq = state.seq + 1;
      return { ...state, seq, phase: { kind: "previewing", seq, draftKey: draftKey(state.draft) } };
    }
    case "preview-ok":
    case "preview-failed": {
      // Stale answers are dropped: an older request, or a draft since edited.
      if (state.phase.kind !== "previewing") return state;
      if (event.seq !== state.phase.seq || event.draftKey !== draftKey(state.draft)) return state;
      return event.type === "preview-ok"
        ? { ...state, phase: { kind: "previewed", draftKey: event.draftKey, plan: event.plan } }
        : { ...state, phase: { kind: "preview-failed", draftKey: event.draftKey, message: event.message } };
    }
    case "confirm":
      if (!canConfirm(state) || state.phase.kind !== "previewed") return state;
      return { ...state, phase: { kind: "applying", draftKey: state.phase.draftKey, plan: state.phase.plan } };
    case "reset":
      return initialPersonaChange(event.baseline);
    default:
      return state;
  }
}

/** What the host runs now, per persona: sent back UNCHANGED for a persona the
 *  draft keeps, so keeping it is a no-op (S2 -- a `null` seed would re-drive it
 *  as changed). */
export type RecordedSettings = ReadonlyMap<string, { schedule: string | null; seed: unknown }>;

export function recordedSettings(personas: readonly PersonaInstance[]): RecordedSettings {
  return new Map(personas.map((p) => [p.persona, { schedule: p.schedule, seed: p.seed ?? null }]));
}

/** The request body for a draft (plan and PUT take the same one; the PUT adds
 *  `expect_plan`, the fingerprint of the plan the operator confirmed). */
export function requestFor(
  draft: PersonaDraft,
  recorded: RecordedSettings = new Map(),
  expectPlan: string | null = null,
): PersonaChangeRequest {
  const assignments: PersonaAssignment[] = [...new Set(draft.personas)].map((persona) => ({
    persona,
    schedule: recorded.get(persona)?.schedule ?? null,
    seed: recorded.get(persona)?.seed ?? null,
    intensity: null,
  }));
  return {
    assignments,
    role: draft.role,
    update_dependents: draft.updateDependents,
    allow_uncontained: draft.allowUncontained,
    allow_role_orphaning: draft.allowRoleOrphaning,
    ...(expectPlan ? { expect_plan: expectPlan } : {}),
  };
}

// ---- plain words -------------------------------------------------------------
const REFUSAL_HEADLINES: Readonly<Record<string, string>> = {
  "os-mismatch": "A persona does not run on this host's operating system.",
  "unknown-persona": "The server does not know one of these personas.",
  resolve: "The change cannot be worked out against the rest of the twin.",
  "replay-host": "This host replays a recorded capture; change it through the whole traffic profile instead.",
  "last-holder":
    "This role change would leave no host holding a role that other hosts' personas need, so they would have nobody to talk to.",
  "instance-collision": "A persona on this host would get the same name as one on another host, so it cannot be told apart.",
  containment: "The twin's router is not holding traffic inside the twin (it is not in its contained phase), so new traffic is refused.",
};

/** One refusal in plain words, with the server's own message beneath it. */
export function refusalText(refusal: Refusal): { headline: string; detail: string } {
  return {
    headline: REFUSAL_HEADLINES[refusal.code] ?? "The server refused this change.",
    detail: refusal.message,
  };
}

export interface PlanSummaryLines {
  start: string[];
  stop: string[];
  keep: string[];
  /** Other hosts' personas re-driven because this host's role changes and
   *  the operator opted in (`reason: dependent`), as `host: persona`. */
  alsoRestarted: string[];
  otherHosts: number | null;
  otherInstances: number | null;
}

/** What a plan does, in the lists the preview shows. On THIS host: a
 *  `re-assert` start is a persona it keeps (re-driven, which is convergent --
 *  no change), so it is listed as kept, not started; `changed` is a restart
 *  with new settings (persona, schedule, seed or peers). A `dependent` start
 *  -- or any start on another host -- is listed separately, by host. */
export function summarizePlan(plan: PersonaPlan): PlanSummaryLines {
  const here = (s: { host_id?: string }): boolean => s.host_id === undefined || s.host_id === plan.host_id;
  const mine = plan.start.filter((s) => here(s) && s.reason !== "dependent");
  return {
    start: mine.filter((s) => s.reason !== "re-assert").map((s) => (s.reason === "changed" ? `${s.persona} (restarted with new settings)` : s.persona)),
    stop: plan.stop.filter(here).map((s) => s.persona),
    keep: mine.filter((s) => s.reason === "re-assert").map((s) => s.persona),
    alsoRestarted: plan.start.filter((s) => !here(s) || s.reason === "dependent").map((s) => `${s.host_id ?? "?"}: ${s.persona}`),
    otherHosts: typeof plan.unchanged?.hosts === "number" ? plan.unchanged.hosts : null,
    otherInstances: typeof plan.unchanged?.instances === "number" ? plan.unchanged.instances : null,
  };
}

// ---- what changed between two plans (M4) --------------------------------------
/** In words, how a fresh plan differs from the one the operator confirmed --
 *  shown when the server refused the confirm because the twin moved. */
export function planChanges(before: PersonaPlan, after: PersonaPlan): string[] {
  const a = summarizePlan(before);
  const b = summarizePlan(after);
  const out: string[] = [];
  const diff = (label: string, x: string[], y: string[]): void => {
    const added = y.filter((v) => !x.includes(v));
    const gone = x.filter((v) => !y.includes(v));
    if (added.length) out.push(`${label} now also: ${added.join(", ")}`);
    if (gone.length) out.push(`${label} no longer: ${gone.join(", ")}`);
  };
  diff("Starts", a.start, b.start);
  diff("Stops", a.stop, b.stop);
  diff("Unchanged", a.keep, b.keep);
  diff("Other hosts restarted", a.alsoRestarted, b.alsoRestarted);
  if (a.otherHosts !== b.otherHosts) out.push(`Other hosts untouched: ${a.otherHosts ?? "?"} → ${b.otherHosts ?? "?"}`);
  if (before.role.new !== after.role.new) out.push(`Role: ${before.role.new ?? "none"} → ${after.role.new ?? "none"}`);
  const refused = after.refusals.map((r) => refusalText(r).headline);
  if (refused.length) out.push(`Now refused: ${refused.join(" ")}`);
  return out.length > 0 ? out : ["The server's plan changed in a detail this summary does not show (for example a peer address)."];
}

// ---- the result, honestly (M2) -------------------------------------------------
export interface Diagnostic {
  level: string;
  message: string;
}

export interface ResultWords {
  tone: "good" | "warn" | "bad";
  headline: string;
  problems: string[];
}

/**
 * The result line of an apply. "Applied." is said ONLY when the apply
 * completed, recorded no error diagnostic, and every persona it meant to stop
 * is stopped. A stop that failed reads as "could not stop X -- it may still be
 * running", never as success.
 */
export function resultWords(
  outcome: string,
  diagnostics: readonly Diagnostic[],
  personas: readonly PersonaInstance[],
): ResultWords {
  const problems: string[] = [];
  for (const p of personas) {
    if (p.stop_error) problems.push(`Could not stop ${p.persona} — it may still be running (${p.stop_error.message}).`);
  }
  for (const d of diagnostics) if (d.level === "error") problems.push(d.message);
  if (outcome !== "completed") {
    return { tone: "bad", headline: `The apply ended ${outcome}.`, problems };
  }
  return problems.length > 0
    ? { tone: "warn", headline: "Applied, with problems:", problems }
    : { tone: "good", headline: "Applied.", problems };
}

/** The newest host-edit job for a host among a tfc deploy's `host_edits[]`,
 *  preferring the one whose fingerprint is the executed plan's. */
export function jobFor(jobs: readonly HostEditJob[] | undefined, hostId: string, fingerprint: string | null): HostEditJob | null {
  const mine = (jobs ?? []).filter((j) => j.host_id === hostId);
  const exact = fingerprint ? mine.find((j) => j.fingerprint === fingerprint) : undefined;
  if (exact) return exact;
  // Without an exact match, a superseded job is history, never "this apply".
  return [...mine.filter((j) => !j.superseded)].sort((x, y) => (y.started_at ?? 0) - (x.started_at ?? 0))[0] ?? null;
}

