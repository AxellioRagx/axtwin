import { useState } from "react";

import { useApi } from "../../api/context";
import type { CreateDeployResponse, PlanSummary, Target, ValidationFinding } from "../../api/types";
import { formatCount } from "../../model/sources";
import { missingSensor, preflightRows, sensorRef } from "../../model/preflight";
import { ErrorLine } from "./parts";
import { useLoad } from "./useLoad";

/** What holds the deploy back before a plan can mean anything (the canvas: unsaved changes), and how to clear it. */
export interface DeployHold {
  reason: string;
  action?: { label: string; run: () => void | Promise<unknown> };
}

/** The deploy dialog's body (docs/design/targets-and-lab.md §2.3), on the twin page's Deploy panel and in the canvas
 *  bar's Deploy dialog: pick the Target (its platform badge beside it), plan (pure, nothing written), read the
 *  preflight -- "images bound for <platform>: N / N", a row per sensor the tap plan names that the target's lab does
 *  not have (with a Remap select that writes the twin's tap plan through `remap`) and per image with no binding for
 *  the platform -- then deploy pinned to that plan's id, on that target. Deploy stays disabled until the preflight
 *  is clean; the server refuses an unclean one too. Changing the target re-runs the plan and its preflight. */
export function DeployFlow({
  twinId,
  onDeployed,
  remap,
  remapNote,
  hold = null,
}: {
  twinId: string;
  onDeployed: (r: CreateDeployResponse) => void;
  /** Write `to` in place of the finding's sensor (at `location`) into the twin's tap plan; the flow then re-plans. */
  remap?: (location: string, to: string) => Promise<void>;
  /** Where a Remap writes, said beside the rows. */
  remapNote?: string;
  hold?: DeployHold | null;
}) {
  const api = useApi();
  const targets = useLoad<Target[]>((signal) => api.listTargets(signal).then((t) => t.targets), []);
  const [targetName, setTargetName] = useState<string>("");
  const [node, setNode] = useState<string>("");
  const [plan, setPlan] = useState<PlanSummary | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState<"plan" | "deploy" | "remap" | null>(null);
  const [error, setError] = useState<{ what: string; e: unknown } | null>(null);

  const list = targets.data ?? [];
  const nameOf = (t: Target) => t.name ?? t.node;
  const target = list.find((t) => nameOf(t) === targetName) ?? list.find((t) => t.default) ?? list[0] ?? null;
  const tname = target ? nameOf(target) : "";
  const nodes = target?.nodes?.length ? target.nodes : target ? [target.node] : [];
  const chosen = nodes.includes(node) ? node : nodes[0] || "";
  const pf = plan?.preflight ?? null;
  const rows = preflightRows(pf);
  const clean = pf ? pf.clean : true;

  async function doPlan(t = tname, n = chosen) {
    setBusy("plan");
    setError(null);
    setConfirming(false);
    try {
      setPlan(await api.planTwin(twinId, n ? { target_node: n } : {}, undefined, t || undefined));
    } catch (e) {
      setPlan(null);
      setError({ what: "plan", e });
    } finally {
      setBusy(null);
    }
  }
  // A plan answers for one target and node: changing either re-runs it (and its preflight) when one was shown.
  const pickTarget = (name: string) => {
    setTargetName(name);
    setNode("");
    setConfirming(false);
    const t = list.find((x) => nameOf(x) === name);
    const n = t?.nodes?.[0] ?? t?.node ?? "";
    if (plan) void doPlan(name, n);
  };
  const pickNode = (n: string) => {
    setNode(n);
    setConfirming(false);
    if (plan) void doPlan(tname, n);
  };
  async function doRemap(f: ValidationFinding, to: string) {
    if (!remap || !to || !f.location) return;
    setBusy("remap");
    setError(null);
    try {
      await remap(f.location, to);
    } catch (e) {
      setError({ what: "remap", e });
      setBusy(null);
      return;
    }
    setBusy(null);
    if (!hold) await doPlan();
  }
  async function doDeploy() {
    if (!plan) return;
    setBusy("deploy");
    setError(null);
    try {
      // The whole twin, the CLI's sequence: te, then the tap plan, then the traffic profile (2026-11-09).
      const r = await api.deployTwin(twinId, { expect_plan_id: plan.plan_id, follow_on: ["tap", "tfc"], ...(chosen ? { target_node: chosen } : {}) }, tname || undefined);
      setPlan(null);
      setConfirming(false);
      onDeployed(r);
    } catch (e) {
      setError({ what: "deploy", e });
    } finally {
      setBusy(null);
    }
  }

  const where = target ? `${tname}${chosen && chosen !== tname ? ` / ${chosen}` : ""}` : null;
  return (
    <div className="deploy-flow" data-testid="deploy-flow">
      <div className="row-actions deploy-target">
        {list.length ? (
          <label>
            Target{" "}
            <select aria-label="Target" value={tname} onChange={(e) => pickTarget(e.target.value)} disabled={busy !== null}>
              {list.map((t) => (
                <option key={nameOf(t)} value={nameOf(t)}>
                  {nameOf(t)}
                </option>
              ))}
            </select>
          </label>
        ) : null}
        {target?.platform ? (
          <span className="badge platform" data-testid="deploy-platform">
            {target.platform}
          </span>
        ) : null}
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
      </div>
      {targets.error ? <p className="muted">The targets could not be listed; the server deploys to its default one.</p> : null}
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
      {hold ? (
        <p className="warn-line" data-testid="deploy-hold">
          {hold.reason}
          {hold.action ? (
            <>
              {" "}
              <button type="button" onClick={() => void hold.action!.run()} disabled={busy !== null}>
                {hold.action.label}
              </button>
            </>
          ) : null}
        </p>
      ) : null}
      <div className="row-actions">
        <button type="button" onClick={() => void doPlan()} disabled={busy !== null || Boolean(hold)}>
          {busy === "plan" ? "Planning…" : plan ? "Plan again" : "Plan the deploy"}
        </button>
      </div>
      {plan ? (
        <div data-testid="deploy-plan">
          <p>
            Plan <span className="mono">{plan.plan_id}</span>: {formatCount(plan.vm_count)} VM{plan.vm_count === 1 ? "" : "s"}, {formatCount(plan.ops)} operations.
          </p>
          {pf ? (
            <p data-testid="images-bound" className={pf.images_bound < pf.images_total ? "warn-line" : "muted"}>
              images bound for <span className="mono">{pf.platform}</span>: {pf.images_bound} / {pf.images_total}
            </p>
          ) : null}
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
          {rows.length ? (
            <div className="preflight" data-testid="preflight">
              <p className="preflight-head">
                Preflight on <b>{pf!.target}</b>: {rows.length} problem{rows.length === 1 ? "" : "s"}. Nothing deploys until it is clean.
                {remap && remapNote && rows.some((f) => f.rule_id === "TARGET-SENSOR") ? <span className="muted"> {remapNote}</span> : null}
              </p>
              <ul>
                {rows.map((f, i) => (
                  <PreflightRow key={`${f.location}-${i}`} f={f} sensors={pf!.sensors} canRemap={Boolean(remap) && sensorRef(f.location) !== null} disabled={busy !== null} onRemap={(to) => void doRemap(f, to)} />
                ))}
              </ul>
            </div>
          ) : pf ? (
            <p className="muted" data-testid="preflight-clean">
              Preflight on <b>{pf.target}</b> is clean: every sensor the tap plan names is in its lab, every image is bound for {pf.platform}.
            </p>
          ) : null}
          <div className="row-actions">
            {confirming && clean ? (
              <>
                <button className="danger" type="button" onClick={() => void doDeploy()} disabled={busy !== null}>
                  {busy === "deploy" ? "Starting…" : `Confirm: create ${formatCount(plan.vm_count)} VMs${where ? ` on ${where}` : ""}`}
                </button>
                <button type="button" onClick={() => setConfirming(false)} disabled={busy !== null}>
                  Keep
                </button>
              </>
            ) : (
              <button className="primary" type="button" onClick={() => setConfirming(true)} disabled={busy !== null || !clean || Boolean(hold)} title={!clean ? "the preflight is not clean: remap the sensors, bind the images" : undefined}>
                Deploy this plan
              </button>
            )}
          </div>
        </div>
      ) : null}
      <ErrorLine error={error?.e} prefix={`could not ${error?.what ?? "plan"}`} />
    </div>
  );
}

function PreflightRow({ f, sensors, canRemap, disabled, onRemap }: { f: ValidationFinding; sensors: string[]; canRemap: boolean; disabled: boolean; onRemap: (to: string) => void }) {
  const from = missingSensor(f);
  return (
    <li className="preflight-row" data-testid="preflight-row" data-rule={f.rule_id}>
      <span className="preflight-msg">{f.message}</span>
      {f.location ? <span className="mono muted preflight-loc">{f.location}</span> : null}
      {f.rule_id === "TARGET-SENSOR" && canRemap ? (
        <label className="preflight-remap">
          Remap <span className="mono">{from}</span> →{" "}
          <select aria-label={`Remap ${from ?? "sensor"}`} value="" disabled={disabled || sensors.length === 0} onChange={(e) => onRemap(e.target.value)}>
            <option value="">{sensors.length ? "pick a lab sensor" : "the target's lab has no sensor"}</option>
            {sensors.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
      ) : f.rule_id === "TARGET-IMAGE" ? (
        <span className="muted">bind the image for this platform (images/bindings/&lt;platform&gt;/), or pick another target</span>
      ) : null}
    </li>
  );
}
