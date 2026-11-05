/**
 * The canvas's floating chrome (`SPEC.md` "Shell and chrome"): the icon rail
 * with its popovers, the tabbed inspector, the dismissible status strip.
 * Presentation only -- every handler and every piece of state is the
 * canvas's (`Canvas.tsx`), passed in; nothing here reads the document.
 */
import { useMemo, useState, type ReactNode } from "react";

import { Icon } from "./icons";
import type { ConsoleGraph } from "./model/graph";

// ---------------------------------------------------------------------------
// the rail
// ---------------------------------------------------------------------------
export interface RailButton {
  /** The accessible name; the tooltip adds the shortcut. */
  name: string;
  key?: string;
  icon: ReactNode;
  onClick(): void;
  pressed?: boolean;
  disabled?: boolean;
  testId?: string;
}

export function RailButtonView({ b }: { b: RailButton }) {
  return (
    <button type="button" className="cv-rail-btn" aria-label={b.name} title={b.key ? `${b.name} (${b.key})` : b.name} aria-pressed={b.pressed} disabled={b.disabled} onClick={b.onClick} data-testid={b.testId}>
      {b.icon}
    </button>
  );
}

export function Rail({ view, edit, children }: { view: RailButton[]; edit: RailButton[] | null; children?: ReactNode }) {
  return (
    <div className="cv-rail-wrap">
      <div className="cv-rail" role="toolbar" aria-label="Canvas tools" data-testid="rail">
        {view.map((b) => (
          <RailButtonView key={b.name} b={b} />
        ))}
        {edit ? (
          <>
            <div className="cv-rail-sep" />
            <div className="cv-rail-group" role="group" aria-label="Edit tools">
              {edit.map((b, i) => (
                <RailButtonView key={b.testId ?? i} b={b} />
              ))}
            </div>
          </>
        ) : null}
      </div>
      {children}
    </div>
  );
}

/** A card beside the rail; one is open at a time (the canvas holds which). */
export function Popover({ title, onClose, children, wide, testId }: { title: string; onClose(): void; children: ReactNode; wide?: boolean; testId?: string }) {
  return (
    <div className={`cv-popover${wide ? " wide" : ""}`} role="dialog" aria-label={title} data-testid={testId ?? "popover"}>
      <div className="cv-popover-head">
        <span>{title}</span>
        <button type="button" className="cv-rail-btn small" aria-label={`Close ${title.toLowerCase()}`} onClick={onClose}>
          <Icon.close />
        </button>
      </div>
      {children}
    </div>
  );
}

// ---------------------------------------------------------------------------
// the inspector
// ---------------------------------------------------------------------------
export type InspectorTab = "selection" | "hosts" | "notes" | "checks";

export interface InspectorProps {
  open: InspectorTab | null;
  onOpen(tab: InspectorTab | null): void;
  selection: ReactNode;
  /** The twin's hosts for the Hosts tab; click centres the canvas on one. */
  graph: ConsoleGraph;
  onCentre(id: string, kind: "host" | "router"): void;
  notes: string[];
  /** The validation state and pending changes, edit mode only. */
  checks: ReactNode | null;
  checksBadge: string | null;
  /** Something is selected: the Selection tab says so on its strip. */
  hasSelection: boolean;
}

const TABS: Array<{ id: InspectorTab; label: string }> = [
  { id: "selection", label: "Selection" },
  { id: "hosts", label: "Hosts" },
  { id: "notes", label: "Notes" },
  { id: "checks", label: "Checks" },
];

