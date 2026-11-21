/**
 * The L3 switch device and the gateway-on-a-LAN connect (2026-11-21, `l3-switch-canvas-prompt` A1-A5):
 * the "L3 switch" card (between Router and Switch; profile `l3-switch`; found by "l3", "layer 3",
 * "core", "aggregation", "multilayer"), its drop (N separate `kind: router`, `role: l3-switch` nodes
 * on the profile's image; the tier seeds the prefix only), and router / L3 switch -> switch making
 * the router the LAN's gateway (an addressed leg, `.1` then `.2`, one single-segment link per
 * segment) instead of a bare trunk.
 */
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ImageProfile } from "../src/api/types";
import { DEVICE_CARDS, healthHints, imageFor, isL3Switch, profileFor, searchAdd, twoGatewayLans } from "../src/features/edit-canvas/add/catalog";
import { applyDeviceDrop } from "../src/features/edit-canvas/add/drop";
import { gatewayAddress } from "../src/features/edit-canvas/edit/allocate";
import { applyIntent, connectIntent, intentHint, wirePairs, wireRouters } from "../src/features/edit-canvas/edit/connect";
import { danglingLinks, readDoc, removalPlan, removeDanglingLinks, removeNode, removeSegment, type WorkingDoc } from "../src/features/edit-canvas/edit/document";
import { attachDevices, copyPositions, duplicateSubtree, gatewayOnSwitch, nextNumberedRename, parkedDevices, uplinksOf } from "../src/features/edit-canvas/edit/subtree";
import { besideSubtree } from "../src/features/edit-canvas/add/placeDrop";
import { dragFollowers } from "../src/features/edit-canvas/canvas/free";
import { readTe, setNodeField } from "../src/features/edit-canvas/edit/te";
import { readManifest, setManifestCluster } from "../src/features/edit-canvas/edit/manifest";
import { effectiveGateway, shareGateway, stopSharing, vrrpImageGaps } from "../src/features/edit-canvas/edit/vrrp";
import { placeOnDevice, placeTarget } from "../src/features/edit-canvas/Canvas";
import { card, check, clickPane, dropPopover, openPalette, panel, seeded, twinYaml } from "./helpers/canvasFixtures";
import { renderAt } from "./helpers/render";

const PROFILES: ImageProfile[] = [
  { name: "router", image: "debian-12-cloud@v1" },
  { name: "router-ospf", image: "vyos@1.5" },
  { name: "l3-switch", image: "vyos@1.5" },
];

const doc = (te: string): WorkingDoc => ({ id: "dc", teFile: "te.yaml", files: { "te.yaml": te } });

/** Two aggregation routers and a rack switch on its own LAN with one server. */
const RACK = `twin_config:
  id: dc
  segments:
    - { name: rack1-srv, cidr: 10.200.0.0/24, zone_type: vlan }
  nodes:
    - { name: agg-1, kind: router, role: l3-switch, image: vyos@1.5 }
    - { name: agg-2, kind: router, role: l3-switch, image: vyos@1.5 }
    - { name: rack1-tor, kind: switch, role: l2-switch, net: rack1-srv }
    - name: rack1-web
      image: debian-12-cloud@v1
      nets:
        - { segment: rack1-srv, ip: 10.200.0.10 }
`;

describe("A2: the L3 switch card", () => {
  it("stands between Router and Switch with its line and prefix; its profile is l3-switch (vyos@1.5)", () => {
    expect(DEVICE_CARDS.map((c) => c.id)).toEqual(["router", "l3switch", "switch", "server", "workstation", "ot"]);
    expect(DEVICE_CARDS[1]).toMatchObject({ label: "L3 switch", line: "switches a LAN and routes between VLANs; core or aggregation", oses: null, prefix: "l3sw" });
    expect(profileFor("l3switch", null)).toBe("l3-switch");
    expect(imageFor(PROFILES, profileFor("l3switch", null))).toBe("vyos@1.5");
  });

  it.each(["l3", "layer 3", "core", "aggregation", "multilayer"])("search %j finds it first", (q) => {
    expect(searchAdd(q, [])[0]).toMatchObject({ kind: "device", card: "l3switch", label: "L3 switch" });
  });

  it("a node is an L3 switch by its role on a router, not by its kind or image", () => {
    expect(isL3Switch({ kind: "router", role: "l3-switch" })).toBe(true);
    expect(isL3Switch({ kind: "router", role: "gw" })).toBe(false);
    expect(isL3Switch({ kind: "host", role: "l3-switch" })).toBe(false);
  });
});

describe("A3 / B6: dropping an L3 switch", () => {
  it("count N writes N separate kind: router, role: l3-switch nodes on the profile's image, named by the tier's prefix", () => {
    const d = doc(RACK);
    const r = applyDeviceDrop(d, readDoc(d), { card: "l3switch", os: null, role: null, persona: null, count: 2, prefix: "core", tier: "core", target: { pane: true }, image: imageFor(PROFILES, "l3-switch") });
    expect(r.names).toEqual(["core-1", "core-2"]);
    const te = readTe(r.files["te.yaml"]!);
    for (const name of r.names) expect(te.nodes.find((n) => n.name === name)).toMatchObject({ kind: "router", role: "l3-switch", image: "vyos@1.5", count: 1, nets: [] });
    // The tier writes nothing beyond the prefix it seeded.
    expect(r.files["te.yaml"]).not.toMatch(/tier|core:|aggregation/);
  });

  it("count N on a router card writes N routers too (core-1, core-2), each with its own name", () => {
    const d = doc(RACK);
    const r = applyDeviceDrop(d, readDoc(d), { card: "router", os: null, role: null, persona: null, count: 3, prefix: "edge", target: { pane: true }, image: "debian-12-cloud@v1" });
    expect(r.names).toEqual(["edge-1", "edge-2", "edge-3"]);
    expect(readTe(r.files["te.yaml"]!).nodes.filter((n) => n.kind === "router" && n.name.startsWith("edge-")).every((n) => n.countValue === 1)).toBe(true);
  });
});

