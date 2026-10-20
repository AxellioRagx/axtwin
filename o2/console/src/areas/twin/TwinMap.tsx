/**
 * The deployed twin's map: `layout/deployed.ts` drawn as SVG.
 *
 * Level 0 aggregates each segment into a tile (hosts, personas, status) so a
 * 106-host twin stays readable; a tile click drills into that segment
 * (`?segment=`), where every host is a chip. The host table beneath narrows
 * to the same segment, and a chip click opens the same host panel a table row
 * does -- one selection, one URL.
 *
 * Status colour is the RECORDED status (the state store). When a live check
 * has been run, a chip also carries the VM's live power state as a ring.
 */
import { useMemo } from "react";

import { layoutDeployed0, layoutDeployed1, LINKS_GROUP } from "../../layout/deployed";
import type { DeployedSegment, DeployedTwin } from "../../model/deployed";
import { plural, powerTone, statusTone, statusWord } from "../../model/format";
import { Nav } from "../../views/mapParts";

export function TwinMap(props: {
  model: DeployedTwin;
  segment: string | null;
  selectedHost: string | null;
  expanded: ReadonlySet<string>;
  onToggleGroup: (key: string) => void;
  onSegment: (segment: string | null) => void;
  onHost: (host: string) => void;
  livePower: ReadonlyMap<string, string> | null;
}): JSX.Element {
  const { model, segment } = props;
  const drilled = segment ? model.segmentById.get(segment) ?? null : null;

  return (
    <section className="twin-map" aria-labelledby="map-title">
      <div className="section-head">
        <h2 id="map-title">
          {drilled ? (
            <>
              Segment <span className="mono">{drilled.id}</span>
            </>
          ) : (
            "Map"
          )}
        </h2>
        {drilled ? (
          <button type="button" className="btn small" onClick={() => props.onSegment(null)} data-testid="map-up">
            ← All segments
          </button>
        ) : (
          <span className="meta">
            {plural(model.segments.length, "segment")} · click a segment to see its hosts
          </span>
        )}
        <MapLegend />
      </div>
      {segment && !drilled && (
        <p className="notice">
          This twin has no segment named <span className="mono">{segment}</span>.{" "}
          <button type="button" className="link" onClick={() => props.onSegment(null)}>
            Show all segments
          </button>
        </p>
      )}
      <div className="map-frame">
        {drilled ? (
          <SegmentDrill {...props} segmentId={drilled.id} />
        ) : (
          <Overview {...props} />
        )}
      </div>
    </section>
  );
}

function MapLegend(): JSX.Element {
  return (
    <span className="map-legend" aria-label="Status colours (recorded)">
      {(["completed", "running", "failed", "halted-due-to-upstream"] as const).map((s) => (
        <span key={s} className="legend-item">
          <span className={`dot tone-${statusTone(s)}`} aria-hidden="true" /> {statusWord(s)}
        </span>
      ))}
      <span className="meta">(recorded)</span>
    </span>
  );
}

