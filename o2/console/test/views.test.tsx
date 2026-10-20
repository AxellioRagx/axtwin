import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { ConsoleApi } from "../src/api/client";
import type { AssertionsDoc, CastSummary, ImprintList, ImprintProvenance } from "../src/api/types";
import { TestApp } from "../src/routes";
import { buildGraph } from "../src/model/graph";
import { layout0 } from "../src/layout/level0";
import { TIERS, TIER_LABELS } from "../src/layout/tiers";
import { MIN_FIT_SCALE } from "../src/layout/viewport";
import { NO_FILTERS, zoomToPath } from "../src/model/zoom";
import { FiltersBar } from "../src/views/Filters";
import { Level0 } from "../src/views/Level0";
import { Level1 } from "../src/views/Level1";
import { Level2 } from "../src/views/Level2";
import { MatrixView } from "../src/views/Matrix";
import { Search } from "../src/views/Search";
import { trimmedCast, trimmedExport } from "./fixtures/trimmed";

const graph = buildGraph(trimmedExport, trimmedCast);

/** Stub the measured width of every `div` -- jsdom measures 0, so without this
 *  only `estimateMapWidth`'s fallback is ever exercised. A function argument
 *  lets a test change the pane width mid-test (the S3 resize case). */
function stubClientWidth(px: number | (() => number)): () => void {
  const proto = window.HTMLDivElement.prototype;
  const prior = Object.getOwnPropertyDescriptor(proto, "clientWidth");
  Object.defineProperty(proto, "clientWidth", {
    configurable: true,
    get: () => (typeof px === "function" ? px() : px),
  });
  return () => {
    if (prior) Object.defineProperty(proto, "clientWidth", prior);
    else Reflect.deleteProperty(proto, "clientWidth");
  };
}

