import { useEffect, useRef, useState } from "react";
import { Link } from "react-router";

import { useApi } from "../../api/context";
import type { CreateDeployResponse, DeploySummary } from "../../api/types";
import { useWhoami } from "../../api/whoami";
import { remapFiles } from "../../model/preflight";
import { DeployFlow } from "../shell/DeployFlow";
import { DestroyControl } from "../shell/destroy";

/** Deploy a twin from the console (2026-11-08; the Twins list's "Deploy" used to land on a
 *  page that only printed the CLI commands). Two steps, the same ones the CLI takes: plan
 *  (pure, nothing written), then deploy pinned to that plan's id, so a twin edited in
 *  between is refused (409) rather than deployed unseen. The deploy runs detached; its row
 *  shows in the page's deploy state. Deploy stands the whole twin up (2026-11-09): te, then the
 *  twin's tap plan, then its traffic profile, each tier's outcome on the te row's `follow_on`.
 *  A twin with a standing deploy offers no Deploy: the panel
 *  offers Destroy… instead (state-aware, never both); the deploy is changed on the canvas.
 *  The plan-and-deploy body is `DeployFlow` (target select, preflight, Remap; docs/design/targets-and-lab.md §2.3). */
export function DeployPanel({ id, standing, rows = [], focus, onDeployed, destroyOpen = false, onDestroyOpenChange }: { id: string; standing: DeploySummary | null; rows?: readonly DeploySummary[]; focus: boolean; onDeployed: () => void; destroyOpen?: boolean; onDestroyOpenChange?: (open: boolean) => void }) {
  const api = useApi();
  const whoami = useWhoami();
  const [started, setStarted] = useState<CreateDeployResponse | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (focus) ref.current?.scrollIntoView?.({ block: "start" });
  }, [focus]);

  // A Remap (the preflight's sensor not in the target's lab) rewrites the twin's tap plan through the Save path:
  // undeployed, the catalog twin IS the copy the deploy runs. Validated server-side like any Save.
  async function remap(location: string, to: string) {
    const detail = await api.getTwinConfig(id);
    if (!detail.document) throw new Error("the twin's document is not readable by this account");
    await api.putTwinConfig(id, { id, files: remapFiles(detail.document.files, location, to) });
  }

  return (
    <div className="panel" ref={ref} aria-label="Deploy the twin" data-testid="deploy-panel">
      <h2 style={{ marginTop: 0 }}>{standing ? "Standing deploy" : "Deploy"}</h2>
      {started ? (
        <p role="status" data-testid="deploy-started">
          Deploy <span className="mono">{started.deploy_id}</span> started ({started.status}
          {started.detached ? ", running in the background" : ""}). Its progress shows in the deploy state above, tier by tier: te, then tap, then tfc (a tier the twin has no plan for shows —).
        </p>
      ) : null}
      {standing ? (
        <>
          <p className="muted" data-testid="deploy-blocked">
            A deploy stands on this twin (<span className="mono">{standing.deploy_id}</span>, {standing.status}). Change it on the <Link to={`/canvas/${encodeURIComponent(id)}`}>design canvas</Link> (Apply, Persist, Redeploy), or Destroy it to deploy again.
          </p>
          {whoami.can_write ? (
            <div className="row-actions">
              <DestroyControl twinId={id} deploy={standing} rows={rows} onDestroyed={onDeployed} open={destroyOpen} onOpenChange={onDestroyOpenChange} />
            </div>
          ) : null}
        </>
      ) : !whoami.can_write ? (
        <p className="muted" data-testid="deploy-blocked">
          Deploying needs an account that may write; sign in to deploy.
        </p>
      ) : (
        <DeployFlow
          twinId={id}
          remap={remap}
          remapNote="Remap saves the twin's tap plan, then plans again."
          onDeployed={(r) => {
            setStarted(r);
            onDeployed();
          }}
        />
      )}
    </div>
  );
}
