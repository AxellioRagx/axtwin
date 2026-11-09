import type { DeploySummary } from "../../api/types";

// The deploy-state wording of the canvas pill (features/edit-canvas/CanvasPage.tsx),
// re-implemented here read-only so the list pages do not import the canvas.

/** The twin's standing deploy: its newest `te` deploy that is running or completed. */
export function standingDeploy(deploys: readonly DeploySummary[], twinId: string): DeploySummary | null {
  const mine = deploys.filter((d) => d.scenario_id === twinId && (d.tier ?? "te") === "te" && (d.status === "running" || d.status === "completed"));
  mine.sort((a, b) => b.started_at - a.started_at);
  return mine[0] ?? null;
}

/** The follow-on tiers, in the order Deploy runs them (te → tap → tfc). */
export const FOLLOW_ON_TIERS = ["tap", "tfc"] as const;

/** The tier playouts standing on a te deploy: a tier row's `scenario_id` is `<tier>-<te deploy id>`.
 *  Where no row stands, the te row's `follow_on` record answers (2026-11-09): `pending` behind te,
 *  `running`, `failed` before its playout wrote a row, or `skipped` (the twin has no plan for it).
 *  `rowsOnly` leaves the records out (what a baseline re-apply can act on). Ordered tap, tfc. */
export function tierStatus(rows: readonly DeploySummary[], teDeployId: string, opts: { rowsOnly?: boolean } = {}): Record<string, string> {
  const out: Record<string, string> = {};
  const te = rows.find((d) => d.deploy_id === teDeployId);
  for (const tier of FOLLOW_ON_TIERS) {
    const mine = rows.filter((d) => d.scenario_id === `${tier}-${teDeployId}` && d.status !== "destroyed");
    mine.sort((a, b) => b.started_at - a.started_at);
    const rec = opts.rowsOnly ? undefined : te?.follow_on?.[tier];
    // A record newer than the row (a re-run that failed) says more than the stale row.
    if (mine[0] && !(rec && rec.status !== "completed" && (rec.at ?? 0) > mine[0].updated_at)) out[tier] = mine[0].status;
    else if (rec) out[tier] = rec.status;
  }
  return out;
}

/** Why a follow-on tier is in the state it is (its record's reason), or null. */
export function tierReason(rows: readonly DeploySummary[], teDeployId: string, tier: string): string | null {
  return rows.find((d) => d.deploy_id === teDeployId)?.follow_on?.[tier]?.reason ?? null;
}

/** Whether a te deploy, or a tier following it, is still moving: the pages poll until it is not. */
export function deployMoving(rows: readonly DeploySummary[], te: DeploySummary | null): boolean {
  if (!te) return false;
  if (te.status === "running") return true;
  return Object.values(tierStatus(rows, te.deploy_id)).some((s) => s === "running" || s === "pending");
}

export function tierWord(status: string): string {
  return status === "completed" ? "✓" : status === "failed" ? "✗ failed" : status === "running" ? "⟳ running" : status === "pending" ? "… queued" : status === "skipped" ? "—" : status;
}

/** Every tier of one twin as `[tier, status]`, te first; empty when nothing stands. */
export function twinTiers(rows: readonly DeploySummary[], twinId: string): Array<[string, string]> {
  const te = standingDeploy(rows, twinId);
  if (!te) return [];
  return [["te", te.status], ...Object.entries(tierStatus(rows, te.deploy_id))];
}
