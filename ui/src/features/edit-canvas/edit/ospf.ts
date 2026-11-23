/**
 * OSPF from the canvas alone (round 3, 2026-11-21; `SPEC.md` "OSPF writes"). `ospf_area` on a
 * transit is not enough for the engine to bring OSPF up: the planner emits `configure-routing` only
 * for a router with a `routing:` block whose region is a `fabric.regions[]` entry with `igp: ospf`
 * (IR_SCHEMA §3.4, `planner/fabric.py`). So "run OSPF on these links" also writes:
 *
 * - one OSPF region (`{ id: dc, igp: ospf }`), reusing one the twin already has;
 * - on each router, `routing: { region, role, loopback }` -- the role `spine` for a core (`core*`,
 *   the prefix the L3 switch's core tier seeds), `leaf` for an aggregation (`agg*`), else `area`; the
 *   loopback the next free `/32` of `LOOPBACK_POOL`. No `ospf.router_id`: the renderer derives it.
 * - `ospf_area` on the router's other legs (its LANs, the internet): the renderer
 *   (`fabric_render._render_ospf`) runs OSPF only on interfaces whose network has an `ospf_area`, and
 *   `routing.redistribute` is not rendered, so this is how a rack /24 or the internet sink's segment
 *   reaches the other routers.
 *
 * `ospfFollow` keeps it true as the design grows: a leg added to a router that runs OSPF (a peer
 * link, a switch it becomes the gateway of, a Duplicate with the same uplinks) takes the router's
 * area, and a copied router's loopback is renumbered. Pure `(text) => text`.
 */
import { LOOPBACK_POOL, nextFreeLoopback } from "./allocate";
import { addFabricRegion, readTe, setNodeRouting, setSegmentField, type TeModel, type TeNode } from "./te";

/** The id a new OSPF region takes. */
export const OSPF_REGION_ID = "dc";

const OSPF_IGPS = new Set(["ospf", "both"]);

/** The ids of the twin's regions that run OSPF. */
export function ospfRegions(te: Pick<TeModel, "fabricRegions">): Set<string> {
  return new Set(te.fabricRegions.filter((r) => OSPF_IGPS.has((r.igp ?? "").toLowerCase())).map((r) => r.id));
}

/** The role a router takes in the fabric, from its name (the L3 switch's tier seeds `core` / `agg`). */
export function ospfRoleFor(name: string): "spine" | "leaf" | "area" {
  if (/^core/i.test(name)) return "spine";
  if (/^agg/i.test(name)) return "leaf";
  return "area";
}

/** The OSPF area a router already runs: the first `ospf_area` among its legs' segments, else null. */
export function routerArea(te: Pick<TeModel, "segments">, node: Pick<TeNode, "nets">): number | null {
  for (const l of node.nets) {
    const a = te.segments.find((s) => s.name === l.segment)?.ospfArea;
    if (typeof a === "number") return a;
  }
  return null;
}

/** Whether a router runs OSPF: its `routing.region` is an OSPF region. */
export function runsOspf(te: Pick<TeModel, "fabricRegions">, node: Pick<TeNode, "kind" | "routing">): boolean {
  return node.kind === "router" && !!node.routing?.region && ospfRegions(te).has(node.routing.region);
}

function loopbacksInUse(te: Pick<TeModel, "nodes">): string[] {
  return te.nodes.flatMap((n) => (n.routing?.loopback ? [n.routing.loopback] : []));
}

export interface EnableOspfResult {
  text: string;
  /** The region the routers run in, and whether this call wrote it. */
  region: string;
  regionAdded: boolean;
  /** Routers given a `routing:` block. */
  routed: string[];
  /** Segments given `ospf_area` here (beyond the transits the caller set). */
  advertised: string[];
  /** Routers left as they are: their `routing:` names a region that does not run OSPF. */
  skipped: string[];
}

