import { Link, useNavigate, useParams } from "react-router";

import { useApi } from "../../api/context";
import { formatCount } from "../../model/sources";
import { ConfirmDelete } from "../shell/ConfirmDelete";
import { ErrorLine } from "../shell/parts";
import { useLoad } from "../shell/useLoad";
import { TwinTree } from "./TwinTree";

/** One twin in the catalog: the summary the deploy tiers read. */
export function TwinPage() {
  const api = useApi();
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const { data, error, loading } = useLoad((signal) => api.getTwin(id, signal), [id]);
  return (
    <>
      <div className="row">
        <h1 style={{ flex: 1 }}>Twin {id}</h1>
        {data ? (
          <ConfirmDelete label="Delete twin" target={id} confirmText="Confirm: remove the twin from the catalog" onDelete={() => api.deleteTwin(id)} onDeleted={() => navigate("/twins")} />
        ) : null}
      </div>
      <ErrorLine error={error} prefix="could not load the twin" />
      {loading && !data ? <p className="muted">Loading…</p> : null}
      {data ? (
        <div className="panel" data-testid="twin-summary">
          <dl className="facts">
            <dt>Cast from</dt>
            <dd>
              {data.pit_id ? (
                <Link to={`/imprints/${encodeURIComponent(data.pit_id)}`}>{data.pit_id}</Link>
              ) : (
                <span className="muted">authored, not discovered</span>
              )}
            </dd>
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
          <h2>Segments</h2>
          <TwinTree id={id} />
          <p className="muted">
            The twin is in the catalog; the deploy tiers read it from there. <Link to="/twins">All twins</Link>
          </p>
        </div>
      ) : null}
    </>
  );
}
