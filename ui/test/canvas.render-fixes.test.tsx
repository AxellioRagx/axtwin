/**
 * Small rendering fixes (part C): a transit's pill at every Labels setting (C1); under the
 * Provenance overlay a transit is never dashed, an unconfirmed one wears a "?" on its pill (C2);
 * host labels under one switch are cut to their column and shown whole on hover (C3).
 */
import { render } from "@testing-library/react";
import { Position, ReactFlowProvider } from "@xyflow/react";
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import { LabelsContext, LinkEdge, OverlayContext, type LabelsMode } from "../src/features/edit-canvas/nodes";

if (!(SVGElement.prototype as unknown as { getBBox?: unknown }).getBBox) (SVGElement.prototype as unknown as { getBBox: () => DOMRect }).getBBox = () => ({ x: 0, y: 0, width: 40, height: 12 }) as DOMRect;

function seglink(labels: LabelsMode, overlay: boolean, source: string | null) {
  const data = { kind: "seglink" as const, segment: "r1--r2", cidr: "10.254.0.0/30", hue: "#888", source };
  const props = { id: "seglink:r1--r2", source: "router:r1", target: "router:r2", sourceX: 0, sourceY: 0, targetX: 200, targetY: 100, sourcePosition: Position.Bottom, targetPosition: Position.Top, data, selected: false } as unknown as Parameters<typeof LinkEdge>[0];
  return render(
    <ReactFlowProvider>
      <LabelsContext.Provider value={labels}>
        <OverlayContext.Provider value={overlay}>
          <svg>
            <LinkEdge {...props} />
          </svg>
        </OverlayContext.Provider>
      </LabelsContext.Provider>
    </ReactFlowProvider>,
  );
}

const css = readFileSync("src/features/edit-canvas/canvas.css", "utf8");

describe("part C", () => {
  it("C1: a transit's pill (name · CIDR) shows at every Labels setting, none included", () => {
    for (const mode of ["all", "selected", "none"] as const) {
      const r = seglink(mode, false, "authored");
      expect(r.getByTestId("canvas-seglink").textContent).toContain("r1--r2 · 10.254.0.0/30");
      r.unmount();
    }
  });

  it("C2: under the overlay an unconfirmed transit wears '?' on its pill; the overlay's dash rule skips seglinks", () => {
    const r = seglink("none", true, "unconfirmed");
    expect(r.getByTestId("canvas-seglink").textContent).toContain("? r1--r2 · 10.254.0.0/30");
    expect(r.getByTestId("canvas-seglink").classList.contains("cv-ov-edge-unconfirmed")).toBe(true);
    r.unmount();
    expect(seglink("none", false, "unconfirmed").getByTestId("canvas-seglink").textContent).not.toContain("?");
    expect(css).toContain(".canvas-shell.overlay g.cv-ov-edge-unconfirmed:not(.cv-seglink) .react-flow__edge-path.cv-link { stroke-dasharray: 6 4; }");
    expect(css).not.toMatch(/g\.cv-ov-edge-unconfirmed \.react-flow__edge-path\.cv-link \{ stroke-dasharray/);
  });

  it("C3: a host label is cut to its column (icon + gap) with an ellipsis, and whole on hover or selection", () => {
    const rule = /\.cv-host \.cv-host-label \{([^}]*)\}/.exec(css)![1]!;
    expect(rule).toContain("max-width: calc(100% + 14px)");
    expect(rule).toContain("white-space: nowrap");
    expect(rule).toContain("text-overflow: ellipsis");
    expect(css).toContain(".cv-host:hover .cv-host-label, .cv-host.selected .cv-host-label { max-width: none;");
  });
});
