/**
 * The bundle manifest (`twin.yaml`): which file is each tier's default. The
 * canvas reads it to find the traffic profile the side panel edits, rewrites
 * `twin.id` on a Save-As, and wires a profile it had to create. Pure.
 */
import { contentEnd, encodeScalar, entry, get, insertLines, joinLines, parseYaml, removeSpan, replaceValueOnLine, scalarText } from "./yaml";

export const MANIFEST = "twin.yaml";

export interface Manifest {
  id: string | null;
  te: string | null;
  tfc: string | null;
  tap: string | null;
  /** `twin.cluster`: the target this twin deploys to (null: the default target). D6: the target a
   *  dropped device's image is chosen for. */
  cluster: string | null;
}

export function readManifest(text: string): Manifest {
  const doc = parseYaml(text);
  const tw = get(doc.root, "twin");
  return {
    id: scalarText(get(tw, "id")),
    te: scalarText(get(tw, "tiers", "te", "default")),
    tfc: scalarText(get(tw, "tiers", "tfc", "default")),
    tap: scalarText(get(tw, "tiers", "tap", "default")),
    cluster: scalarText(get(tw, "cluster")),
  };
}

export function setManifestId(text: string, id: string): string {
  return setManifestScalar(text, "id", id);
}

export function setManifestDescription(text: string, description: string): string {
  return setManifestScalar(text, "description", description);
}

/** Set (or, with null, remove) `twin.cluster` -- the target the twin deploys to and its dropped
 *  devices' images are chosen for (D6). */
export function setManifestCluster(text: string, cluster: string | null): string {
  return setManifestScalar(text, "cluster", cluster);
}

function setManifestScalar(text: string, key: "id" | "description" | "cluster", value: string | null): string {
  const doc = parseYaml(text);
  const lines = [...doc.lines];
  const tw = get(doc.root, "twin");
  if (!tw || tw.kind !== "map") return text;
  const e = entry(tw, key);
  if (value === null) {
    if (!e) return text;
    removeSpan(lines, e.start, e.end, tw.indent);
    return joinLines(lines, doc.trailingNewline);
  }
  if (e) replaceValueOnLine(lines, e, encodeScalar(value));
  else insertLines(lines, tw.start, [`${" ".repeat(tw.indent)}${key}: ${encodeScalar(value)}`]);
  return joinLines(lines, doc.trailingNewline);
}

/** Wire `twin.tiers.tfc.default` to `path` when the manifest has no tfc tier. */
export function wireTfcDefault(text: string, path: string): string {
  return wireTierDefault(text, "tfc", path);
}

export function wireTapDefault(text: string, path: string): string {
  return wireTierDefault(text, "tap", path);
}

function wireTierDefault(text: string, tier: "tfc" | "tap", path: string): string {
  const doc = parseYaml(text);
  const lines = [...doc.lines];
  const tw = get(doc.root, "twin");
  if (!tw || tw.kind !== "map") return text;
  const tiers = entry(tw, "tiers");
  if (!tiers) {
    insertLines(lines, contentEnd(lines, tw.end), [`${" ".repeat(tw.indent)}tiers:`, `${" ".repeat(tw.indent + 2)}${tier}:`, `${" ".repeat(tw.indent + 4)}default: ${encodeScalar(path)}`]);
    return joinLines(lines, doc.trailingNewline);
  }
  if (tiers.value.kind !== "map") return text;
  const tfc = entry(tiers.value, tier);
  if (tfc && tfc.value.kind === "map") {
    const d = entry(tfc.value, "default");
    if (d) replaceValueOnLine(lines, d, encodeScalar(path));
    else insertLines(lines, contentEnd(lines, tfc.end), [`${" ".repeat(tfc.value.indent)}default: ${encodeScalar(path)}`]);
    return joinLines(lines, doc.trailingNewline);
  }
  const ind = tiers.value.indent;
  insertLines(lines, contentEnd(lines, tiers.end), [`${" ".repeat(ind)}${tier}:`, `${" ".repeat(ind + 2)}default: ${encodeScalar(path)}`]);
  return joinLines(lines, doc.trailingNewline);
}

/** Drop `twin.tiers.<tier>` when its tier file goes with its last entry (the
 *  canvas's last tap removed, 2026-11-09): the engine's manifest refuses a
 *  default that names no file. A tier block carrying more than `default`
 *  (variants) keeps its other keys and loses only `default:`. */
export function unwireTierDefault(text: string, tier: "tfc" | "tap"): string {
  const doc = parseYaml(text);
  const lines = [...doc.lines];
  const tw = get(doc.root, "twin");
  if (!tw || tw.kind !== "map") return text;
  const tiers = entry(tw, "tiers");
  if (!tiers || tiers.value.kind !== "map") return text;
  const t = entry(tiers.value, tier);
  if (!t) return text;
  if (t.value.kind === "map" && t.value.entries.some((e) => e.key !== "default")) {
    const d = entry(t.value, "default");
    if (!d) return text;
    removeSpan(lines, d.start, d.end, t.value.indent);
  } else removeSpan(lines, t.start, t.end, tiers.value.indent);
  return joinLines(lines, doc.trailingNewline);
}
