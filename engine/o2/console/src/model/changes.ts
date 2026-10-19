/**
 * The post-apply change set: what an apply's diff reports as moved, reduced to
 * the keys the MAP is drawn from so the S6 highlight (docs/design/15-console-
 * canvas.md section 12, O5) can mark exactly those elements.
 *
 * Pure -- no React, no fetch, no clock. The workspace fetches `GET .../diff`
 * once per apply and hands the response here; the views take the `ChangeSet`
 * and add a highlight class to any element whose key is in it.
 *
 * **Why the OBSERVED side, and `suppressed_keys`, but not `twin`.** The level
 * 0/1/2 map is keyed on the OBSERVED identity -- a tile is its `observedCidr`,
 * a host is its observed entity id (`model/graph.ts`). The diff's `twin.*`
 * sections are keyed on the twin's own minted ids (the relocated CIDR, the
 * cast host id), which name nothing this map draws, so folding them in would
 * add keys that can never match an element -- noise, not signal. What DOES
 * align: `observed.segments` (a CIDR) with a tile, and `observed.nodes` plus
 * `suppressed_keys` (an AUTHORED key, which IS the observed entity id -- see
 * `GraphNode.id`) with a host. A `suppress` moves only the twin side and the
 * suppressed-key set, so `suppressed_keys` is the one channel that lets the
 * map show the change the Console's lead edit produces.
 */
import type { DiffSection, ImprintDiff } from "../api/types";

export interface ChangeSet {
  /** Observed segment CIDRs that moved -- match a tile's `observedCidr`. */
  segments: ReadonlySet<string>;
  /** Observed node ids / authored keys that moved -- match a `GraphNode.id`. */
  nodes: ReadonlySet<string>;
}

export const EMPTY_CHANGE_SET: ChangeSet = {
  segments: new Set<string>(),
  nodes: new Set<string>(),
};

function collect(section: DiffSection | null | undefined, into: Set<string>): void {
  if (!section) return;
  for (const k of section.added) into.add(k);
  for (const k of section.removed) into.add(k);
  for (const e of section.changed) into.add(e.key);
}

/**
 * The map-highlightable change set from an apply's diff. `null` (the endpoint
 * could not compare, or no apply has run) is the empty set: nothing to
 * highlight, never a fabricated change.
 */
export function changeSetFromDiff(diff: ImprintDiff | null | undefined): ChangeSet {
  if (!diff) return EMPTY_CHANGE_SET;
  const segments = new Set<string>();
  const nodes = new Set<string>();
  collect(diff.observed?.segments, segments);
  collect(diff.observed?.nodes, nodes);
  // Authored keys are observed entity ids (`14` section 3): a suppress names
  // the host it removed by the same string the map draws it under.
  collect(diff.suppressed_keys, nodes);
  return { segments, nodes };
}

export function changeSetSize(set: ChangeSet): number {
  return set.segments.size + set.nodes.size;
}

export function isSegmentChanged(set: ChangeSet, observedCidr: string): boolean {
  return set.segments.has(observedCidr);
}

export function isNodeChanged(set: ChangeSet, nodeId: string): boolean {
  return set.nodes.has(nodeId);
}

/** How many hosts the TWIN gained and lost across an apply (GAP-087). */
export interface TwinHostChurn {
  added: number;
  removed: number;
}

/**
 * The twin side's host churn, for the compiler-change outcome only.
 *
 * This is the number the map deliberately cannot show: `changeSetFromDiff`
 * above joins on OBSERVED identity, by design, which is exactly why a
 * compiler that RENAMED 11 node groups produced 22 hosts added and 23 removed
 * while the Console honestly reported "1 element changed" (GAP-087). So it is
 * read here, separately, and used only to say how big the population move
 * was -- never to highlight anything, since these ids name nothing drawn.
 *
 * `null` when the endpoint could not compare that side (`twin` is `null`,
 * which is a different answer from present-and-empty -- LES-240) or when no
 * diff was fetched at all. The caller must render that as "the size is
 * unknown", never as zero.
 */
export function twinHostChurn(diff: ImprintDiff | null | undefined): TwinHostChurn | null {
  const hosts = diff?.twin?.hosts;
  if (!hosts) return null;
  return { added: hosts.added.length, removed: hosts.removed.length };
}
