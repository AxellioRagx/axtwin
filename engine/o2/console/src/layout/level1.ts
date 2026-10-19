/**
 * Level 1 -- the spine drill (`docs/design/15-console-canvas.md` §4).
 *
 * A segment is a spine in the middle; its router leg sits on top; its hosts
 * hang off the **left** in **role bands in the fixed role order** (two chips
 * per row, sorted by address, a spoke from each band to the spine); its
 * conduit peers and the way off the segment hang off the **right**; and a
 * *below threshold* band collects the ghosts pulled out of their role bands
 * (§4). A band over the chip budget collapses by occupancy rung (O6).
 *
 * Pure -- `layout1(graph, observedCidr)` is a function of the graph and the
 * segment's identity; hosts are never pinnable (§4), which is what makes this
 * level dense and stable.
 */
import type { ConsoleGraph, GraphNode, GraphSegment } from "../model/graph";
import { bandCollapses, tallyByRung, type RungTally } from "../model/budgets";
import { orderRoles, roleBucket } from "../model/roles";
import {
  BAND_COL_W,
  BAND_GAP_Y,
  BAND_LABEL_H,
  BAND_PAD,
  CANVAS_PAD,
  CHIP_GAP_X,
  CHIP_GAP_Y,
  CHIP_H,
  CHIP_W,
  CHIPS_PER_ROW,
  PEER_GAP_Y,
  PEER_H,
  PEER_W,
  ROUTER_LEG_H,
  ROUTER_LEG_W,
  RUNG_ROW_H,
  SPINE_GAP_X,
  SPINE_W,
  type Box,
} from "./geometry";
import { stackBands } from "./tiers";

export const BELOW_THRESHOLD = "below-threshold";

export interface Chip {
  node: GraphNode;
  box: Box;
}

export interface BandLayout {
  /** A role bucket, or the `below-threshold` sentinel. */
  role: string;
  box: Box;
  /** The point on the spine this band's spoke connects to. */
  spoke: { from: { x: number; y: number }; to: { x: number; y: number } };
  count: number;
  collapsed: boolean;
  /** Present when not collapsed. */
  chips: Chip[];
  /** Present when collapsed (O6): a count per occupancy rung. */
  rungs: RungTally[];
}

export interface PeerLayout {
  /** A conduit peer's segment name, or the gateway sentinel `__gateway__`. */
  name: string;
  label: string;
  cidr: string | null;
  kind: "conduit" | "gateway";
  box: Box;
}

export interface Level1Layout {
  width: number;
  height: number;
  segment: GraphSegment;
  spine: Box;
  routerLeg: Box | null;
  bands: BandLayout[];
  peers: PeerLayout[];
}

export const GATEWAY_PEER = "__gateway__";

function compareByAddress(a: GraphNode, b: GraphNode): number {
  const ka = a.address ?? a.id;
  const kb = b.address ?? b.id;
  return ka < kb ? -1 : ka > kb ? 1 : a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
}

function bandBodyHeight(chipCount: number, collapsed: boolean, rungCount: number): number {
  if (collapsed) return rungCount * RUNG_ROW_H;
  const rows = Math.max(1, Math.ceil(chipCount / CHIPS_PER_ROW));
  return rows * CHIP_H + (rows - 1) * CHIP_GAP_Y;
}

interface BandSpec {
  role: string;
  hosts: GraphNode[];
  collapsed: boolean;
  rungs: RungTally[];
}

