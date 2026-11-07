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
import { PILL_LABEL_W, PILL_PAD_RIGHT } from "./canvas/tree";
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

/** The provenance overlay (`docs/design/topology-layers.md` §3.3): off, a
 *  small dot marks an asserted or authored item; on, the four states render
 *  (solid / accent border + badge / dashed) and the taps light up. */
export const OverlayContext = createContext<boolean>(false);
/** The Checks tab's provenance filter (§3.3): the one source left bright, or `null`. */
export const ProvFilterContext = createContext<string | null>(null);

/** `cv-dim` when a provenance filter is on and this item's source is not the one. */
function useDim(source: string | null | undefined): string {
  const filter = useContext(ProvFilterContext);
  return filter && (source ?? "authored") !== filter ? " cv-dim" : "";
}

/** The provenance mark an icon wears: nothing for a discovered item with the
 *  overlay off; a dot for an asserted or authored one; with the overlay on,
 *  the state's own badge. */
export function ProvenanceMark({ source, confidence }: { source?: string | null; confidence?: number | null }) {
  const overlay = useContext(OverlayContext);
  const s = source ?? "authored";
  if (!overlay) {
    if (s === "asserted" || s === "authored") return <span className={`cv-prov-dot cv-prov-${s}`} data-testid="prov-dot" data-source={s} title={`${s}: survives a re-cast`} />;
    return null;
  }
  const word = s === "discovered" && typeof confidence === "number" ? `${s} ${confidence.toFixed(2)}` : s;
  return <span className={`cv-prov-badge cv-prov-${s}`} data-testid="prov-badge" data-source={s} title={word}>{s === "discovered" ? "D" : s === "asserted" ? "A" : s === "authored" ? "+" : "?"}</span>;
}

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
  const overlay = useContext(OverlayContext);
  const dim = useDim(d.kind === "segment" ? d.source : null);
  if (d.kind !== "segment") return null;
  const dominant = d.evidenceMix[0]?.evidenceClass ?? null;
  const target = Boolean(edit && !edit.locked && (edit.attachFrom || edit.placing || edit.rehomeTarget === d.name));
  const glyphs = [...d.roleBadges].sort((a, b) => b.count - a.count);
  // Compact: as many glyphs as the host rows hold between the label and the badge (60 px a glyph), the ghost glyph and a `+N` fold each taking a slot. A pill's label column is the tree's (`PILL_LABEL_W`).
  const pill = d.carrier !== undefined;
  const perRow = Math.max(1, Math.floor(((p.width ?? 360) - (pill ? PILL_LABEL_W : BUS_LABEL_W) - (pill ? PILL_PAD_RIGHT : BUS_BADGE_W + 20) + 6) / 60));
  const capacity = Math.max(1, perRow * Math.max(1, d.rows ?? 1) - (d.ghosts ? 1 : 0));
  const limit = Math.min(TILE_GLYPH_MAX, glyphs.length <= capacity ? capacity : Math.max(1, capacity - 1));
  const shown = glyphs.slice(0, limit);
  const folded = glyphs.slice(limit).reduce((n, g) => n + g.count, 0);
  const legend = glyphs.map((g) => `${g.role} ${g.count}`).join(" · ");
  const served = d.served ?? [];
  const hostsWord = `${d.hosts} host${d.hosts === 1 ? "" : "s"}${d.ghosts ? ` · ${d.ghosts} ghost${d.ghosts === 1 ? "" : "s"}` : ""}`;
  return (
    <div
      className={`cv-segment cv-bus cv-pill ${evidenceClassName(dominant === "(none)" ? null : dominant)}${p.selected ? " selected" : ""}${target ? " cv-target" : ""}${detail === "compact" ? " compact" : ""}${overlay ? ` cv-ov cv-ov-${d.source ?? "authored"}` : ""}${dim}`}
      data-testid="canvas-segment"
      data-cidr={d.cidr}
      data-name={d.name}
      data-carrier={d.carrier ?? undefined}
      data-source={d.source ?? undefined}
      data-detail={detail}
      data-rank={d.rank}
      {...posAttrs(p, `segment:${d.cidr}`)}
      style={{ width: p.width, height: p.height, ...(d.hue ? ({ "--hue": d.hue } as React.CSSProperties) : {}) }}
      title={`${d.name} · ${d.cidr}${d.deployedCidr && d.deployedCidr !== d.cidr ? ` → ${d.deployedCidr} deployed` : ""}${d.zoneType ? ` · ${d.zoneType}` : ""}${d.carrier ? `\ncarried by ${d.carrier}` : ""}${served.length ? `\nrouters: ${served.map((s) => s.router).join(", ")}` : ""}${legend ? `\n${legend}` : ""}`}
      onContextMenu={edit ? (e) => edit.onContextMenu(e, { kind: "segment", name: d.name }) : undefined}
    >
      <div className="cv-bus-label">
        <span className="mono cv-segment-name">{d.name}</span>
        <span className="mono muted cv-segment-cidr">{d.cidr}</span>
        <ProvenanceMark source={d.source} confidence={d.confidence} />
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
/** The member chip being dragged (HTML5 drag; the data rides here, not in dataTransfer, so jsdom and the browser agree). */
let draggingMember: { from: string; index: number } | null = null;

/** The `×N` group's member chips (§5): one per instance under the icon, each
 *  a click to the member panel and a drag onto another group (a move of one). */
function MemberChips({ name, count, members }: { name: string; count: number; members: ReadonlyArray<{ index: number; role: string | null }> }) {
  const edit = useEdit();
  return (
    <div className="cv-members-strip nodrag" data-testid="member-chips" onPointerDown={(e) => e.stopPropagation()}>
      {Array.from({ length: count }, (_, i) => i + 1).map((i) => {
        const m = members.find((x) => x.index === i) ?? null;
        return (
          <button
            key={i}
            type="button"
            className={`cv-member-chip${m ? " overridden" : ""}`}
            data-testid="member-chip"
            data-index={i}
            draggable={!edit?.locked}
            title={`${name}${String(i).padStart(2, "0")}${m?.role ? ` · ${m.role}` : ""}`}
            onClick={(e) => {
              e.stopPropagation();
              edit?.onSelectMember(name, i);
            }}
            onDragStart={(e) => {
              draggingMember = { from: name, index: i };
              try {
                e.dataTransfer.effectAllowed = "move";
                e.dataTransfer.setData("text/plain", `${name}:${i}`);
              } catch {
                /* jsdom */
              }
            }}
            onDragEnd={() => {
              draggingMember = null;
            }}
          >
            {String(i).padStart(2, "0")}
          </button>
        );
      })}
    </div>
  );
}

export function HostNode(p: P) {
  const d = p.data;
  const glow = useContext(GlowContext);
  const edit = useEdit();
  const card = useHoverCard(p.dragging);
  const overlay = useContext(OverlayContext);
  const dim = useDim(d.kind === "host" ? d.source : null);
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
      className={`cv-host bare ${evidenceClassName(d.evidence)}${p.selected ? " selected" : ""}${lit ? " glow" : ""}${d.isGhost ? " ghost" : ""}${armed ? " cv-armed" : ""}${sw ? " cv-switch" : ""}${d.multi ? " cv-multi" : ""}${overlay ? ` cv-ov cv-ov-${d.source ?? "authored"}` : ""}${dim}`}
      data-testid="canvas-host"
      data-node-id={d.nodeId}
      data-source={d.source ?? undefined}
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
      onDragOver={edit && !edit.locked && group ? (e) => { if (draggingMember && draggingMember.from !== d.nodeId) { e.preventDefault(); e.stopPropagation(); } } : undefined}
      onDrop={edit && !edit.locked && group ? (e) => { const m = draggingMember; if (!m || m.from === d.nodeId) return; draggingMember = null; e.preventDefault(); e.stopPropagation(); edit.onMoveMember(m.from, m.index, d.nodeId); } : undefined}
    >
      <span className="cv-ring" style={{ opacity: saturationFor(d.roleConfidence) }} title={d.role ?? "no role discovered"} data-role={d.roleLabel}>
        {sw ? <Icon.switch /> : <RoleIcon label={d.roleLabel} />}
      </span>
      {sw ? <span className="cv-role-badge" data-testid="role-badge" title={`role: ${d.role}`}>{switchBadge(d.role!)}</span> : null}
      <ProvenanceMark source={d.source} confidence={d.confidence} />
      {labelled ? <span className="cv-host-label mono" title={d.label}>{d.short}</span> : null}
      {group && group.countValue > 1 ? (
        <span className={`badge cv-count-badge${group.members.length ? " has-overrides" : ""}${edit?.membersOpen.has(d.nodeId) ? " open" : ""}`} data-testid="count-badge" data-overrides={group.members.length || undefined} title={`${group.countValue} instances (count: ${String(group.count)})${group.members.length ? `; ${group.members.length} with a role override` : ""}; double-click for the members`} onDoubleClick={(e) => { e.stopPropagation(); edit?.onToggleMembers(d.nodeId); }}>
          ×{group.countValue}
          {group.members.length ? <span className="cv-count-dot" aria-hidden="true">·</span> : null}
        </span>
      ) : null}
      {group && group.countValue > 1 && edit?.membersOpen.has(d.nodeId) ? <MemberChips name={d.nodeId} count={group.countValue} members={group.members} /> : null}
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
            <dt>Provenance</dt>
            <dd>{d.source ?? "authored"}{typeof d.confidence === "number" ? ` (${d.confidence.toFixed(2)})` : ""}</dd>
          </dl>
        </div>
      ) : null}
      <Handles role="host" />
    </div>
  );
}

