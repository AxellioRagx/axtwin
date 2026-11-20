/**
 * Where everything a drop or a template made stands (2026-11-20, round 2; `SPEC.md` "Free layout",
 * placement of dropped nodes): every node and every new segment's implied switch gets a pin near the
 * drop point, so nothing a drop creates is left for the tree's first placement (which put a new
 * segment and its hosts at the canvas's top-left corner, joined to the drop by a long diagonal).
 *
 * - A device the drop or the template placed keeps that spot (`fixed`).
 * - A new segment's implied switch stands just above its first placed host, else just below the
 *   router it hangs from, else at the drop point.
 * - A host group stands in the row under its switch (the real switch carrying its segment, else the
 *   segment's implied switch), with the tree's spacing (`TREE_RANK_SEP` below the switch,
 *   `HOST_GAP` apart): centred under it, or continuing a row the segment already has.
 *
 * Pins are absolute top-lefts, keyed as the side-car keys them (`host:<name>`, `segment:<cidr>`).
 * Pure.
 */
import { DEVICE_H, DEVICE_W } from "../layout/geometry";
import { BUS_ICON } from "../canvas/bus";
import { HOST_GAP, TREE_NODE_SEP, TREE_RANK_SEP } from "../canvas/tree";
import type { TeModel } from "../edit/te";

export interface Point {
  x: number;
  y: number;
}

/** The switch that carries `segment`: a real switch with a leg on it or a link trunking it, else null (the segment is drawn as its implied switch). */
export function carrierOf(te: Pick<TeModel, "nodes" | "links">, segment: string): string | null {
  const switches = new Set(te.nodes.filter((n) => n.kind === "switch").map((n) => n.name));
  const legged = te.nodes.find((n) => n.kind === "switch" && n.nets.some((l) => l.segment === segment));
  if (legged) return legged.name;
  for (const l of te.links) {
    if (l.native !== segment && !l.vlans.includes(segment)) continue;
    for (const end of [l.a.device, l.b.device]) if (switches.has(end)) return end;
  }
  return null;
}

const round = (p: Point): Point => ({ x: Math.round(p.x), y: Math.round(p.y) });

/**
 * The pins for what `after` has that `before` did not.
 * `fixed`: spots already decided (the dropped device at the drop point, a template's laid-out items).
 * `known`: where everything already on the canvas stands (the side-car's pins and the session's settled positions).
 */
export function placeDropped(before: Pick<TeModel, "nodes" | "segments">, after: Pick<TeModel, "nodes" | "segments" | "links">, at: Point | null, fixed: Readonly<Record<string, Point>> = {}, known: Readonly<Record<string, Point>> = {}): Record<string, Point> {
  const oldNodes = new Set(before.nodes.map((n) => n.name));
  const oldSegs = new Set(before.segments.map((s) => s.name));
  const fresh = after.nodes.filter((n) => !oldNodes.has(n.name));
  const freshSegs = after.segments.filter((s) => !oldSegs.has(s.name));
  const pos: Record<string, Point> = {};
  for (const [k, p] of Object.entries(fixed)) pos[k] = round(p);
  const where = (key: string): Point | null => pos[key] ?? known[key] ?? null;
  const cidrOf = new Map(after.segments.map((s) => [s.name, s.cidr]));
  const anchorKey = (segment: string): string | null => {
    const sw = carrierOf(after, segment);
    if (sw) return `host:${sw}`;
    const cidr = cidrOf.get(segment);
    return cidr ? `segment:${cidr}` : null;
  };

  // Devices (routers, switches) the drop made with no spot yet: at the drop point, side by side.
  let k = 0;
  for (const n of fresh) {
    if (n.kind === "host" || where(`host:${n.name}`)) continue;
    if (at) pos[`host:${n.name}`] = round({ x: at.x + k * (DEVICE_W + TREE_NODE_SEP), y: at.y });
    k++;
  }

  // A new segment drawn as its implied switch: above its first placed host, else below its router.
  for (const s of freshSegs) {
    if (carrierOf(after, s.name)) continue;
    const key = `segment:${s.cidr}`;
    if (where(key)) continue;
    const host = after.nodes.find((n) => n.kind === "host" && n.nets[0]?.segment === s.name && where(`host:${n.name}`));
    const router = after.nodes.find((n) => n.kind === "router" && n.nets.some((l) => l.segment === s.name) && where(`host:${n.name}`));
    if (host) {
      const h = where(`host:${host.name}`)!;
      pos[key] = round({ x: h.x + BUS_ICON / 2 - DEVICE_W / 2, y: h.y - TREE_RANK_SEP - DEVICE_H });
    } else if (router) {
      const r = where(`host:${router.name}`)!;
      pos[key] = round({ x: r.x, y: r.y + DEVICE_H + TREE_RANK_SEP });
    } else if (at) pos[key] = round({ x: at.x - DEVICE_W / 2, y: at.y - DEVICE_H / 2 });
  }

  // Host groups: the row under their switch, with the tree's spacing.
  const bySegment = new Map<string, string[]>();
  for (const n of fresh) {
    if (n.kind !== "host" || where(`host:${n.name}`)) continue;
    const seg = n.nets[0]?.segment ?? "";
    bySegment.set(seg, [...(bySegment.get(seg) ?? []), n.name]);
  }
  for (const [seg, names] of bySegment) {
    const ak = seg ? anchorKey(seg) : null;
    const anchor = ak ? where(ak) : null;
    if (!anchor) {
      // No switch to hang from: a row at the drop point.
      if (at) names.forEach((name, i) => (pos[`host:${name}`] = round({ x: at.x + i * (BUS_ICON + HOST_GAP), y: at.y })));
      continue;
    }
    const rowY = anchor.y + DEVICE_H + TREE_RANK_SEP;
    // The segment's hosts already on that row (a dropped server, the hosts it had): continue it.
    const onRow = after.nodes
      .filter((n) => n.kind === "host" && n.nets.some((l) => l.segment === seg))
      .map((n) => where(`host:${n.name}`))
      .filter((p): p is Point => p !== null && Math.abs(p.y - rowY) <= BUS_ICON);
    const width = names.length * BUS_ICON + (names.length - 1) * HOST_GAP;
    const x0 = onRow.length ? Math.max(...onRow.map((p) => p.x)) + BUS_ICON + HOST_GAP : anchor.x + DEVICE_W / 2 - width / 2;
    names.forEach((name, i) => (pos[`host:${name}`] = round({ x: x0 + i * (BUS_ICON + HOST_GAP), y: onRow.length ? onRow[0]!.y : rowY })));
  }
  return pos;
}