describe("A5: router or L3 switch -> switch makes the router the LAN's gateway", () => {
  it("the first router gets an addressed leg at .1 and one single-segment link; the second .2; one edit each", () => {
    let d = doc(RACK);
    const intent = connectIntent(readDoc(d).te, { kind: "router", name: "agg-1" }, { kind: "switch", name: "rack1-tor" });
    expect(intent).toEqual({ kind: "gateway", router: "agg-1", switch: "rack1-tor", segments: ["rack1-srv"] });
    expect(intentHint(intent)).toBe("agg-1 becomes the gateway of rack1-srv (rack1-tor)");
    d = { ...d, files: applyIntent(d, readDoc(d), intent) };
    // The drag's direction does not matter: switch -> router is the same gesture.
    const second = connectIntent(readDoc(d).te, { kind: "switch", name: "rack1-tor" }, { kind: "router", name: "agg-2" });
    d = { ...d, files: applyIntent(d, readDoc(d), second) };
    const m = readDoc(d);
    expect(m.te.nodes.find((n) => n.name === "agg-1")!.nets).toEqual([{ segment: "rack1-srv", ip: "10.200.0.1" }]);
    expect(m.te.nodes.find((n) => n.name === "agg-2")!.nets).toEqual([{ segment: "rack1-srv", ip: "10.200.0.2" }]);
    expect(m.te.links.map((l) => ({ a: l.a.device, b: l.b.device, mode: l.mode, native: l.native, vlans: l.vlans }))).toEqual([
      { a: "agg-1", b: "rack1-tor", mode: "trunk", native: "rack1-srv", vlans: ["rack1-srv"] },
      { a: "agg-2", b: "rack1-tor", mode: "trunk", native: "rack1-srv", vlans: ["rack1-srv"] },
    ]);
    // The ports differ on the switch; the canvas's own Check finds nothing wrong.
    expect(new Set(m.te.links.map((l) => l.b.port)).size).toBe(2);
    expect(m.findings.filter((f) => f.severity === "error")).toEqual([]);
    // Again: refused, it is already the gateway.
    expect(connectIntent(m.te, { kind: "router", name: "agg-1" }, { kind: "switch", name: "rack1-tor" })).toEqual({ kind: "refused", reason: "agg-1 is already the gateway on rack1-srv" });
  });

  it("a switch carrying two segments: a leg and a link for each", () => {
    const te = RACK.replace("    - { name: rack1-srv, cidr: 10.200.0.0/24, zone_type: vlan }\n", "    - { name: rack1-srv, cidr: 10.200.0.0/24, zone_type: vlan }\n    - { name: rack1-mgmt, cidr: 10.200.1.0/24, zone_type: vlan }\n").replace("net: rack1-srv }", "nets: [{ segment: rack1-srv }, { segment: rack1-mgmt }] }");
    const d = doc(te);
    const out = readDoc({ ...d, files: attachDevices(d, readDoc(d), "agg-1", "rack1-tor") });
    expect(out.te.nodes.find((n) => n.name === "agg-1")!.nets).toEqual([{ segment: "rack1-srv", ip: "10.200.0.1" }, { segment: "rack1-mgmt", ip: "10.200.1.1" }]);
    expect(out.te.links.map((l) => l.native)).toEqual(["rack1-srv", "rack1-mgmt"]);
  });

  it("an old bare trunk (no leg) is repaired by wiring the pair again: the leg, and the trunk replaced", () => {
    const te = `${RACK}  links:\n    - { a: { device: agg-1, port: eth1 }, b: { device: rack1-tor, port: P1 }, mode: trunk, vlans: [rack1-srv] }\n`;
    const d = doc(te);
    const intent = connectIntent(readDoc(d).te, { kind: "router", name: "agg-1" }, { kind: "switch", name: "rack1-tor" });
    expect(intent.kind).toBe("gateway");
    const m = readDoc({ ...d, files: applyIntent(d, readDoc(d), intent) });
    expect(m.te.nodes.find((n) => n.name === "agg-1")!.nets).toEqual([{ segment: "rack1-srv", ip: "10.200.0.1" }]);
    expect(m.te.links).toHaveLength(1);
    expect(m.te.links[0]).toMatchObject({ native: "rack1-srv", vlans: ["rack1-srv"], a: { device: "agg-1", port: "eth1" }, b: { device: "rack1-tor", port: "P1" } });
  });

  it("a switch that carries nothing is refused with the way out", () => {
    const te = RACK.replace("    - { name: rack1-tor, kind: switch, role: l2-switch, net: rack1-srv }\n", "    - { name: rack1-tor, kind: switch, role: l2-switch, net: rack1-srv }\n    - { name: bare-sw, kind: switch, role: l2-switch }\n");
    expect(connectIntent(readTe(te), { kind: "router", name: "agg-1" }, { kind: "switch", name: "bare-sw" })).toEqual({ kind: "refused", reason: "bare-sw carries no segment yet: drop a host on it first" });
  });

  it("gatewayAddress: .1 first, .2 next; an unpinned router leg holds .1; none left below .10 -> null", () => {
    const te = readTe(RACK);
    expect(gatewayAddress(te, "rack1-srv")).toBe("10.200.0.1");
    const one = { ...te, nodes: te.nodes.map((n) => (n.name === "agg-1" ? { ...n, nets: [{ segment: "rack1-srv", ip: null }] } : n)) };
    expect(gatewayAddress(one, "rack1-srv")).toBe("10.200.0.2");
    const full = { ...te, nodes: [...te.nodes, ...Array.from({ length: 9 }, (_, i) => ({ ...te.nodes[0]!, name: `r${i}`, nets: [{ segment: "rack1-srv", ip: `10.200.0.${i + 1}` }] }))] };
    expect(gatewayAddress(full, "rack1-srv")).toBeNull();
  });
});

