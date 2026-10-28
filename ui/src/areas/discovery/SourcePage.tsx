import { useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";

import { useApi } from "../../api/context";
import type { DiscoveryPlugin, DiscoveryRun, DiscoverySource, ProbeResult, SourceRunRequest } from "../../api/types";
import {
  defaultImprintId,
  describeAccess,
  formatBig,
  formatCount,
  formatIso,
  formatTs,
  runOptions,
  siteRows,
  windowPreview,
} from "../../model/sources";
import { ErrorLine, StatusBadge } from "../shell/parts";
import { useLoad } from "../shell/useLoad";

/** One source: how it is reached, what the last test found, and the run form. */
export function SourcePage() {
  const api = useApi();
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
            <DeleteButton source={source.data} />
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
          <RunForm source={source.data} plugin={plugin} />
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
          <tr key={r.id}>
            <td>
              <Link to={`/discovery/runs/${encodeURIComponent(r.id)}`}>{r.id}</Link>
            </td>
            <td>
              <StatusBadge status={r.status} />
            </td>
            <td>{r.imprint_id}</td>
            <td className="muted">
              {r.ts_from !== null && r.ts_to !== null ? `${formatTs(r.ts_from)} → ${formatTs(r.ts_to)}` : "resolving"}
            </td>
            <td className="muted">{formatIso(r.started_at ?? r.created_at)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function DeleteButton({ source }: { source: DiscoverySource }) {
  const api = useApi();
  const navigate = useNavigate();
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<unknown>(null);
  async function remove() {
    setError(null);
    try {
      await api.deleteSource(source.id);
      navigate("/discovery");
    } catch (e) {
      setError(e);
    }
  }
  return (
    <>
      {confirming ? (
        <>
          <button className="danger" type="button" onClick={remove}>
            Confirm delete
          </button>
          <button type="button" onClick={() => setConfirming(false)}>
            Keep
          </button>
        </>
      ) : (
        <button className="danger" type="button" onClick={() => setConfirming(true)}>
          Delete
        </button>
      )}
      <ErrorLine error={error} prefix="could not delete" />
    </>
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
  const sites = siteRows(probe?.extra.sites);
  return (
    <div className="panel" data-testid="probe-panel">
      <div className="row">
        <h2 style={{ flex: 1, margin: 0 }}>Reachability and data held</h2>
        <button type="button" onClick={test} disabled={busy}>
          {busy ? "Testing…" : "Test connection"}
        </button>
      </div>
      <ErrorLine error={error} prefix="the test could not run" />
      {!probe ? <p className="muted">Not tested yet. Testing opens the access path and reads the source's own census.</p> : null}
      {probe && !probe.reachable ? (
        <div className="error" role="alert">
          Not reachable ({formatIso(probe.probed_at)}): {probe.error ?? "no reason given"}
        </div>
      ) : null}
      {probe && probe.reachable ? (
        <>
          <div className="notice">Reachable ({formatIso(probe.probed_at)}){probe.via ? `, ${probe.via}` : ""}</div>
          <dl className="facts">
            <dt>Data span</dt>
            <dd>
              {`${formatTs(probe.first_ts)} → ${formatTs(probe.last_ts)}${
                probe.first_ts !== null && probe.last_ts !== null
                  ? ` (${((probe.last_ts - probe.first_ts) / 86400).toFixed(1)} days)`
                  : ""
              }`}
            </dd>
            <dt>Vantage</dt>
            <dd>
              {probe.vantage ? (
                <>
                  <code>{probe.vantage}</code>{" "}
                  <span className="muted">site/system:streams, the sensor's own name for where it sat</span>
                </>
              ) : (
                "not reported"
              )}
            </dd>
            {CENSUS.map(({ key, label, format }) =>
              key in probe.extra ? <Fact key={key} label={label} value={format(probe.extra[key])} /> : null,
            )}
          </dl>
          {sites.length ? (
            <table className="list" aria-label="Sites">
              <thead>
                <tr>
                  <th>Location</th>
                  <th>System</th>
                  <th>Streams</th>
                  <th>Chunks</th>
                  <th>First (epoch s)</th>
                  <th>Last (epoch s)</th>
                </tr>
              </thead>
              <tbody>
                {sites.map((row) => (
                  <tr key={`${row.location}/${row.system}`}>
                    <td>{row.location}</td>
                    <td>{row.system}</td>
                    <td>{formatCount(row.streams)}</td>
                    <td>{formatCount(row.chunks)}</td>
                    <td className="muted mono">{row.first_ts ?? "—"}</td>
                    <td className="muted mono">{row.last_ts ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : null}
        </>
      ) : null}
    </div>
  );
}

/** The census figures a probe may carry, in display order, each with its own reading. */
const CENSUS: ReadonlyArray<{ key: string; label: string; format: (v: unknown) => string }> = [
  { key: "chunks", label: "Chunks", format: formatCount },
  { key: "packets", label: "Packets", format: (v) => `${formatBig(v)} (${formatCount(v)})` },
  { key: "bytes", label: "Bytes", format: (v) => `${formatBig(v, "B")} (${formatCount(v)})` },
  { key: "entities", label: "Entities", format: formatCount },
  { key: "events", label: "Events", format: formatCount },
  { key: "endpoints", label: "Endpoints", format: formatCount },
  { key: "applications", label: "Applications", format: formatCount },
];

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </>
  );
}

function RunForm({ source, plugin }: { source: DiscoverySource; plugin: DiscoveryPlugin | undefined }) {
  const api = useApi();
  const navigate = useNavigate();
  const options = useMemo(() => runOptions(plugin), [plugin]);
  const [imprintId, setImprintId] = useState(() => defaultImprintId(source.name, new Date()));
  const [mode, setMode] = useState<"days" | "bounds">("days");
  const [amount, setAmount] = useState(String(source.defaults.days ?? 10));
  const [unit, setUnit] = useState<"days" | "hours">("days");
  // The API takes `days` (fractional allowed); hours are just a smaller unit of it.
  const days = unit === "hours" ? Number(amount) / 24 : Number(amount);
  const [tsFrom, setTsFrom] = useState("");
  const [tsTo, setTsTo] = useState("");
  const [values, setValues] = useState<Record<string, string>>(() => {
    const v: Record<string, string> = {};
    for (const [k, val] of Object.entries(source.defaults)) {
      if (k !== "days" && k !== "sensors" && val !== undefined) v[k] = String(val);
    }
    return v;
  });
  const [sensors, setSensors] = useState((source.defaults.sensors ?? []).join(", "));
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const preview = windowPreview(source.last_probe, days);

  async function start(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    const body: SourceRunRequest = { imprint_id: imprintId.trim() };
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
      <div className="fields-2">
        <div className="field">
          <label htmlFor="run-imprint">Imprint id</label>
          <input id="run-imprint" type="text" value={imprintId} onChange={(e) => setImprintId(e.target.value)} />
          <span className="hint">The catalog id the result is installed under. An existing id is refused, never overwritten.</span>
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
                onChange={(e) => setAmount(e.target.value)}
                style={{ width: 80 }}
                disabled={mode !== "days"}
              />
              <select aria-label="Unit" value={unit} onChange={(e) => setUnit(e.target.value as "days" | "hours")} disabled={mode !== "days"}>
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
        {options.map((opt) => (
          <div className="field" key={opt.key}>
            <label htmlFor={`run-${opt.key}`}>{opt.key}</label>
            <input
              id={`run-${opt.key}`}
              type={opt.type === "integer" || opt.type === "number" ? "number" : "text"}
              value={values[opt.key] ?? ""}
              onChange={(e) => setValues((v) => ({ ...v, [opt.key]: e.target.value }))}
            />
            {opt.description ? <span className="hint">{opt.description}</span> : null}
          </div>
        ))}
        <div className="field">
          <label htmlFor="run-sensors">Sensors</label>
          <input id="run-sensors" type="text" placeholder="comma-separated" value={sensors} onChange={(e) => setSensors(e.target.value)} />
          <span className="hint">Sensor names filled into every tap binding at install time; empty leaves them unset.</span>
        </div>
      </div>
      <ErrorLine error={error} prefix="the run was refused" />
      <button className="primary" type="submit" disabled={busy}>
        {busy ? "Starting…" : "Start discovery"}
      </button>
    </form>
  );
}
