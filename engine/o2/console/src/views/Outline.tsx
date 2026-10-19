/**
 * The outline pane (`15` §7, left): the catalog's imprints, and -- when one is
 * open -- its tree (sites → segments → role groups) with counts and evidence
 * dots, so a filter like "everything imputed" reads without opening the map.
 *
 * The tree reuses `layout/sites.ts`'s grouping so the outline and the map
 * agree on what a site is. The root row carries the O7 counts (placed /
 * unplaced / non-local), so nothing is silently invisible. Only the currently
 * selected segment expands its role groups, keeping the DOM bounded on a
 * 32-segment twin.
 */
import { Link } from "react-router";

import type { ConsoleApiError } from "../api/client";
import type { ImprintList } from "../api/types";
import type { ConsoleGraph, GraphSegment } from "../model/graph";
import { zoomToPath, type Zoom } from "../model/zoom";
import { groupIntoSites } from "../layout/sites";
import { addSegmentLabel, type AddedSegment } from "../edit/allocate";

export interface OutlineProps {
  catalog: ImprintList | null;
  error: ConsoleApiError | null;
  loading: boolean;
  currentImprint: string | null;
  graph: ConsoleGraph | null;
  zoom: Zoom | null;
  go: (path: string) => void;
  /**
   * S5.1.a: "add a segment" from a site row (or, with `null`, from the tree
   * root -- the site-less add). The outline carries it as well as the map
   * because the outline lists **every** segment and every site, including the
   * groups R6's per-group budget collapsed on the map.
   * Absent ⇒ no add controls (the read-only render).
   */
  onAddSegment?: (siteKey: string | null) => void;
  /** S5.1.a (A3): the segments the edit store adds that the export does not
   *  observe. Listed under the site each would group with, with its state --
   *  the map does not draw them yet, so this is where they are reachable. */
  added?: readonly AddedSegment[];
  /** Select a segment IN PLACE -- an added segment's row, which has no drawn
   *  level to drill into. */
  onSelectSegment?: (cidr: string) => void;
  /** The segment the inspector is acting on; its row is marked `aria-current`. */
  selectedCidr?: string | null;
}

