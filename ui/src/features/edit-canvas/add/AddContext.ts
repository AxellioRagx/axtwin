/**
 * What the Add panel reaches on the canvas it is shown over (`docs/design/canvas-add-panel.md` §3):
 * a card's click lands on the selected segment, else at the centre of the view -- which only the
 * canvas knows, so `Canvas.tsx` provides it. `null` outside edit mode.
 */
import { createContext, useContext } from "react";

import type { PaletteItem } from "../edit/EditContext";

export const PALETTE_MIME = "application/x-axr-palette";

export interface AddTarget {
  addHere(item: PaletteItem): void;
}

export const AddContext = createContext<AddTarget | null>(null);

export function useAddTarget(): AddTarget | null {
  return useContext(AddContext);
}
