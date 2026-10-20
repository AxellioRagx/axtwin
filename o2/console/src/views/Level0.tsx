/**
 * Level 0 -- the tiered path diagram, rendered from `layout0`'s pure
 * coordinates (`docs/design/15-console-canvas.md` §4). Plain SVG: the element
 * budget (§3) makes canvas/WebGL unnecessary (§9).
 *
 * Every tile carries its CIDR, deployed/ghost counts, occupancy, a role
 * histogram and an evidence-mix ring; every element's evidence is the export's
 * own `*_evidence_class`, aggregated, never inferred. Clicking a tile drills
 * to its segment; the Internet pill and the *unplaced* tile open their
 * populations in the inspector (O7), so nothing is silently invisible.
 *
 * **S4 pins.** A segment tile or a whole site header can be dragged; the drag
 * gesture lives here (`usePinDrag`), the pin MATH stays in the pure layout
 * (`layout/pins.ts`, applied by `layout0`). Hosts -- here the infrastructure
 * devices, the Internet pill and the *unplaced* tile -- are NEVER pinnable
 * (`15` §4), so they carry no drag affordance. A drag beyond a few pixels
 * pins; a plain click still drills.
 *
 * **R1 width.** The SVG is sized in CSS pixels at `1 layout unit = 1 px` and
 * the pane scrolls, instead of being fitted to the pane with `max-width:100%`
 * (which drew a ~2,800-unit plane at ~25 % and rendered 11 px type at under
 * 3 px -- `docs/WORK_ORDER_console_level0_rendering.md` §2 F6). A *fit width*
 * control trades size for overview but never goes below `MIN_FIT_SCALE`; past
 * that the map pans. The decision is `layout/viewport.ts` (pure); this file
 * only measures the pane and applies the result -- the viewBox stays the pure
 * layout's own units, so the scale never perturbs the golden coordinates
 * (LES-268) and the pin drag keeps reading its scale off the live CTM.
 *
 * **S5.1.b clones.** An Alt-drag of a tile drags a ghost off it and, on release,
 * opens the clone form at the drop position; *Clone this segment…* in the
 * inspector draws a dashed proposed tile instead, which a tap or click on the
 * map places. A clone the store has STAGED is drawn at its pin
 * (`layout/added.ts` decides where, and whether its collapsed group holds it),
 * hatched while it is pending -- and selecting it offers the added segment's
 * actions. None of it reflows anything: every clone box is an absolute
 * position on top of the pure layout.
 */
import { useEffect, useId, useMemo, useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent, ReactNode } from "react";

import type { ConsoleGraph, GraphSegment, TrafficPair } from "../model/graph";
import { anyFilterActive, nodePassesFilters } from "../model/graph";
import { EMPTY_CHANGE_SET, isNodeChanged, isSegmentChanged, type ChangeSet } from "../model/changes";
import { saturationFor } from "../model/evidence";
import type { Filters } from "../model/zoom";
import { filtersToQuery, zoomToPath } from "../model/zoom";
import {
  layout0,
  siteKeyOfOverflowAnchor,
  type Level0Layout,
  type Level0Options,
  type OverflowTile,
  type TapBadge,
} from "../layout/level0";
import { arcPath, ARC_TOP_K, type Arc } from "../layout/arcs";
import type { Trunk } from "../layout/trunk";
import { NO_PINS, type AuthoredPin, type Pins } from "../layout/pins";
import { placeAddedTiles, type AddedTilePlacement, type HeldAddedTile } from "../layout/added";
import { pinPlaneStatus, PLANE_STAMP, type PinPlaneStatus } from "../layout/plane";
import { CANVAS_PAD, TILE_H, TILE_W, type Box, type Point } from "../layout/geometry";
import { TIER_LABELS } from "../layout/tiers";
import {
  estimateMapWidth,
  MIN_FIT_SCALE,
  scaleFor,
  type MapScaleMode,
} from "../layout/viewport";
import type { Focus } from "./selection";
import {
  changeClasses,
  EvidenceRing,
  formatBytes,
  formatPercent,
  Nav,
  RoleBadges,
  roleDisplayName,
} from "./mapParts";
import { layoutPointOf, usePinDrag, type PinController } from "./usePinDrag";
import { addSegmentLabel } from "../edit/allocate";

export interface Level0Props {
  graph: ConsoleGraph;
  /** The graph is a DECLARED design (no capture): the east-west band is the
   *  config's conduits, and no sentence speaks of observed traffic. */
  declared?: boolean;
  filters: Filters;
  go: (path: string) => void;
  onFocus: (focus: Focus) => void;
  /** S5.1.a: the segment the inspector is acting on -- its tile is marked
   *  (`aria-current` and `is-selected`, `15` §7). Absent ⇒ nothing marked. */
  selectedCidr?: string | null;
  /** Saved pins from `GET .../layout` (S4). Absent ⇒ the pure no-pins render. */
  savedPins?: Pins;
  /** Persist the pin set via `PUT .../layout` (S4). Absent ⇒ a read-only
   *  render: the affordance shows but a drag commits nothing.
   *
   *  TWO arguments, declared here as well as on `MapPane` and in `usePinDrag`
   *  (final review S2): the whole set, and the one pin this gesture wrote. A
   *  link left on the one-argument form lets a later wrapper drop the second
   *  silently, degrading the plane stamp to reset-only. (TypeScript's
   *  parameter-arity rule still ACCEPTS a one-argument function here, so the
   *  behavioural guard is `r2.ui.test.tsx`'s seam test, not the compiler.) */
  onSavePins?: (pins: Pins, authored?: AuthoredPin) => void;
  /** How many pins on file are still this session's own writes -- the note's
   *  "N of M re-placed" progress. */
  reauthoredCount?: number;
  /** The last failed `PUT .../layout`, or absent (review S3). Said beside the
   *  pin controls, and every tile whose position came only from this session is
   *  marked -- a placement that vanishes on reload is the gesture's whole
   *  point, and an `aria-label` alone does not say so. */
  pinSaveError?: string | null;
  /** The saved layout document's PLANE stamp (R2, `layout/plane.ts`).
   *  `null`/absent ⇒ a legacy file with no stamp. Compared with the plane
   *  being drawn so stale pins are flagged rather than silently misplaced. */
  pinsPlane?: string | null;
  /** S6: the last apply's map-highlightable change set. A tile/device whose
   *  key is in it is marked. Absent ⇒ empty ⇒ nothing highlighted. */
  changeSet?: ChangeSet;
  /** S6: true during the five-second post-apply window. */
  changeGlow?: boolean;
  /** S5.1.b: the segments the edit store adds, with what drawing one needs.
   *  Only a PLACED one (a pin, or this session's placement) is drawn. */
  added?: readonly AddedTileView[];
  /** S5.1.b: the clone being placed (`placed: false`, a dashed proposal the map
   *  can be tapped to place) or filled in (`placed: true`), not yet staged. */
  cloneDraft?: CloneDraftView | null;
  /** S5.1.b: a tap or click placed the proposed clone at `at` (a top-left). */
  onPlaceClone?: (at: Point) => void;
  /** S5.1.b: an Alt-drag of `sourceCidr`'s tile was released with its ghost at
   *  `at`. Absent: an Alt-drag is an ordinary pin drag. */
  onCloneDrop?: (sourceCidr: string, at: Point) => void;
}

/** An added segment as level 0 draws it (S5.1.b). */
export interface AddedTileView {
  cidr: string;
  name: string;
  state: "pending" | "saved" | "applied";
  /** The observed CIDR it is a clone of, when its gesture tag says so. */
  cloneOf: string | null;
  /** How many `add: host` records the store holds on it. */
  hostCount: number;
  /** This session's placement, for when no pin is on file. */
  fallback: Point | null;
}

/** The clone being placed or filled in, as level 0 draws it (S5.1.b). */
export interface CloneDraftView {
  source: string;
  /** The proposed or chosen top-left. */
  at: Point;
  placed: boolean;
  /** The CIDR the clone form currently holds, or `null` -- which group decides
   *  whether a placed draft is held. */
  cidr: string | null;
}

/** A held element's kind, for `heldAddedClause`. */
export interface HeldAddedView {
  siteLabel: string;
  state: "pending" | "saved" | "applied" | "draft";
  clone: boolean;
}

/**
 * The sentence that keeps a HELD added segment discoverable (S5.1.b): a placed
 * clone whose group the per-group budget collapsed is not drawn, and a count of
 * segments cannot say that one of them is the operator's own pending clone. ONE
 * helper, rendered in the visible budget note and in the SVG's description
 * (LES-307). Empty when nothing is held -- no claim about an absent thing.
 */
/**
 * The clause for an added segment the map can draw NOWHERE (S5.1.d): its group
 * is one this map has no column for, so there is no column to append it to and
 * no budget that is hiding it. Said for the same reason a held one is: a count
 * of tiles cannot say that one of the operator's own segments is missing from
 * it (LES-307). ONE helper, used in the visible note and the SVG description.
 */
export function unplaceableClause(unplaceable: readonly { cidr: string }[]): string {
  if (unplaceable.length === 0) return "";
  const n = unplaceable.length;
  return (
    ` · ${n} added segment${n === 1 ? "" : "s"} (${unplaceable.map((u) => u.cidr).join(", ")}) ` +
    `belong${n === 1 ? "s" : ""} to a group this map draws no column for — drag ${n === 1 ? "it" : "them"} ` +
    "onto the map, or open the outline, to work with " + (n === 1 ? "it" : "them")
  );
}

