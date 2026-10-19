/**
 * R6 as RENDERED (`docs/WORK_ORDER_console_level0_rendering.md` §3 R6) — the
 * half `r6.columns.test.ts` cannot see.
 *
 * The geometry can be right and the drawing still wrong in the two ways this
 * slice is about: a `+N more` tile that names no count is a projection nobody
 * can see, and a byte figure the layout carries but the tile never draws is F5
 * still open.
 */
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { buildGraph } from "../src/model/graph";
import { NO_FILTERS, type Filters } from "../src/model/zoom";
import { SITE_BUDGET } from "../src/model/budgets";
import {
  Level0,
  arcTitle,
  tileBudgetNote,
  overflowTitle,
  overflowLabel,
  toggleExpand,
} from "../src/views/Level0";
import type { Arc } from "../src/layout/arcs";
import {
  trimmedCast,
  trimmedExport,
  withCollapsedTraffic,
  withOverBudgetSite,
} from "./fixtures/trimmed";

const plain = buildGraph(trimmedExport, trimmedCast);
const over = buildGraph(withOverBudgetSite(), trimmedCast);
/** The same drawing with traffic reaching the collapsed set, so the volume the
 *  budget hides is a real figure rather than a zero. */
const traffic = buildGraph(withCollapsedTraffic(), trimmedCast);
const BIG = "site:10.80";

function draw(
  graph = over,
  filters: Filters = NO_FILTERS,
  go: (path: string) => void = () => {},
): ReturnType<typeof render> {
  return render(<Level0 graph={graph} filters={filters} go={go} onFocus={() => {}} />);
}

describe("the `+N more` tile (R6)", () => {
  it("draws one per over-budget group, naming the count and the total", () => {
    draw();
    const tiles = screen.getAllByTestId("overflow-tile");
    expect(tiles).toHaveLength(1);
    expect(tiles[0]!.getAttribute("data-site")).toBe(BIG);
    expect(tiles[0]!.getAttribute("data-hidden")).toBe("3");
    // The affordance itself carries the number -- not just the note above.
    const group = tiles[0]!.parentElement!;
    expect(group.textContent).toContain("+3 more");
    expect(group.textContent).toContain("of 9 segments");
  });

  it("draws exactly SITE_BUDGET tiles in that group, and every tile elsewhere", () => {
    const { container } = draw();
    const inBig = [...container.querySelectorAll("[data-pinnable^='segment:10.80.']")];
    expect(inBig).toHaveLength(SITE_BUDGET);
    // The two groups under budget are untouched: 2 + 2 + 6 = 10 tiles.
    expect(screen.getAllByTestId("segment-tile")).toHaveLength(SITE_BUDGET + 4);
  });

  it("opens the group through the URL, not a store", () => {
    const go = vi.fn();
    draw(over, NO_FILTERS, go);
    fireEvent.click(screen.getByTestId("overflow-tile").parentElement!);
    expect(go).toHaveBeenCalledWith("/design/trimmed-fixture?expand=site:10.80");
  });

  it("becomes the way BACK once the group is expanded", () => {
    const go = vi.fn();
    draw(over, { ...NO_FILTERS, expand: [BIG] }, go);
    const tile = screen.getByTestId("overflow-tile");
    expect(tile.getAttribute("data-hidden")).toBe("0");
    expect(tile.parentElement!.textContent).toContain("collapse");
    expect(tile.parentElement!.textContent).toContain("showing all 9");
    fireEvent.click(tile.parentElement!);
    expect(go).toHaveBeenCalledWith("/design/trimmed-fixture");
    // ...and the whole group is drawn.
    expect(screen.getAllByTestId("segment-tile")).toHaveLength(13);
  });

  it("says the same thing on hover and to a screen reader, from one helper", () => {
    draw();
    const group = screen.getByTestId("overflow-tile").parentElement!;
    const title = group.querySelector("title")!.textContent;
    expect(title).toBe(
      "3 of 9 segments in site:10.80 are collapsed: 10.80.84.0/24, 10.80.85.0/24, " +
        "10.80.86.0/24 — click to show them",
    );
    expect(group.getAttribute("aria-label")).toContain("Expand site:10.80: 3 more segments");
    expect(group.getAttribute("aria-label")).toContain(title!);
  });
});

