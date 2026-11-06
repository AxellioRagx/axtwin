/**
 * The canvas's node and edge components over React Flow primitives (the
 * `RF+` rows of `SPEC.md`), on the BUS plane (track 4, 2026-11-06): a site
 * as a tint, a segment as a bus lane -- one line, name and CIDR at its left
 * end, a host-count badge at its right end, the routers' hue dots -- a host
 * as an icon sitting on the bus, a router as a box in its hue, a leg as a
 * drop in that hue, an observed arc with a `log(bytes)` width, and the fixed
 * chrome (Internet). Nothing here edits anything.
 */
import { BaseEdge, Handle, NodeResizer, Position, getSmoothStepPath, getStraightPath, useInternalNode, type EdgeProps, type InternalNode, type NodeProps } from "@xyflow/react";
import { createContext, useContext, useEffect, useRef, useState } from "react";

import { ARC_DEPTH_MAX, ARC_DEPTH_MIN, ARC_DEPTH_PER_DX } from "./layout/geometry";
import { evidenceClassName, saturationFor } from "./model/evidence";
import type { DetailLevel } from "./model/detail";
import { roleBadgeLabel, SWITCH_ROLES, switchBadge } from "./model/roles";
import { clipLine, encodePoints, legPath, polylineMid, polylinePath, type Point } from "./canvas/legs";
import { BUS_BADGE_W, BUS_LABEL_W } from "./canvas/bus";
import { HANDLE, type CanvasEdge, type CanvasNode, type Rect } from "./canvas/placement";
import { clipBox, ROUTER_ICON } from "./canvas/wiring";
import { useEdit } from "./edit/EditContext";
import { Icon, RoleIcon } from "./icons";

/** The level of detail (`model/detail.ts`), read by the nodes and edges
 *  through context so a zoom crossing never rebuilds the node array. At
 *  `compact` a bus carries a glyph row instead of its icons (the canvas
 *  hides them), routers and the chrome are icons, arc labels are off. */
export const DetailContext = createContext<DetailLevel>("full");

/** The labels toggle: a host icon's caption is drawn for every icon, the
 *  selected ones only, or none; name, address and role stay on the hover
 *  card and in the inspector. The rail's toggle, kept per user. */
export type LabelsMode = "all" | "selected" | "none";
export const LABELS_MODES: readonly LabelsMode[] = ["all", "selected", "none"];
export const LabelsContext = createContext<LabelsMode>("selected");

/** The legs' polylines, wired by the canvas over the live node boxes
 *  (`canvas/wiring.ts`) and read by edge id; a leg with no entry falls back
 *  to its straight line. */
export const RoutesContext = createContext<ReadonlyMap<string, Point[]>>(new Map());

/** How many role glyphs a compact bus carries before folding the rest. */
export const TILE_GLYPH_MAX = 6;

/** What the traffic overlay lit: host ids and arc keys, read by the nodes
 *  through context so a highlight never rebuilds the node array. */
export interface Glow {
  hosts: ReadonlySet<string>;
  arcs: ReadonlySet<string>;
}
export const NO_GLOW: Glow = { hosts: new Set(), arcs: new Set() };
export const GlowContext = createContext<Glow>(NO_GLOW);

/** Every node offers the three handles the edges attach to. In edit mode a
 *  host's or router's bottom handle is a connection SOURCE and a bus's top
 *  handle a TARGET -- dragging one to the other is the attach gesture;
 *  read-only, none accepts a connection. */
function Handles({ role = "none" }: { role?: "host" | "segment" | "none" }) {
  const edit = useEdit();
  const live = Boolean(edit && !edit.locked);
  return (
    <>
      <Handle type="target" position={Position.Top} id={HANDLE.top} isConnectable={live && role === "segment"} />
      <Handle type="source" position={Position.Bottom} id={HANDLE.bottom} isConnectable={live && role === "host"} />
      <Handle type="target" position={Position.Bottom} id={HANDLE.bottomIn} isConnectable={false} />
    </>
  );
}