export function heldAddedClause(held: readonly HeldAddedView[]): string {
  if (held.length === 0) return "";
  const bySite = new Map<string, Map<string, { view: HeldAddedView; n: number }>>();
  for (const h of held) {
    const kinds = bySite.get(h.siteLabel) ?? new Map<string, { view: HeldAddedView; n: number }>();
    const key = `${h.state}:${h.clone}`;
    const entry = kinds.get(key) ?? { view: h, n: 0 };
    entry.n += 1;
    kinds.set(key, entry);
    bySite.set(h.siteLabel, kinds);
  }
  const words = ({ view, n }: { view: HeldAddedView; n: number }): string => {
    if (view.state === "draft") return n === 1 ? "the clone being filled in" : `${n} clones being filled in`;
    const noun = view.clone ? `clone${n === 1 ? "" : "s"}` : `added segment${n === 1 ? "" : "s"}`;
    return `${n} ${view.state} ${noun}`;
  };
  const parts = [...bySite.entries()].map(
    ([site, kinds]) => `${[...kinds.values()].map(words).join(" and ")} held in ${site}`,
  );
  return (
    ` · ${parts.join("; ")} — not drawn while ${bySite.size === 1 ? "its group is" : "their groups are"} ` +
    `collapsed: open ${bySite.size === 1 ? "it" : "them"} to see ${held.length === 1 ? "it" : "them"}`
  );
}

/**
 * The layout options a level-0 drawing takes from the URL -- ONE mapping, for
 * the map and for the workspace, which asks the same pure layout where a clone
 * is proposed to go (S5.1.b). `?ew=all`, `?order=` and `?expand=` are BUDGETS
 * the pure layout applies, not a dimming pass over a drawing that exists.
 */
export function level0OptionsFor(filters: Pick<Filters, "eastWestAll" | "tileOrder" | "expand">): Level0Options {
  return { showAllPairs: filters.eastWestAll, order: filters.tileOrder, expanded: filters.expand };
}

/**
 * The WAN box's words (GAP-082 review, S5). A leg is counted only when it
 * lands on a DRAWN tile -- the cast's `internet-sink` is a router leg with no
 * tile. Exactly one router is "the gateway" (the router slice's SNAT
 * egress); several are counted, never collapsed into one gateway, and the
 * count says it is of segments reached (`twins/datacenter` has three
 * routers).
 */
export function wanLabelFor(graph: ConsoleGraph): string {
  // R2: with NO router leg published, the edge is INFERRED from observed
  // traffic -- a statement that non-local traffic exists, and nothing more. It
  // is not the discovery of a NAT address (§8), so it is never called a
  // "gateway" and the label says where it came from.
  if (graph.wanEdge?.inferred) return "egress (inferred from observed traffic)";
  const drawn = (names: readonly string[]): number =>
    names.filter((n) => graph.segmentByName.has(n)).length;
  const plural = (n: number, word: string): string => `${n} ${word}${n === 1 ? "" : "s"}`;
  const [only] = graph.routers;
  // No leg on any drawn tile (every leg on a segment with no tile, e.g. only
  // the cast's `internet-sink`): no count at all rather than "0 legs" -- the
  // box's tooltip lists the legs it could not place (GAP-082 review round 2).
  if (graph.routers.length === 1 && only) {
    const legs = drawn(only.segments);
    return legs > 0 ? `gateway · ${plural(legs, "leg")} · SNAT` : "gateway · SNAT";
  }
  const reached = drawn(graph.routerSegmentNames);
  const routers = `${graph.routers.length} routers`;
  return reached > 0 ? `${routers} · legs on ${plural(reached, "segment")}` : routers;
}

/** The tap badge's label -- the vantage VERBATIM plus the window count it
 *  bounds. Never parsed (the two real bundles spell the vantage differently),
 *  and an absent one says so rather than rendering an empty string. */
export function tapLabel(tap: TapBadge): string {
  const vantage = tap.vantage ?? "vantage not recorded";
  if (tap.windows === null) return vantage;
  return `${vantage} · ${tap.windows} window${tap.windows === 1 ? "" : "s"}`;
}

/** An SVG `points` attribute from a layout polyline. */
function pointsOf(points: readonly Point[]): string {
  return points.map((p) => `${p.x},${p.y}`).join(" ");
}

/**
 * The spine's hover text (review S3): the WHOLE edge's volume, and — when it
 * exceeds what the legs carry — where the difference went.
 *
 * `Trunk.bytes` counts every crossing edge; a leg can only carry traffic whose
 * local end the export PLACED in a segment. On the pit bundle that difference
 * is 4.67 MB of 3.46 GB. Publishing it in the type and never rendering it
 * would make the docstring's "recoverable rather than quietly dropped" false,
 * so the remainder is named here and pointed at the *unplaced* tile, which is
 * where an operator goes for those entities.
 */
export function trunkTitle(trunk: Trunk): string {
  const carried = trunk.legs.reduce((sum, leg) => sum + leg.bytes, 0);
  const remainder = trunk.bytes - carried;
  const head =
    `${formatBytes(trunk.bytes)} crossed this twin's edge in this window ` +
    `(${trunk.flows} flows)`;
  if (remainder <= 0) return head;
  return (
    `${head} — ${formatBytes(remainder)} of it to or from local entities the ` +
    "export placed in no segment, so it is in no leg below (see the unplaced tile)"
  );
}

/**
 * One east-west arc's hover text (R4): the pair, its volume in this window,
 * and — because `15` §5 keeps colour for evidence and the map is therefore
 * protocol-blind — the protocol mix, which is the one channel the geometry
 * cannot carry.
 *
 * `label` maps a segment NAME to what an operator reads (its observed CIDR),
 * so this stays a pure function of the arc plus two lookups the caller already
 * has.
 */
export function arcTitle(
  arc: Arc,
  label: (name: string) => string,
  pair?: TrafficPair,
): string {
  const pairText = `${label(arc.a)} ↔ ${label(arc.b)}`;
  // An asserted arc says so BEFORE its numbers: "declared" and "observed" are
  // different claims, and the dash that carries it on screen is not a word.
  const declared = arc.kind === "asserted" ? "declared conduit · " : "";
  // R6: one curve can stand for several segment pairs once a group is
  // collapsed, and a volume with no such note would read as one link's.
  const merged = arc.pairs > 1 ? ` · ${arc.pairs} links merged` : "";
  if (!arc.hasTraffic) {
    return `${pairText}: ${declared}no traffic observed in this window${merged}`;
  }
  const head =
    `${pairText}: ${declared}${formatBytes(arc.bytes)} east-west in this window ` +
    `(${arc.flows} flows)${merged}`;
  const protos = (pair?.protoMix ?? [])
    .slice(0, 3)
    .map((share) => `${share.proto} ${formatBytes(share.bytes)}`)
    .join(", ");
  if (protos) return `${head} · ${protos}`;
  // A MERGED curve has no single pair to take a mix from -- its key is an
  // anchor, not a pair -- so the clause is stated as unavailable rather than
  // quietly dropped, which would read as "this link has no protocols".
  return arc.pairs > 1
    ? `${head} · protocol mix is per link; this curve merges ${arc.pairs}, so the matrix has each one`
    : head;
}

/**
 * What the east-west band is showing, in ONE sentence and ONE helper (R4,
 * LES-307).
 *
 * The top-K budget is a projection, and a projection that does not disclose
 * itself is indistinguishable from a complete answer: twelve arcs read as
 * "there are twelve links". So this sentence is rendered in three places — the
 * visible note beside the control, the control's own accessible name, and the
 * SVG `<desc>` (the arcs group is `aria-hidden`, so it is the only channel a
 * screen reader has) — and all three call this function, because a second
 * phrasing is a second thing to keep true.
 *
 * It also names where the complete record is, rather than telling the reader
 * something is hidden and giving them nowhere to go.
 */
/** The east-west line for a DECLARED design: conduits, never traffic. */
export function declaredEastWestNote(conduits: number, folded = 0, internal = 0): string {
  return (
    `${conduits} declared conduit${conduits === 1 ? "" : "s"} (paths the twin config allows; nothing observed)` +
    (folded > 0 ? ` · ${folded} drawn through a collapsed group` : "") +
    (internal > 0 ? ` · ${internal} inside one collapsed group, not drawn (expand it)` : "")
  );
}

export function eastWestNote(
  shown: number,
  hidden: number,
  silent = 0,
  folded = 0,
  internal = 0,
): string {
  const total = shown + hidden;
  // The band is fed by TWO families, so a sentence counting only one of them
  // describes a drawing nobody is looking at: the shipped fixture draws three
  // arcs under "showing all 1 observed east-west link" (review S2). It does not
  // bite on either real bundle, because every conduit they declare also
  // carries traffic — it appears first on a twin that declares a path the
  // capture never saw, which is the most informative thing the band can show.
  const conduits =
    silent > 0
      ? ` · ${silent} declared conduit${silent === 1 ? "" : "s"} with no traffic in this window`
      : "";
  // R6: a link whose segment the per-group budget collapsed is still drawn —
  // through its group's `+N more` tile, merged with anything else that lands
  // there — and one whose BOTH ends collapsed into the same group cannot be
  // drawn at all. Two different facts, so two clauses, and neither is silent.
  const collapsed =
    (folded > 0
      ? ` · ${folded} drawn through a collapsed group`
      : "") +
    (internal > 0
      ? ` · ${internal} inside one collapsed group, not drawn (expand it, or see the matrix)`
      : "");
  if (total === 0) return `no observed east-west traffic in this window${conduits}${collapsed}`;
  if (hidden > 0) {
    return (
      `showing the ${shown} busiest of ${total} observed east-west links · ` +
      `${hidden} smaller link${hidden === 1 ? "" : "s"} hidden ` +
      `(the matrix has every one)${conduits}${collapsed}`
    );
  }
  return `showing all ${total} observed east-west link${total === 1 ? "" : "s"}${conduits}${collapsed}`;
}

