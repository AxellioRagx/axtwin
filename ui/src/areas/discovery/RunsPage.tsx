import { Link } from "react-router";

import { useApi } from "../../api/context";
import type { DiscoveryRun } from "../../api/types";
import { formatIso, formatTs, runProgress } from "../../model/sources";
import { ErrorLine, StatusBadge } from "../shell/parts";
import { useLoad } from "../shell/useLoad";

/** Every discovery run this API process has started, newest first. */
export function RunsPage() {
  const api = useApi();
  const { data, error, loading } = useLoad((signal) => api.listRuns(signal), []);
  return (
    <>
      <h1>Discovery runs</h1>
      <ErrorLine error={error} prefix="could not list runs" />
      {loading && !data ? <p className="muted">Loading…</p> : null}
      {data && data.runs.length === 0 ? (
        <div className="empty">No discovery run since the API started. Start one from a source.</div>
      ) : null}
      {data && data.runs.length > 0 ? (
        <table className="list">
          <thead>
            <tr>
              <th>Run</th>
              <th>Status</th>
              <th>Plugin</th>
              <th>Imprint</th>
              <th>Window</th>
              <th>Progress</th>
              <th>Created</th>
            </tr>
          </thead>
          <tbody>
            {data.runs.map((r) => (
              <tr key={r.id} data-testid="run-row">
                <td>
                  <Link to={`/discovery/runs/${encodeURIComponent(r.id)}`}>{r.id}</Link>
                </td>
                <td>
                  <StatusBadge status={r.status} />
                </td>
                <td>{r.plugin}</td>
                <td>
                  {r.status === "completed" ? (
                    <Link to={`/imprints/${encodeURIComponent(r.imprint_id)}`}>{r.imprint_id}</Link>
                  ) : (
                    r.imprint_id
                  )}
                </td>
                <td className="muted">
                  {r.ts_from !== null && r.ts_to !== null ? `${formatTs(r.ts_from)} → ${formatTs(r.ts_to)}` : "—"}
                </td>
                <td className="muted">{progressCell(r)}</td>
                <td className="muted">{formatIso(r.created_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
    </>
  );
}

function progressCell(r: DiscoveryRun): string {
  if (r.status === "completed") return "done";
  const p = runProgress(r, Date.now());
  if (!p) return "—";
  if (p.ingestDone) return r.status === "running" || r.status === "queued" ? "ingest done" : `ingest done, ${r.status}`;
  if (p.reached === null) return r.status === "running" ? "starting" : "—";
  return `${Math.round(p.fraction * 100)}% (${formatTs(p.reached)})`;
}
