/**
 * The app's theme as React Flow's `colorMode`. `theme/app.css` follows the
 * OS unless `<html data-theme="light|dark">` says otherwise; this hook reads
 * the same two sources and follows both as they change.
 */
import { useEffect, useState } from "react";

export type AppColorMode = "light" | "dark";

function read(): AppColorMode {
  if (typeof document === "undefined") return "light";
  const forced = document.documentElement.dataset["theme"];
  if (forced === "dark" || forced === "light") return forced;
  const mq = typeof window.matchMedia === "function" ? window.matchMedia("(prefers-color-scheme: dark)") : null;
  return mq?.matches ? "dark" : "light";
}

export function useAppColorMode(): AppColorMode {
  const [mode, setMode] = useState<AppColorMode>(read);
  useEffect(() => {
    const update = () => setMode(read());
    const observer = typeof MutationObserver === "function" ? new MutationObserver(update) : null;
    observer?.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    const mq = typeof window.matchMedia === "function" ? window.matchMedia("(prefers-color-scheme: dark)") : null;
    mq?.addEventListener?.("change", update);
    return () => {
      observer?.disconnect();
      mq?.removeEventListener?.("change", update);
    };
  }, []);
  return mode;
}
