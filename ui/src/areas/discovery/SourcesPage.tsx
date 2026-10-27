import { Link } from "react-router";

import { useApi } from "../../api/context";
import { describeAccess, formatIso } from "../../model/sources";
import { ErrorLine } from "../shell/parts";
import { useLoad } from "../shell/useLoad";

/** Every saved discovery source. Empty says why (ui/Spec.md T0.5), never a blank. */
export function SourcesPage() {
  const api = useApi();
  const { data, error, loading } = useLoad((signal) => api.listSources(signal), []);
  return (
    <>
      <div className="row">
        <h1 style={{ flex: 1 }}>Discovery sources</h1>
        <Link to="/discovery/sources/new">
          <button className="primary" type="button">
            New source
          </button>
        </Link>
      </div>
      <p className="muted">
        A source is a sensor's query API and how to reach it. Save one, test it, then run discovery over
        the data it holds; the result is an imprint in the catalog.
      </p>
      <ErrorLine error={error} prefix="could not list sources" />
      {loading && !data ? <p className="muted">Loading…</p> : null}
      {data && data.sources.length === 0 ? (
        <div className="empty">No discovery sources saved yet. Add one to start.</div>
      ) : null}
      {data && data.sources.length > 0 ? (
        <div className="cards">
          {data.sources.map((s) => (
            <div className="card" key={s.id} data-testid="source-card">
              <h3>
                <Link to={`/discovery/sources/${encodeURIComponent(s.id)}`}>{s.name}</Link>
              </h3>
              <div>
                <span className="badge">{s.plugin}</span> <code>{s.endpoint}</code>
              </div>
              <div className="muted">{describeAccess(s)}</div>
              <div className="muted">
                {s.last_probe
                  ? s.last_probe.reachable
                    ? `reachable at last test (${formatIso(s.last_probe.probed_at)})`
                    : `not reachable at last test (${formatIso(s.last_probe.probed_at)})`
                  : "never tested"}
              </div>
            </div>
          ))}
        </div>
      ) : null}
    </>
  );
}
