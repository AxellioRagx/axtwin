/**
 * R1's acceptance, verified INDEPENDENTLY of the tests that shipped with it
 * (`docs/WORK_ORDER_console_level0_rendering.md` §3 R1).
 *
 * This file exists because a change's own tests cannot be the only evidence
 * that the change is correct: they were written by the same hand, against the
 * same mental model, and they naturally assert the paths the author had in
 * mind. Everything here either states the acceptance in a DIFFERENT form from
 * the shipped tests, or covers a realistic path they do not reach:
 *
 *   - the scale is read as the **computed transform** (rendered CSS width /
 *     viewBox units), never from `data-scale` alone and never compared to a
 *     constant the component also imports;
 *   - the summary flag is round-tripped through the **actual router location**
 *     after a click, not through the `href` the link advertises;
 *   - the realistic failure paths: a measurable pane, a pane too narrow for
 *     the floor, an unmeasurable pane, a tier with no content, and a browser
 *     profile where `localStorage` THROWS on access (private mode).
 *
 * The file-reading half of the audit -- the committed goldens' rails and the
 * stylesheet's rule split -- is in `r1.goldens.test.ts`, which runs in the
 * `node` environment: under `jsdom` `import.meta.url` resolves against the
 * document's `http://localhost/` base, so a path built from it escapes the
 * package (the same reason `layout.golden.test.ts` opts into `node`).
 */
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { useLocation } from "react-router";

import { ConsoleApiError, type ConsoleApi } from "../src/api/client";
import type {
  AssertionsDoc,
  CastSummary,
  ImprintList,
  ImprintProvenance,
} from "../src/api/types";
import { ConsoleRoutes, TestApp } from "../src/routes";
import { anyFilterActive, buildGraph } from "../src/model/graph";
import { layout0 } from "../src/layout/level0";
import { TIERS, TIER_LABELS } from "../src/layout/tiers";
import { BASE_SCALE, estimateMapWidth, MIN_FIT_SCALE } from "../src/layout/viewport";
import { filtersToQuery, NO_FILTERS, parseFilters } from "../src/model/zoom";
import { FiltersBar } from "../src/views/Filters";
import { Level0 } from "../src/views/Level0";
import { Level1 } from "../src/views/Level1";
import { MatrixView } from "../src/views/Matrix";
import { trimmedCast, trimmedExport } from "./fixtures/trimmed";

const graph = buildGraph(trimmedExport, trimmedCast);

/** The acceptance's window: 1,246 CSS px, outline OPEN. */
const ACCEPTANCE_WINDOW = 1246;

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------

/**
 * The scale the browser actually applies: rendered CSS pixels per layout unit.
 * Derived from two RENDERED attributes, so it measures the transform rather
 * than trusting the component's own `data-scale` claim (which is then checked
 * against it -- a published value that disagrees with the real geometry is
 * worse than no published value).
 */
function computedScale(svg: Element): number {
  const widthPx = Number(svg.getAttribute("width"));
  const units = Number((svg.getAttribute("viewBox") ?? "").split(/\s+/)[2]);
  return widthPx / units;
}

/** Stub the map pane's measured width. jsdom measures every element as 0, so
 *  without this only the estimate fallback is ever exercised. */
function stubPaneWidth(px: number): () => void {
  const proto = window.HTMLDivElement.prototype;
  const prior = Object.getOwnPropertyDescriptor(proto, "clientWidth");
  Object.defineProperty(proto, "clientWidth", { configurable: true, get: () => px });
  return () => {
    if (prior) Object.defineProperty(proto, "clientWidth", prior);
    else Reflect.deleteProperty(proto, "clientWidth");
  };
}

function setWindowWidth(px: number): void {
  Object.defineProperty(window, "innerWidth", { value: px, writable: true, configurable: true });
}

const restorers: Array<() => void> = [];
afterEach(() => {
  while (restorers.length > 0) restorers.pop()?.();
  setWindowWidth(1024); // jsdom's default
  try {
    window.localStorage.clear();
  } catch {
    // a test that denied storage already restored it; nothing to clear
  }
});

function drawLevel0(): Element {
  render(<Level0 graph={graph} filters={NO_FILTERS} go={() => {}} onFocus={() => {}} />);
  return screen.getByTestId("level0-svg");
}