// ===========================================================================
// The pure views, fed a real-shaped graph (no router; go/onFocus are props).
// ===========================================================================
describe("Level0 draws the tiered diagram", () => {
  it("renders one tile per segment, the east-west arcs and the devices", () => {
    const { container } = render(
      <Level0 graph={graph} filters={NO_FILTERS} go={() => {}} onFocus={() => {}} />,
    );
    expect(screen.getAllByTestId("segment-tile")).toHaveLength(6);
    // R4: 4 conduits, one to `internet-sink` (unresolved) -> 3 ASSERTED arcs.
    // The fixture's one observed pair (10.80.72 <-> 10.80.20) is one of those
    // conduits, so it is drawn once, as asserted -- never twice.
    expect(screen.getAllByTestId("east-west-arc")).toHaveLength(3);
    expect(container.querySelectorAll("rect.device")).toHaveLength(3);
    expect(screen.getByText("unplaced")).toBeDefined();
  });

  it("names the gateway's legs in the WAN box (R5)", () => {
    render(<Level0 graph={graph} filters={NO_FILTERS} go={() => {}} onFocus={() => {}} />);
    expect(screen.getByTestId("wan-label").textContent).toBe("gateway · 2 legs · SNAT");
  });

  it("drills into a segment on tile click", () => {
    const go = vi.fn();
    render(<Level0 graph={graph} filters={NO_FILTERS} go={go} onFocus={() => {}} />);
    fireEvent.click(screen.getByLabelText(/Segment 10\.80\.20\.0\/24/));
    expect(go).toHaveBeenCalledWith(
      zoomToPath({ level: "segment", imprint: "trimmed-fixture", cidr: "10.80.20.0/24" }),
    );
  });

  it("opens the non-local drawer from the Internet pill", () => {
    const onFocus = vi.fn();
    render(<Level0 graph={graph} filters={NO_FILTERS} go={() => {}} onFocus={onFocus} />);
    fireEvent.click(screen.getByLabelText(/Internet:/));
    expect(onFocus).toHaveBeenCalledWith({ kind: "nonlocal" });
  });

  it("labels every tier with a left-edge rail (R1)", () => {
    render(<Level0 graph={graph} filters={NO_FILTERS} go={() => {}} onFocus={() => {}} />);
    const rails = screen.getAllByTestId("tier-rail");
    // The fixture has conduits, a router and infra devices, so every tier has
    // content and every rail draws.
    expect(rails).toHaveLength(TIERS.length);
    expect(rails.map((r) => r.textContent)).toEqual(TIERS.map((t) => TIER_LABELS[t]));
    // The rail groups are `aria-hidden` (their labels would be read in
    // document order among the tiles), so the tier structure has to reach
    // assistive tech some other way -- the SVG's description (nit).
    const desc = screen.getByTestId("level0-desc").textContent ?? "";
    for (const tier of TIERS) expect(desc, tier).toContain(TIER_LABELS[tier]);
  });

  it("renders at 1:1 by default, and never below 0.8 when fitted (R1)", () => {
    // The acceptance: a 1,246 px window with the outline open. jsdom measures
    // every element as 0, which is exactly the case `estimateMapWidth` covers
    // -- so this exercises the fallback the real browser never needs.
    Object.defineProperty(window, "innerWidth", { value: 1246, writable: true, configurable: true });
    const pure = layout0(graph);
    const { getByTestId } = render(
      <Level0 graph={graph} filters={NO_FILTERS} go={() => {}} onFocus={() => {}} />,
    );
    const svg = getByTestId("level0-svg");

    // Default: 1 layout unit = 1 CSS px, and the pane scrolls.
    expect(svg.getAttribute("data-scale")).toBe("1");
    expect(svg.getAttribute("width")).toBe(String(pure.width));
    expect(svg.getAttribute("viewBox")).toBe(`0 0 ${pure.width} ${pure.height}`);

    // Fit-to-width: clamped at the floor, never the ~25 % of the old
    // `max-width: 100%` rule -- and the viewBox (the pure units) is untouched.
    fireEvent.click(getByTestId("zoom-fit"));
    const scale = Number(getByTestId("level0-svg").getAttribute("data-scale"));
    expect(scale).toBeGreaterThanOrEqual(MIN_FIT_SCALE);
    expect(scale).toBeLessThanOrEqual(1);
    expect(getByTestId("level0-svg").getAttribute("width")).toBe(String(pure.width * scale));
    expect(getByTestId("level0-svg").getAttribute("viewBox")).toBe(`0 0 ${pure.width} ${pure.height}`);
  });

  it("says how much is off-screen when the plane is cut at 1:1 (S7)", () => {
    const pure = layout0(graph);
    const restore = stubClientWidth(1000);
    try {
      const { getByTestId } = render(
        <Level0 graph={graph} filters={NO_FILTERS} go={() => {}} onFocus={() => {}} />,
      );
      // At the 1:1 default on a narrow pane the drawing is simply clipped, and
      // the scrollbar used to be the only tell -- which is why the INTERNET
      // and WAN EDGE rails read as empty rather than as "scroll right".
      expect(getByTestId("zoom-readout").textContent).toContain(
        `${pure.width - 1000} px off-screen`,
      );
    } finally {
      restore();
    }
  });

  it("claims NO truncation when fit shows the whole plane at the floor (nit)", () => {
    const pure = layout0(graph);
    // A pane at just over 0.8 of the plane: `fit` shows ALL of it, so neither
    // the floor note nor the off-screen note may appear. The bug was reading
    // `scale <= MIN_FIT_SCALE`, which calls an exactly-fitting plane truncated.
    const restore = stubClientWidth(Math.ceil(pure.width * MIN_FIT_SCALE));
    try {
      const { getByTestId } = render(
        <Level0 graph={graph} filters={NO_FILTERS} go={() => {}} onFocus={() => {}} />,
      );
      fireEvent.click(getByTestId("zoom-fit"));
      const readout = getByTestId("zoom-readout").textContent ?? "";
      expect(readout).not.toMatch(/floor/);
      expect(readout).not.toMatch(/off-screen/);
    } finally {
      restore();
    }
  });

  it("re-measures when the PANE resizes with no window resize (S3)", () => {
    const pure = layout0(graph);
    let paneWidth = 300;
    const restore = stubClientWidth(() => paneWidth);
    let notify: (() => void) | null = null;
    class FakeResizeObserver {
      constructor(callback: () => void) {
        notify = callback;
      }
      observe(): void {}
      disconnect(): void {}
    }
    const globals = globalThis as unknown as { ResizeObserver?: unknown };
    const priorRO = globals.ResizeObserver;
    globals.ResizeObserver = FakeResizeObserver;
    try {
      const { getByTestId } = render(
        <Level0 graph={graph} filters={NO_FILTERS} go={() => {}} onFocus={() => {}} />,
      );
      fireEvent.click(getByTestId("zoom-fit"));
      expect(Number(getByTestId("level0-svg").getAttribute("data-scale"))).toBe(MIN_FIT_SCALE);

      // Collapsing the outline widens the pane and fires NO window resize --
      // the case a `window.addEventListener("resize")` alone never sees, and
      // exactly when `fit width` is being used.
      paneWidth = pure.width;
      expect(notify, "the pane was never observed").not.toBeNull();
      act(() => notify!());
      expect(Number(getByTestId("level0-svg").getAttribute("data-scale"))).toBe(1);
    } finally {
      if (priorRO === undefined) delete globals.ResizeObserver;
      else globals.ResizeObserver = priorRO;
      restore();
    }
  });

  it("dims (never hides) the tiles a filter does not match", () => {
    // ghosts=1: a tile with no sub-threshold host is dimmed; the counts stay.
    const filters = { ghosts: true, evidenceClass: null, occupancyLt: null, changed: false, summary: false, eastWestAll: false, tileOrder: "hosts" as const, expand: [], problems: [] };
    const { container } = render(
      <Level0 graph={graph} filters={filters} go={() => {}} onFocus={() => {}} />,
    );
    expect(screen.getAllByTestId("segment-tile")).toHaveLength(6); // nothing hidden
    const dimmed = container.querySelectorAll("rect.tile.is-dimmed");
    // 10.20.193.0/24 (18 live workstations) and 192.168.0.0/24 have no ghost.
    expect(dimmed.length).toBeGreaterThan(0);
    expect(dimmed.length).toBeLessThan(6);
  });
});

