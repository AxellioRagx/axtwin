/**
 * The S4 drag gesture (`docs/design/15-console-canvas.md` §4,
 * `docs/WORK_ORDER_console_canvas.md` §3 S4). A pointer-drag on a segment tile
 * or a site header repositions it and, on release, commits a `Pins` entry that
 * `PUT .../layout` persists.
 *
 * **The DOM lives here; the pin math stays pure.** This hook only turns a
 * screen delta into a LAYOUT-coordinate position and hands the resulting
 * absolute top-left to `layout/pins.ts` through the `effectivePins` it feeds
 * back into `layout0`. It computes no geometry of its own -- a pinned element's
 * placement is `applyPins`' job (`layout/level0.ts`), so `layout/` knows
 * nothing about React or the DOM (`o2/console/README.md`'s boundary).
 *
 * A pin stores an ABSOLUTE top-left (`applyPins` lands the element exactly
 * there), so a drag is a pure translation: `newTopLeft = grabbedTopLeft +
 * scale * (pointerNow - pointerAtGrab)`. Two properties are load-bearing and
 * were review should-fixes:
 *
 *  - **S2 (drift).** The client->layout `scale` is CAPTURED ONCE at `begin` and
 *    reused for every delta. Re-reading a live CTM mid-drag drifts, because the
 *    level-0 viewBox is `max-width:100%`-scaled and Level0 freezes it to the
 *    committed pins during a drag anyway -- a delta read under a changed CTM
 *    would inflate and the tile would accelerate away from the cursor.
 *  - **S1 (stale suppressor).** A drag uses `setPointerCapture`, so the
 *    compatibility `click` after `pointerup` is dispatched to the capturing
 *    `<svg>` (no tile `onClick`) in several browsers -- the drag's own trailing
 *    click never reaches `consumeDragClick`. Suppression is therefore bounded
 *    by TIME (`isSuppressedClick`), never by "the expected click arrived",
 *    which would leave the flag armed to eat a LATER genuine drill.
 *
 * **S5.1.b: an Alt-drag of a TILE is a clone, not a pin.** The same gesture
 * machinery -- scale captured once, a time-bounded click suppressor -- but the
 * source tile does not move: a ghost follows the pointer (`cloneGhost`), and on
 * release the drop position is reported (`onCloneDrop`) for the workspace to
 * open the clone form at. Nothing is pinned by the drag itself; the clone's pin
 * is committed when its batch is staged, under the key its CIDR then has.
 *
 * **No text selection DURING a gesture -- and only during one.** In a real
 * browser a press-and-move on a tile's SVG text also starts a text selection
 * (measured: 364 characters swept across tile labels by one pin drag, and by an
 * Alt-drag). The selection is the default action of the PRESS, so this hook
 * cancels `selectstart` on the plane while a gesture it began is active, and
 * lets it through otherwise: a selection started anywhere else (empty map
 * space, the panes, a read-only render with no drag) still works. It
 * deliberately does NOT `preventDefault` the `pointerdown`: that would also
 * suppress the compatibility mouse events a press carries, focus-on-press among
 * them, and the pinnable groups are keyboard controls that must still take it.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent, RefObject } from "react";

import type { Point } from "../layout/geometry";
import { NO_PINS, type AuthoredPin, type Pins } from "../layout/pins";

/** Below this client-pixel move a pointerdown->up is a CLICK (drill), not a
 *  drag (pin) -- so a plain click still navigates. */
const CLICK_MOVE_THRESHOLD_PX = 4;

/** How long after a drag-commit a click on the SAME element is treated as the
 *  drag's own trailing click and swallowed (S1). Comfortably covers the
 *  synthetic click a browser fires after `pointerup`, well under a deliberate
 *  second click. */
const SUPPRESS_WINDOW_MS = 250;

/** A finished drag-commit, armed for the trailing click's suppression window. */
export interface DragCommit {
  key: string;
  at: number;
}

