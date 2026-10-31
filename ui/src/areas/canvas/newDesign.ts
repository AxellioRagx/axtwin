/**
 * What `/canvas/new` writes through A3 `POST /api/twin-configs`: a BLANK
 * design, or a copy of a twin-config in the catalog under a new id.
 *
 * A blank design is the smallest document the engine accepts: one segment
 * and no nodes (`twin_config.segments` may not be empty -- "no segments left
 * after `when` gating" -- while `nodes` may), with the root manifest the
 * loader needs (`twin.yaml`, `containment: uncontained`: the te file declares
 * no egress posture). The validator answers PASS with the "network has no
 * nodes attached" warning, which is the canvas's starting point.
 */
import { saveAsFiles, type WorkingDoc } from "../../features/edit-canvas/edit/document";
import { isNetworkAddress } from "../../features/edit-canvas/edit/allocate";

/** The ids `POST /api/twin-configs` accepts (the fake and `_is_safe_catalog_ref` agree). */
export const DESIGN_ID = /^[A-Za-z0-9][A-Za-z0-9._-]*$/;
export const NAME = /^[A-Za-z0-9][A-Za-z0-9._-]*$/;

export const DEFAULT_SEGMENT = "lan";
export const DEFAULT_CIDR = "10.0.0.0/24";

export interface BlankDesign {
  id: string;
  description: string;
  segment: string;
  cidr: string;
}

/** A YAML double-quoted scalar: JSON's string form is one. */
function quoted(s: string): string {
  return JSON.stringify(s);
}

/** Why the form cannot be submitted yet, or null. */
export function blankDesignProblem(d: BlankDesign): string | null {
  if (!DESIGN_ID.test(d.id)) return "the id must start with a letter or digit and use only letters, digits, '.', '_' and '-'";
  if (!NAME.test(d.segment)) return "the segment name must start with a letter or digit and use only letters, digits, '.', '_' and '-'";
  if (!isNetworkAddress(d.cidr)) return `'${d.cidr}' is not an IPv4 network address in CIDR form (10.1.2.0/24)`;
  return null;
}

/** The two files of a blank design. */
export function blankDesignFiles(d: BlankDesign): Record<string, string> {
  const description = d.description.trim() || `${d.id}: a twin designed on the canvas.`;
  const te = [
    `# ${d.id} -- designed on the axTwin canvas; the twin config (te tier).`,
    "twin_config:",
    `  id: ${d.id}`,
    `  description: ${quoted(description)}`,
    "",
    "  segments:",
    `    - { name: ${d.segment}, cidr: ${d.cidr}, zone_type: vlan }`,
    "",
    "  nodes: []",
    "",
  ].join("\n");
  const manifest = [
    `# ${d.id} -- the root manifest: which file is each tier's default.`,
    "twin:",
    `  id: ${d.id}`,
    `  description: ${quoted(description)}`,
    "  containment: uncontained",
    "  tiers:",
    "    te:",
    "      default: te.yaml",
    "",
  ].join("\n");
  return { "te.yaml": te, "twin.yaml": manifest };
}

/** A copy of an existing document under `newId`: the canvas's own Save-As rewrite. */
export function copiedDesignFiles(source: { id: string; te_file: string; files: Record<string, string> }, newId: string): Record<string, string> {
  const doc: WorkingDoc = { id: source.id, teFile: source.te_file, files: { ...source.files } };
  return saveAsFiles(doc, newId);
}
