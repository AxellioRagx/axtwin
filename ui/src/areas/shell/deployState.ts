import type { DeploySummary } from "../../api/types";

// The deploy-state wording of the canvas pill (features/edit-canvas/CanvasPage.tsx),
// re-implemented here read-only so the list pages do not import the canvas.

/** The twin's standing deploy: its newest `te` deploy that is running or completed. */
export function standingDeploy(deploys: readonly DeploySummary[], twinId: string): DeploySummary | null {
  const mine = deploys.filter((d) => d.scenario_id === twinId && (d.tier ?? "te") === "te" && (d.status === "running" || d.status === "completed"));
  mine.sort((a, b) => b.started_at - a.started_at);
  return mine[0] ?? null;
}

/** The tier playouts standing on a te deploy: a tier row's `scenario_id` is `<tier>-<te deploy id>`. */
export function tierStatus(rows: readonly DeploySummary[], teDeployId: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const tier of ["tfc", "tap"]) {
    const mine = rows.filter((d) => d.scenario_id === `${tier}-${teDeployId}` && d.status !== "destroyed");
    mine.sort((a, b) => b.started_at - a.started_at);
    if (mine[0]) out[tier] = mine[0].status;
  }
  return out;
}

export function tierWord(status: string): string {
  return status === "completed" ? "✓" : status === "failed" ? "✗ failed" : status === "running" ? "⟳ running" : status;
}

/** Every tier of one twin as `[tier, status]`, te first; empty when nothing stands. */
export function twinTiers(rows: readonly DeploySummary[], twinId: string): Array<[string, string]> {
  const te = standingDeploy(rows, twinId);
  if (!te) return [];
  return [["te", te.status], ...Object.entries(tierStatus(rows, te.deploy_id))];
}