/**
 * What the SEGMENT TIER is showing, in ONE sentence and ONE helper (R6, the
 * same rule R4's `eastWestNote` follows — LES-307/LES-312).
 *
 * The per-group budget is a projection, and eighteen tiles read as "this twin
 * has eighteen segments" unless the drawing says otherwise. `shown` counts the
 * tiles DRAWN (not a group's roster), so the number in the sentence is the
 * number an operator can count on screen; a map with nothing collapsed claims
 * no residual at all.
 */
export function tileBudgetNote(
  shown: number,
  hidden: number,
  groups: number,
  volume?: HiddenVolume,
): string {
  const total = shown + hidden;
  if (total === 0) return "no segments in this twin";
  if (hidden === 0) return `showing all ${total} segment${total === 1 ? "" : "s"}`;
  return (
    `showing ${shown} of ${total} segments · ${hidden} collapsed in ` +
    `${groups} group${groups === 1 ? "" : "s"}${volumeClause(volume)} — open a group to see them ` +
    "(the outline, the matrix and search list every one)"
  );
}

/** How much traffic the collapsed segments carry. Summed from the graph, not
 *  from the layout: a collapsed segment has no tile, so the byte line on the
 *  tiles cannot state it. */
export interface HiddenVolume {
  northSouth: number;
  eastWest: number;
}

/**
 * **The clause that stops the budget from hiding a group's VOLUME in silence**
 * (review S1). The ranking is by placed-host COUNT, so a segment with one host
 * and a gigabyte of egress collapses — and six modest tiles beside a `+N more`
 * that counts only segments say nothing about it. Measured on the shipped
 * bundles: the pit `10.20` group hides 4 of 10 segments carrying **75.1 % of
 * its north-south bytes** (`10.20.195.0/24` alone is 1.77 GB on one host), pit
 * `10.80` hides 13 of 19 carrying **65.5 %**, and `corpapcn-twin`'s `10.20`
 * hides 4 of 10 carrying **99.9 % of its east-west volume**. With the figure on
 * screen, `?order=cidr` becomes a choice an operator makes knowingly.
 *
 * Omitted when the collapsed set carried nothing: "0 B and 0 B" is noise, and
 * its absence is not a claim (the count is still there).
 */
function volumeClause(volume?: HiddenVolume): string {
  if (!volume || (volume.northSouth <= 0 && volume.eastWest <= 0)) return "";
  return (
    `, carrying ${formatBytes(volume.northSouth)} north-south and ` +
    `${formatBytes(volume.eastWest)} east-west`
  );
}

/** A tile's two volumes, in this window set — never a rate (§8). */
export function tileVolumeLine(segment: GraphSegment): string {
  return `N-S ${formatBytes(segment.northSouth.bytes)} · E-W ${formatBytes(segment.eastWest.bytes)}`;
}

/** The `+N more` tile's hover text: what is in there, by name, and what
 *  clicking does. The complete list rather than a further projection — thirteen
 *  CIDRs is a readable tooltip, and a projection of a projection is where a
 *  count stops being checkable. */
export function overflowTitle(more: OverflowTile, volume?: HiddenVolume): string {
  if (more.expanded) {
    return (
      `${more.siteKey} is showing all ${more.total} of its segments — ` +
      "click to collapse it to the busiest few"
    );
  }
  const names = more.hidden.map((s) => s.cidr).join(", ");
  return (
    `${more.hidden.length} of ${more.total} segments in ${more.siteKey} are ` +
    `collapsed${volumeClause(volume)}: ${names} — click to show them`
  );
}

/** The same sentence as an accessible name (LES-307: one helper, every
 *  channel), prefixed so a screen reader hears what the control IS first. */
export function overflowLabel(more: OverflowTile, volume?: HiddenVolume): string {
  const head = more.expanded
    ? `Collapse ${more.siteKey}`
    : `Expand ${more.siteKey}: ${more.hidden.length} more segments`;
  return `${head} — ${overflowTitle(more, volume)}`;
}

/** Add or remove one site key from `?expand=`, keeping the set sorted so the
 *  URL is canonical whichever order the operator clicked in. */
export function toggleExpand(expand: readonly string[], siteKey: string): string[] {
  const next = new Set(expand);
  if (next.has(siteKey)) next.delete(siteKey);
  else next.add(siteKey);
  return [...next].sort();
}

/**
 * What to say about pins saved against a DIFFERENT layout plane (R2, LES-289).
 * `null` when there is nothing to say -- no pins, or pins that match.
 *
 * Both cases keep the pins ON the map: they are the operator's work, and the
 * whole point of the stamp is that "your pins may be in the wrong place" can
 * be SAID instead of silently drawn or silently discarded.
 */
export function pinPlaneNote(
  status: PinPlaneStatus,
  saved: string | null | undefined,
  progress?: { done: number; total: number },
): string | null {
  if (status === "none" || status === "current") return null;
  // Progress makes the rule visible rather than implied: the operator can see
  // that re-placing one pin moved them one step, not that nothing happened.
  const done = progress && progress.done > 0 && progress.done < progress.total
    ? ` You have re-placed ${progress.done} of ${progress.total} so far.`
    : "";
  if (done) return `${planeNoteBody(status, saved)}${done}`;
  return planeNoteBody(status, saved);
}

function planeNoteBody(status: PinPlaneStatus, saved: string | null | undefined): string {
  // The advice and the mechanism must agree (M1): one drag does NOT clear this
  // warning, because one drag re-places one pin. The layout is recorded when
  // the set is WHOLLY re-authored -- a reset, or every pin re-placed.
  if (status === "legacy") {
    return (
      "These pins were saved before the Console recorded which layout they " +
      "were drawn on, so they may no longer line up. They are shown as saved; " +
      "reset them, or re-place every one, and this layout is recorded on the " +
      "save that completes the set."
    );
  }
  return (
    `These pins were saved on a different layout (${saved}) from the one drawn ` +
    `here (${PLANE_STAMP}), so they may no longer line up. They are shown as ` +
    "saved rather than silently dropped; reset them, or re-place every one to " +
    "record this layout."
  );
}

/** The one-line "not published" note (GAP-082 review, S4): an older server
 *  omits the roster fields, and an undrawn band -- or a WAN edge shown as
 *  merely inferred -- must not read as "the twin declares none". */
function rosterNote(omits: ReadonlyArray<"conduits" | "routers">): string {
  const what = omits.map((o) => (o === "conduits" ? "the conduit roster" : "the routers' legs"));
  // R2: an unpublished `routers` no longer means no WAN box -- the edge is
  // still drawn when traffic crosses it, labelled as INFERRED. What is missing
  // is the twin's own DECLARATION of a gateway, so the note names that.
  //
  // R4: an unpublished ROSTER no longer means no east-west band either -- the
  // band now draws OBSERVED segment-pair arcs from the export's own edges, and
  // only the twin's declared conduits are missing. (This is the last of
  // GAP-079's "the band is empty because the server is old" wording, and it is
  // deleted rather than kept: the band is not empty any more.)
  const drawnNot = omits.map((o) =>
    o === "conduits" ? "the twin's declared conduits" : "the declared WAN edge",
  );
  return (
    `This server does not publish ${what.join(" or ")} (it predates R5), so ` +
    `${drawnNot.join(" and ")} ${omits.length === 1 ? "is" : "are"} not drawn -- ` +
    "that is not a statement that the twin declares none."
  );
}

