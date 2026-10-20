/**
 * Level 1 -- the spine drill, rendered from `layout1`'s pure coordinates
 * (`docs/design/15-console-canvas.md` §4). Role bands off the left (chips, or
 * a rung summary when collapsed), the spine and its router leg in the middle,
 * conduit peers and the gateway on the right. A chip drills to its host; a
 * peer drills to its segment.
 *
 * **S5.1.d: hosts this FILE adds are hatched chips.** A pending `add: host`
 * gets a chip of its own in a band below the observed ones, in the same
 * *pending* state the map's tiles use (`15` §8) -- so the hosts of a clone or a
 * generated segment are visible where their hosts belong, not only in the
 * change list. A segment the file adds has no observed topology at all, so it
 * gets `PendingSegmentLevel1`: the same chips around a hatched spine.
 *
 * The pending band's geometry is computed HERE, in the view, and never in
 * `layout1` (LES-268): the pure layout stays the golden-tested function of the
 * observed graph, and with no added hosts the render is byte-identical to
 * before.
 */
import { useMemo } from "react";

import type { ConsoleGraph } from "../model/graph";
import { anyFilterActive, nodePassesFilters } from "../model/graph";
import { EMPTY_CHANGE_SET, isNodeChanged, type ChangeSet } from "../model/changes";
import { evidenceClassName, saturationFor } from "../model/evidence";
import type { Filters } from "../model/zoom";
import { zoomToPath } from "../model/zoom";
import {
  BAND_COL_W,
  BAND_GAP_Y,
  BAND_LABEL_H,
  BAND_PAD,
  CHIP_GAP_X,
  CHIP_GAP_Y,
  CHIP_H,
  CHIP_W,
  CHIPS_PER_ROW,
  RUNG_ROW_H,
  type Box,
} from "../layout/geometry";
import { layout1 } from "../layout/level1";
import { STATE_LABEL } from "./ChangeList";
import { changeClasses, Nav } from "./mapParts";
import type { Focus } from "./selection";

export interface Level1Props {
  graph: ConsoleGraph;
  /** A declared design: no ghosts to count, and hosts are named by their node
   *  id (their `hostname:` key prefix carries nothing a chip needs). */
  declared?: boolean;
  filters: Filters;
  cidr: string;
  /** S5.1.a: select an element in place. The SPINE is this level's subject --
   *  clicking it puts the segment's facts and actions in the inspector. (The
   *  segment is already selected on arrival, since the zoom names it; the
   *  gesture exists so the spine is a real affordance rather than decoration,
   *  so it brings the segment back after the outline's site `+` took the one
   *  selection slot, and so a keyboard select moves focus to the actions.)
   *  Absent ⇒ a read-only render, which is what the unit tests drive. */
  onFocus?: (focus: Focus) => void;
  /** S5.1.a: the inspector is acting on this segment -- the spine is marked
   *  (`aria-current` and `is-selected`). False when the site `+` has the slot. */
  selected?: boolean;
  /** S6: the last apply's map-highlightable change set; a chip whose node id
   *  is in it is marked (`is-changed` under `?changed=1`, glow for 5s). */
  changeSet?: ChangeSet;
  changeGlow?: boolean;
  /** S5.1.d: the hosts this file ADDS on this segment, in store order.
   *  Absent/empty ⇒ the render is exactly the observed one. */
  added?: readonly PendingChipView[];
  go: (path: string) => void;
}

/** A host this file adds, as level 1 draws it (S5.1.d). */
export interface PendingChipView {
  /** The record's key -- `address:10.80.31.10`, or a hostname key. */
  key: string;
  role: string | null;
  state: "pending" | "saved" | "applied";
}

/** The band's state is the WEAKEST of its chips' -- the same rule a gesture
 *  group uses (`edit/gestures.ts`), so one saved chip cannot make a band of
 *  pending ones read as saved. */
function bandState(chips: readonly PendingChipView[]): "pending" | "saved" | "applied" {
  const rank = { pending: 0, saved: 1, applied: 2 } as const;
  let state: "pending" | "saved" | "applied" = "applied";
  for (const c of chips) if (rank[c.state] < rank[state]) state = c.state;
  return state;
}

/** The pending band's own geometry, laid out beside the observed bands. */
function pendingBandBox(chips: number, top: number, x: number): Box {
  const rows = Math.max(1, Math.ceil(chips / CHIPS_PER_ROW));
  return {
    x,
    y: top,
    w: BAND_COL_W,
    h: BAND_LABEL_H + 2 * BAND_PAD + rows * CHIP_H + (rows - 1) * CHIP_GAP_Y,
  };
}

