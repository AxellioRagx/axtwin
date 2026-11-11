/**
 * The refit after a fold opens or closes (R5-2, 2026-11-08): the view goes to the router plus the
 * row that changed (an open site's row, or the router's rail), padded, and never below 50 % zoom.
 * A row too wide for 50 % is shown from its left edge, and the operator pans the rest.
 */
import type { CanvasNode, Placement } from "./placement";

/** The least zoom a fold's refit lands at: below it a fold's and a switch's words are unreadable. */
export const FOLD_FIT_MIN_ZOOM = 0.5;
/** The room around the fitted boxes, as a fraction of the view (React Flow's `padding`). */
export const FOLD_FIT_PADDING = 0.15;

export type Box = { x: number; y: number; w: number; h: number };
export type Viewport = { x: number; y: number; zoom: number };

/** The ids a fold's refit frames: the carrier's node and the open site's tile with its row, or the carrier's rail. */
export function foldFitTargets(placement: Pick<Placement, "nodes" | "rows">, key: string): string[] | null {
  const base = key.replace(/#\d+$/, "");
  const carrier = base.startsWith("site:") ? base.slice("site:".length, base.indexOf("|")) : base.startsWith("carrier:") ? base.slice("carrier:".length) : null;
  if (carrier == null) return null;
  const rows = placement.rows ?? {};
  const carrierNode = placement.nodes.find((n) => (n.data.kind === "router" || n.data.kind === "device") && n.data.name === carrier);
  const tile = base.startsWith("site:") ? placement.nodes.find((n) => n.data.kind === "overflow" && n.data.siteKey === base && n.data.expanded) : undefined;
  const head = carrierNode ? [carrierNode.id] : [];
  if (tile && rows[tile.id]?.length) return [...head, tile.id, ...rows[tile.id]!];
  if (carrierNode && rows[carrierNode.id]?.length) return [...head, ...rows[carrierNode.id]!];
  return head.length ? head : null;
}

/** The bounding box of the top-level boxes among `ids`. */
export function boundsOf(nodes: readonly CanvasNode[], ids: readonly string[]): Box | null {
  const want = new Set(ids);
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const n of nodes) {
    if (!want.has(n.id) || n.parentId) continue;
    x0 = Math.min(x0, n.position.x);
    y0 = Math.min(y0, n.position.y);
    x1 = Math.max(x1, n.position.x + (n.width ?? 0));
    y1 = Math.max(y1, n.position.y + (n.height ?? 0));
  }
  return Number.isFinite(x0) ? { x: x0, y: y0, w: x1 - x0, h: y1 - y0 } : null;
}

/** Where the view goes to frame `b` in a `view`-sized pane: fitted and centred, or, when that would
 *  need less than `minZoom`, at `minZoom` with the box's left edge (and top, if it is too tall) in view. */
export function foldViewport(b: Box, view: { width: number; height: number }, opts: { padding?: number; minZoom?: number; maxZoom?: number } = {}): Viewport {
  const pad = opts.padding ?? FOLD_FIT_PADDING;
  const minZoom = opts.minZoom ?? FOLD_FIT_MIN_ZOOM;
  const maxZoom = opts.maxZoom ?? 1.5;
  const fit = Math.min(view.width / Math.max(1, b.w * (1 + 2 * pad)), view.height / Math.max(1, b.h * (1 + 2 * pad)), maxZoom);
  if (fit >= minZoom) return { zoom: fit, x: view.width / 2 - (b.x + b.w / 2) * fit, y: view.height / 2 - (b.y + b.h / 2) * fit };
  const zoom = minZoom;
  const gutterX = (view.width * pad) / (1 + 2 * pad);
  const gutterY = (view.height * pad) / (1 + 2 * pad);
  const x = b.w * zoom <= view.width - 2 * gutterX ? view.width / 2 - (b.x + b.w / 2) * zoom : gutterX - b.x * zoom;
  const y = b.h * zoom <= view.height - 2 * gutterY ? view.height / 2 - (b.y + b.h / 2) * zoom : gutterY - b.y * zoom;
  return { zoom, x, y };
}
