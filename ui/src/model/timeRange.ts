/**
 * The run form's time range, as pure data. A range is either RELATIVE -- the last N seconds
 * of what the source holds, counted back from the END of its data (the probe's `last_ts`),
 * never from now, and re-resolved by the server when the run starts -- or ABSOLUTE bounds
 * sent as-is. Both are kept inside the source's data span when the span is known.
 */
import type { DiscoveryRun, Probe } from "../api/types";

export interface Span {
  first: number;
  /** Exclusive end: the probe's `last_ts + 1`, the same arithmetic the server applies. */
  end: number;
}

export type RangeChoice =
  | { kind: "relative"; seconds: number | null } // null: the entire span
  | { kind: "absolute"; from: number; to: number };

export interface QuickRange {
  id: string;
  label: string;
  seconds: number | null;
}

export const QUICK_RANGES: readonly QuickRange[] = [
  { id: "1h", label: "Last 1 h of data", seconds: 3600 },
  { id: "6h", label: "Last 6 h of data", seconds: 6 * 3600 },
  { id: "24h", label: "Last 24 h of data", seconds: 86400 },
  { id: "7d", label: "Last 7 d of data", seconds: 7 * 86400 },
  { id: "span", label: "Entire span", seconds: null },
];

export function spanOf(probe: Probe | null | undefined): Span | null {
  if (!probe || !probe.reachable || probe.first_ts === null || probe.last_ts === null) return null;
  return { first: probe.first_ts, end: probe.last_ts + 1 };
}

/** "1 h", "5 min", "30 s", "7 d", "1 h 30 min". */
export function formatSpan(seconds: number): string {
  const s = Math.round(seconds);
  if (s <= 0) return "0 s";
  if (s % 86400 === 0) return `${s / 86400} d`;
  if (s % 3600 === 0) return `${s / 3600} h`;
  if (s >= 3600) return `${Math.floor(s / 3600)} h ${Math.round((s % 3600) / 60)} min`;
  if (s % 60 === 0) return `${s / 60} min`;
  return `${s} s`;
}

/** The label of a relative range: a quick range's own, else "Last <n> of data". */
export function relativeLabel(seconds: number | null): string {
  const q = QUICK_RANGES.find((r) => r.seconds === seconds);
  if (q) return q.label;
  return `Last ${formatSpan(seconds ?? 0)} of data`;
}

/** The bounds a choice covers: relative ones from the span's end; null when they need a span
 *  that is not known yet. */
export function resolveRange(choice: RangeChoice, span: Span | null): { from: number; to: number } | null {
  if (choice.kind === "absolute") return { from: choice.from, to: choice.to };
  if (!span) return null;
  if (choice.seconds === null) return { from: span.first, to: span.end };
  return { from: Math.max(span.first, span.end - choice.seconds), to: span.end };
}

/** Absolute bounds pulled inside the span; `clamped` says whether anything moved. */
export function clampToSpan(from: number, to: number, span: Span | null): { from: number; to: number; clamped: boolean } {
  if (!span) return { from, to, clamped: false };
  const f = Math.min(Math.max(from, span.first), span.end);
  const t = Math.min(Math.max(to, span.first), span.end);
  return { from: f, to: t, clamped: f !== from || t !== to };
}

/** What the API is sent for a choice: `days` for a relative one (fractional allowed), bounds
 *  for an absolute one. The entire span is sent as its length; the server clamps the start. */
export function rangeBody(choice: RangeChoice, span: Span | null): { days: number } | { ts_from: number; ts_to: number } | null {
  if (choice.kind === "absolute") return { ts_from: choice.from, ts_to: choice.to };
  const seconds = choice.seconds ?? (span ? span.end - span.first : null);
  if (seconds === null || !(seconds > 0)) return null;
  return { days: seconds / 86400 };
}

