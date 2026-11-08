import { useEffect, useRef, useState } from "react";
import { Link } from "react-router";

import { useApi } from "../../api/context";
import type { CreateDeployResponse, DeploySummary, PlanSummary, Target } from "../../api/types";
import { useWhoami } from "../../api/whoami";
import { formatCount } from "../../model/sources";
import { DestroyControl } from "../shell/destroy";
import { ErrorLine } from "../shell/parts";
import { useLoad } from "../shell/useLoad";

/** Deploy a twin from the console (2026-11-08; the Twins list's "Deploy" used to land on a
 *  page that only printed the CLI commands). Two steps, the same ones the CLI takes: plan
 *  (pure, nothing written), then deploy pinned to that plan's id, so a twin edited in
 *  between is refused (409) rather than deployed unseen. The deploy runs detached; its row
 *  shows in the page's deploy state. A twin with a standing deploy offers no Deploy: the panel
 *  offers Destroy… instead (state-aware, never both); the deploy is changed on the canvas. */
export function DeployPanel({ id, standing, rows = [], focus, onDeployed, destroyOpen = false, onDestroyOpenChange }: { id: string; standing: DeploySummary | null; rows?: readonly DeploySummary[]; focus: boolean; onDeployed: () => void; destroyOpen?: boolean; onDestroyOpenChange?: (open: boolean) => void }) {
  const api = useApi();
  const whoami = useWhoami();
  const targets = useLoad<Target[]>((signal) => api.listTargets(signal).then((t) => t.targets), []);
  const [node, setNode] = useState<string>("");
  const [plan, setPlan] = useState<PlanSummary | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState<"plan" | "deploy" | null>(null);
  const [error, setError] = useState<{ what: "plan" | "deploy"; e: unknown } | null>(null);
  const [started, setStarted] = useState<CreateDeployResponse | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (focus) ref.current?.scrollIntoView?.({ block: "start" });
  }, [focus]);

  const target = targets.data?.find((t) => t.default) ?? targets.data?.[0] ?? null;
  const nodes = target?.nodes?.length ? target.nodes : target ? [target.node] : [];
  const chosen = node || nodes[0] || "";
  // A plan answers for one node; picking another drops it.
  const pickNode = (n: string) => {
    setNode(n);
    setPlan(null);
    setConfirming(false);
  };

  async function doPlan() {
    setBusy("plan");
    setError(null);
    setConfirming(false);
    setStarted(null);
    try {
      setPlan(await api.planTwin(id, chosen ? { target_node: chosen } : {}));
    } catch (e) {
      setPlan(null);
      setError({ what: "plan", e });
    } finally {
      setBusy(null);
    }
  }
  async function doDeploy() {
    if (!plan) return;
    setBusy("deploy");
    setError(null);
    try {
      const r = await api.deployTwin(id, { expect_plan_id: plan.plan_id, ...(chosen ? { target_node: chosen } : {}) });
      setStarted(r);
      setPlan(null);
      setConfirming(false);
      onDeployed();
    } catch (e) {
      setError({ what: "deploy", e });
    } finally {
      setBusy(null);
    }
  }

  const where = target ? `${target.name ?? target.node}${chosen && chosen !== (target.name ?? target.node) ? ` / ${chosen}` : ""}` : null;
  return (
    <div className="panel" ref={ref} aria-label="Deploy the twin" data-testid="deploy-panel">
      <h2 style={{ marginTop: 0 }}>{standing ? "Standing deploy" : "Deploy"}</h2>
      {started ? (
        <p role="status" data-testid="deploy-started">
          Deploy <span className="mono">{started.deploy_id}</span> started ({started.status}
          {started.detached ? ", running in the background" : ""}). Its progress shows in the deploy state above; the tap and traffic tiers follow on the canvas.
        </p>
      ) : null}
      {standing ? (
        <>
          <p className="muted" data-testid="deploy-blocked">
            A deploy stands on this twin (<span className="mono">{standing.deploy_id}</span>, {standing.status}). Change it on the <Link to={`/canvas/${encodeURIComponent(id)}`}>design canvas</Link> (Apply, Persist, Redeploy), or Destroy it to deploy again.
          </p>
          {whoami.can_write ? (
            <div className="row-actions">
              <DestroyControl twinId={id} deploy={standing} rows={rows} onDestroyed={onDeployed} open={destroyOpen} onOpenChange={onDestroyOpenChange} />
            </div>
          ) : null}
        </>
      ) : !whoami.can_write ? (
        <p className="muted" data-testid="deploy-blocked">
          Deploying needs an account that may write; sign in to deploy.
        </p>
      ) : (
        <>
          <p className="muted">
            Plan first: nothing is written and no VM is touched. The deploy then runs exactly that plan
            {where ? (
              <>
                {" "}
                on <b>{where}</b>
              </>
            ) : null}
            .
          </p>
          {targets.error ? <p className="muted">The targets could not be listed; the server deploys to its default one.</p> : null}
          <div className="row-actions">
            {nodes.length > 1 ? (
              <label>
                Node{" "}
                <select aria-label="Target node" value={chosen} onChange={(e) => pickNode(e.target.value)} disabled={busy !== null}>
                  {nodes.map((n) => (
                    <option key={n} value={n}>
                      {n}
                    </option>
                  ))}
                </select>
              </label>
            ) : null}
            <button type="button" onClick={() => void doPlan()} disabled={busy !== null}>
              {busy === "plan" ? "Planning…" : plan ? "Plan again" : "Plan the deploy"}
            </button>
          </div>
          {plan ? (
            <div data-testid="deploy-plan">
              <p>
                Plan <span className="mono">{plan.plan_id}</span>: {formatCount(plan.vm_count)} VM{plan.vm_count === 1 ? "" : "s"}, {formatCount(plan.ops)} operations.
              </p>
              {plan.config_warnings.length ? (
                <details>
                  <summary>
                    {plan.config_warnings.length} note{plan.config_warnings.length === 1 ? "" : "s"} from the deploy gate (what it leaves out and why)
                  </summary>
                  <ul className="muted mono">
                    {plan.config_warnings.map((w, i) => (
                      <li key={i}>{w}</li>
                    ))}
                  </ul>
                </details>
              ) : null}
              <div className="row-actions">
                {confirming ? (
                  <>
                    <button className="danger" type="button" onClick={() => void doDeploy()} disabled={busy !== null}>
                      {busy === "deploy" ? "Starting…" : `Confirm: create ${formatCount(plan.vm_count)} VMs${where ? ` on ${where}` : ""}`}
                    </button>
                    <button type="button" onClick={() => setConfirming(false)} disabled={busy !== null}>
                      Keep
                    </button>
                  </>
                ) : (
                  <button className="primary" type="button" onClick={() => setConfirming(true)} disabled={busy !== null}>
                    Deploy this plan
                  </button>
                )}
              </div>
            </div>
          ) : null}
        </>
      )}
      <ErrorLine error={error?.e} prefix={`could not ${error?.what ?? "plan"}`} />
    </div>
  );
}
