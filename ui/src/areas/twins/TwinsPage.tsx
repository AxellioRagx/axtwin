import { Link, useNavigate } from "react-router";

import { useApi } from "../../api/context";
import type { DeploySummary } from "../../api/types";
import { formatCount } from "../../model/sources";
import { tierWord, twinTiers } from "../shell/deployState";
import { EntityName, ErrorLine, LinkRow, When } from "../shell/parts";
import { useLoad } from "../shell/useLoad";

/** The twin catalog: every deployable twin definition, cast from an imprint or authored.
 *  Deleting lives on the twin's page; the row's "Delete…" goes there. */
export function TwinsPage() {
  const api = useApi();
  const navigate = useNavigate();
  const twins = useLoad((signal) => api.listTwins(signal), []);
  // The deploy state is a second, optional read: a refusal there leaves the column "—".
  const deploys = useLoad<DeploySummary[] | null>((signal) => api.listDeploys(signal).then((d) => d.deploys).catch(() => null), []);
  const rows = twins.data?.imprints ?? [];
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
              <th>Origin</th>
              <th>Deploy</th>
              <th>Hosts</th>
              <th>Segments</th>
              <th>Last changed</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((t) => {
              const enc = encodeURIComponent(t.id);
              const tiers = deploys.data ? twinTiers(deploys.data, t.id) : null;
              return (
                <LinkRow key={t.id} to={`/twins/${enc}`} data-testid="twin-row">
                  <td>
                    <EntityName id={t.id} to={`/twins/${enc}`} />
                  </td>
                  <td>
                    {t.cast_from ? (
                      <>
                        cast from{" "}
                        <Link className="navlink" to={`/imprints/${encodeURIComponent(t.cast_from.pit_id)}`} title={t.cast_from.pit_id}>
                          {t.cast_from.pit_name ?? t.cast_from.pit_id}
                        </Link>
                      </>
                    ) : (
                      <span className="muted">authored</span>
                    )}
                  </td>
                  <td className="tiers" data-testid="twin-deploy">
                    {tiers === null ? (
                      <span className="muted">—</span>
                    ) : tiers.length === 0 ? (
                      <span className="muted">undeployed</span>
                    ) : (
                      tiers.map(([tier, st], k) => (
                        <span key={tier}>
                          {k ? " · " : ""}
                          <span className={st === "failed" ? "tier-failed" : ""} data-testid={`tier-${tier}`} title={`${tier} ${st}`}>
                            {tier} {tierWord(st)}
                          </span>
                        </span>
                      ))
                    )}
                  </td>
                  <td>{formatCount(t.hosts)}</td>
                  <td>{formatCount(t.segments)}</td>
                  <td>
                    <When value={t.modified_at ?? ""} />
                  </td>
                  <td>
                    <span className="row-actions">
                      <Link className="linkbutton" to={`/canvas/${enc}`}>
                        Canvas
                      </Link>
                      <Link className="linkbutton" to={`/twins/${enc}#deploy`} title="Plan and deploy the twin, on its page">
                        Deploy
                      </Link>
                      <button className="quiet" type="button" onClick={() => navigate(`/twins/${enc}#delete`)} aria-label={`Delete twin ${t.id}…`}>
                        Delete…
                      </button>
                    </span>
                  </td>
                </LinkRow>
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
