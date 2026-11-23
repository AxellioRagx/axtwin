/**
 * Round 3 item 2 (`SPEC.md` "OSPF writes"): OSPF comes up from the canvas alone. Ticking "run OSPF
 * on these links" writes a fabric region, each router's `routing:` block (region, role, loopback from
 * LOOPBACK_POOL) and `ospf_area` on each router's other legs; a later leg on an OSPF router (a peer
 * link, a rack LAN) takes its area; a copied loopback is renumbered.
 */
import { describe, expect, it } from "vitest";

import { ipv4Range, LOOPBACK_POOL, nextFreeLan, nextFreeLoopback } from "../src/features/edit-canvas/edit/allocate";
import { connectRouters, wireRouters } from "../src/features/edit-canvas/edit/connect";
import { readDoc } from "../src/features/edit-canvas/edit/document";
import { enableOspf, ospfFollow, ospfRoleFor } from "../src/features/edit-canvas/edit/ospf";
import { addFabricRegion, readTe, setNodeNets, setNodeRouting } from "../src/features/edit-canvas/edit/te";

const l3 = (name: string) => `  - name: ${name}\n    role: l3-switch\n    kind: router\n    image: vyos@2026.06\n`;
const TE = `twin_config:\n  id: dc3\n  segments:\n    - { name: internet, cidr: 10.250.0.0/24, zone_type: vlan }\n    - { name: rack1-lan, cidr: 10.200.0.0/24, zone_type: vlan }\n  nodes:\n${l3("core-1")}    nets:\n      - { segment: internet }\n${l3("core-2")}${l3("agg-1")}    nets:\n      - { segment: rack1-lan, ip: 10.200.0.2 }\n${l3("agg-2")}  - name: rack1-web\n    image: debian-12-cloud@v1\n    net: rack1-lan\n`;
const doc = (te: string) => ({ id: "dc3", teFile: "te.yaml", files: { "te.yaml": te } });

describe("the loopback pool", () => {
  it("is outside the transit, LAN and internet pools and clear of the lab's 10.255.251.0/24 transport", () => {
    const p = ipv4Range(LOOPBACK_POOL)!;
    for (const other of ["10.254.0.0/16", "10.250.0.0/16", "10.200.0.0/16", "10.255.251.0/24"]) {
      const o = ipv4Range(other)!;
      expect(p.start <= o.end && o.start <= p.end, other).toBe(false);
    }
  });

  it("hands out /32s from .1, skipping what is taken (bare or /32)", () => {
    expect(nextFreeLoopback([])).toBe("10.255.0.1/32");
    expect(nextFreeLoopback(["10.255.0.1/32", "10.255.0.2"])).toBe("10.255.0.3/32");
  });

  it("is never handed out as a LAN /24", () => {
    expect(nextFreeLan("10.255", [])).toBe("10.255.1.0/24");
  });
});

describe("the te writers", () => {
  it("addFabricRegion writes fabric: / regions: when absent and appends when present; a taken id is refused", () => {
    let t = addFabricRegion(TE, { id: "dc", igp: "ospf" });
    expect(readTe(t).fabricRegions).toEqual([{ id: "dc", igp: "ospf" }]);
    t = addFabricRegion(t, { id: "wan", igp: "none" });
    expect(readTe(t).fabricRegions).toEqual([{ id: "dc", igp: "ospf" }, { id: "wan", igp: "none" }]);
    expect(() => addFabricRegion(t, { id: "dc", igp: "ospf" })).toThrow(/already exists/);
    expect(readTe(t).nodes).toHaveLength(5);
  });

  it("setNodeRouting writes one flow map on a block node and on a flow node, and removes it", () => {
    const flow = TE.replace(l3("core-2"), "  - { name: core-2, role: l3-switch, kind: router, image: vyos@2026.06 }\n");
    let t = setNodeRouting(flow, "core-1", { region: "dc", role: "spine", loopback: "10.255.0.1/32" });
    t = setNodeRouting(t, "core-2", { region: "dc", role: "spine", loopback: "10.255.0.2/32" });
    const te = readTe(t);
    expect(te.nodes.find((n) => n.name === "core-1")!.routing).toEqual({ region: "dc", role: "spine", loopback: "10.255.0.1/32" });
    expect(te.nodes.find((n) => n.name === "core-2")!.routing).toEqual({ region: "dc", role: "spine", loopback: "10.255.0.2/32" });
    expect(t).toContain("    routing: { region: dc, role: spine, loopback: 10.255.0.1/32 }");
    expect(readTe(setNodeRouting(t, "core-1", null)).nodes.find((n) => n.name === "core-1")!.routing).toBeNull();
  });
});

