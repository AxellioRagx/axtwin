import { readTap, setTapSensors } from "../features/edit-canvas/edit/tap";
import type { Preflight, ValidationFinding } from "../api/types";

/** Where a `TARGET-SENSOR` finding points: `tap/default.yaml: tap-plan.bindings[0].sensors[1]`. */
export interface SensorRef {
  file: string;
  binding: number;
  sensor: number;
}

export function sensorRef(location: string | null | undefined): SensorRef | null {
  const m = /^(.+?): tap-plan\.bindings\[(\d+)\]\.sensors\[(\d+)\]$/.exec(location ?? "");
  return m ? { file: m[1]!, binding: Number(m[2]), sensor: Number(m[3]) } : null;
}

/** The sensor id a `TARGET-SENSOR` finding names (`sensor X not in lab of target Y`). */
export function missingSensor(f: ValidationFinding): string | null {
  return /^sensor (\S+) not in lab of target /.exec(f.message)?.[1] ?? null;
}

/** The document with the finding's sensor replaced by `to` in its binding's `sensors[]` (a duplicate is
 *  dropped, the order kept). Throws when the tap file or the binding is not there. */
export function remapFiles(files: Readonly<Record<string, string>>, location: string, to: string): Record<string, string> {
  const ref = sensorRef(location);
  if (!ref) throw new Error(`not a tap binding's sensor: ${location}`);
  const text = files[ref.file];
  if (text === undefined) throw new Error(`the document has no ${ref.file}`);
  const binding = readTap(text).bindings.find((b) => b.index === ref.binding);
  if (!binding) throw new Error(`${ref.file} has no binding ${ref.binding}`);
  const next = binding.sensors.map((s, j) => (j === ref.sensor ? to : s)).filter((s, j, all) => all.indexOf(s) === j);
  return { ...files, [ref.file]: setTapSensors(text, ref.binding, next) };
}

/** The preflight's rows, sensor problems first (each remappable), then unbound images. */
export function preflightRows(pf: Preflight | null | undefined): ValidationFinding[] {
  if (!pf) return [];
  const rank = (f: ValidationFinding) => (f.rule_id === "TARGET-SENSOR" ? 0 : f.rule_id === "TARGET-IMAGE" ? 1 : 2);
  return [...pf.findings].sort((a, b) => rank(a) - rank(b));
}
