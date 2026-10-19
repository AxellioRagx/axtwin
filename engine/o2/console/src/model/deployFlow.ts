/**
 * The guarded deploy (ruling §2 rule 6; the Grafana plugin's "Deploy as twin"
 * behaviour, re-implemented): pick -> preview -> confirm (naming the cluster
 * and the VM count) -> progress -> result. Pure.
 *
 * Changing the source or the target DISCARDS the preview, and an answer for a
 * superseded preview is dropped, so a confirmed deploy always matches the
 * preview on screen. Confirm is a one-shot: a second click while the deploy is
 * being sent or running does nothing.
 */
import type { CatalogKind } from "./appRoutes";

export interface DeploySource {
  kind: CatalogKind;
  id: string;
}

export interface DeployPreview {
  /** VMs the deploy creates, or `null` when it cannot be known before the
   *  server plans it (a count that depends on a parameter). */
  vmCount: number | null;
  vmCountNote: string | null;
  /** Lines describing what would be built (segments, node groups, op count). */
  lines: string[];
  warnings: string[];
  /** The previewed plan's id, sent as `expect_plan_id` so the server refuses
   *  a deploy whose plan is not the one shown (GAP-098). Only where a plan was
   *  actually built (scenarios). */
  planId?: string;
  /** The parameters as the server resolved them for this plan. */
  resolvedParams?: Record<string, unknown>;
}

export type DeployPhase =
  | { kind: "idle" }
  | { kind: "previewing"; seq: number; key: string }
  | { kind: "previewed"; key: string; preview: DeployPreview }
  | { kind: "preview-failed"; key: string; message: string }
  | { kind: "confirming"; key: string; preview: DeployPreview }
  | { kind: "sending"; key: string; preview: DeployPreview }
  | { kind: "running"; deployId: string; preview: DeployPreview; warnings: string[] }
  | { kind: "done"; deployId: string; outcome: string; detail: string | null }
  | { kind: "failed"; message: string };

export interface DeployFlowState {
  source: DeploySource | null;
  target: string | null;
  /** The operator's parameter values (twins); part of what a preview is FOR. */
  params: Readonly<Record<string, string>>;
  phase: DeployPhase;
  seq: number;
}

export type DeployFlowEvent =
  | { type: "select"; source: DeploySource | null; target: string | null }
  | { type: "set-target"; target: string | null }
  | { type: "set-params"; params: Record<string, string> }
  | { type: "preview-start" }
  | { type: "preview-ok"; seq: number; key: string; preview: DeployPreview }
  | { type: "preview-failed"; seq: number; key: string; message: string }
  | { type: "ask-confirm" }
  | { type: "cancel-confirm" }
  | { type: "send" }
  | { type: "sent"; deployId: string; warnings: string[] }
  | { type: "send-failed"; message: string }
  | { type: "finished"; outcome: string; detail: string | null };

export function flowKey(
  source: DeploySource | null,
  target: string | null,
  params: Readonly<Record<string, string>> = {},
): string {
  const p = JSON.stringify(Object.keys(params).sort().map((k) => [k, params[k]]));
  return source ? `${source.kind}|${source.id}|${target ?? ""}|${p}` : "";
}

export function initialDeployFlow(source: DeploySource | null = null, target: string | null = null): DeployFlowState {
  return { source, target, params: {}, phase: { kind: "idle" }, seq: 0 };
}

export function deployBusy(phase: DeployPhase): boolean {
  return phase.kind === "sending" || phase.kind === "running";
}

export function deployFlowReducer(state: DeployFlowState, event: DeployFlowEvent): DeployFlowState {
  const key = flowKey(state.source, state.target, state.params);
  if (deployBusy(state.phase)) {
    if (event.type === "sent" && state.phase.kind === "sending") {
      return { ...state, phase: { kind: "running", deployId: event.deployId, preview: state.phase.preview, warnings: event.warnings } };
    }
    if (event.type === "send-failed" && state.phase.kind === "sending") {
      return { ...state, phase: { kind: "failed", message: event.message } };
    }
    if (event.type === "finished" && state.phase.kind === "running") {
      return { ...state, phase: { kind: "done", deployId: state.phase.deployId, outcome: event.outcome, detail: event.detail } };
    }
    return state; // nothing else moves a deploy in flight
  }
  switch (event.type) {
    case "select": {
      const same = flowKey(event.source, event.target, state.params) === key;
      return same ? state : { ...state, source: event.source, target: event.target, params: {}, phase: { kind: "idle" } };
    }
    case "set-params":
      // A parameter changes what would be built: any preview is discarded.
      if (flowKey(state.source, state.target, event.params) === key) return state;
      return { ...state, params: { ...event.params }, phase: { kind: "idle" } };
    case "set-target":
      if (event.target === state.target) return state;
      return { ...state, target: event.target, phase: { kind: "idle" } };
    case "preview-start": {
      if (!state.source) return state;
      const seq = state.seq + 1;
      return { ...state, seq, phase: { kind: "previewing", seq, key } };
    }
    case "preview-ok":
    case "preview-failed":
      if (state.phase.kind !== "previewing" || state.phase.seq !== event.seq || event.key !== key) return state;
      return event.type === "preview-ok"
        ? { ...state, phase: { kind: "previewed", key, preview: event.preview } }
        : { ...state, phase: { kind: "preview-failed", key, message: event.message } };
    case "ask-confirm":
      if (state.phase.kind !== "previewed" || state.phase.key !== key) return state;
      return { ...state, phase: { kind: "confirming", key, preview: state.phase.preview } };
    case "cancel-confirm":
      if (state.phase.kind !== "confirming") return state;
      return { ...state, phase: { kind: "previewed", key: state.phase.key, preview: state.phase.preview } };
    case "send":
      if (state.phase.kind !== "confirming" || state.phase.key !== key) return state;
      return { ...state, phase: { kind: "sending", key, preview: state.phase.preview } };
    default:
      return state;
  }
}

/**
 * How many VMs a twin config declares, from its node groups -- or `null` with
 * the reason when that cannot be known without planning (a count that is a
 * parameter, or a group only built `when` a parameter says so). Never a guess.
 */
export function declaredVmCount(nodes: ReadonlyArray<{ count: unknown; when: string | null }>): { count: number | null; note: string | null } {
  let total = 0;
  for (const n of nodes) {
    if (n.when) return { count: null, note: "some hosts are built only when a parameter says so; the server works out the count when it plans" };
    const c = typeof n.count === "number" ? n.count : typeof n.count === "string" && /^\d+$/.test(n.count) ? Number(n.count) : NaN;
    if (!Number.isInteger(c) || c < 0) {
      return { count: null, note: "a host count is a parameter; the server works out the count when it plans" };
    }
    total += c;
  }
  return { count: total, note: null };
}

/** The editable starting values of a twin's parameters: each default as a
 *  string ("" when it has none). */
export function paramDefaults(params: ReadonlyArray<{ name: string; default: unknown }>): Record<string, string> {
  const out: Record<string, string> = {};
  for (const p of params) out[p.name] = p.default === null || p.default === undefined ? "" : String(p.default);
  return out;
}

/** Only the values the operator CHANGED from the defaults are sent, so an
 *  untouched form plans exactly what `te deploy --config` would. */
export function changedParams(
  params: Readonly<Record<string, string>>,
  defaults: Readonly<Record<string, string>>,
): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [k, v] of Object.entries(params)) if (defaults[k] !== v) out[k] = v;
  return out;
}

