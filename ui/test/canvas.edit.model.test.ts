/**
 * The canvas's edit model (`features/edit-canvas/edit/`): the YAML reader
 * round-trips the real `corp-enterprise` bundle, edits are line splices
 * that leave every other line byte-for-byte, undo restores the pre-edit
 * text byte-equivalent, N copies are named `{name}-{n}`, and a placed node
 * gets a segment-appropriate address.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import { allocateHostAddresses, nextFreeSlash24, takenAddresses } from "../src/features/edit-canvas/edit/allocate";
import { EMPTY_STACK, record, redo, undo } from "../src/features/edit-canvas/edit/commands";
import { attach, detach, documentGraph, duplicateOnto, mergeSegments, placeNodes, readDoc, removeSegment, saveAsFiles, setPersona, type WorkingDoc } from "../src/features/edit-canvas/edit/document";
import { readManifest } from "../src/features/edit-canvas/edit/manifest";
import { addSegment, copyNames, readTe, setNodeField, setNodeNets, setSegmentField } from "../src/features/edit-canvas/edit/te";
import { readTfc, setAssignment } from "../src/features/edit-canvas/edit/tfc";
import { validateTe } from "../src/features/edit-canvas/edit/validate";
import { joinLines, parseYaml, toPlain } from "../src/features/edit-canvas/edit/yaml";

const BUNDLE = resolve(__dirname, "../../catalog/twins/corp-enterprise");
const TE = readFileSync(resolve(BUNDLE, "te.yaml"), "utf8");
const TWIN = readFileSync(resolve(BUNDLE, "twin.yaml"), "utf8");
const TFC = readFileSync(resolve(BUNDLE, "tfc/default.yaml"), "utf8");

function doc(files: Partial<Record<string, string>> = {}): WorkingDoc {
  return { id: "corp-enterprise", teFile: "te.yaml", files: { "te.yaml": TE, "twin.yaml": TWIN, "tfc/default.yaml": TFC, ...files } as Record<string, string> };
}

/** The lines `after` has that `before` has not (as a multiset), and vice versa. */
function diff(before: string, after: string): { added: string[]; removed: string[] } {
  const count = (xs: string[]) => xs.reduce((m, l) => m.set(l, (m.get(l) ?? 0) + 1), new Map<string, number>());
  const a = count(before.split("\n"));
  const b = count(after.split("\n"));
  const only = (x: Map<string, number>, y: Map<string, number>) => [...x.entries()].flatMap(([l, n]) => Array<string>(Math.max(0, n - (y.get(l) ?? 0))).fill(l));
  return { added: only(b, a).sort(), removed: only(a, b).sort() };
}

describe("the YAML reader", () => {
  it("reads the real corp-enterprise bundle and keeps its text", () => {
    const d = parseYaml(TE);
    expect(joinLines(d.lines, d.trailingNewline)).toBe(TE);
    const te = readTe(TE);
    expect(te.id).toBe("corp-enterprise");
    expect(te.segments.map((s) => s.name)).toEqual(["corp-lan", "server-vlan", "dmz", "mgmt"]);
    expect(te.nodes.map((n) => n.name)).toEqual(["gw", "domain-controller", "file-server", "mail-server", "egress-proxy", "dmz-web", "monitoring-collector", "corp-workstation", "attacker"]);
    const gw = te.nodes[0]!;
    expect(gw.kind).toBe("router");
    expect(gw.nets.map((l) => l.segment)).toEqual(["corp-lan", "server-vlan", "dmz", "mgmt"]);
    const ws = te.nodes.find((n) => n.name === "corp-workstation")!;
    expect(ws.count).toBe("${N_WORKSTATIONS:25}");
    expect(ws.countValue).toBe(25);
    expect(ws.nets).toEqual([{ segment: "corp-lan", ip: null }]);
    expect(te.params).toEqual({ N_WORKSTATIONS: 25, DOMAIN: "corp.axr.lab" });
    expect(te.problems).toEqual([]);
    expect(validateTe(te)).toEqual([]);
    expect(readManifest(TWIN)).toEqual({ id: "corp-enterprise", te: "te.yaml", tfc: "tfc/default.yaml", tap: "tap/default.yaml" });
    expect(readTfc(TFC).assignments.find((a) => a.target === "corp-workstation")).toEqual({ by: "role", target: "corp-workstation", persona: "corp-workstation", intensity: null, schedule: null, seed: null });
    // Multi-line flow mappings, block scalars and anchors are read, not dropped.
    const plain = toPlain(parseYaml(TE).root) as { twin_config: { identity: { users: unknown[] } } };
    expect(plain.twin_config.identity.users.length).toBe(4);
    expect(toPlain(parseYaml("a: &x\n  b: 1\nc: *x\n").root)).toEqual({ a: { b: 1 }, c: "*x" });
    expect(toPlain(parseYaml('d: "one\\\n  \\ two"\ne: 3\n').root)).toEqual({ d: "one two", e: 3 });
  });
});

