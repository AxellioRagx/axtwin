/**
 * Segments drawn as switches, the edit side (`docs/design/topology-layers.md` §7.1, 2026-11-11):
 * where a dropped host lands -- an implied switch joins its segment, a real switch its one segment
 * (else its uplink trunk's native, else its busiest), a router a new segment in its site -- and
 * "Insert switch", which replaces an implied switch with a real `kind: switch` carrying the segment,
 * through the command stack, into a config the canvas's own checks accept.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import { placeOnDevice, placeTarget } from "../src/features/edit-canvas/Canvas";
import { ID } from "../src/features/edit-canvas/canvas/placement";
import { TREE_ID, placeTree } from "../src/features/edit-canvas/canvas/tree";
import { EMPTY_STACK, record, redo, undo } from "../src/features/edit-canvas/edit/commands";
import { documentGraph, moveTo, placeNodes, readDoc, type WorkingDoc } from "../src/features/edit-canvas/edit/document";
import { hostSwitch, insertSwitch, newSegmentOnRouter, switchSegment } from "../src/features/edit-canvas/edit/switches";
import { readTe } from "../src/features/edit-canvas/edit/te";
import { validateTe } from "../src/features/edit-canvas/edit/validate";
import { buildGraph } from "../src/features/edit-canvas/model/graph";

const CATALOG = resolve(__dirname, "../../catalog/twins");
const doc = (id: string, te: string): WorkingDoc => ({ id, teFile: "te.yaml", files: { "te.yaml": te } });
const catalogDoc = (id: string) => doc(id, readFileSync(resolve(CATALOG, id, "te.yaml"), "utf8"));
function placed(d: WorkingDoc) {
  const g = documentGraph(readDoc(d), d.id);
  return placeTree(buildGraph(g.exportData, g.cast), {}, { extraLegs: g.extraLegs, uplinks: g.uplinks, tree: g.tree });
}

/** A router, a core switch trunking two VLANs (native `users`) and an access switch on one. */
const MULTI = `twin_config:
  id: multi
  segments:
    - { name: users, cidr: 10.50.1.0/24 }
    - { name: voice, cidr: 10.50.2.0/24 }
    - { name: lab, cidr: 10.50.3.0/24 }
  nodes:
    - { name: gw, kind: router, image: vyos@v1, nets: [{ segment: users }, { segment: voice }, { segment: lab }] }
    - { name: core, kind: switch, role: l2-switch }
    - { name: edge, kind: switch, role: l2-switch }
    - { name: bare, kind: switch, role: l2-switch }
    - { name: phones, role: voip, image: img@v1, count: 5, net: voice }
    - { name: pc, role: workstation, image: img@v1, net: users }
  links:
    - { a: { device: gw, port: eth1 }, b: { device: core, port: P1 }, mode: trunk, native: users, vlans: [users, voice] }
    - { a: { device: gw, port: eth2 }, b: { device: edge, port: P1 }, mode: trunk, vlans: [voice, lab] }
`;

describe("which segment a host dropped on a switch joins", () => {
  const te = readTe(MULTI);
  it("of several: the uplink trunk's native; with no native, the most-populated; nothing carried, nowhere", () => {
    expect(switchSegment(te, "core")).toEqual({ segment: "users", choices: ["users", "voice"] });
    expect(switchSegment(te, "edge")).toEqual({ segment: "voice", choices: ["voice", "lab"] }); // phones ×5 on voice
    expect(switchSegment(te, "bare")).toEqual({ segment: null, choices: [] });
  });
  it("of one: that one (switch-test's access-sw carries office)", () => {
    const st = readDoc(catalogDoc("switch-test")).te;
    expect(switchSegment(st, "access-sw")).toEqual({ segment: "office", choices: ["office"] });
  });
  it("placeOnDevice / placeTarget: an implied switch is its segment, a switch its rule's, a router a new segment, the pane nothing", () => {
    expect(placeOnDevice(te, "core")).toEqual({ segment: "users" });
    expect(placeOnDevice(te, "gw")).toEqual({ router: "gw" });
    expect(placeOnDevice(te, "bare")).toMatchObject({ blocked: expect.stringContaining("bare carries no segment") });
    expect(placeOnDevice(te, "pc")).toBeNull();
    const nodes = [
      { id: ID.segment("lab"), position: { x: 0, y: 0 }, width: 150, height: 66, data: { kind: "segment", name: "lab" } },
      { id: TREE_ID.device("core"), position: { x: 300, y: 0 }, width: 150, height: 66, data: { kind: "device", name: "core" } },
      { id: ID.router("gw"), position: { x: 600, y: 0 }, width: 150, height: 66, data: { kind: "router", name: "gw" } },
    ] as never;
    expect(placeTarget(nodes, { x: 75, y: 30 }, te)).toEqual({ segment: "lab" });
    expect(placeTarget(nodes, { x: 375, y: 30 }, te)).toEqual({ segment: "users" });
    expect(placeTarget(nodes, { x: 675, y: 30 }, te)).toEqual({ router: "gw" });
    expect(placeTarget(nodes, { x: 1000, y: 30 }, te)).toBeNull();
  });
  it("hostSwitch: the inspector's VLAN picker offers what the host's switch carries", () => {
    expect(hostSwitch(te, "pc")).toEqual({ switch: "core", segment: "users", choices: ["users", "voice"] });
    expect(hostSwitch(te, "gw")).toBeNull();
  });
});

