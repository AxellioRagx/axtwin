/**
 * The 2026-11-06 round-2 findings: the command stack is restored by its
 * content, for a deployed and an undeployed twin alike; a site's box grows
 * around a segment dragged out of it; a leg runs border to border towards
 * its target from whichever side; the legs take no pointer events and the
 * pane finds the one under the pointer.
 */
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { stackFits } from "../src/features/edit-canvas/CanvasPage";
import { clipLine, distanceToSegment, legPath, nearestLeg, parseLegPath } from "../src/features/edit-canvas/canvas/legs";
import { LEG_CLASS } from "../src/features/edit-canvas/canvas/placement";
import { placeTree } from "../src/features/edit-canvas/canvas/tree";
import { documentGraph, readDoc } from "../src/features/edit-canvas/edit/document";
import { buildGraph } from "../src/features/edit-canvas/model/graph";
import { enabled, host, panel, seeded } from "./helpers/canvasFixtures";
import { renderAt } from "./helpers/render";

const TE = `twin_config:
  id: three
  segments:
    - { name: office, cidr: 10.70.10.0/24 }
    - { name: servers, cidr: 10.70.20.0/24 }
  nodes:
    - name: gw
      role: gw
      kind: router
      image: debian-12-cloud@v1
      nets:
        - { segment: office }
        - { segment: servers }
    - name: ws
      image: debian-12-cloud@v1
      net: office
`;

function graphOf(te: string) {
  const g = documentGraph(readDoc({ id: "three", teFile: "te.yaml", files: { "te.yaml": te } }), "three");
  return buildGraph(g.exportData, g.cast);
}

afterEach(() => {
  localStorage.clear();
  sessionStorage.clear();
});

describe("the command stack is restored by its content", () => {
  it("stackFits: the last command's after-text, or the next redo's before-text, must be what is loaded", () => {
    const cmd = { label: "x", before: { "te.yaml": "a" }, after: { "te.yaml": "b" } };
    expect(stackFits({ done: [cmd], undone: [] }, { "te.yaml": "b", "twin.yaml": "t" })).toBe(true);
    expect(stackFits({ done: [cmd], undone: [] }, { "te.yaml": "a" })).toBe(false);
    expect(stackFits({ done: [], undone: [cmd] }, { "te.yaml": "a" })).toBe(true);
    expect(stackFits({ done: [], undone: [cmd] }, { "te.yaml": "b" })).toBe(false);
    expect(stackFits({ done: [], undone: [] }, { "te.yaml": "a" })).toBe(false);
  });

  it("an undeployed twin: edit, Save, reload -- Undo is back and Redo stays empty; a stack that no longer fits is dropped", async () => {
    const { api } = seeded();
    const first = renderAt(api, "/twins/corp-auth/canvas");
    await screen.findAllByTestId("canvas-host");
    fireEvent.click(host("ws01"));
    fireEvent.change(within(await panel().findByTestId("node-panel")).getByLabelText("Persona"), { target: { value: "generic-endpoint" } });
    await waitFor(() => expect(screen.getByRole("button", { name: "Undo" }).getAttribute("title")).toBe("undo: persona of ws01"));
    fireEvent.click(await enabled("Save"));
    await waitFor(() => expect(screen.getByRole("button", { name: "Save" }).hasAttribute("disabled")).toBe(true));
    expect(localStorage.getItem("axr-canvas-undo:twin:corp-auth")).toContain("persona of ws01");
    first.unmount();
    renderAt(api, "/twins/corp-auth/canvas");
    await screen.findAllByTestId("canvas-host");
    await waitFor(() => expect(screen.getByRole("button", { name: "Undo" }).getAttribute("title")).toBe("undo: persona of ws01"));
    expect(screen.getByRole("button", { name: "Redo" }).hasAttribute("disabled")).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    expect(screen.getByRole("button", { name: "Undo" }).hasAttribute("disabled")).toBe(true);
    expect(screen.getByRole("button", { name: "Redo" }).hasAttribute("disabled")).toBe(false);
    // The catalog moves under this browser: the stored stack no longer fits the text loaded.
    localStorage.setItem("axr-canvas-undo:twin:corp-auth", JSON.stringify({ stack: { done: [{ label: "elsewhere", before: { "te.yaml": "x" }, after: { "te.yaml": "y" } }], undone: [] } }));
    screen.getByTestId("canvas").remove();
    renderAt(api, "/twins/corp-auth/canvas");
    await screen.findAllByTestId("canvas-host");
    await waitFor(() => expect(screen.getAllByRole("button", { name: "Undo" }).at(-1)!.hasAttribute("disabled")).toBe(true));
    expect(screen.getAllByRole("button", { name: "Redo" }).at(-1)!.hasAttribute("disabled")).toBe(true);
  });
});

describe("a leg runs border to border and takes no pointer events", () => {
  it("clipLine: below, above, beside, and overlapping", () => {
    const router = { x: 100, y: 0, w: 100, h: 40 };
    const below = clipLine(router, { x: 100, y: 200, w: 100, h: 100 })!;
    expect([below.from.x, below.from.y, below.to.x, below.to.y]).toEqual([150, 40, 150, 200]);
    const above = clipLine(router, { x: 100, y: -300, w: 100, h: 100 })!;
    expect([above.from.x, above.from.y, above.to.x, above.to.y]).toEqual([150, 0, 150, -200]);
    const beside = clipLine(router, { x: 400, y: 0, w: 100, h: 40 })!;
    expect([beside.from.x, beside.from.y, beside.to.x, beside.to.y]).toEqual([200, 20, 400, 20]);
    // Overlapping boxes: centre to centre, so the attachment is still drawn.
    const over = clipLine(router, { x: 120, y: 10, w: 100, h: 60 })!;
    expect([over.from.x, over.from.y, over.to.x, over.to.y]).toEqual([150, 20, 170, 40]);
    expect(clipLine(router, router)).toBeNull();
    expect(below.mid).toEqual({ x: 150, y: 120 });
  });

  it("the path round-trips and the nearest leg is found within the tolerance", () => {
    const line = { from: { x: 1.5, y: -2 }, to: { x: 300, y: 40.25 } };
    expect(parseLegPath(legPath(line))).toEqual(line);
    expect(parseLegPath("M 0 0 C 1 1, 2 2, 3 3")).toBeNull();
    expect(distanceToSegment({ x: 5, y: 10 }, { x: 0, y: 0 }, { x: 10, y: 0 })).toBe(10);
    expect(distanceToSegment({ x: -3, y: 4 }, { x: 0, y: 0 }, { x: 10, y: 0 })).toBe(5);
    const legs = [
      { id: "leg:gw>office", from: { x: 0, y: 0 }, to: { x: 0, y: 100 } },
      { id: "leg:gw>servers", from: { x: 50, y: 0 }, to: { x: 50, y: 100 } },
    ];
    expect(nearestLeg({ x: 46, y: 50 }, legs, 8)).toBe("leg:gw>servers");
    expect(nearestLeg({ x: 25, y: 50 }, legs, 8)).toBeNull();
    expect(nearestLeg({ x: 3, y: 50 }, legs, 8)).toBe("leg:gw>office");
  });

  it("every leg edge carries the class that takes its pointer events away", () => {
    const p = placeTree(graphOf(TE));
    const legs = p.edges.filter((e) => e.type === "leg");
    expect(legs.length).toBe(2);
    expect(legs.every((e) => e.className === LEG_CLASS)).toBe(true);
  });
});
