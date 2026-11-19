import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router";

import { useApi } from "../../api/context";
import { ErrorLine } from "../shell/parts";
import { standingDeploy } from "../shell/deployState";
import { useLoad } from "../shell/useLoad";

/**
 * Open: every twin in the catalog -- cast from an imprint or authored -- in a
 * searchable list; picking one opens it on the canvas. A twin with a standing
 * deploy says so: it opens on its working copy (Apply / Reset / Persist), not
 * as a config to Save. Each row is a link, so the canvas's unsaved-work guard
 * sees the move.
 */
export function OpenDesignDialog({ onClose }: { onClose: () => void }) {
  const api = useApi();
  const twins = useLoad((signal) => api.listTwins(signal), []);
  const deploys = useLoad((signal) => api.listDeploys(signal).catch(() => ({ deploys: [], warnings: [] })), []);
  const [query, setQuery] = useState("");
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);
  const rows = useMemo(() => {
    const q = query.trim().toLowerCase();
    const all = twins.data?.imprints ?? [];
    return q ? all.filter((t) => t.id.toLowerCase().includes(q) || (t.cast_from?.pit_name ?? "").toLowerCase().includes(q)) : all;
  }, [twins.data, query]);
  const list = deploys.data?.deploys ?? [];
  return (
    <div className="modal-backdrop" data-testid="open-backdrop" onClick={onClose}>
      <div className="modal panel" role="dialog" aria-modal="true" aria-label="Open a design" data-testid="open-dialog" onClick={(e) => e.stopPropagation()}>
        <h2 style={{ marginTop: 0 }}>Open a design</h2>
        <input type="search" aria-label="Search designs" placeholder="search by id or imprint" value={query} onChange={(e) => setQuery(e.target.value)} autoFocus autoComplete="off" />
        <ErrorLine error={twins.error} prefix="could not list twins" />
        {twins.loading && !twins.data ? <p className="muted">Loading…</p> : null}
        {twins.data && rows.length === 0 ? <p className="muted">{query.trim() ? "No design matches." : "No twin in the catalog yet."}</p> : null}
        {rows.length > 0 ? (
          <ul className="open-list" aria-label="Designs">
            {rows.map((t) => {
              const deploy = standingDeploy(list, t.id);
              return (
                <li key={t.id} data-testid="open-row" data-deployed={deploy ? "true" : undefined}>
                  <Link to={`/canvas/${encodeURIComponent(t.id)}`} onClick={onClose}>
                    <span className="mono">{t.id}</span>
                  </Link>{" "}
                  <span className="muted">{t.cast_from ? `cast from ${t.cast_from.pit_name ?? t.cast_from.pit_id}` : "authored"}</span>
                  {deploy ? (
                    <span className="badge" data-testid="open-deployed" title={`deploy ${deploy.deploy_id} (${deploy.status}): opens on its working copy -- Apply, Reset, Persist`}>
                      deployed{deploy.target ? ` · ${deploy.target}` : ""}
                    </span>
                  ) : null}
                </li>
              );
            })}
          </ul>
        ) : null}
        <div className="row-actions">
          <button type="button" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
    </div>
  );
}
