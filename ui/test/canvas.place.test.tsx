/**
 * Placement after the 2026-11-05 canvas review: a box without a pin never
 * lands under a pinned one (it takes the nearest free spot and the site grows
 * to hold it), a site's or segment's computed position settles for the session
 * so a mutation never shuffles its neighbours, the view fits once and not on
 * an edit, and a leg is drawn above the boxes it crosses.
 */
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { EDGE_Z, freeSlot, place } from "../src/features/edit-canvas/canvas/placement";
import { documentGraph, readDoc } from "../src/features/edit-canvas/edit/document";
import { buildGraph } from "../src/features/edit-canvas/model/graph";
import { clickPane, item, seeded, segment, segments } from "./helpers/canvasFixtures";
import { renderAt } from "./helpers/render";

const TE = `twin_config:
  id: three
  segments:
    - { name: office, cidr: 10.70.10.0/24 }
    - { name: servers, cidr: 10.70.20.0/24 }
    - { name: segment-3, cidr: 10.70.0.0/24 }
  nodes:
    - name: ws
      image: debian-12-cloud@v1
      net: office
`;

function graphOf(te: string) {
  const g = documentGraph(readDoc({ id: "three", teFile: "te.yaml", files: { "te.yaml": te } }), "three");
  return buildGraph(g.exportData, g.cast);
}

/** Every placed element's absolute position, by its pin key. */
function positions(): Record<string, [number, number]> {
  const out: Record<string, [number, number]> = {};
  for (const el of document.querySelectorAll<HTMLElement>("[data-key][data-x]")) out[el.dataset["key"]!] = [Number(el.dataset["x"]), Number(el.dataset["y"])];
  return out;
}

afterEach(() => {
  localStorage.clear();
  sessionStorage.clear();
});

describe("a pinned box is fixed; the others go into free space", () => {
  it("freeSlot moves a box right of what it overlaps, and onto a new row when the row is full", () => {
    const origin = { x: 0, y: 0 };
    const taken = [{ x: 12, y: 38, w: 200, h: 100 }];
    expect(freeSlot({ x: 12, y: 38 }, { w: 200, h: 100 }, taken, origin, 600)).toEqual({ x: 228, y: 38 });
    expect(freeSlot({ x: 300, y: 38 }, { w: 200, h: 100 }, taken, origin, 600)).toEqual({ x: 300, y: 38 });
    // The row is full: under everything, from the left edge.
    expect(freeSlot({ x: 12, y: 38 }, { w: 200, h: 100 }, [{ x: 12, y: 38, w: 200, h: 100 }, { x: 228, y: 38, w: 200, h: 100 }], origin, 440)).toEqual({ x: 12, y: 154 });
  });

  it("place: a segment pinned onto another's slot does not hide it; the unpinned one moves and the site holds both", () => {
    const graph = graphOf(TE);
    const plain = place(graph);
    const abs = (p: ReturnType<typeof place>, id: string) => {
      const n = p.nodes.find((x) => x.id === id)!;
      const parent = n.parentId ? p.nodes.find((x) => x.id === n.parentId)! : null;
      return { x: n.position.x + (parent?.position.x ?? 0), y: n.position.y + (parent?.position.y ?? 0), w: n.width!, h: n.height! };
    };
    const office = abs(plain, "seg:office");
    // Pin `servers` exactly where `office` is packed (a 12 px offset would do the same).
    const pinned = place(graph, { "segment:10.70.20.0/24": { x: office.x + 12, y: office.y + 12 } });
    const o = abs(pinned, "seg:office");
    const sv = abs(pinned, "seg:servers");
    const s3 = abs(pinned, "seg:segment-3");
    expect([sv.x, sv.y]).toEqual([office.x + 12, office.y + 12]);
    const overlap = (a: typeof o, b: typeof o) => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
    expect(overlap(o, sv)).toBe(false);
    expect(overlap(s3, sv)).toBe(false);
    expect(overlap(o, s3)).toBe(false);
    const site = pinned.nodes.find((n) => n.type === "site")!;
    for (const b of [o, sv, s3]) {
      expect(b.x + b.w).toBeLessThanOrEqual(site.position.x + site.width! + 0.01);
      expect(b.y + b.h).toBeLessThanOrEqual(site.position.y + site.height! + 0.01);
    }
    // Legs ride above the boxes.
    expect(plain.edges.filter((e) => e.type === "leg").every((e) => e.zIndex === EDGE_Z)).toBe(true);
    expect(EDGE_Z).toBeGreaterThan(2);
  });
});

describe("positions settle for the session", () => {
  it("adding a segment and a node leaves the existing segments where they were, and the new segment clear of them", async () => {
    const { api } = seeded();
    renderAt(api, "/twins/corp-auth/canvas");
    await screen.findAllByTestId("canvas-host");
    const before = positions();
    expect(Object.keys(before)).toContain("segment:10.1.0.0/24");
    fireEvent.click(item("segment", "segment"));
    await waitFor(() => expect(segments()).toEqual(["net-dmz", "net-lan", "segment-3"]));
    const after = positions();
    expect(after["segment:10.1.0.0/24"]).toEqual(before["segment:10.1.0.0/24"]);
    expect(after["segment:10.1.1.0/24"]).toEqual(before["segment:10.1.1.0/24"]);
    expect(after["site:10.1"]).toEqual(before["site:10.1"]);
    expect(after["segment:10.1.2.0/24"]).not.toEqual(after["segment:10.1.0.0/24"]);
    expect(after["segment:10.1.2.0/24"]).not.toEqual(after["segment:10.1.1.0/24"]);
    // A node onto the new segment: still nothing else moves.
    fireEvent.click(item("image", "debian-12-cloud@v1"));
    fireEvent.click(segment("segment-3"));
    await waitFor(() => expect(screen.getAllByTestId("canvas-host").length).toBe(3));
    const later = positions();
    expect(later["segment:10.1.0.0/24"]).toEqual(before["segment:10.1.0.0/24"]);
    expect(later["segment:10.1.2.0/24"]).toEqual(after["segment:10.1.2.0/24"]);
    // An armed router on an empty spot does not move them either.
    fireEvent.click(item("router", "router"));
    clickPane();
    await waitFor(() => expect(screen.getAllByTestId("canvas-router").length).toBe(2));
    expect(positions()["segment:10.1.1.0/24"]).toEqual(before["segment:10.1.1.0/24"]);
  });
});
