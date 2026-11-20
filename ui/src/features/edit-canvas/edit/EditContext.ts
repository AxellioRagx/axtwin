/**
 * What the canvas's nodes and edges read in edit mode without the node
 * array being rebuilt: the working model (for a chip's count badge and
 * pinned address), whether the canvas is locked, the armed attach gesture,
 * and the handlers a node's own controls call. `null` in Phase 1's
 * read-only use.
 */
import { createContext, useContext } from "react";

import type { CardId, Os, ToolId } from "../add/catalog";
import type { DocModel } from "./document";

export type PaletteKind = "device" | "tool" | "template";

/** An Add-panel item (`docs/design/canvas-add-panel.md` §3) as it is dragged, clicked or quick-added:
 *  a device card (with the role and OS a search preselected), a tool, or a template. */
export interface PaletteItem {
  kind: PaletteKind;
  /** The card id, the tool id or the template id. */
  id: string;
  label: string;
  card?: CardId;
  /** A search hit's preselected role (a persona id). */
  role?: string | null;
  os?: Os | null;
  tool?: ToolId;
  count?: number;
}

export interface EditState {
  model: DocModel;
  locked: boolean;
  /** The attach gesture is armed: the next host click then segment click attaches (Esc disarms). */
  attachArmed: boolean;
  attachFrom: string | null;
  /** Rule 6 (track 4): the bus a router's leg is being dragged over, the drop that would re-home it. */
  rehomeTarget: string | null;
  onContextMenu(e: React.MouseEvent, target: { kind: "host" | "router"; name: string } | { kind: "leg"; node: string; segment: string } | { kind: "segment"; name: string }): void;
  /** Members (topology-layers §5): the `×N` groups whose chips are open, a badge's double-click,
   *  a chip's click (the member panel) and a chip dropped on another group (a move of one). */
  membersOpen: ReadonlySet<string>;
  onToggleMembers(name: string): void;
  onSelectMember(name: string, index: number): void;
  onMoveMember(from: string, index: number, into: string): void;
}

export const EditContext = createContext<EditState | null>(null);

export function useEdit(): EditState | null {
  return useContext(EditContext);
}