// ---------------------------------------------------------------------------
// A minimal API fake for the shell-level tests, keyed to the trimmed fixture.
// ---------------------------------------------------------------------------
const CAST: CastSummary = {
  id: "trimmed-fixture",
  bundle_dir: "/b/trimmed-fixture",
  twin_config_id: "trimmed-fixture",
  description: null,
  pit_id: "pit-trim",
  bundle_schema: "axr-topology/1.5",
  topology_schema_version: "1.3",
  code_fingerprint: null,
  params: {},
  deploy_min_occupancy: 0.8,
  deploy_min_occupancy_source: "engine default",
  segments: 6,
  node_specs: 6,
  vms: 40,
  hosts: 40,
  conduits: 4,
  observed_nodes: trimmedExport.nodes.length,
  observed_edges: trimmedExport.edges.length,
  observed_segments: 6,
  census: {},
  has_provenance: false,
  has_topology_export: true,
  has_assertions: false,
  has_layout: false,
  provenance_schema: null,
  provenance_path: null,
  sidecar_shape: "absent",
  evidence_source: "",
  warnings: [],
  conduit_roster: [...(trimmedCast.conduits ?? [])],
  routers: (trimmedCast.routers ?? []).map((r) => ({ name: r.name, segments: [...r.segments] })),
};

const PROV_ABSENT: ImprintProvenance = {
  imprint_id: "trimmed-fixture",
  rows: [],
  sidecar_shape: "absent",
  sidecar_schema: null,
  evidence_source: "absent",
  code_fingerprint: null,
  population: {},
  warnings: [],
};

const CATALOG: ImprintList = {
  imprints: [
    {
      id: "trimmed-fixture",
      hosts: 40,
      segments: 6,
      as_of: "",
      mean_confidence: 0,
      has_provenance: false,
      confidence_source: "absent",
    },
  ],
  skipped: [],
};

const EMPTY_ASSERTIONS: AssertionsDoc = {
  imprint_id: "trimmed-fixture",
  path: "/b/trimmed-fixture/trimmed-fixture.assertions.yaml",
  exists: false,
  etag: null,
  schema: "axr-assertions/1",
  imprint: "trimmed-fixture",
  as_of: null,
  source: null,
  add: [],
  override: [],
  suppress: [],
  warnings: [],
};

function fakeApi(overrides: Partial<ConsoleApi> = {}): ConsoleApi {
  const unused = (name: string) => () => Promise.reject(new Error(`${name} not used`));
  return {
    listImprints: () => Promise.resolve(CATALOG),
    getImprint: unused("getImprint"),
    getProvenance: () => Promise.resolve(PROV_ABSENT),
    getTopology: () => Promise.resolve(trimmedExport),
    getCast: () => Promise.resolve(CAST),
    getAssertions: () => Promise.resolve(EMPTY_ASSERTIONS),
    putAssertions: unused("putAssertions"),
    getLayout: unused("getLayout"),
    putLayout: unused("putLayout"),
    apply: unused("apply"),
    getDiff: unused("getDiff"),
    ...overrides,
  };
}

/** Reports the router's CURRENT location, so a test can assert the URL a
 *  click actually produced rather than the `href` a link advertised. */
function LocationProbe(): JSX.Element {
  const loc = useLocation();
  return <output data-testid="loc">{`${loc.pathname}${loc.search}`}</output>;
}

function renderShell(url: string, overrides: Partial<ConsoleApi> = {}): void {
  render(
    <TestApp client={fakeApi(overrides)} url={url}>
      <>
        <ConsoleRoutes />
        <LocationProbe />
      </>
    </TestApp>,
  );
}

function locationNow(): string {
  return screen.getByTestId("loc").textContent ?? "";
}