/** The `i`-th chip inside a band box. */
function pendingChipBox(box: Box, i: number): Box {
  const col = i % CHIPS_PER_ROW;
  const row = Math.floor(i / CHIPS_PER_ROW);
  return {
    x: box.x + BAND_PAD + col * (CHIP_W + CHIP_GAP_X),
    y: box.y + BAND_LABEL_H + BAND_PAD + row * (CHIP_H + CHIP_GAP_Y),
    w: CHIP_W,
    h: CHIP_H,
  };
}

/**
 * The band of hatched chips for the hosts this file adds (S5.1.d).
 *
 * NOT the `.chip` class: its stylesheet rule sets `fill`, which would beat the
 * hatch pattern set as a presentation attribute and flatten every one of these
 * in a browser while every jsdom assertion still passed (LES-327). `.added-chip`
 * sets no `fill`, and `test/pending.test.ts` guards that.
 */
function PendingBand(props: { chips: readonly PendingChipView[]; box: Box; hatchId: string }): JSX.Element {
  const { chips, box, hatchId } = props;
  return (
    <g data-testid="pending-band">
      <rect className="band is-pending" x={box.x} y={box.y} width={box.w} height={box.h} rx={6} />
      {/* S6: the project's OWN wording for each state (`ChangeList`'s
          `STATE_LABEL`). "added by this file" claimed the file held records
          that are browser-only -- the one thing *pending* means it does not. */}
      <text x={box.x + 8} y={box.y + 15} className="band-label">
        added here · {chips.length} · {STATE_LABEL[bandState(chips)]}
      </text>
      {chips.map((chip, i) => {
        const cb = pendingChipBox(box, i);
        return (
          <g
            key={chip.key}
            role="img"
            aria-label={`${chip.state} added host ${chip.key}${chip.role ? ` as ${chip.role}` : ""} — this file adds it; it is not in the observed topology`}
          >
            <rect
              className={`added-chip is-${chip.state}`}
              data-testid="pending-chip"
              data-key={chip.key}
              data-state={chip.state}
              x={cb.x}
              y={cb.y}
              width={cb.w}
              height={cb.h}
              rx={4}
              fill={chip.state === "pending" ? `url(#${hatchId})` : undefined}
            />
            <text x={cb.x + 6} y={cb.y + cb.h / 2} className="chip-label mono" dominantBaseline="central">
              {chipLabel(chip.key.replace(/^address:/, ""))}
            </text>
          </g>
        );
      })}
    </g>
  );
}

