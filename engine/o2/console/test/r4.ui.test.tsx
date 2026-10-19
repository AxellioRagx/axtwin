/**
 * R4 as RENDERED (`docs/WORK_ORDER_console_level0_rendering.md` §3 R4) — the
 * half `r4.arcs.test.ts` cannot see.
 *
 * Three things can be geometrically right and still wrong on screen: an arc
 * that never reaches the DOM leaves the retired lanes in place; an asserted
 * conduit drawn like an observed flow loses the one property the arcs ruling
 * kept for the asserted set; and a top-K projection that does not SAY it is
 * one reads as the complete answer (LES-307).
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { buildGraph } from "../src/model/graph";
import { NO_FILTERS } from "../src/model/zoom";
import { ARC_TOP_K } from "../src/layout/arcs";
import { Level0, arcTitle, eastWestNote } from "../src/views/Level0";
import { trimmedCast, trimmedExport, withEastWestPairs } from "./fixtures/trimmed";

const graph = buildGraph(trimmedExport, trimmedCast);
/** 15 observed pairs and NO roster: 12 drawn, 3 hidden -- the budget's case. */
const manyGraph = buildGraph(withEastWestPairs(), { ...trimmedCast, conduits: [] });

function draw(g = graph, filters = NO_FILTERS, go: (p: string) => void = () => {}) {
  return render(<Level0 graph={g} filters={filters} go={go} onFocus={() => {}} />);
}

describe("the arcs reach the DOM, and the lanes are gone (R4)", () => {
  it("draws one path per arc, and NO lane element anywhere", () => {
    const { container } = draw();
    expect(screen.getAllByTestId("east-west-arc")).toHaveLength(3);
    // The retired geometry: a `<polyline class="lane">` per conduit.
    expect(container.querySelectorAll("polyline.lane")).toHaveLength(0);
    expect(container.querySelectorAll(".lane")).toHaveLength(0);
    expect(container.querySelectorAll("[data-testid='conduit-lane']")).toHaveLength(0);
  });

  it("draws each arc as a drop-curve-drop path, with its width as an attribute", () => {
    draw();
    for (const arc of screen.getAllByTestId("east-west-arc")) {
      const d = arc.getAttribute("d") ?? "";
      expect(d.startsWith("M "), d).toBe(true);
      expect(d).toContain(" L "); // the drop into the band
      expect(d).toContain(" C "); // the curve across it
      expect(Number(arc.getAttribute("stroke-width"))).toBeGreaterThan(0);
    }
  });

  it("marks an ASSERTED conduit in the edit family, and a silent one dashed", () => {
    const { container } = draw();
    const asserted = container.querySelectorAll("path.arc.is-asserted");
    expect(asserted).toHaveLength(3);
    // Two of the fixture's three conduits carry no observed traffic.
    expect(container.querySelectorAll("path.arc.is-silent")).toHaveLength(2);
    // ...and the one that does carry traffic is NOT dashed.
    const busy = [...asserted].find((a) => !a.classList.contains("is-silent"))!;
    expect(busy.getAttribute("data-bytes")).toBe("150");
  });

  it("says what an arc is, on hover, including the protocol the map cannot show", () => {
    draw();
    const titles = screen
      .getAllByTestId("east-west-arc")
      .map((a) => a.querySelector("title")?.textContent ?? "");
    expect(titles).toContain(
      "10.80.20.0/24 ↔ 10.80.72.0/24: declared conduit · 150 B east-west in this window (2 flows) · udp 150 B",
    );
    expect(titles).toContain(
      "10.20.192.0/24 ↔ 10.20.193.0/24: declared conduit · no traffic observed in this window",
    );
  });
});

