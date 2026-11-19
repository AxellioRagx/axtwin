import { useMemo, useState, type MouseEvent } from "react";
import { Link, useNavigate } from "react-router";

import { useApi } from "../../api/context";
import type { DeploySummary, TwinListItem } from "../../api/types";
import { formatCount } from "../../model/sources";
import { standingDeploy, tierWord, twinTiers } from "../shell/deployState";
import { DestroyControl } from "../shell/destroy";
import { EntityName, ErrorLine, When } from "../shell/parts";
import { useLoad } from "../shell/useLoad";

/**
 * The twin catalog as one list, in two views over the same rows (`GET /api/twins`
 * with the deploys beside it):
 *
 * - `manage` (the Twins page): a row opens the twin's page; the actions are
 *   Canvas, Deploy / Destroy… (state-aware, never both) and Delete…, and the
 *   catalog folders the listing could not read are listed beneath.
 * - `pick` (the canvas's Open…): searchable; a row opens the twin on the canvas,
 *   and its one action, Duplicate, starts an untitled design from a copy of it. A row's move is a click on its link, so the canvas's
 *   unsaved-work guard sees it. A deployed twin opens on its working copy
 *   (Apply / Reset / Persist), which the Deploy column says.
 */
export type TwinListProps = { mode: "manage" } | { mode: "pick"; onPick: () => void };

export function TwinList(props: TwinListProps) {
  const api = useApi();
  const navigate = useNavigate();
  const pick = props.mode === "pick";
  const twins = useLoad((signal) => api.listTwins(signal), []);
  // The deploy state is a second, optional read: a refusal there leaves the column "—".
  const deploys = useLoad<DeploySummary[] | null>((signal) => api.listDeploys(signal).then((d) => d.deploys).catch(() => null), []);
  const reloadDeploys = deploys.reload;
  const [query, setQuery] = useState("");
  const all = twins.data?.imprints ?? [];
  const rows = useMemo(() => {
    const q = query.trim().toLowerCase();
    return q ? all.filter((t) => t.id.toLowerCase().includes(q) || (t.cast_from?.pit_name ?? "").toLowerCase().includes(q)) : all;
  }, [all, query]);
  const skipped = twins.data?.skipped ?? [];

  const target = (t: TwinListItem) => (pick ? `/canvas/${encodeURIComponent(t.id)}` : `/twins/${encodeURIComponent(t.id)}`);
  // A click on the row's empty space follows its first link (pick: through the guard) or navigates (manage).
  const onRow = (e: MouseEvent<HTMLTableRowElement>, t: TwinListItem) => {
    if ((e.target as HTMLElement).closest("a,button,input,select,textarea")) return;
    if (pick) e.currentTarget.querySelector<HTMLAnchorElement>("a")?.click();
    else navigate(target(t));
  };

  return (
    <>
      {pick ? <input type="search" aria-label="Search twins" placeholder="search by id or imprint" value={query} onChange={(e) => setQuery(e.target.value)} autoFocus autoComplete="off" /> : null}
      <ErrorLine error={twins.error} prefix="could not list twins" />
      {twins.loading && !twins.data ? <p className="muted">Loading…</p> : null}
      {twins.data && all.length === 0 ? <div className="empty">No twin in the catalog. Cast an imprint, or author one.</div> : null}
      {twins.data && all.length > 0 && rows.length === 0 ? <p className="muted">No twin matches.</p> : null}
      {rows.length > 0 ? (
        <table className={`list${pick ? " compact" : ""}`} aria-label="Twins">
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
              const standing = deploys.data ? standingDeploy(deploys.data, t.id) : null;
              return (
                <tr
                  key={t.id}
                  className="clickable"
                  onClick={(e) => onRow(e, t)}
                  data-testid={pick ? "open-row" : "twin-row"}
                  data-deployed={standing ? "true" : undefined}
                  title={pick && standing ? `deployed (${standing.deploy_id}${standing.target ? ` on ${standing.target}` : ""}): opens on its working copy -- Apply, Reset, Persist` : undefined}
                >
                  <td>{pick ? <Link className="navlink" to={target(t)} onClick={props.onPick}>{t.id}</Link> : <EntityName id={t.id} to={target(t)} />}</td>
                  <td>
                    {t.cast_from ? (
                      <>
                        cast from{" "}
                        {pick ? (
                          <span title={t.cast_from.pit_id}>{t.cast_from.pit_name ?? t.cast_from.pit_id}</span>
                        ) : (
                          <Link className="navlink" to={`/imprints/${encodeURIComponent(t.cast_from.pit_id)}`} title={t.cast_from.pit_id}>
                            {t.cast_from.pit_name ?? t.cast_from.pit_id}
                          </Link>
                        )}
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
                  {pick ? (
                    <td>
                      <Link
                        className="linkbutton"
                        to={`/canvas/new?from=${enc}`}
                        onClick={props.onPick}
                        aria-label={`Duplicate ${t.id}`}
                        title={`a new untitled design with ${t.id}'s saved twin config and layout, to edit and Save as a new twin (${t.id} is not changed${standing ? "; its deploy's unpersisted working-copy changes are not included" : ""})`}
                      >
                        Duplicate
                      </Link>
                    </td>
                  ) : (
                    <td>
                      <span className="row-actions">
                        <Link className="linkbutton" to={`/canvas/${enc}`}>
                          Canvas
                        </Link>
                        {/* State-aware: Deploy while nothing stands, Destroy… once a deploy does (never both). */}
                        {standing && deploys.data ? (
                          <DestroyControl twinId={t.id} deploy={standing} rows={deploys.data} onDestroyed={reloadDeploys} />
                        ) : (
                          <Link className="linkbutton" to={`/twins/${enc}#deploy`} title="Plan and deploy the twin, on its page">
                            Deploy
                          </Link>
                        )}
                        <button className="quiet" type="button" onClick={() => navigate(`/twins/${enc}#delete`)} aria-label={`Delete twin ${t.id}…`}>
                          Delete…
                        </button>
                      </span>
                    </td>
                  )}
                </tr>
              );
            })}
          </tbody>
        </table>
      ) : null}
      {!pick && skipped.length > 0 ? (
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