function Overview(props: Parameters<typeof TwinMap>[0]): JSX.Element {
  const { model } = props;
  const l0 = useMemo(() => layoutDeployed0(model, props.expanded), [model, props.expanded]);
  if (model.hosts.length === 0) {
    return <p className="empty">The recorded inventory for this twin lists no hosts.</p>;
  }
  return (
    <svg
      className="deployed-map"
      width={l0.width}
      height={l0.height}
      viewBox={`0 0 ${l0.width} ${l0.height}`}
      role="group"
      aria-label={`Map of ${model.twinId ?? model.deployId}: ${plural(model.segments.length, "segment")}, ${plural(model.hosts.length, "host")}`}
    >
      {l0.rails.map((r) => (
        <text key={r.label} className="rail-label" x={24} y={r.y + 16}>
          {r.label}
        </text>
      ))}
      {l0.uplink && <polyline className="trunk" points={l0.uplink.map((p) => `${p.x},${p.y}`).join(" ")} />}
      {l0.bus && <polyline className="trunk" points={l0.bus.map((p) => `${p.x},${p.y}`).join(" ")} />}
      {l0.groups.map((g) =>
        g.leg ? <polyline key={`leg-${g.key}`} className="trunk" points={g.leg.map((p) => `${p.x},${p.y}`).join(" ")} /> : null,
      )}
      {l0.internet && (
        <g>
          <rect className="internet" x={l0.internet.x} y={l0.internet.y} width={l0.internet.w} height={l0.internet.h} rx={17} />
          <text className="box-label" x={l0.internet.x + l0.internet.w / 2} y={l0.internet.y + 22} textAnchor="middle">
            Internet (WAN uplink)
          </text>
        </g>
      )}
      {l0.routers.map((r) => {
        const host = model.hostById.get(r.hostId);
        const legs = host?.segments.length ?? 0;
        return (
          <Nav key={r.hostId} label={`Router ${r.hostId}, ${plural(legs, "leg")}`} onActivate={() => props.onHost(r.hostId)} current={props.selectedHost === r.hostId}>
            <rect className={`device tone-${statusTone(host?.recorded)}`} x={r.box.x} y={r.box.y} width={r.box.w} height={r.box.h} rx={6} />
            <text className="box-label" x={r.box.x + 10} y={r.box.y + 22}>
              {truncate(r.hostId, 18)}
            </text>
            <text className="box-meta" x={r.box.x + 10} y={r.box.y + 40}>
              router · {plural(legs, "leg")}
            </text>
            <text className="box-meta" x={r.box.x + 10} y={r.box.y + 56}>
              {host?.raw.role ?? "no role"}
            </text>
          </Nav>
        );
      })}
      {l0.groups.map((g) => (
        <g key={g.key}>
          <rect className="group-box" x={g.box.x} y={g.box.y} width={g.box.w} height={g.box.h} rx={8} />
          <text className="group-label" x={g.header.x + 12} y={g.header.y + 18}>
            <title>{g.note}</title>
            {g.label} · {plural(g.total, g.key === LINKS_GROUP ? "link" : "segment")}
            {g.leg ? "" : " · reached through other devices"}
          </text>
          {g.tiles.map((t) => {
            const seg = model.segmentById.get(t.segmentId)!;
            return (
              <SegmentTile
                key={t.segmentId}
                seg={seg}
                box={t.box}
                holdsSelected={props.selectedHost !== null && seg.hosts.includes(props.selectedHost)}
                onOpen={() => props.onSegment(seg.id)}
              />
            );
          })}
          {g.overflow && (
            <Nav
              label={
                g.overflow.expanded
                  ? `Collapse ${g.label}`
                  : `Show ${plural(g.overflow.hidden.length, "more segment")} in ${g.label} (${plural(g.overflow.hiddenHosts, "host")})`
              }
              onActivate={() => props.onToggleGroup(g.key)}
            >
              <rect className="tile overflow" x={g.overflow.box.x} y={g.overflow.box.y} width={g.overflow.box.w} height={g.overflow.box.h} rx={6} />
              <text className="box-label" x={g.overflow.box.x + 12} y={g.overflow.box.y + 34}>
                {g.overflow.expanded ? "show fewer" : `+${g.overflow.hidden.length} more`}
              </text>
              {!g.overflow.expanded && (
                <text className="box-meta" x={g.overflow.box.x + 12} y={g.overflow.box.y + 54}>
                  {plural(g.overflow.hiddenHosts, "host")} in them
                </text>
              )}
            </Nav>
          )}
        </g>
      ))}
    </svg>
  );
}

function SegmentTile({
  seg,
  box,
  holdsSelected,
  onOpen,
}: {
  seg: DeployedSegment;
  box: { x: number; y: number; w: number; h: number };
  holdsSelected: boolean;
  onOpen: () => void;
}): JSX.Element {
  const top = seg.personaMix.slice(0, 2);
  const more = seg.personaMix.length - top.length;
  const label =
    `Segment ${seg.id}: ${plural(seg.hosts.length, "host")}, ` +
    `${plural(seg.personaInstances, "persona instance")} on ${plural(seg.hostsWithPersonas, "host")}, ` +
    `recorded status ${statusWord(seg.recorded)}` +
    (seg.personaMix.length > 0 ? `. Personas: ${seg.personaMix.map((p) => `${p.persona} ×${p.count}`).join(", ")}` : "");
  return (
    <Nav label={holdsSelected ? `${label}. Holds the selected host.` : label} onActivate={onOpen} current={holdsSelected}>
      <rect className={`tile tone-${statusTone(seg.recorded)}`} x={box.x} y={box.y} width={box.w} height={box.h} rx={6} />
      <circle className={`dot-svg tone-${statusTone(seg.recorded)}`} cx={box.x + box.w - 12} cy={box.y + 14} r={5} />
      <text className="box-label mono" x={box.x + 10} y={box.y + 20}>
        {truncate(seg.id, 17)}
      </text>
      <text className="box-meta" x={box.x + 10} y={box.y + 38}>
        {plural(seg.hosts.length, "host")}
        {seg.hostsWithPersonas > 0 ? ` · ${seg.hostsWithPersonas} with personas` : ""}
      </text>
      {top.map((p, i) => (
        <g key={p.persona}>
          <rect className="persona-chip" x={box.x + 10} y={box.y + 46 + i * 18} width={box.w - 20} height={15} rx={7} />
          <text className="persona-chip-text" x={box.x + 16} y={box.y + 57 + i * 18}>
            {truncate(shortPersona(p.persona), 21)} ×{p.count}
          </text>
        </g>
      ))}
      {top.length === 0 && (
        <text className="box-meta muted" x={box.x + 10} y={box.y + 58}>
          no personas
        </text>
      )}
      {more > 0 && (
        <text className="box-meta" x={box.x + 10} y={box.y + box.h - 6}>
          +{more} more persona{more === 1 ? "" : "s"}
        </text>
      )}
    </Nav>
  );
}

