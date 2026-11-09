import { useEffect } from "react";
import { Link } from "react-router";

import { useApi } from "../../api/context";
import type { ComponentHealth, DeploySummary, LabComponent, Target, TapStatus } from "../../api/types";
import { componentUrl, deliveryText, labGroups, overallHealth, sensorDeliveries } from "../../model/targets";
import { standingDeploy, tierWord, twinTiers } from "../shell/deployState";
import { ErrorLine, formatWhen } from "../shell/parts";
import { useLoad } from "../shell/useLoad";

/** How often the page re-reads each target's lab health (the server caches 15 s too). */
export const HEALTH_POLL_MS = 15_000;

const EDIT_HINT = "edit targets.yaml on the host; UI editing lands with A4";

/** The targets twins are cast onto, each with its platform, the twins deployed on it and its lab -- the O2
 *  components standing beside every twin there (docs/design/targets-and-lab.md §2.3). Read-only: the targets
 *  file is edited on the host until A4. */
export function TargetsPage() {
  const api = useApi();
  const targets = useLoad((signal) => api.listTargets(signal), []);
  const list = targets.data?.targets ?? [];
  return (
    <>
      <h1>Targets</h1>
      <ErrorLine error={targets.error} prefix="could not list targets" />
      {targets.loading && !targets.data ? <p className="muted">Loading…</p> : null}
      {targets.data && list.length === 0 ? <div className="empty">This API serves no target.</div> : null}
      {list.map((t) => (
        <TargetCard key={t.name ?? t.node} target={t} />
      ))}
    </>
  );
}

function TargetCard({ target }: { target: Target }) {
  const api = useApi();
  const name = target.name ?? target.node;
  const components = target.lab?.components ?? [];
  const health = useHealth(name, components.length > 0);
  const deploys = useLoad<DeploySummary[] | null>((signal) => api.listTargetDeploys(name, signal).then((d) => d.deploys).catch(() => null), [name]);
  const twins = standingTwins(deploys.data ?? []);
  const taps = useLoad(
    (signal) =>
      Promise.all(
        twins.map((d) =>
          api.getTaps(d.deploy_id, signal).then(
            (status): { twin: string; status: TapStatus } => ({ twin: d.scenario_id, status }),
            () => null,
          ),
        ),
      ).then((xs) => xs.filter((x): x is { twin: string; status: TapStatus } => x !== null)),
    [twins.map((d) => d.deploy_id).join(",")],
  );
  const deliveries = sensorDeliveries(taps.data ?? []);
  const overall = overallHealth(components, health.data?.components ?? null);
  const byId = new Map((health.data?.components ?? []).map((h) => [h.id, h]));
  const primary = target.stores?.find((s) => s.primary) ?? null;
  return (
    <section className="panel target-card" data-testid="target-card" aria-label={`Target ${name}`}>
      <header className="target-head">
        <HealthDot status={components.length ? overall : "unknown"} label={components.length ? `lab health: ${overall}` : "no lab declared"} testid="target-health" />
        <h2>{name}</h2>
        {target.platform ? (
          <span className="badge platform" data-testid="platform-badge">
            {target.platform}
          </span>
        ) : null}
        {target.default ? <span className="badge">default</span> : null}
        <span className="spacer" />
        <button type="button" disabled title={EDIT_HINT} aria-describedby={`edit-hint-${name}`}>
          Edit
        </button>
      </header>
      <p className="muted target-hint" id={`edit-hint-${name}`}>
        {EDIT_HINT}
      </p>
      {target.error ? <div className="error">{target.error}</div> : null}
      <dl className="facts">
        <dt>Nodes</dt>
        <dd>{target.nodes?.length ? target.nodes.join(", ") : <span className="muted">—</span>}</dd>
        <dt>State store</dt>
        <dd>
          {primary ? (
            <span className="mono" title={primary.path}>
              {primary.label}
            </span>
          ) : (
            <span className="muted">—</span>
          )}
          {target.stores && target.stores.length > 1 ? <span className="muted"> (+{target.stores.length - 1} more)</span> : null}
        </dd>
      </dl>

      <h3>Deployed twins</h3>
      {deploys.data === null && !deploys.loading ? <p className="muted">The deploys on this target could not be read.</p> : null}
      {deploys.data && twins.length === 0 ? <p className="muted">No twin is deployed on this target.</p> : null}
      {twins.length > 0 ? (
        <ul className="target-twins" data-testid="target-twins">
          {twins.map((d) => (
            <li key={d.deploy_id}>
              <Link className="navlink" to={`/canvas/${encodeURIComponent(d.scenario_id)}`}>
                {d.scenario_id}
              </Link>{" "}
              <span className="mono muted">{d.deploy_id}</span>{" "}
              <span className="tiers">
                {twinTiers(deploys.data ?? [], d.scenario_id).map(([tier, st], k) => (
                  <span key={tier}>
                    {k ? " · " : ""}
                    <span className={st === "failed" ? "tier-failed" : ""} title={`${tier} ${st}`}>
                      {tier} {tierWord(st)}
                    </span>
                  </span>
                ))}
              </span>
            </li>
          ))}
        </ul>
      ) : null}

      <h3>Lab</h3>
      <ErrorLine error={health.error} prefix="could not read the lab health" />
      {components.length === 0 ? (
        <p className="muted">No lab declared for this target (a lab: block in its targets.yaml entry).</p>
      ) : (
        labGroups(components).map((g) => (
          <div key={g.role} className="lab-group" data-testid={`lab-${g.role}`}>
            <h4>{g.label}</h4>
            <ul className="lab-chips">
              {g.components.map((c) => (
                <LabChip key={c.id} c={c} health={byId.get(c.id) ?? null} delivery={c.role === "sensor" ? deliveryText(deliveries.get(c.id)) : null} />
              ))}
            </ul>
          </div>
        ))
      )}
    </section>
  );
}