export function Level0(props: Level0Props): JSX.Element {
  const { graph, filters, go, onFocus, onSavePins, onCloneDrop, onPlaceClone } = props;
  const selectedCidr = props.selectedCidr ?? null;
  const changeSet = props.changeSet ?? EMPTY_CHANGE_SET;
  const changeGlow = props.changeGlow ?? false;
  const savedPins = props.savedPins ?? NO_PINS;
  const added = props.added ?? NO_ADDED;
  const cloneDraft = props.cloneDraft ?? null;
  const svgRef = useRef<SVGSVGElement | null>(null);
  const hatchId = `pending-hatch-${useId().replace(/[^A-Za-z0-9_-]/g, "")}`;
  const pin = usePinDrag({
    svgRef,
    savedPins,
    onCommit: onSavePins,
    ...(onCloneDrop
      ? { onCloneDrop: (key: string, at: Point) => onCloneDrop(key.slice("segment:".length), at) }
      : {}),
  });

  // R4: `?ew=all` is a BUDGET, not a filter (`model/zoom.ts`'s taxonomy) --
  // it changes which arcs the LAYOUT draws, so it is an option on the layout
  // call rather than a dimming pass over the result.
  const mapOptions = useMemo<Level0Options>(
    // R6: the ranking and the per-group budget are BUDGETS, decided by the URL
    // and applied by the pure layout (`level0OptionsFor`). Keyed on the three
    // fields it reads, so an unrelated filter does not re-lay the map out.
    () => level0OptionsFor(filters),
    [filters.eastWestAll, filters.tileOrder, filters.expand],
  );
  // The pinned positions (saved + the in-progress drag) flow into the PURE
  // layout, so a pinned tile lands at its top-left and its arcs follow; an
  // empty set is the no-pins render, byte-identical to the goldens.
  const l0: Level0Layout = useMemo(
    () => layout0(graph, pin.effectivePins, mapOptions),
    [graph, pin.effectivePins, mapOptions],
  );
  // The protocol mix per drawn pair, for the hover text only -- the geometry
  // is deliberately protocol-blind (`15` §5).
  const pairByKey = useMemo(
    () => new Map(graph.trafficPairs.map((pair) => [`${pair.a} ${pair.b}`, pair])),
    [graph],
  );
  // Counted off the ARCS, which know both `kind` and `hasTraffic` -- never off
  // `trafficPairs` alone, which counts one of the two families feeding the
  // band (review S2). An asserted conduit that carries traffic IS an observed
  // link shown; one that carries none is a different fact and gets its own
  // clause.
  // Counted in LINKS, not in curves (LES-312): since R6 one curve can carry
  // several segment pairs, so `arcs.length` would count the drawing rather than
  // the traffic it stands for.
  const hiddenPairs = l0.hiddenPairs;
  const shownPairs = l0.arcs
    .filter((a) => a.hasTraffic)
    .reduce((n, a) => n + a.pairs, 0);
  const silentConduits = l0.arcs
    .filter((a) => !a.hasTraffic)
    .reduce((n, a) => n + a.pairs, 0);
  const eastWestText = props.declared
    ? declaredEastWestNote(silentConduits + shownPairs, l0.foldedPairs, l0.internalPairs)
    : eastWestNote(shownPairs, hiddenPairs, silentConduits, l0.foldedPairs, l0.internalPairs);
  // R6's own disclosure: how many segments have a tile, how many do not, and --
  // the half a tile cannot show -- how much traffic went with them (review S1).
  const collapsedGroups = l0.overflow.filter((more) => more.hidden.length > 0).length;
  const hiddenVolumeOf = (more: OverflowTile): HiddenVolume =>
    more.hidden.reduce(
      (sum, seg) => {
        const segment = graph.segmentByCidr.get(seg.cidr);
        return {
          northSouth: sum.northSouth + (segment?.northSouth.bytes ?? 0),
          eastWest: sum.eastWest + (segment?.eastWest.bytes ?? 0),
        };
      },
      { northSouth: 0, eastWest: 0 },
    );
  const hiddenVolume = l0.overflow.reduce<HiddenVolume>(
    (sum, more) => {
      const one = hiddenVolumeOf(more);
      return {
        northSouth: sum.northSouth + one.northSouth,
        eastWest: sum.eastWest + one.eastWest,
      };
    },
    { northSouth: 0, eastWest: 0 },
  );
  // S5.1.b: the placed added segments -- drawn at their pins, or HELD by a
  // collapsed group (`layout/added.ts`). The draft clone joins the held set
  // once it is placed, so a drop into a collapsed group is said, not lost.
  const observedCidrs = useMemo(() => graph.segments.map((s) => s.observedCidr), [graph]);
  const addedPlaced = placeAddedTiles(
    l0,
    observedCidrs,
    added.map((a) => ({ cidr: a.cidr, fallback: a.fallback })),
    pin.effectivePins,
  );
  const addedByCidr = new Map(added.map((a) => [a.cidr, a]));
  const draftHeld: HeldAddedTile | null =
    cloneDraft && cloneDraft.placed && cloneDraft.cidr !== null
      ? (placeAddedTiles(
          l0,
          [...observedCidrs, ...added.map((a) => a.cidr)],
          [{ cidr: cloneDraft.cidr, fallback: cloneDraft.at }],
          NO_PINS,
        ).held[0] ?? null)
      : null;
  const heldViews: HeldAddedView[] = [
    ...addedPlaced.held.map((h) => ({
      siteLabel: h.siteLabel,
      state: addedByCidr.get(h.cidr)?.state ?? "pending",
      clone: (addedByCidr.get(h.cidr)?.cloneOf ?? null) !== null,
    })),
    ...(draftHeld ? [{ siteLabel: draftHeld.siteLabel, state: "draft" as const, clone: true }] : []),
  ];
  const budgetText =
    tileBudgetNote(l0.tiles.length, l0.hiddenSegments, collapsedGroups, hiddenVolume) +
    heldAddedClause(heldViews) +
    unplaceableClause(addedPlaced.unplaceable);
  // Pins naming a tile the budget collapsed: kept and re-applied on expand, but
  // invisible until then -- so "3 pinned" with two of them off the map reads as
  // two pins doing nothing (review S2).
  const collapsedCidrs = new Set(l0.overflow.flatMap((m) => m.hidden.map((s) => s.cidr)));
  const heldPins = Object.keys(savedPins).filter(
    (key) => key.startsWith("segment:") && collapsedCidrs.has(key.slice("segment:".length)),
  ).length;
  // Pins naming NOTHING in play (review S2): a clone's drop position is a pin
  // on `segment:<cidr>`, and removing the clone's records leaves it behind --
  // "1 pinned" over a map with no pinned tile. Nothing here deletes it (that is
  // S5.1.d's cleanup); the count simply stops claiming it does something.
  // Counted for site keys too, on the same rule, since a group the twin no
  // longer forms leaves the same kind of orphan.
  const inPlay = new Set<string>([
    ...observedCidrs.map((c) => `segment:${c}`),
    ...[...collapsedCidrs].map((c) => `segment:${c}`),
    ...added.map((a) => `segment:${a.cidr}`),
    ...l0.sites.map((sp) => sp.site.key),
    ...l0.overflow.map((more) => more.siteKey),
  ]);
  const orphanPins = Object.keys(savedPins).filter((key) => !inPlay.has(key)).length;
  // A tile whose position came only from this session, i.e. its `PUT` has not
  // landed. Marked visibly (S3), not only in its accessible name.
  const sessionPlaced = addedPlaced.drawn.filter((d) => d.from === "session").length;
  const mapHref = (next: Partial<Filters>): string =>
    `${zoomToPath({ level: "network", imprint: graph.imprintId })}` +
    `${filtersToQuery({ ...filters, ...next, problems: [] })}`;
  // An arc end is a segment name, or a collapsed group's shared anchor.
  const anchorLabel = (key: string): string => {
    const site = siteKeyOfOverflowAnchor(key);
    if (site === null) return graph.segmentByName.get(key)?.observedCidr ?? key;
    const hidden = l0.overflow.find((more) => more.siteKey === site)?.hidden.length ?? 0;
    return `${hidden} collapsed segment${hidden === 1 ? "" : "s"} in ${site}`;
  };
  const showAllLabel = filters.eastWestAll ? `show the ${ARC_TOP_K} busiest` : "show all";
  const pinCount = Object.keys(savedPins).length;
  // R2: were the saved pins authored against the plane about to be drawn? A
  // mismatch is REPORTED and the pins are still applied (`layout/plane.ts`).
  const planeNote = pinPlaneNote(
    pinPlaneStatus(props.pinsPlane, pinCount),
    props.pinsPlane,
    { done: props.reauthoredCount ?? 0, total: pinCount },
  );
  // The viewBox grows to CONTAIN a tile/site a COMMITTED pin pushed past the
  // pure canvas. It is framed to `savedPins`, NOT the live drag (S2): a viewBox
  // that grew with the in-progress drag would rescale the `max-width:100%` SVG
  // mid-gesture, changing the CTM under the cursor so the tile accelerates away
  // from it. It grows on the next committed save instead (a tile dragged past
  // the current edge clips until release -- an accepted trade for a drag that
  // tracks the cursor). Computed HERE (the view), never in the pure layout --
  // `l0.width`/`height` stay the golden-pinned function; with no pins the frame
  // is exactly `0 0 width height` (unchanged from S3).
  // S5.1.b: framed to contain every placed added segment and the clone draft
  // too -- from SAVED pins, never the live drag, for the S2 reason above.
  const frameExtras = useMemo(() => {
    const committed = layout0(graph, savedPins, mapOptions);
    const boxes: Box[] = placeAddedTiles(
      committed,
      graph.segments.map((s) => s.observedCidr),
      added.map((a) => ({ cidr: a.cidr, fallback: a.fallback })),
      savedPins,
    ).drawn.map((d) => d.box);
    if (cloneDraft) boxes.push({ x: cloneDraft.at.x, y: cloneDraft.at.y, w: TILE_W, h: TILE_H });
    return { committed, boxes };
  }, [graph, savedPins, mapOptions, added, cloneDraft]);
  const frame = useMemo(() => frameFor(frameExtras.committed, frameExtras.boxes), [frameExtras]);
  // A filter emphasizes a subset (`15` §7): a tile whose placed hosts include
  // no match is dimmed, never hidden -- the counts stay honest.
  const dim = anyFilterActive(filters);
  const tileMatches = (seg: GraphSegment): boolean =>
    !dim || seg.hosts.some((h) => nodePassesFilters(h, filters));
  // R5: the WAN box names what the cast publishes about its routers -- "the
  // gateway" only for exactly ONE router, legs counted only where they land
  // on a drawn tile (GAP-082 review, S5). (R2 adds the edge INFERRED from
  // observed traffic and its own label.)
  const wanLabel = wanLabelFor(graph);
  const undrawnLegs = graph.routerSegmentNames.filter((n) => !graph.segmentByName.has(n));

  // R1: the pane's inner width, measured -- with the shell-geometry ESTIMATE
  // as the fallback for the first paint and for jsdom, where every element
  // measures 0 (`layout/viewport.ts`). `null` means "not measured yet", which
  // is why the estimate is also used inline below rather than only in state:
  // the first render must already pick a scale, or the map would flash at 1:1
  // and then resize.
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const [paneWidth, setPaneWidth] = useState<number | null>(null);
  useEffect(() => {
    const measure = (): void => {
      const measured = wrapRef.current?.clientWidth ?? 0;
      setPaneWidth(measured > 0 ? measured : estimateMapWidth(window.innerWidth));
    };
    measure();
    // The pane also changes width with NO window resize (S3): collapsing the
    // outline rewrites the shell's `grid-template-columns`, which is exactly
    // when `fit width` matters most -- and a stale width there would compute
    // the scale from the space the map no longer has. Observe the element, and
    // keep the window listener as the cheap extra (and the only one jsdom has).
    window.addEventListener("resize", measure);
    let observer: ResizeObserver | null = null;
    if (typeof ResizeObserver !== "undefined" && wrapRef.current) {
      observer = new ResizeObserver(measure);
      observer.observe(wrapRef.current);
    }
    return () => {
      window.removeEventListener("resize", measure);
      observer?.disconnect();
    };
  }, []);
  const available =
    paneWidth ?? (typeof window === "undefined" ? 0 : estimateMapWidth(window.innerWidth));
  const [scaleMode, setScaleMode] = useState<MapScaleMode>("actual");
  const scale = scaleFor(scaleMode, available, frame.w);
  // Whether FIT ITSELF cannot show the whole plane -- read off the UNCLAMPED
  // ratio, so a plane that happens to fit at exactly 0.8 does not claim to be
  // truncated when it is precisely whole.
  const belowFloor = available > 0 && available / frame.w < MIN_FIT_SCALE;
  // How much is cut at the current scale (S7). At the 1:1 default on a narrow
  // window the plane is simply clipped, and the scrollbar was the only tell --
  // which is why the INTERNET and WAN EDGE rails read as empty.
  const offscreenPx = available > 0 ? Math.max(0, Math.round(frame.w * scale - available)) : 0;
  const drawnRails = l0.rails.filter((rail) => rail.box.h > 0);
  // A DECLARED gateway whose edge carried nothing in this window: the spine,
  // stem and bus all take the hairline, so they need the same visible
  // treatment the silent legs get (final review S3) -- otherwise the whole
  // path renders at 0.5px under 50% opacity, i.e. not at all.
  const trunkSilent = l0.trunk && l0.trunk.bytes <= 0 ? " is-silent" : "";

  return (
    <div className="map-scroll" ref={wrapRef}>
      {/* S5.1.b: while a clone is being placed, the map says how -- and that the
          inspector's controls do the same without a pointer. */}
      {cloneDraft && !cloneDraft.placed && (
        <p className="notice placement-bar" data-testid="placement-bar">
          Placing a clone of <span className="mono">{cloneDraft.source}</span>: click or tap
          the map where it goes, or move and place the dashed proposal with the
          inspector&apos;s controls. Escape cancels.
        </p>
      )}
      {onSavePins && (
        <div className="pin-controls" data-testid="pin-controls">
          <span className="meta">
            Drag a segment tile or a site header to pin its position.
            {pinCount > 0 && <> · <strong>{pinCount}</strong> pinned</>}
            {heldPins > 0 && (
              <span data-testid="pins-held"> · {heldPins} held for collapsed tiles</span>
            )}
            {orphanPins > 0 && (
              <span data-testid="pins-orphan">
                {" "}
                · {orphanPins} naming no segment this twin or this file has
              </span>
            )}
            {sessionPlaced > 0 && (
              <span data-testid="pins-session">
                {" "}
                · {sessionPlaced} placed in this browser only
              </span>
            )}
          </span>
          {pinCount > 0 && (
            <button
              type="button"
              className="pin-reset"
              data-testid="pin-reset"
              onClick={() => onSavePins(NO_PINS)}
            >
              Reset pins
            </button>
          )}
        </div>
      )}
      {props.pinSaveError && (
        <p className="notice is-error" role="alert" data-testid="pins-save-failed">
          {props.pinSaveError}
        </p>
      )}
      {planeNote && (
        <p className="notice" data-testid="pins-plane-note">
          {planeNote}
        </p>
      )}
      {graph.castOmits.length > 0 && (
        <p className="meta roster-note" data-testid="roster-unpublished">
          {rosterNote(graph.castOmits)}
        </p>
      )}
      {/* R2: no observed crossing traffic AND no declared router. The map
          draws no WAN box and no trunk -- and says why, because an absent
          edge on a diagram reads as "this twin has no egress". */}
      {!l0.wanEdge && (
        <p className="meta" data-testid="no-wan-edge">
          No traffic crossing this twin's edge was observed in this window, and
          the cast declares no router — so no WAN edge and no trunk are drawn.
          That is not a statement that this twin has no egress.
        </p>
      )}
      {/* R4: the band draws OBSERVED traffic, so "nothing here" is a statement
          about the capture -- not about an old server or a missing roster. */}
      {graph.trafficPairs.length === 0 && props.declared && (
        <p className="meta" data-testid="declared-east-west">
          A declared design has no observed traffic. The dashed lines are the conduits the twin config declares —
          the segment pairs its router will carry traffic between.
        </p>
      )}
      {graph.trafficPairs.length === 0 && !props.declared && (
        <p className="meta" data-testid="no-east-west">
          No observed east-west traffic in this window — the export shows no
          traffic between two segments this map draws.
        </p>
      )}
      {/* The top-K disclosure: N of M, in words, beside a real control (the
          arcs themselves are `aria-hidden`). LES-307. */}
      {(l0.arcs.length > 0 || hiddenPairs > 0) && (
        <div className="ew-controls" data-testid="ew-controls">
          <span className="meta" data-testid="ew-note">
            East-west: {eastWestText}
          </span>
          {(hiddenPairs > 0 || filters.eastWestAll) && (
            <button
              type="button"
              className="link"
              data-testid="ew-toggle"
              aria-label={`${eastWestText} — ${showAllLabel}`}
              onClick={() =>
                go(
                  `${zoomToPath({ level: "network", imprint: graph.imprintId })}` +
                    `${filtersToQuery({ ...filters, eastWestAll: !filters.eastWestAll })}`,
                )
              }
            >
              {showAllLabel}
            </button>
          )}
        </div>
      )}
      {/* R6's disclosure: how many segments have a tile, and the ranking that
          decided which. The `+N more` tiles are the affordance; this is the
          sentence, and both come from one helper (LES-307). */}
      <div className="budget-controls" data-testid="budget-controls">
        <span className="meta" data-testid="budget-note">
          Segments: {budgetText}
        </span>
        <span className="meta">
          order:{" "}
          <a
            className={filters.tileOrder === "hosts" ? "link is-current" : "link"}
            data-testid="order-hosts"
            href={mapHref({ tileOrder: "hosts" })}
            onClick={(e) => {
              e.preventDefault();
              go(mapHref({ tileOrder: "hosts" }));
            }}
          >
            hosts
          </a>{" "}
          ·{" "}
          <a
            className={filters.tileOrder === "cidr" ? "link is-current" : "link"}
            data-testid="order-cidr"
            href={mapHref({ tileOrder: "cidr" })}
            onClick={(e) => {
              e.preventDefault();
              go(mapHref({ tileOrder: "cidr" }));
            }}
          >
            cidr
          </a>
        </span>
      </div>
      <div className="map-zoom" data-testid="map-zoom">
        <span className="meta">scale</span>
        <button
          type="button"
          className={scaleMode === "actual" ? "zoom-chip on" : "zoom-chip"}
          onClick={() => setScaleMode("actual")}
          data-testid="zoom-actual"
        >
          1:1
        </button>
        <button
          type="button"
          className={scaleMode === "fit" ? "zoom-chip on" : "zoom-chip"}
          onClick={() => setScaleMode("fit")}
          data-testid="zoom-fit"
        >
          fit width
        </button>
        <span className="meta" data-testid="zoom-readout">
          {Math.round(scale * 100)}%
          {scaleMode === "fit" && belowFloor
            ? ` · ${Math.round(MIN_FIT_SCALE * 100)}% floor — scroll for the rest`
            : offscreenPx > 0
              ? ` · ${offscreenPx} px off-screen — scroll, or fit width`
              : ""}
        </span>
      </div>
      <svg
        ref={svgRef}
        className="level0"
        viewBox={`${frame.x} ${frame.y} ${frame.w} ${frame.h}`}
        // Sized in CSS px = layout units * scale (R1). The viewBox keeps the
        // PURE units, so this is the only place the browser's user-unit -> px
        // transform is decided, and `data-scale` publishes it for a test.
        width={frame.w * scale}
        height={frame.h * scale}
        data-scale={scale}
        role="img"
        aria-label={`Level 0 network diagram for ${graph.imprintId}`}
        data-testid="level0-svg"
        onPointerMove={pin.svgProps.onPointerMove}
        onPointerUp={pin.svgProps.onPointerUp}
        onPointerCancel={pin.svgProps.onPointerCancel}
      >
        {/* The tier structure for assistive tech: the rail groups below are
            `aria-hidden` (their labels would otherwise be read in document
            order among forty tiles), so the tiers are stated once, here, as
            the diagram's description. */}
        {/* The trunk's own `<g>` is `aria-hidden` (forty tile labels would
            otherwise be interleaved with connector titles in document order),
            so the PATH -- R2's whole contribution -- is stated here or it
            reaches assistive tech nowhere at all. */}
        <desc data-testid="level0-desc">
          {`Tiered network diagram. Tiers, top to bottom: ${drawnRails
            .map((rail) => TIER_LABELS[rail.tier])
            .join(", ")}.` +
            (l0.trunk
              ? ` A trunk runs from the Internet through the capture tap and the WAN edge, branching into ${l0.trunk.legs.length} leg${l0.trunk.legs.length === 1 ? "" : "s"}, one per site group, each drawn in proportion to its traffic across the edge.`
              : " No external edge was observed or declared, so no trunk is drawn.") +
            // R3: the devices are POSITIONED over their group, which is a
            // statement a sighted reader gets for free and a screen reader
            // cannot get at all unless it is said.
            (l0.infraDevices.length > 0
              ? ` ${l0.infraDevices.length} infrastructure device${l0.infraDevices.length === 1 ? " is" : "s are"} drawn above the site group each one serves.`
              : "") +
            // R4: the arcs group is `aria-hidden` (forty curve titles in
            // document order is not a reading), so the band -- and the fact
            // that it is a PROJECTION -- is stated here or nowhere (LES-307).
            ` The east-west band beneath the segments is ${eastWestText}.` +
            // R6: the per-group budget is the same kind of projection, and the
            // `+N more` tiles that disclose it on screen are inside groups a
            // screen reader reaches one tile at a time. Said once, here.
            ` The segment tier is ${budgetText}.`}
        </desc>

        {/* Tier rails (R1) -- the left-edge labels that make the stack read as
            a path, drawn FIRST so every element paints over them. A tier with
            a zero-height extent (no conduits, no router, no infra devices)
            draws no rail: a label over nothing would claim the tier has
            content. */}
        {drawnRails
          .map((rail) => (
            <g key={rail.tier} className={`rail rail-${rail.tier}`} data-testid="tier-rail" aria-hidden="true">
              <text x={rail.box.x} y={rail.box.y + 11} className="rail-label">
                {TIER_LABELS[rail.tier]}
              </text>
              <line
                className="rail-rule"
                x1={rail.box.x + rail.box.w}
                y1={rail.box.y}
                x2={rail.box.x + rail.box.w}
                y2={rail.box.y + rail.box.h}
              />
            </g>
          ))}

        {/* The trunk (R2): Internet -> tap -> WAN edge -> one leg per site
            group, stroke width by north-south bytes. Drawn FIRST of the
            content so every box paints over it -- a leg passes BEHIND the
            infrastructure row rather than striking through it (R3 moves those
            devices over their own column and gives them stubs). Legs stop at
            each group header's top edge and enter no box. */}
        {l0.trunk && (
          <g className="trunk-group" data-testid="trunk" aria-hidden="true">
            <polyline
              className={`trunk${trunkSilent}`}
              data-testid="trunk-spine"
              points={pointsOf(l0.trunk.spine)}
              strokeWidth={l0.trunk.strokeWidth}
            >
              <title>{trunkTitle(l0.trunk)}</title>
            </polyline>
            {l0.trunk.stem.length > 0 && (
              <polyline
                className={`trunk${trunkSilent}`}
                data-testid="trunk-stem"
                points={pointsOf(l0.trunk.stem)}
                strokeWidth={l0.trunk.strokeWidth}
              />
            )}
            {l0.trunk.bus && (
              <polyline
                className={`trunk${trunkSilent}`}
                data-testid="trunk-bus"
                points={pointsOf(l0.trunk.bus)}
                strokeWidth={l0.trunk.strokeWidth}
              />
            )}
            {l0.trunk.legs.map((leg) => (
              <g key={leg.siteKey}>
                <polyline
                  className={`trunk${leg.bytes > 0 ? "" : " is-silent"}`}
                  data-testid="trunk-leg"
                  data-site={leg.siteKey}
                  data-bytes={leg.bytes}
                  points={pointsOf(leg.points)}
                  strokeWidth={leg.strokeWidth}
                >
                  <title>
                    {`${leg.siteKey}: ${formatBytes(leg.bytes)} north-south in this window (${leg.flows} flows)`}
                  </title>
                </polyline>
                {leg.labelled && (
                  <text
                    className="trunk-label"
                    data-testid="trunk-leg-label"
                    x={(leg.points[0]?.x ?? 0) + 6}
                    y={(leg.points[0]?.y ?? 0) + 13}
                  >
                    {formatBytes(leg.bytes)}
                  </text>
                )}
              </g>
            ))}
          </g>
        )}

        {/* The capture tap, on the trunk's spine (F8). The vantage is drawn
            VERBATIM beside the badge -- never parsed. */}
        {l0.tap && (
          <g data-testid="tap-badge">
            <rect
              className="tap-badge"
              x={l0.tap.box.x}
              y={l0.tap.box.y}
              width={l0.tap.box.w}
              height={l0.tap.box.h}
              rx={l0.tap.box.h / 2}
            />
            <text
              className="tap-text"
              x={l0.tap.box.x + l0.tap.box.w / 2}
              y={l0.tap.box.y + l0.tap.box.h / 2}
              dominantBaseline="central"
              textAnchor="middle"
            >
              TAP
            </text>
            <text
              className="tap-vantage"
              data-testid="tap-vantage"
              x={l0.tap.box.x + l0.tap.box.w + 8}
              y={l0.tap.box.y + l0.tap.box.h / 2}
              dominantBaseline="central"
            >
              {tapLabel(l0.tap)}
            </text>
          </g>
        )}

        {/* Internet pill (O7: the non-local population) -- NOT pinnable. */}
        <Nav
          onActivate={() => onFocus({ kind: "nonlocal" })}
          label={`Internet: ${l0.nonLocalCount} non-local entities`}
        >
          <rect
            className="tier-internet"
            x={l0.internet.x}
            y={l0.internet.y}
            width={l0.internet.w}
            height={l0.internet.h}
            rx={l0.internet.h / 2}
          />
          <text x={l0.internet.x + l0.internet.w / 2} y={l0.internet.y + l0.internet.h / 2} className="tier-label" dominantBaseline="central" textAnchor="middle">
            Internet · {l0.nonLocalCount} external
          </text>
        </Nav>

        {/* WAN edge: drawn when the cast publishes router legs (R5). */}
        {l0.wanEdge && (
          <g>
            {undrawnLegs.length > 0 && (
              <title>{`router legs with no tile on this map (not counted): ${undrawnLegs.join(", ")}`}</title>
            )}
            <rect className="tier-wan" x={l0.wanEdge.x} y={l0.wanEdge.y} width={l0.wanEdge.w} height={l0.wanEdge.h} rx={6} />
            <text x={l0.wanEdge.x + l0.wanEdge.w / 2} y={l0.wanEdge.y + l0.wanEdge.h / 2} className="tier-label" dominantBaseline="central" textAnchor="middle" data-testid="wan-label">
              {wanLabel}
            </text>
          </g>
        )}

        {/* The infra STUBS (R3): each device's short orthogonal vertical down
            to its own group's header. Drawn before the devices and the columns
            so both paint over the ends -- and, unlike the diagonal tie-lines
            they replace (§2 F3), they cross nothing on the way, which
            `noConnectorCrossesATile` checks rather than an eye. */}
        {l0.infraStubs.map((stub) => (
          <polyline
            key={stub.deviceId}
            className="infra-stub"
            data-testid="infra-stub"
            data-device={stub.deviceId}
            data-site={stub.siteKey}
            points={pointsOf(stub.points)}
          />
        ))}

        {/* Infrastructure devices, each over the site column that holds its
            segment (R3). These are HOSTS -- never pinnable (`15` §4) -- so they
            stay plain Nav. A device whose segment this map draws no tile for
            stays centred and dashed: there is no group to sit over, and that is
            a fact about the export, not a rendering accident. */}
        {l0.infraDevices.map((d) => {
          const cx = d.box.x + d.box.w / 2;
          const unplaced = d.siteKey === null;
          return (
            <Nav
              key={d.node.id}
              onActivate={() => go(zoomToPath({ level: "host", imprint: graph.imprintId, key: d.node.id, cidr: d.node.segment ?? undefined }))}
              label={`${d.node.role}: ${d.node.id}${unplaced ? " (its segment has no tile on this map)" : ""}`}
            >
              <rect className={`device${unplaced ? " is-unplaced" : ""} ev-${d.node.roleEvidenceClass ?? "none"}${changeClasses(isNodeChanged(changeSet, d.node.id), filters.changed, changeGlow)}`} x={d.box.x} y={d.box.y} width={d.box.w} height={d.box.h} rx={5} data-testid="infra-device" data-site={d.siteKey ?? ""} />
              <text x={cx} y={d.box.y + 22} className="device-role" textAnchor="middle">{d.node.role}</text>
              <text x={cx} y={d.box.y + 42} className="device-id mono" textAnchor="middle">{shortId(d.node.id)}</text>
            </Nav>
          );
        })}

        {/* Site columns -- the header is a drag handle for the whole site, and
            since S5.1.a it carries the `+` that adds a segment INSIDE this site.
            The `+` is a SIBLING of the `Pinnable`, not a child: nested, its
            pointer-down would begin a pin drag on the site it sits on. Its
            accessible name is the outline's own, and promises no prefill: a
            full prefix, or the misc group, has none to give. */}
        {l0.sites.map((sp) => (
          <g key={sp.site.key}>
            <Pinnable
              pin={pin}
              pinKey={sp.site.key}
              origin={{ x: sp.box.x, y: sp.box.y }}
              label={`Site ${sp.site.isMisc ? "misc" : sp.site.label} -- drag to pin`}
              className="site-drag"
            >
              <rect className="site-box" x={sp.box.x} y={sp.box.y} width={sp.box.w} height={sp.box.h} rx={6} />
              <text x={sp.box.x + 8} y={sp.header.y + 17} className="site-label">
                {sp.site.isMisc ? "misc /16s" : sp.site.label} · {sp.site.segments.length}
                {sp.site.mixed ? " · mixed prefixes (split)" : ""}
              </text>
            </Pinnable>
            <Nav
              onActivate={() => onFocus({ kind: "site-add", siteKey: sp.site.key })}
              label={addSegmentLabel(sp.site)}
            >
              <rect
                className="site-add"
                data-testid="site-add"
                data-site={sp.site.key}
                x={sp.box.x + sp.box.w - CONTROL - 2}
                y={sp.header.y + 1}
                width={CONTROL}
                height={CONTROL}
                rx={4}
              />
              <ControlIcon
                kind="plus"
                x={sp.box.x + sp.box.w - CONTROL - 2}
                y={sp.header.y + 1}
              />
            </Nav>
          </g>
        ))}

        {/* Segment tiles -- draggable to pin, clickable to drill. */}
        {l0.tiles.map((t) => (
          <Tile
            key={t.segment.observedCidr}
            declared={!!props.declared}
            segment={t.segment}
            x={t.box.x}
            y={t.box.y}
            w={t.box.w}
            h={t.box.h}
            dimmed={!tileMatches(t.segment)}
            changeCls={changeClasses(isSegmentChanged(changeSet, t.segment.observedCidr), filters.changed, changeGlow)}
            pin={pin}
            onActivate={() => go(zoomToPath({ level: "segment", imprint: graph.imprintId, cidr: t.segment.observedCidr }))}
            onSelect={() => onFocus({ kind: "segment", cidr: t.segment.observedCidr })}
            selected={t.segment.observedCidr === selectedCidr}
          />
        ))}

        {/* The `+N more` tile (R6): one per over-budget group, in the slot
            after its last drawn tile. NOT pinnable -- it is a control, and a
            pinned projection would be a position saved for a box that exists
            only while the group is collapsed. Clicking toggles `?expand=` for
            that group, so the state is the URL and the back button works. */}
        {l0.overflow.map((more) => (
          <Nav
            key={more.siteKey}
            onActivate={() => go(mapHref({ expand: toggleExpand(filters.expand, more.siteKey) }))}
            label={overflowLabel(more, hiddenVolumeOf(more))}
          >
            <title>{overflowTitle(more, hiddenVolumeOf(more))}</title>
            <rect
              className="tile is-more"
              x={more.box.x}
              y={more.box.y}
              width={more.box.w}
              height={more.box.h}
              rx={6}
              data-testid="overflow-tile"
              data-site={more.siteKey}
              data-hidden={more.hidden.length}
            />
            <text
              x={more.box.x + more.box.w / 2}
              y={more.box.y + 38}
              className="tile-more"
              textAnchor="middle"
            >
              {more.expanded ? "collapse" : `+${more.hidden.length} more`}
            </text>
            <text
              x={more.box.x + more.box.w / 2}
              y={more.box.y + 58}
              className="tile-more-hint"
              textAnchor="middle"
            >
              {more.expanded ? `showing all ${more.total}` : `of ${more.total} segments`}
            </text>
          </Nav>
        ))}

        {/* Unplaced tile (O7) -- a synthetic population, NOT pinnable. */}
        {l0.unplaced && (
          <Nav onActivate={() => onFocus({ kind: "unplaced" })} label={`Unplaced: ${l0.unplaced.count} local entities with no segment`}>
            <rect className="tile is-unplaced" x={l0.unplaced.box.x} y={l0.unplaced.box.y} width={l0.unplaced.box.w} height={l0.unplaced.box.h} rx={6} />
            <text x={l0.unplaced.box.x + l0.unplaced.box.w / 2} y={l0.unplaced.box.y + 34} className="tile-cidr" textAnchor="middle">unplaced</text>
            <text x={l0.unplaced.box.x + l0.unplaced.box.w / 2} y={l0.unplaced.box.y + 58} className="tile-counts" textAnchor="middle">{l0.unplaced.count} local</text>
          </Nav>
        )}

        {/* The east-west band (R4, §4.1's arcs ruling): one arc per segment
            pair, leaving its GROUP's bottom edge at its TILE's x -- never a
            tile's own edge, which is what made the retired lanes drop through
            every row beneath them (§2 F4). Width carries volume; the edit
            colour marks an ASSERTED conduit and a dash marks one the export
            saw no traffic on. Re-anchored to whatever a pin moved. */}
        <g className="arc-band" data-testid="east-west-band" aria-hidden="true">
          {l0.arcs.map((arc) => (
            <path
              key={`${arc.a} ${arc.b}`}
              className={
                `arc${arc.kind === "asserted" ? " is-asserted" : ""}` +
                `${arc.hasTraffic ? "" : " is-silent"}`
              }
              data-testid="east-west-arc"
              data-kind={arc.kind}
              data-pair={`${arc.a} ${arc.b}`}
              data-bytes={arc.bytes}
              d={arcPath(arc)}
              strokeWidth={arc.strokeWidth}
            >
              <title>{arcTitle(arc, anchorLabel, pairByKey.get(`${arc.a} ${arc.b}`))}</title>
            </path>
          ))}
        </g>

        {/* S5.1.b: the placed added segments -- at their pins, drawn over the
            pure layout and reflowing nothing. Pinnable like any segment tile,
            and a click SELECTS rather than drills: an added segment has no
            level 1 yet. Hatched while pending, outlined once saved. Painted
            AFTER the east-west band: a clone is placed wherever the operator
            drops it, often in the band, and an arc drawn across the operator's
            own pending tile made it unreadable (seen in a real browser). */}
        <defs>
          <pattern id={hatchId} patternUnits="userSpaceOnUse" width={6} height={6} patternTransform="rotate(-45)">
            <rect className="hatch-bg" width={6} height={6} />
            <line className="hatch-line" x1={0} y1={0} x2={0} y2={6} />
          </pattern>
        </defs>
        {addedPlaced.drawn.map((d) => {
          const view = addedByCidr.get(d.cidr);
          if (!view) return null;
          return (
            <AddedTile
              key={`added:${d.cidr}`}
              placement={d}
              view={view}
              hatchId={hatchId}
              pin={pin}
              selected={d.cidr === selectedCidr}
              onSelect={() => onFocus({ kind: "segment", cidr: d.cidr })}
            />
          );
        })}

        {/* S5.1.b: the clone not yet staged -- a dashed, hatched proposal while
            it is being placed, and at its chosen position while its form is
            open (unless a collapsed group holds it). Not a control: its form is
            the inspector. */}
        {cloneDraft && !draftHeld && (
          <g
            role="img"
            data-testid="clone-draft-tile"
            data-placed={cloneDraft.placed ? "true" : "false"}
            aria-label={
              cloneDraft.placed
                ? `Clone of ${cloneDraft.source}, placed and not staged yet — its form is in the inspector`
                : `Proposed position for a clone of ${cloneDraft.source} — tap or click the map to place it elsewhere`
            }
          >
            <rect
              className="added-tile is-draft"
              x={cloneDraft.at.x}
              y={cloneDraft.at.y}
              width={TILE_W}
              height={TILE_H}
              rx={6}
              fill={`url(#${hatchId})`}
            />
            <text x={cloneDraft.at.x + 8} y={cloneDraft.at.y + 18} className="tile-cidr mono">
              {cloneDraft.cidr ?? "clone"}
            </text>
            <text x={cloneDraft.at.x + 8} y={cloneDraft.at.y + 34} className="tile-counts">
              {cloneDraft.placed ? "clone · not staged" : "place the clone"}
            </text>
          </g>
        )}

        {/* The Alt-drag ghost (S5.1.b): the source tile stays where it is. */}
        {pin.cloneGhost && (
          <rect
            className="added-tile is-ghost"
            data-testid="clone-ghost"
            aria-hidden="true"
            x={pin.cloneGhost.at.x}
            y={pin.cloneGhost.at.y}
            width={TILE_W}
            height={TILE_H}
            rx={6}
            fill={`url(#${hatchId})`}
          />
        )}

        {/* S5.1.b: while a clone is being PLACED, a tap or click anywhere on the
            map places it there -- centred on the point -- instead of drilling
            into whatever is under it. Drawn last, so it is on top; pointer-only
            by nature, and the inspector's controls are the keyboard path. */}
        {cloneDraft && !cloneDraft.placed && onPlaceClone && (
          <rect
            className="placement-overlay"
            data-testid="placement-overlay"
            aria-hidden="true"
            x={frame.x}
            y={frame.y}
            width={frame.w}
            height={frame.h}
            style={{ pointerEvents: "all" }}
            onClick={(e) => {
              const svg = svgRef.current;
              if (!svg) return;
              const point = layoutPointOf(svg, e.clientX, e.clientY);
              onPlaceClone({ x: Math.round(point.x - TILE_W / 2), y: Math.round(point.y - TILE_H / 2) });
            }}
          />
        )}
      </svg>
    </div>
  );
}