describe("the filter bar and the summary view flag share one query (R1)", () => {
  // `?summary=` rides in the filter query but is NOT a filter, so `clear` --
  // which used to link to the bare pathname -- must not collapse the summary
  // bar as a side effect of clearing a class filter.
  const withSummary = { ...NO_FILTERS, ghosts: true, summary: true };

  function renderBar(filters: typeof withSummary): void {
    render(
      <TestApp client={fakeApi()} url="/design/x">
        <FiltersBar filters={filters} pathname="/design/x" />
      </TestApp>,
    );
  }

  it("`clear` drops the filters and KEEPS the summary bar open", () => {
    renderBar(withSummary);
    expect(screen.getByTestId("filters-clear").getAttribute("href")).toBe("/design/x?summary=1");
  });

  it("`clear` is a bare path when the summary is closed (unchanged behaviour)", () => {
    renderBar({ ...withSummary, summary: false });
    expect(screen.getByTestId("filters-clear").getAttribute("href")).toBe("/design/x");
  });

  it("the summary flag alone does not raise a `clear` chip -- it is not a filter", () => {
    renderBar({ ...NO_FILTERS, summary: true });
    expect(screen.queryByTestId("filters-clear")).toBeNull();
  });
});

describe("Level1 draws the spine drill", () => {
  it("shows the real role bands in fixed order and a below-threshold band", () => {
    render(<Level1 graph={graph} filters={NO_FILTERS} cidr="10.80.20.0/24" go={() => {}} />);
    expect(screen.getByTestId("band-domain-controller")).toBeDefined();
    expect(screen.getByTestId("band-dns-resolver")).toBeDefined();
    expect(screen.getByTestId("band-corp-workstation")).toBeDefined();
    expect(screen.getByTestId("band-below-threshold")).toBeDefined();
    expect(screen.getAllByTestId("host-chip").length).toBeGreaterThan(0);
  });

  it("drills into a host on chip click", () => {
    const go = vi.fn();
    render(<Level1 graph={graph} filters={NO_FILTERS} cidr="10.80.20.0/24" go={go} />);
    fireEvent.click(screen.getByLabelText("domain-controller: hostname:axdc01"));
    expect(go).toHaveBeenCalledWith(
      zoomToPath({ level: "host", imprint: "trimmed-fixture", key: "hostname:axdc01", cidr: "10.80.20.0/24" }),
    );
  });

  it("renders the below-threshold rung summary when it collapses (10.80.72.0/24)", () => {
    render(<Level1 graph={graph} filters={NO_FILTERS} cidr="10.80.72.0/24" go={() => {}} />);
    const band = screen.getByTestId("band-below-threshold");
    expect(band.textContent).toContain("collapsed");
    expect(band.textContent).toMatch(/regular: 4/);
    expect(band.textContent).toMatch(/transient: 18/);
  });
});