function withTwin(id: string, files: Record<string, string>) {
  const base = seeded();
  return seeded({
    twins: { ...base.state.twins, [id]: { ...base.state.twins["corp-auth"]!, id } },
    documents: { ...base.state.documents, [id]: { id, te_file: "te.yaml", files: { "twin.yaml": twinYaml(id), ...files }, skipped: [] } },
    twinConfigs: [...base.state.twinConfigs, { id, description: null, cluster: null, parameterized: false, param_names: [], error: null }],
  });
}
const router = (name: string) => screen.getAllByTestId("canvas-router").find((r) => r.dataset["nodeId"] === name)!;

describe("A4: an L3 switch on the canvas", () => {
  const TE = RACK.replace("    - { name: agg-2, kind: router, role: l3-switch, image: vyos@1.5 }\n", "    - { name: agg-2, kind: router, role: firewall, image: debian-12-cloud@v1 }\n").replace("kind: router, role: l3-switch, image: vyos@1.5 }", "kind: router, role: l3-switch, image: vyos@1.5, net: rack1-srv }");
  it("wears its own icon and an L3 badge; a plain router neither; its inspector says 'L3 switch agg-1' and a router's help, never a switch's", async () => {
    const { api } = withTwin("dc", { "te.yaml": TE });
    renderAt(api, "/twins/dc/canvas");
    await screen.findAllByTestId("canvas-router");
    expect(router("agg-1").dataset["l3"]).toBe("true");
    expect(within(router("agg-1")).getByTestId("device-badge").textContent).toBe("L3");
    expect(router("agg-2").dataset["l3"]).toBeUndefined();
    expect(within(router("agg-2")).queryByTestId("device-badge")).toBeNull();
    fireEvent.click(router("agg-1"));
    const np = await panel().findByTestId("node-panel");
    expect(within(np).getByRole("heading", { level: 3 }).textContent).toBe("L3 switch agg-1");
    expect(within(np).queryByTestId("switch-note")).toBeNull();
    expect(np.textContent).not.toMatch(/a switch is a device wired by links/);
    // B8: a firewall-role router (a SWITCH_ROLES role) gets a router's help too.
    fireEvent.click(router("agg-2"));
    const fw = await panel().findByTestId("node-panel");
    expect(within(fw).getByRole("heading", { level: 3 }).textContent).toBe("Router agg-2");
    expect(within(fw).queryByTestId("switch-note")).toBeNull();
    expect(fw.textContent).not.toMatch(/a switch's attachments are its own ports|a switch is a device wired by links/);
  });
});

describe("B1: deleting a device takes what hangs on it", () => {
  const AGGS = `twin_config:
  id: dc
  segments:
    - { name: up, cidr: 10.1.0.0/24, zone_type: vlan }
  nodes:
    - { name: agg-1, kind: router, role: l3-switch, image: vyos@1.5, net: up }
    - { name: agg-2, kind: router, role: l3-switch, image: vyos@1.5, net: up }
`;
  /** A switch dropped on the pane, wired to both routers. */
  function wired(): WorkingDoc {
    let d = doc(AGGS);
    const r = applyDeviceDrop(d, readDoc(d), { card: "switch", os: null, role: null, persona: null, count: 1, prefix: "sw", target: { pane: true }, image: null });
    d = { ...d, files: r.files };
    for (const agg of ["agg-1", "agg-2"]) d = { ...d, files: applyIntent(d, readDoc(d), connectIntent(readDoc(d).te, { kind: "router", name: agg }, { kind: "switch", name: "sw-1" })) };
    return d;
  }

  it("add switch -> link to two routers -> delete switch -> the Check passes: no link, no segment, no leg left", () => {
    const d = wired();
    const before = readDoc(d);
    expect(before.te.links).toHaveLength(2);
    expect(removalPlan(before, "sw-1")).toEqual({ links: [0, 1], segments: ["switch-lan"], hosted: {} });
    const m = readDoc({ ...d, files: removeNode(d, before, "sw-1") });
    expect(m.te.links).toEqual([]);
    expect(m.te.segments.map((s) => s.name)).toEqual(["up"]);
    expect(m.te.nodes.find((n) => n.name === "agg-1")!.nets.map((l) => l.segment)).toEqual(["up"]);
    expect(m.findings.filter((f) => f.severity === "error")).toEqual([]);
    expect(danglingLinks(m.te)).toEqual([]);
  });

  it("a segment with hosts still on it is kept by default (it is implied again); unticked, it goes too", () => {
    let d = wired();
    const r = applyDeviceDrop(d, readDoc(d), { card: "server", os: "linux", role: "web-tier", persona: null, count: 2, prefix: "web", target: { segment: "switch-lan" }, image: "debian-12-cloud@v1" });
    d = { ...d, files: r.files };
    const m0 = readDoc(d);
    expect(removalPlan(m0, "sw-1").hosted).toEqual({ "switch-lan": 2 });
    const kept = readDoc({ ...d, files: removeNode(d, m0, "sw-1") });
    expect(kept.te.segments.map((s) => s.name)).toEqual(["up", "switch-lan"]);
    expect(kept.te.links).toEqual([]);
    expect(kept.findings.filter((f) => f.severity === "error")).toEqual([]);
    const gone = readDoc({ ...d, files: removeNode(d, m0, "sw-1", { keepSegments: false }) });
    expect(gone.te.segments.map((s) => s.name)).toEqual(["up"]);
    expect(gone.te.nodes.find((n) => n.name === "agg-2")!.nets.map((l) => l.segment)).toEqual(["up"]);
  });

  it("removing a segment drops it from the links that carry it; a link left carrying nothing goes", () => {
    const te = `${RACK.replace("net: rack1-srv }", "nets: [{ segment: rack1-srv }, { segment: other }] }").replace("    - { name: rack1-srv, cidr: 10.200.0.0/24, zone_type: vlan }\n", "    - { name: rack1-srv, cidr: 10.200.0.0/24, zone_type: vlan }\n    - { name: other, cidr: 10.200.1.0/24 }\n")}  links:\n    - { a: { device: agg-1, port: eth1 }, b: { device: rack1-tor, port: P1 }, mode: trunk, vlans: [rack1-srv, other] }\n    - { a: { device: agg-2, port: eth1 }, b: { device: rack1-tor, port: P2 }, mode: trunk, native: other, vlans: [other] }\n`;
    const d = doc(te);
    const m = readDoc({ ...d, files: removeSegment(d, readDoc(d), "other") });
    expect(m.te.links.map((l) => ({ native: l.native, vlans: l.vlans }))).toEqual([{ native: null, vlans: ["rack1-srv"] }]);
  });

  it("a dangling link (a node or a segment the twin does not have) is a design hint whose one click removes it", () => {
    const te = `${RACK}  links:\n    - { a: { device: agg-1, port: eth1 }, b: { device: gone-sw, port: P1 }, mode: trunk, native: rack1-srv, vlans: [rack1-srv] }\n    - { a: { device: agg-2, port: eth1 }, b: { device: rack1-tor, port: P2 }, mode: trunk, native: nowhere, vlans: [nowhere] }\n    - { a: { device: agg-2, port: eth2 }, b: { device: rack1-tor, port: P3 }, mode: trunk, native: rack1-srv, vlans: [rack1-srv] }\n`;
    const d = doc(te);
    const m = readDoc(d);
    expect(danglingLinks(m.te)).toEqual([0, 1]);
    expect(healthHints(m.te, [])).toContain("2 dangling links");
    const fixed = readDoc({ ...d, files: removeDanglingLinks(d, m) });
    expect(fixed.te.links.map((l) => l.a.port)).toEqual(["eth2"]);
    expect(healthHints(fixed.te, []).join(" ")).not.toMatch(/dangling/);
  });
});

describe("B2: a host dropped on a switch joins that switch's LAN", () => {
  const TOR = `twin_config:
  id: dc
  segments:
    - { name: rack1-srv, cidr: 10.200.0.0/24, zone_type: vlan }
    - { name: rack1-mgmt, cidr: 10.200.1.0/24, zone_type: vlan }
  nodes:
    - { name: agg-1, kind: router, role: l3-switch, image: vyos@1.5, nets: [{ segment: rack1-srv, ip: 10.200.0.1 }] }
    - { name: rack1-tor, kind: switch, role: l2-switch, net: rack1-srv }
    - { name: rack2-tor, kind: switch, role: l2-switch, nets: [{ segment: rack1-srv }, { segment: rack1-mgmt }] }
    - { name: web, image: debian-12-cloud@v1, nets: [{ segment: rack1-srv, ip: 10.200.0.10 }] }
`;
  const box = (id: string, kind: "router" | "device", name: string, x: number, y: number, w: number, h: number) => ({ id, position: { x, y }, width: w, height: h, data: { kind, name } }) as unknown as Parameters<typeof placeTarget>[0][number];

  it("the node under the pointer decides: a switch's one segment; of several, its pick with the choices to ask", () => {
    const te = readTe(TOR);
    const nodes = [box("r", "router", "agg-1", 0, 0, 200, 120), box("s1", "device", "rack1-tor", 150, 80, 80, 60), box("s2", "device", "rack2-tor", 400, 80, 80, 60)];
    expect(placeTarget(nodes, { x: 0, y: 0 }, te, "s1")).toEqual({ segment: "rack1-srv", via: "rack1-tor" });
    expect(placeTarget(nodes, { x: 0, y: 0 }, te, "s2")).toEqual({ segment: "rack1-srv", choices: ["rack1-srv", "rack1-mgmt"], via: "rack2-tor" });
    // No DOM answer (jsdom): of the boxes under the point the nearest centre wins -- the router's box
    // reaching over the switch no longer takes the drop.
    expect(placeTarget(nodes, { x: 190, y: 110 }, te)).toEqual({ segment: "rack1-srv", via: "rack1-tor" });
    expect(placeTarget(nodes, { x: 60, y: 40 }, te)).toEqual({ router: "agg-1" });
    // A host dropped on is its own segment.
    expect(placeOnDevice(te, "web")).toEqual({ segment: "rack1-srv" });
  });

  it("the drop popover asks which segment of a switch carrying several, and the host lands on the one picked", async () => {
    const { api, state } = withTwin("dc", { "te.yaml": TOR });
    renderAt(api, "/twins/dc/canvas");
    await screen.findAllByTestId("canvas-device");
    // A selected switch is where a card's click lands (it had gone to the view's centre).
    fireEvent.click(screen.getAllByTestId("canvas-device").find((d) => d.dataset["nodeId"] === "rack2-tor")!);
    fireEvent.click(card("server"));
    const pop = dropPopover();
    expect(screen.getByTestId("drop-popover").textContent).toContain("on rack2-tor");
    fireEvent.change(pop.getByTestId("drop-segment"), { target: { value: "rack1-mgmt" } });
    fireEvent.click(pop.getByTestId("drop-add"));
    await check();
    const files = (state.writes.filter((w) => w.op === "validate").at(-1)!.body as { files: Record<string, string> }).files;
    const added = readTe(files["te.yaml"]!).nodes.filter((n) => n.kind === "host" && n.name !== "web");
    expect(added).toHaveLength(1);
    expect(added[0]!.nets[0]!.segment).toBe("rack1-mgmt");
  });

  it("a card clicked with a one-segment switch selected lands on its LAN, no question asked", async () => {
    const { api, state } = withTwin("dc", { "te.yaml": TOR });
    renderAt(api, "/twins/dc/canvas");
    await screen.findAllByTestId("canvas-device");
    fireEvent.click(screen.getAllByTestId("canvas-device").find((d) => d.dataset["nodeId"] === "rack1-tor")!);
    fireEvent.click(card("server"));
    expect(screen.getByTestId("drop-popover").textContent).toMatch(/on rack1-(tor|srv)/);
    expect(dropPopover().queryByTestId("drop-segment")).toBeNull();
    fireEvent.click(dropPopover().getByTestId("drop-add"));
    await check();
    const files = (state.writes.filter((w) => w.op === "validate").at(-1)!.body as { files: Record<string, string> }).files;
    const added = readTe(files["te.yaml"]!).nodes.filter((n) => n.kind === "host" && n.name !== "web");
    expect(added.map((n) => n.nets[0]!.segment)).toEqual(["rack1-srv"]);
  });
});

describe("B3: Duplicate subtree makes the next rack", () => {
  /** rack1: a ToR on its LAN with web / app servers, dual-homed (A5) to agg-1 and agg-2. */
  function rack1(): WorkingDoc {
    let d = doc(`twin_config:
  id: dc
  segments:
    - { name: rack1-srv, cidr: 10.200.0.0/24, zone_type: vlan }
  nodes:
    - { name: agg-1, kind: router, role: l3-switch, image: vyos@1.5 }
    - { name: agg-2, kind: router, role: l3-switch, image: vyos@1.5 }
    - { name: rack1-tor, kind: switch, role: l2-switch, net: rack1-srv }
    - name: rack1-web
      image: debian-12-cloud@v1
      nets:
        - { segment: rack1-srv, ip: 10.200.0.10 }
    - { name: rack1-app, image: debian-12-cloud@v1, count: 2, net: rack1-srv }
`);
    for (const agg of ["agg-1", "agg-2"]) d = { ...d, files: gatewayOnSwitch(d, readDoc(d), agg, "rack1-tor") };
    return d;
  }

  it("names renumber the shared prefix (rack2, then rack3); 'same uplinks' wires the copy to agg-1 and agg-2 as its gateways", () => {
    let d = rack1();
    const m = readDoc(d);
    expect(uplinksOf(m, "rack1-tor")).toEqual(["agg-1", "agg-2"]);
    const r = duplicateSubtree(d, m, "rack1-tor", null, { sameUplinks: true });
    expect(r.root).toBe("rack2-tor");
    expect(r.names).toMatchObject({ "rack1-tor": "rack2-tor", "rack1-web": "rack2-web", "rack1-app": "rack2-app", "rack1-srv": "rack2-srv" });
    d = { ...d, files: r.files };
    const m2 = readDoc(d);
    expect(m2.te.segments.find((s) => s.name === "rack2-srv")!.cidr).toBe("10.200.1.0/24");
    expect(m2.te.nodes.find((n) => n.name === "agg-1")!.nets).toContainEqual({ segment: "rack2-srv", ip: "10.200.1.1" });
    expect(m2.te.nodes.find((n) => n.name === "agg-2")!.nets).toContainEqual({ segment: "rack2-srv", ip: "10.200.1.2" });
    expect(m2.te.links.filter((l) => l.b.device === "rack2-tor").map((l) => [l.a.device, l.native])).toEqual([["agg-1", "rack2-srv"], ["agg-2", "rack2-srv"]]);
    expect(m2.findings.filter((f) => f.severity === "error")).toEqual([]);
    expect(parkedDevices(m2.te)).toEqual([]);
    // The next copy (of either rack) is rack3.
    const r3 = duplicateSubtree(d, m2, "rack1-tor", null, { sameUplinks: true });
    expect(r3.root).toBe("rack3-tor");
    expect(Object.values(r3.names)).toEqual(expect.arrayContaining(["rack3-web", "rack3-srv"]));
  });

  it("without 'same uplinks' the copy is parked (no link to the aggregations), as before; a name with no number keeps -2", () => {
    const d = rack1();
    const r = duplicateSubtree(d, readDoc(d), "rack1-tor", null);
    expect(readDoc({ ...d, files: r.files }).te.links.filter((l) => l.b.device === "rack2-tor")).toEqual([]);
    expect(nextNumberedRename(readDoc(d).te, "access")).toBeNull();
    const rn = nextNumberedRename(readDoc(d).te, "rack1-tor")!;
    expect(rn("rack1-web", new Set())).toBe("rack2-web");
    expect(rn("shared-printer", new Set())).toBe("shared-printer-2");
  });

  it("the copy keeps its hosts' places relative to the root; with no drop point it stands beside the original", () => {
    const d = rack1();
    const m = readDoc(d);
    const known = { "host:rack1-tor": { x: 100, y: 200 }, "host:rack1-web": { x: 60, y: 330 }, "host:rack1-app": { x: 140, y: 330 } };
    const r = duplicateSubtree(d, m, "rack1-tor", null, { sameUplinks: true });
    const at = besideSubtree(m, "rack1-tor", known)!;
    expect(at.y).toBe(200);
    expect(at.x).toBeGreaterThan(140);
    const pos = copyPositions(m, "rack1-tor", r, known, at);
    expect(pos["host:rack2-web"]).toEqual({ x: at.x - 40, y: 330 });
    expect(pos["host:rack2-app"]).toEqual({ x: at.x + 40, y: 330 });
  });
});

describe("A6: two gateways on one LAN", () => {
  it("a second router on a LAN shows the hint (health line and the segment); one gateway or a transit does not", () => {
    let d = doc(RACK);
    d = { ...d, files: gatewayOnSwitch(d, readDoc(d), "agg-1", "rack1-tor") };
    expect(twoGatewayLans(readDoc(d).te)).toEqual([]);
    expect(healthHints(readDoc(d).te, []).join(" ")).not.toMatch(/gateways/);
    d = { ...d, files: gatewayOnSwitch(d, readDoc(d), "agg-2", "rack1-tor") };
    const te = readDoc(d).te;
    expect(twoGatewayLans(te)).toEqual([{ segment: "rack1-srv", routers: ["agg-1", "agg-2"] }]);
    expect(healthHints(te, [])).toContain("rack1-srv has two gateways (agg-1, agg-2) and no shared VIP — servers will use only one");
    // A router-to-router transit (two routers, nothing else, in the transit pool) is not a LAN.
    const transit = readTe(`twin_config:\n  id: t\n  segments:\n    - { name: a--b, cidr: 10.254.0.0/30, zone_type: vlan }\n  nodes:\n    - { name: a, kind: router, image: x, nets: [{ segment: a--b, ip: 10.254.0.1 }] }\n    - { name: b, kind: router, image: x, nets: [{ segment: a--b, ip: 10.254.0.2 }] }\n`);
    expect(twoGatewayLans(transit)).toEqual([]);
  });

  it("the segment's panel says it too", async () => {
    let d = doc(RACK);
    for (const agg of ["agg-1", "agg-2"]) d = { ...d, files: gatewayOnSwitch(d, readDoc(d), agg, "rack1-tor") };
    const { api } = withTwin("dc", { "te.yaml": d.files["te.yaml"]! });
    renderAt(api, "/twins/dc/canvas");
    await screen.findAllByTestId("canvas-device");
    fireEvent.click(screen.getAllByTestId("canvas-device").find((x) => x.dataset["nodeId"] === "rack1-tor")!);
    // The switch's panel lists what it carries; a click opens the segment's.
    const np = await panel().findByTestId("node-panel");
    fireEvent.click(within(within(np).getByTestId("carried-segments")).getByRole("button"));
    const sp = await panel().findByTestId("segment-panel");
    expect(within(sp).getByTestId("two-gateways").textContent).toBe("rack1-srv has two gateways (agg-1, agg-2) and no shared VIP — servers will use only one");
  });
});

describe("D5: one shared gateway (VRRP) on a LAN with two", () => {
  function dual(te = RACK): WorkingDoc {
    let d = doc(te);
    for (const agg of ["agg-1", "agg-2"]) d = { ...d, files: gatewayOnSwitch(d, readDoc(d), agg, "rack1-tor") };
    return d;
  }
  const seg = (d: WorkingDoc) => readDoc(d).te.segments.find((s) => s.name === "rack1-srv")!;

  it("A5 writes the LAN's gateway: the engine gives a segment with two router legs and no gateway: none", () => {
    let d = doc(RACK);
    d = { ...d, files: gatewayOnSwitch(d, readDoc(d), "agg-1", "rack1-tor") };
    expect(seg(d).gateway).toBe("10.200.0.1");
    d = { ...d, files: gatewayOnSwitch(d, readDoc(d), "agg-2", "rack1-tor") };
    expect(seg(d).gateway).toBe("10.200.0.1");
    expect(effectiveGateway(readDoc(d).te, "rack1-srv")).toBe("10.200.0.1");
  });

  it("Share one gateway: a vrrp block with the gateway as VIP, the member at the VIP re-pinned, the gateway line gone, the hint gone", () => {
    const d0 = dual();
    const d = { ...d0, files: shareGateway(d0, readDoc(d0), "rack1-srv") };
    const s = seg(d);
    expect(s.vrrp).toEqual({ vip: "10.200.0.1", members: ["agg-1", "agg-2"], vrid: null, priority: null });
    expect(s.gateway).toBeNull();
    const te = readDoc(d).te;
    expect(te.nodes.find((n) => n.name === "agg-1")!.nets).toEqual([{ segment: "rack1-srv", ip: "10.200.0.3" }]);
    expect(te.nodes.find((n) => n.name === "agg-2")!.nets).toEqual([{ segment: "rack1-srv", ip: "10.200.0.2" }]);
    expect(twoGatewayLans(te)).toEqual([]);
    expect(d.files["te.yaml"]).toContain("vrrp: { vip: 10.200.0.1, members: [agg-1, agg-2] }");
    // Stop sharing: back to the first member's address as the gateway.
    const back = { ...d, files: stopSharing(d, readDoc(d), "rack1-srv") };
    expect(seg(back).vrrp).toBeNull();
    expect(seg(back).gateway).toBe("10.200.0.3");
  });

  it("a member not on VyOS is switched to the l3-switch image in the same edit, when asked", () => {
    const d0 = dual(RACK.replace("    - { name: agg-2, kind: router, role: l3-switch, image: vyos@1.5 }", "    - { name: agg-2, kind: router, role: gw, image: debian-12-cloud@v1 }"));
    expect(vrrpImageGaps(readDoc(d0).te, ["agg-1", "agg-2"])).toEqual([{ router: "agg-2", image: "debian-12-cloud@v1" }]);
    const d = { ...d0, files: shareGateway(d0, readDoc(d0), "rack1-srv", { image: "vyos@1.5" }) };
    expect(readDoc(d).te.nodes.find((n) => n.name === "agg-2")!.image).toBe("vyos@1.5");
  });

  it("a member leaving: a group left with one member goes, the gateway the remaining router's address", () => {
    const d0 = dual();
    const d = { ...d0, files: shareGateway(d0, readDoc(d0), "rack1-srv") };
    const gone = { ...d, files: removeNode(d, readDoc(d), "agg-1") };
    expect(seg(gone).vrrp).toBeNull();
    expect(seg(gone).gateway).toBe("10.200.0.2");
  });

  it("a duplicate with the same uplinks keeps VRRP on the copy: the VIP at the same offset, both members with legs off the VIP", () => {
    const d0 = dual(`twin_config:
  id: dc
  segments:
    - { name: rack1-srv, cidr: 10.200.0.0/24, zone_type: vlan }
  nodes:
    - { name: agg-1, kind: router, role: l3-switch, image: vyos@1.5 }
    - { name: agg-2, kind: router, role: l3-switch, image: vyos@1.5 }
    - { name: rack1-tor, kind: switch, role: l2-switch, net: rack1-srv }
    - { name: rack1-web, image: debian-12-cloud@v1, count: 2, net: rack1-srv }
`);
    const d1 = { ...d0, files: shareGateway(d0, readDoc(d0), "rack1-srv") };
    const r = duplicateSubtree(d1, readDoc(d1), "rack1-tor", null, { sameUplinks: true });
    const m = readDoc({ ...d1, files: r.files });
    const copy = m.te.segments.find((s) => s.name === "rack2-srv")!;
    expect(copy.vrrp).toMatchObject({ vip: "10.200.1.1", members: ["agg-1", "agg-2"] });
    for (const agg of ["agg-1", "agg-2"]) {
      const leg = m.te.nodes.find((n) => n.name === agg)!.nets.find((l) => l.segment === "rack2-srv")!;
      expect(leg.ip).toMatch(/^10\.200\.1\.[2-9]$/);
    }
    expect(copy.gateway).toBeNull();
    expect(m.findings.filter((f) => f.severity === "error")).toEqual([]);
  });
});

describe("D5 on the page, and a flow-written node's field edit", () => {
  it("setNodeField on a node written as one flow line rewrites it in place (it had cut the node apart)", () => {
    const t = "twin_config:\n  id: dc\n  nodes:\n    - { name: agg-2, kind: router, role: gw, image: debian-12-cloud@v1, nets: [{ segment: a, ip: 10.0.0.1 }] }\n";
    const out = setNodeField(t, "agg-2", "image", "vyos@1.5");
    expect(out).toBe("twin_config:\n  id: dc\n  nodes:\n    - { name: agg-2, kind: router, role: gw, image: vyos@1.5, nets: [{ segment: a, ip: 10.0.0.1 }] }\n");
  });

  it("the segment panel offers 'Share one gateway (VRRP)', and once shared shows the VIP and the members", async () => {
    let d = doc(RACK);
    for (const agg of ["agg-1", "agg-2"]) d = { ...d, files: gatewayOnSwitch(d, readDoc(d), agg, "rack1-tor") };
    const { api, state } = withTwin("dc", { "te.yaml": d.files["te.yaml"]! });
    renderAt(api, "/twins/dc/canvas");
    await screen.findAllByTestId("canvas-device");
    fireEvent.click(screen.getAllByTestId("canvas-device").find((x) => x.dataset["nodeId"] === "rack1-tor")!);
    fireEvent.click(within(within(await panel().findByTestId("node-panel")).getByTestId("carried-segments")).getByRole("button"));
    const sp = await panel().findByTestId("segment-panel");
    const share = within(sp).getByTestId("vrrp-share");
    expect(share.textContent).toBe("Share one gateway (VRRP) at 10.200.0.1 between agg-1, agg-2");
    fireEvent.click(share);
    const v = await panel().findByTestId("vrrp");
    expect(within(v).getByLabelText("VIP")).toHaveProperty("value", "10.200.0.1");
    expect(within(v).getByTestId("vrrp-members").textContent).toContain("agg-1, agg-2");
    expect(panel().queryByTestId("two-gateways")).toBeNull();
    await check();
    const files = (state.writes.filter((w) => w.op === "validate").at(-1)!.body as { files: Record<string, string> }).files;
    expect(readTe(files["te.yaml"]!).segments[0]!.vrrp?.vip).toBe("10.200.0.1");
  });
});

describe("B4: in the free layout a dragged device carries what hangs from it", () => {
  const TE = readTe(`twin_config:
  id: dc
  segments:
    - { name: rack1-srv, cidr: 10.200.0.0/24 }
    - { name: lan, cidr: 10.200.1.0/24 }
    - { name: shared, cidr: 10.200.2.0/24 }
  nodes:
    - { name: agg-1, kind: router, image: vyos@1.5, nets: [{ segment: rack1-srv, ip: 10.200.0.1 }, { segment: lan }, { segment: shared }] }
    - { name: agg-2, kind: router, image: vyos@1.5, nets: [{ segment: shared }] }
    - { name: rack1-tor, kind: switch, role: l2-switch, net: rack1-srv }
    - { name: web, image: x, count: 2, net: rack1-srv }
    - { name: pc, image: x, net: lan }
    - { name: db, image: x, net: shared }
`);
  const nodes = [
    { id: "host:web01", data: { kind: "host", nodeId: "web01" } },
    { id: "host:web02", data: { kind: "host", nodeId: "web02" } },
    { id: "host:pc", data: { kind: "host", nodeId: "pc" } },
    { id: "host:db", data: { kind: "host", nodeId: "db" } },
    { id: "seg:lan", data: { kind: "segment", name: "lan" } },
    { id: "seg:shared", data: { kind: "segment", name: "shared" } },
  ] as never;
  it("a switch takes its hosts (a group's instances too)", () => {
    expect(dragFollowers(TE, nodes, "rack1-tor")).toEqual(["host:web01", "host:web02"]);
  });
  it("a router takes the implied switches it alone serves and their hosts, not a switch's LAN nor one it shares", () => {
    expect(dragFollowers(TE, nodes, "agg-1")).toEqual(["host:pc", "seg:lan"]);
    expect(dragFollowers(TE, nodes, "pc")).toEqual([]);
  });
});

describe("B6: counts on Router, L3 switch and Switch; Wire… between the new set and the selected one", () => {
  it("a Switch with count 2 on the pane: two switches, each on a LAN of its own", () => {
    const d = doc(RACK);
    const r = applyDeviceDrop(d, readDoc(d), { card: "switch", os: null, role: null, persona: null, count: 2, prefix: "sw", target: { pane: true }, image: null });
    expect(r.names).toEqual(["sw-1", "sw-2"]);
    const te = readTe(r.files["te.yaml"]!);
    const legs = r.names.map((n) => te.nodes.find((x) => x.name === n)!.nets[0]!.segment);
    expect(new Set(legs).size).toBe(2);
    expect(new Set(legs.map((sg) => te.segments.find((x) => x.name === sg)!.cidr)).size).toBe(2);
  });

  it("'across': each of the first set to each of the rest, none within a set", () => {
    expect(wirePairs(["core-1", "core-2", "agg-1", "agg-2"], "across", 2)).toEqual([["core-1", "agg-1"], ["core-1", "agg-2"], ["core-2", "agg-1"], ["core-2", "agg-2"]]);
    const te = `twin_config:\n  id: t\n  segments:\n    - { name: lan, cidr: 10.1.0.0/24 }\n  nodes:\n${["core-1", "core-2", "agg-1", "agg-2"].map((n) => `    - { name: ${n}, kind: router, role: l3-switch, image: vyos@1.5 }\n`).join("")}`;
    const d = doc(te);
    const r = wireRouters(d, readDoc(d), ["core-1", "core-2", "agg-1", "agg-2"], "across", { split: 2, ospfArea: 0 });
    expect(r.made).toEqual(["agg-1--core-1", "agg-2--core-1", "agg-1--core-2", "agg-2--core-2"]);
  });

  it("with core-1 and core-2 selected, dropping 2 L3 switches opens the wiring dialog: each core to each new agg", async () => {
    const te = `twin_config:\n  id: dc\n  segments:\n    - { name: up, cidr: 10.1.0.0/24 }\n  nodes:\n    - { name: core-1, kind: router, role: l3-switch, image: vyos@1.5, net: up }\n    - { name: core-2, kind: router, role: l3-switch, image: vyos@1.5, net: up }\n`;
    const { api } = withTwin("dc", { "te.yaml": te });
    renderAt(api, "/twins/dc/canvas");
    await screen.findAllByTestId("canvas-router");
    fireEvent.click(router("core-1"));
    fireEvent.click(router("core-2"), { shiftKey: true });
    fireEvent.click(card("l3switch"));
    const pop = dropPopover();
    fireEvent.click(within(pop.getByRole("group", { name: "Tier" })).getByRole("button", { name: "aggregation" }));
    fireEvent.change(pop.getByLabelText("Count"), { target: { value: "2" } });
    fireEvent.click(pop.getByTestId("drop-add"));
    const dlg = await screen.findByTestId("wire-dialog");
    expect(within(dlg).getByLabelText(/each of core-1, core-2 to each of agg-1, agg-2/)).toHaveProperty("checked", true);
    expect(within(dlg).getAllByTestId("wire-plan-row").map((r) => r.textContent)).toEqual(["new: agg-1--core-1", "new: agg-2--core-1", "new: agg-1--core-2", "new: agg-2--core-2"]);
  });
});

describe("B9: a click on the empty canvas always clears a multi-selection", () => {
  it("two routers Shift-selected, then a pane click: nothing selected", async () => {
    const te = `twin_config:\n  id: dc\n  segments:\n    - { name: up, cidr: 10.1.0.0/24 }\n  nodes:\n    - { name: core-1, kind: router, role: l3-switch, image: vyos@1.5, net: up }\n    - { name: core-2, kind: router, role: l3-switch, image: vyos@1.5, net: up }\n`;
    const { api } = withTwin("dc", { "te.yaml": te });
    renderAt(api, "/twins/dc/canvas");
    await screen.findAllByTestId("canvas-router");
    fireEvent.click(router("core-1"));
    fireEvent.click(router("core-2"), { shiftKey: true });
    await waitFor(() => expect(screen.getAllByTestId("canvas-router").filter((r) => r.closest(".react-flow__node")?.classList.contains("selected")).length).toBe(2));
    clickPane(900, 700);
    await waitFor(() => expect(screen.getAllByTestId("canvas-router").filter((r) => r.closest(".react-flow__node")?.classList.contains("selected")).length).toBe(0));
  });
});

describe("D6: a dropped device's image is the profile's image for the twin's target platform", () => {
  it("the manifest's cluster: is read, written and removed", () => {
    const m = twinYaml("dc");
    expect(readManifest(m).cluster).toBeNull();
    const set = setManifestCluster(m, "kvm1");
    expect(readManifest(set).cluster).toBe("kvm1");
    expect(readManifest(setManifestCluster(set, "dev45")).cluster).toBe("dev45");
    const gone = setManifestCluster(set, null);
    expect(readManifest(gone).cluster).toBeNull();
    expect(gone).toBe(m);
  });

  it("the Add panel names the target and platform; picking kvm1 writes cluster: and an L3 switch then gets kvm1's image", async () => {
    const base = withTwin("dc", { "te.yaml": "twin_config:\n  id: dc\n  segments:\n    - { name: up, cidr: 10.1.0.0/24 }\n  nodes: []\n" });
    const { api, state } = seeded({
      ...base.state,
      imageProfiles: [{ name: "l3-switch", image: "vyos@1.5", platforms: { libvirt: "debian-12-cloud@v1" } }],
      targets: [...base.state.targets, { node: "kvm1", name: "kvm1", default: false, nodes: ["kvm1"], platform: "libvirt", error: null }],
    });
    renderAt(api, "/twins/dc/canvas");
    await screen.findByTestId("rail-add");
    openPalette();
    const line = await screen.findByTestId("add-images");
    await waitFor(() => expect(line.textContent).toContain("proxmox"));
    fireEvent.change(within(line).getByTestId("add-images-target"), { target: { value: "kvm1" } });
    await waitFor(() => expect(screen.getByTestId("add-images").textContent).toContain("libvirt"));
    // The target travels with the design: an undo-able edit, shown in the picker.
    expect((screen.getByTestId("add-images-target") as HTMLSelectElement).value).toBe("kvm1");
    fireEvent.click(card("l3switch"));
    fireEvent.click(dropPopover().getByTestId("drop-add"));
    await check();
    const files = (state.writes.filter((w) => w.op === "validate").at(-1)!.body as { files: Record<string, string> }).files;
    expect(readManifest(files["twin.yaml"]!).cluster).toBe("kvm1");
    expect(readTe(files["te.yaml"]!).nodes.find((n) => n.role === "l3-switch")!.image).toBe("debian-12-cloud@v1");
  });
});