const NO_ADDED: readonly AddedTileView[] = [];

/**
 * A segment the edit store adds, drawn at its placement (S5.1.b). Pinnable --
 * its position is an S4 pin, so dragging it re-places it -- and a click selects
 * it: an added segment has no drawn level 1 to drill into. Its accessible name
 * says what it is: a PENDING CLONE of the segment it copies (or an added
 * segment), and how many hosts are staged on it.
 */
function AddedTile(props: {
  placement: AddedTilePlacement;
  view: AddedTileView;
  hatchId: string;
  pin: PinController;
  selected: boolean;
  onSelect: () => void;
}): JSX.Element {
  const { placement, view, hatchId, pin, selected, onSelect } = props;
  const { box } = placement;
  const state = view.state.charAt(0).toUpperCase() + view.state.slice(1);
  const what = view.cloneOf !== null ? `clone of ${view.cloneOf}` : "added segment";
  const hosts = `${view.hostCount} host${view.hostCount === 1 ? "" : "s"} staged`;
  // N2: overlap is allowed (an S4 pin is absolute), but this tile takes the
  // pointer events of whatever it lands on, so the covered tile is NAMED --
  // in the accessible name and in the hover title alike.
  const covers =
    placement.covers.length > 0
      ? ` — drawn over ${placement.covers.join(", ")}, which it covers on the map`
      : "";
  const session =
    placement.from === "session" ? ", placed in this browser only (its position is not saved)" : "";
  const label = `${state} ${what}: segment ${view.cidr}${view.name ? ` (${view.name})` : ""}, ${hosts}${session}${covers} — select it for its actions`;
  return (
    <Pinnable
      pin={pin}
      pinKey={`segment:${view.cidr}`}
      origin={{ x: box.x, y: box.y }}
      label={label}
      title={label}
      onActivate={onSelect}
      current={selected}
    >
      {/* NOT the `.tile` class: its stylesheet `fill` would override this
          element's `fill` attribute, and the hatch would never be seen in a
          browser (a presentation attribute loses to any CSS rule). */}
      <rect
        className={`added-tile is-${view.state}`}
        data-testid="added-tile"
        data-cidr={view.cidr}
        data-state={view.state}
        x={box.x}
        y={box.y}
        width={box.w}
        height={box.h}
        rx={6}
        fill={view.state === "pending" ? `url(#${hatchId})` : undefined}
      />
      <text x={box.x + 8} y={box.y + 18} className="tile-cidr mono">{view.cidr}</text>
      <text x={box.x + 8} y={box.y + 34} className="tile-counts">
        {view.state} {view.cloneOf !== null ? "clone" : "add"} · {view.hostCount} host{view.hostCount === 1 ? "" : "s"}
      </text>
      {view.cloneOf !== null && (
        <text x={box.x + 8} y={box.y + 48} className="tile-bytes">
          of {view.cloneOf}
        </text>
      )}
      {/* S3: the position is only in this browser -- said ON the tile, because
          reloading loses it and an `aria-label` is not a warning a sighted
          operator ever meets. */}
      {placement.from === "session" && (
        <text x={box.x + 8} y={box.y + box.h - 8} className="tile-unsaved" data-testid="added-tile-unsaved">
          position not saved
        </text>
      )}
    </Pinnable>
  );
}

