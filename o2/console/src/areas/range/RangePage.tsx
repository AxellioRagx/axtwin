/**
 * The Range -- the home page (ruling §2): which twins are up, on which
 * cluster, with which taps / traffic / exercises, and are they healthy. One
 * call (`GET /api/range`) paints it.
 *
 * Everything on a twin card is RECORDED (the state store) and says so; the
 * cluster's reachability is the one LIVE value and carries its age.
 */
import { Link } from "react-router";

import { useNow, useRange } from "../../api/runtimeHooks";
import type { DeploySummary, RangeTarget, RangeTwin, UnrecordedVms } from "../../api/runtimeTypes";
import { catalogPath, twinPath } from "../../model/appRoutes";
import { ago, count, statusTone, statusWord, when } from "../../model/format";
import { ErrorNotice } from "../shell/parts";
import { ProtectedBadge } from "../shell/ProtectionOverride";

export function RangePage(): JSX.Element {
  const range = useRange();
  const tick = useNow(10000);
  // Never older than the answer on screen: the ticking clock can lag it.
  const now = Math.max(tick, (range.data?.generated_at ?? 0) * 1000);

  return (
    <section className="page range-page" aria-labelledby="range-title">
      <div className="page-head">
        <h1 id="range-title">Range</h1>
        <p className="page-lede">Every deployed twin, grouped by the cluster it runs on.</p>
        <button type="button" className="btn small" onClick={range.reload} disabled={range.loading} data-testid="range-refresh">
          {range.loading ? "Refreshing…" : "Refresh"}
        </button>
      </div>
      {range.loading && !range.data && <p className="meta">Reading the range…</p>}
      {range.error && (
        <ErrorNotice
          error={range.error}
          what="Reading the range"
          notFound="This server does not have the Range endpoint (GET /api/range answered 404) — it predates the Unified Console. Restart `axrange serve` from this branch."
        />
      )}
      {range.data && range.data.targets.length === 0 && (
        <p className="empty" data-testid="range-empty">
          This server has no cluster target configured, so there is nothing to list. Configure one (a targets file,
          or the adapter config `axrange serve` starts with).
        </p>
      )}
      {range.data?.targets.map((t) => (
        <ClusterGroup key={t.name} target={t} now={now} single={range.data!.targets.length === 1} />
      ))}
    </section>
  );
}

function ClusterGroup({ target, now, single }: { target: RangeTarget; now: number; single: boolean }): JSX.Element {
  const reach = target.reachability;
  const tone = statusTone(reach.state);
  return (
    <section className="cluster" aria-labelledby={`cluster-${target.name}`} data-testid="cluster-group" data-cluster={target.name}>
      <header className="cluster-head">
        <h2 id={`cluster-${target.name}`}>
          Cluster <span className="mono">{target.name}</span>
          {target.default && !single && <span className="tag">default</span>}
        </h2>
        <span className="meta mono" title="Proxmox API this cluster target talks to">
          {target.api_url ?? "API URL unknown"}
        </span>
        <span className={`pill tone-${tone}`} data-testid="reachability" title={reach.reason ?? reach.detail ?? ""}>
          {reach.state === "reachable" ? "reachable" : reach.state === "unknown" ? "reachability unknown" : reach.state}
          <span className="age">
            {" "}
            · live, checked {ago(reach.checked_at, now)}
          </span>
        </span>
        {reach.state !== "reachable" && reach.reason && <span className="meta">({reach.reason})</span>}
      </header>
      {target.error && (
        <p className="notice is-error">
          This cluster&apos;s adapter config could not be loaded: {target.error}
        </p>
      )}
      {!target.store.ok && (
        <p className="notice is-error">
          This cluster&apos;s deploy records cannot be read: {target.store.reason ?? "no reason given"}.
        </p>
      )}
      {target.unrecorded && <UnrecordedSection u={target.unrecorded} now={now} />}
      {target.store.ok && target.twins.length === 0 && (
        <p className="empty" data-testid="cluster-empty">
          No twin is deployed on <span className="mono">{target.name}</span>. Pick one from the{" "}
          <Link to={catalogPath(null, null, single ? null : target.name)}>Catalog</Link> to deploy it here.
        </p>
      )}
      {target.twins.length > 0 && (
        <ul className="twin-cards">
          {target.twins.map((twin) => (
            <TwinCard
              key={twin.deploy_id}
              twin={twin}
              target={target}
              now={now}
              showStore={(target.stores?.length ?? 0) > 1}
            />
          ))}
        </ul>
      )}
    </section>
  );
}

