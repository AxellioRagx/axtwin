import { useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router";

import { useApi } from "../../api/context";
import type { DeploySummary, TwinSummary } from "../../api/types";
import { formatCount } from "../../model/sources";
import { standingDeploy, tierStatus } from "../shell/deployState";
import { ConfirmDelete } from "../shell/ConfirmDelete";
import { ErrorLine, When } from "../shell/parts";
import { useLoad } from "../shell/useLoad";
import { DeployPanel } from "./DeployPanel";
import { TwinTree } from "./TwinTree";

/** One twin in the catalog: the summary the deploy tiers read, and what stands
 *  on it. A cast twin shows its discovery facts (as of, confidence, days
 *  observed); an authored one has none and shows none (2026-11-05: "As of"
 *  and "Mean confidence 0.00" on a designed twin read as findings). */
export function TwinPage() {
  const api = useApi();
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const { hash } = useLocation();
  const { data, error, loading, reload } = useLoad<{ twin: TwinSummary; deploys: DeploySummary[] }>(async (signal) => {
    const twin = await api.getTwin(id, signal);
    const deploys = await api.listDeploys(signal).catch(() => ({ deploys: [], warnings: [] }));
    return { twin, deploys: deploys.deploys };
  }, [id]);
  const twin = data?.twin;
  const cast = Boolean(twin?.pit_id);
  const te = data ? standingDeploy(data.deploys, id) : null;
  const tiers = data && te ? tierStatus(data.deploys, te.deploy_id) : {};
  const mine = data ? data.deploys.filter((d) => d.scenario_id === id && (d.tier ?? "te") === "te") : [];
  const [destroyOpen, setDestroyOpen] = useState(false);
  const [deleteAsked, setDeleteAsked] = useState(false);
  return (
    <>
      <div className="row">
        <h1 style={{ flex: 1 }}>Twin {id}</h1>
        {twin ? (
          <Link className="linkbutton" to={`/canvas/${encodeURIComponent(id)}`}>
            Open the design canvas
          </Link>
        ) : null}
        {twin && te ? (
          <button className="danger" type="button" onClick={() => setDeleteAsked(true)}>
            Delete twin
          </button>
        ) : twin ? (
          <ConfirmDelete label="Delete twin" target={id} startConfirming={hash === "#delete"} confirmText="Confirm: remove the twin from the catalog" onDelete={() => api.deleteTwin(id)} onDeleted={() => navigate("/twins")} />
        ) : null}
      </div>
      {/* Delete removes the twin config; a standing deploy would refuse it (409). Say so, and offer Destroy. */}
      {twin && te && (deleteAsked || hash === "#delete") ? (
        <p className="warn-line" role="status" data-testid="delete-blocked">
          Destroy the deploy first: Delete removes the twin config from the catalog, and deploy <span className="mono">{te.deploy_id}</span> stands on it.{" "}
          <button type="button" className="linkbutton" onClick={() => setDestroyOpen(true)} data-testid="delete-blocked-destroy">
            Destroy…
          </button>
        </p>
      ) : null}
      <ErrorLine error={error} prefix="could not load the twin" />
      {loading && !data ? <p className="muted">Loading…</p> : null}
      {twin ? (
        <div className="panel" data-testid="twin-summary">
          <dl className="facts">
            <dt>Origin</dt>
            <dd>
              {twin.pit_id ? (
                <>
                  cast from <Link className="navlink" to={`/imprints/${encodeURIComponent(twin.pit_id)}`}>{twin.pit_id}</Link>
                </>
              ) : (
                <span>authored on the canvas, not discovered</span>
              )}
            </dd>
            <dt>Deploy</dt>
            <dd id="deploy" data-testid="deploy-state">
              {te ? (
                <>
                  <span className="mono">{te.deploy_id}</span>
                  {" · "}
                  <span className={te.status === "failed" ? "error" : ""} data-testid="tier-te">te {te.status}</span>
                  {Object.entries(tiers).map(([t, st]) => (
                    <span key={t} className={st === "failed" ? "error" : ""} data-testid={`tier-${t}`}>
                      {" · "}
                      {t} {st}
                    </span>
                  ))}
                  {te.target ? <span className="muted"> · on {te.target}</span> : null}
                </>
              ) : mine.length ? (
                <span className="muted">none standing ({mine.length} past deploy{mine.length === 1 ? "" : "s"}: {[...new Set(mine.map((d) => d.status))].join(", ")})</span>
              ) : (
                <span className="muted">none: the twin is in the catalog, not deployed</span>
              )}
            </dd>
            {cast ? (
              <>
                <dt>As of</dt>
                <dd>
                  <When value={twin.as_of} />
                </dd>
              </>
            ) : null}
            <dt>Hosts</dt>
            <dd>{formatCount(twin.hosts)}</dd>
            <dt>Segments</dt>
            <dd>{formatCount(twin.segments)}</dd>
            <dt>Services</dt>
            <dd>{formatCount(twin.services)}</dd>
            <dt>Personas</dt>
            <dd>{formatCount(twin.personas)}</dd>
            {cast ? (
              <>
                <dt>Mean confidence</dt>
                <dd>{typeof twin.mean_confidence === "number" ? twin.mean_confidence.toFixed(2) : "unknown"}</dd>
              </>
            ) : null}
            {cast && twin.days_observed !== undefined && twin.days_observed !== null ? (
              <>
                <dt>Days observed</dt>
                <dd>{formatCount(twin.days_observed)}</dd>
              </>
            ) : null}
            {cast && twin.windows !== undefined && twin.windows !== null ? (
              <>
                <dt>Windows</dt>
                <dd>{formatCount(twin.windows)}</dd>
              </>
            ) : null}
          </dl>
          {Array.isArray(twin.warnings) && twin.warnings.length ? (
            <ul className="muted">
              {twin.warnings.map((w, i) => (
                <li key={i}>{w}</li>
              ))}
            </ul>
          ) : null}
          <DeployPanel id={id} standing={te} rows={data?.deploys ?? []} focus={hash === "#deploy"} onDeployed={reload} destroyOpen={destroyOpen} onDestroyOpenChange={setDestroyOpen} />
          <h2>Segments</h2>
          <TwinTree id={id} />
          <p className="muted">
            The twin is in the catalog; the deploy tiers read it from there (Deploy above, or <code>axrange te deploy --config {id}</code>; then <code>tap deploy</code> and <code>tfc deploy --twin {id}</code>). <Link to="/twins">All twins</Link>
          </p>
        </div>
      ) : null}
    </>
  );
}