/** Turn OSPF on for `routers` in area `area` (one edit's worth of text changes). */
export function enableOspf(text: string, routers: readonly string[], area: number): EnableOspfResult {
  let te = readTe(text);
  let region = [...ospfRegions(te)][0] ?? null;
  let regionAdded = false;
  if (!region) {
    const taken = new Set(te.fabricRegions.map((r) => r.id));
    region = OSPF_REGION_ID;
    for (let n = 2; taken.has(region); n++) region = `${OSPF_REGION_ID}-${n}`;
    text = addFabricRegion(text, { id: region, igp: "ospf" });
    regionAdded = true;
    te = readTe(text);
  }
  const regions = ospfRegions(te);
  const routed: string[] = [];
  const skipped: string[] = [];
  const advertised: string[] = [];
  for (const name of routers) {
    const n = te.nodes.find((x) => x.name === name);
    if (!n || n.kind !== "router") continue;
    if (n.routing?.region && !regions.has(n.routing.region)) {
      skipped.push(name);
      continue;
    }
    if (!n.routing?.region || !n.routing.loopback || !n.routing.role) {
      const loopback = n.routing?.loopback ?? nextFreeLoopback(loopbacksInUse(te));
      if (!loopback) throw new Error(`the loopback pool ${LOOPBACK_POOL} is full`);
      text = setNodeRouting(text, name, { region: n.routing?.region ?? region, role: n.routing?.role ?? ospfRoleFor(name), loopback });
      routed.push(name);
      te = readTe(text);
    }
  }
  for (const name of routers) {
    const n = te.nodes.find((x) => x.name === name);
    if (!n || !runsOspf(te, n)) continue;
    for (const l of n.nets) {
      const s = te.segments.find((x) => x.name === l.segment);
      if (s && s.ospfArea === null) {
        text = setSegmentField(text, s.name, "ospf_area", area);
        advertised.push(s.name);
        te = readTe(text);
      }
    }
  }
  return { text, region, regionAdded, routed, advertised, skipped };
}

/**
 * After an edit: every leg NEW in `after` on a router that runs OSPF takes the router's area (its
 * other legs' area, else 0) when its segment has none, and a router whose loopback another router
 * already had (a copy) gets the next free one. Returns `after` unchanged when there is nothing to do
 * or either text does not read.
 */
export function ospfFollow(before: string | undefined, after: string): string {
  let teA: TeModel;
  let teB: TeModel | null;
  try {
    teA = readTe(after);
    teB = before === undefined ? null : readTe(before);
  } catch {
    return after;
  }
  if (!ospfRegions(teA).size) return after;
  let text = after;
  const set: Record<string, number> = {};
  for (const n of teA.nodes) {
    if (!runsOspf(teA, n)) continue;
    const prev = teB?.nodes.find((x) => x.name === n.name);
    const had = new Set(prev?.nets.map((l) => l.segment) ?? []);
    const fresh = n.nets.filter((l) => !had.has(l.segment));
    if (!fresh.length) continue;
    const area = routerArea(teA, { nets: n.nets.filter((l) => had.has(l.segment)) }) ?? routerArea(teA, n) ?? 0;
    for (const l of fresh) {
      const s = teA.segments.find((x) => x.name === l.segment);
      if (s && s.ospfArea === null && set[s.name] === undefined) set[s.name] = area;
    }
  }
  for (const [seg, area] of Object.entries(set)) text = setSegmentField(text, seg, "ospf_area", area);
  // A loopback two routers share: the one that is new (or the later one) is renumbered.
  const before0 = new Set(teB?.nodes.filter((n) => n.routing?.loopback).map((n) => n.name) ?? []);
  const seen = new Map<string, string>();
  let te = Object.keys(set).length ? readTe(text) : teA;
  const order = [...te.nodes].sort((a, b) => Number(!before0.has(a.name)) - Number(!before0.has(b.name)));
  for (const n of order) {
    const lo = n.routing?.loopback?.split("/")[0];
    if (!lo) continue;
    if (!seen.has(lo)) {
      seen.set(lo, n.name);
      continue;
    }
    const next = nextFreeLoopback(loopbacksInUse(te));
    if (!next) continue;
    text = setNodeRouting(text, n.name, { region: n.routing!.region, role: n.routing!.role, loopback: next });
    te = readTe(text);
    seen.set(next.split("/")[0]!, n.name);
  }
  return text;
}
