import { useState } from "react";
import { Link } from "react-router";

import { useApi } from "../../api/context";
import type { DiscoveryRun, ImprintListItem } from "../../api/types";
import { formatCount, formatIso } from "../../model/sources";
import { ErrorLine, StatusBadge } from "../shell/parts";
import { useLoad } from "../shell/useLoad";
import { DeleteImprint } from "./DeleteImprint";

/** Every imprint in the catalog, with the discovery run that produced it when this
 *  API process ran it (run records live in memory, so an older bundle shows none). */
export function ImprintsPage() {
  const api = useApi();
  const imprints = useLoad((signal) => api.listImprints(signal), []);
  const runs = useLoad((signal) => api.listRuns(signal), []);
  const runOf = latestRunByImprint(runs.data?.runs ?? []);
  const [deleted, setDeleted] = useState<string[]>([]);
  const rows = (imprints.data?.imprints ?? []).filter((i) => !deleted.includes(i.id));
  const skipped = imprints.data?.skipped ?? [];
  return (
    <>
      <h1>Imprints</h1>
      <ErrorLine error={imprints.error} prefix="could not list imprints" />
      <ErrorLine error={runs.error} prefix="could not list runs" />
      {imprints.loading && !imprints.data ? <p className="muted">Loading…</p> : null}
      {imprints.data && rows.length === 0 ? <div className="empty">No imprint in the catalog. A completed discovery run installs one.</div> : null}
      {rows.length > 0 ? (
        <table className="list">
          <thead>
            <tr>
              <th>Imprint</th>
              <th>As of</th>
              <th>Hosts</th>
              <th>Segments</th>
              <th>Confidence</th>
              <th>Run</th>
              <th>PIT</th>
              <th>Apply</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((i) => {
              const run = runOf.get(i.id);
              return (
                <tr key={i.id} data-testid="imprint-row">
                  <td>
                    <Link to={`/imprints/${encodeURIComponent(i.id)}`}>{i.id}</Link>
                  </td>
                  <td className="muted">{formatIso(i.as_of) || "—"}</td>
                  <td>{formatCount(i.hosts)}</td>
                  <td>{formatCount(i.segments)}</td>
                  <td title={i.confidence_source}>{confidenceCell(i)}</td>
                  <td>
                    {run ? (
                      <>
                        <Link to={`/discovery/runs/${encodeURIComponent(run.id)}`}>{run.id}</Link> <StatusBadge status={run.status} />
                      </>
                    ) : (
                      <span className="muted">—</span>
                    )}
                  </td>
                  <td className="mono">{run?.pit_id ?? "—"}</td>
                  <td className="muted">{i.can_apply ? "yes" : i.apply_blocked_reason || "no"}</td>
                  <td>
                    <DeleteImprint id={i.id} compact onDeleted={(id) => setDeleted((d) => [...d, id])} />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      ) : null}
      {skipped.length > 0 ? (
        <>
          <h2>Not readable</h2>
          <ul className="muted">
            {skipped.map((s) => (
              <li key={s.id}>
                <span className="mono">{s.id}</span>: {s.reason}
              </li>
            ))}
          </ul>
        </>
      ) : null}
    </>
  );
}

/** The newest run per imprint id; a completed run wins over a failed retry of the same
 *  id only by being newer, which is what `listRuns` (newest first) gives. */
export function latestRunByImprint(runs: DiscoveryRun[]): Map<string, DiscoveryRun> {
  const out = new Map<string, DiscoveryRun>();
  for (const r of runs) if (!out.has(r.imprint_id)) out.set(r.imprint_id, r);
  return out;
}

function confidenceCell(i: ImprintListItem): string {
  if (i.confidence_source === "absent") return "none";
  return i.mean_confidence.toFixed(2);
}
