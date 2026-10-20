/**
 * Transient in-pane selection -- the state that is deliberately NOT in the URL
 * (`15` §7 keeps level/scope/filters in the route; this is the "which cell /
 * which drawer" a click opens in the inspector). Lifted to the shell so the
 * map and the outline set it and the inspector reads it.
 *
 * **S5.1.a -- selection drives editing.** Two more `Focus` kinds join the
 * matrix cell and the two O7 drawers, because "what is selected" is now what
 * decides which edit gestures the inspector offers
 * (`docs/WORK_ORDER_console_canvas.md` §3 S5.1.a): a segment selected in place,
 * and the "add a segment inside this site" a site `+` opens. They live HERE, in
 * the same one slot, rather than in a second store, so selecting a tile closes
 * an open matrix cell exactly as selecting another cell does -- one selection
 * at a time, and nothing to rehydrate.
 *
 * **Its lifetime is the route's IDENTITY -- the pathname -- and nothing else.**
 * A query flag changes how the page shows a place, never which place it is: a
 * view flag re-budgets the map, a dimming filter emphasizes a subset of it (no
 * filter hides a segment), and an apply's `?changed=1` marks what moved. Keyed
 * on any of them, the selection -- and whatever was typed under it -- vanished
 * the moment the operator opened a group, toggled a filter, or applied.
 *
 * A HOST is never an in-place selection: a chip, an infrastructure device and a
 * search hit all DRILL to the host's route, and the host the URL names is the
 * host selection (`resolveSelection`).
 */
export type Focus =
  | null
  /** A matrix cell -- the inspector shows its edge list (`15` §5). */
  | { kind: "cell"; row: string; col: string }
  /** The *unplaced* tile -- the inspector lists the local-but-unplaced
   *  entities (O7). */
  | { kind: "unplaced" }
  /** The Internet pill -- the inspector lists (a sample of) the non-local
   *  population (O7). */
  | { kind: "nonlocal" }
  /** S5.1.a: a segment selected IN PLACE -- a level-0 tile's select control,
   *  the level-1 spine, an added segment's outline row, or the segment a site
   *  `+` just staged -- without drilling into it. */
  | { kind: "segment"; cidr: string }
  /**
   * S5.1.a: the site `+` -- "add a segment inside this site". `siteKey` is
   * `null` for the outline root's site-less add. The prefix a suggestion is
   * allocated from is NOT stored here: it is the site's own
   * (`layout/sites.ts` `Site.prefix`), read when the form draws, so a stored
   * copy cannot disagree with the grouping.
   */
  | { kind: "site-add"; siteKey: string | null }
  /**
   * S5.1.b: a CLONE of the observed segment `source`, being placed or filled in
   * -- opened by an Alt-drag of its tile or by *Clone this segment…*. Its
   * position lives beside the selection in the workspace, keyed by THIS object,
   * never inside it: a new object is a new select gesture (keyboard focus moves),
   * and nudging a placement must not be one.
   */
  | { kind: "clone"; source: string };

/** The explicit kinds -- the ones a gesture puts in the slot for editing. */
export type ExplicitSelection = Extract<Focus, { kind: "segment" } | { kind: "site-add" } | { kind: "clone" }>;

/**
 * What the inspector is acting ON -- the subject S5.1.a's actions hang off: an
 * explicit selection, or the host the URL names. The matrix cell and the two O7
 * drawers are *detail* views, not edit subjects.
 */
export type Selection = ExplicitSelection | { kind: "host"; key: string };

/** The zoom shape this resolver reads. Structural rather than an import of
 *  `model/zoom.ts`'s `Zoom`, so this module stays a leaf both the views and a
 *  DOM-free test can call. */
export interface SelectionZoom {
  level: string;
  cidr?: string | undefined;
  key?: string | undefined;
}

/**
 * The selected element: an explicit in-pane selection when there is one,
 * otherwise **the element the URL is already pointing at**.
 *
 * The fallback is what makes "selection drives editing" true without a second
 * gesture for every path into the canvas: drilling into a segment -- from a
 * tile, the outline or search -- IS selecting it, and so is drilling into a
 * host from a chip, a device or search. A segment the R6 per-group budget
 * collapsed has no tile, but the outline and search list every segment, so it
 * reaches exactly the same actions. A SEGMENT-scoped matrix names its segment
 * too; the level-0 matrix is roles × roles and names none.
 *
 * `null` at the network level with nothing selected, and at the catalog.
 */
export function resolveSelection(
  zoom: SelectionZoom | null,
  focus: Focus,
): Selection | null {
  const explicit = selectionFromFocus(focus);
  if (explicit) return explicit;
  if (!zoom) return null;
  if (zoom.level === "host" && zoom.key) return { kind: "host", key: zoom.key };
  if ((zoom.level === "segment" || zoom.level === "matrix") && zoom.cidr) {
    return { kind: "segment", cidr: zoom.cidr };
  }
  return null;
}

/**
 * The EXPLICIT in-pane selection a `Focus` carries, or `null` for no focus and
 * for the detail kinds (a matrix cell, an O7 drawer).
 *
 * Returns the focus object ITSELF, never a copy: its identity is what tells the
 * inspector that a NEW select gesture happened (and so that keyboard focus
 * should move to what it opened), where an equal copy made on every render
 * would read as a gesture per keystroke.
 */
export function selectionFromFocus(focus: Focus): ExplicitSelection | null {
  if (focus && (focus.kind === "segment" || focus.kind === "site-add" || focus.kind === "clone")) return focus;
  return null;
}

/** Whether two selections name the same subject -- which is when a `clear`
 *  that drops the explicit one would change nothing on screen. */
export function sameSelection(a: Selection | null, b: Selection | null): boolean {
  if (a === null || b === null) return a === b;
  switch (a.kind) {
    case "segment":
      return b.kind === "segment" && a.cidr === b.cidr;
    case "host":
      return b.kind === "host" && a.key === b.key;
    case "site-add":
      return b.kind === "site-add" && a.siteKey === b.siteKey;
    case "clone":
      return b.kind === "clone" && a.source === b.source;
  }
}
