import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";

import { useApi } from "../../api/context";
import type { DiscoveryPlugin, DiscoveryRun, DiscoverySource, ProbeResult, SourceRunRequest } from "../../api/types";
import {
  defaultImprintId,
  describeAccess,
  formatTs,
  lastRunOf,
  prefillHint,
  runOptions,
  runPrefill,
  windowPreview,
} from "../../model/sources";
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

/** The run options a plugin's schema offers that most runs never touch. */
const ADVANCED_OPTIONS = ["vantage", "window", "subbuckets"];

/** What the last test reported that a run can use: the vantage, and window / subbuckets if
 *  its census carried them. The lowest pre-fill tier above the built-ins (`runPrefill`). */
function probeRunExtras(source: DiscoverySource): Record<string, string> {
  const out: Record<string, string> = {};
  const probe = source.last_probe;
  if (!probe || !probe.reachable) return out;
  if (probe.vantage) out.vantage = probe.vantage;
  for (const k of ["window", "subbuckets"] as const) {
    const v = probe.extra[k];
    if (typeof v === "number" && Number.isFinite(v)) out[k] = String(v);
  }
  return out;
}

/**
 * The run form. Its starting values come from `runPrefill`: the source's last run, then the
 * source's legacy saved defaults, then the built-ins; the hint says which. The runs list
 * loads after the page, so the pre-fill can change once -- it is re-applied to every field
 * the operator has NOT touched, never to one they typed in.
 */
function RunForm({ source, plugin, lastRun }: { source: DiscoverySource; plugin: DiscoveryPlugin | undefined; lastRun: DiscoveryRun | undefined }) {
  const api = useApi();
  const navigate = useNavigate();
  const options = useMemo(() => runOptions(plugin), [plugin]);
  const probeExtras = useMemo(() => probeRunExtras(source), [source]);
  const prefill = useMemo(() => runPrefill(source, lastRun, probeExtras), [source, lastRun, probeExtras]);
  const [touched, setTouched] = useState<ReadonlySet<string>>(() => new Set());
  const touch = (key: string) => setTouched((t) => (t.has(key) ? t : new Set(t).add(key)));
  const [imprintId, setImprintId] = useState(() => defaultImprintId(source.name, new Date()));
  const [mode, setMode] = useState<"days" | "bounds">("days");
  const [amount, setAmount] = useState(prefill.amount);
  const [unit, setUnit] = useState<"days" | "hours">(prefill.unit);
  // The API takes `days` (fractional allowed); hours are just a smaller unit of it.
  const days = unit === "hours" ? Number(amount) / 24 : Number(amount);
  const [tsFrom, setTsFrom] = useState("");
  const [tsTo, setTsTo] = useState("");
  const [values, setValues] = useState<Record<string, string>>(() => ({ ...prefill.values }));
  const [sensors, setSensors] = useState(prefill.sensors);
  useEffect(() => {
    if (!touched.has("length")) {
      setAmount(prefill.amount);
      setUnit(prefill.unit);
    }
    if (!touched.has("sensors")) setSensors(prefill.sensors);
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
  const preview = windowPreview(source.last_probe, days);
  const setOption = (key: string, value: string) => {
    touch(key);
    setValues((v) => ({ ...v, [key]: value }));
  };
  const mainOptions = options.filter((o) => !ADVANCED_OPTIONS.includes(o.key));
  const advancedOptions = options.filter((o) => ADVANCED_OPTIONS.includes(o.key));

  async function start(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    const body: SourceRunRequest = { name: imprintId.trim() };
    if (mode === "days") body.days = days;
    else {
      body.ts_from = Number(tsFrom);
      body.ts_to = Number(tsTo);
    }
    for (const opt of options) {
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
    body.sensors = sensors.split(",").map((s) => s.trim()).filter(Boolean);
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
      <div className="fields-2">
        <div className="field">
          <label htmlFor="run-imprint">Imprint name</label>
          <input id="run-imprint" type="text" value={imprintId} onChange={(e) => setImprintId(e.target.value)} />
          <span className="hint">What the baked imprint is called; rename it later if you like. An existing name is refused, never replaced.</span>
        </div>
        <div className="field">
          <label>Window</label>
          <div className="choices">
            <label>
              <input type="radio" name="window" checked={mode === "days"} onChange={() => setMode("days")} /> last
              <input
                type="number"
                aria-label="Amount"
                min="0"
                step="any"
                value={amount}
                onChange={(e) => {
                  touch("length");
                  setAmount(e.target.value);
                }}
                style={{ width: 80 }}
                disabled={mode !== "days"}
              />
              <select
                aria-label="Unit"
                value={unit}
                onChange={(e) => {
                  touch("length");
                  setUnit(e.target.value as "days" | "hours");
                }}
                disabled={mode !== "days"}
              >
                <option value="days">days</option>
                <option value="hours">hours</option>
              </select>
            </label>
            <label>
              <input type="radio" name="window" checked={mode === "bounds"} onChange={() => setMode("bounds")} /> epoch bounds
            </label>
          </div>
          {mode === "days" ? (
            <span className="hint">
              {preview
                ? `Resolves to ${formatTs(preview.from)} → ${formatTs(preview.to)} from the last test; re-resolved when the run starts.`
                : "Resolved from the source's own span when the run starts (test the connection to preview it)."}
            </span>
          ) : (
            <div className="row">
              <input type="number" aria-label="From (epoch seconds)" placeholder="from" value={tsFrom} onChange={(e) => setTsFrom(e.target.value)} />
              <input type="number" aria-label="To (epoch seconds)" placeholder="to (exclusive)" value={tsTo} onChange={(e) => setTsTo(e.target.value)} />
            </div>
          )}
        </div>
        {mainOptions.map((opt) => (
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
        <div className="field">
          <label htmlFor="run-sensors">Sensors</label>
          <input id="run-sensors" type="text" placeholder="comma-separated" value={sensors} onChange={(e) => { touch("sensors"); setSensors(e.target.value); }} />
          <span className="hint">Sensor names filled into every tap binding at install time; empty leaves them unset.</span>
        </div>
      </div>
      {advancedOptions.length ? (
        <details className="advanced">
          <summary>Advanced: {advancedOptions.map((o) => o.key).join(", ")}</summary>
          <div className="fields-2">
        {advancedOptions.map((opt) => (
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
        </details>
      ) : null}
      <ErrorLine error={error} prefix="the run was refused" />
      <button className="primary" type="submit" disabled={busy}>
        {busy ? "Starting…" : "Start discovery"}
      </button>
    </form>
  );
}