// ===========================================================================
// ACCEPTANCE 1 -- "at a 1,246 px window with the outline open the level-0 SVG
// renders at >= 0.8 scale".
// ===========================================================================
describe("R1 acceptance: the 1,246 px window renders at >= 0.8 (computed)", () => {
  it("the floor is genuinely BINDING at this window -- the test is not vacuous", () => {
    // If the plane fitted at this width the acceptance would pass with no
    // clamp at all and prove nothing. State the precondition first.
    const pure = layout0(graph);
    const pane = estimateMapWidth(ACCEPTANCE_WINDOW);
    expect(pane).toBe(737);
    expect(pane / pure.width).toBeLessThan(MIN_FIT_SCALE);
  });

  it("renders at >= 0.8 with a MEASURED pane (the path jsdom never takes)", () => {
    restorers.push(stubPaneWidth(737));
    setWindowWidth(ACCEPTANCE_WINDOW);
    const svg = drawLevel0();

    // Default: 1 layout unit = 1 CSS px.
    expect(computedScale(svg)).toBe(BASE_SCALE);

    fireEvent.click(screen.getByTestId("zoom-fit"));
    const fitted = screen.getByTestId("level0-svg");
    const scale = computedScale(fitted);
    expect(scale).toBeGreaterThanOrEqual(0.8);
    // The published value agrees with the real geometry.
    expect(Number(fitted.getAttribute("data-scale"))).toBe(scale);
  });

  it("renders at >= 0.8 with an UNMEASURABLE pane (jsdom / first paint)", () => {
    setWindowWidth(ACCEPTANCE_WINDOW);
    drawLevel0();
    fireEvent.click(screen.getByTestId("zoom-fit"));
    expect(computedScale(screen.getByTestId("level0-svg"))).toBeGreaterThanOrEqual(0.8);
  });

  it("renders at >= 0.8 through the WHOLE SHELL, outline open", async () => {
    setWindowWidth(ACCEPTANCE_WINDOW);
    renderShell("/design/trimmed-fixture");
    const svg = await screen.findByTestId("level0-svg");
    // The outline is open (its pane is in the tree) -- the narrow case.
    expect(screen.getByLabelText("Imprints")).toBeDefined();
    expect(computedScale(svg)).toBeGreaterThanOrEqual(0.8);

    fireEvent.click(screen.getByTestId("zoom-fit"));
    expect(computedScale(screen.getByTestId("level0-svg"))).toBeGreaterThanOrEqual(0.8);
  });

  it("the viewBox stays in PURE layout units at every scale (LES-268)", () => {
    restorers.push(stubPaneWidth(737));
    const pure = layout0(graph);
    const svg = drawLevel0();
    const expected = `0 0 ${pure.width} ${pure.height}`;
    expect(svg.getAttribute("viewBox")).toBe(expected);
    fireEvent.click(screen.getByTestId("zoom-fit"));
    expect(screen.getByTestId("level0-svg").getAttribute("viewBox")).toBe(expected);
  });
});

// ===========================================================================
// ACCEPTANCE 3 -- the summary bar round-trips through the URL.
// ===========================================================================
describe("R1 acceptance: the summary bar's state round-trips through the URL", () => {
  it("collapsed -> expanded -> collapsed, observed on the ROUTER LOCATION", async () => {
    renderShell("/design/trimmed-fixture");
    await screen.findByTestId("summary-bar");

    // Collapsed by default, and the URL says nothing about it.
    expect(locationNow()).toBe("/design/trimmed-fixture");
    expect(screen.getByTestId("summary-digest")).toBeDefined();
    expect(screen.queryByTestId("cast-numbers")).toBeNull();

    fireEvent.click(screen.getByTestId("summary-toggle"));
    expect(locationNow()).toBe("/design/trimmed-fixture?summary=1");
    expect(await screen.findByTestId("cast-numbers")).toBeDefined();
    expect(screen.queryByTestId("summary-digest")).toBeNull();

    fireEvent.click(screen.getByTestId("summary-toggle"));
    expect(locationNow()).toBe("/design/trimmed-fixture");
    expect(screen.getByTestId("summary-digest")).toBeDefined();
  });

  it("a pasted `?summary=1` link opens expanded (it survives a reload)", async () => {
    renderShell("/design/trimmed-fixture?summary=1");
    expect(await screen.findByTestId("cast-numbers")).toBeDefined();
    expect(screen.queryByTestId("summary-digest")).toBeNull();
  });
});