/** The id of a leg edge is `leg:<router>><segment>`; of a stub `stub:<host>><segment>`. */
export function legParts(id: string): { node: string; segment: string; kind: "leg" | "stub" } | null {
  const kind = id.startsWith("leg:") ? "leg" : id.startsWith("stub:") ? "stub" : null;
  if (!kind) return null;
  const body = id.slice(kind.length + 1);
  const at = body.indexOf(">");
  if (at === -1) return null;
  return { node: body.slice(0, at), segment: body.slice(at + 1), kind };
}

type P = NodeProps<CanvasNode>;

function posAttrs(p: P, key: string) {
  return { "data-key": key, "data-x": String(p.positionAbsoluteX), "data-y": String(p.positionAbsoluteY) };
}

/** Rule 5: the site group is a faint band behind its buses with a small
 *  label and the collapse caret -- no border, no title bar; the caret folds
 *  it to its label (the canvas hides its lanes, `data-site-collapse` is the
 *  click the canvas reads). */
export function SiteNode(p: P) {
  const d = p.data;
  if (d.kind !== "site") return null;
  const count = d.shown < d.total ? `${d.shown} of ${d.total} segments` : `${d.total} segment${d.total === 1 ? "" : "s"}`;
  return (
    <div className={`cv-site${p.selected ? " selected" : ""}${d.collapsed ? " collapsed" : ""}`} data-testid="canvas-site" data-collapsed={d.collapsed || undefined} {...posAttrs(p, d.key)} style={{ width: p.width, height: p.height }}>
      <div className="cv-site-label" data-testid="site-label">
        <button type="button" className="cv-site-toggle nodrag" data-site-collapse={d.key} aria-expanded={!d.collapsed} aria-label={d.collapsed ? `Expand ${d.label}` : `Collapse ${d.label}`} title={d.collapsed ? "show this group's segments" : "fold this group to its label"}>
          {d.collapsed ? "▸" : "▾"}
        </button>
        <span className="mono">{d.label}</span>
        <span className="muted"> · {count}{d.collapsed ? " folded" : ""}</span>
      </div>
      <Handles />
    </div>
  );
}

/** Rule 1: a segment is a bus -- one line along the lane's bottom edge, the
 *  name and CIDR at its left end, the host-count badge at its right end,
 *  one dot per serving router in that router's hue (rule 2) at the left
 *  end of the line. Its hosts are the canvas's child nodes, sitting on the
 *  line. Compact (zoomed out), the icons are hidden and a glyph row sits in
 *  their place. The lane's box is the drop target of every attach gesture. */
