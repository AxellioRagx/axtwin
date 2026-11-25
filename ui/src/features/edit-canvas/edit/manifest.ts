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

/** The root key of each tier file and the key under it that names the twin it is for. */
const TIER_ROOTS: Record<string, readonly string[]> = {
  "traffic-profile": ["target", "twin"],
  "tap-plan": ["target"],
  exercise: ["twin", "target"],
};

/** Is `path` a tier file of the bundle (tfc / tap / exr profile)? The te file and manifest are not. */
export function isTierFile(path: string): boolean {
  return /^(tfc|tap|exr)\/[^/]+\.ya?ml$/.test(path);
}

/** The twin id a tier file names (`traffic-profile.target`, `tap-plan.target`, `exercise.twin`), or null. */
export function tierTarget(text: string): string | null {
  const doc = parseYaml(text);
  for (const [root, keys] of Object.entries(TIER_ROOTS)) {
    const body = get(doc.root, root);
    if (!body) continue;
    for (const k of keys) {
      const v = scalarText(get(body, k));
      if (v !== null) return v;
    }
  }
  return null;
}

/** A Save-As of a tier file: the twin it names becomes `newId`, and the leading comment block's
 *  mentions of `oldId` (the header, "Traffic profile: <id> / default") follow. Every other line is
 *  kept. A file that names no twin is returned as it is. */
export function retargetTierFile(text: string, newId: string, oldId: string | null): string {
  const doc = parseYaml(text);
  const lines = [...doc.lines];
  let changed = false;
  for (const [root, keys] of Object.entries(TIER_ROOTS)) {
    const body = get(doc.root, root);
    if (!body || body.kind !== "map") continue;
    for (const k of keys) {
      const e = entry(body, k);
      if (!e || e.valueCol === -1) continue;
      if (scalarText(e.value) !== newId) {
        if (body.flow) continue; // a one-line flow root: not written by the engine or canvas
        replaceValueOnLine(lines, e, encodeScalar(newId));
        changed = true;
      }
    }
  }
  if (oldId && oldId !== newId) {
    const escaped = oldId.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const word = new RegExp("(^|[^A-Za-z0-9_-])" + escaped + "(?![A-Za-z0-9_-])", "g");
    for (let i = 0; i < lines.length; i++) {
      const raw = lines[i]!;
      if (raw.trim() === "") continue;
      if (!raw.trimStart().startsWith("#")) break;
      const next = raw.replace(word, `$1${newId}`);
      if (next !== raw) { lines[i] = next; changed = true; }
    }
  }
  return changed ? joinLines(lines, doc.trailingNewline) : text;
}