export interface PinController {
  /** The saved pins with the in-progress drag merged in, fed to `layout0` so
   *  the dragged element tracks the cursor live. `savedPins` verbatim when no
   *  drag is active -- and `NO_PINS` all the way to the pure layout when there
   *  are none, which is what keeps the no-pins render byte-identical. */
  effectivePins: Pins;
  /** The key currently being dragged, for a grabbed-cursor / raised style. */
  draggedKey: string | null;
  /** S5.1.b: the ghost of a clone being Alt-dragged off `source` (a tile's pin
   *  key) -- where its top-left is right now -- or `null`. */
  cloneGhost: { source: string; at: Point } | null;
  /** Start a drag from a pinnable element's pointerdown. `origin` is the
   *  element's CURRENTLY displayed top-left (post-pin). A no-op with no writer
   *  (a read-only render) or a non-primary button. With `clonable` and the Alt
   *  key held -- and a clone handler -- the drag is a CLONE (S5.1.b). */
  begin: (pinKey: string, origin: Point, event: ReactPointerEvent, clonable?: boolean) => void;
  /** True (once) when a click on this key falls inside the post-drag
   *  suppression window, so the click that follows a drag does not also drill. */
  consumeDragClick: (pinKey: string) => boolean;
  /** Spread onto the level-0 `<svg>`: the move/up/cancel handlers for the
   *  captured pointer. Kept on the SVG (not `window`) via pointer capture, so
   *  React owns the listener lifetime. */
  svgProps: {
    onPointerMove: (event: ReactPointerEvent) => void;
    onPointerUp: (event: ReactPointerEvent) => void;
    onPointerCancel: (event: ReactPointerEvent) => void;
  };
}

interface ActiveDrag {
  pinKey: string;
  pointerId: number;
  /** The element's displayed top-left at grab (layout coords). */
  origin: Point;
  downClientX: number;
  downClientY: number;
  /** The client->layout scale captured at `begin` and used for EVERY delta, so
   *  a viewBox that changes mid-gesture cannot rescale the drag (S2). */
  scale: { sx: number; sy: number };
  moved: boolean;
  /** `pin` moves the element; `clone` leaves it and drags a ghost (S5.1.b). */
  mode: "pin" | "clone";
}

function nowMs(): number {
  return typeof performance !== "undefined" && typeof performance.now === "function"
    ? performance.now()
    : Date.now();
}

/**
 * The client->layout scale (layout units per client pixel), captured once at
 * `begin`. Prefers the exact CTM (`1/a`, `1/d`); falls back to the
 * bounding-rect ÷ viewBox ratio when no CTM is available (jsdom, or an SVG not
 * yet laid out); identity as a last resort so a drag degrades rather than
 * throwing.
 */
export function layoutScaleOf(svg: SVGSVGElement): { sx: number; sy: number } {
  const ctm = svg.getScreenCTM?.();
  if (ctm && ctm.a !== 0 && ctm.d !== 0) {
    return { sx: 1 / ctm.a, sy: 1 / ctm.d };
  }
  const rect = svg.getBoundingClientRect();
  const vb = svg.viewBox?.baseVal;
  if (vb && rect.width > 0 && rect.height > 0) {
    return { sx: vb.width / rect.width, sy: vb.height / rect.height };
  }
  return { sx: 1, sy: 1 };
}

/**
 * A client point in the SVG's LAYOUT coordinates -- where a tap or a click on
 * the map lands in the plane the layout draws in (S5.1.b's placement). The
 * same chain as `layoutScaleOf`: the exact screen CTM inverted (the full affine
 * inverse, so a translated SVG maps too), else the bounding rect against the
 * viewBox, else a 1:1 map from the rect's corner -- so it degrades rather than
 * throwing where there is no layout at all (jsdom).
 */
export function layoutPointOf(svg: SVGSVGElement, clientX: number, clientY: number): Point {
  const ctm = svg.getScreenCTM?.();
  if (ctm) {
    const det = ctm.a * ctm.d - ctm.b * ctm.c;
    if (det !== 0) {
      const dx = clientX - ctm.e;
      const dy = clientY - ctm.f;
      return { x: (ctm.d * dx - ctm.c * dy) / det, y: (ctm.a * dy - ctm.b * dx) / det };
    }
  }
  const rect = svg.getBoundingClientRect();
  const vb = viewBoxOf(svg);
  if (vb && rect.width > 0 && rect.height > 0) {
    return {
      x: vb.x + ((clientX - rect.left) * vb.width) / rect.width,
      y: vb.y + ((clientY - rect.top) * vb.height) / rect.height,
    };
  }
  return { x: (vb?.x ?? 0) + (clientX - rect.left), y: (vb?.y ?? 0) + (clientY - rect.top) };
}

