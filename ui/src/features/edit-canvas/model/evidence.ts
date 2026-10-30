// Lifted verbatim from the frozen `o2/console/src/model/evidence.ts` (2026-10-30);
// pure, no React, no DOM. Import paths adapted; nothing imports `o2/console` at runtime.
/**
 * The six evidence classes, confidence, and the ghost rule.
 *
 * `docs/design/14-operator-assertions.md` §2 is the source: six classes in
 * **precedence order**, where `defaulted` was renamed `compiler` and
 * `referenced` was split from out-of-band `imported`. Precedence is not
 * cosmetic -- it is what decides which class wins a fact and therefore what
 * colour an element carries, and the losing class is published beside it
 * (`overrides`) rather than erased.
 */

/** Highest precedence first. Index IS the precedence. */
export const EVIDENCE_CLASSES = [
  "asserted",
  "imported",
  "observed",
  "referenced",
  "imputed",
  "compiler",
] as const;

export type EvidenceClass = (typeof EVIDENCE_CLASSES)[number];

export function isEvidenceClass(value: string): value is EvidenceClass {
  return (EVIDENCE_CLASSES as readonly string[]).includes(value);
}

/** 0 = highest. `null` for a class this build does not know -- an unknown
 *  class is reported, never rewritten to something familiar. */
export function precedenceOf(value: string): number | null {
  const at = (EVIDENCE_CLASSES as readonly string[]).indexOf(value);
  return at < 0 ? null : at;
}

/** `true` when `a` wins over `b`. An unknown class never wins. */
export function outranks(a: string, b: string): boolean {
  const pa = precedenceOf(a);
  const pb = precedenceOf(b);
  if (pa === null) return false;
  if (pb === null) return true;
  return pa < pb;
}

/** The CSS class for an element's evidence fill (`theme/tokens.css`'s
 *  `.ev-<class>`). `null`/unknown maps to `ev-none`, the neutral swatch --
 *  the render half of "absent is not a class" (never recolour it as one of
 *  the six). */
export function evidenceClassName(value: string | null | undefined): string {
  return value && isEvidenceClass(value) ? `ev-${value}` : "ev-none";
}

/**
 * **`compiler` is never authorable.** It is the sidecar's own word for "this
 * value is a compiler choice, not a measurement" (a deterministic seed, a
 * catalog default), so no operator gesture may produce it: the canvas writes
 * `asserted` records and nothing else. A UI that offered `compiler` as an
 * authoring option would be offering to forge the compiler's signature.
 *
 * The other five are not authorable either *through this canvas* -- an
 * assertion is always `asserted` (`14` §2) -- but they are legitimate values
 * a producer may stamp, which `compiler` is too; the distinction this
 * predicate draws is the one the editor needs at the point of writing.
 */
export function isAuthorable(value: string): boolean {
  return value === "asserted";
}

// ---------------------------------------------------------------------------
// Confidence -> saturation (`15` §6: "Saturation is confidence")
// ---------------------------------------------------------------------------
/** The floor a KNOWN-but-low confidence renders at, so a real measurement
 *  never fades to invisible and gets mistaken for an absent one. */
export const SATURATION_FLOOR = 0.35;

/**
 * `null` confidence is ABSENT, not zero (the house rule this API is built on),
 * so it renders at full saturation with the *absence* carried by the class
 * and the inspector -- fading it would state a low confidence nobody
 * measured. A canonical `compiler` row has no confidence by definition.
 */
export function saturationFor(confidence: number | null | undefined): number {
  if (confidence === null || confidence === undefined || Number.isNaN(confidence)) {
    return 1;
  }
  const clamped = Math.min(1, Math.max(0, confidence));
  return SATURATION_FLOOR + (1 - SATURATION_FLOOR) * clamped;
}

// ---------------------------------------------------------------------------
// Ghosts (`15` §6, `13` §3.4) -- a STUB with its inputs named
// ---------------------------------------------------------------------------
export const GHOST_REASONS = [
  "suppressed",
  "below-threshold",
  "dropped-by-cast",
  "referenced-unseen",
] as const;

export type GhostReason = (typeof GHOST_REASONS)[number];

export interface GhostInput {
  /** An entity's own occupancy, `null` when the bundle publishes none. */
  occupancy?: number | null;
  /** `CastSummary.deploy_min_occupancy` -- the EFFECTIVE gate threshold. */
  threshold: number;
  /** The operator suppressed this key (saved or applied). */
  suppressed?: boolean;
  /** The cast emitted no node for it, for a reason other than the gate. */
  droppedByCast?: boolean;
  /** Named by a reference channel but never seen on the wire. */
  referencedUnseen?: boolean;
}

/**
 * Every reason that applies, in the order the UI should name them: an
 * operator's own act first (it is the one they can undo), then the gate, then
 * the compiler, then the reference channel. An entity can be a ghost for more
 * than one reason and collapsing them to a boolean loses the actionable one
 * (LES-220: a "below threshold" label sourced from the wrong mechanism is a
 * lie a reader cannot see).
 *
 * **Stub, deliberately:** `droppedByCast` has no publisher yet -- a cast node
 * spec carries no member keys (`docs/WORK_ORDER_console_canvas.md` §2), so
 * "the cast dropped THIS entity" is not computable from the bundle today and
 * S3 must feed it from the population accounting or leave it unset. Unset is
 * absent, not false-and-therefore-fine.
 */
export function ghostReasons(input: GhostInput): GhostReason[] {
  const reasons: GhostReason[] = [];
  if (input.suppressed) reasons.push("suppressed");
  if (
    input.occupancy !== null &&
    input.occupancy !== undefined &&
    input.occupancy < input.threshold
  ) {
    reasons.push("below-threshold");
  }
  if (input.droppedByCast) reasons.push("dropped-by-cast");
  if (input.referencedUnseen) reasons.push("referenced-unseen");
  return reasons;
}

export function isGhost(input: GhostInput): boolean {
  return ghostReasons(input).length > 0;
}

// ---------------------------------------------------------------------------
// Counting a population by class
// ---------------------------------------------------------------------------
export interface ClassCounts {
  /** One entry per known class, in precedence order, zero-filled. */
  known: Array<{ evidenceClass: EvidenceClass; count: number }>;
  /**
   * Classes the producer used that this build does not know -- reported as
   * written, never folded into a familiar one or silently dropped (the same
   * rule `precedenceOf` follows).
   */
  unknown: Array<{ evidenceClass: string; count: number }>;
  total: number;
}

/** Tally any evidence-classed rows. Pure, so the view does not do arithmetic
 *  and the arithmetic can be tested without a DOM. */
export function countByClass(
  rows: ReadonlyArray<{ evidence_class: string }>,
): ClassCounts {
  const counts = new Map<string, number>();
  for (const row of rows) {
    counts.set(row.evidence_class, (counts.get(row.evidence_class) ?? 0) + 1);
  }
  const known = EVIDENCE_CLASSES.map((evidenceClass) => ({
    evidenceClass,
    count: counts.get(evidenceClass) ?? 0,
  }));
  const unknown = [...counts.entries()]
    .filter(([cls]) => !isEvidenceClass(cls))
    .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
    .map(([evidenceClass, count]) => ({ evidenceClass, count }));
  return { known, unknown, total: rows.length };
}
