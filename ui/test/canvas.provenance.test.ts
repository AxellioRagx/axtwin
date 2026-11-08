/**
 * What the canvas creates or changes is `source: authored` (topology-layers §3.1, R4 item 2,
 * 2026-11-08): `edit/provenance.ts`, run by the editor on every apply.
 */
import { describe, expect, it } from "vitest";

import { readDoc, type WorkingDoc } from "../src/features/edit-canvas/edit/document";
import { stampAuthored } from "../src/features/edit-canvas/edit/provenance";
import { attachDevices, duplicateSubtree } from "../src/features/edit-canvas/edit/subtree";
import { addTapBinding, readTap } from "../src/features/edit-canvas/edit/tap";
import { readTe, setMemberRole, setNodeField } from "../src/features/edit-canvas/edit/te";

const TE = `twin_config:
  id: switch-test
  segments:
    - { name: office, cidr: 10.71.10.0/24, zone_type: vlan, source: discovered, confidence: 0.9 }
    - { name: servers, cidr: 10.71.20.0/24, zone_type: vlan }
  nodes:
    - name: corp-workstation
      role: corp-workstation
      image: debian-12-cloud@v1
      count: 2
      net: office
      source: discovered
      confidence: 0.8
      vantage: corp-apcn
    - name: printer
      role: printer
      image: debian-12-cloud@v1
      net: servers
      source: asserted
    - { name: access-sw, kind: switch, role: l2-switch, source: authored }
    - name: gw
      kind: router
      image: debian-12-cloud@v1
      nets:
        - { segment: office }
        - { segment: servers }
  links:
    - a: { device: gw, port: eth1 }
      b: { device: access-sw, port: SFP1 }
      mode: trunk
      native: office
      vlans: [office]
      source: authored
`;
const TAP = `tap-plan:\n  target: switch-test\n  bindings:\n    - selector: { scope: segment, target: office }\n      sensors: [x]\n      mode: exclusive\n      source: discovered\n`;

/** One editor apply: the edit, then the stamp. */
function apply(doc: WorkingDoc, edit: (d: WorkingDoc) => Record<string, string>): WorkingDoc {
  return { ...doc, files: stampAuthored(doc.files, edit(doc), doc.teFile) };
}

describe("the canvas stamps what it writes authored", () => {
  const start: WorkingDoc = { id: "switch-test", teFile: "te.yaml", files: { "te.yaml": TE, "tap/default.yaml": TAP } };

  it("after duplicate (parked) + attach, every new node, segment and link carries source: authored; the baseline is untouched", () => {
    let doc = apply(start, (d) => duplicateSubtree(d, readDoc(d), "access-sw", null).files);
    doc = apply(doc, (d) => attachDevices(d, readDoc(d), "gw", "access-sw-2"));
    const te = readTe(doc.files["te.yaml"]!);
    const base = readTe(TE);
    const fresh = (names: string[], all: string[]) => all.filter((n) => !names.includes(n));
    for (const n of fresh(base.nodes.map((x) => x.name), te.nodes.map((x) => x.name))) expect([n, te.nodes.find((x) => x.name === n)!.source]).toEqual([n, "authored"]);
    for (const s of fresh(base.segments.map((x) => x.name), te.segments.map((x) => x.name))) expect([s, te.segments.find((x) => x.name === s)!.source]).toEqual([s, "authored"]);
    expect(te.nodes.map((n) => n.name)).toContain("corp-workstation-2");
    expect(te.links.length).toBe(2);
    const attach = te.links.at(-1)!;
    expect([attach.a.device, attach.b.device, attach.source]).toEqual(["gw", "access-sw-2", "authored"]);
    // The originals keep their marks, byte for byte.
    const out = doc.files["te.yaml"]!.split("\n");
    let at = 0;
    for (const line of TE.split("\n")) {
      const found = out.indexOf(line, at);
      expect([line, found >= 0]).toEqual([line, true]);
      at = found + 1;
    }
    expect(doc.files["tap/default.yaml"]).toBe(TAP);
    // A copy of a discovered node carries no cast-only confidence or vantage.
    expect(doc.files["te.yaml"]!.split("corp-workstation-2")[1]).not.toMatch(/confidence|vantage/);
  });

  it("an edit to a discovered item makes it authored and drops confidence and vantage; an asserted one keeps its mark", () => {
    let doc = apply(start, (d) => ({ ...d.files, "te.yaml": setNodeField(d.files["te.yaml"]!, "corp-workstation", "role", "kiosk") }));
    doc = apply(doc, (d) => ({ ...d.files, "te.yaml": setNodeField(d.files["te.yaml"]!, "printer", "role", "label-printer") }));
    doc = apply(doc, (d) => ({ ...d.files, "te.yaml": setMemberRole(d.files["te.yaml"]!, "corp-workstation", 2, "jump-host") }));
    const te = readTe(doc.files["te.yaml"]!);
    const ws = te.nodes.find((n) => n.name === "corp-workstation")!;
    expect([ws.role, ws.source, ws.confidence]).toEqual(["kiosk", "authored", null]);
    expect(doc.files["te.yaml"]).not.toMatch(/vantage/);
    expect(ws.members).toEqual([{ index: 2, role: "jump-host", source: "authored" }]);
    expect(te.nodes.find((n) => n.name === "printer")!.source).toBe("asserted");
    // Untouched items keep what they had.
    expect(te.segments.find((s) => s.name === "office")).toMatchObject({ source: "discovered", confidence: 0.9 });
  });

  it("a new tap binding is authored; an untouched one keeps its mark", () => {
    const doc = apply(start, (d) => ({ ...d.files, "tap/default.yaml": addTapBinding(d.files["tap/default.yaml"]!, { scope: "segment", target: "servers", sensors: ["x"] }) }));
    expect(readTap(doc.files["tap/default.yaml"]!).bindings.map((b) => [b.targets[0], b.source])).toEqual([["office", "discovered"], ["servers", "authored"]]);
  });
});
