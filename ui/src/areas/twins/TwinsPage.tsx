import { useState } from "react";
import { Link } from "react-router";

import { useApi } from "../../api/context";
import type { TwinListItem } from "../../api/types";
import { formatCount, formatIso } from "../../model/sources";
import { ConfirmDelete } from "../shell/ConfirmDelete";
import { ErrorLine } from "../shell/parts";
import { useLoad } from "../shell/useLoad";

/** The twin catalog: every deployable twin definition, cast from an imprint or authored. */
export function TwinsPage() {
  const api = useApi();
  const twins = useLoad((signal) => api.listTwins(signal), []);
  const [deleted, setDeleted] = useState<string[]>([]);
  const rows = (twins.data?.imprints ?? []).filter((t) => !deleted.includes(t.id));
  const skipped = twins.data?.skipped ?? [];
  return (
    <>
      <h1>Twins</h1>
      <ErrorLine error={twins.error} prefix="could not list twins" />
      {twins.loading && !twins.data ? <p className="muted">Loading…</p> : null}
      {twins.data && rows.length === 0 ? <div className="empty">No twin in the catalog. Cast an imprint, or author one.</div> : null}
      {rows.length > 0 ? (
        <table className="list">
          <thead>
            <tr>
              <th>Twin</th>
              <th>As of</th>
              <th>Hosts</th>
              <th>Segments</th>
              <th>Confidence</th>
              <th>Cast from</th>
              <th>Apply</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((t) => (
              <tr key={t.id} data-testid="twin-row">
                <td>
                  <Link to={`/twins/${encodeURIComponent(t.id)}`}>{t.id}</Link>
                </td>
                <td className="muted">{formatIso(t.as_of) || "—"}</td>
                <td>{formatCount(t.hosts)}</td>
                <td>{formatCount(t.segments)}</td>
                <td title={t.confidence_source}>{confidenceCell(t)}</td>
                <td>
                  {t.cast_from ? (
                    <Link to={`/imprints/${encodeURIComponent(t.cast_from.pit_id)}`} title={t.cast_from.pit_id}>
                      {t.cast_from.pit_name ?? t.cast_from.pit_id}
                    </Link>
                  ) : (
                    <span className="muted">authored</span>
                  )}
                </td>
                <td className="muted">{t.can_apply ? "yes" : t.apply_blocked_reason || "no"}</td>
                <td>
                  <ConfirmDelete label="Delete twin" target={t.id} compact onDelete={() => api.deleteTwin(t.id)} onDeleted={() => setDeleted((d) => [...d, t.id])} />
                </td>
              </tr>
            ))}
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

function confidenceCell(t: TwinListItem): string {
  if (t.confidence_source === "absent") return "none";
  return t.mean_confidence.toFixed(2);
}
