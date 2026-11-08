/**
 * The ONE timestamp presentation of the console (R4-7, 2026-11-08): local time with the zone
 * abbreviation, to the minute -- `2026-10-06 14:00 CDT`. Lists, detail pages, the Discovery
 * pages' windows and probes, and anything the canvas shows go through `formatInstant`; where an
 * element can carry a tooltip (`When`), the exact UTC instant to the second rides there.
 * Before it the Imprints list said "2026-10-06 14:00" (local, no zone) while a source's page
 * said "19:30:36 UTC" for the same kind of value.
 */

/** An ISO 8601 string or epoch SECONDS as a Date, or null when it is not a time. */
export function instantOf(value: string | number | null | undefined): Date | null {
  if (value === null || value === undefined || value === "") return null;
  const d = typeof value === "number" ? new Date(value * 1000) : new Date(Date.parse(value));
  return Number.isFinite(d.getTime()) ? d : null;
}

/** The local zone's abbreviation at that instant (`CDT`, `CET`, or `GMT+5:30` where none exists). */
function zoneOf(d: Date): string {
  try {
    return new Intl.DateTimeFormat("en-US", { timeZoneName: "short" }).formatToParts(d).find((p) => p.type === "timeZoneName")?.value ?? "";
  } catch {
    return "";
  }
}

/** `2026-10-06 14:00 CDT`; `fallback` (default `—`) when the value is not a time. */
export function formatInstant(value: string | number | null | undefined, fallback = "—"): string {
  const d = instantOf(value);
  if (!d) return fallback;
  const p = (n: number) => String(n).padStart(2, "0");
  const zone = zoneOf(d);
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}${zone ? ` ${zone}` : ""}`;
}

/** The exact instant in UTC, to the second: what a tooltip carries. */
export function formatInstantUtc(value: string | number | null | undefined): string {
  const d = instantOf(value);
  return d ? d.toISOString().replace("T", " ").replace(/\.\d+Z$/, " UTC") : "";
}
