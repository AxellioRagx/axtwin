/**
 * The INFRASTRUCTURE band (R3, `docs/WORK_ORDER_console_level0_rendering.md`
 * §3 R3) — where each infra device sits, and how it reaches its group.
 *
 * Before R3 the devices were one row centred on the canvas, each tied to its
 * segment's tile by a diagonal `<line>` that crossed whatever lay between (§2
 * F3). The fix is positional rather than cosmetic: **a device's x is the centre
 * of the site column that holds its segment**, so the device is already over
 * the group it belongs to, and the connector collapses to a short orthogonal
 * **stub** down to that group's header. Nothing has to be routed around,
 * because nothing is in the way.
 *
 * Pure geometry, like `trunk.ts`: ids in, boxes and polylines out. It knows
 * nothing about the graph, roles or React — `level0.ts` hands it devices
 * already in role order and maps the ids back to nodes, which is also what lets
 * `applyPins` re-run it over MOVED columns and get devices that track a dragged
 * group for free (LES-266).
 *
 * **Three rules worth naming.**
 *
 *  - **A stub ends at its header's NEAREST horizontal edge and enters no box**
 *    — the rule `trunk.ts`'s legs already needed, shared as
 *    `geometry.nearestEdgeY` rather than written twice. Normally the device is
 *    above and the stub stops at the header's top; a group a pin dragged ABOVE
 *    the band is approached from below.
 *  - **The stub is a single vertical, and it lands on the header because the
 *    device is over the column.** The header spans the column's full width, so
 *    any device inside that width drops straight onto it. That is the whole
 *    reason this module moves devices at all.
 *  - **A device whose segment has no tile stays CENTRED and gets no stub.**
 *    There is no group to point at, and a stub to the nearest column would
 *    assert a membership the export does not support. `level0.ts` draws it with
 *    a dashed outline so "not placed" is visible rather than implied.
 *
 * **Wrapping, and the limit it leaves.** Devices that do not fit across their
 * column wrap to a second row, and the band grows (`infraBandHeight`) instead
 * of overlapping the tier beneath it. A wrapped column uses one slot FEWER per
 * row and offsets alternate rows by half a slot, which is what keeps a stub
 * from descending through the device below it — with two rows that clearance is
 * exact. At THREE or more rows (a column with more than 2·(across−1) devices —
 * no bundle in the tree comes close) rows one and three share a parity and the
 * guarantee lapses; that is measured in `test/r3.infra.test.ts` rather than
 * claimed away.
 *
 * **How little headroom there is:** `devicesAcross(columnW)` is **3** for a
 * three-across column, and the busiest column on BOTH real bundles holds exactly
 * 3. The margin before a wrap moves every tile on the plane is therefore **zero
 * spare slots** — `layout.real.test.ts` pins that relation so it fails the day it
 * stops holding.
 *
 * **Since R6 a column's width is per-GROUP** (`layout/columns.ts`: two tiles
 * across for a group showing four or fewer, three otherwise), so this module
 * takes each column's own width rather than one. It used to size the whole band
 * from `groups[0].header.w` while placing each column against its own — which
 * was harmless only while every column was the same width, and would have
 * silently mis-sized the band the moment one narrowed.
 *
 * **A SECOND limit, unbounded by the wrap: a pinned column drags its stub.** A
 * column a pin parks under ANOTHER column's tiles takes its devices with it,
 * and the stub then descends through whatever now lies beneath — the same
 * no-reflow consequence R2 records for the trunk bus (`15` §4). It is reachable
 * at ordinary coordinates, not a corner: sweeping a pin across one column's
 * width finds the stub crossing a tile at MOST positions, and the few clean
 * ones are a **lattice coincidence rather than a property** — the device pitch
 * (`DEVICE_W + DEVICE_GAP_X` = 168) and the tile pitch (`TILE_W + TILE_GAP_X` =
 * 172) differ, so the ~8-unit window in which a moved device centre happens to
 * land in the gap between two tiles slides as the pin moves. Both sides are
 * pinned in `test/r3.infra.test.ts` — a crossing coordinate AND a clean one —
 * so neither can be read as the rule.
 */
import {
  DEVICE_GAP_X,
  DEVICE_GAP_Y,
  DEVICE_H,
  DEVICE_W,
  nearestEdgeY,
  type Box,
  type Point,
} from "./geometry";

/** Slot pitch: one device box plus the gap after it. */
const PITCH = DEVICE_W + DEVICE_GAP_X;

export interface InfraDeviceInput {
  /** The export node id. Opaque here — `level0.ts` maps it back to the node. */
  id: string;
  /** The site column holding this device's segment, or `null` when the export
   *  placed it nowhere this map draws a tile for. */
  siteKey: string | null;
}

