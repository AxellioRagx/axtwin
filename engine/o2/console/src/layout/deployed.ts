/**
 * The DEPLOYED twin's map -- the Twin area's drawing of `model/deployed.ts`
 * (ruling §2 rule 2: the state-store inventory adapted into this layout
 * engine; the IR re-parse path is not used for a deployed view).
 *
 * Two levels, the same semantic zoom the Design map uses:
 *
 *   level 0 -- Internet -> router(s) -> segment groups -> segment tiles.
 *              A tile aggregates a segment (hosts, personas, status); groups
 *              over a budget collapse behind a `+N more` tile.
 *   level 1 -- one segment drilled (`?segment=`): its hosts as chips in role
 *              bands, the devices it shares with other segments first.
 *
 * **Groups stack vertically** and the trunk is a vertical bus on the left with
 * one horizontal leg into each group's header that a router reaches. That is
 * a deliberate difference from the Design map's horizontal site columns: a
 * deployed twin like corp-twin has ~50 nets, and a row of columns 3000 px wide
 * is the known limit that map still carries (README: "column wrapping"). A
 * stack keeps the plane ~1300 px wide whatever the net count, and a leg that
 * enters a header's LEFT edge crosses no tile by construction.
 *
 * Reused, not restated (LES-025): the site rule (`sites.ts`), the tile grid
 * slot (`level0.ts`'s `columnSlot`), the column sizes (`columns.ts`), the band
 * stacker (`tiers.ts`) and every dimension (`geometry.ts`). Pure: no React.
 */
import type { DeployedSegment, DeployedTwin } from "../model/deployed";
import { columnHeightFor, columnWidthFor } from "./columns";
import {
  BAND_GAP_Y,
  BAND_LABEL_H,
  BAND_PAD,
  CANVAS_PAD,
  CHIP_GAP_X,
  CHIP_GAP_Y,
  DEVICE_GAP_X,
  DEVICE_GAP_Y,
  DEVICE_H,
  DEVICE_W,
  INTERNET_W,
  RAIL_GAP_X,
  RAIL_W,
  SITE_HEADER_H,
  TIER_GAP_Y,
  TIER_INTERNET_H,
  type Box,
  type Point,
} from "./geometry";
import { columnSlot } from "./level0";
import { groupIntoSites } from "./sites";
import { stackBands } from "./tiers";

/** Tiles across a group -- fixed, so every group has one width. */
export const DEPLOYED_COLS = 6;
/** Tiles a group draws before the rest collapse behind `+N more`: two rows
 *  of `DEPLOYED_COLS`, one slot of which is the `+N more` tile. */
export const DEPLOYED_BUDGET = 2 * DEPLOYED_COLS - 1;
/** How far right of the content edge the groups start: the bus runs here. */
export const BUS_INSET = 28;
/** A host chip at level 1 (wider than the Design chip: host ids are long). */
export const HOST_CHIP_W = 188;
export const HOST_CHIP_H = 34;
export const HOST_CHIPS_PER_ROW = 5;

export const LINKS_GROUP = "group:links";
export const UNADDRESSED_GROUP = "group:unaddressed";

export interface DeployedRail {
  label: string;
  y: number;
  h: number;
}

export interface DeployedTile {
  segmentId: string;
  box: Box;
}

export interface DeployedGroup {
  key: string;
  label: string;
  /** Why the group exists, in words (a tooltip and the aria-label). */
  note: string;
  header: Box;
  box: Box;
  tiles: DeployedTile[];
  overflow: { box: Box; hidden: string[]; hiddenHosts: number; expanded: boolean } | null;
  total: number;
  /** The trunk leg into this group's header, or `null` when no router has a
   *  leg on any of its segments (it is reached through other devices). */
  leg: Point[] | null;
}

export interface DeployedLayout0 {
  width: number;
  height: number;
  rails: DeployedRail[];
  internet: Box | null;
  routers: Array<{ hostId: string; box: Box }>;
  /** Internet -> routers, and the vertical bus the group legs hang from. */
  uplink: Point[] | null;
  bus: Point[] | null;
  groups: DeployedGroup[];
}

interface Member {
  segment: DeployedSegment;
  observedCidr: string;
  northSouth: { bytes: number; flows: number };
}

/** A LINK is a net no host lives on alone -- every member also sits on
 *  another net (router-to-switch transit, an inter-switch link). Drawn in their
 *  own collapsed group so the segments hosts live on get the space. */
export function isLink(segment: DeployedSegment): boolean {
  return segment.residents === 0;
}

function bySize(a: DeployedSegment, b: DeployedSegment): number {
  return b.hosts.length - a.hosts.length || a.id.localeCompare(b.id);
}