/**
 * A pinnable, keyboard-accessible SVG group: a pointer-drag repositions it
 * (S4), a plain click / Enter / Space activates it (drill). The drag→pin math
 * is `usePinDrag`'s; a real drag swallows the click that follows so it does
 * not also drill. `data-pinnable` names the pin key -- only `site:`/`segment:`
 * elements carry it, which is how a test proves a host has none (`15` §4).
 */
function Pinnable(props: {
  pin: PinController;
  pinKey: string;
  origin: Point;
  label: string;
  /** A hover tooltip, when there is something the label says that the drawing
   *  cannot (review N2's overlap disclosure). */
  title?: string;
  onActivate?: () => void;
  className?: string;
  /** The inspector is acting on this element (S5.1.a). */
  current?: boolean;
  /** An Alt-drag of this element is a CLONE of it (S5.1.b) -- an observed
   *  segment tile only. */
  clonable?: boolean;
  children: ReactNode;
}): JSX.Element {
  const { pin, pinKey, origin, onActivate } = props;
  const dragging = pin.draggedKey === pinKey;
  return (
    <g
      role="button"
      tabIndex={0}
      aria-label={props.label}
      aria-current={props.current ? "true" : undefined}
      data-pinnable={pinKey}
      className={`nav pinnable${dragging ? " is-dragging" : ""}${props.current ? " is-selected" : ""}${props.className ? ` ${props.className}` : ""}`}
      onPointerDown={(e: ReactPointerEvent) => pin.begin(pinKey, origin, e, props.clonable === true)}
      onClick={(e) => {
        // A real drag already fired its commit; swallow the trailing click so
        // a drag-to-pin does not also drill.
        if (pin.consumeDragClick(pinKey)) {
          e.preventDefault();
          e.stopPropagation();
          return;
        }
        onActivate?.();
      }}
      onKeyDown={(e) => {
        if (onActivate && (e.key === "Enter" || e.key === " ")) {
          e.preventDefault();
          onActivate();
        }
      }}
    >
      {props.title !== undefined && <title>{props.title}</title>}
      {props.children}
    </g>
  );
}