describe("the top-K projection says N of M (LES-307)", () => {
  it("names the hidden pairs in words, and offers `show all`", () => {
    draw(manyGraph);
    expect(screen.getAllByTestId("east-west-arc")).toHaveLength(ARC_TOP_K);
    expect(screen.getByTestId("ew-note").textContent).toBe(
      `East-west: showing the 12 busiest of 15 observed east-west links · 3 smaller links hidden (the matrix has every one)`,
    );
    const toggle = screen.getByTestId("ew-toggle");
    expect(toggle.textContent).toBe("show all");
    // The accessible name carries the SAME sentence: the arcs are aria-hidden,
    // so a control reading only "show all" tells a screen-reader user nothing
    // about what is being hidden.
    expect(toggle.getAttribute("aria-label")).toBe(
      "showing the 12 busiest of 15 observed east-west links · 3 smaller links hidden (the matrix has every one) — show all",
    );
    // ...and so does the SVG description, the only channel for the drawing.
    expect(screen.getByTestId("level0-desc").textContent).toContain(
      "The east-west band beneath the segments is showing the 12 busiest of 15",
    );
  });

  it("navigates to `?ew=all`, preserving the other filters", () => {
    const go = vi.fn();
    draw(manyGraph, { ...NO_FILTERS, ghosts: true }, go);
    screen.getByTestId("ew-toggle").click();
    expect(go).toHaveBeenCalledWith("/design/trimmed-fixture?ghosts=1&ew=all");
  });

  it("under `?ew=all` it draws every pair and offers the way back", () => {
    const go = vi.fn();
    draw(manyGraph, { ...NO_FILTERS, eastWestAll: true }, go);
    expect(screen.getAllByTestId("east-west-arc")).toHaveLength(15);
    expect(screen.getByTestId("ew-note").textContent).toBe(
      "East-west: showing all 15 observed east-west links",
    );
    expect(screen.getByTestId("ew-toggle").textContent).toBe(`show the ${ARC_TOP_K} busiest`);
    screen.getByTestId("ew-toggle").click();
    expect(go).toHaveBeenCalledWith("/design/trimmed-fixture");
  });

  it("offers no toggle when nothing is hidden -- a control that does nothing is worse", () => {
    draw();
    expect(screen.queryByTestId("ew-toggle")).toBeNull();
  });

  it("counts BOTH families, on a fixture where they differ in size (review S2)", () => {
    // THE case the shipped note got wrong: this fixture draws THREE arcs --
    // one observed pair (which is also a conduit) and two conduits the capture
    // never saw -- under a sentence that counted only `trafficPairs`. Both
    // halves were pinned in this file, and neither noticed the other.
    draw();
    expect(screen.getAllByTestId("east-west-arc")).toHaveLength(3);
    expect(graph.trafficPairs).toHaveLength(1);
    expect(screen.getByTestId("ew-note").textContent).toBe(
      "East-west: showing all 1 observed east-west link · " +
        "2 declared conduits with no traffic in this window",
    );
    // ...and the same sentence reaches assistive tech, since the arcs are
    // aria-hidden.
    expect(screen.getByTestId("level0-desc").textContent).toContain(
      "The east-west band beneath the segments is showing all 1 observed east-west link · " +
        "2 declared conduits with no traffic in this window",
    );
  });

  it("says nothing about silent conduits when there are none (manyGraph)", () => {
    // The other side: a fixture whose families coincide must NOT grow a clause
    // for a fact that does not apply.
    draw(manyGraph);
    expect(screen.getByTestId("ew-note").textContent).not.toContain("declared conduit");
  });

  it("`eastWestNote` is ONE helper, so the three surfaces cannot drift", () => {
    expect(eastWestNote(12, 28)).toBe(
      "showing the 12 busiest of 40 observed east-west links · 28 smaller links hidden (the matrix has every one)",
    );
    expect(eastWestNote(1, 1)).toContain("1 smaller link hidden"); // singular
    expect(eastWestNote(5, 0)).toBe("showing all 5 observed east-west links");
    expect(eastWestNote(1, 0)).toBe("showing all 1 observed east-west link");
    expect(eastWestNote(0, 0)).toBe("no observed east-west traffic in this window");
    // The second family, at both numbers and in every clause shape.
    expect(eastWestNote(1, 0, 2)).toBe(
      "showing all 1 observed east-west link · 2 declared conduits with no traffic in this window",
    );
    expect(eastWestNote(5, 0, 1)).toBe(
      "showing all 5 observed east-west links · 1 declared conduit with no traffic in this window",
    );
    expect(eastWestNote(12, 28, 3)).toContain(
      "hidden (the matrix has every one) · 3 declared conduits with no traffic in this window",
    );
    // A twin that declares paths and observed none: both facts, one sentence.
    expect(eastWestNote(0, 0, 4)).toBe(
      "no observed east-west traffic in this window · 4 declared conduits with no traffic in this window",
    );
  });
});

