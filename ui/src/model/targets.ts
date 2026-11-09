import type { ComponentHealth, LabComponent, TapStatus } from "../api/types";

/** The lab's roles in the deck's order, with the heading each group carries (docs/design/targets-and-lab.md §2.3). */
export const LAB_ROLES: ReadonlyArray<{ role: string; label: string }> = [
  { role: "sensor", label: "Sensors" },
  { role: "telemetry", label: "Telemetry" },
  { role: "attack", label: "Attack" },
  { role: "defence", label: "Defence" },
];

/** The lab grouped by role, in `LAB_ROLES` order; empty groups are left out. */
export function labGroups(components: readonly LabComponent[]): Array<{ role: string; label: string; components: LabComponent[] }> {
  return LAB_ROLES.map(({ role, label }) => ({ role, label, components: components.filter((c) => c.role === role) })).filter((g) => g.components.length > 0);
}

/** A target's overall dot: `down` when any component is down, `up` when every one is up, else `unknown`
 *  (no health read yet, or a component the server could not decide). */
export function overallHealth(components: readonly LabComponent[], health: readonly ComponentHealth[] | null): "up" | "down" | "unknown" {
  if (!health || components.length === 0) return "unknown";
  const by = new Map(health.map((h) => [h.id, h.status]));
  const states = components.map((c) => by.get(c.id) ?? "unknown");
  if (states.includes("down")) return "down";
  if (states.every((s) => s === "up")) return "up";
  return "unknown";
}

/** The `url` a component opens in a new tab, or null (a sensor has no UI). */
export function componentUrl(c: LabComponent): string | null {
  const u = c.reach?.url;
  return typeof u === "string" && /^https?:\/\//.test(u) ? u : null;
}

/** Which live tap bindings deliver to each sensor: per sensor id, the binding count and the twins they belong to.
 *  `taps` is one entry per standing twin (`twin` the twin id, `status` its tap playout); a destroyed or failed
 *  playout delivers nothing. */
export function sensorDeliveries(taps: ReadonlyArray<{ twin: string; status: TapStatus }>): Map<string, { bindings: number; twins: string[] }> {
  const out = new Map<string, { bindings: number; twins: string[] }>();
  for (const { twin, status } of taps) {
    if (status.status !== "running" && status.status !== "completed") continue;
    for (const b of status.bindings ?? []) {
      for (const s of b.sensors ?? []) {
        const e = out.get(s) ?? { bindings: 0, twins: [] };
        e.bindings += 1;
        if (!e.twins.includes(twin)) e.twins.push(twin);
        out.set(s, e);
      }
    }
  }
  return out;
}

/** "2 bindings · switch-test" -- what a sensor chip says it currently receives. */
export function deliveryText(d: { bindings: number; twins: string[] } | undefined): string {
  if (!d || d.bindings === 0) return "no live binding";
  return `${d.bindings} binding${d.bindings === 1 ? "" : "s"} · ${d.twins.join(", ")}`;
}