describe("edits are line splices", () => {
  it("adds a segment on one new line and nothing else moves", () => {
    const out = addSegment(TE, { name: "honeynet", cidr: "10.62.40.0/24", zoneType: "vlan" });
    const d = diff(TE, out);
    expect(d.removed).toEqual([]);
    expect(d.added).toEqual(["    - { name: honeynet, cidr: 10.62.40.0/24, zone_type: vlan }"]);
    expect(out.split("\n").length).toBe(TE.split("\n").length + 1);
    expect(readTe(out).segments.map((s) => s.name)).toEqual(["corp-lan", "server-vlan", "dmz", "mgmt", "honeynet"]);
    // The new line sits right after the last segment, before the blank line and `nodes:`.
    const lines = out.split("\n");
    const at = lines.indexOf("    - { name: honeynet, cidr: 10.62.40.0/24, zone_type: vlan }");
    expect(lines[at - 1]).toContain("mgmt");
    expect(lines[at + 1]).toBe("");
  });

  it("changes one scalar, one flow pair, one attachment; the rest is byte-identical", () => {
    const role = setNodeField(TE, "file-server", "role", "backup-server");
    expect(diff(TE, role)).toEqual({ removed: ["      role: file-server"], added: ["      role: backup-server"] });
    const cidr = setSegmentField(TE, "dmz", "cidr", "10.62.21.0/24");
    expect(diff(TE, cidr).removed).toEqual(["    - { name: dmz,         cidr: 10.62.20.0/24, zone_type: vlan }   # internet-facing services"]);
    expect(diff(TE, cidr).added).toEqual(["    - { name: dmz, cidr: 10.62.21.0/24, zone_type: vlan }   # internet-facing services"]);
    const nets = setNodeNets(TE, "mail-server", [{ segment: "dmz", ip: "10.62.20.10" }, { segment: "mgmt", ip: null }]);
    expect(diff(TE, nets)).toEqual({ removed: ["      net: dmz"], added: ["      nets:", "        - { segment: dmz, ip: 10.62.20.10 }", "        - { segment: mgmt }"].sort() });
    expect(readTe(nets).nodes.find((n) => n.name === "mail-server")!.nets).toEqual([{ segment: "dmz", ip: "10.62.20.10" }, { segment: "mgmt", ip: null }]);
    // Back to a single unpinned leg: `net:` again.
    const back = setNodeNets(nets, "mail-server", [{ segment: "dmz", ip: null }]);
    expect(back).toBe(TE);
    // A count on a node that had none lands before its attachment.
    const counted = setNodeField(TE, "dmz-web", "count", 3);
    const lines = counted.split("\n");
    const at = lines.indexOf("      count: 3");
    expect(lines[at + 1]).toBe("      net: dmz");
    expect(setNodeField(counted, "dmz-web", "count", null)).toBe(TE);
  });

  it("binds a persona in the traffic profile, by node or by role", () => {
    const out = setAssignment(TFC, "node", "attacker", { persona: "generic-endpoint", intensity: 0.5, schedule: "24x7" });
    expect(diff(TFC, out)).toEqual({ removed: [], added: ["    - node: attacker", "      persona: generic-endpoint", "      intensity: 0.5", "      schedule: 24x7"].sort() });
    const m = readTfc(out);
    expect(m.assignments.at(-1)).toEqual({ by: "node", target: "attacker", persona: "generic-endpoint", intensity: 0.5, schedule: "24x7", seed: null });
    const changed = setAssignment(out, "node", "attacker", { intensity: null, persona: "corp-workstation" });
    expect(readTfc(changed).assignments.at(-1)).toMatchObject({ persona: "corp-workstation", intensity: null, schedule: "24x7" });
    const role = setAssignment(TFC, "role", "dmz-web", { intensity: 2 });
    expect(diff(TFC, role)).toEqual({ removed: [], added: ["      intensity: 2"] });
  });
});