describe("the budget's own sentence (R6, LES-307/LES-312)", () => {
  it("counts the tiles DRAWN, and names where the rest are", () => {
    draw();
    expect(screen.getByTestId("budget-note").textContent).toBe(
      "Segments: showing 10 of 13 segments · 3 collapsed in 1 group — open a " +
        "group to see them (the outline, the matrix and search list every one)",
    );
  });

  it("claims NO residual when nothing is collapsed", () => {
    draw(plain);
    expect(screen.getByTestId("budget-note").textContent).toBe(
      "Segments: showing all 6 segments",
    );
    expect(screen.queryAllByTestId("overflow-tile")).toHaveLength(0);
  });

  it("reaches assistive tech through the SVG description as well", () => {
    draw();
    expect(screen.getByTestId("level0-desc").textContent).toContain(
      "The segment tier is showing 10 of 13 segments · 3 collapsed in 1 group",
    );
  });

  it("degenerates cleanly", () => {
    expect(tileBudgetNote(0, 0, 0)).toBe("no segments in this twin");
    expect(tileBudgetNote(1, 0, 0)).toBe("showing all 1 segment");
    expect(tileBudgetNote(6, 1, 1)).toContain("1 collapsed in 1 group —");
    expect(tileBudgetNote(12, 8, 2)).toContain("8 collapsed in 2 groups");
  });
});

// ===========================================================================
// The volume the budget hides — review S1. The tile byte line CANNOT show it:
// a collapsed segment has no tile.
// ===========================================================================
describe("the collapsed set's VOLUME is disclosed (R6, review S1)", () => {
  it("names it in the group's own affordance, on hover and in the label", () => {
    draw(traffic);
    const group = screen.getByTestId("overflow-tile").parentElement!;
    expect(group.querySelector("title")!.textContent).toBe(
      "3 of 9 segments in site:10.80 are collapsed, carrying 0 B north-south and " +
        "1.1 MB east-west: 10.80.84.0/24, 10.80.85.0/24, 10.80.86.0/24 — click to show them",
    );
    expect(group.getAttribute("aria-label")).toContain("carrying 0 B north-south and 1.1 MB east-west");
  });

  it("names it in the tier's one sentence too", () => {
    draw(traffic);
    expect(screen.getByTestId("budget-note").textContent).toContain(
      "3 collapsed in 1 group, carrying 0 B north-south and 1.1 MB east-west — open a group",
    );
  });

  it("says nothing when the collapsed set carried nothing", () => {
    // Absence of the clause is not a claim: the COUNT is still there, and
    // "0 B and 0 B" would be noise on every quiet twin.
    draw(over);
    expect(screen.getByTestId("budget-note").textContent).not.toContain("carrying");
    expect(
      screen.getByTestId("overflow-tile").parentElement!.querySelector("title")!.textContent,
    ).not.toContain("carrying");
  });
});

describe("a pin held for a collapsed tile is visible (R6, review S2)", () => {
  const pins = {
    "segment:10.80.85.0/24": { x: 10, y: 10 }, // collapsed
    "segment:10.80.72.0/24": { x: 20, y: 20 }, // drawn
  };

  it("counts the pins the operator cannot see moving", () => {
    render(
      <Level0
        graph={over}
        filters={NO_FILTERS}
        go={() => {}}
        onFocus={() => {}}
        savedPins={pins}
        onSavePins={() => {}}
      />,
    );
    expect(screen.getByTestId("pin-controls").textContent).toContain("2 pinned");
    expect(screen.getByTestId("pins-held").textContent).toBe(" · 1 held for collapsed tiles");
  });

  it("says nothing once the group is expanded and they are all on the map", () => {
    render(
      <Level0
        graph={over}
        filters={{ ...NO_FILTERS, expand: [BIG] }}
        go={() => {}}
        onFocus={() => {}}
        savedPins={pins}
        onSavePins={() => {}}
      />,
    );
    expect(screen.queryByTestId("pins-held")).toBeNull();
  });
});

