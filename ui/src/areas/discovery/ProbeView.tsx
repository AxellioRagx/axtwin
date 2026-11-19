import type { Probe } from "../../api/types";
import { formatBig, formatCount, formatSteps, formatTs, probeFailure, siteRows } from "../../model/sources";
import { When } from "../shell/parts";

/**
 * What one connection test found: the failed step and its reason, or the source's span,
 * vantage and census. Shared by the source page's "Reachability and data held" panel and
 * the editor's "Test connection", so a saved and an unsaved test read the same.
 */
export function ProbeView({ probe }: { probe: Probe & { probed_at: string } }) {
  if (!probe.reachable) {
    const trail = formatSteps(probe.steps);
    const failure = probeFailure(probe);
    return (
      <div className="error" role="alert" data-testid="probe-failure">
        <div>
          Not reachable (<When value={probe.probed_at} />)
          {trail ? (
            <>
              : <span data-testid="probe-steps">{trail}</span>
            </>
          ) : null}
        </div>
        <div data-testid="probe-failed-step">
          {failure?.step ? `${failure.step} ✗ — ` : ""}
          {failure?.reason ?? "no reason given"}
        </div>
        {probe.error ? (
          <details data-testid="probe-raw-error">
            <summary>Details</summary>
            <pre className="mono" style={{ whiteSpace: "pre-wrap", margin: 0 }}>{probe.error}</pre>
          </details>
        ) : null}
      </div>
    );
  }
  const sites = siteRows(probe.extra.sites);
  return (
    <>
      <div className="notice">Reachable (<When value={probe.probed_at} />){probe.via ? `, ${probe.via}` : ""}</div>
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
