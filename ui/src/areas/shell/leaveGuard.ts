/**
 * Asks before unsaved work is left behind. The console runs under a plain
 * `BrowserRouter` (no data router, so no `useBlocker`); what a page can be left
 * by is a reload or close (`beforeunload`) or a click on one of the console's
 * own links -- the top menu, New design, Open's rows, the title link. A
 * capture-phase click listener on the document sees every such click before
 * React Router does, and stops it when the operator declines. The browser's
 * Back button is not guarded (a plain router cannot hold a popstate).
 */
import { useEffect, useRef } from "react";

/** A click that would navigate inside the console: a plain left click on a same-origin link. */
function inAppLink(e: MouseEvent): HTMLAnchorElement | null {
  if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return null;
  const a = (e.target as Element | null)?.closest?.("a[href]") as HTMLAnchorElement | null;
  if (!a || a.hasAttribute("download") || (a.target && a.target !== "_self")) return null;
  try {
    if (new URL(a.href, window.location.href).origin !== window.location.origin) return null;
  } catch {
    return null;
  }
  return a;
}

/**
 * While `active`, a reload or close asks the browser's own question, and an
 * in-app link asks `message`; `onLeave` runs when the operator agrees to go by
 * a link (the untitled design drops its browser copy then).
 */
export function useLeaveGuard(active: boolean, message: string, onLeave?: () => void): void {
  const latest = useRef({ message, onLeave });
  latest.current = { message, onLeave };
  useEffect(() => {
    if (!active) return;
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault();
    };
    const onClick = (e: MouseEvent) => {
      if (!inAppLink(e)) return;
      if (window.confirm(latest.current.message)) {
        latest.current.onLeave?.();
        return;
      }
      e.preventDefault();
      e.stopPropagation();
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    document.addEventListener("click", onClick, true);
    return () => {
      window.removeEventListener("beforeunload", onBeforeUnload);
      document.removeEventListener("click", onClick, true);
    };
  }, [active]);
}
