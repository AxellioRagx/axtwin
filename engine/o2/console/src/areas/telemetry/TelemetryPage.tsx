/**
 * Telemetry: link-outs to the three live Grafana dashboards (ruling §4 --
 * Grafana stays for telemetry only). The Grafana base URL is configured at
 * RUN time, never compiled in: the server's resolved observability endpoints
 * (`GET /api/o2/info`, from adapter.conf) when it publishes one, else a URL
 * typed here and kept on this browser.
 */
import { useEffect, useRef, useState } from "react";

import { useResource } from "../../api/hooks";
import { useRuntimeApi } from "../../api/runtimeContext";

export const DASHBOARDS: ReadonlyArray<{ uid: string; title: string; shows: string }> = [
  {
    uid: "axr-capture-health",
    title: "Capture health",
    shows: "Whether each sensor is capturing right now: capture up/down, newest pcap age, disk use, drops.",
  },
  {
    uid: "axr-suricata-alerts",
    title: "Suricata alerts",
    shows: "Intrusion alerts raised on the mirrored traffic: over time, by severity, top signatures and host pairs.",
  },
  {
    uid: "axr-attack-coverage",
    title: "Attack coverage",
    shows: "Which attack techniques were attempted and which of them the sensors alerted on.",
  },
];

const STORAGE_KEY = "axrange.console.grafanaUrl";

/** A usable base URL, or `null`. The server publishes a PLACEHOLDER sentence
 *  when Grafana is not configured ("(not configured — …)"), and a placeholder
 *  must never become a link (LES-342). */
export function usableBaseUrl(value: string | null | undefined): string | null {
  if (!value) return null;
  const trimmed = value.trim().replace(/\/+$/, "");
  return /^https?:\/\/[^\s()]+$/i.test(trimmed) ? trimmed : null;
}

function readStored(): string {
  try {
    return window.localStorage.getItem(STORAGE_KEY) ?? "";
  } catch {
    return "";
  }
}

export function TelemetryPage(): JSX.Element {
  const api = useRuntimeApi();
  const info = useResource((signal) => api.getO2Info(signal), [api]);
  const [typed, setTyped] = useState(readStored);
  const served = usableBaseUrl(info.data?.endpoints?.["grafana"]);
  const local = usableBaseUrl(typed);
  const base = local ?? served;
  // Once the server has answered with no address, the field is where the
  // operator's next keystroke belongs: focus it (once).
  const fieldRef = useRef<HTMLInputElement | null>(null);
  const focused = useRef(false);
  const needsAddress = !base && !info.loading;
  useEffect(() => {
    if (needsAddress && !focused.current) {
      focused.current = true;
      fieldRef.current?.focus();
    }
  }, [needsAddress]);

  return (
    <section className="page telemetry-page" aria-labelledby="telemetry-title">
      <div className="page-head">
        <h1 id="telemetry-title">Telemetry</h1>
        <p className="page-lede">Live capture and detection dashboards, in Grafana.</p>
      </div>
      <div className={`settings${needsAddress ? " needs-address" : ""}`}>
        {needsAddress && (
          <p className="notice" role="status" data-testid="grafana-needed">
            To open the dashboards, type your Grafana address below (for example http://10.80.65.20:3000); this browser
            remembers it.
          </p>
        )}
        <label className="field">
          <span>Grafana address</span>
          <input
            type="url"
            placeholder={served ?? "http://grafana-host:3000"}
            ref={fieldRef}
            aria-describedby="grafana-source"
            value={typed}
            data-testid="grafana-url"
            onChange={(e) => {
              setTyped(e.target.value);
              try {
                window.localStorage.setItem(STORAGE_KEY, e.target.value);
              } catch {
                // storage unavailable: the field still works for this visit
              }
            }}
          />
        </label>
        <p className="meta" id="grafana-source" data-testid="grafana-source">
          {local
            ? "Using the address typed here (kept on this browser)."
            : served
              ? `Using the address this server publishes (${served}).`
              : info.loading
                ? "Asking the server for its Grafana address…"
                : `This server publishes no Grafana address${
                    info.data?.endpoints?.["grafana"] ? ` (it says: ${info.data.endpoints["grafana"]})` : ""
                  }.`}
        </p>
      </div>
      <ul className="dashboards">
        {DASHBOARDS.map((d) => (
          <li key={d.uid} className="dashboard-card">
            {base ? (
              <a href={`${base}/d/${d.uid}`} target="_blank" rel="noreferrer" data-testid={`dash-${d.uid}`}>
                {d.title} ↗
              </a>
            ) : (
              <span className="disabled-link" data-testid={`dash-${d.uid}`} title="Set the Grafana address first">
                {d.title}
              </span>
            )}
            <p>{d.shows}</p>
          </li>
        ))}
      </ul>
    </section>
  );
}
