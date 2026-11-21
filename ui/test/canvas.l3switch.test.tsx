/**
 * The L3 switch device and the gateway-on-a-LAN connect (2026-11-21, `l3-switch-canvas-prompt` A1-A5):
 * the "L3 switch" card (between Router and Switch; profile `l3-switch`; found by "l3", "layer 3",
 * "core", "aggregation", "multilayer"), its drop (N separate `kind: router`, `role: l3-switch` nodes
 * on the profile's image; the tier seeds the prefix only), and router / L3 switch -> switch making
 * the router the LAN's gateway (an addressed leg, `.1` then `.2`, one single-segment link per
 * segment) instead of a bare trunk.
 */
import { fireEvent, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ImageProfile } from "../src/api/types";
import { DEVICE_CARDS, imageFor, isL3Switch, profileFor, searchAdd } from "../src/features/edit-canvas/add/catalog";
import { applyDeviceDrop } from "../src/features/edit-canvas/add/drop";
import { gatewayAddress } from "../src/features/edit-canvas/edit/allocate";
import { applyIntent, connectIntent, intentHint } from "../src/features/edit-canvas/edit/connect";
import { readDoc, type WorkingDoc } from "../src/features/edit-canvas/edit/document";
import { attachDevices } from "../src/features/edit-canvas/edit/subtree";
import { readTe } from "../src/features/edit-canvas/edit/te";
import { panel, seeded, twinYaml } from "./helpers/canvasFixtures";
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