describe("Level2 is the facts table", () => {
  it("shows the export's listen_ports verbatim", () => {
    render(<Level2 graph={graph} hostKey="hostname:axdc01" assertions={null} />);
    const listen = screen.getByTestId("listen-set");
    const ports = [...listen.querySelectorAll("li")].map((li) => li.textContent);
    expect(ports).toEqual(["53/udp", "88/tcp", "389/tcp", "445/tcp"]);
    // edges by peer role/segment are grouped and rendered.
    expect(screen.getByTestId("host-edges")).toBeDefined();
  });

  it("says so for a host that is not in the twin", () => {
    render(<Level2 graph={graph} hostKey="address:9.9.9.9" assertions={null} />);
    expect(screen.getByTestId("no-host")).toBeDefined();
  });
});

describe("the matrix", () => {
  it("renders a grid whose non-empty cells sum to the graph's flows", () => {
    render(<MatrixView graph={graph} onFocus={() => {}} />);
    expect(screen.getByTestId("matrix-svg")).toBeDefined();
    expect(screen.getAllByTestId("matrix-cell").length).toBeGreaterThan(0);
  });

  it("opens a cell's edge list on click", () => {
    const onFocus = vi.fn();
    render(<MatrixView graph={graph} onFocus={onFocus} />);
    fireEvent.click(screen.getAllByTestId("matrix-cell")[0]!);
    expect(onFocus).toHaveBeenCalledTimes(1);
    expect(onFocus.mock.calls[0]![0]).toMatchObject({ kind: "cell" });
  });
});

describe("search resolves to a level", () => {
  it("a CIDR jumps to the segment", () => {
    const go = vi.fn();
    render(<Search graph={graph} go={go} />);
    fireEvent.change(screen.getByTestId("search-input"), { target: { value: "10.80.72.0/24" } });
    fireEvent.submit(screen.getByRole("search"));
    expect(go).toHaveBeenCalledWith(
      zoomToPath({ level: "segment", imprint: "trimmed-fixture", cidr: "10.80.72.0/24" }),
    );
  });

  it("a miss reports rather than navigating", () => {
    const go = vi.fn();
    render(<Search graph={graph} go={go} />);
    fireEvent.change(screen.getByTestId("search-input"), { target: { value: "nope-nothing" } });
    fireEvent.submit(screen.getByRole("search"));
    expect(go).not.toHaveBeenCalled();
    expect(screen.getByTestId("search-note").textContent).toMatch(/no name, address, role or CIDR/);
  });
});

