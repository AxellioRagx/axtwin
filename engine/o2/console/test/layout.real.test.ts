// @vitest-environment node
/**
 * The §3 concrete checks against the REAL cast bundle
 * (`twins/corpapcn-twin/`, 786 nodes / 1437 edges / 32 segments) --
 * LES-185: a hand fixture cannot prove the layout works on what ships, so the
 * counts are asserted against the actual file the installer wrote.
 *
 * The export (`corpapcn-twin.topology.json`) is read directly. The one thing
 * the export does not carry is the cast's conduit list, which lives in the
 * twin config YAML; rather than add a YAML parser dependency (packages are not
 * installed in this slice), the flat `conduits:` block is read with a small
 * scoped reader -- the block is a plain list of `- from:` / `to:` pairs and is
 * the last key under `twin_config`, so a full YAML parser is not warranted.
 */
import { existsSync, lstatSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import type { TopologyExport } from "../src/api/types";
import { buildGraph, type Conduit, type ConsoleGraph } from "../src/model/graph";
import { roleBadgeLabel } from "../src/model/roles";
import { SITE_BUDGET } from "../src/model/budgets";
import { devicesAcross } from "../src/layout/infra";
import { ARC_TOP_K } from "../src/layout/arcs";
import { ARC_BAND_PAD, ARC_DEPTH_MAX } from "../src/layout/geometry";
import { layout0, overflowAnchorKey } from "../src/layout/level0";
import { groupIntoSites } from "../src/layout/sites";
import { noConnectorCrossesATile } from "./layout/helpers";
import { layout1 } from "../src/layout/level1";
import { matrix0 } from "../src/layout/matrix";
import {
  BUDGET_LEVEL0,
  BUDGET_LEVEL1_BANDS,
  BUDGET_LEVEL1_BAND_CHIPS,
  BUDGET_MATRIX_AXIS,
} from "../src/model/budgets";

const BUNDLE = fileURLToPath(
  new URL("../../../twins/corpapcn-twin/", import.meta.url),
);
const TOPOLOGY = `${BUNDLE}generated/corpapcn-twin.topology.json`;
const CAST_YAML = `${BUNDLE}te.yaml`;

/** Read the flat `conduits:` list out of the twin config YAML. Scoped to the
 *  exact 2-space-indented shape aximprint writes (`- from: <name>` /
 *  `    to: <name>`); it stops at the next twin_config key. */
function parseConduits(yamlText: string): Conduit[] {
  const out: Conduit[] = [];
  let inBlock = false;
  let current: Conduit | null = null;
  for (const line of yamlText.split("\n")) {
    if (/^ {2}conduits: *$/.test(line)) {
      inBlock = true;
      continue;
    }
    if (!inBlock) continue;
    // A 2-space-indented non-list key ends the block.
    if (/^ {2}\S/.test(line) && !/^ {2}-/.test(line)) break;
    const from = line.match(/^ {2}- +from: +(\S+) *$/);
    if (from) {
      if (current) out.push(current);
      current = { from: from[1]!, to: "" };
      continue;
    }
    const to = line.match(/^ {4}to: +(\S+) *$/);
    if (to && current) current.to = to[1]!;
  }
  if (current) out.push(current);
  return out;
}

/**
 * Every site key, for the layout option that lifts R6's per-group budget.
 *
 * Several R4 clauses below are about **one curve per pair**, which is precisely
 * what a collapsed group projects away (a collapsed segment's arcs anchor at its
 * `+N more` tile and merge there). They are restated against this view rather
 * than deleted (LES-299): the claim still holds where it is meaningful, and the
 * budgeted view gets its own assertions beside it.
 */
function everySite(g: ConsoleGraph): string[] {
  return groupIntoSites(g.segments).map((s) => s.key);
}

/** The site group holding a segment -- read off the GROUPING, not off a drawn
 *  tile, because since R6 a segment may legitimately have no tile. */
function siteOf(g: ConsoleGraph, cidr: string): string {
  return groupIntoSites(g.segments).find((s) =>
    s.segments.some((seg) => seg.observedCidr === cidr),
  )!.key;
}

const present = existsSync(TOPOLOGY) && existsSync(CAST_YAML);

describe.skipIf(!present)("the real corpapcn-twin bundle (§3, LES-185)", () => {
  const exportData = JSON.parse(readFileSync(TOPOLOGY, "utf8")) as TopologyExport;
  const conduits = parseConduits(readFileSync(CAST_YAML, "utf8"));
  // The canonical id is the bundle directory name (`corpapcn-twin`), NOT the
  // export's baked `imprint_id` (a `pit-*` for this bundle) -- the exact
  // mismatch the drill-route bug was about. This is a layout test, so the id
  // value doesn't affect its assertions, but it is spelled canonically here so
  // the fixture models the right practice.
  const graph = buildGraph(exportData, {
    imprintId: "corpapcn-twin",
    deployMinOccupancy: 0.8,
    conduits,
  });

  it("has the census the work order names", () => {
    expect(exportData.nodes).toHaveLength(786);
    expect(exportData.edges).toHaveLength(1437);
    expect(exportData.segments).toHaveLength(32);
  });

  it("budgets its 32 segments into 18 drawn tiles and 14 collapsed (R6)", () => {
    // This read "renders 32 level-0 tiles" until R6 gave each site group a
    // budget. The twin still HAS 32 segments -- every one is in the outline,
    // the matrix, search and its own drill-down -- and the map draws each
    // group's busiest six with a `+N more` for the rest.
    const l0 = layout0(graph);
    expect(l0.tiles).toHaveLength(18);
    expect(l0.hiddenSegments).toBe(14);
    expect(l0.tiles.length + l0.hiddenSegments).toBe(32);
    const all = layout0(graph, {}, { expanded: everySite(graph) });
    expect(all.tiles).toHaveLength(32);
    expect(all.hiddenSegments).toBe(0);
  });

  it("reads 22 conduits and renders 22 ASSERTED arcs (R4, retiring the lanes)", () => {
    expect(conduits).toHaveLength(22);
    // R6 restatement: one curve per conduit is a claim about a drawing where
    // every segment has a tile. With the groups expanded, that is this one.
    const arcs = layout0(graph, {}, { expanded: everySite(graph) }).arcs;
    expect(arcs.filter((a) => a.kind === "asserted")).toHaveLength(22);
    // The twin's declared conduits are drawn in full -- the top-K budget is
    // the OBSERVED set's alone, since an authored conduit is not a projection
    // of anything.
    expect(arcs.every((a) => a.kind !== "asserted" || a.a < a.b)).toBe(true);
  });

  it("loses NO conduit to the per-group budget -- each is drawn or internal (R6)", () => {
    // The budget hides TILES. A conduit into a collapsed segment is re-anchored
    // at that group's `+N more` box and merged with whatever else lands there;
    // one with BOTH ends collapsed in one group has no two anchors and is
    // counted in `internalPairs`. Neither vanishes, which is the property.
    const l0 = layout0(graph);
    const hidden = new Set(l0.overflow.flatMap((m) => m.hidden.map((s) => s.name)));
    const site = new Map<string, string>();
    for (const s of groupIntoSites(graph.segments)) {
      for (const seg of s.segments) site.set(seg.name, s.key);
    }
    const anchorOf = (name: string): string =>
      hidden.has(name) ? overflowAnchorKey(site.get(name)!) : name;
    const drawn = new Set(l0.arcs.map((a) => `${a.a}|${a.b}`));
    let internal = 0;
    for (const c of graph.conduits) {
      const [ka, kb] = [anchorOf(c.a), anchorOf(c.b)].sort();
      if (ka === kb) {
        internal += 1;
        continue;
      }
      expect(drawn.has(`${ka}|${kb}`), `${c.a}|${c.b}`).toBe(true);
    }
    expect(l0.internalPairs).toBeGreaterThanOrEqual(internal);
  });

  it("finds exactly six infrastructure devices (LES-210: LOCAL only)", () => {
    expect(graph.infraDevices).toHaveLength(6);
    // The 12 non-local nodes carrying an infra role are NOT devices.
    const roles = graph.infraDevices.map((d) => d.role).sort();
    expect(roles).toEqual([
      "dns-resolver",
      "dns-resolver",
      "dns-resolver",
      "dns-resolver",
      "domain-controller",
      "monitoring-collector",
    ]);
    expect(graph.infraDevices.every((d) => d.locality === "local")).toBe(true);
  });

  it("opens a level-1 spine at 10.80.20.0/24 with its real role bands", () => {
    const l1 = layout1(graph, "10.80.20.0/24");
    expect(l1).not.toBeNull();
    const roles = l1!.bands.map((b) => b.role);
    expect(roles).toContain("domain-controller");
    expect(roles).toContain("dns-resolver");
    expect(roles).toContain("corp-workstation");
    expect(roles).toContain("generic-endpoint");
    // Fixed order: the DC band is above the workstation band.
    expect(roles.indexOf("domain-controller")).toBeLessThan(roles.indexOf("corp-workstation"));
  });

  it("a host's facts-table listen set is the export's listen_ports verbatim", () => {
    const dc = exportData.nodes.find((n) => n.id === "hostname:axdc01")!;
    const node = graph.nodeById.get("hostname:axdc01")!;
    expect(node.listenPorts).toEqual(dc.listen_ports);
    expect(node.listenPorts.length).toBeGreaterThan(0);
  });

  it("matrix cell totals sum to the export's edge flows", () => {
    const m = matrix0(graph);
    const edgeFlows = exportData.edges.reduce((sum, e) => sum + e.flows, 0);
    const cellFlows = m.cells.reduce((sum, c) => sum + c.flows, 0);
    expect(m.totalFlows).toBe(edgeFlows);
    expect(cellFlows).toBe(edgeFlows);
  });

  it("counts the 49 unplaced and 596 non-local entities and makes them reachable (O7)", () => {
    expect(graph.unplacedLocal).toHaveLength(49);
    expect(graph.nonLocal).toHaveLength(596);
    const l0 = layout0(graph);
    expect(l0.unplaced?.count).toBe(49);
    expect(l0.nonLocalCount).toBe(596);
  });

  it("no view exceeds its budget on this bundle", () => {
    const l0 = layout0(graph);
    const boxes =
      l0.tiles.length + l0.infraDevices.length + 1 + (l0.wanEdge ? 1 : 0) + (l0.unplaced ? 1 : 0);
    expect(boxes).toBeLessThanOrEqual(BUDGET_LEVEL0);

    expect(matrix0(graph).axisCount).toBeLessThanOrEqual(BUDGET_MATRIX_AXIS);

    for (const seg of graph.segments) {
      const l1 = layout1(graph, seg.observedCidr)!;
      expect(l1.bands.length, seg.observedCidr).toBeLessThanOrEqual(BUDGET_LEVEL1_BANDS);
      for (const b of l1.bands) {
        if (!b.collapsed) {
          expect(b.chips.length, `${seg.observedCidr} ${b.role}`).toBeLessThanOrEqual(
            BUDGET_LEVEL1_BAND_CHIPS,
          );
        }
      }
    }
  });

  it("hangs one trunk leg per site group, widest where the egress actually is (R2)", () => {
    const l0 = layout0(graph);
    expect(l0.trunk).not.toBeNull();
    expect(l0.trunk!.legs.map((l) => l.siteKey)).toEqual(l0.sites.map((s) => s.site.key));
    // The busiest north-south SEGMENT on this bundle is 10.80.20.0/24, and the
    // widest leg must be the group that holds it -- the property, read off the
    // data rather than a hardcoded site key.
    const top = [...graph.segments].sort((a, b) => b.northSouth.bytes - a.northSouth.bytes)[0]!;
    expect(top.observedCidr).toBe("10.80.20.0/24");
    // R6: resolved from the GROUPING, not from a drawn tile -- the busiest
    // north-south talker is not guaranteed a tile under a host-count ranking
    // (on the pit bundle it does not get one), and a leg carries its whole
    // group either way.
    const site = siteOf(graph, top.observedCidr);
    const widest = [...l0.trunk!.legs].sort((a, b) => b.bytes - a.bytes)[0]!;
    expect(widest.siteKey).toBe(site);
    expect(widest.strokeWidth).toBe(Math.max(...l0.trunk!.legs.map((l) => l.strokeWidth)));
  });

  it("the 27-host generic-endpoint band in 10.80.72.0/24 exists and collapses (LES-185)", () => {
    // The regression case: 27 generic-endpoint on one segment, over the
    // 15-chip budget once its ghosts land in the below-threshold band.
    const placed = exportData.nodes.filter(
      (n) => n.segment === "10.80.72.0/24" && n.role === "generic-endpoint",
    );
    expect(placed).toHaveLength(27);
    const l1 = layout1(graph, "10.80.72.0/24")!;
    const below = l1.bands.find((b) => b.role === "below-threshold");
    expect(below?.collapsed).toBe(true);
  });
});

// ===========================================================================
// The SECOND real bundle: the discovered PIT the live Console is opened on
// (2,641 nodes / 4,157 edges / 39 segments). It is the bundle §2's own
// north-south figures came from, and unlike `corpapcn-twin` it has NO cast
// roster -- so it is also the real-content exercise of R2's INFERRED WAN edge.
// ===========================================================================
const PIT = fileURLToPath(
  new URL("../../../twins/pit-395a61f04e-1780405200/", import.meta.url),
);
const PIT_TOPOLOGY = `${PIT}generated/pit-395a61f04e-1780405200.topology.json`;

/**
 * The PIT export is a SYMLINK into `corpapcn-disco/`, and `existsSync` FOLLOWS
 * it — so a missing target would make this whole block `skipIf` itself away,
 * silently taking R2's headline widest-leg acceptance with it and still
 * reporting green. A dangling link is therefore a hard failure; only a
 * genuinely absent bundle (no link at all) skips.
 */
function pitAvailable(): boolean {
  let linked = false;
  try {
    lstatSync(PIT_TOPOLOGY);
    linked = true;
  } catch {
    linked = false;
  }
  if (linked && !existsSync(PIT_TOPOLOGY)) {
    throw new Error(
      `${PIT_TOPOLOGY} is a DANGLING symlink (its target is missing). R2's ` +
        "widest-leg acceptance runs on this bundle -- restore the link target " +
        "rather than letting this block skip itself green.",
    );
  }
  return linked;
}

describe.skipIf(!pitAvailable())("the real pit bundle (R2)", () => {
  const exportData = JSON.parse(readFileSync(PIT_TOPOLOGY, "utf8")) as TopologyExport;
  // **Built WITHOUT the roster the API publishes for this bundle.** No conduits
  // and no routers is the shape a server serves before a cast is registered --
  // the case the WAN edge must be INFERRED in (R2), and the only way to reach
  // R4's top-K budget on real data at all. It is NOT the live shape: this
  // bundle's own `<id>.yaml` carries 40 conduits and `axrange/api/imprints.py`
  // publishes them, so the Console always receives the roster, every observed
  // pair is also an asserted conduit, and the budget cannot engage. The live
  // shape has its own block at the bottom of this file (review S1).
  const graph = buildGraph(exportData, {
    imprintId: "pit-395a61f04e-1780405200",
    deployMinOccupancy: 0.8,
  });

  it("infers the WAN edge from observed crossing traffic alone", () => {
    expect(graph.routers).toEqual([]);
    expect(graph.wanEdge?.inferred).toBe(true);
    expect(graph.wanEdge!.crossingEdges).toBeGreaterThan(0);
    expect(layout0(graph).trunk).not.toBeNull();
  });

  it("makes 10.20.195.0/24 the largest egress talker, as §2 measured", () => {
    const top = [...graph.segments].sort((a, b) => b.northSouth.bytes - a.northSouth.bytes)[0]!;
    expect(top.observedCidr).toBe("10.20.195.0/24");
    expect(top.northSouth.bytes).toBeGreaterThan(1_700_000_000); // §2's "1.77 GB"
  });

  it("draws the widest leg to the site HOLDING 10.20.195.0/24 (§3 R2 acceptance)", () => {
    const l0 = layout0(graph);
    expect(l0.trunk!.legs).toHaveLength(l0.sites.length);
    // **R6's measured cost, recorded here because this is where it bites.**
    // `10.20.195.0/24` is this bundle's largest egress talker (1.77 GB) and
    // holds exactly ONE placed host, so the default host-count ranking
    // collapses it: it has no tile. Its bytes are still in its group's leg --
    // a leg sums the whole group, collapsed members included -- which is why
    // R2's acceptance survives the budget unchanged.
    expect(l0.tiles.some((t) => t.segment.observedCidr === "10.20.195.0/24")).toBe(false);
    const site = siteOf(graph, "10.20.195.0/24");
    const widest = [...l0.trunk!.legs].sort((a, b) => b.bytes - a.bytes)[0]!;
    expect(widest.siteKey).toBe(site);
    expect(widest.strokeWidth).toBe(Math.max(...l0.trunk!.legs.map((l) => l.strokeWidth)));
    // ...and it is identifiable by width ALONE: strictly wider than every other.
    for (const leg of l0.trunk!.legs) {
      if (leg.siteKey !== site) expect(leg.strokeWidth).toBeLessThan(widest.strokeWidth);
    }
  });

  it("crosses no tile with any connector, at 39 segments (§1.2)", () => {
    expect(noConnectorCrossesATile(layout0(graph))).toEqual([]);
  });

  it("sits all SEVEN infrastructure devices inside their own site column (§3 R3)", () => {
    const l0 = layout0(graph);
    // Seven, not eight: the bundle's one local `file-server` is not in
    // `INFRA_DEVICE_ROLES`, so it is a host on its tile rather than a device in
    // the band. Asserted so the count is read off the data, not assumed.
    expect(l0.infraDevices).toHaveLength(7);
    const columnOf = new Map(l0.sites.map((s) => [s.site.key, s.box]));
    for (const d of l0.infraDevices) {
      expect(d.siteKey, d.node.id).not.toBeNull();
      const col = columnOf.get(d.siteKey!)!;
      expect(d.box.x, d.node.id).toBeGreaterThanOrEqual(col.x);
      expect(d.box.x + d.box.w, d.node.id).toBeLessThanOrEqual(col.x + col.w);
    }
    // "No shipped bundle wraps" is the claim `PLANE_EPOCH`'s bump leans on, so
    // it is pinned as a RELATION rather than a literal: the busiest column must
    // fit across. There are ZERO spare slots (3 of 3), so this fails the day a
    // bundle grows a fourth device in one column -- which is also the day the
    // plane moves for every tile.
    const perColumn = new Map<string, number>();
    for (const d of l0.infraDevices) {
      perColumn.set(d.siteKey!, (perColumn.get(d.siteKey!) ?? 0) + 1);
    }
    const columnW = l0.sites[0]!.box.w;
    expect(Math.max(...perColumn.values())).toBeLessThanOrEqual(devicesAcross(columnW));
    expect(Math.max(...perColumn.values())).toBe(devicesAcross(columnW)); // zero spare
    expect(new Set(l0.infraDevices.map((d) => d.box.y)).size).toBe(1); // one row
  });

  it("aggregates exactly 40 east-west pairs, topped by 10.80.20.0/24 ↔ 10.80.75.0/24 (§3 R4)", () => {
    expect(graph.trafficPairs).toHaveLength(40);
    const top = graph.trafficPairs[0]!;
    const cidr = (name: string) => graph.segmentByName.get(name)!.observedCidr;
    expect([cidr(top.a), cidr(top.b)].sort()).toEqual(["10.80.20.0/24", "10.80.75.0/24"]);
    // 2.23 GB against a 0.04 GB runner-up: one arc dominates by construction,
    // which is exactly why the width scale is normalised over the drawn set
    // (§11.3) rather than against the maximum alone.
    expect(top.bytes).toBeGreaterThan(2_200_000_000);
    expect(graph.trafficPairs[1]!.bytes).toBeLessThan(top.bytes / 10);
  });

  it("draws the top 12, NAMES the rest, and bounds the band [no-roster construction]", () => {
    // Under R6's budget every pair is still accounted for, but not one curve
    // each: a pair into a collapsed group shares its group's anchor. The
    // identity is therefore over PAIRS -- drawn + budgeted-out + internal.
    const l0 = layout0(graph, {}, { expanded: everySite(graph) });
    expect(l0.arcs).toHaveLength(ARC_TOP_K);
    expect(l0.hiddenPairs).toBe(40 - ARC_TOP_K);
    expect(l0.arcs.length + l0.hiddenPairs).toBe(graph.trafficPairs.length);

    const budgeted = layout0(graph);
    const drawnPairs = budgeted.arcs.reduce((n, a) => n + a.pairs, 0);
    expect(drawnPairs + budgeted.hiddenPairs + budgeted.internalPairs).toBe(
      graph.trafficPairs.length,
    );
    expect(budgeted.foldedPairs).toBeGreaterThan(0);
    const band = l0.rails.find((r) => r.tier === "east-west")!.box;
    expect(band.h).toBeLessThanOrEqual(ARC_DEPTH_MAX + ARC_BAND_PAD);
    // The widest arc is the busiest pair, identifiable by width ALONE.
    const widest = [...l0.arcs].sort((a, b) => b.strokeWidth - a.strokeWidth)[0]!;
    const cidr = (name: string) => graph.segmentByName.get(name)!.observedCidr;
    expect([cidr(widest.a), cidr(widest.b)].sort()).toEqual(["10.80.20.0/24", "10.80.75.0/24"]);
    for (const arc of l0.arcs) {
      if (arc !== widest) expect(arc.strokeWidth).toBeLessThan(widest.strokeWidth);
    }
  });

  it("leaves every arc on its group's bottom edge and crosses NO tile, at 39 segments", () => {
    // The ANCHOR rule, on both sides of R6's budget. An arc's y is its GROUP's
    // bottom edge whether it leaves a tile or a `+N more` box -- which is why
    // the site is resolved by key here rather than by member segment (a folded
    // arc's end names an anchor, not a segment).
    const expanded = everySite(graph);
    for (const l0 of [layout0(graph), layout0(graph, {}, { expanded })]) {
      const bottomOf = (key: string): number => {
        const site =
          l0.sites.find((s) => s.site.segments.some((seg) => seg.name === key)) ??
          l0.sites.find((s) => overflowAnchorKey(s.site.key) === key)!;
        return site.box.y + site.box.h;
      };
      for (const arc of l0.arcs) {
        expect(arc.from.y, `${arc.a}|${arc.b}`).toBe(bottomOf(arc.a));
        expect(arc.to.y, `${arc.a}|${arc.b}`).toBe(bottomOf(arc.b));
      }
      expect(noConnectorCrossesATile(l0)).toEqual([]);
    }
    // ...and under `?ew=all`, where all 40 are drawn -- the case that caught
    // the three-quarter sag (six crossings before the drop into the band).
    const all = layout0(graph, {}, { showAllPairs: true, expanded });
    expect(all.arcs).toHaveLength(40);
    expect(all.hiddenPairs).toBe(0);
    expect(noConnectorCrossesATile(all)).toEqual([]);
    expect(all.rails.find((r) => r.tier === "east-west")!.box.h).toBeLessThanOrEqual(
      ARC_DEPTH_MAX + ARC_BAND_PAD,
    );
  });

  it("draws the SAME pair wider under `?ew=all` -- D2's cost [no-roster construction]", () => {
    // §5 D2: width is relative to the DRAWN set, so toggling the budget moves
    // every width. Measured here so an operator (and the doc) can point at it:
    // the runner-up pair is 3.06 px among the top 12 and 4.74 px across all 40.
    // Measured with every group EXPANDED (R6), because the figures describe one
    // curve per pair and a collapsed group merges several into one.
    const expanded = everySite(graph);
    const top = layout0(graph, {}, { expanded });
    const all = layout0(graph, {}, { showAllPairs: true, expanded });
    const second = graph.trafficPairs[1]!;
    const find = (l: typeof top) =>
      l.arcs.find((a) => a.a === second.a && a.b === second.b)!.strokeWidth;
    expect(find(top)).toBeCloseTo(3.06, 2);
    expect(find(all)).toBeCloseTo(4.74, 2);
  });

  it("shows six of nineteen in 10.80, and the rest behind one `+N more` (§3 R6)", () => {
    // The acceptance's own example, asserted as the RULE rather than as the
    // literal "+10 more" the work order wrote: this bundle's 10.80 group holds
    // 19 segments where `corpapcn-twin`'s holds 16, so the count differs per
    // bundle while `shown === SITE_BUDGET` and `hidden === total - SITE_BUDGET`
    // do not (LES-303/LES-311).
    const l0 = layout0(graph);
    const big = groupIntoSites(graph.segments).find((s) => s.key === "site:10.80")!;
    expect(big.segments).toHaveLength(19);
    expect(l0.tiles.filter((t) => t.siteKey === "site:10.80")).toHaveLength(SITE_BUDGET);
    const more = l0.overflow.find((m) => m.siteKey === "site:10.80")!;
    expect(more.hidden).toHaveLength(19 - SITE_BUDGET);
    expect(more.total).toBe(19);
    // The busiest six are the ones drawn, by placed-host count.
    expect(
      l0.tiles.filter((t) => t.siteKey === "site:10.80").map((t) => t.segment.observedCidr),
    ).toEqual([
      "10.80.72.0/24",
      "10.80.20.0/24",
      "10.80.75.0/24",
      "10.80.64.0/24",
      "10.80.79.0/24",
      "10.80.34.0/24", // a 4-host tie with .65 and .73, broken by CIDR
    ]);
    // Expanding restores all 19 with the first six where they already were.
    const expanded = layout0(graph, {}, { expanded: ["site:10.80"] });
    const after = new Map(expanded.tiles.map((t) => [t.segment.observedCidr, t.box]));
    for (const t of l0.tiles) {
      expect(after.get(t.segment.observedCidr), t.segment.observedCidr).toEqual(t.box);
    }
    expect(expanded.tiles.filter((t) => t.siteKey === "site:10.80")).toHaveLength(19);
    expect(noConnectorCrossesATile(expanded)).toEqual([]);
  });

  it("fits the plane in far less height than it did before the budget (R6)", () => {
    // The point of the slice, as a number: the 19-segment column was seven tile
    // rows and ran off the bottom of any window. Bounded now by the budget, and
    // the plane is narrower too (the *unplaced* column is one tile wide).
    const l0 = layout0(graph);
    expect(l0.height).toBeLessThan(1000);
    expect(l0.width).toBeLessThan(2100);
    expect(layout0(graph, {}, { expanded: everySite(graph) }).height).toBeGreaterThan(l0.height);
  });

  it("puts BOTH domain controllers over the 10.80 column, badged `DC 2` (§3 R3 acceptance)", () => {
    // (R3's acceptance -- unaffected by the roster, which carries no devices.)
    const l0 = layout0(graph);
    const dcs = l0.infraDevices.filter((d) => d.node.role === "domain-controller");
    expect(dcs.map((d) => d.node.id).sort()).toEqual([
      "address:10.80.20.4",
      "address:10.80.20.5",
    ]);
    // The column that holds 10.80.20.0/24 -- read off the tile, not hardcoded.
    const siteKey = l0.tiles.find((t) => t.segment.observedCidr === "10.80.20.0/24")!.siteKey;
    expect(siteKey).toBe("site:10.80");
    for (const dc of dcs) expect(dc.siteKey, dc.node.id).toBe(siteKey);
    // ...and the tile itself says there are two of them, in words.
    const seg = graph.segmentByCidr.get("10.80.20.0/24")!;
    expect(seg.roleBadges).toContainEqual({ role: "domain-controller", count: 2 });
    expect(roleBadgeLabel("domain-controller")).toBe("DC");
  });
});

// ===========================================================================
// The pit bundle AS THE API SERVES IT — the LIVE shape (R4, review S1).
//
// Everything above builds this bundle with NO roster. That is the only way to
// reach R4's top-K budget on real data, and it is a view an operator cannot
// open: the bundle's own `<id>.yaml` carries 40 conduits and
// `axrange/api/imprints.py` publishes them, so the Console always receives
// them. Here the roster is read from the same YAML the API reads, which makes
// this the configuration the live canvas actually draws — and nothing else in
// the suite covered it.
// ===========================================================================
const PIT_CAST_YAML = `${PIT}te.yaml`;

describe.skipIf(!pitAvailable())("the real pit bundle WITH its published roster (R4 live shape)", () => {
  const exportData = JSON.parse(readFileSync(PIT_TOPOLOGY, "utf8")) as TopologyExport;
  const conduits = parseConduits(readFileSync(PIT_CAST_YAML, "utf8"));
  const graph = buildGraph(exportData, {
    imprintId: "pit-395a61f04e-1780405200",
    deployMinOccupancy: 0.8,
    conduits,
  });

  it("declares a conduit for every observed pair -- WHY the budget is inert here", () => {
    expect(conduits).toHaveLength(40);
    expect(graph.conduits).toHaveLength(40);
    expect(graph.trafficPairs).toHaveLength(40);
    const declared = new Set(graph.conduits.map((c) => `${c.a}|${c.b}`));
    const undeclared = graph.trafficPairs.filter((p) => !declared.has(`${p.a}|${p.b}`));
    expect(undeclared).toEqual([]);
  });

  it("draws 40 ASSERTED arcs, hides nothing, and crosses no tile", () => {
    // With the groups expanded: one curve per declared conduit (R6 restatement).
    const l0 = layout0(graph, {}, { expanded: everySite(graph) });
    expect(l0.arcs).toHaveLength(40);
    expect(l0.arcs.filter((a) => a.kind === "asserted")).toHaveLength(40);
    expect(l0.arcs.filter((a) => a.kind === "observed")).toEqual([]);
    expect(l0.hiddenPairs).toBe(0);
    expect(noConnectorCrossesATile(l0)).toEqual([]);
    expect(l0.rails.find((r) => r.tier === "east-west")!.box.h).toBeLessThanOrEqual(
      ARC_DEPTH_MAX + ARC_BAND_PAD,
    );
  });

  it("under the DEFAULT per-group budget, folds rather than drops (R6)", () => {
    // The live shape an operator opens. Fewer curves than conduits, and not one
    // link lost: every pair is drawn, merged into a group's anchor, or counted.
    const l0 = layout0(graph);
    expect(l0.arcs.length).toBeLessThan(40);
    expect(l0.foldedPairs).toBeGreaterThan(0);
    const drawn = l0.arcs.reduce((n, a) => n + a.pairs, 0);
    expect(drawn + l0.hiddenPairs + l0.internalPairs).toBe(40);
    expect(noConnectorCrossesATile(l0)).toEqual([]);
    expect(l0.rails.find((r) => r.tier === "east-west")!.box.h).toBeLessThanOrEqual(
      ARC_DEPTH_MAX + ARC_BAND_PAD,
    );
  });

  it("makes `?ew=all` a NO-OP on this shape -- widths included", () => {
    // The consequence worth naming: with nothing hidden the toggle is not even
    // rendered, so D2's "3.06 px among 12 versus 4.74 px across 40" describes a
    // view no operator can currently open on this bundle.
    const expanded = everySite(graph);
    const top = layout0(graph, {}, { expanded });
    const all = layout0(graph, {}, { showAllPairs: true, expanded });
    expect(all.arcs).toHaveLength(top.arcs.length);
    expect(all.hiddenPairs).toBe(0);
    expect(all.arcs.map((a) => a.strokeWidth)).toEqual(top.arcs.map((a) => a.strokeWidth));
  });

  it("carries the observed volume on every declared arc, widest at the busiest pair", () => {
    const l0 = layout0(graph, {}, { expanded: everySite(graph) });
    // Every conduit this cast declares was also seen on the wire, so none is
    // drawn dashed -- the silent case needs a twin that declares a path the
    // capture never saw (covered in `r4.arcs.test.ts`).
    expect(l0.arcs.every((a) => a.hasTraffic)).toBe(true);
    const widest = [...l0.arcs].sort((a, b) => b.strokeWidth - a.strokeWidth)[0]!;
    const cidr = (name: string) => graph.segmentByName.get(name)!.observedCidr;
    expect([cidr(widest.a), cidr(widest.b)].sort()).toEqual(["10.80.20.0/24", "10.80.75.0/24"]);
  });
});
