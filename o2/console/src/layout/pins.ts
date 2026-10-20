/**
 * Pins (`docs/design/15-console-canvas.md` §4) -- the S4 seam, built as a pure
 * function now so level 0 has a place to apply them and S4 only adds the drag
 * gesture and the `PUT .../layout` persistence.
 *
 * A pin moves a **segment tile** or a **whole site**; a host is never pinnable
 * (`15` §4, and the API refuses a host key). An unpinned element keeps its
 * computed slot, so *reset pins* is just "call the layout with no pins" -- the
 * property that let the deterministic layout win over a fully-pinned one.
 *
 * S3 keeps this minimal on purpose: the golden and locality tests run the
 * layout with **no** pins (the pure function of identity), and the pin
 * translation is exercised by its own small test here. Nothing above calls it
 * with a non-empty set until S4.
 */
import type { Box, Point } from "./geometry";

/** `site:<prefix>` / `segment:<cidr>` → an absolute top-left. Host keys are
 *  not representable (`15` §4). */
export type Pins = Readonly<Record<string, { x: number; y: number }>>;

export const NO_PINS: Pins = Object.freeze({});

export interface Pinnable {
  /** The pin key: `site:<prefix>` for a site column, `segment:<observedCidr>`
   *  for a tile. */
  pinKey: string;
  box: Box;
}

/**
 * One pin this session actually WROTE: the key, and the coordinates we sent.
 *
 * The VALUE is the load-bearing half (R2 final review S1). A key alone says
 * "we re-placed A at some point", which stops being true the moment the
 * document underneath is replaced -- a 412 rolls our save back and
 * `reloadLayout()` swaps in the other writer's coordinates for the very same
 * key. Recording what we wrote lets the stamp rule ask the only question that
 * survives that: *is the pin on screen still the one we put there?*
 */
export interface AuthoredPin {
  key: string;
  at: Point;
}

/** Translate one element by a delta -- the primitive a site pin uses to move
 *  its header and every member tile together. */
export function translateBox(box: Box, dx: number, dy: number): Box {
  return { x: box.x + dx, y: box.y + dy, w: box.w, h: box.h };
}

/**
 * Move a pinned element's box to its pinned top-left, returning the delta so a
 * caller can move the element's children (a site's tiles) by the same amount.
 * `null` when the key is not pinned -- the element keeps its computed slot.
 */
export function pinDelta(pinKey: string, box: Box, pins: Pins): { dx: number; dy: number } | null {
  const pin = pins[pinKey];
  if (!pin) return null;
  return { dx: pin.x - box.x, dy: pin.y - box.y };
}