// ===========================================================================
// The whole shell, end to end, against a topology-RESOLVING fake API -- the
// wiring S2 could not exercise (its fake rejected `getTopology`).
// ===========================================================================
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
  // R5: what the live path now draws from -- the same four conduits (one to
  // the unresolvable `internet-sink`) and the one router with its two legs the
  // pure `trimmedCast` carries, so the shell's band can be compared with the
  // pure one.
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
    { id: "trimmed-fixture", hosts: 40, segments: 6, as_of: "", mean_confidence: 0, has_provenance: false, confidence_source: "absent" },
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

function fakeApi(): ConsoleApi {
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
  };
}

describe("the shell draws and navigates end to end", () => {
  it("renders level 0 from the topology export", async () => {
    render(<TestApp client={fakeApi()} url="/design/trimmed-fixture" />);
    expect(await screen.findByTestId("level0-svg")).toBeDefined();
    // R1: the summary is a COLLAPSED one-line bar by default -- the numbers
    // are in its digest, and the full grid is behind `?summary=1`.
    expect(screen.getByTestId("summary-digest").textContent).toContain("6 segments");
    expect(screen.queryByTestId("cast-numbers")).toBeNull();
    // R5 (GAP-079): the live path draws the fetched roster -- 4 conduits, one
    // to `internet-sink` (unresolvable), so 3 asserted arcs -- and the fetched
    // legs light the WAN box. (Until R5 this asserted an empty-band note.)
    expect(screen.getAllByTestId("east-west-arc")).toHaveLength(3);
    expect(screen.getByTestId("wan-label").textContent).toBe("gateway · 2 legs · SNAT");
    expect(screen.queryByTestId("conduits-absent")).toBeNull();
  });

  it("drilling a tile changes the URL to the segment and draws level 1", async () => {
    render(<TestApp client={fakeApi()} url="/design/trimmed-fixture" />);
    const map = await screen.findByTestId("level0-svg");
    fireEvent.click(within(map).getByLabelText(/Segment 10\.80\.20\.0\/24/));
    expect(await screen.findByTestId("level1-svg")).toBeDefined();
    const inspector = screen.getByLabelText("Inspector");
    const dt = [...inspector.querySelectorAll("dt")].find((n) => n.textContent === "segment");
    expect(dt?.nextElementSibling?.textContent).toBe("10.80.20.0/24");
  });

  it("the outline tree counts the unplaced and non-local populations (O7)", async () => {
    render(<TestApp client={fakeApi()} url="/design/trimmed-fixture" />);
    await screen.findByTestId("level0-svg");
    expect(screen.getByTestId("outline-unplaced").textContent).toBe("2");
    expect(screen.getByTestId("outline-nonlocal").textContent).toBe("3");
  });

  it("keeps an open drawer when the summary bar is toggled (S1)", async () => {
    render(<TestApp client={fakeApi()} url="/design/trimmed-fixture" />);
    const map = await screen.findByTestId("level0-svg");
    fireEvent.click(within(map).getByLabelText(/Internet:/)); // the O7 drawer
    expect(document.querySelector(".focus")).not.toBeNull();

    // `?summary=` is view chrome, not scope. The focus reset keyed on the
    // whole query string, so opening the summary bar closed the drawer the
    // operator had just opened.
    fireEvent.click(screen.getByTestId("summary-toggle"));
    expect(await screen.findByTestId("cast-numbers")).toBeDefined();
    expect(document.querySelector(".focus")).not.toBeNull();
  });

  it("forces the outline open at the CATALOG, whatever the preference (S5)", async () => {
    window.localStorage.setItem("axrange.console.outline", "closed");
    try {
      render(<TestApp client={fakeApi()} url="/design" />);
      // At the catalog the outline IS the navigation: a stored `closed` left
      // the operator with "pick an imprint from the outline" and no outline.
      expect(await screen.findByLabelText("Imprints")).toBeDefined();
      // ...and the toggle is hidden rather than left as a control that does
      // nothing visible.
      expect(screen.queryByTestId("outline-toggle")).toBeNull();
    } finally {
      window.localStorage.clear();
    }
  });
});
