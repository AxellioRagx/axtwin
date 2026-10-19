/**
 * The twin's taps: which host NIC / net is mirrored to which sensor, and the
 * tap deploy's recorded status. Read from the SAME target-aware `/hosts`
 * model as the rest of the page (`hosts[].taps[]`, S9) -- never from
 * `/api/o2/taps`, which reads only the default cluster.
 *
 * Labelled as RECORDED, NOT capture-verified (LES-089 / LES-247: a green tap
 * deploy is not proof a sensor is capturing -- the Telemetry area's
 * capture-health dashboard is where that is measured).
 */
import { Link } from "react-router";

import type { SiblingRef, TwinHost } from "../../api/runtimeTypes";
import { plural } from "../../model/format";
import { RecordedTag, StatusBadge } from "../shell/parts";

export function TapsSection({ tap, hosts }: { tap: SiblingRef | null; hosts: readonly TwinHost[] }): JSX.Element {
  const rows = hosts.flatMap((h) => (h.taps ?? []).map((t) => ({ host: h.host_id, ...t })));
  const sensors = [...new Set(rows.flatMap((r) => r.sensors))];
  return (
    <section className="taps" aria-labelledby="taps-title" data-testid="taps-section">
      <div className="section-head">
        <h2 id="taps-title">Taps</h2>
        {tap && <StatusBadge status={tap.status} title={tap.deploy_id} />}
        <RecordedTag title="What the tap deploy recorded. It does not prove a sensor is receiving the mirror." />
        <span className="meta">
          Recorded, not capture-verified — see <Link to="/telemetry">Telemetry</Link> for capture health.
        </span>
      </div>
      {!tap && <p className="empty">No tap deploy is recorded for this twin, so nothing is mirrored to a sensor.</p>}
      {tap && rows.length === 0 && (
        <p className="empty">The tap deploy is recorded, but no completed mirror binding is recorded on any host.</p>
      )}
      {rows.length > 0 && (
        <details>
          <summary>
            {plural(rows.length, "mirrored NIC")} to {sensors.join(", ") || "no sensor"}
          </summary>
          <div className="table-scroll">
            <table className="mini" data-testid="taps-table">
              <thead>
                <tr>
                  <th scope="col">host</th>
                  <th scope="col">net</th>
                  <th scope="col">sensor</th>
                  <th scope="col">direction</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((b) => (
                  <tr key={`${b.host}:${b.net_id}:${b.nic_index ?? "?"}`}>
                    <td className="mono">{b.host}</td>
                    <td className="mono">{b.net_id}</td>
                    <td>{b.sensors.join(", ")}</td>
                    <td>{b.sensors.map((s) => b.directions?.[s] ?? "not recorded").join(", ")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      )}
    </section>
  );
}
