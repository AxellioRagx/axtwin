/**
 * The Catalog (ruling §2): every twin in `twins/` and every scenario, with the
 * cluster each is meant for, and the ONE deploy flow -- guarded, previewed,
 * confirmed by name (rule 6).
 *
 * Validate / Plan are offered where the API supports the source kind
 * (scenarios) and shown disabled WITH the reason where it does not (twins: the
 * server validates and plans a twin itself, before it creates anything, when
 * you deploy it).
 */
import { useEffect, useMemo, useReducer, useRef, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router";

import { asApiError } from "../../api/hooks";
import { useResource } from "../../api/hooks";
import { useRuntimeApi } from "../../api/runtimeContext";
import { useScenarios, useTargets, useTwinConfigs } from "../../api/runtimeHooks";
import type { PlanSource, PlanSummary, TargetInfo, ValidateResult } from "../../api/runtimeTypes";
import { catalogPath, twinPath, type CatalogKind } from "../../model/appRoutes";
import {
  changedParams,
  declaredVmCount,
  deployBusy,
  deployFlowReducer,
  flowKey,
  initialDeployFlow,
  paramDefaults,
  type DeployPreview,
} from "../../model/deployFlow";
import { plural } from "../../model/format";
import { ErrorNotice } from "../shell/parts";
import { useWriteAccess } from "../shell/writeAccess";

const POLL_MS = 3000;


function targetName(t: TargetInfo): string {
  return t.name ?? t.node;
}

export function CatalogPage({ kind, id }: { kind: CatalogKind | null; id: string | null }): JSX.Element {
  const twins = useTwinConfigs();
  const scenarios = useScenarios();
  const location = useLocation();
  const requestedTarget = new URLSearchParams(location.search).get("target");

  return (
    <section className="page catalog-page" aria-labelledby="catalog-title">
      <div className="page-head">
        <h1 id="catalog-title">Catalog</h1>
        <p className="page-lede">Twins and scenarios you can deploy. Pick one to see what it builds and deploy it.</p>
      </div>
      <div className="catalog-body">
        <div className="catalog-lists">
          <section aria-labelledby="cat-twins">
            <h2 id="cat-twins">Twins</h2>
            {twins.loading && <p className="meta">…</p>}
            {twins.error && <ErrorNotice error={twins.error} what="Listing twins" />}
            {twins.data && twins.data.twin_configs.length === 0 && (
              <p className="empty">This server lists no twins (its twins directory is empty or not set).</p>
            )}
            <ul className="catalog-list">
              {twins.data?.twin_configs.map((t) => (
                <li key={t.id}>
                  <Link
                    to={catalogPath("twins", t.id, requestedTarget)}
                    className={kind === "twins" && id === t.id ? "is-current" : undefined}
                    aria-current={kind === "twins" && id === t.id ? "page" : undefined}
                    data-testid="catalog-twin"
                  >
                    <span className="mono">{t.id}</span>
                    {t.cluster ? <span className="tag">for {t.cluster}</span> : null}
                    {t.error && <span className="tag bad">cannot load</span>}
                  </Link>
                  {t.description && <p className="meta clamp">{firstLine(t.description)}</p>}
                </li>
              ))}
            </ul>
          </section>
          <section aria-labelledby="cat-scenarios">
            <h2 id="cat-scenarios">Scenarios</h2>
            {scenarios.loading && <p className="meta">…</p>}
            {scenarios.error && <ErrorNotice error={scenarios.error} what="Listing scenarios" />}
            {scenarios.data && scenarios.data.scenarios.length === 0 && <p className="empty">No scenario files.</p>}
            <ul className="catalog-list">
              {scenarios.data?.scenarios.map((s) => (
                <li key={s.id}>
                  <Link
                    to={catalogPath("scenarios", s.id, requestedTarget)}
                    className={kind === "scenarios" && id === s.id ? "is-current" : undefined}
                    aria-current={kind === "scenarios" && id === s.id ? "page" : undefined}
                  >
                    <span className="mono">{s.id}</span>
                  </Link>
                </li>
              ))}
            </ul>
          </section>
        </div>
        <div className="catalog-detail">
          {kind && id ? (
            <CatalogItem key={`${kind}|${id}`} kind={kind} id={id} requestedTarget={requestedTarget} />
          ) : (
            <p className="empty">Pick a twin or a scenario on the left.</p>
          )}
        </div>
      </div>
    </section>
  );
}

function firstLine(text: string): string {
  const line = text.trim().split(/\n\s*\n/)[0] ?? "";
  return line.replace(/\s+/g, " ");
}

function CatalogItem({ kind, id, requestedTarget }: { kind: CatalogKind; id: string; requestedTarget: string | null }): JSX.Element {
  const api = useRuntimeApi();
  const access = useWriteAccess();
  const navigate = useNavigate();
  const targets = useTargets();
  const twin = useResource((signal) => api.getTwinConfig(id, signal), [api, id], kind === "twins");
  const intended = kind === "twins" ? (twin.data?.cluster ?? null) : null;

  const targetList = targets.data?.targets ?? [];
  const defaultTarget =
    (requestedTarget && targetList.some((t) => targetName(t) === requestedTarget) ? requestedTarget : null) ??
    (intended && targetList.some((t) => targetName(t) === intended) ? intended : null) ??
    (targetList.find((t) => t.default) ? targetName(targetList.find((t) => t.default)!) : targetList[0] ? targetName(targetList[0]) : null);

  const [state, dispatch] = useReducer(deployFlowReducer, initialDeployFlow({ kind, id }, null));
  // Seed the target once the list (and the twin's intended cluster) is known.
  useEffect(() => {
    if (state.target === null && defaultTarget !== null) dispatch({ type: "set-target", target: defaultTarget });
  }, [defaultTarget, state.target]);

  const [validation, setValidation] = useState<{ result?: ValidateResult; plan?: PlanSummary; error?: string } | null>(null);
  const [checking, setChecking] = useState(false);
  const [progress, setProgress] = useState<{ counts: Record<string, number>; status: string } | null>(null);
  const sending = useRef(false);

  const phase = state.phase;
  const busy = deployBusy(phase);
  const chosen = targetList.find((t) => targetName(t) === state.target) ?? null;
  const elsewhere = intended !== null && state.target !== null && intended !== state.target;

  // Twin parameters (fix round 2): each declared one, prefilled with its
  // default; only CHANGED values are sent, and any change drops the preview.
  const defaults = useMemo(() => paramDefaults(twin.data?.params ?? []), [twin.data]);
  const values: Record<string, string> = { ...defaults, ...state.params };
  const sentParams = changedParams(values, defaults);
  const source = (): PlanSource =>
    kind === "twins"
      ? { config: id, ...(Object.keys(sentParams).length ? { params: sentParams } : {}) }
      : { scenario: id };

  const preview = async (): Promise<void> => {
    const seq = state.seq + 1;
    const forKey = flowKey(state.source, state.target, state.params);
    dispatch({ type: "preview-start" });
    try {
      // One pure plan call for every kind (`POST /api/plan` with scenario |
      // config + params): the VM count and the resolved parameters are the
      // SERVER's, and its plan_id pins the deploy (`expect_plan_id`).
      const plan = await api.plan(source());
      let vmCount: number | null = typeof plan.vm_count === "number" ? plan.vm_count : null;
      let vmCountNote: string | null = null;
      if (vmCount === null && kind === "twins" && twin.data) {
        const declared = declaredVmCount(twin.data.nodes);
        vmCount = declared.count;
        vmCountNote = declared.note;
      } else if (vmCount === null) {
        const topo = await api.getScenarioTopology(id);
        vmCount = topo.nodes.filter((n) => n.type !== "network").length;
      }
      const result: DeployPreview = {
        vmCount,
        vmCountNote,
        lines: [
          ...(kind === "twins" && twin.data ? [plural(twin.data.segments.length, "segment")] : []),
          `${plural(plan.ops, "deploy step")} in plan ${plan.plan_id}`,
        ],
        warnings: plan.config_warnings ?? [],
        planId: plan.plan_id,
        ...(plan.params ? { resolvedParams: plan.params } : {}),
      };
      dispatch({ type: "preview-ok", seq, key: forKey, preview: result });
    } catch (cause) {
      dispatch({ type: "preview-failed", seq, key: forKey, message: asApiError(cause).message });
    }
  };

  const send = async (): Promise<void> => {
    if (sending.current || phase.kind !== "confirming") return;
    sending.current = true;
    const planId = phase.preview.planId;
    dispatch({ type: "send" });
    try {
      // The SAME source and parameters the preview planned, pinned to its plan.
      const body = { ...source(), ...(planId ? { expect_plan_id: planId } : {}) };
      const res = await access.runWrite(`Deploying ${id}`, () => api.createDeploy(body, state.target));
      dispatch({ type: "sent", deployId: res.deploy_id, warnings: res.config_warnings ?? [] });
    } catch (cause) {
      const error = asApiError(cause);
      const detail = (error.detail && typeof error.detail === "object" ? error.detail : {}) as { code?: string; plan_id?: string };
      dispatch({
        type: "send-failed",
        message:
          detail.code === "plan-changed"
            ? `The plan changed since you previewed it (it is now ${detail.plan_id ?? "different"}). Nothing was deployed — preview again.`
            : error.message,
      });
    } finally {
      sending.current = false;
    }
  };

  const runningId = phase.kind === "running" ? phase.deployId : null;
  useEffect(() => {
    if (!runningId) return;
    let stop = false;
    let timer: number | undefined;
    const tick = async (): Promise<void> => {
      try {
        const d = await api.getDeploy(runningId, state.target);
        if (stop) return;
        setProgress({ counts: d.counts, status: d.deploy.status });
        if (d.deploy.status !== "running") {
          const failed = d.ops.filter((o) => o.status === "failed");
          dispatch({
            type: "finished",
            outcome: d.deploy.status,
            detail: failed.length ? failed.slice(0, 3).map((o) => `${o.op_name}: ${o.attempts.at(-1)?.error_message ?? "failed"}`).join("; ") : null,
          });
          return;
        }
      } catch {
        // A read that fails mid-deploy is retried; the deploy itself goes on.
      }
      if (!stop) timer = window.setTimeout(() => void tick(), POLL_MS);
    };
    void tick();
    return () => {
      stop = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [runningId, api, state.target]);

  const validate = async (): Promise<void> => {
    setChecking(true);
    try {
      const [result, plan] = await Promise.all([api.validate(source()), api.plan(source())]);
      setValidation({ result, plan });
    } catch (cause) {
      setValidation({ error: asApiError(cause).message });
    } finally {
      setChecking(false);
    }
  };

  const counts = useMemo(
    () => (progress ? Object.entries(progress.counts).map(([k, v]) => `${v} ${k}`).join(", ") : ""),
    [progress],
  );

  return (
    <article className="catalog-item" aria-labelledby="item-title">
      <h2 id="item-title">
        {kind === "twins" ? "Twin" : "Scenario"} <span className="mono">{id}</span>
      </h2>
      {kind === "twins" && twin.error && <ErrorNotice error={twin.error} what="Reading this twin" />}
      {kind === "twins" && twin.data?.description && <p className="description">{twin.data.description.trim()}</p>}
      {kind === "twins" && (
        <p className="meta">Intended cluster: {intended ?? "not stated in twin.yaml"}</p>
      )}

      {kind === "twins" && twin.data && twin.data.params.length > 0 && (
        <fieldset className="params" disabled={busy} data-testid="twin-params">
          <legend>Parameters</legend>
          <p className="meta">Prefilled with the twin&apos;s defaults. Changing one clears the preview.</p>
          {twin.data.params.map((p) => (
            <label key={p.name} className="field">
              <span>
                <span className="mono">{p.name}</span>
                {p.description ? <span className="meta"> — {p.description}</span> : null}
                {values[p.name] !== defaults[p.name] && <span className="tag">changed</span>}
              </span>
              <input
                value={values[p.name] ?? ""}
                placeholder={defaults[p.name] ? `default ${defaults[p.name]}` : "no default"}
                data-testid={`param-${p.name}`}
                onChange={(e) => dispatch({ type: "set-params", params: { ...values, [p.name]: e.target.value } })}
              />
            </label>
          ))}
        </fieldset>
      )}

      <section className="checks" aria-label="Validate and plan">
        <button
          type="button"
          className="btn"
          disabled={checking}
          onClick={() => void validate()}
          data-testid="validate"
        >
          {checking ? "Checking…" : "Validate and plan"}
        </button>
        {validation?.error && <p className="notice is-error">{validation.error}</p>}
        {validation?.result && (
          <div className={`notice${validation.result.disposition === "fail" ? " is-error" : ""}`}>
            <p>
              Validation: <strong>{validation.result.disposition.replace(/_/g, " ")}</strong> —{" "}
              {plural(validation.result.errors, "error")}, {plural(validation.result.warnings, "warning")}.
            </p>
            <ul>
              {validation.result.findings.slice(0, 20).map((f, i) => (
                <li key={`${f.rule_id}-${i}`}>
                  <span className="mono">{f.rule_id}</span> {f.severity}: {f.message}
                </li>
              ))}
            </ul>
            {validation.plan && <p>Plan: {plural(validation.plan.ops, "deploy step")}.</p>}
          </div>
        )}
      </section>

      <section className="deploy-flow" aria-label="Deploy">
        <h3>Deploy</h3>
        <label className="field">
          <span>Cluster</span>
          <select
            value={state.target ?? ""}
            disabled={busy || targetList.length === 0}
            onChange={(e) => dispatch({ type: "set-target", target: e.target.value || null })}
            data-testid="deploy-target"
          >
            {targetList.length === 0 && <option value="">{targets.loading ? "loading…" : "no cluster configured"}</option>}
            {targetList.map((t) => (
              <option key={targetName(t)} value={targetName(t)} disabled={!!t.error}>
                {targetName(t)}
                {t.api_url ? ` — ${t.api_url}` : ""}
                {t.error ? " (cannot load)" : ""}
              </option>
            ))}
          </select>
        </label>
        {elsewhere && (
          <p className="notice" data-testid="cluster-mismatch">
            This twin says it is meant for <strong>{intended}</strong>; you are about to deploy it to{" "}
            <strong>{state.target}</strong>.
          </p>
        )}
        <div className="flow-actions">
          <button
            type="button"
            className="btn"
            onClick={() => void preview()}
            disabled={busy || phase.kind === "previewing" || state.target === null}
            data-testid="deploy-preview"
          >
            {phase.kind === "previewing" ? "Working it out…" : "Preview"}
          </button>
        </div>
        {phase.kind === "preview-failed" && <p className="notice is-error">The preview failed: {phase.message}</p>}
        {(phase.kind === "previewed" || phase.kind === "confirming") && (
          <div className="preview" data-testid="deploy-plan">
            <p>
              <strong>
                {phase.preview.vmCount === null ? "VM count: not known until the server plans it" : plural(phase.preview.vmCount, "VM")}
              </strong>
              {phase.preview.vmCountNote ? <span className="meta"> — {phase.preview.vmCountNote}</span> : null}
            </p>
            {phase.preview.resolvedParams && Object.keys(phase.preview.resolvedParams).length > 0 && (
              <p data-testid="resolved-params">
                Parameters as planned:{" "}
                {Object.entries(phase.preview.resolvedParams)
                  .map(([k, v]) => `${k} = ${String(v)}`)
                  .join(", ")}
              </p>
            )}
            <ul>
              {phase.preview.lines.map((l) => (
                <li key={l}>{l}</li>
              ))}
            </ul>
            {phase.preview.warnings.map((w) => (
              <p key={w} className="notice">
                {w}
              </p>
            ))}
            {phase.kind === "previewed" && (
              <button type="button" className="btn primary" onClick={() => dispatch({ type: "ask-confirm" })} data-testid="deploy-open-confirm">
                Deploy…
              </button>
            )}
          </div>
        )}
        {phase.kind === "confirming" && (
          <div className="confirm-box" role="alertdialog" aria-labelledby="deploy-confirm-title" data-testid="deploy-confirm-box">
            <p id="deploy-confirm-title">
              This creates{" "}
              <strong>{phase.preview.vmCount === null ? "real VMs" : plural(phase.preview.vmCount, "real VM")}</strong> on cluster{" "}
              <strong>{state.target}</strong>
              {chosen?.api_url ? (
                <>
                  {" "}
                  (<span className="mono">{chosen.api_url}</span>)
                </>
              ) : null}
              .
            </p>
            <div className="actions">
              <button type="button" className="btn primary" onClick={() => void send()} data-testid="deploy-confirm">
                Deploy {id} to {state.target}
              </button>
              <button type="button" className="btn" onClick={() => dispatch({ type: "cancel-confirm" })}>
                Cancel
              </button>
            </div>
          </div>
        )}
        {phase.kind === "sending" && <p className="progress" role="status">Starting the deploy…</p>}
        {phase.kind === "running" && (
          <div className="progress" role="status" data-testid="deploy-progress">
            <p>
              Deploying <span className="mono">{phase.deployId}</span> — {progress?.status ?? "running"}
              {counts ? ` (${counts})` : ""}. You can leave this page; the deploy continues on the server.
            </p>
            {phase.warnings.map((w) => (
              <p key={w} className="notice">
                {w}
              </p>
            ))}
          </div>
        )}
        {phase.kind === "failed" && (
          <p className="notice is-error" role="alert">
            The deploy did not start: {phase.message}
          </p>
        )}
        {phase.kind === "done" && (
          <div className={`notice${phase.outcome === "completed" ? "" : " is-error"}`} role="status" data-testid="deploy-result">
            <p>
              <strong>{phase.outcome === "completed" ? "Deployed." : `The deploy ended ${phase.outcome}.`}</strong>{" "}
              {phase.detail ?? ""}
            </p>
            <button type="button" className="btn" onClick={() => void navigate(twinPath(phase.deployId, { target: chosen?.default ? null : state.target }))}>
              Open the twin
            </button>
          </div>
        )}
      </section>
    </article>
  );
}
