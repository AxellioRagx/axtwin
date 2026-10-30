/**
 * The canvas's node and edge components over React Flow primitives (the
 * `RF+` rows of `SPEC.md`): a site and a segment as compound nodes, a host as
 * a chip with its role badge and evidence ring, a router as a node spanning
 * the segments it joins, an observed arc with a `log(bytes)` width, and the
 * fixed chrome (Internet, the WAN edge). Nothing here edits anything.
 */
import { BaseEdge, Handle, Position, getSmoothStepPath, getStraightPath, type EdgeProps, type NodeProps } from "@xyflow/react";
import { createContext, useContext, useState } from "react";

import { ARC_DEPTH_MAX, ARC_DEPTH_MIN, ARC_DEPTH_PER_DX } from "./layout/geometry";
import { evidenceClassName, saturationFor } from "./model/evidence";
import { HANDLE, type CanvasEdge, type CanvasNode } from "./canvas/placement";

/** What the traffic overlay lit: host chip ids and arc keys, read by the
 *  nodes through context so a highlight never rebuilds the node array. */
export interface Glow {
  hosts: ReadonlySet<string>;
  arcs: ReadonlySet<string>;
}
export const NO_GLOW: Glow = { hosts: new Set(), arcs: new Set() };
export const GlowContext = createContext<Glow>(NO_GLOW);

/** Every node offers the three handles the edges attach to; none accepts a connection. */
function Handles() {
  return (
    <>
      <Handle type="target" position={Position.Top} id={HANDLE.top} isConnectable={false} />
      <Handle type="source" position={Position.Bottom} id={HANDLE.bottom} isConnectable={false} />
      <Handle type="target" position={Position.Bottom} id={HANDLE.bottomIn} isConnectable={false} />
    </>
  );
}

type P = NodeProps<CanvasNode>;

function posAttrs(p: P, key: string) {
  return { "data-key": key, "data-x": String(p.positionAbsoluteX), "data-y": String(p.positionAbsoluteY) };
}

export function SiteNode(p: P) {
  const d = p.data;
  if (d.kind !== "site") return null;
  return (
    <div className={`cv-site${p.selected ? " selected" : ""}`} data-testid="canvas-site" {...posAttrs(p, d.key)} style={{ width: p.width, height: p.height }}>
      <div className="cv-site-header">
        <span className="mono">{d.label}</span>
        <span className="muted"> · {d.shown < d.total ? `${d.shown} of ${d.total} segments` : `${d.total} segment${d.total === 1 ? "" : "s"}`}</span>
      </div>
      <Handles />
    </div>
  );
}

export function SegmentNode(p: P) {
  const d = p.data;
  if (d.kind !== "segment") return null;
  const dominant = d.evidenceMix[0]?.evidenceClass ?? null;
  return (
    <div
      className={`cv-segment ${evidenceClassName(dominant === "(none)" ? null : dominant)}${p.selected ? " selected" : ""}`}
      data-testid="canvas-segment"
      data-cidr={d.cidr}
      {...posAttrs(p, `segment:${d.cidr}`)}
      style={{ width: p.width, height: p.height }}
      title={`${d.name} · ${d.cidr}${d.deployedCidr && d.deployedCidr !== d.cidr ? ` → ${d.deployedCidr} deployed` : ""}${d.zoneType ? ` · ${d.zoneType}` : ""}`}
    >
      <div className="cv-segment-header">
        <span className="mono">{d.cidr}</span>
        <span className="muted"> {d.hosts} host{d.hosts === 1 ? "" : "s"}{d.ghosts ? ` · ${d.ghosts} ghost${d.ghosts === 1 ? "" : "s"}` : ""}</span>
        {d.hasRouterLeg ? <span className="badge" title="a router has a leg here">gw</span> : null}
      </div>
      {d.hosts === 0 ? <p className="muted cv-empty">No hosts on this segment.</p> : null}
      <Handles />
    </div>
  );
}

export function HostNode(p: P) {
  const d = p.data;
  const glow = useContext(GlowContext);
  const [hover, setHover] = useState(false);
  if (d.kind !== "host") return null;
  const lit = glow.hosts.has(d.nodeId);
  const evidence = d.evidence ?? "no evidence";
  return (
    <div
      className={`cv-host ${evidenceClassName(d.evidence)}${p.selected ? " selected" : ""}${lit ? " glow" : ""}${d.isGhost ? " ghost" : ""}`}
      data-testid="canvas-host"
      data-node-id={d.nodeId}
      {...posAttrs(p, `host:${d.nodeId}`)}
      style={{ width: p.width, height: p.height }}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
    >
      <span className="cv-ring" style={{ opacity: saturationFor(d.roleConfidence) }} aria-hidden="true" />
      <span className="cv-host-label mono">{d.label}</span>
      <span className="badge cv-role" title={d.role ?? "no role discovered"}>{d.roleLabel}</span>
      {hover ? (
        <div className="cv-card" role="tooltip" data-testid="canvas-hover-card">
          <div className="mono">{d.nodeId}</div>
          <dl className="facts">
            <dt>Address</dt>
            <dd className="mono">{d.address ?? "none"}</dd>
            <dt>Role</dt>
            <dd>
              {d.role ?? <span className="muted">not discovered</span>}
              {typeof d.roleConfidence === "number" ? ` (${d.roleConfidence.toFixed(2)})` : ""}
              {d.roleEvidence ? <span className="muted"> · {d.roleEvidence}</span> : null}
            </dd>
            <dt>Evidence</dt>
            <dd>{evidence}</dd>
            {typeof d.occupancy === "number" ? (
              <>
                <dt>Occupancy</dt>
                <dd>{d.occupancy.toFixed(2)}{d.isGhost ? " · ghost" : ""}</dd>
              </>
            ) : null}
            {d.listen.length ? (
              <>
                <dt>Listens</dt>
                <dd className="mono">{d.listen.join(", ")}</dd>
              </>
            ) : null}
          </dl>
        </div>
      ) : null}
      <Handles />
    </div>
  );
}

