/**
 * The bundle manifest (`twin.yaml`): which file is each tier's default. The
 * canvas reads it to find the traffic profile the side panel edits, rewrites
 * `twin.id` on a Save-As, and wires a profile it had to create. Pure.
 */
import { contentEnd, encodeScalar, entry, get, insertLines, joinLines, parseYaml, replaceValueOnLine, scalarText } from "./yaml";

export const MANIFEST = "twin.yaml";

export interface Manifest {
  id: string | null;
  te: string | null;
  tfc: string | null;
  tap: string | null;
}

export function readManifest(text: string): Manifest {
  const doc = parseYaml(text);
  const tw = get(doc.root, "twin");
  return {
    id: scalarText(get(tw, "id")),
    te: scalarText(get(tw, "tiers", "te", "default")),
    tfc: scalarText(get(tw, "tiers", "tfc", "default")),
    tap: scalarText(get(tw, "tiers", "tap", "default")),
  };
}

export function setManifestId(text: string, id: string): string {
  const doc = parseYaml(text);
  const lines = [...doc.lines];
  const tw = get(doc.root, "twin");
  if (!tw || tw.kind !== "map") return text;
  const e = entry(tw, "id");
  if (e) replaceValueOnLine(lines, e, encodeScalar(id));
  else insertLines(lines, tw.start, [`${" ".repeat(tw.indent)}id: ${encodeScalar(id)}`]);
  return joinLines(lines, doc.trailingNewline);
}

/** Wire `twin.tiers.tfc.default` to `path` when the manifest has no tfc tier. */
export function wireTfcDefault(text: string, path: string): string {
  const doc = parseYaml(text);
  const lines = [...doc.lines];
  const tw = get(doc.root, "twin");
  if (!tw || tw.kind !== "map") return text;
  const tiers = entry(tw, "tiers");
  if (!tiers) {
    insertLines(lines, contentEnd(lines, tw.end), [`${" ".repeat(tw.indent)}tiers:`, `${" ".repeat(tw.indent + 2)}tfc:`, `${" ".repeat(tw.indent + 4)}default: ${encodeScalar(path)}`]);
    return joinLines(lines, doc.trailingNewline);
  }
  if (tiers.value.kind !== "map") return text;
  const tfc = entry(tiers.value, "tfc");
  if (tfc && tfc.value.kind === "map") {
    const d = entry(tfc.value, "default");
    if (d) replaceValueOnLine(lines, d, encodeScalar(path));
    else insertLines(lines, contentEnd(lines, tfc.end), [`${" ".repeat(tfc.value.indent)}default: ${encodeScalar(path)}`]);
    return joinLines(lines, doc.trailingNewline);
  }
  const ind = tiers.value.indent;
  insertLines(lines, contentEnd(lines, tiers.end), [`${" ".repeat(ind)}tfc:`, `${" ".repeat(ind + 2)}default: ${encodeScalar(path)}`]);
  return joinLines(lines, doc.trailingNewline);
}