function latest(list: readonly DeploySummary[] | undefined): DeploySummary | null {
  if (!list || list.length === 0) return null;
  return [...list].sort((a, b) => b.started_at - a.started_at)[0] ?? null;
}

function Sibling({ label, list, extra }: { label: string; list: readonly DeploySummary[] | undefined; extra?: string }): JSX.Element {
  const top = latest(list);
  return (
    <span className="sibling" title={top ? `${top.deploy_id} · started ${when(top.started_at)}` : `No ${label.toLowerCase()} deploy`}>
      <span className="sibling-label">{label}</span>{" "}
      {top ? (
        <span className={`status tone-${statusTone(top.status)}`}>
          {statusWord(top.status)}
          {extra ? ` · ${extra}` : ""}
        </span>
      ) : (
        <span className="meta">none</span>
      )}
    </span>
  );
}

function TwinCard({
  twin,
  target,
  now,
  showStore,
}: {
  twin: RangeTwin;
  target: RangeTarget;
  now: number;
  showStore: boolean;
}): JSX.Element {
  const to = twinPath(twin.deploy_id, { target: target.default ? null : target.name });
  const personas =
    twin.persona_instance_count === null ? "no traffic deploy" : count(twin.persona_instance_count, "persona", "personas");
  return (
    <li className="twin-card" data-testid="twin-card" data-deploy={twin.deploy_id}>
      <Link to={to} className="twin-card-link" aria-label={`Open twin ${twin.twin_id ?? twin.deploy_id} (${twin.deploy_id}) on ${target.name}`}>
        <span className="twin-name">{twin.twin_id ?? "unknown twin"}</span>
        <span className="mono meta">{twin.deploy_id}</span>
      </Link>
      {twin.protected && <ProtectedBadge />}
      <div className="twin-facts">
        <span className={`status tone-${statusTone(twin.status)}`} title="Recorded deploy status (state store)">
          {statusWord(twin.status)}
        </span>
        <span>on {twin.cluster ?? target.name}</span>
        <span>{count(twin.host_count, "host")}</span>
        <span>{personas}</span>
        <span title={when(twin.started_at)}>deployed {ago(twin.started_at, now)}</span>
        {showStore && (
          <span className="meta store-label" data-testid="twin-store" title="The state store that records this deploy">
            store {twin.store ?? "unknown"}
          </span>
        )}
      </div>
      <div className="twin-siblings">
        <Sibling label="Taps" list={twin.siblings?.tap} />
        <Sibling label="Traffic" list={twin.siblings?.tfc} />
        <Sibling label="Exercise" list={twin.siblings?.exr} />
        <span className="meta recorded-tag">recorded</span>
      </div>
      {twin.warnings?.length > 0 && (
        <ul className="warnings">
          {twin.warnings.map((w) => (
            <li key={w} className="notice">
              {w}
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}

/**
 * VMs on the cluster that no recorded deploy owns (live, from the cluster
 * sweep): collapsed, read-only, labelled with its age. A failed sweep says
 * "unknown" with the reason -- never "none".
 */
function UnrecordedSection({ u, now }: { u: UnrecordedVms; now: number }): JSX.Element {
  const known = u.state === "known";
  const n = known ? (u.count ?? u.vms.length) : null;
  return (
    <details className="unrecorded" data-testid="unrecorded">
      <summary>
        VMs on this cluster that no recorded deploy owns:{" "}
        <strong>{n === null ? "unknown" : n}</strong> <span className="src-tag is-live">live · {ago(u.checked_at, now)}</span>
      </summary>
      {!known && (
        <p className="meta" data-testid="unrecorded-unknown">
          Unknown — the cluster could not be read ({u.reason ?? "no reason given"}).
        </p>
      )}
      {known && u.vms.length === 0 && <p className="meta">None: every axRange VM on the cluster belongs to a recorded deploy.</p>}
      {known && u.vms.length > 0 && (
        <>
          <p className="meta">
            Read-only. These may be left over from a deploy whose record was removed, or from another tool.
            {n !== null && n > u.vms.length ? ` Showing ${u.vms.length} of ${n}.` : ""}
          </p>
          <table className="mini" data-testid="unrecorded-table">
            <thead>
              <tr>
                <th scope="col">vmid</th>
                <th scope="col">name</th>
                <th scope="col">status</th>
                <th scope="col">node</th>
              </tr>
            </thead>
            <tbody>
              {u.vms.map((v) => (
                <tr key={v.vmid}>
                  <td className="mono">{v.vmid}</td>
                  <td className="mono">{v.name ?? "unnamed"}</td>
                  <td>{v.status ?? "unknown"}</td>
                  <td>{v.node ?? "unknown"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </details>
  );
}

