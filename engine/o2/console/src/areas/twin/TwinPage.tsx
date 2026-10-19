/**
 * One deployed twin (ruling §2): what it looks like (a map and a host table
 * that share selection and the URL), the personas on each host, per-host
 * health and access, changing one host's personas, and destroy.
 *
 * One model behind all of it -- `GET /api/deploys/{id}/hosts`, adapted by
 * `model/deployed.ts` (rule 2). Everything from it is RECORDED; the live
 * power sweep and the per-host live check are fetched only when asked, and
 * carry their age (rule 3).
 */
import { useCallback, useMemo, useRef, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router";

import { useRuntimeApi } from "../../api/runtimeContext";
import { useHosts, useNow, useOnDemand } from "../../api/runtimeHooks";
import type { HealthDoc, TwinHostsDoc } from "../../api/runtimeTypes";
import { hostPath, parseTwinQuery, personasPath, rangePath, twinPath, type TwinQuery } from "../../model/appRoutes";
import { buildDeployedTwin } from "../../model/deployed";
import { ago, count, plural } from "../../model/format";
import { ProtectedAction, ProtectedBadge } from "../shell/ProtectionOverride";
import { ErrorNotice, isMissingEndpoint, LiveTag, RecordedTag, StatusBadge } from "../shell/parts";
import { DestroyDialog } from "./DestroyDialog";
import { HostPanel } from "./HostPanel";
import { HostTable } from "./HostTable";
import { PersonaEditor } from "./PersonaEditor";
import { TapsSection } from "./TapsSection";
import { TwinMap } from "./TwinMap";

export function TwinPage({
  deployId,
  host,
  personas,
}: {
  deployId: string;
  host: string | null;
  personas: boolean;
}): JSX.Element {
  const location = useLocation();
  const navigate = useNavigate();
  const api = useRuntimeApi();
  const query = useMemo(() => parseTwinQuery(location.search), [location.search]);
  const target = query.target;
  const hosts = useHosts(deployId, target);
  // A reload (after a persona change) must not blank the page and unmount the
  // editor showing its result: the last answer is HELD while the next is read
  // -- keyed on the subject it was read for, never shown for another (LES-259).
  const subject = `${deployId}|${target ?? ""}`;
  const held = useRef<{ subject: string; doc: TwinHostsDoc } | null>(null);
  if (hosts.data) held.current = { subject, doc: hosts.data };
  const doc0 = hosts.data ?? (held.current?.subject === subject ? held.current.doc : null);
  const model = useMemo(() => (doc0 ? buildDeployedTwin(doc0) : null), [doc0]);
  const tick = useNow(5000);
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(new Set());
  const [destroying, setDestroying] = useState(false);
  // An override of protection, typed by the operator on THIS page; it lasts
  // until they leave the twin (the page is keyed by deploy id) and is passed
  // to each write, which sends it as `override_protection`.
  const [override, setOverride] = useState<string | null>(null);

  const live = useOnDemand<HealthDoc>(`${deployId}|${target ?? ""}`, (signal) =>
    api.getHealth(deployId, target, null, signal),
  );
  // An age shown right after an answer lands must not use a clock read
  // before it (the ticking clock can be up to 5 s stale).
  const now = Math.max(tick, live.receivedAt ?? 0);
  const powerByHost = useMemo(() => {
    const out = new Map<string, string>();
    for (const h of live.data?.hosts ?? []) out.set(h.host_id, h.power?.state ?? "unknown");
    return out;
  }, [live.data]);

  const setQuery = useCallback(
    (patch: Partial<TwinQuery>, opts: { replace?: boolean } = {}) => {
      const next = { ...query, ...patch };
      const path = host
        ? personas
          ? personasPath(deployId, host, next)
          : hostPath(deployId, host, next)
        : twinPath(deployId, next);
      void navigate(path, { replace: opts.replace ?? false });
    },
    [query, host, personas, deployId, navigate],
  );

  const selectHost = useCallback(
    (id: string | null) => {
      void navigate(id ? hostPath(deployId, id, query) : twinPath(deployId, query));
    },
    [deployId, query, navigate],
  );

  const toggleGroup = useCallback((key: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);

  if (hosts.error && !doc0) {
    return (
      <section className="page">
        <p>
          <Link to={rangePath()}>← Range</Link>
        </p>
        <h1 className="mono">{deployId}</h1>
        <ErrorNotice
          error={hosts.error}
          what="Reading this twin"
          notFound={
            isMissingEndpoint(hosts.error)
              ? "This server does not have the twin hosts endpoint (it predates the Unified Console) — restart `axrange serve` from this branch."
              : `No twin deploy named ${deployId} on this cluster${target ? ` (${target})` : ""}. It may have been destroyed — see the Range.`
          }
        />
      </section>
    );
  }
  if (!model || !doc0) {
    return (
      <section className="page">
        <p className="meta">Reading twin {deployId}…</p>
      </section>
    );
  }

  const doc = doc0;
  const selected = host ? model.hostById.get(host) ?? null : null;

  return (
    <section className="page twin-page" aria-labelledby="twin-title">
      <nav className="crumbs" aria-label="Breadcrumb">
        <Link to={rangePath()}>Range</Link> <span aria-hidden="true">›</span>{" "}
        <Link to={twinPath(deployId, { target })}>{model.twinId ?? deployId}</Link>
        {host && (
          <>
            {" "}
            <span aria-hidden="true">›</span> <span className="mono">{host}</span>
          </>
        )}
      </nav>
      <header className="twin-head">
        <div>
          <h1 id="twin-title">
            {model.twinId ?? "unknown twin"}
            {doc.protected && <ProtectedBadge />}
          </h1>
          <p className="mono meta">
            {deployId}
            {model.twinId && (
              <>
                {" · "}
                <Link to={`/design/${encodeURIComponent(model.twinId)}`} data-testid="open-design" title="This twin's design (Design area)">
                  Design
                </Link>
              </>
            )}
          </p>
        </div>
        <dl className="summary" data-testid="twin-summary">
          <div>
            <dt>cluster</dt>
            <dd>{doc.target ?? target ?? "unknown"}</dd>
          </div>
          <div>
            <dt>status</dt>
            <dd>
              <StatusBadge status={doc.deploy_status} /> <RecordedTag />
            </dd>
          </div>
          <div>
            <dt>hosts</dt>
            <dd>{model.hosts.length}</dd>
          </div>
          <div>
            <dt>personas</dt>
            <dd>
              {doc.tfc
                ? `${plural(model.personaInstanceCount, "running instance")} on ${plural(model.hostsWithPersonas, "host")}`
                : "no traffic deploy yet"}
            </dd>
          </div>
          <div>
            <dt>traffic</dt>
            <dd>{doc.tfc ? <StatusBadge status={doc.tfc.status} title={doc.tfc.deploy_id} /> : "none"}</dd>
          </div>
          <div>
            <dt>taps</dt>
            <dd>{doc.tap ? <StatusBadge status={doc.tap.status} title={doc.tap.deploy_id} /> : "none"}</dd>
          </div>
          {doc.egress && (
            <div>
              <dt>containment</dt>
              <dd title={`router ${doc.egress.router_host ?? "?"}: declared ${doc.egress.phase_declared ?? "?"}, applied ${doc.egress.phase_applied ?? "?"}`}>
                {doc.egress.contained ? "traffic held inside the twin" : "NOT contained"}{" "}
                <RecordedTag />
              </dd>
            </div>
          )}
        </dl>
        <div className="twin-actions">
          <button type="button" className="btn" onClick={live.run} disabled={live.loading} data-testid="check-live">
            {live.loading ? "Checking…" : live.data ? "Re-check live status" : "Check live status"}
          </button>
          {live.data && <LiveTag checkedAt={live.data.checked_at} now={now} />}
          {live.error && <span className="meta bad">live check failed: {live.error.message}</span>}
          <ProtectedAction
            protectedTwin={!!doc.protected}
            deployId={deployId}
            override={override}
            onOverride={setOverride}
            what="destroying it"
            label="Destroy…"
            className="btn danger"
            onAct={() => setDestroying(true)}
            testId="destroy-open"
          />
        </div>
      </header>
      {hosts.error && (
        <p className="notice is-error" role="alert">
          Re-reading this twin failed ({hosts.error.message}); showing the previous answer.
        </p>
      )}
      {model.warnings.length > 0 && (
        <ul className="warnings">
          {model.warnings.map((w) => (
            <li key={w} className="notice">
              {w}
            </li>
          ))}
        </ul>
      )}
      {live.data && live.data.cluster?.state !== "reachable" && (
        <p className="notice">
          The cluster was not reachable for the live check ({live.data.cluster?.reason ?? "no reason given"}); every live
          value below is unknown.
        </p>
      )}

      {/* Two views of equal standing (fix round 2): one fills the page and
          scrolls on its own; the choice is in the URL (`?view=hosts`), and the
          selection (segment, host) is shared by both. */}
      <div className="view-switch" role="tablist" aria-label="Twin views" data-testid="view-switch">
        {(
          [
            ["map", "Map", `${plural(model.segments.length, "segment")}`],
            ["hosts", "Hosts", `${plural(model.hosts.length, "host")}`],
          ] as const
        ).map(([v, label, hint]) => (
          <button
            key={v}
            type="button"
            role="tab"
            id={`tab-${v}`}
            aria-selected={query.view === v}
            aria-controls="twin-view"
            className={`view-tab${query.view === v ? " is-current" : ""}`}
            onClick={() => setQuery({ view: v })}
            data-testid={`view-${v}`}
          >
            {label} <span className="meta">{hint}</span>
          </button>
        ))}
      </div>
      <div className={`twin-body${selected ? " has-panel" : ""}`}>
        <div className="twin-main twin-view" id="twin-view" role="tabpanel" aria-labelledby={`tab-${query.view}`}>
          {query.view === "map" ? (
            <TwinMap
              model={model}
              segment={query.segment}
              selectedHost={host}
              expanded={expanded}
              onToggleGroup={toggleGroup}
              onSegment={(segment) => setQuery({ segment })}
              onHost={selectHost}
              livePower={live.data ? powerByHost : null}
            />
          ) : (
            <>
              <HostTable
                model={model}
                query={query}
                onQuery={setQuery}
                selectedHost={host}
                onHost={selectHost}
                live={live.data ? { power: powerByHost, checkedAt: live.data.checked_at } : null}
                now={now}
              />
              <TapsSection tap={doc.tap} hosts={doc.hosts} />
            </>
          )}
        </div>
        {host && (
          <aside className="twin-panel" aria-label={`Host ${host}`}>
            {selected ? (
              personas ? (
                <PersonaEditor
                  protectedTwin={!!doc.protected}
                  override={override}
                  onOverride={setOverride}
                  key={`${deployId}|${host}`}
                  model={model}
                  host={selected}
                  target={target}
                  onDone={() => hosts.reload()}
                  onClose={() => void navigate(hostPath(deployId, host, query))}
                />
              ) : (
                <HostPanel
                  protectedTwin={!!doc.protected}
                  override={override}
                  onOverride={setOverride}
                  key={`${deployId}|${host}`}
                  model={model}
                  host={selected}
                  target={target}
                  now={now}
                  onClose={() => selectHost(null)}
                  onEditPersonas={() => void navigate(personasPath(deployId, host, query))}
                  onSegment={(segment) => setQuery({ segment })}
                />
              )
            ) : (
              <div className="panel">
                <p className="notice">
                  This twin has no host named <span className="mono">{host}</span> in its recorded inventory.
                </p>
                <button type="button" className="btn small" onClick={() => selectHost(null)}>
                  Close
                </button>
              </div>
            )}
          </aside>
        )}
      </div>
      {destroying && (
        <DestroyDialog
          deployId={deployId}
          target={target}
          twinName={model.twinId}
          hostCount={model.hosts.length}
          doc={doc}
          override={override}
          onClose={() => setDestroying(false)}
          onDestroyed={() => void navigate(rangePath())}
        />
      )}
      <p className="meta footnote">
        {count(model.segments.length, "segment")} · host facts are <strong>recorded</strong> (what axRange recorded when
        it deployed); use “Check live status” for the cluster&apos;s own view.
        {live.data ? ` Last live check ${ago(live.data.checked_at, now)}.` : ""}
      </p>
    </section>
  );
}
