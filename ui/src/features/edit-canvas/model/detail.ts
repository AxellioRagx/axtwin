/**
 * Node level of detail (`SPEC.md` "Shell and chrome", decided 2026-11-05):
 * two levels switched on the viewport zoom with hysteresis, and the short
 * name a chip carries. Pure -- no React, no DOM.
 */
export type DetailLevel = "full" | "compact";
/** The rail's toggle: follow the zoom, or pin a level. */
export type DetailMode = "auto" | DetailLevel;

/** Expand to chips at and above this zoom ... */
export const DETAIL_EXPAND = 0.6;
/** ... and collapse to glyph tiles below this one; between the two the
 *  level holds, so a tile does not flicker while panning at the boundary. */
export const DETAIL_COLLAPSE = 0.5;

export function nextLevel(prev: DetailLevel, zoom: number): DetailLevel {
  if (zoom >= DETAIL_EXPAND) return "full";
  if (zoom < DETAIL_COLLAPSE) return "compact";
  return prev;
}

/** The zoom's bucket, so a store selector only re-renders on a crossing. */
export function zoomBucket(zoom: number): "hi" | "mid" | "lo" {
  return zoom >= DETAIL_EXPAND ? "hi" : zoom < DETAIL_COLLAPSE ? "lo" : "mid";
}

export const DETAIL_MODES: DetailMode[] = ["auto", "full", "compact"];

export function nextMode(mode: DetailMode): DetailMode {
  return DETAIL_MODES[(DETAIL_MODES.indexOf(mode) + 1) % DETAIL_MODES.length]!;
}

/**
 * A chip's short name, derived and never stored: a `hostname:` key's first
 * label, an `address:` key's last two octets (`.20.4`), a `mac:` key's last
 * two bytes; a design node (named after its group) keeps its name.
 */
export function shortName(id: string, address: string | null): string {
  const at = id.indexOf(":");
  const kind = at > 0 ? id.slice(0, at) : "";
  const value = at > 0 ? id.slice(at + 1) : id;
  if (kind === "hostname") return value.split(".")[0] || value;
  if (kind === "address" || (kind === "" && /^\d+\.\d+\.\d+\.\d+$/.test(value))) return lastOctets(value);
  if (kind === "mac") return value.slice(-5);
  if (kind) return value;
  return value || (address ? lastOctets(address) : id);
}

function lastOctets(ip: string): string {
  const parts = ip.split(".");
  return parts.length === 4 ? `.${parts[2]}.${parts[3]}` : ip;
}

/** Bytes as a chrome label reads them: `1.2 GB`, `310 MB`, `0 B`. */
export function shortBytes(n: number): string {
  if (!Number.isFinite(n) || n <= 0) return "0 B";
  const units = ["B", "kB", "MB", "GB", "TB", "PB"];
  let i = 0;
  let v = n;
  while (v >= 1000 && i < units.length - 1) {
    v /= 1000;
    i += 1;
  }
  const text = i === 0 ? String(Math.round(v)) : v >= 100 ? v.toFixed(0) : v >= 10 ? v.toFixed(1).replace(/\.0$/, "") : v.toFixed(1).replace(/\.0$/, "");
  return `${text} ${units[i]}`;
}

/**
 * A compact tile's text keeps a constant SCREEN size: the shell carries
 * `--inv` = 1/zoom, clamped so a tile zoomed far out does not grow its header
 * past its own box (at 2.5 a 13 px label is 32 px in canvas units).
 */
export const INV_ZOOM_MAX = 2.5;
export function invZoom(zoom: number): number {
  if (!Number.isFinite(zoom) || zoom <= 0) return 1;
  return Math.min(INV_ZOOM_MAX, Math.max(1, 1 / zoom));
}

/** The compact tile's glyph geometry in canvas units (`canvas.css` draws
 *  them at these sizes), and how many glyphs a tile of `w × h` can show
 *  under its header without overflowing: the rest fold into `+N`. */
export const GLYPH_W = 54;
export const GLYPH_H = 32;
export const GLYPH_GAP = 6;
export const GLYPH_PAD = 10;
export const COMPACT_HEADER_H = 34;
export function glyphCapacity(w: number, h: number): number {
  const perRow = Math.max(1, Math.floor((w - 2 * GLYPH_PAD + GLYPH_GAP) / (GLYPH_W + GLYPH_GAP)));
  const rows = Math.max(0, Math.floor((h - COMPACT_HEADER_H - 2 * GLYPH_PAD + GLYPH_GAP) / (GLYPH_H + GLYPH_GAP)));
  return perRow * rows;
}