export function Inspector(p: InspectorProps) {
  const tabs = TABS.filter((t) => t.id !== "checks" || p.checks !== null);
  return (
    <div className={`cv-inspector${p.open ? " open" : ""}`} data-testid="inspector">
      <div className="cv-tabs" role="tablist" aria-label="Inspector">
        {tabs.map((t) => {
          const badge = t.id === "checks" ? p.checksBadge : t.id === "notes" && p.notes.length ? String(p.notes.length) : t.id === "selection" && p.hasSelection ? "●" : null;
          return (
            <button key={t.id} type="button" role="tab" className={`cv-tab${p.open === t.id ? " active" : ""}`} aria-selected={p.open === t.id} onClick={() => p.onOpen(p.open === t.id ? null : t.id)}>
              {t.label}
              {badge ? <span className={`cv-tab-badge${t.id === "checks" ? ` ${badge}` : ""}`} data-testid={t.id === "checks" ? "validation-state" : undefined}>{badge}</span> : null}
            </button>
          );
        })}
        <button type="button" className="cv-rail-btn small cv-collapse" aria-label={p.open ? "Collapse the inspector" : "Expand the inspector"} title={p.open ? "collapse" : "expand"} onClick={() => p.onOpen(p.open ? null : "selection")}>
          {p.open ? <Icon.chevronRight /> : <Icon.chevronLeft />}
        </button>
      </div>
      {p.open ? (
        <div className="cv-inspector-body" role="tabpanel" data-testid={`inspector-${p.open}`}>
          {p.open === "selection" ? p.selection : null}
          {p.open === "hosts" ? <Hosts graph={p.graph} onCentre={p.onCentre} /> : null}
          {p.open === "notes" ? <Notes notes={p.notes} /> : null}
          {p.open === "checks" ? p.checks : null}
        </div>
      ) : null}
    </div>
  );
}

function Hosts({ graph, onCentre }: { graph: ConsoleGraph; onCentre: InspectorProps["onCentre"] }) {
  const [q, setQ] = useState("");
  const rows = useMemo(() => {
    const needle = q.trim().toLowerCase();
    const hosts = graph.nodes.map((n) => ({ kind: "host" as const, id: n.id, label: n.address ?? n.id, role: n.role, where: n.segment, ghost: n.isGhost }));
    const routers = graph.routers.map((r) => ({ kind: "router" as const, id: r.name, label: r.name, role: "router", where: r.segments.join(", "), ghost: false }));
    const all = [...routers, ...hosts];
    return needle ? all.filter((h) => `${h.id} ${h.label} ${h.role ?? ""} ${h.where ?? ""}`.toLowerCase().includes(needle)) : all;
  }, [graph, q]);
  return (
    <div className="cv-hosts">
      <input type="search" aria-label="Filter hosts" placeholder={`filter ${graph.nodes.length} hosts by name, address, role or segment`} value={q} onChange={(e) => setQ(e.target.value)} />
      {rows.length === 0 ? <p className="muted">{graph.nodes.length ? "nothing matches" : "no hosts"}</p> : null}
      <ul className="cv-hosts-list">
        {rows.map((h) => (
          <li key={`${h.kind}:${h.id}`}>
            <button type="button" className={`cv-hosts-row${h.ghost ? " ghost" : ""}`} onClick={() => onCentre(h.id, h.kind)} title={`centre on ${h.id}`} data-testid="hosts-row" data-id={h.id}>
              <span className="mono cv-hosts-name">{h.label}</span>
              <span className="badge">{h.role ?? "no role"}</span>
              {h.where ? <span className="muted cv-hosts-where">{h.where}</span> : null}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Notes({ notes }: { notes: string[] }) {
  if (!notes.length) return <p className="muted">nothing to note about this twin</p>;
  return (
    <ul className="cv-notes" data-testid="canvas-notes">
      {notes.map((n, i) => (
        <li key={i}>{n}</li>
      ))}
    </ul>
  );
}

// ---------------------------------------------------------------------------
// the status strip
// ---------------------------------------------------------------------------
/** Run-time warnings about the drawing (segments behind "+N more", pairs not
 *  drawn), dismissible; a new set of warnings comes back on its own. */
export function StatusStrip({ lines }: { lines: string[] }) {
  const key = lines.join("\n");
  const [dismissed, setDismissed] = useState<string | null>(null);
  if (!lines.length || dismissed === key) return null;
  return (
    <div className="cv-status" role="status" data-testid="canvas-status">
      <div className="cv-status-lines">
        {lines.map((s, i) => (
          <div key={i} className="muted">{s}</div>
        ))}
      </div>
      <button type="button" className="cv-rail-btn small" aria-label="Dismiss" onClick={() => setDismissed(key)}>
        <Icon.close />
      </button>
    </div>
  );
}