describe("a merged arc says its protocol mix is unavailable (R6)", () => {
  const merged: Arc = {
    a: "seg-10-20-193-0",
    b: "overflow:site:10.80",
    kind: "observed",
    from: { x: 0, y: 0 },
    to: { x: 0, y: 0 },
    dropFrom: { x: 0, y: 0 },
    dropTo: { x: 0, y: 0 },
    c1: { x: 0, y: 0 },
    c2: { x: 0, y: 0 },
    bytes: 1_000_000,
    flows: 12,
    hasTraffic: true,
    strokeWidth: 3,
    pairs: 2,
  };

  it("states the merge rather than dropping the clause in silence", () => {
    expect(arcTitle(merged, (n) => n)).toBe(
      "seg-10-20-193-0 ↔ overflow:site:10.80: 1.0 MB east-west in this window " +
        "(12 flows) · 2 links merged · protocol mix is per link; this curve merges 2, " +
        "so the matrix has each one",
    );
  });

  it("leaves an ordinary arc's wording alone", () => {
    expect(arcTitle({ ...merged, pairs: 1 }, (n) => n)).toBe(
      "seg-10-20-193-0 ↔ overflow:site:10.80: 1.0 MB east-west in this window (12 flows)",
    );
  });
});

describe("the tile's byte figure (R6, F5)", () => {
  it("draws both directions on every tile, in this window", () => {
    const { container } = draw(plain);
    const tile = container.querySelector('[data-pinnable="segment:10.80.20.0/24"]')!;
    expect(tile.querySelector("[data-testid='tile-bytes']")!.textContent).toBe(
      "N-S 0 B · E-W 150 B",
    );
    // A segment that DID talk off-twin, so the two figures are not both a
    // fixture artefact of one tile.
    const busy = container.querySelector('[data-pinnable="segment:10.80.72.0/24"]')!;
    expect(busy.querySelector("[data-testid='tile-bytes']")!.textContent).toBe(
      "N-S 5.1 kB · E-W 150 B",
    );
    // Every tile has one -- a segment with no traffic says "0 B", which is a
    // measurement, not a missing line.
    expect(screen.getAllByTestId("tile-bytes")).toHaveLength(6);
  });

  it("puts the same figure in the tile's accessible name", () => {
    draw(plain);
    expect(
      screen.getByLabelText(/^Segment 10\.80\.20\.0\/24, 18 hosts, 0 B north-south and 150 B east-west in this window/),
    ).toBeDefined();
  });
});

describe("the ranking is visible and switchable (R6)", () => {
  it("offers both orders, marking the current one", () => {
    draw();
    expect(screen.getByTestId("order-hosts").className).toContain("is-current");
    expect(screen.getByTestId("order-cidr").className).not.toContain("is-current");
  });

  it("switches through the URL", () => {
    const go = vi.fn();
    draw(over, NO_FILTERS, go);
    fireEvent.click(screen.getByTestId("order-cidr"));
    expect(go).toHaveBeenCalledWith("/design/trimmed-fixture?order=cidr");
  });

  it("draws a different six under `?order=cidr`", () => {
    // The two orders differ in WHICH six, not only in their order: by hosts the
    // group's third-busiest segment (`10.80.87.0/24`, five hosts) is drawn and
    // `10.80.84.0/24` (one) is not; by CIDR it is the other way round.
    const byHosts = draw(over).container;
    expect(byHosts.querySelector('[data-pinnable="segment:10.80.87.0/24"]')).toBeTruthy();
    expect(byHosts.querySelector('[data-pinnable="segment:10.80.84.0/24"]')).toBeNull();

    const byCidr = draw(over, { ...NO_FILTERS, tileOrder: "cidr" }).container;
    expect(byCidr.querySelector('[data-pinnable="segment:10.80.87.0/24"]')).toBeNull();
    expect(byCidr.querySelector('[data-pinnable="segment:10.80.84.0/24"]')).toBeTruthy();
  });
});

describe("the expand toggle is a set operation", () => {
  it("adds, removes and stays sorted", () => {
    expect(toggleExpand([], "site:10.80")).toEqual(["site:10.80"]);
    expect(toggleExpand(["site:10.80"], "site:10.80")).toEqual([]);
    expect(toggleExpand(["site:10.80"], "site:10.20")).toEqual(["site:10.20", "site:10.80"]);
  });

  it("phrases the two directions differently", () => {
    const collapsed = { siteKey: BIG, box: { x: 0, y: 0, w: 1, h: 1 }, hidden: [{ name: "n", cidr: "10.80.85.0/24" }], expanded: false, total: 7 };
    const expanded = { ...collapsed, hidden: [], expanded: true };
    expect(overflowTitle(collapsed)).toContain("are collapsed: 10.80.85.0/24");
    expect(overflowTitle(expanded)).toContain("is showing all 7 of its segments");
    expect(overflowLabel(expanded)).toContain(`Collapse ${BIG}`);
  });
});
