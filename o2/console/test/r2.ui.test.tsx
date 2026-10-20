/**
 * R2 — the trunk, the tap and the stale-pin note as RENDERED
 * (`docs/WORK_ORDER_console_level0_rendering.md` §3 R2). The pure geometry,
 * the scale and the plane stamp are `r2.trunk.test.ts`.
 *
 * What this file is for: the geometry can be perfect and the drawing still
 * wrong — a leg rendered without its width carries no magnitude, a vantage
 * rendered through a parser is a different string, and a stale-pin note that
 * never reaches the DOM is a silent corruption with a comment about it.
 */
import { act, render, renderHook, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { PointerEvent as ReactPointerEvent } from "react";

import { usePinDrag } from "../src/views/usePinDrag";

import { buildGraph } from "../src/model/graph";
import { layout0 } from "../src/layout/level0";
import { PLANE_STAMP } from "../src/layout/plane";
import { HAIRLINE } from "../src/layout/scale";
import type { AuthoredPin, Pins } from "../src/layout/pins";
import { NO_FILTERS } from "../src/model/zoom";
import { Level0, tapLabel, wanLabelFor } from "../src/views/Level0";
import { trimmedCast, trimmedExport } from "./fixtures/trimmed";

const graph = buildGraph(trimmedExport, trimmedCast);

function draw(
  over: Partial<Parameters<typeof Level0>[0]> = {},
): ReturnType<typeof render> {
  return render(
    <Level0 graph={graph} filters={NO_FILTERS} go={() => {}} onFocus={() => {}} {...over} />,
  );
}

describe("the trunk is drawn as a connected path (R2)", () => {
  it("draws the spine, the stem, the bus and one leg per site group", () => {
    const { container } = draw();
    expect(screen.getByTestId("trunk-spine")).toBeDefined();
    expect(screen.getByTestId("trunk-stem")).toBeDefined();
    expect(screen.getByTestId("trunk-bus")).toBeDefined();
    const legs = screen.getAllByTestId("trunk-leg");
    expect(legs).toHaveLength(3);
    expect(legs.map((l) => l.getAttribute("data-site"))).toEqual([
      "site:10.20", "site:10.80", "site:misc",
    ]);
    // The trunk paints BEFORE the tiles, so a tile covers a leg rather than a
    // leg striking through a tile.
    const svg = container.querySelector("svg.level0")!;
    const order = [...svg.querySelectorAll("[data-testid]")].map((n) => n.getAttribute("data-testid"));
    expect(order.indexOf("trunk")).toBeLessThan(order.indexOf("segment-tile"));
  });

  it("states the PATH for assistive tech, since the trunk group is aria-hidden", () => {
    draw();
    // The trunk's own `<g>` is `aria-hidden`, so without this clause R2's
    // whole contribution reaches a screen reader nowhere at all.
    const desc = screen.getByTestId("level0-desc").textContent ?? "";
    expect(desc).toContain("A trunk runs from the Internet through the capture tap");
    expect(desc).toContain("3 legs");
    expect(desc).toContain("in proportion to its traffic across the edge");
  });

  it("carries the magnitude in the STROKE, so the busiest group reads before any label", () => {
    draw();
    const widths = new Map(
      screen.getAllByTestId("trunk-leg").map((l) => [
        l.getAttribute("data-site"),
        Number(l.getAttribute("stroke-width")),
      ]),
    );
    expect(widths.get("site:10.80")).toBe(6); // the full width
    // HAIRLINE is the CLAIM ("no magnitude"); `.trunk.is-silent`'s
    // `stroke-width: 1` is what the browser actually draws, and it beats this
    // presentation attribute (final review S4). Assert both, or this line
    // reads as "the silent leg renders at 0.5px", which it does not.
    expect(widths.get("site:misc")).toBe(HAIRLINE);
    expect(
      screen.getAllByTestId("trunk-leg").find((l) => l.getAttribute("data-site") === "site:misc")!
        .getAttribute("class"),
    ).toBe("trunk is-silent");
    expect(widths.get("site:10.20")!).toBeGreaterThan(HAIRLINE);
    expect(widths.get("site:10.20")!).toBeLessThan(widths.get("site:10.80")!);
  });

  it("labels the busiest leg with its bytes and says so on hover", () => {
    draw();
    const labels = screen.getAllByTestId("trunk-leg-label").map((n) => n.textContent);
    // The LITERAL, not `formatBytes(5080)`: comparing the render against the
    // same function the render calls cannot fail if that function changes.
    expect(labels).toEqual(["5.1 kB"]);
    const busiest = screen
      .getAllByTestId("trunk-leg")
      .find((l) => l.getAttribute("data-site") === "site:10.80")!;
    expect(busiest.querySelector("title")?.textContent).toContain("north-south in this window");
  });

  it("marks a SILENT leg so a hairline is not an invisible one (S4)", () => {
    draw();
    const byKey = new Map(
      screen.getAllByTestId("trunk-leg").map((l) => [l.getAttribute("data-site"), l]),
    );
    // 0 bytes: present, dashed, and distinguishable from a measured leg.
    expect(byKey.get("site:misc")!.getAttribute("class")).toBe("trunk is-silent");
    // Every leg that DID carry traffic is a plain trunk stroke.
    expect(byKey.get("site:10.80")!.getAttribute("class")).toBe("trunk");
    expect(byKey.get("site:10.20")!.getAttribute("class")).toBe("trunk");
  });

  it("marks the SPINE, STEM and BUS silent too when a declared gateway carried nothing (S3)", () => {
    // A cast-declared gateway whose edge was silent in this window: every
    // trunk element takes the hairline, so without this the whole path renders
    // at 0.5px under 50% opacity -- i.e. the diagram claims a gateway and then
    // draws nothing you can see.
    const external = new Set(graph.nonLocal.map((n) => n.id));
    const silent = buildGraph(
      {
        ...trimmedExport,
        nodes: trimmedExport.nodes.filter((n) => !external.has(n.id)),
        edges: trimmedExport.edges.filter((e) => !external.has(e.src) && !external.has(e.dst)),
      },
      trimmedCast, // routers ARE declared -- that is what keeps the trunk drawn
    );
    render(<Level0 graph={silent} filters={NO_FILTERS} go={() => {}} onFocus={() => {}} />);
    for (const id of ["trunk-spine", "trunk-stem", "trunk-bus"]) {
      expect(screen.getByTestId(id).getAttribute("class"), id).toBe("trunk is-silent");
    }
    // ...and a trunk that DID carry traffic is not marked.
    for (const id of ["trunk-spine", "trunk-stem", "trunk-bus"]) {
      expect(screen.queryAllByTestId(id)).toHaveLength(1);
    }
  });

  it("names the whole edge's volume on the spine, and where the unlegged part went (S3)", () => {
    // The fixture's crossing traffic all has a PLACED local end, so the total
    // equals the legs and the sentence stays short.
    draw();
    const title = screen.getByTestId("trunk-spine").querySelector("title")!.textContent ?? "";
    expect(title).toContain("5.1 kB crossed this twin's edge in this window");
    expect(title).toContain("17 flows");
    expect(title).not.toContain("placed in no segment");
  });

  it("names the remainder when traffic crosses from an UNPLACED local entity (S3)", () => {
    // `Trunk.bytes` counts every crossing edge; a leg can only carry traffic
    // whose local end was placed. Publishing that difference and never
    // rendering it is what the docstring's "recoverable" claim would make false.
    const withUnplaced = buildGraph(
      {
        ...trimmedExport,
        edges: [
          ...trimmedExport.edges,
          { src: "address:10.99.99.99", dst: "address:8.8.8.8", proto: "tcp", bytes: 900_000, flows: 3, evidence_class: "observed" },
        ],
      },
      trimmedCast,
    );
    render(<Level0 graph={withUnplaced} filters={NO_FILTERS} go={() => {}} onFocus={() => {}} />);
    const title = screen.getByTestId("trunk-spine").querySelector("title")!.textContent ?? "";
    expect(title).toContain("905 kB crossed this twin's edge");
    expect(title).toContain("900 kB of it to or from local entities the export placed in no segment");
    expect(title).toContain("unplaced tile");
  });
});

describe("the tap badge (F8)", () => {
  it("shows the vantage VERBATIM -- never parsed", () => {
    // The pit bundle's spelling: a path-and-colon form that is not a URL.
    const vantage = "axellio-hq/corp-apcn-rdr1:12";
    const withVantage = buildGraph({ ...trimmedExport, vantage }, trimmedCast);
    render(<Level0 graph={withVantage} filters={NO_FILTERS} go={() => {}} onFocus={() => {}} />);
    expect(screen.getByTestId("tap-badge")).toBeDefined();
    expect(screen.getByTestId("tap-vantage").textContent).toBe(`${vantage} · 10 windows`);
  });

  it("renders the OTHER real spelling identically -- a URL-shaped vantage", () => {
    // The twin bundle's: `https://10.80.65.7:0-11`. Neither is parsed, which
    // is the point -- one parser would be wrong on one of them.
    const vantage = "https://10.80.65.7:0-11";
    const l0 = layout0(buildGraph({ ...trimmedExport, vantage, windows: 11 }, trimmedCast));
    expect(tapLabel(l0.tap!)).toBe(`${vantage} · 11 windows`);
  });

  it("says the vantage is unrecorded rather than drawing an empty label", () => {
    draw();
    expect(screen.getByTestId("tap-vantage").textContent).toBe("vantage not recorded · 10 windows");
  });
});

describe("the WAN edge's label (R2)", () => {
  it("names the gateway when the cast DECLARES one", () => {
    draw();
    expect(screen.getByTestId("wan-label").textContent).toBe("gateway · 2 legs · SNAT");
  });

  it("says INFERRED -- never 'gateway' -- when only traffic supports the edge", () => {
    const inferred = buildGraph(trimmedExport, { ...trimmedCast, routers: [] });
    expect(wanLabelFor(inferred)).toBe("egress (inferred from observed traffic)");
    render(<Level0 graph={inferred} filters={NO_FILTERS} go={() => {}} onFocus={() => {}} />);
    expect(screen.getByTestId("wan-label").textContent).toBe(
      "egress (inferred from observed traffic)",
    );
  });

  it("draws NOTHING and says why when neither traffic nor a router supports it", () => {
    const external = new Set(graph.nonLocal.map((n) => n.id));
    const local = buildGraph(
      {
        ...trimmedExport,
        nodes: trimmedExport.nodes.filter((n) => !external.has(n.id)),
        edges: trimmedExport.edges.filter((e) => !external.has(e.src) && !external.has(e.dst)),
      },
      { ...trimmedCast, routers: [] },
    );
    render(<Level0 graph={local} filters={NO_FILTERS} go={() => {}} onFocus={() => {}} />);
    expect(screen.queryByTestId("wan-label")).toBeNull();
    expect(screen.queryByTestId("trunk")).toBeNull();
    expect(screen.queryByTestId("tap-badge")).toBeNull();
    // The absence is EXPLAINED: an absent edge on a diagram otherwise reads as
    // "this twin has no egress".
    expect(screen.getByTestId("no-wan-edge").textContent).toContain(
      "not a statement that this twin has no egress",
    );
    // ...and the same is said to assistive tech, which cannot see the absence.
    expect(screen.getByTestId("level0-desc").textContent).toContain(
      "No external edge was observed or declared, so no trunk is drawn.",
    );
  });
});

describe("a drag reports WHICH pins it re-authored (review M1)", () => {
  /** A stub `<svg>` whose viewBox matches its rect 1:1, so a client delta is an
   *  equal layout delta without a real browser CTM. */
  function fakeSvg(): SVGSVGElement {
    return {
      getScreenCTM: () => null,
      getBoundingClientRect: () => ({ left: 0, top: 0, width: 100, height: 100 }),
      viewBox: { baseVal: { x: 0, y: 0, width: 100, height: 100 } },
      setPointerCapture: () => {},
      releasePointerCapture: () => {},
    } as unknown as SVGSVGElement;
  }
  const ptr = (over: Partial<{ clientX: number; clientY: number }>): ReactPointerEvent =>
    ({ button: 0, pointerId: 1, clientX: 0, clientY: 0, ...over }) as unknown as ReactPointerEvent;

  it("reports the pin it wrote by key AND value, so an inherited pin is never claimed", () => {
    const onCommit = vi.fn();
    const svgRef = { current: fakeSvg() };
    // Two pins already on file; the operator touches ONE of them.
    const savedPins: Pins = { "site:10.80": { x: 10, y: 10 }, "site:10.20": { x: 20, y: 20 } };
    const { result } = renderHook(() => usePinDrag({ svgRef, savedPins, onCommit }));

    act(() => result.current.begin("site:10.80", { x: 10, y: 10 }, ptr({ clientX: 5, clientY: 5 })));
    act(() => result.current.svgProps.onPointerMove(ptr({ clientX: 60, clientY: 45 })));
    act(() => result.current.svgProps.onPointerUp(ptr({ clientX: 60, clientY: 45 })));

    expect(onCommit).toHaveBeenCalledTimes(1);
    const [pins, authored] = onCommit.mock.calls[0]! as [Pins, AuthoredPin];
    // The commit carries BOTH pins (that is the shape of the bug) ...
    expect(Object.keys(pins).sort()).toEqual(["site:10.20", "site:10.80"]);
    // ... and names only the one it wrote, WITH the coordinates it wrote --
    // the value is what stays true after another writer replaces the document.
    expect(authored).toEqual({ key: "site:10.80", at: pins["site:10.80"] });
  });

  it("holds NO session state of its own -- each drag reports only itself", () => {
    // The accumulation lives in the workspace, beside the layout document
    // (final review S1): `Level0` unmounts on a drill to level 1, so a session
    // map kept in this hook would silently lose the operator's progress.
    const onCommit = vi.fn();
    const svgRef = { current: fakeSvg() };
    const savedPins: Pins = { "site:10.80": { x: 10, y: 10 }, "site:10.20": { x: 20, y: 20 } };
    const { result } = renderHook(() => usePinDrag({ svgRef, savedPins, onCommit }));

    for (const key of ["site:10.80", "site:10.20"]) {
      act(() => result.current.begin(key, { x: 10, y: 10 }, ptr({ clientX: 5, clientY: 5 })));
      act(() => result.current.svgProps.onPointerMove(ptr({ clientX: 60, clientY: 45 })));
      act(() => result.current.svgProps.onPointerUp(ptr({ clientX: 60, clientY: 45 })));
    }
    const [, first] = onCommit.mock.calls[0]! as [Pins, AuthoredPin];
    const [, second] = onCommit.mock.calls[1]! as [Pins, AuthoredPin];
    expect(first.key).toBe("site:10.80");
    expect(second.key).toBe("site:10.20");
  });
});

describe("pins saved against another layout plane (R2, LES-289)", () => {
  const pins: Pins = { "site:10.80": { x: 900, y: 620 } };

  it("says nothing when the stamp matches", () => {
    draw({ savedPins: pins, pinsPlane: PLANE_STAMP, onSavePins: () => {} });
    expect(screen.queryByTestId("pins-plane-note")).toBeNull();
  });

  it("says nothing when there are no pins, whatever the stamp", () => {
    draw({ savedPins: {}, pinsPlane: "l0-1-00000000", onSavePins: () => {} });
    expect(screen.queryByTestId("pins-plane-note")).toBeNull();
  });

  it("FLAGS a stale stamp, names both planes, and still draws the pins", () => {
    draw({ savedPins: pins, pinsPlane: "l0-1-00000000", onSavePins: () => {} });
    const note = screen.getByTestId("pins-plane-note").textContent ?? "";
    expect(note).toContain("l0-1-00000000");
    expect(note).toContain(PLANE_STAMP);
    expect(note).toContain("shown as saved rather than silently dropped");
    // The advice must match the mechanism (M1): ONE drag does not clear this.
    expect(note).toContain("re-place every one");
    expect(note).not.toContain("drag one to re-pin");
    // The reset control is right there, and the pin was applied, not dropped.
    expect(screen.getByTestId("pin-reset")).toBeDefined();
    const site = screen.getByLabelText(/Site 10\.80/);
    expect(site.querySelector("rect.site-box")!.getAttribute("x")).toBe("900");
  });

  it("FLAGS a legacy (unstamped) file as saved-before-the-stamp, and keeps the pins", () => {
    draw({ savedPins: pins, pinsPlane: null, onSavePins: () => {} });
    const note = screen.getByTestId("pins-plane-note").textContent ?? "";
    expect(note).toContain("before the Console recorded which layout");
    expect(screen.getByTestId("pin-reset")).toBeDefined();
  });
});
