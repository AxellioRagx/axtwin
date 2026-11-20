/**
 * What the Add panel's popovers share (`docs/design/canvas-add-panel.md` §4.1, §5.2): opened at the
 * drop point and kept inside the canvas, and closed by Esc or a click outside -- which COMMIT (keep
 * the defaults), never cancel.
 */
import { useEffect, useLayoutEffect, useRef, useState, type RefObject } from "react";

export function usePopoverAt(at: { x: number; y: number }, commit: () => void): { box: RefObject<HTMLDivElement>; pos: { x: number; y: number } } {
  const box = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState(at);
  // Moved up / left when it would overflow the canvas.
  useLayoutEffect(() => {
    const el = box.current;
    const shell = el?.offsetParent as HTMLElement | null;
    if (!el || !shell) return;
    const top = Math.max(8, Math.min(at.y, shell.clientHeight - el.offsetHeight - 8));
    const left = Math.max(8, Math.min(at.x, shell.clientWidth - el.offsetWidth - 8));
    if (top !== pos.y || left !== pos.x) setPos({ x: left, y: top });
  });
  const commitRef = useRef(commit);
  commitRef.current = commit;
  useEffect(() => {
    const onDown = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) commitRef.current();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        commitRef.current();
      }
    };
    document.addEventListener("mousedown", onDown, true);
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("mousedown", onDown, true);
      document.removeEventListener("keydown", onKey, true);
    };
  }, []);
  return { box, pos };
}
