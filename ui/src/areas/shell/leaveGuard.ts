/**
 * Asks before unsaved work is left behind. The console runs under a plain
 * `BrowserRouter` (no data router, so no `useBlocker`); what a page can be left
 * by is a reload or close (`beforeunload`), a click on one of the console's
 * own links -- the top menu, New design, the logo, Open's rows, the title link
 * -- or the browser's Back button. A capture-phase click listener on the
 * document sees every such click before React Router does and holds it; Back
 * is caught by a sentinel history entry pushed while the guard is active (a
 * plain router cannot cancel a popstate, so the sentinel absorbs it and the
 * question is asked over the page that is still there).
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

/** How the page would be left: by an in-app link (to `href`), or by the browser's Back. */
export type LeaveVia = { kind: "link"; href: string } | { kind: "back" };

/** Asks the operator; calls `proceed()` (once) when the page is to be left, nothing to stay. */
export type LeaveAsk = (via: LeaveVia, proceed: () => void) => void;

const SENTINEL = "axr-leave-guard";

/**
 * While `active`, a reload or close asks the browser's own question, and an
 * in-app link or Back asks: through `ask` when given (the page's own dialog),
 * else `window.confirm(message)`. `onLeave` runs when the operator agrees to go
 * (the untitled design drops its browser copy then); with `ask`, the page's
 * dialog decides what leaving means and `onLeave` runs only on the confirm path.
 */
export function useLeaveGuard(active: boolean, message: string, onLeave?: () => void, ask?: LeaveAsk): void {
  const latest = useRef({ message, onLeave, ask });
  latest.current = { message, onLeave, ask };
  useEffect(() => {
    if (!active) return;
    let released = false;
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault();
    };
    const go = (a: HTMLAnchorElement) => {
      released = true;
      a.click();
    };
    const onClick = (e: MouseEvent) => {
      if (released) return;
      const a = inAppLink(e);
      if (!a) return;
      const { ask: asker, message: msg, onLeave: leave } = latest.current;
      if (!asker) {
        if (window.confirm(msg)) {
          leave?.();
          return;
        }
        e.preventDefault();
        e.stopPropagation();
        return;
      }
      e.preventDefault();
      e.stopPropagation();
      asker({ kind: "link", href: a.getAttribute("href") ?? a.href }, () => go(a));
    };
    // Back: a sentinel entry over the current one; popping it lands on the same URL, so the router
    // does not move, and the question is asked. Leaving goes back past the real entry.
    const pushed = !(window.history.state && (window.history.state as Record<string, unknown>)[SENTINEL]);
    if (pushed) window.history.pushState({ ...(window.history.state ?? {}), [SENTINEL]: true }, "", window.location.href);
    const onPop = () => {
      if (released) return;
      const { ask: asker, message: msg, onLeave: leave } = latest.current;
      const leaveBack = () => {
        released = true;
        window.history.back();
      };
      if (!asker) {
        if (window.confirm(msg)) {
          leave?.();
          leaveBack();
        } else window.history.pushState({ ...(window.history.state ?? {}), [SENTINEL]: true }, "", window.location.href);
        return;
      }
      window.history.pushState({ ...(window.history.state ?? {}), [SENTINEL]: true }, "", window.location.href);
      asker({ kind: "back" }, () => {
        released = true;
        window.history.go(-2);
      });
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    window.addEventListener("popstate", onPop);
    document.addEventListener("click", onClick, true);
    return () => {
      window.removeEventListener("beforeunload", onBeforeUnload);
      window.removeEventListener("popstate", onPop);
      document.removeEventListener("click", onClick, true);
      // The work was saved (or let go) in place: drop the sentinel so one Back is one step again.
      if (!released && window.history.state && (window.history.state as Record<string, unknown>)[SENTINEL]) {
        const { [SENTINEL]: _drop, ...rest } = window.history.state as Record<string, unknown>;
        window.history.replaceState(Object.keys(rest).length ? rest : null, "", window.location.href);
      }
    };
  }, [active]);
}