/** A switch of the device tree (§2.2, §7.2): the switch glyph in its branch's
 *  hue, the `L2` / `L3` / `FW` badge, its name above (a landmark, like a
 *  router's); ports, carried segments and provenance on the hover card. */
export function DeviceNode(p: P) {
  const d = p.data;
  const edit = useEdit();
  const card = useHoverCard(p.dragging);
  const overlay = useContext(OverlayContext);
  const dim = useDim(d.kind === "device" ? d.source : null);
  if (d.kind !== "device") return null;
  const armed = edit?.attachFrom === d.name;
  return (
    <div
      className={`cv-router cv-device${p.selected ? " selected" : ""}${armed ? " cv-armed" : ""}${overlay ? ` cv-ov cv-ov-${d.source ?? "authored"}` : ""}${dim}`}
      data-testid="canvas-device"
      data-node-id={d.name}
      data-device-kind={d.deviceKind}
      data-hue={d.hue}
      data-source={d.source ?? undefined}
      {...posAttrs(p, `host:${d.name}`)}
      style={{ width: p.width, height: p.height, "--hue": d.hue } as React.CSSProperties}
      ref={card.el}
      onMouseEnter={card.enter}
      onMouseLeave={card.close}
      onPointerDown={card.close}
      onContextMenu={edit ? (e) => edit.onContextMenu(e, { kind: "host", name: d.name }) : undefined}
    >
      <span className="mono cv-icon-name">{d.name}</span>
      <span className="muted cv-icon-sub">{d.ports.length ? `${d.ports.length} port${d.ports.length === 1 ? "" : "s"}` : `${d.segments.length} segment${d.segments.length === 1 ? "" : "s"}`}</span>
      <span className="cv-icon-box" aria-hidden="true">
        {d.deviceKind === "switch" ? <Icon.switch /> : <Icon.router />}
        <span className="cv-role-badge cv-device-badge" data-testid="device-badge">{d.badge}</span>
        <ProvenanceMark source={d.source} confidence={d.confidence} />
      </span>
      {card.open ? (
        <div className={`cv-card${card.flip.left ? " flip-left" : ""}${card.flip.up ? " flip-up" : ""}`} role="tooltip" data-testid="canvas-hover-card">
          <div className="mono">{d.name}</div>
          <dl className="facts">
            <dt>Kind</dt>
            <dd>{d.deviceKind}{d.role ? ` · ${d.role}` : ""}</dd>
            <dt>Image</dt>
            <dd className="mono">{d.image ?? <span className="muted">none: deploys as its segments' vnets</span>}</dd>
            <dt>Ports</dt>
            <dd className="mono">{d.ports.join(", ") || "none"}</dd>
            <dt>Segments</dt>
            <dd className="mono">{d.segments.join(", ") || "none"}</dd>
            <dt>Provenance</dt>
            <dd>{d.source ?? "authored"}{typeof d.confidence === "number" ? ` (${d.confidence.toFixed(2)})` : ""}</dd>
          </dl>
        </div>
      ) : null}
      <Handles role="host" />
    </div>
  );
}

/** A tap chip on a segment or a device (§8): amber, the sensor names on
 *  hover; a click opens its row, a segment tap drags to another pill. */
export function TapNode(p: P) {
  const d = p.data;
  const overlay = useContext(OverlayContext);
  if (d.kind !== "tap") return null;
  return (
    <div className={`cv-tap${p.selected ? " selected" : ""}${overlay ? " cv-ov-tap" : ""}`} data-testid="canvas-tap" data-tap={d.id} data-target={d.target} data-scope={d.scope} style={{ width: p.width, height: p.height }} title={`tap on ${d.scope} ${d.target}${d.sensors.length ? ` → ${d.sensors.join(", ")}` : " (no sensor named)"}`}>
      <span className="cv-tap-chip">tap</span>
      <ProvenanceMark source={d.source} />
      <Handles />
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
  const overlay = useContext(OverlayContext);
  const dim = useDim(d.kind === "router" ? d.source : null);
  if (d.kind !== "router") return null;
  const armed = edit?.attachFrom === d.name;
  const gateway = d.uplink || d.crossing !== null;
  const wan = d.uplink ? "the config's uplink" : d.crossing ? "the gateway by observed crossing traffic" : null;
  return (
    <div
      className={`cv-router${p.selected ? " selected" : ""}${armed ? " cv-armed" : ""}${overlay ? ` cv-ov cv-ov-${d.source ?? "authored"}` : ""}${dim}`}
      data-testid="canvas-router"
      data-node-id={d.name}
      data-gateway={gateway || undefined}
      data-hue={d.hue}
      data-source={d.source ?? undefined}
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
        <ProvenanceMark source={d.source} confidence={d.confidence} />
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
  device: DeviceNode,
  tap: TapNode,
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
  const label = kind === "fan" ? (e.data as { label: string }).label : kind === "bus" ? (e.data as { segment: string }).segment : kind === "carry" ? (e.data as { label: string }).label : folded ? (e.data as { label?: string }).label : edit && parts ? (leg?.ip ?? parts.segment) : undefined;
  const hue = kind === "leg" || kind === "fan" || kind === "bus" || kind === "carry" ? (e.data as { hue?: string }).hue : undefined;
  const cls = kind === "fan" ? " cv-leg-fan" : kind === "bus" ? " cv-leg-bus" : kind === "carry" ? " cv-leg-carry" : kind === "stub" ? " cv-leg-stub" : folded ? " cv-leg-folded" : "";
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

/** A `links[]` entry (§2.3): an orthogonal wire between two devices in the
 *  branch's hue, dashed when down, blocked or unconfirmed; the port pair,
 *  mode, VLANs and state on hover or while selected; clickable, so the
 *  inspector can show its row. */
export function LinkEdge(e: E) {
  const overlay = useContext(OverlayContext);
  const d = e.data;
  if (!d || d.kind !== "link") return null;
  const [path, labelX, labelY] = getSmoothStepPath({ sourceX: e.sourceX, sourceY: e.sourceY, sourcePosition: Position.Bottom, targetX: e.targetX, targetY: e.targetY, targetPosition: Position.Top, borderRadius: 6 });
  const weak = d.state !== "up" || d.source === "unconfirmed";
  return (
    <g data-testid="canvas-link" data-link={d.index} data-state={d.state} data-mode={d.mode} data-source={d.source ?? undefined} className={`cv-link-edge${e.selected ? " selected" : ""}${weak ? " weak" : ""}${overlay ? ` cv-ov-edge-${d.source ?? "authored"}` : ""}`} style={{ "--hue": d.hue } as React.CSSProperties}>
      <BaseEdge id={e.id} path={path} className={`cv-link${e.selected ? " selected" : ""}${weak ? " cv-link-weak" : ""}`} style={{ ...e.style, stroke: d.hue }} label={e.selected ? d.label : undefined} labelX={labelX} labelY={labelY} labelShowBg interactionWidth={14} />
    </g>
  );
}

export const edgeTypes = { arc: ArcEdge, leg: LegEdge, trunk: TrunkEdge, link: LinkEdge };