export function SegmentNode(p: P) {
  const d = p.data;
  const edit = useEdit();
  const detail = useContext(DetailContext);
  if (d.kind !== "segment") return null;
  const dominant = d.evidenceMix[0]?.evidenceClass ?? null;
  const target = Boolean(edit && !edit.locked && (edit.attachFrom || edit.placing || edit.rehomeTarget === d.name));
  const glyphs = [...d.roleBadges].sort((a, b) => b.count - a.count);
  // Compact: as many glyphs as the host rows hold between the label and the port region (60 px a glyph), the ghost glyph and a `+N` fold each taking a slot.
  const perRow = Math.max(1, Math.floor(((p.width ?? 360) - BUS_LABEL_W - BUS_BADGE_W - 20 + 6) / 60));
  const capacity = Math.max(1, perRow * Math.max(1, d.rows ?? 1) - (d.ghosts ? 1 : 0));
  const limit = Math.min(TILE_GLYPH_MAX, glyphs.length <= capacity ? capacity : Math.max(1, capacity - 1));
  const shown = glyphs.slice(0, limit);
  const folded = glyphs.slice(limit).reduce((n, g) => n + g.count, 0);
  const legend = glyphs.map((g) => `${g.role} ${g.count}`).join(" · ");
  const served = d.served ?? [];
  const hostsWord = `${d.hosts} host${d.hosts === 1 ? "" : "s"}${d.ghosts ? ` · ${d.ghosts} ghost${d.ghosts === 1 ? "" : "s"}` : ""}`;
  return (
    <div
      className={`cv-segment cv-bus ${evidenceClassName(dominant === "(none)" ? null : dominant)}${p.selected ? " selected" : ""}${target ? " cv-target" : ""}${detail === "compact" ? " compact" : ""}`}
      data-testid="canvas-segment"
      data-cidr={d.cidr}
      data-name={d.name}
      data-detail={detail}
      data-rank={d.rank}
      {...posAttrs(p, `segment:${d.cidr}`)}
      style={{ width: p.width, height: p.height }}
      title={`${d.name} · ${d.cidr}${d.deployedCidr && d.deployedCidr !== d.cidr ? ` → ${d.deployedCidr} deployed` : ""}${d.zoneType ? ` · ${d.zoneType}` : ""}${served.length ? `\nrouters: ${served.map((s) => s.router).join(", ")}` : ""}${legend ? `\n${legend}` : ""}`}
      onContextMenu={edit ? (e) => edit.onContextMenu(e, { kind: "segment", name: d.name }) : undefined}
    >
      <div className="cv-bus-label">
        <span className="mono cv-segment-name">{d.name}</span>
        <span className="mono muted cv-segment-cidr">{d.cidr}</span>
      </div>
      {detail === "compact" ? (
        <div className="cv-glyphs" data-testid="segment-glyphs" aria-label={legend || "no roles"}>
          {shown.map((g) => (
            <span key={g.role} className="cv-glyph" title={`${g.role} · ${g.count}`} data-role={roleBadgeLabel(g.role)}>
              <RoleIcon label={roleBadgeLabel(g.role)} />
              <span className="cv-glyph-count">{g.count}</span>
            </span>
          ))}
          {folded ? <span className="cv-glyph cv-glyph-fold" data-testid="glyph-fold" title={glyphs.slice(limit).map((g) => `${g.role} ${g.count}`).join(" · ")}>+{folded}</span> : null}
          {d.ghosts ? (
            <span className="cv-glyph ghost" title={`${d.ghosts} ghost${d.ghosts === 1 ? "" : "s"}: under the occupancy gate`}>
              <RoleIcon label="GHOST" />
              <span className="cv-glyph-count">{d.ghosts}</span>
            </span>
          ) : null}
        </div>
      ) : null}
      <div className="cv-bus-line" aria-hidden="true" />
      <span className="cv-bus-dots" data-testid="bus-dots" aria-label={served.length ? `routers: ${served.map((s) => s.router).join(", ")}` : "no router"}>
        {served.map((s) => (
          <span key={s.router} className="cv-bus-dot" data-router={s.router} style={{ background: s.hue }} title={`${s.router} has a port here`} />
        ))}
      </span>
      <span className="badge cv-segment-count" title={hostsWord} data-testid="segment-count">
        {d.hosts}
      </span>
      <Handles role="segment" />
    </div>
  );
}

/** The hover card opens after this long over an icon, so a click or a drag never raises one. */
export const HOVER_CARD_MS = 350;

/** The hover card's own hooks: opens after `HOVER_CARD_MS` over the icon, closes on a press,
 *  a leave or a drag, and flips left / up when it would run off the canvas shell. */
function useHoverCard(dragging: boolean | undefined) {
  const [open, setOpen] = useState(false);
  const [flip, setFlip] = useState<{ left: boolean; up: boolean }>({ left: false, up: false });
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const el = useRef<HTMLDivElement | null>(null);
  const cancel = () => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
  };
  const close = () => {
    cancel();
    setOpen(false);
  };
  const enter = () => {
    cancel();
    timer.current = setTimeout(() => {
      const chip = el.current;
      const shell = chip?.closest<HTMLElement>(".canvas-shell");
      if (chip && shell) {
        const c = chip.getBoundingClientRect();
        const s = shell.getBoundingClientRect();
        setFlip({ left: c.left + 260 > s.right, up: c.bottom + 200 > s.bottom });
      }
      setOpen(true);
    }, HOVER_CARD_MS);
  };
  useEffect(() => cancel, []);
  useEffect(() => {
    if (dragging) close();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dragging]);
  return { open, flip, el, enter, close };
}

/** A host on its bus (rule 1): the role glyph in the evidence ring, the
 *  `×N` badge inside the icon's bounds, a switch-class host's role badge
 *  (rule 3), its caption under the line when labels are on; name, address
 *  and role on the hover card and in the inspector. */