function groupsOf(twin: DeployedTwin): Array<{ key: string; label: string; note: string; segments: DeployedSegment[]; budget: number }> {
  const hostNets = twin.segments.filter((s) => !isLink(s));
  const links = twin.segments.filter(isLink);
  const addressed: Member[] = hostNets
    .filter((s) => s.sortCidr !== null)
    .map((segment) => ({ segment, observedCidr: segment.sortCidr!, northSouth: { bytes: 0, flows: 0 } }));
  const unaddressed = hostNets.filter((s) => s.sortCidr === null);

  const out: Array<{ key: string; label: string; note: string; segments: DeployedSegment[]; budget: number }> = [];
  for (const site of groupIntoSites(addressed)) {
    out.push({
      key: site.key,
      label: site.isMisc ? "other addresses" : `${site.label}.*`,
      note: site.isMisc
        ? "Segments whose hosts' addresses share no two-octet prefix with another segment."
        : `Segments whose hosts hold ${site.label}.* addresses.`,
      segments: site.segments.map((m) => m.segment).sort(bySize),
      budget: DEPLOYED_BUDGET,
    });
  }
  if (unaddressed.length > 0) {
    out.push({
      key: UNADDRESSED_GROUP,
      label: "no recorded address",
      note: "Segments whose hosts have no address recorded in the state store.",
      segments: [...unaddressed].sort(bySize),
      budget: DEPLOYED_BUDGET,
    });
  }
  if (links.length > 0) {
    out.push({
      key: LINKS_GROUP,
      label: "links between devices",
      note: "Nets where every host also sits on another net: router, switch and transit links.",
      segments: [...links].sort(bySize),
      budget: 0,
    });
  }
  return out;
}

export function layoutDeployed0(twin: DeployedTwin, expanded: ReadonlySet<string> = new Set()): DeployedLayout0 {
  const contentX = CANVAS_PAD + RAIL_W + RAIL_GAP_X;
  const groupX = contentX + BUS_INSET;
  const groupW = columnWidthFor(DEPLOYED_COLS);

  const routerCount = twin.routers.length;
  const perRow = Math.max(1, Math.floor((groupW + DEVICE_GAP_X) / (DEVICE_W + DEVICE_GAP_X)));
  const routerRows = routerCount === 0 ? 0 : Math.ceil(routerCount / perRow);
  const routerBandH = routerRows === 0 ? 0 : routerRows * DEVICE_H + (routerRows - 1) * DEVICE_GAP_Y;

  const specs = groupsOf(twin);
  const placed = specs.map((spec) => {
    const open = expanded.has(spec.key);
    const overflowing = spec.segments.length > spec.budget;
    const shown = overflowing && !open ? spec.segments.slice(0, spec.budget) : spec.segments;
    const hidden = overflowing && !open ? spec.segments.slice(spec.budget) : [];
    const boxes = shown.length + (overflowing ? 1 : 0);
    return { spec, shown, hidden, overflowing, open, h: columnHeightFor(boxes, DEPLOYED_COLS) };
  });
  const groupsH = placed.reduce((sum, g, i) => sum + g.h + (i > 0 ? TIER_GAP_Y : 0), 0);

  const bands = stackBands(
    [twin.hasWan ? TIER_INTERNET_H : 0, routerBandH, groupsH],
    CANVAS_PAD,
    TIER_GAP_Y,
  );
  const [internetBand, routerBand, groupBand] = bands as [typeof bands[0], typeof bands[0], typeof bands[0]];

  const internet: Box | null = twin.hasWan
    ? { x: groupX, y: internetBand.y + (TIER_INTERNET_H - 34) / 2, w: INTERNET_W, h: 34 }
    : null;

  const routers = twin.routers.map((r, i) => ({
    hostId: r.id,
    box: {
      x: groupX + (i % perRow) * (DEVICE_W + DEVICE_GAP_X),
      y: routerBand.y + Math.floor(i / perRow) * (DEVICE_H + DEVICE_GAP_Y),
      w: DEVICE_W,
      h: DEVICE_H,
    },
  }));
  const routerIds = new Set(twin.routers.map((r) => r.id));

  let y = groupBand.y;
  const busX = contentX + BUS_INSET / 2;
  const groups: DeployedGroup[] = placed.map((g) => {
    const origin = { x: groupX, y };
    const tiles: DeployedTile[] = g.shown.map((segment, i) => ({
      segmentId: segment.id,
      box: columnSlot(origin, DEPLOYED_COLS, i),
    }));
    const header: Box = { x: groupX, y, w: groupW, h: SITE_HEADER_H };
    const reached = g.spec.segments.some((s) => s.hosts.some((h) => routerIds.has(h)));
    const group: DeployedGroup = {
      key: g.spec.key,
      label: g.spec.label,
      note: g.spec.note,
      header,
      box: { x: groupX, y, w: groupW, h: g.h },
      tiles,
      overflow: g.overflowing
        ? {
            box: columnSlot(origin, DEPLOYED_COLS, g.shown.length),
            hidden: g.hidden.map((s) => s.id),
            hiddenHosts: g.hidden.reduce((n, s) => n + s.hosts.length, 0),
            expanded: g.open,
          }
        : null,
      total: g.spec.segments.length,
      leg:
        reached && routers.length > 0
          ? [
              { x: busX, y: header.y + header.h / 2 },
              { x: groupX, y: header.y + header.h / 2 },
            ]
          : null,
    };
    y += g.h + TIER_GAP_Y;
    return group;
  });

  const legYs = groups.flatMap((g) => (g.leg ? [g.leg[0]!.y] : []));
  const firstRouter = routers[0];
  const bus: Point[] | null =
    firstRouter && legYs.length > 0
      ? [
          { x: busX, y: firstRouter.box.y + firstRouter.box.h / 2 },
          { x: busX, y: Math.max(...legYs) },
        ]
      : null;
  const uplink: Point[] | null =
    internet && firstRouter
      ? [
          { x: internet.x + 40, y: internet.y + internet.h },
          { x: internet.x + 40, y: firstRouter.box.y },
        ]
      : null;

  const rails: DeployedRail[] = [];
  if (internet) rails.push({ label: "INTERNET", y: internetBand.y, h: internetBand.h });
  if (routerBandH > 0) rails.push({ label: "ROUTERS", y: routerBand.y, h: routerBand.h });
  if (groupsH > 0) rails.push({ label: "SEGMENTS", y: groupBand.y, h: groupBand.h });

  const bottom = groupsH > 0 ? groupBand.y + groupBand.h : routerBand.y + routerBand.h;
  return {
    width: groupX + groupW + CANVAS_PAD,
    height: Math.max(bottom, CANVAS_PAD) + CANVAS_PAD,
    rails,
    internet,
    routers,
    uplink,
    bus,
    groups,
  };
}