function SegmentDrill(props: Parameters<typeof TwinMap>[0] & { segmentId: string }): JSX.Element {
  const { model } = props;
  const l1 = useMemo(() => layoutDeployed1(model, props.segmentId), [model, props.segmentId]);
  if (!l1) return <p className="empty">No such segment.</p>;
  return (
    <svg className="deployed-map" width={l1.width} height={l1.height} viewBox={`0 0 ${l1.width} ${l1.height}`} role="group" aria-label={`Hosts on segment ${props.segmentId}`}>
      {l1.bands.map((b) => (
        <g key={b.label}>
          <text className="group-label" x={b.labelBox.x} y={b.labelBox.y + 14}>
            {b.label} · {b.count}
          </text>
          {b.chips.map((c) => {
            const host = model.hostById.get(c.hostId)!;
            const power = props.livePower?.get(c.hostId) ?? null;
            const label =
              `Host ${host.id}${host.ips[0] ? `, ${host.ips[0]}` : ""}, ${host.raw.role ?? "no role"}, ` +
              `${plural(host.personas.length, "persona")}${host.personaIds.length ? ` (${host.personaIds.join(", ")})` : ""}, ` +
              `recorded ${statusWord(host.recorded)}${power ? `, live power ${power}` : ""}`;
            return (
              <Nav key={c.hostId} label={label} onActivate={() => props.onHost(c.hostId)} current={props.selectedHost === c.hostId}>
                <rect className={`chip tone-${statusTone(host.recorded)}${power ? ` live-${powerTone(power)}` : ""}`} x={c.box.x} y={c.box.y} width={c.box.w} height={c.box.h} rx={5} />
                <circle className={`dot-svg tone-${statusTone(host.recorded)}`} cx={c.box.x + 10} cy={c.box.y + c.box.h / 2} r={4} />
                <text className="chip-text mono" x={c.box.x + 20} y={c.box.y + 14}>
                  {truncate(chipName(host.id, b.label), 22)}
                </text>
                <text className="chip-meta" x={c.box.x + 20} y={c.box.y + 28}>
                  {host.ips[0] ?? "no address"} · {host.personas.length > 0 ? plural(host.personas.length, "persona") : "no personas"}
                </text>
              </Nav>
            );
          })}
        </g>
      ))}
    </svg>
  );
}

/** A chip sits under its role's band label, so a host id that starts with
 *  that role (`generic-endpoint-10-80-64-core03`) is shown by the part that
 *  tells it apart (`…10-80-64-core03`); the full id is in the accessible name
 *  and the table. */
export function chipName(hostId: string, bandLabel: string): string {
  return hostId.startsWith(`${bandLabel}-`) && hostId.length > bandLabel.length + 1
    ? `…${hostId.slice(bandLabel.length + 1)}`
    : hostId;
}

/** `cast-corp-workstation-local` -> `corp-workstation`: the twin-local
 *  `cast-` prefix and `-local` suffix say where a persona came from, not what
 *  it does, and a tile has 140 px. The full id is in the accessible name. */
export function shortPersona(id: string): string {
  return id.replace(/^cast-/, "").replace(/-local$/, "");
}

function truncate(text: string, max: number): string {
  return text.length <= max ? text : `${text.slice(0, max - 1)}…`;
}