// ===========================================================================
// The `?summary=` flag among the filters it shares a query with.
// ===========================================================================
describe("`?summary=` travels with the filters without BEING one", () => {
  it("round-trips alongside `?changed=1` and `?ghosts=1`", () => {
    const parsed = parseFilters("?ghosts=1&changed=1&summary=1");
    expect(parsed.ghosts).toBe(true);
    expect(parsed.changed).toBe(true);
    expect(parsed.summary).toBe(true);
    expect(parsed.problems).toEqual([]);
    expect(parseFilters(filtersToQuery(parsed))).toEqual(parsed);
  });

  it("never activates the dimming pass on its own", () => {
    expect(anyFilterActive({ ...NO_FILTERS, summary: true })).toBe(false);
    // ...and does not suppress it either, when a real filter is also on.
    expect(anyFilterActive({ ...NO_FILTERS, summary: true, ghosts: true })).toBe(true);
  });

  it("`clear` drops `?changed=1` but KEEPS the summary bar open", () => {
    render(
      <TestApp client={fakeApi()} url="/design/x">
        <FiltersBar
          filters={{ ...NO_FILTERS, changed: true, summary: true }}
          pathname="/design/x"
        />
      </TestApp>,
    );
    expect(screen.getByTestId("filters-clear").getAttribute("href")).toBe("/design/x?summary=1");
  });

  it("the shell renders expanded when `?summary=1` arrives WITH a filter", async () => {
    renderShell("/design/trimmed-fixture?ghosts=1&summary=1");
    expect(await screen.findByTestId("cast-numbers")).toBeDefined();
    expect(locationNow()).toBe("/design/trimmed-fixture?ghosts=1&summary=1");
  });
});

// ===========================================================================
// Realistic edges: narrow panes, unmeasurable panes, empty tiers, denied
// storage. None of these is a damaged-input matrix -- each is a browser or a
// bundle an operator will actually meet.
// ===========================================================================
describe("a pane too narrow for the floor pans instead of shrinking", () => {
  it("clamps at the floor and overflows the pane (the scroll is the answer)", () => {
    const paneWidth = 300;
    restorers.push(stubPaneWidth(paneWidth));
    const pure = layout0(graph);
    drawLevel0();
    fireEvent.click(screen.getByTestId("zoom-fit"));

    const svg = screen.getByTestId("level0-svg");
    expect(computedScale(svg)).toBe(MIN_FIT_SCALE);
    // The whole point: the drawing is WIDER than the pane and pans, rather
    // than being squeezed to ~11 % and becoming unreadable.
    const renderedPx = Number(svg.getAttribute("width"));
    expect(renderedPx).toBe(pure.width * MIN_FIT_SCALE);
    expect(renderedPx).toBeGreaterThan(paneWidth);
    // The panning container is the SVG's own wrapper.
    expect(svg.parentElement?.className).toContain("map-scroll");
  });

  it("says so, rather than leaving the reader to wonder why it clipped", () => {
    restorers.push(stubPaneWidth(300));
    drawLevel0();
    fireEvent.click(screen.getByTestId("zoom-fit"));
    expect(screen.getByTestId("zoom-readout").textContent).toMatch(/floor/);
  });
});

describe("an unmeasurable viewport never shrinks the drawing", () => {
  it("a 0-width pane and a 0-width window still render 1:1, even in fit mode", () => {
    restorers.push(stubPaneWidth(0));
    setWindowWidth(0);
    drawLevel0();
    fireEvent.click(screen.getByTestId("zoom-fit"));
    // "I don't know how wide the pane is" must not become "draw it tiny".
    expect(computedScale(screen.getByTestId("level0-svg"))).toBe(BASE_SCALE);
  });
});