export function Outline(props: OutlineProps): JSX.Element {
  const { catalog, error, loading, currentImprint, graph, zoom, go } = props;
  return (
    <nav className="pane pane-outline" aria-label="Imprints">
      <h2>Imprints</h2>
      {loading && <p className="meta">loading the catalog…</p>}
      {error && (
        <p className="notice is-error" role="alert">
          {error.kind}: {error.message}
        </p>
      )}
      {catalog && (
        <ul className="imprint-list">
          {catalog.imprints.map((item) => (
            <li key={item.id}>
              {/* S5.1.d: through `go`, so the shell's ONE leave-guard covers
                  the outline's imprint list too -- it was one of GAP-094's
                  unguarded exits. Still a real anchor: middle-click and
                  copy-link keep working. */}
              <Link
                to={zoomToPath({ level: "network", imprint: item.id })}
                className={item.id === currentImprint ? "is-current" : ""}
                data-testid="outline-imprint"
                data-imprint={item.id}
                onClick={(event) => {
                  if (event.defaultPrevented || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
                  if (event.button !== 0) return;
                  event.preventDefault();
                  go(zoomToPath({ level: "network", imprint: item.id }));
                }}
              >
                <span className="mono">{item.id}</span>
                <span className="meta">
                  {" "}
                  {item.hosts} hosts · {item.segments} segments
                  {fromCapture(item) ? " · from capture" : " · hand-authored"}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
      {catalog && catalog.imprints.length === 0 && (
        <p className="meta">
          no imprints in this catalog — point <code>axrange serve</code> at one
          with <code>--imprints-dir</code>
        </p>
      )}
      {catalog && Array.isArray(catalog.skipped) && catalog.skipped.length > 0 && (
        <>
          <h3>Could not be read</h3>
          <p className="meta">In the imprints folder, but its files could not be read, so it cannot be opened:</p>
          <ul className="skipped" data-testid="unreadable-imprints">
            {catalog.skipped.map((s) => (
              <li key={s.id}>
                <span className="mono">{s.id}</span> — {s.reason}
              </li>
            ))}
          </ul>
        </>
      )}

      {graph && currentImprint && (
        <Tree
          graph={graph}
          zoom={zoom}
          go={go}
          onAddSegment={props.onAddSegment}
          added={props.added ?? []}
          onSelectSegment={props.onSelectSegment}
          selectedCidr={props.selectedCidr ?? null}
        />
      )}
    </nav>
  );
}

function currentCidr(zoom: Zoom | null): string | null {
  if (!zoom) return null;
  if (zoom.level === "segment" || zoom.level === "host" || zoom.level === "matrix") {
    return zoom.cidr ?? null;
  }
  return null;
}

/** Where each ADDED segment is listed: under the observed site it would group
 *  with, or -- when it would form a group no observed site is (a new prefix, or
 *  the misc group this twin does not have) -- under a group of its own. Asked
 *  of `groupIntoSites` over the observed segments plus the added ones, so the
 *  outline and the add form's landing note apply one rule. */
function placeAdded(
  graph: ConsoleGraph,
  observedKeys: ReadonlySet<string>,
  added: readonly AddedSegment[],
): { bySite: Map<string, AddedSegment[]>; elsewhere: Array<{ key: string; label: string; items: AddedSegment[] }> } {
  const bySite = new Map<string, AddedSegment[]>();
  const elsewhere = new Map<string, { key: string; label: string; items: AddedSegment[] }>();
  if (added.length === 0) return { bySite, elsewhere: [] };
  const NONE = { bytes: 0, flows: 0 };
  const combined = groupIntoSites([
    ...graph.segments.map((s) => ({ observedCidr: s.observedCidr, northSouth: s.northSouth })),
    ...added.map((a) => ({ observedCidr: a.cidr, northSouth: NONE })),
  ]);
  for (const a of added) {
    const home = combined.find((site) => site.segments.some((m) => m.observedCidr === a.cidr));
    const key = home?.key ?? "site:misc";
    if (observedKeys.has(key)) {
      bySite.set(key, [...(bySite.get(key) ?? []), a]);
    } else {
      const label = home && !home.isMisc ? home.label : "misc";
      const group = elsewhere.get(key) ?? { key, label, items: [] };
      group.items.push(a);
      elsewhere.set(key, group);
    }
  }
  return { bySite, elsewhere: [...elsewhere.values()] };
}

function Tree(props: {
  graph: ConsoleGraph;
  zoom: Zoom | null;
  go: (path: string) => void;
  onAddSegment?: ((siteKey: string | null) => void) | undefined;
  added: readonly AddedSegment[];
  onSelectSegment?: ((cidr: string) => void) | undefined;
  selectedCidr: string | null;
}): JSX.Element {
  const { graph, zoom, go, onAddSegment, added, onSelectSegment, selectedCidr } = props;
  const sites = groupIntoSites(graph.segments);
  const openCidr = currentCidr(zoom);
  const { bySite, elsewhere } = placeAdded(graph, new Set(sites.map((s) => s.key)), added);

  return (
    <div className="outline-tree" data-testid="outline-tree">
      <h3>{graph.imprintId}</h3>
      {/* O7: nothing is silently invisible -- the root row counts the placed,
          unplaced and non-local populations. */}
      <ul className="tree-root">
        <li className="tree-count">
          <span>placed</span>
          <span className="mono">{graph.segments.reduce((s, seg) => s + seg.hostCount, 0)}</span>
        </li>
        <li className="tree-count">
          <Link to={zoomToPath({ level: "network", imprint: graph.imprintId })}>unplaced</Link>
          <span className="mono" data-testid="outline-unplaced">{graph.unplacedLocal.length}</span>
        </li>
        <li className="tree-count">
          <Link to={zoomToPath({ level: "matrix", imprint: graph.imprintId })}>external</Link>
          <span className="mono" data-testid="outline-nonlocal">{graph.nonLocal.length}</span>
        </li>
      </ul>

      {/* The site-LESS add (S5.1.a): a segment whose two-octet prefix no other
          segment shares folds into the shared `misc` group, which is what the
          form says. */}
      {onAddSegment && (
        <button
          type="button"
          className="link tree-add-root"
          data-testid="outline-add-segment"
          onClick={() => onAddSegment(null)}
        >
          + segment (no site)
        </button>
      )}

      {sites.map((site) => (
        <div className="tree-site" key={site.key}>
          <div className="tree-site-label">
            {site.isMisc ? "misc /16s" : site.label} <span className="meta">· {site.segments.length}</span>
            {onAddSegment && (
              <button
                type="button"
                className="link tree-add"
                data-testid="outline-site-add"
                data-site={site.key}
                aria-label={addSegmentLabel(site)}
                onClick={() => onAddSegment(site.key)}
              >
                +
              </button>
            )}
          </div>
          <ul className="tree-segments">
            {site.segments.map((seg) => {
              const selected = seg.observedCidr === selectedCidr;
              return (
                <li
                  key={seg.observedCidr}
                  className={`${seg.observedCidr === openCidr ? "is-current" : ""}${selected ? " is-selected" : ""}`.trim()}
                >
                  <button
                    type="button"
                    className="tree-segment"
                    aria-current={selected ? "true" : undefined}
                    onClick={() => go(zoomToPath({ level: "segment", imprint: graph.imprintId, cidr: seg.observedCidr }))}
                  >
                    <EvidenceDot segment={seg} />
                    <span className="mono">{seg.observedCidr}</span>
                    <span className="meta" title={`${seg.deployedCount} of ${seg.hostCount} observed hosts would deploy (design-time, not running VMs)`}> {seg.deployedCount}/{seg.hostCount}</span>
                  </button>
                  {seg.observedCidr === openCidr && seg.roleHistogram.length > 0 && (
                    <ul className="tree-roles">
                      {seg.roleHistogram.map((r) => (
                        <li key={r.role}>
                          <span>{r.role}</span> <span className="mono">{r.count}</span>
                        </li>
                      ))}
                    </ul>
                  )}
                </li>
              );
            })}
            {(bySite.get(site.key) ?? []).map((a) => (
              <AddedRow key={a.cidr} added={a} selected={a.cidr === selectedCidr} onSelect={onSelectSegment} />
            ))}
          </ul>
        </div>
      ))}

      {elsewhere.map((group) => (
        <div className="tree-site" key={`added:${group.key}`} data-testid="outline-added-group">
          <div className="tree-site-label">
            {/* Restated for S5.1.b (LES-299): a PLACED added segment -- a
                clone -- IS drawn on the network map now, so the group label no
                longer claims otherwise; each row says its own record state. */}
            {group.label} <span className="meta">· added in this file</span>
          </div>
          <ul className="tree-segments">
            {group.items.map((a) => (
              <AddedRow key={a.cidr} added={a} selected={a.cidr === selectedCidr} onSelect={onSelectSegment} />
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}

/** A segment the edit store adds. Clicking SELECTS it in place -- the map has
 *  no drawing of it to drill into -- and the badge says which state its record
 *  is in, in the change list's own words. */
function AddedRow(props: {
  added: AddedSegment;
  selected: boolean;
  onSelect?: ((cidr: string) => void) | undefined;
}): JSX.Element {
  const { added, selected, onSelect } = props;
  return (
    <li className={`tree-added${selected ? " is-selected" : ""}`}>
      <button
        type="button"
        className="tree-segment"
        data-testid="outline-added-segment"
        data-cidr={added.cidr}
        aria-current={selected ? "true" : undefined}
        disabled={!onSelect}
        onClick={() => onSelect?.(added.cidr)}
      >
        <span className="mono">{added.cidr}</span>{" "}
        <span className={`change-state is-${added.state}`} data-testid="outline-added-state">
          {added.state} add
        </span>
      </button>
    </li>
  );
}

/** A small dot in the tree carrying the segment's dominant placement-evidence
 *  class (`15` §6: the same encoding in the tree as on the map). */
function EvidenceDot({ segment }: { segment: GraphSegment }): JSX.Element {
  const top = segment.evidenceMix[0];
  const cls = !top || top.evidenceClass === "(none)" ? "ev-none" : `ev-${top.evidenceClass}`;
  return <span className={`tree-dot ${cls}`} aria-hidden="true" />;
}

/** Whether a bundle was cast from a capture (an aximprint export or sidecar)
 *  rather than hand-authored -- the outline's one-word label. A server that
 *  predates `has_topology_export` is read off the sidecar alone. */
export function fromCapture(item: { has_topology_export?: boolean; has_provenance: boolean }): boolean {
  return item.has_topology_export === true || item.has_provenance;
}

