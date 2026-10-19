/**
 * Change ONE host's personas without touching the rest of the twin (ruling
 * §3.3, the scalpel): add / remove / swap from a picker filtered to the
 * host's OS, optionally change its role, then Preview -> Confirm -> progress
 * -> result (rule 6). The flow's rules live in `model/personaChange.ts`; this
 * is its screen.
 */
import { useEffect, useMemo, useReducer, useRef, useState, type ReactNode } from "react";

import { asApiError } from "../../api/hooks";
import { useRuntimeApi } from "../../api/runtimeContext";
import { usePersonas } from "../../api/runtimeHooks";
import type { PersonaInfo, PersonaPlan, ProfileExport, Refusal } from "../../api/runtimeTypes";
import type { DeployedHost, DeployedTwin } from "../../model/deployed";
import { plural } from "../../model/format";
import {
  canConfirm,
  draftKey,
  initialPersonaChange,
  isBusy,
  isDirty,
  jobFor,
  personaChangeReducer,
  planChanges,
  recordedSettings,
  refusalText,
  requestFor,
  resultWords,
  summarizePlan,
  type PersonaDraft,
} from "../../model/personaChange";
import { LockIcon, PROTECTED_REASON, ProtectedBadge, ProtectionOverride } from "../shell/ProtectionOverride";
import { useWriteAccess } from "../shell/writeAccess";
import { personaSentence } from "../../model/personaWords";
import { PersonaLine } from "./PersonaLine";
import { RecordedTag, StatusBadge } from "../shell/parts";

const POLL_MS = 2000;

/** Why a persona cannot go on this host, or `null` when it can. */
export function incompatibility(persona: PersonaInfo, hostOs: string | null): string | null {
  if (!hostOs) return null; // unknown OS: the server's check decides, and says so
  if (!persona.os_family || persona.os_family === "any") return null;
  return persona.os_family === hostOs ? null : `runs on ${persona.os_family}; this host is ${hostOs}`;
}

