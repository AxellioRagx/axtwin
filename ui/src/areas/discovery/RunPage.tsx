import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";

import { useApi } from "../../api/context";
import type { DiscoveryRun } from "../../api/types";
import { formatDuration, formatIso, formatTs, runProgress } from "../../model/sources";
import { ErrorLine, StatusBadge } from "../shell/parts";
import { useLoad } from "../shell/useLoad";
import { ApiError } from "../../api/client";
import { PitName } from "./PitName";
import { RunAction } from "./RunActions";

export const POLL_MS = 2000;

/** One run, polled while it is queued or running (C1.2); a completed run
 *  offers its imprint. */
export function RunPage() {
  const api = useApi();
  const { id = "" } = useParams();
  const navigate = useNavigate();
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
  const progress = run ? runProgress(run, now) : null;
  const startedMs = run?.started_at ? Date.parse(run.started_at) : NaN;
  const endMs = run?.finished_at ? Date.parse(run.finished_at) : now;
  const elapsed = Number.isFinite(startedMs) ? (endMs - startedMs) / 1000 : NaN;
  const activeStage = run ? run.stages.find((s) => !run.stages_done.includes(s)) : undefined;
  // The stage a failed run died in is the first one it never finished.
  const failedStage = run?.status === "failed" ? activeStage : undefined;

  return (
    <>
      <ErrorLine error={error} prefix="could not read the run" />
      {run ? (
        <>
          <div className="row">
            <h1 style={{ flex: 1 }}>
              Run {run.id} <StatusBadge status={run.cancel_requested_at && active ? "cancelling" : run.status} />
            </h1>
            <RunAction run={run} onCancelled={setRun} onDeleted={() => navigate("/discovery/runs")} />
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
              {run.pit_id ? (
                <>
                  <dt>PIT</dt>
                  <dd>
                    <PitName run={run} onRenamed={(name) => setRun({ ...run, pit_name: name })} />
                  </dd>
                </>
              ) : null}
              <dt>Started</dt>
              <dd>{formatIso(run.started_at)}</dd>
              <dt>Elapsed</dt>
              <dd>{Number.isFinite(elapsed) ? formatDuration(elapsed) : "—"}</dd>
            </dl>
            {progress ? <ProgressBar run={run} progress={progress} active={!!active} /> : null}
            <ul className="stages" aria-label="Stages">
              {run.stages.map((s) => (
                <li
                  key={s}
                  className={run.stages_done.includes(s) ? "done" : s === failedStage ? "failed" : s === activeStage && active ? "active" : ""}
                >
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
            {run.status === "cancelled" ? (
              <div className="notice muted-notice">Cancelled; the partial bundle and belief store were removed and the imprint id is free again.</div>
            ) : null}
            {run.status === "completed" ? <CompletedNotice run={run} /> : null}
          </div>
        </>
      ) : !error ? (
        <p className="muted">Loading…</p>
      ) : null}
    </>
  );
}

/** The window as a bar: where the ingest has reached between the run's bounds, and the
 *  rate-based remainder. After ingest the bar is full and the later stages carry on. */
function ProgressBar({ run, progress, active }: { run: DiscoveryRun; progress: ReturnType<typeof runProgress>; active: boolean }) {
  if (!progress || run.ts_from === null || run.ts_to === null) return null;
  const pct = Math.round(progress.fraction * 100);
  const spanDays = (run.ts_to - run.ts_from) / 86400;
  let line: string;
  if (progress.ingestDone) {
    line = active ? `Ingest covered the whole window; ${run.stages.find((s) => !run.stages_done.includes(s)) ?? "finishing"} in progress.` : "Ingest covered the whole window.";
  } else if (progress.reached === null) {
    line = active ? "Ingest has not closed a window yet." : "Ingest closed no window.";
  } else {
    const coveredDays = (progress.reached - run.ts_from) / 86400;
    line = `Ingest has reached ${formatTs(progress.reached)}: ${coveredDays.toFixed(2)} of ${spanDays.toFixed(2)} days (${pct}%)`;
    if (active && progress.remainingS !== null) line += `, about ${formatDuration(progress.remainingS)} to go at this rate`;
    line += ".";
  }
  return (
    <div className="progress-block" data-testid="progress">
      <div className="progress" role="progressbar" aria-label="Ingest progress" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct}>
        <div className="progress-fill" style={{ width: `${pct}%` }} />
        {progress.reached !== null && !progress.ingestDone ? (
          <span className="progress-mark" style={{ left: `${pct}%` }} title={formatTs(progress.reached)} />
        ) : null}
      </div>
      <div className="progress-ends muted">
        <span>{formatTs(run.ts_from)}</span>
        <span>{formatTs(run.ts_to)}</span>
      </div>
      <p className="progress-line">{line}</p>
    </div>
  );
}

/** A completed run offers its imprint only after checking the bundle is still in the
 *  catalog: the run record outlives a bundle someone removed, and a link to nothing
 *  is worse than saying so. */
function CompletedNotice({ run }: { run: DiscoveryRun }) {
  const api = useApi();
  const imprint = useLoad((signal) => api.getImprint(run.imprint_id, signal), [run.imprint_id]);
  const gone = imprint.error instanceof ApiError && imprint.error.kind === "not-found";
  return (
    <div className={gone ? "error" : "notice"} role={gone ? "alert" : undefined}>
      Completed; PIT <code>{run.pit_name ?? run.pit_id}</code>.{" "}
      {gone ? (
        <>Imprint {run.imprint_id} is no longer in the catalog (the bundle was removed after this run).</>
      ) : imprint.data ? (
        <Link to={`/imprints/${encodeURIComponent(run.imprint_id)}`} data-testid="open-imprint">
          Open imprint {run.imprint_id}
        </Link>
      ) : imprint.error ? (
        <span className="muted">could not check the imprint: {String((imprint.error as Error).message ?? imprint.error)}</span>
      ) : (
        <span className="muted">checking the catalog…</span>
      )}
    </div>
  );
}