export function RungsNode(p: P) {
  const d = p.data;
  if (d.kind !== "rungs") return null;
  const words = d.rungs.map((r) => `${r.count} ${r.rung}`).join(" · ");
  return (
    <div className="cv-host cv-rungs" data-testid="canvas-rungs" style={{ width: p.width, height: p.height }} title={`${d.count} ${d.role} hosts, over the band budget: ${words}`}>
      <span className="cv-host-label">{d.count} × {d.role}</span>
      <span className="badge cv-role">{d.roleLabel}</span>
      <Handles />
    </div>
  );
}

export function RouterNode(p: P) {
  const d = p.data;
  if (d.kind !== "router") return null;
  return (
    <div className={`cv-router${p.selected ? " selected" : ""}`} data-testid="canvas-router" {...posAttrs(p, `host:${d.name}`)} style={{ width: p.width, height: p.height }} title={`${d.name} joins ${d.segments.join(", ") || "no segment"}`}>
      <span className="mono">{d.name}</span>
      <span className="badge">router · {d.segments.length} leg{d.segments.length === 1 ? "" : "s"}</span>
      <Handles />
    </div>
  );
}

export function OverflowNode(p: P) {
  const d = p.data;
  if (d.kind !== "overflow") return null;
  return (
    <div className="cv-overflow" data-testid="canvas-overflow" style={{ width: p.width, height: p.height }} title={d.expanded ? "collapse this group to its busiest segments" : "show every segment of this group"}>
      <span>{d.expanded ? `showing all ${d.total}` : `+${d.hidden} more`}</span>
      <span className="muted">{d.expanded ? "click to collapse" : `of ${d.total} segments`}</span>
      <Handles />
    </div>
  );
}

export function ChromeNode(p: P) {
  const d = p.data;
  if (d.kind !== "internet" && d.kind !== "wan") return null;
  return (
    <div className={`cv-chrome cv-${d.kind}`} data-testid={`canvas-${d.kind}`} style={{ width: p.width, height: p.height }}>
      <span>{d.label}</span>
      <span className="muted">{d.sub}</span>
      <Handles />
    </div>
  );
}

export function UnplacedNode(p: P) {
  const d = p.data;
  if (d.kind !== "unplaced") return null;
  return (
    <div className="cv-segment cv-unplaced" data-testid="canvas-unplaced" style={{ width: p.width, height: p.height }}>
      <div className="cv-segment-header">
        <span>unplaced</span>
        <span className="muted"> {d.count} local entit{d.count === 1 ? "y" : "ies"} in no segment</span>
      </div>
      <Handles />
    </div>
  );
}

export const nodeTypes = {
  site: SiteNode,
  segment: SegmentNode,
  host: HostNode,
  rungs: RungsNode,
  router: RouterNode,
  overflow: OverflowNode,
  chrome: ChromeNode,
  unplaced: UnplacedNode,
};

// ---------------------------------------------------------------------------
// Edges
// ---------------------------------------------------------------------------
type E = EdgeProps<CanvasEdge>;

/** An observed segment pair: a cubic leaving both tiles' bottom edges and
 *  dipping below them, deeper the further apart they are (the lifted arc
 *  depth rule), with the engine's log-scaled stroke. */
export function ArcEdge(e: E) {
  const glow = useContext(GlowContext);
  const d = e.data;
  if (!d || d.kind !== "arc") return null;
  const dx = Math.abs(e.targetX - e.sourceX);
  const depth = Math.min(ARC_DEPTH_MAX, ARC_DEPTH_MIN + dx * ARC_DEPTH_PER_DX);
  const base = Math.max(e.sourceY, e.targetY) + depth;
  const path = `M ${e.sourceX} ${e.sourceY} C ${e.sourceX} ${base}, ${e.targetX} ${base}, ${e.targetX} ${e.targetY}`;
  const lit = glow.arcs.has(d.key);
  return (
    <g data-testid="canvas-arc" data-key={d.key} className={lit ? "glow" : ""}>
      <BaseEdge id={e.id} path={path} style={e.style} className={`cv-arc cv-arc-${d.arcKind}${lit ? " glow" : ""}`} label={d.label} labelX={(e.sourceX + e.targetX) / 2} labelY={base - 6} labelShowBg />
    </g>
  );
}

/** A router's or a multi-homed host's leg onto a segment: a straight line. */
export function LegEdge(e: E) {
  const [path] = getStraightPath({ sourceX: e.sourceX, sourceY: e.sourceY, targetX: e.targetX, targetY: e.targetY });
  return <BaseEdge id={e.id} path={path} className="cv-leg" style={e.style} />;
}

/** The trunk from the Internet through the WAN edge down to each site. */
export function TrunkEdge(e: E) {
  const [path] = getSmoothStepPath({ sourceX: e.sourceX, sourceY: e.sourceY, sourcePosition: Position.Bottom, targetX: e.targetX, targetY: e.targetY, targetPosition: Position.Top });
  return <BaseEdge id={e.id} path={path} className="cv-trunk" style={e.style} />;
}

export const edgeTypes = { arc: ArcEdge, leg: LegEdge, trunk: TrunkEdge };