describe("the command stack", () => {
  it("undo returns the document byte-equivalent to the pre-edit text; redo re-applies", () => {
    const d = doc();
    const m = readDoc(d);
    const placed = placeNodes(d, m, { name: "honeypot", count: 2, image: "debian-12-cloud@v1", role: "honeypot", segment: "dmz" });
    let stack = record(EMPTY_STACK, "place honeypot ×2", d.files, placed.files);
    expect(stack.done.map((c) => c.label)).toEqual(["place honeypot ×2"]);
    expect(Object.keys(stack.done[0]!.after)).toEqual(["te.yaml"]);
    const persona = setPersona({ ...d, files: placed.files }, readDoc({ ...d, files: placed.files }), "node", "honeypot-1", { persona: "dmz-web" });
    stack = record(stack, "persona honeypot-1", placed.files, persona);
    expect(persona["te.yaml"]).toBe(placed.files["te.yaml"]);
    expect(persona["tfc/default.yaml"]).not.toBe(TFC);

    const u1 = undo(stack, persona)!;
    expect(u1.files["tfc/default.yaml"]).toBe(TFC);
    expect(u1.files["te.yaml"]).toBe(placed.files["te.yaml"]);
    const u2 = undo(u1.stack, u1.files)!;
    expect(u2.files["te.yaml"]).toBe(TE);
    expect(u2.files).toEqual(d.files);
    expect(undo(u2.stack, u2.files)).toBeNull();
    const r1 = redo(u2.stack, u2.files)!;
    expect(r1.files["te.yaml"]).toBe(placed.files["te.yaml"]);
    expect(r1.command.label).toBe("place honeypot ×2");
    // A no-op edit records nothing.
    expect(record(stack, "nothing", persona, { ...persona })).toBe(stack);
  });
});

