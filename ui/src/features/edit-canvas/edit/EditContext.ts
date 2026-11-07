/**
 * What the canvas's nodes and edges read in edit mode without the node
 * array being rebuilt: the working model (for a chip's count badge and
 * pinned address), whether the canvas is locked, the armed attach gesture,
 * and the handlers a node's own controls call. `null` in Phase 1's
 * read-only use.
 */
import { createContext, useContext } from "react";

import type { DocModel } from "./document";

export type PaletteKind = "image" | "persona" | "segment" | "group" | "router";

export interface PaletteItem {
  kind: PaletteKind;
  /** The image ref, the persona id, or the group's name. */
  id: string;
  label: string;
  /** For a group: the node kind it was copied from, so a copied router stays a router. */
  nodeKind?: "host" | "router" | "switch";
  /** For a group: the role and image to place with. */
  role?: string | null;
  image?: string;
  count?: number;
  requires?: string[];
  os?: string | null;
  summary?: string;
  /** The image is referenced by a twin in the catalog (the availability cue). */
  available?: boolean;
  /** For a group: declared by another twin in the catalog, listed under "From other twins". */
  foreign?: boolean;
}

export interface EditState {
  model: DocModel;
  locked: boolean;
  /** The attach gesture is armed: the next host click then segment click attaches (Esc disarms). */
  attachArmed: boolean;
  attachFrom: string | null;
  /** The palette item armed for click-to-place. */
  placing: PaletteItem | null;
  /** Rule 6 (track 4): the bus a router's leg is being dragged over, the drop that would re-home it. */
  rehomeTarget: string | null;
  onContextMenu(e: React.MouseEvent, target: { kind: "host" | "router"; name: string } | { kind: "leg"; node: string; segment: string } | { kind: "segment"; name: string }): void;
}

export const EditContext = createContext<EditState | null>(null);

export function useEdit(): EditState | null {
  return useContext(EditContext);
}