export function Level1(props: Level1Props): JSX.Element {
  const { graph, filters, cidr, go } = props;
  const changeSet = props.changeSet ?? EMPTY_CHANGE_SET;
  const changeGlow = props.changeGlow ?? false;
  const l1 = useMemo(() => layout1(graph, cidr), [graph, cidr]);

  if (!l1) {
    return (
      <div className="map-scroll">
        <p className="notice is-error" data-testid="no-segment">
          no segment <code>{cidr}</code> in this twin
        </p>
      </div>
    );
  }

  const dim = anyFilterActive(filters);
  const segment = l1.segment;
  // S5.1.d: the pending band hangs BELOW the observed bands, in the same
  // column, and grows the viewBox rather than the pure layout (LES-268). With
  // no added hosts nothing here changes a coordinate.
  const added = props.added ?? NO_PENDING;
  const bandsBottom = l1.bands.reduce((max, b) => Math.max(max, b.box.y + b.box.h), 0);
  const bandX = l1.bands[0]?.box.x ?? 0;
  const pendingBox = added.length > 0 ? pendingBandBox(added.length, bandsBottom + BAND_GAP_Y, bandX) : null;
  const height = pendingBox ? Math.max(l1.height, pendingBox.y + pendingBox.h + BAND_GAP_Y) : l1.height;
  const hatchId = "l1-pending-hatch";

  return (
    <div className="map-scroll">
      <svg
        className="level1"
        viewBox={`0 0 ${l1.width} ${height}`}
        width={l1.width}
        height={height}
        role="img"
        aria-label={`Level 1 spine for ${cidr}`}
        data-testid="level1-svg"
      >
        <defs>
          <PendingHatch id={hatchId} />
        </defs>
        {/* Spokes first, under everything. */}
        {l1.bands.map((b) => (
          <line
            key={`spoke-${b.role}`}
            className="spoke"
            x1={b.spoke.from.x}
            y1={b.spoke.from.y}
            x2={b.spoke.to.x}
            y2={b.spoke.to.y}
          />
        ))}

        {/* The spine -- this level's SUBJECT (S5.1.a): selecting it opens the
            segment's facts and actions in the inspector. */}
        <Nav
          onActivate={() => props.onFocus?.({ kind: "segment", cidr })}
          label={`Select segment ${segment.observedCidr} — its facts and actions open in the inspector`}
          current={props.selected ?? false}
        >
          <rect className="spine" data-testid="spine" x={l1.spine.x} y={l1.spine.y} width={l1.spine.w} height={l1.spine.h} rx={8} />
          <text x={l1.spine.x + l1.spine.w / 2} y={l1.spine.y + 22} className="spine-cidr mono" textAnchor="middle">{segment.observedCidr}</text>
          <text x={l1.spine.x + l1.spine.w / 2} y={l1.spine.y + 42} className="spine-meta" textAnchor="middle">{segment.hostCount} hosts</text>
          <text x={l1.spine.x + l1.spine.w / 2} y={l1.spine.y + 60} className="spine-meta" textAnchor="middle">{props.declared ? "declared" : `${segment.ghostCount} ghost`}</text>
          {segment.deployedCidr && (
            <text x={l1.spine.x + l1.spine.w / 2} y={l1.spine.y + 80} className="spine-meta" textAnchor="middle">→ {segment.deployedCidr}</text>
          )}
        </Nav>

        {/* Router leg. */}
        {l1.routerLeg && (
          <g>
            <rect className="router-leg" x={l1.routerLeg.x} y={l1.routerLeg.y} width={l1.routerLeg.w} height={l1.routerLeg.h} rx={5} />
            <text x={l1.routerLeg.x + l1.routerLeg.w / 2} y={l1.routerLeg.y + l1.routerLeg.h / 2} className="router-label" dominantBaseline="central" textAnchor="middle">router leg</text>
          </g>
        )}

        {/* Role bands. */}
        {l1.bands.map((b) => (
          <g key={b.role} data-testid={`band-${b.role}`}>
            <rect className={`band${b.role === "below-threshold" ? " is-below" : ""}`} x={b.box.x} y={b.box.y} width={b.box.w} height={b.box.h} rx={6} />
            <text x={b.box.x + 8} y={b.box.y + 15} className="band-label">
              {b.role} · {b.count}
              {b.collapsed ? " · collapsed" : ""}
            </text>
            {b.collapsed
              ? b.rungs.map((r, i) => (
                  <text key={r.rung} x={b.box.x + 12} y={b.box.y + BAND_LABEL_H + 8 + i * RUNG_ROW_H + 14} className="rung-row">
                    {r.rung}: {r.count}
                  </text>
                ))
              : b.chips.map((chip) => {
                  const passes = !dim || nodePassesFilters(chip.node, filters);
                  return (
                    <Nav
                      key={chip.node.id}
                      onActivate={() => go(zoomToPath({ level: "host", imprint: graph.imprintId, key: chip.node.id, cidr }))}
                      label={`${chip.node.role ?? "unroled"}: ${chip.node.id}`}
                    >
                      <rect
                        className={`chip ${evidenceClassName(chip.node.roleEvidenceClass)}${chip.node.isGhost ? " is-ghost" : ""}${passes ? "" : " is-dimmed"}${changeClasses(isNodeChanged(changeSet, chip.node.id), filters.changed, changeGlow)}`}
                        x={chip.box.x}
                        y={chip.box.y}
                        width={chip.box.w}
                        height={chip.box.h}
                        rx={4}
                        style={{ fillOpacity: saturationFor(chip.node.roleConfidence) }}
                        data-testid="host-chip"
                      />
                      <text x={chip.box.x + 6} y={chip.box.y + chip.box.h / 2} className="chip-label mono" dominantBaseline="central">
                        {chipLabel(chip.node.address ?? (props.declared ? chip.node.id.replace(/^hostname:/, "") : chip.node.id))}
                      </text>
                    </Nav>
                  );
                })}
          </g>
        ))}

        {pendingBox && <PendingBand chips={added} box={pendingBox} hatchId={hatchId} />}

        {/* Peers on the right. */}
        {l1.peers.map((peer) => (
          <Nav
            key={peer.name}
            onActivate={() => (peer.cidr ? go(zoomToPath({ level: "segment", imprint: graph.imprintId, cidr: peer.cidr })) : undefined)}
            label={peer.kind === "gateway" ? "gateway egress" : `conduit peer ${peer.label}`}
          >
            <rect className={`peer is-${peer.kind}`} x={peer.box.x} y={peer.box.y} width={peer.box.w} height={peer.box.h} rx={5} data-testid={`peer-${peer.kind}`} />
            <text x={peer.box.x + peer.box.w / 2} y={peer.box.y + peer.box.h / 2} className="peer-label mono" dominantBaseline="central" textAnchor="middle">{peer.label}</text>
          </Nav>
        ))}
      </svg>
    </div>
  );
}

