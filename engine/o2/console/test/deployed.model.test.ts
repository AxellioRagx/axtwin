/**
 * The deployed-twin model and its map layout, over the REAL 106-host
 * corp-twin (`fixtures/corp-twin-hosts.json`, built from the live read-only
 * inventory + tfc + tap reads of the standing deploy and joined into the
 * `/hosts` contract shape -- see its `_provenance`). Assembled through the
 * same two functions the Twin page calls (`buildDeployedTwin` ->
 * `layoutDeployed0/1`), so this is the map an operator opens (LES-311).
 */
import { describe, expect, it } from "vitest";

import {
  BUS_INSET,
  DEPLOYED_BUDGET,
  isLink,
  layoutDeployed0,
  layoutDeployed1,
  LINKS_GROUP,
} from "../src/layout/deployed";
import { buildDeployedTwin, filterHosts, sortHosts, worstStatus } from "../src/model/deployed";
import type { Box } from "../src/layout/geometry";
import { corpTwinHosts } from "./helpers/runtime";

const doc = corpTwinHosts();
const twin = buildDeployedTwin(doc);

function inside(p: { x: number; y: number }, b: Box): boolean {
  return p.x > b.x && p.x < b.x + b.w && p.y > b.y && p.y < b.y + b.h;
}

describe("buildDeployedTwin over the real corp-twin", () => {
  it("keeps every host, persona instance and net the store recorded", () => {
    expect(twin.hosts).toHaveLength(106);
    expect(twin.personaInstanceCount).toBe(84);
    expect(twin.hostsWithPersonas).toBe(83);
    // Every non-WAN net a host has a NIC on is a segment, once.
    const nets = new Set(doc.hosts.flatMap((h) => h.nets.filter((n) => !n.external).map((n) => n.net_id)));
    expect(twin.segments.map((s) => s.id).sort()).toEqual([...nets].sort());
  });

  it("finds the router by its WAN uplink and draws the Internet", () => {
    expect(twin.routers.map((r) => r.id)).toEqual(["hq-mx"]);
    expect(twin.hasWan).toBe(true);
    // The WAN is not a segment.
    expect(twin.segmentById.has("__wan__")).toBe(false);
  });

  it("never turns an unknown into a zero: a host with no address has none, not 0.0.0.0", () => {
    const noAddr = twin.hosts.find((h) => h.ips.length === 0);
    expect(noAddr).toBeDefined();
    expect(noAddr!.ips).toEqual([]);
  });

  it("rolls status up WORST first, and an unknown does not win", () => {
    expect(worstStatus(["completed", "failed", "running"])).toBe("failed");
    expect(worstStatus(["completed", null])).toBe("completed");
    expect(worstStatus([null, null])).toBeNull();
  });
});