/** The SVG's viewBox: the live `baseVal` where the DOM has one, else the
 *  attribute parsed (jsdom implements no SVG geometry). */
function viewBoxOf(svg: SVGSVGElement): { x: number; y: number; width: number; height: number } | null {
  const base = svg.viewBox?.baseVal;
  if (base) return { x: base.x, y: base.y, width: base.width, height: base.height };
  const parts = (svg.getAttribute?.("viewBox") ?? "").trim().split(/[\s,]+/).map(Number);
  if (parts.length !== 4 || parts.some((n) => !Number.isFinite(n))) return null;
  return { x: parts[0]!, y: parts[1]!, width: parts[2]!, height: parts[3]! };
}

/**
 * The dragged element's absolute layout top-left for a pointer at `client`,
 * from the grabbed top-left and the scale CAPTURED at begin (never a live CTM
 * a growing viewBox would change mid-gesture -- the S2 drift fix). Pure and
 * exported so the "delta stays 1:1 in layout units under a non-identity scale"
 * property is unit-tested directly.
 */
export function draggedTopLeft(
  origin: Point,
  down: { x: number; y: number },
  client: { x: number; y: number },
  scale: { sx: number; sy: number },
): Point {
  return {
    x: origin.x + (client.x - down.x) * scale.sx,
    y: origin.y + (client.y - down.y) * scale.sy,
  };
}

/**
 * Pure: is a click on `pinKey` inside the post-drag suppression window? Bounded
 * by TIME, not by "the expected follow-up click arrived" -- pointer capture may
 * route the trailing click to the `<svg>`, so a match-and-clear scheme would
 * leave the flag armed to eat a later legitimate drill (S1).
 */
export function isSuppressedClick(commit: DragCommit | null, pinKey: string, now: number): boolean {
  return commit !== null && commit.key === pinKey && now - commit.at < SUPPRESS_WINDOW_MS;
}

