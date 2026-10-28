import { useState } from "react";
import { Link } from "react-router";

import { useApi } from "../../api/context";
import { formatCount, formatTs } from "../../model/sources";
import { ConfirmDelete } from "../shell/ConfirmDelete";
import { ErrorLine } from "../shell/parts";
import { useLoad } from "../shell/useLoad";

/** Every imprint: what discovery produced, in the API's IR store, each with its operator
 *  state and the twins cast from it. */
export function ImprintsPage() {
  const api = useApi();
  const imprints = useLoad((signal) => api.listImprints(signal), []);
  const [deleted, setDeleted] = useState<string[]>([]);
  const rows = (imprints.data?.pits ?? []).filter((i) => !deleted.includes(i.pit_id));
  return (
    <>
      <h1>Imprints</h1>
      <ErrorLine error={imprints.error} prefix="could not list imprints" />
      {imprints.data?.warnings?.length ? (
        <div className="notice" role="status">
          {imprints.data.warnings.join(" ")}
        </div>
      ) : null}
      {imprints.loading && !imprints.data ? <p className="muted">Loading…</p> : null}
      {imprints.data && rows.length === 0 ? <div className="empty">No imprint yet. A completed discovery run bakes one.</div> : null}
      {rows.length > 0 ? (
        <table className="list">
          <thead>
            <tr>
              <th>Imprint</th>
              <th>As of</th>
              <th>Entities</th>
              <th>Segments</th>
              <th>Windows</th>
              <th>Derived from</th>
              <th>Assertions</th>
              <th>Twins</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((i) => (
              <tr key={i.pit_id} data-testid="imprint-row">
                <td>
                  <Link to={`/imprints/${encodeURIComponent(i.pit_id)}`} title={i.pit_id}>
                    {i.name}
                  </Link>
                </td>
                <td className="muted">{i.as_of != null ? formatTs(i.as_of) : "—"}</td>
                <td>{formatCount(i.entities)}</td>
                <td>{formatCount(i.segments)}</td>
                <td>{formatCount(i.windows)}</td>
                <td className="mono">
                  {i.derived_from ? <Link to={`/imprints/${encodeURIComponent(i.derived_from)}`}>{i.derived_from}</Link> : <span className="muted">—</span>}
                </td>
                <td className="muted">{i.has_assertions ? "yes" : "none"}</td>
                <td>
                  {i.twins.length ? i.twins.map((t, k) => (
                    <span key={t.id}>
                      {k ? ", " : ""}
                      <Link to={`/twins/${encodeURIComponent(t.id)}`}>{t.id}</Link>
                    </span>
                  )) : <span className="muted">—</span>}
                </td>
                <td>
                  <ConfirmDelete label="Delete imprint" target={i.name} compact onDelete={() => api.deleteImprint(i.pit_id)} onDeleted={() => setDeleted((d) => [...d, i.pit_id])} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
    </>
  );
}
