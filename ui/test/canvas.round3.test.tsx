/**
 * Review round 3, track A (2026-11-07, `docs/design/canvas-review-2026-11-05.md`):
 * provenance defaults by origin (R3-1), the Checks word on a pass with warnings
 * (R3-3), the switch's own node type (R3-7), the blocked banner clearing on Undo
 * and the unplaced count in Notes rather than on the drawing (R3-9).
 */
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { placeTree } from "../src/features/edit-canvas/canvas/tree";
import { documentGraph } from "../src/features/edit-canvas/edit/document";
import { readDoc } from "../src/features/edit-canvas/edit/document";
import { buildGraph } from "../src/features/edit-canvas/model/graph";
import { provenanceCounts, validationState } from "../src/features/edit-canvas/SidePanel";
import { item, openPalette, seeded, segments } from "./helpers/canvasFixtures";
import { renderAt } from "./helpers/render";

afterEach(() => {
  localStorage.clear();
});

const TE = `twin_config:
  id: r3
  segments:
    - { name: office, cidr: 10.71.10.0/24, source: discovered, confidence: 0.9 }
    - { name: servers, cidr: 10.71.20.0/24 }
  nodes:
    - name: ws
      role: workstation
      image: ubuntu-desktop-22@v1
      net: office
    - name: access-sw
      kind: switch
      role: l2-switch
      source: asserted
    - name: gw
      kind: router
      role: gw
      image: vyos@v1
      nets: [{ segment: office }, { segment: servers }]
  links:
    - { a: { device: gw, port: eth0 }, b: { device: access-sw, port: P1 }, mode: trunk, vlans: [office] }
`;

function model() {
  return readDoc({ id: "r3", teFile: "te.yaml", files: { "te.yaml": TE } });
}

function placed(defaultSource?: "discovered" | "authored") {
  const g = documentGraph(model(), "r3");
  return placeTree(buildGraph(g.exportData, g.cast), {}, { extraLegs: g.extraLegs, uplinks: g.uplinks, tree: g.tree, ...(defaultSource ? { defaultSource } : {}) });
}

describe("R3-1: a missing source reads by the twin's origin", () => {
  it("discovered on a cast twin, authored on a hand-authored one; an explicit source always wins", () => {
    const srcOf = (p: ReturnType<typeof placed>, id: string) => (p.nodes.find((n) => n.id === id)!.data as { source: string | null }).source;
    const cast = placed("discovered");
    expect(srcOf(cast, "seg:servers")).toBe("discovered");
    expect(srcOf(cast, "router:gw")).toBe("discovered");
    expect(srcOf(cast, "device:access-sw")).toBe("asserted");
    const authored = placed("authored");
    expect(srcOf(authored, "seg:servers")).toBe("authored");
    expect(srcOf(authored, "seg:office")).toBe("discovered");
  });
  it("the Checks summary counts by the same rule", () => {
    const m = model();
    expect(provenanceCounts(m, "discovered")).toMatchObject({ discovered: 5, asserted: 1, authored: 0 });
    expect(provenanceCounts(m, "authored")).toMatchObject({ discovered: 1, asserted: 1, authored: 4 });
  });
});

describe("R3-3: warnings are not invalid", () => {
  it("a pass with warnings and no canvas error reads valid", () => {
    const validation = { files: {}, error: null, result: { disposition: "PASS_WITH_WARNINGS", exit_code: 0, passes_run: [1], errors: 0, warnings: 1, findings: [{ rule_id: "OVR-020", severity: "warning", message: "unused", location: null }] } };
    expect(validationState({ findings: [{ rule_id: "CV-099", severity: "warning", message: "w", location: null }], serverReason: null, validation, validating: false } as Parameters<typeof validationState>[0])).toBe("valid");
    // A canvas error is still an error, and the Checks tab says whose it is.
    expect(validationState({ findings: [{ rule_id: "CV-012", severity: "error", message: "not IPv4", location: null }], serverReason: null, validation, validating: false } as Parameters<typeof validationState>[0])).toBe("invalid");
  });
});

describe("R3-7 / R3-9 on the placement", () => {
  it("a switch is a `switch` node; no unplaced box is drawn, its count rides the placement for Notes", () => {
    const p = placed();
    expect(p.nodes.find((n) => n.id === "device:access-sw")!.type).toBe("switch");
    expect(p.nodes.some((n) => n.type === "unplaced")).toBe(false);
    expect(typeof p.unplaced).toBe("number");
  });
});

describe("R3-9: the blocked banner clears on the next change", () => {
  it("Undo after a blocked gesture clears the banner", async () => {
    const { api } = seeded();
    renderAt(api, "/twins/corp-auth/canvas");
    await screen.findAllByTestId("canvas-host");
    fireEvent.click(item("segment", "segment"));
    await waitFor(() => expect(segments()).toContain("segment-3"));
    openPalette();
    fireEvent.click(screen.getByRole("button", { name: /Personas/ }));
    fireEvent.click(item("persona", "generic-endpoint"));
    expect((await screen.findByTestId("blocked-reason")).textContent).toContain("select a node group first");
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    await waitFor(() => expect(segments()).not.toContain("segment-3"));
    expect(screen.queryByTestId("blocked-reason")).toBeNull();
  });
});