function chipLabel(value: string): string {
  return value.length > 18 ? `${value.slice(0, 17)}…` : value;
}

const NO_PENDING: readonly PendingChipView[] = [];

/** The hatch pattern the *pending* state is drawn with -- the same diagonal the
 *  level-0 tiles use, so one visual means one thing across both levels. */
export function PendingHatch({ id }: { id: string }): JSX.Element {
  return (
    <pattern id={id} width={6} height={6} patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
      <rect width={6} height={6} className="hatch-bg" />
      <line x1={0} y1={0} x2={0} y2={6} className="hatch-line" />
    </pattern>
  );
}

/**
 * Level 1 for a segment this FILE adds (S5.1.d). The observed topology has no
 * such segment, so there is no `layout1` to render: this draws the hatched
 * spine and the hosts staged on it, which is everything there is to know about
 * it until it is applied.
 *
 * It replaced a notice that said level 1 "does not draw added segments" -- true
 * when it was written, and the thing this slice was for.
 */
export function PendingSegmentLevel1(props: {
  cidr: string;
  name: string;
  state: "pending" | "saved" | "applied";
  chips: readonly PendingChipView[];
  /** S5.1.a: select the segment in place -- the spine is the subject here too. */
  onFocus?: (focus: Focus) => void;
  selected?: boolean;
}): JSX.Element {
  const { cidr, name, state, chips } = props;
  const hatchId = "l1-added-hatch";
  const spine: Box = { x: BAND_COL_W + 80, y: 40, w: 180, h: 110 };
  const box = pendingBandBox(Math.max(chips.length, 1), 40, 24);
  const width = spine.x + spine.w + 80;
  const height = Math.max(spine.y + spine.h, box.y + box.h) + 40;
  return (
    <div className="map-scroll">
      <svg
        className="level1"
        viewBox={`0 0 ${width} ${height}`}
        width={width}
        height={height}
        role="img"
        aria-label={`Level 1 for ${cidr}, a segment this file adds`}
        data-testid="level1-svg"
      >
        <defs>
          <PendingHatch id={hatchId} />
        </defs>
        <Nav
          onActivate={() => props.onFocus?.({ kind: "segment", cidr })}
          label={`Select segment ${cidr} — this file adds it; its facts and actions open in the inspector`}
          current={props.selected ?? false}
        >
          <rect
            className={`added-tile is-${state}`}
            data-testid="added-spine"
            data-cidr={cidr}
            data-state={state}
            x={spine.x}
            y={spine.y}
            width={spine.w}
            height={spine.h}
            rx={8}
            fill={state === "pending" ? `url(#${hatchId})` : undefined}
          />
          <text x={spine.x + spine.w / 2} y={spine.y + 26} className="spine-cidr mono" textAnchor="middle">{cidr}</text>
          <text x={spine.x + spine.w / 2} y={spine.y + 48} className="spine-meta" textAnchor="middle">{name || "—"}</text>
          <text x={spine.x + spine.w / 2} y={spine.y + 68} className="spine-meta" textAnchor="middle">{STATE_LABEL[state]}</text>
          <text x={spine.x + spine.w / 2} y={spine.y + 88} className="spine-meta" textAnchor="middle">
            {chips.length} host{chips.length === 1 ? "" : "s"} staged
          </text>
        </Nav>
        {chips.length > 0 ? (
          <PendingBand chips={chips} box={box} hatchId={hatchId} />
        ) : (
          <text x={24} y={60} className="band-label" data-testid="added-no-hosts">
            no hosts staged on it yet — Add host here, in the inspector
          </text>
        )}
      </svg>
    </div>
  );
}
