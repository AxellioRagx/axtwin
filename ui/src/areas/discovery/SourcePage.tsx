import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";

import { useApi } from "../../api/context";
import type { DiscoveryPlugin, DiscoveryRun, DiscoverySource, ProbeResult, SourceRunRequest } from "../../api/types";
import {
  defaultImprintId,
  describeAccess,
  formatBig,
  formatCount,
  formatTs,
  lastRunOf,
  prefillHint,
  runOptions,
  runPrefill,
} from "../../model/sources";
import {
  DEFAULT_INTERVAL,
  DEFAULT_SUBBUCKETS,
  INTERVAL_PRESETS,
  QUICK_RANGES,
  type RangeChoice,
  burstChoices,
  clampToSpan,
  estimateCost,
  fitSubbuckets,
  formatSpan,
  fromLocalInput,
  gapsIn,
  rangeBody,
  recentRanges,
  relativeLabel,
  resolveRange,
  spanOf,
  toLocalInput,
  zoneLabel,
} from "../../model/timeRange";
import { ConfirmDelete } from "../shell/ConfirmDelete";
import { EntityName, ErrorLine, LinkRow, StatusBadge, When } from "../shell/parts";
import { useLoad } from "../shell/useLoad";
import { ProbeView } from "./ProbeView";

/** One source: how it is reached, what the last test found, and the run form. */
export function SourcePage() {
  const api = useApi();
  const navigate = useNavigate();
  const { id = "" } = useParams();
  const source = useLoad((signal) => api.getSource(id, signal), [id]);
  const plugins = useLoad((signal) => api.listPlugins(signal), []);
  const runs = useLoad((signal) => api.listRuns(signal), [id]);
  const plugin = plugins.data?.plugins.find((p) => p.name === source.data?.plugin);
  return (
    <>
      <ErrorLine error={source.error} prefix="could not load the source" />
      {source.data ? (
        <>
          <div className="row">
            <h1 style={{ flex: 1 }}>{source.data.name}</h1>
            <Link to={`/discovery/sources/${encodeURIComponent(id)}/edit`}>
              <button type="button">Edit</button>
            </Link>
            <ConfirmDelete label="Delete source" target={source.data.name} onDelete={() => api.deleteSource(id)} onDeleted={() => navigate("/discovery")} />
          </div>
          <div className="panel">
            <dl className="facts">
              <dt>Plugin</dt>
              <dd>{source.data.plugin}</dd>
              <dt>Endpoint</dt>
              <dd>
                <code>{source.data.endpoint}</code>
              </dd>
              <dt>Access</dt>
              <dd>{describeAccess(source.data)}</dd>
            </dl>
          </div>
          <ProbePanel source={source.data} onProbed={source.reload} />
          <RunForm
            source={source.data}
            plugin={plugin}
            lastRun={lastRunOf(runs.data?.runs, id)}
            runs={runs.data?.runs}
          />
          <h2>Runs from this source</h2>
          <ErrorLine error={runs.error} />
          {runs.data ? (
            <RunsOfSource runs={runs.data.runs.filter((r) => r.source_id === id)} />
          ) : null}
        </>
      ) : source.loading ? (
        <p className="muted">Loading…</p>
      ) : null}
    </>
  );
}

function RunsOfSource({ runs }: { runs: DiscoveryRun[] }) {
  if (runs.length === 0) return <p className="muted">No run yet since the API started.</p>;
  return (
    <table className="list">
      <thead>
        <tr>
          <th>Run</th>
          <th>Status</th>
          <th>Imprint</th>
          <th>Window</th>
          <th>Started</th>
        </tr>
      </thead>
      <tbody>
        {runs.map((r) => (
          <LinkRow key={r.id} to={`/discovery/runs/${encodeURIComponent(r.id)}`}>
            <td>
              <EntityName id={r.id} to={`/discovery/runs/${encodeURIComponent(r.id)}`} />
            </td>
            <td>
              <StatusBadge status={r.status} />
            </td>
            <td>{r.name}</td>
            <td className="muted">
              {r.ts_from !== null && r.ts_to !== null ? (
                <>
                  <When value={r.ts_from} /> → <When value={r.ts_to} />
                </>
              ) : (
                "resolving"
              )}
            </td>
            <td>
              <When value={r.started_at ?? r.created_at} />
            </td>
          </LinkRow>
        ))}
      </tbody>
    </table>
  );
}