describe("layoutDeployed0 -- level 0 stays readable at 106 hosts", () => {
  const l0 = layoutDeployed0(twin);

  it("is deterministic", () => {
    expect(JSON.stringify(layoutDeployed0(buildDeployedTwin(corpTwinHosts())))).toBe(JSON.stringify(l0));
  });

  it("stays under 1400 units wide whatever the net count (groups stack, they do not widen)", () => {
    expect(l0.width).toBeLessThan(1400);
  });

  it("accounts for every segment exactly once: drawn as a tile, or disclosed in its group's +N more", () => {
    const seen: string[] = [];
    for (const g of l0.groups) {
      seen.push(...g.tiles.map((t) => t.segmentId));
      if (g.overflow && !g.overflow.expanded) seen.push(...g.overflow.hidden);
      expect(g.tiles.length + (g.overflow && !g.overflow.expanded ? g.overflow.hidden.length : 0)).toBe(g.total);
    }
    expect(seen.sort()).toEqual(twin.segments.map((s) => s.id).sort());
  });

  it("puts transit links in their own collapsed group, so the host segments get the space", () => {
    const links = l0.groups.find((g) => g.key === LINKS_GROUP);
    expect(links).toBeDefined();
    expect(links!.tiles).toHaveLength(0);
    expect(links!.overflow?.hidden.length).toBe(twin.segments.filter(isLink).length);
    // The largest host segment is drawn, not hidden.
    const biggest = [...twin.segments].filter((s) => !isLink(s)).sort((a, b) => b.hosts.length - a.hosts.length)[0]!;
    expect(l0.groups.some((g) => g.tiles.some((t) => t.segmentId === biggest.id))).toBe(true);
    // No host group draws more than its budget.
    for (const g of l0.groups) expect(g.tiles.length).toBeLessThanOrEqual(DEPLOYED_BUDGET);
  });

  it("expanding a group draws all of its segments and nothing else moves above it", () => {
    const links = l0.groups.find((g) => g.key === LINKS_GROUP)!;
    const open = layoutDeployed0(twin, new Set([LINKS_GROUP]));
    const after = open.groups.find((g) => g.key === LINKS_GROUP)!;
    expect(after.tiles).toHaveLength(links.total);
    for (const g of l0.groups.filter((x) => x.box.y < links.box.y)) {
      expect(open.groups.find((x) => x.key === g.key)!.box).toEqual(g.box);
    }
  });

  it("no trunk leg crosses a tile: a leg runs from the bus to its group's LEFT edge", () => {
    const tiles = l0.groups.flatMap((g) => g.tiles.map((t) => t.box));
    for (const g of l0.groups) {
      if (!g.leg) continue;
      const [a, b] = g.leg;
      expect(a!.y).toBe(b!.y);
      expect(b!.x).toBe(g.box.x);
      for (let x = a!.x; x <= b!.x; x += 4) {
        for (const t of tiles) expect(inside({ x, y: a!.y }, t)).toBe(false);
      }
    }
    expect(l0.bus![0]!.x).toBeLessThan(l0.groups[0]!.box.x);
    expect(l0.groups[0]!.box.x - l0.bus![0]!.x).toBe(BUS_INSET / 2);
  });
});

describe("layoutDeployed1 -- one segment drilled", () => {
  it("draws every host of the largest segment, devices first", () => {
    const biggest = [...twin.segments].sort((a, b) => b.hosts.length - a.hosts.length)[0]!;
    const l1 = layoutDeployed1(twin, biggest.id)!;
    const drawn = l1.bands.flatMap((b) => b.chips.map((c) => c.hostId));
    expect(drawn.sort()).toEqual([...biggest.hosts].sort());
    const devices = biggest.hosts.filter((id) => twin.hostById.get(id)!.multiHomed);
    if (devices.length > 0) expect(l1.bands[0]!.label).toBe("shared with other segments");
  });

  it("answers null for a segment the twin does not have", () => {
    expect(layoutDeployed1(twin, "no-such-net")).toBeNull();
  });
});

describe("the host table's filter and sort", () => {
  it("filters on every word, across name, address, role and persona", () => {
    const ws = twin.hosts.find((h) => h.raw.role === "corp-workstation")!;
    const hits = filterHosts(twin.hosts, { segment: null, q: `WORKSTATION ${ws.raw.os}` });
    expect(hits.map((h) => h.id)).toContain(ws.id);
    for (const h of hits) {
      expect(`${h.id} ${h.raw.role} ${h.personaIds.join(" ")}`.toLowerCase()).toContain("workstation");
      expect(h.raw.os).toBe(ws.raw.os);
    }
    // A word nothing carries matches nothing.
    expect(filterHosts(twin.hosts, { segment: null, q: "workstation zzz-nothing" })).toEqual([]);
  });

  it("narrows to a segment", () => {
    const seg = twin.segments[0]!;
    expect(filterHosts(twin.hosts, { segment: seg.id, q: "" }).map((h) => h.id).sort()).toEqual([...seg.hosts].sort());
  });

  it("sorts an unknown LAST in both directions -- unknown is not the smallest value", () => {
    const live = (id: string): string | null => (id === "hq-mx" ? "running" : id === "9k01" ? "stopped" : null);
    const asc = sortHosts(twin.hosts, "live", "asc", live);
    const desc = sortHosts(twin.hosts, "live", "desc", live);
    expect(asc.slice(0, 2).map((h) => h.id)).toEqual(["hq-mx", "9k01"]);
    expect(desc.slice(0, 2).map((h) => h.id)).toEqual(["9k01", "hq-mx"]);
    expect(live(asc.at(-1)!.id)).toBeNull();
    expect(live(desc.at(-1)!.id)).toBeNull();
  });
});