export interface InfraGroup {
  siteKey: string;
  /** The column's header box. Its x/width ARE the column's, so it carries both
   *  the centre a device is placed on and the edge a stub ends at. */
  header: Box;
}

export interface InfraPlacement {
  id: string;
  /** The column this device was placed over; `null` for an unplaced device,
   *  which the view draws dashed. */
  siteKey: string | null;
  box: Box;
}

/** A device's connector to its group: one vertical, device edge → header edge. */
export interface InfraStub {
  deviceId: string;
  siteKey: string;
  points: Point[];
}

export interface InfraInput {
  /** In the order they should read left-to-right within a column — `level0.ts`
   *  passes the graph's (role order, id) sort, so "role order" is the caller's
   *  contract and this module only preserves it. */
  devices: readonly InfraDeviceInput[];
  /** In column order, left to right. */
  groups: readonly InfraGroup[];
  band: { y: number; h: number };
  /** Where a device with no column sits — the canvas centre, as before R3. */
  unplacedCx: number;
}

/** How many devices fit across one site column. */
export function devicesAcross(columnW: number): number {
  return Math.max(1, Math.floor((columnW + DEVICE_GAP_X) / PITCH));
}

/**
 * The row grid one column's devices need.
 *
 * A column that fits uses the full width. A column that must wrap drops to one
 * slot FEWER per row, which buys the slack the half-slot row offset needs to
 * stay inside the column — the acceptance is that every device's x-range lies
 * within its column's.
 */
export function infraGridFor(count: number, columnW: number): { perRow: number; rows: number } {
  const across = devicesAcross(columnW);
  if (count <= 0) return { perRow: across, rows: 0 };
  if (count <= across) return { perRow: across, rows: 1 };
  const perRow = Math.max(1, across - 1);
  return { perRow, rows: Math.ceil(count / perRow) };
}

/** One column's contribution to the band: how many devices it holds, and how
 *  wide it is (R6 — columns no longer share one width). */
export interface InfraColumn {
  count: number;
  width: number;
}

/** The deepest column's row count. */
export function infraRows(columns: readonly InfraColumn[]): number {
  return columns.reduce((max, col) => Math.max(max, infraGridFor(col.count, col.width).rows), 0);
}

/**
 * Every row the band must hold: the deepest column's, **plus one for the
 * unplaced devices** when there are any.
 *
 * The unplaced row is its OWN row rather than sharing the deepest column's
 * (review S3). Unplaced devices are centred on the canvas midpoint — a position
 * no column owns — so sharing a row means one can be drawn on top of a column's
 * device whenever that midpoint falls over an occupied slot. It cannot happen
 * on any bundle in the tree (midX 1251 against device centres 339/813/981),
 * which is exactly why it would have been found by an operator rather than by a
 * test. Separating the rows removes the overlap by construction instead of
 * leaving it to coordinates.
 *
 * ONE function, both callers: `level0.ts` sizes the band with it and
 * `layoutInfra` places against it, so the two cannot disagree about how many
 * rows exist (LES-025).
 */
export function infraBandRows(
  columns: readonly InfraColumn[],
  unplacedCount: number,
): number {
  return infraRows(columns) + (unplacedCount > 0 ? 1 : 0);
}

/** The height of `rows` stacked device rows; `0` for no rows. */
function stackHeight(rows: number): number {
  return rows <= 0 ? 0 : rows * DEVICE_H + (rows - 1) * DEVICE_GAP_Y;
}

/**
 * The INFRASTRUCTURE band's height: the stacked rows, never less than the
 * tier's own floor.
 *
 * With one row (every bundle that ships) this returns the floor unchanged, so
 * the segment tier's top edge — and therefore every tile — is exactly where R2
 * left it. Only a wrapping column, or an unplaced device claiming its own row,
 * moves the plane — which is why R3 bumps `PLANE_EPOCH` (`layout/plane.ts`).
 */
export function infraBandHeight(rows: number, floor: number): number {
  if (rows <= 0) return floor;
  return Math.max(floor, stackHeight(rows));
}

function centerX(box: Box): number {
  return box.x + box.w / 2;
}

/**
 * Place every device over its column and connect it with a stub.
 *
 * Deterministic: the output is column-major in the given group order, with the
 * unplaced devices last — which is also the drawing order, so the document
 * order a keyboard or screen reader walks matches what the eye sees.
 */