export function layout1(graph: ConsoleGraph, observedCidr: string): Level1Layout | null {
  const segment = graph.segmentByCidr.get(observedCidr);
  if (!segment) return null;

  // Live hosts band by role; ghosts collect in the below-threshold band.
  const live = segment.hosts.filter((h) => !h.isGhost);
  const ghosts = segment.hosts.filter((h) => h.isGhost);

  const byRole = new Map<string, GraphNode[]>();
  for (const h of live) {
    const bucket = roleBucket(h.role, h.locality);
    const list = byRole.get(bucket) ?? [];
    list.push(h);
    byRole.set(bucket, list);
  }

  const specs: BandSpec[] = orderRoles(byRole.keys()).map((role) => {
    const hosts = (byRole.get(role) ?? []).slice().sort(compareByAddress);
    const collapsed = bandCollapses(hosts.length);
    return {
      role,
      hosts,
      collapsed,
      rungs: collapsed ? tallyByRung(hosts.map((h) => h.occupancy)) : [],
    };
  });

  if (ghosts.length > 0) {
    const collapsed = bandCollapses(ghosts.length);
    specs.push({
      role: BELOW_THRESHOLD,
      hosts: ghosts.slice().sort(compareByAddress),
      collapsed,
      rungs: collapsed ? tallyByRung(ghosts.map((h) => h.occupancy)) : [],
    });
  }

  // Vertical bands on the left.
  const bandHeights = specs.map(
    (s) => BAND_LABEL_H + bandBodyHeight(s.hosts.length, s.collapsed, s.rungs.length) + 2 * BAND_PAD,
  );
  const placedBands = stackBands(bandHeights, CANVAS_PAD, BAND_GAP_Y);

  const bandX = CANVAS_PAD;
  const spineX = bandX + BAND_COL_W + SPINE_GAP_X;
  const spineTop = CANVAS_PAD + ROUTER_LEG_H + BAND_GAP_Y;
  const bandsBottom = placedBands.length
    ? Math.max(...placedBands.map((b) => b.y + b.h))
    : spineTop;

  const bands: BandLayout[] = specs.map((spec, i) => {
    const band = placedBands[i]!;
    const box: Box = { x: bandX, y: band.y, w: BAND_COL_W, h: band.h };
    const bodyTop = band.y + BAND_LABEL_H + BAND_PAD;
    const chips: Chip[] = spec.collapsed
      ? []
      : spec.hosts.map((node, j) => {
          const col = j % CHIPS_PER_ROW;
          const row = Math.floor(j / CHIPS_PER_ROW);
          return {
            node,
            box: {
              x: bandX + BAND_PAD + col * (CHIP_W + CHIP_GAP_X),
              y: bodyTop + row * (CHIP_H + CHIP_GAP_Y),
              w: CHIP_W,
              h: CHIP_H,
            },
          };
        });
    const midY = band.y + band.h / 2;
    return {
      role: spec.role,
      box,
      spoke: { from: { x: box.x + box.w, y: midY }, to: { x: spineX, y: midY } },
      count: spec.hosts.length,
      collapsed: spec.collapsed,
      chips,
      rungs: spec.rungs,
    };
  });

  const spineBottom = Math.max(bandsBottom, spineTop + 120);
  const spine: Box = { x: spineX, y: spineTop, w: SPINE_W, h: spineBottom - spineTop };
  const routerLeg: Box | null = segment.hasRouterLeg
    ? { x: spineX + (SPINE_W - ROUTER_LEG_W) / 2, y: CANVAS_PAD, w: ROUTER_LEG_W, h: ROUTER_LEG_H }
    : null;

  // Peers on the right: conduit neighbours (in name order), then the gateway.
  const peerNames = new Set<string>();
  for (const c of graph.conduits) {
    if (c.a === segment.name) peerNames.add(c.b);
    else if (c.b === segment.name) peerNames.add(c.a);
  }
  const peersX = spineX + SPINE_W + SPINE_GAP_X;
  let peerY = spineTop;
  const peers: PeerLayout[] = [];
  for (const name of [...peerNames].sort()) {
    const neighbour = graph.segmentByName.get(name);
    peers.push({
      name,
      label: neighbour?.observedCidr ?? name,
      cidr: neighbour?.observedCidr ?? null,
      kind: "conduit",
      box: { x: peersX, y: peerY, w: PEER_W, h: PEER_H },
    });
    peerY += PEER_H + PEER_GAP_Y;
  }
  if (segment.hasRouterLeg) {
    peers.push({
      name: GATEWAY_PEER,
      label: "gateway → egress",
      cidr: null,
      kind: "gateway",
      box: { x: peersX, y: peerY, w: PEER_W, h: PEER_H },
    });
    peerY += PEER_H + PEER_GAP_Y;
  }

  const width = peersX + PEER_W + CANVAS_PAD;
  const height = Math.max(spine.y + spine.h, peerY, bandsBottom) + CANVAS_PAD;
  return { width, height, segment, spine, routerLeg, bands, peers };
}