describe("a node written as one flow line", () => {
  it("moves to another segment without losing itself (setNodeNets re-serialises the flow map whole)", () => {
    const d = doc("multi", MULTI);
    const files = moveTo(d, readDoc(d), "pc", "voice");
    const te = readDoc({ ...d, files }).te;
    expect(te.problems).toEqual([]);
    expect(te.nodes.map((n) => n.name)).toEqual(["gw", "core", "edge", "bare", "phones", "pc"]);
    expect(te.nodes.find((n) => n.name === "pc")).toMatchObject({ role: "workstation", image: "img@v1", nets: [{ segment: "voice", ip: null }] });
    expect(te.links.length).toBe(2);
    expect(files["te.yaml"]!.split("\n").filter((l) => l.includes("name: pc"))).toEqual(["    - { name: pc, role: workstation, image: img@v1, net: voice }"]);
  });
});

describe("a host dropped on a router: a new segment under it", () => {
  it("the next free /24 in the router's site, the router's leg on it, the host on it, drawn under a new implied switch", () => {
    const d = catalogDoc("canvas-test");
    const made = newSegmentOnRouter(d, readDoc(d), "gw");
    expect(made.segment).toBe("segment-3");
    const d2 = { ...d, files: made.files };
    const m2 = readDoc(d2);
    expect(m2.te.segments.find((s) => s.name === "segment-3")).toMatchObject({ cidr: "10.70.21.0/24", zoneType: "vlan" });
    expect(m2.te.nodes.find((n) => n.name === "gw")!.nets.map((l) => l.segment)).toEqual(["office", "servers", "segment-3"]);
    const r = placeNodes(d2, m2, { name: "kiosk", count: 1, image: "debian-12-cloud@v1", role: null, kind: "host", segment: "segment-3" });
    const d3 = { ...d, files: r.files };
    expect(validateTe(readDoc(d3).te).filter((f) => f.severity === "error")).toEqual([]);
    const p = placed(d3);
    expect(p.nodes.find((n) => n.id === ID.segment("segment-3"))?.type).toBe("impliedSwitch");
    const kiosk = r.names[0]!;
    expect(p.edges.find((e) => e.id === TREE_ID.access(kiosk, "segment-3"))).toMatchObject({ source: ID.segment("segment-3"), target: ID.host(kiosk) });
    expect(p.edges.find((e) => e.id === "leg:gw>segment-3")).toMatchObject({ source: ID.router("gw"), target: ID.segment("segment-3") });
  });
});

describe("Insert switch", () => {
  it("on canvas-test's servers: an imageless authored switch, a trunk from gw, an access link to the file server; the implied switch is gone", () => {
    const d = catalogDoc("canvas-test");
    const out = insertSwitch(d, readDoc(d), "servers");
    expect(out.name).toBe("sw-servers");
    expect(out.skipped).toEqual([]);
    const te = readDoc({ ...d, files: out.files }).te;
    expect(te.problems).toEqual([]);
    const sw = te.nodes.find((n) => n.name === "sw-servers")!;
    expect(sw).toMatchObject({ kind: "switch", image: "", source: "authored", nets: [] });
    expect(te.links.map((l) => [l.a.device, l.a.port, l.b.device, l.b.port, l.mode, l.native, l.vlans, l.source])).toEqual([
      ["gw", "eth1", "sw-servers", "P1", "trunk", "servers", ["servers"], "authored"],
      ["sw-servers", "P2", "file-server", "eth1", "access", null, ["servers"], "authored"],
    ]);
    // The file server keeps its net (and address): the access link is the same attachment spelled as a link.
    expect(te.nodes.find((n) => n.name === "file-server")!.nets).toEqual([{ segment: "servers", ip: "10.70.20.10" }]);
    expect(validateTe(te).filter((f) => f.severity === "error")).toEqual([]);
    const p = placed({ ...d, files: out.files });
    expect(p.nodes.some((n) => n.id === ID.segment("servers"))).toBe(false);
    expect(p.anchorByName["servers"]).toBe(TREE_ID.device("sw-servers"));
    expect(p.edges.find((e) => e.id === "link:1")).toMatchObject({ source: TREE_ID.device("sw-servers"), target: ID.host("file-server") });
    expect(p.edges.some((e) => e.id === TREE_ID.access("file-server", "servers"))).toBe(false); // the link IS the access edge
  });
  it("on office (a ×2 group only): the trunk, no access link -- a group cannot be named by a link; it still draws under the switch", () => {
    const d = catalogDoc("canvas-test");
    const out = insertSwitch(d, readDoc(d), "office");
    expect(out.skipped).toEqual(["corp-workstation"]);
    const te = readDoc({ ...d, files: out.files }).te;
    expect(te.links.length).toBe(1);
    expect(validateTe(te).filter((f) => f.severity === "error")).toEqual([]);
    const p = placed({ ...d, files: out.files });
    expect(p.edges.find((e) => e.id === TREE_ID.access("corp-workstation", "office"))).toMatchObject({ source: TREE_ID.device("sw-office") });
  });
  it("round-trips through the command stack byte for byte; refuses a segment a switch already carries", () => {
    const d = catalogDoc("canvas-test");
    const out = insertSwitch(d, readDoc(d), "servers");
    const stack = record(EMPTY_STACK, "insert switch on servers", d.files, out.files);
    expect(stack.done.length).toBe(1);
    const u = undo(stack, out.files)!;
    expect(u.files["te.yaml"]).toBe(d.files["te.yaml"]);
    const r = redo(u.stack, u.files)!;
    expect(r.files["te.yaml"]).toBe(out.files["te.yaml"]);
    const st = catalogDoc("switch-test");
    expect(() => insertSwitch(st, readDoc(st), "office")).toThrow(/already carried by switch access-sw/);
  });
});
