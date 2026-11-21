/**
 * Round 3 item 5 (`SPEC.md` "Names"): a switch's LAN is named after it (`rack1-tor` -> `rack1-lan`) and
 * Duplicate renumbers it as it renumbers devices; one device takes the bare prefix (the -N suffix only
 * for N > 1); the two-gateway hint is for server and user LANs only; a core L3 switch uplinks to the
 * internet.
 */
import { describe, expect, it } from "vitest";

import { twoGatewayLans } from "../src/features/edit-canvas/add/catalog";
import { applyDeviceDrop, lanStemOf, type DeviceDrop } from "../src/features/edit-canvas/add/drop";
import { readDoc, type WorkingDoc } from "../src/features/edit-canvas/edit/document";
import { duplicateSubtree, gatewayOnSwitch } from "../src/features/edit-canvas/edit/subtree";
import { readTe } from "../src/features/edit-canvas/edit/te";

const BASE = `twin_config:
  id: dc
  segments: []
  nodes:
    - { name: agg-1, kind: router, role: l3-switch, image: vyos@2026.06 }
    - { name: agg-2, kind: router, role: l3-switch, image: vyos@2026.06 }
`;
const doc = (te: string): WorkingDoc => ({ id: "dc", teFile: "te.yaml", files: { "te.yaml": te } });
const drop = (d: WorkingDoc, spec: Partial<DeviceDrop> & Pick<DeviceDrop, "card" | "prefix" | "target">): WorkingDoc => ({ ...d, files: applyDeviceDrop(d, readDoc(d), { os: null, role: null, persona: null, count: 1, image: null, ...spec }).files });

describe("5a: a segment follows its device", () => {
  it("the stem drops the prefix's last token", () => {
    expect([lanStemOf("rack1-tor"), lanStemOf("sw"), lanStemOf("dc-core-sw")]).toEqual(["rack1", "sw", "dc-core"]);
  });

  it("a switch dropped as rack1-tor stands on rack1-lan; another as rack1-tor goes on rack1-lan-2", () => {
    let d = drop(doc(BASE), { card: "switch", prefix: "rack1-tor", target: { pane: true } });
    d = drop(d, { card: "switch", prefix: "rack1-tor", target: { pane: true } });
    const te = readTe(d.files["te.yaml"]!);
    expect(te.nodes.filter((n) => n.kind === "switch").map((n) => [n.name, n.nets[0]!.segment])).toEqual([
      ["rack1-tor", "rack1-lan"],
      ["rack1-tor-2", "rack1-lan-2"],
    ]);
  });

  it("Duplicate renumbers the segment as it does the devices: rack2-lan, rack3-lan (and B3 still renumbers rack1-tor)", () => {
    let d = drop(doc(BASE), { card: "switch", prefix: "rack1-tor", target: { pane: true } });
    d = drop(d, { card: "server", os: "linux", role: "web-tier", prefix: "rack1-web", count: 3, image: "debian-12-cloud@v1", target: { segment: "rack1-lan" } });
    for (const agg of ["agg-1", "agg-2"]) d = { ...d, files: gatewayOnSwitch(d, readDoc(d), agg, "rack1-tor") };
    for (let i = 0; i < 2; i++) d = { ...d, files: duplicateSubtree(d, readDoc(d), "rack1-tor", null, { sameUplinks: true }).files };
    const te = readTe(d.files["te.yaml"]!);
    expect(te.segments.map((s) => s.name)).toEqual(["rack1-lan", "rack2-lan", "rack3-lan"]);
    expect(te.nodes.filter((n) => n.kind !== "router").map((n) => n.name)).toEqual(["rack1-tor", "rack1-web", "rack2-tor", "rack2-web", "rack3-tor", "rack3-web"]);
  });
});

describe("5b: one device takes the bare prefix", () => {
  it("count 1 is bare; count N numbers from -1; a taken bare name continues at -2", () => {
    let d = drop(doc(BASE), { card: "l3switch", prefix: "core", count: 1, target: { pane: true }, image: "vyos@2026.06" });
    d = drop(d, { card: "l3switch", prefix: "core", count: 1, target: { pane: true }, image: "vyos@2026.06" });
    d = drop(d, { card: "router", prefix: "edge", count: 2, target: { pane: true }, image: "debian-12-cloud@v1" });
    expect(readTe(d.files["te.yaml"]!).nodes.map((n) => n.name)).toEqual(["agg-1", "agg-2", "core", "core-2", "edge-1", "edge-2"]);
  });
});

describe("5c: the two-gateway hint", () => {
  const te = (extra: string) =>
    readTe(`twin_config:
  id: dc
  segments:
    - { name: internet, cidr: 10.250.0.0/24, zone_type: vlan }
    - { name: rack1-lan, cidr: 10.200.0.0/24, zone_type: vlan }
    - { name: rack2-lan, cidr: 10.200.1.0/24, zone_type: vlan }
  nodes:
    - { name: internet-sink, role: internet-sink, image: debian-12-cloud@v1, nets: [{ segment: internet, ip: 10.250.0.10 }] }
    - { name: core-1, kind: router, image: vyos@2026.06, nets: [{ segment: internet, ip: 10.250.0.1 }] }
    - { name: core-2, kind: router, image: vyos@2026.06, nets: [{ segment: internet, ip: 10.250.0.2 }] }
    - { name: agg-1, kind: router, image: vyos@2026.06, nets: [{ segment: rack1-lan, ip: 10.200.0.2 }, { segment: rack2-lan, ip: 10.200.1.2 }] }
    - { name: agg-2, kind: router, image: vyos@2026.06, nets: [{ segment: rack1-lan, ip: 10.200.0.3 }, { segment: rack2-lan, ip: 10.200.1.3 }] }
    - { name: rack2-tor, kind: switch, net: rack2-lan }
${extra}`);

  it("fires on a server LAN with two gateways; never on the internet, nor on a LAN with no host yet", () => {
    expect(twoGatewayLans(te(""))).toEqual([]);
    expect(twoGatewayLans(te("    - { name: rack1-web, image: debian-12-cloud@v1, net: rack1-lan }\n"))).toEqual([{ segment: "rack1-lan", routers: ["agg-1", "agg-2"] }]);
  });
});

describe("5d: a core L3 switch uplinks to the internet", () => {
  it("each core gets a leg on the internet, pinned .1 and .2; the internet and its sink are made when absent", () => {
    const d = drop(doc(BASE), { card: "l3switch", prefix: "core", count: 2, target: { pane: true }, image: "vyos@2026.06", tier: "core", uplink: true, sinkImage: "debian-12-cloud@v1" });
    const te = readTe(d.files["te.yaml"]!);
    expect(te.segments.find((s) => s.name === "internet")?.cidr).toBe("10.250.0.0/24");
    expect(te.nodes.find((n) => n.name === "internet-sink")).toMatchObject({ image: "debian-12-cloud@v1" });
    expect(te.nodes.filter((n) => n.name.startsWith("core")).map((n) => n.nets)).toEqual([[{ segment: "internet", ip: "10.250.0.1" }], [{ segment: "internet", ip: "10.250.0.2" }]]);
  });

  it("no uplink unless asked (an aggregation tier, or the box unticked)", () => {
    const d = drop(doc(BASE), { card: "l3switch", prefix: "core", count: 1, target: { pane: true }, image: "vyos@2026.06", tier: "core", uplink: false });
    expect(readTe(d.files["te.yaml"]!).segments).toEqual([]);
  });
});