function ProbePanel({ source, onProbed }: { source: DiscoverySource; onProbed: () => void }) {
  const api = useApi();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [fresh, setFresh] = useState<ProbeResult | null>(null);
  const probe = fresh ?? source.last_probe;
  async function test() {
    setBusy(true);
    setError(null);
    try {
      setFresh(await api.probeSource(source.id));
      onProbed();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="panel" data-testid="probe-panel">
      <div className="row">
        <h2 style={{ flex: 1, margin: 0 }}>Reachability and data held</h2>
        <button type="button" onClick={test} disabled={busy}>
          {busy ? "Testing…" : "Test connection"}
        </button>
      </div>
      <ErrorLine error={error} prefix="the test could not run" />
      {probe ? (
        <ProbeView probe={probe} />
      ) : (
        <p className="muted">Not tested yet. Testing opens the access path and reads the source's own census.</p>
      )}
    </div>
  );
}

/** Run options the form draws itself (the Advanced block), or no longer takes at all. */
const DRAWN_OPTIONS = ["window", "subbuckets", "network", "vantage"];

/** What the last test's census reported for window / subbuckets, if anything: the lowest
 *  pre-fill tier above the built-ins (`runPrefill`). The vantage is the sensor's to report. */
function probeRunExtras(source: DiscoverySource): Record<string, string> {
  const out: Record<string, string> = {};
  const probe = source.last_probe;
  if (!probe || !probe.reachable) return out;
  for (const k of ["window", "subbuckets"] as const) {
    const v = probe.extra[k];
    if (typeof v === "number" && Number.isFinite(v)) out[k] = String(v);
  }
  return out;
}

const INTERVAL_HELP =
  "Traffic is summarised in slices this long: each host's ports, peers and volumes per slice, then combined into one imprint.";
const BURST_HELP =
  "How finely flows are counted inside each interval to score burstiness. Recorded on the imprint; not used by the twin yet.";

const positive = (raw: string | undefined, fallback: number) => {
  const n = Number(raw);
  return Number.isFinite(n) && n > 0 ? Math.round(n) : fallback;
};

/**
 * The run form. Its starting values come from `runPrefill`: the source's last run, then the
 * source's legacy saved defaults, then the built-ins; the hint says which. The runs list
 * loads after the page, so the pre-fill can change once -- it is re-applied to every field
 * the operator has NOT touched, never to one they typed in.
 *
 * The time range is counted from the END of the source's data (the last test's `last_ts`),
 * never from now. A quick range is relative (`days`, re-resolved when the run starts); typed
 * bounds or a recently used range are absolute (`ts_from`/`ts_to`, sent as-is). Either way
 * the run record keeps the bounds actually read.
 */
function RunForm({
  source,
  plugin,
  lastRun,
  runs,
}: {
  source: DiscoverySource;
  plugin: DiscoveryPlugin | undefined;
  lastRun: DiscoveryRun | undefined;
  runs: DiscoveryRun[] | undefined;
}) {
  const api = useApi();
  const navigate = useNavigate();
  const imprints = useLoad((signal) => api.listImprints(signal), []);
  const options = useMemo(() => runOptions(plugin), [plugin]);
  const probeExtras = useMemo(() => probeRunExtras(source), [source]);
  const prefill = useMemo(() => runPrefill(source, lastRun, probeExtras), [source, lastRun, probeExtras]);
  const span = useMemo(() => spanOf(source.last_probe), [source.last_probe]);
  const recent = useMemo(() => recentRanges(runs, source.id), [runs, source.id]);
  const [touched, setTouched] = useState<ReadonlySet<string>>(() => new Set());
  const touch = (key: string) => setTouched((t) => (t.has(key) ? t : new Set(t).add(key)));

  const [imprintId, setImprintId] = useState(() => defaultImprintId(source.name, new Date()));
  const [choice, setChoice] = useState<RangeChoice>({ kind: "relative", seconds: prefill.seconds });
  const [clamped, setClamped] = useState(false);
  const [search, setSearch] = useState("");
  const prefillInterval = positive(prefill.values.window, DEFAULT_INTERVAL);
  const [interval, setIntervalS] = useState(prefillInterval);
  const [subbuckets, setSubbuckets] = useState(fitSubbuckets(prefillInterval, positive(prefill.values.subbuckets, DEFAULT_SUBBUCKETS)));
  const [values, setValues] = useState<Record<string, string>>(() => ({ ...prefill.values }));
  const [adding, setAdding] = useState(false);
  const [baseId, setBaseId] = useState("");

  useEffect(() => {
    if (!touched.has("range")) setChoice({ kind: "relative", seconds: prefill.seconds });
    const iv = positive(prefill.values.window, DEFAULT_INTERVAL);
    if (!touched.has("interval")) setIntervalS(iv);
    if (!touched.has("interval") && !touched.has("subbuckets")) {
      setSubbuckets(fitSubbuckets(iv, positive(prefill.values.subbuckets, DEFAULT_SUBBUCKETS)));
    }
    setValues((v) => {
      const next = { ...v };
      for (const k of new Set([...Object.keys(v), ...Object.keys(prefill.values)])) {
        if (touched.has(k)) continue;
        if (k in prefill.values) next[k] = prefill.values[k]!;
        else delete next[k];
      }
      return next;
    });
    // Only a new pre-fill re-applies; a touch never pulls a value back in.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prefill]);

  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  const pits = imprints.data?.pits ?? [];
  const base = adding ? pits.find((p) => p.pit_id === baseId) : undefined;
  const baseLineage = base?.lineage && base.lineage.extendable ? base.lineage : null;
  const effInterval = baseLineage ? baseLineage.window_seconds : interval;
  const effSubbuckets = baseLineage ? baseLineage.subbuckets : subbuckets;
  const hasInterval = options.some((o) => o.key === "window");
  const hasBurst = options.some((o) => o.key === "subbuckets");
  const otherOptions = options.filter((o) => !DRAWN_OPTIONS.includes(o.key));

  const resolved = resolveRange(choice, span);
  const length = resolved ? resolved.to - resolved.from : choice.kind === "relative" ? choice.seconds : null;
  const estimate = estimateCost(resolved, span, source.last_probe?.extra);
  const gaps = gapsIn(resolved, source.last_probe?.extra);

  let problem: string | null = null;
  if (choice.kind === "absolute" && !(choice.to > choice.from)) problem = "From must be before To.";
  else if (choice.kind === "relative" && choice.seconds === null && !span) problem = "Test the connection to run the entire span.";
  else if (length !== null && length < effInterval)
    problem = `The time range (${formatSpan(length)}) is shorter than one measurement interval (${formatSpan(effInterval)}).`;
  else if (adding && !baseLineage) problem = "Choose the imprint to add to.";

  const setOption = (key: string, value: string) => {
    touch(key);
    setValues((v) => ({ ...v, [key]: value }));
  };
  const pickRelative = (seconds: number | null) => {
    touch("range");
    setClamped(false);
    setChoice({ kind: "relative", seconds });
  };
  const pickAbsolute = (from: number, to: number) => {
    touch("range");
    const c = clampToSpan(from, to, span);
    setClamped(c.clamped);
    setChoice({ kind: "absolute", from: c.from, to: c.to });
  };
  const editBound = (which: "from" | "to", raw: string) => {
    const v = fromLocalInput(raw);
    if (v === null) return;
    const cur = resolved ?? { from: v, to: v };
    pickAbsolute(which === "from" ? v : cur.from, which === "to" ? v : cur.to);
  };
  const changeInterval = (iv: number) => {
    touch("interval");
    setIntervalS(iv);
    setSubbuckets((n) => fitSubbuckets(iv, n));
  };
  const toggleAdding = (on: boolean) => {
    setAdding(on);
    if (!on) {
      // Editable again, back at the form's own defaults.
      setIntervalS(prefillInterval);
      setSubbuckets(fitSubbuckets(prefillInterval, positive(prefill.values.subbuckets, DEFAULT_SUBBUCKETS)));
      setTouched((t) => {
        const n = new Set(t);
        n.delete("interval");
        n.delete("subbuckets");
        return n;
      });
    }
  };

  const q = search.trim().toLowerCase();
  const quick = [...QUICK_RANGES];
  if (choice.kind === "relative" && !quick.some((r) => r.seconds === choice.seconds)) {
    quick.unshift({ id: "prefill", label: relativeLabel(choice.seconds), seconds: choice.seconds });
  }
  const recentLabel = (r: { from: number; to: number; runId: string }) => `${formatTs(r.from)} → ${formatTs(r.to)} (${r.runId})`;
  const shownQuick = quick.filter((r) => !q || r.label.toLowerCase().includes(q));
  const shownRecent = recent.filter((r) => !q || recentLabel(r).toLowerCase().includes(q));

  async function start(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (problem) {
      setError(new Error(problem));
      return;
    }
    const rb = rangeBody(choice, span);
    if (!rb) {
      setError(new Error("the time range could not be resolved; test the connection first"));
      return;
    }
    const body: SourceRunRequest = { name: (baseLineage ? baseLineage.next_name : imprintId).trim(), ...rb };
    if (baseLineage && base) {
      // The base's interval and resolution apply; the server refuses a different one.
      body.base_imprint = base.pit_id;
    } else {
      if (hasInterval) body.window = interval;
      if (hasBurst) body.subbuckets = subbuckets;
    }
    for (const opt of otherOptions) {
      const raw = values[opt.key]?.trim();
      if (!raw) continue;
      if (opt.type === "integer" || opt.type === "number") {
        const n = Number(raw);
        if (!Number.isFinite(n)) {
          setError(new Error(`${opt.key} must be a number`));
          return;
        }
        body[opt.key] = n;
      } else {
        body[opt.key] = raw;
      }
    }
    setBusy(true);
    try {
      const run = await api.startRun(source.id, body);
      navigate(`/discovery/runs/${encodeURIComponent(run.id)}`);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  const selectedLabel =
    choice.kind === "relative"
      ? `${relativeLabel(choice.seconds)}${resolved ? `: ${formatTs(resolved.from)} → ${formatTs(resolved.to)}` : ""} — re-resolved from the end of the data when the run starts`
      : `${formatTs(choice.from)} → ${formatTs(choice.to)} (absolute, sent as-is)`;

  return (
    <form className="panel" onSubmit={start} aria-label="Run discovery">
      <h2 style={{ marginTop: 0 }}>Run discovery</h2>
      <p className="hint" data-testid="run-prefill-hint">
        {prefill.from.kind === "run" ? (
          <>
            Filled from run{" "}
            <Link to={`/discovery/runs/${encodeURIComponent(prefill.from.runId)}`}>{prefill.from.runId}</Link>, the last
            one from this source.
          </>
        ) : (
          prefillHint(prefill.from)
        )}
      </p>
      <div className="field">
        <label htmlFor="run-imprint">Imprint name</label>
        {adding ? (
          <>
            <input id="run-imprint" type="text" readOnly value={baseLineage ? baseLineage.next_name : ""} placeholder="choose the imprint to add to (Advanced)" />
            <span className="hint" data-testid="run-next-version">
              {baseLineage && base
                ? `Adds to ${base.name} as version ${baseLineage.version + 1}; ${base.name} itself is left as it is.`
                : "Choose the imprint to add to, under Advanced."}
            </span>
          </>
        ) : (
          <>
            <input id="run-imprint" type="text" value={imprintId} onChange={(e) => setImprintId(e.target.value)} />
            <span className="hint">What the baked imprint is called; rename it later if you like. An existing name is refused, never replaced.</span>
          </>
        )}
      </div>

      <fieldset data-testid="time-range">
        <legend>Time range</legend>
        <p className="hint" data-testid="range-span">
          {span
            ? `Data held: ${formatTs(span.first)} → ${formatTs(span.end)} (${((span.end - span.first) / 86400).toFixed(1)} days).`
            : "Data span unknown: test the connection to see it. Quick ranges still resolve from the end of the data when the run starts."}{" "}
          Times in {zoneLabel()}.
        </p>
        <input type="search" aria-label="Search ranges" placeholder="Search ranges…" value={search} onChange={(e) => setSearch(e.target.value)} />
        <div className="choices" role="group" aria-label="Quick ranges">
          {shownQuick.map((r) => (
            <button
              key={r.id}
              type="button"
              aria-pressed={choice.kind === "relative" && choice.seconds === r.seconds}
              disabled={r.seconds === null && !span}
              onClick={() => pickRelative(r.seconds)}
            >
              {r.label}
            </button>
          ))}
        </div>
        {shownRecent.length ? (
          <div className="choices" role="group" aria-label="Recently used">
            <span className="muted">Recently used:</span>
            {shownRecent.map((r) => (
              <button key={r.runId} type="button" onClick={() => pickAbsolute(r.from, r.to)}>
                {recentLabel(r)}
              </button>
            ))}
          </div>
        ) : null}
        <div className="row">
          <label>
            From{" "}
            <input
              type="datetime-local"
              aria-label="From"
              value={resolved ? toLocalInput(resolved.from) : ""}
              min={span ? toLocalInput(span.first) : undefined}
              max={span ? toLocalInput(span.end) : undefined}
              onChange={(e) => editBound("from", e.target.value)}
            />
          </label>
          <label>
            To{" "}
            <input
              type="datetime-local"
              aria-label="To"
              value={resolved ? toLocalInput(resolved.to) : ""}
              min={span ? toLocalInput(span.first) : undefined}
              max={span ? toLocalInput(span.end) : undefined}
              onChange={(e) => editBound("to", e.target.value)}
            />
          </label>
        </div>
        <p className="hint" data-testid="range-selected">{selectedLabel}</p>
        {clamped ? (
          <p className="notice" data-testid="range-clamped">Kept within the data the source holds.</p>
        ) : null}
        {gaps.length ? (
          <p className="notice" data-testid="range-gaps">
            The source reports {gaps.length} gap{gaps.length > 1 ? "s" : ""} in this range:{" "}
            {gaps.map(([a, b]) => `${formatTs(a)} → ${formatTs(b)}`).join("; ")}.
          </p>
        ) : null}
        {estimate ? (
          <p className="hint" data-testid="range-estimate">
            Estimate, scaled from the census assuming even spread: ≈{" "}
            {[
              estimate.chunks !== undefined ? `${formatCount(estimate.chunks)} chunks` : null,
              estimate.packets !== undefined ? `${formatBig(estimate.packets)} packets` : null,
              estimate.bytes !== undefined ? formatBig(estimate.bytes, "B") : null,
            ]
              .filter(Boolean)
              .join(" · ")}
          </p>
        ) : null}
      </fieldset>

      {otherOptions.length ? (
        <div className="fields-2">
          {otherOptions.map((opt) => (
            <div className="field" key={opt.key}>
              <label htmlFor={`run-${opt.key}`}>{opt.key}</label>
              <input
                id={`run-${opt.key}`}
                type={opt.type === "integer" || opt.type === "number" ? "number" : "text"}
                value={values[opt.key] ?? ""}
                onChange={(e) => setOption(opt.key, e.target.value)}
              />
              {opt.description ? <span className="hint">{opt.description}</span> : null}
            </div>
          ))}
        </div>
      ) : null}

      <details className="advanced">
        <summary>Advanced</summary>
        <div className="field">
          <label>
            <input type="checkbox" checked={adding} onChange={(e) => toggleAdding(e.target.checked)} /> Add to an existing imprint
          </label>
          {adding ? (
            <>
              <select aria-label="Imprint to add to" value={baseId} onChange={(e) => setBaseId(e.target.value)}>
                <option value="">choose an imprint…</option>
                {pits.map((p) => {
                  const ok = Boolean(p.lineage?.extendable);
                  return (
                    <option key={p.pit_id} value={p.pit_id} disabled={!ok}>
                      {p.name}
                      {p.lineage ? ` (v${p.lineage.version})` : ""}
                      {ok ? "" : ` — can't be extended: ${p.lineage?.reason ?? "no belief store"}`}
                    </option>
                  );
                })}
              </select>
              <ErrorLine error={imprints.error} prefix="could not list imprints" />
              <span className="hint">
                The run's evidence is added to a copy of that imprint's evidence and baked as its next version. Every
                run counts as its own observation: overlapping ranges count twice in totals.
              </span>
            </>
          ) : null}
        </div>
        {baseLineage && base ? (
          <div className="field">
            <label>Measurement interval · Burst resolution</label>
            <span data-testid="interval-locked">
              {formatSpan(baseLineage.window_seconds)} · {formatSpan(baseLineage.window_seconds / baseLineage.subbuckets)}, from {base.name} v
              {baseLineage.version}
            </span>
            <span className="hint">An imprint's runs share one interval and resolution, so these come from it.</span>
          </div>
        ) : (
          <div className="fields-2">
            {hasInterval ? (
              <div className="field">
                <label htmlFor="run-interval">Measurement interval</label>
                <select id="run-interval" value={interval} disabled={adding} onChange={(e) => changeInterval(Number(e.target.value))}>
                  {[...new Set([...INTERVAL_PRESETS, interval])]
                    .sort((a, b) => a - b)
                    .map((s) => (
                      <option key={s} value={s}>
                        {formatSpan(s)}
                      </option>
                    ))}
                </select>
                <span className="hint">{INTERVAL_HELP}</span>
              </div>
            ) : null}
            {hasBurst ? (
              <div className="field">
                <label htmlFor="run-burst">Burst resolution</label>
                <select
                  id="run-burst"
                  value={effSubbuckets}
                  disabled={adding}
                  onChange={(e) => {
                    touch("subbuckets");
                    setSubbuckets(Number(e.target.value));
                  }}
                >
                  {burstChoices(effInterval).map((n) => (
                    <option key={n} value={n}>
                      {formatSpan(effInterval / n)}
                    </option>
                  ))}
                </select>
                <span className="hint">{BURST_HELP}</span>
              </div>
            ) : null}
          </div>
        )}
      </details>
      {problem ? (
        <p className="error" data-testid="run-problem">
          {problem}
        </p>
      ) : null}
      <ErrorLine error={error} prefix="the run was refused" />
      <button className="primary" type="submit" disabled={busy || Boolean(problem)}>
        {busy ? "Starting…" : "Start discovery"}
      </button>
    </form>
  );
}