describe("placement and addressing (C3.2)", () => {
  it("names N copies {name}-{n} and pins each to the segment's next free address", () => {
    const d = doc();
    const m = readDoc(d);
    const r = placeNodes(d, m, { name: "honeypot", count: 3, image: "debian-12-cloud@v1", role: "honeypot", segment: "dmz" });
    expect(r.names).toEqual(["honeypot-1", "honeypot-2", "honeypot-3"]);
    const te = readTe(r.files["te.yaml"]!);
    expect(te.nodes.slice(-3).map((n) => n.nets[0])).toEqual([
      { segment: "dmz", ip: "10.62.20.10" },
      { segment: "dmz", ip: "10.62.20.11" },
      { segment: "dmz", ip: "10.62.20.12" },
    ]);
    expect(validateTe(te)).toEqual([]);
    // One copy keeps the bare name when it is free; a taken name gets -1.
    expect(copyNames("probe", 1, new Set())).toEqual(["probe"]);
    expect(copyNames("attacker", 1, new Set(["attacker"]))).toEqual(["attacker-1"]);
    expect(copyNames("x", 2, new Set(["x-1"]))).toEqual(["x-2", "x-3"]);
    // The next placement skips what the first pinned, and the gateway's .1.
    const taken = takenAddresses(te, "dmz");
    expect([...taken].sort()).toEqual(["10.62.20.1", "10.62.20.10", "10.62.20.11", "10.62.20.12"]);
    expect(allocateHostAddresses("10.62.20.0/24", 1, taken)).toEqual({ ok: true, addresses: ["10.62.20.13"] });
    expect(allocateHostAddresses("10.62.20.0/30", 1, new Set())).toMatchObject({ ok: false });
    expect(nextFreeSlash24("10.62", te.segments.map((s) => s.cidr))).toBe("10.62.1.0/24");
  });

  it("duplicates a section onto a target segment, re-addressed there and still valid", () => {
    const d = doc();
    const m = readDoc(d);
    const r = duplicateOnto(d, m, ["dmz-web", "mail-server"], "mgmt");
    expect(r.names).toEqual(["dmz-web-1", "mail-server-1"]);
    const te = readTe(r.files["te.yaml"]!);
    expect(te.nodes.find((n) => n.name === "dmz-web-1")).toMatchObject({ role: "dmz-web", image: "ubuntu-server-22@v5", nets: [{ segment: "mgmt", ip: "10.62.30.10" }], requires: ["linux"] });
    expect(te.nodes.find((n) => n.name === "mail-server-1")!.nets).toEqual([{ segment: "mgmt", ip: "10.62.30.11" }]);
    expect(validateTe(te)).toEqual([]);
    // A fanned-out group keeps its count and is not pinned (the planner numbers its instances).
    const ws = duplicateOnto(d, m, ["corp-workstation"], "mgmt");
    expect(readTe(ws.files["te.yaml"]!).nodes.at(-1)).toMatchObject({ name: "corp-workstation-1", count: "${N_WORKSTATIONS:25}", nets: [{ segment: "mgmt", ip: null }] });
  });

  it("attaches a second leg, detaches, merges and removes segments, keeps the twin valid", () => {
    const d = doc();
    const m = readDoc(d);
    const legged = attach(d, m, "file-server", "mgmt");
    expect(readTe(legged["te.yaml"]!).nodes.find((n) => n.name === "file-server")!.nets).toEqual([{ segment: "server-vlan", ip: null }, { segment: "mgmt", ip: "10.62.30.10" }]);
    expect(() => attach(d, m, "file-server", "server-vlan")).toThrow(/already on server-vlan/);
    expect(() => attach(d, m, "file-server", "nope")).toThrow(/no segment named 'nope'/);
    const d2 = { ...d, files: legged };
    const off = detach(d2, readDoc(d2), "file-server", "mgmt");
    expect(off["te.yaml"]).toBe(TE);
    const merged = mergeSegments(d, m, "mgmt", "server-vlan");
    const te = readTe(merged["te.yaml"]!);
    expect(te.segments.map((s) => s.name)).toEqual(["corp-lan", "server-vlan", "dmz"]);
    expect(te.nodes.find((n) => n.name === "monitoring-collector")!.nets).toEqual([{ segment: "server-vlan", ip: null }]);
    expect(te.nodes.find((n) => n.name === "gw")!.nets.map((l) => l.segment)).toEqual(["corp-lan", "server-vlan", "dmz"]);
    expect(validateTe(te)).toEqual([]);
    const gone = removeSegment(d, m, "dmz");
    const te2 = readTe(gone["te.yaml"]!);
    expect(te2.segments.map((s) => s.name)).toEqual(["corp-lan", "server-vlan", "mgmt"]);
    expect(te2.nodes.find((n) => n.name === "dmz-web")!.nets).toEqual([]);
    expect(validateTe(te2)).toEqual([]);
  });

  it("the structural validator names what the loader would refuse", () => {
    const bad = readTe(addSegment(TE, { name: "x", cidr: "10.62.40.5/24" }));
    expect(validateTe(bad).map((f) => f.rule_id)).toEqual(["CV-012"]);
    const orphan = readTe(setNodeNets(TE, "dmz-web", [{ segment: "nowhere", ip: null }]));
    expect(validateTe(orphan)[0]!.message).toContain("attaches to 'nowhere', which is not a segment");
    const outside = readTe(setNodeNets(TE, "dmz-web", [{ segment: "dmz", ip: "10.9.9.9" }]));
    expect(validateTe(outside)[0]!.message).toContain("not a host address inside dmz");
  });

  it("draws the document as a graph: every segment, node and leg", () => {
    const g = documentGraph(readDoc(doc()), "corp-enterprise");
    expect(g.exportData.segments.map((s) => s.name)).toEqual(["corp-lan", "server-vlan", "dmz", "mgmt"]);
    expect(g.exportData.nodes.map((n) => n.id)).toEqual(["domain-controller", "file-server", "mail-server", "egress-proxy", "dmz-web", "monitoring-collector", "corp-workstation", "attacker"]);
    expect(g.cast.routers).toEqual([{ name: "gw", segments: ["corp-lan", "server-vlan", "dmz", "mgmt"] }]);
    expect(g.exportData.nodes.find((n) => n.id === "attacker")!.role).toBe("attacker");
  });

  it("Save-As rewrites the id in the twin config and the manifest, nothing else", () => {
    const files = saveAsFiles(doc(), "corp-enterprise-2");
    expect(diff(TE, files["te.yaml"]!)).toEqual({ removed: ["  id: corp-enterprise"], added: ["  id: corp-enterprise-2"] });
    expect(diff(TWIN, files["twin.yaml"]!)).toEqual({ removed: ["  id: corp-enterprise"], added: ["  id: corp-enterprise-2"] });
    expect(files["tfc/default.yaml"]).toBe(TFC);
  });

  it("creates and wires a traffic profile when the bundle has none", () => {
    const d: WorkingDoc = { id: "bare", teFile: "te.yaml", files: { "te.yaml": TE.replace("id: corp-enterprise", "id: bare"), "twin.yaml": "twin:\n  id: bare\n  tiers:\n    te:\n      default: te.yaml\n" } };
    const files = setPersona(d, readDoc(d), "node", "attacker", { persona: "generic-endpoint" });
    expect(files["twin.yaml"]).toBe("twin:\n  id: bare\n  tiers:\n    te:\n      default: te.yaml\n    tfc:\n      default: tfc/default.yaml\n");
    expect(readTfc(files["tfc/default.yaml"]!)).toEqual({ target: "bare", assignments: [{ by: "node", target: "attacker", persona: "generic-endpoint", intensity: null, schedule: null, seed: null }] });
  });
});