/** Earlier runs' ranges from this source, newest first, one per distinct range. */
export function recentRanges(runs: DiscoveryRun[] | undefined, sourceId: string, limit = 5): Array<{ from: number; to: number; runId: string }> {
  const seen = new Set<string>();
  const out: Array<{ from: number; to: number; runId: string }> = [];
  const mine = (runs ?? [])
    .filter((r) => r.source_id === sourceId && r.ts_from !== null && r.ts_to !== null && r.ts_to > r.ts_from)
    .sort((a, b) => (a.created_at < b.created_at ? 1 : a.created_at > b.created_at ? -1 : 0));
  for (const r of mine) {
    const key = `${r.ts_from}-${r.ts_to}`;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push({ from: r.ts_from!, to: r.ts_to!, runId: r.id });
    if (out.length >= limit) break;
  }
  return out;
}

/** The census scaled by the fraction of the span selected: an ESTIMATE, assuming the data is
 *  spread evenly. Only the figures the census carries. */
export function estimateCost(range: { from: number; to: number } | null, span: Span | null, extra: Record<string, unknown> | undefined): Partial<Record<"chunks" | "packets" | "bytes", number>> | null {
  if (!range || !span || !extra) return null;
  const total = span.end - span.first;
  if (!(total > 0)) return null;
  const fraction = Math.max(0, Math.min(1, (range.to - range.from) / total));
  const out: Partial<Record<"chunks" | "packets" | "bytes", number>> = {};
  for (const k of ["chunks", "packets", "bytes"] as const) {
    const v = extra[k];
    if (typeof v === "number" && Number.isFinite(v)) out[k] = k === "chunks" ? Math.ceil(v * fraction) : Math.round(v * fraction);
  }
  return Object.keys(out).length ? out : null;
}

/** Gaps the census reports (`extra.gaps` as `[from, to]` pairs) that overlap the range. */
export function gapsIn(range: { from: number; to: number } | null, extra: Record<string, unknown> | undefined): Array<[number, number]> {
  const gaps = extra?.gaps;
  if (!range || !Array.isArray(gaps)) return [];
  return gaps.filter(
    (g): g is [number, number] =>
      Array.isArray(g) && typeof g[0] === "number" && typeof g[1] === "number" && g[0] < range.to && g[1] > range.from,
  );
}

/** Epoch seconds as a `datetime-local` value in the browser's zone. */
export function toLocalInput(epoch: number): string {
  const d = new Date(epoch * 1000);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
}

/** A `datetime-local` value (browser zone) as epoch seconds; null when empty or invalid. */
export function fromLocalInput(value: string): number | null {
  if (!value) return null;
  const ms = new Date(value).getTime();
  return Number.isFinite(ms) ? Math.floor(ms / 1000) : null;
}

/** The browser's zone and its current UTC offset: "America/Chicago, UTC−06:00". */
export function zoneLabel(now: Date = new Date()): string {
  let name = "";
  try {
    name = Intl.DateTimeFormat().resolvedOptions().timeZone ?? "";
  } catch {
    name = "";
  }
  const off = -now.getTimezoneOffset();
  const sign = off >= 0 ? "+" : "−";
  const a = Math.abs(off);
  const offset = `UTC${sign}${String(Math.floor(a / 60)).padStart(2, "0")}:${String(a % 60).padStart(2, "0")}`;
  return name ? `${name}, ${offset}` : offset;
}

/** The measurement-interval presets (seconds). */
export const INTERVAL_PRESETS: readonly number[] = [900, 3600, 14400];
export const DEFAULT_INTERVAL = 3600;
export const DEFAULT_SUBBUCKETS = 12;

const SUBBUCKET_COUNTS = [1, 2, 3, 4, 5, 6, 8, 10, 12, 15, 20, 24, 30, 60];

/** The burst resolutions an interval offers: slice counts that divide it evenly into whole
 *  seconds, coarsest first. */
export function burstChoices(interval: number): number[] {
  return SUBBUCKET_COUNTS.filter((n) => interval % n === 0);
}

/** A valid slice count for an interval: the one asked for if it divides evenly, else the one
 *  whose slice is nearest five minutes. */
export function fitSubbuckets(interval: number, wanted: number): number {
  const choices = burstChoices(interval);
  if (choices.includes(wanted)) return wanted;
  return choices.reduce((best, n) => (Math.abs(interval / n - 300) < Math.abs(interval / best - 300) ? n : best), choices[0] ?? 1);
}