describe("run OSPF on these links", () => {
  it("the role follows the tier's prefix: core -> spine, agg -> leaf, else area", () => {
    expect([ospfRoleFor("core-1"), ospfRoleFor("agg-2"), ospfRoleFor("l3sw-1")]).toEqual(["spine", "leaf", "area"]);
  });

  it("the wiring writes the region, a routing: block on all four, and ospf_area on the transits, the LAN and the internet leg", () => {
    const d = doc(TE);
    const r = wireRouters(d, readDoc(d), ["core-1", "core-2", "agg-1", "agg-2"], "across", { ospfArea: 0, split: 2 });
    const te = readTe(r.files["te.yaml"]!);
    expect(te.fabricRegions).toEqual([{ id: "dc", igp: "ospf" }]);
    const routing = Object.fromEntries(te.nodes.filter((n) => n.routing).map((n) => [n.name, n.routing]));
    expect(routing).toEqual({
      "core-1": { region: "dc", role: "spine", loopback: "10.255.0.1/32" },
      "core-2": { region: "dc", role: "spine", loopback: "10.255.0.2/32" },
      "agg-1": { region: "dc", role: "leaf", loopback: "10.255.0.3/32" },
      "agg-2": { region: "dc", role: "leaf", loopback: "10.255.0.4/32" },
    });
    expect(r.made).toHaveLength(4);
    for (const s of te.segments) expect([s.name, s.ospfArea]).toEqual([s.name, 0]);
    expect(r.fabric).toMatchObject({ region: "dc", regionAdded: true, advertised: ["internet", "rack1-lan"] });
    expect(r.files["te.yaml"]).not.toContain("router_id");
  });

  it("reuses an OSPF region the twin has, keeps a router's existing loopback, and skips a router in a non-OSPF region", () => {
    let t = addFabricRegion(TE, { id: "fab", igp: "both" });
    t = addFabricRegion(t, { id: "wan", igp: "none" });
    t = setNodeRouting(t, "core-1", { region: "fab", role: "border", loopback: "10.255.0.9/32" });
    t = setNodeRouting(t, "core-2", { region: "wan", role: "edge", loopback: "10.255.0.8/32" });
    const r = enableOspf(t, ["core-1", "core-2", "agg-1"], 0);
    const te = readTe(r.text);
    expect(r.regionAdded).toBe(false);
    expect(te.nodes.find((n) => n.name === "core-1")!.routing).toEqual({ region: "fab", role: "border", loopback: "10.255.0.9/32" });
    expect(te.nodes.find((n) => n.name === "agg-1")!.routing).toEqual({ region: "fab", role: "leaf", loopback: "10.255.0.1/32" });
    expect(r.skipped).toEqual(["core-2"]);
  });

  it("a later peer link between two OSPF routers gets the same ospf_area (agg-1--agg-2)", () => {
    const d = doc(TE);
    const wired = wireRouters(d, readDoc(d), ["core-1", "core-2", "agg-1", "agg-2"], "across", { ospfArea: 0, split: 2 }).files["te.yaml"]!;
    const d2 = doc(wired);
    const peer = connectRouters(d2, readDoc(d2), "agg-1", "agg-2").files["te.yaml"]!;
    expect(readTe(peer).segments.find((s) => s.name === "agg-1--agg-2")!.ospfArea).toBeNull();
    const followed = ospfFollow(wired, peer);
    expect(readTe(followed).segments.find((s) => s.name === "agg-1--agg-2")!.ospfArea).toBe(0);
  });

  it("a rack LAN an OSPF router becomes the gateway of later is advertised; a non-OSPF router's is not", () => {
    const d = doc(TE);
    let wired = wireRouters(d, readDoc(d), ["core-1", "agg-1"], "chain", { ospfArea: 0 }).files["te.yaml"]!;
    wired = wired.replace("  - name: rack1-web", "  - name: r9\n    kind: router\n    image: vyos@2026.06\n  - name: rack1-web");
    const withRack2 = wired.replace("    - { name: rack1-lan,", "    - { name: rack2-lan, cidr: 10.200.1.0/24, zone_type: vlan }\n    - { name: rack3-lan, cidr: 10.200.2.0/24, zone_type: vlan }\n    - { name: rack1-lan,");
    let after = setNodeNets(withRack2, "agg-1", [...readTe(withRack2).nodes.find((n) => n.name === "agg-1")!.nets, { segment: "rack2-lan", ip: "10.200.1.2" }]);
    after = setNodeNets(after, "r9", [{ segment: "rack3-lan", ip: "10.200.2.1" }]);
    const te = readTe(ospfFollow(withRack2, after));
    expect(te.segments.find((s) => s.name === "rack2-lan")!.ospfArea).toBe(0);
    expect(te.segments.find((s) => s.name === "rack3-lan")!.ospfArea).toBeNull();
  });

  it("a copied router's loopback is renumbered; the original keeps its own", () => {
    const t = setNodeRouting(addFabricRegion(TE, { id: "dc", igp: "ospf" }), "core-1", { region: "dc", role: "spine", loopback: "10.255.0.1/32" });
    const copy = setNodeRouting(t, "core-2", { region: "dc", role: "spine", loopback: "10.255.0.1/32" });
    const te = readTe(ospfFollow(t, copy));
    expect(te.nodes.find((n) => n.name === "core-1")!.routing!.loopback).toBe("10.255.0.1/32");
    expect(te.nodes.find((n) => n.name === "core-2")!.routing!.loopback).toBe("10.255.0.2/32");
  });

  it("does nothing on a twin with no OSPF region", () => {
    expect(ospfFollow(TE, TE.replace("rack1-web", "rack1-www"))).toBe(TE.replace("rack1-web", "rack1-www"));
  });
});
