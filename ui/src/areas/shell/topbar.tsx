import { createContext, useContext, type ReactNode } from "react";
import { createPortal } from "react-dom";

/** The top bar's middle: a page that is the window (the canvas) puts its
 *  title, pills and exits here, so nothing twin-specific sits below the bar
 *  (`features/edit-canvas/SPEC.md` "Shell and chrome"). `AppFrame` owns the
 *  element; a page renders into it through `TopbarPortal`. */
export const TopbarSlotContext = createContext<HTMLElement | null>(null);

export function TopbarPortal({ children }: { children: ReactNode }) {
  const slot = useContext(TopbarSlotContext);
  return slot ? createPortal(children, slot) : null;
}
