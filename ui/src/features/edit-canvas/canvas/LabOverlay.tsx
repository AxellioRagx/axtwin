import { useEffect, useLayoutEffect, useRef, useState } from "react";

import { useViewport } from "@xyflow/react";

import type { ComponentHealth, LabComponent, TargetHealth } from "../../../api/types";
import { useLoad } from "../../../areas/shell/useLoad";
import { componentUrl, labGroups } from "../../../model/targets";

/** How often the overlay re-reads the lab's health while it is shown (the server caches 15 s too). */
export const LAB_HEALTH_POLL_MS = 15_000;

/** What the canvas needs to draw a target's lab (docs/design/targets-and-lab.md §2.3). */
export interface LabSource {
  /** The target the lab belongs to: the deployed twin's, else the default one. */
  target: string;
  platform?: string | null;
  components: LabComponent[];
  /** Sensor ids the target offers that its lab block does not declare (adapter.conf's EXTERNAL_SENSORS, read for
   *  one release): drawn among the sensors, without a health dot. */
  extraSensors: string[];
  loadHealth: (signal: AbortSignal) => Promise<TargetHealth>;
}

/** One tap chip on the canvas (a React Flow node id) and the sensors its binding delivers to. */
export interface TapChipRef {
  nodeId: string;
  sensors: string[];
}

/** The Lab overlay: the target's lab docked at the canvas's edge -- Sensors on top, then Telemetry, Attack, Defence,
 *  each chip with a health dot -- and a dotted line from every tap chip to each sensor it delivers to. It is chrome,
 *  not drawing: no lab component is a node of the topology graph, and nothing here edits the document (the twin's
 *  te.yaml, layout and links are untouched while it is on). Lines are measured from the DOM in screen space, so
 *  `version` (anything that changes when the drawing moves: the viewport, the nodes) re-measures them. */
export function LabOverlay({ lab, taps, version }: { lab: LabSource; taps: readonly TapChipRef[]; version: unknown }) {
  const health = useLoad((signal) => lab.loadHealth(signal), [lab.target]);
  // Re-measured on every pan / zoom (subscribed here, so only the overlay re-renders, and only while it is shown).
  const vp = useViewport();
  const reload = health.reload;
  useEffect(() => {
    const h = setInterval(reload, LAB_HEALTH_POLL_MS);
    return () => clearInterval(h);
  }, [reload]);
  const byId = new Map((health.data?.components ?? []).map((h) => [h.id, h]));
  const declared = new Set(lab.components.map((c) => c.id));
  const extras: LabComponent[] = lab.extraSensors.filter((s) => !declared.has(s)).map((id) => ({ id, role: "sensor", type: "adapter.conf", reach: {} }));
  const groups = labGroups([...lab.components, ...extras]);

  const colRef = useRef<HTMLDivElement>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const [lines, setLines] = useState<Array<{ key: string; tap: string; sensor: string; x1: number; y1: number; x2: number; y2: number }>>([]);
  const tapKey = taps.map((t) => `${t.nodeId}:${t.sensors.join(",")}`).join("|");
  useLayoutEffect(() => {
    const svg = svgRef.current;
    const col = colRef.current;
    if (!svg || !col) return;
    const origin = svg.getBoundingClientRect();
    const out: typeof lines = [];
    for (const t of taps) {
      const chip = document.querySelector(`.react-flow__node[data-id=${JSON.stringify(t.nodeId)}]`);
      if (!chip) continue;
      const a = chip.getBoundingClientRect();
      for (const s of t.sensors) {
        const target = col.querySelector(`[data-lab-sensor=${JSON.stringify(s)}]`);
        if (!target) continue;
        const b = target.getBoundingClientRect();
        out.push({
          key: `${t.nodeId}->${s}`, tap: t.nodeId, sensor: s,
          x1: a.left + a.width / 2 - origin.left, y1: a.top + a.height / 2 - origin.top,
          x2: b.right - origin.left, y2: b.top + b.height / 2 - origin.top,
        });
      }
    }
    setLines(out);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tapKey, version, vp.x, vp.y, vp.zoom, health.data, lab.components.length, lab.extraSensors.length]);

  return (
    <>
      <svg ref={svgRef} className="cv-lab-lines" aria-hidden="true" data-testid="lab-lines">
        {lines.map((l) => (
          <line key={l.key} x1={l.x1} y1={l.y1} x2={l.x2} y2={l.y2} data-tap={l.tap} data-sensor={l.sensor} data-testid="lab-line" />
        ))}
      </svg>
      <div ref={colRef} className="cv-lab" data-testid="lab-overlay" aria-label={`Lab of target ${lab.target}`} role="region">
        <div className="cv-lab-head">
          Lab <span className="mono">{lab.target}</span>
          {lab.platform ? <span className="muted"> · {lab.platform}</span> : null}
        </div>
        {groups.length === 0 ? <p className="muted cv-lab-empty">No lab declared for this target.</p> : null}
        {groups.map((g) => (
          <div key={g.role} className="cv-lab-group" data-testid={`lab-overlay-${g.role}`}>
            <div className="cv-lab-role">{g.label}</div>
            {g.components.map((c) => (
              <LabChip key={c.id} c={c} health={byId.get(c.id) ?? null} extra={c.type === "adapter.conf"} />
            ))}
          </div>
        ))}
      </div>
    </>
  );
}

function LabChip({ c, health, extra }: { c: LabComponent; health: ComponentHealth | null; extra: boolean }) {
  const status = extra ? "unknown" : (health?.status ?? "unknown");
  const title = extra ? `${c.id}: declared in adapter.conf EXTERNAL_SENSORS (not in the lab block; no health probe)` : `${c.type} ${c.id}: ${status}${health?.detail ? ` -- ${health.detail}` : ""}`;
  const url = componentUrl(c);
  return (
    <div className="cv-lab-chip" data-testid="lab-overlay-chip" data-component={c.id} {...(c.role === "sensor" ? { "data-lab-sensor": c.id } : {})} title={title}>
      <span className={`dot ${status}`} data-status={status} />
      <span className="mono">{c.id}</span>
      {extra ? <span className="muted cv-lab-type">adapter.conf</span> : c.type !== c.id ? <span className="muted cv-lab-type">{c.type}</span> : null}
      {url ? (
        <a href={url} target="_blank" rel="noopener noreferrer" aria-label={`Open ${c.id}`} onClick={(e) => e.stopPropagation()}>
          ↗
        </a>
      ) : null}
    </div>
  );
}
