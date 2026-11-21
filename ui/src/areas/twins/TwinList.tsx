import { useEffect, useMemo, useRef, useState, type MouseEvent } from "react";
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
 *   and its one action, Clone, starts an untitled design from a copy of it. A row's move is a click on its link, so the canvas's
 *   unsaved-work guard sees it. A deployed twin opens on its working copy
 *   (Apply / Reset / Persist), which the Deploy column says.
 *
 * Both views list the DRAFTS beneath (`GET /api/drafts`, `api/Spec.md` A3d),
 * each labelled `draft` with the twin it may replace: a draft opens on the
 * canvas and is never deployed (no Deploy action).
 */
/** How long a picker row must have been where it is before a click on it counts (round 3 item 4). */
export const ROW_SETTLE_MS = 300;

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
  // The drafts: a second, optional read (an API without the drafts area lists none).
  const drafts = useLoad((signal) => api.listDrafts(signal).then((d) => d.drafts).catch(() => []), []);
  const [draftError, setDraftError] = useState<unknown>(null);
  const draftRows = useMemo(() => {
    const q = query.trim().toLowerCase();
    const list = drafts.data ?? [];
    return q ? list.filter((d) => d.id.toLowerCase().includes(q) || (d.origin ?? "").toLowerCase().includes(q)) : list;
  }, [drafts.data, query]);
  const deleteDraft = (id: string) => {
    if (!window.confirm(`Delete draft ${id}? Its document is removed; no twin is touched.`)) return;
    setDraftError(null);
    api.deleteDraft(id).then(() => drafts.reload(), (e: unknown) => setDraftError(e));
  };
  const all = twins.data?.imprints ?? [];
  const rows = useMemo(() => {
    const q = query.trim().toLowerCase();
    return q ? all.filter((t) => t.id.toLowerCase().includes(q) || (t.cast_from?.pit_name ?? "").toLowerCase().includes(q)) : all;
  }, [all, query]);
  const skipped = twins.data?.skipped ?? [];

  // Round 3 item 4: in the picker, a click on a row that appeared (or moved) under the pointer less
  // than ROW_SETTLE_MS ago is ignored -- the list loading under a click had opened the wrong design.
  // Stopped at the window, in the capture phase, so no link, row or leave guard ever sees it.
  const listRef = useRef<HTMLDivElement | null>(null);
  const layoutSig = `${rows.map((t) => t.id).join("|")}#${draftRows.map((d) => d.id).join("|")}#${deploys.data ? 1 : 0}`;
  const settledAt = useRef(0);
  useEffect(() => {
    settledAt.current = Date.now() + ROW_SETTLE_MS;
  }, [layoutSig]);
  useEffect(() => {
    if (!pick) return;
    const shield = (e: globalThis.MouseEvent) => {
      const el = e.target as Element | null;
      if (!el?.closest || !listRef.current?.contains(el) || !el.closest("tbody tr")) return;
      if (Date.now() >= settledAt.current) return;
      e.preventDefault();
      e.stopPropagation();
    };
    window.addEventListener("click", shield, true);
    return () => window.removeEventListener("click", shield, true);
  }, [pick]);

  const target = (t: TwinListItem) => (pick ? `/canvas/${encodeURIComponent(t.id)}` : `/twins/${encodeURIComponent(t.id)}`);
  // A click on the row's empty space follows its first link (pick: through the guard) or navigates (manage).
  const onRow = (e: MouseEvent<HTMLTableRowElement>, t: TwinListItem) => {
    if ((e.target as HTMLElement).closest("a,button,input,select,textarea")) return;
    if (pick) e.currentTarget.querySelector<HTMLAnchorElement>("a")?.click();
    else navigate(target(t));
  };

  return (
    <div ref={listRef} style={{ display: "contents" }} data-testid={pick ? "open-list" : undefined}>
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
                        aria-label={`Clone ${t.id}`}
                        title={`a new untitled design with ${t.id}'s saved twin config and layout, to edit and Save as a new twin (${t.id} is not changed${standing ? "; its deploy's unpersisted working-copy changes are not included" : ""})`}
                      >
                        Clone
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
      <ErrorLine error={draftError} prefix="could not delete the draft" />
      {draftRows.length > 0 ? (
        <>
          <h2>Drafts</h2>
          <table className={`list${pick ? " compact" : ""}`} aria-label="Drafts">
            <thead>
              <tr>
                <th>Draft</th>
                <th>Origin</th>
                <th>Last check</th>
                <th>Last changed</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {draftRows.map((d) => {
                const to = `/canvas/drafts/${encodeURIComponent(d.id)}`;
                return (
                  <tr key={d.id} data-testid="draft-row" title="a draft: kept without the validation gate, never deployed">
                    <td>
                      <Link className="navlink" to={to} onClick={pick ? props.onPick : undefined}>
                        {d.id}
                      </Link>
                      <span className="cv-draft-label" data-testid="draft-label">draft</span>
                    </td>
                    <td>{d.origin ? <>draft of <span className="mono">{d.origin}</span></> : <span className="muted">no origin</span>}</td>
                    <td>{d.last_check ? `${d.last_check.disposition.toLowerCase()}${d.last_check.errors ? ` · ${d.last_check.errors} error${d.last_check.errors === 1 ? "" : "s"}` : ""}` : <span className="muted">unchecked</span>}</td>
                    <td>
                      <When value={d.updated_at} />
                    </td>
                    <td>
                      {pick ? null : (
                        <span className="row-actions">
                          <Link className="linkbutton" to={to}>
                            Canvas
                          </Link>
                          <button className="quiet" type="button" disabled title="a draft is never deployed: replace its origin, or Save As a new twin, from the canvas">
                            Deploy
                          </button>
                          <button className="quiet" type="button" onClick={() => deleteDraft(d.id)} aria-label={`Delete draft ${d.id}…`}>
                            Delete…
                          </button>
                        </span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </>
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
    </div>
  );
}
