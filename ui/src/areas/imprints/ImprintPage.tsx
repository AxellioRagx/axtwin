import { Link, useNavigate, useParams } from "react-router";

import { useApi } from "../../api/context";
import { formatCount } from "../../model/sources";
import { ErrorLine } from "../shell/parts";
import { useLoad } from "../shell/useLoad";
import { DeleteImprint } from "./DeleteImprint";

/** What discovery produced: the imprint's summary from the catalog. */
export function ImprintPage() {
  const api = useApi();
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const { data, error, loading } = useLoad((signal) => api.getImprint(id, signal), [id]);
  return (
    <>
      <div className="row">
        <h1 style={{ flex: 1 }}>Imprint {id}</h1>
        {data ? <DeleteImprint id={id} onDeleted={() => navigate("/imprints")} /> : null}
      </div>
      <ErrorLine error={error} prefix="could not load the imprint" />
      {loading && !data ? <p className="muted">Loading…</p> : null}
      {data ? (
        <div className="panel" data-testid="imprint-summary">
          <dl className="facts">
            <dt>As of</dt>
            <dd>{data.as_of}</dd>
            <dt>Hosts</dt>
            <dd>{formatCount(data.hosts)}</dd>
            <dt>Segments</dt>
            <dd>{formatCount(data.segments)}</dd>
            <dt>Services</dt>
            <dd>{formatCount(data.services)}</dd>
            <dt>Personas</dt>
            <dd>{formatCount(data.personas)}</dd>
            <dt>Mean confidence</dt>
            <dd>{typeof data.mean_confidence === "number" ? data.mean_confidence.toFixed(2) : "unknown"}</dd>
            {data.days_observed !== undefined && data.days_observed !== null ? (
              <>
                <dt>Days observed</dt>
                <dd>{formatCount(data.days_observed)}</dd>
              </>
            ) : null}
            {data.windows !== undefined && data.windows !== null ? (
              <>
                <dt>Windows</dt>
                <dd>{formatCount(data.windows)}</dd>
              </>
            ) : null}
          </dl>
          {Array.isArray(data.warnings) && data.warnings.length ? (
            <ul className="muted">
              {data.warnings.map((w, i) => (
                <li key={i}>{w}</li>
              ))}
            </ul>
          ) : null}
          {Array.isArray(data.topology_segments) && data.topology_segments.length ? (
            <>
              <h2>Segments</h2>
              <table className="list">
                <thead>
                  <tr>
                    <th>Segment</th>
                    <th>Hosts</th>
                  </tr>
                </thead>
                <tbody>
                  {data.topology_segments.map((s, i) => (
                    <tr key={i}>
                      <td className="mono">{String(s.cidr ?? s.name ?? JSON.stringify(s))}</td>
                      <td>{formatCount(s.hosts)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          ) : null}
          <p className="muted">
            The bundle is installed in the catalog; the twin tiers read it from there. <Link to="/imprints">All imprints</Link>
          </p>
        </div>
      ) : null}
    </>
  );
}
