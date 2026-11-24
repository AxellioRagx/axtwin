import { useEffect } from "react";

import { useApi } from "../../api/context";
import type { DeployOp, TrafficLineStatus } from "../../api/types";
import { formatCount } from "../../model/sources";
import { ErrorLine } from "./parts";
import { useLoad } from "./useLoad";

/** The tfc deploy's traffic lines, live (design `docs/design/traffic-generation-trex-astf.md` §8.2-8.3,
 *  canvas task item 6): the planned generators with their ops, and each line's counters as its
 *  generator reports them (`GET /api/tfc`), re-read every `pollMs` while shown. The real VMs'
 *  persona ops are counted, not listed. Read-only. */
export function TrafficLive({ tfcDeployId, pollMs = 10_000 }: { tfcDeployId: string; pollMs?: number }) {
  const api = useApi();
  const detail = useLoad((signal) => api.getDeploy(tfcDeployId, signal), [tfcDeployId]);
  const status = useLoad((signal) => api.getTfcStatus(tfcDeployId, signal), [tfcDeployId]);
  const reloadDetail = detail.reload;
  const reloadStatus = status.reload;
  useEffect(() => {
    if (!pollMs) return;
    const t = setInterval(() => {
      reloadDetail();
      reloadStatus();
    }, pollMs);
    return () => clearInterval(t);
  }, [pollMs, reloadDetail, reloadStatus]);

  const ops = detail.data?.ops ?? [];
  const gens = new Map<string, DeployOp[]>();
  for (const op of ops) {
    const g = op.scope?.["generator"] ?? (op.scope?.["host"]?.startsWith("gen-") ? op.scope["host"] : null);
    if (g) gens.set(g, [...(gens.get(g) ?? []), op]);
  }
  const personaOps = ops.filter((o) => o.op_name === "drive-persona");
  const lines = Object.entries(status.data?.lines ?? {});
  return (
    <div className="cv-traffic-live" data-testid="traffic-live">
      <h4>
        Traffic <span className="muted">· tfc {status.data?.status ?? detail.data?.deploy.status ?? "…"}</span>
      </h4>
      {detail.error ? <ErrorLine error={detail.error} prefix="could not read the tfc deploy" /> : null}
      <p className="muted" data-testid="traffic-live-summary">
        {gens.size} generator{gens.size === 1 ? "" : "s"} · {personaOps.filter((o) => o.status === "completed").length}/{personaOps.length} real persona ops completed
      </p>
      {gens.size ? (
        <ul className="cv-legs" data-testid="generator-ops">
          {[...gens.entries()].map(([g, gops]) => {
            const done = gops.filter((o) => o.status === "completed").length;
            const failed = gops.filter((o) => o.status === "failed" || o.status === "halted-due-to-upstream").length;
            return (
              <li key={g} data-testid="generator" data-generator={g} className={failed ? "error" : undefined}>
                <details>
                  <summary>
                    <span className="mono">{g}</span> · {done}/{gops.length} ops completed{failed ? ` · ${failed} failed` : ""}
                  </summary>
                  <ul>
                    {gops.map((o) => (
                      <li key={o.op_id} data-testid="generator-op" data-op={o.op_name} data-status={o.status}>
                        <span className="mono">{o.op_name}</span>{o.scope?.["net"] ? <span className="muted"> {o.scope["net"]}</span> : null} · {o.status}
                      </li>
                    ))}
                  </ul>
                </details>
              </li>
            );
          })}
        </ul>
      ) : null}
      {status.error ? <ErrorLine error={status.error} prefix="could not read the traffic lines" /> : null}
      {lines.length ? (
        <table className="cv-members cv-lines" data-testid="line-counters">
          <thead>
            <tr><th>line</th><th>state</th><th>cps (planned)</th><th>cps now</th><th>established</th><th>errors</th><th>open</th><th>bytes</th></tr>
          </thead>
          <tbody>
            {lines.map(([id, l]) => <LineRow key={id} id={id} l={l} />)}
          </tbody>
        </table>
      ) : status.data ? <p className="muted">no traffic lines on this deploy</p> : null}
    </div>
  );
}

function LineRow({ id, l }: { id: string; l: TrafficLineStatus }) {
  const n = (v: unknown) => (typeof v === "number" ? formatCount(v) : "—");
  return (
    <tr data-testid="line-counter" data-line={id} title={l.error ? String(l.error) : `${l.from ?? "?"} → ${l.to ?? "?"} · ${l.mix ?? "?"} on ${l.generator ?? "?"}`}>
      <td className="mono">{id}</td>
      <td className={l.error || (typeof l.errors === "number" && l.errors > 0) ? "error" : undefined}>{l.error ? "unreadable" : (l.state ?? "—")}</td>
      <td>{n(l.rate_cps)}</td>
      <td>{typeof l.cps_now === "number" ? l.cps_now.toFixed(1) : "—"}</td>
      <td data-testid="line-established">{n(l.established)}</td>
      <td>{n(l.errors)}</td>
      <td>{n(l.open)}</td>
      <td>{n(l.bytes)}</td>
    </tr>
  );
}