export function usePinDrag(args: {
  svgRef: RefObject<SVGSVGElement | null>;
  savedPins: Pins;
  /**
   * Persist the whole pin set. The second argument is **the one pin this
   * gesture wrote**, by key AND value (R2/M1): a commit writes the WHOLE set,
   * so the writer cannot otherwise tell a set the operator re-placed against
   * the plane on screen from one it merely inherited. Without it, one drag on
   * a stale set re-stamps all N pins as current and retires the warning for
   * ever, with N-1 of them still in the wrong place (LES-289).
   *
   * **This hook reports; it does not accumulate.** The session's authored-pin
   * map lives in the workspace beside the layout document it describes —
   * `Level0` unmounts on a drill to level 1, so session state kept here would
   * silently lose an operator's progress toward "re-place every one".
   */
  onCommit?: (pins: Pins, authored: AuthoredPin) => void;
  /** S5.1.b: an Alt-drag of a clonable tile released here -- `source` is the
   *  tile's pin key, `at` the ghost's top-left. Absent: an Alt-drag is a pin. */
  onCloneDrop?: (source: string, at: Point) => void;
}): PinController {
  const { svgRef, savedPins, onCommit, onCloneDrop } = args;
  const activeRef = useRef<ActiveDrag | null>(null);
  const commitRef = useRef<DragCommit | null>(null);
  const [draggedKey, setDraggedKey] = useState<string | null>(null);
  const [livePin, setLivePin] = useState<Point | null>(null);
  const [cloneGhost, setCloneGhost] = useState<{ source: string; at: Point } | null>(null);

  // Cancel a text selection that STARTS while a gesture is active (see the
  // module note). A native listener -- React has no `onSelectStart` -- on the
  // document, where `selectstart` bubbles to from the pinnable the press landed
  // on. The document rather than the `<svg>` so the listener never depends on
  // the ref holding a DOM node yet; the SCOPE is `activeRef`, which `begin` sets
  // on the press (before the browser's default action fires the event) and the
  // gesture's end clears, so nothing outside a gesture is ever cancelled.
  useEffect(() => {
    if (typeof document === "undefined") return;
    const onSelectStart = (event: Event): void => {
      if (activeRef.current !== null) event.preventDefault();
    };
    document.addEventListener("selectstart", onSelectStart);
    return () => document.removeEventListener("selectstart", onSelectStart);
  }, []);

  const begin = useCallback(
    (pinKey: string, origin: Point, event: ReactPointerEvent, clonable?: boolean) => {
      const clone = clonable === true && event.altKey === true && !!onCloneDrop;
      if (!clone && !onCommit) return; // read-only render: no drag
      if (event.button !== 0) return; // primary pointer only (skip right/middle)
      const svg = svgRef.current;
      if (!svg) return;
      activeRef.current = {
        pinKey,
        pointerId: event.pointerId,
        origin,
        downClientX: event.clientX,
        downClientY: event.clientY,
        scale: layoutScaleOf(svg), // S2: capture the scale ONCE, reuse per delta
        moved: false,
        mode: clone ? "clone" : "pin",
      };
      try {
        svg.setPointerCapture(event.pointerId);
      } catch {
        // jsdom / no pointer-capture support -- the drag still works via the
        // SVG's own move/up handlers.
      }
      if (clone) {
        // The source stays where it is; only the ghost follows the pointer.
        setCloneGhost({ source: pinKey, at: origin });
        return;
      }
      setDraggedKey(pinKey);
      setLivePin(origin);
    },
    [svgRef, onCommit, onCloneDrop],
  );

  const onPointerMove = useCallback((event: ReactPointerEvent) => {
    const drag = activeRef.current;
    if (!drag || event.pointerId !== drag.pointerId) return;
    if (
      !drag.moved &&
      Math.hypot(event.clientX - drag.downClientX, event.clientY - drag.downClientY) >
        CLICK_MOVE_THRESHOLD_PX
    ) {
      drag.moved = true;
    }
    const at = draggedTopLeft(
      drag.origin,
      { x: drag.downClientX, y: drag.downClientY },
      { x: event.clientX, y: event.clientY },
      drag.scale,
    );
    if (drag.mode === "clone") setCloneGhost({ source: drag.pinKey, at });
    else setLivePin(at);
  }, []);

  const end = useCallback(
    (event: ReactPointerEvent, commit: boolean) => {
      const drag = activeRef.current;
      if (!drag || event.pointerId !== drag.pointerId) return;
      const svg = svgRef.current;
      try {
        svg?.releasePointerCapture(event.pointerId);
      } catch {
        // nothing captured (jsdom) -- ignore
      }
      const pos = draggedTopLeft(
        drag.origin,
        { x: drag.downClientX, y: drag.downClientY },
        { x: event.clientX, y: event.clientY },
        drag.scale,
      );
      if (commit && drag.moved && drag.mode === "clone" && onCloneDrop) {
        // Nothing is pinned here: the clone has no CIDR yet, so no key. The
        // workspace opens the clone form at `pos` and pins it on Stage.
        onCloneDrop(drag.pinKey, pos);
        commitRef.current = { key: drag.pinKey, at: nowMs() };
      } else if (commit && drag.moved && drag.mode === "pin" && onCommit) {
        // What we wrote, by key AND value: the workspace records it and the
        // stamp rule later asks whether the pin on screen is still ours.
        onCommit({ ...savedPins, [drag.pinKey]: pos }, { key: drag.pinKey, at: pos });
        // Arm the TIME-bounded suppression window for the trailing click (S1).
        commitRef.current = { key: drag.pinKey, at: nowMs() };
      }
      activeRef.current = null;
      setDraggedKey(null);
      setLivePin(null);
      setCloneGhost(null);
    },
    [svgRef, onCommit, onCloneDrop, savedPins],
  );

  const onPointerUp = useCallback((event: ReactPointerEvent) => end(event, true), [end]);
  const onPointerCancel = useCallback((event: ReactPointerEvent) => end(event, false), [end]);

  const consumeDragClick = useCallback((pinKey: string) => {
    const hit = isSuppressedClick(commitRef.current, pinKey, nowMs());
    if (hit) commitRef.current = null;
    return hit;
  }, []);

  const effectivePins = useMemo<Pins>(() => {
    if (draggedKey && livePin) return { ...savedPins, [draggedKey]: livePin };
    return savedPins ?? NO_PINS;
  }, [savedPins, draggedKey, livePin]);

  return {
    effectivePins,
    draggedKey,
    cloneGhost,
    begin,
    consumeDragClick,
    svgProps: { onPointerMove, onPointerUp, onPointerCancel },
  };
}