/**
 * A segment tile. Clicking it DRILLS (unchanged since S3); the select control
 * in its top-right corner SELECTS it in place (S5.1.a), putting the segment's
 * facts and actions in the inspector without leaving the level-0 overview --
 * the point of acting from a map rather than from a drill-down.
 *
 * The control is a SIBLING of the `Pinnable`, never a child: nested, its
 * pointer-down would begin a pin drag and its click would bubble to the drill.
 * Drawn after the tile, its 24×24 hit area takes the tile's empty corner OUT of
 * the drill target instead of overlapping it -- one pointer event, one owner.
 */
function Tile(props: {
  segment: GraphSegment;
  x: number;
  y: number;
  w: number;
  h: number;
  dimmed: boolean;
  changeCls: string;
  pin: PinController;
  onActivate: () => void;
  onSelect: () => void;
  selected: boolean;
  /** A declared design: no measured volume, ghosts or occupancy to state. */
  declared?: boolean;
}): JSX.Element {
  const { segment, x, y, w, h, dimmed, changeCls, pin, onActivate, selected } = props;
  const occ = segment.occupancy === null ? "—" : formatPercent(segment.occupancy);
  // What the badge row leaves out -- derived from the two arrays the model
  // already publishes rather than stored a third time.
  const hiddenRoles = segment.roleHistogram.length - segment.roleBadges.length;
  return (
    <>
      <Pinnable
        pin={pin}
        pinKey={`segment:${segment.observedCidr}`}
        origin={{ x, y }}
        // R3: the badges are `aria-hidden` (three interleaved title nodes per
        // tile, times forty tiles, is not a reading order), so the roles they
        // carry reach assistive tech here, in the tile's own one sentence -- and
        // that sentence must make the same PROJECTION disclosure the badge row
        // makes on hover (review S2), or the one user who cannot see the tile is
        // the only one told that three roles are the whole story.
        label={
          `Segment ${segment.observedCidr}, ${segment.hostCount} hosts` +
          (props.declared
            ? " declared in the twin config (would deploy)"
            : `, ${formatBytes(segment.northSouth.bytes)} north-south and ` +
              `${formatBytes(segment.eastWest.bytes)} east-west in this window`) +
          (segment.roleBadges.length > 0
            ? `, roles: ${segment.roleBadges
                .map((b) => `${roleDisplayName(b.role)} ${b.count}`)
                .join(", ")}`
            : "") +
          (hiddenRoles > 0 ? `, +${hiddenRoles} more roles` : "")
        }
        onActivate={onActivate}
        current={selected}
        clonable
      >
        <rect
          className={`tile${segment.hasRouterLeg ? " has-router" : ""}${dimmed ? " is-dimmed" : ""}${changeCls}`}
          x={x}
          y={y}
          width={w}
          height={h}
          rx={6}
          style={{ opacity: saturationFor(segment.occupancy) }}
          data-testid="segment-tile"
        />
        <text x={x + 8} y={y + 18} className="tile-cidr mono">{segment.observedCidr}</text>
        <text x={x + 8} y={y + 34} className="tile-counts">
          {props.declared
            ? `${segment.deployedCount} would deploy · declared`
            : `${segment.deployedCount} would deploy · ${segment.ghostCount} ghost · ${occ}`}
        </text>
        {/* R6: the byte figure the reference report prints and this tile did not
            -- both directions, in this window set, never a rate (§8). A
            declared design has none, and says nothing rather than "0 B". */}
        {!props.declared && (
          <text x={x + 8} y={y + 48} className="tile-bytes" data-testid="tile-bytes">
            {tileVolumeLine(segment)}
          </text>
        )}
        <RoleBadges badges={segment.roleBadges} hidden={hiddenRoles} x={x + 8} y={y + 56} w={w - 16} />
        <EvidenceRing mix={segment.evidenceMix} x={x + 8} y={y + 76} w={w - 16} h={8} />
      </Pinnable>
      <Nav
        onActivate={props.onSelect}
        label={`Select segment ${segment.observedCidr} — its facts and actions open in the inspector, and the map stays where it is`}
      >
        <rect
          className="tile-actions"
          data-testid="tile-actions"
          data-cidr={segment.observedCidr}
          x={x + w - CONTROL - 2}
          y={y + 2}
          width={CONTROL}
          height={CONTROL}
          rx={4}
        />
        <ControlIcon kind="pencil" x={x + w - CONTROL - 2} y={y + 2} />
      </Nav>
    </>
  );
}