// ---- level 1: one segment ---------------------------------------------------
export interface HostChip {
  hostId: string;
  box: Box;
}

export interface HostBand {
  label: string;
  /** How many chips the band holds (never truncated). */
  count: number;
  labelBox: Box;
  chips: HostChip[];
}

export interface DeployedLayout1 {
  segmentId: string;
  width: number;
  height: number;
  bands: HostBand[];
}

/**
 * One segment's hosts, as chips in bands: first the DEVICES it shares with
 * other nets (routers, switches, dual-homed hosts), then one band per role,
 * largest first. Every host is drawn -- a segment is small enough (corp-twin's
 * largest holds 36) that no budget is needed here, and the table beneath is
 * the complete record either way.
 */
export function layoutDeployed1(twin: DeployedTwin, segmentId: string): DeployedLayout1 | null {
  const segment = twin.segmentById.get(segmentId);
  if (!segment) return null;
  const members = segment.hosts.map((id) => twin.hostById.get(id)).filter((h) => h !== undefined);
  const devices = members.filter((h) => h.multiHomed || h.isRouter);
  const residents = members.filter((h) => !(h.multiHomed || h.isRouter));
  const byRole = new Map<string, string[]>();
  for (const h of residents) {
    const role = h.raw.role ?? "(no role)";
    const list = byRole.get(role) ?? [];
    list.push(h.id);
    byRole.set(role, list);
  }
  const specs: Array<{ label: string; ids: string[] }> = [];
  if (devices.length > 0) specs.push({ label: "shared with other segments", ids: devices.map((h) => h.id) });
  for (const [role, ids] of [...byRole.entries()].sort((a, b) => b[1].length - a[1].length || a[0].localeCompare(b[0]))) {
    specs.push({ label: role, ids });
  }

  const x0 = CANVAS_PAD;
  const innerW = HOST_CHIPS_PER_ROW * HOST_CHIP_W + (HOST_CHIPS_PER_ROW - 1) * CHIP_GAP_X;
  const heights = specs.map((s) => {
    const rows = Math.max(1, Math.ceil(s.ids.length / HOST_CHIPS_PER_ROW));
    return BAND_LABEL_H + rows * HOST_CHIP_H + (rows - 1) * CHIP_GAP_Y + BAND_PAD;
  });
  const stacked = stackBands(heights, CANVAS_PAD, BAND_GAP_Y);
  const bands: HostBand[] = specs.map((s, i) => {
    const band = stacked[i]!;
    return {
      label: s.label,
      count: s.ids.length,
      labelBox: { x: x0, y: band.y, w: innerW, h: BAND_LABEL_H },
      chips: [...s.ids].sort().map((hostId, j) => ({
        hostId,
        box: {
          x: x0 + (j % HOST_CHIPS_PER_ROW) * (HOST_CHIP_W + CHIP_GAP_X),
          y: band.y + BAND_LABEL_H + Math.floor(j / HOST_CHIPS_PER_ROW) * (HOST_CHIP_H + CHIP_GAP_Y),
          w: HOST_CHIP_W,
          h: HOST_CHIP_H,
        },
      })),
    };
  });
  const last = stacked[stacked.length - 1];
  return {
    segmentId,
    width: x0 + innerW + CANVAS_PAD,
    height: (last ? last.y + last.h : CANVAS_PAD) + CANVAS_PAD,
    bands,
  };
}
