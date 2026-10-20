/**
 * Plain-words formatting shared by the run-time screens. Pure.
 *
 * Two rules from the ruling live here so no screen restates them:
 *   - **unknown is never zero** (rule 4): `count(null)` is "unknown";
 *   - **recorded vs live** (rule 3): `recordedLabel` / `liveLabel` are the
 *     only spellings the screens use, and a live value always carries its age.
 */

/** "3 s ago", "4 min ago", "2 h ago", "5 days ago" -- from epoch SECONDS. */
export function ago(epochSeconds: number | null | undefined, nowMs: number): string {
  if (epochSeconds === null || epochSeconds === undefined || !Number.isFinite(epochSeconds)) return "at an unknown time";
  const s = Math.max(0, Math.round(nowMs / 1000 - epochSeconds));
  if (s < 60) return `${s} s ago`;
  const m = Math.round(s / 60);
  if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60);
  if (h < 48) return `${h} h ago`;
  return `${Math.round(h / 24)} days ago`;
}

/** An absolute local timestamp for a tooltip. */
export function when(epochSeconds: number | null | undefined): string {
  if (epochSeconds === null || epochSeconds === undefined || !Number.isFinite(epochSeconds)) return "unknown time";
  return new Date(epochSeconds * 1000).toLocaleString();
}

export function count(value: number | null | undefined, noun: string, plural = `${noun}s`): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return `${plural}: unknown`;
  return `${value} ${value === 1 ? noun : plural}`;
}

export function plural(n: number, noun: string, many = `${noun}s`): string {
  return `${n} ${n === 1 ? noun : many}`;
}

/** Recorded op / deploy status in plain words. */
export function statusWord(status: string | null | undefined): string {
  switch (status) {
    case null:
    case undefined:
      return "unknown";
    case "completed":
      return "up";
    case "running":
      return "in progress";
    case "failed":
      return "failed";
    case "halted-due-to-upstream":
      return "not reached (an earlier step failed)";
    case "pending":
      return "waiting";
    case "destroyed":
      return "removed";
    case "cancelled":
      return "cancelled";
    default:
      return status;
  }
}

/** The CSS modifier a status dot / badge takes. */
export function statusTone(status: string | null | undefined): "good" | "busy" | "bad" | "warn" | "none" {
  switch (status) {
    case "completed":
    case "running-vm":
    case "alive":
    case "reachable":
      return "good";
    case "running":
    case "pending":
      return "busy";
    case "failed":
    case "dead":
    case "unreachable":
    case "stopped":
    case "absent":
      return "bad";
    case "halted-due-to-upstream":
    case "paused":
      return "warn";
    default:
      return "none";
  }
}

export const RECORDED = "recorded";

export function liveLabel(checkedAt: number | null | undefined, nowMs: number): string {
  return `live, checked ${ago(checkedAt, nowMs)}`;
}

/** A VM's LIVE power state's tone (`running` is healthy here, unlike an op). */
export function powerTone(state: string | null | undefined): "good" | "busy" | "bad" | "warn" | "none" {
  switch (state) {
    case "running":
      return "good";
    case "stopped":
    case "absent":
      return "bad";
    case "paused":
      return "warn";
    default:
      return "none";
  }
}