describe("a tier with no content", () => {
  // R2 ADJUSTMENT: emptying the WAN-EDGE tier now takes more than dropping the
  // routers. The edge is drawn whenever the twin HAS one -- declared by a
  // router or INFERRED from observed local↔non-local traffic -- so the
  // non-local population and the edges reaching it come out too. Dropping only
  // the routers leaves an inferred edge, which is R2 working as ruled, not a
  // rail bug; the inferred case has its own coverage in `r2.trunk.test.ts`.
  const externalIds = new Set(
    buildGraph(trimmedExport, trimmedCast).nonLocal.map((n) => n.id),
  );
  // R4 ADJUSTMENT, the same shape one tier down (LES-299): emptying the
  // EAST-WEST tier used to mean "publish no conduits", and now means that AND
  // "observe no local↔local traffic" -- the band draws OBSERVED segment-pair
  // arcs from the export's own edges since the arcs ruling. Leaving the edges
  // in would silently exercise the observed branch and quietly stop testing
  // the absence this block is named for; the observed case has its own
  // coverage in `r4.arcs.test.ts`.
  const noConduits = buildGraph(
    {
      ...trimmedExport,
      nodes: trimmedExport.nodes.filter((n) => !externalIds.has(n.id)),
      edges: [],
    },
    { ...trimmedCast, conduits: [], routers: [] },
  );

  it("the PURE layout still emits all five rails (a total function of TIERS)", () => {
    const l0 = layout0(noConduits);
    expect(l0.rails.map((r) => r.tier)).toEqual([...TIERS]);
    const eastWest = l0.rails.find((r) => r.tier === "east-west")!;
    expect(eastWest.box.h).toBe(0);
  });

  it("the VIEW draws three -- a label over nothing would claim content", () => {
    // ADJUSTED in the R1 fix round, to preserve this test's intent rather than
    // its arithmetic: the fixture empties TWO tiers (no conduits => east-west,
    // no routers => wan-edge), and S4 made the WAN band's height content-
    // derived like the other data tiers, so one fewer rail draws.
    render(<Level0 graph={noConduits} filters={NO_FILTERS} go={() => {}} onFocus={() => {}} />);
    const drawn = screen.getAllByTestId("tier-rail").map((r) => r.textContent);
    // Stated as the EXACT drawn set rather than a count plus two absences: a
    // count says how many tiers claimed content, not which, so a future change
    // that silently empties a third tier and adds a fourth would still pass.
    expect(drawn).toEqual([
      TIER_LABELS.internet,
      TIER_LABELS.infrastructure,
      TIER_LABELS.segment,
    ]);
    expect(drawn).toHaveLength(TIERS.length - 2);
    expect(screen.queryAllByTestId("east-west-arc")).toHaveLength(0);
  });

  it("and the drawing is unaffected otherwise -- still one tile per segment", () => {
    render(<Level0 graph={noConduits} filters={NO_FILTERS} go={() => {}} onFocus={() => {}} />);
    expect(screen.getAllByTestId("segment-tile")).toHaveLength(6);
  });
});

describe("a browser profile where `localStorage` THROWS (private mode)", () => {
  function denyStorage(): () => void {
    const prior = Object.getOwnPropertyDescriptor(window, "localStorage");
    Object.defineProperty(window, "localStorage", {
      configurable: true,
      get() {
        throw new Error("localStorage access is denied in this profile");
      },
    });
    return () => {
      if (prior) Object.defineProperty(window, "localStorage", prior);
      else Reflect.deleteProperty(window as unknown as Record<string, unknown>, "localStorage");
    };
  }

  it("the shell still renders, with the outline OPEN (the safe default)", async () => {
    restorers.push(denyStorage());
    renderShell("/design/trimmed-fixture");
    expect(await screen.findByTestId("level0-svg")).toBeDefined();
    expect(screen.getByLabelText("Imprints")).toBeDefined();
    expect(screen.getByTestId("outline-toggle").getAttribute("aria-expanded")).toBe("true");
  });

  it("and the toggle still works for the session (it just cannot persist)", async () => {
    restorers.push(denyStorage());
    renderShell("/design/trimmed-fixture");
    await screen.findByTestId("level0-svg");
    fireEvent.click(screen.getByTestId("outline-toggle"));
    expect(screen.queryByLabelText("Imprints")).toBeNull();
    fireEvent.click(screen.getByTestId("outline-toggle"));
    expect(screen.getByLabelText("Imprints")).toBeDefined();
  });
});

describe("collapsing the outline gives the map width without moving the drawing", () => {
  it("marks the shell and leaves the pure layout untouched", async () => {
    renderShell("/design/trimmed-fixture");
    const svg = await screen.findByTestId("level0-svg");
    const before = svg.getAttribute("viewBox");
    const shell = document.querySelector(".shell")!;
    expect(shell.className).not.toContain("is-outline-collapsed");

    fireEvent.click(screen.getByTestId("outline-toggle"));
    expect(document.querySelector(".shell")!.className).toContain("is-outline-collapsed");
    // Chrome state is not layout state: the coordinates cannot move (LES-268).
    expect(screen.getByTestId("level0-svg").getAttribute("viewBox")).toBe(before);
  });

  it("does NOT leak into the URL -- a pasted link carries no pane preference", async () => {
    renderShell("/design/trimmed-fixture");
    await screen.findByTestId("level0-svg");
    fireEvent.click(screen.getByTestId("outline-toggle"));
    expect(locationNow()).toBe("/design/trimmed-fixture");
  });
});