export function layoutInfra(input: InfraInput): {
  devices: InfraPlacement[];
  stubs: InfraStub[];
} {
  const { band } = input;
  const groupByKey = new Map(input.groups.map((g) => [g.siteKey, g]));

  // Bucket by column, preserving the caller's order inside each.
  const byGroup = new Map<string, InfraDeviceInput[]>();
  const unplaced: InfraDeviceInput[] = [];
  for (const device of input.devices) {
    const group = device.siteKey === null ? undefined : groupByKey.get(device.siteKey);
    // A siteKey naming no drawn column is treated as unplaced rather than
    // dropped: it is still a device the twin depends on.
    if (!group || device.siteKey === null) {
      unplaced.push({ ...device, siteKey: null });
      continue;
    }
    const list = byGroup.get(device.siteKey) ?? [];
    list.push(device);
    byGroup.set(device.siteKey, list);
  }

  const devices: InfraPlacement[] = [];
  const stubs: InfraStub[] = [];

  // ONE grid for the whole band, so row 0 of a one-row column lines up with row
  // 0 of a wrapped one instead of floating in the middle of a taller band -- and
  // so the unplaced row below can be given an index rather than a coordinate.
  const columns: InfraColumn[] = input.groups.map((g) => ({
    count: (byGroup.get(g.siteKey) ?? []).length,
    width: g.header.w,
  }));
  const placedRows = infraRows(columns);
  const gridTop =
    band.y + (band.h - stackHeight(infraBandRows(columns, unplaced.length))) / 2;

  for (const group of input.groups) {
    const members = byGroup.get(group.siteKey) ?? [];
    if (members.length === 0) continue;
    const { perRow, rows } = infraGridFor(members.length, group.header.w);
    const boxes = placeRows(members.length, centerX(group.header), gridTop, perRow, rows, 0);
    members.forEach((device, i) => {
      const box = boxes[i]!;
      devices.push({ id: device.id, siteKey: group.siteKey, box });
      stubs.push(stubFor(device.id, group.siteKey, box, group.header));
    });
  }

  if (unplaced.length > 0) {
    // ONE centred row of its own, BELOW every column's rows (review S3): these
    // sit on the canvas midpoint, which no column owns, so any row they share
    // is a row they can collide in. (An earlier form asked `infraGridFor` for an
    // infinite width, and `0 * Infinity` is `NaN` -- every unplaced device got
    // a NaN x. The fixture caught it; no real bundle has an unplaced device.)
    const boxes = placeRows(
      unplaced.length,
      input.unplacedCx,
      gridTop,
      unplaced.length,
      1,
      placedRows,
    );
    unplaced.forEach((device, i) => {
      devices.push({ id: device.id, siteKey: null, box: boxes[i]! });
    });
  }

  return { devices, stubs };
}

/**
 * `count` boxes in `rows` rows of at most `perRow`, each row centred on `cx`,
 * starting at band row `startRow` of the shared grid.
 *
 * The grid top is the band's, not this stack's, so a single row in a
 * floor-height band lands exactly where the pre-R3 centred row did
 * (`band.y + (band.h - DEVICE_H) / 2`) — which is why R3's golden diff carries
 * no device `y` at all.
 *
 * **Alternate rows are offset by half a slot** when the column wraps, so a
 * stub descending from an upper row passes through the gap between the boxes
 * below it (the offset is 84 units against a 75-unit half-box, i.e. 9 units
 * clear) instead of through a device — the F3 defect, re-entering one tier up.
 */
function placeRows(
  count: number,
  cx: number,
  gridTop: number,
  perRow: number,
  rows: number,
  startRow: number,
): Box[] {
  const out: Box[] = [];
  for (let i = 0; i < count; i++) {
    const row = Math.floor(i / perRow);
    const col = i % perRow;
    const inRow = Math.min(perRow, count - row * perRow);
    const rowW = inRow * DEVICE_W + (inRow - 1) * DEVICE_GAP_X;
    const offset = rows > 1 ? (row % 2) * (PITCH / 2) : 0;
    out.push({
      x: cx - rowW / 2 + offset + col * PITCH,
      y: gridTop + (startRow + row) * (DEVICE_H + DEVICE_GAP_Y),
      w: DEVICE_W,
      h: DEVICE_H,
    });
  }
  return out;
}

/**
 * One vertical from the device's near edge to the header's near edge.
 *
 * The header edge is chosen from the device's CENTRE and the device edge from
 * the header edge — two steps rather than one because the device is the thing
 * that moved. A device straddling its header's own y-range would resolve the
 * two inconsistently, but it cannot: the header sits in the segment tier and
 * the device in the infrastructure band, and a pin moves the header only, never
 * the device's y.
 */
function stubFor(deviceId: string, siteKey: string, box: Box, header: Box): InfraStub {
  const x = centerX(box);
  const headerY = nearestEdgeY(header, box.y + box.h / 2);
  const deviceY = nearestEdgeY(box, headerY);
  return {
    deviceId,
    siteKey,
    points: [
      { x, y: deviceY },
      { x, y: headerY },
    ],
  };
}
