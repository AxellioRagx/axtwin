import { useEffect, useState } from "react";
import { Link, useParams } from "react-router";

import { useApi } from "../../api/context";
import type { DiscoveryRun } from "../../api/types";
import { formatDuration, formatIso, formatTs } from "../../model/sources";
import { ErrorLine, StatusBadge } from "../shell/parts";

export const POLL_MS = 2000;

/** One run, polled while it is queued or running (C1.2); a completed run
 *  offers its imprint. */
export function RunPage() {
  const api = useApi();
  const { id = "" } = useParams();
  const [run, setRun] = useState<DiscoveryRun | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    const ctl = new AbortController();
    async function tick() {
      try {
        const r = await api.getRun(id, ctl.signal);
        if (ctl.signal.aborted) return;
        setRun(r);
        setError(null);
        setNow(Date.now());
        if (r.status === "queued" || r.status === "running") timer = setTimeout(tick, POLL_MS);
      } catch (e) {
        if (ctl.signal.aborted) return;
        setError(e);
      }
    }
    tick();
    return () => {
      ctl.abort();
      if (timer) clearTimeout(timer);
    };
  }, [api, id]);

  const active = run && (run.status === "queued" || run.status === "running");
  const startedMs = run?.started_at ? Date.parse(run.started_at) : NaN;
  const endMs = run?.finished_at ? Date.parse(run.finished_at) : now;
  const elapsed = Number.isFinite(startedMs) ? (endMs - startedMs) / 1000 : NaN;
  const activeStage = run ? run.stages.find((s) => !run.stages_done.includes(s)) : undefined;

  return (
    <>
      <ErrorLine error={error} prefix="could not read the run" />
      {run ? (
        <>
          <div className="row">
            <h1 style={{ flex: 1 }}>
              Run {run.id} <StatusBadge status={run.status} />
            </h1>
          </div>
          <div className="panel">
            <dl className="facts">
              <dt>Plugin</dt>
              <dd>{run.plugin}</dd>
              <dt>Imprint</dt>
              <dd>{run.imprint_id}</dd>
              {run.source_id ? (
                <>
                  <dt>Source</dt>
                  <dd>
                    <Link to={`/discovery/sources/${encodeURIComponent(run.source_id)}`}>{run.source_id}</Link>
                  </dd>
                </>
              ) : null}
              <dt>Window</dt>
              <dd>
                {run.ts_from !== null && run.ts_to !== null
                  ? `${formatTs(run.ts_from)} → ${formatTs(run.ts_to)} (${((run.ts_to - run.ts_from) / 86400).toFixed(2)} days)`
                  : active
                    ? "resolving from the source's span"
                    : "not resolved"}
              </dd>
              <dt>Reached via</dt>
              <dd>{run.via ?? (active ? "opening" : "—")}</dd>
              <dt>Started</dt>
              <dd>{formatIso(run.started_at)}</dd>
              <dt>Elapsed</dt>
              <dd>{Number.isFinite(elapsed) ? formatDuration(elapsed) : "—"}</dd>
            </dl>
            <ul className="stages" aria-label="Stages">
              {run.stages.map((s) => (
                <li key={s} className={run.stages_done.includes(s) ? "done" : s === activeStage && active ? "active" : ""}>
                  {s}
                </li>
              ))}
            </ul>
            {active ? <p className="muted">Polling every {POLL_MS / 1000}s. A multi-day read takes a while; this page keeps up.</p> : null}
            {run.status === "failed" ? (
              <div className="error" role="alert">
                {run.error ?? "failed without a reason"}
              </div>
            ) : null}
            {run.status === "completed" ? (
              <div className="notice">
                Completed; PIT <code>{run.pit_id}</code>.{" "}
                <Link to={`/imprints/${encodeURIComponent(run.imprint_id)}`} data-testid="open-imprint">
                  Open imprint {run.imprint_id}
                </Link>
              </div>
            ) : null}
          </div>
        </>
      ) : !error ? (
        <p className="muted">Loading…</p>
      ) : null}
    </>
  );
}
