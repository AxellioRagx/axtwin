import { createContext, useContext } from "react";

import type { Whoami } from "./types";

/** Not asked yet, or the API has no `/api/whoami` (an older server): the
 *  console assumes nothing and lets each write find out. */
export const UNKNOWN_WHOAMI: Whoami = { user: null, can_write: true, mode: "", signin_url: "" };

export const WhoamiContext = createContext<Whoami>(UNKNOWN_WHOAMI);

export function useWhoami(): Whoami {
  return useContext(WhoamiContext);
}

/**
 * The sign-in link for a 401. `AXRANGE_SIGNIN_URL` ends in `rd=` (or `?`)
 * when the sign-in flow wants the page to come back to: this page is
 * appended. Empty -> null: the message stands alone.
 */
export function signinHref(signinUrl: string, here: string = typeof window === "undefined" ? "" : window.location.href): string | null {
  const url = signinUrl.trim();
  if (!url) return null;
  if (url.endsWith("=") || url.endsWith("?")) return url + encodeURIComponent(here);
  return url;
}