/** The on-map select and add controls' hit target: 24×24 layout units -- the
 *  WCAG 2.2 minimum (2.5.8) at the map's default 1:1 scale. */
const CONTROL = 24;

/** A 16-unit stroked icon centred in a control's 24×24 box. Drawn as a path,
 *  never a text glyph: `✎` has no font on some hosts and rendered as an empty
 *  square. `aria-hidden` -- the control's `Nav` carries the accessible name. */
function ControlIcon(props: { kind: "pencil" | "plus"; x: number; y: number }): JSX.Element {
  const d =
    props.kind === "pencil"
      ? "M3 13v-2.5L10.5 3 13 5.5 5.5 13z M9 4.5l2.5 2.5"
      : "M8 3v10 M3 8h10";
  return (
    <path
      className="control-icon"
      data-testid={`control-icon-${props.kind}`}
      aria-hidden="true"
      transform={`translate(${props.x + (CONTROL - 16) / 2} ${props.y + (CONTROL - 16) / 2})`}
      d={d}
    />
  );
}

function shortId(id: string): string {
  const at = id.indexOf(":");
  return at < 0 ? id : id.slice(at + 1);
}

/**
 * The SVG viewBox that CONTAINS every drawn element -- the pure canvas, grown
 * to hold any tile or site a pin (or an in-progress drag) pushed past it, so a
 * dragged element never clips out of view. With no pins every tile and site is
 * already inside `[0..width] x [0..height]`, so this returns `{0, 0, width,
 * height}` unchanged -- the no-pins render is untouched. Only the pinnable
 * elements (tiles, sites) can move; the chrome never does, and an east-west
 * arc is anchored to a tile and a site box, both of which are included here.
 */
function frameFor(l0: Level0Layout, extra: readonly Box[] = []): { x: number; y: number; w: number; h: number } {
  let minX = 0;
  let minY = 0;
  let maxX = l0.width;
  let maxY = l0.height;
  const consider = (b: Box): void => {
    minX = Math.min(minX, b.x - CANVAS_PAD);
    minY = Math.min(minY, b.y - CANVAS_PAD);
    maxX = Math.max(maxX, b.x + b.w + CANVAS_PAD);
    maxY = Math.max(maxY, b.y + b.h + CANVAS_PAD);
  };
  for (const t of l0.tiles) consider(t.box);
  for (const s of l0.sites) consider(s.box);
  // S5.1.b: a placed added segment and the clone draft sit at absolute
  // positions of their own, so the frame grows to contain them as it does a pin.
  for (const b of extra) consider(b);
  return { x: minX, y: minY, w: maxX - minX, h: maxY - minY };
}