export function HostNode(p: P) {
  const d = p.data;
  const glow = useContext(GlowContext);
  const edit = useEdit();
  const card = useHoverCard(p.dragging);
  if (d.kind !== "host") return null;
  const lit = glow.hosts.has(d.nodeId);
  // In edit mode the icon is a node GROUP of the working document: its count and pinned address ride along.
  const group = edit?.model.te.nodes.find((n) => n.name === d.nodeId) ?? null;
  const pinned = group?.nets[0]?.ip ?? null;
  const armed = edit?.attachFrom === d.nodeId;
  const labels = useContext(LabelsContext);
  const labelled = labels === "all" || (labels === "selected" && Boolean(p.selected));
  const sw = d.role !== null && SWITCH_ROLES.has(d.role);
  return (
    <div
      className={`cv-host bare ${evidenceClassName(d.evidence)}${p.selected ? " selected" : ""}${lit ? " glow" : ""}${d.isGhost ? " ghost" : ""}${armed ? " cv-armed" : ""}${sw ? " cv-switch" : ""}${d.multi ? " cv-multi" : ""}`}
      data-testid="canvas-host"
      data-node-id={d.nodeId}
      data-role={d.roleLabel}
      data-switch={sw || undefined}
      data-multi={d.multi || undefined}
      data-address={pinned ?? d.address ?? undefined}
      {...posAttrs(p, `host:${d.nodeId}`)}
      style={{ width: p.width, height: p.height }}
      ref={card.el}
      onMouseEnter={card.enter}
      onMouseLeave={card.close}
      onPointerDown={card.close}
      onContextMenu={edit ? (e) => edit.onContextMenu(e, { kind: "host", name: d.nodeId }) : undefined}
    >
      <span className="cv-ring" style={{ opacity: saturationFor(d.roleConfidence) }} title={d.role ?? "no role discovered"} data-role={d.roleLabel}>
        {sw ? <Icon.switch /> : <RoleIcon label={d.roleLabel} />}
      </span>
      {sw ? <span className="cv-role-badge" data-testid="role-badge" title={`role: ${d.role}`}>{switchBadge(d.role!)}</span> : null}
      {labelled ? <span className="cv-host-label mono" title={d.label}>{d.short}</span> : null}
      {group && group.countValue > 1 ? <span className="badge cv-count-badge" data-testid="count-badge" title={`${group.countValue} instances (count: ${String(group.count)})`}>×{group.countValue}</span> : null}
      {card.open ? (
        <div className={`cv-card${card.flip.left ? " flip-left" : ""}${card.flip.up ? " flip-up" : ""}`} role="tooltip" data-testid="canvas-hover-card">
          <div className="mono">{d.nodeId}</div>
          <dl className="facts">
            <dt>Address</dt>
            <dd className="mono">{pinned ?? d.address ?? "none"}</dd>
            <dt>Role</dt>
            <dd>
              {d.role ?? <span className="muted">{edit ? "none" : "not discovered"}</span>}
              {typeof d.roleConfidence === "number" ? ` (${d.roleConfidence.toFixed(2)})` : ""}
              {d.roleEvidence ? <span className="muted"> · {d.roleEvidence}</span> : null}
            </dd>
            {group && group.nets.length > 1 ? (
              <>
                <dt>Segments</dt>
                <dd className="mono">{group.nets.map((l) => l.segment).join(", ")}</dd>
              </>
            ) : null}
            {d.evidence ? (
              <>
                <dt>Evidence</dt>
                <dd>{d.evidence}</dd>
              </>
            ) : null}
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
      <Handles role="host" />
    </div>
  );
}

/** A collapsed role band: the band's ring with its count as the one `×N`
 *  badge style, the role's label only when labels are on. */
export function RungsNode(p: P) {
  const d = p.data;
  const labels = useContext(LabelsContext);
  if (d.kind !== "rungs") return null;
  const words = d.rungs.map((r) => `${r.count} ${r.rung}`).join(" · ");
  const labelled = labels === "all";
  return (
    <div className="cv-host bare cv-rungs" data-testid="canvas-rungs" style={{ width: p.width, height: p.height }} title={`${d.count} ${d.role} hosts, over the band budget: ${words}`}>
      <span className="cv-ring" data-role={d.roleLabel}><RoleIcon label={d.roleLabel} /></span>
      {labelled ? <span className="cv-host-label">{d.roleLabel}</span> : null}
      <span className="badge cv-count-badge" data-testid="count-badge">×{d.count}</span>
      <Handles />
    </div>
  );
}

/** The crossing figure a gateway router carries: the WAN badge's title, the hover card's row and the inspector's fact. */
export function crossingText(c: { edges: number; bytes: number; flows: number; inferred: boolean }): string {
  return `${c.edges} crossing edge${c.edges === 1 ? "" : "s"} · ${c.bytes.toLocaleString()} bytes · ${c.flows.toLocaleString()} flows${c.inferred ? " (inferred from observed traffic)" : ""}`;
}

/** A router (rule 2): its icon ring in its one hue, its name and leg count above; the WAN badge when it is the gateway. */
export function RouterNode(p: P) {
  const d = p.data;
  const edit = useEdit();
  const card = useHoverCard(p.dragging);
  if (d.kind !== "router") return null;
  const armed = edit?.attachFrom === d.name;
  const gateway = d.uplink || d.crossing !== null;
  const wan = d.uplink ? "the config's uplink" : d.crossing ? "the gateway by observed crossing traffic" : null;
  return (
    <div
      className={`cv-router${p.selected ? " selected" : ""}${armed ? " cv-armed" : ""}`}
      data-testid="canvas-router"
      data-node-id={d.name}
      data-gateway={gateway || undefined}
      data-hue={d.hue}
      {...posAttrs(p, `host:${d.name}`)}
      style={{ width: p.width, height: p.height, "--hue": d.hue } as React.CSSProperties}
      ref={card.el}
      onMouseEnter={card.enter}
      onMouseLeave={card.close}
      onPointerDown={card.close}
      onContextMenu={edit ? (e) => edit.onContextMenu(e, { kind: "router", name: d.name }) : undefined}
    >
      {/* Name and leg count above, the icon flush with the box's bottom edge: the legs leave from the bottom-centre handle, so they meet the icon. */}
      <span className="mono cv-icon-name">{d.name}</span>
      <span className="muted cv-icon-sub">{d.segments.length} leg{d.segments.length === 1 ? "" : "s"}</span>
      <span className="cv-icon-box" aria-hidden="true">
        <Icon.router />
        {gateway ? <span className="cv-wan-badge" data-testid="wan-badge" title={wan ?? undefined}><Icon.globe /></span> : null}
      </span>
      {card.open ? (
        <div className={`cv-card${card.flip.left ? " flip-left" : ""}${card.flip.up ? " flip-up" : ""}`} role="tooltip" data-testid="canvas-hover-card">
          <div className="mono">{d.name}</div>
          <dl className="facts">
            <dt>Ports</dt>
            <dd className="mono">{d.segments.join(", ") || "none"}</dd>
            {wan ? (
              <>
                <dt>WAN</dt>
                <dd>{wan}</dd>
              </>
            ) : null}
            {d.crossing ? (
              <>
                <dt>Crossing</dt>
                <dd>{crossingText(d.crossing)}</dd>
              </>
            ) : null}
          </dl>
        </div>
      ) : null}
      <Handles role="host" />
    </div>
  );
}

/** An annotation: text, a rectangle, an ellipse or a line, resizable while
 *  selected and unlocked. Presentation only; it never touches the twin. */
export function AnnotationNode(p: P) {
  const d = p.data;
  if (d.kind !== "annotation") return null;
  const a = d.ann;
  const w = p.width ?? a.w;
  const h = p.height ?? a.h;
  return (
    <div className={`cv-ann cv-ann-${a.kind}${a.locked ? " locked" : ""}${p.selected ? " selected" : ""}`} data-testid="canvas-annotation" data-ann-id={a.id} data-ann-kind={a.kind} style={{ width: w, height: Math.max(h, a.kind === "line" ? 2 : h) }}>
      {a.kind !== "text" && !a.locked ? <NodeResizer isVisible={p.selected} minWidth={20} minHeight={a.kind === "line" ? 0 : 20} /> : null}
      {a.kind === "text" ? <span className="cv-ann-text">{a.text}</span> : null}
      {a.kind === "rect" ? <svg width={w} height={h} aria-hidden="true"><rect x={1} y={1} width={Math.max(0, w - 2)} height={Math.max(0, h - 2)} rx={6} /></svg> : null}
      {a.kind === "ellipse" ? <svg width={w} height={h} aria-hidden="true"><ellipse cx={w / 2} cy={h / 2} rx={Math.max(0, w / 2 - 1)} ry={Math.max(0, h / 2 - 1)} /></svg> : null}
      {a.kind === "line" ? <svg width={w} height={Math.max(2, h)} aria-hidden="true"><line x1={0} y1={1} x2={w} y2={Math.max(1, h - 1)} /></svg> : null}
      {a.locked ? <span className="badge cv-ann-lock" title="locked">lock</span> : null}
    </div>
  );
}

/** The `+N more` lane of an over-budget site. */
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
  if (d.kind !== "internet") return null;
  return (
    <div className="cv-chrome cv-internet" data-testid="canvas-internet" style={{ width: p.width, height: p.height }} title={d.title}>
      <span className="cv-icon-box" aria-hidden="true"><Icon.globe /></span>
      <span className="cv-icon-text">
        <span className="cv-icon-name">{d.label}</span>
        <span className="muted cv-icon-sub">{d.sub}</span>
      </span>
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
  annotation: AnnotationNode,
};

// ---------------------------------------------------------------------------
// Edges
// ---------------------------------------------------------------------------
type E = EdgeProps<CanvasEdge>;

/** An observed segment pair: a cubic leaving both buses' bottom edges and
 *  dipping below them, deeper the further apart they are (the lifted arc
 *  depth rule), with the engine's log-scaled stroke. */
export function ArcEdge(e: E) {
  const glow = useContext(GlowContext);
  const detail = useContext(DetailContext);
  const d = e.data;
  if (!d || d.kind !== "arc") return null;
  const dx = Math.abs(e.targetX - e.sourceX);
  const depth = Math.min(ARC_DEPTH_MAX, ARC_DEPTH_MIN + dx * ARC_DEPTH_PER_DX);
  const base = Math.max(e.sourceY, e.targetY) + depth;
  const path = `M ${e.sourceX} ${e.sourceY} C ${e.sourceX} ${base}, ${e.targetX} ${base}, ${e.targetX} ${e.targetY}`;
  const lit = glow.arcs.has(d.key);
  return (
    <g data-testid="canvas-arc" data-key={d.key} className={lit ? "glow" : ""}>
      <BaseEdge id={e.id} path={path} style={e.style} className={`cv-arc cv-arc-${d.arcKind}${lit ? " glow" : ""}`} label={detail === "full" ? d.label : undefined} labelX={(e.sourceX + e.targetX) / 2} labelY={base - 6} labelShowBg />
    </g>
  );
}

export { ROUTER_ICON };

/** The box a leg is clipped against in the straight-line fallback: the
 *  node's box on the plane, from React Flow's internals (measured when the
 *  DOM has laid it out, else the size the placement gave it) -- for a
 *  router, its icon box. */
function boxOf(n: InternalNode): Rect {
  const w = n.measured.width ?? n.width ?? 0;
  const h = n.measured.height ?? n.height ?? 0;
  const { x, y } = n.internals.positionAbsolute;
  return clipBox(n, { x, y, w, h });
}

/** The radius of the port dot where a drop meets its bus. */
export const PORT_DOT = 3.5;

/** A router's leg onto a bus (rule 2, in the router's hue, a port dot where
 *  it meets the line), a multi-homed host's stub (rule 4), a fan's trunk
 *  onto a site, or the rail inside the site: the orthogonal polyline the
 *  canvas wired through the gutters (`RoutesContext`), with rounded
 *  corners; until it is wired, a straight line from the one box's border
 *  towards the other's centre (`legs.ts`). In edit mode a leg is an
 *  ATTACHMENT of the working document: labelled with the pinned address (or
 *  the segment). It takes no pointer events (`canvas.css`): the pane finds
 *  it under the pointer by its `data-pts` for the click, the hover label,
 *  the menu and the re-home drag. */
export function LegEdge(e: E) {
  const edit = useEdit();
  const routes = useContext(RoutesContext);
  const src = useInternalNode(e.source);
  const dst = useInternalNode(e.target);
  const kind = e.data?.kind;
  const routed = routes.get(e.id);
  const line = !routed && src && dst ? clipLine(boxOf(src), boxOf(dst)) : null;
  const pts: Point[] = routed ?? (line ? [line.from, line.to] : [{ x: e.sourceX, y: e.sourceY }, { x: e.targetX, y: e.targetY }]);
  const mid = polylineMid(pts);
  const [path, labelX, labelY] = routed ? [polylinePath(routed), mid.x, mid.y] : line ? [legPath(line), line.mid.x, line.mid.y] : getStraightPath({ sourceX: e.sourceX, sourceY: e.sourceY, targetX: e.targetX, targetY: e.targetY });
  const folded = kind === "leg" || kind === "stub" ? ((e.data as { folded?: number }).folded ?? 0) : 0;
  const parts = folded || (kind !== "leg" && kind !== "stub") ? null : legParts(e.id);
  const leg = edit && parts ? (edit.model.te.nodes.find((n) => n.name === parts.node)?.nets.find((l) => l.segment === parts.segment) ?? null) : null;
  // The label (the pinned address, else the segment) draws only while the
  // leg is selected or hovered (`canvas.css`): forty labelled legs from one
  // router were a wedge of text. A folded leg names its count instead; a
  // fan's badge (its count) is always on.
  const label = kind === "fan" ? (e.data as { label: string }).label : kind === "bus" ? (e.data as { segment: string }).segment : folded ? (e.data as { label?: string }).label : edit && parts ? (leg?.ip ?? parts.segment) : undefined;
  const hue = kind === "leg" || kind === "fan" || kind === "bus" ? (e.data as { hue?: string }).hue : undefined;
  const cls = kind === "fan" ? " cv-leg-fan" : kind === "bus" ? " cv-leg-bus" : kind === "stub" ? " cv-leg-stub" : folded ? " cv-leg-folded" : "";
  const end = pts[pts.length - 1];
  // A drop ends in a port dot on its bus (a stub in a hollow one); a fan's trunk ends at the site's edge, no dot.
  const dot = end && kind !== "fan" && !(kind === "leg" && folded) && dst?.type === "segment";
  return (
    <g data-testid="canvas-leg" data-leg={e.id} data-pts={encodePoints(pts)} data-kind={kind} data-folded={folded || undefined} data-fan={kind === "fan" ? (e.data as { count: number }).count : undefined} data-bus={kind === "bus" || undefined} data-routed={routed ? "true" : undefined} className={`${e.selected ? "selected" : ""}${folded ? " folded" : ""}`} style={hue ? ({ "--hue": hue } as React.CSSProperties) : undefined}>
      <BaseEdge id={e.id} path={path} className={`cv-leg${e.selected ? " selected" : ""}${cls}`} style={hue ? { ...e.style, stroke: hue } : e.style} label={label} labelX={labelX} labelY={labelY} labelShowBg interactionWidth={0} />
      {dot ? <circle className={`cv-port${kind === "stub" ? " cv-port-stub" : ""}`} cx={end.x} cy={end.y} r={PORT_DOT} style={hue ? { fill: hue, stroke: hue } : undefined} data-testid="port-dot" /> : null}
    </g>
  );
}

/** The trunk from the Internet down to each gateway router. */
export function TrunkEdge(e: E) {
  const [path] = getSmoothStepPath({ sourceX: e.sourceX, sourceY: e.sourceY, sourcePosition: Position.Bottom, targetX: e.targetX, targetY: e.targetY, targetPosition: Position.Top });
  return <BaseEdge id={e.id} path={path} className="cv-trunk" style={e.style} />;
}

export const edgeTypes = { arc: ArcEdge, leg: LegEdge, trunk: TrunkEdge };