// ===========================================================================
// Spot-checks of the R1 fix round, on the realistic path each shipped test
// stops one step short of. Not a widening of the matrix: one test per fix,
// each pinning the fix's OWN stated property rather than a new scenario.
// ===========================================================================
describe("M1: a failed provenance read is shown while the cast is still LOADING", () => {
  it("renders the error with the summary expanded and no cast yet", async () => {
    // The shipped test covers a cast that FAILED. The commoner path is a cast
    // still in flight while the provenance read has already errored -- the old
    // `graph.cast &&` guard was falsy for BOTH, so this is the same defect
    // reached the way an operator on a slow bundle read actually reaches it.
    const boom = new ConsoleApiError({
      kind: "unavailable",
      status: 503,
      message: "the sidecar is not readable",
      url: "/api/imprints/trimmed-fixture/provenance",
    });
    renderShell("/design/trimmed-fixture?summary=1", {
      getProvenance: () => Promise.reject(boom),
      getCast: () => new Promise<never>(() => {}), // never settles
    });
    const failure = await screen.findByTestId("evidence-error");
    expect(failure.textContent).toContain("the sidecar is not readable");
    // The cast's own state is still reported as loading, not as a fact.
    expect(screen.getByTestId("summary-bar").textContent).toContain("loading the cast summary");
  });
});

describe("S3: the resize observer watches the MAP PANE, and lets go", () => {
  it("observes the `.map-scroll` element itself and disconnects on unmount", () => {
    // The shipped test proves the callback re-measures. It cannot prove WHAT
    // was observed: its `observe()` is a no-op, so observing the wrong element
    // -- or never calling `observe` at all -- would pass it. A ResizeObserver
    // that watches the document would never fire on a grid-column change.
    const observed: Element[] = [];
    let disconnects = 0;
    class RecordingResizeObserver {
      constructor(_cb: () => void) {}
      observe(el: Element): void {
        observed.push(el);
      }
      disconnect(): void {
        disconnects += 1;
      }
    }
    const globals = globalThis as unknown as { ResizeObserver?: unknown };
    const prior = globals.ResizeObserver;
    globals.ResizeObserver = RecordingResizeObserver;
    try {
      const { unmount } = render(
        <Level0 graph={graph} filters={NO_FILTERS} go={() => {}} onFocus={() => {}} />,
      );
      const pane = document.querySelector(".map-scroll");
      expect(pane, "the map pane is gone").not.toBeNull();
      expect(observed).toEqual([pane]);

      unmount();
      expect(disconnects, "the observer outlives the component").toBe(1);
    } finally {
      if (prior === undefined) delete globals.ResizeObserver;
      else globals.ResizeObserver = prior;
    }
  });
});

describe("S5: the catalog forces the outline WITHOUT rewriting the preference", () => {
  it("leaves a stored `closed` intact, so it still applies at an imprint", async () => {
    // The shipped test proves the outline shows and the toggle hides at the
    // catalog. The property that decides whether the operator's deliberate
    // "closed" SURVIVES is the one it does not assert: forcing it open must
    // not write `open` back, or the next imprint view silently reopens it.
    window.localStorage.setItem("axrange.console.outline", "closed");
    const atCatalog = render(<TestApp client={fakeApi()} url="/design" />);
    expect(await screen.findByLabelText("Imprints")).toBeDefined();
    expect(screen.queryByTestId("outline-toggle")).toBeNull();
    expect(window.localStorage.getItem("axrange.console.outline")).toBe("closed");
    atCatalog.unmount();

    // The end-to-end half: the preference still applies where it can.
    render(<TestApp client={fakeApi()} url="/design/trimmed-fixture" />);
    await screen.findByTestId("summary-bar");
    expect(screen.queryByLabelText("Imprints")).toBeNull();
    expect(screen.getByTestId("outline-toggle").getAttribute("aria-expanded")).toBe("false");
  });
});

// ===========================================================================
// Level 1 and the matrix are UNAFFECTED. Their goldens prove their geometry
// and `r1.goldens.test.ts` proves the CSS rule split; what is left to check
// here is that neither view grew a scale control.
// ===========================================================================
describe("level 1 and the matrix are untouched by the width slice", () => {
  it("neither view grew a scale control or a scaled width", () => {
    const { unmount } = render(
      <Level1 graph={graph} filters={NO_FILTERS} cidr="10.80.20.0/24" go={() => {}} />,
    );
    expect(screen.queryByTestId("map-zoom")).toBeNull();
    const l1 = screen.getByTestId("level1-svg");
    expect(l1.getAttribute("data-scale")).toBeNull();
    unmount();

    render(<MatrixView graph={graph} onFocus={() => {}} />);
    expect(screen.queryByTestId("map-zoom")).toBeNull();
  });
});
