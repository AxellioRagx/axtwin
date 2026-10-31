import { Link } from "react-router";

import { useApi } from "../../api/context";
import type { TwinListItem } from "../../api/types";
import { formatCount, formatIso } from "../../model/sources";
import { ErrorLine } from "../shell/parts";
import { useLoad } from "../shell/useLoad";

/** The canvas's own front door (`/canvas`): every twin in the catalog -- cast
 *  from an imprint or authored -- opens on the design canvas; an imprint not
 *  yet cast is listed beneath, since it has no twin-config to draw until it is. */
export function DesignsPage() {
  const api = useApi();
  const twins = useLoad((signal) => api.listTwins(signal), []);
  const imprints = useLoad((signal) => api.listImprints(signal).catch(() => ({ pits: [] })), []);
  const rows = twins.data?.imprints ?? [];
  const uncast = (imprints.data?.pits ?? []).filter((p) => p.twins.length === 0);
  return (
    <>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h1>Canvas</h1>
        <Link to="/canvas/new" className="button primary" role="button">
          New design
        </Link>
      </div>
      <p className="muted">Open a cast or authored twin to view and edit it on the canvas, or start a new topology from scratch.</p>
      <ErrorLine error={twins.error} prefix="could not list twins" />
      {twins.loading && !twins.data ? <p className="muted">Loading…</p> : null}
      {twins.data && rows.length === 0 ? <div className="empty">No twin to open yet. Cast an imprint, or start a new design.</div> : null}
      {rows.length > 0 ? (
        <table className="list">
          <thead>
            <tr>
              <th>Design</th>
              <th>Origin</th>
              <th>Hosts</th>
              <th>Segments</th>
              <th>As of</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((t) => (
              <tr key={t.id} data-testid="design-row">
                <td>
                  <Link to={`/canvas/${encodeURIComponent(t.id)}`}>{t.id}</Link>
                </td>
                <td>{originCell(t)}</td>
                <td>{formatCount(t.hosts)}</td>
                <td>{formatCount(t.segments)}</td>
                <td className="muted">{formatIso(t.as_of) || "—"}</td>
                <td>
                  <Link to={`/canvas/${encodeURIComponent(t.id)}`} aria-label={`Open ${t.id} on the canvas`}>
                    Open
                  </Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
      {uncast.length > 0 ? (
        <>
          <h2>Imprints not yet cast</h2>
          <p className="muted">An imprint is drawn once it is cast into a twin; cast it from its page.</p>
          <ul>
            {uncast.map((p) => (
              <li key={p.pit_id}>
                <Link to={`/imprints/${encodeURIComponent(p.pit_id)}`}>{p.name}</Link>
              </li>
            ))}
          </ul>
        </>
      ) : null}
    </>
  );
}

function originCell(t: TwinListItem) {
  if (!t.cast_from) return <span className="muted">authored</span>;
  return (
    <>
      cast from{" "}
      <Link to={`/imprints/${encodeURIComponent(t.cast_from.pit_id)}`} title={t.cast_from.pit_id}>
        {t.cast_from.pit_name ?? t.cast_from.pit_id}
      </Link>
    </>
  );
}