export function PersonaEditor(props: {
  model: DeployedTwin;
  host: DeployedHost;
  target: string | null;
  /** The twin is on the server's protected list: the apply is disabled until
   *  the operator overrides protection for this change. */
  protectedTwin?: boolean;
  /** The page's override of protection (typed once, on the locked control). */
  override?: string | null;
  onOverride?: (override: string | null) => void;
  onDone: () => void;
  onClose: () => void;
}): JSX.Element {
  const { model, host, target } = props;
  const api = useRuntimeApi();
  const access = useWriteAccess();
  const catalog = usePersonas(model.twinId);
  const [state, dispatch] = useReducer(personaChangeReducer, host.personaIds, initialPersonaChange);
  const [pick, setPick] = useState("");
  const [progress, setProgress] = useState<{ ops: Record<string, number>; status: string } | null>(null);
  const [exported, setExported] = useState<ProfileExport | null>(null);
  const [exportError, setExportError] = useState<string | null>(null);
  const [applyWarnings, setApplyWarnings] = useState<string[]>([]);
  // The page owns the override (so the host panel's locked button and this
  // editor agree); a standalone editor keeps its own.
  const [ownOverride, setOwnOverride] = useState<string | null>(props.override ?? null);
  const override = props.onOverride ? (props.override ?? null) : ownOverride;
  const setOverride = props.onOverride ?? setOwnOverride;
  // The server can say a twin is protected even when the page did not know
  // (a 423): from then on the override step is shown.
  const [lockedByServer, setLockedByServer] = useState(false);
  const isProtected = !!props.protectedTwin || lockedByServer;
  const alive = useRef(true);
  useEffect(() => () => void (alive.current = false), []);

  const byId = useMemo(() => {
    const out = new Map<string, PersonaInfo>();
    for (const p of catalog.data?.personas ?? []) out.set(p.id, p);
    return out;
  }, [catalog.data]);
  const options = useMemo(
    () =>
      [...(catalog.data?.personas ?? [])]
        .sort((a, b) => a.id.localeCompare(b.id))
        .map((p) => ({ p, why: incompatibility(p, host.raw.os) })),
    [catalog.data, host.raw.os],
  );
  const roles = useMemo(() => [...new Set(model.hosts.map((h) => h.raw.role).filter((r): r is string => !!r))].sort(), [model.hosts]);
  // Each kept persona goes back with its RECORDED schedule and seed, so keeping
  // it changes nothing (S2). Taken from the host as the editor opened.
  const recorded = useMemo(() => recordedSettings(host.personas), [host.personas]);

  const phase = state.phase;
  const busy = isBusy(phase);

  /** `draft` is the draft the preview is FOR -- passed explicitly when an
   *  opt-in toggle re-previews in the same event that changed it. */
  const preview = async (draft: PersonaDraft = state.draft): Promise<void> => {
    const key = draftKey(draft);
    const seq = state.seq + 1;
    dispatch({ type: "preview-start" });
    try {
      const plan = await api.planHostPersonas(model.deployId, host.id, target, requestFor(draft, recorded));
      dispatch({ type: "preview-ok", seq, draftKey: key, plan });
    } catch (cause) {
      dispatch({ type: "preview-failed", seq, draftKey: key, message: asApiError(cause).message });
    }
  };

  // A double click lands twice before React re-renders, when both clicks
  // still see the "previewed" state -- so the guard is a ref, not the state.
  const submitting = useRef(false);
  const confirm = async (): Promise<void> => {
    if (submitting.current || !canConfirm(state) || state.phase.kind !== "previewed") return;
    if (isProtected && override === null) return;
    submitting.current = true;
    // The fingerprint of the plan the operator READ: the server refuses (409
    // plan-changed) if the plan it computes under the lease is another (M4).
    const expect = state.phase.plan.fingerprint ?? null;
    dispatch({ type: "confirm" });
    try {
      const res = await access.runWrite(`Changing personas on ${host.id}`, () =>
        api.putHostPersonas(model.deployId, host.id, target, requestFor(state.draft, recorded, expect), isProtected ? override : null),
      );
      setApplyWarnings(res.warnings ?? []);
      dispatch({ type: "apply-ok", tfcDeployId: res.tfc_deploy_id, plan: res.plan ?? null });
    } catch (cause) {
      const error = asApiError(cause);
      const raw = (error.detail && typeof error.detail === "object" ? error.detail : {}) as {
        code?: string;
        plan?: PersonaPlan;
      };
      if (raw.code === "plan-changed" && raw.plan) {
        dispatch({ type: "plan-changed", plan: raw.plan });
        return;
      }
      if (error.kind === "locked" || raw.code === "protected") {
        setLockedByServer(true);
        setOverride(null);
        dispatch({
          type: "apply-failed",
          code: "protected",
          message: "This twin is protected, so the server refused the change. Override protection below to apply it anyway.",
        });
        return;
      }
      // The PUT's refusals (contract: 400 `detail.refusals`, 409
      // `detail.refusals[code=containment]`, 409 `detail.held_by_deploy_id`).
      const detail = (error.detail && typeof error.detail === "object" ? error.detail : {}) as {
        refusals?: Refusal[];
        held_by_deploy_id?: string;
      };
      const refusals = Array.isArray(detail.refusals) ? detail.refusals : [];
      const code = refusals.some((x) => x.code === "containment")
        ? "containment"
        : typeof detail.held_by_deploy_id === "string"
          ? "busy"
          : (refusals[0]?.code ?? null);
      const message =
        code === "busy"
          ? `Another traffic change on this twin is running (${detail.held_by_deploy_id}). Wait for it to finish, then preview again.`
          : refusals.length > 0
            ? refusals.map((x) => `${refusalText(x).headline} (${x.message})`).join(" ")
            : error.message;
      dispatch({ type: "apply-failed", message, code });
    } finally {
      submitting.current = false;
    }
  };

  // Progress: poll the tfc deploy until it leaves `running` (LES-274: the
  // stale guard gates only the state writes, never the request itself).
  const runningId = phase.kind === "running" ? phase.tfcDeployId : null;
  const runningFingerprint = phase.kind === "running" ? (phase.plan.fingerprint ?? null) : null;
  useEffect(() => {
    if (!runningId) return;
    let stop = false;
    let timer: number | undefined;
    const tick = async (): Promise<void> => {
      try {
        const d = await api.getDeploy(runningId, target);
        if (stop) return;
        setProgress({ ops: d.counts, status: d.deploy.status });
        // This host edit's own job record is the authority (its status and
        // diagnostics, M2); the tfc deploy's status is the fallback for a
        // server that keeps none.
        const job = jobFor(d.host_edits, host.id, runningFingerprint);
        const status = job ? job.status : d.deploy.status;
        if (status !== "running") {
          const failed = d.ops.filter((o) => o.status === "failed");
          dispatch({
            type: "finished",
            outcome: status,
            detail: failed.length > 0 ? failed.map((o) => `${o.op_name}: ${o.attempts.at(-1)?.error_message ?? "failed"}`).join("; ") : null,
            diagnostics: job?.diagnostics ?? [],
          });
          props.onDone();
          return;
        }
      } catch (cause) {
        if (stop) return;
        setProgress((p) => ({ ops: p?.ops ?? {}, status: `could not read progress (${asApiError(cause).message}); retrying` }));
      }
      timer = window.setTimeout(() => void tick(), POLL_MS);
    };
    void tick();
    return () => {
      stop = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
    // `props.onDone` is a fresh closure each render; the poll is keyed on the deploy.
  }, [runningId, runningFingerprint, api, target, host.id]);

  const doExport = async (): Promise<void> => {
    setExportError(null);
    try {
      const doc = await api.exportProfile(model.deployId, target);
      if (alive.current) setExported(doc);
    } catch (cause) {
      if (alive.current) setExportError(asApiError(cause).message);
    }
  };

  const draft = state.draft;
  const currentRole = host.raw.role;
  const current = phase.kind === "previewed" || phase.kind === "applying" || phase.kind === "running" || phase.kind === "done" || phase.kind === "apply-failed" ? phase.plan : null;
  // While an opt-in re-previews, the plan it replaces stays on screen, marked
  // as refreshing, so the checkbox the operator just used does not vanish.
  const lastShown = useRef<PersonaPlan | null>(null);
  if (current) lastShown.current = current;
  if (phase.kind === "editing" || phase.kind === "preview-failed") lastShown.current = null;
  const refreshing = phase.kind === "previewing" && lastShown.current !== null;
  const summary = current ?? (refreshing ? lastShown.current : null);
  const lines = summary ? summarizePlan(summary) : null;
  const executed = phase.kind === "running" || phase.kind === "done";
  const reopt = (next: PersonaDraft): void => void preview(next);

  return (
    <div className="panel persona-editor" data-testid="persona-editor">
      <div className="panel-head">
        <h2>
          Change personas on <span className="mono">{host.id}</span>
          {isProtected && <ProtectedBadge />}
        </h2>
        <button type="button" className="btn small" onClick={props.onClose} disabled={busy} aria-label="Close the persona editor">
          ×
        </button>
      </div>
      <p className="meta">
        Only this host changes. Every other host keeps running exactly as it is. Host OS:{" "}
        <strong>{host.raw.os ?? "unknown"}</strong>.
      </p>
      <p>
        Runs now <RecordedTag />:{" "}
        {host.personaIds.length > 0 ? host.personaIds.map((p) => <span key={p} className="persona-tag">{p}</span>) : <span className="meta">nothing</span>}
      </p>

      <fieldset className="persona-draft" disabled={busy}>
        <legend>Personas after the change</legend>
        {draft.personas.length === 0 && <p className="meta" data-testid="draft-empty">None — applying this stops every persona on this host.</p>}
        <ul className="persona-list">
          {draft.personas.map((id) => {
            const info = byId.get(id);
            return (
              <li key={id} data-testid="draft-persona" data-persona={id}>
                <div>
                  <span className="mono">{id}</span>
                  {!host.personaIds.includes(id) && <span className="tag">new</span>}
                </div>
                {info || catalog.loading ? (
                  <PersonaLine info={info} loading={catalog.loading} />
                ) : (
                  <div className="meta">Not in this twin&apos;s persona list.</div>
                )}
                <div className="row-actions">
                  <label>
                    <span className="sr-only">Swap {id} for</span>
                    <select
                      value=""
                      onChange={(e) => e.target.value && dispatch({ type: "swap", from: id, to: e.target.value })}
                      aria-label={`Swap ${id} for another persona`}
                    >
                      <option value="">Swap for…</option>
                      {options.map(({ p, why }) => (
                        <option key={p.id} value={p.id} disabled={why !== null || draft.personas.includes(p.id)}>
                          {p.id}
                          {why ? ` — ${why}` : ""}
                        </option>
                      ))}
                    </select>
                  </label>
                  <button type="button" className="btn small" onClick={() => dispatch({ type: "remove", persona: id })} data-testid={`remove-${id}`}>
                    Remove
                  </button>
                </div>
              </li>
            );
          })}
        </ul>
        <div className="add-row">
          <label>
            <span>Add a persona</span>
            <select value={pick} onChange={(e) => setPick(e.target.value)} data-testid="persona-picker" disabled={!catalog.data}>
              <option value="">{catalog.loading ? "loading personas…" : catalog.error ? "persona list unavailable" : "choose…"}</option>
              {options.map(({ p, why }) => (
                <option key={p.id} value={p.id} disabled={why !== null || draft.personas.includes(p.id)}>
                  {p.id}
                  {why ? ` — ${why}` : draft.personas.includes(p.id) ? " — already on" : ` — ${personaSentence(p)}`}
                  {!why && p.source === "twin" ? " (this twin's)" : ""}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            className="btn"
            disabled={!pick}
            onClick={() => {
              dispatch({ type: "add", persona: pick });
              setPick("");
            }}
            data-testid="persona-add"
          >
            Add
          </button>
        </div>
        {pick && byId.get(pick) && <PersonaLine info={byId.get(pick)} loading={false} />}
        {catalog.error && <p className="notice is-error">The persona list could not be read: {catalog.error.message}</p>}
        <label className="role-row">
          <span>Role</span>
          <select
            value={draft.role === null ? "__keep" : draft.role === "" ? "__baseline" : draft.role}
            onChange={(e) => {
              const v = e.target.value;
              dispatch({ type: "set-role", role: v === "__keep" ? null : v === "__baseline" ? "" : v });
            }}
            data-testid="role-picker"
          >
            <option value="__keep">keep “{currentRole ?? "none"}”</option>
            {host.raw.role_baseline && host.raw.role_baseline !== currentRole && (
              <option value="__baseline">back to the twin&apos;s own (“{host.raw.role_baseline}”)</option>
            )}
            {roles
              .filter((r) => r !== currentRole)
              .map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
          </select>
        </label>
      </fieldset>

      <div className="flow-actions">
        <button
          type="button"
          className="btn"
          onClick={() => void preview()}
          disabled={busy || !isDirty(state) || phase.kind === "previewing"}
          data-testid="persona-preview"
        >
          {phase.kind === "previewing" ? "Working out the change…" : "Preview"}
        </button>
        {!isDirty(state) && !busy && phase.kind !== "done" && <span className="meta">Nothing to change yet.</span>}
      </div>

      {phase.kind === "preview-failed" && (
        <p className="notice is-error" role="alert">
          The preview failed: {phase.message}
        </p>
      )}

      {lines && summary && (
        <section
          className={`preview${refreshing ? " is-refreshing" : ""}`}
          aria-label="What this change will do"
          aria-busy={refreshing}
          data-testid="persona-plan"
        >
          <h3>{executed ? "What the server is doing" : "What happens"}</h3>
          {refreshing && (
            <p className="meta" role="status" data-testid="plan-refreshing">
              Refreshing the preview for your choice…
            </p>
          )}
          {phase.kind === "previewed" && phase.previous && (
            <div className="notice" role="alert" data-testid="plan-changed">
              <p>
                <strong>The twin changed since you previewed.</strong> This is the plan the server would run now;
                confirm again if it is still what you want.
              </p>
              <ul>
                {planChanges(phase.previous, phase.plan).map((c) => (
                  <li key={c} className="changed-line">
                    {c}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {summary.refusals.length > 0 ? (
            <div className="refusals" role="alert" data-testid="refusals">
              <p>
                <strong>This change cannot be applied:</strong>
              </p>
              <ul>
                {summary.refusals.map((r) => {
                  const t = refusalText(r);
                  return (
                    <li key={`${r.code}:${r.message}`}>
                      {t.headline} <span className="meta">({t.detail})</span>
                    </li>
                  );
                })}
              </ul>
              {summary.refusals.some((r) => r.code === "last-holder") && (
                <RoleOrphanOptIn draft={draft} busy={busy} dispatch={dispatch} reopt={reopt} />
              )}
            </div>
          ) : (
            <ul className="plan-lines">
              <li data-testid="plan-start">
                <strong>Will start on {host.id}:</strong> {lines.start.length ? lines.start.join(", ") : "nothing"}
              </li>
              <li data-testid="plan-stop">
                <strong>Will stop on {host.id}:</strong> {lines.stop.length ? lines.stop.join(", ") : "nothing"}
              </li>
              <li data-testid="plan-keep">
                <strong>Unchanged on {host.id}:</strong> {lines.keep.length ? `${lines.keep.join(", ")} (restarted in place, same settings)` : "nothing"}
              </li>
              <li data-testid="plan-others">
                <strong>Other hosts:</strong>{" "}
                {lines.otherHosts === null
                  ? "the server did not say how many are untouched"
                  : `${plural(lines.otherHosts, "other host")} untouched${lines.otherInstances !== null ? ` (${plural(lines.otherInstances, "persona instance")} keep running)` : ""}`}
              </li>
              {lines.alsoRestarted.length > 0 && (
                <li data-testid="plan-dependents">
                  <strong>Also restarted on other hosts (they talk to this one):</strong> {lines.alsoRestarted.join(", ")}
                </li>
              )}
              {summary.role.changed && (
                <li>
                  <strong>Role:</strong> {summary.role.current ?? "none"} → {summary.role.new ?? "none"}
                </li>
              )}
              {summary.creates_tfc_deploy && <li>This twin has no traffic deploy yet; this creates it.</li>}
            </ul>
          )}
          {summary.dependents.length > 0 && !executed && (
            <div className="dependents" data-testid="dependents">
              {summary.dependents.some((d) => !d.no_new_holder) && (
                <p>
                  Personas on {[...new Set(summary.dependents.filter((d) => !d.no_new_holder).map((d) => d.host_id))].join(", ")}{" "}
                  talk to this host by its role; unless updated they keep using the address they were started with.
                </p>
              )}
              {summary.dependents.some((d) => d.no_new_holder) && (
                <p data-testid="no-new-holder">
                  Personas on {[...new Set(summary.dependents.filter((d) => d.no_new_holder).map((d) => d.host_id))].join(", ")}{" "}
                  need a role no host would hold after this change, so they would have <strong>nobody to talk to</strong> —
                  updating them cannot fix that.
                </p>
              )}
              <label>
                <input
                  type="checkbox"
                  checked={draft.updateDependents}
                  disabled={busy || refreshing}
                  onChange={(e) => {
                    dispatch({ type: "set-dependents", value: e.target.checked });
                    reopt({ ...draft, updateDependents: e.target.checked });
                  }}
                  data-testid="update-dependents"
                />{" "}
                Also update the {plural(new Set(summary.dependents.map((d) => d.host_id)).size, "host")} that talk to this one
              </label>
            </div>
          )}
          {summary.warnings.length > 0 && (
            <ul className="warnings">
              {summary.warnings.map((w) => (
                <li key={w} className="notice">
                  {w}
                </li>
              ))}
            </ul>
          )}
        </section>
      )}

      {phase.kind === "previewed" && canConfirm(state) && (
        <div className="confirm-box">
          {isProtected && (
            <ProtectionOverride deployId={model.deployId} value={override} onChange={setOverride} what="this persona change" />
          )}
          <p>
            Apply to <span className="mono">{host.id}</span> only, on the running twin{" "}
            <span className="mono">{model.deployId}</span>.
          </p>
          <button
            type="button"
            className={isProtected && override === null ? "btn locked" : "btn primary"}
            onClick={() => void confirm()}
            disabled={isProtected && override === null}
            data-testid="persona-confirm"
          >
            {isProtected && override === null && <LockIcon />} Apply to {host.id}
          </button>
          {isProtected && override === null && (
            <span className="lock-reason" data-testid="confirm-blocked">
              {" "}
              {PROTECTED_REASON}
            </span>
          )}
        </div>
      )}

      {(phase.kind === "applying" || phase.kind === "running") && (
        <p className="progress" role="status" data-testid="persona-progress">
          {phase.kind === "applying"
            ? "Sending the change…"
            : `Applying — traffic deploy ${phase.tfcDeployId}: ${progress?.status ?? "running"}${
                progress ? ` (${Object.entries(progress.ops).map(([k, v]) => `${v} ${k}`).join(", ")})` : ""
              }`}
        </p>
      )}

      {(phase.kind === "running" || phase.kind === "done") &&
        applyWarnings.map((w) => (
          <p key={w} className="notice">
            {w}
          </p>
        ))}
      {phase.kind === "apply-failed" && (
        <div className="notice is-error" role="alert" data-testid="persona-apply-failed">
          <p>
            <strong>Not applied.</strong> {phase.message}
          </p>
          {phase.code === "protected" && (
            <ProtectionOverride deployId={model.deployId} value={override} onChange={setOverride} what="this persona change" />
          )}
          {phase.code === "protected" && override !== null && (
            <button type="button" className="btn small" onClick={() => void preview()} data-testid="persona-repreview">
              Preview again
            </button>
          )}
          {phase.code === "containment" && (
            <label>
              <input
                type="checkbox"
                checked={draft.allowUncontained}
                onChange={(e) => dispatch({ type: "set-allow-uncontained", value: e.target.checked })}
              />{" "}
              Start it anyway, even though the twin is not contained (then Preview again)
            </label>
          )}
        </div>
      )}

      {phase.kind === "done" && (
        <ResultBlock outcome={phase.outcome} detail={phase.detail} diagnostics={phase.diagnostics} host={host}>
          <button
            type="button"
            className="btn small"
            onClick={() => {
              setOverride(null);
              dispatch({ type: "reset", baseline: host.personaIds });
            }}
          >
            Make another change
          </button>
        </ResultBlock>
      )}

      <details className="export">
        <summary>Export the running traffic profile</summary>
        <p className="meta">
          The personas every host runs now, as a traffic-profile YAML — save it into the twin&apos;s <code>tfc/</code>{" "}
          folder to keep a host edit in the twin&apos;s files.
        </p>
        <button type="button" className="btn small" onClick={() => void doExport()} data-testid="export-profile">
          Export
        </button>
        {exportError && <p className="notice is-error">{exportError}</p>}
        {exported && (
          <>
            <p className="meta">
              {plural(exported.instances, "persona instance")}
              {exported.host_edits.length ? ` · host edits: ${exported.host_edits.join(", ")}` : ""}
            </p>
            <textarea readOnly className="yaml" value={exported.yaml} rows={10} aria-label="Exported traffic profile" />
            <a
              className="btn small"
              download={`${exported.twin_id ?? model.deployId}-running.yaml`}
              href={`data:text/yaml;charset=utf-8,${encodeURIComponent(exported.yaml)}`}
            >
              Download
            </a>
          </>
        )}
      </details>
    </div>
  );
}

/** The result, honestly (M2): "Applied." only when nothing went wrong; a
 *  stop that failed reads as "could not stop X -- it may still be running",
 *  and every error the job recorded is listed. The host's personas are re-read
 *  after the apply, so the heartbeats and stop errors shown are the recorded
 *  ones -- a started persona is not necessarily a running one. */
function ResultBlock(props: {
  outcome: string;
  detail: string | null;
  diagnostics: ReadonlyArray<{ level: string; message: string }>;
  host: DeployedHost;
  children: ReactNode;
}): JSX.Element {
  const words = resultWords(props.outcome, props.diagnostics, props.host.personas);
  return (
    <div className={`notice${words.tone === "good" ? "" : words.tone === "bad" ? " is-error" : " is-warn"}`} role="status" data-testid="persona-result">
      <p>
        <strong>{words.headline}</strong> {props.detail ?? ""}
      </p>
      {words.problems.length > 0 && (
        <ul data-testid="result-problems">
          {words.problems.map((m) => (
            <li key={m}>{m}</li>
          ))}
        </ul>
      )}
      {props.diagnostics
        .filter((d) => d.level !== "error")
        .map((d) => (
          <p key={d.message} className="meta">
            {d.level}: {d.message}
          </p>
        ))}
      {props.host.personas.length === 0 ? (
        <p className="meta">This host now runs no persona.</p>
      ) : (
        <ul className="persona-list compact" data-testid="result-heartbeats">
          {props.host.personas.map((p) => (
            <li key={p.instance_id}>
              <span className="mono">{p.persona}</span> <StatusBadge status={p.status} /> ·{" "}
              {p.stop_error
                ? "could not be stopped — it may still be running"
                : `after start: ${p.heartbeat ? `${p.heartbeat.state}${p.heartbeat.reason ? ` (${p.heartbeat.reason})` : ""}` : "no heartbeat recorded"}`}
            </li>
          ))}
        </ul>
      )}
      {props.children}
    </div>
  );
}

/** The `last-holder` opt-in (S7), in plain words; toggling it re-previews. */
function RoleOrphanOptIn(props: {
  draft: PersonaDraft;
  busy: boolean;
  dispatch: (e: { type: "set-allow-role-orphaning"; value: boolean }) => void;
  reopt: (d: PersonaDraft) => void;
}): JSX.Element {
  return (
    <label className="opt-in" data-testid="role-orphaning">
      <input
        type="checkbox"
        checked={props.draft.allowRoleOrphaning}
        disabled={props.busy}
        onChange={(e) => {
          props.dispatch({ type: "set-allow-role-orphaning", value: e.target.checked });
          props.reopt({ ...props.draft, allowRoleOrphaning: e.target.checked });
        }}
      />{" "}
      Change the role anyway — the personas that need it will have nobody to talk to until a host takes the role again
    </label>
  );
}