describe("a twin with no observed east-west traffic says so (R4)", () => {
  const bare = buildGraph({ ...trimmedExport, edges: [] }, { ...trimmedCast, conduits: [] });

  it("renders the empty band with a note, not an unexplained gap", () => {
    draw(bare);
    expect(screen.queryAllByTestId("east-west-arc")).toHaveLength(0);
    expect(screen.getByTestId("no-east-west").textContent).toContain(
      "No observed east-west traffic in this window",
    );
    // ...and the top-K chrome is absent, since there is no projection to
    // disclose.
    expect(screen.queryByTestId("ew-controls")).toBeNull();
  });

  it("is DISTINCT from the roster-not-published note (GAP-079's, now deleted)", () => {
    // An old server's missing roster costs the twin's DECLARED conduits and
    // nothing else: the observed band is the export's. The roster has to be
    // ABSENT for that note at all -- `conduits: []` is the twin declaring
    // none, which is a different statement and says nothing (the `castOmits`
    // distinction R5 built).
    const { conduits: _absent, ...noRoster } = trimmedCast;
    const { container } = draw(buildGraph({ ...trimmedExport, edges: [] }, noRoster));
    const roster = screen.getByTestId("roster-unpublished");
    expect(roster.textContent).toContain("the twin's declared conduits");
    expect(container.textContent).not.toContain("the east-west band is not drawn");
    // Both notes stand together: nothing observed AND nothing published.
    expect(screen.getByTestId("no-east-west")).toBeDefined();
  });
});

describe("arcTitle (the hover text) is total", () => {
  const l0Arc = {
    a: "seg-a",
    b: "seg-b",
    kind: "observed" as const,
    from: { x: 0, y: 0 },
    to: { x: 0, y: 0 },
    dropFrom: { x: 0, y: 0 },
    dropTo: { x: 0, y: 0 },
    c1: { x: 0, y: 0 },
    c2: { x: 0, y: 0 },
    bytes: 2_230_038_148,
    flows: 384,
    hasTraffic: true,
    strokeWidth: 6,
    // R6: one curve, one segment pair -- the ordinary case, with nothing
    // collapsed. The merged case has its own assertion below.
    pairs: 1,
  };
  const label = (n: string) => (n === "seg-a" ? "10.80.20.0/24" : "10.80.75.0/24");

  it("names the pair, the volume and up to three protocols", () => {
    expect(
      arcTitle(l0Arc, label, {
        a: "seg-a",
        b: "seg-b",
        bytes: l0Arc.bytes,
        flows: l0Arc.flows,
        protoMix: [
          { proto: "smb", bytes: 2_228_600_000 },
          { proto: "tls", bytes: 1_000_000 },
          { proto: "other", bytes: 500_000 },
          { proto: "dns", bytes: 1 },
        ],
      }),
    ).toBe(
      "10.80.20.0/24 ↔ 10.80.75.0/24: 2.2 GB east-west in this window (384 flows) · " +
        "smb 2.2 GB, tls 1.0 MB, other 500 kB",
    );
  });

  it("drops the protocol clause when the mix is unknown, rather than inventing one", () => {
    expect(arcTitle(l0Arc, label)).toBe(
      "10.80.20.0/24 ↔ 10.80.75.0/24: 2.2 GB east-west in this window (384 flows)",
    );
  });

  it("falls back to the segment NAME when the graph has no CIDR for it", () => {
    expect(arcTitle(l0Arc, (n) => n)).toContain("seg-a ↔ seg-b");
  });
});