function LabChip({ c, health, delivery }: { c: LabComponent; health: ComponentHealth | null; delivery: string | null }) {
  const url = componentUrl(c);
  const status = health?.status ?? "unknown";
  const title = health ? `${status}${health.detail ? `: ${health.detail}` : ""}${health.checked_at ? ` (checked ${formatWhen(health.checked_at)})` : ""}` : "not checked yet";
  return (
    <li className="lab-chip" data-testid="lab-chip" data-component={c.id}>
      <HealthDot status={status} label={title} testid="component-health" />
      <span className="lab-type">{c.type}</span>
      <span className="mono">{c.id}</span>
      {c.protected ? <span className="badge" title="protected: no teardown may touch it">protected</span> : null}
      {delivery !== null ? (
        <span className="muted lab-delivery" data-testid="sensor-delivery">
          {delivery}
        </span>
      ) : null}
      {url ? (
        <a href={url} target="_blank" rel="noopener noreferrer" className="lab-open" aria-label={`Open ${c.id}`}>
          Open ↗
        </a>
      ) : null}
    </li>
  );
}

function HealthDot({ status, label, testid }: { status: string; label: string; testid?: string }) {
  return <span className={`dot ${status}`} role="img" aria-label={label} title={label} data-testid={testid} data-status={status} />;
}

/** Each twin's standing te deploy on this target, one per twin, newest first. */
function standingTwins(rows: readonly DeploySummary[]): DeploySummary[] {
  const ids = [...new Set(rows.filter((d) => (d.tier ?? "te") === "te").map((d) => d.scenario_id))];
  return ids.map((id) => standingDeploy(rows, id)).filter((d): d is DeploySummary => d !== null);
}

/** The target's lab health, re-read every `HEALTH_POLL_MS` while the page is open. */
function useHealth(name: string, enabled: boolean) {
  const api = useApi();
  const health = useLoad((signal) => (enabled ? api.getTargetHealth(name, signal) : Promise.resolve(null)), [name, enabled]);
  const reload = health.reload;
  useEffect(() => {
    if (!enabled) return;
    const h = setInterval(reload, HEALTH_POLL_MS);
    return () => clearInterval(h);
  }, [enabled, reload]);
  return health;
}
