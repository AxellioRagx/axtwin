/**
 * One host (`/twin/<id>/hosts/<host>`): its recorded facts, NICs, the personas
 * it runs (each with one plain line of what it sends), its recorded status and
 * last error, how to log in, and -- on request -- its live health.
 */
import { useMemo } from "react";

import { useRuntimeApi } from "../../api/runtimeContext";
import { useAccess, useOnDemand, usePersonas } from "../../api/runtimeHooks";
import type { HealthDoc, PersonaInfo } from "../../api/runtimeTypes";
import type { DeployedHost, DeployedTwin } from "../../model/deployed";
import { ago, when } from "../../model/format";
import { LiveTag, RecordedTag, StatusBadge } from "../shell/parts";
import { PersonaLine } from "./PersonaLine";
import { ProtectedAction, ProtectedBadge } from "../shell/ProtectionOverride";

export function HostPanel(props: {
  model: DeployedTwin;
  host: DeployedHost;
  target: string | null;
  protectedTwin?: boolean;
  override?: string | null;
  onOverride?: (override: string | null) => void;
  now: number;
  onClose: () => void;
  onEditPersonas: () => void;
  onSegment: (segment: string) => void;
}): JSX.Element {
  const { model, host, target } = props;
  const api = useRuntimeApi();
  const raw = host.raw;
  const catalog = usePersonas(model.twinId);
  const access = useAccess(model.deployId, target);
  const health = useOnDemand<HealthDoc>(`${model.deployId}|${target ?? ""}|${host.id}`, (signal) =>
    api.getHealth(model.deployId, target, host.id, signal),
  );

  const now = Math.max(props.now, health.receivedAt ?? 0);
  const personaInfo = useMemo(() => {
    const out = new Map<string, PersonaInfo>();
    for (const p of catalog.data?.personas ?? []) out.set(p.id, p);
    return out;
  }, [catalog.data]);
  const hostAccess = access.data?.hosts.find((h) => h.host_id === host.id) ?? null;
  const livePower = health.data?.hosts.find((h) => h.host_id === host.id)?.power ?? null;
  const detail = health.data?.host_detail && health.data.host_detail.host_id === host.id ? health.data.host_detail : null;

  return (
    <div className="panel host-panel" data-testid="host-panel">
      <div className="panel-head">
        <h2>
          <span className="mono">{host.id}</span>
          {props.protectedTwin && <ProtectedBadge title="This twin is protected: persona changes need an explicit override." />}
        </h2>
        <button type="button" className="btn small" onClick={props.onClose} aria-label="Close the host panel">
          ×
        </button>
      </div>
      <div className="panel-actions">
        <ProtectedAction
          protectedTwin={!!props.protectedTwin}
          deployId={model.deployId}
          override={props.override ?? null}
          onOverride={props.onOverride ?? (() => undefined)}
          what="changing this host's personas"
          label="Change personas…"
          className="btn primary"
          onAct={props.onEditPersonas}
          testId="edit-personas"
        />
        <button type="button" className="btn" onClick={health.run} disabled={health.loading} data-testid="check-host-live">
          {health.loading ? "Checking…" : health.data ? "Re-check live health" : "Check live health"}
        </button>
      </div>

      {(health.data || health.error) && (
        <section className="panel-section" aria-label="Live health">
          <h3>
            Live health {health.data && <LiveTag checkedAt={health.data.checked_at} now={now} />}
          </h3>
          {health.error && <p className="notice is-error">The live check failed: {health.error.message}</p>}
          {health.data && (
            <dl className="facts">
              <dt>VM power</dt>
              <dd data-testid="live-power">
                {livePower ? livePower.state : "unknown"}
                {livePower?.reason ? ` (${livePower.reason})` : ""}
              </dd>
              <dt>guest agent</dt>
              <dd>
                {detail ? detail.qga.state : "unknown"}
                {detail?.qga.reason ? ` (${detail.qga.reason})` : ""}
              </dd>
              {detail?.personas.map((p) => (
                <FragmentPair
                  key={p.instance_id}
                  name={p.persona}
                  value={`${p.state}${p.alive === true ? " (running)" : p.alive === false ? " (not running)" : ""}${p.reason ? ` — ${p.reason}` : ""}`}
                />
              ))}
            </dl>
          )}
        </section>
      )}

      <section className="panel-section" aria-label="Personas">
        <h3>
          Personas <RecordedTag />
        </h3>
        {host.personas.length === 0 ? (
          <p className="meta">No persona runs on this host.</p>
        ) : (
          <ul className="persona-list" data-testid="host-personas">
            {host.personas.map((p) => {
              const info = personaInfo.get(p.persona);
              return (
                <li key={p.instance_id}>
                  <div>
                    <span className="mono">{p.persona}</span> <StatusBadge status={p.status} />
                    {p.origin === "host-edit" && (
                      <span className="tag" title="Set on this host alone; a whole-profile apply would revert it and asks first.">
                        host edit
                      </span>
                    )}
                  </div>
                  <PersonaLine info={info} loading={catalog.loading} />
                  <div className="meta">
                    {p.schedule ? `schedule ${p.schedule}` : "schedule not recorded"}
                    {p.started_at ? ` · started ${ago(p.started_at, now)}` : ""}
                    {p.heartbeat && (
                      <span title={when(p.heartbeat.checked_at)}>
                        {" "}
                        · after start: {p.heartbeat.state}
                        {p.heartbeat.reason ? ` (${p.heartbeat.reason})` : ""}
                      </span>
                    )}
                  </div>
                  {p.stop_error && (
                    <p className="notice is-error" data-testid="stop-error">
                      Could not stop {p.persona} — it may still be running. {p.stop_error.message}
                    </p>
                  )}
                  {p.last_error && <p className="notice is-error">{p.last_error.error_message ?? "failed"}</p>}
                </li>
              );
            })}
          </ul>
        )}
      </section>

      {raw.host_edit && raw.host_edit.superseded && (
        <section className="panel-section" aria-label="Last change on this host" data-testid="host-edit">
          <h3>
            Last persona change <RecordedTag />
          </h3>
          <p data-testid="host-edit-superseded">
            A change made on this host alone was <strong>superseded by a whole-profile apply</strong>
            {raw.host_edit.superseded_at ? ` ${ago(raw.host_edit.superseded_at, now)}` : ""}: the personas above are the
            profile&apos;s, not that edit&apos;s.
          </p>
          <details>
            <summary>What that edit recorded</summary>
            <p className="meta">
              {raw.host_edit.status}
              {raw.host_edit.finished_at ? `, finished ${ago(raw.host_edit.finished_at, now)}` : ""}
            </p>
            {raw.host_edit.diagnostics.map((d) => (
              <p key={`${d.level}:${d.message}`} className="meta">
                {d.level}: {d.message}
              </p>
            ))}
          </details>
        </section>
      )}
      {raw.host_edit && !raw.host_edit.superseded && (
        <section className="panel-section" aria-label="Last change on this host" data-testid="host-edit">
          <h3>
            Last persona change <RecordedTag />
          </h3>
          <p>
            <StatusBadge status={raw.host_edit.status} />
            {raw.host_edit.finished_at ? ` · finished ${ago(raw.host_edit.finished_at, now)}` : raw.host_edit.started_at ? ` · started ${ago(raw.host_edit.started_at, now)}` : ""}
          </p>
          {raw.host_edit.diagnostics.length === 0 ? (
            <p className="meta">No problems recorded.</p>
          ) : (
            <ul className="diagnostics">
              {raw.host_edit.diagnostics.map((d) => (
                <li key={`${d.level}:${d.message}`} className={d.level === "error" ? "bad" : undefined}>
                  {d.level}: {d.message}
                </li>
              ))}
            </ul>
          )}
        </section>
      )}

      <section className="panel-section" aria-label="Facts">
        <h3>
          Facts <RecordedTag />
        </h3>
        <dl className="facts">
          <dt>status</dt>
          <dd>
            <StatusBadge status={host.recorded} title="The worst of the VM's deploy step and its persona steps" />
          </dd>
          <dt>role</dt>
          <dd>
            {raw.role ?? "none"}
            {raw.role_baseline && raw.role_baseline !== raw.role ? ` (twin's own: ${raw.role_baseline})` : ""}
          </dd>
          <dt>OS</dt>
          <dd>{raw.os ?? "unknown"}</dd>
          <dt>kind</dt>
          <dd>{raw.kind ?? "unknown"}</dd>
          <dt>VM</dt>
          <dd className="mono">
            {raw.vm_name ?? "?"} · vmid {raw.vmid ?? "?"} · on {raw.pve_node ?? "?"}
          </dd>
          {raw.last_error && (
            <>
              <dt>last error</dt>
              <dd className="bad">
                {raw.last_error.op_name} ({raw.last_error.tier}): {raw.last_error.error_message ?? raw.last_error.error_class ?? "failed"}
                {raw.last_error.at ? ` — ${ago(raw.last_error.at, now)}` : ""}
              </dd>
            </>
          )}
        </dl>
      </section>

      <section className="panel-section" aria-label="Network interfaces">
        <h3>NICs</h3>
        <table className="mini">
          <thead>
            <tr>
              <th scope="col">#</th>
              <th scope="col">segment</th>
              <th scope="col">address</th>
            </tr>
          </thead>
          <tbody>
            {[...raw.nets]
              .sort((a, b) => (a.nic_index ?? 99) - (b.nic_index ?? 99))
              .map((n) => (
                <tr key={`${n.net_id}-${n.nic_index ?? "?"}`}>
                  <td>{n.nic_index ?? "?"}</td>
                  <td className="mono">
                    {n.external ? (
                      "WAN uplink"
                    ) : (
                      <button type="button" className="link mono" onClick={() => props.onSegment(n.net_id)}>
                        {n.net_id}
                      </button>
                    )}
                  </td>
                  <td className="mono">{n.ip ?? "not recorded"}</td>
                </tr>
              ))}
          </tbody>
        </table>
        {raw.taps.length > 0 && (
          <p className="meta">
            Mirrored to{" "}
            {raw.taps.map((t) => `${t.sensors.join(", ")} (${t.net_id})`).join("; ")} — recorded, not capture-verified.
          </p>
        )}
      </section>

      <section className="panel-section" aria-label="Access">
        <h3>Access</h3>
        {access.loading && <p className="meta">…</p>}
        {access.error && <p className="meta">Access facts unavailable: {access.error.message}</p>}
        {hostAccess && (
          <dl className="facts">
            <dt>log in</dt>
            <dd className="mono">{hostAccess.hint ?? `${hostAccess.user ?? "?"}@${hostAccess.ip}`}</dd>
            <dt>user</dt>
            <dd className="mono">{hostAccess.user ?? "none (no default login)"}</dd>
            <dt>password</dt>
            <dd className="meta">
              {hostAccess.password === null ? (
                "none"
              ) : (
                <>
                  not shown here — <code>axrange te info</code> prints it
                </>
              )}
            </dd>
          </dl>
        )}
        {access.data && !hostAccess && <p className="meta">No access facts recorded for this host.</p>}
      </section>
    </div>
  );
}

function FragmentPair({ name, value }: { name: string; value: string }): JSX.Element {
  return (
    <>
      <dt className="mono">{name}</dt>
      <dd>{value}</dd>
    </>
  );
}
