/**
 * The document behind `/canvas/new`: an UNTITLED design that lives in the
 * browser until its first Save, which is a Save-As (`POST /api/twin-configs`)
 * under an id the operator names then.
 *
 * A blank design is the smallest document the loader reads: the te file with
 * no segments and no nodes, and the root manifest the loader needs
 * (`twin.yaml`, `containment: uncontained`: the te file declares no egress
 * posture). The engine refuses a twin_config with no segments ("no segments
 * left after `when` gating"), so the canvas holds an untitled design with
 * none back from Save with a plain reason -- never a hidden default segment.
 */

/** The ids `POST /api/twin-configs` accepts (the fake and `_is_safe_catalog_ref` agree). */
export const DESIGN_ID = /^[A-Za-z0-9][A-Za-z0-9._-]*$/;
export const NAME = /^[A-Za-z0-9][A-Za-z0-9._-]*$/;

/** The placeholder id an untitled design carries until its Save-As renames it. */
export const UNTITLED_ID = "untitled";

/** Why an untitled design cannot be saved yet. */
export const NO_SEGMENT_REASON = "add at least one segment before saving";

export interface BlankDesign {
  id: string;
  description?: string;
  /** The first segment; none gives `segments: []` (the untitled canvas's start). */
  segment?: { name: string; cidr: string };
}

/** A YAML double-quoted scalar: JSON's string form is one. */
function quoted(s: string): string {
  return JSON.stringify(s);
}

/** The two files of a blank design. */
export function blankDesignFiles(d: BlankDesign): Record<string, string> {
  // Neither the comments nor the default description name the id: a Save-As rewrites the id lines only.
  const description = d.description?.trim() || "a twin designed on the canvas.";
  const segments = d.segment ? ["  segments:", `    - { name: ${d.segment.name}, cidr: ${d.segment.cidr}, zone_type: vlan }`] : ["  segments: []"];
  const te = [
    "# Designed on the XTwin canvas; the twin config (te tier).",
    "twin_config:",
    `  id: ${d.id}`,
    `  description: ${quoted(description)}`,
    "",
    ...segments,
    "",
    "  nodes: []",
    "",
  ].join("\n");
  const manifest = [
    "# The root manifest: which file is each tier's default.",
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

/** Why `id` cannot name a new design, or null: the API's id rule, then the ids already in the catalog. */
export function designIdProblem(id: string, taken: ReadonlySet<string>): string | null {
  if (!id) return "give the design an id";
  if (!DESIGN_ID.test(id)) return "the id must start with a letter or digit and use only letters, digits, '.', '_' and '-'";
  if (taken.has(id)) return `'${id}' is already in the catalog; pick another id`;
  return null;
}
